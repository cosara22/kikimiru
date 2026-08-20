# -*- coding: utf-8 -*-
"""プレイヤーUIの基本回帰(タブ・章シーク・速度ポップアップ・マーキー・書き出し・狭幅)。"""
import json
import os
import tempfile
import unittest

from playwright.sync_api import sync_playwright

import harness


class PlayerBasicTest(unittest.TestCase):
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

    def open_page(self, **ctx_kw):
        self.ctx = harness.mobile_context(self.browser, **ctx_kw)
        page = self.ctx.new_page()
        harness.login(page, self.server.base)
        return page

    def tearDown(self):
        if getattr(self, "ctx", None):
            self.ctx.close()
            self.ctx = None

    def test_tabs_and_chapter_seek(self):
        page = self.open_page()
        page.goto(self.server.base + "/web/player.html?book=demo-audio-lab",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        # 既定は表紙(正方形ボックス)
        self.assertTrue(page.is_visible("#npCover"))
        # タブでスライド(16:9)へ
        page.click("#segSlide")
        page.wait_for_timeout(300)
        self.assertTrue(page.is_visible("#stagebox"))
        # チャプター3タップで cue(0:14)へシーク
        page.eval_on_selector("#npScroll", "el => el.scrollTo(0, el.scrollHeight)")
        page.wait_for_timeout(400)
        page.click(".np-chap:nth-child(3)")
        page.wait_for_timeout(800)
        t = page.evaluate("() => document.getElementById('audio').currentTime")
        self.assertTrue(13.5 <= t <= 16.5, "章タップのシーク位置: %s" % t)
        page.evaluate("() => document.getElementById('audio').pause()")

    def test_speed_popup(self):
        page = self.open_page()
        page.goto(self.server.base + "/web/player.html?book=demo-audio-lab",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        page.click("#actSpeed")
        page.wait_for_selector(".np-pop", timeout=5000)
        items = page.eval_on_selector_all(".np-pop-item", "els => els.map(e => e.textContent)")
        self.assertEqual(len(items), 5)
        page.click(".np-pop-item:nth-child(4)")   # 1.5×
        page.wait_for_timeout(300)
        self.assertEqual(page.evaluate("() => document.getElementById('audio').playbackRate"), 1.5)
        self.assertFalse(page.is_visible(".np-pop"))
        # 端末記憶を汚さないよう戻す
        page.click("#actSpeed")
        page.wait_for_selector(".np-pop", timeout=5000)
        page.click(".np-pop-item:nth-child(2)")   # 1.0×

    def test_marquee_only_for_long_title(self):
        page = self.open_page()
        page.goto(self.server.base + "/web/player.html?book=demo-audio-lab",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        self.assertTrue(page.evaluate(
            "() => document.getElementById('npTitleBox').classList.contains('marquee')"))
        page.goto(self.server.base + "/web/player.html?book=demo-book",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        self.assertFalse(page.evaluate(
            "() => document.getElementById('npTitleBox').classList.contains('marquee')"))

    def test_chapter_export_shape(self):
        page = self.open_page(accept_downloads=True)
        page.goto(self.server.base + "/web/player.html?view=book&book=demo-guide-1",
                  wait_until="networkidle")
        page.wait_for_timeout(600)
        with page.expect_download() as dl_info:
            page.click("text=チャプター書き出し")
        dl = dl_info.value
        self.assertEqual(dl.suggested_filename, "demo-guide-1.chapters.json")
        path = os.path.join(tempfile.mkdtemp(), "chapters.json")
        dl.save_as(path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["version"], "1.2.0")
        self.assertEqual(len(data["chapters"]), 7)
        for ch in data["chapters"]:
            self.assertEqual(set(ch.keys()), {"startTime", "title"})
        self.assertEqual(data["chapters"][0]["startTime"], 0)

    def test_transport_stays_circular_at_320px(self):
        self.ctx = self.browser.new_context(viewport={"width": 320, "height": 700},
                                            has_touch=True, is_mobile=True,
                                            color_scheme="dark")
        page = self.ctx.new_page()
        harness.login(page, self.server.base)
        page.goto(self.server.base + "/web/player.html?book=demo-audio-lab",
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        dims = page.evaluate("""() => {
          const r = id => { const b = document.getElementById(id).getBoundingClientRect();
            return [b.width, b.height]; };
          return { prev: r('prev'), play: r('playBtn') };
        }""")
        self.assertAlmostEqual(dims["prev"][0], dims["prev"][1], delta=0.6)
        self.assertAlmostEqual(dims["play"][0], dims["play"][1], delta=0.6)


if __name__ == "__main__":
    unittest.main()
