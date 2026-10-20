"""The real input map, the Z asymmetry, and link health.

Input map is hand-verified in docs/DURUM.md:69-80. The prototype's was invented.
Link health (packet loss + watchdog) was absent from the prototype entirely.
"""

from __future__ import annotations

import re
import unittest

from helpers import ALL_SCREENS, all_html, html, session, view

from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.capabilities import Capability
from bettercnc.profiles import TEST_RIG_INPUTS, load_profile
from bettercnc.state import LinkHealth, Severity

#: docs/DURUM.md:73-80 (locations, hand-verified by triggering each switch) and
#: docs/DURUM.md:82 (jog directions: X+ = right, Z+ = up, Y unknown). Y's ends are
#: "" because its DIR fault hides which way is +; asserting "+"/"-" there would be
#: presenting a guess as a measurement.
EXPECTED_MAP = {
    "gpio.020": ("X", "+"),   # X sağ
    "gpio.021": ("X", "-"),   # X sol
    "gpio.022": ("Y", ""),    # Y arka - + end unknown
    "gpio.023": ("Y", ""),    # Y ön   - + end unknown
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

    def test_y_end_is_unknown_not_guessed(self):
        """Regression guard: the map once said Y rear = "+", Y front = "-" while
        docs/DURUM.md:82 says "Y = belirsiz". Unknown stays unknown until the Y
        DIR fault is fixed and Y+ is jogged (docs/DURUM.md:127)."""
        for inp in self.profile.inputs_for_axis("Y"):
            with self.subTest(pin=inp.pin):
                self.assertEqual(inp.end, "")
                self.assertIn("DURUM.md:", inp.source)
                self.assertIn(":82", inp.source)

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


class TestUnmeasuredClaimsStayHedged(unittest.TestCase):
    """Guards for the 2026-10-18 corrections: none of these may silently revert."""

    def test_y_dir_fault_location_is_not_claimed(self):
        """Where Y's DIR path fails (board terminal, cable or driver) has not been
        measured (docs/DURUM.md:12), so no screen may call it a cable fault."""
        self.assertNotIn("DIR kablosu", all_html("test-rig"))
        self.assertIn("ölçülmedi", html("test-rig", "alarmlar"))

    def test_estop_requirement_keeps_the_encoder_front_end_caveat(self):
        """IO 25-33 are GPIO with num_encoders=0, but the circuit behind the
        encoder terminals is unverified (OPEN-QUESTIONS B2)."""
        req = load_profile("test-rig").capability(Capability.ESTOP_INPUT).requirement
        self.assertIn("ohmmetre", req)
        self.assertIn("OPEN-QUESTIONS B2", req)
        self.assertIn("ohmmetre", html("test-rig", "makine"))

    def test_marble_subtitle_does_not_reuse_plan_phase_numbers(self):
        """The marble machine was "Faz 2" in the original plan, which collides
        with the test-rig phase numbers. docs/DURUM.md:177 renamed it."""
        self.assertNotIn("Faz 2", all_html("marble"))


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
        self.assertIn("Kayıp paket", self.rows)

    def test_watchdog_is_present(self):
        self.assertIn("Kart bağlantısı", self.rows)

    def test_io_error_is_present(self):
        self.assertIn("İletişim hatası", self.rows)

    def test_hal_pin_names_are_shown(self):
        """The operator reads plain labels; the HAL names stay on the rows
        for maintenance."""
        self.assertEqual(
            self.rows["Kayıp paket"]["hal"], "hm2_7i92.0.packet-error-total"
        )
        self.assertEqual(
            self.rows["Kart bağlantısı"]["hal"], "hm2_7i92.0.watchdog.has_bit"
        )
        self.assertEqual(self.rows["İletişim hatası"]["hal"], "hm2_7i92.0.io_error")

    def test_no_driver_jargon_reaches_the_operator(self):
        """2026-10-20: "a worker does not know what 'watchdog bit' means".
        Labels, values and problems say what happened and what to do."""
        _, config, adapter = session("test-rig")
        state = adapter.snapshot()
        for link in (
            LinkHealth(True, 3, True, True, 1.0),
            LinkHealth(True, None, None, None, 1.0),
            LinkHealth(False, None, None, None, None),
        ):
            panel = VM.link_panel(state._replace(link=link), config)
            words = " ".join(
                [r["label"] + " " + r["value"] for r in panel["rows"]] + panel["problems"]
            ).lower()
            for jargon in ("watchdog", "ısır", "isir", "io_error", "has_bit", "sessiz"):
                with self.subTest(link=link, word=jargon):
                    self.assertNotIn(jargon, words)

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
        row = {r["label"]: r for r in panel["rows"]}["Kayıp paket"]
        self.assertEqual(row["value"], "7")
        self.assertEqual(row["severity"], "error")

    LOST = (
        "Kartla bağlantı koptu — makine durduruldu. Ethernet kablosunu ve "
        "kartın beslemesini kontrol edin, sonra «Yazılım sıfırla»ya basın. "
        "Ardından referansı yeniden alın."
    )

    def test_bitten_watchdog_is_an_error(self):
        link = LinkHealth(True, 0, True, False, 1.0)
        self.assertFalse(link.healthy)
        self.assertEqual(link.problems, [self.LOST])

    def test_io_error_is_an_error(self):
        link = LinkHealth(True, 0, False, True, 1.0)
        self.assertFalse(link.healthy)
        self.assertEqual(link.problems, [self.LOST])

    def test_both_faults_are_one_problem_with_one_remedy(self):
        """io_error stops the watchdog's petting, so it bites too; the
        operator does one thing about both."""
        self.assertEqual(LinkHealth(True, 0, True, True, 1.0).problems, [self.LOST])

    def test_healthy_link_reports_healthy(self):
        self.assertTrue(LinkHealth(True, 0, False, False, 1.0).healthy)

    def test_packet_loss_alone_makes_the_link_unhealthy(self):
        """Round-1 mutant T06: every other unhealthy case also had the
        watchdog or io_error set, so dropping the packet-error term from
        LinkHealth.healthy went unnoticed. Counted packet loss on an otherwise
        clean link must still raise the header severity to WARN."""
        lossy = LinkHealth(True, 3, False, False, 1.0)
        self.assertFalse(lossy.healthy)
        self.assertEqual(
            lossy.problems,
            [
                "Kartla iletişimde 3 paket kayboldu. Sayı artmaya devam ederse "
                "Ethernet kablosunu ve bağlantısını kontrol edin."
            ],
        )
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        ready = adapter.snapshot()
        self.assertTrue(ready.link.healthy)  # premise: the mock's link is clean
        self.assertEqual(ready.status_severity, Severity.INFO)
        self.assertEqual(ready._replace(link=lossy).status_severity, Severity.WARN)

    def test_disconnected_blocks_program_start(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        state = adapter.snapshot()._replace(
            link=LinkHealth(False, 0, False, False, 1.0)
        )
        self.assertIn("bağlantı yok", " ".join(state.start_blocks))

    def test_unknown_counters_render_as_dashes_not_zero(self):
        """A pin we could not read is not the same as a pin reading zero, and
        its row is WARN: neither the ERROR of a fault nor the INFO of a clean
        reading."""
        link = LinkHealth(True, None, None, None, 1.0)
        profile, config, adapter = session("test-rig")
        state = adapter.snapshot()._replace(link=link)
        panel = VM.link_panel(state, config)
        rows = {r["label"]: (r["value"], r["severity"]) for r in panel["rows"]}
        for label in ("Kayıp paket", "Kart bağlantısı", "İletişim hatası"):
            with self.subTest(row=label):
                self.assertEqual(rows[label], ("—", "warn"))
        self.assertFalse(panel["healthy"])

    def test_any_unreadable_counter_makes_the_link_not_healthy(self):
        """LinkHealth.healthy: an unread counter is unknown, not clean.
        Calling such a link healthy would present an unmeasured value as
        measured. Each counter alone, against a clean link."""
        clean = LinkHealth(True, 0, False, False, 1.0)
        self.assertTrue(clean.healthy)  # premise
        for field, name in (
            ("packet_error_total", "kayıp paket sayısı"),
            ("watchdog_has_bit", "kart bağlantısı"),
            ("io_error", "iletişim hatası"),
        ):
            with self.subTest(field=field):
                link = clean._replace(**{field: None})
                self.assertFalse(link.healthy)
                self.assertEqual(link.unreadable, [name])
                self.assertEqual(
                    link.problems,
                    ["Okunamayan gösterge: {} — kartla iletişimin sağlığı bilinmiyor.".format(name)],
                )
        self.assertEqual(
            LinkHealth(True, None, None, None, 1.0).problems,
            [
                "Okunamayan gösterge: kayıp paket sayısı, kart bağlantısı, iletişim hatası — "
                "kartla iletişimin sağlığı bilinmiyor."
            ],
        )

    def test_lost_connection_is_named_once(self):
        """Without a connection every counter is unreadable; the one reason
        is the connection, so the unreadable line is not added to it."""
        self.assertFalse(LinkHealth(False, 0, False, False, 1.0).healthy)
        lost = LinkHealth(False, None, None, None, None)
        self.assertFalse(lost.healthy)
        self.assertEqual(
            lost.problems, ["Kart bağlantısının durumu okunamıyor — kartla iletişim bilinmiyor."]
        )

    def test_unreadable_counter_warns_in_the_header_and_on_the_makine_card(self):
        profile, config, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        ready = adapter.snapshot()
        ready_markup = R.render_body(VM.build(profile, config, ready, "makine"))
        # Premise: a clean link on a ready machine reads INFO and "sağlıklı".
        self.assertEqual(ready.status_severity, Severity.INFO)
        self.assertIn("Bağlantı sağlıklı.", ready_markup)

        part = ready._replace(link=ready.link._replace(watchdog_has_bit=None))
        self.assertEqual(part.status_severity, Severity.WARN)
        vm = VM.build(profile, config, part, "makine")
        self.assertFalse(vm["makine"]["link_healthy"])
        markup = R.render_body(vm)
        self.assertIn('class="state" data-severity="warn"', markup)
        self.assertIn(
            "Okunamayan gösterge: kart bağlantısı — kartla iletişimin sağlığı bilinmiyor.", markup
        )
        self.assertNotIn("Bağlantı sağlıklı.", markup)
        gate = VM.build(profile, config, part, "kontrol")["kontrol"]["gate"]
        self.assertIn(
            "Okunamayan gösterge: kart bağlantısı — kartla iletişimin sağlığı bilinmiyor.",
            gate["warnings"],
        )

    def test_unknown_servo_period_is_a_dash_in_warn(self):
        """LinkHealth.servo_period_ms is None when the INI gives no
        [EMCMOT]SERVO_PERIOD. The header shows "—", never a default."""
        profile, config, adapter = session("test-rig")
        base = adapter.snapshot()

        def servo(link):
            vm = VM.build(profile, config, base._replace(link=link), "kontrol")
            row = {r["label"]: r for r in vm["link"]["rows"]}["Servo döngü"]
            shown = re.search(
                r'data-link="Servo döngü"[^>]*>.*?<span class="link-v"[^>]*>([^<]*)</span>',
                R.render_header(vm),
            )
            return row["value"], row["severity"], shown.group(1)

        self.assertEqual(
            servo(LinkHealth(True, 0, False, False, 0.5)), ("0.5 ms", "info", "0.5 ms")
        )
        self.assertEqual(servo(LinkHealth(True, 0, False, False, None)), ("—", "warn", "—"))

    def test_link_row_appears_on_every_screen(self):
        """Header is global, so these indicators are always in view."""
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                self.assertIn("packet-error-total", html("test-rig", screen))


if __name__ == "__main__":
    unittest.main(verbosity=2)
