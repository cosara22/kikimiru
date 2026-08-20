# -*- coding: utf-8 -*-
"""E2Eテスト共通ハーネス。

- 一時state-dir(既知パスワードのauth.json)を作り、空きポートでデモサーバを起動する
- 「真のオフライン」は**サーバプロセスの停止**で再現する。Playwrightの set_offline は
  Service Workerの通信を遮断しないため、それ単体ではオフライン再生の検証にならない
  (実測で確認済みの落とし穴)。navigator.onLine を偽にしたい検証だけ set_offline を併用する
- サーバの標準出力はファイルへ逃がす。パイプのまま放置するとリクエストログで
  バッファが満杯になり、サーバが write でブロックして応答しなくなる(実測)

実行方法(要: pip install playwright / playwright install chromium):
    python -m unittest discover -s tests/e2e -p "test_*.py" -v
"""
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PASSWORD = "e2e-password-0000"


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Server:
    """デモライブラリを配信するテスト用サーバ。stop()/start() で真のオフラインを再現する。"""

    def __init__(self):
        self.state_dir = tempfile.mkdtemp(prefix="kikimiru-e2e-")
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", PASSWORD.encode("utf-8"), salt, 600_000)
        with open(os.path.join(self.state_dir, "auth.json"), "w", encoding="utf-8") as f:
            json.dump({"v": 1, "algo": "pbkdf2_sha256", "iterations": 600_000,
                       "salt_hex": salt.hex(), "hash_hex": digest.hex()}, f)
        self.port = free_port()
        self.base = "http://127.0.0.1:%d" % self.port
        self.log = open(os.path.join(self.state_dir, "server.log"), "ab")
        self.proc = None
        self.start()

    def start(self):
        if self.proc:
            return
        for attempt in (1, 2):
            self.proc = subprocess.Popen(
                [sys.executable, os.path.join(ROOT, "server", "kikimiru_server.py"),
                 "--port", str(self.port), "--state-dir", self.state_dir],
                stdout=self.log, stderr=subprocess.STDOUT, cwd=ROOT)
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    with urllib.request.urlopen(self.base + "/web/player.html",
                                                timeout=1) as r:
                        if r.status == 200:
                            return
                except Exception:
                    time.sleep(0.2)
            # 立ち上がらなかった: 一度だけ作り直す(ポートのTIME_WAIT等の一過性対策)
            self.stop()
        raise RuntimeError("サーバが起動しませんでした: %s\n---- server.log 末尾 ----\n%s"
                           % (self.base, self._tail_log()))

    def _tail_log(self):
        try:
            self.log.flush()
            with open(self.log.name, "rb") as f:
                return f.read()[-2000:].decode("utf-8", "replace")
        except Exception:
            return "(ログを読めませんでした)"

    def stop(self):
        """真のオフラインを作る(接続拒否になる)。"""
        if not self.proc:
            return
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        self.proc = None
        time.sleep(0.3)

    def close(self):
        self.stop()
        try:
            self.log.close()
        except Exception:
            pass


def mobile_context(browser, **kw):
    return browser.new_context(viewport={"width": 390, "height": 844},
                               has_touch=True, is_mobile=True,
                               color_scheme="dark", **kw)


def login(page, base):
    """ログインして本棚UIが出るまで待つ。

    アプリはSWの初回claim時に1度だけ自動リロードする。遅い環境では
    「パスワード入力→リロードで消える→空送信」の競合が起きるため、
    先にSWの制御確立(=リロード)を待ってから入力し、成功をUIで検証して
    ダメなら再試行する。
    """
    page.goto(base + "/web/player.html", wait_until="domcontentloaded")
    try:
        page.wait_for_function("() => !!navigator.serviceWorker.controller",
                               timeout=20000)
    except Exception:
        pass  # SWが使えない環境でもログイン自体は成立する
    page.wait_for_timeout(600)   # claim直後の自動リロードを跨ぐ
    for _ in range(3):
        try:
            page.wait_for_selector("#login input", timeout=4000)
        except Exception:
            return  # ログイン画面が無い=既にセッションあり
        page.fill("#login input", PASSWORD)
        page.click("#login button")
        try:
            # 成功すると location.reload() され、本棚UI(タブバー)が出る
            page.wait_for_selector("#tabbar a", timeout=8000)
            return
        except Exception:
            page.wait_for_timeout(500)   # リロード競合等はやり直す
    raise RuntimeError("ログインできませんでした")


def wait_sw(page):
    """SWが制御権を持つまで待つ(初回はcontrollerchangeで1回自動リロードされる)。"""
    page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=20000)


def attach_console(page):
    """コンソール出力とページ内例外の収集を開始し、収集先リストを返す(失敗時診断用)。"""
    logs = []
    page.on("console", lambda m: logs.append("[%s] %s" % (m.type, m.text)))
    page.on("pageerror", lambda e: logs.append("[pageerror] %s" % e))
    return logs


def dump_state(page, label, console=None):
    """タイムアウト時の原因切り分け用に、ページ状態を標準出力へ書き出す。"""
    try:
        st = page.evaluate("""async () => ({
          url: location.href,
          ua: navigator.userAgent,
          onLine: navigator.onLine,
          swControlled: !!navigator.serviceWorker.controller,
          warn: (document.getElementById('warn') || {}).textContent || '',
          netBadge: (function () {
            var b = document.getElementById('netBadge');
            return b ? getComputedStyle(b).display : '(要素なし)';
          })(),
          audioSrc: ((document.getElementById('audio') || {}).src || '').slice(0, 80),
          cacheKeys: await caches.keys(),
          bodyHead: document.body.textContent.replace(/\\s+/g, ' ').slice(0, 300),
        })""")
    except Exception as e:
        st = "evaluate失敗: %r" % (e,)
    print("\n==== 診断: %s ====\n%s" % (label, json.dumps(st, ensure_ascii=False, indent=1)
                                        if isinstance(st, dict) else st), flush=True)
    if console:
        print("---- コンソール末尾 ----\n%s\n" % "\n".join(console[-30:]), flush=True)


def wait_api_cached(page):
    """オフライン遷移の前提となるAPIスナップショットがSWキャッシュに載るまで待つ。

    SWの cache.put は応答返却後の非同期処理のため、応答直後にサーバを止めると
    スナップショットが欠けたままになり、オフライン画面(起動の /api/libraries、
    詳細フォールバックの /api/books)が組み立てられないことがある(実測フレーク)。
    """
    page.wait_for_function(
        "async () => !!(await caches.match('/api/libraries'))"
        " && !!(await caches.match('/api/books'))",
        timeout=15000)


def save_book(page, base, book_id):
    """ブック詳細から「オフライン保存」して完了(保存を削除の表示)まで待つ。"""
    page.goto(base + "/web/player.html?view=book&book=" + book_id,
              wait_until="networkidle")
    page.wait_for_timeout(600)
    page.click("text=オフライン保存")
    page.wait_for_selector("text=保存を削除", timeout=30000)


def clear_downloads(page):
    """テスト間の独立性のため、保存キャッシュと記録を全消しする。"""
    page.evaluate("""async () => {
      for (const k of await caches.keys()) {
        if (k.startsWith('kikimiru-book-')) await caches.delete(k);
      }
      for (let i = localStorage.length - 1; i >= 0; i--) {
        const key = localStorage.key(i);
        if (key && key.indexOf('kikimiru.dl.') === 0) localStorage.removeItem(key);
      }
    }""")
