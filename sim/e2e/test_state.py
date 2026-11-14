"""MachineState's motion fields (plan WP02), end to end: what GET /api/state
reports, checked against LinuxCNC's own stat.

One scenario on one fresh LinuxCNC, step by step (harness.OrderedSteps): a
limit-switch flag read with the machine off and unhomed, the readings after
Home All, `moving` and the commanded position through a 10 mm jog, an MDI
move, and the work DRO through G92 and G92.1. Every step must pass: the
package that owns this module is done (no expected failures here).
"""

from __future__ import annotations

import time

import harness as h


def _flags(m):
    """motion.hard_limits as (joint, axis, min, max) tuples."""
    return [(f["joint"], f["axis"], f["min"], f["max"]) for f in m["hard_limits"]]


class StatePlumbing(h.OrderedSteps):

    def test_01_hard_limit_flag_with_the_machine_off_and_unhomed(self):
        """Motion reads the switches every servo cycle, enabled or not
        (reference/linuxcnc/src/emc/motion/control.c:491-501): X's left
        switch pressed under an unhomed X with the machine off shows as
        joint 0 min in /api/state, and trips nothing."""
        # A negative-side switch can be pressed only below motor 0
        # (harness.press): one 1 mm joint jog there first.
        h.reset_on()
        h.jog_and_wait("X", -1, 1.0)
        h.post("software_estop")
        h.wait_task_state(h.STATE_ESTOP_RESET, 2.0)
        h.press("X-")
        try:
            m = h.wait_until(lambda: (lambda m: m if m["hard_limits"][0]["min"] else None)(h.motion()),
                             2.0, "joint 0 min in /api/state",
                             detail=lambda: "UI {}, LinuxCNC {}".format(
                                 _flags(h.motion()), h.hard_limits()))
            self.assertEqual(_flags(m), [(0, "X", True, False), (1, "Y", False, False),
                                         (2, "Z", False, False)])
            self.assertEqual(h.hard_limits(), ((True, False), (False, False), (False, False)))
            self.assertIs(m["moving"], False)
            self.assertIs(m["override_limits"], False)
            s = h.stat()
            self.assertEqual(h.task_state_name(s), "ESTOP_RESET", h.describe(s))
            self.assertEqual(h.homed(s), (False, False, False))
        finally:
            self.assertEqual(h.restore_switches(), [])
        h.wait_until(lambda: not any(f[2] or f[3] for f in _flags(h.motion())), 2.0,
                     "every flag clear in /api/state", detail=lambda: str(_flags(h.motion())))

    def test_02_readings_after_homing(self):
        h.reset_on()
        h.home_all()
        s = h.wait_still()
        m = h.motion()
        self.assertEqual((m["task_mode"], m["motion_mode"], m["interp_state"]),
                         ("manual", "teleop", "idle"))
        self.assertEqual((m["moving"], m["program_paused"], m["mdi_running"], m["g92_active"],
                          m["override_limits"]), (False, False, False, False, False))
        dro = h.dro()
        g5x = tuple(s.g5x_offset)[:3]
        for i, axis in enumerate(h.AXES):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(dro[axis].machine, h.HOME_POSITION[i], places=3)
                self.assertAlmostEqual(dro[axis].work, h.HOME_POSITION[i] - g5x[i], places=3)
                self.assertAlmostEqual(m["commanded"][axis], h.HOME_POSITION[i], places=6)

    def test_03_moving_through_a_ten_mm_jog(self):
        """/api/state says moving while LinuxCNC moves and not after. A
        teleop jog leaves stat.inpos True throughout (control.c:1398-1461),
        so this is current_vel. The commanded position (stat.position,
        carte_pos_cmd) leads the actual one while the jog runs - in sim/ by
        0.009 mm at 3 mm/s - and the two meet at the target."""
        start = h.position()[0]
        target = start + 10.0                    # 5 -> 15: inside X's 3..399
        h.jog("X", 1, 10.0)
        leads = []
        ui_leads = []
        deadline = time.monotonic() + h.move_time(10.0, 3.0)
        saw_moving = False
        while time.monotonic() < deadline:
            s = h.stat()
            leads.append(float(s.position[0]) - float(s.actual_position[0]))
            vm = h.state()
            m = h.motion(vm)
            if m["moving"]:
                saw_moving = True
                ui_leads.append(m["commanded"]["X"] - h.dro(vm)["X"].machine)
            elif saw_moving:
                break
            time.sleep(0.05)
        self.assertTrue(saw_moving, h.describe())
        s = h.wait_position((target, None, None), h.move_time(10.0, 3.0))
        m = h.motion()
        self.assertIs(m["moving"], False)
        self.assertAlmostEqual(m["commanded"]["X"], target, places=6)
        self.assertAlmostEqual(float(s.position[0]), float(s.actual_position[0]), places=6)
        self.assertGreater(max(leads), 0.001, leads)
        self.assertGreaterEqual(min(leads), -1e-9, leads)
        self.assertGreater(max(ui_leads), 0.004, ui_leads)

    def test_04_mdi_move_and_g92_in_the_work_dro(self):
        """While an MDI move runs: MDI mode, interpreter busy, mdi_running,
        moving. Then G92 X10 at machine X 20: the work X reads 10, i.e.
        machine X - 10 (G54 is zero in a fresh sim), and g92_active; G92.1
        puts them back level. G92 survives a LinuxCNC restart, so this
        step clears it whatever happens."""
        h.mdi("G53 G0 X20")
        m = h.wait_until(lambda: (lambda m: m if m["mdi_running"] else None)(h.motion()),
                         2.0, "mdi_running in /api/state", every=0.02, detail=h.describe)
        self.assertEqual(m["task_mode"], "mdi")
        self.assertIn(m["interp_state"], ("reading", "waiting"))
        self.assertIs(m["moving"], True)
        h.wait_position((20.0, None, None), h.move_time(5.0, 10.0))
        h.wait_until(lambda: h.motion()["mdi_running"] is False, 2.0, "mdi_running False",
                     detail=lambda: str(h.motion()))
        g5x = tuple(h.stat().g5x_offset)[:3]
        h.mdi("G92 X10")
        try:
            want = 20.0 - g5x[0] - 10.0
            s = h.wait_until(lambda: (lambda s: s if abs(s.g92_offset[0] - want) < 1e-6 else None)(
                h.stat()), 3.0, "stat.g92_offset X {:g}".format(want),
                detail=lambda: str(tuple(h.stat().g92_offset)[:3]))
            vm = h.state()
            dro = h.dro(vm)
            self.assertIs(h.motion(vm)["g92_active"], True)
            self.assertAlmostEqual(dro["X"].machine, 20.0, places=3)
            self.assertAlmostEqual(dro["X"].work, 10.0, places=3)
            self.assertAlmostEqual(dro["X"].work, dro["X"].machine - 10.0 - g5x[0], places=3)
            # Every axis by AXIS's formula, from LinuxCNC's own numbers (no
            # tool offset or rotation in the sim: both checked).
            self.assertEqual(float(s.rotation_xy), 0.0)
            self.assertEqual(tuple(s.tool_offset)[:3], (0.0, 0.0, 0.0))
            for i, axis in enumerate(h.AXES):
                with self.subTest(axis=axis):
                    self.assertAlmostEqual(
                        dro[axis].work,
                        float(s.actual_position[i]) - g5x[i] - float(s.g92_offset[i]), places=3)
        finally:
            h.mdi("G92.1")
        h.wait_until(lambda: all(abs(v) < 1e-9 for v in h.stat().g92_offset), 3.0,
                     "G92 cleared", detail=lambda: str(tuple(h.stat().g92_offset)[:3]))
        vm = h.state()
        dro = h.dro(vm)
        self.assertIs(h.motion(vm)["g92_active"], False)
        for i, axis in enumerate(h.AXES):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(dro[axis].work, dro[axis].machine - g5x[i], places=3)


if __name__ == "__main__":
    import unittest

    unittest.main()
