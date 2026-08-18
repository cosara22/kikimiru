# -*- coding: utf-8 -*-
"""認証・進捗同期まわりの部品のユニットテスト(サーバを起動せず純関数・クラス単位で検査)。"""
import hashlib
import json
import secrets
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import kikimiru_server as ks  # noqa: E402


def make_auth(password: str, iterations: int = 1000) -> dict:
    """テスト用のauth辞書(反復回数を落として高速化。形式は本番と同一)。"""
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"v": 1, "algo": "pbkdf2_sha256", "iterations": iterations,
            "salt_hex": salt.hex(), "hash_hex": digest.hex()}


class TestPassword(unittest.TestCase):
    def test_roundtrip(self):
        auth = make_auth("correct horse battery staple")
        self.assertTrue(ks.verify_password(auth, "correct horse battery staple"))
        self.assertFalse(ks.verify_password(auth, "wrong"))
        self.assertFalse(ks.verify_password(auth, ""))


class TestSessionStore(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = ks.SessionStore(self.tmp)

    def test_issue_check_revoke(self):
        token = self.store.issue()
        self.assertTrue(self.store.check(token))
        self.assertFalse(self.store.check("bogus-token"))
        self.assertFalse(self.store.check(""))
        self.store.revoke(token)
        self.assertFalse(self.store.check(token))

    def test_expired_session_is_rejected(self):
        token = self.store.issue()
        key = ks.SessionStore._key(token)
        self.store.sessions[key] = time.time() - 1  # 期限切れに書き換え
        self.assertFalse(self.store.check(token))

    def test_persistence_roundtrip(self):
        token = self.store.issue()
        # 別インスタンス(=サーバ再起動)でもログインが維持される
        store2 = ks.SessionStore(self.tmp)
        self.assertTrue(store2.check(token))


class TestLoginThrottle(unittest.TestCase):
    def test_lock_after_threshold(self):
        th = ks.LoginThrottle()
        ip = "192.0.2.1"
        for _ in range(th.THRESHOLD - 1):
            th.record_failure(ip)
        self.assertEqual(th.retry_after(ip), 0)  # 閾値未満は受け付ける
        th.record_failure(ip)
        self.assertGreater(th.retry_after(ip), 0)  # 閾値でロック

    def test_success_clears(self):
        th = ks.LoginThrottle()
        ip = "192.0.2.2"
        for _ in range(th.THRESHOLD):
            th.record_failure(ip)
        self.assertGreater(th.retry_after(ip), 0)
        th.record_success(ip)
        self.assertEqual(th.retry_after(ip), 0)

    def test_ips_are_independent(self):
        th = ks.LoginThrottle()
        for _ in range(th.THRESHOLD):
            th.record_failure("192.0.2.3")
        self.assertEqual(th.retry_after("192.0.2.4"), 0)


class TestProgressMerge(unittest.TestCase):
    def test_valid_key(self):
        self.assertTrue(ks.valid_progress_key("demo/demo-book"))
        self.assertTrue(ks.valid_progress_key("技術書/ドメイン駆動設計"))
        self.assertFalse(ks.valid_progress_key("demo"))
        self.assertFalse(ks.valid_progress_key("demo/a/b"))
        self.assertFalse(ks.valid_progress_key("../etc/passwd"))
        self.assertFalse(ks.valid_progress_key("demo/.."))
        self.assertFalse(ks.valid_progress_key(""))

    def test_lww(self):
        store = {"demo/a": {"t": 10.0, "d": 60.0, "at": 1000.0}}
        n = ks.merge_progress(store, {
            "demo/a": {"t": 20, "d": 60, "at": 2000},      # 新しい→採用
            "demo/b": {"t": 5, "d": 30, "at": 500, "s": 2, "n": 6},  # 新規→採用
        })
        self.assertEqual(n, 2)
        self.assertEqual(store["demo/a"]["t"], 20.0)
        self.assertEqual(store["demo/b"]["s"], 2)
        # 古い値は棄却される
        n = ks.merge_progress(store, {"demo/a": {"t": 1, "d": 60, "at": 1500}})
        self.assertEqual(n, 0)
        self.assertEqual(store["demo/a"]["t"], 20.0)

    def test_rejects_invalid_records(self):
        store = {}
        n = ks.merge_progress(store, {
            "demo/x": {"t": "abc", "d": 1, "at": 1},   # 型不正
            "demo/y": {"t": -1, "d": 1, "at": 1},      # 負数
            "demo/z": "not-a-dict",
            "bad key with / too / many": {"t": 1, "d": 1, "at": 1},
        })
        self.assertEqual(n, 0)
        self.assertEqual(store, {})


class TestWriteJsonAtomic(unittest.TestCase):
    def test_concurrent_writes_leave_valid_json(self):
        tmp = Path(tempfile.mkdtemp()) / "out.json"
        errors = []

        def writer(i):
            try:
                ks.write_json_atomic(tmp, {"n": i, "data": ["x"] * 50})
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        data = json.loads(tmp.read_text(encoding="utf-8"))
        self.assertIn("n", data)  # どれかの書き込みが完全な形で残っている
        # 一時ファイルの残骸が無いこと
        leftovers = list(tmp.parent.glob("*.tmp"))
        self.assertEqual(leftovers, [])


class TestAllowHosts(unittest.TestCase):
    def test_merge_variants(self):
        base = ks.build_allowed_hosts("127.0.0.1", 8484)
        merged = ks.merge_allow_hosts(base, ["nas.example", "proxy.example:9000"], 8484)
        self.assertIn("nas.example", merged)
        self.assertIn("nas.example:8484", merged)   # ポート無し指定に既定ポートを補完
        self.assertIn("proxy.example:9000", merged)
        self.assertIn("proxy.example", merged)      # ポート付き指定に素のホストを補完
        self.assertIn("127.0.0.1:8484", merged)     # 既存は維持

    def test_none_and_empty(self):
        base = ks.build_allowed_hosts("127.0.0.1", 8484)
        self.assertEqual(ks.merge_allow_hosts(base, None, 8484), base)
        self.assertEqual(ks.merge_allow_hosts(base, ["", "  "], 8484), base)


if __name__ == "__main__":
    unittest.main()
