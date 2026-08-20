# -*- coding: utf-8 -*-
"""デスクトップ(マウス・広幅)の回帰: シアター2カラム・ポップアップ・キーボード操作。"""
import unittest

from playwright.sync_api import sync_playwright

import harness

BOOK = "demo-audio-lab"


class DesktopTest(unittest.TestCase):
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

    def open_player(self):
        self.ctx = harness.desktop_context(self.browser)
        page = self.ctx.new_page()
        harness.login(page, self.server.base)
        page.goto(self.server.base + "/web/player.html?book=" + BOOK,
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        return page

    def tearDown(self):
        if getattr(self, "ctx", None):
            self.ctx.close()
            self.ctx = None

    def test_theater_two_columns(self):
        page = self.open_player()
        st = page.evaluate("""() => {
          const scroll = document.querySelector('.np-scroll');
          const media = document.querySelector('.np-media').getBoundingClientRect();
          const chap = document.querySelector('.np-chapters').getBoundingClientRect();
          return { display: getComputedStyle(scroll).display,
                   mediaRight: media.right, mediaWidth: media.width,
                   chapLeft: chap.left, chapWidth: chap.width };
        }""")
        self.assertEqual(st["display"], "grid", st)
        # チャプターはメディアの右カラムに独立して並ぶ
        self.assertGreater(st["chapLeft"], st["mediaRight"] - 1, st)
        self.assertGreaterEqual(st["chapWidth"], 300, st)
        # メディア面がシアター相当の広さを持つ
        self.assertGreater(st["mediaWidth"], 500, st)

    def test_popup_opens_near_button_and_closes_outside(self):
        page = self.open_player()
        page.click("#actSpeed")
        page.wait_for_selector(".np-pop", timeout=5000)
        prox = page.evaluate("""() => {
          const b = document.getElementById('actSpeed').getBoundingClientRect();
          const p = document.querySelector('.np-pop').getBoundingClientRect();
          const dx = Math.abs((p.left + p.width / 2) - (b.left + b.width / 2));
          const dy = Math.min(Math.abs(p.bottom - b.top), Math.abs(p.top - b.bottom));
          return { dx: dx, dy: dy };
        }""")
        # ボタン近傍(直上/直下・水平ほぼ同位置)に出る
        self.assertLess(prox["dy"], 60, prox)
        self.assertLess(prox["dx"], 240, prox)
        # 外側クリックで閉じる
        page.mouse.click(30, 400)
        page.wait_for_timeout(300)
        self.assertFalse(page.is_visible(".np-pop"))

    def test_keyboard_shortcuts_survive_mouse_click(self):
        page = self.open_player()
        # ボタンをマウスクリックした後(フォーカスが残るとSpaceがボタンを再発火する)
        page.click("#actSpeed")
        page.wait_for_selector(".np-pop", timeout=5000)
        page.mouse.click(30, 400)   # ポップアップを閉じる
        page.wait_for_timeout(300)
        # Space = 再生
        page.keyboard.press(" ")
        page.wait_for_function(
            "() => { const a = document.getElementById('audio');"
            " return !a.paused && a.currentTime > 0; }",
            timeout=8000)
        # Space = 停止
        page.keyboard.press(" ")
        page.wait_for_function(
            "() => document.getElementById('audio').paused", timeout=5000)
        # F = 全画面、Escape = 復帰
        page.keyboard.press("f")
        page.wait_for_function(
            "() => document.body.classList.contains('np-fs')", timeout=5000)
        page.keyboard.press("Escape")
        page.wait_for_function(
            "() => !document.body.classList.contains('np-fs')", timeout=5000)


if __name__ == "__main__":
    unittest.main()
