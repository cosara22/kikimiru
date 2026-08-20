# -*- coding: utf-8 -*-
"""release_gate — 公開前の混入検査ゲート。exit 0 が push/公開の条件。

このリポジトリは「コンテンツを同梱しない汎用プレイヤー」である。私的制作物
(書籍由来のテキスト・音声・スライド、内部IP・ユーザ名等の私的文脈)が誤って
混入していないことを機械で確かめる。

設計(2026-08-20 監査反映・v3):
  - 禁止語の設定ファイルは**リポジトリの外**に置く。v2 は「平文を持たず正規化SHA256
    だけを tools/release_gate_config.json に持つ」設計だったが、
      (1) ソルト無しの素のSHA256
      (2) 正規化関数がリポジトリ内に公開されている
      (3) 語ごとの文字数 `len` を併記していた
    の3点が揃うため、設定ファイル自体が「思いついた語をタダで照合できるオラクル」
    として機能してしまった。事前知識ゼロの総当たり・公開ハンドルからのマスク攻撃で
    平文が復元されることを実測で確認している。公開物から設定を外し、この経路を断つ。
  - 設定の在り処(この順に探す):
      1. 環境変数 KIKIMIRU_GATE_CONFIG が指すパス
      2. OS既定のユーザ設定ディレクトリ
         Windows: %APPDATA%\\kikimiru\\release_gate_config.json
         その他:   ${XDG_CONFIG_HOME:-~/.config}/kikimiru/release_gate_config.json
  - 語ごとの長さは持たない。走査窓の範囲 min_len/max_len だけを持つ
    (どの長さの語が何件あるか、という分布自体が手掛かりになるため)
  - **設定ファイルがリポジトリ内に現れたらNG**にする(v2形式への逆戻りを防ぐ)
  - **メディアのメタデータも検査する**。v2 はマジックバイトでメディアと判定した時点で
    素通りさせていたため、PNGのtEXt・MP3のID3・JPEGのEXIFに私的文脈が残っていても
    緑になった。v3 は各形式のメタデータ領域を取り出して禁止語照合にかけ、
    メタデータの存在自体も警告として可視化する
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
  - 設定を持たない環境(外部contributor)では禁止語照合は行われない。
    メンテナとCIは `--require-config` を付けて fail-closed で回すこと
  - メタデータ検査は PNG / JPEG / MP3 のみ。他形式は警告を出して素通りする

使い方:
    python tools/release_gate.py                      # 検査
    python tools/release_gate.py --require-config     # 設定必須(メンテナ/CI用)
    python tools/install_hooks.py                     # pre-push フック化(推奨)
    python tools/gen_gate_hashes.py                   # 禁止語の追加(標準入力から1行1語)
    python tools/gen_gate_hashes.py --migrate PATH    # v2形式(リポジトリ内)からの移行
"""
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import unicodedata
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]

# 設定ファイルの名前。この名前のファイルがリポジトリ内にあること自体を異常とみなす
CONFIG_BASENAME = "release_gate_config.json"
CONFIG_ENV = "KIKIMIRU_GATE_CONFIG"

# メディアファイルを置いてよいディレクトリ(いずれも SOURCES.md 出所台帳が必須)。
# demo/ = 同梱デモ素材、docs/screenshots/ = README掲載用のUIスクリーンショット、
# web/ = PWAアイコン等の配信素材(tools/make_icons.py による自作生成)
MEDIA_ALLOWED_PREFIXES = ("demo/", "docs/screenshots/", "web/")

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


# --- 禁止語設定の読み込み ---------------------------------------------------

def default_config_path() -> pathlib.Path:
    """OS既定のユーザ設定ディレクトリ上の設定パス(リポジトリ外)。"""
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        root = pathlib.Path(base) if base else pathlib.Path.home() / "AppData" / "Roaming"
    else:
        base = os.environ.get("XDG_CONFIG_HOME")
        root = pathlib.Path(base) if base else pathlib.Path.home() / ".config"
    return root / "kikimiru" / CONFIG_BASENAME


def resolve_config_path() -> tuple:
    """(パス, 環境変数で明示指定されたか) を返す。"""
    env = os.environ.get(CONFIG_ENV)
    if env:
        return pathlib.Path(env), True
    return default_config_path(), False


def load_spec(require_config: bool) -> dict:
    """禁止語ハッシュの走査仕様を返す。

    戻り値: {"min": int, "max": int, "hashes": set[str], "count": int, "source": str}
    設定が無い場合は空仕様(count=0)を返す。require_config なら fail-closed。
    """
    path, explicit = resolve_config_path()
    empty = {"min": 0, "max": -1, "hashes": set(), "count": 0, "source": "(なし)"}

    if not path.exists():
        msg = f"禁止語設定が見つかりません: {path}"
        if require_config or explicit:
            print(f"エラー: {msg}", file=sys.stderr)
            print(f"  {CONFIG_ENV} で場所を指定するか、tools/gen_gate_hashes.py で作成してください",
                  file=sys.stderr)
            sys.exit(2)
        print(f"注意: {msg}")
        print("  → 禁止語の照合は行いません(外部contributor向けの動作)。"
              "メンテナ/CIは --require-config を付けてください")
        return empty

    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"エラー: 禁止語設定を読めません({path}): {e}", file=sys.stderr)
        sys.exit(2)

    hashes = set()
    lengths = []
    raw = cfg.get("hashes", [])
    for h in raw:
        if isinstance(h, str):
            # v3形式: ハッシュ文字列の配列(長さは持たない)
            hashes.add(h)
        elif isinstance(h, dict) and "sha256" in h:
            # v2形式: {"len": n, "sha256": "..."} — 読めるが移行を促す
            hashes.add(h["sha256"])
            if isinstance(h.get("len"), int):
                lengths.append(h["len"])
        else:
            print(f"エラー: 禁止語設定の形式が不正です({path})", file=sys.stderr)
            sys.exit(2)

    if lengths:
        print("注意: 設定が旧v2形式(語ごとのlen付き)です。"
              "tools/gen_gate_hashes.py --migrate で v3 形式へ移行してください")

    min_len = cfg.get("min_len")
    max_len = cfg.get("max_len")
    if min_len is None or max_len is None:
        if lengths:
            min_len, max_len = min(lengths), max(lengths)
        elif hashes:
            print(f"エラー: min_len/max_len がありません({path})", file=sys.stderr)
            sys.exit(2)
        else:
            min_len, max_len = 0, -1

    if hashes and (not isinstance(min_len, int) or not isinstance(max_len, int)
                   or min_len < 1 or max_len < min_len):
        print(f"エラー: min_len/max_len の値が不正です({path})", file=sys.stderr)
        sys.exit(2)

    return {"min": min_len, "max": max_len, "hashes": hashes,
            "count": len(hashes), "source": str(path)}


def scan_text(text: str, spec: dict) -> list:
    """禁止語ハッシュに一致した窓長を返す(空リスト=一致なし)。"""
    if not spec["hashes"]:
        return []
    variants = {normalize(text)}
    variants.add(normalize(unescape_json_unicode(text)))
    hits = []
    for variant in variants:
        for length in range(spec["min"], spec["max"] + 1):
            if len(variant) < length:
                continue
            for i in range(len(variant) - length + 1):
                digest = hashlib.sha256(variant[i:i + length].encode("utf-8")).hexdigest()
                if digest in spec["hashes"]:
                    hits.append(length)
    return hits


# --- メディアのメタデータ抽出 -----------------------------------------------

_PRINTABLE_RUN = re.compile(rb"[\x20-\x7e\xa1-\xff]{4,}")


def _printable_runs(blob: bytes) -> str:
    """バイト列から可読文字列の断片を拾って連結する(取りこぼし防止の保険)。"""
    return " ".join(m.decode("latin-1", "replace") for m in _PRINTABLE_RUN.findall(blob))


def png_metadata(data: bytes) -> tuple:
    """PNGのテキスト系チャンクを取り出す。戻り値: (抽出テキスト, 見つけたチャンク名)。"""
    texts, kinds = [], []
    off = 8
    n = len(data)
    while off + 8 <= n:
        length = int.from_bytes(data[off:off + 4], "big")
        typ = data[off + 4:off + 8].decode("ascii", "replace")
        body = data[off + 8:off + 8 + length]
        if len(body) != length:
            break  # 壊れている/切り詰められている
        if typ == "tEXt":
            kinds.append(typ)
            texts.append(body.replace(b"\x00", b" ").decode("latin-1", "replace"))
        elif typ == "zTXt":
            kinds.append(typ)
            sep = body.find(b"\x00")
            keyword = body[:sep].decode("latin-1", "replace") if sep >= 0 else ""
            try:
                texts.append(keyword + " " + zlib.decompress(body[sep + 2:]).decode("utf-8", "replace"))
            except (zlib.error, ValueError):
                texts.append(keyword + " " + _printable_runs(body))
        elif typ == "iTXt":
            kinds.append(typ)
            # keyword \0 compflag compmethod langtag \0 transkey \0 text
            parts = body.split(b"\x00", 1)
            keyword = parts[0].decode("latin-1", "replace")
            rest = parts[1] if len(parts) > 1 else b""
            compressed = bool(rest[:1] == b"\x01")
            tail = rest[2:].split(b"\x00", 2)
            payload = tail[2] if len(tail) > 2 else b""
            if compressed:
                try:
                    payload = zlib.decompress(payload)
                except zlib.error:
                    pass
            texts.append(keyword + " " + payload.decode("utf-8", "replace"))
        elif typ in ("eXIf", "tIME"):
            kinds.append(typ)
            texts.append(_printable_runs(body))
        off += 12 + length
        if typ == "IEND":
            break
    return "\n".join(t for t in texts if t.strip()), kinds


def jpeg_metadata(data: bytes) -> tuple:
    """JPEGのAPPn/COMセグメントを取り出す。"""
    texts, kinds = [], []
    off = 2
    n = len(data)
    while off + 4 <= n:
        if data[off] != 0xFF:
            break
        marker = data[off + 1]
        if marker == 0xD9 or marker == 0xDA:  # EOI / SOS(以降は画素データ)
            break
        seg_len = int.from_bytes(data[off + 2:off + 4], "big")
        body = data[off + 4:off + 2 + seg_len]
        if 0xE0 <= marker <= 0xEF:            # APP0..APP15(EXIF/XMP/ICC等)
            kinds.append(f"APP{marker - 0xE0}")
            texts.append(_printable_runs(body))
        elif marker == 0xFE:                  # COM
            kinds.append("COM")
            texts.append(body.decode("utf-8", "replace"))
        off += 2 + seg_len
    return "\n".join(t for t in texts if t.strip()), kinds


def _syncsafe(b: bytes) -> int:
    v = 0
    for byte in b:
        v = (v << 7) | (byte & 0x7F)
    return v


def mp3_metadata(data: bytes) -> tuple:
    """MP3のID3v2タグ全体とID3v1テールを取り出す。"""
    texts, kinds = [], []
    if data[:3] == b"ID3" and len(data) >= 10:
        size = _syncsafe(data[6:10])
        tag = data[10:10 + size]
        if tag:
            kinds.append(f"ID3v2.{data[3]}")
            texts.append(_printable_runs(tag))
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        kinds.append("ID3v1")
        texts.append(data[-128:].decode("latin-1", "replace"))
    return "\n".join(t for t in texts if t.strip()), kinds


# メタデータを検査できる形式。ここに無い形式は「未検査」として警告する
_METADATA_READERS = {
    "png": png_metadata,
    "jpg": jpeg_metadata,
    "mp3": mp3_metadata,
}


def media_metadata(kind: str, data: bytes) -> tuple:
    """(抽出テキスト, 見つけたメタデータ種別, 検査できたか) を返す。"""
    reader = _METADATA_READERS.get(kind)
    if reader is None:
        return "", [], False
    try:
        text, kinds = reader(data)
    except Exception as e:                     # 壊れたファイルでゲートを落とさない
        return f"(メタデータ解析に失敗: {e})", ["解析失敗"], False
    return text, kinds, True


# --- 検査本体 ---------------------------------------------------------------

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
    require_config = "--require-config" in sys.argv[1:]
    for arg in sys.argv[1:]:
        if arg != "--require-config":
            print(f"エラー: 未知の引数: {arg}", file=sys.stderr)
            return 2

    spec = load_spec(require_config)
    errors = []
    warnings = []
    files = tracked_files()

    # 設定ファイルがリポジトリ内へ戻っていないか(v2形式への逆戻り防止)
    for rel in files:
        if pathlib.PurePosixPath(rel).name == CONFIG_BASENAME:
            errors.append(f"{rel}: 禁止語設定をリポジトリ内に置かないこと"
                          f"(ハッシュと長さの公開は照合オラクルになる。設定は {CONFIG_ENV} "
                          f"または {default_config_path()} へ)")
    stray = ROOT / "tools" / CONFIG_BASENAME
    if stray.exists():
        warnings.append(f"tools/{CONFIG_BASENAME} が作業ツリーに残っています"
                        f"(git管理外でも、配布物に混ざらないよう削除を推奨)")

    for rel in files:
        p = ROOT / rel
        if not p.is_file():
            continue

        # パス名自体も検査対象(ファイル名に私的文脈が出るケース)
        hits = scan_text(rel, spec)
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

            # 画素/音声そのものは読めないが、メタデータは私的文脈が残る主要経路なので検査する
            meta_text, meta_kinds, inspected = media_metadata(media_kind, data)
            if not inspected:
                warnings.append(f"{rel}: {media_kind} のメタデータは未検査"
                                f"(内容は目視で確認すること)")
            elif meta_kinds:
                hits = scan_text(meta_text, spec)
                if hits:
                    errors.append(f"{rel}: メタデータに禁止パターンを検出"
                                  f"({'/'.join(meta_kinds)} 長さ{sorted(set(hits))})")
                else:
                    warnings.append(f"{rel}: メタデータあり({'/'.join(meta_kinds)})"
                                    f" — 生成ツール名・パス等が残っていないか確認してください")
            continue

        text = try_decode_text(data)
        if text is None:
            # テキストとしてもメディアとしても判定できない未知バイナリ
            if not rel.startswith("demo/"):
                errors.append(f"{rel}: 未知のバイナリ形式です。意図的なら demo/ 配下へ置き "
                               f"demo/SOURCES.md に出所を記録してください")
            else:
                warnings.append(f"{rel}: demo/ 配下の未知バイナリ(出所台帳の記載を確認してください)")
            continue

        hits = scan_text(text, spec)
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
    print(f"release_gate: OK(検査 {len(files)} ファイル / 禁止語ハッシュ {spec['count']}件"
          f" / 設定 {spec['source']})")
    print("注意: 本ゲートは作業ツリーのみを検査する。過去コミットの履歴は別途確認すること。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
