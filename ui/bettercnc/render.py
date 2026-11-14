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

#: An MDI history entry's colour by its status (state.MDI_*): sent and
#: waiting for its result is yellow, done green, refused or rejected red.
MDI_STATUS_COLOR = {"done": OK, "sent": WARN, "rejected": BAD, "refused": BAD}


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
    # LinuxCNC's limit override on (viewmodel.limit_override_badge): red,
    # beside the state, on every screen.
    override = st.get("limit_override") or {}
    badge = (
        '<span class="override-badge" data-override="1" role="status">{}</span>'.format(
            e(override["label"]))
        if override.get("active")
        else ""
    )
    return (
        '<header class="top">'
        '<div class="top-l">'
        '<span class="mach">{mach}</span>'
        '<span class="mach-sub">{sub}</span>'
        "</div>"
        '<div class="top-c">{links}</div>'
        '<div class="top-r">'
        "{badge}"
        '<span class="state" data-severity="{sev}" style="color:{col};border-color:{col}">{state}</span>'
        "</div>"
        "</header>".format(
            mach=e(vm["profile"]["label"]),
            sub=e(vm["profile"]["subtitle"]),
            links="".join(link_bits),
            badge=badge,
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
    """The two-state estop panel: the drive-power and software rows, and
    «Yazılım sıfırla», small and apart. The estop button is not here but on
    the stop rail (render_rail), on every screen."""
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
        + button(
            "Yazılım sıfırla",
            "software_reset",
            p["reset_enabled"],
            p["reset_disabled_reason"],
            kind="chip",
        )
        + '<span class="estop-where">Acil stop: sağdaki şeritte, her ekranda.</span>'
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


def render_limit(p: Optional[Dict[str, Any]]) -> str:
    """The limit card (viewmodel.limit_panel), only while a switch reads
    pressed: each switch by label and HAL pin, then «Limitten kurtar: X+
    yönünde 3 mm» - what will move, which way, how far - and, when it is
    off, why, as text under it (the kiosk removes title tooltips). Always
    first in the middle column of Kontrol, above the estop card."""
    if not p:
        return ""
    rows = "".join(
        '<div class="limit-row" data-axis="{ax}" data-end="{end}">'
        '<span class="limit-label">{text}</span>'
        '<code class="hal">{hal}</code></div>'.format(
            ax=e(s["axis"]), end=e(s["end"]), text=e(s["text"]), hal=e(s["hal_pin"]))
        for s in p["switches"]
    )
    stale = banner(p["stale_note"], "warn") if p["stale"] else ""
    btn = '<div class="row">{}</div>'.format(
        button(p["button_label"], "recover_limit", p["enabled"], p["disabled_reason"],
               kind="primary limit-btn")
    )
    why = (
        '<div class="cap-reason">{}</div>'.format(e(p["disabled_reason"]))
        if not p["enabled"] and p["disabled_reason"]
        else ""
    )
    others = (
        '<div class="limit-note">{}</div>'.format(e(p["others_note"])) if p["others_note"] else ""
    )
    return card(
        "Limit switch",
        stale + rows + btn + why + others + '<div class="limit-note">{}</div>'.format(e(p["note"])),
        sub="eksen switch'te — makine açılmaz",
        extra_class="limit",
    )


def render_homing(p: Dict[str, Any]) -> str:
    """The homing card (viewmodel.homing_panel): the order, the HOME =
    LIMIT warning, a chip per joint with its own «<axis> referansla»
    button (home_joint, the axis letter as the value), «Tümünü referansla»
    (home_all), and - when they are off - why, as text under them (the
    kiosk removes title tooltips); while a joint homes, what DURDUR does
    and can leave. A press that must be asked about first gets its
    question on the confirmation band (render_confirm)."""
    buttons = p.get("axis_buttons", {})
    chips = []
    for j in p["joints"]:
        colour = SEV_COLOR.get(j["severity"], WARN)
        b = buttons.get(j["axis"])
        home_one = (
            button(b["label"], "home_joint", b["enabled"], b["reason"], kind="chip home-btn",
                   value=j["axis"])
            if b else ""
        )
        chips.append(
            '<div class="home-chip" data-axis="{ax}" data-phase="{ph}" '
            'style="border-color:{c}">'
            '<span class="home-ax">{ax}</span>'
            '<span class="home-st" style="color:{c}">{lab}</span>'
            '<span class="home-seq">sıra {seq}</span>'
            '<span class="home-dir">{dir}</span>'
            "{btn}</div>".format(
                ax=e(j["axis"]),
                ph=e(j["phase"]),
                c=colour,
                lab=e(j["label"]),
                seq="?" if j["sequence"] is None else j["sequence"],
                dir=e(j["direction"]),
                btn=home_one,
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
    btn = '<div class="row">{}</div>'.format(
        button("Tümünü referansla", "home_all", p["home_enabled"], p["home_disabled_reason"])
    )
    why = "".join(
        '<div class="cap-reason home-reason">{}</div>'.format(e(r)) for r in p.get("reasons", [])
    )
    stop = banner(p["stop_note"], "warn", "DURDUR") if p.get("stop_note") else ""
    return card(
        "Referans alma",
        order + note + shares + '<div class="home-chips">{}</div>'.format("".join(chips))
        + btn + why + stop,
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
    elif p.get("run"):
        # Plan WP09: a program running or paused never reads "HAZIR".
        bits.append(banner(p["run"]["text"], p["run"]["kind"], p["run"]["tag"]))
    elif p.get("program"):
        # Plan WP08: nothing blocks the machine, but no program is loaded.
        bits.append(banner(p["program"], "warn", "PROGRAM YOK"))
    else:
        bits.append(banner("Program başlatılabilir.", "info", "HAZIR"))
    if p["blocks"] and p.get("program"):
        bits.append('<div class="cap-reason gate-program">{}</div>'.format(e(p["program"])))
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
        # homed is None while LinuxCNC's status is unknown
        # (viewmodel.control_screen): "ref yok" would claim a reading.
        if a["homed"] is None:
            hc, hs = MUTED, "—"
        elif a["homed"]:
            hc, hs = OK, "ref"
        else:
            hc, hs = WARN, "ref yok"
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
                hc=hc,
                hs=hs,
            )
        )
    head = (
        '<div class="dro-row head"><span>Eksen</span><span>İş ({})</span>'
        "<span>Makine</span><span>Strok</span><span></span></div>".format(e(c["wcs"]))
    )
    wcs = '<div class="row">{}</div>'.format(
        "".join(
            button(
                w, "wcs", c["commands_enabled"], c["commands_disabled_reason"], value=w,
                kind="chip" + (" on" if w == c["wcs"] else ""),
            )
            for w in c["wcs_options"]
        )
    )
    return card("Konum", head + "".join(rows) + wcs, sub=e(c["wcs"]))


def render_jog(c: Dict[str, Any]) -> str:
    """The jog card (plan WP06): the pad, each button on or off by its own
    reason (viewmodel.jog_pad), the reasons written out under it - the
    kiosk removes title tooltips - then the soft-limit line, the step chips
    (the 50 mm chip off while a joint is unhomed, its reason under them)
    and the feed chips. While the whole card is off, a banner says why and
    the reasons under the pad are not repeated."""
    pad_state = c["jog_pad"]["buttons"]

    def jb(label: str, axis: str, direction: int) -> str:
        if not axis:
            return '<span class="jog-empty"></span>'
        key = "{}:{}".format(axis, direction)
        b = pad_state.get(key, {"enabled": False, "reason": c["jog_disabled_reason"]})
        return button(
            label,
            "jog",
            b["enabled"],
            "" if b["enabled"] else b["reason"],
            kind="jog",
            value=key,
        )

    pad = "".join(
        [
            jb("", "", 0), jb("Y+", "Y", 1), jb("", "", 0),
            jb("X−", "X", -1), jb("⌖", "", 0), jb("X+", "X", 1),
            jb("", "", 0), jb("Y−", "Y", -1), jb("", "", 0),
        ]
    )
    zcol = "".join([jb("Z+", "Z", 1), '<span class="jog-empty"></span>', jb("Z−", "Z", -1)])
    pad_note = "".join(
        '<div class="cap-reason jog-reason">{}</div>'.format(e(r))
        for r in (c["jog_pad"]["reasons"] if c["can_jog"] else [])
    )
    limits = '<div class="jog-limits">{}</div>'.format(e(c["soft_limits"]))
    steps = "".join(
        button(
            "{:g}".format(s["mm"]), "jog_step", s["enabled"], s["reason"],
            value=str(s["mm"]), kind="chip" + (" on" if s["on"] else ""),
        )
        for s in c["jog_step_chips"]
    )
    # A disabled step chip's reason is written out too, once, unless the
    # card's banner already says it (observer mode).
    step_reasons: List[str] = []
    for s in c["jog_step_chips"]:
        if (not s["enabled"] and s["reason"] not in step_reasons
                and s["reason"] != c["jog_disabled_reason"]):
            step_reasons.append(s["reason"])
    step_note = "".join(
        '<div class="cap-reason">{}</div>'.format(e(r)) for r in step_reasons
    )
    feeds = "".join(
        button(
            "{}%".format(f["pct"]), "feed", f["enabled"], f["reason"], value=str(f["pct"]),
            kind="chip" + (" on" if f["pct"] == c["feed_override"] else ""),
        )
        for f in c["feed_overrides"]
    )
    # A disabled chip's reason is also written out: the title tooltip alone
    # does not show on a touch screen. Not twice, though: in observer mode
    # the chips give the jog banner's own reason.
    feed_reasons = [c["jog_disabled_reason"]] if not c["can_jog"] else []
    shown = len(feed_reasons)
    for f in c["feed_overrides"]:
        if not f["enabled"] and f["reason"] not in feed_reasons:
            feed_reasons.append(f["reason"])
    feed_reasons = feed_reasons[shown:]
    feed_note = "".join(
        '<div class="cap-reason">{}</div>'.format(e(r)) for r in feed_reasons
    )
    body = (
        '<div class="jog-wrap"><div class="jog-pad">{pad}</div>'
        '<div class="jog-z">{z}</div></div>'
        "{pad_note}{limits}"
        '<div class="row-label">Adım (mm)</div><div class="row">{steps}</div>{step_note}'
        '<div class="row-label">İlerleme oranı</div><div class="row">{feeds}</div>'
        "{note}".format(pad=pad, z=zcol, pad_note=pad_note, limits=limits, steps=steps,
                        step_note=step_note, feeds=feeds, note=feed_note)
    )
    if not c["can_jog"]:
        # The reason comes from the view model, which says "bilinmiyor" when the
        # status could not be read - "makine kapalı" would be read off a placeholder.
        body = banner(c["jog_disabled_reason"], "warn") + body
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
        '<li data-accepted="{acc}" data-status="{st}"><code>{cmd}</code>'
        '<span class="mdi-note" style="color:{col}">{note}</span></li>'.format(
            acc="1" if h["accepted"] else "0",
            st=e(h["status"]),
            cmd=e(h["command"]),
            col=MDI_STATUS_COLOR.get(h["status"], BAD),
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
        '<div class="col">{limit}{estop}{homing}</div>'
        '<div class="col">{gate}{mdi}</div>'
        "</div>"
        "{tiles}{caps}".format(
            dro=render_dro(c),
            jog=render_jog(c),
            limit=render_limit(c.get("limit")),
            estop=render_estop(c["estop"]),
            homing=render_homing(c["homing"]),
            gate=render_gate(c["gate"]),
            mdi=render_mdi(c["mdi"]),
            tiles=render_tiles(c["tiles"]),
            caps=capability_grid(c["capabilities"], "Bu ekranın yetenekleri"),
        )
    )


def render_program(vm: Dict[str, Any]) -> str:
    """The Program screen (viewmodel.program_screen, plan WP08): a row per
    listed file with «Yükle» (load_program, the file's name as the value)
    and a «YÜKLÜ» tag on the loaded one; why the list is empty or loading
    is off, as text (the kiosk removes title tooltips); the loaded file -
    name, whole path in small type, line count; «Yeniden yükle»
    (reload_program), «Listeyi yenile» (refresh_programs); «Başlat» and
    «Durdur» with «Başlat»'s reason as text when it is off.

    Plan WP09: the start row's first button is the one primary by what
    runs (p["primary"]: «Başlat», «Duraklat» or «Devam»), then «Adım» and
    «Durdur»; each one's reason when off is text (div.cap-reason:
    start-reason for «Başlat», run-reason for «Duraklat»/«Devam»,
    step-reason for «Adım», said once if it repeats the first). While a
    program runs or is paused, a run line: «Satır N / M», the percentage
    and the line's text."""
    p = vm["program"]
    rows = []
    for f in p["files"]:
        rows.append(
            '<div class="prog-row" data-file="{name}" data-loaded="{ld}">'
            '<span class="prog-name">{name}</span>'
            '<span class="prog-meta">{size} · {date}</span>'
            "{tag}{btn}{why}</div>".format(
                name=e(f["name"]),
                ld="1" if f["loaded"] else "0",
                size=e(f["size_text"]),
                date=e(f["date"]),
                tag='<span class="prog-tag">YÜKLÜ</span>' if f["loaded"] else "",
                btn=button("Yükle", "load_program", f["enabled"],
                           f["reason"] or p["load_disabled_reason"], kind="prog-btn",
                           value=f["name"]),
                why=('<div class="cap-reason prog-reason">{}</div>'.format(e(f["reason"]))
                     if f["reason"] else ""),
            )
        )
    files = (
        '<div class="prog-list">{}</div>'.format("".join(rows)) if rows
        else '<div class="empty prog-empty">{}</div>'.format(e(p["files_empty_reason"]))
    )
    if p.get("files_count"):
        # P0 cleanup: the count, and «Önceki» / «Sonraki» with more than one
        # page (app.js keeps the page; nothing goes to the server's actions).
        nav = ""
        if p.get("pages", 1) > 1:
            prev, nxt = p.get("page_prev"), p.get("page_next")
            nav = (button("Önceki", "program_page", prev is not None, "İlk sayfa",
                          kind="chip prog-page", value="" if prev is None else str(prev))
                   + button("Sonraki", "program_page", nxt is not None, "Son sayfa",
                            kind="chip prog-page", value="" if nxt is None else str(nxt)))
        files += '<div class="row prog-pages">{}<span class="cap-src prog-count">{}</span></div>'.format(
            nav, e(p["files_count"]))
    if p["files_note"]:
        files += '<div class="cap-src prog-note">{}</div>'.format(e(p["files_note"]))
    if p["dir"]:
        files = '<div class="cap-src prog-dir">Dizin: {}</div>'.format(e(p["dir"])) + files
    if p["files"] and p["load_disabled_reason"]:
        files += '<div class="cap-reason prog-reason">{}</div>'.format(
            e(p["load_disabled_reason"]))
    lines = p.get("loaded_lines")
    loaded = (
        '<div class="kv"><span class="kv-l">Yüklü</span><span class="kv-v">{}</span></div>'.format(
            e(p["loaded"] or "—")
        )
    )
    if p.get("loaded_path"):
        loaded += '<div class="prog-path">{}{}</div>'.format(
            e(p["loaded_path"]),
            " · {} satır".format(lines) if lines is not None else "",
        )
    tools = '<div class="row">{}{}</div>'.format(
        button("Yeniden yükle", "reload_program", p["reload_enabled"],
               p["reload_disabled_reason"]),
        button("Listeyi yenile", "refresh_programs", p["refresh_enabled"],
               p["refresh_disabled_reason"]),
    )
    if not p["reload_enabled"] and p["reload_disabled_reason"] not in (
            "", p["load_disabled_reason"]):
        tools += '<div class="cap-reason reload-reason">Yeniden yükle kapalı: {}</div>'.format(
            e(p["reload_disabled_reason"]))
    primary = p["primary"]
    start = button(primary["label"], primary["action"], primary["enabled"],
                   primary["reason"], kind="primary")
    step = button("Adım", "program_step", p["step_enabled"], p["step_disabled_reason"])
    stop = button("Durdur", "program_stop", p["stop_enabled"], p["stop_disabled_reason"])
    start_why = ""
    if not primary["enabled"] and primary["reason"]:
        start_why = '<div class="cap-reason {cls}">{label} kapalı: {why}</div>'.format(
            cls="start-reason" if primary["action"] == "program_start" else "run-reason",
            label=e(primary["label"]), why=e(primary["reason"]))
    if not p["step_enabled"] and p["step_disabled_reason"] not in ("", primary["reason"]):
        start_why += '<div class="cap-reason step-reason">Adım kapalı: {}</div>'.format(
            e(p["step_disabled_reason"]))
    run_line = ""
    if p.get("line"):
        bits = ["Satır {}{}".format(
            p["line"], " / {}".format(lines) if lines is not None else "")]
        if p.get("progress_pct") is not None and lines:
            bits.append("%{}".format(p["progress_pct"]).replace(".", ","))
        run_line = '<div class="prog-run" data-line="{}">{}{}</div>'.format(
            p["line"], e(" · ".join(bits)),
            '<code class="prog-line">{}</code>'.format(e(p["line_text"]))
            if p.get("line_text") else "")
    return (
        '<div class="grid">'
        '<div class="col">{files_card}</div>'
        '<div class="col">{gate}</div>'
        "</div>{caps}".format(
            files_card=card(
                "Programlar",
                files + loaded + tools + run_line
                + '<div class="row prog-run-row">{}{}{}</div>'.format(start, step, stop)
                + start_why,
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
            "<span>HAL</span><span>Polarite</span><span>Durum</span></div>"
        ]
        for i in a["inputs"]:
            rows.append(
                '<div class="in-row" data-pin="{pin}" data-readonly="1" data-state="{st}">'
                '<span class="in-pin">{pin}</span>'
                '<span class="in-label">{label}</span>'
                '<code class="hal">{hal}</code>'
                '<span class="in-pol">{pol}</span>'
                '<span class="in-state" style="color:{col}">{state}</span>'
                "</div>".format(
                    pin=e(i["pin"]),
                    st=e(i["state"]),
                    label=e(i["label"]),
                    hal=e(i["hal_pin"]),
                    pol=e(i["polarity_note"]),
                    col=SEV_COLOR.get(i["state_severity"], MUTED_DIM),
                    state=e(i["state_label"]),
                )
            )
        inputs_body = (
            "".join(rows)
            + '<div class="in-note">{}</div>'.format(e(a["inputs_live_note"]))
            + banner(
                "Girişler salt okunur — makinenin bize söylediği şeydir, ayarlanmaz.",
                "info",
            )
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
        live = (
            '<div class="kv-wrap">{}</div>'.format(kv_rows(m["timing_live_rows"]))
            if m["timing_live_rows"]
            else '<div class="empty">{}</div>'.format(e(m["timing_live_note"]))
        )
        timing = card(
            "Step zamanlaması",
            '<div class="row-label">INI</div>'
            + kv_rows(m["timing_rows"])
            + '<div class="row-label">Canlı (HAL)</div>'
            + live
            + (
                '<div class="in-note">{}</div>'.format(e(m["timing_live_note"]))
                if m["timing_live_rows"]
                else ""
            )
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


def render_observe(o: Dict[str, Any]) -> str:
    """Observer mode's banner (app.py --observe), at the top of every
    screen and kept in view while it scrolls, above the message band in
    the block render_body makes stick (app.css .top-stick); "" in normal
    mode."""
    if not o["active"]:
        return ""
    return '<div class="observe" data-observe="1" role="status">{}</div>'.format(
        banner("{}. {}".format(o["banner"], o["detail"]), "warn")
    )


#: The id of the stop rail's band that app.js shows the moment a rail button
#: is pressed, until the server's answer is on the screen.
RAIL_SENT_ID = "rail-sent"


def render_rail(vm: Dict[str, Any]) -> str:
    """The stop rail: ACİL STOP and DURDUR on every screen
    (viewmodel.stop_rail). render_page puts it outside #app, once: the 1 Hz
    poll replaces #app and never touches the rail, so a press on it cannot
    be lost to a re-render and nothing on a screen can cover or move it.

    data-instant: app.js posts these on pointerdown, not on click, and
    without waiting for anything. type="button": no form submits them. A
    disabled DURDUR (observer mode) says why in text under it; the kiosk
    strips title tooltips (app.js noTooltips)."""
    r = vm["rail"]
    stop_attrs = "" if r["stop_enabled"] else " disabled"
    why = (
        '<div class="rail-why">{}</div>'.format(e(r["stop_disabled_reason"]))
        if r["stop_disabled_reason"]
        else ""
    )
    # One key a line: "Esc = Durdur · Pause = Acil stop" wrapped mid-phrase.
    hint = "".join(
        '<span class="rail-key">{}</span>'.format(e(part)) for part in r["hint"].split(" · ")
    )
    return (
        '<aside id="rail" class="rail" aria-label="Durdurma">'
        '<button type="button" class="rail-estop" data-action="software_estop" '
        'data-instant="1">{estop}</button>'
        '<button type="button" class="rail-stop" data-action="stop" '
        'data-instant="1"{stop_attrs}>{stop}</button>'
        "{why}"
        '<div class="rail-hint">{hint}</div>'
        '<div class="rail-sent" id="{sent_id}" role="status" hidden></div>'
        "</aside>".format(
            estop=e(r["estop_label"]),
            stop=e(r["stop_label"]),
            stop_attrs=stop_attrs,
            why=why,
            hint=hint,
            sent_id=RAIL_SENT_ID,
        )
    )


#: The error box's acknowledge button (app.Session.act "ack_error").
ACK_LABEL = "Tamam"


def render_msgband(vm: Dict[str, Any]) -> str:
    """The message band, at the top of every screen and kept in view while
    it scrolls (app.css .top-stick): the operator message on the left, in
    its severity's colour (grey news, yellow refusal, red fault), and on
    the right the error box while there is one
    (viewmodel.last_error_view): its time to the second, the Turkish text
    large and red, LinuxCNC's own text small under it, and «Tamam», which
    carries the error's id so it clears that error and no later one. It
    replaces the old footer line, small, grey and overwritten by the next
    message.

    The line is left empty while it says exactly what the box says. «Tamam»
    is a plain button inside #app: a press holds the 1 Hz re-render
    (app.js), and in observer mode it stays enabled - the acknowledgement
    sends nothing to LinuxCNC."""
    st = vm["status"]
    err = st.get("last_error")
    text, sev = st["message"], st["message_severity"]
    if err is not None and sev == "error" and text == err["text"]:
        text = ""
    line = '<div class="msg-line" data-severity="{}">{}</div>'.format(e(sev), e(text))
    box = ""
    if err is not None:
        raw = err["raw"]
        raw_line = (
            '<div class="errbox-raw">LinuxCNC: {}</div>'.format(e(raw))
            if raw and err["text"] != "LinuxCNC: " + raw
            else ""
        )
        box = (
            '<div class="errbox" data-error-id="{id}">'
            '<div class="errbox-head"><span class="errbox-label">HATA</span>'
            '<span class="errbox-time">{time}</span></div>'
            '<div class="errbox-text">{text}</div>{raw}{ack}</div>'.format(
                id=e(err["id"]),
                time=e(err["time"]),
                text=e(err["text"]),
                raw=raw_line,
                ack=button(ACK_LABEL, "ack_error", True, kind="errbox-ack", value=str(err["id"])),
            )
        )
    return '<div class="msgband" role="status">{}{}</div>'.format(line, box)


def render_confirm(c: Optional[Dict[str, Any]]) -> str:
    """The confirmation band (viewmodel.confirm_band, plan WP07), under the
    message band on every screen while app.Session has a question standing:
    the question - what will move, 'yol açık mı?' - large, «Evet»
    (data-action="confirm", the question's token as data-value) and
    «Vazgeç» (cancel_confirm), and how long it stands. Plain buttons inside
    #app: a press holds the 1 Hz re-render (app.js), and the token is the
    same in every re-render. «Evet» is off until `ready` (app.CONFIRM_MIN_S
    after the question; the hint says so): the band opens where a double
    tap's second press can land. For a moment after an «Evet» that came for
    a question no longer standing, a warning that nothing was sent. "" for
    neither. Never over the stop rail, which is outside #app."""
    if not c:
        return ""
    if c["refused"]:
        return '<div class="confirm" data-confirm="refused" role="status">{}</div>'.format(
            banner(c["text"], "warn", c["label"]))
    # While «Evet» is off: when it turns on, for app.js's one extra render.
    ready_in = int(c.get("ready_in_ms") or 0)
    return (
        '<div class="confirm" data-confirm="1"{ready_in} role="alertdialog">'
        '<div class="confirm-head"><span class="confirm-label">{label}</span>'
        '<span class="confirm-text">{text}</span></div>'
        '<div class="confirm-row">{yes}{no}<span class="confirm-hint">{hint}</span></div>'
        "</div>".format(
            ready_in=' data-ready-in-ms="{}"'.format(ready_in) if ready_in > 0 else "",
            label=e(c["label"]),
            text=e(c["text"]),
            yes=button(c["yes"], "confirm", c["ready"], c["hint"], kind="confirm-yes",
                       value=c["token"]),
            no=button(c["no"], "cancel_confirm", True, kind="confirm-no"),
            hint=e(c["hint"]),
        )
    )


def render_body(vm: Dict[str, Any]) -> str:
    """Header + nav + active screen. This is what the poll swaps in; the
    stop rail is not part of it (render_rail). Each screen opens with the
    observer banner (observer mode only), the message band and the
    confirmation band (only while a question stands), in one block that
    stays in view while the screen scrolls."""
    return (
        render_header(vm)
        + render_nav(vm)
        + '<main class="main" data-screen="{}"><div class="top-stick">{}{}{}</div>{}</main>'.format(
            e(vm["screen"]), render_observe(vm["observe"]), render_msgband(vm),
            render_confirm(vm.get("confirm")), render_screen(vm),
        )
    )


def _script_json(value: Any) -> str:
    """json.dumps for a value pasted inside <script>...</script>.

    json.dumps leaves "<", ">" and "&" as they are, so a string holding
    "</script>" would end the element early and turn the rest into live
    markup, and "<!--" changes where the parser looks for the end (HTML
    Standard, 4.12.1.3 "Restrictions for contents of script elements"). Each
    is written as its \\u escape instead: the same string to JavaScript, and
    JSON outside a string never contains them. U+2028/U+2029 are line
    terminators that ECMAScript before ES2019 rejects inside a string literal
    (the "JSON superset" change), so they are escaped as well. Other non-ASCII
    text (Turkish) stays as UTF-8, which the page declares (meta charset).

    Defence in depth: the only value inlined today is the screen name, which
    app.py already restricts to VM.SCREENS on every route and on --dump.
    """
    return (
        json.dumps(value, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace("\u2028", "\\u2028")
        .replace("\u2029", "\\u2029")
    )


def render_page(
    vm: Dict[str, Any], css: str, js: str, boot_url: Optional[str] = None
) -> str:
    """The whole page: the stop rail (render_rail), then #app, the part the
    poll replaces. window.__VM__ carries what app.js needs at load: the
    screen, and in kiosk mode (app.py --boot-url) where to go when the UI
    stops answering."""
    client: Dict[str, Any] = {"screen": vm["screen"]}
    if boot_url:
        client["boot_url"] = boot_url
    return (
        "<!doctype html>\n"
        # translate="no": in sim/ the kiosk's Chromium offered to translate
        # the page into English (2026-10-20).
        '<html lang="tr" translate="no"><head><meta charset="utf-8">'
        '<meta name="google" content="notranslate">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>better-cnc · {title}</title>"
        "<style>{css}</style></head>"
        '<body>{rail}<div id="app">{body}</div>'
        "<script>window.__VM__={vmjson};</script>"
        "<script>{js}</script></body></html>".format(
            title=e(vm["profile"]["label"]),
            css=css,
            rail=render_rail(vm),
            body=render_body(vm),
            vmjson=_script_json(client),
            js=js,
        )
    )
