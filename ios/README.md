# kikimiru iOS アプリ

自己ホストの kikimiru サーバに接続する SwiftUI ネイティブクライアント。
外部パッケージ依存ゼロ(OS標準SDKのみ)。サーバは無変更で、既存APIだけを使う。

現在の段階: **G0(骨格)** — ログイン・書棚一覧・詳細表示まで。
再生(ロック画面統合)は G1 で実装する。

## Mac mini でのビルド手順

前提: Xcode 16 以降 / macOS 14 以降。

1. リポジトリを取得して開く:

   ```
   git clone https://github.com/cosara22/kikimiru.git
   open kikimiru/ios/Kikimiru.xcodeproj
   ```

2. 署名を設定する(初回のみ): プロジェクト設定 → TARGETS Kikimiru →
   Signing & Capabilities → Team に自分の Apple ID を選ぶ
   (無料の Personal Team でよい。Bundle Identifier が衝突する場合は
   `app.kikimiru.ios` を適当な独自値に変える)。
3. 実行先を選んで Run:
   - **シミュレータ**: そのまま実行できる
   - **実機 iPhone**: USB接続 → iPhone側で「設定 > プライバシーとセキュリティ >
     デベロッパモード」を有効化 → 初回起動時に「設定 > 一般 > VPNとデバイス管理」で
     開発者を信頼する(無料Apple IDの署名は7日で失効するため都度Runし直す)

## 動作確認用サーバ

Mac mini 側でデモサーバを起動する(Python 3.9+、依存なし):

```
cd kikimiru
python3 server/kikimiru_server.py
```

- シミュレータから: アプリのサーバURLに `http://127.0.0.1:8000`(起動ログのポート)
- 実機から: Mac のLAN IPで起動し直す:
  `python3 server/kikimiru_server.py --bind 0.0.0.0 --allow-host 192.168.x.x`
  アプリのサーバURLに `http://192.168.x.x:8000`
  (`--allow-host` を付けないと 403 になる。DNSリバインディング対策の仕様)

## G0 受け入れチェックリスト

- [ ] シミュレータでサーバURL入力 → パスワードでログインできる
- [ ] 書棚にデモブック4冊がカバー付きで表示される
- [ ] 検索でタイトル絞り込みができる
- [ ] 詳細画面にカバー・著者・スライド枚数・時間が表示される
- [ ] アプリを終了して再起動しても、ログインなしで書棚に入れる(Cookie永続)

## プロジェクト構成

- `Kikimiru.xcodeproj` — Xcode 16 の同期フォルダ形式。`Kikimiru/` 配下への
  ファイル追加は pbxproj の編集なしで自動的にターゲットへ入る
- `Kikimiru/` — Swift ソース(App / AppState / Models / APIClient / Keychain / Views)
- `Kikimiru-Info.plist` — バックグラウンド音声(UIBackgroundModes: audio)と
  LAN内平文HTTP許可(NSAllowsLocalNetworking)のみ

## プロジェクトが開けない場合(フォールバック)

`project.pbxproj` は手書きのため、Xcodeのバージョン差で開けない可能性がある。
その場合は新規作成で差し替える:

1. Xcode → Create New Project → iOS App / 名前 `Kikimiru` / SwiftUI / 保存先は
   リポジトリ外の一時場所
2. 生成された `Kikimiru.xcodeproj` でこのディレクトリの同名フォルダを置き換える
3. 生成されたソースフォルダ(`ContentView.swift` 等)を削除し、この `Kikimiru/`
   フォルダをプロジェクトナビゲータへドラッグして追加する
4. TARGETS → Build Settings → Info.plist File に `Kikimiru-Info.plist` を設定、
   Deployment Target を iOS 17.0 にする
5. その pbxproj をコミットして共有する
