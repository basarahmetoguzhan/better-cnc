"""Plan WP05: recovering from a limit switch - «Limitten kurtar», one press.

On the test rig every switch is a limit switch, and three are home switches
too (X−, Y−, Z+). An axis standing on one keeps LinuxCNC from switching the
machine on: motion enables for one servo cycle and stops again, "joint N on
limit switch error" (reference/linuxcnc/src/emc/motion/control.c:807-826).
Until WP05 «Yazılım sıfırla» sent that ON in a loop and the operator had to go
back to AXIS. Now:

  - motionrules (pure, shared by both adapters and the view) finds the
    switches motion's own flags show pressed - a homing joint's own home
    switch excepted - and plans the way off: which way, how far
    (RECOVERY_STEP_MM unhomed; homed, far enough to land inside the soft
    limits, motion drops a homed jog outside them);
  - LinuxCncAdapter.recover_limit does it in one press: out of ESTOP, MANUAL
    (task takes the override out of ON in MANUAL only, emctaskmain.cc:
    846-852), override_limits() read back, ON read back, teleop off, one
    joint jog away - every step read back, a stop or estop from another
    thread honoured before the override, the ON and the jog;
  - the override clears itself when that jog ends (control.c:1263-1283); the
    snapshot after says how it went (_follow_recovery);
  - «Yazılım sıfırla» sends no ON onto a pressed switch, Home All refuses
    while one is pressed, a jog onto one is refused in every mode;
  - the trip has one Turkish text (messages.limit_trip_text), the drain's and
    the state note's alike, never "komut bu ekrandan gelmedi";
  - Kontrol shows a limit card with «Limitten kurtar: X+ yönünde 3 mm», and
    the header a red badge while the override is on, on every screen.

The adapter runs against fake_linuxcnc, which models what motion does with
the switch flags (the trip, the mask, its clearing, motion's own refusal of a
joint jog onto a switch); test_linuxcnc_adapter.TestOverrideFidelity ties
that model to reference/linuxcnc.
"""

from __future__ import annotations

import html as htmllib
import threading
import unittest
from typing import List, Optional, Tuple

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS

import fake_linuxcnc as F
# The module, not its names: a TestCase class imported by name would run
# here a second time. Only its FakeCase base is reused.
import test_linuxcnc_adapter as T
import test_stop_rail as S
from bettercnc import adapter as A
from bettercnc import app
from bettercnc import messages as M
from bettercnc import motionrules as MR
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import MockAdapter
from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import (
    AxisPosition,
    DrivePower,
    HardLimit,
    HomingPhase,
    LinkHealth,
    MachineState,
    Severity,
)

C = F.CONSTANTS
PROFILE = load_profile("test-rig")
CONFIG = load_machine_config(PROFILE)
WINDOWS = MR.soft_windows(CONFIG)

#: The rig's switch inputs by (joint, end), as the HAL nets take them
#: (profiles.TEST_RIG_INPUTS).
PIN = {
    (0, "+"): "hm2_7i92.0.gpio.020.in_not", (0, "-"): "hm2_7i92.0.gpio.021.in_not",
    (1, "+"): "hm2_7i92.0.gpio.022.in_not", (1, "-"): "hm2_7i92.0.gpio.023.in_not",
    (2, "+"): "hm2_7i92.0.gpio.024.in_not",
}
LABEL = {(0, "+"): "X sağ uç", (0, "-"): "X sol uç", (1, "+"): "Y arka uç",
         (1, "-"): "Y ön uç", (2, "+"): "Z üst"}

TRIP_X_LEFT = "X sol uç switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın."
JOG = C["JOG_INCREMENT"]
#: [DISPLAY]DEFAULT_LINEAR_VELOCITY in configs/test-rig/test-rig.ini: the
#: recovery jog's speed.
JOG_VEL = 3.0


def esc(text: str) -> str:
    return htmllib.escape(text, quote=True)


# ---------------------------------------------------------------------------
# A MachineState by hand, for the pure rules and the view
# ---------------------------------------------------------------------------


def flags(*ends: str) -> Tuple[HardLimit, ...]:
    """Motion's flags with the switches `ends` pressed ("X-", "Z+", ...)."""
    return tuple(
        HardLimit(j, a, (a + "-") in ends, (a + "+") in ends) for j, a in enumerate("XYZ"))


def rig_state(*ends: str, homed: str = "", homing: str = "", at=None, **kw) -> MachineState:
    """The rig as LinuxCncAdapter would map it: the switches `ends` pressed,
    the axes in `homed` homed and those in `homing` homing, the machine
    position `at` (X, Y, Z); then `kw` over it."""
    base = MockAdapter(PROFILE).snapshot()
    phases = {a: (HomingPhase.HOMING if a in homing else
                  HomingPhase.HOMED if a in homed else HomingPhase.NOT_HOMED) for a in "XYZ"}
    at = at or (0.0, 0.0, 0.0)
    state = base._replace(
        homing=base.homing._replace(joints=tuple(
            j._replace(phase=phases[j.axis]) for j in base.homing.joints)),
        positions=tuple(AxisPosition(a, at[i], at[i], a in homed) for i, a in enumerate("XYZ")),
        hard_limits=flags(*ends),
        override_limits=False, moving=False, task_mode="manual", interp_state="idle",
        motion_mode="free",
    )
    return state._replace(**kw)


def plan_of(state: MachineState) -> Optional[MR.RecoveryPlan]:
    return MR.recovery_plan(state, PROFILE, WINDOWS)


# ---------------------------------------------------------------------------
# motionrules: pure
# ---------------------------------------------------------------------------


class TestLimitTrips(unittest.TestCase):

    def test_each_pressed_switch_by_its_label_pin_and_way_off(self):
        trips = MR.limit_trips(rig_state("X-", "Z+"), PROFILE)
        self.assertEqual(trips, (
            MR.LimitTrip(0, "X", "-", "X sol uç", PIN[(0, "-")], +1),
            MR.LimitTrip(2, "Z", "+", "Z üst", PIN[(2, "+")], -1),
        ))

    def test_min_before_max_in_joint_order(self):
        trips = MR.limit_trips(rig_state("Y+", "X+", "X-"), PROFILE)
        self.assertEqual([(t.axis, t.end) for t in trips], [("X", "-"), ("X", "+"), ("Y", "+")])

    def test_a_homing_joint_on_its_home_switch_is_not_a_trip(self):
        """HOME_IGNORE_LIMITS: motion ignores a homing joint's flags
        (control.c:815-816); on the rig the home switch is the limit."""
        self.assertEqual(MR.limit_trips(rig_state("Z+", homing="Z"), PROFILE), ())
        # Another joint's switch is one, homing or not.
        self.assertEqual([t.axis for t in MR.limit_trips(rig_state("Z+", "X-", homing="Z"),
                                                          PROFILE)], ["X"])

    def test_nothing_while_the_status_is_unknown_or_not_read(self):
        self.assertEqual(MR.limit_trips(rig_state("X-").as_status_unknown(), PROFILE), ())
        self.assertEqual(MR.limit_trips(MockAdapter(PROFILE).snapshot(), PROFILE), ())
        unread = rig_state()._replace(hard_limits=(HardLimit(0, "X", None, None),))
        self.assertEqual(MR.limit_trips(unread, PROFILE), ())

    def test_an_end_the_profile_has_no_switch_for_is_named_by_axis_and_sign(self):
        """The rig has no Z bottom switch (profile.missing_switches); a flag
        there - another machine, a wiring fault - still names its end."""
        (trip,) = MR.limit_trips(rig_state("Z-"), PROFILE)
        self.assertEqual((trip.label, trip.hal_pin, trip.away), ("Z− ucu", "", +1))

    def test_unmasked_only_while_the_override_is_off(self):
        self.assertEqual(len(MR.unmasked_trips(rig_state("X-"), PROFILE)), 1)
        self.assertEqual(MR.unmasked_trips(rig_state("X-", override_limits=True), PROFILE), ())
        self.assertEqual(len(MR.unmasked_trips(rig_state("X-", override_limits=None), PROFILE)), 1)

    def test_toward_a_pressed_switch(self):
        trips = MR.limit_trips(rig_state("X-", "Z+"), PROFILE)
        self.assertEqual(MR.toward_pressed_switch(trips, "X", -1).label, "X sol uç")
        self.assertIsNone(MR.toward_pressed_switch(trips, "X", +1))
        self.assertEqual(MR.toward_pressed_switch(trips, "Z", +1).label, "Z üst")
        self.assertIsNone(MR.toward_pressed_switch(trips, "Z", -1))
        self.assertIsNone(MR.toward_pressed_switch(trips, "Y", -1))

    def test_away_direction(self):
        self.assertEqual((MR.away_direction("-"), MR.away_direction("+")), (1, -1))

    def test_the_windows_are_the_ini_soft_limits(self):
        self.assertEqual(dict(WINDOWS), {0: (3.0, 399.0), 1: (3.0, 387.0), 2: (-96.5, -3.0)})


class TestRecoveryPlan(unittest.TestCase):

    def test_none_without_a_pressed_switch(self):
        self.assertIsNone(plan_of(rig_state()))

    def test_unhomed_the_step_is_the_chosen_three_mm(self):
        plan = plan_of(rig_state("X-"))
        self.assertEqual((plan.trip.label, plan.step_mm, plan.refusal), ("X sol uç", 3.0, ""))
        self.assertEqual(MR.RECOVERY_STEP_MM, 3.0)
        self.assertEqual(MR.recovery_label(plan), "Limitten kurtar: X+ yönünde 3 mm")
        self.assertEqual(MR.recovery_label(plan_of(rig_state("Z+"))),
                         "Limitten kurtar: Z− yönünde 3 mm")

    def test_homed_and_inside_the_limits_three_mm(self):
        plan = plan_of(rig_state("X+", homed="XYZ", at=(55.0, 5.0, -5.0)))
        self.assertEqual(plan.step_mm, 3.0)

    def test_homed_outside_the_limit_the_step_lands_inside(self):
        """X's left switch trips 3.64 mm under MIN_LIMIT 3 (test-rig.ini):
        3.64 + 0.5 margin, up to the next 0.1 mm."""
        plan = plan_of(rig_state("X-", homed="XYZ", at=(-0.64, 5.0, -5.0)))
        self.assertEqual((plan.step_mm, plan.refusal), (4.2, ""))
        self.assertGreaterEqual(-0.64 + plan.step_mm, 3.0 + MR.RECOVERY_INSIDE_MARGIN_MM)
        # Z's top switch above MAX_LIMIT -3 (trip +0.52 machine).
        plan = plan_of(rig_state("Z+", homed="XYZ", at=(5.0, 5.0, 0.52)))
        self.assertEqual(plan.step_mm, 4.1)
        self.assertEqual(MR.recovery_label(plan), "Limitten kurtar: Z− yönünde 4.1 mm")

    def test_homed_too_far_out_for_one_step_is_refused(self):
        plan = plan_of(rig_state("X-", homed="XYZ", at=(-8.0, 5.0, -5.0)))
        self.assertIsNone(plan.step_mm)
        self.assertIn("11.0 mm dışında", plan.refusal)
        self.assertIn("en çok 10 mm", plan.refusal)
        self.assertEqual(MR.recovery_label(plan), "Limitten kurtar: X+ yönünde")

    def test_homed_a_target_past_the_far_limit_is_refused(self):
        """A min switch read pressed near the max end (a wiring fault, a moved
        switch): +3 mm would leave the window, which motion drops."""
        plan = plan_of(rig_state("X-", homed="XYZ", at=(397.0, 5.0, -5.0)))
        self.assertIsNone(plan.step_mm)
        self.assertIn("yumuşak limitlerin dışına çıkar (3…399 mm)", plan.refusal)

    def test_homed_without_a_window_is_refused(self):
        plan = MR.recovery_plan(rig_state("X-", homed="XYZ", at=(-1.0, 0, 0)), PROFILE, {})
        self.assertIn("yumuşak limitleri bilinmiyor", plan.refusal)

    def test_refusals_in_order(self):
        cases = (
            (rig_state("X-", link=rig_state().link._replace(io_error=True)), MR.LINK_FIRST),
            (rig_state("X-", link=rig_state().link._replace(watchdog_has_bit=True)), MR.LINK_FIRST),
            (rig_state("X-", estop=rig_state().estop._replace(drive_power=DrivePower.CUT)),
             MR.DRIVE_POWER_CUT),
            (rig_state("X-", task_mode="mdi", interp_state="reading"), MR.PROGRAM_BUSY),
            (rig_state("X-", program_running=True), MR.PROGRAM_BUSY),
            (rig_state("X-", homing="Z"), MR.HOMING_BUSY),
            (rig_state("X-", moving=True), MR.MOVING_BUSY),
        )
        for state, why in cases:
            with self.subTest(why=why):
                plan = plan_of(state)
                self.assertEqual((plan.step_mm, plan.refusal), (None, why))

    def test_both_switches_of_one_axis_is_a_fault_not_a_recovery(self):
        plan = plan_of(rig_state("X-", "X+"))
        self.assertIsNone(plan.step_mm)
        self.assertEqual(plan.refusal, "X ekseninde iki switch birden basılı — kablo ya da "
                                       "switch arızası; kurtarma yapılmadı.")

    def test_two_axes_the_first_is_left_the_rest_named(self):
        plan = plan_of(rig_state("Y-", "X-"))
        self.assertEqual((plan.trip.axis, [o.label for o in plan.others]), ("X", ["Y ön uç"]))
        self.assertIn("Ayrıca basılı: Y ön uç", MR.recovery_started_text(plan))


class TestTexts(unittest.TestCase):

    def test_one_trip_text_names_the_switch_and_the_way_off(self):
        self.assertEqual(M.limit_trip_text("X", ["X sol uç"]), TRIP_X_LEFT)
        self.assertEqual(M.limit_trip_text("X", []), "X limit switch'ine çarptı — makine durdu.")
        self.assertIn("AXIS", M.limit_trip_text("X", ["X sol uç"], MR.OBSERVE_RECOVER_HINT))

    def test_the_words_of_each_refusal(self):
        trips = MR.limit_trips(rig_state("X-", "Y-"), PROFILE)
        self.assertEqual(MR.on_refused_text(trips[:1]),
                         "X sol uç switch'i basılı — LinuxCNC bir eksen limitteyken makineyi açmaz. "
                         "«Limitten kurtar»a basın.")
        self.assertTrue(MR.on_refused_text(trips).startswith(
            "X sol uç ve Y ön uç switch'leri basılı — "))
        self.assertEqual(MR.home_refused_text(trips[:1]),
                         "X sol uç switch'i basılı — önce «Limitten kurtar»: referans alma "
                         "switch üstünden başlayamaz.")
        self.assertEqual(MR.jog_refused_text(trips[0]), "X sol uç basılı — yalnız X+.")
        self.assertEqual(MR.switch_pressed_text(trips[1]), "Y ön uç switch'i BASILI")
        self.assertEqual(MR.released_text("X sol uç", homed=False),
                         "X sol uç serbest; limit koruması yeniden etkin. Referans alın.")
        self.assertEqual(MR.released_text("X sağ uç", homed=True),
                         "X sağ uç serbest; limit koruması yeniden etkin.")
        self.assertEqual(MR.still_on_text("Z"), "Z hâlâ switch'te — «Limitten kurtar»a tekrar basın.")
        self.assertEqual(MR.recovery_started_text(plan_of(rig_state("X-"))),
                         "X switch'ten çıkarılıyor: +3 mm. Limit koruması bu hareket bitince "
                         "kendiliğinden geri gelir.")


# ---------------------------------------------------------------------------
# The fake's model of what motion does with the switches
# ---------------------------------------------------------------------------


class TestFakeMotionWithSwitches(unittest.TestCase):
    """fake_linuxcnc's model (each rule cited there, tied to the source by
    test_linuxcnc_adapter.TestOverrideFidelity)."""

    def setUp(self):
        cm = F.installed()
        self.lc, _, self.m, _ = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.cmd = self.lc.command()

    def test_on_onto_a_pressed_switch_trips_at_once(self):
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = 1
        self.cmd.state(C["STATE_ON"])
        self.assertEqual(m.task_state, C["STATE_ESTOP_RESET"])
        self.assertEqual(list(m.errors), [(C["OPERATOR_ERROR"], "joint 0 on limit switch error")])

    def test_a_switch_pressed_while_on_trips_at_the_next_poll(self):
        m = self.m
        m.power_on()
        m.hard_max[2] = 1
        s = self.lc.stat()
        s.poll()
        self.assertEqual(s.task_state, C["STATE_ESTOP_RESET"])
        self.assertEqual(list(m.errors), [(C["OPERATOR_ERROR"], "joint 2 on limit switch error")])

    def test_not_a_homing_joint(self):
        m = self.m
        m.power_on()
        m.homing[2], m.hard_max[2] = 1, 1
        self.lc.stat().poll()
        self.assertEqual(m.task_state, C["STATE_ON"])
        m.homing[2] = 0                       # homing cut short, the joint on its switch
        self.lc.stat().poll()
        self.assertEqual(m.task_state, C["STATE_ESTOP_RESET"])

    def test_the_override_masks_the_sides_pressed_then_and_on_holds(self):
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = m.hard_max[2] = 1
        self.cmd.override_limits()
        self.assertEqual(m.override_mask, 0b10_0001)
        self.cmd.state(C["STATE_ON"])
        self.assertEqual(m.task_state, C["STATE_ON"])
        m.hard_max[1] = 1                     # a switch pressed since: not masked
        self.lc.stat().poll()
        self.assertEqual(m.task_state, C["STATE_ESTOP_RESET"])

    def test_out_of_on_only_manual_takes_it_and_another_mode_drops_it_silently(self):
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = 1
        m.task_mode = C["MODE_AUTO"]
        self.cmd.override_limits()
        self.assertEqual((m.override_mask, list(m.errors)), (0, []))
        self.assertEqual(self.cmd.wait_complete(), C["RCS_DONE"])

    def test_on_in_mdi_mode_it_is_refused(self):
        m = self.m
        m.power_on(homed=True)
        m.task_mode, m.motion_mode = C["MODE_MDI"], C["TRAJ_MODE_COORD"]
        self.cmd.override_limits()
        self.assertEqual(m.override_mask, 0)
        self.assertEqual(list(m.errors), [(C["OPERATOR_ERROR"],
                                           "can't do that (EMC_JOINT_OVERRIDE_LIMITS:129) in MDI mode")])

    def test_a_joint_jog_onto_the_switch_is_refused_mask_or_not(self):
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = 1
        self.cmd.override_limits()
        self.cmd.state(C["STATE_ON"])
        self.cmd.jog(JOG, 1, 0, -JOG_VEL, 1.0)
        self.assertEqual(list(m.errors)[-1],
                         (C["OPERATOR_ERROR"], "Can't jog joint 0 further past min hard limit."))
        self.assertEqual(m.override_mask, 1)      # a refused jog does not end the override
        self.cmd.jog(JOG, 1, 0, JOG_VEL, 3.0)
        self.assertEqual(m.joint_pos[0], 3.0)
        self.assertEqual(m.override_mask, 0)      # the jog ended, and the override with it

    def test_both_sides_masked_motion_skips_its_direction_check(self):
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = m.hard_max[0] = 1
        self.cmd.override_limits()
        self.cmd.state(C["STATE_ON"])
        self.cmd.jog(JOG, 1, 0, -JOG_VEL, 1.0)
        self.assertEqual(m.joint_pos[0], -1.0)

    def test_a_teleop_jog_is_not_checked_against_the_switches(self):
        m = self.m
        m.estop_reset_state()
        m.homed[:3] = [1, 1, 1]
        m.motion_mode = C["TRAJ_MODE_TELEOP"]
        m.hard_max[0] = 1
        self.cmd.override_limits()
        self.cmd.state(C["STATE_ON"])
        self.cmd.jog(JOG, 0, 0, JOG_VEL, 1.0)          # onto the switch
        self.assertEqual((m.axis_pos[0], list(m.errors)), (1.0, []))

    def test_entering_coordinated_mode_clears_the_mask(self):
        m = self.m
        m.estop_reset_state()
        m.homed[:3] = [1, 1, 1]
        m.hard_max[0] = 1
        self.cmd.override_limits()
        self.cmd.state(C["STATE_ON"])
        self.cmd.mode(C["MODE_MDI"])
        self.assertEqual(m.override_mask, 0)
        self.lc.stat().poll()                 # and the axis still on its switch trips
        self.assertEqual(m.task_state, C["STATE_ESTOP_RESET"])

    def test_the_binding_takes_no_arguments(self):
        with self.assertRaises(TypeError):
            self.cmd.override_limits(0)
        self.assertEqual(self.m.log, [])

    def test_a_jog_an_estop_cut_drops_the_mask_at_the_next_enable(self):
        """Review of WP05, seen in sim/: motion's `overriding` (a jog ran
        under the mask) survives the disable - no end-of-jog check runs
        while disabled (control.c:1463-1477) - so the status still shows
        the override, but the planner drops the mask at the first servo
        cycle after enabling (control.c:1278-1283) and ON trips on the
        switch. A new override clears `overriding` (command.c:700): ON
        holds."""
        m = self.m
        m.estop_reset_state()
        m.hard_min[0] = 1
        self.cmd.override_limits()
        m.overriding = True                   # the recovery jog the estop cut
        s = self.lc.stat()
        s.poll()
        self.assertTrue(s.joint[0]["override_limits"])   # still on in the status
        self.cmd.state(C["STATE_ON"])
        self.assertEqual((m.task_state, m.override_mask), (C["STATE_ESTOP_RESET"], 0))
        self.assertEqual(list(m.errors), [(C["OPERATOR_ERROR"], "joint 0 on limit switch error")])
        m.errors.clear()
        self.cmd.override_limits()
        self.assertFalse(m.overriding)
        self.cmd.state(C["STATE_ON"])
        self.assertEqual((m.task_state, m.override_mask), (C["STATE_ON"], 1))
        self.assertEqual(list(m.errors), [])


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake
# ---------------------------------------------------------------------------


class _Case(T.FakeCase):
    """FakeCase with the rig's switch inputs among the HAL pins, and the
    switches pressed the way the rig presses them: motion's flag (the fake)
    and the input pin (HAL) together."""

    def make(self, *args, **kw):
        if kw.get("hal_pins") is None:
            kw["hal_pins"] = dict(T.ALL_LINK_PINS, **{pin: False for pin in PIN.values()})
        return super().make(*args, **kw)

    def press(self, joint: int, end: str) -> None:
        (self.machine.hard_min if end == "-" else self.machine.hard_max)[joint] = 1
        if (joint, end) in PIN:
            self.hal.pins[PIN[(joint, end)]] = True

    def release(self, joint: int, end: str) -> None:
        (self.machine.hard_min if end == "-" else self.machine.hard_max)[joint] = 0
        if (joint, end) in PIN:
            self.hal.pins[PIN[(joint, end)]] = False

    def tripped(self, joint: int, end: str, homed: bool = False, **kw) -> A.LinuxCncAdapter:
        """The machine ON, then the axis onto its switch: motion stops the
        machine at the next poll, which the snapshot takes. The log is
        cleared after."""
        adapter = self.on(homed=homed, **kw)
        adapter.snapshot()
        self.press(joint, end)
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled, "premise: the switch stopped the machine")
        self.machine.log.clear()
        self.machine.log_channel.clear()
        return adapter

    def sent(self) -> List[tuple]:
        return self.machine.calls_except("wait_complete")


class TestRecoverLimitSequence(_Case):

    def test_unhomed_x_left_exactly_override_on_and_one_jog_away(self):
        """Plan WP05 test (1): ESTOP_RESET and MANUAL after the trip."""
        adapter = self.tripped(0, "-")
        self.assertEqual((self.machine.task_state, self.machine.task_mode),
                         (C["STATE_ESTOP_RESET"], C["MODE_MANUAL"]))
        adapter.recover_limit()
        self.assertEqual(self.sent(), [
            ("override_limits", ()), ("state", (C["STATE_ON"],)), ("jog", (JOG, 1, 0, JOG_VEL, 3.0)),
        ])
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertEqual(self.machine.joint_pos[0], 3.0)
        self.assertEqual(adapter._message, "X switch'ten çıkarılıyor: +3 mm. Limit koruması bu "
                                           "hareket bitince kendiliğinden geri gelir.")
        self.assertNoLinuxCncErrors()

    def test_every_switch_the_way_off_it(self):
        for (joint, end), label in sorted(LABEL.items()):
            with self.subTest(switch=label):
                adapter = self.tripped(joint, end)
                adapter.recover_limit()
                jog = [args for name, args in self.machine.log if name == "jog"]
                away = 1 if end == "-" else -1
                self.assertEqual(jog, [(JOG, 1, joint, away * JOG_VEL, 3.0)])
                self.assertNoLinuxCncErrors()

    def test_from_auto_mode_manual_comes_first(self):
        """Test (2): out of ON task takes the override in MANUAL alone, and
        drops it silently in any other mode (emctaskmain.cc:846-852)."""
        adapter = self.tripped(0, "-")
        self.machine.task_mode, self.machine.motion_mode = C["MODE_AUTO"], C["TRAJ_MODE_COORD"]
        adapter.recover_limit()
        self.assertEqual(self.sent()[:2], [("mode", (C["MODE_MANUAL"],)), ("override_limits", ())])
        self.assertEqual(self.sent()[-1][0], "jog")
        self.assertNoLinuxCncErrors()

    def test_manual_not_reached_sends_no_override(self):
        adapter = self.tripped(0, "-")
        self.machine.task_mode = C["MODE_AUTO"]
        self.machine.jogging_active = True        # task ignores the mode change
        adapter.recover_limit()
        self.assertNotIn("override_limits", self.machine.calls())
        self.assertIn("MANUAL moduna geçilemedi", adapter._message)

    def test_an_override_not_taken_sends_no_on(self):
        """Test (3)."""
        adapter = self.tripped(0, "-")
        self.machine.issue_override_limits = lambda: None   # accepted, mask never set
        adapter.recover_limit()
        self.assertEqual(self.sent(), [("override_limits", ())])
        self.assertEqual(adapter._message, "Limit aşma kabul edilmedi — kurtarma yapılmadı.")
        self.assertNotEqual(self.machine.task_state, C["STATE_ON"])

    def test_on_not_holding_sends_no_jog(self):
        """Test (4): a switch pressed after the override - not in the mask -
        stops the machine at its ON."""
        adapter = self.tripped(0, "-")
        override = self.machine.issue_override_limits

        def override_then_y_rear():
            override()
            self.press(1, "+")
        self.machine.issue_override_limits = override_then_y_rear
        adapter.recover_limit()
        self.assertEqual(self.sent(), [("override_limits", ()), ("state", (C["STATE_ON"],))])
        self.assertTrue(adapter._message.startswith("Makine açılamadı — kurtarma yapılmadı."))
        self.assertIn(MR.OVERRIDE_LEFT_ON, adapter._message)
        # LinuxCNC's reason went to the box first.
        self.assertIn("joint 1 on limit switch error", [a.text for a in adapter._alarms])

    def test_on_not_holding_for_the_latch(self):
        adapter = self.tripped(0, "-")
        self.machine.estop_reasserts_on_power_on = True
        adapter.recover_limit()
        self.assertNotIn("jog", self.machine.calls())

    def test_from_estop_the_reset_comes_first(self):
        adapter = self.tripped(0, "-")
        self.machine.aux_estop, self.machine.latch_ok = 1, False   # a bite opened the latch, gone since
        adapter.recover_limit()
        self.assertEqual([n for n, _ in self.sent()],
                         ["state", "override_limits", "state", "jog"])
        self.assertEqual(self.sent()[0], ("state", (C["STATE_ESTOP_RESET"],)))
        self.assertNoLinuxCncErrors()

    def test_estop_not_left_sends_nothing_more(self):
        adapter = self.tripped(0, "-")
        self.machine.aux_estop, self.machine.latch_ok = 1, False
        self.machine.fault_in = True              # the chain still open
        adapter.recover_limit()
        self.assertEqual(self.sent(), [("state", (C["STATE_ESTOP_RESET"],))])
        self.assertEqual(adapter._message, "LinuxCNC acil stoptan çıkmadı — kurtarma yapılmadı.")

    def test_homed_in_teleop_free_mode_first_then_a_step_into_the_limits(self):
        """Test (5): homed, X under its soft minimum on its left switch."""
        adapter = self.make()
        self.machine.estop_reset_state()
        self.machine.homed[:3] = [1, 1, 1]
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = -0.64
        self.press(0, "-")
        adapter.recover_limit()
        self.assertEqual(self.sent(), [
            ("override_limits", ()), ("state", (C["STATE_ON"],)), ("teleop_enable", (0,)),
            ("jog", (JOG, 1, 0, JOG_VEL, 4.2)),
        ])
        self.assertNoLinuxCncErrors()

    def test_homed_and_too_far_out_sends_nothing(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        self.machine.homed[:3] = [1, 1, 1]
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = -12.0
        self.press(0, "-")
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertIn("en çok 10 mm", adapter._message)

    def test_pressed_again_with_the_override_on_and_on_it_only_jogs(self):
        """A press whose jog never went out: idempotent, from step 6."""
        adapter = self.tripped(0, "-")
        self.machine.issue_override_limits()      # an earlier press got this far
        self.machine.issue_set_state(C["STATE_ON"])
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        adapter.recover_limit()
        self.assertEqual(self.sent(), [("jog", (JOG, 1, 0, JOG_VEL, 3.0))])

    def test_after_an_estop_cut_its_jog_one_press_recovers(self):
        """Review of WP05, seen in sim/: the estop that cuts a recovery jog
        with the axis still on its switch leaves the override on in the
        status and motion's `overriding` set, so an ON would drop the mask
        and trip at once (a wasted press, LinuxCNC's limit error again).
        The next press issues the override again, which clears `overriding`
        (command.c:700): override, ON, one jog - no trip."""
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        # The fake's jog ends at once: put back what motion keeps of a jog
        # the estop cut with X still on the switch.
        self.machine.issue_override_limits()
        self.machine.overriding = True
        adapter.software_estop()
        self.assertTrue(adapter.snapshot().override_limits, "premise: the status says on")
        self.machine.log.clear()
        self.machine.log_channel.clear()
        before = len(adapter._alarms)
        adapter.recover_limit()
        self.assertEqual(self.sent(), [
            ("override_limits", ()), ("state", (C["STATE_ON"],)), ("jog", (JOG, 1, 0, JOG_VEL, 3.0)),
        ])
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        new = [a.text for a in adapter._alarms[:len(adapter._alarms) - before]]
        self.assertFalse([t for t in new if "on limit switch error" in t], new)
        self.assertTrue(adapter._message.startswith("X switch'ten çıkarılıyor"), adapter._message)

    def test_two_axes_the_first_now_the_other_named(self):
        adapter = self.tripped(0, "-")
        self.press(1, "-")
        adapter.recover_limit()
        self.assertEqual([args for name, args in self.machine.log if name == "jog"],
                         [(JOG, 1, 0, JOG_VEL, 3.0)])
        self.assertIn("Ayrıca basılı: Y ön uç", adapter._message)
        self.assertEqual(self.machine.override_mask, 0)     # the X jog ended the override
        snap = adapter.snapshot()                  # Y, still on its switch, trips again
        self.assertTrue(snap.estop.software_disabled)

    def test_nothing_pressed_sends_nothing(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, MR.NO_TRIP)
        self.assertIs(adapter._message_severity, Severity.WARN)

    def test_board_link_lost_sends_nothing_and_no_halcmd(self):
        adapter = self.tripped(0, "-")
        self.bite(io_error=True)
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, MR.LINK_FIRST)
        self.assertEqual(self.halcmd.calls, [])

    def test_both_switches_of_x_sends_nothing(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        self.press(0, "-")
        self.press(0, "+")
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertIn("iki switch birden basılı", adapter._message)

    def test_while_a_program_or_mdi_runs_sends_nothing(self):
        adapter = self.tripped(0, "-")
        self.run_mdi_program()
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, MR.PROGRAM_BUSY)

    def test_without_a_jog_speed_sends_nothing(self):
        adapter = self.tripped(0, "-")
        adapter._jog_velocity = None
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertIn("kurtarma yapılmadı", adapter._message)

    def test_the_switch_letting_go_meanwhile_sends_no_jog(self):
        adapter = self.tripped(0, "-")
        on = self.machine.issue_set_state

        def on_and_release(state):
            on(state)
            self.release(0, "-")
        self.machine.issue_set_state = on_and_release
        adapter.recover_limit()
        self.assertNotIn("jog", self.machine.calls())
        self.assertTrue(adapter._message.startswith("Switch serbest kaldı"))

    def test_the_link_lost_after_the_on_sends_no_jog(self):
        """Step 7 refuses with the protection already off: the refusal
        says so (the header badge too)."""
        adapter = self.tripped(0, "-")
        on = self.machine.issue_set_state

        def on_then_link_lost(state):
            on(state)
            self.hal.pins[LinkHealth.HAL_IO_ERROR] = True
        self.machine.issue_set_state = on_then_link_lost
        adapter.recover_limit()
        self.assertNotIn("jog", self.machine.calls())
        self.assertEqual(adapter._message, MR.LINK_FIRST + MR.OVERRIDE_LEFT_ON)

    def test_teleop_not_left_sends_no_jog(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        self.machine.homed[:3] = [1, 1, 1]
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.press(0, "+")
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 55.0
        teleop = self.machine.issue_teleop
        self.machine.issue_teleop = lambda enable: None   # motion ignores it
        adapter.recover_limit()
        self.machine.issue_teleop = teleop
        self.assertNotIn("jog", self.machine.calls())
        self.assertIn("Eklem moduna geçilemedi", adapter._message)

    def test_switching_on_ends_the_ui_s_own_stop(self):
        """As software_estop_reset does: the stop this UI sent is over once
        the recovery switched the machine on, so the next stop is reported
        (_note_task_state), not taken for the UI's own."""
        adapter = self.tripped(0, "-")
        adapter.software_estop()                  # pressed, no snapshot since
        adapter.recover_limit()
        self.release(0, "-")
        self.assertFalse(adapter.snapshot().estop.software_disabled)
        self.clock.now += A.FOLLOW_UP_WINDOW_S + 1.0  # the trip's own error long past
        self.machine.traj_enabled = False         # switched off elsewhere
        snap = adapter.snapshot()
        self.assertIn("Makine kapatıldı — komut bu ekrandan gelmedi", snap.alarms[0].text)
        self.assertTrue(snap.last_error.text.startswith("Makine kapatıldı — komut bu ekrandan gelmedi"),
                        snap.last_error)

    def test_the_status_lost_is_a_refusal_not_an_exception(self):
        adapter = self.tripped(0, "-")
        self.machine.channels_valid = False
        self.assertIsNone(adapter.recover_limit())
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, A.NO_LINUXCNC_REFUSAL)


class TestAStopDuringTheRecovery(_Case):
    """Test (13): DURDUR or the software estop from another thread, without
    the session lock, while recover_limit waits: nothing switched on or
    moved after it (_stopped_since before the override, the ON and the
    jog)."""

    def recover_with(self, overtaker: str, at_wait: int) -> A.LinuxCncAdapter:
        adapter = self.tripped(0, "-")
        waits = {"n": 0}
        wait = adapter._wait

        def wait_then_stop():
            waits["n"] += 1
            if waits["n"] == at_wait:
                getattr(adapter, overtaker)()
            return wait()
        adapter._wait = wait_then_stop
        adapter.recover_limit()
        return adapter

    def test_after_the_override_no_on_and_no_jog(self):
        for overtaker, why in (("stop_motion", A.STOP_REFUSAL), ("software_estop", A.ESTOP_REFUSAL)):
            with self.subTest(overtaker=overtaker):
                adapter = self.recover_with(overtaker, at_wait=1)
                self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)
                self.assertNotIn("jog", self.machine.calls())
                self.assertTrue(adapter._message.startswith(why))
                self.assertIn(MR.OVERRIDE_LEFT_ON, adapter._message)

    def test_after_the_on_no_jog(self):
        for overtaker in ("stop_motion", "software_estop"):
            with self.subTest(overtaker=overtaker):
                adapter = self.recover_with(overtaker, at_wait=2)
                self.assertIn(("state", (C["STATE_ON"],)), self.machine.log)
                self.assertNotIn("jog", self.machine.calls())

    def test_before_the_estop_reset_nothing_at_all(self):
        """A stop between the plan and the first send: not even the
        ESTOP_RESET goes out."""
        adapter = self.tripped(0, "-")
        self.machine.aux_estop, self.machine.latch_ok = 1, False
        read_hal = adapter._read_hal

        def read_hal_then_stop():
            adapter.stop_motion()
            return read_hal()
        adapter._read_hal = read_hal_then_stop
        adapter.recover_limit()
        self.assertEqual([(n, a) for (n, a), ch in zip(self.machine.log, self.machine.log_channel)
                          if ch == 0 and n != "wait_complete"], [])
        self.assertEqual(adapter._message, A.STOP_REFUSAL)

    def test_during_the_estop_reset_wait_nothing_at_all(self):
        adapter = self.make(machine=F.FakeMachine(estop_clear_after_polls=4))
        self.machine.hard_min[0] = 1
        self.hal.pins[PIN[(0, "-")]] = True
        fired = []

        def sleep(seconds):
            if not fired:
                fired.append(1)
                adapter.software_estop()
            self.clock.sleep(seconds)
        adapter._sleep = sleep
        adapter.recover_limit()
        self.assertTrue(fired, "premise: the reset waited for the latch")
        self.assertNotIn("override_limits", self.machine.calls())
        self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)

    def test_premise_without_a_stop_it_jogs(self):
        self.recover_with("snapshot", at_wait=1)
        self.assertIn("jog", self.machine.calls())


class TestResetAndHomeAllOnASwitch(_Case):

    def test_reset_sends_no_on_and_names_the_switch(self):
        """Test (6), from ESTOP_RESET (the rig after a trip): nothing sent."""
        adapter = self.tripped(0, "-")
        adapter.software_estop_reset()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, "X sol uç switch'i basılı — LinuxCNC bir eksen "
                                           "limitteyken makineyi açmaz. «Limitten kurtar»a basın.")
        self.assertIs(adapter._message_severity, Severity.WARN)
        self.assertNoLinuxCncErrors()

    def test_from_estop_the_reset_but_no_on(self):
        adapter = self.make()
        self.press(2, "+")
        adapter.software_estop_reset()
        self.assertEqual(self.sent(), [("state", (C["STATE_ESTOP_RESET"],))])
        self.assertIn("Z üst switch'i basılı", adapter._message)

    def test_with_the_override_on_the_reset_still_sends_no_on(self):
        """Review of WP05: the status cannot say whether the mask outlives
        the enable. After an estop cut a recovery jog it does not (motion's
        `overriding`, control.c:1278-1283), and the reset's ON tripped at
        once in sim/. So no ON while a switch reads pressed, overridden or
        not: «Limitten kurtar» issues the override again itself."""
        for cut_jog in (False, True):
            with self.subTest(cut_jog=cut_jog):
                adapter = self.tripped(0, "-")
                self.machine.issue_override_limits()
                self.machine.overriding = cut_jog
                adapter.software_estop_reset()
                self.assertEqual(self.sent(), [])
                self.assertEqual(adapter._message, "X sol uç switch'i basılı — LinuxCNC bir eksen "
                                                   "limitteyken makineyi açmaz. «Limitten kurtar»a basın.")
                self.assertNotEqual(self.machine.task_state, C["STATE_ON"])
                # One press of «Limitten kurtar» does it, either way.
                adapter.recover_limit()
                self.assertEqual([n for n, _ in self.sent()], ["override_limits", "state", "jog"])
                self.assertEqual(self.machine.task_state, C["STATE_ON"])
                self.assertNoLinuxCncErrors()

    def test_premise_an_on_after_a_cut_jog_trips(self):
        adapter = self.tripped(0, "-")
        self.machine.issue_override_limits()
        self.machine.overriding = True
        self.machine.issue_set_state(C["STATE_ON"])
        self.assertEqual(self.machine.task_state, C["STATE_ESTOP_RESET"])
        self.assertTrue(adapter.snapshot().estop.software_disabled)

    def test_after_the_link_comes_back_the_switch_still_refuses_on(self):
        adapter = self.tripped(0, "-")
        self.bite()
        adapter.software_estop_reset()
        self.assertNotIn(("state", (C["STATE_ON"],)), self.machine.log)
        self.assertIn("unhome", self.machine.calls())
        self.assertTrue(adapter._message.startswith(
            "Kart bağlantısı yeniden kuruldu, referanslar silindi. X sol uç switch'i basılı"))

    def test_premise_without_a_switch_the_reset_switches_on(self):
        adapter = self.make()
        adapter.software_estop_reset()
        self.assertEqual(self.machine.task_state, C["STATE_ON"])

    def test_home_all_sends_no_home(self):
        """Test (7): refused first, with the machine off or on."""
        adapter = self.tripped(1, "-")
        adapter.home_all()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, "Y ön uç switch'i basılı — önce «Limitten kurtar»: "
                                           "referans alma switch üstünden başlayamaz.")
        self.machine.issue_override_limits()
        self.machine.issue_set_state(C["STATE_ON"])
        adapter.home_all()
        self.assertNotIn("home", self.machine.calls())

    def test_home_all_with_the_link_lost_says_the_machine_is_off(self):
        adapter = self.tripped(1, "-")
        self.bite(io_error=True)
        adapter.home_all()
        self.assertEqual(adapter._message, "Makine devre dışı — referans alınamaz.")


class TestJogOntoAPressedSwitch(_Case):
    """Test (8): in the override window - ON, a switch pressed and masked -
    a jog onto it is refused in every mode, a jog away goes."""

    def window(self, joint: int, end: str, homed: bool) -> A.LinuxCncAdapter:
        adapter = self.make()
        self.machine.estop_reset_state()
        if homed:
            self.machine.homed[:3] = [1, 1, 1]
            self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
            # Inside the soft limits, which a homed jog is checked against
            # (plan WP06): the jog away must not be cut or refused for them.
            self.machine.axis_pos[:3] = self.machine.joint_pos[:3] = [398.0, 386.0, -4.0]
        self.press(joint, end)
        self.machine.issue_override_limits()
        self.machine.issue_set_state(C["STATE_ON"])
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        return adapter

    def test_teleop_onto_refused_away_sent(self):
        adapter = self.window(0, "+", homed=True)
        adapter.jog("X", 1)
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, "X sağ uç basılı — yalnız X−.")
        adapter.jog("X", -1)
        self.assertEqual(self.sent(), [("jog", (JOG, 0, 0, -JOG_VEL, 1.0))])   # an axis jog
        self.assertNoLinuxCncErrors()

    def test_free_mode_onto_refused_away_sent(self):
        adapter = self.window(2, "+", homed=False)
        adapter.jog("Z", 1)
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, "Z üst basılı — yalnız Z−.")
        adapter.jog("Z", -1)
        self.assertEqual(self.sent(), [("jog", (JOG, 1, 2, -JOG_VEL, 1.0))])
        self.assertNoLinuxCncErrors()

    def test_another_axis_jogs_freely(self):
        adapter = self.window(0, "-", homed=False)
        adapter.jog("Y", -1)
        self.assertEqual([n for n, _ in self.sent()], ["jog"])


class TestHomingSwitchIsNotATrip(_Case):
    """Test (9): the joint homing on its own switch (HOME_IGNORE_LIMITS)."""

    def test_no_card_no_recovery_no_trip_text(self):
        adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
        adapter.home_all()
        self.press(2, "+")                       # Z reaches its top switch
        snap = adapter.snapshot()
        self.assertEqual(self.machine.task_state, C["STATE_ON"])
        self.assertTrue(snap.homing.any_homing)
        vm = VM.build(PROFILE, CONFIG, snap)
        self.assertIsNone(vm["kontrol"]["limit"])
        # Plan WP07: while a joint homes, the homing card's note says what
        # DURDUR can leave - naming «Limitten kurtar» as the way off then.
        # A note for later, not an offer: nothing else names it.
        markup = R.render_body(vm)
        note = htmllib.escape(MR.HOME_STOP_NOTE, quote=True)
        self.assertEqual(markup.count(note), 1)
        self.assertNotIn("Limitten kurtar", markup.replace(note, ""))
        self.assertNotIn('data-action="recover_limit"', markup)
        self.machine.log.clear()
        adapter.recover_limit()
        self.assertEqual(self.sent(), [])
        self.assertEqual(adapter._message, MR.NO_TRIP)


class TestTheTripIsSaidAsOne(_Case):
    """Test (10): the trip's text names the switch, in the box and the log,
    never "komut bu ekrandan gelmedi"; LinuxCNC's report and the stop the
    snapshot sees give one box."""

    def test_the_box_and_the_log(self):
        adapter = self.on()
        adapter.snapshot()
        self.press(0, "-")
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, TRIP_X_LEFT)
        self.assertEqual(snap.last_error.raw, "joint 0 on limit switch error")   # LinuxCNC's, kept
        texts = [a.text for a in snap.alarms]
        self.assertIn(TRIP_X_LEFT, texts)
        self.assertFalse(any("komut bu ekrandan gelmedi" in t for t in texts), texts)
        self.assertEqual(snap.message, TRIP_X_LEFT)

    def test_the_report_drained_a_snapshot_earlier_keeps_its_box(self):
        adapter = self.on()
        adapter.snapshot()
        self.hal.pins[PIN[(0, "-")]] = True
        self.machine.operator_error("joint 0 on limit switch error")
        boxed = adapter.snapshot().last_error            # still ON in this stat
        self.assertEqual(boxed.text, TRIP_X_LEFT)
        self.clock.now += 0.5
        self.machine.hard_min[0] = 1                     # the flag, the stop
        self.machine.trip_on_limits()
        self.machine.errors.clear()                      # reported once already
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error, boxed)         # same box, same id
        self.assertNotIn("komut bu ekrandan gelmedi", " ".join(a.text for a in snap.alarms))

    def test_without_the_report_or_its_label_the_stop_says_it(self):
        """No hal module: the drain's translation cannot name the switch;
        motion's flags can."""
        adapter = self.on(hal_available=False)
        adapter.snapshot()
        self.machine.hard_min[0] = 1
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, TRIP_X_LEFT)
        self.assertEqual(snap.last_error.source, A.SOURCE_UI)

    def test_in_observer_mode_the_way_off_is_axis(self):
        """An observer sends nothing but the estop: the way off is AXIS's.
        (FakeCase.make builds a normal adapter; the flag is what counts.)"""
        adapter = self.make()
        adapter._observe = True
        self.machine.power_on()
        adapter.snapshot()
        self.press(0, "-")
        snap = adapter.snapshot()
        self.assertIn("AXIS", snap.last_error.text)
        self.assertNotIn("Limitten kurtar", snap.last_error.text)

    def test_a_stop_with_no_switch_is_still_the_generic_one(self):
        adapter = self.on()
        adapter.snapshot()
        self.machine.traj_enabled = False
        snap = adapter.snapshot()
        self.assertTrue(snap.last_error.text.startswith("Makine kapatıldı — komut bu ekrandan gelmedi"))

    def test_a_link_loss_is_said_as_one_whatever_the_flags(self):
        adapter = self.on()
        adapter.snapshot()
        self.press(0, "-")
        self.bite()
        snap = adapter.snapshot()
        self.assertTrue(snap.last_error.text.startswith(A.LINK_LOST), snap.last_error)


class TestRecoveryFollowUp(_Case):
    """Test (11): the snapshot that finds the override over says how it went."""

    def test_freed_unhomed_says_free_and_home(self):
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        self.release(0, "-")                     # the jog took X off the switch
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "X sol uç serbest; limit koruması yeniden etkin. Referans alın.")
        self.assertIs(snap.message_severity, Severity.INFO)
        self.assertFalse(snap.estop.software_disabled)
        self.assertIsNone(adapter._recovery)
        self.assertEqual(adapter.snapshot().message, snap.message)   # said once

    def test_freed_homed_does_not_ask_for_homing(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        self.machine.homed[:3] = [1, 1, 1]
        self.machine.motion_mode = C["TRAJ_MODE_TELEOP"]
        self.machine.axis_pos[0] = self.machine.joint_pos[0] = 55.0
        self.press(0, "+")
        adapter.recover_limit()
        self.release(0, "+")
        self.assertEqual(adapter.snapshot().message, "X sağ uç serbest; limit koruması yeniden etkin.")

    def test_still_on_the_switch_says_press_again(self):
        adapter = self.tripped(0, "-")
        adapter.recover_limit()                   # the jog ends with X still on it
        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertEqual(snap.last_error.text, "X hâlâ switch'te — «Limitten kurtar»a tekrar basın.")
        self.assertIn("Limitten kurtarma yetmedi: X hâlâ switch'te — «Limitten kurtar»a tekrar basın.",
                      [a.text for a in snap.alarms])

    def test_the_instant_before_motion_trips_decides_nothing(self):
        """Override over, the switch still pressed, the machine still ON:
        a snapshot taken before motion's next servo cycle trips it. The
        following waits for the next."""
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        trip = self.machine.trip_on_limits
        self.machine.trip_on_limits = lambda: None     # that servo cycle not yet
        snap = adapter.snapshot()
        self.assertFalse(snap.estop.software_disabled)
        self.assertIsNotNone(adapter._recovery)
        self.assertTrue(snap.message.startswith("X switch'ten çıkarılıyor"))
        self.machine.trip_on_limits = trip
        self.assertEqual(adapter.snapshot().last_error.text, MR.still_on_text("X"))

    def test_waits_while_the_override_lasts(self):
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        self.machine.override_mask = 1            # the jog still running
        self.assertEqual(adapter.snapshot().message.split(":")[0], "X switch'ten çıkarılıyor")
        self.assertIsNotNone(adapter._recovery)

    def test_an_estop_that_cut_the_jog_says_its_own_word(self):
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        self.release(0, "-")
        adapter.software_estop()
        snap = adapter.snapshot()
        self.assertEqual(snap.last_error.text, A.ESTOP_SENT)
        self.assertNotIn("serbest", snap.message)
        self.assertIsNone(adapter._recovery)

    def test_a_lost_link_ends_it_unsaid(self):
        adapter = self.tripped(0, "-")
        adapter.recover_limit()
        self.bite()
        adapter.snapshot()
        self.assertIsNone(adapter._recovery)


class TestObserverMode(_Case):
    """Test (12): Session drops it (test_observe_and_live_hal.ACTIONS); the
    adapter refuses it itself."""

    def test_the_adapter_refuses_it_and_sends_nothing(self):
        adapter = self.tripped(0, "-")
        adapter._observe = True
        self.machine.log.clear()
        adapter.recover_limit()
        self.assertEqual(self.machine.log, [])
        self.assertEqual(adapter._message, A.OBSERVE_REFUSAL)
        self.assertEqual(self.halcmd.calls, [])

    def test_the_session_drops_it(self):
        calls = []

        class Recorder:
            def __getattr__(self, name):
                return lambda *args: calls.append(name)
        sess = app.Session(Recorder(), PROFILE, CONFIG, observe=True)
        sess.act("recover_limit", "")
        self.assertEqual(calls, [])
        app.Session(Recorder(), PROFILE, CONFIG).act("recover_limit", "")
        self.assertEqual(calls, ["recover_limit"])


class TestLatchPhaseStop(_Case):
    """Batch-1 finding (WP03): DURDUR during a homing's final latch phase
    leaves the joint on its home switch, which on the rig is also its limit
    switch (X−, Y−, Z+): abort cancels the homing (command.c:509-520), the
    joint is unhomed and no longer homing, and motion trips on it
    (control.c:807-826). The card must appear and the recovery work."""

    def test_each_home_switch(self):
        for joint, end in ((0, "-"), (1, "-"), (2, "+")):
            with self.subTest(switch=LABEL[(joint, end)]):
                adapter = self.on(homed=False, machine=F.FakeMachine(home_instantly=False))
                adapter.home_all()
                while not self.machine.homing[joint]:   # Z, then Y, then X
                    self.machine.advance_homing()
                self.press(joint, end)           # the joint latching on its switch
                self.assertFalse(adapter.snapshot().estop.software_disabled)
                adapter.stop_motion()
                adapter.stop_followup()
                snap = adapter.snapshot()
                self.assertTrue(snap.estop.software_disabled)
                # The joint stopped is unhomed; the groups before it stay homed.
                self.assertEqual([snap.homing.joints[j].phase is HomingPhase.HOMED
                                  for j in range(3)],
                                 [False, joint == 0, joint in (0, 1)])
                self.assertIn(LABEL[(joint, end)] + " switch'ine çarptı", snap.last_error.text)
                card = VM.build(PROFILE, CONFIG, snap)["kontrol"]["limit"]
                away = "+" if end == "-" else "−"
                self.assertEqual(card["button_label"], "Limitten kurtar: {}{} yönünde 3 mm".format(
                    "XYZ"[joint], away))
                self.assertTrue(card["enabled"])
                self.machine.log.clear()
                adapter.recover_limit()
                self.assertEqual(self.sent()[-1],
                                 ("jog", (JOG, 1, joint, (1 if end == "-" else -1) * JOG_VEL, 3.0)))
                self.release(joint, end)
                self.assertIn("serbest", adapter.snapshot().message)
                self.assertNoLinuxCncErrors()


class TestYLeftOnItsFrontSwitchAfterHomeAll(_Case):
    """Batch-1 finding (WP01): a jog click during Home All left Y on its
    front switch with the machine off, Z homed, X and Y not. The card must
    offer Y+ and the recovery work (a joint jog: not all homed, free mode)."""

    def test_the_card_and_the_recovery(self):
        adapter = self.make()
        self.machine.estop_reset_state()
        self.machine.homed[2] = 1
        self.press(1, "-")
        snap = adapter.snapshot()
        card = VM.build(PROFILE, CONFIG, snap)["kontrol"]["limit"]
        self.assertEqual([s["text"] for s in card["switches"]], ["Y ön uç switch'i BASILI"])
        self.assertEqual(card["button_label"], "Limitten kurtar: Y+ yönünde 3 mm")
        markup = R.render_body(VM.build(PROFILE, CONFIG, snap))
        self.assertIn('data-action="recover_limit"', markup)
        adapter.software_estop_reset()            # what the operator tried first
        self.assertEqual(self.sent(), [])
        adapter.recover_limit()
        self.assertEqual(self.sent(), [
            ("override_limits", ()), ("state", (C["STATE_ON"],)), ("jog", (JOG, 1, 1, JOG_VEL, 3.0)),
        ])
        self.release(1, "-")
        snap = adapter.snapshot()
        self.assertEqual(snap.message, "Y ön uç serbest; limit koruması yeniden etkin. Referans alın.")
        self.assertIsNone(VM.build(PROFILE, CONFIG, snap)["kontrol"]["limit"])
        self.assertNoLinuxCncErrors()


# ---------------------------------------------------------------------------
# The view: the limit card and the header badge
# ---------------------------------------------------------------------------


class TestLimitCard(unittest.TestCase):

    def card(self, state: MachineState, observe: bool = False):
        return VM.build(PROFILE, CONFIG, state, observe=observe)["kontrol"]["limit"]

    def markup(self, state: MachineState, observe: bool = False, screen: str = "kontrol") -> str:
        return R.render_body(VM.build(PROFILE, CONFIG, state, screen=screen, observe=observe))

    def test_no_card_without_a_pressed_switch(self):
        self.assertIsNone(self.card(rig_state()))
        self.assertNotIn('class="card limit"', self.markup(rig_state()))
        self.assertNotIn('data-action="recover_limit"', self.markup(rig_state()))

    def test_the_button_says_what_moves_which_way_how_far(self):
        """Test (14), the card."""
        markup = self.markup(rig_state("X-"))
        self.assertIn(esc("Limitten kurtar: X+ yönünde 3 mm"), markup)
        self.assertIn(esc("X sol uç switch'i BASILI"), markup)
        self.assertIn(PIN[(0, "-")], markup)
        self.assertIn('data-action="recover_limit"', markup)
        button = markup[markup.index('data-action="recover_limit"') - 80:
                        markup.index('data-action="recover_limit"') + 80]
        self.assertNotIn("disabled", button)
        self.assertIn(esc(VM.LIMIT_CARD_NOTE), markup)

    def test_in_the_middle_column_above_the_estop_card(self):
        markup = self.markup(rig_state("Z+"))
        self.assertLess(markup.index('class="card limit"'), markup.index("Acil stop</span>"))

    def test_off_with_its_reason_as_text(self):
        cases = (
            (rig_state("X-", link=rig_state().link._replace(io_error=True)), MR.LINK_FIRST, False),
            (rig_state("X-", task_mode="auto", interp_state="reading"), MR.PROGRAM_BUSY, False),
            (rig_state("X-", homing="Z"), MR.HOMING_BUSY, False),
            (rig_state("X-"), VM.OBSERVE_REASON, True),
        )
        for state, why, observe in cases:
            with self.subTest(why=why):
                card = self.card(state, observe)
                self.assertEqual((card["enabled"], card["disabled_reason"]), (False, why))
                markup = self.markup(state, observe)
                start = markup.index('data-action="recover_limit"')
                self.assertIn("disabled", markup[start - 80:start + 80])
                self.assertIn('<div class="cap-reason">{}</div>'.format(esc(why)), markup)

    def test_the_stale_note_while_the_link_is_lost(self):
        state = rig_state("X-", link=rig_state().link._replace(watchdog_has_bit=True))
        self.assertTrue(self.card(state)["stale"])
        self.assertIn(esc(VM.LIMIT_STALE_NOTE), self.markup(state))

    def test_several_switches_listed_the_first_left_first(self):
        card = self.card(rig_state("X-", "Y-"))
        self.assertEqual([s["label"] for s in card["switches"]], ["X sol uç", "Y ön uç"])
        self.assertIn("ardından Y ön uç için tekrar basın", card["others_note"])

    def test_both_ends_listed_and_refused(self):
        card = self.card(rig_state("X-", "X+"))
        self.assertEqual(len(card["switches"]), 2)
        self.assertFalse(card["enabled"])

    def test_the_mock_never_shows_one(self):
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                self.assertNotIn("recover_limit", helpers.html("test-rig", screen))


class TestOverrideBadge(unittest.TestCase):

    def test_on_every_screen_while_the_override_is_on(self):
        """Test (14), the badge."""
        state = rig_state("X-", override_limits=True)
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                markup = R.render_body(VM.build(PROFILE, CONFIG, state, screen=screen))
                header = markup[:markup.index("</header>")]
                self.assertIn('data-override="1"', header)
                self.assertIn(esc(MR.OVERRIDE_BADGE), header)

    def test_not_while_off_unread_or_unknown(self):
        for state in (rig_state(), rig_state(override_limits=None),
                      rig_state(override_limits=True).as_status_unknown(),
                      # A reading kept while the status is unknown is no reading.
                      rig_state(override_limits=True)._replace(status_known=False),
                      MockAdapter(PROFILE).snapshot()):
            with self.subTest(state=state.override_limits):
                vm = VM.build(PROFILE, CONFIG, state)
                self.assertFalse(vm["status"]["limit_override"]["active"])
                self.assertNotIn('data-override="1"', R.render_body(vm))

    def test_from_the_fake(self):
        with F.installed(hal_state=F.FakeHal(T.ALL_LINK_PINS)) as (_, _, m, _):
            adapter = A.LinuxCncAdapter(PROFILE, halcmd=lambda args: (True, ""),
                                        proc_root="/nonexistent/proc")
            m.estop_reset_state()
            m.hard_min[0] = 1
            m.issue_override_limits()
            vm = VM.build(PROFILE, CONFIG, adapter.snapshot())
        self.assertEqual(vm["status"]["limit_override"], {"active": True, "label": MR.OVERRIDE_BADGE})


class TestMock(unittest.TestCase):

    def test_recover_limit_says_none_is_pressed(self):
        adapter = MockAdapter(PROFILE)
        adapter.recover_limit()
        snap = adapter.snapshot()
        self.assertEqual((snap.message, snap.message_severity), (MR.NO_TRIP, Severity.WARN))
        self.assertFalse(snap.hard_limits)

    def test_through_the_session(self):
        sess = app.build_session("test-rig", "mock")
        sess.act("recover_limit", "")
        self.assertEqual(sess.view("kontrol")["status"]["message"], MR.NO_TRIP)


class TestSessionDropsAQueuedRecovery(unittest.TestCase):
    """recover_limit switches the machine on and jogs: in MOTION_ACTIONS, so
    one still waiting for the session lock when DURDUR or the estop comes is
    dropped unseen by the adapter (test_stop_rail's lock-queue helpers)."""

    def test_dropped_after_a_stop_or_an_estop(self):
        self.assertIn("recover_limit", app.MOTION_ACTIONS)
        self.assertIn("recover_limit", app.DROPPED_AFTER_A_STOP)
        for overtaker in ("stop", "software_estop", None):
            with self.subTest(overtaker=overtaker):
                adapter = S._HeldAdapter()
                sess = S._session(adapter)
                sess._lock = S._ObservedLock()
                holder = threading.Thread(target=sess.act, args=("home_all", ""), daemon=True)
                waiter = threading.Thread(target=sess.act, args=("recover_limit", ""), daemon=True)
                holder.start()
                self.assertTrue(adapter.entered.wait(S.WAIT_S))
                waiter.start()
                self.assertTrue(sess._lock.waiting.wait(S.WAIT_S), "the queued press never waited")
                threads = [holder, waiter]
                if overtaker:
                    # DURDUR's second half waits for the lock: from its own thread.
                    stopper = threading.Thread(target=sess.act, args=(overtaker, ""), daemon=True)
                    stopper.start()
                    threads.append(stopper)
                    first = "stop_motion" if overtaker == "stop" else "software_estop"
                    S.wait_for(lambda: first in adapter.calls, overtaker + " to go out")
                adapter.release.set()
                for t in threads:
                    t.join(S.WAIT_S)
                    self.assertFalse(t.is_alive())
                if overtaker:
                    self.assertNotIn("recover_limit", adapter.calls)
                else:   # premise: without a stop the queued press runs
                    self.assertIn("recover_limit", adapter.calls)


if __name__ == "__main__":
    unittest.main()
