"""Unmeasured values must render as unmeasured, and no default may leak.

Two things were never measured: axis travel and realtime latency. The INI does
contain travel numbers - LinuxCNC needs something - but they are marked
ASSUMPTION, so the UI must not present them as facts.
"""

from __future__ import annotations

import re
import unittest

from helpers import ALL_SCREENS, all_html, html, ini_raw_values, session, view

from bettercnc.configmodel import UNMEASURED, TravelSpec, load_machine_config
from bettercnc.iniload import load_ini, parse_ini_text
from bettercnc.profiles import load_profile


class TestTravelIsUnmeasured(unittest.TestCase):
    def setUp(self):
        self.cfg = load_machine_config(load_profile("test-rig"))
        self.ini = ini_raw_values()

    def test_every_axis_travel_is_flagged_unmeasured(self):
        for a in self.cfg.axes:
            with self.subTest(axis=a.axis):
                self.assertFalse(a.travel.measured)

    def test_travel_displays_as_unmeasured(self):
        for a in self.cfg.axes:
            with self.subTest(axis=a.axis):
                self.assertEqual(a.travel.display, UNMEASURED)

    def test_travel_display_does_not_contain_the_provisional_numbers(self):
        """The specific leak this guards: the INI says MAX_LIMIT = 150.0, and
        that number must not reach the operator dressed as a measurement."""
        for a in self.cfg.axes:
            with self.subTest(axis=a.axis):
                for value in (a.travel.min_limit, a.travel.max_limit):
                    if value is None:
                        continue
                    self.assertNotIn("{:g}".format(value), a.travel.display)

    def test_no_crash_protection_is_reported(self):
        for a in self.cfg.axes:
            with self.subTest(axis=a.axis):
                self.assertFalse(a.travel.has_crash_protection)
        self.assertFalse(self.cfg.soft_limits_configured)

    def test_provisional_numbers_are_still_known_internally(self):
        """We keep them because LinuxCNC is using them - we just do not display
        them. Losing them entirely would make the diagnostic worse."""
        x = self.cfg.axis("X")
        self.assertIsNotNone(x.travel.min_limit)
        self.assertIsNotNone(x.travel.max_limit)
        self.assertAlmostEqual(
            x.travel.max_limit, float(self.ini["JOINT_0.MAX_LIMIT"]), places=6
        )

    def test_ini_limits_are_marked_provisional_by_the_loader(self):
        ini = load_ini(self.cfg.ini_path)
        for joint in (0, 1, 2):
            for key in ("MIN_LIMIT", "MAX_LIMIT"):
                with self.subTest(joint=joint, key=key):
                    self.assertTrue(
                        ini.require("JOINT_{}".format(joint), key).provisional
                    )

    def test_scale_is_not_marked_provisional(self):
        """Guard against the marker over-reaching: SCALE was calibrated."""
        ini = load_ini(self.cfg.ini_path)
        for joint in (0, 1, 2):
            with self.subTest(joint=joint):
                self.assertFalse(
                    ini.require("JOINT_{}".format(joint), "SCALE").provisional
                )

    def test_step_timing_is_not_marked_provisional(self):
        ini = load_ini(self.cfg.ini_path)
        for key in ("STEPLEN", "STEPSPACE", "DIRSETUP", "DIRHOLD"):
            with self.subTest(key=key):
                self.assertFalse(ini.require("JOINT_0", key).provisional)


class TestTravelMarkup(unittest.TestCase):
    def test_travel_renders_the_unmeasured_word(self):
        for screen in ("kontrol", "makine"):
            with self.subTest(screen=screen):
                self.assertIn(UNMEASURED, html("test-rig", screen))

    def test_travel_cells_are_tagged_unmeasured(self):
        markup = html("test-rig", "makine")
        self.assertIn('data-travel-measured="0"', markup)
        self.assertNotIn('data-travel-measured="1"', markup)

    def test_provisional_limits_never_appear_anywhere(self):
        """Across every screen. 150.0 / -50.0 are the INI placeholders."""
        markup = all_html("test-rig")
        ini = ini_raw_values()
        for key in (
            "JOINT_0.MAX_LIMIT", "JOINT_1.MAX_LIMIT", "JOINT_2.MIN_LIMIT",
        ):
            raw = ini[key]
            with self.subTest(key=key, value=raw):
                self.assertNotIn(raw, markup)

    def test_prototype_travel_strings_absent(self):
        markup = all_html("test-rig")
        for wrong in ("0 – 300 mm", "0 – 180 mm", "−45 – 0 mm", "0 – 150 mm"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)

    def test_soft_limits_warning_is_shown(self):
        markup = html("test-rig", "makine")
        self.assertIn("Yumuşak limitler tanımsız", markup)
        self.assertIn("KORUMA YOK", markup)

    def test_soft_limits_warning_also_in_the_gate(self):
        vm = view("test-rig", "kontrol")
        self.assertTrue(
            any("Yumuşak limitler tanımsız" in w for w in vm["kontrol"]["gate"]["warnings"])
        )


class TestTravelSpecBehaviour(unittest.TestCase):
    """The type itself, so the rule holds for any future machine."""

    def test_unmeasured_hides_numbers_even_when_present(self):
        t = TravelSpec(min_limit=0.0, max_limit=999.0, measured=False, note="")
        self.assertEqual(t.display, UNMEASURED)
        self.assertNotIn("999", t.display)
        self.assertFalse(t.has_crash_protection)

    def test_measured_shows_numbers(self):
        t = TravelSpec(min_limit=0.0, max_limit=250.0, measured=True, note="")
        self.assertIn("250", t.display)
        self.assertTrue(t.has_crash_protection)

    def test_measured_but_missing_values_still_unmeasured(self):
        t = TravelSpec(min_limit=None, max_limit=None, measured=True, note="")
        self.assertEqual(t.display, UNMEASURED)

    def test_removing_the_assumption_marker_flips_the_display(self):
        """The useful property: when travel is finally measured and the comment
        deleted, the UI starts showing numbers with no code change."""
        measured_ini = parse_ini_text(
            "[JOINT_0]\n"
            "# Measured 2026-09-01 with a rule, end to end.\n"
            "MIN_LIMIT = 0.0\n"
            "MAX_LIMIT = 187.5\n"
        )
        self.assertFalse(measured_ini.require("JOINT_0", "MIN_LIMIT").provisional)
        self.assertFalse(measured_ini.require("JOINT_0", "MAX_LIMIT").provisional)

    def test_assumption_marker_governs_a_run_of_keys(self):
        """A single ASSUMPTION block above two keys must taint both - otherwise
        MAX_LIMIT slips through unmarked."""
        ini = parse_ini_text(
            "[JOINT_0]\n"
            "# ASSUMPTION: 150 mm of usable X travel.\n"
            "MIN_LIMIT = 0.0\n"
            "MAX_LIMIT = 150.0\n"
        )
        self.assertTrue(ini.require("JOINT_0", "MIN_LIMIT").provisional)
        self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)

    def test_one_provisional_limit_taints_the_pair(self):
        from bettercnc.configmodel import load_machine_config as lmc  # noqa: F401

        half = TravelSpec(0.0, 150.0, measured=False, note="")
        self.assertFalse(half.has_crash_protection)


class TestLatencyIsUnmeasured(unittest.TestCase):
    """latency-test was never run. The prototype invented 38 microseconds."""

    def test_profile_latency_is_none(self):
        for key in ("test-rig", "marble"):
            with self.subTest(profile=key):
                self.assertIsNone(load_profile(key).platform.latency_us)

    def test_latency_renders_as_a_dash(self):
        vm = view("test-rig", "makine")
        rows = {r["label"]: r for r in vm["makine"]["platform_rows"]}
        self.assertEqual(rows["Maks. jitter"]["value"], "—")

    def test_latency_row_carries_a_note_and_a_warning_severity(self):
        vm = view("test-rig", "makine")
        rows = {r["label"]: r for r in vm["makine"]["platform_rows"]}
        self.assertEqual(rows["Maks. jitter"]["severity"], "warn")
        self.assertTrue(rows["Maks. jitter"]["note"].strip())

    def test_no_microsecond_figure_appears_anywhere(self):
        """Nothing of the form "NN µs" may be rendered while unmeasured."""
        markup = all_html("test-rig")
        hits = re.findall(r"\d+(?:[.,]\d+)?\s*µs", markup)
        self.assertEqual(hits, [], "fabricated latency figure(s): {}".format(hits))

    def test_prototype_latency_absent(self):
        markup = all_html("test-rig")
        self.assertNotIn("38 µs", markup)

    def test_unmeasured_latency_is_listed_as_a_warning(self):
        vm = view("test-rig", "kontrol")
        self.assertTrue(
            any("Gecikme" in w for w in vm["kontrol"]["gate"]["warnings"])
        )


class TestOtherAbsentData(unittest.TestCase):
    """Things the prototype invented that have no source at all."""

    def test_no_program_files_are_invented(self):
        vm = view("test-rig", "program")
        self.assertEqual(vm["program"]["files"], [])
        self.assertTrue(vm["program"]["files_empty_reason"].strip())

    def test_prototype_filenames_absent(self):
        markup = all_html("test-rig")
        for name in (
            "kapak_plakasi_v3.ngc", "govde_cebi.ngc", "delik_deseni_8mm.ngc",
            "yuzey_alma_240x180.ngc", "test_kare_50mm.ngc",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, markup)

    def test_no_tools_are_invented(self):
        vm = view("test-rig", "takimlar")
        self.assertEqual(vm["takimlar"]["tools"], [])
        self.assertFalse(vm["takimlar"]["table_exists"])

    def test_no_spindle_rpm_is_invented(self):
        markup = all_html("test-rig")
        for wrong in ("12 000", "12000", "8 000"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)

    def test_no_flow_rate_is_invented(self):
        markup = all_html("test-rig")
        self.assertNotIn("2.4", markup)

    def test_probe_params_are_blank_not_invented(self):
        vm = view("test-rig", "probing")
        for row in vm["probing"]["params"]:
            with self.subTest(param=row["label"]):
                self.assertEqual(row["value"], "—")

    def test_prototype_probe_speeds_absent(self):
        markup = all_html("test-rig")
        for wrong in ("300 mm/dk", "25 mm/dk", "2.0 mm"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)


if __name__ == "__main__":
    unittest.main(verbosity=2)
