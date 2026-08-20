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
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "kikimiru_server.py"),
             "--port", str(self.port), "--state-dir", self.state_dir],
            stdout=self.log, stderr=subprocess.STDOUT, cwd=ROOT)
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/web/player.html", timeout=1) as r:
                    if r.status == 200:
                        return
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("サーバが起動しませんでした: " + self.base)

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
    page.goto(base + "/web/player.html", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("#login input", timeout=5000)
    except Exception:
        return  # 既にセッションあり
    page.fill("#login input", PASSWORD)
    page.click("#login button")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(600)


def wait_sw(page):
    """SWが制御権を持つまで待つ(初回はcontrollerchangeで1回自動リロードされる)。"""
    page.wait_for_function("() => !!navigator.serviceWorker.controller", timeout=20000)


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
