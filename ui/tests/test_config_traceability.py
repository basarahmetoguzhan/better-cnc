"""Every hardware number in the UI must come from the config file.

The rule being enforced: a number duplicated between config and UI without a
test tying them together is a bug waiting to happen. So these tests read
configs/test-rig/test-rig.ini with their own parser and assert the rendered HTML
agrees. No expected value is written down twice.
"""

from __future__ import annotations

import re
import unittest

from helpers import TEST_RIG_INI, html, ini_raw_values, session, view

from bettercnc.configmodel import load_machine_config
from bettercnc.profiles import load_profile


class TestScaleFromIni(unittest.TestCase):
    """SCALE is the number that decides how far the machine moves."""

    def setUp(self):
        self.ini = ini_raw_values()
        self.vm = view("test-rig", "makine")

    def test_scale_matches_ini_for_every_axis(self):
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                raw = self.ini["JOINT_{}.SCALE".format(joint)]
                row = next(a for a in self.vm["makine"]["axes"] if a["axis"] == axis)
                self.assertIn(
                    raw, row["scale"],
                    "UI shows {} for {} but INI says {}".format(row["scale"], axis, raw),
                )

    def test_scale_reaches_the_rendered_html(self):
        markup = html("test-rig", "makine")
        for joint in (0, 1, 2):
            raw = self.ini["JOINT_{}.SCALE".format(joint)]
            with self.subTest(scale=raw):
                self.assertIn(raw, markup)

    def test_scale_values_are_distinct(self):
        """X and Y differ by 1.2% from real calibration. If the UI ever shows
        them as equal, something has averaged or hardcoded them."""
        vals = {a["axis"]: a["scale"] for a in self.vm["makine"]["axes"]}
        self.assertNotEqual(vals["X"], vals["Y"])

    def test_no_scale_is_the_prototype_value(self):
        """The prototype said 80.000 / 80.000 / 400.000 steps/mm."""
        markup = html("test-rig", "makine")
        for wrong in ("80.000 adım/mm", "400.000 adım/mm"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)


class TestVelocityAndAccelFromIni(unittest.TestCase):
    def setUp(self):
        self.ini = ini_raw_values()
        self.vm = view("test-rig", "makine")

    def test_velocity_matches_ini(self):
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                raw = float(self.ini["JOINT_{}.MAX_VELOCITY".format(joint)])
                row = next(a for a in self.vm["makine"]["axes"] if a["axis"] == axis)
                shown = float(re.match(r"([\d.]+)", row["velocity"]).group(1))
                self.assertAlmostEqual(shown, raw, places=6)

    def test_acceleration_matches_ini(self):
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                raw = float(self.ini["JOINT_{}.MAX_ACCELERATION".format(joint)])
                row = next(a for a in self.vm["makine"]["axes"] if a["axis"] == axis)
                shown = float(re.match(r"([\d.]+)", row["acceleration"]).group(1))
                self.assertAlmostEqual(shown, raw, places=6)

    def test_velocity_is_not_the_prototype_value(self):
        """The prototype said 40/40/8 mm/s - roughly 13x too fast."""
        markup = html("test-rig", "makine")
        for wrong in ("40 mm/s", "500 mm/s²", "300 mm/s²"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)


class TestStepTimingFromIni(unittest.TestCase):
    """steplen/stepspace/dirsetup/dirhold. Too short and the TB6560's
    optocouplers drop pulses - which is what the prototype's 2000 ns would do."""

    def setUp(self):
        self.ini = ini_raw_values()
        self.cfg = load_machine_config(load_profile("test-rig"))

    def test_timing_matches_ini(self):
        for key, attr in (
            ("STEPLEN", "steplen_ns"),
            ("STEPSPACE", "stepspace_ns"),
            ("DIRSETUP", "dirsetup_ns"),
            ("DIRHOLD", "dirhold_ns"),
        ):
            with self.subTest(key=key):
                raw = int(float(self.ini["JOINT_0.{}".format(key)]))
                self.assertEqual(getattr(self.cfg.timing, attr), raw)

    def test_timing_reaches_the_html(self):
        markup = html("test-rig", "makine")
        for key in ("STEPLEN", "DIRSETUP"):
            raw = int(float(self.ini["JOINT_0.{}".format(key)]))
            # rendered with a space as the thousands separator
            pretty = "{:,}".format(raw).replace(",", " ")
            with self.subTest(key=key, value=pretty):
                self.assertIn(pretty, markup)

    def test_all_joints_share_the_timing_shown(self):
        """The UI shows one timing block. Assert the INI really is uniform, so
        that block is not quietly hiding a per-axis difference."""
        for key in ("STEPLEN", "STEPSPACE", "DIRSETUP", "DIRHOLD"):
            vals = {
                self.ini["JOINT_{}.{}".format(j, key)] for j in (0, 1, 2)
            }
            with self.subTest(key=key):
                self.assertEqual(len(vals), 1, "{} differs between joints".format(key))

    def test_prototype_timing_is_absent(self):
        markup = html("test-rig", "makine")
        for wrong in ("2 000 ns", "20 000 ns"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)


class TestBoardAndConfigStrings(unittest.TestCase):
    def setUp(self):
        self.ini = ini_raw_values()
        self.vm = view("test-rig", "makine")

    def test_hal_prefix_derives_from_ini_board(self):
        board = self.ini["HOSTMOT2.BOARD"]
        expected = "hm2_{}.0".format(board)
        rows = {r["label"]: r["value"] for r in self.vm["makine"]["board_rows"]}
        self.assertEqual(rows["HAL öneki"], expected)

    def test_board_ip_from_ini(self):
        self.assertIn(self.ini["HOSTMOT2.BOARD_IP"], self.vm["link"]["rows"][0]["value"])

    def test_hm2_config_string_from_ini(self):
        rows = {r["label"]: r["value"] for r in self.vm["makine"]["config_rows"]}
        self.assertEqual(
            rows["hm2 config"], self.ini["HOSTMOT2.CONFIG"].strip('"')
        )

    def test_servo_period_from_ini(self):
        ns = float(self.ini["EMCMOT.SERVO_PERIOD"])
        rows = {r["label"]: r["value"] for r in self.vm["makine"]["config_rows"]}
        self.assertEqual(rows["Servo döngü"], "{:g} ms".format(ns / 1e6))

    def test_kinematics_and_joints_from_ini(self):
        rows = {r["label"]: r["value"] for r in self.vm["makine"]["config_rows"]}
        self.assertEqual(rows["Kinematik"], self.ini["KINS.KINEMATICS"])
        self.assertEqual(rows["Joint sayısı"], self.ini["KINS.JOINTS"])


class TestHomeSequenceFromIni(unittest.TestCase):
    def setUp(self):
        self.ini = ini_raw_values()

    def test_home_sequence_read_not_assumed(self):
        cfg = load_machine_config(load_profile("test-rig"))
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                raw = int(float(self.ini["JOINT_{}.HOME_SEQUENCE".format(joint)]))
                self.assertEqual(cfg.axis(axis).home_sequence, raw)

    def test_displayed_order_follows_home_sequence(self):
        vm = view("test-rig", "kontrol")
        order = [
            (int(float(self.ini["JOINT_{}.HOME_SEQUENCE".format(j)])), a)
            for j, a in ((0, "X"), (1, "Y"), (2, "Z"))
        ]
        expected = " → ".join(a for _, a in sorted(order))
        self.assertEqual(vm["kontrol"]["homing"]["order_label"], expected)

    def test_home_ignore_limits_read_from_ini(self):
        cfg = load_machine_config(load_profile("test-rig"))
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                raw = self.ini["JOINT_{}.HOME_IGNORE_LIMITS".format(joint)]
                self.assertEqual(
                    cfg.axis(axis).home_ignore_limits, raw.strip().upper() == "YES"
                )


class TestStepRateAgainstDriverCeiling(unittest.TestCase):
    """SCALE x MAX_VELOCITY is the step frequency the TB6560 has to swallow.
    docs/09-tb6560-drivers.md:77 puts the ceiling at 15 kHz."""

    def test_computed_from_ini_not_hardcoded(self):
        ini = ini_raw_values()
        cfg = load_machine_config(load_profile("test-rig"))
        for joint, axis in ((0, "X"), (1, "Y"), (2, "Z")):
            with self.subTest(axis=axis):
                expected = float(ini["JOINT_{}.SCALE".format(joint)]) * float(
                    ini["JOINT_{}.MAX_VELOCITY".format(joint)]
                )
                self.assertAlmostEqual(
                    cfg.axis(axis).max_step_rate_hz, expected, places=4
                )

    def test_current_config_is_under_the_ceiling(self):
        from bettercnc.viewmodel import TB6560_STEP_CEILING_HZ

        cfg = load_machine_config(load_profile("test-rig"))
        for a in cfg.axes:
            with self.subTest(axis=a.axis):
                self.assertLess(a.max_step_rate_hz, TB6560_STEP_CEILING_HZ)


class TestNoConfigMeansNoNumbers(unittest.TestCase):
    """The marble profile has no INI. It must render an absence, not zeros."""

    def test_marble_config_absent(self):
        cfg = load_machine_config(load_profile("marble"))
        self.assertFalse(cfg.present)
        self.assertEqual(cfg.axes, [])
        self.assertIsNone(cfg.timing)
        self.assertTrue(cfg.absence_reason.strip())

    def test_marble_machine_screen_says_so(self):
        markup = html("marble", "makine")
        self.assertIn("config yok", markup)

    def test_marble_shows_no_axis_numbers(self):
        vm = view("marble", "makine")
        self.assertEqual(vm["makine"]["axes"], [])
        self.assertEqual(vm["makine"]["timing_rows"], [])

    def test_marble_does_not_borrow_test_rig_numbers(self):
        markup = html("marble", "makine")
        for leaked in ("833.300", "823.500", "1250.000", "10 000 ns"):
            with self.subTest(value=leaked):
                self.assertNotIn(leaked, markup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
