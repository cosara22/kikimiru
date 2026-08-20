# -*- coding: utf-8 -*-
"""E2Eテスト共通ハーネス。

- 一時state-dir(既知パスワードのauth.json)を作り、空きポートでデモサーバを起動する
- 「真のオフライン」は**サーバプロセスの停止**で再現する。Playwrightの set_offline は
  (1) Service Workerの通信を遮断しない、(2) Linux headless では navigator.onLine すら
  false にならない(CI実測: Windows では効き、Linux では効かない)。どちらの用途にも
  使えないため使用禁止。onLine を偽にしたい検証は force_offline() を使う
- サーバの標準出力はファイルへ逃がす。パイプのまま放置するとリクエストログで
  バッファが満杯になり、サーバが write でブロックして応答しなくなる(実測)

実行方法(要: pip install playwright / playwright install chromium):
    python -m unittest discover -s tests/e2e -p "test_*.py" -v
"""
import hashlib
import json
import os
import re
import secrets
import shutil
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
        self.app_root = ROOT   # restart_from() で複製アプリへ差し替えられる
        self.start()

    def start(self):
        if self.proc:
            return
        for attempt in (1, 2):
            self.proc = subprocess.Popen(
                [sys.executable, os.path.join(self.app_root, "server", "kikimiru_server.py"),
                 "--port", str(self.port), "--state-dir", self.state_dir],
                stdout=self.log, stderr=subprocess.STDOUT, cwd=self.app_root)
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

    def restart_from(self, app_root):
        """別のアプリルート(server/+web/+demo/ の複製)から同一ポート・同一state-dirで
        起動し直す。同一オリジンのままシェル(sw.js等)だけ差し替わるため、
        Service Worker更新経路の検証に使う。"""
        self.stop()
        self.app_root = app_root
        self.start()

    def close(self):
        self.stop()
        try:
            self.log.close()
        except Exception:
            pass


def make_app_copy(version_suffix):
    """server/+web/+demo/ を一時ディレクトリへ複製し、複製側 sw.js の CACHE_VERSION に
    接尾辞を付けて「シェル更新後のアプリ」を作る。(複製ルート, 新版数) を返す。"""
    dst = tempfile.mkdtemp(prefix="kikimiru-e2e-app-")
    for d in ("server", "web", "demo"):
        shutil.copytree(os.path.join(ROOT, d), os.path.join(dst, d))
    swp = os.path.join(dst, "web", "sw.js")
    with open(swp, encoding="utf-8") as f:
        src = f.read()
    m = re.search(r'const CACHE_VERSION = "([^"]+)"', src)
    if not m:
        raise RuntimeError("sw.js の CACHE_VERSION が見つかりません")
    new_ver = m.group(1) + version_suffix
    src = src.replace(m.group(0), 'const CACHE_VERSION = "%s"' % new_ver, 1)
    with open(swp, "w", encoding="utf-8") as f:
        f.write(src)
    return dst, new_ver


# navigator.onLine の決定論的な模擬。localStorageフラグ方式にすることで、
# ページ遷移後の新しい文書でも(初期化スクリプトが毎回走るため)状態が引き継がれる
_FORCE_OFFLINE_INIT = """(() => {
  const orig = Object.getOwnPropertyDescriptor(Navigator.prototype, 'onLine').get;
  Object.defineProperty(navigator, 'onLine', {
    configurable: true,
    get: () => {
      try { if (localStorage.getItem('e2e.forceOffline') === '1') return false; } catch (e) {}
      return orig.call(navigator);
    },
  });
})();"""


def mobile_context(browser, **kw):
    ctx = browser.new_context(viewport={"width": 390, "height": 844},
                              has_touch=True, is_mobile=True,
                              color_scheme="dark", **kw)
    ctx.add_init_script(_FORCE_OFFLINE_INIT)
    return ctx


def desktop_context(browser, **kw):
    """マウス+広幅(シアターレイアウト・キーボード操作の検証用)。"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900},
                              color_scheme="dark", **kw)
    ctx.add_init_script(_FORCE_OFFLINE_INIT)
    return ctx


def force_offline(page, value):
    """navigator.onLine を決定論的に切り替え、offline/online イベントを発火する。

    set_offline は Linux headless で onLine に反映されない(CI実測)ため、
    アプリが onLine を入力条件とする挙動(バッジ・入口ガード・blobフォールバック)は
    この模擬で検証する。実際の到達不能は Server.stop() が担う。
    """
    page.evaluate("""v => {
      if (v) localStorage.setItem('e2e.forceOffline', '1');
      else localStorage.removeItem('e2e.forceOffline');
      window.dispatchEvent(new Event(v ? 'offline' : 'online'));
    }""", value)


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
          shellCacheEntries: await caches.open('kikimiru-v5')
            .then(c => c.keys())
            .then(ks => ks.map(r => r.url.replace(location.origin, ''))),
          libKey: localStorage.getItem('kikimiru.lib'),
          probeMatch: !!(await caches.match('/api/books?library=demo')),
          probeFetch: await fetch('/api/books?library=demo')
            .then(r => r.status + ' cache=' + (r.headers.get('X-Kikimiru-Cache') || '0'))
            .catch(e => 'reject: ' + e),
          bodyHead: document.body.textContent.replace(/\\s+/g, ' ').slice(0, 300),
        })""")
    except Exception as e:
        st = "evaluate失敗: %r" % (e,)
    print("\n==== 診断: %s ====\n%s" % (label, json.dumps(st, ensure_ascii=False, indent=1)
                                        if isinstance(st, dict) else st), flush=True)
    if console:
        print("---- コンソール末尾 ----\n%s\n" % "\n".join(console[-30:]), flush=True)


def wait_api_cached(page, lib="demo", timeout=15.0):
    """オフライン遷移の前提となるAPIスナップショットがSWキャッシュに載るまで待つ。

    SWの cache.put は応答返却後の非同期処理のため、応答直後にサーバを止めると
    スナップショットが欠けたままになり、オフライン画面(起動の /api/libraries、
    詳細フォールバックの /api/books?library=…)が組み立てられない(実測フレーク)。

    注意: 照合はキャッシュキーと完全一致のクエリ付きURLで行う(api() は常に
    library= を付ける)。また wait_for_function にasync関数を渡すとPromise自体が
    truthy扱いされ即座に通過してしまうため、Promiseを正しくawaitする
    page.evaluate をPython側でポーリングする。
    """
    deadline = time.time() + timeout
    while True:
        ok = page.evaluate(
            "async (lib) => !!(await caches.match('/api/libraries'))"
            " && !!(await caches.match('/api/books?library=' + lib))", lib)
        if ok:
            return
        if time.time() > deadline:
            raise RuntimeError("APIスナップショットがキャッシュに載りませんでした")
        page.wait_for_timeout(200)


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
