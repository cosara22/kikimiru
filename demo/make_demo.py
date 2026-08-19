# -*- coding: utf-8 -*-
"""同梱デモライブラリの生成スクリプト。

素材はすべて ffmpeg で合成する(第三者素材・音声合成・外部画像を一切使わない):

  - 音声: lavfi の正弦波(sine)のみ。区間ごとに高さが変わり、スライドがそれに同期する
  - 表紙: lavfi の gradients / drawbox / drawgrid のみで描く幾何学画像(512x512 PNG)

    python demo/make_demo.py                 # 全ブックを再生成
    python demo/make_demo.py --book demo-book  # 1冊だけ再生成
    python demo/make_demo.py --list          # 定義済みブックの一覧

前提: ffmpeg / ffprobe が PATH 上にあること(無ければ起動時にエラーで案内する)。

スキーマ:
  - `demo-book` は **v1 のまま**据え置く(後方互換の実証を兼ねる)。書誌メタデータも表紙も持たない
  - 他の3冊は v2。deck.json に authors / narrators / series / tags / description /
    cover / addedAt を持つ

書誌情報はすべて**架空**である。実在の書籍・著者・作品を指すものではない。
"""
import argparse
import hashlib
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "demo" / "library"

COVER_NAME = "cover.png"
COVER_SIZE = "512x512"
SLIDE_IMG_SIZE = "1280x720"  # 画像スライド(v2.1)のデモ用サイズ(16:9)

# 表紙の配色(UI再設計・案Dに同調)。シリーズ2冊は深い森×リーフグリーン、
# 単独本はUIのquestionアクセントと同系の琥珀にして、近黒のUI上で静かに際立たせる。
INK = "0xE9F5F2"   # 寒色系の図形色
INK_WARM = "0xFFF3E0"  # 暖色系の図形色

# ---------------------------------------------------------------------------
# ブック定義
#
# 1ブック = 1エントリ。音声・表紙・deck.json・content.json のすべてを
# ここから導出する(TIMELINE と slides/cues/content の二重管理をやめ、
# 要素数のズレで存在しないスライドIDを指すcueが生成される事故を防ぐ)。
#
#   id        … フォルダ名。そのままブックIDになる
#   schema    … deck.json の kikimiru 版(1 または 2)
#   seg_sec   … 1スライドあたりの秒数(冊ごとに変えて長さを散らす)
#   meta      … v2の書誌メタデータ。schema=1 のときは空にする
#   cover     … 表紙の lavfi フィルタ式(None なら表紙を作らない)
#   timeline  … (周波数Hz, スライドid, kind, title, bullets, note) の並び
# ---------------------------------------------------------------------------
BOOKS = [
    {
        "id": "demo-book",
        "schema": 1,
        "seg_sec": 8,
        "title": "kikimiru デモ — 同期の仕組み",
        "meta": {},
        "cover": None,  # v1のまま(表紙が無いブックの既定表示の確認も兼ねる)
        "timeline": [
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
        ],
    },
    {
        # シリーズ1冊目。2冊目と著者・話者・シリーズ名を共有する。
        "id": "demo-guide-1",
        "schema": 2,
        "seg_sec": 6,
        "title": "kikimiru の手引き 1 — スキーマの読み方",
        "meta": {
            "authors": ["サンプル・ラボ"],
            "narrators": ["合成音サンプルA"],
            "series": {"name": "kikimiru の手引き", "sequence": "1"},
            "tags": ["デモ", "スキーマ", "入門"],
            "description": "deck.json と content.json の役割を、デモ音声に合わせて1枚ずつ読み解く手引き。",
            "addedAt": "2026-08-17",
        },
        # 深い森→リーフグリーンの斜めグラデーション + 入れ子の四角(同心)
        "cover": (
            f"gradients=s={COVER_SIZE}:c0=0x0F2A1E:c1=0x3E9268"
            ":x0=0:y0=0:x1=511:y1=511:n=2:t=linear:d=1"
            f",drawbox=x=96:y=96:w=320:h=320:color={INK}@0.85:t=8"
            f",drawbox=x=160:y=160:w=192:h=192:color={INK}@0.55:t=8"
            f",drawbox=x=224:y=224:w=64:h=64:color={INK}@0.90:t=fill"
        ),
        "timeline": [
            (196, "s1", "title", "kikimiru の手引き 1", [
                "スキーマ v2 の読み方",
                "全7枚・約42秒のデモ音声",
            ], "音声・表紙とも ffmpeg で合成(自作素材)"),
            (247, "s2", "section", "2つのファイル", [], None),
            (294, "s3", "content", "deck.json — 構造", [
                "再生位置とスライドの対応だけを持つ",
                "本文テキストは入らない",
                "共有・書き出しの対象はこちら側",
            ], None),
            (330, "s4", "content", "content.json — 本文", [
                "title / bullets / note を持つ",
                "既定でローカル専用",
                "無くても構成だけで再生できる",
            ], None),
            (392, "s5", "content", "書誌メタデータ(v2)", [
                "authors / narrators で人を示す",
                "series で巻をまとめる",
                "tags と description は一覧の手がかり",
            ], "v2のフィールドはすべて任意"),
            (294, "s6", "content", "cover と addedAt", [
                "cover はフォルダ内の画像ファイル名",
                "addedAt は YYYY-MM-DD の追加日",
                "どちらも無ければ既定の表示になる",
            ], None),
            (247, "s7", "question", "書いてみる", [
                "自分の録音で deck.json を書く",
                "tools/validate_deck.py で検証する",
            ], None),
        ],
    },
    {
        # シリーズ2冊目。表紙は1冊目と同系色にして「同じシリーズ」と分かるようにする。
        "id": "demo-guide-2",
        "schema": 2,
        "seg_sec": 5,
        "title": "kikimiru の手引き 2 — 操作とライブラリ",
        "meta": {
            "authors": ["サンプル・ラボ"],
            "narrators": ["合成音サンプルA"],
            "series": {"name": "kikimiru の手引き", "sequence": "2"},
            "tags": ["デモ", "操作", "入門"],
            "description": "再生の操作とライブラリ画面の使い方を、デモ音声に合わせて1枚ずつたどる手引き。",
            "addedAt": "2026-08-18",
        },
        # 1冊目と同じ2色を逆向きに使い、図形は階段状の柱に変える
        "cover": (
            f"gradients=s={COVER_SIZE}:c0=0x3E9268:c1=0x0F2A1E"
            ":x0=511:y0=0:x1=0:y1=511:n=2:t=linear:d=1"
            f",drawbox=x=64:y=352:w=96:h=96:color={INK}@0.90:t=fill"
            f",drawbox=x=176:y=272:w=96:h=176:color={INK}@0.70:t=fill"
            f",drawbox=x=288:y=176:w=96:h=272:color={INK}@0.50:t=fill"
            f",drawbox=x=400:y=64:w=48:h=384:color={INK}@0.85:t=fill"
        ),
        "timeline": [
            (349, "s1", "title", "kikimiru の手引き 2", [
                "再生の操作とライブラリ画面",
                "全8枚・約40秒のデモ音声",
            ], "音声・表紙とも ffmpeg で合成(自作素材)"),
            (440, "s2", "section", "再生の操作", [], None),
            (523, "s3", "content", "シークと移動", [
                "スライド一覧のタップでその時刻へ飛ぶ",
                "前へ/次へでスライド単位に移動する",
                "再生位置は次に開いたとき復元される",
            ], None),
            (587, "s4", "content", "再生速度", [
                "×0.75 から ×2.0 まで切り替えられる",
                "長い教材は倍速で見取りができる",
            ], None),
            (659, "s5", "section", "ライブラリ", [], None),
            (523, "s6", "content", "並べ方", [
                "表紙のグリッドで一覧する",
                "著者・シリーズごとにまとめる",
                "タグは絞り込みの手がかりになる",
            ], None),
            (440, "s7", "content", "ブックの置き方", [
                "1ブック = 1フォルダ",
                "フォルダ名がそのままブックIDになる",
                "--library で置き場所を指定する",
            ], None),
            (392, "s8", "question", "次に試すこと", [
                "content.json を消して再生してみる",
                "cover の画像を差し替えてみる",
            ], None),
        ],
    },
    {
        # 単独本。著者・話者ともシリーズ2冊とは別にして、著者一覧の見え方を確認できるようにする。
        "id": "demo-audio-lab",
        "schema": 2,
        "seg_sec": 7,
        "title": "デモ素材のつくりかた — 正弦波と図形だけで組む",
        "meta": {
            "authors": ["音の実験室"],
            "narrators": ["合成音サンプルB"],
            "tags": ["デモ", "音声合成", "ffmpeg"],
            # 詳細画面の折返し表示を実データで確認できるよう、意図的に長め(数文)の紹介文にしている
            "description": "ffmpeg の正弦波と lavfi の図形だけでデモ用ブックを組み立てる手順のメモ。"
                           "音声は sine フィルタで合成し、表紙は geq と drawbox の組み合わせで描く。"
                           "外部素材を一切使わないため、生成し直しても常に同じ見た目と長さになる。"
                           "自分のライブラリを作る前の練習台として、deck.json の書き方をここで一巡できる。",
            "addedAt": "2026-08-19",
        },
        # 画像スライド(v2.1)のデモを兼ねる: 全スライドに幾何学画像+意味層(alt)を持たせる
        "slide_images": True,
        # 琥珀の放射グラデーション + 格子 + 枠(シリーズ2冊と対照的な配色にする)
        "cover": (
            f"gradients=s={COVER_SIZE}:c0=0xD9A86A:c1=0x3A2314"
            ":x0=256:y0=256:x1=511:y1=511:n=2:t=radial:d=1"
            f",drawgrid=w=64:h=64:t=2:color={INK_WARM}@0.25"
            f",drawbox=x=128:y=128:w=256:h=256:color={INK_WARM}@0.90:t=10"
            f",drawbox=x=224:y=224:w=64:h=64:color={INK_WARM}@0.95:t=fill"
        ),
        "timeline": [
            (220, "s1", "title", "デモ素材のつくりかた", [
                "正弦波と図形だけで同期教材を組む",
                "全5枚・約35秒のデモ音声",
            ], "生成スクリプト: demo/make_demo.py"),
            (262, "s2", "content", "音をつくる", [
                "ffmpeg の sine で1区間ずつ作る",
                "区間ごとに周波数を変える",
                "concat で1本の mp3 にまとめる",
            ], None),
            (330, "s3", "content", "cue を書き出す", [
                "区間の開始秒がそのまま cue になる",
                "先頭の cue は必ず t = 0.0",
                "秒とスライドIDの対応表ができる",
            ], None),
            (392, "s4", "content", "表紙をつくる", [
                "lavfi の gradients で下地を描く",
                "drawbox と drawgrid で図形を重ねる",
                "512x512 の PNG で書き出す",
            ], None),
            (440, "s5", "question", "作り替える", [
                "周波数と区間の長さを変えて試す",
                "第三者の素材は使わない",
            ], "出所は demo/SOURCES.md に記録する"),
        ],
    },
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


def build_audio(book_dir: pathlib.Path, timeline, seg_sec: int):
    """正弦波の区間をつなげて audio.mp3 を作り、(長さ秒, sha256) を返す。

    _seg*.wav / _concat.txt は処理途中の一時ファイル。例外発生時も finally で
    必ず削除し、release_gate の検査対象フォルダに紛れ込ませない。
    """
    segs = []
    concat = book_dir / "_concat.txt"
    try:
        for i, row in enumerate(timeline):
            freq = row[0]
            seg = book_dir / f"_seg{i}.wav"
            run_checked([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", f"sine=frequency={freq}:duration={seg_sec}",
                "-af", f"volume=0.2,afade=t=in:d=0.3,afade=t=out:st={seg_sec - 0.5}:d=0.5",
                str(seg)])
            segs.append(seg)

        concat.write_text("".join(f"file '{s.name}'\n" for s in segs), encoding="utf-8")
        mp3 = book_dir / "audio.mp3"
        run_checked(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
                     "-codec:a", "libmp3lame", "-b:a", "48k", "-ac", "1", str(mp3)],
                    cwd=book_dir)

        probe = run_checked(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
             str(mp3)])
        duration = float(probe.stdout.strip())
        sha = hashlib.sha256(mp3.read_bytes()).hexdigest()
        return duration, sha
    finally:
        for s in segs:
            s.unlink(missing_ok=True)
        concat.unlink(missing_ok=True)


def build_cover(book_dir: pathlib.Path, cover_filter: str) -> str:
    """lavfi のフィルタ式だけで表紙PNGを描き、ファイル名を返す。

    外部の画像素材も画像ライブラリも使わない(gradients / drawbox / drawgrid のみ)。
    """
    out = book_dir / COVER_NAME
    run_checked([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"{cover_filter},format=rgb24",
        "-frames:v", "1", str(out)])
    return COVER_NAME


def build_slide_images(book_dir: pathlib.Path, timeline) -> dict:
    """画像スライド(v2.1デモ)を lavfi の図形だけで描く。sid→相対パスの辞書を返す。

    琥珀系グラデーション地に白い枠、下段にスライド進行度を示す正方形の列
    (i枚目= i+1個)という決定論の構図。外部素材・文字描画は使わない。
    """
    out_dir = book_dir / "slides"
    out_dir.mkdir(exist_ok=True)
    names = {}
    for i, (_, sid, *_rest) in enumerate(timeline):
        boxes = "".join(
            f",drawbox=x={80 + k * 90}:y=580:w=60:h=60:color=0xFFF3E0@0.9:t=fill"
            for k in range(i + 1))
        filt = (
            f"gradients=s={SLIDE_IMG_SIZE}:c0=0x2B1A10:c1=0x8A5A2E"
            ":x0=0:y0=0:x1=1279:y1=719:n=2:t=linear:d=1"
            ",drawbox=x=80:y=80:w=1120:h=440:color=0xFFF3E0@0.85:t=6"
            f",drawbox=x=560:y=260:w=160:h=80:color=0xFFF3E0@0.9:t=fill"
            + boxes)
        run_checked([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"{filt},format=rgb24",
            "-frames:v", "1", str(out_dir / f"{sid}.png")])
        names[sid] = f"slides/{sid}.png"
    return names


def build_deck(book: dict, cover_name, duration: float, sha: str,
               slide_image_names: dict = None) -> dict:
    """deck.json の中身を組み立てる。

    キーの並びは docs/SCHEMA.md の記載順に合わせる。v2の書誌フィールドは
    meta に入っているものだけを出力するため、v1のブックには一切現れない。
    """
    meta = book["meta"]
    deck = {"kikimiru": book["schema"], "title": book["title"]}
    for key in ("authors", "narrators", "series", "tags", "description"):
        if key in meta:
            deck[key] = meta[key]
    if cover_name:
        deck["cover"] = cover_name
    if "addedAt" in meta:
        deck["addedAt"] = meta["addedAt"]

    seg_sec = book["seg_sec"]
    timeline = book["timeline"]
    deck["audio"] = {"src": "audio.mp3", "duration": round(duration, 3), "sha256": sha}
    slide_image_names = slide_image_names or {}
    deck["slides"] = [
        ({"id": sid, "kind": kind, "image": slide_image_names[sid]}
         if sid in slide_image_names else {"id": sid, "kind": kind})
        for _, sid, kind, *_ in timeline]
    deck["cues"] = [{"t": float(i * seg_sec), "slide": sid}
                    for i, (_, sid, *_rest) in enumerate(timeline)]
    return deck


def build_content(timeline, schema: int, with_image_alt: bool = False) -> dict:
    """content.json(本文)の中身を組み立てる。版番号は deck と揃える(SCHEMA.md推奨)。

    with_image_alt が真のとき、各スライドに画像の意味層(alt)を付ける
    (画像スライドはピクセルだけにせず、内容をテキストで併記する規約のデモ)。
    """
    slides = {}
    for i, (_, sid, _kind, title, bullets, note) in enumerate(timeline):
        body = {"title": title, "bullets": bullets, "note": note}
        if with_image_alt:
            body["alt"] = (
                f"デモの幾何学スライド画像({i + 1}枚目)。琥珀系のグラデーション地に"
                f"白い枠と中央の矩形、下段にスライド進行度を示す正方形が{i + 1}個並ぶ。"
                f"内容は「{title}」。")
        slides[sid] = body
    return {"kikimiru": schema, "slides": slides}


def write_json(path: pathlib.Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def build_book(book: dict) -> dict:
    """1冊分の素材とJSONを生成し、表示用のサマリを返す。"""
    book_dir = LIBRARY / book["id"]
    book_dir.mkdir(parents=True, exist_ok=True)

    duration, sha = build_audio(book_dir, book["timeline"], book["seg_sec"])
    cover_name = build_cover(book_dir, book["cover"]) if book["cover"] else None
    slide_image_names = (build_slide_images(book_dir, book["timeline"])
                         if book.get("slide_images") else None)

    deck = build_deck(book, cover_name, duration, sha, slide_image_names)
    write_json(book_dir / "deck.json", deck)
    write_json(book_dir / "content.json",
               build_content(book["timeline"], book["schema"],
                             with_image_alt=bool(slide_image_names)))

    return {
        "id": book["id"],
        "dir": book_dir,
        "duration": duration,
        "slides": len(book["timeline"]),
        "cover": cover_name,
        "schema": book["schema"],
    }


def soften_console_encoding() -> None:
    """コンソールが表現できない文字(em dash 等)で落ちないようにする。

    Windows の既定コンソールは cp932 のため、書籍タイトルに含まれる「—」を
    そのまま print すると UnicodeEncodeError で異常終了する。エンコーディング自体は
    端末に合わせたまま、表現できない文字だけを置換文字に落とす。
    """
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="replace")
            except (OSError, ValueError):
                pass


def main() -> None:
    soften_console_encoding()
    parser = argparse.ArgumentParser(description="同梱デモライブラリを生成する")
    parser.add_argument("--book", metavar="ID",
                        help="このIDのブックだけを再生成する(既定: 全ブック)")
    parser.add_argument("--list", action="store_true", help="定義済みブックの一覧を表示して終了")
    args = parser.parse_args()

    known = {b["id"]: b for b in BOOKS}
    if args.list:
        for b in BOOKS:
            series = b["meta"].get("series")
            label = f" [{series['name']} #{series['sequence']}]" if series else ""
            print(f"{b['id']}\tv{b['schema']}\t{b['title']}{label}")
        return

    if args.book is not None and args.book not in known:
        print(f"エラー: 未知のブックIDです: {args.book}", file=sys.stderr)
        print(f"指定できるID: {', '.join(known)}", file=sys.stderr)
        sys.exit(1)

    check_ffmpeg_available()
    targets = [known[args.book]] if args.book else BOOKS

    results = [build_book(b) for b in targets]

    print(f"DEMO_DONE {len(results)}冊 → {LIBRARY}")
    for r in results:
        cover = r["cover"] or "(表紙なし)"
        print(f"  {r['id']}\tv{r['schema']}\t{r['duration']:.1f}s\t"
              f"{r['slides']}枚\t{cover}\t{r['dir']}")


if __name__ == "__main__":
    main()
