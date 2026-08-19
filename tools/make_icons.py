# -*- coding: utf-8 -*-
"""PWA用アイコンを生成する開発時ツール(Pillowで図形を直接描画)。

意匠の正典は web/logo.svg。ここはその 48x48 座標系を Pillow の描画系へ写したもので、
web/player.html の glyph も同じ形を写している。**3か所は必ず同時に直すこと。**
外部素材ゼロ・決定論生成。出力先: web/icon-*.png / web/apple-touch-icon.png

意匠: ヘッドフォン(聞く)がスクリーン(見る)を抱え、その面の上で本文の行と音の波形が並ぶ。
16〜32px では中身が潰れるため、ファビコン用の icon-32 だけは中身を省いた簡略版を使う。

使い方:
    python tools/make_icons.py
"""
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

# player.html のダークテーマのトークンに一致させる
BG = (19, 21, 25, 255)      # --bg:   #131519
INK = (231, 227, 218, 255)  # --ink:  #e7e3da
TINT = (110, 164, 220, 255)  # --tint: #6ea4dc

BASE = 48.0  # logo.svg の viewBox

# 線幅は確定した意匠の実測値。細さが意匠の要なので、読みやすさのために太らせない
# (小さいサイズは simple=True の簡略版で受ける)
SW_BAND = 1.35   # ヘッドバンド
SW_CUP = 1.05    # イヤーカップ
SW_RAIL = 1.5    # スクリーンのレール
SW_BODY = 0.98   # スクリーンの面
SW_STEM = 0.94   # 引き手の軸
SW_RING = 0.84   # 引き手の輪
SW_LINE = 1.12   # 本文の行
SW_BAR = 0.66    # 波形のバー

# 簡略版(ファビコン用)の線幅倍率。形は変えず太さだけ上げる
SIMPLE_BOOST = 1.9

# 波形: (中心x, 上端y, 下端y)。13本という本数も意匠の確定値
BARS = (
    (17.61, 27.35, 29.27), (18.68, 26.27, 30.35), (19.62, 27.63, 28.99),
    (20.63, 26.79, 29.83), (21.68, 25.59, 31.03), (22.76, 26.64, 29.98),
    (23.84, 27.44, 29.18), (24.87, 27.25, 29.37), (25.97, 26.46, 30.16),
    (27.03, 25.75, 30.87), (28.11, 27.09, 29.53), (29.14, 26.62, 30.00),
    (30.24, 27.31, 29.31),
)


class Pen:
    """48x48 の設計座標をキャンバス座標へ写して描く。

    Pillow の arc / rounded_rectangle は線幅を境界の**内側**へ引くため、SVGの
    中心揃えストロークと形が変わる。そこで曲線もすべて折れ線に展開し、
    line(joint="curve") + 両端の丸で描く。これで線幅の解釈がSVGと一致する。
    """

    def __init__(self, draw: ImageDraw.ImageDraw, size: int, scale: float):
        self.d = draw
        self.g = size * scale / BASE
        self.o = (size - BASE * self.g) / 2.0

    def _p(self, x: float, y: float) -> tuple[float, float]:
        return (self.o + x * self.g, self.o + y * self.g)

    def _w(self, w: float) -> int:
        return max(1, int(round(w * self.g)))

    @staticmethod
    def _arc_pts(cx, cy, r, a0, a1, step=3.0) -> list[tuple[float, float]]:
        """設計座標での円弧を折れ線にする。角度は Pillow と同じ(0度=3時・時計回り)。"""
        n = max(2, int(round(abs(a1 - a0) / step)) + 1)
        out = []
        for i in range(n):
            a = math.radians(a0 + (a1 - a0) * i / (n - 1))
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return out

    def stroke(self, pts, color, w, closed=False) -> None:
        px = self._w(w)
        cpts = [self._p(x, y) for x, y in pts]
        if closed:
            cpts = cpts + [cpts[0]]
        self.d.line(cpts, fill=color, width=px, joint="curve")
        # joint="curve" は継ぎ目だけを丸める。開いた線の両端は自前で丸める
        ends = () if closed else (cpts[0], cpts[-1])
        r = px / 2.0
        for cx, cy in ends:
            self.d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)

    def line(self, x0, y0, x1, y1, color, w) -> None:
        self.stroke([(x0, y0), (x1, y1)], color, w)

    def rrect(self, x0, y0, x1, y1, r, color, w) -> None:
        pts = [(x0 + r, y0)]
        pts += self._arc_pts(x1 - r, y0 + r, r, 270, 360)
        pts += self._arc_pts(x1 - r, y1 - r, r, 0, 90)
        pts += self._arc_pts(x0 + r, y1 - r, r, 90, 180)
        pts += self._arc_pts(x0 + r, y0 + r, r, 180, 270)
        self.stroke(pts, color, w, closed=True)

    def circle(self, cx, cy, r, color, w) -> None:
        self.stroke(self._arc_pts(cx, cy, r, 0, 360), color, w, closed=True)


def draw_mark(draw: ImageDraw.ImageDraw, size: int, scale: float,
              ink=INK, tint=TINT, simple: bool = False) -> None:
    """マークを中央に描く。

    simple=True はファビコン用の簡略版で、中身(行と波形)を省き線を太らせる。
    16〜32pxでは元の線幅だと形が消えるため、位置と輪郭は保ったまま太さだけ上げる。
    """
    p = Pen(draw, size, scale)
    k = SIMPLE_BOOST if simple else 1.0

    # ヘッドフォン: ヘッドバンド(縦棒 - 半円 - 縦棒の一筆)とイヤーカップ
    band = [(10.32, 29.5)] + p._arc_pts(23.9, 21.67, 13.58, 180, 360) + [(37.48, 29.5)]
    p.stroke(band, ink, SW_BAND * k)
    p.rrect(7.8, 24.9, 12.55, 34.1, 2.0, ink, SW_CUP * k)
    p.rrect(35.25, 24.9, 40.0, 34.1, 2.0, ink, SW_CUP * k)

    # スクリーン: レール・面(上辺なし・下角のみ丸め)・引き手
    p.line(13.55, 17.72, 34.27, 17.72, tint, SW_RAIL * k)
    body = ([(14.49, 18.2)] + p._arc_pts(16.29, 32.5, 1.8, 180, 90)
            + p._arc_pts(31.5, 32.5, 1.8, 90, 0) + [(33.3, 18.2)])
    p.stroke(body, tint, SW_BODY * k)
    p.line(23.9, 34.3, 23.9, 36.84, tint, SW_STEM * k)
    p.circle(23.9, 37.99, 1.15, tint, SW_RING * k)

    if simple:
        return

    # 面の上の中身: 本文の行と音の波形
    p.line(17.76, 22.64, 30.10, 22.64, ink, SW_LINE)
    for x, y0, y1 in BARS:
        p.line(x, y0, x, y1, ink, SW_BAR)


def make_icon(size: int, scale: float, simple: bool = False) -> Image.Image:
    img = Image.new("RGBA", (size, size), BG)
    draw_mark(ImageDraw.Draw(img), size, scale, simple=simple)
    return img


def main() -> None:
    # 通常アイコン: scale 1.0 = 確定意匠の余白の取り方そのまま(48の枠にマーク69%)
    make_icon(192, 1.0).save(WEB / "icon-192.png")
    make_icon(512, 1.0).save(WEB / "icon-512.png")
    # maskable: OS側で円形等に切り抜かれるため、セーフゾーン(中央80%の円)に収める。
    # 0.95 での実測は最遠191px / セーフ半径205px(生成画像の画素走査で確認)
    make_icon(512, 0.95).save(WEB / "icon-maskable-512.png")
    # apple-touch-icon: 非透過・180px(iOSはtransparent非推奨)。iOS側の角丸ぶん少し内側へ
    make_icon(180, 0.95).save(WEB / "apple-touch-icon.png")
    # ファビコン: 16〜20pxまで縮むので中身を省いた簡略版にする
    make_icon(32, 1.0, simple=True).save(WEB / "icon-32.png")
    for name in ("icon-192.png", "icon-512.png", "icon-maskable-512.png",
                 "apple-touch-icon.png", "icon-32.png"):
        print(f"生成: {WEB / name}")


if __name__ == "__main__":
    main()
