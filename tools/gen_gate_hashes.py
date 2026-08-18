# -*- coding: utf-8 -*-
"""禁止フレーズの正規化ハッシュを生成する(平文はこのプロセスの中だけに存在し、
リポジトリには一切書き込まれない)。

release_gate_config.json は平文の禁止語ではなく、正規化後の SHA256 ハッシュだけを持つ
(C-1対策: 設定ファイル自体が「何を隠したいか」を公開してしまう問題への対応)。

使い方:
    python tools/gen_gate_hashes.py
    -> 標準入力から1行1フレーズで読み取り、release_gate_config.json を上書きする
    -> 既存のハッシュ(release_gate_config.json 内)は保持したまま追記する

    echo -e "秘密の語1\n秘密の語2" | python tools/gen_gate_hashes.py

このスクリプト自体は禁止語を書き込まないため、公開リポジトリに含めてよい
(実行者の手元にだけ平文フレーズが一時的に存在する)。
"""
import hashlib
import json
import pathlib
import sys
import unicodedata

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "tools" / "release_gate_config.json"


def normalize(text: str) -> str:
    """release_gate.py の検査正規化と完全に同じロジックを使うこと(ハッシュ照合の前提)。"""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(c for c in text if not (0x200B <= ord(c) <= 0x200D or ord(c) == 0xFEFF))
    return text.casefold()


def main() -> None:
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    else:
        cfg = {"comment": "禁止フレーズの正規化SHA256ハッシュのみを保持する(平文は含まない)。"
                          "生成: tools/gen_gate_hashes.py", "hashes": []}
    existing = {(h["len"], h["sha256"]) for h in cfg["hashes"]}

    added = 0
    for line in sys.stdin:
        phrase = line.rstrip("\n\r")
        if not phrase:
            continue
        norm = normalize(phrase)
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        key = (len(norm), digest)
        if key not in existing:
            cfg["hashes"].append({"len": len(norm), "sha256": digest})
            existing.add(key)
            added += 1

    cfg["hashes"].sort(key=lambda h: (h["len"], h["sha256"]))
    CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"追加: {added}件 / 合計: {len(cfg['hashes'])}件 -> {CONFIG}")


if __name__ == "__main__":
    main()
