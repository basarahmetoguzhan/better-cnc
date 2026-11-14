"""Jog safety (plan WP06): the UI decides a jog click before anything goes
to LinuxCNC - refused while a joint homes, capped while unhomed, cut to the
soft limit after homing (and said so), refused while a jog still runs - and
LinuxCNC itself (linuxcnc.stat, halcmd) is checked to have done exactly
that.

Until WP06 these were known gaps: a click during Home All went to LinuxCNC,
which refused it ("Can't jog any joint while homing.",
reference/linuxcnc/src/emc/motion/command.c:853-857) and aborted, cutting
the homing short - in the sim lens's runs mid-search, in WP01's run with Y
left on its front switch and the machine off; unhomed any step went; after
homing motion dropped a jog past a soft limit without a word
(reference/linuxcnc/src/emc/motion/axis.c:289-290) while the UI said
"gönderildi"; a second click while a jog ran added to it (axis.c:283-287).

Classes run in name order on one LinuxCNC: A and B need it never homed (B's
Home All is then a first one, which asks no confirmation, plan WP07), C
homes.
"""

from __future__ import annotations

import time
import unittest

import harness as h

#: The jog speed: [DISPLAY]DEFAULT_LINEAR_VELOCITY (test-rig.ini:59).
JOG_VEL = 3.0


def _unmoved_for(seconds: float, before) -> object:
    time.sleep(seconds)
    s = h.stat()
    return s if h.near(h.position(s), before) else None


def _pad(vm, key: str) -> dict:
    """kontrol.jog_pad.buttons[key] (viewmodel.jog_pad): enabled, reason."""
    return vm["kontrol"]["jog_pad"]["buttons"][key]


def _chip(vm, mm: float) -> dict:
    return next(c for c in vm["kontrol"]["jog_step_chips"] if abs(c["mm"] - mm) < 1e-9)


class A_UnhomedStepLimits(h.Scenario):
    """Unhomed, a free-mode jog may go a whole stroke from where the joint
    stands, re-centred at every jog (reference/linuxcnc/src/emc/motion/
    command.c:161-175), and Z has no switch below: the UI caps the step at
    10 mm, and at 1 mm toward the Z bottom."""

    def setUp(self):
        super().setUp()
        h.ensure_on()
        if any(h.homed()):
            self.fail("needs a LinuxCNC never homed: " + h.describe())

    def test_a_50_mm_step_is_refused_while_unhomed(self):
        before = h.position()
        h.jog("X", 1, 50)
        self.assertTrue(_unmoved_for(1.0, before), "moved: " + h.describe())
        self.assertIn("en çok 10 mm", h.norm(h.message()))
        vm = h.state()
        self.assertFalse(_chip(vm, 50.0)["enabled"])
        self.assertIn("referanssız en çok 10 mm", _chip(vm, 50.0)["reason"])
        # 10 mm goes, whole (a joint jog: X is unhomed), and the line says
        # what moved only now that it went.
        target = before[0] + 10.0
        h.jog("X", 1, 10)
        s = h.wait_position((target, before[1], before[2]), h.move_time(10.0, JOG_VEL))
        self.assertEqual(h.task_state_name(s), "ON", h.describe(s))
        self.assertEqual(h.norm(h.message()), "Jog X+10 mm.")

    def test_z_down_10_mm_is_refused_while_unhomed(self):
        before = h.position()
        h.jog("Z", -1, 10)
        self.assertTrue(_unmoved_for(1.0, before), "moved: " + h.describe())
        self.assertIn("en çok 1 mm", h.norm(h.message()))
        self.assertIn("Z alt switch'i yok", h.message())
        pad = _pad(h.state(), "Z:-1")
        self.assertFalse(pad["enabled"])
        self.assertIn("en çok 1 mm", h.norm(pad["reason"]))
        # 1 mm goes.
        h.jog("Z", -1, 1)
        h.wait_position((before[0], before[1], before[2] - 1.0), h.move_time(1.0, JOG_VEL))
        self.assertEqual(h.norm(h.message()), "Jog Z-1 mm.")


class B_JogDuringHoming(h.Scenario):
    """The sim lens's step R: a jog click during Home All. The UI now
    refuses it before anything is sent, so LinuxCNC never sees it and the
    homing goes on to its normal end: every joint homed, standing at HOME,
    no switch pressed - Y must not end on its switch at all.

    The click comes while Y searches for its switch, Z already homed, not
    "1 s after the start" as the plan has it: 1 s in, Z is within about
    0.2 s of its top switch, and if anything did cut the homing short there
    (the gap this closes) Z would be left on the switch
    (harness.wait_homing_search)."""

    def setUp(self):
        super().setUp()
        h.ensure_on()
        h.post("jog_step", "1")

    def test_a_jog_click_during_home_all_is_refused_and_homing_completes(self):
        mark = h.alarm_mark()
        h.post("home_all")
        h.confirm_if_asked()
        h.wait_homing_search(1, travel=1.0)
        answer = h.post("jog", "Z:1")
        said = h.message()
        s = h.wait_homing_over()
        self.assertEqual(h.homed(s), (True, True, True), h.describe(s))
        self.assertIn("Referans alma sürüyor", said)
        # The page answering the refused click shows the jog card off, why.
        self.assertIn("Referans alma sürüyor — jog kapalı.", answer["html"])
        # LinuxCNC's own word that the homing ended normally.
        s = h.wait_still()
        self.assertEqual(h.task_state_name(s), "ON", h.describe(s))
        self.assertTrue(h.near(h.position(s), h.HOME_POSITION), h.describe(s))
        self.assertEqual(h.homing(s), (False, False, False), h.describe(s))
        self.assertEqual(h.hard_limits(s), ((False, False),) * 3, h.describe(s))
        self.assertEqual([n for n in h.SWITCHES if h.switch_pressed(n)], [])
        # No jog reached motion to be refused, so no refusal came back.
        self.assertFalse([t for t in h.new_alarms(mark) if "jog" in t.lower()],
                         h.new_alarms(mark))
        self.assertTrue(h.state()["kontrol"]["can_jog"])


class C_HomedJogs(h.Scenario):

    def setUp(self):
        super().setUp()
        h.ensure_homed()
        if abs(h.position()[2] - h.HOME_POSITION[2]) > h.POSITION_TOL:
            h.mdi("G0 Z{:g}".format(h.HOME_POSITION[2]))
            h.wait_position((None, None, h.HOME_POSITION[2]), h.move_time(100.0, 5.0))
        if abs(h.position()[0] - h.HOME_POSITION[0]) > h.POSITION_TOL:
            h.mdi("G0 X{:g}".format(h.HOME_POSITION[0]))
            h.wait_position((h.HOME_POSITION[0], None, None), h.move_time(400.0, 10.0))
        # The MDI line ends a little after the move; until then the UI
        # refuses a jog (a program or MDI line running), as test_limits E.
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 2.0, "interp IDLE",
                     detail=h.describe)

    def test_a_jog_past_the_soft_limit_is_cut_to_it(self):
        # Z at -5, MAX_LIMIT -3: a 10 mm step may go 2 mm.
        top = h.SOFT_LIMITS["Z"][1]
        h.jog("Z", 1, 10)
        s = h.wait_position((None, None, top), h.move_time(2.0, JOG_VEL))
        self.assertEqual(h.task_state_name(s), "ON", h.describe(s))
        said = h.norm(h.message())
        self.assertRegex(said, r"en çok 2\.000 mm gidebilir")
        self.assertIn("kısaltıldı", said)
        # The plan's open item: stat.position is where motion's teleop
        # planner stands - its target, axis.L.teleop-pos-cmd (axis.c:161),
        # at rest - the position the cut counts from.
        self.assertLess(abs(float(s.position[2]) - h.get_float("axis.z.teleop-pos-cmd")), 1e-6,
                        h.describe(s))
        self.assertLess(abs(float(s.position[2]) - top), h.POSITION_TOL, h.describe(s))
        # On the limit now: Z+ is refused and nothing moves; Z- still goes.
        before = h.position()
        h.jog("Z", 1, 1)
        self.assertTrue(_unmoved_for(1.0, before), "moved: " + h.describe())
        self.assertIn("Z yumuşak limitte (-3.000 mm)", h.norm(h.message()))
        self.assertFalse(_pad(h.state(), "Z:1")["enabled"])
        h.jog("Z", -1, 1)
        h.wait_position((None, None, before[2] - 1.0), h.move_time(1.0, JOG_VEL))

    def test_a_second_jog_while_one_runs_is_refused(self):
        x0 = h.position()[0]
        h.jog("X", 1, 10)
        h.wait_until(lambda: h.stat().current_vel > 0, 2.0, "the jog to start", detail=h.describe)
        h.jog("X", 1, 10)
        said = h.message()
        s = h.wait_still(h.move_time(20.0, JOG_VEL))
        self.assertAlmostEqual(h.position(s)[0], x0 + 10.0, delta=h.POSITION_TOL, msg=h.describe(s))
        self.assertIn("Hareket sürüyor", said)

    def test_x_left_ten_from_home_goes_two(self):
        """X at 5, MIN_LIMIT 3: the cut works at a minimum too."""
        low = h.SOFT_LIMITS["X"][0]
        h.jog("X", -1, 10)
        h.wait_position((low, None, None), h.move_time(2.0, JOG_VEL))
        self.assertIn("X- en çok 2.000 mm gidebilir", h.norm(h.message()))


if __name__ == "__main__":
    unittest.main()
