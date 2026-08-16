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

A related framework mechanic worth knowing alongside this one: the host
holds a per-plugin lock for the *entire duration* of an `update()` call,
and skips that plugin's `display()` entirely while it's held — the panel
just shows its last pushed frame rather than a new one
(`display_controller.py`'s `_display_lock_or_skip`, a non-blocking
try-lock). That's invisible as long as `update()` is fast, but `update()`
doing real, synchronous network I/O turns straight into a visible freeze
for however long the fetch takes, every time it runs. Confirmed on a busy
live-game day: `games.refresh()` alone took ~1s per call, every single
`live_interval` (5s) — the panel was frozen roughly a fifth of every five
seconds. See "Backgrounding `update()`'s Data Fetch" below for the fix;
the lesson that generalizes is the same shape as `update_interval` above —
a framework-level scheduling/locking behavior that no amount of correct
plugin-side logic can compensate for, only work around by not giving the
framework a slow call to lock around in the first place.

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

Four gotchas hit building this, all now handled but worth knowing if
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
- **The host framework's own plugin isolation breaks pickling a top-level
  function by reference, silently, until a background rebuild actually
  runs.** LEDMatrix's plugin loader renames this module's `sys.modules`
  entry from the bare `strip_renderer` to a namespaced key
  (`_plg_local-scoreboard_strip_renderer`) immediately after loading it —
  correct on the framework's part, so two different plugins that both
  ship a same-named file don't collide — but `multiprocessing`'s `spawn`
  start method pickles a `Process(target=...)` by `(module_name,
  qualname)` and verifies that against `sys.modules[module_name]` at
  pickle time, which fails once the bare key is gone:
  `PicklingError: Can't pickle <function _compose_worker_main ...>: it's
  not the same object as strip_renderer._compose_worker_main`. This
  shipped undetected for a full version, because confirming a clean
  deploy only ever checked `journalctl` for a "Loaded plugin" line
  moments after restart — the pickling only happens on the *first
  background dispatch*, which can be a full `update_interval` or more
  later. Checking a clean load is not the same as checking the
  background path actually ran; watch `journalctl` for at least a
  couple of minutes past the load line, not just the load line itself.
  Fixed in `_ensure_compose_process`: a module-level `_THIS_MODULE =
  sys.modules[__name__]`, captured at import time before the framework
  renames the entry, gets briefly restored under the bare name around
  the `Process.start()` call (which pickles synchronously, in the
  calling thread, before it returns) and removed again right after, so
  the framework's own isolation still holds the rest of the time. An
  old, pre-rename `nyc-teams` plugin folder with its own stale copy of
  this file was also found still on the Pi during this investigation —
  not the actual cause of this specific bug, but a real `sys.path`
  collision risk in its own right, and removed per the note in
  Deployment below that doing so is safe once a new deploy is confirmed
  working.

`use_process` defaults to `False` — every `StripRenderer` in
`test_offline.py` uses the original thread path unchanged, since spawning
a real OS process per test instance would make the suite far slower for
no benefit (nothing there is racing real matrix output for CPU). Only
`manager.py`'s actual on-device instance opts in.

## Backgrounding `update()`'s Data Fetch

A second, distinct performance fix from the strip-composing one above,
found the same way: a user report of the panel visibly freezing, then
root-caused on the Pi rather than guessed at. Different mechanism, so it
needed a different fix.

`manager.py`'s `update()` used to fetch everything — followed teams'
games, other-live games league-wide, their leaders, streaks, season
leaders, weather — inline, synchronously. That is genuine network I/O.
The host framework holds a per-plugin lock for the whole duration of a
plugin's `update()` call and skips that plugin's `display()` entirely
while it's held (see the note under "Manifest `update_interval` Gates
Everything" above) — so a slow `update()` is a frozen panel, not just
stale data. Confirmed directly against the Pi on a day with several live
games at once: `games.refresh()` alone took ~1 second, every single
`live_interval` (5s) — the panel was frozen for roughly a fifth of every
five seconds, worse the more games were live.

`update()` now dispatches its whole body of work to a background thread
(`_dispatch_background_update`) and returns in under 1ms regardless of
how long the fetch actually takes — confirmed by direct measurement
against the same live workload, before and after. A few things worth
knowing if this needs touching again:

- **A thread is enough here — not a separate process.** This looks like
  the same problem `use_process` above solved, but it isn't: that fix
  was working around GIL contention from real CPU-bound drawing work
  competing with the render thread. A network call *releases* the GIL
  while it's waiting on the socket, so a background thread costs the
  render loop nothing here. Reaching for multiprocessing again for this
  would add all of `_compose_worker_main`'s complexity (spawn vs fork,
  Pipe vs Queue, objects that can't cross the process boundary) for a
  problem threading already solves cleanly.
- **Single-flight, guarded by `_refresh_lock`/`_refresh_in_flight`.**
  `update()`'s own gate (`idle_interval`/`live_interval`) already limits
  how often a dispatch is attempted, but a slow network day could still
  have one dispatch still running when the next would-be one arrives.
  Starting a second, overlapping fetch would only add more concurrent
  network load, not fix anything — the guard makes a second `update()`
  call during an in-flight refresh a harmless no-op instead.
- **`GamesManager.refresh()` itself stays fully synchronous, deliberately
  unchanged.** Every test in `test_offline.py` that calls `games.refresh()`
  directly depends on it being finished by the time the call returns —
  making it asynchronous by default would have broken that contract for
  everything that already worked. The background dispatch wraps the call
  from `manager.py`'s side instead, the same relationship
  `_dispatch_background_build` has to `_compose_strip` in
  `strip_renderer.py` — the callee stays simple and synchronous, the
  caller decides whether to background it.
- **Tests that call `update()` and immediately check what it did now need
  `_wait_for_background_update()` first** (mirroring
  `StripRenderer._wait_for_background_build`), or they race the
  background thread and become flaky. A dedicated test also forces a
  slow fake `refresh()` (via a small delay and `threading.Event`s) to
  prove `update()` itself returns near-instantly regardless of how long
  the underlying fetch takes, and that a second `update()` mid-fetch
  doesn't dispatch an overlapping one.

## Text Positioning: Always Use `_text_top` / `_text_bottom` / `_vblock_start`

Never call `draw.text((x, N), ...)` with a bare row number. This font
family's leading means a glyph drawn "at row 1" doesn't put ink at row 1.
Use:

- `_text_top(draw, font, target_row, sample="0")` — returns the y to pass to
  `draw.text` so ink starts at `target_row`.
- `_text_bottom(draw, font, target_bottom_row, sample="0")` — same, for ink
  that should *end* at a given row.
- **Pass `sample=` as the actual text being positioned whenever that text
  might not be all-caps or numeric.** Both helpers compensate for this
  font's leading by measuring a stand-in glyph's own bounding box — `sample`
  defaults to a digit because nearly everything on this strip used to be
  all-caps or numeric, and a digit's ink-top lines up with a cap letter's
  in this font. That default silently stops being safe the moment the text
  being positioned has a lowercase ascender or dot: it can sit a pixel
  above where a digit's own ink starts, which is invisible everywhere
  except right at a block's own top edge. Confirmed the hard way: switching
  status labels from `"FINAL"` to `"Final"` shipped a real, reproducible
  1px top-margin violation, caught immediately by the existing "final game:
  margins top/bottom" test, isolated by reverting just that one string back
  to see the test pass again. Every other call site that positions literal,
  code-authored text (section titles, status labels, forecast headers) now
  passes its own text as `sample`; call sites positioning data-driven
  content (player names, team abbreviations) were left on the digit
  default, since auditing every data-driven string was out of scope for
  the change that found this.
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
