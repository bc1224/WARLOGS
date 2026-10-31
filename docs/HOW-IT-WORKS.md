# How it works

The details behind the tracker. For setup, see the [README](../README.md).

## Logging matches

- When the results screen sits still for a few seconds, the tracker reads it once with
  Windows' built-in text recognition. If it shows XP and results words (victory,
  placement, rewards...), the match is logged and that screen is remembered so later
  ones are caught faster.
- **Ctrl+Alt+E** (or Steam's **F12**) logs a match by hand if one was missed.
  **Ctrl+Alt+S** marks a match start. Both are optional.
- The results screen is also searched for result, winner, score, match length and your
  stats (kills, deaths, revives...). Lengths marked `*` are estimated from when the
  previous match ended.
- Between rounds, the money screen (spent / gained / bonus) and the Progression tab
  ("NEXT ROUND IN ...") merge into the same match. A role card's "XP a b" is that
  role's XP before and after the round.
- Roles and items named on loadout or deploy screens are listed as "used". Loadout screens
  list every role, so role stats count a match only toward the role that gained the most XP
  on its Progression tab, and gun stats use the gun seen in your hands most during the round.
- Map comes from the coordinates line on the results banner. Game mode isn't read yet.

## Reads during a match

- **Every 10 seconds**: Hot Zone, FOB, team, top-bar cash and the gun nameplate (chat
  is ignored). These build the match timeline. They also catch being downed, the item
  open in a vendor (a cash drop then counts as buying it) and "PROMOTED TO ... LVL n".
  The first screen of each kind, plus one every 3 minutes, is kept in `data/samples/`
  (newest 150).
- **Every 1.5 seconds** (`feed_seconds`): only two small corners, the reward feed under
  the cash ("CONTROL ZONE PRESENCE +$200") and the kill feed. Lines naming your
  `player_name` count as your kills or deaths, without the kill distance ("1220m").
  Smeared reward names snap to the reward they look like; a teammate's name with a few
  dollars is a tip, and anything else unreadable counts as Other. An entry that scrolls
  past between reads can be missed.
- **Your gun** comes from the nameplate bottom right. Common misreads ("MPS", "MSOO",
  "GAL1L") snap to the gun, and gear in your hands (range finder, defibrillator, medical
  bag) isn't counted as a gun. Older matches are cleaned up the same way once.
- **Every 3 seconds** (`track_seconds`, 0 turns it off): a small minimap picture
  (171 x 153, about 6 KB) for the replay map. Nothing is worked out while you play.
- **While the big map is open** (on a screen read anyway): its picture, plus where its
  edges are in map coordinates, read off the grid labels. At most one every 8 seconds.
- **On a slower PC** these reads take longer, so they are spaced out, up to twice as far
  apart, to keep the tracker within a tenth of one CPU core (`load_budget`). The scan
  that catches the results screens keeps its pace. The tracker log says when this changes.
  Turn **Go easy on a slower PC** off under More to always read at the full pace (fewer
  missed kills and rewards); it stays off until you turn it back on, and the menu says
  whether this PC would need it.
- XP isn't on the HUD, so round XP lands when the round ends. Open the in-game
  Scoreboard tab for a few seconds to update your place.

## Dashboard tabs

Each tab opens with a short summary. The rest sits under a row of sub-tabs below it, one
section at a time, and each tab remembers the one you picked. Click a table's column
heading to sort by it (again for the other way).

| Tab | Up front | Sub-tabs |
|---|---|---|
| **Live** | In a round: profit and $/min vs your usual, pace, scoreboard place, one "do this now" tip, charts, reward/kill feed. Between rounds: last round and session so far. | None; **More detail** (balance, spending, Hot Zone/FOB time, K/D, best money maker, loadout, best unlock to buy) and the **Round plan** expand in place. |
| **Overview** | Summary figures, session recap led by net cash, best rounds (click one to open it). | XP and cash per match, goals and streaks, sessions. |
| **Matches** | Searchable, filterable match list with notes. Click a match for its own page: earned vs spent, timeline, profit over time, money by place and gun, XP per role, and the results screen. Set the result and your role by hand when the screens weren't read. **Where you went**: a map of your route with kills, deaths, downs and purchases on it; drag the slider or press play. | |
| **Insights** | Win rate and averages, what each role pays (highest or lowest first), stats by gun. | Win rate over time, where XP and cash came from, zone/team/map/fights, when you play best, tips per role from community guides. |
| **Career** | Players around you on the leaderboard, level forecast per role. | Career XP, cash and rank over time, unlocks, synced totals. |

- **Time range**: Last session (default, spans midnight), Hour, Today, Week, Month, All.
- Numbers in gold are text-recognition guesses. Click one to fix it. **not a match**
  removes a wrong entry.
- The header shows WARDOGS server status (checked every 10 minutes).

## Replay map

The first time you open a match, the dashboard matches each minimap picture against the
big map pictures you've taken (a few seconds, in your browser) and saves the route with
the match. The route only covers parts of the map you've opened at least once, and it's
approximate. Open the big map for a moment now and then to fill in more of it.

## Unlocks and totals

Career totals and unlocks come from the [wardogstracker.gg](https://wardogstracker.gg)
public API, checked every 15 minutes and when the game closes. Everything you had on the
first pull is the baseline; anything new after that is credited to the session it came
from. The XP table isn't published, so "XP to next" is an estimate.

## Files

- `data/wardogs.db`: your match log (SQLite). Back this up to keep your history.
- `data/shots/`: end-of-match screenshots for your last 50 matches (`shots_matches` in `config.json`); older ones are deleted after each match.
- `data/track/`: minimap pictures for the last `track_rounds` rounds (15) and up to
  `track_maps` big map pictures (80). A worked-out route stays with its match.
- `data/tracker.log`: what the tracker did.
- `config.json`: Steam ID, `player_name`, hotkeys, scan speed, `auto_read`,
  `still_scans`, `read_gap_seconds`, `match_threshold`, `dashboard_port`,
  `track_seconds`, `track_rounds`, `track_maps`
  (see `config.example.json`).
