"""The core contract: an absent capability is rendered DISABLED, never hidden.

This file asserts it explicitly for every capability the test rig lacks, plus
structurally (a profile that forgets a capability must fail rather than hide it).
"""

from __future__ import annotations

import unittest

from helpers import ALL_SCREENS, all_html, html, session, view

from bettercnc.capabilities import (
    CAPABILITY_CATALOGUE,
    Capability,
    Presence,
    absent,
    todo,
)
from bettercnc.profiles import load_profile, validate_profile
from bettercnc.render import e as escape
from bettercnc.viewmodel import capability_panel, capability_panels


#: Every capability the TEST RIG does not have. Each is asserted individually
#: rather than in bulk so a failure names the capability.
TEST_RIG_LACKS = [
    Capability.SPINDLE,
    Capability.SPINDLE_ANALOG_SPEED,
    Capability.COOLANT_PUMP,
    Capability.FLOW_SENSOR,
    Capability.FLOW_INTERLOCK,
    Capability.TOOL_PROBE,
    Capability.TOOL_LENGTH_SENSOR,
    Capability.ESTOP_INPUT,
    Capability.DOOR_SWITCH,
    Capability.FOURTH_AXIS,
    Capability.SOFT_LIMITS,
]


class TestTestRigLacksEverything(unittest.TestCase):
    """The test rig has none of the catalogued capabilities. Assert each one."""

    def setUp(self):
        self.profile = load_profile("test-rig")

    def test_the_lacks_list_is_exhaustive(self):
        """Guard the list itself: if a capability is added and the test rig
        gains or lacks it, this test forces the list to be revisited."""
        lacking = [
            meta.key
            for meta in CAPABILITY_CATALOGUE
            if not self.profile.has(meta.key)
        ]
        self.assertEqual(
            sorted(c.value for c in lacking),
            sorted(c.value for c in TEST_RIG_LACKS),
            "TEST_RIG_LACKS is out of date with the profile",
        )

    def test_each_absent_capability_is_declared_not_omitted(self):
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                self.assertIn(
                    cap, self.profile.capabilities,
                    "{} not declared - would be silently hidden".format(cap.value),
                )

    def test_each_absent_capability_is_disabled(self):
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                panel = capability_panel(self.profile, cap)
                self.assertFalse(panel["enabled"])
                self.assertTrue(panel["disabled"])

    def test_each_absent_capability_is_never_hidden(self):
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                self.assertFalse(capability_panel(self.profile, cap)["hidden"])

    def test_each_absent_capability_states_a_reason(self):
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                panel = capability_panel(self.profile, cap)
                self.assertTrue(
                    panel["reason"].strip(),
                    "{} is disabled with no reason for the operator".format(cap.value),
                )

    def test_each_absent_capability_says_what_would_enable_it(self):
        """docs/MERMER-GECIS.md's whole purpose: not forgetting what is missing."""
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                panel = capability_panel(self.profile, cap)
                self.assertTrue(panel["requirement"].strip())

    def test_each_absent_capability_cites_a_source(self):
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                self.assertTrue(capability_panel(self.profile, cap)["source"].strip())

    def test_no_absent_capability_reports_present(self):
        """The failure mode worth naming: rendering absent as though present."""
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                panel = capability_panel(self.profile, cap)
                self.assertNotEqual(panel["presence"], Presence.PRESENT.value)
                self.assertNotEqual(panel["badge"], "var")


class TestRenderedMarkup(unittest.TestCase):
    """The same contract, asserted on the actual HTML."""

    def test_every_capability_appears_in_the_machine_screen(self):
        markup = html("test-rig", "makine")
        for meta in CAPABILITY_CATALOGUE:
            with self.subTest(capability=meta.key.value):
                self.assertIn(
                    'data-capability="{}"'.format(meta.key.value), markup,
                    "{} missing from rendered output".format(meta.key.value),
                )

    def test_absent_capabilities_render_with_the_disabled_class(self):
        markup = html("test-rig", "makine")
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                # find the block for this capability and check its class
                needle = 'data-capability="{}"'.format(cap.value)
                idx = markup.index(needle)
                block_start = markup.rindex("<div class=", 0, idx)
                block = markup[block_start:idx]
                self.assertIn("disabled", block)

    def test_absent_capabilities_render_enabled_zero(self):
        markup = html("test-rig", "makine")
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                needle = 'data-capability="{}"'.format(cap.value)
                idx = markup.index(needle)
                tail = markup[idx: idx + 220]
                self.assertIn('data-enabled="0"', tail)

    def test_reason_text_reaches_the_markup(self):
        profile = load_profile("test-rig")
        markup = html("test-rig", "makine")
        for cap in TEST_RIG_LACKS:
            with self.subTest(capability=cap.value):
                reason = profile.capability(cap).reason
                # Escape first: reasons contain apostrophes and dashes that the
                # renderer escapes, so comparing raw text would false-fail.
                self.assertIn(escape(reason), markup)

    def test_spindle_and_coolant_controls_are_not_operable(self):
        """The prototype shipped working spindle/coolant toggles with a default
        of coolant ON. Neither exists on this machine."""
        markup = all_html("test-rig")
        for token in ('data-action="spindle"', 'data-action="coolant"'):
            self.assertNotIn(token, markup)


class TestProfileCompleteness(unittest.TestCase):
    def test_both_profiles_validate(self):
        for key in ("test-rig", "marble"):
            with self.subTest(profile=key):
                validate_profile(load_profile(key))

    def test_missing_declaration_is_an_error_not_a_hidden_panel(self):
        profile = load_profile("test-rig")
        crippled = profile._replace(
            capabilities={
                k: v for k, v in profile.capabilities.items() if k is not Capability.SPINDLE
            }
        )
        with self.assertRaises(ValueError) as ctx:
            validate_profile(crippled)
        self.assertIn("spindle", str(ctx.exception))

    def test_capability_helpers_refuse_a_reasonless_absence(self):
        with self.assertRaises(ValueError):
            absent("", "something", "src")

    def test_capability_helpers_refuse_a_requirementless_absence(self):
        with self.assertRaises(ValueError):
            absent("no spindle", "", "src")

    def test_todo_requires_a_reason(self):
        with self.assertRaises(ValueError):
            todo("", "req", "src")


class TestMarbleProfileTodos(unittest.TestCase):
    """The marble machine is unsurveyed. TODO must not read as yes or no."""

    def setUp(self):
        self.profile = load_profile("marble")

    def test_marble_capabilities_are_todo_not_absent(self):
        for meta in CAPABILITY_CATALOGUE:
            with self.subTest(capability=meta.key.value):
                st = self.profile.capability(meta.key)
                self.assertIs(
                    st.presence, Presence.TODO,
                    "marble should be TODO until surveyed, got {}".format(st.presence),
                )

    def test_todo_capabilities_are_disabled(self):
        for meta in CAPABILITY_CATALOGUE:
            with self.subTest(capability=meta.key.value):
                self.assertTrue(capability_panel(self.profile, meta.key)["disabled"])

    def test_todo_is_visually_distinct_from_absent(self):
        rig = capability_panel(load_profile("test-rig"), Capability.SPINDLE)
        marble = capability_panel(self.profile, Capability.SPINDLE)
        self.assertNotEqual(rig["badge"], marble["badge"])
        self.assertEqual(marble["badge"], "BELİRSİZ")
        self.assertEqual(marble["severity"], "warn")

    def test_todo_renders_in_markup_as_todo(self):
        markup = html("marble", "makine")
        self.assertIn('data-presence="todo"', markup)
        self.assertIn("BELİRSİZ", markup)

    def test_marble_renders_every_screen_without_a_config(self):
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                markup = html("marble", screen)
                self.assertTrue(len(markup) > 400)


class TestCapabilityCatalogueIntegrity(unittest.TestCase):
    def test_catalogue_covers_every_enum_member(self):
        self.assertEqual(
            {m.key for m in CAPABILITY_CATALOGUE}, set(Capability)
        )

    def test_panels_are_built_from_the_catalogue_not_the_profile(self):
        """The structural guarantee. Even with an over-full profile the render
        list is exactly the catalogue, in catalogue order."""
        profile = load_profile("test-rig")
        panels = capability_panels(profile)
        self.assertEqual(
            [p["key"] for p in panels],
            [m.key.value for m in CAPABILITY_CATALOGUE],
        )

    def test_every_panel_carries_hidden_false(self):
        for key in ("test-rig", "marble"):
            for panel in capability_panels(load_profile(key)):
                with self.subTest(profile=key, capability=panel["key"]):
                    self.assertFalse(panel["hidden"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
