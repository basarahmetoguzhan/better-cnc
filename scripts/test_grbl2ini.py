#!/usr/bin/env python3
"""Unit tests for grbl2ini.py.

Run:  python3 -m unittest discover -s scripts -v
  or: python3 scripts/test_grbl2ini.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import grbl2ini  # noqa: E402


# A synthetic but realistic GRBL 1.1 dump. Values chosen so every conversion has an
# exact, hand-checkable answer:
#   $0=10      -> STEPLEN 10000 ns
#   $24=120    -> HOME_LATCH_VEL  2.0 mm/s
#   $25=1500   -> HOME_SEARCH_VEL 25.0 mm/s
#   $110=2400  -> MAX_VELOCITY 40.0 mm/s
#   $111=1800  -> 30.0 ;  $112=600 -> 10.0
FULL_DUMP = """\
Grbl 1.1h ['$' for help]
$0=10
$1=25
$2=0
$3=5
$4=0
$5=0
$6=0
$10=1
$11=0.010
$12=0.002
$13=0
$20=0
$21=1
$22=1
$23=3
$24=120.000
$25=1500.000
$26=250
$27=1.000
$30=1000
$31=0
$32=0
$100=250.000
$101=250.000
$102=800.000
$110=2400.000
$111=1800.000
$112=600.000
$120=500.000
$121=500.000
$122=100.000
$130=200.000
$131=300.000
$132=80.000
ok
"""


class TestParsing(unittest.TestCase):
    def test_parses_all_settings(self):
        d = grbl2ini.parse_dump(FULL_DUMP)
        self.assertEqual(d.get(0), 10.0)
        self.assertEqual(d.get(100), 250.0)
        self.assertEqual(d.get(132), 80.0)
        self.assertEqual(d.malformed, [])

    def test_ignores_banner_and_ok(self):
        d = grbl2ini.parse_dump(FULL_DUMP)
        # "Grbl 1.1h [...]" and "ok" are counted as ignored, not malformed.
        self.assertEqual(d.ignored, 2)
        self.assertEqual(d.malformed, [])

    def test_tolerates_trailing_paren_comments(self):
        d = grbl2ini.parse_dump("$0=10 (Step pulse time, microseconds)\n"
                                "$110=2400.000 (X Max rate, mm/min)\n")
        self.assertEqual(d.get(0), 10.0)
        self.assertEqual(d.get(110), 2400.0)
        self.assertEqual(d.malformed, [])

    def test_tolerates_semicolon_and_hash_comments(self):
        d = grbl2ini.parse_dump("$100=250.0 ; steps per mm\n$101=250.0 # ditto\n")
        self.assertEqual(d.get(100), 250.0)
        self.assertEqual(d.get(101), 250.0)
        self.assertEqual(d.malformed, [])

    def test_tolerates_whitespace_variants(self):
        d = grbl2ini.parse_dump("   $0 = 10   \n\t$110=2400\n")
        self.assertEqual(d.get(0), 10.0)
        self.assertEqual(d.get(110), 2400.0)
        self.assertEqual(d.malformed, [])

    def test_negative_and_exponent_values(self):
        d = grbl2ini.parse_dump("$130=-200.5\n$131=1e2\n")
        self.assertEqual(d.get(130), -200.5)
        self.assertEqual(d.get(131), 100.0)

    def test_malformed_lines_recorded_not_raised(self):
        d = grbl2ini.parse_dump("$0=10\n"
                                "$oops=hello\n"
                                "$=\n"
                                "$110=\n"
                                "$111=abc\n"
                                "$112=2400\n")
        self.assertEqual(d.get(0), 10.0)
        self.assertEqual(d.get(112), 2400.0)
        # Four bad lines, all captured with their line numbers.
        self.assertEqual(len(d.malformed), 4)
        self.assertEqual([n for n, _ in d.malformed], [2, 3, 4, 5])

    def test_empty_input_does_not_raise(self):
        d = grbl2ini.parse_dump("")
        self.assertEqual(d.settings, {})
        self.assertEqual(d.malformed, [])

    def test_garbage_input_does_not_raise(self):
        d = grbl2ini.parse_dump("this is not a grbl dump at all\n\n\x00binary\n")
        self.assertEqual(d.settings, {})


class TestUnitConversions(unittest.TestCase):
    """The conversions that cause real-world breakage. Asserted numerically."""

    def setUp(self):
        self.d = grbl2ini.parse_dump(FULL_DUMP)

    # --- Trap 1: mm/min -> mm/sec, divide by 60 -----------------------------------------
    def test_max_rate_divided_by_60(self):
        v, why = grbl2ini.conv_per_minute_to_per_second(self.d, 110, "max rate")
        self.assertAlmostEqual(v, 40.0, places=9)   # 2400 / 60
        self.assertIn("/ 60", why)
        self.assertIn("$110=2400", why)

    def test_max_rate_divided_by_60_all_axes(self):
        for num, expected in ((110, 40.0), (111, 30.0), (112, 10.0)):
            v, _ = grbl2ini.conv_per_minute_to_per_second(self.d, num, "max rate")
            self.assertAlmostEqual(v, expected, places=9,
                                   msg="${} should convert to {}".format(num, expected))

    def test_the_classic_2000_mm_per_min_case(self):
        """The canonical example: 2000 mm/min -> 33.333 mm/s."""
        d = grbl2ini.parse_dump("$110=2000.000\n")
        v, _ = grbl2ini.conv_per_minute_to_per_second(d, 110, "max rate")
        self.assertAlmostEqual(v, 2000.0 / 60.0, places=9)
        self.assertAlmostEqual(v, 33.333333333, places=6)

    def test_not_divided_by_3600_or_left_alone(self):
        """Guard against the two plausible wrong answers."""
        v, _ = grbl2ini.conv_per_minute_to_per_second(self.d, 110, "max rate")
        self.assertNotAlmostEqual(v, 2400.0, places=3)          # forgot to convert
        self.assertNotAlmostEqual(v, 2400.0 / 3600.0, places=3)  # divided twice

    # --- Trap 2: homing rates also /60, and feed<->latch, seek<->search -----------------
    def test_homing_seek_divided_by_60(self):
        v, why = grbl2ini.conv_per_minute_to_per_second(self.d, 25, "homing seek")
        self.assertAlmostEqual(v, 25.0, places=9)   # 1500 / 60
        self.assertIn("/ 60", why)

    def test_homing_feed_divided_by_60(self):
        v, _ = grbl2ini.conv_per_minute_to_per_second(self.d, 24, "homing feed")
        self.assertAlmostEqual(v, 2.0, places=9)    # 120 / 60

    def test_seek_maps_to_search_and_feed_to_latch(self):
        """$25 (fast seek) -> HOME_SEARCH_VEL; $24 (slow feed) -> HOME_LATCH_VEL."""
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        # 25.0 is the seek-derived magnitude, 2.0 the feed-derived one.
        self.assertIn("HOME_SEARCH_VEL magnitude = 25", out)
        self.assertIn("HOME_LATCH_VEL  magnitude = 2", out)
        # And the search magnitude must exceed the latch magnitude.
        self.assertGreater(1500.0 / 60.0, 120.0 / 60.0)

    # --- Trap 3: microseconds -> nanoseconds, multiply by 1000 --------------------------
    def test_step_pulse_multiplied_by_1000(self):
        v, why = grbl2ini.conv_microseconds_to_nanoseconds(self.d, 0, "step pulse")
        self.assertAlmostEqual(v, 10000.0, places=9)   # 10 us -> 10000 ns
        self.assertIn("x 1000", why)
        self.assertIn("$0=10", why)

    def test_step_pulse_not_divided_or_left_alone(self):
        v, _ = grbl2ini.conv_microseconds_to_nanoseconds(self.d, 0, "step pulse")
        self.assertNotAlmostEqual(v, 10.0, places=3)      # forgot to convert
        self.assertNotAlmostEqual(v, 0.01, places=6)      # converted the wrong way

    def test_steplen_appears_in_output_as_nanoseconds(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("STEPLEN = 10000", out)

    # --- Trap 4: acceleration passes through UNCHANGED ---------------------------------
    def test_acceleration_unchanged(self):
        v, why = grbl2ini.conv_passthrough(self.d, 120, "acceleration", "mm/sec^2")
        self.assertAlmostEqual(v, 500.0, places=9)
        self.assertIn("unchanged", why)

    def test_acceleration_not_divided_by_60(self):
        """The mirror-image mistake: 'helpfully' converting acceleration too."""
        v, _ = grbl2ini.conv_passthrough(self.d, 120, "acceleration", "mm/sec^2")
        self.assertNotAlmostEqual(v, 500.0 / 60.0, places=6)
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("MAX_ACCELERATION = 500", out)
        self.assertNotIn("MAX_ACCELERATION = 8.33", out)

    # --- Trap 5: steps/mm magnitude unchanged ------------------------------------------
    def test_steps_per_mm_magnitude_unchanged(self):
        v, why = grbl2ini.conv_passthrough(self.d, 100, "steps/mm", "steps/mm")
        self.assertAlmostEqual(v, 250.0, places=9)
        self.assertIn("unchanged", why)

    def test_scale_appears_with_original_magnitude(self):
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        self.assertIn("SCALE = 250", out)
        self.assertIn("SCALE = 800", out)   # $102 for Z


class TestMissingSettings(unittest.TestCase):
    def test_missing_setting_yields_todo_not_a_value(self):
        # Only steps/mm present. Everything else must become a TODO.
        d_text = "$100=250.000\n"
        out = grbl2ini.convert(d_text, joints=1)
        self.assertIn("SCALE = 250", out)
        self.assertIn("# TODO MAX_VELOCITY", out)
        self.assertIn("# TODO MAX_ACCELERATION", out)
        self.assertIn("# TODO STEPLEN", out)

    def test_missing_setting_reason_names_the_setting(self):
        out = grbl2ini.convert("$100=250.000\n", joints=1)
        self.assertIn("$110", out)   # the reason cites the absent setting number
        self.assertIn("not present in dump", out)

    def test_never_emits_a_fabricated_value(self):
        """With an almost-empty dump, no assignment line may carry a number
        that did not come from the input."""
        out = grbl2ini.convert("$100=250.000\n", joints=1)
        assignments = [ln for ln in out.splitlines()
                       if "=" in ln and not ln.lstrip().startswith("#")]
        # The only real assignments should be TYPE and SCALE.
        keys = [ln.split("=")[0].strip() for ln in assignments]
        self.assertEqual(sorted(keys), ["SCALE", "TYPE"])

    def test_completely_empty_dump_still_renders(self):
        out = grbl2ini.convert("Grbl 1.1h ['$' for help]\nok\n", joints=3)
        self.assertIn("settings parsed : 0", out)
        self.assertIn("MUST BE FILLED IN BY HAND", out)
        # No stray assignments other than TYPE for each joint.
        assignments = [ln for ln in out.splitlines()
                       if "=" in ln and not ln.lstrip().startswith("#")]
        self.assertEqual(len(assignments), 3)
        for ln in assignments:
            self.assertTrue(ln.startswith("TYPE ="), ln)

    def test_partial_axis_coverage(self):
        """X fully specified, Y and Z absent - X must convert, Y/Z must TODO."""
        text = "$100=250\n$110=2400\n$120=500\n$130=200\n"
        out = grbl2ini.convert(text, joints=3)
        self.assertIn("MAX_VELOCITY = 40", out)          # X converted
        self.assertIn("$111 (max rate) not present", out)  # Y missing
        self.assertIn("$112 (max rate) not present", out)  # Z missing


class TestWarningBlock(unittest.TestCase):
    def test_lists_required_human_inputs(self):
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        for key in ("FERROR", "MIN_FERROR", "HOME_SEQUENCE",
                    "STEPSPACE", "DIRSETUP", "DIRHOLD"):
            self.assertIn(key, out, "warning block must mention {}".format(key))

    def test_mentions_home_vel_sign_is_unknown(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("sign unknown", out)

    def test_reports_malformed_lines(self):
        out = grbl2ini.convert("$0=10\n$bogus=x\n", joints=1)
        self.assertIn("MALFORMED", out)
        self.assertIn("$bogus=x", out)

    def test_reports_hard_limit_and_homing_state(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("hard limits ($21)", out)
        self.assertIn("homing cycle ($22)", out)
        self.assertIn("ENABLED", out)

    def test_flags_both_disabled_case(self):
        out = grbl2ini.convert("$20=0\n$21=0\n$22=0\n$100=250\n", joints=1)
        self.assertIn("DISABLED", out)
        self.assertIn("switches may never have been wired", out)

    def test_notes_dir_invert_mask_when_set(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)   # $3=5 in the fixture
        self.assertIn("$3=5", out)
        self.assertIn("SCALE negated", out)

    def test_absent_informational_settings_reported_as_unknown(self):
        out = grbl2ini.convert("$100=250\n", joints=1)
        self.assertIn("NOT IN DUMP", out)


class TestStructure(unittest.TestCase):
    def test_emits_requested_joint_count(self):
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        for j in range(3):
            self.assertIn("[JOINT_{}]".format(j), out)
        self.assertNotIn("[JOINT_3]", out)

    def test_joints_argument_respected(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("[JOINT_0]", out)
        self.assertNotIn("[JOINT_1]", out)

    def test_emits_axis_sections_with_letters(self):
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        self.assertIn("[AXIS_X]", out)
        self.assertIn("[AXIS_Y]", out)
        self.assertIn("[AXIS_Z]", out)

    def test_four_joints_gives_axis_a(self):
        out = grbl2ini.convert(FULL_DUMP, joints=4)
        self.assertIn("[JOINT_3]", out)
        self.assertIn("[AXIS_A]", out)

    def test_converted_lines_carry_arithmetic_comment(self):
        """Every converted assignment must show its provenance and arithmetic.

        With joints=1 there are two MAX_VELOCITY lines - one in [JOINT_0] and one
        in [AXIS_X] - because LinuxCNC requires the limit in both sections. Both
        must carry the comment.
        """
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        vel_lines = [ln for ln in out.splitlines() if ln.startswith("MAX_VELOCITY")]
        self.assertEqual(len(vel_lines), 2, "expected one for JOINT_0 and one for AXIS_X")
        for ln in vel_lines:
            self.assertIn("#", ln)
            self.assertIn("$110=2400", ln)
            self.assertIn("/ 60", ln)
            self.assertIn("40", ln)

    def test_joint_and_axis_values_agree(self):
        """[JOINT_n] and [AXIS_x] must carry identical limits, in the same order.

        A config where the joint and axis limits disagree is broken, so compare
        the two sequences positionally rather than by counting (two axes may
        legitimately share a value).
        """
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        joint_vals, axis_vals, in_axis = [], [], False
        for ln in out.splitlines():
            if ln.startswith("[JOINT_"):
                in_axis = False
            elif ln.startswith("[AXIS_"):
                in_axis = True
            elif ln.startswith(("MAX_VELOCITY", "MAX_ACCELERATION")):
                key, rest = ln.split("=", 1)
                entry = (key.strip(), rest.split("#")[0].strip())
                (axis_vals if in_axis else joint_vals).append(entry)

        self.assertEqual(len(joint_vals), 6)   # 3 joints x 2 keys
        self.assertEqual(len(axis_vals), 6)    # 3 axes   x 2 keys
        self.assertEqual(joint_vals, axis_vals,
                         "joint and axis limits must match element-for-element")
        # Sanity-check the actual expected numbers from the fixture.
        self.assertEqual(joint_vals, [
            ("MAX_VELOCITY", "40"), ("MAX_ACCELERATION", "500"),
            ("MAX_VELOCITY", "30"), ("MAX_ACCELERATION", "500"),
            ("MAX_VELOCITY", "10"), ("MAX_ACCELERATION", "100"),
        ])

    def test_output_is_not_a_complete_ini(self):
        """Guard the design intent: fragments only, no [EMC]/[HAL]/[TRAJ]."""
        out = grbl2ini.convert(FULL_DUMP, joints=3)
        for section in ("[EMC]", "[HAL]", "[TRAJ]", "[KINS]", "[DISPLAY]"):
            self.assertNotIn(section, out)

    def test_travel_shows_both_sign_options(self):
        out = grbl2ini.convert(FULL_DUMP, joints=1)
        self.assertIn("home at minimum end", out)
        self.assertIn("home at maximum end", out)


class TestCli(unittest.TestCase):
    def test_rejects_zero_joints(self):
        with self.assertRaises(SystemExit):
            grbl2ini.main(["--joints", "0", "-"])

    def test_rejects_too_many_joints(self):
        with self.assertRaises(SystemExit):
            grbl2ini.main(["--joints", "99", "-"])

    def test_missing_file_returns_2(self):
        rc = grbl2ini.main(["/nonexistent/path/to/dump.txt"])
        self.assertEqual(rc, 2)


class TestFormatting(unittest.TestCase):
    def test_fmt_drops_trailing_zeros_for_integers(self):
        self.assertEqual(grbl2ini.fmt(250.0), "250")
        self.assertEqual(grbl2ini.fmt(10000.0), "10000")

    def test_fmt_keeps_precision_for_fractions(self):
        self.assertEqual(grbl2ini.fmt(2000.0 / 60.0), "33.3333")

    def test_fmt_handles_negatives(self):
        self.assertEqual(grbl2ini.fmt(-200.0), "-200")


if __name__ == "__main__":
    unittest.main(verbosity=2)
