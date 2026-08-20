<img src="web/icon-192.png" width="76" alt="">

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
<img src="docs/screenshots/player.png" width="49%" alt="Player — slide view synchronized to playback, with the chapter list marking the current slide">
<img src="docs/screenshots/book-detail.png" width="49%" alt="Book detail — cover, metadata, tags, description and play actions">
</p>
<img src="docs/screenshots/mobile-home.png" width="32%" alt="Home on a phone-sized viewport with bottom tab bar">

## Player

- **Two views, one player.** The default view shows the cover (square, on a blurred
  backdrop); a segmented tab at the top switches to the 16:9 slide view. Your choice is
  remembered per device.
- **Chapter list.** Scroll up and the chapters — number, timestamp, heading and key points
  from `content.json` — stack vertically; tapping one seeks the audio. While you browse,
  the slide stays pinned on top with full controls overlaid, and the list follows the
  current chapter during playback (never while you are scrolling it yourself).
- **Fullscreen.** The ⛶ button — or simply rotating a phone to landscape — switches to a
  fullscreen slide view with tap-to-show controls that auto-hide during playback.
  Double-tap the left/right side to skip ±10s. Rotating back restores the portrait layout.
- **Desktop layout.** On wide screens the player becomes two columns: media with hover
  controls on the left (click the slide to play/pause), an independently scrolling chapter
  panel on the right.
- **Playback controls.** ±30s skip, playback speed and a sleep timer chosen from popup
  menus, chapter markers on the seek bar, remaining time at the current speed, and Media
  Session integration for lock-screen control.
- **Mini player.** The persistent dock stays live-synced to playback (progress, remaining
  time, play/pause in place), and reopening the player never interrupts the audio.
- **Chapter export.** Book detail can export Podcasting 2.0 JSON Chapters built from the
  structure side only (`deck.json` timing + chapter titles — see
  [docs/SCHEMA.md](docs/SCHEMA.md)).

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
working read-only when the server is unreachable (last-fetched snapshot).

**Offline listening (per-book).** Book detail has a "save offline" action that stores the
whole book (audio, timing, text, cover and slide images) in the browser's cache. Saved
books keep playing — including seeking, chapters and chapter export — with no server at
all; the service worker synthesizes HTTP range responses from the stored audio. Downloads
show deterministic progress and resume over flaky connections. Honest caveats:

- Offline features require HTTPS (or `127.0.0.1`) — on plain-http LAN setups the service
  worker never activates. See [docs/DEPLOY.md](docs/DEPLOY.md) for easy TLS options.
- Saved books are **not guaranteed to persist**: browsers may evict stored data under
  pressure, and an uninstalled Safari tab can drop it after days of non-use. Installing to
  the home screen makes storage much more durable; the UI tells you this once after your
  first save.
- iOS does not let web apps keep playing reliably from the lock screen after long pauses —
  this is a WebKit platform limitation, not something a PWA can fix.

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
detail), a fully rebuilt player (cover/slide views, chapter list, rotation fullscreen,
desktop two-column layout, live-synced mini player, chapter export), password auth,
cross-device progress sync, PWA, Docker/GHCR. Per-book offline saving has landed
(queued downloads, offline playback with seeking, storage management, bulk save).
Roadmap: multi-user, native mobile apps.

## License

[AGPL-3.0](LICENSE). Contributions require the lightweight CLA described in [CONTRIBUTING.md](CONTRIBUTING.md).

---

<img src="web/icon-192.png" width="76" alt="">

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
<img src="docs/screenshots/player.png" width="49%" alt="プレイヤー — 再生位置に同期したスライド表示と、現在位置を示すチャプター一覧">
<img src="docs/screenshots/book-detail.png" width="49%" alt="ブック詳細 — カバー・書誌・タグ・説明文と再生操作">
</p>
<img src="docs/screenshots/mobile-home.png" width="32%" alt="モバイル幅のホーム(下部タブバー)">

## プレイヤー

- **1つのプレイヤーに2つの表示。** 既定はぼかし背景の上の正方形の表紙。上部のタブで
  16:9のスライド表示へ切り替えられ、選択は端末ごとに記憶されます
- **チャプター一覧。** 下から上へスクロールすると、章(番号・時刻・見出し・`content.json`
  の要点)が縦に積み上がり、タップでその時間へシークします。閲覧中もスライドは上部に
  固定され、フル操作が面内に出ます。再生が進むと一覧は現在の章へ自動で追従します
  (自分でスクロールしている間は動きません)
- **全画面。** ⛶ボタン、またはスマホを横向きにするだけで全画面のスライド表示へ。
  コントロールはタップで表示・再生中は自動で隠れ、左右のダブルタップで±10秒送れます。
  縦に戻すと元のレイアウトへ復帰します
- **デスクトップ。** 広い画面では2カラムになり、左はホバー操作つきのメディア
  (スライド面のクリックで再生/一時停止)、右は独立してスクロールするチャプター欄です
- **再生操作。** ±30秒スキップ、ポップアップから選ぶ再生速度とスリープタイマー、
  シークバー上の章の目盛、再生速度で換算した残り時間、ロック画面操作(Media Session)
- **ミニプレイヤー。** 常駐ドックは再生にライブ同期し(進捗・残り時間・その場での
  再生/一時停止)、プレイヤーを開き直しても音声は途切れません
- **チャプター書き出し。** ブック詳細から Podcasting 2.0 JSON Chapters 形式で
  書き出せます。内容は構造側(`deck.json` の時刻+章タイトル)のみです
  ([docs/SCHEMA.md](docs/SCHEMA.md))

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

**ブック単位のオフライン保存。** ブック詳細の「オフライン保存」で、音声・タイミング・
本文・表紙・画像スライドの一式をブラウザに保存できます。保存済みブックはサーバなしでも
再生・シーク・チャプター・書き出しまで動きます(Service Worker が保存済み音声から
Range応答を合成します)。ダウンロードは確定%表示で、不安定な回線でも途中から再開します。
正直な注意点:

- オフライン機能は HTTPS(または 127.0.0.1)でのみ動きます。LANのhttp運用では
  Service Worker は動きません([docs/DEPLOY.md](docs/DEPLOY.md) の簡単なTLS手引きを参照)
- 保存の**永続は保証されません**。ブラウザは容量逼迫時に保存データを消すことがあり、
  ホーム画面に追加していないSafariのタブ利用では数日の未使用で消えることがあります。
  ホーム画面への追加で大幅に保たれやすくなります(初回保存後にUIが一度だけ案内します)
- iOSでは長時間停止後のロック画面からの再生再開が失敗することがあります。これは
  WebKitのプラットフォーム制約で、PWA側では解決できません

## セキュリティ

- **パスワード認証が必須です**(単一ユーザー・セッションCookie)。`--set-password` を
  実行するまでサーバは起動しません。ログイン試行はIP毎にレート制限されます
- 再生進捗はサーバ経由で端末間同期されます(保存先: `--state-dir`、既定 `~/.kikimiru`)
- **通信は平文HTTPです。** 信頼できないネットワークでは必ずTLS終端を前置してください
  (Tailscale Serve・リバースプロキシ — [docs/DEPLOY.md](docs/DEPLOY.md))。Dockerでは
  ホスト側バインドを既定の `127.0.0.1:8484` のままにするのが安全側です

## ステータス

Phase 1 完了 — ライブラリ体験(複数ライブラリ・検索・シリーズ/著者/タグ・ブック詳細)、
プレイヤーの全面再構築(表紙/スライドの2表示・チャプター一覧・回転連動の全画面・
デスクトップ2カラム・ライブ同期ミニプレイヤー・チャプター書き出し)、パスワード認証、
端末間進捗同期、PWA、Docker/GHCR。ブック単位のオフライン保存が入りました
(キュー式ダウンロード・オフライン再生/シーク・管理画面・一括保存)。
ロードマップ: マルチユーザー・ネイティブアプリ。
