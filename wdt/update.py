"""Update check: compare this folder with the latest version on GitHub and, when
you click Update on the dashboard, install it.

Works for the ZIP download and for a git clone. Each file is compared by its git
hash, so nothing has to remember which version you unzipped. Your config.json
and data folder are never touched.
"""
import hashlib
import io
import json
import logging
import os
import subprocess
import threading
import time
import urllib.request
import zipfile

REPO = "bc1224/wardogs-match-tracker"
API = f"https://api.github.com/repos/{REPO}"
UA = "wardogs-local-tracker/1.0 (update check)"
KEEP = {"config.json", ".python-path", "WARDOGS Dashboard.url"}
CHECK_EVERY = 6 * 3600
NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW

log = logging.getLogger(__name__)


def blob_sha(data):
    """The hash git gives a file's contents."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _kept(rel):
    first = rel.split("/", 1)[0]
    return rel in KEEP or first in ("data", ".git")


def installed_version(root, ddir):
    """Short id of the version in this folder: the last update installed, or the git checkout."""
    try:
        with open(os.path.join(ddir, "installed.json"), encoding="utf-8") as f:
            return json.load(f)["sha"][:7]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        with open(os.path.join(root, ".git", "HEAD"), encoding="utf-8") as f:
            head = f.read().strip()
        if not head.startswith("ref: "):
            return head[:7]
        ref = head[5:]
        try:
            with open(os.path.join(root, ".git", *ref.split("/")), encoding="utf-8") as f:
                return f.read().strip()[:7]
        except OSError:
            with open(os.path.join(root, ".git", "packed-refs"), encoding="utf-8") as f:
                return next(l.split()[0][:7] for l in f if l.rstrip().endswith(" " + ref))
    except (OSError, StopIteration):
        return "unknown"


class Updater:
    def __init__(self, root, ddir, fetch=None, now=time.time, background=True):
        self.root, self.ddir = os.path.abspath(root), ddir
        self.fetch, self.now, self.background = fetch or _get, now, background
        self.lock = threading.Lock()
        self.last = {"available": False, "checked": None, "error": None}
        self.checking = False

    # paths and files ------------------------------------------------------------------
    def _path(self, rel):
        """Where a repo path lives here, or None if it would land outside this folder."""
        if _kept(rel) or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
            return None
        p = os.path.normpath(os.path.join(self.root, rel))
        return p if p.startswith(self.root + os.sep) else None

    def _same(self, rel, sha):
        p = self._path(rel)
        try:
            with open(p, "rb") as f:
                data = f.read()
        except (OSError, TypeError):
            return False
        # a clone on Windows may have turned line ends into CRLF
        return sha in (blob_sha(data), blob_sha(data.replace(b"\r\n", b"\n")))

    def _manifest_path(self):
        return os.path.join(self.ddir, "installed.json")

    # GitHub ---------------------------------------------------------------------------
    def _latest(self):
        c = json.loads(self.fetch(f"{API}/commits/main"))
        tree = json.loads(self.fetch(f"{API}/git/trees/{c['sha']}?recursive=1"))
        blobs = {t["path"]: t["sha"] for t in tree["tree"] if t["type"] == "blob" and self._path(t["path"])}
        return c, blobs

    def check(self):
        """Ask GitHub for the latest version. Never raises."""
        try:
            c, blobs = self._latest()
            changed = [p for p, s in blobs.items() if not self._same(p, s)]
            st = {"available": bool(changed), "sha": c["sha"],
                  "title": c["commit"]["message"].split("\n", 1)[0],
                  "date": c["commit"]["committer"]["date"], "files": len(changed),
                  "url": f"https://github.com/{REPO}/commits/main", "error": None}
        except Exception as e:
            st = dict(self.last, available=False, error=str(e) or type(e).__name__)
        st["checked"] = self.now()
        st["git"] = os.path.isdir(os.path.join(self.root, ".git"))
        with self.lock:
            self.last = st
        return st

    def state(self):
        """The last check, starting a new one when it's old."""
        with self.lock:
            stale = not self.checking and (self.last["checked"] is None
                                           or self.now() - self.last["checked"] > CHECK_EVERY)
            if stale:
                self.checking = True
        if stale:
            def run():
                try:
                    self.check()
                finally:
                    self.checking = False
            if self.background:
                threading.Thread(target=run, daemon=True).start()
            else:
                run()
        with self.lock:
            return dict(self.last, checking=self.checking)

    # installing -----------------------------------------------------------------------
    def apply(self, sha):
        """Install the version the dashboard offered. Returns {"ok": ..., "error"/"files"}."""
        try:
            return self._apply(sha)
        except Exception as e:
            log.exception("update failed")
            return {"ok": False, "error": str(e) or type(e).__name__}

    def _apply(self, sha):
        c, blobs = self._latest()
        if c["sha"] != sha:  # pushed again since the last check: install the newest instead
            log.info("offered %s, installing the newer %s", sha[:7], c["sha"][:7])
            sha = c["sha"]
        if os.path.isdir(os.path.join(self.root, ".git")):
            r = subprocess.run(["git", "-C", self.root, "pull", "--ff-only", "origin", "main"],
                               capture_output=True, text=True, timeout=120, creationflags=NO_WINDOW)
            if r.returncode:
                return {"ok": False, "error": (r.stderr or r.stdout).strip()[-300:]}
            return {"ok": True, "files": None}
        z = zipfile.ZipFile(io.BytesIO(self.fetch(f"https://codeload.github.com/{REPO}/zip/{sha}")))
        files = {}
        for info in z.infolist():
            rel = info.filename.split("/", 1)[-1]
            if info.is_dir() or rel not in blobs:
                continue
            data = z.read(info)
            if blob_sha(data) != blobs[rel]:
                return {"ok": False, "error": f"download didn't match GitHub ({rel}); nothing changed"}
            files[rel] = data
        missing = set(blobs) - set(files)
        if missing:
            return {"ok": False, "error": f"download is missing {sorted(missing)[0]}; nothing changed"}
        # every file checked out; now write them
        for rel, data in files.items():
            p = self._path(rel)
            if self._same(rel, blobs[rel]):
                continue
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".update"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, p)
        # files the last update installed that this version no longer has, if you left them alone
        try:
            with open(self._manifest_path(), encoding="utf-8") as f:
                before = json.load(f)["files"]
        except (OSError, ValueError, KeyError):
            before = {}
        for rel, old in before.items():
            if rel not in blobs and self._path(rel) and self._same(rel, old):
                os.remove(self._path(rel))
        tmp = self._manifest_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"sha": sha, "files": blobs}, f)
        os.replace(tmp, self._manifest_path())
        log.info("updated to %s (%d files)", sha[:7], len(files))
        return {"ok": True, "files": len(files)}
