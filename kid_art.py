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

# Soft-shaded flyovers: paint large with gradients, then LANCZOS-downscale.
# This is about as realistic as a 32px LED row can look. No moon.
# Handmade procedural art -- not licensed characters.

FLYER_ORDER: Tuple[str, ...] = (
    "soccer", "hawk", "baseball", "dolphin", "cardinal", "fox",
)
# Membership map so existing tests / callers can do `id in FLYERS`.
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


def _shade_disk(px: int, py: int, cx: float, cy: float, r: float,
                light: Tuple[int, int, int], mid: Tuple[int, int, int],
                dark: Tuple[int, int, int],
                lx: float = -0.45, ly: float = -0.55
                ) -> Optional[Tuple[int, int, int]]:
    """Sphere-ish shading; None outside the disk."""
    dx = (px + 0.5 - cx) / r
    dy = (py + 0.5 - cy) / r
    d2 = dx * dx + dy * dy
    if d2 > 1.0:
        return None
    # Soft edge alpha handled by caller; here just RGB.
    # Lambert-ish: brighter toward light direction.
    nz = math.sqrt(max(0.0, 1.0 - d2))
    # Light vector (normalized-ish).
    llen = math.sqrt(lx * lx + ly * ly + 1.0)
    ndotl = (dx * lx + dy * ly + nz * 1.0) / llen
    ndotl = max(0.0, min(1.0, (ndotl + 0.15) / 1.15))
    if ndotl > 0.72:
        return _mix(mid, light, (ndotl - 0.72) / 0.28)
    if ndotl > 0.35:
        return _mix(dark, mid, (ndotl - 0.35) / 0.37)
    return _mix((12, 12, 16), dark, ndotl / 0.35)


def _render_soccer(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    hi = max(36, size * 3)
    im = _Im.new("RGBA", (hi, hi), (0, 0, 0, 0))
    cx = cy = (hi - 1) / 2.0
    r = hi * 0.46
    rot = progress * math.pi * 2 + frame * 0.4
    # Base sphere.
    light, mid, dark = (250, 250, 255), (210, 210, 220), (120, 125, 135)
    pix = im.load()
    for y in range(hi):
        for x in range(hi):
            col = _shade_disk(x, y, cx, cy, r, light, mid, dark)
            if col is None:
                continue
            dx = (x + 0.5 - cx) / r
            dy = (y + 0.5 - cy) / r
            # Soft rim alpha.
            d = math.sqrt(dx * dx + dy * dy)
            a = 255 if d < 0.92 else _clamp(255 * (1.0 - d) / 0.08)
            # Pentagon-ish dark patches via angular bands.
            ang = math.atan2(dy, dx) + rot
            ring = abs(math.sin(ang * 2.5 + d * 6.0))
            if d < 0.88 and (ring > 0.82 or (d < 0.28 and ring > 0.55)):
                col = _mix(col, (25, 25, 30), 0.85)
            # Specular.
            if (dx + 0.35) ** 2 + (dy + 0.4) ** 2 < 0.04:
                col = _mix(col, (255, 255, 255), 0.9)
            pix[x, y] = (col[0], col[1], col[2], a)
    return im.resize((size, size), resample=_RESAMPLE)


def _render_baseball(size: int, frame: int, progress: float):
    from PIL import Image as _Im
    hi = max(36, size * 3)
    im = _Im.new("RGBA", (hi, hi), (0, 0, 0, 0))
    cx = cy = (hi - 1) / 2.0
    r = hi * 0.46
    rot = progress * math.pi * 2 + frame * 0.3
    light, mid, dark = (255, 252, 245), (235, 230, 220), (170, 165, 155)
    pix = im.load()
    for y in range(hi):
        for x in range(hi):
            col = _shade_disk(x, y, cx, cy, r, light, mid, dark,
                              lx=-0.4, ly=-0.5)
            if col is None:
                continue
            dx = (x + 0.5 - cx) / r
            dy = (y + 0.5 - cy) / r
            d = math.sqrt(dx * dx + dy * dy)
            a = 255 if d < 0.92 else _clamp(255 * (1.0 - d) / 0.08)
            # Red stitches: two opposing arcs.
            for sign in (-1, 1):
                # Rotate point.
                ang = rot * sign
                rx = dx * math.cos(ang) - dy * math.sin(ang)
                ry = dx * math.sin(ang) + dy * math.cos(ang)
                # Arc near x = ±0.35
                target = 0.38 * sign
                if abs(rx - target) < 0.07 and abs(ry) < 0.72:
                    # Dashed stitches.
                    if int((ry + 1) * 14 + frame) % 3 != 0:
                        col = _mix(col, (200, 45, 55), 0.9)
            if (dx + 0.32) ** 2 + (dy + 0.38) ** 2 < 0.035:
                col = _mix(col, (255, 255, 255), 0.85)
            pix[x, y] = (col[0], col[1], col[2], a)
    return im.resize((size, size), resample=_RESAMPLE)


def _render_hawk(size: int, frame: int, progress: float):
    from PIL import Image as _Im, ImageDraw as _ID
    # Wide wings: size is height; width ~ 1.7x
    h = max(20, size)
    w = max(32, int(round(h * 1.75)))
    hi_h = h * 3
    hi_w = w * 3
    im = _Im.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
    d = _ID.Draw(im)
    cx, cy = hi_w // 2, hi_h // 2
    flap = 1.0 if frame % 2 == 0 else -1.0
    # Wings (gradient via stacked polygons).
    wing_y = cy - int(10 * flap)
    for i, col in enumerate((
        (90, 55, 30), (140, 95, 55), (185, 140, 90), (210, 175, 125),
    )):
        spread = 18 + i * 10
        lift = int((8 - i * 2) * flap)
        d.polygon([
            (cx, cy),
            (cx - spread * 4, wing_y - lift - i * 3),
            (cx - spread * 2, cy + 6),
        ], fill=col + (230,))
        d.polygon([
            (cx, cy),
            (cx + spread * 4, wing_y - lift - i * 3),
            (cx + spread * 2, cy + 6),
        ], fill=col + (230,))
    # Body
    d.ellipse([cx - 18, cy - 10, cx + 18, cy + 14], fill=(160, 110, 65, 255))
    d.ellipse([cx - 12, cy - 6, cx + 14, cy + 10], fill=(200, 160, 110, 255))
    # Head + beak
    d.ellipse([cx + 10, cy - 12, cx + 28, cy + 4], fill=(175, 125, 80, 255))
    d.polygon([(cx + 26, cy - 4), (cx + 40, cy - 1), (cx + 26, cy + 2)],
              fill=(230, 160, 50, 255))
    # Eye
    d.ellipse([cx + 18, cy - 8, cx + 23, cy - 3], fill=(20, 20, 25, 255))
    # Tail
    d.polygon([(cx - 16, cy + 4), (cx - 34, cy + 16), (cx - 10, cy + 12)],
              fill=(120, 70, 40, 240))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_dolphin(size: int, frame: int, progress: float):
    from PIL import Image as _Im, ImageDraw as _ID
    h = max(18, size)
    w = max(36, int(round(h * 2.0)))
    hi_h, hi_w = h * 3, w * 3
    im = _Im.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
    d = _ID.Draw(im)
    cx, cy = hi_w // 2, hi_h // 2 + (2 if frame % 2 else -2)
    # Body gradient via concentric ellipses.
    for i, col in enumerate((
        (55, 75, 95, 255), (90, 115, 135, 255),
        (140, 160, 175, 255), (190, 205, 215, 255),
    )):
        pad = 8 * i
        d.ellipse([cx - 55 + pad // 2, cy - 18 + i,
                   cx + 40 - pad // 3, cy + 18 - i], fill=col)
    # Belly
    d.ellipse([cx - 30, cy + 2, cx + 28, cy + 16], fill=(230, 235, 240, 220))
    # Dorsal fin
    d.polygon([(cx - 5, cy - 14), (cx + 2, cy - 36), (cx + 12, cy - 12)],
              fill=(100, 125, 145, 255))
    # Tail flukes
    d.polygon([(cx - 52, cy), (cx - 72, cy - 14), (cx - 60, cy + 2)],
              fill=(110, 130, 150, 255))
    d.polygon([(cx - 52, cy), (cx - 72, cy + 14), (cx - 60, cy - 2)],
              fill=(90, 110, 130, 255))
    # Rostrum + eye
    d.ellipse([cx + 28, cy - 6, cx + 55, cy + 8], fill=(150, 170, 185, 255))
    d.ellipse([cx + 34, cy - 4, cx + 40, cy + 2], fill=(20, 25, 30, 255))
    # Soft splash sparkles under
    if frame % 2:
        for sx, sy in ((cx - 10, cy + 22), (cx + 8, cy + 26), (cx - 28, cy + 24)):
            d.ellipse([sx, sy, sx + 4, sy + 3], fill=(160, 210, 255, 180))
    return im.resize((w, h), resample=_RESAMPLE)


def _render_cardinal(size: int, frame: int, progress: float):
    from PIL import Image as _Im, ImageDraw as _ID
    h = max(20, size)
    w = max(28, int(round(h * 1.35)))
    hi_h, hi_w = h * 3, w * 3
    im = _Im.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
    d = _ID.Draw(im)
    cx, cy = hi_w // 2, hi_h // 2
    hop = 4 if frame % 2 else 0
    cy -= hop
    # Body
    for i, col in enumerate((
        (120, 20, 30, 255), (180, 35, 45, 255), (230, 60, 70, 255),
        (255, 110, 115, 255),
    )):
        d.ellipse([cx - 22 + i * 2, cy - 10 + i,
                   cx + 18 - i, cy + 16 - i], fill=col)
    # Crest
    d.polygon([(cx + 6, cy - 10), (cx + 14, cy - 28), (cx + 18, cy - 8)],
              fill=(200, 40, 50, 255))
    # Head + beak + eye
    d.ellipse([cx + 8, cy - 12, cx + 28, cy + 6], fill=(220, 55, 65, 255))
    d.polygon([(cx + 26, cy - 2), (cx + 40, cy + 2), (cx + 26, cy + 5)],
              fill=(40, 35, 35, 255))
    d.ellipse([cx + 16, cy - 6, cx + 21, cy - 1], fill=(15, 15, 20, 255))
    # Wing
    d.ellipse([cx - 18, cy - 4, cx + 4, cy + 12], fill=(140, 25, 35, 255))
    # Tail / feet
    d.polygon([(cx - 20, cy + 6), (cx - 34, cy + 4), (cx - 22, cy + 14)],
              fill=(150, 30, 40, 255))
    d.line([(cx - 4, cy + 16), (cx - 4, cy + 26)], fill=(60, 40, 25, 255), width=3)
    d.line([(cx + 4, cy + 16), (cx + 6, cy + 26)], fill=(60, 40, 25, 255), width=3)
    return im.resize((w, h), resample=_RESAMPLE)


def _render_fox(size: int, frame: int, progress: float):
    from PIL import Image as _Im, ImageDraw as _ID
    h = max(18, size)
    w = max(34, int(round(h * 1.8)))
    hi_h, hi_w = h * 3, w * 3
    im = _Im.new("RGBA", (hi_w, hi_h), (0, 0, 0, 0))
    d = _ID.Draw(im)
    cx, cy = hi_w // 2, hi_h // 2
    # Body
    for i, col in enumerate((
        (140, 70, 20, 255), (200, 110, 40, 255), (255, 150, 60, 255),
    )):
        d.ellipse([cx - 40 + i * 3, cy - 10 + i,
                   cx + 20 - i * 2, cy + 14 - i], fill=col)
    # Head
    d.ellipse([cx + 12, cy - 14, cx + 40, cy + 8], fill=(240, 140, 50, 255))
    d.polygon([(cx + 18, cy - 12), (cx + 22, cy - 28), (cx + 30, cy - 10)],
              fill=(230, 120, 40, 255))
    d.polygon([(cx + 28, cy - 10), (cx + 36, cy - 26), (cx + 38, cy - 8)],
              fill=(230, 120, 40, 255))
    # White muzzle + nose + eye
    d.ellipse([cx + 24, cy - 2, cx + 40, cy + 10], fill=(245, 240, 230, 255))
    d.ellipse([cx + 36, cy + 2, cx + 42, cy + 7], fill=(30, 25, 25, 255))
    d.ellipse([cx + 22, cy - 8, cx + 27, cy - 3], fill=(20, 20, 25, 255))
    # Legs (run cycle)
    if frame % 2 == 0:
        legs = [(cx - 28, cy + 12), (cx - 10, cy + 12),
                (cx + 2, cy + 12), (cx + 14, cy + 12)]
    else:
        legs = [(cx - 22, cy + 12), (cx - 14, cy + 12),
                (cx - 2, cy + 12), (cx + 10, cy + 12)]
    for lx, ly in legs:
        d.line([(lx, ly), (lx - 2, ly + 16)], fill=(90, 50, 25, 255), width=4)
    # Tail
    d.polygon([(cx - 36, cy), (cx - 70, cy - 8), (cx - 60, cy + 10)],
              fill=(255, 160, 70, 255))
    d.ellipse([cx - 72, cy - 10, cx - 58, cy + 2], fill=(250, 245, 235, 255))
    return im.resize((w, h), resample=_RESAMPLE)


_FLYER_RENDERERS = {
    "soccer": _render_soccer,
    "baseball": _render_baseball,
    "hawk": _render_hawk,
    "dolphin": _render_dolphin,
    "cardinal": _render_cardinal,
    "fox": _render_fox,
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
        "soccer": 1.0, "baseball": 1.0, "hawk": 1.75,
        "dolphin": 2.0, "cardinal": 1.35, "fox": 1.8,
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


def apply_flyer(img, t: float, interval: float = 10.0,
                flight: float = 2.8) -> Optional[str]:
    """Soft-shaded subjects fly across with depth (closer mid-screen)."""
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

    depth = math.sin(progress * math.pi)
    if cycle_i % 2 == 1:
        depth = max(0.15, min(1.0, 0.35 + 0.65 * (1.0 - progress)))

    # Target on-panel height: ~12px far → ~26px close.
    fh = max(12, min(h - 2, 12 + int(round(depth * (min(26, h - 2) - 12)))))
    sprite = render_flyer(flyer_id, fh, frame=frame, progress=progress)
    if sprite is None:
        return None
    fw, fh = sprite.size

    going_right = (cycle_i % 2 == 0)
    travel = w + fw + 4
    if going_right:
        x = int(-fw + progress * travel)
    else:
        x = int(w - progress * travel)
        # Face travel direction: flip horizontal when going left.
        sprite = sprite.transpose(_FLIP_LR)

    bob = int(round(math.sin(progress * math.pi * 3) * (1 + fh // 10)))
    if flyer_id in ("hawk",):
        bob = int(round(math.sin(progress * math.pi * 5) * (1 + fh // 12)))
    elif flyer_id == "dolphin":
        bob = int(round(math.sin(progress * math.pi * 2) * (fh // 8))) - 1
    elif flyer_id in ("soccer", "baseball"):
        bob = int(round(math.sin(progress * math.pi * 6) * (fh // 10)))
    elif flyer_id == "fox":
        bob = int(round(abs(math.sin(progress * math.pi * 8)) * (fh // 10)))
    elif flyer_id == "cardinal":
        bob = int(round(abs(math.sin(progress * math.pi * 7)) * (fh // 12)))
    y = max(0, min(h - fh, (h - fh) // 2 + bob))

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

