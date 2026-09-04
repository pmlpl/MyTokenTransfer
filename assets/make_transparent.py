# -*- coding: utf-8 -*-
"""MTT logo 透明底处理：浅色背景 -> alpha 透明，边缘去色晕，输出 PNG + 棋盘格预览。"""
import sys
from PIL import Image, ImageDraw

SRC = r"C:\Users\MOM\Desktop\学习项目\MyTokenTransfer\assets\mtt_logo_src.jpg"
OUT = r"C:\Users\MOM\Desktop\学习项目\MyTokenTransfer\assets\mtt-logo.png"
PREVIEW = r"C:\Users\MOM\Desktop\学习项目\MyTokenTransfer\assets\mtt-logo_preview.png"

T0 = 45   # 距离低于此 -> 完全透明
T1 = 110  # 距离高于此 -> 完全不透明

img = Image.open(SRC).convert("RGBA")
w, h = img.size
print("size:", w, h, "mode:", img.mode)

px = img.load()

# 1) 采样四角 24x24 区域的中位色作为背景色
def corner_median(x0, y0, size=24):
    r = g = b = 0
    samples = []
    for y in range(y0, y0 + size):
        for x in range(x0, x0 + size):
            samples.append(px[x, y][:3])
    # 简化：按通道取中位数
    rs = sorted(s[0] for s in samples)
    gs = sorted(s[1] for s in samples)
    bs = sorted(s[2] for s in samples)
    n = len(samples)
    return (rs[n // 2], gs[n // 2], bs[n // 2])

corners = [corner_median(0, 0), corner_median(w - 25, 0), corner_median(0, h - 25), corner_median(w - 25, h - 25)]
bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))
print("corners:", corners)
print("background (avg):", bg)

# 2) 逐像素计算距离 -> alpha + 去色晕
import math

B0, B1, B2 = bg
out = Image.new("RGBA", (w, h))
opx = out.load()

stats = {"transparent": 0, "opaque": 0, "partial": 0}

for y in range(h):
    for x in range(w):
        r, g, b, a0 = px[x, y]
        # 欧氏距离（RGB 空间）
        d = math.sqrt((r - B0) ** 2 + (g - B1) ** 2 + (b - B2) ** 2)
        if d <= T0:
            a = 0
            stats["transparent"] += 1
            opx[x, y] = (r, g, b, 0)
        elif d >= T1:
            a = 255
            stats["opaque"] += 1
            opx[x, y] = (r, g, b, 255)
        else:
            an = (d - T0) / (T1 - T0)  # 0..1
            a = int(round(an * 255))
            stats["partial"] += 1
            # 去色晕：假设边缘像素 = 背景*（1-an）+ 前景*an
            if an > 0.02:
                nr = int(round((r - B0 * (1 - an)) / an))
                ng = int(round((g - B1 * (1 - an)) / an))
                nb = int(round((b - B2 * (1 - an)) / an))
                nr = max(0, min(255, nr)); ng = max(0, min(255, ng)); nb = max(0, min(255, nb))
            else:
                nr, ng, nb = r, g, b
            opx[x, y] = (nr, ng, nb, a)

print("stats:", stats)
out.save(OUT)
print("saved:", OUT)

# 3) 棋盘格预览（验证透明）
CELL = 64
prev = Image.new("RGBA", (w, h))
dp = ImageDraw.Draw(prev)
for yy in range(0, h, CELL):
    for xx in range(0, w, CELL):
        c = (220, 220, 220) if ((xx // CELL) + (yy // CELL)) % 2 == 0 else (255, 255, 255)
        dp.rectangle([xx, yy, xx + CELL - 1, yy + CELL - 1], fill=c)
prev.alpha_composite(out)
prev.convert("RGB").save(PREVIEW)
print("preview:", PREVIEW)
