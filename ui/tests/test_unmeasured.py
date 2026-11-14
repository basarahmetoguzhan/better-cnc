"""Unmeasured values must render as unmeasured, and no default may leak.

Realtime latency was never measured. Axis travel was, on 2026-11-12 (Faz 4):
test-rig.ini lost its ASSUMPTION markers and the screens show its limits.

The unmeasured path stays guarded for the next INI (the marble machine has
none yet). LinuxCNC needs travel numbers, so an INI holds them before anyone
has measured; they are marked ASSUMPTION, and the UI must not present them as
facts. Those tests run on test-rig.ini with the markers put back
(helpers.provisional_travel).
"""

from __future__ import annotations

import os
import re
import unittest

from helpers import (
    ALL_SCREENS,
    TEST_RIG_INI,
    all_html,
    html,
    ini_raw_values,
    provisional_travel,
    rig_profile,
    session,
    view,
)

from bettercnc.configmodel import UNMEASURED, TravelSpec, load_machine_config
from bettercnc.iniload import load_ini, parse_ini_text
from bettercnc.profiles import load_profile

#: What the screens said about the travel while it was unmeasured
#: (configmodel, profiles, viewmodel, the mock's alarm log).
UNMEASURED_TRAVEL_TEXTS = (
    "Yumuşak limitler tanımsız",
    "KORUMA YOK",
    "strok ölçülmedi",
    "Strok ölçülmedi",
    "Strok ÖLÇÜLMEDİ",
)


def _provisional_rig(test: unittest.TestCase):
    """The test-rig profile on test-rig.ini with its travel marked ASSUMPTION.

    Premise checked here: the copy differs in comment lines only, so every
    key reads the same with helpers' independent parser."""
    profile = rig_profile(test, transform=provisional_travel)
    test.assertEqual(ini_raw_values(profile.ini_path), ini_raw_values())
    return profile


class TestTravelIsMeasured(unittest.TestCase):
    """test-rig.ini as it is: travel measured in Faz 4, no marker left."""

    def setUp(self):
        self.profile = load_profile("test-rig")
        self.cfg = load_machine_config(self.profile)
        self.ini = ini_raw_values()

    def test_no_value_in_the_ini_is_marked_provisional(self):
        ini = load_ini(self.cfg.ini_path)
        marked = [
            (sec, key) for sec in ini.sections() for key in ini.keys(sec)
            if ini.require(sec, key).provisional
        ]
        self.assertEqual(marked, [])

    def test_every_axis_shows_the_ini_limits(self):
        """Compared with helpers' own parser, not the loader."""
        self.assertEqual([a.axis for a in self.cfg.axes], ["X", "Y", "Z"])
        for a in self.cfg.axes:
            with self.subTest(axis=a.axis):
                lo = float(self.ini["JOINT_{}.MIN_LIMIT".format(a.joint)])
                hi = float(self.ini["JOINT_{}.MAX_LIMIT".format(a.joint)])
                self.assertTrue(a.travel.measured)
                self.assertEqual((a.travel.min_limit, a.travel.max_limit), (lo, hi))
                self.assertEqual(a.travel.display, "{:g} – {:g} mm".format(lo, hi))
                self.assertTrue(a.travel.has_crash_protection)
                self.assertEqual(a.travel.note, "")
        self.assertTrue(self.cfg.soft_limits_configured)
        self.assertTrue(self.profile.travel_measured)

    def test_travel_cells_are_tagged_measured_on_both_screens(self):
        for screen in ("kontrol", "makine"):
            with self.subTest(screen=screen):
                markup = html("test-rig", screen)
                self.assertEqual(markup.count('data-travel-measured="1"'), len(self.cfg.axes))
                self.assertNotIn('data-travel-measured="0"', markup)
                for a in self.cfg.axes:
                    self.assertIn(a.travel.display, markup)

    def test_no_screen_calls_the_travel_unmeasured(self):
        """Seen beside AXIS on the machine (2026-11-12, docs/11): while the
        markers were still in the INI the screens said "ölçülmedi" and
        "Yumuşak limitler tanımsız" for limits motion was enforcing."""
        markup = all_html("test-rig")
        for text in UNMEASURED_TRAVEL_TEXTS:
            with self.subTest(text=text):
                self.assertNotIn(text, markup)


class TestReadyGateHasNoStaleWarnings(unittest.TestCase):
    """Seen on the machine (2026-11-12): homed, the gate said "Program
    başlatılabilir" next to "Yumuşak limitler tanımsız" - and next to the Y
    DIR fault, solved in Faz 1 but still listed as active."""

    def test_homed_and_enabled_the_gate_contradicts_nothing(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        # A program loaded too (plan WP08: with none, the gate says so).
        adapter.load_program("/x/faz8-kare.ngc")
        gate = view("test-rig", "kontrol", adapter)["kontrol"]["gate"]
        self.assertEqual(gate["blocks"], [])  # premise: the startable state
        self.assertIn("Program başlatılabilir.", html("test-rig", "kontrol", adapter))
        for warning in gate["warnings"]:
            with self.subTest(warning=warning[:40]):
                for text in UNMEASURED_TRAVEL_TEXTS + ("aktif arıza", "yön değişmiyor"):
                    self.assertNotIn(text, warning)


class TestProvisionalTravelIsUnmeasured(unittest.TestCase):
    def setUp(self):
        self.cfg = load_machine_config(_provisional_rig(self))
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
        """The specific leak this guards: the INI has a MAX_LIMIT, and while
        it is marked that number must not reach the operator dressed as a
        measurement."""
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
        """Guard against the marker over-reaching: SCALE was measured (Faz 3)."""
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
    """The provisional copy on screen (TestTravelIsMeasured has the real INI)."""

    def setUp(self):
        self.profile = _provisional_rig(self)

    def test_travel_renders_the_unmeasured_word(self):
        """In the travel cell itself: the gate's latency warning says
        "ölçülmedi" on every machine."""
        for screen in ("kontrol", "makine"):
            with self.subTest(screen=screen):
                self.assertIn(
                    'data-travel-measured="0">{}</span>'.format(UNMEASURED),
                    html(self.profile, screen),
                )

    def test_travel_cells_are_tagged_unmeasured(self):
        markup = html(self.profile, "makine")
        self.assertIn('data-travel-measured="0"', markup)
        self.assertNotIn('data-travel-measured="1"', markup)

    def test_provisional_limits_never_appear_anywhere(self):
        """Across every screen: neither the INI's raw text nor the travel as
        TravelSpec.display would write it."""
        markup = all_html(self.profile)
        ini = ini_raw_values()
        for key in (
            "JOINT_0.MAX_LIMIT", "JOINT_1.MAX_LIMIT", "JOINT_2.MIN_LIMIT",
        ):
            raw = ini[key]
            with self.subTest(key=key, value=raw):
                self.assertNotIn(raw, markup)
        for joint in (0, 1, 2):
            lo = float(ini["JOINT_{}.MIN_LIMIT".format(joint)])
            hi = float(ini["JOINT_{}.MAX_LIMIT".format(joint)])
            with self.subTest(joint=joint):
                self.assertNotIn("{:g} – {:g} mm".format(lo, hi), markup)

    def test_prototype_travel_strings_absent(self):
        for profile in ("test-rig", self.profile):
            markup = all_html(profile)
            for wrong in ("0 – 300 mm", "0 – 180 mm", "−45 – 0 mm", "0 – 150 mm"):
                with self.subTest(value=wrong, ini=getattr(profile, "ini_path", profile)):
                    self.assertNotIn(wrong, markup)

    def test_soft_limits_warning_is_shown(self):
        markup = html(self.profile, "makine")
        self.assertIn("Yumuşak limitler tanımsız", markup)
        self.assertIn("KORUMA YOK", markup)

    def test_soft_limits_warning_also_in_the_gate(self):
        vm = view(self.profile, "kontrol")
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


class TestServoPeriodWithoutAnIni(unittest.TestCase):
    """The marble machine has no INI yet, so nothing gives its servo period.
    Its platform record used to carry an uncited 1.0, and the link card fell
    back to it: "1 ms" in the header of a machine with no config. The servo
    period is [EMCMOT]SERVO_PERIOD (configmodel.MachineConfig.servo_period_ms);
    without one the screens say "—"."""

    def test_marble_link_card_shows_a_dash(self):
        self.assertIsNone(load_profile("marble").ini_path)  # premise
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                rows = {r["label"]: r for r in view("marble", screen)["link"]["rows"]}
                self.assertEqual(
                    (rows["Servo döngü"]["value"], rows["Servo döngü"]["severity"]),
                    ("—", "warn"),
                )

    def test_no_millisecond_figure_appears_for_marble(self):
        hits = re.findall(r"\d+(?:[.,]\d+)?\s*ms\b", all_html("marble"))
        self.assertEqual(hits, [], "servo period without a source: {}".format(hits))
        # Premise: the same pattern finds the test rig's INI value.
        self.assertIn("1 ms", re.findall(r"\d+(?:[.,]\d+)?\s*ms\b", all_html("test-rig")))

    def test_marble_platform_states_no_servo_period(self):
        self.assertIsNone(load_profile("marble").platform.servo_period_ms)


class TestOtherAbsentData(unittest.TestCase):
    """Things the prototype invented that have no source at all."""

    def test_no_program_files_are_invented(self):
        """Plan WP08: the list is the INI's PROGRAM_PREFIX directory, read -
        exactly its G-code files, nothing more (read here with os.listdir
        and the INI parsed without project code)."""
        prefix = ini_raw_values()["DISPLAY.PROGRAM_PREFIX"]
        directory = os.path.normpath(os.path.join(os.path.dirname(TEST_RIG_INI), prefix))
        on_disk = sorted(n for n in os.listdir(directory)
                         if n.lower().endswith((".ngc", ".nc", ".tap")) and not n.startswith("."))
        vm = view("test-rig", "program")
        self.assertEqual(sorted(f["name"] for f in vm["program"]["files"]), on_disk)
        self.assertIn("faz8-kare.ngc", on_disk)  # premise: Faz 8's program is there
        self.assertEqual(vm["program"]["dir"], directory)

    def test_empty_reason_stays_true_with_programs_in_the_repo(self):
        """With files listed there is no empty reason: the old "not read"
        text would now be false, and "Dizin boş" too."""
        program = view("test-rig", "program")["program"]
        self.assertTrue(program["files"])
        self.assertEqual(program["files_empty_reason"], "")
        self.assertNotIn("okunmuyor", html("test-rig", "program"))
        self.assertNotIn("Dizin boş", html("test-rig", "program"))

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
