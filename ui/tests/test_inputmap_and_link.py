"""The real input map, the Z asymmetry, and link health.

Input map is hand-verified in docs/DURUM.md:69-80. The prototype's was invented.
Link health (packet loss + watchdog) was absent from the prototype entirely.
"""

from __future__ import annotations

import unittest

from helpers import ALL_SCREENS, all_html, html, session, view

from bettercnc.profiles import TEST_RIG_INPUTS, load_profile
from bettercnc.state import LinkHealth

#: docs/DURUM.md:73-80. Hand-verified by triggering each switch.
EXPECTED_MAP = {
    "gpio.020": ("X", "+"),   # X sağ
    "gpio.021": ("X", "-"),   # X sol
    "gpio.022": ("Y", "+"),   # Y arka
    "gpio.023": ("Y", "-"),   # Y ön
    "gpio.024": ("Z", "+"),   # Z üst
}


class TestInputMap(unittest.TestCase):
    def setUp(self):
        self.profile = load_profile("test-rig")

    def test_exactly_five_inputs(self):
        self.assertEqual(len(self.profile.inputs), 5)

    def test_pins_are_the_verified_ones(self):
        self.assertEqual(
            [i.pin for i in self.profile.inputs], sorted(EXPECTED_MAP)
        )

    def test_each_pin_maps_to_the_verified_axis_and_end(self):
        for inp in self.profile.inputs:
            with self.subTest(pin=inp.pin):
                axis, end = EXPECTED_MAP[inp.pin]
                self.assertEqual(inp.axis, axis)
                self.assertEqual(inp.end, end)

    def test_each_input_cites_its_source(self):
        for inp in self.profile.inputs:
            with self.subTest(pin=inp.pin):
                self.assertIn("DURUM.md", inp.source)

    def test_x_and_y_have_two_ends_each(self):
        for axis in ("X", "Y"):
            with self.subTest(axis=axis):
                self.assertEqual(len(self.profile.inputs_for_axis(axis)), 2)

    def test_z_has_only_one_switch(self):
        self.assertEqual(len(self.profile.inputs_for_axis("Z")), 1)


class TestZAsymmetry(unittest.TestCase):
    """Z has no bottom switch. Faking symmetry would imply protection that is
    not there. docs/DURUM.md:80"""

    def setUp(self):
        self.profile = load_profile("test-rig")

    def test_missing_switch_is_declared(self):
        missing = self.profile.missing_switches
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0].axis, "Z")
        self.assertEqual(missing[0].end, "-")

    def test_missing_switch_states_the_consequence(self):
        m = self.profile.missing_switches[0]
        self.assertIn("switch YOK", m.consequence)
        self.assertIn("donanımsal durdurma", m.consequence)

    def test_no_z_minus_input_is_fabricated(self):
        for inp in self.profile.inputs:
            with self.subTest(pin=inp.pin):
                self.assertFalse(inp.axis == "Z" and inp.end == "-")

    def test_asymmetry_is_rendered_on_the_alarms_screen(self):
        markup = html("test-rig", "alarmlar")
        self.assertIn("SWITCH YOK", markup)
        self.assertIn("Z alt", markup)

    def test_asymmetry_is_rendered_on_the_machine_screen(self):
        markup = html("test-rig", "makine")
        self.assertIn("Z alt", markup)

    def test_asymmetry_is_listed_as_a_gate_warning(self):
        vm = view("test-rig", "kontrol")
        self.assertTrue(
            any("Z" in w and "switch YOK" in w for w in vm["kontrol"]["gate"]["warnings"])
        )


class TestInputPolarity(unittest.TestCase):
    """Inputs idle HIGH and go LOW when triggered, so HAL uses .in_not.
    docs/DURUM.md:71"""

    def test_all_inputs_idle_high(self):
        for inp in load_profile("test-rig").inputs:
            with self.subTest(pin=inp.pin):
                self.assertTrue(inp.idle_high)

    def test_hal_pin_uses_in_not(self):
        for inp in load_profile("test-rig").inputs:
            with self.subTest(pin=inp.pin):
                self.assertTrue(inp.hal_pin.endswith(".in_not"))

    def test_hal_pin_carries_the_right_prefix(self):
        for inp in load_profile("test-rig").inputs:
            with self.subTest(pin=inp.pin):
                self.assertTrue(inp.hal_pin.startswith("hm2_7i92.0."))

    def test_in_not_is_visible_in_the_markup(self):
        markup = html("test-rig", "alarmlar")
        self.assertIn(".in_not", markup)

    def test_polarity_is_explained(self):
        vm = view("test-rig", "alarmlar")
        for row in vm["alarmlar"]["inputs"]:
            with self.subTest(pin=row["pin"]):
                self.assertIn("in_not", row["polarity_note"])

    def test_a_low_idling_input_would_use_in(self):
        """Guard the helper rather than only the current data."""
        from bettercnc.profiles import DigitalInput

        low = DigitalInput("gpio.030", "test", "A", "+", False, "test")
        self.assertTrue(low.hal_pin.endswith(".in"))
        self.assertFalse(low.hal_pin.endswith(".in_not"))


class TestInputsAreReadOnly(unittest.TestCase):
    """The prototype made inputs clickable toggles. An input is something the
    machine tells us, not something we set."""

    def test_view_model_marks_inputs_read_only(self):
        vm = view("test-rig", "alarmlar")
        for row in vm["alarmlar"]["inputs"]:
            with self.subTest(pin=row["pin"]):
                self.assertTrue(row["read_only"])

    def test_no_toggle_action_is_rendered(self):
        markup = all_html("test-rig")
        self.assertNotIn('data-action="toggle_input"', markup)

    def test_markup_marks_rows_readonly(self):
        markup = html("test-rig", "alarmlar")
        self.assertIn('data-readonly="1"', markup)


class TestFabricatedInputsAbsent(unittest.TestCase):
    """The prototype's map claimed an estop input, a tool sensor and a flow
    sensor. None exist on this machine."""

    def test_no_gpio_025_input(self):
        pins = [i.pin for i in load_profile("test-rig").inputs]
        self.assertNotIn("gpio.025", pins)

    def test_prototype_input_labels_absent_from_the_input_table(self):
        """The prototype listed an estop, a tool sensor and a flow sensor as
        INPUTS. Those words legitimately appear elsewhere as capability labels
        (rendered disabled), so this checks the input rows specifically."""
        vm = view("test-rig", "alarmlar")
        labels = [row["label"] for row in vm["alarmlar"]["inputs"]]
        for wrong in ("Acil stop zinciri", "Takım boyu sensörü", "Su debi sensörü"):
            with self.subTest(label=wrong):
                self.assertNotIn(wrong, labels)

    def test_input_rows_are_only_the_five_verified_switches(self):
        vm = view("test-rig", "alarmlar")
        self.assertEqual(
            [r["pin"] for r in vm["alarmlar"]["inputs"]],
            ["gpio.020", "gpio.021", "gpio.022", "gpio.023", "gpio.024"],
        )

    def test_input_count_matches_the_board_and_not_the_prototype(self):
        """Five isolated inputs exist; the prototype listed six."""
        self.assertEqual(len(TEST_RIG_INPUTS), 5)


class TestLinkHealth(unittest.TestCase):
    """packet-error-total and watchdog.has_bit - the two the prototype omitted."""

    def setUp(self):
        self.vm = view("test-rig", "kontrol")
        self.rows = {r["label"]: r for r in self.vm["link"]["rows"]}

    def test_packet_error_total_is_present(self):
        self.assertIn("Paket kaybı", self.rows)

    def test_watchdog_is_present(self):
        self.assertIn("Watchdog", self.rows)

    def test_io_error_is_present(self):
        self.assertIn("io_error", self.rows)

    def test_hal_pin_names_are_shown(self):
        self.assertEqual(
            self.rows["Paket kaybı"]["hal"], "hm2_7i92.0.packet-error-total"
        )
        self.assertEqual(
            self.rows["Watchdog"]["hal"], "hm2_7i92.0.watchdog.has_bit"
        )
        self.assertEqual(self.rows["io_error"]["hal"], "hm2_7i92.0.io_error")

    def test_hal_pin_names_reach_the_markup(self):
        markup = html("test-rig", "kontrol")
        for pin in (
            "hm2_7i92.0.packet-error-total",
            "hm2_7i92.0.watchdog.has_bit",
            "hm2_7i92.0.io_error",
        ):
            with self.subTest(pin=pin):
                self.assertIn(pin, markup)

    def test_packet_loss_turns_the_row_red(self):
        link = LinkHealth(True, 7, False, False, 1.0)
        from bettercnc.viewmodel import link_panel
        from bettercnc.configmodel import load_machine_config

        _, _, adapter = session("test-rig")
        state = adapter.snapshot()._replace(link=link)
        panel = link_panel(state, load_machine_config(load_profile("test-rig")))
        row = {r["label"]: r for r in panel["rows"]}["Paket kaybı"]
        self.assertEqual(row["value"], "7")
        self.assertEqual(row["severity"], "error")

    def test_bitten_watchdog_is_an_error(self):
        link = LinkHealth(True, 0, True, False, 1.0)
        self.assertFalse(link.healthy)
        self.assertTrue(any("Watchdog" in p for p in link.problems))

    def test_io_error_is_an_error(self):
        link = LinkHealth(True, 0, False, True, 1.0)
        self.assertFalse(link.healthy)
        self.assertTrue(any("io_error" in p for p in link.problems))

    def test_healthy_link_reports_healthy(self):
        self.assertTrue(LinkHealth(True, 0, False, False, 1.0).healthy)

    def test_disconnected_blocks_program_start(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        state = adapter.snapshot()._replace(
            link=LinkHealth(False, 0, False, False, 1.0)
        )
        self.assertIn("bağlantı yok", " ".join(state.start_blocks))

    def test_unknown_counters_render_as_dashes_not_zero(self):
        """A pin we could not read is not the same as a pin reading zero."""
        from bettercnc.viewmodel import link_panel
        from bettercnc.configmodel import load_machine_config

        link = LinkHealth(True, None, None, None, 1.0)
        _, _, adapter = session("test-rig")
        state = adapter.snapshot()._replace(link=link)
        panel = link_panel(state, load_machine_config(load_profile("test-rig")))
        rows = {r["label"]: r["value"] for r in panel["rows"]}
        self.assertEqual(rows["Paket kaybı"], "—")
        self.assertEqual(rows["Watchdog"], "—")
        self.assertEqual(rows["io_error"], "—")

    def test_link_row_appears_on_every_screen(self):
        """Header is global, so these indicators are always in view."""
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                self.assertIn("packet-error-total", html("test-rig", screen))


if __name__ == "__main__":
    unittest.main(verbosity=2)
