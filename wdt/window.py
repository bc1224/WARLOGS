"""Opens the dashboard in its own app window (Edge or Chrome with no tabs or address
bar), starting the tracker first if it isn't running. Falls back to the default browser."""
import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SIZE = "1440,900"


def port():
    try:
        with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
            return int(json.load(f).get("dashboard_port", 8790))
    except (OSError, ValueError):
        return 8790


def app_browser(env=os.environ, exists=os.path.isfile):
    """Edge ships with Windows 10 and 11; Chrome is the backup."""
    paths = []
    for base in (env.get("ProgramFiles(x86)"), env.get("ProgramFiles"), env.get("LOCALAPPDATA")):
        if base:
            paths.append(os.path.join(base, "Microsoft", "Edge", "Application", "msedge.exe"))
    for base in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LOCALAPPDATA")):
        if base:
            paths.append(os.path.join(base, "Google", "Chrome", "Application", "chrome.exe"))
    return next((p for p in paths if exists(p)), None)


def app_command(exe, url):
    return [exe, f"--app={url}", f"--window-size={SIZE}"]


def answering(url, timeout=2):
    try:
        with urllib.request.urlopen(url + "api/version", timeout=timeout):
            return True
    except OSError:
        return False


def start_tracker(url, wait=15):
    """The tracker serves the dashboard; a second copy just exits, so this is safe."""
    subprocess.Popen([sys.executable, os.path.join(ROOT, "tracker.pyw")], cwd=ROOT,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    end = time.time() + wait
    while time.time() < end:
        if answering(url):
            return True
        time.sleep(0.5)
    return False


def main():
    url = f"http://127.0.0.1:{port()}/"
    if not answering(url):
        start_tracker(url)
    exe = app_browser()
    if exe:
        subprocess.Popen(app_command(exe, url))
    else:
        webbrowser.open(url)


if __name__ == "__main__":
    main()
