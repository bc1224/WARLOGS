import io
import json
import os
import tempfile
import unittest
import zipfile

from wdt.update import REPO, Updater, blob_sha

API = f"https://api.github.com/repos/{REPO}"


class FakeGitHub:
    """Serves one commit of the repo: its tree and its ZIP."""

    def __init__(self, sha, files, title="Fix the round timer"):
        self.sha, self.files, self.hits = sha, files, []
        self.title = title

    def __call__(self, url):
        self.hits.append(url)
        if url == f"{API}/commits/main":
            return json.dumps({"sha": self.sha, "commit": {"message": self.title + "\n\nmore",
                                                           "committer": {"date": "2026-10-27T23:00:00Z"}}}).encode()
        if url == f"{API}/git/trees/{self.sha}?recursive=1":
            return json.dumps({"tree": [{"path": p, "type": "blob", "sha": blob_sha(b)} for p, b in self.files.items()]
                              + [{"path": "wdt", "type": "tree", "sha": "x"}]}).encode()
        if url == f"https://codeload.github.com/{REPO}/zip/{self.sha}":
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w") as z:
                z.writestr(f"WARLOGS-{self.sha}/", "")
                for p, b in self.files.items():
                    z.writestr(f"WARLOGS-{self.sha}/{p}", b)
            return buf.getvalue()
        raise OSError("404 " + url)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        self.ddir = os.path.join(self.root, "data")
        os.makedirs(os.path.join(self.root, "wdt"))
        os.makedirs(self.ddir)
        self.write("wdt/tracker.py", b"old\n")
        self.write("README.md", b"same\r\n")  # unzipped or checked out with Windows line ends
        self.write("config.json", b'{"steam_id": "mine"}')
        self.write("data/wardogs.db", b"my matches")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, data):
        with open(os.path.join(self.root, rel), "wb") as f:
            f.write(data)

    def read(self, rel):
        with open(os.path.join(self.root, rel), "rb") as f:
            return f.read()

    def test_blob_sha_matches_git(self):
        self.assertEqual(blob_sha(b"hello\n"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_up_to_date_when_every_file_matches(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"old\n", "README.md": b"same\n"})
        st = Updater(self.root, self.ddir, fetch=gh).check()
        self.assertFalse(st["available"])
        self.assertIsNone(st["error"])

    def test_a_changed_or_new_file_means_an_update(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"new\n", "README.md": b"same\n", "wdt/update.py": b"x\n"})
        st = Updater(self.root, self.ddir, fetch=gh).check()
        self.assertTrue(st["available"])
        self.assertEqual((st["sha"], st["title"], st["files"]), ("abc", "Fix the round timer", 2))

    def test_docs_only_changes_are_not_offered(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"old\n", "README.md": b"new words\n",
                                "docs/social/post.md": b"x", "tests/test_x.py": b"x"})
        self.assertFalse(Updater(self.root, self.ddir, fetch=gh).check()["available"])

    def test_update_replaces_code_and_keeps_your_data(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"new\n", "README.md": b"same\n", "wdt/update.py": b"x\n"})
        up = Updater(self.root, self.ddir, fetch=gh)
        res = up.apply("abc")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.read("wdt/tracker.py"), b"new\n")
        self.assertEqual(self.read("wdt/update.py"), b"x\n")
        self.assertEqual(self.read("config.json"), b'{"steam_id": "mine"}')
        self.assertEqual(self.read("data/wardogs.db"), b"my matches")
        self.assertFalse(up.check()["available"])

    def test_a_file_removed_upstream_is_removed_unless_you_changed_it(self):
        Updater(self.root, self.ddir, fetch=FakeGitHub("a1", {"wdt/old.py": b"o\n", "wdt/mine.py": b"m\n",
                                                              "wdt/tracker.py": b"old\n"})).apply("a1")
        self.write("wdt/mine.py", b"my edit\n")
        Updater(self.root, self.ddir, fetch=FakeGitHub("a2", {"wdt/tracker.py": b"new\n"})).apply("a2")
        self.assertFalse(os.path.exists(os.path.join(self.root, "wdt/old.py")))
        self.assertEqual(self.read("wdt/mine.py"), b"my edit\n")

    def test_a_download_that_does_not_match_changes_nothing(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"new\n"})
        real = gh.__call__

        def tampered(url):
            body = real(url)
            if "codeload" in url:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w") as z:
                    z.writestr("WARLOGS-abc/wdt/tracker.py", b"evil\n")
                return buf.getvalue()
            return body
        res = Updater(self.root, self.ddir, fetch=tampered).apply("abc")
        self.assertFalse(res["ok"])
        self.assertEqual(self.read("wdt/tracker.py"), b"old\n")

    def test_a_version_pushed_after_the_offer_is_installed_instead(self):
        # 2026-10-27: the offer was hours old, and refusing it kept failing until the next check
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"new\n"})
        res = Updater(self.root, self.ddir, fetch=gh).apply("older")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.read("wdt/tracker.py"), b"new\n")

    def test_paths_outside_the_folder_are_never_written(self):
        gh = FakeGitHub("abc", {"../escape.py": b"x\n", "data/wardogs.db": b"wiped", "wdt/tracker.py": b"new\n"})
        res = Updater(self.root, self.ddir, fetch=gh).apply("abc")
        self.assertTrue(res["ok"], res)
        self.assertFalse(os.path.exists(os.path.join(self.root, "..", "escape.py")))
        self.assertEqual(self.read("data/wardogs.db"), b"my matches")

    def test_checks_are_cached(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"old\n"})
        clock = [1000.0]
        up = Updater(self.root, self.ddir, fetch=gh, now=lambda: clock[0], background=False)
        up.state()
        up.state()
        self.assertEqual(len(gh.hits), 2)  # one commit + one tree
        clock[0] += 7 * 3600
        up.state()
        self.assertEqual(len(gh.hits), 4)

    def test_refresh_button_checks_now_but_not_twice_a_minute(self):
        gh = FakeGitHub("abc", {"wdt/tracker.py": b"old\n"})
        clock = [1000.0]
        up = Updater(self.root, self.ddir, fetch=gh, now=lambda: clock[0], background=False)
        up.state()
        clock[0] += 30
        up.state(now=True)
        self.assertEqual(len(gh.hits), 2)  # GitHub allows 60 calls an hour
        clock[0] += 60
        up.state(now=True)
        self.assertEqual(len(gh.hits), 4)

    def test_offline_is_reported_not_raised(self):
        def offline(url):
            raise OSError("no network")
        st = Updater(self.root, self.ddir, fetch=offline).check()
        self.assertFalse(st["available"])
        self.assertIn("no network", st["error"])


if __name__ == "__main__":
    unittest.main()
