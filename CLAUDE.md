# CLAUDE.md — local-scoreboard LEDMatrix Plugin

Context for continuing this project in Claude Code. Read this before making
changes — several of these were expensive to learn once already.

## What This Is

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

## The Most Important Thing: Sandbox Fonts ≠ Real Fonts

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

## The Second Most Important Thing: Manifest `update_interval` Gates Everything

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

## The Third Most Important Thing: Composing A Rebuilt Strip Runs In Its Own Process

`strip_renderer.py`'s background rebuild (`_dispatch_background_build`)
originally ran on a background *thread*. That was never actually free: a
thread still shares the main process's GIL, and composing a rebuilt strip
is real CPU-bound work (PIL drawing calls across a couple thousand
pixels of content). No amount of throttling how *often* it ran (tried
5s → 10s → 15s → 8s) fully fixed a small periodic pause in the scroll,
because the underlying cost per rebuild never went away — only how often
it was paid.

Root-caused on the Pi with a direct measurement: a tight spin-loop
"render thread" running concurrently with a background-thread compose
lost ~17% of its throughput during that compose, and the SAME compose
call, run in a genuinely separate OS process instead, brought that down
to ~4% — and the compose itself went from over a second wall-clock
(contending with the real matrix output) to under 150ms. `manager.py`
now constructs the real `StripRenderer` with `use_process=True`, which
routes composition to a persistent worker process (`_compose_worker_main`
in `strip_renderer.py`) over a `multiprocessing.Pipe`, kept alive for the
life of the service rather than spawned per rebuild.

Three gotchas hit building this, all now handled but worth knowing if
this code needs touching again:

- **`Pipe`, not `Queue`.** `Queue.put()` defers pickling to its own
  internal feeder thread — a request that fails to pickle (an unpicklable
  object accidentally in the weather dict, say) is silently lost there,
  and the response side then waits forever for a reply that was never
  actually sent. `Connection.send()` (Pipe) pickles synchronously in the
  calling thread, so a bad request raises right where it's sent and gets
  caught the normal way. The response wait is also bounded (`conn.poll(20.0)`
  before `conn.recv()`), so a worker that dies or hangs is eventually
  reported as failed rather than blocking forever either way.
- **`spawn`, not `fork`.** Linux defaults to `fork` for multiprocessing,
  which inherits the parent's memory (and any already-open C-extension
  state) directly. Forking a process that had already loaded PIL/FreeType
  font resources left the *parent* process's own font loading broken
  afterward — `OSError: cannot open resource` on a later, completely
  unrelated `ImageFont.load_default()` call, in the parent, not the
  child. `_ensure_compose_process` uses
  `multiprocessing.get_context("spawn")` explicitly, which starts a
  genuinely fresh interpreter with none of that inherited state, at the
  one-time cost of a slower process start (paid once — the worker stays
  alive for the service's whole session).
- **Font objects can't cross the process boundary.** `_draw_clock`'s
  `clock_state` includes a live `PIL.ImageFont.FreeTypeFont` object, used
  by `refresh_clock()` to repaint the clock in place every frame.
  Pickling that font in the worker and unpickling it in the parent raised
  the same "cannot open resource" error, since `FreeTypeFont.__setstate__`
  tries to reopen the font file in a context it wasn't loaded from. The
  worker strips the font out of `clock_state` before sending (keeping
  just the box and the clock text, both plain data); the parent resolves
  an equivalent font itself via its own `_largest_fit` call, which is
  deterministic given the same panel height, so it always picks the same
  font file, just loaded fresh in its own process.

`use_process` defaults to `False` — every `StripRenderer` in
`test_offline.py` uses the original thread path unchanged, since spawning
a real OS process per test instance would make the suite far slower for
no benefit (nothing there is racing real matrix output for CPU). Only
`manager.py`'s actual on-device instance opts in.

## Text Positioning: Always Use `_text_top` / `_text_bottom` / `_vblock_start`

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

## Testing Discipline

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
- **Clear `__pycache__` on the Pi before trusting a Pi-side test run.**
  `rsync` deploys never delete stale `.pyc` files (nothing excludes or
  cleans `__pycache__` on the receiving end), and Python will silently
  reuse a cached bytecode file instead of recompiling from the newly
  deployed source if the cache looks current to it. This produced one
  confirmed false positive: a `_draw_leaderboard` centering test came
  back PASS against a Pi run, which turned out to be stale bytecode from
  an earlier, already-superseded iteration of the fix, not the actual
  deployed source — rerunning after `find . -name __pycache__ -exec rm
  -rf {} +` reproduced the real, still-broken result immediately and
  deterministically. Treat a Pi-side PASS as suspect, not just an
  on-device sandbox render, whenever the source file it exercises
  changed since the last time that Pi's `__pycache__` was touched.
- **A `textbbox()`-based ink-height estimate is still just an estimate,
  not what actually gets lit.** `_draw_leaderboard`'s vertical centring
  first tried `row_h` for every row, then `row_h` for inner rows plus a
  sample glyph's own `textbbox` height for just the last row (to drop
  the trailing leading line-following rows don't need) -- both were
  provably wrong on the Pi, off by a real, visible pixel in the second
  case, because `textbbox` reports a string's *declared* bounding box,
  and the actual glyphs a leaderboard draws (digits, periods, no
  descenders) don't always light every row that box implies. The fix
  that finally held: render the block once onto a scratch canvas,
  measure the real min/max **lit pixels**, and only then compute the
  vertical offset needed to centre that measured extent -- no font
  metric involved at all, just what the font actually put on screen.
  Slower (one extra render per leaderboard segment) but exact regardless
  of which BDF file loads. The general lesson: for anything pixel-exact,
  prefer rendering and measuring over any font-metric formula, however
  many times the formula has already been refined -- this one needed
  three attempts before an approach stopped being wrong in some new way.

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

## Known Open Items

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

## Real BDF Fonts Can't Encode Arbitrary Unicode

A second, distinct flavor of the sandbox-fonts-≠-real-fonts gap: it's not
just metrics (leading, row height) that differ, real BDF fonts also have a
much narrower character set than whatever font the sandbox falls back to.
A football possession marker drawn with the Unicode "●" (U+25CF) rendered
fine in every sandbox check and crashed on the Pi with
`UnicodeEncodeError: 'latin-1' codec can't encode character '●'` —
only surfaced by running `test_offline.py` on the Pi directly, not by
anything in the local suite. `_safe()` (NFKD-normalize + ASCII-encode,
ignoring what doesn't fit) exists for exactly this, but it fails silently
in two different ways depending on how it's used: skip it entirely and an
unencodable character crashes the render; call it but then measure width
with the original unsanitized string and the crash just moves to the
measurement call instead of the draw call. Both happened here — draw was
correctly wrapped in `_safe()`, but the width `_measure()` call right
after it used the raw string. The actual fix was to stop feeding a
Unicode symbol through `_safe()` at all and draw plain ASCII ("*" instead
of "●") — sanitizing a symbol that can't survive ASCII conversion just
means it silently vanishes from the display, not that it renders
correctly by another means. Same lesson as the font-metrics gap: a
sandbox-only render is not sufficient evidence for anything involving the
real BDF fonts, character set included, not just row height.
