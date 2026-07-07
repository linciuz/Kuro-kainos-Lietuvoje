"""Fuelis app icons. A white fuel droplet over a blue gradient with a small RED
UP chevron (prices rising) above a larger GREEN DOWN chevron (prices falling ->
cheaper, the app's promise). The mark is nudged up ~3.5% for optical balance.
Drawn big and downscaled for crisp edges. Run from repo root: python tools/gen_icons.py"""

import math
from PIL import Image, ImageDraw

BLUE_TOP = (46, 155, 255)     # #2E9BFF
BLUE_BOT = (0, 86, 214)       # #0056D6
GREEN = (32, 199, 120)        # #20C778  price down / cheaper
RED = (240, 69, 58)           # #F0453A  price up
WHITE = (255, 255, 255)
SHIFT = 0.035                 # nudge the mark up for optical balance


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def droplet(cx, cy, w, h, n=240, m=2.4):
    """Teardrop curve (Wikipedia), rotated so the point faces up."""
    raw, ymax = [], 0.0
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        ox = math.cos(t)
        oy = math.sin(t) * (math.sin(t / 2) ** m)
        raw.append((oy, -ox))
        ymax = max(ymax, abs(oy))
    return [(cx + nx / ymax * w / 2, cy + ny * h / 2) for nx, ny in raw]


def chevron(d, cx, y_arms, y_apex, cw, sw, color):
    """Apex at (cx, y_apex), arms at (cx +/- cw, y_arms). apex above arms = up."""
    pts = [(cx - cw, y_arms), (cx, y_apex), (cx + cw, y_arms)]
    d.line([pts[0], pts[1]], fill=color, width=sw, joint="curve")
    d.line([pts[1], pts[2]], fill=color, width=sw, joint="curve")
    for p in pts:
        r = sw / 2
        d.ellipse([p[0] - r, p[1] - r, p[0] + r, p[1] + r], fill=color)


def make(size, maskable=False):
    S = 1024
    img = Image.new("RGB", (S, S), BLUE_BOT)
    px = img.load()
    for y in range(S):
        c = lerp(BLUE_TOP, BLUE_BOT, y / S)
        for x in range(S):
            px[x, y] = c
    d = ImageDraw.Draw(img, "RGBA")

    # Maskable icons keep art inside the ~80% safe circle -> shrink + recentre.
    k = 0.80 if maskable else 1.0
    cx = S * 0.5
    cy = S * 0.50 - S * SHIFT * k        # nudged up for optical balance
    dw = S * 0.46 * k
    dh = S * 0.66 * k

    d.polygon(droplet(cx, cy + S * 0.012 * k, dw, dh), fill=(0, 40, 100, 70))
    d.polygon(droplet(cx, cy, dw, dh), fill=WHITE)

    Cy = cy + dh * 0.15
    g = dh * 0.065
    dep = dh * 0.095
    cw = dw * 0.28
    sw = int(S * 0.048 * k)
    rs = 0.72                            # red smaller: the mark leans to the cheaper (green) side
    chevron(d, cx, Cy - g, Cy - g - dep * rs, cw * rs, int(sw * 0.85), RED)   # up (rising)
    chevron(d, cx, Cy + g, Cy + g + dep, cw, sw, GREEN)                       # down (cheaper)

    return img.resize((size, size), Image.LANCZOS)


make(512).save("icon-512.png")
make(192).save("icon-192.png")
make(512, maskable=True).save("icon-512-maskable.png")
make(180).save("apple-touch-icon.png")
print("Fuelis icons written: 512, 192, 512-maskable, 180")
