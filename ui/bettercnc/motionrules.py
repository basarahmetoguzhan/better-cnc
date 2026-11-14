"""Motion rules both adapters and the view model share (plans WP05-WP07).

Which limit switches motion sees pressed (limit_trips), which way is off
each (away_direction), whether a jog heads onto one (toward_pressed_switch),
what the one-press recovery «Limitten kurtar» would do now (recovery_plan),
whether a jog click may go and how far (jog_refusal, jog_plan), and whether
a homing - all joints or one - may start and what to ask the operator
first (home_block, home_refusal, home_question), with the operator's words
for each. Pure: no LinuxCNC, no I/O. LinuxCncAdapter passes a MachineState
mapped from LinuxCNC's status (adapter.map_linuxcnc_state), MockAdapter its
own, the view model the snapshot it renders.

What LinuxCNC 2.9 does, which the rules follow (paths under
reference/linuxcnc/src/emc/):

  - Motion reads every joint's switch inputs each servo cycle, enabled or
    not (motion/control.c:491-501), and trips - "joint N on limit switch
    error", motion disabled, LinuxCNC out of ON - on any flag of an
    enabled joint that is neither overridden nor homing
    (motion/control.c:807-826). So an axis standing on a switch keeps the
    machine from being switched on: ON enables it for one servo cycle.
  - The only way off is the override (motion/command.c:675-707): it masks
    the sides pressed at that instant, every joint's, and clears itself
    when the next jog that ran under it ends (motion/control.c:1263-1283
    in free mode, :1404-1411 and :1452-1456 in teleop) or when motion
    enters coordinated mode (motion/control.c:990-992). Task takes it out
    of ON only in MANUAL mode and drops it without a word in any other
    (task/emctaskmain.cc:846-852); the binding always names joint 0, so it
    cannot be withdrawn (usr_intf/axis/extensions/emcmodule.cc:1113-1119).
  - A joint jog onto a pressed switch is refused by motion itself, mask or
    not, unless both of the joint's sides are masked (joint_jog_ok,
    motion/command.c:118-138); a teleop jog is not
    checked against the switches at all (motion/axis.c:278-297).
  - Once homed, motion drops a jog whose target lies outside the soft
    limits without a word (motion/command.c:879-885 for a joint jog,
    motion/axis.c:289-290 for a teleop one); unhomed, its window is the
    joint's position plus or minus the whole travel (motion/command.c:
    161-175), re-centred at every jog. A teleop jog is checked against the
    axis's limits whether the joint is homed or not (axis.c:289-290).
  - An incremental jog adds its step to the planner's target, not to where
    the axis stands (motion/command.c:873-877 free, motion/axis.c:283-287
    teleop): a click while one runs extends it.
  - Motion refuses any jog while a joint homes, "Can't jog any joint while
    homing." (motion/command.c:853-857), and sets the joint's error flag;
    task reads that as motion's error (task/taskintf.cc:974-979, :2069-2086)
    and aborts (task/emctaskmain.cc:3473-3499), which cuts the homing short
    (in sim/ the homing stopped mid-search, or a joint went on and stopped
    on its switch).

The joint homing on its own switch is the normal case, not a trip:
HOME_IGNORE_LIMITS (all three joints of the rig, where home and limit share
one switch) makes motion ignore that joint's flags while it homes
(motion/control.c:815-816).

Homing (plan WP07):

  - Task takes a home command only with the machine ON in MANUAL
    (task/emctaskmain.cc:911), and motion only in free (joint) mode, "must
    be in joint mode to home" (motion/command.c:1366-1378).
  - One joint whose HOME_SEQUENCE is not negative is homed alone
    (do_home_one_joint, motion/homing.c:265-281; only it is in the
    sequence, :342-353); the rig's are 0 / 1 / 2. A joint homed again is
    unhomed as its homing starts (:401-410, :771-772), every joint of a
    Home All at once.
  - Motion switches to teleop when a homing leaves every joint homed
    (motion/homing.c:1400, motion/control.c:261-263), and only then: after
    one joint homed alone the others unhomed, it stays in free mode.
  - A Home All sent while a homing runs clears joint 0's homed flag and
    starts nothing (homing.c:553-560, :258-263); a joint homing at that
    instant is cancelled by an abort, DURDUR (motion/command.c:509-520).
    In the rig's final, latch phase the joint stands on its home switch,
    which is also its limit switch (X−, Y−, Z+): cancelled there, it is
    left on it, and motion stops the machine (control.c:807-826) - the
    limit card's «Limitten kurtar» takes it off (plan WP05).
  - AXIS asks before homing a homed machine or joint again
    (usr_intf/axis/scripts/axis.py:2693-2701, :2708-2728); this UI asks
    whenever any joint is homed (plan design (g)).
"""

from __future__ import annotations

import math
from typing import List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .messages import RECOVER_HINT  # noqa: F401  (the hint after a trip: one text)
from .profiles import DigitalInput, MachineProfile, MissingSwitch
from .state import (
    JOG_BLOCK_HOMING,
    JOG_BLOCK_OFF,
    JOG_BLOCK_PAUSED,
    JOG_BLOCK_RUNNING,
    JOG_BLOCK_UNKNOWN,
    JOG_BLOCK_UNREAD,
    DrivePower,
    HomingPhase,
    HomingState,
    JointHoming,
    ORDER_UNKNOWN,
    MachineState,
)

#: The recovery jog's length while the joint is unhomed, in mm. Chosen, not
#: measured: on the rig a switch releases within 0.64 mm of where it trips
#: (the homing hysteresis, configs/test-rig/test-rig.ini) and its
#: over-travel is about 1.3 mm (the same file), so 3 mm leaves the switch
#: with room to spare. If it does not, the machine stops again, safely, and
#: «Limitten kurtar» is pressed once more; the PC checklist measures it.
RECOVERY_STEP_MM = 3.0

#: How far inside its soft limit a homed joint's recovery jog must end, in
#: mm: motion drops a homed jog whose target is outside them. Chosen, not
#: measured.
RECOVERY_INSIDE_MARGIN_MM = 0.5

#: The longest recovery jog, in mm. On the rig the switches sit 3.5-3.7 mm
#: beyond the soft limits (test-rig.ini), so a homed joint tripped there
#: needs about 4-5.5 mm; a longer way back is not a recovery to make in one
#: press. Chosen, not measured.
RECOVERY_MAX_STEP_MM = 10.0

#: The operator's words (Turkish, as every message is).
NO_TRIP = "Basılı limit switch yok."
#: In observer mode (app.py --observe) the machine is run from AXIS.
OBSERVE_RECOVER_HINT = "Switch'ten AXIS'te çıkın (limit aşma, sonra switch'ten uzağa jog)."
OVERRIDE_BADGE = "LİMİT KORUMASI GEÇİCİ KAPALI"
#: Said after a refusal that came once the override was on: it stays on
#: until a jog away from the switch ends (or MDI mode is entered).
OVERRIDE_LEFT_ON = " Limit koruması geçici kapalı; switch'ten uzağa bir jog bitince kendiliğinden geri gelir."
LINK_FIRST = (
    "Önce kart bağlantısını «Yazılım sıfırla» ile kurun — bağlantı yokken switch'ler "
    "okunamaz; kurtarma yapılmadı."
)
DRIVE_POWER_CUT = "Sürücü gücü donanımsal kesik — kurtarma yapılmadı."
PROGRAM_BUSY = "Program ya da MDI çalışırken limitten kurtarma yapılmaz."
HOMING_BUSY = "Referans alma sürüyor — limitten kurtarma yapılmaz."
MOVING_BUSY = "Hareket sürüyor — bitmesini bekleyin; kurtarma gönderilmedi."


class LimitTrip(NamedTuple):
    """One switch motion sees pressed (MachineState.hard_limits)."""

    joint: int
    axis: str
    #: The end of travel it sits at: "+" (motion's max flag) or "-" (min).
    end: str
    #: The profile's label for it ("X sol uç"), or "X− ucu" without one.
    label: str
    #: The HAL pin the profile nets it from, "" without one.
    hal_pin: str
    #: The jog direction off it: +1 off a min switch, -1 off a max one.
    away: int


class RecoveryPlan(NamedTuple):
    """What «Limitten kurtar» would do now (recovery_plan)."""

    #: The switch the recovery jog leaves: the first pressed one.
    trip: LimitTrip
    #: The jog's length in mm; None when the recovery is refused.
    step_mm: Optional[float]
    #: Why it is refused, "" when it can go.
    refusal: str
    #: Every other switch pressed: one override covers them all, but the
    #: first jog that ends clears it, so each needs its own press.
    others: Tuple[LimitTrip, ...] = ()


def _sign(direction: int) -> str:
    """+1 / -1 as the jog buttons write it: + or U+2212."""
    return "+" if direction > 0 else "−"


def _mm(value: float) -> str:
    """A length or position, shortest form, the minus typographic."""
    return "{:g}".format(value).replace("-", "−")


def away_direction(end: str) -> int:
    """The jog direction off a switch at `end`: + off the negative end
    (motion's min flag), − off the positive end (its max flag)."""
    return 1 if end == "-" else -1


def _input_for(profile: MachineProfile, axis: str, end: str) -> Optional[DigitalInput]:
    for inp in profile.inputs:
        if inp.axis == axis and inp.end == end:
            return inp
    return None


def limit_trips(state: MachineState, profile: MachineProfile) -> Tuple[LimitTrip, ...]:
    """Every switch motion sees pressed now, in joint order, min before max:
    motion's own flags (MachineState.hard_limits), labelled from the
    profile. Not a joint that is homing: its home switch, which on the rig
    is also its limit switch, is pressed then by design, and motion ignores
    its flags (HOME_IGNORE_LIMITS). Empty while LinuxCNC's status is
    unknown (no flags are read) and for an adapter that reads none
    (MockAdapter).

    The flags are no fresher than the board's inputs: with the board link
    lost they keep their last value (state.HardLimit) - the callers that
    act on them check the link first."""
    if not state.status_known:
        return ()
    homing = {j.joint for j in state.homing.joints if j.phase is HomingPhase.HOMING}
    trips = []
    for h in state.hard_limits:
        if h.joint in homing:
            continue
        for end, pressed in (("-", h.min), ("+", h.max)):
            if not pressed:
                continue
            inp = _input_for(profile, h.axis, end)
            trips.append(LimitTrip(
                joint=h.joint, axis=h.axis, end=end,
                label=inp.label if inp else "{}{} ucu".format(h.axis, _sign(1 if end == "+" else -1)),
                hal_pin=inp.hal_pin if inp else "",
                away=away_direction(end),
            ))
    return tuple(trips)


def unmasked_trips(state: MachineState, profile: MachineProfile) -> Tuple[LimitTrip, ...]:
    """The trips that can have stopped an enabled machine (what
    LinuxCncAdapter._note_task_state names): all of them while the override
    is off, none while it is on. The override masks the sides pressed when
    it was given; a switch pressed since is not masked, but the status
    carries one value for the whole mask (task/taskintf.cc:963) - such a
    switch still stops the machine, the safe side. Not what an ON would
    meet: the status cannot say whether the mask outlives the enable (a jog
    an estop cut drops it then, LinuxCncAdapter.recover_limit step 4), so
    software_estop_reset goes by limit_trips."""
    if state.override_limits:
        return ()
    return limit_trips(state, profile)


def toward_pressed_switch(trips: Sequence[LimitTrip], axis: str, direction: int) -> Optional[LimitTrip]:
    """The pressed switch a jog of `axis` in `direction` (+1/-1) heads onto,
    or None. Refused in every mode: a joint jog onto it motion refuses
    itself, a teleop jog it does not check."""
    for t in trips:
        if t.axis == axis and direction == -t.away:
            return t
    return None


def soft_windows(config) -> Mapping[int, Tuple[Optional[float], Optional[float]]]:
    """joint -> its soft limits ([JOINT_n] MIN_LIMIT / MAX_LIMIT, what motion
    checks a homed joint jog against, configmodel.TravelSpec), measured or
    not: LinuxCNC enforces them either way."""
    return {a.joint: (a.travel.min_limit, a.travel.max_limit) for a in config.axes}


def _round_up(mm: float) -> float:
    """Up to the next 0.1 mm, so the length on the button is the one sent."""
    return math.ceil(mm * 10.0 - 1e-9) / 10.0


def recovery_step(trip: LimitTrip, state: MachineState,
                  window: Optional[Tuple[Optional[float], Optional[float]]]) -> Tuple[Optional[float], str]:
    """(the recovery jog's length, "") or (None, why not).

    Unhomed: RECOVERY_STEP_MM - motion's window is then the joint's
    position plus or minus its whole travel. Homed: the joint stands outside
    its soft limit when the switch is the real one (beyond the limits on the
    rig), and motion drops a homed jog whose target is outside, so the jog
    must end at least RECOVERY_INSIDE_MARGIN_MM inside: max(RECOVERY_STEP_MM,
    the way in plus the margin), at most RECOVERY_MAX_STEP_MM, and its
    target within the limits."""
    homed = any(j.joint == trip.joint and j.phase is HomingPhase.HOMED for j in state.homing.joints)
    if not homed:
        return RECOVERY_STEP_MM, ""
    lo, hi = window if window is not None else (None, None)
    pos = next((p.machine for p in state.positions if p.axis == trip.axis), None)
    if lo is None or hi is None or pos is None:
        return None, "{} yumuşak limitleri bilinmiyor — kurtarma adımı hesaplanamadı.".format(trip.axis)
    outside = (lo - pos) if trip.away > 0 else (pos - hi)
    step = RECOVERY_STEP_MM
    if outside > 0:
        step = max(step, _round_up(outside + RECOVERY_INSIDE_MARGIN_MM))
    if step > RECOVERY_MAX_STEP_MM:
        return None, (
            "{} yumuşak limitin {:.1f} mm dışında — tek kurtarma adımı en çok {:g} mm; "
            "kurtarma yapılmadı.".format(trip.axis, outside, RECOVERY_MAX_STEP_MM))
    target = pos + trip.away * step
    if not lo <= target <= hi:
        return None, (
            "{}{} {} mm yumuşak limitlerin dışına çıkar ({}…{} mm) — LinuxCNC bu hareketi "
            "yapmaz; kurtarma yapılmadı.".format(trip.axis, _sign(trip.away), _mm(step), _mm(lo), _mm(hi)))
    return step, ""


def _program_busy(state: MachineState) -> bool:
    """A program or an MDI line runs or is paused (MachineState.program_busy:
    LinuxCncAdapter._running's rule, AUTO or MDI with the interpreter not
    idle)."""
    return state.program_busy


def recovery_plan(state: MachineState, profile: MachineProfile,
                  windows: Mapping[int, Tuple[Optional[float], Optional[float]]]) -> Optional[RecoveryPlan]:
    """What «Limitten kurtar» would do now, None when no switch is pressed.

    It leaves the first pressed switch (limit_trips' order). Refused, with
    the reason in the plan, while the board link is lost (the flags are then
    stale; the link reset comes first), with the drive power cut, while a
    program or MDI line runs, while a joint homes, while the machine moves,
    when one joint's two switches read pressed at once (no direction is
    safe then, and motion skips its own direction check, motion/command.c:
    118-124), or when no single jog can bring the joint inside its soft
    limits (recovery_step)."""
    trips = limit_trips(state, profile)
    if not trips:
        return None
    trip, others = trips[0], tuple(t for t in trips[1:] if t.joint != trips[0].joint)

    def refused(why: str) -> RecoveryPlan:
        return RecoveryPlan(trip, None, why, others)

    if state.link.watchdog_has_bit or state.link.io_error:
        return refused(LINK_FIRST)
    if state.estop.drive_power is DrivePower.CUT:
        return refused(DRIVE_POWER_CUT)
    if _program_busy(state):
        return refused(PROGRAM_BUSY)
    if state.homing.any_homing:
        return refused(HOMING_BUSY)
    if state.moving:
        return refused(MOVING_BUSY)
    if any(t.joint == trip.joint and t.end != trip.end for t in trips):
        return refused(
            "{} ekseninde iki switch birden basılı — kablo ya da switch arızası; "
            "kurtarma yapılmadı.".format(trip.axis))
    step, why = recovery_step(trip, state, windows.get(trip.joint))
    if step is None:
        return refused(why)
    return RecoveryPlan(trip, step, "", others)


# -- jog (plan WP06) ----------------------------------------------------------

#: The longest jog step while the joint is not homed, in mm (plan WP06,
#: design (d)). Chosen, not measured. Unhomed, motion's only bound is the
#: joint's position plus or minus its whole travel, re-centred at every jog
#: (motion/command.c:161-175): nothing else limits one click, or a run of
#: them, and an axis that meets a switch stops the machine.
UNHOMED_MAX_STEP_MM = 10.0

#: The same toward an end that has no switch (profile.missing_switches: the
#: rig's Z bottom), where nothing at all stops the axis before the table.
#: Chosen, not measured.
UNHOMED_NO_SWITCH_MAX_MM = 1.0

#: A homed axis with less room than this to its soft limit stands on it: no
#: jog toward it. Chosen, not measured: the DRO's last digit.
AT_SOFT_LIMIT_MM = 0.001

#: How far inside its soft limit a jog cut to the limit ends, in mm. Motion
#: drops a jog whose target lies beyond the limit without a word
#: (motion/axis.c:289-290, motion/command.c:879-885), and it adds the step to
#: its planner's own target (axis.c:283-287, command.c:873-877), which the
#: commanded position read here (stat.position, task/taskintf.cc:1600; in
#: teleop the planner's position, motion/control.c:1404) trails at rest by up
#: to the planner's tiny_dp (motion/simple_tp.c:33-45, simple_tp.h:23: 5e-8 mm
#: on the rig) plus float rounding - a jog cut exactly to the limit could
#: land a hair past it and be dropped. Chosen, not measured: far above both,
#: far below the DRO's last digit.
JOG_LIMIT_MARGIN_MM = 0.0001

#: The jog card's step chips, mm (viewmodel.control_screen).
JOG_STEPS_MM = (0.01, 0.1, 1.0, 10.0, 50.0)

#: A click while a joint homes: motion would refuse it and the abort after
#: that refusal would cut the homing short; DURDUR is the way to end it.
JOG_HOMING = "Referans alma sürüyor — jog yok. Durdurmak için «Durdur»."
#: A refused click's words for each MachineState.jog_block key (the jog
#: card says them its own way, viewmodel.JOG_OFF_REASONS).
JOG_BLOCK_TEXTS = {
    JOG_BLOCK_UNKNOWN: "Makine durumu bilinmiyor — jog yok.",
    JOG_BLOCK_OFF: "Makine devre dışı — jog yok.",
    JOG_BLOCK_HOMING: JOG_HOMING,
    JOG_BLOCK_UNREAD: "Program durumu okunamadı — jog yok.",
    JOG_BLOCK_PAUSED: "Program duraklatılmışken jog yok.",
    JOG_BLOCK_RUNNING: "Program çalışırken jog yok.",
}
#: An incremental jog adds its step to the target of the one still running
#: (command.c:873-877, axis.c:283-287): no second click until it ends.
JOG_MOVING = "Hareket sürüyor — bitmesini bekleyin ya da «Durdur»."
#: Whether the machine moves was not read (MachineState.moving None).
JOG_MOVING_UNREAD = "Hareket durumu okunamadı — jog yok."


class JogDecision(NamedTuple):
    """What a jog click may do (jog_refusal, jog_plan)."""

    #: Why it is refused, "" when it may go.
    refusal: str
    #: The increment to send, mm: the step, or the room to the soft limit
    #: when that is less; None when refused.
    step_mm: Optional[float]
    #: Said instead of the plain "sent" line when the step was cut by a
    #: visible amount; "" otherwise.
    note: str = ""


def _pos(value: float) -> str:
    """A position or a limit as the DRO shows it, the minus typographic."""
    return "{:.3f}".format(value).replace("-", "−")


def axis_windows(config) -> Mapping[str, Tuple[Optional[float], Optional[float]]]:
    """axis letter -> its soft limits, (MIN_LIMIT, MAX_LIMIT) as
    soft_windows reads them - what motion checks a jog against: a homed
    joint jog against the joint's ([JOINT_n], motion/command.c:161-175,
    :879-885), a teleop jog against the axis's ([AXIS_<L>], axis.c:289-290).
    With trivkins and the rig's INI the two are the same numbers
    (test_config_traceability ties them). Keyed by letter, the axis a jog
    button names. An end the INI does not give is None: LinuxCNC's default
    there is +-1e99 (ini/inijoint.cc:131-140), no limit at all."""
    return {a.axis: (a.travel.min_limit, a.travel.max_limit) for a in config.axes}


def jog_block_text(state: MachineState) -> str:
    """A refused click's words for MachineState.jog_block, "" when no block
    holds."""
    return JOG_BLOCK_TEXTS.get(state.jog_block, "")


def jog_refusal(state: MachineState, profile: MachineProfile,
                windows: Mapping[str, Tuple[Optional[float], Optional[float]]],
                axis: str, direction: int, step: float,
                teleop: Optional[bool] = None) -> JogDecision:
    """Whether a jog click of `axis` in `direction` (+1/-1) by `step` mm may
    go now, and how far (plan WP06): the one rule both adapters send by and
    the jog card's buttons follow. In order:

      - MachineState.jog_block: the status unknown, the machine off, a joint
        homing (motion refuses the jog and the abort that follows cuts the
        homing short), a program or MDI line paused or running - or one of
        these not read;
      - the machine moving: a second click would extend the first
        (JOG_MOVING), and a "not read" moving refuses too;
      - then jog_plan: the click's own axis, direction and step.

    `windows`: axis_windows of the INI. `teleop`: whether the jog goes as an
    axis (teleop) jog, None to predict it from the state (jog_plan)."""
    block = jog_block_text(state)
    if block:
        return JogDecision(block, None)
    if state.moving is None:
        return JogDecision(JOG_MOVING_UNREAD, None)
    if state.moving:
        return JogDecision(JOG_MOVING, None)
    return jog_plan(state, profile, windows, axis, direction, step, teleop)


def _missing_switch(profile: MachineProfile, axis: str, end: str) -> Optional[MissingSwitch]:
    """The profile's note that `axis` has no switch at `end`, or None."""
    for m in profile.missing_switches:
        if m.axis == axis and m.end == end:
            return m
    return None


def jog_plan(state: MachineState, profile: MachineProfile,
             windows: Mapping[str, Tuple[Optional[float], Optional[float]]],
             axis: str, direction: int, step: float,
             teleop: Optional[bool] = None) -> JogDecision:
    """jog_refusal's part that depends on the click itself - the axis, the
    direction, the step - and not on whether jogging is possible at all:
    what the jog card asks for each button (viewmodel.jog_pad).

      - the step a positive finite number, the axis one the machine has;
      - never onto a pressed switch (plan WP05, toward_pressed_switch);
      - the joint not homed: at most UNHOMED_MAX_STEP_MM, and
        UNHOMED_NO_SWITCH_MAX_MM toward an end with no switch
        (profile.missing_switches); a joint jog's target also inside the
        joint's position plus or minus its whole travel (command.c:161-175,
        :879-885 - never the bound with these steps on the rig);
      - the joint homed, or the jog an axis (teleop) jog, which motion
        checks against the axis's limits homed or not (axis.c:289-290):
        the target inside the soft limits. Standing on the limit (less than
        AT_SOFT_LIMIT_MM of room), or outside it, no jog that way; a step
        longer than the room is cut to it, JOG_LIMIT_MARGIN_MM short, with
        a note; from outside, a step that would not reach back inside is
        refused with the least that would.

    Motion would drop a jog past these limits without a word (command.c:
    879-885, axis.c:289-290): refused or cut here, the operator is told.
    The target counts from the commanded position (AxisPosition.commanded,
    where motion's planner stands; the machine position where an adapter
    reads none).

    `teleop` None predicts the mode the jog goes in: an axis jog when every
    joint is homed - task's MANUAL then puts motion in teleop
    (task/emctask.cc:276-280) - or motion is in teleop already, else a
    joint jog (LinuxCncAdapter._select_jog_mode, axis.py get_jog_mode).
    LinuxCncAdapter asks again with the mode it switched to."""
    if direction not in (1, -1):
        raise ValueError("jog direction must be +1 or -1, got {!r}".format(direction))

    def refused(why: str) -> JogDecision:
        return JogDecision(why, None)

    if not (isinstance(step, (int, float)) and math.isfinite(step) and step > 0):
        return refused("Geçersiz jog adımı: {!r} mm.".format(step))
    joints = [j for j in state.homing.joints if j.axis == axis]
    pos = next((p for p in state.positions if p.axis == axis), None)
    if not joints or pos is None:
        return refused("Bu makinede {} ekseni yok — jog yok.".format(axis))
    onto = toward_pressed_switch(limit_trips(state, profile), axis, direction)
    if onto is not None:
        return refused(jog_refused_text(onto))
    sign = _sign(direction)
    homed = all(j.phase is HomingPhase.HOMED for j in joints)
    if teleop is None:
        teleop = state.homing.all_homed or state.motion_mode == "teleop"
    lo, hi = windows.get(axis, (None, None))
    at = pos.commanded if pos.commanded is not None else pos.machine
    if not homed:
        missing = _missing_switch(profile, axis, "+" if direction > 0 else "-")
        cap = UNHOMED_NO_SWITCH_MAX_MM if missing is not None else UNHOMED_MAX_STEP_MM
        if step > cap:
            if missing is not None:
                return refused(
                    "{a} referanssız ve {l} switch'i yok — {a}{s} adımı en çok {c:g} mm "
                    "(seçili {n:g} mm).".format(a=axis, l=missing.label, s=sign, c=cap, n=step))
            return refused("{} referanssız — jog adımı en çok {:g} mm (seçili {:g} mm).".format(
                axis, cap, step))
        if not teleop and lo is not None and hi is not None:
            travel = hi - lo
            if abs(at + direction * step - pos.machine) > travel:
                return refused(
                    "{}{} {:g} mm, referanssız jog sınırının ({:g} mm) dışına çıkar — jog "
                    "yok.".format(axis, sign, step, travel))
    note = ""
    if homed or teleop:
        limit = hi if direction > 0 else lo
        room = math.inf if limit is None else (limit - at) * direction
        if room < AT_SOFT_LIMIT_MM:
            if room <= -AT_SOFT_LIMIT_MM:
                return refused("{a} yumuşak limitin {d:.3f} mm dışında (limit {l} mm) — {a}{s} "
                               "yönünde jog yok.".format(a=axis, d=-room, l=_pos(limit), s=sign))
            return refused("{a} yumuşak limitte ({l} mm) — {a}{s} yönünde jog yok.".format(
                a=axis, l=_pos(limit), s=sign))
        sent = float(min(step, room - JOG_LIMIT_MARGIN_MM))
        near = lo if direction > 0 else hi
        if near is not None and (near - at) * direction > 0:
            # Outside the window behind the jog: the target must come back in.
            need = (near - at) * direction + JOG_LIMIT_MARGIN_MM
            if sent < need:
                return refused(
                    "{a} yumuşak limitin {d:.3f} mm dışında (limit {l} mm) — {a}{s} adımı en az "
                    "{n:.3f} mm olmalı.".format(a=axis, d=(near - at) * direction, l=_pos(near),
                                                s=sign, n=math.ceil(need * 1000.0) / 1000.0))
        if step - sent >= AT_SOFT_LIMIT_MM / 2:
            # A cut the DRO's last digit would not show is not mentioned.
            note = ("{a}{s} en çok {r:.3f} mm gidebilir (yumuşak limit {l} mm) — adım {n:.3f} mm'ye "
                    "kısaltıldı.".format(a=axis, s=sign, r=room, l=_pos(limit), n=sent))
        return JogDecision("", sent, note)
    return JogDecision("", float(step), note)


def jog_sent_text(axis: str, direction: int, step: float) -> str:
    """Said when a jog went out uncut: 'Jog X+1 mm.' - what moved, which
    way, how far; no "joint" or "axis" (plan WP06)."""
    return "Jog {}{}{:g} mm.".format(axis, _sign(direction), step)


def unhomed_step_note(profile: MachineProfile, axes: Sequence[str]) -> str:
    """The jog card's standing words while the joints of `axes` are not
    homed: the step caps jog_plan applies to them (plan WP06)."""
    text = "adım en çok {:g} mm".format(UNHOMED_MAX_STEP_MM)
    for m in profile.missing_switches:
        if m.axis in axes:
            text += ", {}{} en çok {:g} mm ({} switch'i yok)".format(
                m.axis, _sign(1 if m.end == "+" else -1), UNHOMED_NO_SWITCH_MAX_MM, m.label)
    return text


# -- homing (plan WP07) -------------------------------------------------------

#: The axis that carries the tool. Homing another axis alone while it is
#: unhomed can drag a lowered tool through the work - why the rig's INI
#: homes it first (configs/test-rig/test-rig.ini, the HOME_SEQUENCE note:
#: "Z first so the tool lifts clear before X and Y move").
TOOL_AXIS = "Z"

#: Why no homing may start now (home_block), the homing card's words and a
#: refused press's alike.
HOME_UNKNOWN = "Makine durumu bilinmiyor — referans alınamaz."
HOME_OFF = "Makine devre dışı — referans alınamaz."
HOME_UNREAD = "Program durumu okunamadı — referans alınamaz."
HOME_PAUSED = "Program duraklatılmışken referans alınamaz."
HOME_RUNNING = "Program çalışırken referans alınamaz."
#: A homing runs: another would leave joint 0 unhomed (homing.c:553-560);
#: DURDUR cancels it (command.c:509-520).
HOME_BUSY = "Referans alma sürüyor — durdurmak için «Durdur»."
#: The press's own questions (home_refusal), not the card's: a card that
#: went off and on with every jog would move under the finger.
HOME_MOVING = "Hareket sürüyor — bitmesini bekleyin ya da «Durdur»."
HOME_MOVING_UNREAD = "Hareket durumu okunamadı — referans alınamaz."
#: The homing card's note while a joint homes: what DURDUR does, and what
#: is left when it lands in the latch phase, on the home switch - the limit
#: switch on X−, Y− and Z+ (the module docstring, "Homing").
HOME_STOP_NOTE = (
    "«Durdur» (Esc) referans almayı keser. Eksen switch'in üstündeyken kesilirse orada kalır "
    "ve makine durur — o zaman «Limitten kurtar», sonra yeniden referans."
)


def home_block(state: MachineState, profile: MachineProfile) -> str:
    """Why no homing - all joints or one - may start now, "" when one may:
    the homing card's buttons follow it and both adapters refuse by it
    (home_refusal). In order:

      - the status unknown (nothing below is read then);
      - a switch pressed (limit_trips, a homing joint's own home switch not
        counted), named with the way off: even with the override a homing
        fails - the mask clears when the first joint's homing move pauses
        (control.c:1263-1283) and the joint on its switch then stops the
        machine (seen in sim/; home_refused_text). Not while the board link
        is lost: the flags are stale then (state.HardLimit), and the
        machine is off anyway;
      - the machine not ON - task takes a home command only then
        (emctaskmain.cc:911);
      - a program or MDI line paused or running - for a home task must be
        in MANUAL, and it refuses to leave AUTO with the interpreter busy,
        while leaving MDI aborts the line (emctaskmain.cc:2125-2171) - or
        that not read;
      - a joint homing (HOME_BUSY)."""
    if not state.status_known:
        return HOME_UNKNOWN
    if not (state.link.watchdog_has_bit or state.link.io_error):
        trips = limit_trips(state, profile)
        if trips:
            return home_refused_text(trips)
    if state.estop.blocks_motion:
        return HOME_OFF
    if state.program_paused is None or state.mdi_running is None:
        return HOME_UNREAD
    if state.program_paused:
        return HOME_PAUSED
    if state.program_busy or state.mdi_running:
        return HOME_RUNNING
    if state.homing.any_homing:
        return HOME_BUSY
    return ""


#: One axis's homing refused: its HOME_SEQUENCE is not known (no INI), so
#: whether motion would home other joints with it cannot be said.
HOME_ONE_SEQ_UNKNOWN = ("{} ekseninin HOME_SEQUENCE'i bilinmiyor (INI yok) — tek başına "
                        "referans alınmaz; «Tümünü referansla».")
#: One axis's homing refused: a negative HOME_SEQUENCE homes its group.
HOME_ONE_GROUP = ("{} negatif HOME_SEQUENCE ile {} ile birlikte referanslanır — tek başına "
                  "referans alınmaz; «Tümünü referansla».")


def home_axis_refusal(state: MachineState, axis: str) -> str:
    """Why `axis` cannot be homed alone on this machine, "" when it can: it
    must be the letter of exactly one joint - AXIS homes no duplicated
    letter alone (axis.py:2715-2717); on a gantry its joints share a
    sequence and home together. And that joint must home alone: with a
    negative HOME_SEQUENCE motion homes every joint sharing its
    abs(sequence) (do_home_one_joint, homing.c:265-281), so its button
    would move axes it does not name - refused, naming them, as every
    motion button says what moves (P0 cleanup, WP07 review); with the
    sequence not known (no INI) whether it would cannot be said - refused
    too."""
    joints = [j for j in state.homing.joints if j.axis == axis]
    if not joints:
        return "Bu makinede {} ekseni yok — referans alınamaz.".format(axis)
    if len(joints) > 1:
        return "{} ekseni birden çok eklemde — tek başına referans alınmaz; «Tümünü referansla».".format(
            axis)
    group = state.homing.homed_with(joints[0])
    if group is None:
        return HOME_ONE_SEQ_UNKNOWN.format(axis)
    others = [j.axis for j in group if j.joint != joints[0].joint]
    if others:
        return HOME_ONE_GROUP.format(axis, ", ".join(others))
    return ""


def home_refusal(state: MachineState, profile: MachineProfile, axis: Optional[str] = None) -> str:
    """Why a press of «Tümünü referansla» (`axis` None) or of one axis's
    button must send nothing now, "" when it may: home_block, then the
    press's own questions - the axis homeable alone (home_axis_refusal),
    and the machine standing still: a homing would start under a jog still
    running (command.c:1366-1388 does not ask), and a "not read" moving
    refuses too."""
    block = home_block(state, profile)
    if block:
        return block
    if axis is not None:
        problem = home_axis_refusal(state, axis)
        if problem:
            return problem
    if state.moving is None:
        return HOME_MOVING_UNREAD
    if state.moving:
        return HOME_MOVING
    return ""


def home_move(joint: JointHoming, profile: MachineProfile) -> str:
    """Where `joint`'s homing search goes, from the INI and the profile:
    toward the switch at the end its HOME_SEARCH_VEL points to - 'Z üst
    switch'ine (Z+)' - or, with no switch named there, the direction alone;
    with no search (HOME_SEARCH_VEL 0), where the joint stands becomes its
    reference (HOME_SET_SWITCH_POSITION, motion/homing.c:807-811); with
    no INI, that the direction is not known."""
    if joint.search_vel is None:
        return "{} (arama yönü bilinmiyor — INI yok)".format(joint.axis)
    if joint.search_vel == 0:
        return "{} (arama yok — bulunduğu yer referans olur)".format(joint.axis)
    end = "+" if joint.search_vel > 0 else "-"
    sign = _sign(1 if end == "+" else -1)
    inp = _input_for(profile, joint.axis, end)
    if inp is None:
        return "{}{} yönüne".format(joint.axis, sign)
    return "{} switch'ine ({}{})".format(inp.label, joint.axis, sign)


def home_route(homing: HomingState, profile: MachineProfile) -> str:
    """Home All's moves in its order (HomingState.required_order, the INI's
    HOME_SEQUENCE): joints of one step together ('ve'), the steps one after
    the other ('sonra') - 'Z üst switch'ine (Z+), sonra Y ön uç switch'ine
    (Y−), sonra X sol uç switch'ine (X−)' on the rig. With the order not
    known (no INI): every axis, and that the order is not known."""
    if not homing.order_known:
        return "{} ({})".format(
            " ve ".join(home_move(j, profile) for j in homing.joints), ORDER_UNKNOWN)
    steps: List[List[str]] = []
    last = None
    for j in homing.required_order:
        if abs(j.sequence) != last:
            steps.append([])
            last = abs(j.sequence)
        steps[-1].append(home_move(j, profile))
    return ", sonra ".join(" ve ".join(step) for step in steps)


def home_question(state: MachineState, profile: MachineProfile, axis: Optional[str] = None) -> str:
    """What to ask before a homing the rules allow (home_refusal ""), ""
    for none - the adapters return it to app.Session, which shows it with
    «Evet» / «Vazgeç» and runs the homing on «Evet» (plan WP07):

      - Home All (`axis` None) with any joint homed: a homing again, which
        unhomes every joint as it starts (homing.c:401-410) and drives each
        to its switch with the limits ignored - the moves in their order
        (home_route). The first homing, nothing homed, asks nothing (plan
        design (g));
      - one axis already homed: the same, for that axis (axis.py:
        2708-2728 asks too);
      - another axis than TOOL_AXIS while that one is unhomed: a lowered
        tool would be dragged along.

    Each question ends with the moves and 'yol açık mı?': the operator
    answers for what will move (plan safety rules)."""
    joints = state.homing.joints
    if axis is None:
        if not any(j.phase is HomingPhase.HOMED for j in joints):
            return ""
        return "Yeniden referans: {} gidecek — yol açık mı?".format(
            home_route(state.homing, profile))
    target = [j for j in joints if j.axis == axis]
    if not target:
        return ""
    parts = []
    if axis != TOOL_AXIS and any(
            j.axis == TOOL_AXIS and j.phase is not HomingPhase.HOMED for j in joints):
        parts.append("{} referanssız: takım aşağıdaysa {} hareketi çarpabilir.".format(
            TOOL_AXIS, axis))
    again = any(j.phase is HomingPhase.HOMED for j in target)
    if not parts and not again:
        return ""
    route = home_move(target[0], profile)
    if again:
        parts.append("{} yeniden referanslanacak: {} gidecek — yol açık mı?".format(axis, route))
    else:
        parts.append("{} gidecek — yol açık mı?".format(route))
    return " ".join(parts)


def home_started_text(axis: Optional[str] = None) -> str:
    """Said once the home command went out (not waited for: its progress
    shows on the homing chips)."""
    if axis is None:
        return "Referans alma başlatıldı."
    return "Referans alma başlatıldı: yalnız {}.".format(axis)


# -- the operator's words ---------------------------------------------------


def pressed_phrase(trips: Sequence[LimitTrip]) -> str:
    """'X sol uç switch'i basılı', or several: 'X sol uç ve Y ön uç
    switch'leri basılı'."""
    labels = [t.label for t in trips]
    if len(labels) == 1:
        return "{} switch'i basılı".format(labels[0])
    return "{} ve {} switch'leri basılı".format(", ".join(labels[:-1]), labels[-1])


def on_refused_text(trips: Sequence[LimitTrip]) -> str:
    """«Yazılım sıfırla» sends no ON onto a pressed switch (motion would
    stop the machine at once)."""
    return "{} — LinuxCNC bir eksen limitteyken makineyi açmaz. «Limitten kurtar»a basın.".format(
        pressed_phrase(trips))


def home_refused_text(trips: Sequence[LimitTrip]) -> str:
    """Homing is refused while a switch is pressed: even with the override a
    Home All fails - the mask clears when the first joint's homing move
    pauses, and the joint on its switch trips (seen in sim/)."""
    return "{} — önce «Limitten kurtar»: referans alma switch üstünden başlayamaz.".format(
        pressed_phrase(trips))


def jog_refused_text(trip: LimitTrip) -> str:
    """A jog onto a pressed switch, refused."""
    return "{} basılı — yalnız {}{}.".format(trip.label, trip.axis, _sign(trip.away))


def switch_pressed_text(trip: LimitTrip) -> str:
    """The Kontrol limit card's line for one switch."""
    return "{} switch'i BASILI".format(trip.label)


def recovery_label(plan: RecoveryPlan) -> str:
    """The button: what will move, which way, how far. Without a length
    (refused) it names the axis and the direction only."""
    t = plan.trip
    if plan.step_mm is None:
        return "Limitten kurtar: {}{} yönünde".format(t.axis, _sign(t.away))
    return "Limitten kurtar: {}{} yönünde {} mm".format(t.axis, _sign(t.away), _mm(plan.step_mm))


def recovery_started_text(plan: RecoveryPlan) -> str:
    """Said when the recovery jog went out."""
    t = plan.trip
    text = "{} switch'ten çıkarılıyor: {}{} mm. Limit koruması bu hareket bitince kendiliğinden geri gelir.".format(
        t.axis, _sign(t.away), _mm(plan.step_mm or 0.0))
    if plan.others:
        text += " Ayrıca basılı: {} — bu hareket bitince makine yine duracak; sonra «Limitten kurtar»a tekrar basın.".format(
            ", ".join(o.label for o in plan.others))
    return text


def released_text(label: str, homed: bool) -> str:
    """The switch left and the override over: protection is back."""
    return "{} serbest; limit koruması yeniden etkin.{}".format(label, "" if homed else " Referans alın.")


def still_on_text(axis: str) -> str:
    """The recovery jog ended with the axis still on its switch: the
    machine stopped again."""
    return "{} hâlâ switch'te — «Limitten kurtar»a tekrar basın.".format(axis)
