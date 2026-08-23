"""Server-side HTML rendering from the view model.

Rendering on the server rather than in the browser is a deliberate testability
choice: "a disabled capability is rendered, not hidden" becomes a plain string
assertion on the output of a pure function, with no DOM and no browser.

Visual language (palette, nav, tile and card shapes) is carried over from
design/prototype-source.jsx. The facts are not.
"""

from __future__ import annotations

import html
import json
from typing import Any, Dict, Iterable, List, Optional

# Palette, lifted from design/prototype-source.jsx:78-80
BG = "#161826"
SURF = "#1a1c2c"
SURF_HI = "#1e2035"
LINE = "#292b31"
FG = "#e9e9ed"
MUTED = "#9397ab"
MUTED_DIM = "#75798c"
DISABLED_FG = "#595d6c"
DISABLED_LINE = "#3f424d"
ACC = "#9184d9"
ACC_LIGHT = "#b5abfc"
ACC_DEEP = "#5d5294"
ACC_GHOST = "#2b2741"
OK = "#84d9a5"
BAD = "#d9848c"
WARN = "#f0a92b"

SEV_COLOR = {"info": OK, "warn": WARN, "error": BAD}


def e(v: Any) -> str:
    """Escape for HTML text and attributes."""
    return html.escape("" if v is None else str(v), quote=True)


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------


def card(title: str, body: str, sub: str = "", extra_class: str = "") -> str:
    head = '<div class="card-head"><span class="card-title">{}</span>{}</div>'.format(
        e(title),
        '<span class="card-sub">{}</span>'.format(e(sub)) if sub else "",
    )
    return '<section class="card {}">{}{}</section>'.format(
        e(extra_class), head, body
    )


def kv_rows(rows: Iterable[Dict[str, Any]]) -> str:
    out = []
    for r in rows:
        sev = r.get("severity")
        colour = SEV_COLOR.get(sev, "") if sev else ""
        style = ' style="color:{}"'.format(colour) if colour else ""
        note = (
            '<span class="kv-note">{}</span>'.format(e(r["note"]))
            if r.get("note")
            else ""
        )
        hal = (
            '<code class="hal">{}</code>'.format(e(r["hal"])) if r.get("hal") else ""
        )
        out.append(
            '<div class="kv"><span class="kv-l">{}</span>'
            '<span class="kv-v"{}>{}</span>{}{}</div>'.format(
                e(r["label"]), style, e(r["value"]), hal, note
            )
        )
    return '<div class="kv-list">{}</div>'.format("".join(out))


def capability_block(cap: Dict[str, Any]) -> str:
    """Render one capability.

    A capability that is not present is rendered with the `disabled` class and
    its reason visible. It is never omitted - that is the whole contract, and
    tests assert on `data-capability` and `data-presence` being in the output for
    every catalogued capability.
    """
    cls = "cap" if cap["enabled"] else "cap disabled"
    badge_colour = {"present": OK, "absent": MUTED_DIM, "todo": WARN}[cap["presence"]]
    bits = [
        '<div class="{}" data-capability="{}" data-presence="{}" data-enabled="{}">'.format(
            cls, e(cap["key"]), e(cap["presence"]), "1" if cap["enabled"] else "0"
        ),
        '<div class="cap-head"><span class="cap-label">{}</span>'
        '<span class="cap-badge" style="color:{}">{}</span></div>'.format(
            e(cap["label"]), badge_colour, e(cap["badge"])
        ),
        '<div class="cap-blurb">{}</div>'.format(e(cap["blurb"])),
    ]
    if cap["reason"]:
        bits.append('<div class="cap-reason">{}</div>'.format(e(cap["reason"])))
    if cap["requirement"]:
        bits.append(
            '<div class="cap-req"><span>Açmak için:</span> {}</div>'.format(
                e(cap["requirement"])
            )
        )
    if cap["source"]:
        bits.append('<div class="cap-src">{}</div>'.format(e(cap["source"])))
    bits.append("</div>")
    return "".join(bits)


def capability_grid(caps: List[Dict[str, Any]], title: str = "Yetenekler") -> str:
    if not caps:
        return ""
    body = '<div class="cap-grid">{}</div>'.format(
        "".join(capability_block(c) for c in caps)
    )
    return card(title, body, sub="olmayanlar gizlenmez, devre dışı görünür")


def banner(text: str, severity: str = "warn", label: str = "") -> str:
    colour = SEV_COLOR.get(severity, WARN)
    lab = (
        '<span class="banner-label" style="color:{}">{}</span>'.format(colour, e(label))
        if label
        else ""
    )
    return (
        '<div class="banner" data-severity="{}" style="border-color:{}">{}'
        '<span class="banner-text">{}</span></div>'.format(
            e(severity), colour, lab, e(text)
        )
    )


def button(
    label: str,
    action: str = "",
    enabled: bool = True,
    reason: str = "",
    kind: str = "",
    value: str = "",
) -> str:
    attrs = [
        'class="btn {}{}"'.format(kind, "" if enabled else " disabled"),
        'data-action="{}"'.format(e(action)) if action else "",
        'data-value="{}"'.format(e(value)) if value else "",
        "disabled" if not enabled else "",
        'title="{}"'.format(e(reason)) if reason and not enabled else "",
    ]
    return "<button {}>{}</button>".format(
        " ".join(a for a in attrs if a), e(label)
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------


def render_header(vm: Dict[str, Any]) -> str:
    st = vm["status"]
    link = vm["link"]
    colour = SEV_COLOR.get(st["severity"], FG)
    link_bits = []
    for r in link["rows"]:
        c = SEV_COLOR.get(r["severity"], MUTED)
        # The HAL pin goes in title/data-hal so a glance stays compact but the
        # operator can still find out which pin a number came from. The Makine
        # screen lists them in full.
        hal_attrs = (
            ' data-hal="{h}" title="{h}"'.format(h=e(r["hal"])) if r.get("hal") else ""
        )
        link_bits.append(
            '<span class="link-item" data-link="{}"{}><span class="link-l">{}</span>'
            '<span class="link-v" style="color:{}">{}</span></span>'.format(
                e(r["label"]), hal_attrs, e(r["label"]), c, e(r["value"])
            )
        )
    return (
        '<header class="top">'
        '<div class="top-l">'
        '<span class="mach">{mach}</span>'
        '<span class="mach-sub">{sub}</span>'
        "</div>"
        '<div class="top-c">{links}</div>'
        '<div class="top-r">'
        '<span class="state" data-severity="{sev}" style="color:{col};border-color:{col}">{state}</span>'
        "</div>"
        "</header>".format(
            mach=e(vm["profile"]["label"]),
            sub=e(vm["profile"]["subtitle"]),
            links="".join(link_bits),
            sev=e(st["severity"]),
            col=colour,
            state=e(st["label"]),
        )
    )


def render_nav(vm: Dict[str, Any]) -> str:
    items = []
    for n in vm["nav"]:
        badge = (
            '<span class="nav-badge">{}</span>'.format(n["badge"]) if n["badge"] else ""
        )
        items.append(
            '<button class="nav-item{active}" data-action="screen" data-value="{id}">'
            '<span class="nav-dot"></span><span class="nav-label">{label}</span>{badge}'
            "</button>".format(
                active=" active" if n["active"] else "",
                id=e(n["id"]),
                label=e(n["label"]),
                badge=badge,
            )
        )
    return '<nav class="nav">{}</nav>'.format("".join(items))


# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------


def render_estop(p: Dict[str, Any]) -> str:
    """The two-state estop panel."""
    dp_colour = SEV_COLOR.get(p["drive_power_severity"], WARN)
    sw_colour = SEV_COLOR.get(p["software_severity"], OK)
    rows = (
        '<div class="estop-row" data-estop="drive-power" data-value="{dpv}">'
        '<span class="estop-l">Sürücü gücü <em>(donanım)</em></span>'
        '<span class="estop-v" style="color:{dpc}">{dpl}</span>'
        '<span class="estop-d">{dpd}</span>'
        "</div>"
        '<div class="estop-row" data-estop="software">'
        '<span class="estop-l">Yazılım durumu <em>(LinuxCNC)</em></span>'
        '<span class="estop-v" style="color:{swc}">{swl}</span>'
        '<span class="estop-d">Yazılım sıfırlaması yalnızca bu satırı etkiler.</span>'
        "</div>".format(
            dpv=e(p["drive_power"]),
            dpc=dp_colour,
            dpl=e(p["drive_power_label"]),
            dpd=e(p["drive_power_detail"]),
            swc=sw_colour,
            swl=e(p["software_label"]),
        )
    )
    controls = (
        '<div class="estop-controls">'
        + button("Yazılım acil stop", "software_estop", True, kind="danger")
        + button(
            "Yazılım sıfırla",
            "software_reset",
            p["reset_enabled"],
            p["reset_blocked_reason"],
        )
        + "</div>"
    )
    note = banner(p["note"], "warn", "GÜVENLİK")
    extra = ""
    if not p["hardware_input_present"]:
        extra = banner(
            "Bu makinede donanımsal acil stop girişi yok. GUI acil stop hareketi "
            "durdurur ama hiçbir yerden güç kesmez — güç anahtarını elinin "
            "altında tut.",
            "error",
            "GİRİŞ YOK",
        )
    blocked = ""
    if p["reset_blocked_reason"]:
        blocked = banner(p["reset_blocked_reason"], "error", "SIFIRLAMA ETKİSİZ")
    return card(
        "Acil stop", note + extra + blocked + rows + controls,
        sub="donanım ve yazılım ayrı",
    )


def render_homing(p: Dict[str, Any]) -> str:
    chips = []
    for j in p["joints"]:
        colour = SEV_COLOR.get(j["severity"], WARN)
        chips.append(
            '<div class="home-chip" data-axis="{ax}" data-phase="{ph}" '
            'style="border-color:{c}">'
            '<span class="home-ax">{ax}</span>'
            '<span class="home-st" style="color:{c}">{lab}</span>'
            '<span class="home-seq">sıra {seq}</span>'
            '<span class="home-dir">{dir}</span>'
            "</div>".format(
                ax=e(j["axis"]),
                ph=e(j["phase"]),
                c=colour,
                lab=e(j["label"]),
                seq=j["sequence"],
                dir=e(j["direction"]),
            )
        )
    order = (
        '<div class="home-order">Gerekli sıra: <strong>{}</strong> '
        '<span class="src">({})</span></div>'.format(
            e(p["order_label"]), e(p["order_source"])
        )
    )
    note = banner(p["order_note"], "warn", "TUTARSIZLIK") if p["order_note"] else ""
    shares = (
        banner(p["shares_warning"], "error", "HOME = LIMIT")
        if p["shares_warning"]
        else ""
    )
    btn = '<div class="row">{}</div>'.format(button("Tümünü referansla", "home_all"))
    return card(
        "Referans alma",
        order + note + shares + '<div class="home-chips">{}</div>'.format("".join(chips)) + btn,
        sub="referans alınmadan program başlamaz",
    )


def render_gate(p: Dict[str, Any]) -> str:
    bits = []
    if p["blocks"]:
        bits.append(
            banner(
                "Program başlatma engelli: {}".format(p["blocks"][0]),
                "error",
                "ENGELLİ",
            )
        )
        bits.append(
            '<ul class="gate-list">{}</ul>'.format(
                "".join("<li>{}</li>".format(e(b)) for b in p["blocks"])
            )
        )
    else:
        bits.append(banner("Program başlatılabilir.", "info", "HAZIR"))
    if p["warnings"]:
        bits.append(
            '<div class="gate-warn-title">Uyarılar</div><ul class="gate-list warn">{}</ul>'.format(
                "".join("<li>{}</li>".format(e(w)) for w in p["warnings"])
            )
        )
    return card("Durum kapısı", "".join(bits), sub="ne engelliyor, ne riskli")


def render_dro(c: Dict[str, Any]) -> str:
    rows = []
    for a in c["axes"]:
        travel_cls = "ok" if a["travel_measured"] else "unmeasured"
        rows.append(
            '<div class="dro-row" data-axis="{ax}">'
            '<span class="dro-ax">{ax}</span>'
            '<span class="dro-work">{work}</span>'
            '<span class="dro-mach">{mach}</span>'
            '<span class="dro-travel {tc}" data-travel-measured="{tm}">{travel}</span>'
            '<span class="dro-home" style="color:{hc}">{hs}</span>'
            "</div>".format(
                ax=e(a["axis"]),
                work=e(a["work"]),
                mach=e(a["machine"]),
                tc=travel_cls,
                tm="1" if a["travel_measured"] else "0",
                travel=e(a["travel"]),
                hc=OK if a["homed"] else WARN,
                hs="ref" if a["homed"] else "ref yok",
            )
        )
    head = (
        '<div class="dro-row head"><span>Eksen</span><span>İş ({})</span>'
        "<span>Makine</span><span>Strok</span><span></span></div>".format(e(c["wcs"]))
    )
    wcs = '<div class="row">{}</div>'.format(
        "".join(
            button(w, "wcs", True, value=w, kind="chip" + (" on" if w == c["wcs"] else ""))
            for w in c["wcs_options"]
        )
    )
    return card("Konum", head + "".join(rows) + wcs, sub=e(c["wcs"]))


def render_jog(c: Dict[str, Any]) -> str:
    def jb(label: str, axis: str, direction: int) -> str:
        if not axis:
            return '<span class="jog-empty"></span>'
        return button(
            label,
            "jog",
            c["can_jog"],
            "" if c["can_jog"] else "Makine devre dışı",
            kind="jog",
            value="{}:{}".format(axis, direction),
        )

    pad = "".join(
        [
            jb("", "", 0), jb("Y+", "Y", 1), jb("", "", 0),
            jb("X−", "X", -1), jb("⌖", "", 0), jb("X+", "X", 1),
            jb("", "", 0), jb("Y−", "Y", -1), jb("", "", 0),
        ]
    )
    zcol = "".join([jb("Z+", "Z", 1), '<span class="jog-empty"></span>', jb("Z−", "Z", -1)])
    steps = "".join(
        button(
            "{:g}".format(s), "jog_step", True, value=str(s),
            kind="chip" + (" on" if abs(s - c["jog_step"]) < 1e-9 else ""),
        )
        for s in c["jog_steps"]
    )
    feeds = "".join(
        button(
            "{}%".format(f), "feed", True, value=str(f),
            kind="chip" + (" on" if f == c["feed_override"] else ""),
        )
        for f in c["feed_overrides"]
    )
    body = (
        '<div class="jog-wrap"><div class="jog-pad">{pad}</div>'
        '<div class="jog-z">{z}</div></div>'
        '<div class="row-label">Adım (mm)</div><div class="row">{steps}</div>'
        '<div class="row-label">İlerleme oranı</div><div class="row">{feeds}</div>'.format(
            pad=pad, z=zcol, steps=steps, feeds=feeds
        )
    )
    if not c["can_jog"]:
        body = banner("Jog devre dışı — makine kapalı.", "warn") + body
    return card("Jog", body)


def render_tiles(tiles: List[Dict[str, Any]]) -> str:
    out = []
    for t in tiles:
        cls = "tile" if t["enabled"] else "tile disabled"
        reason = (
            '<span class="tile-reason">{}</span>'.format(e(t["reason"]))
            if t["reason"]
            else ""
        )
        out.append(
            '<div class="{cls}" data-enabled="{en}">'
            '<span class="tile-l">{label}</span>'
            '<span class="tile-v">{value}</span>'
            '<span class="tile-s">{sub}</span>{reason}</div>'.format(
                cls=cls,
                en="1" if t["enabled"] else "0",
                label=e(t["label"]),
                value=e(t["value"]),
                sub=e(t["sub"]),
                reason=reason,
            )
        )
    return '<div class="tiles">{}</div>'.format("".join(out))


def render_mdi(m: Dict[str, Any]) -> str:
    """MDI with a real text field. The prototype had presets only."""
    form = (
        '<form class="mdi-form" data-action="mdi">'
        '<input class="mdi-input" name="mdi" type="text" '
        'placeholder="{ph}" autocomplete="off" spellcheck="false"{dis}>'
        "{btn}</form>".format(
            ph=e(m["placeholder"]),
            dis="" if m["enabled"] else " disabled",
            btn=button("Gönder", "mdi_submit", m["enabled"], m["disabled_reason"]),
        )
    )
    presets = '<div class="row">{}</div>'.format(
        "".join(
            button(p, "mdi_preset", m["enabled"], m["disabled_reason"], kind="chip", value=p)
            for p in m["presets"]
        )
    )
    hist_items = "".join(
        '<li data-accepted="{acc}"><code>{cmd}</code><span class="mdi-note" '
        'style="color:{col}">{note}</span></li>'.format(
            acc="1" if h["accepted"] else "0",
            cmd=e(h["command"]),
            col=OK if h["accepted"] else BAD,
            note=e(h["note"]),
        )
        for h in m["history"]
    )
    hist = (
        '<div class="row-label">Geçmiş</div><ul class="mdi-hist">{}</ul>'.format(hist_items)
        if hist_items
        else '<div class="row-label">Geçmiş</div><div class="empty">henüz komut yok</div>'
    )
    body = form + presets + hist
    if not m["enabled"]:
        body = banner(m["disabled_reason"], "warn") + body
    return card("MDI", body, sub="serbest komut girişi")


def render_kontrol(vm: Dict[str, Any]) -> str:
    c = vm["kontrol"]
    return (
        '<div class="grid">'
        '<div class="col">{dro}{jog}</div>'
        '<div class="col">{estop}{homing}</div>'
        '<div class="col">{gate}{mdi}</div>'
        "</div>"
        "{tiles}{caps}".format(
            dro=render_dro(c),
            jog=render_jog(c),
            estop=render_estop(c["estop"]),
            homing=render_homing(c["homing"]),
            gate=render_gate(c["gate"]),
            mdi=render_mdi(c["mdi"]),
            tiles=render_tiles(c["tiles"]),
            caps=capability_grid(c["capabilities"], "Bu ekranın yetenekleri"),
        )
    )


def render_program(vm: Dict[str, Any]) -> str:
    p = vm["program"]
    files = (
        '<div class="empty">{}</div>'.format(e(p["files_empty_reason"]))
        if not p["files"]
        else ""
    )
    start = button(
        "Başlat",
        "program_start",
        p["can_start"],
        p["gate"]["headline"] or "Program yüklenmedi",
        kind="primary",
    )
    stop = button("Durdur", "program_stop", True)
    loaded = (
        '<div class="kv"><span class="kv-l">Yüklü</span><span class="kv-v">{}</span></div>'.format(
            e(p["loaded"] or "—")
        )
    )
    return (
        '<div class="grid">'
        '<div class="col">{files_card}</div>'
        '<div class="col">{gate}</div>'
        "</div>{caps}".format(
            files_card=card(
                "Programlar",
                files + loaded + '<div class="row">{}{}</div>'.format(start, stop),
                sub="referans alınmadan başlamaz",
            ),
            gate=render_gate(p["gate"]),
            caps=capability_grid(p["capabilities"], "Bu ekranın yetenekleri"),
        )
    )


def render_probing(vm: Dict[str, Any]) -> str:
    p = vm["probing"]
    head = ""
    if not p["enabled"]:
        head = banner(p["disabled_reason"], "warn", "PROB YOK") + banner(
            "Açmak için: {}".format(p["requirement"]), "info", "GEREKEN"
        )
    modes = []
    for m in p["modes"]:
        steps = "".join(
            '<li><span class="step-no">{}</span><span class="step-t">{}</span></li>'.format(
                i + 1, e(s)
            )
            for i, s in enumerate(m["steps"])
        )
        modes.append(
            '<div class="probe-mode{dis}" data-mode="{id}" data-enabled="{en}">'
            '<div class="probe-name">{name}</div>'
            '<div class="probe-desc">{desc}</div>'
            '<ol class="probe-steps">{steps}</ol>'
            "{btn}</div>".format(
                dis="" if p["enabled"] else " disabled",
                id=e(m["id"]),
                en="1" if p["enabled"] else "0",
                name=e(m["name"]),
                desc=e(m["desc"]),
                steps=steps,
                btn=button(
                    "Probing başlat", "probe_start", p["enabled"], p["disabled_reason"]
                ),
            )
        )
    params = kv_rows(p["params"]) + '<div class="cap-reason">{}</div>'.format(
        e(p["params_note"])
    )
    return (
        head
        + banner(p["probe_is_not_a_tool"], "info", "NOT")
        + '<div class="probe-grid">{}</div>'.format("".join(modes))
        + card("Prob parametreleri", params)
        + capability_grid(p["capabilities"], "Bu ekranın yetenekleri")
    )


def render_takimlar(vm: Dict[str, Any]) -> str:
    t = vm["takimlar"]
    body = '<div class="empty">{}</div>'.format(e(t["empty_reason"]))
    note = banner(t["probe_note"], "info", "PROB ≠ TAKIM")
    path = (
        '<div class="cap-src">TOOL_TABLE: {}</div>'.format(e(t["table_path"]))
        if t["table_path"]
        else ""
    )
    return (
        note
        + card("Takım tablosu", body + path, sub="boş — uydurulmadı")
        + capability_grid(t["capabilities"], "Bu ekranın yetenekleri")
    )


def render_alarmlar(vm: Dict[str, Any]) -> str:
    a = vm["alarmlar"]
    if a["inputs"]:
        rows = [
            '<div class="in-row head"><span>Pin</span><span>İşlev</span>'
            "<span>HAL</span><span>Polarite</span></div>"
        ]
        for i in a["inputs"]:
            rows.append(
                '<div class="in-row" data-pin="{pin}" data-readonly="1">'
                '<span class="in-pin">{pin}</span>'
                '<span class="in-label">{label}</span>'
                '<code class="hal">{hal}</code>'
                '<span class="in-pol">{pol}</span>'
                "</div>".format(
                    pin=e(i["pin"]),
                    label=e(i["label"]),
                    hal=e(i["hal_pin"]),
                    pol=e(i["polarity_note"]),
                )
            )
        inputs_body = "".join(rows) + banner(
            "Girişler salt okunur — makinenin bize söylediği şeydir, ayarlanmaz.",
            "info",
        )
    else:
        inputs_body = '<div class="empty">{}</div>'.format(e(a["inputs_empty_reason"]))

    missing = ""
    if a["missing_switches"]:
        missing = "".join(
            banner(
                "{}: {}".format(m["label"], m["consequence"]), "error", "SWITCH YOK"
            )
            for m in a["missing_switches"]
        )

    events = "".join(
        '<div class="ev" data-severity="{sev}">'
        '<span class="ev-t">{time}</span>'
        '<span class="ev-x" style="color:{col}">{text}</span>'
        '<code class="ev-src">{src}</code></div>'.format(
            sev=e(ev["severity"]),
            time=e(ev["time"]),
            col=SEV_COLOR.get(ev["severity"], FG),
            text=e(ev["text"]),
            src=e(ev["source"]),
        )
        for ev in a["events"]
    ) or '<div class="empty">kayıt yok</div>'

    return (
        '<div class="grid">'
        '<div class="col wide">{inputs}</div>'
        '<div class="col wide">{log}</div>'
        "</div>{caps}".format(
            inputs=card("Girişler", missing + inputs_body, sub="elle doğrulandı"),
            log=card("Olay kaydı", events, sub="kaynak atfıyla"),
            caps=capability_grid(a["capabilities"], "Bu ekranın yetenekleri"),
        )
    )


def render_makine(vm: Dict[str, Any]) -> str:
    m = vm["makine"]
    board = card(
        "Kart", kv_rows(m["board_rows"])
        + '<div class="cap-src">{}</div>'.format(e(m["board_source"])),
        sub="IDROM'dan doğrulandı",
    )
    link = card(
        "Bağlantı sağlığı",
        kv_rows(m["link_rows"])
        + (
            "".join(banner(p, "error") for p in m["link_problems"])
            if m["link_problems"]
            else banner("Bağlantı sağlıklı.", "info")
        ),
        sub="Ethernet kartının birincil göstergeleri",
    )
    plat = card("Platform", kv_rows(m["platform_rows"]), sub="çalışan ortam")

    if m["config_present"]:
        cfg = card("Config", kv_rows(m["config_rows"]))
        rows = [
            '<div class="ax-row head"><span>Eksen</span><span>Ölçek</span>'
            "<span>Hız</span><span>İvme</span><span>Strok</span>"
            "<span>Adım frek.</span></div>"
        ]
        for a in m["axes"]:
            rows.append(
                '<div class="ax-row" data-axis="{ax}">'
                '<span>{ax}</span><span>{sc}</span><span>{v}</span>'
                '<span>{acc}</span>'
                '<span class="{tc}" data-travel-measured="{tm}">{tr}</span>'
                "<span>{sr}</span></div>".format(
                    ax=e(a["axis"]),
                    sc=e(a["scale"]),
                    v=e(a["velocity"]),
                    acc=e(a["acceleration"]),
                    tc="ok" if a["travel_measured"] else "unmeasured",
                    tm="1" if a["travel_measured"] else "0",
                    tr=e(a["travel"]),
                    sr=e(a["step_rate"]),
                )
            )
        axes_card = card("Eksenler", "".join(rows), sub="INI'den okundu")
        timing = card(
            "Step zamanlaması",
            kv_rows(m["timing_rows"])
            + '<div class="cap-reason">{}</div>'.format(e(m["timing_note"])),
        )
    else:
        cfg = card("Config", '<div class="empty">{}</div>'.format(e(m["config_absence_reason"])))
        axes_card = card("Eksenler", '<div class="empty">config yok</div>')
        timing = card("Step zamanlaması", '<div class="empty">config yok</div>')

    warn = (
        banner(m["soft_limits_warning"], "error", "KORUMA YOK")
        if m["soft_limits_warning"]
        else ""
    )
    missing = "".join(
        banner("{}: {}".format(x["label"], x["consequence"]), "error", "SWITCH YOK")
        for x in m["missing_switches"]
    )
    notes = "".join(banner(n, "warn", "NOT") for n in m["profile_notes"])

    return (
        warn
        + missing
        + notes
        + '<div class="grid"><div class="col">{}</div><div class="col">{}</div>'
        '<div class="col">{}</div><div class="col">{}</div></div>'.format(
            board, plat, cfg, link
        )
        + axes_card
        + timing
        + capability_grid(m["capabilities"], "Tüm yetenekler")
    )


SCREEN_RENDERERS = {
    "kontrol": render_kontrol,
    "program": render_program,
    "probing": render_probing,
    "takimlar": render_takimlar,
    "alarmlar": render_alarmlar,
    "makine": render_makine,
}


def render_screen(vm: Dict[str, Any]) -> str:
    fn = SCREEN_RENDERERS.get(vm["screen"], render_kontrol)
    return fn(vm)


def render_body(vm: Dict[str, Any]) -> str:
    """Header + nav + active screen. This is what the poll swaps in."""
    msg = vm["status"]["message"]
    return (
        render_header(vm)
        + render_nav(vm)
        + '<main class="main" data-screen="{}">{}</main>'.format(
            e(vm["screen"]), render_screen(vm)
        )
        + '<footer class="msg">{}</footer>'.format(e(msg))
    )


def render_page(vm: Dict[str, Any], css: str, js: str) -> str:
    return (
        "<!doctype html>\n"
        '<html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>better-cnc · {title}</title>"
        "<style>{css}</style></head>"
        '<body><div id="app">{body}</div>'
        "<script>window.__VM__={vmjson};</script>"
        "<script>{js}</script></body></html>".format(
            title=e(vm["profile"]["label"]),
            css=css,
            body=render_body(vm),
            vmjson=json.dumps({"screen": vm["screen"]}),
            js=js,
        )
    )
