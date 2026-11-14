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
fake): switch positions (a test sets the hard-limit flags; no move presses
or releases a switch), jog velocity clamping to [JOINT_n]MAX_VELOCITY
(taskintf.cc:820-826, magnitude only, the sign survives), homing switch
search (homing completes in one step unless `home_instantly=False`, and ends
with the joint at its HOME, `home_pos`, as homing.c's final move leaves it;
it unhomes its joints as it starts, a Home All runs group by group in
HOME_SEQUENCE order with the gap between groups visible to a poll, home(j)
homes joint j alone or its negative-sequence group, and a homing that leaves
every joint homed switches motion to teleop - FakeMachine.issue_home,
advance_homing), the interpreter (an opened program "runs" until
aborted; pause, resume and step change only the flags task and motion
set - FakeMachine.issue_pause / issue_resume / issue_step), spindle,
coolant, tool changes, volatile homing on estop, real time. Commands complete synchronously, and so does iocontrol (no echo
latency on state()).

The soft limits only when a test gives them (`soft_limits`, plan WP06):
then motion's own jog checks - a teleop jog past the axis's limits dropped
without a word (axis.c:283-290), a joint jog against its jog limits, the
soft limits once homed and its position plus or minus its whole travel
before (command.c:161-175), refused with an error from beyond them
(:139-150) and dropped without one past them (:879-885). Without it every
jog goes as far as it is told: the adapter's own checks stop it first.

The hard limits, as far as plan WP05's recovery needs them: the flags
(`hard_min`, `hard_max`) are a test's to set, as the switches are the
machine's; what motion does with them is modelled - the trip on an enabled
machine (FakeMachine.trip_on_limits, at state(ON) and at every poll), the
override_limits() command masking the sides pressed at that instant
(`override_mask`), the mask clearing when a jog run under it ends (at once
here: jogs complete at once) or when motion enters coordinated mode, and
motion's own refusal of a joint jog onto a pressed switch. A jog an estop
cut is a test's to model: `overriding` set with motion disabled, and the
mask then drops at the next enable (FakeMachine.end_override_jog).

Reported but passive - a test sets them, nothing here changes them or reacts
to them: motion's velocity (`current_vel`), the program lines
(`motion_line`, `current_line`), the offsets and rotation the interpreter
would set (`g92_offset`, `tool_offset`, `rotation_xy`), the active G codes
(`gcodes`) and a commanded position apart from the actual one
(`commanded_pos`).

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
  - abort() ends a jog (`jogging_active`) and, in free mode, cancels a
    homing in progress, leaving those joints unhomed (FakeMachine.
    issue_abort; command.c:493-554, homing.c:1354-1362);
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
    "EMC_JOINT_OVERRIDE_LIMITS": 129,
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
    "EMC_TASK_PLAN_PAUSE": 510,
    "EMC_TASK_PLAN_STEP": 511,
    "EMC_TASK_PLAN_RESUME": 512,
    "EMC_TASK_PLAN_CLOSE": 514,
    "EMC_TASK_PLAN_SYNCH": 516,
}

#: sizeof(EMC_TASK_PLAN_EXECUTE::command) - 1 and sizeof(...OPEN::file) - 1:
#: both are char[LINELEN] (emc_nml.hh:1309,1345), LINELEN = 255 (linuxcnc.h:18).
LINE_LIMIT = 254

#: ACTIVE_G_CODES (interp_base.hh:28): the length of stat.gcodes.
ACTIVE_G_CODES = 17

#: [JOINT_n]HOME of configs/test-rig/test-rig.ini by axis letter, where a
#: homing leaves each joint (FakeMachine.end_homing): the rig's 5 / 5 / -5,
#: inside its soft limits, as sim/ shows after Home All (test_linuxcnc_adapter
#: ties these to the INI).
RIG_HOME: Dict[str, float] = {"X": 5.0, "Y": 5.0, "Z": -5.0}

#: [JOINT_n]HOME_SEQUENCE of configs/test-rig/test-rig.ini by axis letter:
#: Z first, then Y, then X (FakeMachine.issue_home groups a Home All by it;
#: test_linuxcnc_adapter ties these to the INI).
RIG_HOME_SEQUENCE: Dict[str, int] = {"Z": 0, "Y": 1, "X": 2}

#: stat.gcodes as LinuxCNC 2.9.9 reported it right after startup in sim/
#: with the rig's INI (2026-11-13): slot by slot as write_g_codes fills them
#: (interp_write.cc:73-140) - sequence number, G80, no group-0 code, G17,
#: G40, G21, G90, G94, G54, G49, G99, G64, unused, G97, G91.1, G8, no G92.
SIM_STARTUP_GCODES: Tuple[int, ...] = (
    0, 800, -1, 170, 400, 210, 900, 940, 540, 490, 990, 640, -1, 970, 911, 80, -1,
)

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
#: OFF / ESTOP / ESTOP_RESET: "one case where we need to be in manual
#: mode" - issued in MANUAL, in any other mode accepted and dropped with no
#: message (retval 0, emctaskmain.cc:846-852).
_NOT_ON_MANUAL_ONLY = {"EMC_JOINT_OVERRIDE_LIMITS"}
#: ON + MANUAL, emctaskmain.cc:893-1011 (PLAN_EXECUTE: 978-984; the limit
#: override: 917; PLAN_PAUSE and PLAN_RESUME: 947, 950 - accepted here too,
#: and issued: a pause in MANUAL pauses motion and sets the interpreter
#: PAUSED like anywhere else). AUTO and MDI do not list the override:
#: refused there. PLAN_STEP is not listed: refused.
_MANUAL_OK = {
    "EMC_JOINT_HOME", "EMC_JOINT_UNHOME", "EMC_JOG_CONT", "EMC_JOG_INCR", "EMC_JOG_STOP",
    "EMC_JOINT_OVERRIDE_LIMITS",
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE",
    "EMC_TASK_PLAN_SYNCH", "EMC_TRAJ_SET_TELEOP_ENABLE", "EMC_TASK_PLAN_EXECUTE",
    "EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME",
}
#: ON + MANUAL + PLAN_RUN: accepted and dropped without a message unless an
#: external offset is applied (emctaskmain.cc:995-1000).
_MANUAL_SILENT = {"EMC_TASK_PLAN_RUN"}
#: ON + AUTO + interpreter IDLE, emctaskmain.cc:1015-1112 (PLAN_PAUSE and
#: PLAN_RESUME: 1063, 1066). No PLAN_SYNCH here. PLAN_STEP (1078-1091) has
#: its own body: FakeMachine.issue_step.
_AUTO_IDLE_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE",
    "EMC_TASK_PLAN_RUN", "EMC_TASK_PLAN_EXECUTE",
    "EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME", "EMC_TASK_PLAN_STEP",
}
#: ON + AUTO + interpreter READING, emctaskmain.cc:1120-1182 (PLAN_PAUSE and
#: PLAN_RESUME: 1147, 1150; PLAN_STEP: 1172-1175).
_AUTO_READING_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT",
    "EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME", "EMC_TASK_PLAN_STEP",
}
#: ON + AUTO + interpreter PAUSED, emctaskmain.cc:1192-1270 (PLAN_EXECUTE,
#: PLAN_PAUSE, PLAN_RESUME: 1235-1239; PLAN_STEP: 1251-1261).
_AUTO_PAUSED_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_EXECUTE",
    "EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME", "EMC_TASK_PLAN_STEP",
}
#: ON + AUTO + interpreter WAITING (it ran to the end of what it reads),
#: emctaskmain.cc:1276-1341 (PLAN_EXECUTE, PLAN_PAUSE, PLAN_RESUME:
#: 1305-1309; PLAN_STEP: 1330-1332).
_AUTO_WAITING_OK = _AUTO_PAUSED_OK
#: ON + MDI, emctaskmain.cc:1360-1468 (PLAN_PAUSE and PLAN_RESUME: 1402,
#: 1407). PLAN_STEP is not listed: refused.
_MDI_OK = {
    "EMC_TRAJ_SET_SCALE", "EMC_TASK_SET_MODE", "EMC_TASK_SET_STATE",
    "EMC_TASK_PLAN_OPEN", "EMC_TASK_PLAN_CLOSE", "EMC_TASK_PLAN_SYNCH",
    "EMC_TASK_ABORT", "EMC_TASK_PLAN_EXECUTE",
    "EMC_TASK_PLAN_PAUSE", "EMC_TASK_PLAN_RESUME",
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
        home_pos: Optional[Dict[str, float]] = None,
        soft_limits: Optional[Dict[str, Tuple[float, float]]] = None,
        home_sequence: Optional[Dict[str, int]] = None,
    ):
        #: Joint n carries coordinate letter coordinates[n] (trivkins).
        self.coordinates = coordinates.upper()
        self.num_joints = len(self.coordinates)
        self.kinematics_type = kinematics_type
        #: [TRAJ]NO_FORCE_HOMING (emctaskmain.cc:121-122, 3245-3261).
        self.no_force_homing = no_force_homing
        self.home_instantly = home_instantly
        #: axis letter -> [JOINT_n]HOME: where a joint's homing ends - its
        #: last move goes to HOME (HOME_FINAL_MOVE_START, homing.c:1250-1287);
        #: 0 for a letter not listed (inijoint.cc:162-163). Default RIG_HOME.
        self.home_pos = dict(RIG_HOME if home_pos is None else home_pos)
        #: axis letter -> [JOINT_n]HOME_SEQUENCE, which groups a Home All
        #: (issue_home); a letter not listed has 999, "not homed by Home All"
        #: (inijoint.cc:185-188, homing.c:390-396). Default RIG_HOME_SEQUENCE.
        self.home_sequence = dict(RIG_HOME_SEQUENCE if home_sequence is None
                                  else home_sequence)
        #: The groups of joints a homing sequence still has to start, in
        #: order (homing.c HOME_SEQUENCE_START_JOINTS, :435-456). Non-empty
        #: with no joint homing is the gap between two groups, where
        #: motion's homing is still active (homing_active) but no joint's
        #: flag shows it: advance_homing() starts the next group.
        self.home_pending: List[List[int]] = []
        #: axis letter -> (MIN_LIMIT, MAX_LIMIT), the soft limits a jog is
        #: checked against (issue_jog_incr), or None: not modelled, every jog
        #: goes as far as it is told. [JOINT_n] and [AXIS_<L>] alike - with
        #: trivkins and the rig's INI they are the same numbers.
        self.soft_limits = None if soft_limits is None else dict(soft_limits)
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
        #: stat.paused: motion.traj.paused (emcmodule.cc:371), set by
        #: EMCMOT_PAUSE and, again, by EMCMOT_STEP (command.c:1169-1212);
        #: cleared by EMCMOT_RESUME and by an abort (:530).
        self.paused = False
        #: task's interpResumeState: the interpreter state a PLAN_PAUSE
        #: found, which PLAN_RESUME restores (emctaskmain.cc:2312-2320,
        #: :2344-2352).
        self.interp_resume_state = _C["INTERP_IDLE"]
        #: task's `stepping` (emctaskmain.cc:432): set by PLAN_STEP, cleared
        #: by PLAN_RUN, PLAN_RESUME, an abort and leaving for MANUAL. Kept
        #: for tests to read; the fake runs no interpreter, so nothing waits
        #: on it.
        self.stepping = False
        #: emcStatus->motion.traj.queue: motions queued in the trajectory
        #: planner. Passive (a test sets it): a PLAN_STEP while paused steps
        #: motion (emcTrajStep) only when it is above zero
        #: (emctaskmain.cc:1251-1261). `motion_steps` counts those steps.
        self.traj_queue = 0
        self.motion_steps = 0
        #: Test switch: a program so short that the interpreter reads it to
        #: its end (M2) and goes idle within the PLAN_RUN send
        #: (emctaskmain.cc:2294-2310, then :524-534 and readahead_waiting
        #: :713-737; an "M2" alone in sim/, 2026-11-14): issue_run leaves the
        #: interpreter IDLE.
        self.program_ends_at_once = False
        self.file = ""
        self.feedrate = 1.0
        self.g5x_index = 1
        self.g5x_offset = [0.0] * 9
        self.homed = [0] * _C["MAX_JOINTS"]
        self.homing = [0] * _C["MAX_JOINTS"]
        self.joint_pos = [0.0] * _C["MAX_JOINTS"]
        self.axis_pos = [0.0] * _C["MAX_AXIS"]

        #: Motion's hard-limit flags per joint, stat.joint[n]
        #: ["min_hard_limit"] / ["max_hard_limit"] (emcmodule.cc:604-605):
        #: the limit-switch inputs as motion reads them every servo cycle,
        #: enabled or not (control.c:491-501), copied from the joint's flag
        #: bits (taskintf.cc:959-962). 1 = active. A test sets them (the
        #: switches); an enabled machine trips on them (trip_on_limits).
        self.hard_min = [0] * _C["MAX_JOINTS"]
        self.hard_max = [0] * _C["MAX_JOINTS"]
        #: emcmotStatus.overrideLimitMask: bit 2n is joint n's negative
        #: switch, bit 2n+1 its positive one (command.c:116-117). Every
        #: configured joint reports !!mask as "override_limits"
        #: (taskintf.cc:963). Set by override_limits()
        #: (issue_override_limits), cleared by the end of a jog run under it
        #: and by entering coordinated mode; a test may set it too.
        self.override_mask = 0
        #: emcmotInternal->overriding (motion.h:749-750): a jog has run
        #: under the mask (control.c:1263-1270 free, :1406-1410 teleop).
        #: Motion clears the mask once it is set and no jog is active
        #: (end_override_jog). Jogs end at once here, so only a test sets
        #: it: a jog an estop cut, which leaves it set - the DISABLED
        #: branch runs no end-of-jog check (control.c:1463-1477).
        self.overriding = False

        # -- passive status (see the module docstring): a test sets these ---
        #: stat.current_vel (emcmodule.cc:387). Motion computes it every
        #: servo cycle in every mode (control.c:1946-1962); moves complete at
        #: once here, so a test sets it to model one in progress.
        self.current_vel = 0.0
        #: stat.motion_line (emcmodule.cc:340): motion's id, whenever that is
        #: set (emctask.cc:705-707). stat.current_line (:341): the
        #: interpreter's line, which runs ahead of it.
        self.motion_line = 0
        self.current_line = 0
        #: stat.g92_offset, stat.tool_offset (9 components, X Y Z A B C U V
        #: W, pose(), emcmodule.cc:444-456) and stat.rotation_xy (degrees):
        #: what task copies from the interpreter's canon calls
        #: (emctaskmain.cc:1904-1908, :1889, :1893-1896).
        self.g92_offset = [0.0] * 9
        self.tool_offset = [0.0] * 9
        self.rotation_xy = 0.0
        #: stat.gcodes (emcmodule.cc:746, :508-510): the ACTIVE_G_CODES = 17
        #: modal slots (interp_base.hh:28), each a G code times ten, -1 for
        #: none (interp_write.cc:73-140). Default: what LinuxCNC 2.9.9
        #: reported at startup in sim/ with the rig's INI on 2026-11-13.
        self.gcodes: Tuple[int, ...] = SIM_STARTUP_GCODES
        #: stat.position, motion's commanded position (carte_pos_cmd,
        #: taskintf.cc:1600). None: the same as axis_pos, as at rest - moves
        #: complete at once here.
        self.commanded_pos: Optional[List[float]] = None

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
        self.end_override_jog()
        self.trip_on_limits()

    def end_override_jog(self) -> None:
        """The planners' end-of-jog check, run every servo cycle while motion
        is enabled: `overriding` set and no jog active - the free planner
        in position (control.c:1278-1283), the teleop one with no axis jog
        (:1453-1456) - clears the mask. Run before trip_on_limits at every
        poll and after state(ON): a jog an estop cut (`overriding` left
        set) drops the mask at the first servo cycle after enabling, and a
        switch it covered stops the machine again."""
        if self.traj_enabled and self.overriding and self.inpos:
            self.override_mask = 0
            self.overriding = False

    def trip_on_limits(self) -> None:
        """check_for_faults' hard-limit part (control.c:807-826), which
        motion runs every servo cycle: with motion enabled, a joint whose
        switch flag is set, not overridden (the mask's bit for that side)
        and not homing reports "joint N on limit switch error" (once per
        trip: enabling clears the joints' error flags, control.c:904-914)
        and disables motion, so LinuxCNC leaves ON (determineState,
        emctask.cc:419-430). Every tripping joint of the pass reports. Run
        at every stat.poll() (tick) - servo cycles passed - and right after
        state(ON), whose next servo cycle checks."""
        if not self.traj_enabled:
            return
        for j in range(self.num_joints):
            if self.homing[j]:
                continue
            neg = self.hard_min[j] and not self.override_mask & (1 << (2 * j))
            pos = self.hard_max[j] and not self.override_mask & (2 << (2 * j))
            if neg or pos:
                self.operator_error("joint {} on limit switch error".format(j))
                self.traj_enabled = False

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
        """Put the machine in ON + MANUAL directly, without logging commands.
        Homed: as a Home All leaves it, every joint at its HOME (end_homing)."""
        self.estop_reset_state()
        self.traj_enabled = True
        self.task_mode = _C["MODE_MANUAL"]
        if homed:
            for j in range(self.num_joints):
                self.end_homing(j)
            self.motion_mode = _C["TRAJ_MODE_TELEOP"]   # emctask.cc:277-278
        else:
            self.motion_mode = _C["TRAJ_MODE_FREE"]     # emctask.cc:279-280

    @property
    def homing_active(self) -> bool:
        """Motion's homing_active (homing.c:660-662): a joint homing, or a
        sequence between two of its groups."""
        return any(self.homing[: self.num_joints]) or bool(self.home_pending)

    def advance_homing(self) -> None:
        """One visible step of motion's homing (home_instantly=False): the
        joints homing end (HOME_FINISHED, homing.c:1343-1352) - which, with
        groups still to start, leaves the gap a poll can show (the sequence
        moves on two servo cycles later, HOME_SEQUENCE_WAIT_JOINTS
        :458-477, then HOME_SEQUENCE_START_JOINTS :435-456) - or, in that
        gap, the next group starts. A homing that leaves every joint homed
        switches motion to teleop (do_homing returns 1 then, homing.c:1400;
        control.c:261-263 -> switch_to_teleop_mode, motion.c:163-180)."""
        busy = [j for j in range(self.num_joints) if self.homing[j]]
        if busy:
            for j in busy:
                self.homing[j] = 0
                self.end_homing(j)
        elif self.home_pending:
            for j in self.home_pending.pop(0):
                self.homing[j] = 1
        else:
            return
        if not self.homing_active and self.all_homed():
            self._homing_completed()

    def finish_homing(self) -> None:
        """The homing in progress runs to its end, every group of it."""
        while self.homing_active:
            self.advance_homing()

    def _homing_completed(self) -> None:
        """do_homing() returned 1 in free mode: motion goes to teleop
        (control.c:261-263; homing.c:1400 returns 1 only on the period that
        leaves every joint homed). With identity kins switch_to_teleop_mode
        always succeeds (motion.c:163-180); task's mode stays MANUAL
        (determineMode, emctask.cc:393-410). Abort or not in free mode: no
        switch, as motion runs do_homing only in EMCMOT_MOTION_FREE."""
        if self.motion_mode != _C["TRAJ_MODE_FREE"] or not self.traj_enabled:
            return
        if self.kinematics_type != _C["KINEMATICS_IDENTITY"] and not self.all_homed():
            return
        self.motion_mode = _C["TRAJ_MODE_TELEOP"]
        self.task_mode = _C["MODE_MANUAL"]

    def home_all_groups(self) -> List[List[int]]:
        """The joints a Home All homes, grouped as homing.c starts them:
        every joint whose HOME_SEQUENCE is at most 100 (:390-396), groups
        by abs(sequence) from 0 up (:435-456; the negative sign only syncs
        the final move, :410-421), ending at the first number no joint
        has unless it is 0 (:450-455)."""
        seqs = {}
        for j, letter in enumerate(self.coordinates):
            seq = self.home_sequence.get(letter, 999)
            if seq <= 100:
                seqs.setdefault(abs(seq), []).append(j)
        groups: List[List[int]] = []
        current = 0
        while True:
            group = seqs.get(current, [])
            if not group and current != 0:
                break
            if group:
                groups.append(group)
            current += 1
        return groups

    def home_one_targets(self, joint: int) -> List[int]:
        """The joints home(joint) homes: the joint alone, or with a negative
        HOME_SEQUENCE every joint that shares its abs(sequence)
        (do_home_one_joint, homing.c:265-281)."""
        if joint >= self.num_joints:
            return [joint]
        seq = self.home_sequence.get(self.coordinates[joint], 999)
        if seq >= 0:
            return [joint]
        return [j for j, letter in enumerate(self.coordinates)
                if abs(self.home_sequence.get(letter, 999)) == abs(seq)]

    def end_homing(self, j: int) -> None:
        """Joint `j` homed: its homing's last move takes it to HOME
        (HOME_FINAL_MOVE_START sets free_tp.pos_cmd to H.home, homing.c:
        1250-1287; then HOME_FINISHED sets homed, :1343-1352). The axis
        it carries stands there too (trivkins)."""
        letter = self.coordinates[j]
        home = float(self.home_pos.get(letter, 0.0))
        self.joint_pos[j] = home
        self.axis_pos[AXIS_LETTERS.index(letter)] = home
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
            if msg in _NOT_ON_MANUAL_ONLY:
                if self.task_mode != _C["MODE_MANUAL"]:
                    raise _Silent()                       # emctaskmain.cc:848-851
                return
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
            table, word = {
                _C["INTERP_PAUSED"]: (_AUTO_PAUSED_OK, "paused"),      # emctaskmain.cc:1265-1268
                _C["INTERP_WAITING"]: (_AUTO_WAITING_OK, "waiting"),   # emctaskmain.cc:1336-1339
            }.get(self.interp_state, (_AUTO_READING_OK, "reading"))    # emctaskmain.cc:1178-1181
            if msg in table:
                return
            raise Rejected(
                "can't do that ({}) in auto mode with the interpreter {}".format(msg, word)
            )
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
        if mode == _C["TRAJ_MODE_COORD"] and self.motion_mode != mode:
            # Entering coordinated mode clears the limit override
            # (control.c:990-992).
            self.override_mask = 0
            self.overriding = False
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
                # emcTaskAbort, the interpreter idle, stepping off
                # (emctaskmain.cc:2132-2162; abort clears motion's paused,
                # command.c:530).
                self.interp_state = _C["INTERP_IDLE"]           # emctaskmain.cc:2159
                self.paused = False
                self.stepping = False
            self.mdi_or_auto = _C["MODE_AUTO"]
            self.set_traj_mode(
                _C["TRAJ_MODE_TELEOP"] if self.all_homed() else _C["TRAJ_MODE_FREE"]
            )
        else:
            self.mdi_or_auto = mode
            self.set_traj_mode(_C["TRAJ_MODE_COORD"])
            # emcTaskSetMode(AUTO or MDI) aborts after the motion mode
            # (emcTaskAbort, emctask.cc:286-296 -> :226-262): an MDI line
            # still running ends, and so does a pause.
            self.interp_state = _C["INTERP_IDLE"]
            self.paused = False
            self.stepping = False

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
            self.stepping = False
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
            self.stepping = False
        elif state == _C["STATE_ON"]:
            self.traj_enabled = True              # emcTrajEnable
            if self.estop_reasserts_on_power_on:
                self.latch_ok = False
                self.aux_estop = 1
        elif state == _C["STATE_OFF"]:
            self.traj_enabled = False
        if self.aux_estop and self.traj_enabled:
            self.traj_enabled = False             # emctaskmain.cc:3413-3416
        # Enabling keeps the override mask (control.c:896-924 clears only
        # the joints' error flags) unless a cut jog left `overriding` set
        # (end_override_jog); a switch it does not cover stops the machine
        # on the next servo cycle.
        self.end_override_jog()
        self.trip_on_limits()

    def issue_home(self, joint: int) -> None:
        """emcJointHome (taskintf.cc:767-776) -> EMCMOT_JOINT_HOME (command.c:1366-1388).

        -1 homes every joint of the sequence group by group in
        HOME_SEQUENCE order (home_all_groups), a joint number that joint
        alone - or, with a negative HOME_SEQUENCE, every joint sharing its
        abs(sequence) (do_home_one_joint, homing.c:265-281, :342-383;
        home_one_targets). A homing unhomes its joints as it starts - every
        joint of a Home All in HOME_SEQUENCE_START (homing.c:401-410), each
        joint again in HOME_START (:771-772) - so a joint homed again reads
        unhomed until it ends.

        home_instantly: the whole homing ends here, group after group.
        Otherwise the first group homes and the rest wait in home_pending;
        advance_homing() is the next step a poll can see (a group ending,
        the gap, the next group starting). A homing that leaves every joint
        homed switches motion to teleop (_homing_completed)."""
        if joint < -1 or joint >= _C["MAX_JOINTS"]:
            return                                # taskintf.cc:769-771, silently
        if not self.traj_enabled or self.motion_mode != _C["TRAJ_MODE_FREE"]:
            # motion_state is FREE only when enabled and in free mode.
            raise MotionRejected("must be in joint mode to home")   # command.c:1374-1378
        if joint == -1 and self.homing_active:
            # base_do_home_joint(-1) clears joint 0's homed flag, then
            # do_home_all does nothing while homing is active - between two
            # groups of a sequence too (homing.c:553-560, :258-263): no
            # restart, joint 0 ends unhomed.
            self.homed[0] = 0
            return
        if joint == -1:
            groups = self.home_all_groups()
            for group in groups:                  # HOME_SEQUENCE_START, :401-410
                for j in group:
                    self.homed[j] = 0
        else:
            # do_home_one_joint takes the sequence state over (homing.c:
            # 265-281): the groups a Home All still had to start never do.
            groups = [self.home_one_targets(joint)]
            self.home_pending = []
            for j in groups[0]:
                self.homed[j] = 0
        if not groups:
            return
        for j in groups[0]:
            self.homing[j] = 1
        self.home_pending = [list(g) for g in groups[1:]]
        if self.home_instantly:
            self.finish_homing()

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
        # get_homing_is_active(), command.c:853-857: between two groups of
        # a Home All too (homing_active).
        if self.homing_active:
            raise MotionRejected("Can't jog any joint while homing.", sets_joint_error=True)
        # The increment is a magnitude; the velocity's sign picks the
        # direction (free: command.c:873-877, teleop: src/emc/motion/axis.c:283-287).
        step = incr if vel > 0.0 else -incr
        if not teleop:
            # joint_jog_ok (command.c:108-152, called at :869): onto a
            # pressed switch is refused, overridden or not (:129-138) -
            # unless both of the joint's sides are overridden, when no
            # direction is known to be safe and the test is skipped
            # (:118-124). A teleop jog is not checked (axis.c:278-297).
            both = (self.override_mask >> (2 * joint_num)) & 3 == 3
            if not both and vel > 0.0 and self.hard_max[joint_num]:
                raise MotionRejected("Can't jog joint {} further past max hard limit.".format(
                    joint_num), sets_joint_error=True)
            if not both and vel < 0.0 and self.hard_min[joint_num]:
                raise MotionRejected("Can't jog joint {} further past min hard limit.".format(
                    joint_num), sets_joint_error=True)
            if self.soft_limits is not None and not self._joint_jog_in_window(
                    joint_num, vel, step, both):
                return
        elif self.soft_limits is not None:
            # axis_jog_incr: a target past the axis's limits is dropped
            # without a word, homed or not (axis.c:283-290).
            lo, hi = self.soft_limits.get(AXIS_LETTERS[axis], (-1e99, 1e99))
            target = self.axis_pos[axis] + step
            if target > hi or target < lo:
                return
        if teleop:
            self.axis_pos[axis] += step
            for j, letter in enumerate(self.coordinates):
                if AXIS_LETTERS.index(letter) == axis:
                    self.joint_pos[j] += step
        else:
            self.joint_pos[joint_num] += step
            letter = self.coordinates[joint_num]
            self.axis_pos[AXIS_LETTERS.index(letter)] += step
        # The jog is over at once here, and an override it ran under with it
        # (control.c:1263-1283 in free mode, :1404-1411 and :1452-1456 in
        # teleop). Whether the switch let go is the test's to say.
        self.override_mask = 0
        self.overriding = False

    def _joint_jog_in_window(self, joint_num: int, vel: float, step: float,
                             both: bool = False) -> bool:
        """A free-mode jog against the joint's jog limits, with soft_limits
        set: refresh_jog_limits makes them the soft limits once the joint is
        homed, else its position plus or minus its whole travel
        (command.c:161-175); joint_jog_ok refuses a jog further out from
        beyond them, with an error (:139-150), and JOG_INCR drops a target
        past them without one (:879-885). The joint's position stands for
        pos_cmd and pos_fb alike: moves complete at once here.

        `both`: both of the joint's sides overridden. joint_jog_ok then
        returns before any of its tests (:118-124), the soft-limit refusals
        included; only JOG_INCR's silent drop is left."""
        lo, hi = self.soft_limits.get(self.coordinates[joint_num], (-1e99, 1e99))
        pos = self.joint_pos[joint_num]
        if not self.homed[joint_num]:
            lo, hi = pos - (hi - lo), pos + (hi - lo)
        if not both and vel > 0.0 and pos > hi:
            raise MotionRejected("Can't jog joint {} further past max soft limit.".format(
                joint_num), sets_joint_error=True)
        if not both and vel < 0.0 and pos < lo:
            raise MotionRejected("Can't jog joint {} further past min soft limit.".format(
                joint_num), sets_joint_error=True)
        return lo <= pos + step <= hi

    def issue_override_limits(self) -> None:
        """EMC_JOINT_OVERRIDE_LIMITS (emctaskmain.cc:1778-1780,
        emcJointOverrideLimits taskintf.cc:730-740) -> EMCMOT_OVERRIDE_LIMITS
        (command.c:675-707), joint 0 as the binding always sends it
        (emcmodule.cc:1113-1119): the mask becomes exactly the sides pressed
        now, every joint's ("only override limits that are currently
        tripped", :691), and `overriding` clears (:700); the joints' error
        flags clear (not modelled)."""
        self.overriding = False
        mask = 0
        for j in range(self.num_joints):
            if self.hard_min[j]:
                mask |= 1 << (2 * j)
            if self.hard_max[j]:
                mask |= 2 << (2 * j)
        self.override_mask = mask

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
        self.interp_state = _C["INTERP_IDLE" if self.program_ends_at_once else "INTERP_READING"]
        self.paused = False
        self.stepping = False

    def end_program(self) -> None:
        """The program's end (M2/M30) as task reaches it: once motion's
        queue is empty readahead_waiting puts the interpreter IDLE
        (emctaskmain.cc:713-737) and nothing resumes motion - so a program
        stepped to its end with PLAN_STEP alone keeps motion's paused flag,
        which EMCMOT_STEP set again after each step (command.c:1200-1212).
        Seen in sim/ (LinuxCNC 2.9.9, 2026-11-14): stat.paused True,
        interp_state IDLE, exec_state DONE, until an abort (command.c:530)."""
        self.interp_state = _C["INTERP_IDLE"]

    def issue_pause(self) -> None:
        """EMC_TASK_PLAN_PAUSE, emctaskmain.cc:2312-2320: emcTrajPause
        (EMCMOT_PAUSE sets motion's paused, command.c:1169-1175), the
        interpreter state kept for the resume unless already PAUSED, the
        interpreter PAUSED. In every mode task lets it through (MANUAL
        :947, AUTO :1063 / :1147 / :1236 / :1306, MDI :1402)."""
        self.paused = True
        if self.interp_state != _C["INTERP_PAUSED"]:
            self.interp_resume_state = self.interp_state
        self.interp_state = _C["INTERP_PAUSED"]

    def issue_resume(self) -> None:
        """EMC_TASK_PLAN_RESUME, emctaskmain.cc:2344-2352: emcTrajResume
        (EMCMOT_RESUME clears paused and stepping, command.c:1191-1198), the
        interpreter back to the state the pause found, stepping off."""
        self.paused = False
        self.interp_state = self.interp_resume_state
        self.stepping = False

    def issue_step(self) -> None:
        """EMC_TASK_PLAN_STEP, which task handles in emcTaskPlan itself.

        AUTO, interpreter IDLE (emctaskmain.cc:1078-1091): the program runs
        from its start (PLAN_RUN, line 0 - not homed is refused as for a
        run) and is paused at once: motion paused, the interpreter PAUSED.
        AUTO, READING or WAITING (:1172-1175, :1330-1332): stepping on,
        nothing else. AUTO, PAUSED (:1251-1261): stepping on; with motion
        paused and motions queued, motion steps one (emcTrajStep ->
        EMCMOT_STEP, which resumes until the motion id changes and leaves
        paused set, command.c:1200-1212); otherwise the interpreter goes
        back to the state the pause found. Not listed in MANUAL or MDI:
        _plan refuses it there."""
        state = self.interp_state
        if state == _C["INTERP_IDLE"]:
            self.issue_run(0)
            self.issue_pause()
            self.interp_resume_state = _C["INTERP_READING"]
            return
        self.stepping = True
        if state == _C["INTERP_PAUSED"]:
            if self.paused and self.traj_queue > 0:
                self.motion_steps += 1
                self.paused = True
            else:
                self.interp_state = self.interp_resume_state

    def issue_open(self, path: str) -> None:
        """EMC_TASK_PLAN_OPEN, emctaskmain.cc:2190-2200."""
        if not self.openable(path):
            raise Rejected("can't open {}".format(path))
        self.file = path

    def issue_abort(self) -> None:
        """EMC_TASK_ABORT (emctaskmain.cc:2108-2121): emcTaskAbort() puts the
        interpreter idle and unpauses it (emctask.cc:226-262; the file name
        stays), and emcMotionAbort() (taskintf.cc:1798-1815) sends motion a
        JOG_ABORT per joint and an ABORT. Those end every jog - in teleop
        axis_jog_abort_all (command.c:505-506), in free mode each joint's
        planner (:509-520, :533-554) - and in free mode cancel a homing in
        progress (do_cancel_homing, :516-518, :548-550): HOME_ABORT clears
        the joint's homing and homed flags (homing.c:1354-1362). A joint
        already homed is not in the sequence any more and keeps its flag
        (HOME_FINISHED, homing.c:1343-1352; base_do_cancel_homing,
        :562-568). Moves stop at once here, not at their acceleration
        limit; current_vel stays passive.

        Motion cancels only the joints homing at that instant (the
        get_home_is_idle test, command.c:516, :548). In a Home All the
        sequence then moves on to the next joint (homing.c:458-477), which
        waits HOME_DELAY, 0.1 s, before its first move (:54, :845, :886);
        EMC_TASK_ABORT runs emcMotionAbort twice (emcTaskAbort's and the
        "KLUDGE" one, emctaskmain.cc:2110-2113), 2 x 17 motion commands at
        about one per servo cycle, so a later cancel point of the same abort
        catches that joint before it moves. In sim/ on 2026-11-13 one abort
        during Z's search, and one during Y's, ended the rig's whole
        sequence with no other joint moving. The fake's abort cancels the
        group homing and drops the groups still to start: the same end. A
        longer sequence, or a task stalled for over 0.1 s mid-abort, could
        let the next joint start; LinuxCncAdapter.stop_followup's second
        abort is the margin."""
        self.interp_state = _C["INTERP_IDLE"]
        self.paused = False                       # command.c:530
        self.stepping = False                     # emctask.cc:243-244
        self.jogging_active = False
        if self.motion_mode == _C["TRAJ_MODE_FREE"]:
            for j in range(self.num_joints):
                if self.homing[j]:
                    self.homing[j] = 0
                    self.homed[j] = 0
            # The joints still to start are in the sequence (joint_in_sequence)
            # and cancelled too (base_do_cancel_homing, homing.c:562-568).
            self.home_pending = []

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
    "position", "current_vel", "motion_line", "current_line", "g92_offset",
    "tool_offset", "rotation_xy", "gcodes",
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
    "override_limits": None,                       # emcmodule.cc:1467 METH_NOARGS
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
                "position": (0.0,) * 9, "current_vel": 0.0, "motion_line": 0,
                "current_line": 0, "g92_offset": (0.0,) * 9,
                "tool_offset": (0.0,) * 9, "rotation_xy": 0.0,
                "gcodes": (0,) * ACTIVE_G_CODES,
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
                if j < m.num_joints:
                    # Task copies only the configured joints (emcJointUpdate's
                    # loop, taskintf.cc:926); motion reads only active joints'
                    # switches (control.c:442-445). The rest stay zero.
                    d.update(
                        min_hard_limit=int(bool(m.hard_min[j])),
                        max_hard_limit=int(bool(m.hard_max[j])),
                        override_limits=int(bool(m.override_mask)),   # taskintf.cc:963
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
                "joint": tuple(joints),           # 16 dicts, emcmodule.cc:612-618
                "estop": m.aux_estop,
                "enabled": m.traj_enabled,
                "inpos": m.inpos_flag,
                "actual_position": tuple(m.axis_pos),
                "g5x_offset": tuple(m.g5x_offset),
                "g5x_index": m.g5x_index,
                "feedrate": m.feedrate,
                "file": m.file,
                "paused": m.paused,
                "position": tuple(m.axis_pos if m.commanded_pos is None else m.commanded_pos),
                "current_vel": m.current_vel,
                "motion_line": m.motion_line,
                "current_line": m.current_line,
                "g92_offset": tuple(m.g92_offset),
                "tool_offset": tuple(m.tool_offset),
                "rotation_xy": m.rotation_xy,
                "gcodes": tuple(m.gcodes),
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
            # emcmodule.cc:1210-1214: "s#" gives the UTF-8 encoding's length
            # in bytes, so a Turkish name reaches the limit sooner.
            if len(path.encode("utf-8")) > LINE_LIMIT:
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
            self._log("auto", (fn,))
            # emcmodule.cc:1236-1244: PLAN_PAUSE, PLAN_RESUME, PLAN_STEP.
            # REVERSE / FORWARD: logged, not modelled.
            msg, issue = {
                _C["AUTO_PAUSE"]: ("EMC_TASK_PLAN_PAUSE", m.issue_pause),
                _C["AUTO_RESUME"]: ("EMC_TASK_PLAN_RESUME", m.issue_resume),
                _C["AUTO_STEP"]: ("EMC_TASK_PLAN_STEP", m.issue_step),
            }.get(fn, (None, None))
            if msg is not None:
                m.submit(msg, issue)

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

        def override_limits(self, *args: Any) -> None:
            """emcmodule.cc:1113-1119: always joint 0 ("same number for
            all"), so there is no call that withdraws the override."""
            if args:
                raise TypeError("override_limits() takes no arguments ({} given)".format(len(args)))
            self._log("override_limits", ())
            m.submit("EMC_JOINT_OVERRIDE_LIMITS", m.issue_override_limits)

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
