# -*- coding: utf-8 -*-
"""Service Worker更新経路の回帰。

「シェル(sw.js)の版数が上がっても、ブック単位オフライン保存(kikimiru-book-*)は
生存し、旧世代のシェルキャッシュだけが削除される」というオフライン保存の中核
不変条件を検証する。版数を上げた複製アプリを同一ポートで差し替え起動し、
実際のSW更新フロー(install→activate→claim→1回自動リロード)を通す。
"""
import shutil
import time
import unittest

from playwright.sync_api import sync_playwright

import harness

BOOK = "demo-audio-lab"


class PwaUpdateTest(unittest.TestCase):
    app2 = None

    @classmethod
    def setUpClass(cls):
        cls.server = harness.Server()
        cls.play = sync_playwright().start()
        cls.browser = cls.play.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.play.stop()
        cls.server.close()
        if cls.app2:
            shutil.rmtree(cls.app2, ignore_errors=True)

    def setUp(self):
        self.server.start()
        self.ctx = harness.mobile_context(self.browser)
        self.page = self.ctx.new_page()
        self.console = harness.attach_console(self.page)
        harness.login(self.page, self.server.base)
        harness.wait_sw(self.page)

    def tearDown(self):
        # 次のテストのために元のアプリへ戻す
        self.server.restart_from(harness.ROOT)
        try:
            harness.clear_downloads(self.page)
        except Exception:
            pass
        self.ctx.close()

    def test_shell_update_preserves_downloads(self):
        page = self.page
        harness.save_book(page, self.server.base, BOOK)
        harness.wait_api_cached(page)
        vers = page.evaluate(
            "async () => (await caches.keys()).filter(k => !k.startsWith('kikimiru-book-'))")
        self.assertEqual(len(vers), 1, vers)
        old_ver = vers[0]

        # 版数を上げた複製アプリへ、同一ポート(=同一オリジン)で差し替える
        app2, new_ver = harness.make_app_copy("-e2enext")
        type(self).app2 = app2
        self.server.restart_from(app2)

        # 再訪問で更新チェック(sw.jsはno-cache配信)。claim時の自動リロードを跨いで
        # 新世代の成立をポーリングで待つ(リロード中は実行コンテキストが破棄される)
        try:
            page.goto(self.server.base + "/web/player.html?view=home",
                      wait_until="networkidle")
            page.evaluate("() => navigator.serviceWorker.getRegistration('/web/')"
                          ".then(r => r && r.update())")
        except Exception:
            pass
        deadline = time.time() + 30
        state = None
        while time.time() < deadline:
            try:
                state = page.evaluate("async () => await caches.keys()")
                if new_ver in state and old_ver not in state:
                    break
            except Exception:
                pass
            try:
                page.wait_for_timeout(400)
            except Exception:
                time.sleep(0.4)
        self.assertIsNotNone(state, "キャッシュ状態を取得できませんでした")
        self.assertIn(new_ver, state, state)
        self.assertNotIn(old_ver, state, state)

        # 中核不変条件: 保存済みブックはシェル更新後も生存する
        self.assertTrue(page.evaluate(
            "async () => await caches.has('kikimiru-book-demo/%s')" % BOOK), state)

        # 更新後のSWでも、ネットワーク不達時の206合成が生きている
        self.server.stop()
        rng = page.evaluate("""async () => {
          const r = await fetch('/books/demo/%s/audio.mp3',
                                { headers: { Range: 'bytes=100-199' } });
          return [r.status, (await r.blob()).size];
        }""" % BOOK)
        self.assertEqual(rng[0], 206, rng)
        self.assertEqual(rng[1], 100, rng)


if __name__ == "__main__":
    unittest.main()
