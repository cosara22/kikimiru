# -*- coding: utf-8 -*-
"""deck.json / content.json のスキーマ検証CLI(docs/SCHEMA.md 準拠)。

kikimiru は「構造(deck.json)」と「本文(content.json)」を分離するスキーマを採る。
本ツールはその定義に沿ってブックフォルダを検証する。問題は1件見つけて止めず、
まとめて報告する。

スキーマ v2 では deck.json に書誌メタデータ(著者・話者・シリーズ・タグ・説明・
表紙・追加日時)を追加した。v2フィールドは**すべて任意**であり、v1のdeckも
引き続き VALID と判定する(後方互換)。

使い方:
    python tools/validate_deck.py <ブックフォルダ>
    例: python tools/validate_deck.py demo/library/demo-book

終了コード:
    0 — 検証OK(標準出力に VALID。警告のみの場合も0)
    1 — 検証NG(標準出力に ERROR の一覧)
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

VALID_KINDS = {"title", "section", "content", "question"}
MAX_BULLETS = 5
MAX_BULLET_CHARS = 40

# 許容するスキーマ版。v1のdeckも読めるよう両方を受け入れる。
VALID_SCHEMA_VERSIONS = (1, 2)

# v2で追加された deck.json の任意フィールド。
# kikimiru: 1 のdeckにこれらが入っていた場合は WARN で版の更新を促す。
V2_DECK_FIELDS = (
    "authors", "narrators", "series", "tags", "description", "cover", "addedAt",
)

# addedAt の日付のみ形式(YYYY-MM-DD)。
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def is_num(v) -> bool:
    """bool は int のサブクラスなので明示的に除外する(True/Falseを数値扱いしない)。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def is_int(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def is_str(v) -> bool:
    return isinstance(v, str)


def is_str_list(v) -> bool:
    """文字列のみからなる配列か(空配列は許容)。"""
    return isinstance(v, list) and all(is_str(x) for x in v)


def looks_like_iso_date(s: str) -> bool:
    """`YYYY-MM-DD` もしくは ISO8601 日時として解釈できるか。

    形式違反はWARN止まりなので、ここでは「妥当そうか」だけを判定する。
    末尾 `Z` は Python 3.10 以前の fromisoformat が解釈できないため補正する。
    """
    if DATE_ONLY_RE.match(s):
        try:
            date.fromisoformat(s)
            return True
        except ValueError:
            return False
    t = s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s
    try:
        datetime.fromisoformat(t)
        return True
    except ValueError:
        return False


def validate_v2_meta(deck: dict, errors: list, warnings: list) -> None:
    """deck.json のv2書誌メタデータを検証する。

    すべて任意フィールドのため、**存在する場合のみ**型を検査する(欠損はエラーにしない)。
    """
    # --- 文字列配列 3種(authors / narrators / tags) ---
    for key in ("authors", "narrators", "tags"):
        if key not in deck:
            continue
        if not is_str_list(deck[key]):
            errors.append(
                f"deck.json: {key} は文字列の配列である必要があります(実際: {deck[key]!r})")

    # --- series ---
    if "series" in deck:
        series = deck["series"]
        if not isinstance(series, dict):
            errors.append(
                f"deck.json: series はオブジェクトである必要があります(実際: {series!r})")
        else:
            name = series.get("name")
            if not is_str(name) or not name:
                errors.append(
                    f"deck.json: series.name は空でない文字列である必要があります(実際: {name!r})")
            # sequence は任意。"1" や "2.5" のような文字列で表す(数値ではない)。
            if "sequence" in series and not is_str(series["sequence"]):
                errors.append(
                    f"deck.json: series.sequence は文字列である必要があります"
                    f"(実際: {series['sequence']!r})")

    # --- description ---
    if "description" in deck and not is_str(deck["description"]):
        errors.append(
            f"deck.json: description は文字列である必要があります(実際: {deck['description']!r})")
    elif "description" in deck and is_str(deck["description"]) and len(deck["description"]) > 400:
        # 逐語転載ガード: descriptionは「書誌としての紹介文」であり書籍本文を置く場所ではない。
        # 長文は本文引用の混入を疑うサイン(SCHEMA.mdの引用禁止規約の機械的な接地点)。ERRORにはしない
        warnings.append(
            f"deck.json: description が400字を超えています({len(deck['description'])}字)。"
            f"本文の引用が混入していないか確認してください(紹介文は数文に収める)")

    # --- cover ---
    if "cover" in deck:
        cover = deck["cover"]
        if not is_str(cover) or not cover:
            errors.append(
                f"deck.json: cover は空でない文字列である必要があります(実際: {cover!r})")
        elif "/" in cover or "\\" in cover:
            errors.append(
                f"deck.json: cover は単一セグメントのファイル名である必要があります"
                f"('/' '\\' を含めない): {cover!r}")
        elif ".." in cover:
            errors.append(f"deck.json: cover に '..' を含めることはできません: {cover!r}")

    # --- addedAt ---
    if "addedAt" in deck:
        added_at = deck["addedAt"]
        if not is_str(added_at):
            errors.append(
                f"deck.json: addedAt は文字列である必要があります(実際: {added_at!r})")
        elif not looks_like_iso_date(added_at):
            warnings.append(
                f"deck.json: addedAt が YYYY-MM-DD でも ISO8601 日時でもありません"
                f"(実際: {added_at!r})")


def load_json(path: Path, errors: list):
    """JSONファイルを読み込む。失敗したらerrorsに追記してNoneを返す。"""
    if not path.is_file():
        errors.append(f"{path.name}: ファイルが存在しません({path})")
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        errors.append(f"{path.name}: 読み込みに失敗しました({e})")
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        errors.append(f"{path.name}: JSONとして不正です({e})")
        return None


def validate_deck(deck, errors: list, warnings: list) -> set:
    """deck.json(構造データ)を検証する。戻り値はslide idの集合(cross-check用)。"""
    if not isinstance(deck, dict):
        errors.append("deck.json: トップレベルはオブジェクトである必要があります")
        return set()

    kikimiru = deck.get("kikimiru")
    if not is_int(kikimiru) or kikimiru not in VALID_SCHEMA_VERSIONS:
        errors.append(
            f"deck.json: kikimiru は整数の "
            f"{' または '.join(str(v) for v in VALID_SCHEMA_VERSIONS)} "
            f"である必要があります(実際: {kikimiru!r})")

    # v2の書誌メタデータは版に関係なく型を検査する(混入していても型は正しく保つ)。
    validate_v2_meta(deck, errors, warnings)

    # v1宣言のdeckにv2フィールドが入っている場合は版の更新を促す(エラーにはしない)。
    if kikimiru == 1:
        used = [k for k in V2_DECK_FIELDS if k in deck]
        if used:
            warnings.append(
                f"deck.json: v2フィールド({', '.join(used)})が使われていますが "
                f"kikimiru: 1 です。2 への更新を検討してください")

    title = deck.get("title")
    if not is_str(title):
        errors.append(f"deck.json: title は文字列である必要があります(実際: {title!r})")

    audio = deck.get("audio")
    if not isinstance(audio, dict):
        errors.append("deck.json: audio はオブジェクトである必要があります")
    else:
        src = audio.get("src")
        if not is_str(src) or not src:
            errors.append(f"deck.json: audio.src は文字列である必要があります(実際: {src!r})")
        elif "/" in src or "\\" in src:
            errors.append(
                f"deck.json: audio.src は単一セグメントのファイル名である必要があります"
                f"('/' '\\' を含めない): {src!r}")

    # --- slides ---------------------------------------------------------
    slides = deck.get("slides")
    slide_ids = set()
    if not isinstance(slides, list):
        errors.append("deck.json: slides は配列である必要があります")
        slides = []
    for i, s in enumerate(slides):
        if not isinstance(s, dict):
            errors.append(f"deck.json: slides[{i}] はオブジェクトである必要があります")
            continue
        sid = s.get("id")
        if not is_str(sid) or not sid:
            errors.append(f"deck.json: slides[{i}].id は文字列である必要があります(実際: {sid!r})")
        else:
            if sid in slide_ids:
                errors.append(f"deck.json: slides[{i}].id が重複しています: {sid!r}")
            slide_ids.add(sid)
        kind = s.get("kind")
        if kind not in VALID_KINDS:
            errors.append(
                f"deck.json: slides[{i}].kind は {sorted(VALID_KINDS)} のいずれかである必要があります"
                f"(実際: {kind!r})")

    # --- cues -------------------------------------------------------------
    cues = deck.get("cues")
    if not isinstance(cues, list):
        errors.append("deck.json: cues は配列である必要があります")
        cues = []
    if not cues:
        errors.append("deck.json: cues が空です(先頭cueは t=0.0 である必要があります)")

    prev_t = None
    first_valid_t = None
    for i, c in enumerate(cues):
        if not isinstance(c, dict):
            errors.append(f"deck.json: cues[{i}] はオブジェクトである必要があります")
            continue
        t = c.get("t")
        slide = c.get("slide")
        if not is_num(t):
            errors.append(f"deck.json: cues[{i}].t は数値である必要があります(実際: {t!r})")
        if not is_str(slide) or not slide:
            errors.append(f"deck.json: cues[{i}].slide は文字列である必要があります(実際: {slide!r})")
        elif slide_ids and slide not in slide_ids:
            errors.append(f"deck.json: cues[{i}].slide が slides に存在しません: {slide!r}")

        if is_num(t):
            if i == 0:
                first_valid_t = t
            if prev_t is not None and t < prev_t:
                errors.append(
                    f"deck.json: cues は t について昇順である必要があります"
                    f"(cues[{i}].t={t} が直前の t={prev_t} を下回っています)")
            prev_t = t

    if cues and first_valid_t is not None and first_valid_t != 0.0:
        errors.append(f"deck.json: cues[0].t は 0.0 である必要があります(実際: {first_valid_t})")

    return slide_ids


def validate_content(content, errors: list, warnings: list) -> None:
    """content.json(本文データ)を検証する。bulletsの件数/文字数上限は警告に留める。"""
    if not isinstance(content, dict):
        errors.append("content.json: トップレベルはオブジェクトである必要があります")
        return

    kikimiru = content.get("kikimiru")
    if not is_int(kikimiru) or kikimiru not in VALID_SCHEMA_VERSIONS:
        errors.append(
            f"content.json: kikimiru は整数の "
            f"{' または '.join(str(v) for v in VALID_SCHEMA_VERSIONS)} "
            f"である必要があります(実際: {kikimiru!r})")

    slides = content.get("slides")
    if not isinstance(slides, dict):
        errors.append("content.json: slides は辞書(オブジェクト)である必要があります")
        return

    for sid, body in slides.items():
        if not isinstance(body, dict):
            errors.append(f"content.json: slides[{sid!r}] はオブジェクトである必要があります")
            continue

        title = body.get("title")
        if not is_str(title):
            errors.append(f"content.json: slides[{sid!r}].title は文字列である必要があります(実際: {title!r})")

        bullets = body.get("bullets")
        if not isinstance(bullets, list) or not all(is_str(b) for b in bullets):
            errors.append(
                f"content.json: slides[{sid!r}].bullets は文字列の配列である必要があります"
                f"(実際: {bullets!r})")
        else:
            if len(bullets) > MAX_BULLETS:
                warnings.append(
                    f"content.json: slides[{sid!r}].bullets が推奨上限({MAX_BULLETS}件)を"
                    f"超えています({len(bullets)}件)")
            for j, b in enumerate(bullets):
                if len(b) > MAX_BULLET_CHARS:
                    warnings.append(
                        f"content.json: slides[{sid!r}].bullets[{j}] が推奨上限"
                        f"({MAX_BULLET_CHARS}字)を超えています({len(b)}字): {b!r}")

        note = body.get("note")
        if note is not None and not is_str(note):
            errors.append(f"content.json: slides[{sid!r}].note は文字列またはnullである必要があります(実際: {note!r})")


def main() -> int:
    if len(sys.argv) != 2:
        print("使い方: python tools/validate_deck.py <ブックフォルダ>")
        return 1
    book = Path(sys.argv[1])

    errors: list = []
    warnings: list = []

    deck = load_json(book / "deck.json", errors)
    slide_ids = set()
    if deck is not None:
        slide_ids = validate_deck(deck, errors, warnings)

    content_path = book / "content.json"
    if content_path.is_file():
        content = load_json(content_path, errors)
        if content is not None:
            validate_content(content, errors, warnings)
    # content.json はSCHEMA.mdの設計上「無くても構成のみで動作する」任意ファイルのため、
    # 存在しなくてもエラーにしない。

    for w in warnings:
        print(f"WARN: {w}")

    if errors:
        print(f"INVALID: {len(errors)}件の問題")
        for e in errors:
            print(f"  ERROR: {e}")
        return 1

    print("VALID")
    return 0


if __name__ == "__main__":
    sys.exit(main())
