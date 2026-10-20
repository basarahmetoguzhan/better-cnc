"""Edge cases the other suites never reach: edited or sparse INIs, states only a
wired estop input or the real adapter produce, and the adapter contract.

Why these and not "every uncovered line": `make coverage-missing` left a tail of
lines outside LinuxCncAdapter and app.py. Each one was read. The ones tested here
decide what reaches the operator:

* The INI is the single source of truth (the configmodel module docstring).
  What the loader does with an INI that is half-measured, finally measured, or
  sparse decides whether a number, an "ölçülmedi" or a "—" reaches the screen.
* The UI is built for the marble machine (the profiles module docstring). That
  machine will have an estop input and the real adapter. Both produce
  drive-power, link, alarm and unreadable-status states the mock never
  produces on the test rig.
* The status label and the gate are what the operator reads first, so their
  priority order is behaviour, not decoration.

Deliberately NOT tested, because a test would only execute the line: the
`...` bodies of MachineAdapter's abstract methods; the message of the
import-time assert in capabilities.py, which is only evaluated if that assert
fails; and render_program's non-empty file branch. viewmodel.program_screen
always returns files=[], and render_program never renders file entries at all.

Known defects are recorded with unittest.expectedFailure, the convention set in
test_app_cli.py. Each such test states the behaviour we want. When the fix lands
it becomes an "unexpected success", which fails the run, so the decorator cannot
outlive the bug. The three recorded in round 1 (backslash continuation, the
link card's servo period, the startup estop latch) are fixed, and their tests
now pin the fixed behaviour. The fourth (with the status unknown, the jog card
said the machine was off — TestStatusUnknown.test_kontrol_markup_claims_no_machine_state)
is fixed as well; no expectedFailure remains.

Synthetic INI values below exist only to pass through the loader. They are
chosen to differ from configs/test-rig/test-rig.ini so that a leak from either
file is visible, and they describe no machine.

reference/ (the gitignored LinuxCNC 2.9 clone) is optional. The last class
re-checks the LinuxCNC facts cited here when it is present and skips otherwise.
"""

from __future__ import annotations

import ast
import os
import re
import tempfile
import types
import unittest

from helpers import ALL_SCREENS, REPO_ROOT, TEST_RIG_INI, UI_DIR, ini_raw_values, session

from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import (
    TASK_STATE_ESTOP,
    TASK_STATE_ON,
    LinuxCncAdapter,
    MachineAdapter,
    MockAdapter,
    map_linuxcnc_state,
)
from bettercnc.capabilities import Capability, Presence, present, todo
from bettercnc.configmodel import (
    UNMEASURED,
    joint_config_from_profile,
    load_config_from_ini,
    load_machine_config,
)
from bettercnc.iniload import PROVISIONAL_MARKERS, load_ini, parse_ini_text
# Aliased so no test runner mistakes the factory for a test function.
from bettercnc.profiles import load_profile, validate_profile
from bettercnc.profiles import test_rig_profile as build_test_rig_profile
from bettercnc.state import (
    STATUS_UNKNOWN_BLOCK,
    DrivePower,
    HomingPhase,
    HomingState,
    LinkHealth,
    Severity,
)

LINUXCNC_SRC = os.path.join(REPO_ROOT, "reference", "linuxcnc")
HAVE_REFERENCE = os.path.isdir(os.path.join(LINUXCNC_SRC, "src"))

#: stat.interp_state values, src/emc/nml_intf/emc.hh:278-279.
EMC_TASK_INTERP_IDLE = 1
EMC_TASK_INTERP_READING = 2


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _ini_file(test: unittest.TestCase, text: str) -> str:
    """`text` written to a temp INI file, removed after the test."""
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    path = os.path.join(tmp.name, "edge-case.ini")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _profile_for_ini(test: unittest.TestCase, text: str):
    """The test-rig profile built on `text` written to a temp INI file.

    Built with profiles.test_rig_profile(path), not load_profile()._replace(ini_path=):
    the profile's travel facts (SOFT_LIMITS, the Z-bottom consequence) are
    read from its INI, so they must be read from this one.
    """
    return build_test_rig_profile(_ini_file(test, text))


def _build(profile, screen: str, state=None):
    config = load_machine_config(profile)
    if state is None:
        state = MockAdapter(profile).snapshot()
    return VM.build(profile, config, state, screen=screen)


def _rows(rows):
    return {r["label"]: r["value"] for r in rows}


def _synthetic_joint(n: int) -> str:
    """One [JOINT_n] holding every key load_machine_config requires.

    The limits carry no comment, so they count as measured.
    """
    return (
        "\n[JOINT_{n}]\n"
        "SCALE = 111.0\n"
        "MAX_VELOCITY = 11.0\n"
        "MAX_ACCELERATION = 111.0\n"
        "STEPLEN = 1111\n"
        "STEPSPACE = 1111\n"
        "DIRSETUP = 1111\n"
        "DIRHOLD = 1111\n"
        "HOME_SEQUENCE = {n}\n"
        "HOME_IGNORE_LIMITS = NO\n"
        "HOME_SEARCH_VEL = 0.0\n"
        "MIN_LIMIT = 0.0\n"
        "MAX_LIMIT = 123.0\n"
    ).format(n=n)


THREE_JOINTS = "".join(_synthetic_joint(n) for n in range(3))


def _test_rig_text() -> str:
    with open(TEST_RIG_INI, "r", encoding="utf-8") as fh:
        return fh.read()


def _strip_markers(text: str) -> str:
    """Delete every provisional marker word: the "travel was measured" edit.

    This is exactly the edit the iniload module docstring describes. Key lines
    are left alone. Callers check that by comparing values with an independent
    parser.
    """
    for marker in PROVISIONAL_MARKERS:
        text = re.sub(re.escape(marker), "", text, flags=re.IGNORECASE)
    return text


def _stat(**over):
    """A hand-built linuxcnc.stat (field names: adapter.py module docstring)."""
    base = dict(
        task_state=TASK_STATE_ON,
        estop=0,
        joint=[{"homed": 1, "homing": 0}] * 3,
        actual_position=(0.0, 0.0, 0.0),
        g5x_offset=(0.0, 0.0, 0.0),
        g5x_index=1,
        feedrate=1.0,
        file="",
        paused=0,
        interp_state=EMC_TASK_INTERP_IDLE,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def _map(profile, stat, hal):
    return map_linuxcnc_state(
        stat=stat,
        hal_values=hal,
        profile=profile,
        joint_axis_map=["X", "Y", "Z"],
        joint_config=joint_config_from_profile(profile),
    )


def _wired_profile():
    """The test rig as it would be with an estop input wired.

    No profile has one yet: the test rig declares it absent and the marble
    machine declares it TODO (profiles.py).
    """
    profile = load_profile("test-rig")
    caps = dict(profile.capabilities)
    caps[Capability.ESTOP_INPUT] = present(
        "test_edge_cases.py: hypothetical wired input, no machine has one yet"
    )
    return profile._replace(capabilities=caps)


def _enabled_session():
    """(profile, config, state) for a mock that is reset and homed: "HAZIR"."""
    profile, config, adapter = session("test-rig")
    adapter.software_estop_reset()
    adapter.home_all()
    return profile, config, adapter.snapshot()


# ---------------------------------------------------------------------------
# Travel: measured, half-measured
# ---------------------------------------------------------------------------


class TestMeasuredTravelReachesTheScreen(unittest.TestCase):
    """The iniload module docstring promises that when travel is finally
    measured and the ASSUMPTION comment is deleted, the UI shows the numbers
    with no code change.

    test_unmeasured.py checks only half of that promise: that the parser stops
    flagging the keys. Nothing checked that the numbers actually reach the DRO
    and the Makine screen, or that the "KORUMA YOK" banner goes away. Here the
    real test-rig.ini goes through exactly that edit.
    """

    def setUp(self):
        self.original = ini_raw_values()
        self.profile = _profile_for_ini(self, _strip_markers(_test_rig_text()))
        # Premise 1: the edit changed comments only. Every key/value is the same,
        # read with the independent parser in helpers.py.
        self.assertEqual(ini_raw_values(self.profile.ini_path), self.original)
        # Premise 2: no travel limit is still provisional.
        ini = load_ini(self.profile.ini_path)
        for joint in range(3):
            for key in ("MIN_LIMIT", "MAX_LIMIT"):
                self.assertFalse(
                    ini.require("JOINT_{}".format(joint), key).provisional,
                    "marker left on JOINT_{} {}".format(joint, key),
                )
        self.config = load_machine_config(self.profile)

    def test_each_axis_shows_the_travel_written_in_the_ini(self):
        self.assertEqual([a.axis for a in self.config.axes], ["X", "Y", "Z"])
        for a in self.config.axes:
            with self.subTest(axis=a.axis):
                lo = float(self.original["JOINT_{}.MIN_LIMIT".format(a.joint)])
                hi = float(self.original["JOINT_{}.MAX_LIMIT".format(a.joint)])
                self.assertTrue(a.travel.measured)
                self.assertEqual(a.travel.display, "{:g} – {:g} mm".format(lo, hi))
                self.assertTrue(a.travel.has_crash_protection)
                # No leftover "why unmeasured" note on a measured travel.
                self.assertEqual(a.travel.note, "")
        self.assertTrue(self.config.soft_limits_configured)

    def test_numbers_and_measured_tags_reach_both_screens(self):
        for screen in ("kontrol", "makine"):
            with self.subTest(screen=screen):
                markup = R.render_body(_build(self.profile, screen))
                self.assertEqual(markup.count('data-travel-measured="1"'), 3)
                self.assertNotIn('data-travel-measured="0"', markup)
                for a in self.config.axes:
                    self.assertIn(a.travel.display, markup)

    def test_no_protection_warnings_are_withdrawn(self):
        """The warnings must follow the INI in both directions. A "KORUMA YOK"
        that stays up after the travel is measured trains the operator to
        ignore it."""
        makine = _build(self.profile, "makine")
        self.assertEqual(makine["makine"]["soft_limits_warning"], "")
        self.assertNotIn("KORUMA YOK", R.render_body(makine))
        warnings = _build(self.profile, "kontrol")["kontrol"]["gate"]["warnings"]
        self.assertFalse(any("Yumuşak limitler tanımsız" in w for w in warnings))


class TestHalfMeasuredTravel(unittest.TestCase):
    """configmodel.load_config_from_ini, the provisional-pair rule: "a
    half-measured travel is not a measured travel".

    test_unmeasured.TestTravelSpecBehaviour.test_one_provisional_limit_taints_the_pair
    builds a TravelSpec by hand with measured=False, so it never runs the
    loader's own decision. This test does, in both orders. Only the
    MAX_LIMIT-provisional order reaches the `not max_v.provisional` clause. The
    failure it guards against: one measured end lights the travel up as real
    crash protection.
    """

    TAG = "# ASSUMPTION: synthetic - this end of the travel is left unmeasured.\n"
    NEUTRAL = "# synthetic - this end of the travel counts as measured.\n"

    def _text(self, provisional_key: str) -> str:
        text = _strip_markers(_test_rig_text())
        text = re.sub(
            r"(?m)^({}\s*=)".format(provisional_key), self.TAG + r"\1", text
        )
        if provisional_key == "MIN_LIMIT":
            # Without this, MIN's new ASSUMPTION block would govern MAX as well.
            text = re.sub(r"(?m)^(MAX_LIMIT\s*=)", self.NEUTRAL + r"\1", text)
        return text

    def test_one_provisional_end_keeps_the_whole_travel_unmeasured(self):
        for key, other in (("MAX_LIMIT", "MIN_LIMIT"), ("MIN_LIMIT", "MAX_LIMIT")):
            with self.subTest(provisional=key):
                profile = _profile_for_ini(self, self._text(key))
                ini = load_ini(profile.ini_path)
                for joint in range(3):  # premise: exactly one end is provisional
                    sec = "JOINT_{}".format(joint)
                    self.assertTrue(ini.require(sec, key).provisional)
                    self.assertFalse(ini.require(sec, other).provisional)

                cfg = load_machine_config(profile)
                self.assertEqual(len(cfg.axes), 3)
                for a in cfg.axes:
                    self.assertFalse(a.travel.measured, a.axis)
                    self.assertEqual(a.travel.display, UNMEASURED)
                    self.assertFalse(a.travel.has_crash_protection)
                self.assertFalse(cfg.soft_limits_configured)
                self.assertIn("KORUMA YOK", R.render_body(_build(profile, "makine")))


# ---------------------------------------------------------------------------
# Sparse INIs: fallbacks must mirror LinuxCNC or say "—", never invent
# ---------------------------------------------------------------------------


class TestSparseIni(unittest.TestCase):
    def test_missing_coordinates_falls_back_to_linuxcnc_default_xyz(self):
        """No [TRAJ]COORDINATES maps joints 0-2 to X, Y, Z. The fallback is
        LinuxCNC's own, not ours: src/emc/ini/initraj.cc:139-140 sets the axis
        mask to X|Y|Z ("default: XYZ machine") when COORDINATES is missing. If
        the fallback drifted, the UI would label joints differently from the
        controller it describes."""
        cfg = load_machine_config(_profile_for_ini(self, THREE_JOINTS))
        self.assertEqual(
            [(a.axis, a.joint) for a in cfg.axes], [("X", 0), ("Y", 1), ("Z", 2)]
        )

    def test_coordinate_letter_without_a_joint_section_invents_nothing(self):
        """A COORDINATES letter with no [JOINT_n] behind it is skipped. Without
        the guard, ini.require() raises KeyError and the UI does not start at
        all. The guard must not produce a fourth axis built from nothing."""
        profile = _profile_for_ini(self, "[TRAJ]\nCOORDINATES = X Y Z A\n" + THREE_JOINTS)
        cfg = load_machine_config(profile)
        self.assertEqual([a.axis for a in cfg.axes], ["X", "Y", "Z"])
        self.assertIsNone(cfg.axis("A"))
        self.assertEqual(sorted(joint_config_from_profile(profile)), [0, 1, 2])
        self.assertEqual(len(_build(profile, "makine")["makine"]["axes"]), 3)

    def test_ini_without_joints_never_claims_soft_limits(self):
        """all() over an empty list is True. The `bool(self.axes)` guard in
        MachineConfig.soft_limits_configured is what stops an INI with no
        [JOINT_n] from reading as fully crash-protected. Step timing must be
        absent too: TimingConfig is copied from the first axis, and there is
        none."""
        profile = _profile_for_ini(self, "[TRAJ]\nCOORDINATES = X Y Z\nLINEAR_UNITS = mm\n")
        cfg = load_machine_config(profile)
        self.assertTrue(cfg.present)
        self.assertEqual(cfg.axes, [])
        self.assertIsNone(cfg.timing)
        self.assertFalse(cfg.soft_limits_configured)
        vm = _build(profile, "makine")
        self.assertEqual(vm["makine"]["timing_rows"], [])
        self.assertIn("KORUMA YOK", R.render_body(vm))

    def test_ini_without_joints_says_why_start_is_blocked(self):
        """Round-1 finding: with no [JOINT_n] the homing block rendered as
        "Referans alınmadı: . Sıra: ." The block itself was right
        (HomingState.all_homed is False for an empty set); the text named no
        axis and no order. It must say why, and start must stay refused even
        after a reset, a home-all and a loaded program."""
        expected = "Referans durumu bilinmiyor: config'de eklem ([JOINT_n]) tanımlı değil."
        profile = _profile_for_ini(self, "[TRAJ]\nCOORDINATES = X Y Z\nLINEAR_UNITS = mm\n")
        adapter = MockAdapter(profile)
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.load_program("x.ngc")
        snap = adapter.snapshot()
        self.assertEqual(snap.homing.joints, ())  # premise
        self.assertEqual(snap.start_blocks, [expected])
        self.assertFalse(snap.can_start_program)
        adapter.program_start()
        self.assertFalse(adapter.snapshot().program_running)
        self.assertEqual(adapter.snapshot().message, "Başlatılamaz: " + expected)
        gate = VM.build(profile, load_machine_config(profile), snap, "kontrol")["kontrol"]["gate"]
        self.assertEqual(gate["headline"], expected)
        # The message with joints present is unchanged.
        _, _, unhomed = session("test-rig")
        self.assertTrue(
            unhomed.snapshot().start_blocks[-1].startswith("Referans alınmadı: X, Y, Z. Sıra: ")
        )

    def test_empty_joint_set_message_comes_from_the_state_itself(self):
        """The same rule on a bare MachineState, so it holds for any adapter."""
        _, _, ready = _enabled_session()
        empty = ready._replace(homing=HomingState(joints=()))
        self.assertEqual(
            empty.start_blocks,
            ["Referans durumu bilinmiyor: config'de eklem ([JOINT_n]) tanımlı değil."],
        )
        self.assertNotIn(": .", " ".join(empty.start_blocks))

    def test_missing_middle_joint_is_skipped_without_renumbering(self):
        """configmodel skips a COORDINATES letter with no [JOINT_n] and keeps
        going (round-1 mutant C04 ended the scan instead). Joint numbers must
        not shift: with trivkins joint n is the nth letter, so Z stays joint 2
        and keeps [JOINT_2]'s values. A renumbered Z would make the adapter
        jog or report LinuxCNC's joint 1 when the operator means Z."""
        original = ini_raw_values()
        text, n = re.subn(r"(?m)^\[JOINT_1\]", "[JOINT_1_REMOVED]", _test_rig_text())
        self.assertEqual(n, 1)  # premise: only the header, not the comments citing it
        profile = _profile_for_ini(self, text)
        cfg = load_machine_config(profile)
        self.assertEqual([(a.axis, a.joint) for a in cfg.axes], [("X", 0), ("Z", 2)])
        self.assertEqual(cfg.axis("Z").scale_steps_per_mm, float(original["JOINT_2.SCALE"]))
        self.assertIsNone(cfg.axis("Y"))
        joints = joint_config_from_profile(profile)
        self.assertEqual(sorted(joints), [0, 2])
        self.assertEqual(joints[2]["axis"], "Z")
        self.assertEqual(
            joints[2]["HOME_SEQUENCE"], int(float(original["JOINT_2.HOME_SEQUENCE"]))
        )
        rows = _build(profile, "makine")["makine"]["axes"]
        self.assertEqual([(r["axis"], r["joint"]) for r in rows], [("X", 0), ("Z", 2)])

    def test_absent_optional_keys_render_as_dashes(self):
        """The configmodel module docstring: no hardware number is hardcoded.
        If the INI lacks a key, the Makine screen says "—". It does not show a
        plausible default such as trivkins, 3 joints, mm or 1 ms."""
        profile = _profile_for_ini(self, THREE_JOINTS)
        cfg = load_machine_config(profile)
        for field in (
            "kinematics", "joints", "units", "board_ip", "hal_prefix",
            "hm2_config_string", "servo_period_ms",
        ):
            with self.subTest(field=field):
                self.assertIsNone(getattr(cfg, field))
        m = _build(profile, "makine")["makine"]
        config_rows = _rows(m["config_rows"])
        for label in ("Kinematik", "Joint sayısı", "Birim", "hm2 config", "Servo döngü"):
            with self.subTest(row=label):
                self.assertEqual(config_rows[label], "—")
        link_rows = {r["label"]: r for r in m["link_rows"]}
        self.assertEqual(link_rows["Kart"]["value"], "—")
        # The link card has no default either (LinkHealth.servo_period_ms is
        # None), and an unknown reads WARN, not the INFO of a real value.
        self.assertEqual(
            (link_rows["Servo döngü"]["value"], link_rows["Servo döngü"]["severity"]),
            ("—", "warn"),
        )

    def test_servo_period_row_is_converted_from_nanoseconds(self):
        """[EMCMOT]SERVO_PERIOD is in nanoseconds (cited in test-rig.ini as
        ini-config.adoc:597). The test rig's 1000000 ns gives 1 ms, which is the
        same number before and after a /1e6. A value other than 1 shows that the
        conversion is really applied."""
        profile = _profile_for_ini(self, "[EMCMOT]\nSERVO_PERIOD = 500000\n" + THREE_JOINTS)
        m = _build(profile, "makine")["makine"]
        self.assertEqual(_rows(m["config_rows"])["Servo döngü"], "0.5 ms")

    def test_link_card_servo_period_never_contradicts_the_ini(self):
        """Round-1 defect, fixed in adapter.py (_servo_period_ms): the link
        card's "Servo döngü" was a hardcoded 1.0 ms in MockAdapter, so an INI
        period of 0.5 ms showed as "0.5 ms" in the config card and "1 ms" in
        the link card of the same Makine screen.

        Both cards must now show the INI's own value. 1000000 ns (the test
        rig's value) is avoided: it equals the old hardcoded 1.0 and would
        pass either way. 0 is not a usable period, but it is what the INI
        says, and the config card used to test the value's truth, not its
        presence, so it showed "—" beside the link card's "0 ms"."""
        for ns, shown in (("500000", "0.5 ms"), ("2000000", "2 ms"), ("0", "0 ms")):
            with self.subTest(servo_period_ns=ns):
                profile = _profile_for_ini(
                    self, "[EMCMOT]\nSERVO_PERIOD = {}\n".format(ns) + THREE_JOINTS
                )
                m = _build(profile, "makine")["makine"]
                self.assertEqual(_rows(m["config_rows"])["Servo döngü"], shown)
                self.assertEqual(_rows(m["link_rows"])["Servo döngü"], shown)


# ---------------------------------------------------------------------------
# INI loader: comment runs and continuation lines
# ---------------------------------------------------------------------------


class TestIniCommentRuns(unittest.TestCase):
    """The ASSUMPTION rule (iniload.parse_ini_text): a comment block governs
    the keys after it. Every test here would let a guessed number read as
    measured if the rule regressed."""

    def test_unparsed_line_inside_an_assumption_run_keeps_the_taint(self):
        """A line the loader cannot parse clears only the pending comment
        block. It does not clear the comment already governing the run. If it
        did, every limit after a stray line would leak as measured.

        The stray line is real upstream syntax with no "=":
        configs/by_machine/boss/boss.ini:105 in LinuxCNC 2.9."""
        ini = parse_ini_text(
            "[JOINT_0]\n"
            "# ASSUMPTION: travel guessed.\n"
            "MIN_LIMIT = 0.0\n"
            "TOOL_CHANGE_POSITION    -18.0 -12.0 0.0\n"
            "MAX_LIMIT = 150.0\n"
        )
        self.assertTrue(ini.require("JOINT_0", "MIN_LIMIT").provisional)
        self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)
        self.assertEqual(ini.keys("JOINT_0"), ["MIN_LIMIT", "MAX_LIMIT"])

    def test_unparsed_line_between_the_block_and_its_first_key_keeps_the_taint(self):
        """Round-1 mutant I01: the stray line sits between the ASSUMPTION
        block and the first key it governs. Before the fix the stray line
        dropped the pending block, so both limits read as measured."""
        ini = parse_ini_text(
            "[JOINT_0]\n"
            "FERROR = 0.05\n"
            "# ASSUMPTION: travel guessed.\n"
            "TOOL_CHANGE_POSITION    -18.0 -12.0 0.0\n"
            "MIN_LIMIT = 0.0\n"
            "MAX_LIMIT = 150.0\n"
        )
        self.assertFalse(ini.require("JOINT_0", "FERROR").provisional)  # premise
        for key in ("MIN_LIMIT", "MAX_LIMIT"):
            with self.subTest(key=key):
                value = ini.require("JOINT_0", key)
                self.assertTrue(value.provisional)
                self.assertIn("ASSUMPTION: travel guessed.", value.comment)

    def test_stray_line_above_the_real_limits_keeps_the_travel_unmeasured(self):
        """The same rule end to end on the real test-rig.ini: a stray line
        inserted right above each MIN_LIMIT must not light the travel up.
        (Six lines: three [JOINT_n] and three [AXIS_*] carry MIN_LIMIT.)"""
        text = re.sub(
            r"(?m)^(MIN_LIMIT\s*=)", "STRAY_LINE_WITHOUT_EQUALS 1 2 3\n\\1", _test_rig_text()
        )
        self.assertEqual(text.count("STRAY_LINE_WITHOUT_EQUALS"), 6)  # premise
        cfg = load_machine_config(_profile_for_ini(self, text))
        self.assertEqual([a.axis for a in cfg.axes], ["X", "Y", "Z"])
        for a in cfg.axes:
            with self.subTest(axis=a.axis):
                self.assertFalse(a.travel.measured)
                self.assertEqual(a.travel.display, UNMEASURED)
        self.assertFalse(cfg.soft_limits_configured)

    def test_marker_block_directly_above_a_section_governs_its_first_keys(self):
        """iniload.py, the section branch: a block directly above a header is
        that header's comment and governs the section's keys until the next
        block. Round-1 mutant I02 dropped it."""
        ini = parse_ini_text(
            "[EMCMOT]\n"
            "SERVO_PERIOD = 1000000\n"
            "# PROVISIONAL travel\n"
            "[JOINT_0]\n"
            "MAX_LIMIT = 150.0\n"
            "MIN_LIMIT = 0.0\n"
            "# measured with a rule\n"
            "SCALE = 800.0\n"
        )
        self.assertFalse(ini.require("EMCMOT", "SERVO_PERIOD").provisional)
        self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)
        self.assertTrue(ini.require("JOINT_0", "MIN_LIMIT").provisional)
        # The next block takes over: the header's block does not run on.
        self.assertFalse(ini.require("JOINT_0", "SCALE").provisional)

    def test_each_marker_alone_is_enough(self):
        """Literal markers, not a loop over PROVISIONAL_MARKERS: a loop over
        the tuple cannot notice an entry being dropped from it (round-1
        mutant I04). The test-rig INI writes "PROVISIONAL" and "MUST BE
        MEASURED" in its soft-limit headers."""
        for marker in ("PROVISIONAL", "MUST BE MEASURED", "ASSUMPTION:"):
            with self.subTest(marker=marker):
                ini = parse_ini_text(
                    "[JOINT_0]\n# {}\nMAX_LIMIT = 150.0\n".format(marker)
                )
                self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)
        self.assertFalse(
            parse_ini_text("[JOINT_0]\n# measured\nMAX_LIMIT = 150.0\n")
            .require("JOINT_0", "MAX_LIMIT").provisional
        )


class TestIniEmptyValues(unittest.TestCase):
    def test_key_with_an_empty_value_is_absent_and_keeps_the_taint(self):
        """"KEY =" is not found by LinuxCNC (AfterEqual returns NULL,
        inifile.cc:359-363, :517-519). It must not show as a value, and it
        must not consume the ASSUMPTION block above it."""
        ini = parse_ini_text(
            "[JOINT_0]\n# ASSUMPTION: travel guessed.\nMIN_LIMIT =   \nMAX_LIMIT = 150.0\n"
        )
        self.assertIsNone(ini.get("JOINT_0", "MIN_LIMIT"))
        self.assertEqual(ini.keys("JOINT_0"), ["MAX_LIMIT"])
        self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)


class TestIniContinuationLines(unittest.TestCase):
    """LinuxCNC joins a line ending in "\\" with the next one
    (src/libnml/inifile/inifile.cc:295-323). Upstream configs use it, e.g.
    configs/sim/qtdragon/qtdragon_tool_probe/qtdragon_auto_tool_probe.ini:103-106."""

    def test_backslash_continuation_is_joined_like_linuxcnc(self):
        """Round-1 defect, fixed: the value was cut at the backslash and the
        continuation became a bogus key of its own."""
        ini = parse_ini_text(
            "[HOSTMOT2]\n"
            'CONFIG = "num_encoders=0 num_pwmgens=0 \\\n'
            'num_stepgens=3"\n'
            "BOARD = 7i92\n"
        )
        self.assertEqual(
            ini.require("HOSTMOT2", "CONFIG").raw,
            '"num_encoders=0 num_pwmgens=0 num_stepgens=3"',
        )
        self.assertEqual(ini.keys("HOSTMOT2"), ["CONFIG", "BOARD"])

    def test_several_continuations_join_verbatim(self):
        """Only the backslash is removed (inifile.cc:297-298); the next line
        is appended as it is, leading spaces included (:304, :319)."""
        ini = parse_ini_text("[S]\nK = a\\\n  b\\\nc\n")
        self.assertEqual(ini.require("S", "K").raw, "a  bc")

    def test_continued_hm2_config_reaches_the_makine_screen_whole(self):
        """configmodel shows [HOSTMOT2]CONFIG verbatim (minus quotes)."""
        profile = _profile_for_ini(
            self,
            '[HOSTMOT2]\nCONFIG = "num_encoders=0 \\\nnum_stepgens=3"\n' + THREE_JOINTS,
        )
        self.assertEqual(
            load_machine_config(profile).hm2_config_string,
            "num_encoders=0 num_stepgens=3",
        )
        m = _build(profile, "makine")["makine"]
        self.assertEqual(_rows(m["config_rows"])["hm2 config"], "num_encoders=0 num_stepgens=3")

    def test_comment_ending_in_a_backslash_swallows_the_next_line(self):
        """LinuxCNC joins before it looks at the line (inifile.cc:295-330), so
        the key under a "\\"-ended comment is part of the comment and is not
        found. The UI must not show a value LinuxCNC never reads, and the key
        after it is still governed by the ASSUMPTION block."""
        ini = parse_ini_text(
            "[JOINT_0]\n"
            "# ASSUMPTION: travel guessed \\\n"
            "MIN_LIMIT = 0.0\n"
            "MAX_LIMIT = 150.0\n"
        )
        self.assertIsNone(ini.get("JOINT_0", "MIN_LIMIT"))
        self.assertTrue(ini.require("JOINT_0", "MAX_LIMIT").provisional)

    def test_continuation_open_at_end_of_file_is_not_found(self):
        """LinuxCNC reaches end of file while extending and reports the key
        as not found (inifile.cc:273-277)."""
        ini = parse_ini_text("[S]\nA = 1\nK = 2 \\\n")
        self.assertEqual(ini.keys("S"), ["A"])
        # Without a final newline the backslash is the value's last character.
        self.assertEqual(parse_ini_text("[S]\nK = 2\\").require("S", "K").raw, "2\\")

    def test_dos_line_ending_is_not_a_continuation(self):
        """Only "\\n" ends a line (fgets); in "\\\\\\r\\n" the backslash is not
        the last character, so LinuxCNC does not join. load_ini must keep the
        "\\r" (open(newline="")) for this to hold on a file."""
        path = _ini_file(self, "[S]\r\nK = a \\\r\nB = 2\r\n")
        ini = load_ini(path)
        self.assertEqual(ini.keys("S"), ["K", "B"])
        self.assertEqual(ini.require("S", "K").raw, "a \\")
        self.assertEqual(ini.require("S", "B").raw, "2")


class TestIniSectionNames(unittest.TestCase):
    """LinuxCNC finds [NAME] as a prefix of a line (inifile.cc:227, :261) and
    ends a section at ANY line starting with "[" (:337-340). The old
    SECTION_RE accepted only [A-Za-z0-9_] names; keys after any other header
    were filed under the previous section and could overwrite it."""

    def test_keys_after_an_unusual_header_do_not_overwrite_the_previous_section(self):
        ini = parse_ini_text(
            "[JOINT_0]\nSCALE = 800.0\n"
            "[SPINDLE-0]\nSCALE = 999.0\n"
            "[JOINT 1]\nSCALE = 555.0\n"
        )
        self.assertEqual(ini.require("JOINT_0", "SCALE").raw, "800.0")
        self.assertEqual(ini.require("SPINDLE-0", "SCALE").raw, "999.0")
        self.assertEqual(ini.require("JOINT 1", "SCALE").raw, "555.0")
        self.assertNotIn(("JOINT_1", "SCALE"), ini)  # "[JOINT 1]" is not JOINT_1

    def test_header_with_trailing_text_still_names_the_section(self):
        """strncmp against "[JOINT_0]" matches a longer line (inifile.cc:261)."""
        ini = parse_ini_text("[JOINT_0] ; X axis\nSCALE = 800.0\n")
        self.assertEqual(ini.require("JOINT_0", "SCALE").raw, "800.0")

    def test_header_without_a_closing_bracket_hides_its_keys(self):
        """No "[NAME]" can match "[BROKEN", but it still ends the previous
        section, so its keys belong nowhere LinuxCNC looks."""
        ini = parse_ini_text("[JOINT_0]\nSCALE = 800.0\n[BROKEN\nSCALE = 999.0\n")
        self.assertEqual(ini.require("JOINT_0", "SCALE").raw, "800.0")
        self.assertEqual(ini.sections(), ["JOINT_0"])

    def test_unusual_header_inside_the_joints_leaves_the_axis_config_intact(self):
        """End to end: before the fix X's SCALE became 999 here."""
        text = THREE_JOINTS.replace(
            "\n[JOINT_1]\n", "\n[SPINDLE-0]\nSCALE = 999.0\nMAX_LIMIT = 9.0\n\n[JOINT_1]\n"
        )
        self.assertIn("[SPINDLE-0]", text)  # premise
        cfg = load_machine_config(_profile_for_ini(self, text))
        x = cfg.axis("X")
        self.assertEqual(x.scale_steps_per_mm, 111.0)
        self.assertEqual(x.travel.max_limit, 123.0)


class TestIniRepeats(unittest.TestCase):
    """For a repeated key or a repeated [SECTION] LinuxCNC reads the FIRST.
    IniFile::Find (num = 1 unless asked, inifile.hh) stops its section search
    at the first "[SECTION]" line (inifile.cc:261-267), ends the key search at
    the next "[" (:337-340) and returns the first matching key line (:354).
    iniload used to keep the LAST, so a value appended below the original
    showed on screen while LinuxCNC kept running on the original."""

    def test_repeated_key_keeps_the_first_value(self):
        ini = parse_ini_text("[S]\nK = 1\nK = 2\n")
        self.assertEqual(ini.require("S", "K").raw, "1")
        self.assertEqual(ini.keys("S"), ["K"])

    def test_first_line_without_a_value_hides_the_key(self):
        """The first matching line decides even without a value: Find throws
        ERR_TAG_NOT_FOUND (inifile.cc:359-363) and never reaches the second."""
        ini = parse_ini_text("[S]\nK =\nK = 2\nL = 3\n")
        self.assertIsNone(ini.get("S", "K"))
        self.assertEqual(ini.keys("S"), ["L"])

    def test_repeated_section_is_never_read(self):
        """Not even for a key the first occurrence lacks. A section that
        starts after the repeat is read as usual."""
        ini = parse_ini_text(
            "[S]\nA = 1\n[T]\nX = 0\n[S]\nA = 2\nB = 3\n[U]\nY = 4\n"
        )
        self.assertEqual(ini.require("S", "A").raw, "1")
        self.assertIsNone(ini.get("S", "B"))
        self.assertEqual(ini.sections(), ["S", "T", "U"])
        self.assertEqual(ini.require("U", "Y").raw, "4")

    def test_the_value_read_keeps_its_own_marker(self):
        """The ASSUMPTION marker that counts is the one above the line LinuxCNC
        reads. A "measured" value appended below a placeholder must not make
        the placeholder look measured, and the reverse."""
        cases = (
            ("# ASSUMPTION: guessed.\nMAX_LIMIT = 150.0\n\n# measured\nMAX_LIMIT = 200.0\n", True),
            ("MAX_LIMIT = 150.0\n# ASSUMPTION: guessed.\nMAX_LIMIT = 200.0\n", False),
        )
        for body, provisional in cases:
            with self.subTest(provisional=provisional):
                v = parse_ini_text("[JOINT_0]\n" + body).require("JOINT_0", "MAX_LIMIT")
                self.assertEqual((v.raw, v.provisional), ("150.0", provisional))

    def test_appended_corrections_do_not_reach_the_screens(self):
        """End to end on the test rig's INI: a second SERVO_PERIOD line and a
        second [JOINT_0] with unmarked limits change nothing LinuxCNC reads.
        With last-wins, X's travel showed as measured (-5 to 5) and the servo
        period as 0.5 ms."""
        original = ini_raw_values()
        text, n = re.subn(
            r"(?m)^(SERVO_PERIOD\s*=.*)$", r"\1\nSERVO_PERIOD = 500000", _test_rig_text()
        )
        self.assertEqual(n, 1)  # premise: the edit landed inside [EMCMOT]
        text += "\n[JOINT_0]\nSCALE = 999.0\nMIN_LIMIT = -5.0\nMAX_LIMIT = 5.0\n"
        profile = _profile_for_ini(self, text)
        cfg = load_machine_config(profile)
        x = cfg.axis("X")
        self.assertEqual(x.scale_steps_per_mm, float(original["JOINT_0.SCALE"]))
        self.assertFalse(x.travel.measured)
        period = "{:g} ms".format(float(original["EMCMOT.SERVO_PERIOD"]) / 1e6)
        m = _build(profile, "makine")["makine"]
        self.assertEqual(_rows(m["config_rows"])["Servo döngü"], period)
        self.assertEqual(_rows(m["link_rows"])["Servo döngü"], period)
        self.assertFalse(m["soft_limits_configured"])


# ---------------------------------------------------------------------------
# HOME_IGNORE_LIMITS read the way LinuxCNC reads a boolean
# ---------------------------------------------------------------------------


def _with_home_ignore_limits(value: str, count: int = 0) -> str:
    """test-rig.ini with the first `count` (0 = all) HOME_IGNORE_LIMITS set to `value`."""
    line = "HOME_IGNORE_LIMITS = {}".format(value).rstrip()
    text, n = re.subn(
        r"(?m)^HOME_IGNORE_LIMITS\s*=.*$", lambda _m: line, _test_rig_text(), count=count
    )
    assert n == (count or 3), n
    return text


class TestHomeIgnoreLimitsParsing(unittest.TestCase):
    """EmcIniFile::Find(bool) (src/emc/ini/emcIniFile.cc:50-71) reads an
    integer prefix first (any non-zero is true, inifile.cc:122-127), then
    TRUE/YES/1 or FALSE/NO/0 case-insensitively (:129-137). Anything else is a
    conversion error (:139) that fails the joint load (inijoint.cc:102,
    :236-239). The old rule was `== "YES"`: "1" or "TRUE" showed as False
    while LinuxCNC ignored the limits (round-1 mutant C08), and garbage showed
    as a definite False."""

    CASES = (
        ("YES", True), ("yes", True), ("TRUE", True), ("true", True),
        ("1", True), ("2", True), ("-1", True), ("0x10", True),
        # sscanf %i takes an upper-case prefix too (round-2 mutant N115 read
        # "0X1" as octal, and int("0X1", 8) raised inside the config load).
        ("0X1", True), ("0X0", False),
        ("NO", False), ("no", False), ("FALSE", False), ("0", False), ("0x0", False),
        ("garbage", None), ("Y", None), ("on", None), ("1.5x", True), ("08", False),
        # strcasecmp is ASCII-only; Python's upper() maps U+017F to "S".
        ("yeſ", None),
    )

    def test_values_through_load_machine_config(self):
        for raw, expected in self.CASES:
            with self.subTest(raw=raw):
                profile = _profile_for_ini(self, _with_home_ignore_limits(raw))
                cfg = load_machine_config(profile)
                self.assertEqual([a.home_ignore_limits for a in cfg.axes], [expected] * 3)
                self.assertEqual([a.home_ignore_limits_raw for a in cfg.axes], [raw] * 3)
                self.assertEqual(
                    [j["HOME_IGNORE_LIMITS"] for j in joint_config_from_profile(profile).values()],
                    [expected] * 3,
                )

    def test_empty_value_is_absent_like_in_linuxcnc(self):
        """LinuxCNC does not find "HOME_IGNORE_LIMITS =" at all
        (inifile.cc:359-363) and uses its default. The UI treats it as the
        missing key it is and refuses to invent that default, as it does for
        every required joint key (iniload.IniFile.require). The profile reads
        its travel facts from the same INI, so building it fails the same way."""
        path = _ini_file(self, _with_home_ignore_limits(""))
        for build in (load_config_from_ini, build_test_rig_profile):
            with self.subTest(build=build.__name__):
                with self.assertRaises(KeyError) as ctx:
                    build(path)
                self.assertIn("[JOINT_0]HOME_IGNORE_LIMITS missing", str(ctx.exception))

    def test_integer_truth_shows_the_shared_switch_warning(self):
        """LinuxCNC ignores the limits for "1" and "TRUE" too, so the HOME =
        LIMIT banner must be up for them, and down for "NO"."""
        for raw, banner in (("1", True), ("TRUE", True), ("NO", False)):
            with self.subTest(raw=raw):
                profile = _profile_for_ini(self, _with_home_ignore_limits(raw))
                vm = _build(profile, "kontrol")
                homing = vm["kontrol"]["homing"]
                self.assertIs(homing["shares_home_and_limit"], banner)
                self.assertFalse(homing["ignore_limits_unknown"])
                self.assertEqual("HOME = LIMIT" in R.render_body(vm), banner)

    def test_unreadable_value_is_named_never_read_as_limits_active(self):
        """An unreadable value must not make the banner disappear: whether
        the limits are ignored is unknown, and the operator is told which
        joint and which text."""
        profile = _profile_for_ini(self, _with_home_ignore_limits("garbage"))
        vm = _build(profile, "kontrol")
        homing = vm["kontrol"]["homing"]
        self.assertEqual([j["ignore_limits"] for j in homing["joints"]], [None] * 3)
        self.assertFalse(homing["shares_home_and_limit"])
        self.assertTrue(homing["ignore_limits_unknown"])
        self.assertIn(
            "HOME_IGNORE_LIMITS okunamadı: X ('garbage'), Y ('garbage'), Z ('garbage').",
            homing["shares_warning"],
        )
        self.assertIn("bilinmiyor", homing["shares_warning"])
        self.assertIn("HOME = LIMIT", R.render_body(vm))

    def test_one_unreadable_joint_keeps_the_others_readable(self):
        """Only JOINT_0 (X) is garbled. Y and Z still share home and limit,
        so both sentences are on the banner."""
        profile = _profile_for_ini(self, _with_home_ignore_limits("evet", count=1))
        homing = _build(profile, "kontrol")["kontrol"]["homing"]
        by_axis = {j["axis"]: j["ignore_limits"] for j in homing["joints"]}
        self.assertEqual(by_axis, {"X": None, "Y": True, "Z": True})
        self.assertTrue(homing["shares_home_and_limit"])
        self.assertIn("HOME_IGNORE_LIMITS okunamadı: X ('evet').", homing["shares_warning"])
        self.assertIn("durduracak hiçbir şey", homing["shares_warning"])


# ---------------------------------------------------------------------------
# The profile's travel facts follow the INI (the iniload module docstring)
# ---------------------------------------------------------------------------


def _turkish_lower(text: str) -> str:
    return text.replace("İ", "i").replace("I", "ı").lower()


def _says_travel_unmeasured(markup: str) -> bool:
    t = _turkish_lower(markup)
    return "strok ölçülmedi" in t or "strok ölçülmediği" in t


def _joint_section(text: str, n: int):
    """(before, section, after) around the [JOINT_n] header in `text`."""
    start = re.search(r"(?m)^\[JOINT_{}\]".format(n), text).start()
    nxt = text.find("\n[", start + 1)
    end = len(text) if nxt == -1 else nxt + 1
    return text[:start], text[start:end], text[end:]


class TestProfileTravelFollowsTheIni(unittest.TestCase):
    """The iniload module docstring: removing the ASSUMPTION markers once
    travel is measured updates the UI with no code change. The SOFT_LIMITS
    capability, travel_measured and the Z-bottom consequence used to be
    hardcoded as "not measured" in profiles.py, so they kept saying so after
    that edit. They are now derived from
    load_config_from_ini(...).soft_limits_configured."""

    def _soft_limits(self, profile):
        validate_profile(profile)  # the catalogue rules still hold either way
        return profile.capability(Capability.SOFT_LIMITS)

    def test_real_ini_still_reports_unmeasured_travel(self):
        profile = load_profile("test-rig")
        cap = self._soft_limits(profile)
        self.assertIs(cap.presence, Presence.ABSENT)
        self.assertEqual(
            cap.reason,
            "Strok ÖLÇÜLMEDİ (X, Y, Z). INI'deki değerler yer tutucu (ASSUMPTION) "
            "— gerçek çarpışma koruması yok.",
        )
        self.assertTrue(cap.requirement)
        self.assertIn("configs/test-rig/test-rig.ini", cap.source)
        self.assertFalse(profile.travel_measured)
        self.assertIn("strok ölçülmediği için tanımsız", profile.missing_switches[0].consequence)
        self.assertTrue(_says_travel_unmeasured(R.render_body(_build(profile, "kontrol"))))

    def test_markers_removed_reports_measured_travel_everywhere(self):
        profile = _profile_for_ini(self, _strip_markers(_test_rig_text()))
        cap = self._soft_limits(profile)
        self.assertIs(cap.presence, Presence.PRESENT)
        self.assertEqual(cap.reason, "")
        self.assertIn("ASSUMPTION işareti yok", cap.source)
        self.assertTrue(profile.travel_measured)
        self.assertNotIn("ölçülmedi", profile.travel_note)
        z_bottom = profile.missing_switches[0]
        self.assertEqual((z_bottom.axis, z_bottom.end), ("Z", "-"))
        self.assertIn("switch YOK", z_bottom.consequence)
        self.assertIn("referans alındıktan sonra", z_bottom.consequence)
        self.assertIn("command.c:154-175", z_bottom.source)
        panel = {p["key"]: p for p in VM.capability_panels(profile)}["soft_limits"]
        self.assertEqual((panel["badge"], panel["enabled"]), ("var", True))
        for screen in VM.SCREENS:
            with self.subTest(screen=screen[0]):
                markup = R.render_body(_build(profile, screen[0]))
                self.assertFalse(_says_travel_unmeasured(markup))

    def test_one_unmeasured_axis_is_named_alone(self):
        """Only [JOINT_1] (Y) keeps a marker. SOFT_LIMITS stays ABSENT and
        names Y alone; Z is measured, so its bottom-end text is the measured
        one."""
        before, joint1, after = _joint_section(_strip_markers(_test_rig_text()), 1)
        joint1 = re.sub(r"(?m)^(MIN_LIMIT\s*=)", "# ASSUMPTION: Y not measured.\n\\1", joint1)
        profile = _profile_for_ini(self, before + joint1 + after)
        cap = self._soft_limits(profile)
        self.assertIs(cap.presence, Presence.ABSENT)
        self.assertTrue(cap.reason.startswith("Strok ÖLÇÜLMEDİ (Y)."), cap.reason)
        self.assertFalse(profile.travel_measured)
        self.assertIn("referans alındıktan sonra", profile.missing_switches[0].consequence)

    def test_missing_limit_is_not_called_a_placeholder(self):
        """No MAX_LIMIT on Z: LinuxCNC has no placeholder to show there, so
        the reason must not say "yer tutucu (ASSUMPTION)" about Z."""
        before, joint2, after = _joint_section(_strip_markers(_test_rig_text()), 2)
        joint2 = re.sub(r"(?m)^MAX_LIMIT\s*=.*\n", "", joint2)
        profile = _profile_for_ini(self, before + joint2 + after)
        cap = self._soft_limits(profile)
        self.assertIs(cap.presence, Presence.ABSENT)
        self.assertEqual(
            cap.reason, "MIN_LIMIT/MAX_LIMIT eksik (Z) — gerçek çarpışma koruması yok."
        )
        self.assertIn("strok ölçülmediği için tanımsız", profile.missing_switches[0].consequence)

    def test_ini_without_joints_says_so(self):
        profile = _profile_for_ini(self, "[TRAJ]\nCOORDINATES = X Y Z\n")
        cap = self._soft_limits(profile)
        self.assertIs(cap.presence, Presence.ABSENT)
        self.assertTrue(cap.reason.startswith("INI'de eklem ([JOINT_n]) yok"))
        self.assertFalse(profile.travel_measured)


# ---------------------------------------------------------------------------
# Link trouble, status priority, estop with a wired input
# ---------------------------------------------------------------------------


class TestLinkTroubleReachesTheGate(unittest.TestCase):
    """LinkHealth.problems is the explanation. gate_panel copies it into the
    Kontrol screen's warnings, which is where the operator looks when the
    machine stops.

    The watchdog is the test rig's only fault source (test-rig.hal, estop chain
    notes). Its pin is hm2_7i92.0.watchdog.has_bit (watchdog.c:140-144, as
    cited in state.py). LinuxCncAdapter._read_hal reports a lost link when the
    hal module is missing."""

    def test_lost_link_is_named_and_stops_motion(self):
        profile, config, state = _enabled_session()
        lost = state._replace(link=LinkHealth(False, None, None, None, 1.0))
        self.assertIn("Kart bağlantısının durumu okunamıyor — kartla iletişim bilinmiyor.", lost.link.problems)
        gate = VM.build(profile, config, lost, "kontrol")["kontrol"]["gate"]
        self.assertIn("Kart bağlantısının durumu okunamıyor — kartla iletişim bilinmiyor.", gate["warnings"])
        self.assertFalse(gate["can_jog"])
        self.assertFalse(gate["can_start"])

    def test_bitten_watchdog_is_explained_on_the_kontrol_screen(self):
        profile, config, state = _enabled_session()
        bitten = state._replace(link=LinkHealth(True, 0, True, False, 1.0))
        vm = VM.build(profile, config, bitten, "kontrol")
        self.assertTrue(
            any(w.startswith("Kartla bağlantı koptu") for w in vm["kontrol"]["gate"]["warnings"])
        )
        self.assertIn("«Yazılım sıfırla»ya basın", R.render_body(vm))


class TestStatusLabelPriority(unittest.TestCase):
    """MachineState.status_label is the header's one-word summary. An estop
    must never be hidden behind "ÇALIŞIYOR". A homing in progress must not read
    as "REFERANS GEREKLİ", because that tells the operator to press home
    again."""

    def test_status_ladder(self):
        _, _, ready = _enabled_session()
        self.assertEqual(ready.status_label, "HAZIR")
        homing = ready._replace(homing=ready.homing.with_phase("X", HomingPhase.HOMING))
        running = ready._replace(program_file="t.ngc", program_running=True)
        cases = (
            ("homing", homing, "REFERANS ALINIYOR"),
            ("running", running, "ÇALIŞIYOR"),
            ("running+software estop",
             running._replace(estop=running.estop.software_estop()), "DEVRE DIŞI"),
            ("running+chain open",
             running._replace(estop=running.estop.hardware_chain_opened()), "ACİL STOP"),
        )
        for name, state, label in cases:
            with self.subTest(case=name):
                self.assertEqual(state.status_label, label)

    def test_real_mapping_can_report_running_during_estop(self):
        """The ladder's order is not hypothetical. map_linuxcnc_state derives
        program_running from file, paused and interp_state alone, so a stat in
        STATE_ESTOP with a file still READING maps to running=True. The label
        must still say the machine is disabled."""
        st = _map(
            load_profile("test-rig"),
            _stat(task_state=TASK_STATE_ESTOP, file="a.ngc",
                  interp_state=EMC_TASK_INTERP_READING),
            {"connected": True},
        )
        self.assertTrue(st.program_running)  # premise
        self.assertEqual(st.status_label, "DEVRE DIŞI")


class TestWiredEstopInput(unittest.TestCase):
    """Drive power can read LIVE only on a machine with an estop input. No
    profile has one yet, so no other test renders that state."""

    def test_closed_chain_reads_live_and_drops_the_no_input_caveat(self):
        st = _map(
            _wired_profile(),
            _stat(task_state=TASK_STATE_ON, estop=0),
            {"connected": True, "estop_input": True},
        )
        self.assertIs(st.estop.drive_power, DrivePower.LIVE)
        panel = VM.estop_panel(st)
        self.assertEqual(panel["drive_power_severity"], "info")
        self.assertEqual(panel["drive_power_detail"], "Zincir kapalı.")
        self.assertTrue(panel["reset_enabled"])
        markup = R.render_estop(panel)
        self.assertIn("VERİLİYOR", markup)
        # The "LinuxCNC cannot see drive power" banner belongs only to a
        # machine with no input. On a wired one it would be false.
        self.assertNotIn("GİRİŞ YOK", markup)

    def test_startup_estop_latch_does_not_lock_out_the_reset(self):
        """Round-1 defect, fixed in map_linuxcnc_state: drive power used to be
        overridden by stat.estop on a wired machine. stat.estop is io.aux.estop
        (emcmodule.cc:418), simply `!iocontrol.0.emc-enable-in`
        (ioControl.cc:523-526), and it starts at 1 (ioControl.cc:791).

        In the estop-latch arrangement the test rig uses (test-rig.hal "net
        enable-latch", taken from hm2-stepper-eth.hal:195-197), emc-enable-in
        is estop-latch.0.ok-out. That output starts false
        (estop_latch.comp:71) and is set only by a reset edge from
        iocontrol.0.user-request-enable (estop_latch.comp:84-89), which only
        the GUI's estop reset raises (ioControl.cc:1090-1096). So at startup,
        with the chain physically closed, the override reported "KESİLDİ
        (donanım)" and estop_panel disabled the one button that clears the
        latch.

        Drive power now follows the chain input alone: closed is LIVE and open
        is CUT, whatever stat.estop says. The software side stays disabled
        until the reset, and the reset button is enabled exactly when the
        chain is closed."""
        wired = _wired_profile()
        for stat_estop in (1, 0):
            with self.subTest(stat_estop=stat_estop):
                stat = _stat(task_state=TASK_STATE_ESTOP, estop=stat_estop)
                closed = _map(wired, stat, {"connected": True, "estop_input": True})
                self.assertIs(closed.estop.drive_power, DrivePower.LIVE)
                self.assertTrue(closed.estop.software_disabled)
                self.assertTrue(VM.estop_panel(closed)["reset_enabled"])

                opened = _map(wired, stat, {"connected": True, "estop_input": False})
                self.assertIs(opened.estop.drive_power, DrivePower.CUT)
                self.assertFalse(VM.estop_panel(opened)["reset_enabled"])


# ---------------------------------------------------------------------------
# LinuxCNC status unreadable (MachineState.status_known)
# ---------------------------------------------------------------------------


def _outside_buttons(markup: str) -> str:
    """`markup` without its <button> elements. A chip's label ("G54", "100%")
    names a choice the operator can make; it is not a reading."""
    return re.sub(r"<button\b[^>]*>.*?</button>", "", markup, flags=re.S)


def _selected_chips(markup: str):
    """(data-action, data-value) of every button rendered selected ("on")."""
    out = []
    for attrs in re.findall(r"<button\b([^>]*)>", markup):
        cls = re.search(r'class="([^"]*)"', attrs)
        if cls and "on" in cls.group(1).split():
            action = re.search(r'data-action="([^"]*)"', attrs)
            value = re.search(r'data-value="([^"]*)"', attrs)
            out.append((action.group(1) if action else "", value.group(1) if value else ""))
    return out


def _header_state(markup: str):
    """(data-severity, text) of the header's status chip."""
    m = re.search(r'<span class="state" data-severity="([^"]*)"[^>]*>([^<]*)</span>', markup)
    return m.groups() if m else None


class TestStatusUnknown(unittest.TestCase):
    """When stat.poll() fails, LinuxCncAdapter reports status_known=False
    (MachineState.status_known). Every field LinuxCNC's status fills is then
    a placeholder, and the placeholders are a fresh machine's values: every
    axis at 0, G54, feed 100 %, software disabled, nothing homed. Shown as
    they are, they read as a live machine sitting at the origin.

    The states here are hand-built: the mock's fresh snapshot, whose values
    are those placeholders, with a loaded and running program added and only
    status_known changed. Each check is first made with status_known=True, so
    it is known to see the reading it guards against."""

    def setUp(self):
        self.profile, self.config, adapter = session("test-rig")
        self.known = adapter.snapshot()._replace(
            program_file="deneme.ngc", program_running=True, program_progress=0.5
        )
        self.unknown = self.known._replace(status_known=False)

    def vm(self, state, screen="kontrol"):
        return VM.build(self.profile, self.config, state, screen)

    def markup(self, state, screen="kontrol"):
        return R.render_body(self.vm(state, screen))

    def test_no_reading_reaches_any_screen(self):
        kontrol = self.markup(self.known)
        # Premise: with the status known, each reading is on screen.
        self.assertIn("+0.000", kontrol)
        self.assertIn("G54", _outside_buttons(kontrol))
        self.assertTrue({("wcs", "G54"), ("feed", "100")} <= set(_selected_chips(kontrol)))
        self.assertIn("deneme.ngc", self.markup(self.known, "program"))
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                markup = self.markup(self.unknown, screen)
                self.assertNotIn("+0.000", markup)
                self.assertNotIn("deneme.ngc", markup)
                rest = _outside_buttons(markup)
                for reading in ("G54", "100%", "100 %"):
                    self.assertNotIn(reading, rest)
                self.assertEqual(
                    [c for c in _selected_chips(markup) if c[0] in ("wcs", "feed")], []
                )
                self.assertEqual(_header_state(markup), ("error", "DURUM BİLİNMİYOR"))

    def test_dro_keeps_its_rows_with_dashes_and_the_config_facts(self):
        """The DRO says "—" rather than dropping its rows. Travel is a config
        fact and the jog step is the UI's own setting: both stay."""
        known = VM.control_screen(self.profile, self.config, self.known)
        c = VM.control_screen(self.profile, self.config, self.unknown)
        self.assertFalse(c["status_known"])
        self.assertEqual(
            [(a["axis"], a["work"], a["machine"], a["homed"]) for a in c["axes"]],
            [("X", "—", "—", None), ("Y", "—", "—", None), ("Z", "—", "—", None)],
        )
        self.assertEqual((c["wcs"], c["feed_override"]), ("—", None))
        self.assertEqual((known["wcs"], known["feed_override"]), ("G54", 100))  # premise
        self.assertEqual([a["travel"] for a in c["axes"]], [a["travel"] for a in known["axes"]])
        self.assertEqual(c["jog_step"], self.known.jog_step_mm)
        markup = self.markup(self.unknown)
        self.assertEqual(markup.count('<span class="dro-work">—</span>'), 3)
        self.assertIn(("jog_step", str(self.known.jog_step_mm)), _selected_chips(markup))

    def test_dro_rows_come_from_the_config_when_no_positions_are_reported(self):
        """LinuxCncAdapter._status_unreadable reports no positions at all.
        The axes are still the INI's, so the DRO keeps one "—" row each
        instead of going blank, which reads as a broken screen."""
        unknown = self.unknown._replace(positions=())
        c = VM.control_screen(self.profile, self.config, unknown)
        self.assertEqual(
            [(a["axis"], a["work"], a["machine"], a["homed"]) for a in c["axes"]],
            [(a.axis, "—", "—", None) for a in self.config.axes],
        )
        self.assertEqual([a["axis"] for a in c["axes"]], ["X", "Y", "Z"])  # premise
        self.assertEqual(
            [a["travel"] for a in c["axes"]], [a.travel.display for a in self.config.axes]
        )
        self.assertEqual(self.markup(unknown).count('<span class="dro-work">—</span>'), 3)

    def test_header_says_unknown_even_over_a_ready_placeholder(self):
        _, _, ready = _enabled_session()
        self.assertEqual((ready.status_label, ready.status_severity), ("HAZIR", Severity.INFO))
        placeholders = (("fresh", self.unknown), ("ready", ready._replace(status_known=False)))
        for name, state in placeholders:
            with self.subTest(placeholder=name):
                self.assertEqual(state.status_label, "DURUM BİLİNMİYOR")
                self.assertIs(state.status_severity, Severity.ERROR)
        # Drive power is a HAL reading, taken separately: a cut chain still wins.
        cut = self.unknown._replace(estop=self.unknown.estop.hardware_chain_opened())
        self.assertEqual(cut.status_label, "ACİL STOP")

    def test_gate_reasons_are_not_read_off_placeholders(self):
        known = self.known.start_blocks
        self.assertIn("Makine yazılımda devre dışı (acil stop).", known)  # premise
        self.assertTrue(any(b.startswith("Referans alınmadı") for b in known))
        self.assertEqual(self.unknown.start_blocks, [STATUS_UNKNOWN_BLOCK])
        gate = self.vm(self.unknown)["kontrol"]["gate"]
        self.assertEqual((gate["headline"], gate["can_start"]), (STATUS_UNKNOWN_BLOCK, False))
        # A cut chain and a lost link come from HAL pins and are still named.
        lost = self.unknown._replace(
            estop=self.unknown.estop.hardware_chain_opened(),
            link=LinkHealth(False, None, None, None, None),
        )
        self.assertEqual(
            lost.start_blocks,
            ["Sürücü gücü donanımsal kesildi.", STATUS_UNKNOWN_BLOCK, "Karta bağlantı yok."],
        )

    def test_motion_is_off_even_when_the_placeholders_say_ready(self):
        _, _, ready = _enabled_session()
        ready = ready._replace(program_file="deneme.ngc")
        self.assertTrue(ready.can_jog and ready.can_start_program)  # premise
        unknown = ready._replace(status_known=False)
        self.assertFalse(unknown.can_jog)
        self.assertFalse(unknown.can_start_program)
        c = self.vm(unknown)["kontrol"]
        self.assertFalse(c["can_jog"])
        self.assertFalse(c["mdi"]["enabled"])
        self.assertEqual(
            c["mdi"]["disabled_reason"], "Makine durumu bilinmiyor — MDI kabul edilmiyor."
        )
        self.assertEqual(c["jog_disabled_reason"], "Makine durumu bilinmiyor — jog kapalı.")
        self.assertEqual(self.vm(ready)["kontrol"]["jog_disabled_reason"], "")
        self.assertEqual(
            self.vm(self.known)["kontrol"]["jog_disabled_reason"],
            "Makine devre dışı — jog kapalı.",
        )

    def test_estop_and_homing_panels_say_unknown(self):
        known = VM.estop_panel(self.known)
        e = VM.estop_panel(self.unknown)
        self.assertEqual(known["software_label"], "devre dışı")  # premise
        self.assertEqual(
            (e["software_label"], e["software_severity"], e["software_disabled"]),
            ("bilinmiyor", "warn", None),
        )
        # Drive power is a HAL reading and keeps its own row.
        self.assertEqual(e["drive_power_label"], known["drive_power_label"])
        h = VM.homing_panel(self.unknown, self.config)
        self.assertIsNone(h["all_homed"])
        self.assertEqual(
            {(j["phase"], j["label"], j["homed"], j["severity"]) for j in h["joints"]},
            {("unknown", "bilinmiyor", None, "warn")},
        )
        self.assertEqual(h["order_label"], VM.homing_panel(self.known, self.config)["order_label"])
        self.assertEqual(
            re.findall(r'data-phase="([^"]*)"', self.markup(self.unknown)), ["unknown"] * 3
        )

    def test_program_screen_shows_no_file_run_state_or_progress(self):
        known = VM.program_screen(self.profile, self.config, self.known)
        self.assertEqual(
            (known["loaded"], known["running"], known["progress_pct"]),
            ("deneme.ngc", True, 50.0),
        )
        p = VM.program_screen(self.profile, self.config, self.unknown)
        self.assertEqual((p["loaded"], p["running"], p["progress_pct"]), (None, None, None))
        self.assertFalse(p["can_start"])

    def test_kontrol_markup_claims_no_machine_state(self):
        """Fixed defect in render.render_jog: the jog card used to say "Jog devre
        dışı — makine kapalı." and title its buttons "Makine devre dışı"
        whenever jog was off. While the status is unknown, whether the machine
        is off is not known. The view model carries the reason
        (control_screen's jog_disabled_reason). The DRO's home column is
        checked too: it must not say "ref yok" for an unknown flag. Shown
        with no positions, as the real adapter reports it."""
        markup = self.markup(self.unknown._replace(positions=()))
        self.assertNotIn("ref yok", markup)
        jog = re.search(r'<span class="card-title">Jog</span>.*?</section>', markup, re.S)
        self.assertIsNotNone(jog)  # premise: the jog card was found
        self.assertIn("bilinmiyor", jog.group(0))
        self.assertNotIn("makine kapalı", jog.group(0))
        self.assertNotIn('title="Makine devre dışı"', jog.group(0))


# ---------------------------------------------------------------------------
# Alarm log on the real adapter's first render
# ---------------------------------------------------------------------------


class TestEmptyAlarmLog(unittest.TestCase):
    def test_empty_log_says_so(self):
        """LinuxCncAdapter's alarm list starts empty, and map_linuxcnc_state
        defaults to alarms=(). So the real machine's first Alarmlar screen is
        normally this one. It must say "kayıt yok" rather than show a blank card, which
        an operator cannot tell apart from a broken log. It must also not count
        phantom active alarms in the nav badge."""
        profile, config, adapter = session("test-rig")
        state = adapter.snapshot()._replace(alarms=())
        vm = VM.build(profile, config, state, "alarmlar")
        self.assertEqual(vm["alarmlar"]["events"], [])
        self.assertEqual(vm["alarmlar"]["active_count"], 0)
        self.assertEqual(
            {n["id"]: n["badge"] for n in vm["nav"]}["alarmlar"], 0
        )
        markup = R.render_body(vm)
        self.assertIn("kayıt yok", markup)
        self.assertNotIn('class="ev"', markup)


# ---------------------------------------------------------------------------
# Adapter contract, profile validation, mock motion
# ---------------------------------------------------------------------------


def _session_adapter_calls():
    """(unguarded, guarded) adapter method names that app.Session calls.

    Read from app.py's source. A method behind `hasattr(a, ...)` is optional
    (app.py, Session.act). Every other call must succeed on any adapter.
    """
    with open(os.path.join(UI_DIR, "bettercnc", "app.py"), "r", encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Session"
    )
    aliases = set()
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "adapter"
        ):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))
    called, guarded = set(), set()
    for node in ast.walk(cls):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id == "hasattr" and len(node.args) == 2:
            if isinstance(node.args[1], ast.Constant):
                guarded.add(node.args[1].value)
        elif isinstance(f, ast.Attribute):
            owner = f.value
            if (isinstance(owner, ast.Name) and owner.id in aliases) or (
                isinstance(owner, ast.Attribute) and owner.attr == "adapter"
            ):
                called.add(f.attr)
    return called - guarded, guarded


class TestAdapterContract(unittest.TestCase):
    def test_every_method_the_session_relies_on_is_abstract(self):
        """The real adapter only runs on the machine. LinuxCncAdapter's own
        docstring says tests do not exercise it. So "it implements everything
        the UI calls" has to be enforced at class definition time, by
        MachineAdapter's abstract methods.

        If a method Session calls unconditionally were not abstract, an adapter
        that forgot it would either crash on the first click or, if the base
        gave it a body, silently do nothing. For software_estop, "silently do
        nothing" means an estop button that does nothing."""
        required, _ = _session_adapter_calls()
        # The walker still finds the calls (guards against a vacuous pass).
        self.assertTrue({"snapshot", "software_estop", "software_estop_reset"} <= required)
        abstract = set(MachineAdapter.__abstractmethods__)
        self.assertEqual(required - abstract, set(), "called but not abstract")
        for cls in (MockAdapter, LinuxCncAdapter):
            with self.subTest(adapter=cls.__name__):
                self.assertEqual(set(cls.__abstractmethods__), set())
        for missing in sorted(abstract):
            with self.subTest(missing=missing):
                body = {m: (lambda self, *a: None) for m in abstract if m != missing}
                partial = type("Partial", (MachineAdapter,), body)
                with self.assertRaises(TypeError):
                    partial()

    def test_joint_config_used_by_these_tests_is_the_mocks(self):
        """configmodel.joint_config_from_profile is what _map hands
        map_linuxcnc_state as "the adapter's" joint config, but the adapters
        build theirs separately (adapter._joint_config). If the two drift,
        the mapping tests check a configuration no adapter uses. Compared
        through the mock's homing state, for both profiles: the marble
        machine has no INI and takes the no-config branch."""
        for key in ("test-rig", "marble"):
            with self.subTest(profile=key):
                profile = load_profile(key)
                expected = [
                    (n, c["axis"], c["HOME_SEQUENCE"],
                     c["HOME_IGNORE_LIMITS"], c["HOME_SEARCH_VEL"])
                    for n, c in sorted(joint_config_from_profile(profile).items())
                ]
                got = [
                    (j.joint, j.axis, j.sequence, j.ignore_limits, j.search_vel)
                    for j in MockAdapter(profile).snapshot().homing.joints
                ]
                self.assertEqual(len(got), 3)  # premise: both profiles have joints
                self.assertEqual(got, expected)


class TestProfileValidation(unittest.TestCase):
    def test_undeclarable_capability_is_rejected(self):
        """Rendering iterates CAPABILITY_CATALOGUE, not the profile
        (the capabilities module docstring). So a key outside the catalogue would never be
        shown. The profile's author would believe they had declared something
        the operator never sees. validate_profile must refuse it, as it refuses
        a missing one."""
        profile = load_profile("test-rig")
        caps = dict(profile.capabilities)
        caps["coolant_flow_alarm"] = todo("unknown", "survey", "test_edge_cases.py")
        bogus = profile._replace(capabilities=caps)
        # Premise: the renderer really would drop it.
        self.assertNotIn(
            "coolant_flow_alarm", [p["key"] for p in VM.capability_panels(bogus)]
        )
        with self.assertRaises(ValueError) as ctx:
            validate_profile(bogus)
        self.assertIn("coolant_flow_alarm", str(ctx.exception))


class TestMockAdapterMotion(unittest.TestCase):
    """MockAdapter is what the UI renders from today, and its docstring says it
    "enforces the same rules the real machine does". These are its two motion
    paths no other test reaches with the machine enabled."""

    def test_start_with_nothing_loaded_does_not_claim_running(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()
        adapter.home_all()
        adapter.program_start()
        snap = adapter.snapshot()
        self.assertFalse(snap.program_running)
        self.assertEqual(snap.message, "Program yüklenmedi.")
        self.assertNotEqual(snap.status_label, "ÇALIŞIYOR")

    def test_jog_moves_only_the_named_axis_by_the_step(self):
        _, _, adapter = session("test-rig")
        adapter.software_estop_reset()  # jog is allowed unhomed (state.py can_jog)
        adapter.set_jog_step(10.0)
        adapter.jog("Y", -1)
        pos = {p.axis: p for p in adapter.snapshot().positions}
        self.assertAlmostEqual(pos["Y"].machine, -10.0)
        self.assertAlmostEqual(pos["Y"].work, -10.0)
        self.assertAlmostEqual(pos["X"].machine, 0.0)
        self.assertAlmostEqual(pos["Z"].machine, 0.0)
        adapter.jog("Y", 1)
        self.assertAlmostEqual(
            {p.axis: p for p in adapter.snapshot().positions}["Y"].machine, 0.0
        )


# ---------------------------------------------------------------------------
# Citations into production code are by symbol
# ---------------------------------------------------------------------------


class TestCitationsBySymbol(unittest.TestCase):
    """Round 2 found line citations in this file into configmodel, viewmodel
    and profiles that pointed at unrelated code after an edit. Production code
    is therefore cited by symbol in the config, state and view-model modules
    and their tests. file:line stays for LinuxCNC under reference/ and for
    docs/, which do not move when the code does."""

    FILES = tuple(
        os.path.join(UI_DIR, *parts)
        for parts in (
            ("bettercnc", "iniload.py"),
            ("bettercnc", "configmodel.py"),
            ("bettercnc", "state.py"),
            ("bettercnc", "profiles.py"),
            ("bettercnc", "viewmodel.py"),
            ("tests", "test_edge_cases.py"),
            ("tests", "test_capabilities.py"),
            ("tests", "test_unmeasured.py"),
            ("tests", "test_inputmap_and_link.py"),
            ("tests", "test_homing_gate.py"),
        )
    )

    def test_no_file_line_citation_into_production_code(self):
        pkg = os.path.join(UI_DIR, "bettercnc")
        names = {n for n in os.listdir(pkg) if n.endswith(".py")}
        self.assertIn("viewmodel.py", names)  # the listing found the package
        # Not preceded by a word character or a dot, so "myiniload.py" or a
        # dotted module path does not match.
        cite = re.compile(
            r"(?<![\w.])(?:{}):\d".format("|".join(re.escape(n) for n in sorted(names)))
        )
        # Probes built by concatenation, so this file does not match itself.
        for hit in ("see viewmodel.py" + ":354", "(profiles.py" + ":232-233)"):
            self.assertIsNotNone(cite.search(hit), hit)
        for miss in ("inifile.cc" + ":261", "viewmodel.program_screen", "myviewmodel.py" + ":1"):
            self.assertIsNone(cite.search(miss), miss)
        found = []
        for path in self.FILES:
            with open(path, "r", encoding="utf-8") as fh:
                for lineno, line in enumerate(fh, 1):
                    if cite.search(line):
                        found.append("{}:{}: {}".format(
                            os.path.basename(path), lineno, line.strip()))
        self.assertEqual(found, [])


# ---------------------------------------------------------------------------
# The LinuxCNC facts cited above, re-checked when reference/ is present
# ---------------------------------------------------------------------------


@unittest.skipUnless(HAVE_REFERENCE, "reference/linuxcnc (gitignored 2.9 clone) not present")
class TestLinuxCncCitations(unittest.TestCase):
    """Read-only checks against the LinuxCNC 2.9 source. They match content,
    not line numbers, so they survive upstream reformatting. They fail only if
    the fact itself changes."""

    @staticmethod
    def _src(rel: str) -> str:
        with open(os.path.join(LINUXCNC_SRC, rel), "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def test_missing_coordinates_defaults_to_xyz(self):
        text = self._src("src/emc/ini/initraj.cc")
        self.assertRegex(text, r"axismask\s*=\s*1\s*\|\s*2\s*\|\s*4;\s*//\s*default: XYZ machine")

    def test_inifile_joins_backslash_continued_lines(self):
        text = self._src("src/libnml/inifile/inifile.cc")
        self.assertIn("honor backslash", text)
        self.assertIn("line[newLinePos-1] == '\\\\'", text)

    def test_stat_estop_is_inverted_emc_enable_in_and_starts_set(self):
        self.assertRegex(
            self._src("src/emc/usr_intf/axis/extensions/emcmodule.cc"),
            r'"estop", T_INT, O\(io\.aux\.estop\)',
        )
        io = self._src("src/emc/iotask/ioControl.cc")
        self.assertRegex(io, r"emc_enable_in\)\s*==\s*0\)")
        self.assertIn("emcioStatus.aux.estop = 1; //estop=1 means", io)
        self.assertRegex(io, r"EMC_AUX_ESTOP_OFF_TYPE:[\s\S]{0,400}user_request_enable\) = 1")
        latch = self._src("src/hal/components/estop_latch.comp")
        self.assertIn("pin out bit ok_out = false;", latch)

    def test_inifile_matches_sections_by_prefix_and_ends_them_at_any_bracket(self):
        text = self._src("src/libnml/inifile/inifile.cc")
        self.assertIn('sprintf(bracketSection, "[%s]", section);', text)
        self.assertIn("strncmp(bracketSection, nonWhite, strlen(bracketSection))", text)
        self.assertIn("if (NULL != section && nonWhite[0] == '[') {", text)

    def test_inifile_returns_the_first_section_and_the_first_key_line(self):
        text = self._src("src/libnml/inifile/inifile.cc")
        self.assertRegex(text, r"it matches-- fp is now set up for search on tag \*/\s*break;")
        self.assertIn("if (--_num > 0) {", text)
        self.assertIn("int num = 1", self._src("src/libnml/inifile/inifile.hh"))

    def test_bool_is_integer_first_then_the_bool_map(self):
        ini = self._src("src/libnml/inifile/inifile.cc")
        self.assertIn('sscanf(pStr, "%i", &tmp) == 1', ini)
        self.assertIn("strcasecmp(pStr, pPair->pStr) == 0", ini)
        emc = self._src("src/emc/ini/emcIniFile.cc")
        for pair in ('{"TRUE", 1}', '{"YES", 1}', '{"1", 1}',
                     '{"FALSE", 0}', '{"NO", 0}', '{"0", 0}'):
            with self.subTest(pair=pair):
                self.assertIn(pair, emc)
        joint = self._src("src/emc/ini/inijoint.cc")
        self.assertIn("jointIniFile->EnableExceptions(EmcIniFile::ERR_CONVERSION);", joint)
        self.assertIn('Find(&ignore_limits, "HOME_IGNORE_LIMITS", jointString)', joint)

    def test_unhomed_jog_limits_are_relative_to_the_current_position(self):
        text = self._src("src/emc/motion/command.c")
        self.assertRegex(
            text,
            r"if \(get_homed\(joint_num\) \) \{[\s\S]{0,200}"
            r"joint->min_jog_limit = joint->min_pos_limit;[\s\S]{0,200}"
            r"joint->max_jog_limit = joint->pos_fb \+ range;",
        )

    def test_interp_reading_value(self):
        self.assertRegex(
            self._src("src/emc/nml_intf/emc.hh"), r"EMC_TASK_INTERP_READING\s*=\s*2\b"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
