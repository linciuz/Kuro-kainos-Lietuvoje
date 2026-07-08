#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Generate og-image.png (1200x630) for social / search / AI-preview cards.
Brand gradient + the Fuelis logo + name + tagline. Run: python tools/gen_og.py"""
import os
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W, H = 1200, 630
TOP = (22, 115, 232)      # #1673E8
BOT = (0, 87, 216)        # #0057D8

img = Image.new("RGB", (W, H), TOP)
px = img.load()
for y in range(H):
    t = y / (H - 1)
    r = round(TOP[0] + (BOT[0] - TOP[0]) * t)
    g = round(TOP[1] + (BOT[1] - TOP[1]) * t)
    b = round(TOP[2] + (BOT[2] - TOP[2]) * t)
    for x in range(W):
        px[x, y] = (r, g, b)

d = ImageDraw.Draw(img)

# Logo (reuse the real app icon), pasted left, vertically centred.
logo_path = os.path.join(ROOT, "icon-512.png")
logo_w = 300
if os.path.exists(logo_path):
    logo = Image.open(logo_path).convert("RGBA").resize((logo_w, logo_w), Image.LANCZOS)
    img.paste(logo, (95, (H - logo_w) // 2), logo)

def font(size, bold=True):
    for name in (["arialbd.ttf", "Arialbd.ttf"] if bold else ["arial.ttf", "Arial.ttf"]):
        for base in ("C:/Windows/Fonts/", "/usr/share/fonts/truetype/dejavu/"):
            p = base + name
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
    # DejaVu fallback (Linux)
    for p in (("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
               else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),):
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()

tx = 445
d.text((tx, 205), "Fuelis", font=font(140, True), fill=(255, 255, 255))
d.text((tx, 365), "Oficialios degalų kainos", font=font(44, False), fill=(233, 240, 255))
d.text((tx, 420), "Lietuvoje · žemėlapis · EV", font=font(44, False), fill=(233, 240, 255))

out = os.path.join(ROOT, "og-image.png")
img.save(out, "PNG", optimize=True)
print("wrote", out, img.size)
