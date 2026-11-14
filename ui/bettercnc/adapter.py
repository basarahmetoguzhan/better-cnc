"""The boundary between this UI and LinuxCNC.

Everything LinuxCNC-specific lives here. The view layer sees only MachineState,
so it can be rendered and tested with no machine and no `linuxcnc` module.

Three pieces:

  MachineAdapter      the interface the UI calls
  MockAdapter         what this iteration actually renders from
  LinuxCncAdapter     the real one, importing `linuxcnc` lazily; tested against
                      a fake built from the 2.9 bindings (ui/tests/fake_linuxcnc.py),
                      never yet run on a machine

and, most importantly:

  map_linuxcnc_state()  a PURE function turning (stat, hal_values, profile) into
                        MachineState.

That last one is separated out deliberately. Translating LinuxCNC's status
structure is the part most likely to be wrong, and keeping it pure means it is
testable against a hand-built fake stat object without a running machine.

The stat fields read, verified against the Python bindings
(src/emc/usr_intf/axis/extensions/emcmodule.cc); ui/tests/fake_linuxcnc.py
STAT_ATTRIBUTES is the checked list (test_linuxcnc_adapter.py ties both this
module and the fake to the binding):

  map_linuxcnc_state:
  stat.task_state            emcmodule.cc:329   STATE_ESTOP / STATE_ESTOP_RESET /
                                                STATE_OFF / STATE_ON
  stat.joint[n]["homed"]     emcmodule.cc:599   (getter :741)
  stat.joint[n]["homing"]    emcmodule.cc:598
  stat.joint[n]["min_hard_limit"]  emcmodule.cc:604   motion's switch flags,
  stat.joint[n]["max_hard_limit"]  emcmodule.cc:605   read every servo cycle
  stat.joint[n]["override_limits"] emcmodule.cc:606   one value for all joints
  stat.actual_position       emcmodule.cc:738   machine DRO (feedback)
  stat.position              emcmodule.cc:756   commanded (carte_pos_cmd)
  stat.g5x_offset            emcmodule.cc:753
  stat.g5x_index             emcmodule.cc:754
  stat.g92_offset            emcmodule.cc:755   work DRO (work_coordinates)
  stat.tool_offset           emcmodule.cc:765
  stat.rotation_xy           emcmodule.cc:350
  stat.feedrate              emcmodule.cc:372   motion.traj.scale
  stat.file                  emcmodule.cc:342
  stat.paused                emcmodule.cc:371   motion.traj.paused
  stat.interp_state          emcmodule.cc:337
  stat.task_mode             emcmodule.cc:328
  stat.motion_mode           emcmodule.cc:363
  stat.inpos                 emcmodule.cc:366   motion.traj.inpos
  stat.current_vel           emcmodule.cc:387   motion.traj.current_vel
  stat.motion_line           emcmodule.cc:340   the line motion executes

  LinuxCncAdapter, in addition:
  stat.kinematics_type       emcmodule.cc:381
  stat.joints                emcmodule.cc:360
  stat.homed                 emcmodule.cc:747
  stat.joint[n]["homing"], stat.file, stat.interp_state, stat.task_mode,
  stat.motion_mode, stat.inpos, stat.current_vel (as above)

stat.current_line (emcmodule.cc:341) is deliberately NOT read: the
interpreter reads ahead of motion, so it names a line motion has not reached
yet (in sim/ it stayed at a program's last motion line while motion_line
counted up to it). stat.joint is read once per snapshot: every access builds
16 dicts (Stat_joint, emcmodule.cc:612-618).

stat.estop (emcmodule.cc:418) is deliberately NOT read: see map_linuxcnc_state.

Both adapters share the MDI gate (mdi.mdi_refusal) and the "does this MDI
line move the machine" rule (mdi._needs_homing), so the mock cannot drift from
what the real adapter - and LinuxCNC - refuse. The limit-switch rules - which
switches motion sees pressed, what «Limitten kurtar» would do - are
motionrules', which the view model uses too (plan WP05); so is the jog rule,
whether a click may go and how far (motionrules.jog_refusal, plan WP06), and
the homing rule, whether a homing may start and what to ask the operator
first (motionrules.home_refusal, home_question, plan WP07).
"""

from __future__ import annotations

import abc
import datetime
import functools
import math
import os
import re
import subprocess
import threading
import time
import types
from typing import Any, Callable, Dict, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from . import messages, motionrules, programs
from .capabilities import Capability
# Re-exported: the MDI gate used to live here, and tests import it from here.
from .mdi import (  # noqa: F401
    MDI_WHILE_HOMING,
    MDI_WHILE_PAUSED,
    _needs_homing,
    mdi_context_from_ini,
    mdi_refusal,
)
from .profiles import MachineProfile
from .state import (
    MDI_DONE,
    MDI_REFUSED,
    MDI_REJECTED,
    MDI_SENT,
    AlarmEvent,
    AxisPosition,
    DrivePower,
    EstopState,
    HardLimit,
    HomingPhase,
    HomingState,
    JointHoming,
    LastError,
    LinkHealth,
    MachineState,
    MdiEntry,
    RUN_CMD_PAUSE,
    RUN_CMD_RESUME,
    RUN_CMD_START,
    RUN_CMD_STEP,
    Severity,
    initial_estop,
)

# LinuxCNC task_state values (emcmodule.cc:329-334). Mirrored rather than
# imported so this module works without the linuxcnc package.
TASK_STATE_ESTOP = 1
TASK_STATE_ESTOP_RESET = 2
TASK_STATE_OFF = 3
TASK_STATE_ON = 4

# stat.task_mode, stat.interp_state and stat.motion_mode values, by the names
# MachineState carries (emc.hh:249-253, :277-282, :285-289; exported as
# linuxcnc.MODE_*, INTERP_* and TRAJ_MODE_*, emcmodule.cc:2447-2454,
# :2488-2490). Mirrored for the same reason; a value not listed maps to None.
TASK_MODES = {1: "manual", 2: "auto", 3: "mdi"}
INTERP_STATES = {1: "idle", 2: "reading", 3: "paused", 4: "waiting"}
MOTION_MODES = {1: "free", 2: "coord", 3: "teleop"}

#: stat.current_vel above this counts as moving (MachineState.moving), in
#: machine units per second. Chosen, not measured: at rest the simulator
#: reads exactly 0.0, and 1e-6 mm/s is far below any jog or feed.
MOVING_VEL_EPS = 1e-6

#: A G92 offset component larger than this in magnitude makes
#: MachineState.g92_active. Chosen, not measured: G92.1 sets the offsets to
#: exactly zero, and a real offset is many orders of magnitude larger.
G92_ACTIVE_EPS = 1e-9

# -- DURDUR (stop = abort), both adapters -----------------------------------
#: The stop's first half has gone out (stop_motion).
STOP_SENT = "Durdur gönderildi."
#: The stop's second half found the interpreter idle and nothing moving
#: (stop_followup).
STOPPED = "Durduruldu."
#: ...or not yet, after its bounded wait.
STOP_NOT_IDLE = "Durdurma gönderildi — LinuxCNC henüz boşta değil."
#: A motion action that a stop overtook on its way: nothing of it was sent
#: (LinuxCncAdapter._stopped_since).
STOP_REFUSAL = "Durdur basıldı — komut gönderilmedi."
#: The same, overtaken by the software estop.
ESTOP_REFUSAL = "Acil stop basıldı — komut gönderilmedi."

# -- the message band (render.render_msgband), both adapters -----------------
#: The software estop's message; an ERROR, so it stays in the error box
#: (MachineState.last_error) whatever a concurrent action says after it.
ESTOP_SENT = "Yazılım acil stop gönderildi. Sürücü gücü DEĞİŞMEDİ."
#: The same on MockAdapter, which has no LinuxCNC to send it to.
MOCK_ESTOP = "Yazılım acil stop. Sürücü gücü DEĞİŞMEDİ."
#: Where an error box's text came from (LastError.source).
SOURCE_LINUXCNC = "linuxcnc"
SOURCE_UI = "ui"

# -- the result of an MDI line (MdiEntry.status, LinuxCncAdapter) ------------
#: Sent; what LinuxCNC makes of it is not known yet.
MDI_SENT_NOTE = "Gönderildi, sonuç bekleniyor."
#: The interpreter went idle with no error and the machine still ON.
MDI_DONE_NOTE = "Tamamlandı."
#: A newer line was sent before this one's result was seen: LinuxCNC queues
#: MDI lines (emctaskmain.cc:1428-1439) and reports no line number for them
#: (MDI moves carry line 0), so only the newest line is followed.
MDI_UNTRACKED_NOTE = "Gönderildi; sonucu izlenmedi (ardından yeni satır gönderildi)."
#: Pending when DURDUR, the software estop or something else stopped the
#: machine: the line may not have completed.
MDI_CUT_BY_STOP = "Durdur basıldı — satır yarıda kalmış olabilir."
MDI_CUT_BY_ESTOP = "Acil stop basıldı — satır yarıda kalmış olabilir."
MDI_CUT_MACHINE_OFF = "Makine kapandı — satır yarıda kalmış olabilir."

#: An error drained this long after the last LinuxCNC error kept in the box
#: may still be that error's follow-up (messages.is_follow_up), which the
#: box does not take - unless LinuxCNC's last word on that refusal came
#: already (messages.ends_report); and a stop LinuxCNC makes within it is
#: that error's consequence, unless the error is a refusal or the board link
#: is lost (_note_task_state). Task forwards motion's
#: errors one per cycle (taskintf.cc:2024-2028; [TASK]CYCLE_TIME 0.010 on
#: the rig) and the UI drains at least once a second, so a report split
#: across two drains is well inside it. Chosen, not measured.
FOLLOW_UP_WINDOW_S = 2.0

#: An error drained this long after an MDI line was sent is that line's
#: (MdiEntry.status "rejected") even when task has left MDI mode by then;
#: in MDI mode any error is, while the line has no result. Chosen, not
#: measured (plan WP04).
MDI_RESULT_WINDOW_S = 2.0

#: HAL pins the UI reads for link health. Verified against the driver source.
HAL_LINK_PINS = (
    LinkHealth.HAL_PACKET_ERROR_TOTAL,   # hm2_eth.c:1558-1561
    LinkHealth.HAL_WATCHDOG_HAS_BIT,     # watchdog.c:140-144
    LinkHealth.HAL_IO_ERROR,             # hostmot2.c:1446
)

# LinuxCncAdapter also reads, the same way (LinuxCncAdapter._read_live_hal):
#   the switch inputs   each profile input's hal_pin, hm2_7i92.0.gpio.NNN.in_not
#                       on the rig: every GPIO exports .in and .in_not
#                       (hostmot2 ioport.c:250-263); MachineState.input_values
#   the step timing     STEPGEN_TIMING_PARAMS of every enabled stepgen
#                       (_stepgen_timing_names); MachineState.stepgen_timing


# ---------------------------------------------------------------------------
# Pure mapping
# ---------------------------------------------------------------------------


def map_drive_power(
    profile: MachineProfile,
    estop_input_value: Optional[bool],
) -> DrivePower:
    """Decide the HARDWARE drive-power state.

    The rule that matters: with no hardware estop input, the answer is UNKNOWN,
    never LIVE. LinuxCNC has nothing to read, so claiming the drives are powered
    would be an invention. A green "powered" light on an unsensed chain is worse
    than an honest "unknown".

    `estop_input_value` must come from the physical input pin itself, never
    from stat.estop: see map_linuxcnc_state.
    """
    if not profile.has(Capability.ESTOP_INPUT):
        return DrivePower.UNKNOWN
    if estop_input_value is None:
        return DrivePower.UNKNOWN
    # Input asserted (chain closed) means power is present.
    return DrivePower.LIVE if estop_input_value else DrivePower.CUT


def map_linuxcnc_state(
    stat: Any,
    hal_values: Mapping[str, Any],
    profile: MachineProfile,
    joint_axis_map: Sequence[str],
    joint_config: Mapping[int, Mapping[str, float]],
    alarms: Sequence[AlarmEvent] = (),
    mdi_history: Sequence[MdiEntry] = (),
    joint_numbers: Optional[Sequence[int]] = None,
) -> MachineState:
    """Turn a LinuxCNC status snapshot into MachineState. Pure.

    `joint_numbers[i]` is the LinuxCNC joint number of `joint_axis_map[i]`
    (the INI's [JOINT_n] numbers, sorted); None means 0, 1, 2, ... in
    order. Each joint's homing flags, hard-limit flags and homing config
    are read by that number - stat.joint is indexed by joint number
    (emcmodule.cc:612-618) - so a missing [JOINT_n] does not shift a later
    joint onto the wrong entry (P0 cleanup).

    `stat` only needs the attributes listed in this module's docstring, so a
    plain object or SimpleNamespace works for tests. A missing attribute
    gives the motion fields their "not read" value, None (for a hard-limit
    flag too); a missing offset or rotation counts as zero in the work DRO.

    The switch inputs come from hal_values keyed by each profile input's
    hal_pin, the step timing from hal_values["stepgen_timing"], a sequence
    of (HAL name, value) pairs (LinuxCncAdapter._read_live_hal). The
    message band is the adapter's own, passed through the same mapping:
    "message", "message_severity" and "last_error" (LinuxCncAdapter._map).

    Drive power comes ONLY from hal_values["estop_input"], the physical chain
    input. stat.estop is not used for it: stat.estop is io.aux.estop
    (emcmodule.cc:418), LinuxCNC's software latch, which is `!emc-enable-in`
    (ioControl.cc:523-526) and starts at 1 at every startup (ioControl.cc:791).
    In the estop-latch wiring emc-enable-in stays false until the GUI's reset
    edge (ioControl.cc:1090-1096), so reading stat.estop as "chain open" would
    report a hardware cut at every startup and disable the very reset button
    that clears the latch. The software side of it is already in
    task_state (determineState, emctask.cc:419-430).
    """
    raw_task_state = getattr(stat, "task_state", None)
    task_state = TASK_STATE_ESTOP if raw_task_state is None else int(raw_task_state)
    software_disabled = task_state != TASK_STATE_ON

    estop = EstopState(
        drive_power=map_drive_power(profile, hal_values.get("estop_input")),
        software_disabled=software_disabled,
        hardware_input_present=profile.has(Capability.ESTOP_INPUT),
    )

    # Read once: every access to stat.joint builds 16 dicts (Stat_joint,
    # emcmodule.cc:612-618).
    joints_raw = list(getattr(stat, "joint", []) or [])
    homing_joints: List[JointHoming] = []
    hard_limits: List[HardLimit] = []
    numbers = list(range(len(joint_axis_map))) if joint_numbers is None else list(joint_numbers)
    if len(numbers) != len(joint_axis_map):
        raise ValueError("joint_numbers and joint_axis_map differ in length")
    for jnum, axis in zip(numbers, joint_axis_map):
        j = joints_raw[jnum] if 0 <= jnum < len(joints_raw) else {}
        if j.get("homing"):
            phase = HomingPhase.HOMING
        elif j.get("homed"):
            phase = HomingPhase.HOMED
        else:
            phase = HomingPhase.NOT_HOMED
        cfg = joint_config.get(jnum, {})
        homing_joints.append(
            JointHoming(
                joint=jnum,
                axis=axis,
                phase=phase,
                sequence=_opt_int(cfg.get("HOME_SEQUENCE", jnum)),
                # Passed through: None means the INI value is unreadable
                # (state.JointHoming.ignore_limits) and must stay None.
                ignore_limits=cfg.get("HOME_IGNORE_LIMITS"),
                search_vel=_opt_float(cfg.get("HOME_SEARCH_VEL", 0.0)),
            )
        )
        # Motion's own reading of the switch inputs, taken every servo
        # cycle whether or not motion is enabled (control.c:491-501) and
        # copied from the joint's flag bits (taskintf.cc:959-962).
        hard_limits.append(
            HardLimit(
                joint=jnum,
                axis=axis,
                min=_opt_bool(j.get("min_hard_limit")),
                max=_opt_bool(j.get("max_hard_limit")),
            )
        )
    # !!overrideLimitMask, the same for every joint (taskintf.cc:963); joint
    # 0 exists whenever any does, and is the one the binding's
    # override_limits() names (emcmodule.cc:1113-1119).
    override_limits = _opt_bool(joints_raw[0].get("override_limits")) if joints_raw else None

    link = LinkHealth(
        connected=bool(hal_values.get("connected", False)),
        packet_error_total=_opt_int(hal_values.get(LinkHealth.HAL_PACKET_ERROR_TOTAL)),
        watchdog_has_bit=_opt_bool(hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT)),
        io_error=_opt_bool(hal_values.get(LinkHealth.HAL_IO_ERROR)),
        # [EMCMOT]SERVO_PERIOD from the INI, supplied by LinuxCncAdapter;
        # None when the INI has none - never an invented figure.
        servo_period_ms=_opt_float(hal_values.get("servo_period_ms")),
    )

    actual = list(getattr(stat, "actual_position", ()) or ())
    machine_xyz = [float(actual[idx]) if idx < len(actual) else 0.0 for idx in range(3)]
    g92 = getattr(stat, "g92_offset", None)
    work_xyz = work_coordinates(
        machine_xyz,
        tool_offset=getattr(stat, "tool_offset", None) or (),
        g5x_offset=getattr(stat, "g5x_offset", None) or (),
        rotation_xy=float(getattr(stat, "rotation_xy", 0.0) or 0.0),
        g92_offset=g92 or (),
    )
    commanded = getattr(stat, "position", None)
    positions: List[AxisPosition] = []
    for idx, axis in enumerate(("X", "Y", "Z")):
        homed = any(
            j.axis == axis and j.phase is HomingPhase.HOMED for j in homing_joints
        )
        positions.append(
            AxisPosition(
                axis=axis,
                work=work_xyz[idx],
                machine=machine_xyz[idx],
                homed=homed,
                commanded=(
                    float(commanded[idx])
                    if commanded is not None and idx < len(commanded) else None
                ),
            )
        )

    task_mode = TASK_MODES.get(_opt_int(getattr(stat, "task_mode", None)))
    interp = INTERP_STATES.get(_opt_int(getattr(stat, "interp_state", None)))
    motion_mode = MOTION_MODES.get(_opt_int(getattr(stat, "motion_mode", None)))
    paused = getattr(stat, "paused", None)
    moving = _moving(
        None if raw_task_state is None else task_state == TASK_STATE_ON,
        motion_mode,
        _opt_float(getattr(stat, "current_vel", None)),
        getattr(stat, "inpos", None),
    )

    return MachineState(
        estop=estop,
        homing=HomingState(joints=tuple(homing_joints)),
        link=link,
        positions=tuple(positions),
        alarms=tuple(alarms),
        mdi_history=tuple(mdi_history),
        wcs=_wcs_name(int(getattr(stat, "g5x_index", 1))),
        jog_step_mm=float(hal_values.get("jog_step_mm", 1.0)),
        feed_override_pct=int(round(float(getattr(stat, "feedrate", 1.0)) * 100)),
        program_file=getattr(stat, "file", None) or None,
        # Plan WP09: AUTO, the interpreter busy, nothing paused - not an MDI
        # line, not a paused program (program_paused below).
        program_running=(
            task_mode == "auto" and interp not in (None, "idle")
            and not paused and interp != "paused"
        ),
        program_progress=float(hal_values.get("program_progress", 0.0)),
        # The adapter's own message band, passed through like the message.
        message=str(hal_values.get("message", "")),
        message_severity=hal_values.get("message_severity", Severity.INFO),
        last_error=hal_values.get("last_error"),
        # A pin hal_values does not carry is None, unknown: never "free".
        input_values=tuple(
            (inp.hal_pin, _opt_bool(hal_values.get(inp.hal_pin))) for inp in profile.inputs
        ),
        stepgen_timing=tuple(
            (name, _opt_int(value)) for name, value in hal_values.get("stepgen_timing", ())
        ),
        hard_limits=tuple(hard_limits),
        override_limits=override_limits,
        moving=moving,
        task_mode=task_mode,
        interp_state=interp,
        motion_mode=motion_mode,
        # PLAN_PAUSE pauses motion and sets the interpreter PAUSED
        # (emctaskmain.cc:2312-2319); single-stepping pauses motion alone
        # (command.c:1200-1212, control.c:2170-2177).
        program_paused=(
            None if paused is None and interp is None
            else bool(paused) or interp == "paused"
        ),
        mdi_running=(
            None if task_mode is None or (task_mode == "mdi" and interp is None)
            else task_mode == "mdi" and interp != "idle"
        ),
        # task.motionLine is motion's id whenever that is set
        # (emctask.cc:705-707), so it lags the interpreter's current_line.
        program_line=_opt_int(getattr(stat, "motion_line", None)),
        g92_active=(
            None if g92 is None
            else any(abs(float(v)) > G92_ACTIVE_EPS for v in g92)
        ),
        # The adapter's own memory of what it sent (MachineState.run_command).
        run_command=hal_values.get("run_command"),
    )


def work_coordinates(
    machine: Sequence[float],
    tool_offset: Sequence[float] = (),
    g5x_offset: Sequence[float] = (),
    rotation_xy: float = 0.0,
    g92_offset: Sequence[float] = (),
) -> Tuple[float, ...]:
    """The work (program) coordinates of a machine position, computed as
    AXIS's DRO computes them (posstrs,
    reference/linuxcnc/lib/python/rs274/glcanon.py:1586-1596): minus the
    tool offset and the G5x origin, X and Y turned by -rotation_xy degrees
    about that origin, minus the G92 offset. The inverse of how the
    interpreter places a program point: G92, then the rotation, then the
    G5x origin and the tool offset.

    Pure. A component missing from an offset counts as 0; the result has as
    many components as `machine`.
    """

    def comp(values: Sequence[float], i: int) -> float:
        return float(values[i]) if i < len(values) else 0.0

    p = [float(m) - comp(tool_offset, i) - comp(g5x_offset, i) for i, m in enumerate(machine)]
    if len(p) >= 2:
        t = math.radians(-float(rotation_xy))
        x, y = p[0], p[1]
        p[0] = x * math.cos(t) - y * math.sin(t)
        p[1] = x * math.sin(t) + y * math.cos(t)
    return tuple(v - comp(g92_offset, i) for i, v in enumerate(p))


def _moving(
    task_on: Optional[bool],
    motion_mode: Optional[str],
    current_vel: Optional[float],
    inpos: Any,
) -> Optional[bool]:
    """MachineState.moving from whether task_state is ON, stat.motion_mode
    (by name), stat.current_vel and stat.inpos; None when one it needs is
    missing.

    Only while ON. Motion computes current_vel every servo cycle in every
    mode (control.c:1946-1962), but in teleop from the teleop planner, which
    keeps its last velocity when motion is disabled mid-jog: in sim/ a limit
    trip left ESTOP_RESET reading 3.000 mm/s. And disabled motion zeroes the
    joints' velocities but leaves INPOS as it was (control.c:1463-1477). In
    free (joint) mode motion clears INPOS while any joint's planner runs
    (control.c:1205-1272), which also covers the instants a joint jog starts
    or ends at zero velocity; a teleop jog leaves INPOS as it was
    (control.c:1398-1461), so there only current_vel tells.
    """
    if task_on is None:
        return None
    if not task_on:
        return False
    if current_vel is not None and current_vel > MOVING_VEL_EPS:
        return True
    if motion_mode == "free" and inpos is not None and not inpos:
        return True
    if current_vel is None or motion_mode is None or (motion_mode == "free" and inpos is None):
        return None
    return False


def _homing_sequence_gap_possible(state: MachineState) -> bool:
    """Whether `state` may be Home All between two joints of its sequence,
    where motion's homing is still active but no joint's homing flag shows
    it: the machine ON in free (joint) mode - motion homes only there
    (motion/command.c:1374-1378) - some joints homed, the others not, none
    homing.

    The joint that ends clears its flag (HOME_FINISHED, motion/homing.c:
    1343-1352); a servo cycle later the sequence moves on
    (HOME_SEQUENCE_WAIT_JOINTS, :458-477), and the cycle after that it
    starts the next joint (:435-456), which raises its flag in HOME_START
    (:754-772). Task publishes motion's status once per [TASK]CYCLE_TIME
    (task/emctaskmain.cc:3411, :3571), so a poll can show those two cycles
    for a whole task cycle (sim/e2e/harness.py wait_homing_over sees it). A
    jog sent then reaches motion while it homes the next joint: refused
    (motion/command.c:853-857), and task's abort after that error cancels
    that joint (command.c:510-519, homing.c:562-568) - the homing ends with
    it unhomed. A homing cut short, or one joint homed alone, looks the
    same for good: the only cost is the look again (HOMING_GAP_RECHECK_S)."""
    if not state.status_known or state.estop.blocks_motion or state.motion_mode != "free":
        return False
    phases = {j.phase for j in state.homing.joints}
    return (HomingPhase.HOMED in phases and HomingPhase.NOT_HOMED in phases
            and HomingPhase.HOMING not in phases)


def _opt_int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _opt_bool(v: Any) -> Optional[bool]:
    return None if v is None else bool(v)


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)


#: g5x_index 1..9 in the interpreter's order (interp_convert.cc:1762-1789).
_G5X_NAMES = ("G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3")


def _wcs_name(g5x_index: int) -> str:
    """g5x_index 1 -> G54 ... 6 -> G59, 7..9 -> G59.1..G59.3 (emcmodule.cc:754,
    interp_convert.cc:1762-1789). Anything else (0 before the first poll,
    emcmodule.cc Stat zero-fill) falls back to G54."""
    if 1 <= g5x_index <= len(_G5X_NAMES):
        return _G5X_NAMES[g5x_index - 1]
    return "G54"


# ---------------------------------------------------------------------------
# INI helpers shared by both adapters
# ---------------------------------------------------------------------------


def _ini_number(ini: Any, *keys: Tuple[str, str]) -> Optional[float]:
    """First of `keys` ([SECTION, KEY]) present in `ini`, as a float, or None."""
    if ini is None:
        return None
    for section, key in keys:
        value = ini.get(section, key)
        if value is not None:
            try:
                return value.as_float()
            except ValueError:
                return None
    return None


def _load_profile_ini(profile: MachineProfile) -> Any:
    from .iniload import load_ini

    return load_ini(profile.ini_path) if profile.ini_path else None


#: What sscanf("%d") reads at the start of a value: optional whitespace, a
#: sign, decimal digits. Anything after them ("0.5" -> 0) is ignored.
_SCANF_DECIMAL_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?[0-9]+)")


def _no_force_homing(ini: Any) -> bool:
    """Whether [TRAJ]NO_FORCE_HOMING lets MDI and programs run unhomed
    (emctaskmain.cc:2208, 2295). Read the way task reads it
    (emctaskmain.cc:3245-3262): sscanf("%d") takes the leading integer, so
    "0.5" is 0 and "1.5" is 1; task then forces a value <= 0 to 0 (off); a
    value sscanf cannot read is off too. So: on only for a leading integer
    >= 1."""
    value = ini.get("TRAJ", "NO_FORCE_HOMING") if ini is not None else None
    if value is None:
        return False
    m = _SCANF_DECIMAL_RE.match(value.raw)
    return bool(m) and int(m.group(1)) >= 1


def _read_profile_ini(profile: MachineProfile) -> Tuple[Any, Any]:
    """(MachineConfig, raw IniFile or None) for the profile: what each
    adapter's __init__ takes from the INI, read once each. The raw file
    carries the [DISPLAY], [TRAJ] and [EMCIO] keys configmodel does not.

    [EMCMOT]SERVO_PERIOD comes from config.servo_period_ms, so the link card
    never contradicts the config card; it is None when the INI has none, and
    LinkHealth then shows "—" rather than a placeholder (never the profile's
    platform figure).
    """
    from .configmodel import load_machine_config

    return load_machine_config(profile), _load_profile_ini(profile)


def _num_stepgens(config: Any) -> Optional[int]:
    """hostmot2's num_stepgens in [HOSTMOT2]CONFIG, or None when the INI
    gives no count - without num_stepgens hostmot2 enables every stepgen the
    firmware has (hostmot2.9:162-163), a number the INI does not hold."""
    m = re.search(r"(?:^|\s)num_stepgens=(\d+)(?=\s|$)", config.hm2_config_string or "")
    return int(m.group(1)) if m else None


def _stepgen_line(config: Any) -> Optional[str]:
    """The mock's "stepgens up" log line, counted from the INI: hostmot2's
    num_stepgens (_num_stepgens) and the axes configmodel found. None when
    the INI gives no count, so the mock never guesses."""
    count = _num_stepgens(config)
    if count is None or not config.axes:
        return None
    return "{} stepgen aktif, {} eksen jog ediyor".format(count, len(config.axes))


#: The hostmot2 stepgen timing parameters, each a u32 in nanoseconds, HAL_RW
#: (reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c:1066-1091).
#: The driver turns each into clock ticks and resets a value the FPGA timer
#: cannot hold to the maximum, writing that back into the parameter
#: (stepgen.c:348-387): the live value is what the board runs.
STEPGEN_TIMING_PARAMS = ("steplen", "stepspace", "dirsetup", "dirhold")


def _stepgen_timing_names(config: Any) -> Tuple[str, ...]:
    """The live timing parameters LinuxCncAdapter reads: every stepgen the
    INI enables (_num_stepgens), under its board's HAL prefix ([HOSTMOT2]
    BOARD, configmodel.MachineConfig.hal_prefix), in stepgen order. Empty
    when the INI gives either no count or no board - never a guessed name."""
    count = _num_stepgens(config)
    if count is None or not config.hal_prefix:
        return ()
    return tuple(
        "{}.stepgen.{:02d}.{}".format(config.hal_prefix, n, param)
        for n in range(count)
        for param in STEPGEN_TIMING_PARAMS
    )


def _joint_config(config: Any) -> Dict[int, Dict[str, Any]]:
    """configmodel.joint_config_from_profile, from an already loaded
    MachineConfig, so an adapter does not read its INI a third time. The two
    must agree (test_mdi_tools_adapter pins that for both profiles)."""
    if not config.present:
        # Not known without an INI (state.JointHoming): None, never made up.
        return {
            i: {"axis": a, "HOME_SEQUENCE": None, "HOME_IGNORE_LIMITS": None,
                "HOME_SEARCH_VEL": None}
            for i, a in enumerate(("X", "Y", "Z"))
        }
    return {
        a.joint: {
            "axis": a.axis,
            "HOME_SEQUENCE": a.home_sequence,
            "HOME_IGNORE_LIMITS": a.home_ignore_limits,
            "HOME_SEARCH_VEL": a.home_search_vel,
        }
        for a in config.axes
    }


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------


class MachineAdapter(abc.ABC):
    """What the UI is allowed to ask of a machine."""

    profile: MachineProfile

    def note_startup(self, text: str) -> None:
        """Something the operator should know as the UI comes up - why the
        control system last stopped (deploy/kiosk-session): the UI is back
        before the boot screen had a chance to show it. Logged, and the
        first message line."""

    @abc.abstractmethod
    def snapshot(self) -> MachineState:
        ...

    @abc.abstractmethod
    def send_mdi(self, command: str) -> MdiEntry:
        ...

    @abc.abstractmethod
    def software_estop(self) -> None:
        ...

    @abc.abstractmethod
    def software_estop_reset(self) -> None:
        ...

    @abc.abstractmethod
    def home_all(self, confirmed: bool = False) -> Optional[str]:
        """«Tümünü referansla» (plan WP07): every joint, in HOME_SEQUENCE
        order, under motionrules.home_refusal - refused, with the reason on
        the message line, while the machine is off, a switch is pressed, a
        program or MDI line or a homing runs, or the machine moves. With a
        joint homed already and `confirmed` False, nothing is sent and the
        question to ask first is returned (motionrules.home_question):
        app.Session shows it and calls again with confirmed=True on
        «Evet». None otherwise."""

    @abc.abstractmethod
    def home_joint(self, axis: str, confirmed: bool = False) -> Optional[str]:
        """One axis homed alone (plan WP07; AXIS's Machine ▸ Homing), under
        the same rules as home_all; the question comes for an axis homed
        already, and for another axis than motionrules.TOOL_AXIS while that
        one is unhomed. An axis the machine lacks is a KeyError (app.Session
        drops it)."""

    @abc.abstractmethod
    def jog(self, axis: str, direction: int) -> None:
        ...

    @abc.abstractmethod
    def load_program(self, path: str) -> None:
        """Open the G-code file at `path` (plan WP08): an absolute path that
        app.Session built from the server's own program list
        (programs.resolve), never a browser's string. Refused, with the
        reason on the message line, under programs.load_block - a program
        or MDI line running or paused, a homing, the machine moving, the
        status unknown. Out of ON it loads, as LinuxCNC and AXIS do."""

    @abc.abstractmethod
    def program_start(self) -> None:
        """«Başlat»: run the loaded program from its start. Refused, with
        the reason on the message line, by start_blocks, no file loaded,
        and anything running or paused (programs.start_busy, plan WP09)."""

    @abc.abstractmethod
    def program_pause(self) -> None:
        """«Duraklat» (plan WP09): pause the running program (or MDI line)
        where it is, keeping its place. Refused by programs.pause_refusal."""

    @abc.abstractmethod
    def program_resume(self) -> None:
        """«Devam» (plan WP09): go on from a pause - «Duraklat», an M0/M1,
        another GUI's. Refused by start_blocks and programs.resume_refusal;
        it starts motion, so nothing goes after a stop."""

    @abc.abstractmethod
    def program_step(self) -> None:
        """«Adım» (plan WP09): with nothing running, start the program
        paused at its beginning; with it paused, one line more. Refused by
        start_blocks, no file, and programs.step_refusal; it starts motion,
        so nothing goes after a stop."""

    @abc.abstractmethod
    def refuse(self, text: str) -> None:
        """A refusal app.Session decided before reaching any command (a
        program name not in the list, plan WP08): `text` on the message
        line as a WARN. Sends nothing, reads nothing."""

    @abc.abstractmethod
    def recover_limit(self) -> None:
        """«Limitten kurtar» (plan WP05): with an axis standing on a limit
        switch, the machine off, one press takes it off - LinuxCNC's limit
        override, the machine on, one joint jog away from the switch
        (motionrules.recovery_plan says how far) - and the override ends
        with that jog. Never a toggle, never sent by another action."""

    @abc.abstractmethod
    def stop_motion(self) -> None:
        """DURDUR, first half: stop whatever moves - a program, an MDI
        command, a jog, a homing - at once. app.Session calls it WITHOUT its
        lock, as it calls software_estop, so it must be safe to call while
        any other method runs in another thread, and must neither wait nor
        read LinuxCNC's status."""

    @abc.abstractmethod
    def stop_followup(self) -> None:
        """DURDUR, second half: app.Session calls it under its lock right
        after stop_motion, so it runs once the action that held the lock has
        returned. It makes sure of the stop and says how it went."""

    @abc.abstractmethod
    def ack_error(self, error_id: str) -> None:
        """«Tamam» on the error box: forget MachineState.last_error if its
        id is `error_id` (the button's data-value), else nothing - an error
        that arrived after the screen was drawn stays until it is seen.
        Local to the UI: sends nothing to LinuxCNC and reads nothing from
        it, so app.Session lets it through in observer mode too."""

    # -- the message band, shared by both adapters ---------------------------
    #
    # The operator message (MachineState.message) with its severity, and the
    # error box (MachineState.last_error). Written only through _say, under
    # _say_lock: software_estop and stop_motion run on another thread than
    # the action holding app.Session's lock, and the four fields must not
    # mix two messages. The lock is held for the assignments alone - never
    # across I/O - so it cannot delay the estop.

    def _init_messages(self, text: str = "") -> None:
        self._say_lock = threading.Lock()
        self._message = text
        self._message_severity = Severity.INFO
        self._last_error: Optional[LastError] = None
        #: The last error box's id (LastError.id); never reused.
        self._error_seq = 0

    def _say(self, text: str, severity: Severity = Severity.INFO,
             raw: str = "", source: str = SOURCE_UI) -> None:
        """Make `text` the operator message. INFO is news, WARN a refusal,
        ERROR a fault - LinuxCNC's errors, the software estop, a lost link
        or status - which also becomes the error box and stays there when
        the next message comes (ack_error clears it). `raw`: LinuxCNC's own
        text behind an ERROR (LastError.raw)."""
        with self._say_lock:
            self._message = text
            self._message_severity = severity
            if severity is Severity.ERROR:
                self._error_seq += 1
                self._last_error = LastError(self._error_seq, _now_s(), text, raw, source)

    def _say_if(self, expected: str, text: str, severity: Severity = Severity.INFO) -> None:
        """_say, only while `expected` is still the message: a later word
        on the line (an estop from another thread, say) is not covered."""
        with self._say_lock:
            if self._message != expected:
                return
            self._message = text
            self._message_severity = severity

    def _message_band(self) -> Tuple[str, Severity, Optional[LastError]]:
        """(message, severity, error box), read together."""
        with self._say_lock:
            return self._message, self._message_severity, self._last_error

    def _forget_error(self, error_id: str) -> None:
        """ack_error's body: compare and clear in one step under the lock,
        so an error said meanwhile on another thread is never cleared
        unseen."""
        with self._say_lock:
            box = self._last_error
            if box is not None and str(box.id) == str(error_id).strip():
                self._last_error = None


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a MockAdapter method under the adapter's own lock."""

    @functools.wraps(method)
    def wrapper(self: "MockAdapter", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MockAdapter(MachineAdapter):
    """In-memory machine. What this iteration renders from.

    It is not a toy: it enforces the same rules the real machine does, so the
    tests that matter (estop cannot be cleared in software, program start gated on
    homing, MDI refused before homing) exercise real logic rather than a
    rendering coincidence. The MDI gate is the real adapter's (mdi_refusal).

    Threading: app.Session calls software_estop without its own lock, so it
    may arrive while another method runs in another thread. Every public
    method therefore runs under this adapter's own small lock. No method
    waits or does I/O while holding it, so the estop is delayed by at most
    one in-memory method body - and a method that checked "not in estop"
    can no longer write "running" or "homed" after the estop landed.
    """

    def __init__(self, profile: MachineProfile, joint_config: Optional[Dict] = None):
        self.profile = profile
        self._lock = threading.Lock()
        config, ini = _read_profile_ini(profile)
        self._joint_config = (
            joint_config if joint_config is not None else _joint_config(config)
        )
        axes = [c["axis"] for c in self._joint_config.values()] or ["X", "Y", "Z"]
        self._no_force_homing = _no_force_homing(ini)
        #: What the INI adds to the MDI gate: a tool change that moves,
        #: remapped codes (mdi.MdiContext; P0 cleanup).
        self._mdi_context = mdi_context_from_ini(ini)
        #: [DISPLAY]MAX_FEED_OVERRIDE in percent; None with no INI, as on
        #: LinuxCncAdapter (feed_override_decision).
        self._max_feed_pct: Optional[int] = config.max_feed_override_pct

        self._estop = initial_estop(profile.has(Capability.ESTOP_INPUT))
        self._homing = HomingState(
            joints=tuple(
                JointHoming(
                    joint=jnum,
                    axis=cfg["axis"],
                    phase=HomingPhase.NOT_HOMED,
                    sequence=_opt_int(cfg.get("HOME_SEQUENCE", jnum)),
                    # Passed through; None = unreadable (state.JointHoming).
                    ignore_limits=cfg.get("HOME_IGNORE_LIMITS"),
                    search_vel=_opt_float(cfg.get("HOME_SEARCH_VEL", 0.0)),
                )
                for jnum, cfg in sorted(self._joint_config.items())
            )
        )
        self._positions = tuple(
            AxisPosition(axis=a, work=0.0, machine=0.0, homed=False) for a in axes
        )
        #: axis -> [JOINT_n]HOME, where home_all leaves it: LinuxCNC's
        #: homing ends with a move to HOME (HOME_FINAL_MOVE_START,
        #: reference/linuxcnc/src/emc/motion/homing.c:1250-1287), 0 when the
        #: INI has none (ini/inijoint.cc:162-163). On the rig 5 / 5 / -5,
        #: inside the soft limits the jog rule checks a homed jog against.
        self._home_pos = {
            cfg["axis"]: _ini_number(ini, ("JOINT_{}".format(jnum), "HOME")) or 0.0
            for jnum, cfg in sorted(self._joint_config.items())
        }
        #: The soft limits a homed jog is checked against (plan WP06), as
        #: LinuxCncAdapter has them.
        self._windows = motionrules.axis_windows(config)
        self._link = LinkHealth(
            connected=True,
            packet_error_total=0,
            watchdog_has_bit=False,
            io_error=False,
            servo_period_ms=config.servo_period_ms,
        )
        self._mdi: List[MdiEntry] = []
        self._init_messages("Konfigürasyon yüklendi. Referans alınmadı.")
        self._alarms: List[AlarmEvent] = [
            AlarmEvent(
                _now(), "hm2_eth: 7i92 bulundu, IDROM eşleşti (2 port × 17 = 34 IO)",
                "hm2_eth", Severity.INFO,
            ),
        ]
        stepgens = _stepgen_line(config)
        if stepgens is not None:
            self._alarms.append(AlarmEvent(_now(), stepgens, "hm2_7i92.0", Severity.INFO))
        if profile.key == "test-rig":
            # Why the rig has one stepgen more than it has axes. Until
            # 2026-11-12 this line was the "Y DIR fault", an ERROR.
            self._alarms.append(
                AlarmEvent(
                    _now(),
                    "Y'nin iki motoru tek eklemde: stepgen.01 ve stepgen.03 aynı komutu "
                    "alıyor (Y DIR arızası 2026-11-12'de çözüldü)",
                    "docs/DURUM.md:12", Severity.INFO,
                )
            )
        # Driven by the INI's ASSUMPTION markers, so measuring the travel and
        # deleting the markers withdraws it with no code change (the iniload
        # module docstring).
        if not config.soft_limits_configured:
            self._alarms.append(
                AlarmEvent(
                    _now(),
                    "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok",
                    "config", Severity.WARN,
                )
            )
        self._program_file: Optional[str] = None
        #: A program "runs" (until DURDUR or the estop; the mock reads no
        #: file), paused or not.
        self._running = False
        #: The mock's program is paused («Duraklat», or «Adım» from idle).
        self._paused = False
        #: The run command sent last (MachineState.run_command), reported
        #: while the mock's program runs.
        self._run_command: Optional[str] = None
        self._progress = 0.0
        self._wcs = "G54"
        self._jog_step = 1.0
        self._feed = 100

    # -- reads ---------------------------------------------------------------

    @_locked
    def note_startup(self, text: str) -> None:
        self._alarms.insert(0, AlarmEvent(_now(), "Kontrol sistemi yeniden başlatıldı — önceki durma: " + text,
                                          "kiosk", Severity.WARN))
        self._say("Kontrol sistemi yeniden başlatıldı — önceki durma: " + text, Severity.WARN)

    @_locked
    def snapshot(self) -> MachineState:
        return self._state()

    def _state(self) -> MachineState:
        """snapshot() without the lock, for methods that already hold it.

        The motion fields this model defines are reported, so the jog rule
        (motionrules.jog_refusal) reads them as LinuxCncAdapter's: nothing
        moves between calls (a jog or a homing completes at once), nothing
        pauses, an MDI line is done when sent; AUTO with the interpreter
        reading while a program "runs", else MANUAL - in teleop once every
        joint is homed, as task's MANUAL puts motion (emctask.cc:276-280),
        free before. What it has no model of stays "not read": the switch
        flags, the limit override, the program line, G92, the commanded
        position."""
        running = self._running
        paused = running and self._paused
        return MachineState(
            estop=self._estop,
            homing=self._homing,
            link=self._link,
            positions=self._positions,
            alarms=tuple(self._alarms),
            mdi_history=tuple(self._mdi),
            wcs=self._wcs,
            jog_step_mm=self._jog_step,
            feed_override_pct=self._feed,
            program_file=self._program_file,
            program_running=running and not paused,
            program_progress=self._progress,
            message=self._message,
            message_severity=self._message_severity,
            last_error=self._last_error,
            moving=False,
            task_mode="auto" if running else "manual",
            interp_state="paused" if paused else "reading" if running else "idle",
            motion_mode=(
                "coord" if running else "teleop" if self._homing.all_homed else "free"
            ),
            program_paused=paused,
            mdi_running=False,
            run_command=self._run_command if running else None,
        )

    # -- writes --------------------------------------------------------------

    @_locked
    def send_mdi(self, command: str) -> MdiEntry:
        return self._send_mdi(command)

    def _send_mdi(self, command: str) -> MdiEntry:
        """send_mdi without the lock, for methods that already hold it."""
        command = (command or "").strip()
        refusal = mdi_refusal(
            command,
            machine_on=not self._estop.blocks_motion,
            homed=self._homing.all_homed,
            no_force_homing=self._no_force_homing,
            program_running=self._running,
            paused=self._running and self._paused,
            homing=self._homing.any_homing,
            context=self._mdi_context,
        )
        # The mock runs nothing: a line it accepts is done at once.
        entry = MdiEntry(command, refusal is None, refusal or "Kabul edildi.",
                         MDI_REFUSED if refusal else MDI_DONE)
        self._mdi.append(entry)
        self._say("MDI: {} — {}".format(command or "(boş)", entry.note),
                  Severity.WARN if refusal else Severity.INFO)
        return entry

    @_locked
    def software_estop(self) -> None:
        self._estop = self._estop.software_estop()
        self._running = False
        self._paused = False
        self._say(MOCK_ESTOP, Severity.ERROR)
        self._alarms.insert(
            0, AlarmEvent(_now(), "Yazılım acil stop uygulandı", "task", Severity.ERROR)
        )

    @_locked
    def software_estop_reset(self) -> None:
        before = self._estop
        self._estop = self._estop.software_reset()
        if self._estop is before and before.drive_power is DrivePower.CUT:
            self._say(
                "Yazılım sıfırlaması reddedildi — sürücü gücü donanımsal kesik.", Severity.WARN
            )
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "Yazılım sıfırlaması reddedildi: donanım zinciri açık",
                    "task", Severity.WARN,
                ),
            )
        else:
            # Same wording as LinuxCncAdapter: homing survives a software
            # estop unless a joint has VOLATILE_HOME (emctask.cc:364,
            # homing.c:316-323).
            self._say("Yazılım acil stop sıfırlandı." + (
                "" if self._homing.all_homed else " Referans gerekli."
            ))

    @_locked
    def hardware_chain_open(self) -> None:
        """Simulate the physical chain opening. Not reachable from the UI."""
        self._estop = self._estop.hardware_chain_opened()
        self._running = False
        self._paused = False
        self._alarms.insert(
            0,
            AlarmEvent(
                _now(), "Acil stop zinciri açıldı — sürücü gücü kesildi",
                "estop-chain", Severity.ERROR,
            ),
        )

    @_locked
    def home_all(self, confirmed: bool = False) -> Optional[str]:
        """Every joint homed at once, standing at its [JOINT_n]HOME as
        LinuxCNC's homing leaves it (_home_pos), under the real adapter's
        rules (MachineAdapter.home_all): refused while the machine is off
        or the mock's program runs; with a joint homed, the question first."""
        return self._home(None, confirmed)

    @_locked
    def home_joint(self, axis: str, confirmed: bool = False) -> Optional[str]:
        """One axis homed at once, at its HOME (MachineAdapter.home_joint).
        An axis the machine lacks is a KeyError, as on LinuxCncAdapter."""
        if axis not in {j.axis for j in self._homing.joints}:
            raise KeyError(axis)
        return self._home(axis, confirmed)

    def _home(self, axis: Optional[str], confirmed: bool) -> Optional[str]:
        """home_all (`axis` None) and home_joint, under the lock."""
        state = self._state()
        refusal = motionrules.home_refusal(state, self.profile, axis)
        if refusal:
            self._say(refusal, Severity.WARN)
            return None
        if not confirmed:
            question = motionrules.home_question(state, self.profile, axis)
            if question:
                return question
        axes = {j.axis for j in self._homing.joints} if axis is None else {axis}
        self._homing = self._homing._replace(joints=tuple(
            j._replace(phase=HomingPhase.HOMED) if j.axis in axes else j
            for j in self._homing.joints
        ))
        self._positions = tuple(
            p._replace(homed=True, work=self._home_pos.get(p.axis, 0.0),
                       machine=self._home_pos.get(p.axis, 0.0))
            if p.axis in axes else p
            for p in self._positions
        )
        what = self._homing.order_label if axis is None else axis
        if axis is None:
            self._say("Referans tamam. Sıra: {}.".format(what))
        else:
            self._say("Referans tamam: yalnız {}.".format(what))
        self._alarms.insert(
            0,
            AlarmEvent(
                _now(),
                "Referans alma tamamlandı ({})".format(what),
                "homing", Severity.INFO,
            ),
        )
        return None

    @_locked
    def jog(self, axis: str, direction: int) -> None:
        """One incremental jog by the jog step, moved at once, under the
        real adapter's rule (motionrules.jog_refusal, plan WP06): refused
        while the machine is off or a program runs, unhomed beyond the step
        caps, homed beyond the soft limits - or cut to them, said so. An
        axis the machine lacks is a KeyError and a direction other than
        +1/-1 a ValueError, as on LinuxCncAdapter (app.Session catches
        both)."""
        if direction not in (1, -1):
            raise ValueError("jog direction must be +1 or -1, got {!r}".format(direction))
        if axis not in {p.axis for p in self._positions}:
            raise KeyError(axis)
        decision = motionrules.jog_refusal(
            self._state(), self.profile, self._windows, axis, direction, self._jog_step)
        if decision.refusal:
            self._say(decision.refusal, Severity.WARN)
            return
        step = decision.step_mm
        self._positions = tuple(
            p._replace(
                work=p.work + direction * step,
                machine=p.machine + direction * step,
            )
            if p.axis == axis
            else p
            for p in self._positions
        )
        if decision.note:
            self._say(decision.note, Severity.WARN)
        else:
            self._say(motionrules.jog_sent_text(axis, direction, self._jog_step))

    @_locked
    def load_program(self, path: str) -> None:
        """The same rule as LinuxCncAdapter's (programs.load_block); the
        mock opens nothing and keeps `path`, as stat.file would."""
        refusal = programs.load_block(self._state())
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        self._program_file = path
        self._progress = 0.0
        self._running = False
        self._paused = False
        self._say("{} yüklendi.".format(os.path.basename(path)))

    @_locked
    def refuse(self, text: str) -> None:
        self._say(text, Severity.WARN)

    @_locked
    def program_start(self) -> None:
        """LinuxCncAdapter.program_start's rules; the mock's program then
        "runs" until DURDUR or the estop."""
        state = self._state()
        blocks = state.start_blocks
        if blocks:
            self._say("Başlatılamaz: {}".format(blocks[0]), Severity.WARN)
            return
        if self._program_file is None:
            self._say("Program yüklenmedi.", Severity.WARN)
            return
        busy = programs.start_busy(state)
        if busy:
            self._say(busy, Severity.WARN)
            return
        self._running = True
        self._paused = False
        self._run_command = RUN_CMD_START
        self._say(programs.STARTED_TEXT.format(os.path.basename(self._program_file)))

    @_locked
    def program_pause(self) -> None:
        """LinuxCncAdapter.program_pause's rule (programs.pause_refusal)."""
        refusal = programs.pause_refusal(self._state())
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        self._paused = True
        self._run_command = RUN_CMD_PAUSE
        self._say(programs.PAUSED_TEXT)

    @_locked
    def program_resume(self) -> None:
        """LinuxCncAdapter.program_resume's rules."""
        state = self._state()
        blocks = state.start_blocks
        if blocks:
            self._say("Sürdürülemez: {}".format(blocks[0]), Severity.WARN)
            return
        refusal = programs.resume_refusal(state)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        self._paused = False
        self._run_command = RUN_CMD_RESUME
        self._say(programs.RESUMED_TEXT)

    @_locked
    def program_step(self) -> None:
        """LinuxCncAdapter.program_step's rules. From idle the mock's
        program starts paused; from a pause it stays paused (the mock has
        no lines to step through)."""
        state = self._state()
        blocks = state.start_blocks
        if blocks:
            self._say("Adım atılamaz: {}".format(blocks[0]), Severity.WARN)
            return
        if self._program_file is None:
            self._say("Program yüklenmedi.", Severity.WARN)
            return
        refusal = programs.step_refusal(state)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        first = not self._running
        self._running = True
        self._paused = True
        self._run_command = RUN_CMD_STEP
        self._say(programs.STEP_FIRST_TEXT if first else programs.STEP_TEXT)

    @_locked
    def recover_limit(self) -> None:
        """The mock reads no switches - its MachineState.hard_limits stays
        empty, so motionrules.limit_trips finds none and the Kontrol screen
        offers no recovery - and says what LinuxCncAdapter says with none
        pressed."""
        self._say(motionrules.NO_TRIP, Severity.WARN)

    @_locked
    def stop_motion(self) -> None:
        """DURDUR: the program stops and a homing in progress ends unhomed,
        as LinuxCNC's abort leaves it (HOME_ABORT clears homed,
        reference/linuxcnc/src/emc/motion/homing.c:1354-1362). The mock's
        jogs and homing complete at once, so nothing else is in motion.
        Under the mock's own lock, like every method: no stop can land
        between another method's check and its write."""
        self._running = False
        self._paused = False
        self._progress = 0.0
        stopped = {j.axis for j in self._homing.joints if j.phase is HomingPhase.HOMING}
        if stopped:
            self._homing = self._homing._replace(joints=tuple(
                j._replace(phase=HomingPhase.NOT_HOMED) if j.axis in stopped else j
                for j in self._homing.joints
            ))
            self._positions = tuple(
                p._replace(homed=False) if p.axis in stopped else p for p in self._positions
            )
        self._say(STOP_SENT)
        self._alarms.insert(0, AlarmEvent(_now(), "Durdur gönderildi", "task", Severity.INFO))

    @_locked
    def stop_followup(self) -> None:
        """Nothing is left moving after stop_motion on the mock."""
        self._say(STOPPED)

    @_locked
    def ack_error(self, error_id: str) -> None:
        self._forget_error(error_id)

    @_locked
    def set_jog_step(self, mm: float) -> None:
        """As LinuxCncAdapter.set_jog_step: a positive finite number only."""
        mm = float(mm)
        if not math.isfinite(mm) or mm <= 0:
            self._say("Geçersiz jog adımı: {!r} mm.".format(mm), Severity.WARN)
            return
        self._jog_step = mm

    @_locked
    def set_feed_override(self, pct: int) -> None:
        """LinuxCncAdapter.set_feed_override's rule (feed_override_decision):
        the INI's ceiling, refused with no INI or below zero."""
        decision = feed_override_decision(int(pct), self._max_feed_pct)
        self._say(decision.text, decision.severity)
        if decision.pct is not None:
            self._feed = decision.pct

    @_locked
    def set_wcs(self, wcs: str) -> None:
        """LinuxCncAdapter.set_wcs's rule: G54-G59 only (wcs_refusal), sent
        as an MDI line under the same gate (send_mdi, mdi_refusal)."""
        refusal = wcs_refusal(wcs)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        if self._send_mdi(wcs).accepted:
            self._wcs = wcs


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M")


def _now_s() -> str:
    """The error box's time (LastError.time): to the second."""
    return datetime.datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Real
# ---------------------------------------------------------------------------


#: Coordinate systems the UI may select, in g5x_index order 1..6
#: (src/emc/rs274ngc/interp_convert.cc:1762-1779). G59.1-G59.3 (7..9) are
#: legal in LinuxCNC and are shown when stat reports them (_wcs_name), but
#: set_wcs refuses them by choice: the UI offers G54-G56 only
#: (viewmodel.control_screen, wcs_options), and nothing here uses 7..9 yet.
_WCS_CODES = _G5X_NAMES[:6]


class FeedDecision(NamedTuple):
    """feed_override_decision's answer: the percent to send (None: send
    nothing), the operator's words and their severity."""

    pct: Optional[int]
    text: str
    severity: Severity


def feed_override_decision(pct: int, max_pct: Optional[int]) -> FeedDecision:
    """The feed-override rule both adapters apply (P0 cleanup; until then
    MockAdapter took any value): with no INI the ceiling is not known and
    nothing changes; a negative percent is refused - motion clamps only
    negatives, to 0 (command.c:1214-1221), so it would read as a hold the
    operator did not ask for; above [DISPLAY]MAX_FEED_OVERRIDE the ceiling
    is applied, in whole percent as axis.py does (:3353-3355). 0 % is a
    valid override (it holds the feed)."""
    if max_pct is None:
        return FeedDecision(None, "Besleme değiştirilmedi — INI yok, besleme sınırı bilinmiyor.",
                            Severity.WARN)
    if pct < 0:
        return FeedDecision(None, "Geçersiz besleme yüzdesi: %{}.".format(pct), Severity.WARN)
    if pct > max_pct:
        return FeedDecision(max_pct, "Besleme %{} istendi; INI sınırı %{} uygulandı.".format(
            pct, max_pct), Severity.WARN)
    return FeedDecision(pct, "Besleme %{}.".format(pct), Severity.INFO)


def wcs_refusal(wcs: str) -> str:
    """Why `wcs` is not a coordinate system this UI selects, "" when it is:
    G54-G59 only (_WCS_CODES), sent as an MDI line under send_mdi's gates.
    Both adapters (P0 cleanup)."""
    return "" if wcs in _WCS_CODES else "Bilinmeyen koordinat sistemi: {}".format(wcs)

#: The longest any LinuxCncAdapter action waits in command.wait_complete().
#:
#: Why short: the binding keeps the GIL for the whole wait. wait_complete
#: (emcmodule.cc:1383-1388) calls emcWaitCommandComplete (:212-231), which
#: sleeps in esleep() with no Py_BEGIN_ALLOW_THREADS - the module's only one is
#: in logger.start (:2125). While an action waits, no other Python thread runs,
#: the HTTP thread carrying a software estop included, and on the test rig
#: that button is the only estop. The binding's default is
#: EMC_COMMAND_TIMEOUT = 5 s (:209).
#:
#: Every command SEND holds the GIL too: emcSendCommand (:236-251) spins until
#: task echoes the command, up to those 5 s. Task echoes an ordinary command
#: at the end of the cycle that read it (about one [TASK]CYCLE_TIME), but a
#: state() command (ESTOP, ESTOP_RESET, ON) only after task's emcLubeOn/Off
#: has waited for iocontrol (sendCommand, taskclass.cc:155-200; iocontrol
#: sleeps one [EMCIO]CYCLE_TIME between reads, ioControl.cc:1138) - so about
#: one [EMCIO]CYCLE_TIME plus a task cycle. The worst normal delay of a
#: software estop is therefore about max(WAIT_TIMEOUT_S, one EMCIO cycle plus
#: one task cycle), and up to 5 s per send if task or iocontrol stalls.
#: Computed from the source, not measured.
#:
#: Why waiting is not enough anyway: task reports RCS_DONE only when motion is
#: DONE and the interpreter idle (emctaskmain.cc:3546-3561), and a joint is
#: RCS_EXEC until it is in position (taskintf.cc:974-983). While a jog or a
#: homing move is still running, no wait of any length short of the move
#: itself returns DONE.
#:
#: Nor is DONE proof: NML serial numbers are global across channels
#: (emcCommand is a "queue ... serial" buffer, configs/common/linuxcnc.nml:9;
#: write_id comes from the shared queue header, cms_in.cc:1489-1516), and
#: emcWaitCommandComplete returns DONE as soon as task has echoed any LATER
#: serial (emcmodule.cc:218-220). A software estop sent on the other channel
#: during a wait can therefore end that wait early.
#:
#: So every wait is short and is followed by reading the state it was meant to
#: produce; if that state was not reached the action is refused with a Turkish
#: message, never continued in the wrong mode. 0.25 s is 25 cycles of the
#: rig's [TASK]CYCLE_TIME = 0.010. Chosen, not measured: if it proves too short
#: on the machine, the symptom is a refusal that goes away on retry.
WAIT_TIMEOUT_S = 0.25

#: LinuxCNC's own default when [EMCIO]CYCLE_TIME is absent (emccfg.h:34,
#: applied by ioControl.cc:223-238). A value <= 0 falls back to it as well,
#: by choice: iocontrol itself accepts any number there (sscanf %lf with no
#: range check, ioControl.cc:225), but a reset deadline of zero would refuse
#: every reset whose latch is not clear on the first poll.
DEFAULT_EMCIO_CYCLE_TIME_S = 0.100

#: Pause between stat polls while waiting for the estop latch; the same
#: interval the binding uses between its own checks (EMC_COMMAND_DELAY,
#: emcmodule.cc:210). time.sleep releases the GIL, unlike wait_complete.
_RESET_POLL_INTERVAL_S = 0.01

#: How long LinuxCncAdapter.jog waits before it looks at the status again
#: when that may be Home All between two joints of its sequence
#: (_homing_sequence_gap_possible). Task publishes motion's status once per
#: [TASK]CYCLE_TIME (task/emctaskmain.cc:3411, :3571; 0.010 on the rig), and
#: the gap lasts two servo cycles, so three task cycles are more than enough
#: for the next joint's homing flag to show. Chosen, not measured.
HOMING_GAP_RECHECK_S = 0.03

#: At most this many error-channel messages are read per drain. Error_poll
#: answers (type, "unrecognized error N") for a type it does not know
#: (emcmodule.cc:1588-1593), and NML read() errors come back as such a type
#: while the buffer still reports valid() - a loop without a bound could
#: then spin forever, under app.Session's lock.
_MAX_ERRORS_PER_DRAIN = 64

#: Stands in for stat when the status channel cannot be read. The mapped
#: state is then marked status_known=False (MachineState.as_status_unknown),
#: with no positions and the motion fields emptied: the other fields
#: map_linuxcnc_state fills in (software disabled, nothing homed, feed
#: 100 %, G54) are placeholders the view must not show as readings.
_NO_STATUS = types.SimpleNamespace()

#: The refusal when an action cannot read LinuxCNC's status before sending
#: anything.
NO_LINUXCNC_REFUSAL = "LinuxCNC'ye bağlantı yok — komut gönderilmedi."
#: The same, once the action had already sent a command.
LINUXCNC_LOST_REFUSAL = (
    "LinuxCNC'ye bağlantı koptu — gönderilen komutun sonucu bilinmiyor."
)


class LinuxCncUnavailable(RuntimeError):
    """LinuxCncAdapter cannot be built: no `linuxcnc` module, LinuxCNC not
    running, or this process cannot read LinuxCNC's status. The message is
    the operator's, in Turkish; app.main prints it and exits 1."""


def _tool_mmap_path() -> str:
    """LinuxCNC's tool-table mmap file as the binding looks for it:
    $HOME/.tool.mmap, /tmp without HOME (tool_mmap_fname and
    TOOL_MMAP_FILENAME, reference/linuxcnc/src/emc/tooldata/
    tooldata_mmap.cc:31, :74-80). Only for messages; the binding opens it."""
    return os.path.join(os.environ.get("HOME") or "/tmp", ".tool.mmap")


#: Why every stat.poll() after the first can fail in one process, and the
#: message for it. poll() opens the tool mmap on its first call in the
#: process; if that fails it sets a process-wide static, and from then on
#: every poll() of every stat object returns NULL without setting an
#: exception (emcmodule.cc:286-305), which Python raises as SystemError.
#: Seen in sim/ on 2026-10-19 (2.9.9): no restart of LinuxCNC helps, only a
#: new process.
POLL_SPOILED = (
    "linuxcnc.stat.poll() bu süreçte kalıcı olarak bozuldu: ilk poll'da "
    "LinuxCNC'nin takım tablosu ({}) yoktu — arayüzü LinuxCNC açıkken, onu "
    "başlatan kullanıcıyla (aynı HOME) yeniden başlatın"
)

# -- the board link, in the operator's words --------------------------------
#: What the operator reads when the board link is lost (watchdog bite or
#: hm2 io_error), and what to do. No "watchdog", no "io_error": the person at
#: the machine needs the cause they can check and the button to press.
LINK_LOST = "Kartla bağlantı koptu — makine durduruldu"
LINK_ACTION = (
    "Ethernet kablosunu ve kartın beslemesini kontrol edin, "
    "sonra «Yazılım sıfırla»ya basın."
)
#: After a reset that brought the link back. Position may be wrong: while
#: the board was cut off its step generators kept counting with their
#: outputs dropped (reference/linuxcnc/docs/src/drivers/hostmot2.adoc:96-103),
#: so the reset also unhomes every joint.
LINK_RESTORED = (
    "Kart bağlantısı yeniden kuruldu, makine açıldı. Konum kaymış olabilir: "
    "hareketten önce referansı yeniden alın."
)
LINK_NOT_RESTORED = (
    "Kartla bağlantı kurulamadı — makine açılamadı. Ethernet kablosunu ve "
    "kartın beslemesini kontrol edip tekrar «Yazılım sıfırla»ya basın."
)
#: After a stop this UI did not cause (_note_task_state).
RESET_ACTION = "Sebep giderilince «Yazılım sıfırla»ya basın."

# -- observer mode (app.py --observe) ----------------------------------------
#: An action refused because the UI only observes: the machine is run from
#: AXIS beside it, and nothing but the software estop leaves this UI
#: (_not_while_observing).
OBSERVE_REFUSAL = "Salt izleme — komut gönderilmedi; makine AXIS'ten kullanılıyor."
#: LINK_ACTION in observer mode, which has no «Yazılım sıfırla»: AXIS's own
#: reset clears neither the watchdog's has_bit, which holds the rig's estop
#: latch open, nor io_error, with which this UI estops the machine again at
#: every ON (_stop_on_link_fault). A LinuxCNC restart loads the driver anew,
#: and the driver clears has_bit at load (watchdog.c:171).
OBSERVE_LINK_ACTION = (
    "Ethernet kablosunu ve kartın beslemesini kontrol edin, sonra LinuxCNC'yi "
    "yeniden başlatın — salt izlemede bu ekran kart bağlantısını sıfırlayamaz."
)
#: RESET_ACTION in observer mode: the machine is switched back on in AXIS.
OBSERVE_RESET_ACTION = "Sebep giderilince makineyi AXIS'ten açın."

#: After io_error is cleared, how long the driver gets to report what it
#: finds on the board. hm2_eth sets io_error, and only io_error, when the
#: link drops (hm2_eth.c:883); while it is set the driver skips every read,
#: so the board's own watchdog bite is not seen (watchdog.c:38). The read
#: after the clear finds it and raises has_bit (watchdog.c:48-51) within a
#: servo period or two - or io_error comes straight back when the cable is
#: still out (5 misses at 1 kHz). 0.5 s is hundreds of either. Chosen.
LINK_SETTLE_S = 0.5
_LINK_POLL_S = 0.05

#: How long one halcmd call may take before it counts as failed. halcmd
#: answers in tens of milliseconds; it waits longer only while another
#: process holds the HAL mutex. Chosen, not measured; 2 s keeps a reset from
#: hanging on it while being far above any normal answer.
HALCMD_TIMEOUT_S = 2.0


class _LinkStillDown(Exception):
    """io_error came back right after it was cleared: the cable is still out."""


def _run_halcmd(args: List[str]) -> Tuple[bool, str]:
    """Run `halcmd ARGS`: (exit status 0, stdout + stderr). Never raises.

    halcmd, not hal.set_p: the binding's set_p refuses a pin that is on a
    signal ("pin connected to signal", halmodule.cc:970-975), and the
    watchdog pin is on the rig's `watchdog` net; halcmd `sets` can write it.
    """
    try:
        done = subprocess.run(
            ["halcmd"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=HALCMD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return done.returncode == 0, done.stdout


def _signal_of(show_pin: str, pin: str) -> Optional[str]:
    """The signal `halcmd -s show pin PIN` says PIN is on, or None.
    Lines read "OWNER TYPE DIR VALUE NAME <=> SIGNAL" (also ==> and <==),
    as halcmd 2.9.9 printed them in sim/ on 2026-10-20."""
    for line in show_pin.splitlines():
        words = line.split()
        if pin in words:
            i = words.index(pin)
            if i + 2 < len(words) and words[i + 1] in ("<=>", "==>", "<=="):
                return words[i + 2]
    return None


def _process_table(proc_root: str = "/proc") -> Optional[Dict[str, List[str]]]:
    """Running processes by name (comm) -> their pids, or None where there is
    no /proc to read (off Linux): the checks are then skipped, never failed."""
    if not os.path.isdir(proc_root):
        return None
    table: Dict[str, List[str]] = {}
    for entry in os.listdir(proc_root):
        if entry.isdigit():
            try:
                with open(os.path.join(proc_root, entry, "comm"), "r") as fh:
                    table.setdefault(fh.read().strip(), []).append(entry)
            except OSError:
                continue  # exited while we looked
    return table


def _voluntary_switches(proc_root: str, pid: str) -> Optional[int]:
    """The process's voluntary context switches (/proc/PID/status), or None."""
    try:
        with open(os.path.join(proc_root, pid, "status"), "r") as fh:
            for line in fh:
                if line.startswith("voluntary_ctxt_switches:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def _cpu_ticks(proc_root: str, pid: str) -> Optional[int]:
    """The process's CPU time, user + system clock ticks (/proc/PID/stat
    fields 14 and 15, after the parenthesised comm), or None."""
    try:
        with open(os.path.join(proc_root, pid, "stat"), "r") as fh:
            rest = fh.read().rsplit(")", 1)[1].split()
        return int(rest[11]) + int(rest[12])
    except (OSError, ValueError, IndexError):
        return None


#: A LinuxCNC loop that has neither slept nor worked for this long is stuck.
#: Task and iocontrol each sleep once per cycle ([TASK]CYCLE_TIME 10 ms,
#: [EMCIO] CYCLE_TIME 100 ms on the rig), and every sleep is a voluntary
#: context switch the kernel counts in /proc/PID/status; in sim/ on
#: 2026-10-20 task counted ~87/s, iocontrol ~9/s, and a frozen task 0.
#: Task does not sleep while it has queued work - "eager", emctaskmain.cc:
#: 3593 - which a long run of program lines can keep up; so its CPU time
#: (/proc/PID/stat) counts as life too, and only a process that is doing
#: nothing at all is stuck: blocked in a lock, or frozen. 2 s is 200 task
#: cycles and 20 iocontrol cycles: no normal turn of either loop comes near
#: it. This is the stand-in for task's own heartbeat (emc_nml.hh:1172,
#: emctask.cc:739), which the Python binding does not export. Chosen;
#: deploy/README.md "Süreler". Still invisible: a loop that keeps cycling
#: while doing nothing useful.
LOOP_STALL_S = 2.0

#: Why the status is treated as lost while a loop is stuck: the status buffer
#: still reads, but nothing writes it, so every value in it is stale.
LOOP_STALLED = "{} {:g} s'dir cevap vermiyor"


class _Drained(NamedTuple):
    """The error that took the error box in one drain (_drain_errors):
    LinuxCNC's text and the operator's (messages.operator_text)."""

    raw: str
    text: str


class _LastLinuxCnc(NamedTuple):
    """The last LinuxCNC error the error box took (_drain_errors)."""

    raw: str
    #: The adapter clock when it was drained.
    at: float
    #: More reports of the same refusal may still come: no
    #: messages.ends_report text since.
    open: bool


class _PendingMdi(NamedTuple):
    """An MDI line sent, its result not seen yet (_note_mdi_result)."""

    #: Its MdiEntry's place in LinuxCncAdapter._mdi, which only grows.
    index: int
    #: The adapter clock when it was sent.
    sent_at: float
    #: The stop count its send was checked against: a stop or estop since
    #: may have cut it short.
    stop_seq: int
    #: The operator message its send said, replaced once the result is
    #: known unless something else has been said since.
    message: str


class _Recovery(NamedTuple):
    """A recovery jog sent (recover_limit), followed until LinuxCNC's limit
    override is over (_follow_recovery)."""

    joint: int
    axis: str
    #: The switch's end, "+" or "-" (motionrules.LimitTrip.end).
    end: str
    label: str


class _Run(NamedTuple):
    """A program run followed until the interpreter is idle again
    (LinuxCncAdapter._follow_run, plan WP09): begun by «Başlat» or «Adım»
    here, or seen running by a snapshot (started elsewhere)."""

    #: stat.file as the run began.
    file: str
    #: The stop count then: a stop or an estop since ended it.
    stop_seq: int
    #: How many LinuxCNC errors the box had taken then (_errors_taken):
    #: more since means LinuxCNC reported an error during the run. A count,
    #: not _last_linuxcnc itself: a follow-up closing that error replaces
    #: the tuple (_read_errors) without being a new error.
    error: int


def _message_facts(profile: MachineProfile, joint_config: Mapping[int, Mapping[str, Any]],
                   ini: Any) -> messages.Facts:
    """What messages.translate may name on this machine: the joints' axis
    letters, the profile's switch labels, and the soft-limit windows
    LinuxCNC checks a move against - [AXIS_<L>] MIN_LIMIT / MAX_LIMIT for an
    axis report (iniaxis.cc:84, :95), [JOINT_<n>] for a joint report
    (inijoint.cc:132, :140). An end the INI does not give is None, and the
    translation then names the side, not a number."""
    joint_axes = {int(j): str(c["axis"]) for j, c in joint_config.items()}

    def window(section: str) -> messages.Limits:
        return (_ini_number(ini, (section, "MIN_LIMIT")), _ini_number(ini, (section, "MAX_LIMIT")))

    return messages.Facts(
        joint_axes=joint_axes,
        inputs=tuple(profile.inputs),
        axis_limits={a: window("AXIS_" + a) for a in sorted(set(joint_axes.values()))},
        joint_limits={j: window("JOINT_{}".format(j)) for j in sorted(joint_axes)},
    )


def _refused_without_status(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a LinuxCncAdapter action; refuse it if LinuxCNC's status is lost.

    stat.poll() raises linuxcnc.error once the status buffer is invalid
    (check_stat, emcmodule.cc:277-283); error_channel.poll() does too, but
    _drain_errors catches that itself. Command sends and wait_complete never
    raise it (emcmodule.cc:212-251). So the poll is the one place an action
    can fail this way, and without this guard the exception would escape
    app.Session.act, which catches only ValueError/KeyError, and kill the
    HTTP request with no message.

    The outage is logged as snapshot() logs it, and the operator message says
    whether anything had been sent (counted by _send): every guarded action
    polls before its first send, so a dead status channel normally means
    nothing was sent - a refusal (WARN), the outage itself being in the
    error box; a command whose result is unknown is an ERROR of its own.
    Returns None in place of the action's result.
    """

    @functools.wraps(method)
    def wrapper(self: "LinuxCncAdapter", *args: Any, **kwargs: Any) -> Any:
        sends_before = self._sends
        try:
            return method(self, *args, **kwargs)
        except self._linuxcnc.error as exc:
            self._note_status_lost(exc)
            if self._sends == sends_before:
                self._say(NO_LINUXCNC_REFUSAL, Severity.WARN)
            else:
                self._say(LINUXCNC_LOST_REFUSAL, Severity.ERROR)
            return None

    return wrapper


def _not_while_observing(method: Callable[..., Any]) -> Callable[..., Any]:
    """Refuse a LinuxCncAdapter action in observer mode (observe=True)
    before it reads, sends or runs halcmd: OBSERVE_REFUSAL becomes the
    operator message and the action returns None, as a guarded action does
    with the status lost.

    app.Session already drops every action but the software estop (and
    the local ack_error) in that mode; this is the adapter's own line, for
    any caller that does not go through Session. Every action guarded by
    _refused_without_status carries it too (test_observe_and_live_hal pins
    that), so a new action cannot send from an observer by forgetting it.
    software_estop does not: the estop is the one command an observer
    sends. Nor does ack_error, which sends nothing.
    """

    @functools.wraps(method)
    def wrapper(self: "LinuxCncAdapter", *args: Any, **kwargs: Any) -> Any:
        if self._observe:
            self._say(OBSERVE_REFUSAL, Severity.WARN)
            return None
        return method(self, *args, **kwargs)

    return wrapper


class LinuxCncAdapter(MachineAdapter):
    """The real adapter.

    Tested against ui/tests/fake_linuxcnc.py, a fake `linuxcnc`/`hal` pair
    built from the LinuxCNC 2.9 bindings, task planner and motion source, and
    checked against that source by ui/tests/test_linuxcnc_adapter.py whenever
    reference/ is present. On a real machine it has only run view-only, beside
    AXIS on 2026-11-12 with its commands cut by an out-of-repo viewer: the
    fake is only as right as our reading of the source, and real timing is
    not measured.

    Sequencing follows axis.py, the reference GUI
    (src/emc/usr_intf/axis/scripts/axis.py), with two deliberate differences:
    every wait is bounded by WAIT_TIMEOUT_S and checked, and homing is not
    waited for at all.

      task mode     mode(m), wait, poll, check mode    ensure_mode, :986-992
      jog mode      joint jog until homed, teleop after get_jog_mode, :3225-3242
      teleop switch teleop_enable(v), wait, check mode set_motion_teleop, :273-279
      home          MANUAL, teleop off, home(-1)       :2693-2702, :1954-1957
                    or home(j) for one axis            home_joint, :2708-2728
      program       synch, wait, program_open, wait    open_file_guts, :1206-1208
      stop          own channel, abort(), no wait;     task_stop, :2411-2417
                    then abort(), wait, read back
      estop         own channel, state(ESTOP), nothing else
      limit         MANUAL, override_limits(), wait,   toggle_override_limits,
                    read back; ON; teleop off; one     :2914-2921 (no toggle:
                    joint jog away (recover_limit)     see recover_limit)

    Refusals are decided here first, so the operator gets an immediate Turkish
    message through MachineState.message - the same channel MockAdapter uses.
    LinuxCNC's own refusal still arrives later through the error channel and
    lands in the alarm log and in the error box (MachineState.last_error),
    in Turkish where messages.translate knows the text (_drain_errors). An
    MDI line's history entry follows its result (_note_mdi_result). Every
    action that polls is guarded (_refused_without_status): with LinuxCNC's
    status lost it refuses instead of raising.

    Threading: app.Session runs every action under its lock except
    software_estop and stop_motion (DURDUR's first abort), which may arrive
    from another thread while an action is in progress. Each therefore has
    a command channel of its own and touches nothing but that channel, the
    message band (_say: _message, _message_severity and, for the estop's
    ERROR, the error box _last_error and its _error_seq - under _say_lock,
    held for the assignments alone), _alarms and the stop sequence
    (_note_stop: _stop_seq and _stop_why, under a lock held for the
    increment alone); software_estop also the _estop_sent flag (one bool
    store; the snapshot only reads and clears it). An estop or abort sent
    during another action's wait can end that wait early (see
    WAIT_TIMEOUT_S); the action's post-wait state check then refuses, and in
    any case its last check before the motion command (_stopped_since)
    does.

    Observer mode (observe=True, app.py --observe), for running beside AXIS:
    every action but software_estop is refused (_not_while_observing), so
    the only commands that leave are the operator's estop and the link-fault
    estop (_stop_on_link_fault), both STATE_ESTOP; ack_error, which sends
    nothing, is allowed. No error channel is opened: an NML error message
    goes to whichever reader polls first, and draining it here would take
    it away from AXIS (_drain_errors). The operator messages name AXIS or a
    restart, not «Yazılım sıfırla», as what to do next.

    Usage on the machine:

        from bettercnc.adapter import LinuxCncAdapter
        from bettercnc.profiles import load_profile
        adapter = LinuxCncAdapter(load_profile("test-rig"))
    """

    #: Teleop jog and the stat position tuples address axes by this fixed
    #: index (axis.py:3297, command.c:448).
    AXIS_LETTERS = "XYZABCUVW"

    def __init__(
        self,
        profile: MachineProfile,
        joint_config: Optional[Dict] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        halcmd: Callable[[List[str]], Tuple[bool, str]] = _run_halcmd,
        proc_root: str = "/proc",
        observe: bool = False,
    ):
        try:
            import linuxcnc  # type: ignore
        except ImportError as exc:
            raise LinuxCncUnavailable(
                "`linuxcnc` Python modülü yok — LinuxCNC ile gelir, PyPI'da yok. "
                "Makinede (ya da sim/ içinde) çalıştırın; makine dışında "
                "--adapter mock kullanın."
            ) from exc

        try:
            import hal  # type: ignore
        except ImportError:
            hal = None

        self._linuxcnc = linuxcnc
        self._hal = hal
        self.profile = profile
        self._clock = clock
        self._sleep = sleep
        self._halcmd = halcmd
        self._proc_root = proc_root
        self._stat = linuxcnc.stat()
        self._command = linuxcnc.command()
        # A separate channel for the software estop, so it never shares a
        # channel (and its serial number bookkeeping) with an action in
        # progress on another thread. Each command() opens its own command and
        # status NML channels (Command_init, emcmodule.cc:817-838).
        self._estop_channel = linuxcnc.command()
        # A third, for DURDUR's first abort (stop_motion), which also runs
        # without app.Session's lock and so must not share the action
        # channel either. Task takes EMC_TASK_ABORT in every state and mode
        # (emctaskmain.cc:832, :944, :1057, :1156, :1234, :1315, :1410).
        self._stop_channel = linuxcnc.command()
        #: Counts the stops and software estops sent: a motion action takes
        #: it as it begins and sends nothing if it changed (_stopped_since).
        #: Written under _stop_lock, which is never held for more than the
        #: increment; read without it.
        self._stop_seq = 0
        #: The refusal for an action the latest stop overtook.
        self._stop_why = STOP_REFUSAL
        self._stop_lock = threading.Lock()
        #: Observer mode: see the class docstring.
        self._observe = observe
        # None in observer mode: AXIS keeps LinuxCNC's error messages.
        self._error = None if observe else linuxcnc.error_channel()
        self._link_action = OBSERVE_LINK_ACTION if observe else LINK_ACTION
        self._reset_action = OBSERVE_RESET_ACTION if observe else RESET_ACTION
        self._probe_status()

        config, ini = _read_profile_ini(profile)
        #: Read by _read_live_hal at every snapshot, like the link pins.
        self._input_pins = tuple(inp.hal_pin for inp in profile.inputs)
        self._timing_names = _stepgen_timing_names(config)
        self._joint_config = (
            joint_config if joint_config is not None else _joint_config(config)
        )
        self._alarms: List[AlarmEvent] = []
        self._mdi: List[MdiEntry] = []
        self._init_messages("")
        #: What a translation of LinuxCNC's errors may name (_drain_errors).
        self._facts = _message_facts(profile, self._joint_config, ini)
        #: The last LinuxCNC error the box took: what a later error may be a
        #: follow-up of (while open) or a consequence of
        #: (FOLLOW_UP_WINDOW_S).
        self._last_linuxcnc: Optional[_LastLinuxCnc] = None
        #: How many LinuxCNC errors the box has taken (_read_errors): what a
        #: program run compares to tell an error during it (_follow_run).
        self._errors_taken = 0
        #: The newest MDI line whose result is not known yet
        #: (_note_mdi_result); _mdi_sent_seq is the stop count its send
        #: was checked against (_mdi_entry).
        self._mdi_pending: Optional[_PendingMdi] = None
        self._mdi_sent_seq = 0
        #: The newest line, once done, for MDI_RESULT_WINDOW_S after its
        #: send: a late error may still be its own (_note_mdi_result).
        self._mdi_done: Optional[_PendingMdi] = None
        self._jog_step = 1.0  # same default as MockAdapter
        self._status_lost = False
        #: Commands sent on the action channel (see _send).
        self._sends = 0
        #: task_state at the previous snapshot, None before the first and
        #: after a status outage (see _note_task_state).
        self._last_task_state: Optional[int] = None
        #: Set by software_estop, so the snapshot that sees LinuxCNC leave ON
        #: knows the UI caused it (see _note_task_state).
        self._estop_sent = False
        #: The soft limits motion checks a homed joint jog against, joint ->
        #: (MIN_LIMIT, MAX_LIMIT): what sizes the recovery jog
        #: (motionrules.recovery_plan), the same the view model uses.
        self._windows = motionrules.soft_windows(config)
        #: The same limits by axis letter: what a homed jog is checked and
        #: cut against (motionrules.jog_refusal, plan WP06).
        self._windows_by_axis = motionrules.axis_windows(config)
        #: The switch a recovery jog is leaving, until the override is over
        #: (_follow_recovery); None otherwise.
        self._recovery: Optional[_Recovery] = None
        #: The program run being followed to its end (_follow_run); None
        #: while none runs.
        self._run: Optional[_Run] = None
        #: The run command this UI sent last for the program or MDI line in
        #: progress (state.RUN_CMD_*; MachineState.run_command), None once
        #: nothing runs (snapshot) or when this UI sent none.
        self._run_command: Optional[str] = None

        # Same lookup order as axis.py:3400-3403. None means "not configured"
        # (or not a number): jog is then refused rather than run at an
        # invented speed.
        self._jog_velocity = _ini_number(
            ini, ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"), ("TRAJ", "DEFAULT_LINEAR_VELOCITY")
        )
        # [DISPLAY]MAX_FEED_OVERRIDE in whole percent, read by
        # configmodel.max_feed_override_pct - the same value the Kontrol
        # screen disables the feed chips above. No INI at all: None, and feed
        # changes are refused.
        self._max_feed_pct: Optional[int] = config.max_feed_override_pct
        self._no_force_homing = _no_force_homing(ini)
        #: What the INI adds to the MDI gate: a tool change that moves,
        #: remapped codes (mdi.MdiContext; P0 cleanup).
        self._mdi_context = mdi_context_from_ini(ini)
        # How long software_estop_reset waits for LinuxCNC to leave ESTOP.
        # iocontrol re-reads emc-enable-in once per cycle (read_hal_inputs,
        # ioControl.cc:518-526, then esleep(emc_io_cycle_time), :1138), and
        # the estop-latch's ok-out rises only after iocontrol has raised
        # user-request-enable (ioControl.cc:1090-1096), so the reset needs
        # about two iocontrol cycles plus a task cycle. Three cycles is the
        # margin; not measured. Missing or <= 0: DEFAULT_EMCIO_CYCLE_TIME_S.
        io_cycle = _ini_number(ini, ("EMCIO", "CYCLE_TIME"))
        if io_cycle is None or io_cycle <= 0:
            io_cycle = DEFAULT_EMCIO_CYCLE_TIME_S
        self._estop_reset_deadline_s = 3 * io_cycle
        self._servo_period_ms: Optional[float] = config.servo_period_ms
        # LinuxCNC's own processes, for linuxcnc_lost(), named as the linuxcnc
        # script names them (reference/linuxcnc/scripts/linuxcnc.in): the NML
        # server, hardcoded (:556-558); iocontrol from [IO]IO, else
        # [EMCIO]EMCIO, else "io" (:545-546); task from [TASK]TASK, "emctask"
        # meaning linuxcnctask (:550-553).
        def ini_raw(section: str, key: str) -> Optional[str]:
            value = ini.get(section, key) if ini is not None else None
            return value.raw.strip() if value is not None else None

        task = ini_raw("TASK", "TASK") or "milltask"
        #: The two loops judged for a stall (_stalled_loops).
        self._loop_processes = (
            "linuxcnctask" if task == "emctask" else task,
            ini_raw("IO", "IO") or ini_raw("EMCIO", "EMCIO") or "io",
        )
        #: Every process that must be there: the two loops, the NML server,
        #: and rtapi_app, the uspace realtime process that runs motion and
        #: the board driver (scripts/realtime.in). With rtapi_app gone the
        #: status buffer still reads, so only the process list shows it.
        self._linuxcnc_processes = ("linuxcncsvr",) + self._loop_processes + ("rtapi_app",)
        #: name -> ((switches, cpu ticks), when they last changed), for _stalled_loops.
        self._loop_seen: Dict[str, Tuple[Tuple[int, int], float]] = {}
        self._hal_component = self._open_hal_component()

    def _probe_status(self) -> None:
        """Refuse to build unless LinuxCNC's status reads, twice over.

        Two polls, because of how the binding fails (POLL_SPOILED): the first
        poll in a process opens LinuxCNC's tool mmap, and if it is missing
        every later poll in the process fails for good. So:

        * the first poll raising linuxcnc.error means LinuxCNC is not running
          (check_stat, emcmodule.cc:277-283). The mmap was missing too, so
          this process can never read status again: it must exit, not wait.
        * the second poll raising SystemError means LinuxCNC runs but this
          process could not open the mmap - it looks under its own $HOME, so
          the UI runs as a different user (or HOME) than LinuxCNC.

        Started before LinuxCNC, the UI used to come up, show "bağlantı yok"
        once and then fail every request with that SystemError (sim/,
        2026-10-19).
        """
        L = self._linuxcnc
        for _ in range(2):
            try:
                self._stat.poll()
            except L.error as exc:
                raise LinuxCncUnavailable(
                    "LinuxCNC çalışmıyor ({}) — önce LinuxCNC'yi, sonra arayüzü "
                    "başlatın.".format(exc)
                ) from exc
            except SystemError as exc:
                raise LinuxCncUnavailable(POLL_SPOILED.format(_tool_mmap_path()) + ".") from exc

    # -- reads ---------------------------------------------------------------

    def note_startup(self, text: str) -> None:
        self._alarms.insert(0, AlarmEvent(_now(), "Kontrol sistemi yeniden başlatıldı — önceki durma: " + text,
                                          "kiosk", Severity.WARN))
        self._say("Kontrol sistemi yeniden başlatıldı — önceki durma: " + text, Severity.WARN)

    def snapshot(self) -> MachineState:
        try:
            s = self._poll()
        except self._linuxcnc.error as exc:
            return self._status_unreadable(exc)
        self._drain_errors()
        hal_values = self._read_hal()
        self._stop_on_link_fault(s, hal_values)
        self._note_task_state(s, hal_values)
        self._follow_recovery(s, hal_values)
        self._follow_run(s)
        if not self._running(s):
            self._run_command = None
        hal_values.update(self._read_live_hal())
        return self._map(s, hal_values)

    def ack_error(self, error_id: str) -> None:
        """«Tamam» on the error box (MachineAdapter.ack_error): local - no
        send, no poll, no halcmd - so neither guard applies and it works in
        observer mode, where app.Session lets it through too."""
        self._forget_error(error_id)

    def _stop_on_link_fault(self, s: Any, hal_values: Dict[str, Any]) -> None:
        """A board link fault with the machine ON: send ESTOP.

        The rig's HAL stops the machine on a watchdog bite (the `watchdog`
        net into the estop latch, test-rig.hal: "a comms failure stops the
        machine"). But a cable pull on the real driver raises io_error, and
        only io_error (hm2_eth.c:883) - a parameter, which no net can carry -
        and has_bit is not read while it is set (watchdog.c:38): the latch
        never trips, LinuxCNC says ON, and the board's own watchdog has
        already dropped the outputs, so any move would end in a following
        error. This does for io_error what the net does for the bit, within
        one snapshot (1 s), and reports it here and now as the link's loss
        (LINK_LOST); the transition it causes is marked as the UI's own so
        _note_task_state does not report it a second time, over whatever a
        reset pressed in between had to say.
        """
        L = self._linuxcnc
        if s.task_state != L.STATE_ON or not hal_values.get(LinkHealth.HAL_IO_ERROR):
            # has_bit is the latch's: the `watchdog` net trips it within a
            # servo period, long before this 1 Hz look could.
            return
        self._estop_sent = True
        self._send().state(L.STATE_ESTOP)
        self._alarms.insert(
            0, AlarmEvent(_now(), LINK_LOST + " (iletişim hatası; yazılımdan durduruldu)",
                          "hal", Severity.ERROR),
        )
        self._say("{}. {}".format(LINK_LOST, self._link_action), Severity.ERROR)

    def _note_task_state(self, s: Any, hal_values: Dict[str, Any]) -> None:
        """Say so when LinuxCNC leaves ON and the UI did not cause it.

        A watchdog bite, an estop chain opened in HAL, another GUI or halui:
        LinuxCNC leaves ON with no action of this UI, and nothing used to say
        so - the header and the estop panel changed, but the message line kept
        the last action's text (sim/, 2026-10-19: a watchdog bite left
        "Besleme %150 istendi..." on it). Now the snapshot that first sees the
        machine out of ON logs an ERROR alarm and, unless LinuxCNC's own
        error already says why (_linuxcnc_explains_stop: drained in this
        same snapshot or within FOLLOW_UP_WINDOW_S - a limit trip's error can
        be drained a snapshot before the status shows the machine off - and
        not a mere refusal), sets the message and the error box.

        The board link's loss is said whatever LinuxCNC forwarded around it,
        as _stop_on_link_fault says it for io_error: what LinuxCNC forwards
        then is the loss's consequence - hostmot2's own "Watchdog has bit!
        (set the .has-bit pin to False to resume)" (hal/drivers/
        mesa-hostmot2/watchdog.c:50, an RTAPI_MSG_ERR, which motion puts in
        its error queue, motion.c:207-214) or a following error - and only
        LINK_LOST names what to do.

        A limit trip is said as one (plan WP05): with no link loss, a switch
        that motion's flags show pressed and not overridden in this
        snapshot's status is what stopped the machine (control.c:807-826),
        jog from this screen or not. The alarm and the box then carry
        messages.limit_trip_text - the same text the drain made of
        LinuxCNC's own report, with what to do - never "komut bu ekrandan
        gelmedi". The box is not said twice: LinuxCNC's report of the trip,
        drained within FOLLOW_UP_WINDOW_S with the same text, stays (its id
        and raw line with it).

        software_estop leaves ON on purpose and writes its own message; it
        sets _estop_sent so the transition it causes is not reported again.
        The flag is consumed by any snapshot that finds the machine out of
        ON, so it cannot later excuse a stop the UI did not cause.
        """
        L = self._linuxcnc
        task_state = s.task_state
        previous, self._last_task_state = self._last_task_state, task_state
        ours = self._estop_sent
        if task_state != L.STATE_ON:
            self._estop_sent = False
        if previous != L.STATE_ON or task_state == L.STATE_ON or ours:
            return
        link = hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or hal_values.get(
            LinkHealth.HAL_IO_ERROR
        )
        trips = () if link else motionrules.unmasked_trips(self._state_of(s), self.profile)
        if trips:
            self._note_limit_trip(trips)
            return
        if link:
            text, action = LINK_LOST, self._link_action
        elif task_state == L.STATE_ESTOP:
            text = "Makine acil stopa geçti — komut bu ekrandan gelmedi"
            action = self._reset_action
        else:
            text = "Makine kapatıldı — komut bu ekrandan gelmedi"
            action = self._reset_action
        self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.ERROR))
        if link or not self._linuxcnc_explains_stop():
            self._say("{}. {}".format(text, action), Severity.ERROR)

    def _note_limit_trip(self, trips: Sequence[motionrules.LimitTrip]) -> None:
        """_note_task_state's words for a limit trip: the first tripped
        joint's switches (motionrules.limit_trips' order). In observer mode
        the way off is AXIS's."""
        first = trips[0]
        text = messages.limit_trip_text(
            first.axis, [t.label for t in trips if t.joint == first.joint],
            motionrules.OBSERVE_RECOVER_HINT if self._observe else messages.RECOVER_HINT,
        )
        self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.ERROR))
        box = self._last_error
        if box is not None and box.text == text and self._linuxcnc_explains_stop():
            return  # LinuxCNC's report of this trip, in the box already
        self._say(text, Severity.ERROR)

    def _follow_recovery(self, s: Any, hal_values: Dict[str, Any]) -> None:
        """After a recovery jog (recover_limit), the snapshot that finds
        LinuxCNC's limit override over says how it went, once: the override
        ends when the jog does (control.c:1263-1283 free mode). The switch
        free and the machine ON: INFO, protection is back (and, unhomed,
        home next). The switch still pressed with the machine off - the jog
        was too short, and with the mask gone motion stopped the machine
        again (control.c:807-826): ERROR, press again. Free but off (an
        estop or DURDUR cut the jog, say): nothing - that stop said its own
        word. Still pressed and ON is the instant before motion trips: the
        next snapshot decides. While the board link is lost the flags are
        stale (state.HardLimit): the following ends unsaid."""
        r = self._recovery
        if r is None:
            return
        if hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or hal_values.get(LinkHealth.HAL_IO_ERROR):
            self._recovery = None
            return
        joints = list(s.joint)   # each access builds 16 dicts (emcmodule.cc:612-618)
        if joints and joints[0]["override_limits"]:
            return
        j = joints[r.joint] if r.joint < len(joints) else {}
        pressed = bool(j.get("min_hard_limit" if r.end == "-" else "max_hard_limit"))
        on = s.task_state == self._linuxcnc.STATE_ON
        if pressed and on:
            return
        self._recovery = None
        if pressed:
            text = motionrules.still_on_text(r.axis)
            self._alarms.insert(0, AlarmEvent(_now(), "Limitten kurtarma yetmedi: " + text,
                                              "limit", Severity.ERROR))
            self._say(text, Severity.ERROR)
        elif on:
            text = motionrules.released_text(r.label, bool(s.homed[r.joint]))
            self._alarms.insert(0, AlarmEvent(_now(), "Limitten kurtarma: " + text,
                                              "limit", Severity.INFO))
            self._say(text)

    def _program_busy(self, s: Any) -> bool:
        """A program in AUTO with the interpreter not idle, paused or not."""
        L = self._linuxcnc
        return s.task_mode == L.MODE_AUTO and s.interp_state != L.INTERP_IDLE

    def _begin_run(self, s: Any, seq: int) -> None:
        """Follow the run «Başlat» or «Adım» just started (_follow_run)."""
        if self._run is None:
            self._run = _Run(str(s.file or ""), seq, self._errors_taken)

    def _follow_run(self, s: Any) -> None:
        """Say how a program run ended, once, at the snapshot that first
        finds the interpreter idle after it (plan WP09). A run is followed
        from «Başlat» or «Adım» here (_begin_run), or from the first
        snapshot that sees one (started from AXIS or halui, say). Every
        caller drains the error channel first, on the same poll.

        - the machine left ON meanwhile: nothing - _note_task_state or the
          estop said why;
        - a stop or the estop since it began: after DURDUR, STOPPED_TEXT,
          and only while the line still carries the stop's own words (an
          estop's, or a later action's, stand);
        - a LinuxCNC error the box took since it began: FAILED_TEXT (WARN).
          Any error, a refusal too: a move past the soft limits is one
          (messages.is_refusal) and ends the program (tpAbort and motion's
          error flag, command.c:1002-1007), so "bitti" is never said after
          an error - an error that left the program running reads as a
          failure too (docs/11 Known gaps). The error itself stays in the
          box. Task aborts the program on an interpreter error
          (emctaskmain.cc:546-551) and on a motion or I/O error
          (:3498-3520);
        - otherwise FINISHED_TEXT: the program ran to its end (M2/M30).

        In observer mode nothing is said: no error channel is read then,
        so an error could not be told from an end."""
        run = self._run
        if self._program_busy(s):
            if run is None:
                self._run = _Run(str(s.file or ""), self._stop_seq, self._errors_taken)
            return
        if run is None:
            return
        self._run = None
        if self._observe or s.task_state != self._linuxcnc.STATE_ON:
            return
        name = os.path.basename(run.file) or run.file
        if self._stop_seq != run.stop_seq:
            if self._stop_why != STOP_REFUSAL:
                return
            text = programs.STOPPED_TEXT.format(name)
            self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.INFO))
            for said in (STOP_SENT, STOPPED, STOP_NOT_IDLE):
                self._say_if(said, text)
            return
        if self._errors_taken != run.error:
            text = programs.FAILED_TEXT.format(name)
            self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.WARN))
            self._say(text, Severity.WARN)
            return
        # A program stepped to its end leaves motion's pause set
        # (MachineState.pause_left_at_end): it ended, and «Durdur» clears it.
        text = (programs.FINISHED_PAUSED_TEXT if getattr(s, "paused", False)
                else programs.FINISHED_TEXT).format(name)
        self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.INFO))
        self._say(text)

    def _note_status_lost(self, exc: Exception) -> None:
        """Log a status outage: the alarm once per outage, not on every UI
        poll. Whether and how fast stat.poll() starts raising when LinuxCNC
        shuts down is not measured."""
        if not self._status_lost:
            self._status_lost = True
            self._say("LinuxCNC'ye bağlantı yok — makine durumu okunamıyor.", Severity.ERROR)
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "LinuxCNC durum kanalı okunamıyor ({})".format(exc),
                    "linuxcnc", Severity.ERROR,
                ),
            )

    def _status_unreadable(self, exc: Exception) -> MachineState:
        """The snapshot while stat.poll() raises (check_stat,
        emcmodule.cc:277-283).

        Showing the last good stat would present a dead machine as live, and
        mapping an empty stat would invent readings (DRO +0.000, feed 100 %,
        G54, "not moving"). So the state says status_known=False and carries
        no positions, no limit flags and None for the motion and interpreter
        fields (MachineState.as_status_unknown); the view shows "—" for what
        LinuxCNC's status would have filled. The link and drive power come
        from HAL pins, which are read separately and keep their own meaning.
        """
        self._note_status_lost(exc)
        self._last_task_state = None
        hal_values = self._read_hal()
        hal_values.update(self._read_live_hal())
        return self._map(_NO_STATUS, hal_values).as_status_unknown()

    def _state_of(self, s: Any, hal_values: Optional[Mapping[str, Any]] = None) -> MachineState:
        """`s` mapped without the message band, the alarms or the live HAL
        reads, for an action's rule that reads motion's own status - the
        limit flags, homing, the modes (motionrules) - and, given
        `hal_values` (_read_hal), the link. Not on the 1 Hz path unless a
        transition or a recovery asks for it."""
        return map_linuxcnc_state(
            stat=s,
            hal_values=dict(hal_values or {}),
            profile=self.profile,
            joint_axis_map=[c["axis"] for _, c in sorted(self._joint_config.items())],
            joint_config=self._joint_config,
            joint_numbers=sorted(self._joint_config),
        )

    @staticmethod
    def _override_on(s: Any) -> bool:
        """stat.joint[0]["override_limits"]: motion's limit override, one
        value for every joint (taskintf.cc:963)."""
        joints = list(s.joint)
        return bool(joints and joints[0]["override_limits"])

    def _map(self, stat: Any, hal_values: Dict[str, Any]) -> MachineState:
        hal_values["jog_step_mm"] = self._jog_step
        # Plan WP09: motion's line over the loaded file's line count, while a
        # program runs or is paused. line_count is cached by the file's
        # version (counted at load): one os.stat here, no read.
        if getattr(stat, "file", None) and self._program_busy(stat):
            hal_values["program_progress"] = programs.progress(
                _opt_int(getattr(stat, "motion_line", None)), programs.line_count(stat.file))
        (hal_values["message"], hal_values["message_severity"],
         hal_values["last_error"]) = self._message_band()
        hal_values["servo_period_ms"] = self._servo_period_ms
        hal_values["run_command"] = self._run_command
        return map_linuxcnc_state(
            stat=stat,
            hal_values=hal_values,
            profile=self.profile,
            joint_axis_map=[
                c["axis"] for _, c in sorted(self._joint_config.items())
            ],
            joint_config=self._joint_config,
            alarms=tuple(self._alarms),
            mdi_history=tuple(self._mdi),
            joint_numbers=sorted(self._joint_config),
        )

    def _open_hal_component(self) -> Any:
        """Create an empty HAL component so `hal.get_value` works at all.

        get_value raises "Cannot call before creating component" until this
        process has called hal_init() (halmodule.cc:1010-1014; hal_shmem_base
        is set only in hal_init, hal_lib.c:217). halcmd does the same for the
        same reason (halcmd.c:103). The component has no pins, so it never
        joins a net - the UI stays a passive observer.

        Side effect, accepted: the component makes this process a HAL user
        component. When LinuxCNC shuts down, its script runs `halcmd unload
        all` (scripts/linuxcnc.in:707), which sends SIGTERM to every user
        component's pid (do_unloadusr_cmd, halcmd_commands.cc:1260-1290).
        Nothing here handles SIGTERM, so the whole UI process - web server
        included - exits with LinuxCNC. The UI cannot do anything useful
        without LinuxCNC anyway; restart it after LinuxCNC.
        """
        if self._hal is None:
            return None
        try:
            component = self._hal.component("bettercnc-ui-{}".format(os.getpid()))
            component.ready()
        except self._hal.error as exc:
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "HAL bileşeni açılamadı ({}) — bağlantı pinleri okunamıyor".format(exc),
                    "hal", Severity.WARN,
                ),
            )
            return None
        return component

    def _read_hal(self) -> Dict[str, Any]:
        """Read the link-health pins. An unreadable pin is None, never 0.

        `connected` is True only if at least one pin could be read: with no
        readable pin nothing is known about the link, and showing it healthy
        would present an unmeasured state as measured.

        No "estop_input" is read: no profile wires a hardware estop input yet
        (test-rig: absent, marble: TODO), so drive power stays UNKNOWN. A
        profile that wires one must name its HAL pin, and this method must
        read that pin here - never stat.estop (see map_linuxcnc_state).
        """
        if self._hal is None:
            return {"connected": False}
        out: Dict[str, Any] = {}
        for pin in HAL_LINK_PINS:
            try:
                out[pin] = self._hal.get_value(pin)
            except RuntimeError:
                # No component, or no such pin (halmodule.cc:1010-1014, 1078-1079).
                out[pin] = None
        out["connected"] = any(out[pin] is not None for pin in HAL_LINK_PINS)
        return out

    def _read_live_hal(self) -> Dict[str, Any]:
        """Read the switch inputs and the stepgen timing, as _read_hal reads
        the link pins: hal.get_value, None for a name it cannot read, never a
        default. Read separately from the link so the reset's link checks
        (_clear_link_faults) do not read them on every turn.

        The inputs land under their pin names, which map_linuxcnc_state looks
        up per profile input; the timing as "stepgen_timing", (name, value)
        pairs in stepgen order. Without the hal module nothing is read: the
        inputs are then unknown, not free, and no timing is shown.
        """
        if self._hal is None:
            return {}
        out: Dict[str, Any] = {pin: self._hal_value(pin) for pin in self._input_pins}
        out["stepgen_timing"] = tuple(
            (name, self._hal_value(name)) for name in self._timing_names
        )
        return out

    def _hal_value(self, name: str) -> Any:
        """hal.get_value(name), None for a name it cannot read."""
        try:
            return self._hal.get_value(name)
        except RuntimeError:
            # No component, or no such pin or parameter
            # (halmodule.cc:1010-1014, 1078-1079).
            return None

    def _input_values(self) -> Tuple[Tuple[str, Optional[bool]], ...]:
        """The switch inputs now, as MachineState.input_values carries them
        - for the drain to name the switch a limit trip stands on. Empty
        without the hal module."""
        if self._hal is None:
            return ()
        return tuple((pin, _opt_bool(self._hal_value(pin))) for pin in self._input_pins)

    def _drain_errors(self) -> Optional[_Drained]:
        """Pull from error_channel into the alarm log, newest first. Returns
        the error that took the error box this drain (_Drained: LinuxCNC's
        text and the operator's), None when no new error came.

        Every message lands in the log with LinuxCNC's own text. The four
        text and display kinds are INFO and nothing more. An error kind is
        an ERROR, and one per drain also becomes the operator message and
        the error box (_say, MachineState.last_error), in the operator's
        words (messages.operator_text): the FIRST that is not a follow-up of
        the last error the box took (messages.is_follow_up). LinuxCNC
        reports the specific error first and the generic one after it -
        "Linear move on line 0 would exceed Z's positive limit", the same
        for joint 2, then "invalid params in linear command" (command.c:
        216-268, then :1003) - and until plan WP04 every error overwrote the
        message, so the generic one won: the text that confused the
        operator on the rig.

        The rule holds across drains: task forwards motion's errors one per
        cycle (taskintf.cc:2024-2028), and in sim/ a POST's snapshot took
        the first report alone and the rest came later - once even after
        the next MDI line had been sent. So the last error the box took
        keeps taking follow-ups until LinuxCNC's last word on that refusal
        (messages.ends_report) or FOLLOW_UP_WINDOW_S; a follow-up is never
        "new" (not returned, so no MDI line takes it as its result).

        A kind Error_poll does not know - including the -1 a failed NML
        read gives, "unrecognized error -1" (emcmodule.cc:1588-1593) -
        counts as an ERROR and ends the drain. At most
        _MAX_ERRORS_PER_DRAIN messages are read per call; the rest wait for
        the next snapshot.

        Every drain follows a poll (snapshot, _mdi_entry, _ensure_mode,
        load_program), so it also gives the pending MDI line its result from
        that fresh stat (_note_mdi_result): an error drained inside an action
        is attributed as one drained by a snapshot.

        In observer mode there is no error channel and nothing is drained:
        emcError is a "queue" buffer (configs/common/linuxcnc.nml:10), so a
        message read here never reaches AXIS. LinuxCNC's refusals then show
        in AXIS only.
        """
        if self._error is None:
            return None
        first = self._read_errors()
        self._note_mdi_result(self._stat, first)
        return first

    def _read_errors(self) -> Optional[_Drained]:
        """_drain_errors' reading: the log, the follow-ups, the box."""
        L = self._linuxcnc
        info_kinds = (L.NML_TEXT, L.NML_DISPLAY, L.OPERATOR_TEXT, L.OPERATOR_DISPLAY)
        first: Optional[_Drained] = None
        inputs: Optional[Tuple[Tuple[str, Optional[bool]], ...]] = None
        for _ in range(_MAX_ERRORS_PER_DRAIN):
            try:
                got = self._error.poll()
            except L.error:
                # "Error buffer invalid" (Error_poll, emcmodule.cc:1562-1565);
                # snapshot reports the outage through the status channel.
                return first
            if not got:
                return first
            kind, text = got
            text = str(text).strip()
            if kind in info_kinds:
                self._alarms.insert(0, AlarmEvent(_now(), text, "linuxcnc", Severity.INFO))
                continue
            self._alarms.insert(0, AlarmEvent(_now(), text, "linuxcnc", Severity.ERROR))
            if self._is_follow_up(text):
                if messages.ends_report(text):
                    self._last_linuxcnc = self._last_linuxcnc._replace(open=False)
            elif first is None:
                if inputs is None:
                    inputs = self._input_values()   # only for an error that is shown
                first = _Drained(text, messages.operator_text(text, self._facts, inputs))
                self._say(first.text, Severity.ERROR, raw=text, source=SOURCE_LINUXCNC)
                self._last_linuxcnc = _LastLinuxCnc(
                    text, self._clock(), not messages.ends_report(text))
                self._errors_taken += 1
            if kind not in (L.NML_ERROR, L.OPERATOR_ERROR):
                return first
        return first

    def _is_follow_up(self, raw: str) -> bool:
        """messages.is_follow_up against the last LinuxCNC error the box
        took, while more of its reports may come (open) and it is recent
        (FOLLOW_UP_WINDOW_S)."""
        last = self._last_linuxcnc
        return (
            last is not None
            and last.open
            and self._clock() - last.at <= FOLLOW_UP_WINDOW_S
            and messages.is_follow_up(raw, last.raw)
        )

    def _linuxcnc_explains_stop(self) -> bool:
        """The error box took a LinuxCNC error within FOLLOW_UP_WINDOW_S -
        in this snapshot's drain too, which sets _last_linuxcnc - that can
        be why the machine left ON: not a refusal (messages.is_refusal),
        after which LinuxCNC stays ON."""
        last = self._last_linuxcnc
        return (
            last is not None
            and self._clock() - last.at <= FOLLOW_UP_WINDOW_S
            and not messages.is_refusal(last.raw)
        )

    # -- helpers -------------------------------------------------------------

    def _poll(self) -> Any:
        """stat.poll(), returning the stat. Raises linuxcnc.error while the
        status buffer is invalid; callers are snapshot() and guarded actions.
        The first good poll after an outage logs the recovery, once.

        A SystemError from the binding (POLL_SPOILED) is raised as
        linuxcnc.error, so snapshot() and _refused_without_status treat it as
        lost status instead of letting it escape app.Session. _probe_status
        keeps it from happening at startup; this covers anything that spoils
        poll() later.

        So is a stuck LinuxCNC loop (_stalled_loops): its status buffer still
        reads, but stale, and a command sent to a stuck task would spin for up
        to 5 s holding the GIL (emcmodule.cc:209, :233-250). The screen then
        shows the status as unknown, and actions refuse without sending.
        """
        stalled = self._stalled_loops()
        if stalled:
            raise self._linuxcnc.error(LOOP_STALLED.format(", ".join(stalled), LOOP_STALL_S))
        try:
            self._stat.poll()
        except SystemError as exc:
            raise self._linuxcnc.error(POLL_SPOILED.format(_tool_mmap_path())) from exc
        if self._status_lost:
            self._status_lost = False
            self._say("LinuxCNC durumu yeniden okunuyor.")
            self._alarms.insert(
                0, AlarmEvent(_now(), "LinuxCNC durum kanalı yeniden okunuyor",
                              "linuxcnc", Severity.INFO),
            )
        return self._stat

    def _send(self) -> Any:
        """The action command channel, for a send: counts it, so a lost
        status channel can be reported with whether anything went out
        (_refused_without_status). Waits use self._command directly."""
        self._sends += 1
        return self._command

    def _wait(self) -> int:
        """Bounded wait_complete; see WAIT_TIMEOUT_S. Returns the RCS status,
        or -1 when the wait timed out (emcmodule.cc:230)."""
        return self._command.wait_complete(WAIT_TIMEOUT_S)

    def _all_homed(self, s: Any) -> bool:
        """The first stat.joints joints, like axis.py:1947-1952 and task's
        all_homed() (emctaskmain.cc:147-152). stat.homed has 16 entries
        (emcmodule.cc:541-547); the unused ones are 0."""
        homed = list(s.homed)[: int(s.joints)]
        return bool(homed) and all(homed)

    def _homing(self, s: Any) -> bool:
        """Any of the first stat.joints joints is homing (stat.joint[n]
        ["homing"], emcmodule.cc:598)."""
        return any(j["homing"] for j in list(s.joint)[: int(s.joints)])

    def _running(self, s: Any) -> bool:
        """A program or MDI command is running (or paused), axis.py:963-966."""
        L = self._linuxcnc
        return s.task_mode in (L.MODE_AUTO, L.MODE_MDI) and s.interp_state != L.INTERP_IDLE

    def _ensure_mode(self, mode: int, require_on: bool = True) -> bool:
        """axis.py:986-992, with a bounded wait. Returns whether the task is
        now in `mode` and, unless require_on is False, the machine is ON - an
        estop sent from another thread during the wait ends the action here.

        On failure LinuxCNC's reason (e.g. "Ignoring task mode change while
        jogging", emctask.cc:269-272) is drained now - into the alarm log and
        the error box (_drain_errors) - so the caller's Turkish refusal, said
        after it, is the operator message.

        Leaving MDI mode with an MDI line still unresolved, the line first
        gets its result from this poll and the errors queued now
        (_drain_errors, _note_mdi_result): the errors of the action that
        switches would otherwise come within MDI_RESULT_WINDOW_S of the send
        and be taken for the line's - a jog refused right after a line that
        ran marked the line rejected with the jog's reason (review of WP04).
        """
        s = self._poll()
        if s.task_mode != mode:
            if s.task_mode == self._linuxcnc.MODE_MDI and self._mdi_pending is not None:
                self._drain_errors()
            self._send().mode(mode)
            self._wait()
            s = self._poll()
        off = require_on and s.task_state != self._linuxcnc.STATE_ON
        if off or s.task_mode != mode:
            self._drain_errors()
            return False
        return True

    def _set_motion_teleop(self, enable: bool) -> bool:
        """axis.py:273-279, with a bounded wait. Returns whether the machine
        is ON and motion is now in the requested mode.

        Motion honours a teleop request only while in position (control.c:
        927-958 drops it). Leaving teleop first aborts every teleop jog
        (EMCMOT_FREE, command.c:556-567) and switches once in position
        (control.c:961-972). Either way the mode is read back rather than
        assumed.
        """
        L = self._linuxcnc
        self._send().teleop_enable(1 if enable else 0)
        self._wait()
        s = self._poll()
        wanted = L.TRAJ_MODE_TELEOP if enable else L.TRAJ_MODE_FREE
        return s.task_state == L.STATE_ON and s.motion_mode == wanted

    def _switch_refused(self, what_failed: str, what: str) -> str:
        """Refusal text after a mode switch did not take. An estop that
        arrived meanwhile (from another thread) is reported as such."""
        if self._stat.task_state != self._linuxcnc.STATE_ON:
            return "Makine devre dışı — {}.".format(what)
        return "{} — {}.".format(what_failed, what)

    def _select_jog_mode(self) -> Optional[bool]:
        """axis.py get_jog_mode (:3225-3242). Returns jjogmode (True = joint
        jog), or None when motion did not switch to the mode the jog needs.

        Before homing motion is in free mode, where an axis jog carries joint
        number -1 and motion drops it with "Joint jog requested for undefined
        joint number=-1 (min=0,max=N)" (command.c:436-443). After homing it is
        in teleop and drops a joint jog with "JOG_INCR Mode is TELEOP, cannot
        jog joint" (command.c:421-426, :459).
        """
        L = self._linuxcnc
        s = self._poll()
        if s.kinematics_type == L.KINEMATICS_IDENTITY and self._all_homed(s):
            teleop = True
        else:
            # Keep what motion is in; another GUI (halui) may have changed it.
            teleop = s.motion_mode != L.TRAJ_MODE_FREE
        wanted = L.TRAJ_MODE_TELEOP if teleop else L.TRAJ_MODE_FREE
        if s.motion_mode != wanted and not self._set_motion_teleop(teleop):
            return None
        return not teleop

    def _joint_for_axis(self, axis: str) -> int:
        """First joint carrying `axis` (axis.py uses the first occurrence for a
        duplicated letter, :1938-1942). KeyError for an axis the profile lacks."""
        for jnum, cfg in sorted(self._joint_config.items()):
            if cfg["axis"] == axis:
                return jnum
        raise KeyError(axis)

    def _note_stop(self, why: str) -> None:
        """A stop (DURDUR) or the software estop is about to go out: every
        motion action that began before it sends nothing more
        (_stopped_since). Called before the command is sent, so an action
        checking in between refuses too."""
        with self._stop_lock:
            self._stop_seq += 1
            self._stop_why = why

    def _stopped_since(self, seq: int) -> bool:
        """Whether a stop or the software estop came after `seq` (the
        _stop_seq an action took as it began). Each action asks right before
        its motion command - home(-1), jog, auto(RUN), mdi - and on True
        refuses with _stop_why instead of sending it. A stop that lands
        after this check and before the send is closed by stop_followup's
        second abort, which app.Session sends as soon as this action
        returns."""
        return self._stop_seq != seq

    # -- writes --------------------------------------------------------------

    def send_mdi(self, command: str) -> MdiEntry:
        """Check and send one MDI line; the MdiEntry recorded for it.

        A line sent is MDI_SENT ("Gönderildi, sonuç bekleniyor."): LinuxCNC
        decides later - motion refuses a target past the soft limits only
        when task hands it the move (command.c:1002-1003) - so the entry
        follows the line until its result is seen (_note_mdi_result), the
        newest line only: a line still unresolved when another is sent says
        it was not followed (MDI_UNTRACKED_NOTE). A refusal here is
        MDI_REFUSED."""
        command = (command or "").strip()
        # None: a guard refused (LinuxCNC's status lost, observer mode), and
        # the refusal is already the operator message.
        entry = self._mdi_entry(command) or MdiEntry(
            command, False, self._message, MDI_REFUSED)
        self._mdi.append(entry)
        text = "MDI: {} — {}".format(command or "(boş)", entry.note)
        if entry.status != MDI_SENT:
            self._say(text, Severity.WARN)
            return entry
        old = self._mdi_pending
        if old is not None:
            self._mdi[old.index] = self._mdi[old.index]._replace(note=MDI_UNTRACKED_NOTE)
        self._mdi_done = None
        self._mdi_pending = _PendingMdi(
            len(self._mdi) - 1, self._clock(), self._mdi_sent_seq, text)
        self._say(text)
        return entry

    def _note_mdi_result(self, s: Any, drained: Optional[_Drained]) -> None:
        """The pending MDI line's result, from a fresh stat `s` and the new
        error of the drain that came with it (plan WP04's rule; called by
        every _drain_errors):

        - an error, while task is in MDI mode or within MDI_RESULT_WINDOW_S
          of the send: MDI_REJECTED, with the error's operator text;
        - still in MDI mode with the interpreter busy: still running, or
          queued behind a line that is - nothing yet;
        - otherwise the line is over: MDI_DONE, unless a stop or the
          software estop came after its send (MDI_CUT_BY_STOP /
          MDI_CUT_BY_ESTOP) or the machine is no longer ON
          (MDI_CUT_MACHINE_OFF) - then MDI_REJECTED, as it may not have
          completed;
        - a line already done is still rejected by an error that comes
          within MDI_RESULT_WINDOW_S of its send with task still in MDI
          mode (nothing else has run since).

        Normally a stat that shows the line over comes with its error in
        the same drain: task forwards motion's error in the loop's
        emcMotionUpdate (emctaskmain.cc:3411) no later than the cycle whose
        abort idles the interpreter (:2569-2572 after an execution error,
        :3498-3502 after motion's), and the snapshot reads the status
        before it drains. But the forwarding skips a cycle whose motion
        status read fails (taskintf.cc:2007-2009), and in sim/ reports of
        one refusal arrived well over a cycle apart - hence the window.
        """
        L = self._linuxcnc
        in_mdi = s.task_mode == L.MODE_MDI
        p = self._mdi_pending
        if p is None:
            late = self._mdi_done
            if (late is not None and drained is not None and in_mdi
                    and self._clock() - late.sent_at <= MDI_RESULT_WINDOW_S):
                self._mdi_pending = late
                self._resolve_mdi(MDI_REJECTED, drained.text)
            return
        if drained is not None and (in_mdi or self._clock() - p.sent_at <= MDI_RESULT_WINDOW_S):
            self._resolve_mdi(MDI_REJECTED, drained.text)
        elif in_mdi and s.interp_state != L.INTERP_IDLE:
            return
        elif self._stop_seq != p.stop_seq:
            self._resolve_mdi(MDI_REJECTED, (
                MDI_CUT_BY_ESTOP if self._stop_why == ESTOP_REFUSAL else MDI_CUT_BY_STOP))
        elif s.task_state != L.STATE_ON:
            self._resolve_mdi(MDI_REJECTED, MDI_CUT_MACHINE_OFF)
        else:
            self._resolve_mdi(MDI_DONE, MDI_DONE_NOTE)

    def _resolve_mdi(self, status: str, note: str) -> None:
        """Give the pending MDI line its result; its last message on the
        line, if nothing has been said since, gives way to the result."""
        p = self._mdi_pending
        self._mdi_pending = None
        entry = self._mdi[p.index]._replace(status=status, note=note)
        self._mdi[p.index] = entry
        text = "MDI: {} — {}".format(entry.command, note)
        self._say_if(p.message, text, Severity.INFO if status == MDI_DONE else Severity.WARN)
        self._mdi_done = p._replace(message=text) if status == MDI_DONE else None

    @_not_while_observing
    @_refused_without_status
    def _mdi_entry(self, command: str) -> MdiEntry:
        """send_mdi's checks and send; the MdiEntry to record.

        LinuxCNC's errors already queued are drained first, which gives the
        pending line its result with this poll (_drain_errors): they are
        older than this line, and drained after its send they would be
        taken for its errors. (A follow-up of an earlier refusal that comes
        later still is not taken for anything.)"""
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        if self._no_force_homing and _homing_sequence_gap_possible(self._state_of(s)):
            # Between two groups of a Home All no joint reads homing, yet the
            # switch to MDI mode would abort the sequence (emctask.cc:
            # 286-289): looked at again, as jog does (HOMING_GAP_RECHECK_S).
            # Without NO_FORCE_HOMING "not homed" refuses there anyway.
            self._sleep(HOMING_GAP_RECHECK_S)
            s = self._poll()
        self._drain_errors()
        refusal = mdi_refusal(
            command,
            machine_on=s.task_state == L.STATE_ON,
            homed=self._all_homed(s),
            no_force_homing=self._no_force_homing,
            # MDI mode with a command still running is NOT refused: LinuxCNC
            # queues MDI lines (emctaskmain.cc mdi_execute_queue).
            program_running=s.task_mode == L.MODE_AUTO and s.interp_state != L.INTERP_IDLE,
            # ...but a line typed while one is paused would run on «Devam»
            # with it (mdi_refusal; the program_paused rule of
            # map_linuxcnc_state).
            paused=bool(s.paused) or s.interp_state == L.INTERP_PAUSED,
            # ...and nothing while a joint homes: entering MDI mode aborts
            # (emctask.cc:286-289) - the homing too (mdi_refusal).
            homing=self._homing(s),
            context=self._mdi_context,
        )
        if refusal is not None:
            return MdiEntry(command, False, refusal, MDI_REFUSED)
        if not self._ensure_mode(L.MODE_MDI):
            return MdiEntry(command, False, self._switch_refused(
                "MDI moduna geçilemedi", "MDI kabul edilmedi"), MDI_REFUSED)
        if self._stopped_since(seq):
            return MdiEntry(command, False, self._stop_why, MDI_REFUSED)
        try:
            self._send().mdi(command)
        except ValueError:
            # Longer than the 254-character NML field (emcmodule.cc:985-988).
            return MdiEntry(command, False, "Komut çok uzun — LinuxCNC satır sınırı aşıldı.",
                            MDI_REFUSED)
        self._mdi_sent_seq = seq
        return MdiEntry(command, True, MDI_SENT_NOTE, MDI_SENT)

    def software_estop(self) -> None:
        """Send STATE_ESTOP on the dedicated channel. No wait, no poll.

        Safe to call from another thread while an action is in progress, and
        while LinuxCNC's status cannot be read: stat.poll() would raise then
        (emcmodule.cc:277-283), and the estop must still go out.

        The binding ignores whether the send succeeded (state(),
        emcmodule.cc:995-1010, drops emcSendCommand's result), and the send
        itself waits - holding the GIL - until task echoes the command: for
        state() about one [EMCIO]CYCLE_TIME, up to 5 s if task or iocontrol
        stalls (see WAIT_TIMEOUT_S). So the message says "sent"; whether
        LinuxCNC left ON shows in the next snapshot's task_state.

        On the test rig LinuxCNC then shows ESTOP_RESET, not ESTOP:
        iocontrol's user-enable-out, which ESTOP clears (ioControl.cc:1086),
        is not wired into the rig's estop chain (configs/test-rig/
        test-rig.hal, ESTOP section), so emc-enable-in stays true and only
        motion is disabled (determineState, emctask.cc:419-430). Either state
        refuses motion.

        Its message is an ERROR, so it is also the error box
        (MachineState.last_error): an action finishing on another thread
        after it writes its own message on the line, but the box keeps the
        estop until the operator acknowledges it (plan WP04).
        """
        # Before the send, so a snapshot on another thread that sees LinuxCNC
        # leave ON already knows the UI caused it (_note_task_state), and an
        # action in progress sends no motion command after it (_note_stop).
        self._estop_sent = True
        self._note_stop(ESTOP_REFUSAL)
        self._estop_channel.state(self._linuxcnc.STATE_ESTOP)
        self._say(ESTOP_SENT, Severity.ERROR)
        self._alarms.insert(
            0, AlarmEvent(_now(), "Yazılım acil stop gönderildi", "task", Severity.ERROR)
        )

    @_not_while_observing
    @_refused_without_status
    def software_estop_reset(self) -> None:
        """Clear the software estop and switch the machine on.

        Note what this does NOT do: it cannot restore drive power. LinuxCNC
        leaves estop only when iocontrol's emc-enable-in is true
        (ioControl.cc:523-526, emctask.cc:419-430), so with the chain open the
        reset does not take and ON is not sent.

        Already ON: nothing to do. Refused without sending anything while a
        program or MDI command runs: ESTOP_RESET runs emcTaskAbort()
        (emctask.cc:340-349) in every mode, which would stop it. A program
        runs only in ON, so that guard is defence in depth behind the ON
        check.

        From ESTOP, ESTOP_RESET is sent and the stat is polled until LinuxCNC
        leaves ESTOP or 3 x [EMCIO]CYCLE_TIME passes (see __init__). Only
        then is the reset refused, without naming a cause: an open chain and
        a latch slower than the deadline look the same from here. From
        ESTOP_RESET (on the test rig, where a software estop leaves the latch
        closed; see software_estop) only ON is sent.

        Homing survives: a software estop unhomes only joints with
        VOLATILE_HOME (emctask.cc:364 emcJointUnhome(-2), homing.c:316-323).

        A lost board link is cleared first (_clear_link_faults): the driver's
        io_error and the hm2 watchdog's has_bit stay set until someone clears
        them (hm2_eth.c:883, watchdog.c:50-52, :259; hostmot2.c), and has_bit
        holds the rig's estop latch open, so without this the reset could
        never succeed and only a LinuxCNC restart would. On the real driver a
        cable pull raises io_error alone and the latch does NOT trip: LinuxCNC
        goes on saying ON (or OFF, after the following error of a move with
        frozen feedback) while the board's own watchdog has cut the outputs.
        So a link fault is looked for before "already ON", and the machine is
        put in ESTOP first (the snapshot normally did that the second the
        fault appeared, _stop_on_link_fault): clearing io_error makes the
        driver read the board again, and the bite it finds there would trip
        the latch in the middle of the reset otherwise. Every joint is then unhomed, as the board's
        step generators kept counting while it was cut off (LINK_RESTORED).
        If the link is still bad, io_error comes straight back, or the
        watchdog bites again within its timeout, and the reset is refused.

        A software estop (or DURDUR) that lands while the reset runs - from
        another thread, without the session lock - wins: ON is not sent
        after it (_stopped_since, checked right before the ON). Without
        that check, an estop pressed during the reset's wait for the latch
        leaves LinuxCNC in ESTOP_RESET on the rig (its software estop does
        not open the latch, see software_estop), the wait ends, and the ON
        that follows switches the machine back on - shown on the fake wired
        as the rig (review of plan WP03).

        No ON onto a pressed limit switch (plan WP05): with a switch motion
        sees pressed (motionrules.limit_trips), ON would enable motion for
        one servo cycle and the limit would stop it again - "Makine
        açılamadı" over LinuxCNC's English, the loop the rig met. The reset
        says which switch and that «Limitten kurtar» is the way off instead,
        after the link path (which keeps its priority: the flags are stale
        while the link is down) and the ESTOP_RESET, which the recovery
        would send anyway. The override on in the status does not change
        that: it cannot say whether the mask outlives the enable. A recovery
        jog an estop cut leaves motion's `overriding` set, and the planner
        drops the mask at the first servo cycle after enabling (control.c:
        1263-1270, :1278-1283; recover_limit, step 4) - the ON tripped at
        once in sim/ (review of WP05). «Limitten kurtar» issues the
        override again and switches on itself.
        """
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        link = self._read_hal()
        link_lost = bool(
            link.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or link.get(LinkHealth.HAL_IO_ERROR)
        )
        if not link_lost and s.task_state == L.STATE_ON:
            self._say("Makine zaten açık.")
            return
        if self._running(s):
            self._say(
                "Program çalışırken yazılım sıfırlaması gönderilmedi — "
                "sıfırlama programı durdurur.", Severity.WARN
            )
            return
        if link_lost:
            if s.task_state == L.STATE_ON:
                # Normally the snapshot stopped it already (_stop_on_link_fault).
                self._estop_sent = True  # ours: not a link loss to report
                self._send().state(L.STATE_ESTOP)
                self._wait()
                s = self._poll()
            try:
                why = self._clear_link_faults(link)
            except _LinkStillDown:
                self._say(LINK_NOT_RESTORED, Severity.ERROR)
                self._alarms.insert(
                    0, AlarmEvent(_now(), "Kartla bağlantı kurulamadı: iletişim hatası sürüyor",
                                  "hal", Severity.WARN),
                )
                return
            if why is not None:
                self._say(
                    "Kart bağlantısı sıfırlanamadı — LinuxCNC'yi yeniden başlatın. "
                    "(Bakım için: {})".format(why), Severity.ERROR
                )
                self._alarms.insert(
                    0, AlarmEvent(_now(), "Kart bağlantısı sıfırlanamadı: {}".format(why),
                                  "hal", Severity.ERROR),
                )
                return
            # Immediate in ESTOP (emctaskmain.cc:814); motion accepts it while
            # disabled (command.c:1399-1402).
            self._send().unhome(-1)
            self._wait()
            self._alarms.insert(
                0, AlarmEvent(_now(), "Kart bağlantısı sıfırlandı, referanslar silindi",
                              "hal", Severity.WARN),
            )
            # The bite the driver read while clearing tripped the latch: a
            # software ESTOP alone leaves the chain closed (ESTOP_RESET), a
            # bite opens it (ESTOP), and only ESTOP_RESET closes it again.
            s = self._poll()
        if s.task_state == L.STATE_ESTOP:
            self._send().state(L.STATE_ESTOP_RESET)
            s = self._wait_out_of_estop()
            if s.task_state == L.STATE_ESTOP and link_lost:
                self._say(LINK_NOT_RESTORED, Severity.ERROR)
                self._alarms.insert(
                    0, AlarmEvent(_now(), "Kartla bağlantı kurulamadı: makine acil stopta kaldı",
                                  "task", Severity.WARN),
                )
                return
            if s.task_state == L.STATE_ESTOP:
                self._say((
                    "Yazılım sıfırlaması reddedildi — LinuxCNC {:g} s içinde acil "
                    "stoptan çıkmadı. Acil stop zinciri kapalıysa tekrar deneyin."
                ).format(self._estop_reset_deadline_s), Severity.WARN)
                self._alarms.insert(
                    0,
                    AlarmEvent(
                        _now(),
                        "Yazılım sıfırlaması reddedildi: LinuxCNC acil stopta kaldı",
                        "task", Severity.WARN,
                    ),
                )
                return
        # Plan WP05: no ON onto a pressed switch. Motion would enable for one
        # servo cycle and stop the machine again (control.c:807-826, the
        # flags read even while disabled, :491-501); the way off is
        # «Limitten kurtar». After the link path above, whose reads the
        # driver resumes once io_error is cleared, the flags are fresh again.
        # Overridden or not: the docstring says why.
        blocked = motionrules.limit_trips(self._state_of(s), self.profile)
        if blocked:
            text = motionrules.on_refused_text(blocked)
            if link_lost:
                text = "Kart bağlantısı yeniden kuruldu, referanslar silindi. " + text
            self._say(text, Severity.WARN)
            self._alarms.insert(0, AlarmEvent(
                _now(), "Makine açılmadı: " + motionrules.pressed_phrase(blocked),
                "limit", Severity.WARN))
            return
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return
        self._send().state(L.STATE_ON)
        self._wait()
        s = self._poll()
        if s.task_state == L.STATE_ON:
            # The stop this UI sent (software_estop, _stop_on_link_fault, the
            # ESTOP above) is over: the next stop is not its own. Without this
            # a reset pressed before a snapshot had seen the stop left the
            # flag up, and the next real stop went unreported (review,
            # 2026-10-21).
            self._estop_sent = False
        if s.task_state != L.STATE_ON and link_lost:
            self._say(LINK_NOT_RESTORED, Severity.ERROR)
            return
        if s.task_state != L.STATE_ON:
            self._say("Makine açılamadı — LinuxCNC açık duruma geçmedi.", Severity.WARN)
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(), "Makine açılamadı: LinuxCNC açık duruma geçmedi",
                    "task", Severity.WARN,
                ),
            )
            return
        if link_lost:
            if any(s.homed[j] for j in self._joint_config):
                # set_all_unhomed refuses all-or-none, quietly (homing.c:294-314).
                self._say(LINK_RESTORED + (
                    " DİKKAT: referanslar silinemedi, LinuxCNC'yi yeniden başlatın."
                ), Severity.WARN)
            else:
                # The position may be off: home again before moving.
                self._say(LINK_RESTORED, Severity.WARN)
            return
        self._say("Yazılım acil stop sıfırlandı." + (
            "" if self._all_homed(s) else " Referans gerekli."
        ))

    def _clear_link_faults(self, link: Dict[str, Any]) -> Optional[str]:
        """Clear the board link's latched faults; None, or why halcmd failed.
        Raises _LinkStillDown when io_error comes back after the clear: the
        cable is still out.

        io_error first: it is a parameter, so setp clears it, and while it is
        set the driver reads nothing from the board. Then up to LINK_SETTLE_S
        for what the next reads bring - has_bit, the bite the board recorded
        while unreachable (watchdog.c:48-51), or io_error again. has_bit is a
        pin, and on the rig it is on the `watchdog` signal (test-rig.hal,
        ESTOP CHAIN), where setp refuses it; so the signal it is on is looked
        up and cleared with sets. Clearing has_bit is how the driver is told
        to reset the board and pet the watchdog again (hostmot2.adoc:113-115,
        watchdog.c:259-266).
        """
        if link.get(LinkHealth.HAL_IO_ERROR):
            ok, out = self._halcmd(["setp", LinkHealth.HAL_IO_ERROR, "0"])
            if not ok:
                return out.strip() or "halcmd setp io_error"
            deadline = self._clock() + LINK_SETTLE_S
            while True:
                self._sleep(_LINK_POLL_S)
                link = self._read_hal()
                if link.get(LinkHealth.HAL_IO_ERROR):
                    raise _LinkStillDown()
                if link.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or self._clock() >= deadline:
                    break
        if link.get(LinkHealth.HAL_WATCHDOG_HAS_BIT):
            pin = LinkHealth.HAL_WATCHDOG_HAS_BIT
            ok, out = self._halcmd(["-s", "show", "pin", pin])
            if not ok:
                return out.strip() or "halcmd show pin"
            signal = _signal_of(out, pin)
            ok, out = self._halcmd(["sets", signal, "0"] if signal else ["setp", pin, "0"])
            if not ok:
                return out.strip() or "halcmd sets"
        return None

    def _stalled_loops(self) -> List[str]:
        """LinuxCNC's looping processes - task and iocontrol - whose loop has
        neither slept nor used CPU for LOOP_STALL_S, judged by the kernel's
        counts of their voluntary context switches and CPU ticks (see
        LOOP_STALL_S). linuxcncsvr and rtapi_app are not judged: linuxcncsvr
        waits on events and counted 0/s when healthy (sim/, 2026-10-20), and
        /proc/PID/status counts the main thread only, which in rtapi_app
        idles while its realtime thread works. A missing process is not
        "stalled": linuxcnc_lost() reports it as missing. The first look at a
        process only records it."""
        table = _process_table(self._proc_root)
        if table is None:
            return []
        now = self._clock()
        stalled = []
        for name in self._loop_processes:
            switches = ticks = 0
            seen_any = False
            for pid in table.get(name, []):
                c = _voluntary_switches(self._proc_root, pid)
                if c is None:
                    continue
                seen_any = True
                switches += c
                ticks += _cpu_ticks(self._proc_root, pid) or 0
            if not seen_any:
                continue
            total = (switches, ticks)
            seen = self._loop_seen.get(name)
            if seen is None or seen[0] != total:
                self._loop_seen[name] = (total, now)
            elif now - seen[1] >= LOOP_STALL_S:
                stalled.append(name)
        return stalled

    def linuxcnc_lost(self) -> Optional[str]:
        """Why LinuxCNC is gone or half gone, in the operator's words, or None.

        For app.serve's kiosk supervision (--exit-if-linuxcnc-lost). Two
        things are visible from here: the status channel no longer reads
        (stat.poll raises; linuxcncsvr is gone), and one of LinuxCNC's own
        processes has died while the rest runs on - its task crashed, say:
        the status buffer stays valid but stale then, and the on-screen estop,
        which goes through task, no longer reaches the machine.

        A task (or iocontrol) that hangs without dying: its heartbeat is in
        the status buffer (emc_nml.hh:1172) but the Python binding does not
        export it, so it is judged from the kernel's side (_stalled_loops).
        """
        running = _process_table(self._proc_root)
        if running is not None:
            missing = [p for p in self._linuxcnc_processes if p not in running]
            if missing:
                return "LinuxCNC'nin bir parçası çalışmıyor ({})".format(", ".join(missing))
            stalled = self._stalled_loops()
            if stalled:
                return "LinuxCNC'nin bir parçası cevap vermiyor ({})".format(", ".join(stalled))
        try:
            self._poll()
        except self._linuxcnc.error as exc:
            return "LinuxCNC'nin durumu okunamıyor ({})".format(exc)
        return None

    def _wait_out_of_estop(self) -> Any:
        """Poll until task_state leaves ESTOP or the reset deadline passes."""
        deadline = self._clock() + self._estop_reset_deadline_s
        while True:
            s = self._poll()
            if s.task_state != self._linuxcnc.STATE_ESTOP:
                return s
            remaining = deadline - self._clock()
            if remaining <= 0:
                return s
            self._sleep(min(_RESET_POLL_INTERVAL_S, remaining))

    @_not_while_observing
    @_refused_without_status
    def home_all(self, confirmed: bool = False) -> Optional[str]:
        """«Tümünü referansla» (MachineAdapter.home_all): home(-1), every
        joint in HOME_SEQUENCE order. The sequence and its rules: _home."""
        return self._home(-1, None, confirmed)

    @_not_while_observing
    @_refused_without_status
    def home_joint(self, axis: str, confirmed: bool = False) -> Optional[str]:
        """One axis homed alone (MachineAdapter.home_joint; axis.py
        home_joint, :2708-2728): home(j) for the first joint carrying
        `axis` - with a HOME_SEQUENCE that is not negative, as the rig's
        0 / 1 / 2 are, motion homes that joint alone (do_home_one_joint,
        homing.c:265-281, :342-353). KeyError for an axis the profile
        lacks, before anything is read. The sequence and its rules: _home."""
        return self._home(self._joint_for_axis(axis), axis, confirmed)

    def _home(self, jnum: int, axis: Optional[str], confirmed: bool) -> Optional[str]:
        """home_all (`jnum` -1) and home_joint: axis.py's home_all_joints /
        home_joint and go_home (:2693-2728, :1954-1957) - MANUAL, teleop
        off, home(j) - each step read back, and not waited for: task
        reports DONE only when the whole homing move is over (see
        WAIT_TIMEOUT_S). Progress shows in stat.joint[n]["homing"] /
        ["homed"] on the next snapshots.

        Decided first, on this poll, before anything is sent
        (motionrules.home_refusal, the rule MockAdapter and the homing
        card's buttons follow too):

          - refused while a limit switch reads pressed (plan WP05),
            overridden or not, machine on or off - the operator learns the
            way off («Limitten kurtar»), not just that the machine is off.
            Even with the override a homing fails: the mask clears when the
            first joint's homing move pauses (control.c:1263-1283, a
            free-mode move ending) and the joint on its switch then stops
            the machine (sim lens, 2026-11-13: X tripped 1.6 s into Z's
            homing). Not while the board link is lost: the flags are stale
            then, and the machine is off anyway;
          - refused unless ON, while a program or MDI line runs or is
            paused, and while a joint is still homing: motion's home-all
            then clears joint 0's homed flag and does NOT restart the
            sequence (H[0].homed = 0 before do_home_all, which does nothing
            while homing is active; homing.c:553-560, :258-263), so the
            machine would end with joint 0 unhomed; a single joint's home
            would take the sequence over (do_home_one_joint sets its own
            sequence state, homing.c:265-281);
          - refused while the machine moves (a jog still running).

        A poll that may be Home All between two joints of its sequence is
        looked at once more first, HOMING_GAP_RECHECK_S later
        (_homing_sequence_gap_possible, as jog does): no joint's flag shows
        the homing there for a moment, and a home sent then would be the
        "while homing" case above.

        Then, unless `confirmed`, the question motionrules.home_question
        has - a homed joint homed again, another axis than TOOL_AXIS while
        that one is unhomed - is returned with nothing sent; app.Session
        asks the operator and calls again with confirmed=True on «Evet»,
        when every rule above is checked again on a fresh poll. A stop or
        the software estop since the action began sends nothing
        (_stopped_since)."""
        seq = self._stop_seq
        L = self._linuxcnc
        state = self._home_state()
        if _homing_sequence_gap_possible(state):
            self._sleep(HOMING_GAP_RECHECK_S)
            state = self._home_state()
        refusal = motionrules.home_refusal(state, self.profile, axis)
        if refusal:
            self._say(refusal, Severity.WARN)
            return None
        if not confirmed:
            question = motionrules.home_question(state, self.profile, axis)
            if question:
                return question
        if not self._ensure_mode(L.MODE_MANUAL):
            self._say(self._switch_refused("MANUAL moduna geçilemedi", "referans alınamaz"),
                      Severity.WARN)
            return None
        # Motion homes only in free mode: "must be in joint mode to home"
        # (command.c:1374-1378). After a first homing MANUAL means teleop.
        if not self._set_motion_teleop(False):
            self._say(self._switch_refused(
                "Eklem moduna geçilemedi (LinuxCNC jogu durdurup hareket bitince "
                "modu değiştirir; tekrar deneyin)",
                "referans alınamaz",
            ), Severity.WARN)
            return None
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return None
        self._send().home(jnum)
        self._say(motionrules.home_started_text(axis))
        return None

    def _home_state(self) -> MachineState:
        """A fresh poll, mapped for the homing rules with the board link
        (_read_hal): the switch flags do not count while it is lost."""
        s = self._poll()
        return self._state_of(s, self._read_hal())

    @_not_while_observing
    @_refused_without_status
    def jog(self, axis: str, direction: int) -> None:
        """One incremental jog of `axis` (+1/-1) by the jog step, under
        motionrules.jog_refusal (plan WP06) - the rule MockAdapter and the
        jog card's buttons follow too - decided from this poll before
        anything is sent:

          - refused while the machine is off, while a joint homes (motion
            would refuse the jog, "Can't jog any joint while homing.",
            command.c:853-857, and task's abort after that error cuts the
            homing short, emctaskmain.cc:3473-3499 - in sim/ mid-search,
            or with a joint left on its switch), while a program or MDI
            line runs or is paused, and while the machine moves (the step
            would extend the jog still running, command.c:873-877,
            axis.c:283-287);
          - never onto a pressed switch (plan WP05): motion refuses a joint
            jog there itself (command.c:129-138), not a teleop one
            (axis.c:278-297);
          - unhomed, the step capped (UNHOMED_MAX_STEP_MM, and
            UNHOMED_NO_SWITCH_MAX_MM toward the rig's Z bottom, which has
            no switch); homed, the target inside the soft limits - motion
            drops a jog past them without a word (axis.c:289-290,
            command.c:879-885) - so a longer step is cut to the room left
            and the operator is told, and one on the limit is refused.

        A status that may be Home All between two joints of its sequence -
        some joints homed, the others not, none homing - is looked at once
        more, HOMING_GAP_RECHECK_S later, before anything is decided: no
        joint's flag shows the homing there for a moment
        (_homing_sequence_gap_possible; review of WP06).

        Then MANUAL and the jog mode (axis.py get_jog_mode), each read back,
        and the rule once more on the status after them, with the mode the
        jog goes in. The line says "Jog X+1 mm." only once the jog has gone
        out; a stop or the software estop since the action began sends
        nothing (_stopped_since)."""
        seq = self._stop_seq
        L = self._linuxcnc
        jnum = self._joint_for_axis(axis)
        if direction not in (1, -1):
            raise ValueError("jog direction must be +1 or -1, got {!r}".format(direction))
        state = self._state_of(self._poll())
        if _homing_sequence_gap_possible(state):
            self._sleep(HOMING_GAP_RECHECK_S)
            state = self._state_of(self._poll())
        decision = motionrules.jog_refusal(
            state, self.profile, self._windows_by_axis, axis, direction, self._jog_step)
        if decision.refusal:
            self._say(decision.refusal, Severity.WARN)
            return
        if self._jog_velocity is None:
            self._say(
                "Jog hızı okunamadı — INI'de [DISPLAY] veya [TRAJ] "
                "DEFAULT_LINEAR_VELOCITY yok ya da sayı değil.", Severity.WARN
            )
            return
        if not self._ensure_mode(L.MODE_MANUAL):
            self._say(self._switch_refused("MANUAL moduna geçilemedi", "jog yok"), Severity.WARN)
            return
        jjogmode = self._select_jog_mode()
        if jjogmode is None:
            self._say(self._switch_refused(
                "Jog modu değişmedi (LinuxCNC hareket sürerken mod değiştirmez)", "jog yok"
            ), Severity.WARN)
            return
        # The mode is known now: a teleop jog is checked against the axis's
        # limits, homed or not (axis.c:289-290), a joint jog by its homing.
        decision = motionrules.jog_refusal(
            self._state_of(self._stat), self.profile, self._windows_by_axis, axis, direction,
            self._jog_step, teleop=not jjogmode)
        if decision.refusal:
            self._say(decision.refusal, Severity.WARN)
            return
        index = jnum if jjogmode else self.AXIS_LETTERS.index(axis)
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return
        # The increment is a magnitude; the velocity's sign is the direction
        # (command.c:873-877; emcJogIncr, taskintf.cc:818-845).
        self._send().jog(
            L.JOG_INCREMENT, jjogmode, index,
            direction * self._jog_velocity, decision.step_mm,
        )
        if decision.note:
            self._say(decision.note, Severity.WARN)
        else:
            self._say(motionrules.jog_sent_text(axis, direction, self._jog_step))

    @_not_while_observing
    @_refused_without_status
    def recover_limit(self) -> None:
        """«Limitten kurtar» (plan WP05; MachineAdapter.recover_limit): one
        press takes an axis off a pressed limit switch.

        Why one press of three steps: LinuxCNC will not switch the machine
        on while a joint stands on a switch it does not override - motion
        enables for one servo cycle and stops again (control.c:807-826) - and
        jogs only when ON. The override (command.c:675-707) masks the sides
        pressed at that instant and clears itself when the next jog that ran
        under it ends (control.c:1263-1283 free mode, :1404-1411 and
        :1452-1456 teleop) or motion enters coordinated mode
        (control.c:990-992); nothing else clears it, and the binding cannot
        withdraw it (it always names joint 0, emcmodule.cc:1113-1119). So
        override, ON and one jog away belong together: no window with the
        protection off and nothing moving, and no toggle - AXIS's checkbox
        switches task to AUTO on its second click (axis.py:2914-2921),
        which drops the mask by the side door, the trap the rig met.

        The sequence, under the session lock, each step read back:
          1. poll; motionrules.recovery_plan decides from that status (and
             the link pins) whether and how far: refused with no switch
             pressed, with the board link lost (stale flags), while a
             program, MDI line or homing runs or the machine moves, with
             one joint's two switches pressed, or with no single jog able
             to bring a homed joint inside its soft limits;
          2. out of ESTOP: state(ESTOP_RESET) and the latch wait of
             software_estop_reset (on the rig a trip leaves ESTOP_RESET);
          3. MANUAL (_ensure_mode, ON not required): out of ON task takes
             the override in MANUAL alone and drops it in any other mode
             without a word (emctaskmain.cc:846-852);
          4. out of ON: override_limits(), a bounded wait, and
             stat.joint[0]["override_limits"] read back - on every press,
             the status showing it on already or not. A recovery jog an
             estop cut leaves motion's `overriding` set (a jog ran under
             the mask, control.c:1263-1270; the DISABLED branch runs no
             end-of-jog check, control.c:1463-1477), so the status still
             says on, but the free planner drops the mask at the first
             servo cycle after enabling (control.c:1278-1283) and ON then
             trips on the switch at once (sim/, review of WP05: a wasted
             press, LinuxCNC's limit error again). The command clears
             `overriding` and rebuilds the mask from the switches pressed
             now (command.c:686-700);
          5. out of ON: state(ON) and read back - enabling leaves the mask
             (control.c:896-924: it clears the joints' error flags only);
          6. motion out of free mode (a homed machine is in teleop):
             teleop off, read back - a joint jog needs free mode
             (command.c:421-426), and in free mode motion itself refuses a
             jog onto the pressed side (command.c:129-138);
          7. the plan again, from the status now (the switch may have let
             go meanwhile);
          8. one JOG_INCREMENT of that joint away from the switch at the
             jog speed, not waited for. The override ends when it does
             (_follow_recovery says how it went).
        A press with the machine already ON and the override on (a press
        whose jog never went out) starts at 6.

        A stop or the software estop from another thread (_stopped_since)
        ends the sequence before the override, the ON and the jog: nothing
        is switched on or moved after it. A refusal that comes once the
        override is on says so - it stays on until a jog away ends or MDI
        mode is entered. Never MDI or AUTO here: entering coordinated mode
        would drop the mask (control.c:990-992) with the axis still on the
        switch.
        """
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        plan = motionrules.recovery_plan(self._state_of(s, self._read_hal()), self.profile,
                                         self._windows)
        if plan is None:
            self._say(motionrules.NO_TRIP, Severity.WARN)
            return
        if plan.refusal:
            self._say(plan.refusal, Severity.WARN)
            return
        if self._jog_velocity is None:
            self._say(
                "Jog hızı okunamadı — INI'de [DISPLAY] veya [TRAJ] DEFAULT_LINEAR_VELOCITY "
                "yok ya da sayı değil; kurtarma yapılmadı.", Severity.WARN)
            return
        if s.task_state == L.STATE_ESTOP:
            if self._stopped_since(seq):
                self._say(self._stop_why, Severity.WARN)
                return
            self._send().state(L.STATE_ESTOP_RESET)
            s = self._wait_out_of_estop()
            if s.task_state == L.STATE_ESTOP:
                self._say("LinuxCNC acil stoptan çıkmadı — kurtarma yapılmadı.", Severity.WARN)
                return
        if not self._ensure_mode(L.MODE_MANUAL, require_on=False):
            self._say(
                "MANUAL moduna geçilemedi — limit aşma yalnız MANUAL'de kabul edilir; "
                "kurtarma yapılmadı.", Severity.WARN)
            return
        s = self._stat
        if s.task_state != L.STATE_ON:
            # Even when the status shows the override on: see step 4.
            if self._stopped_since(seq):
                self._say(self._stop_why, Severity.WARN)
                return
            self._send().override_limits()
            self._wait()
            s = self._poll()
            if not self._override_on(s):
                self._drain_errors()
                self._say("Limit aşma kabul edilmedi — kurtarma yapılmadı.", Severity.WARN)
                return
        left_on = motionrules.OVERRIDE_LEFT_ON
        if s.task_state != L.STATE_ON:
            if self._stopped_since(seq):
                self._say(self._stop_why + left_on, Severity.WARN)
                return
            self._send().state(L.STATE_ON)
            self._wait()
            s = self._poll()
            if s.task_state != L.STATE_ON:
                # LinuxCNC's reason (a switch the mask does not cover, say)
                # into the box first, so the refusal said after it is the line.
                self._drain_errors()
                self._say("Makine açılamadı — kurtarma yapılmadı." + left_on, Severity.WARN)
                return
            # As software_estop_reset: the stop this UI sent is over.
            self._estop_sent = False
        if s.motion_mode != L.TRAJ_MODE_FREE:
            if not self._set_motion_teleop(False):
                self._say(self._switch_refused(
                    "Eklem moduna geçilemedi", "kurtarma hareketi gönderilmedi") + left_on,
                    Severity.WARN)
                return
            s = self._stat
        plan = motionrules.recovery_plan(self._state_of(s, self._read_hal()), self.profile,
                                         self._windows)
        if plan is None:
            self._say("Switch serbest kaldı — kurtarma hareketi gerekmedi." + left_on)
            return
        if plan.refusal:
            self._say(plan.refusal + left_on, Severity.WARN)
            return
        if self._stopped_since(seq):
            self._say(self._stop_why + left_on, Severity.WARN)
            return
        trip, step = plan.trip, plan.step_mm
        # A joint jog: the increment a magnitude, the velocity's sign the
        # direction (command.c:873-877), away from the switch.
        self._send().jog(L.JOG_INCREMENT, True, trip.joint, trip.away * self._jog_velocity, step)
        self._recovery = _Recovery(trip.joint, trip.axis, trip.end, trip.label)
        self._alarms.insert(0, AlarmEvent(
            _now(), "Limitten kurtarma: {} {}{:g} mm".format(
                trip.label, "+" if trip.away > 0 else "-", step),
            "limit", Severity.WARN))
        self._say(motionrules.recovery_started_text(plan))

    @_not_while_observing
    @_refused_without_status
    def load_program(self, path: str) -> None:
        """Open a G-code file: MANUAL, task_plan_synch, wait,
        program_open(path), wait. axis.py's open_file_guts (:1174-1208)
        differs in one step: it switches to MANUAL, then back to the mode
        it found (ensure_mode(save_task_mode), :1176-1177, :1198) BEFORE
        task_plan_synch (:1206-1207) - from AUTO that synch is refused,
        task takes no PLAN_SYNCH in AUTO (emctaskmain.cc:1015-1112). This
        synchs in MANUAL and stays there; program_start switches to AUTO
        itself. `path` is absolute (app.Session, programs.resolve):
        milltask's working directory is the INI's (linuxcnc.in:777).

        Refused up front by programs.load_block on this poll - the rule
        MockAdapter and the Program screen's buttons follow: a program or
        MDI line running or paused (_running, axis.py:962-965), a joint
        homing, or the machine moving (MachineState.moving: with the
        machine ON, in free mode motion clears stat.inpos while any joint's
        planner runs, control.c:1205-1272, i.e. during a joint jog; a
        teleop jog only shows in current_vel - adapter._moving). Out of ON
        motion is disabled and does not update INPOS, so it is not
        consulted there. Nothing here starts motion, so there is no stop
        sequence check (_stopped_since): an abort meanwhile leaves the load
        harmless.

        After the open: a failure is status RCS_ERROR or stat.file not naming
        the file. A wait that timed out (-1) while stat.file names the file
        is a success - task reports DONE only once motion is quiet
        (emctaskmain.cc:3546-3561), so a timeout says nothing about the open.
        DONE alone is not proof either (serials, see WAIT_TIMEOUT_S), nor is
        stat.file alone: task never clears it on PLAN_CLOSE or a failed
        PLAN_OPEN, so reloading a file that has meanwhile become unreadable
        still shows its name - with RCS_ERROR.
        """
        L = self._linuxcnc
        s = self._poll()
        refusal = programs.load_block(self._state_of(s))
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        # PLAN_SYNCH is not accepted in AUTO (emctaskmain.cc:1015-1112), which
        # is why axis.py switches to MANUAL first. Loading needs no ON: task
        # takes SET_MODE, PLAN_SYNCH and PLAN_OPEN in estop too
        # (emctaskmain.cc:807-844).
        if not self._ensure_mode(L.MODE_MANUAL, require_on=False):
            self._say(self._switch_refused("MANUAL moduna geçilemedi", "program yüklenmedi"),
                      Severity.WARN)
            return
        self._send().task_plan_synch()
        self._wait()
        try:
            self._send().program_open(path)
        except ValueError:
            # Longer than the 254-character NML field (emcmodule.cc:1211-1214).
            self._say("Dosya yolu çok uzun — program yüklenmedi.", Severity.WARN)
            return
        status = self._wait()
        s = self._poll()
        if status == L.RCS_ERROR or s.file != path:
            # LinuxCNC's reason ("can't open ...") goes to the alarm log and
            # the error box first (_drain_errors: "Dosya açılamadı: ..."), so
            # the Turkish refusal said after it is the message line.
            self._drain_errors()
            self._say("Program açılamadı: {}".format(path), Severity.WARN)
            return
        self._say("{} yüklendi.".format(os.path.basename(path)))

    def refuse(self, text: str) -> None:
        """MachineAdapter.refuse: the message band alone, so it is safe in
        observer mode too (app.Session drops the actions that call it
        there anyway)."""
        self._say(text, Severity.WARN)

    @_not_while_observing
    @_refused_without_status
    def program_start(self) -> None:
        """«Başlat»: AUTO, then auto(AUTO_RUN, 0), as axis.py's task_run
        (:2345-2358), from the start of the file. Refused first by
        start_blocks (homed, ON, the link), no file loaded, and anything
        running or paused (programs.start_busy: a paused program is resumed
        with «Devam», plan WP09). The stop sequence is checked right before
        the send. The run read back, then followed to its end from here
        (_follow_run)."""
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        # The errors first, as snapshot drains them before _follow_run: a
        # run that ended on an error since the last snapshot must not read
        # as "bitti" (review of WP09).
        self._drain_errors()
        self._follow_run(s)
        state = self._map(s, self._read_hal())
        blocks = state.start_blocks
        if blocks:
            self._say("Başlatılamaz: {}".format(blocks[0]), Severity.WARN)
            return
        if not s.file:
            self._say("Program yüklenmedi.", Severity.WARN)
            return
        busy = programs.start_busy(state)
        if busy:
            self._say(busy, Severity.WARN)
            return
        if not self._ensure_mode(L.MODE_AUTO):
            self._say(self._switch_refused("AUTO moduna geçilemedi", "program başlatılmadı"),
                      Severity.WARN)
            return
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return
        errors_before = self._errors_taken
        self._send().auto(L.AUTO_RUN, 0)
        self._run_command = RUN_CMD_START
        # Read back without a wait: the send returns once task has run
        # PLAN_RUN, which sets the interpreter READING at once
        # (emcmodule.cc:233-250, emctaskmain.cc:2294-2310); a wait would
        # only run out while it runs (emctaskmain.cc:3546-3561).
        after = self._poll()
        if self._program_busy(after):
            self._begin_run(s, seq)
            self._say(programs.STARTED_TEXT.format(os.path.basename(s.file)))
            return
        # Idle already: refused, or a program short enough to end within
        # the send (the interpreter reads to M2 and goes idle in the same
        # task cycles, emctaskmain.cc:524-534, :713-737 - an "M2" alone in
        # sim/, 2026-11-14). wait_complete tells them apart by this
        # command's serial (emcmodule.cc:212-231): PLAN_RUN's only refusal
        # is RCS_ERROR (not homed, emctaskmain.cc:2294-2299); one dropped
        # silently is DONE, but only in MANUAL (:995-1000), which the
        # read-back shows. Bounded; motion is quiet with the interpreter
        # idle, so DONE comes at once (P0 cleanup).
        status = self._wait()
        after = self._poll()
        if self._program_busy(after):
            self._begin_run(s, seq)
            self._say(programs.STARTED_TEXT.format(os.path.basename(s.file)))
            return
        self._drain_errors()
        if status == L.RCS_DONE and after.task_mode == L.MODE_AUTO:
            # It ran: _follow_run says how it ended - FAILED_TEXT if
            # LinuxCNC reported an error since the send.
            self._run = _Run(str(s.file or ""), seq, errors_before)
            self._follow_run(after)
            return
        self._say(programs.START_NOT_TAKEN, Severity.WARN)

    @_not_while_observing
    @_refused_without_status
    def program_pause(self) -> None:
        """«Duraklat» (plan WP09): auto(AUTO_PAUSE), as axis.py's task_pause
        (:2368-2372) - from AUTO with the interpreter READING or WAITING, and
        from MDI as its task_pauseresume (:2399-2409); refused otherwise by
        programs.pause_refusal. Task pauses motion where it is and sets the
        interpreter PAUSED (emctaskmain.cc:2312-2320); the position is kept,
        so «Devam» goes on from there. It starts no motion, so no stop
        sequence check. A bounded wait - task reports nothing DONE while the
        interpreter is not idle (emctaskmain.cc:3546-3561), so it runs out -
        then the pause read back."""
        L = self._linuxcnc
        s = self._poll()
        refusal = programs.pause_refusal(self._state_of(s))
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        mdi = s.task_mode == L.MODE_MDI
        self._send().auto(L.AUTO_PAUSE)
        self._run_command = RUN_CMD_PAUSE
        self._wait()
        s = self._poll()
        if self._state_of(s).program_paused:
            self._say(programs.MDI_PAUSED_TEXT if mdi else programs.PAUSED_TEXT)
        else:
            self._drain_errors()
            self._say(programs.PAUSE_NOT_TAKEN, Severity.WARN)

    @_not_while_observing
    @_refused_without_status
    def program_resume(self) -> None:
        """«Devam» (plan WP09): auto(AUTO_RESUME), as axis.py's task_resume
        (:2390-2397): only with something paused (stat.paused or the
        interpreter PAUSED - an M0/M1 too) and the task in AUTO or MDI,
        whose mode it keeps (programs.resume_refusal). Task resumes motion
        and puts the interpreter back as the pause found it (emctaskmain.cc:
        2344-2352). It starts motion: start_blocks first (ON, homed, the
        link), the stop sequence right before the send. A bounded wait, then
        the resume read back."""
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        state = self._state_of(s, self._read_hal())
        blocks = state.start_blocks
        if blocks:
            self._say("Sürdürülemez: {}".format(blocks[0]), Severity.WARN)
            return
        refusal = programs.resume_refusal(state)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return
        mdi = s.task_mode == L.MODE_MDI
        if (not mdi and s.paused and s.interp_state != L.INTERP_PAUSED):
            # Stepping left the interpreter READING or WAITING under motion's
            # pause (emctaskmain.cc:1251-1261) - WAITING once it has read to
            # the program end (:524-534). PLAN_RESUME restores the state the
            # last PLAN_PAUSE saved (:2344-2352): READING, from the first
            # «Adım» (:1078-1091), so the interpreter would read past the
            # end and report "File ended with no percent sign (%) or program
            # end (M2)" (interp_read.cc:3128-3143; seen in sim/ after «Adım»,
            # «Adım», «Devam»). A PLAN_PAUSE first saves the state it is in
            # now (:2312-2320); motion is paused already, so it moves
            # nothing. In sim/ (2026-11-14) pause then resume after 2, 3 and
            # 4 steps ran the program to its end with no error (P0 cleanup).
            self._send().auto(L.AUTO_PAUSE)
            self._wait()
            if self._stopped_since(seq):
                self._say(self._stop_why, Severity.WARN)
                return
        self._send().auto(L.AUTO_RESUME)
        self._run_command = RUN_CMD_RESUME
        self._wait()
        s = self._poll()
        if not self._state_of(s).program_paused:
            self._say(programs.MDI_RESUMED_TEXT if mdi else programs.RESUMED_TEXT)
        else:
            self._drain_errors()
            self._say(programs.RESUME_NOT_TAKEN, Severity.WARN)

    @_not_while_observing
    @_refused_without_status
    def program_step(self) -> None:
        """«Adım» (plan WP09): AUTO, then auto(AUTO_STEP), as axis.py's
        task_step (:2361-2366). Nothing running: the program starts paused
        at its beginning (task issues PLAN_RUN and pauses at once,
        emctaskmain.cc:1078-1091). A program paused in AUTO: one line more -
        motion steps until its line changes and pauses again (:1251-1261,
        command.c:1200-1212). Refused like «Başlat» (start_blocks, no file)
        and by programs.step_refusal: a program running unpaused, an MDI
        line. The mode switch is made only with nothing running (switching
        to AUTO aborts, emctask.cc:293-296). The stop sequence right before
        the send; a bounded wait, then the pause read back."""
        seq = self._stop_seq
        L = self._linuxcnc
        s = self._poll()
        # The errors first, as snapshot drains them before _follow_run: a
        # run that ended on an error since the last snapshot must not read
        # as "bitti" (review of WP09).
        self._drain_errors()
        self._follow_run(s)
        state = self._map(s, self._read_hal())
        blocks = state.start_blocks
        if blocks:
            self._say("Adım atılamaz: {}".format(blocks[0]), Severity.WARN)
            return
        if not s.file:
            self._say("Program yüklenmedi.", Severity.WARN)
            return
        refusal = programs.step_refusal(state)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        first = not state.program_paused
        if first and not self._ensure_mode(L.MODE_AUTO):
            self._say(self._switch_refused("AUTO moduna geçilemedi", "adım atılmadı"),
                      Severity.WARN)
            return
        if self._stopped_since(seq):
            self._say(self._stop_why, Severity.WARN)
            return
        self._send().auto(L.AUTO_STEP)
        self._run_command = RUN_CMD_STEP
        self._wait()
        after = self._poll()
        if self._state_of(after).program_paused:
            if self._program_busy(after):
                self._begin_run(s, seq)
            self._say(programs.STEP_FIRST_TEXT if first else programs.STEP_TEXT)
        else:
            self._drain_errors()
            self._say(programs.STEP_NOT_TAKEN, Severity.WARN)

    @_not_while_observing
    def stop_motion(self) -> None:
        """DURDUR, first half: abort() on the stop channel. No wait, no poll.

        app.Session calls it without its lock, like software_estop, so it
        may run while an action holds the lock in another thread - waiting
        inside wait_complete, say. It touches only its own channel, the stop
        sequence (_note_stop, first, so that action sends no motion command
        after it), _message and _alarms. Like software_estop it goes out
        while LinuxCNC's status cannot be read: it reads nothing.

        What abort stops (EMC_TASK_ABORT, emctaskmain.cc:2108-2121, then
        emcTaskAbort, emctask.cc:226-262): every joint jog and the
        trajectory (emcMotionAbort, taskintf.cc:1798-1815), so a teleop jog
        (axis_jog_abort_all, command.c:505-506), a coordinated move - a
        program or MDI line (tpAbort, :507-508) - and in free mode every
        joint's planner and its homing (do_cancel_homing, :509-520 and
        :533-554; HOME_ABORT leaves the joint unhomed, homing.c:1354-1362).
        Only the joints homing at that instant are cancelled; a Home All's
        sequence then moves on to the next joint, which a later cancel
        point of the same abort catches during its 0.1 s HOME_DELAY, before
        it moves (fake_linuxcnc FakeMachine.issue_abort has the detail;
        seen in sim/). Each planner stops at its acceleration limit
        (simple_tp.c:26-66), not dead. The interpreter goes idle and the
        open program is closed, its name kept (emctask.cc:226-262).

        The send waits - holding the GIL - until task echoes the command
        (emcmodule.cc:233-250): about one [TASK]CYCLE_TIME, up to 5 s if
        task stalls; the estop's caveat (WAIT_TIMEOUT_S) applies.
        """
        self._note_stop(STOP_REFUSAL)
        self._stop_channel.abort()
        self._say(STOP_SENT)
        self._alarms.insert(0, AlarmEvent(_now(), "Durdur gönderildi", "task", Severity.INFO))

    @_not_while_observing
    @_refused_without_status
    def stop_followup(self) -> None:
        """DURDUR, second half, under app.Session's lock: abort() again on
        the action channel, a bounded wait and the state read back, as
        axis.py's task_stop does (:2411-2417).

        The second abort closes the one gap _stopped_since leaves: an action
        that passed its last stop check just before stop_motion and sent its
        motion command just after. app.Session runs this as soon as that
        action returns, so whatever it sent is aborted at once.

        Then the interpreter idle and nothing moving (MachineState.moving's
        rule, _moving) is STOPPED; otherwise STOP_NOT_IDLE - a planner still
        braking after the bounded wait, say. With the status lost after the
        send, the guard says the result is unknown."""
        L = self._linuxcnc
        self._send().abort()
        self._wait()
        s = self._poll()
        moving = _moving(
            s.task_state == L.STATE_ON, MOTION_MODES.get(s.motion_mode),
            float(s.current_vel), s.inpos,
        )
        if s.interp_state == L.INTERP_IDLE and not moving:
            self._say(STOPPED)
        else:
            self._say(STOP_NOT_IDLE, Severity.WARN)

    def set_jog_step(self, mm: float) -> None:
        """Jog increment in mm. Must be positive: motion treats it as a
        magnitude and takes the direction from the velocity's sign
        (command.c:873-877), so a negative step would reverse the jog."""
        mm = float(mm)
        if not math.isfinite(mm) or mm <= 0:
            self._say("Geçersiz jog adımı: {!r} mm.".format(mm), Severity.WARN)
            return
        self._jog_step = mm

    @_not_while_observing
    @_refused_without_status
    def set_feed_override(self, pct: int) -> None:
        """Feed override in percent, sent as command.feedrate(scale) the way
        axis.py does (:2104-2111). Not command.set_feed_override, which only
        enables or disables the override (emcmodule.cc:1314-1323).

        Motion clamps only negatives (command.c:1214-1221), so the INI ceiling
        [DISPLAY]MAX_FEED_OVERRIDE is applied here, in whole percent as axis.py
        does (:3353-3355). 0 % is a valid override (it holds the feed).
        """
        pct = int(pct)
        # The guard's question first: a send to a stuck task spins 5 s, and
        # the poll after an outage writes its own message, which must not
        # cover this action's.
        self._poll()
        decision = feed_override_decision(pct, self._max_feed_pct)
        self._say(decision.text, decision.severity)
        if decision.pct is None:
            return
        self._send().feedrate(decision.pct / 100.0)

    def set_wcs(self, wcs: str) -> None:
        """Select a coordinate system by MDI, under the same gates as send_mdi
        (and its guard). Only G54-G59 (wcs_refusal)."""
        refusal = wcs_refusal(wcs)
        if refusal:
            self._say(refusal, Severity.WARN)
            return
        self.send_mdi(wcs)
