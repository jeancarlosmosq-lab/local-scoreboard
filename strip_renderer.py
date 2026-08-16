"""
Team strip renderer.

One wide image per team, scrolled right to left, carrying everything about
that team in order: a banner with the logo at full panel height, then any
live game with its situation drawn out, then finals, then upcoming games,
then the notable performances.

Keeping a team's information together is the point. Cycling card by card
across five teams means a Yankees score, then a Knicks score, then a Giants
fixture -- you never get a picture of any one of them. A strip gives you the
whole team in one pass and then moves on.

The live segment is sport-specific because what a fan watches for is:

    baseball    a diamond with the runners, the count, the outs
    football    down and distance, and who has the ball
    basketball  the period and the clock
    soccer      the live minute

Rendering is a composite: the strip is built once, cached against its data,
and each frame is a crop. Rebuilding a 600px image every frame would be
ruinous on a Pi that is also driving the matrix.
"""

import logging
import math
import multiprocessing
import os
import sys
import threading
import time
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

from espn_data_source import ESPNGamesSource, abbr_group
import moon_phase

try:
    from PIL import BdfFontFile
except ImportError:  # pragma: no cover
    BdfFontFile = None


MIN_LEGIBLE_ROW_H = 6

# Captured at import time, while sys.modules still holds this module under
# its own bare name -- the host framework's plugin loader renames that
# entry to a namespaced key (e.g. "_plg_local-scoreboard_strip_renderer")
# immediately after loading, specifically so two different plugins that
# happen to both ship a "strip_renderer.py" don't collide in sys.modules.
# That's the right call for the framework in general, but it breaks
# pickling _compose_worker_main by reference: multiprocessing's spawn start
# method pickles the Process target as (module_name, qualname) and verifies
# it by looking up sys.modules[module_name] at pickle time, which fails
# ("not the same object as strip_renderer._compose_worker_main") once the
# bare "strip_renderer" key is gone. _ensure_compose_process briefly
# restores this exact module under its own name in sys.modules for just the
# call to Process.start() (which pickles synchronously, in the calling
# thread, before it returns), then removes it again immediately afterward
# so the framework's own namespace isolation still holds the rest of the
# time.
_THIS_MODULE = sys.modules[__name__]


def _compose_worker_main(conn, width: int, height: int,
                         allow_download: bool) -> None:
    """Runs in a separate OS process, not a thread.

    A background *thread* still shares the main process's GIL -- composing
    a rebuilt strip is real CPU-bound work (PIL drawing calls), and a
    thread doing that work still starves the main thread of GIL time to
    run its own Python bytecode, confirmed on the Pi as a periodic pause
    in the scroll no amount of throttling or caching fully removed,
    because the underlying cost never went away, only how often it was
    paid. A separate process has its own interpreter and its own GIL, so
    composing here genuinely cannot block the render loop's own thread the
    way a background thread could -- only real OS-level CPU contention
    remains, which the matrix's own isolated core (isolcpus) is already
    set up to be shielded from.

    Owns its own StripRenderer and TeamLogoManager -- fully independent of
    the caller's, including its own font/logo caches, warmed the same way
    over the life of the process. teams/logo cache_dir default to the
    same on-disk locations _compose_strip already resolves relative to
    this same file, so nothing needs to cross the process boundary except
    plain, already-picklable request data (dicts, lists, a datetime) in,
    and a PIL Image plus the clock-state tuple back -- over a Pipe, not a
    Queue; see the comment by self._compose_conn for why.
    """
    from logo_manager import TeamLogoManager

    worker_logger = logging.getLogger("strip_compose_worker")

    class _WorkerDisplay:
        def __init__(self, w, h):
            self.width = w
            self.height = h

    logos = TeamLogoManager(worker_logger, allow_download=allow_download)
    renderer = StripRenderer(
        _WorkerDisplay(width, height), {}, worker_logger, logo_manager=logos)

    while True:
        try:
            request = conn.recv()
        except (EOFError, OSError):
            return
        if request is None:
            return
        try:
            strip, clock_state = renderer._compose_strip(*request)
            if clock_state is not None:
                # The font object in the middle of clock_state can't
                # safely cross a process boundary -- confirmed the hard
                # way, pickling a PIL FreeTypeFont here and unpickling it
                # in the parent raised "cannot open resource" trying to
                # reopen the font file from within a context that isn't
                # this worker's own. The parent resolves an equivalent
                # font itself instead (same panel height, same
                # _largest_fit call, same font file -- deterministically
                # the same result), so only the box and the clock text,
                # both plain data, need to make the trip.
                box, _font, clock_text = clock_state
                clock_state = (box, None, clock_text)
            conn.send(("ok", strip, clock_state))
        except Exception as e:
            worker_logger.error(
                "Background strip compose failed in worker process: %s",
                e, exc_info=True)
            conn.send(("error", str(e), None))


class StripRenderer:
    """Builds and scrolls a per-team strip."""

    FONT_LADDER = ["5x7.bdf", "4x6.bdf", "6x10.bdf"]
    FALLBACK_SIZES = [8, 7, 6, 5, 4]

    # Colours. Bright by default: on an emissive panel a mid grey is not
    # subdued, it is simply dim.
    LABEL = (255, 255, 255)
    DIM = (150, 150, 160)
    VALUE = (0, 220, 255)
    LIVE = (0, 230, 90)
    UPCOMING = (255, 200, 0)
    FINAL = (200, 200, 210)
    DIVIDER = (60, 55, 25)
    RIVALRY = (255, 70, 25)
    STREAK_WIN = (0, 230, 90)
    STREAK_LOSS = (255, 90, 70)

    # Medal colours for a leaderboard's top 3 ranks.
    GOLD = (255, 215, 0)
    SILVER = (200, 200, 210)
    BRONZE = (205, 127, 50)
    MEDALS = (GOLD, SILVER, BRONZE)

    # Each MLB team's own primary brand colour, for the abbreviation beside
    # a leaderboard name -- lifted to a 175 floor on the brightest channel,
    # the same proportional brightening _lift_dark already applies to
    # logos, and for the identical reason: several teams' real primaries
    # (most of the AL/NL navy blues -- Tigers, Twins, Mariners, Yankees
    # among them) are too dark to read at a few pixels of text on an unlit
    # panel. A team at or near true black (White Sox, Pirates, Giants,
    # Athletics' green is dark too) uses its brighter secondary instead,
    # since no amount of lifting rescues a colour with nothing in it.
    TEAM_COLORS = {
        "ARI": (175, 26, 50), "ATL": (206, 17, 65), "BAL": (223, 70, 1),
        "BOS": (189, 48, 57), "CHC": (18, 66, 175), "CWS": (196, 206, 212),
        "CHW": (196, 206, 212), "CIN": (198, 1, 31), "CLE": (0, 105, 175),
        "COL": (87, 87, 175), "DET": (32, 95, 175), "HOU": (0, 80, 175),
        "KC": (0, 90, 175), "LAA": (186, 0, 33), "LAD": (0, 100, 175),
        "MIA": (0, 163, 224), "MIL": (42, 93, 175), "MIN": (0, 81, 175),
        "NYM": (0, 69, 175), "NYY": (0, 62, 175), "ATH": (239, 178, 61),
        "OAK": (239, 178, 61), "PHI": (232, 24, 40), "PIT": (253, 184, 39),
        "SD": (255, 196, 37), "SF": (253, 90, 30), "SEA": (24, 89, 175),
        "STL": (196, 30, 58), "TB": (143, 188, 230), "TEX": (0, 72, 175),
        "TOR": (23, 91, 175), "WSH": (175, 0, 3),
    }

    # One row of light at the very top and bottom of every segment, on every
    # segment type -- the rule the whole strip is measured against.
    MARGIN = 1
    BASE_ON = (255, 200, 0)
    BASE_OFF = (55, 50, 20)
    OUT_ON = (255, 90, 70)
    OUT_OFF = (55, 25, 20)

    def __init__(self, display_manager, config: Dict[str, Any],
                 logger: logging.Logger, logo_manager=None,
                 use_process: bool = False):
        self.display_manager = display_manager
        self.config = config or {}
        self.logger = logger
        self.logo_manager = logo_manager
        self.kid_friendly = bool(self.config.get("kid_friendly", False))
        # Off by default -- every existing caller (all of test_offline.py
        # included) gets the original thread-based background build
        # unchanged. manager.py opts the real, on-device renderer in;
        # spawning an OS process per StripRenderer instance would make the
        # test suite (which constructs dozens of them) far slower for no
        # benefit, since nothing there is racing the real matrix output
        # for CPU. See _compose_worker_main's own docstring for why a
        # process, not just a thread, is what this actually needs.
        self._use_process = use_process
        self._compose_process: Optional[multiprocessing.Process] = None
        # A Pipe, not a Queue -- Connection.send() pickles synchronously
        # in the calling thread, so a request that can't be pickled (a
        # stray lambda or lock ending up in the weather dict, say) raises
        # right there and is caught the same way any other dispatch
        # failure is. Queue.put() defers pickling to its own internal
        # feeder thread instead; a pickling failure there is silently
        # lost, and the response side would then wait forever for an
        # answer to a request the worker never actually received --
        # confirmed the hard way, once, before switching to Pipe.
        self._compose_conn = None

        try:
            self.width = display_manager.matrix.width
            self.height = display_manager.matrix.height
        except AttributeError:
            self.width = getattr(display_manager, "width", 128)
            self.height = getattr(display_manager, "height", 32)

        self._font_cache: Dict[Any, Any] = {}
        # _fit_font/_largest_fit scan every FONT_LADDER candidate and
        # measure it, but the result only ever depends on (rows,
        # available) -- the panel's own height never changes mid-session,
        # so this was rescanning and remeasuring the same candidates on
        # every single composition. Real, avoidable work on the render
        # path's own critical section (composing a rebuilt strip already
        # competes with the matrix output for CPU on a Pi).
        self._fit_font_cache: Dict[Any, Any] = {}
        self._largest_fit_cache: Dict[Any, Any] = {}
        self._strip_cache: Optional[Image.Image] = None
        self._strip_key = None
        # A rebuilt strip waits here until the scroll reaches its seam. Data
        # changes constantly -- a score, an out, a count -- and swapping the
        # image mid-pass shifts everything after the changed segment sideways
        # under the reader. Holding the new one until the join means an update
        # is only ever adopted while it is off-screen.
        self._pending: Optional[Image.Image] = None
        self._pending_key = None
        # Travels with _pending, applied to the live _clock_box/_clock_font/
        # _clock_shown only at the same moment adopt_pending() swaps the
        # image itself in -- see _draw_clock's own comment for why this
        # can't just be written during composition.
        self._pending_clock_state = None
        # Composing the strip costs tens of milliseconds -- hundreds on a Pi
        # driving a matrix -- so it must never be able to happen every frame.
        # build_strip is called from the render path, and any instability in
        # the data would otherwise rebuild continuously and freeze the scroll.
        #
        # Set higher than live_interval (5s) on purpose, not equal to it.
        # Once has_any_live() started covering other-live games too, "some
        # game is live somewhere" became true for large stretches of the
        # day -- not just the rarer case of a followed team playing -- so
        # data now refetches on the 5s cadence almost continuously during
        # that stretch, and background composes were keeping pace with it:
        # confirmed on the Pi running alongside the real service, a
        # composition backgrounded this often measurably slowed the
        # concurrent render thread, visible as a small periodic pause in
        # the scroll. The background thread was never free -- it was
        # competing for the same CPU the matrix output itself needs on
        # time.
        #
        # Tuned in stages: 10s helped but left a smaller pause still
        # noticeable; 15s next, paired with memoizing _fit_font/
        # _largest_fit (composing is real work regardless -- confirmed on
        # the Pi at ~60ms per rebuild even with that cache warm, so
        # widening the gap is still the only lever that actually moves the
        # needle). 15s traded away more freshness than the pause needed,
        # though -- settled on 8s as the balance: still comfortably above
        # the 5s floor that caused the original stutter, while keeping
        # worst-case staleness for a live score close to live_interval's
        # own 5s rather than triple it. Data still refetches every
        # live_interval regardless; this only caps how often a fetched
        # change becomes a new image.
        self._last_build = 0.0
        self._min_rebuild_interval = 8.0
        # The actual composition runs on a background thread once something
        # is already on screen, so a rebuild -- still tens to hundreds of ms
        # -- never blocks the render path itself. Confirmed live: at the
        # display controller's 125 FPS high-FPS loop (needed for a smooth
        # scroll), a rebuild running in-line on that thread was slow enough
        # relative to the ~8ms frame budget to read as the board freezing
        # for a moment, roughly every live_interval while a game is live.
        # The first build (nothing cached yet, nothing to keep showing in
        # the meantime) stays synchronous -- there is no "meanwhile" to
        # protect on that one call.
        self._build_lock = threading.Lock()
        self._build_thread: Optional[threading.Thread] = None
        self._dispatched_signature = None
        self._clock_box = None
        self._clock_font = None
        self._clock_shown = None
        # A fixed card occupying the left module, when one is wanted.
        self._static_panel = None

    # ------------------------------------------------------------------
    # Fonts
    # ------------------------------------------------------------------
    def _font_roots(self):
        home = os.path.expanduser("~")
        return [
            os.path.join(os.getcwd(), "assets", "fonts"),
            os.path.join(home, "LEDMatrix", "assets", "fonts"),
            "/home/sportspi/LEDMatrix/assets/fonts",
            os.path.join(os.path.dirname(__file__), "assets", "fonts"),
        ]

    def _compile_bdf(self, path: str):
        """BDF must be compiled; ImageFont.load reads PIL's .pil format only."""
        if BdfFontFile is None:
            return None
        cache_dir = os.path.join(os.path.dirname(__file__), ".fontcache")
        stem = os.path.join(cache_dir, os.path.basename(path).replace(".bdf", ""))
        compiled = stem + ".pil"
        try:
            if not os.path.exists(compiled):
                os.makedirs(cache_dir, exist_ok=True)
                with open(path, "rb") as handle:
                    BdfFontFile.BdfFontFile(handle).save(stem)
            return ImageFont.load(compiled)
        except Exception as e:
            self.logger.debug("Could not compile %s: %s", path, e)
            return None

    def _named_font(self, name: str):
        if ("named", name) in self._font_cache:
            return self._font_cache[("named", name)]
        result = None
        for root in self._font_roots():
            path = os.path.normpath(os.path.join(root, name))
            if not os.path.exists(path):
                continue
            result = self._compile_bdf(path) if path.endswith(".bdf") else None
            if result is None:
                try:
                    result = ImageFont.load(path)
                except Exception:
                    result = None
            if result is not None:
                break
        self._font_cache[("named", name)] = result
        return result

    def _largest_fit(self, draw, rows: int, available: int):
        """The largest font, by measured size, that still fits rows*row_h.

        _fit_font returns the first candidate in FONT_LADDER that satisfies
        the constraint, in preference order -- not size order. For a loose
        constraint (few rows, lots of height) the first, smaller-preferred
        font already fits, so a genuinely bigger font later in the list is
        never even tried. This scans every candidate and keeps the one with
        the greatest row height that still fits, which is what "make this
        as big as the space allows" actually means.

        Memoized on (rows, available) -- neither the panel's own height
        nor a given call site's row constraint changes mid-session, so
        the result is identical every time, and rescanning/remeasuring
        every FONT_LADDER candidate on every single composition was
        avoidable work on the render path's own critical section.
        """
        key = (rows, available)
        if key in self._largest_fit_cache:
            return self._largest_fit_cache[key]

        candidates = []
        for name in self.FONT_LADDER:
            font = self._named_font(name)
            if font is not None:
                candidates.append(font)
        for size in self.FALLBACK_SIZES:
            try:
                candidates.append(ImageFont.load_default(size=size))
            except Exception:
                pass
        if not candidates:
            candidates = [ImageFont.load_default()]

        best = None
        for font in candidates:
            _, h = self._measure(draw, "Ay", font)
            row_h = h + 1
            if row_h < MIN_LEGIBLE_ROW_H:
                continue
            if rows * row_h <= available:
                if best is None or row_h > best[1]:
                    best = (font, row_h)
        result = best if best else self._fit_font(draw, rows, available)
        self._largest_fit_cache[key] = result
        return result

    def _fit_font(self, draw, rows: int, available: int):
        # Memoized the same way and for the same reason as _largest_fit.
        key = (rows, available)
        if key in self._fit_font_cache:
            return self._fit_font_cache[key]

        candidates = []
        for name in self.FONT_LADDER:
            font = self._named_font(name)
            if font is not None:
                candidates.append(font)
        for size in self.FALLBACK_SIZES:
            try:
                candidates.append(ImageFont.load_default(size=size))
            except Exception:
                pass
        if not candidates:
            candidates = [ImageFont.load_default()]

        smallest = None
        result = None
        for font in candidates:
            _, h = self._measure(draw, "Ay", font)
            row_h = h + 1
            if row_h < MIN_LEGIBLE_ROW_H:
                continue
            if smallest is None or row_h < smallest[1]:
                smallest = (font, row_h)
            if rows * row_h <= available:
                result = (font, row_h)
                break
        if result is None:
            result = smallest if smallest else None
        if result is None:
            font = candidates[0]
            _, h = self._measure(draw, "Ay", font)
            result = (font, h + 1)
        self._fit_font_cache[key] = result
        return result

    def font_report(self) -> str:
        return "; ".join(
            f"{n}: {'loaded' if self._named_font(n) else 'missing'}"
            for n in self.FONT_LADDER
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _safe(text) -> str:
        if text is None:
            return ""
        text = str(text)
        if text.isascii():
            return text
        decomposed = unicodedata.normalize("NFKD", text)
        stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
        return stripped.encode("ascii", "ignore").decode("ascii")

    @staticmethod
    def _measure(draw, text: str, font) -> Tuple[int, int]:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            return bbox[2] - bbox[0], bbox[3] - bbox[1]
        except Exception:
            return len(text) * 5, 7

    def _logo(self, league: str, abbr: str, size: int):
        if not self.logo_manager or not abbr:
            return None
        try:
            return self.logo_manager.get_logo(league, abbr, size)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Segment Drawing
    # ------------------------------------------------------------------
    def _smaller_font(self, draw, than_row_h: int):
        """The largest available font strictly shorter than than_row_h.

        FONT_LADDER is ordered by preference, not by size, so "the first
        name in the ladder" is not the same thing as "smaller than whatever
        font is already in use" -- that mismatch is what let the status label
        end up the same size as the score, eating a row of height that was
        meant to go to the crest instead. Returns (font, row_h), or None if
        nothing smaller is available.
        """
        candidates = []
        for name in self.FONT_LADDER:
            font = self._named_font(name)
            if font is not None:
                candidates.append(font)
        for size in self.FALLBACK_SIZES:
            try:
                candidates.append(ImageFont.load_default(size=size))
            except Exception:
                pass

        best = None
        for font in candidates:
            extent = draw.textbbox((0, 0), "Ay", font=font)[3] + 1
            if extent < than_row_h and (best is None or extent > best[1]):
                best = (font, extent)
        return best

    def _vblock_start(self, row_h: int, num_rows: int) -> int:
        """The target row for the first of num_rows stacked rows, centred.

        Placing every row at MARGIN + row_h*i looks centred only when
        row_h*num_rows happens to fill the available height exactly -- which
        it does for whichever font this was last measured against, and does
        not for a font with different proportions. Real BDF fonts on the
        actual hardware are not the fallback font this sandbox falls back
        to, so a fit that looks perfect here is not guaranteed to hold
        there. This computes the real slack and splits it evenly, so the
        block centres regardless of which font actually produced row_h.
        """
        content_h = row_h * num_rows
        available = self.height - self.MARGIN * 2
        slack = max(0, available - content_h)
        return self.MARGIN + slack // 2

    def _text_top(self, draw, font, target_row: int, sample: str = "0") -> int:
        """The y to pass to draw.text so the glyph's ink starts at target_row.

        This font carries a few pixels of built-in top leading on every
        glyph -- draw.text at y=1 puts the first visible pixel around y=4,
        not y=1. The leading is blank by definition, so pulling the origin
        up by that amount and letting it fall above the canvas costs nothing.

        `sample` defaults to a digit because nearly everything on this strip
        used to be drawn all-caps or numeric, and a digit's own ink-top lines
        up with a cap letter's. That default stopped being safe the moment
        mixed-case labels ("Final", "Feels Like") were introduced: a
        lowercase ascender/dot can sit a pixel above where a digit's own
        ink starts in this sandbox's fallback font, which put a status
        label's ink a pixel above its intended row (confirmed: "Final"
        wrong, reverting to "FINAL" fixed it, isolating the cause to the
        specific glyphs, not any of that session's other changes). Passing
        the actual text being positioned measures its own real leading
        instead of guessing from a digit that may not represent it.
        """
        leading = draw.textbbox((0, 0), sample or "0", font=font)[1]
        return target_row - leading

    def _text_bottom(self, draw, font, target_bottom_row: int,
                     sample: str = "0") -> int:
        """The y to pass to draw.text so the glyph's ink ends at target_bottom_row.

        The same leading that pushes a top-anchored row down means a
        bottom-anchored row cannot simply be placed at height-row_h: the
        glyph's true reach below its origin is the bounding box's bottom
        edge, not a plain height measurement, which is what clipped the
        forecast temperatures against the panel edge. See _text_top for why
        `sample` matters once mixed-case text is in play.
        """
        extent = draw.textbbox((0, 0), sample or "0", font=font)[3]
        return target_bottom_row - extent

    def _draw_banner(self, img, draw, x: int, team: Dict, font, row_h: int,
                     streak: str = "") -> int:
        """The team's logo at full panel height, with its name beside it.

        This is the strip's title card -- the thing that tells you at a glance
        whose numbers you are about to read, without having to parse an
        abbreviation in a score line.

        The current streak, when there is one, rides right after the name --
        from the standings, not tallied here, since a couple of days of
        game history is nowhere near enough to reconstruct a real streak.
        """
        # One row of margin, not two: a circular crest carries its own
        # transparent inset before the disc begins, which already reads as a
        # little extra breathing room on top of whatever margin is set here.
        logo_size = self.height - self.MARGIN * 2
        logo = self._logo(team.get("league", ""), team.get("abbr", ""), logo_size)

        start = x
        if logo is not None:
            oy = self.MARGIN + max(0, (logo_size - logo.height) // 2)
            try:
                img.paste(logo, (x, oy), logo)
            except Exception:
                img.paste(logo.convert("RGB"), (x, oy))
            x += logo.width + 4

        name = self._safe(team.get("name") or team.get("abbr") or "")
        nw, nh = self._measure(draw, name, font)
        draw.text((x, max(0, (self.height - nh) // 2)), name, font=font,
                  fill=self.LABEL)
        x += nw + 6

        if streak:
            colour = self.STREAK_WIN if streak.upper().startswith("W") \
                else self.STREAK_LOSS
            sw, sh = self._measure(draw, streak, font)
            draw.text((x, max(0, (self.height - sh) // 2)), streak,
                      font=font, fill=colour)
            x += sw + 6

        return x - start

    def _draw_section(self, img, draw, x: int, league: str, title: str,
                      subtitle: str, font, row_h: int) -> int:
        """A section banner: the league mark, then what this block is.

        Each team opens with a crest, so a block of statistics arriving with
        nothing but a title reads as a continuation of the last team rather
        than a change of subject. The mark is the signal -- it is recognised
        before any of the words are.
        """
        start = x
        size = self.height - self.MARGIN * 2
        logo = None
        if self.logo_manager is not None:
            try:
                logo = self.logo_manager.get_league_logo(league, size)
            except Exception:
                logo = None

        if logo is not None:
            oy = self.MARGIN + max(0, (size - logo.height) // 2)
            try:
                img.paste(logo, (x, oy), logo)
            except Exception:
                img.paste(logo.convert("RGB"), (x, oy))
            x += logo.width + 5

        tw = self._measure(draw, title, font)[0]
        sw = self._measure(draw, subtitle, font)[0] if subtitle else 0

        # Centred against the same available height as the logo beside it,
        # not top-anchored -- text top-anchored while its logo is centred
        # left the two visibly out of line with each other, on top of
        # dumping all the block's slack below the text.
        rows = 2 if subtitle else 1
        top = self._text_top(draw, font, self._vblock_start(row_h, rows))
        draw.text((x, top), self._safe(title), font=font, fill=self.UPCOMING)
        if subtitle:
            draw.text((x, top + row_h), self._safe(subtitle), font=font,
                      fill=self.DIM)
        x += max(tw, sw) + 8
        return x - start

    def _draw_trophy(self, draw, x: int, y: int, size: int) -> int:
        """A trophy, drawn rather than fetched.

        There is no logo for "MVP" the way there is for a club, and inventing
        one from a league mark would say the wrong thing. A cup is read
        instantly and at any size, which a word at four pixels is not.
        """
        gold = (255, 200, 60)
        rim = (190, 140, 30)
        w = max(6, size)
        h = max(7, size)

        bowl_w = int(w * 0.62)
        bowl_h = int(h * 0.45)
        bx = x + (w - bowl_w) // 2

        # Cup: a trapezoid, widest at the rim.
        draw.polygon(
            [(bx, y), (bx + bowl_w, y),
             (bx + bowl_w - bowl_w // 5, y + bowl_h),
             (bx + bowl_w // 5, y + bowl_h)],
            fill=gold,
        )
        # Handles either side of the rim.
        draw.line([(bx - 1, y + 1), (bx - 1, y + bowl_h // 2)], fill=rim)
        draw.line([(bx + bowl_w, y + 1), (bx + bowl_w, y + bowl_h // 2)], fill=rim)
        # Stem and foot.
        stem_x = x + w // 2
        draw.line([(stem_x, y + bowl_h), (stem_x, y + h - 2)], fill=gold)
        draw.line([(x + w // 4, y + h - 1), (x + w - w // 4, y + h - 1)], fill=gold)
        return w

    def _draw_award_section(self, img, draw, x: int, font, row_h: int) -> int:
        """Section banner for the award watch lists."""
        start = x
        size = min(self.height - self.MARGIN * 2, 14)
        top = self.MARGIN + max(0, (self.height - self.MARGIN * 2 - size) // 2)
        x += self._draw_trophy(draw, x, top, size) + 6

        title, subtitle = "MLB", "Awards Watch"
        if self.kid_friendly:
            subtitle = "Trophy Race"
        text_top = self._text_top(draw, font, self._vblock_start(row_h, 2))
        draw.text((x, text_top), self._safe(title), font=font, fill=self.UPCOMING)
        draw.text((x, text_top + row_h), self._safe(subtitle), font=font,
                  fill=self.DIM)
        x += max(self._measure(draw, title, font)[0],
                 self._measure(draw, subtitle, font)[0]) + 8
        return x - start

    def _draw_live_pulse(self, draw, x: int, y: int, size: int) -> int:
        """A filled dot in the live colour, standing in for a league mark.

        This section spans three sports at once, so no single league logo
        belongs at its head. A live indicator says what the section is for
        without picking a sport to represent the other two.
        """
        r = max(2, size // 2)
        draw.ellipse([x, y, x + r * 2, y + r * 2], fill=self.LIVE)
        return r * 2

    def _draw_live_section(self, img, draw, x: int, font, row_h: int) -> int:
        """Section banner for live games outside the followed teams."""
        start = x
        size = min(self.height - self.MARGIN * 2, 10)
        top = self.MARGIN + max(0, (self.height - self.MARGIN * 2 - size) // 2)
        x += self._draw_live_pulse(draw, x, top, size) + 6

        title = "Live"
        text_top = self._text_top(draw, font, self._vblock_start(row_h, 1),
                                  sample=title)
        draw.text((x, text_top), self._safe(title), font=font, fill=self.LIVE)
        x += self._measure(draw, title, font)[0] + 8
        return x - start

    def _draw_divider(self, img, draw, x: int) -> int:
        """A hairline between segments -- the one and only divider style
        on the strip, whether between two items in the same section or
        between two sections entirely. A second, heavier style used to
        mark section boundaries, but two different treatments mixed
        together read as inconsistent rather than as a deliberate
        hierarchy; one line everywhere is the one that actually looks
        uniform end to end.
        """
        draw.line([(x + 2, 3), (x + 2, self.height - 4)], fill=self.DIVIDER)
        return 6

    def _draw_bases(self, draw, x: int, y: int, situation: Dict,
                    size: int = 4) -> int:
        """A diamond, drawn the way a ballpark board draws it.

        Second at the top, third to the left, first to the right -- the
        orientation everyone already reads without thinking. Filled means
        occupied.
        """
        gap = 1
        span = size * 3 + gap * 2

        def diamond(cx, cy, on):
            fill = self.BASE_ON if on else self.BASE_OFF
            half = size // 2
            draw.polygon(
                [(cx, cy - half), (cx + half, cy), (cx, cy + half), (cx - half, cy)],
                fill=fill,
            )

        mid_x = x + span // 2
        diamond(mid_x, y + size // 2, situation.get("second"))
        diamond(x + size // 2, y + size + gap + size // 2, situation.get("third"))
        diamond(x + span - size // 2, y + size + gap + size // 2,
                situation.get("first"))
        return span

    def _draw_outs(self, draw, x: int, y: int, outs: int) -> int:
        """Three pips, filled for each out. Two is worth noticing."""
        radius = 2
        gap = 2
        for i in range(3):
            cx = x + i * (radius * 2 + gap)
            colour = self.OUT_ON if i < outs else self.OUT_OFF
            draw.ellipse([cx, y, cx + radius, y + radius], fill=colour)
        return 3 * (radius * 2 + gap)

    def _draw_live_detail(self, img, draw, x: int, game: Dict, font,
                          row_h: int) -> int:
        """The sport-specific live segment."""
        situation = game.get("situation") or {}
        kind = situation.get("kind")
        start = x
        top = 2

        if kind == "baseball":
            # Diamond, then the count and outs stacked beside it.
            x += self._draw_bases(draw, x, top, situation) + 5
            count = f"{situation.get('balls', 0)}-{situation.get('strikes', 0)}"
            draw.text((x, top), self._safe(count), font=font, fill=self.LABEL)
            cw = self._measure(draw, count, font)[0]
            draw.text((x, top + row_h + 1), "Out", font=font, fill=self.DIM)
            ow = self._measure(draw, "Out", font)[0]
            self._draw_outs(draw, x + ow + 3, top + row_h + 3,
                            situation.get("outs", 0))
            x += max(cw, ow + 3 + 12) + 6

        elif kind == "football":
            # Two-row block beside the crests, same stacking rule as
            # baseball's count/outs: possession + down on the top row,
            # yard line under them on the left edge. The yard line used
            # to be drawn at the cursor *after* the down text without
            # advancing the returned width for it -- confirmed on the Pi
            # during DAL@SEA other-live: returned width undercounted by
            # ~30px, so the next strip segment painted through "SEA 35".
            # Width is the wider of the two rows, like baseball above.
            #
            # Kid-friendly mode drops yard-line jargon ("SEA 35", "3rd & 7")
            # for "DAL Ball" / "3rd Down" / "Near Goal!" -- enough for a
            # child to know who has it and whether it is exciting.
            down = situation.get("down_distance") or ""
            spot = situation.get("yard_line") or ""
            possession = situation.get("possession") or ""
            col = x
            row1 = col
            if self.kid_friendly:
                if possession:
                    possession_text = self._safe(f"{possession} Ball")
                    draw.text((row1, top), possession_text, font=font,
                              fill=self.LIVE if not situation.get("red_zone")
                              else (255, 80, 60))
                    row1 += self._measure(draw, possession_text, font)[0] + 5
                row2 = col
                if situation.get("red_zone"):
                    spot_text = "Near Goal!"
                elif down:
                    # "3rd & 7" / "1st & 10" -> "3rd Down"
                    spot_text = f"{down.split()[0]} Down" if down.split() else down
                else:
                    spot_text = ""
                if spot_text:
                    draw.text((col, top + row_h + 1), self._safe(spot_text),
                              font=font, fill=self.DIM)
                    row2 += self._measure(draw, spot_text, font)[0]
                if row1 > col or row2 > col:
                    x = max(row1, row2) + 6
            else:
                if possession:
                    # A ball marker beside the team with possession is the
                    # quickest read on a football board. Plain "*", not the
                    # Unicode "\u25cf" circle this drew before -- real BDF
                    # fonts can't encode that at all (confirmed on the Pi:
                    # UnicodeEncodeError, a live-game crash the moment a
                    # followed team's opponent had the ball), and _safe()
                    # stripping it silently would have fixed the crash but
                    # also silently dropped the marker itself, with nothing
                    # left indicating who has the ball. "*" survives _safe()
                    # (it's already ASCII) and still reads as a marker.
                    possession_text = self._safe(f"{possession} *")
                    draw.text((row1, top), possession_text, font=font,
                              fill=self.LIVE if not situation.get("red_zone")
                              else (255, 80, 60))
                    row1 += self._measure(draw, possession_text, font)[0] + 5
                if down:
                    draw.text((row1, top), self._safe(down), font=font,
                              fill=self.LABEL)
                    row1 += self._measure(draw, down, font)[0] + 4
                row2 = col
                if spot:
                    spot_text = self._safe(spot)
                    draw.text((col, top + row_h + 1), spot_text, font=font,
                              fill=self.DIM)
                    row2 += self._measure(draw, spot_text, font)[0]
                if row1 > col or row2 > col:
                    x = max(row1, row2) + 6

        elif kind == "soccer":
            # Minute beside the crests. Status often already carries "67'",
            # but other-live cards and thin status strings benefit from the
            # same explicit live-detail slot football/baseball use.
            clock = situation.get("clock") or game.get("clock") or ""
            if clock:
                if self.kid_friendly:
                    # "67'" -> "67 Min" -- clearer than the prime mark.
                    minute = clock.rstrip("'").strip()
                    text = self._safe(f"{minute} Min" if minute else clock)
                else:
                    text = self._safe(clock)
                draw.text((x, top), text, font=font, fill=self.LIVE)
                x += self._measure(draw, text, font)[0] + 6

        # Basketball carries nothing beyond the clock, which the status line
        # already shows, so it adds no segment of its own.
        return max(0, x - start)

    def _draw_game(self, img, draw, x: int, game: Dict, font, row_h: int,
                   start_label: str = "", performer: Dict = None,
                   focus_abbr: str = "", rivals: Optional[List[str]] = None) -> int:
        """A game as two crests facing each other, at full size.

        Full size means the same size as the team banner's crest -- nearly
        the whole panel height -- which is only possible because the score
        sits beside the crest, not stacked under it. Stacking a status row,
        a crest and a score row on top of each other is what capped the
        crest at a third of the panel; putting the score next to the crest,
        the way the banner puts the team name next to its logo, frees that
        space back to the thing that is supposed to be full size.

        A live game keeps the smaller, stacked layout, because bases, count
        and outs need their own row and a crest that crowded them out would
        cost more than it gained.
        """
        start = x
        state = game.get("state")
        league = game.get("league", "")
        home = game.get("home") or {}
        away = game.get("away") or {}

        wanted = abbr_group(focus_abbr) if focus_abbr else set()
        if wanted and home.get("abbr", "").upper() in wanted:
            ours, theirs = home, away
        elif wanted and away.get("abbr", "").upper() in wanted:
            ours, theirs = away, home
        else:
            ours, theirs = away, home

        if state == "live":
            status, colour = (game.get("status_detail") or "Live"), self.LIVE
        elif state == "final":
            status, colour = "Final", self.FINAL
        else:
            status, colour = (start_label or "Next"), self.UPCOMING
            channel = game.get("broadcast") or ""
            if channel:
                # Appended rather than a separate row: the status label
                # already uses the smaller chrome font specifically to
                # leave the crest at full size, and this is a scrolling
                # strip with no width constraint forcing a shorter form.
                status = f"{status} {channel}"

        # A configured rival takes over the status colour and label
        # regardless of state -- a rivalry final is still worth flagging,
        # not just a live one -- since the whole point is catching the eye
        # before working out who is even playing. Expand aliases so a rival
        # configured as ARI still matches ESPN's AZ (and NYK vs NY, etc.).
        rival_abbrs = set()
        for a in (rivals or []):
            rival_abbrs |= abbr_group(str(a))
        if rival_abbrs and abbr_group(theirs.get("abbr", "")) & rival_abbrs:
            status = f"Rivalry {status}"
            colour = self.RIVALRY

        margin = self.MARGIN
        figure_font = font
        figure_extent = draw.textbbox((0, 0), "Ay", font=figure_font)[3] + 1

        if state == "live":
            # Bases, count and outs need their own row below the crest, so
            # this layout stays stacked: status, then crest, then score.
            status_font = self._named_font(self.FONT_LADDER[0]) or font
            status_extent = draw.textbbox((0, 0), "Ay", font=status_font)[3] + 1
            crest_size = max(8, min(16, self.height - margin * 2
                                    - status_extent - figure_extent - 2))
            block_h = status_extent + 1 + crest_size + 1 + figure_extent
            block_top = margin + max(0, (self.height - margin * 2 - block_h) // 2)
            status_y = block_top
            crest_top = block_top + status_extent + 1
            score_below = True
        else:
            # The same standard body font as everywhere else, not a
            # smaller candidate from the ladder -- that smaller font's own
            # letterforms read badly at this size (confirmed against real
            # hardware: "FINAL" was the clearest case), and the crest only
            # gives up about a pixel by using the bigger, correct one
            # instead of chasing a font size that was never worth it.
            status_font = figure_font
            status_extent = figure_extent

            crest_size = max(10, self.height - margin * 2 - status_extent - 1)
            status_y = margin
            crest_top = margin + status_extent + 1
            # Vertically centre the (status + crest) block as a whole, so
            # any leftover pixel is shared rather than left at one edge.
            block_h = status_extent + 1 + crest_size
            slack = max(0, self.height - margin * 2 - block_h)
            status_y += slack // 2
            crest_top += slack // 2
            score_below = False

        draw.text((x, self._text_top(draw, status_font, status_y, sample=status)),
                  self._safe(status), font=status_font, fill=colour)
        block = self._measure(draw, status, status_font)[0]

        cursor = x
        for index, side in enumerate((ours, theirs)):
            crest = self._logo(league, side.get("abbr", ""), crest_size)
            figure = self._safe(side.get("score", ""))
            if state == "upcoming":
                figure = self._safe(side.get("record", ""))

            crest_w = 0
            if crest is not None:
                oy = crest_top + max(0, (crest_size - crest.height) // 2)
                try:
                    img.paste(crest, (cursor, oy), crest)
                except Exception:
                    img.paste(crest.convert("RGB"), (cursor, oy))
                crest_w = crest.width
            else:
                text = self._safe(side.get("abbr", ""))
                draw.text((cursor, crest_top), text, font=figure_font,
                          fill=self.LABEL)
                crest_w = self._measure(draw, text, figure_font)[0]

            entry_w = crest_w
            if figure:
                fill = self.VALUE
                if state == "final" and not side.get("winner"):
                    fill = self.DIM
                fw = self._measure(draw, figure, figure_font)[0]
                if score_below:
                    fx = cursor + max(0, (crest_w - fw) // 2)
                    fy = crest_top + crest_size + 1
                else:
                    # Beside the crest, vertically centred against it -- the
                    # same relationship the banner has between its logo and
                    # the team name, which is what leaves the crest at full
                    # height instead of splitting the row three ways.
                    fx = cursor + crest_w + 4
                    fy = crest_top + max(0, (crest_size - figure_extent) // 2)
                    entry_w = crest_w + 4 + fw
                draw.text((fx, self._text_top(draw, figure_font, fy)),
                          figure, font=figure_font, fill=fill)

            cursor += entry_w
            if index == 0:
                joiner = "@" if state == "upcoming" else "-"
                jw = self._measure(draw, joiner, figure_font)[0]
                jy = crest_top + max(0, (crest_size - figure_extent) // 2)
                draw.text((cursor + 4, self._text_top(draw, figure_font, jy)),
                          joiner, font=figure_font, fill=self.DIM)
                cursor += jw + 8

        block = max(block, cursor - x)
        x += block + 6

        if state == "live":
            x += self._draw_live_detail(img, draw, x, game, font, row_h)

        if performer and state != "upcoming":
            x += self._draw_divider(img, draw, x)
            x += self._draw_note(
                img, draw, x,
                performer.get("full_name", ""), performer.get("name", ""),
                performer.get("line", ""), font, row_h,
            )

        if self.kid_friendly and state == "final":
            winner = self._followed_side_won(game, focus_abbr)
            if winner:
                cheer_name = winner.get("name") or winner.get("abbr") or ""
                if cheer_name:
                    x += self._draw_divider(img, draw, x)
                    x += self._draw_win_cheer(
                        img, draw, x, cheer_name, font, row_h)

        return x - start

    def _draw_leaderboard(self, img, draw, x: int, title: str,
                          rows, font, row_h: int, value_header: str = "",
                          scope: str = "") -> int:
        """A leaderboard as a strip segment: title over three ranked rows.

        The segment is a small table. Names start at one column and values
        end at another, both measured across every row in the segment, so the
        figures line up vertically -- a ragged right edge is what makes a
        leaderboard look typed rather than set.

        Rank and name are coloured gold/silver/bronze for the top 3 -- a
        leaderboard's whole point is who's on top, and a flat white row 1
        said that no more loudly than row 3. A row's team abbreviation is
        its own team's colour, not the rank colour, so a name and its team
        stay visually distinct from each other rather than reading as one
        run of colour.
        """
        if not rows:
            return 0
        start = x

        logo = None
        if scope in ("al", "nl") and self.logo_manager is not None:
            try:
                size = min(self.height - self.MARGIN * 2, 14)
                logo = self.logo_manager.get_scope_logo(scope, size)
            except Exception:
                logo = None
        if logo is not None:
            oy = self.MARGIN + max(0, (self.height - self.MARGIN * 2 - logo.height) // 2)
            try:
                img.paste(logo, (x, oy), logo)
            except Exception:
                img.paste(logo.convert("RGB"), (x, oy))
            x += logo.width + 5

        visible = rows[:3]
        use_font, use_row_h = font, row_h

        # Measure first: the value column is as wide as its widest entry, and
        # the name column takes what is left of the segment.
        value_w = max(
            (self._measure(draw, self._safe(r.get("value", "")), use_font)[0]
             for r in visible),
            default=0,
        )
        if value_header:
            value_w = max(value_w, self._measure(draw, value_header, use_font)[0])

        name_w = 0
        labels = []
        for i, row in enumerate(visible):
            name = row.get("short_name") or row.get("name", "")
            team = self._safe(row.get("team", ""))
            rank_name = self._safe(f"{row.get('rank', i + 1)}.{name}")
            combined = f"{rank_name} {team}" if team else rank_name
            labels.append((rank_name, team))
            name_w = max(name_w, self._measure(draw, combined, use_font)[0])

        gap = 4
        title_w = self._measure(draw, self._safe(title), use_font)[0]
        header_w = (self._measure(draw, value_header, use_font)[0]
                    if value_header else 0)
        # The title and the column header share the top row, so the block has
        # to hold both side by side -- sizing it to the title alone ran the
        # two together.
        block = max(
            title_w + (gap + header_w if header_w else 0),
            name_w + gap + value_w,
        )

        def _paint(target_draw, start_row, visible_rows, x_offset):
            title_y = self._text_top(target_draw, use_font, start_row)
            target_draw.text((x_offset, title_y), self._safe(title),
                             font=use_font, fill=self.UPCOMING)
            if value_header:
                hw = self._measure(target_draw, value_header, use_font)[0]
                target_draw.text((x_offset + block - hw, title_y),
                                 self._safe(value_header), font=use_font,
                                 fill=self.DIM)
            for i, row in enumerate(visible_rows):
                rank_name, team = labels[i]
                y = self._text_top(target_draw, use_font,
                                   start_row + use_row_h * (i + 1))
                rank_num = row.get("rank", i + 1)
                medal = (self.MEDALS[rank_num - 1] if rank_num in (1, 2, 3)
                         else self.LABEL)
                target_draw.text((x_offset, y), rank_name, font=use_font,
                                 fill=medal)
                if team:
                    cursor = x_offset + self._measure(
                        target_draw, rank_name + " ", use_font)[0]
                    team_color = self.TEAM_COLORS.get(team.upper(), self.LABEL)
                    target_draw.text((cursor, y), team, font=use_font,
                                     fill=team_color)
                value = self._safe(row.get("value", ""))
                if value:
                    vw = self._measure(target_draw, value, use_font)[0]
                    target_draw.text((x_offset + block - vw, y), value,
                                     font=use_font, fill=self.VALUE)

        # Where this block actually lands vertically is decided by
        # rendering it once onto a scratch canvas and measuring the real
        # lit pixels, not a font-metric formula. A textbbox-based
        # estimate was tried first (row_h per row, a sample glyph's own
        # ink height for just the last row so its trailing leading isn't
        # counted) and was closer, but still off by a real pixel on the
        # Pi: textbbox reports a string's declared bounding box, and the
        # specific glyphs a leaderboard actually draws (digits, periods,
        # no descenders) don't always light every row that box implies.
        # Rendering for real and measuring what actually lit removes the
        # guesswork entirely, the same principle this file already
        # applies to crest margins.
        margined_available = self.height - self.MARGIN * 2
        scratch_h = self.height + use_row_h * 4

        def _measure_block(n_rows):
            trial = Image.new("RGB", (max(block, 1), scratch_h), (0, 0, 0))
            tdraw = ImageDraw.Draw(trial)
            _paint(tdraw, 0, visible[:n_rows], 0)
            px = trial.load()
            lit = [yy for yy in range(scratch_h)
                  for xx in range(trial.width) if px[xx, yy] != (0, 0, 0)]
            return (min(lit), max(lit)) if lit else (0, -1)

        n_rows = len(visible)
        ink_top, ink_bottom = _measure_block(n_rows)
        content_h = ink_bottom - ink_top + 1
        while content_h > self.height and n_rows > 0:
            n_rows -= 1
            if n_rows == 0:
                return 0
            ink_top, ink_bottom = _measure_block(n_rows)
            content_h = ink_bottom - ink_top + 1
        visible = visible[:n_rows]

        # Title plus up to three ranked rows, genuinely centred rather
        # than top-anchored -- a shorter board (fewer than three rows
        # returned) would otherwise dump all its slack below the last
        # row instead of splitting it evenly around the block.
        if content_h <= margined_available:
            target_top = self.MARGIN + (margined_available - content_h) // 2
        else:
            target_top = (self.height - content_h) // 2
        start_row = target_top - ink_top

        _paint(draw, start_row, visible, x)

        return (x + block + 6) - start

    @staticmethod
    def condition_kind(text: str) -> str:
        """Forecast wording reduced to one of a handful of drawable shapes.

        NWS writes prose -- "Chance Showers And Thunderstorms" -- and a panel
        needs a symbol. Order matters: a thunderstorm mentioning rain is a
        thunderstorm, so the most specific test has to come first.
        """
        t = (text or "").upper()
        if any(w in t for w in ("THUNDER", "TSTM")):
            return "storm"
        if any(w in t for w in ("SNOW", "SLEET", "FLURR", "ICE", "WINTRY")):
            return "snow"
        if any(w in t for w in ("RAIN", "SHOWER", "DRIZZLE")):
            return "rain"
        if any(w in t for w in ("FOG", "HAZE", "SMOKE", "MIST")):
            return "fog"
        if any(w in t for w in ("PARTLY", "MOSTLY SUNNY", "MOSTLY CLEAR",
                                "FEW CLOUDS", "SCATTERED CLOUDS")):
            return "partly"
        if any(w in t for w in ("CLOUD", "OVERCAST")):
            return "cloud"
        if any(w in t for w in ("CLEAR", "SUNNY", "FAIR")):
            return "clear"
        return "cloud"

    @staticmethod
    def weather_kid_tip(weather: Dict) -> str:
        """A short outdoor tip a child can act on -- jacket, umbrella, etc.

        Prefer rain/snow over temperature when both apply. Empty string when
        nothing useful to say (mild and dry).
        """
        if not weather:
            return ""
        condition = (
            weather.get("now_condition") or weather.get("condition") or ""
        )
        kind = StripRenderer.condition_kind(condition)
        temp = weather.get("now_temp")
        if temp is None:
            temp = weather.get("temp")
        try:
            temp_f = float(temp) if temp is not None else None
        except (TypeError, ValueError):
            temp_f = None
        if temp_f is not None and (weather.get("units") or "F").upper() == "C":
            temp_f = temp_f * 9.0 / 5.0 + 32.0

        if kind == "storm":
            return "Stay In!"
        if kind == "rain":
            return "Umbrella!"
        if kind == "snow":
            return "Bundle Up!"
        if temp_f is not None and temp_f <= 45:
            return "Jacket!"
        if temp_f is not None and temp_f >= 88:
            return "Hot Out!"
        if kind == "clear" and temp_f is not None and 60 <= temp_f <= 80:
            return "Nice Day!"
        return ""

    def _draw_weather_icon(self, draw, x: int, y: int, size: int,
                           kind: str) -> int:
        """A weather symbol drawn from primitives.

        Drawn rather than fetched: at ten pixels an icon has to be designed
        for ten pixels, and a downloaded PNG scaled down to that is mush.
        """
        sun = (255, 200, 60)
        cloud = (200, 205, 215)
        wet = (90, 170, 255)
        white = (240, 245, 255)
        s = max(6, size)
        mid = x + s // 2

        def disc(cx, cy, r, fill):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)

        def puff(cy, fill=cloud):
            r = max(2, s // 4)
            disc(x + r, cy, r, fill)
            disc(x + s - r, cy, r, fill)
            disc(mid, cy - r // 2, r + 1, fill)
            draw.rectangle([x + r, cy - 1, x + s - r, cy + r], fill=fill)

        if kind == "clear":
            # A small core with four rays held clearly apart from it by a
            # real gap. Sizing the core and the rays off the same radius, as
            # the previous version did, put them close enough to blend into
            # one blob at small sizes -- and a filled ellipse a couple of
            # pixels wide reads as a diamond on a bitmap grid, not a circle,
            # so a blended sun-plus-rays came out looking like a plain
            # diamond with no rays visible at all.
            cy = y + s // 2
            half = s // 2
            core_r = max(1, min(half - 2, s // 4))
            ray_gap = core_r + 1
            ray_len = max(1, half - ray_gap)
            disc(mid, cy, core_r, sun)
            draw.line([(mid, cy - ray_gap - ray_len), (mid, cy - ray_gap)],
                     fill=sun)
            draw.line([(mid, cy + ray_gap), (mid, cy + ray_gap + ray_len)],
                     fill=sun)
            draw.line([(mid - ray_gap - ray_len, cy), (mid - ray_gap, cy)],
                     fill=sun)
            draw.line([(mid + ray_gap, cy), (mid + ray_gap + ray_len, cy)],
                     fill=sun)
        elif kind == "partly":
            disc(x + s // 3, y + s // 3, max(2, s // 4), sun)
            puff(y + s - s // 3)
        elif kind == "cloud":
            puff(y + s // 2)
        elif kind == "rain":
            puff(y + s // 2 - 1)
            for i in range(3):
                dx = x + 1 + i * max(2, (s - 2) // 3)
                draw.line([(dx, y + s - 3), (dx, y + s)], fill=wet)
        elif kind == "storm":
            puff(y + s // 2 - 1)
            draw.line([(mid, y + s - 4), (mid - 2, y + s - 1)], fill=sun)
            draw.line([(mid - 2, y + s - 1), (mid + 1, y + s)], fill=sun)
        elif kind == "snow":
            puff(y + s // 2 - 1)
            for i in range(3):
                dx = x + 1 + i * max(2, (s - 2) // 3)
                draw.point((dx, y + s - 2), fill=white)
                draw.point((dx, y + s), fill=white)
        else:  # fog
            for i in range(3):
                yy = y + s // 3 + i * 3
                draw.line([(x, yy), (x + s, yy)], fill=cloud)
        return s

    def _draw_moon_icon(self, draw, x: int, y: int, size: int,
                        name: str) -> int:
        """A moon phase from two overlapping discs -- a lit one, and a dark
        one shifted across it to cover whatever the sun isn't reaching. The
        same trick any moon-phase illustration uses, just at panel scale.

        Shift magnitude decides crescent vs quarter vs gibbous; shift
        direction decides waxing (dark retreats to the left, lit grows on
        the right) vs waning (mirrored). At the two extremes -- shift 0 or
        shift >= the full diameter -- direction does not matter, since the
        result is all-dark or all-lit either way.
        """
        lit = (225, 225, 235)
        dark = (35, 35, 50)
        s = max(6, size)
        r = s // 2
        cx, cy = x + r, y + r

        def disc(dx: float, fill) -> None:
            draw.ellipse(
                [cx - r + dx, cy - r, cx + r + dx, cy + r], fill=fill)

        shifts = {
            "New Moon": 0, "Waxing Crescent": r * 0.6, "First Quarter": r,
            "Waxing Gibbous": r * 1.5, "Full Moon": r * 2.2,
            "Waning Gibbous": r * 1.5, "Last Quarter": r,
            "Waning Crescent": r * 0.6,
        }
        shift = shifts.get(name, r * 2.2)
        direction = -1 if name.startswith("Waning") else 1

        disc(0, lit)
        if shift < r * 2:
            disc(direction * shift, dark)
        # A pure dark disc (new moon) would otherwise vanish into the
        # panel's own black background -- an outline keeps it a visible
        # circle at every phase, not just the lit ones.
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(70, 70, 85))
        return s

    def _draw_weather(self, img, draw, x: int, weather: Dict,
                      font, row_h: int, when=None,
                      show_forecast: bool = True,
                      show_current: bool = True) -> int:
        """Weather: now, then the next few hours.

        An active warning displaces the place name and takes the alert colour.
        A severe thunderstorm warning is the only thing on this board more
        urgent than a live score.

        show_forecast controls the hourly columns and the moon phase only;
        the 4-day forecast row stays up either way (same as current conditions)
        -- current conditions always draw regardless, on the same
        reasoning as the warning above: whatever's happening right now
        outside stays visible even while a live game hides everything
        else competing for the strip. Off by default from the caller's
        side (weather.hide_forecast_when_live in config, opt-in per
        install) -- the original design kept the whole block up during a
        live game on purpose, and this only overrides that for whoever
        turns it on.

        The hourly column has its own separate cutoff on top of that,
        6am-8pm only, read from `when` -- unlike show_forecast this one is
        not configurable, since it is a fixed daily rhythm rather than a
        per-install preference. The moon phase is not affected by it.

        show_current controls the icon and the plain current-temperature
        number specifically, not the whole "now" block -- feels-like is
        not a duplicate of anything shown elsewhere and stays up
        regardless. Off (False) when the static panel is already showing
        that same current-temperature reading, since a live game hasn't
        taken that slot over; on (True) once a live game has, the same
        moment the scroll gains its own copy of the clock for the same
        reason.
        """
        if not weather:
            return 0
        start = x
        unit = weather.get("units", "F")

        alerts = weather.get("alerts") or []
        if alerts:
            head = alerts[0].get("event", "")
            head_colour = (255, 70, 60) if alerts[0].get("severity") in (
                "Extreme", "Severe") else self.UPCOMING
        else:
            head = weather.get("label") or "Weather"
            head_colour = self.UPCOMING

        # --- Now: Icon, Temperature, And What It Feels Like ---------------
        # show_current controls the icon and the plain current-temperature
        # number only: whenever the static panel is already showing that
        # same reading (nothing live pinned there), a second copy scrolling
        # past is just a duplicate. Feels-like is not a duplicate of
        # anything the panel shows -- it's scroll-only content -- so it
        # stays up regardless, using the same icon+single-line treatment
        # the temperature would otherwise get, not left paired under a
        # hidden number.
        now_temp = weather.get("now_temp")
        if now_temp is None:
            now_temp = weather.get("temp")
        feels = weather.get("now_feels")
        kid_tip = self.weather_kid_tip(weather) if self.kid_friendly else ""
        # Kid tip takes the second row when present -- "Jacket!" is more
        # useful to a child than "Feels Like 71F". Feels-like stays for
        # the adult board.
        show_feels = (
            not kid_tip
            and feels is not None
            and now_temp is not None
            and feels != now_temp
        )
        show_tip = bool(kid_tip)

        if show_current:
            text_rows = 2 if (show_feels or show_tip) else 1
        elif show_tip or feels is not None:
            text_rows = 1
        else:
            text_rows = 0

        # The header and the now-content are centred together as one block,
        # not the header fixed at the very top with only the content below
        # it centred in whatever was left. Centring just the sub-block still
        # leaves the *combined* header-plus-content sitting high, with a
        # thin gap above the header and a much larger one below the content
        # -- the same top-anchoring mistake one level up.
        content_h = (
            max(min(16, self.height), text_rows * row_h) if text_rows else 0
        )
        total_h = row_h + 1 + content_h
        available = self.height - self.MARGIN * 2
        slack = max(0, available - total_h)
        block_top = self.MARGIN + slack // 2

        head_y = self._text_top(draw, font, block_top, sample=head)
        draw.text((x, head_y), self._safe(head), font=font, fill=head_colour)
        block = self._measure(draw, head, font)[0]

        cursor = x
        if content_h:
            now_top = block_top + row_h + 1
            icon_size = min(16, content_h)
            icon_top = now_top + max(0, (content_h - icon_size) // 2)
            text_top = now_top + max(0, (content_h - text_rows * row_h) // 2)

            condition = weather.get("now_condition") or weather.get("condition") or ""
            cursor = x
            cursor += self._draw_weather_icon(
                draw, cursor, icon_top, icon_size,
                self.condition_kind(condition)) + 3

            if show_current and now_temp is not None:
                big = f"{now_temp}{unit}"
                draw.text((cursor, self._text_top(draw, font, text_top)),
                          self._safe(big), font=font, fill=self.VALUE)
                width = self._measure(draw, big, font)[0]
                if show_tip:
                    draw.text((cursor, self._text_top(draw, font, text_top + row_h,
                                                       sample=kid_tip)),
                              self._safe(kid_tip), font=font, fill=self.UPCOMING)
                    width = max(width, self._measure(draw, kid_tip, font)[0])
                elif show_feels:
                    # Written out in full -- this is a scrolling strip, not
                    # a fixed-width panel, so there is no space pressure
                    # forcing an abbreviation that a viewer has to decode.
                    text = f"Feels Like {feels}{unit}"
                    draw.text((cursor, self._text_top(draw, font, text_top + row_h,
                                                       sample=text)),
                              self._safe(text), font=font, fill=self.DIM)
                    width = max(width, self._measure(draw, text, font)[0])
                cursor += width + 6
            elif not show_current and show_tip:
                draw.text((cursor, self._text_top(draw, font, text_top,
                                                   sample=kid_tip)),
                          self._safe(kid_tip), font=font, fill=self.UPCOMING)
                cursor += self._measure(draw, kid_tip, font)[0] + 6
            elif not show_current and feels is not None:
                text = f"Feels Like {feels}{unit}"
                draw.text((cursor, self._text_top(draw, font, text_top, sample=text)),
                          self._safe(text), font=font, fill=self.VALUE)
                cursor += self._measure(draw, text, font)[0] + 6

        block = max(block, cursor - x)
        x += block + 6

        # A section header sits in its own row, with the columns beneath it
        # starting a full row lower -- not sharing the top margin with a
        # smaller-font day/hour label that used to draw into the same rows
        # at once, which read as misaligned rather than header-over-content.
        forecast_content_top = self.MARGIN + row_h + 1

        # --- Next Hours ---------------------------------------------------
        # Shown 6am-8pm only. An hour-by-hour forecast is for deciding what
        # to do with the rest of today; overnight it's the 5-day forecast
        # that's still useful, not tonight's hourly, which by 8pm is mostly
        # "asleep" anyway. Fails open (shows) when there's no clock to read
        # an hour from, the same as every other clock-optional path here.
        daytime = when is None or 6 <= when.hour < 20
        hourly = [h for h in (weather.get("hourly") or []) if h.get("temp") is not None]
        if show_forecast and daytime and hourly:
            x += self._draw_divider(img, draw, x)
            x += self._draw_forecast_row(
                img, draw, x, hourly[:5], "Next Hours", font, row_h, unit,
                forecast_content_top)

        # --- 4 Day Forecast -----------------------------------------------
        # Not gated by show_forecast -- unlike the hourly column and the
        # moon phase, this stays up through a live game too, per the same
        # "brief enough not to compete" reasoning as current conditions
        # (and matching weather.hide_forecast_when_live's schema text).
        daily = [d for d in (weather.get("daily") or []) if d.get("temp") is not None]
        if daily:
            x += self._draw_divider(img, draw, x)
            x += self._draw_forecast_row(
                img, draw, x, daily[:4], "4 Day Forecast", font, row_h, unit,
                forecast_content_top)

        # --- Moon -----------------------------------------------------------
        if show_forecast and when is not None:
            x += self._draw_divider(img, draw, x)
            phase = moon_phase.phase_info(when)

            moon_text_rows = 2
            moon_content_h = max(min(16, self.height), moon_text_rows * row_h)
            moon_total_h = row_h + 1 + moon_content_h
            moon_available = self.height - self.MARGIN * 2
            moon_slack = max(0, moon_available - moon_total_h)
            moon_block_top = self.MARGIN + moon_slack // 2

            header_y = self._text_top(draw, font, moon_block_top, sample="Moon")
            draw.text((x, header_y), "Moon", font=font, fill=self.DIM)
            block = self._measure(draw, "Moon", font)[0]

            moon_row_top = moon_block_top + row_h + 1
            icon_size = min(16, moon_content_h)
            icon_top = moon_row_top + max(0, (moon_content_h - icon_size) // 2)
            text_top = moon_row_top + max(
                0, (moon_content_h - moon_text_rows * row_h) // 2)

            cursor = x
            cursor += self._draw_moon_icon(
                draw, cursor, icon_top, icon_size, phase["name"]) + 3

            draw.text((cursor, self._text_top(draw, font, text_top,
                                              sample=phase["name"])),
                      self._safe(phase["name"]), font=font, fill=self.VALUE)
            name_w = self._measure(draw, phase["name"], font)[0]
            pct_text = f"{phase['illumination']}% Lit"
            draw.text((cursor, self._text_top(draw, font, text_top + row_h,
                                              sample=pct_text)),
                      self._safe(pct_text), font=font, fill=self.DIM)
            pct_w = self._measure(draw, pct_text, font)[0]
            cursor += max(name_w, pct_w) + 6

            block = max(block, cursor - x)
            x += block + 6

        return x - start

    def _draw_forecast_column(self, draw, x: int, entry: Dict, font,
                              row_h: int, unit: str, content_top: int = None,
                              measure_only: bool = False) -> int:
        """One forecast column: label, icon, temperature, stacked.

        measure_only skips the actual draw calls and just returns the
        width this column would consume -- used to size a whole row of
        columns before any of them (or the header above them) are drawn,
        so the row can be centred under its own header rather than always
        starting flush with it. Kept as one function rather than a
        parallel measuring helper so the two can never drift apart on
        exactly how a column's width is computed.

        Label and temperature use a font strictly smaller than the shared
        body font -- the same trick that let the team crests reach full
        size, applied here for the same reason. Three stacked elements in
        one column is tight even on the fallback font this was originally
        tuned against; a real device's bitmap fonts run a full pixel taller
        per row, which left only 3px for the icon and tripped the safety
        floor meant to prevent an overlap, silently drawing no icon at all
        rather than a cramped one. A smaller label and temperature free
        real room instead of relying on that geometry surviving unchanged
        across whichever font actually loads.

        content_top, when given, is the exact target row for this column's
        own top row -- below a section header drawn above it, rather than
        the panel's own top margin plus this column's own default one-row
        offset. Without this the header (the larger body font) and this
        column's day/hour label (a smaller font, independently anchored to
        the same top margin) drew into the same rows at once, which read as
        misaligned rather than as a header over its content.
        """
        smaller = self._smaller_font(draw, row_h)
        text_font = smaller[0] if smaller else font
        text_row_h = smaller[1] if smaller else row_h

        label = self._safe(entry.get("name", ""))
        # Daily columns may carry a paired overnight low; hourly never does.
        # "82/65" fits the small font better than repeating the unit twice.
        if entry.get("low") is not None and entry.get("temp") is not None:
            temp = f"{entry.get('temp')}/{entry.get('low')}"
        else:
            temp = f"{entry.get('temp')}{unit}"

        lw = self._measure(draw, label, text_font)[0]
        tw = self._measure(draw, temp, text_font)[0]

        label_target = (self.MARGIN + text_row_h if content_top is None
                        else content_top)
        label_y = self._text_top(draw, text_font, label_target)
        label_ink_bottom = label_y + draw.textbbox((0, 0), "0", font=text_font)[3]

        temp_y = self._text_bottom(draw, text_font, self.height - 1 - self.MARGIN)
        temp_ink_top = temp_y + draw.textbbox((0, 0), "0", font=text_font)[1]

        # A 2px gap on each side of the icon was never actually affordable
        # in the worst case: whenever _smaller_font finds nothing and this
        # falls back to the shared body font, label and temperature alone
        # consume 16 of the 30px this column has to work with, leaving very
        # little for icon-plus-gaps and tripping the no-overlap floor below
        # -- the exact silent-blank bug 0.19.2 meant to fix. That worst
        # case still needs gap=1 to keep the icon above the floor at all
        # (confirmed on the Pi: gap=2 there drops it below the floor
        # entirely). But whenever a strictly-smaller font *is* found --
        # the common case -- there's enough room to afford gap=2 instead,
        # buying real breathing room between label/icon/temp at a cost
        # small enough the icon still reads as its own shape rather than a
        # single blob (confirmed by rendering both and comparing, not
        # assumed from the arithmetic alone).
        gap = 2 if smaller else 1
        available = temp_ink_top - label_ink_bottom - gap * 2
        # Still a floor: a fallback to no icon at all remains correct if
        # some font combination is tighter still.
        icon_size = min(14, available) if available >= 4 else 0
        # Centred in the gap between label and temperature, not pinned
        # immediately below the label with every remaining pixel dumped
        # before the temperature -- confirmed on real hardware, where the
        # icon rode high in its own slot instead of sitting centred in it,
        # the same top-anchoring mistake this file has fixed everywhere
        # else via slack-splitting.
        icon_top = label_ink_bottom + gap + max(0, (available - icon_size) // 2)

        width = max(lw, tw, icon_size)

        if not measure_only:
            draw.text((x + (width - lw) // 2, label_y), label, font=text_font,
                      fill=self.LABEL)
            if icon_size:
                self._draw_weather_icon(
                    draw, x + (width - icon_size) // 2, icon_top, icon_size,
                    self.condition_kind(entry.get("condition", "")))
            draw.text((x + (width - tw) // 2, temp_y), temp, font=text_font,
                      fill=self.VALUE)
        return width + 5

    def _draw_forecast_row(self, img, draw, x: int, entries: List[Dict],
                           header: str, font, row_h: int, unit: str,
                           content_top: int) -> int:
        """A section header ("Next Hours") with its own row of forecast
        columns beneath it, the columns centred under the header rather
        than always starting flush with its left edge.

        The header and its columns rarely measure the same width -- a
        short header over many columns, or a long one over few, however
        many actually came back for the hour window -- and starting the
        columns flush-left under a wider header left
        them looking pinned to one side of their own label instead of
        belonging to it, the same slack-dumped-on-one-side mistake this
        file already fixes everywhere vertically. Measured with a
        measure-only pass over the same _draw_forecast_column this then
        draws for real, so the two can never disagree on a column's width.

        The header itself draws in a font strictly smaller than the
        columns beneath it -- it's a label for the row, not content with
        equal billing, and the shared body font read as too heavy over a
        short row of day/temp columns already using a smaller font of
        their own. `content_top` (where the columns start) is untouched by
        this -- it's still measured off the shared body font's own row_h,
        so this alone doesn't buy the columns any extra room, on purpose:
        changing that tradeoff is a separate decision from how big the
        header reads.
        """
        smaller_header = self._smaller_font(draw, row_h)
        header_font = smaller_header[0] if smaller_header else font
        total_w = sum(
            self._draw_forecast_column(draw, 0, entry, font, row_h, unit,
                                       content_top=content_top,
                                       measure_only=True)
            for entry in entries
        )
        header_w = self._measure(draw, header, header_font)[0]
        draw.text((x, self._text_top(draw, header_font, self.MARGIN,
                                     sample=header)),
                  header, font=header_font, fill=self.DIM)
        column = x + max(0, (header_w - total_w) // 2)
        for entry in entries:
            column += self._draw_forecast_column(
                draw, column, entry, font, row_h, unit,
                content_top=content_top)
        # A width to add to x, not the absolute end position -- the caller
        # does x += self._draw_forecast_row(...), the same convention
        # every other segment on the strip returns.
        return max(header_w, column - x) + 4

    def _truncate(self, draw, text: str, font, max_width: int) -> str:
        """Longest prefix that fits, so a condition never runs into the next
        segment."""
        if max_width <= 0:
            return ""
        if self._measure(draw, text, font)[0] <= max_width:
            return text
        trimmed = text
        while trimmed and self._measure(draw, trimmed, font)[0] > max_width:
            trimmed = trimmed[:-1]
        return trimmed.rstrip()

    def _draw_countdown_icon(self, draw, x: int, y: int, size: int,
                             name: str) -> int:
        """A small icon guessed from the event's own name -- a cake for a
        birthday, a tree for Christmas, a pencil for school, a star for
        anything else. Drawn from primitives rather than looked up from a
        fixed set of files: the name is free text a person typed in, not a
        known category, so there is no icon to fetch even if one were
        wanted -- guessing from a keyword and drawing it is the only option
        that works for whatever someone actually names their own event.
        """
        upper = (name or "").upper()
        s = max(6, size)
        mid = x + s // 2

        if "BIRTHDAY" in upper or "BDAY" in upper:
            cake, icing, flame = (230, 140, 170), (255, 255, 255), (255, 190, 60)
            body_top = y + s // 2
            draw.rectangle([x, body_top, x + s - 1, y + s - 1], fill=cake)
            draw.line([(x, body_top), (x + s - 1, body_top)], fill=icing)
            draw.line([(mid, y), (mid, body_top - 1)], fill=icing)
            draw.ellipse([mid - 1, y - 2, mid + 1, y], fill=flame)
        elif "CHRISTMAS" in upper or "XMAS" in upper:
            tree, trunk, star = (40, 160, 70), (120, 80, 50), (255, 215, 0)
            draw.polygon(
                [(mid, y), (x, y + s - 3), (x + s - 1, y + s - 3)], fill=tree)
            draw.polygon(
                [(mid, y + 2), (x + 1, y + s - 1), (x + s - 2, y + s - 1)],
                fill=tree)
            tw = max(1, s // 6)
            draw.rectangle(
                [mid - tw, y + s - 3, mid + tw, y + s - 1], fill=trunk)
            draw.point((mid, y - 1), fill=star)
        elif "SCHOOL" in upper:
            pencil, tip, eraser = (255, 200, 60), (90, 60, 40), (230, 100, 120)
            draw.rectangle([x + 2, y, x + s - 3, y + s - 4], fill=pencil)
            draw.polygon(
                [(x + 2, y + s - 4), (x + s - 3, y + s - 4), (mid, y + s - 1)],
                fill=tip)
            draw.rectangle([x + 2, y - 2, x + s - 3, y], fill=eraser)
        else:
            gold = (255, 215, 0)
            r_out, r_in = s / 2, max(1.0, s / 5)
            points = []
            for i in range(10):
                angle = -math.pi / 2 + i * math.pi / 5
                r = r_out if i % 2 == 0 else r_in
                points.append(
                    (mid + r * math.cos(angle), y + s / 2 + r * math.sin(angle)))
            draw.polygon(points, fill=gold)
        return s

    def _draw_countdown(self, img, draw, x: int, name: str, days: int,
                        font, row_h: int) -> int:
        """Days until one configured date -- a birthday, a holiday -- as an
        icon guessed from the name, beside a two-row block: the count on
        top, what it is counting down to below. Same shape as the
        notable-performer note, since both are "a fact, then what it is
        about" -- the text needs no extra label of its own, since "62 Days
        / To Christmas" already says what it is.
        """
        start = x
        if days <= 0:
            big, label = "Today!", self._safe(name)
        else:
            big = f"{days} Day" + ("s" if days != 1 else "")
            label = f"To {self._safe(name)}"

        start_row = self._vblock_start(row_h, 2)
        icon_size = min(16, row_h * 2)
        icon_y = start_row + max(0, (row_h * 2 - icon_size) // 2)
        x += self._draw_countdown_icon(draw, x, icon_y, icon_size, name) + 3

        top = self._text_top(draw, font, start_row, sample=big)
        draw.text((x, top), big, font=font, fill=self.VALUE)
        w1 = self._measure(draw, big, font)[0]
        draw.text((x, top + row_h), label, font=font, fill=self.DIM)
        w2 = self._measure(draw, label, font)[0]
        return (x + max(w1, w2) + 6) - start

    def _draw_win_cheer(self, img, draw, x: int, name: str,
                        font, row_h: int) -> int:
        """A short celebration after a followed team's win -- for kids.

        Two rows: "Yankees" over "Win!" in the rivalry/gold colour so it
        reads as a moment, not another score line.
        """
        start = x
        team = self._safe(name or "We")
        top_line, bottom = team, "Win!"
        start_row = self._vblock_start(row_h, 2)
        top = self._text_top(draw, font, start_row, sample=top_line)
        colour = self.RIVALRY
        draw.text((x, top), top_line, font=font, fill=colour)
        w1 = self._measure(draw, top_line, font)[0]
        draw.text((x, top + row_h), bottom, font=font, fill=self.VALUE)
        w2 = self._measure(draw, bottom, font)[0]
        return (x + max(w1, w2) + 6) - start

    @staticmethod
    def _followed_side_won(game: Dict, focus_abbr: str) -> Optional[Dict]:
        """The followed side's competitor dict if they won this final."""
        if not focus_abbr or game.get("state") != "final":
            return None
        wanted = abbr_group(focus_abbr)
        for side in (game.get("home") or {}, game.get("away") or {}):
            if side.get("abbr", "").upper() in wanted and side.get("winner"):
                return side
        return None

    def _draw_favorite_player(self, img, draw, x: int, name: str,
                              font, row_h: int) -> int:
        """A configured favorite athlete on a team's strip -- Star + name.

        Always on for that team (not only when they scored tonight), so a
        kid's favorite still shows on quiet days. Uses the same two-row
        shape as a performer note.
        """
        from espn_data_source import abbreviate_name
        full = self._safe(name.strip())
        if not full:
            return 0
        short = abbreviate_name(full) or full
        label = "Star" if self.kid_friendly else "Favorite"
        start = x
        start_row = self._vblock_start(row_h, 2)
        top = self._text_top(draw, font, start_row, sample=label)
        draw.text((x, top), label, font=font, fill=self.DIM)
        w1 = self._measure(draw, label, font)[0]
        # Prefer the full name when the kid-friendly board is on --
        # "Lamine Yamal" is the point; abbreviate only if it clearly
        # overflows a short adult strip note.
        shown = full if (self.kid_friendly or len(full) <= 14) else short
        draw.text((x, top + row_h), shown, font=font, fill=self.UPCOMING)
        w2 = self._measure(draw, shown, font)[0]
        return (x + max(w1, w2) + 6) - start

    def _draw_note(self, img, draw, x: int, name: str, short_name: str,
                   body: str, font, row_h: int) -> int:
        """A performer: name above, stat line below.

        The name is the heading. A category code sitting over it -- PITCH,
        RAT -- added a word without adding information, and on a board this
        size every word has to earn its place.

        Same reasoning as the season MVP note: the block's width is
        whichever row is widest, not their sum, so when the stat line
        alone is already wider than the abbreviated name would have been,
        showing the full name instead costs nothing extra. Applies to live
        and final games alike, since both draw through this one method.
        """
        start = x
        line_w = self._measure(draw, body, font)[0]
        short_w = self._measure(draw, short_name, font)[0]
        display_name = name if (name and line_w > short_w) else short_name

        # A two-row block top-anchored in ~30px of available height dumps
        # most of that height as dead space below it -- name and stat line
        # together are maybe 14px, leaving up to 16px unused, all of it at
        # the bottom. Centred instead, the same way the leaderboard block
        # now is.
        start_row = self._vblock_start(row_h, 2)
        top = self._text_top(draw, font, start_row)
        draw.text((x, top), self._safe(display_name), font=font, fill=self.UPCOMING)
        w1 = self._measure(draw, display_name, font)[0]
        draw.text((x, top + row_h), self._safe(body), font=font, fill=self.LABEL)
        w2 = self._measure(draw, body, font)[0]
        return max(w1, w2) + 6

    def _draw_team_mvp(self, img, draw, x: int, name: str, short_name: str,
                       line: str, font, row_h: int) -> int:
        """A followed team's own season standout: a small label, the name,
        then their stat line.

        Three rows rather than reusing _draw_note's two -- name over stat
        line is already what a live game's own notable-performer note looks
        like, and this is a season-long fact, not a note about tonight's
        game. The label on top is what keeps a reader from mistaking one
        for the other.

        The block's width is already whichever row is widest, not the sum
        of all three -- so when the stat line alone (e.g. "ERA 2.21  W 10
        K 182") is wider than the abbreviated name would have been, showing
        the full name instead costs nothing extra: the block was already
        going to be at least that wide. Abbreviating in that case only
        threw away readability for a saving that was never real.
        """
        start = x
        label = "Season MVP"
        line_w = self._measure(draw, line, font)[0]
        short_w = self._measure(draw, short_name, font)[0]
        display_name = name if (name and line_w > short_w) else short_name

        start_row = self._vblock_start(row_h, 3)
        label_y = self._text_top(draw, font, start_row, sample=label)
        draw.text((x, label_y), label, font=font, fill=self.DIM)
        name_y = self._text_top(draw, font, start_row + row_h)
        draw.text((x, name_y), self._safe(display_name), font=font,
                  fill=self.UPCOMING)
        line_y = self._text_top(draw, font, start_row + row_h * 2)
        draw.text((x, line_y), self._safe(line), font=font, fill=self.LABEL)
        width = max(self._measure(draw, label, font)[0],
                    self._measure(draw, display_name, font)[0],
                    line_w)
        return width + 6

    # ------------------------------------------------------------------
    # Strip
    # ------------------------------------------------------------------
    def _draw_clock(self, img, draw, x: int, now, font, row_h: int):
        """Time and date at the head of the strip, as large as two rows allow.

        The shared body font is sized to fit four rows -- a leaderboard's
        title plus three entries -- but the clock only ever shows two, so
        reusing that font left real height unused. Fitting a font to two
        rows specifically, the same way the crest work earlier made the
        team logos as large as their own row budget allowed, gives the
        clock a noticeably bigger, easier-to-read face instead of matching
        the smallest text on the strip by default.

        The position is remembered so the minute can be repainted in place. A
        clock that only advanced when the strip was recomposed would sit
        minutes behind on a long pass, which is worse than showing no clock.
        """
        start = x
        clock = self._clock_text(now)
        try:
            date = f"{ESPNGamesSource.day_abbr(now)} {now.month}/{now.day}"
        except Exception:
            date = ""

        available = self.height - self.MARGIN * 2
        big_font, big_row_h = self._largest_fit(draw, 2, available)

        # Centred like every other block on the strip, not anchored to the
        # top margin -- an earlier version of this comment argued the
        # opposite, on the theory that "anchored consistently" meant
        # "top-anchored consistently." It meant the wrong thing: every block
        # here should be centred the same way, and top-anchoring a two-row
        # clock in ~30px of available height is exactly the bug that dumped
        # dead space at the bottom of the leaderboard, award, note and
        # weather blocks before they were fixed. The clock was the one
        # remaining holdout.
        top = self._text_top(draw, big_font, self._vblock_start(big_row_h, 2))
        width = self._draw_clock_face(draw, x, top, self._safe(clock), big_font,
                                       self.LABEL)
        if date:
            draw.text((x, top + big_row_h), self._safe(date), font=big_font,
                      fill=self.DIM)
            width = max(width, self._measure(draw, date, big_font)[0])

        # Returned rather than written to self directly -- this can run on
        # the background build thread (_compose_strip's docstring explains
        # why), and self._clock_box is what refresh_clock() reads against
        # self._strip_cache on the render thread. Writing it here, before
        # this composition has even been adopted, let a still-visible old
        # strip's clock repaint at a *new* build's box position -- mostly
        # harmless while the clock always drew at the same fixed spot, but
        # this box can now also disappear/reappear (show_clock), and
        # repainting at a position from a build the screen isn't showing
        # yet is a real corruption risk, not just a stale-until-next-frame
        # one. The caller threads this through _pending and only applies
        # it to self._clock_box at the same moment adopt_pending() swaps
        # the image itself in.
        clock_state = ((start, top, width, big_row_h), big_font, clock)
        return width + 8, clock_state

    def _draw_clock_face(self, draw, x: int, y: int, text: str, font,
                         fill) -> int:
        """Clock text with a small, hand-drawn colon in place of the font's
        own.

        At the clock's own larger font -- sized independently to fill two
        rows rather than sharing the smaller four-row body font -- a real
        BDF colon glyph is two solid squares nearly as tall as a digit,
        heavier than the thin divider a real clock face uses. Splitting the
        string around its single ':' and drawing two small dots instead,
        sized off the digit's own ink height rather than the font's full
        row height, keeps the separator a separator instead of a third
        character competing with the numbers either side of it.
        """
        if ":" not in text:
            draw.text((x, y), text, font=font, fill=fill)
            return self._measure(draw, text, font)[0]

        hour, rest = text.split(":", 1)
        cursor = x
        draw.text((cursor, y), hour, font=font, fill=fill)
        cursor += self._measure(draw, hour, font)[0]

        bbox = draw.textbbox((0, 0), "0", font=font)
        ink_top, ink_h = y + bbox[1], max(1, bbox[3] - bbox[1])
        dot = max(1, ink_h // 6)
        gap = 2
        cx = cursor + gap
        draw.rectangle([cx, ink_top + ink_h // 3 - dot // 2,
                        cx + dot, ink_top + ink_h // 3 - dot // 2 + dot],
                       fill=fill)
        draw.rectangle([cx, ink_top + (ink_h * 2) // 3 - dot // 2,
                        cx + dot, ink_top + (ink_h * 2) // 3 - dot // 2 + dot],
                       fill=fill)
        cursor += gap * 2 + dot

        draw.text((cursor, y), rest, font=font, fill=fill)
        cursor += self._measure(draw, rest, font)[0]
        return cursor - x

    @staticmethod
    def _clock_text(now, military: bool = False) -> str:
        try:
            if military:
                return now.strftime("%H:%M")
            hour = now.strftime("%I").lstrip("0") or "12"
            return f"{hour}:{now.strftime('%M')}{now.strftime('%p')[0]}"
        except Exception:
            return ""

    def refresh_clock(self, now) -> None:
        """Repaint the clock on the cached strip, in place.

        Cheap enough for every frame: it redraws a box a few dozen pixels wide
        instead of recomposing several thousand pixels of strip.
        """
        if self._strip_cache is None or not self._clock_box:
            return
        text = self._clock_text(now)
        if not text or text == self._clock_shown or self._clock_font is None:
            return
        x, y, width, row_h = self._clock_box
        draw = ImageDraw.Draw(self._strip_cache)
        draw.rectangle([x, y, x + width, y + row_h - 1], fill=(0, 0, 0))
        self._draw_clock_face(draw, x, y, self._safe(text), self._clock_font,
                              self.LABEL)
        self._clock_shown = text

    def build_strip(self, teams_and_games, start_labels=None, leaderboards=None,
                    weather=None, clock=None, awards=None,
                    other_live=None, team_mvps=None, countdowns=None,
                    streaks=None, weather_show_forecast=True, show_clock=True,
                    weather_show_current=True, rivalry_live_boost=0):
        """One continuous strip across every team.

        A single image rather than one per team: the board then scrolls
        without stopping, and a team's section flows into the next instead of
        the panel blanking and restarting between them.

        Composed once and cached against its data, so each frame is a crop.
        Rebuilding several hundred pixels of image every frame would be
        ruinous on a Pi that is also driving the matrix.
        """
        self.kid_friendly = bool(self.config.get("kid_friendly", False))
        signature = (
            self.width, self.height,
            bool(self.kid_friendly),
            int(rivalry_live_boost or 0),
            tuple(
                (entry[0], tuple((r.get("rank"), r.get("short_name"), r.get("value"))
                                 for r in entry[1]))
                for entry in (leaderboards or [])
            ),
            # The hour, not the full timestamp -- the visible clock digits
            # repaint separately via refresh_clock() without a full rebuild,
            # so the signature deliberately ignores most of a clock change.
            # The hour is the exception: it is what the hourly-forecast
            # day/night cutoff below reads, and without it in here a rebuild
            # would only happen to land on that boundary by coincidence,
            # whenever something else (weather's own refetch, a score)
            # happened to trigger one anyway.
            (bool(clock), clock.hour if clock else None, bool(show_clock)),
            tuple(
                (entry[0], tuple((r.get("rank"), r.get("short_name"),
                                  r.get("value")) for r in entry[1]))
                for entry in (awards or [])
            ),
            (
                (weather or {}).get("now_temp"), (weather or {}).get("temp"),
                (weather or {}).get("next_temp"),
                (weather or {}).get("now_condition"),
                tuple(a.get("event", "") for a in (weather or {}).get("alerts", [])),
                bool(weather_show_forecast), bool(weather_show_current),
            ),
            tuple(
                sorted((k, v.get("name", ""), v.get("short_name", ""),
                       v.get("line", "")) for k, v in (team_mvps or {}).items())
            ),
            tuple(
                (e.get("name", ""), e.get("days", 0)) for e in (countdowns or [])
            ),
            tuple(sorted((streaks or {}).items())),
            tuple(
                (team.get("abbr"), team.get("league"),
                 tuple(
                     (g.get("id"), g.get("state"),
                      g.get("away", {}).get("score"),
                      g.get("home", {}).get("score"),
                      (g.get("situation") or {}).get("balls"),
                      (g.get("situation") or {}).get("strikes"),
                      (g.get("situation") or {}).get("outs"),
                      (g.get("situation") or {}).get("down_distance"),
                      tuple(l.get("line", "") for l in (g.get("leaders") or [])))
                     for g in games
                 ))
                for team, games in teams_and_games
            ),
        )
        if signature == self._strip_key and self._strip_cache is not None:
            return self._strip_cache
        if signature == self._pending_key and self._pending is not None:
            # Already built and waiting for the seam; keep showing the old one.
            return self._strip_cache

        if self._strip_cache is None:
            # Nothing on screen yet, so nothing to keep showing while a
            # background build runs -- block once, here, at startup.
            strip, clock_state = self._compose_strip(
                teams_and_games, start_labels, leaderboards, weather, clock,
                awards, other_live, team_mvps, countdowns, streaks,
                weather_show_forecast, show_clock, weather_show_current,
                rivalry_live_boost,
            )
            self._strip_key = signature
            self._strip_cache = strip
            self._clock_box, self._clock_font, self._clock_shown = (
                clock_state if clock_state else (None, None, None)
            )
            self._last_build = time.time()
            return strip

        with self._build_lock:
            already_building = (
                self._build_thread is not None and self._build_thread.is_alive()
            )
            same_as_dispatched = self._dispatched_signature == signature

        now = time.time()
        if (not already_building and not same_as_dispatched
                and now - self._last_build >= self._min_rebuild_interval):
            self._dispatch_background_build(
                signature, teams_and_games, start_labels, leaderboards,
                weather, clock, awards, other_live, team_mvps, countdowns,
                streaks, weather_show_forecast, show_clock,
                weather_show_current, rivalry_live_boost,
            )
        return self._strip_cache

    def _ensure_compose_process(self) -> None:
        """Lazily start the persistent worker process, once, the first
        time a process-backed background build is actually needed.

        Not started eagerly in __init__: most StripRenderer instances
        (every one test_offline.py creates) never opt into use_process at
        all, and even the ones that do don't need a live worker until the
        first real background dispatch.

        Uses the "spawn" start method explicitly, not the platform
        default ("fork" on Linux) -- confirmed the hard way: forking a
        process that has already loaded PIL/FreeType font resources left
        the *parent* process's own font loading broken afterward
        ("OSError: cannot open resource" on a later, unrelated
        ImageFont.load_default() call), a known class of gotcha where a
        fork inherits a C extension's internal file handles/state in a
        way that isn't safe to use from either side afterward. spawn
        starts a genuinely fresh interpreter with none of that inherited
        state, at the one-time cost of a slower process start -- paid
        once, since the worker is kept alive for the life of the service.
        """
        if self._compose_process is not None and self._compose_process.is_alive():
            return
        ctx = multiprocessing.get_context("spawn")
        self._compose_conn, child_conn = ctx.Pipe()
        allow_download = bool(
            self.logo_manager and getattr(self.logo_manager, "allow_download", True)
        )
        self._compose_process = ctx.Process(
            target=_compose_worker_main,
            args=(child_conn, self.width, self.height, allow_download),
            daemon=True, name="strip-compose-worker",
        )
        # See _THIS_MODULE's own comment: start() pickles the target by
        # reference synchronously, in this thread, before it returns -- the
        # bare module name only needs to resolve for that one call.
        already_present = sys.modules.get(__name__) is _THIS_MODULE
        if not already_present:
            sys.modules[__name__] = _THIS_MODULE
        try:
            self._compose_process.start()
        finally:
            if not already_present:
                del sys.modules[__name__]
        # The child has its own copy (duplicated across fork); this
        # process only ever talks over self._compose_conn, the other end.
        child_conn.close()

    def _dispatch_background_build(self, signature, teams_and_games,
                                    start_labels, leaderboards, weather,
                                    clock, awards, other_live, team_mvps,
                                    countdowns, streaks,
                                    weather_show_forecast=True,
                                    show_clock=True,
                                    weather_show_current=True,
                                    rivalry_live_boost=0) -> None:
        """Compose the next strip off the render path, so the scroll
        never waits on it -- only the finished image, swapped in at
        adopt_pending()'s next seam, is ever shared back with the caller.

        use_process routes this to a separate OS process instead of a
        background thread -- see _compose_worker_main's own docstring for
        why a thread alone wasn't enough. Either way, self._build_thread
        ends up holding whatever _wait_for_background_build() should join:
        the thread doing the compose itself, or (use_process) a thin
        watcher thread that just blocks on the worker's response queue,
        which is I/O wait, not CPU work, so it costs nothing to have
        running.
        """
        self._dispatched_signature = signature
        self._last_build = time.time()

        def _finish(strip, clock_state) -> None:
            with self._build_lock:
                self._pending_key = signature
                self._pending = strip
                # Held alongside _pending rather than applied to
                # self._clock_box now -- this runs off the render thread,
                # while self._strip_cache (what refresh_clock() is still
                # repainting every frame) is the *previous* build until
                # adopt_pending() swaps them together.
                self._pending_clock_state = clock_state
                if self._dispatched_signature == signature:
                    self._dispatched_signature = None

        def _fail(context: str, exc_info=None) -> None:
            self.logger.error(
                "Background strip build failed (%s)", context, exc_info=exc_info)
            with self._build_lock:
                if self._dispatched_signature == signature:
                    self._dispatched_signature = None

        if self._use_process:
            self._ensure_compose_process()
            conn = self._compose_conn
            request = (
                teams_and_games, start_labels, leaderboards, weather, clock,
                awards, other_live, team_mvps, countdowns, streaks,
                weather_show_forecast, show_clock, weather_show_current,
                rivalry_live_boost,
            )

            def _watch() -> None:
                try:
                    conn.send(request)
                except Exception:
                    _fail("sending request to worker process", exc_info=True)
                    return
                # Bounded, not conn.recv() outright -- a worker that died,
                # hung, or never received a request that failed to
                # pickle partway through must eventually be reported as
                # failed rather than leaving this renderer waiting on a
                # reply that is never coming.
                if not conn.poll(20.0):
                    _fail("worker process timed out")
                    return
                try:
                    status, payload, clock_state = conn.recv()
                except Exception:
                    _fail("reading response from worker process", exc_info=True)
                    return
                if status == "ok":
                    if clock_state is not None:
                        # The worker sent the box and clock text but not
                        # the font object itself -- see its own comment
                        # for why. Resolved fresh, in this process: same
                        # panel height, same _largest_fit call, so
                        # deterministically the same font file the worker
                        # used, just loaded (and cached) here instead.
                        box, _missing_font, clock_text = clock_state
                        probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
                        local_font, _ = self._largest_fit(
                            probe, 2, self.height - self.MARGIN * 2)
                        clock_state = (box, local_font, clock_text)
                    _finish(payload, clock_state)
                else:
                    _fail(f"worker reported: {payload}")

            thread = threading.Thread(target=_watch, daemon=True,
                                      name="strip-build-watch")
            self._build_thread = thread
            thread.start()
            return

        def _run() -> None:
            try:
                strip, clock_state = self._compose_strip(
                    teams_and_games, start_labels, leaderboards, weather,
                    clock, awards, other_live, team_mvps, countdowns, streaks,
                    weather_show_forecast, show_clock, weather_show_current,
                    rivalry_live_boost,
                )
            except Exception:
                _fail("thread", exc_info=True)
                return
            _finish(strip, clock_state)

        thread = threading.Thread(target=_run, daemon=True,
                                  name="strip-build")
        self._build_thread = thread
        thread.start()

    def _wait_for_background_build(self, timeout: float = 5.0) -> bool:
        """Block until any in-flight background rebuild finishes.

        Never called from the render path -- the entire point of
        backgrounding the build is that nothing there waits on it. This
        exists for tests, which need a deterministic point to synchronize
        on rather than a real wall-clock race against a daemon thread.
        """
        thread = self._build_thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def _compose_strip(self, teams_and_games, start_labels, leaderboards,
                       weather, clock, awards, other_live, team_mvps,
                       countdowns, streaks,
                       weather_show_forecast: bool = True,
                       show_clock: bool = True,
                       weather_show_current: bool = True,
                       rivalry_live_boost: int = 0):
        """The actual drawing work -- tens of milliseconds, hundreds on a
        Pi. Safe to run off the main thread: everything it touches is
        either local to this call (the scratch canvas, its font) or the
        caller's own already-built data for this one signature, never
        self._strip_cache/_pending directly.

        Returns (image, clock_state) -- clock_state is whatever _draw_clock
        produced (or None), for the caller to apply to the live clock
        attributes only once this composition is actually adopted, not
        during composition itself.
        """
        start_labels = start_labels or {}

        # The strip is as wide as its content, and the content is only known
        # once drawn, so draw onto a scratch canvas and crop. Sized from the
        # content rather than a flat 8000px: allocating and clearing that
        # buffer is itself part of the cost, and most strips need a fraction
        # of it.
        estimate = self.width + 260 * max(1, len(teams_and_games))
        estimate += 150 * len(leaderboards or [])
        estimate += 90 if clock is not None else 0
        estimate += 150 * len(awards or []) + (120 if awards else 0)
        estimate += 280 if weather else 0
        estimate += 220 * len(other_live or []) + (120 if other_live else 0)
        estimate += 90 * len(countdowns or [])
        scratch = Image.new("RGB", (min(9000, estimate + 600), self.height),
                            (0, 0, 0))
        draw = ImageDraw.Draw(scratch)
        # Four rows: a leaderboard needs a title and three entries, which is
        # one more than a game segment's status line and two teams.
        font, row_h = self._fit_font(draw, 4, self.height)

        order = {"live": 0, "final": 1, "upcoming": 2}
        # Weather leads: a warning is the one thing here more urgent than a
        # live score, and the temperature is what most people glance up for.
        # No leading blank: the strip wraps, so a panel of clear space at the
        # start is simply a blank screen once per pass. The strip's own tail
        # already closes with a divider before its blank margin -- the wrap
        # seam only had that on one side, with nothing marking the other, so
        # where a pass loops back into a new one (starting with the clock)
        # read as a gap rather than a deliberate boundary.
        x = 4
        x += self._draw_divider(scratch, draw, x)
        clock_state = None
        if clock is not None and show_clock:
            added, clock_state = self._draw_clock(scratch, draw, x, clock, font, row_h)
            x += added
            x += self._draw_divider(scratch, draw, x)

        if weather:
            added = self._draw_weather(scratch, draw, x, weather, font, row_h,
                                       clock, show_forecast=weather_show_forecast,
                                       show_current=weather_show_current)
            if added:
                x += added
                x += self._draw_divider(scratch, draw, x)

        if countdowns:
            for i, event in enumerate(countdowns):
                if i:
                    x += self._draw_divider(scratch, draw, x)
                x += self._draw_countdown(
                    scratch, draw, x, event.get("name", ""),
                    event.get("days", 0), font, row_h)
            x += self._draw_divider(scratch, draw, x)

        # Other-live games are interleaved one at a time after each
        # followed team, instead of bunched into a single block at the
        # tail of the strip -- a game around the league used to sit
        # behind every followed team's full set of games, so on a long
        # roster it could be most of a lap before it came back around.
        # Popping from the front here means the first other-live game
        # trails the first team with games, the second trails the
        # second, and so on; any left over once the teams run out (more
        # live games elsewhere than followed teams) still get a
        # trailing section, same as the old single-block behaviour.
        other_live_queue = list(other_live or [])

        for team, games in teams_and_games:
            fav = (team.get("favorite_player") or "").strip()
            if not games and not fav:
                continue
            streak = (streaks or {}).get(team.get("abbr", ""), "")
            x += self._draw_banner(scratch, draw, x, team, font, row_h, streak)
            x += self._draw_divider(scratch, draw, x)
            if fav:
                x += self._draw_favorite_player(
                    scratch, draw, x, fav, font, row_h)
                x += self._draw_divider(scratch, draw, x)
            rivals = team.get("rivals") or []
            rival_abbrs = set()
            for a in rivals:
                rival_abbrs |= abbr_group(str(a))
            for game in sorted(games, key=lambda g: order.get(g.get("state"), 3)):
                repeats = 1
                if (rivalry_live_boost and game.get("state") == "live"
                        and rival_abbrs):
                    wanted = abbr_group(team.get("abbr", ""))
                    home = (game.get("home") or {}).get("abbr", "")
                    away = (game.get("away") or {}).get("abbr", "")
                    if home.upper() in wanted:
                        theirs = away
                    elif away.upper() in wanted:
                        theirs = home
                    else:
                        theirs = away
                    if abbr_group(theirs) & rival_abbrs:
                        repeats = 1 + int(rivalry_live_boost)
                for _ in range(repeats):
                    x += self._draw_game(
                        scratch, draw, x, game, font, row_h,
                        start_labels.get(game.get("id"), ""),
                        performer=ESPNGamesSource.pick_performer(
                            game, team.get("abbr", ""),
                            prefer_name=fav,
                        ),
                        focus_abbr=team.get("abbr", ""),
                        rivals=rivals,
                    )
                    x += self._draw_divider(scratch, draw, x)

            # The team's own season standout, right after its games -- a
            # fact about their season belongs closer to the scores than
            # anything else on the strip.
            mvp = (team_mvps or {}).get(team.get("abbr", ""))
            if mvp and mvp.get("line"):
                x += self._draw_team_mvp(
                    scratch, draw, x, mvp.get("name", ""),
                    mvp.get("short_name", ""), mvp["line"], font, row_h,
                )
                x += self._draw_divider(scratch, draw, x)

            # One other-live game right after this team, banner and all,
            # so it reads as its own thing and not part of the team just
            # shown. No divider needed before the banner -- the team's own
            # block (or its MVP note) already closed with one -- but one
            # is still needed after it, the same as every other section
            # banner on the strip (leaderboards, awards): without it the
            # banner and the game it introduces read as one run-on block
            # instead of a header over its own content.
            if other_live_queue:
                game = other_live_queue.pop(0)
                x += self._draw_live_section(scratch, draw, x, font, row_h)
                x += self._draw_divider(scratch, draw, x)
                x += self._draw_game(
                    scratch, draw, x, game, font, row_h,
                    performer=ESPNGamesSource.pick_performer(game, ""),
                )
                x += self._draw_divider(scratch, draw, x)

        # Anything left over -- more live games elsewhere than followed
        # teams with games to trail -- closes out the same way the whole
        # section used to: one banner, then every remaining game.
        if other_live_queue:
            x += self._draw_live_section(scratch, draw, x, font, row_h)
            x += self._draw_divider(scratch, draw, x)
            for game in other_live_queue:
                x += self._draw_game(
                    scratch, draw, x, game, font, row_h,
                    performer=ESPNGamesSource.pick_performer(game, ""),
                )
                x += self._draw_divider(scratch, draw, x)

        # League leaders after the teams, behind a section banner: the block
        # changes subject from "my teams" to "the league", and the reader
        # needs telling.
        # Only banner a section that has something in it: a list of empty
        # leaderboards would otherwise announce statistics and then show none.
        drawable = [entry for entry in (leaderboards or []) if entry[1]]
        if drawable:
            leaders_sub = "Top Players" if self.kid_friendly else "Season Leaders"
            x += self._draw_section(scratch, draw, x, "mlb", "MLB",
                                    leaders_sub, font, row_h)
            x += self._draw_divider(scratch, draw, x)

        for entry in drawable:
            title, rows = entry[0], entry[1]
            header = entry[2] if len(entry) > 2 else ""
            scope = entry[3] if len(entry) > 3 else ""
            added = self._draw_leaderboard(scratch, draw, x, title, rows,
                                           font, row_h, header, scope)
            if added:
                x += added
                x += self._draw_divider(scratch, draw, x)

        # Awards behind their own banner: a watch list is a computed opinion,
        # not a factual leaderboard, and the trophy says so before the words do.
        drawable_awards = [entry for entry in (awards or []) if entry[1]]
        if drawable_awards:
            x += self._draw_award_section(scratch, draw, x, font, row_h)
            x += self._draw_divider(scratch, draw, x)
            for entry in drawable_awards:
                title, rows = entry[0], entry[1]
                scope = entry[2] if len(entry) > 2 else ""
                added = self._draw_leaderboard(scratch, draw, x, title, rows,
                                               font, row_h, "", scope)
                if added:
                    x += added
                    x += self._draw_divider(scratch, draw, x)

        # No explicit closing divider: whichever of other-live, leaderboards
        # or awards ran last already ended its own loop with one -- the
        # same reasoning as above, just at the other end of the block.
        #
        # A small tail so the last item does not butt against the first when
        # the strip wraps, and a floor of one panel so a short strip still
        # fills the screen.
        x = min(x + 12, scratch.width)
        return scratch.crop((0, 0, max(self.width, x), self.height)), clock_state

    def set_static_panel(self, panel) -> None:
        """Fix a card to the left module, or clear it with None."""
        self._static_panel = panel

    def scroll_window(self) -> int:
        """Width the strip actually scrolls in, once any panel is reserved."""
        if self._static_panel is None:
            return self.width
        return max(1, self.width - self._static_panel.width - 1)

    def adopt_pending(self) -> bool:
        """Swap in a rebuilt strip. Call only at the seam.

        Returns True if a swap happened, so the caller can reset its offset
        against the new width. Locked against the background build thread,
        which writes _pending/_pending_key from the same completion callback
        this reads and clears.
        """
        with self._build_lock:
            if self._pending is None:
                return False
            self._strip_cache = self._pending
            self._strip_key = self._pending_key
            self._clock_box, self._clock_font, self._clock_shown = (
                self._pending_clock_state if self._pending_clock_state
                else (None, None, None)
            )
            self._pending = None
            self._pending_key = None
            self._pending_clock_state = None
            return True

    def has_pending(self) -> bool:
        return self._pending is not None

    def close(self) -> None:
        """Stop the compose worker process, if one was ever started.

        daemon=True already guarantees it doesn't outlive this process
        (same as the background thread already relied on), but an
        explicit stop here means a plugin disable/reload doesn't leave a
        worker sitting idle until the whole service eventually restarts.
        Safe to call whether or not use_process was ever on, or a worker
        was ever actually needed.
        """
        if self._compose_process is not None and self._compose_process.is_alive():
            try:
                if self._compose_conn is not None:
                    self._compose_conn.send(None)
            except Exception:
                pass
            self._compose_process.join(2.0)
            if self._compose_process.is_alive():
                self._compose_process.terminate()

    def scroll_span(self, strip: Image.Image) -> int:
        """Pixels to travel for one full pass.

        The strip already opens with a panel of clear space and closes with
        another, so its own width is the whole journey.
        """
        if strip is None:
            return 0
        return strip.width

    def render_static_panel(self, game: Dict, focus_abbr: str,
                            width: int, phase: int = 0) -> Optional[Image.Image]:
        """A fixed card for one live game, beside the scrolling strip.

        Laid out the way a broadcast ticker lays out a game: the clock or
        inning across the top, the two sides stacked with their scores
        right-aligned beneath it, and the sport's own situation on the last
        row. Stacking the sides rather than placing them left and right is
        what buys the room for that situation line -- and it is how a ticker
        does it, because a column of scores is read faster than a row.

        Four rows share the same one-row top-and-bottom margin as everything
        else on the strip, using the identical placement scheme proven on the
        leaderboard segment: each row's y is target_row = MARGIN + row_h * i,
        corrected through _text_top for this font's built-in leading. This
        method predates that scheme and, until now, used raw row_h arithmetic
        with no leading correction at all -- fine for the rows in the middle,
        but the top row sat wherever the font's leading happened to put it,
        and the bottom row's "height - row_h" math is exactly the class of
        bug that clipped the live game's score before the strip segments were
        fixed. On a display that lives inside a case, that margin is not
        cosmetic: rows outside it can be physically covered by the bezel.
        """
        if not game or width < 24:
            return None
        try:
            panel = Image.new("RGB", (width, self.height), (0, 0, 0))
            draw = ImageDraw.Draw(panel)
            font, row_h = self._fit_font(draw, 4, self.height)

            home = game.get("home") or {}
            away = game.get("away") or {}
            wanted = abbr_group(focus_abbr) if focus_abbr else set()
            if wanted and home.get("abbr", "").upper() in wanted:
                ours, theirs = home, away
            else:
                ours, theirs = away, home

            situation = game.get("situation") or {}
            kind = situation.get("kind")

            def row_y(i, sample="0"):
                return self._text_top(draw, font, self.MARGIN + row_h * i,
                                      sample=sample)

            # Row 0: where the game is.
            status = self._safe(game.get("status_detail") or "Live")
            if kind in ("football", "basketball") and situation.get("clock"):
                status = self._safe(
                    f"Q{game.get('period', '')} {situation['clock']}".strip())
            elif kind == "soccer" and situation.get("clock"):
                status = self._safe(situation["clock"])
            draw.text((1, row_y(0, sample=status)), status, font=font, fill=self.LIVE)

            # Rows 1 and 2: crest, abbreviation, score right-aligned.
            #
            # Confirmed on real hardware: a crest even 1px taller than its
            # own row_h bleeds into the row below it, and with three rows
            # stacked at zero gap (team, team, situation) that 1px compounds
            # all the way down -- the second crest touched the first, and
            # both crowded the bases/count/outs row beneath them. Capping at
            # row_h itself, not row_h + 1, keeps each crest inside its own
            # row.
            crest_size = min(row_h, 9)
            content_end_x = 0
            score_start_x = width
            for index, side in enumerate((ours, theirs)):
                y = self.MARGIN + row_h * (index + 1)
                x = 1
                crest = self._logo(game.get("league", ""),
                                   side.get("abbr", ""), crest_size)
                if crest is not None:
                    oy = y + max(0, (row_h - crest.height) // 2)
                    panel.paste(crest, (x, oy), crest)
                    x += crest.width + 2

                label = self._safe(side.get("abbr", ""))
                if kind == "football" and situation.get("possession") and \
                        situation["possession"].upper() == label.upper():
                    # A ticker marks possession beside the team that has
                    # it. Plain "*", not the Unicode "\u25cf" this drew
                    # before -- real BDF fonts can't encode that at all
                    # (confirmed on the Pi: crashed both the draw and the
                    # width measurement below with UnicodeEncodeError),
                    # and just _safe()-stripping it would have silently
                    # dropped the marker with nothing left indicating
                    # possession.
                    label = f"{label}*"
                draw.text((x, row_y(index + 1)), label, font=font,
                          fill=self.LABEL)
                content_end_x = max(
                    content_end_x, x + self._measure(draw, label, font)[0])

                score = self._safe(side.get("score", ""))
                if score:
                    sw = self._measure(draw, score, font)[0]
                    score_x = width - sw - 1
                    draw.text((score_x, row_y(index + 1)), score,
                              font=font, fill=self.VALUE)
                    score_start_x = min(score_start_x, score_x)

            # Baseball: the bases diamond lives beside the team names, not
            # stacked into the bottom row with the count and outs.
            #
            # Confirmed on real hardware: squeezed into row 3, the diamond's
            # own geometry (its two bottom markers sit lower than the top
            # one) ran past the panel's bottom edge and was clipped clean
            # off, no matter how the origin was nudged -- there was simply
            # not enough of row 3's ~8px left over for it once the count and
            # outs also had to fit. The gap between the team names and the
            # scores spans both team rows (16px against the diamond's own
            # ~7px need), so it fits with room to spare there, and row 3 is
            # freed up entirely for the count, outs and batter/pitcher name.
            if kind == "baseball":
                diamond_size = 3
                gap_start = content_end_x + 3
                gap_end = (score_start_x - 2) if score_start_x < width \
                    else width - 2
                diamond_span = diamond_size * 3 + 2
                if gap_end - gap_start >= diamond_span:
                    band_top = self.MARGIN + row_h
                    diamond_h = diamond_size * 2 + 1
                    diamond_y = band_top + max(
                        0, (row_h * 2 - diamond_h) // 2)
                    diamond_x = gap_start + max(
                        0, (gap_end - gap_start - diamond_span) // 2)
                    self._draw_bases(draw, diamond_x, diamond_y, situation,
                                     diamond_size)

            # Row 3: the sport's own situation, at the bottom margin.
            #
            # Sixty-four pixels will not hold the bases, the count, the outs
            # AND a player name at once -- the name ends up truncated to a
            # letter. A ticker solves this by alternating, so the row shows
            # the situation, then who is involved in it, and back.
            bottom_y = row_y(3)
            # Graphics (the bases diamond, the outs pips) are not text, so
            # they anchor to the same row's un-corrected target instead of
            # the leading-compensated text y.
            bottom_graphic = self.MARGIN + row_h * 3

            if kind == "baseball":
                batter = situation.get("batter") or ""
                pitcher = situation.get("pitcher") or ""
                show_people = bool(batter or pitcher) and phase % 2 == 1

                if show_people:
                    if batter:
                        text = self._truncate(
                            draw, self._safe(f"AB {batter}"), font, width - 2)
                        draw.text((1, bottom_y), text, font=font, fill=self.LABEL)
                    elif pitcher:
                        text = self._truncate(
                            draw, self._safe(f"P {pitcher}"), font, width - 2)
                        draw.text((1, bottom_y), text, font=font, fill=self.LABEL)
                else:
                    x = 1
                    count = (f"{situation.get('balls', 0)}-"
                             f"{situation.get('strikes', 0)}")
                    draw.text((x, bottom_y), self._safe(count), font=font,
                              fill=self.LABEL)
                    x += self._measure(draw, count, font)[0] + 4
                    self._draw_outs(draw, x, bottom_graphic + 2,
                                    situation.get("outs", 0))

            elif kind == "football":
                down = situation.get("down_distance") or ""
                spot = situation.get("yard_line") or ""
                text = " ".join(p for p in (down, spot) if p)
                colour = (255, 80, 60) if situation.get("red_zone") else self.DIM
                text = self._truncate(draw, self._safe(text), font, width - 2)
                if text:
                    draw.text((1, bottom_y), text, font=font, fill=colour)

            return panel
        except Exception as e:
            self.logger.error("Error drawing static panel: %s", e, exc_info=True)
            return None

    def render_clock_weather_panel(self, now, weather: Dict,
                                   width: int) -> Optional[Image.Image]:
        """A fixed clock-and-weather card for the left module, for whenever
        no live game is pinned there.

        The static panel already exists to hold the left module still while
        the rest of the strip scrolls past it -- that mechanism is what
        makes this possible without ever touching the scroll itself; this
        is a second, lower-priority thing to put in that same slot, not a
        new way of freezing part of the display. A live game still wins
        outright when there is one, the same as before -- a live score is
        the one thing here that will not keep, and the time is already
        visible in the scrolling clock segment a live game replaces too.
        Three rows (time, date, temperature) at the shared body font, the
        same font and row budget already proven to fit three rows on a
        leaderboard. Sized to its own content rather than always filling
        the full module width: centring inside a fixed 64px box still left
        dead space on both sides whenever the content was narrower than
        the box, since centring only moves where the empty space sits, not
        how much of it there is. A narrower panel here also means more of
        the module goes back to the scroll.
        """
        if width < 24:
            return None
        try:
            # Measured against a throwaway surface first, since the actual
            # panel's own size depends on the measurement.
            probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
            font, row_h = self._fit_font(probe, 4, self.height)

            clock_text = self._safe(self._clock_text(now, military=True))
            try:
                date_text = self._safe(
                    f"{ESPNGamesSource.day_abbr(now)} {now.month}/{now.day}")
            except Exception:
                date_text = ""

            weather = weather or {}
            temp = weather.get("now_temp")
            if temp is None:
                temp = weather.get("temp")
            unit = weather.get("units", "F")
            condition = weather.get("now_condition") or weather.get("condition") or ""

            icon_size = row_h if temp is not None else 0
            temp_text = self._safe(f"{temp}{unit}") if temp is not None else ""
            row3_w = (icon_size + 3 if icon_size >= 4 else 0) + (
                self._measure(probe, temp_text, font)[0] if temp_text else 0)
            widths = [
                self._measure(probe, clock_text, font)[0] if clock_text else 0,
                self._measure(probe, date_text, font)[0] if date_text else 0,
                row3_w,
            ]
            content_w = max(widths) if any(widths) else 0
            panel_w = max(24, min(width, content_w + self.MARGIN * 2)) if content_w else width

            panel = Image.new("RGB", (panel_w, self.height), (0, 0, 0))
            draw = ImageDraw.Draw(panel)
            inner_w = panel_w - self.MARGIN * 2

            rows = 3 if temp is not None else (2 if date_text else 1)
            start_row = self._vblock_start(row_h, rows)

            def centred_x(w):
                return self.MARGIN + max(0, (inner_w - w) // 2)

            if clock_text:
                y = self._text_top(draw, font, start_row)
                self._draw_clock_face(
                    draw, centred_x(widths[0]), y, clock_text, font, self.LABEL)

            if date_text:
                y = self._text_top(draw, font, start_row + row_h)
                draw.text((centred_x(widths[1]), y), date_text, font=font,
                          fill=self.DIM)

            if temp is not None:
                row3_top = start_row + row_h * 2
                cursor = centred_x(row3_w)
                if icon_size >= 4:
                    icon_top = row3_top + max(0, (row_h - icon_size) // 2)
                    cursor += self._draw_weather_icon(
                        draw, cursor, icon_top, icon_size,
                        self.condition_kind(condition)) + 3
                y = self._text_top(draw, font, row3_top)
                draw.text((cursor, y), temp_text, font=font, fill=self.VALUE)

            return panel
        except Exception as e:
            self.logger.debug("Error drawing clock/weather panel: %s", e)
            return None

    def draw_strip(self, strip: Image.Image, offset: float) -> bool:
        """Paste the strip at a horizontal offset, wrapping cleanly."""
        if strip is None:
            return False
        try:
            img = Image.new("RGB", (self.width, self.height), (0, 0, 0))

            # A static panel takes the left module and the strip scrolls in
            # what is left, so a game in progress stays put while everything
            # else keeps moving.
            static = self._static_panel
            reserved = static.width + 1 if static is not None else 0
            window = max(1, self.width - reserved)

            span = max(1, strip.width)
            paste_x = -int(offset) % span
            scrolled = Image.new("RGB", (window, self.height), (0, 0, 0))
            scrolled.paste(strip, (paste_x - span, 0))
            if paste_x < window:
                scrolled.paste(strip, (paste_x, 0))

            if static is not None:
                img.paste(static, (0, 0))
                # A hairline so the fixed panel reads as its own module
                # rather than as content that has stopped scrolling -- the
                # same divider style used everywhere else on the strip.
                ImageDraw.Draw(img).line(
                    [(static.width, 3), (static.width, self.height - 4)],
                    fill=self.DIVIDER)
            img.paste(scrolled, (reserved, 0))
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
            return True
        except Exception as e:
            self.logger.error("Error drawing strip: %s", e, exc_info=True)
            return False

    def draw_message(self, message: str) -> bool:
        try:
            img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
            draw = ImageDraw.Draw(img)
            font, _ = self._fit_font(draw, 3, self.height)
            tw, th = self._measure(draw, message, font)
            draw.text((max(0, (self.width - tw) // 2),
                       max(0, (self.height - th) // 2)),
                      self._safe(message), font=font, fill=self.DIM)
            self.display_manager.image.paste(img, (0, 0))
            self.display_manager.update_display()
            return True
        except Exception as e:
            self.logger.error("Error drawing message: %s", e)
            return False
