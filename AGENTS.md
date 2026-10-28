# Setting this up for a user (instructions for AI coding agents)

You're installing WARLOGS on the user's Windows PC. It's a local Python
app that reads the screen (like OBS) and serves a dashboard on http://127.0.0.1:8790.

## Rules

- Windows only. It needs Python 3.10+ and nothing else (stdlib only, no pip installs).
- Never touch the WARDOGS game process, its files or memory. The tracker only reads the
  screen; keep it that way.
- Don't ask the user for passwords or Steam login. The Steam ID comes from their local
  Steam install; their in-game name is the only thing you may need to ask.
- Don't open admin prompts or change system settings.

## Steps

1. Clone the repo somewhere the user owns, for example
   `git clone https://github.com/bc1224/warlogs "$env:USERPROFILE\Documents\wardogs-tracker"`.
   No git? Download and unzip `https://github.com/bc1224/warlogs/archive/refs/heads/main.zip`.
2. Ask the user for their exact WARDOGS in-game name (as it appears on the scoreboard).
   If they don't know, skip `-PlayerName`; setup uses their Steam display name.
3. From the repo folder run:
   ```
   powershell -NoProfile -ExecutionPolicy Bypass -File setup.ps1 -Yes -PlayerName "<their name>"
   ```
   `-Yes` takes every default: the most recently used Steam account on this PC, a Windows
   Startup shortcut, start the tracker, open the dashboard. Add `-SteamId 7656...` if they
   have several Steam accounts and name a different one, `-NoStartup` if they don't want
   it to run at login. If Python is missing, setup offers `winget install Python.Python.3.12`
   (user scope); with `-Yes` it installs it.
4. Check it worked: `Invoke-WebRequest http://127.0.0.1:8790/api/version -UseBasicParsing`
   should return JSON. If not, read `data\tracker.log`.
5. Tell the user two things: set WARDOGS to Borderless or Windowed fullscreen, and open
   `https://wardogstracker.gg/profile/<their SteamID64>` once to update their stats there.

## Other commands

- Try the dashboard with made-up data (no game needed): `py -3 demo.py` (port 8791).
- Tests: `py -3 -m unittest discover -s tests -t .`; UI check (needs Playwright): `py -3 tests/ui_smoke.py`. Both run in GitHub Actions (QA) on every push and PR
- Uninstall: `powershell -ExecutionPolicy Bypass -File setup.ps1 -Uninstall`, then delete the folder.

## Issue reports

- The dashboard's **Report an issue** button posts to `/api/report`, which forwards to the
  Cloudflare Worker in `relay/` (`wardogs-report-relay.bcf1224.workers.dev`). The relay files a
  GitHub issue labelled `from-app` and keeps screenshots in KV (served at `/img/<id>`).
- Relay tests: `node --test relay/worker.test.mjs`. Deploy from `relay/` with `npx wrangler deploy`.
- `.github/workflows/fix-from-app-report.yml` has Claude open a fix PR for each `from-app` issue.
  It never merges; the owner reviews and merges, and the updater only offers what is on main.
