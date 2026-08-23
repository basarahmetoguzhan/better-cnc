"""Server routes, session wiring, and the INI loader's edge cases."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from helpers import UI_DIR  # noqa: F401  (ensures sys.path)

from bettercnc.app import Handler, build_session
from bettercnc.iniload import parse_ini_text
from bettercnc.state import DrivePower


class TestSession(unittest.TestCase):
    def test_build_session_validates_the_profile(self):
        sess = build_session("test-rig")
        self.assertEqual(sess.profile.key, "test-rig")
        self.assertTrue(sess.config.present)

    def test_marble_session_builds_without_a_config(self):
        sess = build_session("marble")
        self.assertFalse(sess.config.present)

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            build_session("nope")

    def test_view_renders_every_screen(self):
        sess = build_session("test-rig")
        for screen in ("kontrol", "program", "probing", "takimlar", "alarmlar", "makine"):
            with self.subTest(screen=screen):
                self.assertEqual(sess.view(screen)["screen"], screen)

    def test_unknown_action_is_ignored_not_raised(self):
        """A stale browser must not be able to 500 the controller UI."""
        sess = build_session("test-rig")
        sess.act("definitely-not-an-action", "x")
        sess.act("", "")
        self.assertIsNotNone(sess.view("kontrol"))

    def test_malformed_jog_value_is_ignored(self):
        sess = build_session("test-rig")
        for bad in ("", "X", "X:", ":1", "Q:1", "X:notanint"):
            with self.subTest(value=bad):
                sess.act("jog", bad)
        self.assertIsNotNone(sess.view("kontrol"))

    def test_malformed_numeric_actions_are_ignored(self):
        sess = build_session("test-rig")
        sess.act("jog_step", "abc")
        sess.act("feed", "abc")
        self.assertIsNotNone(sess.view("kontrol"))

    def test_actions_change_state(self):
        sess = build_session("test-rig")
        self.assertTrue(sess.view("kontrol")["kontrol"]["estop"]["software_disabled"])
        sess.act("software_reset", "")
        self.assertFalse(sess.view("kontrol")["kontrol"]["estop"]["software_disabled"])
        sess.act("home_all", "")
        self.assertTrue(sess.view("kontrol")["kontrol"]["homing"]["all_homed"])


class TestHttpRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Handler.session = build_session("test-rig")
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)

    def url(self, path: str) -> str:
        return "http://127.0.0.1:{}{}".format(self.port, path)

    def get(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=5) as r:
            return r.status, r.read().decode("utf-8")

    def post(self, path: str, payload: dict):
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.url(path), data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))

    def test_index_serves_a_full_page(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("<!doctype html>", body)
        self.assertIn("hm2_7i92.0", body)
        self.assertIn("--acc:", body)  # inlined stylesheet

    def test_index_includes_the_client_script(self):
        _, body = self.get("/")
        self.assertIn("api/action", body)

    def test_healthz(self):
        status, body = self.get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["ok"], True)

    def test_api_render_returns_html(self):
        status, body = self.get("/api/render?screen=makine")
        payload = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(payload["screen"], "makine")
        self.assertIn("833.300", payload["html"])

    def test_api_render_rejects_an_unknown_screen(self):
        _, body = self.get("/api/render?screen=../../etc/passwd")
        self.assertEqual(json.loads(body)["screen"], "kontrol")

    def test_api_state_returns_the_view_model(self):
        _, body = self.get("/api/state?screen=kontrol")
        vm = json.loads(body)
        self.assertIn("kontrol", vm)
        self.assertIn("link", vm)

    def test_unknown_path_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)

    def test_post_to_wrong_path_404s(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post("/api/nope", {})
        self.assertEqual(ctx.exception.code, 404)

    def test_action_round_trip(self):
        status, payload = self.post(
            "/api/action", {"action": "software_reset", "value": "", "screen": "kontrol"}
        )
        self.assertEqual(status, 200)
        self.assertIn("html", payload)

    def test_malformed_json_does_not_crash(self):
        req = urllib.request.Request(
            self.url("/api/action"),
            data=b"{not json",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_no_route_can_clear_a_hardware_estop(self):
        Handler.session.adapter.hardware_chain_open()
        for action in ("software_reset", "home_all", "program_start", "wcs"):
            with self.subTest(action=action):
                self.post("/api/action", {"action": action, "value": "G55"})
                self.assertIs(
                    Handler.session.adapter.snapshot().estop.drive_power,
                    DrivePower.CUT,
                )


class TestIniLoaderEdgeCases(unittest.TestCase):
    def test_empty_text(self):
        ini = parse_ini_text("")
        self.assertEqual(len(ini), 0)

    def test_comment_only(self):
        self.assertEqual(len(parse_ini_text("# nothing here\n")), 0)

    def test_semicolon_comments(self):
        ini = parse_ini_text("[S]\n; ASSUMPTION: guessed\nK = 1\n")
        self.assertTrue(ini.require("S", "K").provisional)

    def test_blank_line_ends_a_comment_block(self):
        ini = parse_ini_text("[S]\n# ASSUMPTION: x\n\nK = 1\n")
        # the blank line clears the pending block, so K inherits nothing new
        self.assertFalse(ini.require("S", "K").provisional)

    def test_section_change_resets_governing_comment(self):
        ini = parse_ini_text(
            "[A]\n# ASSUMPTION: guessed\nK = 1\n[B]\nK = 2\n"
        )
        self.assertTrue(ini.require("A", "K").provisional)
        self.assertFalse(ini.require("B", "K").provisional)

    def test_require_raises_with_a_useful_message(self):
        ini = parse_ini_text("[S]\nK = 1\n")
        with self.assertRaises(KeyError) as ctx:
            ini.require("S", "MISSING")
        self.assertIn("refuses to invent", str(ctx.exception))

    def test_get_returns_none_for_missing(self):
        self.assertIsNone(parse_ini_text("[S]\nK=1\n").get("S", "X"))

    def test_contains(self):
        ini = parse_ini_text("[S]\nK=1\n")
        self.assertIn(("S", "K"), ini)
        self.assertNotIn(("S", "Z"), ini)

    def test_value_conversions(self):
        ini = parse_ini_text("[S]\nF = 1.5\nI = 42.0\n")
        self.assertAlmostEqual(ini.require("S", "F").as_float(), 1.5)
        self.assertEqual(ini.require("S", "I").as_int(), 42)

    def test_sections_and_keys(self):
        ini = parse_ini_text("[A]\nX=1\nY=2\n[B]\nZ=3\n")
        self.assertEqual(ini.sections(), ["A", "B"])
        self.assertEqual(ini.keys("A"), ["X", "Y"])

    def test_quoted_values_are_kept_raw(self):
        ini = parse_ini_text('[S]\nC = "a b c"\n')
        self.assertEqual(ini.require("S", "C").raw, '"a b c"')

    def test_all_provisional_markers_are_recognised(self):
        from bettercnc.iniload import PROVISIONAL_MARKERS

        for marker in PROVISIONAL_MARKERS:
            with self.subTest(marker=marker):
                ini = parse_ini_text("[S]\n# {} something\nK = 1\n".format(marker))
                self.assertTrue(ini.require("S", "K").provisional)

    def test_marker_matching_is_case_insensitive(self):
        ini = parse_ini_text("[S]\n# assumption: lowercase\nK = 1\n")
        self.assertTrue(ini.require("S", "K").provisional)


if __name__ == "__main__":
    unittest.main(verbosity=2)
