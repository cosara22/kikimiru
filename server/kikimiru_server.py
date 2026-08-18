# -*- coding: utf-8 -*-
"""kikimiru server — スライド同期音声のself-host配信サーバ(Phase 0)。

使い方:
    python server/kikimiru_server.py [--library <dir>] [--bind 127.0.0.1] [--port 8484]

- --library 配下の「1フォルダ=1ブック」(deck.json 必須・音声ファイル同梱)を配信する
- iOS Safari の <audio> シークに必要な HTTP Range(206) に対応する
- 既定 bind は 127.0.0.1(安全側)。LAN・VPN へ公開する場合のみ --bind で明示する
- 認証機構は無い(私的利用限定の想定)。非ループバックへ bind すると起動時に警告を表示する
- Host/Origin ヘッダを検証しているため、DNSリバインディングを狙ったブラウザ経由の
  アクセスは拒否される(--bind で指定したIP:port と 127.0.0.1/localhost のみ許可)

エンドポイント:
    GET  /                      -> /web/player.html へリダイレクト
    GET  /web/player.html       -> プレイヤー静的ファイル(固定allowlist配信。他ファイルは404)
    GET  /api/books             -> ブック一覧(deck.json の要約。壊れたdeck.jsonはスキップ)
    GET  /books/<id>/<file>     -> ブックフォルダ内ファイル(拡張子allowlist・Range対応)
    HEAD 上記いずれも同一ルーティングでボディ無し応答(SimpleHTTPRequestHandler非継承のため
         カレントディレクトリ配下の任意ファイルが露出することはない)
    その他メソッド(POST/PUT/DELETE/PATCH/OPTIONS) -> 405
"""
import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

APP_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = APP_ROOT / "web"

# /web/ 配下で配信を許可する固定ファイルの一覧(name -> (実パス, Content-Type))。
# 動的にパスを解決せず allowlist から引くだけにすることで、UNCパスや絶対パスの
# 注入によるbase乗っ取りを構造的に防ぐ。Phase 0では player.html のみ。
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

# Range ヘッダの数値部分は桁数を18桁までに制限する。
# 際限なく長い数字列を int() へ渡すとパース自体がエラーになりうるため、
# 正規表現の時点で桁数を絞って未処理例外を防ぐ(万一 ValueError が出ても下流で捕捉する)。
RANGE_RE = re.compile(r"bytes=(\d{0,18})-(\d{0,18})$")


def is_safe_segment(seg: str) -> bool:
    """パス構成要素(book_id・ファイル名の1階層分)がbase外へ逸脱しないかを判定する。

    以前はホワイトリスト方式(使える文字を列挙)だったが、長音符「ー」・中黒「・」・
    「々」等の正当な日本語文字まで弾いてしまっていた。危険な構成要素だけを拒否する
    ブラックリスト方式に変更する。
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


class KikimiruServer(ThreadingHTTPServer):
    # ThreadingHTTPServer既定の allow_reuse_address=1 はUnixでは無害だが、
    # WindowsではSO_REUSEADDRの意味が異なり、同一ポートへ別プロセスが
    # 後乗りできてしまう(乗っ取りの余地がある)ため明示的に無効化する。
    # インスタンス属性ではなくクラス属性で設定する必要がある
    # (server_bind() は __init__ 内、コンストラクタ完了前に実行されるため)。
    allow_reuse_address = False


class KikimiruHandler(BaseHTTPRequestHandler):
    library_root: Path       # serve() が起動時に一度だけ resolve() して設定する固定基準
    allowed_hosts: set        # serve() が設定する Host/Origin 検証用allowlist(小文字)

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
        self._route(send_body=True)

    def do_HEAD(self):
        self._route(send_body=False)

    def _method_not_allowed(self):
        # message(第2引数)はHTTPステータス行にそのまま載りlatin-1制限があるため、
        # 日本語の説明は本文側に載るexplain(第3引数)に渡す
        self.send_error(405, explain="対応していないメソッドです")

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def _route(self, send_body: bool):
        # message(第2引数)はHTTPステータス行にそのまま載りlatin-1制限があるため、
        # 日本語の説明はここでも全てexplain(第3引数)に渡す
        if not self._host_allowed():
            return self.send_error(403, explain="Hostヘッダが許可されていません")
        if not self._origin_allowed():
            return self.send_error(403, explain="Originヘッダが許可されていません")

        raw_path = self.path.split("?", 1)[0]
        try:
            # ブラウザ/プレイヤーは非ASCII文字を必ず encodeURIComponent するため、
            # ここでデコードしないと日本語のブックIDが常に404になる。
            # デコード後の文字列に対して is_safe_segment 等の検査を行うことで、
            # デコードによって traversal が復活しないようにしている。
            path = unquote(raw_path, errors="strict")
        except UnicodeDecodeError:
            return self.send_error(400, explain="パスのパーセントデコードに失敗しました")

        if path in ("/", "/player"):
            self.send_response(302)
            self.send_header("Location", "/web/player.html")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/books":
            return self.send_books(send_body=send_body)
        if path.startswith("/web/"):
            return self.send_web(path[len("/web/"):], send_body=send_body)
        if path.startswith("/books/"):
            return self.send_book_file(path[len("/books/"):], send_body=send_body)
        return self.send_error(404)

    def send_web(self, name: str, send_body: bool):
        entry = WEB_ALLOWLIST.get(name)
        if entry is None:
            return self.send_error(404)
        target, ctype = entry
        return self.send_file(target, ctype, send_body=send_body)

    def send_book_file(self, rest: str, send_body: bool):
        book_id, _, fname = rest.partition("/")
        if not fname or not is_safe_segment(book_id):
            return self.send_error(404)
        segs = fname.split("/")
        if not all(is_safe_segment(s) for s in segs):
            return self.send_error(404)
        ext = Path(segs[-1]).suffix.lower()
        ctype = BOOK_CONTENT_TYPES.get(ext)
        if ctype is None:
            return self.send_error(404)
        target = self.resolve_book_path(book_id, segs)
        if target is None:
            return self.send_error(404)
        return self.send_file(target, ctype, send_body=send_body)

    def resolve_book_path(self, book_id: str, segs: list):
        """book_id配下のファイルパスを解決する。シンボリックリンク/ジャンクションによる
        ライブラリ外への脱出を防ぐため、以下をすべて満たさない限り None を返す。

        - library_root は起動時に一度だけ resolve() 済みの固定基準(ここでは再resolveしない。
          book_idフォルダがジャンクションだとリンク先が新しい基準になり検査が無意味化するため)
        - book_id フォルダ自体、および経路上の中間ディレクトリがシンボリックリンク/
          リパースポイント(Windowsジャンクション含む)でないこと
        - 解決先そのものがシンボリックリンクでないこと
        - 解決先(resolve後)が library_root の配下に収まっていること(is_relative_to)
        """
        book_dir = self.library_root / book_id
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
        if not resolved.is_relative_to(self.library_root):
            return None
        if not resolved.is_file():
            return None
        return resolved

    def send_books(self, send_body: bool):
        """ブック一覧を返す。1冊のdeck.jsonが壊れていても他のブックは一覧に出す。

        JSONとして妥当でも型が期待と違う(deckがlist・titleが数値等)場合に
        AttributeError/TypeErrorが飛ぶのを防ぐため、各フィールドを isinstance で
        確認しながら安全に取り出す。走査自体の失敗(OSError等)も含め、
        関数全体を例外から保護し、失敗しても空リストで応答する。
        """
        books = []
        try:
            for deck_path in sorted(self.library_root.glob("*/deck.json")):
                try:
                    deck = json.loads(deck_path.read_text(encoding="utf-8"))
                    if not isinstance(deck, dict):
                        raise ValueError("deck.jsonのルートがオブジェクトではありません")
                    title = deck.get("title")
                    if not isinstance(title, str) or not title:
                        title = deck_path.parent.name
                    audio = deck.get("audio")
                    duration = audio.get("duration") if isinstance(audio, dict) else None
                    slides = deck.get("slides")
                    slide_count = len(slides) if isinstance(slides, list) else 0
                    books.append({
                        "id": deck_path.parent.name,
                        "title": title,
                        "duration": duration,
                        "slides": slide_count,
                    })
                except (OSError, json.JSONDecodeError, ValueError, AttributeError, TypeError) as e:
                    print(f"警告: {deck_path} の読み込みをスキップしました({e})")
                    continue
        except OSError as e:
            print(f"警告: ライブラリの走査に失敗しました({e})")
        self.send_json({"kikimiru": 1, "books": books}, send_body=send_body)

    def send_json(self, obj, send_body: bool = True):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    # --- 静的配信(Range対応) --------------------------------------------

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


def main() -> None:
    ap = argparse.ArgumentParser(description="kikimiru self-host server (Phase 0)")
    ap.add_argument("--library", default=str(APP_ROOT / "demo" / "library"),
                    help="ブックフォルダ群のディレクトリ(既定: 同梱デモ)")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind先IP(既定: 127.0.0.1。LAN/VPNへ出す場合のみ明示)")
    ap.add_argument("--port", type=int, default=8484)
    args = ap.parse_args()

    library = Path(args.library).resolve()
    if not library.is_dir():
        print(f"エラー: ライブラリが見つかりません: {library}")
        sys.exit(1)

    if args.bind not in ("127.0.0.1", "localhost"):
        print(
            "警告: 認証が実装されていないため、到達可能な全員がライブラリを読めます"
            f"(bind={args.bind})。信頼できないネットワークへは公開しないでください。"
        )

    # ライブラリルートは起動時に一度だけ resolve() し、以降固定の基準として使う
    # (book_idフォルダがジャンクションでもこの基準は変わらないようにするため)。
    KikimiruHandler.library_root = library
    KikimiruHandler.allowed_hosts = build_allowed_hosts(args.bind, args.port)

    srv = KikimiruServer((args.bind, args.port), KikimiruHandler)
    print(f"kikimiru: http://{args.bind}:{args.port}/  (library: {library})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
