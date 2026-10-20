"""MDI text entry, the probe-is-not-a-tool rule, and the LinuxCNC mapping."""

from __future__ import annotations

import importlib.util
import threading
import types
import unittest

from helpers import all_html, html, rig_profile, session, strip_markers, view

from bettercnc import mdi as M
from bettercnc.adapter import (
    TASK_STATE_ESTOP,
    TASK_STATE_ESTOP_RESET,
    TASK_STATE_ON,
    MockAdapter,
    _joint_config,
    _needs_homing,
    _wcs_name,
    map_drive_power,
    map_linuxcnc_state,
    mdi_refusal,
)
from bettercnc.capabilities import Capability, present
from bettercnc.configmodel import joint_config_from_profile, load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import DrivePower, HomingPhase

#: Upper bound for every wait in the threaded test; never reached unless the
#: lock it checks is missing.
WAIT_S = 5.0


def _wired_test_rig():
    """The test rig as if it had a hardware estop input."""
    profile = load_profile("test-rig")
    return profile._replace(
        capabilities={**profile.capabilities, Capability.ESTOP_INPUT: present("test")}
    )


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

    def test_any_command_refused_while_unhomed_like_linuxcnc(self):
        """LinuxCNC refuses every MDI line before homing unless
        [TRAJ]NO_FORCE_HOMING is set (emctaskmain.cc:2208-2212), and the
        test rig does not set it. The mock used to accept "M5" here - more
        permissive than the machine it stands for."""
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        entry = adapter.send_mdi("M5")
        self.assertFalse(entry.accepted)
        self.assertIn("NO_FORCE_HOMING", entry.note)

    def test_non_motion_command_allowed_unhomed_with_no_force_homing(self):
        # Inside the existing [TRAJ]: LinuxCNC reads only the first one
        # (inifile.cc:261-267), and so does iniload.
        profile = rig_profile(self, insert={("TRAJ", "NO_FORCE_HOMING"): "1"})
        adapter = MockAdapter(profile)
        adapter.software_estop_reset()
        self.assertTrue(adapter.send_mdi("M5").accepted)
        entry = adapter.send_mdi("G0 X1")
        self.assertFalse(entry.accepted)
        self.assertEqual(entry.note, "Referans alınmadı — koordinatlı hareket kabul edilmiyor.")

    def test_no_force_homing_is_read_like_sscanf_d(self):
        """emctaskmain.cc:3245-3262: sscanf("%d") takes the leading integer,
        then a value <= 0 becomes 0 (off); an unreadable value is off too."""
        for value, on in (("1", True), ("2", True), ("1.5", True), ("+1", True),
                          ("0", False), ("-1", False), ("0.5", False), ("yes", False)):
            with self.subTest(value=value):
                profile = rig_profile(self, insert={("TRAJ", "NO_FORCE_HOMING"): value})
                adapter = MockAdapter(profile)
                adapter.software_estop_reset()
                entry = adapter.send_mdi("M5")
                self.assertIs(entry.accepted, on)
                if not on:
                    self.assertIn("NO_FORCE_HOMING", entry.note)

    def test_mock_refuses_mdi_while_a_program_runs(self):
        """The one MDI gate: the mock passes its running flag in, as
        LinuxCncAdapter passes stat's."""
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.load_program("a.ngc")
        adapter.program_start()
        self.assertTrue(adapter.snapshot().program_running)
        entry = adapter.send_mdi("G0 X0")
        self.assertFalse(entry.accepted)
        self.assertEqual(entry.note, "Program çalışıyor — MDI kabul edilmedi.")

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
        self.assertEqual(self._map(self._stat(g5x_index=6)).wcs, "G59")

    def test_g5x_index_7_to_9_are_g59_point_codes(self):
        """interp_convert.cc:1762-1789: origins 7, 8, 9 are G59.1-G59.3."""
        for index, name in ((7, "G59.1"), (8, "G59.2"), (9, "G59.3")):
            with self.subTest(index=index):
                self.assertEqual(self._map(self._stat(g5x_index=index)).wcs, name)

    def test_out_of_range_g5x_index_falls_back(self):
        self.assertEqual(self._map(self._stat(g5x_index=42)).wcs, "G54")

    def test_g5x_index_0_before_the_first_poll_falls_back_to_g54(self):
        """A stat that was never polled is zero-filled (emcmodule.cc Stat,
        PyType_GenericNew)."""
        self.assertEqual(_wcs_name(0), "G54")
        self.assertEqual(self._map(self._stat(g5x_index=0)).wcs, "G54")

    def test_feedrate_becomes_a_percentage(self):
        self.assertEqual(self._map(self._stat(feedrate=1.5)).feed_override_pct, 150)

    def test_feed_percentage_is_rounded_not_truncated(self):
        """0.29 * 100 is 28.999... in binary; truncating would show 28 %."""
        self.assertEqual(self._map(self._stat(feedrate=0.29)).feed_override_pct, 29)

    def test_missing_connected_key_means_not_connected(self):
        st = map_linuxcnc_state(
            stat=self._stat(), hal_values={}, profile=load_profile("test-rig"),
            joint_axis_map=["X", "Y", "Z"], joint_config={},
        )
        self.assertFalse(st.link.connected)

    def test_paused_program_is_not_running(self):
        """interp_state is PAUSED (3) while paused, not IDLE (1)."""
        st = self._map(self._stat(file="a.ngc", paused=1, interp_state=3))
        self.assertFalse(st.program_running)
        self.assertTrue(self._map(self._stat(file="a.ngc", paused=0, interp_state=3)).program_running)

    def test_estop_reset_state_means_software_disabled(self):
        """Out of estop but not ON is not ON (emctask.cc:419-430)."""
        st = self._map(self._stat(task_state=TASK_STATE_ESTOP_RESET))
        self.assertTrue(st.estop.software_disabled)
        self.assertEqual(st.status_label, "DEVRE DIŞI")

    def test_startup_estop_latch_is_not_a_hardware_cut(self):
        """stat.estop is LinuxCNC's latch (io.aux.estop, emcmodule.cc:418), 1
        at every startup (ioControl.cc:791). With a wired chain input it
        must not override the input pin: drive power is the pin's reading."""
        wired = _wired_test_rig()
        for pin, expected in ((True, DrivePower.LIVE), (False, DrivePower.CUT), (None, DrivePower.UNKNOWN)):
            with self.subTest(pin=pin):
                st = map_linuxcnc_state(
                    stat=self._stat(task_state=TASK_STATE_ESTOP, estop=1),
                    hal_values={"connected": True, "estop_input": pin},
                    profile=wired, joint_axis_map=["X", "Y", "Z"], joint_config={},
                )
                self.assertIs(st.estop.drive_power, expected)
                self.assertTrue(st.estop.software_disabled)

    def test_no_estop_input_key_is_unknown_even_when_wired(self):
        """LinuxCncAdapter._read_hal never supplies "estop_input" yet. With no
        key, drive power is UNKNOWN whatever stat.estop says - it is never
        derived from the latch."""
        for stat_estop in (1, 0):
            with self.subTest(stat_estop=stat_estop):
                st = map_linuxcnc_state(
                    stat=self._stat(task_state=TASK_STATE_ESTOP, estop=stat_estop),
                    hal_values={"connected": True},
                    profile=_wired_test_rig(), joint_axis_map=["X", "Y", "Z"], joint_config={},
                )
                self.assertIs(st.estop.drive_power, DrivePower.UNKNOWN)

    def test_unreadable_ignore_limits_is_passed_through_as_none(self):
        """state.JointHoming.ignore_limits: None = unreadable, never False."""
        cfg = {0: {"axis": "X", "HOME_IGNORE_LIMITS": None},
               1: {"axis": "Y", "HOME_IGNORE_LIMITS": True},
               2: {"axis": "Z", "HOME_IGNORE_LIMITS": False}}
        st = map_linuxcnc_state(
            stat=self._stat(), hal_values={"connected": True},
            profile=load_profile("test-rig"), joint_axis_map=["X", "Y", "Z"], joint_config=cfg,
        )
        self.assertEqual([j.ignore_limits for j in st.homing.joints], [None, True, False])

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
        wired = _wired_test_rig()
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

    @unittest.skipIf(importlib.util.find_spec("linuxcnc") is not None,
                     "the real linuxcnc module is installed here")
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


class TestNeedsHoming(unittest.TestCase):
    """_needs_homing (bettercnc.mdi): which MDI lines move the machine. Errs
    towards "yes". Group 1 motion and the axis-word rules are in reference/
    linuxcnc/docs/src/gcode/overview.adoc:879-884, :969-990 and :1016-1022;
    the parse follows close_and_downcase and enhance_block
    (src/emc/rs274ngc/interp_internal.cc:70-107, :149-218)."""

    MOTION = (
        "G0 X1", "G1 Y1 F100", "G2 X1 Y1 I1", "G3 X0 Y0 J1", "G38.2 Z-5 F10",
        "G28", "G30", "G28 Z2.5", "G53 G0 Z0", "G53 X0", "X10", "y-3",
        "M3 G0 X1", "g0x1", "G91 G0 X1", "G81 X1 Y1 Z-1 R1", "G5.2 X1 Y1",
        "G33 Z-1 K0.1", "O100 call", "o<sub> call", "G5.3", "X[1+2]", "G[1] X1", "G0 X1 (comment) ",
        "G1 X[SIN[30]]",
    )
    NOT_MOTION = (
        "G21", "G20", "G17", "G18", "G19", "G90", "G91", "G54", "G59.3",
        "G10 L20 P1 X0 Y0", "G10 L2 P1 X10", "G92 X0", "G92.1", "G52 X10",
        "G43 H1", "G43.1 Z0.25", "G28.1", "G30.1", "G4 P1", "M3 S1000",
        "M5", "F100", "S1000", "T1", "(G0 X1)", "M5 ; G0 X1", "G80",
        "#1 = 5", "#<depth> = 2",
    )
    #: Round-2 survivors and defects: each would let an unhomed machine with
    #: [TRAJ]NO_FORCE_HOMING move with no soft limits.
    ROUND2_MOTION = {
        # Blanks are dropped before the scan, as close_and_downcase drops
        # them (interp_internal.cc:91-92): "X - 10" is "x-10".
        "blank inside a word": ("X - 10", "X- 10", "X+ 10", "Z - [1]", "X 10",
                                "G 0 X 10", "G0\tX1", "G\t1 Y2"),
        # read_atsign / read_carat (interp_read.cc:3339-3357); polar_flag
        # makes a move like an axis word (interp_internal.cc:170, :204-211;
        # overview.adoc:915-919).
        "polar words": ("@.5 ^90", "G91 @.5 ^90", "@1", "^45", "G1 @1^90"),
        # "i1" alone is an arc in G2/G3 mode (interp_internal.cc:212-215);
        # the modal mode is not visible here, so I/J/K always count. R too,
        # by choice (mdi._ARC_LETTERS).
        "bare arc words": ("I1", "J-1", "K0.5", "R2"),
        # G70-G72 run with no axis words (interp_internal.cc:190-196,
        # g-code.adoc:1771, :1817).
        "lathe cycles": ("G18 G70 Q100", "G70", "G71", "G71.1", "G71.2",
                         "G72", "G72.1", "G72.2"),
        # G74 is a group 1 canned cycle (interp_array.cc _gees[740] = 1;
        # is_a_cycle, interp_internal.hh:837-839; g-code.adoc:1921).
        "left-hand tapping": ("G74 X1 Z-1 R1 F1", "G74", "G73", "G76"),
        "rotary and UVW axes": ("A10", "B-5", "C1", "U2", "V3", "W4"),
        "parameter values": ("X#1", "X#<xpos>", "Y-#2", "G1 Z#<_depth>"),
        "leading-dot values": ("X.5", "Z-.5", "G0 Y.25"),
        # A G number that is an expression or parameter: which G code it is
        # cannot be known here, so it counts as motion - and must not crash
        # (float("#") would raise).
        "unknown G number": ("G#1", "G#<code>", "G[1]", "G#<code> X1", "G-#1"),
    }
    ROUND2_NOT_MOTION = {
        # Letters inside a named parameter are not words.
        "named parameter": ("#<x1> = 5", "#<g0x10> = 1", "#<_a> = [#1 + 2]"),
        "comment with blanks": ("( G 0 X 1 )", "M5 ; X - 10"),
    }

    def test_motion_lines(self):
        for line in self.MOTION:
            with self.subTest(line=line):
                self.assertTrue(_needs_homing(line))

    def test_non_motion_lines(self):
        for line in self.NOT_MOTION:
            with self.subTest(line=line):
                self.assertFalse(_needs_homing(line))

    def test_round2_motion_lines(self):
        for why, lines in self.ROUND2_MOTION.items():
            for line in lines:
                with self.subTest(why=why, line=line):
                    self.assertIs(_needs_homing(line), True)

    def test_round2_non_motion_lines(self):
        for why, lines in self.ROUND2_NOT_MOTION.items():
            for line in lines:
                with self.subTest(why=why, line=line):
                    self.assertIs(_needs_homing(line), False)

    def test_unbalanced_bracket_counts_as_motion(self):
        self.assertTrue(_needs_homing("G1 X[1+2"))

    def test_the_gate_refuses_these_unhomed_even_with_no_force_homing(self):
        """End to end through mdi_refusal: the round-2 motion lines are
        refused unhomed, and none of them raises."""
        for lines in self.ROUND2_MOTION.values():
            for line in lines:
                with self.subTest(line=line):
                    self.assertEqual(
                        mdi_refusal(line, True, False, True, False),
                        "Referans alınmadı — koordinatlı hareket kabul edilmiyor.",
                    )

    def test_patterns_are_precompiled_module_constants(self):
        """No pattern is compiled inside the parse: every regex the module
        uses is a module-level compiled constant."""
        import inspect
        import re as _re

        self.assertNotRegex(inspect.getsource(M._needs_homing), r"re\.(?:compile|sub|search|match|findall)\(")
        compiled = [v for v in vars(M).values() if isinstance(v, _re.Pattern)]
        self.assertGreaterEqual(len(compiled), 6)

    def test_adapter_re_exports_the_gate(self):
        from bettercnc import adapter as A

        self.assertIs(A._needs_homing, M._needs_homing)
        self.assertIs(A.mdi_refusal, M.mdi_refusal)


class TestMdiRefusal(unittest.TestCase):
    """The one MDI gate both adapters use."""

    def refusal(self, command="M5", machine_on=True, homed=True,
                no_force_homing=False, program_running=False):
        return mdi_refusal(command, machine_on, homed, no_force_homing, program_running)

    def test_order_of_reasons(self):
        self.assertEqual(self.refusal(command="", machine_on=False), "Boş komut.")
        self.assertEqual(self.refusal(machine_on=False, program_running=True),
                         "Makine devre dışı — MDI kabul edilmedi.")
        self.assertEqual(self.refusal(program_running=True, homed=False),
                         "Program çalışıyor — MDI kabul edilmedi.")
        self.assertIn("NO_FORCE_HOMING", self.refusal(homed=False, command="G0 X1"))
        self.assertEqual(self.refusal(homed=False, no_force_homing=True, command="G0 X1"),
                         "Referans alınmadı — koordinatlı hareket kabul edilmiyor.")

    def test_accepted(self):
        self.assertIsNone(self.refusal())
        self.assertIsNone(self.refusal(command="G0 X1"))
        self.assertIsNone(self.refusal(homed=False, no_force_homing=True, command="M5"))


class TestMockFollowsTheIni(unittest.TestCase):
    """iniload.py:18-19: deleting the ASSUMPTION markers once travel is
    measured must update the UI with no code change - the mock's alarm log
    included."""

    ALARM = "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok"

    def test_soft_limit_alarm_while_travel_is_unmeasured(self):
        adapter = MockAdapter(load_profile("test-rig"))
        self.assertIn(self.ALARM, [a.text for a in adapter.snapshot().alarms])

    def test_soft_limit_alarm_gone_once_the_markers_are_removed(self):
        profile = rig_profile(self, transform=strip_markers)
        self.assertTrue(load_machine_config(profile).soft_limits_configured)
        adapter = MockAdapter(profile)
        self.assertNotIn(self.ALARM, [a.text for a in adapter.snapshot().alarms])

    def test_link_servo_period_is_the_ini_value(self):
        profile = rig_profile(self, {("EMCMOT", "SERVO_PERIOD"): "500000"})
        self.assertEqual(MockAdapter(profile).snapshot().link.servo_period_ms, 0.5)

    def test_no_ini_means_no_servo_period(self):
        """marble has no INI: "—", never the profile's placeholder."""
        self.assertIsNone(MockAdapter(load_profile("marble")).snapshot().link.servo_period_ms)

    def test_joint_config_matches_configmodel(self):
        """The adapters derive joint config from the MachineConfig they
        already loaded (adapter._joint_config); it must stay what
        configmodel.joint_config_from_profile gives."""
        for profile in (load_profile("test-rig"), load_profile("marble"),
                        rig_profile(self, {("JOINT_0", "HOME_IGNORE_LIMITS"): "maybe"})):
            with self.subTest(profile=profile.key, ini=profile.ini_path):
                self.assertEqual(
                    _joint_config(load_machine_config(profile)),
                    joint_config_from_profile(profile),
                )

    def test_reset_on_a_homed_mock_does_not_ask_for_homing(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        self.assertEqual(adapter.snapshot().message, "Yazılım acil stop sıfırlandı. Referans gerekli.")
        adapter.home_all()
        adapter.software_estop()
        adapter.software_estop_reset()
        self.assertEqual(adapter.snapshot().message, "Yazılım acil stop sıfırlandı.")


class TestMockLock(unittest.TestCase):
    """app.Session sends software_estop without its own lock, so the mock -
    the adapter build_session serves - must not let an estop land between
    a check and the write it guards (MockAdapter docstring, "Threading")."""

    def test_estop_waits_for_a_program_start_in_progress(self):
        """program_start is held between its start_blocks check and setting
        _running. A software_estop from another thread must wait for it -
        otherwise the mock ends estopped AND "Program çalışıyor"."""

        class ObservedLock:
            """threading.Lock that records a thread having to wait for it."""

            def __init__(self):
                self._lock = threading.Lock()
                self.contended = threading.Event()

            def __enter__(self):
                if not self._lock.acquire(blocking=False):
                    self.contended.set()
                    self._lock.acquire()
                return self

            def __exit__(self, *exc):
                self._lock.release()

        class HeldMock(MockAdapter):
            hold = False

            def _state(self):
                state = super()._state()
                if self.hold:
                    self.hold = False
                    in_check.set()
                    gate.wait(WAIT_S)
                return state

        in_check, gate = threading.Event(), threading.Event()
        adapter = HeldMock(load_profile("test-rig"))
        adapter._lock = ObservedLock()
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.load_program("a.ngc")
        adapter.hold = True
        starter = threading.Thread(target=adapter.program_start, daemon=True)
        stopper = threading.Thread(target=adapter.software_estop, daemon=True)
        for t in (starter, stopper):
            self.addCleanup(t.join, WAIT_S)
        self.addCleanup(gate.set)       # registered last: runs before the joins

        starter.start()
        self.assertTrue(in_check.wait(WAIT_S))
        stopper.start()
        self.assertTrue(adapter._lock.contended.wait(WAIT_S), "software_estop did not take the lock")
        self.assertTrue(stopper.is_alive())
        gate.set()
        starter.join(WAIT_S)
        stopper.join(WAIT_S)
        self.assertFalse(starter.is_alive() or stopper.is_alive())

        snap = adapter.snapshot()
        self.assertTrue(snap.estop.software_disabled)
        self.assertFalse(snap.program_running)
        self.assertEqual(snap.message, "Yazılım acil stop. Sürücü gücü DEĞİŞMEDİ.")

    def test_every_public_method_takes_the_lock(self):
        """Each public method runs under the lock (the _locked wrapper); a
        method added without it would reopen the window above."""
        import inspect

        public = [n for n, f in inspect.getmembers(MockAdapter, inspect.isfunction)
                  if not n.startswith("_")]
        self.assertIn("software_estop", public)
        for name in public:
            with self.subTest(method=name):
                self.assertTrue(hasattr(getattr(MockAdapter, name), "__wrapped__"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
