#!/usr/bin/env python3
"""產生 PWA／iPhone 主畫面圖示（site/icons/）。需要 Pillow：pip install pillow（只在改圖示時用）"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "site" / "icons"
TOP, BOTTOM = (200, 50, 40), (150, 24, 18)  # 品牌紅，上亮下暗


def draw(size: int, safe: float) -> Image.Image:
    """safe＝圖案占畫面的比例（maskable 圖示要留更多邊）。先 4 倍大畫再縮小，線條才平滑。"""
    S = size * 4
    im = Image.new("RGB", (S, S))
    d = ImageDraw.Draw(im)
    for y in range(S):  # 直向漸層底色（iOS 會自己切圓角，所以鋪滿不留透明）
        t = y / (S - 1)
        d.line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    m = S * (1 - safe) / 2
    box = S - 2 * m
    p = lambda x, y: (m + box * x, m + box * y)  # noqa: E731
    w = round(box * 0.085)
    # 淡淡的底線：像 K 線圖的基準
    d.line([p(0.10, 0.84), p(0.90, 0.84)], fill=(236, 170, 160), width=max(1, w // 3))
    # 走勢折線
    pts = [p(0.12, 0.70), p(0.36, 0.44), p(0.55, 0.58), p(0.86, 0.22)]
    d.line(pts, fill="white", width=w, joint="curve")
    for x, y in pts[:-1]:
        r = w / 2
        d.ellipse([x - r, y - r, x + r, y + r], fill="white")
    # 終點：亮點（盯盤的「現在」）
    x, y = pts[-1]
    r = w * 1.25
    d.ellipse([x - r, y - r, x + r, y + r], fill="white")
    r2 = w * 0.55
    d.ellipse([x - r2, y - r2, x + r2, y + r2], fill=BOTTOM)
    return im.resize((size, size), Image.LANCZOS)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    draw(180, 0.74).save(OUT / "apple-touch-icon.png", optimize=True)
    draw(192, 0.74).save(OUT / "icon-192.png", optimize=True)
    draw(512, 0.74).save(OUT / "icon-512.png", optimize=True)
    draw(512, 0.58).save(OUT / "icon-maskable-512.png", optimize=True)
    draw(32, 0.80).save(OUT / "favicon-32.png", optimize=True)
    print("圖示完成：", ", ".join(sorted(p.name for p in OUT.glob("*.png"))))


if __name__ == "__main__":
    main()
