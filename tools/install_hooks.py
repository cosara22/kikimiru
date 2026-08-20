# -*- coding: utf-8 -*-
"""pre-push フックとして release_gate を仕込む。

    python tools/install_hooks.py

- `.git` がファイル(worktree/submodule)の場合にも対応する(`git rev-parse --git-path`)
- 既存の pre-push フックがある場合は上書きせず、警告して中断する
- フック本文は `python3` を優先探索する(macOS/LinuxにはPATHへ`python`が無いことが多く、
  決め打ちだと push が常に失敗していた)
- フックは `--require-config` を付けて呼ぶ。禁止語設定はリポジトリ外にあるため、
  設定を見失ったまま「禁止語0件で全部合格」となる事故を防ぐ(fail-closed)。
  禁止語を持たない場合は `python tools/gen_gate_hashes.py --init-empty` で
  空の設定を明示的に作ってからインストールすること

限界: `git push --no-verify` でこのフックは無効化できる。CI(.github/workflows/release-gate.yml)
にも同じゲートを置いてあるのは、このローカルフック回避への対策。
"""
import subprocess
import sys
from pathlib import Path


def git_hooks_dir() -> Path:
    r = subprocess.run(["git", "rev-parse", "--git-path", "hooks"],
                       capture_output=True, text=True, check=True)
    return Path(r.stdout.strip())


HOOK_BODY = """#!/bin/sh
# kikimiru release_gate — 自動生成(tools/install_hooks.py)。手動編集しないこと。
set -e
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "release_gate: python3/python が見つからないため検査をスキップできません" >&2
  exit 1
fi
"$PY" tools/release_gate.py --require-config
"""


def main() -> None:
    hooks_dir = git_hooks_dir()
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook = hooks_dir / "pre-push"

    if hook.exists():
        existing = hook.read_text(encoding="utf-8", errors="replace")
        if "kikimiru release_gate" in existing:
            # 自分が生成したフックは中身を最新へ更新する。ここで「既にある」と
            # 何もせずに戻ると、フック本文を直しても既存の環境へ永久に反映されない
            if existing == HOOK_BODY:
                print(f"既にインストール済みです(最新): {hook}")
                return
            hook.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
            print(f"更新しました: {hook}")
            return
        print(f"エラー: 既存の pre-push フックがあります({hook})。")
        print("上書きすると既存の内容が失われるため、中断します。")
        print("内容を確認し、必要なら手動で `python tools/release_gate.py` の呼び出しを追記してください。")
        sys.exit(1)

    hook.write_text(HOOK_BODY, encoding="utf-8", newline="\n")
    # 実行ビットの付与。Windows(NTFS)では効果がないが、POSIX環境(WSL/Mac/Linux)や
    # Git for Windows の一部構成では必要なため、失敗しても致命的エラーにしない。
    try:
        import stat
        hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    except OSError:
        pass
    print(f"installed: {hook}")


if __name__ == "__main__":
    main()
