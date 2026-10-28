import unittest
from unittest import mock

from wdt import window

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
ENV = {"ProgramFiles(x86)": r"C:\Program Files (x86)", "ProgramFiles": r"C:\Program Files",
       "LOCALAPPDATA": r"C:\Users\me\AppData\Local"}


class WindowTests(unittest.TestCase):
    def test_prefers_edge_then_chrome(self):
        self.assertEqual(window.app_browser(ENV, {EDGE, CHROME}.__contains__), EDGE)
        self.assertEqual(window.app_browser(ENV, {CHROME}.__contains__), CHROME)
        self.assertIsNone(window.app_browser(ENV, lambda p: False))

    def test_app_mode_has_no_tabs_or_address_bar(self):
        cmd = window.app_command(EDGE, "http://127.0.0.1:8790/")
        self.assertEqual(cmd[:2], [EDGE, "--app=http://127.0.0.1:8790/"])

    def test_starts_tracker_only_when_dashboard_is_down(self):
        for up in (True, False):
            with mock.patch.object(window, "answering", return_value=up), \
                 mock.patch.object(window, "start_tracker") as start, \
                 mock.patch.object(window, "app_browser", return_value=EDGE), \
                 mock.patch.object(window.subprocess, "Popen") as popen:
                window.main()
            self.assertEqual(start.called, not up)
            self.assertEqual(popen.call_args[0][0][0], EDGE)

    def test_falls_back_to_default_browser(self):
        with mock.patch.object(window, "answering", return_value=True), \
             mock.patch.object(window, "app_browser", return_value=None), \
             mock.patch.object(window.webbrowser, "open") as op:
            window.main()
        op.assert_called_once()


if __name__ == "__main__":
    unittest.main()
