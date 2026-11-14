"""The baseline: what the UI does today against a real LinuxCNC 2.9.9, end to
end. Every step must pass (no expected failures in this module).

One scenario on one fresh LinuxCNC, step by step (harness.OrderedSteps):
the sim INI's program directory, the switches read on Alarmlar, reset +
ON, Home All Z -> Y -> X, a 1 mm jog each way on each axis, MDI inside and
outside the soft limits, feed override, the software estop during a move,
the board link lost and back. About 25 s of tests, 30 s with
`sim/sim.sh up` and `down` (2026-11-13; Home All alone 14 s).
"""

from __future__ import annotations

import os
import time

import harness as h


class Baseline(h.OrderedSteps):

    def test_01_program_prefix_is_the_repos_nc_files(self):
        """LinuxCNC runs the sim INI, whose PROGRAM_PREFIX names the repo's
        nc_files/ (sim/make_sim_ini.py), not /nc_files."""
        lc = h._lc()
        ini = lc.ini(h.stat().ini_filename)
        prefix = ini.find("DISPLAY", "PROGRAM_PREFIX")
        self.assertEqual(prefix, os.path.join(h.REPO, "nc_files"))
        self.assertTrue(os.path.isfile(os.path.join(prefix, "faz8-kare.ngc")), prefix)

    def test_02_switches_read_live_on_alarmlar(self):
        """The rig's five .in_not pins exist in the sim (sim/hm2_link_stub.py)
        and carry the switch signals (sim/test-rig-sim.hal): Alarmlar shows
        each switch's state instead of 'okunamadı'."""
        def inputs():
            return {i["label"]: i["state_label"] for i in h.state("alarmlar")["alarmlar"]["inputs"]}

        self.assertEqual(set(inputs().values()), {"serbest"}, inputs())
        h.press("Z+")   # motors start at 0, so Z's top switch can be put there
        try:
            self.assertEqual(inputs()["Z üst"], "basılı")
            # Motion sees it too; its status reaches stat a task cycle or
            # two after the HAL pin changed.
            h.wait_until(lambda: h.hard_limits()[2] == (False, True), 1.0,
                         "joint 2's max_hard_limit", detail=lambda: str(h.hard_limits()))
        finally:
            self.assertEqual(h.restore_switches(), [])
        self.assertEqual(set(inputs().values()), {"serbest"}, inputs())
        h.wait_until(lambda: h.hard_limits() == ((False, False),) * 3, 1.0,
                     "every hard limit flag clear", detail=lambda: str(h.hard_limits()))

    def test_03_reset_turns_the_machine_on(self):
        s = h.reset_on()
        self.assertEqual(h.homed(s), (False, False, False))

    def test_04_home_all_z_then_y_then_x(self):
        order = h.home_all()
        self.assertEqual(order, [2, 1, 0], "HOME_SEQUENCE Z 0, Y 1, X 2")
        s = h.wait_still()
        self.assertTrue(h.near(h.position(s), h.HOME_POSITION), h.describe(s))
        for j in range(3):
            self.assertTrue(h.get_bit("joint.{}.homed".format(j)), j)
        self.assertTrue(h.state()["kontrol"]["homing"]["all_homed"])

    def test_05_jog_one_mm_each_way_on_each_axis(self):
        for axis in h.AXES:
            for direction in (1, -1):
                with self.subTest(axis=axis, direction=direction):
                    h.jog_and_wait(axis, direction, 1.0)
        self.assertTrue(h.near(h.position(), h.HOME_POSITION), h.describe())

    def test_06_mdi_inside_the_limits_moves(self):
        h.mdi("G0 X50 Y50")
        s = h.wait_position((50.0, 50.0, -5.0), h.move_time(64.0, 10.0))
        h.wait_until(lambda: h.stat().interp_state == h._lc().INTERP_IDLE, 2.0, "interp IDLE",
                     detail=h.describe)
        self.assertEqual(h.homed(s), (True, True, True))

    def test_07_mdi_outside_the_limits_is_refused_by_linuxcnc(self):
        """Z0 is above Z's MAX_LIMIT -3: motion refuses the move
        (reference/linuxcnc/src/emc/motion/command.c:213-224), and the
        refusal reaches the alarm log."""
        before = h.position()
        mark = h.alarm_mark()
        h.mdi("G0 Z0")
        h.wait_alarm("would exceed Z's positive limit", mark)
        s = h.wait_still()
        self.assertTrue(h.near(h.position(s), before), (before, h.describe(s)))

    def test_08_feed_override(self):
        h.post("feed", "50")
        h.wait_until(lambda: abs(h.stat().feedrate - 0.5) < 1e-9, 2.0, "stat.feedrate 0.5",
                     detail=lambda: str(h.stat().feedrate))
        self.assertEqual(h.state()["kontrol"]["feed_override"], 50)
        h.post("feed", "100")
        h.wait_until(lambda: abs(h.stat().feedrate - 1.0) < 1e-9, 2.0, "stat.feedrate 1.0",
                     detail=lambda: str(h.stat().feedrate))

    def test_09_estop_stops_an_mdi_move_and_keeps_homes(self):
        """The software estop mid-move: motion stopped and ESTOP_RESET (the
        rig's estop chain leaves the latch closed, adapter.software_estop)
        within ESTOP_STOP_S of the POST. Homes survive: no VOLATILE_HOME
        (reference/linuxcnc/src/emc/task/emctask.cc:363)."""
        h.mdi("G0 X300")
        h.wait_until(lambda: h.stat().current_vel > 0, 3.0, "the move to start", detail=h.describe)
        time.sleep(0.3)
        t0 = time.monotonic()
        h.post("software_estop")

        def stopped():
            s = h.stat()
            return s if s.task_state != h.STATE_ON and float(s.current_vel) == 0.0 else None
        s = h.wait_until(stopped, 2.0, "the estop to stop the move", every=0.005, detail=h.describe)
        elapsed = time.monotonic() - t0
        self.assertLessEqual(elapsed, h.ESTOP_STOP_S, h.describe(s))
        self.assertEqual(int(s.task_state), h.STATE_ESTOP_RESET, h.describe(s))
        self.assertLess(h.position(s)[0], 300.0)
        self.assertEqual(h.homed(s), (True, True, True))
        s = h.reset_on()
        self.assertEqual(h.homed(s), (True, True, True))

    def test_10_link_down_estops_on_one_request(self):
        """A cable pull as the real driver plays it: io_error only, LinuxCNC
        stays ON (sim/hm2_link_stub.py). The UI's next snapshot sends ESTOP
        (adapter._stop_on_link_fault): one GET is enough."""
        h.setp("hm2_7i92.0.sim-link-down", True)
        h.wait_until(lambda: h.get_bit("hm2_7i92.0.io_error"), 2.0, "io_error")
        vm = h.state()   # the one request
        s = h.wait_task_state(h.STATE_ESTOP_RESET, 1.0)
        self.assertTrue(any("Kartla bağlantı koptu" in t for t in h.alarm_texts(vm)),
                        h.alarm_texts(vm)[:5])
        self.assertEqual(h.homed(s), (True, True, True))

    def test_11_link_back_and_reset_unhomes(self):
        """Cable back, «Yazılım sıfırla»: the link faults cleared, every joint
        unhomed (the board's step generators kept counting while it was cut
        off), the machine ON."""
        h.setp("hm2_7i92.0.sim-link-down", False)
        mark = h.alarm_mark()
        s = h.reset_on()
        self.assertEqual(h.homed(s), (False, False, False))
        self.assertFalse(h.get_bit("hm2_7i92.0.io_error"))
        self.assertFalse(h.get_bit("hm2_7i92.0.watchdog.has_bit"))
        h.wait_alarm("referanslar silindi", mark)


if __name__ == "__main__":
    import unittest

    unittest.main()
