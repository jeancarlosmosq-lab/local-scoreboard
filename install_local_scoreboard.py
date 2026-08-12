#!/usr/bin/env python3
"""
Register the local-scoreboard plugin in LEDMatrix's config.json.

LEDMatrix discovers plugins from config/config.json, not by scanning the
plugins folder, so a hand-copied plugin folder is invisible until it has an
entry here.

Safe to run more than once. Writes a timestamped backup before touching
anything and validates the result before saving, so a failure leaves the
original file untouched.

An existing install under the plugin's old id ("nyc-teams", before it was
renamed for a public release) is migrated automatically: its whole config
entry -- teams, rivals, countdown dates, panel priority, everything --
becomes the starting point for the new "local-scoreboard" entry, and the
old key is removed once copied, since the framework keys a plugin purely by
its manifest's own "id" field and would otherwise leave that old entry
orphaned, matching nothing.

Usage:
    python3 install_local_scoreboard.py
"""

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime

HOME = os.path.expanduser("~")
LEDMATRIX = os.path.join(HOME, "LEDMatrix")
CONFIG = os.path.join(LEDMATRIX, "config", "config.json")
PLUGIN_ID = "local-scoreboard"
OLD_PLUGIN_ID = "nyc-teams"

REQUIRED_FILES = [
    "manifest.json",
    "config_schema.json",
    "manager.py",
    "espn_data_source.py",
    "games_manager.py",
    "game_renderer.py",
    "strip_renderer.py",
    "logo_manager.py",
    "leaders_data_source.py",
    "leaders_manager.py",
    "awards_manager.py",
    "weather_source.py",
    "moon_phase.py",
    "countdowns.py",
]

DEFAULT_ENTRY = {
    "enabled": True,
    "teams": [
        {"abbr": "NYY", "league": "mlb", "name": "Yankees",
         "rivals": ["BOS", "NYM"]},
        {"abbr": "NYM", "league": "mlb", "name": "Mets",
         "rivals": ["ATL", "PHI", "NYY"]},
        {"abbr": "BKN", "league": "nba", "name": "Nets", "rivals": ["NYK"]},
        {"abbr": "NYK", "league": "nba", "name": "Knicks",
         "rivals": ["BOS", "BKN"]},
        {"abbr": "NYG", "league": "nfl", "name": "Giants",
         "rivals": ["DAL", "PHI"]},
    ],
    "game_duration": 12.0,
    "max_display_duration": 60.0,
    "live_interval": 5,
    "idle_interval": 60,
    "upcoming_limit": 5,
    "leaders_per_game": 2,
    "display_duration": 30.0,
    "layout": "strip",
    "static_panel": {
        "enabled": True,
        "priority": ["NYY", "NYM", "NYK", "BKN", "NYG"],
        "width": 64,
        "alternate": 5,
    },
    "other_live_games": {"enabled": True, "limit": 5},
    "countdowns": {"enabled": True, "events": [], "limit": 3},
    "scroll_speed": 22.0,
    "weather": {
        "enabled": True,
        "label": "BAYONNE",
        "latitude": 40.6687,
        "longitude": -74.1143,
        "units": "F",
        "interval": 900,
    },
    "leaderboards": {
        "enabled": True,
        "categories": ["homeRuns", "battingAverage", "earnedRunAverage"],
        "scope": "al_nl",
        "depth": 3,
        "awards": ["mvp", "cy_young", "roy"],
        "cache_duration": 21600,
    },
    "customization": {
        "card": {"show_logos": True, "show_leaders": True},
    },
}

ok_count = 0


def good(msg):
    global ok_count
    ok_count += 1
    print(f"  OK    {msg}")


def bad(msg, fix=""):
    print(f"  FAIL  {msg}")
    if fix:
        print(f"        -> {fix}")


def step(label):
    print(f"\n=== {label} ===")


def main():
    step("1. Locate the plugins directory")
    candidates = [
        os.path.join(LEDMATRIX, "plugin-repos"),
        os.path.join(LEDMATRIX, "plugins"),
    ]
    best = None
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        found = [
            name for name in os.listdir(directory)
            if os.path.exists(os.path.join(directory, name, "manifest.json"))
        ]
        print(f"  {directory}: {len(found)} plugin(s) -> {', '.join(found) or 'none'}")
        if best is None or len(found) > best[1]:
            best = (directory, len(found))

    if not best or best[1] == 0:
        bad("No plugins directory found", f"Is LEDMatrix installed at {LEDMATRIX}?")
        return 1

    plugins_dir = best[0]
    good(f"Using {plugins_dir}")
    plugin_dir = os.path.join(plugins_dir, PLUGIN_ID)

    step("2. Check the plugin files")
    if not os.path.isdir(plugin_dir):
        bad(f"{plugin_dir} does not exist",
            "Copy the local-scoreboard folder there, then run this again.")
        return 1
    good(f"Folder exists: {plugin_dir}")

    missing = [f for f in REQUIRED_FILES
               if not os.path.exists(os.path.join(plugin_dir, f))]
    if missing:
        bad(f"Missing file(s): {', '.join(missing)}",
            "Re-copy the plugin files -- a partial transfer stops it loading.")
        return 1
    good(f"All {len(REQUIRED_FILES)} required files present")

    if os.path.isdir(os.path.join(plugin_dir, PLUGIN_ID)):
        bad(f"Found a nested {PLUGIN_ID}/{PLUGIN_ID} folder",
            f"Run: mv {plugin_dir}/{PLUGIN_ID}/* {plugin_dir}/ && "
            f"rmdir {plugin_dir}/{PLUGIN_ID}")
        return 1

    step("3. Check the Python compiles")
    result = subprocess.run(
        [sys.executable, "-m", "py_compile"]
        + [os.path.join(plugin_dir, f) for f in REQUIRED_FILES if f.endswith(".py")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        bad("A Python file has a syntax error")
        print(result.stderr)
        return 1
    good("All Python files compile")

    step("4. Check the manifest")
    try:
        with open(os.path.join(plugin_dir, "manifest.json")) as f:
            manifest = json.load(f)
    except json.JSONDecodeError as e:
        bad(f"manifest.json is not valid JSON: {e}")
        return 1
    if manifest.get("id") != PLUGIN_ID:
        bad(f"manifest id is {manifest.get('id')!r}, expected {PLUGIN_ID!r}")
        return 1
    good(f"manifest id={manifest['id']} version={manifest.get('version')} "
         f"class={manifest.get('class_name')}")
    good(f"Declares modes: {', '.join(manifest.get('display_modes', []))}")

    step("5. Register and enable in config.json")
    if not os.path.exists(CONFIG):
        bad(f"{CONFIG} not found")
        return 1
    try:
        with open(CONFIG) as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        bad(f"config.json is not valid JSON: {e}",
            "Restore from ~/LEDMatrix/config/backups/")
        return 1

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = f"{CONFIG}.install-backup.{stamp}"
    shutil.copy2(CONFIG, backup)
    good(f"Backed up config to {backup}")

    if PLUGIN_ID not in config and OLD_PLUGIN_ID in config:
        config[PLUGIN_ID] = config.pop(OLD_PLUGIN_ID)
        good(f"Migrated the old {OLD_PLUGIN_ID!r} config entry to "
             f"{PLUGIN_ID!r} -- every setting carried over as-is")

    existing = config.get(PLUGIN_ID)
    if existing is None:
        config[PLUGIN_ID] = DEFAULT_ENTRY
        good("Added a fresh config entry with defaults")
    else:
        # Preserve choices already made; only guarantee the keys that must
        # exist, and force enabled on -- a disabled entry is the commonest
        # reason a plugin is discovered but never loaded.
        #
        # Nested blocks are filled one level deep as well. Doing only the top
        # level meant a settings group that already existed -- "leaderboards",
        # say -- never gained the keys added in a later version, so new
        # options silently kept their old values and the update looked like it
        # had not taken.
        added = []
        for key, value in DEFAULT_ENTRY.items():
            if key not in existing:
                existing[key] = value
                added.append(key)
            elif isinstance(value, dict) and isinstance(existing.get(key), dict):
                for inner_key, inner_value in value.items():
                    if inner_key not in existing[key]:
                        existing[key][inner_key] = inner_value
                        added.append(f"{key}.{inner_key}")

        was = existing.get("enabled")
        existing["enabled"] = True
        good(f"Kept existing settings, enabled: {was} -> True")
        if added:
            good(f"Filled in new settings: {', '.join(added)}")

    payload = json.dumps(config, indent=4)
    json.loads(payload)
    with open(CONFIG, "w") as f:
        f.write(payload + "\n")
    good("Wrote config.json (validated)")

    step("6. Restart the display service")
    restart = subprocess.run(["sudo", "systemctl", "restart", "ledmatrix"],
                             capture_output=True, text=True)
    if restart.returncode != 0:
        bad("Could not restart ledmatrix", restart.stderr.strip())
    else:
        good("Service restarted")

    print("\n" + "=" * 60)
    print(f"{ok_count} checks passed.")
    print("""
Wait about 30 seconds, then confirm it loaded:

  journalctl -u ledmatrix --since "1 minute ago" --no-pager \\
      | grep -iE "Loaded plugin|Local Scoreboard|Refreshed games"

You want 'Loaded plugin: local-scoreboard'.

If it loads but no games appear, check the team abbreviations. Common
variants are aliased (NYK and NY both find the Knicks), but one outside that
table yields an empty board rather than an error:

  journalctl -u ledmatrix --no-pager | grep -i "Refreshed games" | tail -3
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
