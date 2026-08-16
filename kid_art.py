"""Original kid-friendly pixel bumpers for the scrolling strip.

These are tiny handmade sprites -- a rocket, a dino, a bot, a kitty, a
star hero, a soccer ball, a comet -- drawn for a ~32px LED panel. They are
NOT Nintendo, Pokemon, or any other licensed characters; those are someone
else's IP. The idea is the same (a little bit of fun art every so often)
without shipping trademarked mascots.
"""

from __future__ import annotations

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

Sprite = Tuple[str, str, Tuple[Tuple[Optional[Tuple[int, int, int]], ...], ...]]

# Each sprite: (id, short cheer label, rows of pixels). Designed at ~14-16
# px tall so they sit comfortably on a 32-row panel with a label beside them.
SPRITES: Dict[str, Sprite] = {
    "rocket": (
        "rocket",
        "Zoom!",
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
    ),
    "dino": (
        "dino",
        "Roar!",
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
    ),
    "bot": (
        "bot",
        "Beep!",
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
    ),
    "kitty": (
        "kitty",
        "Meow!",
        (
            (P, _, _, _, _, _, P, _),
            (_, P, P, P, P, P, _, _),
            (P, W, P, P, P, W, P, _),
            (P, P, P, N, P, P, P, _),
            (_, P, P, P, P, P, _, _),
            (_, _, P, P, P, _, _, _),
            (_, P, _, _, _, P, _, _),
        ),
    ),
    "star": (
        "star",
        "Go!",
        (
            (_, _, _, Y, _, _, _, _),
            (_, _, Y, Y, Y, _, _, _),
            (Y, Y, Y, W, Y, Y, Y, _),
            (_, Y, Y, Y, Y, Y, _, _),
            (_, _, O, Y, O, _, _, _),
            (_, _, Y, _, Y, _, _, _),
            (_, Y, _, _, _, Y, _, _),
        ),
    ),
    "ball": (
        "ball",
        "Kick!",
        (
            (_, _, W, W, W, _, _, _),
            (_, W, K, W, K, W, _, _),
            (W, K, W, W, W, K, W, _),
            (W, W, W, K, W, W, W, _),
            (W, K, W, W, W, K, W, _),
            (_, W, K, W, K, W, _, _),
            (_, _, W, W, W, _, _, _),
        ),
    ),
    "comet": (
        "comet",
        "Whoosh!",
        (
            (C, C, _, _, _, _, Y, _),
            (_, C, C, C, _, Y, Y, Y),
            (_, _, B, B, B, Y, W, Y),
            (_, _, _, B, B, Y, Y, Y),
            (_, _, _, _, C, C, Y, _),
            (_, _, _, _, _, C, _, _),
        ),
    ),
    "fish": (
        "fish",
        "Splash!",
        (
            (_, _, T, T, T, _, _, _),
            (_, T, W, T, T, T, T, _),
            (T, T, T, T, T, T, _, R),
            (_, T, T, T, T, T, T, _),
            (_, _, T, T, T, _, _, _),
        ),
    ),
}

SPRITE_ORDER: Tuple[str, ...] = (
    "rocket", "dino", "bot", "kitty", "star", "ball", "comet", "fish",
)


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


def sprite_size(sprite_id: str, scale: int = 2) -> Tuple[int, int]:
    entry = SPRITES.get(sprite_id)
    if not entry:
        return 0, 0
    _sid, _label, rows = entry
    scale = max(1, int(scale))
    h = len(rows)
    w = max((len(row) for row in rows), default=0)
    return w * scale, h * scale


def blit(draw, x: int, y: int, sprite_id: str, scale: int = 2) -> Tuple[int, int]:
    """Paint one sprite; returns (width, height) in pixels including scale."""
    entry = SPRITES.get(sprite_id)
    if not entry:
        return 0, 0
    _sid, _label, rows = entry
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


def label_for(sprite_id: str) -> str:
    entry = SPRITES.get(sprite_id)
    return entry[1] if entry else ""
