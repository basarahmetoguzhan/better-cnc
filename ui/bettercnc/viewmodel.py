"""Pure view models: (profile, config, state) -> plain dicts per screen.

No HTML here and no LinuxCNC here. This is the layer the tests hammer, because
every rule worth protecting is expressible as an assertion about these dicts.

The one rule that shapes the whole module:

    Capability panels are built by iterating CAPABILITY_CATALOGUE, never by
    iterating what a profile declares.

That is why a capability cannot be hidden by omission - the loop does not consult
the profile for the list of things to show, only for their state.
"""

from __future__ import annotations

import math
import os
import time
from typing import Any, Dict, List, Optional

from . import motionrules, programs
from .capabilities import (
    CAPABILITY_CATALOGUE,
    CAPABILITY_META,
    Capability,
    Presence,
)
from .configmodel import NO_CONFIG, UNMEASURED, MachineConfig
from .mdi import MDI_WHILE_PAUSED
from .profiles import MachineProfile
from .state import (
    JOG_BLOCK_HOMING,
    JOG_BLOCK_OFF,
    JOG_BLOCK_PAUSED,
    JOG_BLOCK_RUNNING,
    JOG_BLOCK_UNKNOWN,
    JOG_BLOCK_UNREAD,
    MDI_REFUSED,
    MDI_SENT,
    DrivePower,
    HomingPhase,
    LastError,
    MachineState,
    MdiEntry,
    Severity,
)

SCREENS = (
    ("kontrol", "Kontrol", "Çalışma ekranı"),
    ("program", "Program", "G-code dosyaları ve önizleme"),
    ("probing", "Probing", "Yüzey, köşe ve merkez bulma"),
    ("takimlar", "Takımlar", "Takım tablosu ve boy telafisi"),
    ("alarmlar", "Alarmlar", "Giriş durumları ve olay kaydı"),
    ("makine", "Makine", "Yapılandırma ve kart bilgisi"),
)

#: TB6560 optocoupler step-frequency ceiling. docs/09-tb6560-drivers.md:77
TB6560_STEP_CEILING_HZ = 15000.0

#: The homing order the prose docs state, and where they state it.
#:
#: This is a declared copy of what the documentation claims - the UI does not
#: parse Turkish prose at runtime to find it. That means the constant can drift
#: from the docs, so tests/test_homing_gate.py reads both files and asserts this
#: value still appears in them. Same principle as the INI numbers: a value held
#: in two places without a test tying them together is a bug waiting to happen.
#:
#: Corrected 2026-08-23: both docs previously said "Z → X → Y", which
#: disagreed with the INI. The INI was right and the docs now agree with it.
DOCUMENTED_HOME_ORDER = "Z → Y → X"
DOCUMENTED_HOME_ORDER_SOURCES = ("docs/DURUM.md", "docs/MERMER-GECIS.md")

#: Observer mode (app.py --observe): the UI runs beside AXIS, which runs the
#: machine, and sends nothing but the software estop. Every screen opens
#: with the banner, and every other control is off with OBSERVE_REASON.
OBSERVE_BANNER = "SALT İZLEME — makine AXIS'ten kullanılıyor"
OBSERVE_DETAIL = (
    "Bu ekrandan yalnız acil stop gider: sağdaki «ACİL STOP» düğmesiyle (ya da "
    "Pause tuşuyla), bir de kart bağlantısı koparsa kendiliğinden. Diğer "
    "düğmeler kapalı."
)
OBSERVE_REASON = "Salt izleme — makine AXIS'ten kullanılıyor, bu ekrandan komut gönderilmez."
#: LinkHealth.problems_with's step in observer mode, which has no «Yazılım
#: sıfırla» to press (adapter.OBSERVE_LINK_ACTION gives the reason).
OBSERVE_LINK_RESET_STEP = (
    "sonra LinuxCNC'yi yeniden başlatın (salt izlemede bu ekran kart "
    "bağlantısını sıfırlayamaz)"
)

#: The estop panel's safety note (estop_panel). Without a hardware estop
#: input - the test rig: configs/test-rig/test-rig.hal, "ESTOP CHAIN - GUI
#: ONLY" - the screen's estop is software alone, and saying it cuts power
#: would be false.
ESTOP_NOTE_SOFTWARE_ONLY = (
    "Bu ekrandaki ACİL STOP yalnız yazılımdır: hareketi durdurur, motor "
    "gücünü KESMEZ. Gerçek durdurma: güç anahtarı."
)
#: ...and on a machine whose profile wires one (Capability.ESTOP_INPUT).
ESTOP_NOTE_WIRED = (
    "Acil stop sürücü gücünü DONANIMSAL keser. LinuxCNC sadece haberdar "
    "olur; yazılım güvenlik fonksiyonu değildir."
)

# -- the stop rail (render.render_rail) ---------------------------------------
STOP_RAIL_ESTOP_LABEL = "ACİL STOP"
STOP_RAIL_STOP_LABEL = "DURDUR"
#: Why DURDUR is off in observer mode, written under it: only the estop
#: leaves an observer (app.Session.act).
OBSERVE_STOP_REASON = (
    "Salt izleme — Durdur AXIS'ten verilir; buradan yalnız ACİL STOP gider."
)
#: The keys app.js binds (its keydown handler): Esc as in AXIS (axis.py:3116,
#: task_stop), Pause for the estop - the kiosk keymap leaves both alone
#: (deploy/files/xkb/symbols/bettercnc) but kills the F-keys, AXIS's F1.
STOP_RAIL_HINT = "Esc = Durdur · Pause = Acil stop"
STOP_RAIL_HINT_OBSERVE = "Pause = Acil stop"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


def capability_panel(profile: MachineProfile, key: Capability) -> Dict[str, Any]:
    """One capability, always rendered.

    `enabled` False means: show the control, greyed out, with `reason` visible.
    It never means: omit it.
    """
    meta = CAPABILITY_META[key]
    st = profile.capability(key)
    return {
        "key": key.value,
        "label": meta.label,
        "blurb": meta.blurb,
        "screen": meta.screen,
        "presence": st.presence.value,
        "enabled": st.usable,
        "disabled": st.disabled,
        "hidden": False,  # invariant: never hidden. Asserted in tests.
        "reason": st.reason,
        "requirement": st.requirement,
        "source": st.source,
        "badge": {
            Presence.PRESENT: "var",
            Presence.ABSENT: "yok",
            Presence.TODO: "BELİRSİZ",
        }[st.presence],
        "severity": {
            Presence.PRESENT: Severity.INFO.value,
            Presence.ABSENT: Severity.INFO.value,
            Presence.TODO: Severity.WARN.value,
        }[st.presence],
    }


def capability_panels(profile: MachineProfile) -> List[Dict[str, Any]]:
    """Every catalogued capability, in catalogue order."""
    return [capability_panel(profile, meta.key) for meta in CAPABILITY_CATALOGUE]


def capabilities_for_screen(profile: MachineProfile, screen: str) -> List[Dict[str, Any]]:
    return [p for p in capability_panels(profile) if p["screen"] == screen]


# ---------------------------------------------------------------------------
# Header / chrome
# ---------------------------------------------------------------------------


def _reading_severity(value: Any, fault: bool) -> str:
    """WARN for a value that could not be read: unknown is neither a fault
    (ERROR) nor a clean reading (INFO)."""
    if value is None:
        return Severity.WARN.value
    return Severity.ERROR.value if fault else Severity.INFO.value


def _link_problems(state: MachineState, observe: bool) -> List[str]:
    """LinkHealth.problems, with observer mode's step after a lost link."""
    if observe:
        return state.link.problems_with(OBSERVE_LINK_RESET_STEP)
    return state.link.problems


def link_panel(
    state: MachineState, config: MachineConfig, observe: bool = False
) -> Dict[str, Any]:
    """Connection health.

    Carries the two indicators the prototype omitted and commissioning actually
    needs: cumulative packet loss and the watchdog bit. A pin that could not
    be read shows "—" in WARN, and the servo period shows "—" when the INI
    has none.
    """
    link = state.link
    rows = [
        {
            "label": "Kart",
            "value": config.board_ip or "—",
            "severity": Severity.INFO.value if link.connected else Severity.ERROR.value,
            "hal": "",
        },
        {
            "label": "Kayıp paket",
            "value": "—" if link.packet_error_total is None else str(link.packet_error_total),
            "severity": _reading_severity(
                link.packet_error_total, (link.packet_error_total or 0) != 0
            ),
            "hal": link.HAL_PACKET_ERROR_TOTAL,
        },
        {
            # The hm2 watchdog, named for what it tells the operator; the HAL
            # name stays in "hal" for maintenance.
            "label": "Kart bağlantısı",
            "value": (
                "—"
                if link.watchdog_has_bit is None
                else ("KOPTU" if link.watchdog_has_bit else "iyi")
            ),
            "severity": _reading_severity(link.watchdog_has_bit, bool(link.watchdog_has_bit)),
            "hal": link.HAL_WATCHDOG_HAS_BIT,
        },
        {
            "label": "İletişim hatası",
            "value": (
                "—" if link.io_error is None else ("VAR" if link.io_error else "yok")
            ),
            "severity": _reading_severity(link.io_error, bool(link.io_error)),
            "hal": link.HAL_IO_ERROR,
        },
        {
            "label": "Servo döngü",
            "value": (
                "—" if link.servo_period_ms is None
                else "{:g} ms".format(link.servo_period_ms)
            ),
            "severity": _reading_severity(link.servo_period_ms, False),
            "hal": "",
        },
    ]
    return {
        "rows": rows,
        "healthy": link.healthy,
        "problems": _link_problems(state, observe),
    }


def estop_panel(state: MachineState, observe: bool = False) -> Dict[str, Any]:
    """Two distinct states, never merged.

    `drive_power` is hardware. `software` is LinuxCNC. The reset button may only
    ever affect the second, and `reset_clears_drive_power` is exposed as False so
    the UI can state that rather than leave it to be inferred.

    While LinuxCNC's status is unknown the software row says "bilinmiyor":
    the software state is LinuxCNC's, and it could not be read. Drive power
    comes from a HAL pin and keeps its own reading.

    In observer mode the reset is off too (the machine is reset in AXIS);
    the estop button never is. `reset_blocked_reason` stays the hardware
    one, which the screen shows as a banner; `reset_disabled_reason` is
    whichever reason the button gives.

    The estop button itself is not here: it is on the stop rail
    (stop_rail), on every screen. `note` says what it does: software alone
    unless the profile wires a hardware estop input.
    """
    e = state.estop
    if not state.status_known:
        software_label = "bilinmiyor"
        software_severity = Severity.WARN.value
    else:
        software_label = e.software_label
        software_severity = (
            Severity.ERROR.value if e.software_disabled else Severity.INFO.value
        )
    blocked = (
        "Sürücü gücü donanımsal kesik — yazılım sıfırlaması etkisiz."
        if e.drive_power is DrivePower.CUT
        else ""
    )
    return {
        "drive_power": e.drive_power.value,
        "drive_power_label": e.drive_power_label,
        "drive_power_detail": e.drive_power_detail,
        "drive_power_severity": (
            Severity.ERROR.value
            if e.drive_power is DrivePower.CUT
            else Severity.WARN.value
            if e.drive_power is DrivePower.UNKNOWN
            else Severity.INFO.value
        ),
        "hardware_input_present": e.hardware_input_present,
        "software_disabled": e.software_disabled if state.status_known else None,
        "software_label": software_label,
        "software_severity": software_severity,
        # The whole point of the panel:
        "reset_clears_drive_power": False,
        "reset_enabled": e.drive_power is not DrivePower.CUT and not observe,
        "reset_blocked_reason": blocked,
        "reset_disabled_reason": blocked or (OBSERVE_REASON if observe else ""),
        "note": ESTOP_NOTE_WIRED if e.hardware_input_present else ESTOP_NOTE_SOFTWARE_ONLY,
    }


def stop_rail(observe: bool = False) -> Dict[str, Any]:
    """The stop rail: ACİL STOP and DURDUR, on every screen
    (render.render_rail), fired on the press (app.js).

    The estop is never off: not in observer mode, not while LinuxCNC's
    status is unknown - it reads nothing. DURDUR is off only in observer
    mode (app.Session.act drops it there), with the reason as text: the
    kiosk removes title tooltips. The page renders the rail once, so
    nothing here may follow the machine's state; observer mode is fixed
    for the server's life."""
    return {
        "estop_label": STOP_RAIL_ESTOP_LABEL,
        "stop_label": STOP_RAIL_STOP_LABEL,
        "stop_enabled": not observe,
        "stop_disabled_reason": OBSERVE_STOP_REASON if observe else "",
        "hint": STOP_RAIL_HINT_OBSERVE if observe else STOP_RAIL_HINT,
    }


#: The Kontrol limit card (limit_panel): what one press does.
LIMIT_CARD_NOTE = (
    "Tek basış: limit korumasını yalnız bu hareket için kaldırır, makineyi açar ve "
    "ekseni switch'ten uzağa götürür. Koruma hareket bitince kendiliğinden geri gelir."
)
#: ...and while the board link is lost: motion's flags keep their last value.
LIMIT_STALE_NOTE = "Kart bağlantısı yok — switch durumu son okunan değer."


def limit_panel(
    state: MachineState, config: MachineConfig, profile: MachineProfile, observe: bool = False
) -> Optional[Dict[str, Any]]:
    """The Kontrol screen's limit card (plan WP05), None - no card - while
    no switch reads pressed: motion's own flags (motionrules.limit_trips),
    a homing joint's own home switch not counted. So also none while
    LinuxCNC's status is unknown (no flag is read then) and for
    MockAdapter, which reads none.

    Each pressed switch by its label and HAL pin; the button says what will
    move, which way and how far (motionrules.recovery_label: «Limitten
    kurtar: X+ yönünde 3 mm»), from the same plan the adapter sends
    (motionrules.recovery_plan over the INI's soft limits). Off with the
    plan's reason, or OBSERVE_REASON, as visible text: the kiosk removes
    title tooltips."""
    plan = motionrules.recovery_plan(state, profile, motionrules.soft_windows(config))
    if plan is None:
        return None
    reason = OBSERVE_REASON if observe else plan.refusal
    stale = bool(state.link.watchdog_has_bit or state.link.io_error)
    others = ""
    if plan.others:
        others = "Önce {}; ardından {} için tekrar basın — her switch ayrı bir basış.".format(
            plan.trip.label, ", ".join(o.label for o in plan.others))
    return {
        "switches": [
            {"joint": t.joint, "axis": t.axis, "end": t.end, "label": t.label,
             "hal_pin": t.hal_pin, "text": motionrules.switch_pressed_text(t)}
            for t in motionrules.limit_trips(state, profile)
        ],
        "axis": plan.trip.axis,
        "direction": plan.trip.away,
        "step_mm": plan.step_mm,
        "button_label": motionrules.recovery_label(plan),
        "enabled": not reason,
        "disabled_reason": reason,
        "note": LIMIT_CARD_NOTE,
        "others_note": others,
        "stale": stale,
        "stale_note": LIMIT_STALE_NOTE if stale else "",
    }


def limit_override_badge(state: MachineState) -> Dict[str, Any]:
    """The header's red badge while LinuxCNC's limit override is on
    (stat.joint[0]["override_limits"]), on every screen: nothing stops an
    axis at a switch until the jog it was given for ends (plan WP05). Off
    when not read (MockAdapter, status unknown)."""
    active = bool(state.status_known and state.override_limits)
    return {"active": active, "label": motionrules.OVERRIDE_BADGE if active else ""}


#: The homing card's button for one axis (plan WP07): what it homes.
HOME_AXIS_LABEL = "{} referansla"


def homing_panel(
    state: MachineState, config: MachineConfig, profile: MachineProfile, observe: bool = False
) -> Dict[str, Any]:
    """The homing gate, and its controls (plan WP07): «Tümünü referansla»
    (`home_enabled`, `home_disabled_reason`) and one button per axis
    (`axis_buttons`: {axis: {label, enabled, reason}}), each off with its
    reason as visible text (`reasons`; the kiosk removes title tooltips)
    whenever the adapters would refuse it - motionrules.home_block: the
    status unknown, a switch pressed (the reason names the switch and the
    way off, «Limitten kurtar»), the machine off, a program or MDI line
    running or paused, a homing running - and in observer mode
    (OBSERVE_REASON). Whether the machine moves is the press's question,
    not the card's (motionrules.home_refusal), as on the jog card. While a
    joint homes, `stop_note` says what DURDUR does and what it can leave
    (motionrules.HOME_STOP_NOTE). A press that must be asked about first
    (a homing again, one axis while the tool axis is unhomed) gets its
    question on the confirmation band (confirm_band), not here.

    HOME_IGNORE_LIMITS is a config fact, so it is read from the config where
    the INI has the joint: AxisConfig also keeps the raw text, which the
    unreadable-value message names. JointHoming.ignore_limits carries the
    same Optional[bool] (None = unreadable), passed through by the adapters,
    and is the fallback for a joint the config does not list. Either way an
    unknown must not read as "limits active".

    While LinuxCNC's status is unknown, no joint's phase is known either:
    each chip says "bilinmiyor" and `homed` is None.
    """
    from_ini = {a.joint: a for a in config.axes}
    known = state.status_known
    joints = []
    unreadable = []
    for j in state.homing.joints:
        ac = from_ini.get(j.joint)
        ignore = ac.home_ignore_limits if ac is not None else j.ignore_limits
        if ignore is None:
            raw = ac.home_ignore_limits_raw if ac is not None else ""
            unreadable.append("{} ({!r})".format(j.axis, raw))
        joints.append(
            {
                "axis": j.axis,
                "joint": j.joint,
                "phase": j.phase.value if known else "unknown",
                "label": j.phase_label if known else "bilinmiyor",
                "homed": (j.phase is HomingPhase.HOMED) if known else None,
                "sequence": j.sequence,
                "ignore_limits": ignore,
                "direction": j.direction_label,
                "severity": (
                    Severity.INFO.value
                    if known and j.phase is HomingPhase.HOMED
                    else Severity.WARN.value
                ),
            }
        )
    shares_switch = any(j["ignore_limits"] is True for j in joints)
    warning = []
    if shares_switch:
        warning.append(
            "Home ve limit aynı switch'te → HOME_IGNORE_LIMITS = YES. "
            "Referans alma sırasında limitler yok sayılır: switch'in yanlış "
            "tarafından başlanırsa ekseni durduracak hiçbir şey yoktur "
            "(ini-homing.adoc:46)."
        )
    if unreadable and not config.present:
        # No INI at all (the marble profile): the setting is not known - not
        # a bad value LinuxCNC would refuse (P0 cleanup review).
        warning.append(
            "HOME_IGNORE_LIMITS bilinmiyor (INI yok): referans alma sırasında "
            "limitlerin yok sayılıp sayılmayacağı bilinmiyor."
        )
    elif unreadable:
        # EmcIniFile::Find(bool) accepts an integer or TRUE/YES/FALSE/NO
        # (emcIniFile.cc:50-58, inifile.cc:122-139); anything else fails the
        # joint's INI load (inijoint.cc:102, :236-239).
        warning.append(
            "HOME_IGNORE_LIMITS okunamadı: {}. LinuxCNC yalnızca bir tamsayı ya "
            "da TRUE/YES/FALSE/NO kabul eder, bu değerle eklemi yüklemez. "
            "Referans alma sırasında limitlerin yok sayılıp sayılmayacağı "
            "bilinmiyor.".format(", ".join(unreadable))
        )
    block = OBSERVE_REASON if observe else motionrules.home_block(state, profile)
    buttons: Dict[str, Dict[str, Any]] = {}
    reasons = [block] if block else []
    for j in state.homing.joints:
        if j.axis in buttons:
            continue
        why = block or motionrules.home_axis_refusal(state, j.axis)
        buttons[j.axis] = {"label": HOME_AXIS_LABEL.format(j.axis), "enabled": not why,
                           "reason": why}
        if why and why not in reasons:
            reasons.append(why)
    homing_now = known and state.homing.any_homing and not observe
    return {
        "joints": joints,
        "all_homed": state.homing.all_homed if known else None,
        "order_label": state.homing.order_label,
        "order_source": ("[JOINT_n]HOME_SEQUENCE" if state.homing.order_known
                         else "INI'de [JOINT_n] yok"),
        "order_note": homing_order_note(config),
        "shares_home_and_limit": shares_switch,
        "ignore_limits_unknown": bool(unreadable),
        "shares_warning": " ".join(warning),
        "home_enabled": not block,
        "home_disabled_reason": block,
        "axis_buttons": buttons,
        "reasons": reasons,
        "stop_note": motionrules.HOME_STOP_NOTE if homing_now else "",
    }


def homing_order_note(
    config: MachineConfig, documented_order: str = DOCUMENTED_HOME_ORDER
) -> str:
    """Drift detector between the INI's HOME_SEQUENCE and the prose docs.

    Returns "" when they agree - which they do as of 2026-08-23, after the docs
    were corrected to match the INI's Z -> Y -> X.

    The comparison target is injectable so the detector itself can be tested
    against a disagreeing value; without that, a detector that never fires and
    a detector that cannot fire look identical.

    LinuxCNC obeys the INI, so the INI is always what the UI displays. The note
    only ever says "these two sources disagree, go and fix one".
    """
    if not config.present or not config.axes:
        return ""
    by_seq = sorted(config.axes, key=lambda a: abs(a.home_sequence))
    ini_order = " → ".join(a.axis for a in by_seq)
    if ini_order == documented_order:
        return ""
    return (
        "INI'deki HOME_SEQUENCE sırası {}. Dokümanlarda ({}) {} yazıyor. "
        "LinuxCNC INI'ye uyar; bu tutarsızlık giderilmeli.".format(
            ini_order, ", ".join(DOCUMENTED_HOME_ORDER_SOURCES), documented_order
        )
    )


def gate_panel(
    state: MachineState, config: MachineConfig, profile: MachineProfile, observe: bool = False
) -> Dict[str, Any]:
    """Why the program cannot start, and what is merely risky. It describes
    the machine, so observer mode changes only what a lost link asks for
    (_link_problems): the program itself is then started from AXIS."""
    warnings: List[str] = []
    if not config.present:
        warnings.append(config.absence_reason)
    elif not config.soft_limits_configured:
        warnings.append(
            "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok."
        )
    for m in profile.missing_switches:
        warnings.append(m.consequence)
    warnings.extend(_link_problems(state, observe))
    if profile.platform.latency_us is None:
        warnings.append(
            "Gecikme (jitter) ölçülmedi — {}.".format(profile.platform.latency_note)
        )
    for note in profile.notes:
        warnings.append(note)

    return {
        "blocks": state.start_blocks,
        "can_start": state.can_start_program,
        "can_jog": state.can_jog,
        "warnings": warnings,
        "headline": state.start_blocks[0] if state.start_blocks else "",
        # Plan WP08: no program loaded is said in the gate too, but is not a
        # start block (MachineState.start_blocks is unchanged): it is "what
        # to do next", not "what is wrong with the machine".
        "program": NO_PROGRAM if state.status_known and state.program_file is None else "",
        # Plan WP09: what runs, so a running or paused program never reads
        # "HAZIR" here. None when nothing runs (or it is not known).
        "run": run_gate(state),
    }


#: The gate's line for what runs (run_gate, plan WP09): text, tag, kind.
GATE_PAUSED = "Program duraklatıldı — «Devam» ile sürer, «Durdur» bitirir."
GATE_STEP = "Tek adım: bir satır yürütülüyor, sonra yine duraklar."
GATE_RUNNING = "Program çalışıyor."
GATE_RUNNING_LINE = "Program çalışıyor — satır {}."
GATE_MDI = "MDI satırı çalışıyor."


def run_state(state: MachineState) -> Optional[str]:
    """What runs, for the Program screen and the gate (plan WP09): "paused";
    "step" - a single step's motion, which runs with motion's paused flag
    set again (MachineState.single_step_moving); "end_paused" - the pause
    a program stepped to its end leaves (MachineState.pause_left_at_end);
    "running"; "mdi" - an MDI line; "idle". None while it cannot be said
    (programs.run_unread)."""
    if programs.run_unread(state):
        return None
    if state.pause_left_at_end:
        return "end_paused"
    if state.program_paused:
        return "step" if state.single_step_moving else "paused"
    if state.mdi_running:
        return "mdi"
    if state.program_busy:
        return "running"
    return "idle"


def run_gate(state: MachineState) -> Optional[Dict[str, str]]:
    """The gate's run line: {text, tag, kind}, or None when nothing runs."""
    run = run_state(state)
    if run == "paused":
        text = programs.MDI_PAUSED_TEXT if state.task_mode == "mdi" else GATE_PAUSED
        return {"text": text, "tag": "DURAKLATILDI", "kind": "warn"}
    if run == "step":
        return {"text": GATE_STEP, "tag": "TEK ADIM", "kind": "info"}
    if run == "end_paused":
        return {"text": programs.PAUSED_AT_END, "tag": "PROGRAM SONU", "kind": "warn"}
    if run == "running":
        text = (GATE_RUNNING_LINE.format(state.program_line) if state.program_line
                else GATE_RUNNING)
        return {"text": text, "tag": "ÇALIŞIYOR", "kind": "info"}
    if run == "mdi":
        return {"text": GATE_MDI, "tag": "MDI", "kind": "info"}
    return None


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


#: The feed-override chips the Kontrol screen offers, in percent.
FEED_OVERRIDE_CHOICES = (50, 100, 150)


def feed_chips(config: MachineConfig) -> List[Dict[str, Any]]:
    """One dict per feed-override chip: pct, enabled, reason.

    A choice above the INI's ceiling (config.max_feed_override_pct) is shown
    disabled with the reason, not hidden: LinuxCncAdapter would only clamp
    it, so offering it promised a speed the machine never allows (found in
    sim/ on 2026-10-19, the rig's ceiling being 100 %). With no INI every
    chip is disabled: LinuxCncAdapter refuses every feed change then.
    """
    ceiling = config.max_feed_override_pct
    chips = []
    for pct in FEED_OVERRIDE_CHOICES:
        if ceiling is None:
            reason = "INI yok — besleme sınırı ([DISPLAY]MAX_FEED_OVERRIDE) bilinmiyor"
        elif pct > ceiling:
            reason = "INI sınırı %{} ([DISPLAY]MAX_FEED_OVERRIDE)".format(ceiling)
        else:
            reason = ""
        chips.append({"pct": pct, "enabled": not reason, "reason": reason})
    return chips


def control_screen(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    observe: bool = False,
) -> Dict[str, Any]:
    """The Kontrol screen.

    While LinuxCNC's status is unknown (MachineState.status_known), the DRO
    and the WCS read "—" and no feed-override chip is marked active
    (feed_override None): the adapter's values for them are placeholders.
    The DRO keeps a row per axis even when the adapter reports no positions
    then (LinuxCncAdapter._status_unreadable): the axes are a config fact.
    The WCS and feed chips stay as buttons; the jog step is the UI's own
    setting, not a LinuxCNC reading. `jog_disabled_reason` says why jog is
    off without claiming the machine is off when that is not known.

    The jog card (plan WP06): `can_jog` and `jog_disabled_reason` for the
    whole card (MachineState.jog_block - homing, a program or MDI line
    running or paused - besides the machine off), `jog_pad` for each button
    under the adapters' own rule (motionrules.jog_plan), `jog_step_chips`
    with the 50 mm chip off while a joint is unhomed, and `soft_limits`,
    whether LinuxCNC applies the soft limits to a jog yet.

    In observer mode every control but the software estop is off, with
    OBSERVE_REASON: jog, the jog-step and feed chips and MDI through their
    own flags, the WCS chips through `commands_enabled`.
    """
    known = state.status_known
    if known:
        shown = [(p.axis, p) for p in state.positions]
    else:
        letters = [p.axis for p in state.positions] or [a.axis for a in config.axes]
        shown = [(axis, None) for axis in letters]
    axes = []
    for axis, pos in shown:
        ac = config.axis(axis)
        axes.append(
            {
                "axis": axis,
                "work": "—" if pos is None else "{:+.3f}".format(pos.work),
                "machine": "—" if pos is None else "{:+.3f}".format(pos.machine),
                "homed": None if pos is None else pos.homed,
                "travel": ac.travel.display if ac else UNMEASURED,
                "travel_measured": bool(ac and ac.travel.measured),
            }
        )
    feeds = feed_chips(config)
    if observe:
        feeds = [dict(f, enabled=False, reason=OBSERVE_REASON) for f in feeds]
    can_jog = state.can_jog and not observe
    jog_reason = OBSERVE_REASON if observe else _jog_off_reason(state)
    return {
        "status_known": known,
        "axes": axes,
        "wcs": state.wcs if known else "—",
        "wcs_options": ["G54", "G55", "G56"],
        "jog_steps": list(motionrules.JOG_STEPS_MM),
        "jog_step_chips": jog_step_chips(state, observe),
        "jog_step": state.jog_step_mm,
        "feed_overrides": feeds,
        "feed_override": state.feed_override_pct if known else None,
        "can_jog": can_jog,
        "jog_disabled_reason": jog_reason,
        "jog_pad": jog_pad(state, config, profile, "" if can_jog else jog_reason),
        "soft_limits": soft_limits_line(state, config, profile),
        "commands_enabled": not observe,
        "commands_disabled_reason": OBSERVE_REASON if observe else "",
        "estop": estop_panel(state, observe),
        "limit": limit_panel(state, config, profile, observe),
        "homing": homing_panel(state, config, profile, observe),
        "gate": gate_panel(state, config, profile, observe),
        "capabilities": capabilities_for_screen(profile, "kontrol"),
        "mdi": mdi_panel(state, config, observe),
        "tiles": control_tiles(profile, config, state),
    }


def control_tiles(
    profile: MachineProfile, config: MachineConfig, state: MachineState
) -> List[Dict[str, Any]]:
    """The four status tiles. Values only where a source exists."""
    spindle = profile.capability(Capability.SPINDLE)
    coolant = profile.capability(Capability.COOLANT_PUMP)
    flow = profile.capability(Capability.FLOW_SENSOR)
    active = config.axis("X")
    return [
        {
            "label": "JOG ADIMI",
            "value": "{:g}".format(state.jog_step_mm),
            "sub": "mm",
            "enabled": True,
            "reason": "",
        },
        {
            "label": "İĞ DEVRİ",
            "value": "—",
            "sub": "d/dk",
            "enabled": spindle.usable,
            "reason": spindle.reason,
        },
        {
            "label": "SU DEBİSİ",
            "value": "—",
            "sub": "L/dk",
            "enabled": flow.usable,
            "reason": flow.reason,
        },
        {
            "label": "MAKS. ADIM FREKANSI",
            "value": (
                "{:.0f}".format(max(a.max_step_rate_hz for a in config.axes))
                if config.axes
                else "—"
            ),
            "sub": (
                "Hz · TB6560 tavanı {:.0f} Hz".format(TB6560_STEP_CEILING_HZ)
                if config.axes
                else NO_CONFIG
            ),
            "enabled": bool(config.axes),
            "reason": "" if config.axes else config.absence_reason,
        },
    ]


def _motion_off_reason(state: MachineState, consequence: str) -> str:
    """Why the machine takes no motion command (MDI), "" while
    MachineState.motion_enabled allows it: the status unknown, the machine
    off, or the board link unread - each named as what it is.

    An unreadable status is named as such: "devre dışı" would be read off a
    placeholder."""
    if state.motion_enabled:
        return ""
    if not state.status_known:
        return "Makine durumu bilinmiyor — {}.".format(consequence)
    if state.estop.blocks_motion:
        return "Makine devre dışı — {}.".format(consequence)
    return "Kart bağlantısı okunamıyor — {}.".format(consequence)


#: The jog card's banner for each MachineState.jog_block key (plan WP06); a
#: refused click says it as motionrules.JOG_BLOCK_TEXTS does.
JOG_OFF_REASONS = {
    JOG_BLOCK_UNKNOWN: "Makine durumu bilinmiyor — jog kapalı.",
    JOG_BLOCK_OFF: "Makine devre dışı — jog kapalı.",
    JOG_BLOCK_HOMING: "Referans alma sürüyor — jog kapalı. Durdurmak için «Durdur».",
    JOG_BLOCK_UNREAD: "Program durumu okunamadı — jog kapalı.",
    JOG_BLOCK_PAUSED: "Program duraklatıldı — jog kapalı.",
    JOG_BLOCK_RUNNING: "Program çalışıyor — jog kapalı.",
}


def _jog_off_reason(state: MachineState) -> str:
    """Why the whole jog card is off, "" while MachineState.can_jog allows
    it: MachineState.jog_block's reason, else the board link unread."""
    if state.can_jog:
        return ""
    block = state.jog_block
    if block:
        return JOG_OFF_REASONS[block]
    return _motion_off_reason(state, "jog kapalı")


#: The jog pad's buttons, (label, axis, direction), in the order their
#: refusals are listed under the pad.
JOG_BUTTONS = (
    ("X−", "X", -1), ("X+", "X", 1),
    ("Y−", "Y", -1), ("Y+", "Y", 1),
    ("Z−", "Z", -1), ("Z+", "Z", 1),
)


def jog_pad(
    state: MachineState, config: MachineConfig, profile: MachineProfile, off_reason: str
) -> Dict[str, Any]:
    """Each jog button on its own (plan WP06): {"buttons": {"X:1": {label,
    enabled, reason}, ...}, "reasons": [...]}.

    While the jog card is off (`off_reason`: the card's banner, or
    OBSERVE_REASON) every button is, with that reason. Otherwise each asks
    the rule the adapters send by, for the jog step as it stands
    (motionrules.jog_plan): Z− with a 10 mm step while Z is unhomed (no
    switch below it), a step past the unhomed caps, a homed axis standing
    on its soft limit, a jog onto a pressed switch - each button off with
    its own reason, and the reasons, once each, in `reasons`, which the
    card writes out under the pad: the kiosk removes title tooltips
    (app.js noTooltips). Whether the machine moves is the click's question,
    not the pad's: a pad that went off and on with every jog would move
    under the finger."""
    windows = motionrules.axis_windows(config)
    buttons: Dict[str, Dict[str, Any]] = {}
    reasons: List[str] = []
    for label, axis, direction in JOG_BUTTONS:
        key = "{}:{}".format(axis, direction)
        if off_reason:
            buttons[key] = {"label": label, "enabled": False, "reason": off_reason}
            continue
        decision = motionrules.jog_plan(state, profile, windows, axis, direction,
                                        state.jog_step_mm)
        buttons[key] = {"label": label, "enabled": not decision.refusal,
                        "reason": decision.refusal}
        if decision.refusal and decision.refusal not in reasons:
            reasons.append(decision.refusal)
    return {"buttons": buttons, "reasons": reasons}


def jog_step_chips(state: MachineState, observe: bool = False) -> List[Dict[str, Any]]:
    """One dict per jog-step chip (motionrules.JOG_STEPS_MM): mm, enabled,
    reason, on. A step past motionrules.UNHOMED_MAX_STEP_MM - the 50 mm
    chip - is off while any joint is unhomed (plan WP06), and while the
    status is unknown, when whether one is homed is not known. The step is
    the UI's own setting, so the chip of the step chosen stays marked."""
    chips = []
    for mm in motionrules.JOG_STEPS_MM:
        reason = ""
        if observe:
            reason = OBSERVE_REASON
        elif mm > motionrules.UNHOMED_MAX_STEP_MM:
            if not state.status_known:
                reason = "Makine durumu bilinmiyor — {:g} mm adım kapalı.".format(mm)
            elif not state.homing.all_homed:
                reason = "Referans yok — {:g} mm adım kapalı; referanssız en çok {:g} mm.".format(
                    mm, motionrules.UNHOMED_MAX_STEP_MM)
        chips.append({"mm": mm, "enabled": not reason, "reason": reason,
                      "on": abs(mm - state.jog_step_mm) < 1e-9})
    return chips


def soft_limits_line(state: MachineState, config: MachineConfig, profile: MachineProfile) -> str:
    """The jog card's line on the soft limits (plan WP06): LinuxCNC applies
    them to a jog only once the joint is homed (motionrules.jog_plan), so
    'etkin' for every joint homed, 'etkin değil (referans yok)' with the
    unhomed step caps instead, and which axes when only some are homed."""
    if not state.status_known:
        return "Yumuşak limitler: bilinmiyor (makine durumu okunamıyor)."
    windows = motionrules.axis_windows(config)
    if not any(end is not None for window in windows.values() for end in window):
        return "Yumuşak limitler: INI'de tanımsız."
    homed = list(dict.fromkeys(
        j.axis for j in state.homing.joints if j.phase is HomingPhase.HOMED))
    unhomed = list(dict.fromkeys(
        j.axis for j in state.homing.joints if j.phase is not HomingPhase.HOMED))
    if not unhomed:
        return "Yumuşak limitler: etkin."
    caps = motionrules.unhomed_step_note(profile, unhomed)
    if not homed:
        return "Yumuşak limitler: etkin değil (referans yok) — {}.".format(caps)
    return "Yumuşak limitler: yalnız {} için etkin ({} referanssız) — {}.".format(
        ", ".join(homed), ", ".join(unhomed), caps)


def _gcode_number(value: float) -> Optional[str]:
    """`value` as a G-code number that reads back as exactly `value`, or None.

    The shortest round-trip form (repr), with "-3.0" written "-3". The
    G-code reader takes signs, digits and a point, no exponent
    (read_real_number, src/emc/rs274ngc/interp_read.cc:2740-2746), so a
    value repr writes with an exponent (|value| < 1e-4 or >= 1e16) gives
    None, as do inf and nan.
    """
    text = repr(float(value))
    if "e" in text or "n" in text:
        return None
    return text[:-2] if text.endswith(".0") else text


def mdi_presets(config: MachineConfig) -> List[str]:
    """The MDI preset chips: only commands LinuxCNC accepts on this machine.

    The one Z move is a G53 (machine-coordinate) move to the top of Z's
    soft-limit window, the INI's MAX_LIMIT, so no work offset can carry it
    out of range. Motion accepts a target on a limit and refuses one beyond
    it (inRange, src/emc/motion/command.c:259-268). It needs measured
    travel: without an INI (the marble profile) or with provisional limits
    there is no window, and no Z move is offered. The prototype's "G0 Z5"
    and "G53 G0 Z0" lie above the test rig's window (its G54 offsets are
    zero), so motion refuses both there.
    """
    presets: List[str] = []
    z = config.axis("Z")
    if z is not None and z.travel.has_crash_protection and z.travel.max_limit is not None:
        top = _gcode_number(z.travel.max_limit)
        if top is not None:
            presets.append("G53 G0 Z{}".format(top))
    return presets + ["M5", "G92.1"]


def mdi_status(entry: MdiEntry) -> str:
    """An MDI history entry's status (state.MDI_*): its own, or for an
    entry that carries none, what `accepted` alone can say - "sent", never
    "done", since a line sent is not known to have completed."""
    return entry.status or (MDI_SENT if entry.accepted else MDI_REFUSED)


def mdi_panel(
    state: MachineState, config: MachineConfig, observe: bool = False
) -> Dict[str, Any]:
    """Free text entry plus history. The prototype had presets only; these
    come from mdi_presets. Off in observer mode.

    Each history entry carries its status (state.MDI_*): refused here,
    sent with its result awaited, rejected or cut short after it was sent,
    or done (adapter.LinuxCncAdapter._note_mdi_result).

    Off while a program or an MDI line is paused (MachineState.
    program_paused - the condition mdi.mdi_refusal's `paused` refuses by,
    in both adapters), with MDI_WHILE_PAUSED as the visible reason (P0
    cleanup; final P0 review)."""
    paused = bool(state.motion_enabled and state.program_paused)
    if observe:
        reason = OBSERVE_REASON
    elif paused:
        reason = MDI_WHILE_PAUSED
    else:
        reason = _motion_off_reason(state, "MDI kabul edilmiyor")
    return {
        "enabled": state.motion_enabled and not observe and not paused,
        "disabled_reason": reason,
        "presets": mdi_presets(config),
        "history": [
            {"command": e.command, "accepted": e.accepted, "note": e.note,
             "status": mdi_status(e)}
            for e in reversed(state.mdi_history[-12:])
        ],
        "placeholder": "MDI komutu yaz ve Enter'a bas",
    }


#: The gate's line while no program is loaded (gate_panel, plan WP08).
NO_PROGRAM = "Program yüklenmedi — Program ekranından bir dosya seçip «Yükle»."
#: The Program screen's «Başlat» with no program loaded.
START_NO_PROGRAM = "Program yüklenmedi."


def _size_text(size: int) -> str:
    """A file size as the list shows it: bytes, kB or MB, decimal comma."""
    if size < 1024:
        return "{} B".format(size)
    if size < 1024 * 1024:
        return "{:.1f} kB".format(size / 1024).replace(".", ",")
    return "{:.1f} MB".format(size / (1024 * 1024)).replace(".", ",")


def _date_text(mtime: float) -> str:
    """A file's mtime as the list shows it; "—" for one the platform cannot
    turn into a date (a render must not fail on a file's odd mtime)."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    except (OverflowError, OSError, ValueError):
        return "—"


def program_screen(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    observe: bool = False,
    page: int = 0,
) -> Dict[str, Any]:
    """The Program screen (plan WP08): the G-code files in the program
    directory ([DISPLAY]PROGRAM_PREFIX, config.program_dir), newest first,
    each with «Yükle» (load_program, its name as the value); the loaded
    file; «Yeniden yükle», «Listeyi yenile», «Başlat» and «Durdur».

    The list comes from programs.list_programs, cached by the directory's
    mtime: a render costs one os.stat of the directory and one of the
    loaded file (programs.line_count), no scan. An empty list says why:
    the directory missing, unreadable or empty, or no PROGRAM_PREFIX - each
    its own text.

    Loading is off - for every file, with the reason as visible text - by
    programs.load_block: a program or MDI line running or paused, a homing,
    the machine moving, the status unknown; and in observer mode (programs
    are loaded from AXIS then). A file whose path LinuxCNC could not take
    (programs.path_problem) is off with its own reason.

    The loaded file and the run state come from LinuxCNC's status, so they
    are None while it is unknown. `loaded` is the file's base name,
    `loaded_path` the whole path, `loaded_lines` its line count (WP09's
    progress), `loaded_listed` its name in the directory or None.

    «Başlat»'s reason when off is visible text too (start_disabled_reason;
    the kiosk removes title tooltips).

    Running (plan WP09): one primary button by what runs (`primary`) -
    «Başlat» (program_start) with nothing running, «Duraklat»
    (program_pause) while a program or an MDI line runs, «Devam»
    (program_resume) while something is paused - and beside it «Adım»
    (program_step) and «Durdur». Each with `enabled` and, when off, its
    `reason` as visible text, by the adapters' own rules (programs.
    start_busy, pause_refusal, resume_refusal, step_refusal; start_blocks).
    `run` is run_state; `line` motion's line and `line_text` that line of
    the loaded file (programs.line_text, from the copy made at load),
    while a program runs or is paused; `progress_pct` motion's line over
    the line count (MachineState.program_progress).
    """
    known = state.status_known
    gate = gate_panel(state, config, profile, observe)
    directory = config.program_dir
    listing = (
        programs.list_programs(directory) if directory
        else programs.Listing(None, (), 0, config.program_dir_reason or programs.NO_DIR_KEY)
    )
    loaded_path = state.program_file if known else None
    listed = programs.listed_name(directory, loaded_path)
    if observe:
        load_reason = OBSERVE_REASON
    else:
        load_reason = programs.load_block(state)
    # P0 cleanup: one page of the list (programs.PAGE_SIZE rows), «Önceki» /
    # «Sonraki» and the count line. A page is the browser's own view state,
    # like the screen (app.js keeps it): no action, nothing sent, the same
    # in observer mode.
    page = listing.page_index(page)
    files = []
    for f in listing.page(page):
        problem = programs.path_problem(directory, f.name) if directory else ""
        files.append({
            "name": f.name,
            "size": f.size,
            "size_text": _size_text(f.size),
            "date": _date_text(f.mtime),
            "loaded": listed is not None and f.name == listed,
            "enabled": not load_reason and not problem,
            "reason": problem,
        })
    if not loaded_path:
        reload_reason = programs.NOTHING_LOADED if known else load_reason
    elif listed is None:
        reload_reason = programs.LOADED_OUTSIDE.format(loaded_path)
    else:
        reload_reason = ""
    reload_reason = load_reason or reload_reason
    if observe:
        start_reason = OBSERVE_REASON
    elif gate["headline"]:
        start_reason = gate["headline"]
    elif state.program_file is None:
        start_reason = START_NO_PROGRAM
    else:
        start_reason = programs.start_busy(state)
    can_start = state.can_start_program and not observe
    run = run_state(state)
    # A program's line and progress: running, or paused in AUTO - an MDI
    # line's motion id is no line of the loaded file.
    busy = known and (bool(state.program_running)
                      or (bool(state.program_paused) and state.task_mode == "auto"))
    line = state.program_line if busy and state.program_line else None
    lines = programs.line_count(loaded_path) if loaded_path else None
    primary = _primary_button(state, run, can_start, start_reason or START_NO_PROGRAM,
                              gate["headline"], observe)
    step_reason = (
        OBSERVE_REASON if observe
        else gate["headline"] or (START_NO_PROGRAM if state.program_file is None else "")
        or programs.step_refusal(state)
    )
    return {
        "dir": directory,
        "files": files,
        "files_empty_reason": listing.empty_reason,
        "files_note": listing.note,
        "files_count": listing.page_text(page),
        "page": page,
        "pages": listing.pages,
        "page_prev": page - 1 if page > 0 else None,
        "page_next": page + 1 if page + 1 < listing.pages else None,
        "load_enabled": not load_reason,
        "load_disabled_reason": load_reason,
        "reload_enabled": not reload_reason,
        "reload_disabled_reason": reload_reason,
        "refresh_enabled": bool(directory) and not observe,
        "refresh_disabled_reason": (
            OBSERVE_REASON if observe else "" if directory else listing.empty_reason
        ),
        "loaded": os.path.basename(loaded_path) if loaded_path else None,
        "loaded_path": loaded_path,
        "loaded_listed": listed,
        "loaded_lines": lines,
        "running": state.program_running if known else None,
        "paused": state.program_paused if known else None,
        "run": run,
        "line": line,
        "line_text": programs.line_text(loaded_path, line) if line else None,
        "progress_pct": round(state.program_progress * 100, 1) if busy else None,
        "gate": gate,
        "can_start": can_start,
        "start_disabled_reason": "" if can_start else (start_reason or START_NO_PROGRAM),
        "primary": primary,
        "step_enabled": not step_reason,
        "step_disabled_reason": step_reason,
        "stop_enabled": not observe,
        "stop_disabled_reason": OBSERVE_REASON if observe else "",
        "capabilities": capabilities_for_screen(profile, "program"),
    }


def _primary_button(state: MachineState, run: Optional[str], can_start: bool,
                    start_reason: str, headline: str, observe: bool) -> Dict[str, Any]:
    """The Program screen's one primary button by what runs (plan WP09):
    {label, action, enabled, reason}. «Devam» while something is paused (a
    single step's motion included: resuming it lets the program run on),
    «Duraklat» while a program or an MDI line runs, «Başlat» otherwise. A
    pause left at the program's end shows «Devam» off, its reason saying
    «Durdur» ends it (programs.PAUSED_AT_END)."""
    if run in ("paused", "step", "end_paused"):
        reason = (OBSERVE_REASON if observe
                  else headline or programs.resume_refusal(state))
        return {"label": "Devam", "action": "program_resume",
                "enabled": not reason, "reason": reason}
    if run in ("running", "mdi"):
        reason = OBSERVE_REASON if observe else programs.pause_refusal(state)
        return {"label": "Duraklat", "action": "program_pause",
                "enabled": not reason, "reason": reason}
    return {"label": "Başlat", "action": "program_start",
            "enabled": can_start, "reason": "" if can_start else start_reason}


def probing_screen(profile: MachineProfile) -> Dict[str, Any]:
    """Step-by-step probing flow, preserved from the prototype but gated.

    The flow is good UI and worth keeping visible. It is disabled here because
    the test rig has no probe, with the reason on screen.
    """
    probe = profile.capability(Capability.TOOL_PROBE)
    modes = [
        {
            "id": "z",
            "name": "Z yüzey bulma",
            "desc": "Takım boyunu iş yüzeyine göre sıfırlar",
            "steps": [
                "Takımı sensörün üstüne getir",
                "Yavaş inişle temas ara",
                "Takım boyunu kaydet",
                "Z'yi iş sıfırına al",
            ],
        },
        {
            "id": "corner",
            "name": "Köşe bulma",
            "desc": "X ve Y'yi parçanın köşesinden alır",
            "steps": [
                "X kenarına yaklaş",
                "Y kenarına yaklaş",
                "Uç yarıçapını düş",
                "Köşeyi sıfır kabul et",
            ],
        },
        {
            "id": "center",
            "name": "Delik merkezi",
            "desc": "Dört nokta ile merkez hesaplar",
            "steps": [
                "Deliğe ortala ve in",
                "Dört yönde temas ara",
                "Merkezi hesapla",
                "X/Y sıfırla",
            ],
        },
    ]
    return {
        "enabled": probe.usable,
        "disabled_reason": probe.reason,
        "requirement": probe.requirement,
        "source": probe.source,
        "modes": modes,
        # Parameters are left blank rather than invented: the prototype's
        # 300 mm/dk approach and 25 mm/dk contact speeds have no source.
        "params": [
            {"label": "Yaklaşma hızı", "value": "—"},
            {"label": "Temas hızı", "value": "—"},
            {"label": "Geri çekilme", "value": "—"},
        ],
        "params_note": "Prob parametreleri belirlenmedi — prob yok.",
        "capabilities": capabilities_for_screen(profile, "probing"),
        "probe_is_not_a_tool": (
            "Prob bir HAL girişidir, takım tablosu kaydı DEĞİLDİR. "
            "Takım tablosunda prob satırı bulunmaz."
        ),
    }


def tools_screen(profile: MachineProfile, config: MachineConfig) -> Dict[str, Any]:
    """Tool table.

    Empty: [EMCIO]TOOL_TABLE names tool.tbl but configs/test-rig/tool.tbl does
    not exist. Inventing tools would be inventing tool length offsets, which is
    a number that moves a machine.
    """
    tool_table_path = None
    if config.ini_path:
        tool_table_path = os.path.join(os.path.dirname(config.ini_path), "tool.tbl")
    exists = bool(tool_table_path and os.path.exists(tool_table_path))
    return {
        "tools": [],
        "table_exists": exists,
        "table_path": tool_table_path,
        "empty_reason": (
            "Takım tablosu yok. INI'de TOOL_TABLE = tool.tbl yazıyor ama dosya "
            "oluşturulmadı. Boy telafisi değerleri makineyi hareket ettirir — "
            "uydurulmaz."
        ),
        "probe_note": (
            "Prob burada listelenmez. Prob bir HAL girişidir, takım değildir."
        ),
        "capabilities": capabilities_for_screen(profile, "takimlar"),
    }


#: An input's live state (MachineState.input_values) as the Alarmlar screen
#: shows it: (state, label, severity). "unread": the adapter reads no inputs
#: (MockAdapter), so nothing is claimed - muted, not WARN. "unknown": it
#: tried and the pin did not answer, WARN as any unreadable value
#: (_reading_severity). A pressed switch is WARN: with the machine ON it is
#: a limit error, and the machine refuses to enable on one.
_INPUT_STATES = {
    "unread": ("—", ""),
    "unknown": ("okunamadı", Severity.WARN.value),
    "pressed": ("basılı", Severity.WARN.value),
    "free": ("serbest", Severity.INFO.value),
}


def _input_state(state: MachineState, hal_pin: str) -> str:
    if not state.input_values:
        return "unread"
    value = state.input_value(hal_pin)
    if value is None:
        return "unknown"
    return "pressed" if value else "free"


def alarms_screen(profile: MachineProfile, state: MachineState) -> Dict[str, Any]:
    """Inputs and the event log, both with source attribution.

    Each input carries its live state, read from the pin the HAL nets take
    (profiles.DigitalInput.hal_pin), where TRUE means pressed whichever way
    the input idles (_INPUT_STATES)."""
    inputs = []
    for inp in profile.inputs:
        live = _input_state(state, inp.hal_pin)
        inputs.append(
            {
                "pin": inp.pin,
                "hal_pin": inp.hal_pin,
                "label": inp.label,
                "axis": inp.axis,
                "end": inp.end,
                "idle_high": inp.idle_high,
                "polarity_note": (
                    "idle HIGH → HAL'de .in_not" if inp.idle_high else "idle LOW → .in"
                ),
                "source": inp.source,
                # Read-only: the prototype made these clickable toggles, but an
                # input is something the machine tells us, not something we set.
                "read_only": True,
                "state": live,
                "state_label": _INPUT_STATES[live][0],
                "state_severity": _INPUT_STATES[live][1],
            }
        )
    missing = [
        {
            "axis": m.axis,
            "end": m.end,
            "label": m.label,
            "consequence": m.consequence,
            "source": m.source,
        }
        for m in profile.missing_switches
    ]
    events = [
        {
            "time": a.time,
            "text": a.text,
            "source": a.source,
            "severity": a.severity.value,
        }
        for a in state.alarms
    ]
    return {
        "inputs": inputs,
        "inputs_empty_reason": (
            "Giriş haritası çıkarılmadı — makine envanteri boş."
            if not inputs
            else ""
        ),
        "inputs_live": bool(state.input_values),
        "inputs_live_note": (
            "Durum canlı: her saniye HAL'den okunur (basılı = switch tetiklenmiş)."
            if state.input_values
            else "Canlı durum okunmuyor — bu adaptör HAL girişlerini okumuyor."
        ),
        "missing_switches": missing,
        "events": events,
        "active_count": sum(
            1 for a in state.alarms if a.severity is Severity.ERROR
        ),
        "capabilities": capabilities_for_screen(profile, "alarmlar"),
    }


def _ns(value: Optional[int]) -> str:
    """Nanoseconds as TimingConfig.rows writes them ("10 000"), "—" unread."""
    return "—" if value is None else "{:,}".format(value).replace(",", " ")


def _stepgen_runs(items: List[Any]) -> str:
    """Per-stepgen values, a run of equal neighbours written once:
    "00–02: 10 000 · 03: 20 000 ns"."""
    runs: List[List[Any]] = []
    for _, index, value in items:
        if runs and runs[-1][2] == value:
            runs[-1][1] = index
        else:
            runs.append([index, index, value])
    return " · ".join(
        "{}{}: {}".format(first, "" if last == first else "–" + last, _ns(value))
        for first, last, value in runs
    ) + " ns"


def timing_live_rows(config: MachineConfig, state: MachineState) -> List[Dict[str, Any]]:
    """The step timing the driver runs (MachineState.stepgen_timing), one row
    per parameter over every stepgen read, for the card that shows the INI's
    (TimingConfig.rows). [] when nothing was read.

    One value when every stepgen has the same, else each stepgen's. WARN,
    with a note, when a value could not be read, when the stepgens differ,
    or when the value differs from the INI's on that card: the INI then no
    longer says what runs (a halcmd setp since LinuxCNC started, or a value
    the driver reset).
    """
    groups: Dict[str, List[Any]] = {}
    for name, value in state.stepgen_timing:
        # "<prefix>.stepgen.NN.<param>", as adapter._stepgen_timing_names builds it
        base, _, param = name.rpartition(".")
        head, _, index = base.rpartition(".")
        groups.setdefault(param, []).append((head, index, value))
    ini = config.timing._asdict() if config.timing else {}
    rows = []
    for param, items in groups.items():
        values = [v for _, _, v in items]
        known = {v for v in values if v is not None}
        expected = ini.get(param + "_ns")
        notes = []
        if None in values:
            notes.append("okunamadı: " + ", ".join(
                "stepgen.{}".format(i) for _, i, v in items if v is None))
        if len(known) > 1:
            notes.append("stepgen'ler aynı değerde değil")
        if expected is not None and known and known != {expected}:
            notes.append("INI'de {} ns".format(_ns(expected)))
        if len(set(values)) == 1:
            value = "—" if values[0] is None else "{} ns".format(_ns(values[0]))
        else:
            value = _stepgen_runs(items)
        first, last = items[0], items[-1]
        rows.append({
            "label": param,
            "value": value,
            "severity": Severity.WARN.value if notes else Severity.INFO.value,
            "hal": "{}.{}{}.{}".format(
                first[0], first[1], "–" + last[1] if len(items) > 1 else "", param
            ),
            "note": "; ".join(notes),
        })
    return rows


def machine_screen(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    observe: bool = False,
) -> Dict[str, Any]:
    board = profile.board
    plat = profile.platform
    link = link_panel(state, config, observe)
    timing_live = timing_live_rows(config, state)
    axes = [
        {
            "axis": a.axis,
            "joint": a.joint,
            "scale": a.scale_display,
            "velocity": a.velocity_display,
            "acceleration": a.acceleration_display,
            "travel": a.travel.display,
            "travel_measured": a.travel.measured,
            "step_rate": "{:.0f} Hz".format(a.max_step_rate_hz),
            "home_sequence": a.home_sequence,
        }
        for a in config.axes
    ]
    return {
        "board_rows": [
            {"label": "Kart", "value": board.name},
            {"label": "HAL öneki", "value": board.hal_prefix},
            {"label": "FPGA", "value": board.fpga},
            {"label": "IO pinleri", "value": board.io_pins},
            {"label": "Saat", "value": board.clock},
            {"label": "Bağlantı", "value": board.link},
        ],
        "board_source": board.source,
        "link_rows": link["rows"],
        "link_problems": link["problems"],
        "link_healthy": link["healthy"],
        "platform_rows": [
            {"label": "Çekirdek", "value": plat.kernel, "severity": Severity.INFO.value},
            {"label": "LinuxCNC", "value": plat.linuxcnc, "severity": Severity.INFO.value},
            {"label": "Host", "value": plat.host, "severity": Severity.INFO.value},
            {"label": "NIC", "value": plat.nic, "severity": Severity.INFO.value},
            {
                "label": "Maks. jitter",
                # Never a number: it was never measured.
                "value": "—" if plat.latency_us is None else "{:.0f} µs".format(plat.latency_us),
                "severity": (
                    Severity.WARN.value if plat.latency_us is None else Severity.INFO.value
                ),
                "note": plat.latency_note if plat.latency_us is None else "",
            },
        ],
        "config_present": config.present,
        "config_absence_reason": config.absence_reason,
        "config_rows": (
            [
                {"label": "INI", "value": os.path.basename(config.ini_path or "")},
                {"label": "Kinematik", "value": config.kinematics or "—"},
                {"label": "Joint sayısı", "value": str(config.joints or "—")},
                {"label": "Birim", "value": config.units or "—"},
                {"label": "hm2 config", "value": config.hm2_config_string or "—"},
                {
                    "label": "Servo döngü",
                    # Same rule as the link card's row (link_panel), so the
                    # two cards of this screen cannot disagree.
                    "value": (
                        "—" if config.servo_period_ms is None
                        else "{:g} ms".format(config.servo_period_ms)
                    ),
                },
            ]
            if config.present
            else []
        ),
        "axes": axes,
        "timing_rows": config.timing.rows() if config.timing else [],
        "timing_live_rows": timing_live,
        "timing_live_note": (
            "Sürücünün kullandığı değerler, her saniye HAL'den okunur."
            if timing_live
            else "Canlı değer okunmuyor — HAL okunmuyor ya da INI'de num_stepgens yok."
        ),
        "timing_note": (
            "TB6560 optokuplörleri için hesaplandı (docs/09). Sürücü değişirse "
            "bu değerler geçersiz."
        ),
        "soft_limits_configured": config.soft_limits_configured,
        "soft_limits_warning": (
            ""
            if config.soft_limits_configured
            else "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok."
        ),
        "capabilities": capability_panels(profile),
        "profile_notes": list(profile.notes),
        "missing_switches": [
            {"label": m.label, "consequence": m.consequence} for m in profile.missing_switches
        ],
    }


# ---------------------------------------------------------------------------
# Motion status
# ---------------------------------------------------------------------------


def motion_status(state: MachineState) -> Dict[str, Any]:
    """What motion and the interpreter report, as read: MachineState's
    motion fields, for GET /api/state and the screens that will show them
    (nothing renders this yet). Raw values, not display text: None where
    the adapter did not read the field (MockAdapter reads none) and, like
    every reading, while LinuxCNC's status is unknown - then hard_limits is
    empty too, and commanded has no axes."""
    known = state.status_known

    def read(value: Any) -> Any:
        return value if known else None

    return {
        "task_mode": read(state.task_mode),
        "motion_mode": read(state.motion_mode),
        "interp_state": read(state.interp_state),
        "moving": read(state.moving),
        "program_paused": read(state.program_paused),
        "mdi_running": read(state.mdi_running),
        "program_line": read(state.program_line),
        "g92_active": read(state.g92_active),
        "override_limits": read(state.override_limits),
        "hard_limits": [
            {"joint": h.joint, "axis": h.axis, "min": h.min, "max": h.max}
            for h in (state.hard_limits if known else ())
        ],
        "commanded": {p.axis: p.commanded for p in (state.positions if known else ())},
    }


# ---------------------------------------------------------------------------
# The message band
# ---------------------------------------------------------------------------


#: The confirmation band (confirm_band, render.render_confirm; plan WP07).
CONFIRM_LABEL = "ONAY"
CONFIRM_YES = "Evet"
CONFIRM_NO = "Vazgeç"
CONFIRM_HINT = (
    "{:g} s içinde «Evet»e basılmazsa ya da arada başka bir komut verilirse iptal olur."
)
#: The hint while «Evet» does not count yet (app.CONFIRM_MIN_S after the
#: question: a double tap on the button that asked must not answer it).
CONFIRM_WAIT = "Önce soruyu okuyun — «Evet» {:g} s sonra açılır."
#: An «Evet» that came for a question no longer standing (app.Session).
CONFIRM_REFUSED = (
    "Onay geçersiz — süresi doldu ya da arada başka bir komut geldi; hiçbir şey "
    "gönderilmedi. Düğmeye yeniden basın."
)


def confirm_band(confirm: Optional[Dict[str, Any]], observe: bool = False) -> Optional[Dict[str, Any]]:
    """The confirmation band at the top of every screen (plan WP07), from
    app.Session's question (its confirm view: token, text, timeout_s,
    ready, wait_s): the question - what will move, ending 'yol açık mı?' -
    with «Evet», which carries the token, «Vazgeç» and how long it stands.
    «Evet» is off (`ready` False) for the first app.CONFIRM_MIN_S, the hint
    saying so (CONFIRM_WAIT): the band opens where a double tap's second
    press can land. For a moment after an «Evet» for a question that was
    gone ({"refused": True}), that it sent nothing. None for neither, and
    always in observer mode, which asks nothing."""
    if not confirm or observe:
        return None
    if confirm.get("refused"):
        return {"token": "", "text": CONFIRM_REFUSED, "refused": True, "ready": False,
                "label": CONFIRM_LABEL, "yes": "", "no": "", "hint": ""}
    ready = bool(confirm.get("ready"))
    return {
        "token": str(confirm.get("token") or ""),
        "text": str(confirm.get("text") or ""),
        "refused": False,
        "ready": ready,
        # While «Evet» is off: in how many ms it turns on, so app.js asks for
        # one render right then (P0 cleanup; until then it waited for the
        # next 1 Hz poll, 1-2 s). The server's rule stands either way.
        "ready_in_ms": 0 if ready else int(math.ceil(
            float(confirm.get("ready_in_s") or 0) * 1000)),
        "label": CONFIRM_LABEL,
        "yes": CONFIRM_YES,
        "no": CONFIRM_NO,
        "hint": (CONFIRM_HINT.format(float(confirm.get("timeout_s") or 0)) if ready
                 else CONFIRM_WAIT.format(float(confirm.get("wait_s") or 0))),
    }


def last_error_view(error: Optional[LastError]) -> Optional[Dict[str, Any]]:
    """The error box (render.render_msgband) as plain data, None when there
    is nothing to show. `id` goes on «Tamam» (app.Session.act "ack_error"),
    so it clears this error and no later one."""
    if error is None:
        return None
    return {
        "id": error.id,
        "time": error.time,
        "text": error.text,
        "raw": error.raw,
        "source": error.source,
    }


# ---------------------------------------------------------------------------
# Top level
# ---------------------------------------------------------------------------


def build(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    screen: str = "kontrol",
    observe: bool = False,
    confirm: Optional[Dict[str, Any]] = None,
    program_page: int = 0,
) -> Dict[str, Any]:
    """The whole view model for one render. `observe`: observer mode
    (app.Session), the banner and every control but the estop off.
    `confirm`: app.Session's question standing, for the confirmation band
    (confirm_band, plan WP07). `program_page`: the Program list's page the
    browser shows (0-based, clamped; P0 cleanup)."""
    alarms = alarms_screen(profile, state)
    return {
        "profile": {
            "key": profile.key,
            "label": profile.label,
            "subtitle": profile.subtitle,
        },
        "screen": screen,
        "nav": [
            {
                "id": sid,
                "label": label,
                "sub": sub,
                "active": sid == screen,
                "badge": alarms["active_count"] if sid == "alarmlar" else 0,
            }
            for sid, label, sub in SCREENS
        ],
        "status": {
            "label": state.status_label,
            "severity": state.status_severity.value,
            "message": state.message,
            "message_severity": state.message_severity.value,
            "last_error": last_error_view(state.last_error),
            "limit_override": limit_override_badge(state),
        },
        "observe": {
            "active": observe,
            "banner": OBSERVE_BANNER if observe else "",
            "detail": OBSERVE_DETAIL if observe else "",
        },
        "link": link_panel(state, config, observe),
        "rail": stop_rail(observe),
        "confirm": confirm_band(confirm, observe),
        "motion": motion_status(state),
        "kontrol": control_screen(profile, config, state, observe),
        "program": program_screen(profile, config, state, observe, program_page),
        "probing": probing_screen(profile),
        "takimlar": tools_screen(profile, config),
        "alarmlar": alarms,
        "makine": machine_screen(profile, config, state, observe),
    }
