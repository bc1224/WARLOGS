# Contributing

Thanks for helping. Bug reports, misread screens and small fixes are all welcome.

## Reporting a problem

- **From the app**: click **Report an issue** at the top of the dashboard, add a
  screenshot and press Send. It becomes a GitHub issue with the details the app needs.
- **On GitHub**: open an issue with the bug report or feature request template.

Misreads (a wrong gun, reward, map or result) are easiest to fix with a screenshot of
what the game showed.

## Changing the code

1. Fork the repo and make a branch from `main`.
2. Run the app with `Start Tracker.vbs` (see the README), or try the dashboard with
   sample data: `py -3 demo.py`.
3. Add or update a test in `tests/` for what you changed. Screen-reading fixes should
   include the OCR text that was misread.
4. Run the checks:
   - `py -3 -m unittest discover -s tests -t .`
   - `py -3 tests/ui_smoke.py` (needs Playwright) if you touched the dashboard.
5. Open a pull request. GitHub Actions runs the same checks.

Keep changes small and focused, match the style of the code around them, and don't
commit anything from `data/` or your `config.json`.

By contributing you agree your work is released under the [MIT License](LICENSE).
