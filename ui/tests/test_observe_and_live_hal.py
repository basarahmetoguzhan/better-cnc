"""Observer mode (--observe) and the live HAL readings: switches, step timing.

Observer mode is for running beside AXIS. On the machine (2026-11-12) an
out-of-repo viewer did it by patching Session.act, swapping the command
channel for a recorder and turning off the error drain and the link-fault
estop. In the repo version the software estop is the only action that
leaves the UI, and the error channel is not read, so LinuxCNC's messages
stay AXIS's (a drained message never reaches AXIS). The link-fault estop
stays on purpose: on the real driver a cable pull raises io_error alone,
and nothing else stops the machine. Every screen says so and shows the
other controls disabled.

The live readings: the five switch inputs on the Alarmlar screen, and the
stepgen timing beside the INI's on the Makine screen, read through
hal.get_value as the link pins are. LinuxCncAdapter is tested against
fake_linuxcnc, like the rest of it.
"""

from __future__ import annotations

import contextlib
import html as htmllib
import inspect
import io
import re
import unittest
from html.parser import HTMLParser
from typing import Dict, List, Optional

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS, ini_raw_values, rig_profile

import fake_linuxcnc as F
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import LinuxCncAdapter, MockAdapter
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import PROFILES, load_profile
from bettercnc.state import LinkHealth, Severity

C = F.CONSTANTS
LINK_PINS = {
    LinkHealth.HAL_PACKET_ERROR_TOTAL: 0,
    LinkHealth.HAL_WATCHDOG_HAS_BIT: False,
    LinkHealth.HAL_IO_ERROR: False,
}
#: Every action Session.act knows besides the estop, with a value it takes
#: (test_server_and_iniload.TestSessionLocking.LOCKED checks that list
#: against Session.act's source).
ACTIONS = (
    ("software_reset", ""), ("home_all", ""), ("jog", "X:1"), ("mdi", "G0 X1"),
    ("mdi_preset", "G0 X0 Y0"), ("program_start", ""), ("program_stop", ""),
    ("jog_step", "0.1"), ("feed", "100"), ("wcs", "G55"),
)


def esc(text: str) -> str:
    """As render.e writes text into the page (quote=True: ' -> &#x27;)."""
    return htmllib.escape(text, quote=True)


class _Controls(HTMLParser):
    """Every <button> and <input> of a rendered body: (tag, attributes)."""

    def __init__(self) -> None:
        super().__init__()
        self.found: List[tuple] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("button", "input"):
            self.found.append((tag, dict(attrs)))

    @classmethod
    def of(cls, markup: str) -> List[tuple]:
        parser = cls()
        parser.feed(markup)
        return parser.found


def ready_mock(profile_key: str = "test-rig") -> MockAdapter:
    """A mock machine that is ON and homed: every control is live."""
    adapter = MockAdapter(load_profile(profile_key))
    adapter.software_estop_reset()
    adapter.home_all()
    return adapter


def body(profile_key: str, screen: str, adapter, observe: bool) -> str:
    profile = load_profile(profile_key)
    vm = VM.build(profile, load_machine_config(profile), adapter.snapshot(), screen, observe)
    return R.render_body(vm)


class FakeAdapterCase(unittest.TestCase):
    """A LinuxCncAdapter on fresh fakes (fake_linuxcnc), removed after the
    test; halcmd is the fake one, so nothing reaches a real HAL."""

    def make(self, hal_pins: Optional[Dict] = None, observe: bool = False,
             profile=None, hal_available: bool = True) -> LinuxCncAdapter:
        cm = F.installed(
            hal_state=F.FakeHal(LINK_PINS if hal_pins is None else hal_pins),
            hal_available=hal_available,
        )
        _, _, self.machine, self.hal = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.halcmd = F.FakeHalcmd(self.hal)
        self.profile = profile if profile is not None else load_profile("test-rig")
        return LinuxCncAdapter(
            self.profile, halcmd=self.halcmd, proc_root="/nonexistent/proc", observe=observe,
        )


# ---------------------------------------------------------------------------
# Observer mode: what leaves the UI
# ---------------------------------------------------------------------------


class _Recorder(object):
    """Adapter stand-in recording each call Session makes."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append(name)


class TestObserveSession(unittest.TestCase):
    def session(self, observe: bool):
        profile = load_profile("test-rig")
        return app.Session(_Recorder(), profile, load_machine_config(profile), observe=observe)

    def test_only_the_estop_reaches_the_adapter(self):
        sess = self.session(observe=True)
        for action, value in ACTIONS + (("nonsense", ""),):
            sess.act(action, value)
        self.assertEqual(sess.adapter.calls, [])
        sess.act("software_estop", "")
        self.assertEqual(sess.adapter.calls, ["software_estop"])

    def test_without_observe_the_same_actions_do_reach_it(self):
        """Premise: the recorder sees what Session dispatches."""
        sess = self.session(observe=False)
        for action, value in ACTIONS:
            sess.act(action, value)
        self.assertEqual(len(sess.adapter.calls), len(ACTIONS))

    def test_build_session_hands_the_flag_to_both(self):
        self.assertTrue(app.build_session("test-rig", "mock", None, True).observe)
        self.assertFalse(app.build_session("test-rig", "mock").observe)
        with F.installed():
            sess = app.build_session("test-rig", "linuxcnc", None, True)
        self.assertTrue(sess.observe)
        self.assertTrue(sess.adapter._observe)

    def test_the_view_carries_the_mode(self):
        sess = app.build_session("test-rig", "mock", None, True)
        observe = sess.view("kontrol")["observe"]
        self.assertEqual(observe, {"active": True, "banner": VM.OBSERVE_BANNER,
                                   "detail": VM.OBSERVE_DETAIL})
        self.assertFalse(app.build_session("test-rig").view("kontrol")["observe"]["active"])


class TestObserveRealAdapter(FakeAdapterCase):
    def test_every_action_through_the_session_sends_only_the_estop(self):
        adapter = self.make(observe=True)
        self.machine.power_on(homed=True)
        profile = self.profile
        sess = app.Session(adapter, profile, load_machine_config(profile), observe=True)
        for action, value in ACTIONS:
            sess.act(action, value)
            sess.view("kontrol")
        self.assertEqual(self.machine.log, [])
        sess.act("software_estop", "")
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])
        self.assertEqual(self.halcmd.calls, [])

    def test_the_adapter_refuses_its_actions_itself(self):
        """For a caller that does not go through Session."""
        adapter = self.make(observe=True)
        self.machine.power_on(homed=True)
        self.hal.pins[LinkHealth.HAL_IO_ERROR] = True  # the reset would run halcmd
        calls = (
            ("software_estop_reset", adapter.software_estop_reset),
            ("home_all", adapter.home_all),
            ("jog", lambda: adapter.jog("X", 1)),
            ("load_program", lambda: adapter.load_program("/tmp/a.ngc")),
            ("program_start", adapter.program_start),
            ("program_stop", adapter.program_stop),
            ("set_feed_override", lambda: adapter.set_feed_override(100)),
        )
        for name, call in calls:
            with self.subTest(action=name):
                self.assertIsNone(call())
                self.assertEqual(adapter._message, A.OBSERVE_REFUSAL)
        for name, call in (("send_mdi", lambda: adapter.send_mdi("G0 X1")),
                           ("set_wcs", lambda: adapter.set_wcs("G55"))):
            with self.subTest(action=name):
                call()
                self.assertFalse(adapter._mdi[-1].accepted)
                self.assertEqual(adapter._mdi[-1].note, A.OBSERVE_REFUSAL)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(self.halcmd.calls, [])
        self.assertEqual(self.machine.stat_polls, 2)  # the startup probe's only

    def test_every_guarded_action_carries_the_observer_guard(self):
        """An action added later with only @_refused_without_status would
        send from an observer."""
        source = inspect.getsource(LinuxCncAdapter)
        guarded = re.findall(r"(\n[ \t]*@\w+)?\n[ \t]*@_refused_without_status\n", source)
        self.assertGreaterEqual(len(guarded), 8)
        for above in guarded:
            with self.subTest(decorator_above=above.strip()):
                self.assertEqual(above.strip(), "@_not_while_observing")
        self.assertNotIn("@_not_while_observing\n    def software_estop(", source)

    def test_the_software_estop_still_goes_out_on_its_own_channel(self):
        adapter = self.make(observe=True)
        self.machine.power_on(homed=True)
        adapter.software_estop()
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])
        self.assertEqual(self.machine.log_channel, [1])  # the second channel opened
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])

    def test_linuxcnc_error_messages_stay_with_axis(self):
        adapter = self.make(observe=True)
        queued = [(C["OPERATOR_ERROR"], "joint 0 on limit switch error"),
                  (C["OPERATOR_TEXT"], "text")]
        self.machine.errors.extend(queued)
        for _ in range(3):
            snap = adapter.snapshot()
        self.assertEqual(list(self.machine.errors), queued)
        self.assertFalse(any("limit switch" in a.text for a in snap.alarms))
        self.assertNotIn("LinuxCNC:", snap.message)

    def test_premise_the_normal_adapter_drains_them(self):
        adapter = self.make()
        self.machine.errors.append((C["OPERATOR_ERROR"], "joint 0 on limit switch error"))
        adapter.snapshot()
        self.assertEqual(list(self.machine.errors), [])

    def test_the_link_fault_estop_stays_and_names_a_restart(self):
        """On the real driver a cable pull raises io_error alone: the HAL
        latch never trips, and this UI is what stops the machine."""
        adapter = self.make(observe=True)
        self.machine.power_on(homed=True)
        self.hal.pins[LinkHealth.HAL_IO_ERROR] = True
        snap = adapter.snapshot()
        self.assertEqual(self.machine.calls(), ["state"])
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])
        self.assertEqual(snap.message, "{}. {}".format(A.LINK_LOST, A.OBSERVE_LINK_ACTION))
        self.assertIn("LinuxCNC'yi yeniden başlatın", snap.message)
        self.assertNotIn("Yazılım sıfırla", snap.message)

    def test_a_stop_from_axis_names_axis_not_the_reset_button(self):
        adapter = self.make(observe=True)
        self.machine.power_on(homed=True)
        adapter.snapshot()
        self.machine.traj_enabled = False  # switched off in AXIS
        snap = adapter.snapshot()
        self.assertEqual(
            snap.message,
            "Makine kapatıldı — komut bu ekrandan gelmedi. " + A.OBSERVE_RESET_ACTION,
        )
        self.assertNotIn("Yazılım sıfırla", snap.message)


# ---------------------------------------------------------------------------
# Observer mode: what the screens show
# ---------------------------------------------------------------------------


class TestObserveScreens(unittest.TestCase):
    ALLOWED = ("screen", "software_estop")

    def test_every_screen_opens_with_the_banner(self):
        for key in sorted(PROFILES):
            for screen in ALL_SCREENS:
                with self.subTest(profile=key, screen=screen):
                    markup = body(key, screen, ready_mock(key), observe=True)
                    main = markup[markup.index("<main "):markup.index("</main>")]
                    self.assertEqual(main.count('data-observe="1"'), 1)
                    self.assertIn(esc(VM.OBSERVE_BANNER), main)
                    self.assertIn(esc("SALT İZLEME — makine AXIS'ten kullanılıyor"), main)

    def test_no_banner_without_observe(self):
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                markup = body("test-rig", screen, ready_mock(), observe=False)
                self.assertNotIn('data-observe="1"', markup)
                self.assertNotIn(esc(VM.OBSERVE_BANNER), markup)

    def test_only_the_estop_and_the_screen_tabs_stay_enabled(self):
        for key in sorted(PROFILES):
            for screen in ALL_SCREENS:
                with self.subTest(profile=key, screen=screen):
                    controls = _Controls.of(body(key, screen, ready_mock(key), observe=True))
                    self.assertTrue(controls)
                    for tag, attrs in controls:
                        action = attrs.get("data-action", "")
                        if tag == "button" and action in self.ALLOWED:
                            self.assertNotIn("disabled", attrs, action)
                        else:
                            self.assertIn("disabled", attrs, (tag, action, attrs))

    def test_premise_the_same_ready_machine_enables_them_without_observe(self):
        controls = _Controls.of(body("test-rig", "kontrol", ready_mock(), observe=False))
        enabled = {a.get("data-action") for t, a in controls if "disabled" not in a}
        for action in ("software_estop", "home_all", "jog", "wcs", "jog_step",
                       "mdi_submit", "mdi_preset", "feed"):
            with self.subTest(action=action):
                self.assertIn(action, enabled)
        self.assertIn("software_estop", {
            a.get("data-action") for t, a in _Controls.of(
                body("test-rig", "kontrol", ready_mock(), observe=True))
            if "disabled" not in a
        })
        program = _Controls.of(body("test-rig", "program", ready_mock(), observe=False))
        self.assertIn("program_stop", {a.get("data-action") for t, a in program
                                       if "disabled" not in a})

    def test_each_disabled_control_says_why(self):
        vm = VM.build(load_profile("test-rig"), load_machine_config(load_profile("test-rig")),
                      ready_mock().snapshot(), "kontrol", observe=True)
        c = vm["kontrol"]
        self.assertEqual(c["jog_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual(c["mdi"]["disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual(c["estop"]["reset_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual(c["estop"]["reset_blocked_reason"], "")  # no hardware banner
        self.assertEqual(c["homing"]["home_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual(c["commands_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual({f["reason"] for f in c["feed_overrides"]}, {VM.OBSERVE_REASON})
        self.assertEqual(vm["program"]["start_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual(vm["program"]["stop_disabled_reason"], VM.OBSERVE_REASON)
        markup = R.render_body(vm)
        self.assertNotIn("SIFIRLAMA ETKİSİZ", markup)
        start = markup.index('<span class="card-title">Jog</span>')
        jog = markup[start:markup.index('<section class="card', start)]
        # As text, not as a button's title: once, though the feed chips give it too.
        self.assertEqual(jog.count(esc(VM.OBSERVE_REASON) + "</"), 1)

    def test_a_lost_link_asks_for_a_restart_not_the_disabled_reset(self):
        profile = load_profile("test-rig")
        config = load_machine_config(profile)
        state = ready_mock().snapshot()
        state = state._replace(link=state.link._replace(io_error=True))
        for observe, wanted, unwanted in (
            (True, "LinuxCNC'yi yeniden başlatın", "«Yazılım sıfırla»"),
            (False, "«Yazılım sıfırla»", "LinuxCNC'yi yeniden başlatın"),
        ):
            with self.subTest(observe=observe):
                vm = VM.build(profile, config, state, "makine", observe)
                for problems in (vm["kontrol"]["gate"]["warnings"], vm["makine"]["link_problems"],
                                 vm["link"]["problems"]):
                    joined = " ".join(problems)
                    self.assertIn("Kartla bağlantı koptu", joined)
                    self.assertIn(wanted, joined)
                    self.assertNotIn(unwanted, joined)

    def test_dump_renders_the_observer_page(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = app.main(["--dump", "kontrol", "--observe"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn(esc(VM.OBSERVE_BANNER), out.getvalue())


class TestObserveServeBanner(unittest.TestCase):
    """serve() says it on stderr, before it blocks (TestServe's stub)."""

    def serve(self, observe: bool) -> str:
        self.addCleanup(setattr, app, "ThreadingHTTPServer", app.ThreadingHTTPServer)
        self.addCleanup(setattr, app.Handler, "session", app.Handler.session)

        class Stub(object):
            def __init__(self, address, handler):
                pass

            def serve_forever(self):
                raise KeyboardInterrupt()

            def server_close(self):
                pass

        app.ThreadingHTTPServer = Stub
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            app.serve("test-rig", "127.0.0.1", 18080, "mock", None, None, None, None, None,
                      observe)
        return err.getvalue()

    def test_observe(self):
        self.assertIn(app.OBSERVE_BANNER, self.serve(True))
        self.assertTrue(app.Handler.session.observe)

    def test_normal(self):
        self.assertNotIn("SALT İZLEME", self.serve(False))
        self.assertFalse(app.Handler.session.observe)


# ---------------------------------------------------------------------------
# Live switch inputs (Alarmlar)
# ---------------------------------------------------------------------------


class TestLiveSwitches(FakeAdapterCase):
    def pins(self, pressed=(), missing=()):
        """Link pins plus the five inputs, read where the profile says the
        HAL nets them (profiles.DigitalInput.hal_pin; test_inputmap_and_link
        ties those to test-rig.hal)."""
        values = dict(LINK_PINS)
        for inp in load_profile("test-rig").inputs:
            if inp.pin not in missing:
                values[inp.hal_pin] = inp.pin in pressed
        return values

    def alarms(self, adapter) -> Dict[str, dict]:
        vm = VM.alarms_screen(self.profile, adapter.snapshot())
        return {row["pin"]: row for row in vm["inputs"]}

    def test_pressed_and_free_are_shown(self):
        adapter = self.make(self.pins(pressed=("gpio.021",)))
        snap = adapter.snapshot()
        self.assertEqual(
            snap.input_values,
            tuple((inp.hal_pin, inp.pin == "gpio.021") for inp in self.profile.inputs),
        )
        rows = self.alarms(adapter)
        self.assertEqual(rows["gpio.021"]["state"], "pressed")
        self.assertEqual(rows["gpio.021"]["state_label"], "basılı")
        self.assertEqual(rows["gpio.021"]["state_severity"], Severity.WARN.value)
        for pin in ("gpio.020", "gpio.022", "gpio.023", "gpio.024"):
            with self.subTest(pin=pin):
                self.assertEqual(rows[pin]["state"], "free")
                self.assertEqual(rows[pin]["state_label"], "serbest")
        vm = VM.build(self.profile, load_machine_config(self.profile), snap, "alarmlar")
        markup = R.render_body(vm)
        self.assertIn('data-pin="gpio.021" data-readonly="1" data-state="pressed"', markup)
        self.assertEqual(markup.count('data-state="free"'), 4)
        self.assertIn(esc(vm["alarmlar"]["inputs_live_note"]), markup)
        self.assertTrue(vm["alarmlar"]["inputs_live"])

    def test_the_state_follows_the_pin_from_one_snapshot_to_the_next(self):
        adapter = self.make(self.pins())
        self.assertEqual(self.alarms(adapter)["gpio.024"]["state"], "free")
        self.hal.pins["hm2_7i92.0.gpio.024.in_not"] = True
        self.assertEqual(self.alarms(adapter)["gpio.024"]["state"], "pressed")

    def test_an_unreadable_pin_is_unknown_not_free(self):
        adapter = self.make(self.pins(missing=("gpio.022",)))
        rows = self.alarms(adapter)
        self.assertEqual(rows["gpio.022"]["state"], "unknown")
        self.assertEqual(rows["gpio.022"]["state_label"], "okunamadı")
        self.assertEqual(rows["gpio.022"]["state_severity"], Severity.WARN.value)
        self.assertEqual(rows["gpio.023"]["state"], "free")

    def test_without_the_hal_module_every_input_is_unknown(self):
        adapter = self.make(hal_available=False)
        self.assertEqual({r["state"] for r in self.alarms(adapter).values()}, {"unknown"})

    def test_the_inputs_are_still_read_while_the_status_is_lost(self):
        """HAL pins, like the link: they keep their meaning."""
        adapter = self.make(self.pins(pressed=("gpio.020",)))
        self.machine.channels_valid = False
        snap = adapter.snapshot()
        self.assertFalse(snap.status_known)
        self.assertIs(snap.input_value("hm2_7i92.0.gpio.020.in_not"), True)

    def test_the_mock_claims_nothing(self):
        """No HAL behind the mock: every input reads "—", never "serbest"."""
        profile = load_profile("test-rig")
        snap = MockAdapter(profile).snapshot()
        self.assertEqual(snap.input_values, ())
        vm = VM.build(profile, load_machine_config(profile), snap, "alarmlar")
        self.assertEqual({r["state"] for r in vm["alarmlar"]["inputs"]}, {"unread"})
        self.assertFalse(vm["alarmlar"]["inputs_live"])
        markup = R.render_body(vm)
        self.assertNotIn("serbest", markup)
        self.assertNotIn("basılı", markup)
        self.assertIn(esc(vm["alarmlar"]["inputs_live_note"]), markup)

    def test_only_the_profile_pins_and_the_timing_are_read(self):
        adapter = self.make(self.pins())
        read = []
        original = adapter._hal.get_value

        def spy(name):
            read.append(name)
            return original(name)

        adapter._hal.get_value = spy
        adapter.snapshot()
        expected = list(A.HAL_LINK_PINS) + [i.hal_pin for i in self.profile.inputs]
        expected += list(A._stepgen_timing_names(load_machine_config(self.profile)))
        self.assertEqual(read, expected)


# ---------------------------------------------------------------------------
# Live step timing (Makine)
# ---------------------------------------------------------------------------


def _ini_timing() -> Dict[str, int]:
    """The rig's timing straight off test-rig.ini (helpers' own parser)."""
    raw = ini_raw_values()
    return {p: int(float(raw["JOINT_0." + p.upper()])) for p in A.STEPGEN_TIMING_PARAMS}


def _ns(value: int) -> str:
    return "{:,}".format(value).replace(",", " ")


class TestLiveStepTiming(FakeAdapterCase):
    def setUp(self):
        raw = ini_raw_values()
        self.count = int(re.search(r"num_stepgens=(\d+)", raw["HOSTMOT2.CONFIG"]).group(1))
        self.board = "hm2_{}.0".format(raw["HOSTMOT2.BOARD"])
        self.ini = _ini_timing()

    def params(self, **override) -> Dict[str, Optional[int]]:
        """Every stepgen's four parameters at the INI's values; override
        with stepgen_param=value (None leaves the parameter out)."""
        values = dict(LINK_PINS)
        for n in range(self.count):
            for param, ns in self.ini.items():
                key = "{}_{:02d}".format(param, n)
                value = override.get(key, ns)
                if value is not None:
                    values["{}.stepgen.{:02d}.{}".format(self.board, n, param)] = value
        return values

    def rows(self, adapter) -> Dict[str, dict]:
        vm = VM.machine_screen(self.profile, load_machine_config(self.profile), adapter.snapshot())
        return {r["label"]: r for r in vm["timing_live_rows"]}

    def test_names_follow_the_ini(self):
        """num_stepgens=4 on the rig: Y's second motor is stepgen.03."""
        names = A._stepgen_timing_names(load_machine_config(load_profile("test-rig")))
        self.assertEqual(len(names), 4 * self.count)
        self.assertEqual(self.count, 4)
        self.assertEqual(names[0], "{}.stepgen.00.steplen".format(self.board))
        self.assertEqual(names[-1], "{}.stepgen.{:02d}.dirhold".format(self.board, self.count - 1))

    def test_no_count_in_the_ini_reads_none(self):
        raw = ini_raw_values()["HOSTMOT2.CONFIG"]
        profile = rig_profile(self, {("HOSTMOT2", "CONFIG"): re.sub(r"\s*num_stepgens=\d+", "", raw)})
        self.assertEqual(A._stepgen_timing_names(load_machine_config(profile)), ())

    def test_values_matching_the_ini_read_as_one(self):
        rows = self.rows(self.make(self.params()))
        self.assertEqual(list(rows), list(A.STEPGEN_TIMING_PARAMS))
        for param, ns in self.ini.items():
            with self.subTest(param=param):
                self.assertEqual(rows[param]["value"], "{} ns".format(_ns(ns)))
                self.assertEqual(rows[param]["severity"], Severity.INFO.value)
                self.assertEqual(rows[param]["note"], "")
                self.assertEqual(rows[param]["hal"], "{}.stepgen.00–{:02d}.{}".format(
                    self.board, self.count - 1, param))

    def test_a_stepgen_set_differently_is_called_out(self):
        changed = self.ini["steplen"] * 2
        rows = self.rows(self.make(self.params(steplen_03=changed)))
        row = rows["steplen"]
        self.assertEqual(row["severity"], Severity.WARN.value)
        self.assertEqual(row["value"], "00–02: {} · 03: {} ns".format(
            _ns(self.ini["steplen"]), _ns(changed)))
        self.assertIn("aynı değerde değil", row["note"])
        self.assertIn("INI'de {} ns".format(_ns(self.ini["steplen"])), row["note"])
        self.assertEqual(rows["dirhold"]["severity"], Severity.INFO.value)

    def test_all_changed_alike_still_differs_from_the_ini(self):
        """A halcmd setp on every stepgen since start (the runtime test of
        2026-11-12 did that with longer pulses)."""
        changed = self.ini["dirsetup"] + 10000
        override = {"dirsetup_{:02d}".format(n): changed for n in range(self.count)}
        row = self.rows(self.make(self.params(**override)))["dirsetup"]
        self.assertEqual(row["value"], "{} ns".format(_ns(changed)))
        self.assertEqual(row["severity"], Severity.WARN.value)
        self.assertEqual(row["note"], "INI'de {} ns".format(_ns(self.ini["dirsetup"])))

    def test_an_unreadable_parameter_is_named(self):
        row = self.rows(self.make(self.params(stepspace_02=None)))["stepspace"]
        self.assertEqual(row["severity"], Severity.WARN.value)
        ns = _ns(self.ini["stepspace"])
        self.assertEqual(row["value"], "00–01: {} · 02: — · 03: {} ns".format(ns, ns))
        self.assertEqual(row["note"], "okunamadı: stepgen.02")

    def test_the_makine_card_shows_both(self):
        adapter = self.make(self.params())
        vm = VM.build(self.profile, load_machine_config(self.profile), adapter.snapshot(), "makine")
        markup = R.render_body(vm)
        self.assertIn("Canlı (HAL)", markup)
        self.assertIn("{}.stepgen.00–{:02d}.steplen".format(self.board, self.count - 1), markup)
        self.assertIn(esc(vm["makine"]["timing_live_note"]), markup)

    def test_the_mock_shows_none_and_says_so(self):
        profile = load_profile("test-rig")
        vm = VM.build(profile, load_machine_config(profile), MockAdapter(profile).snapshot(),
                      "makine")
        self.assertEqual(vm["makine"]["timing_live_rows"], [])
        markup = R.render_body(vm)
        self.assertIn("Canlı (HAL)", markup)
        self.assertIn(esc(vm["makine"]["timing_live_note"]), markup)
        self.assertIn("num_stepgens", vm["makine"]["timing_live_note"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
