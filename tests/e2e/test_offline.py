# -*- coding: utf-8 -*-
"""オフライン保存の中核回帰。

「真のオフライン」はサーバプロセス停止で再現する(set_offline はSWの通信を
遮断しないため、それ単体では検証にならない)。navigator.onLine を偽にする
必要がある検証(blobフォールバックの初期経路)だけ set_offline を併用する。
"""
import unittest

from playwright.sync_api import sync_playwright

import harness

BOOK = "demo-audio-lab"   # 画像スライド+表紙を持つ(一式保存の検証に最適)


class OfflineCoreTest(unittest.TestCase):
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

    def setUp(self):
        self.server.start()   # 前のテストが止めたままでも復帰させる
        self.ctx = harness.mobile_context(self.browser)
        self.page = self.ctx.new_page()
        harness.login(self.page, self.server.base)
        harness.wait_sw(self.page)

    def tearDown(self):
        self.server.start()
        try:
            harness.clear_downloads(self.page)
        except Exception:
            pass
        self.ctx.close()

    def test_save_range_synthesis_and_blob_playback(self):
        page = self.page
        harness.save_book(page, self.server.base, BOOK)
        n = page.evaluate(
            "async () => (await (await caches.open('kikimiru-book-demo/%s')).keys()).length" % BOOK)
        self.assertEqual(n, 9, "音声+deck+content+表紙+スライド5の一式")

        # 真のオフライン: 206合成しかあり得ない状態で検証する
        self.server.stop()
        rng = page.evaluate("""async () => {
          const u = '/books/demo/%s/audio.mp3';
          const out = {};
          let r = await fetch(u, { headers: { Range: 'bytes=100-199' } });
          out.mid = [r.status, r.headers.get('Content-Range'), (await r.blob()).size];
          r = await fetch(u, { headers: { Range: 'bytes=-50' } });
          out.sufSize = (await r.blob()).size;
          out.sufStatus = r.status;
          r = await fetch(u, { headers: { Range: 'bytes=999999999-' } });
          out.invalid = r.status;
          // バイト精度検証用の断片
          r = await fetch(u, { headers: { Range: 'bytes=1000-1015' } });
          out.frag = Array.from(new Uint8Array(await r.arrayBuffer()));
          return out;
        }""" % BOOK)
        self.assertEqual(rng["mid"][0], 206)
        self.assertEqual(rng["mid"][2], 100)
        self.assertRegex(rng["mid"][1], r"^bytes 100-199/\d+$")
        self.assertEqual(rng["sufStatus"], 206)
        self.assertEqual(rng["sufSize"], 50)
        self.assertEqual(rng["invalid"], 416)

        # blobフォールバック: 機内モード相当(onLine=false)で src が blob: になり再生できる
        self.ctx.set_offline(True)
        page.goto(self.server.base + "/web/player.html?book=" + BOOK,
                  wait_until="domcontentloaded")
        page.wait_for_function(
            "() => { const a = document.getElementById('audio'); return a && a.src; }",
            timeout=20000)
        self.assertTrue(page.evaluate(
            "() => document.getElementById('audio').src.startsWith('blob:')"))
        page.evaluate("() => document.getElementById('audio').play()")
        page.wait_for_timeout(1500)
        st = page.evaluate("""() => {
          const a = document.getElementById('audio');
          return { playing: !a.paused, t: a.currentTime, dur: a.duration || 0 };
        }""")
        self.assertTrue(st["playing"] and st["t"] > 0.3, st)
        self.assertGreater(st["dur"], 30)
        page.evaluate("() => { document.getElementById('audio').currentTime = 20; }")
        page.wait_for_timeout(1000)
        self.assertGreaterEqual(
            page.evaluate("() => document.getElementById('audio').currentTime"), 19.5)
        page.evaluate("() => document.getElementById('audio').pause()")

        # 合成断片がサーバ応答とバイト単位で一致する
        self.ctx.set_offline(False)
        self.server.start()
        frag_on = page.evaluate("""async () => {
          const r = await fetch('/books/demo/%s/audio.mp3',
                                { headers: { Range: 'bytes=1000-1015' } });
          return Array.from(new Uint8Array(await r.arrayBuffer()));
        }""" % BOOK)
        self.assertEqual(rng["frag"], frag_on)

    def test_unsaved_book_shows_clear_error_offline(self):
        page = self.page
        self.server.stop()
        page.goto(self.server.base + "/web/player.html?book=demo-guide-2",
                  wait_until="domcontentloaded")
        page.wait_for_timeout(4500)   # /api系のnetwork-firstタイムアウト(3秒)を跨ぐ
        warn = page.evaluate("() => document.getElementById('warn').textContent")
        self.assertIn("ブックを読み込めませんでした", warn)

    def test_delete_flow(self):
        page = self.page
        harness.save_book(page, self.server.base, BOOK)
        page.click("text=保存を削除")
        page.wait_for_selector("text=本当に削除?", timeout=5000)
        page.click("text=本当に削除?")
        page.wait_for_selector("text=オフライン保存", timeout=5000)
        self.assertFalse(page.evaluate(
            "async () => await caches.has('kikimiru-book-demo/%s')" % BOOK))


if __name__ == "__main__":
    unittest.main()
