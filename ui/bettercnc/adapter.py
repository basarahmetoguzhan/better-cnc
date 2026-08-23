"""The boundary between this UI and LinuxCNC.

Everything LinuxCNC-specific lives here. The view layer sees only MachineState,
so it can be rendered and tested with no machine and no `linuxcnc` module.

Three pieces:

  MachineAdapter      the interface the UI calls
  MockAdapter         what this iteration actually renders from
  LinuxCncAdapter     the real one, importing `linuxcnc` lazily

and, most importantly:

  map_linuxcnc_state()  a PURE function turning (stat, hal_values, profile) into
                        MachineState.

That last one is separated out deliberately. Translating LinuxCNC's status
structure is the part most likely to be wrong, and keeping it pure means it is
testable against a hand-built fake stat object without a running machine. Field
names are verified against the Python bindings:

  stat.task_state            emcmodule.cc:329   STATE_ESTOP / STATE_ESTOP_RESET /
                                                STATE_OFF / STATE_ON
  stat.estop                 emcmodule.cc:418   io.aux.estop
  stat.joint[n]["homed"]     emcmodule.cc:600
  stat.joint[n]["homing"]    emcmodule.cc:599
  stat.joint[n]["fault"]     emcmodule.cc:601
  stat.actual_position       emcmodule.cc:738
  stat.g5x_offset            emcmodule.cc:753
  stat.g5x_index             emcmodule.cc:752
  stat.file / .motion_line   emcmodule.cc
  stat.paused / .interp_state emcmodule.cc:337
"""

from __future__ import annotations

import abc
import datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .capabilities import Capability
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
    """
    task_state = int(getattr(stat, "task_state", TASK_STATE_ESTOP))
    software_disabled = task_state != TASK_STATE_ON

    drive_power = map_drive_power(
        profile, hal_values.get("estop_input")
    )
    # An estop reported through LinuxCNC's own io.aux.estop counts as the chain
    # being open, but only on a machine that actually has the input wired.
    if profile.has(Capability.ESTOP_INPUT) and int(getattr(stat, "estop", 0)):
        drive_power = DrivePower.CUT

    estop = EstopState(
        drive_power=drive_power,
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
                ignore_limits=bool(cfg.get("HOME_IGNORE_LIMITS", 0)),
                search_vel=float(cfg.get("HOME_SEARCH_VEL", 0.0)),
            )
        )

    link = LinkHealth(
        connected=bool(hal_values.get("connected", False)),
        packet_error_total=_opt_int(hal_values.get(LinkHealth.HAL_PACKET_ERROR_TOTAL)),
        watchdog_has_bit=_opt_bool(hal_values.get(LinkHealth.HAL_WATCHDOG_HAS_BIT)),
        io_error=_opt_bool(hal_values.get(LinkHealth.HAL_IO_ERROR)),
        servo_period_ms=float(hal_values.get("servo_period_ms", 1.0)),
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


def _wcs_name(g5x_index: int) -> str:
    """g5x_index 1 -> G54, 2 -> G55, ... (emcmodule.cc:752)."""
    if 1 <= g5x_index <= 9:
        return "G{}".format(53 + g5x_index)
    return "G54"


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


class MockAdapter(MachineAdapter):
    """In-memory machine. What this iteration renders from.

    It is not a toy: it enforces the same rules the real machine does, so the
    tests that matter (estop cannot be cleared in software, program start gated on
    homing) exercise real logic rather than a rendering coincidence.
    """

    def __init__(self, profile: MachineProfile, joint_config: Optional[Dict] = None):
        self.profile = profile
        from .configmodel import joint_config_from_profile

        self._joint_config = (
            joint_config
            if joint_config is not None
            else joint_config_from_profile(profile)
        )
        axes = [c["axis"] for c in self._joint_config.values()] or ["X", "Y", "Z"]

        self._estop = initial_estop(profile.has(Capability.ESTOP_INPUT))
        self._homing = HomingState(
            joints=tuple(
                JointHoming(
                    joint=jnum,
                    axis=cfg["axis"],
                    phase=HomingPhase.NOT_HOMED,
                    sequence=int(cfg.get("HOME_SEQUENCE", jnum)),
                    ignore_limits=bool(cfg.get("HOME_IGNORE_LIMITS", False)),
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
            servo_period_ms=1.0,
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
            AlarmEvent(
                _now(),
                "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok",
                "config", Severity.WARN,
            ),
        ]
        if profile.key == "test-rig":
            self._alarms.insert(
                0,
                AlarmEvent(
                    _now(),
                    "Y ekseni DIR kablosu arızalı — yön değişmiyor, hep öne gidiyor",
                    "gpio/AXIS 1", Severity.ERROR,
                ),
            )
        self._program_file: Optional[str] = None
        self._running = False
        self._progress = 0.0
        self._wcs = "G54"
        self._jog_step = 1.0
        self._feed = 100

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> MachineState:
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

    def send_mdi(self, command: str) -> MdiEntry:
        command = (command or "").strip()
        if not command:
            entry = MdiEntry("", False, "Boş komut.")
        elif self._estop.blocks_motion:
            entry = MdiEntry(command, False, "Makine devre dışı — MDI kabul edilmedi.")
        elif not self._homing.all_homed and _needs_homing(command):
            entry = MdiEntry(
                command, False,
                "Referans alınmadı — koordinatlı hareket kabul edilmiyor.",
            )
        else:
            entry = MdiEntry(command, True, "Kabul edildi.")
        self._mdi.append(entry)
        self._message = "MDI: {} — {}".format(command or "(boş)", entry.note)
        return entry

    def software_estop(self) -> None:
        self._estop = self._estop.software_estop()
        self._running = False
        self._message = "Yazılım acil stop. Sürücü gücü DEĞİŞMEDİ."
        self._alarms.insert(
            0, AlarmEvent(_now(), "Yazılım acil stop uygulandı", "task", Severity.ERROR)
        )

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
            self._message = "Yazılım acil stop sıfırlandı. Referans gerekli."

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

    def load_program(self, name: str) -> None:
        self._program_file = name
        self._progress = 0.0
        self._running = False
        self._message = "{} yüklendi.".format(name)

    def program_start(self) -> None:
        blocks = self.snapshot().start_blocks
        if blocks:
            self._message = "Başlatılamaz: {}".format(blocks[0])
            return
        if self._program_file is None:
            self._message = "Program yüklenmedi."
            return
        self._running = True
        self._message = "Program çalışıyor."

    def program_stop(self) -> None:
        self._running = False
        self._progress = 0.0
        self._message = "Program durduruldu."

    def set_jog_step(self, mm: float) -> None:
        self._jog_step = float(mm)

    def set_feed_override(self, pct: int) -> None:
        self._feed = int(pct)

    def set_wcs(self, wcs: str) -> None:
        self._wcs = wcs


def _needs_homing(command: str) -> bool:
    """Whether an MDI command implies coordinated motion."""
    head = command.strip().upper()
    return head.startswith(("G0", "G1", "G2", "G3", "G53")) and not head.startswith(
        ("G04", "G4 ")
    )


def _now() -> str:
    return datetime.datetime.now().strftime("%H:%M")


# ---------------------------------------------------------------------------
# Real
# ---------------------------------------------------------------------------


class LinuxCncAdapter(MachineAdapter):
    """The real adapter. Not exercised by tests - `linuxcnc` is not importable
    off-machine - but written out so the mapping is reviewable and so switching
    over is a constructor change rather than a rewrite.

    Usage on the machine:

        from bettercnc.adapter import LinuxCncAdapter
        from bettercnc.profiles import load_profile
        adapter = LinuxCncAdapter(load_profile("test-rig"))
    """

    def __init__(self, profile: MachineProfile):
        try:
            import linuxcnc  # type: ignore
        except ImportError as exc:  # pragma: no cover - needs a machine
            raise RuntimeError(
                "the `linuxcnc` Python module is not importable. It ships with "
                "LinuxCNC and is not on PyPI; run this on the machine, or use "
                "MockAdapter off-machine."
            ) from exc

        try:
            import hal  # type: ignore
        except ImportError:  # pragma: no cover - needs a machine
            hal = None

        self._linuxcnc = linuxcnc
        self._hal = hal
        self.profile = profile
        self._stat = linuxcnc.stat()
        self._command = linuxcnc.command()
        self._error = linuxcnc.error_channel()

        from .configmodel import joint_config_from_profile

        self._joint_config = joint_config_from_profile(profile)
        self._alarms: List[AlarmEvent] = []
        self._mdi: List[MdiEntry] = []

    # -- reads ---------------------------------------------------------------

    def snapshot(self) -> MachineState:  # pragma: no cover - needs a machine
        self._stat.poll()
        self._drain_errors()
        return map_linuxcnc_state(
            stat=self._stat,
            hal_values=self._read_hal(),
            profile=self.profile,
            joint_axis_map=[
                c["axis"] for _, c in sorted(self._joint_config.items())
            ],
            joint_config=self._joint_config,
            alarms=tuple(self._alarms),
            mdi_history=tuple(self._mdi),
        )

    def _read_hal(self) -> Dict[str, Any]:  # pragma: no cover - needs a machine
        """Read the link-health pins.

        `hal.get_value` is used rather than a component with connected pins so
        the UI can be a passive observer and never appear in the HAL graph.
        """
        out: Dict[str, Any] = {"connected": True, "servo_period_ms": 1.0}
        if self._hal is None:
            return {**out, "connected": False}
        for pin in HAL_LINK_PINS:
            try:
                out[pin] = self._hal.get_value(pin)
            except Exception:
                out[pin] = None
        return out

    def _drain_errors(self) -> None:  # pragma: no cover - needs a machine
        """Pull from error_channel into the alarm log, preserving attribution."""
        while True:
            got = self._error.poll()
            if not got:
                break
            kind, text = got
            sev = (
                Severity.ERROR
                if kind in (self._linuxcnc.NML_ERROR, self._linuxcnc.OPERATOR_ERROR)
                else Severity.INFO
            )
            self._alarms.insert(0, AlarmEvent(_now(), str(text), "linuxcnc", sev))

    # -- writes --------------------------------------------------------------

    def send_mdi(self, command: str) -> MdiEntry:  # pragma: no cover
        self._stat.poll()
        self._command.mode(self._linuxcnc.MODE_MDI)
        self._command.wait_complete()
        self._command.mdi(command)
        entry = MdiEntry(command, True, "Gönderildi.")
        self._mdi.append(entry)
        return entry

    def software_estop(self) -> None:  # pragma: no cover
        self._command.state(self._linuxcnc.STATE_ESTOP)

    def software_estop_reset(self) -> None:  # pragma: no cover
        """Clear the software estop and switch the machine on.

        Note what this does NOT do: it cannot restore drive power. If the
        hardware chain is open, LinuxCNC will re-enter estop immediately.
        """
        self._command.state(self._linuxcnc.STATE_ESTOP_RESET)
        self._command.state(self._linuxcnc.STATE_ON)

    def home_all(self) -> None:  # pragma: no cover
        self._command.mode(self._linuxcnc.MODE_MANUAL)
        self._command.home(-1)

    def jog(self, axis: str, direction: int) -> None:  # pragma: no cover
        idx = {"X": 0, "Y": 1, "Z": 2}[axis]
        self._command.mode(self._linuxcnc.MODE_MANUAL)
        self._command.jog(
            self._linuxcnc.JOG_INCREMENT, False, idx, direction * 1.0, 1.0
        )

    def program_start(self) -> None:  # pragma: no cover
        self._command.mode(self._linuxcnc.MODE_AUTO)
        self._command.auto(self._linuxcnc.AUTO_RUN, 0)

    def program_stop(self) -> None:  # pragma: no cover
        self._command.abort()
