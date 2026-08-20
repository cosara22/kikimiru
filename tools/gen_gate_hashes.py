# -*- coding: utf-8 -*-
"""禁止フレーズの正規化ハッシュを生成する(平文はこのプロセスの中だけに存在する)。

設定ファイルは**リポジトリの外**に置く(2026-08-20 監査反映・v3)。

v2 では「平文でなくSHA256だけを持てば設定ファイルを公開してよい」と考え、
tools/release_gate_config.json をリポジトリに含めていた。しかしソルト無しの
素のSHA256・公開された正規化関数・語ごとの文字数(len)の3点が揃うと、設定ファイルは
「思いついた語をタダで照合できるオラクル」になる。実測で、事前知識ゼロの総当たりと
公開ハンドルからのマスク攻撃だけで平文が復元された。よって v3 では設定そのものを
公開物から外し、あわせて語ごとの長さも持たない(走査窓の範囲 min_len/max_len のみ)。

設定の在り処(release_gate.py と同じ規則):
    1. 環境変数 KIKIMIRU_GATE_CONFIG が指すパス
    2. Windows: %APPDATA%\\kikimiru\\release_gate_config.json
       その他:   ${XDG_CONFIG_HOME:-~/.config}/kikimiru/release_gate_config.json

使い方:
    python tools/gen_gate_hashes.py
    -> 標準入力から1行1フレーズで読み取り、設定へ追記する(既存のハッシュは保持)

    echo -e "秘密の語1\\n秘密の語2" | python tools/gen_gate_hashes.py

    python tools/gen_gate_hashes.py --migrate tools/release_gate_config.json
    -> v2形式(リポジトリ内)の設定を v3 形式へ変換して外部の設定へ取り込む。
       ハッシュ値は同一アルゴリズムのためそのまま引き継げる(平文は不要)。
       取り込んだら移行元のファイルは git から外して削除すること

    python tools/gen_gate_hashes.py --where
    -> 現在の設定パスを表示するだけ

    python tools/gen_gate_hashes.py --init-empty
    -> 空の設定を作る。禁止語を持たない外部contributorが、ゲートを
       fail-closed(--require-config)のまま通せるようにするための明示的な宣言

このスクリプト自体は禁止語を書き込まないため、公開リポジトリに含めてよい。
"""
import hashlib
import json
import pathlib
import sys
import unicodedata

# 設定パスの解決は release_gate.py と同一の規則を使う(単一の正本にする)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from release_gate import resolve_config_path  # noqa: E402

COMMENT = ("禁止フレーズの正規化SHA256ハッシュのみを保持する(平文は含まない)。"
           "このファイルはリポジトリの外に置くこと — ハッシュと長さの公開は照合オラクルになる。"
           "生成: tools/gen_gate_hashes.py")


def normalize(text: str) -> str:
    """release_gate.py の検査正規化と完全に同じロジックを使うこと(ハッシュ照合の前提)。"""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not (0x200B <= ord(c) <= 0x200D or ord(c) == 0xFEFF))
    return text.casefold()


def load(path: pathlib.Path) -> dict:
    """既存設定を v3 の内部表現({hashes:set, min_len, max_len})で読む。"""
    if not path.exists():
        return {"hashes": set(), "min_len": None, "max_len": None}
    cfg = json.loads(path.read_text(encoding="utf-8"))
    hashes, lengths = set(), []
    for h in cfg.get("hashes", []):
        if isinstance(h, str):
            hashes.add(h)
        elif isinstance(h, dict) and "sha256" in h:
            hashes.add(h["sha256"])
            if isinstance(h.get("len"), int):
                lengths.append(h["len"])
    min_len = cfg.get("min_len", min(lengths) if lengths else None)
    max_len = cfg.get("max_len", max(lengths) if lengths else None)
    return {"hashes": hashes, "min_len": min_len, "max_len": max_len}


def save(path: pathlib.Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    cfg = {
        "comment": COMMENT,
        "version": 3,
        "min_len": state["min_len"],
        "max_len": state["max_len"],
        # 長さと対応づかないよう、ハッシュ文字列だけをソートして持つ
        "hashes": sorted(state["hashes"]),
    }
    path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = sys.argv[1:]
    path, _ = resolve_config_path()

    if "--where" in args:
        print(path)
        return 0

    if "--init-empty" in args:
        if path.exists():
            print(f"既に設定があります(上書きしません): {path}")
            return 0
        save(path, {"hashes": set(), "min_len": None, "max_len": None})
        print(f"空の設定を作成しました: {path}")
        print("  禁止語を追加するには、このスクリプトへ標準入力から1行1語で流してください")
        return 0

    state = load(path)
    before = len(state["hashes"])

    if "--migrate" in args:
        i = args.index("--migrate")
        if i + 1 >= len(args):
            print("エラー: --migrate の後に移行元のパスを指定してください", file=sys.stderr)
            return 2
        src = pathlib.Path(args[i + 1])
        if not src.exists():
            print(f"エラー: 移行元が見つかりません: {src}", file=sys.stderr)
            return 2
        old = load(src)
        if not old["hashes"]:
            print(f"エラー: 移行元にハッシュがありません: {src}", file=sys.stderr)
            return 2
        state["hashes"] |= old["hashes"]
        lens = [x for x in (old["min_len"], old["max_len"], state["min_len"], state["max_len"])
                if isinstance(x, int)]
        state["min_len"] = min(lens)
        state["max_len"] = max(lens)
        save(path, state)
        print(f"移行: {len(old['hashes'])}件を取り込み(合計 {len(state['hashes'])}件) -> {path}")
        # 「〜」(U+301C)は Windows の既定コンソール(cp932)で encode できずに落ちるため使わない
        print(f"  走査窓: {state['min_len']}-{state['max_len']} 文字")
        print(f"  次の手順: git rm --cached {src} && 手元の {src} を削除してください")
        return 0

    if args:
        print(f"エラー: 未知の引数: {' '.join(args)}", file=sys.stderr)
        return 2

    if sys.stdin.isatty():
        print(f"設定: {path}")
        print("禁止フレーズを1行1語で入力してください(終了: Ctrl+Z→Enter / Ctrl+D)")

    for line in sys.stdin:
        phrase = line.rstrip("\n\r")
        if not phrase:
            continue
        norm = normalize(phrase)
        if not norm:
            continue
        state["hashes"].add(hashlib.sha256(norm.encode("utf-8")).hexdigest())
        n = len(norm)
        state["min_len"] = n if state["min_len"] is None else min(state["min_len"], n)
        state["max_len"] = n if state["max_len"] is None else max(state["max_len"], n)

    if state["min_len"] is None:
        print("追加するフレーズがありませんでした", file=sys.stderr)
        return 1

    save(path, state)
    added = len(state["hashes"]) - before
    print(f"追加: {added}件 / 合計: {len(state['hashes'])}件 -> {path}")
    print(f"  走査窓: {state['min_len']}〜{state['max_len']} 文字")
    return 0


if __name__ == "__main__":
    sys.exit(main())
