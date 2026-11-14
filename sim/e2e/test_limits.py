"""Recovering from a limit switch: WP05's scenarios (a)-(e), and the two
situations batch 1 found an axis left on a switch in.

The arrange steps (setUp) trip a switch: one moved into the axis's way
(harness.place_switch_ahead) and the axis jogged onto it, or a homing
stopped with DURDUR while its joint latches on its home switch. Motion
then stops the machine ("joint N on limit switch error",
reference/linuxcnc/src/emc/motion/control.c:807-826). Since plan WP05 the
error box names the switch, «Yazılım sıfırla» sends no ON onto it, Home All
refuses, and «Limitten kurtar» takes the axis off in one press: LinuxCNC's
limit override, the machine on, one joint jog away (bettercnc.adapter
LinuxCncAdapter.recover_limit); the override ends with that jog.

Classes run in name order on one LinuxCNC; A-D and H need the axis they
use unhomed, E homes, F and G run Home All and stop it. tearDown puts every
switch back where sim/test-rig-sim.hal has it, frees an axis still on one
(the UI's «Limitten kurtar» first, harness.rescue_from_switches) and turns
the machine on.
"""

from __future__ import annotations

import time

import harness as h

#: WP05's step off a switch while unhomed, and while homed inside the soft
#: limits (bettercnc.motionrules.RECOVERY_STEP_MM: chosen, not measured).
RECOVERY_STEP_MM = 3.0

#: The header badge while LinuxCNC's limit override is on
#: (bettercnc.motionrules.OVERRIDE_BADGE).
OVERRIDE_BADGE = "LİMİT KORUMASI GEÇİCİ KAPALI"


def trip_text(label: str) -> str:
    """The error box after a trip on the switch `label`
    (bettercnc.messages.limit_trip_text): one text, the drain's and the
    state note's alike."""
    return "{} switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın.".format(label)


def card(vm=None):
    """The Kontrol limit card (viewmodel.limit_panel), None without one."""
    vm = vm or h.state()
    return vm["kontrol"].get("limit")


def button_label(axis: str, away: int, step: float = RECOVERY_STEP_MM) -> str:
    return "Limitten kurtar: {}{} yönünde {:g} mm".format(axis, "+" if away > 0 else "−", step)


class _Recovers(h.Scenario):
    """The recovery, checked against LinuxCNC: `self.sw` is the switch the
    axis stands on, `self.trip_position` where it stopped."""

    sw: h.Switch

    def recover(self, step: float = RECOVERY_STEP_MM, homed: bool = False) -> None:
        """The card offers it; one press switches the machine on (the badge
        while the override lasts) and jogs `step` mm off the switch; the
        override ends with the jog, the switch is free, the machine stays
        ON and the message says protection is back."""
        away = 1 if self.sw.end == "-" else -1
        c = card()
        self.assertIsNotNone(c, h.describe())
        self.assertEqual(c["button_label"], button_label(self.sw.axis, away, step))
        self.assertTrue(c["enabled"], c["disabled_reason"])
        answer = h.post("recover_limit")
        # The jog takes about a second: the POST's own page still sees the
        # override on.
        self.assertIn(OVERRIDE_BADGE, answer["html"])
        i = self.sw.joint
        target = [None, None, None]
        target[i] = self.trip_position[i] + away * step
        s = h.wait_position(target, h.move_time(step, 3.0) + 2.0)
        self.assertEqual(int(s.task_state), h.STATE_ON, h.describe(s))
        h.wait_until(lambda: not h.override_limits(), 1.0, "the override to clear")
        self.assertFalse(h.switch_pressed(self.name))
        said = h.wait_until(lambda: (lambda m: m if "serbest" in m else None)(h.message()), 2.0,
                            "the recovery's word", detail=h.message)
        self.assertEqual(h.norm(said), h.norm("{} serbest; limit koruması yeniden etkin.{}".format(
            self.sw.label, "" if homed else " Referans alın.")))
        self.assertIsNone(card(), "no card once the switch is free")
        time.sleep(0.5)
        self.assertEqual(int(h.stat().task_state), h.STATE_ON, "stays ON: " + h.describe())


class _Trip(_Recovers):
    """Arrange: `switch` placed 2 mm ahead of its unhomed axis, the axis
    jogged toward it 1 mm at a time (`direction`) until motion trips."""

    switch = ""
    direction = 0
    max_steps = 4

    @property
    def sw(self) -> h.Switch:
        return h.SWITCHES[self.switch]

    @property
    def name(self) -> str:
        return self.switch

    def place(self) -> None:
        h.place_switch_ahead(self.switch, 2.0)

    def setUp(self):
        super().setUp()
        h.ensure_on()
        if h.homed()[self.sw.joint]:
            self.fail("needs {} unhomed: {}".format(self.sw.axis, h.describe()))
        self.mark = h.alarm_mark()
        self.place()
        s = h.drive_into_switch(self.sw.axis, self.direction, 1.0, self.max_steps)
        self.assertNotEqual(int(s.task_state), h.STATE_ON)
        self.trip_position = h.position(s)
        h.wait_alarm("joint {} on limit switch error".format(self.sw.joint), self.mark)
        self.assertTrue(h.switch_pressed(self.switch))

    def check_recovery(self) -> None:
        """Plan WP05 (a): the error names the switch; software_reset does
        not send ON and says how to recover; recover_limit turns the
        machine on and jogs RECOVERY_STEP_MM away; the override clears when
        that jog ends, the switch is free and the machine stays ON."""
        self.assertIn(h.norm(trip_text(self.sw.label)), h.norm(h.last_error_text()))
        mark = h.alarm_mark()
        h.post("software_reset")
        time.sleep(0.5)
        s = h.stat()
        self.assertNotEqual(int(s.task_state), h.STATE_ON, h.describe(s))
        # ON sent onto the switch re-trips (control.c:807-826) - a new limit
        # error - or fails to take: neither may happen.
        self.assertFalse([t for t in h.new_alarms(mark)
                          if "on limit switch error" in t or "açılamadı" in t], h.new_alarms(mark))
        self.assertIn(h.norm("{} switch'i basılı — LinuxCNC bir eksen limitteyken makineyi açmaz. "
                             "«Limitten kurtar»a basın.".format(self.sw.label)), h.norm(h.message()))
        self.recover()


class A_XLeftSwitch(_Trip):
    switch, direction = "x-min", -1

    def test_recover_from_the_left_switch(self):
        self.check_recovery()


class B_ZTopSwitch(_Trip):
    switch, direction = "z-top", 1

    def test_recover_from_the_top_switch(self):
        self.check_recovery()


class C_XRightSwitch(_Trip):
    switch, direction = "x-max", 1
    max_steps = 6

    def place(self) -> None:
        # Wherever A left X (unhomed): 2 mm ahead, kept on the switch's
        # side of motor 0 (sim_home_switch.comp:29-59).
        h.place_switch("x-max", max(h.switch_motor_position("x-max"), 0.0) + 2.0)

    def test_recover_from_the_right_switch(self):
        self.check_recovery()


class D_YFrontSwitch(_Trip):
    """Y on its front switch with the machine off, unhomed: the end state of
    the batch-1 finding where a jog click during Home All left Y there
    (sim/README.md). Home All from a pressed switch fails even with the
    override - the mask drops when an earlier joint's homing pauses (sim
    lens (d)) - so it is refused, and the recovery is the way."""

    switch, direction = "y-min", -1

    def test_home_all_is_refused_and_says_why(self):
        h.post("home_all")
        time.sleep(1.0)
        self.assertFalse(any(h.homing()), h.describe())
        self.assertIn(h.norm("Y ön uç switch'i basılı — önce «Limitten kurtar»: referans alma "
                             "switch üstünden başlayamaz."), h.norm(h.message()))

    def test_the_card_offers_y_plus_and_the_recovery_frees_it(self):
        c = card()
        self.assertEqual([s["text"] for s in c["switches"]], ["Y ön uç switch'i BASILI"])
        self.assertEqual([s["hal_pin"] for s in c["switches"]], ["hm2_7i92.0.gpio.023.in_not"])
        self.recover()


class E_HomedTrip(_Recovers):
    """Homed, X at 50, its right switch moved 5 mm ahead (the sim lens's
    way: a homed axis never reaches the real switches, which sit beyond
    the soft limits), a 10 mm teleop jog trips it at about X55."""

    sw = h.SWITCHES["x-max"]
    name = "x-max"

    def setUp(self):
        super().setUp()
        h.ensure_homed()
        h.mdi("G0 X50")
        h.wait_position((50.0, None, None), h.move_time(45.0, 10.0))
        # The MDI line ends a little after the move; until then the UI
        # refuses the jog below ("Program çalışırken jog yok."). Once (review
        # of WP05) no jog moved X with task still in MDI mode here and the
        # arrange timed out. As test_baseline does.
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 2.0, "interp IDLE",
                     detail=h.describe)
        self.mark = h.alarm_mark()
        h.place_switch_ahead("x-max", 5.0)
        s = h.drive_into_switch("X", 1, 10.0, max_steps=1)
        self.assertNotEqual(int(s.task_state), h.STATE_ON)
        self.trip_position = h.position(s)
        h.wait_alarm("joint 0 on limit switch error", self.mark)

    def test_recovery_lands_inside_the_soft_limits_and_teleop_jogs_again(self):
        """Plan WP05 (e): homed, motion in teleop when it tripped; the
        recovery leaves teleop, jogs the joint 3 mm (inside the soft
        limits there), and the next jog is a teleop jog again."""
        self.recover(homed=True)
        low, high = h.SOFT_LIMITS["X"]
        self.assertTrue(low <= h.position()[0] <= high, h.describe())
        h.jog_and_wait("X", -1, 1.0)
        self.assertEqual(int(h.stat().task_state), h.STATE_ON, h.describe())
        self.assertEqual(h.stat().motion_mode, h._lc().TRAJ_MODE_TELEOP, h.describe())
        self.assertEqual(h.homed(), (True, True, True))


class _StopInTheLatch(_Recovers):
    """Batch-1 finding (WP03): DURDUR while a joint latches on its home
    switch - on the rig also its limit switch (X−, Y−, Z+) - cancels the
    homing there (command.c:509-520), the joint unhomed and no longer
    homing, and motion stops the machine on it (control.c:807-826). Home
    All, wait until `joint` is homing with its switch flag up, DURDUR."""

    joint = -1

    @property
    def sw(self) -> h.Switch:
        return h.SWITCHES[self.name]

    def setUp(self):
        super().setUp()
        h.ensure_on()
        self.mark = h.alarm_mark()
        side = 0 if self.sw.end == "-" else 1
        h.post("home_all")
        h.confirm_if_asked()

        def latching():
            s = h.stat()
            return s if h.homing(s)[self.joint] and h.hard_limits(s)[self.joint][side] else None
        h.wait_until(latching, h.HOME_TIMEOUT_S, "joint {} on its switch, homing".format(self.joint),
                     every=0.005, detail=h.describe)
        h.post("stop")
        h.wait_until(lambda: int(h.stat().task_state) != h.STATE_ON, 2.0,
                     "the limit to stop the machine", detail=h.describe)
        h.wait_alarm("joint {} on limit switch error".format(self.joint), self.mark)
        self.trip_position = h.position()
        self.assertTrue(h.switch_pressed(self.name), h.describe())
        self.assertFalse(h.homed()[self.joint])
        self.assertFalse(any(h.homing()), h.describe())


class F_StopInYsLatch(_StopInTheLatch):
    """Y left on its front switch, unhomed, the machine off, Z homed: also
    the state the batch-1 jog click during Home All left (WP01)."""

    joint, name = 1, "y-min"

    def test_the_card_appears_and_the_recovery_frees_y(self):
        self.assertIn(h.norm(trip_text("Y ön uç")), h.norm(h.last_error_text()))
        self.assertTrue(h.homed()[2], "Z homed before Y: " + h.describe())
        self.recover()
        self.assertTrue(h.homed()[2], h.describe())


class G_StopInZsLatch(_StopInTheLatch):
    joint, name = 2, "z-top"

    def test_the_card_appears_and_the_recovery_frees_z(self):
        self.assertIn(h.norm(trip_text("Z üst")), h.norm(h.last_error_text()))
        self.recover()


class H_EstopCutsTheRecoveryJog(_Trip):
    """Review of plan WP05: the software estop while the recovery jog is
    still on the switch. The jog ran under the override, which sets
    motion's `overriding` (control.c:1263-1270); the disable runs no
    end-of-jog check (control.c:1463-1477), so LinuxCNC still shows the
    override on, and at the next enable the free planner drops the mask at
    once (control.c:1278-1283): an ON then trips on the switch - which
    «Yazılım sıfırla» sent, and the next «Limitten kurtar» too, before the
    review. Now the reset sends no ON onto a pressed switch, overridden or
    not, and one press recovers: it issues the override again, which
    clears `overriding` (command.c:700)."""

    switch, direction = "x-max", 1
    max_steps = 6

    def place(self) -> None:
        # As C: 2 mm ahead, kept on the switch's side of motor 0.
        h.place_switch("x-max", max(h.switch_motor_position("x-max"), 0.0) + 2.0)

    def test_reset_sends_no_on_and_one_press_recovers(self):
        # X 2 mm deep on its switch, so the 3 mm jog is still on it, at
        # speed, when the estop comes.
        h.place_switch("x-max", max(h.switch_motor_position("x-max") - 2.0, 0.0))
        self.assertTrue(h.switch_pressed(self.switch))
        start = h.position()[0]
        h.post("recover_limit")
        h.wait_until(lambda: h.moving() and start - h.position()[0] >= 0.3, 2.0,
                     "the recovery jog under way", every=0.01, detail=h.describe)
        h.post("software_estop")
        h.wait_until(lambda: int(h.stat().task_state) != h.STATE_ON, h.ESTOP_STOP_S,
                     "the estop", detail=h.describe)
        s = h.wait_still()
        self.assertTrue(h.switch_pressed(self.switch), "premise: still on it " + h.describe(s))
        self.assertTrue(h.override_limits(s), "premise: the override outlived the estop "
                        + h.describe(s))
        mark = h.alarm_mark()
        h.post("software_reset")
        time.sleep(0.5)
        s = h.stat()
        self.assertNotEqual(int(s.task_state), h.STATE_ON, h.describe(s))
        self.assertFalse([t for t in h.new_alarms(mark)
                          if "on limit switch error" in t or "açılamadı" in t], h.new_alarms(mark))
        self.assertIn(h.norm("X sağ uç switch'i basılı — LinuxCNC bir eksen limitteyken makineyi "
                             "açmaz. «Limitten kurtar»a basın."), h.norm(h.message()))
        self.trip_position = h.position(s)
        self.recover()
        self.assertFalse([t for t in h.new_alarms(mark) if "on limit switch error" in t],
                         h.new_alarms(mark))


if __name__ == "__main__":
    import unittest
    unittest.main()
