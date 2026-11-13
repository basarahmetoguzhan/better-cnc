"""Every hardware number in the UI must come from the config file.

The rule being enforced: a number duplicated between config and UI without a
test tying them together is a bug waiting to happen. So these tests read
configs/test-rig/test-rig.ini with their own parser and assert the rendered HTML
agrees. No expected value is written down twice.

The INI's measured values are tied the other way too: to the commissioning
record they came from (TestIniAgainstTheRigRecord).
"""

from __future__ import annotations

import json
import os
import re
import unittest

from helpers import REPO_ROOT, TEST_RIG_INI, html, ini_raw_values, session, view

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
        """X and Y were measured separately (Faz 3) and differ. If the UI ever
        shows them as equal, something has averaged or hardcoded them."""
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


#: How test-rig.ini names the measurement record it cites as "JSON" (its
#: header). Read from the INI, so a newer record is the one checked as soon
#: as the INI cites it instead.
_RECORD_RE = re.compile(r"docs/board-dumps/rig-measurements-[0-9-]+\.json")


class TestIniAgainstTheRigRecord(unittest.TestCase):
    """SCALE and the travel limits were measured on the rig on 2026-11-12
    (Faz 3 and 4); the raw numbers are in the record under docs/board-dumps/.
    The INI states the results and cites the record, and nothing else ties
    the two: a SCALE or a limit edited in one file only would go unnoticed.

    The INI is read with helpers' own parser; the record with json. Each
    result is also recomputed from the record's raw numbers, so the record
    cannot hold a typed-in figure either."""

    #: (record key, joint, [AXIS_*] section)
    AXES = (("x", 0, "AXIS_X"), ("y", 1, "AXIS_Y"), ("z", 2, "AXIS_Z"))

    @classmethod
    def setUpClass(cls):
        with open(TEST_RIG_INI, "r", encoding="utf-8") as fh:
            cls.cited = sorted(set(_RECORD_RE.findall(fh.read())))
        cls.ini = ini_raw_values()
        cls.record = None
        if len(cls.cited) == 1 and os.path.isfile(os.path.join(REPO_ROOT, cls.cited[0])):
            with open(os.path.join(REPO_ROOT, cls.cited[0]), "r", encoding="utf-8") as fh:
                cls.record = json.load(fh)

    def setUp(self):
        self.assertEqual(len(self.cited), 1, "test-rig.ini should cite one record: {}".format(self.cited))
        self.assertIsNotNone(self.record, "{} missing".format(self.cited[0]))

    def _limit(self, section: str, key: str) -> float:
        return float(self.ini["{}.{}".format(section, key)])

    def test_the_record_describes_this_ini(self):
        self.assertEqual(self.record["config"]["ini"], "configs/test-rig/test-rig.ini")
        self.assertEqual(self.record["units"]["scale"], "steps/mm")
        self.assertEqual(self.record["units"]["position"], self.ini["TRAJ.LINEAR_UNITS"])

    def test_scale_is_the_recorded_result(self):
        final = self.record["faz3_scale"]["final_scale"]
        for axis, joint, _ in self.AXES:
            with self.subTest(axis=axis):
                self.assertEqual(self._limit("JOINT_{}".format(joint), "SCALE"), final[axis])

    def test_recorded_scale_follows_from_the_recorded_moves(self):
        """new = previous x commanded / measured (the record's formula), in
        both rounds; the rough round started from GRBL's values and the fine
        round from the rough round's interim ones."""
        faz3 = self.record["faz3_scale"]
        rough, fine = faz3["rough_round"], faz3["fine_round"]
        self.assertEqual(rough["previous_scale"], {a: faz3["grbl_scale"][a] for a in "xyz"})
        self.assertEqual(fine["previous_scale"], rough["interim_scale"])
        for name, rnd in (("rough", rough), ("fine", fine)):
            for run in rnd["runs"]:
                with self.subTest(round=name, axis=run["axis"], commanded=run["commanded_mm"]):
                    computed = rnd["previous_scale"][run["axis"]] * run["commanded_mm"] / run["measured_mm"]
                    self.assertEqual(round(computed, 3), run["computed_scale"])
        by_axis = {run["axis"]: run["computed_scale"] for run in fine["runs"]}
        self.assertEqual(by_axis, faz3["final_scale"])

    def test_the_recorded_scale_passed_its_gate(self):
        """Gate 3: a move commanded with the final SCALE measured within the
        tolerance, on both Y gantry sides."""
        gate = self.record["faz3_scale"]["verification_gate3"]
        self.assertTrue(gate["passed"])
        self.assertEqual(sorted({run["axis"] for run in gate["runs"]}), ["x", "y", "z"])
        for run in gate["runs"]:
            with self.subTest(axis=run["axis"], side=run.get("side", "")):
                error = (run["measured_mm"] - run["commanded_mm"]) / run["commanded_mm"] * 100
                self.assertEqual(round(error, 2), run["error_pct"])
                self.assertLessEqual(abs(error), gate["tolerance_pct"])

    def test_travel_limits_are_the_recorded_ones(self):
        """[JOINT_n] and [AXIS_*] alike: the INI keeps them identical."""
        limits = self.record["faz4_travel"]["soft_limits"]
        for axis, joint, axis_section in self.AXES:
            for key in ("MIN_LIMIT", "MAX_LIMIT"):
                for section in ("JOINT_{}".format(joint), axis_section):
                    with self.subTest(section=section, key=key):
                        self.assertEqual(self._limit(section, key), limits[axis][key.lower()])
            with self.subTest(axis=axis, usable=True):
                self.assertAlmostEqual(
                    limits[axis]["max_limit"] - limits[axis]["min_limit"], limits[axis]["usable_mm"],
                    places=6,
                )

    def test_every_limit_stays_inside_the_recorded_switches(self):
        """In the machine frame (origin at the home switch's release edge),
        X and Y between their two switch trips, Z between the table and its
        top trip - each at least the record's minimum margin inside."""
        faz4 = self.record["faz4_travel"]
        frame, margins = faz4["machine_frame"], faz4["computed_margins_mm"]
        ends = {
            "x": (frame["x"]["min_trip"], frame["x"]["max_trip"], "min_side", "max_side"),
            "y": (frame["y"]["min_trip"], frame["y"]["max_trip"], "min_side", "max_side"),
            "z": (frame["z"]["table_approx"], frame["z"]["top_trip"], "to_table", "to_top_trip"),
        }
        for axis, joint, _ in self.AXES:
            low_end, high_end, low_key, high_key = ends[axis]
            below = self._limit("JOINT_{}".format(joint), "MIN_LIMIT") - low_end
            above = high_end - self._limit("JOINT_{}".format(joint), "MAX_LIMIT")
            with self.subTest(axis=axis):
                self.assertGreaterEqual(round(below, 6), faz4["minimum_margin_mm"])
                self.assertGreaterEqual(round(above, 6), faz4["minimum_margin_mm"])
                self.assertAlmostEqual(below, margins[axis][low_key], places=6)
                self.assertAlmostEqual(above, margins[axis][high_key], places=6)

    def test_machine_frame_trips_are_the_session_trips_shifted(self):
        """Homing moves the origin, not the switches: trip to trip, X and Y
        measure the same in both frames."""
        faz4 = self.record["faz4_travel"]
        session_frame, machine = faz4["session_frame"], faz4["machine_frame"]
        for axis, low, high in (("x", "left_trip", "right_trip"), ("y", "front_trip", "rear_trip")):
            with self.subTest(axis=axis):
                self.assertAlmostEqual(
                    machine[axis]["max_trip"] - machine[axis]["min_trip"],
                    session_frame[axis][high] - session_frame[axis][low],
                    places=6,
                )


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
        ini = ini_raw_values()
        leaks = [ini["JOINT_{}.SCALE".format(j)] for j in (0, 1, 2)]
        leaks.append("{:,} ns".format(int(float(ini["JOINT_0.STEPLEN"]))).replace(",", " "))
        for leaked in leaks:
            with self.subTest(value=leaked):
                self.assertNotIn(leaked, markup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
