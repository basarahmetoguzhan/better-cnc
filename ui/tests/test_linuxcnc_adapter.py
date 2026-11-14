"""LinuxCncAdapter against a fake `linuxcnc`/`hal` built from the 2.9 sources.

Why: LinuxCncAdapter is the UI's only path to the machine and, until this file,
had no tests at all. docs/11-ui-implementation.md calls it "the code most likely
to be wrong on first contact with the real machine". Reading it against the
LinuxCNC source found that it could not jog before homing - exactly when the
operator has to jog: to trace the Y DIR fault, check SCALE with a 10 mm jog
and measure travel, all before any homing (docs/DURUM.md:121-139) - because it
always asked for an axis (teleop) jog, which motion drops in free mode
(command.c:436-443).

The fake (fake_linuxcnc.py) models the task planner and motion closely enough
to reject what LinuxCNC rejects, and reports refusals on the error channel the
way LinuxCNC does. Most tests below therefore assert two things: the exact
command sequence the adapter sent, and that LinuxCNC (the fake) raised no error
about it. The fake also models the timing the adapter's safety rests on:
wait_complete() does not return DONE while motion is moving, and the estop
latch clears only after some polls.

The fidelity tests at the top keep the fake honest: when reference/ (a
LinuxCNC 2.9 clone, gitignored) is present they parse the binding and check
every constant, method name, argument format and error text the fake uses.
Without reference/ they skip, and the rest of the suite still runs.

Source paths in citations are relative to reference/linuxcnc/src/.
"""

from __future__ import annotations

import importlib.util
import inspect
import os
import re
import sys
import tempfile
import threading
import unittest
from typing import Dict, List, Optional, Tuple

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import REPO_ROOT, rig_profile

import fake_linuxcnc as F
from bettercnc import adapter as A
from bettercnc import programs
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import LinuxCncAdapter, MockAdapter
from bettercnc.app import Session
from bettercnc.capabilities import Capability, present
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import DrivePower, HomingPhase, LinkHealth, Severity

SRC = os.path.join(REPO_ROOT, "reference", "linuxcnc", "src")
HAVE_REFERENCE = os.path.isdir(SRC)
NEEDS_REFERENCE = unittest.skipUnless(
    HAVE_REFERENCE, "reference/linuxcnc not cloned (it is gitignored)"
)
#: On the LinuxCNC machine the real module imports; tests that rely on its
#: absence skip there instead of failing.
REAL_LINUXCNC_IMPORTABLE = importlib.util.find_spec("linuxcnc") is not None

C = F.CONSTANTS
WAIT = A.WAIT_TIMEOUT_S
ALL_LINK_PINS = {
    LinkHealth.HAL_PACKET_ERROR_TOTAL: 0,
    LinkHealth.HAL_WATCHDOG_HAS_BIT: False,
    LinkHealth.HAL_IO_ERROR: False,
}


def _src(rel: str) -> str:
    with open(os.path.join(SRC, rel), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


#: Values that differ from test-rig.ini AND from every fallback in the code
#: (servo 1.0 ms, feed ceiling 1.0, iocontrol cycle 0.100 s), with [TRAJ]
#: DEFAULT_LINEAR_VELOCITY left at the rig's 1.0 so [DISPLAY] must win.
DISTINCT = {
    ("EMCMOT", "SERVO_PERIOD"): "500000",
    ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"): "2.5",
    ("DISPLAY", "MAX_FEED_OVERRIDE"): "1.2",
    ("EMCIO", "CYCLE_TIME"): "0.2",
}


class FakeClock:
    """Stands in for time.monotonic / time.sleep: sleeping advances it."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: List[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeCase(unittest.TestCase):
    """Builds an adapter on fresh fakes; the fakes are removed after each test."""

    def make(
        self,
        profile_key: str = "test-rig",
        machine: Optional[F.FakeMachine] = None,
        hal_pins: Optional[Dict] = None,
        hal_available: bool = True,
        joint_config: Optional[Dict] = None,
        profile=None,
        proc_root: str = "/nonexistent/proc",
    ) -> LinuxCncAdapter:
        cm = F.installed(
            machine=machine,
            hal_state=F.FakeHal(ALL_LINK_PINS if hal_pins is None else hal_pins),
            hal_available=hal_available,
        )
        _, _, self.machine, self.hal = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        # As test-rig.hal nets them: has_bit on `watchdog` with the estop
        # latch's fault input; io_error is a parameter.
        self.hal.signals = {"watchdog": [LinkHealth.HAL_WATCHDOG_HAS_BIT]}
        self.hal.params = {LinkHealth.HAL_IO_ERROR}
        self.halcmd = F.FakeHalcmd(self.hal, on_change=self._couple_latch)
        self.profile = profile if profile is not None else load_profile(profile_key)
        self.clock = FakeClock()
        # proc_root: no /proc by default, so linuxcnc_lost() never reads the
        # host's processes; TestLinuxCncLost passes its own.
        return LinuxCncAdapter(
            self.profile, joint_config=joint_config,
            clock=self.clock, sleep=self.clock.sleep,
            halcmd=lambda args: self.halcmd(args), proc_root=proc_root,
        )

    def _couple_latch(self) -> None:
        """The `watchdog` net: the estop latch's fault input is has_bit."""
        self.machine.fault_in = bool(self.hal.pins.get(LinkHealth.HAL_WATCHDOG_HAS_BIT))

    def bite(self, io_error: bool = False) -> None:
        """The board link is lost: the watchdog bites (and io_error, if asked)."""
        self.hal.pins[LinkHealth.HAL_WATCHDOG_HAS_BIT] = True
        if io_error:
            self.hal.pins[LinkHealth.HAL_IO_ERROR] = True
        self.machine.fault_in = True

    def on(self, homed: bool = False, **kw) -> LinuxCncAdapter:
        """Adapter on a machine that is already out of estop and ON."""
        adapter = self.make(**kw)
        self.machine.power_on(homed=homed)
        return adapter

    def assertNoLinuxCncErrors(self) -> None:
        self.assertEqual(list(self.machine.errors), [], "LinuxCNC refused something")

    def jogs(self) -> List[tuple]:
        return [args for name, args in self.machine.log if name == "jog"]

    def ngc(self) -> str:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".ngc", delete=False)
        tmp.write("G0 X0\nM2\n")
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def run_mdi_program(self) -> None:
        """Model an MDI command still executing: MDI mode, interpreter busy."""
        self.machine.task_mode = C["MODE_MDI"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        self.machine.interp_state = C["INTERP_READING"]


# ---------------------------------------------------------------------------
# The fake itself
# ---------------------------------------------------------------------------


class TestFakeInstallation(unittest.TestCase):
    def test_fakes_are_removed_afterwards(self):
        """A leaked fake would make other tests think LinuxCNC is installed -
        test_mdi_tools_adapter checks that the adapter fails clearly without it."""
        before = (sys.modules.get("linuxcnc"), sys.modules.get("hal"))
        with F.installed():
            import linuxcnc  # noqa: F401
        self.assertEqual((sys.modules.get("linuxcnc"), sys.modules.get("hal")), before)

    @unittest.skipIf(REAL_LINUXCNC_IMPORTABLE, "the real linuxcnc module is installed here")
    def test_adapter_is_unusable_again_after_the_fakes_are_gone(self):
        with F.installed():
            pass
        with self.assertRaises(RuntimeError):
            LinuxCncAdapter(load_profile("test-rig"))

    def test_previous_entries_are_restored_even_on_error(self):
        missing = object()
        saved = {name: sys.modules.get(name, missing) for name in ("linuxcnc", "hal")}
        sentinels = {"linuxcnc": object(), "hal": object()}
        try:
            sys.modules.update(sentinels)  # type: ignore[arg-type]
            with self.assertRaises(ZeroDivisionError):
                with F.installed():
                    1 / 0
            self.assertIs(sys.modules["linuxcnc"], sentinels["linuxcnc"])
            self.assertIs(sys.modules["hal"], sentinels["hal"])
        finally:
            for name, previous in saved.items():
                if previous is missing:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = previous  # type: ignore[assignment]

    def test_stat_offers_every_listed_attribute_before_and_after_poll(self):
        """Zero-filled before the first poll (emcmodule.cc:258-271), copied
        in by it (:306-310): either way, every STAT_ATTRIBUTES name reads."""
        with F.installed() as (lc, _, _, _):
            s = lc.stat()
            for when in ("before poll", "after poll"):
                for name in F.STAT_ATTRIBUTES:
                    with self.subTest(when=when, attribute=name):
                        getattr(s, name)
                s.poll()
            self.assertEqual(len(s.gcodes), F.ACTIVE_G_CODES)

    def test_passive_status_reaches_stat_only_on_poll(self):
        """The plan-WP02 fields: a test sets them on the machine, stat
        reports them at the next poll - the joint flags only for configured
        joints (taskintf.cc:926, :959-963)."""
        machine = F.FakeMachine()
        with F.installed(machine=machine) as (lc, _, m, _):
            s = lc.stat()
            s.poll()
            m.hard_min[0] = 1
            m.hard_max[2] = 1
            m.hard_min[5] = 1                # beyond [KINS]JOINTS = 3
            m.override_mask = 1 << 0
            m.current_vel = 2.5
            m.motion_line, m.current_line = 8, 13
            m.g92_offset[0] = 10.0
            m.tool_offset[2] = 12.5
            m.rotation_xy = 30.0
            m.commanded_pos = [1.0] + [0.0] * 8
            self.assertEqual(s.joint[0]["min_hard_limit"], 0)   # not polled yet
            self.assertEqual(s.current_vel, 0.0)
            s.poll()
        self.assertEqual([(d["min_hard_limit"], d["max_hard_limit"], d["override_limits"])
                          for d in s.joint[:6]],
                         [(1, 0, 1), (0, 0, 1), (0, 1, 1), (0, 0, 0), (0, 0, 0), (0, 0, 0)])
        self.assertEqual((s.current_vel, s.motion_line, s.current_line), (2.5, 8, 13))
        self.assertEqual((s.g92_offset[0], s.tool_offset[2], s.rotation_xy), (10.0, 12.5, 30.0))
        self.assertEqual(s.position[0], 1.0)
        self.assertEqual(s.actual_position[0], 0.0)
        self.assertEqual(s.gcodes, F.SIM_STARTUP_GCODES)

    def test_axis_jog_in_free_mode_reports_the_joint_range_message(self):
        """The old adapter's jog, replayed against the fake: an axis jog in
        free mode. Its joint number is -1, so the range check at
        command.c:436-443 answers first - "Mode is NOT TELEOP" (:430-433) is
        never printed on this path - and the text has no trailing newline.
        It travels on the error channel (motion.c:206-212, taskintf.cc:
        2024-2028), and it is a motion refusal: task's status stays DONE."""
        machine = F.FakeMachine()
        machine.power_on(homed=False)
        with F.installed(machine=machine) as (lc, _, m, _):
            c = lc.command()
            c.jog(lc.JOG_INCREMENT, False, 0, 1.0, 1.0)
            status = c.wait_complete(0.1)
        self.assertEqual(
            list(m.errors),
            [(C["OPERATOR_ERROR"], "Joint jog requested for undefined joint number=-1 (min=0,max=2)")],
        )
        self.assertEqual(m.axis_pos[0], 0.0)
        self.assertEqual(status, C["RCS_DONE"])

    def test_task_and_joint_error_refusals_give_rcs_error(self):
        """Task-level refusals mark the status ERROR (emctaskmain.cc:3546-3551);
        so do motion refusals that set the joint error flag (command.c:853-857
        with taskintf.cc:974-978)."""
        machine = F.FakeMachine()
        with F.installed(machine=machine) as (lc, _, m, _):
            c = lc.command()
            c.home(-1)                       # estopped: task refuses
            self.assertEqual(c.wait_complete(0.1), C["RCS_ERROR"])
            m.power_on()
            m.homing[0] = 1                  # a homing move in progress
            c.jog(lc.JOG_INCREMENT, True, 1, 1.0, 1.0)
            self.assertEqual(c.wait_complete(0.1), C["RCS_ERROR"])
        self.assertEqual(m.errors[-1], (C["OPERATOR_ERROR"], "Can't jog any joint while homing."))

    def test_wait_complete_times_out_while_motion_moves_and_records_the_timeout(self):
        machine = F.FakeMachine()
        machine.power_on()
        with F.installed(machine=machine) as (lc, _, m, _):
            c = lc.command()
            c.feedrate(0.5)
            m.jogging_active = True
            self.assertEqual(c.wait_complete(0.25), -1)
            m.jogging_active = False
            self.assertEqual(c.wait_complete(), C["RCS_DONE"])
        waits = [args for name, args in m.log if name == "wait_complete"]
        self.assertEqual(waits, [(0.25,), ()])

    def test_teleop_request_is_dropped_while_not_in_position(self):
        """control.c:927-958: not in position, motion does not honour it."""
        machine = F.FakeMachine()
        machine.power_on()
        machine.jogging_active = True
        with F.installed(machine=machine) as (lc, _, m, _):
            lc.command().teleop_enable(1)
            s = lc.stat()
            s.poll()
        self.assertEqual(s.motion_mode, C["TRAJ_MODE_FREE"])
        self.assertEqual(list(m.errors), [])

    def test_estop_latch_clears_on_the_nth_poll(self):
        machine = F.FakeMachine(estop_clear_after_polls=3)
        with F.installed(machine=machine) as (lc, _, m, _):
            lc.command().state(lc.STATE_ESTOP_RESET)
            s = lc.stat()
            seen = []
            for _ in range(4):
                s.poll()
                seen.append(s.task_state)
        self.assertEqual(seen, [C["STATE_ESTOP"], C["STATE_ESTOP"],
                                C["STATE_ESTOP_RESET"], C["STATE_ESTOP_RESET"]])

    def test_the_latch_trips_on_a_fault_and_resets_only_once_it_is_gone(self):
        """estop_latch.comp: faulted whenever fault-in is true; OK again only
        on a reset edge with the fault gone. Task drops motion on aux.estop
        (emctaskmain.cc:3413-3416)."""
        machine = F.FakeMachine()
        machine.power_on()
        with F.installed(machine=machine) as (lc, _, m, _):
            s, c = lc.stat(), lc.command()
            m.fault_in = True
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ESTOP"])
            self.assertFalse(s.enabled)
            c.state(lc.STATE_ESTOP_RESET)
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ESTOP"])
            m.fault_in = False
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ESTOP"], "no reset edge yet")
            c.state(lc.STATE_ESTOP_RESET)
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ESTOP_RESET"])

    def test_software_estop_trips_the_latch_only_where_user_enable_out_feeds_it(self):
        for in_chain, expected in ((False, "STATE_ESTOP_RESET"), (True, "STATE_ESTOP")):
            with self.subTest(user_enable_out_in_chain=in_chain):
                machine = F.FakeMachine()
                machine.user_enable_out_in_chain = in_chain
                machine.power_on()
                with F.installed(machine=machine) as (lc, _, m, _):
                    lc.command().state(lc.STATE_ESTOP)
                    s = lc.stat()
                    s.poll()
                    self.assertEqual(s.task_state, C[expected])
                    self.assertEqual(m.user_enable_out, 0)

    def test_stat_inpos_ignores_a_teleop_jog(self):
        """control.c clears INPOS in free mode while a joint planner runs
        (:1205-1272); its TELEOP branch never touches it (:1398-)."""
        machine = F.FakeMachine()
        machine.power_on(homed=True)
        machine.jogging_active = True
        with F.installed(machine=machine) as (lc, _, m, _):
            s = lc.stat()
            s.poll()
            self.assertTrue(s.inpos)
            m.motion_mode = C["TRAJ_MODE_FREE"]
            s.poll()
            self.assertFalse(s.inpos)
            m.jogging_active = False
            m.homing[2] = 1
            s.poll()
            self.assertFalse(s.inpos)

    def test_estop_reset_aborts_a_running_program(self):
        """emcTaskAbort() in the ESTOP_RESET case, emctask.cc:340-349."""
        machine = F.FakeMachine()
        machine.power_on(homed=True)
        machine.task_mode = C["MODE_AUTO"]
        machine.interp_state = C["INTERP_READING"]
        with F.installed(machine=machine) as (lc, _, m, _):
            lc.command().state(lc.STATE_ESTOP_RESET)
        self.assertEqual(m.interp_state, C["INTERP_IDLE"])

    def test_invalid_channels_raise_linuxcnc_error(self):
        machine = F.FakeMachine()
        with F.installed(machine=machine) as (lc, _, m, _):
            s, e = lc.stat(), lc.error_channel()
            m.channels_valid = False
            with self.assertRaises(lc.error):
                s.poll()
            with self.assertRaises(lc.error):
                e.poll()
            self.assertTrue(issubclass(lc.error, RuntimeError))

    def test_argument_errors_raise_like_the_binding(self):
        with F.installed() as (lc, _, m, _):
            c = lc.command()
            with self.assertRaisesRegex(TypeError, r"jog\(JOG_INCREMENT, \.\.\.\) takes 5 arguments \(4 given\)"):
                c.jog(lc.JOG_INCREMENT, 1, 0, 1.0)
            with self.assertRaisesRegex(TypeError, "integer argument expected, got float"):
                c.mode(1.0)
            with self.assertRaisesRegex(ValueError, "Mode should be"):
                c.mode(7)
            with self.assertRaisesRegex(lc.error, "Unexpected argument '0' to command.auto"):
                c.auto(lc.AUTO_RUN)
        self.assertEqual(m.log, [], "a call that raised must not count as sent")

    def test_stat_changes_only_on_poll(self):
        machine = F.FakeMachine()
        with F.installed(machine=machine) as (lc, _, m, _):
            s = lc.stat()
            self.assertEqual(s.task_state, 0)
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ESTOP"])
            m.power_on()
            self.assertEqual(s.task_state, C["STATE_ESTOP"])
            s.poll()
            self.assertEqual(s.task_state, C["STATE_ON"])


class TestFakeFidelity(unittest.TestCase):
    """The fake against reference/linuxcnc. Skipped when reference/ is absent."""

    EMCMODULE = "emc/usr_intf/axis/extensions/emcmodule.cc"

    @classmethod
    def _c_symbols(cls) -> Dict[str, int]:
        """C integer symbols from the headers emcmodule.cc's constants name."""
        out: Dict[str, int] = {}
        for rel in (
            "emc/nml_intf/emc.hh", "emc/kinematics/kinematics.h",
            "libnml/rcs/rcs.hh", "libnml/nml/nml_oi.hh", "emc/motion/emcmotcfg.h",
            cls.EMCMODULE,
        ):
            text = _src(rel)
            for m in re.finditer(
                r"#define\s+(\w+)\s+\(*\s*(?:\(NMLTYPE\)\s*)?(-?\d+)\s*\)*\s*$",
                text, re.M,
            ):
                out.setdefault(m.group(1), int(m.group(2)))
            for body in re.findall(r"enum\s*\w*\s*\{([^}]*)\}", text):
                value = -1
                for item in re.sub(r"/\*.*?\*/|//[^\n]*", "", body, flags=re.S).split(","):
                    item = item.strip()
                    if not item:
                        continue
                    name, _, rhs = item.partition("=")
                    value = int(rhs.strip(), 0) if rhs.strip() else value + 1
                    out.setdefault(name.strip(), value)
        return out

    @classmethod
    def _real_constants(cls) -> Dict[str, Optional[int]]:
        """Python name -> value; None where the value is in a header not
        searched (only constants the fake does not mirror)."""
        text = _src(cls.EMCMODULE)
        syms = cls._c_symbols()
        out: Dict[str, Optional[int]] = {}
        for m in re.finditer(r"^\s*ENUM\((\w+)\);", text, re.M):
            out[m.group(1)] = syms.get(m.group(1))
        for m in re.finditer(r"^\s*ENUMX\((\d+),\s*(\w+)\);", text, re.M):
            out[m.group(2)[int(m.group(1)):]] = syms.get(m.group(2))
        for m in re.finditer(r'PyModule_AddIntConstant\(m,\s*"(\w+)",\s*(\w+)\)', text):
            out[m.group(1)] = syms.get(m.group(2))
        return out

    @classmethod
    def _method_table(cls, text: str, table: str) -> Dict[str, tuple]:
        start = text.index(table)
        block = text[start:text.index("{NULL}", start)]
        return {
            m.group(1): (m.group(2), m.group(3))
            for m in re.finditer(r'\{"(\w+)",\s*(?:\(PyCFunction\))?(\w+),\s*(METH_\w+)', block)
        }

    @staticmethod
    def _first_format(text: str, func: str) -> str:
        m = re.search(r"\n(?:static\s+)?PyObject\s*\*\s*%s\s*\(" % re.escape(func), text)
        assert m, func
        body = text[m.end():text.find("\n}", m.end())]
        fmt = re.search(r'PyArg_ParseTuple\(\s*\w+\s*,\s*"([^"]*)"', body)
        assert fmt, "no PyArg_ParseTuple in " + func
        return fmt.group(1)

    @NEEDS_REFERENCE
    def test_every_constant_matches_the_binding(self):
        real = self._real_constants()
        for name, value in F.CONSTANTS.items():
            with self.subTest(constant=name):
                self.assertIn(name, real)
                self.assertEqual(value, real[name])

    @NEEDS_REFERENCE
    def test_every_command_method_exists_with_the_same_format(self):
        text = _src(self.EMCMODULE)
        table = self._method_table(text, "static PyMethodDef Command_methods[]")
        for name, fmt in F.COMMAND_FORMATS.items():
            with self.subTest(method=name):
                self.assertIn(name, table)
                cfunc, flags = table[name]
                if fmt is None:
                    self.assertEqual(flags, "METH_NOARGS")
                else:
                    self.assertEqual(flags, "METH_VARARGS")
                    self.assertEqual(self._first_format(text, cfunc), fmt)

    @NEEDS_REFERENCE
    def test_every_stat_attribute_exists(self):
        text = _src(self.EMCMODULE)
        real = set(re.findall(r'\{\(char\*\)"(\w+)",\s*(?:T_\w+|\(getter\))', text))
        for name in F.STAT_ATTRIBUTES:
            with self.subTest(attribute=name):
                self.assertIn(name, real)

    #: The stat fields plan WP02 added to the fake: each must stay listed,
    #: and so checked against the binding by the test above.
    STATE_PLUMBING_ATTRIBUTES = (
        "position", "current_vel", "motion_line", "current_line", "g92_offset",
        "tool_offset", "rotation_xy", "gcodes",
    )

    @NEEDS_REFERENCE
    def test_the_state_plumbing_attributes_are_in_the_binding(self):
        text = _src(self.EMCMODULE)
        real = set(re.findall(r'\{\(char\*\)"(\w+)",\s*(?:T_\w+|\(getter\))', text))
        for name in self.STATE_PLUMBING_ATTRIBUTES:
            with self.subTest(attribute=name):
                self.assertIn(name, F.STAT_ATTRIBUTES)
                self.assertIn(name, real)

    @NEEDS_REFERENCE
    def test_adapter_docstring_cites_the_binding_line_of_each_stat_field(self):
        """The adapter module docstring gives emcmodule.cc:N for each stat
        field; line N must define it: the member or getter for stat.NAME,
        the dict entry for stat.joint[n]["KEY"]. Unlike most citations in
        this suite, these line numbers are checked."""
        lines = _src(self.EMCMODULE).split("\n")
        rows = re.findall(
            r'stat\.(\w+)(?:\[n\]\["(\w+)"\])?\s+\(?emcmodule\.cc:(\d+)', A.__doc__)
        self.assertGreaterEqual(len(rows), 25)
        for name, key, line in rows:
            with self.subTest(field=name, key=key, line=line):
                text = lines[int(line) - 1]
                if key:
                    self.assertRegex(text, r'\bF2\("{0}",|\bF\({0}\);'.format(re.escape(key)))
                else:
                    self.assertIn('{{(char*)"{}",'.format(name), text)

    @NEEDS_REFERENCE
    def test_joint_dict_keys_match_stat_joint_one(self):
        text = _src(self.EMCMODULE)
        start = text.index("static PyObject *Stat_joint_one(")
        body = text[start:text.index("\n}", start)]
        keys = [m.group(1) or m.group(2)
                for m in re.finditer(r'\bF\((\w+)\);|\bF2\("(\w+)",', body)]
        self.assertEqual(tuple(keys), F.JOINT_DICT_KEYS)

    @NEEDS_REFERENCE
    def test_message_type_numbers_match_emc_hh(self):
        syms = self._c_symbols()
        for name, number in F._MSG.items():
            with self.subTest(msg=name):
                self.assertEqual(syms[name + "_TYPE"], number)

    @NEEDS_REFERENCE
    def test_hal_functions_match_halmodule(self):
        text = _src("hal/halmodule.cc")
        module = self._method_table(text, "PyMethodDef module_methods[]")
        self.assertIn("get_value", module)
        self.assertEqual(self._first_format(text, "get_value"), "s")
        comp = self._method_table(text, "static PyMethodDef hal_methods[]")
        self.assertIn("ready", comp)
        self.assertIn('PyArg_ParseTuple(args, "s|s:hal.component"', text)

    @NEEDS_REFERENCE
    def test_estop_latch_pins_and_rule_match_the_component(self):
        """The fake's run_latch against estop_latch.comp: defaults, and the
        reset-edge / trip rule."""
        text = _src("hal/components/estop_latch.comp")
        for pin in ("pin in bit ok_in = true;", "pin in bit fault_in = false;",
                    "pin in bit reset;", "pin out bit ok_out = false;"):
            with self.subTest(pin=pin):
                self.assertIn(pin, text)
        body = re.sub(r"\s+", " ", text[text.index("FUNCTION(_)"):])
        self.assertIn("if ( ok_in && !fault_in) {", body)
        self.assertIn("if ( reset && !data.old_reset ) {", body)
        self.assertIn("} else { /* fault condition exists, trip */ ok_out = 0;", body)

    def test_rig_hal_keeps_user_enable_out_out_of_the_estop_chain(self):
        """What FakeMachine.user_enable_out_in_chain = False stands for: in
        configs/test-rig/test-rig.hal the latch's reset is iocontrol's
        user-request-enable, its ok-out is emc-enable-in, its fault-in the
        watchdog, and neither user-enable-out nor ok-in is connected."""
        with open(os.path.join(REPO_ROOT, "configs", "test-rig", "test-rig.hal"),
                  encoding="utf-8") as fh:
            nets = [line.split("#")[0].split() for line in fh
                    if line.strip().startswith("net ")]
        pins = {pin for net in nets for pin in net[2:] if pin != "=>"}
        self.assertNotIn("iocontrol.0.user-enable-out", pins)
        self.assertNotIn("estop-latch.0.ok-in", pins)

        def joined(a, b):
            return any(a in net and b in net for net in nets)

        self.assertTrue(joined("iocontrol.0.user-request-enable", "estop-latch.0.reset"))
        self.assertTrue(joined("estop-latch.0.ok-out", "iocontrol.0.emc-enable-in"))
        self.assertTrue(any("estop-latch.0.fault-in" in net for net in nets))
        self.assertFalse(F.FakeMachine().user_enable_out_in_chain)

    @NEEDS_REFERENCE
    def test_wait_complete_does_not_release_the_gil(self):
        """WAIT_TIMEOUT_S rests on this: the only Py_BEGIN_ALLOW_THREADS in the
        binding is in logger.start, so wait_complete keeps the GIL."""
        text = _src(self.EMCMODULE)
        self.assertEqual(text.count("Py_BEGIN_ALLOW_THREADS"), 1)
        start = text.index("static PyObject *Logger_start(")
        end = text.index("\n}", start)
        self.assertIn("Py_BEGIN_ALLOW_THREADS", text[start:end])
        self.assertIn("#define EMC_COMMAND_TIMEOUT 5.0", text)

    #: (source file, text in the source, text the fake emits). Each refusal the
    #: fake produces must be traceable to the line that produces it in LinuxCNC.
    MESSAGES = [
        ("emc/motion/command.c", '"JOG_INCR %s\\n"', "JOG_INCR Mode is TELEOP, cannot jog joint\\n"),
        ("emc/motion/command.c", "Mode is TELEOP, cannot jog joint", "Mode is TELEOP, cannot jog joint"),
        ("emc/motion/command.c", "must be in joint mode to home", "must be in joint mode to home"),
        ("emc/motion/command.c", "Can't jog joint when not enabled.", "Can't jog joint when not enabled."),
        ("emc/motion/command.c", "Can't jog any joint while homing.", "Can't jog any joint while homing."),
        # Plan WP05: motion's own refusal of a joint jog onto a pressed
        # switch (joint_jog_ok) and its limit trip (check_for_faults).
        ("emc/motion/command.c", "Can't jog joint %d further past max hard limit.",
         "Can't jog joint {} further past max hard limit."),
        ("emc/motion/command.c", "Can't jog joint %d further past min hard limit.",
         "Can't jog joint {} further past min hard limit."),
        ("emc/motion/control.c", "joint %d on limit switch error", "joint {} on limit switch error"),
        ("emc/motion/command.c", '"Joint jog requested for undefined joint number=%d (min=0,max=%d)",',
         '"Joint jog requested for undefined joint number={} (min=0,max={})".format('),
        ("emc/motion/motion.c", "all joints must be homed before going into teleop mode",
         "all joints must be homed before going into teleop mode"),
        ("emc/task/emctaskmain.cc",
         "command (%s) cannot be executed until the machine is out of E-stop and turned on",
         "command ({}) cannot be executed until the machine is out of "),
        ("emc/task/emctaskmain.cc", "can't do that (%s:%d) in manual mode",
         "can't do that ({}:{}) in manual mode"),
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter idle",
         "can't do that ({}) in auto mode with the interpreter idle"),
        # Plan WP09: one text for the three busy interpreter states, the
        # state's word filled in from _plan's table.
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter reading",
         '(_AUTO_READING_OK, "reading")'),
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter paused",
         '(_AUTO_PAUSED_OK, "paused")'),
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter waiting",
         '(_AUTO_WAITING_OK, "waiting")'),
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter idle",
         "can't do that ({}) in auto mode with the interpreter {}"),
        ("emc/task/emctaskmain.cc", "can't do that (%s:%d) in MDI mode", "can't do that ({}:{}) in MDI mode"),
        ("emc/task/emctaskmain.cc", "Can't switch mode while mode is AUTO and interpreter is not IDLE",
         "Can't switch mode while mode is AUTO and interpreter is not IDLE"),
        ("emc/task/emctaskmain.cc", "Can't issue MDI command when not homed",
         "Can't issue MDI command when not homed"),
        ("emc/task/emctaskmain.cc", "Must be in MDI mode to issue MDI command",
         "Must be in MDI mode to issue MDI command"),
        ("emc/task/emctaskmain.cc", "Can't run a program when not homed", "Can't run a program when not homed"),
        ("emc/task/emctaskmain.cc", "can't open %s", "can't open {}"),
        ("emc/task/emctask.cc", "Ignoring task mode change while jogging",
         "Ignoring task mode change while jogging"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "jog(JOG_INCREMENT, ...) takes 5 arguments",
         "jog({}, ...) takes {} arguments"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "MDI commands limited to %zu characters",
         "MDI commands limited to {} characters"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "File name limited to %zu characters",
         "File name limited to {} characters"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "Mode should be MODE_MDI, MODE_MANUAL, or MODE_AUTO",
         "Mode should be MODE_MDI, MODE_MANUAL, or MODE_AUTO"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "Unexpected argument '%d' to command.auto",
         "Unexpected argument '{}' to command.auto"),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", "emcStatusBuffer invalid err=%d",
         "emcStatusBuffer invalid err="),
        ("emc/usr_intf/axis/extensions/emcmodule.cc", '"Error buffer invalid"', '"Error buffer invalid"'),
        ("hal/halmodule.cc", "Cannot call before creating component", "Cannot call before creating component"),
        ("hal/halmodule.cc", "Can't set value: pin / param %s not found",
         "Can't set value: pin / param {} not found"),
    ]

    @NEEDS_REFERENCE
    def test_every_refusal_text_is_in_the_source(self):
        with open(F.__file__, "r", encoding="utf-8") as fh:
            fake = fh.read()
        for rel, in_source, in_fake in self.MESSAGES:
            with self.subTest(text=in_source):
                self.assertIn(in_source, _src(rel))
                self.assertIn(in_fake, fake)

    @NEEDS_REFERENCE
    def test_range_check_returns_before_the_not_teleop_text_is_printed(self):
        """The order the fake's jog refusal relies on (command.c:418-466):
        the joint range check, which returns, comes after the line that sets
        the "NOT TELEOP" text and before the line that prints it."""
        text = _src("emc/motion/command.c")
        set_text = text.index('emsg = "Mode is NOT TELEOP, cannot jog axis coordinate";')
        range_check = text.index('"Joint jog requested for undefined joint number=%d (min=0,max=%d)"')
        printed = text.index('rtapi_print_msg(RTAPI_MSG_ERR,"JOG_INCR %s\\n",emsg);')
        self.assertLess(set_text, range_check)
        self.assertLess(range_check, printed)
        self.assertIn("return;", text[range_check:printed].split("}")[0])


class TestAbortFidelity(unittest.TestCase):
    """DURDUR (plan WP03): what the fake's abort() does
    (FakeMachine.issue_abort) and what LinuxCncAdapter.stop_motion and
    stop_followup rely on, against reference/linuxcnc. Content, not line
    numbers."""

    @staticmethod
    def between(text: str, start: str, end: str) -> str:
        i = text.index(start)
        return text[i:text.index(end, i)]

    @NEEDS_REFERENCE
    def test_task_takes_abort_in_every_state_and_mode(self):
        """emcTaskPlan lists EMC_TASK_ABORT_TYPE in all seven branches - out
        of ON; ON with MANUAL; AUTO idle, reading, paused, waiting; MDI -
        and the fake accepts it in every branch it models."""
        plan = self.between(_src("emc/task/emctaskmain.cc"), "static int emcTaskPlan(void)",
                            "static int emcTaskCheckPreconditions(")
        self.assertEqual(plan.count("case EMC_TASK_ABORT_TYPE:"), 7)
        for table in (F._NOT_ON_OK, F._MANUAL_OK, F._AUTO_IDLE_OK, F._AUTO_READING_OK, F._MDI_OK):
            self.assertIn("EMC_TASK_ABORT", table)

    @NEEDS_REFERENCE
    def test_abort_idles_the_interpreter_and_aborts_motion(self):
        issue = self.between(_src("emc/task/emctaskmain.cc"), "case EMC_TASK_ABORT_TYPE:\n\t// abort",
                             "break;")
        self.assertIn("emcTaskAbort();", issue)
        self.assertIn("emcMotionAbort();", issue)
        # emcMotionAbort twice per abort - emcTaskAbort's and this one -
        # which is what catches the next joint of a Home All
        # (FakeMachine.issue_abort).
        self.assertLess(issue.index("emcTaskAbort();"), issue.index("emcMotionAbort();"))
        task_abort = self.between(_src("emc/task/emctask.cc"), "int emcTaskAbort()", "\n}\n")
        self.assertIn("emcMotionAbort();", task_abort)
        self.assertIn("emcStatus->task.interpState = EMC_TASK_INTERP_IDLE;", task_abort)
        self.assertIn("emcStatus->task.task_paused = 0;", task_abort)
        motion_abort = self.between(_src("emc/task/taskintf.cc"), "int emcMotionAbort()", "\n}\n")
        self.assertIn("emcJogAbort(t)", motion_abort)
        self.assertIn("emcTrajAbort();", motion_abort)
        self.assertIn("emcmotCommand.command = EMCMOT_ABORT;",
                      self.between(_src("emc/task/taskintf.cc"), "int emcTrajAbort()", "\n}\n"))

    @NEEDS_REFERENCE
    def test_motion_abort_ends_jogs_and_cancels_homing_in_free_mode(self):
        """EMCMOT_ABORT by mode: teleop jogs, the coordinated planner, and in
        free mode every joint planner and its homing; EMCMOT_JOG_ABORT, sent
        per joint first, cancels homing as well."""
        text = _src("emc/motion/command.c")
        abort = self.between(text, "case EMCMOT_ABORT:", "case EMCMOT_JOG_ABORT:")
        teleop = abort.index("if (GET_MOTION_TELEOP_FLAG()) {")
        coord = abort.index("} else if (GET_MOTION_COORD_FLAG()) {")
        free = abort.index("} else {", coord)
        self.assertIn("axis_jog_abort_all(0);", abort[teleop:coord])
        self.assertIn("tpAbort(&emcmotInternal->coord_tp);", abort[coord:free])
        self.assertIn("joint->free_tp.enable = 0;", abort[free:])
        self.assertIn("do_cancel_homing(joint_num);", abort[free:])
        jog_abort = self.between(text, "case EMCMOT_JOG_ABORT:", "case EMCMOT_FREE:")
        self.assertIn("do_cancel_homing(joint_num);", jog_abort)
        # Only a joint homing at that instant is cancelled; one of a Home
        # All still waiting for its turn is not (FakeMachine.issue_abort).
        idle = re.compile(r"if \( ?! ?get_home_is_idle\(joint_num\) ?\) \{\s*"
                          r"(/\*[^*]*\*/\s*)?do_cancel_homing\(joint_num\);")
        self.assertRegex(abort[free:], idle)
        self.assertRegex(jog_abort, idle)

    @NEEDS_REFERENCE
    def test_a_cancelled_homing_leaves_the_joint_unhomed_and_a_finished_one_homed(self):
        text = _src("emc/motion/homing.c")
        aborted = self.between(text, "case HOME_ABORT:", "break;")
        self.assertIn("H[joint_num].homing = 0;", aborted)
        self.assertIn("H[joint_num].homed = 0;", aborted)
        finished = self.between(text, "case HOME_FINISHED:", "break;")
        self.assertIn("H[joint_num].homed = 1;", finished)
        self.assertIn("H[joint_num].joint_in_sequence = 0;", finished)
        cancel = self.between(text, "static void base_do_cancel_homing(int jno) {", "\n}\n")
        self.assertIn("if (H[jno].homing) {", cancel)
        self.assertIn("}else if(H[jno].joint_in_sequence){", cancel)
        # Why one abort still ends the rig's whole Home All
        # (FakeMachine.issue_abort): once the cancelled joint is idle the
        # sequence starts the next step's joints, and a joint's first move
        # waits HOME_DELAY (0.1 s) - long enough for a later cancel point
        # of the same abort.
        wait = self.between(text, "case HOME_SEQUENCE_WAIT_JOINTS:", "default:")
        self.assertIn("if(H[i].home_state != HOME_IDLE) {", wait)
        self.assertIn("current_sequence++;", wait)
        self.assertIn("sequence_state = HOME_SEQUENCE_START_JOINTS;", wait)
        self.assertIn("#define HOME_DELAY 0.100", text)
        for state in ("case HOME_INITIAL_BACKOFF_START:", "case HOME_INITIAL_SEARCH_START:"):
            with self.subTest(state=state):
                first = self.between(text, state, "break;\n\n        case")
                self.assertIn("if (H[joint_num].pause_timer < (HOME_DELAY * servo_freq)) {",
                              first)

    @NEEDS_REFERENCE
    def test_each_command_channel_is_a_channel_pair_of_its_own(self):
        """Command_init: every linuxcnc.command() opens its own NML command
        and status channels - the adapter's third, for DURDUR, included."""
        init = self.between(_src(TestFakeFidelity.EMCMODULE), "static int Command_init(", "\n}\n")
        self.assertIn('new RCS_CMD_CHANNEL(emcFormat, "emcCommand", "xemc", file);', init)
        self.assertIn('new RCS_STAT_CHANNEL(emcFormat, "emcStatus", "xemc", file);', init)

    @NEEDS_REFERENCE
    def test_axis_binds_escape_to_task_stop_which_aborts(self):
        axis = _src("emc/usr_intf/axis/scripts/axis.py")
        self.assertIn('root_window.bind("<Escape>", commands.task_stop)', axis)
        stop = self.between(axis, "    def task_stop(*event):", "    def mdi_up_cmd(")
        self.assertIn("c.abort()", stop)
        self.assertIn("c.wait_complete()", stop)


class TestOverrideFidelity(unittest.TestCase):
    """Plan WP05: what the fake does with the switch flags and the limit
    override (FakeMachine.trip_on_limits, issue_override_limits, the mask
    clearing in issue_jog_incr and set_traj_mode, the planner's
    _NOT_ON_MANUAL_ONLY), and what LinuxCncAdapter.recover_limit relies on,
    against reference/linuxcnc. Content, not line numbers."""

    between = staticmethod(TestAbortFidelity.between)

    @NEEDS_REFERENCE
    def test_task_takes_the_override_out_of_on_in_manual_alone(self):
        """emcTaskPlan: out of ON the override is issued in MANUAL and
        otherwise dropped with retval 0 - no error, no message; ON, only
        the MANUAL branch lists it (two cases in all), so AUTO and MDI
        refuse it. The binding always sends joint 0: no withdrawing it."""
        plan = self.between(_src("emc/task/emctaskmain.cc"), "static int emcTaskPlan(void)",
                            "static int emcTaskCheckPreconditions(")
        self.assertEqual(plan.count("case EMC_JOINT_OVERRIDE_LIMITS_TYPE:"), 2)
        not_on = self.between(plan, "case EMC_JOINT_OVERRIDE_LIMITS_TYPE:", "break;")
        self.assertIn("retval = 0;", not_on)
        self.assertIn("if (emcStatus->task.mode == EMC_TASK_MODE_MANUAL) {", not_on)
        self.assertNotIn("emcOperatorError", not_on)
        manual_on = self.between(plan, "case EMC_TASK_MODE_MANUAL:\t// ON, MANUAL",
                                 "case EMC_TASK_MODE_AUTO:\t// ON, AUTO")
        self.assertIn("case EMC_JOINT_OVERRIDE_LIMITS_TYPE:", manual_on)
        self.assertIn("EMC_JOINT_OVERRIDE_LIMITS", F._NOT_ON_MANUAL_ONLY)
        self.assertIn("EMC_JOINT_OVERRIDE_LIMITS", F._MANUAL_OK)
        for table in (F._NOT_ON_OK, F._AUTO_IDLE_OK, F._AUTO_READING_OK, F._MDI_OK,
                      F._ALLOW_WHILE_IDLE):
            self.assertNotIn("EMC_JOINT_OVERRIDE_LIMITS", table)
        binding = self.between(_src(TestFakeFidelity.EMCMODULE),
                               "static PyObject *override_limits(", "\n}")
        self.assertIn("m.joint = 0;", binding)

    @NEEDS_REFERENCE
    def test_the_override_masks_only_the_sides_pressed_then(self):
        """EMCMOT_OVERRIDE_LIMITS: joint >= 0 rebuilds the mask from the
        flags of that instant; joint < 0 (which the binding never sends)
        clears it."""
        body = self.between(_src("emc/motion/command.c"), "case EMCMOT_OVERRIDE_LIMITS:",
                            "case EMCMOT_SET_JOINT_MOTOR_OFFSET:")
        self.assertIn("only override limits that are currently tripped", body)
        self.assertIn("if ( GET_JOINT_NHL_FLAG(joint) ) {", body)
        self.assertIn("emcmotStatus->overrideLimitMask |= (1 << (joint_num*2));", body)
        self.assertIn("if ( GET_JOINT_PHL_FLAG(joint) ) {", body)
        self.assertIn("emcmotStatus->overrideLimitMask |= (2 << (joint_num*2));", body)

    @NEEDS_REFERENCE
    def test_motion_trips_on_an_unmasked_flag_unless_the_joint_homes(self):
        # The definitions, not the prototypes at the top of the file.
        faults = self.between(_src("emc/motion/control.c"), "static void check_for_faults(void)\n{",
                              "static void set_operating_mode(void)\n{")
        trip = self.between(faults, "/* check for hard limits */", "/* check for amp fault */")
        self.assertIn("GET_JOINT_PHL_FLAG(joint) && ! pos_limit_override", trip)
        self.assertIn("GET_JOINT_NHL_FLAG(joint) && ! neg_limit_override", trip)
        self.assertIn("if (get_homing(joint_num)) {", trip)
        self.assertIn('reportError(_("joint %d on limit switch error")', trip)
        self.assertIn("emcmotInternal->enabling = 0;", trip)
        self.assertIn("GET_JOINT_ACTIVE_FLAG(joint) && GET_JOINT_ENABLE_FLAG(joint)", faults)

    @NEEDS_REFERENCE
    def test_the_mask_clears_only_at_a_jog_end_or_coordinated_mode(self):
        """Every write of overrideLimitMask: the command (two), the end of a
        free-mode move, the end of a teleop jog, entering coordinated mode,
        and motion's start - not enabling, not an abort, not ESTOP."""
        control = _src("emc/motion/control.c")
        self.assertEqual(control.count("overrideLimitMask = 0;"), 3)
        free = self.between(control, "case EMCMOT_MOTION_FREE:", "case KINEMATICS_IDENTITY:")
        self.assertIn("if ( (emcmotInternal->overriding ) && ( GET_MOTION_INPOS_FLAG() ) ) {", free)
        teleop = self.between(control, "case EMCMOT_MOTION_TELEOP:", "case EMCMOT_MOTION_DISABLED:")
        self.assertIn("if ( ( emcmotInternal->overriding ) && ( !axis_jog_is_active() ) ) {",
                      teleop)
        coord = self.between(control, "/* check for entering coordinated mode */",
                             "/* check entering free space mode */")
        self.assertIn("emcmotStatus->overrideLimitMask = 0;", coord)
        enabling = self.between(control, "/* check for emcmotInternal->enabling */",
                                "/* check for entering teleop mode */")
        self.assertNotIn("overrideLimitMask", enabling)
        self.assertEqual(_src("emc/motion/command.c").count("overrideLimitMask = 0;"), 2)
        self.assertEqual(_src("emc/motion/motion.c").count("overrideLimitMask = 0;"), 1)
        status = _src("emc/task/taskintf.cc")
        self.assertIn("overrideLimits = !!(emcmotStatus.overrideLimitMask);", status)

    @NEEDS_REFERENCE
    def test_a_jog_the_disable_cut_leaves_overriding_set(self):
        """Review of WP05 (FakeMachine.overriding, end_override_jog): a jog
        run under the mask sets motion's `overriding` in either planner, and
        only their end-of-jog checks, entering coordinated mode and the
        override command clear it - not the disabling, and the DISABLED
        branch runs no check. So after an estop cut such a jog the status
        still shows the override, and the mask drops at the next enable:
        why recover_limit issues the override again on every press out of
        ON, and the reset sends no ON onto a pressed switch at all."""
        control = _src("emc/motion/control.c")
        free = self.between(control, "case EMCMOT_MOTION_FREE:", "case KINEMATICS_IDENTITY:")
        self.assertIn("emcmotInternal->overriding = 1;", free)
        teleop = self.between(control, "case EMCMOT_MOTION_TELEOP:", "case EMCMOT_MOTION_DISABLED:")
        self.assertIn("emcmotInternal->overriding = 1;", teleop)
        disabled = self.between(control, "case EMCMOT_MOTION_DISABLED:", "default:")
        self.assertNotIn("overriding", disabled)
        disabling = self.between(control, "/* check for disabling */",
                                 "/* check for emcmotInternal->enabling */")
        self.assertNotIn("overriding", disabling)
        self.assertEqual(control.count("emcmotInternal->overriding = 0;"), 3)
        command = self.between(_src("emc/motion/command.c"), "case EMCMOT_OVERRIDE_LIMITS:",
                               "case EMCMOT_SET_JOINT_MOTOR_OFFSET:")
        self.assertIn("emcmotInternal->overriding = 0;", command)

    @NEEDS_REFERENCE
    def test_a_joint_jog_onto_a_switch_is_refused_a_teleop_jog_unchecked(self):
        """joint_jog_ok refuses the pressed side, override or not, unless
        both sides are overridden; axis_jog_incr checks only the soft
        limits."""
        command = _src("emc/motion/command.c")
        ok = self.between(command, "STATIC int joint_jog_ok(int joint_num, double vel)",
                          "void refresh_jog_limits(")
        self.assertIn("if ( neg_limit_override && pos_limit_override ) {", ok)
        self.assertIn("if (vel > 0.0 && GET_JOINT_PHL_FLAG(joint)) {", ok)
        self.assertIn("if (vel < 0.0 && GET_JOINT_NHL_FLAG(joint)) {", ok)
        jog_incr = self.between(command, "/* do an incremental jog */", "case EMCMOT_JOG_ABS:")
        self.assertIn("if (!joint_jog_ok(joint_num, emcmotCommand->vel)) {", jog_incr)
        axis = self.between(_src("emc/motion/axis.c"), "void axis_jog_incr(", "void axis_jog_abs(")
        self.assertNotIn("HL_FLAG", axis)
        self.assertIn("if (tmp1 > axis->max_pos_limit) { return; }", axis)


class TestJogFidelity(unittest.TestCase):
    """Plan WP06: what the jog rule (motionrules.jog_refusal) and the fake's
    jog model (FakeMachine.issue_jog_incr with `soft_limits`,
    _joint_jog_in_window; end_homing) rely on, against reference/linuxcnc.
    Content, not line numbers."""

    between = staticmethod(TestAbortFidelity.between)

    @NEEDS_REFERENCE
    def test_a_jog_during_homing_is_refused_with_a_joint_error_and_task_aborts(self):
        """Why the UI refuses it first: motion's refusal sets the joint's
        error flag, task reads that as motion's error and aborts - which
        cancels the homing (TestAbortFidelity)."""
        command = _src("emc/motion/command.c")
        jog_incr = self.between(command, "/* do an incremental jog */", "case EMCMOT_JOG_ABS:")
        homing = self.between(jog_incr, "if ( get_homing_is_active() ) {", "break;")
        self.assertIn('reportError(_("Can\'t jog any joint while homing."));', homing)
        self.assertIn("SET_JOINT_ERROR_FLAG(joint, 1);", homing)
        task = _src("emc/task/taskintf.cc")
        flag = self.between(task, "if (joint->flag & EMCMOT_JOINT_ERROR_BIT) {", "} else if")
        self.assertIn("stat[joint_num].status = RCS_ERROR;", flag)
        self.assertIn("if (stat->joint[joint].status == RCS_ERROR) {", task)
        main = _src("emc/task/emctaskmain.cc")
        abort = self.between(main, "} else if (emcStatus->motion.status == RCS_ERROR ||",
                             "mdi_execute_abort();")
        self.assertIn("emcTaskAbort();", abort)
        # The fake refuses it the same way: an error and the joint's flag
        # (RCS_ERROR on the wait).
        fake = inspect.getsource(F.FakeMachine.issue_jog_incr)
        self.assertIn('raise MotionRejected("Can\'t jog any joint while homing.", '
                      'sets_joint_error=True)', fake)

    @NEEDS_REFERENCE
    def test_an_increment_adds_to_the_planners_target(self):
        """A second click while one runs extends it (why moving refuses):
        free mode from free_tp.pos_cmd, teleop from teleop_tp.pos_cmd."""
        jog_incr = self.between(_src("emc/motion/command.c"), "/* do an incremental jog */",
                                "case EMCMOT_JOG_ABS:")
        self.assertIn("tmp1 = joint->free_tp.pos_cmd + emcmotCommand->offset;", jog_incr)
        self.assertIn("tmp1 = joint->free_tp.pos_cmd - emcmotCommand->offset;", jog_incr)
        self.assertIn("axis_jog_incr(emcmotCommand->axis, emcmotCommand->offset, "
                      "emcmotCommand->vel, servo_period);", jog_incr)
        axis = self.between(_src("emc/motion/axis.c"), "void axis_jog_incr(", "void axis_jog_abs(")
        self.assertIn("tmp1 = axis->teleop_tp.pos_cmd + offset;", axis)
        self.assertIn("tmp1 = axis->teleop_tp.pos_cmd - offset;", axis)

    @NEEDS_REFERENCE
    def test_past_the_limits_motion_drops_the_jog_without_a_word(self):
        """Teleop: the axis's limits, homed or not, a silent return. Free:
        the jog limits - soft limits homed, position +- travel unhomed -
        refused with an error from beyond them, dropped silently past them.
        The fake models exactly these (`soft_limits`)."""
        axis = self.between(_src("emc/motion/axis.c"), "void axis_jog_incr(", "void axis_jog_abs(")
        self.assertIn("if (tmp1 > axis->max_pos_limit) { return; }", axis)
        self.assertIn("if (tmp1 < axis->min_pos_limit) { return; }", axis)
        self.assertNotIn("homed", axis)
        self.assertNotIn("reportError", axis)
        command = _src("emc/motion/command.c")
        refresh = self.between(command, "void refresh_jog_limits(emcmot_joint_t *joint, int joint_num)\n{",
                               "\n}\n")
        homed = self.between(refresh, "if (get_homed(joint_num) ) {", "} else {")
        self.assertIn("joint->max_jog_limit = joint->max_pos_limit;", homed)
        self.assertIn("joint->min_jog_limit = joint->min_pos_limit;", homed)
        unhomed = refresh[refresh.index("} else {"):]
        self.assertIn("range = joint->max_pos_limit - joint->min_pos_limit;", unhomed)
        self.assertIn("joint->max_jog_limit = joint->pos_fb + range;", unhomed)
        self.assertIn("joint->min_jog_limit = joint->pos_fb - range;", unhomed)
        ok = self.between(command, "STATIC int joint_jog_ok(int joint_num, double vel)",
                          "void refresh_jog_limits(")
        self.assertIn("if ( vel > 0.0 && (joint->pos_cmd > joint->max_jog_limit) ) {", ok)
        self.assertIn('"Can\'t jog joint %d further past max soft limit."', ok)
        self.assertIn("if ( vel < 0.0 && (joint->pos_cmd < joint->min_jog_limit) ) {", ok)
        jog_incr = self.between(command, "/* do an incremental jog */", "case EMCMOT_JOG_ABS:")
        drop = jog_incr[jog_incr.index("/* don't jog past limits */"):]
        self.assertRegex(drop, r"if \(tmp1 > joint->max_jog_limit\) \{\s*break;\s*\}")
        self.assertRegex(drop, r"if \(tmp1 < joint->min_jog_limit\) \{\s*break;\s*\}")
        # The soft limits' defaults when the INI gives none: no limit.
        joint_ini = _src("emc/ini/inijoint.cc")
        self.assertRegex(joint_ini, r"limit = -1e99;[^\n]*\n\s*jointIniFile->Find\(&limit, \"MIN_LIMIT\"")
        self.assertRegex(joint_ini, r"limit = 1e99;[^\n]*\n\s*jointIniFile->Find\(&limit, \"MAX_LIMIT\"")

    @NEEDS_REFERENCE
    def test_both_sides_overridden_skips_the_soft_limit_refusals_too(self):
        """joint_jog_ok returns before all its tests when both of a joint's
        sides are overridden - the soft-limit refusals as well as the
        switch ones - so only JOG_INCR's silent drop is left
        (FakeMachine._joint_jog_in_window `both`; review of WP06)."""
        ok = self.between(_src("emc/motion/command.c"),
                          "STATIC int joint_jog_ok(int joint_num, double vel)",
                          "void refresh_jog_limits(")
        both = self.between(ok, "if ( neg_limit_override && pos_limit_override ) {", "}")
        self.assertIn("return 1;", both)
        self.assertLess(ok.index("if ( neg_limit_override && pos_limit_override ) {"),
                        ok.index("refresh_jog_limits(joint,joint_num);"))
        self.assertLess(ok.index("refresh_jog_limits(joint,joint_num);"),
                        ok.index('"Can\'t jog joint %d further past max soft limit."'))

    @NEEDS_REFERENCE
    def test_the_commanded_position_is_where_the_planner_stands(self):
        """stat.position is carte_pos_cmd, which teleop fills from its
        planner's position; that position stops within tiny_dp of the
        target, and a planner not enabled takes its target from its
        position (why the cut jog keeps JOG_LIMIT_MARGIN_MM)."""
        self.assertIn("stat->position = emcmotStatus.carte_pos_cmd;", _src("emc/task/taskintf.cc"))
        teleop = self.between(_src("emc/motion/control.c"), "case EMCMOT_MOTION_TELEOP:",
                              "case EMCMOT_MOTION_DISABLED:")
        self.assertIn("axis_sync_carte_pos_to_teleop_tp(+1, pcmd_p);", teleop)
        simple = _src("emc/motion/simple_tp.c")
        self.assertIn("within 'tiny_dp' of desired pos, no need to move", simple)
        self.assertIn("tp->pos_cmd = tp->curr_pos;", simple)
        self.assertIn("#define TINY_DP(max_acc,period) (max_acc*period*period*0.001)",
                      _src("emc/motion/simple_tp.h"))

    @NEEDS_REFERENCE
    def test_homing_ends_at_home_and_manual_means_teleop_once_homed(self):
        """FakeMachine.end_homing and the rule's mode prediction."""
        homing = _src("emc/motion/homing.c")
        final = self.between(homing, "case HOME_FINAL_MOVE_START:", "case HOME_FINAL_MOVE_WAIT:")
        self.assertIn("joint->free_tp.pos_cmd = H[joint_num].home;", final)
        finished = self.between(homing, "case HOME_FINISHED:", "break;")
        self.assertIn("H[joint_num].homed = 1;", finished)
        self.assertIn("joints[joint_num].free_tp.curr_pos = H[joint_num].home;", finished)
        self.assertRegex(_src("emc/ini/inijoint.cc"),
                         r"home = 0;[^\n]*\n\s*jointIniFile->Find\(&home, \"HOME\"")
        manual = self.between(_src("emc/task/emctask.cc"), "case EMC_TASK_MODE_MANUAL:", "break;")
        self.assertRegex(manual, r"if \(all_homed\(\)\) \{\s*emcTrajSetMode\(EMC_TRAJ_MODE_TELEOP\);"
                                 r"\s*\} else \{\s*emcTrajSetMode\(EMC_TRAJ_MODE_FREE\);")

    def test_the_fakes_home_is_the_rigs(self):
        """RIG_HOME is [JOINT_n]HOME of the rig's INI, by axis letter."""
        from helpers import ini_raw_values
        ini = ini_raw_values()
        letters = ini["TRAJ.COORDINATES"].replace(" ", "")
        self.assertEqual(F.RIG_HOME, {letter: float(ini["JOINT_{}.HOME".format(j)])
                                      for j, letter in enumerate(letters)})

    def test_the_fakes_home_sequence_is_the_rigs(self):
        """RIG_HOME_SEQUENCE is [JOINT_n]HOME_SEQUENCE of the rig's INI."""
        from helpers import ini_raw_values
        ini = ini_raw_values()
        letters = ini["TRAJ.COORDINATES"].replace(" ", "")
        self.assertEqual(F.RIG_HOME_SEQUENCE,
                         {letter: int(ini["JOINT_{}.HOME_SEQUENCE".format(j)])
                          for j, letter in enumerate(letters)})


class TestHomingFidelity(unittest.TestCase):
    """Plan WP07: what one axis's homing and the re-home question rely on -
    the fake's home model (FakeMachine.issue_home, the planner's
    _MANUAL_OK), LinuxCncAdapter._home and motionrules.home_question -
    against reference/linuxcnc. Content, not line numbers."""

    between = staticmethod(TestAbortFidelity.between)

    @NEEDS_REFERENCE
    def test_task_takes_a_home_only_on_in_manual_and_motion_only_in_free_mode(self):
        plan = self.between(_src("emc/task/emctaskmain.cc"), "static int emcTaskPlan(void)",
                            "static int emcTaskCheckPreconditions(")
        self.assertEqual(plan.count("case EMC_JOINT_HOME_TYPE:"), 1)
        on_manual = self.between(plan, "case EMC_TASK_MODE_MANUAL:\t// ON, MANUAL",
                                 "// queued commands")
        self.assertIn("case EMC_JOINT_HOME_TYPE:", on_manual)
        self.assertIn("EMC_JOINT_HOME", F._MANUAL_OK)
        for table in (F._NOT_ON_OK, F._AUTO_IDLE_OK, F._AUTO_READING_OK, F._MDI_OK,
                      F._ALLOW_WHILE_IDLE):
            self.assertNotIn("EMC_JOINT_HOME", table)
        home = self.between(_src("emc/motion/command.c"), "case EMCMOT_JOINT_HOME:",
                            "case EMCMOT_JOINT_UNHOME:")
        self.assertIn("if (emcmotStatus->motion_state != EMCMOT_MOTION_FREE) {", home)
        self.assertIn('reportError(_("must be in joint mode to home"));', home)
        self.assertIn("do_home_joint(joint_num);", home)

    @NEEDS_REFERENCE
    def test_one_joint_with_a_sequence_not_negative_is_homed_alone(self):
        """home(j) homes joint j alone on the rig (sequences 0 / 1 / 2): the
        sequence takes only the joint started (FakeMachine.issue_home)."""
        homing = _src("emc/motion/homing.c")
        one = self.between(homing, "static void do_home_one_joint(int jno)", "\n} // do_home_one_joint")
        self.assertRegex(one, r"if \(H\[jno\]\.home_sequence < 0\) \{")
        self.assertRegex(one, r"\} else \{\s*sequence_state = HOME_SEQUENCE_DO_ONE_JOINT;\s*\}")
        self.assertIn("H[jno].home_state = HOME_START;", one)
        only = self.between(homing, "case HOME_SEQUENCE_DO_ONE_JOINT:",
                            "case HOME_SEQUENCE_DO_ONE_SEQUENCE:")
        self.assertRegex(only, r"if \(H\[i\]\.home_state == HOME_START\) \{\s*"
                               r"H\[i\]\.joint_in_sequence = 1;")
        self.assertRegex(only, r"\} else \{\s*H\[i\]\.joint_in_sequence = 0;")
        dispatch = self.between(homing, "static void base_do_home_joint(int jno) {", "\n}\n")
        self.assertRegex(dispatch, r"if \(jno == -1\) \{\s*H\[0\]\.homed = 0;[^\n]*\n\s*"
                                   r"do_home_all\(\);\s*\} else \{\s*do_home_one_joint\(jno\);")

    @NEEDS_REFERENCE
    def test_a_homing_unhomes_its_joints_as_it_starts(self):
        """Every joint of the sequence at its start, each again in
        HOME_START - so a joint homed again reads unhomed until it ends,
        as FakeMachine.issue_home models it."""
        homing = _src("emc/motion/homing.c")
        start = self.between(homing, "/* Initializations */", "/* tell the world we're on the job */")
        self.assertIn("if (!H[i].joint_in_sequence) continue;", start)
        self.assertRegex(start, r"\} else \{\s*H\[i\]\.homed = 0;\s*\}")
        first = self.between(homing, "case HOME_START:", "case HOME_UNLOCK:")
        self.assertRegex(first, r"H\[joint_num\]\.homing = 1;\s*H\[joint_num\]\.homed = 0;")
        machine = F.FakeMachine(home_instantly=False)
        machine.power_on(homed=True)
        machine.motion_mode = C["TRAJ_MODE_FREE"]
        machine.issue_home(2)
        self.assertEqual((machine.homed[:3], machine.homing[:3]), ([1, 1, 0], [0, 0, 1]))

    @NEEDS_REFERENCE
    def test_motion_goes_to_teleop_only_when_a_homing_leaves_every_joint_homed(self):
        """After one joint homed alone, the others unhomed, motion stays in
        free mode - the next jog is a joint jog (_select_jog_mode)."""
        homing = _src("emc/motion/homing.c")
        done = self.between(homing, "static bool base_do_homing(void)", "} // base_do_homing()")
        self.assertIn("bool beginning_allhomed = get_allhomed();", done)
        self.assertIn("if (!beginning_allhomed && get_allhomed()) {homing_active=0; return 1;}", done)
        self.assertRegex(_src("emc/motion/control.c"),
                         r"if \(\s*\(emcmotStatus->motion_state == EMCMOT_MOTION_FREE\)\s*"
                         r"&& do_homing\(\)\) \{\s*switch_to_teleop_mode\(\);")

    @NEEDS_REFERENCE
    def test_home_all_runs_group_by_group_with_a_gap_between(self):
        """P0 cleanup: the fake's Home All (home_all_groups, advance_homing)
        against homing.c's sequence: groups by abs(sequence) from 0 up, the
        sequence over at the first number no joint has (unless 0), a joint
        with HOME_SEQUENCE over 100 (999 when unset) left out, a jog refused
        while homing is active - between two groups too."""
        homing = _src("emc/motion/homing.c")
        start = self.between(homing, "case HOME_SEQUENCE_START_JOINTS:",
                             "case HOME_SEQUENCE_WAIT_JOINTS:")
        self.assertIn("if(ABS(H[i].home_sequence) == current_sequence) {", start)
        self.assertRegex(start, r"if \(seen \|\| current_sequence == 0\) \{\s*"
                                r"sequence_state = HOME_SEQUENCE_WAIT_JOINTS;")
        wait = self.between(homing, "case HOME_SEQUENCE_WAIT_JOINTS:", "default:")
        self.assertRegex(wait, r"current_sequence\+\+;\s*sequence_state = HOME_SEQUENCE_START_JOINTS;")
        self.assertIn("if (H[i].home_sequence >100) {", homing)
        self.assertIn("sequence = 999;", _src("emc/ini/inijoint.cc"))
        self.assertRegex(_src("emc/motion/command.c"), r"if \( get_homing_is_active\(\) \) \{\s*"
                              r"reportError\(_\(\"Can't jog any joint while homing.\"\)\);")
        m = F.FakeMachine(home_instantly=False)
        self.assertEqual(m.home_all_groups(), [[2], [1], [0]])        # the rig: Z, Y, X
        self.assertEqual(F.FakeMachine(home_sequence={"X": 0, "Y": 2, "Z": 3})
                         .home_all_groups(), [[0]])                 # no 1: over after 0
        self.assertEqual(F.FakeMachine(home_sequence={"X": 1, "Y": -1, "Z": 2})
                         .home_all_groups(), [[0, 1], [2]])         # no 0 is fine
        self.assertEqual(F.FakeMachine(home_sequence={"X": 0, "Y": 1})
                         .home_all_groups(), [[0], [1]])            # Z: 999, left out
        m.power_on()
        m.issue_home(-1)
        seen = [(list(m.homing[:3]), list(m.homed[:3]))]
        while m.homing_active:
            m.advance_homing()
            seen.append((list(m.homing[:3]), list(m.homed[:3])))
        self.assertEqual(seen, [
            ([0, 0, 1], [0, 0, 0]),       # Z homing
            ([0, 0, 0], [0, 0, 1]),       # the gap: Z homed, homing still active
            ([0, 1, 0], [0, 0, 1]),       # Y homing
            ([0, 0, 0], [0, 1, 1]),       # the gap
            ([1, 0, 0], [0, 1, 1]),       # X homing
            ([0, 0, 0], [1, 1, 1]),       # over
        ])

    def test_a_jog_in_the_gap_between_groups_is_refused_by_motion(self):
        m = F.FakeMachine(home_instantly=False)
        m.power_on()
        m.issue_home(-1)
        m.advance_homing()                       # Z homed, Y not started yet
        self.assertTrue(m.homing_active)
        m.submit("EMC_JOG_INCR", lambda: m.issue_jog_incr(1, 0, 1.0, 1.0))
        self.assertEqual(list(m.errors)[-1], (C["OPERATOR_ERROR"], "Can't jog any joint while homing."))

    @NEEDS_REFERENCE
    def test_one_joint_with_a_negative_sequence_homes_its_group(self):
        """do_home_one_joint (homing.c:265-281): a negative sequence homes
        every joint sharing abs(sequence) (home_one_targets)."""
        one = self.between(_src("emc/motion/homing.c"), "static void do_home_one_joint(int jno)",
                           "\n} // do_home_one_joint")
        self.assertIn("if (ABS(H[jj].home_sequence) == ABS(H[jno].home_sequence)) {", one)
        m = F.FakeMachine(home_instantly=False, home_sequence={"X": 1, "Y": -1, "Z": 0})
        m.power_on()
        m.issue_home(1)
        self.assertEqual(m.homing[:3], [1, 1, 0])
        m.finish_homing()
        self.assertEqual(m.homed[:3], [1, 1, 0])
        self.assertEqual(m.motion_mode, C["TRAJ_MODE_FREE"])      # Z still unhomed
        m.issue_home(2)
        m.finish_homing()
        self.assertEqual(m.motion_mode, C["TRAJ_MODE_TELEOP"])    # every joint homed now

    @NEEDS_REFERENCE
    def test_axis_asks_before_homing_again_and_homes_no_duplicated_letter_alone(self):
        """motionrules.home_question and home_axis_refusal follow AXIS - and
        ask more (plan design (g): any joint homed, not only all)."""
        axis = _src("emc/usr_intf/axis/scripts/axis.py")
        every = self.between(axis, "    def home_all_joints(event=None):",
                             "    def unhome_all_joints(event=None):")
        self.assertIn("isHomed = all_homed()", every)
        self.assertIn("Joint is already homed, are you sure you want to re-home?", every)
        self.assertIn("go_home(-1)", every)
        one = self.between(axis, "    def home_joint(event=None):", "    def home_joint_number(num):")
        self.assertRegex(one, r"if jora in duplicate_coord_letters:\s*\n\s*print\(_\(\"\\nIndividual "
                              r"axis homing disallowed for duplicated letter")
        self.assertIn("if s.homed[jnum]:", one)
        self.assertIn("This joint is already homed, are you sure you want to re-home?", one)
        go = self.between(axis, "def go_home(num):", "\n#----")
        self.assertIn("set_motion_teleop(0)", go)
        self.assertIn("c.home(num)", go)


class TestAdapterUsesOnlyWhatTheFakeProvides(unittest.TestCase):
    """Every `linuxcnc`/`hal` name LinuxCncAdapter touches must exist in the
    fake; TestFakeFidelity then ties the fake to the binding. Without this a
    typo in a rarely used name (say `L.MODE_MDl`) would fail only on the
    machine. The patterns capture whole identifiers (\\w+), so a misspelt
    name with a digit or lower-case letter is caught, not skipped. Runs
    without reference/."""

    #: The patterns each test below uses - one definition, so the typo test
    #: checks the very patterns the surface tests run.
    CONST_RES = (re.compile(r"\bL\.(\w+)"), re.compile(r"self\._linuxcnc\.(\w+)"))
    MODULE_CALL_RE = re.compile(r"\blinuxcnc\.(\w+)\(")
    #: The action channel through _send(), the estop and stop channels, and
    #: waits.
    COMMAND_RES = (
        re.compile(r"self\._send\(\)\.(\w+)"),
        re.compile(r"self\._(?:command|estop_channel|stop_channel)\.(\w+)"),
    )
    STAT_RES = (re.compile(r"\bs\.(\w+)"), re.compile(r"self\._stat\.(\w+)"))
    MAPPED_STAT_RE = re.compile(r'getattr\(stat, "(\w+)"')
    HAL_RE = re.compile(r"self\._hal\.(\w+)")
    #: Module-level objects, not constants.
    NOT_CONSTANTS = {"stat", "command", "error_channel", "error"}

    @classmethod
    def setUpClass(cls):
        cls.source = inspect.getsource(LinuxCncAdapter)

    @staticmethod
    def found(patterns, text):
        return set().union(*(set(p.findall(text)) for p in patterns))

    def test_constants(self):
        names = self.found(self.CONST_RES, self.source) - self.NOT_CONSTANTS
        self.assertTrue(names)
        self.assertLessEqual(names, set(F.CONSTANTS))

    def test_typo_with_a_digit_would_be_caught(self):
        """The example from the docstring, through the patterns used above."""
        text = "x = L.MODE_MDl + L.MODE_MD1 + self._linuxcnc.STATE_0N"
        names = self.found(self.CONST_RES, text)
        self.assertEqual(names, {"MODE_MDl", "MODE_MD1", "STATE_0N"})
        self.assertFalse(names <= set(F.CONSTANTS))
        self.assertEqual(self.found(self.COMMAND_RES, "self._send().m0de(1)"), {"m0de"})

    def test_module_level_names(self):
        names = set(self.MODULE_CALL_RE.findall(self.source))
        names |= self.found(self.CONST_RES[1:], self.source)
        self.assertTrue(names)
        with F.installed() as (lc, _, _, _):
            for name in names:
                with self.subTest(name=name):
                    self.assertTrue(hasattr(lc, name))

    def test_command_methods(self):
        names = self.found(self.COMMAND_RES, self.source)
        self.assertTrue(names)
        self.assertLessEqual(names, set(F.COMMAND_FORMATS))

    def test_every_action_send_is_counted(self):
        """_refused_without_status tells "nothing sent" from "sent, result
        unknown" by counting _send(); a send straight on self._command would
        make that message lie. Only waits use the channel directly."""
        direct = set(re.findall(r"self\._command\.(\w+)", self.source))
        self.assertEqual(direct, {"wait_complete"})

    def test_stat_attributes(self):
        names = self.found(self.STAT_RES, self.source) - {"poll"}
        self.assertTrue(names)
        # map_linuxcnc_state reads with getattr defaults, so a misspelt name
        # there would silently read the default forever.
        mapped = set(self.MAPPED_STAT_RE.findall(inspect.getsource(A.map_linuxcnc_state)))
        self.assertTrue(mapped)
        self.assertLessEqual(names | mapped, set(F.STAT_ATTRIBUTES))

    def test_module_docstring_lists_every_stat_field_read(self):
        """The module docstring's stat table is the reader's map of what
        LinuxCNC fields matter; it must not miss one the code reads."""
        names = self.found(self.STAT_RES, self.source) - {"poll"}
        names |= set(self.MAPPED_STAT_RE.findall(inspect.getsource(A.map_linuxcnc_state)))
        names |= {"joint"}   # read as s.joint / stat.joint[n][...]
        for name in sorted(names):
            with self.subTest(field=name):
                self.assertRegex(A.__doc__, r"stat\.{}\b".format(re.escape(name)))

    def test_hal_names(self):
        names = set(self.HAL_RE.findall(self.source))
        self.assertTrue(names)
        with F.installed() as (_, hal, _, _):
            for name in names:
                with self.subTest(name=name):
                    self.assertTrue(hasattr(hal, name))


# ---------------------------------------------------------------------------
# Construction and snapshot
# ---------------------------------------------------------------------------


class TestConstructor(FakeCase):
    def test_constructing_sends_no_command(self):
        """Opening the UI must not change the machine's mode or state."""
        self.make()
        self.assertEqual(self.machine.log, [])

    def test_estop_channel_is_opened_up_front_and_used_only_for_the_estop(self):
        """Opening a channel at estop time could fail or block exactly when it
        matters, so every channel exists after construction: the action
        channel, the estop's and (plan WP03) DURDUR's."""
        adapter = self.on(homed=True)
        self.assertEqual(len(self.machine.channels), 3)
        adapter.jog("X", 1)
        adapter.software_estop()
        adapter.software_estop_reset()
        estop_entry = self.machine.log.index(("state", (C["STATE_ESTOP"],)))
        estop_channel = self.machine.log_channel[estop_entry]
        others = [ch for i, ch in enumerate(self.machine.log_channel) if i != estop_entry]
        self.assertTrue(others)
        self.assertNotIn(estop_channel, others)

    def test_opens_a_pinless_hal_component(self):
        """hal.get_value refuses to run before this process created a HAL
        component (halmodule.cc:1010-1014). Without one every link pin read as
        None on the real machine."""
        self.make()
        name = "bettercnc-ui-{}".format(os.getpid())
        self.assertEqual(self.hal.components, [name])
        self.assertEqual(self.hal.ready, [name])

    def test_failed_hal_component_is_reported_not_hidden(self):
        """A stale component of the same name (another process) makes
        hal_init fail (hal_lib.c:243-250)."""
        state = F.FakeHal(ALL_LINK_PINS)
        state.foreign_components.append("bettercnc-ui-{}".format(os.getpid()))
        cm = F.installed(hal_state=state)
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        adapter = LinuxCncAdapter(load_profile("test-rig"))
        self.assertEqual(state.components, [])
        snap = adapter.snapshot()
        self.assertEqual(snap.alarms[0].severity, Severity.WARN)
        self.assertIn("HAL bileşeni açılamadı", snap.alarms[0].text)
        self.assertFalse(snap.link.connected)


class TestIniValuesFlowThrough(FakeCase):
    """Each INI value the adapter uses, set to a number that matches neither
    test-rig.ini nor any fallback, so "read from the INI" cannot pass by
    coincidence."""

    def adapter(self) -> LinuxCncAdapter:
        return self.on(homed=True, profile=rig_profile(self, DISTINCT))

    def test_servo_period(self):
        self.assertEqual(self.adapter().snapshot().link.servo_period_ms, 0.5)

    def test_jog_velocity_is_display_default_linear_velocity(self):
        adapter = self.adapter()
        adapter.jog("X", 1)
        adapter.jog("X", -1)
        self.assertEqual([j[3] for j in self.jogs()], [2.5, -2.5])

    def test_feed_ceiling(self):
        adapter = self.adapter()
        for pct, sent, message in (
            (150, 1.2, "Besleme %150 istendi; INI sınırı %120 uygulandı."),
            (110, 1.1, "Besleme %110."),
            (120, 1.2, "Besleme %120."),
        ):
            with self.subTest(pct=pct):
                adapter.set_feed_override(pct)
                self.assertEqual(self.machine.log[-1], ("feedrate", (sent,)))
                self.assertEqual(adapter.snapshot().message, message)

    def test_estop_reset_deadline_is_three_iocontrol_cycles(self):
        adapter = self.make(profile=rig_profile(self, DISTINCT))
        self.machine.fault_in = True
        adapter.software_estop_reset()
        self.assertGreaterEqual(self.clock.now, 0.6 - 1e-9)
        self.assertLess(self.clock.now, 0.6 + A._RESET_POLL_INTERVAL_S)
        self.assertIn("0.6 s", adapter.snapshot().message)

    def test_reset_deadline_without_a_usable_emcio_cycle_time(self):
        """Missing: LinuxCNC's own default, 0.100 s (emccfg.h:34), so 0.3 s.
        Zero or negative: the same fallback, by choice
        (DEFAULT_EMCIO_CYCLE_TIME_S) - a zero deadline would refuse every
        reset whose latch is not clear on the first poll. The other DISTINCT
        values stay, so the 0.3 s cannot come from the rig's own 0.100."""
        for value in (None, "0", "-0.1"):
            with self.subTest(cycle_time=value):
                edits = dict(DISTINCT)
                edits[("EMCIO", "CYCLE_TIME")] = value
                adapter = self.make(profile=rig_profile(self, edits))
                self.machine.fault_in = True
                adapter.software_estop_reset()
                self.assertGreaterEqual(self.clock.now, 0.3 - 1e-9)
                self.assertLess(self.clock.now, 0.3 + A._RESET_POLL_INTERVAL_S)
                self.assertIn("LinuxCNC 0.3 s içinde", adapter.snapshot().message)

    def test_feed_ceiling_is_rounded_like_axis(self):
        """axis.py:3353-3355 int(max_feed_override * 100 + 0.5): 1.15 * 100 is
        114.999... in binary, so truncating would give 114."""
        adapter = self.on(homed=True, profile=rig_profile(self, {
            ("DISPLAY", "MAX_FEED_OVERRIDE"): "1.15",
        }))
        adapter.set_feed_override(115)
        self.assertEqual(adapter.snapshot().message, "Besleme %115.")
        adapter.set_feed_override(150)
        self.assertEqual(self.machine.log[-1], ("feedrate", (1.15,)))
        self.assertEqual(adapter.snapshot().message, "Besleme %150 istendi; INI sınırı %115 uygulandı.")

    def test_jog_velocity_falls_back_to_traj(self):
        """axis.py:3400-3403: [DISPLAY] first, then [TRAJ]."""
        adapter = self.on(profile=rig_profile(self, {
            ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"): None,
            ("TRAJ", "DEFAULT_LINEAR_VELOCITY"): "3.0",
        }))
        adapter.jog("X", 1)
        self.assertEqual(self.jogs()[0][3], 3.0)

    JOG_VELOCITY_REFUSAL = (
        "Jog hızı okunamadı — INI'de [DISPLAY] veya [TRAJ] "
        "DEFAULT_LINEAR_VELOCITY yok ya da sayı değil."
    )

    def test_no_jog_velocity_anywhere_refuses_the_jog(self):
        adapter = self.on(profile=rig_profile(self, {
            ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"): None,
            ("TRAJ", "DEFAULT_LINEAR_VELOCITY"): None,
        }))
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, self.JOG_VELOCITY_REFUSAL)

    def test_unparsable_jog_velocity_is_not_guessed(self):
        """The same message covers a value that is there but not a number."""
        adapter = self.on(profile=rig_profile(self, {
            ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"): "slow",
        }))
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, self.JOG_VELOCITY_REFUSAL)

    def test_unreadable_home_ignore_limits_stays_unknown(self):
        """state.JointHoming.ignore_limits: None means the INI value cannot be
        read, and must never become False ("limits active"). Both adapters
        pass it through."""
        profile = rig_profile(self, {("JOINT_0", "HOME_IGNORE_LIMITS"): "maybe"})
        for name, snap in (
            ("linuxcnc", self.make(profile=profile).snapshot()),
            ("mock", MockAdapter(profile).snapshot()),
        ):
            with self.subTest(adapter=name):
                self.assertIsNone(snap.homing.joints[0].ignore_limits)
                self.assertIsNotNone(snap.homing.joints[1].ignore_limits)


class TestSnapshot(FakeCase):
    def test_fresh_machine_maps_to_estopped_and_unhomed(self):
        snap = self.make().snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertIs(snap.estop.drive_power, DrivePower.UNKNOWN)  # no estop input on the rig
        self.assertEqual([j.phase for j in snap.homing.joints], [HomingPhase.NOT_HOMED] * 3)
        self.assertTrue(snap.start_blocks)

    def test_estop_reset_state_is_still_software_disabled(self):
        """Out of estop but not ON (determineState, emctask.cc:419-430):
        LinuxCNC refuses motion there too, so the UI must."""
        adapter = self.make()
        self.machine.estop_reset_state()
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertEqual(snap.status_label, "DEVRE DIŞI")
        self.assertIn("Makine yazılımda devre dışı (acil stop).", snap.start_blocks)
        # Task in MDI (idle): without the up-front ON check jog and home
        # would first send mode(MANUAL). Refused before anything is sent.
        self.machine.task_mode = C["MODE_MDI"]
        for name, call, text in (
            ("jog", lambda: adapter.jog("X", 1), "Makine devre dışı — jog yok."),
            ("home", adapter.home_all, "Makine devre dışı — referans alınamaz."),
        ):
            with self.subTest(action=name):
                call()
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, text)

    def test_snapshot_polls_so_it_sees_new_state(self):
        adapter = self.make()
        adapter.snapshot()
        self.machine.power_on(homed=True)
        self.machine.axis_pos[0] = 12.5
        self.machine.g5x_offset[0] = 2.5
        self.machine.g5x_index = 2
        self.machine.feedrate = 0.5
        self.machine.file = "/tmp/a.ngc"
        snap = adapter.snapshot()
        self.assertFalse(snap.estop.software_disabled)
        self.assertTrue(snap.homing.all_homed)
        x = snap.positions[0]
        self.assertEqual((x.machine, x.work, x.homed), (12.5, 10.0, True))
        self.assertEqual(snap.wcs, "G55")
        self.assertEqual(snap.feed_override_pct, 50)
        self.assertEqual(snap.program_file, "/tmp/a.ngc")

    def test_homing_in_progress_is_shown(self):
        adapter = self.on(machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        snap = adapter.snapshot()
        self.assertEqual([j.phase for j in snap.homing.joints],      # Z first
                         [HomingPhase.NOT_HOMED, HomingPhase.NOT_HOMED, HomingPhase.HOMING])
        self.assertEqual(snap.status_label, "REFERANS ALINIYOR")
        self.machine.finish_homing()
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_error_channel_is_drained_newest_first_with_severity(self):
        """Every message in the log; of the two error kinds the FIRST is the
        message (plan WP04: until then the last one won, and LinuxCNC says
        the specific error before the generic one)."""
        adapter = self.make()
        self.machine.errors.extend([
            (C["NML_ERROR"], "first"),
            (C["OPERATOR_TEXT"], "second"),
            (C["NML_DISPLAY"], "third"),
            (C["OPERATOR_ERROR"], "fourth\n"),
        ])
        snap = adapter.snapshot()
        got = [(a.text, a.severity, a.source) for a in snap.alarms]
        self.assertEqual(got, [
            ("fourth", Severity.ERROR, "linuxcnc"),
            ("third", Severity.INFO, "linuxcnc"),
            ("second", Severity.INFO, "linuxcnc"),
            ("first", Severity.ERROR, "linuxcnc"),
        ])
        self.assertEqual(snap.message, "LinuxCNC: first")
        self.assertEqual((snap.last_error.text, snap.last_error.raw), ("LinuxCNC: first", "first"))
        self.assertEqual(len(self.machine.errors), 0)
        self.assertEqual(len(adapter.snapshot().alarms), 4, "drained twice")

    def test_text_and_display_kinds_never_become_the_message(self):
        """Only NML_ERROR / OPERATOR_ERROR are refusals; every text and display
        kind is INFO and leaves the operator message as it was."""
        adapter = self.make()
        self.machine.errors.append((C["OPERATOR_ERROR"], "the refusal"))
        adapter.snapshot()
        for kind in ("OPERATOR_TEXT", "OPERATOR_DISPLAY", "NML_TEXT", "NML_DISPLAY"):
            with self.subTest(kind=kind):
                self.machine.errors.append((C[kind], "chatter " + kind))
                snap = adapter.snapshot()
                self.assertEqual(snap.alarms[0].text, "chatter " + kind)
                self.assertIs(snap.alarms[0].severity, Severity.INFO)
                self.assertEqual(snap.message, "LinuxCNC: the refusal")

    def test_hal_pins_present_are_read(self):
        snap = self.make(hal_pins={
            LinkHealth.HAL_PACKET_ERROR_TOTAL: 3,
            LinkHealth.HAL_WATCHDOG_HAS_BIT: True,
            LinkHealth.HAL_IO_ERROR: False,
        }).snapshot()
        self.assertTrue(snap.link.connected)
        self.assertEqual(snap.link.packet_error_total, 3)
        self.assertIs(snap.link.watchdog_has_bit, True)
        self.assertIs(snap.link.io_error, False)
        self.assertFalse(snap.link.healthy)

    def test_packet_errors_alone_make_the_link_unhealthy(self):
        snap = self.make(hal_pins={
            LinkHealth.HAL_PACKET_ERROR_TOTAL: 2,
            LinkHealth.HAL_WATCHDOG_HAS_BIT: False,
            LinkHealth.HAL_IO_ERROR: False,
        }).snapshot()
        self.assertFalse(snap.link.healthy)
        self.assertEqual(
            snap.link.problems,
            [
                "Kartla iletişimde 2 paket kayboldu. Sayı artmaya devam ederse "
                "Ethernet kablosunu ve bağlantısını kontrol edin."
            ],
        )

    def test_missing_pin_is_none_not_zero(self):
        """0 packet errors is a measurement; a missing pin is not."""
        snap = self.make(hal_pins={LinkHealth.HAL_IO_ERROR: False}).snapshot()
        self.assertIsNone(snap.link.packet_error_total)
        self.assertIsNone(snap.link.watchdog_has_bit)
        self.assertIs(snap.link.io_error, False)
        self.assertTrue(snap.link.connected)

    def test_no_readable_pin_is_not_shown_as_a_healthy_link(self):
        snap = self.make(hal_pins={}).snapshot()
        self.assertFalse(snap.link.connected)
        self.assertFalse(snap.link.healthy)

    def test_no_hal_module_means_disconnected(self):
        snap = self.make(hal_available=False).snapshot()
        self.assertFalse(snap.link.connected)
        self.assertIsNone(snap.link.packet_error_total)

    def test_servo_period_without_one_in_the_ini_is_unknown(self):
        """Never the profile's platform figure: the link card must not show a
        period the INI does not give (LinkHealth.servo_period_ms None renders
        "—")."""
        profile = rig_profile(self, {("EMCMOT", "SERVO_PERIOD"): None})
        profile = profile._replace(platform=profile.platform._replace(servo_period_ms=0.75))
        self.assertIsNone(self.make(profile=profile).snapshot().link.servo_period_ms)
        self.assertIsNone(MockAdapter(profile).snapshot().link.servo_period_ms)

    def test_no_ini_means_no_servo_period(self):
        self.assertIsNone(self.make("marble").snapshot().link.servo_period_ms)

    def test_startup_latch_on_a_wired_profile_is_not_a_hardware_cut(self):
        """_read_hal gives no "estop_input" (no profile names that pin yet),
        and stat.estop - 1 at every startup (ioControl.cc:791) - must not
        stand in for it: drive power stays UNKNOWN, so the reset stays
        possible."""
        profile = load_profile("test-rig")
        wired = profile._replace(
            capabilities={**profile.capabilities, Capability.ESTOP_INPUT: present("test")}
        )
        snap = self.make(profile=wired).snapshot()
        self.assertEqual(self.machine.aux_estop, 1)
        self.assertIs(snap.estop.drive_power, DrivePower.UNKNOWN)
        self.assertTrue(snap.estop.hardware_input_present)

    def test_drain_reads_at_most_a_bounded_number_of_messages(self):
        """Error_poll can keep answering while valid() holds; one drain must
        end (emcmodule.cc:1562-1597)."""
        adapter = self.make()
        for n in range(A._MAX_ERRORS_PER_DRAIN + 10):
            self.machine.errors.append((C["OPERATOR_TEXT"], "t{}".format(n)))
        snap = adapter.snapshot()
        self.assertEqual(len(snap.alarms), A._MAX_ERRORS_PER_DRAIN)
        self.assertEqual(len(self.machine.errors), 10)
        self.assertEqual(len(adapter.snapshot().alarms), A._MAX_ERRORS_PER_DRAIN + 10)

    def test_unknown_error_kind_is_an_error_and_ends_the_drain(self):
        """Error_poll's default branch: (type, "unrecognized error N"),
        e.g. -1 from a failed NML read (emcmodule.cc:1588-1593)."""
        for kind in (-1, 99):
            with self.subTest(kind=kind):
                adapter = self.make()
                self.machine.errors.extend([
                    (kind, "unrecognized error {}".format(kind)),
                    (C["OPERATOR_TEXT"], "after"),
                ])
                snap = adapter.snapshot()
                self.assertEqual(
                    [(a.text, a.severity) for a in snap.alarms],
                    [("unrecognized error {}".format(kind), Severity.ERROR)],
                )
                self.assertEqual(snap.message, "LinuxCNC: unrecognized error {}".format(kind))
                self.assertEqual(list(self.machine.errors), [(C["OPERATOR_TEXT"], "after")])


class TestStatusChannelLost(FakeCase):
    """stat.poll() raises linuxcnc.error once the status buffer is invalid
    (emcmodule.cc:277-283). The UI polls snapshot() every second, so it must
    not raise; it must say the machine state is unknown, once, never show
    invented readings, and recover."""

    def test_snapshot_shows_unknown_state_instead_of_raising(self):
        adapter = self.on(homed=True)
        self.machine.axis_pos[0] = 12.5
        self.assertFalse(adapter.snapshot().estop.software_disabled)
        self.machine.channels_valid = False
        snap = adapter.snapshot()
        self.assertFalse(snap.status_known)
        self.assertTrue(snap.estop.software_disabled)
        self.assertFalse(snap.homing.all_homed)
        self.assertIs(snap.alarms[0].severity, Severity.ERROR)
        self.assertIn("durum kanalı okunamıyor", snap.alarms[0].text)
        self.assertIn("bağlantı yok", snap.message)
        self.assertTrue(snap.start_blocks)
        self.assertFalse(snap.can_jog)

    def test_no_position_is_produced_while_the_status_is_unknown(self):
        """Neither the last good reading (12.5) nor a made-up 0.0: there are
        no positions at all, so the DRO has nothing to show as measured."""
        adapter = self.on(homed=True)
        self.machine.axis_pos[0] = 12.5
        adapter.snapshot()
        self.machine.channels_valid = False
        self.assertEqual(adapter.snapshot().positions, ())

    def test_the_link_still_comes_from_the_hal_pins(self):
        """HAL is read separately from LinuxCNC's status (state.MachineState.
        status_known), so the link card keeps its own, measured meaning."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        self.assertTrue(adapter.snapshot().link.connected)
        adapter = self.on(homed=True, hal_pins={})
        self.machine.channels_valid = False
        self.assertFalse(adapter.snapshot().link.connected)

    def test_unreadable_error_channel_alone_does_not_break_the_snapshot(self):
        """Error_poll raises "Error buffer invalid" (emcmodule.cc:1562-1565);
        the snapshot still shows the readable status."""
        adapter = self.on(homed=True)
        self.machine.error_channel_valid = False
        self.machine.errors.append((C["OPERATOR_ERROR"], "never read"))
        snap = adapter.snapshot()
        self.assertTrue(snap.status_known)
        self.assertFalse(snap.estop.software_disabled)
        self.assertNotIn("never read", [a.text for a in snap.alarms])

    def test_the_alarm_is_logged_once_per_outage(self):
        adapter = self.make()
        self.machine.channels_valid = False
        for _ in range(5):
            snap = adapter.snapshot()
        self.assertEqual(sum("okunamıyor" in a.text for a in snap.alarms), 1)

    def test_recovers_when_polling_works_again(self):
        """Recovery is logged once: a second snapshot must neither add
        another alarm nor overwrite the operator message again."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        adapter.snapshot()
        self.machine.channels_valid = True
        snap = adapter.snapshot()
        self.assertTrue(snap.status_known)
        self.assertFalse(snap.estop.software_disabled)
        self.assertTrue(snap.link.connected)
        self.assertTrue(snap.homing.all_homed)
        self.assertEqual(len(snap.positions), 3)
        self.assertEqual(snap.message, "LinuxCNC durumu yeniden okunuyor.")
        self.assertIs(snap.alarms[0].severity, Severity.INFO)
        adapter.set_feed_override(50)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Besleme %50.")
        self.assertEqual(sum("yeniden okunuyor" in a.text for a in snap.alarms), 1)


class TestActionsWithTheStatusLost(FakeCase):
    """Every action that polls is guarded (_refused_without_status): with the
    status buffer invalid it refuses in Turkish and sends nothing, instead of
    letting linuxcnc.error escape app.Session.act (which catches only
    ValueError/KeyError) and kill the request without an answer."""

    REFUSAL = "LinuxCNC'ye bağlantı yok — komut gönderilmedi."

    def actions(self, path):
        return (
            ("jog", lambda a: a.jog("X", 1)),
            ("home", lambda a: a.home_all()),
            ("reset", lambda a: a.software_estop_reset()),
            ("load", lambda a: a.load_program(path)),
            ("start", lambda a: a.program_start()),
            ("wcs", lambda a: a.set_wcs("G55")),
        )

    def test_each_action_refuses_and_sends_nothing(self):
        path = self.ngc()
        for name, call in self.actions(path):
            with self.subTest(action=name):
                adapter = self.on(homed=True)
                self.machine.file = path
                self.machine.channels_valid = False
                self.assertIsNone(call(adapter))
                self.assertEqual(self.machine.log, [])
                snap = adapter.snapshot()
                self.assertFalse(snap.status_known)
                self.assertEqual(
                    snap.message,
                    "MDI: G55 — " + self.REFUSAL if name == "wcs" else self.REFUSAL,
                )
                self.assertEqual(sum("okunamıyor" in a.text for a in snap.alarms), 1)

    def test_mdi_returns_a_refused_entry(self):
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        entry = adapter.send_mdi("G0 X1")
        self.assertEqual((entry.command, entry.accepted, entry.note), ("G0 X1", False, self.REFUSAL))
        self.assertEqual(self.machine.log, [])
        snap = adapter.snapshot()
        self.assertEqual(snap.mdi_history[-1], entry)
        self.assertEqual(snap.message, "MDI: G0 X1 — " + self.REFUSAL)

    def test_stop_still_sends_abort_and_says_the_result_is_unknown(self):
        """Stopping never waits on a read: both aborts go out first - the
        first (stop_motion) reads nothing at all. Afterwards the status
        cannot be read, so the message must not claim either "stopped" or
        "nothing sent". (Until plan WP03 this was program_stop, one abort.)"""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(self.machine.calls(), ["abort", "abort", "wait_complete"])
        self.assertEqual(
            adapter.snapshot().message,
            "LinuxCNC'ye bağlantı koptu — gönderilen komutun sonucu bilinmiyor.",
        )

    def test_status_lost_between_send_and_read_back(self):
        """Lost after mode() went out: something was sent, so "komut
        gönderilmedi" would be false."""
        class DiesOnModeChange(F.FakeMachine):
            def issue_set_mode(self, mode):
                super().issue_set_mode(mode)
                self.channels_valid = False

        adapter = self.on(homed=True, machine=DiesOnModeChange())
        entry = adapter.send_mdi("G0 X1")
        self.assertFalse(entry.accepted)
        self.assertEqual(self.machine.calls(), ["mode", "wait_complete"])
        self.assertEqual(self.machine.mdi_executed, [])
        self.assertIn("bağlantı koptu", entry.note)

    def test_software_estop_never_polls(self):
        """software_estop must go out exactly when the status side has
        failed, so it may not read stat at all (a poll would raise first)."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        polls = self.machine.stat_polls
        adapter.software_estop()
        self.assertEqual(self.machine.stat_polls, polls)
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])

    def test_the_feed_override_message_survives_the_poll_after_an_outage(self):
        """The poll after an outage writes "yeniden okunuyor"; it must come
        before this action's own message, not after (review 2026-10-21)."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        adapter.snapshot()
        self.machine.channels_valid = True
        adapter.set_feed_override(50)
        self.assertEqual(adapter.snapshot().message, "Besleme %50.")
        self.assertIn(("feedrate", (0.5,)), self.machine.log)

    def test_the_step_does_not_poll_but_the_feed_override_refuses(self):
        """The jog step is the adapter's own; the feed override is a send,
        and a send to a stuck task spins 5 s holding the GIL (audit,
        2026-10-21: it used to go out without the guard's question)."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        polls = self.machine.stat_polls
        adapter.set_jog_step(0.1)
        self.assertEqual(self.machine.stat_polls, polls)
        adapter.set_feed_override(50)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, A.NO_LINUXCNC_REFUSAL)

    def test_a_session_action_answers_instead_of_raising(self):
        adapter = self.on(homed=True)
        session = Session(adapter, self.profile, load_machine_config(self.profile))
        self.machine.channels_valid = False
        for action, value in (("jog", "X:1"), ("home_all", ""), ("mdi", "G0 X1"),
                              ("program_start", ""), ("program_stop", ""),
                              ("software_reset", ""), ("wcs", "G55")):
            with self.subTest(action=action):
                session.act(action, value)
        self.assertNotIn("jog", self.machine.calls())
        self.assertNotIn("mdi", self.machine.calls())


# ---------------------------------------------------------------------------
# Jog
# ---------------------------------------------------------------------------


class TestJog(FakeCase):
    def test_before_homing_jogs_the_joint(self):
        """The bug this file exists for: before homing motion is in free mode
        and drops an axis jog (command.c:436-443). axis.py jogs the joint
        (get_jog_mode, axis.py:3225-3242)."""
        adapter = self.on(homed=False)
        adapter.jog("X", 1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls(), ["jog"])
        fn, jjogmode, index, vel, incr = self.jogs()[0]
        self.assertEqual((fn, jjogmode, index), (C["JOG_INCREMENT"], 1, 0))
        self.assertEqual(self.machine.joint_pos[0], 1.0)
        # Plan WP06: what moved, which way, how far - no "joint"/"axis".
        self.assertEqual(adapter.snapshot().message, "Jog X+1 mm.")

    def test_after_homing_jogs_the_axis(self):
        """Homed, the fake stands at the rig's HOME (5, 5, -5), as homing
        leaves it (FakeMachine.end_homing)."""
        adapter = self.on(homed=True)
        adapter.jog("Z", 1)
        self.assertNoLinuxCncErrors()
        fn, jjogmode, index, vel, incr = self.jogs()[0]
        self.assertEqual((jjogmode, index), (0, 2))
        self.assertAlmostEqual(self.machine.axis_pos[2], -4.0)
        self.assertEqual(adapter.snapshot().message, "Jog Z+1 mm.")

    def test_homed_but_in_free_mode_switches_to_teleop_first(self):
        """axis.py:3239-3241: switch motion with teleop_enable, then wait."""
        adapter = self.on(homed=True)
        self.machine.motion_mode = C["TRAJ_MODE_FREE"]
        adapter.jog("Y", 1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[:2], [("teleop_enable", (1,)), ("wait_complete", (WAIT,))])
        self.assertEqual(self.machine.calls(), ["teleop_enable", "wait_complete", "jog"])
        self.assertEqual(self.jogs()[0][1], 0)

    def test_teleop_not_reached_refuses_without_jogging(self):
        """Motion drops a teleop request while not in position (control.c:
        927-958). The adapter reads the mode back instead of jogging anyway.
        Motion here ignores the request (as it does out of position): with
        a joint jog actually running the jog is refused before anything is
        sent (plan WP06, test_jog_rules)."""
        adapter = self.on(homed=True)
        self.machine.motion_mode = C["TRAJ_MODE_FREE"]
        teleop = self.machine.issue_teleop
        self.machine.issue_teleop = lambda enable: None
        adapter.jog("Y", 1)
        self.machine.issue_teleop = teleop
        self.assertEqual(self.machine.calls(), ["teleop_enable", "wait_complete"])
        self.assertEqual(
            adapter.snapshot().message,
            "Jog modu değişmedi (LinuxCNC hareket sürerken mod değiştirmez) — jog yok.",
        )

    def test_unhomed_teleop_set_elsewhere_is_kept(self):
        """Identity kins allow teleop unhomed (motion.c:166-171 only checks
        other kins); axis.py keeps whatever motion is in (:3230-3237). A
        teleop jog is checked against the axis's soft limits, homed or not
        (axis.c:289-290; plan WP06): X stands inside them here."""
        adapter = self.on(homed=False)
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 50.0
        adapter.jog("X", -1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls(), ["jog"])
        self.assertEqual(self.jogs()[0][1], 0)

    def test_non_identity_kinematics_keep_free_mode_when_homed(self):
        """axis.py:3227-3237: only identity kins force teleop after homing."""
        machine = F.FakeMachine(kinematics_type=C["KINEMATICS_BOTH"])
        adapter = self.on(homed=True, machine=machine)
        machine.motion_mode = C["TRAJ_MODE_FREE"]
        adapter.jog("Y", 1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls(), ["jog"])
        self.assertEqual(self.jogs()[0][1:3], (1, 1))

    def test_non_identity_kinematics_in_coord_mode_switch_to_teleop(self):
        machine = F.FakeMachine(kinematics_type=C["KINEMATICS_BOTH"])
        adapter = self.on(homed=True, machine=machine)
        machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.jog("Y", 1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[0], ("teleop_enable", (1,)))
        self.assertEqual(self.jogs()[0][1:3], (0, 1))

    def test_from_mdi_mode_switches_to_manual_and_waits(self):
        adapter = self.on(homed=False)
        self.machine.task_mode = C["MODE_MDI"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.jog("X", 1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[:2], [("mode", (C["MODE_MANUAL"],)), ("wait_complete", (WAIT,))])
        self.assertEqual(self.machine.calls(), ["mode", "wait_complete", "jog"])
        self.assertEqual(self.jogs()[0][1], 1)

    def test_joint_number_and_axis_index_differ_when_joints_skip_a_letter(self):
        """Joint mode addresses joints, teleop addresses "XYZABCUVW" slots. On
        the test rig the two coincide, so a machine whose joint 1 is Z is the
        only way to tell them apart."""
        cfg = {0: {"axis": "X"}, 1: {"axis": "Z"}}
        # Homed, Z starts at its HOME, -5 (FakeMachine.end_homing).
        for homed, expected, z in ((False, (1, 1), 1.0), (True, (0, 2), -4.0)):
            with self.subTest(homed=homed):
                adapter = self.make(machine=F.FakeMachine(coordinates="XZ"), joint_config=cfg)
                self.machine.power_on(homed=homed)
                adapter.jog("Z", 1)
                self.assertNoLinuxCncErrors()
                self.assertEqual(self.jogs()[0][1:3], expected)
                self.assertAlmostEqual(self.machine.axis_pos[2], z)

    def test_gantry_duplicate_letter_jogs_the_first_joint_then_the_axis(self):
        """XYYZ: joints 1 and 2 both carry Y. axis.py jogs the first joint of a
        duplicated letter (:1938-1942); after homing the axis slot is Y's."""
        cfg = {0: {"axis": "X"}, 1: {"axis": "Y"}, 2: {"axis": "Y"}, 3: {"axis": "Z"}}
        for homed, expected in ((False, (1, 1)), (True, (0, 1))):
            with self.subTest(homed=homed):
                adapter = self.make(machine=F.FakeMachine(coordinates="XYYZ"), joint_config=cfg)
                self.machine.power_on(homed=homed)
                adapter.jog("Y", 1)
                self.assertNoLinuxCncErrors()
                self.assertEqual(self.jogs()[0][1:3], expected)
                adapter.jog("Z", 1)
                self.assertEqual(self.jogs()[1][1:3], (1, 3) if not homed else (0, 2))

    def test_direction_is_the_velocity_sign_and_the_step_stays_positive(self):
        """command.c:873-877: the increment is a magnitude."""
        adapter = self.on()
        adapter.jog("Y", -1)
        _, _, _, vel, incr = self.jogs()[0]
        self.assertLess(vel, 0)
        self.assertGreater(incr, 0)
        self.assertEqual(self.machine.joint_pos[1], -1.0)

    def test_step_default_matches_the_mock_and_set_jog_step_changes_it(self):
        adapter = self.on()
        mock = MockAdapter(self.profile)
        self.assertEqual(adapter.snapshot().jog_step_mm, mock.snapshot().jog_step_mm)
        adapter.jog("X", 1)
        adapter.set_jog_step(0.1)
        adapter.jog("X", 1)
        self.assertEqual([j[4] for j in self.jogs()], [mock.snapshot().jog_step_mm, 0.1])
        self.assertEqual(adapter.snapshot().jog_step_mm, 0.1)

    def test_a_step_that_is_not_a_positive_number_is_refused(self):
        """A negative increment would reverse the jog (command.c:873-877)."""
        adapter = self.on()
        for bad in (0, -1, float("nan"), float("inf")):
            with self.subTest(step=bad):
                adapter.set_jog_step(bad)
                self.assertEqual(adapter.snapshot().jog_step_mm, 1.0)
                self.assertIn("Geçersiz jog adımı", adapter.snapshot().message)

    def test_refused_while_estopped_like_the_mock(self):
        adapter = self.make()
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        mock = MockAdapter(self.profile)
        mock.jog("X", 1)
        self.assertEqual(adapter.snapshot().message, mock.snapshot().message)

    def test_refused_while_a_program_runs(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Program çalışırken jog yok.")

    def test_refused_without_a_configured_velocity(self):
        adapter = self.on(profile_key="marble")
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        self.assertIn("DEFAULT_LINEAR_VELOCITY", adapter.snapshot().message)

    def test_bad_axis_and_direction_raise_what_session_catches(self):
        adapter = self.on()
        with self.assertRaises(KeyError):
            adapter.jog("Q", 1)
        for bad in (0, 2, -5):
            with self.subTest(direction=bad):
                with self.assertRaises(ValueError):
                    adapter.jog("X", bad)
        self.assertEqual(self.machine.log, [])


class TestRefusedWhileMdiRuns(FakeCase):
    """The MDI half of the running gate (axis.py:963-966). Switching to MANUAL
    during an MDI move would abort it (emcTaskSetMode, emctask.cc:265-307),
    silently. Each action must send nothing."""

    def test_jog_home_load_send_nothing(self):
        path = self.ngc()
        for name, call, text in (
            ("jog", lambda a: a.jog("X", 1), "Program çalışırken jog yok."),
            ("home", lambda a: a.home_all(), "Program çalışırken referans alınamaz."),
            ("load", lambda a: a.load_program(path), "Program çalışırken yeni program yüklenemez."),
        ):
            with self.subTest(action=name):
                adapter = self.on(homed=True)
                self.run_mdi_program()
                call(adapter)
                self.assertEqual(self.machine.calls(), [])
                self.assertEqual(self.machine.interp_state, C["INTERP_READING"])
                self.assertEqual(adapter.snapshot().message, text)


# ---------------------------------------------------------------------------
# Homing, estop
# ---------------------------------------------------------------------------


class TestHomeAll(FakeCase):
    def test_sequence_matches_axis_without_waiting_for_the_homing_move(self):
        """axis.py:2693-2702 + go_home :1954-1957, minus axis.py's wait after
        home(-1): task reports DONE only when the whole homing move is over
        (taskintf.cc:974-983), and that wait would hold the GIL meanwhile."""
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("teleop_enable", (0,)), ("wait_complete", (WAIT,)), ("home", (-1,)),
        ])
        self.assertEqual(adapter.snapshot().message, "Referans alma başlatıldı.")

    def test_rehoming_leaves_teleop_first(self):
        """After homing MANUAL means teleop (emctask.cc:277-278) and motion
        homes only in free mode (command.c:1374-1378). Without teleop off the
        second home is refused. Confirmed: a homed machine is asked about
        first (plan WP07, test_homing_single)."""
        adapter = self.on(homed=True, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all(confirmed=True)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[-1], ("home", (-1,)))
        self.assertEqual(self.machine.motion_mode, C["TRAJ_MODE_FREE"])
        # Motion goes back to teleop by itself once every joint is homed
        # (homing.c:1400, control.c:261-263).
        self.machine.finish_homing()
        self.assertEqual(self.machine.motion_mode, C["TRAJ_MODE_TELEOP"])

    def test_leaving_teleop_aborts_a_teleop_jog_then_homes(self):
        """EMCMOT_FREE aborts every teleop jog (axis_jog_abort_all,
        command.c:556-567), and motion switches once in position
        (control.c:961-972) - in the fake, at once."""
        adapter = self.on(homed=True)
        self.machine.jogging_active = True
        adapter.home_all(confirmed=True)
        self.assertNoLinuxCncErrors()
        self.assertFalse(self.machine.jogging_active)
        self.assertEqual(self.machine.calls(), ["teleop_enable", "wait_complete", "home"])

    def test_teleop_not_left_in_time_refuses_without_homing(self):
        """On the machine the aborted jog still has to decelerate, so motion
        may not have left teleop when the bounded wait ends. Still in teleop,
        home would be refused ("must be in joint mode to home"); the operator
        is told to retry."""
        class SlowToLeaveTeleop(F.FakeMachine):
            def issue_teleop(self, enable):
                if enable:
                    super().issue_teleop(enable)

        adapter = self.on(homed=True, machine=SlowToLeaveTeleop())
        adapter.home_all(confirmed=True)
        self.assertNotIn("home", self.machine.calls())
        self.assertEqual(adapter.snapshot().message, (
            "Eklem moduna geçilemedi (LinuxCNC jogu durdurup hareket bitince "
            "modu değiştirir; tekrar deneyin) — referans alınamaz."
        ))

    def test_refused_while_homing_is_in_progress(self):
        """A second home-all during homing clears joint 0's homed flag and
        does not restart (homing.c:553-560, :258-263): the machine would end
        with X unhomed. The fake models that, so the refusal is what keeps
        joint 0 homed."""
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.machine.homing[0] = 0      # X finished first
        self.machine.homed[0] = 1
        del self.machine.log[:]
        adapter.home_all()
        self.assertEqual(self.machine.log, [])
        # Plan WP07: the refusal says how a homing is ended.
        self.assertEqual(adapter.snapshot().message,
                         "Referans alma sürüyor — durdurmak için «Durdur».")
        self.machine.finish_homing()
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_a_home_all_leaves_motion_in_teleop_and_the_next_jog_needs_no_switch(self):
        """homing.c:1400 + control.c:261-263: the homing that leaves every
        joint homed switches motion to teleop. Until the P0 cleanup the fake
        stayed in free mode and a jog after Home All logged one
        teleop_enable(1) LinuxCNC would not need."""
        adapter = self.on(homed=False)
        adapter.home_all()
        self.assertEqual(self.machine.motion_mode, C["TRAJ_MODE_TELEOP"])
        del self.machine.log[:]
        adapter.jog("Z", -1)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls(), ["jog"])
        self.assertEqual(self.jogs()[0][1:3], (0, 2))          # an axis (teleop) jog

    def test_the_fake_unhomes_joint_0_on_a_second_home_all(self):
        """What the refusal above prevents, straight on the fake: with joint
        0 homed first, a second home(-1) in the sequence - mid-group or in
        the gap between groups (homing_active, homing.c:258-263) - unhomes
        joint 0 and does not restart."""
        for gap in (False, True):
            with self.subTest(gap=gap):
                machine = F.FakeMachine(home_instantly=False,
                                        home_sequence={"X": 0, "Y": 1, "Z": 2})
                machine.power_on()
                with F.installed(machine=machine) as (lc, _, m, _):
                    c = lc.command()
                    c.home(-1)
                    self.assertEqual(m.homing[:3], [1, 0, 0])
                    m.advance_homing()                       # X homed: the gap
                    self.assertEqual((m.homed[:3], m.homing[:3]), ([1, 0, 0], [0, 0, 0]))
                    if not gap:
                        m.advance_homing()                   # Y starts
                        self.assertEqual(m.homing[:3], [0, 1, 0])
                    c.home(-1)
                    self.assertEqual(m.homed[0], 0)
                    m.finish_homing()
                self.assertEqual(m.homed[:3], [0, 1, 1])
                self.assertEqual(m.motion_mode, C["TRAJ_MODE_FREE"])   # not all homed

    def test_from_auto_mode_switches_to_manual_first(self):
        adapter = self.on(homed=False)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.home_all()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[:2], [("mode", (C["MODE_MANUAL"],)), ("wait_complete", (WAIT,))])
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_refused_while_a_program_runs(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        adapter.home_all()
        self.assertEqual(self.machine.log, [])
        self.assertIn("Program çalışırken", adapter.snapshot().message)

    def test_refused_while_estopped_like_the_mock(self):
        adapter = self.make()
        adapter.home_all()
        self.assertEqual(self.machine.log, [])
        mock = MockAdapter(self.profile)
        mock.home_all()
        self.assertEqual(adapter.snapshot().message, mock.snapshot().message)


class TestEveryWaitIsBounded(FakeCase):
    def test_no_action_waits_with_the_binding_default(self):
        """wait_complete() with no argument waits up to EMC_COMMAND_TIMEOUT =
        5 s holding the GIL (emcmodule.cc:209, :1383-1388). Every action is
        driven here and every wait must carry WAIT_TIMEOUT_S."""
        adapter = self.make()
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.jog("X", 1)
        self.machine.task_mode = C["MODE_MDI"]
        adapter.jog("X", 1)
        adapter.send_mdi("G0 X1")
        adapter.load_program(self.ngc())
        adapter.program_start()
        adapter.stop_motion()
        adapter.stop_followup()
        adapter.set_wcs("G55")
        waits = [args for name, args in self.machine.log if name == "wait_complete"]
        self.assertGreaterEqual(len(waits), 6)
        self.assertEqual(set(waits), {(WAIT,)})
        self.assertLess(WAIT, 5.0)


class TestEstop(FakeCase):
    def test_software_estop_sends_one_command_and_nothing_else(self):
        adapter = self.on(homed=True)
        polls = self.machine.stat_polls
        adapter.software_estop()
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])
        self.assertEqual(self.machine.stat_polls, polls, "software_estop polled")
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertEqual(snap.message, "Yazılım acil stop gönderildi. Sürücü gücü DEĞİŞMEDİ.")
        self.assertEqual(snap.alarms[0].severity, Severity.ERROR)

    def test_software_estop_touches_only_the_message_and_the_alarms(self):
        """The threading contract (class docstring): software_estop may run
        while an action runs in another thread, so it may write nothing else
        of the adapter's state - except _estop_sent, a single bool store the
        snapshot only reads and clears (LinuxCncAdapter._note_task_state),
        and (plan WP03) the stop sequence an action in progress checks
        before its motion command (_note_stop: _stop_seq, _stop_why).
        Since plan WP04 its message is an ERROR: the message band's four
        fields - the message, its severity, the error box and the box's id
        counter - all change, written together under _say_lock."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X1")
        adapter.set_jog_step(0.5)

        def state():
            return {k: (list(v) if isinstance(v, list) else v)
                    for k, v in vars(adapter).items()}

        before = state()
        adapter.software_estop()
        after = state()
        changed = {k for k in before if before[k] != after[k]}
        self.assertEqual(changed, {"_message", "_message_severity", "_last_error", "_error_seq",
                                   "_alarms", "_estop_sent", "_stop_seq", "_stop_why"})
        self.assertEqual(adapter._last_error.text, A.ESTOP_SENT)

    def test_on_the_rig_a_software_estop_is_undone_by_on_alone(self):
        """test-rig.hal leaves user-enable-out out of the estop chain, so
        state(ESTOP) disables motion but leaves aux.estop clear: task is in
        ESTOP_RESET (emctask.cc:419-430), and the reset sends only ON."""
        adapter = self.on(homed=True)
        adapter.software_estop()
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])
        self.assertTrue(adapter.snapshot().estop.software_disabled)
        del self.machine.log[:]
        adapter.software_estop_reset()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls_except("wait_complete"), [("state", (C["STATE_ON"],))])
        self.assertEqual(adapter.snapshot().message, "Yazılım acil stop sıfırlandı.")

    def test_with_user_enable_out_in_the_chain_the_reset_needs_estop_reset(self):
        """The other wiring (user-enable-out -> estop-latch ok-in): the
        software estop trips the latch, LinuxCNC is in ESTOP, and the reset
        sends ESTOP_RESET then ON."""
        machine = F.FakeMachine()
        machine.user_enable_out_in_chain = True
        adapter = self.on(homed=True, machine=machine)
        adapter.software_estop()
        self.assertEqual(machine.task_state, C["STATE_ESTOP"])
        del machine.log[:]
        adapter.software_estop_reset()
        self.assertEqual(machine.calls_except("wait_complete"), [
            ("state", (C["STATE_ESTOP_RESET"],)), ("state", (C["STATE_ON"],)),
        ])
        self.assertEqual(machine.task_state, C["STATE_ON"])

    def test_estop_is_not_delayed_by_an_action_blocked_in_wait_complete(self):
        """app.Session sends software_estop without its lock, from another
        thread. With an action held inside wait_complete (wait_gate), the
        estop must reach LinuxCNC before that action is released, and the
        action must then stop instead of jogging or homing into an estopped
        machine.

        What this cannot show: the real wait_complete keeps the GIL
        (emcmodule.cc:212-231), so on the machine the estop thread runs only
        after the wait - which is why every wait is WAIT_TIMEOUT_S long
        (TestEveryWaitIsBounded). This test proves the adapter adds no lock
        or shared channel on top."""
        for name, homed, call, refused in (
            ("jog", False, lambda a: a.jog("X", 1), "Makine devre dışı — jog yok."),
            # Confirmed: a homed machine is asked about first (plan WP07).
            ("home", True, lambda a: a.home_all(confirmed=True),
             "Makine devre dışı — referans alınamaz."),
        ):
            with self.subTest(action=name):
                adapter = self.on(homed=homed)
                if name == "jog":
                    self.machine.task_mode = C["MODE_MDI"]   # jog will switch mode and wait
                    self.machine.motion_mode = C["TRAJ_MODE_COORD"]
                gate = threading.Event()
                self.machine.wait_gate = gate
                worker = threading.Thread(target=call, args=(adapter,), daemon=True)
                worker.start()
                self.addCleanup(worker.join, F.GATE_LIMIT_S)
                self.addCleanup(gate.set)
                self.assertTrue(self.machine.wait_entered.wait(F.GATE_LIMIT_S))

                adapter.software_estop()
                self.assertTrue(worker.is_alive(), "the action was not actually blocked")
                self.assertEqual(self.machine.log[-1], ("state", (C["STATE_ESTOP"],)))
                # On a channel of its own, opened before any action ran
                # (with DURDUR's, plan WP03: three in all).
                estop_channel = self.machine.log_channel[-1]
                self.assertNotIn(estop_channel, self.machine.log_channel[:-1])
                self.assertEqual(len(self.machine.channels), 3)
                # The rig's latch is not fed by user-enable-out, so LinuxCNC
                # lands in ESTOP_RESET: motion disabled, refused all the same.
                self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])

                gate.set()
                worker.join(F.GATE_LIMIT_S)
                self.assertFalse(worker.is_alive())
                self.assertNotIn("jog", self.machine.calls())
                self.assertNotIn("home", self.machine.calls())
                self.assertEqual(adapter.snapshot().message, refused)

    def test_reset_sends_estop_reset_then_on(self):
        adapter = self.make()
        adapter.software_estop_reset()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("state", (C["STATE_ESTOP_RESET"],)),
            ("state", (C["STATE_ON"],)), ("wait_complete", (WAIT,)),
        ])
        snap = adapter.snapshot()
        self.assertFalse(snap.estop.software_disabled)
        self.assertEqual(snap.message, "Yazılım acil stop sıfırlandı. Referans gerekli.")
        self.assertEqual(self.clock.sleeps, [], "no wait when the latch clears at once")

    def test_reset_waits_for_a_latch_that_clears_after_a_few_polls(self):
        """iocontrol answers on its own cycle (ioControl.cc:518-526): the first
        polls after ESTOP_RESET still show ESTOP even with the chain closed."""
        adapter = self.make(machine=F.FakeMachine(estop_clear_after_polls=4))
        adapter.software_estop_reset()
        self.assertEqual(self.machine.calls(), ["state", "state", "wait_complete"])
        self.assertEqual(self.machine.log[1], ("state", (C["STATE_ON"],)))
        self.assertEqual(len(self.clock.sleeps), 3)
        self.assertLess(self.clock.now, 0.3)
        self.assertFalse(adapter.snapshot().estop.software_disabled)

    def test_reset_refused_after_the_deadline_without_claiming_a_cause(self):
        """With emc-enable-in false aux.estop stays set (ioControl.cc:523-526),
        so task stays in ESTOP (emctask.cc:421-423) and ON is not sent. From
        here an open chain and a slow latch look the same, so the message
        names neither as fact and says to retry."""
        adapter = self.make()
        self.machine.fault_in = True
        adapter.software_estop_reset()
        self.assertEqual(self.machine.calls(), ["state"])
        self.assertGreaterEqual(self.clock.now, 0.3 - 1e-9)      # 3 x [EMCIO]CYCLE_TIME 0.100
        self.assertLess(self.clock.now, 0.3 + A._RESET_POLL_INTERVAL_S)
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertEqual(snap.message, (
            "Yazılım sıfırlaması reddedildi — LinuxCNC 0.3 s içinde acil stoptan "
            "çıkmadı. Acil stop zinciri kapalıysa tekrar deneyin."
        ))
        self.assertEqual(snap.alarms[0].severity, Severity.WARN)
        self.assertNotIn("emc-enable-in", snap.message + snap.alarms[0].text)

    def test_latch_slower_than_the_deadline_is_refused_then_works_on_retry(self):
        adapter = self.make(machine=F.FakeMachine(estop_clear_after_polls=100))
        adapter.software_estop_reset()
        self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)
        self.assertIn("tekrar deneyin", adapter.snapshot().message)
        self.machine.estop_clear_after_polls = 0
        adapter.software_estop_reset()
        self.assertFalse(adapter.snapshot().estop.software_disabled)

    def test_on_not_reached_says_so_and_logs_it(self):
        """ESTOP_RESET took, but LinuxCNC fell back to ESTOP once ON was sent
        (emctaskmain.cc:3413-3416)."""
        adapter = self.make()
        self.machine.estop_reasserts_on_power_on = True
        adapter.software_estop_reset()
        self.assertIn(("state", (C["STATE_ON"],)), self.machine.log)
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertEqual(snap.message, "Makine açılamadı — LinuxCNC açık duruma geçmedi.")
        self.assertNotIn("sıfırlandı", snap.message)
        self.assertEqual(snap.alarms[0].severity, Severity.WARN)

    def test_reset_on_a_homed_machine_does_not_ask_for_homing(self):
        """A software estop unhomes only VOLATILE_HOME joints (emctask.cc:364,
        homing.c:316-323); test-rig.ini sets none."""
        adapter = self.on(homed=True)
        adapter.software_estop()
        adapter.software_estop_reset()
        self.assertEqual(adapter.snapshot().message, "Yazılım acil stop sıfırlandı.")

    def test_already_on_sends_nothing(self):
        adapter = self.on(homed=True)
        adapter.software_estop_reset()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Makine zaten açık.")

    def test_out_of_estop_but_off_only_switches_on(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        adapter.software_estop_reset()
        self.assertEqual(self.machine.calls_except("wait_complete"), [("state", (C["STATE_ON"],))])
        self.assertFalse(adapter.snapshot().estop.software_disabled)

    def test_on_with_a_program_running_is_simply_already_on(self):
        """A program runs only in ON, and from ON nothing is sent: the ON
        check comes first, so the message does not claim a reset would have
        stopped the program."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        adapter.software_estop_reset()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Makine zaten açık.")

    def test_refused_while_a_program_or_mdi_runs(self):
        """ESTOP_RESET aborts whatever runs, in every mode (emctask.cc:340-349).
        Defence in depth behind the ON check: the fake is put in a state
        LinuxCNC would not show (busy interpreter, not ON) to reach it."""
        for name, prepare in (
            ("auto", lambda m: (setattr(m, "task_mode", C["MODE_AUTO"]),
                                setattr(m, "interp_state", C["INTERP_READING"]))),
            ("paused", lambda m: (setattr(m, "task_mode", C["MODE_AUTO"]),
                                  setattr(m, "interp_state", C["INTERP_PAUSED"]),
                                  setattr(m, "paused", True))),
            ("mdi", lambda m: (setattr(m, "task_mode", C["MODE_MDI"]),
                               setattr(m, "interp_state", C["INTERP_READING"]))),
        ):
            with self.subTest(running=name):
                adapter = self.on(homed=True)
                prepare(self.machine)
                self.machine.traj_enabled = False      # ESTOP_RESET
                adapter.software_estop_reset()
                self.assertEqual(self.machine.log, [])
                self.assertNotEqual(self.machine.interp_state, C["INTERP_IDLE"])
                self.assertIn("Program çalışırken", adapter.snapshot().message)

    def test_no_adapter_call_can_restore_hardware_drive_power(self):
        """Software estop is not a safety function (docs/MERMER-GECIS.md:139).
        Whatever the UI calls, the chain input stays as the hardware left it
        and the machine stays in estop."""
        adapter = self.make()
        self.machine.fault_in = True
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.jog("X", 1)
        adapter.send_mdi("G0 X1")
        adapter.program_start()
        adapter.set_feed_override(100)
        adapter.software_estop_reset()
        self.assertTrue(self.machine.fault_in)
        self.assertFalse(self.machine.emc_enable_in)
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP"])
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.blocks_motion)
        self.assertFalse(snap.estop.drive_power_clearable_by_software)
        self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)


# ---------------------------------------------------------------------------
# MDI
# ---------------------------------------------------------------------------


class TestMdi(FakeCase):
    def test_homed_command_switches_to_mdi_and_is_sent(self):
        adapter = self.on(homed=True)
        entry = adapter.send_mdi(" G0 X1 ")
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("mode", (C["MODE_MDI"],)), ("wait_complete", (WAIT,)), ("mdi", ("G0 X1",)),
        ])
        self.assertEqual(self.machine.mdi_executed, ["G0 X1"])
        self.assertTrue(entry.accepted)
        # Plan WP04: sent, its result awaited; the fake runs no interpreter,
        # so the next snapshot finds it idle with no error - done.
        self.assertEqual((entry.status, entry.note), (A.MDI_SENT, A.MDI_SENT_NOTE))
        self.assertEqual(adapter._message, "MDI: G0 X1 — Gönderildi, sonuç bekleniyor.")
        snap = adapter.snapshot()
        self.assertEqual(snap.mdi_history[-1], entry._replace(status="done", note=A.MDI_DONE_NOTE))
        self.assertEqual(snap.message, "MDI: G0 X1 — Tamamlandı.")

    def test_already_in_mdi_sends_no_mode_change(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_MDI"]
        adapter.send_mdi("G21")
        self.assertEqual(self.machine.calls(), ["mdi"])

    def test_a_running_mdi_command_does_not_block_the_next_line(self):
        """LinuxCNC queues MDI lines; only a running PROGRAM refuses MDI."""
        adapter = self.on(homed=True)
        self.run_mdi_program()
        self.assertTrue(adapter.send_mdi("G21").accepted)
        self.assertEqual(self.machine.calls(), ["mdi"])

    def test_unhomed_refusals_match_the_mock(self):
        """Both adapters use mdi_refusal, so the notes are the same."""
        adapter = self.on(homed=False)
        mock = MockAdapter(self.profile)
        mock.software_estop_reset()
        for cmd in ("G0 X10", "G1 Y1 F100", "G53 G0 Z0", "M5", "G21"):
            with self.subTest(cmd=cmd):
                entry = adapter.send_mdi(cmd)
                self.assertFalse(entry.accepted)
                self.assertEqual(entry.note, mock.send_mdi(cmd).note)
        self.assertEqual(self.machine.log, [])

    def test_any_mdi_while_unhomed_refused_without_no_force_homing(self):
        """LinuxCNC refuses every MDI command before homing unless
        [TRAJ]NO_FORCE_HOMING is set (emctaskmain.cc:2208-2212); the rig does
        not set it, so refusing here is only faster, not stricter."""
        adapter = self.on(homed=False)
        entry = adapter.send_mdi("M5")
        self.assertFalse(entry.accepted)
        self.assertIn("NO_FORCE_HOMING", entry.note)
        self.assertEqual(self.machine.log, [])

    def test_with_no_force_homing_non_motion_mdi_is_sent_unhomed(self):
        machine = F.FakeMachine(no_force_homing=True)
        # Inside the existing [TRAJ]: LinuxCNC reads only the first one.
        profile = rig_profile(self, insert={("TRAJ", "NO_FORCE_HOMING"): "1"})
        adapter = self.make(machine=machine, profile=profile)
        machine.power_on(homed=False)
        self.assertTrue(adapter.send_mdi("M5").accepted)
        self.assertNoLinuxCncErrors()
        self.assertEqual(machine.mdi_executed, ["M5"])
        refused = adapter.send_mdi("X10")    # modal motion, no G word
        self.assertFalse(refused.accepted)
        self.assertEqual(refused.note, "Referans alınmadı — koordinatlı hareket kabul edilmiyor.")
        self.assertEqual(machine.mdi_executed, ["M5"])

    def test_refused_while_estopped_like_the_mock(self):
        adapter = self.make()
        mock = MockAdapter(self.profile)
        self.assertEqual(adapter.send_mdi("G21").note, mock.send_mdi("G21").note)
        self.assertEqual(self.machine.log, [])

    def test_empty_command(self):
        adapter = self.on(homed=True)
        self.assertEqual(adapter.send_mdi("   ").note, "Boş komut.")
        self.assertEqual(self.machine.log, [])

    def test_refused_while_a_program_runs(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        entry = adapter.send_mdi("G21")
        self.assertFalse(entry.accepted)
        self.assertEqual(entry.note, "Program çalışıyor — MDI kabul edilmedi.")
        self.assertEqual(self.machine.log, [])

    def test_overlong_command_is_a_refusal_not_an_exception(self):
        """The binding raises ValueError past 254 characters (emcmodule.cc:
        985-988). Session.act does not catch it for MDI, so the adapter must."""
        adapter = self.on(homed=True)
        entry = adapter.send_mdi("G0 X1 " + "(x)" * 100)
        self.assertFalse(entry.accepted)
        self.assertEqual(self.machine.mdi_executed, [])


# ---------------------------------------------------------------------------
# Programs
# ---------------------------------------------------------------------------


class _SilentOpenFailure(F.FakeMachine):
    """PLAN_OPEN fails with no error text - e.g. another NML client already
    dequeued it. Only the RCS status tells."""

    def issue_open(self, path: str) -> None:
        raise F.Rejected("")


class TestProgram(FakeCase):
    def test_load_program_sequence_matches_axis(self):
        """open_file_guts, axis.py:1174-1208."""
        adapter = self.on(homed=True)
        path = self.ngc()
        adapter.load_program(path)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("task_plan_synch", ()), ("wait_complete", (WAIT,)),
            ("program_open", (path,)), ("wait_complete", (WAIT,)),
        ])
        snap = adapter.snapshot()
        self.assertEqual(snap.program_file, path)
        # The base name on the message line (plan WP08); the whole path is
        # on the Program screen.
        self.assertEqual(snap.message, "{} yüklendi.".format(os.path.basename(path)))

    def test_load_program_leaves_auto_first(self):
        """PLAN_SYNCH is refused in AUTO (not in emctaskmain.cc:1024-1080)."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        adapter.load_program(self.ngc())
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.calls()[:2], ["mode", "wait_complete"])

    def test_loading_works_in_estop(self):
        """Task takes SET_MODE, PLAN_SYNCH and PLAN_OPEN when not ON
        (emctaskmain.cc:807-844), and so does axis.py."""
        adapter = self.make()
        path = self.ngc()
        adapter.load_program(path)
        self.assertNoLinuxCncErrors()
        self.assertEqual(adapter.snapshot().program_file, path)

    def test_overlong_path_is_a_refusal_not_an_exception(self):
        """emcmodule.cc:1211-1214 raises ValueError past 254 characters."""
        adapter = self.on(homed=True)
        adapter.load_program("/" + "a" * 300 + ".ngc")
        snap = adapter.snapshot()
        self.assertIsNone(snap.program_file)
        self.assertEqual(snap.message, "Dosya yolu çok uzun — program yüklenmedi.")

    def test_load_missing_file_is_one_consistent_refusal(self):
        """LinuxCNC's "can't open" goes to the alarm log; the message is the
        Turkish refusal, and a later snapshot does not overwrite it."""
        adapter = self.on(homed=True)
        adapter.load_program("/nonexistent/x.ngc")
        snap = adapter.snapshot()
        self.assertIsNone(snap.program_file)
        self.assertEqual(snap.message, "Program açılamadı: /nonexistent/x.ngc")
        self.assertIn("can't open /nonexistent/x.ngc", [a.text for a in snap.alarms])

    def test_reloading_a_file_that_became_unreadable_is_refused(self):
        """Task never clears stat.file on PLAN_CLOSE or a failed PLAN_OPEN, so
        stat.file still names the old file; the RCS status is what tells."""
        path = self.ngc()
        readable = {"ok": True}
        machine = F.FakeMachine(openable=lambda p: readable["ok"] and os.path.isfile(p))
        adapter = self.on(homed=True, machine=machine)
        adapter.load_program(path)
        readable["ok"] = False
        adapter.load_program(path)
        self.assertEqual(self.machine.file, path)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Program açılamadı: {}".format(path))

    def test_done_without_stat_file_naming_the_file_is_a_failure(self):
        """wait_complete can report DONE for a command task has not applied:
        it answers DONE as soon as any later serial is echoed, and serials
        are shared by every channel (emcmodule.cc:218-220; see
        adapter.WAIT_TIMEOUT_S). stat.file is the check that catches it."""
        class OpenNotApplied(F.FakeMachine):
            def issue_open(self, path):
                pass

        adapter = self.on(homed=True, machine=OpenNotApplied())
        path = self.ngc()
        adapter.load_program(path)
        snap = adapter.snapshot()
        self.assertIsNone(snap.program_file)
        self.assertEqual(snap.message, "Program açılamadı: {}".format(path))

    def test_timed_out_wait_with_stat_file_naming_the_file_is_a_success(self):
        """A teleop jog still moving: task reports nothing DONE meanwhile
        (emctaskmain.cc:3546-3561), so both waits time out (-1), yet the
        open took. stat.inpos stays true during a teleop jog (control.c's
        TELEOP branch never clears it), so the up-front refusal does not
        catch this case; the result rule must."""
        adapter = self.on(homed=True)
        self.machine.jogging_active = True
        path = self.ngc()
        adapter.load_program(path)
        waits = [a for n, a in self.machine.log if n == "wait_complete"]
        self.assertEqual(len(waits), 2)
        self.assertEqual(self.machine.wait_status(), -1)
        snap = adapter.snapshot()
        self.assertEqual(snap.program_file, path)
        self.assertEqual(snap.message, "{} yüklendi.".format(os.path.basename(path)))

    def test_timed_out_wait_without_the_file_is_a_failure(self):
        class OpenNotApplied(F.FakeMachine):
            def issue_open(self, path):
                pass

        adapter = self.on(homed=True, machine=OpenNotApplied())
        self.machine.jogging_active = True
        path = self.ngc()
        adapter.load_program(path)
        self.assertEqual(adapter.snapshot().message, "Program açılamadı: {}".format(path))

    def test_refused_up_front_while_homing_or_a_joint_jog_moves(self):
        """Loading would not hurt motion, but its waits could only time out
        and the result would be guesswork. Refused, nothing sent."""
        path = self.ngc()
        # Plan WP08: one rule for both adapters and the screen
        # (programs.load_block), a text for each.
        for name, prepare, text in (
            ("homing", lambda m: m.homing.__setitem__(1, 1), programs.LOAD_HOMING),
            ("joint jog", lambda m: setattr(m, "jogging_active", True), programs.LOAD_MOVING),
        ):
            with self.subTest(moving=name):
                adapter = self.on(homed=False)
                prepare(self.machine)
                adapter.load_program(path)
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, text)

    def test_a_stale_inpos_out_of_on_does_not_block_loading(self):
        """Disabled motion does not update its INPOS flag (control.c
        DISABLED branch), so out of ON the flag is not consulted."""
        adapter = self.make()
        self.machine.jogging_active = True       # e.g. estopped mid-jog
        path = self.ngc()
        adapter.load_program(path)
        self.assertEqual(adapter.snapshot().program_file, path)

    def test_failure_without_error_text_is_still_a_failure(self):
        adapter = self.on(homed=True, machine=_SilentOpenFailure())
        path = self.ngc()
        adapter.load_program(path)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Program açılamadı: {}".format(path))
        self.assertEqual([a for a in snap.alarms if a.source == "linuxcnc"], [])

    def test_start_runs_in_auto(self):
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        del self.machine.log[:]
        adapter.program_start()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("mode", (C["MODE_AUTO"],)), ("wait_complete", (WAIT,)), ("auto", (C["AUTO_RUN"], 0)),
        ])
        self.assertTrue(adapter.snapshot().program_running)

    def test_start_without_a_program_is_refused(self):
        adapter = self.on(homed=True)
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Program yüklenmedi.")

    def test_start_is_gated_on_homing_like_the_mock(self):
        adapter = self.on(homed=False)
        self.machine.file = self.ngc()
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        self.assertTrue(adapter.snapshot().message.startswith("Başlatılamaz: Referans alınmadı"))

    def test_start_is_gated_on_estop(self):
        adapter = self.make()
        self.machine.file = self.ngc()
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(
            adapter.snapshot().message,
            "Başlatılamaz: Makine yazılımda devre dışı (acil stop).",
        )

    def test_start_is_gated_on_an_unknown_link(self):
        adapter = self.on(homed=True, hal_pins={})
        self.machine.file = self.ngc()
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Başlatılamaz: Karta bağlantı yok.")

    def test_start_while_running_is_refused(self):
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        adapter.program_start()
        del self.machine.log[:]
        adapter.program_start()
        self.assertEqual(self.machine.log, [])
        # Plan WP09: programs.start_busy (was "Program zaten çalışıyor.").
        self.assertEqual(adapter.snapshot().message, programs.START_RUNNING)

    def test_load_while_running_is_refused(self):
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        adapter.program_start()
        del self.machine.log[:]
        adapter.load_program(self.ngc())
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Program çalışırken yeni program yüklenemez.")

    def test_stop_aborts_and_waits(self):
        """task_stop, axis.py:2411-2417: abort() on the stop channel at once
        (stop_motion), then abort() on the action channel and a bounded wait
        (stop_followup). Until plan WP03: program_stop, one abort."""
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        adapter.program_start()
        del self.machine.log[:]
        del self.machine.log_channel[:]
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(self.machine.log, [("abort", ()), ("abort", ()), ("wait_complete", (WAIT,))])
        self.assertEqual(self.machine.log_channel, [2, 0, 0])
        snap = adapter.snapshot()
        self.assertFalse(snap.program_running)
        # Plan WP09: the snapshot that finds the run over after DURDUR names
        # the program (was "Durduruldu.", stop_followup's word, which it
        # replaces).
        self.assertEqual(snap.message,
                         programs.STOPPED_TEXT.format(os.path.basename(self.machine.file)))

    def test_stop_that_has_not_taken_yet_says_so(self):
        """abort() was sent but the interpreter is still busy after the
        bounded wait: the operator is told it was sent, not that it stopped."""
        class SlowAbort(F.FakeMachine):
            def issue_abort(self) -> None:
                pass

        adapter = self.on(homed=True, machine=SlowAbort())
        adapter.load_program(self.ngc())
        adapter.program_start()
        adapter.stop_motion()
        adapter.stop_followup()
        self.assertEqual(adapter.snapshot().message, "Durdurma gönderildi — LinuxCNC henüz boşta değil.")

    def test_paused_program_is_not_running(self):
        adapter = self.on(homed=True)
        self.machine.file = self.ngc()
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_PAUSED"]
        self.machine.paused = True
        snap = adapter.snapshot()
        self.assertFalse(snap.program_running)
        self.assertNotEqual(snap.status_label, "ÇALIŞIYOR")


class TestModeSwitchRefused(FakeCase):
    """Task ignores a mode change while a jog is still moving (emctask.cc:
    269-272). At the rig's 1 mm/s a 10 mm jog takes 10 s, so pressing MDI or
    start right after a jog is ordinary. The bounded wait times out, the
    adapter reads the mode back and sends nothing in the wrong mode."""

    def test_nothing_is_sent_in_the_wrong_mode(self):
        path = self.ngc()
        for name, call, refused_text in (
            ("mdi", lambda a: a.send_mdi("G0 X1"), "MDI moduna geçilemedi — MDI kabul edilmedi."),
            ("start", lambda a: a.program_start(), "AUTO moduna geçilemedi — program başlatılmadı."),
        ):
            with self.subTest(action=name):
                adapter = self.on(homed=True)
                self.machine.file = path
                self.machine.task_mode = C["MODE_MANUAL"]
                self.machine.jogging_active = True
                call(adapter)
                self.assertEqual(self.machine.calls(), ["mode", "wait_complete"])
                snap = adapter.snapshot()
                self.assertIn(refused_text, snap.message)
                # LinuxCNC's own reason is kept, in the alarm log.
                self.assertEqual(snap.alarms[0].text, "Ignoring task mode change while jogging")

    def test_jog_and_load_from_another_mode_are_refused_too(self):
        path = self.ngc()
        for name, call, what in (
            ("jog", lambda a: a.jog("X", 1), "jog yok"),
            ("load", lambda a: a.load_program(path), "program yüklenmedi"),
            # Confirmed: a homed machine is asked about first (plan WP07).
            ("home", lambda a: a.home_all(confirmed=True), "referans alınamaz"),
        ):
            with self.subTest(action=name):
                adapter = self.on(homed=True)
                self.machine.task_mode = C["MODE_MDI"]
                self.machine.motion_mode = C["TRAJ_MODE_COORD"]
                self.machine.jogging_active = True
                call(adapter)
                self.assertEqual(self.machine.calls(), ["mode", "wait_complete"])
                self.assertEqual(
                    adapter.snapshot().message, "MANUAL moduna geçilemedi — {}.".format(what)
                )


# ---------------------------------------------------------------------------
# Feed override and coordinate system
# ---------------------------------------------------------------------------


class TestFeedOverride(FakeCase):
    def feeds(self) -> List[float]:
        return [args[0] for name, args in self.machine.log if name == "feedrate"]

    def test_percent_becomes_a_scale(self):
        """axis.py:2104-2111 sends value / 100 through command.feedrate."""
        adapter = self.make()
        adapter.set_feed_override(50)
        self.assertEqual(self.feeds(), [0.5])
        self.assertEqual(adapter.snapshot().feed_override_pct, 50)

    def test_zero_percent_is_valid(self):
        adapter = self.make()
        adapter.set_feed_override(0)
        self.assertEqual(self.feeds(), [0.0])
        self.assertEqual(adapter.snapshot().message, "Besleme %0.")

    def test_exactly_at_the_rig_ceiling_is_not_reported_as_clamped(self):
        adapter = self.make()
        adapter.set_feed_override(100)
        self.assertEqual(self.feeds(), [1.0])
        self.assertEqual(adapter.snapshot().message, "Besleme %100.")

    def test_above_the_rig_ceiling_is_clamped(self):
        """Motion only clamps negatives (command.c:1214-1221)."""
        adapter = self.make()
        adapter.set_feed_override(150)
        self.assertEqual(self.feeds(), [1.0])
        self.assertEqual(adapter.snapshot().message, "Besleme %150 istendi; INI sınırı %100 uygulandı.")

    def test_missing_key_in_an_existing_ini_means_100_percent(self):
        """axis.py:3353: inifile.find(...) or 1.0."""
        adapter = self.make(profile=rig_profile(self, {("DISPLAY", "MAX_FEED_OVERRIDE"): None}))
        adapter.set_feed_override(150)
        self.assertEqual(self.feeds(), [1.0])
        self.assertIn("INI sınırı %100", adapter.snapshot().message)

    def test_without_an_ini_feed_changes_are_refused(self):
        adapter = self.make("marble")
        adapter.set_feed_override(150)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(
            adapter.snapshot().message,
            "Besleme değiştirilmedi — INI yok, besleme sınırı bilinmiyor.",
        )

    def test_negative_is_refused(self):
        adapter = self.make()
        adapter.set_feed_override(-10)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Geçersiz besleme yüzdesi: %-10.")


class TestWcs(FakeCase):
    def test_homed_selection_goes_through_mdi(self):
        adapter = self.on(homed=True)
        adapter.set_wcs("G55")
        self.assertEqual(self.machine.mdi_executed, ["G55"])

    def test_unknown_code_is_refused(self):
        """G54-G59 only. G59.1-G59.3 are legal in LinuxCNC (interp_convert.cc:
        1762-1789) but refused by choice (adapter._WCS_CODES)."""
        for code in ("G92", "G59.1", "G59.3", "g55"):
            with self.subTest(code=code):
                adapter = self.on(homed=True)
                adapter.set_wcs(code)
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, "Bilinmeyen koordinat sistemi: " + code)

    def test_every_offered_code_is_sent(self):
        for code in ("G54", "G55", "G56", "G57", "G58", "G59"):
            with self.subTest(code=code):
                adapter = self.on(homed=True)
                self.machine.task_mode = C["MODE_MDI"]
                adapter.set_wcs(code)
                self.assertEqual(self.machine.mdi_executed, [code])

    def test_send_mdi_gates_apply(self):
        for name, homed, power, note in (
            ("estop", False, False, "Makine devre dışı — MDI kabul edilmedi."),
            ("unhomed", False, True,
             "Referans alınmadı — LinuxCNC referanssız MDI kabul etmiyor ([TRAJ]NO_FORCE_HOMING yok)."),
        ):
            with self.subTest(gate=name):
                adapter = self.make()
                if power:
                    self.machine.power_on(homed=homed)
                adapter.set_wcs("G55")
                self.assertEqual(self.machine.log, [])
                entry = adapter.snapshot().mdi_history[-1]
                self.assertEqual((entry.command, entry.accepted, entry.note), ("G55", False, note))


# ---------------------------------------------------------------------------
# End to end through Session
# ---------------------------------------------------------------------------


class TestSessionEndToEnd(FakeCase):
    """Session.act forwards jog_step / feed / wcs only if the adapter has the
    method (hasattr, app.py). Before this change LinuxCncAdapter had none of
    them, so those buttons silently did nothing on the real machine."""

    def session(self, **kw) -> Session:
        adapter = self.on(**kw)
        return Session(adapter, self.profile, load_machine_config(self.profile))

    def test_jog_step_and_feed_reach_the_machine(self):
        session = self.session(homed=False)
        session.act("jog_step", "0.1")
        session.act("feed", "50")
        session.act("jog", "X:1")
        self.assertNoLinuxCncErrors()
        self.assertIn(("feedrate", (0.5,)), self.machine.log)
        self.assertEqual(self.jogs()[0][4], 0.1)
        self.assertAlmostEqual(self.machine.joint_pos[0], 0.1)

    def test_wcs_reaches_the_machine(self):
        session = self.session(homed=True)
        session.act("wcs", "G55")
        self.assertEqual(self.machine.mdi_executed, ["G55"])

    def test_estop_through_the_session(self):
        session = self.session(homed=True)
        session.act("software_estop", "")
        self.assertEqual(self.machine.log, [("state", (C["STATE_ESTOP"],))])
        # ESTOP_RESET on the rig's wiring (see TestEstop): motion disabled.
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])
        self.assertTrue(session.adapter.snapshot().estop.software_disabled)

    def test_bad_input_never_raises_out_of_act(self):
        session = self.session(homed=True)
        for action, value in (
            ("jog", "Q:1"), ("jog", "X:0"), ("jog", "X:abc"), ("jog_step", "-1"),
            ("feed", "x"), ("mdi", "G1 " + "X1 " * 100), ("wcs", "G99"),
        ):
            with self.subTest(action=action, value=value):
                session.act(action, value)
        self.assertEqual([n for n, _ in self.machine.log if n in ("jog", "feedrate", "mdi")], [])

    def test_rendered_screens_show_what_the_real_adapter_reports(self):
        session = self.session(homed=True)
        self.machine.axis_pos[0] = 42.125
        self.machine.errors.append((C["OPERATOR_ERROR"], "fake-linuxcnc-refusal-7"))
        bodies = {screen: R.render_body(session.view(screen)) for screen in helpers.ALL_SCREENS}
        self.assertIn("LinuxCNC: fake-linuxcnc-refusal-7", bodies["kontrol"])
        self.assertIn("fake-linuxcnc-refusal-7", bodies["alarmlar"])
        self.assertIn("42.125", bodies["kontrol"])
        for screen, body in bodies.items():
            with self.subTest(screen=screen):
                self.assertIn('data-screen="{}"'.format(screen), body)



# ---------------------------------------------------------------------------
# Found in sim/ on 2026-10-19, against a real LinuxCNC 2.9.9
# ---------------------------------------------------------------------------


class TestStartupProbe(FakeCase):
    """LinuxCncAdapter._probe_status. The binding's poll() spoils its whole
    process when the first poll finds no tool mmap (emcmodule.cc:286-305,
    A.POLL_SPOILED), so the adapter polls twice before anything else."""

    def test_linuxcnc_not_running_is_refused_in_turkish(self):
        """The UI started before LinuxCNC used to come up, show "bağlantı
        yok" once, then fail every request with SystemError."""
        machine = F.FakeMachine()
        machine.channels_valid = False
        machine.tool_mmap_present = False
        with self.assertRaises(A.LinuxCncUnavailable) as ctx:
            self.make(machine=machine)
        self.assertIn("LinuxCNC çalışmıyor", str(ctx.exception))
        self.assertIn("önce LinuxCNC'yi, sonra arayüzü", str(ctx.exception))

    def test_a_process_without_the_mmap_is_refused_with_the_home_hint(self):
        """LinuxCNC runs, but this process looks for $HOME/.tool.mmap under
        another HOME: the first poll passes, the second raises SystemError."""
        machine = F.FakeMachine()
        machine.tool_mmap_present = False
        with self.assertRaises(A.LinuxCncUnavailable) as ctx:
            self.make(machine=machine)
        self.assertIn(A._tool_mmap_path(), str(ctx.exception))
        self.assertIn("aynı HOME", str(ctx.exception))
        self.assertIsInstance(ctx.exception.__cause__, SystemError)
        self.assertEqual(machine.stat_polls, 2)

    def test_it_is_still_a_runtime_error(self):
        """Callers that caught RuntimeError (the missing-module case) still do."""
        self.assertTrue(issubclass(A.LinuxCncUnavailable, RuntimeError))

    def test_mmap_path_follows_home_like_the_binding(self):
        """tool_mmap_fname: $HOME, or /tmp without HOME (tooldata_mmap.cc:74-80)."""
        saved = os.environ.get("HOME")
        self.addCleanup(
            (lambda: os.environ.__setitem__("HOME", saved)) if saved is not None
            else (lambda: os.environ.pop("HOME", None))
        )
        os.environ["HOME"] = "/home/cnc"
        self.assertEqual(A._tool_mmap_path(), "/home/cnc/.tool.mmap")
        del os.environ["HOME"]
        self.assertEqual(A._tool_mmap_path(), "/tmp/.tool.mmap")

    def test_a_poll_spoiled_later_is_lost_status_not_an_exception(self):
        """Defence in depth: should poll() start raising SystemError after
        startup, the snapshot reports lost status and actions refuse; nothing
        escapes app.Session."""
        adapter = self.on(homed=True)

        def spoiled():
            raise SystemError("returned NULL without setting an exception")

        adapter._stat.poll = spoiled
        snap = adapter.snapshot()
        self.assertFalse(snap.status_known)
        self.assertIn(".tool.mmap", snap.alarms[0].text)
        del self.machine.log[:]
        adapter.jog("X", 1)
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, A.NO_LINUXCNC_REFUSAL)


class TestStopNotFromThisUi(FakeCase):
    """LinuxCncAdapter._note_task_state: LinuxCNC leaving ON without an
    action of this UI is said on the message line and logged. In sim/ a
    watchdog bite left the previous action's text on the message line."""

    def stopped_elsewhere(self, adapter):
        adapter.snapshot()  # the snapshot that sees ON
        self.machine.fault_in = True
        return adapter.snapshot()

    def test_watchdog_bite(self):
        adapter = self.on(homed=True)
        adapter.set_feed_override(150)  # leaves a message, as in sim/
        self.hal.pins[LinkHealth.HAL_WATCHDOG_HAS_BIT] = True
        snap = self.stopped_elsewhere(adapter)
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP"])
        self.assertEqual(snap.message, "{}. {}".format(A.LINK_LOST, A.LINK_ACTION))
        self.assertEqual(
            snap.message,
            "Kartla bağlantı koptu — makine durduruldu. Ethernet kablosunu ve kartın "
            "beslemesini kontrol edin, sonra «Yazılım sıfırla»ya basın.",
        )
        self.assertEqual(snap.alarms[0].text, A.LINK_LOST)
        self.assertEqual(snap.alarms[0].severity, Severity.ERROR)

    def test_said_once_not_on_every_poll(self):
        adapter = self.on(homed=True)
        snap = self.stopped_elsewhere(adapter)
        again = adapter.snapshot()
        self.assertEqual(len(again.alarms), len(snap.alarms))

    def test_estop_chain_opened_without_a_watchdog_bite(self):
        adapter = self.on(homed=True)
        snap = self.stopped_elsewhere(adapter)
        self.assertEqual(
            snap.message,
            "Makine acil stopa geçti — komut bu ekrandan gelmedi. "
            "Sebep giderilince «Yazılım sıfırla»ya basın.",
        )

    def test_machine_switched_off_elsewhere(self):
        """Out of ON but not in ESTOP: another GUI or halui turned it off."""
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.machine.traj_enabled = False
        snap = adapter.snapshot()
        self.assertTrue(snap.message.startswith(
            "Makine kapatıldı — komut bu ekrandan gelmedi"), snap.message)

    def test_own_software_estop_keeps_its_own_message(self):
        adapter = self.on(homed=True)
        adapter.snapshot()
        adapter.software_estop()
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Yazılım acil stop gönderildi. Sürücü gücü DEĞİŞMEDİ.")
        self.assertFalse(any("ekrandan gelmedi" in a.text for a in snap.alarms))

    def test_own_estop_does_not_excuse_a_later_stop(self):
        adapter = self.on(homed=True)
        adapter.snapshot()
        adapter.software_estop()
        adapter.snapshot()
        adapter.software_estop_reset()
        snap = self.stopped_elsewhere(adapter)
        self.assertIn("acil stopa geçti", snap.message)

    def test_linuxcnc_own_error_text_stays_on_the_message_line(self):
        """When the same drain brings LinuxCNC's own error, that is the more
        precise text; the stop is still logged as an alarm."""
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.machine.fault_in = True
        self.machine.errors.append((C["NML_ERROR"], "fake-following-error"))
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "LinuxCNC: fake-following-error")
        self.assertTrue(any("acil stopa geçti" in a.text for a in snap.alarms))

    def test_the_phrase_the_negative_checks_look_for_is_the_real_one(self):
        """The three tests that assert "ekrandan gelmedi" is ABSENT would pass
        vacuously if the wording moved on again (it did once, 2026-10-20)."""
        adapter = self.on(homed=True)
        snap = self.stopped_elsewhere(adapter)
        self.assertTrue(any("ekrandan gelmedi" in a.text for a in snap.alarms))

    def test_nothing_invented_on_the_first_snapshot(self):
        """The UI opened on a machine already in estop saw no transition."""
        adapter = self.make()
        snap = adapter.snapshot()
        self.assertFalse(any("ekrandan gelmedi" in a.text for a in snap.alarms))

    def test_nothing_invented_after_a_status_outage(self):
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.machine.channels_valid = False
        adapter.snapshot()
        self.machine.fault_in = True
        self.machine.channels_valid = True
        snap = adapter.snapshot()
        self.assertFalse(any("ekrandan gelmedi" in a.text for a in snap.alarms))


class TestFeedChipsFollowTheCeiling(FakeCase):
    """The Kontrol screen's feed chips and LinuxCncAdapter's clamp read one
    value, configmodel.max_feed_override_pct. In sim/ the rig (ceiling
    100 %) offered a live 150 % chip that the adapter then clamped."""

    @staticmethod
    def chips(profile) -> Dict[int, Dict]:
        return {c["pct"]: c for c in VM.feed_chips(load_machine_config(profile))}

    def test_rig_shows_150_disabled_with_the_reason(self):
        chips = self.chips(load_profile("test-rig"))
        self.assertTrue(chips[50]["enabled"])
        self.assertTrue(chips[100]["enabled"])
        self.assertFalse(chips[150]["enabled"])
        self.assertEqual(chips[150]["reason"], "INI sınırı %100 ([DISPLAY]MAX_FEED_OVERRIDE)")

    def test_rendered_disabled_and_explained_on_screen(self):
        body = R.render_body(helpers.view("test-rig", "kontrol"))
        tag = re.search(r'<button [^>]*data-action="feed" data-value="150"[^>]*>', body)
        self.assertIsNotNone(tag, "no 150 % chip rendered")
        self.assertIn(" disabled", tag.group(0))
        self.assertIn(
            '<div class="cap-reason">INI sınırı %100 ([DISPLAY]MAX_FEED_OVERRIDE)</div>', body
        )

    def test_no_ini_disables_every_chip(self):
        chips = self.chips(load_profile("marble"))
        self.assertFalse(any(c["enabled"] for c in chips.values()))
        self.assertIn("INI yok", chips[100]["reason"])

    def test_chips_and_clamp_agree(self):
        """An enabled chip is sent as is; a disabled one would be clamped."""
        for value in ("1.0", "1.15", "1.5", "abc", None):
            with self.subTest(MAX_FEED_OVERRIDE=value):
                profile = rig_profile(self, {("DISPLAY", "MAX_FEED_OVERRIDE"): value})
                chips = self.chips(profile)
                adapter = self.make(profile=profile)
                self.machine.power_on(homed=True)
                for pct, chip in sorted(chips.items()):
                    adapter.set_feed_override(pct)
                    sent_as_is = self.machine.log[-1] == ("feedrate", (pct / 100.0,))
                    self.assertEqual(chip["enabled"], sent_as_is, pct)


@NEEDS_REFERENCE
class TestToolMmapFidelity(unittest.TestCase):
    """The fake's process-wide tool mmap (fake_linuxcnc, "the tool mmap")
    and A._tool_mmap_path, against reference/linuxcnc."""

    def test_poll_spoils_its_process_without_the_mmap(self):
        text = _src(TestFakeFidelity.EMCMODULE)
        start = text.index("static PyObject *poll(pyStatChannel *s, PyObject *o) {")
        body = text[start:text.index("if(!check_stat(s->c)) return NULL;", start)]
        self.assertIn("static bool mmap_available = 1;", body)
        self.assertIn("if (!mmap_available) return NULL;", body)
        self.assertIn("mmap_available = 0;", body)
        self.assertIn("static bool initialized=0;", text[start - 200:start])

    def test_mmap_file_is_under_home_or_tmp(self):
        text = _src("emc/tooldata/tooldata_mmap.cc")
        self.assertIn('#define TOOL_MMAP_FILENAME ".tool.mmap"', text)
        self.assertIn('secure_getenv("HOME")', text)
        self.assertIn('hdir = (char*)"/tmp";', text)



# ---------------------------------------------------------------------------
# A lost board link, and the reset that recovers it (2026-10-20)
# ---------------------------------------------------------------------------


class TestLinkRecovery(FakeCase):
    """software_estop_reset after the board link was lost: the watchdog's
    has_bit and io_error stay set until cleared (watchdog.c:50-52, :259),
    has_bit holds the estop latch open, so without clearing them the reset
    could never succeed. Found reading the source; in sim/ the bit had been
    cleared by hand with halcmd."""

    HAS_BIT = LinkHealth.HAL_WATCHDOG_HAS_BIT
    IO_ERROR = LinkHealth.HAL_IO_ERROR

    def bitten(self, **kw) -> LinuxCncAdapter:
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.bite(**kw)
        adapter.snapshot()
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP"])  # premise
        del self.machine.log[:]
        return adapter

    def test_reset_clears_the_bit_unhomes_and_turns_on(self):
        adapter = self.bitten()
        adapter.software_estop_reset()
        self.assertEqual(
            self.halcmd.calls,
            [["-s", "show", "pin", self.HAS_BIT], ["sets", "watchdog", "0"]],
        )
        self.assertFalse(self.hal.pins[self.HAS_BIT])
        sent = self.machine.calls_except("wait_complete")
        self.assertEqual(
            sent,
            [("unhome", (-1,)), ("state", (C["STATE_ESTOP_RESET"],)), ("state", (C["STATE_ON"],))],
        )
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertEqual(list(self.machine.homed[:3]), [0, 0, 0])
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_io_error_is_cleared_first(self):
        adapter = self.bitten(io_error=True)
        adapter.software_estop_reset()
        self.assertEqual(self.halcmd.calls[0], ["setp", self.IO_ERROR, "0"])
        self.assertFalse(self.hal.pins[self.IO_ERROR])
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_a_link_still_down_bites_again_and_the_reset_says_so(self):
        """If the cable is still out, the watchdog bites again at once."""
        adapter = self.bitten()

        def down_again():
            self.hal.pins[self.HAS_BIT] = True
            self.machine.fault_in = True

        self.halcmd.on_change = down_again
        adapter.software_estop_reset()
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP"])
        self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)
        self.assertEqual(adapter.snapshot().message, A.LINK_NOT_RESTORED)

    def pulled(self) -> LinuxCncAdapter:
        """A cable pull on the real driver: io_error alone, nothing trips the
        latch, LinuxCNC says ON (hm2_eth.c:883, watchdog.c:38). The adapter's
        next snapshot stops the machine."""
        adapter = self.on(homed=True)
        self.hal.pins[self.IO_ERROR] = True
        adapter.snapshot()   # stops the machine
        adapter.snapshot()   # reports the stop as the link's (LINK_LOST)
        return adapter

    def test_on_the_real_driver_a_cable_pull_is_io_error_and_the_ui_stops_the_machine(self):
        """Audit 2026-10-21: the sim stub had held has_bit - the opposite of
        the driver - so the machine looked stopped; on the rig it would have
        stayed ON with the board's outputs cut, and the reset would have
        answered "zaten açık"."""
        adapter = self.on(homed=True)
        self.hal.pins[self.IO_ERROR] = True
        del self.machine.log[:]
        snap = adapter.snapshot()
        self.assertEqual(self.machine.calls_except("wait_complete"), [("state", (C["STATE_ESTOP"],))])
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])
        self.assertEqual(snap.alarms[0].text, A.LINK_LOST + " (iletişim hatası; yazılımdan durduruldu)")
        self.assertEqual(snap.message, "{}. {}".format(A.LINK_LOST, A.LINK_ACTION))
        alarms = len(snap.alarms)
        self.halcmd.on_change = lambda: self.hal.pins.__setitem__(self.IO_ERROR, True)
        adapter.software_estop_reset()    # pressed at once, cable still out: its answer must stand
        snap = adapter.snapshot()
        self.assertEqual(len(snap.alarms), alarms + 1)   # the reset's refusal only
        self.assertEqual(self.machine.calls_except("wait_complete"), [("state", (C["STATE_ESTOP"],))])

    def test_a_cable_pull_mid_program_stops_the_program(self):
        """The board has dropped its outputs: nothing of the program is
        running on the machine any more."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        self.hal.pins[self.IO_ERROR] = True
        adapter.snapshot()
        self.assertIn(("state", (C["STATE_ESTOP"],)), self.machine.log)
        self.assertEqual(self.machine.interp_state, C["INTERP_IDLE"])

    def test_the_reset_after_a_real_cable_pull_clears_io_error_then_the_bite(self):
        """io_error cleared, the driver's next read finds the bite the board
        recorded and raises has_bit - it is cleared too - unhome, reset, ON."""
        adapter = self.pulled()
        del self.machine.log[:]
        bite_pending = [True]

        def driver() -> None:
            # the first read after the clear finds the bite the board recorded
            if not self.hal.pins[self.IO_ERROR] and bite_pending[0]:
                self.hal.pins[self.HAS_BIT] = True
                bite_pending[0] = False
            self._couple_latch()
            self.machine.tick()   # the servo thread: the latch sees the bit at once

        self.halcmd.on_change = driver
        adapter.software_estop_reset()
        self.assertEqual(self.halcmd.calls[0], ["setp", self.IO_ERROR, "0"])
        self.assertEqual(self.halcmd.calls[-1], ["sets", "watchdog", "0"])
        self.assertEqual(self.machine.calls_except("wait_complete"),
                         [("unhome", (-1,)), ("state", (C["STATE_ESTOP_RESET"],)),
                          ("state", (C["STATE_ON"],))])
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertEqual(list(self.machine.homed[:3]), [0, 0, 0])
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_a_reset_pressed_before_any_snapshot_saw_the_fault_stops_first(self):
        adapter = self.on(homed=True)
        self.hal.pins[self.IO_ERROR] = True
        del self.machine.log[:]
        adapter.software_estop_reset()
        sent = self.machine.calls_except("wait_complete")
        self.assertEqual(sent[0], ("state", (C["STATE_ESTOP"],)))
        self.assertEqual(sent[-1], ("state", (C["STATE_ON"],)))
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_a_stop_after_a_successful_reset_is_reported_again(self):
        """Review 2026-10-21: the reset left _estop_sent up when no snapshot
        had seen the stop it sent, and the next real stop went unreported."""
        adapter = self.on(homed=True)
        self.hal.pins[self.IO_ERROR] = True
        adapter.software_estop_reset()           # pressed at once, no snapshot between
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        adapter.snapshot()
        self.bite()                              # a real stop, not ours
        snap = adapter.snapshot()
        self.assertEqual(snap.alarms[0].text, A.LINK_LOST)
        self.assertEqual(snap.message, "{}. {}".format(A.LINK_LOST, A.LINK_ACTION))

    def test_a_cable_still_out_brings_io_error_straight_back(self):
        """Five misses after the clear (packet_error_limit 10, increment 2,
        hm2_eth.c:874-883): nothing else is touched, the operator is told."""
        adapter = self.pulled()
        del self.machine.log[:]

        def driver() -> None:
            if not self.hal.pins[self.IO_ERROR]:
                self.hal.pins[self.IO_ERROR] = True

        self.halcmd.on_change = driver
        adapter.software_estop_reset()
        self.assertEqual(self.halcmd.calls, [["setp", self.IO_ERROR, "0"]])
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])
        self.assertEqual(self.machine.calls_except("wait_complete"), [])
        self.assertEqual(adapter.snapshot().message, A.LINK_NOT_RESTORED)
        self.assertIn("iletişim hatası sürüyor", adapter.snapshot().alarms[0].text)

    def test_a_board_that_was_power_cycled_recorded_no_bite(self):
        """io_error alone, nothing follows the clear: after LINK_SETTLE_S the
        reset goes on."""
        adapter = self.pulled()
        t0 = self.clock.now
        adapter.software_estop_reset()
        self.assertEqual(self.halcmd.calls, [["setp", self.IO_ERROR, "0"]])
        self.assertGreaterEqual(self.clock.now - t0, A.LINK_SETTLE_S)
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_a_halcmd_failure_sends_nothing_and_says_restart(self):
        adapter = self.bitten()
        self.halcmd = lambda args: (False, "HAL kilidi alınamadı")
        adapter.software_estop_reset()
        self.assertEqual(self.machine.calls_except("wait_complete"), [])
        msg = adapter.snapshot().message
        self.assertTrue(msg.startswith("Kart bağlantısı sıfırlanamadı — LinuxCNC'yi yeniden başlatın."))
        self.assertIn("HAL kilidi alınamadı", msg)

    def test_each_halcmd_step_that_fails_is_reported(self):
        for failing, kw in (("setp", {"io_error": True}), ("sets", {}), ("-s", {})):
            with self.subTest(step=failing):
                adapter = self.bitten(**kw)
                real = self.halcmd
                self.halcmd = (lambda real, failing: lambda args: (
                    (False, "") if args[0] == failing else real(args)))(real, failing)
                adapter.software_estop_reset()
                self.assertEqual(self.machine.calls_except("wait_complete"), [])
                self.assertIn("Kart bağlantısı sıfırlanamadı", adapter.snapshot().message)

    def test_a_link_that_drops_again_at_power_on_says_so(self):
        """The latch holds through ESTOP_RESET but drops the moment ON is
        sent (a link that fails under load, say)."""
        adapter = self.bitten()
        self.machine.estop_reasserts_on_power_on = True
        adapter.software_estop_reset()
        self.assertNotEqual(self.machine.task_state, C["STATE_ON"])
        self.assertEqual(adapter.snapshot().message, A.LINK_NOT_RESTORED)

    def test_a_has_bit_on_no_signal_is_set_directly(self):
        adapter = self.bitten()
        self.hal.signals = {}
        adapter.software_estop_reset()
        self.assertIn(["setp", self.HAS_BIT, "0"], self.halcmd.calls)
        self.assertEqual(adapter.snapshot().message, A.LINK_RESTORED)

    def test_an_unhome_linuxcnc_refused_is_called_out(self):
        """set_all_unhomed refuses all-or-none with only an rtapi message
        (homing.c:294-314): the operator must hear the joints are still homed."""
        adapter = self.bitten()
        self.machine.issue_unhome = lambda joint: None
        adapter.software_estop_reset()
        msg = adapter.snapshot().message
        self.assertTrue(msg.startswith(A.LINK_RESTORED))
        self.assertIn("DİKKAT: referanslar silinemedi", msg)

    def test_a_plain_software_estop_reset_is_unchanged(self):
        """No lost link: no halcmd, no unhome, homing kept."""
        adapter = self.on(homed=True)
        adapter.software_estop()
        del self.machine.log[:]
        adapter.software_estop_reset()
        self.assertEqual(self.halcmd.calls, [])
        self.assertNotIn("unhome", self.machine.calls())
        self.assertEqual(adapter.snapshot().message, "Yazılım acil stop sıfırlandı.")


class TestHalcmdHelpers(unittest.TestCase):
    """adapter._signal_of and _run_halcmd, against halcmd 2.9.9's own output
    (sim/, 2026-10-20)."""

    REAL = {
        "hm2_7i92.0 bit   I/O FALSE hm2_7i92.0.watchdog.has_bit <=> watchdog\n\n":
            ("hm2_7i92.0.watchdog.has_bit", "watchdog"),
        "estop_latch bit   IN FALSE estop-latch.0.fault-in <== watchdog\n\n":
            ("estop-latch.0.fault-in", "watchdog"),
        "sim_home_switch bit   OUT FALSE sim-x-min.home-sw ==> x-home-limit\n\n":
            ("sim-x-min.home-sw", "x-home-limit"),
    }

    def test_signal_of_reads_every_arrow(self):
        for out, (pin, sig) in self.REAL.items():
            with self.subTest(pin=pin):
                self.assertEqual(A._signal_of(out, pin), sig)

    def test_signal_of_an_unconnected_or_unknown_pin_is_none(self):
        self.assertIsNone(A._signal_of("hm2_7i92.0 bit   I/O FALSE p.q\n", "p.q"))
        self.assertIsNone(A._signal_of("", "p.q"))

    def test_fake_halcmd_prints_like_the_real_one(self):
        hal = F.FakeHal({"hm2_7i92.0.watchdog.has_bit": False})
        hal.signals = {"watchdog": ["hm2_7i92.0.watchdog.has_bit"]}
        ok, out = F.FakeHalcmd(hal)(["-s", "show", "pin", "hm2_7i92.0.watchdog.has_bit"])
        self.assertTrue(ok)
        self.assertIn(out, self.REAL)

    def test_run_halcmd_never_raises(self):
        saved = os.environ.get("PATH", "")
        self.addCleanup(os.environ.__setitem__, "PATH", saved)
        # Starting /bin/sh can take longer than the production timeout on a
        # loaded host (seen once on 2026-11-13); this test is about never
        # raising, not about the timeout, so give the stub script room.
        self.addCleanup(setattr, A, "HALCMD_TIMEOUT_S", A.HALCMD_TIMEOUT_S)
        A.HALCMD_TIMEOUT_S = 30.0
        with tempfile.TemporaryDirectory() as d:
            os.environ["PATH"] = d
            ok, out = A._run_halcmd(["-s", "show", "pin", "x"])
            self.assertFalse(ok)
            self.assertTrue(out)
            script = os.path.join(d, "halcmd")
            with open(script, "w") as fh:
                fh.write("#!/bin/sh\necho \"args: $*\"\nexit 3\n")
            os.chmod(script, 0o755)
            ok, out = A._run_halcmd(["sets", "watchdog", "0"])
            self.assertFalse(ok)
            self.assertEqual(out, "args: sets watchdog 0\n")


def write_switches(proc_dir: str, pid: str, count: int) -> None:
    """A /proc/PID/status with the lines that matter (format as the kernel's)."""
    with open(os.path.join(proc_dir, pid, "status"), "w") as fh:
        fh.write("Name:\tx\nState:\tS (sleeping)\n"
                 "voluntary_ctxt_switches:\t{}\nnonvoluntary_ctxt_switches:\t3\n".format(count))


def write_ticks(proc_dir: str, pid: str, utime: int, stime: int = 7, comm: str = "x y") -> None:
    """A /proc/PID/stat as the kernel prints it (sim/, 2026-10-21): the comm
    in parentheses - it may hold spaces - then 50 fields; utime and stime
    are the 14th and 15th overall."""
    fields = [str(pid), "({})".format(comm), "S", "1", "7", "1", "0", "-1", "4194560", "2011",
              "0", "1", "0", str(utime), str(stime)] + ["0"] * 37
    with open(os.path.join(proc_dir, pid, "stat"), "w") as fh:
        fh.write(" ".join(fields) + "\n")


def make_proc(testcase: unittest.TestCase, *names: str) -> Tuple[str, Dict[str, str]]:
    """A fake /proc with one process per name; returns (dir, name -> pid)."""
    d = tempfile.mkdtemp()
    testcase.addCleanup(__import__("shutil").rmtree, d)
    pids = {}
    for pid, name in enumerate(names, start=100):
        os.makedirs(os.path.join(d, str(pid)))
        with open(os.path.join(d, str(pid), "comm"), "w") as fh:
            fh.write(name + "\n")
        pids[name] = str(pid)
        write_switches(d, str(pid), 1000)
        write_ticks(d, str(pid), 500)
    os.makedirs(os.path.join(d, "self"))  # not a pid: ignored
    os.makedirs(os.path.join(d, "999"))   # exited while listed: no comm
    return d, pids


class TestLinuxCncLost(FakeCase):
    """linuxcnc_lost(), for app.serve's kiosk supervision."""

    def proc(self, *names) -> str:
        return make_proc(self, *names)[0]

    def test_all_there_is_none(self):
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "milltask", "io", "rtapi_app", "bash"))
        self.assertIsNone(adapter.linuxcnc_lost())

    def test_a_dead_task_is_named(self):
        """Task gone: the status buffer still reads, stale - only the process
        list shows it."""
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "io", "rtapi_app"))
        self.assertEqual(adapter.linuxcnc_lost(), "LinuxCNC'nin bir parçası çalışmıyor (milltask)")

    def test_unreadable_status_is_named(self):
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "milltask", "io", "rtapi_app"))
        self.machine.channels_valid = False
        self.assertTrue(adapter.linuxcnc_lost().startswith("LinuxCNC'nin durumu okunamıyor"))

    def test_without_proc_only_the_status_is_checked(self):
        self.assertIsNone(self.make().linuxcnc_lost())

    def test_process_names_follow_the_ini_as_the_linuxcnc_script_does(self):
        profile = rig_profile(
            self, {("TASK", "TASK"): "emctask", ("EMCIO", "EMCIO"): "myio"},
        )
        adapter = self.make(profile=profile)
        self.assertEqual(adapter._linuxcnc_processes,
                         ("linuxcncsvr", "linuxcnctask", "myio", "rtapi_app"))
        self.assertEqual(adapter._loop_processes, ("linuxcnctask", "myio"))

    def test_the_startup_note_is_logged_and_shown(self):
        adapter = self.make()
        adapter.note_startup("13:10 — LinuxCNC'nin durumu okunamıyor")
        snap = adapter.snapshot()
        self.assertEqual(snap.message,
                         "Kontrol sistemi yeniden başlatıldı — önceki durma: 13:10 — "
                         "LinuxCNC'nin durumu okunamıyor")
        self.assertEqual(snap.alarms[0].source, "kiosk")

    def test_a_dead_rtapi_app_is_named(self):
        """uspace's realtime process, motion and the board driver in one
        (scripts/realtime.in); with it gone the status still reads. Named
        rtapi_app in /proc (sim/, 2026-10-21)."""
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "milltask", "io"))
        self.assertEqual(adapter.linuxcnc_lost(), "LinuxCNC'nin bir parçası çalışmıyor (rtapi_app)")



class TestStuckLoop(FakeCase):
    """A LinuxCNC task (or iocontrol) that hangs without dying, judged by
    the kernel's counts of its voluntary context switches and CPU time
    (_stalled_loops): the stand-in for task's heartbeat, which the binding
    does not export."""

    NAMES = ("linuxcncsvr", "milltask", "io", "rtapi_app")

    def setUp(self):
        self.d, self.pids = make_proc(self, *self.NAMES)
        self.adapter = self.on(homed=True, proc_root=self.d)
        self.counts = {n: 1000 for n in self.NAMES}
        self.ticks = {n: 500 for n in self.NAMES}

    def tick(self, seconds: float, frozen=(), eager=()) -> None:
        """Let `seconds` pass; every loop but the `frozen` ones runs. An
        `eager` loop works without sleeping: CPU time grows, switches do
        not (task with queued program lines, emctaskmain.cc:3593)."""
        self.clock.now += seconds
        for name in ("milltask", "io"):
            if name not in frozen and name not in eager:
                self.counts[name] += max(1, int(seconds * 50))
                self.ticks[name] += max(1, int(seconds * 3))
            if name in eager:
                self.ticks[name] += max(1, int(seconds * 90))
            write_switches(self.d, self.pids[name], self.counts[name])
            write_ticks(self.d, self.pids[name], self.ticks[name])

    def test_an_eager_task_is_alive(self):
        """Audit 2026-10-21: task skips its sleep while it has queued work,
        so a long run of program lines froze the switch count; judged by
        switches alone the UI would have exited and the kiosk killed LinuxCNC
        mid-program."""
        self.adapter.snapshot()
        for _ in range(4):
            self.tick(1.0, eager=("milltask",))
            self.assertIsNone(self.adapter.linuxcnc_lost())
        self.tick(1.0, frozen=("milltask",))
        self.tick(A.LOOP_STALL_S, frozen=("milltask",))
        self.assertEqual(self.adapter.linuxcnc_lost(),
                         "LinuxCNC'nin bir parçası cevap vermiyor (milltask)")

    def test_cpu_ticks_are_read_past_a_comm_with_spaces_and_parens(self):
        write_ticks(self.d, self.pids["io"], 18, 7, comm="io (x) y")
        self.assertEqual(A._cpu_ticks(self.d, self.pids["io"]), 25)
        self.assertIsNone(A._cpu_ticks(self.d, "424242"))
        with open(os.path.join(self.d, self.pids["io"], "stat"), "w") as fh:
            fh.write("garbage\n")
        self.assertIsNone(A._cpu_ticks(self.d, self.pids["io"]))

    def test_rtapi_app_is_not_judged(self):
        """/proc/PID/status counts the main thread, which idles in rtapi_app
        while its realtime thread works (sim/ 2026-10-21: 6 switches in
        total for a running one)."""
        self.adapter.snapshot()
        for _ in range(4):
            self.tick(1.0)   # rtapi_app's counters never move
        self.assertIsNone(self.adapter.linuxcnc_lost())

    def test_running_loops_are_fine(self):
        for _ in range(5):
            self.tick(1.0)
            self.assertIsNone(self.adapter.linuxcnc_lost())
            self.assertTrue(self.adapter.snapshot().status_known)

    def test_a_stuck_task_is_named_and_its_stale_status_not_shown(self):
        self.adapter.snapshot()                    # first look: recorded
        self.tick(1.0, frozen=("milltask",))
        self.assertTrue(self.adapter.snapshot().status_known)   # under LOOP_STALL_S
        self.tick(A.LOOP_STALL_S, frozen=("milltask",))
        self.assertEqual(self.adapter.linuxcnc_lost(),
                         "LinuxCNC'nin bir parçası cevap vermiyor (milltask)")
        snap = self.adapter.snapshot()
        self.assertFalse(snap.status_known)
        self.assertEqual(snap.positions, ())
        self.assertIn("milltask 2 s'dir cevap vermiyor", snap.alarms[0].text)

    def test_actions_refuse_without_sending_while_the_task_is_stuck(self):
        """A send to a stuck task spins up to 5 s holding the GIL."""
        self.adapter.snapshot()
        self.tick(A.LOOP_STALL_S + 0.5, frozen=("milltask",))
        del self.machine.log[:]
        self.adapter.jog("X", 1)
        self.adapter.home_all()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(self.adapter.snapshot().message, A.NO_LINUXCNC_REFUSAL)

    def test_the_software_estop_still_goes_out(self):
        self.adapter.snapshot()
        self.tick(A.LOOP_STALL_S + 0.5, frozen=("milltask",))
        self.adapter.software_estop()
        self.assertIn(("state", (C["STATE_ESTOP"],)), self.machine.log)

    def test_a_stuck_iocontrol_counts_too(self):
        self.adapter.snapshot()
        self.tick(A.LOOP_STALL_S + 0.5, frozen=("io",))
        self.assertEqual(self.adapter.linuxcnc_lost(), "LinuxCNC'nin bir parçası cevap vermiyor (io)")

    def test_linuxcncsvr_is_not_judged(self):
        """It waits on events: 0 switches/s when healthy (sim/, 2026-10-20)."""
        self.adapter.snapshot()
        for _ in range(4):
            self.tick(1.0)   # linuxcncsvr's count never moves
        self.assertIsNone(self.adapter.linuxcnc_lost())

    def test_recovery_is_logged_once_and_the_status_comes_back(self):
        self.adapter.snapshot()
        self.tick(A.LOOP_STALL_S + 0.5, frozen=("milltask",))
        self.assertFalse(self.adapter.snapshot().status_known)
        self.tick(1.0)
        snap = self.adapter.snapshot()
        self.assertTrue(snap.status_known)
        self.assertEqual(snap.alarms[0].text, "LinuxCNC durum kanalı yeniden okunuyor")

    def test_an_unreadable_status_file_is_not_a_stall(self):
        self.adapter.snapshot()
        os.unlink(os.path.join(self.d, self.pids["milltask"], "status"))
        self.clock.now += 10
        self.counts["io"] += 100   # iocontrol keeps running
        write_switches(self.d, self.pids["io"], self.counts["io"])
        self.assertEqual(self.adapter._stalled_loops(), [])
        self.assertIsNone(A._voluntary_switches(self.d, self.pids["milltask"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
