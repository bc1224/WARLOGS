# WARDOGS Match Tracker

A free, local tracker for [WARDOGS](https://store.steampowered.com/app/1867240/) on Windows.
It logs your XP and cash for every round, shows a live dashboard for your second monitor
(round profit, pace, scoreboard place, reward feed, what to play next) and keeps a full
history with insights. It reads your screen like OBS does and never touches the game
process, so it's safe from anti-cheat (details below).

Not affiliated with the WARDOGS developers or wardogstracker.gg.

![Live tab mid-round](docs/screenshots/live-in-round.png)

More: [between rounds](docs/screenshots/live-between-rounds.png),
[overview](docs/screenshots/overview.png), [insights](docs/screenshots/insights.png),
[matches](docs/screenshots/matches.png), [one match](docs/screenshots/match-detail.png),
[career](docs/screenshots/career.png), [phone](docs/screenshots/live-phone.png).
All screenshots use made-up data. See it yourself without the game: `py -3 demo.py`.

## Set it up

**Easiest:** [download the ZIP](https://github.com/bc1224/wardogs-match-tracker/archive/refs/heads/main.zip), unzip it anywhere
(for example `Documents\wardogs-tracker`), and double-click **`Setup.bat`**. The wizard:

1. finds Python 3.10+ (or offers to install Python 3.12 with winget),
2. finds your Steam account and in-game name,
3. writes `config.json`, runs a quick self-test,
4. adds the tracker to Windows Startup, starts it, and opens the dashboard at
   http://127.0.0.1:8790.

Then set WARDOGS to **Borderless** or **Windowed** fullscreen and just play.

**With an AI coding agent** (Claude Code, Codex, Cursor and the like): paste this line
into it and it does the rest.

```text
Set up the WARDOGS Match Tracker from https://github.com/bc1224/wardogs-match-tracker on this Windows PC: clone it into my Documents folder, follow its AGENTS.md, ask me for anything it can't detect, and tell me when the dashboard is open.
```

**By hand:** install Python 3.10+, copy `config.example.json` to `config.json` and fill in
your SteamID64 and in-game name, then run `Start Tracker.vbs`. Uninstall with
`powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall` and delete the folder.

## Use it

Just play. The tracker starts with Windows (Startup shortcut "WARDOGS Match Tracker"),
sleeps until WARDOGS is running, and logs each match by itself.

- **Live dashboard**: http://127.0.0.1:8790 (or double-click `WARDOGS Dashboard.url`).
  It updates by itself within about 2 seconds of a new match. Bookmark it or keep it on
  a second monitor.
- **No hotkey needed**: when the results screen sits still for a few seconds, the
  tracker reads it once with Windows' built-in text recognition. If it shows XP and
  results words (victory, placement, rewards, ...), the match is logged and that screen
  is remembered so later ones are caught faster.
- **Ctrl+Alt+E** (or Steam's **F12**) is an optional fallback if a match was missed.
  **Ctrl+Alt+S** marks a match start (optional).
- Numbers in gold are text-recognition guesses. Click to fix one; it turns white and
  counts as checked. **not a match** removes a wrong auto-logged entry and forgets that
  screen.
- **Per match**, the results screen is also searched for the result (victory/defeat),
  winner, score (first to 100), match length and your stats (kills, deaths, revives...).
  Roles and owned items named on loadout or deploy screens during the match are listed
  as "used". Lengths marked * are estimated from when the previous match ended.
- **Best rounds** shows your top match for XP, money, XP per minute, kills and length.
  **Played as** (sessions) is role XP gained between WARDOGS Tracker syncs.
- **Unlocks** come from the WARDOGS Tracker API. Everything you had on the first pull
  is the baseline; anything new or upgraded after that is stamped with when it first
  showed up and counted against the session it came from. The API is checked every 15
  minutes and when the game closes (unlocks are only re-fetched when your sync changed).
- **Overview** also has daily and weekly XP goal bars (type a goal next to each bar),
  day and win streaks, and a "New personal best!" banner after a record match.
- **Insights**: win rate (overall and rolling), stats by role and by item used, where
  your XP and cash came from (rows on the results screen such as "Revives +450 XP"),
  a best-time-to-play heatmap, a fatigue check (XP per hour by hours into a session)
  and a cash breakdown with every match that lost money.
- **Matches** has a search box, filters (won, lost, made money, lost money, unchecked)
  with totals for what's shown, and a note per match (notes are searchable).
- **Career** adds leaderboard rank over time, the players just above and below you with
  the gap to pass the next one, and an estimated level forecast per role. The XP table
  isn't published, so "XP to next" is estimated from your role levels.
- **Strategy**: leveling and cash tips for each role, summarised from community guides
  (linked on the tab), next to your own numbers for that role.
- **Live** tab (opens by default): in a round, a strip of big numbers you can read
  from across the desk (round profit and $/min against your usual, where the round is
  heading at that pace, your scoreboard place, your role, the session total), one
  "do this now" tip with your plan, line charts of profit and XP against your usual pace,
  and a merged reward/kill feed. **More detail** shows the rest: balance, spending,
  Hot Zone/FOB time, K/D, ranked tips, the best money maker for your situation (click
  what your team is doing), a suggested loadout and the best unlock to buy. Between
  rounds and before the game starts it shows your last round, your session so far and
  a plan for the next round (Cash, XP or Both; Infantry or with vehicles). Open the
  in-game **Scoreboard** tab for a few seconds to update your place. XP isn't on the
  HUD, so round XP lands when the round ends.
- **Time range**: Last session (the default; it spans midnight), Hour, Today, Week,
  Month, All. When a range is empty the page says so and offers one that has matches.
- **Matches** tab: a scrollable list of match cards (time, length, result, top role,
  gun, Hot Zone/FOB share, XP and net cash against your average). Click one for its own
  page (`#match/<id>`): XP, cash, earned/spent/bonus and per-minute rates against your
  averages, a timeline of where you were and what you held, round profit over time,
  money made by place and by gun, XP gained per role, loadout, edit/note/delete, and the
  results screen with its text. Matches logged before 2026-10-26 have no timeline.
- **Between rounds**: the money screen (spent / gained / bonus) and the Progression tab
  ("NEXT ROUND IN ...") count as end screens and merge into the same match. Each role
  card's "XP a b" is that role's XP before and after the round; round XP is the sum.
- **In-match reads**: every 10 seconds during a match the screen is read for Hot Zone,
  FOB, team, top-bar cash and the gun nameplate (chat lines are ignored). These build
  the match timeline. The first screen of each kind per match, plus one every 3 minutes,
  is kept in `data/samples/` (small JPEG plus the text read); the newest 150 are kept.
  The same reads catch being downed ("GIVE UP" / "BEING REVIVED BY"), the item open in
  a vendor (a cash drop then counts as buying it) and "PROMOTED TO <role> LVL n" lines.
- **Feed reads**: every 3 seconds (`feed_seconds`) only two small corners are read: the
  reward feed under the top-right cash ("CONTROL ZONE PRESENCE +$200", "PLAYER ASSIST
  38XP", "BRIBE -$500") and the kill feed on the left. Lines naming `player_name`
  (config.json) count as your kills or deaths. Entries count once while they stay up;
  one that scrolls past between reads can be missed.
- **Map**: read from the "<MAP> 45'15.0"N ..." line on the results banner; Insights has
  By map and Fights tables. Game mode isn't read yet (its wording hasn't been seen).
- **Overview** opens with a recap of your latest session and one thing to try next time.
- The header shows WARDOGS server status (checked every 10 minutes).
- `Start Tracker.vbs` starts the tracker by hand (no window). Running it twice is
  harmless; a second copy exits.

## Why it's safe from anti-cheat

The game process is never opened, read, injected or hooked. The tracker only:

1. Reads the Windows process list to see if `WardogsClient-Win64-Shipping.exe` is running
   (same as Task Manager).
2. Copies pixels from the desktop while WARDOGS is the window in front (same as OBS
   Display Capture or Print Screen). It grabs a 64x36 thumbnail every 2 seconds and reads
   a full frame only when the screen has gone still.
3. Reads the game's `PlayerRoleProgress.sav` file to spot role level-ups.
4. Calls the public wardogstracker.gg API every 15 minutes for your synced totals.

Hotkeys use Windows' standard `RegisterHotKey`, not a keyboard hook.

## Where things are

- `data/wardogs.db`: your match log (SQLite). Back this up to keep your history.
- `data/shots/`: end-of-match screenshots.
- `data/tracker.log`: what the tracker did; check here if matches are not being logged.
- `config.json` (made by setup; see `config.example.json`): Steam ID, `player_name`, hotkeys, scan speed, `auto_read`, `still_scans`,
  `read_gap_seconds`, `match_threshold`, `dashboard_port`.

## Troubleshooting

- **"capture was blank"**: the game's display mode hides it from desktop capture. Use
  Borderless/Windowed fullscreen in WARDOGS video settings.
- **Matches not auto-logged**: press Ctrl+Alt+E on the results screen once; check
  `data/tracker.log` for what text was read.
- **False matches**: click **not a match** on the dashboard.
- **Stop the tracker**: end the `pythonw.exe` process running `tracker.pyw` in Task
  Manager. Remove the Startup shortcut to stop autostart.

## Tests

```bash
py -3 -m unittest discover -s tests -t .
```

Totals data: [WARDOGS Tracker](https://wardogstracker.gg) public API.

## License

MIT. Free and open source.
