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


def link_panel(state: MachineState, config: MachineConfig) -> Dict[str, Any]:
    """Connection health.

    Carries the two indicators the prototype omitted and commissioning actually
    needs: cumulative packet loss and the watchdog bit.
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
            "label": "Paket kaybı",
            "value": "—" if link.packet_error_total is None else str(link.packet_error_total),
            "severity": (
                Severity.INFO.value
                if (link.packet_error_total or 0) == 0
                else Severity.ERROR.value
            ),
            "hal": link.HAL_PACKET_ERROR_TOTAL,
        },
        {
            "label": "Watchdog",
            "value": (
                "—"
                if link.watchdog_has_bit is None
                else ("ISIRDI" if link.watchdog_has_bit else "sessiz")
            ),
            "severity": (
                Severity.ERROR.value if link.watchdog_has_bit else Severity.INFO.value
            ),
            "hal": link.HAL_WATCHDOG_HAS_BIT,
        },
        {
            "label": "io_error",
            "value": (
                "—" if link.io_error is None else ("HATA" if link.io_error else "yok")
            ),
            "severity": Severity.ERROR.value if link.io_error else Severity.INFO.value,
            "hal": link.HAL_IO_ERROR,
        },
        {
            "label": "Servo döngü",
            "value": "{:g} ms".format(link.servo_period_ms),
            "severity": Severity.INFO.value,
            "hal": "",
        },
    ]
    return {
        "rows": rows,
        "healthy": link.healthy,
        "problems": link.problems,
    }


def estop_panel(state: MachineState) -> Dict[str, Any]:
    """Two distinct states, never merged.

    `drive_power` is hardware. `software` is LinuxCNC. The reset button may only
    ever affect the second, and `reset_clears_drive_power` is exposed as False so
    the UI can state that rather than leave it to be inferred.
    """
    e = state.estop
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
        "software_disabled": e.software_disabled,
        "software_label": e.software_label,
        "software_severity": (
            Severity.ERROR.value if e.software_disabled else Severity.INFO.value
        ),
        # The whole point of the panel:
        "reset_clears_drive_power": False,
        "reset_enabled": e.drive_power is not DrivePower.CUT,
        "reset_blocked_reason": (
            "Sürücü gücü donanımsal kesik — yazılım sıfırlaması etkisiz."
            if e.drive_power is DrivePower.CUT
            else ""
        ),
        "note": (
            "Acil stop sürücü gücünü DONANIMSAL keser. LinuxCNC sadece haberdar "
            "olur; yazılım güvenlik fonksiyonu değildir."
        ),
    }


def homing_panel(state: MachineState, config: MachineConfig) -> Dict[str, Any]:
    """The homing gate."""
    joints = [
        {
            "axis": j.axis,
            "joint": j.joint,
            "phase": j.phase.value,
            "label": j.phase_label,
            "homed": j.phase is HomingPhase.HOMED,
            "sequence": j.sequence,
            "ignore_limits": j.ignore_limits,
            "direction": j.direction_label,
            "severity": (
                Severity.INFO.value
                if j.phase is HomingPhase.HOMED
                else Severity.WARN.value
            ),
        }
        for j in state.homing.joints
    ]
    shares_switch = any(j["ignore_limits"] for j in joints)
    return {
        "joints": joints,
        "all_homed": state.homing.all_homed,
        "order_label": state.homing.order_label,
        "order_source": "[JOINT_n]HOME_SEQUENCE",
        "order_note": homing_order_note(config),
        "shares_home_and_limit": shares_switch,
        "shares_warning": (
            "Home ve limit aynı switch'te → HOME_IGNORE_LIMITS = YES. "
            "Referans alma sırasında limitler yok sayılır: switch'in yanlış "
            "tarafından başlanırsa ekseni durduracak hiçbir şey yoktur "
            "(ini-homing.adoc:46)."
            if shares_switch
            else ""
        ),
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


def gate_panel(state: MachineState, config: MachineConfig, profile: MachineProfile) -> Dict[str, Any]:
    """Why the program cannot start, and what is merely risky."""
    warnings: List[str] = []
    if not config.present:
        warnings.append(config.absence_reason)
    elif not config.soft_limits_configured:
        warnings.append(
            "Yumuşak limitler tanımsız — strok ölçülmedi, çarpışma koruması yok."
        )
    for m in profile.missing_switches:
        warnings.append(m.consequence)
    if state.link.problems:
        warnings.extend(state.link.problems)
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


def control_screen(
    profile: MachineProfile, config: MachineConfig, state: MachineState
) -> Dict[str, Any]:
    axes = []
    for pos in state.positions:
        ac = config.axis(pos.axis)
        axes.append(
            {
                "axis": pos.axis,
                "work": "{:+.3f}".format(pos.work),
                "machine": "{:+.3f}".format(pos.machine),
                "homed": pos.homed,
                "travel": ac.travel.display if ac else UNMEASURED,
                "travel_measured": bool(ac and ac.travel.measured),
            }
        )
    return {
        "axes": axes,
        "wcs": state.wcs,
        "wcs_options": ["G54", "G55", "G56"],
        "jog_steps": [0.01, 0.1, 1.0, 10.0, 50.0],
        "jog_step": state.jog_step_mm,
        "feed_overrides": [50, 100, 150],
        "feed_override": state.feed_override_pct,
        "can_jog": state.can_jog,
        "estop": estop_panel(state),
        "homing": homing_panel(state, config),
        "gate": gate_panel(state, config, profile),
        "capabilities": capabilities_for_screen(profile, "kontrol"),
        "mdi": mdi_panel(state),
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


def mdi_panel(state: MachineState) -> Dict[str, Any]:
    """Free text entry plus history. The prototype had presets only."""
    return {
        "enabled": state.can_jog,
        "disabled_reason": (
            "" if state.can_jog else "Makine devre dışı — MDI kabul edilmiyor."
        ),
        "presets": ["G0 Z5", "G53 G0 Z0", "M5", "G92.1"],
        "history": [
            {"command": e.command, "accepted": e.accepted, "note": e.note}
            for e in reversed(state.mdi_history[-12:])
        ],
        "placeholder": "MDI komutu yaz ve Enter'a bas",
    }


def program_screen(
    profile: MachineProfile, config: MachineConfig, state: MachineState
) -> Dict[str, Any]:
    """Program list is empty by design: no .ngc files exist in the repo.

    The prototype shipped five invented filenames with invented line counts and
    run times. None of that traces to anything, so none of it is carried over.
    """
    return {
        "files": [],
        "files_empty_reason": (
            "Program dizini tanımlı değil ve depoda .ngc dosyası yok. "
            "PROGRAM_PREFIX = ../../nc_files/ (test-rig.ini)"
        ),
        "loaded": state.program_file,
        "running": state.program_running,
        "progress_pct": round(state.program_progress * 100, 1),
        "gate": gate_panel(state, config, profile),
        "can_start": state.can_start_program,
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


def alarms_screen(profile: MachineProfile, state: MachineState) -> Dict[str, Any]:
    """Inputs and the event log, both with source attribution."""
    inputs = []
    for inp in profile.inputs:
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
        "missing_switches": missing,
        "events": events,
        "active_count": sum(
            1 for a in state.alarms if a.severity is Severity.ERROR
        ),
        "capabilities": capabilities_for_screen(profile, "alarmlar"),
    }


def machine_screen(
    profile: MachineProfile, config: MachineConfig, state: MachineState
) -> Dict[str, Any]:
    board = profile.board
    plat = profile.platform
    link = link_panel(state, config)
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
                    "value": (
                        "{:g} ms".format(config.servo_period_ms)
                        if config.servo_period_ms
                        else "—"
                    ),
                },
            ]
            if config.present
            else []
        ),
        "axes": axes,
        "timing_rows": config.timing.rows() if config.timing else [],
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
) -> Dict[str, Any]:
    """The whole view model for one render."""
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
        "link": link_panel(state, config),
        "kontrol": control_screen(profile, config, state),
        "program": program_screen(profile, config, state),
        "probing": probing_screen(profile),
        "takimlar": tools_screen(profile, config),
        "alarmlar": alarms,
        "makine": machine_screen(profile, config, state),
    }
