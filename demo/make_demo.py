# -*- coding: utf-8 -*-
"""同梱デモの生成スクリプト。

音声は ffmpeg の正弦波のみで合成する(第三者素材・音声合成を一切使わない)。
8秒ごとに音の高さが変わり、スライドがそれに同期して切り替わる。

    python demo/make_demo.py   # demo/library/demo-book/ を再生成

前提: ffmpeg / ffprobe が PATH 上にあること(無ければ起動時にエラーで案内する)。
"""
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
BOOK = ROOT / "demo" / "library" / "demo-book"
SEG_SEC = 8

# デモの全データはこの1本のテーブルから導出する(FREQS/slides/cues/content の
# 二重管理をやめ、要素数のズレで存在しないスライドIDを指すcueが生成される事故を防ぐ)。
# (周波数Hz, スライドid, kind, title, bullets, note)
TIMELINE = [
    (262, "s1", "title", "kikimiru デモ", [
        "音声に同期してスライドが切り替わります",
        "8秒ごとに音の高さが変わります(全6枚・約48秒)",
    ], "音声は正弦波のみで合成(自作素材)"),
    (330, "s2", "section", "同期の仕組み", [], None),
    (392, "s3", "content", "deck.json — 構造", [
        "cues が「秒 → スライドID」を対応づける",
        "テキスト本文を含まない構造データ",
        "共有・書き出しの対象は常にこちら側だけ",
    ], None),
    (440, "s4", "content", "content.json — 本文", [
        "タイトル・箇条書き・注記を持つ",
        "既定でローカル専用",
        "無くても構成情報のみで再生できる(消して試せます)",
    ], None),
    (523, "s5", "content", "操作", [
        "スライド一覧のタップでシーク",
        "倍速 ×0.75〜×2.0",
        "前へ/次へボタンでスライド単位の移動",
    ], None),
    (349, "s6", "question", "試してみる", [
        "自分の録音・自作教材で deck.json を書いてみる",
        "書式は docs/SCHEMA.md を参照",
    ], None),
]


def check_ffmpeg_available() -> None:
    """ffmpeg/ffprobeがPATH上にあるか確認する。無ければ案内して終了する。"""
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        print(f"エラー: {' / '.join(missing)} が見つかりません。", file=sys.stderr)
        print("https://ffmpeg.org/ からインストールし、PATHを通してから再実行してください。",
              file=sys.stderr)
        sys.exit(1)


def run_checked(args, cwd=None) -> subprocess.CompletedProcess:
    """ffmpeg/ffprobeをcheck=True・stderr捕捉付きで実行する共通ラッパー。

    capture_output=True だけだと失敗時にstderrが握り潰されて原因が読めないため、
    CalledProcessError を捕まえてstderrを日本語メッセージと共に表示してから終了する。
    """
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, check=True, text=True)
    except subprocess.CalledProcessError as e:
        print(f"エラー: コマンドの実行に失敗しました: {' '.join(args)}", file=sys.stderr)
        print(f"終了コード: {e.returncode}", file=sys.stderr)
        if e.stderr:
            print("--- stderr ---", file=sys.stderr)
            print(e.stderr, file=sys.stderr)
        sys.exit(1)


def main() -> None:
    check_ffmpeg_available()
    BOOK.mkdir(parents=True, exist_ok=True)

    # _seg*.wav / _concat.txt は処理途中の一時ファイル。例外発生時も finally で
    # 必ず削除し、release_gate の検査対象フォルダに紛れ込ませない。
    segs = []
    concat = BOOK / "_concat.txt"
    try:
        for i, row in enumerate(TIMELINE):
            freq = row[0]
            seg = BOOK / f"_seg{i}.wav"
            run_checked([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={SEG_SEC}",
                "-af", f"volume=0.2,afade=t=in:d=0.3,afade=t=out:st={SEG_SEC - 0.5}:d=0.5",
                str(seg)])
            segs.append(seg)

        concat.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        mp3 = BOOK / "audio.mp3"
        run_checked(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                     "-codec:a", "libmp3lame", "-b:a", "48k", "-ac", "1", str(mp3)],
                    cwd=BOOK)

        probe = run_checked(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(mp3)])
        duration = float(probe.stdout.strip())
        sha = hashlib.sha256(mp3.read_bytes()).hexdigest()

        deck = {
            "kikimiru": 1,
            "title": "kikimiru デモ — 同期の仕組み",
            "audio": {"src": "audio.mp3", "duration": round(duration, 3), "sha256": sha},
            "slides": [{"id": sid, "kind": kind} for _, sid, kind, *_ in TIMELINE],
            "cues": [{"t": float(i * SEG_SEC), "slide": sid}
                     for i, (_, sid, *_rest) in enumerate(TIMELINE)],
        }
        content = {
            "kikimiru": 1,
            "slides": {
                sid: {"title": title, "bullets": bullets, "note": note}
                for _, sid, _kind, title, bullets, note in TIMELINE
            },
        }
        (BOOK / "deck.json").write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        (BOOK / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"DEMO_DONE {mp3}({duration:.1f}s)")
    finally:
        for s in segs:
            s.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
