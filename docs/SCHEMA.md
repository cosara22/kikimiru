# kikimiru スキーマ v1

1ブック = 1フォルダ。フォルダには音声ファイルと `deck.json`(必須)、`content.json`(任意)を置く。

## 設計原則: 構造と本文の分離

- **deck.json** — タイミング・構成などの**構造データのみ**。テキスト本文を含まない。
  共有・書き出し機能の対象は常にこちら側だけとする
- **content.json** — スライドの本文(タイトル・箇条書き・注記)。既定でローカル専用とし、
  アプリの共有・エクスポート機能の対象にしない

この分離は仕様であり、実装の都合ではない。本文を含むファイルの流通をフォーマットの段階で
起こしにくくすることが目的である(プレイヤーは content.json が無くても構成のみで動作する)。

## deck.json

```json
{
  "kikimiru": 1,
  "title": "ブックのタイトル",
  "audio": {
    "src": "audio.mp3",
    "duration": 48.0,
    "sha256": "任意(音声との対応検証用)"
  },
  "slides": [
    { "id": "s1", "kind": "title" },
    { "id": "s2", "kind": "content" }
  ],
  "cues": [
    { "t": 0.0, "slide": "s1" },
    { "t": 8.0, "slide": "s2" }
  ]
}
```

- `kikimiru`: スキーマ版(整数)。v1 は `1`
- `audio.src`: フォルダ内の音声ファイル名(単一セグメント・相対のみ。`/` や `\` を含めない)
- `audio.sha256`: 音声ファイルとの対応検証用に予約されたフィールド。**現状プレイヤー側では
  検証を行っていない**(将来の完全性チェック用の予約枠)
- `slides[].kind`: `title` / `section` / `content` / `question`。未知の `kind` 値が来た場合、
  プレイヤーは `content` 相当の見た目・扱い(専用装飾なしの通常スライド)にフォールバック表示する
- `cues[].t`: 表示開始秒(**昇順は必須**)。`slide` は slides の id を参照
- **先頭cue(`cues[0].t`)は `0.0` であることが必須**(音声再生開始と同時に何らかのスライドが
  表示されている状態を保証するため)。この規約は `tools/validate_deck.py` で機械検証される
- 同じ slide を複数 cue から参照してよい(戻り表示)

## content.json

```json
{
  "kikimiru": 1,
  "slides": {
    "s1": { "title": "…", "bullets": ["…"], "note": "…" }
  }
}
```

- `bullets` は最大5点・1点40字以内を推奨(スマホ縦画面の可読性)。これらは強制ではなく
  推奨値であり、`tools/validate_deck.py` は超過を警告(WARN)として報告するのみでエラーにしない
- `note` は補足(出典・参照など)。省略可・`null` も許容

## /api/books

サーバの `GET /api/books` はライブラリ内の全ブックを次の形状で返す:

```json
{
  "kikimiru": 1,
  "books": [
    { "id": "demo-book", "title": "kikimiru デモ — 同期の仕組み", "duration": 48.0, "slides": 6 }
  ]
}
```

- `id`: **ブックフォルダ名そのもの**(`--library` 配下のディレクトリ名と一致する)。
  `deck.json` にIDフィールドは無い
- `id` に使用できる文字: 現在の実装(`server/kikimiru_server.py` の `SAFE_SEGMENT`)は
  **英数字・`.`・`_`・`-`・日本語(ひらがな・カタカナ・常用漢字相当の範囲)のみを許可する
  ホワイトリスト方式**であり、加えて `..` を含むこと・先頭が `.` であることを個別に禁止する。
  日本語のブック名は主要な範囲で問題なく使えるが、全角記号・絵文字・ハングル等の
  日本語以外の非ASCII文字・拡張漢字はフォルダ名に使えない点に注意する
- `title`: `deck.json.title`。無ければフォルダ名にフォールバック
- `duration`: `deck.json.audio.duration`。無ければ `null`
- `slides`: `deck.json.slides` の要素数

## スキーマ検証

`tools/validate_deck.py` で、ブックフォルダが本スキーマに沿っているか機械検証できる:

```bash
python tools/validate_deck.py demo/library/demo-book
```

- 検証項目: `deck.json` の型・必須値(`kikimiru == 1` / `audio.src` の形式 / `slides[].kind` の
  値域 / `cues` の昇順・先頭 `t == 0.0` / `cues[].slide` が `slides[].id` に存在するか 等)、
  `content.json`(存在する場合のみ)の型・必須値
- 問題なければ `VALID` を表示して終了コード `0`。問題があれば `ERROR:` 行を全てまとめて表示し
  終了コード `1`(1件見つけて終了はしない)。`bullets` の件数・文字数上限超過は `WARN:` として
  報告するのみで終了コードには影響しない

## エクスポート(予定)

- [Podcasting 2.0 JSON Chapters](https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/examples/chapters/jsonChapters.md)
  への書き出し(`startTime` + `title` + `img`)。書き出し対象は deck 側の構造+章タイトルに限る
