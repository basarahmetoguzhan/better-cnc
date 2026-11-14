"""Plan WP03: the stop rail - ACİL STOP and DURDUR on every screen - and
DURDUR (stop = abort) itself.

On the test rig the screen's ACİL STOP is the only estop
(configs/test-rig/test-rig.hal, "ESTOP CHAIN - GUI ONLY") and DURDUR the
only way short of it to stop a jog, a homing, an MDI line or a program.
Until WP03 the estop was a small outlined button on the Kontrol screen
alone, DURDUR was on the Program screen alone, both fired on a click that
the 1 Hz re-render could swallow, and the stop queued behind the session
lock. Now:

  - render_page puts the rail outside #app, the part the poll replaces;
  - app.js posts its buttons on pointerdown (data-instant), holds a
    re-render while a pointer is down, and binds Esc (DURDUR, as AXIS does)
    and Pause (ACİL STOP);
  - app.Session sends DURDUR's first abort without its lock
    (adapter.stop_motion, on a command channel of its own) and the second
    under it (adapter.stop_followup); "program_stop" goes the same way;
  - a motion action that a stop overtakes sends nothing: the adapter checks
    its stop count right before the motion command (_stopped_since), and
    the Session drops one still waiting for the lock;
  - the estop card no longer says the estop cuts drive power where no
    hardware estop input exists.

LinuxCncAdapter is tested against fake_linuxcnc (test_linuxcnc_adapter.py
ties the fake to the LinuxCNC source); app.js has no JS runtime here, so
its handlers are pinned as text, in the style of test_app_cli.
"""

from __future__ import annotations

import contextlib
import html as htmllib
import io
import os
import re
import threading
import time
import unittest
from html.parser import HTMLParser
from typing import Callable, Dict, List

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS, REPO_ROOT

import fake_linuxcnc as F
# The module, not its names: a TestCase class imported by name would run
# here a second time. Only its FakeCase base is reused.
import test_linuxcnc_adapter as T
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.app import Session
from bettercnc.capabilities import Capability, present
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import PROFILES, load_profile
from bettercnc.state import HomingPhase, Severity

C = F.CONSTANTS
WAIT_S = 5.0
CSS = app._read_static("app.css")
JS = app._read_static("app.js")


def esc(text: str) -> str:
    return htmllib.escape(text, quote=True)


def wait_for(cond: Callable[[], bool], what: str, timeout: float = WAIT_S) -> None:
    """Poll `cond` until true; fail naming `what` after `timeout` s."""
    deadline = time.monotonic() + timeout
    while not cond():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for " + what)
        time.sleep(0.001)


def vm_for(profile_key: str = "test-rig", screen: str = "kontrol", observe: bool = False,
           state=None) -> Dict:
    profile = load_profile(profile_key)
    state = state if state is not None else MockAdapter(profile).snapshot()
    return VM.build(profile, load_machine_config(profile), state, screen, observe)


class _Buttons(HTMLParser):
    """Every <button> of some markup: its attributes, and its text."""

    def __init__(self) -> None:
        super().__init__()
        self.found: List[Dict] = []
        self._open = None

    def handle_starttag(self, tag, attrs):
        if tag == "button":
            self._open = dict(attrs)
            self._open["_text"] = ""
            self.found.append(self._open)

    def handle_data(self, data):
        if self._open is not None:
            self._open["_text"] += data

    def handle_endtag(self, tag):
        if tag == "button":
            self._open = None

    @classmethod
    def of(cls, markup: str) -> List[Dict]:
        parser = cls()
        parser.feed(markup)
        return parser.found


def rail_of(page: str) -> str:
    start = page.index('<aside id="rail"')
    return page[start:page.index("</aside>", start) + len("</aside>")]


# ---------------------------------------------------------------------------
# (1) The rail: on every page, outside #app
# ---------------------------------------------------------------------------


class TestRailMarkup(unittest.TestCase):

    def test_the_rail_is_on_every_page_outside_what_the_poll_replaces(self):
        for key in sorted(PROFILES):
            for screen in ALL_SCREENS:
                with self.subTest(profile=key, screen=screen):
                    vm = vm_for(key, screen)
                    page = R.render_page(vm, CSS, JS)
                    body = R.render_body(vm)
                    self.assertEqual(page.count('<aside id="rail"'), 1)
                    rail = rail_of(page)
                    # before #app and not inside it: the poll swaps #app only
                    self.assertLess(page.index(rail), page.index('<div id="app">'))
                    self.assertIn('<div id="app">{}</div>'.format(body), page)
                    self.assertNotIn('id="rail"', body)
                    buttons = {b["data-action"]: b for b in _Buttons.of(rail)}
                    self.assertEqual(set(buttons), {"software_estop", "stop"})
                    for action, b in buttons.items():
                        self.assertEqual(b.get("data-instant"), "1", action)
                        self.assertEqual(b.get("type"), "button", action)
                        self.assertNotIn("disabled", b, action)
                    self.assertEqual(buttons["software_estop"]["_text"], "ACİL STOP")
                    self.assertEqual(buttons["stop"]["_text"], "DURDUR")

    def test_only_the_rail_fires_on_the_press(self):
        """data-instant is the rail's alone (and the overlay's, built by
        app.js): a jog or a start must not fire on pointerdown."""
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                vm = vm_for("test-rig", screen)
                self.assertNotIn("data-instant", R.render_body(vm))
                self.assertNotIn('data-action="software_estop"', R.render_body(vm))

    def test_the_kontrol_card_keeps_its_rows_and_the_reset_but_no_estop_button(self):
        markup = R.render_body(vm_for("test-rig", "kontrol"))
        self.assertIn('data-estop="drive-power"', markup)
        self.assertIn('data-estop="software"', markup)
        self.assertIn('data-action="software_reset"', markup)
        self.assertNotIn("Yazılım acil stop</button>", markup)
        self.assertIn("Acil stop: sağdaki şeritte, her ekranda.", markup)
        # «Yazılım sıfırla» is a small chip, not a button the size of an estop
        reset = next(b for b in _Buttons.of(markup) if b["data-action"] == "software_reset")
        self.assertIn("chip", reset["class"])

    def test_the_hint_names_the_keys_and_the_band_waits_hidden(self):
        rail = R.render_rail(vm_for())
        self.assertEqual(VM.stop_rail()["hint"], "Esc = Durdur · Pause = Acil stop")
        self.assertIn('<div class="rail-hint"><span class="rail-key">Esc = Durdur</span>'
                      '<span class="rail-key">Pause = Acil stop</span></div>', rail)
        self.assertIn('<div class="rail-sent" id="{}" role="status" hidden></div>'.format(
            R.RAIL_SENT_ID), rail)
        self.assertIn('getElementById("{}")'.format(R.RAIL_SENT_ID), JS)

    def test_the_dump_page_carries_it_too(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = app.main(["--dump", "program"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(out.getvalue().count('data-action="stop" data-instant="1"'), 1)


class TestRailLayout(unittest.TestCase):
    """CSS, as text: no layout engine runs here (docs/11, "CSS and visual
    appearance"); the sizes are the plan's floor, the browser check of
    2026-11-13 (docs/11) the layout."""

    def rule(self, selector: str, css: str = CSS) -> str:
        m = re.search(r"(?m)^\s*" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m, selector)
        return m.group(1)

    def px(self, rule: str, prop: str) -> float:
        m = re.search(r"(?<![-\w])" + re.escape(prop) + r":\s*(\d+(?:\.\d+)?)px", rule)
        self.assertIsNotNone(m, (prop, rule))
        return float(m.group(1))

    def test_fixed_at_the_edge_and_app_keeps_clear_of_it(self):
        rail = self.rule(".rail")
        self.assertIn("position: fixed", rail)
        self.assertIn("right: 0", rail)
        self.assertIn("width: var(--rail-w)", rail)
        self.assertIn("margin-right: var(--rail-w)", self.rule("#app"))
        self.assertGreaterEqual(self.px(self.rule(":root", CSS[CSS.index("the stop rail"):]),
                                        "--rail-w"), 112)

    def test_the_buttons_are_at_least_the_plans_size_also_on_a_narrow_screen(self):
        narrow = CSS[CSS.index("@media (max-width: 700px)"):]
        narrow = narrow[:narrow.index("\n}\n")]
        for css, label in ((CSS, "wide"), (narrow, "narrow")):
            with self.subTest(screen=label):
                both = self.rule(".rail-estop, .rail-stop", css)
                estop = self.rule(".rail-estop", css)
                stop = self.rule(".rail-stop", css)
                self.assertGreaterEqual(self.px(both, "width"), 96)
                self.assertGreaterEqual(self.px(estop, "height"), 96)
                self.assertGreaterEqual(self.px(stop, "height"), 72)

    def test_the_buttons_fit_inside_the_rail_so_it_never_scrolls_sideways(self):
        """Review of plan WP03: 112 px buttons in a 132 px rail with 10 px
        padding and a 1 px border (box-sizing: border-box) left 111 px, and
        96 in 112 - 21 on a narrow screen; overflow-y: auto made the rail
        scroll sideways (Chrome: scrollWidth 132 > clientWidth 131)."""
        rail = self.rule(".rail")
        self.assertIn("overflow-x: hidden", rail)
        pad = re.search(r"(?<![-\w])padding:\s*(\d+)px\s+(\d+)px", rail)
        self.assertIsNotNone(pad, rail)
        border = re.search(r"border-left:\s*(\d+)px", rail)
        self.assertIsNotNone(border, rail)
        inner_extra = 2 * float(pad.group(2)) + float(border.group(1))
        self.assertIn("box-sizing: border-box", CSS)
        narrow = CSS[CSS.index("@media (max-width: 700px)"):]
        narrow = narrow[:narrow.index("\n}\n")]
        for css, label in ((CSS[CSS.index("the stop rail"):], "wide"), (narrow, "narrow")):
            with self.subTest(screen=label):
                rail_w = self.px(self.rule(":root", css), "--rail-w")
                button_w = self.px(self.rule(".rail-estop, .rail-stop", css), "width")
                self.assertGreaterEqual(rail_w - inner_extra, button_w)

    def test_estop_is_solid_red_and_durdur_amber(self):
        self.assertIn("background: #c62828", self.rule(".rail-estop"))
        self.assertIn("background: var(--warn)", self.rule(".rail-stop"))

    def test_only_the_connection_overlay_covers_it(self):
        """The overlay has an estop of its own (app.js showLost)."""
        z = int(re.search(r"z-index:\s*(\d+)", self.rule(".rail")).group(1))
        lost = int(re.search(r"z-index:\s*(\d+)", self.rule(".lost")).group(1))
        observe = int(re.search(r"z-index:\s*(\d+)", self.rule(".observe")).group(1))
        self.assertGreater(lost, z)
        self.assertLess(observe, z)


# ---------------------------------------------------------------------------
# (2) Observer mode: DURDUR off with its reason as text, the estop never
# ---------------------------------------------------------------------------


class TestRailObserve(unittest.TestCase):

    def test_durdur_is_off_with_a_visible_reason_and_the_estop_stays(self):
        rail = R.render_rail(vm_for(observe=True))
        buttons = {b["data-action"]: b for b in _Buttons.of(rail)}
        self.assertIn("disabled", buttons["stop"])
        self.assertNotIn("disabled", buttons["software_estop"])
        self.assertNotIn("title=", rail)    # the kiosk strips titles: text only
        self.assertIn('<div class="rail-why">{}</div>'.format(esc(VM.OBSERVE_STOP_REASON)), rail)
        self.assertIn(">Pause = Acil stop<", rail)
        self.assertNotIn("Esc", rail)       # Esc sends nothing in observer mode

    def test_status_unknown_disables_neither(self):
        """Neither button depends on a reading: the abort and the estop go
        out without one (LinuxCncAdapter.stop_motion, software_estop)."""
        state = MockAdapter(load_profile("test-rig")).snapshot().as_status_unknown()
        rail = R.render_rail(vm_for(state=state))
        self.assertNotIn("disabled", rail)

    def test_the_view_model_says_the_same(self):
        self.assertEqual(VM.stop_rail(False)["stop_enabled"], True)
        self.assertEqual(VM.stop_rail(False)["stop_disabled_reason"], "")
        self.assertEqual(VM.stop_rail(True)["stop_enabled"], False)
        self.assertEqual(vm_for(observe=True)["rail"], VM.stop_rail(True))


# ---------------------------------------------------------------------------
# (7) The estop note says what the screen's estop does
# ---------------------------------------------------------------------------


def _wired_profile():
    profile = load_profile("test-rig")
    caps = dict(profile.capabilities)
    caps[Capability.ESTOP_INPUT] = present("test_stop_rail.py: hypothetical wired input")
    return profile._replace(capabilities=caps)


class TestEstopNote(unittest.TestCase):

    def test_without_an_estop_input_it_says_software_only(self):
        p = VM.estop_panel(MockAdapter(load_profile("test-rig")).snapshot())
        self.assertFalse(p["hardware_input_present"])  # the rig: GUI-only chain
        self.assertNotIn("DONANIMSAL keser", p["note"])
        self.assertEqual(p["note"], VM.ESTOP_NOTE_SOFTWARE_ONLY)
        self.assertIn("yalnız yazılımdır", p["note"])
        self.assertIn("motor gücünü KESMEZ", p["note"])
        self.assertIn("güç anahtarı", p["note"])
        markup = R.render_body(vm_for("test-rig", "kontrol"))
        self.assertNotIn("DONANIMSAL keser", markup)
        self.assertIn(esc(VM.ESTOP_NOTE_SOFTWARE_ONLY), markup)

    def test_the_marble_profile_has_none_wired_either(self):
        p = VM.estop_panel(MockAdapter(load_profile("marble")).snapshot())
        self.assertEqual(p["note"], VM.ESTOP_NOTE_SOFTWARE_ONLY)

    def test_a_profile_with_a_wired_input_keeps_the_hardware_text(self):
        p = VM.estop_panel(MockAdapter(_wired_profile()).snapshot())
        self.assertTrue(p["hardware_input_present"])
        self.assertEqual(p["note"], VM.ESTOP_NOTE_WIRED)
        self.assertIn("DONANIMSAL keser", p["note"])


# ---------------------------------------------------------------------------
# (5), (6) Session: observer drops DURDUR; program_stop is DURDUR
# ---------------------------------------------------------------------------


class _Recorder(object):
    def __init__(self) -> None:
        self.calls: List[str] = []

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append(name)


def _session(adapter, observe: bool = False) -> app.Session:
    profile = load_profile("test-rig")
    return app.Session(adapter, profile, load_machine_config(profile), observe=observe)


class TestSessionRouting(unittest.TestCase):

    def test_observer_mode_drops_durdur_unseen(self):
        sess = _session(_Recorder(), observe=True)
        sess.act("stop", "")
        sess.act("program_stop", "")
        self.assertEqual(sess.adapter.calls, [])
        sess.act("software_estop", "")
        self.assertEqual(sess.adapter.calls, ["software_estop"])

    def test_both_names_are_the_same_stop(self):
        for action in ("stop", "program_stop"):
            with self.subTest(action=action):
                sess = _session(_Recorder())
                sess.act(action, "")
                self.assertEqual(sess.adapter.calls, ["stop_motion", "stop_followup"])

    def test_on_the_mock_both_stop_a_program(self):
        for action in ("stop", "program_stop"):
            with self.subTest(action=action):
                sess = app.build_session("test-rig")
                sess.act("software_reset", "")
                sess.act("home_all", "")
                sess.adapter.load_program("a.ngc")
                sess.act("program_start", "")
                self.assertTrue(sess.adapter.snapshot().program_running)
                sess.act(action, "")
                snap = sess.adapter.snapshot()
                self.assertFalse(snap.program_running)
                self.assertEqual(snap.message, A.STOPPED)
                self.assertEqual((snap.alarms[0].text, snap.alarms[0].severity),
                                 ("Durdur gönderildi", Severity.INFO))


class _ObservedLock(object):
    """threading.Lock that tells when a thread has to wait for it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.waiting = threading.Event()

    def acquire(self) -> None:
        if not self._lock.acquire(blocking=False):
            self.waiting.set()
            self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


class _HeldAdapter(object):
    """home_all parks on an Event while Session holds its lock; every call
    is recorded."""

    def __init__(self) -> None:
        self.calls: List[str] = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def home_all(self):
        self.calls.append("home_all")
        self.entered.set()
        self.release.wait(WAIT_S)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args: self.calls.append(name)


class TestSessionDropsWhatAStopOvertook(unittest.TestCase):
    """A motion action still waiting for the session lock when DURDUR or the
    estop arrives is dropped: it must not run after the stop's second half
    (nor after an estop and the reset that followed it). Plan WP03's own
    design checks inside the adapter only; this is the queue in front of
    it."""

    def run_overtaken(self, queued, overtaker):
        adapter = _HeldAdapter()
        sess = _session(adapter)
        sess._lock = _ObservedLock()
        holder = threading.Thread(target=sess.act, args=("home_all", ""), daemon=True)
        waiter = threading.Thread(target=sess.act, args=queued, daemon=True)
        stopper = threading.Thread(target=sess.act, args=(overtaker, ""), daemon=True)
        for t in (holder, waiter, stopper):
            self.addCleanup(t.join, WAIT_S)
        self.addCleanup(adapter.release.set)       # runs before the joins
        holder.start()
        self.assertTrue(adapter.entered.wait(WAIT_S))
        waiter.start()
        self.assertTrue(sess._lock.waiting.wait(WAIT_S), "the queued action never waited")
        stopper.start()
        first = "stop_motion" if overtaker == "stop" else "software_estop"
        wait_for(lambda: first in adapter.calls, overtaker + " to go out")
        self.assertTrue(waiter.is_alive())
        adapter.release.set()
        for t in (holder, waiter, stopper):
            t.join(WAIT_S)
            self.assertFalse(t.is_alive())
        return adapter.calls

    def test_a_queued_jog_is_dropped_by_durdur(self):
        calls = self.run_overtaken(("jog", "X:1"), "stop")
        self.assertNotIn("jog", calls)
        self.assertEqual(calls.count("stop_followup"), 1)

    def test_a_queued_motion_action_is_dropped_by_the_estop(self):
        for queued in (("home_all", ""), ("mdi", "G0 X1"), ("program_start", ""),
                       ("wcs", "G55"), ("mdi_preset", "M5")):
            with self.subTest(action=queued[0]):
                calls = self.run_overtaken(queued, "software_estop")
                self.assertEqual(calls.count("home_all"), 1)   # the holder's only
                for name in ("send_mdi", "program_start", "set_wcs"):
                    self.assertNotIn(name, calls)

    def test_a_queued_reset_is_dropped_too(self):
        """«Yazılım sıfırla» queued before the estop would switch the machine
        back on right behind it (review of plan WP03): dropped as well, and
        after DURDUR too - the machine then stays off, the safe side."""
        for overtaker in ("software_estop", "stop"):
            with self.subTest(overtaker=overtaker):
                calls = self.run_overtaken(("software_reset", ""), overtaker)
                self.assertNotIn("software_estop_reset", calls)

    def test_premise_without_a_stop_the_queued_reset_runs(self):
        adapter = _HeldAdapter()
        sess = _session(adapter)
        sess._lock = _ObservedLock()
        holder = threading.Thread(target=sess.act, args=("home_all", ""), daemon=True)
        waiter = threading.Thread(target=sess.act, args=("software_reset", ""), daemon=True)
        for t in (holder, waiter):
            self.addCleanup(t.join, WAIT_S)
        self.addCleanup(adapter.release.set)
        holder.start()
        self.assertTrue(adapter.entered.wait(WAIT_S))
        waiter.start()
        self.assertTrue(sess._lock.waiting.wait(WAIT_S), "the queued reset never waited")
        adapter.release.set()
        for t in (holder, waiter):
            t.join(WAIT_S)
            self.assertFalse(t.is_alive())
        self.assertIn("software_estop_reset", adapter.calls)

    def test_a_queued_setting_is_not_dropped(self):
        """Only motion and the reset: a jog step chosen just before DURDUR
        still counts."""
        calls = self.run_overtaken(("jog_step", "0.1"), "stop")
        self.assertIn("set_jog_step", calls)

    def test_premise_without_a_stop_the_queued_jog_runs(self):
        adapter = _HeldAdapter()
        sess = _session(adapter)
        holder = threading.Thread(target=sess.act, args=("home_all", ""), daemon=True)
        holder.start()
        self.addCleanup(holder.join, WAIT_S)
        self.addCleanup(adapter.release.set)
        self.assertTrue(adapter.entered.wait(WAIT_S))
        adapter.release.set()
        holder.join(WAIT_S)
        sess.act("jog", "X:1")
        self.assertIn("jog", adapter.calls)

    def test_the_motion_list_is_what_starts_motion(self):
        # Plan WP05 added «Limitten kurtar», which switches the machine on
        # and jogs (MachineAdapter.recover_limit); plan WP07 one axis's
        # homing and «Evet», which runs the homing it asked about; plan WP09
        # «Devam» and «Adım», which set a program moving again («Duraklat»
        # stops motion: not one).
        self.assertEqual(app.MOTION_ACTIONS,
                         {"home_all", "jog", "mdi", "mdi_preset", "program_start", "wcs",
                          "recover_limit", "home_joint", "confirm",
                          "program_resume", "program_step"})
        self.assertEqual(app.DROPPED_AFTER_A_STOP, app.MOTION_ACTIONS | {"software_reset"})


# ---------------------------------------------------------------------------
# (3), (4) LinuxCncAdapter on the fake: the first abort at once, on its own
# channel; nothing an action would send after it; the second abort
# ---------------------------------------------------------------------------


STOP_CHANNEL = 2   # the third linuxcnc.command(): action 0, estop 1, stop 2


class TestStopOnTheRealAdapter(T.FakeCase):

    def test_stop_goes_out_while_home_all_waits_and_again_once_it_returns(self):
        """(3) home_all holds the session lock inside wait_complete
        (wait_gate); Session.act("stop") from another thread sends abort on
        the stop channel at once, while the lock is still held; home(-1)
        never goes out; once the lock is free the second abort follows on
        the action channel."""
        adapter = self.on(homed=True)    # teleop: home_all leaves it and waits
        now = [100.0]
        sess = Session(adapter, self.profile, load_machine_config(self.profile),
                       clock=lambda: now[0])
        # Homed: plan WP07 asks first, with nothing sent; «Evet» runs it,
        # once it counts (app.CONFIRM_MIN_S after the question).
        sess.act("home_all", "")
        self.assertEqual(self.machine.log, [])
        token = sess.view("kontrol")["confirm"]["token"]
        now[0] += app.CONFIRM_MIN_S
        gate = threading.Event()
        self.machine.wait_gate = gate
        homer = threading.Thread(target=sess.act, args=("confirm", token), daemon=True)
        stopper = threading.Thread(target=sess.act, args=("stop", ""), daemon=True)
        for t in (homer, stopper):
            self.addCleanup(t.join, F.GATE_LIMIT_S)
        self.addCleanup(gate.set)
        homer.start()
        self.assertTrue(self.machine.wait_entered.wait(F.GATE_LIMIT_S))
        self.assertTrue(sess._lock.locked())

        stopper.start()
        wait_for(lambda: ("abort", ()) in self.machine.log, "the first abort")
        first = self.machine.log.index(("abort", ()))
        self.assertEqual(self.machine.log_channel[first], STOP_CHANNEL)
        self.assertTrue(sess._lock.locked(), "the abort waited for the lock")
        self.assertTrue(homer.is_alive() and stopper.is_alive())

        gate.set()
        homer.join(F.GATE_LIMIT_S)
        stopper.join(F.GATE_LIMIT_S)
        self.assertFalse(homer.is_alive() or stopper.is_alive())
        self.assertNotIn("home", self.machine.calls())
        aborts = [ch for (name, _), ch in zip(self.machine.log, self.machine.log_channel)
                  if name == "abort"]
        self.assertEqual(aborts, [STOP_CHANNEL, 0])
        # the second abort is the last thing sent, after home_all returned
        self.assertEqual(self.machine.log[-2:], [("abort", ()), ("wait_complete", (T.WAIT,))])
        self.assertEqual(self.machine.log_channel[-2:], [0, 0])
        snap = adapter.snapshot()
        self.assertEqual(snap.message, A.STOPPED)
        self.assertIn("Durdur gönderildi", [a.text for a in snap.alarms])

    def overtake(self, call, setup) -> None:
        """Run `call` in a thread until it waits in ensure_mode, stop it
        (stop_motion, from this thread, as Session does without its lock),
        let it go."""
        setup()
        gate = threading.Event()
        self.machine.wait_gate = gate
        worker = threading.Thread(target=call, daemon=True)
        self.addCleanup(worker.join, F.GATE_LIMIT_S)
        self.addCleanup(gate.set)
        worker.start()
        self.assertTrue(self.machine.wait_entered.wait(F.GATE_LIMIT_S))
        self.assertEqual(self.machine.calls()[-2:], ["mode", "wait_complete"])
        self.adapter.stop_motion()
        self.assertTrue(worker.is_alive(), "the action was not actually blocked")
        gate.set()
        worker.join(F.GATE_LIMIT_S)
        self.assertFalse(worker.is_alive())

    def test_nothing_is_sent_after_a_stop_that_came_during_ensure_mode(self):
        """(4) Each motion action sends no motion command once a stop came
        while it switched modes: no home(-1), no jog, no auto(RUN), no
        mdi."""
        def mdi_mode():
            self.machine.task_mode = C["MODE_MDI"]
            self.machine.motion_mode = C["TRAJ_MODE_COORD"]

        def loaded():
            self.machine.file = self.ngc()

        cases = (
            # Confirmed: a homed machine is asked about first (plan WP07).
            ("home_all", True, lambda: self.adapter.home_all(confirmed=True), mdi_mode, "home"),
            ("jog", False, lambda: self.adapter.jog("X", 1), mdi_mode, "jog"),
            ("program_start", True, lambda: self.adapter.program_start(), loaded, "auto"),
        )
        for name, homed, call, setup, command in cases:
            with self.subTest(action=name):
                self.adapter = self.on(homed=homed)
                self.overtake(call, setup)
                self.assertNotIn(command, self.machine.calls())
                self.assertEqual(self.adapter.snapshot().message, A.STOP_REFUSAL)
                self.assertIn("Durdur basıldı", A.STOP_REFUSAL)
                self.assertNoLinuxCncErrors()

    def test_an_mdi_line_or_a_wcs_is_refused_as_an_entry(self):
        for name, call, command in (
            ("send_mdi", lambda: self.adapter.send_mdi("G0 X1"), "G0 X1"),
            ("set_wcs", lambda: self.adapter.set_wcs("G55"), "G55"),
        ):
            with self.subTest(action=name):
                self.adapter = self.on(homed=True)
                self.overtake(call, lambda: None)
                self.assertNotIn("mdi", self.machine.calls())
                entry = self.adapter.snapshot().mdi_history[-1]
                self.assertEqual((entry.command, entry.accepted, entry.note),
                                 (command, False, A.STOP_REFUSAL))
                self.assertEqual(self.adapter.snapshot().message,
                                 "MDI: {} — {}".format(command, A.STOP_REFUSAL))

    def test_premise_without_the_stop_the_same_actions_send(self):
        self.adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_MDI"]
        self.adapter.home_all(confirmed=True)
        self.assertIn("home", self.machine.calls())
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X1")
        self.assertIn("mdi", self.machine.calls())

    def test_a_stop_before_the_action_began_does_not_refuse_it(self):
        """The count is taken as the action begins: a stop pressed earlier
        is history, not a reason."""
        adapter = self.on(homed=False)
        adapter.stop_motion()
        adapter.stop_followup()
        adapter.jog("X", 1)
        self.assertIn("jog", self.machine.calls())
        self.assertNotEqual(adapter.snapshot().message, A.STOP_REFUSAL)

    def test_the_software_estop_counts_too_with_its_own_reason(self):
        adapter = self.on(homed=True)
        seq = adapter._stop_seq
        adapter.software_estop()
        self.assertTrue(adapter._stopped_since(seq))
        self.assertEqual(adapter._stop_why, A.ESTOP_REFUSAL)
        adapter.software_estop_reset()
        seq = adapter._stop_seq
        adapter.stop_motion()
        self.assertTrue(adapter._stopped_since(seq))
        self.assertEqual(adapter._stop_why, A.STOP_REFUSAL)

    def reset_with_an_estop_during_its_wait(self, overtaker=None):
        """software_estop_reset from ESTOP, the latch slow to close
        (estop_clear_after_polls): while the reset polls for it, `overtaker`
        (an adapter method name, or None) runs as Session runs it, from
        another thread without the lock - here, at the reset's first sleep."""
        adapter = self.make(machine=F.FakeMachine(estop_clear_after_polls=4))
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP"])
        fired = []

        def sleep(seconds):
            if overtaker and not fired:
                fired.append(overtaker)
                getattr(adapter, overtaker)()
            self.clock.sleep(seconds)
        adapter._sleep = sleep
        adapter.software_estop_reset()
        return adapter

    def test_an_estop_during_the_reset_keeps_the_machine_off(self):
        """Review of plan WP03: the operator's estop landed while the reset
        waited for the latch; on the rig that leaves ESTOP_RESET (the
        software estop does not open its latch), so the wait ended and the
        reset's ON switched the machine back on behind the estop."""
        for overtaker, why in (("software_estop", A.ESTOP_REFUSAL),
                               ("stop_motion", A.STOP_REFUSAL)):
            with self.subTest(overtaker=overtaker):
                adapter = self.reset_with_an_estop_during_its_wait(overtaker)
                self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)
                self.assertNotEqual(self.machine.task_state, C["STATE_ON"])
                self.assertEqual(adapter.snapshot().message, why)
                self.assertNoLinuxCncErrors()

    def test_premise_the_same_reset_without_an_estop_switches_on(self):
        adapter = self.reset_with_an_estop_during_its_wait(None)
        self.assertIn(("state", (C["STATE_ON"],)), self.machine.log)
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertTrue(self.clock.sleeps, "the reset never waited for the latch")
        self.assertNotEqual(adapter.snapshot().message, A.ESTOP_REFUSAL)

    def test_stop_motion_reads_nothing_and_goes_out_without_the_status(self):
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        polls = self.machine.stat_polls
        adapter.stop_motion()
        self.assertEqual(self.machine.stat_polls, polls)
        self.assertEqual(self.machine.log, [("abort", ())])
        self.assertEqual(self.machine.log_channel, [STOP_CHANNEL])

    def test_stop_motion_touches_only_its_channel_message_alarms_and_count(self):
        """The threading contract (LinuxCncAdapter class docstring): it runs
        while an action may run in another thread. Its message is INFO
        (plan WP04): the line's severity changes with it - from the WARN
        of the refused jog step before - and the error box does not."""
        adapter = self.on(homed=True)
        adapter.set_jog_step(0.5)
        adapter.set_jog_step(-1.0)            # refused: a WARN on the line
        self.assertIs(adapter._message_severity, Severity.WARN)

        def state():
            return {k: (list(v) if isinstance(v, list) else v) for k, v in vars(adapter).items()}

        before = state()
        adapter.stop_motion()
        after = state()
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"_message", "_message_severity", "_alarms", "_stop_seq"})
        self.assertEqual(adapter._message, A.STOP_SENT)
        self.assertIs(adapter._message_severity, Severity.INFO)
        self.assertEqual(adapter._sends, 0)   # not the action channel

    def test_stop_ends_a_jog_and_cancels_a_homing_on_the_fake(self):
        """What abort does in motion (fake_linuxcnc FakeMachine.issue_abort,
        checked against command.c and homing.c in test_linuxcnc_adapter)."""
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        self.machine.homed[2] = 1          # Z finished first, as HOME_SEQUENCE has it
        self.machine.homing[0] = self.machine.homing[1] = 1
        self.assertTrue(adapter.snapshot().homing.any_homing)
        adapter.stop_motion()
        self.assertEqual(self.machine.homing[:3], [0, 0, 0])
        self.assertEqual(self.machine.homed[:3], [0, 0, 1])
        adapter.stop_followup()
        snap = adapter.snapshot()
        phases = {j.axis: j.phase for j in snap.homing.joints}
        self.assertEqual(phases, {"X": HomingPhase.NOT_HOMED, "Y": HomingPhase.NOT_HOMED,
                                  "Z": HomingPhase.HOMED})
        self.assertEqual(snap.message, A.STOPPED)

        adapter = self.on(homed=True)
        self.machine.jogging_active = True
        adapter.stop_motion()
        self.assertFalse(self.machine.jogging_active)

    def test_home_all_after_a_stop_starts_afresh(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.assertEqual(self.machine.homing[:3], [0, 0, 1])     # Z first
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(self.machine.home_pending, [])          # Y and X never start
        del self.machine.log[:]
        adapter.home_all()
        self.assertIn(("home", (-1,)), self.machine.log)
        self.assertEqual(self.machine.homing[:3], [0, 0, 1])
        self.assertEqual(self.machine.home_pending, [[1], [0]])

    def test_the_followup_says_stopped_only_once_nothing_moves(self):
        adapter = self.on(homed=True)
        self.machine.current_vel = 2.5       # still braking after the wait
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(adapter.snapshot().message, A.STOP_NOT_IDLE)
        self.machine.current_vel = 0.0
        adapter.stop_followup()
        self.assertEqual(adapter.snapshot().message, A.STOPPED)

    def test_the_followup_stops_an_mdi_line_and_a_program(self):
        adapter = self.on(homed=True)
        self.run_mdi_program()
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(self.machine.interp_state, C["INTERP_IDLE"])
        self.assertEqual(adapter.snapshot().message, A.STOPPED)

    def test_stop_in_estop_is_taken_too(self):
        """EMC_TASK_ABORT is immediate out of ON (emctaskmain.cc:832)."""
        adapter = self.make()
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertNoLinuxCncErrors()
        self.assertEqual(adapter.snapshot().message, A.STOPPED)


# ---------------------------------------------------------------------------
# MockAdapter
# ---------------------------------------------------------------------------


class TestMockStop(unittest.TestCase):

    def test_a_homing_joint_ends_unhomed_and_the_homed_ones_stay(self):
        adapter = MockAdapter(load_profile("test-rig"))
        adapter.software_estop_reset()
        adapter.home_all()
        adapter._homing = adapter._homing.with_phase("X", HomingPhase.HOMING)
        adapter.stop_motion()
        snap = adapter.snapshot()
        phases = {j.axis: j.phase for j in snap.homing.joints}
        self.assertEqual(phases, {"X": HomingPhase.NOT_HOMED, "Y": HomingPhase.HOMED,
                                  "Z": HomingPhase.HOMED})
        self.assertEqual({p.axis: p.homed for p in snap.positions},
                         {"X": False, "Y": True, "Z": True})
        self.assertEqual(snap.message, A.STOP_SENT)
        adapter.stop_followup()
        self.assertEqual(adapter.snapshot().message, A.STOPPED)

    def test_nothing_homing_nothing_unhomed(self):
        adapter = MockAdapter(load_profile("test-rig"))
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.stop_motion()
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_both_halves_take_the_mocks_lock(self):
        for name in ("stop_motion", "stop_followup"):
            with self.subTest(method=name):
                self.assertTrue(hasattr(getattr(MockAdapter, name), "__wrapped__"))


# ---------------------------------------------------------------------------
# (8) app.js, as text
# ---------------------------------------------------------------------------


class TestAppJs(unittest.TestCase):

    def block(self, start: str, end: str) -> str:
        i = JS.index(start)
        return JS[i:JS.index(end, i)]

    def test_rail_buttons_post_on_pointerdown(self):
        down = self.block('document.addEventListener("pointerdown"', "}, true);")
        # only a press inside #app holds the re-render (the rail is not swapped)
        self.assertIn("if (app.contains(ev.target)) pointerPressed(ev);", down)
        self.assertIn('closest("button[data-instant]")', down)
        self.assertIn("if (!btn || btn.disabled) return;", down)
        self.assertIn('fireInstant(btn.getAttribute("data-action"))', down)
        self.assertIn("if (ev.button !== 0) return;", down)

    def test_the_click_after_the_press_is_not_sent_again(self):
        click = self.block('document.addEventListener("click"', "\n  });")
        instant = click[click.index('if (btn.hasAttribute("data-instant"))'):]
        instant = instant[:instant.index("return;") + 7]
        self.assertIn("ev.detail === 0 && Date.now() - lastInstantDown > INSTANT_GUARD_MS",
                      instant)
        self.assertIn("var INSTANT_GUARD_MS = 500;", JS)

    def test_a_swap_waits_for_the_release(self):
        swap = self.block("function swap(data)", "function applySwap(data)")
        self.assertIn("if (pressHeld()) {", swap)
        self.assertIn("pendingSwap = data;", swap)
        self.assertIn('document.addEventListener("pointerup", pointerReleased, true);', JS)
        self.assertIn('document.addEventListener("pointercancel", pointerReleased, true);', JS)
        released = self.block("function pointerReleased(ev)", "\n  }\n")
        self.assertIn("SWAP_AFTER_UP_MS", released)
        self.assertIn("applySwap(data);", released)
        held = self.block("function pressHeld()", "\n  }\n")
        self.assertIn("Date.now() - downSince < SWAP_HOLD_MAX_MS", held)
        # app.innerHTML is written in one place only, and not while held
        self.assertEqual(JS.count("app.innerHTML = "), 1)
        self.assertIn("app.innerHTML = data.html;", self.block("function applySwap(data)",
                                                              "function refresh()"))

    def test_escape_is_durdur_and_pause_the_estop_with_any_focus(self):
        keys = self.block('var action = ev.key === "Escape"', "}, true);")
        self.assertIn('ev.key === "Escape" ? "stop"', keys)
        self.assertIn('ev.key === "Pause" ? "software_estop"', keys)
        self.assertIn("if (ev.repeat) return;", keys)
        # observer mode: the rail's disabled DURDUR means Esc sends nothing
        self.assertIn("var btn = railButton(action);", keys)
        self.assertIn("if (!btn || btn.disabled) return;", keys)
        # on the document, in the capture phase: no field can keep the key
        at = JS.index(keys)
        self.assertTrue(JS[at + len(keys):].startswith("}, true);"))
        self.assertTrue(JS[:at].rstrip().endswith(
            'document.addEventListener("keydown", function (ev) {'))

    def test_the_kiosk_keymap_leaves_both_keys_alone(self):
        with open(os.path.join(REPO_ROOT, "deploy", "files", "xkb", "symbols", "bettercnc"),
                  encoding="utf-8") as fh:
            keymap = fh.read()
        for key in ("<ESC>", "<PAUS>"):
            with self.subTest(key=key):
                self.assertNotIn(key, keymap)

    def test_the_band_says_it_went_until_the_answer_is_on_screen(self):
        fire = self.block("function fireInstant(action)", "\n  }\n")
        self.assertIn('"ACİL STOP GÖNDERİLDİ — onay bekleniyor"', fire)
        self.assertIn('"DURDUR gönderildi"', fire)
        self.assertIn("sentAnswered = token;", fire)
        self.assertIn("hideSent", fire)
        self.assertIn("if (sentAnswered === sentToken) hideSent();",
                      self.block("function applySwap(data)", "function refresh()"))

    def test_the_overlay_estop_fires_on_the_press_too(self):
        lost = self.block("function showLost(", "function connectionOk(")
        self.assertIn('estop.setAttribute("data-instant", "1");', lost)
        self.assertIn('estop.setAttribute("data-action", "software_estop");', lost)
        self.assertNotIn('estop.addEventListener("click"', JS)
        fire = self.block("function fireInstant(action)", "\n  }\n")
        self.assertIn('if (action === "software_estop" && overlay) { sendLostEstop(); return; }',
                      fire)


if __name__ == "__main__":
    unittest.main(verbosity=2)
