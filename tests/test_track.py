import io
import json
import os
import tempfile
import unittest
from unittest import mock

from wdt import track
from wdt.store import Store
from wdt.tracker import DEFAULTS, Tracker

RW, RH = 1920, 1080
PX, PY = track.PANE[0] * RW, track.PANE[1] * RH
PW, PH = track.PANE[2] * RW, track.PANE[3] * RH


def L(text, x, y, w=None, h=None):
    d = {"text": text, "x": x, "y": y}
    if w:
        d.update(w=w, h=h)
    return d


def labels(x0=64.35, y0=110.8, step=52.0, xs=range(65, 77), ys=range(99, 111)):
    """Grid labels as OCR gives them for a pane whose top-left is (x0, y0)."""
    out = [L(str(v), PX + (v - x0) * step - 12, PY + PH + 18, 24, 20) for v in xs]
    out += [L(str(v), PX - 40, PY + (y0 - v) * step - 10, 34, 20) for v in ys]
    return out


class FullMapTests(unittest.TestCase):
    def test_map_names_read_off_screens_find_their_tiles(self):
        from wdt.sources import map_key
        self.assertEqual([map_key(n) for n in ("Bakurani", "Akurani", "OZETI", "Zestafona ", "Lonestar", None)],
                         ["bakurani", "bakurani", "ozeti", "zestafona", None, None])

    def test_only_real_pictures_are_kept(self):
        from wdt import sources
        page = io.BytesIO(b"<html>busy</html>")  # a site error page, not a map picture
        with tempfile.TemporaryDirectory() as d, mock.patch("wdt.sources.urllib.request.urlopen", return_value=page):
            self.assertIsNone(sources.map_tile(d, "ozeti", 0, 0, 0, True))
            self.assertFalse(os.path.exists(os.path.join(d, "maptiles")))


class GeorefTests(unittest.TestCase):
    def test_pane_edges_from_grid_labels(self):
        g = track.georef(labels(), RW, RH)
        self.assertAlmostEqual(g["x0"], 64.35, places=1)
        self.assertAlmostEqual(g["x1"], 64.35 + PW / 52, places=1)
        self.assertAlmostEqual(g["y0"], 110.8, places=1)
        self.assertAlmostEqual(g["y1"], 110.8 - PH / 52, places=1)

    def test_misread_labels_fall_out(self):
        lines = labels() + [L("17", PX + 100, PY + PH + 18, 24, 20), L("7", PX + 300, PY + PH + 18, 12, 20)]
        self.assertAlmostEqual(track.georef(lines, RW, RH)["x0"], 64.35, places=1)

    def test_zoomed_in_with_two_labels_on_one_axis(self):
        g = track.georef(labels(x0=70.03, y0=107.09, step=151.5, xs=(72, 74), ys=(103, 104, 105, 106)), RW, RH)
        self.assertAlmostEqual(g["x0"], 70.03, places=1)
        self.assertAlmostEqual(g["y1"], 107.09 - PH / 151.5, places=1)

    def test_kilometre_scale(self):
        # 2026-10-30: zoomed out the scale says 1KM, but the labels still count 100 m
        # (the cursor read x100.63 on a pane labelled 94 to 104); x10 put it off the map
        lines = labels() + [L("1KM", PX + PW + 30, PY + 50)] * 3
        self.assertAlmostEqual(track.georef(lines, RW, RH)["x0"], 64.35, places=1)

    def test_one_misread_axis_follows_the_other(self):
        # 2026-10-30: the left labels came out 4% too far apart, which put the pane 1.7 south of
        # where the cursor read showed it; the map's pixels are square, so the axis with more labels sets the scale
        lines = labels(xs=range(65, 77), ys=()) + labels(step=54.2, y0=110.8, xs=(), ys=(102, 106, 110))
        g = track.georef(lines, RW, RH)
        self.assertAlmostEqual((g["x1"] - g["x0"]) / PW, (g["y0"] - g["y1"]) / PH, places=4)
        self.assertAlmostEqual(g["x0"], 64.35, places=1)

    def test_not_enough_labels(self):
        self.assertIsNone(track.georef(labels(xs=(70,), ys=(100, 101)), RW, RH))
        self.assertIsNone(track.georef([], RW, RH))
        # axes that disagree on scale aren't one map
        bad = labels(step=52, ys=()) + labels(step=80, xs=())
        self.assertIsNone(track.georef(bad, RW, RH))

    def test_map_open(self):
        self.assertTrue(track.map_open([L("Toggle Zoom", 773, 970), L("Recenter", 1206, 970)]))
        self.assertTrue(track.map_open([L("-100M", 1290, y) for y in (300, 360, 420)]))
        self.assertFalse(track.map_open([L("Toggle Zoom", 773, 970), L("$3,000", 10, 10)]))

    def test_unzoom_moves_strip_lines_back_onto_the_frame(self):
        frac = (0.5, 0.25, 0.1, 0.05)  # 192 x 54 on the frame, read at 3x
        got = track.unzoom([L("70", 30, 60, 60, 45), "junk"], frac, 576, 162, RW, RH)
        self.assertEqual(got, [{"text": "70", "x": 970.0, "y": 290.0, "w": 20.0, "h": 15.0}])


class FileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def test_crop(self):
        px = lambda x, y: bytes((x, y, 0, 255))
        frame = b"".join(px(x, y) for y in range(4) for x in range(4))
        out, w, h = track.crop(frame, 4, 4, (0.25, 0.5, 0.5, 0.5))
        self.assertEqual((w, h), (2, 2))
        self.assertEqual(out, px(1, 2) + px(2, 2) + px(1, 3) + px(2, 3))

    def test_prune_keeps_the_newest_and_leaves_other_names(self):
        root = os.path.join(self.d, "track")
        for n in ("20261027-100000", "20261027-110000", "20261027-120000", "maps"):
            os.makedirs(os.path.join(root, n))
        track.prune(root, 1)
        self.assertEqual(sorted(os.listdir(root)), ["20261027-120000", "maps"])
        track.prune(root, 0)
        self.assertEqual(os.listdir(root), ["maps"])

    def test_listing_and_file_paths(self):
        rnd = os.path.join(self.d, "track", "20261027-210000")
        maps = os.path.join(self.d, "track", "maps")
        os.makedirs(rnd)
        os.makedirs(maps)
        for n in ("1761600003.jpg", "1761600000.jpg", "notes.txt"):
            open(os.path.join(rnd, n), "wb").close()
        open(os.path.join(maps, "20261027-210100.jpg"), "wb").close()
        with open(os.path.join(maps, "20261027-210100.json"), "w") as f:
            json.dump({"x0": 1, "file": "20261027-210100.jpg"}, f)
        with open(os.path.join(maps, "20261027-210200.json"), "w") as f:
            json.dump({"x0": 1, "file": "gone.jpg"}, f)  # its picture was removed
        got = track.listing(self.d, "20261027-210000")
        self.assertEqual(got["snaps"], [[1761600000, "1761600000.jpg"], [1761600003, "1761600003.jpg"]])
        self.assertEqual([m["file"] for m in got["maps"]], ["20261027-210100.jpg"])
        self.assertEqual(track.listing(self.d, "../..")["snaps"], [])
        self.assertTrue(track.file_path(self.d, "20261027-210000", "1761600000.jpg"))
        self.assertTrue(track.file_path(self.d, "maps", "20261027-210100.jpg"))
        for rd, n in (("20261027-210000", "notes.txt"), ("20261027-210000", "..\\..\\t.db"),
                      ("..", "wardogs.db"), ("maps", "20261027-210100.json"), ("20261027-210000", "1761609999.jpg")):
            self.assertIsNone(track.file_path(self.d, rd, n), (rd, n))


class TrackerCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(os.path.join(self.tmp.name, "t.db"))
        cfg = dict(DEFAULTS, steam_userdata=self.tmp.name)
        self.t = Tracker(cfg, self.store, self.tmp.name)
        self.t.rect = (0, 0, RW, RH)
        self.saved = []
        write = lambda path, data, w, h, quality=70: (self.saved.append((os.path.basename(path), w, h)),
                                                      open(path, "wb").close(), path)[-1]
        self.patches = [mock.patch("wdt.tracker.winapi.grab", side_effect=lambda r, w, h: b"\0" * (w * h * 4)),
                        mock.patch("wdt.tracker.winapi.write_jpg", side_effect=write)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.store.db.close()
        self.tmp.cleanup()

    def test_minimap_pictures_only_while_playing_and_land_on_the_match(self):
        self.t._start_session(1761600000)
        with mock.patch.object(self.t, "_read_and_log"), mock.patch.object(self.t, "_read_feeds"), \
                mock.patch("wdt.tracker.winapi.foreground_rect", return_value=self.t.rect), \
                mock.patch.object(self.t, "_thumb", return_value=b"\0" * 2304), \
                mock.patch.object(self.t, "_pull_api"), mock.patch.object(self.t, "_pull_status"):
            self.t.pids = {1}
            self.t.next_proc_check = 1e12
            self.t.tick(1761600001)
            self.assertEqual(self.saved, [])  # not in a round yet
            self.t.hud["playing"] = 1761600001
            for s in (1761600002, 1761600003, 1761600005):
                self.t.tick(s)
        self.assertEqual(self.saved, [("1761600002.jpg", 171, 153), ("1761600005.jpg", 171, 153)])
        rnd = self.t.hud["track"]
        self.t.record_end("hotkey", 1761600100, screen=False)
        m = self.store.dump()["matches"][0]
        self.assertEqual(self.store.match_full(m["id"])["details"]["track"], {"dir": rnd, "n": 2, "maps": []})
        self.assertEqual(sorted(os.listdir(os.path.join(self.tmp.name, "track", rnd))),
                         ["1761600002.jpg", "1761600005.jpg"])

    def test_open_big_map_is_kept_with_its_coordinates(self):
        self.t.hud["track"] = "20261027-210000"
        hint = [L("Toggle Zoom", 773, 970), L("Toggle Legend", 911, 970)]
        with mock.patch.object(self.t, "_ocr_frame", return_value=[]):
            self.t._note_map(1761600000, RW, RH, hint + labels())
            self.t._note_map(1761600003, RW, RH, hint + labels())  # too soon to keep another
            self.t._note_map(1761600020, RW, RH, labels())        # map closed
        self.assertEqual(len(self.saved), 1)
        name = self.t.hud["maps"][0]
        with open(os.path.join(self.tmp.name, "track", "maps", name + ".json")) as f:
            m = json.load(f)
        self.assertEqual((m["file"], m["round"], m["w"]), (name + ".jpg", "20261027-210000", 656))
        self.assertAlmostEqual(m["x0"], 64.35, places=1)
        self.assertLessEqual(self.t.next_hud, 1761600004)  # looks again soon in case you pan


if __name__ == "__main__":
    unittest.main()
