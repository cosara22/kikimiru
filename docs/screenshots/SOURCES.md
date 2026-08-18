# docs/screenshots/ 出所台帳

README掲載用のUIスクリーンショット。すべて本リポジトリの同梱デモライブラリ
(`demo/library/` — 音声・表紙・文言とも `demo/make_demo.py` による自作生成素材のみ)を
表示した kikimiru 自身の画面を、Playwright(Chromium)で撮影したもの。
外部の書籍・書影・私的コンテンツは一切含まれない。

| ファイル | 内容 | 撮影 |
|---|---|---|
| home.png | ホーム画面(1440×900・再生途中の進捗を注入した状態) | 2026-08-19 |
| player.png | プレイヤー画面(1440×900・demo-guide-1) | 2026-08-19 |
| book-detail.png | ブック詳細画面(1440×900・demo-guide-1) | 2026-08-19 |
| mobile-home.png | ホーム画面(390×844・モバイル幅) | 2026-08-19 |

再撮影する場合: サーバをデモライブラリで起動し、各画面を同解像度で撮り直す
(進捗表示は localStorage に `kikimiru.pos.demo/<id>` を注入して再現する)。
