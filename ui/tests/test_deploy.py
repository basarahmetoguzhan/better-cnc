"""deploy/ - the kiosk and the machine over SSH - and the kiosk's boot screen
(bettercnc.bootpage).

What runs here, off the machine: the INIs the kiosk and the simulator derive
from the rig's (scripts/ini_derive.py), the boot screen's server, and the
rules deploy/README.md states, pinned in the scripts as text. The kiosk itself
- cage, Chromium, LinuxCNC, the UI - runs in sim/ (`sim/sim.sh kiosk`), and
the systemd unit and shell scripts are checked there (`sim/sim.sh lint`).
"""

from __future__ import annotations

import ast
import contextlib
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List

from helpers import REPO_ROOT, ini_raw_values

from bettercnc import bootpage as B
from bettercnc.configmodel import load_config_from_ini
from bettercnc.profiles import TEST_RIG_INPUTS

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
# The e2e harness imports without linuxcnc: its pure parts are checked here.
e2e = _load(os.path.join(REPO_ROOT, "sim", "e2e", "harness.py"), "e2e_harness")


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

    def test_a_source_that_is_not_utf8_is_one_line_too(self):
        bad = os.path.join(tempfile.mkdtemp(), "m.ini")
        self.addCleanup(__import__("shutil").rmtree, os.path.dirname(bad))
        with open(bad, "wb") as fh:
            fh.write(b"[DISPLAY]\nDISPLAY = axis\n# \xff\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(make_kiosk_ini.main(["x", bad]), 1)
        self.assertEqual(err.getvalue().count("\n"), 1, err.getvalue())
        self.assertIn("okunamadı", err.getvalue())

    def test_gitignored(self):
        self.assertIn("configs/**/*-kiosk.ini", read(".gitignore"))
        self.assertIn("deploy/machine.local", read(".gitignore"))


class TestSimIniAfterTheRefactor(unittest.TestCase):
    """sim/make_sim_ini.py moved its copying to scripts/ini_derive.py."""

    def test_exactly_halfile_display_and_program_prefix_change(self):
        out = make_sim_ini.convert(rig_text())
        diff = changed_lines(rig_text(), out)
        self.assertEqual(
            [d[1] for d in diff],
            ["DISPLAY = axis", "PROGRAM_PREFIX = ../../nc_files/", "HALFILE = test-rig.hal"],
        )
        self.assertIn("\n[SIM]\nLINK_STUB = ", out)

    def test_program_prefix_is_the_repos_nc_files_absolute(self):
        """The sim INI is written to /tmp/better-cnc-sim (sim/start.sh),
        where the rig's relative ../../nc_files/ would name /nc_files."""
        out = make_sim_ini.convert(rig_text())
        line = next(x for x in out.splitlines() if x.startswith("PROGRAM_PREFIX = "))
        prefix = line.split(" = ", 1)[1]
        self.assertTrue(os.path.isabs(prefix), prefix)
        self.assertEqual(prefix, os.path.join(REPO_ROOT, "nc_files"))
        # the directory the rig's relative prefix names from the rig's INI
        rig_prefix = ini_raw_values()["DISPLAY.PROGRAM_PREFIX"]
        self.assertEqual(os.path.normpath(os.path.join(os.path.dirname(RIG_INI), rig_prefix)), prefix)
        self.assertTrue(os.path.isfile(os.path.join(prefix, "faz8-kare.ngc")))


# ---------------------------------------------------------------------------
# sim/: the switch inputs under the rig's names (hm2_link_stub.py,
# test-rig-sim.hal) and the e2e harness (sim/e2e)
# ---------------------------------------------------------------------------


def _hal_nets(text: str) -> Dict[str, List[str]]:
    """{signal: [every pin token on its `net` lines]} - arrows dropped."""
    nets: Dict[str, List[str]] = {}
    for line in text.splitlines():
        words = line.split("#", 1)[0].split()
        if len(words) >= 2 and words[0] == "net":
            nets.setdefault(words[1], []).extend(w for w in words[2:] if w not in ("=>", "<=", "<=>"))
    return nets


class TestSimSwitchInputs(unittest.TestCase):
    """The UI reads the switches from hm2_7i92.0.gpio.0NN.in_not
    (profiles.TEST_RIG_INPUTS); the sim had no such pins, so Alarmlar read
    'okunamadı' there (sim lens, 2026-11-13)."""

    def test_the_stub_makes_the_five_in_not_pins_as_hal_in_bits(self):
        stub = read("sim/hm2_link_stub.py")
        prefix = re.search(r'^COMPONENT = "([^"]+)"', stub, re.M).group(1)
        made = re.findall(r'comp\.newpin\("(gpio\.\d{3}\.in_not)", hal\.HAL_BIT, hal\.HAL_IN\)', stub)
        self.assertEqual(sorted(made), ["gpio.020.in_not", "gpio.021.in_not", "gpio.022.in_not",
                                        "gpio.023.in_not", "gpio.024.in_not"])
        self.assertEqual(sorted(prefix + "." + m for m in made),
                         sorted(inp.hal_pin for inp in TEST_RIG_INPUTS))
        # readers only: no gpio pin is made any other way
        self.assertEqual(len(re.findall(r"gpio\.\d{3}\.in_not", stub.split("def main")[1])), 5)

    def test_the_sim_hal_puts_each_on_the_rigs_signal(self):
        """Same signal per pin as configs/test-rig/test-rig.hal nets it."""
        sim = _hal_nets(read("sim/test-rig-sim.hal"))
        rig = _hal_nets(read("configs/test-rig/test-rig.hal"))
        for inp in TEST_RIG_INPUTS:
            with self.subTest(pin=inp.pin):
                rig_sig = [sig for sig, pins in rig.items()
                           if "hm2_[HOSTMOT2](BOARD).0.{}.in_not".format(inp.pin) in pins]
                sim_sig = [sig for sig, pins in sim.items() if inp.hal_pin in pins]
                self.assertEqual(len(rig_sig), 1, rig_sig)
                self.assertEqual(sim_sig, rig_sig)

    def test_each_switch_signal_keeps_one_writer_the_simulated_switch(self):
        sim = _hal_nets(read("sim/test-rig-sim.hal"))
        for name, sw in e2e.SWITCHES.items():
            with self.subTest(switch=name):
                sig = [s for s, pins in sim.items() if "hm2_7i92.0.{}.in_not".format(sw.gpio) in pins]
                self.assertEqual(len(sig), 1, sig)
                writers = [p for p in sim[sig[0]] if p.endswith(".home-sw")]
                self.assertEqual(writers, ["sim-{}.home-sw".format(name)])


class TestE2EHarnessTables(unittest.TestCase):
    """sim/e2e/harness.py's tables, tied to the files they mirror."""

    def test_switches_match_the_profile_and_the_sim_hal(self):
        by_pin = {inp.pin: inp for inp in TEST_RIG_INPUTS}
        self.assertEqual(sorted(sw.gpio for sw in e2e.SWITCHES.values()), sorted(by_pin))
        for name, sw in e2e.SWITCHES.items():
            with self.subTest(switch=name):
                inp = by_pin[sw.gpio]
                self.assertEqual((sw.label, sw.axis, sw.end), (inp.label, inp.axis, inp.end))
                self.assertEqual(sw.joint, "XYZ".index(sw.axis))
        self.assertEqual(sorted(e2e.END_TO_SWITCH), ["X+", "X-", "Y+", "Y-", "Z+"])

    def test_default_positions_come_from_the_sim_hal_on_their_side(self):
        """A min switch sits below motor 0, a max (and Z's top) above:
        sim_home_switch picks the side from home-pos's sign."""
        defaults = e2e.default_switch_positions()
        self.assertEqual(defaults, {"x-min": -10.0, "x-max": 392.82, "y-min": -10.0,
                                    "y-max": 380.78, "z-top": 5.0})
        for name, pos in defaults.items():
            self.assertEqual(e2e.switch_side(pos), 1 if e2e.SWITCHES[name].end == "+" else -1, name)
        with self.assertRaises(e2e.SwitchError):
            e2e.default_switch_positions("setp sim-x-min.home-pos -10\n")

    def test_home_and_soft_limits_match_the_ini(self):
        ini = ini_raw_values()
        for i, axis in enumerate("XYZ"):
            with self.subTest(axis=axis):
                self.assertEqual(e2e.HOME_POSITION[i], float(ini["JOINT_{}.HOME".format(i)]))
                self.assertEqual(e2e.SOFT_LIMITS[axis], (float(ini["AXIS_{}.MIN_LIMIT".format(axis)]),
                                                         float(ini["AXIS_{}.MAX_LIMIT".format(axis)])))


class TestE2EHarnessSwitchRules(unittest.TestCase):
    """The pure rules of place_switch and press (no HAL)."""

    D = {"x-min": -10.0, "x-max": 392.82, "y-min": -10.0, "y-max": 380.78, "z-top": 5.0}

    def test_a_switch_is_never_moved_across_motor_0(self):
        self.assertEqual(e2e.check_placement("x-min", -2.0, self.D), -2.0)
        self.assertEqual(e2e.check_placement("x-max", 0.0, self.D), 0.0)   # 0 is the + side
        for name, pos in (("x-min", 0.0), ("x-min", 3.0), ("z-top", -0.1), ("y-max", -5.0)):
            with self.subTest(name=name, pos=pos), self.assertRaises(e2e.SwitchError) as ctx:
                e2e.check_placement(name, pos, self.D)
            self.assertIn("işaret", str(ctx.exception))
        for bad in (float("nan"), float("inf")):
            with self.assertRaises(e2e.SwitchError):
                e2e.check_placement("x-min", bad, self.D)
        with self.assertRaises(e2e.SwitchError):
            e2e.check_placement("z-bottom", -1.0, self.D)

    def test_press_puts_the_trip_point_behind_the_axis_on_the_switchs_side(self):
        depth = e2e.PRESS_DEPTH_MM
        self.assertEqual(e2e.press_position("x-max", 40.1, self.D), 40.1 - depth)
        self.assertEqual(e2e.press_position("z-top", 0.0, self.D), 0.0)     # fresh start: at 0
        self.assertEqual(e2e.press_position("z-top", 0.2, self.D), 0.0)     # 0 is closer
        self.assertEqual(e2e.press_position("x-min", -4.9, self.D), -4.9 + depth)
        self.assertEqual(e2e.press_position("x-min", -0.2, self.D), -0.1)   # stays below 0
        for name, cur in (("z-top", -0.1), ("x-max", -4.9), ("x-min", 0.0), ("y-min", 43.7)):
            with self.subTest(name=name, cur=cur), self.assertRaises(e2e.SwitchError) as ctx:
                e2e.press_position(name, cur, self.D)
            self.assertIn("burada basılamaz", str(ctx.exception))

    def test_press_never_yields_a_position_check_placement_refuses(self):
        for name in self.D:
            for cur in (-50.0, -4.9, -0.3, -0.01, 0.0, 0.01, 0.3, 4.9, 50.0):
                try:
                    pos = e2e.press_position(name, cur, self.D)
                except e2e.SwitchError:
                    continue
                with self.subTest(name=name, cur=cur):
                    e2e.check_placement(name, pos, self.D)
                    side = e2e.switch_side(self.D[name])
                    # pressed where it stands: at or past the trip point
                    self.assertTrue(cur >= pos if side > 0 else cur <= pos, (cur, pos))

    def test_cli_refuses_without_touching_hal(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(e2e.main([]), 2)
            self.assertEqual(e2e.main(["press"]), 2)
            self.assertEqual(e2e.main(["bogus"]), 2)
            self.assertEqual(e2e.main(["press", "Z-"]), 1)   # no Z bottom switch
            self.assertEqual(e2e.main(["release", "W+"]), 1)
        self.assertIn("Z'nin alt switch'i yok", err.getvalue())


class TestE2ERunner(unittest.TestCase):
    """make sim-e2e: sim/e2e/run.sh. The rules it must keep, as text (it
    runs Docker, so its behaviour is checked by running it)."""

    def test_its_own_container_name_and_port_never_an_inherited_one(self):
        run = read("sim/e2e/run.sh")
        self.assertIn('export SIM_NAME="${E2E_SIM_NAME:-better-cnc-e2e}"', run)
        self.assertIn('export SIM_PORT="${E2E_SIM_PORT:-8771}"', run)
        self.assertNotIn("${SIM_NAME:-", run)
        self.assertIn("""trap '"$SIM" down >/dev/null 2>&1 || true' EXIT""", run)

    def test_a_fresh_linuxcnc_per_module_tests_run_inside(self):
        run = read("sim/e2e/run.sh")
        loop = run[run.index('for m in "${modules[@]}"'):]
        self.assertLess(loop.index('"$SIM" down'), loop.index('"$SIM" up'))
        self.assertIn("docker exec -e PYTHONDONTWRITEBYTECODE=1", loop)
        self.assertIn("python3 -m unittest discover -s /repo/sim/e2e -p", loop)
        self.assertTrue(os.access(os.path.join(REPO_ROOT, "sim", "e2e", "run.sh"), os.X_OK))

    def test_make_target_lint_and_sim_sh_reach_it(self):
        self.assertIn("sim-e2e:", read("Makefile"))
        self.assertIn("\tsim/e2e/run.sh $(E2E)", read("Makefile"))
        sim = read("sim/sim.sh")
        self.assertIn("shellcheck -x sim/*.sh sim/e2e/*.sh ", sim)
        self.assertIn("python3 /repo/sim/e2e/harness.py \"$cmd\" \"$@\"", sim)
        for cmd in ("press", "release", "switches"):
            self.assertIn("#   sim/sim.sh {}".format(cmd), sim)

    def test_the_harness_never_opens_a_command_or_error_channel(self):
        """It commands through the UI only, and an error read from the
        queue would be gone for the UI's drain (harness docstring): no
        call to command() or error_channel() in any sim/e2e module."""
        e2e_dir = os.path.join(REPO_ROOT, "sim", "e2e")
        modules = sorted(n for n in os.listdir(e2e_dir) if n.endswith(".py"))
        self.assertIn("harness.py", modules)
        for name in modules:
            with self.subTest(file=name):
                tree = ast.parse(read(os.path.join("sim", "e2e", name)))
                called = {
                    getattr(node.func, "attr", getattr(node.func, "id", None))
                    for node in ast.walk(tree) if isinstance(node, ast.Call)
                }
                self.assertFalse(called & {"command", "error_channel"}, name)

    def test_the_harness_imports_without_linuxcnc(self):
        """Loaded above with no linuxcnc module (on the Mac), so the
        import stays inside _lc()."""
        self.assertIsNone(re.search(r"^(import|from)\s+(linuxcnc|hal)\b",
                                    read("sim/e2e/harness.py"), re.M))


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
        for url in ("http://x/</script><script>alert(1)//", "http://x/<!--"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                B.main(["--ui-url", url])
        self.assertEqual(self.bound, [])

    def test_the_boot_page_navigates_once(self):
        """Every tick used to call location.replace() again while the first
        navigation was still loading (audit 2026-10-21)."""
        page = B.render("x", "http://127.0.0.1:8080/")
        self.assertIn("if (s.ui_up && !leaving) {", page)
        # and tried again later: Esc stops a load, and nothing else would retry
        self.assertIn("setTimeout(function () { leaving = false; }, 5000);", page)

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
            self.screen._sent += 1              # as the real send() numbers them
            if method != "Target.getTargets":   # asked on every look
                self.sent.append((method, params or {}))
            return self.screen._sent

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
        self.now[0] += W.REPLACE_AGAIN_S   # (the wait tab still listed: throttled)
        self.assertEqual(self.screen.guard(True, True), ["arayüze dönüldü"])
        self.assertEqual(self.sent[0], ("Target.createTarget", {"url": self.UI}))

    def test_chromium_internal_tabs_and_closed_tabs_are_ignored(self):
        self.tab("o", "chrome://omnibox-popup.top-chrome/", "chrome://omnibox-popup")
        self.tab("ui", self.UI, "better-cnc · Test tezgahı")
        self.tab("gone", "http://example.com/", "x")
        self.screen.handle({"method": "Target.targetDestroyed", "params": {"targetId": "gone"}})
        self.now[0] += 10
        self.assertEqual(self.screen.guard(True, True), [])

    def test_each_look_asks_for_the_tab_list_under_its_own_id(self):
        """The answer is matched to the look by id (audit 2026-10-21: the
        fake send did not number its messages, so nothing checked this)."""
        self.screen.guard(True, True)
        self.assertEqual(self.screen._asked, {self.screen._sent})
        self.assertEqual(self.screen.unanswered(), 1)
        self.screen.handle({"id": self.screen._sent, "result": {"targetInfos": [
            {"targetId": "ui", "type": "page", "url": self.UI, "title": "better-cnc · x"}]}})
        self.assertEqual(self.screen.unanswered(), 0)
        self.assertEqual(list(self.screen._tabs), ["ui"])

    def test_two_of_our_pages_keep_the_newest(self):
        """A replacement opened while the tab list still showed the tab it
        replaced left two UI tabs, each polling, for the browser's life."""
        self.tab("old", self.UI, "better-cnc · x")
        self.tab("new", self.UI, "better-cnc · x")
        self.assertEqual(self.screen.guard(True, True), ["fazla sekme kapatıldı (1)"])
        self.assertEqual(self.sent, [("Target.closeTarget", {"targetId": "old"})])

    def test_the_way_back_from_the_wait_page_is_throttled_too(self):
        self.tab("w", W.WAIT_URL, "better-cnc · bekleyin")
        self.assertEqual(self.screen.guard(True, True), ["arayüze dönüldü"])
        self.sent.clear()
        self.now[0] += W.GUARD_EVERY_S          # the closed tab still listed
        self.assertEqual(self.screen.guard(True, True), [])
        self.assertEqual(self.sent, [])
        self.now[0] += W.REPLACE_AGAIN_S
        self.assertEqual(self.screen.guard(True, True), ["arayüze dönüldü"])

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

    def test_a_tab_list_answer_over_the_real_pipe_updates_the_table(self):
        del self.screen.send  # the real one again
        self.screen.guard(True, True)
        asked = [json.loads(m) for m in os.read(self.cmd_r, 65536).split(b"\0") if m]
        look = next(m for m in asked if m["method"] == "Target.getTargets")
        answer = {"id": look["id"], "result": {"targetInfos": [
            {"targetId": "t9", "type": "page", "url": self.UI, "title": "better-cnc · x"}]}}
        os.write(self.ev_w, json.dumps(answer).encode() + b"\0")
        for _ in range(200):
            if self.screen._tabs:
                break
            __import__("time").sleep(0.005)
        self.assertEqual(self.screen._tabs["t9"]["title"], "better-cnc · x")
        self.assertEqual(self.screen.unanswered(), 0)

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
        # alive until just under GRACE_S: a watch without the grace would have
        # judged the never-asked page stale from STALE_S on
        proc = _Proc(polls=int(W.GRACE_S / W.GUARD_EVERY_S) - 2)
        checks = int(W.GRACE_S / W.CHECK_EVERY_S)
        n, err = self.run_watch([proc], [(W.NEVER, W.NEVER)] * checks, starts=1)
        self.assertFalse(proc.terminated, err)        # not during the grace
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

    def test_a_ui_back_after_a_short_outage_is_not_judged_before_it_is_asked(self):
        """A fresh UI answers /healthz with client_age_s null (NEVER) until
        the page's next poll; the page is still on it, the boot screen was
        last asked long ago. Audit 2026-10-21: NEVER counted as "stale" and
        the browser was restarted in that second."""
        checks = int(W.GRACE_S / W.CHECK_EVERY_S) + 1
        answers = [(1.0, W.NEVER)] * checks          # normal running
        answers += [(None, 600.0)] * 4               # UI away 8 s: under BOOT_MS
        answers += [(W.NEVER, 600.0)] * 2            # UI back, not yet asked
        answers += [(1.0, 600.0)] * 4                # the page asks it again
        proc = _Proc(polls=int(len(answers) * W.CHECK_EVERY_S / W.GUARD_EVERY_S) + 4)
        n, err = self.run_watch([proc], answers, starts=1)
        self.assertFalse(proc.terminated, err)
        # but a fresh server nobody asks for STALE_S is still a dead page
        answers = [(1.0, W.NEVER)] * checks + [(W.NEVER, 600.0)] * 8
        proc = _Proc()
        n, err = self.run_watch([proc], answers, starts=1)
        self.assertTrue(proc.terminated)

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

    def test_a_browser_that_answers_nothing_on_the_pipe_is_restarted(self):
        clock = [0.0]

        class Mute:
            def wait(self, s):
                clock[0] += s

            def guard(self, ui_up, boot_up):
                return []

            def unanswered(self):
                return W.STUCK_LOOKS + 1

            def close(self):
                pass

        proc = _Proc()
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            W.watch(["b"], "ui", "boot", start=lambda cmd: (proc, Mute()),
                    ask=lambda url: 1.0, clock=lambda: clock[0],
                    sleep=lambda s: clock.__setitem__(0, clock[0] + s), starts=1)
        self.assertTrue(proc.terminated)
        self.assertIn("bekçiye cevap vermiyor", err.getvalue())
        # but not before GRACE_S: a cold Chromium answers nothing until its
        # main loop runs (review 2026-10-21: it was cut at 11 s)
        self.assertGreaterEqual(clock[0], W.GRACE_S)
        proc = _Proc(polls=int(W.GRACE_S / W.GUARD_EVERY_S) - 2)
        clock[0] = 0.0
        with contextlib.redirect_stderr(io.StringIO()):
            W.watch(["b"], "ui", "boot", start=lambda cmd: (proc, Mute()),
                    ask=lambda url: 1.0, clock=lambda: clock[0],
                    sleep=lambda s: clock.__setitem__(0, clock[0] + s), starts=1)
        self.assertFalse(proc.terminated)

    def test_a_start_that_fails_leaks_no_pipe_ends(self):
        """start() is retried every second; four pipe ends per failure
        would have hit the fd limit in minutes (review 2026-10-21)."""
        before = len(os.listdir("/dev/fd"))
        for _ in range(3):
            with self.assertRaises(FileNotFoundError):
                W.start_browser(["/nonexistent/kiosk-browser", "x"], "http://u/", "http://b/")
        self.assertEqual(len(os.listdir("/dev/fd")), before)

    def test_a_guard_error_restarts_the_browser_not_the_watch(self):
        """The browser exits with the watch (the DevTools pipe): a watch that
        died left the kiosk black until power off (audit 2026-10-21)."""
        clock = [0.0]

        class Broken:
            def wait(self, s):
                clock[0] += s

            def guard(self, ui_up, boot_up):
                raise RuntimeError("boom")

            def unanswered(self):
                return 0

            def close(self):
                pass

        procs = [_Proc(), _Proc(polls=0)]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = W.watch(["b"], "ui", "boot", start=lambda cmd: (procs.pop(0), Broken()),
                        ask=lambda url: 1.0, clock=lambda: clock[0],
                        sleep=lambda s: clock.__setitem__(0, clock[0] + s), starts=2)
        self.assertEqual(n, 2)
        self.assertIn("bekçi hatası (RuntimeError: boom)", err.getvalue())

    def test_a_start_that_fails_is_retried(self):
        clock = [0.0]
        attempts = [OSError("no pipe"), (_Proc(polls=0), None)]

        def start(cmd):
            a = attempts.pop(0)
            if isinstance(a, Exception):
                raise a
            return a

        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            n = W.watch(["b"], "ui", "boot", start=start, ask=lambda url: 1.0,
                        clock=lambda: clock[0], sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                        starts=2)
        self.assertEqual(n, 2)
        self.assertIn("tarayıcı başlatılamadı (OSError: no pipe)", err.getvalue())

    def test_a_server_dying_mid_answer_is_no_answer_not_a_crash(self):
        """http.client.BadStatusLine / IncompleteRead are neither OSError nor
        URLError; the UI dies mid-answer whenever LinuxCNC stops."""
        hc = importlib.import_module("http.client")
        self.addCleanup(setattr, urllib.request, "urlopen", urllib.request.urlopen)
        for exc in (hc.BadStatusLine("NOT HTTP"), hc.IncompleteRead(b"")):
            def boom(*a, **k):
                raise exc
            urllib.request.urlopen = boom
            self.assertIsNone(W.client_age("http://x/healthz"))
            self.assertFalse(B.ui_is_up("http://x/"))

    def test_the_screen_guard_hears_which_server_answers(self):
        clock = [0.0]

        class FakeScreen:
            calls, closed = [], False

            def wait(self, s):          # instead of sleep while a screen is there
                clock[0] += s

            def guard(self, ui_up, boot_up):
                self.calls.append((ui_up, boot_up))
                return ["arayüz açıldı"] if len(self.calls) == 3 else []

            def unanswered(self):
                return 0

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
        stuck = next(r for r in table.splitlines() if r.startswith("| `STUCK_LOOKS`"))
        self.assertIn("| {} bakış = {:g} s |".format(W.STUCK_LOOKS, W.STUCK_LOOKS * W.GUARD_EVERY_S), stuck)
        # the adapter's rows too
        from bettercnc import adapter as A
        for name, value in {"LOOP_STALL_S": "2 s", "HALCMD_TIMEOUT_S": "2 s", "LINK_SETTLE_S": "0,5 s"}.items():
            with self.subTest(name=name):
                row = next(r for r in table.splitlines() if r.startswith("| `{}`".format(name)))
                self.assertIn("| {} |".format(value), row)
                self.assertEqual(float(value.split()[0].replace(",", ".")), getattr(A, name))

    def test_the_sim_link_stub_names_the_pins_the_adapter_reads(self):
        """sim/hm2_link_stub.py stands in for the three HAL items; its names
        were tied to the adapter's only by hand (audit 2026-10-21)."""
        from bettercnc.state import LinkHealth
        stub = read("sim/hm2_link_stub.py")
        prefix = re.search(r'^COMPONENT = "([^"]+)"', stub, re.M).group(1)
        for const, kind in (("HAL_PACKET_ERROR_TOTAL", "newpin"), ("HAL_WATCHDOG_HAS_BIT", "newpin"),
                            ("HAL_IO_ERROR", "newparam")):
            full = getattr(LinkHealth, const)
            self.assertTrue(full.startswith(prefix + "."), full)
            self.assertIn('comp.{}("{}"'.format(kind, full[len(prefix) + 1:]), stub)
        # and plays the driver: io_error on a cable pull, has_bit after the clear
        self.assertIn('comp["io_error"] = 1', stub)
        self.assertIn('comp["watchdog.has_bit"] = 1', stub)

    def test_linuxcnc_is_started_without_display_or_tty(self):
        """linuxcnc.in opens Tk dialogs with DISPLAY and waits for typed
        answers with a tty (kiosk-session's own comment); pinned here."""
        self.assertIn('env -u DISPLAY setsid linuxcnc "$KIOSK_INI" < /dev/null', read("deploy/kiosk-session"))

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
        # the INI LinuxCNC runs, and a 30 s deadline rather than 30 tries
        self.assertIn('args+=(--ini "$INI")', display)
        self.assertIn('if [ "$1" = -ini ]; then INI="$2"; break; fi', display)
        self.assertIn("deadline=$((SECONDS + 30))", display)
        self.assertNotIn("seq 1 30", display)

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
        # Debian starts sshd on install, password login on: the keys and the
        # key-only config come BEFORE the install, and the running daemon is
        # checked after (audit 2026-10-21).
        self.assertLess(boot.index(".keys"), boot.index("apt-get install"))
        self.assertLess(boot.index("PasswordAuthentication no"), boot.index("apt-get install"))
        self.assertIn("sshd -T", boot)
        self.assertLess(boot.index("systemctl restart ssh"), boot.index("sshd -T"))

    def test_sync_leaves_the_plan_and_the_machine_address_on_the_mac(self):
        machine = read("deploy/machine.sh")
        self.assertIn("--exclude docs/PLAN-BUGUN.md", machine)
        self.assertIn("--exclude deploy/machine.local", machine)
        # bootstrap's server is a background job: Ctrl-C does not reach it
        self.assertIn("""trap 'kill "$srv" 2>/dev/null; rm -rf "$dir"' EXIT""", machine)

    def test_the_session_can_stop_what_it_started(self):
        """The linuxcnc script handles TERM only once its foreground display
        exits; the boot screen's subshell died without its python."""
        session = read("deploy/kiosk-session")
        self.assertIn("setsid linuxcnc", session)
        self.assertIn("""pkill -TERM -s "$LCNC_PID" -f 'bettercnc-display|bettercnc\\.app'""", session)
        # the INI-error pause is stoppable too, and last-stop.txt is this session's
        self.assertEqual(session.count('SLEEP_PID=$!'), 2)
        self.assertIn('rm -f "$BETTERCNC_LAST_STOP_FILE"', session)
        self.assertIn("""trap 'kill "$child" 2>/dev/null; exit 0' TERM INT HUP""", session)
        self.assertIn('export BETTERCNC_INI="$BASE_INI"', session)
        # the browser exits with its watch: the watch is in a loop too
        self.assertIn("tarayıcı bekçisi durdu — yeniden başlatılıyor", session)
        # why it last stopped reaches the new UI (the boot screen's line lives RESTART_S)
        self.assertIn('export BETTERCNC_LAST_STOP_FILE="$RUN/last-stop.txt"', session)
        self.assertIn("Önceki durma:", session)
        self.assertIn('--startup-note-file "$BETTERCNC_LAST_STOP_FILE"', read("deploy/bettercnc-display"))
        # the linuxcnc script's own output is not in $LOG without a tty
        self.assertIn("linuxcnc_print.txt", session)

    def test_enable_now_survives_the_desktop_it_stops(self):
        kiosk = read("deploy/kiosk.sh")
        self.assertIn("systemd-run", kiosk)
        self.assertIn("sh -c \"systemctl stop '$dm' && systemctl start '$UNIT'\"", kiosk)
        self.assertIn("systemd-run --quiet --collect", kiosk)   # a failed switch must not block the next

    def test_usage_texts_end_with_the_header(self):
        """The usage text is the header comment; a fixed line range printed
        code once the header grew (audit 2026-10-21)."""
        for script in ("deploy/machine.sh", "deploy/kiosk.sh", "sim/sim.sh"):
            with self.subTest(script=script):
                self.assertIn("""sed -n '2,/^[^#]/p' "$0" | sed '$d; s/^# \\{0,1\\}//'""", read(script))
                out = __import__("subprocess").run(["bash", os.path.join(REPO_ROOT, script)],
                                     capture_output=True, text=True)
                self.assertEqual(out.returncode, 2)
                self.assertNotIn("set -euo", out.stdout)
                lines = read(script).split("\n")
                end = next(i for i in range(1, len(lines)) if not lines[i].startswith("#"))
                self.assertEqual(out.stdout.rstrip("\n").split("\n")[-1].strip(),
                                 lines[end - 1].lstrip("#").strip())

    def test_board_link_never_takes_the_default_route(self):
        self.assertIn("ipv4.never-default yes", read("deploy/kiosk.sh"))


# ---------------------------------------------------------------------------
# kiosk.sh board-network: the board's address on whatever PC the live USB is in
# ---------------------------------------------------------------------------

# Stand-ins that log their arguments (one line per call, \x1f between
# arguments) and answer what board-network asks.
_FAKE_NMCLI = r"""#!/bin/bash
{ for a in "$@"; do printf '%s\037' "$a"; done; printf '\n'; } >> "$FAKE/nmcli.log"
if [ "$*" = "-t -f DEVICE,TYPE,STATE device" ]; then
  printf '%s' "${LC_ALL:-}" > "$FAKE/lc_all"
  cat "$FAKE/devices"
fi
exit 0
"""
_FAKE_IP = r"""#!/bin/bash
if [ "$1" = link ] && [ "$2" = show ]; then grep -qxF "$3" "$FAKE/links"; exit $?; fi
exit 0
"""
_FAKE_ID = """#!/bin/sh
echo "$FAKE_UID"
"""
_FAKE_SYSTEMCTL = """#!/bin/sh
[ -n "${FAKE_NM_DOWN:-}" ] && exit 3
exit 0
"""


class TestBoardNetwork(unittest.TestCase):
    """The live USB moves between PCs and each names its wired port its own
    way: the August laptop enp3s0, the 2026-11-12 PC enp12s0. board-network
    used to bind the board's profile to enp3s0. Run here with stand-ins for
    id, systemctl, ip and nmcli; the nmcli lines themselves were run against
    a real NetworkManager 1.52 (Debian 13) on 2026-11-12."""

    @classmethod
    def setUpClass(cls):
        # One set of stand-ins for the whole class: macOS checks every new
        # executable on its first run, which made a fresh set per call ~0.6 s.
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fake = cls._tmp.name
        for name, body in (("nmcli", _FAKE_NMCLI), ("ip", _FAKE_IP),
                           ("id", _FAKE_ID), ("systemctl", _FAKE_SYSTEMCTL)):
            path = os.path.join(cls.fake, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, 0o755)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def run_board_network(self, *args, devices="", links=("enp12s0",), uid="0", nm_down=False):
        fake = self.fake
        for stale in ("nmcli.log", "lc_all"):
            if os.path.exists(os.path.join(fake, stale)):
                os.remove(os.path.join(fake, stale))
        with open(os.path.join(fake, "devices"), "w", encoding="utf-8") as fh:
            fh.write(devices)
        with open(os.path.join(fake, "links"), "w", encoding="utf-8") as fh:
            fh.write("\n".join(links) + "\n")
        env = {"PATH": fake + os.pathsep + "/usr/bin:/bin", "FAKE": fake, "FAKE_UID": uid}
        if nm_down:
            env["FAKE_NM_DOWN"] = "1"
        out = subprocess.run(["bash", os.path.join(DEPLOY, "kiosk.sh"), "board-network", *args],
                             capture_output=True, encoding="utf-8", errors="replace",
                             env=env, timeout=30)
        calls = []
        if os.path.exists(os.path.join(fake, "nmcli.log")):
            with open(os.path.join(fake, "nmcli.log"), encoding="utf-8") as fh:
                calls = [line.rstrip("\n").split("\x1f")[:-1] for line in fh]
        lc_all = None
        if os.path.exists(os.path.join(fake, "lc_all")):
            with open(os.path.join(fake, "lc_all"), encoding="utf-8") as fh:
                lc_all = fh.read()
        return out, calls, lc_all

    @staticmethod
    def added(calls):
        return next(c for c in calls if c[:2] == ["connection", "add"])

    @staticmethod
    def ups(calls):
        return [c for c in calls if c[:2] == ["connection", "up"]]

    def test_on_any_pc_the_profile_names_no_port(self):
        out, calls, _ = self.run_board_network(
            devices="enp12s0:ethernet:connected\nwlx788cb5b85934:wifi:connected\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        add = self.added(calls)
        self.assertEqual(add[add.index("ifname") + 1], "*")
        # wired ports only, never a USB adapter (udev names those enx<MAC>)
        self.assertEqual(add[add.index("match.interface-name") + 1], "en* eth* !enx*")
        start = add.index("ipv4.method")
        settings = dict(zip(add[start::2], add[start + 1::2]))
        self.assertEqual(settings["ipv4.method"], "manual")
        self.assertEqual(settings["ipv4.addresses"], "10.10.10.1/24")
        self.assertEqual(settings["ipv4.never-default"], "yes")
        self.assertEqual(settings["ipv6.method"], "disabled")
        self.assertEqual(settings["connection.autoconnect"], "yes")
        # above a profile's default (0): a plugged-in board gets this one
        self.assertGreater(int(settings["connection.autoconnect-priority"]), 0)
        # the old profile goes first: a second run replaces it, never adds a twin
        self.assertLess(calls.index(["connection", "delete", "bettercnc-board"]), calls.index(add))

    def test_a_port_in_use_is_never_taken_over(self):
        """2026-11-12: the PC's only port carried the SSH session the command
        would have run in; bringing the profile up there cuts it."""
        for state in ("connected", "connecting (getting IP configuration)"):
            with self.subTest(state=state):
                out, calls, _ = self.run_board_network(devices="enp12s0:ethernet:%s\n" % state)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(self.ups(calls), [])
                self.assertIn("kendiliğinden devreye girer", out.stdout)
                self.assertIn("Wired connection", out.stdout)

    def test_the_one_free_port_with_a_cable_gets_it_now(self):
        out, calls, lc_all = self.run_board_network(
            devices="lo:loopback:unmanaged\nenp3s0:ethernet:disconnected\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.ups(calls), [["connection", "up", "bettercnc-board", "ifname", "enp3s0"]])
        self.assertEqual(lc_all, "C")   # the state words are compared in English

    def test_usb_adapters_unplugged_ports_and_wifi_are_not_the_board(self):
        not_the_board = ("enx001122334455:ethernet:disconnected",   # USB adapter
                         "enp3s0:ethernet:unavailable",            # no cable in
                         "wlp2s0:wifi:disconnected")
        for device in not_the_board:
            with self.subTest(alone=device):
                out, calls, _ = self.run_board_network(devices=device + "\n")
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(self.ups(calls), [])
            with self.subTest(next_to_the_port=device):
                out, calls, _ = self.run_board_network(
                    devices=device + "\nenp12s0:ethernet:disconnected\n")
                self.assertEqual(self.ups(calls),
                                 [["connection", "up", "bettercnc-board", "ifname", "enp12s0"]])

    def test_two_free_ports_wait_and_name_the_way_to_choose(self):
        out, calls, _ = self.run_board_network(
            devices="enp3s0:ethernet:disconnected\neno1:ethernet:disconnected\n")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(self.ups(calls), [])
        self.assertIn("enp3s0 eno1", out.stdout)
        self.assertIn("--iface", out.stdout)

    def test_iface_binds_the_profile_to_that_port_and_brings_it_up(self):
        out, calls, _ = self.run_board_network("--iface", "enp12s0", "--addr", "10.10.10.5/24")
        self.assertEqual(out.returncode, 0, out.stderr)
        add = self.added(calls)
        self.assertEqual(add[add.index("ifname") + 1], "enp12s0")
        self.assertNotIn("match.interface-name", add)
        self.assertEqual(add[add.index("ipv4.addresses") + 1], "10.10.10.5/24")
        self.assertEqual(add[add.index("ipv4.never-default") + 1], "yes")
        self.assertEqual(self.ups(calls), [["connection", "up", "bettercnc-board"]])
        self.assertNotIn(["-t", "-f", "DEVICE,TYPE,STATE", "device"], calls)

    def test_refusals_change_nothing(self):
        cases = (
            (("--iface", "yok0"), {}, "arayüz yok: yok0"),
            (("--bogus",), {}, "bilinmeyen seçenek: --bogus"),
            (("--iface",), {}, "--iface bir değer ister"),
            ((), {"uid": "1000"}, "sudo ile çalıştırın"),
            ((), {"nm_down": True}, "ip addr add 10.10.10.1/24 dev <kartın takılı olduğu port>"),
        )
        for args, kw, message in cases:
            with self.subTest(args=args, **kw):
                out, calls, _ = self.run_board_network(*args, **kw)
                self.assertEqual(out.returncode, 1)
                self.assertIn(message, out.stderr)
                self.assertEqual([c for c in calls if c[:2] in (["connection", "add"], ["connection", "up"])], [])

    def test_the_match_and_the_port_filter_agree(self):
        """BOARD_MATCH says which ports NetworkManager may give the profile;
        board_port() which port it is brought up on at once. One rule."""
        kiosk = read("deploy/kiosk.sh")
        self.assertIn("BOARD_MATCH='en* eth* !enx*'", kiosk)
        self.assertRegex(kiosk, r"enx\*\) return 1 ;;\s*en\*\|eth\*\) return 0 ;;")


# ---------------------------------------------------------------------------
# kiosk.sh clean-shm: the motion shared memory a TERM shutdown leaves behind
# ---------------------------------------------------------------------------

# Stand-ins in TestBoardNetwork's style: each logs its arguments (one line per
# call, \x1f between arguments) and answers from files the test writes.
_FAKE_PGREP = r"""#!/bin/bash
{ for a in "$@"; do printf '%s\037' "$a"; done; printf '\n'; } >> "$FAKE/pgrep.log"
code="$(cat "$FAKE/pgrep_code")"
if [ "$code" = 0 ]; then cat "$FAKE/pids"; fi
exit "$code"
"""
_FAKE_IPCS = r"""#!/bin/bash
{ for a in "$@"; do printf '%s\037' "$a"; done; printf '\n'; } >> "$FAKE/ipcs.log"
printf '%s' "${LC_ALL:-}" > "$FAKE/ipcs_lc_all"
cat "$FAKE/segments"
"""
_FAKE_IPCRM = r"""#!/bin/bash
{ for a in "$@"; do printf '%s\037' "$a"; done; printf '\n'; } >> "$FAKE/ipcrm.log"
exit "$(cat "$FAKE/ipcrm_code")"
"""

#: `ipcs -m` as util-linux prints it in the C locale.
_IPCS_HEAD = (
    "\n------ Shared Memory Segments --------\n"
    "key        shmid      owner      perms      bytes      nattch     status      \n"
)
#: HAL's own segment (HAL_KEY, src/hal/hal_priv.h:120): never ours to remove.
_HAL_SEGMENT = "0x48414c32 32768      user       600        262144     0                       \n"


def _motion_segment(shmid: int, nattch: int) -> str:
    return "0x00000064 {:<10} user       600        2297472    {:<10} {}\n".format(
        shmid, nattch, "locked")


class TestCleanShm(unittest.TestCase):
    """2026-11-12 on the rig: after a TERM shutdown LinuxCNC sometimes left
    its motion segment (SysV key 0x00000064) with nothing attached, and the
    next start failed ("A configuration error is preventing LinuxCNC from
    starting"). The kiosk's restart loop would have failed the same way on
    every try. Run here with stand-ins for pgrep, ipcs and ipcrm."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.fake = cls._tmp.name
        for name, body in (("pgrep", _FAKE_PGREP), ("ipcs", _FAKE_IPCS), ("ipcrm", _FAKE_IPCRM)):
            path = os.path.join(cls.fake, name)
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(body)
            os.chmod(path, 0o755)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _write(self, name: str, text: str) -> None:
        with open(os.path.join(self.fake, name), "w", encoding="utf-8") as fh:
            fh.write(text)

    def _log(self, name: str):
        path = os.path.join(self.fake, name)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [line.rstrip("\n").split("\x1f")[:-1] for line in fh]

    def run_clean_shm(self, segments="", pids="", pgrep_code=1, ipcrm_code=0):
        for stale in ("pgrep.log", "ipcs.log", "ipcrm.log", "ipcs_lc_all"):
            if os.path.exists(os.path.join(self.fake, stale)):
                os.remove(os.path.join(self.fake, stale))
        self._write("segments", _IPCS_HEAD + segments)
        self._write("pids", pids)
        self._write("pgrep_code", str(pgrep_code))
        self._write("ipcrm_code", str(ipcrm_code))
        # No id/systemctl stand-ins: clean-shm runs as the kiosk's user.
        env = {"PATH": self.fake + os.pathsep + "/usr/bin:/bin", "FAKE": self.fake}
        out = subprocess.run(["bash", os.path.join(DEPLOY, "kiosk.sh"), "clean-shm"],
                             capture_output=True, encoding="utf-8", errors="replace",
                             env=env, timeout=30)
        return out, self._log("ipcrm.log")

    def test_an_orphaned_motion_segment_is_removed(self):
        out, removed = self.run_clean_shm(_HAL_SEGMENT + _motion_segment(32769, 0))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(removed, [["-m", "32769"]])   # by its id; HAL's untouched
        self.assertIn("silindi", out.stdout)
        self.assertEqual(self._log("pgrep.log"), [["-x", "linuxcnc|rtapi_app|milltask|linuxcncsvr"]])
        self.assertEqual(self._log("ipcs.log"), [["-m"]])
        with open(os.path.join(self.fake, "ipcs_lc_all"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "C")   # the columns are read in the C locale

    def test_nothing_is_touched_while_linuxcnc_runs(self):
        """pgrep found one: not even the segment table is read."""
        out, removed = self.run_clean_shm(_motion_segment(32769, 0), pids="4242\n4243\n",
                                          pgrep_code=0)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(removed, [])
        self.assertEqual(self._log("ipcs.log"), [])
        self.assertIn("LinuxCNC çalışıyor (pid 4242 4243)", out.stdout)

    def test_a_pgrep_that_cannot_answer_touches_nothing(self):
        """Exit 2 or 3 (bad pattern, fatal error) or 127 (no pgrep) is not
        "no LinuxCNC process"."""
        for code in (2, 3, 127):
            with self.subTest(pgrep_exit=code):
                out, removed = self.run_clean_shm(_motion_segment(32769, 0), pgrep_code=code)
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(removed, [])
                self.assertIn("bakılamadı", out.stderr)

    def test_a_segment_with_something_attached_stays(self):
        out, removed = self.run_clean_shm(_HAL_SEGMENT + _motion_segment(32769, 2))
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(removed, [])
        self.assertIn("kullanımda (2 bağlı)", out.stdout)

    def test_no_motion_segment_nothing_to_do(self):
        for segments in ("", _HAL_SEGMENT):
            with self.subTest(segments=segments):
                out, removed = self.run_clean_shm(segments)
                self.assertEqual((out.returncode, out.stdout, out.stderr), (0, "", ""))
                self.assertEqual(removed, [])

    def test_a_removal_that_fails_says_how_to_do_it_by_hand(self):
        out, removed = self.run_clean_shm(_motion_segment(65538, 0), ipcrm_code=1)
        self.assertEqual(out.returncode, 1)
        self.assertEqual(removed, [["-m", "65538"]])
        self.assertIn("sudo ipcrm -m 65538", out.stderr)

    def test_the_session_cleans_before_every_linuxcnc_start(self):
        """In kiosk-session's restart loop, between the boot screen's
        "starting" line and the linuxcnc command."""
        session = read("deploy/kiosk-session")
        loop = session.index("\nwhile true; do\n  n=$((n + 1))")
        clean = session.index('"$DEPLOY_DIR/kiosk.sh" clean-shm')
        start = session.index('env -u DISPLAY setsid linuxcnc "$KIOSK_INI"')
        self.assertLess(loop, clean)
        self.assertLess(session.index("Makinenin kontrol sistemi başlatılıyor", loop), clean)
        self.assertLess(clean, start)
        self.assertEqual(session.count("clean-shm"), 1)

    def test_the_key_is_motions_default_and_the_rig_keeps_it(self):
        """[EMCMOT]SHMEM_KEY unset: motion uses DEFAULT_SHMEM_KEY, 100
        (reference/linuxcnc/src/emc/motion/emcmotcfg.h:49)."""
        kiosk = read("deploy/kiosk.sh")
        self.assertEqual(int(re.search(r"^LCNC_SHM_KEY=(0x[0-9a-f]{8})$", kiosk, re.M).group(1), 16), 100)
        self.assertIsNone(re.search(r"^\s*SHMEM_KEY\s*=", rig_text(), re.M))
        emcmotcfg = os.path.join(REPO_ROOT, "reference", "linuxcnc", "src", "emc", "motion", "emcmotcfg.h")
        if os.path.exists(emcmotcfg):   # reference/ is optional (make reference)
            with open(emcmotcfg, encoding="utf-8") as fh:
                self.assertRegex(fh.read(), r"#define DEFAULT_SHMEM_KEY 100\b")


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

    def test_the_service_waits_for_no_network_and_keeps_local_time(self):
        unit = read("deploy/files/bettercnc-kiosk.service.in")
        directives = [l for l in unit.splitlines() if l and not l.startswith("#")]
        self.assertFalse([l for l in directives if "network" in l], directives)
        self.assertIn("Environment=TZ=@TZ@", unit)
        kiosk = read("deploy/kiosk.sh")
        self.assertIn("--tz) tz=", kiosk)
        self.assertIn('-e "s|@TZ@|$tz|g"', kiosk)
        self.assertIn('[ -f "/usr/share/zoneinfo/$tz" ] || die', kiosk)   # glibc falls back to UTC silently
        self.assertIn("TZ=%s", kiosk)
        self.assertIn("@TZ@", read("sim/sim.sh"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
