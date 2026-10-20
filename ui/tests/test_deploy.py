"""deploy/ - the kiosk and the machine over SSH - and the kiosk's boot screen
(bettercnc.bootpage).

What runs here, off the machine: the INIs the kiosk and the simulator derive
from the rig's (scripts/ini_derive.py), the boot screen's server, and the
rules deploy/README.md states, pinned in the scripts as text. The kiosk itself
- cage, Chromium, LinuxCNC, the UI - runs in sim/ (`sim/sim.sh kiosk`), and
the systemd unit and shell scripts are checked there (`sim/sim.sh lint`).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from helpers import REPO_ROOT

from bettercnc import bootpage as B
from bettercnc.configmodel import load_config_from_ini

sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))
from ini_derive import derive  # noqa: E402

RIG_INI = os.path.join(REPO_ROOT, "configs", "test-rig", "test-rig.ini")
DEPLOY = os.path.join(REPO_ROOT, "deploy")


def _load(path: str, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


make_kiosk_ini = _load(os.path.join(DEPLOY, "make_kiosk_ini.py"), "make_kiosk_ini")
make_sim_ini = _load(os.path.join(REPO_ROOT, "sim", "make_sim_ini.py"), "make_sim_ini")


def read(rel: str) -> str:
    with open(os.path.join(REPO_ROOT, rel), "r", encoding="utf-8") as fh:
        return fh.read()


def rig_text() -> str:
    return read("configs/test-rig/test-rig.ini")


def changed_lines(a: str, b: str):
    """(line number, old, new) for every line that differs; both texts must
    have the same lines up to the end of the shorter one."""
    la, lb = a.splitlines(), b.splitlines()
    return [(i + 1, x, y) for i, (x, y) in enumerate(zip(la, lb)) if x != y]


# ---------------------------------------------------------------------------
# scripts/ini_derive.py
# ---------------------------------------------------------------------------


class TestIniDerive(unittest.TestCase):
    TEXT = (
        "[DISPLAY]\n"
        "DISPLAY = axis\n"
        "CYCLE_TIME = 0.1\n"
        "# DISPLAY = commented\n"
        "[OTHER]\n"
        "DISPLAY = not-this-one\n"
    )

    def test_only_the_key_in_its_section_changes(self):
        out = derive(self.TEXT, {("DISPLAY", "DISPLAY"): "/x/y"}, "t.ini")
        self.assertEqual(changed_lines(self.TEXT, out), [(2, "DISPLAY = axis", "DISPLAY = /x/y")])
        self.assertEqual(len(out.splitlines()), len(self.TEXT.splitlines()))

    def test_a_missing_key_is_refused(self):
        with self.assertRaises(SystemExit) as ctx:
            derive(self.TEXT, {("DISPLAY", "NOPE"): "1"}, "t.ini")
        self.assertIn("[DISPLAY]NOPE", str(ctx.exception))

    def test_a_key_found_twice_is_refused(self):
        with self.assertRaises(SystemExit):
            derive(self.TEXT + "[DISPLAY]\nDISPLAY = again\n", {("DISPLAY", "DISPLAY"): "x"}, "t.ini")

    def test_append_goes_after_a_final_line_without_newline(self):
        out = derive("[A]\nK = 1", {("A", "K"): "2"}, "t.ini", append="[B]\nJ = 3\n")
        self.assertEqual(out, "[A]\nK = 2\n[B]\nJ = 3\n")


# ---------------------------------------------------------------------------
# deploy/make_kiosk_ini.py and sim/make_sim_ini.py on the real rig INI
# ---------------------------------------------------------------------------


class TestKioskIni(unittest.TestCase):
    def test_only_the_display_line_differs_from_the_rig(self):
        out = make_kiosk_ini.convert(rig_text(), RIG_INI)
        diff = changed_lines(rig_text(), out)
        self.assertEqual(len(diff), 1, diff)
        self.assertEqual(diff[0][1], "DISPLAY = axis")
        self.assertEqual(diff[0][2], "DISPLAY = " + make_kiosk_ini.DISPLAY_PROGRAM)

    def test_the_display_program_exists_and_runs(self):
        self.assertTrue(os.access(make_kiosk_ini.DISPLAY_PROGRAM, os.X_OK))

    def test_written_next_to_its_source_so_relative_paths_match_axis(self):
        """HALFILE, PARAMETER_FILE, TOOL_TABLE stay relative and resolve
        against the INI's directory - the same files an axis run uses."""
        self.assertEqual(
            make_kiosk_ini.kiosk_path(RIG_INI),
            os.path.join(os.path.dirname(RIG_INI), "test-rig-kiosk.ini"),
        )
        out = make_kiosk_ini.convert(rig_text(), RIG_INI)
        self.assertIn("\nHALFILE = test-rig.hal\n", out)
        self.assertIn("\nPARAMETER_FILE = test-rig.var\n", out)

    def test_main_writes_the_file_and_prints_its_path(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "m.ini")
            with open(src, "w", encoding="utf-8") as fh:
                fh.write(rig_text())
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(make_kiosk_ini.main(["x", src]), 0)
            path = out.getvalue().strip()
            self.assertEqual(path, os.path.join(d, "m-kiosk.ini"))
            # The UI reads the same machine from either file.
            self.assertEqual(load_config_from_ini(path).axes, load_config_from_ini(src).axes)
            self.assertFalse(os.path.exists(path + ".tmp"))

    def test_main_refuses_a_kiosk_ini_and_bad_usage(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(make_kiosk_ini.main(["x", "a-kiosk.ini"]), 2)
            self.assertEqual(make_kiosk_ini.main(["x"]), 2)
        self.assertIn("already a kiosk INI", err.getvalue())

    def test_a_missing_source_is_one_line_for_the_boot_screen(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(make_kiosk_ini.main(["x", "/nonexistent/m.ini"]), 1)
        self.assertEqual(err.getvalue().count("\n"), 1, err.getvalue())
        self.assertIn("/nonexistent/m.ini okunamadı", err.getvalue())

    def test_gitignored(self):
        self.assertIn("configs/**/*-kiosk.ini", read(".gitignore"))
        self.assertIn("deploy/machine.local", read(".gitignore"))


class TestSimIniAfterTheRefactor(unittest.TestCase):
    """sim/make_sim_ini.py moved its copying to scripts/ini_derive.py."""

    def test_exactly_halfile_and_display_change(self):
        out = make_sim_ini.convert(rig_text())
        diff = changed_lines(rig_text(), out)
        self.assertEqual([d[1] for d in diff], ["DISPLAY = axis", "HALFILE = test-rig.hal"])
        self.assertIn("\n[SIM]\nLINK_STUB = ", out)


# ---------------------------------------------------------------------------
# bettercnc.bootpage
# ---------------------------------------------------------------------------


class _Ui(BaseHTTPRequestHandler):
    """Stands in for the operator UI's /healthz."""

    answer = (200, b'{"ok": true}')

    def do_GET(self):  # noqa: N802
        code, body = self.answer
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _server(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    t = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    t.start()
    return httpd, "http://127.0.0.1:{}/".format(httpd.server_address[1])


class TestBootStatus(unittest.TestCase):
    def test_no_file_or_an_empty_one_reads_as_starting(self):
        self.assertEqual(B.read_status(None), B.NO_STATUS)
        self.assertEqual(B.read_status("/nonexistent/bettercnc/status.txt"), B.NO_STATUS)
        with tempfile.NamedTemporaryFile("w", delete=False) as fh:
            fh.write("  \n")
        self.addCleanup(os.unlink, fh.name)
        self.assertEqual(B.read_status(fh.name), B.NO_STATUS)

    def test_text_is_read_capped_and_never_raises_on_bad_bytes(self):
        with tempfile.NamedTemporaryFile("wb", delete=False) as fh:
            fh.write(b"LinuxCNC durdu \xff\n" + b"x" * (2 * B.MAX_STATUS_BYTES))
        self.addCleanup(os.unlink, fh.name)
        text = B.read_status(fh.name)
        self.assertTrue(text.startswith("LinuxCNC durdu �"))
        self.assertLessEqual(len(text.encode("utf-8")), B.MAX_STATUS_BYTES + 2)


class TestUiIsUp(unittest.TestCase):
    def serve(self, code, body):
        handler = type("H", (_Ui,), {"answer": (code, body)})
        httpd, url = _server(handler)
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        return url

    def test_up_only_on_ok_true(self):
        self.assertTrue(B.ui_is_up(self.serve(200, b'{"ok": true}')))
        self.assertFalse(B.ui_is_up(self.serve(200, b'{"ok": false}')))
        self.assertFalse(B.ui_is_up(self.serve(200, b"not json")))
        self.assertFalse(B.ui_is_up(self.serve(500, b'{"ok": true}')))

    def test_nothing_listening_is_down(self):
        httpd, url = _server(_Ui)
        httpd.shutdown()
        httpd.server_close()
        self.assertFalse(B.ui_is_up(url, timeout=0.2))


class TestBootPageServer(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, B.Handler, "status_file", B.Handler.status_file)
        self.addCleanup(setattr, B.Handler, "ui_url", B.Handler.ui_url)
        fd, self.status_path = tempfile.mkstemp()
        os.close(fd)
        self.addCleanup(os.unlink, self.status_path)
        ui, self.ui_url = _server(_Ui)
        self.addCleanup(ui.server_close)
        self.addCleanup(ui.shutdown)
        B.Handler.status_file = self.status_path
        B.Handler.ui_url = self.ui_url
        boot, self.url = _server(B.Handler)
        self.addCleanup(boot.server_close)
        self.addCleanup(boot.shutdown)

    def get(self, path):
        with urllib.request.urlopen(self.url.rstrip("/") + path, timeout=2) as r:
            return r.status, r.read().decode("utf-8")

    def test_page_shows_the_status_escaped_and_knows_where_the_ui_is(self):
        with open(self.status_path, "w", encoding="utf-8") as fh:
            fh.write("LinuxCNC durdu <b>(kod 1)</b>")
        code, page = self.get("/")
        self.assertEqual(code, 200)
        self.assertIn("LinuxCNC durdu &lt;b&gt;(kod 1)&lt;/b&gt;", page)
        self.assertIn("var ui = {};".format(json.dumps(self.ui_url)), page)
        self.assertIn("location.replace(ui)", page)
        self.assertNotIn("linuxcnc", page.lower().replace("linuxcnc durdu", ""))

    def test_status_api(self):
        with open(self.status_path, "w", encoding="utf-8") as fh:
            fh.write("LinuxCNC başlatılıyor… (deneme 2)\n")
        code, body = self.get("/api/status")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"text": "LinuxCNC başlatılıyor… (deneme 2)", "ui_up": True})

    def test_healthz_and_404(self):
        self.addCleanup(setattr, B.Contact, "last", B.Contact.last)
        B.Contact.last = None
        code, body = self.get("/healthz")
        self.assertEqual((code, json.loads(body)), (200, {"ok": True, "client_age_s": None}))
        self.get("/api/status")
        self.assertLess(json.loads(self.get("/healthz")[1])["client_age_s"], 1.0)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.get("/nope")
        self.assertEqual(ctx.exception.code, 404)


class TestBootPageEntryPoint(unittest.TestCase):
    def setUp(self):
        self.addCleanup(setattr, B, "ThreadingHTTPServer", B.ThreadingHTTPServer)
        self.addCleanup(setattr, B.Handler, "status_file", B.Handler.status_file)
        self.addCleanup(setattr, B.Handler, "ui_url", B.Handler.ui_url)
        self.bound = []
        bound = self.bound

        class Stub:
            def __init__(self, address, handler):
                bound.append(address)

            def serve_forever(self):
                raise KeyboardInterrupt

            def server_close(self):
                bound.append("closed")

        B.ThreadingHTTPServer = Stub

    def test_main_binds_loopback_8079_by_default_and_closes_on_ctrl_c(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(B.main(["--status-file", "/tmp/s", "--ui-url", "http://127.0.0.1:8080/"]), 0)
        self.assertEqual(self.bound, [("127.0.0.1", 8079), "closed"])
        self.assertEqual(B.Handler.status_file, "/tmp/s")
        self.assertIn("http://127.0.0.1:8079/", err.getvalue())

    def test_a_ui_url_that_could_close_the_script_is_refused_before_binding(self):
        with self.assertRaises(ValueError):
            B.main(["--ui-url", "http://x/</script><script>alert(1)//"])
        self.assertEqual(self.bound, [])

    def test_never_imports_linuxcnc_or_hal(self):
        """It must outlive every LinuxCNC run; the UI dies with each one."""
        src = read("ui/bettercnc/bootpage.py")
        self.assertIsNone(re.search(r"^\s*(import|from)\s+(linuxcnc|hal)\b", src, re.M))
        self.assertIsNone(re.search(r"^from \.(adapter|app)\b", src, re.M))


# ---------------------------------------------------------------------------
# bettercnc.browserwatch
# ---------------------------------------------------------------------------

from bettercnc import browserwatch as W  # noqa: E402


class _Proc:
    """A browser process: alive for `polls` checks, then exited."""

    def __init__(self, polls=None, code=0):
        self.polls, self.code = polls, code
        self.returncode = None
        self.terminated = False

    def poll(self):
        if self.returncode is not None:
            return self.returncode
        if self.polls is not None:
            if self.polls <= 0:
                self.returncode = self.code
                return self.code
            self.polls -= 1
        return None

    def terminate(self):
        self.terminated = True
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class TestScreen(unittest.TestCase):
    """browserwatch.Screen: Chromium's own pages kept off the screen over
    the DevTools pipe, with the browser-level Target commands the kiosk
    policy still allows (sim/, 2026-10-20)."""

    UI = "http://127.0.0.1:8080/"
    BOOT = "http://127.0.0.1:8079/"

    def setUp(self):
        self.now = [100.0]
        self.cmd_r, cmd_w = os.pipe()
        ev_r, self.ev_w = os.pipe()
        self.up = {self.UI: True, self.BOOT: True}   # which server answers its /healthz
        self.screen = W.Screen(cmd_w, ev_r, self.UI, self.BOOT, clock=lambda: self.now[0],
                               answers=lambda url: self.up[url])
        self.addCleanup(self.screen.close)
        self.addCleanup(os.close, self.cmd_r)
        self.addCleanup(lambda: os.close(self.ev_w) if self.ev_w is not None else None)
        self.sent = []

        def record(method, params=None):
            if method != "Target.getTargets":   # asked on every look
                self.sent.append((method, params or {}))
            return len(self.sent)

        self.screen.send = record

    def tab(self, tid, url, title, method="Target.targetCreated"):
        self.screen.handle({"method": method, "params": {"targetInfo": {
            "targetId": tid, "type": "page", "url": url, "title": title}}})

    def crash(self, tid):
        self.screen.handle({"method": "Target.targetCrashed",
                            "params": {"targetId": tid, "status": "killed"}})

    def test_a_crashed_tab_with_nobody_answering_gets_the_wait_page(self):
        self.tab("ui", self.UI, "better-cnc · Test tezgahı")
        self.assertEqual(self.screen.guard(False, False), [])
        self.crash("ui")
        self.assertEqual(self.screen.guard(False, False), [
            "bekleme ekranı açıldı",
            "kapatıldı: çöken sekme ('http://127.0.0.1:8080/', 'better-cnc · Test tezgahı')"])
        self.assertEqual(self.sent, [("Target.createTarget", {"url": W.WAIT_URL}),
                                     ("Target.closeTarget", {"targetId": "ui"})])

    def test_a_crashed_tab_goes_straight_back_to_the_ui_when_it_answers(self):
        """sim/: through the wait page and the boot screen it showed the
        boot screen's last, stale line ("başlatılıyor…") on the way."""
        self.tab("ui", self.UI, "better-cnc · Test tezgahı")
        self.crash("ui")
        self.assertEqual(self.screen.guard(True, True)[0], "arayüz açıldı")
        self.assertEqual(self.sent[0], ("Target.createTarget", {"url": self.UI}))

    def test_else_to_the_boot_screen(self):
        self.tab("b", self.BOOT, "better-cnc · başlatılıyor")
        self.crash("b")
        self.assertEqual(self.screen.guard(False, True)[0], "başlatma ekranı açıldı")
        self.assertEqual(self.sent[0], ("Target.createTarget", {"url": self.BOOT}))

    def test_a_crash_wakes_the_guard_at_once(self):
        self.tab("ui", self.UI, "better-cnc · x")
        t0 = __import__("time").monotonic()
        threading.Timer(0.05, self.crash, ("ui",)).start()
        self.screen.wait(5.0)
        self.assertLess(__import__("time").monotonic() - t0, 2.0)
        self.screen.wait(0.01)   # cleared: a later wait sleeps its time out

    def test_an_error_page_on_a_server_that_is_down_goes_at_once(self):
        """Chromium's connection error keeps the URL, takes the host as title.
        sim/: judged by time alone it stayed on the screen ~1.5 s."""
        self.up[self.BOOT] = False
        self.tab("b", self.BOOT, "127.0.0.1")
        self.assertEqual(self.screen.guard(False, False), [
            "bekleme ekranı açıldı",
            "kapatıldı: hata sayfası ('http://127.0.0.1:8079/', '127.0.0.1')"])

    def test_a_page_not_ours_on_a_server_that_answers_goes_after_not_ours_s(self):
        """It may be ours, loading; if it stays, it is not."""
        self.tab("ui", self.UI, "127.0.0.1")
        self.assertEqual(self.screen.guard(False, True), [])
        self.now[0] += W.NOT_OURS_S - 0.1
        self.assertEqual(self.screen.guard(False, True), [])
        self.now[0] += 0.2
        self.assertEqual(self.screen.guard(False, True), [
            "başlatma ekranı açıldı",
            "kapatıldı: bizim olmayan sayfa ('http://127.0.0.1:8080/', '127.0.0.1')"])

    def test_servers_are_asked_only_about_a_tab_that_is_not_ours(self):
        asked = []
        self.screen._answers = lambda url: asked.append(url) or True
        self.tab("ui", self.UI, "better-cnc · x")
        self.tab("w", W.WAIT_URL, "wait.html")
        self.screen.guard(True, True)
        self.assertEqual(asked, [])
        self.tab("x", "http://example.com/", "Blocked")
        self.screen.guard(True, True)
        self.assertEqual(asked, [])                  # not one of our servers
        self.tab("b", self.BOOT, "127.0.0.1:8079")
        self.screen.guard(True, True)
        self.assertEqual(asked, [self.BOOT])

    def test_a_page_of_ours_that_is_loading_is_left_alone(self):
        self.tab("ui", self.UI, "127.0.0.1:8080")
        self.now[0] += 0.5
        self.screen.guard(True, True)
        self.tab("ui", self.UI, "better-cnc · Test tezgahı", "Target.targetInfoChanged")
        self.now[0] += 5
        self.assertEqual(self.screen.guard(True, True), [])
        self.assertEqual(self.sent, [])

    def test_a_stray_tab_is_closed_and_ours_kept(self):
        """Ctrl+T past the keymap opens a tab; if it is not ours it goes,
        and no replacement is needed while one of ours is there."""
        self.tab("ui", self.UI, "better-cnc · Test tezgahı")
        self.tab("x", "http://example.com/", "Blocked")
        self.screen.guard(True, True)            # first seen: the clock starts
        self.now[0] += W.NOT_OURS_S
        self.screen.guard(True, True)
        self.assertEqual(self.sent, [("Target.closeTarget", {"targetId": "x"})])

    def test_the_wait_page_gives_way_to_whichever_server_answers_first(self):
        self.tab("w", W.WAIT_URL, "better-cnc · bekleyin")
        self.assertEqual(self.screen.guard(False, False), [])
        self.assertEqual(self.screen.guard(False, True), ["başlatma ekranına dönüldü"])
        self.assertEqual(self.sent, [("Target.createTarget", {"url": self.BOOT}),
                                     ("Target.closeTarget", {"targetId": "w"})])
        self.sent.clear()
        self.assertEqual(self.screen.guard(True, True), ["arayüze dönüldü"])
        self.assertEqual(self.sent[0], ("Target.createTarget", {"url": self.UI}))

    def test_chromium_internal_tabs_and_closed_tabs_are_ignored(self):
        self.tab("o", "chrome://omnibox-popup.top-chrome/", "chrome://omnibox-popup")
        self.tab("ui", self.UI, "better-cnc · Test tezgahı")
        self.tab("gone", "http://example.com/", "x")
        self.screen.handle({"method": "Target.targetDestroyed", "params": {"targetId": "gone"}})
        self.now[0] += 10
        self.assertEqual(self.screen.guard(True, True), [])

    def test_titles_come_from_the_tab_list_not_the_events(self):
        """In sim/ the discovery events never reported a loaded page's real
        title; Target.getTargets does."""
        self.tab("ui", self.UI, "127.0.0.1:8080")       # the event's stale title
        self.screen._asked.add(77)
        self.screen.handle({"id": 77, "result": {"targetInfos": [
            {"targetId": "ui", "type": "page", "url": self.UI, "title": "better-cnc · Test tezgahı"},
            {"targetId": "sw", "type": "service_worker", "url": self.UI, "title": ""}]}})
        self.now[0] += 10
        self.assertEqual(self.screen.guard(True, True), [])
        self.assertEqual(list(self.screen._tabs), ["ui"])

    def test_the_tab_list_keeps_a_crash_already_seen(self):
        self.tab("ui", self.UI, "better-cnc · x")
        self.crash("ui")
        self.screen._asked.add(5)
        self.screen.handle({"id": 5, "result": {"targetInfos": [
            {"targetId": "ui", "type": "page", "url": self.UI, "title": "better-cnc · x"}]}})
        self.assertIn("arayüz açıldı", self.screen.guard(True, True))

    def test_the_wait_page_is_never_judged_by_its_title(self):
        """In sim/ its title read "wait.html" while loading; judged by title,
        it was replaced by itself every 2 s."""
        self.tab("w", W.WAIT_URL, "wait.html")
        self.now[0] += 10
        self.assertEqual(self.screen.guard(False, False), [])

    def test_at_most_one_replacement_per_replace_again_s(self):
        self.tab("a", self.UI, "127.0.0.1")
        self.screen.guard(False, False)
        self.now[0] += W.NOT_OURS_S
        self.assertIn("bekleme ekranı açıldı", self.screen.guard(False, False))
        self.tab("b", self.UI, "127.0.0.1")              # say the wait tab is not listed yet
        self.screen.handle({"method": "Target.targetDestroyed", "params": {"targetId": "a"}})
        self.screen.guard(False, False)
        self.now[0] += W.NOT_OURS_S
        self.assertEqual(self.screen.guard(False, False), [])
        self.now[0] += W.REPLACE_AGAIN_S
        self.assertIn("bekleme ekranı açıldı", self.screen.guard(False, False))

    def test_the_wait_page_is_ours_and_needs_no_server(self):
        page = read("deploy/files/wait.html")
        self.assertIn("<title>better-cnc · bekleyin</title>", page)
        self.assertTrue(("<title>" + W.TITLE_MARK).replace(" ", "") in page.replace(" ", ""))
        self.assertIn("Bir hata oluştu — lütfen bekleyin.", page)
        self.assertNotIn("<script", page)
        self.assertNotRegex(page, r'(src|href)="(?!#)')
        self.assertIn('install -D -m 0644 "$FILES/wait.html" "$WAIT_PATH"', read("deploy/kiosk.sh"))

    def test_over_the_real_pipe(self):
        """Framing as Chromium's --remote-debugging-pipe: JSON, NUL-ended."""
        del self.screen.send  # the real one again
        events = [
            {"method": "Target.targetCreated", "params": {"targetInfo": {
                "targetId": "t1", "type": "page", "url": self.UI, "title": "better-cnc · x"}}},
            {"method": "Target.targetCrashed", "params": {"targetId": "t1", "status": "killed"}},
        ]
        os.write(self.ev_w, b"".join(json.dumps(e).encode() + b"\0" for e in events))
        for _ in range(100):
            self.screen.wait(0.01)
            if self.screen.guard(False, False):
                break
        os.close(self.ev_w)
        self.ev_w = None
        out = os.read(self.cmd_r, 65536).split(b"\0")
        methods = [json.loads(m)["method"] for m in out if m]
        self.assertEqual([m for m in methods if m != "Target.getTargets"],
                         ["Target.setDiscoverTargets", "Target.createTarget", "Target.closeTarget"])
        self.assertIn("Target.getTargets", methods)


class TestScreenPipeFaults(unittest.TestCase):
    """The browser going away mid-session must not take the watch with it."""

    def test_bad_json_is_skipped_and_a_closed_browser_is_survived(self):
        cmd_r, cmd_w = os.pipe()
        ev_r, ev_w = os.pipe()
        screen = W.Screen(cmd_w, ev_r, "http://127.0.0.1:8080/", "http://127.0.0.1:8079/",
                          answers=lambda url: True)
        os.write(ev_w, b"{not json\0" + json.dumps({"method": "Target.targetCreated", "params": {
            "targetInfo": {"targetId": "t", "type": "page", "url": "x", "title": "y"}}}).encode()
                 + b"\0")
        for _ in range(200):
            if screen._tabs:
                break
            __import__("time").sleep(0.005)
        self.assertEqual(list(screen._tabs), ["t"])
        os.close(ev_w)                       # the browser exits: the reader ends
        screen._reader.join(timeout=2)
        self.assertFalse(screen._reader.is_alive())
        os.close(cmd_r)                      # nobody reads the commands any more
        screen.guard(True, True)             # EPIPE on the write: swallowed
        screen.close()
        screen.close()                       # twice: harmless

    def test_a_read_error_ends_the_reader(self):
        cmd_r, cmd_w = os.pipe()
        ev_r, ev_w = os.pipe()
        os.close(ev_r)                       # reading a closed fd: OSError
        screen = W.Screen(cmd_w, ev_r, "http://a/", "http://b/", answers=lambda url: True)
        screen._reader.join(timeout=2)
        self.assertFalse(screen._reader.is_alive())
        for fd in (cmd_r, cmd_w, ev_w):
            os.close(fd)


class TestKioskBrowserPipe(unittest.TestCase):
    def test_kiosk_browser_moves_the_pipe_to_3_and_4(self):
        script = read("deploy/kiosk-browser")
        self.assertIn('exec 3<&"$cdp_in" 4>&"$cdp_out" {cdp_in}<&- {cdp_out}>&-', script)
        self.assertIn("pipe=(--remote-debugging-pipe)", script)

    def test_the_child_ends_are_above_20(self):
        r, w = os.pipe()
        moved = W._above(r, 20)
        self.addCleanup(os.close, moved)
        self.addCleanup(os.close, w)
        self.assertGreaterEqual(moved, 20)


class TestPageIsDead(unittest.TestCase):
    def test_grace_after_a_start(self):
        self.assertFalse(W.page_is_dead(W.NEVER, W.NEVER, W.GRACE_S - 1, 0))
        self.assertTrue(W.page_is_dead(W.NEVER, W.NEVER, W.GRACE_S, 0))

    def test_any_recent_contact_is_life(self):
        """The page is on one server at a time: the UI or the boot screen."""
        self.assertFalse(W.page_is_dead(W.NEVER, 1.0, 100, 0))
        self.assertFalse(W.page_is_dead(W.STALE_S, None, 100, 0))
        self.assertTrue(W.page_is_dead(W.STALE_S + 0.1, 50.0, 100, 0))

    def test_a_page_on_a_ui_that_went_away_gets_time_to_reach_the_boot_screen(self):
        """Found in sim/ 2026-10-20: the first version restarted the browser
        on every UI restart - the page was alive, covering the screen, with
        no one to ask, and the boot screen had not heard from it in minutes."""
        self.assertFalse(W.page_is_dead(None, 600.0, 100, W.BOOT_HANDOFF_S))
        self.assertTrue(W.page_is_dead(None, 600.0, 100, W.BOOT_HANDOFF_S + 0.1))
        # it made it to the boot screen: alive however long the UI is away
        self.assertFalse(W.page_is_dead(None, 1.0, 100, 600))

    def test_with_nobody_reachable_nothing_is_restarted(self):
        self.assertFalse(W.page_is_dead(None, None, 100, 600))

    def test_handoff_covers_app_js_boot_redirect(self):
        js = read("ui/bettercnc/static/app.js")
        boot_ms = int(re.search(r"var BOOT_MS = (\d+);", js).group(1))
        self.assertGreater(W.BOOT_HANDOFF_S, boot_ms / 1000.0)


class TestWatch(unittest.TestCase):
    def run_watch(self, procs, answers, starts):
        """`answers`: per check, (ui_age, boot_age); after they run out the
        page answers the UI every second."""
        made = list(procs)
        clock = [0.0]

        def sleep(s):
            clock[0] += s

        seq = list(answers)
        current = [None]

        def ask(url):
            if url == "ui":
                current[0] = seq.pop(0) if seq else (1.0, W.NEVER)
                return current[0][0]
            return current[0][1]

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = W.watch(["browser"], "ui", "boot", start=lambda cmd: (made.pop(0), None),
                        ask=ask, clock=lambda: clock[0], sleep=sleep, starts=starts)
        return n, err.getvalue()

    def test_an_exited_browser_is_restarted(self):
        first, second = _Proc(polls=0, code=1), _Proc(polls=0)
        n, err = self.run_watch([first, second], [], starts=2)
        self.assertEqual(n, 2)
        self.assertIn("tarayıcı kapandı (çıkış kodu 1)", err)

    def test_a_dead_page_is_restarted_but_not_during_grace(self):
        """The renderer crashed: the process lives, nothing asks any server."""
        proc = _Proc()
        checks = int(W.GRACE_S / W.CHECK_EVERY_S) + 2
        n, err = self.run_watch([proc], [(W.NEVER, W.NEVER)] * checks, starts=1)
        self.assertTrue(proc.terminated)
        self.assertIn("hiçbir sayfayı sormadı", err)

    def test_a_ui_restart_leaves_the_browser_alone(self):
        """LinuxCNC + UI restart: the UI is away ~12 s, the page reaches the
        boot screen ~11 s after the UI went, then the UI is back."""
        checks = int(W.GRACE_S / W.CHECK_EVERY_S) + 1
        answers = [(1.0, W.NEVER)] * checks          # normal running
        answers += [(None, 600.0)] * 5               # UI away, page still on it
        answers += [(None, 1.0)] * 2                 # page on the boot screen
        answers += [(1.0, 5.0)] * 5                  # UI back, page back on it
        # one poll per GUARD_EVERY_S, one ask per CHECK_EVERY_S
        proc = _Proc(polls=int(len(answers) * W.CHECK_EVERY_S / W.GUARD_EVERY_S) + 4)
        n, err = self.run_watch([proc], answers, starts=1)
        self.assertFalse(proc.terminated, err)
        self.assertNotIn("sormadı", err)

    def test_a_page_that_dies_while_the_ui_is_away_is_restarted(self):
        proc = _Proc()
        checks = int(W.GRACE_S / W.CHECK_EVERY_S) + 1
        answers = [(1.0, W.NEVER)] * checks + [(None, 600.0)] * 12
        n, err = self.run_watch([proc], answers, starts=1)
        self.assertTrue(proc.terminated)

    def test_a_live_page_is_left_alone(self):
        proc = _Proc(polls=40)
        n, err = self.run_watch([proc], [], starts=1)  # the UI hears from it every 1 s
        self.assertFalse(proc.terminated)
        self.assertNotIn("sormadı", err)

    def test_the_screen_guard_hears_which_server_answers(self):
        clock = [0.0]

        class FakeScreen:
            calls, closed = [], False

            def wait(self, s):          # instead of sleep while a screen is there
                clock[0] += s

            def guard(self, ui_up, boot_up):
                self.calls.append((ui_up, boot_up))
                return ["arayüz açıldı"] if len(self.calls) == 3 else []

            def close(self):
                FakeScreen.closed = True

        screen = FakeScreen()
        answers = iter([None, 1.0, 1.0, 1.0] + [1.0, 1.0] * 20)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            W.watch(["b"], "ui", "boot", start=lambda cmd: (_Proc(polls=10), screen),
                    ask=lambda url: next(answers), clock=lambda: clock[0],
                    sleep=lambda s: clock.__setitem__(0, clock[0] + s), starts=1)
        # asked at the first look, then every 2 s: UI away / boot up, then both
        self.assertEqual(screen.calls[:4], [(False, True)] * 4)
        self.assertEqual(screen.calls[4], (True, True))
        self.assertEqual(screen.calls[-1], (True, True))
        self.assertIn("browserwatch: arayüz açıldı", err.getvalue())
        self.assertTrue(FakeScreen.closed)

    def test_client_age_reads_healthz(self):
        def serve(body):
            handler = type("H", (_Ui,), {"answer": (200, body)})
            httpd, url = _server(handler)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            return url + "healthz"

        self.assertEqual(W.client_age(serve(b'{"ok": true, "client_age_s": 3.5}')), 3.5)
        self.assertEqual(W.client_age(serve(b'{"ok": true, "client_age_s": null}')), W.NEVER)
        self.assertIsNone(W.client_age(serve(b"not json")))
        httpd, url = _server(_Ui)
        httpd.shutdown()
        httpd.server_close()
        self.assertIsNone(W.client_age(url + "healthz", timeout=0.2))

    def test_client_age_refuses_anything_but_ok_true(self):
        for body in (b'{"ok": false, "client_age_s": 1}', b"[1, 2]"):
            handler = type("H", (_Ui,), {"answer": (200, body)})
            httpd, url = _server(handler)
            self.addCleanup(httpd.server_close)
            self.addCleanup(httpd.shutdown)
            with self.subTest(body=body):
                self.assertIsNone(W.client_age(url + "healthz"))

    def test_stop_kills_a_browser_that_ignores_sigterm(self):
        class Stubborn(_Proc):
            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                if timeout is not None and self.returncode is None:
                    raise __import__("subprocess").TimeoutExpired("browser", timeout)
                return self.returncode

        proc = Stubborn()
        W.stop(proc, wait_s=0.01)
        self.assertTrue(proc.terminated)
        self.assertEqual(proc.returncode, -9)

    def test_main_starts_the_command_it_was_given(self):
        import signal as _signal
        self.addCleanup(_signal.signal, _signal.SIGTERM, _signal.getsignal(_signal.SIGTERM))
        seen = {}

        def fake_watch(command, ui, boot, start):
            seen["args"] = (command, ui, boot)
            proc, screen = start([sys.executable, "-c",
                                  "import os; print(os.environ['BETTERCNC_CDP_FDS'])"])
            seen["code"] = proc.wait(timeout=10)
            screen.close()
            return 1

        self.addCleanup(setattr, W, "watch", W.watch)
        W.watch = fake_watch
        W.main(["--ui-health", "http://u/healthz", "--boot-health", "http://b/healthz",
                "--", "kiosk-browser", "http://127.0.0.1:8079/"])
        self.assertEqual(seen["args"], (["kiosk-browser", "http://127.0.0.1:8079/"],
                                        "http://u/healthz", "http://b/healthz"))
        self.assertEqual(seen["code"], 0)

    def test_the_browser_starts_on_the_wait_page_while_the_boot_screen_is_down(self):
        import signal as _signal
        self.addCleanup(_signal.signal, _signal.SIGTERM, _signal.getsignal(_signal.SIGTERM))
        self.addCleanup(setattr, W, "watch", W.watch)
        self.addCleanup(setattr, W, "client_age", W.client_age)
        self.addCleanup(setattr, W, "start_browser", W.start_browser)
        self.addCleanup(setattr, W, "BOOT_WAIT_S", W.BOOT_WAIT_S)
        W.BOOT_WAIT_S = 0.05
        started = []
        W.start_browser = lambda cmd, ui, boot: (started.append(cmd), (None, None))[1]
        W.watch = lambda command, ui, boot, start: (start(command), start(command))
        boot = "http://127.0.0.1:8079/"
        args = ["--ui-health", "http://127.0.0.1:8080/healthz",
                "--boot-health", boot + "healthz", "--", "kiosk-browser", boot]
        W.client_age = lambda url: 1.0 if started else None   # up after the first start
        W.main(args)
        self.assertEqual(started, [["kiosk-browser", W.WAIT_URL], ["kiosk-browser", boot]])

    def test_answers_within_waits_for_a_server_that_is_starting(self):
        self.addCleanup(setattr, W, "client_age", W.client_age)
        answers = iter([None, None, 3.0])
        W.client_age = lambda url: next(answers)
        self.assertTrue(W.answers_within("u", 5.0, every_s=0.01))
        W.client_age = lambda url: None
        self.assertFalse(W.answers_within("u", 0.03, every_s=0.01))

    def test_sigterm_takes_the_browser_down_too(self):
        """The kiosk stopping (systemd SIGTERM) must not leave a browser behind."""
        import signal as _signal
        self.addCleanup(_signal.signal, _signal.SIGTERM, _signal.getsignal(_signal.SIGTERM))
        seen = {}

        def fake_watch(command, ui, boot, start):
            proc, screen = start([sys.executable, "-c", "import time; time.sleep(30)"])
            handler = _signal.getsignal(_signal.SIGTERM)
            with self.assertRaises(SystemExit) as ctx:
                handler(_signal.SIGTERM, None)
            seen["exit"] = ctx.exception.code
            seen["alive"] = proc.poll() is None
            screen.close()
            return 1

        self.addCleanup(setattr, W, "watch", W.watch)
        W.watch = fake_watch
        W.main(["--ui-health", "u", "--boot-health", "b", "--", "x"])
        self.assertEqual(seen, {"exit": 0, "alive": False})

    def test_main_needs_a_command_and_both_servers(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                W.main(["--ui-health", "http://x/healthz", "--boot-health", "http://y/healthz"])
            with self.assertRaises(SystemExit):
                W.main(["--ui-health", "http://x/healthz", "--", "browser"])

    def test_its_timings_are_the_ones_deploy_readme_explains(self):
        """Every browserwatch timing that shapes what the operator sees has a
        row in deploy/README.md "Süreler"; a new one must get one."""
        readme = read("deploy/README.md")
        table = readme[readme.index("## Süreler"):readme.index("## Simülatörde denemek")]
        shown = {"STALE_S": "10 s", "BOOT_HANDOFF_S": "15 s", "GRACE_S": "30 s",
                 "CHECK_EVERY_S": "2 s", "GUARD_EVERY_S": "0,5 s", "NOT_OURS_S": "1,5 s",
                 "REPLACE_AGAIN_S": "5 s", "BOOT_WAIT_S": "5 s"}
        for name, value in shown.items():
            with self.subTest(name=name):
                row = next(r for r in table.splitlines() if r.startswith("| `{}`".format(name)))
                self.assertIn("| {} |".format(value), row)
                self.assertEqual(float(value.split()[0].replace(",", ".")), getattr(W, name))

    def test_never_imports_linuxcnc_or_hal(self):
        src = read("ui/bettercnc/browserwatch.py")
        self.assertIsNone(re.search(r"^\s*(import|from)\s+(linuxcnc|hal)\b", src, re.M))


# ---------------------------------------------------------------------------
# The rules deploy/README.md states, as the scripts hold them
# ---------------------------------------------------------------------------


class TestKioskRulesInTheScripts(unittest.TestCase):
    def test_cage_never_allows_vt_switching(self):
        """cage switches VTs only with -s: without it Ctrl+Alt+Fn cannot leave
        the kiosk for a console."""
        unit = read("deploy/files/bettercnc-kiosk.service.in")
        exec_line = re.search(r"^ExecStart=(.*)$", unit, re.M).group(1)
        self.assertTrue(exec_line.startswith("/usr/bin/cage "))
        self.assertNotRegex(exec_line.split(" -- ")[0], r"\s-s\b")
        self.assertIn("Restart=always", unit)
        self.assertIn("User=@USER@", unit)

    def test_the_ways_out_are_in_the_unit(self):
        unit = read("deploy/files/bettercnc-kiosk.service.in")
        self.assertIn("ConditionKernelCommandLine=!bettercnc.kiosk=0", unit)
        self.assertIn("ConditionPathExists=!/etc/bettercnc/kiosk-disabled", unit)

    def test_browser_reaches_only_the_boot_screen_and_the_ui(self):
        policy = json.loads(read("deploy/files/chromium-policy.json"))
        self.assertEqual(policy["URLBlocklist"][0], "*")
        for scheme in ("chrome://*", "devtools://*", "file://*", "view-source:*"):
            self.assertIn(scheme, policy["URLBlocklist"])
        # A new tab, a new window or Home lands on our boot screen, and the
        # hidden address bar cannot search.
        for key in ("NewTabPageLocation", "HomepageLocation"):
            self.assertEqual(policy[key], "http://127.0.0.1:8079/")
        self.assertIs(policy["DefaultSearchProviderEnabled"], False)
        self.assertEqual(policy["DefaultPopupsSetting"], 2)
        self.assertEqual(
            sorted(policy["URLAllowlist"]),
            ["file:///usr/share/bettercnc/wait.html", "http://127.0.0.1:8079", "http://127.0.0.1:8080"],
        )
        self.assertEqual(W.WAIT_URL, "file:///usr/share/bettercnc/wait.html")
        self.assertEqual(policy["DeveloperToolsAvailability"], 2)
        session = read("deploy/kiosk-session")
        self.assertIn('BOOT_PORT="${BETTERCNC_BOOT_PORT:-8079}"', session)
        self.assertIn('BETTERCNC_UI_PORT="${BETTERCNC_UI_PORT:-8080}"', session)
        self.assertIn("--kiosk", read("deploy/kiosk-browser"))

    def test_the_ui_starts_after_linuxcnc_as_its_display(self):
        display = read("deploy/bettercnc-display")
        self.assertIn("python3 -c 'import linuxcnc; linuxcnc.stat().poll()'", display)
        self.assertIn("--adapter linuxcnc --host 127.0.0.1", display)
        self.assertIn("--boot-url", display)
        self.assertIn("exec python3 -m bettercnc.app", display)

    def test_no_page_is_offered_for_translation(self):
        """In sim/ the kiosk's Chromium offered to translate the boot screen
        into English. Pages say translate="no"; the browser runs in Turkish
        and the policy turns translation off (TranslateEnabled)."""
        from bettercnc import render as R
        from helpers import view

        self.assertIn('<html lang="tr" translate="no">', B.render("x", "http://127.0.0.1:8080/"))
        page = R.render_page(view("test-rig", "kontrol"), "", "")
        self.assertIn('<html lang="tr" translate="no">', page)
        self.assertIn("--lang=tr", read("deploy/kiosk-browser"))
        self.assertIs(json.loads(read("deploy/files/chromium-policy.json"))["TranslateEnabled"], False)

    def test_ssh_is_key_only(self):
        boot = read("deploy/bootstrap-ssh.sh")
        self.assertIn("PasswordAuthentication no", boot)
        self.assertIn("KbdInteractiveAuthentication no", boot)
        self.assertIn("PermitRootLogin no", boot)

    def test_board_link_never_takes_the_default_route(self):
        self.assertIn("ipv4.never-default yes", read("deploy/kiosk.sh"))


# ---------------------------------------------------------------------------
# The kiosk keyboard (deploy/files/xkb), through libxkbcommon itself
# ---------------------------------------------------------------------------

import ctypes  # noqa: E402
import ctypes.util  # noqa: E402

_XKB = ctypes.util.find_library("xkbcommon")


@unittest.skipUnless(_XKB, "libxkbcommon not installed (it is in sim/'s image: sim/sim.sh test)")
class TestKioskKeymap(unittest.TestCase):
    """The keymap cage builds for the kiosk (XKB_CONFIG_EXTRA_PATH,
    XKB_DEFAULT_LAYOUT, XKB_DEFAULT_OPTIONS from the service), compiled and
    pressed through libxkbcommon: no browser shortcut can be typed, MDI can."""

    EFFECTIVE = 1 << 3  # XKB_STATE_MODS_EFFECTIVE
    VOID = 0xFFFFFF     # VoidSymbol
    # evdev codes (linux/input-event-codes.h); xkb keycode = evdev + 8
    KEY = dict(LCTRL=29, RCTRL=97, LALT=56, RALT=100, LMETA=125, RMETA=126,
               COMPOSE=127, A=30, G=34, N0=11, N3=4, N8=9, N9=10, SPACE=57, TAB=15,
               ENTER=28, BACKSPACE=14, F1=59, F5=63, F11=87, F12=88,
               BACK=158, FORWARD=159, REFRESH=173, HOMEPAGE=172, SEARCH=217)

    class Names(ctypes.Structure):
        _fields_ = [(n, ctypes.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]

    @classmethod
    def setUpClass(cls):
        lib = ctypes.CDLL(_XKB)
        lib.xkb_context_new.restype = ctypes.c_void_p
        lib.xkb_context_new.argtypes = [ctypes.c_int]
        lib.xkb_keymap_new_from_names.restype = ctypes.c_void_p
        lib.xkb_keymap_new_from_names.argtypes = [ctypes.c_void_p, ctypes.POINTER(cls.Names), ctypes.c_int]
        lib.xkb_state_new.restype = ctypes.c_void_p
        lib.xkb_state_new.argtypes = [ctypes.c_void_p]
        lib.xkb_state_update_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int]
        lib.xkb_state_mod_name_is_active.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.xkb_state_key_get_utf8.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t]
        lib.xkb_state_key_get_one_sym.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        lib.xkb_state_key_get_one_sym.restype = ctypes.c_uint32
        cls.lib = lib
        cls._saved = os.environ.get("XKB_CONFIG_EXTRA_PATH")
        # As the service sets it; read when a context is created.
        os.environ["XKB_CONFIG_EXTRA_PATH"] = os.path.join(DEPLOY, "files", "xkb")

    @classmethod
    def tearDownClass(cls):
        if cls._saved is None:
            os.environ.pop("XKB_CONFIG_EXTRA_PATH", None)
        else:
            os.environ["XKB_CONFIG_EXTRA_PATH"] = cls._saved

    def state(self, layout=b"tr", options=b"bettercnc:kiosk"):
        lib = self.lib
        ctx = lib.xkb_context_new(0)
        names = self.Names(b"evdev", b"pc105", layout, b"", options)
        km = lib.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
        self.assertTrue(km, "keymap {} {} did not compile".format(layout, options))
        return lib.xkb_state_new(km)

    def mods_after(self, st, key):
        self.lib.xkb_state_update_key(st, self.KEY[key] + 8, 1)
        active = {m for m in (b"Control", b"Mod1", b"Mod4", b"Shift")
                  if self.lib.xkb_state_mod_name_is_active(st, m, self.EFFECTIVE) == 1}
        self.lib.xkb_state_update_key(st, self.KEY[key] + 8, 0)
        return active

    def typed(self, st, key, with_key=None):
        if with_key:
            self.lib.xkb_state_update_key(st, self.KEY[with_key] + 8, 1)
        buf = ctypes.create_string_buffer(16)
        self.lib.xkb_state_key_get_utf8(st, self.KEY[key] + 8, buf, 16)
        if with_key:
            self.lib.xkb_state_update_key(st, self.KEY[with_key] + 8, 0)
        return buf.value.decode("utf-8")

    def test_the_premise_without_the_option_ctrl_is_ctrl(self):
        st = self.state(options=b"")
        self.assertEqual(self.mods_after(st, "LCTRL"), {b"Control"})
        self.assertNotEqual(self.lib.xkb_state_key_get_one_sym(st, self.KEY["F5"] + 8), self.VOID)

    def test_no_modifier_a_browser_shortcut_needs(self):
        for layout in (b"tr", b"us"):
            st = self.state(layout)
            for key in ("LCTRL", "RCTRL", "LALT", "RALT", "LMETA", "RMETA", "COMPOSE"):
                with self.subTest(layout=layout, key=key):
                    self.assertEqual(self.mods_after(st, key), set())

    def test_function_and_browser_keys_produce_nothing(self):
        st = self.state()
        for key in ("TAB", "F1", "F5", "F11", "F12", "BACK", "FORWARD", "REFRESH", "HOMEPAGE", "SEARCH"):
            with self.subTest(key=key):
                self.assertEqual(self.lib.xkb_state_key_get_one_sym(st, self.KEY[key] + 8), self.VOID)

    def test_mdi_still_types(self):
        """G-code: letters, digits, space, and on a Turkish keyboard the
        # [ ] of parameters and expressions through AltGr."""
        st = self.state()
        self.assertEqual(self.typed(st, "G"), "g")
        self.assertEqual(self.typed(st, "N0"), "0")
        self.assertEqual(self.typed(st, "SPACE"), " ")
        self.assertEqual(self.typed(st, "N3", with_key="RALT"), "#")
        self.assertEqual(self.typed(st, "N8", with_key="RALT"), "[")
        self.assertEqual(self.typed(st, "N9", with_key="RALT"), "]")
        self.assertEqual(self.lib.xkb_state_key_get_one_sym(st, self.KEY["ENTER"] + 8), 0xFF0D)
        self.assertEqual(self.lib.xkb_state_key_get_one_sym(st, self.KEY["BACKSPACE"] + 8), 0xFF08)


class TestKioskLayersInThePages(unittest.TestCase):
    """The page's own layer under the keymap, and the browser policy."""

    def test_app_js_blocks_what_a_page_may_block_in_kiosk_mode_only(self):
        js = read("ui/bettercnc/static/app.js")
        block = js[js.index("if (bootUrl) {"):js.index("setInterval(refresh")]
        for needle in ('"contextmenu"', '"dragstart"', '"auxclick"', "ev.button === 3",
                       "ev.ctrlKey || ev.metaKey", "/^F\\d{1,2}$/", 'classList.add("kiosk")',
                       'ev.key === "Tab"', "noTooltips(document)"):
            with self.subTest(needle=needle):
                self.assertIn(needle, block)
        # and again after every re-render, which brings the title attributes back
        swap = js[js.index("function swap(data)"):js.index("function refresh()")]
        self.assertIn("if (bootUrl) noTooltips(app);", swap)
        self.assertIn("body.kiosk {", read("ui/bettercnc/static/app.css"))

    def test_a_clicked_field_still_looks_active_and_shows_its_caret(self):
        """The user (2026-10-20): a field clicked with the mouse must show it
        is active, and the blinking caret stays - desktop programs have both;
        only what gives a browser away goes."""
        css = read("ui/bettercnc/static/app.css")
        kiosk = css[css.index("---- kiosk"):]
        self.assertRegex(css, r"\.mdi-input:focus \{[^}]*border-color: var\(--acc-deep\)")
        self.assertNotRegex(css, r"caret-color\s*:")      # the caret is never hidden
        focus = re.findall(r"body\.kiosk :focus[^{]*\{([^}]*)\}", kiosk)
        self.assertEqual([f.strip() for f in focus], ["outline: none;"])
        self.assertIn("body.kiosk input { cursor: text; }", kiosk)

    def test_the_boot_screen_blocks_the_same(self):
        page = B.render("x", "http://127.0.0.1:8080/")
        for needle in ('"contextmenu"', '"dragstart"', "ev.ctrlKey || ev.metaKey", "/^F\\d{1,2}$/"):
            with self.subTest(needle=needle):
                self.assertIn(needle, page)

    def test_the_boot_screen_server_is_restarted_if_it_dies(self):
        """Without it the browser would sit on Chromium's own error page."""
        session = read("deploy/kiosk-session")
        loop = session[session.index("python3 -m bettercnc.bootpage") - 120:
                       session.index("python3 -m bettercnc.bootpage") + 260]
        self.assertIn("while true; do", loop)

    def test_the_service_sets_the_kiosk_keyboard(self):
        unit = read("deploy/files/bettercnc-kiosk.service.in")
        self.assertIn("Environment=XKB_CONFIG_EXTRA_PATH=@REPO@/deploy/files/xkb", unit)
        self.assertIn("Environment=XKB_DEFAULT_OPTIONS=bettercnc:kiosk", unit)
        self.assertIn("Environment=XKB_DEFAULT_LAYOUT=@LAYOUT@", unit)


if __name__ == "__main__":
    unittest.main(verbosity=2)
