# -*- coding: utf-8 -*-
"""PWA用アイコンを生成する開発時ツール(Pillowで図形を直接描画)。

意匠は web/player.html のプレースホルダグリフ(スライド+音波)と同一モチーフ。
外部素材ゼロ・決定論生成。出力先: web/icon-*.png / web/apple-touch-icon.png

使い方:
    python tools/make_icons.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

BG = (14, 15, 17, 255)        # --bg: #0e0f11
TINT = (92, 174, 125, 255)    # --tint: #5cae7d
INK = (242, 244, 246, 255)    # --ink: #f2f4f6


def draw_glyph(draw: ImageDraw.ImageDraw, size: int, scale: float, color) -> None:
    """スライド+音波のグリフを中央に描く。scale はキャンバスに対する占有率。"""
    # 基準座標系はグリフのviewBox(44x44)。中央配置でスケールする
    g = size * scale / 44.0
    ox = (size - 44 * g) / 2.0
    oy = (size - 44 * g) / 2.0
    w = max(2, int(round(2.2 * g)))  # 線幅

    def xy(x, y):
        return (ox + x * g, oy + y * g)

    # スライド面(角丸矩形)
    draw.rounded_rectangle([xy(6, 10), xy(28, 27)], radius=2.5 * g, outline=color, width=w)
    # スライド内の行
    draw.line([xy(11, 16), xy(23, 16)], fill=color, width=w)
    draw.line([xy(11, 21), xy(19, 21)], fill=color, width=w)
    # 音波(縦棒2本)
    draw.line([xy(33, 15), xy(33, 29)], fill=color, width=w)
    draw.line([xy(38, 19), xy(38, 25)], fill=color, width=w)


def make_icon(size: int, scale: float, bg, fg) -> Image.Image:
    img = Image.new("RGBA", (size, size), bg)
    draw_glyph(ImageDraw.Draw(img), size, scale, fg)
    return img


def main() -> None:
    # 通常アイコン: グリフ大きめ・アクセント色
    make_icon(192, 0.66, BG, TINT).save(WEB / "icon-192.png")
    make_icon(512, 0.66, BG, TINT).save(WEB / "icon-512.png")
    # maskable: OS側で円形等に切り抜かれるため、セーフゾーン(中央80%)に収める
    make_icon(512, 0.52, BG, TINT).save(WEB / "icon-maskable-512.png")
    # apple-touch-icon: 非透過・180px(iOSはtransparent非推奨)
    make_icon(180, 0.62, BG, TINT).save(WEB / "apple-touch-icon.png")
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png", "apple-touch-icon.png"):
        print(f"生成: {WEB / name}")


if __name__ == "__main__":
    main()
