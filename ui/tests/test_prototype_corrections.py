"""Regression guard: none of the prototype's wrong facts may reappear.

Table-driven so it mirrors the correction table in docs/11-ui-implementation.md.
Every row is a string the prototype rendered that must never appear in our
output, plus the repo file that settles it.

If a future change reintroduces one of these, this file names it.
"""

from __future__ import annotations

import unittest

from helpers import ALL_SCREENS, all_html

#: (prototype value, why it is wrong, repo citation)
FORBIDDEN = [
    # 1 - input map was fabricated
    ("gpio.025", "only gpio.020-024 are wired as inputs on the test rig", "docs/DURUM.md:73-80"),
    ("Acil stop zinciri</span>", "no estop input on the test rig", "docs/DURUM.md:69-80"),
    # 2 - axis numbers were ~50x wrong
    ("80.000 adım/mm", "X/Y scale is 833.300 / 823.500", "configs/test-rig/test-rig.ini"),
    ("400.000 adım/mm", "Z scale is 1250.000", "configs/test-rig/test-rig.ini"),
    ("40 mm/s", "max velocity is 3 mm/s for first run", "configs/test-rig/test-rig.ini"),
    ("500 mm/s²", "acceleration is 10 mm/s²", "configs/test-rig/test-rig.ini"),
    ("300 mm/s²", "acceleration is 10 mm/s²", "configs/test-rig/test-rig.ini"),
    # 3 - step timing would drop pulses through the TB6560 optocouplers
    ("2 000 ns", "steplen/stepspace are 10 000 ns", "docs/09-tb6560-drivers.md"),
    ("20 000 ns", "dirsetup/dirhold are 50 000 ns", "docs/09-tb6560-drivers.md"),
    # 4 - wrong architecture
    ("arm64", "the controller is amd64 (live USB on a laptop)", "docs/DURUM.md:22"),
    # 5 - travel was never measured
    ("0 – 300 mm", "travel unmeasured", "configs/test-rig/test-rig.ini ASSUMPTION"),
    ("0 – 180 mm", "travel unmeasured", "configs/test-rig/test-rig.ini ASSUMPTION"),
    ("−45 – 0 mm", "travel unmeasured", "configs/test-rig/test-rig.ini ASSUMPTION"),
    # 6 - latency was never measured
    ("38 µs", "latency-test was never run", "docs/DURUM.md"),
    # 7 - capabilities that do not exist
    ("12 000", "no spindle, so no rpm", "docs/MERMER-GECIS.md:34"),
    ("2.4", "no flow sensor, so no flow rate", "docs/MERMER-GECIS.md:37"),
    ("L/dk · kilit açık", "no flow interlock exists", "docs/MERMER-GECIS.md:38"),
    # invented program files
    ("kapak_plakasi_v3.ngc", "no .ngc files in the repo", "repo has none"),
    ("4 812", "invented line count", "repo has none"),
    ("18 dk", "invented run time", "repo has none"),
    # probe as a tool
    ("Prob (tool sensörü)", "a probe is a HAL input, not a tool", "brief item F"),
    ("42.180", "invented tool length offset", "no tool.tbl exists"),
    # invented probe parameters
    ("300 mm/dk", "no probe, no parameters", "docs/MERMER-GECIS.md:39"),
    ("25 mm/dk", "no probe, no parameters", "docs/MERMER-GECIS.md:39"),
]


class TestNoPrototypeFactsSurvive(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markup = all_html("test-rig")
        cls.marble = all_html("marble")

    def test_forbidden_values_absent_from_test_rig(self):
        for value, why, cite in FORBIDDEN:
            with self.subTest(value=value):
                self.assertNotIn(
                    value, self.markup,
                    "prototype value {!r} reappeared - {} ({})".format(value, why, cite),
                )

    def test_forbidden_values_absent_from_marble(self):
        for value, why, cite in FORBIDDEN:
            with self.subTest(value=value):
                self.assertNotIn(value, self.marble)


class TestCorrectValuesPresent(unittest.TestCase):
    """The other half: the right values must actually be shown."""

    @classmethod
    def setUpClass(cls):
        cls.markup = all_html("test-rig")

    def test_real_scales_present(self):
        for value in ("833.300", "823.500", "1250.000"):
            with self.subTest(value=value):
                self.assertIn(value, self.markup)

    def test_real_timing_present(self):
        for value in ("10 000 ns", "50 000 ns"):
            with self.subTest(value=value):
                self.assertIn(value, self.markup)

    def test_real_kernel_present(self):
        self.assertIn("amd64", self.markup)
        self.assertIn("PREEMPT_RT", self.markup)

    def test_real_input_pins_present(self):
        for pin in ("gpio.020", "gpio.021", "gpio.022", "gpio.023", "gpio.024"):
            with self.subTest(pin=pin):
                self.assertIn(pin, self.markup)

    def test_board_panel_preserved_from_prototype(self):
        """The prototype's board panel was correct and is kept verbatim."""
        for value in (
            "Zhulong V2.0 (Mesa 7i92)", "hm2_7i92.0", "Spartan-6 XC6SLX9",
            "34 (2 × 17)", "100 MHz", "10.10.10.10",
        ):
            with self.subTest(value=value):
                self.assertIn(value, self.markup)

    def test_unmeasured_states_present(self):
        self.assertIn("ölçülmedi", self.markup)


class TestPreservedStructure(unittest.TestCase):
    """What the prototype got right and we kept."""

    def test_six_screens(self):
        from bettercnc.viewmodel import SCREENS

        self.assertEqual(len(SCREENS), 6)
        self.assertEqual(
            [s[0] for s in SCREENS],
            ["kontrol", "program", "probing", "takimlar", "alarmlar", "makine"],
        )

    def test_all_six_render(self):
        from helpers import html

        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                self.assertGreater(len(html("test-rig", screen)), 600)

    def test_turkish_labels(self):
        from bettercnc.viewmodel import SCREENS

        labels = [s[1] for s in SCREENS]
        self.assertIn("Kontrol", labels)
        self.assertIn("Takımlar", labels)
        self.assertIn("Alarmlar", labels)
        self.assertIn("Makine", labels)

    def test_alarm_log_keeps_source_attribution(self):
        from helpers import view

        vm = view("test-rig", "alarmlar")
        self.assertTrue(vm["alarmlar"]["events"])
        for ev in vm["alarmlar"]["events"]:
            with self.subTest(text=ev["text"][:30]):
                self.assertTrue(ev["source"].strip())

    def test_alarm_sources_reach_the_markup(self):
        from helpers import html

        markup = html("test-rig", "alarmlar")
        self.assertIn('class="ev-src"', markup)
        self.assertIn("hm2_eth", markup)

    def test_dark_theme_palette_carried_over(self):
        """Palette from design/prototype-source.jsx:78-80."""
        from bettercnc import render as R

        self.assertEqual(R.SURF, "#1a1c2c")
        self.assertEqual(R.ACC, "#9184d9")
        self.assertEqual(R.OK, "#84d9a5")
        self.assertEqual(R.BAD, "#d9848c")
        self.assertEqual(R.WARN, "#f0a92b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
