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
- Roles and items named on loadout or deploy screens are listed as "used".
- Map comes from the coordinates line on the results banner. Game mode isn't read yet.

## Reads during a match

- **Every 10 seconds**: Hot Zone, FOB, team, top-bar cash and the gun nameplate (chat
  is ignored). These build the match timeline. They also catch being downed, the item
  open in a vendor (a cash drop then counts as buying it) and "PROMOTED TO ... LVL n".
  The first screen of each kind, plus one every 3 minutes, is kept in `data/samples/`
  (newest 150).
- **Every 3 seconds** (`feed_seconds`): only two small corners, the reward feed under
  the cash ("CONTROL ZONE PRESENCE +$200") and the kill feed. Lines naming your
  `player_name` count as your kills or deaths. An entry that scrolls past between reads
  can be missed.
- XP isn't on the HUD, so round XP lands when the round ends. Open the in-game
  Scoreboard tab for a few seconds to update your place.

## Dashboard tabs

| Tab | What's on it |
|---|---|
| **Live** | In a round: profit and $/min vs your usual, pace, scoreboard place, one "do this now" tip, charts, reward/kill feed. **More detail** adds balance, spending, Hot Zone/FOB time, K/D, best money maker, a suggested loadout and the best unlock to buy. Between rounds: last round, session so far and a plan for the next one. |
| **Overview** | Session recap, daily and weekly XP goals, streaks, best rounds, personal-best banner. |
| **Insights** | Win rate, stats by role, gun and map, where XP and cash came from, best time to play, fatigue check, rounds that lost money. |
| **Matches** | Searchable, filterable match list with notes. Click a match for its own page: earned vs spent, timeline, profit over time, money by place and gun, XP per role, and the results screen. |
| **Career** | Leaderboard rank over time, the players around you, estimated level forecast per role. |
| **Strategy** | Leveling and cash tips per role from community guides, next to your own numbers. |

- **Time range**: Last session (default, spans midnight), Hour, Today, Week, Month, All.
- Numbers in gold are text-recognition guesses. Click one to fix it. **not a match**
  removes a wrong entry.
- The header shows WARDOGS server status (checked every 10 minutes).

## Unlocks and totals

Career totals and unlocks come from the [wardogstracker.gg](https://wardogstracker.gg)
public API, checked every 15 minutes and when the game closes. Everything you had on the
first pull is the baseline; anything new after that is credited to the session it came
from. The XP table isn't published, so "XP to next" is an estimate.

## Files

- `data/wardogs.db`: your match log (SQLite). Back this up to keep your history.
- `data/shots/`: end-of-match screenshots.
- `data/tracker.log`: what the tracker did.
- `config.json`: Steam ID, `player_name`, hotkeys, scan speed, `auto_read`,
  `still_scans`, `read_gap_seconds`, `match_threshold`, `dashboard_port`
  (see `config.example.json`).
