"""
Game card renderer.

Layout conventions are carried over from the baseball-stats leaderboard,
because they were arrived at the hard way on this exact hardware:

  * sizing derived from the panel, never hardcoded for one build
  * one font for everything on a card, so nothing looks like a different design
  * text inset from the left edge, which the module frame otherwise eats
  * spare height shared rather than dumped below the last row
  * names folded to ASCII, because the bitmap fonts have no accented glyphs
  * bitmap fonts compiled from BDF, not loaded as if they were PIL fonts

A card is four rows on a 32px panel:

    NYY @ BOS            Final      <- matchup and status
    NYY  7                          <- away, score
    BOS  4                          <- home, score
    A.Judge 2-4 HR 3RBI             <- notable performer
"""

import logging
import os
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

try:
    from PIL import BdfFontFile
except ImportError:  # pragma: no cover
    BdfFontFile = None


MIN_LEGIBLE_ROW_H = 6


class CardProfile:
    """Layout constants derived from the panel's real dimensions."""

    NARROW_MAX = 95
    STANDARD_MAX = 159

    def __init__(self, width: int, height: int):
        self.width = max(1, int(width))
        self.height = max(1, int(height))

    @property
    def tier(self) -> str:
        if self.width <= self.NARROW_MAX:
            return "narrow"
        if self.width <= self.STANDARD_MAX:
            return "standard"
        return "wide"

    @property
    def text_inset(self) -> int:
        """Text flush to x=0 loses its first column to the module frame."""
        return 4 if self.tier == "wide" else 2

    @property
    def column_gap(self) -> int:
        return 3 if self.tier == "wide" else 2

    @property
    def show_records(self) -> bool:
        return self.tier != "narrow"

    @property
    def show_leaders(self) -> bool:
        """A notable-player line needs width to say anything useful."""
        return self.tier != "narrow"

    @property
    def max_row_leading(self) -> int:
        return 3 if self.height > 32 else 2

    # Colours. Bright by default: on an emissive panel a mid grey is not
    # subdued, it is simply dim.
    @property
    def label_color(self):
        return (255, 255, 255)

    @property
    def score_color(self):
        return (0, 220, 255)

    @property
    def winner_color(self):
        return (255, 255, 255)

    @property
    def loser_color(self):
        return (150, 150, 160)

    @property
    def leader_color(self):
        return (215, 215, 220)

    @property
    def live_color(self):
        return (0, 230, 90)

    @property
    def final_color(self):
        return (200, 200, 210)

    @property
    def upcoming_color(self):
        return (255, 200, 0)

    def describe(self) -> str:
        return f"{self.width}x{self.height} ({self.tier})"


class GameCardRenderer:
    """Draws one game as a card."""

    FONT_LADDER = ["5x7.bdf", "4x6.bdf", "6x10.bdf"]
    FALLBACK_SIZES = [8, 7, 6, 5, 4]

    def __init__(self, display_manager, config: Dict[str, Any],
                 logger: logging.Logger, logo_manager=None):
        self.display_manager = display_manager
        self.config = config or {}
        self.logger = logger
        self.logo_manager = logo_manager

        try:
            self.width = display_manager.matrix.width
            self.height = display_manager.matrix.height
        except AttributeError:
            self.width = getattr(display_manager, "width", 128)
            self.height = getattr(display_manager, "height", 32)

        self.profile = CardProfile(self.width, self.height)
        self._font_cache: Dict[Any, Any] = {}

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
        """BDF must be compiled; ImageFont.load reads PIL's .pil format only.

        Getting this wrong is invisible -- both load attempts raise, the
        failure is swallowed, and everything silently falls back to a scaled
        outline font that looks soft on a matrix.
        """
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

    def _fit_font(self, draw, rows_needed: int, available: int,
                  min_row_h: int = MIN_LEGIBLE_ROW_H):
        """Largest font whose rows fit, never below the legibility floor."""
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
        for font in candidates:
            _, h = self._measure(draw, "Ay", font)
            row_h = h + 1
            if row_h < min_row_h:
                continue
            if smallest is None or row_h < smallest[1]:
                smallest = (font, row_h)
            if rows_needed * row_h <= available:
                return font, row_h
        if smallest:
            return smallest
        font = candidates[0]
        _, h = self._measure(draw, "Ay", font)
        return font, h + 1

    def font_report(self) -> str:
        parts = []
        for name in self.FONT_LADDER:
            parts.append(f"{name}: {'loaded' if self._named_font(name) else 'missing'}")
        return "; ".join(parts)

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

    def _truncate(self, draw, text: str, font, max_width: int) -> str:
        if max_width <= 0:
            return ""
        if self._measure(draw, text, font)[0] <= max_width:
            return text
        trimmed = text
        while trimmed and self._measure(draw, trimmed, font)[0] > max_width:
            trimmed = trimmed[:-1]
        return trimmed

    def _new_frame(self):
        img = Image.new("RGB", (self.width, self.height), (0, 0, 0))
        return img, ImageDraw.Draw(img)

    def _push(self, img) -> None:
        self.display_manager.image.paste(img, (0, 0))
        self.display_manager.update_display()

    def _logo(self, league: str, abbr: str, size: int,
              espn_id: str = ""):
        if not self.logo_manager or not abbr:
            return None
        try:
            return self.logo_manager.get_logo(
                league, abbr, size, espn_id=espn_id or None)
        except Exception:
            return None

    def _distribute_slack(self, rows: int, row_h: int):
        """Share spare height rather than leaving it below the last row."""
        used = rows * row_h
        slack = max(0, self.height - used)
        if slack <= 0 or rows <= 0:
            return 0, row_h
        share = min(slack // (rows + 1), self.profile.max_row_leading)
        if share and (share + rows * (row_h + share)) >= self.height:
            share = max(0, share - 1)
        leftover = max(0, slack - share * (rows + 1))
        return share + leftover // 2, row_h + share

    # ------------------------------------------------------------------
    # The Card
    # ------------------------------------------------------------------
    def draw_game(self, game: Dict) -> bool:
        """Draw one game card. Returns False if there is nothing to draw."""
        if not game:
            return False

        try:
            cfg = self.config.get("customization", {}).get("card", {})
            show_logos = cfg.get("show_logos", True)
            show_leaders = cfg.get("show_leaders", True) and self.profile.show_leaders

            away = game.get("away") or {}
            home = game.get("home") or {}
            state = game.get("state", "upcoming")
            leaders = game.get("leaders") or []

            # Header, two team rows, and a performer line when there is one.
            wants_leader = bool(leaders) and show_leaders and state != "upcoming"
            rows = 3 + (1 if wants_leader else 0)

            img, draw = self._new_frame()
            font, row_h = self._fit_font(draw, rows, self.height)
            if rows * row_h > self.height and wants_leader:
                # No room for the performer line; the score matters more.
                wants_leader = False
                rows = 3
                font, row_h = self._fit_font(draw, rows, self.height)

            top, pitch = self._distribute_slack(rows, row_h)
            left = self.profile.text_inset
            y = top

            self._draw_header(draw, game, font, left, y)
            y += pitch

            logo_size = max(5, row_h - 1)
            logos = {
                "away": self._logo(
                    game.get("league", ""), away.get("abbr", ""), logo_size,
                    espn_id=str(away.get("id") or ""),
                ) if show_logos else None,
                "home": self._logo(
                    game.get("league", ""), home.get("abbr", ""), logo_size,
                    espn_id=str(home.get("id") or ""),
                ) if show_logos else None,
            }
            logo_w = max((l.width for l in logos.values() if l is not None),
                         default=0)

            for side_key, side in (("away", away), ("home", home)):
                self._draw_team_row(
                    img, draw, side, logos[side_key], logo_w, font,
                    left, y, row_h, state, game,
                )
                y += pitch

            if wants_leader:
                self._draw_leader_row(draw, leaders, font, left, y)

            self._push(img)
            return True

        except Exception as e:
            self.logger.error("Error drawing game card: %s", e, exc_info=True)
            return False

    # ------------------------------------------------------------------
    def _draw_header(self, draw, game: Dict, font, left: int, y: int) -> None:
        """Matchup on the left, status pinned right.

        The status is the first thing you look for -- is it on, is it over,
        when is it -- so it gets the fixed right edge and the colour.
        """
        away = (game.get("away") or {}).get("abbr", "")
        home = (game.get("home") or {}).get("abbr", "")
        state = game.get("state", "upcoming")

        if state == "live":
            status = self._live_status(game)
            colour = self.profile.live_color
        elif state == "final":
            status = "Final"
            colour = self.profile.final_color
        else:
            status = game.get("start_label") or ""
            colour = self.profile.upcoming_color

        status = self._safe(status)
        status_w = self._measure(draw, status, font)[0] if status else 0
        if status:
            draw.text((self.width - status_w - 1, y), status, font=font, fill=colour)

        matchup = f"{away} @ {home}"
        room = self.width - left - status_w - self.profile.column_gap * 2
        matchup = self._truncate(draw, matchup, font, room)
        draw.text((left, y), self._safe(matchup), font=font,
                  fill=self.profile.label_color)

    @staticmethod
    def _live_status(game: Dict) -> str:
        """Compact in-progress status, e.g. "T7" or "Q3 4:12"."""
        detail = (game.get("status_detail") or "").strip()
        clock = (game.get("clock") or "").strip()
        if game.get("league") == "mlb":
            # Baseball has no clock; the inning is the whole story.
            return detail or "Live"
        period = game.get("period") or 0
        if period and clock:
            return f"Q{period} {clock}"
        return detail or "Live"

    def _draw_team_row(self, img, draw, side: Dict, logo, logo_w: int, font,
                       left: int, y: int, row_h: int, state: str,
                       game: Dict) -> None:
        """Logo, abbreviation, record, then the score pinned right."""
        x = left
        if logo is not None:
            oy = y + max(0, (row_h - logo.height) // 2)
            try:
                img.paste(logo, (x, oy), logo)
            except Exception:
                img.paste(logo.convert("RGB"), (x, oy))
        if logo_w:
            x += logo_w + self.profile.column_gap

        # A finished game dims the loser, so the result reads without having
        # to compare two numbers.
        if state == "final":
            colour = (self.profile.winner_color if side.get("winner")
                      else self.profile.loser_color)
        else:
            colour = self.profile.label_color

        score = self._safe(side.get("score", ""))
        score_w = self._measure(draw, score, font)[0] if score else 0
        if score:
            draw.text((self.width - score_w - 1, y), score, font=font,
                      fill=self.profile.score_color if state != "final" else colour)

        label = self._safe(side.get("abbr", ""))
        if state == "upcoming" and self.profile.show_records and side.get("record"):
            label = f"{label} {side['record']}"
        room = self.width - x - score_w - self.profile.column_gap * 2
        draw.text((x, y), self._truncate(draw, label, font, room), font=font,
                  fill=colour)

    def _draw_leader_row(self, draw, leaders: List[Dict], font,
                         left: int, y: int) -> None:
        """The notable performer, as a scoreboard shows under a final.

        One line, so the strongest single performance wins the space rather
        than two half-truncated ones.
        """
        if not leaders:
            return
        best = leaders[0]
        text = f"{best.get('name', '')} {best.get('line', '')}".strip()
        text = self._truncate(draw, self._safe(text), font,
                              self.width - left - 2)
        if text:
            draw.text((left, y), text, font=font, fill=self.profile.leader_color)

    # ------------------------------------------------------------------
    def draw_message(self, message: str) -> bool:
        """Placeholder for a cold start or an empty schedule."""
        try:
            img, draw = self._new_frame()
            font, _ = self._fit_font(draw, 3, self.height)
            tw, th = self._measure(draw, message, font)
            draw.text(
                (max(0, (self.width - tw) // 2), max(0, (self.height - th) // 2)),
                self._safe(message), font=font, fill=self.profile.leader_color,
            )
            self._push(img)
            return True
        except Exception as e:
            self.logger.error("Error drawing message: %s", e)
            return False
