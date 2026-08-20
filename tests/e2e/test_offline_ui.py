# -*- coding: utf-8 -*-
"""オフライン保存のUI回帰(管理画面・消去検知・DLキュー・一括保存・オフラインガード)。"""
import time
import unittest

from playwright.sync_api import sync_playwright

import harness

BOOK = "demo-audio-lab"


class OfflineUiTest(unittest.TestCase):
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
        self.server.start()
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

    def test_management_view_and_eviction_notice(self):
        page = self.page
        harness.save_book(page, self.server.base, BOOK)
        # 幽霊レコード(記録だけ残ってキャッシュが無い=システムに消された保存)
        page.evaluate("""() => localStorage.setItem('kikimiru.dl.demo/ghost',
          JSON.stringify({bytes: 12345, at: 1}))""")
        page.goto(self.server.base + "/web/player.html?view=offline",
                  wait_until="networkidle")
        page.wait_for_timeout(1200)
        st = page.evaluate("""() => ({
          gauge: document.querySelector('.off-gauge .row1').textContent,
          rows: document.querySelectorAll('.group .grow').length,
          evictWarn: document.getElementById('warn').textContent.includes('システムにより削除'),
          ghostGone: !localStorage.getItem('kikimiru.dl.demo/ghost'),
          persistRow: [...document.querySelectorAll('.off-gauge .row1')]
            .some(r => r.textContent.includes('保護状態')),
          note: !!document.querySelector('.stats-note'),
        })""")
        self.assertIn("保存済み 1冊", st["gauge"])
        self.assertEqual(st["rows"], 1)
        self.assertTrue(st["evictWarn"], "消去検知の通知")
        self.assertTrue(st["ghostGone"], "幽霊レコードの掃除")
        self.assertTrue(st["persistRow"], "永続化の状態表示")
        self.assertTrue(st["note"])

    def test_queue_survives_navigation_and_bulk_save(self):
        page = self.page
        # A の保存を開始して即座に B へ遷移しても、両方完了する(直列キュー)
        page.goto(self.server.base + "/web/player.html?view=book&book=demo-audio-lab",
                  wait_until="networkidle")
        page.wait_for_timeout(500)
        page.click("text=オフライン保存")
        page.goto(self.server.base + "/web/player.html?view=book&book=demo-guide-1",
                  wait_until="networkidle")
        page.wait_for_timeout(300)
        page.click("text=オフライン保存")
        page.wait_for_selector("text=保存を削除", timeout=30000)
        both = page.evaluate("""async () => ({
          a: await caches.has('kikimiru-book-demo/demo-audio-lab'),
          b: await caches.has('kikimiru-book-demo/demo-guide-1'),
        })""")
        self.assertTrue(both["a"] and both["b"], both)
        harness.clear_downloads(page)

        # コレクション一括保存
        page.goto(self.server.base + "/web/player.html?view=collections",
                  wait_until="networkidle")
        page.wait_for_timeout(600)
        page.fill(".newcol input", "e2e一括")
        page.click("text=作成")
        page.wait_for_timeout(800)
        page.click("text=ブックを追加/編集")
        page.wait_for_timeout(400)
        page.evaluate("""() => {
          const boxes = [...document.querySelectorAll('.picker input[type=checkbox]')];
          boxes[0].checked = true; boxes[1].checked = true;
        }""")
        page.click(".picker >> text=保存")
        page.wait_for_timeout(1000)
        page.click("text=一括保存")
        page.wait_for_timeout(500)
        fb = page.evaluate("""() => {
          const b = [...document.querySelectorAll('button')]
            .find(x => x.textContent.includes('キューに追加'));
          return b ? b.textContent : null;
        }""")
        self.assertEqual(fb, "2冊をキューに追加")
        deadline = time.time() + 30
        n = 0
        while time.time() < deadline:
            n = page.evaluate(
                "async () => (await caches.keys()).filter(k => k.startsWith('kikimiru-book-')).length")
            if n >= 2:
                break
            time.sleep(0.5)
        self.assertEqual(n, 2, "一括保存の完了冊数")
        # 後片付け(コレクション削除)
        page.click("text=削除")
        page.wait_for_timeout(200)
        page.click("text=本当に削除?")
        page.wait_for_timeout(500)

    def test_offline_badge_and_unsaved_guard(self):
        page = self.page
        harness.save_book(page, self.server.base, BOOK)
        # オンラインのうちに書棚スナップショット(/api/books)を作っておく。
        # オフライン時の詳細画面はこのスナップショットから組み立てられる
        page.goto(self.server.base + "/web/player.html?view=home",
                  wait_until="networkidle")
        harness.wait_api_cached(page)   # cache.put完了前に止めると欠ける(非同期put対策)
        self.server.stop()
        self.ctx.set_offline(True)   # navigator.onLine=false(バッジ・ガードの条件)
        page.goto(self.server.base + "/web/player.html?view=home",
                  wait_until="domcontentloaded")
        # バッジ表示は時間ではなく状態として待つ(遅い環境でのフレーク対策)
        page.wait_for_function(
            "() => { const b = document.getElementById('netBadge');"
            " return b && getComputedStyle(b).display !== 'none'; }",
            timeout=20000)
        # 未保存ブック: 入口ガードでオーバーレイを開かず理由を表示する
        page.goto(self.server.base + "/web/player.html?view=book&book=demo-guide-2",
                  wait_until="domcontentloaded")
        page.wait_for_selector(".cta", timeout=20000)
        page.click(".cta")
        page.wait_for_function(
            "() => document.getElementById('warn').textContent"
            ".includes('保存されていないため再生できません')",
            timeout=10000)
        self.assertFalse(page.evaluate(
            "() => document.body.classList.contains('player-open')"))
        self.ctx.set_offline(False)


if __name__ == "__main__":
    unittest.main()
