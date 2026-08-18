# kikimiru スキーマ v2

1ブック = 1フォルダ。フォルダには音声ファイルと `deck.json`(必須)、`content.json`(任意)を置く。

## v1 と v2 の関係

v2 は v1 に**書誌メタデータ(著者・話者・シリーズ・タグ・説明・表紙・追加日時)を
足しただけ**の拡張であり、**完全な後方互換**である。

- v2で追加されたフィールドは**すべて任意**。1つも書かなければ v1 と同じ内容になる
- `kikimiru: 1` の deck はそのまま有効で、`tools/validate_deck.py` も VALID と判定する
- 追加は deck.json 側にのみ行う。**構造(deck.json)と本文(content.json)の分離という
  設計原則は v2 でも変わらない**。書誌メタデータは「この音源が何であるか」を指す
  ライブラリ用の情報であって、本文ではない
- 新規に作るブックは `kikimiru: 2` を宣言する。`kikimiru: 1` のまま v2フィールドを
  書いた場合、検証は WARN を出すがエラーにはしない

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
  "kikimiru": 2,
  "title": "ブックのタイトル",

  "authors": ["著者名"],
  "narrators": ["話者名"],
  "series": { "name": "シリーズ名", "sequence": "1" },
  "tags": ["タグ"],
  "description": "説明文",
  "cover": "cover.jpg",
  "addedAt": "2026-08-19",

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

- `kikimiru`: スキーマ版(整数)。`1` または `2`。新規は `2`
- `audio.src`: フォルダ内の音声ファイル名(単一セグメント・相対のみ。`/` や `\` を含めない)
- `audio.sha256`: 音声ファイルとの対応検証用に予約されたフィールド。**現状プレイヤー側では
  検証を行っていない**(将来の完全性チェック用の予約枠)
- `slides[].kind`: `title` / `section` / `content` / `question`。未知の `kind` 値が来た場合、
  プレイヤーは `content` 相当の見た目・扱い(専用装飾なしの通常スライド)にフォールバック表示する
- `cues[].t`: 表示開始秒(**昇順は必須**)。`slide` は slides の id を参照
- **先頭cue(`cues[0].t`)は `0.0` であることが必須**(音声再生開始と同時に何らかのスライドが
  表示されている状態を保証するため)。この規約は `tools/validate_deck.py` で機械検証される
- 同じ slide を複数 cue から参照してよい(戻り表示)

### 書誌メタデータ(v2で追加)

著者・シリーズ・話者ごとのライブラリ画面を組み立てるための情報。
**すべて任意フィールドであり、欠損してよい**。書かれている場合のみ型が検証される。

| フィールド | 型 | 意味・省略時の挙動 |
| --- | --- | --- |
| `authors` | 文字列の配列 | 著者名。複数可。省略時は著者不明として扱う |
| `narrators` | 文字列の配列 | 話者(ナレーター)名。複数可 |
| `series` | オブジェクト | シリーズ情報。下記参照 |
| `tags` | 文字列の配列 | 任意のタグ。分類・絞り込み用 |
| `description` | 文字列 | ブックの説明文。下記の規約に従う |
| `cover` | 文字列 | 表紙画像のファイル名。省略時はサーバが自動検出(下記) |
| `addedAt` | 文字列 | ライブラリへの追加日時。省略時はサーバがファイルのmtimeで代替する |

- `series`: `name`(文字列・必須)と `sequence`(文字列・任意)を持つオブジェクト。
  `sequence` は `"1"` や `"2.5"` のように**文字列**で表す(巻の間に入る番号や
  `"0"` 始まりの前日譚を表せるようにするため、数値型にはしない)
- `cover`: **単一セグメントのファイル名**(`/` `\` および `..` を含めない)。
  省略した場合、サーバはブックフォルダ内を `cover.jpg` → `cover.png` → `cover.webp`
  の順に探して最初に見つかったものを表紙として使う
- `addedAt`: ISO日付文字列。`YYYY-MM-DD`(日付のみ)または ISO8601 日時
  (例 `2026-08-19T09:30:00+09:00`)。省略した場合、サーバはブックフォルダ内の
  ファイルの mtime を代替値として使う

#### `description` の規約(重要)

`description` は**書誌情報としての説明**を置く場所である。
**書籍本文・原文の引用を置く場所ではない。著作物の逐語転載を避けること。**

ライブラリ一覧やブック詳細で「これは何の音源か」を人が判断できるだけの
要約・紹介文にとどめる。原著のテキストをそのまま貼り付ける用途で使ってはならない。
本文(スライドのタイトル・箇条書き)は従来どおり `content.json` 側の管轄であり、
そちらは既定でローカル専用・共有/エクスポートの対象外という扱いを維持する。

## content.json

```json
{
  "kikimiru": 2,
  "slides": {
    "s1": { "title": "…", "bullets": ["…"], "note": "…" }
  }
}
```

- `kikimiru`: `1` または `2`。**v2で content.json の構造そのものに変更はない**
  (追加は deck.json 側のみ)。版番号は deck.json と揃えておくのがよい
- `bullets` は最大5点・1点40字以内を推奨(スマホ縦画面の可読性)。これらは強制ではなく
  推奨値であり、`tools/validate_deck.py` は超過を警告(WARN)として報告するのみでエラーにしない
- `note` は補足(出典・参照など)。省略可・`null` も許容

## /api/books

サーバの `GET /api/books` はライブラリ内の全ブックを次の形状で返す(v2確定版):

```json
{
  "kikimiru": 2,
  "library": "demo",
  "books": [
    {
      "id": "demo-guide-1",
      "library": "demo",
      "title": "kikimiru の手引き 1 — スキーマの読み方",
      "authors": ["サンプル・ラボ"],
      "narrators": ["合成音サンプルA"],
      "series": { "name": "kikimiru の手引き", "sequence": "1" },
      "tags": ["デモ", "スキーマ", "入門"],
      "description": "…",
      "cover": "cover.png",
      "addedAt": "2026-08-17",
      "duration": 42.0,
      "slides": 7
    }
  ]
}
```

- クエリパラメータ: `library=`(ライブラリID・省略時は先頭) / `q=`(タイトル・著者・話者・
  シリーズ・タグ・説明の部分一致検索) / `author=` `narrator=` `series=` `tag=`(完全一致絞り込み) /
  `sort=`(`title`(既定)・`added`・`duration`・`series`)
- v1のdeck(書誌フィールド無し)は `authors: []`・`series: null` 等の空値で返る(後方互換)

### GET /api/books/&lt;id&gt;

ブック単体の書誌を返す(詳細画面用・一覧と同じ形状の要素1件):

```json
{ "kikimiru": 2, "library": "demo", "book": { "id": "demo-guide-1", "…": "…" } }
```

存在しないIDは `404`。IDはファイル配信と同じ封じ込め規則(下記)で検査される。

### 集計エンドポイント

`GET /api/authors` / `/api/narrators` / `/api/series` / `/api/stats` / `/api/collections`
(いずれも `library=` を受ける)。`stats` は
`{books, duration, slides, authors, narrators, series, tags, withCover}` を返す。
`collections` は `PUT` で全置換更新(1MiB上限・既知ブックIDのみ保存)。

- `id`: **ブックフォルダ名そのもの**(`--library` 配下のディレクトリ名と一致する)。
  `deck.json` にIDフィールドは無い
- `id` に使用できる文字: 現在の実装(`server/kikimiru_server.py` の `is_safe_segment`)は
  **危険な構成要素だけを拒否するブラックリスト方式**である。次の条件を**すべて**満たす
  フォルダ名が許可される:
  1. 空文字列でない
  2. `.` で始まらない
  3. `/` `\` を含まない
  4. `..` を含まない
  5. 制御文字(コードポイント `0x20` 未満)を含まない
- 上記以外の文字種の制限は無いため、長音符「ー」・中黒「・」・「々」・絵文字を含む
  フォルダ名も使える。加えてサーバは解決後の実パスがライブラリ配下に収まっているかを
  検証しており、シンボリックリンク/ジャンクションによるライブラリ外への脱出も防いでいる
- なお、以前の版のこのドキュメントは「英数字・`.`・`_`・`-`・日本語のみを許可する
  ホワイトリスト方式(`SAFE_SEGMENT`)」と記述していたが、**その記述は実装と一致しない**。
  ホワイトリスト方式は長音符「ー」等の正当な日本語文字まで弾いてしまうため、
  実装は既にブラックリスト方式へ変更済みである(本節はその実装に合わせて修正した)
- `title`: `deck.json.title`。無ければフォルダ名にフォールバック
- `duration`: `deck.json.audio.duration`。無ければ `null`
- `slides`: `deck.json.slides` の要素数

## スキーマ検証

`tools/validate_deck.py` で、ブックフォルダが本スキーマに沿っているか機械検証できる:

```bash
python tools/validate_deck.py demo/library/demo-book
```

- 検証項目: `deck.json` の型・必須値(`kikimiru` が `1` または `2` / `audio.src` の形式 /
  `slides[].kind` の値域 / `cues` の昇順・先頭 `t == 0.0` / `cues[].slide` が `slides[].id` に
  存在するか 等)、`content.json`(存在する場合のみ)の型・必須値
- 問題なければ `VALID` を表示して終了コード `0`。問題があれば `ERROR:` 行を全てまとめて表示し
  終了コード `1`(1件見つけて終了はしない)。`bullets` の件数・文字数上限超過は `WARN:` として
  報告するのみで終了コードには影響しない

### v2フィールドの検証範囲

v2の書誌メタデータは**すべて任意**なので、**欠損はエラーにしない**。
書かれている場合のみ次を検査する:

| 対象 | 判定 | 内容 |
| --- | --- | --- |
| `authors` / `narrators` / `tags` | ERROR | 配列でない、または要素に文字列以外がある |
| `series` | ERROR | オブジェクトでない |
| `series.name` | ERROR | 文字列でない、または空文字列 |
| `series.sequence` | ERROR | 存在するのに文字列でない |
| `description` | ERROR | 文字列でない |
| `cover` | ERROR | 文字列でない/空、`/` `\` を含む、`..` を含む |
| `addedAt` | ERROR | 文字列でない |
| `addedAt` | WARN | `YYYY-MM-DD` でも ISO8601 日時でもない形式 |

- **後方互換**: `kikimiru: 1` の deck も VALID と判定する
- `kikimiru: 1` のまま v2フィールドが使われている場合は
  「v2フィールドが使われていますが kikimiru: 1 です。2 への更新を検討してください」
  という **WARN** を出す。エラーにはせず、終了コードは `0` のまま
- 型検査は版宣言に関わらず行う。つまり `kikimiru: 1` の deck に壊れた `authors` が
  入っていれば、版のWARNに加えて型のERRORも報告される

## エクスポート(予定)

- [Podcasting 2.0 JSON Chapters](https://github.com/Podcastindex-org/podcast-namespace/blob/main/docs/examples/chapters/jsonChapters.md)
  への書き出し(`startTime` + `title` + `img`)。書き出し対象は deck 側の構造+章タイトルに限る
