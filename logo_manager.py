"""
Team Logo Manager

Loads small MLB team logos for the leaderboard rows.

Lookup order, cheapest first:
    1. In-memory cache (already loaded this session)
    2. On-disk cache in this plugin's own logos/ directory
    3. Any logo directory the scoreboard plugin already populated -- no reason
       to download a second copy of a logo already sitting on the Pi
    4. Download from ESPN's CDN and write it to (2) for next time

Every step degrades to None rather than raising, and the renderer falls back to
drawing the text abbreviation. A missing logo should never blank a screen.

Abbreviation note: MLB StatsAPI and ESPN disagree on several teams
(StatsAPI "AZ" vs ESPN "ARI", "CWS" vs "CHW"), so abbreviations are translated
before building a filename or URL.
"""

import logging
import os
from typing import Dict, List, Optional

from PIL import Image

try:
    import requests
except ImportError:  # pragma: no cover - requests is a hard dep of the plugin
    requests = None


# StatsAPI abbreviation -> candidate ESPN abbreviations, tried in order.
# Most teams match exactly and are omitted; only the disagreements are listed.
ESPN_ABBR_OVERRIDES: Dict[str, List[str]] = {
    "AZ": ["ARI"],
    "ARI": ["ARI", "AZ"],
    "CWS": ["CHW", "CWS"],
    "CHW": ["CHW", "CWS"],
    # The Athletics dropped "Oakland" in 2025; feeds are inconsistent about
    # which abbreviation they use, so try both.
    "ATH": ["ATH", "OAK"],
    "OAK": ["OAK", "ATH"],
    "WSH": ["WSH", "WSN"],
    "SD": ["SD", "SDP"],
    "SF": ["SF", "SFG"],
    "TB": ["TB", "TBR"],
    "KC": ["KC", "KCR"],
    # Basketball and football: ESPN's own spellings, which are not always the
    # ones a person would guess. The Knicks are "NY", not "NYK".
    # Upper case, because the disk lookup is case-sensitive; the download
    # path lowercases these itself when building a URL.
    "NY": ["NY", "NYK"],
    "NYK": ["NYK", "NY"],
    "GS": ["GS", "GSW"],
    "SA": ["SA", "SAS"],
    "NO": ["NO", "NOP"],
    "UTAH": ["UTAH", "UTA"],
    "LAR": ["LAR", "LA"],
    "JAX": ["JAX", "JAC"],
}

# Soccer's crest CDN is keyed by ESPN's own numeric team id, not the
# abbreviation the way every other league here is -- confirmed against a
# real request, where the abbreviation path 404s and the numeric one
# doesn't. Only entries actually configured need listing; add a team's
# ESPN id here (visible in ESPN's own team URLs, /soccer/team/_/id/<id>)
# the first time a new one is followed.
ESPN_LOGO_ID_OVERRIDES: Dict[str, str] = {
    "BAR": "83",  # Barcelona
}


class TeamLogoManager:
    """Loads and caches small team logos."""

    ESPN_LOGO_URL = "https://a.espncdn.com/i/teamlogos/{league}/500/{abbr}.png"

    # Directories the scoreboard plugin may already have populated. Checked
    # before downloading anything.
    SHARED_LOGO_DIRS = [
        "~/LEDMatrix/assets/sports/{league}_logos",
        "~/LEDMatrix/assets/logos/{league}",
        "~/LEDMatrix/plugin-repos/baseball-scoreboard/assets/{league}_logos",
        "~/LEDMatrix/plugin-repos/baseball-scoreboard/logos",
    ]

    def __init__(
        self,
        logger: logging.Logger,
        cache_dir: Optional[str] = None,
        allow_download: bool = True,
        extra_dirs: Optional[List[str]] = None,
    ):
        self.logger = logger
        self.allow_download = allow_download and requests is not None

        self.cache_dir = os.path.expanduser(
            cache_dir or os.path.join(os.path.dirname(__file__), "logos")
        )

        search = list(extra_dirs or [])
        search.append(self.cache_dir)
        search.extend(self.SHARED_LOGO_DIRS)
        self.search_dirs = [os.path.expanduser(d) for d in search]

        # (abbr, size) -> Image, so repeated rows do not re-open files.
        self._cache: Dict[tuple, Optional[Image.Image]] = {}
        # Abbreviations already known to be unavailable, so a missing logo
        # costs one failed lookup per session rather than one per frame.
        self._misses: set = set()

    # ------------------------------------------------------------------
    def _candidates(self, abbr: str) -> List[str]:
        abbr = (abbr or "").strip().upper()
        if not abbr:
            return []
        return ESPN_ABBR_OVERRIDES.get(abbr, [abbr])

    def _find_on_disk(self, league: str, abbr: str) -> Optional[str]:
        for candidate in self._candidates(abbr):
            for directory in self._dirs_for(league):
                if not os.path.isdir(directory):
                    continue
                for ext in (".png", ".PNG", ".gif", ".jpg"):
                    path = os.path.join(directory, candidate + ext)
                    if os.path.exists(path):
                        return path
                # Case-insensitive fallback: one path saves "NYY.png" and
                # another looks for "nyy.png", and a filesystem that cares
                # about the difference turns that into a missing logo.
                try:
                    wanted = {candidate.lower() + e
                              for e in (".png", ".gif", ".jpg")}
                    for entry in os.listdir(directory):
                        if entry.lower() in wanted:
                            return os.path.join(directory, entry)
                except OSError:
                    pass
        return None

    def _dirs_for(self, league: str):
        """Search directories for one league, plus this plugin's own cache."""
        out = [os.path.join(self.cache_dir, league)]
        for template in self.SHARED_LOGO_DIRS:
            out.append(os.path.expanduser(template.format(league=league)))
        return out

    def _download(self, league: str, abbr: str) -> Optional[str]:
        if not self.allow_download:
            return None

        target_dir = os.path.join(self.cache_dir, league)
        os.makedirs(target_dir, exist_ok=True)

        for candidate in self._candidates(abbr):
            url_path = ESPN_LOGO_ID_OVERRIDES.get(candidate, candidate.lower())
            url = self.ESPN_LOGO_URL.format(league=league, abbr=url_path)
            try:
                response = requests.get(url, timeout=10)
                if response.status_code != 200 or not response.content:
                    continue
                path = os.path.join(target_dir, candidate + ".png")
                with open(path, "wb") as f:
                    f.write(response.content)
                self.logger.info(f"Downloaded logo for {abbr} -> {path}")
                return path
            except Exception as e:
                self.logger.debug(f"Logo download failed for {candidate}: {e}")

        return None

    # ------------------------------------------------------------------
    def get_logo(self, league: str, abbr: str, size: int) -> Optional[Image.Image]:
        """Return an RGBA logo scaled to fit a size x size box, or None.

        Called on the render path, so a miss is remembered and never retried
        within the session -- a Pi drawing 100 frames a second must not attempt
        100 failed HTTP requests.
        """
        abbr = (abbr or "").strip().upper()
        if not abbr or size < 3:
            return None

        key = (league, abbr, size)
        if key in self._cache:
            return self._cache[key]
        if (league, abbr) in self._misses:
            return None

        path = self._find_on_disk(league, abbr) or self._download(league, abbr)
        if not path:
            self._misses.add((league, abbr))
            self._cache[key] = None
            return None

        try:
            logo = Image.open(path).convert("RGBA")
            # thumbnail preserves aspect ratio; a 500px source becomes a
            # legible 8-11px glyph on the panel.
            logo.thumbnail((size, size), Image.LANCZOS)
            logo = self._lift_dark(logo)
            self._cache[key] = logo
            return logo
        except Exception as e:
            self.logger.debug(f"Could not open logo {path}: {e}")
            self._misses.add((league, abbr))
            self._cache[key] = None
            return None

    @staticmethod
    def _lift_dark(logo: Image.Image, floor: int = 140) -> Image.Image:
        """Brighten a logo that would otherwise vanish into a black panel.

        Navy and dark green crests -- the Yankees and the Giants among them --
        are near-black on an unlit background, so at eleven pixels they read
        as a smudge or as nothing at all. Broadcast graphics solve this by
        placing dark marks on a light plate; a matrix has no plate, so the
        mark itself has to carry.

        The lift is proportional and applied only to logos that need it, so a
        red or white crest is untouched and no team's colour is replaced --
        the hue is preserved, only the value is raised. The floor is set where
        a navy crest reads clearly from across a room without becoming a
        different blue.
        """
        try:
            pixels = logo.load()
            width, height = logo.size

            total, count = 0, 0
            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    if a < 40:
                        continue
                    total += max(r, g, b)
                    count += 1
            if not count:
                return logo

            brightest = total / count
            if brightest >= floor:
                return logo

            # Scale so the average peak channel reaches the floor, capped so
            # a very dark crest is lifted but not washed to white.
            gain = min(floor / max(brightest, 1.0), 3.2)
            for y in range(height):
                for x in range(width):
                    r, g, b, a = pixels[x, y]
                    if a < 40:
                        continue
                    pixels[x, y] = (
                        min(255, int(r * gain)),
                        min(255, int(g * gain)),
                        min(255, int(b * gain)),
                        a,
                    )
            return logo
        except Exception:
            return logo

    def get_league_logo(self, league: str, size: int):
        """The league's own mark, for a section banner.

        ESPN serves these from a separate path to team crests -- leagues, not
        teams -- so it cannot go through the same lookup. Worth the special
        case: a block of statistics with no crest above it gives the reader
        nothing to tell them the subject has changed.
        """
        key = ("league", league, size)
        if key in self._cache:
            return self._cache[key]
        if ("league", league) in self._misses:
            return None

        path = None
        for directory in self._dirs_for(league):
            for name in (f"{league}.png", f"{league}_league.png", "league.png"):
                candidate = os.path.join(directory, name)
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path:
                break

        if path is None and self.allow_download:
            target_dir = os.path.join(self.cache_dir, league)
            os.makedirs(target_dir, exist_ok=True)
            url = f"https://a.espncdn.com/i/teamlogos/leagues/500/{league}.png"
            try:
                response = requests.get(url, timeout=15, headers={
                    "User-Agent": "LEDMatrix/1.0"})
                response.raise_for_status()
                path = os.path.join(target_dir, f"{league}.png")
                with open(path, "wb") as handle:
                    handle.write(response.content)
                self.logger.info("Downloaded %s league logo", league.upper())
            except Exception as e:
                self.logger.debug("No league logo for %s: %s", league, e)
                path = None

        if not path:
            self._misses.add(("league", league))
            self._cache[key] = None
            return None

        try:
            logo = Image.open(path).convert("RGBA")
            logo.thumbnail((size, size), Image.LANCZOS)
            logo = self._lift_dark(logo)
            self._cache[key] = logo
            return logo
        except Exception as e:
            self.logger.debug("Could not open league logo %s: %s", path, e)
            self._misses.add(("league", league))
            self._cache[key] = None
            return None

    def get_scope_logo(self, scope: str, size: int):
        """The American or National League's own mark, for a leaderboard
        or award list scoped to one of them.

        Not the same URL as get_league_logo's: AL and NL are not "leagues"
        in ESPN's own sense the way MLB, NBA and NFL are -- confirmed live,
        .../teamlogos/leagues/500/al.png 404s. They turned up instead at
        the path ESPN uses for scoreboard group marks.
        """
        if scope not in ("al", "nl"):
            return None
        key = ("scope", scope, size)
        if key in self._cache:
            return self._cache[key]
        if ("scope", scope) in self._misses:
            return None

        path = None
        for directory in self._dirs_for(scope):
            for name in (f"{scope}.png", f"{scope}_scope.png"):
                candidate = os.path.join(directory, name)
                if os.path.exists(candidate):
                    path = candidate
                    break
            if path:
                break

        if path is None and self.allow_download:
            target_dir = os.path.join(self.cache_dir, scope)
            os.makedirs(target_dir, exist_ok=True)
            url = f"https://a.espncdn.com/i/teamlogos/mlb/500/scoreboard/{scope}.png"
            try:
                response = requests.get(url, timeout=15, headers={
                    "User-Agent": "LEDMatrix/1.0"})
                response.raise_for_status()
                path = os.path.join(target_dir, f"{scope}.png")
                with open(path, "wb") as handle:
                    handle.write(response.content)
                self.logger.info("Downloaded %s mark", scope.upper())
            except Exception as e:
                self.logger.debug("No scope logo for %s: %s", scope, e)
                path = None

        if not path:
            self._misses.add(("scope", scope))
            self._cache[key] = None
            return None

        try:
            logo = Image.open(path).convert("RGBA")
            logo.thumbnail((size, size), Image.LANCZOS)
            logo = self._lift_dark(logo)
            self._cache[key] = logo
            return logo
        except Exception as e:
            self.logger.debug("Could not open scope logo %s: %s", path, e)
            self._misses.add(("scope", scope))
            self._cache[key] = None
            return None

    def prefetch(self, pairs, size: int) -> int:
        """Warm the cache off the render path, from (league, abbr) pairs."""
        loaded = 0
        for league, abbr in pairs:
            if self.get_logo(league, abbr, size) is not None:
                loaded += 1
        return loaded
