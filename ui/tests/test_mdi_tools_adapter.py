"""MDI text entry, the probe-is-not-a-tool rule, and the LinuxCNC mapping."""

from __future__ import annotations

import types
import unittest

from helpers import all_html, html, session, view

from bettercnc.adapter import (
    TASK_STATE_ESTOP,
    TASK_STATE_ON,
    map_drive_power,
    map_linuxcnc_state,
)
from bettercnc.capabilities import Capability
from bettercnc.profiles import load_profile
from bettercnc.state import DrivePower, HomingPhase


class TestMdiTextEntry(unittest.TestCase):
    """The prototype had preset buttons and no way to type a command."""

    def test_markup_has_a_text_input(self):
        markup = html("test-rig", "kontrol")
        self.assertIn('class="mdi-input"', markup)
        self.assertIn('name="mdi"', markup)
        self.assertIn('type="text"', markup)

    def test_markup_has_a_submit_form(self):
        markup = html("test-rig", "kontrol")
        self.assertIn('data-action="mdi"', markup)

    def test_presets_are_kept_as_well(self):
        vm = view("test-rig", "kontrol")
        self.assertTrue(len(vm["kontrol"]["mdi"]["presets"]) >= 3)

    def test_history_starts_empty(self):
        vm = view("test-rig", "kontrol")
        self.assertEqual(vm["kontrol"]["mdi"]["history"], [])

    def test_history_records_commands(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.send_mdi("G0 X10")
        adapter.send_mdi("M5")
        vm = view("test-rig", "kontrol", adapter=adapter)
        cmds = [h["command"] for h in vm["kontrol"]["mdi"]["history"]]
        self.assertIn("G0 X10", cmds)
        self.assertIn("M5", cmds)

    def test_history_is_newest_first(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.send_mdi("first")
        adapter.send_mdi("second")
        vm = view("test-rig", "kontrol", adapter=adapter)
        self.assertEqual(vm["kontrol"]["mdi"]["history"][0]["command"], "second")

    def test_history_records_rejections_too(self):
        _, _, adapter = session("test-rig")  # unhomed, software-disabled
        adapter.send_mdi("G0 X10")
        vm = view("test-rig", "kontrol", adapter=adapter)
        entry = vm["kontrol"]["mdi"]["history"][0]
        self.assertFalse(entry["accepted"])
        self.assertTrue(entry["note"].strip())

    def test_history_reaches_the_markup(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.send_mdi("G0 X1.5")
        markup = html("test-rig", "kontrol", adapter=adapter)
        self.assertIn("G0 X1.5", markup)

    def test_mdi_disabled_while_machine_off(self):
        vm = view("test-rig", "kontrol")
        self.assertFalse(vm["kontrol"]["mdi"]["enabled"])
        self.assertTrue(vm["kontrol"]["mdi"]["disabled_reason"].strip())

    def test_disabled_mdi_input_is_marked_disabled_in_html(self):
        markup = html("test-rig", "kontrol")
        idx = markup.index('class="mdi-input"')
        tag = markup[markup.rindex("<input", 0, idx): markup.index(">", idx) + 1]
        self.assertIn("disabled", tag)

    def test_empty_command_is_rejected(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        self.assertFalse(adapter.send_mdi("   ").accepted)

    def test_motion_command_rejected_while_unhomed(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        entry = adapter.send_mdi("G1 X20 F100")
        self.assertFalse(entry.accepted)
        self.assertIn("Referans", entry.note)

    def test_non_motion_command_allowed_while_unhomed(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        self.assertTrue(adapter.send_mdi("M5").accepted)

    def test_html_escaping_in_history(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.send_mdi("<script>alert(1)</script>")
        markup = html("test-rig", "kontrol", adapter=adapter)
        self.assertNotIn("<script>alert(1)</script>", markup)
        self.assertIn("&lt;script&gt;", markup)


class TestProbeIsNotATool(unittest.TestCase):
    """The prototype listed the probe as tool T6. A probe is a HAL input."""

    def test_tool_table_is_empty(self):
        vm = view("test-rig", "takimlar")
        self.assertEqual(vm["takimlar"]["tools"], [])

    def test_no_probe_row_in_the_tool_table(self):
        markup = html("test-rig", "takimlar")
        self.assertNotIn("Prob (tool sensörü)", markup)

    def test_tools_screen_states_the_rule(self):
        vm = view("test-rig", "takimlar")
        note = vm["takimlar"]["probe_note"]
        self.assertIn("HAL girişi", note)
        self.assertIn("takım değildir", note)

    def test_rule_is_rendered(self):
        markup = html("test-rig", "takimlar")
        self.assertIn("PROB ≠ TAKIM", markup)

    def test_probing_screen_repeats_the_rule(self):
        vm = view("test-rig", "probing")
        self.assertIn("takım tablosu kaydı DEĞİLDİR", vm["probing"]["probe_is_not_a_tool"])

    def test_probe_capability_lives_on_the_probing_screen(self):
        from bettercnc.capabilities import CAPABILITY_META

        self.assertEqual(CAPABILITY_META[Capability.TOOL_PROBE].screen, "probing")

    def test_prototype_tool_names_absent(self):
        markup = all_html("test-rig")
        for name in (
            "3 mm düz freze", "6 mm yüzey alma", "8 mm matkap ucu",
            "V-uç 60° gravür", "2 mm elmas uç",
        ):
            with self.subTest(name=name):
                self.assertNotIn(name, markup)

    def test_no_tool_length_offsets_are_invented(self):
        markup = all_html("test-rig")
        for wrong in ("42.180", "38.400", "61.250", "44.900"):
            with self.subTest(value=wrong):
                self.assertNotIn(wrong, markup)


class TestProbingScreenPreserved(unittest.TestCase):
    """The step-by-step flow is good UI and is kept - but gated."""

    def test_step_flow_still_present(self):
        vm = view("test-rig", "probing")
        self.assertTrue(len(vm["probing"]["modes"]) >= 3)
        for mode in vm["probing"]["modes"]:
            with self.subTest(mode=mode["id"]):
                self.assertTrue(len(mode["steps"]) >= 3)

    def test_flow_is_disabled_with_a_reason(self):
        vm = view("test-rig", "probing")
        self.assertFalse(vm["probing"]["enabled"])
        self.assertTrue(vm["probing"]["disabled_reason"].strip())

    def test_disabled_modes_are_rendered_not_hidden(self):
        markup = html("test-rig", "probing")
        self.assertIn('data-mode="z"', markup)
        self.assertIn('data-enabled="0"', markup)

    def test_probe_start_button_is_disabled(self):
        markup = html("test-rig", "probing")
        idx = markup.index('data-action="probe_start"')
        tag = markup[markup.rindex("<button", 0, idx): markup.index(">", idx) + 1]
        self.assertIn("disabled", tag)


class TestLinuxCncMapping(unittest.TestCase):
    """The pure mapping, exercised with a fake stat object.

    This is the part that will be wrong first when we plug in a real machine, so
    it is tested without one.
    """

    def _stat(self, **over):
        base = dict(
            task_state=TASK_STATE_ON,
            estop=0,
            joint=[
                {"homed": 1, "homing": 0},
                {"homed": 1, "homing": 0},
                {"homed": 1, "homing": 0},
            ],
            actual_position=(10.0, 20.0, -5.0),
            g5x_offset=(1.0, 2.0, 3.0),
            g5x_index=1,
            feedrate=1.0,
            file="",
            paused=0,
            interp_state=1,
        )
        base.update(over)
        return types.SimpleNamespace(**base)

    def _map(self, stat, hal=None):
        profile = load_profile("test-rig")
        from bettercnc.configmodel import joint_config_from_profile

        return map_linuxcnc_state(
            stat=stat,
            hal_values=hal or {"connected": True},
            profile=profile,
            joint_axis_map=["X", "Y", "Z"],
            joint_config=joint_config_from_profile(profile),
        )

    def test_task_state_on_means_software_enabled(self):
        st = self._map(self._stat(task_state=TASK_STATE_ON))
        self.assertFalse(st.estop.software_disabled)

    def test_task_state_estop_means_software_disabled(self):
        st = self._map(self._stat(task_state=TASK_STATE_ESTOP))
        self.assertTrue(st.estop.software_disabled)

    def test_no_estop_input_yields_unknown_drive_power(self):
        st = self._map(self._stat())
        self.assertIs(st.estop.drive_power, DrivePower.UNKNOWN)

    def test_estop_flag_ignored_without_a_hardware_input(self):
        """stat.estop on a machine with no wired input tells us about LinuxCNC's
        own software state, not about drive power. It must not be promoted to a
        hardware claim."""
        st = self._map(self._stat(estop=1))
        self.assertIs(st.estop.drive_power, DrivePower.UNKNOWN)

    def test_homed_joints_map_to_homed(self):
        st = self._map(self._stat())
        self.assertTrue(st.homing.all_homed)

    def test_homing_joint_maps_to_homing_phase(self):
        stat = self._stat(joint=[{"homed": 0, "homing": 1}, {"homed": 1, "homing": 0}, {"homed": 1, "homing": 0}])
        st = self._map(stat)
        phases = {j.axis: j.phase for j in st.homing.joints}
        self.assertIs(phases["X"], HomingPhase.HOMING)

    def test_unhomed_joint_blocks_start(self):
        stat = self._stat(joint=[{"homed": 0, "homing": 0}] * 3, file="a.ngc")
        st = self._map(stat)
        self.assertFalse(st.can_start_program)

    def test_missing_joint_data_is_treated_as_unhomed(self):
        st = self._map(self._stat(joint=[]))
        self.assertFalse(st.homing.all_homed)

    def test_work_position_subtracts_the_g5x_offset(self):
        st = self._map(self._stat())
        by_axis = {p.axis: p for p in st.positions}
        self.assertAlmostEqual(by_axis["X"].machine, 10.0)
        self.assertAlmostEqual(by_axis["X"].work, 9.0)

    def test_g5x_index_maps_to_wcs_name(self):
        self.assertEqual(self._map(self._stat(g5x_index=1)).wcs, "G54")
        self.assertEqual(self._map(self._stat(g5x_index=2)).wcs, "G55")
        self.assertEqual(self._map(self._stat(g5x_index=9)).wcs, "G62")

    def test_out_of_range_g5x_index_falls_back(self):
        self.assertEqual(self._map(self._stat(g5x_index=42)).wcs, "G54")

    def test_feedrate_becomes_a_percentage(self):
        self.assertEqual(self._map(self._stat(feedrate=1.5)).feed_override_pct, 150)

    def test_link_pins_are_read_from_hal_values(self):
        hal = {
            "connected": True,
            "hm2_7i92.0.packet-error-total": 3,
            "hm2_7i92.0.watchdog.has_bit": False,
            "hm2_7i92.0.io_error": False,
        }
        st = self._map(self._stat(), hal)
        self.assertEqual(st.link.packet_error_total, 3)
        self.assertFalse(st.link.watchdog_has_bit)

    def test_unreadable_pins_become_none_not_zero(self):
        hal = {
            "connected": True,
            "hm2_7i92.0.packet-error-total": None,
            "hm2_7i92.0.watchdog.has_bit": None,
        }
        st = self._map(self._stat(), hal)
        self.assertIsNone(st.link.packet_error_total)
        self.assertIsNone(st.link.watchdog_has_bit)

    def test_missing_stat_attributes_do_not_raise(self):
        """A partially-populated stat must degrade, not crash the UI."""
        st = self._map(types.SimpleNamespace())
        self.assertTrue(st.estop.software_disabled)
        self.assertFalse(st.homing.all_homed)


class TestDrivePowerMapping(unittest.TestCase):
    def test_absent_capability_forces_unknown(self):
        profile = load_profile("test-rig")
        for value in (True, False, None):
            with self.subTest(input_value=value):
                self.assertIs(
                    map_drive_power(profile, value), DrivePower.UNKNOWN
                )

    def test_with_an_input_the_value_decides(self):
        from bettercnc.capabilities import present

        profile = load_profile("test-rig")
        wired = profile._replace(
            capabilities={
                **profile.capabilities,
                Capability.ESTOP_INPUT: present("test"),
            }
        )
        self.assertIs(map_drive_power(wired, True), DrivePower.LIVE)
        self.assertIs(map_drive_power(wired, False), DrivePower.CUT)
        self.assertIs(map_drive_power(wired, None), DrivePower.UNKNOWN)


class TestAdapterInterface(unittest.TestCase):
    def test_mock_implements_the_whole_interface(self):
        from bettercnc.adapter import MachineAdapter

        _, _, adapter = session("test-rig")
        for name in (
            "snapshot", "send_mdi", "software_estop", "software_estop_reset",
            "home_all", "jog", "program_start", "program_stop",
        ):
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(adapter, name)))
        self.assertIsInstance(adapter, MachineAdapter)

    def test_real_adapter_fails_clearly_without_linuxcnc(self):
        from bettercnc.adapter import LinuxCncAdapter

        with self.assertRaises(RuntimeError) as ctx:
            LinuxCncAdapter(load_profile("test-rig"))
        self.assertIn("linuxcnc", str(ctx.exception))

    def test_mock_works_for_both_profiles(self):
        for key in ("test-rig", "marble"):
            with self.subTest(profile=key):
                _, _, adapter = session(key)
                self.assertIsNotNone(adapter.snapshot())


if __name__ == "__main__":
    unittest.main(verbosity=2)
