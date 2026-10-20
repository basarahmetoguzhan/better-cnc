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
        ("emc/task/emctaskmain.cc", "can't do that (%s) in auto mode with the interpreter reading",
         "can't do that ({}) in auto mode with the interpreter reading"),
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
    #: The action channel through _send(), the estop channel, and waits.
    COMMAND_RES = (
        re.compile(r"self\._send\(\)\.(\w+)"),
        re.compile(r"self\._(?:command|estop_channel)\.(\w+)"),
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
        matters, so both channels exist after construction."""
        adapter = self.on(homed=True)
        self.assertEqual(len(self.machine.channels), 2)
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
        self.assertEqual({j.phase for j in snap.homing.joints}, {HomingPhase.HOMING})
        self.assertEqual(snap.status_label, "REFERANS ALINIYOR")
        self.machine.finish_homing()
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_error_channel_is_drained_newest_first_with_severity(self):
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
        self.assertEqual(snap.message, "LinuxCNC: fourth")
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
        """Stopping never waits on a read: abort() goes out first. Afterwards
        the status cannot be read, so the message must not claim either
        "stopped" or "nothing sent"."""
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        adapter.program_stop()
        self.assertEqual(self.machine.calls(), ["abort", "wait_complete"])
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

    def test_feed_and_step_do_not_poll(self):
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        polls = self.machine.stat_polls
        adapter.set_feed_override(50)
        adapter.set_jog_step(0.1)
        self.assertEqual(self.machine.stat_polls, polls)
        self.assertEqual(self.machine.log, [("feedrate", (0.5,))])

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
        self.assertEqual(adapter.snapshot().message, "Jog X+1 mm gönderildi (eklem).")

    def test_after_homing_jogs_the_axis(self):
        adapter = self.on(homed=True)
        adapter.jog("Z", 1)
        self.assertNoLinuxCncErrors()
        fn, jjogmode, index, vel, incr = self.jogs()[0]
        self.assertEqual((jjogmode, index), (0, 2))
        self.assertEqual(self.machine.axis_pos[2], 1.0)
        self.assertEqual(adapter.snapshot().message, "Jog Z+1 mm gönderildi (eksen).")

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
        927-958). The adapter reads the mode back instead of jogging anyway."""
        adapter = self.on(homed=True)
        self.machine.motion_mode = C["TRAJ_MODE_FREE"]
        self.machine.jogging_active = True
        adapter.jog("Y", 1)
        self.assertEqual(self.machine.calls(), ["teleop_enable", "wait_complete"])
        self.assertEqual(
            adapter.snapshot().message,
            "Jog modu değişmedi (LinuxCNC hareket sürerken mod değiştirmez) — jog yok.",
        )

    def test_unhomed_teleop_set_elsewhere_is_kept(self):
        """Identity kins allow teleop unhomed (motion.c:166-171 only checks
        other kins); axis.py keeps whatever motion is in (:3230-3237)."""
        adapter = self.on(homed=False)
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
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
        for homed, expected in ((False, (1, 1)), (True, (0, 2))):
            with self.subTest(homed=homed):
                adapter = self.make(machine=F.FakeMachine(coordinates="XZ"), joint_config=cfg)
                self.machine.power_on(homed=homed)
                adapter.jog("Z", 1)
                self.assertNoLinuxCncErrors()
                self.assertEqual(self.jogs()[0][1:3], expected)
                self.assertEqual(self.machine.axis_pos[2], 1.0)

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
        second home is refused."""
        adapter = self.on(homed=True)
        adapter.home_all()
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log[-1], ("home", (-1,)))
        self.assertEqual(self.machine.motion_mode, C["TRAJ_MODE_FREE"])

    def test_leaving_teleop_aborts_a_teleop_jog_then_homes(self):
        """EMCMOT_FREE aborts every teleop jog (axis_jog_abort_all,
        command.c:556-567), and motion switches once in position
        (control.c:961-972) - in the fake, at once."""
        adapter = self.on(homed=True)
        self.machine.jogging_active = True
        adapter.home_all()
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
        adapter.home_all()
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
        self.assertEqual(adapter.snapshot().message, "Referans alma sürüyor.")
        self.machine.finish_homing()
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_the_fake_unhomes_joint_0_on_a_second_home_all(self):
        """What the refusal above prevents, straight on the fake."""
        machine = F.FakeMachine(home_instantly=False)
        machine.power_on()
        with F.installed(machine=machine) as (lc, _, m, _):
            c = lc.command()
            c.home(-1)
            m.homing[0], m.homed[0] = 0, 1
            c.home(-1)
            self.assertEqual(m.homed[0], 0)
            self.assertEqual(m.homing[:3], [0, 1, 1])
            m.finish_homing()
        self.assertEqual(m.homed[:3], [0, 1, 1])

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
        adapter.program_stop()
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
        snapshot only reads and clears (LinuxCncAdapter._note_task_state)."""
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
        self.assertEqual(changed, {"_message", "_alarms", "_estop_sent"})

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
            ("home", True, lambda a: a.home_all(), "Makine devre dışı — referans alınamaz."),
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
                # On a channel of its own, opened before any action ran.
                estop_channel = self.machine.log_channel[-1]
                self.assertNotIn(estop_channel, self.machine.log_channel[:-1])
                self.assertEqual(len(self.machine.channels), 2)
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
        snap = adapter.snapshot()
        self.assertEqual(snap.mdi_history[-1], entry)
        self.assertEqual(snap.message, "MDI: G0 X1 — Gönderildi.")

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
        self.assertEqual(snap.message, "{} yüklendi.".format(path))

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
        self.assertEqual(snap.message, "{} yüklendi.".format(path))

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
        for name, prepare in (
            ("homing", lambda m: m.homing.__setitem__(1, 1)),
            ("joint jog", lambda m: setattr(m, "jogging_active", True)),
        ):
            with self.subTest(moving=name):
                adapter = self.on(homed=False)
                prepare(self.machine)
                adapter.load_program(path)
                self.assertEqual(self.machine.log, [])
                self.assertEqual(
                    adapter.snapshot().message,
                    "Referans alma ya da jog sürüyor — program yüklenmedi.",
                )

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
        self.assertEqual(adapter.snapshot().message, "Program zaten çalışıyor.")

    def test_load_while_running_is_refused(self):
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        adapter.program_start()
        del self.machine.log[:]
        adapter.load_program(self.ngc())
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, "Program çalışırken yeni program yüklenemez.")

    def test_stop_aborts_and_waits(self):
        """task_stop, axis.py:2411-2417."""
        adapter = self.on(homed=True)
        adapter.load_program(self.ngc())
        adapter.program_start()
        del self.machine.log[:]
        adapter.program_stop()
        self.assertEqual(self.machine.log, [("abort", ()), ("wait_complete", (WAIT,))])
        snap = adapter.snapshot()
        self.assertFalse(snap.program_running)
        self.assertEqual(snap.message, "Program durduruldu.")

    def test_stop_that_has_not_taken_yet_says_so(self):
        """abort() was sent but the interpreter is still busy after the
        bounded wait: the operator is told it was sent, not that it stopped."""
        class SlowAbort(F.FakeMachine):
            def issue_abort(self) -> None:
                pass

        adapter = self.on(homed=True, machine=SlowAbort())
        adapter.load_program(self.ngc())
        adapter.program_start()
        adapter.program_stop()
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
            ("home", lambda a: a.home_all(), "referans alınamaz"),
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
    os.makedirs(os.path.join(d, "self"))  # not a pid: ignored
    os.makedirs(os.path.join(d, "999"))   # exited while listed: no comm
    return d, pids


class TestLinuxCncLost(FakeCase):
    """linuxcnc_lost(), for app.serve's kiosk supervision."""

    def proc(self, *names) -> str:
        return make_proc(self, *names)[0]

    def test_all_there_is_none(self):
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "milltask", "io", "bash"))
        self.assertIsNone(adapter.linuxcnc_lost())

    def test_a_dead_task_is_named(self):
        """Task gone: the status buffer still reads, stale - only the process
        list shows it."""
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "io"))
        self.assertEqual(adapter.linuxcnc_lost(), "LinuxCNC'nin bir parçası çalışmıyor (milltask)")

    def test_unreadable_status_is_named(self):
        adapter = self.make(proc_root=self.proc("linuxcncsvr", "milltask", "io"))
        self.machine.channels_valid = False
        self.assertTrue(adapter.linuxcnc_lost().startswith("LinuxCNC'nin durumu okunamıyor"))

    def test_without_proc_only_the_status_is_checked(self):
        self.assertIsNone(self.make().linuxcnc_lost())

    def test_process_names_follow_the_ini_as_the_linuxcnc_script_does(self):
        profile = rig_profile(
            self, {("TASK", "TASK"): "emctask", ("EMCIO", "EMCIO"): "myio"},
        )
        adapter = self.make(profile=profile)
        self.assertEqual(adapter._linuxcnc_processes, ("linuxcncsvr", "linuxcnctask", "myio"))



class TestStuckLoop(FakeCase):
    """A LinuxCNC task (or iocontrol) that hangs without dying, judged by
    the kernel's count of its voluntary context switches (_stalled_loops):
    the stand-in for task's heartbeat, which the binding does not export."""

    NAMES = ("linuxcncsvr", "milltask", "io")

    def setUp(self):
        self.d, self.pids = make_proc(self, *self.NAMES)
        self.adapter = self.on(homed=True, proc_root=self.d)
        self.counts = {n: 1000 for n in self.NAMES}

    def tick(self, seconds: float, frozen=(), ) -> None:
        """Let `seconds` pass; every loop but the `frozen` ones runs."""
        self.clock.now += seconds
        for name in ("milltask", "io"):
            if name not in frozen:
                self.counts[name] += max(1, int(seconds * 50))
            write_switches(self.d, self.pids[name], self.counts[name])

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
