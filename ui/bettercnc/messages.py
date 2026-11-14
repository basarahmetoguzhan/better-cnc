"""LinuxCNC's error texts in the operator's words.

The error box (render.render_msgband, MachineState.last_error) shows the
latest error in Turkish, with LinuxCNC's own text under it. LinuxCNC 2.9 has
no Turkish catalogue (reference/linuxcnc/src/po has no tr.po), and its texts
number joints ("joint 0") where the operator thinks in axes and switches. So
the texts the test rig meets most are matched here and rewritten: the axis by
its letter, the switch by its label, the soft-limit window in numbers. Any
other text stays LinuxCNC's own behind "LinuxCNC: " (operator_text); the raw
text always stays in the alarm log as well.

Each pattern is a LinuxCNC 2.9 format string, cited by file:line under
reference/linuxcnc/ and checked against that source by ui/tests/
test_messages.py whenever reference/ is present. A text LinuxCNC rewords
stops matching and falls back to its raw form: never a wrong translation.

Pure: no LinuxCNC, no I/O. The adapter supplies what a translation may name
(Facts, from the profile and the INI) and the switch inputs as read now.
"""

from __future__ import annotations

import re
from typing import Iterable, List, Mapping, NamedTuple, Optional, Sequence, Tuple

from .profiles import DigitalInput

#: A soft-limit window, (MIN_LIMIT, MAX_LIMIT) in machine units; None for an
#: end the INI does not give.
Limits = Tuple[Optional[float], Optional[float]]

#: What every untranslated text is shown behind (operator_text).
RAW_PREFIX = "LinuxCNC: "


class Facts(NamedTuple):
    """What a translation may name, read once by the adapter.

    joint_axes: joint number -> axis letter (the adapter's joint config).
    inputs: the profile's switch inputs, for a switch's label.
    axis_limits: axis letter -> [AXIS_<L>] MIN_LIMIT / MAX_LIMIT, the window
        an axis report checks (iniaxis.cc:84, :95; axis.c:540-564).
    joint_limits: joint number -> [JOINT_<n>] MIN_LIMIT / MAX_LIMIT, the
        window a joint report checks (inijoint.cc:132, :140; command.c:259-269).

    The limits are the ones LinuxCNC enforces, provisional or not: they
    explain the refusal LinuxCNC made with them.
    """

    joint_axes: Mapping[int, str]
    inputs: Tuple[DigitalInput, ...] = ()
    axis_limits: Optional[Mapping[str, Limits]] = None
    joint_limits: Optional[Mapping[int, Limits]] = None


#: Nothing known: joints keep their numbers, no label, no window.
NO_FACTS = Facts(joint_axes={})

# -- LinuxCNC's texts, by where they are made (paths under reference/linuxcnc/) --

#: src/emc/motion/control.c:821 - "joint %d on limit switch error": motion
#: trips on an unmasked limit flag and disables (control.c:807-826).
_LIMIT_SWITCH = re.compile(r"^joint (\d+) on limit switch error$")

#: src/emc/motion/command.c:216, :221 - "%s move on line %d would exceed %c's
#: %s limit" (an axis, axis_check_constraints); :261, :267 - "%s move on line
#: %d would exceed joint %d's positive|negative limit". inRange (command.c:
#: 191-272) reports every failing axis, then every failing joint: on the rig
#: (trivkins, [AXIS_*] = [JOINT_n] limits) one target past X's window gives
#: both reports. MDI moves carry line 0.
_SOFT_LIMIT = re.compile(
    r"^(.+) move on line (-?\d+) would exceed (?:([XYZABCUVW])|joint (\d+))'s "
    r"(positive|negative) limit$"
)

#: src/emc/motion/command.c:1003 - after inRange refused a linear move
#: (:1002): the move's own report came first.
LINEAR_INVALID = "invalid params in linear command"

#: Every inRange report of a move (command.c:216-268, with "fails
#: kinematicsInverse" :239 and "gave non-finite joint location" :254): what
#: LINEAR_INVALID follows.
_MOVE_REPORT = re.compile(
    r"^.+ move on line -?\d+ (?:would exceed |fails kinematicsInverse|gave non-finite joint location)"
)

#: src/emc/motion/command.c:130, :135 (hard), :141, :146 (soft) - "Can't jog
#: joint %d further past max|min hard|soft limit." (joint_jog_ok, :108-152).
_JOG_PAST = re.compile(r"^Can't jog joint (\d+) further past (max|min) (hard|soft) limit\.$")

#: src/emc/motion/command.c:786 and :933 ("joints"), :854 ("joint", the
#: incremental jog this UI sends).
_JOG_WHILE_HOMING = re.compile(r"^Can't jog any joints? while homing\.$")

#: src/emc/task/emctaskmain.cc:2197 - "can't open %s" (PLAN_OPEN refused).
_CANT_OPEN = re.compile(r"^can't open (.+)$")

#: src/emc/rs274ngc/rs274ngc_return.hh:152 - NCE_UNABLE_TO_OPEN_FILE, "Unable
#: to open file <%s>", passed on by task (emctask.cc:70).
_UNABLE_TO_OPEN = re.compile(r"^Unable to open file <(.*)>$")

#: src/emc/motion/command.c:1376 - JOINT_HOME outside free mode.
HOME_NEEDS_JOINT_MODE = "must be in joint mode to home"

#: src/emc/task/emctask.cc:270 - a task mode change while a jog runs.
MODE_CHANGE_WHILE_JOGGING = "Ignoring task mode change while jogging"


def _mm(value: float) -> str:
    """A machine position as the operator reads it: shortest form, the
    minus sign typographic (U+2212), as the jog buttons write it."""
    return "{:g}".format(value).replace("-", "−")


def _sign(end: str) -> str:
    """'+' or '-' as an end of travel is written: + or U+2212."""
    return "+" if end == "+" else "−"


def _axis(facts: Facts, joint: int) -> str:
    """The axis letter of `joint`, or "Eklem N" when the facts do not say."""
    return facts.joint_axes.get(joint) or "Eklem {}".format(joint)


def _label(facts: Facts, axis: str, end: str) -> Optional[str]:
    """The profile's label of the switch at `axis`'s `end`, if it has one."""
    for inp in facts.inputs:
        if inp.axis == axis and inp.end == end:
            return inp.label
    return None


def _pressed(facts: Facts, axis: str,
             input_values: Iterable[Tuple[str, Optional[bool]]]) -> List[str]:
    """Labels of `axis`'s switches whose input reads pressed now."""
    live = dict(input_values)
    return [inp.label for inp in facts.inputs if inp.axis == axis and live.get(inp.hal_pin)]


#: What to do after a limit trip while the switch reads pressed: the
#: one-press recovery (plan WP05, LinuxCncAdapter.recover_limit).
RECOVER_HINT = "«Limitten kurtar» ile switch'ten çıkın."


def limit_trip_text(axis: str, pressed: Sequence[str], hint: str = RECOVER_HINT) -> str:
    """A limit trip in the operator's words: the one text the error box
    shows for it, whether it comes from LinuxCNC's report (translate, the
    switch whose input reads pressed) or from the stop that motion's flags
    show (LinuxCncAdapter._note_task_state, plan WP05). `pressed`: the
    labels of `axis`'s switches that read pressed; `hint`: what to do
    while one does (in observer mode, AXIS's way).

    One pressed: '<label> switch'ine çarptı — makine durdu.' and the hint.
    Both ends at once: no direction is safe, so no recovery is offered.
    None read pressed (no reading, or released since): the axis only."""
    if len(pressed) == 1:
        return "{} switch'ine çarptı — makine durdu. {}".format(pressed[0], hint)
    if pressed:
        return "{} birden basılı — makine durdu; kablo ya da switch arızası olabilir.".format(
            " ve ".join(pressed))
    return "{} limit switch'ine çarptı — makine durdu.".format(axis)


def translate(
    raw: str,
    facts: Facts = NO_FACTS,
    input_values: Sequence[Tuple[str, Optional[bool]]] = (),
) -> Optional[str]:
    """The operator's Turkish line for LinuxCNC's text `raw`, or None when
    the text is not one this module knows.

    `input_values`: the switch inputs as read now, (HAL pin, value) as
    MachineState.input_values carries them; a limit trip names the switch
    that reads pressed.
    """
    text = raw.strip()

    m = _LIMIT_SWITCH.match(text)
    if m:
        axis = _axis(facts, int(m.group(1)))
        return limit_trip_text(axis, _pressed(facts, axis, input_values))

    m = _SOFT_LIMIT.match(text)
    if m:
        line = int(m.group(2))
        if m.group(3):
            axis = m.group(3)
            low, high = (facts.axis_limits or {}).get(axis, (None, None))
        else:
            joint = int(m.group(4))
            axis = _axis(facts, joint)
            low, high = (facts.joint_limits or {}).get(joint, (None, None))
        if low is not None and high is not None:
            window = "izinli {}…{} mm, makine".format(_mm(low), _mm(high))
        else:
            window = "{} sınır aşıldı".format("üst" if m.group(5) == "positive" else "alt")
        where = "Satır {}: ".format(line) if line > 0 else ""
        return "{}{} hedefi yumuşak limit dışında ({}) — reddedildi".format(where, axis, window)

    if text == LINEAR_INVALID:
        return "Geçersiz doğrusal hareket (çoğunlukla yumuşak limit dışı hedef) — reddedildi"

    m = _JOG_PAST.match(text)
    if m:
        axis = _axis(facts, int(m.group(1)))
        end = "+" if m.group(2) == "max" else "-"
        away = "{}{}".format(axis, _sign("-" if end == "+" else "+"))
        if m.group(3) == "hard":
            label = _label(facts, axis, end)
            pressed = "{} basılı".format(label) if label else "{}{} ucu basılı".format(axis, _sign(end))
            return "{} switch'e doğru jog yapılamaz — {}, yalnız {}".format(axis, pressed, away)
        return "{}{} yönünde jog sınırına ulaşıldı — yalnız {}".format(axis, _sign(end), away)

    if _JOG_WHILE_HOMING.match(text):
        return "Referans alma sürerken jog yapılamaz"

    m = _CANT_OPEN.match(text) or _UNABLE_TO_OPEN.match(text)
    if m:
        return "Dosya açılamadı: {}".format(m.group(1))

    if text == HOME_NEEDS_JOINT_MODE:
        return "Referans için eklem moduna geçilemedi"

    if text == MODE_CHANGE_WHILE_JOGGING:
        return "Jog sürerken mod değiştirilmedi — jog bitince tekrar deneyin"

    return None


def operator_text(
    raw: str,
    facts: Facts = NO_FACTS,
    input_values: Sequence[Tuple[str, Optional[bool]]] = (),
) -> str:
    """translate(), or LinuxCNC's own text behind RAW_PREFIX."""
    said = translate(raw, facts, input_values)
    return said if said is not None else RAW_PREFIX + raw.strip()


def is_refusal(raw: str) -> bool:
    """Whether `raw` is one of the refusals translate() knows: a move, a
    jog, a homing, a file or a mode change LinuxCNC would not take, the
    machine staying ON. inRange's reports and LINEAR_INVALID drop the move
    (tpAbort and the motion error flag, command.c:1002-1007); joint_jog_ok's
    and the homing jog's drop the jog (the joint's error flag, :852-856,
    :866-871); JOINT_HOME outside free mode returns (:1374-1378); task's own
    refuse a command (emctaskmain.cc:2197, emctask.cc:70, :270). None of them
    clears emcmotInternal->enabling, which is what disables motion
    (control.c:867-891) and so takes LinuxCNC out of ON (determineState,
    emctask.cc:419-430); the faults that do clear it are reported by
    check_for_faults (control.c:781-859), the limit trip among them. So a
    refusal never explains the machine leaving ON
    (LinuxCncAdapter._note_task_state). A limit trip is not a refusal, nor
    is any text this module does not know."""
    text = raw.strip()
    return not _LIMIT_SWITCH.match(text) and translate(text) is not None


def is_follow_up(raw: str, previous: str) -> bool:
    """Whether LinuxCNC reports `raw` only as a consequence of `previous`,
    the error before it: then `raw` adds nothing the operator needs, and the
    error box keeps `previous`.

    - LINEAR_INVALID follows the move's own inRange report
      (command.c:1002-1003);
    - a second soft-limit report of the same move (the same line) follows
      the first: inRange reports every failing axis, then every failing
      joint (command.c:214-225, :244-270).

    Task forwards motion's errors one per cycle (taskintf.cc:2024-2028, once
    per emcMotionUpdate, emctaskmain.cc:3411), so these arrive apart and a
    drain can split them: in sim/ (2026-11-13) a POST's snapshot took the
    first report and the next poll the rest.
    """
    text = raw.strip()
    if text == LINEAR_INVALID:
        return bool(_MOVE_REPORT.match(previous.strip()))
    now, before = _SOFT_LIMIT.match(text), _SOFT_LIMIT.match(previous.strip())
    return bool(now and before and now.group(2) == before.group(2))


def ends_report(raw: str) -> bool:
    """Whether `raw` is the last thing LinuxCNC says about a refused move:
    LINEAR_INVALID closes inRange's reports of a linear move (command.c:
    1002-1003). After it, a report is a new move's, even with the same line
    number - MDI moves all carry line 0 - and motion's errors reach the
    channel in the order motion made them (one queue, taskintf.cc:
    2024-2028), so a line sent after a refusal reports after its
    LINEAR_INVALID. A circular, probe or rigid-tap move has no such last
    word (command.c:1070-1074, :1424-1428, :1493-1497)."""
    return raw.strip() == LINEAR_INVALID
