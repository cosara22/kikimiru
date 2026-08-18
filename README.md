# kikimiru <sub>(working title)</sub>

**Self-hosted audio player with synchronized slides.**
Listen and watch: audio playback drives slide transitions, and tapping a slide seeks the audio.

For your own lectures, recordings, narrated tutorials, language practice, and public-domain audio.

- **No content included.** Bring your own audio and slide definitions (see [docs/SCHEMA.md](docs/SCHEMA.md)).
- **Self-host only.** A small Python server (stdlib only, HTTP Range support for mobile Safari) serves your local library. Nothing leaves your machine.
- **Structure/content separation by design.** Timing data (`deck.json`) and slide text (`content.json`) are separate files; sharing and export features only ever operate on the structure side.

## Screenshots

*All screenshots show the bundled demo library — every cover, title and audio file is generated from scratch by [`demo/make_demo.py`](demo/make_demo.py).*

![Home — resume card, recently added, series and browse](docs/screenshots/home.png)

<p>
<img src="docs/screenshots/player.png" width="49%" alt="Player — synchronized slide view with cue-marked scrubber and slide filmstrip">
<img src="docs/screenshots/book-detail.png" width="49%" alt="Book detail — cover, metadata, tags, description and play actions">
</p>
<img src="docs/screenshots/mobile-home.png" width="32%" alt="Home on a phone-sized viewport with bottom tab bar">

## Quickstart

Set a password once, then start the server (the bundled demo library is served by default —
no ffmpeg needed):

```bash
python server/kikimiru_server.py --set-password   # first run only
python server/kikimiru_server.py                  # serves the demo at http://127.0.0.1:8484/
```

Open http://127.0.0.1:8484/, log in, and pick a demo book. To use your own library:

```bash
python server/kikimiru_server.py --library /path/to/your/library
```

One folder per book: `audio.mp3` + `deck.json` (+ optional `content.json`).

### Docker

```bash
docker volume create kikimiru-state
docker run --rm -it -v kikimiru-state:/state ghcr.io/cosara22/kikimiru --set-password
docker run -d -p 127.0.0.1:8484:8484 -v kikimiru-state:/state \
  -v /path/to/library:/library ghcr.io/cosara22/kikimiru --library books=/library
```

See [docker-compose.yml](docker-compose.yml) for the recommended hardened setup and
[docs/DEPLOY.md](docs/DEPLOY.md) for volumes, LAN exposure, reverse proxies and HTTPS.

## Install as an app (PWA)

kikimiru ships a web app manifest and a service worker: you can install it to your home
screen / desktop, it launches full-screen with its own icon, and the library views keep
working read-only when the server is unreachable (last-fetched snapshot). Two honest caveats:

- Offline features require HTTPS (or `127.0.0.1`) — on plain-http LAN setups the service
  worker never activates. See [docs/DEPLOY.md](docs/DEPLOY.md) for easy TLS options.
- iOS does not let web apps keep playing reliably from the lock screen after long pauses —
  this is a WebKit platform limitation, not something a PWA can fix. Audio itself is never
  cached offline.

## Security

- **Password authentication is required** (single user, session cookie). The server refuses
  to start until you run `--set-password`. Login attempts are rate-limited per IP.
- Playback progress syncs across your devices through the server (stored under
  `--state-dir`, default `~/.kikimiru`).
- **Transport is plain HTTP** — on untrusted networks put a TLS terminator in front
  (Tailscale Serve or a reverse proxy; see [docs/DEPLOY.md](docs/DEPLOY.md)). With Docker,
  keep the default host-side bind `127.0.0.1:8484` unless you know what you are exposing.

## Status

Phase 1 complete — library experience (multi-library, search, series/authors/tags, book
detail, immersive player), password auth, cross-device progress sync, PWA, Docker/GHCR.
Roadmap: offline book downloads, multi-user, native mobile apps.

## License

[AGPL-3.0](LICENSE). Contributions require the lightweight CLA described in [CONTRIBUTING.md](CONTRIBUTING.md).

---

# kikimiru(仮称)

**スライド同期機能付きの self-host 音声プレイヤー。**
音声の再生に同期してスライドが切り替わり、スライドの一覧をタップすると音声がシークします。

自作の講義録音・ナレーション付き教材・語学練習・パブリックドメイン音源のために。

- **コンテンツは同梱しません。** 音声とスライド定義はご自身で用意します([docs/SCHEMA.md](docs/SCHEMA.md))
- **self-host 専用。** Python 標準ライブラリのみの小さなサーバがローカルのライブラリを配信します(モバイル Safari のシークに必要な HTTP Range 対応)。データは手元から出ません
- **構造と本文の分離。** タイミング(`deck.json`)と本文(`content.json`)は別ファイルで、共有・書き出し機能が扱うのは常に構造側だけです

## スクリーンショット

*すべて同梱デモライブラリの画面です — 表紙・タイトル・音声はいずれも [`demo/make_demo.py`](demo/make_demo.py) がゼロから生成した自作素材です。*

![ホーム — 続きから・最近追加・シリーズ・ブラウズ](docs/screenshots/home.png)

<p>
<img src="docs/screenshots/player.png" width="49%" alt="プレイヤー — cue目盛付きシークバーとスライド一覧を備えた同期表示">
<img src="docs/screenshots/book-detail.png" width="49%" alt="ブック詳細 — カバー・書誌・タグ・説明文と再生操作">
</p>
<img src="docs/screenshots/mobile-home.png" width="32%" alt="モバイル幅のホーム(下部タブバー)">

## 使い方

最初に一度パスワードを設定し、サーバを起動します(既定では同梱デモを配信。ffmpeg 不要):

```bash
python server/kikimiru_server.py --set-password   # 初回のみ
python server/kikimiru_server.py                  # http://127.0.0.1:8484/ でデモを配信
```

http://127.0.0.1:8484/ を開いてログインし、デモブックを選びます。
自分のライブラリを使う場合は `--library <dir>` を指定します(1フォルダ=1ブック)。

### Docker で動かす

```bash
docker volume create kikimiru-state
docker run --rm -it -v kikimiru-state:/state ghcr.io/cosara22/kikimiru --set-password
docker run -d -p 127.0.0.1:8484:8484 -v kikimiru-state:/state \
  -v /path/to/library:/library ghcr.io/cosara22/kikimiru --library 本棚=/library
```

推奨構成は [docker-compose.yml](docker-compose.yml)、ボリューム設計・LAN公開・
リバースプロキシ・HTTPSは [docs/DEPLOY.md](docs/DEPLOY.md) を参照してください。

## アプリとしてインストール(PWA)

manifest と Service Worker を同梱しており、ホーム画面/デスクトップへのインストール・
アイコン付き全画面起動・サーバ停止時の読み取り専用表示(最後に取得した書棚)に対応します。
正直な注意点が2つ:

- オフライン機能は HTTPS(または 127.0.0.1)でのみ動きます。LANのhttp運用では
  Service Worker は動きません([docs/DEPLOY.md](docs/DEPLOY.md) の簡単なTLS手引きを参照)
- iOSでは長時間停止後のロック画面からの再生再開が失敗することがあります。これは
  WebKitのプラットフォーム制約で、PWA側では解決できません。音声そのものは
  オフライン保存しません

## セキュリティ

- **パスワード認証が必須です**(単一ユーザー・セッションCookie)。`--set-password` を
  実行するまでサーバは起動しません。ログイン試行はIP毎にレート制限されます
- 再生進捗はサーバ経由で端末間同期されます(保存先: `--state-dir`、既定 `~/.kikimiru`)
- **通信は平文HTTPです。** 信頼できないネットワークでは必ずTLS終端を前置してください
  (Tailscale Serve・リバースプロキシ — [docs/DEPLOY.md](docs/DEPLOY.md))。Dockerでは
  ホスト側バインドを既定の `127.0.0.1:8484` のままにするのが安全側です

## ステータス

Phase 1 完了 — ライブラリ体験(複数ライブラリ・検索・シリーズ/著者/タグ・ブック詳細・
没入プレイヤー)、パスワード認証、端末間進捗同期、PWA、Docker/GHCR。
ロードマップ: ブック単位のオフライン保存・マルチユーザー・ネイティブアプリ。
