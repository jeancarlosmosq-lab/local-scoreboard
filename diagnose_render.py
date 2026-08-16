#!/usr/bin/env python3
"""
Render actual strip segments using the plugin's own code and the real BDF
fonts on this Pi, and report exactly what happens -- pixel by pixel.

Every centering fix so far has been tested against this sandbox's fallback
font, which turns out to behave differently from the real compiled BDF
fonts this plugin loads on real hardware: a quick comparison found the
fallback font has 2-3px of built-in leading above each glyph, while a
compiled BDF font has none at all. That gap is large enough to explain why
fixes that measured correctly in testing do not look right here.

This script bypasses that gap entirely: it imports strip_renderer.py
directly and calls its real drawing methods, using whichever fonts actually
load from the real font files on this machine. No simulation, no
stand-in font.

Run from inside the plugin's own folder, so the import can find it:

    cd ~/LEDMatrix/plugin-repos/local-scoreboard
    python3 diagnose_render.py

Writes a few PNG files into the current directory so they can be pulled off
the Pi and viewed, and prints the exact pixel measurements for each one.
Needs nothing beyond what the plugin itself already needs (Pillow).
"""

import logging
import sys

logging.basicConfig(level=logging.WARNING)


def main():
    try:
        from strip_renderer import StripRenderer
    except ImportError as e:
        print(f"Could not import strip_renderer.py: {e}")
        print("Run this from inside the local-scoreboard plugin folder:")
        print("  cd ~/LEDMatrix/plugin-repos/local-scoreboard")
        print("  python3 diagnose_render.py")
        return 1

    from PIL import Image, ImageDraw

    class FakeMatrix:
        def __init__(self, w, h):
            self.width, self.height = w, h

    class FakeDisplay:
        def __init__(self, w=192, h=32):
            self.matrix = FakeMatrix(w, h)
            self.image = Image.new("RGB", (w, h))
            self.font = None

        def update_display(self):
            pass

    logger = logging.getLogger("diagnose")
    display = FakeDisplay(192, 32)

    def new_renderer():
        # build_strip() caches its result and, by design, refuses to rebuild
        # more than once every 5 seconds -- a real production safeguard
        # (rebuilding every frame would stall the Pi) that turns into a bug
        # here: calling build_strip() several times in a row on one instance
        # returns the *first* call's cached image for every call after it,
        # silently. A fresh renderer per segment has no cache to be stale.
        return StripRenderer(display, {}, logger)

    renderer = new_renderer()

    print("Which font files actually loaded:")
    print(f"  {renderer.font_report()}")
    print()

    draw = ImageDraw.Draw(Image.new("RGB", (192, 32)))
    font, row_h = renderer._fit_font(draw, 4, 32)
    print(f"Shared 4-row body font: row_h={row_h}")
    bbox = draw.textbbox((0, 0), "0", font=font)
    print(f"  textbbox for '0': {bbox}  (top={bbox[1]}, bottom={bbox[3]})")

    big_font, big_row_h = renderer._largest_fit(draw, 2, 30)
    print(f"Clock's 2-row font: row_h={big_row_h}")
    bbox2 = draw.textbbox((0, 0), "0", font=big_font)
    print(f"  textbbox for '0': {bbox2}  (top={bbox2[1]}, bottom={bbox2[3]})")
    print()

    def measure_strip(name, strip):
        px = strip.load()
        lit = [(x, y) for y in range(strip.height) for x in range(strip.width)
              if px[x, y] != (0, 0, 0)]
        if not lit:
            print(f"  {name}: BLANK -- nothing drew at all")
            return
        top = min(y for x, y in lit)
        bottom = max(y for x, y in lit)
        margin_bottom = strip.height - 1 - bottom
        print(f"  {name}: top_margin={top}  bottom_margin={margin_bottom}  "
              f"(width={strip.width})")
        fname = f"diag_{name}.png"
        strip.resize((strip.width * 4, strip.height * 4), Image.NEAREST).save(fname)
        print(f"    saved {fname}")

    print("Rendering real segments with the plugin's own code:\n")

    rows = [{"rank": i, "short_name": f"P.{i}", "team": "NYY", "value": "41"}
           for i in range(1, 4)]
    lb_strip = new_renderer().build_strip([], leaderboards=[("AL HR Leaders", rows, "HR")])
    measure_strip("leaderboard", lb_strip)

    award_rows = [{"rank": i, "short_name": f"P.{i}", "team": "NYY"}
                 for i in range(1, 4)]
    aw_strip = new_renderer().build_strip([], awards=[("AL MVP", award_rows)])
    measure_strip("awards", aw_strip)

    from datetime import datetime
    clock_strip = new_renderer().build_strip([], clock=datetime.now())
    measure_strip("clock", clock_strip)

    weather = {"label": "Bayonne", "units": "F", "now_temp": 78,
              "now_feels": 85, "now_condition": "CLEAR", "alerts": [],
              "hourly": [{"name": "8P", "temp": 77, "condition": "Clear"},
                        {"name": "9P", "temp": 75, "condition": "Rain"}]}
    weather_strip = new_renderer().build_strip([], weather=weather)
    measure_strip("weather", weather_strip)

    # The overall weather margin check above does not, on its own, prove
    # the hourly forecast icons actually drew anything -- the rest of
    # the segment can still hit the right top/bottom rows even if those
    # specific icons are blank. Render one forecast column in isolation and
    # count its own lit pixels directly.
    from PIL import ImageDraw as _FCID
    fc_img = Image.new("RGB", (40, 32), (0, 0, 0))
    fc_draw = _FCID.Draw(fc_img)
    fc_font, fc_row_h = renderer._fit_font(fc_draw, 4, 32)
    renderer._draw_forecast_column(
        fc_draw, 2, {"name": "8P", "temp": 77, "condition": "Clear"},
        fc_font, fc_row_h, "F")
    fc_px = fc_img.load()
    fc_lit = sum(1 for y in range(32) for x in range(40) if fc_px[x, y] != (0, 0, 0))
    print(f"\n  forecast column alone: {fc_lit} lit pixels total "
         f"({'OK' if fc_lit > 15 else 'LOW -- icon may be missing, only label/temp text drew'})")
    fc_img.resize((40 * 6, 32 * 6), Image.NEAREST).save("diag_forecast_column.png")
    print("    saved diag_forecast_column.png")

    # Specifically check the sun icon draws something at all with the real
    # font/colour setup.
    icon_img = Image.new("RGB", (18, 18), (0, 0, 0))
    icon_draw = ImageDraw.Draw(icon_img)
    renderer._draw_weather_icon(icon_draw, 1, 1, 14, "clear")
    icon_px = icon_img.load()
    icon_lit = sum(1 for y in range(18) for x in range(18)
                  if icon_px[x, y] != (0, 0, 0))
    print(f"\n  sun icon alone: {icon_lit} lit pixels "
         f"({'OK, drew something' if icon_lit else 'BLANK -- drew nothing at all'})")
    icon_img.resize((18 * 8, 18 * 8), Image.NEAREST).save("diag_sun_icon.png")
    print("    saved diag_sun_icon.png")

    print("\n" + "=" * 60)
    print("Copy the diag_*.png files off the Pi and look at them directly,")
    print("and paste everything printed above back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
