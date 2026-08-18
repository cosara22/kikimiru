# web/ 出所台帳

配信用の画像素材。すべて `tools/make_icons.py` が Pillow の図形描画のみで生成した
自作素材(外部素材・フォントラスタライズ不使用・決定論生成)。
意匠は `player.html` 内のプレースホルダグリフ(スライド+音波)と同一モチーフ。

| ファイル | 内容 |
|---|---|
| icon-192.png / icon-512.png | PWAアイコン(通常) |
| icon-maskable-512.png | PWAアイコン(maskable・セーフゾーン80%) |
| apple-touch-icon.png | iOSホーム画面用(180px・非透過) |

再生成: `python tools/make_icons.py`
