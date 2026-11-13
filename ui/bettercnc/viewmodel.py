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

import os
from typing import Any, Dict, List, Optional

from .capabilities import (
    CAPABILITY_CATALOGUE,
    CAPABILITY_META,
    Capability,
    Presence,
)
from .configmodel import NO_CONFIG, UNMEASURED, MachineConfig
from .profiles import MachineProfile
from .state import DrivePower, HomingPhase, MachineState, Severity

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
    "Bu ekrandan yalnız acil stop gider: «Yazılım acil stop» düğmesiyle, bir de "
    "kart bağlantısı koparsa kendiliğinden. Diğer düğmeler kapalı."
)
OBSERVE_REASON = "Salt izleme — makine AXIS'ten kullanılıyor, bu ekrandan komut gönderilmez."
#: LinkHealth.problems_with's step in observer mode, which has no «Yazılım
#: sıfırla» to press (adapter.OBSERVE_LINK_ACTION gives the reason).
OBSERVE_LINK_RESET_STEP = (
    "sonra LinuxCNC'yi yeniden başlatın (salt izlemede bu ekran kart "
    "bağlantısını sıfırlayamaz)"
)


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
        "note": (
            "Acil stop sürücü gücünü DONANIMSAL keser. LinuxCNC sadece haberdar "
            "olur; yazılım güvenlik fonksiyonu değildir."
        ),
    }


def homing_panel(
    state: MachineState, config: MachineConfig, observe: bool = False
) -> Dict[str, Any]:
    """The homing gate. Its button is off in observer mode (`home_enabled`).

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
    if unreadable:
        # EmcIniFile::Find(bool) accepts an integer or TRUE/YES/FALSE/NO
        # (emcIniFile.cc:50-58, inifile.cc:122-139); anything else fails the
        # joint's INI load (inijoint.cc:102, :236-239).
        warning.append(
            "HOME_IGNORE_LIMITS okunamadı: {}. LinuxCNC yalnızca bir tamsayı ya "
            "da TRUE/YES/FALSE/NO kabul eder, bu değerle eklemi yüklemez. "
            "Referans alma sırasında limitlerin yok sayılıp sayılmayacağı "
            "bilinmiyor.".format(", ".join(unreadable))
        )
    return {
        "joints": joints,
        "all_homed": state.homing.all_homed if known else None,
        "order_label": state.homing.order_label,
        "order_source": "[JOINT_n]HOME_SEQUENCE",
        "order_note": homing_order_note(config),
        "shares_home_and_limit": shares_switch,
        "ignore_limits_unknown": bool(unreadable),
        "shares_warning": " ".join(warning),
        "home_enabled": not observe,
        "home_disabled_reason": OBSERVE_REASON if observe else "",
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
    }


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

    In observer mode every control but the software estop is off, with
    OBSERVE_REASON: jog, the feed chips and MDI through their own flags,
    the WCS and jog-step chips through `commands_enabled`.
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
    return {
        "status_known": known,
        "axes": axes,
        "wcs": state.wcs if known else "—",
        "wcs_options": ["G54", "G55", "G56"],
        "jog_steps": [0.01, 0.1, 1.0, 10.0, 50.0],
        "jog_step": state.jog_step_mm,
        "feed_overrides": feeds,
        "feed_override": state.feed_override_pct if known else None,
        "can_jog": state.can_jog and not observe,
        "jog_disabled_reason": (
            OBSERVE_REASON if observe else _motion_off_reason(state, "jog kapalı")
        ),
        "commands_enabled": not observe,
        "commands_disabled_reason": OBSERVE_REASON if observe else "",
        "estop": estop_panel(state, observe),
        "homing": homing_panel(state, config, observe),
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
    """Why jog or MDI is off, "" while MachineState.can_jog allows it.

    An unreadable status is named as such: "devre dışı" would be read off a
    placeholder."""
    if state.can_jog:
        return ""
    if not state.status_known:
        return "Makine durumu bilinmiyor — {}.".format(consequence)
    return "Makine devre dışı — {}.".format(consequence)


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


def mdi_panel(
    state: MachineState, config: MachineConfig, observe: bool = False
) -> Dict[str, Any]:
    """Free text entry plus history. The prototype had presets only; these
    come from mdi_presets. Off in observer mode."""
    return {
        "enabled": state.can_jog and not observe,
        "disabled_reason": (
            OBSERVE_REASON if observe else _motion_off_reason(state, "MDI kabul edilmiyor")
        ),
        "presets": mdi_presets(config),
        "history": [
            {"command": e.command, "accepted": e.accepted, "note": e.note}
            for e in reversed(state.mdi_history[-12:])
        ],
        "placeholder": "MDI komutu yaz ve Enter'a bas",
    }


def program_screen(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    observe: bool = False,
) -> Dict[str, Any]:
    """Program list is empty by design: the program directory is not read
    yet, and no action loads a file (Session.act never calls load_program),
    so there is nothing to pick here. The reason must not call the directory
    empty: the test rig's (nc_files/) holds the Faz 8 program.

    The prototype shipped five invented filenames with invented line counts and
    run times. None of that traces to anything, so none of it is carried over.

    The loaded file and the run state come from LinuxCNC's status, so they
    are None while it is unknown.

    In observer mode start and stop are off: programs run from AXIS.
    """
    known = state.status_known
    gate = gate_panel(state, config, profile, observe)
    return {
        "files": [],
        "files_empty_reason": (
            "Program dizini henüz okunmuyor, bu ekrandan dosya yüklenemez. "
            "PROGRAM_PREFIX = ../../nc_files/ (test-rig.ini)"
        ),
        "loaded": state.program_file if known else None,
        "running": state.program_running if known else None,
        "progress_pct": round(state.program_progress * 100, 1) if known else None,
        "gate": gate,
        "can_start": state.can_start_program and not observe,
        "start_disabled_reason": (
            OBSERVE_REASON if observe else (gate["headline"] or "Program yüklenmedi")
        ),
        "stop_enabled": not observe,
        "stop_disabled_reason": OBSERVE_REASON if observe else "",
        "capabilities": capabilities_for_screen(profile, "program"),
    }


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
# Top level
# ---------------------------------------------------------------------------


def build(
    profile: MachineProfile,
    config: MachineConfig,
    state: MachineState,
    screen: str = "kontrol",
    observe: bool = False,
) -> Dict[str, Any]:
    """The whole view model for one render. `observe`: observer mode
    (app.Session), the banner and every control but the estop off."""
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
        },
        "observe": {
            "active": observe,
            "banner": OBSERVE_BANNER if observe else "",
            "detail": OBSERVE_DETAIL if observe else "",
        },
        "link": link_panel(state, config, observe),
        "kontrol": control_screen(profile, config, state, observe),
        "program": program_screen(profile, config, state, observe),
        "probing": probing_screen(profile),
        "takimlar": tools_screen(profile, config),
        "alarmlar": alarms,
        "makine": machine_screen(profile, config, state, observe),
    }
