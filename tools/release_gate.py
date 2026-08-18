# -*- coding: utf-8 -*-
"""release_gate — 公開前の混入検査ゲート。exit 0 が push/公開の条件。

このリポジトリは「コンテンツを同梱しない汎用プレイヤー」である。私的制作物
(書籍由来のテキスト・音声・スライド、内部IP・ユーザ名等の私的文脈)が誤って
混入していないことを機械で確かめる。

設計(2026-08-18 セキュリティレビュー反映・v2):
  - 禁止語は**平文で保持しない**。release_gate_config.json には正規化後の
    SHA256ハッシュのみを置く(設定ファイル自体が「何を隠したいか」を公開しないため)。
    新しい禁止語の追加は `tools/gen_gate_hashes.py` を使う
  - 検査対象は**拒否リスト方式**(既知バイナリ以外は全部読む)。
    旧版の許可リスト方式(TEXT_EXT)は拡張子の抜け漏れが構造的に生じるため廃止
  - メディア判定は**拡張子でなくマジックバイト**で行う(拡張子偽装への対策)
  - 検査対象は**ファイル内容だけでなくファイルパス自身**も含む
    (ファイル名自体に私的文脈が現れるケースに対応)
  - 正規化(NFKC + casefold + ゼロ幅文字除去)+ JSON \\uXXXX エスケープの実体化、
    の両方の表現でハッシュ照合する(全角/半角・大文字小文字・JSON化による
    見た目の差異をすり抜けさせない)
  - `git ls-files` の失敗を**fail-closed**で扱う(黙って0件=全部合格、を防ぐ)

限界(既知の制約・README/CONTRIBUTINGに明記すること):
  - 作業ツリー(=これからpushする内容)のみを検査する。**過去コミットの履歴は見ない**。
    一度コミットして後で削除した私的情報は、このゲートでは検出できない
  - `git push --no-verify` でpre-pushフックは無効化できる。CI側(.github/workflows/)
    にも同じゲートを置き、ローカルのフック回避を検出すること

使い方:
    python tools/release_gate.py            # 検査
    python tools/install_hooks.py           # pre-push フック化(推奨)
    python tools/gen_gate_hashes.py          # 禁止語の追加(標準入力から1行1語)
"""
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tools" / "release_gate_config.json"

# メディアファイルを置いてよいディレクトリ(いずれも SOURCES.md 出所台帳が必須)。
# demo/ = 同梱デモ素材、docs/screenshots/ = README掲載用のUIスクリーンショット
MEDIA_ALLOWED_PREFIXES = ("demo/", "docs/screenshots/")

# 拡張子は判断材料にせず、実データ(マジックバイト)で既知メディアかどうかを判定する
_FTYP_OFFSET = 4


def detect_media_kind(data: bytes) -> str | None:
    """既知のメディア形式ならその種別名を返す。テキスト扱いしてよいものは None。"""
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3"):
        return "mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"OggS":
        return "ogg/opus"
    if data[:4] == b"fLaC":
        return "flac"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if len(data) >= 12 and data[_FTYP_OFFSET:_FTYP_OFFSET + 4] == b"ftyp":
        return "mp4/m4a/m4b"
    return None


def try_decode_text(data: bytes) -> str | None:
    """テキストとしてデコードを試みる。UTF-8 → BOM付きUTF-16 → NUL混入時のUTF-16推測。"""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if b"\x00" in data[:400]:
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    return None


def normalize(text: str) -> str:
    """gen_gate_hashes.py と完全に同一のロジックを保つこと(ハッシュ照合の前提)。"""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not (0x200B <= ord(c) <= 0x200D or ord(c) == 0xFEFF))
    return text.casefold()


_UESC = re.compile(r"\\u([0-9a-fA-F]{4})")


def unescape_json_unicode(text: str) -> str:
    """json.dump(ensure_ascii=True) が生む \\uXXXX を実体化する(検査すり抜け対策)。"""
    def repl(m):
        try:
            return chr(int(m.group(1), 16))
        except ValueError:
            return m.group(0)
    return _UESC.sub(repl, text)


def load_hash_buckets() -> dict:
    if not CONFIG.exists():
        print(f"エラー: 設定ファイルが見つかりません: {CONFIG}", file=sys.stderr)
        sys.exit(2)
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    buckets: dict = {}
    for h in cfg.get("hashes", []):
        buckets.setdefault(h["len"], set()).add(h["sha256"])
    return buckets


def scan_text(text: str, buckets: dict) -> list:
    """禁止語ハッシュに一致する長さを返す(空リスト=一致なし)。"""
    if not buckets:
        return []
    variants = {normalize(text)}
    unescaped = normalize(unescape_json_unicode(text))
    variants.add(unescaped)
    hits = []
    for variant in variants:
        for length, hashes in buckets.items():
            if len(variant) < length:
                continue
            for i in range(len(variant) - length + 1):
                digest = hashlib.sha256(variant[i:i + length].encode("utf-8")).hexdigest()
                if digest in hashes:
                    hits.append(length)
    return hits


def tracked_files() -> list:
    """git 追跡済み+ステージ済みのファイル一覧(公開されるものの近似)。fail-closed。"""
    r = subprocess.run(["git", "rev-parse", "--is-inside-work-tree"],
                       cwd=ROOT, capture_output=True, text=True)
    if r.returncode != 0 or r.stdout.strip() != "true":
        print("エラー: gitリポジトリとして認識できません(rev-parse失敗)", file=sys.stderr)
        sys.exit(2)
    r = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "-z",
         "--cached", "--others", "--exclude-standard"],
        cwd=ROOT, capture_output=True, text=False)
    if r.returncode != 0:
        print("エラー: git ls-files が失敗しました:\n" + r.stderr.decode("utf-8", "replace"),
              file=sys.stderr)
        sys.exit(2)
    raw = r.stdout.decode("utf-8", "replace")
    return [f for f in raw.split("\0") if f.strip()]


def main() -> int:
    buckets = load_hash_buckets()
    errors = []
    warnings = []
    files = tracked_files()

    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue

        # パス名自体も検査対象(ファイル名に私的文脈が出るケース)
        hits = scan_text(rel, buckets)
        if hits:
            errors.append(f"{rel}: ファイルパス自体に禁止パターンを検出(長さ{sorted(set(hits))})")

        try:
            data = p.read_bytes()
        except OSError as e:
            errors.append(f"{rel}: 読み取れません({e})")
            continue

        media_kind = detect_media_kind(data)
        if media_kind is not None:
            # メディアの置き場所は出所台帳(SOURCES.md)を伴うディレクトリに限定する。
            # docs/screenshots/ はREADME掲載用のUIスクリーンショット(表示内容は
            # 自作デモ素材のみ)を置く場所として 2026-08-19 に追加
            if not any(rel.startswith(pre) for pre in MEDIA_ALLOWED_PREFIXES):
                allowed = " / ".join(MEDIA_ALLOWED_PREFIXES)
                errors.append(f"{rel}: メディアファイル({media_kind})は {allowed} 配下のみ許可")
            continue  # メディアの内容自体はテキスト検査しない

        text = try_decode_text(data)
        if text is None:
            # テキストとしてもメディアとしても判定できない未知バイナリ
            if not rel.startswith("demo/"):
                errors.append(f"{rel}: 未知のバイナリ形式です。意図的なら demo/ 配下へ置き "
                               f"demo/SOURCES.md に出所を記録してください")
            else:
                warnings.append(f"{rel}: demo/ 配下の未知バイナリ(出所台帳の記載を確認してください)")
            continue

        hits = scan_text(text, buckets)
        if hits:
            errors.append(f"{rel}: 内容に禁止パターンを検出(長さ{sorted(set(hits))})")

    # メディア許可ディレクトリごとに出所台帳(SOURCES.md)の存在を要求する
    for pre in MEDIA_ALLOWED_PREFIXES:
        member_files = [f for f in files if f.startswith(pre) and not f.endswith("SOURCES.md")]
        if member_files and (pre + "SOURCES.md") not in files:
            errors.append(f"{pre} に素材があるのに {pre}SOURCES.md(出所台帳)がありません")

    if warnings:
        print(f"release_gate: 警告 {len(warnings)}件")
        for w in warnings:
            print("  " + w)
    if errors:
        print(f"release_gate: NG {len(errors)}件")
        for e in errors:
            print("  " + e)
        return 1
    print(f"release_gate: OK(検査 {len(files)} ファイル / 禁止語ハッシュ {sum(len(v) for v in buckets.values())}件)")
    print("注意: 本ゲートは作業ツリーのみを検査する。過去コミットの履歴は別途確認すること。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
