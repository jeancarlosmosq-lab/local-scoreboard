"""Original kid-friendly pixel bumpers for the scrolling strip.

These are tiny handmade sprites -- a rocket, a dino, a bot, a kitty, a
star hero, a soccer ball, a comet -- drawn for a ~32px LED panel. They are
NOT Nintendo, Pokemon, or any other licensed characters; those are someone
else's IP. The idea is the same (a little bit of fun art every so often)
without shipping trademarked mascots.

Each sprite has 2+ frames and a motion style so refresh_fun_art can animate
them in place every frame without rebuilding the whole strip.
"""

from __future__ import annotations

import math
from typing import Dict, List, Optional, Sequence, Tuple

# Palette indices used in the sprite grids below.
_ = None  # transparent
K = (40, 40, 50)       # outline / dark
W = (245, 245, 250)    # white
R = (230, 60, 70)      # red
O = (255, 140, 40)     # orange
Y = (255, 210, 50)     # yellow
G = (60, 190, 90)      # green
B = (70, 150, 255)     # blue
C = (120, 220, 255)    # cyan
P = (220, 120, 200)    # pink
N = (180, 120, 70)     # brown
T = (90, 200, 170)     # teal
# Extra tones for shaded / more realistic flyovers.
RD = (160, 35, 45)     # dark red
RL = (255, 110, 115)   # light red
ND = (110, 70, 40)     # dark brown
NL = (210, 165, 110)   # light tan / cream
GD = (35, 110, 55)     # dark green
GL = (120, 210, 130)   # light green
BD = (35, 70, 140)     # dark blue
BL = (150, 195, 255)   # light blue
GY = (130, 135, 145)   # gray
SG = (185, 190, 200)   # silver
SK = (255, 195, 155)   # peach / warm highlight
GO = (220, 170, 40)    # gold

Grid = Tuple[Tuple[Optional[Tuple[int, int, int]], ...], ...]
Sprite = Tuple[str, str, Tuple[Grid, ...]]

# Each sprite: (id, cheer label, frames...). Frames alternate for walk /
# flame / blink; motion() adds bob / bounce / swim on top.
SPRITES: Dict[str, Sprite] = {
    "rocket": (
        "rocket",
        "Zoom!",
        (
            (
                (_, _, _, W, W, _, _, _),
                (_, _, R, R, R, R, _, _),
                (_, R, W, R, R, W, R, _),
                (R, R, R, R, R, R, R, R),
                (R, R, C, C, C, C, R, R),
                (_, R, R, R, R, R, R, _),
                (_, _, O, Y, Y, O, _, _),
                (_, _, _, O, O, _, _, _),
                (_, _, _, Y, Y, _, _, _),
            ),
            (
                (_, _, _, W, W, _, _, _),
                (_, _, R, R, R, R, _, _),
                (_, R, W, R, R, W, R, _),
                (R, R, R, R, R, R, R, R),
                (R, R, C, C, C, C, R, R),
                (_, R, R, R, R, R, R, _),
                (_, _, Y, O, O, Y, _, _),
                (_, _, _, Y, Y, _, _, _),
                (_, _, _, O, O, _, _, _),
            ),
        ),
    ),
    "dino": (
        "dino",
        "Roar!",
        (
            (
                (_, _, _, G, G, G, _, _),
                (_, _, G, G, W, G, _, _),
                (_, _, G, G, G, G, G, _),
                (_, G, G, G, G, _, _, _),
                (G, G, G, G, G, G, _, _),
                (_, G, G, _, G, G, _, _),
                (_, G, _, _, _, G, _, _),
                (_, N, _, _, _, N, _, _),
            ),
            (
                (_, _, _, G, G, G, _, _),
                (_, _, G, G, W, G, _, _),
                (_, _, G, G, G, G, G, _),
                (_, G, G, G, G, _, _, _),
                (G, G, G, G, G, G, _, _),
                (_, G, G, _, G, G, _, _),
                (_, _, G, _, G, _, _, _),
                (_, _, N, _, N, _, _, _),
            ),
        ),
    ),
    "bot": (
        "bot",
        "Beep!",
        (
            (
                (_, _, C, _, C, _, _, _),
                (_, B, B, B, B, B, _, _),
                (B, W, B, B, B, W, B, _),
                (B, B, B, Y, B, B, B, _),
                (_, B, B, B, B, B, _, _),
                (_, _, B, _, B, _, _, _),
                (_, B, B, _, B, B, _, _),
                (_, K, _, _, _, K, _, _),
            ),
            (
                (_, C, _, _, _, C, _, _),
                (_, B, B, B, B, B, _, _),
                (B, Y, B, B, B, Y, B, _),
                (B, B, B, W, B, B, B, _),
                (_, B, B, B, B, B, _, _),
                (_, _, B, _, B, _, _, _),
                (_, B, B, _, B, B, _, _),
                (_, K, _, _, _, K, _, _),
            ),
        ),
    ),
    "kitty": (
        "kitty",
        "Meow!",
        (
            (
                (P, _, _, _, _, _, P, _),
                (_, P, P, P, P, P, _, _),
                (P, W, P, P, P, W, P, _),
                (P, P, P, N, P, P, P, _),
                (_, P, P, P, P, P, _, _),
                (_, _, P, P, P, _, _, _),
                (_, P, _, _, _, P, _, _),
            ),
            (
                (_, P, _, _, _, P, _, _),
                (_, P, P, P, P, P, _, _),
                (P, W, P, P, P, W, P, _),
                (P, P, P, N, P, P, P, _),
                (_, P, P, P, P, P, _, _),
                (_, _, P, P, P, _, _, _),
                (P, _, _, _, _, _, P, _),
            ),
        ),
    ),
    "star": (
        "star",
        "Go!",
        (
            (
                (_, _, _, Y, _, _, _, _),
                (_, _, Y, Y, Y, _, _, _),
                (Y, Y, Y, W, Y, Y, Y, _),
                (_, Y, Y, Y, Y, Y, _, _),
                (_, _, O, Y, O, _, _, _),
                (_, _, Y, _, Y, _, _, _),
                (_, Y, _, _, _, Y, _, _),
            ),
            (
                (_, _, _, O, _, _, _, _),
                (_, _, O, Y, O, _, _, _),
                (O, Y, Y, W, Y, Y, O, _),
                (_, Y, Y, Y, Y, Y, _, _),
                (_, _, Y, O, Y, _, _, _),
                (_, _, O, _, O, _, _, _),
                (_, O, _, _, _, O, _, _),
            ),
        ),
    ),
    "ball": (
        "ball",
        "Kick!",
        (
            (
                (_, _, W, W, W, _, _, _),
                (_, W, K, W, K, W, _, _),
                (W, K, W, W, W, K, W, _),
                (W, W, W, K, W, W, W, _),
                (W, K, W, W, W, K, W, _),
                (_, W, K, W, K, W, _, _),
                (_, _, W, W, W, _, _, _),
            ),
            (
                (_, _, W, W, W, _, _, _),
                (_, W, W, K, W, W, _, _),
                (W, K, W, W, W, K, W, _),
                (W, W, K, W, K, W, W, _),
                (W, K, W, W, W, K, W, _),
                (_, W, W, K, W, W, _, _),
                (_, _, W, W, W, _, _, _),
            ),
        ),
    ),
    "comet": (
        "comet",
        "Whoosh!",
        (
            (
                (C, C, _, _, _, _, Y, _),
                (_, C, C, C, _, Y, Y, Y),
                (_, _, B, B, B, Y, W, Y),
                (_, _, _, B, B, Y, Y, Y),
                (_, _, _, _, C, C, Y, _),
                (_, _, _, _, _, C, _, _),
            ),
            (
                (_, C, C, _, _, _, Y, _),
                (C, _, C, C, _, Y, Y, Y),
                (_, _, _, B, B, Y, W, Y),
                (_, _, B, B, B, Y, Y, Y),
                (_, _, _, C, C, _, Y, _),
                (_, _, _, _, C, _, _, _),
            ),
        ),
    ),
    "fish": (
        "fish",
        "Splash!",
        (
            (
                (_, _, T, T, T, _, _, _),
                (_, T, W, T, T, T, T, _),
                (T, T, T, T, T, T, _, R),
                (_, T, T, T, T, T, T, _),
                (_, _, T, T, T, _, _, _),
            ),
            (
                (_, _, _, T, T, T, _, _),
                (_, T, T, T, T, W, T, _),
                (R, _, T, T, T, T, T, T),
                (_, T, T, T, T, T, T, _),
                (_, _, _, T, T, T, _, _),
            ),
        ),
    ),
}

SPRITE_ORDER: Tuple[str, ...] = (
    "rocket", "dino", "bot", "kitty", "star", "ball", "comet", "fish",
)

# Soft-shaded bird flyovers: paint large, then LANCZOS-downscale.
# Handmade procedural birds -- not licensed characters.

FLYER_ORDER: Tuple[str, ...] = (
    "hawk", "eagle", "seagull", "crow", "goose", "pelican", "hummingbird",
)
FLYERS: Dict[str, str] = {name: name for name in FLYER_ORDER}

try:
    from PIL import Image as _PILImage
    _RESAMPLE = getattr(getattr(_PILImage, "Resampling", _PILImage), "LANCZOS")
    _FLIP_LR = getattr(getattr(_PILImage, "Transpose", _PILImage), "FLIP_LEFT_RIGHT")
except Exception:  # pragma: no cover
    _RESAMPLE = 1
    _FLIP_LR = 0

_FLYER_CACHE: Dict[Tuple, object] = {}


def _clamp(v: float, lo: float = 0.0, hi: float = 255.0) -> int:
    return int(max(lo, min(hi, round(v))))


def _mix(a: Tuple[int, int, int], b: Tuple[int, int, int], t: float
         ) -> Tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        _clamp(a[0] + (b[0] - a[0]) * t),
        _clamp(a[1] + (b[1] - a[1]) * t),
        _clamp(a[2] + (b[2] - a[2]) * t),
    )


def _bird_canvas(size: int, aspect: float = 1.6):
    from PIL import Image as _Im, ImageDraw as _ID
    h = max(18, int(size))
    w = max(28, int(round(h * aspect)))
    hi_h, hi_w = h * 3, w * 3
    im = _Im.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
    return im, _ID.Draw(im), hi_w // 2, hi_h // 2, w, h


def _shade_disk(px: int, py: int, cx: float, cy: float, r: float,
                light: Tuple[int, int, int], mid: Tuple[int, int, int],
                dark: Tuple[int, int, int],
                lx: float = -0.45, ly: float = -0.55
                ) -> Optional[Tuple[int, int, int]]:
    dx = (px + 0.5 - cx) / r
    dy = (py + 0.5 - cy) / r
    d2 = dx * dx + dy * dy
    if d2 > 1.0:
        return None
    nz = math.sqrt(max(0.0, 1.0 - d2))
    llen = math.sqrt(lx * lx + ly * ly + 1.0)
    ndotl = (dx * lx + dy * ly + nz * 1.0) / llen
    ndotl = max(0.0, min(1.0, (ndotl + 0.15) / 1.15))
    if ndotl > 0.72:
        return _mix(mid, light, (ndotl - 0.72) / 0.28)
    if ndotl > 0.35:
        return _mix(dark, mid, (ndotl - 0.35) / 0.37)
    return _mix((12, 12, 16), dark, ndotl / 0.35)


def _draw_soaring_wings(d, cx, cy, flap: float, colors, spread_base: int = 18):
    wing_y = cy - int(10 * flap)
    for i, col in enumerate(colors):
        spread = spread_base + i * 10
        lift = int((8 - i * 2) * flap)
        rgba = col + (230,) if len(col) == 3 else col
        d.polygon([
            (cx, cy),
            (cx - spread * 4, wing_y - lift - i * 3),
            (cx - spread * 2, cy + 6),
        ], fill=rgba)
        d.polygon([
            (cx, cy),
            (cx + spread * 4, wing_y - lift - i * 3),
            (cx + spread * 2, cy + 6),
        ], fill=rgba)


def _render_hawk(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.75)
    flap = 1.0 if frame % 2 == 0 else -1.0
    _draw_soaring_wings(d, cx, cy, flap, (
        (90, 55, 30), (140, 95, 55), (185, 140, 90), (210, 175, 125),
    ))
    d.ellipse([cx - 18, cy - 10, cx + 18, cy + 14], fill=(160, 110, 65, 255))
    d.ellipse([cx - 12, cy - 6, cx + 14, cy + 10], fill=(200, 160, 110, 255))
    d.ellipse([cx + 10, cy - 12, cx + 28, cy + 4], fill=(175, 125, 80, 255))
    d.polygon([(cx + 26, cy - 4), (cx + 40, cy - 1), (cx + 26, cy + 2)],
              fill=(230, 160, 50, 255))
    d.ellipse([cx + 18, cy - 8, cx + 23, cy - 3], fill=(20, 20, 25, 255))
    d.polygon([(cx - 16, cy + 4), (cx - 34, cy + 16), (cx - 10, cy + 12)],
              fill=(120, 70, 40, 240))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_eagle(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.9)
    flap = 1.0 if frame % 2 == 0 else -0.7
    _draw_soaring_wings(d, cx, cy, flap, (
        (35, 30, 28), (70, 60, 55), (110, 95, 85), (150, 130, 115),
    ), spread_base=20)
    # Dark body
    d.ellipse([cx - 20, cy - 8, cx + 16, cy + 16], fill=(45, 40, 38, 255))
    d.ellipse([cx - 14, cy - 4, cx + 12, cy + 12], fill=(75, 65, 60, 255))
    # White head
    d.ellipse([cx + 6, cy - 14, cx + 30, cy + 4], fill=(240, 240, 245, 255))
    d.ellipse([cx + 12, cy - 10, cx + 26, cy], fill=(255, 255, 255, 255))
    # Yellow beak + eye
    d.polygon([(cx + 28, cy - 4), (cx + 44, cy), (cx + 28, cy + 3)],
              fill=(240, 190, 50, 255))
    d.ellipse([cx + 16, cy - 8, cx + 21, cy - 3], fill=(20, 20, 25, 255))
    # White tail
    d.polygon([(cx - 18, cy + 6), (cx - 40, cy + 4), (cx - 36, cy + 16),
               (cx - 12, cy + 14)], fill=(230, 230, 235, 250))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_cardinal(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.35)
    hop = 4 if frame % 2 else 0
    cy -= hop
    for i, col in enumerate((
        (120, 20, 30, 255), (180, 35, 45, 255), (230, 60, 70, 255),
        (255, 110, 115, 255),
    )):
        d.ellipse([cx - 22 + i * 2, cy - 10 + i,
                   cx + 18 - i, cy + 16 - i], fill=col)
    d.polygon([(cx + 6, cy - 10), (cx + 14, cy - 28), (cx + 18, cy - 8)],
              fill=(200, 40, 50, 255))
    d.ellipse([cx + 8, cy - 12, cx + 28, cy + 6], fill=(220, 55, 65, 255))
    d.polygon([(cx + 26, cy - 2), (cx + 40, cy + 2), (cx + 26, cy + 5)],
              fill=(40, 35, 35, 255))
    d.ellipse([cx + 16, cy - 6, cx + 21, cy - 1], fill=(15, 15, 20, 255))
    d.ellipse([cx - 18, cy - 4, cx + 4, cy + 12], fill=(140, 25, 35, 255))
    d.polygon([(cx - 20, cy + 6), (cx - 34, cy + 4), (cx - 22, cy + 14)],
              fill=(150, 30, 40, 255))
    d.line([(cx - 4, cy + 16), (cx - 4, cy + 26)], fill=(60, 40, 25, 255), width=3)
    d.line([(cx + 4, cy + 16), (cx + 6, cy + 26)], fill=(60, 40, 25, 255), width=3)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_bluejay(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.45)
    hop = 3 if frame % 2 else 0
    cy -= hop
    # Blue body with white belly / black necklace
    for i, col in enumerate((
        (30, 70, 140, 255), (50, 110, 190, 255), (90, 150, 230, 255),
        (140, 185, 255, 255),
    )):
        d.ellipse([cx - 20 + i * 2, cy - 10 + i,
                   cx + 16 - i, cy + 14 - i], fill=col)
    d.ellipse([cx - 12, cy + 2, cx + 14, cy + 16], fill=(245, 245, 250, 255))
    d.arc([cx - 8, cy - 4, cx + 14, cy + 10], 200, 340, fill=(20, 20, 30, 255))
    # Crest
    d.polygon([(cx + 4, cy - 10), (cx + 10, cy - 26), (cx + 16, cy - 8)],
              fill=(70, 130, 210, 255))
    d.ellipse([cx + 6, cy - 12, cx + 26, cy + 4], fill=(80, 140, 220, 255))
    d.polygon([(cx + 24, cy - 2), (cx + 36, cy + 2), (cx + 24, cy + 4)],
              fill=(35, 35, 40, 255))
    d.ellipse([cx + 14, cy - 6, cx + 19, cy - 1], fill=(15, 15, 20, 255))
    # Wing bars
    d.ellipse([cx - 16, cy - 2, cx + 2, cy + 12], fill=(40, 90, 170, 255))
    d.line([(cx - 12, cy + 2), (cx - 2, cy + 6)], fill=(245, 245, 250, 255), width=3)
    d.line([(cx - 10, cy + 6), (cx, cy + 10)], fill=(20, 20, 30, 255), width=2)
    d.polygon([(cx - 18, cy + 4), (cx - 32, cy + 2), (cx - 20, cy + 12)],
              fill=(55, 100, 180, 255))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_owl(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.25)
    blink = frame % 2
    # Round body
    for i, col in enumerate((
        (70, 50, 30, 255), (120, 90, 50, 255), (170, 130, 75, 255),
        (200, 165, 110, 255),
    )):
        d.ellipse([cx - 26 + i * 2, cy - 16 + i,
                   cx + 26 - i * 2, cy + 22 - i], fill=col)
    # Facial disk
    d.ellipse([cx - 18, cy - 14, cx + 18, cy + 10], fill=(210, 185, 140, 255))
    # Eyes
    if blink:
        d.line([(cx - 10, cy - 4), (cx - 2, cy - 4)], fill=(20, 20, 25, 255), width=3)
        d.line([(cx + 2, cy - 4), (cx + 10, cy - 4)], fill=(20, 20, 25, 255), width=3)
    else:
        d.ellipse([cx - 12, cy - 8, cx - 2, cy + 2], fill=(240, 200, 60, 255))
        d.ellipse([cx + 2, cy - 8, cx + 12, cy + 2], fill=(240, 200, 60, 255))
        d.ellipse([cx - 9, cy - 5, cx - 5, cy - 1], fill=(15, 15, 20, 255))
        d.ellipse([cx + 5, cy - 5, cx + 9, cy - 1], fill=(15, 15, 20, 255))
    # Beak
    d.polygon([(cx - 2, cy + 2), (cx + 2, cy + 2), (cx, cy + 10)],
              fill=(40, 30, 20, 255))
    # Ear tufts
    d.polygon([(cx - 16, cy - 12), (cx - 22, cy - 28), (cx - 8, cy - 14)],
              fill=(130, 95, 55, 255))
    d.polygon([(cx + 16, cy - 12), (cx + 22, cy - 28), (cx + 8, cy - 14)],
              fill=(130, 95, 55, 255))
    # Feet
    d.line([(cx - 6, cy + 20), (cx - 8, cy + 30)], fill=(200, 150, 60, 255), width=3)
    d.line([(cx + 6, cy + 20), (cx + 8, cy + 30)], fill=(200, 150, 60, 255), width=3)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_hummingbird(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(max(14, size - 2), 1.7)
    # Blurred wings
    if frame % 2 == 0:
        d.ellipse([cx - 28, cy - 18, cx - 4, cy + 2], fill=(180, 220, 200, 120))
        d.ellipse([cx + 4, cy - 16, cx + 30, cy + 4], fill=(180, 220, 200, 120))
    else:
        d.ellipse([cx - 26, cy - 6, cx - 2, cy + 14], fill=(160, 200, 180, 110))
        d.ellipse([cx + 2, cy - 8, cx + 28, cy + 12], fill=(160, 200, 180, 110))
    # Emerald body
    for i, col in enumerate((
        (10, 90, 60, 255), (20, 150, 90, 255), (40, 200, 120, 255),
        (120, 230, 160, 255),
    )):
        d.ellipse([cx - 10 + i, cy - 6 + i, cx + 12 - i, cy + 10 - i], fill=col)
    # Ruby throat
    d.ellipse([cx + 4, cy - 2, cx + 14, cy + 8], fill=(200, 30, 50, 255))
    d.ellipse([cx + 8, cy - 8, cx + 20, cy + 2], fill=(30, 160, 100, 255))
    d.ellipse([cx + 12, cy - 6, cx + 16, cy - 2], fill=(15, 15, 20, 255))
    # Long bill
    d.line([(cx + 18, cy - 2), (cx + 42, cy - 6)], fill=(40, 40, 45, 255), width=3)
    # Tail
    d.polygon([(cx - 10, cy + 2), (cx - 24, cy - 4), (cx - 22, cy + 10)],
              fill=(15, 120, 80, 255))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_flamingo(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.15)
    # Long neck S-curve via ellipses
    pinks = [(220, 90, 120, 255), (255, 130, 150, 255), (255, 170, 180, 255)]
    d.ellipse([cx - 8, cy + 4, cx + 18, cy + 28], fill=pinks[0])  # body
    d.ellipse([cx - 4, cy + 8, cx + 14, cy + 24], fill=pinks[1])
    # Neck
    d.ellipse([cx + 10, cy - 8, cx + 22, cy + 12], fill=pinks[1])
    d.ellipse([cx + 4, cy - 22, cx + 18, cy - 6], fill=pinks[2])
    d.ellipse([cx + 14, cy - 28, cx + 28, cy - 14], fill=pinks[1])
    # Head + black-tipped beak
    d.ellipse([cx + 22, cy - 30, cx + 36, cy - 16], fill=pinks[2])
    d.polygon([(cx + 34, cy - 24), (cx + 50, cy - 20), (cx + 34, cy - 18)],
              fill=(240, 200, 80, 255))
    d.polygon([(cx + 44, cy - 22), (cx + 54, cy - 18), (cx + 44, cy - 16)],
              fill=(25, 25, 30, 255))
    d.ellipse([cx + 26, cy - 26, cx + 30, cy - 22], fill=(20, 20, 25, 255))
    # One long leg (step)
    leg_x = cx + (4 if frame % 2 == 0 else -2)
    d.line([(leg_x, cy + 26), (leg_x + 2, cy + 48)], fill=(220, 120, 100, 255), width=3)
    d.line([(cx + 8, cy + 26), (cx + 14, cy + 40)], fill=(200, 100, 90, 200), width=2)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_robin(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.35)
    hop = 4 if frame % 2 else 0
    cy -= hop
    # Brown back
    for i, col in enumerate((
        (70, 45, 30, 255), (110, 75, 45, 255), (150, 105, 65, 255),
    )):
        d.ellipse([cx - 20 + i * 2, cy - 10 + i,
                   cx + 16 - i, cy + 14 - i], fill=col)
    # Orange breast
    d.ellipse([cx - 10, cy - 2, cx + 14, cy + 16], fill=(230, 110, 50, 255))
    d.ellipse([cx - 6, cy + 2, cx + 12, cy + 14], fill=(255, 140, 70, 255))
    d.ellipse([cx + 6, cy - 12, cx + 24, cy + 2], fill=(130, 90, 55, 255))
    d.polygon([(cx + 22, cy - 4), (cx + 34, cy), (cx + 22, cy + 2)],
              fill=(40, 35, 30, 255))
    d.ellipse([cx + 12, cy - 8, cx + 17, cy - 3], fill=(15, 15, 20, 255))
    d.ellipse([cx - 16, cy - 2, cx, cy + 10], fill=(90, 60, 40, 255))
    d.polygon([(cx - 18, cy + 4), (cx - 30, cy + 2), (cx - 20, cy + 12)],
              fill=(100, 70, 45, 255))
    d.line([(cx - 2, cy + 14), (cx - 2, cy + 24)], fill=(50, 35, 25, 255), width=2)
    d.line([(cx + 6, cy + 14), (cx + 8, cy + 24)], fill=(50, 35, 25, 255), width=2)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_macaw(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.4)
    # Scarlet macaw-ish: red body, blue/yellow wing
    for i, col in enumerate((
        (140, 20, 25, 255), (200, 35, 40, 255), (240, 55, 50, 255),
    )):
        d.ellipse([cx - 18 + i * 2, cy - 12 + i,
                   cx + 14 - i, cy + 18 - i], fill=col)
    # Wing flash
    wing = (cx - 8, cy - 6, cx + 6, cy + 14)
    d.ellipse(list(wing), fill=(30, 90, 200, 255))
    d.ellipse([cx - 6, cy, cx + 4, cy + 12], fill=(250, 200, 40, 255))
    # Head + white face patch + beak
    d.ellipse([cx + 4, cy - 16, cx + 26, cy + 2], fill=(230, 50, 45, 255))
    d.ellipse([cx + 12, cy - 10, cx + 22, cy], fill=(250, 245, 240, 255))
    d.polygon([(cx + 24, cy - 6), (cx + 40, cy - 2), (cx + 24, cy + 2)],
              fill=(30, 30, 35, 255))
    d.ellipse([cx + 14, cy - 12, cx + 18, cy - 8], fill=(15, 15, 20, 255))
    # Long tail
    if frame % 2 == 0:
        d.polygon([(cx - 14, cy + 10), (cx - 40, cy + 4), (cx - 36, cy + 20),
                   (cx - 10, cy + 16)], fill=(40, 100, 210, 255))
    else:
        d.polygon([(cx - 14, cy + 10), (cx - 38, cy + 12), (cx - 32, cy + 24),
                   (cx - 10, cy + 16)], fill=(40, 100, 210, 255))
    d.line([(cx, cy + 16), (cx - 2, cy + 28)], fill=(50, 35, 25, 255), width=2)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_pelican(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.85)
    flap = 0.8 if frame % 2 == 0 else -0.5
    _draw_soaring_wings(d, cx, cy + 4, flap, (
        (180, 175, 160), (220, 215, 200), (245, 240, 230),
    ), spread_base=16)
    # White body
    d.ellipse([cx - 22, cy - 6, cx + 20, cy + 18], fill=(235, 230, 220, 255))
    d.ellipse([cx - 14, cy - 2, cx + 14, cy + 14], fill=(250, 248, 240, 255))
    # Head + huge pouch beak
    d.ellipse([cx + 10, cy - 14, cx + 30, cy + 2], fill=(240, 235, 225, 255))
    d.polygon([(cx + 26, cy - 4), (cx + 56, cy + 2), (cx + 28, cy + 10),
               (cx + 22, cy + 4)], fill=(240, 190, 70, 255))
    d.polygon([(cx + 30, cy + 2), (cx + 54, cy + 4), (cx + 32, cy + 12)],
              fill=(220, 150, 50, 255))
    d.ellipse([cx + 16, cy - 10, cx + 21, cy - 5], fill=(20, 20, 25, 255))
    # Feet splash hint
    if frame % 2:
        d.ellipse([cx - 6, cy + 20, cx + 10, cy + 26], fill=(140, 190, 230, 150))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_swan(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.7)
    # White body floating
    d.ellipse([cx - 28, cy - 2, cx + 18, cy + 20], fill=(220, 220, 230, 255))
    d.ellipse([cx - 20, cy + 2, cx + 12, cy + 16], fill=(250, 250, 255, 255))
    # S-curve neck
    d.ellipse([cx + 8, cy - 10, cx + 22, cy + 8], fill=(240, 240, 248, 255))
    d.ellipse([cx + 2, cy - 24, cx + 18, cy - 8], fill=(250, 250, 255, 255))
    d.ellipse([cx + 14, cy - 30, cx + 30, cy - 14], fill=(245, 245, 252, 255))
    # Orange beak + black lore
    d.polygon([(cx + 28, cy - 24), (cx + 46, cy - 20), (cx + 28, cy - 16)],
              fill=(240, 140, 40, 255))
    d.ellipse([cx + 24, cy - 26, cx + 32, cy - 18], fill=(20, 20, 25, 255))
    d.ellipse([cx + 18, cy - 26, cx + 23, cy - 21], fill=(15, 15, 20, 255))
    # Wing lift
    lift = -4 if frame % 2 == 0 else 2
    d.ellipse([cx - 24, cy - 8 + lift, cx - 2, cy + 10 + lift],
              fill=(230, 230, 240, 240))
    # Water ripple
    d.ellipse([cx - 16, cy + 18, cx + 20, cy + 26], fill=(120, 180, 230, 100))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_crow(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.55)
    flap = 1.0 if frame % 2 == 0 else -0.8
    _draw_soaring_wings(d, cx, cy, flap, (
        (15, 15, 20), (40, 40, 50), (70, 70, 80),
    ), spread_base=14)
    d.ellipse([cx - 16, cy - 8, cx + 14, cy + 12], fill=(30, 30, 35, 255))
    d.ellipse([cx - 10, cy - 4, cx + 10, cy + 8], fill=(55, 55, 65, 255))
    d.ellipse([cx + 6, cy - 12, cx + 24, cy + 2], fill=(25, 25, 30, 255))
    d.polygon([(cx + 22, cy - 4), (cx + 36, cy), (cx + 22, cy + 2)],
              fill=(20, 20, 25, 255))
    d.ellipse([cx + 12, cy - 8, cx + 17, cy - 3], fill=(220, 200, 40, 255))
    d.ellipse([cx + 13, cy - 7, cx + 16, cy - 4], fill=(10, 10, 12, 255))
    d.polygon([(cx - 14, cy + 2), (cx - 30, cy + 6), (cx - 12, cy + 10)],
              fill=(20, 20, 28, 255))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_seagull(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.85)
    flap = 1.0 if frame % 2 == 0 else -0.6
    _draw_soaring_wings(d, cx, cy, flap, (
        (160, 165, 175), (210, 210, 220), (245, 245, 250),
    ), spread_base=18)
    d.ellipse([cx - 16, cy - 6, cx + 14, cy + 12], fill=(235, 235, 240, 255))
    d.ellipse([cx - 10, cy, cx + 12, cy + 12], fill=(180, 185, 195, 255))  # gray back
    d.ellipse([cx + 6, cy - 12, cx + 24, cy + 2], fill=(250, 250, 255, 255))
    d.polygon([(cx + 22, cy - 4), (cx + 38, cy), (cx + 22, cy + 2)],
              fill=(240, 170, 40, 255))
    d.ellipse([cx + 12, cy - 8, cx + 17, cy - 3], fill=(20, 20, 25, 255))
    d.polygon([(cx - 14, cy + 4), (cx - 28, cy + 2), (cx - 16, cy + 12)],
              fill=(90, 95, 105, 255))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_penguin(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.05)
    wobble = 3 if frame % 2 == 0 else -3
    cx += wobble
    # Black back / white belly
    d.ellipse([cx - 16, cy - 18, cx + 16, cy + 22], fill=(25, 25, 35, 255))
    d.ellipse([cx - 12, cy - 10, cx + 12, cy + 20], fill=(245, 245, 250, 255))
    d.ellipse([cx - 14, cy - 20, cx + 14, cy - 2], fill=(20, 20, 28, 255))
    # Face
    d.ellipse([cx - 8, cy - 18, cx + 8, cy - 4], fill=(250, 250, 255, 255))
    d.ellipse([cx - 6, cy - 14, cx - 2, cy - 10], fill=(15, 15, 20, 255))
    d.ellipse([cx + 2, cy - 14, cx + 6, cy - 10], fill=(15, 15, 20, 255))
    d.polygon([(cx - 2, cy - 8), (cx + 2, cy - 8), (cx, cy - 2)],
              fill=(240, 150, 40, 255))
    # Flippers
    d.ellipse([cx - 22, cy - 4, cx - 12, cy + 12], fill=(30, 30, 40, 255))
    d.ellipse([cx + 12, cy - 4, cx + 22, cy + 12], fill=(30, 30, 40, 255))
    # Feet
    d.polygon([(cx - 8, cy + 20), (cx - 14, cy + 28), (cx - 2, cy + 24)],
              fill=(240, 150, 40, 255))
    d.polygon([(cx + 8, cy + 20), (cx + 14, cy + 28), (cx + 2, cy + 24)],
              fill=(240, 150, 40, 255))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_heron(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.2)
    # Tall gray body + long neck
    d.ellipse([cx - 10, cy + 2, cx + 14, cy + 24], fill=(140, 150, 165, 255))
    d.ellipse([cx - 6, cy + 6, cx + 10, cy + 20], fill=(190, 200, 215, 255))
    d.ellipse([cx + 6, cy - 10, cx + 18, cy + 10], fill=(160, 170, 185, 255))
    d.ellipse([cx + 2, cy - 24, cx + 16, cy - 8], fill=(180, 190, 205, 255))
    d.ellipse([cx + 12, cy - 30, cx + 26, cy - 16], fill=(200, 210, 220, 255))
    # Long dagger beak
    d.polygon([(cx + 24, cy - 24), (cx + 48, cy - 22), (cx + 24, cy - 18)],
              fill=(240, 180, 60, 255))
    d.ellipse([cx + 16, cy - 26, cx + 20, cy - 22], fill=(20, 20, 25, 255))
    # Crest plume
    d.line([(cx + 14, cy - 28), (cx + 8, cy - 40)], fill=(220, 225, 235, 255), width=2)
    # One long leg in water
    leg = cx + (2 if frame % 2 == 0 else -2)
    d.line([(leg, cy + 22), (leg + 1, cy + 48)], fill=(200, 160, 80, 255), width=2)
    d.ellipse([cx - 8, cy + 44, cx + 16, cy + 52], fill=(100, 170, 220, 90))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_toucan(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.55)
    hop = 3 if frame % 2 else 0
    cy -= hop
    # Black body, yellow bib
    d.ellipse([cx - 18, cy - 8, cx + 12, cy + 16], fill=(25, 25, 30, 255))
    d.ellipse([cx - 8, cy, cx + 10, cy + 14], fill=(250, 210, 50, 255))
    d.ellipse([cx + 2, cy - 12, cx + 20, cy + 2], fill=(30, 30, 35, 255))
    # Huge colorful bill
    d.polygon([(cx + 16, cy - 10), (cx + 52, cy - 14), (cx + 50, cy + 2),
               (cx + 16, cy + 4)], fill=(240, 70, 50, 255))
    d.polygon([(cx + 20, cy - 6), (cx + 48, cy - 8), (cx + 46, cy),
               (cx + 20, cy + 2)], fill=(250, 200, 40, 255))
    d.ellipse([cx + 8, cy - 10, cx + 13, cy - 5], fill=(20, 200, 80, 255))
    # Blue feet hint / tail
    d.polygon([(cx - 16, cy + 6), (cx - 32, cy + 4), (cx - 18, cy + 14)],
              fill=(40, 40, 50, 255))
    d.line([(cx - 2, cy + 14), (cx - 2, cy + 24)], fill=(40, 100, 200, 255), width=2)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_woodpecker(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.25)
    # Vertical cling pose; peck animation
    peck = 4 if frame % 2 == 0 else 0
    d.ellipse([cx - 10, cy - 16, cx + 12, cy + 18], fill=(40, 40, 50, 255))
    d.ellipse([cx - 6, cy - 8, cx + 10, cy + 14], fill=(245, 245, 250, 255))
    # Red crown
    d.ellipse([cx - 4, cy - 22, cx + 12, cy - 8], fill=(220, 40, 50, 255))
    d.ellipse([cx + 2 + peck, cy - 14, cx + 16 + peck, cy - 2],
              fill=(50, 50, 60, 255))
    d.polygon([(cx + 14 + peck, cy - 8), (cx + 28 + peck, cy - 6),
               (cx + 14 + peck, cy - 4)], fill=(35, 35, 40, 255))
    d.ellipse([cx + 6 + peck, cy - 12, cx + 10 + peck, cy - 8],
              fill=(15, 15, 20, 255))
    # Black wing bars
    d.ellipse([cx - 12, cy - 4, cx + 2, cy + 12], fill=(30, 30, 40, 255))
    d.line([(cx - 8, cy), (cx - 2, cy + 4)], fill=(245, 245, 250, 255), width=2)
    d.line([(cx - 8, cy + 6), (cx - 2, cy + 10)], fill=(245, 245, 250, 255), width=2)
    # Tree trunk hint
    d.rectangle([cx + 26, 0, cx + 34, h * 3], fill=(90, 55, 30, 120))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_goose(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    im, d, cx, cy, w, h = _bird_canvas(size, 1.75)
    flap = 0.7 if frame % 2 == 0 else -0.5
    _draw_soaring_wings(d, cx, cy, flap, (
        (80, 90, 100), (140, 150, 160), (200, 205, 215),
    ), spread_base=15)
    # Canada goose-ish: brown body, black neck, white cheek
    d.ellipse([cx - 22, cy - 4, cx + 16, cy + 16], fill=(120, 90, 55, 255))
    d.ellipse([cx - 14, cy, cx + 12, cy + 12], fill=(160, 125, 80, 255))
    d.ellipse([cx + 8, cy - 16, cx + 22, cy + 4], fill=(25, 25, 30, 255))
    d.ellipse([cx + 12, cy - 22, cx + 28, cy - 8], fill=(20, 20, 25, 255))
    d.ellipse([cx + 18, cy - 18, cx + 26, cy - 10], fill=(245, 245, 250, 255))
    d.polygon([(cx + 26, cy - 14), (cx + 40, cy - 12), (cx + 26, cy - 10)],
              fill=(35, 35, 40, 255))
    d.ellipse([cx + 16, cy - 16, cx + 20, cy - 12], fill=(15, 15, 20, 255))
    d.polygon([(cx - 20, cy + 4), (cx - 36, cy + 2), (cx - 22, cy + 12)],
              fill=(100, 75, 45, 255))
    return im.resize((w, h), resample=_RESAMPLE)


_FLYER_RENDERERS = {
    "hawk": _render_hawk,
    "eagle": _render_eagle,
    "cardinal": _render_cardinal,
    "bluejay": _render_bluejay,
    "owl": _render_owl,
    "hummingbird": _render_hummingbird,
    "flamingo": _render_flamingo,
    "robin": _render_robin,
    "macaw": _render_macaw,
    "pelican": _render_pelican,
    "swan": _render_swan,
    "crow": _render_crow,
    "seagull": _render_seagull,
    "penguin": _render_penguin,
    "heron": _render_heron,
    "toucan": _render_toucan,
    "woodpecker": _render_woodpecker,
    "goose": _render_goose,
}



def render_flyer(flyer_id: str, height: int, frame: int = 0,
                 progress: float = 0.0):
    """Build a soft-shaded RGBA sprite sized for the panel."""
    fn = _FLYER_RENDERERS.get(flyer_id)
    if not fn:
        return None
    height = max(10, min(30, int(height)))
    key = (flyer_id, height, int(frame) % 2, int(progress * 16))
    cached = _FLYER_CACHE.get(key)
    if cached is not None:
        return cached.copy()
    try:
        im = fn(height, frame, progress)
    except Exception:
        return None
    if im is None:
        return None
    if len(_FLYER_CACHE) > 48:
        _FLYER_CACHE.clear()
    _FLYER_CACHE[key] = im
    return im.copy()


def flyer_size(flyer_id: str, scale: int = 2) -> Tuple[int, int]:
    """Approx size for layout / depth travel (height ~= 10*scale)."""
    if flyer_id not in FLYERS:
        return 0, 0
    h = max(10, min(30, 10 * max(1, int(scale))))
    # Match aspect hints from renderers.
    aspects = {
        "hawk": 1.75, "eagle": 1.9, "cardinal": 1.35, "bluejay": 1.45,
        "owl": 1.25, "hummingbird": 1.7, "flamingo": 1.15, "robin": 1.35,
        "macaw": 1.4, "pelican": 1.85, "swan": 1.7, "crow": 1.55,
        "seagull": 1.85, "penguin": 1.05, "heron": 1.2, "toucan": 1.55,
        "woodpecker": 1.25, "goose": 1.75,
    }
    w = int(round(h * aspects.get(flyer_id, 1.4)))
    return w, h


def blit_flyer(draw, x: int, y: int, flyer_id: str, scale: int = 2,
               frame: int = 0) -> Tuple[int, int]:
    """Legacy entry: soft-render and paste onto draw.im when possible."""
    img = getattr(draw, "_image", None) or getattr(draw, "im", None)
    # ImageDraw stores the image as draw.im in older PIL / ._image rarely.
    host = None
    for attr in ("_image", "im"):
        host = getattr(draw, attr, None)
        if host is not None:
            break
    fw, fh = flyer_size(flyer_id, scale=scale)
    sprite = render_flyer(flyer_id, fh, frame=frame)
    if sprite is None or host is None:
        return fw, fh
    if host.mode != "RGBA":
        # Composite onto RGB host.
        from PIL import Image as _Im
        layer = _Im.new("RGBA", host.size, (0, 0, 0, 0))
        layer.paste(sprite, (int(x), int(y)), sprite)
        composed = _Im.alpha_composite(host.convert("RGBA"), layer)
        host.paste(composed.convert(host.mode))
    else:
        host.paste(sprite, (int(x), int(y)), sprite)
    return sprite.size


def _flyer_route(cycle_i: int, panel_w: int, panel_h: int,
                 fw: int, fh: int
                 ) -> Tuple[float, float, float, float]:
    """Pick an entry→exit route for this flight.

    Returns (x0, y0, x1, y1) in panel pixels; start/end sit just off-screen
    so the bird flies in from one place and leaves through another. Routes
    are tuned so a short 32px panel still sees most of the flight.
    """
    # Just off each edge (partially visible soon after entry).
    top = float(-max(4, fh // 3))
    bot = float(panel_h - fh + max(4, fh // 3))
    left = float(-max(4, fw // 3))
    right = float(panel_w - fw + max(4, fw // 3))
    y_hi = float(max(0, min(panel_h - fh, int(panel_h * 0.08))))
    y_mid = float(max(0, (panel_h - fh) // 2))
    y_lo = float(max(0, min(panel_h - fh, int(panel_h * 0.58))))
    x_l = float(max(0, int(panel_w * 0.08)))
    x_m = float(max(0, (panel_w - fw) // 2))
    x_r = float(max(0, min(panel_w - fw, int(panel_w * 0.78))))

    routes: Tuple[Tuple[float, float, float, float], ...] = (
        # Across — different heights / exit lanes.
        (left, y_mid, right, y_mid),
        (right, y_hi, left, y_hi),
        (left, y_lo, right, y_lo),
        (right, y_mid, left, y_lo),
        # Diagonals.
        (left, y_hi, right, y_lo),
        (right, y_lo, left, y_hi),
        (left, y_lo, right, y_hi),
        (right, y_hi, left, y_lo),
        # Dive / climb with horizontal drift (stay on-panel longer).
        (x_l, top, x_r, bot),
        (x_r, bot, x_l, top),
        (x_m, top, right, y_lo),
        (left, y_hi, x_m, bot),
        # Corner skims.
        (left, top, right, y_mid),
        (right, top, left, y_lo),
        (left, bot, right, y_hi),
        (right, bot, left, y_mid),
    )
    return routes[cycle_i % len(routes)]


def apply_flyer(img, t: float, interval: float = 10.0,
                flight: float = 2.8) -> Optional[str]:
    """Soft-shaded birds fly in from varied edges and exit elsewhere."""
    try:
        from PIL import Image as _Im
    except ImportError:  # pragma: no cover
        return None

    w, h = img.size
    if w < 16 or h < 8 or interval <= 0 or flight <= 0:
        return None

    cycle_i = int(t // interval)
    local = t - cycle_i * interval
    if local > flight:
        return None

    flyer_id = FLYER_ORDER[cycle_i % len(FLYER_ORDER)]
    progress = local / flight
    frame = int(local * 10) % 2

    # Depth: bigger mid-flight (closest), smaller at the ends.
    depth = math.sin(progress * math.pi)
    if cycle_i % 3 == 2:
        depth = max(0.15, min(1.0, 0.35 + 0.65 * (1.0 - progress)))

    fh = max(12, min(h - 2, 12 + int(round(depth * (min(26, h - 2) - 12)))))
    sprite = render_flyer(flyer_id, fh, frame=frame, progress=progress)
    if sprite is None:
        return None
    fw, fh = sprite.size

    x0, y0, x1, y1 = _flyer_route(cycle_i, w, h, fw, fh)
    t_ease = progress * progress * (3.0 - 2.0 * progress)
    x = x0 + (x1 - x0) * t_ease
    y = y0 + (y1 - y0) * t_ease
    if cycle_i % 4 == 1:
        y -= math.sin(progress * math.pi) * (3 + fh // 8)
    elif cycle_i % 4 == 3:
        y += math.sin(progress * math.pi) * (2 + fh // 10)

    bob = int(round(math.sin(progress * math.pi * 5) * (1 + fh // 14)))
    if flyer_id == "hummingbird":
        bob = int(round(math.sin(progress * math.pi * 9) * (2 + fh // 10)))
    x = int(round(x))
    y = int(round(y + bob))

    if (x1 - x0) < 0:
        sprite = sprite.transpose(_FLIP_LR)

    if img.mode != "RGBA":
        layer = _Im.new("RGBA", img.size, (0, 0, 0, 0))
        layer.paste(sprite, (x, y), sprite)
        composed = _Im.alpha_composite(img.convert("RGBA"), layer)
        img.paste(composed.convert(img.mode))
    else:
        img.paste(sprite, (x, y), sprite)
    return flyer_id






# Pixels of horizontal / vertical room reserved around each sprite so a
# bounce, wiggle, and flying wreckage never clips into neighbouring content.
MOTION_PAD_X = 4
MOTION_PAD_Y = 4
# Extra width for debris / cracks so the character looks like it is
# tearing through the panel, not just bobbing in empty black.
WRECK_PAD_X = 8

# Cheer words lean into the "wrecking the board" gag.
CHEERS: Dict[str, str] = {
    "rocket": "Kaboom!",
    "dino": "Smash!",
    "bot": "Zap!",
    "kitty": "Pounce!",
    "star": "Crash!",
    "ball": "Wham!",
    "comet": "Kapow!",
    "fish": "Chomp!",
}

# Debris / spark colours -- bright so they read as broken LEDs flying off.
_DEBRIS = (
    (255, 60, 40), (255, 220, 30), (80, 220, 255),
    (255, 255, 255), (40, 255, 90), (255, 80, 200),
    (255, 140, 0), (255, 255, 120),
)
_CRACK = (180, 190, 210)
_STATIC = (120, 130, 160)
_HOLE = (0, 0, 0)
_FLASH = (255, 255, 220)


def pick_sprites(when_hour: int, count: int = 2,
                 enabled_ids: Optional[Sequence[str]] = None) -> List[str]:
    """Rotate which bumpers appear this hour so the strip stays fresh."""
    catalog = [s for s in (enabled_ids or SPRITE_ORDER) if s in SPRITES]
    if not catalog or count <= 0:
        return []
    start = int(when_hour) % len(catalog)
    out: List[str] = []
    for i in range(min(count, len(catalog))):
        out.append(catalog[(start + i * 3) % len(catalog)])
    return out


def _frames(sprite_id: str) -> Tuple[Grid, ...]:
    entry = SPRITES.get(sprite_id)
    if not entry:
        return tuple()
    return entry[2]


def sprite_size(sprite_id: str, scale: int = 2) -> Tuple[int, int]:
    frames = _frames(sprite_id)
    if not frames:
        return 0, 0
    rows = frames[0]
    scale = max(1, int(scale))
    h = len(rows)
    w = max((len(row) for row in rows), default=0)
    return w * scale, h * scale


def motion(sprite_id: str, t: float) -> Tuple[int, int, int]:
    """(dx, dy, frame) for this sprite at time t (seconds)."""
    frames = _frames(sprite_id)
    n = max(1, len(frames))
    # Phase offset per sprite so two bumpers on the strip don't sync-march.
    phase = (sum(ord(c) for c in sprite_id) % 7) * 0.37
    u = t + phase

    if sprite_id == "ball":
        # Bounce: abs(sin) so it hits the "ground" and pops up.
        dy = -int(round(abs(math.sin(u * 5.5)) * MOTION_PAD_Y))
        dx = int(round(math.sin(u * 2.2) * 1))
        frame = int(u * 6) % n
        return dx, dy, frame
    if sprite_id == "rocket":
        dy = int(round(math.sin(u * 4.0) * 2))
        dx = int(round(math.sin(u * 1.5) * 1))
        frame = int(u * 8) % n
        return dx, dy, frame
    if sprite_id == "comet":
        dx = int(round(math.sin(u * 3.5) * MOTION_PAD_X))
        dy = int(round(math.cos(u * 3.5) * 1))
        frame = int(u * 10) % n
        return dx, dy, frame
    if sprite_id == "fish":
        dy = int(round(math.sin(u * 3.0) * 2))
        dx = int(round(math.sin(u * 1.8) * MOTION_PAD_X))
        frame = int(u * 4) % n
        return dx, dy, frame
    if sprite_id == "star":
        dy = int(round(math.sin(u * 5.0) * 1))
        dx = 0
        frame = int(u * 5) % n
        return dx, dy, frame
    if sprite_id == "dino":
        dy = int(round(abs(math.sin(u * 6.0)) * 1))
        dx = int(round(math.sin(u * 6.0) * 1))
        frame = int(u * 6) % n
        return dx, dy, frame
    if sprite_id == "bot":
        dy = int(round(math.sin(u * 3.2) * 1))
        dx = 0
        frame = int(u * 3) % n
        return dx, dy, frame
    if sprite_id == "kitty":
        dy = int(round(math.sin(u * 2.5) * 1))
        dx = int(round(math.sin(u * 2.5) * 1))
        frame = int(u * 3) % n
        return dx, dy, frame
    # Default idle bob.
    dy = int(round(math.sin(u * 3.0) * 1))
    frame = int(u * 4) % n
    return 0, dy, frame


def label_for(sprite_id: str) -> str:
    if sprite_id in CHEERS:
        return CHEERS[sprite_id]
    entry = SPRITES.get(sprite_id)
    return entry[1] if entry else ""


def draw_wreckage(draw, box_x: int, box_w: int, height: int,
                  sprite_id: str, t: float,
                  cx: int, cy: int) -> None:
    """Paint cracks, sparks, and flying debris around a wrecking character.

    The gag: it looks like the sprite is punching holes in the LED panel.
    Deterministic from (sprite_id, t) so both bumpers stay lively without
    needing per-frame particle state on the renderer.
    """
    phase = (sum(ord(c) for c in sprite_id) % 7) * 0.37
    u = t + phase
    seed = sum(ord(c) for c in sprite_id) * 17 + int(u * 10)

    def rnd(i: int, mod: int) -> int:
        return abs((seed * 1103515245 + i * 12345) >> 8) % max(1, mod)

    # Bright wreckage frame -- a chewed / broken border so the segment
    # reads as "something smashed this part of the board" even at a glance.
    for bx in range(box_x, box_x + box_w):
        if rnd(bx, 3) != 0:
            draw.point((bx, 0), fill=_DEBRIS[rnd(bx, len(_DEBRIS))])
        if rnd(bx + 9, 3) != 0:
            draw.point((bx, height - 1), fill=_DEBRIS[rnd(bx + 1, len(_DEBRIS))])
    for by in range(height):
        if rnd(by + 3, 2) == 0:
            draw.point((box_x, by), fill=_CRACK)
        if rnd(by + 5, 2) == 0:
            draw.point((box_x + box_w - 1, by), fill=_CRACK)

    # Thick jagged cracks radiating from the character.
    for i in range(6):
        angle = (u * 2.0 + i * 0.95) % (math.pi * 2)
        length = 6 + rnd(i, 8)
        for step in range(1, length + 1):
            jx = rnd(i * 10 + step, 3) - 1
            jy = rnd(i * 11 + step, 3) - 1
            x2 = int(round(cx + math.cos(angle) * step * 1.6)) + jx
            y2 = int(round(cy + math.sin(angle) * step * 1.2)) + jy
            if box_x <= x2 < box_x + box_w and 0 <= y2 < height:
                draw.point((x2, y2), fill=_CRACK)
                if x2 + 1 < box_x + box_w:
                    draw.point((x2 + 1, y2), fill=_STATIC)
                if 0 <= y2 + 1 < height:
                    draw.point((x2, y2 + 1), fill=_CRACK)

    # Missing LED chunks (2x2 holes).
    for i in range(8):
        hx = box_x + 1 + rnd(30 + i, max(1, box_w - 3))
        hy = 1 + rnd(40 + i, max(1, height - 3))
        hx = box_x + ((hx - box_x + int(u * (3 + i % 3))) % max(1, box_w - 2))
        for ox, oy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            px, py = hx + ox, hy + oy
            if box_x <= px < box_x + box_w and 0 <= py < height:
                draw.point((px, py), fill=_HOLE)
                # Lit rim so the hole reads against black panel.
                if ox == 0 and oy == 0 and px > box_x and py > 0:
                    draw.point((px - 1, py), fill=_CRACK)

    # Glitchy static bars.
    for bar in range(2):
        gy = rnd(50 + bar, max(1, height))
        for gx in range(box_x, box_x + box_w):
            if rnd(gx + bar * 7, 3) != 0:
                draw.point((gx, gy), fill=_STATIC)

    # Flying debris chunks.
    for i in range(14):
        birth = (u * 3.0 + i * 0.28) % 1.2
        speed = 4 + rnd(60 + i, 5)
        ang = (i * 0.7 + phase) % (math.pi * 2)
        px = int(round(cx + math.cos(ang) * speed * birth * 4))
        py = int(round(cy + math.sin(ang) * speed * birth * 2.5
                       + birth * birth * 12))
        if not (box_x <= px < box_x + box_w and 0 <= py < height):
            continue
        colour = _DEBRIS[rnd(70 + i, len(_DEBRIS))]
        draw.point((px, py), fill=colour)
        if rnd(80 + i, 2) == 0 and px + 1 < box_x + box_w:
            draw.point((px + 1, py), fill=colour)
            if py + 1 < height:
                draw.point((px, py + 1), fill=colour)

    # Impact burst.
    hit = abs(math.sin(u * 5.5))
    if hit < 0.25:
        for i in range(8):
            ang = i * (math.pi / 4) + u
            bx = int(round(cx + math.cos(ang) * (3 + rnd(i, 3))))
            by = int(round(cy + math.sin(ang) * (2 + rnd(i + 1, 2))))
            if box_x <= bx < box_x + box_w and 0 <= by < height:
                draw.point((bx, by), fill=_DEBRIS[i % len(_DEBRIS)])
        for d in range(1, 4):
            for px, py in ((cx + d, cy), (cx - d, cy), (cx, cy + d), (cx, cy - d)):
                if box_x <= px < box_x + box_w and 0 <= py < height:
                    draw.point((px, py), fill=_FLASH)


def blit(draw, x: int, y: int, sprite_id: str, scale: int = 2,
         frame: int = 0) -> Tuple[int, int]:
    """Paint one sprite frame; returns (width, height) including scale."""
    frames = _frames(sprite_id)
    if not frames:
        return 0, 0
    rows = frames[int(frame) % len(frames)]
    scale = max(1, int(scale))
    h = len(rows)
    w = max((len(row) for row in rows), default=0)
    for row_i, row in enumerate(rows):
        for col_i, colour in enumerate(row):
            if colour is None:
                continue
            px = x + col_i * scale
            py = y + row_i * scale
            draw.rectangle(
                [px, py, px + scale - 1, py + scale - 1], fill=colour)
    return w * scale, h * scale


# Short enough to fit a 192px panel with the tiny default font.
FUNNY_GAGS: Tuple[str, ...] = (
    "UH OH",
    "WHOOPS!",
    "HEHEHE",
    "DINO DID IT",
    "NOT A BUG",
    "OOPSIE",
    "PIXELS OUT",
    "CALL MOM",
    "BRB FIXING",
    "IT WAS THE CAT",
    "FAKE ERROR",
    "DON'T TELL DAD",
    "SCREEN GO BOOM",
    "LOL BROKE",
    "WIGGLE TIME",
    "TOASTY!",
    "NOPE NOPE",
    "SCORE? LOL",
)


def funny_gag(t: float) -> str:
    """Rotate a silly one-liner so each interrupt feels fresh."""
    if not FUNNY_GAGS:
        return "UH OH"
    return FUNNY_GAGS[int(t / 10.0) % len(FUNNY_GAGS)]


def _draw_gag_banner(draw, img, text: str, y: Optional[int] = None) -> None:
    """A black bar with a bright joke so kids can actually read it."""
    try:
        from PIL import ImageFont
        font = ImageFont.load_default()
    except Exception:
        return
    w, h = img.size
    text = (text or "UH OH")[:18]
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except Exception:
        tw, th = len(text) * 6, 8
    bar_h = max(th + 4, 10)
    by = h // 2 - bar_h // 2 if y is None else max(0, min(h - bar_h, y))
    draw.rectangle([0, by, w - 1, by + bar_h - 1], fill=(0, 0, 0))
    # Cheeky rainbow-ish edge.
    draw.line([(0, by), (w - 1, by)], fill=Y)
    draw.line([(0, by + bar_h - 1), (w - 1, by + bar_h - 1)], fill=P)
    tx = max(2, (w - tw) // 2)
    ty = by + max(1, (bar_h - th) // 2)
    draw.text((tx, ty), text, font=font, fill=Y)


def _draw_silly_face(draw, cx: int, cy: int, scale: int = 2) -> None:
    """A goofy face in the smash -- fascinates kids more than abstract sparks."""
    # Eyes
    for ox in (-3, 3):
        draw.rectangle(
            [cx + ox * scale - scale, cy - 2 * scale,
             cx + ox * scale + scale - 1, cy - scale],
            fill=W)
        draw.point((cx + ox * scale, cy - 2 * scale), fill=K)
    # Big grin
    for i in range(-4, 5):
        draw.point((cx + i * scale, cy + 2 * scale), fill=Y)
    draw.point((cx - 4 * scale, cy + scale), fill=Y)
    draw.point((cx + 4 * scale, cy + scale), fill=Y)
    # Tongue
    draw.rectangle(
        [cx - scale, cy + 2 * scale, cx + scale - 1, cy + 4 * scale], fill=P)


# Glass crack colours -- bright edge + cool shadow so it reads as a window.
_GLASS = (230, 240, 255)
_GLASS_EDGE = (160, 200, 255)
_GLASS_SHADOW = (40, 50, 70)


def _put(img, x: int, y: int, colour) -> None:
    w, h = img.size
    if 0 <= x < w and 0 <= y < h:
        img.putpixel((x, y), colour)


def _draw_cracked_window(img, cx: int, cy: int, grow: float,
                         seed: int, spokes: int = 10) -> None:
    """Classic smashed-window spiderweb: radial cracks + ring fractures.

    grow 0..1 controls how far the cracks have spread from the impact.
    """
    w, h = img.size
    grow = max(0.15, min(1.0, grow))
    max_r = math.hypot(max(cx, w - cx), max(cy, h - cy))

    def rnd(i: int, mod: int) -> int:
        return abs((seed * 1103515245 + i * 9973) >> 8) % max(1, mod)

    # Impact chip at the hit point (the pebble mark).
    for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1),
                   (1, 1), (-1, 1), (1, -1), (-1, -1)):
        _put(img, cx + dx, cy + dy, _GLASS)
    _put(img, cx, cy, _FLASH)

    # Radial spokes -- real glass cracks wander a little.
    spoke_ends = []
    for i in range(spokes):
        ang = (i / spokes) * math.pi * 2 + (rnd(i, 7) - 3) * 0.04
        length = int(max_r * grow * (0.55 + rnd(i + 3, 45) / 100.0))
        px, py = cx, cy
        for step in range(1, length + 1):
            # Slight wander so lines aren't perfect rays.
            wobble = (rnd(i * 40 + step, 5) - 2) * 0.015
            a = ang + wobble
            nx = int(round(cx + math.cos(a) * step))
            ny = int(round(cy + math.sin(a) * step))
            # Shadow pixel beside the bright crack (glass thickness).
            _put(img, nx + 1, ny, _GLASS_SHADOW)
            _put(img, nx, ny, _GLASS if step % 3 else _GLASS_EDGE)
            # Occasional branch crack (secondary fracture).
            if step > 4 and rnd(i * 50 + step, 18) == 0:
                bang = a + (0.4 if rnd(step, 2) else -0.4)
                for b in range(1, 3 + rnd(step, 4)):
                    bx = int(round(nx + math.cos(bang) * b))
                    by = int(round(ny + math.sin(bang) * b))
                    _put(img, bx, by, _GLASS_EDGE)
            px, py = nx, ny
        spoke_ends.append((px, py, ang))

    # Concentric ring cracks (the spiderweb circles) -- incomplete arcs
    # between spokes, like real shattered glass.
    rings = 2 + int(grow * 3)
    for r_i in range(1, rings + 1):
        radius = int(max_r * grow * (r_i / (rings + 1)))
        if radius < 3:
            continue
        for i in range(spokes):
            # Skip some segments so rings look broken, not perfect circles.
            if rnd(r_i * 20 + i, 5) == 0:
                continue
            a0 = (i / spokes) * math.pi * 2
            a1 = ((i + 1) / spokes) * math.pi * 2
            steps = max(4, int(radius * (a1 - a0)))
            for s in range(steps + 1):
                a = a0 + (a1 - a0) * (s / max(1, steps))
                px = int(round(cx + math.cos(a) * radius))
                py = int(round(cy + math.sin(a) * radius))
                _put(img, px, py, _GLASS)
                _put(img, px, py + 1, _GLASS_SHADOW)


def apply_screen_chaos(img, t: float) -> str:
    """Overlay a cracked-window look on the whole panel.

    The gag: the LED board looks like a pane of glass that got hit --
    spiderweb fractures from an impact, spreading, then a second hit,
    with a silly joke banner so kids laugh at the "broken window".

    Returns the active phase name (for tests / logging).
    """
    try:
        from PIL import ImageDraw as _ID
    except ImportError:  # pragma: no cover
        return "none"

    w, h = img.size
    if w < 8 or h < 8:
        return "none"

    # ~12s loop: window cracks grow, then a second smash, then a joke beat.
    cycle = 12.0
    u = t % cycle
    seed = int(t * 8) + 17
    draw = _ID.Draw(img)
    gag = funny_gag(t)

    def rnd(i: int, mod: int) -> int:
        return abs((seed * 1103515245 + i * 9973) >> 8) % max(1, mod)

    # Primary impact wanders slowly so the crack pattern moves over time.
    cx = int((math.sin(t * 0.35) * 0.35 + 0.5) * (w - 1))
    cy = int((math.cos(t * 0.45) * 0.35 + 0.5) * (h - 1))
    cx = max(8, min(w - 9, cx))
    cy = max(4, min(h - 5, cy))

    if u < 5.0:
        phase = "cracks"
        # Growing spiderweb -- like watching a window crack in slow motion.
        grow = 0.25 + (u / 5.0) * 0.75
        _draw_cracked_window(img, cx, cy, grow, seed, spokes=10)
        if u > 3.5:
            _draw_gag_banner(draw, img, "UH OH", y=1)

    elif u < 8.5:
        phase = "shatter"
        # Full primary web + a second hit elsewhere (window really broke).
        _draw_cracked_window(img, cx, cy, 1.0, seed, spokes=11)
        cx2 = (cx + w // 3 + rnd(2, 20)) % w
        cy2 = (cy + h // 2 + rnd(3, 8)) % h
        _draw_cracked_window(img, cx2, cy2, 0.55 + (u - 5.0) / 7.0, seed + 9,
                             spokes=8)
        # Tiny glass shards (glints), not TV static.
        for i in range(12):
            px = rnd(40 + i, w)
            py = rnd(50 + i, h)
            _put(img, px, py, _GLASS if rnd(i, 2) else _FLASH)
        if u > 7.0:
            _draw_gag_banner(draw, img, gag)

    else:
        phase = "smash"
        # Impact flash + full shattered pane + goofy face in the hole.
        _draw_cracked_window(img, w // 2, h // 2, 1.0, seed + 3, spokes=12)
        _draw_cracked_window(img, cx, cy, 0.7, seed + 5, spokes=7)
        flash = (u - 8.5) < 0.5
        if flash:
            for x in range(w):
                _put(img, x, 0, _FLASH)
                _put(img, x, h - 1, _FLASH)
            for y in range(h):
                _put(img, 0, y, _FLASH)
                _put(img, w - 1, y, _FLASH)
        # Falling glass chips.
        for i in range(16):
            birth = ((t * 3 + i * 0.25) % 1.2)
            px = (cx + rnd(70 + i, w // 2) - w // 4) % w
            py = int((cy + birth * birth * h)) % h
            _put(img, px, py, _GLASS)
            _put(img, px + 1, py, _GLASS_EDGE)
        _draw_silly_face(draw, w // 2, max(6, h // 2 - 2), scale=2)
        _draw_gag_banner(draw, img, gag, y=h - 11)

    return phase

