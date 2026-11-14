"""Plan WP04: the message band - a visible, sticky, Turkish error box, the
most specific LinuxCNC error kept, and the MDI line's real result.

Until WP04 every operator message, LinuxCNC's errors included, went to one
small grey footer line that the next message overwrote. Each error the
drain read overwrote the one before it, so of LinuxCNC's pair for a target
past the soft limits - "Linear move on line 0 would exceed Z's positive
limit", then "invalid params in linear command" (reference/linuxcnc/src/
emc/motion/command.c:216-268, then :1003) - the generic one won: the text
that confused the operator on the rig. And an MDI line was green
"Gönderildi." the moment it was sent, though motion refused it later.
Now:

  - bettercnc.messages translates the texts the rig meets most (axis by
    letter, switch by label, the soft-limit window in numbers); anything
    else stays LinuxCNC's own behind "LinuxCNC: ";
  - the adapters speak through _say(text, severity): an ERROR is also the
    error box (MachineState.last_error), which the next INFO or WARN does
    not clear and only «Tamam» (ack_error, local, allowed in observer mode)
    does;
  - one error per drain takes the box: the first that is not a follow-up
    of the last one the box took (messages.is_follow_up, within
    FOLLOW_UP_WINDOW_S - in sim/ the POST's snapshot drained the first
    report alone, the next poll the rest);
  - an MDI line is "sent" until its result is seen: "rejected" with the
    error's text, "done" when the interpreter is idle with no error, or
    "rejected" as cut short after a stop or with the machine off;
  - render_body puts the band at the top of every screen, the footer is
    gone.

LinuxCncAdapter runs against fake_linuxcnc, its errors put on the error
channel as LinuxCNC words them; the patterns are tied to the LinuxCNC
source when reference/ is present.
"""

from __future__ import annotations

import html as htmllib
import os
import re
import threading
import unittest
from typing import Dict, List, Optional

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS, REPO_ROOT

import fake_linuxcnc as F
# The module, not its names: a TestCase class imported by name would run
# here a second time. Only its FakeCase base is reused.
import test_linuxcnc_adapter as T
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import messages as M
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.configmodel import load_machine_config
from bettercnc.iniload import load_ini
from bettercnc.profiles import PROFILES, load_profile
from bettercnc.state import LastError, MdiEntry, Severity

C = F.CONSTANTS
CSS = app._read_static("app.css")
LINUXCNC = os.path.join(REPO_ROOT, "reference", "linuxcnc")
NEEDS_REFERENCE = unittest.skipUnless(
    os.path.isdir(os.path.join(LINUXCNC, "src")), "reference/linuxcnc not cloned (it is gitignored)"
)

#: The rig's switch inputs as the HAL nets take them (profiles.TEST_RIG_INPUTS).
X_LEFT = "hm2_7i92.0.gpio.021.in_not"
X_RIGHT = "hm2_7i92.0.gpio.020.in_not"
RIG_INPUT_PINS = {
    "hm2_7i92.0.gpio.020.in_not": False, "hm2_7i92.0.gpio.021.in_not": False,
    "hm2_7i92.0.gpio.022.in_not": False, "hm2_7i92.0.gpio.023.in_not": False,
    "hm2_7i92.0.gpio.024.in_not": False,
}

# What LinuxCNC prints for a target past the rig's windows (command.c:216-268,
# :1003), in the order it prints it - as seen in sim/ on 2026-11-13.
X500 = ("Linear move on line 0 would exceed X's positive limit",
        "Linear move on line 0 would exceed joint 0's positive limit",
        "invalid params in linear command")
Z0 = ("Linear move on line 0 would exceed Z's positive limit",
      "Linear move on line 0 would exceed joint 2's positive limit",
      "invalid params in linear command")
X_TEXT = "X hedefi yumuşak limit dışında (izinli 3…399 mm, makine) — reddedildi"
#: A limit trip on X's left switch in the operator's words (plan WP05).
TRIP_X_LEFT = "X sol uç switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın."
Z_TEXT = "Z hedefi yumuşak limit dışında (izinli −96.5…−3 mm, makine) — reddedildi"
GENERIC = "Geçersiz doğrusal hareket (çoğunlukla yumuşak limit dışı hedef) — reddedildi"


def esc(text: str) -> str:
    return htmllib.escape(text, quote=True)


def rig_facts() -> M.Facts:
    """The facts LinuxCncAdapter builds for the test rig, from its INI."""
    profile = load_profile("test-rig")
    return A._message_facts(profile, A._joint_config(load_machine_config(profile)),
                            load_ini(profile.ini_path))


# ---------------------------------------------------------------------------
# messages.translate: LinuxCNC's texts in the operator's words
# ---------------------------------------------------------------------------


class TestTranslate(unittest.TestCase):

    def setUp(self):
        self.facts = rig_facts()

    def t(self, raw: str, values=()) -> Optional[str]:
        return M.translate(raw, self.facts, values)

    def test_the_rig_facts_come_from_the_ini(self):
        self.assertEqual(dict(self.facts.joint_axes), {0: "X", 1: "Y", 2: "Z"})
        self.assertEqual(self.facts.axis_limits["X"], (3.0, 399.0))
        self.assertEqual(self.facts.axis_limits["Z"], (-96.5, -3.0))
        self.assertEqual(self.facts.joint_limits[1], (3.0, 387.0))
        self.assertEqual({i.label for i in self.facts.inputs},
                         {"X sağ uç", "X sol uç", "Y arka uç", "Y ön uç", "Z üst"})

    def test_limit_trip_names_the_axis_and_the_pressed_switch(self):
        """Plan WP05's one text (messages.limit_trip_text), the same the
        stop that motion's flags show is said with: the switch, which names
        the axis, and the way off."""
        self.assertEqual(
            self.t("joint 0 on limit switch error", ((X_LEFT, True), (X_RIGHT, False))),
            TRIP_X_LEFT)
        self.assertEqual(self.t("joint 2 on limit switch error",
                                (("hm2_7i92.0.gpio.024.in_not", True),)),
                         "Z üst switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın.")

    def test_limit_trip_without_a_reading_names_the_axis_only(self):
        """No input read pressed (none read, or released since): the axis,
        and no recovery to offer."""
        for values in ((), ((X_LEFT, False), (X_RIGHT, None))):
            with self.subTest(values=values):
                self.assertEqual(self.t("joint 0 on limit switch error", values),
                                 "X limit switch'ine çarptı — makine durdu.")

    def test_limit_trip_with_both_ends_pressed_lists_them(self):
        """Both ends at once: no direction is safe, no recovery offered."""
        self.assertEqual(self.t("joint 0 on limit switch error", ((X_LEFT, True), (X_RIGHT, True))),
                         "X sağ uç ve X sol uç birden basılı — makine durdu; kablo ya da "
                         "switch arızası olabilir.")

    def test_a_joint_the_facts_do_not_know_keeps_its_number(self):
        self.assertEqual(self.t("joint 7 on limit switch error"),
                         "Eklem 7 limit switch'ine çarptı — makine durdu.")
        self.assertEqual(M.translate("joint 0 on limit switch error"),
                         "Eklem 0 limit switch'ine çarptı — makine durdu.")

    def test_soft_limit_names_the_axis_and_the_window(self):
        self.assertEqual(self.t(X500[0]), X_TEXT)
        self.assertEqual(self.t(Z0[0]), Z_TEXT)
        self.assertEqual(self.t("Linear move on line 0 would exceed Y's negative limit"),
                         "Y hedefi yumuşak limit dışında (izinli 3…387 mm, makine) — reddedildi")

    def test_the_joint_report_is_named_by_its_axis(self):
        self.assertEqual(self.t(X500[1]), X_TEXT)
        self.assertEqual(self.t(Z0[1]), Z_TEXT)

    def test_a_program_line_is_named_mdi_line_zero_is_not(self):
        self.assertEqual(self.t("Linear move on line 37 would exceed Z's negative limit"),
                         "Satır 37: " + Z_TEXT)
        self.assertNotIn("Satır", self.t(Z0[0]))

    def test_every_move_kind_is_matched(self):
        """inRange is called with "Linear", "Circular", "Probe" and "Rigid
        tap" (command.c:1002, :1070, :1424, :1493)."""
        for kind in ("Linear", "Circular", "Probe", "Rigid tap"):
            with self.subTest(kind=kind):
                self.assertEqual(self.t("{} move on line 0 would exceed X's positive limit"
                                        .format(kind)), X_TEXT)

    def test_without_a_window_the_side_is_named(self):
        self.assertEqual(M.translate("Linear move on line 0 would exceed X's positive limit"),
                         "X hedefi yumuşak limit dışında (üst sınır aşıldı) — reddedildi")
        self.assertEqual(M.translate("Linear move on line 0 would exceed joint 2's negative limit"),
                         "Eklem 2 hedefi yumuşak limit dışında (alt sınır aşıldı) — reddedildi")
        half = self.facts._replace(axis_limits={"X": (3.0, None)})
        self.assertIn("üst sınır aşıldı", M.translate(X500[0], half))

    def test_the_generic_linear_refusal(self):
        self.assertEqual(self.t("invalid params in linear command"), GENERIC)

    def test_jog_onto_a_pressed_switch_names_it_and_the_way_out(self):
        self.assertEqual(self.t("Can't jog joint 0 further past max hard limit."),
                         "X switch'e doğru jog yapılamaz — X sağ uç basılı, yalnız X−")
        self.assertEqual(self.t("Can't jog joint 0 further past min hard limit."),
                         "X switch'e doğru jog yapılamaz — X sol uç basılı, yalnız X+")
        # No switch at Z's bottom on the rig (profile.missing_switches).
        self.assertEqual(self.t("Can't jog joint 2 further past min hard limit."),
                         "Z switch'e doğru jog yapılamaz — Z− ucu basılı, yalnız Z+")

    def test_jog_past_the_jog_window(self):
        self.assertEqual(self.t("Can't jog joint 2 further past max soft limit."),
                         "Z+ yönünde jog sınırına ulaşıldı — yalnız Z−")

    def test_jog_while_homing(self):
        for raw in ("Can't jog any joint while homing.", "Can't jog any joints while homing."):
            with self.subTest(raw=raw):
                self.assertEqual(self.t(raw), "Referans alma sürerken jog yapılamaz")

    def test_file_that_cannot_be_opened(self):
        self.assertEqual(self.t("can't open /home/cnc/nc_files/a.ngc"),
                         "Dosya açılamadı: /home/cnc/nc_files/a.ngc")
        self.assertEqual(self.t("Unable to open file </tmp/b.ngc>"), "Dosya açılamadı: /tmp/b.ngc")

    def test_homing_outside_joint_mode(self):
        self.assertEqual(self.t("must be in joint mode to home"),
                         "Referans için eklem moduna geçilemedi")

    def test_mode_change_while_jogging(self):
        self.assertEqual(self.t("Ignoring task mode change while jogging"),
                         "Jog sürerken mod değiştirilmedi — jog bitince tekrar deneyin")

    def test_an_unknown_text_is_not_translated(self):
        for raw in ("joint 0 following error", "fake-following-error", "",
                    "Linear move on line 0 would exceed Q's positive limit",
                    "invalid params in circular command"):
            with self.subTest(raw=raw):
                self.assertIsNone(self.t(raw))
        self.assertEqual(M.operator_text(" joint 0 following error\n", self.facts),
                         "LinuxCNC: joint 0 following error")

    def test_operator_text_uses_the_translation(self):
        self.assertEqual(M.operator_text(X500[0], self.facts), X_TEXT)


class TestFollowUp(unittest.TestCase):
    """messages.is_follow_up: what adds nothing to the error before it."""

    def test_the_generic_refusal_follows_the_moves_report(self):
        for previous in (X500[0], X500[1],
                         "Linear move on line 3 fails kinematicsInverse",
                         "Linear move on line 3 gave non-finite joint location on joint 1"):
            with self.subTest(previous=previous):
                self.assertTrue(M.is_follow_up(M.LINEAR_INVALID, previous))

    def test_the_joint_report_follows_the_axis_report_of_the_same_line(self):
        self.assertTrue(M.is_follow_up(X500[1], X500[0]))
        self.assertTrue(M.is_follow_up("Linear move on line 0 would exceed Y's positive limit",
                                       X500[0]))  # one move past two axes

    def test_the_generic_refusal_is_linuxcncs_last_word(self):
        self.assertTrue(M.ends_report(M.LINEAR_INVALID + "\n"))
        for raw in (X500[0], X500[1], "joint 0 on limit switch error", "invalid params"):
            with self.subTest(raw=raw):
                self.assertFalse(M.ends_report(raw))

    def test_the_refusals_are_the_known_texts_but_the_limit_trip(self):
        """messages.is_refusal: LinuxCNC stays ON after these, so none of
        them explains a stop (LinuxCncAdapter._note_task_state)."""
        for raw in (X500[0], X500[1], M.LINEAR_INVALID + "\n",
                    "Circular move on line 3 would exceed Z's negative limit",
                    "Can't jog joint 0 further past max hard limit.",
                    "Can't jog joint 2 further past min soft limit.",
                    "Can't jog any joint while homing.", "can't open /tmp/a.ngc",
                    "Unable to open file </tmp/a.ngc>", M.HOME_NEEDS_JOINT_MODE,
                    M.MODE_CHANGE_WHILE_JOGGING):
            with self.subTest(raw=raw):
                self.assertTrue(M.is_refusal(raw))
        # What disables motion (control.c:781-859), and anything unknown.
        for raw in ("joint 0 on limit switch error", "joint 0 following error",
                    "joint 1 amplifier fault", "motion stopped by enable input",
                    "kinematicsInverse failed",
                    "hm2/hm2_7i92.0: Watchdog has bit! (set the .has-bit pin to False to resume)",
                    "fake", ""):
            with self.subTest(raw=raw):
                self.assertFalse(M.is_refusal(raw))

    def test_nothing_else_is_a_follow_up(self):
        for raw, previous in (
            (M.LINEAR_INVALID, "joint 0 on limit switch error"),
            (M.LINEAR_INVALID, M.LINEAR_INVALID),
            ("Linear move on line 5 would exceed Z's positive limit", X500[0]),  # another line
            ("joint 0 on limit switch error", X500[0]),
            (X500[0], "fake"),
        ):
            with self.subTest(raw=raw, previous=previous):
                self.assertFalse(M.is_follow_up(raw, previous))


@NEEDS_REFERENCE
class TestTheTextsAreLinuxCncs(unittest.TestCase):
    """Each pattern against the format string LinuxCNC prints it from, at
    the line messages.py cites (reference/ is at its pinned commit)."""

    #: (path under reference/linuxcnc/src, line, format string, arguments)
    SOURCES = (
        ("emc/motion/control.c", 821, '"joint %d on limit switch error"', (0,)),
        ("emc/motion/command.c", 216, '"%s move on line %d would exceed %c\'s %s limit"',
         ("Linear", 0, "Z", "positive")),
        ("emc/motion/command.c", 221, '"%s move on line %d would exceed %c\'s %s limit"',
         ("Linear", 0, "Z", "positive")),
        ("emc/motion/command.c", 261, '"%s move on line %d would exceed joint %d\'s positive limit"',
         ("Linear", 0, 2)),
        ("emc/motion/command.c", 267, '"%s move on line %d would exceed joint %d\'s negative limit"',
         ("Linear", 12, 2)),
        ("emc/motion/command.c", 1003, '"invalid params in linear command"', ()),
        ("emc/motion/command.c", 130, '"Can\'t jog joint %d further past max hard limit."', (0,)),
        ("emc/motion/command.c", 135, '"Can\'t jog joint %d further past min hard limit."', (0,)),
        ("emc/motion/command.c", 141, '"Can\'t jog joint %d further past max soft limit."', (2,)),
        ("emc/motion/command.c", 146, '"Can\'t jog joint %d further past min soft limit."', (2,)),
        ("emc/motion/command.c", 786, '"Can\'t jog any joints while homing."', ()),
        ("emc/motion/command.c", 854, '"Can\'t jog any joint while homing."', ()),
        ("emc/motion/command.c", 933, '"Can\'t jog any joints while homing."', ()),
        ("emc/task/emctaskmain.cc", 2197, '"can\'t open %s"', ("/tmp/a.ngc",)),
        ("emc/rs274ngc/rs274ngc_return.hh", 152, '"Unable to open file <%s>"', ("/tmp/a.ngc",)),
        ("emc/motion/command.c", 1376, '"must be in joint mode to home"', ()),
        ("emc/task/emctask.cc", 270, '"Ignoring task mode change while jogging"', ()),
    )

    def line(self, rel: str, number: int) -> str:
        with open(os.path.join(LINUXCNC, "src", rel), "r", encoding="utf-8",
                  errors="replace") as fh:
            return fh.read().splitlines()[number - 1]

    def test_each_format_is_at_its_cited_line_and_translated(self):
        for rel, number, fmt, args in self.SOURCES:
            with self.subTest(source="{}:{}".format(rel, number)):
                self.assertIn(fmt, self.line(rel, number))
                printed = fmt[1:-1] % args
                self.assertIsNotNone(M.translate(printed, rig_facts()), printed)

    def test_the_follow_ups_are_linuxcncs_order(self):
        """inRange's reports come before the generic refusal (command.c:
        1002-1003), and the axis loop before the joint loop (:214, :244)."""
        lines = self.line("emc/motion/command.c", 1002) + self.line("emc/motion/command.c", 1003)
        self.assertIn("!inRange(", lines)
        self.assertIn('"invalid params in linear command"', lines)
        self.assertIn("axis_num < EMCMOT_MAX_AXIS", self.line("emc/motion/command.c", 214))
        self.assertIn("joint_num < ALL_JOINTS", self.line("emc/motion/command.c", 244))

    def test_task_forwards_one_motion_error_per_update(self):
        """taskintf.cc:2024-2028 reads one emcmot error per emcMotionUpdate,
        which task's loop calls once a cycle (emctaskmain.cc:3411)."""
        self.assertIn("usrmotReadEmcmotError(errorString)", self.line("emc/task/taskintf.cc", 2024))
        self.assertIn('emcOperatorError(0, "%s", errorString)', self.line("emc/task/taskintf.cc", 2028))
        self.assertIn("emcMotionUpdate(&emcStatus->motion)", self.line("emc/task/emctaskmain.cc", 3411))

    def test_a_refusal_leaves_motion_enabled_a_fault_does_not(self):
        """messages.is_refusal's citations: the refused linear move and the
        refused jog drop the command without clearing enabling; the limit
        trip clears it (check_for_faults), and that is what disables
        motion."""
        refused_move = "".join(self.line("emc/motion/command.c", n) for n in range(1002, 1008))
        self.assertIn("tpAbort(", refused_move)
        self.assertIn("SET_MOTION_ERROR_FLAG(1)", refused_move)
        refused_jog = "".join(self.line("emc/motion/command.c", n) for n in range(866, 872))
        self.assertIn("!joint_jog_ok(", refused_jog)
        self.assertIn("SET_JOINT_ERROR_FLAG(joint, 1)", refused_jog)
        for text in (refused_move, refused_jog):
            self.assertNotIn("enabling", text)
        self.assertIn("static void check_for_faults(void)", self.line("emc/motion/control.c", 781))
        trip = "".join(self.line("emc/motion/control.c", n) for n in range(816, 827))
        self.assertIn("joint %d on limit switch error", trip)
        self.assertIn("emcmotInternal->enabling = 0", trip)
        self.assertIn("!emcmotInternal->enabling && GET_MOTION_ENABLE_FLAG()",
                      self.line("emc/motion/control.c", 868))
        self.assertIn("!emcStatus->motion.traj.enabled", self.line("emc/task/emctask.cc", 425))

    def test_a_watchdog_bite_reaches_the_error_channel(self):
        """_note_task_state's reason to say LINK_LOST over it: hostmot2
        reports the bite as an RTAPI error, and motion's message handler
        queues every RTAPI error as a motion error, which task forwards."""
        self.assertIn('HM2_ERR("Watchdog has bit! (set the .has-bit pin to False to resume)\\n")',
                      self.line("hal/drivers/mesa-hostmot2/watchdog.c", 50))
        self.assertIn("if(level == RTAPI_MSG_ERR) emcmotErrorPutfv(emcmotError, fmt, apc);",
                      self.line("emc/motion/motion.c", 211))

    def test_linuxcnc_has_no_turkish_catalogue(self):
        po = os.listdir(os.path.join(LINUXCNC, "src", "po"))
        self.assertIn("de.po", po)    # the listing is the catalogue directory
        self.assertNotIn("tr.po", po)

    def test_the_soft_limit_windows_are_read_where_linuxcnc_reads_them(self):
        """[AXIS_<L>] and [JOINT_<n>] MIN_LIMIT / MAX_LIMIT (_message_facts)."""
        self.assertIn('"MIN_LIMIT", axisString', self.line("emc/ini/iniaxis.cc", 84))
        self.assertIn('"MAX_LIMIT", axisString', self.line("emc/ini/iniaxis.cc", 95))
        self.assertIn('"MIN_LIMIT", jointString', self.line("emc/ini/inijoint.cc", 132))
        self.assertIn('"MAX_LIMIT", jointString', self.line("emc/ini/inijoint.cc", 140))


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake: the drain, the error box, the MDI result
# ---------------------------------------------------------------------------


class _AdapterCase(T.FakeCase):

    def make(self, *args, **kw):
        if kw.get("hal_pins") is None:
            kw["hal_pins"] = dict(T.ALL_LINK_PINS, **RIG_INPUT_PINS)
        return super().make(*args, **kw)

    def errors(self, *texts: str) -> None:
        """LinuxCNC reports `texts`, in this order, on the error channel."""
        for text in texts:
            self.machine.operator_error(text)

    def raw_log(self, snap) -> List[str]:
        return [a.text for a in snap.alarms if a.source == "linuxcnc"]


class TestTheDrainKeepsTheMostSpecificError(_AdapterCase):

    def test_of_two_errors_in_one_drain_the_first_is_the_message(self):
        adapter = self.on(homed=True)
        self.errors(*Z0)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, Z_TEXT)
        self.assertIs(snap.message_severity, Severity.ERROR)
        self.assertEqual((snap.last_error.text, snap.last_error.raw, snap.last_error.source),
                         (Z_TEXT, Z0[0], "linuxcnc"))
        # Every one stays in the log, as LinuxCNC worded it, newest first.
        self.assertEqual(self.raw_log(snap)[:3], list(reversed(Z0)))
        self.assertTrue(all(a.severity is Severity.ERROR for a in snap.alarms[:3]))

    def test_of_two_unrelated_errors_in_one_drain_the_first_takes_the_box(self):
        adapter = self.on(homed=False)
        self.errors("joint 0 on limit switch error", "joint 1 on limit switch error")
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.raw, "joint 0 on limit switch error")
        self.assertEqual(snap.message, "X limit switch'ine çarptı — makine durdu.")
        self.assertEqual(self.raw_log(snap)[:2], ["joint 1 on limit switch error",
                                                  "joint 0 on limit switch error"])

    def test_the_pair_split_across_two_drains_keeps_the_specific_one(self):
        """As in sim/: the POST's snapshot took the first report, the next
        poll the joint report and the generic refusal."""
        adapter = self.on(homed=True)
        self.errors(X500[0])
        first = adapter.snapshot().last_error
        self.assertEqual(first.text, X_TEXT)
        self.clock.now += 0.8
        self.errors(*X500[1:])
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error, first)       # the same box, the same id
        self.assertEqual(snap.message, X_TEXT)
        self.assertEqual(self.raw_log(snap)[:3], list(reversed(X500)))

    def test_alone_the_generic_refusal_is_shown_in_turkish(self):
        adapter = self.on(homed=True)
        self.errors(M.LINEAR_INVALID)
        self.assertEqual(adapter.snapshot().last_error.text, GENERIC)

    def test_after_the_window_a_report_is_news_again(self):
        adapter = self.on(homed=True)
        self.errors(X500[0])
        adapter.snapshot()
        self.clock.now += A.FOLLOW_UP_WINDOW_S + 0.1
        self.errors(M.LINEAR_INVALID)
        self.assertEqual(adapter.snapshot().last_error.text, GENERIC)

    def test_a_follow_up_does_not_cover_a_later_error_of_the_ui(self):
        """The software estop took the box after the report; the late
        generic refusal goes to the log only."""
        adapter = self.on(homed=True)
        self.errors(X500[0])
        adapter.snapshot()
        adapter.software_estop()
        self.errors(*X500[1:])
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, A.ESTOP_SENT)
        self.assertIn(M.LINEAR_INVALID, self.raw_log(snap))

    def test_a_limit_trip_names_the_switch_that_reads_pressed(self):
        adapter = self.on(homed=False)
        self.hal.pins[X_LEFT] = True
        self.errors("joint 0 on limit switch error")
        self.assertEqual(adapter.snapshot().last_error.text, TRIP_X_LEFT)

    def test_without_the_hal_module_a_limit_trip_names_the_axis(self):
        adapter = self.on(homed=False, hal_available=False)
        self.errors("joint 0 on limit switch error")
        self.assertEqual(adapter.snapshot().last_error.text,
                         "X limit switch'ine çarptı — makine durdu.")

    def test_the_stop_it_causes_does_not_cover_it_a_snapshot_later(self):
        """The trip's error can be drained a snapshot before the status
        shows the machine off: the stop is then the error's consequence -
        logged, not boxed over it."""
        adapter = self.on(homed=False)
        adapter.snapshot()                        # sees ON
        self.hal.pins[X_LEFT] = True
        self.errors("joint 0 on limit switch error")
        boxed = adapter.snapshot().last_error     # still ON in this stat
        self.machine.traj_enabled = False         # motion disabled by the trip
        self.clock.now += 1.0
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error, boxed)
        self.assertTrue(any("komut bu ekrandan gelmedi" in a.text for a in snap.alarms))
        self.assertNotIn("komut bu ekrandan gelmedi", snap.message)

    def test_a_stop_long_after_an_error_is_its_own(self):
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.errors("fake-following-error")
        adapter.snapshot()
        self.clock.now += A.FOLLOW_UP_WINDOW_S + 0.1
        self.machine.traj_enabled = False
        snap = adapter.snapshot()
        self.assertTrue(snap.last_error.text.startswith("Makine kapatıldı — komut bu ekrandan gelmedi"))

    def test_a_refusal_just_before_does_not_hide_a_stop(self):
        """Review of WP04: a refusal leaves LinuxCNC ON (messages.is_refusal),
        so it never explains a stop - a soft-limit refusal 1 s before the
        machine went off elsewhere used to keep the box and the line, and
        the stop went to the log alone."""
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.errors(Z0[0])
        adapter.snapshot()
        self.clock.now += 1.0                     # well inside the window
        self.machine.traj_enabled = False
        snap = adapter.snapshot()
        self.assertTrue(snap.last_error.text.startswith("Makine kapatıldı — komut bu ekrandan gelmedi"),
                        snap.last_error)
        self.assertEqual(snap.message, snap.last_error.text)

    def test_a_refusal_in_the_same_drain_does_not_hide_a_stop(self):
        adapter = self.on(homed=True)
        adapter.snapshot()
        self.errors("Can't jog joint 0 further past max soft limit.")
        self.machine.fault_in = True              # the estop chain opened elsewhere
        snap = adapter.snapshot()
        self.assertTrue(snap.last_error.text.startswith(
            "Makine acil stopa geçti — komut bu ekrandan gelmedi"), snap.last_error)
        self.assertIn("Can't jog joint 0 further past max soft limit.", self.raw_log(snap))

    def test_the_link_loss_is_said_over_what_linuxcnc_forwards_around_it(self):
        """Review of WP04: a watchdog bite brings hostmot2's own text through
        motion's error queue; the box showed that raw text ("set the
        .has-bit pin to False") and LINK_LOST, which names what to do, was
        in the log alone - in the same drain, or a drain earlier."""
        bite = "hm2/hm2_7i92.0: Watchdog has bit! (set the .has-bit pin to False to resume)"
        for when in ("same drain", "a drain earlier"):
            with self.subTest(when=when):
                adapter = self.on(homed=True)
                adapter.snapshot()
                self.errors(bite)
                if when == "a drain earlier":
                    adapter.snapshot()            # still ON in this stat
                    self.clock.now += 1.0
                self.bite()
                snap = adapter.snapshot()
                self.assertEqual(snap.last_error.text, "{}. {}".format(A.LINK_LOST, A.LINK_ACTION))
                self.assertEqual(snap.message, snap.last_error.text)
                self.assertIn(bite, self.raw_log(snap))


class TestTheErrorBoxIsSticky(_AdapterCase):

    def test_a_later_message_does_not_clear_it_and_tamam_does(self):
        adapter = self.on(homed=True)
        self.errors(Z0[0])
        box = adapter.snapshot().last_error
        adapter.jog("X", 1)
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Jog X+1 mm.")      # plan WP06: no "gönderildi (eksen)"
        self.assertIs(snap.message_severity, Severity.INFO)
        self.assertEqual(snap.last_error, box)
        adapter.ack_error(str(box.id))
        self.assertIsNone(adapter.snapshot().last_error)

    def test_tamam_for_another_error_clears_nothing(self):
        """«Tamam» carries the id of the error on the screen; one that came
        after the screen was drawn stays until it is seen."""
        adapter = self.on(homed=True)
        self.errors(Z0[0])
        seen = adapter.snapshot().last_error
        adapter.software_estop()
        newer = adapter.snapshot().last_error
        self.assertGreater(newer.id, seen.id)
        for stale in (str(seen.id), "", "x", " "):
            with self.subTest(value=stale):
                adapter.ack_error(stale)
                self.assertEqual(adapter.snapshot().last_error, newer)
        adapter.ack_error(" {} ".format(newer.id))
        self.assertIsNone(adapter.snapshot().last_error)

    def test_the_estop_stays_in_the_box_over_the_action_it_overtook(self):
        """docs/11 Known gap until plan WP04: an action finishing after a
        concurrent software estop wrote its message last. home_all waits
        in a mode switch (wait_gate) while the estop goes out."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_MDI"]           # home_all will switch and wait
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        gate = threading.Event()
        self.machine.wait_gate = gate
        # Confirmed: a homed machine is asked about first (plan WP07).
        worker = threading.Thread(target=lambda: adapter.home_all(confirmed=True), daemon=True)
        worker.start()
        self.addCleanup(worker.join, F.GATE_LIMIT_S)
        self.addCleanup(gate.set)
        self.assertTrue(self.machine.wait_entered.wait(F.GATE_LIMIT_S))
        adapter.software_estop()
        gate.set()
        worker.join(F.GATE_LIMIT_S)
        self.assertFalse(worker.is_alive())
        snap = adapter.snapshot()
        self.assertNotEqual(snap.message, A.ESTOP_SENT)  # home_all spoke last...
        self.assertIs(snap.message_severity, Severity.WARN)
        self.assertEqual(snap.last_error.text, A.ESTOP_SENT)   # ...the box kept the estop
        self.assertNotIn(("home", (-1,)), self.machine.log)

    def test_errors_of_the_ui_are_in_the_box_without_raw_text(self):
        adapter = self.on(homed=True)
        adapter.software_estop()
        box = adapter.snapshot().last_error
        self.assertEqual((box.text, box.raw, box.source), (A.ESTOP_SENT, "", "ui"))
        self.assertRegex(box.time, r"^\d\d:\d\d:\d\d$")

    def test_a_lost_status_is_in_the_box_and_the_refusal_is_a_warning(self):
        adapter = self.on(homed=True)
        self.machine.channels_valid = False
        adapter.jog("X", 1)
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, "LinuxCNC'ye bağlantı yok — makine durumu okunamıyor.")
        self.machine.channels_valid = True
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "LinuxCNC durumu yeniden okunuyor.")
        self.assertEqual(snap.last_error.text, "LinuxCNC'ye bağlantı yok — makine durumu okunamıyor.")
        self.machine.channels_valid = False
        adapter.jog("X", 1)
        self.assertEqual((adapter._message, adapter._message_severity),
                         (A.NO_LINUXCNC_REFUSAL, Severity.WARN))

    def test_refusals_are_warnings_and_news_is_info(self):
        adapter = self.on(homed=True)
        adapter.set_feed_override(150)          # clamped to the INI's ceiling
        self.assertIs(adapter.snapshot().message_severity, Severity.WARN)
        adapter.set_feed_override(50)
        self.assertIs(adapter.snapshot().message_severity, Severity.INFO)
        self.assertIsNone(adapter.snapshot().last_error)


class TestMdiResult(_AdapterCase):

    def test_a_line_motion_refuses_is_rejected_in_turkish(self):
        """Plan WP04's case: G0 X500 is sent, then LinuxCNC reports it."""
        adapter = self.on(homed=True)
        entry = adapter.send_mdi("G0 X500")
        self.assertEqual((entry.accepted, entry.status, entry.note), (True, "sent", A.MDI_SENT_NOTE))
        self.errors(*X500)
        snap = adapter.snapshot()
        last = snap.mdi_history[-1]
        self.assertEqual((last.command, last.status), ("G0 X500", "rejected"))
        self.assertEqual(last.note, X_TEXT)
        self.assertIn("izinli 3…399", last.note)
        self.assertEqual(snap.last_error.text, X_TEXT)

    def test_a_line_that_completes_is_done(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X50")
        snap = adapter.snapshot()
        self.assertEqual((snap.mdi_history[-1].status, snap.mdi_history[-1].note),
                         ("done", A.MDI_DONE_NOTE))
        self.assertEqual(snap.message, "MDI: G0 X50 — Tamamlandı.")
        self.assertIsNone(snap.last_error)

    def test_a_running_line_stays_sent_until_the_interpreter_is_idle(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X200")
        self.run_mdi_program()                     # MDI mode, interpreter busy
        for _ in range(3):
            self.clock.now += 1.0
            self.assertEqual(adapter.snapshot().mdi_history[-1].status, "sent")
        self.machine.interp_state = C["INTERP_IDLE"]
        self.assertEqual(adapter.snapshot().mdi_history[-1].status, "done")

    def test_an_error_while_it_runs_rejects_it_after_the_window(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X200")
        self.run_mdi_program()
        self.clock.now += A.MDI_RESULT_WINDOW_S + 5.0
        self.hal.pins[X_RIGHT] = True
        self.errors("joint 0 on limit switch error")
        last = adapter.snapshot().mdi_history[-1]
        self.assertEqual(last.status, "rejected")
        self.assertIn("X sağ uç switch'ine çarptı", last.note)

    def test_out_of_mdi_mode_an_error_is_the_lines_only_within_the_window(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X1")
        self.machine.task_mode = C["MODE_MANUAL"]   # something switched it since
        self.errors("fake-following-error")
        self.assertEqual(adapter.snapshot().mdi_history[-1].status, "rejected")
        adapter.send_mdi("G0 X2")
        self.machine.task_mode = C["MODE_MANUAL"]
        self.clock.now += A.MDI_RESULT_WINDOW_S + 0.1
        self.errors("fake-following-error")
        self.assertEqual(adapter.snapshot().mdi_history[-1].status, "done")

    def test_a_line_cut_by_durdur_or_the_estop_is_not_done(self):
        for name, stop, note in (
            ("durdur", lambda a: (a.stop_motion(), a.stop_followup()), A.MDI_CUT_BY_STOP),
            ("estop", lambda a: a.software_estop(), A.MDI_CUT_BY_ESTOP),
        ):
            with self.subTest(stop=name):
                adapter = self.on(homed=True)
                adapter.send_mdi("G0 X200")
                self.run_mdi_program()
                self.assertEqual(adapter.snapshot().mdi_history[-1].status, "sent")
                stop(adapter)
                last = adapter.snapshot().mdi_history[-1]
                self.assertEqual((last.status, last.note), ("rejected", note))

    def test_a_line_pending_when_the_machine_went_off_is_not_done(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X200")
        self.run_mdi_program()
        adapter.snapshot()
        self.machine.traj_enabled = False          # switched off elsewhere
        self.machine.interp_state = C["INTERP_IDLE"]
        last = adapter.snapshot().mdi_history[-1]
        self.assertEqual((last.status, last.note), ("rejected", A.MDI_CUT_MACHINE_OFF))

    def test_a_newer_line_supersedes_one_still_running(self):
        """LinuxCNC queues MDI lines and names no line for them: only the
        newest is followed, and the older one says so."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X200")
        self.run_mdi_program()
        adapter.send_mdi("G0 Y100")
        self.machine.interp_state = C["INTERP_IDLE"]
        first, second = adapter.snapshot().mdi_history[-2:]
        self.assertEqual((first.status, first.note), ("sent", A.MDI_UNTRACKED_NOTE))
        self.assertEqual(second.status, "done")

    def test_errors_queued_before_a_send_are_not_the_new_lines(self):
        """The line's own errors arrive after the POST's snapshot; a new
        line sent before the next poll must not take them."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X500")
        self.run_mdi_program()
        adapter.snapshot()                        # busy, no error yet
        self.machine.interp_state = C["INTERP_IDLE"]
        self.errors(*X500)                         # X500's errors, undrained
        adapter.send_mdi("G0 X50")
        self.assertEqual(adapter._mdi[-2].status, "rejected")
        snap = adapter.snapshot()
        self.assertEqual([(e.command, e.status) for e in snap.mdi_history[-2:]],
                         [("G0 X500", "rejected"), ("G0 X50", "done")])
        self.assertEqual(snap.last_error.text, X_TEXT)

    def test_the_next_lines_report_is_not_taken_for_a_follow_up(self):
        """MDI moves all carry line 0: Z0's report must not pass for a
        second report of X500's."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X500")
        self.errors(*X500)
        adapter.snapshot()
        adapter.send_mdi("G0 Z0")
        self.errors(*Z0)
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, Z_TEXT)
        self.assertEqual([(e.command, e.status, e.note) for e in snap.mdi_history[-2:]],
                         [("G0 X500", "rejected", X_TEXT), ("G0 Z0", "rejected", Z_TEXT)])

    def test_a_late_follow_up_of_the_last_refusal_is_not_the_next_lines(self):
        """Seen in sim/ (make sim-e2e, 2026-11-13): G0 Z0's joint report and
        its generic refusal came after G0 X50 had been sent - X50 ran, but
        was marked rejected with Z0's text. A follow-up is never news."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 Z0")
        self.errors(Z0[0])
        snap = adapter.snapshot()                  # the POST's: the first report alone
        self.assertEqual(snap.mdi_history[-1].status, "rejected")
        box = snap.last_error
        adapter.send_mdi("G0 X50")                 # nothing more queued yet
        self.errors(*Z0[1:])                       # ...then the rest of Z0's reports
        self.run_mdi_program()                     # X50 runs
        snap = adapter.snapshot()
        self.assertEqual(snap.mdi_history[-1].status, "sent")
        self.assertEqual(snap.last_error, box)
        self.machine.interp_state = C["INTERP_IDLE"]
        snap = adapter.snapshot()
        self.assertEqual([(e.command, e.status) for e in snap.mdi_history[-2:]],
                         [("G0 Z0", "rejected"), ("G0 X50", "done")])
        self.assertEqual(snap.last_error, box)
        self.assertEqual(self.raw_log(snap)[:3], list(reversed(Z0)))

    def test_after_the_last_word_the_same_report_is_a_new_refusal(self):
        """MDI moves all carry line 0: once X500's refusal ended with the
        generic text, the next X500's identical report is news - the box
        takes it (a new id) and the second line is rejected."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X500")
        self.errors(*X500)
        first = adapter.snapshot().last_error
        adapter.send_mdi("G0 X600")
        self.errors(*X500)
        snap = adapter.snapshot()
        self.assertGreater(snap.last_error.id, first.id)
        self.assertEqual(snap.last_error.text, X_TEXT)
        self.assertEqual(snap.mdi_history[-1].status, "rejected")

    def test_without_a_last_word_the_window_ends_the_refusal(self):
        """A circular move's refusal has no generic last line (command.c:
        1070-1074): its reports stay follow-ups only within the window."""
        adapter = self.on(homed=True)
        circ = "Circular move on line 0 would exceed X's positive limit"
        self.errors(circ)
        first = adapter.snapshot().last_error
        self.errors(circ.replace("X's", "joint 0's"))
        self.assertEqual(adapter.snapshot().last_error, first)
        self.clock.now += A.FOLLOW_UP_WINDOW_S + 0.1
        self.errors(circ)
        self.assertGreater(adapter.snapshot().last_error.id, first.id)

    def test_a_late_error_within_the_window_still_rejects_a_done_line(self):
        """Task skips forwarding motion's error in a cycle whose status read
        fails (taskintf.cc:2007-2009): the line can look idle first."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 Z0")
        self.assertEqual(adapter.snapshot().mdi_history[-1].status, "done")
        self.clock.now += 0.5
        self.errors(*Z0)
        last = adapter.snapshot().mdi_history[-1]
        self.assertEqual((last.status, last.note), ("rejected", Z_TEXT))

    def test_not_after_the_window_nor_once_task_left_mdi_mode(self):
        for name, setup in (
            ("window", lambda: setattr(self.clock, "now", self.clock.now + A.MDI_RESULT_WINDOW_S + 0.1)),
            ("mode", lambda: setattr(self.machine, "task_mode", C["MODE_MANUAL"])),
        ):
            with self.subTest(case=name):
                adapter = self.on(homed=True)
                adapter.send_mdi("G21")
                self.assertEqual(adapter.snapshot().mdi_history[-1].status, "done")
                setup()
                self.errors("joint 0 on limit switch error")
                self.assertEqual(adapter.snapshot().mdi_history[-1].status, "done")

    def test_an_error_drained_inside_another_action_is_still_the_lines(self):
        """Every drain follows a poll and resolves the line: here
        load_program drains after its failed open (_drain_errors)."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X500")
        self.errors(*X500)                         # not drained by a snapshot yet
        adapter.load_program("/nonexistent/a.ngc")
        self.assertEqual(adapter._message, "Program açılamadı: /nonexistent/a.ngc")
        self.assertEqual((adapter._mdi[-1].status, adapter._mdi[-1].note), ("rejected", X_TEXT))
        self.assertIn("can't open /nonexistent/a.ngc", self.raw_log(adapter.snapshot()))

    def test_an_action_leaving_mdi_mode_settles_the_line_first(self):
        """Review of WP04: G0 X10 ran, and a jog pressed before the next
        poll was refused by LinuxCNC; the jog's error came within the 2 s
        window and the line read rejected with the jog's reason. Leaving
        MDI mode (_ensure_mode) now settles the line from what is known
        then: done here, and the jog's error is the box's alone."""
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X10")
        self.run_mdi_program()
        adapter.snapshot()                         # the POST's: still running
        self.machine.interp_state = C["INTERP_IDLE"]   # it ran before the next poll
        self.clock.now += 0.5
        adapter.jog("X", 1)                        # switches to MANUAL, jogs
        jog_refusal = "Can't jog joint 0 further past max hard limit."
        self.errors(jog_refusal)
        snap = adapter.snapshot()
        self.assertEqual((snap.mdi_history[-1].status, snap.mdi_history[-1].note),
                         ("done", A.MDI_DONE_NOTE))
        self.assertEqual(snap.last_error.raw, jog_refusal)

    def test_the_lines_own_error_queued_before_the_switch_is_still_its(self):
        adapter = self.on(homed=True)
        adapter.send_mdi("G0 X500")
        self.run_mdi_program()
        adapter.snapshot()
        self.machine.interp_state = C["INTERP_IDLE"]
        self.errors(*X500)                         # queued, not drained yet
        adapter.jog("X", 1)
        self.assertEqual((adapter._mdi[-1].status, adapter._mdi[-1].note), ("rejected", X_TEXT))
        self.assertEqual(len(self.jogs()), 1)      # and the jog still went out

    def test_a_refused_line_is_refused_and_follows_nothing(self):
        adapter = self.on(homed=False)
        entry = adapter.send_mdi("G0 X10")
        self.assertEqual((entry.accepted, entry.status), (False, "refused"))
        self.assertIs(adapter._message_severity, Severity.WARN)
        self.assertIsNone(adapter._mdi_pending)


class TestAckIsLocal(_AdapterCase):
    """Plan WP04: ack_error changes the UI's own state only, so observer
    mode allows it - and nothing reaches NML (the safety rule)."""

    def test_in_observer_mode_tamam_sends_and_reads_nothing(self):
        cm = F.installed(hal_state=F.FakeHal(dict(T.ALL_LINK_PINS)))
        _, _, machine, _ = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        profile = load_profile("test-rig")
        adapter = A.LinuxCncAdapter(profile, proc_root="/nonexistent/proc", observe=True)
        machine.power_on(homed=True)
        sess = app.Session(adapter, profile, load_machine_config(profile), observe=True)
        sess.act("software_estop", "")               # an observer's one command
        box = sess.view("kontrol")["status"]["last_error"]
        self.assertEqual(box["text"], A.ESTOP_SENT)
        log, polls = list(machine.log), machine.stat_polls
        sess.act("ack_error", str(box["id"]))
        self.assertEqual(machine.log, log)             # no NML command
        self.assertEqual(machine.stat_polls, polls)    # no status read either
        self.assertIsNone(adapter._error)              # no error channel in this mode
        self.assertIsNone(sess.view("kontrol")["status"]["last_error"])

    def test_the_session_hands_it_on_before_the_observer_drop(self):
        class Recorder(object):
            def __init__(self):
                self.calls = []

            def ack_error(self, error_id):
                self.calls.append(("ack_error", error_id))

            def software_estop(self):
                self.calls.append(("software_estop",))

        profile = load_profile("test-rig")
        for observe in (True, False):
            with self.subTest(observe=observe):
                rec = Recorder()
                sess = app.Session(rec, profile, load_machine_config(profile), observe=observe)
                sess.act("ack_error", "7")
                self.assertEqual(rec.calls, [("ack_error", "7")])
                self.assertFalse(sess._lock.locked())


# ---------------------------------------------------------------------------
# MockAdapter: the same band, the same rules
# ---------------------------------------------------------------------------


class TestMock(unittest.TestCase):

    def setUp(self):
        self.adapter = MockAdapter(load_profile("test-rig"))

    def test_the_estop_goes_to_the_box_and_stays_until_tamam(self):
        a = self.adapter
        a.software_estop()
        box = a.snapshot().last_error
        self.assertEqual((box.text, box.source), (A.MOCK_ESTOP, "ui"))
        a.software_estop_reset()
        snap = a.snapshot()
        self.assertTrue(snap.message.startswith("Yazılım acil stop sıfırlandı."))
        self.assertIs(snap.message_severity, Severity.INFO)
        self.assertEqual(snap.last_error, box)
        a.ack_error(str(box.id + 1))
        self.assertEqual(a.snapshot().last_error, box)
        a.ack_error(str(box.id))
        self.assertIsNone(a.snapshot().last_error)

    def test_an_accepted_line_is_done_and_a_refused_one_refused(self):
        a = self.adapter
        refused = a.send_mdi("G0 X10")
        self.assertEqual((refused.status, a.snapshot().message_severity), ("refused", Severity.WARN))
        a.software_estop_reset()
        a.home_all()
        done = a.send_mdi("G0 X10")
        self.assertEqual((done.accepted, done.status, done.note), (True, "done", "Kabul edildi."))
        self.assertIs(a.snapshot().message_severity, Severity.INFO)

    def test_refusals_are_warnings(self):
        a = self.adapter
        for act in (a.home_all, lambda: a.jog("X", 1), a.program_start):
            with self.subTest(action=act):
                act()
                self.assertIs(a.snapshot().message_severity, Severity.WARN)

    def test_the_startup_note_is_a_warning(self):
        self.adapter.note_startup("09:00 — test")
        self.assertIs(self.adapter.snapshot().message_severity, Severity.WARN)


# ---------------------------------------------------------------------------
# View model and markup
# ---------------------------------------------------------------------------


def _vm(state, screen="kontrol", observe=False, profile_key="test-rig") -> Dict:
    profile = load_profile(profile_key)
    return VM.build(profile, load_machine_config(profile), state, screen, observe)


class TestMessageBandMarkup(unittest.TestCase):

    def estopped(self):
        adapter = MockAdapter(load_profile("test-rig"))
        adapter.software_estop()
        return adapter.snapshot()

    def test_the_view_model_carries_the_band(self):
        state = self.estopped()
        status = _vm(state)["status"]
        box = state.last_error
        self.assertEqual(status["message_severity"], "error")
        self.assertEqual(status["last_error"], {"id": box.id, "time": box.time, "text": box.text,
                                                "raw": "", "source": "ui"})
        fresh = MockAdapter(load_profile("test-rig")).snapshot()
        self.assertIsNone(_vm(fresh)["status"]["last_error"])
        self.assertEqual(_vm(fresh)["status"]["message_severity"], "info")

    def test_the_band_is_on_every_screen_and_the_footer_is_gone(self):
        for key in sorted(PROFILES):
            for screen in ALL_SCREENS:
                with self.subTest(profile=key, screen=screen):
                    body = R.render_body(_vm(MockAdapter(load_profile(key)).snapshot(), screen,
                                             profile_key=key))
                    main = body[body.index("<main "):body.index("</main>")]
                    self.assertEqual(main.count('<div class="msgband"'), 1)
                    self.assertLess(main.index('<div class="top-stick">'),
                                    main.index('<div class="msgband"'))
                    self.assertNotIn("<footer", body)
                    self.assertNotIn('class="msg"', body)

    def test_the_error_box_has_the_time_the_text_and_tamam_with_its_id(self):
        state = self.estopped()
        body = R.render_body(_vm(state))
        box = state.last_error
        self.assertIn('<div class="errbox" data-error-id="{}">'.format(box.id), body)
        self.assertIn('<span class="errbox-time">{}</span>'.format(box.time), body)
        self.assertIn('<div class="errbox-text">{}</div>'.format(esc(A.MOCK_ESTOP)), body)
        self.assertIn('data-action="ack_error" data-value="{}">Tamam</button>'.format(box.id), body)
        self.assertNotIn("errbox-raw", body)     # an error of the UI's own

    def test_no_box_without_an_error(self):
        body = R.render_body(_vm(MockAdapter(load_profile("test-rig")).snapshot()))
        self.assertNotIn('class="errbox"', body)
        self.assertNotIn("ack_error", body)
        self.assertIn('<div class="msg-line" data-severity="info">{}</div>'.format(
            esc("Konfigürasyon yüklendi. Referans alınmadı.")), body)

    def test_the_line_does_not_repeat_the_box(self):
        state = self.estopped()
        self.assertIn('<div class="msg-line" data-severity="error"></div>', R.render_body(_vm(state)))
        later = state._replace(message="Jog X+1 mm", message_severity=Severity.INFO)
        body = R.render_body(_vm(later))
        self.assertIn('<div class="msg-line" data-severity="info">Jog X+1 mm</div>', body)
        self.assertIn('class="errbox"', body)

    def test_linuxcncs_own_text_under_a_translation_only(self):
        base = MockAdapter(load_profile("test-rig")).snapshot()
        translated = base._replace(last_error=LastError(4, "12:00:01", Z_TEXT, Z0[0], "linuxcnc"))
        self.assertIn('<div class="errbox-raw">LinuxCNC: {}</div>'.format(esc(Z0[0])),
                      R.render_body(_vm(translated)))
        raw = base._replace(last_error=LastError(5, "12:00:02", "LinuxCNC: odd", "odd", "linuxcnc"))
        self.assertNotIn("errbox-raw", R.render_body(_vm(raw)))

    def test_text_is_escaped(self):
        base = MockAdapter(load_profile("test-rig")).snapshot()
        evil = base._replace(message="<b>x</b>", last_error=LastError(1, "t", "<i>y</i>", "<s>", "linuxcnc"))
        body = R.render_body(_vm(evil))
        for tag in ("<b>x</b>", "<i>y</i>", "<s>"):
            self.assertNotIn(tag, body)

    def test_in_observer_mode_tamam_stays_enabled(self):
        body = R.render_body(_vm(self.estopped(), observe=True))
        stick = body[body.index('<div class="top-stick">'):body.index('<div class="msgband"')]
        self.assertIn('data-observe="1"', stick)         # the banner, then the band
        ack = re.search(r'<button [^>]*data-action="ack_error"[^>]*>', body).group(0)
        self.assertNotIn("disabled", ack)

    def test_the_box_is_large_red_and_the_band_sticks_under_the_rail(self):
        def rule(selector: str) -> str:
            m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
            self.assertIsNotNone(m, selector)
            return m.group(1)

        size = float(re.search(r"font-size:\s*([\d.]+)px", rule(".errbox-text")).group(1))
        self.assertGreaterEqual(size, 18.0)
        self.assertIn("border: 2px solid var(--bad)", rule(".errbox"))
        stick = rule(".top-stick")
        self.assertIn("position: sticky", stick)
        z = int(re.search(r"z-index:\s*(\d+)", stick).group(1))
        rail = int(re.search(r"z-index:\s*(\d+)", rule(".rail")).group(1))
        lost = int(re.search(r"z-index:\s*(\d+)", rule(".lost")).group(1))
        self.assertLess(z, rail)
        self.assertLess(z, lost)
        self.assertIn('.msg-line[data-severity="warn"] { color: var(--warn); }', CSS)
        self.assertNotIn("/* ---- footer ---- */", CSS)


class TestMdiHistoryMarkup(unittest.TestCase):

    def test_each_status_has_its_colour(self):
        base = MockAdapter(load_profile("test-rig")).snapshot()
        history = (MdiEntry("G0 X1", True, "n", "done"), MdiEntry("G0 X2", True, "n", "sent"),
                   MdiEntry("G0 X3", True, "n", "rejected"), MdiEntry("G0 X4", False, "n", "refused"))
        body = R.render_body(_vm(base._replace(mdi_history=history)))
        for entry, colour in zip(history, (R.OK, R.WARN, R.BAD, R.BAD)):
            with self.subTest(status=entry.status):
                self.assertIn('data-status="{}"><code>{}</code><span class="mdi-note" '
                              'style="color:{}">'.format(entry.status, entry.command, colour), body)

    def test_an_entry_without_a_status_is_never_done(self):
        self.assertEqual(VM.mdi_status(MdiEntry("G0 X1", True, "Gönderildi.")), "sent")
        self.assertEqual(VM.mdi_status(MdiEntry("G0 X1", False, "Boş komut.")), "refused")
        self.assertEqual(VM.mdi_status(MdiEntry("G0 X1", True, "", "done")), "done")


if __name__ == "__main__":
    unittest.main(verbosity=2)
