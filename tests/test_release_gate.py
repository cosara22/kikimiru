# -*- coding: utf-8 -*-
"""release_gate.py のリグレッションテスト。

セキュリティレビュー(2026-08-18)と公開後監査(2026-08-20)で実際に確認された
回避パターン・漏洩経路をコード化する。
一時的な最小gitリポジトリを作り、release_gate.py をサブプロセスとして実行して検証する。

**設計上の注意**: このテストは本番の禁止語設定を使わない。テスト専用のダミー禁止語
("kkmr-test-secret" 等、実在の私的プロジェクト名・ユーザー名とは無関係な語)を
tools/gen_gate_hashes.py で都度ハッシュ化し、テストだけで使う一時設定を生成する。
これにより、このテストファイル自身に実際の禁止語をリテラルとして書かずに済み、
公開リポジトリに含めても "検査対象が自分自身の秘密を暴露する" という本末転倒を避けられる
(実際、開発中に本docstringの説明文中で実在の禁止語を引用してしまい、ゲートに
検出された。それ自体がこの設計の正しさの実証になっている)。

また、設定ファイルは**リポジトリの外**に置くのが v3 の要件なので、テストでも
一時リポジトリの外に設定を作り、環境変数 KIKIMIRU_GATE_CONFIG で渡す。

    python -m unittest tests.test_release_gate -v
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "tools" / "release_gate.py"
GEN_HASHES = ROOT / "tools" / "gen_gate_hashes.py"

# 実在の私的情報とは無関係な、テスト専用のダミー禁止語
DUMMY_SECRET = "kkmr-test-secret"


def _png_chunk(typ: bytes, body: bytes) -> bytes:
    # CRCはゲート側で検証しないため0で埋める(構造だけ正しくあればよい)
    return len(body).to_bytes(4, "big") + typ + body + b"\x00\x00\x00\x00"


def png_with_text_chunk(text: str) -> bytes:
    """tEXtチャンクに任意の文字列を仕込んだ最小PNG。"""
    ihdr = (1).to_bytes(4, "big") + (1).to_bytes(4, "big") + bytes([8, 2, 0, 0, 0])
    return (b"\x89PNG\r\n\x1a\n"
            + _png_chunk(b"IHDR", ihdr)
            + _png_chunk(b"tEXt", b"Comment\x00" + text.encode("utf-8"))
            + _png_chunk(b"IEND", b""))


def _syncsafe(n: int) -> bytes:
    return bytes([(n >> 21) & 0x7F, (n >> 14) & 0x7F, (n >> 7) & 0x7F, n & 0x7F])


def mp3_with_id3(text: str) -> bytes:
    """ID3v2タグに任意の文字列を仕込んだ最小mp3(音声データは持たない)。"""
    payload = text.encode("utf-8")
    frame = b"TIT2" + len(payload + b"\x03").to_bytes(4, "big") + b"\x00\x00" + b"\x03" + payload
    return b"ID3\x04\x00\x00" + _syncsafe(len(frame)) + frame


class ReleaseGateTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kikimiru-gate-test-"))
        # 設定は「リポジトリの外」に置くのが v3 の要件。一時リポジトリとは別の場所に作る
        self.cfgdir = Path(tempfile.mkdtemp(prefix="kikimiru-gate-cfg-"))
        self.config = self.cfgdir / "release_gate_config.json"
        # gen_gate_hashes.py は release_gate.py を import するため、放っておくと
        # 一時リポジトリに tools/__pycache__ が生まれ「未知のバイナリ」として検出される
        # (本番リポジトリでは .gitignore 済み)。テストでは生成自体を止める
        # PYTHONIOENCODING: 子プロセスの出力を UTF-8 に固定する。既定のままだと
        # Windows では cp932 になり、日本語のメッセージをパイプ越しに読む際に落ちる
        self.env = dict(os.environ, KIKIMIRU_GATE_CONFIG=str(self.config),
                        PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")

        (self.tmp / "tools").mkdir()
        shutil.copy(GATE, self.tmp / "tools" / "release_gate.py")
        shutil.copy(GEN_HASHES, self.tmp / "tools" / "gen_gate_hashes.py")
        # テスト専用のダミー禁止語だけを含む一時設定を生成する(本番設定は使わない)
        subprocess.run([sys.executable, "tools/gen_gate_hashes.py"], cwd=self.tmp,
                       input=DUMMY_SECRET + "\n", text=True, check=True,
                       capture_output=True, encoding="utf-8", env=self.env)
        self._run_git(["init", "-q"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.cfgdir, ignore_errors=True)

    def _run_git(self, args):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
                       cwd=self.tmp, check=True, capture_output=True)

    def _write(self, rel: str, data: bytes):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    def _commit_all(self):
        self._run_git(["add", "-A"])
        self._run_git(["commit", "-q", "-m", "x"])

    def _run_gate(self, env=None):
        return subprocess.run([sys.executable, "tools/release_gate.py", "--require-config"],
                              cwd=self.tmp, capture_output=True, text=True,
                              encoding="utf-8", env=env or self.env)

    # --- 合格するはずのケース ------------------------------------------------

    def test_clean_repo_passes(self):
        self._write("README.md", "hello world".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_media_under_demo_allowed(self):
        # 最小のID3v2ヘッダのみ(実際のmp3である必要はない。マジックバイト判定のみ検査)
        self._write("demo/library/x/audio.mp3", b"ID3\x03\x00\x00\x00\x00\x00\x00")
        self._write("demo/SOURCES.md", "audio.mp3: dummy".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_media_under_docs_screenshots_allowed(self):
        # README掲載用スクリーンショットの置き場所(出所台帳つきなら合格)
        self._write("docs/screenshots/home.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        self._write("docs/screenshots/SOURCES.md", "home.png: dummy".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_media_in_screenshots_requires_ledger(self):
        # 出所台帳(SOURCES.md)が無ければ docs/screenshots/ でも不合格
        self._write("docs/screenshots/home.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("SOURCES.md", r.stdout)

    def test_harmless_metadata_passes_with_warning(self):
        # メタデータがあるだけでは不合格にしないが、人が見直せるよう警告に出す
        self._write("docs/screenshots/home.png", png_with_text_chunk("Software: dummy"))
        self._write("docs/screenshots/SOURCES.md", "home.png: dummy".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("メタデータあり", r.stdout)

    # --- 検出できるはずの回避パターン(2026-08-18 セキュリティレビューで実測) ----

    def test_extensionless_file_is_scanned(self):
        self._write("NOTES", f"{DUMMY_SECRET} test".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NOTES", r.stdout)

    def test_uncommon_text_extension_is_scanned(self):
        self._write("sub.vtt", DUMMY_SECRET.encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_json_ensure_ascii_escape_is_caught(self):
        # \uXXXX エスケープを手書きで模擬する(json.dumpだと非ASCII文字が無いと素通りするため、
        # 意図的にエスケープ形式で仕込む)
        escaped = "".join(f"\\u{ord(c):04x}" for c in DUMMY_SECRET)
        self._write("deck_ascii.json", ('{"title": "%s"}' % escaped).encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_utf16_encoding_is_caught(self):
        self._write("memo16.md", f"{DUMMY_SECRET} note".encode("utf-16"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_case_insensitive_match(self):
        self._write("case.md", DUMMY_SECRET.upper().encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_fullwidth_and_zerowidth_normalized(self):
        # 先頭文字を全角にし、ゼロ幅スペースを1文字挟む
        mangled = "Ｋ" + DUMMY_SECRET[1:3] + "​" + DUMMY_SECRET[3:]
        self._write("width.md", mangled.encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_disguised_binary_outside_demo_rejected(self):
        # 拡張子を.binに偽装したmp3(マジックバイトで検出されるべき)
        self._write("lecture.bin", b"ID3\x03\x00\x00\x00\x00\x00\x00fake")
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lecture.bin", r.stdout)

    def test_unknown_binary_outside_demo_rejected(self):
        self._write("mystery.dat", bytes(range(256)))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_secret_in_filename_is_caught(self):
        # ファイル名自体に禁止語が現れるケース(内容は無害)
        self._write(f"notes-{DUMMY_SECRET}.md", b"harmless content")
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    # --- メディアのメタデータ経由の漏洩(2026-08-20 監査で判明) ----------------

    def test_png_text_chunk_secret_is_caught(self):
        # 旧版はマジックバイトでメディアと判定した時点で素通りさせていた
        self._write("docs/screenshots/home.png", png_with_text_chunk(f"Author: {DUMMY_SECRET}"))
        self._write("docs/screenshots/SOURCES.md", "home.png: dummy".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("メタデータ", r.stdout)

    def test_mp3_id3_secret_is_caught(self):
        self._write("demo/library/x/audio.mp3", mp3_with_id3(DUMMY_SECRET))
        self._write("demo/SOURCES.md", "audio.mp3: dummy".encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("メタデータ", r.stdout)

    # --- 設定の在り処(2026-08-20 監査で判明) ---------------------------------

    def test_config_inside_repo_is_rejected(self):
        # ハッシュと長さを公開すると照合オラクルになるため、設定はリポジトリ内に置けない
        self._write("tools/release_gate_config.json",
                    self.config.read_text(encoding="utf-8").encode("utf-8"))
        self._commit_all()
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("release_gate_config.json", r.stdout)

    # --- fail-closed ---------------------------------------------------------

    def test_non_git_directory_fails_closed(self):
        shutil.rmtree(self.tmp / ".git")
        self._write("leak.txt", DUMMY_SECRET.encode("utf-8"))
        r = self._run_gate()
        self.assertNotEqual(r.returncode, 0)

    def test_missing_config_fails_closed(self):
        # メンテナ/CI は --require-config で回すため、設定が無ければ落ちること
        self._write("README.md", b"hello")
        self._commit_all()
        env = dict(os.environ, KIKIMIRU_GATE_CONFIG=str(self.cfgdir / "does-not-exist.json"))
        r = self._run_gate(env=env)
        self.assertNotEqual(r.returncode, 0)


if __name__ == "__main__":
    unittest.main()
