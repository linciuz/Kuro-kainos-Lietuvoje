"""Fuelis app icons. A white fuel droplet (fuel + a "drop" in price) over a
blue gradient, with a small green downward chevron = price drop. Drawn big and
downscaled for crisp edges. Run from repo root: python tools/gen_icons.py"""

import math
from PIL import Image, ImageDraw

BLUE_TOP = (46, 155, 255)     # #2E9BFF
BLUE_BOT = (0, 86, 214)       # #0056D6
GREEN = (32, 199, 120)        # #20C778  (savings / price-drop accent)
WHITE = (255, 255, 255)


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def droplet(cx, cy, w, h, n=240, m=2.4):
    """Teardrop curve (Wikipedia), rotated so the point faces up."""
    raw = []
    ymax = 0.0
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        ox = math.cos(t)
        oy = math.sin(t) * (math.sin(t / 2) ** m)
        raw.append((oy, -ox))          # rotate: point (1,0) -> top (0,-1)
        ymax = max(ymax, abs(oy))
    return [(cx + nx / ymax * w / 2, cy + ny * h / 2) for nx, ny in raw]


def make(size, maskable=False):
    S = 1024
    img = Image.new("RGB", (S, S), BLUE_BOT)
    px = img.load()
    for y in range(S):
        c = lerp(BLUE_TOP, BLUE_BOT, y / S)
        for x in range(S):
            px[x, y] = c
    d = ImageDraw.Draw(img, "RGBA")

    # Maskable icons need art inside the ~80% safe circle -> shrink + recentre.
    k = 0.80 if maskable else 1.0
    cx = S * 0.5
    cy = S * 0.50
    dw = S * 0.46 * k
    dh = S * 0.66 * k

    # soft drop shadow, then the droplet
    sh = droplet(cx, cy + S * 0.012 * k, dw, dh)
    d.polygon(sh, fill=(0, 40, 100, 70))
    pts = droplet(cx, cy, dw, dh)
    d.polygon(pts, fill=WHITE)

    # green downward chevron (price drop) centred in the bulb
    cw = dw * 0.32
    top = cy + dh * 0.10
    dep = dh * 0.13
    swid = int(S * 0.052 * k)
    d.line([(cx - cw, top), (cx, top + dep)], fill=GREEN, width=swid, joint="curve")
    d.line([(cx, top + dep), (cx + cw, top)], fill=GREEN, width=swid, joint="curve")
    for pt in [(cx - cw, top), (cx, top + dep), (cx + cw, top)]:
        r = swid / 2
        d.ellipse([pt[0] - r, pt[1] - r, pt[0] + r, pt[1] + r], fill=GREEN)

    return img.resize((size, size), Image.LANCZOS)


make(512).save("icon-512.png")
make(192).save("icon-192.png")
make(512, maskable=True).save("icon-512-maskable.png")
make(180).save("apple-touch-icon.png")
print("Fuelis icons written: 512, 192, 512-maskable, 180")
