# -*- coding: utf-8 -*-
"""全画面と回転の回帰(モバイル・タッチ環境)。

回転による自動全画面はCSSのみ(body.np-fs)で成立する実装のため、ヘッドレスで
不安定なネイティブFullscreen APIではなく、bodyクラスとUI状態を検証する。
ダブルタップ判定(300ms窓)はCDP経由の実タップだと時間が揺れるため、
合成clickイベントの連続発火で決定論的に検証する。
"""
import unittest

from playwright.sync_api import sync_playwright

import harness

BOOK = "demo-audio-lab"


class FullscreenTest(unittest.TestCase):
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

    def open_player_slide(self):
        """没入プレイヤーを開いてスライド面へ(停止中はコントロール表示済み=⛶可視)。"""
        self.ctx = harness.mobile_context(self.browser)
        page = self.ctx.new_page()
        harness.login(page, self.server.base)
        page.goto(self.server.base + "/web/player.html?book=" + BOOK,
                  wait_until="networkidle")
        page.wait_for_timeout(800)
        page.click("#segSlide")
        page.wait_for_timeout(300)
        return page

    def tearDown(self):
        if getattr(self, "ctx", None):
            self.ctx.close()
            self.ctx = None

    def test_fs_button_toggles(self):
        page = self.open_player_slide()
        page.click("#fsBtn")
        page.wait_for_function(
            "() => document.body.classList.contains('np-fs')", timeout=5000)
        # 全画面中は⛶が「終了」表示に切り替わる
        self.assertEqual(
            page.evaluate("() => document.getElementById('fsBtn').getAttribute('aria-label')"),
            "全画面を終了")
        page.click("#fsBtn")
        page.wait_for_function(
            "() => !document.body.classList.contains('np-fs')", timeout=5000)

    def test_rotation_auto_fullscreen(self):
        page = self.open_player_slide()
        # 縦→横: 自動全画面(タッチ端末のみの挙動。CSSクラスで成立)
        page.set_viewport_size({"width": 844, "height": 390})
        page.wait_for_function(
            "() => document.body.classList.contains('np-fs')", timeout=5000)
        # 横→縦: 通常表示へ復帰
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_function(
            "() => !document.body.classList.contains('np-fs')", timeout=5000)

    def test_double_tap_skip(self):
        page = self.open_player_slide()
        page.click("#fsBtn")
        page.wait_for_function(
            "() => document.body.classList.contains('np-fs')", timeout=5000)
        page.evaluate("() => { document.getElementById('audio').currentTime = 20; }")
        page.wait_for_timeout(400)   # 直前のタップと二重判定されない間隔を置く
        # 直前の #fsBtn への実クリック(マウス扱い)が stagebox の pointerdown へ
        # バブリングし、以後の判定がマウス経路(面クリック=再生切替)になるため、
        # pointerType=touch の pointerdown を先に送ってタッチ経路を確定させる
        dbl = """(rel) => {
          const sb = document.getElementById('stagebox');
          sb.dispatchEvent(new PointerEvent('pointerdown',
            { pointerType: 'touch', bubbles: true }));
          const r = sb.getBoundingClientRect();
          const fire = () => sb.dispatchEvent(new MouseEvent('click', {
            clientX: r.left + r.width * rel, clientY: r.top + r.height / 2, bubbles: true }));
          fire(); fire();
          const f = sb.querySelector('.np-skipflash');
          return f ? f.textContent : null;
        }"""
        # 右側ダブルタップ = +10秒(スキップ表示は同期的に挿入されるため戻り値で検証)
        flash = page.evaluate(dbl, 0.85)
        self.assertEqual(flash, "+10秒")
        t = page.evaluate("() => document.getElementById('audio').currentTime")
        self.assertTrue(29.5 <= t <= 31, "+10秒後の位置: %s" % t)
        # 左側ダブルタップ = −10秒
        page.wait_for_timeout(400)
        flash = page.evaluate(dbl, 0.15)
        self.assertEqual(flash, "−10秒")
        t = page.evaluate("() => document.getElementById('audio').currentTime")
        self.assertTrue(19.5 <= t <= 21, "−10秒後の位置: %s" % t)
        # スキップはマウス経路(面クリック=再生切替)を通っていない
        self.assertTrue(page.evaluate("() => document.getElementById('audio').paused"))

    def test_single_tap_toggles_controls(self):
        page = self.open_player_slide()
        page.click("#fsBtn")
        page.wait_for_function(
            "() => document.body.classList.contains('np-fs')", timeout=5000)
        # 入場直後はコントロール表示(停止中は自動退場しない)
        self.assertTrue(page.evaluate(
            "() => document.getElementById('stagebox').classList.contains('ctl')"))
        page.wait_for_timeout(400)
        # 中央シングルタップ: 260msの確定待ちの後に閉じる。
        # pointerType=touch を先に送りタッチ経路を確定させる(マウス経路だと再生切替になる)
        tap = ("() => { const sb = document.getElementById('stagebox');"
               " sb.dispatchEvent(new PointerEvent('pointerdown',"
               " { pointerType: 'touch', bubbles: true }));"
               " const r = sb.getBoundingClientRect();"
               " sb.dispatchEvent(new MouseEvent('click', { clientX: r.left + r.width / 2,"
               " clientY: r.top + r.height / 2, bubbles: true })); }")
        page.evaluate(tap)
        page.wait_for_function(
            "() => !document.getElementById('stagebox').classList.contains('ctl')",
            timeout=3000)
        # もう一度タップで再表示
        page.wait_for_timeout(400)
        page.evaluate(tap)
        page.wait_for_function(
            "() => document.getElementById('stagebox').classList.contains('ctl')",
            timeout=3000)
        # タップは再生切替(マウス経路)として扱われていない
        self.assertTrue(page.evaluate("() => document.getElementById('audio').paused"))


if __name__ == "__main__":
    unittest.main()
