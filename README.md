# Local Scoreboard

One board for the teams you follow, across MLB, NBA, NFL and La Liga: live scores, final results, upcoming games, team logos, and the notable performer from each game — plus local weather, a clock, moon phase and your own personal countdowns, all on the same continuous scroll.

Every one of those is configuration, not code: your teams, your city's weather coordinates, your own dates to count down to. The default example roster is Yankees, Mets, Nets, Giants and Knicks with Bayonne, NJ weather, but none of that is required — point it at your own teams and your own city and it runs exactly the same way.

## One board, one scroll

Scores, performances, season leaderboards, weather and countdowns all live on the same strip. That was the reason to merge scores and stats into one plugin in the first place: with two plugins the display hands between them, so a leaderboard and a score can never be part of one continuous scroll.

This replaces both `baseball-scoreboard` and `baseball-stats` — disable them once this is running, or the same information appears twice in a rotation.

## The team strip

The default layout. Every team lives on **one continuous strip** that scrolls past the panel and wraps, so the board never stops or blanks between teams.

```
[LOGO] YANKEES W3 │ FINAL      │ HR                     │ MON 7:05
                  │ (l) BOS 4  │ A.JUDGE 2-4, HR, 3 RBI │ (l) NYY 70-46
                  │ (l) NYY 7  │                        │ (l) TB  55-61
```

Each team shows a full-height logo, name and current win/loss streak, then **any live game**, **the single most recent final**, and **the next fixture** — the crest on finals and fixtures sized close to the banner's own logo, with the score beside it rather than stacked underneath — not a week of results and a fortnight of schedule. Fixtures carry weekday, date and time together — `TDY 8/9 7:05`, `MON 8/11 7:05` — because a weekday alone repeats every seven days and a bare time is ambiguous, plus the broadcast channel(s) when ESPN publishes them. Both sides of a game carry their own logo, since the opponent is half the result. The standout performance sits beside the final it belongs to, showing the player's full name instead of an abbreviation whenever the stat line is already at least as wide. A game against a configured rival is flagged and recoloured, live or final or upcoming alike.

This is the point of the layout: cycling card by card across five teams gives you a Yankees score, a Knicks score and a Giants fixture in succession, and never builds a picture of any of them.

Games elsewhere in the league that aren't a followed team's own are interleaved one at a time, right after each followed team, rather than bunched into a single block at the tail of the strip -- on a long roster, waiting for that block could mean most of a lap before a score outside your own teams came back around. Any left over once the followed teams run out still close out the strip together, the same as before.

Status is colour-coded — green in progress, amber upcoming, grey final, orange for a rivalry game — and on a finished game the losing side dims, so the result reads without comparing two numbers.

## Weather, moon phase and countdowns

Ahead of the teams: current conditions and an hourly and 4-day forecast from the US National Weather Service (free, keyless), a clock, the moon's current phase, and days-until for any personal dates you configure — a birthday, a holiday, first day of school. Each countdown gets an icon guessed from its own name (a cake for a birthday, a tree for Christmas, a pencil for school, a star otherwise), and reads "TODAY!" on the day itself.

A live game — any live game, not just a followed team's — hides the season leaderboards, award watch lists and countdowns entirely, since a live score is what the board exists to show right now and everything else competing for the same scroll only pushes it further away. They come back automatically the moment nothing is live; the underlying data keeps refreshing in the background the whole time, so there's no delay when they reappear. This takes effect immediately, not on the strip's next full lap -- a live game starting or ending forces the newly rebuilt strip in right away rather than waiting for the scroll to finish its current pass, which on a long strip could otherwise be minutes away.

Current conditions and the 4-day forecast stay up regardless of a live game -- the same reasoning that leads the whole strip with weather in the first place, a warning is more urgent than a live score, and a short outlook is brief enough not to compete for the same space. The moon phase and hourly forecast column follow the same hiding rule as leaderboards, but only if `weather.hide_forecast_when_live` is turned on; it defaults to off, so installs that don't set it keep the original always-shown behaviour.

The hourly forecast has its own separate cutoff on top of that, always on: shown 6am-8pm, hidden overnight. An hour-by-hour forecast is for deciding what to do with the rest of today; by 8pm it's mostly covering hours you'll be asleep for. The 4-day forecast always stays up regardless of the time of day; the moon phase does not, unless weather.hide_forecast_when_live is off.

Both the hourly and 4-day columns centre under their own section header rather than always starting flush with its left edge -- a header wider than the day/hour window that actually came back otherwise left the columns looking pinned to one side of their own label.

Current conditions (the icon and plain temperature) hide from the scroll whenever the static panel is already showing that same reading -- nothing live, so the clock/weather fallback has that slot -- the same duplicate-avoidance as the scroll's own clock. Feels-like isn't shown on the static panel at all, so it stays up in the scroll regardless, using the icon and single-line treatment the temperature would otherwise get rather than sitting paired under a hidden number.

When nothing is live, the leftmost module shows this same clock-and-weather block on its own, pinned in place while the rest of the strip scrolls past — the same slot a live game takes over automatically the moment there is one. The scroll carries its own copy of the clock only while that slot has been taken over by a live game -- otherwise the static panel is already showing the time, and a second copy scrolling past would just be the same clock twice.

## Continuous scrolling

The strip scrolls without stopping and wraps at its end, at the framework's own high-FPS loop so the motion stays smooth. Rebuilding the strip -- tens of milliseconds, more on a Pi -- runs on a background thread rather than blocking a frame, and a rebuilt strip waits for the scroll's seam before swapping in, so an update is only ever adopted while it is off-screen — replacing the image mid-pass would shift every segment after the changed one sideways.

Teams playing now lead the strip. A live score is the one thing here that will not keep, and leaving it in configured order meant waiting most of a pass to reach it.

## Live game detail

Each sport draws what a fan of that sport actually watches for:

| Sport | Shown |
|---|---|
| Baseball | Diamond with the runners on, the count, the outs |
| Football | Possession, down and distance, field position |
| Basketball | Period and clock |
| Soccer | Match clock only |

Basketball and soccer get no segment of their own because nothing beyond the clock changes fast enough to earn the space — the status line already carries it.

A followed team's own live game can also pin to the leftmost module, held in place while everything else scrolls past — so a game you actually care about is never scrolled away mid-at-bat.

## Display modes

`local_scoreboard` in the strip layout — the other three (`local_live`, `local_recent`, `local_upcoming`) are declared so the layout can be switched without reinstalling, but they decline while the strip is active, so the board takes one rotation slot rather than four. Setting **Layout** to `cards` gives the earlier one-game-at-a-time behaviour instead.

A live game for a followed team reports live priority, so the core can interrupt the rotation for it.

## Data

ESPN's public scoreboard and summary endpoints for scores, and the US National Weather Service for weather -- both free, no API key. Moon phase and countdowns are plain date arithmetic, no network involved.

The same ESPN endpoint shape serves baseball, basketball and football, which is why three leagues need only one fetcher.

Refresh cadence follows the games: **~5 seconds** while any game is live -- a followed team's own, or one elsewhere in the league -- or a followed team's game is about to start, **~60 seconds** otherwise (both configurable). A final from last night doesn't change; a game in progress changes every pitch or possession. Finals have their notable players fetched once and remembered, since a completed box score is settled. Win/loss streaks come from ESPN's own league standings, refreshed every 5 minutes.

That 5-second data refresh reaches the panel quickly too: a live game's own score, count or batter changing is adopted onto the strip as soon as the next rebuild is ready, rather than waiting for the current scroll pass to finish -- on a long strip a full pass can take minutes, which is far too slow for an at-bat. The rebuild itself is capped a little slower than the data (every ~10 seconds, not 5) on purpose: composing a new image is real CPU work, and running it as often as the data itself refreshes measurably competed with the matrix output for CPU on a Pi, confirmed as a small periodic pause in the scroll once any live game elsewhere kept the fast data cadence on for long stretches of the day. Data is never stale by more than one fetch either way -- only how often a fetched change actually becomes a new image is capped.

## Configuration

Teams are `{abbr, league, name, rivals}` entries; leagues are `mlb`, `nba`, `nfl`, `laliga`. `rivals` is a list of opponent abbreviations that flag a game as a rivalry on the strip. `laliga` is Spain's top flight only, not every competition a club plays -- ESPN organises soccer one competition at a time rather than one feed per club, and a club that also plays a cup or continental competition would need a second team entry once that competition's own league key is added.

Common alternate spellings are matched automatically — `NYK` and `NY` both find the Knicks, `AZ` and `ARI` both find the Diamondbacks. This matters because a wrong abbreviation is silent: it yields an empty board, which looks exactly like the team not playing.

Weather takes a place name, latitude and longitude, and units (`F` or `C`) — point it at your own city. Countdowns take a list of `{name, month, day}` entries, each recurring every year.

Other settings worth knowing: **Seconds Per Game** (12), **Max Seconds Per Visit** (60, after which the plugin hands the panel back and resumes at the next game on its following turn), **Upcoming Games To Show** (5), **Scroll Speed** (pixels/second), and toggles for logos, leaderboards, other-live games, weather, countdowns and the notable-player line.

## Panel sizes

Layout is derived from the panel, not hardcoded. Tested at 192×32, 128×32, 64×32 and 128×64. On a narrow panel the notable-player line and team records are dropped, since neither can say anything useful in that width.

Conventions carried over from the `baseball-stats` leaderboard, all of which were arrived at the hard way on real hardware: one font per card, text inset from the module edge, spare height shared rather than dumped at the bottom, names folded to ASCII, and BDF fonts compiled rather than loaded as if they were PIL fonts.

## Testing

```bash
python3 test_offline.py
```

Runs the whole pipeline against canned ESPN payloads with no network and no panel, and writes a PNG of every card.

## Installation

```bash
scp -r local-scoreboard sportspi@<your-pi>.local:~/LEDMatrix/plugin-repos/
scp install_local_scoreboard.py sportspi@<your-pi>.local:~
ssh sportspi@<your-pi>.local
python3 ~/install_local_scoreboard.py
```

Safe to run more than once, and migrates an existing install from the plugin's old id (`nyc-teams`) automatically, carrying every setting over.

## Known limitations

- **Team abbreviations are ESPN's.** Common variants are aliased, but an abbreviation outside that table and outside ESPN's own spelling silently yields an empty board. If a team never appears, that's the first thing to check.
- **Notable players depend on ESPN publishing leaders** for that game. Some games have none, particularly early in progress. `diagnose_leaders.py` reports what ESPN is actually returning per game, which separates a data gap from a rendering fault.
- **Four leagues.** NHL and college would each need their abbreviations and a sport path, which is a small addition. Soccer support (`laliga`) is one league only -- other competitions and leagues follow the same pattern once needed.
- **No notable-performer line for soccer.** ESPN carries baseball/basketball/football leaders on the scoreboard competition itself; soccer instead carries a play-by-play (goals, cards) under a different field this plugin doesn't read yet, so a soccer game's standout is always blank rather than sometimes blank like the other three leagues.
- **Season leaderboards and award watch lists are MLB-only** today.

## Version

0.47.1
