# -*- coding: utf-8 -*-
"""kikimiru server — スライド同期音声のself-host配信サーバ(Phase 1)。

使い方:
    python server/kikimiru_server.py [--library <dir>] [--library 名前=<dir> ...]
                                     [--bind 127.0.0.1] [--port 8484]

- --library は複数回指定できる。`名前=パス` の形式で表示名を付けられる
  (例: --library 技術書=D:/books/tech --library 語学=D:/books/lang)。
  `パス` だけを渡した場合はディレクトリ名がライブラリIDになる
- 各ライブラリ配下の「1フォルダ=1ブック」(deck.json 必須・音声ファイル同梱)を配信する
- iOS Safari の <audio> シークに必要な HTTP Range(206) に対応する
- 既定 bind は 127.0.0.1(安全側)。LAN・VPN へ公開する場合のみ --bind で明示する
- 認証機構は無い(私的利用限定の想定)。非ループバックへ bind すると起動時に警告を表示する
- Host/Origin ヘッダを検証しているため、DNSリバインディングを狙ったブラウザ経由の
  アクセスは拒否される(--bind で指定したIP:port と 127.0.0.1/localhost のみ許可)

エンドポイント:
    GET  /                          -> /web/player.html へリダイレクト
    GET  /web/player.html           -> プレイヤー静的ファイル(固定allowlist配信)
    GET  /api/libraries             -> ライブラリ一覧
    GET  /api/books?library=&q=&sort=&author=&narrator=&series=&tag=
                                    -> ブック一覧(検索・絞り込み・並び替え)
    GET  /api/authors?library=      -> 著者一覧(ブック数付き)
    GET  /api/narrators?library=    -> 話者一覧(ブック数付き)
    GET  /api/series?library=       -> シリーズ一覧(巻数・代表カバー付き)
    GET  /api/stats?library=        -> 統計
    GET  /api/collections?library=  -> コレクション一覧
    PUT  /api/collections?library=  -> コレクションの保存(collections.json へ書き込み)
    GET  /books/<library>/<id>/<file> -> ブックフォルダ内ファイル(拡張子allowlist・Range対応)
    HEAD 上記いずれも同一ルーティングでボディ無し応答(SimpleHTTPRequestHandler非継承のため
         カレントディレクトリ配下の任意ファイルが露出することはない)
    その他メソッド(POST/DELETE/PATCH/OPTIONS) -> 405
"""
import argparse
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit, parse_qs

APP_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = APP_ROOT / "web"

# /web/ 配下で配信を許可する固定ファイルの一覧(name -> (実パス, Content-Type))。
# 動的にパスを解決せず allowlist から引くだけにすることで、UNCパスや絶対パスの
# 注入によるbase乗っ取りを構造的に防ぐ。
WEB_ALLOWLIST = {
    "player.html": (WEB_DIR / "player.html", "text/html; charset=utf-8"),
}

# /books/ 配下で配信を許可する拡張子とContent-Typeの固定表(allowlist方式)。
# guess_type() 等のOS依存の推測に任せず、ここに無い拡張子は問答無用で404にする
# ことで、.html/.svg 等を置かれても同一オリジンでのスクリプト実行を防ぐ。
BOOK_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".opus": "audio/opus",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".json": "application/json; charset=utf-8",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}

# カバー画像の自動検出順(deck.json の cover 未指定時)
COVER_CANDIDATES = ("cover.jpg", "cover.png", "cover.webp", "cover.jpeg")

# コレクションの保存先(ライブラリルート直下)。PUT で書き込む唯一のファイル。
COLLECTIONS_FILE = "collections.json"

# Range ヘッダの数値部分は桁数を18桁までに制限する。
# 際限なく長い数字列を int() へ渡すとパース自体がエラーになりうるため、
# 正規表現の時点で桁数を絞って未処理例外を防ぐ(万一 ValueError が出ても下流で捕捉する)。
RANGE_RE = re.compile(r"bytes=(\d{0,18})-(\d{0,18})$")

# PUT /api/collections で受け付ける本文の上限(コレクション定義は小さいはず)
MAX_PUT_BYTES = 1 << 20  # 1MiB


def is_safe_segment(seg: str) -> bool:
    """パス構成要素(library・book_id・ファイル名の1階層分)がbase外へ逸脱しないかを判定する。

    ホワイトリスト方式(使える文字を列挙)は長音符「ー」・中黒「・」・「々」等の
    正当な日本語文字まで弾いてしまうため、危険な構成要素だけを拒否する
    ブラックリスト方式を採る。
    """
    if not seg or seg.startswith("."):
        return False
    if "/" in seg or "\\" in seg:
        return False
    if ".." in seg:
        return False
    if any(ord(c) < 0x20 for c in seg):
        return False
    return True


class Library:
    """1つのライブラリ(ブックフォルダ群を含むディレクトリ)。

    root は起動時に一度だけ resolve() した固定の基準パス。以降のパス解決では
    再 resolve せずこれを基準に封じ込め検査を行う(book_id フォルダがジャンクションでも
    基準が乗っ取られないようにするため)。
    """

    def __init__(self, lib_id: str, root: Path, name: str = None):
        self.id = lib_id
        self.root = root.resolve()
        self.name = name or lib_id
        self._cache = None       # 走査結果(ブックのメタデータ一覧)
        self._cache_key = None   # 走査時点の (deck.jsonのmtime, size) の集合

    # --- 走査とキャッシュ ---------------------------------------------

    def _scan_key(self):
        """ライブラリの状態を表す軽量なキー。deck.json の mtime/size の集合で判定する。

        ブック本数が増えても、全 deck.json を読み直さずに変化の有無だけ判定できる。
        """
        key = []
        try:
            for deck_path in sorted(self.root.glob("*/deck.json")):
                try:
                    st = deck_path.stat()
                except OSError:
                    continue
                key.append((deck_path.parent.name, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
        return tuple(key)

    def books(self) -> list:
        """ブックのメタデータ一覧を返す(キャッシュ付き)。"""
        key = self._scan_key()
        if self._cache is not None and key == self._cache_key:
            return self._cache
        books = []
        for book_id, _mtime, _size in key:
            meta = self._read_book(book_id)
            if meta is not None:
                books.append(meta)
        books.sort(key=lambda b: b["title"])
        self._cache = books
        self._cache_key = key
        return books

    def _read_book(self, book_id: str):
        """1冊分の deck.json を読み、API応答用のメタデータへ整形する。

        JSONとして妥当でも型が期待と違う(deckがlist・titleが数値等)場合に
        AttributeError/TypeError が飛ばないよう、各フィールドを isinstance で
        確認しながら安全に取り出す。壊れた1冊は None を返して呼び出し側でスキップする。
        """
        deck_path = self.root / book_id / "deck.json"
        try:
            deck = json.loads(deck_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"警告: {deck_path} の読み込みをスキップしました({e})")
            return None
        if not isinstance(deck, dict):
            print(f"警告: {deck_path} のルートがオブジェクトではありません")
            return None

        def str_list(v):
            return [x for x in v if isinstance(x, str) and x] if isinstance(v, list) else []

        title = deck.get("title")
        if not isinstance(title, str) or not title:
            title = book_id
        audio = deck.get("audio") if isinstance(deck.get("audio"), dict) else {}
        duration = audio.get("duration")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            duration = None
        slides = deck.get("slides")
        slide_count = len(slides) if isinstance(slides, list) else 0

        series = deck.get("series")
        series_out = None
        if isinstance(series, dict) and isinstance(series.get("name"), str) and series["name"]:
            seq = series.get("sequence")
            series_out = {"name": series["name"],
                          "sequence": seq if isinstance(seq, str) else None}

        description = deck.get("description")
        if not isinstance(description, str):
            description = None

        added_at = deck.get("addedAt")
        if not isinstance(added_at, str):
            # 未指定なら deck.json の mtime を代替に使う(「最近追加した本」の並び替え用)
            try:
                added_at = time.strftime("%Y-%m-%d", time.localtime(deck_path.stat().st_mtime))
            except OSError:
                added_at = None

        return {
            "id": book_id,
            "library": self.id,
            "title": title,
            "authors": str_list(deck.get("authors")),
            "narrators": str_list(deck.get("narrators")),
            "series": series_out,
            "tags": str_list(deck.get("tags")),
            "description": description,
            "cover": self._find_cover(book_id, deck.get("cover")),
            "addedAt": added_at,
            "duration": duration,
            "slides": slide_count,
        }

    def _find_cover(self, book_id: str, declared):
        """カバー画像のファイル名を決める。deck.json の指定を優先し、無ければ既定名を探す。"""
        book_dir = self.root / book_id
        if isinstance(declared, str) and declared and is_safe_segment(declared):
            if (book_dir / declared).is_file():
                return declared
        for name in COVER_CANDIDATES:
            if (book_dir / name).is_file():
                return name
        return None

    # --- パス解決 -------------------------------------------------------

    def resolve_file(self, book_id: str, segs: list):
        """book_id 配下のファイルパスを解決する。シンボリックリンク/ジャンクションによる
        ライブラリ外への脱出を防ぐため、以下をすべて満たさない限り None を返す。

        - root は起動時に resolve() 済みの固定基準(ここでは再resolveしない)
        - book_id フォルダ自体、および経路上の中間ディレクトリがシンボリックリンク/
          リパースポイント(Windowsジャンクション含む)でないこと
        - 解決先そのものがシンボリックリンクでないこと
        - 解決先(resolve後)が root の配下に収まっていること(is_relative_to)
        """
        book_dir = self.root / book_id
        if os.path.islink(book_dir):
            return None
        current = book_dir
        for seg in segs[:-1]:
            current = current / seg
            if os.path.islink(current):
                return None
        target = current / segs[-1]
        if os.path.islink(target):
            return None
        resolved = target.resolve()
        if not resolved.is_relative_to(self.root):
            return None
        if not resolved.is_file():
            return None
        return resolved

    # --- コレクション ---------------------------------------------------

    def collections_path(self) -> Path:
        return self.root / COLLECTIONS_FILE

    def load_collections(self) -> list:
        p = self.collections_path()
        if not p.is_file():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"警告: {p} を読めません({e})")
            return []
        cols = data.get("collections") if isinstance(data, dict) else None
        if not isinstance(cols, list):
            return []
        out = []
        for c in cols:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            books = c.get("books")
            if not isinstance(name, str) or not name:
                continue
            out.append({
                "name": name,
                "books": [b for b in books if isinstance(b, str)] if isinstance(books, list) else [],
            })
        return out

    def save_collections(self, collections: list) -> None:
        """collections.json を書き出す。原子的に置き換えるため一時ファイル経由で行う。"""
        payload = {"kikimiru": 2, "collections": collections}
        p = self.collections_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


class KikimiruServer(ThreadingHTTPServer):
    # ThreadingHTTPServer既定の allow_reuse_address=1 はUnixでは無害だが、
    # WindowsではSO_REUSEADDRの意味が異なり、同一ポートへ別プロセスが
    # 後乗りできてしまう(乗っ取りの余地がある)ため明示的に無効化する。
    # インスタンス属性ではなくクラス属性で設定する必要がある
    # (server_bind() は __init__ 内、コンストラクタ完了前に実行されるため)。
    allow_reuse_address = False


class KikimiruHandler(BaseHTTPRequestHandler):
    libraries: dict          # serve() が設定する {library_id: Library}
    allowed_hosts: set       # serve() が設定する Host/Origin 検証用allowlist(小文字)

    # スロー攻撃(接続を張ったままダラダラ送る等)対策。無制限待ちを防ぐ
    timeout = 30
    # keep-alive を有効化する。以降の全レスポンスで正しい Content-Length を送ること前提
    protocol_version = "HTTP/1.1"

    # --- 共通ヘッダ ------------------------------------------------------

    def send_response(self, code, message=None):
        """全レスポンス(エラー応答含む)に X-Content-Type-Options: nosniff を付与する。"""
        super().send_response(code, message)
        self.send_header("X-Content-Type-Options", "nosniff")

    # --- Host/Origin検証(DNSリバインディング対策) ------------------------

    def _host_allowed(self) -> bool:
        host = self.headers.get("Host", "")
        return host.lower() in self.allowed_hosts

    def _origin_allowed(self) -> bool:
        """Originヘッダがある場合のみhostを検証する(cross-siteのfetch/XHRを拒否する目的)。

        同一オリジンのnavigationではOriginヘッダが付かないことがあるため、
        ヘッダ自体が無い場合は許容する。"null"(サンドボックスiframe等)は
        urlsplitでnetlocが空文字になり、allowlistに一致しないため自然に拒否される。
        """
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        netloc = urlsplit(origin).netloc
        return netloc.lower() in self.allowed_hosts

    # --- ルーティング ---------------------------------------------------

    def do_GET(self):
        self._route("GET", send_body=True)

    def do_HEAD(self):
        self._route("HEAD", send_body=False)

    def do_PUT(self):
        self._route("PUT", send_body=True)

    def _method_not_allowed(self):
        # message(第2引数)はHTTPステータス行にそのまま載りlatin-1制限があるため、
        # 日本語の説明は本文側に載るexplain(第3引数)に渡す
        self.send_error(405, explain="対応していないメソッドです")

    do_POST = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def _route(self, method: str, send_body: bool):
        # message(第2引数)はHTTPステータス行にそのまま載りlatin-1制限があるため、
        # 日本語の説明はすべてexplain(第3引数)に渡す
        if not self._host_allowed():
            return self.send_error(403, explain="Hostヘッダが許可されていません")
        if not self._origin_allowed():
            return self.send_error(403, explain="Originヘッダが許可されていません")

        raw_path, _, raw_query = self.path.partition("?")
        try:
            # ブラウザ/プレイヤーは非ASCII文字を必ず encodeURIComponent するため、
            # ここでデコードしないと日本語のブックIDが常に404になる。
            # デコード後の文字列に対して is_safe_segment 等の検査を行うことで、
            # デコードによって traversal が復活しないようにしている。
            path = unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            return self.send_error(400, explain="パスのパーセントデコードに失敗しました")
        query = parse_qs(raw_query, keep_blank_values=True)

        if method == "PUT":
            if path == "/api/collections":
                return self.put_collections(query)
            return self.send_error(405, explain="このパスへのPUTは対応していません")

        if path in ("/", "/player"):
            self.send_response(302)
            self.send_header("Location", "/web/player.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/libraries":
            return self.api_libraries(send_body)
        if path == "/api/books":
            return self.api_books(query, send_body)
        if path == "/api/authors":
            return self.api_people(query, "authors", send_body)
        if path == "/api/narrators":
            return self.api_people(query, "narrators", send_body)
        if path == "/api/series":
            return self.api_series(query, send_body)
        if path == "/api/stats":
            return self.api_stats(query, send_body)
        if path == "/api/collections":
            return self.api_collections(query, send_body)
        if path.startswith("/web/"):
            return self.send_web(path[len("/web/"):], send_body=send_body)
        if path.startswith("/books/"):
            return self.send_book_file(path[len("/books/"):], send_body=send_body)
        return self.send_error(404)

    # --- ライブラリ選択 --------------------------------------------------

    def _pick_library(self, query: dict):
        """クエリの library= からライブラリを選ぶ。未指定なら最初のライブラリ。

        戻り値は Library か None(不正なID)。
        """
        vals = query.get("library")
        if not vals or not vals[0]:
            return next(iter(self.libraries.values()), None)
        return self.libraries.get(vals[0])

    def _qs(self, query: dict, key: str) -> str:
        vals = query.get(key)
        return vals[0].strip() if vals and vals[0] else ""

    # --- API -------------------------------------------------------------

    def api_libraries(self, send_body: bool):
        out = [{"id": lib.id, "name": lib.name, "books": len(lib.books())}
               for lib in self.libraries.values()]
        self.send_json({"kikimiru": 2, "libraries": out}, send_body=send_body)

    def api_books(self, query: dict, send_body: bool):
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        books = lib.books()

        # 絞り込み(完全一致)
        author = self._qs(query, "author")
        if author:
            books = [b for b in books if author in b["authors"]]
        narrator = self._qs(query, "narrator")
        if narrator:
            books = [b for b in books if narrator in b["narrators"]]
        series = self._qs(query, "series")
        if series:
            books = [b for b in books if b["series"] and b["series"]["name"] == series]
        tag = self._qs(query, "tag")
        if tag:
            books = [b for b in books if tag in b["tags"]]

        # 検索(タイトル・著者・話者・シリーズ・タグ・説明を対象にした部分一致)
        q = self._qs(query, "q").casefold()
        if q:
            def hit(b):
                hay = [b["title"], b["description"] or ""]
                hay += b["authors"] + b["narrators"] + b["tags"]
                if b["series"]:
                    hay.append(b["series"]["name"])
                return any(q in (s or "").casefold() for s in hay)
            books = [b for b in books if hit(b)]

        # 並び替え
        sort = self._qs(query, "sort") or "title"
        if sort == "added":
            books = sorted(books, key=lambda b: (b["addedAt"] or ""), reverse=True)
        elif sort == "duration":
            books = sorted(books, key=lambda b: (b["duration"] or 0), reverse=True)
        elif sort == "series":
            def series_key(b):
                if not b["series"]:
                    return (1, "", 0.0, b["title"])
                seq = b["series"]["sequence"]
                try:
                    seq_num = float(seq) if seq else 0.0
                except ValueError:
                    seq_num = 0.0
                return (0, b["series"]["name"], seq_num, b["title"])
            books = sorted(books, key=series_key)
        else:
            books = sorted(books, key=lambda b: b["title"])

        self.send_json({"kikimiru": 2, "library": lib.id, "books": books}, send_body=send_body)

    def api_people(self, query: dict, field: str, send_body: bool):
        """著者(authors)/話者(narrators)の一覧を集計して返す。"""
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        counts = {}
        covers = {}
        for b in lib.books():
            for name in b[field]:
                counts[name] = counts.get(name, 0) + 1
                if name not in covers and b["cover"]:
                    covers[name] = {"book": b["id"], "cover": b["cover"]}
        out = [{"name": n, "books": c,
                "sample": covers.get(n)} for n, c in sorted(counts.items())]
        key = "authors" if field == "authors" else "narrators"
        self.send_json({"kikimiru": 2, "library": lib.id, key: out}, send_body=send_body)

    def api_series(self, query: dict, send_body: bool):
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        groups = {}
        for b in lib.books():
            if not b["series"]:
                continue
            name = b["series"]["name"]
            g = groups.setdefault(name, {"name": name, "books": 0, "duration": 0.0, "sample": None})
            g["books"] += 1
            if b["duration"]:
                g["duration"] += b["duration"]
            if g["sample"] is None and b["cover"]:
                g["sample"] = {"book": b["id"], "cover": b["cover"]}
        out = sorted(groups.values(), key=lambda g: g["name"])
        self.send_json({"kikimiru": 2, "library": lib.id, "series": out}, send_body=send_body)

    def api_stats(self, query: dict, send_body: bool):
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        books = lib.books()
        authors, narrators, series, tags = set(), set(), set(), set()
        total_duration, total_slides, with_cover = 0.0, 0, 0
        for b in books:
            authors.update(b["authors"])
            narrators.update(b["narrators"])
            if b["series"]:
                series.add(b["series"]["name"])
            tags.update(b["tags"])
            if b["duration"]:
                total_duration += b["duration"]
            total_slides += b["slides"]
            if b["cover"]:
                with_cover += 1
        self.send_json({
            "kikimiru": 2,
            "library": lib.id,
            "stats": {
                "books": len(books),
                "duration": round(total_duration, 3),
                "slides": total_slides,
                "authors": len(authors),
                "narrators": len(narrators),
                "series": len(series),
                "tags": len(tags),
                "withCover": with_cover,
            },
        }, send_body=send_body)

    def api_collections(self, query: dict, send_body: bool):
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        self.send_json({"kikimiru": 2, "library": lib.id,
                        "collections": lib.load_collections()}, send_body=send_body)

    def put_collections(self, query: dict):
        """コレクションを保存する。ライブラリルートの collections.json を丸ごと置き換える。

        受け付けるのは {"collections":[{"name":str,"books":[str,...]}, ...]} のみ。
        書き込み先はライブラリルート直下の固定ファイル1つで、パスは外部入力から組み立てない。
        """
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.send_error(400, explain="Content-Lengthが不正です")
        if length <= 0 or length > MAX_PUT_BYTES:
            return self.send_error(413, explain="本文の長さが不正です")
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self.send_error(400, explain="JSONとして解釈できません")
        cols = body.get("collections") if isinstance(body, dict) else None
        if not isinstance(cols, list):
            return self.send_error(400, explain="collections は配列である必要があります")

        # 既知のブックIDだけを受け付ける(存在しないIDの混入を防ぐ)
        known = {b["id"] for b in lib.books()}
        cleaned = []
        for c in cols:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            ids = c.get("books")
            ids = [b for b in ids if isinstance(b, str) and b in known] if isinstance(ids, list) else []
            cleaned.append({"name": name.strip(), "books": ids})
        try:
            lib.save_collections(cleaned)
        except OSError as e:
            print(f"警告: collections.json の保存に失敗しました({e})")
            return self.send_error(500, explain="保存に失敗しました")
        self.send_json({"kikimiru": 2, "library": lib.id, "collections": cleaned}, send_body=True)

    def send_json(self, obj, send_body: bool = True):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    # --- 静的配信 ---------------------------------------------------------

    def send_web(self, name: str, send_body: bool):
        entry = WEB_ALLOWLIST.get(name)
        if entry is None:
            return self.send_error(404)
        target, ctype = entry
        return self.send_file(target, ctype, send_body=send_body)

    def send_book_file(self, rest: str, send_body: bool):
        """/books/<library>/<book_id>/<file...> を配信する。"""
        parts = rest.split("/")
        if len(parts) < 3:
            return self.send_error(404)
        lib_id, book_id, segs = parts[0], parts[1], parts[2:]
        if not is_safe_segment(lib_id) or not is_safe_segment(book_id):
            return self.send_error(404)
        if not segs or not all(is_safe_segment(s) for s in segs):
            return self.send_error(404)
        lib = self.libraries.get(lib_id)
        if lib is None:
            return self.send_error(404)
        ext = Path(segs[-1]).suffix.lower()
        ctype = BOOK_CONTENT_TYPES.get(ext)
        if ctype is None:
            return self.send_error(404)
        target = lib.resolve_file(book_id, segs)
        if target is None:
            return self.send_error(404)
        return self.send_file(target, ctype, send_body=send_body)

    def send_file(self, target: Path, ctype: str, send_body: bool):
        """target を配信する。Range指定があれば206、無ければ200で返す。

        send_body=False(HEAD)の場合はヘッダのみ送りボディは書き込まない。
        """
        if not target.is_file():
            return self.send_error(404)

        size = target.stat().st_size
        start, end, status = 0, size - 1, 200

        range_header = self.headers.get("Range")
        if range_header:
            m = RANGE_RE.match(range_header.strip())
            if m and (m.group(1) or m.group(2)):
                try:
                    if m.group(1):
                        start = int(m.group(1))
                        end = int(m.group(2)) if m.group(2) else size - 1
                    else:
                        start = max(0, size - int(m.group(2)))
                        end = size - 1
                    end = min(end, size - 1)
                except ValueError:
                    start, end = -1, -1  # 下の不正判定に必ず引っかからせる
                if start < 0 or start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            # マッチしない(桁数超過や不正な形式)場合はRangeを無視し通常の200を返す

        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if not send_body:
            return
        with target.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(65536, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, BrokenPipeError):
                    return
                remaining -= len(chunk)


def build_allowed_hosts(bind: str, port: int) -> set:
    """Host/Originヘッダ検証用のallowlistを組み立てる。

    127.0.0.1/localhost に加え、--bind で指定した実際のIPも許可する。
    既定ポート(80)ではブラウザがHostヘッダにポート番号を付けないことがあるため、
    ポート無しの形式も念のため含める。
    """
    hosts = {
        f"127.0.0.1:{port}", "127.0.0.1",
        f"localhost:{port}", "localhost",
        f"{bind}:{port}", bind,
    }
    return {h.lower() for h in hosts}


def parse_library_arg(spec: str):
    """--library の指定を (id, name, path) に分解する。

    `名前=パス` 形式なら名前をライブラリIDかつ表示名にする。
    パスだけなら末尾のディレクトリ名をIDにする。
    Windowsのドライブレター(`D:/books` の `:`)と取り違えないよう、
    `=` の有無で判定する。
    """
    if "=" in spec:
        name, _, raw_path = spec.partition("=")
        name = name.strip()
        path = Path(raw_path.strip())
        if not name:
            name = path.name
    else:
        path = Path(spec.strip())
        name = path.name
    return name, name, path


def main() -> None:
    ap = argparse.ArgumentParser(description="kikimiru self-host server (Phase 1)")
    ap.add_argument("--library", action="append", default=None, metavar="[名前=]パス",
                    help="ブックフォルダ群のディレクトリ。複数回指定できる"
                         "(例: --library 技術書=D:/books/tech)。既定: 同梱デモ")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind先IP(既定: 127.0.0.1。LAN/VPNへ出す場合のみ明示)")
    ap.add_argument("--port", type=int, default=8484)
    args = ap.parse_args()

    # 既定は同梱デモ。ディレクトリ名(library)がIDになると紛らわしいため demo と名付ける
    specs = args.library or ["demo=" + str(APP_ROOT / "demo" / "library")]
    libraries = {}
    for spec in specs:
        lib_id, name, path = parse_library_arg(spec)
        if not path.is_dir():
            print(f"エラー: ライブラリが見つかりません: {path}")
            sys.exit(1)
        if not is_safe_segment(lib_id):
            print(f"エラー: ライブラリ名に使えない文字が含まれています: {lib_id!r}")
            sys.exit(1)
        if lib_id in libraries:
            print(f"エラー: ライブラリ名が重複しています: {lib_id!r}")
            sys.exit(1)
        libraries[lib_id] = Library(lib_id, path, name)

    if args.bind not in ("127.0.0.1", "localhost"):
        print(
            "警告: 認証が実装されていないため、到達可能な全員がライブラリを読めます"
            f"(bind={args.bind})。信頼できないネットワークへは公開しないでください。"
        )

    KikimiruHandler.libraries = libraries
    KikimiruHandler.allowed_hosts = build_allowed_hosts(args.bind, args.port)

    srv = KikimiruServer((args.bind, args.port), KikimiruHandler)
    print(f"kikimiru: http://{args.bind}:{args.port}/", flush=True)
    for lib in libraries.values():
        print(f"  ライブラリ {lib.id}: {lib.root}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
