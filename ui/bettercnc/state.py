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

    docs/MERMER-GECIS.md:139 --
        "Acil stop surucu ve ig mili gucunu donanimsal kesmeli. LinuxCNC'nin
         haberi olmasi iyidir ama yazilim guvenlik fonksiyonu degildir.
         Pi/PC cokse bile makine durmali."
    """

    CUT = "cut"          # chain open, drives dead
    LIVE = "live"        # chain closed, drives powered
    UNKNOWN = "unknown"  # no hardware estop input: LinuxCNC cannot know

    # Deliberately no "safe to move" helper here. Whether motion is allowed is
    # EstopState.blocks_motion, which lets UNKNOWN through (the test rig has no
    # input to report LIVE). A DrivePower-only rule cannot see software_disabled
    # and would disagree with it.


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


#: HomingState.order_label when no HOME_SEQUENCE is known (no INI, the
#: marble profile): never an invented order (P0 cleanup).
ORDER_UNKNOWN = "INI yok — sıra bilinmiyor"


class JointHoming(NamedTuple):
    joint: int
    axis: str
    phase: HomingPhase
    #: [JOINT_n]HOME_SEQUENCE, from the INI. None = not known (no INI).
    sequence: Optional[int]
    #: HOME_IGNORE_LIMITS. None = the INI value is unreadable
    #: (configmodel.AxisConfig.home_ignore_limits) or there is no INI;
    #: never coerce that to False.
    ignore_limits: Optional[bool]
    #: HOME_SEARCH_VEL; its sign gives the search direction. None = not
    #: known (no INI).
    search_vel: Optional[float]

    @property
    def phase_label(self) -> str:
        return {
            HomingPhase.NOT_HOMED: "referans alınmadı",
            HomingPhase.HOMING: "referans alınıyor",
            HomingPhase.HOMED: "referanslı",
        }[self.phase]

    @property
    def direction_label(self) -> str:
        if self.search_vel is None:
            return "arama yönü bilinmiyor (INI yok)"
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
    def order_known(self) -> bool:
        """Every joint's HOME_SEQUENCE is known (read from an INI)."""
        return bool(self.joints) and all(j.sequence is not None for j in self.joints)

    @property
    def required_order(self) -> List[JointHoming]:
        """Homing order from HOME_SEQUENCE, lowest absolute value first.

        Read from the INI rather than hardcoded, because the INI is what LinuxCNC
        actually obeys. See viewmodel.homing_order_note for the discrepancy this
        surfaced against the prose docs. Empty when the order is not known
        (order_known): never an order made up.
        """
        if not self.order_known:
            return []
        return sorted(self.joints, key=lambda j: (abs(j.sequence or 0), j.joint))

    @property
    def order_label(self) -> str:
        if self.joints and not self.order_known:
            return ORDER_UNKNOWN
        return " → ".join(j.axis for j in self.required_order)

    def homed_with(self, joint: JointHoming) -> Optional[List[JointHoming]]:
        """The joints a home of `joint` alone homes: itself, or with a
        negative HOME_SEQUENCE every joint sharing its abs(sequence)
        (do_home_one_joint, reference/linuxcnc/src/emc/motion/homing.c:
        265-281). None when its sequence is not known."""
        if joint.sequence is None:
            return None
        if joint.sequence >= 0:
            return [joint]
        return [j for j in self.joints
                if j.sequence is not None and abs(j.sequence) == abs(joint.sequence)]

    def with_phase(self, axis: str, phase: HomingPhase) -> "HomingState":
        return self._replace(
            joints=tuple(
                j._replace(phase=phase) if j.axis == axis else j for j in self.joints
            )
        )


# ---------------------------------------------------------------------------
# Link health - the primary health indicators for an Ethernet card
# ---------------------------------------------------------------------------


#: The step LinkHealth.problems gives after a lost board link, once the
#: cable and the board's supply are checked.
LINK_RESET_STEP = "sonra «Yazılım sıfırla»ya basın"


class LinkHealth(NamedTuple):
    """Ethernet link health.

    These two counters are what commissioning actually stares at, and the
    prototype had neither. Pin names verified against the driver:

      hm2_7i92.0.packet-error-total  u32  hm2_eth.c:1558-1561
      hm2_7i92.0.watchdog.has_bit    bit  watchdog.c:140-144
      hm2_7i92.0.io_error            bit  hostmot2.c:1446
    """

    connected: bool
    #: None = the pin could not be read. That is unknown, never zero.
    packet_error_total: Optional[int]
    watchdog_has_bit: Optional[bool]
    io_error: Optional[bool]
    #: [EMCMOT]SERVO_PERIOD in ms. None = the INI does not give one; the UI
    #: shows "—" rather than a default.
    servo_period_ms: Optional[float]

    HAL_PACKET_ERROR_TOTAL = "hm2_7i92.0.packet-error-total"
    HAL_WATCHDOG_HAS_BIT = "hm2_7i92.0.watchdog.has_bit"
    HAL_IO_ERROR = "hm2_7i92.0.io_error"

    @property
    def unreadable(self) -> List[str]:
        """The indicators whose pin could not be read, in the operator's
        words (the HAL names are in the link rows, viewmodel.link_panel)."""
        out: List[str] = []
        if self.packet_error_total is None:
            out.append("kayıp paket sayısı")
        if self.watchdog_has_bit is None:
            out.append("kart bağlantısı")
        if self.io_error is None:
            out.append("iletişim hatası")
        return out

    @property
    def healthy(self) -> bool:
        """Connected, every counter read, and none showing a fault.

        A counter that could not be read is unknown, not clean: reporting
        such a link as healthy would present an unmeasured value as measured.
        """
        return (
            self.connected
            and not self.unreadable
            and not self.watchdog_has_bit
            and not self.io_error
            and self.packet_error_total == 0
        )

    @property
    def problems(self) -> List[str]:
        """Each problem in the operator's words, with what to do.

        Not "watchdog bit" or "io_error": the person at the machine needs a
        cause they can check and the button to press (2026-10-20). Both
        faults stop the machine - the bit through the HAL estop latch, the
        driver's io_error through the UI, which sends ESTOP when it sees it
        with the machine ON (LinuxCncAdapter._stop_on_link_fault; on the real
        driver a cable pull raises io_error alone) - and both are cleared by
        «Yazılım sıfırla» (_clear_link_faults); the reset also unhomes.
        """
        return self.problems_with(LINK_RESET_STEP)

    def problems_with(self, reset_step: str) -> List[str]:
        """problems, with `reset_step` as the step after checking the cable.
        Observer mode (app.py --observe) has no «Yazılım sıfırla» to press
        (viewmodel.OBSERVE_LINK_RESET_STEP)."""
        out: List[str] = []
        if not self.connected:
            out.append("Kart bağlantısının durumu okunamıyor — kartla iletişim bilinmiyor.")
        elif self.unreadable:
            # Without a connection every counter is unreadable, and the line
            # above already says why.
            out.append(
                "Okunamayan gösterge: {} — kartla iletişimin sağlığı bilinmiyor.".format(
                    ", ".join(self.unreadable)
                )
            )
        if self.watchdog_has_bit or self.io_error:
            out.append(
                "Kartla bağlantı koptu — makine durduruldu. Ethernet kablosunu ve "
                "kartın beslemesini kontrol edin, {}. "
                "Ardından referansı yeniden alın.".format(reset_step)
            )
        if (self.packet_error_total or 0) > 0:
            out.append(
                "Kartla iletişimde {} paket kayboldu. Sayı artmaya devam ederse "
                "Ethernet kablosunu ve bağlantısını kontrol edin.".format(self.packet_error_total)
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
    #: The work (program) coordinate, as AXIS's DRO shows it: the machine
    #: position minus the tool offset and the G5x origin, turned by the XY
    #: rotation, minus the G92 offset (adapter.work_coordinates).
    work: float
    #: The machine position motion reports back (stat.actual_position).
    machine: float
    homed: bool
    #: The commanded machine position (stat.position, motion's carte_pos_cmd).
    #: While a move runs it is ahead of `machine`; at rest the two agree.
    #: None = not read (MockAdapter).
    commanded: Optional[float] = None


class HardLimit(NamedTuple):
    """One joint's hard-limit flags as motion sees them
    (stat.joint[n]["min_hard_limit"] / ["max_hard_limit"]).

    Motion reads the limit-switch inputs every servo cycle, enabled or not,
    so these are live with the machine off as well - but no fresher than the
    board's input pins: while the board link is down (hm2 io_error) the
    driver stops refreshing its GPIO pins and they keep their last value
    (reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c:100-107).
    True = that end's switch input is active; None = not read. An end with
    no switch reads False, its input unconnected (the rig's Z bottom,
    profile.missing_switches). A home switch shared with a limit input
    reads True here while its joint homes on it (HOME_IGNORE_LIMITS).
    """

    joint: int
    axis: str
    min: Optional[bool]
    max: Optional[bool]


#: MdiEntry.status: refused here before anything was sent; sent, its result
#: not known yet; sent and then refused or cut short (LinuxCNC's error, a
#: stop, the machine going off); sent and completed.
MDI_REFUSED = "refused"
MDI_SENT = "sent"
MDI_REJECTED = "rejected"
MDI_DONE = "done"


class MdiEntry(NamedTuple):
    command: str
    #: The UI accepted the line and sent it (whatever LinuxCNC made of it).
    accepted: bool
    note: str = ""
    #: One of the MDI_* values above; "" from code that predates them
    #: (viewmodel.mdi_panel then derives one from `accepted`).
    status: str = ""


class LastError(NamedTuple):
    """The error box (render.render_msgband): the latest ERROR an adapter
    said, kept until the operator presses «Tamam» (adapter.ack_error). A
    later INFO or WARN message does not clear it, nor does a later action.

    `id` grows with every error kept, so «Tamam» clears only the one the
    operator saw - its id rides on the button - and never one that arrived
    after the screen was drawn.
    """

    id: int
    #: Local time it was said, HH:MM:SS.
    time: str
    #: What the operator reads: Turkish (messages.operator_text for
    #: LinuxCNC's texts).
    text: str
    #: LinuxCNC's own text; "" for an error of this UI's own (the software
    #: estop, a lost board link, a stop it did not cause).
    raw: str = ""
    #: "linuxcnc" or "ui".
    source: str = "ui"


# ---------------------------------------------------------------------------
# Whole-machine snapshot
# ---------------------------------------------------------------------------


#: The start block, and the reason motion controls are off, while LinuxCNC's
#: status cannot be read (MachineState.status_known is False).
STATUS_UNKNOWN_BLOCK = "Makine durumu bilinmiyor — LinuxCNC durumu okunamıyor."

# MachineState.jog_block's keys: why no jog may go now, whatever the axis,
# the direction and the step (plan WP06). motionrules words each one for a
# refused click (JOG_BLOCK_TEXTS), viewmodel for the jog card
# (JOG_OFF_REASONS).

#: LinuxCNC's status cannot be read.
JOG_BLOCK_UNKNOWN = "unknown"
#: The machine is not ON (an estop, or the drive power cut).
JOG_BLOCK_OFF = "off"
#: A joint is homing: motion refuses a jog then, and task's abort after
#: that refusal cuts the homing short (motionrules.JOG_HOMING).
JOG_BLOCK_HOMING = "homing"
#: Whether a program or an MDI line is paused or running was not read.
JOG_BLOCK_UNREAD = "unread"
#: A program (or an MDI line) is paused.
JOG_BLOCK_PAUSED = "paused"
#: A program or an MDI line runs.
JOG_BLOCK_RUNNING = "running"

#: MachineState.run_command: the run command this UI sent last for the run
#: in progress (P0 cleanup) - «Başlat», «Duraklat», «Devam», «Adım».
RUN_CMD_START = "start"
RUN_CMD_PAUSE = "pause"
RUN_CMD_RESUME = "resume"
RUN_CMD_STEP = "step"


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
    #: A program runs, unpaused: the task in AUTO, the interpreter not idle
    #: and nothing paused (program_paused). Not an MDI line (mdi_running),
    #: and not a paused program (plan WP09; until then a paused program read
    #: as not running and an MDI line with a file loaded as running).
    program_running: bool
    #: How far motion is in the loaded file, 0.0 to 1.0, while a program
    #: runs or is paused (programs.progress); 0.0 otherwise.
    program_progress: float
    message: str
    #: False when the adapter could not read LinuxCNC's status at all
    #: (LinuxCncAdapter._status_unreadable). The fields LinuxCNC's status
    #: would have filled (positions, wcs, feed_override_pct, the homing
    #: phases, software_disabled, the program fields) are then placeholders,
    #: not readings, and the view shows "—" or "bilinmiyor" for them; the
    #: motion fields below are at their "not read" values instead
    #: (as_status_unknown). Drive power and the link come from HAL pins,
    #: read separately, so they keep their own meaning.
    status_known: bool = True
    #: The switch inputs live, as (HAL pin, value) in the profile's input
    #: order: the pin each profile input names (profiles.DigitalInput.hal_pin
    #: - the one the HAL nets take, so TRUE means the switch is pressed),
    #: None for a pin that could not be read. Empty when the adapter reads no
    #: inputs at all (MockAdapter): nothing is known, which the view must not
    #: show as "free". HAL pins, like the link: they keep their meaning while
    #: LinuxCNC's status is unknown.
    input_values: Tuple[Tuple[str, Optional[bool]], ...] = ()
    #: The step generators' timing parameters live, as (HAL name, ns), e.g.
    #: ("hm2_7i92.0.stepgen.00.steplen", 10000); None for one that could not
    #: be read. What the driver runs, which may differ from the INI after a
    #: halcmd setp. Empty when not read (MockAdapter, or an INI that gives no
    #: board or no num_stepgens).
    stepgen_timing: Tuple[Tuple[str, Optional[int]], ...] = ()

    # -- motion and interpreter, as LinuxCNC's status reports them ----------
    #
    # Read by LinuxCncAdapter (adapter.map_linuxcnc_state). The jog gates
    # read them (jog_block, can_jog; motionrules.jog_refusal, plan WP06), as
    # the limit recovery reads the flags (plan WP05). Each has a "not read"
    # value - () or None - which LinuxCncAdapter reports while LinuxCNC's
    # status is unknown (as_status_unknown), never a placeholder that reads
    # like a reading; a jog gate takes "not read" as a refusal. MockAdapter
    # reports what its own model defines - nothing moving, paused or running
    # an MDI line, the modes its machine is in - and "not read" for the rest
    # (the flags, the override, the program line, G92).

    #: Motion's limit-switch flags, one HardLimit per joint in joint order.
    hard_limits: Tuple[HardLimit, ...] = ()
    #: Whether motion's limit override is on (stat.joint[0]["override_limits"]:
    #: one value for every joint). None = not read.
    override_limits: Optional[bool] = None
    #: Whether the machine is moving now: ON, and motion's velocity above
    #: zero or, in free (joint) mode, motion not in position. None = not read.
    moving: Optional[bool] = None
    #: Task mode: "manual", "auto" or "mdi". None = not read.
    task_mode: Optional[str] = None
    #: Interpreter: "idle", "reading", "paused" or "waiting". None = not read.
    interp_state: Optional[str] = None
    #: Motion mode: "free" (joint), "coord" or "teleop". None = not read.
    motion_mode: Optional[str] = None
    #: A program or MDI command is paused: motion paused (stat.paused) or the
    #: interpreter PAUSED. None = not read.
    program_paused: Optional[bool] = None
    #: An MDI command is running (or paused): MDI mode with the interpreter
    #: not idle. None = not read.
    mdi_running: Optional[bool] = None
    #: The program line motion is executing (stat.motion_line; 0 before any).
    #: None = not read.
    program_line: Optional[int] = None
    #: A G92 offset is in effect (any component of stat.g92_offset not zero).
    #: The interpreter restores G92 from the parameter file at startup unless
    #: [RS274NGC]DISABLE_G92_PERSISTENCE is set (rs274ngc_pre.cc:1114-1132;
    #: the rig's INI does not set it), so this can be on from the start.
    #: None = not read.
    g92_active: Optional[bool] = None

    # -- the operator's message band (render.render_msgband) ----------------
    #
    # The adapter's own words, not readings: kept while the status is
    # unknown (an outage is itself one of them).

    #: How `message` reads: INFO grey, WARN yellow (a refusal), ERROR red.
    message_severity: Severity = Severity.INFO
    #: The error box: the latest ERROR, until the operator acknowledges it.
    #: None = nothing to show.
    last_error: Optional[LastError] = None
    #: The run command this UI sent last for the program or MDI line in
    #: progress (RUN_CMD_*), None when it sent none - observer mode, a run
    #: started by AXIS, or nothing running. The UI's own, like last_error.
    #: Read by single_step_moving (P0 cleanup).
    run_command: Optional[str] = None

    def as_status_unknown(self) -> "MachineState":
        """This state, marked as LinuxCNC's status unreadable
        (LinuxCncAdapter._status_unreadable): status_known False, no
        positions, and every motion field above at its "not read" value.

        The older fields LinuxCNC's status fills (wcs, feed_override_pct,
        the homing phases, software_disabled, the program fields) keep their
        placeholders; the view does not show them while status_known is
        False. A field added later with a "not read" value belongs in this
        list too, unless it comes from HAL like input_values and
        stepgen_timing, or is the UI's own like last_error
        (test_state_plumbing pins that).
        """
        return self._replace(
            status_known=False,
            positions=(),
            hard_limits=(),
            override_limits=None,
            moving=None,
            task_mode=None,
            interp_state=None,
            motion_mode=None,
            program_paused=None,
            mdi_running=None,
            program_line=None,
            g92_active=None,
        )

    def input_value(self, hal_pin: str) -> Optional[bool]:
        """input_values for one pin; None when unread or unreadable."""
        for pin, value in self.input_values:
            if pin == hal_pin:
                return value
        return None

    # -- the program-start gate ---------------------------------------------

    @property
    def start_blocks(self) -> List[str]:
        """Reasons program start is blocked. Empty list means startable.

        Order matters: the most fundamental reason comes first, so the UI can
        show `start_blocks[0]` as the headline.

        While the status is unknown, the software-estop and unhomed reasons
        are left out: they would be read off placeholders.
        """
        blocks: List[str] = []
        if self.estop.drive_power is DrivePower.CUT:
            blocks.append("Sürücü gücü donanımsal kesildi.")
        if not self.status_known:
            blocks.append(STATUS_UNKNOWN_BLOCK)
        elif self.estop.software_disabled:
            blocks.append("Makine yazılımda devre dışı (acil stop).")
        if not self.homing.joints:
            # all_homed is False for an empty set, so start stays blocked; the
            # message must say why rather than list no axes.
            blocks.append(
                "Referans durumu bilinmiyor: config'de eklem ([JOINT_n]) tanımlı değil."
            )
        elif self.status_known and not self.homing.all_homed:
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
        """«Başlat» may go: nothing blocks the machine (start_blocks), a
        file is loaded, and nothing runs or is paused - read, not assumed
        (plan WP09; programs.start_busy words why). A paused program is
        resumed with «Devam», never started again."""
        return (
            not self.start_blocks
            and self.program_file is not None
            and self.program_paused is False
            and self.mdi_running is False
            and self.interp_state is not None
            and not self.program_busy
        )

    @property
    def motion_enabled(self) -> bool:
        """The machine takes motion commands at all: LinuxCNC's status read,
        the machine ON (no estop, drive power not cut) and the board link
        read. The MDI panel goes by this alone - LinuxCNC queues an MDI line
        behind a running one (reference/linuxcnc/src/emc/task/
        emctaskmain.cc:1422-1439), and mdi.mdi_refusal has the rest; a jog
        has gates of its own (can_jog).

        Not while the status is unknown: whether the machine is enabled is
        then itself unknown."""
        return (
            self.status_known
            and not self.estop.blocks_motion
            and self.link.connected
        )

    @property
    def program_busy(self) -> bool:
        """A program or an MDI line runs or is paused: program_running, or
        AUTO or MDI with the interpreter not idle - axis.py's running()
        (reference/linuxcnc/src/emc/usr_intf/axis/scripts/axis.py:962-965),
        which LinuxCncAdapter._running checks on a stat."""
        return bool(self.program_running) or (
            self.task_mode in ("auto", "mdi") and self.interp_state not in (None, "idle"))

    @property
    def jog_block(self) -> str:
        """Why no jog may go now, whatever the axis, the direction and the
        step (plan WP06): one of the JOG_BLOCK_* keys, "" when one may. The
        status unknown or the machine off; a joint homing; a program or MDI
        line paused or running - each read, never assumed: a "not read"
        program_paused or mdi_running is JOG_BLOCK_UNREAD. Moving, the
        switches and the step's room are the click's own questions
        (motionrules.jog_refusal), not the jog card's."""
        if not self.status_known:
            return JOG_BLOCK_UNKNOWN
        if self.estop.blocks_motion:
            return JOG_BLOCK_OFF
        if self.homing.any_homing:
            return JOG_BLOCK_HOMING
        if self.program_paused is None or self.mdi_running is None:
            return JOG_BLOCK_UNREAD
        if self.program_paused:
            return JOG_BLOCK_PAUSED
        if self.program_busy or self.mdi_running:
            return JOG_BLOCK_RUNNING
        return ""

    @property
    def can_jog(self) -> bool:
        """The jog card is live: jogging is allowed unhomed - that is how
        you find the switches - but not while a joint homes or a program or
        MDI line runs or is paused (jog_block), and, as every motion
        control, only with the machine enabled and the board link read
        (motion_enabled). Whether one click may go - the machine not
        moving, the switch, the step's room - is motionrules.jog_refusal's.

        Not while the status is unknown: whether the machine is enabled is
        then itself unknown."""
        return self.motion_enabled and not self.jog_block

    @property
    def single_step_moving(self) -> bool:
        """Motion moves under a pause because of a single step: a step's
        motion runs with motion's paused flag set again (EMCMOT_STEP,
        reference/linuxcnc/src/emc/motion/command.c:1200-1212; AXIS reads
        stat.paused the same way). Right after «Duraklat» the flag is set
        too while tpPause still decelerates (command.c:1168-1175), so
        paused-and-moving is a step only when the last run command this UI
        sent was «Adım» (run_command). With none sent by this UI - observer
        mode, AXIS driving - paused-and-moving reads as a step, as before
        (P0 cleanup)."""
        return bool(self.program_paused and self.moving
                    and self.run_command in (None, RUN_CMD_STEP))

    @property
    def pause_left_at_end(self) -> bool:
        """Motion's paused flag still set with the interpreter idle: a
        program stepped to its very end with «Adım» alone. EMCMOT_STEP sets
        paused again after each step (command.c:1200-1212) and nothing at
        the program's end resumes motion (readahead_waiting, reference/
        linuxcnc/src/emc/task/emctaskmain.cc:713-737); in sim/ (LinuxCNC
        2.9.9, 2026-11-14) stat.paused stayed True, interp_state IDLE,
        exec_state DONE, until an abort (command.c:530). «Devam» then makes
        the interpreter read past the end (an error), «Başlat» runs the
        program with the flag still set and «Adım» starts it again, so the
        UI offers only «Durdur» (programs.PAUSED_AT_END; P0 cleanup)."""
        return bool(self.program_paused) and self.interp_state == "idle"

    @property
    def status_label(self) -> str:
        """The header's word. A pause reads DURAKLATILDI - never HAZIR -
        unless a single step's motion moves under it (single_step_moving),
        which reads TEK ADIM; a pause left at the program's end after
        stepping reads PROGRAM SONU · DURDUR (pause_left_at_end). A program
        running reads ÇALIŞIYOR with motion's line once there is one
        (program_line); an MDI line reads MDI YÜRÜTÜLÜYOR, so it never reads
        as a program (plan WP09)."""
        if self.estop.drive_power is DrivePower.CUT:
            return "ACİL STOP"
        if not self.status_known:
            return "DURUM BİLİNMİYOR"
        if self.estop.software_disabled:
            return "DEVRE DIŞI"
        if self.homing.any_homing:
            return "REFERANS ALINIYOR"
        if self.pause_left_at_end:
            return "PROGRAM SONU · DURDUR"
        if self.program_paused:
            return "TEK ADIM" if self.single_step_moving else "DURAKLATILDI"
        if self.program_running:
            if self.program_line:
                return "ÇALIŞIYOR · satır {}".format(self.program_line)
            return "ÇALIŞIYOR"
        if self.mdi_running:
            return "MDI YÜRÜTÜLÜYOR"
        if not self.homing.all_homed:
            return "REFERANS GEREKLİ"
        return "HAZIR"

    @property
    def status_severity(self) -> Severity:
        """ERROR for the machine off or its status unknown; WARN for what
        needs the operator - unhomed, the link unhealthy, a program paused;
        INFO otherwise."""
        if self.estop.blocks_motion or not self.status_known:
            return Severity.ERROR
        if not self.homing.all_homed or not self.link.healthy or self.program_paused:
            return Severity.WARN
        return Severity.INFO
