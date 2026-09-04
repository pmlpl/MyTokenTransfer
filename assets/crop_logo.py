# -*- coding: utf-8 -*-
"""裁剪透明 logo：按非透明像素包围盒紧裁 + 少量内边距，缩到 512x512，输出到 frontend/public/logo.png。"""
from PIL import Image

SRC = r"C:\Users\MOM\Desktop\学习项目\MyTokenTransfer\assets\mtt-logo.png"
OUT = r"C:\Users\MOM\Desktop\学习项目\MyTokenTransfer\frontend\public\logo.png"

img = Image.open(SRC).convert("RGBA")
w, h = img.size
px = img.load()

minx, miny, maxx, maxy = w, h, -1, -1
for y in range(h):
    for x in range(w):
        if px[x, y][3] > 20:  # 半透明/不透明像素
            if x < minx: minx = x
            if x > maxx: maxx = x
            if y < miny: miny = y
            if y > maxy: maxy = y

print("content bbox:", (minx, miny, maxx, maxy))

pad = int((maxx - minx + 1) * 0.05)
x0 = max(0, minx - pad); y0 = max(0, miny - pad)
x1 = min(w, maxx + pad + 1); y1 = min(h, maxy + pad + 1)

crop = img.crop((x0, y0, x1, y1))
crop = crop.resize((512, 512), Image.LANCZOS)
crop.save(OUT)
print("saved:", OUT, crop.size)
