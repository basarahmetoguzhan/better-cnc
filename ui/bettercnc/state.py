"""Machine state: the shape the UI renders from.

This mirrors what `linuxcnc.stat` plus a handful of HAL pins can tell us. It is
deliberately a plain data structure with no LinuxCNC import, so the whole view
layer is testable without a running machine. See adapter.py for the boundary.

Two models here carry real safety meaning and are worth reading carefully:

  EstopState  - separates HARDWARE drive power from LinuxCNC's SOFTWARE state.
  HomingState - a gate, not a decoration.
"""

from __future__ import annotations

import enum
from typing import Dict, List, NamedTuple, Optional, Tuple


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


class DrivePower(str, enum.Enum):
    """State of the physical drive power chain.

    The distinction this enum exists to enforce: emergency stop cuts drive power
    in HARDWARE. LinuxCNC only ever *learns* about it, through an input. Software
    cannot restore it, and on a machine with no estop input software cannot even
    observe it.

    docs/MERMER-GECIS.md:122 --
        "Acil stop surucu ve ig mili gucunu donanimsal kesmeli. LinuxCNC'nin
         haberi olmasi iyidir ama yazilim guvenlik fonksiyonu degildir.
         Pi/PC coksede makine durmali."
    """

    CUT = "cut"          # chain open, drives dead
    LIVE = "live"        # chain closed, drives powered
    UNKNOWN = "unknown"  # no hardware estop input: LinuxCNC cannot know

    @property
    def safe_to_move(self) -> bool:
        return self is DrivePower.LIVE


class EstopState(NamedTuple):
    """Hardware and software estop, held separately on purpose.

    The prototype modelled estop as one clickable boolean whose message claimed
    "surucu gucu donanimsal kesildi" - asserting a hardware effect from a software
    toggle. That conflation is the bug this type exists to prevent.
    """

    drive_power: DrivePower
    software_disabled: bool
    #: Whether this machine has an input that reports the chain state at all.
    hardware_input_present: bool

    # -- transitions ---------------------------------------------------------

    def software_reset(self) -> "EstopState":
        """Clear the SOFTWARE estop. Never touches drive power.

        Returns a new state. If drive power is CUT the reset does not take: with
        the chain open LinuxCNC would re-fault immediately, and showing "ready"
        would be a lie about a machine that cannot move.
        """
        if self.drive_power is DrivePower.CUT:
            return self  # refused - hardware wins
        return self._replace(software_disabled=False)

    def software_estop(self) -> "EstopState":
        """Assert the software estop. Also cannot change drive power."""
        return self._replace(software_disabled=True)

    def hardware_chain_opened(self) -> "EstopState":
        """The physical chain opened. Software follows; it does not lead."""
        return self._replace(drive_power=DrivePower.CUT, software_disabled=True)

    def hardware_chain_closed(self) -> "EstopState":
        """Chain restored. Software estop stays asserted until reset explicitly."""
        return self._replace(drive_power=DrivePower.LIVE)

    # -- derived -------------------------------------------------------------

    @property
    def drive_power_clearable_by_software(self) -> bool:
        """Always False. Present so the UI can state it rather than imply it."""
        return False

    @property
    def blocks_motion(self) -> bool:
        return self.software_disabled or self.drive_power is DrivePower.CUT

    @property
    def drive_power_label(self) -> str:
        return {
            DrivePower.CUT: "KESİLDİ (donanım)",
            DrivePower.LIVE: "VERİLİYOR",
            DrivePower.UNKNOWN: "BİLİNMİYOR",
        }[self.drive_power]

    @property
    def drive_power_detail(self) -> str:
        if self.drive_power is DrivePower.CUT:
            return "Yazılımdan geri alınamaz. Zincir fiziksel olarak kapatılmalı."
        if self.drive_power is DrivePower.UNKNOWN:
            return (
                "Bu makinede donanımsal acil stop girişi yok — LinuxCNC sürücü "
                "gücünün durumunu göremiyor. Yazılım 'hazır' derken sürücüler "
                "gerçekten enerjili mi bilinmiyor."
            )
        return "Zincir kapalı."

    @property
    def software_label(self) -> str:
        return "devre dışı" if self.software_disabled else "etkin"


def initial_estop(hardware_input_present: bool) -> EstopState:
    """Startup state: software disabled, as LinuxCNC actually starts.

    Drive power is UNKNOWN when there is no input to report it - which is the
    truthful default, not LIVE.
    """
    return EstopState(
        drive_power=DrivePower.UNKNOWN if not hardware_input_present else DrivePower.LIVE,
        software_disabled=True,
        hardware_input_present=hardware_input_present,
    )


# ---------------------------------------------------------------------------
# Homing
# ---------------------------------------------------------------------------


class HomingPhase(str, enum.Enum):
    NOT_HOMED = "not_homed"
    HOMING = "homing"
    HOMED = "homed"


class JointHoming(NamedTuple):
    joint: int
    axis: str
    phase: HomingPhase
    sequence: int          # [JOINT_n]HOME_SEQUENCE, from the INI
    ignore_limits: bool    # HOME_IGNORE_LIMITS
    search_vel: float      # sign gives the search direction

    @property
    def phase_label(self) -> str:
        return {
            HomingPhase.NOT_HOMED: "referans alınmadı",
            HomingPhase.HOMING: "referans alınıyor",
            HomingPhase.HOMED: "referanslı",
        }[self.phase]

    @property
    def direction_label(self) -> str:
        if self.search_vel == 0:
            return "arama yok"
        return "eksi yöne" if self.search_vel < 0 else "artı yöne"


class HomingState(NamedTuple):
    joints: Tuple[JointHoming, ...]

    @property
    def all_homed(self) -> bool:
        return bool(self.joints) and all(
            j.phase is HomingPhase.HOMED for j in self.joints
        )

    @property
    def any_homing(self) -> bool:
        return any(j.phase is HomingPhase.HOMING for j in self.joints)

    @property
    def unhomed(self) -> List[JointHoming]:
        return [j for j in self.joints if j.phase is not HomingPhase.HOMED]

    @property
    def required_order(self) -> List[JointHoming]:
        """Homing order from HOME_SEQUENCE, lowest absolute value first.

        Read from the INI rather than hardcoded, because the INI is what LinuxCNC
        actually obeys. See viewmodel.homing_order_note for the discrepancy this
        surfaced against the prose docs.
        """
        return sorted(self.joints, key=lambda j: (abs(j.sequence), j.joint))

    @property
    def order_label(self) -> str:
        return " → ".join(j.axis for j in self.required_order)

    def with_phase(self, axis: str, phase: HomingPhase) -> "HomingState":
        return self._replace(
            joints=tuple(
                j._replace(phase=phase) if j.axis == axis else j for j in self.joints
            )
        )

    def all_homed_state(self) -> "HomingState":
        return self._replace(
            joints=tuple(j._replace(phase=HomingPhase.HOMED) for j in self.joints)
        )


# ---------------------------------------------------------------------------
# Link health - the primary health indicators for an Ethernet card
# ---------------------------------------------------------------------------


class LinkHealth(NamedTuple):
    """Ethernet link health.

    These two counters are what commissioning actually stares at, and the
    prototype had neither. Pin names verified against the driver:

      hm2_7i92.0.packet-error-total  u32  hm2_eth.c:1558-1561
      hm2_7i92.0.watchdog.has_bit    bit  watchdog.c:140-144
      hm2_7i92.0.io_error            bit  hostmot2.c:1446
    """

    connected: bool
    packet_error_total: Optional[int]
    watchdog_has_bit: Optional[bool]
    io_error: Optional[bool]
    servo_period_ms: float

    HAL_PACKET_ERROR_TOTAL = "hm2_7i92.0.packet-error-total"
    HAL_WATCHDOG_HAS_BIT = "hm2_7i92.0.watchdog.has_bit"
    HAL_IO_ERROR = "hm2_7i92.0.io_error"

    @property
    def healthy(self) -> bool:
        return (
            self.connected
            and not self.watchdog_has_bit
            and not self.io_error
            and (self.packet_error_total or 0) == 0
        )

    @property
    def problems(self) -> List[str]:
        out: List[str] = []
        if not self.connected:
            out.append("Karta bağlantı yok.")
        if self.watchdog_has_bit:
            out.append(
                "Watchdog ısırdı — sürücü karta yazmayı bıraktı. Makine durdu."
            )
        if self.io_error:
            out.append("io_error — kalıcı iletişim hatası, elle sıfırlanmalı.")
        if (self.packet_error_total or 0) > 0:
            out.append(
                "{} paket kaybı sayıldı.".format(self.packet_error_total)
            )
        return out


# ---------------------------------------------------------------------------
# Alarms
# ---------------------------------------------------------------------------


class Severity(str, enum.Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class AlarmEvent(NamedTuple):
    """One log entry. `source` attribution is carried over from the prototype,
    which got this right: knowing whether a message came from gpio.020, hm2_eth,
    homing or the interpreter is most of the diagnostic value.
    """

    time: str
    text: str
    source: str
    severity: Severity


# ---------------------------------------------------------------------------
# Axis position / jog
# ---------------------------------------------------------------------------


class AxisPosition(NamedTuple):
    axis: str
    work: float
    machine: float
    homed: bool


class MdiEntry(NamedTuple):
    command: str
    accepted: bool
    note: str = ""


# ---------------------------------------------------------------------------
# Whole-machine snapshot
# ---------------------------------------------------------------------------


class MachineState(NamedTuple):
    estop: EstopState
    homing: HomingState
    link: LinkHealth
    positions: Tuple[AxisPosition, ...]
    alarms: Tuple[AlarmEvent, ...]
    mdi_history: Tuple[MdiEntry, ...]
    wcs: str
    jog_step_mm: float
    feed_override_pct: int
    program_file: Optional[str]
    program_running: bool
    program_progress: float
    message: str

    # -- the program-start gate ---------------------------------------------

    @property
    def start_blocks(self) -> List[str]:
        """Reasons program start is blocked. Empty list means startable.

        Order matters: the most fundamental reason comes first, so the UI can
        show `start_blocks[0]` as the headline.
        """
        blocks: List[str] = []
        if self.estop.drive_power is DrivePower.CUT:
            blocks.append("Sürücü gücü donanımsal kesildi.")
        if self.estop.software_disabled:
            blocks.append("Makine yazılımda devre dışı (acil stop).")
        if not self.homing.all_homed:
            axes = ", ".join(j.axis for j in self.homing.unhomed)
            blocks.append(
                "Referans alınmadı: {}. Sıra: {}.".format(
                    axes, self.homing.order_label
                )
            )
        if not self.link.connected:
            blocks.append("Karta bağlantı yok.")
        return blocks

    @property
    def can_start_program(self) -> bool:
        return not self.start_blocks and self.program_file is not None

    @property
    def can_jog(self) -> bool:
        """Jogging is allowed unhomed - that is how you find the switches."""
        return not self.estop.blocks_motion and self.link.connected

    @property
    def status_label(self) -> str:
        if self.estop.drive_power is DrivePower.CUT:
            return "ACİL STOP"
        if self.estop.software_disabled:
            return "DEVRE DIŞI"
        if self.homing.any_homing:
            return "REFERANS ALINIYOR"
        if self.program_running:
            return "ÇALIŞIYOR"
        if not self.homing.all_homed:
            return "REFERANS GEREKLİ"
        return "HAZIR"

    @property
    def status_severity(self) -> Severity:
        if self.estop.blocks_motion:
            return Severity.ERROR
        if not self.homing.all_homed or not self.link.healthy:
            return Severity.WARN
        return Severity.INFO
