"""The boundary between this UI and LinuxCNC.

Everything LinuxCNC-specific lives here. The view layer sees only MachineState,
so it can be rendered and tested with no machine and no `linuxcnc` module.

Three pieces:

  MachineAdapter      the interface the UI calls
  MockAdapter         what this iteration actually renders from
  LinuxCncAdapter     the real one, importing `linuxcnc` lazily; tested against
                      a fake built from the 2.9 bindings (ui/tests/fake_linuxcnc.py),
                      never yet run on a machine

and, most importantly:

  map_linuxcnc_state()  a PURE function turning (stat, hal_values, profile) into
                        MachineState.

That last one is separated out deliberately. Translating LinuxCNC's status
structure is the part most likely to be wrong, and keeping it pure means it is
testable against a hand-built fake stat object without a running machine.

The stat fields read, verified against the Python bindings
(src/emc/usr_intf/axis/extensions/emcmodule.cc); ui/tests/fake_linuxcnc.py
STAT_ATTRIBUTES is the checked list (test_linuxcnc_adapter.py ties both this
module and the fake to the binding):

  map_linuxcnc_state:
  stat.task_state            emcmodule.cc:329   STATE_ESTOP / STATE_ESTOP_RESET /
                                                STATE_OFF / STATE_ON
  stat.joint[n]["homed"]     emcmodule.cc:599   (getter :741)
  stat.joint[n]["homing"]    emcmodule.cc:598
  stat.actual_position       emcmodule.cc:738
  stat.g5x_offset            emcmodule.cc:753
  stat.g5x_index             emcmodule.cc:754
  stat.feedrate              emcmodule.cc:372   motion.traj.scale
  stat.file                  emcmodule.cc:342
  stat.paused                emcmodule.cc:371
  stat.interp_state          emcmodule.cc:337

  LinuxCncAdapter, in addition:
  stat.task_mode             emcmodule.cc:328
  stat.motion_mode           emcmodule.cc:363
  stat.kinematics_type       emcmodule.cc:381
  stat.joints                emcmodule.cc:360
  stat.homed                 emcmodule.cc:747
  stat.inpos                 emcmodule.cc:366   motion.traj.inpos
  stat.joint[n]["homing"], stat.file, stat.interp_state (as above)

stat.estop (emcmodule.cc:418) is deliberately NOT read: see map_linuxcnc_state.

Both adapters share the MDI gate (mdi.mdi_refusal) and the "does this MDI
line move the machine" rule (mdi._needs_homing), so the mock cannot drift from
what the real adapter - and LinuxCNC - refuse.
"""

from __future__ import annotations

import abc
import datetime
import functools
import math
import os
import re
import subprocess
import threading
import time
import types
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .capabilities import Capability
# Re-exported: the MDI gate used to live here, and tests import it from here.
from .mdi import _needs_homing, mdi_refusal  # noqa: F401
from .profiles import MachineProfile
from .state import (
    AlarmEvent,
    AxisPosition,
    DrivePower,
    EstopState,
    HomingPhase,
    HomingState,
    JointHoming,
    LinkHealth,
    MachineState,
    MdiEntry,
    Severity,
    initial_estop,
)

# LinuxCNC task_state values (emcmodule.cc:329-334). Mirrored rather than
# imported so this module works without the linuxcnc package.
TASK_STATE_ESTOP = 1
TASK_STATE_ESTOP_RESET = 2
TASK_STATE_OFF = 3
TASK_STATE_ON = 4

#: HAL pins the UI reads for link health. Verified against the driver source.
HAL_LINK_PINS = (
    LinkHealth.HAL_PACKET_ERROR_TOTAL,   # hm2_eth.c:1558-1561
    LinkHealth.HAL_WATCHDOG_HAS_BIT,     # watchdog.c:140-144
    LinkHealth.HAL_IO_ERROR,             # hostmot2.c:1446
)


# ---------------------------------------------------------------------------
# Pure mapping
# ---------------------------------------------------------------------------


def map_drive_power(
    profile: MachineProfile,
    estop_input_value: Optional[bool],
) -> DrivePower:
    """Decide the HARDWARE drive-power state.

    The rule that matters: with no hardware estop input, the answer is UNKNOWN,
    never LIVE. LinuxCNC has nothing to read, so claiming the drives are powered
    would be an invention. A green "powered" light on an unsensed chain is worse
    than an honest "unknown".

    `estop_input_value` must come from the physical input pin itself, never
    from stat.estop: see map_linuxcnc_state.
    """
    if not profile.has(Capability.ESTOP_INPUT):
        return DrivePower.UNKNOWN
    if estop_input_value is None:
        return DrivePower.UNKNOWN
    # Input asserted (chain closed) means power is present.
    return DrivePower.LIVE if estop_input_value else DrivePower.CUT


def map_linuxcnc_state(
    stat: Any,
    hal_values: Mapping[str, Any],
    profile: MachineProfile,
    joint_axis_map: Sequence[str],
    joint_config: Mapping[int, Mapping[str, float]],
    alarms: Sequence[AlarmEvent] = (),
    mdi_history: Sequence[MdiEntry] = (),
) -> MachineState:
    """Turn a LinuxCNC status snapshot into MachineState. Pure.

    `stat` only needs the attributes listed in this module's docstring, so a
    plain object or SimpleNamespace works for tests.

    Drive power comes ONLY from hal_values["estop_input"], the physical chain
    input. stat.estop is not used for it: stat.estop is io.aux.estop
    (emcmodule.cc:418), LinuxCNC's software latch, which is `!emc-enable-in`
    (ioControl.cc:523-526) and starts at 1 at every startup (ioControl.cc:791).
    In the estop-latch wiring emc-enable-in stays false until the GUI's reset
    edge (ioControl.cc:1090-1096), so reading stat.estop as "chain open" would
    report a hardware cut at every startup and disable the very reset button
    that clears the latch. The software side of it is already in
    task_state (determineState, emctask.cc:419-430).
    """
    task_state = int(getattr(stat, "task_state", TASK_STATE_ESTOP))
    software_disabled = task_state != TASK_STATE_ON

    estop = EstopState(
        drive_power=map_drive_power(profile, hal_values.get("estop_input")),
        software_disabled=software_disabled,
        hardware_input_present=profile.has(Capability.ESTOP_INPUT),
    )

    joints_raw = list(getattr(stat, "joint", []) or [])
    homing_joints: List[JointHoming] = []
    for jnum, axis in enumerate(joint_axis_map):
        j = joints_raw[jnum] if jnum < len(joints_raw) else {}
        if j.get("homing"):
            phase = HomingPhase.HOMING
        elif j.get("homed"):
            phase = HomingPhase.HOMED
        else:
            phase = HomingPhase.NOT_HOMED
        cfg = joint_config.get(jnum, {})
        homing_joints.append(
            JointHoming(
                joint=jnum,
                axis=axis,
                phase=phase,
                sequence=int(cfg.get("HOME_SEQUENCE", jnum)),
                # Passed through: None means the INI value is unreadable
                # (state.JointHoming.ignore_limits) and must stay None.
                ignore_limits=cfg.get("HOME_IGNORE_LIMITS"),
                search_vel=float(cfg.get("HOME_SEARCH_VEL", 0.0)),
            )
        )

    link = LinkHealth(
        connected=bool(hal_values.get("connected", False)),
        packet_error_total=_opt_int(hal_values.get(LinkHealth.HAL_PACKET_ERROR_TOTAL)),
        watchdog_has_bit=_opt_bool(hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT)),
        io_error=_opt_bool(hal_values.get(LinkHealth.HAL_IO_ERROR)),
        # [EMCMOT]SERVO_PERIOD from the INI, supplied by LinuxCncAdapter;
        # None when the INI has none - never an invented figure.
        servo_period_ms=_opt_float(hal_values.get("servo_period_ms")),
    )

    actual = list(getattr(stat, "actual_position", ()) or ())
    offsets = list(getattr(stat, "g5x_offset", ()) or ())
    positions: List[AxisPosition] = []
    for idx, axis in enumerate(("X", "Y", "Z")):
        machine = float(actual[idx]) if idx < len(actual) else 0.0
        offset = float(offsets[idx]) if idx < len(offsets) else 0.0
        homed = any(
            j.axis == axis and j.phase is HomingPhase.HOMED for j in homing_joints
        )
        positions.append(
            AxisPosition(axis=axis, work=machine - offset, machine=machine, homed=homed)
        )

    return MachineState(
        estop=estop,
        homing=HomingState(joints=tuple(homing_joints)),
        link=link,
        positions=tuple(positions),
        alarms=tuple(alarms),
        mdi_history=tuple(mdi_history),
        wcs=_wcs_name(int(getattr(stat, "g5x_index", 1))),
        jog_step_mm=float(hal_values.get("jog_step_mm", 1.0)),
        feed_override_pct=int(round(float(getattr(stat, "feedrate", 1.0)) * 100)),
        program_file=getattr(stat, "file", None) or None,
        program_running=not bool(getattr(stat, "paused", 0))
        and bool(getattr(stat, "file", None))
        and int(getattr(stat, "interp_state", 1)) != 1,
        program_progress=float(hal_values.get("program_progress", 0.0)),
        message=str(hal_values.get("message", "")),
    )


def _opt_int(v: Any) -> Optional[int]:
    return None if v is None else int(v)


def _opt_bool(v: Any) -> Optional[bool]:
    return None if v is None else bool(v)


def _opt_float(v: Any) -> Optional[float]:
    return None if v is None else float(v)


#: g5x_index 1..9 in the interpreter's order (interp_convert.cc:1762-1789).
_G5X_NAMES = ("G54", "G55", "G56", "G57", "G58", "G59", "G59.1", "G59.2", "G59.3")


def _wcs_name(g5x_index: int) -> str:
    """g5x_index 1 -> G54 ... 6 -> G59, 7..9 -> G59.1..G59.3 (emcmodule.cc:754,
    interp_convert.cc:1762-1789). Anything else (0 before the first poll,
    emcmodule.cc Stat zero-fill) falls back to G54."""
    if 1 <= g5x_index <= len(_G5X_NAMES):
        return _G5X_NAMES[g5x_index - 1]
    return "G54"


# ---------------------------------------------------------------------------
# INI helpers shared by both adapters
# ---------------------------------------------------------------------------


def _ini_number(ini: Any, *keys: Tuple[str, str]) -> Optional[float]:
    """First of `keys` ([SECTION, KEY]) present in `ini`, as a float, or None."""
    if ini is None:
        return None
    for section, key in keys:
        value = ini.get(section, key)
        if value is not None:
            try:
                return value.as_float()
            except ValueError:
                return None
    return None


def _load_profile_ini(profile: MachineProfile) -> Any:
    from .iniload import load_ini

    return load_ini(profile.ini_path) if profile.ini_path else None


#: What sscanf("%d") reads at the start of a value: optional whitespace, a
#: sign, decimal digits. Anything after them ("0.5" -> 0) is ignored.
_SCANF_DECIMAL_RE = re.compile(r"[ \t\n\v\f\r]*([+-]?[0-9]+)")


def _no_force_homing(ini: Any) -> bool:
    """Whether [TRAJ]NO_FORCE_HOMING lets MDI and programs run unhomed
    (emctaskmain.cc:2208, 2295). Read the way task reads it
    (emctaskmain.cc:3245-3262): sscanf("%d") takes the leading integer, so
    "0.5" is 0 and "1.5" is 1; task then forces a value <= 0 to 0 (off); a
    value sscanf cannot read is off too. So: on only for a leading integer
    >= 1."""
    value = ini.get("TRAJ", "NO_FORCE_HOMING") if ini is not None else None
    if value is None:
        return False
    m = _SCANF_DECIMAL_RE.match(value.raw)
    return bool(m) and int(m.group(1)) >= 1


def _read_profile_ini(profile: MachineProfile) -> Tuple[Any, Any]:
    """(MachineConfig, raw IniFile or None) for the profile: what each
    adapter's __init__ takes from the INI, read once each. The raw file
    carries the [DISPLAY], [TRAJ] and [EMCIO] keys configmodel does not.

    [EMCMOT]SERVO_PERIOD comes from config.servo_period_ms, so the link card
    never contradicts the config card; it is None when the INI has none, and
    LinkHealth then shows "—" rather than a placeholder (never the profile's
    platform figure).
    """
    from .configmodel import load_machine_config

    return load_machine_config(profile), _load_profile_ini(profile)


def _joint_config(config: Any) -> Dict[int, Dict[str, Any]]:
    """configmodel.joint_config_from_profile, from an already loaded
    MachineConfig, so an adapter does not read its INI a third time. The two
    must agree (test_mdi_tools_adapter pins that for both profiles)."""
    if not config.present:
        return {
            i: {"axis": a, "HOME_SEQUENCE": i, "HOME_IGNORE_LIMITS": False,
                "HOME_SEARCH_VEL": 0.0}
            for i, a in enumerate(("X", "Y", "Z"))
        }
    return {
        a.joint: {
            "axis": a.axis,
            "HOME_SEQUENCE": a.home_sequence,
            "HOME_IGNORE_LIMITS": a.home_ignore_limits,
            "HOME_SEARCH_VEL": a.home_search_vel,
        }
        for a in config.axes
    }


# ---------------------------------------------------------------------------
# Adapter interface
# ---------------------------------------------------------------------------


class MachineAdapter(abc.ABC):
    """What the UI is allowed to ask of a machine."""

    profile: MachineProfile

    @abc.abstractmethod
    def snapshot(self) -> MachineState:
        ...

    @abc.abstractmethod
    def send_mdi(self, command: str) -> MdiEntry:
        ...

    @abc.abstractmethod
    def software_estop(self) -> None:
        ...

    @abc.abstractmethod
    def software_estop_reset(self) -> None:
        ...

    @abc.abstractmethod
    def home_all(self) -> None:
        ...

    @abc.abstractmethod
    def jog(self, axis: str, direction: int) -> None:
        ...

    @abc.abstractmethod
    def program_start(self) -> None:
        ...

    @abc.abstractmethod
    def program_stop(self) -> None:
        ...


# ---------------------------------------------------------------------------
# Mock
# ---------------------------------------------------------------------------


def _locked(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a MockAdapter method under the adapter's own lock."""

    @functools.wraps(method)
    def wrapper(self: "MockAdapter", *args: Any, **kwargs: Any) -> Any:
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class MockAdapter(MachineAdapter):
    """In-memory machine. What this iteration renders from.

    It is not a toy: it enforces the same rules the real machine does, so the
    tests that matter (estop cannot be cleared in software, program start gated on
    homing, MDI refused before homing) exercise real logic rather than a
    rendering coincidence. The MDI gate is the real adapter's (mdi_refusal).

    Threading: app.Session calls software_estop without its own lock, so it
    may arrive while another method runs in another thread. Every public
    method therefore runs under this adapter's own small lock. No method
    waits or does I/O while holding it, so the estop is delayed by at most
    one in-memory method body - and a method that checked "not in estop"
    can no longer write "running" or "homed" after the estop landed.
    """

    def __init__(self, profile: MachineProfile, joint_config: Optional[Dict] = None):
        self.profile = profile
        self._lock = threading.Lock()
        config, ini = _read_profile_ini(profile)
        self._joint_config = (
            joint_config if joint_config is not None else _joint_config(config)
        )
        axes = [c["axis"] for c in self._joint_config.values()] or ["X", "Y", "Z"]
        self._no_force_homing = _no_force_homing(ini)

        self._estop = initial_estop(profile.has(Capability.ESTOP_INPUT))
        self._homing = HomingState(
            joints=tuple(
                JointHoming(
                    joint=jnum,
                    axis=cfg["axis"],
                    phase=HomingPhase.NOT_HOMED,
                    sequence=int(cfg.get("HOME_SEQUENCE", jnum)),
                    # Passed through; None = unreadable (state.JointHoming).
                    ignore_limits=cfg.get("HOME_IGNORE_LIMITS"),
                    search_vel=float(cfg.get("HOME_SEARCH_VEL", 0.0)),
                )
                for jnum, cfg in sorted(self._joint_config.items())
            )
        )
        self._positions = tuple(
            AxisPosition(axis=a, work=0.0, machine=0.0, homed=False) for a in axes
        )
        self._link = LinkHealth(
            connected=True,
            packet_error_total=0,
            watchdog_has_bit=False,
            io_error=False,
            servo_period_ms=config.servo_period_ms,
        )
        self._mdi: List[MdiEntry] = []
        self._message = "Konfigürasyon yüklendi. Referans alınmadı."
        self._alarms: List[AlarmEvent] = [
            AlarmEvent(
                _now(), "hm2_eth: 7i92 bulundu, IDROM eşleşti (2 port × 17 = 34 IO)",
                "hm2_eth", Severity.INFO,
            ),
            AlarmEvent(
                _now(), "Üç stepgen aktif, üç eksen jog ediyor", "hm2_7i92.0", Severity.INFO,
            ),
        ]
        # Driven by the INI's ASSUMPTION markers, so measuring the travel and
        # deleting the markers withdraws it with no code change (iniload.py:18-19).
        if not config.soft_limits_configured:
            self._alarms.append(
                AlarmEvent(
                    _now(),
                    "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok",
                    "config", Severity.WARN,
                )
            )
        if profile.key == "test-rig":
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "Y ekseninin DIR hattında arıza — yön değişmiyor, hep öne gidiyor; "
                    "yeri (kart klemensi, kablo ya da sürücü) ölçülmedi",
                    "docs/DURUM.md:12", Severity.ERROR,
                ),
            )
        self._program_file: Optional[str] = None
        self._running = False
        self._progress = 0.0
        self._wcs = "G54"
        self._jog_step = 1.0
        self._feed = 100

    # -- reads ---------------------------------------------------------------

    @_locked
    def snapshot(self) -> MachineState:
        return self._state()

    def _state(self) -> MachineState:
        """snapshot() without the lock, for methods that already hold it."""
        return MachineState(
            estop=self._estop,
            homing=self._homing,
            link=self._link,
            positions=self._positions,
            alarms=tuple(self._alarms),
            mdi_history=tuple(self._mdi),
            wcs=self._wcs,
            jog_step_mm=self._jog_step,
            feed_override_pct=self._feed,
            program_file=self._program_file,
            program_running=self._running,
            program_progress=self._progress,
            message=self._message,
        )

    # -- writes --------------------------------------------------------------

    @_locked
    def send_mdi(self, command: str) -> MdiEntry:
        command = (command or "").strip()
        refusal = mdi_refusal(
            command,
            machine_on=not self._estop.blocks_motion,
            homed=self._homing.all_homed,
            no_force_homing=self._no_force_homing,
            program_running=self._running,
        )
        entry = MdiEntry(command, refusal is None, refusal or "Kabul edildi.")
        self._mdi.append(entry)
        self._message = "MDI: {} — {}".format(command or "(boş)", entry.note)
        return entry

    @_locked
    def software_estop(self) -> None:
        self._estop = self._estop.software_estop()
        self._running = False
        self._message = "Yazılım acil stop. Sürücü gücü DEĞİŞMEDİ."
        self._alarms.insert(
            0, AlarmEvent(_now(), "Yazılım acil stop uygulandı", "task", Severity.ERROR)
        )

    @_locked
    def software_estop_reset(self) -> None:
        before = self._estop
        self._estop = self._estop.software_reset()
        if self._estop is before and before.drive_power is DrivePower.CUT:
            self._message = (
                "Yazılım sıfırlaması reddedildi — sürücü gücü donanımsal kesik."
            )
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "Yazılım sıfırlaması reddedildi: donanım zinciri açık",
                    "task", Severity.WARN,
                ),
            )
        else:
            # Same wording as LinuxCncAdapter: homing survives a software
            # estop unless a joint has VOLATILE_HOME (emctask.cc:364,
            # homing.c:316-323).
            self._message = "Yazılım acil stop sıfırlandı." + (
                "" if self._homing.all_homed else " Referans gerekli."
            )

    @_locked
    def hardware_chain_open(self) -> None:
        """Simulate the physical chain opening. Not reachable from the UI."""
        self._estop = self._estop.hardware_chain_opened()
        self._running = False
        self._alarms.insert(
            0,
            AlarmEvent(
                _now(), "Acil stop zinciri açıldı — sürücü gücü kesildi",
                "estop-chain", Severity.ERROR,
            ),
        )

    @_locked
    def home_all(self) -> None:
        if self._estop.blocks_motion:
            self._message = "Makine devre dışı — referans alınamaz."
            return
        self._homing = self._homing.all_homed_state()
        self._positions = tuple(p._replace(homed=True) for p in self._positions)
        self._message = "Referans tamam. Sıra: {}.".format(self._homing.order_label)
        self._alarms.insert(
            0,
            AlarmEvent(
                _now(),
                "Referans alma tamamlandı ({})".format(self._homing.order_label),
                "homing", Severity.INFO,
            ),
        )

    @_locked
    def jog(self, axis: str, direction: int) -> None:
        if self._estop.blocks_motion:
            self._message = "Makine devre dışı — jog yok."
            return
        self._positions = tuple(
            p._replace(
                work=p.work + direction * self._jog_step,
                machine=p.machine + direction * self._jog_step,
            )
            if p.axis == axis
            else p
            for p in self._positions
        )
        self._message = "Jog {}{}{:g} mm".format(
            axis, "+" if direction > 0 else "-", self._jog_step
        )

    @_locked
    def load_program(self, name: str) -> None:
        self._program_file = name
        self._progress = 0.0
        self._running = False
        self._message = "{} yüklendi.".format(name)

    @_locked
    def program_start(self) -> None:
        blocks = self._state().start_blocks
        if blocks:
            self._message = "Başlatılamaz: {}".format(blocks[0])
            return
        if self._program_file is None:
            self._message = "Program yüklenmedi."
            return
        self._running = True
        self._message = "Program çalışıyor."

    @_locked
    def program_stop(self) -> None:
        self._running = False
        self._progress = 0.0
        self._message = "Program durduruldu."

    @_locked
    def set_jog_step(self, mm: float) -> None:
        self._jog_step = float(mm)

    @_locked
    def set_feed_override(self, pct: int) -> None:
        self._feed = int(pct)

    @_locked
    def set_wcs(self, wcs: str) -> None:
        self._wcs = wcs


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Real
# ---------------------------------------------------------------------------


#: Coordinate systems the UI may select, in g5x_index order 1..6
#: (src/emc/rs274ngc/interp_convert.cc:1762-1779). G59.1-G59.3 (7..9) are
#: legal in LinuxCNC and are shown when stat reports them (_wcs_name), but
#: set_wcs refuses them by choice: the UI offers G54-G56 only
#: (viewmodel.control_screen, wcs_options), and nothing here uses 7..9 yet.
_WCS_CODES = _G5X_NAMES[:6]

#: The longest any LinuxCncAdapter action waits in command.wait_complete().
#:
#: Why short: the binding keeps the GIL for the whole wait. wait_complete
#: (emcmodule.cc:1383-1388) calls emcWaitCommandComplete (:212-231), which
#: sleeps in esleep() with no Py_BEGIN_ALLOW_THREADS - the module's only one is
#: in logger.start (:2125). While an action waits, no other Python thread runs,
#: the HTTP thread carrying a software estop included, and on the test rig
#: that button is the only estop. The binding's default is
#: EMC_COMMAND_TIMEOUT = 5 s (:209).
#:
#: Every command SEND holds the GIL too: emcSendCommand (:236-251) spins until
#: task echoes the command, up to those 5 s. Task echoes an ordinary command
#: at the end of the cycle that read it (about one [TASK]CYCLE_TIME), but a
#: state() command (ESTOP, ESTOP_RESET, ON) only after task's emcLubeOn/Off
#: has waited for iocontrol (sendCommand, taskclass.cc:155-200; iocontrol
#: sleeps one [EMCIO]CYCLE_TIME between reads, ioControl.cc:1138) - so about
#: one [EMCIO]CYCLE_TIME plus a task cycle. The worst normal delay of a
#: software estop is therefore about max(WAIT_TIMEOUT_S, one EMCIO cycle plus
#: one task cycle), and up to 5 s per send if task or iocontrol stalls.
#: Computed from the source, not measured.
#:
#: Why waiting is not enough anyway: task reports RCS_DONE only when motion is
#: DONE and the interpreter idle (emctaskmain.cc:3546-3561), and a joint is
#: RCS_EXEC until it is in position (taskintf.cc:974-983). While a jog or a
#: homing move is still running, no wait of any length short of the move
#: itself returns DONE.
#:
#: Nor is DONE proof: NML serial numbers are global across channels
#: (emcCommand is a "queue ... serial" buffer, configs/common/linuxcnc.nml:9;
#: write_id comes from the shared queue header, cms_in.cc:1489-1516), and
#: emcWaitCommandComplete returns DONE as soon as task has echoed any LATER
#: serial (emcmodule.cc:218-220). A software estop sent on the other channel
#: during a wait can therefore end that wait early.
#:
#: So every wait is short and is followed by reading the state it was meant to
#: produce; if that state was not reached the action is refused with a Turkish
#: message, never continued in the wrong mode. 0.25 s is 25 cycles of the
#: rig's [TASK]CYCLE_TIME = 0.010. Chosen, not measured: if it proves too short
#: on the machine, the symptom is a refusal that goes away on retry.
WAIT_TIMEOUT_S = 0.25

#: LinuxCNC's own default when [EMCIO]CYCLE_TIME is absent (emccfg.h:34,
#: applied by ioControl.cc:223-238). A value <= 0 falls back to it as well,
#: by choice: iocontrol itself accepts any number there (sscanf %lf with no
#: range check, ioControl.cc:225), but a reset deadline of zero would refuse
#: every reset whose latch is not clear on the first poll.
DEFAULT_EMCIO_CYCLE_TIME_S = 0.100

#: Pause between stat polls while waiting for the estop latch; the same
#: interval the binding uses between its own checks (EMC_COMMAND_DELAY,
#: emcmodule.cc:210). time.sleep releases the GIL, unlike wait_complete.
_RESET_POLL_INTERVAL_S = 0.01

#: At most this many error-channel messages are read per drain. Error_poll
#: answers (type, "unrecognized error N") for a type it does not know
#: (emcmodule.cc:1588-1593), and NML read() errors come back as such a type
#: while the buffer still reports valid() - a loop without a bound could
#: then spin forever, under app.Session's lock.
_MAX_ERRORS_PER_DRAIN = 64

#: Stands in for stat when the status channel cannot be read. The mapped
#: state is then marked status_known=False with no positions: the other
#: fields map_linuxcnc_state fills in (software disabled, nothing homed,
#: feed 100 %, G54) are placeholders the view must not show as readings.
_NO_STATUS = types.SimpleNamespace()

#: The refusal when an action cannot read LinuxCNC's status before sending
#: anything.
NO_LINUXCNC_REFUSAL = "LinuxCNC'ye bağlantı yok — komut gönderilmedi."
#: The same, once the action had already sent a command.
LINUXCNC_LOST_REFUSAL = (
    "LinuxCNC'ye bağlantı koptu — gönderilen komutun sonucu bilinmiyor."
)


class LinuxCncUnavailable(RuntimeError):
    """LinuxCncAdapter cannot be built: no `linuxcnc` module, LinuxCNC not
    running, or this process cannot read LinuxCNC's status. The message is
    the operator's, in Turkish; app.main prints it and exits 1."""


def _tool_mmap_path() -> str:
    """LinuxCNC's tool-table mmap file as the binding looks for it:
    $HOME/.tool.mmap, /tmp without HOME (tool_mmap_fname and
    TOOL_MMAP_FILENAME, reference/linuxcnc/src/emc/tooldata/
    tooldata_mmap.cc:31, :74-80). Only for messages; the binding opens it."""
    return os.path.join(os.environ.get("HOME") or "/tmp", ".tool.mmap")


#: Why every stat.poll() after the first can fail in one process, and the
#: message for it. poll() opens the tool mmap on its first call in the
#: process; if that fails it sets a process-wide static, and from then on
#: every poll() of every stat object returns NULL without setting an
#: exception (emcmodule.cc:286-305), which Python raises as SystemError.
#: Seen in sim/ on 2026-10-19 (2.9.9): no restart of LinuxCNC helps, only a
#: new process.
POLL_SPOILED = (
    "linuxcnc.stat.poll() bu süreçte kalıcı olarak bozuldu: ilk poll'da "
    "LinuxCNC'nin takım tablosu ({}) yoktu — arayüzü LinuxCNC açıkken, onu "
    "başlatan kullanıcıyla (aynı HOME) yeniden başlatın"
)

# -- the board link, in the operator's words --------------------------------
#: What the operator reads when the board link is lost (watchdog bite or
#: hm2 io_error), and what to do. No "watchdog", no "io_error": the person at
#: the machine needs the cause they can check and the button to press.
LINK_LOST = "Kartla bağlantı koptu — makine durduruldu"
LINK_ACTION = (
    "Ethernet kablosunu ve kartın beslemesini kontrol edin, "
    "sonra «Yazılım sıfırla»ya basın."
)
#: After a reset that brought the link back. Position may be wrong: while
#: the board was cut off its step generators kept counting with their
#: outputs dropped (reference/linuxcnc/docs/src/drivers/hostmot2.adoc:96-103),
#: so the reset also unhomes every joint.
LINK_RESTORED = (
    "Kart bağlantısı yeniden kuruldu, makine açıldı. Konum kaymış olabilir: "
    "hareketten önce referansı yeniden alın."
)
LINK_NOT_RESTORED = (
    "Kartla bağlantı kurulamadı — makine açılamadı. Ethernet kablosunu ve "
    "kartın beslemesini kontrol edip tekrar «Yazılım sıfırla»ya basın."
)

#: How long one halcmd call may take before it counts as failed. halcmd
#: answers in tens of milliseconds; it waits longer only while another
#: process holds the HAL mutex. Chosen, not measured; 2 s keeps a reset from
#: hanging on it while being far above any normal answer.
HALCMD_TIMEOUT_S = 2.0


def _run_halcmd(args: List[str]) -> Tuple[bool, str]:
    """Run `halcmd ARGS`: (exit status 0, stdout + stderr). Never raises.

    halcmd, not hal.set_p: the binding's set_p refuses a pin that is on a
    signal ("pin connected to signal", halmodule.cc:970-975), and the
    watchdog pin is on the rig's `watchdog` net; halcmd `sets` can write it.
    """
    try:
        done = subprocess.run(
            ["halcmd"] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            universal_newlines=True, timeout=HALCMD_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    return done.returncode == 0, done.stdout


def _signal_of(show_pin: str, pin: str) -> Optional[str]:
    """The signal `halcmd -s show pin PIN` says PIN is on, or None.
    Lines read "OWNER TYPE DIR VALUE NAME <=> SIGNAL" (also ==> and <==),
    as halcmd 2.9.9 printed them in sim/ on 2026-10-20."""
    for line in show_pin.splitlines():
        words = line.split()
        if pin in words:
            i = words.index(pin)
            if i + 2 < len(words) and words[i + 1] in ("<=>", "==>", "<=="):
                return words[i + 2]
    return None


def _process_table(proc_root: str = "/proc") -> Optional[Dict[str, List[str]]]:
    """Running processes by name (comm) -> their pids, or None where there is
    no /proc to read (off Linux): the checks are then skipped, never failed."""
    if not os.path.isdir(proc_root):
        return None
    table: Dict[str, List[str]] = {}
    for entry in os.listdir(proc_root):
        if entry.isdigit():
            try:
                with open(os.path.join(proc_root, entry, "comm"), "r") as fh:
                    table.setdefault(fh.read().strip(), []).append(entry)
            except OSError:
                continue  # exited while we looked
    return table


def _voluntary_switches(proc_root: str, pid: str) -> Optional[int]:
    """The process's voluntary context switches (/proc/PID/status), or None."""
    try:
        with open(os.path.join(proc_root, pid, "status"), "r") as fh:
            for line in fh:
                if line.startswith("voluntary_ctxt_switches:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


#: A LinuxCNC loop that has not slept for this long is stuck. Task and
#: iocontrol each sleep once per cycle ([TASK]CYCLE_TIME 10 ms, [EMCIO]
#: CYCLE_TIME 100 ms on the rig), and every sleep is a voluntary context
#: switch the kernel counts in /proc/PID/status; in sim/ on 2026-10-20 task
#: counted ~87/s, iocontrol ~9/s, and a frozen task 0. Blocked in a lock or
#: spinning in a loop, the count stops. 2 s is 200 task cycles and 20
#: iocontrol cycles: no normal turn of either loop comes near it. This is the
#: stand-in for task's own heartbeat (emc_nml.hh:1172, emctask.cc:739),
#: which the Python binding does not export. Chosen; deploy/README.md
#: "Süreler".
LOOP_STALL_S = 2.0

#: Why the status is treated as lost while a loop is stuck: the status buffer
#: still reads, but nothing writes it, so every value in it is stale.
LOOP_STALLED = "{} {:g} s'dir cevap vermiyor"


def _refused_without_status(method: Callable[..., Any]) -> Callable[..., Any]:
    """Run a LinuxCncAdapter action; refuse it if LinuxCNC's status is lost.

    stat.poll() raises linuxcnc.error once the status buffer is invalid
    (check_stat, emcmodule.cc:277-283); error_channel.poll() does too, but
    _drain_errors catches that itself. Command sends and wait_complete never
    raise it (emcmodule.cc:212-251). So the poll is the one place an action
    can fail this way, and without this guard the exception would escape
    app.Session.act, which catches only ValueError/KeyError, and kill the
    HTTP request with no message.

    The outage is logged as snapshot() logs it, and the operator message says
    whether anything had been sent (counted by _send): every guarded action
    polls before its first send, so a dead status channel normally means
    nothing was sent. Returns None in place of the action's result.
    """

    @functools.wraps(method)
    def wrapper(self: "LinuxCncAdapter", *args: Any, **kwargs: Any) -> Any:
        sends_before = self._sends
        try:
            return method(self, *args, **kwargs)
        except self._linuxcnc.error as exc:
            self._note_status_lost(exc)
            self._message = (
                NO_LINUXCNC_REFUSAL if self._sends == sends_before
                else LINUXCNC_LOST_REFUSAL
            )
            return None

    return wrapper


class LinuxCncAdapter(MachineAdapter):
    """The real adapter.

    Tested against ui/tests/fake_linuxcnc.py, a fake `linuxcnc`/`hal` pair
    built from the LinuxCNC 2.9 bindings, task planner and motion source, and
    checked against that source by ui/tests/test_linuxcnc_adapter.py whenever
    reference/ is present. It has still never run on a real machine: the fake
    is only as right as our reading of the source, and real timing is not
    measured.

    Sequencing follows axis.py, the reference GUI
    (src/emc/usr_intf/axis/scripts/axis.py), with two deliberate differences:
    every wait is bounded by WAIT_TIMEOUT_S and checked, and homing is not
    waited for at all.

      task mode     mode(m), wait, poll, check mode    ensure_mode, :986-992
      jog mode      joint jog until homed, teleop after get_jog_mode, :3225-3242
      teleop switch teleop_enable(v), wait, check mode set_motion_teleop, :273-279
      home          MANUAL, teleop off, home(-1)       :2693-2702, :1954-1957
      program       synch, wait, program_open, wait    open_file_guts, :1206-1208
      stop          abort(), wait                      task_stop, :2411-2417
      estop         own channel, state(ESTOP), nothing else

    Refusals are decided here first, so the operator gets an immediate Turkish
    message through MachineState.message - the same channel MockAdapter uses.
    LinuxCNC's own refusal still arrives later through the error channel and
    lands in the alarm log. Every action that polls is guarded
    (_refused_without_status): with LinuxCNC's status lost it refuses
    instead of raising.

    Threading: app.Session runs every action under its lock except
    software_estop, which may arrive from another thread while an action is in
    progress. software_estop therefore uses its own command channel and
    touches nothing but that channel, _message, _alarms and the _estop_sent
    flag (one bool store; the snapshot only reads and clears it). An estop sent
    during another action's wait can end that wait early (see
    WAIT_TIMEOUT_S); the action's post-wait state check then refuses.

    Usage on the machine:

        from bettercnc.adapter import LinuxCncAdapter
        from bettercnc.profiles import load_profile
        adapter = LinuxCncAdapter(load_profile("test-rig"))
    """

    #: Teleop jog and the stat position tuples address axes by this fixed
    #: index (axis.py:3297, command.c:448).
    AXIS_LETTERS = "XYZABCUVW"

    def __init__(
        self,
        profile: MachineProfile,
        joint_config: Optional[Dict] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        halcmd: Callable[[List[str]], Tuple[bool, str]] = _run_halcmd,
        proc_root: str = "/proc",
    ):
        try:
            import linuxcnc  # type: ignore
        except ImportError as exc:
            raise LinuxCncUnavailable(
                "`linuxcnc` Python modülü yok — LinuxCNC ile gelir, PyPI'da yok. "
                "Makinede (ya da sim/ içinde) çalıştırın; makine dışında "
                "--adapter mock kullanın."
            ) from exc

        try:
            import hal  # type: ignore
        except ImportError:
            hal = None

        self._linuxcnc = linuxcnc
        self._hal = hal
        self.profile = profile
        self._clock = clock
        self._sleep = sleep
        self._halcmd = halcmd
        self._proc_root = proc_root
        self._stat = linuxcnc.stat()
        self._command = linuxcnc.command()
        # A separate channel for the software estop, so it never shares a
        # channel (and its serial number bookkeeping) with an action in
        # progress on another thread. Each command() opens its own command and
        # status NML channels (Command_init, emcmodule.cc:817-838).
        self._estop_channel = linuxcnc.command()
        self._error = linuxcnc.error_channel()
        self._probe_status()

        config, ini = _read_profile_ini(profile)
        self._joint_config = (
            joint_config if joint_config is not None else _joint_config(config)
        )
        self._alarms: List[AlarmEvent] = []
        self._mdi: List[MdiEntry] = []
        self._message = ""
        self._jog_step = 1.0  # same default as MockAdapter
        self._status_lost = False
        #: Commands sent on the action channel (see _send).
        self._sends = 0
        #: task_state at the previous snapshot, None before the first and
        #: after a status outage (see _note_task_state).
        self._last_task_state: Optional[int] = None
        #: Set by software_estop, so the snapshot that sees LinuxCNC leave ON
        #: knows the UI caused it (see _note_task_state).
        self._estop_sent = False

        # Same lookup order as axis.py:3400-3403. None means "not configured"
        # (or not a number): jog is then refused rather than run at an
        # invented speed.
        self._jog_velocity = _ini_number(
            ini, ("DISPLAY", "DEFAULT_LINEAR_VELOCITY"), ("TRAJ", "DEFAULT_LINEAR_VELOCITY")
        )
        # [DISPLAY]MAX_FEED_OVERRIDE in whole percent, read by
        # configmodel.max_feed_override_pct - the same value the Kontrol
        # screen disables the feed chips above. No INI at all: None, and feed
        # changes are refused.
        self._max_feed_pct: Optional[int] = config.max_feed_override_pct
        self._no_force_homing = _no_force_homing(ini)
        # How long software_estop_reset waits for LinuxCNC to leave ESTOP.
        # iocontrol re-reads emc-enable-in once per cycle (read_hal_inputs,
        # ioControl.cc:518-526, then esleep(emc_io_cycle_time), :1138), and
        # the estop-latch's ok-out rises only after iocontrol has raised
        # user-request-enable (ioControl.cc:1090-1096), so the reset needs
        # about two iocontrol cycles plus a task cycle. Three cycles is the
        # margin; not measured. Missing or <= 0: DEFAULT_EMCIO_CYCLE_TIME_S.
        io_cycle = _ini_number(ini, ("EMCIO", "CYCLE_TIME"))
        if io_cycle is None or io_cycle <= 0:
            io_cycle = DEFAULT_EMCIO_CYCLE_TIME_S
        self._estop_reset_deadline_s = 3 * io_cycle
        self._servo_period_ms: Optional[float] = config.servo_period_ms
        # LinuxCNC's own processes, for linuxcnc_lost(), named as the linuxcnc
        # script names them (reference/linuxcnc/scripts/linuxcnc.in): the NML
        # server, hardcoded (:556-558); iocontrol from [IO]IO, else
        # [EMCIO]EMCIO, else "io" (:545-546); task from [TASK]TASK, "emctask"
        # meaning linuxcnctask (:550-553).
        def ini_raw(section: str, key: str) -> Optional[str]:
            value = ini.get(section, key) if ini is not None else None
            return value.raw.strip() if value is not None else None

        task = ini_raw("TASK", "TASK") or "milltask"
        self._linuxcnc_processes = (
            "linuxcncsvr",
            "linuxcnctask" if task == "emctask" else task,
            ini_raw("IO", "IO") or ini_raw("EMCIO", "EMCIO") or "io",
        )
        #: name -> (switch count, when it last changed), for _stalled_loops.
        self._loop_seen: Dict[str, Tuple[int, float]] = {}
        self._hal_component = self._open_hal_component()

    def _probe_status(self) -> None:
        """Refuse to build unless LinuxCNC's status reads, twice over.

        Two polls, because of how the binding fails (POLL_SPOILED): the first
        poll in a process opens LinuxCNC's tool mmap, and if it is missing
        every later poll in the process fails for good. So:

        * the first poll raising linuxcnc.error means LinuxCNC is not running
          (check_stat, emcmodule.cc:277-283). The mmap was missing too, so
          this process can never read status again: it must exit, not wait.
        * the second poll raising SystemError means LinuxCNC runs but this
          process could not open the mmap - it looks under its own $HOME, so
          the UI runs as a different user (or HOME) than LinuxCNC.

        Started before LinuxCNC, the UI used to come up, show "bağlantı yok"
        once and then fail every request with that SystemError (sim/,
        2026-10-19).
        """
        L = self._linuxcnc
        for _ in range(2):
            try:
                self._stat.poll()
            except L.error as exc:
                raise LinuxCncUnavailable(
                    "LinuxCNC çalışmıyor ({}) — önce LinuxCNC'yi, sonra arayüzü "
                    "başlatın.".format(exc)
                ) from exc
            except SystemError as exc:
                raise LinuxCncUnavailable(POLL_SPOILED.format(_tool_mmap_path()) + ".") from exc

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> MachineState:
        try:
            s = self._poll()
        except self._linuxcnc.error as exc:
            return self._status_unreadable(exc)
        errored = self._drain_errors()
        hal_values = self._read_hal()
        self._note_task_state(s.task_state, hal_values, errored)
        return self._map(s, hal_values)

    def _note_task_state(self, task_state: int, hal_values: Dict[str, Any], errored: bool) -> None:
        """Say so when LinuxCNC leaves ON and the UI did not cause it.

        A watchdog bite, an estop chain opened in HAL, another GUI or halui:
        LinuxCNC leaves ON with no action of this UI, and nothing used to say
        so - the header and the estop panel changed, but the message line kept
        the last action's text (sim/, 2026-10-19: a watchdog bite left
        "Besleme %150 istendi..." on it). Now the snapshot that first sees the
        machine out of ON logs an ERROR alarm and, unless this same drain
        already put LinuxCNC's own error text there, sets the message.

        software_estop leaves ON on purpose and writes its own message; it
        sets _estop_sent so the transition it causes is not reported again.
        The flag is consumed by any snapshot that finds the machine out of
        ON, so it cannot later excuse a stop the UI did not cause.
        """
        L = self._linuxcnc
        previous, self._last_task_state = self._last_task_state, task_state
        ours = self._estop_sent
        if task_state != L.STATE_ON:
            self._estop_sent = False
        if previous != L.STATE_ON or task_state == L.STATE_ON or ours:
            return
        if hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or hal_values.get(
            LinkHealth.HAL_IO_ERROR
        ):
            text, action = LINK_LOST, LINK_ACTION
        elif task_state == L.STATE_ESTOP:
            text = "Makine acil stopa geçti — komut bu ekrandan gelmedi"
            action = "Sebep giderilince «Yazılım sıfırla»ya basın."
        else:
            text = "Makine kapatıldı — komut bu ekrandan gelmedi"
            action = "Sebep giderilince «Yazılım sıfırla»ya basın."
        self._alarms.insert(0, AlarmEvent(_now(), text, "task", Severity.ERROR))
        if not errored:
            self._message = "{}. {}".format(text, action)

    def _note_status_lost(self, exc: Exception) -> None:
        """Log a status outage: the alarm once per outage, not on every UI
        poll. Whether and how fast stat.poll() starts raising when LinuxCNC
        shuts down is not measured."""
        if not self._status_lost:
            self._status_lost = True
            self._message = "LinuxCNC'ye bağlantı yok — makine durumu okunamıyor."
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "LinuxCNC durum kanalı okunamıyor ({})".format(exc),
                    "linuxcnc", Severity.ERROR,
                ),
            )

    def _status_unreadable(self, exc: Exception) -> MachineState:
        """The snapshot while stat.poll() raises (check_stat,
        emcmodule.cc:277-283).

        Showing the last good stat would present a dead machine as live, and
        mapping an empty stat would invent readings (DRO +0.000, feed 100 %,
        G54). So the state says status_known=False and carries no positions;
        the view shows "—" for what LinuxCNC's status would have filled. The
        link and drive power come from HAL pins, which are read separately
        and keep their own meaning.
        """
        self._note_status_lost(exc)
        self._last_task_state = None
        return self._map(_NO_STATUS, self._read_hal())._replace(
            status_known=False, positions=()
        )

    def _map(self, stat: Any, hal_values: Dict[str, Any]) -> MachineState:
        hal_values["jog_step_mm"] = self._jog_step
        hal_values["message"] = self._message
        hal_values["servo_period_ms"] = self._servo_period_ms
        return map_linuxcnc_state(
            stat=stat,
            hal_values=hal_values,
            profile=self.profile,
            joint_axis_map=[
                c["axis"] for _, c in sorted(self._joint_config.items())
            ],
            joint_config=self._joint_config,
            alarms=tuple(self._alarms),
            mdi_history=tuple(self._mdi),
        )

    def _open_hal_component(self) -> Any:
        """Create an empty HAL component so `hal.get_value` works at all.

        get_value raises "Cannot call before creating component" until this
        process has called hal_init() (halmodule.cc:1010-1014; hal_shmem_base
        is set only in hal_init, hal_lib.c:217). halcmd does the same for the
        same reason (halcmd.c:103). The component has no pins, so it never
        joins a net - the UI stays a passive observer.

        Side effect, accepted: the component makes this process a HAL user
        component. When LinuxCNC shuts down, its script runs `halcmd unload
        all` (scripts/linuxcnc.in:707), which sends SIGTERM to every user
        component's pid (do_unloadusr_cmd, halcmd_commands.cc:1260-1290).
        Nothing here handles SIGTERM, so the whole UI process - web server
        included - exits with LinuxCNC. The UI cannot do anything useful
        without LinuxCNC anyway; restart it after LinuxCNC.
        """
        if self._hal is None:
            return None
        try:
            component = self._hal.component("bettercnc-ui-{}".format(os.getpid()))
            component.ready()
        except self._hal.error as exc:
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "HAL bileşeni açılamadı ({}) — bağlantı pinleri okunamıyor".format(exc),
                    "hal", Severity.WARN,
                ),
            )
            return None
        return component

    def _read_hal(self) -> Dict[str, Any]:
        """Read the link-health pins. An unreadable pin is None, never 0.

        `connected` is True only if at least one pin could be read: with no
        readable pin nothing is known about the link, and showing it healthy
        would present an unmeasured state as measured.

        No "estop_input" is read: no profile wires a hardware estop input yet
        (test-rig: absent, marble: TODO), so drive power stays UNKNOWN. A
        profile that wires one must name its HAL pin, and this method must
        read that pin here - never stat.estop (see map_linuxcnc_state).
        """
        if self._hal is None:
            return {"connected": False}
        out: Dict[str, Any] = {}
        for pin in HAL_LINK_PINS:
            try:
                out[pin] = self._hal.get_value(pin)
            except RuntimeError:
                # No component, or no such pin (halmodule.cc:1010-1014, 1078-1079).
                out[pin] = None
        out["connected"] = any(out[pin] is not None for pin in HAL_LINK_PINS)
        return out

    def _drain_errors(self) -> bool:
        """Pull from error_channel into the alarm log, newest first. Returns
        whether an error kind was drained, i.e. whether the operator message
        now carries LinuxCNC's own text.

        Error kinds also become the operator message, because that is where a
        refusal LinuxCNC makes after the fact (e.g. a jog motion rejects) has
        to show up. The four text and display kinds are INFO and leave the
        message alone. A kind Error_poll does not know - including the -1 a
        failed NML read gives, "unrecognized error -1" (emcmodule.cc:
        1588-1593) - counts as an ERROR and ends the drain. At most
        _MAX_ERRORS_PER_DRAIN messages are read per call; the rest wait for
        the next snapshot.
        """
        L = self._linuxcnc
        info_kinds = (L.NML_TEXT, L.NML_DISPLAY, L.OPERATOR_TEXT, L.OPERATOR_DISPLAY)
        errored = False
        for _ in range(_MAX_ERRORS_PER_DRAIN):
            try:
                got = self._error.poll()
            except L.error:
                # "Error buffer invalid" (Error_poll, emcmodule.cc:1562-1565);
                # snapshot reports the outage through the status channel.
                return errored
            if not got:
                return errored
            kind, text = got
            text = str(text).strip()
            if kind in info_kinds:
                self._alarms.insert(0, AlarmEvent(_now(), text, "linuxcnc", Severity.INFO))
                continue
            self._message = "LinuxCNC: {}".format(text)
            self._alarms.insert(0, AlarmEvent(_now(), text, "linuxcnc", Severity.ERROR))
            errored = True
            if kind not in (L.NML_ERROR, L.OPERATOR_ERROR):
                return errored
        return errored

    # -- helpers -------------------------------------------------------------

    def _poll(self) -> Any:
        """stat.poll(), returning the stat. Raises linuxcnc.error while the
        status buffer is invalid; callers are snapshot() and guarded actions.
        The first good poll after an outage logs the recovery, once.

        A SystemError from the binding (POLL_SPOILED) is raised as
        linuxcnc.error, so snapshot() and _refused_without_status treat it as
        lost status instead of letting it escape app.Session. _probe_status
        keeps it from happening at startup; this covers anything that spoils
        poll() later.

        So is a stuck LinuxCNC loop (_stalled_loops): its status buffer still
        reads, but stale, and a command sent to a stuck task would spin for up
        to 5 s holding the GIL (emcmodule.cc:209, :233-250). The screen then
        shows the status as unknown, and actions refuse without sending.
        """
        stalled = self._stalled_loops()
        if stalled:
            raise self._linuxcnc.error(LOOP_STALLED.format(", ".join(stalled), LOOP_STALL_S))
        try:
            self._stat.poll()
        except SystemError as exc:
            raise self._linuxcnc.error(POLL_SPOILED.format(_tool_mmap_path())) from exc
        if self._status_lost:
            self._status_lost = False
            self._message = "LinuxCNC durumu yeniden okunuyor."
            self._alarms.insert(
                0, AlarmEvent(_now(), "LinuxCNC durum kanalı yeniden okunuyor",
                              "linuxcnc", Severity.INFO),
            )
        return self._stat

    def _send(self) -> Any:
        """The action command channel, for a send: counts it, so a lost
        status channel can be reported with whether anything went out
        (_refused_without_status). Waits use self._command directly."""
        self._sends += 1
        return self._command

    def _wait(self) -> int:
        """Bounded wait_complete; see WAIT_TIMEOUT_S. Returns the RCS status,
        or -1 when the wait timed out (emcmodule.cc:230)."""
        return self._command.wait_complete(WAIT_TIMEOUT_S)

    def _all_homed(self, s: Any) -> bool:
        """The first stat.joints joints, like axis.py:1947-1952 and task's
        all_homed() (emctaskmain.cc:147-152). stat.homed has 16 entries
        (emcmodule.cc:541-547); the unused ones are 0."""
        homed = list(s.homed)[: int(s.joints)]
        return bool(homed) and all(homed)

    def _homing(self, s: Any) -> bool:
        """Any of the first stat.joints joints is homing (stat.joint[n]
        ["homing"], emcmodule.cc:598)."""
        return any(j["homing"] for j in list(s.joint)[: int(s.joints)])

    def _running(self, s: Any) -> bool:
        """A program or MDI command is running (or paused), axis.py:963-966."""
        L = self._linuxcnc
        return s.task_mode in (L.MODE_AUTO, L.MODE_MDI) and s.interp_state != L.INTERP_IDLE

    def _ensure_mode(self, mode: int, require_on: bool = True) -> bool:
        """axis.py:986-992, with a bounded wait. Returns whether the task is
        now in `mode` and, unless require_on is False, the machine is ON - an
        estop sent from another thread during the wait ends the action here.

        On failure LinuxCNC's reason (e.g. "Ignoring task mode change while
        jogging", emctask.cc:269-272) is drained into the alarm log now, so the
        caller's Turkish refusal stays the operator message.
        """
        s = self._poll()
        if s.task_mode != mode:
            self._send().mode(mode)
            self._wait()
            s = self._poll()
        off = require_on and s.task_state != self._linuxcnc.STATE_ON
        if off or s.task_mode != mode:
            self._drain_errors()
            return False
        return True

    def _set_motion_teleop(self, enable: bool) -> bool:
        """axis.py:273-279, with a bounded wait. Returns whether the machine
        is ON and motion is now in the requested mode.

        Motion honours a teleop request only while in position (control.c:
        927-958 drops it). Leaving teleop first aborts every teleop jog
        (EMCMOT_FREE, command.c:556-567) and switches once in position
        (control.c:961-972). Either way the mode is read back rather than
        assumed.
        """
        L = self._linuxcnc
        self._send().teleop_enable(1 if enable else 0)
        self._wait()
        s = self._poll()
        wanted = L.TRAJ_MODE_TELEOP if enable else L.TRAJ_MODE_FREE
        return s.task_state == L.STATE_ON and s.motion_mode == wanted

    def _switch_refused(self, what_failed: str, what: str) -> str:
        """Refusal text after a mode switch did not take. An estop that
        arrived meanwhile (from another thread) is reported as such."""
        if self._stat.task_state != self._linuxcnc.STATE_ON:
            return "Makine devre dışı — {}.".format(what)
        return "{} — {}.".format(what_failed, what)

    def _select_jog_mode(self) -> Optional[bool]:
        """axis.py get_jog_mode (:3225-3242). Returns jjogmode (True = joint
        jog), or None when motion did not switch to the mode the jog needs.

        Before homing motion is in free mode, where an axis jog carries joint
        number -1 and motion drops it with "Joint jog requested for undefined
        joint number=-1 (min=0,max=N)" (command.c:436-443). After homing it is
        in teleop and drops a joint jog with "JOG_INCR Mode is TELEOP, cannot
        jog joint" (command.c:421-426, :459).
        """
        L = self._linuxcnc
        s = self._poll()
        if s.kinematics_type == L.KINEMATICS_IDENTITY and self._all_homed(s):
            teleop = True
        else:
            # Keep what motion is in; another GUI (halui) may have changed it.
            teleop = s.motion_mode != L.TRAJ_MODE_FREE
        wanted = L.TRAJ_MODE_TELEOP if teleop else L.TRAJ_MODE_FREE
        if s.motion_mode != wanted and not self._set_motion_teleop(teleop):
            return None
        return not teleop

    def _joint_for_axis(self, axis: str) -> int:
        """First joint carrying `axis` (axis.py uses the first occurrence for a
        duplicated letter, :1938-1942). KeyError for an axis the profile lacks."""
        for jnum, cfg in sorted(self._joint_config.items()):
            if cfg["axis"] == axis:
                return jnum
        raise KeyError(axis)

    def _refuse_unless_on(self, s: Any, what: str) -> bool:
        if s.task_state != self._linuxcnc.STATE_ON:
            self._message = "Makine devre dışı — {}.".format(what)
            return True
        return False

    # -- writes --------------------------------------------------------------

    def send_mdi(self, command: str) -> MdiEntry:
        command = (command or "").strip()
        # None: the guard refused because LinuxCNC's status is lost, and the
        # refusal is already the operator message.
        entry = self._mdi_entry(command) or MdiEntry(command, False, self._message)
        self._mdi.append(entry)
        self._message = "MDI: {} — {}".format(command or "(boş)", entry.note)
        return entry

    @_refused_without_status
    def _mdi_entry(self, command: str) -> MdiEntry:
        """send_mdi's checks and send; the MdiEntry to record."""
        L = self._linuxcnc
        s = self._poll()
        refusal = mdi_refusal(
            command,
            machine_on=s.task_state == L.STATE_ON,
            homed=self._all_homed(s),
            no_force_homing=self._no_force_homing,
            # MDI mode with a command still running is NOT refused: LinuxCNC
            # queues MDI lines (emctaskmain.cc mdi_execute_queue).
            program_running=s.task_mode == L.MODE_AUTO and s.interp_state != L.INTERP_IDLE,
        )
        if refusal is not None:
            return MdiEntry(command, False, refusal)
        if not self._ensure_mode(L.MODE_MDI):
            return MdiEntry(command, False, self._switch_refused(
                "MDI moduna geçilemedi", "MDI kabul edilmedi"))
        try:
            self._send().mdi(command)
        except ValueError:
            # Longer than the 254-character NML field (emcmodule.cc:985-988).
            return MdiEntry(command, False, "Komut çok uzun — LinuxCNC satır sınırı aşıldı.")
        return MdiEntry(command, True, "Gönderildi.")

    def software_estop(self) -> None:
        """Send STATE_ESTOP on the dedicated channel. No wait, no poll.

        Safe to call from another thread while an action is in progress, and
        while LinuxCNC's status cannot be read: stat.poll() would raise then
        (emcmodule.cc:277-283), and the estop must still go out.

        The binding ignores whether the send succeeded (state(),
        emcmodule.cc:995-1010, drops emcSendCommand's result), and the send
        itself waits - holding the GIL - until task echoes the command: for
        state() about one [EMCIO]CYCLE_TIME, up to 5 s if task or iocontrol
        stalls (see WAIT_TIMEOUT_S). So the message says "sent"; whether
        LinuxCNC left ON shows in the next snapshot's task_state.

        On the test rig LinuxCNC then shows ESTOP_RESET, not ESTOP:
        iocontrol's user-enable-out, which ESTOP clears (ioControl.cc:1086),
        is not wired into the rig's estop chain (configs/test-rig/
        test-rig.hal, ESTOP section), so emc-enable-in stays true and only
        motion is disabled (determineState, emctask.cc:419-430). Either state
        refuses motion.
        """
        # Before the send, so a snapshot on another thread that sees LinuxCNC
        # leave ON already knows the UI caused it (_note_task_state).
        self._estop_sent = True
        self._estop_channel.state(self._linuxcnc.STATE_ESTOP)
        self._message = "Yazılım acil stop gönderildi. Sürücü gücü DEĞİŞMEDİ."
        self._alarms.insert(
            0, AlarmEvent(_now(), "Yazılım acil stop gönderildi", "task", Severity.ERROR)
        )

    @_refused_without_status
    def software_estop_reset(self) -> None:
        """Clear the software estop and switch the machine on.

        Note what this does NOT do: it cannot restore drive power. LinuxCNC
        leaves estop only when iocontrol's emc-enable-in is true
        (ioControl.cc:523-526, emctask.cc:419-430), so with the chain open the
        reset does not take and ON is not sent.

        Already ON: nothing to do. Refused without sending anything while a
        program or MDI command runs: ESTOP_RESET runs emcTaskAbort()
        (emctask.cc:340-349) in every mode, which would stop it. A program
        runs only in ON, so that guard is defence in depth behind the ON
        check.

        From ESTOP, ESTOP_RESET is sent and the stat is polled until LinuxCNC
        leaves ESTOP or 3 x [EMCIO]CYCLE_TIME passes (see __init__). Only
        then is the reset refused, without naming a cause: an open chain and
        a latch slower than the deadline look the same from here. From
        ESTOP_RESET (on the test rig, where a software estop leaves the latch
        closed; see software_estop) only ON is sent.

        Homing survives: a software estop unhomes only joints with
        VOLATILE_HOME (emctask.cc:364 emcJointUnhome(-2), homing.c:316-323).

        A lost board link is cleared first (_clear_link_faults): the hm2
        watchdog's has_bit and the driver's io_error stay set until someone
        clears them (watchdog.c:50-52, :259; hostmot2.c), and has_bit holds
        the rig's estop latch open, so without this the reset could never
        succeed and only a LinuxCNC restart would. Every joint is then
        unhomed, as the board's step generators kept counting while it was
        cut off (LINK_RESTORED). If the link is still bad, the watchdog bites
        again within its timeout and the reset is refused below.
        """
        L = self._linuxcnc
        s = self._poll()
        if s.task_state == L.STATE_ON:
            self._message = "Makine zaten açık."
            return
        if self._running(s):
            self._message = (
                "Program çalışırken yazılım sıfırlaması gönderilmedi — "
                "sıfırlama programı durdurur."
            )
            return
        link = self._read_hal()
        link_lost = bool(
            link.get(LinkHealth.HAL_WATCHDOG_HAS_BIT) or link.get(LinkHealth.HAL_IO_ERROR)
        )
        if link_lost:
            why = self._clear_link_faults(link)
            if why is not None:
                self._message = (
                    "Kart bağlantısı sıfırlanamadı — LinuxCNC'yi yeniden başlatın. "
                    "(Bakım için: {})".format(why)
                )
                self._alarms.insert(
                    0, AlarmEvent(_now(), "Kart bağlantısı sıfırlanamadı: {}".format(why),
                                  "hal", Severity.ERROR),
                )
                return
            # Immediate in ESTOP (emctaskmain.cc:814); motion accepts it while
            # disabled (command.c:1399-1402).
            self._send().unhome(-1)
            self._wait()
            self._alarms.insert(
                0, AlarmEvent(_now(), "Kart bağlantısı sıfırlandı, referanslar silindi",
                              "hal", Severity.WARN),
            )
        if s.task_state == L.STATE_ESTOP:
            self._send().state(L.STATE_ESTOP_RESET)
            s = self._wait_out_of_estop()
            if s.task_state == L.STATE_ESTOP and link_lost:
                self._message = LINK_NOT_RESTORED
                self._alarms.insert(
                    0, AlarmEvent(_now(), "Kartla bağlantı kurulamadı: makine acil stopta kaldı",
                                  "task", Severity.WARN),
                )
                return
            if s.task_state == L.STATE_ESTOP:
                self._message = (
                    "Yazılım sıfırlaması reddedildi — LinuxCNC {:g} s içinde acil "
                    "stoptan çıkmadı. Acil stop zinciri kapalıysa tekrar deneyin."
                ).format(self._estop_reset_deadline_s)
                self._alarms.insert(
                    0,
                    AlarmEvent(
                        _now(),
                        "Yazılım sıfırlaması reddedildi: LinuxCNC acil stopta kaldı",
                        "task", Severity.WARN,
                    ),
                )
                return
        self._send().state(L.STATE_ON)
        self._wait()
        s = self._poll()
        if s.task_state != L.STATE_ON and link_lost:
            self._message = LINK_NOT_RESTORED
            return
        if s.task_state != L.STATE_ON:
            self._message = "Makine açılamadı — LinuxCNC açık duruma geçmedi."
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(), "Makine açılamadı: LinuxCNC açık duruma geçmedi",
                    "task", Severity.WARN,
                ),
            )
            return
        if link_lost:
            self._message = LINK_RESTORED
            if any(s.homed[j] for j in self._joint_config):
                # set_all_unhomed refuses all-or-none, quietly (homing.c:294-314).
                self._message += (
                    " DİKKAT: referanslar silinemedi, LinuxCNC'yi yeniden başlatın."
                )
            return
        self._message = "Yazılım acil stop sıfırlandı." + (
            "" if self._all_homed(s) else " Referans gerekli."
        )

    def _clear_link_faults(self, link: Dict[str, Any]) -> Optional[str]:
        """Clear the board link's latched faults; None, or why it failed.

        io_error is a parameter, so setp clears it. has_bit is a pin, and on
        the rig it is on the `watchdog` signal (test-rig.hal, ESTOP CHAIN),
        where setp refuses it; so the signal it is on is looked up and
        cleared with sets. Clearing has_bit is how the driver is told to
        reset the board and pet the watchdog again (hostmot2.adoc:113-115,
        watchdog.c:259-266).
        """
        if link.get(LinkHealth.HAL_IO_ERROR):
            ok, out = self._halcmd(["setp", LinkHealth.HAL_IO_ERROR, "0"])
            if not ok:
                return out.strip() or "halcmd setp io_error"
        if link.get(LinkHealth.HAL_WATCHDOG_HAS_BIT):
            pin = LinkHealth.HAL_WATCHDOG_HAS_BIT
            ok, out = self._halcmd(["-s", "show", "pin", pin])
            if not ok:
                return out.strip() or "halcmd show pin"
            signal = _signal_of(out, pin)
            ok, out = self._halcmd(["sets", signal, "0"] if signal else ["setp", pin, "0"])
            if not ok:
                return out.strip() or "halcmd sets"
        return None

    def _stalled_loops(self) -> List[str]:
        """LinuxCNC's looping processes - task and iocontrol - whose loop has
        not slept for LOOP_STALL_S, judged by the kernel's count of their
        voluntary context switches. linuxcncsvr is not judged: it waits on
        events and counted 0/s when healthy (sim/, 2026-10-20). A missing
        process is not "stalled": linuxcnc_lost() reports it as missing. The
        first look at a process only records it."""
        table = _process_table(self._proc_root)
        if table is None:
            return []
        now = self._clock()
        stalled = []
        for name in self._linuxcnc_processes[1:]:
            counts = [
                c for c in (_voluntary_switches(self._proc_root, pid) for pid in table.get(name, []))
                if c is not None
            ]
            if not counts:
                continue
            total = sum(counts)
            seen = self._loop_seen.get(name)
            if seen is None or seen[0] != total:
                self._loop_seen[name] = (total, now)
            elif now - seen[1] >= LOOP_STALL_S:
                stalled.append(name)
        return stalled

    def linuxcnc_lost(self) -> Optional[str]:
        """Why LinuxCNC is gone or half gone, in the operator's words, or None.

        For app.serve's kiosk supervision (--exit-if-linuxcnc-lost). Two
        things are visible from here: the status channel no longer reads
        (stat.poll raises; linuxcncsvr is gone), and one of LinuxCNC's own
        processes has died while the rest runs on - its task crashed, say:
        the status buffer stays valid but stale then, and the on-screen estop,
        which goes through task, no longer reaches the machine.

        A task (or iocontrol) that hangs without dying: its heartbeat is in
        the status buffer (emc_nml.hh:1172) but the Python binding does not
        export it, so it is judged from the kernel's side (_stalled_loops).
        """
        running = _process_table(self._proc_root)
        if running is not None:
            missing = [p for p in self._linuxcnc_processes if p not in running]
            if missing:
                return "LinuxCNC'nin bir parçası çalışmıyor ({})".format(", ".join(missing))
            stalled = self._stalled_loops()
            if stalled:
                return "LinuxCNC'nin bir parçası cevap vermiyor ({})".format(", ".join(stalled))
        try:
            self._poll()
        except self._linuxcnc.error as exc:
            return "LinuxCNC'nin durumu okunamıyor ({})".format(exc)
        return None

    def _wait_out_of_estop(self) -> Any:
        """Poll until task_state leaves ESTOP or the reset deadline passes."""
        deadline = self._clock() + self._estop_reset_deadline_s
        while True:
            s = self._poll()
            if s.task_state != self._linuxcnc.STATE_ESTOP:
                return s
            remaining = deadline - self._clock()
            if remaining <= 0:
                return s
            self._sleep(min(_RESET_POLL_INTERVAL_S, remaining))

    @_refused_without_status
    def home_all(self) -> None:
        """Start homing. Not waited for: task reports DONE only when the whole
        homing move is over (see WAIT_TIMEOUT_S). Progress shows in
        stat.joint[n]["homing"] / ["homed"] on the next snapshots.

        Refused while a joint is still homing: motion's home-all then clears
        joint 0's homed flag and does NOT restart the sequence (H[0].homed = 0
        before do_home_all, which does nothing while homing is active;
        homing.c:553-560, :258-263), so the machine would end with joint 0
        unhomed.
        """
        L = self._linuxcnc
        s = self._poll()
        if self._refuse_unless_on(s, "referans alınamaz"):
            return
        if self._running(s):
            self._message = "Program çalışırken referans alınamaz."
            return
        if self._homing(s):
            self._message = "Referans alma sürüyor."
            return
        if not self._ensure_mode(L.MODE_MANUAL):
            self._message = self._switch_refused("MANUAL moduna geçilemedi", "referans alınamaz")
            return
        # Motion homes only in free mode: "must be in joint mode to home"
        # (command.c:1374-1378). After a first homing MANUAL means teleop.
        if not self._set_motion_teleop(False):
            self._message = self._switch_refused(
                "Eklem moduna geçilemedi (LinuxCNC jogu durdurup hareket bitince "
                "modu değiştirir; tekrar deneyin)",
                "referans alınamaz",
            )
            return
        self._send().home(-1)
        self._message = "Referans alma başlatıldı."

    @_refused_without_status
    def jog(self, axis: str, direction: int) -> None:
        L = self._linuxcnc
        jnum = self._joint_for_axis(axis)
        if direction not in (1, -1):
            raise ValueError("jog direction must be +1 or -1, got {!r}".format(direction))
        s = self._poll()
        if self._refuse_unless_on(s, "jog yok"):
            return
        if self._running(s):
            self._message = "Program çalışırken jog yok."
            return
        if self._jog_velocity is None:
            self._message = (
                "Jog hızı okunamadı — INI'de [DISPLAY] veya [TRAJ] "
                "DEFAULT_LINEAR_VELOCITY yok ya da sayı değil."
            )
            return
        if not self._ensure_mode(L.MODE_MANUAL):
            self._message = self._switch_refused("MANUAL moduna geçilemedi", "jog yok")
            return
        jjogmode = self._select_jog_mode()
        if jjogmode is None:
            self._message = self._switch_refused(
                "Jog modu değişmedi (LinuxCNC hareket sürerken mod değiştirmez)", "jog yok"
            )
            return
        index = jnum if jjogmode else self.AXIS_LETTERS.index(axis)
        # The increment is a magnitude; the velocity's sign is the direction
        # (command.c:873-877; emcJogIncr, taskintf.cc:818-845).
        self._send().jog(
            L.JOG_INCREMENT, jjogmode, index,
            direction * self._jog_velocity, self._jog_step,
        )
        self._message = "Jog {}{}{:g} mm gönderildi ({}).".format(
            axis, "+" if direction > 0 else "-", self._jog_step,
            "eklem" if jjogmode else "eksen",
        )

    @_refused_without_status
    def load_program(self, path: str) -> None:
        """Open a G-code file, as axis.py's open_file_guts does (:1174-1208).

        Refused up front while a joint is homing or, with the machine ON,
        motion is not in position (stat.inpos, emcmodule.cc:366; in free mode
        motion clears it while any joint's planner runs, control.c:1205-1272,
        i.e. during a joint jog). Out of ON motion is disabled and does not
        update the flag, so it is not consulted there. A teleop jog does not
        clear it (control.c's TELEOP branch never sets it); the rule below
        covers that case.

        After the open: a failure is status RCS_ERROR or stat.file not naming
        the file. A wait that timed out (-1) while stat.file names the file
        is a success - task reports DONE only once motion is quiet
        (emctaskmain.cc:3546-3561), so a timeout says nothing about the open.
        DONE alone is not proof either (serials, see WAIT_TIMEOUT_S), nor is
        stat.file alone: task never clears it on PLAN_CLOSE or a failed
        PLAN_OPEN, so reloading a file that has meanwhile become unreadable
        still shows its name - with RCS_ERROR.
        """
        L = self._linuxcnc
        s = self._poll()
        if self._running(s):
            self._message = "Program çalışırken yeni program yüklenemez."
            return
        if self._homing(s) or (s.task_state == L.STATE_ON and not s.inpos):
            self._message = "Referans alma ya da jog sürüyor — program yüklenmedi."
            return
        # PLAN_SYNCH is not accepted in AUTO (emctaskmain.cc:1015-1112), which
        # is why axis.py switches to MANUAL first. Loading needs no ON: task
        # takes SET_MODE, PLAN_SYNCH and PLAN_OPEN in estop too
        # (emctaskmain.cc:807-844).
        if not self._ensure_mode(L.MODE_MANUAL, require_on=False):
            self._message = self._switch_refused("MANUAL moduna geçilemedi", "program yüklenmedi")
            return
        self._send().task_plan_synch()
        self._wait()
        try:
            self._send().program_open(path)
        except ValueError:
            # Longer than the 254-character NML field (emcmodule.cc:1211-1214).
            self._message = "Dosya yolu çok uzun — program yüklenmedi."
            return
        status = self._wait()
        s = self._poll()
        if status == L.RCS_ERROR or s.file != path:
            # LinuxCNC's reason ("can't open ...") goes to the alarm log first,
            # so the Turkish refusal is the one message the operator sees.
            self._drain_errors()
            self._message = "Program açılamadı: {}".format(path)
            return
        self._message = "{} yüklendi.".format(path)

    @_refused_without_status
    def program_start(self) -> None:
        L = self._linuxcnc
        s = self._poll()
        blocks = self._map(s, self._read_hal()).start_blocks
        if blocks:
            self._message = "Başlatılamaz: {}".format(blocks[0])
            return
        if not s.file:
            self._message = "Program yüklenmedi."
            return
        if self._running(s):
            self._message = "Program zaten çalışıyor."
            return
        if not self._ensure_mode(L.MODE_AUTO):
            self._message = self._switch_refused("AUTO moduna geçilemedi", "program başlatılmadı")
            return
        self._send().auto(L.AUTO_RUN, 0)
        self._message = "Program başlatıldı: {}".format(s.file)

    @_refused_without_status
    def program_stop(self) -> None:
        """abort() first, unconditionally: stopping must not wait on a read.
        With the status lost after the send, the guard says the result is
        unknown."""
        L = self._linuxcnc
        self._send().abort()
        self._wait()
        s = self._poll()
        if s.interp_state == L.INTERP_IDLE:
            self._message = "Program durduruldu."
        else:
            self._message = "Durdurma gönderildi — LinuxCNC henüz boşta değil."

    def set_jog_step(self, mm: float) -> None:
        """Jog increment in mm. Must be positive: motion treats it as a
        magnitude and takes the direction from the velocity's sign
        (command.c:873-877), so a negative step would reverse the jog."""
        mm = float(mm)
        if not math.isfinite(mm) or mm <= 0:
            self._message = "Geçersiz jog adımı: {!r} mm.".format(mm)
            return
        self._jog_step = mm

    def set_feed_override(self, pct: int) -> None:
        """Feed override in percent, sent as command.feedrate(scale) the way
        axis.py does (:2104-2111). Not command.set_feed_override, which only
        enables or disables the override (emcmodule.cc:1314-1323).

        Motion clamps only negatives (command.c:1214-1221), so the INI ceiling
        [DISPLAY]MAX_FEED_OVERRIDE is applied here, in whole percent as axis.py
        does (:3353-3355). 0 % is a valid override (it holds the feed).
        """
        pct = int(pct)
        if self._max_feed_pct is None:
            self._message = "Besleme değiştirilmedi — INI yok, besleme sınırı bilinmiyor."
            return
        if pct < 0:
            self._message = "Geçersiz besleme yüzdesi: %{}.".format(pct)
            return
        if pct > self._max_feed_pct:
            self._message = "Besleme %{} istendi; INI sınırı %{} uygulandı.".format(
                pct, self._max_feed_pct
            )
            pct = self._max_feed_pct
        else:
            self._message = "Besleme %{}.".format(pct)
        self._send().feedrate(pct / 100.0)

    def set_wcs(self, wcs: str) -> None:
        """Select a coordinate system by MDI, under the same gates as send_mdi
        (and its guard). Only G54-G59 (_WCS_CODES)."""
        if wcs not in _WCS_CODES:
            self._message = "Bilinmeyen koordinat sistemi: {}".format(wcs)
            return
        self.send_mdi(wcs)
