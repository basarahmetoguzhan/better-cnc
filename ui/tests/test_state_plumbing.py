"""LinuxCNC's motion, interpreter and limit-switch status, carried into
MachineState - and the work DRO computed as AXIS computes it (plan WP02).

What later work builds on is read here: the hard-limit flags and the limit
override, whether the machine moves, the task / interpreter / motion modes,
a paused program, a running MDI command, the program line, the commanded
position and whether a G92 offset is in effect. Later plans act on them -
the limit recovery (WP05), the jog gates (WP06) - but the stat fields this
package added change none of the old derived flags;
test_existing_semantics_unchanged pins that.

Three layers:

  - the pure parts (adapter.work_coordinates, adapter.map_linuxcnc_state)
    on hand-built stat objects;
  - LinuxCncAdapter on the fake (fake_linuxcnc.FakeMachine's passive status
    fields), including the status-unknown snapshot;
  - the view model's `motion` section, which GET /api/state carries, and
    MockAdapter, which reports only what its own model defines (plan WP06).

LinuxCNC citations are relative to reference/linuxcnc/; the facts they rest
on are re-checked against that source at the end when it is present.
"""

from __future__ import annotations

import json
import math
import os
import re
import types
import unittest
from typing import Dict, Optional

import helpers  # noqa: F401  (puts ui/ on sys.path)
from helpers import ALL_SCREENS, REPO_ROOT

import fake_linuxcnc as F
from bettercnc import adapter as A
from bettercnc import render as R
from bettercnc import viewmodel as VM
from bettercnc.adapter import LinuxCncAdapter, MockAdapter, map_linuxcnc_state, work_coordinates
from bettercnc.app import Session
from bettercnc.configmodel import joint_config_from_profile, load_machine_config
from bettercnc.profiles import load_profile
from bettercnc.state import HardLimit, LastError, LinkHealth, MachineState

C = F.CONSTANTS
LINK_PINS = {
    LinkHealth.HAL_PACKET_ERROR_TOTAL: 0,
    LinkHealth.HAL_WATCHDOG_HAS_BIT: False,
    LinkHealth.HAL_IO_ERROR: False,
}
LINUXCNC = os.path.join(REPO_ROOT, "reference", "linuxcnc")
NEEDS_REFERENCE = unittest.skipUnless(
    os.path.isdir(os.path.join(LINUXCNC, "src")), "reference/linuxcnc not cloned (it is gitignored)"
)

#: The fields this package added, at their "not read" values.
NOT_READ = {
    "hard_limits": (),
    "override_limits": None,
    "moving": None,
    "task_mode": None,
    "interp_state": None,
    "motion_mode": None,
    "program_paused": None,
    "mdi_running": None,
    "program_line": None,
    "g92_active": None,
}

#: Fields with a "not read" value that come from HAL, not from LinuxCNC's
#: status: they keep their meaning while the status is unknown.
FROM_HAL = {"input_values", "stepgen_timing"}

#: Fields with a None default that are the adapter's own words, not
#: readings (plan WP04): the error box stays while the status is unknown -
#: the outage is one of the errors it shows.
UI_OWN = {"last_error", "run_command"}


def glcanon_relative(actual, tool_offset, g5x_offset, rotation_xy, g92_offset):
    """AXIS's relative DRO, transcribed line for line from posstrs
    (lib/python/rs274/glcanon.py:1586-1596), as an independent check of
    adapter.work_coordinates."""
    positions = list(actual)
    positions = [(i - j) for i, j in zip(positions, tool_offset)]
    positions = [(i - j) for i, j in zip(positions, g5x_offset)]
    t = -rotation_xy
    t = math.radians(t)
    x = positions[0]
    y = positions[1]
    positions[0] = x * math.cos(t) - y * math.sin(t)
    positions[1] = x * math.sin(t) + y * math.cos(t)
    positions = [(i - j) for i, j in zip(positions, g92_offset)]
    return positions


def stat(**over) -> types.SimpleNamespace:
    """A whole stat, as the binding has it after a poll: ON, MANUAL, teleop,
    homed, idle, still, no offsets. Nine-component poses, 16 joint dicts."""
    joint = [dict.fromkeys(F.JOINT_DICT_KEYS, 0) for _ in range(C["MAX_JOINTS"])]
    for j in range(3):
        joint[j]["homed"] = 1
    base = dict(
        task_state=C["STATE_ON"], task_mode=C["MODE_MANUAL"], motion_mode=C["TRAJ_MODE_TELEOP"],
        interp_state=C["INTERP_IDLE"], estop=0, enabled=True, inpos=True,
        joint=joint, actual_position=(0.0,) * 9, position=(0.0,) * 9,
        g5x_offset=(0.0,) * 9, g5x_index=1, g92_offset=(0.0,) * 9, tool_offset=(0.0,) * 9,
        rotation_xy=0.0, feedrate=1.0, file="", paused=False, current_vel=0.0,
        motion_line=0, current_line=0,
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def pose(x=0.0, y=0.0, z=0.0, *rest):
    """A nine-component pose (pose(), emcmodule.cc:444-456)."""
    return (x, y, z) + tuple(rest) + (0.0,) * (6 - len(rest))


def mapped(st, hal: Optional[Dict] = None) -> MachineState:
    profile = load_profile("test-rig")
    return map_linuxcnc_state(
        stat=st, hal_values=hal or {"connected": True}, profile=profile,
        joint_axis_map=["X", "Y", "Z"], joint_config=joint_config_from_profile(profile),
    )


# ---------------------------------------------------------------------------
# The work DRO
# ---------------------------------------------------------------------------


class TestWorkCoordinates(unittest.TestCase):

    def test_spec_case_g92_tool_offset_and_a_quarter_turn(self):
        """g92 (1, 2, 3), tool Z 10, rotation 90: by hand,
        p = actual - tool - g5x = (90, 45, -28); turned by -90 degrees
        (x, y) -> (y, -x) = (45, -90, -28); minus g92 = (44, -92, -31)."""
        actual = pose(100.0, 50.0, -20.0)
        tool, g5x, g92 = pose(0.0, 0.0, 10.0), pose(10.0, 5.0, -2.0), pose(1.0, 2.0, 3.0)
        got = work_coordinates(actual[:3], tool, g5x, 90.0, g92)
        for value, want in zip(got, (44.0, -92.0, -31.0)):
            self.assertAlmostEqual(value, want, places=9)
        for value, want in zip(got, glcanon_relative(actual, tool, g5x, 90.0, g92)):
            self.assertAlmostEqual(value, want, places=12)

    def test_matches_axis_for_any_angle_and_every_component(self):
        actual = pose(12.5, -7.25, -30.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
        tool = pose(0.1, 0.2, 12.7, 0.0, 0.0, 0.0, 0.0, 0.0, 0.5)
        g5x = pose(3.0, -4.0, -1.5, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0)
        g92 = pose(-2.0, 1.0, 0.25, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)
        for angle in (0.0, 30.0, -45.0, 90.0, 180.0, 271.5):
            with self.subTest(rotation_xy=angle):
                got = work_coordinates(actual, tool, g5x, angle, g92)
                self.assertEqual(len(got), 9)
                for value, want in zip(got, glcanon_relative(actual, tool, g5x, angle, g92)):
                    self.assertAlmostEqual(value, want, places=12)

    def test_g92_is_taken_off_after_the_rotation(self):
        """The order matters: G92 lives in the rotated frame, so it is
        subtracted after turning, not before (the interpreter applies it
        first when placing a point)."""
        got = work_coordinates((10.0, 0.0, 0.0), rotation_xy=90.0, g92_offset=(1.0, 0.0, 0.0))
        self.assertAlmostEqual(got[0], -1.0, places=9)    # (10, 0) turned -90 -> (0, -10)
        self.assertAlmostEqual(got[1], -10.0, places=9)
        wrong_order = (0.0, -9.0)                         # turning (10 - 1, 0) instead
        self.assertNotAlmostEqual(got[1], wrong_order[1], places=3)

    def test_without_offsets_it_is_the_machine_position_exactly(self):
        for machine in ((0.0, 0.0, 0.0), (5.0, 5.0, -5.0), (-1.0, 387.0, -96.5)):
            with self.subTest(machine=machine):
                self.assertEqual(work_coordinates(machine, pose(), pose(), 0.0, pose()), machine)
                self.assertEqual(work_coordinates(machine), machine)

    def test_g5x_alone_is_the_old_rule(self):
        """What the UI showed before: machine minus the G5x origin."""
        self.assertEqual(work_coordinates((10.0, 20.0, -5.0), g5x_offset=(1.0, 2.0, 3.0)),
                         (9.0, 18.0, -8.0))

    def test_short_offsets_count_as_zero(self):
        self.assertEqual(work_coordinates((10.0, 20.0, -5.0), tool_offset=(1.0,)),
                         (9.0, 20.0, -5.0))
        self.assertEqual(work_coordinates((10.0,), rotation_xy=90.0, g92_offset=(1.0, 2.0)), (9.0,))


# ---------------------------------------------------------------------------
# map_linuxcnc_state: the new fields
# ---------------------------------------------------------------------------


class TestMappedFields(unittest.TestCase):

    def test_work_dro_follows_g92_tool_offset_and_rotation(self):
        st = stat(actual_position=pose(100.0, 50.0, -20.0), g5x_offset=pose(10.0, 5.0, -2.0),
                  tool_offset=pose(0.0, 0.0, 10.0), rotation_xy=90.0,
                  g92_offset=pose(1.0, 2.0, 3.0))
        state = mapped(st)
        want = glcanon_relative(st.actual_position, st.tool_offset, st.g5x_offset,
                                st.rotation_xy, st.g92_offset)
        self.assertEqual([p.axis for p in state.positions], ["X", "Y", "Z"])
        for p, w in zip(state.positions, want):
            self.assertAlmostEqual(p.work, w, places=12)
        self.assertEqual([p.machine for p in state.positions], [100.0, 50.0, -20.0])
        self.assertTrue(state.g92_active)

    def test_commanded_is_stat_position_not_actual(self):
        """stat.position is motion's carte_pos_cmd (taskintf.cc:1600): in
        sim/ it led actual_position by 0.009 mm through a 3 mm/s teleop jog."""
        state = mapped(stat(actual_position=pose(5.0, 6.0, -7.0),
                            position=pose(5.009, 6.0, -7.0)))
        self.assertEqual([p.commanded for p in state.positions], [5.009, 6.0, -7.0])
        self.assertEqual([p.machine for p in state.positions], [5.0, 6.0, -7.0])

    def test_hard_limits_and_override_pass_through(self):
        st = stat()
        st.joint[0].update(min_hard_limit=1)
        st.joint[2].update(max_hard_limit=1, override_limits=1)
        st.joint[0].update(override_limits=1)
        state = mapped(st)
        self.assertEqual(state.hard_limits, (
            HardLimit(0, "X", True, False),
            HardLimit(1, "Y", False, False),
            HardLimit(2, "Z", False, True),
        ))
        self.assertIs(state.override_limits, True)
        st.joint[0].update(override_limits=0)
        self.assertIs(mapped(st).override_limits, False)

    def test_override_is_read_from_joint_0(self):
        """One value for every joint (taskintf.cc:963); joint 0 is read."""
        st = stat()
        st.joint[1].update(override_limits=1)   # inconsistent on purpose
        self.assertIs(mapped(st).override_limits, False)

    def test_hard_limits_read_with_the_machine_off(self):
        """Motion reads the switches whether or not it is enabled
        (control.c:491-501): the flags are readings in ESTOP too."""
        st = stat(task_state=C["STATE_ESTOP_RESET"])
        st.joint[0].update(min_hard_limit=1)
        state = mapped(st)
        self.assertTrue(state.estop.software_disabled)
        self.assertIs(state.hard_limits[0].min, True)

    def test_moving(self):
        on, off = C["STATE_ON"], C["STATE_ESTOP_RESET"]
        free, teleop, coord = (C["TRAJ_MODE_FREE"], C["TRAJ_MODE_TELEOP"],
                               C["TRAJ_MODE_COORD"])
        cases = (
            # task, motion, current_vel, inpos -> moving
            (on, teleop, 3.0, True, True),       # teleop jog: INPOS stays True
            (on, coord, 10.0, False, True),      # an MDI move
            (on, free, 2.5, False, True),        # a joint jog
            (on, free, 0.0, False, True),        # a joint jog at its first instant
            (on, teleop, 0.0, False, False),     # teleop: INPOS is no evidence
            (on, teleop, 0.0, True, False),
            (on, free, 0.0, True, False),
            (on, teleop, 1e-9, True, False),     # below MOVING_VEL_EPS
            (off, teleop, 3.0, True, False),     # stale velocity after a trip (sim/)
            (off, free, 0.0, False, False),      # stale INPOS while disabled
            (C["STATE_ESTOP"], coord, 5.0, False, False),
        )
        for task, motion, vel, inpos, want in cases:
            with self.subTest(task=task, motion=motion, vel=vel, inpos=inpos):
                state = mapped(stat(task_state=task, motion_mode=motion, current_vel=vel,
                                    inpos=inpos))
                self.assertIs(state.moving, want)

    def test_moving_unknown_when_what_it_needs_is_missing(self):
        st = stat()
        del st.current_vel
        self.assertIsNone(mapped(st).moving)
        st = stat(motion_mode=C["TRAJ_MODE_FREE"])
        del st.inpos
        self.assertIsNone(mapped(st).moving)
        # ...but a velocity alone is enough to say it moves, and off is off.
        st = stat(current_vel=3.0)
        del st.motion_mode
        self.assertIs(mapped(st).moving, True)
        st = stat(task_state=C["STATE_ESTOP"])
        del st.current_vel
        self.assertIs(mapped(st).moving, False)

    def test_modes_by_name(self):
        for value, name in ((1, "manual"), (2, "auto"), (3, "mdi"), (0, None), (7, None)):
            with self.subTest(task_mode=value):
                self.assertEqual(mapped(stat(task_mode=value)).task_mode, name)
        for value, name in ((1, "idle"), (2, "reading"), (3, "paused"), (4, "waiting"), (0, None)):
            with self.subTest(interp_state=value):
                self.assertEqual(mapped(stat(interp_state=value)).interp_state, name)
        for value, name in ((1, "free"), (2, "coord"), (3, "teleop"), (0, None)):
            with self.subTest(motion_mode=value):
                self.assertEqual(mapped(stat(motion_mode=value)).motion_mode, name)

    def test_program_paused(self):
        auto, reading, paused = C["MODE_AUTO"], C["INTERP_READING"], C["INTERP_PAUSED"]
        cases = (
            (auto, paused, True, True),      # PLAN_PAUSE (emctaskmain.cc:2312-2319)
            (auto, paused, False, True),     # interpreter PAUSED alone
            (auto, reading, True, True),     # motion paused alone (single step)
            (auto, reading, False, False),
            (C["MODE_MDI"], paused, True, True),
            (C["MODE_MANUAL"], C["INTERP_IDLE"], False, False),
        )
        for mode, interp, flag, want in cases:
            with self.subTest(mode=mode, interp=interp, paused=flag):
                state = mapped(stat(task_mode=mode, interp_state=interp, paused=flag))
                self.assertIs(state.program_paused, want)

    def test_mdi_running(self):
        cases = (
            (C["MODE_MDI"], C["INTERP_READING"], True),
            (C["MODE_MDI"], C["INTERP_WAITING"], True),
            (C["MODE_MDI"], C["INTERP_PAUSED"], True),
            (C["MODE_MDI"], C["INTERP_IDLE"], False),
            (C["MODE_AUTO"], C["INTERP_READING"], False),
            (C["MODE_MANUAL"], C["INTERP_IDLE"], False),
            (C["MODE_MDI"], 0, None),        # an interpreter state LinuxCNC does not define
            (0, C["INTERP_READING"], None),
        )
        for mode, interp, want in cases:
            with self.subTest(mode=mode, interp=interp):
                self.assertIs(mapped(stat(task_mode=mode, interp_state=interp)).mdi_running, want)

    def test_program_line_is_motion_line_not_current_line(self):
        """In sim/ motion_line counted 8 to 13 through a program while
        current_line, read ahead, stayed at 13."""
        state = mapped(stat(motion_line=8, current_line=13))
        self.assertEqual(state.program_line, 8)

    def test_g92_active(self):
        cases = (
            (pose(), False),
            (pose(1e-12, -1e-12, 0.0), False),      # below G92_ACTIVE_EPS
            (pose(0.0, 0.0, 0.5), True),
            (pose(-10.0), True),
            (pose(0.0, 0.0, 0.0, 15.0), True),      # an A offset counts too
        )
        for g92, want in cases:
            with self.subTest(g92=g92):
                self.assertIs(mapped(stat(g92_offset=g92)).g92_active, want)

    def test_a_partial_stat_reads_as_not_read(self):
        """No attribute: every new field at its "not read" value, the
        positions at the old placeholders (machine and work 0)."""
        state = mapped(types.SimpleNamespace())
        for name, value in NOT_READ.items():
            if name == "hard_limits":
                continue
            with self.subTest(field=name):
                self.assertIs(getattr(state, name), value)
        self.assertEqual(state.hard_limits, (
            HardLimit(0, "X", None, None), HardLimit(1, "Y", None, None),
            HardLimit(2, "Z", None, None),
        ))
        self.assertEqual([(p.work, p.machine, p.commanded) for p in state.positions],
                         [(0.0, 0.0, None)] * 3)

    def test_existing_semantics_unchanged(self):
        """program_running, status_label, can_jog and the start gate read
        the same with or without the new stat fields: this package only
        adds readings."""
        mdi = dict(task_mode=C["MODE_MDI"], interp_state=C["INTERP_READING"],
                   motion_mode=C["TRAJ_MODE_COORD"], current_vel=10.0, inpos=False)
        cases = (
            {},
            dict(file="/x.ngc", task_mode=C["MODE_AUTO"], interp_state=C["INTERP_PAUSED"],
                 paused=True),
            dict(mdi, file="/x.ngc"),
            dict(task_state=C["STATE_ESTOP_RESET"], current_vel=3.0),
        )
        new = ("position", "current_vel", "motion_line", "current_line", "g92_offset",
               "tool_offset", "rotation_xy")
        for over in cases:
            with self.subTest(case=over):
                full = stat(**over)
                old = types.SimpleNamespace(**{k: v for k, v in vars(full).items()
                                               if k not in new})
                a, b = mapped(full), mapped(old)
                for attr in ("program_running", "status_label", "can_jog", "start_blocks",
                             "can_start_program", "status_severity"):
                    self.assertEqual(getattr(a, attr), getattr(b, attr), attr)


class TestModeNamesMirrorTheBinding(unittest.TestCase):
    """The adapter mirrors LinuxCNC's enum values so it imports without the
    linuxcnc module; the fake's CONSTANTS are checked against the binding
    (test_linuxcnc_adapter.TestFakeFidelity), so tying the two is enough."""

    def test_task_states(self):
        for name in ("ESTOP", "ESTOP_RESET", "OFF", "ON"):
            with self.subTest(state=name):
                self.assertEqual(getattr(A, "TASK_STATE_" + name), C["STATE_" + name])

    def test_task_modes_interp_states_and_motion_modes(self):
        for table, prefix in ((A.TASK_MODES, "MODE_"), (A.INTERP_STATES, "INTERP_"),
                              (A.MOTION_MODES, "TRAJ_MODE_")):
            names = {k[len(prefix):].lower(): v for k, v in C.items() if k.startswith(prefix)}
            self.assertEqual({v: k for k, v in table.items()}, names, prefix)


# ---------------------------------------------------------------------------
# LinuxCncAdapter on the fake
# ---------------------------------------------------------------------------


class FakeAdapterCase(unittest.TestCase):
    """A LinuxCncAdapter on fresh fakes, removed after the test."""

    def make(self, machine: Optional[F.FakeMachine] = None) -> LinuxCncAdapter:
        cm = F.installed(machine=machine, hal_state=F.FakeHal(dict(LINK_PINS)))
        _, _, self.machine, self.hal = cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)
        self.profile = load_profile("test-rig")
        return LinuxCncAdapter(self.profile, halcmd=F.FakeHalcmd(self.hal),
                               proc_root="/nonexistent/proc")


class TestAdapterPlumbing(FakeAdapterCase):

    def test_spec_case_through_the_adapter(self):
        """The fake's G92 (1, 2, 3), tool Z 10 and rotation 90 reach the
        DRO as AXIS would show them."""
        adapter = self.make()
        m = self.machine
        m.power_on(homed=True)
        m.axis_pos[:3] = [100.0, 50.0, -20.0]
        m.g5x_offset[:3] = [10.0, 5.0, -2.0]
        m.g92_offset[:3] = [1.0, 2.0, 3.0]
        m.tool_offset[2] = 10.0
        m.rotation_xy = 90.0
        state = adapter.snapshot()
        for p, want in zip(state.positions, (44.0, -92.0, -31.0)):
            self.assertAlmostEqual(p.work, want, places=9)
        self.assertEqual([p.machine for p in state.positions], [100.0, 50.0, -20.0])
        self.assertTrue(state.g92_active)
        kontrol = VM.control_screen(self.profile, load_machine_config(self.profile), state)
        self.assertEqual([(a["work"], a["machine"]) for a in kontrol["axes"]], [
            ("+44.000", "+100.000"), ("-92.000", "+50.000"), ("-31.000", "-20.000")])

    def test_hard_limits_and_override_from_the_fake_flags(self):
        adapter = self.make()          # ESTOP: the flags are read anyway
        m = self.machine
        state = adapter.snapshot()
        self.assertEqual(state.hard_limits, tuple(
            HardLimit(j, a, False, False) for j, a in enumerate("XYZ")))
        self.assertIs(state.override_limits, False)
        m.hard_min[0] = 1
        m.hard_max[2] = 1
        m.override_mask = 0b10_0000      # joint 2's positive switch (command.c:116-117)
        state = adapter.snapshot()
        self.assertEqual(state.hard_limits, (
            HardLimit(0, "X", True, False), HardLimit(1, "Y", False, False),
            HardLimit(2, "Z", False, True)))
        self.assertIs(state.override_limits, True)
        self.assertTrue(state.estop.software_disabled)

    def test_moving_on_the_fake(self):
        adapter = self.make()
        m = self.machine
        m.current_vel = 3.0                  # a stale teleop velocity, machine off
        self.assertIs(adapter.snapshot().moving, False)
        m.power_on(homed=True)               # teleop
        self.assertIs(adapter.snapshot().moving, True)
        m.current_vel = 0.0
        self.assertIs(adapter.snapshot().moving, False)
        m.jogging_active = True              # teleop jog: stat.inpos stays True
        self.assertIs(adapter.snapshot().moving, False)
        m.jogging_active = False
        m.power_on(homed=False)              # free (joint) mode
        m.homed[:3] = [0, 0, 0]
        m.jogging_active = True              # a joint jog clears stat.inpos
        state = adapter.snapshot()
        self.assertEqual(state.motion_mode, "free")
        self.assertIs(state.moving, True)
        m.jogging_active = False
        self.assertIs(adapter.snapshot().moving, False)

    def test_modes_paused_and_mdi_on_the_fake(self):
        adapter = self.make()
        m = self.machine
        m.power_on(homed=True)
        state = adapter.snapshot()
        self.assertEqual((state.task_mode, state.motion_mode, state.interp_state),
                         ("manual", "teleop", "idle"))
        self.assertEqual((state.program_paused, state.mdi_running), (False, False))
        m.task_mode, m.motion_mode, m.interp_state = (
            C["MODE_MDI"], C["TRAJ_MODE_COORD"], C["INTERP_READING"])
        state = adapter.snapshot()
        self.assertEqual((state.task_mode, state.motion_mode, state.interp_state),
                         ("mdi", "coord", "reading"))
        self.assertIs(state.mdi_running, True)
        m.task_mode, m.interp_state, m.paused = C["MODE_AUTO"], C["INTERP_PAUSED"], True
        state = adapter.snapshot()
        self.assertIs(state.program_paused, True)
        self.assertIs(state.mdi_running, False)

    def test_program_line_and_commanded_on_the_fake(self):
        adapter = self.make()
        m = self.machine
        m.motion_line, m.current_line = 8, 13
        m.axis_pos[0] = 5.0
        state = adapter.snapshot()
        self.assertEqual(state.program_line, 8)
        self.assertEqual(state.positions[0].commanded, 5.0)     # at rest: the same
        m.commanded_pos = [5.009] + [0.0] * 8
        self.assertEqual(adapter.snapshot().positions[0].commanded, 5.009)

    def test_status_unknown_empties_the_new_fields(self):
        """_status_unreadable: no reading survives as a placeholder."""
        adapter = self.make()
        m = self.machine
        m.power_on(homed=True)
        m.g92_offset[0] = 4.0
        m.hard_min[0] = 1
        m.current_vel = 2.0
        self.assertTrue(adapter.snapshot().g92_active)       # premise: read while known
        m.channels_valid = False
        state = adapter.snapshot()
        self.assertFalse(state.status_known)
        self.assertEqual(state.positions, ())
        for name, value in NOT_READ.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(state, name), value)
        vm = VM.build(self.profile, load_machine_config(self.profile), state)
        self.assertEqual([(a["work"], a["machine"]) for a in vm["kontrol"]["axes"]],
                         [("—", "—")] * 3)
        motion = vm["motion"]
        self.assertEqual(motion.pop("hard_limits"), [])
        self.assertEqual(motion.pop("commanded"), {})
        self.assertEqual(set(motion.values()), {None})


class TestStatusUnknownList(unittest.TestCase):
    """MachineState.as_status_unknown lists the fields it empties by hand;
    a field added later with a "not read" default must be in it, or be one
    HAL fills."""

    def test_every_status_field_with_a_not_read_default_is_emptied(self):
        state = MockAdapter(load_profile("test-rig")).snapshot()._replace(
            moving=True, task_mode="auto", interp_state="reading", motion_mode="coord",
            program_paused=False, mdi_running=False, program_line=12, g92_active=True,
            override_limits=True, hard_limits=(HardLimit(0, "X", True, False),),
            input_values=(("pin", True),), stepgen_timing=(("name", 1000),),
            last_error=LastError(1, "12:00:00", "kutu"), run_command="step",
        )
        unknown = state.as_status_unknown()
        self.assertFalse(unknown.status_known)
        self.assertEqual(unknown.positions, ())
        defaults = MachineState._field_defaults
        not_read = {n for n, d in defaults.items() if d is None or d == ()} - FROM_HAL - UI_OWN
        self.assertEqual(not_read, set(NOT_READ))
        for name in not_read:
            with self.subTest(field=name):
                self.assertEqual(getattr(unknown, name), defaults[name])
        for name in FROM_HAL | UI_OWN:
            with self.subTest(kept=name):
                self.assertEqual(getattr(unknown, name), getattr(state, name))


# ---------------------------------------------------------------------------
# The view model's motion section, MockAdapter
# ---------------------------------------------------------------------------


class _FixedSnapshot(object):
    def __init__(self, state: MachineState):
        self.state = state

    def snapshot(self) -> MachineState:
        return self.state


class TestMotionSection(unittest.TestCase):

    def setUp(self):
        self.profile = load_profile("test-rig")
        self.config = load_machine_config(self.profile)
        st = stat(task_mode=C["MODE_MDI"], interp_state=C["INTERP_READING"],
                  motion_mode=C["TRAJ_MODE_COORD"], current_vel=10.0, motion_line=0,
                  g92_offset=pose(10.0), actual_position=pose(20.0, 5.0, -5.0),
                  position=pose(20.01, 5.0, -5.0))
        st.joint[1].update(max_hard_limit=1)
        self.state = mapped(st)

    def view(self, state: MachineState) -> Dict:
        """What GET /api/state answers: Session.view, as JSON."""
        session = Session(_FixedSnapshot(state), self.profile, self.config)
        return json.loads(json.dumps(session.view("kontrol"), ensure_ascii=False))

    def test_api_state_carries_the_readings(self):
        vm = self.view(self.state)
        self.assertEqual(vm["motion"], {
            "task_mode": "mdi", "motion_mode": "coord", "interp_state": "reading",
            "moving": True, "program_paused": False, "mdi_running": True, "program_line": 0,
            "g92_active": True, "override_limits": False,
            "hard_limits": [
                {"joint": 0, "axis": "X", "min": False, "max": False},
                {"joint": 1, "axis": "Y", "min": False, "max": True},
                {"joint": 2, "axis": "Z", "min": False, "max": False},
            ],
            "commanded": {"X": 20.01, "Y": 5.0, "Z": -5.0},
        })
        self.assertEqual(vm["kontrol"]["axes"][0]["work"], "+10.000")     # 20 - G92 10
        self.assertEqual(vm["kontrol"]["axes"][0]["machine"], "+20.000")

    def test_a_state_marked_unknown_shows_no_reading(self):
        """Even one that still carries values (built by _replace, not by the
        adapter): the section reads the status flag itself."""
        vm = self.view(self.state._replace(status_known=False))
        motion = vm["motion"]
        self.assertEqual(motion.pop("hard_limits"), [])
        self.assertEqual(motion.pop("commanded"), {})
        self.assertEqual(set(motion.values()), {None})

    #: What MockAdapter's own model defines (plan WP06; updated from WP02's
    #: "the mock reads none of it"): the jog gates take "not read" as a
    #: refusal, so the mock reports these as LinuxCncAdapter does.
    MOCK_MODELLED = {"moving": False, "task_mode": "manual", "interp_state": "idle",
                     "motion_mode": "free", "program_paused": False, "mdi_running": False}

    def test_mock_reads_only_what_its_model_defines(self):
        """MockAdapter reports the fields its model defines - nothing moving,
        paused or running an MDI line, MANUAL in free mode before homing and
        in teleop after (emctask.cc:276-280), AUTO and coordinated while a
        program "runs" - and "not read" for the rest: the switch flags, the
        override, the program line, G92, the commanded position. Every
        screen still renders."""
        adapter = MockAdapter(self.profile)
        state = adapter.snapshot()
        for name, value in NOT_READ.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(state, name), self.MOCK_MODELLED.get(name, value))
        self.assertEqual({p.commanded for p in state.positions}, {None})
        motion = self.view(state)["motion"]
        self.assertEqual(motion.pop("hard_limits"), [])
        self.assertEqual(motion.pop("commanded"), {"X": None, "Y": None, "Z": None})
        self.assertEqual(motion, dict(self.MOCK_MODELLED, program_line=None, g92_active=None,
                                      override_limits=None))
        adapter.software_estop_reset()
        adapter.home_all()
        self.assertEqual(adapter.snapshot().motion_mode, "teleop")
        adapter.jog("X", 1)
        state = adapter.snapshot()
        for screen in ALL_SCREENS:
            with self.subTest(screen=screen):
                self.assertTrue(R.render_body(VM.build(self.profile, self.config, state, screen)))
        self.assertEqual(state.positions[0].commanded, None)
        adapter.load_program("deneme.ngc")
        adapter.program_start()
        running = adapter.snapshot()
        self.assertEqual((running.task_mode, running.interp_state, running.motion_mode),
                         ("auto", "reading", "coord"))


# ---------------------------------------------------------------------------
# The LinuxCNC facts above, re-checked against reference/
# ---------------------------------------------------------------------------


def _src(rel: str) -> str:
    with open(os.path.join(LINUXCNC, rel), "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


@NEEDS_REFERENCE
class TestLinuxCncFacts(unittest.TestCase):
    """Content, not line numbers: they fail only if the fact changes."""

    def test_axis_relative_dro_formula(self):
        text = _flat(_src("lib/python/rs274/glcanon.py"))
        start = text.index("def posstrs(self):")
        body = text[start:start + 3000]
        steps = [
            "positions = [(i-j) for i, j in zip(positions, s.tool_offset)]",
            "positions = [(i-j) for i, j in zip(positions, s.g5x_offset)]",
            "t = -s.rotation_xy",
            "positions[0] = x * math.cos(t) - y * math.sin(t)",
            "positions[1] = x * math.sin(t) + y * math.cos(t)",
            "positions = [(i-j) for i, j in zip(positions, s.g92_offset)]",
        ]
        at = [body.index(step) for step in steps]
        self.assertEqual(at, sorted(at))

    def test_limit_flags_and_override_reach_stat(self):
        text = _flat(_src("src/emc/task/taskintf.cc"))
        self.assertIn("stat[joint_num].minHardLimit = (joint->flag & "
                      "EMCMOT_JOINT_MIN_HARD_LIMIT_BIT ? 1 : 0);", text)
        self.assertIn("stat[joint_num].maxHardLimit = (joint->flag & "
                      "EMCMOT_JOINT_MAX_HARD_LIMIT_BIT ? 1 : 0);", text)
        self.assertIn("stat[joint_num].overrideLimits = !!(emcmotStatus.overrideLimitMask);",
                      text)
        self.assertIn("stat->position = emcmotStatus.carte_pos_cmd;", text)
        self.assertIn("stat->current_vel = emcmotStatus.current_vel;", text)

    def test_motion_reads_the_switches_in_process_inputs(self):
        """In process_inputs' per-joint loop, which runs every servo cycle
        and skips only inactive joints - no machine or joint enable gates
        it. What it reads is no fresher than the board's GPIO pins: hm2
        returns before refreshing them while the link is down (HardLimit)."""
        text = _src("src/emc/motion/control.c")
        start = text.index("static void process_inputs(void)\n{")
        end = text.index("\n}\n", start)
        loop = text[start:end].split("/* read and process per-joint inputs */")[1]
        self.assertIn("/* read limit switches */", loop)
        self.assertIn("SET_JOINT_NHL_FLAG(joint, 1);", loop)
        self.assertIn("if (!GET_JOINT_ACTIVE_FLAG(joint)) {", loop)
        for gate in ("GET_MOTION_ENABLE_FLAG", "GET_JOINT_ENABLE_FLAG"):
            self.assertNotIn(gate, loop)
        hm2 = _src("src/hal/drivers/mesa-hostmot2/hostmot2.c")
        read = hm2[hm2.index("static void hm2_read(void *void_hm2, long period) {"):]
        read = read[:read.index("\n}\n")]
        self.assertLess(read.index("if ((*hm2->llio->io_error) != 0) return;"),
                        read.index("hm2_ioport_gpio_process_tram_read(hm2);"))

    def test_current_vel_is_computed_in_every_mode(self):
        text = _flat(_src("src/emc/motion/control.c"))
        self.assertIn("if(GET_MOTION_COORD_FLAG()) { *(emcmot_hal_data->current_vel) = "
                      "emcmotStatus->current_vel;", text)
        self.assertIn("} else if (GET_MOTION_TELEOP_FLAG()) { emcmotStatus->current_vel = "
                      "(*emcmot_hal_data->current_vel) = axis_get_compound_velocity();", text)
        self.assertIn("emcmotStatus->current_vel = (*emcmot_hal_data->current_vel) = sqrt(v2);",
                      text)

    def test_motion_line_is_motions_id(self):
        text = _flat(_src("src/emc/task/emctask.cc"))
        self.assertIn("if (emcStatus->motion.traj.id > 0) { stat->motionLine = "
                      "emcStatus->motion.traj.id; }", text)

    def test_plan_pause_pauses_motion_and_the_interpreter(self):
        text = _flat(_src("src/emc/task/emctaskmain.cc"))
        start = text.index("case EMC_TASK_PLAN_PAUSE_TYPE: emcTrajPause();")
        body = text[start:start + 400]
        self.assertIn("emcStatus->task.interpState = EMC_TASK_INTERP_PAUSED;", body)

    def test_g92_offset_is_what_canon_last_sent(self):
        text = _flat(_src("src/emc/task/emctaskmain.cc"))
        self.assertIn("case EMC_TRAJ_SET_G92_TYPE: // struct-copy program origin "
                      "emcStatus->task.g92_offset = ((EMC_TRAJ_SET_G92 *) cmd)->origin;", text)

    def test_active_g_codes_length(self):
        self.assertIn("#define ACTIVE_G_CODES 17", _src("src/emc/rs274ngc/interp_base.hh"))
        self.assertEqual(len(F.SIM_STARTUP_GCODES), F.ACTIVE_G_CODES)


if __name__ == "__main__":
    unittest.main()
