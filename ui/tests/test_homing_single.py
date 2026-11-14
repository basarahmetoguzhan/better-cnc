"""Plan WP07: homing one axis at a time, a question before a homing again,
and homing controls that say why they are off.

Until WP07 the UI had «Tümünü referansla» alone: home(-1), always on
outside observer mode, and a homed machine re-homed on one click - every
joint unhomed at once (homing.c:401-410) and driven to its switch with the
limits ignored - where AXIS asks first (axis.py:2693-2701). Now:

  - one axis is homed alone (home_joint, AXIS's Machine ▸ Homing): MANUAL,
    teleop off, home(j) - motion homes only that joint, its HOME_SEQUENCE
    not negative (homing.c:265-281, :342-353);
  - motionrules.home_block / home_refusal are the one rule: both adapters
    refuse by it - the machine off, a switch pressed (the way off named),
    a program or MDI line or a homing running, the machine moving - and
    the homing card's buttons follow it, the reason as visible text;
  - motionrules.home_question is what must be asked first: Home All with
    any joint homed, one axis homed again, another axis than Z while Z is
    unhomed; the first homing asks nothing (plan design (g)). The adapter
    sends nothing and returns the question; app.Session shows it on the
    confirmation band and runs the homing on «Evet», confirmed, while the
    question stands - CONFIRM_TIMEOUT_S, no stop since, no other action in
    between.

LinuxCncAdapter runs against fake_linuxcnc (issue_home: one joint alone, a
homing unhomes its joints as it starts), tied to reference/linuxcnc by
test_linuxcnc_adapter.TestHomingFidelity.
"""

from __future__ import annotations

import html as htmllib
import os
import re
import threading
import unittest
from typing import List

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS, rig_profile

import fake_linuxcnc as F
# The modules, not their names: a TestCase class imported by name would run
# here a second time. Only their helpers are reused.
import test_jog_rules as J
import test_linuxcnc_adapter as T
import test_stop_rail as S
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import motionrules as MR
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.app import Session
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import HomingPhase, HomingState, JointHoming, LinkHealth, Severity

C = F.CONSTANTS
PROFILE = load_profile("test-rig")
CONFIG = load_machine_config(PROFILE)
WAIT = A.WAIT_TIMEOUT_S
state_of = J.state_of
HOME = (5.0, 5.0, -5.0)

#: Home All's moves on the rig: HOME_SEQUENCE Z 0, Y 1, X 2; HOME_SEARCH_VEL
#: Z +, X and Y − (configs/test-rig/test-rig.ini); the profile's labels.
ROUTE = "Z üst switch'ine (Z+), sonra Y ön uç switch'ine (Y−), sonra X sol uç switch'ine (X−)"
REHOME_ALL = "Yeniden referans: " + ROUTE + " gidecek — yol açık mı?"
Z_FIRST = "Z referanssız: takım aşağıdaysa {} hareketi çarpabilir."
X_ALONE_Z_UNHOMED = Z_FIRST.format("X") + " X sol uç switch'ine (X−) gidecek — yol açık mı?"
REHOME_Y = "Y yeniden referanslanacak: Y ön uç switch'ine (Y−) gidecek — yol açık mı?"
REHOME_Z = "Z yeniden referanslanacak: Z üst switch'ine (Z+) gidecek — yol açık mı?"
X_PRESSED = ("X sol uç switch'i basılı — önce «Limitten kurtar»: referans alma switch "
             "üstünden başlayamaz.")


def esc(text: str) -> str:
    return htmllib.escape(text, quote=True)


def visible(markup: str) -> str:
    """The markup as the kiosk shows it: app.js noTooltips removes every
    title, so a reason only in a title is not on the screen."""
    return re.sub(r' title="[^"]*"', "", markup)


def homing_card(markup: str) -> str:
    start = markup.index('<span class="card-title">Referans alma</span>')
    return markup[start:markup.index("</section>", start)]


def button_tag(markup: str, action: str, value: str = "") -> str:
    """The <button> carrying data-action=`action` (and data-value=`value`)."""
    probe = 'data-action="{}"'.format(action)
    if value:
        probe += ' data-value="{}"'.format(value)
    i = markup.index(probe)
    return markup[markup.rindex("<button", 0, i):markup.index(">", i) + 1]


# ---------------------------------------------------------------------------
# The rule, pure (motionrules)
# ---------------------------------------------------------------------------


class TestHomeRules(unittest.TestCase):
    """home_block: the card's and every press's reasons, in order;
    home_refusal adds the press's own."""

    def test_a_ready_machine_may_home_all_and_each_axis(self):
        for homed in ("", "Z", "XYZ"):
            state = state_of(homed=homed, at=HOME)
            with self.subTest(homed=homed):
                self.assertEqual(MR.home_block(state, PROFILE), "")
                for axis in (None, "X", "Y", "Z"):
                    self.assertEqual(MR.home_refusal(state, PROFILE, axis), "")

    def test_each_block_in_order(self):
        on = state_of()
        off = on._replace(estop=on.estop.software_estop())
        cases = (
            ("unknown", on.as_status_unknown(), MR.HOME_UNKNOWN),
            ("unknown over a pressed switch",
             state_of(ends=("X-",)).as_status_unknown(), MR.HOME_UNKNOWN),
            ("a switch, the machine off", off._replace(
                hard_limits=state_of(ends=("X-",)).hard_limits), X_PRESSED),
            ("off", off, MR.HOME_OFF),
            ("paused", state_of(program_paused=True), MR.HOME_PAUSED),
            ("a program", state_of(program_running=True), MR.HOME_RUNNING),
            ("auto, reading", state_of(task_mode="auto", interp_state="reading"),
             MR.HOME_RUNNING),
            ("an MDI line", state_of(task_mode="mdi", interp_state="reading", mdi_running=True),
             MR.HOME_RUNNING),
            ("paused not read", state_of(program_paused=None), MR.HOME_UNREAD),
            ("MDI not read", state_of(mdi_running=None), MR.HOME_UNREAD),
            ("homing", state_of(homed="Z", homing="Y"), MR.HOME_BUSY),
        )
        for name, state, want in cases:
            with self.subTest(case=name):
                self.assertEqual(MR.home_block(state, PROFILE), want)
                self.assertEqual(MR.home_refusal(state, PROFILE), want)
                self.assertEqual(MR.home_refusal(state, PROFILE, "Z"), want)

    def test_the_switch_named_and_the_way_off(self):
        self.assertIn("X sol uç", X_PRESSED)
        self.assertIn("«Limitten kurtar»", X_PRESSED)
        self.assertEqual(MR.home_block(state_of(ends=("X-",)), PROFILE), X_PRESSED)

    def test_the_switches_do_not_count_while_the_board_link_is_lost(self):
        """Stale flags (state.HardLimit): the machine is off then anyway."""
        base = state_of(ends=("X-",))
        for pin in ("watchdog_has_bit", "io_error"):
            with self.subTest(pin=pin):
                state = base._replace(estop=base.estop.software_estop(),
                                      link=base.link._replace(**{pin: True}))
                self.assertEqual(MR.home_block(state, PROFILE), MR.HOME_OFF)

    def test_a_homing_joints_own_switch_is_no_block_of_its_own(self):
        """HOME_IGNORE_LIMITS: Z on its top switch while it homes."""
        state = state_of(homing="Z", ends=("Z+",))
        self.assertEqual(MR.home_block(state, PROFILE), MR.HOME_BUSY)

    def test_moving_is_the_press_question_not_the_cards(self):
        for moving, want in ((True, MR.HOME_MOVING), (None, MR.HOME_MOVING_UNREAD)):
            state = state_of(moving=moving)
            with self.subTest(moving=moving):
                self.assertEqual(MR.home_block(state, PROFILE), "")
                self.assertEqual(MR.home_refusal(state, PROFILE), want)
                self.assertEqual(MR.home_refusal(state, PROFILE, "X"), want)
        self.assertIn("«Durdur»", MR.HOME_MOVING)

    def test_one_axis_must_be_the_letter_of_one_joint(self):
        state = state_of()
        self.assertEqual(MR.home_refusal(state, PROFILE, "A"),
                         "Bu makinede A ekseni yok — referans alınamaz.")
        gantry = state._replace(homing=state.homing._replace(joints=state.homing.joints + (
            state.homing.joints[1]._replace(joint=3),)))
        why = MR.home_axis_refusal(gantry, "Y")
        self.assertIn("birden çok eklemde", why)
        self.assertIn("«Tümünü referansla»", why)
        self.assertEqual(MR.home_refusal(gantry, PROFILE, "Y"), why)
        self.assertEqual(MR.home_refusal(gantry, PROFILE), "")      # Home All homes it
        self.assertEqual(MR.home_axis_refusal(gantry, "X"), "")

    def test_the_busy_texts_say_how_to_stop(self):
        self.assertEqual(MR.HOME_BUSY, "Referans alma sürüyor — durdurmak için «Durdur».")
        self.assertIn("«Limitten kurtar»", MR.HOME_STOP_NOTE)
        self.assertIn("«Durdur» (Esc)", MR.HOME_STOP_NOTE)


class TestHomeQuestion(unittest.TestCase):
    """What must be asked before a homing the rules allow."""

    def test_the_first_homing_asks_nothing(self):
        state = state_of()
        self.assertEqual(MR.home_question(state, PROFILE), "")
        self.assertEqual(MR.home_question(state, PROFILE, "Z"), "")

    def test_home_all_with_any_joint_homed_says_what_will_move(self):
        for homed in ("Z", "ZY", "X", "XYZ"):
            with self.subTest(homed=homed):
                self.assertEqual(MR.home_question(state_of(homed=homed, at=HOME), PROFILE),
                                 REHOME_ALL)

    def test_one_axis_homed_again(self):
        self.assertEqual(MR.home_question(state_of(homed="ZY", at=HOME), PROFILE, "Y"), REHOME_Y)
        self.assertEqual(MR.home_question(state_of(homed="Z", at=HOME), PROFILE, "Z"), REHOME_Z)

    def test_x_or_y_alone_while_z_is_unhomed(self):
        self.assertEqual(MR.home_question(state_of(), PROFILE, "X"), X_ALONE_Z_UNHOMED)
        self.assertEqual(MR.home_question(state_of(), PROFILE, "Y"),
                         Z_FIRST.format("Y") + " Y ön uç switch'ine (Y−) gidecek — yol açık mı?")
        self.assertEqual(MR.home_question(state_of(homed="Y"), PROFILE, "X"), X_ALONE_Z_UNHOMED)

    def test_both_at_once(self):
        self.assertEqual(
            MR.home_question(state_of(homed="X"), PROFILE, "X"),
            Z_FIRST.format("X") + " X yeniden referanslanacak: X sol uç switch'ine (X−) gidecek "
            "— yol açık mı?")

    def test_an_axis_the_machine_lacks_is_not_asked_about(self):
        """home_refusal refuses it (TestHomeRules); there is nothing to ask."""
        self.assertEqual(MR.home_question(state_of(homed="Z"), PROFILE, "A"), "")

    def test_z_homed_x_or_y_alone_asks_nothing(self):
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                self.assertEqual(MR.home_question(state_of(homed="Z"), PROFILE, axis), "")
        self.assertEqual(MR.TOOL_AXIS, "Z")

    def test_every_question_ends_with_the_moves_and_asks_about_the_way(self):
        for state, axis in ((state_of(homed="Z"), None), (state_of(homed="ZY"), "Y"),
                            (state_of(), "X")):
            with self.subTest(axis=axis):
                self.assertTrue(MR.home_question(state, PROFILE, axis).endswith(
                    "gidecek — yol açık mı?"))

    def test_the_route_follows_the_ini(self):
        """Another HOME_SEQUENCE, another order; another HOME_SEARCH_VEL
        sign, the other switch."""
        profile = rig_profile(self, edits={
            ("JOINT_0", "HOME_SEQUENCE"): "0", ("JOINT_2", "HOME_SEQUENCE"): "2",
            ("JOINT_1", "HOME_SEARCH_VEL"): "5.0",
        })
        adapter = MockAdapter(profile)
        adapter.software_estop_reset()
        adapter.home_joint("Z", confirmed=True)
        self.assertEqual(
            MR.home_question(adapter.snapshot(), profile),
            "Yeniden referans: X sol uç switch'ine (X−), sonra Y arka uç switch'ine (Y+), "
            "sonra Z üst switch'ine (Z+) gidecek — yol açık mı?")

    def test_joints_of_one_step_together_and_the_fallbacks(self):
        def joint(n, axis, seq, vel):
            return JointHoming(n, axis, HomingPhase.NOT_HOMED, seq, True, vel)
        homing = HomingState(joints=(joint(0, "X", 1, -5.0), joint(1, "Y", 1, -5.0),
                                     joint(2, "Z", 0, 4.0), joint(3, "A", 2, 0.0)))
        self.assertEqual(
            MR.home_route(homing, PROFILE),
            "Z üst switch'ine (Z+), sonra X sol uç switch'ine (X−) ve Y ön uç switch'ine (Y−), "
            "sonra A (arama yok — bulunduğu yer referans olur)")
        marble = load_profile("marble")      # no switches named
        self.assertEqual(MR.home_move(joint(0, "X", 0, -5.0), marble), "X− yönüne")
        self.assertEqual(MR.home_move(joint(2, "Z", 0, 4.0), marble), "Z+ yönüne")


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake
# ---------------------------------------------------------------------------


class _Case(T.FakeCase):

    def sent(self):
        return self.machine.calls_except("wait_complete")


class TestHomeJointSequence(_Case):

    def test_from_manual_teleop_off_then_home_of_that_joint(self):
        """axis.py home_joint + go_home (:2708-2728, :1954-1957), not waited
        for: only Z homes, at its HOME; X and Y stay unhomed and motion in
        free mode, so the next jog is a joint jog."""
        adapter = self.on(homed=False)
        self.assertIsNone(adapter.home_joint("Z"))
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("teleop_enable", (0,)), ("wait_complete", (WAIT,)), ("home", (2,)),
        ])
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Referans alma başlatıldı: yalnız Z.")
        self.assertEqual([j.phase for j in snap.homing.joints],
                         [HomingPhase.NOT_HOMED, HomingPhase.NOT_HOMED, HomingPhase.HOMED])
        self.assertEqual(snap.positions[2].machine, HOME[2])
        self.assertEqual(self.machine.motion_mode, C["TRAJ_MODE_FREE"])
        self.machine.log.clear()
        adapter.jog("X", 1)
        self.assertEqual(self.sent(), [("jog", (C["JOG_INCREMENT"], 1, 0, J.JOG_VEL, 1.0))])

    def test_from_auto_mode_manual_first(self):
        adapter = self.on(homed=False)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.home_joint("Y", confirmed=True)
        self.assertNoLinuxCncErrors()
        self.assertEqual(self.machine.log, [
            ("mode", (C["MODE_MANUAL"],)), ("wait_complete", (WAIT,)),
            ("teleop_enable", (0,)), ("wait_complete", (WAIT,)), ("home", (1,)),
        ])

    def test_each_axis_its_own_joint(self):
        for axis, joint in (("X", 0), ("Y", 1), ("Z", 2)):
            with self.subTest(axis=axis):
                adapter = self.on(homed=False)
                adapter.home_joint(axis, confirmed=True)
                self.assertEqual(self.sent()[-1], ("home", (joint,)))
                homed = [j.axis for j in adapter.snapshot().homing.joints
                         if j.phase is HomingPhase.HOMED]
                self.assertEqual(homed, [axis])

    def test_an_axis_the_machine_lacks_is_a_key_error_before_anything_is_read(self):
        adapter = self.on(homed=False)
        polls = self.machine.stat_polls
        with self.assertRaises(KeyError):
            adapter.home_joint("A")
        self.assertEqual(self.machine.stat_polls, polls)
        self.assertEqual(self.machine.log, [])

    def test_a_homing_not_waited_for_shows_as_homing_and_turns_the_card_off(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_joint("Z")
        snap = adapter.snapshot()
        self.assertEqual([j.phase for j in snap.homing.joints],
                         [HomingPhase.NOT_HOMED, HomingPhase.NOT_HOMED, HomingPhase.HOMING])
        card = VM.homing_panel(snap, CONFIG, PROFILE)
        self.assertFalse(card["home_enabled"])
        self.assertEqual(card["home_disabled_reason"], MR.HOME_BUSY)
        self.assertEqual(card["stop_note"], MR.HOME_STOP_NOTE)
        self.machine.log.clear()
        self.assertIsNone(adapter.home_joint("X", confirmed=True))
        self.assertEqual(self.machine.log, [])               # refused, nothing sent
        self.assertEqual(adapter.snapshot().message, MR.HOME_BUSY)
        self.machine.finish_homing()
        self.assertEqual(adapter.snapshot().positions[2].machine, HOME[2])


class TestTheQuestionFirst(_Case):
    """A homed joint homed again, or X / Y alone while Z is unhomed: the
    first press sends nothing - not even a mode change - and returns the
    question; confirmed, the homing goes."""

    def test_rehoming_one_axis(self):
        adapter = self.on(homed=True)
        self.assertEqual(adapter.home_joint("Y"), REHOME_Y)
        self.assertEqual(self.machine.log, [])
        self.assertIsNone(adapter.home_joint("Y", confirmed=True))
        self.assertEqual(self.machine.log, [
            ("teleop_enable", (0,)), ("wait_complete", (WAIT,)), ("home", (1,)),
        ])
        self.assertNoLinuxCncErrors()

    def test_home_all_with_one_joint_homed(self):
        adapter = self.on(homed=False)
        adapter.home_joint("Z")
        self.machine.log.clear()
        self.assertEqual(adapter.home_all(), REHOME_ALL)
        self.assertEqual(self.machine.log, [])
        adapter.home_all(confirmed=True)
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (-1,))])
        self.assertTrue(adapter.snapshot().homing.all_homed)

    def test_x_alone_while_z_is_unhomed(self):
        adapter = self.on(homed=False)
        self.assertEqual(adapter.home_joint("X"), X_ALONE_Z_UNHOMED)
        self.assertEqual(self.machine.log, [])
        adapter.home_joint("X", confirmed=True)
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (0,))])

    def test_the_first_home_all_goes_without_one(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        self.assertIsNone(adapter.home_all())
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (-1,))])
        self.assertEqual(adapter.snapshot().message, "Referans alma başlatıldı.")

    def test_rehoming_unhomes_the_joint_while_it_homes(self):
        """homing.c:771-772 (FakeMachine.issue_home): Z reads homing, not
        homed, until it ends; the others stay homed."""
        adapter = self.on(homed=True, machine=F.FakeMachine(home_instantly=False))
        adapter.home_joint("Z", confirmed=True)
        phases = [j.phase for j in adapter.snapshot().homing.joints]
        self.assertEqual(phases, [HomingPhase.HOMED, HomingPhase.HOMED, HomingPhase.HOMING])

    def test_a_refusal_comes_before_the_question(self):
        """A homed machine running a program is refused - never asked."""
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_READING"]
        for call in (adapter.home_all, lambda: adapter.home_joint("X")):
            with self.subTest(call=call):
                self.assertIsNone(call())
                self.assertEqual(adapter.snapshot().message, MR.HOME_RUNNING)
        self.assertEqual(self.machine.log, [])


class TestHomeRefusals(_Case):
    """Refused with the shared rule's words, nothing sent."""

    def assertRefused(self, adapter, text):
        for name, call in (("home_all", lambda: adapter.home_all(confirmed=True)),
                           ("home_joint", lambda: adapter.home_joint("Z", confirmed=True))):
            with self.subTest(action=name):
                self.machine.log.clear()
                self.assertIsNone(call())
                self.assertEqual(self.machine.log, [])
                snap = adapter.snapshot()
                self.assertEqual(snap.message, text)
                self.assertIs(snap.message_severity, Severity.WARN)

    def test_machine_off(self):
        self.assertRefused(self.make(), MR.HOME_OFF)

    def test_an_mdi_line_running(self):
        adapter = self.on(homed=True)
        self.run_mdi_program()
        self.assertRefused(adapter, MR.HOME_RUNNING)

    def test_a_program_paused(self):
        adapter = self.on(homed=True)
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.interp_state = C["INTERP_PAUSED"]
        self.machine.paused = True
        self.assertRefused(adapter, MR.HOME_PAUSED)

    def test_a_switch_pressed_names_it_and_the_way_off(self):
        adapter = self.make()
        self.machine.hard_min[0] = 1          # X left, the machine off (tripped)
        self.hal.pins["hm2_7i92.0.gpio.021.in_not"] = True
        self.assertRefused(adapter, X_PRESSED)

    def test_with_the_link_lost_the_switch_does_not_count(self):
        adapter = self.make()
        self.machine.hard_min[0] = 1
        self.bite()
        self.assertRefused(adapter, MR.HOME_OFF)

    def test_while_a_joint_jog_moves(self):
        """A homing would start under the jog (command.c:1366-1388 does not
        ask): refused, the card stays on."""
        adapter = self.on(homed=False)
        self.machine.jogging_active = True       # free mode: INPOS false
        self.assertRefused(adapter, MR.HOME_MOVING)
        self.assertTrue(VM.homing_panel(adapter.snapshot(), CONFIG, PROFILE)["home_enabled"])


class TestTheHomingGap(_Case):
    """Between two joints of Home All no joint reads homing for a moment
    (review of WP06, _homing_sequence_gap_possible): a home press looked at
    there is looked at again, HOMING_GAP_RECHECK_S later - a Home All sent
    then would unhome joint 0 (homing.c:553-560), one joint's home would
    take the sequence over (homing.c:265-281)."""

    def _between_z_and_y(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        self.machine.homed[2] = 1
        self.machine.axis_pos[2] = self.machine.joint_pos[2] = HOME[2]
        self.machine.log.clear()
        return adapter

    def test_it_looks_again_and_refuses_when_the_next_joint_has_started(self):
        for name, call in (("home_all", lambda a: a.home_all(confirmed=True)),
                           ("home_joint", lambda a: a.home_joint("X", confirmed=True))):
            with self.subTest(action=name):
                adapter = self._between_z_and_y()
                waits: List[float] = []

                def y_starts(seconds):
                    waits.append(seconds)
                    self.machine.homing[1] = 1

                adapter._sleep = y_starts
                call(adapter)
                self.assertEqual(waits, [A.HOMING_GAP_RECHECK_S])
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, MR.HOME_BUSY)
                self.assertNoLinuxCncErrors()

    def test_in_the_fakes_real_gap_between_two_groups_it_looks_again(self):
        """P0 cleanup: the gap the fake's Home All leaves between Z's group
        and Y's (advance_homing); Y starts during the second look."""
        for name, call in (("home_all", lambda a: a.home_all(confirmed=True)),
                           ("home_joint", lambda a: a.home_joint("X", confirmed=True))):
            with self.subTest(action=name):
                adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
                adapter.home_all()
                self.machine.advance_homing()           # Z homed: the gap
                self.machine.log.clear()
                adapter._sleep = lambda seconds: self.machine.advance_homing()
                call(adapter)
                self.assertEqual(self.machine.log, [])
                self.assertEqual(adapter.snapshot().message, MR.HOME_BUSY)
                self.assertEqual(self.machine.homing[:3], [0, 1, 0])
                self.machine.finish_homing()
                self.assertEqual(self.machine.homed[:3], [1, 1, 1])
                self.assertNoLinuxCncErrors()

    def test_a_homing_left_partial_costs_one_look_more(self):
        adapter = self._between_z_and_y()
        adapter.home_joint("Y")
        self.assertEqual(self.clock.sleeps, [A.HOMING_GAP_RECHECK_S])
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (1,))])


class TestStopDuringTheHoming(_Case):
    """Nothing that began before a stop moves after it (_stopped_since):
    a stop or the software estop while home_joint switches modes sends no
    home(j)."""

    def test_no_home_after_a_stop_or_an_estop_during_the_mode_switch(self):
        # The estop takes the machine out of ON: the mode switch's own
        # read-back refuses first (_ensure_mode), before the stop check.
        for overtaker, why in (("stop_motion", A.STOP_REFUSAL),
                               ("software_estop", MR.HOME_OFF)):
            with self.subTest(overtaker=overtaker):
                adapter = self.on(homed=False)
                self.machine.task_mode = C["MODE_MDI"]
                self.machine.motion_mode = C["TRAJ_MODE_COORD"]
                gate = threading.Event()
                self.machine.wait_gate = gate
                worker = threading.Thread(target=lambda: adapter.home_joint("Z"), daemon=True)
                self.addCleanup(worker.join, F.GATE_LIMIT_S)
                self.addCleanup(gate.set)
                worker.start()
                self.assertTrue(self.machine.wait_entered.wait(F.GATE_LIMIT_S))
                getattr(adapter, overtaker)()
                gate.set()
                worker.join(F.GATE_LIMIT_S)
                self.assertFalse(worker.is_alive())
                self.assertNotIn("home", self.machine.calls())
                self.assertEqual(adapter._message, why)

    def test_premise_without_the_stop_it_homes(self):
        adapter = self.on(homed=False)
        self.machine.task_mode = C["MODE_MDI"]
        self.machine.motion_mode = C["TRAJ_MODE_COORD"]
        adapter.home_joint("Z")
        self.assertEqual(self.sent()[-1], ("home", (2,)))


class TestSessionOnTheFake(_Case):
    """Through app.Session, as the page posts it."""

    def test_home_joint_then_a_confirmed_home_all(self):
        adapter = self.on(homed=False)
        clock = _Clock()
        sess = Session(adapter, self.profile, load_machine_config(self.profile), clock=clock)
        sess.act("home_joint", "Z")
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (2,))])
        self.assertIsNone(sess.view("kontrol")["confirm"])
        self.machine.log.clear()
        sess.act("home_all", "")
        self.assertEqual(self.machine.log, [])
        band = sess.view("kontrol")["confirm"]
        self.assertEqual(band["text"], REHOME_ALL)
        clock.now += app.CONFIRM_MIN_S
        sess.act("confirm", band["token"])
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (-1,))])
        vm = sess.view("kontrol")
        self.assertIsNone(vm["confirm"])
        self.assertTrue(vm["kontrol"]["homing"]["all_homed"])

    def test_a_double_tap_does_not_answer_its_own_question(self):
        """Review of WP07: on a narrow screen the band opens where the
        button that asked was, and a double tap's second press lands on
        «Evet» (browser at 800x600: every joint re-homed). Within
        app.CONFIRM_MIN_S of the question «Evet» sends nothing - not even a
        mode change - and the question stands; after it, the same token
        homes."""
        adapter = self.on(homed=True)
        clock = _Clock()
        sess = Session(adapter, self.profile, load_machine_config(self.profile), clock=clock)
        sess.act("home_all", "")
        band = sess.view("kontrol")["confirm"]
        self.assertFalse(band["ready"])
        clock.now += 0.25
        sess.act("confirm", band["token"])
        self.assertEqual(self.machine.log, [])
        again = sess.view("kontrol")["confirm"]
        self.assertEqual((again["token"], again["refused"]), (band["token"], False))
        clock.now += app.CONFIRM_MIN_S - 0.25
        self.assertTrue(sess.view("kontrol")["confirm"]["ready"])
        sess.act("confirm", band["token"])
        self.assertEqual(self.sent(), [("teleop_enable", (0,)), ("home", (-1,))])

    def test_cancel_sends_nothing(self):
        adapter = self.on(homed=True)
        sess = Session(adapter, self.profile, load_machine_config(self.profile))
        sess.act("home_joint", "X")
        self.assertTrue(sess.view("kontrol")["confirm"]["token"])
        sess.act("cancel_confirm", "")
        self.assertIsNone(sess.view("kontrol")["confirm"])
        self.assertEqual(self.machine.log, [])


# ---------------------------------------------------------------------------
# MockAdapter: the same rule
# ---------------------------------------------------------------------------


class TestMockHoming(unittest.TestCase):

    def ready(self) -> MockAdapter:
        adapter = MockAdapter(PROFILE)
        adapter.software_estop_reset()
        return adapter

    def test_one_axis_alone_at_its_home(self):
        adapter = self.ready()
        self.assertIsNone(adapter.home_joint("Z"))
        snap = adapter.snapshot()
        self.assertEqual({j.axis: j.phase for j in snap.homing.joints},
                         {"X": HomingPhase.NOT_HOMED, "Y": HomingPhase.NOT_HOMED,
                          "Z": HomingPhase.HOMED})
        self.assertEqual({p.axis: (p.homed, p.machine) for p in snap.positions},
                         {"X": (False, 0.0), "Y": (False, 0.0), "Z": (True, HOME[2])})
        self.assertEqual(snap.message, "Referans tamam: yalnız Z.")
        self.assertEqual(snap.alarms[0].text, "Referans alma tamamlandı (Z)")

    def test_the_same_questions(self):
        adapter = self.ready()
        self.assertEqual(adapter.home_joint("X"), X_ALONE_Z_UNHOMED)
        self.assertFalse(any(j.phase is HomingPhase.HOMED for j in adapter.snapshot().homing.joints))
        adapter.home_joint("Z")
        self.assertEqual(adapter.home_all(), REHOME_ALL)
        self.assertIsNone(adapter.home_all(confirmed=True))
        self.assertTrue(adapter.snapshot().homing.all_homed)
        self.assertEqual(adapter.home_joint("Y"), REHOME_Y)

    def test_the_same_refusals_its_program_running_included(self):
        """docs/11 Known gaps until plan WP07: the mock homed while its
        program ran, which LinuxCncAdapter refuses."""
        adapter = MockAdapter(PROFILE)
        self.assertIsNone(adapter.home_joint("Z", confirmed=True))
        self.assertEqual(adapter.snapshot().message, MR.HOME_OFF)
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.load_program("a.ngc")
        adapter.program_start()
        for call in (lambda: adapter.home_all(confirmed=True),
                     lambda: adapter.home_joint("X", confirmed=True)):
            self.assertIsNone(call())
            self.assertEqual(adapter.snapshot().message, MR.HOME_RUNNING)
            self.assertIs(adapter.snapshot().message_severity, Severity.WARN)

    def test_an_axis_it_lacks_is_a_key_error(self):
        with self.assertRaises(KeyError):
            self.ready().home_joint("A")


class TestSameTableSameDecisions(_Case):
    """One table, both adapters: the same question, or the same refusal,
    for the same machine."""

    #: (name, axes homed first, the press's axis (None: Home All), the
    #: machine off, its program running)
    ROWS = (
        ("nothing homed, X", "", "X", False, False),
        ("Z homed, Y", "Z", "Y", False, False),
        ("Z homed, all", "Z", None, False, False),
        ("all homed, all", "XYZ", None, False, False),
        ("all homed, Y", "XYZ", "Y", False, False),
        ("off, Z", "", "Z", True, False),
        ("running, all", "XYZ", None, False, True),
    )

    def test_rows(self):
        for name, homed, axis, off, running in self.ROWS:
            with self.subTest(row=name):
                mock = MockAdapter(PROFILE)
                if not off:
                    mock.software_estop_reset()
                real = self.make() if off else self.on(homed=False)
                for a in homed:
                    mock.home_joint(a, confirmed=True)
                    real.home_joint(a, confirmed=True)
                if running:
                    mock.load_program("a.ngc")
                    mock.program_start()
                    self.machine.file = self.ngc()
                    self.machine.task_mode = C["MODE_AUTO"]
                    self.machine.interp_state = C["INTERP_READING"]
                    self.machine.motion_mode = C["TRAJ_MODE_COORD"]

                def press(adapter):
                    return adapter.home_all() if axis is None else adapter.home_joint(axis)

                self.assertEqual(press(mock), press(real))
                if off or running:
                    self.assertEqual(mock.snapshot().message, real.snapshot().message)


# ---------------------------------------------------------------------------
# app.Session: the question, «Evet», «Vazgeç»
# ---------------------------------------------------------------------------


class _Clock(object):
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TestSessionQuestion(unittest.TestCase):

    def session(self, homed: str = "Z", observe: bool = False):
        adapter = MockAdapter(PROFILE)
        adapter.software_estop_reset()
        for axis in homed:
            adapter.home_joint(axis, confirmed=True)
        self.clock = _Clock()
        return Session(adapter, PROFILE, CONFIG, clock=self.clock, observe=observe)

    def homed(self, sess) -> List[str]:
        return [j.axis for j in sess.adapter.snapshot().homing.joints
                if j.phase is HomingPhase.HOMED]

    def yes(self, sess, token: str) -> None:
        """«Evet» once it counts (app.CONFIRM_MIN_S after the question): it
        runs the action if the question still stands."""
        self.clock.now += app.CONFIRM_MIN_S
        sess.act("confirm", token)

    def test_the_first_press_asks_and_changes_nothing(self):
        sess = self.session()
        before = sess.adapter.snapshot()
        sess.act("home_all", "")
        band = sess.view("kontrol")["confirm"]
        self.assertEqual(band["text"], REHOME_ALL)
        self.assertRegex(band["token"], r"^[0-9a-f]{16}$")
        after = sess.adapter.snapshot()
        self.assertEqual((after.homing, after.positions, after.message),
                         (before.homing, before.positions, before.message))

    def test_yes_runs_it(self):
        sess = self.session()
        sess.act("home_all", "")
        self.yes(sess, sess.view("kontrol")["confirm"]["token"])
        self.assertEqual(self.homed(sess), ["X", "Y", "Z"])
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_yes_too_soon_is_no_answer_and_the_question_stands(self):
        """Review of WP07: a double tap on the button that asked lands its
        second press on «Evet». For app.CONFIRM_MIN_S the band shows «Evet»
        off (`ready` False) and an «Evet» sends nothing, the question
        standing with its token and its deadline; then it counts."""
        sess = self.session()
        sess.act("home_all", "")
        band = sess.view("kontrol")["confirm"]
        self.assertFalse(band["ready"])
        self.clock.now += app.CONFIRM_MIN_S - 0.01
        sess.act("confirm", band["token"])
        self.assertEqual(self.homed(sess), ["Z"])
        again = sess.view("kontrol")["confirm"]
        self.assertEqual((again["token"], again["text"], again["refused"], again["ready"]),
                         (band["token"], REHOME_ALL, False, False))
        self.clock.now += 0.01
        self.assertTrue(sess.view("kontrol")["confirm"]["ready"])
        sess.act("confirm", band["token"])
        self.assertEqual(self.homed(sess), ["X", "Y", "Z"])
        # Its deadline stayed the first one: an early «Evet» does not extend it.
        sess.act("home_joint", "Y")
        token = sess.view("kontrol")["confirm"]["token"]
        self.clock.now += 0.5
        sess.act("confirm", token)
        self.clock.now += app.CONFIRM_TIMEOUT_S - 0.5
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_a_token_json_cannot_encode_is_refused_not_raised(self):
        """A hand-made body can carry a lone surrogate ("\\ud800" in JSON),
        which plain UTF-8 will not encode: refused like any wrong token,
        never an exception out of Session.act."""
        sess = self.session()
        sess.act("home_all", "")
        self.clock.now += app.CONFIRM_MIN_S
        sess.act("confirm", "\ud800")
        self.assertEqual(self.homed(sess), ["Z"])
        self.assertTrue(sess.view("kontrol")["confirm"]["refused"])

    def test_no_question_no_band_for_the_first_homing(self):
        sess = self.session(homed="")
        sess.act("home_joint", "Z")
        self.assertEqual(self.homed(sess), ["Z"])
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_cancel(self):
        sess = self.session()
        sess.act("home_joint", "X")      # Z homed: no question - X goes
        self.assertEqual(self.homed(sess), ["X", "Z"])
        sess.act("home_joint", "X")      # again: asked
        token = sess.view("kontrol")["confirm"]["token"]
        sess.act("cancel_confirm", "")
        self.assertIsNone(sess.view("kontrol")["confirm"])
        self.yes(sess, token)
        self.assertEqual(sess.view("kontrol")["confirm"], VM.confirm_band({"refused": True}))

    def test_the_question_times_out(self):
        sess = self.session()
        sess.act("home_all", "")
        token = sess.view("kontrol")["confirm"]["token"]
        self.clock.now += app.CONFIRM_TIMEOUT_S - 0.01
        self.assertTrue(sess.view("kontrol")["confirm"]["token"])
        self.clock.now += 0.02
        self.assertIsNone(sess.view("kontrol")["confirm"])
        sess.act("confirm", token)
        self.assertEqual(self.homed(sess), ["Z"])
        band = sess.view("kontrol")["confirm"]
        self.assertTrue(band["refused"])
        self.assertEqual(band["token"], "")
        self.assertEqual(band["text"], VM.CONFIRM_REFUSED)
        self.clock.now += app.CONFIRM_REFUSED_S
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_a_wrong_token_is_refused(self):
        sess = self.session()
        sess.act("home_all", "")
        for bad in ("", "0" * 16, "x", "ğ"):
            with self.subTest(token=bad):
                sess.act("home_all", "")
                self.yes(sess, bad)
                self.assertEqual(self.homed(sess), ["Z"])
                self.assertTrue(sess.view("kontrol")["confirm"]["refused"])

    def test_any_other_action_ends_the_question(self):
        for action, value in (("jog_step", "0.1"), ("jog", "X:1"), ("feed", "50"),
                              ("software_reset", ""), ("home_joint", "Y")):
            with self.subTest(action=action):
                sess = self.session()
                sess.act("home_all", "")
                token = sess.view("kontrol")["confirm"]["token"]
                sess.act(action, value)
                self.yes(sess, token)
                self.assertNotEqual(self.homed(sess), ["X", "Y", "Z"])

    def test_a_newer_question_replaces_the_older(self):
        sess = self.session(homed="XYZ")
        sess.act("home_joint", "X")
        old = sess.view("kontrol")["confirm"]["token"]
        sess.act("home_joint", "Y")
        band = sess.view("kontrol")["confirm"]
        self.assertNotEqual(band["token"], old)
        self.assertEqual(band["text"], REHOME_Y)

    def test_a_stop_or_the_estop_ends_it(self):
        for stop in ("stop", "program_stop", "software_estop"):
            with self.subTest(stop=stop):
                sess = self.session()
                sess.act("home_all", "")
                token = sess.view("kontrol")["confirm"]["token"]
                sess.act(stop, "")
                self.assertIsNone(sess.view("kontrol")["confirm"])
                sess.adapter.software_estop_reset()
                self.yes(sess, token)
                self.assertEqual(self.homed(sess), ["Z"])

    def test_a_stop_while_the_adapter_was_asked_leaves_no_question(self):
        """The question carries the stop count its press began with."""
        sess = self.session()
        asked = sess.adapter.home_all

        def stop_meanwhile(*args, **kwargs):
            sess._note_stop()
            return asked(*args, **kwargs)

        sess.adapter.home_all = stop_meanwhile
        sess.act("home_all", "")
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_the_adapter_checks_its_rules_again_on_yes(self):
        """Asked with the machine on; the estop since: refused anyway."""
        sess = self.session()
        sess.act("home_all", "")
        token = sess.view("kontrol")["confirm"]["token"]
        sess.adapter.software_estop()     # not through Session: no stop counted
        self.yes(sess, token)
        snap = sess.adapter.snapshot()
        self.assertEqual(snap.message, MR.HOME_OFF)
        self.assertEqual(self.homed(sess), ["Z"])

    def test_observer_mode_asks_nothing_and_shows_no_band(self):
        sess = self.session(observe=True)
        for action, value in (("home_all", ""), ("home_joint", "X"), ("confirm", "x"),
                              ("cancel_confirm", "")):
            sess.act(action, value)
        self.assertIsNone(sess.view("kontrol")["confirm"])
        self.assertEqual(self.homed(sess), ["Z"])
        sess.request_confirm("home_all", "", "?")     # never in observer mode, still not shown
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_an_axis_the_adapter_lacks_is_dropped(self):
        """Both adapters raise KeyError for it: a stale or hand-made page
        must not 500 the UI - on the first press or on «Evet»."""
        sess = self.session()
        sess.act("home_joint", "Q")
        self.assertIsNone(sess.view("kontrol")["confirm"])
        token = sess.request_confirm("home_joint", "Q", "?")
        self.yes(sess, token)
        self.assertEqual(self.homed(sess), ["Z"])
        self.assertIsNone(sess.view("kontrol")["confirm"])

    def test_the_constants_are_named_and_marked(self):
        import inspect
        source = inspect.getsource(app)
        for name in ("CONFIRM_TIMEOUT_S", "CONFIRM_REFUSED_S", "CONFIRM_MIN_S"):
            with self.subTest(constant=name):
                block = source[:source.index("\n{} = ".format(name))]
                self.assertIn("Chosen, not measured", block[block.rindex("\n\n"):])
        self.assertEqual(app.CONFIRM_TIMEOUT_S, 15.0)
        # Longer than a double click (OS defaults 0.4-0.5 s), far shorter
        # than the time the question stands.
        self.assertEqual(app.CONFIRM_MIN_S, 1.0)
        self.assertLess(app.CONFIRM_MIN_S, app.CONFIRM_TIMEOUT_S / 10)


class _HeldView(object):
    """snapshot() parks on an Event while `hold` is set (a 1 Hz poll holding
    the session lock); home_all is recorded."""

    def __init__(self) -> None:
        self.inner = MockAdapter(PROFILE)
        self.inner.software_estop_reset()
        self.inner.home_joint("Z", confirmed=True)
        self.calls: List[str] = []
        self.hold = False
        self.entered = threading.Event()
        self.release = threading.Event()

    def snapshot(self):
        if self.hold:
            self.hold = False
            self.entered.set()
            self.release.wait(S.WAIT_S)
        return self.inner.snapshot()

    def home_all(self, confirmed=False):
        self.calls.append("home_all" + (" confirmed" if confirmed else ""))
        return self.inner.home_all(confirmed)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *args, **kwargs: self.calls.append(name)


class TestYesQueuedBehindThePoll(unittest.TestCase):
    """«Evet» is a motion action (app.MOTION_ACTIONS): waiting for the lock
    when DURDUR comes, it is dropped - and the question is gone anyway."""

    def run_queued(self, stop: bool) -> List[str]:
        adapter = _HeldView()
        clock = _Clock()
        sess = Session(adapter, PROFILE, CONFIG, clock=clock)
        sess._lock = S._ObservedLock()
        sess.act("home_all", "")
        token = sess.view("kontrol")["confirm"]["token"]
        clock.now += app.CONFIRM_MIN_S      # «Evet» counts by now
        adapter.hold = True
        poll = threading.Thread(target=sess.view, args=("kontrol",), daemon=True)
        yes = threading.Thread(target=sess.act, args=("confirm", token), daemon=True)
        threads = [poll, yes]
        if stop:
            threads.append(threading.Thread(target=sess.act, args=("stop", ""), daemon=True))
        for t in threads:
            self.addCleanup(t.join, S.WAIT_S)
        self.addCleanup(adapter.release.set)
        poll.start()
        self.assertTrue(adapter.entered.wait(S.WAIT_S))
        yes.start()
        self.assertTrue(sess._lock.waiting.wait(S.WAIT_S))
        if stop:
            threads[2].start()
            S.wait_for(lambda: "stop_motion" in adapter.calls, "the stop")
        adapter.release.set()
        for t in threads:
            t.join(S.WAIT_S)
            self.assertFalse(t.is_alive())
        return adapter.calls

    def test_dropped_after_a_stop(self):
        calls = self.run_queued(stop=True)
        self.assertNotIn("home_all confirmed", calls)

    def test_premise_without_the_stop_it_runs(self):
        self.assertIn("home_all confirmed", self.run_queued(stop=False))


# ---------------------------------------------------------------------------
# The homing card and the band, as the page shows them
# ---------------------------------------------------------------------------


class TestHomingCard(unittest.TestCase):

    def card(self, state, observe=False):
        vm = VM.build(PROFILE, CONFIG, state, "kontrol", observe)
        return vm["kontrol"]["homing"], visible(homing_card(R.render_body(vm)))

    def test_ready_every_button_on(self):
        p, markup = self.card(state_of(homed="Z", at=HOME))
        self.assertTrue(p["home_enabled"])
        self.assertEqual(p["reasons"], [])
        self.assertEqual(p["stop_note"], "")
        self.assertEqual({a: (b["label"], b["enabled"]) for a, b in p["axis_buttons"].items()},
                         {"X": ("X referansla", True), "Y": ("Y referansla", True),
                          "Z": ("Z referansla", True)})
        for axis in "XYZ":
            with self.subTest(axis=axis):
                tag = button_tag(markup, "home_joint", axis)
                self.assertNotIn("disabled", tag)
                self.assertIn(">{} referansla<".format(axis), markup)
                # Each button in its axis's chip.
                chip = markup[markup.index('data-axis="{}"'.format(axis)):]
                self.assertLess(chip.index('data-action="home_joint"'), chip.index("</div>"))
        self.assertNotIn("disabled", button_tag(markup, "home_all"))

    def test_off_with_the_reason_as_text(self):
        on = state_of()
        cases = (
            ("off", on._replace(estop=on.estop.software_estop()), MR.HOME_OFF),
            ("unknown", on.as_status_unknown(), MR.HOME_UNKNOWN),
            ("homing", state_of(homed="Z", homing="Y"), MR.HOME_BUSY),
            ("a program", state_of(program_running=True), MR.HOME_RUNNING),
            ("paused", state_of(program_paused=True), MR.HOME_PAUSED),
            ("a switch", on._replace(estop=on.estop.software_estop(),
                                     hard_limits=state_of(ends=("X-",)).hard_limits), X_PRESSED),
        )
        for name, state, why in cases:
            with self.subTest(case=name):
                p, markup = self.card(state)
                self.assertFalse(p["home_enabled"])
                self.assertEqual(p["home_disabled_reason"], why)
                self.assertEqual(p["reasons"], [why])
                self.assertEqual(markup.count(esc(why)), 1)        # once, as text
                self.assertIn("disabled", button_tag(markup, "home_all"))
                for axis in "XYZ":
                    self.assertIn("disabled", button_tag(markup, "home_joint", axis))

    def test_a_disabled_button_says_the_limit_must_be_cleared_first(self):
        """After a homing stopped in its latch phase, the joint is on its
        switch, the machine off: the card says to clear it first, as the
        limit card does - not merely that the machine is off."""
        on = state_of(homed="Z")
        tripped = on._replace(estop=on.estop.software_estop(),
                              hard_limits=state_of(ends=("Y-",)).hard_limits)
        p, markup = self.card(tripped)
        self.assertIn("«Limitten kurtar»", p["home_disabled_reason"])
        self.assertIn("Y ön uç", p["home_disabled_reason"])
        vm = VM.build(PROFILE, CONFIG, tripped)
        self.assertTrue(vm["kontrol"]["limit"]["enabled"])       # the way off is there

    def test_the_stop_note_while_a_joint_homes(self):
        p, markup = self.card(state_of(homing="Z"))
        self.assertEqual(p["stop_note"], MR.HOME_STOP_NOTE)
        self.assertIn(esc(MR.HOME_STOP_NOTE), markup)
        for state in (state_of(), state_of(homed="XYZ", at=HOME)):
            self.assertEqual(self.card(state)[0]["stop_note"], "")
        self.assertEqual(self.card(state_of(homing="Z"), observe=True)[0]["stop_note"], "")

    def test_observer_mode(self):
        p, markup = self.card(state_of(homed="Z"), observe=True)
        self.assertEqual(p["home_disabled_reason"], VM.OBSERVE_REASON)
        self.assertEqual({b["reason"] for b in p["axis_buttons"].values()}, {VM.OBSERVE_REASON})
        self.assertIn(esc(VM.OBSERVE_REASON), markup)

    def test_a_letter_on_two_joints_has_one_button_off_with_its_reason(self):
        """A gantry's Y on two joints (home_axis_refusal): one Y button, off,
        its reason written out; Home All and the other axes stay on."""
        state = state_of()
        gantry = state._replace(homing=state.homing._replace(joints=state.homing.joints + (
            state.homing.joints[1]._replace(joint=3),)))
        p = VM.homing_panel(gantry, CONFIG, PROFILE)
        why = MR.home_axis_refusal(gantry, "Y")
        self.assertEqual(sorted(p["axis_buttons"]), ["X", "Y", "Z"])
        self.assertEqual((p["axis_buttons"]["Y"]["enabled"], p["axis_buttons"]["Y"]["reason"]),
                         (False, why))
        self.assertTrue(p["axis_buttons"]["X"]["enabled"] and p["home_enabled"])
        self.assertEqual(p["reasons"], [why])

    def test_moving_does_not_switch_it_off(self):
        p, _ = self.card(state_of(moving=True))
        self.assertTrue(p["home_enabled"])

    def test_the_mock_before_and_after(self):
        adapter = MockAdapter(PROFILE)
        p, _ = self.card(adapter.snapshot())
        self.assertEqual(p["home_disabled_reason"], MR.HOME_OFF)
        adapter.software_estop_reset()
        p, _ = self.card(adapter.snapshot())
        self.assertTrue(p["home_enabled"])


class TestConfirmBand(unittest.TestCase):

    def band_vm(self, screen="kontrol", observe=False, refused=False):
        adapter = MockAdapter(PROFILE)
        self.clock = _Clock()
        sess = Session(adapter, PROFILE, CONFIG, clock=self.clock, observe=observe)
        sess.act("software_reset", "")
        sess.act("home_joint", "Z")
        if refused:
            sess.act("confirm", "nope")
        else:
            sess.act("home_all", "")
        return sess, sess.view(screen)

    def test_on_every_screen_in_the_sticky_block(self):
        """Right under the message band, closing the block that stays in
        view (.top-stick)."""
        sess, _ = self.band_vm()
        self.clock.now += app.CONFIRM_MIN_S
        token = sess._confirm.token
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                vm = sess.view(screen)
                markup = R.render_body(vm)
                stick = R.render_confirm(vm["confirm"])
                self.assertIn('<div class="top-stick">{}{}</div>'.format(
                    R.render_msgband(vm), stick), markup)
                self.assertIn('data-confirm="1"', stick)
                self.assertIn(esc(REHOME_ALL), stick)
                yes = button_tag(stick, "confirm", token)
                self.assertNotIn("disabled", yes)
                self.assertIn(">Evet<", stick)
                self.assertNotIn("disabled", button_tag(stick, "cancel_confirm"))
                self.assertIn(">Vazgeç<", stick)
                self.assertIn(esc(VM.CONFIRM_HINT.format(app.CONFIRM_TIMEOUT_S)), stick)

    def test_evet_is_off_for_its_first_second_and_says_why(self):
        """Review of WP07: the band opens where a double tap's second press
        lands, so «Evet» is off until app.CONFIRM_MIN_S after the question -
        the reason as visible text (the kiosk removes titles) - while
        «Vazgeç» is on at once; then «Evet» is on and the hint is the
        usual one."""
        sess, vm = self.band_vm()
        token = sess._confirm.token
        stick = visible(R.render_confirm(vm["confirm"]))
        self.assertIn("disabled", button_tag(stick, "confirm", token))
        self.assertNotIn("disabled", button_tag(stick, "cancel_confirm"))
        wait = VM.CONFIRM_WAIT.format(app.CONFIRM_MIN_S)
        self.assertEqual(wait, "Önce soruyu okuyun — «Evet» 1 s sonra açılır.")
        self.assertIn('<span class="confirm-hint">{}</span>'.format(esc(wait)), stick)
        self.clock.now += app.CONFIRM_MIN_S - 0.01
        self.assertFalse(sess.view("kontrol")["confirm"]["ready"])
        self.clock.now += 0.01
        stick = visible(R.render_confirm(sess.view("kontrol")["confirm"]))
        self.assertNotIn("disabled", button_tag(stick, "confirm", token))
        self.assertNotIn(esc(wait), stick)
        self.assertIn(esc(VM.CONFIRM_HINT.format(app.CONFIRM_TIMEOUT_S)), stick)

    def test_the_json_carries_the_token_where_the_e2e_harness_reads_it(self):
        sess, vm = self.band_vm()
        self.assertEqual(vm["confirm"]["token"], sess._confirm.token)
        self.assertEqual(set(vm["confirm"]), {"token", "text", "refused", "ready", "label",
                                              "yes", "no", "hint", "ready_in_ms"})
        self.assertFalse(vm["confirm"]["ready"])        # sim/e2e/harness.wait_confirm_ready

    def test_the_band_says_when_yes_turns_on(self):
        """P0 cleanup item 8: «Evet» turned on with the next 1 Hz re-render,
        1-2 s after the question. The band carries the time left
        (data-ready-in-ms) and app.js renders once more right then; the
        server's rule is unchanged (an early «Evet» is no answer)."""
        sess, vm = self.band_vm()
        self.assertEqual(vm["confirm"]["ready_in_ms"], int(app.CONFIRM_MIN_S * 1000))
        self.assertIn('data-ready-in-ms="{}"'.format(int(app.CONFIRM_MIN_S * 1000)),
                      R.render_body(vm))
        self.clock.now += 0.4
        vm = sess.view("kontrol")
        self.assertEqual(vm["confirm"]["ready_in_ms"], int(round((app.CONFIRM_MIN_S - 0.4) * 1000)))
        self.clock.now += app.CONFIRM_MIN_S
        vm = sess.view("kontrol")
        self.assertTrue(vm["confirm"]["ready"])
        self.assertEqual(vm["confirm"]["ready_in_ms"], 0)
        self.assertNotIn("data-ready-in-ms", R.render_body(vm))
        with open(os.path.join(os.path.dirname(R.__file__), "static", "app.js"),
                  encoding="utf-8") as fh:
            js = fh.read()
        self.assertIn('app.querySelector("[data-ready-in-ms]")', js)
        swap = js[js.index("function applySwap("):]
        self.assertIn("scheduleReadyRender();", swap[:swap.index("\n  }\n")])

    def test_a_refused_yes_is_said_and_offers_no_button(self):
        _, vm = self.band_vm(refused=True)
        markup = R.render_body(vm)
        self.assertIn('data-confirm="refused"', markup)
        self.assertIn(esc(VM.CONFIRM_REFUSED), markup)
        self.assertNotIn('data-action="confirm"', markup)

    def test_none_without_a_question_and_none_in_observer_mode(self):
        sess = app.build_session("test-rig")
        self.assertIsNone(sess.view("kontrol")["confirm"])
        self.assertNotIn("data-confirm", R.render_body(sess.view("kontrol")))
        self.assertIsNone(VM.confirm_band({"token": "t", "text": "?", "timeout_s": 15},
                                          observe=True))

    def test_the_band_stays_clear_of_the_rail(self):
        """Inside #app, which keeps clear of the fixed rail (test_stop_rail
        TestRailLayout): the band is in render_body, never in the rail."""
        sess, vm = self.band_vm()
        page = R.render_page(vm, "", "")
        rail = page[page.index('<aside id="rail"'):page.index("</aside>")]
        self.assertNotIn("data-confirm", rail)
        self.assertIn("data-confirm", page[page.index('<div id="app">'):])


# ---------------------------------------------------------------------------
# P0 cleanup: a negative HOME_SEQUENCE (item 6), no INI (item 13)
# ---------------------------------------------------------------------------


def _ini_with_sequences(test: unittest.TestCase, seqs) -> "object":
    """The test-rig profile on a synthetic INI whose joints 0, 1, 2 (X, Y,
    Z) have the HOME_SEQUENCE values `seqs`."""
    import test_edge_cases as E
    text = "[TRAJ]\nCOORDINATES = X Y Z\nLINEAR_UNITS = mm\n" + "".join(
        E._synthetic_joint(n).replace("HOME_SEQUENCE = {}".format(n),
                                      "HOME_SEQUENCE = {}".format(seq))
        for n, seq in enumerate(seqs))
    return E._profile_for_ini(test, text)


class TestNegativeSequence(_Case):
    """WP07 review: do_home_one_joint homes every joint sharing abs(sequence)
    when the joint's sequence is negative (homing.c:265-281; the fake's
    home_one_targets). Its one-axis button would move axes it does not name:
    refused, naming them; Home All is untouched."""

    SEQS = (1, -1, 0)              # X 1, Y -1, Z 0: Y's home takes X along

    def state(self):
        return J.state_of(homed="")._replace(homing=HomingState(joints=tuple(
            JointHoming(j, a, HomingPhase.NOT_HOMED, seq, True, -1.0)
            for j, (a, seq) in enumerate(zip("XYZ", self.SEQS)))))

    def test_the_rule(self):
        state = self.state()
        self.assertEqual(MR.home_axis_refusal(state, "Y"), MR.HOME_ONE_GROUP.format("Y", "X"))
        self.assertEqual(MR.home_axis_refusal(state, "X"), "")      # positive: alone
        self.assertEqual(MR.home_axis_refusal(state, "Z"), "")
        self.assertEqual([j.axis for j in state.homing.homed_with(state.homing.joints[1])],
                         ["X", "Y"])

    def test_the_adapter_sends_nothing_and_the_card_says_why(self):
        profile = _ini_with_sequences(self, self.SEQS)
        adapter = self.make(profile=profile, machine=F.FakeMachine(
            home_instantly=False, home_sequence=dict(zip("XYZ", self.SEQS))))
        self.machine.power_on()
        self.assertIsNone(adapter.home_joint("Y", confirmed=True))
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter.snapshot().message, MR.HOME_ONE_GROUP.format("Y", "X"))
        vm = VM.build(profile, load_machine_config(profile), adapter.snapshot(), "kontrol")
        card = vm["kontrol"]["homing"]
        self.assertFalse(card["axis_buttons"]["Y"]["enabled"])
        self.assertTrue(card["axis_buttons"]["X"]["enabled"])
        self.assertIn(MR.HOME_ONE_GROUP.format("Y", "X"),
                      htmllib.unescape(re.sub(r' title="[^"]*"', "", R.render_body(vm))))
        adapter.home_joint("X", confirmed=True)
        self.assertEqual(self.machine.homing[:3], [1, 0, 0])          # X alone
        self.assertTrue(card["home_enabled"])

    def test_the_mock_follows_the_same_rule(self):
        profile = _ini_with_sequences(self, self.SEQS)
        mock = MockAdapter(profile)
        mock.software_estop_reset()
        self.assertIsNone(mock.home_joint("Y", confirmed=True))
        self.assertEqual(mock.snapshot().message, MR.HOME_ONE_GROUP.format("Y", "X"))
        self.assertFalse(any(j.phase is HomingPhase.HOMED for j in mock.snapshot().homing.joints))


class TestNoIniNoInventedOrder(unittest.TestCase):
    """docs/11 'Also open' (marble): without an INI no HOME_SEQUENCE, no
    HOME_IGNORE_LIMITS and no HOME_SEARCH_VEL are made up - the Kontrol
    screen says the order is not known, and no axis is homed alone (whether
    motion would take others along cannot be said)."""

    def test_the_screen_and_the_rules(self):
        from bettercnc.configmodel import joint_config_from_profile
        from bettercnc.state import ORDER_UNKNOWN
        profile = load_profile("marble")
        self.assertIsNone(profile.ini_path)                       # premise
        config = load_machine_config(profile)
        self.assertEqual(A._joint_config(config), joint_config_from_profile(profile))
        self.assertTrue(all(c["HOME_SEQUENCE"] is None and c["HOME_IGNORE_LIMITS"] is None
                            and c["HOME_SEARCH_VEL"] is None
                            for c in joint_config_from_profile(profile).values()))
        mock = MockAdapter(profile)
        mock.software_estop_reset()
        snap = mock.snapshot()
        self.assertEqual(snap.homing.order_label, ORDER_UNKNOWN)
        self.assertIn("Sıra: " + ORDER_UNKNOWN + ".", " ".join(snap.start_blocks))
        vm = VM.build(profile, config, snap, "kontrol")
        page = htmllib.unescape(re.sub(r' title="[^"]*"', "", R.render_body(vm)))
        self.assertIn("Gerekli sıra: <strong>" + ORDER_UNKNOWN, page)
        self.assertNotIn("X → Y → Z", page)
        self.assertNotIn("arama yok", page)
        self.assertIn("arama yönü bilinmiyor (INI yok)", page)
        self.assertIn("sıra ?", page)
        card = vm["kontrol"]["homing"]
        self.assertFalse(card["axis_buttons"]["Z"]["enabled"])
        self.assertEqual(card["axis_buttons"]["Z"]["reason"], MR.HOME_ONE_SEQ_UNKNOWN.format("Z"))
        self.assertIsNone(mock.home_joint("Z", confirmed=True))
        self.assertFalse(any(j.phase is HomingPhase.HOMED for j in mock.snapshot().homing.joints))
        self.assertTrue(card["home_enabled"])
        mock.home_all()
        self.assertTrue(mock.snapshot().homing.all_homed)
        self.assertEqual(MR.home_route(mock.snapshot().homing, profile).count("INI yok"), 4)

    def test_the_limit_setting_is_said_unknown_not_unreadable(self):
        """Review of the P0 cleanup: with no INI, HOME_IGNORE_LIMITS is not
        known - the card must not say an INI value was unreadable and that
        LinuxCNC would refuse the joint, which is about a bad value in an
        INI that exists."""
        profile = load_profile("marble")
        config = load_machine_config(profile)
        mock = MockAdapter(profile)
        mock.software_estop_reset()
        card = VM.build(profile, config, mock.snapshot(), "kontrol")["kontrol"]["homing"]
        self.assertTrue(card["ignore_limits_unknown"])
        self.assertIn("HOME_IGNORE_LIMITS bilinmiyor (INI yok)", card["shares_warning"])
        self.assertNotIn("okunamadı", card["shares_warning"])
        self.assertNotIn("eklemi yüklemez", card["shares_warning"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
