# kikimiru 配備の手引き(Docker・LAN公開・HTTPS)

self-host 運用の実務をまとめる。前提: kikimiru は**パスワード認証必須**・**TLSは本体に持たない**。
信頼できないネットワークで使う場合は、必ず本書のTLS節に従うこと。

## 1. Docker で動かす

### 初回セットアップ(パスワード設定)

認証情報は `/state` ボリュームに保存される。初回に一度だけ対話設定する:

```bash
docker compose run --rm kikimiru --set-password
docker compose up -d
```

compose を使わない場合:

```bash
docker volume create kikimiru-state
docker run --rm -it -v kikimiru-state:/state ghcr.io/cosara22/kikimiru --set-password
docker run -d -p 127.0.0.1:8484:8484 \
  -v kikimiru-state:/state \
  -v /path/to/library:/library:rw \
  ghcr.io/cosara22/kikimiru --library 本棚=/library
```

### ボリューム設計

| マウント | 用途 | 備考 |
|---|---|---|
| `/state` | 認証(auth.json)・セッション・進捗 | 必須。消すと全端末で再ログイン+進捗消失 |
| `/library` | ブックフォルダ群(1フォルダ=1ブック) | `--library 名前=/library` を複数並べれば複数ライブラリ |

- ライブラリを **読み取り専用(`:ro`)** でマウントする運用も可能。その場合
  コレクション保存(`PUT /api/collections`)だけが500で機能縮退する(再生・進捗同期は無傷。
  進捗は `/state` 側に書くため)
- コンテナは非root(UID 10001)で動く。NAS等で書き込みに失敗する場合は
  compose の `user:` でホスト側の所有者に合わせる

### 公開範囲はホスト側で絞る

コンテナ内はDockerの作法どおり `0.0.0.0` で待ち受ける。**公開範囲の制御はホスト側の
ポートバインドで行う**のが既定方針:

- `127.0.0.1:8484:8484` — 同一マシンのみ(既定・安全側)
- `8484:8484` — LANへ公開(認証はあるが**平文HTTP**。下のTLS節を必ず読む)

## 2. --allow-host(Host検証の追加許可)

kikimiru はDNSリバインディング対策として Host/Origin ヘッダを検証する。既定の許可は
`127.0.0.1` / `localhost` / bind先IP のみのため、次の場合は `--allow-host` が必要:

- ポートリマップ(`-p 9000:8484`)→ `--allow-host localhost:9000`
- ホスト名やmDNS名でアクセス(`http://nas.local:8484`)→ `--allow-host nas.local`
- リバースプロキシ経由(`https://kikimiru.example.com`)→ `--allow-host kikimiru.example.com`

## 3. HTTPS(TLS終端)

平文HTTPではパスワードもセッションCookieも盗聴され得る。LANの外はもちろん、
信頼しきれないLANでも以下のいずれかでTLSを終端すること。
なお **PWA機能(ホーム画面インストール・オフライン表示)はHTTPS(または127.0.0.1)でのみ動く**。

### 3a. Tailscale Serve(私的利用で最も簡単)

Tailscale導入済みなら1コマンドで自分のtailnet内にHTTPSを立てられる:

```bash
tailscale serve --bg 8484
# 表示されたURL(https://<machine>.<tailnet>.ts.net)でアクセスする。
# kikimiru側: --allow-host <machine>.<tailnet>.ts.net を付けて起動
```

### 3b. Caddy リバースプロキシ(compose例)

```yaml
services:
  kikimiru:
    image: ghcr.io/cosara22/kikimiru:latest
    volumes:
      - kikimiru-state:/state
      - ./library:/library:rw
    command: ["--library", "本棚=/library", "--allow-host", "kikimiru.example.com"]
    # プロキシからのみ到達させるため ports は公開しない
  caddy:
    image: caddy:2
    ports: ["443:443", "80:80"]
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - caddy-data:/data
volumes:
  kikimiru-state:
  caddy-data:
```

Caddyfile:

```
kikimiru.example.com {
    reverse_proxy kikimiru:8484
}
```

- 公的なドメインが無いLAN内では `tls internal`(Caddyの内部CA)も使える
- プロキシ配下では全クライアントが同一IPに見えるため、ログイン失敗の
  IPスロットルが共有される点に注意(誤ロック時はサーバ再起動でリセット)

## 4. 素のPython運用(Dockerなし)

```bash
python server/kikimiru_server.py --set-password   # 初回のみ
python server/kikimiru_server.py --library /path/to/library
```

- 状態は既定で `~/.kikimiru` に保存される(`--state-dir` で変更可)
- systemd 化する場合、SIGTERMで即時終了する(ハンドラ実装済み)

## 5. セキュリティ境界の整理

| 脅威 | 対策 | 担当 |
|---|---|---|
| LAN上の他者の直接アクセス | パスワード認証+セッションCookie | 本体 |
| 総当たり | IP毎の失敗スロットル(指数バックオフ) | 本体 |
| DNSリバインディング・クロスサイト攻撃 | Host/Origin検証+CORS無効+preflight不成立 | 本体 |
| 盗聴(平文HTTP) | **本体では守れない** | TLS終端(本書§3) |
| インターネット公開 | 非推奨。公開するならTLS+十分に強いパスワードが最低条件 | 運用 |
