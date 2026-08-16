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
    "comet": "Blast!",
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
