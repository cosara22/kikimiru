# -*- coding: utf-8 -*-
"""kikimiru server — スライド同期音声のself-host配信サーバ(Phase 1後半)。

使い方:
    python server/kikimiru_server.py --set-password       # 初回: パスワードを設定
    python server/kikimiru_server.py [--library <dir>] [--library 名前=<dir> ...]
                                     [--bind 127.0.0.1] [--port 8484]
                                     [--state-dir <dir>] [--allow-host host[:port] ...]

- --library は複数回指定できる。`名前=パス` の形式で表示名を付けられる
  (例: --library 技術書=D:/books/tech --library 語学=D:/books/lang)。
  `パス` だけを渡した場合はディレクトリ名がライブラリIDになる
- 各ライブラリ配下の「1フォルダ=1ブック」(deck.json 必須・音声ファイル同梱)を配信する
- iOS Safari の <audio> シークに必要な HTTP Range(206) に対応する
- 既定 bind は 127.0.0.1(安全側)。LAN・VPN へ公開する場合のみ --bind で明示する
- 認証は必須(パスワード1つ+セッションCookie)。未設定の場合は起動を拒否し、
  --set-password での設定を促す。盗聴対策(TLS)は持たないため、信頼できない
  ネットワークではリバースプロキシ/VPN経由で使うこと(docs/DEPLOY.md)
- Host/Origin ヘッダを検証しているため、DNSリバインディングを狙ったブラウザ経由の
  アクセスは拒否される。リバースプロキシやポートリマップ経由で別のホスト名を
  使う場合は --allow-host で許可を追加する
- 認証・セッション・進捗は --state-dir(既定: ~/.kikimiru)に保存される

エンドポイント:
    GET  /                          -> /web/player.html へリダイレクト(クエリ引継ぎ)
    GET  /web/player.html           -> プレイヤー静的ファイル(固定allowlist配信・認証免除)
    POST /api/login                 -> ログイン(JSON {password} -> セッションCookie)
    POST /api/logout                -> ログアウト(セッション失効)
    GET  /api/libraries             -> ライブラリ一覧
    GET  /api/books?library=&q=&sort=&author=&narrator=&series=&tag=
                                    -> ブック一覧(検索・絞り込み・並び替え)
    GET  /api/books/<id>?library=   -> ブック単体の書誌(詳細画面用)
    GET  /api/authors?library=      -> 著者一覧(ブック数付き)
    GET  /api/narrators?library=    -> 話者一覧(ブック数付き)
    GET  /api/series?library=       -> シリーズ一覧(巻数・代表カバー付き)
    GET  /api/stats?library=        -> 統計
    GET  /api/collections?library=  -> コレクション一覧
    PUT  /api/collections?library=  -> コレクションの保存(collections.json へ書き込み)
    GET  /api/progress              -> 再生進捗の全件(端末間同期用)
    PUT  /api/progress              -> 再生進捗の部分更新(Last-Write-Winsマージ)
    GET  /books/<library>/<id>/<file> -> ブックフォルダ内ファイル(拡張子allowlist・Range対応)
    HEAD 上記GETと同一ルーティングでボディ無し応答(SimpleHTTPRequestHandler非継承のため
         カレントディレクトリ配下の任意ファイルが露出することはない)
    その他メソッド(DELETE/PATCH/OPTIONS) -> 405
"""
import argparse
import getpass
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
from http.cookies import SimpleCookie
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
    # PWA一式(すべて自作素材)。player.html と sw.js は更新の即時反映が必要な
    # ため send_web 側で Cache-Control: no-cache を付ける
    "manifest.webmanifest": (WEB_DIR / "manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "sw.js": (WEB_DIR / "sw.js", "text/javascript; charset=utf-8"),
    "icon-32.png": (WEB_DIR / "icon-32.png", "image/png"),
    "icon-192.png": (WEB_DIR / "icon-192.png", "image/png"),
    "icon-512.png": (WEB_DIR / "icon-512.png", "image/png"),
    "icon-maskable-512.png": (WEB_DIR / "icon-maskable-512.png", "image/png"),
    "apple-touch-icon.png": (WEB_DIR / "apple-touch-icon.png", "image/png"),
}

# ブラウザ/SWにキャッシュさせない配信物(古いシェル・古いSWの固着を防ぐ)
WEB_NO_CACHE = {"player.html", "sw.js", "manifest.webmanifest"}

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

# --- 認証・状態保存の定数 -------------------------------------------------

# サーバ状態(認証・セッション・進捗)の既定置き場。ライブラリとは分離し、
# ライブラリを読み取り専用でマウントする運用(Docker等)と両立させる
DEFAULT_STATE_DIR = Path.home() / ".kikimiru"
AUTH_FILE = "auth.json"
SESSIONS_FILE = "sessions.json"
PROGRESS_FILE = "progress.json"

SESSION_COOKIE = "kikimiru_session"
SESSION_TTL = 30 * 24 * 3600          # 30日(アクセスでローリング延長)
PBKDF2_ITERATIONS = 600_000           # OWASP Password Storage Cheat Sheet の現行推奨値
MAX_LOGIN_BYTES = 4096                # ログイン本文の上限
MAX_PROGRESS_KEYS_PER_PUT = 500       # 1回のPUTで受け付ける進捗キー数
MAX_PROGRESS_KEYS_TOTAL = 5000        # サーバ側で保持する進捗キー数の上限


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


# --- 原子的書き込み(パス毎に直列化) ---------------------------------------

_WRITE_LOCKS: dict = {}
_WRITE_LOCKS_GUARD = threading.Lock()


def write_json_atomic(path: Path, payload) -> None:
    """JSONを一意な一時ファイル経由で原子的に書き出す。

    ThreadingHTTPServer では同一ファイルへのPUTが並行し得るため、パス毎の
    Lock で直列化する。一時ファイル名にPID+スレッドIDを含め、万一Lockを
    経ない書き込みと交錯しても一時ファイル名が衝突しないようにする
    (旧 save_collections はtmp名固定+無ロックで、同時PUT時にWindowsで
    PermissionError になり得た)。
    """
    key = str(path)
    with _WRITE_LOCKS_GUARD:
        lock = _WRITE_LOCKS.setdefault(key, threading.Lock())
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    with lock:
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(data, encoding="utf-8")
        try:
            tmp.replace(path)
        except OSError:
            try:
                tmp.unlink()
            except OSError:
                pass
            raise


def load_json_file(path: Path):
    """JSONファイルを読む。無い・壊れている場合は None(呼び出し側が既定値を決める)。"""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"警告: {path} を読めません({e})")
        return None


# --- 認証(パスワード1つ+セッションCookie) --------------------------------

def load_auth(state_dir: Path):
    """auth.json を読んで検証する。未設定・不正なら None。"""
    data = load_json_file(state_dir / AUTH_FILE)
    if not isinstance(data, dict):
        return None
    if data.get("algo") != "pbkdf2_sha256":
        print(f"警告: {AUTH_FILE} のalgoが未知のため無視します")
        return None
    if not (isinstance(data.get("iterations"), int)
            and isinstance(data.get("salt_hex"), str)
            and isinstance(data.get("hash_hex"), str)):
        print(f"警告: {AUTH_FILE} の形式が不正のため無視します")
        return None
    return data


def verify_password(auth: dict, password: str) -> bool:
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"),
        bytes.fromhex(auth["salt_hex"]), auth["iterations"])
    return hmac.compare_digest(digest.hex(), auth["hash_hex"])


def set_password_interactive(state_dir: Path) -> int:
    """--set-password: 対話でパスワードを設定して終了する。戻り値はexit code。"""
    pw = getpass.getpass("新しいパスワード(8文字以上): ")
    if len(pw) < 8:
        print("エラー: パスワードは8文字以上にしてください")
        return 1
    if getpass.getpass("もう一度入力: ") != pw:
        print("エラー: 入力が一致しません")
        return 1
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    state_dir.mkdir(parents=True, exist_ok=True)
    write_json_atomic(state_dir / AUTH_FILE, {
        "v": 1, "algo": "pbkdf2_sha256", "iterations": PBKDF2_ITERATIONS,
        "salt_hex": salt.hex(), "hash_hex": digest.hex(),
    })
    print(f"パスワードを設定しました: {state_dir / AUTH_FILE}")
    print("既存の全端末のログインを無効にしたい場合は sessions.json を削除してください。")
    return 0


class SessionStore:
    """セッショントークンの保持と永続化。

    生トークンはディスクに置かず、SHA-256ハッシュをキーに期限を持つ。
    再起動してもログインが切れないよう sessions.json へ原子的に永続化する。
    """

    def __init__(self, state_dir: Path):
        self.path = state_dir / SESSIONS_FILE
        self.lock = threading.Lock()
        self.sessions = {}  # sha256(token).hexdigest() -> 失効epoch秒
        data = load_json_file(self.path)
        if isinstance(data, dict) and isinstance(data.get("sessions"), dict):
            now = time.time()
            for k, v in data["sessions"].items():
                if isinstance(k, str) and isinstance(v, (int, float)) and v > now:
                    self.sessions[k] = float(v)

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _persist_locked(self) -> None:
        try:
            write_json_atomic(self.path, {"v": 1, "sessions": self.sessions})
        except OSError as e:
            print(f"警告: {self.path} の保存に失敗しました({e})")

    def issue(self) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.lock:
            # ついでに失効分を掃除する
            self.sessions = {k: v for k, v in self.sessions.items() if v > now}
            self.sessions[self._key(token)] = now + SESSION_TTL
            self._persist_locked()
        return token

    def check(self, token: str) -> bool:
        if not token:
            return False
        key = self._key(token)
        now = time.time()
        with self.lock:
            exp = self.sessions.get(key)
            if not exp or exp <= now:
                self.sessions.pop(key, None)
                return False
            # ローリング延長。書き込みは1日1回程度に間引く
            if exp - now < SESSION_TTL - 86400:
                self.sessions[key] = now + SESSION_TTL
                self._persist_locked()
            return True

    def revoke(self, token: str) -> None:
        with self.lock:
            if self.sessions.pop(self._key(token), None) is not None:
                self._persist_locked()


class LoginThrottle:
    """ログイン総当たり対策。IP毎に失敗を数え、閾値超過で指数バックオフする。

    判定はPBKDF2計算の前に行い、ハッシュ計算を使ったCPU DoSも防ぐ。
    """

    WINDOW = 900        # 15分
    THRESHOLD = 5       # 窓内の許容失敗回数
    BASE_LOCK = 60      # 初回ロック秒
    MAX_LOCK = 3600

    def __init__(self):
        self.lock = threading.Lock()
        self.fails = {}   # ip -> [失敗epoch, ...]
        self.locked = {}  # ip -> 解除epoch

    def retry_after(self, ip: str) -> int:
        """ロック中なら残り秒数(1以上)、受け付けるなら0。"""
        now = time.time()
        with self.lock:
            until = self.locked.get(ip, 0)
            if until > now:
                return max(1, int(until - now))
            return 0

    def record_failure(self, ip: str) -> None:
        now = time.time()
        with self.lock:
            lst = [t for t in self.fails.get(ip, []) if now - t < self.WINDOW]
            lst.append(now)
            self.fails[ip] = lst
            if len(lst) >= self.THRESHOLD:
                over = len(lst) - self.THRESHOLD
                self.locked[ip] = now + min(self.MAX_LOCK, self.BASE_LOCK * (2 ** over))

    def record_success(self, ip: str) -> None:
        with self.lock:
            self.fails.pop(ip, None)
            self.locked.pop(ip, None)


# --- 再生進捗の保存(端末間同期) -------------------------------------------

def valid_progress_key(key: str) -> bool:
    """進捗キーは "ライブラリID/ブックID" の2セグメント固定。"""
    parts = key.split("/")
    return len(parts) == 2 and all(is_safe_segment(p) for p in parts)


def clean_progress_record(rec) -> dict | None:
    """進捗レコード {t,d,at,s?,n?} を型検証して正規化する。不正は None。"""
    if not isinstance(rec, dict):
        return None
    t, d, at = rec.get("t"), rec.get("d"), rec.get("at")
    if not all(isinstance(x, (int, float)) and x >= 0 for x in (t, d, at)):
        return None
    out = {"t": float(t), "d": float(d), "at": float(at)}
    for k in ("s", "n"):
        v = rec.get(k)
        if isinstance(v, int) and v > 0:
            out[k] = v
    return out


def merge_progress(store: dict, incoming: dict) -> int:
    """incoming を store へ Last-Write-Wins(atの新しい方)でマージし、採用件数を返す。"""
    accepted = 0
    for key, rec in incoming.items():
        if not isinstance(key, str) or not valid_progress_key(key):
            continue
        cleaned = clean_progress_record(rec)
        if cleaned is None:
            continue
        cur = store.get(key)
        if cur is None or cleaned["at"] > cur.get("at", 0):
            store[key] = cleaned
            accepted += 1
    return accepted


class ProgressStore:
    """進捗の全件保持と永続化(キー: "ライブラリID/ブックID")。"""

    def __init__(self, state_dir: Path):
        self.path = state_dir / PROGRESS_FILE
        self.lock = threading.Lock()
        self.progress = {}
        data = load_json_file(self.path)
        if isinstance(data, dict) and isinstance(data.get("progress"), dict):
            merge_progress(self.progress, data["progress"])

    def snapshot(self) -> dict:
        with self.lock:
            return dict(self.progress)

    def merge(self, incoming: dict) -> int:
        with self.lock:
            accepted = merge_progress(self.progress, incoming)
            if accepted:
                # 上限超過時は最終更新の古いものから捨てる(暴走したクライアント対策)
                if len(self.progress) > MAX_PROGRESS_KEYS_TOTAL:
                    keep = sorted(self.progress.items(),
                                  key=lambda kv: kv[1].get("at", 0),
                                  reverse=True)[:MAX_PROGRESS_KEYS_TOTAL]
                    self.progress = dict(keep)
                try:
                    write_json_atomic(self.path, {"v": 1, "progress": self.progress})
                except OSError as e:
                    print(f"警告: {self.path} の保存に失敗しました({e})")
            return accepted


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
        """collections.json を原子的に書き出す(パス毎Lockで同時PUTも直列化)。"""
        write_json_atomic(self.collections_path(),
                          {"kikimiru": 2, "collections": collections})


class KikimiruServer(ThreadingHTTPServer):
    # SO_REUSEADDR の意味はOSで異なるため、OS別に切り替える:
    # - Windows: 同一ポートへ別プロセスが後乗りできてしまう(乗っ取りの余地)ため無効化
    # - Unix系: 後乗りは許さず TIME_WAIT 中の再bindだけを許す標準動作。無効にすると
    #   サーバ再起動が最大60秒 EADDRINUSE で失敗する(Linuxで実測)
    # インスタンス属性ではなくクラス属性で設定する必要がある
    # (server_bind() は __init__ 内、コンストラクタ完了前に実行されるため)。
    allow_reuse_address = (sys.platform != "win32")


class KikimiruHandler(BaseHTTPRequestHandler):
    libraries: dict          # main() が設定する {library_id: Library}
    allowed_hosts: set       # main() が設定する Host/Origin 検証用allowlist(小文字)
    auth: dict = None        # main() が設定する auth.json の内容(None なら認証無効=テスト用)
    sessions: SessionStore = None
    throttle: LoginThrottle = None
    progress_store: ProgressStore = None

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

    def do_POST(self):
        self._route("POST", send_body=True)

    def _method_not_allowed(self):
        # message(第2引数)はHTTPステータス行にそのまま載りlatin-1制限があるため、
        # 日本語の説明は本文側に載るexplain(第3引数)に渡す
        # 注意: OPTIONS を405のままにすることはCSRF防御の一部
        # (CookieつきクロスサイトPUT/POSTのpreflightを常に不成立にする)
        self.send_error(405, explain="対応していないメソッドです")

    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    # --- 認証 -----------------------------------------------------------

    @staticmethod
    def _auth_exempt(path: str) -> bool:
        """認証なしで到達できるパス。アプリシェル(静的allowlist)とログインのみ。

        /web/ 配下はWEB_ALLOWLISTの固定ファイルだけが配信され私的データを
        含まない。書誌・音声・カバー・進捗などのデータ面はすべて認証必須。
        """
        return path in ("/", "/player", "/api/login") or path.startswith("/web/")

    def _session_token(self) -> str:
        cookie_header = self.headers.get("Cookie")
        if not cookie_header:
            return ""
        try:
            cookie = SimpleCookie(cookie_header)
        except Exception:
            return ""
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else ""

    def _authed(self) -> bool:
        return self.sessions is not None and self.sessions.check(self._session_token())

    def _send_unauthorized(self):
        # send_error はWWW-Authenticate等の細工ができないため自前で組む。
        # クライアントは401を検知してログインオーバーレイを表示する
        self.send_json({"kikimiru": 2, "error": "auth_required"}, status=401)

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

        # 認証ゲート(Host/Origin検証の直後・全メソッド共通の1箇所)。
        # auth が None のとき(ユニットテスト等)は従来どおり素通しする
        if self.auth is not None and not self._auth_exempt(path):
            if not self._authed():
                return self._send_unauthorized()

        if method == "POST":
            if path == "/api/login":
                return self.post_login()
            if path == "/api/logout":
                return self.post_logout()
            return self.send_error(405, explain="このパスへのPOSTは対応していません")

        if method == "PUT":
            if path == "/api/collections":
                return self.put_collections(query)
            if path == "/api/progress":
                return self.put_progress()
            return self.send_error(405, explain="このパスへのPUTは対応していません")

        if path in ("/", "/player"):
            self.send_response(302)
            # deep link(/?view=stats 等)を殺さないよう、クエリをそのまま引き継ぐ。
            # raw_query は未デコードのままヘッダへ載せる(デコードすると
            # %0d%0a 等によるヘッダ注入が復活するため、ここでは絶対にしない)
            location = "/web/player.html"
            if raw_query:
                location += "?" + raw_query
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if path == "/api/libraries":
            return self.api_libraries(send_body)
        if path.startswith("/api/books/"):
            return self.api_book(path[len("/api/books/"):], query, send_body)
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
        if path == "/api/progress":
            return self.api_progress(send_body)
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

    def api_book(self, book_id: str, query: dict, send_body: bool):
        """ブック単体の書誌を返す(詳細画面用)。一覧と同じ形状の要素1件。"""
        lib = self._pick_library(query)
        if lib is None:
            return self.send_error(404, explain="ライブラリが見つかりません")
        # ブックIDはファイル配信と同じ封じ込め規則で検査する(traversal文字列の拒否)
        if not is_safe_segment(book_id):
            return self.send_error(400, explain="ブックIDが不正です")
        for b in lib.books():
            if b["id"] == book_id:
                return self.send_json({"kikimiru": 2, "library": lib.id, "book": b},
                                      send_body=send_body)
        return self.send_error(404, explain="ブックが見つかりません")

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

    def send_json(self, obj, send_body: bool = True, status: int = 200,
                  extra_headers: list = None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        if send_body:
            self.wfile.write(body)

    # --- 認証エンドポイント ------------------------------------------------

    def _read_json_body(self, max_bytes: int):
        """Content-Length検査つきでJSON本文を読む。不正なら None を返し応答済み。"""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self.send_error(400, explain="Content-Lengthが不正です")
            return None
        if length <= 0 or length > max_bytes:
            self.send_error(413, explain="本文の長さが不正です")
            return None
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.send_error(400, explain="JSONとして解釈できません")
            return None
        return body

    def post_login(self):
        if self.auth is None:
            return self.send_error(405, explain="認証は無効化されています")
        ip = self.client_address[0]
        wait = self.throttle.retry_after(ip)
        if wait > 0:
            return self.send_json({"kikimiru": 2, "error": "throttled", "retry_after": wait},
                                  status=429, extra_headers=[("Retry-After", str(wait))])
        body = self._read_json_body(MAX_LOGIN_BYTES)
        if body is None:
            return
        password = body.get("password") if isinstance(body, dict) else None
        if not isinstance(password, str) or not verify_password(self.auth, password):
            self.throttle.record_failure(ip)
            print(f"認証失敗: {ip}", file=sys.stderr)
            return self.send_json({"kikimiru": 2, "error": "bad_password"}, status=403)
        self.throttle.record_success(ip)
        token = self.sessions.issue()
        cookie = (f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; "
                  f"Max-Age={SESSION_TTL}")
        self.send_json({"kikimiru": 2, "ok": True},
                       extra_headers=[("Set-Cookie", cookie)])

    def post_logout(self):
        if self.sessions is not None:
            self.sessions.revoke(self._session_token())
        expired = f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"
        self.send_json({"kikimiru": 2, "ok": True},
                       extra_headers=[("Set-Cookie", expired)])

    # --- 進捗同期 ---------------------------------------------------------

    def api_progress(self, send_body: bool):
        store = self.progress_store
        progress = store.snapshot() if store is not None else {}
        self.send_json({"kikimiru": 2, "progress": progress}, send_body=send_body)

    def put_progress(self):
        if self.progress_store is None:
            return self.send_error(405, explain="進捗同期は無効化されています")
        body = self._read_json_body(MAX_PUT_BYTES)
        if body is None:
            return
        incoming = body.get("progress") if isinstance(body, dict) else None
        if not isinstance(incoming, dict):
            return self.send_error(400, explain="progress はオブジェクトである必要があります")
        if len(incoming) > MAX_PROGRESS_KEYS_PER_PUT:
            return self.send_error(413, explain="進捗キーが多すぎます")
        accepted = self.progress_store.merge(incoming)
        self.send_json({"kikimiru": 2, "accepted": accepted})

    # --- 静的配信 ---------------------------------------------------------

    def send_web(self, name: str, send_body: bool):
        entry = WEB_ALLOWLIST.get(name)
        if entry is None:
            return self.send_error(404)
        target, ctype = entry
        no_cache = name in WEB_NO_CACHE
        return self.send_file(target, ctype, send_body=send_body, no_cache=no_cache)

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

    def send_file(self, target: Path, ctype: str, send_body: bool,
                  no_cache: bool = False):
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
        if no_cache:
            self.send_header("Cache-Control", "no-cache")
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


def merge_allow_hosts(hosts: set, extra: list, port: int) -> set:
    """--allow-host の指定をallowlistへ合流する。

    ポートリマップ(-p 9000:8484)・リバースプロキシ・mDNS名など、bind IP と
    異なるホスト名でアクセスする場合に必要。ポート無し指定には既定ポート付きの
    形式も補完し、ポート付き指定には素のホスト名も補完する(ブラウザが既定
    ポートでHostにポートを付けないケースへの対処)。
    """
    out = set(hosts)
    for h in extra or []:
        h = h.strip().lower()
        if not h:
            continue
        out.add(h)
        if ":" in h:
            out.add(h.rsplit(":", 1)[0])
        else:
            out.add(f"{h}:{port}")
    return out


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
    ap = argparse.ArgumentParser(description="kikimiru self-host server (Phase 1後半)")
    ap.add_argument("--library", action="append", default=None, metavar="[名前=]パス",
                    help="ブックフォルダ群のディレクトリ。複数回指定できる"
                         "(例: --library 技術書=D:/books/tech)。既定: 同梱デモ")
    ap.add_argument("--bind", default="127.0.0.1",
                    help="bind先IP(既定: 127.0.0.1。LAN/VPNへ出す場合のみ明示)")
    ap.add_argument("--port", type=int, default=8484)
    ap.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR, metavar="DIR",
                    help=f"認証・セッション・進捗の保存先(既定: {DEFAULT_STATE_DIR})")
    ap.add_argument("--set-password", action="store_true",
                    help="パスワードを対話設定して終了する(初回に必須)")
    ap.add_argument("--allow-host", action="append", default=None, metavar="HOST[:PORT]",
                    help="Host/Origin検証で追加許可するホスト名。リバースプロキシや"
                         "ポートリマップ経由でアクセスする場合に指定(複数回可)")
    args = ap.parse_args()

    if args.set_password:
        sys.exit(set_password_interactive(args.state_dir))

    # 認証は必須。未設定なら手順を示して起動を拒否する
    auth = load_auth(args.state_dir)
    if auth is None:
        print("エラー: パスワードが設定されていません。先に次を実行してください:")
        print("  python server/kikimiru_server.py --set-password")
        print(f"(保存先: {args.state_dir / AUTH_FILE}。--state-dir で変更できます)")
        sys.exit(2)

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
            "警告: 通信は平文HTTPです。パスワードとセッションも盗聴され得るため、"
            f"信頼できないネットワーク(bind={args.bind})ではリバースプロキシ/VPNで"
            "TLSを終端してください(docs/DEPLOY.md 参照)。"
        )

    args.state_dir.mkdir(parents=True, exist_ok=True)
    KikimiruHandler.libraries = libraries
    KikimiruHandler.allowed_hosts = merge_allow_hosts(
        build_allowed_hosts(args.bind, args.port), args.allow_host, args.port)
    KikimiruHandler.auth = auth
    KikimiruHandler.sessions = SessionStore(args.state_dir)
    KikimiruHandler.throttle = LoginThrottle()
    KikimiruHandler.progress_store = ProgressStore(args.state_dir)

    # docker stop / systemd の SIGTERM で即時終了する(PID 1 での10秒SIGKILL待ち回避)。
    # Windows では SIGTERM の配送は限定的だが、ハンドラ登録自体は無害
    try:
        signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError):
        pass  # 非メインスレッド等で登録できない環境では諦める

    srv = KikimiruServer((args.bind, args.port), KikimiruHandler)
    print(f"kikimiru: http://{args.bind}:{args.port}/", flush=True)
    for lib in libraries.values():
        print(f"  ライブラリ {lib.id}: {lib.root}")
    print(f"  状態ディレクトリ: {args.state_dir}")
    try:
        srv.serve_forever()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
