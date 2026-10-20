"""Fake `linuxcnc` and `hal` modules, derived from the LinuxCNC 2.9 sources.

Why this exists: LinuxCncAdapter is the UI's only path to the machine, and
docs/11-ui-implementation.md calls it "the code most likely to be wrong on first
contact with the real machine". The real modules only import on a machine with
LinuxCNC running, so without a fake every line of the adapter is untested.

A fake is only worth having if it rejects what LinuxCNC rejects. A permissive
fake (every call succeeds) would have passed the old adapter, whose jog was
refused by motion before homing. So this one carries a small model of the task
planner and of motion, taken from the source, and reports refusals the way
LinuxCNC does: as (kind, text) on the error channel, not as Python exceptions.
Only argument errors raise, because only those raise in the real binding.

Every rule cites its source, relative to reference/linuxcnc/ (LinuxCNC 2.9.10,
commit 18c5bb5b). Abbreviations used in the comments:

  emcmodule.cc   src/emc/usr_intf/axis/extensions/emcmodule.cc  (the binding)
  emctaskmain.cc src/emc/task/emctaskmain.cc                    (task planner)
  emctask.cc     src/emc/task/emctask.cc
  taskintf.cc    src/emc/task/taskintf.cc                       (task -> motion)
  command.c      src/emc/motion/command.c                       (motion)
  motion.c       src/emc/motion/motion.c
  ioControl.cc   src/emc/iotask/ioControl.cc
  halmodule.cc   src/hal/halmodule.cc                           (`hal` binding)
  hal_lib.c      src/hal/hal_lib.c

What is deliberately NOT modelled (a test relying on it would be testing the
fake): soft/hard limits, jog velocity clamping to [JOINT_n]MAX_VELOCITY
(taskintf.cc:820-826, magnitude only, the sign survives), homing switch search
(homing completes in one step unless `home_instantly=False`), the interpreter
(an opened program "runs" until aborted), spindle, coolant, tool changes,
volatile homing on estop, real time. Commands complete synchronously, and so
does iocontrol (no echo latency on state()).

What IS kept of timing, because the adapter's safety depends on it:

  - `stat` only changes on `poll()`, so code that forgets to poll after a
    command reads stale state, as it would on the machine;
  - wait_complete() returns -1 (timed out) while motion is not in position or
    the interpreter is not idle, as task never reports DONE then
    (emctaskmain.cc:3546-3561, taskintf.cc:974-983); it records the timeout
    it was given, and `wait_gate` can hold it open so a test can run another
    thread (the software estop) meanwhile;
  - motion drops a teleop request while not in position (control.c:927-958),
    with `jogging_active` / a homing joint meaning "not in position"; a
    request to leave teleop first aborts the teleop jog (EMCMOT_FREE,
    command.c:556-567) and then switches (control.c:961-972);
  - the rig's estop chain: estop_latch.0 (estop_latch.comp) between
    iocontrol's user-request-enable and emc-enable-in, with `fault_in` as
    its fault input and `user_enable_out_in_chain` saying whether iocontrol's
    user-enable-out feeds its ok-in (False, as configs/test-rig/test-rig.hal
    wires it). With `estop_clear_after_polls = N`, aux.estop clears only on
    the Nth stat.poll() after ESTOP_RESET, standing in for iocontrol's cycle
    (ioControl.cc:518-526);
  - `channels_valid = False` makes stat.poll() and error_channel.poll() raise
    linuxcnc.error, as they do once the NML buffers are invalid
    (emcmodule.cc:277-283, :1562-1565). `stat_polls` counts poll() calls.
  - the tool mmap: the first stat.poll() of a fake module (one module = one
    process) "opens" it; with `tool_mmap_present = False` then, that poll
    carries on but every later poll of any stat object of the module raises
    SystemError, as the binding's process-wide statics make it
    (emcmodule.cc:286-305). LinuxCNC not running at all is
    `channels_valid = False` together with `tool_mmap_present = False`.

ui/tests/test_linuxcnc_adapter.py checks this file against the source when
reference/ is present (constants, method names, argument formats).
"""

from __future__ import annotations

import collections
import contextlib
import os
import sys
import threading
import types
from typing import Any, Callable, Deque, Dict, Iterator, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Constants. Names come from the module init (emcmodule.cc:2405-2533);
# values from the C headers they name.
# ---------------------------------------------------------------------------

CONSTANTS: Dict[str, int] = {
    # ENUMX(9, EMC_TASK_MODE_*)  emcmodule.cc:2452-2454; emc.hh:249-253
    "MODE_MANUAL": 1,
    "MODE_AUTO": 2,
    "MODE_MDI": 3,
    # ENUMX(9, EMC_TASK_STATE_*) emcmodule.cc:2456-2459; emc.hh:256-261
    "STATE_ESTOP": 1,
    "STATE_ESTOP_RESET": 2,
    "STATE_OFF": 3,
    "STATE_ON": 4,
    # ENUMX(9, EMC_TASK_INTERP_*) emcmodule.cc:2447-2450; emc.hh:277-282
    "INTERP_IDLE": 1,
    "INTERP_READING": 2,
    "INTERP_PAUSED": 3,
    "INTERP_WAITING": 4,
    # ENUMX(6, LOCAL_JOG_*) emcmodule.cc:2477-2479; values emcmodule.cc:63-65
    "JOG_STOP": 0,
    "JOG_CONTINUOUS": 1,
    "JOG_INCREMENT": 2,
    # ENUMX(6, LOCAL_AUTO_*) emcmodule.cc:2481-2486; values emcmodule.cc:67-72
    "AUTO_RUN": 0,
    "AUTO_PAUSE": 1,
    "AUTO_RESUME": 2,
    "AUTO_STEP": 3,
    "AUTO_REVERSE": 4,
    "AUTO_FORWARD": 5,
    # ENUMX(4, EMC_TRAJ_MODE_*) emcmodule.cc:2488-2490; emc.hh:285-289
    "TRAJ_MODE_FREE": 1,
    "TRAJ_MODE_COORD": 2,
    "TRAJ_MODE_TELEOP": 3,
    # ENUM(KINEMATICS_*) emcmodule.cc:2499-2502; kinematics.h:45-50
    "KINEMATICS_IDENTITY": 1,
    "KINEMATICS_FORWARD_ONLY": 2,
    "KINEMATICS_INVERSE_ONLY": 3,
    "KINEMATICS_BOTH": 4,
    # ENUM(RCS_*) emcmodule.cc:2531-2533; libnml/rcs/rcs.hh:31-36
    "RCS_DONE": 1,
    "RCS_EXEC": 2,
    "RCS_ERROR": 3,
    # PyModule_AddIntConstant emcmodule.cc:2433-2438;
    # emc.hh:50-52 (OPERATOR_*), libnml/nml/nml_oi.hh:26-28 (NML_*)
    "OPERATOR_ERROR": 11,
    "OPERATOR_TEXT": 12,
    "OPERATOR_DISPLAY": 13,
    "NML_ERROR": 1,
    "NML_TEXT": 2,
    "NML_DISPLAY": 3,
    # ENUMX(7, EMCMOT_MAX_*) emcmodule.cc:2527-2528; emcmotcfg.h:25,31
    "MAX_JOINTS": 16,
    "MAX_AXIS": 9,
}

_C = CONSTANTS  # short alias for the model below

#: Fixed teleop axis order (command.c:441 "XYZABCUVW"[axis]).
AXIS_LETTERS = "XYZABCUVW"

#: NML message type numbers used in task error texts (emc.hh:75-166) and the
#: symbol emc_symbol_lookup() prints for them (emc.cc:479 onwards).
_MSG = {
    "EMC_JOINT_HOME": 123,
    "EMC_JOG_CONT": 124,
    "EMC_JOG_INCR": 125,
    "EMC_JOG_STOP": 136,
    "EMC_TRAJ_SET_SCALE": 209,
    "EMC_TRAJ_SET_TELEOP_ENABLE": 230,
    "EMC_TASK_ABORT": 503,
    "EMC_TASK_SET_MODE": 504,
    "EMC_TASK_SET_STATE": 505,
    "EMC_TASK_PLAN_OPEN": 506,
    "EMC_TASK_PLAN_RUN": 507,
    "EMC_TASK_PLAN_EXECUTE": 509,
    "EMC_TASK_PLAN_CLOSE": 514,
    "EMC_TASK_PLAN_SYNCH": 516,
}

#: sizeof(EMC_TASK_PLAN_EXECUTE::command) - 1 and sizeof(...OPEN::file) - 1:
#: both are char[LINELEN] (emc_nml.hh:1309,1345), LINELEN = 255 (linuxcnc.h:18).
LINE_LIMIT = 254

# -- task planner acceptance tables (emcTaskPlan, emctaskmain.cc:763-1480) ---
# Only the message types this fake implements appear. Anything else in a given
# state/mode is refused with that branch's error text.

#: OFF / ESTOP / ESTOP_RESET, any mode: the immediate list, emctaskmain.cc:807-844.
_NOT_ON_OK = {
    "EMC_JOINT_UNHOME",                                   # emctaskmain.cc:814
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE", "EMC_TASK_ABORT",
    "EMC_TASK_PLAN_SYNCH", "EMC_TRAJ_SET_TELEOP_ENABLE",
}
#: ON + MANUAL, emctaskmain.cc:893-1011 (PLAN_EXECUTE: 978-984).
_MANUAL_OK = {
    "EMC_JOINT_HOME", "EMC_JOINT_UNHOME", "EMC_JOG_CONT", "EMC_JOG_INCR", "EMC_JOG_STOP",
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE",
    "EMC_TASK_PLAN_SYNCH", "EMC_TRAJ_SET_TELEOP_ENABLE", "EMC_TASK_PLAN_EXECUTE",
}
#: ON + MANUAL + PLAN_RUN: accepted and dropped without a message unless an
#: external offset is applied (emctaskmain.cc:995-1000).
_MANUAL_SILENT = {"EMC_TASK_PLAN_RUN"}
#: ON + AUTO + interpreter IDLE, emctaskmain.cc:1015-1112. No PLAN_SYNCH here.
_AUTO_IDLE_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE",
    "EMC_TASK_PLAN_RUN", "EMC_TASK_PLAN_EXECUTE",
}
#: ON + AUTO + interpreter READING, emctaskmain.cc:1120-1182.
_AUTO_READING_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT",
}
#: ON + MDI, emctaskmain.cc:1360-1468.
_MDI_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE", "EMC_TASK_PLAN_SYNCH",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_EXECUTE",
}
#: allow_while_idle_type(), emctaskmain.cc:741-756: let through in AUTO-idle and
#: MDI even though the tables above do not list them.
_ALLOW_WHILE_IDLE = {
    "EMC_JOG_CONT", "EMC_JOG_INCR", "EMC_JOG_STOP", "EMC_TRAJ_SET_TELEOP_ENABLE",
}


class Rejected(Exception):
    """Internal: a command refused by task. Never escapes the fake.

    Task-level refusals (planner tables, MDI when not homed, can't open) make
    the command's status RCS_ERROR (emctaskmain.cc:3546-3551)."""


class MotionRejected(Rejected):
    """Internal: a command refused by motion (command.c / motion.c).

    Motion reports the text but leaves commandStatus OK (command.c:407), so
    task's status stays RCS_DONE - unless the refusal also sets the joint error
    flag (SET_JOINT_ERROR_FLAG), which task turns into RCS_ERROR
    (taskintf.cc:974-978)."""

    def __init__(self, text: str, sets_joint_error: bool = False):
        super().__init__(text)
        self.sets_joint_error = sets_joint_error


#: Upper bound on how long `wait_gate` can hold wait_complete(), so a test that
#: forgets to release it fails instead of hanging the suite.
GATE_LIMIT_S = 5.0


# ---------------------------------------------------------------------------
# PyArg_ParseTuple, for the format letters the binding uses
# ---------------------------------------------------------------------------


def parse_args(fmt: str, args: Sequence[Any]) -> List[Any]:
    """Validate `args` the way CPython 3.9's PyArg_ParseTuple does for `fmt`.

    Supports i, d, b, s, s#, the '|' optional marker and a ':name' suffix -
    every format in the methods this fake implements. Count errors use
    getargs.c's message shape ("function takes exactly 1 argument (2 given)").
    """
    fname = None
    if ":" in fmt:
        fmt, fname = fmt.split(":", 1)
    units: List[str] = []
    optional_from = None
    i = 0
    while i < len(fmt):
        ch = fmt[i]
        if ch == "|":
            optional_from = len(units)
            i += 1
            continue
        if ch == "s" and fmt[i + 1:i + 2] == "#":
            units.append("s#")
            i += 2
            continue
        units.append(ch)
        i += 1
    lo = len(units) if optional_from is None else optional_from
    hi = len(units)
    n = len(args)
    if n < lo or n > hi:
        bound = lo if n < lo else hi
        raise TypeError(
            "{}{} takes {} {} argument{} ({} given)".format(
                fname or "function",
                "()" if fname else "",
                "exactly" if lo == hi else ("at least" if n < lo else "at most"),
                bound,
                "" if bound == 1 else "s",
                n,
            )
        )
    out: List[Any] = []
    for pos, (unit, value) in enumerate(zip(units, args), start=1):
        out.append(_convert(unit, value, pos))
    return out


def _convert(unit: str, value: Any, pos: int) -> Any:
    if unit == "i":
        if isinstance(value, float):
            raise TypeError("integer argument expected, got float")
        if not hasattr(value, "__index__"):
            raise TypeError(
                "an integer is required (got type {})".format(type(value).__name__)
            )
        return int(value.__index__())
    if unit == "b":
        v = _convert("i", value, pos)
        if v < 0:
            raise OverflowError("unsigned byte integer is less than minimum")
        if v > 255:
            raise OverflowError("unsigned byte integer is greater than maximum")
        return v
    if unit == "d":
        if isinstance(value, (str, bytes)) or not (
            hasattr(value, "__float__") or hasattr(value, "__index__")
        ):
            raise TypeError(
                "must be real number, not {}".format(type(value).__name__)
            )
        return float(value)
    if unit == "s":
        if not isinstance(value, str):
            raise TypeError(
                "argument {} must be str, not {}".format(pos, type(value).__name__)
            )
        if "\0" in value:
            raise ValueError("embedded null character")
        return value
    if unit == "s#":
        if isinstance(value, bytes):
            return value.decode("utf-8")
        if not isinstance(value, str):
            raise TypeError(
                "argument {} must be str, bytes or read-only buffer, not {}".format(
                    pos, type(value).__name__
                )
            )
        return value
    raise AssertionError("format unit {!r} not implemented in the fake".format(unit))


# ---------------------------------------------------------------------------
# The machine behind the NML channels
# ---------------------------------------------------------------------------


class FakeMachine:
    """Live task + motion state that `command` changes and `stat.poll()` copies.

    The defaults are LinuxCNC just after start-up: ESTOP (ioControl.cc:791 sets
    aux.estop = 1; the estop latch starts faulted), MANUAL, free (joint) mode,
    nothing homed, identity kins (trivkins, trivkins.c:52,70), feed scale 1.0,
    coordinate system 1 (G54).
    """

    def __init__(
        self,
        coordinates: str = "XYZ",
        kinematics_type: int = _C["KINEMATICS_IDENTITY"],
        no_force_homing: bool = False,
        home_instantly: bool = True,
        openable: Optional[Callable[[str], bool]] = None,
        estop_clear_after_polls: int = 0,
    ):
        #: Joint n carries coordinate letter coordinates[n] (trivkins).
        self.coordinates = coordinates.upper()
        self.num_joints = len(self.coordinates)
        self.kinematics_type = kinematics_type
        #: [TRAJ]NO_FORCE_HOMING (emctaskmain.cc:121-122, 3245-3261).
        self.no_force_homing = no_force_homing
        self.home_instantly = home_instantly
        #: emcTaskPlanOpen() succeeds iff the interpreter can open the file.
        self.openable = openable if openable is not None else os.path.isfile

        self.aux_estop = 1                       # stat.estop, io.aux.estop
        # -- the estop chain, configs/test-rig/test-rig.hal ESTOP section:
        #      iocontrol.0.user-request-enable -> estop-latch.0.reset
        #      estop-latch.0.ok-out            -> iocontrol.0.emc-enable-in
        #      watchdog.has_bit                -> estop-latch.0.fault-in
        #: estop-latch.0.fault-in. True models an open chain (a bitten
        #: watchdog on the rig): the latch trips and cannot be reset.
        self.fault_in = False
        #: Whether iocontrol.0.user-enable-out feeds estop-latch.0.ok-in. The
        #: rig leaves ok-in unconnected (default true, estop_latch.comp), so a
        #: software estop does not trip the latch. True models the wiring
        #: where it does (user-enable-out in the chain).
        self.user_enable_out_in_chain = False
        #: iocontrol.0.user-enable-out: 0 at start (ioControl.cc:489), 0 on
        #: ESTOP (:1086), 1 on ESTOP_RESET (:1093).
        self.user_enable_out = 0
        #: estop-latch.0.ok-out, i.e. emc-enable-in. Starts false
        #: ("pin out bit ok_out = false", estop_latch.comp).
        self.latch_ok = False
        #: 0: ESTOP_RESET clears aux.estop at once. N > 0: only the Nth
        #: stat.poll() afterwards sees it cleared (the latch / iocontrol cycle).
        self.estop_clear_after_polls = estop_clear_after_polls
        self._estop_countdown = 0
        #: stat.poll() calls on any stat object of this machine, failed ones
        #: included.
        self.stat_polls = 0
        #: Test switch: the chain drops again the moment ON is sent, so task
        #: falls back to ESTOP (emctaskmain.cc:3413-3416).
        self.estop_reasserts_on_power_on = False
        #: False once the NML buffers are invalid: polls raise linuxcnc.error.
        self.channels_valid = True
        #: Whether $HOME/.tool.mmap exists for the polling process at its
        #: first poll (see the module docstring, "the tool mmap").
        self.tool_mmap_present = True
        #: The error channel alone invalid (status still readable).
        self.error_channel_valid = True
        #: When set to an Event, wait_complete() blocks on it (bounded by
        #: GATE_LIMIT_S) after setting `wait_entered`.
        self.wait_gate: Optional[threading.Event] = None
        self.wait_entered = threading.Event()
        self.traj_enabled = False                # stat.enabled
        self.task_mode = _C["MODE_MANUAL"]
        self.mdi_or_auto = _C["MODE_AUTO"]       # emctask.cc:283 default
        self.motion_mode = _C["TRAJ_MODE_FREE"]
        self.interp_state = _C["INTERP_IDLE"]
        #: motion.jogging_active (emctaskmain.cc:154-156). Jogs complete
        #: instantly here, so a test sets this to model one still moving.
        self.jogging_active = False
        self.paused = False
        self.file = ""
        self.feedrate = 1.0
        self.g5x_index = 1
        self.g5x_offset = [0.0] * 9
        self.homed = [0] * _C["MAX_JOINTS"]
        self.homing = [0] * _C["MAX_JOINTS"]
        self.joint_pos = [0.0] * _C["MAX_JOINTS"]
        self.axis_pos = [0.0] * _C["MAX_AXIS"]

        self.errors: Deque[Tuple[int, str]] = collections.deque()
        #: Every successfully parsed command-channel call, in order:
        #: (method name, converted arguments).
        self.log: List[Tuple[str, Tuple[Any, ...]]] = []
        #: Parallel to `log`: which linuxcnc.command() instance sent each call
        #: (its index in `channels`, in creation order).
        self.log_channel: List[int] = []
        self.channels: List[Any] = []
        self.mdi_executed: List[str] = []
        self._last_status = _C["RCS_DONE"]

    # -- derived state -------------------------------------------------------

    @property
    def task_state(self) -> int:
        """determineState(), emctask.cc:419-430."""
        if self.aux_estop:
            return _C["STATE_ESTOP"]
        if not self.traj_enabled:
            return _C["STATE_ESTOP_RESET"]
        return _C["STATE_ON"]

    def all_homed(self) -> bool:
        """all_homed(), emctaskmain.cc:147-152: the first traj.joints joints."""
        return all(self.homed[: self.num_joints])

    @property
    def emc_enable_in(self) -> bool:
        """iocontrol.0.emc-enable-in = estop-latch.0.ok-out on the rig."""
        return self.latch_ok

    @property
    def inpos(self) -> bool:
        """Whether motion is quiet: false while a jog or a homing move runs.
        Drives the fake's own rules (wait_status, mode switches)."""
        return not self.jogging_active and not any(self.homing[: self.num_joints])

    @property
    def inpos_flag(self) -> bool:
        """What stat.inpos reports (emcmodule.cc:366, taskintf.cc:1572):
        motion's INPOS flag. In free mode it is false while any joint's
        planner runs (control.c:1205-1272) - a joint jog or homing. The
        TELEOP and DISABLED branches never set it (control.c:1398-1477), so a
        teleop jog leaves it as it was: true, since entering teleop needs it
        (control.c:927-958)."""
        if any(self.homing[: self.num_joints]):
            return False
        return not (self.jogging_active and self.motion_mode == _C["TRAJ_MODE_FREE"])

    def run_latch(self, reset_edge: bool = False) -> None:
        """One estop_latch FUNCTION pass (estop_latch.comp): OK after a reset
        rising edge while ok-in and !fault-in; faulted whenever either
        fails."""
        ok_in = self.user_enable_out if self.user_enable_out_in_chain else True
        if ok_in and not self.fault_in:
            if reset_edge:
                self.latch_ok = True
        else:
            self.latch_ok = False

    def wait_status(self) -> int:
        """What emcWaitCommandComplete (emcmodule.cc:212-231) returns for the
        last command, given that it is never DONE while motion is moving or
        the interpreter busy (emctaskmain.cc:3546-3561): ERROR at once after a
        refusal, otherwise -1 (timeout) until motion is quiet."""
        if self._last_status == _C["RCS_ERROR"]:
            return self._last_status
        if not self.inpos or self.interp_state != _C["INTERP_IDLE"]:
            return -1
        return self._last_status

    def tick(self) -> None:
        """One stat.poll(): the latch runs, iocontrol reads emc-enable-in
        (aux.estop = !emc-enable-in, ioControl.cc:523-526) and task disables
        motion on aux.estop (emctaskmain.cc:3413-3416). A pending reset only
        clears aux.estop when its countdown ends."""
        self.run_latch()
        if not self.latch_ok:
            self.aux_estop = 1
            self.traj_enabled = False
            self._estop_countdown = 0
        elif self._estop_countdown > 0:
            self._estop_countdown -= 1
            if self._estop_countdown == 0:
                self.aux_estop = 0

    # -- test helpers --------------------------------------------------------

    def calls(self) -> List[str]:
        return [name for name, _ in self.log]

    def calls_except(self, *skip: str) -> List[Tuple[str, Tuple[Any, ...]]]:
        return [entry for entry in self.log if entry[0] not in skip]

    def estop_reset_state(self) -> None:
        """Out of estop but not ON (STATE_ESTOP_RESET), directly: the latch
        reset, motion disabled. No command is logged."""
        self.user_enable_out = 1
        self.latch_ok = True
        self.aux_estop = 0
        self.traj_enabled = False

    def power_on(self, homed: bool = False) -> None:
        """Put the machine in ON + MANUAL directly, without logging commands."""
        self.estop_reset_state()
        self.traj_enabled = True
        self.task_mode = _C["MODE_MANUAL"]
        if homed:
            for j in range(self.num_joints):
                self.homed[j] = 1
            self.motion_mode = _C["TRAJ_MODE_TELEOP"]   # emctask.cc:277-278
        else:
            self.motion_mode = _C["TRAJ_MODE_FREE"]     # emctask.cc:279-280

    def finish_homing(self) -> None:
        for j in range(self.num_joints):
            if self.homing[j]:
                self.homing[j] = 0
                self.homed[j] = 1

    # -- error channel -------------------------------------------------------

    def operator_error(self, text: str) -> None:
        """emcOperatorError() -> EMC_OPERATOR_ERROR (emctaskmain.cc:201-230).

        Motion's reportError() and rtapi_print_msg(RTAPI_MSG_ERR) land here
        too: motion.c:193-212 puts both in the emcmot error buffer and
        taskintf.cc:2024-2028 forwards that with emcOperatorError(0, ...).
        """
        self.errors.append((_C["OPERATOR_ERROR"], text))

    # -- the task planner ----------------------------------------------------

    def submit(self, msg: str, issue: Callable[[], None]) -> None:
        """emcTaskPlan() gating, then emcTaskIssueCommand() (`issue`)."""
        try:
            self._plan(msg)
            issue()
            self._last_status = _C["RCS_DONE"]
        except _Silent:
            self._last_status = _C["RCS_DONE"]
        except MotionRejected as exc:
            self.operator_error(str(exc))
            self._last_status = _C["RCS_ERROR" if exc.sets_joint_error else "RCS_DONE"]
        except Rejected as exc:
            if str(exc):
                self.operator_error(str(exc))
            # emctaskmain.cc:3546-3551: a planner error marks the status ERROR.
            self._last_status = _C["RCS_ERROR"]

    def _plan(self, msg: str) -> None:
        state = self.task_state
        if state != _C["STATE_ON"]:
            if msg not in _NOT_ON_OK:
                raise Rejected(
                    "command ({}) cannot be executed until the machine is out of "
                    "E-stop and turned on".format(msg)        # emctaskmain.cc:872-876
                )
            return
        mode = self.task_mode
        if mode == _C["MODE_MANUAL"]:
            if msg in _MANUAL_SILENT:
                raise _Silent()
            if msg not in _MANUAL_OK:
                raise Rejected(
                    "can't do that ({}:{}) in manual mode".format(msg, _MSG[msg])
                )                                              # emctaskmain.cc:1003-1006
            return
        if mode == _C["MODE_AUTO"]:
            if self.interp_state == _C["INTERP_IDLE"]:
                if msg in _AUTO_IDLE_OK or msg in _ALLOW_WHILE_IDLE:
                    return
                raise Rejected(
                    "can't do that ({}) in auto mode with the interpreter idle".format(msg)
                )                                              # emctaskmain.cc:1104-1112
            if msg in _AUTO_READING_OK:
                return
            raise Rejected(
                "can't do that ({}) in auto mode with the interpreter reading".format(msg)
            )                                                  # emctaskmain.cc:1178-1181
        # MDI
        if msg in _MDI_OK or msg in _ALLOW_WHILE_IDLE:
            return
        raise Rejected(
            "can't do that ({}:{}) in MDI mode".format(msg, _MSG[msg])
        )                                                      # emctaskmain.cc:1453-1461

    # -- emcTaskIssueCommand bodies -----------------------------------------

    def set_traj_mode(self, mode: int) -> None:
        """emcTrajSetMode() (taskintf.cc:1066-1083) -> motion, then the task
        mode follows determineMode() (emctask.cc:393-410, called at :692).

        A request to leave teleop first aborts the teleop jogs (EMCMOT_FREE /
        EMCMOT_COORD, axis_jog_abort_all, command.c:556-580); motion then
        switches once in position (control.c:961-972), which in the fake is
        at once. Not in position, motion drops a teleop request (control.c:
        927-958) and defers leaving coord; the fake leaves the mode as it is
        and never applies a deferred change later."""
        if mode == _C["TRAJ_MODE_TELEOP"]:
            # switch_to_teleop_mode(), motion.c:163-180
            if self.kinematics_type != _C["KINEMATICS_IDENTITY"] and not self.all_homed():
                raise MotionRejected("all joints must be homed before going into teleop mode")
        elif self.motion_mode == _C["TRAJ_MODE_TELEOP"]:
            self.jogging_active = False           # the teleop jog is aborted
        if not self.inpos:
            return
        self.motion_mode = mode
        if mode in (_C["TRAJ_MODE_FREE"], _C["TRAJ_MODE_TELEOP"]):
            self.task_mode = _C["MODE_MANUAL"]
        else:
            self.task_mode = self.mdi_or_auto

    def issue_set_mode(self, mode: int) -> None:
        """emctaskmain.cc:2125-2171 then emcTaskSetMode(), emctask.cc:265-307."""
        if self.jogging_active:
            # emctask.cc:269-272: reported, but returns 0 - not a task error.
            self.operator_error("Ignoring task mode change while jogging")
            return
        if (
            self.task_mode == _C["MODE_AUTO"]
            and self.interp_state != _C["INTERP_IDLE"]
            and mode != _C["MODE_AUTO"]
        ):
            raise Rejected("Can't switch mode while mode is AUTO and interpreter is not IDLE")
        if mode == _C["MODE_MANUAL"]:
            if self.task_mode != _C["MODE_MANUAL"]:
                self.interp_state = _C["INTERP_IDLE"]           # emctaskmain.cc:2159
            self.mdi_or_auto = _C["MODE_AUTO"]
            self.set_traj_mode(
                _C["TRAJ_MODE_TELEOP"] if self.all_homed() else _C["TRAJ_MODE_FREE"]
            )
        else:
            self.mdi_or_auto = mode
            self.set_traj_mode(_C["TRAJ_MODE_COORD"])

    def issue_set_state(self, state: int) -> None:
        """emcTaskSetState(), emctask.cc:309-370, plus the estop sync in the
        main loop (emctaskmain.cc:3413-3416).

        ESTOP clears user-enable-out (emcAuxEstopOn -> ioControl.cc:1086);
        only where that pin feeds the latch does aux.estop follow. On the rig
        it does not, so LinuxCNC lands in ESTOP_RESET (motion disabled,
        determineState emctask.cc:419-430), not ESTOP."""
        if state == _C["STATE_ESTOP"]:
            self.user_enable_out = 0              # emcAuxEstopOn
            self.run_latch()
            self.aux_estop = 0 if self.latch_ok else 1
            self.traj_enabled = False
            self.interp_state = _C["INTERP_IDLE"]
            self.paused = False
            self._estop_countdown = 0
        elif state == _C["STATE_ESTOP_RESET"]:
            # emcAuxEstopOff: user-enable-out and a rising edge on
            # user-request-enable (ioControl.cc:1090-1096) reset the latch;
            # aux.estop then follows emc-enable-in (:523-526).
            self.user_enable_out = 1
            self.run_latch(reset_edge=True)
            if self.latch_ok and self.estop_clear_after_polls > 0:
                self._estop_countdown = self.estop_clear_after_polls
            else:
                self.aux_estop = 0 if self.latch_ok else 1
            # emcTaskAbort() in every mode (emctask.cc:340-349).
            self.interp_state = _C["INTERP_IDLE"]
            self.paused = False
        elif state == _C["STATE_ON"]:
            self.traj_enabled = True              # emcTrajEnable
            if self.estop_reasserts_on_power_on:
                self.latch_ok = False
                self.aux_estop = 1
        elif state == _C["STATE_OFF"]:
            self.traj_enabled = False
        if self.aux_estop and self.traj_enabled:
            self.traj_enabled = False             # emctaskmain.cc:3413-3416

    def issue_home(self, joint: int) -> None:
        """emcJointHome (taskintf.cc:767-776) -> EMCMOT_JOINT_HOME (command.c:1366-1388)."""
        if joint < -1 or joint >= _C["MAX_JOINTS"]:
            return                                # taskintf.cc:769-771, silently
        if not self.traj_enabled or self.motion_mode != _C["TRAJ_MODE_FREE"]:
            # motion_state is FREE only when enabled and in free mode.
            raise MotionRejected("must be in joint mode to home")   # command.c:1374-1378
        if joint == -1 and any(self.homing[: self.num_joints]):
            # base_do_home_joint(-1) clears joint 0's homed flag, then
            # do_home_all does nothing while homing is active (homing.c:
            # 553-560, :258-263): no restart, joint 0 ends unhomed.
            self.homed[0] = 0
            return
        targets = range(self.num_joints) if joint == -1 else [joint]
        for j in targets:
            if self.home_instantly:
                self.homed[j] = 1
            else:
                self.homing[j] = 1

    def issue_unhome(self, joint: int) -> None:
        """emcJointUnhome (taskintf.cc:779-789) -> EMCMOT_JOINT_UNHOME
        (command.c:1393-1406) -> set_unhomed (homing.c:570-605, :283-323)."""
        if joint < -2 or joint >= _C["MAX_JOINTS"]:
            return                                # taskintf.cc:781-783, silently
        if self.traj_enabled and self.motion_mode != _C["TRAJ_MODE_FREE"]:
            # motion_state is neither FREE nor DISABLED (command.c:1399-1402).
            raise MotionRejected("must be in joint mode or disabled to unhome")
        targets = range(self.num_joints) if joint < 0 else [joint]
        # All or none, and a refusal is only an rtapi message, never the error
        # channel (homing.c:294-314, :589-599).
        if any(self.homing[j] for j in targets) or not self.inpos:
            return
        if joint == -2:
            return                                # no VOLATILE_HOME modelled
        for j in targets:
            self.homed[j] = 0

    def issue_jog_incr(self, jjogmode: int, nr: int, vel: float, incr: float) -> None:
        """emcJogIncr (taskintf.cc:818-845) -> EMCMOT_JOG_INCR (command.c:418-907)."""
        if jjogmode:
            if nr < 0 or nr >= _C["MAX_JOINTS"]:
                return                            # taskintf.cc:820-821, silently
            joint_num, axis = nr, -1
        else:
            if nr < 0 or nr >= _C["MAX_AXIS"]:
                return                            # taskintf.cc:829, silently
            joint_num, axis = -1, nr
        teleop = self.motion_mode == _C["TRAJ_MODE_TELEOP"]
        # command.c:418-466. Out of teleop, an axis jog has joint_num -1, and
        # the joint range check (:436-443) returns before the "Mode is NOT
        # TELEOP" text set at :430-433 is ever printed - so that text never
        # reaches the operator on this path, and the range message has no
        # trailing newline.
        if not teleop and (joint_num < 0 or joint_num >= self.num_joints):
            raise MotionRejected(
                "Joint jog requested for undefined joint number={} (min=0,max={})".format(
                    joint_num, self.num_joints - 1
                )
            )
        if teleop and axis < 0:
            # Printed with RTAPI_MSG_ERR as "JOG_INCR %s\n" (:459).
            raise MotionRejected("JOG_INCR Mode is TELEOP, cannot jog joint\n")
        if not self.traj_enabled:                 # command.c:843-846
            raise MotionRejected("Can't jog joint when not enabled.", sets_joint_error=True)
        if any(self.homing):                      # command.c:853-857 (854)
            raise MotionRejected("Can't jog any joint while homing.", sets_joint_error=True)
        # The increment is a magnitude; the velocity's sign picks the
        # direction (free: command.c:873-877, teleop: src/emc/motion/axis.c:283-287).
        step = incr if vel > 0.0 else -incr
        if teleop:
            self.axis_pos[axis] += step
            for j, letter in enumerate(self.coordinates):
                if AXIS_LETTERS.index(letter) == axis:
                    self.joint_pos[j] += step
        else:
            self.joint_pos[joint_num] += step
            letter = self.coordinates[joint_num]
            self.axis_pos[AXIS_LETTERS.index(letter)] += step

    def issue_execute(self, command: str) -> None:
        """EMC_TASK_PLAN_EXECUTE, emctaskmain.cc:2205-2218."""
        if not self.all_homed() and not self.no_force_homing:
            raise Rejected("Can't issue MDI command when not homed")
        if self.task_mode != _C["MODE_MDI"]:
            raise Rejected("Must be in MDI mode to issue MDI command")
        self.mdi_executed.append(command)

    def issue_run(self, line: int) -> None:
        """EMC_TASK_PLAN_RUN, emctaskmain.cc:2294-2310. Running with no file
        open is not modelled (the interpreter would decide)."""
        if not self.all_homed() and not self.no_force_homing:
            raise Rejected("Can't run a program when not homed")
        self.interp_state = _C["INTERP_READING"]
        self.paused = False

    def issue_open(self, path: str) -> None:
        """EMC_TASK_PLAN_OPEN, emctaskmain.cc:2190-2200."""
        if not self.openable(path):
            raise Rejected("can't open {}".format(path))
        self.file = path

    def issue_abort(self) -> None:
        """emcTaskAbort(): the interpreter goes idle; the file stays loaded."""
        self.interp_state = _C["INTERP_IDLE"]
        self.paused = False

    def issue_teleop(self, enable: int) -> None:
        """EMC_TRAJ_SET_TELEOP_ENABLE, emctaskmain.cc:1952-1957."""
        self.set_traj_mode(_C["TRAJ_MODE_TELEOP"] if enable else _C["TRAJ_MODE_FREE"])

    def issue_scale(self, scale: float) -> None:
        """EMCMOT_FEED_SCALE clamps negatives to 0 and nothing else (command.c:1214-1221)."""
        self.feedrate = scale if scale >= 0.0 else 0.0


class _Silent(Exception):
    """Internal: accepted by task and dropped without a message."""


# ---------------------------------------------------------------------------
# linuxcnc module
# ---------------------------------------------------------------------------

#: The stat attributes the fake provides. Names: Stat_members
#: (emcmodule.cc:320-418) and Stat_getsetlist (emcmodule.cc:737-770).
STAT_ATTRIBUTES = (
    "task_state", "task_mode", "motion_mode", "interp_state", "kinematics_type",
    "joints", "homed", "joint", "estop", "enabled", "inpos", "actual_position",
    "g5x_offset", "g5x_index", "feedrate", "file", "paused",
)

#: Keys of each stat.joint[n] dict, in Stat_joint_one order (emcmodule.cc:583-607).
JOINT_DICT_KEYS = (
    "jointType", "units", "backlash", "min_position_limit", "max_position_limit",
    "max_ferror", "min_ferror", "ferror_current", "ferror_highmark", "output",
    "input", "velocity", "inpos", "homing", "homed", "fault", "enabled",
    "min_soft_limit", "max_soft_limit", "min_hard_limit", "max_hard_limit",
    "override_limits",
)

#: Command methods the fake implements and their PyArg_ParseTuple formats, as
#: (format, first-ParseTuple line in emcmodule.cc). None = METH_NOARGS.
COMMAND_FORMATS: Dict[str, Optional[str]] = {
    "mode": "i",                                   # emcmodule.cc:873
    "state": "i",                                  # emcmodule.cc:997
    "feedrate": "d",                               # emcmodule.cc:905
    "home": "i",                                   # emcmodule.cc:1123
    "unhome": "i",                                 # emcmodule.cc:1131
    "jog": "iii|dd",                               # emcmodule.cc:1145
    "mdi": "s#",                                   # emcmodule.cc:984
    "program_open": "s#",                          # emcmodule.cc:1210
    "auto": "ii",                                  # emcmodule.cc:1230 (then "i", :1234)
    "teleop_enable": "i",                          # emcmodule.cc:1273
    "wait_complete": "|d:emc.command.wait_complete",  # emcmodule.cc:1385
    "abort": None,                                 # emcmodule.cc:1465 METH_NOARGS
    "task_plan_synch": None,                       # emcmodule.cc:1466 METH_NOARGS
}


def build_linuxcnc_module(machine: FakeMachine) -> types.ModuleType:
    """A fresh fake `linuxcnc` module whose channels all talk to `machine`.

    A new module per test, so nothing is shared between tests.
    """
    mod = types.ModuleType("linuxcnc")
    mod.__doc__ = "Fake linuxcnc (ui/tests/fake_linuxcnc.py)"
    for name, value in CONSTANTS.items():
        setattr(mod, name, value)

    # emcmodule.cc:2418: PyErr_NewException("linuxcnc.error", PyExc_RuntimeError)
    error = type("error", (RuntimeError,), {"__module__": "linuxcnc"})
    mod.error = error
    m = machine
    # poll()'s function statics `initialized` and `mmap_available`
    # (emcmodule.cc:286, :296): one per process, so one per fake module.
    process = {"initialized": False, "mmap_available": True}

    class stat:  # noqa: N801 - the real type is lower-case
        """linuxcnc.stat. Attributes are a copy taken at poll()."""

        def __init__(self) -> None:
            # Before the first poll the copy is all zeros: Stat_init does not
            # read status (emcmodule.cc:258-271), only poll() copies it in
            # (emcmodule.cc:306-310), and tp_new is PyType_GenericNew, which
            # zero-fills.
            self._snap: Dict[str, Any] = {
                "task_state": 0, "task_mode": 0, "motion_mode": 0,
                "interp_state": 0, "kinematics_type": 0, "joints": 0,
                "homed": (0,) * _C["MAX_JOINTS"],
                "joint": tuple(
                    dict.fromkeys(JOINT_DICT_KEYS, 0) for _ in range(_C["MAX_JOINTS"])
                ),
                "estop": 0, "enabled": False, "inpos": False,
                "actual_position": (0.0,) * 9, "g5x_offset": (0.0,) * 9,
                "g5x_index": 0, "feedrate": 0.0, "file": "", "paused": False,
            }

        def poll(self, *args: Any) -> None:
            if args:   # METH_NOARGS (emcmodule.cc:316)
                raise TypeError("poll() takes no arguments ({} given)".format(len(args)))
            m.stat_polls += 1
            # emcmodule.cc:296-303: once the first poll found no tool mmap,
            # every poll returns NULL with no exception set.
            if not process["mmap_available"]:
                raise SystemError(
                    "<method 'poll' of 'linuxcnc.stat' objects> returned NULL "
                    "without setting an exception"
                )
            if not process["initialized"]:
                process["initialized"] = True
                if not m.tool_mmap_present:
                    process["mmap_available"] = False
            if not m.channels_valid:
                # check_stat, emcmodule.cc:277-283. The real error_type number
                # is not modelled.
                raise error("emcStatusBuffer invalid err=0")
            m.tick()
            joints = []
            for j in range(_C["MAX_JOINTS"]):
                d = dict.fromkeys(JOINT_DICT_KEYS, 0)
                d.update(
                    homed=m.homed[j], homing=m.homing[j],
                    enabled=int(m.traj_enabled and j < m.num_joints),
                    input=m.joint_pos[j], output=m.joint_pos[j],
                )
                joints.append(d)
            self._snap = {
                "task_state": m.task_state,
                "task_mode": m.task_mode,
                "motion_mode": m.motion_mode,
                "interp_state": m.interp_state,
                "kinematics_type": m.kinematics_type,
                "joints": m.num_joints,
                "homed": tuple(m.homed),          # 16 entries, emcmodule.cc:541-547
                "joint": tuple(joints),           # 16 dicts, emcmodule.cc:610-616
                "estop": m.aux_estop,
                "enabled": m.traj_enabled,
                "inpos": m.inpos_flag,
                "actual_position": tuple(m.axis_pos),
                "g5x_offset": tuple(m.g5x_offset),
                "g5x_index": m.g5x_index,
                "feedrate": m.feedrate,
                "file": m.file,
                "paused": m.paused,
            }

        def __getattr__(self, name: str) -> Any:
            snap = self.__dict__.get("_snap", {})
            if name in snap:
                return snap[name]
            raise AttributeError("'linuxcnc.stat' object has no attribute {!r}".format(name))

    class command:  # noqa: N801
        """linuxcnc.command. Each method validates like its C counterpart.

        Every instance is its own NML channel pair (Command_init,
        emcmodule.cc:817-838); the fake records which instance sent what."""

        def __init__(self) -> None:
            m.channels.append(self)

        def _log(self, name: str, values: Sequence[Any]) -> None:
            m.log.append((name, tuple(values)))
            m.log_channel.append(m.channels.index(self))

        def mode(self, *args: Any) -> None:
            (value,) = parse_args(COMMAND_FORMATS["mode"], args)
            if value not in (_C["MODE_MDI"], _C["MODE_MANUAL"], _C["MODE_AUTO"]):
                raise ValueError("Mode should be MODE_MDI, MODE_MANUAL, or MODE_AUTO")
            self._log("mode", (value,))
            m.submit("EMC_TASK_SET_MODE", lambda: m.issue_set_mode(value))

        def state(self, *args: Any) -> None:
            (value,) = parse_args(COMMAND_FORMATS["state"], args)
            if value not in (
                _C["STATE_ESTOP"], _C["STATE_ESTOP_RESET"], _C["STATE_ON"], _C["STATE_OFF"]
            ):
                raise ValueError(
                    "Machine state should be STATE_ESTOP, STATE_ESTOP_RESET, "
                    "STATE_ON, or STATE_OFF"
                )
            self._log("state", (value,))
            m.submit("EMC_TASK_SET_STATE", lambda: m.issue_set_state(value))

        def feedrate(self, *args: Any) -> None:
            (scale,) = parse_args(COMMAND_FORMATS["feedrate"], args)
            self._log("feedrate", (scale,))
            m.submit("EMC_TRAJ_SET_SCALE", lambda: m.issue_scale(scale))

        def home(self, *args: Any) -> None:
            (joint,) = parse_args(COMMAND_FORMATS["home"], args)
            self._log("home", (joint,))
            m.submit("EMC_JOINT_HOME", lambda: m.issue_home(joint))

        def unhome(self, *args: Any) -> None:
            (joint,) = parse_args(COMMAND_FORMATS["unhome"], args)
            self._log("unhome", (joint,))
            m.submit("EMC_JOINT_UNHOME", lambda: m.issue_unhome(joint))

        def jog(self, *args: Any) -> None:
            values = parse_args(COMMAND_FORMATS["jog"], args)
            fn, jjogmode, ja = values[0], values[1], values[2]
            # emcmodule.cc:1149-1188: exact argument count per JOG_* kind.
            expected = {_C["JOG_STOP"]: (3, "JOG_STOP"),
                        _C["JOG_CONTINUOUS"]: (4, "JOG_CONTINUOUS"),
                        _C["JOG_INCREMENT"]: (5, "JOG_INCREMENT")}
            if fn not in expected:
                raise TypeError("jog() first argument must be JOG_xxx")
            count, label = expected[fn]
            if len(args) != count:
                raise TypeError(
                    "jog({}, ...) takes {} arguments ({} given)".format(label, count, len(args))
                )
            self._log("jog", values)
            if fn == _C["JOG_INCREMENT"]:
                vel, incr = values[3], values[4]
                m.submit("EMC_JOG_INCR", lambda: m.issue_jog_incr(jjogmode, ja, vel, incr))
            elif fn == _C["JOG_CONTINUOUS"]:
                m.submit("EMC_JOG_CONT", lambda: None)     # not modelled beyond gating
            else:
                m.submit("EMC_JOG_STOP", lambda: None)

        def mdi(self, *args: Any) -> None:
            (cmd,) = parse_args(COMMAND_FORMATS["mdi"], args)
            if len(cmd) > LINE_LIMIT:                       # emcmodule.cc:985-988
                raise ValueError("MDI commands limited to {} characters".format(LINE_LIMIT))
            self._log("mdi", (cmd,))
            m.submit("EMC_TASK_PLAN_EXECUTE", lambda: m.issue_execute(cmd))

        def program_open(self, *args: Any) -> None:
            # emcmodule.cc:1203-1204: PLAN_CLOSE is sent BEFORE the argument
            # is parsed, so even a bad call closes the open program.
            m.submit("EMC_TASK_PLAN_CLOSE", lambda: None)
            (path,) = parse_args(COMMAND_FORMATS["program_open"], args)
            if len(path) > LINE_LIMIT:                      # emcmodule.cc:1211-1214
                raise ValueError("File name limited to {} characters".format(LINE_LIMIT))
            self._log("program_open", (path,))
            m.submit("EMC_TASK_PLAN_OPEN", lambda: m.issue_open(path))

        def auto(self, *args: Any) -> None:
            # emcmodule.cc:1230-1256: try "ii" with fn == AUTO_RUN, else "i".
            try:
                fn, line = parse_args("ii", args)
                if fn == _C["AUTO_RUN"]:
                    self._log("auto", (fn, line))
                    m.submit("EMC_TASK_PLAN_RUN", lambda: m.issue_run(line))
                    return
            except TypeError:
                pass
            (fn,) = parse_args("i", args)
            if fn not in (_C["AUTO_PAUSE"], _C["AUTO_RESUME"], _C["AUTO_STEP"],
                          _C["AUTO_REVERSE"], _C["AUTO_FORWARD"]):
                raise error("Unexpected argument '{}' to command.auto".format(fn))
            self._log("auto", (fn,))   # pause/resume/step: logged, not modelled

        def teleop_enable(self, *args: Any) -> None:
            (enable,) = parse_args(COMMAND_FORMATS["teleop_enable"], args)
            self._log("teleop_enable", (enable,))
            m.submit("EMC_TRAJ_SET_TELEOP_ENABLE", lambda: m.issue_teleop(enable))

        def abort(self, *args: Any) -> None:
            if args:
                raise TypeError("abort() takes no arguments ({} given)".format(len(args)))
            self._log("abort", ())
            m.submit("EMC_TASK_ABORT", m.issue_abort)

        def task_plan_synch(self, *args: Any) -> None:
            if args:
                raise TypeError(
                    "task_plan_synch() takes no arguments ({} given)".format(len(args))
                )
            self._log("task_plan_synch", ())
            m.submit("EMC_TASK_PLAN_SYNCH", lambda: None)

        def wait_complete(self, *args: Any) -> int:
            values = parse_args(COMMAND_FORMATS["wait_complete"], args)
            self._log("wait_complete", values)
            if m.wait_gate is not None:
                m.wait_entered.set()
                m.wait_gate.wait(GATE_LIMIT_S)
            # emcWaitCommandComplete (emcmodule.cc:212-231): the task's RCS
            # status for the last command, or -1 when it did not finish.
            return m.wait_status()

    class error_channel:  # noqa: N801
        """linuxcnc.error_channel. poll() -> None or (kind, text) (emcmodule.cc:1562-1597)."""

        def poll(self, *args: Any) -> Optional[Tuple[int, str]]:
            if args:
                raise TypeError("poll() takes no arguments ({} given)".format(len(args)))
            if not (m.channels_valid and m.error_channel_valid):
                raise error("Error buffer invalid")       # emcmodule.cc:1562-1565
            if not m.errors:
                return None
            return m.errors.popleft()

    mod.stat = stat
    mod.command = command
    mod.error_channel = error_channel
    return mod


# ---------------------------------------------------------------------------
# hal module
# ---------------------------------------------------------------------------


class FakeHal:
    """State behind the fake `hal` module: pin values and live components."""

    def __init__(self, pins: Optional[Dict[str, Any]] = None):
        self.pins: Dict[str, Any] = dict(pins or {})
        #: Components created by THIS process. Only these set hal_shmem_base.
        self.components: List[str] = []
        #: Components other processes own: they make a name a duplicate but do
        #: not let this process call get_value.
        self.foreign_components: List[str] = []
        self.ready: List[str] = []
        #: signal name -> the pins on it, as test-rig.hal nets them.
        self.signals: Dict[str, List[str]] = {}
        #: names in `pins` that are parameters (halcmd setp works on them).
        self.params: set = set()


class FakeHalcmd:
    """Stands in for `halcmd`, as LinuxCncAdapter runs it (adapter._run_halcmd):
    called with the argument list, returns (ok, output). Output and exit
    status as the real halcmd 2.9.9 printed them in sim/ on 2026-10-20.

    Models the three forms the adapter uses, against a FakeHal:
      -s show pin NAME    "OWNER TYPE DIR VALUE NAME <=> SIGNAL", nothing for an
                          unknown pin (both exit 0)
      sets SIGNAL VALUE   every pin on the signal takes the value
      setp NAME VALUE     a parameter or an unconnected pin; refused for a pin
                          on a signal, as the real halcmd refuses it
    `on_change` runs after a successful sets/setp, so a test can couple the
    estop latch's fault input to the watchdog pin as the HAL net does.
    """

    def __init__(self, hal: FakeHal, on_change: Optional[Callable[[], None]] = None):
        self.hal = hal
        self.on_change = on_change
        self.calls: List[List[str]] = []

    @staticmethod
    def _value(text: str) -> Any:
        low = text.lower()
        if low in ("1", "true"):
            return True
        if low in ("0", "false"):
            return False
        return float(text) if "." in text else int(text)

    def signal_of(self, pin: str) -> Optional[str]:
        for sig, pins in self.hal.signals.items():
            if pin in pins:
                return sig
        return None

    def __call__(self, args: List[str]) -> Tuple[bool, str]:
        self.calls.append(list(args))
        if args[:3] == ["-s", "show", "pin"] and len(args) == 4:
            name = args[3]
            if name not in self.hal.pins or name in self.hal.params:
                return True, ""
            sig = self.signal_of(name)
            arrow = " <=> {}".format(sig) if sig else ""
            owner = name.rsplit(".", 2)[0]
            return True, "{} bit   I/O {} {}{}\n\n".format(
                owner, "TRUE" if self.hal.pins[name] else "FALSE", name, arrow)
        if args[0] == "sets" and len(args) == 3:
            sig, value = args[1], self._value(args[2])
            if sig not in self.hal.signals:
                return False, "<commandline>:0: signal '{}' not found\n".format(sig)
            for pin in self.hal.signals[sig]:
                self.hal.pins[pin] = value
        elif args[0] == "setp" and len(args) == 3:
            name, value = args[1], self._value(args[2])
            if name not in self.hal.pins:
                return False, "<commandline>:0: pin or parameter '{}' not found\n".format(name)
            if name not in self.hal.params and self.signal_of(name):
                return False, "<commandline>:0: pin '{}' is connected to a signal\n".format(name)
            self.hal.pins[name] = value
        else:
            return False, "fake halcmd: unmodelled arguments {}\n".format(args)
        if self.on_change is not None:
            self.on_change()
        return True, ""


def build_hal_module(state: FakeHal) -> types.ModuleType:
    """A fresh fake `hal` module (lib/python/hal.py re-exports _hal)."""
    mod = types.ModuleType("hal")
    mod.__doc__ = "Fake hal (ui/tests/fake_linuxcnc.py)"
    # halmodule.cc:1776: PyErr_NewException("hal.error", NULL, NULL) - base Exception.
    error = type("error", (Exception,), {"__module__": "hal"})
    mod.error = error

    class component:  # noqa: N801
        """hal.component(name, prefix=None) - halmodule.cc:181-208."""

        def __init__(self, *args: Any) -> None:
            name = parse_args("s|s:hal.component", args)[0]
            if name in state.components or name in state.foreign_components:
                # hal_init(): duplicate name -> -EINVAL (hal_lib.c:243-250),
                # raised as hal.error(strerror(EINVAL)) (halmodule.cc:176-178).
                raise error("Invalid argument")
            state.components.append(name)
            self._name = name
            self._live = True

        def ready(self) -> None:
            if not self._live:   # EXCEPTION_IF_NOT_LIVE, halmodule.cc:31-36
                raise RuntimeError("Invalid operation on closed HAL component")
            state.ready.append(self._name)

        def exit(self) -> None:
            if self._live:
                state.components.remove(self._name)
                self._live = False

    def get_value(*args: Any) -> Any:
        """halmodule.cc:1001-1081."""
        (name,) = parse_args("s", args)
        if not state.components:
            # hal_shmem_base is set only by hal_init() (hal_lib.c:217) and
            # cleared when the last component exits (hal_lib.c:350-360).
            raise RuntimeError("Cannot call before creating component")
        if name not in state.pins:
            # The message really says "set" on the get path (halmodule.cc:1078-1079).
            raise RuntimeError("Can't set value: pin / param {} not found".format(name))
        return state.pins[name]

    mod.component = component
    mod.get_value = get_value
    return mod


# ---------------------------------------------------------------------------
# Installing the fakes
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def installed(
    machine: Optional[FakeMachine] = None,
    hal_state: Optional[FakeHal] = None,
    hal_available: bool = True,
) -> Iterator[Tuple[types.ModuleType, Optional[types.ModuleType], FakeMachine, FakeHal]]:
    """Put fake `linuxcnc` and `hal` into sys.modules; ALWAYS restore after.

    hal_available=False installs None for `hal`, which makes `import hal`
    raise ImportError - a machine without the HAL Python module.
    """
    machine = machine if machine is not None else FakeMachine()
    hal_state = hal_state if hal_state is not None else FakeHal()
    lc = build_linuxcnc_module(machine)
    hl = build_hal_module(hal_state) if hal_available else None
    missing = object()
    saved = {name: sys.modules.get(name, missing) for name in ("linuxcnc", "hal")}
    sys.modules["linuxcnc"] = lc
    sys.modules["hal"] = hl  # type: ignore[assignment]
    try:
        yield lc, hl, machine, hal_state
    finally:
        for name, previous in saved.items():
            if previous is missing:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous  # type: ignore[assignment]
