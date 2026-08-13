# CLAUDE.md — local-scoreboard LEDMatrix plugin

Context for continuing this project in Claude Code. Read this before making
changes — several of these were expensive to learn once already.

## What this is

A LEDMatrix plugin (id `local-scoreboard`, class `LocalScoreboardPlugin`,
previously shipped privately as `nyc-teams`/`NYCTeamsPlugin` before a public
rename). Deployed here to a Raspberry Pi driving 3× 64×32 4mm panels chained
to 192×32px (hostname `ledpi`, user `sportspi`), but every team, city and
date in it is configuration, not code — this repo is meant to run for
anyone's own teams and city, not just this one household's.

One continuous horizontally scrolling strip: clock, weather (US NWS, free
and keyless), moon phase, personal countdowns, followed teams (Yankees/
Mets/Knicks/Nets/Giants shipped as the example roster), other live games
league-wide, MLB season leaders, MLB award watch lists, and a fixed
live-game panel on the left module. Leaderboards/awards/countdowns hide
themselves entirely while any game anywhere is live, and reappear once
nothing is.

Current version: see `manifest.json`. Bump it and add a changelog entry on
every change — this has been the working discipline throughout.

## The most important thing: sandbox fonts ≠ real fonts

This is the single most consequential lesson from building this. Testing in
a sandbox without real BDF font files uses PIL's `ImageFont.load_default()`
as a fallback, which has **~2-3px of built-in top leading**. The real BDF
fonts on the Pi (`5x7.bdf`, `4x6.bdf`, `6x10.bdf`, compiled via
`BdfFontFile`), have **zero leading**. Row heights differ too (a real 5x7
font's row_h came back as 8, not 7).

This gap caused real, shipped bugs that looked correct in every sandbox
render and test, and were provably broken on the actual device. Two
diagnostics exist because of this:

- `diagnose_render.py` — imports `strip_renderer.py` directly and renders
  real segments using whichever fonts actually load, reporting exact pixel
  margins. **Run this on the Pi after any layout change**, not just the
  local test suite.
- `diagnose_weather.py` / `diagnose_weather_fetch.py` — same idea for the
  weather data path.

If you don't have Pi access in this session, say so explicitly rather than
asserting a layout fix works. A sandbox-only render is not sufficient
evidence for anything involving font metrics.

## The second most important thing: manifest `update_interval` gates everything

LEDMatrix's own scheduler decides how often to even call this plugin's
`update()` — see `plugin_manager.py`'s `_get_plugin_update_interval()` on the
Pi. It checks the manifest's top-level `update_interval` first, then a
`update_interval` key in this plugin's own config.json entry, and **falls
back to a hardcoded 60 seconds if neither is set.** This is a completely
different layer from `idle_interval`/`live_interval` in this plugin's own
config: those decide what `update()` *does* once it runs (whether to
actually refetch), `update_interval` decides whether it runs *at all*.

`update_interval` was set once, then removed in an earlier version on the
belief that it "duplicated idle_interval's old role" — it doesn't. Losing it
silently capped every refresh this plugin does (live scores, streaks,
weather) at once a minute, no matter how urgently `live_interval=5` wanted
to run sooner. Nothing in `test_offline.py` could catch this: it is entirely
the host framework's behavior, invisible to any test that only exercises
this plugin's own code. It was only found by reading real `journalctl`
output during an actual live game and noticing "Refreshed games" log lines
were a full minute apart despite `live_interval=5` and a log line that
itself said "next check 5s" — the internal gate was working exactly as
written, it just wasn't being given the chance to run.

`test_offline.py` now asserts `update_interval` stays present and small in
`manifest.json`, but that only catches a regression in this exact form — if
this stops working again, re-read `plugin_manager.py`'s
`_get_plugin_update_interval()` on the Pi directly rather than assuming the
plugin's own refresh logic is at fault; the plugin's own gates can be
completely correct while a framework-level scheduling default overrides them
invisibly.

## Text positioning: always use `_text_top` / `_text_bottom` / `_vblock_start`

Never call `draw.text((x, N), ...)` with a bare row number. This font
family's leading means a glyph drawn "at row 1" doesn't put ink at row 1.
Use:

- `_text_top(draw, font, target_row)` — returns the y to pass to `draw.text`
  so ink starts at `target_row`.
- `_text_bottom(draw, font, target_bottom_row)` — same, for ink that should
  *end* at a given row.
- `_vblock_start(row_h, num_rows)` — returns the target row for the first of
  several stacked rows, **centred** within the panel height, splitting real
  slack evenly. Every stacked-text segment (leaderboard, awards, note,
  clock, section banners, weather) uses this. Don't hand-roll centering —
  every hand-rolled version so far top-anchored by accident and dumped all
  slack at the bottom.

`_largest_fit(draw, rows, available)` picks the **largest** font that
satisfies a row constraint, by scanning all candidates. `_fit_font` picks
the **first** font in `FONT_LADDER` preference order that fits — it is NOT
"biggest that fits," and using it where you want "as big as possible" is a
bug (this shipped once already, for the clock).

## Testing discipline

`test_offline.py` is the whole safety net — no LED panel or network needed.
Run it after every change:

```
python3 test_offline.py
```

Patterns established and expected to continue:

- **Real render checks, not just assertions.** Build a strip, save a PNG,
  actually look at it before claiming something works. Several bugs were
  only caught by looking at an image, not by a passing numeric assertion.
- **Cross-font regression tests.** Centering claims are tested by forcing
  several different simulated `row_h` values through `_fit_font` (see the
  "centre correctly across 5 simulated font sizes" test), because a single
  sandbox font can accidentally make broken code look right.
- **Spy on function arguments, not just pixels**, when two similar-looking
  elements could be confused (e.g. `_logo()` call args to check requested
  crest size, rather than pixel-scanning an image where a team banner crest
  and a game crest are both circles of similar size).
- **Never trust a wide str.replace() blind.** This file has been
  accidentally gutted twice by a `.replace()` matching a wider span than
  intended. Always verify occurrence count is exactly 1 before replacing,
  and check line count / `grep -c "^    def "` before and after.

## Deployment

The local working copy lives at `~/Projects/nyc-teams` on this Mac (the
folder itself was never renamed when the plugin was — only the id/class
inside it — since git and the deploy scripts below don't care what the
local folder is called). Deploys to a new Pi-side folder name,
`local-scoreboard`; an old `nyc-teams` folder from before the rename may
still exist there too and is safe to remove once a new deploy is confirmed
working.

```
scp -r ~/Projects/nyc-teams sportspi@ledpi.local:~/LEDMatrix/plugin-repos/local-scoreboard
scp ~/Projects/nyc-teams/install_local_scoreboard.py sportspi@ledpi.local:~/
ssh sportspi@ledpi.local
python3 ~/install_local_scoreboard.py
sudo systemctl restart ledmatrix
```

**The restart matters.** The installer tries to restart the service
automatically, but if that step fails silently, the running process keeps
whatever code was in memory — a diagnostic re-importing the files will show
the fix works while the live panel doesn't, because the live process never
picked up the new files. This has caused at least one full round of
"it's still broken" that turned out to be a stale process, not a bug.

Confirm the restart worked:
```
journalctl -u ledmatrix --since "1 minute ago" --no-pager
```

## Known open items

- No confirmed structured source for individual MLB award odds exists
  (`diagnose_award_odds.py` established this — ESPN's futures endpoint only
  covers team-level markets). Award boards show a computed ranking with no
  value column, not odds.
- Broadcast channel parsing (`_parse_broadcast`) is defensive/best-effort —
  only the *existence* of the `broadcast`/`broadcasts` fields on a real
  ESPN competition object has been confirmed, not their exact shape for
  every sport.
- Season leaderboards and award watch lists are MLB-only — NBA/NFL teams
  show scores and streaks but no stats section, since neither leagues'
  leader data is wired up.
- Countdown icons are guessed from the event's own name by keyword match
  (`_draw_countdown_icon` in `strip_renderer.py`) — a birthday/Christmas/
  school get a specific icon, anything else falls back to a plain star,
  since there is no fixed category list for free-text event names.
