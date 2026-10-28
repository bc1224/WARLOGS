# WARDOGS Match Tracker

Free, local XP and cash tracker for [WARDOGS](https://store.steampowered.com/app/1867240/) on Windows.

- **Logs every round automatically**: XP, cash, win/loss, role and gun
- **Live view** for your second monitor: round profit, $/min vs your usual, scoreboard place
- **History** that shows which roles and guns actually pay off for you

It reads the screen like OBS does and never changes the game or its files. Everything stays on your PC.

![Live view mid-round](docs/screenshots/live-in-round.png)

| Between rounds | Matches | Insights |
|---|---|---|
| ![](docs/screenshots/live-between-rounds.png) | ![](docs/screenshots/matches.png) | ![](docs/screenshots/insights.png) |

<sub>Screenshots are from demo mode, which fills in sample matches so you can see every screen before you've played. Try it yourself: `py -3 demo.py`.</sub>

## Setup

1. [Download the ZIP](https://github.com/bc1224/wardogs-match-tracker/archive/refs/heads/main.zip) and unzip it anywhere.
2. Double-click **`Setup.bat`**. It finds Python (or installs it), your Steam account and
   name, then starts the tracker and opens the dashboard.
3. Set WARDOGS to **Borderless** or **Windowed** fullscreen and play.

The dashboard lives at http://127.0.0.1:8790 and the tracker starts with Windows.

**Updates:** when a new version is out, the dashboard shows an Update button. It downloads
the new files from this repo, checks each one against GitHub, keeps your settings and match
log, and restarts the tracker. Nothing updates unless you click it. A git clone updates with
`git pull` instead.

<details>
<summary>Using Claude Code, Codex or another AI agent? Paste this instead.</summary>

```text
Set up the WARDOGS Match Tracker from https://github.com/bc1224/wardogs-match-tracker on this Windows PC: clone it into my Documents folder, follow its AGENTS.md, ask me for anything it can't detect, and tell me when the dashboard is open.
```

</details>

<details>
<summary>Manual setup and uninstall</summary>

Install Python 3.10+, copy `config.example.json` to `config.json`, fill in your SteamID64
and in-game name, then run `Start Tracker.vbs`.

To uninstall: `powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall`, then delete the folder.

</details>

## Is it safe?

The game process is never opened, read, injected or hooked. The tracker only:

- checks the process list to see if WARDOGS is running (like Task Manager)
- copies pixels from the screen while WARDOGS is in front (like OBS)
- reads the game's `PlayerRoleProgress.sav` to spot role level-ups
- calls the public [wardogstracker.gg](https://wardogstracker.gg) API for your totals
- asks GitHub every few hours whether a newer version is out (turn off with
  `"check_updates": false` in `config.json`)
- sends a report only when you click **Report an issue** and press Send: your text, the
  screenshot you added and the app version go to a small relay (`relay/`) that files them as a
  public GitHub issue. Nothing else from your PC is sent

Untick **Online** at the top of the dashboard and it never contacts GitHub, wardogstracker.gg or
the report relay; it keeps reading your screen and logging matches. Tick it again to reconnect.

The dashboard only answers on your own PC (127.0.0.1), refuses requests from other websites,
and only runs its own scripts.

It's light: about 150 MB of RAM, no GPU. Use at your own risk.

## Troubleshooting

| Problem | Fix |
|---|---|
| "capture was blank" | Switch WARDOGS to Borderless or Windowed fullscreen. |
| A match wasn't logged | Press **Ctrl+Alt+E** on the results screen. Check `data/tracker.log`. |
| A wrong match was logged | Click **not a match** on the dashboard. |
| Something else is wrong | Click **Report an issue** at the top of the dashboard, add a screenshot, and send it. |
| Stop the tracker | End `pythonw.exe` in Task Manager. Remove the Startup shortcut to stop autostart. |

## More

- [How it works](docs/HOW-IT-WORKS.md): every tab, what gets read and when, and where your data lives
- Tests: `py -3 -m unittest discover -s tests -t .`; UI check (needs Playwright): `py -3 tests/ui_smoke.py`. Both run in GitHub Actions (QA) on every push and PR
- Not affiliated with the WARDOGS developers or wardogstracker.gg
- MIT license
