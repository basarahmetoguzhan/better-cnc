# 11 — UI Implementation

Implementation of the Claude Design prototype in `design/`, rebuilt against the
verified hardware. Code lives in [ui/](../ui/), run instructions in
[ui/README.md](../ui/README.md).

**Status: implementation VERIFIED against the repo's own config and docs;
`LinuxCncAdapter` tested against a fake built from the LinuxCNC 2.9 source, and
since 2026-10-19 run against a real LinuxCNC 2.9.9 in the simulator
([sim/](../sim/README.md)), but not yet against the machine.** The server
renders from a mock adapter by default (`--adapter linuxcnc` for the real one),
and says which only on the server's stderr; a `--dump` page carries no such
note.

---

## Summary

| | |
|---|---|
| Stack | Python 3 stdlib only + server-side HTML |
| Tests | 781, all passing, no expected failures, `make test` (732 UI + 49 in `scripts/test_grbl2ini.py`; 4 keymap tests run only where libxkbcommon is, e.g. `sim/sim.sh test`); also on arm64 + Python 3.13 in the simulator (`sim/sim.sh test`) |
| Coverage | **99.6 %** (`make coverage`); `adapter.py` 99.0 % |
| Screens | 6, as the prototype: Kontrol / Program / Probing / Takımlar / Alarmlar / Makine |
| Profiles | `test-rig` (verified) and `marble` (TODO placeholders) |
| Prototype facts corrected | 24, table below |
| Machine connection | none by default — the server runs `MockAdapter`; `--adapter linuxcnc` runs `LinuxCncAdapter`, which is tested against `ui/tests/fake_linuxcnc.py`, has run against LinuxCNC 2.9.9 in `sim/`, and has never run on a machine |

---

## Stack choice, and why

**Python 3, standard library only, rendering HTML on the server.**

Three constraints decided this, in order of force:

**1. The `linuxcnc` module is CPython-only and ships with LinuxCNC.** It is not
on PyPI. `linuxcnc.stat` / `.command` / `.error_channel` must be imported in the
same interpreter that talks to the machine, so the data layer is Python whether
we like it or not. Putting the UI in the same language removes a process boundary
and an IPC format from the eventual real deployment.

**2. The target machine has no third-party Python packages.** It is a LinuxCNC
2.9.10 live USB (`docs/DURUM.md:21`) with 10 GB of persistence. `pytest`,
`coverage`, `flask`, `jinja2`, `PyQt5` — none are installed, and the local dev
Python is 3.9.6. Requiring `pip install` puts a network round-trip between a
fault and a diagnosis on a machine that may be in a workshop with no internet.
So: zero dependencies, and a stdlib coverage tool
([ui/tools/covreport.py](../ui/tools/covreport.py)) rather than `coverage`.

**3. The rules worth protecting are testable only if rendering is pure.**
This is the decisive one. The brief's central requirement is *"a capability
absent from a profile must render disabled, never hidden"*. With a JavaScript SPA
that assertion needs a DOM harness — a headless browser, or jsdom, i.e. exactly
the dependencies constraint 2 forbids. Rendering server-side makes it a string
assertion on the output of a pure function:

```python
markup = render_body(build(profile, config, state, screen="makine"))
assert 'data-capability="spindle"' in markup      # rendered
assert 'data-enabled="0"' in tail_of_that_block   # disabled
```

That is why `render.py` returns strings and `app.js` is about 100 lines that only
forward clicks and swap `innerHTML`. The view logic stayed in Python because
that is where it can be tested.

**What was rejected.** PyQt/QtVCP — LinuxCNC's own GUI toolkit, and the
conventional choice — is hard to assert on headlessly and is not installed here.
A React/Vue SPA needs a build step and a DOM test harness. A terminal UI cannot
show the DRO and jog pad the prototype designed.

**The trade-off being accepted:** a 1 Hz poll re-renders the whole screen
server-side, which is heavier than patching the DOM and means the UI is not
suitable for anything needing sub-100 ms feedback. For a status display with a
jog pad on a 1 ms servo loop that is fine; the FPGA does the real-time work. If
this ever needs live DRO at 30 fps, the DRO alone can move to a small JS
component reading `/api/state`.

---

## The capability model

The rule from [MERMER-GECIS.md:43-45](MERMER-GECIS.md): a module the current
machine lacks is **not hidden, it is shown disabled with the reason on screen**,
so that moving to the marble machine surfaces what is missing instead of
requiring someone to remember it.

Implementing that as a convention would decay. It is structural instead:

```
capabilities.py   CAPABILITY_CATALOGUE — every capability the UI can render
profiles.py       each profile declares a CapabilityState for every one
viewmodel.py      capability_panels() iterates the CATALOGUE, not the profile
profiles.py       validate_profile() raises if a profile omits one
```

**Rendering iterates the catalogue, so omission cannot hide anything.** A profile
that forgets `spindle` fails `validate_profile` at session build:

```
ValueError: profile 'test-rig' does not declare: spindle -
an undeclared capability would be silently hidden
```

### Three presence states, not two

| State | Means | Renders as |
|---|---|---|
| `PRESENT` | machine has it | operable |
| `ABSENT` | we looked, it is not there | disabled, grey, `yok` |
| `TODO` | **not surveyed yet** | disabled, amber, `BELİRSİZ` |

`TODO` exists because the marble machine's inventory
([01-machine-survey.md](01-machine-survey.md)) is still blank. Rendering "no
spindle" for a machine that certainly has one, merely unsurveyed, would be as
wrong as rendering a fabricated number. All eleven marble capabilities are
currently `TODO`.

Every non-present capability carries two mandatory UI-visible strings — the
constructors refuse to build without them:

```python
absent(reason="Bu makinede iğ mili yok — mil elle çalıştırılıyor…",
       requirement="VDF bağlantısı + num_pwmgens=1 (IO 16) + CW çıkışı + izole 24V",
       source="docs/MERMER-GECIS.md:34")
```

`requirement` is `MERMER-GECIS.md` §2's "açmak için gereken" column, in the UI
where it will actually be read.

### The eleven capabilities

`spindle` · `spindle_analog_speed` · `coolant_pump` · `flow_sensor` ·
`flow_interlock` · `tool_probe` · `tool_length_sensor` · `estop_input` ·
`door_switch` · `fourth_axis` · `soft_limits`

All eleven are ABSENT on the test rig today and TODO on marble — so on both
profiles every capability renders disabled, which is exactly the state the tests
assert capability by capability.

Ten of the test rig's eleven are fixed facts in `profiles.test_rig_profile`.
`soft_limits` is not: it is derived from the INI (`profiles._soft_limits_from_ini`),
through the same loader the screens use. While the travel limits carry their
provisional markers it is ABSENT, and its reason names the unmeasured axes
(`Strok ÖLÇÜLMEDİ (X, Y, Z)…`). Once the markers are removed it turns PRESENT
with no code change (see D below).

---

## The data boundary to LinuxCNC

```
                    ┌─────────────────────────────┐
   viewmodel.py ◄───┤  MachineState  (plain data) │
   render.py        └─────────────────────────────┘
                                  ▲
                    ┌─────────────┴───────────────┐
                    │      MachineAdapter (ABC)   │
                    └──────┬───────────────┬──────┘
                           │               │
                   MockAdapter        LinuxCncAdapter
                (server default)   (tested against a fake,
                                    never run on a machine)
                           │               │
                           └──── mdi.py ───┘
                         one MDI gate, shared
```

Nothing above `adapter.py` imports `linuxcnc` or `hal`, so the entire view layer
is testable off-machine. `mdi.py` holds the MDI gate both adapters use; it is
pure G-code word parsing with no LinuxCNC dependency (see E below).

### The mapping is a pure function

The riskiest code is translating LinuxCNC's status structure, so it is separated
out as `map_linuxcnc_state(stat, hal_values, profile, …) -> MachineState` and
tested against a hand-built `SimpleNamespace` — no machine, no `linuxcnc`
module. 24 tests cover it (`TestLinuxCncMapping` in
`test_mdi_tools_adapter.py`), including partially-populated `stat` objects, the
ESTOP_RESET state, a paused program and the startup estop latch.

Field names verified against the Python bindings
(`reference/linuxcnc/src/emc/usr_intf/axis/extensions/emcmodule.cc`):

| Field | Source | Used for |
|---|---|---|
| `stat.task_state` | emcmodule.cc:329 | software estop: anything but `STATE_ON` (4) is disabled, ESTOP_RESET included |
| `stat.estop` | emcmodule.cc:418 | **not read** — see A below |
| `stat.joint[n]["homed"]` | emcmodule.cc:599 | homing gate |
| `stat.joint[n]["homing"]` | emcmodule.cc:598 | in-progress phase |
| `stat.actual_position` | emcmodule.cc:738 | machine DRO |
| `stat.g5x_offset` | emcmodule.cc:753 | work DRO |
| `stat.g5x_index` | emcmodule.cc:754 | WCS name: 1–6 → G54–G59, 7–9 → G59.1–G59.3 (interp_convert.cc:1762-1789); anything else (0 before the first poll) → G54 |
| `stat.feedrate` | emcmodule.cc:372 | feed override %, rounded |
| `stat.file` / `.paused` / `.interp_state` | emcmodule.cc:342, :371, :337 | program state |

`LinuxCncAdapter` also reads `stat.task_mode` (:328), `.motion_mode` (:363),
`.kinematics_type` (:381), `.joints` (:360), `.homed` (:747) and `.inpos`
(:366). `fake_linuxcnc.STAT_ATTRIBUTES` is the checked list: a test ties both
the adapter module's docstring and the fake to the binding.

One known flaw: `map_linuxcnc_state` numbers joints by their position in the
joint list, not by their joint number (see Known gaps).

HAL pins, verified against the driver source:

| Pin | Source | Type |
|---|---|---|
| `hm2_7i92.0.packet-error-total` | hm2_eth.c:1558-1561 | u32 |
| `hm2_7i92.0.watchdog.has_bit` | watchdog.c:140-144 | bit |
| `hm2_7i92.0.io_error` | hostmot2.c:1446 (a parameter, not a pin; `hal.get_value` reads parameters too, halmodule.cc:1017-1034) | bit |

### Reading the HAL pins needs a HAL component

The pins are read with `hal.get_value()`, but that call only works in a process
that has created a HAL component: until then it raises `Cannot call before
creating component` (halmodule.cc:1010-1014). So `LinuxCncAdapter` opens an
empty component, `bettercnc-ui-<pid>`, with no pins
(`LinuxCncAdapter._open_hal_component`). It never joins a net, so the UI is
still a passive observer of the HAL graph. If the component cannot be created,
a WARN alarm says so and every pin reads as unreadable.

**Side effect, accepted:** the component makes the UI process a HAL user
component. When LinuxCNC shuts down, its script runs `halcmd unload all`
(scripts/linuxcnc.in:707), which sends SIGTERM to every user component's pid
(halcmd_commands.cc:1260-1290). Nothing in the UI handles SIGTERM, so the whole
UI process — web server included — exits with LinuxCNC. Restart it after
LinuxCNC.

An unreadable pin is `None`, never 0. The link counts as connected only if at
least one pin could be read; with no `hal` module nothing is connected (see C).

### How `LinuxCncAdapter` drives LinuxCNC

Sequencing follows axis.py, the reference GUI
(`src/emc/usr_intf/axis/scripts/axis.py`), with two deliberate differences:
every wait is bounded and checked, and homing is not waited for at all.

| Action | Sequence | axis.py |
|---|---|---|
| task mode | `mode(m)`, bounded wait, poll, check the mode | ensure_mode, :986-992 |
| jog mode | joint jog until homed, teleop after | get_jog_mode, :3225-3242 |
| teleop switch | `teleop_enable(v)`, bounded wait, check `motion_mode` | set_motion_teleop, :273-279 |
| home | MANUAL, teleop off, `home(-1)`, no wait | :2693-2702, :1954-1957 |
| program load | MANUAL (ON not required), `task_plan_synch`, wait, `program_open`, wait | open_file_guts, :1177, :1206-1208 |
| stop | `abort()`, wait | task_stop, :2411-2417 |
| estop | own command channel, `state(ESTOP)`, nothing else | — |

**Bounded waits.** Every `wait_complete()` is bounded by `adapter.WAIT_TIMEOUT_S`
= 0.25 s (25 cycles of the rig's `[TASK]CYCLE_TIME` = 0.010), not the binding's
5 s default (EMC_COMMAND_TIMEOUT, emcmodule.cc:209). Two reasons. The binding
keeps the GIL for the whole wait (emcmodule.cc:212-231; the module's only
`Py_BEGIN_ALLOW_THREADS` is in `logger.start`, :2125), so a long wait would hold
up every other thread, the software estop's included. And task reports DONE only
once motion is idle and the interpreter idle (emctaskmain.cc:3546-3561), so
while a jog or a homing move runs, no wait short of the move itself returns
DONE. DONE is not proof either: NML serial numbers are global across channels,
so a software estop sent on the other channel can end a wait early
(emcmodule.cc:218-220). So every wait is followed by reading back the state it
was meant to produce, and if that state was not reached the action is refused
with a Turkish message — never continued in the wrong mode. 0.25 s is chosen,
not measured: if it proves too short on the machine, the symptom is a refusal
that goes away on retry.

**Refusals first.** The adapter refuses up front, so the operator gets an
immediate Turkish message through `MachineState.message`, the same channel the
mock uses. LinuxCNC's own refusal still arrives later through the error channel.
The error channel is drained into the alarm log newest first: error kinds also
become the operator message; the text and display kinds are INFO and leave it
alone; a kind `Error_poll` does not know counts as an error and ends the drain;
and at most 64 messages are read per drain (`adapter._MAX_ERRORS_PER_DRAIN`), one
drain per snapshot plus one after a refused mode switch or program open.

**Status lost.** When `stat.poll()` raises `linuxcnc.error` (the status buffer
is invalid, emcmodule.cc:278-284), `snapshot()` returns a state marked unknown
(see I below), and every action that polls refuses through
`adapter._refused_without_status` instead of raising out of `Session.act`:
`LinuxCNC'ye bağlantı yok — komut gönderilmedi.` when nothing was sent, or
`LinuxCNC'ye bağlantı koptu — gönderilen komutun sonucu bilinmiyor.` once
something had been (`program_stop` always sends `abort()` first). The outage
and the recovery are each logged once. `software_estop`, the feed override and
the jog step never poll, so they are not refused: the jog step only sets the UI's
value, and the feed override and the estop are sent anyway. A send to a task that
no longer answers can spin in emcSendCommand for up to 5 s holding the GIL
(emcmodule.cc:209, :233-250); for the feed override that is under the session
lock. Not measured.

### What is mocked and what is real

| Real (traced to a repo file) | Mocked (`MockAdapter`, the server default) |
|---|---|
| Board identity, IO count, clock — from `board-dumps/readhmid-*` | Axis positions (start at 0, a jog moves them at once) |
| SCALE, velocity, accel, step timing, HOME_SEQUENCE, HOME_IGNORE_LIMITS, SERVO_PERIOD — read from `test-rig.ini` at runtime | Link health counters (0 / `sessiz` / `yok`), shown like readings |
| Input map, polarity, Z asymmetry — `docs/DURUM.md:69-80` | Alarm log seed entries (`hm2_eth: 7i92 bulundu…`, `Üç stepgen aktif…`), stamped with the current time |
| Kernel, NIC, host — `docs/DURUM.md:20-27` | Homing (completes instantly) |
| Capability reasons and requirements — `docs/MERMER-GECIS.md` §2 | Program progress |
| The estop, homing and MDI **rules** (enforced in `MockAdapter`, not faked; the MDI gate is the real adapter's `mdi.mdi_refusal`) | |
| The soft-limit alarm and NO_FORCE_HOMING — follow the INI | |

Two of the mock's rules are code the real adapter runs too: the program-start
gate (`MachineState.start_blocks`) and the MDI gate (`mdi.mdi_refusal`), so tests
of those exercise logic that will still be there when the adapter is swapped. The
third, that software cannot clear a hardware estop, is the mock's own model
(`EstopState.software_reset`); `LinuxCncAdapter` relies on LinuxCNC's latch for
it, which the fake models and `test_no_adapter_call_can_restore_hardware_drive_power`
tests. Where the mock is looser than the real adapter is listed under Known gaps. Its link servo period comes from the INI's
`[EMCMOT]SERVO_PERIOD`, and its `Yumuşak limitler tanımsız` alarm appears only
while the INI's travel markers remain.

What the mock does not do: say on screen that it is a simulation (only the
server's stderr banner does), or refuse every input the real adapter refuses.
Both are open (Known gaps).

---

## Reading the INI

`iniload.py` follows LinuxCNC's own reader, IniFile::Find
(`reference/linuxcnc/src/libnml/inifile/inifile.cc`), rather than configparser,
because the comments carry the provisional markers:

- **The first occurrence wins.** The section search stops at the first
  `[SECTION]` line (inifile.cc:261-267) and the key search ends at the next `[`
  (:337-340), so the keys of a repeated section are never read, even ones the
  first occurrence lacks. Inside a section the first line for a key decides
  (:354).
- **Continuation lines.** A trailing backslash joins the next line on
  (:295-327). Only `\n` ends a line, so a DOS `\r\n` line is not continued, as
  in LinuxCNC. A continuation still open at end of file drops the key.
- **No value, no key.** `KEY =`, or a key line with no `=`, makes the key not
  found (:359-363), and a later line for the same key does not help.
- **Any `[` line starts a section**, whatever its name holds (`[SPINDLE-0]`,
  `[JOINT 1]`); without a `]` its keys are unreachable.
- **Booleans as LinuxCNC reads them.** HOME_IGNORE_LIMITS goes through the same
  rule as EmcIniFile::Find(bool) (emcIniFile.cc:50-71): an integer prefix first,
  any non-zero value meaning true, then TRUE/YES/1/FALSE/NO/0 case-insensitively.
  Anything else is `None` — unknown, never a silent False.
- **A missing `[JOINT_n]` is skipped, not renumbered**
  (`configmodel.load_config_from_ini`): with trivkins joint n is the nth axis
  letter, so a missing `[JOINT_1]` leaves Z as joint 2.
- **Provisional markers.** A comment block containing `ASSUMPTION:`,
  `MUST BE MEASURED` or `PROVISIONAL` (any case) marks every key it governs as
  provisional, until another comment block takes over, i.e. one followed directly
  by a key with a value or by a section header (a block cut off by a blank line
  does not). Only a blank line, a section header
  or a key with a value ends a pending block; a line the loader cannot parse
  does not, so a stray line between an ASSUMPTION block and a key still taints
  the key. A block directly above a header governs that section's first keys.
  Either limit of an axis being provisional makes its whole travel unmeasured.

In `configs/test-rig/test-rig.ini` every MIN_LIMIT/MAX_LIMIT is marked: the
three `[JOINT_n]` blocks, and all three `[AXIS_*]` sections, `[AXIS_Z]` included
(`Mirrors [JOINT_2]. Provisional; …`). The UI reads only the `[JOINT_n]` limits
today; the `[AXIS_*]` markers make sure a later reader cannot show Z's
placeholder travel as measured either.

Two known divergences, documented in the iniload module docstring, neither of
which the repo's INIs trigger: a backslash line directly above a section header, and
more than 20 continuation lines (LinuxCNC refuses the key; iniload joins them).

---

## The server and the CLI

```
GET  /              full page
GET  /api/render    re-render one screen (the 1 Hz poll)
POST /api/action    an intent, then re-render
GET  /api/state     the raw view model as JSON, for debugging
GET  /healthz       {"ok": true}
```

- **One lock, one exception.** `Session` runs every adapter call under one lock,
  one at a time — except `software_estop`, which never queues (see A).
  `Session.view` takes the lock too, so the page that shows an estop waits until
  the action in progress returns.
- **Screen names are validated on every route** (`app._valid_screen`): an unknown
  one becomes `kontrol`, on `POST /api/action` as on GET, so no route echoes a
  screen that is not in `VM.SCREENS`. The one value inlined into the page's
  `<script>` is escaped so it cannot close the element (`render._script_json`).
- **Bodies.** A bad or negative `Content-Length` is a 400 and a body over 64 KiB
  (`app.MAX_ACTION_BODY`) a 413, both refused before anything is read. Malformed
  or non-object JSON is no action; an unknown action is ignored. The jog value's
  wire format is `<axis>:<1|-1>`; any other direction is dropped.
- **Request timeout.** Every request socket gets `app.REQUEST_TIMEOUT_S` = 10 s
  (`Handler.timeout`). A client that stalls mid-request is disconnected and its
  action is not applied. The timeout is per socket operation, so a client that
  trickles bytes slowly is still not bounded. Chosen, not measured.
- **`--dump SCREEN`** renders one screen to stdout through the same
  `render_page` the server sends. An unknown or empty screen name is a usage
  error (exit 2) that lists the valid screens, rather than a page labelled with
  a screen that does not exist. `make dump SCREEN=makine`.
- The startup banner on stderr says `(mock adapter — makineye bağlı DEĞİL)`.
  Nothing on screen says it (Known gaps).

---

## Every prototype fact corrected

The repo wins wherever the prototype and the repo disagreed. All 24 corrections
below are guarded by
[test_prototype_corrections.py](../ui/tests/test_prototype_corrections.py), so
reintroducing one fails the suite by name.

### Fabricated input map

| Prototype | Reality | Source |
|---|---|---|
| `gpio.020` = X ref/limit | `gpio.020` = **X sağ (right)** | DURUM.md:75 |
| `gpio.021` = Y ref/limit | `gpio.021` = **X sol (left)** | DURUM.md:76 |
| `gpio.022` = Z ref/limit | `gpio.022` = **Y arka (rear)** | DURUM.md:77 |
| `gpio.023` = estop chain | `gpio.023` = **Y ön (front)** | DURUM.md:78 |
| `gpio.024` = tool length sensor | `gpio.024` = **Z üst (top)** | DURUM.md:79 |
| `gpio.025` = flow sensor | **no flow sensor on the test rig** — only `gpio.020`–`024` are wired (the 5 isolated inputs, all switches). `gpio.025` itself exists with `num_encoders=0`, as an unused encoder-terminal GPIO whose front-end circuit is unverified | DURUM.md:53, :69-80, :167; MERMER-GECIS.md:112-114 |
| — (absent) | **Z has NO bottom switch** — rendered as an explicit asymmetry | DURUM.md:80 |
| `.in` implied | inputs idle HIGH → HAL uses **`.in_not`** | DURUM.md:71 |
| inputs were clickable toggles | inputs are **read-only** | — |

The three fabricated inputs (estop, tool sensor, flow) correspond to capabilities
the machine lacks, and now render as disabled capabilities instead of fake inputs.

### Axis numbers ~50× wrong

| Prototype | Reality | Source |
|---|---|---|
| X/Y 80.000 steps/mm | **833.300 / 823.500** | test-rig.ini `[JOINT_0/1]SCALE` |
| Z 400.000 steps/mm | **1250.000** | test-rig.ini `[JOINT_2]SCALE` |
| 40 / 40 / 8 mm/s | **3 mm/s** all axes (deliberate first-run value) | test-rig.ini `MAX_VELOCITY` |
| 500 / 500 / 300 mm/s² | **10 mm/s²** all axes | test-rig.ini `MAX_ACCELERATION` |

Read from the INI at runtime, not copied. The tests parse the INI with their own
parser and compare — so config and UI cannot drift apart silently.

### Step timing would drop pulses

| Prototype | Reality | Source |
|---|---|---|
| steplen 2 000 ns | **10 000 ns** | docs/09, test-rig.ini |
| stepspace 2 000 ns | **10 000 ns** | docs/09, test-rig.ini |
| dirsetup 20 000 ns | **50 000 ns** | docs/09, test-rig.ini |
| dirhold 20 000 ns | **50 000 ns** | docs/09, test-rig.ini |

### Platform

| Prototype | Reality | Source |
|---|---|---|
| PREEMPT_RT **arm64** | **`6.12.95+deb13-rt-amd64`** — a laptop on a live USB, not a Pi | DURUM.md:22 |
| max jitter **38 µs** | **never measured** → renders `—` | DURUM.md |
| latency 38 µs in the header | removed; header shows link health instead | — |

A test greps every screen for `\d+\s*µs` and fails on any match, so a fabricated
latency figure cannot creep back in.

### Travel limits

| Prototype | Reality |
|---|---|
| X 0–300 mm, Y 0–180 mm, Z −45–0 mm | **not measured** → renders `ölçülmedi` |

The INI *does* contain 0–150 / −50–0, because LinuxCNC needs values, but they sit
under `# ASSUMPTION:` comments. `iniload.py` reads that marker and marks the
values provisional; the UI then refuses to display them. A test asserts the
provisional numbers appear on **no** screen.

When travel is finally measured and the marker words (`ASSUMPTION:`,
`PROVISIONAL`, `MUST BE MEASURED`) are deleted from the comments above the
limits, the UI starts showing numbers with no code change. That now holds for
everything that depends on travel: the DRO and the Makine axis rows, the
`KORUMA YOK` banner and the gate warning, the `soft_limits` capability, the
profile's travel note, the Z-bottom consequence text and the mock's soft-limit
alarm. It is tested both ways, end to end on a copy of the real `test-rig.ini`
with only its marker words deleted (`helpers.strip_markers`, and its twin in
`test_edge_cases.py`).

### Capabilities that do not exist

| Prototype | Reality |
|---|---|
| spindle toggle, 12 000 rpm | no spindle → capability disabled with reason |
| coolant toggle, defaulting **ON** | no pump → disabled |
| flow 2.4 L/dk, "kilit açık" | no flow sensor and no interlock → disabled |
| probe modes operable | no probe → flow preserved but disabled |
| tool T6 = "Prob (tool sensörü)" | **a probe is a HAL input, not a tool** — removed from the table |
| 6 tools with length offsets (42.180 mm …) | `tool.tbl` **does not exist** → table empty with the reason |
| 5 program files, 4 812 lines, 18 dk | no `.ngc` files in the repo → list empty with the reason |
| probe speeds 300 / 25 mm/dk | no probe → parameters render `—` |

The empty tool table is a correction beyond the brief's list: `[EMCIO]TOOL_TABLE`
names `tool.tbl` but `configs/test-rig/tool.tbl` does not exist. Tool length
offsets move a machine, so they are not invented.

### What the prototype got right and is preserved

- Six-screen structure and navigation, with the alarm badge
- Alarm log **with source attribution** (`hm2_eth`, `homing`, `gpio/AXIS 1`, `config`) — genuinely good practice, kept
- Step-by-step probing flow — kept in full, gated behind the missing probe
- Dark theme, palette taken verbatim from `prototype-source.jsx:78-80`
- Turkish throughout
- **The board-info panel, which was factually correct**: Zhulong V2.0 (Mesa 7i92) / `hm2_7i92.0` / Spartan-6 XC6SLX9 / 34 IO (2 × 17) / 100 MHz / `hm2_eth` 10.10.10.10

---

## Missing concepts, implemented

### A. Estop: hardware vs software

Modelled as two independent fields, never one boolean:

```python
class DrivePower(Enum):  CUT | LIVE | UNKNOWN     # hardware
software_disabled: bool                          # LinuxCNC
```

`EstopState.software_reset()` **cannot** change `drive_power`. With the chain
open it returns the state unchanged and the UI says the reset was refused — a
software "ready" on a machine that cannot move would be a lie.

**A third state the brief did not ask for: `UNKNOWN`.** The test rig has no
hardware estop input, so LinuxCNC has nothing to read. Reporting `LIVE` would be
inventing a measurement, and a green "powered" light on an unsensed safety chain
is worse than an honest question mark. So the test rig renders:

```
Sürücü gücü (donanım)     BİLİNMİYOR
  Bu makinede donanımsal acil stop girişi yok — LinuxCNC sürücü gücünün
  durumunu göremiyor. Yazılım 'hazır' derken sürücüler gerçekten enerjili
  mi bilinmiyor.
Yazılım durumu (LinuxCNC) devre dışı
  Yazılım sıfırlaması yalnızca bu satırı etkiler.
```

One test enumerates every action the HTTP surface exposes and asserts none of
them clears a hardware cut; another drives the `LinuxCncAdapter` actions
against a latched fault on the fake and checks that LinuxCNC stays in estop and
ON is never sent.

**Drive power comes only from a named physical input pin.** `map_drive_power`
takes `hal_values["estop_input"]`, the chain input itself. Without the
`estop_input` capability the answer is UNKNOWN, and with it but no reading,
UNKNOWN too. `stat.estop` is deliberately not used: it is iocontrol's software
latch, `!emc-enable-in` (ioControl.cc:523-526), set to 1 at every startup
(ioControl.cc:791), so reading it as "chain open" would show a hardware cut at
every startup and disable the very reset that clears the latch. No profile wires
an estop input yet, and no profile field names one, so
`LinuxCncAdapter._read_hal` reads none: drive power is UNKNOWN on both adapters.

**The software estop: its own channel, outside the lock.** On the test rig the
GUI button is the only estop (`configs/test-rig/test-rig.hal`, section "ESTOP
CHAIN - GUI ONLY"), so it must never queue behind another action. `Session.act`
calls `software_estop` without taking the session lock.
`LinuxCncAdapter.software_estop` sends `state(STATE_ESTOP)` on a dedicated
`linuxcnc.command()` channel, with no wait and no poll — so it still goes out
when LinuxCNC's status cannot be read — and touches nothing but that channel,
the message line and the alarm log. `MockAdapter` runs every method under its
own small lock, so a mock action that checked "not in estop" cannot write
"running" or "homed" after the estop landed.

**The GIL caveat.** Skipping the lock removes the queue; it cannot preempt a
call that holds the GIL. The binding's `wait_complete` keeps the GIL for its
whole wait (emcmodule.cc:212-231), and so does every command send, which spins
until task echoes the command (emcmodule.cc:233-250) — for `state()` about one
`[EMCIO]CYCLE_TIME` (0.100 s on the rig), because task waits for iocontrol. So
in normal operation an estop that arrives mid-action is delayed by at most one
bounded wait plus one command echo; with task or iocontrol stalled, by up to
5 s per send. Computed from the source, not measured. The estop's HTTP response
waits longer than the estop itself: it re-renders through `Session.view`, which
takes the lock.

**What the rig shows after a software estop: ESTOP_RESET, not ESTOP.** ESTOP
clears iocontrol's `user-enable-out` (ioControl.cc:1086), but the rig's chain
does not run through it: `user-request-enable → estop-latch.0.reset`,
`estop-latch.0.ok-out → emc-enable-in`, the hostmot2 watchdog → `fault-in`
(test-rig.hal, ESTOP section). `emc-enable-in` stays true, so LinuxCNC only
disables motion (determineState, emctask.cc:419-430). Both states refuse motion
and both render as disabled. The fake models the latch (estop_latch.comp) with
the rig's wiring as its default.

**The reset** (`LinuxCncAdapter.software_estop_reset`):

- already ON: nothing is sent (`Makine zaten açık.`);
- a program or MDI command running: refused, because ESTOP_RESET aborts it
  (emctask.cc:340-349) — defence in depth, since a program runs only in ON;
- from ESTOP: ESTOP_RESET is sent, then the status is polled until LinuxCNC
  leaves ESTOP or 3 × `[EMCIO]CYCLE_TIME` passes (0.3 s on the rig; a missing or
  non-positive value falls back to LinuxCNC's default, 0.100 s). Only then is
  the reset refused, with a WARN alarm and a message that names no cause — an
  open chain and a latch slower than the deadline look the same from here
  (`… Acil stop zinciri kapalıysa tekrar deneyin.`). Once LinuxCNC has left
  ESTOP, `state(ON)` follows, as from ESTOP_RESET;
- from ESTOP_RESET — the rig after a software estop — only `state(ON)` is sent;
- ON not reached: `Makine açılamadı — LinuxCNC açık duruma geçmedi.`, with a
  WARN alarm.

Homing survives a software estop unless a joint has VOLATILE_HOME
(emctask.cc:364, homing.c:316-323), so the success message adds `Referans
gerekli.` only when an axis is unhomed. The deadline is chosen, not measured.

### B. Homing as a gate

Three phases per joint (`not_homed` / `homing` / `homed`), and
`MachineState.start_blocks` returns an ordered list of reasons the program cannot
start. The button is genuinely `disabled` with the reason rendered, not just a
tooltip. Jogging stays allowed while unhomed — that is how you find the switches.

The required order comes from `[JOINT_n]HOME_SEQUENCE` in the INI, not from a
constant (except on a profile with no INI, where the adapters invent X → Y → Z;
see Known gaps), and the shared home/limit warning is rendered prominently:

> Home ve limit aynı switch'te → HOME_IGNORE_LIMITS = YES. Referans alma
> sırasında limitler yok sayılır: switch'in yanlış tarafından başlanırsa ekseni
> durduracak hiçbir şey yoktur (ini-homing.adoc:46).

HOME_IGNORE_LIMITS is read as LinuxCNC reads a boolean (see Reading the INI). An
unreadable value is shown as unknown (`HOME_IGNORE_LIMITS okunamadı: …`), never
as "limits active".

**On the real machine** (`LinuxCncAdapter.home_all`): refused unless ON, while a
program runs, and while any joint is still homing (`Referans alma sürüyor.`) —
a home-all during homing clears joint 0's homed flag and does not restart the
sequence (homing.c:553-560), so the machine would end with joint 0 unhomed.
Otherwise the adapter switches to MANUAL, turns teleop off — motion homes only
in free mode, "must be in joint mode to home" (command.c:1374-1378), and after a
first homing MANUAL means teleop, so leaving teleop first is what makes
re-homing work — and sends `home(-1)`. Homing is not waited for: task reports
DONE only when the whole homing move is over, and a wait would hold the GIL for
its full timeout. Progress shows in `stat.joint[n]["homing"]` / `["homed"]` on
the next snapshots. `MockAdapter` homes instantly.

### ⚠️ A discrepancy this surfaced — now RESOLVED

Reading the order from the config rather than hardcoding it exposed a real
disagreement in the repo:

| Source | Said | Now says |
|---|---|---|
| `configs/test-rig/test-rig.ini` — `HOME_SEQUENCE` Z=0, Y=1, X=2 | **Z → Y → X** | unchanged — it was right |
| `docs/DURUM.md` and `docs/MERMER-GECIS.md` | Z → X → Y | **Z → Y → X**, citing the INI as authority |

**Resolved 2026-08-23:** the INI was correct and the two prose docs had drifted.
Both were corrected to match and now name `test-rig.ini` as the authority. The
`TUTARSIZLIK` banner is consequently clear, and a test asserts it is clear.

**One honest correction to how this was built.** The first implementation
compared the INI against a *hardcoded string literal* buried in the function
body (`doc_order = "Z → X → Y"`). It detected the drift, but it was not really
"reading both sources" — it was reading one source and a copy of the other, and
that copy could itself go stale without anyone noticing.

That is now fixed the same way every other duplicated value in this project is:

- The claimed order is a **declared module constant** with its citation,
  `DOCUMENTED_HOME_ORDER` / `DOCUMENTED_HOME_ORDER_SOURCES`, not a literal hidden
  in a function.
- The comparison target is **injectable**, so the detector can be tested against
  a disagreeing value. Without that, "never fires" and "cannot fire" look
  identical from the outside — and a safety-adjacent detector that has silently
  stopped working is worse than no detector.
- **Tests read the actual doc files** and assert the constant still appears in
  both, plus that neither still contains the old wrong order. If the docs drift
  again, the suite fails and names the file.

Four tests cover this: banner clear when they agree, detector still fires when
they do not, the constant matches both docs on disk, and the constant matches the
INI. The drift can now be caught from either end.

### C. Link health

Added to the header (always visible, on every screen) and given a full card on
the Makine screen with the HAL pin names shown:

| Indicator | HAL pin |
|---|---|
| Paket kaybı | `hm2_7i92.0.packet-error-total` |
| Watchdog | `hm2_7i92.0.watchdog.has_bit` |
| io_error | `hm2_7i92.0.io_error` |
| Servo döngü | — (`[EMCMOT]SERVO_PERIOD` from the INI) |

Unreadable pins render `—` in the warning colour, not `0` — "we could not read
it" is not the same as "it is zero". **Any unreadable counter makes the link not
healthy** (`LinkHealth.healthy`): the header's link readout shows `—` in WARN and its state badge is at least WARN, and `Okunamayan gösterge:
… — bağlantı sağlığı bilinmiyor.` appears on the Makine card and in the gate
warnings. Counted packet loss alone also makes it unhealthy. A link with no
readable pin at all, or no `hal` module, is not connected (`Karta bağlantı
yok.`): that blocks program start in both adapters (`MachineState.start_blocks`)
and disables the jog and MDI controls (`MachineState.can_jog`). The jog/MDI block
is in the view only: neither adapter's `jog` or `send_mdi` checks the link. An unhealthy but connected link warns
and does not block start by itself.

`Servo döngü` comes only from `[EMCMOT]SERVO_PERIOD`, in both adapters and in the
Makine config card, so the two cards cannot disagree. Without it the row shows
`—` in WARN; there is no platform default (marble, which has no INI, shows `—`).

### D. Soft limits not configured

`MachineConfig.soft_limits_configured` is False unless **every** axis has both
limits and neither is provisional. Everything that depends on it follows the INI:

- a `KORUMA YOK` banner on the Makine screen;
- an entry in the Kontrol screen's warning list;
- the `soft_limits` capability, rendered disabled with a reason that names the
  unmeasured axes (`profiles._soft_limits_from_ini`);
- the mock's `Yumuşak limitler tanımsız` alarm-log entry;
- the Z-bottom-switch absence, rendered alongside: while Z's travel is
  unmeasured it says the only protection, the soft limit, is undefined.

With the markers removed, the banner and warning go, the capability turns
PRESENT, and the Z-bottom text switches to saying the soft limit is the only
protection and applies as an absolute limit only after homing — unhomed, the
jog limit is the current position plus or minus the full travel
(command.c:154-175).

### E. MDI text entry

A real `<input>` with Enter-to-submit, plus server-side history showing accepted
and rejected commands with the reason. Presets kept. History is HTML-escaped —
there is a test for `<script>`.

**One gate for both adapters.** `mdi.mdi_refusal` (`ui/bettercnc/mdi.py`)
decides whether a line may be sent at all, in this order: empty; machine not ON;
a program running; unhomed. Both adapters call it, so the mock cannot drift from
the real adapter. Before homing LinuxCNC refuses **every** MDI line unless
`[TRAJ]NO_FORCE_HOMING` is set (emctaskmain.cc:2208-2212), so both adapters
refuse every MDI line before homing, `M5` included:

```
Referans alınmadı — LinuxCNC referanssız MDI kabul etmiyor ([TRAJ]NO_FORCE_HOMING yok).
```

NO_FORCE_HOMING is read as task reads it, with sscanf `%d`
(emctaskmain.cc:3245-3262): on only for a leading integer ≥ 1, so `0.5` and `-1`
are off. No config in the repo sets it.

**With NO_FORCE_HOMING set, lines that move are still refused** while unhomed —
an unhomed machine has no soft limits to stop the move. `mdi._needs_homing`
decides that: a small word parse, not an interpreter, that errs towards "yes".
Like close_and_downcase (interp_internal.cc:70-107) it first drops comments and
blanks, tabs and CRs, so `X - 10` reads as `X-10`; named parameters become `#0`,
and bracketed expressions count as numbers. Then:

- **motion:** a motion G code — group 1 of `_gees` (interp_array.cc:81-132)
  without G80: G0–G3, G5, G5.1, G5.2, G33, G33.1, G38.2–G38.5, the lathe cycles
  G70–G72.x, G73, G74, G76, G81–G89 — plus, by choice, G5.3 (group 0) and
  G28/G30; axis words XYZABCUVW and the polar words `@` and
  `^` (a bare `X10` re-runs the modal motion code), whatever their value is
  written as (`X#1`, `X#<name>`, `X.5`); I, J, K or R words alone; an O-word
  (call or flow control: its body is not visible here); a G word whose number is
  an expression or parameter (`G#1`, `G[1]`); an unbalanced bracket;
- **not motion:** a G10/G52/G92 line (all its words are data); G43.1's axis words
  (tool offsets, g-code.adoc:1517-1530); G20/G21, G17–G19, G90/G91, G54–G59.3,
  G28.1/G30.1 without axis words, G4, and M, S, F, T words alone.

Not seen yet (Known gaps): a value written as a unary function with no bracket
around the function (`XSIN[30]`, `GFUP[27.5]`; `X[SIN[30]]` is seen), M98
subroutine calls, M6 with `[EMCIO]TOOL_CHANGE_AT_G30`, `TOOL_CHANGE_POSITION` or
`TOOL_CHANGE_QUILL_UP`, and remapped codes.

`LinuxCncAdapter` then switches to MDI mode (bounded, checked) and sends the
line. A line longer than the 254-character NML field is a refusal, not an
exception. A line sent while an earlier MDI command still runs is not refused:
LinuxCNC queues MDI lines.

### F. Probe is not a tool

Removed from the tool table. Stated on both the Takımlar and Probing screens:
*"Prob bir HAL girişidir, takım tablosu kaydı DEĞİLDİR."*

### G. Jog

Jogging is allowed unhomed — that is how the switches are found and the travel
measured. `LinuxCncAdapter.jog` does what axis.py's get_jog_mode does
(:3225-3242):

- **Before homing, a joint jog.** Motion is in free mode, where an axis jog
  carries joint number -1 and is dropped ("Joint jog requested for undefined
  joint number", command.c:436-443). The joint is the first one carrying the
  axis letter.
- **After homing, a teleop (axis) jog**, addressed by the fixed axis index
  XYZABCUVW. With identity kinematics and every joint homed the adapter asks for
  teleop; otherwise it keeps whatever mode motion is in.
- It switches to MANUAL first, and switches motion's mode if needed, each with a
  bounded wait and a read-back. Motion drops a teleop request while not in
  position (control.c:927-958); the jog is then refused, not sent in the wrong
  mode.
- **Step:** incremental jogs only (`JOG_INCREMENT`). The step is the UI's own
  setting — chips 0.01 / 0.1 / 1 / 10 / 50 mm, 1 mm by default on both adapters.
  `LinuxCncAdapter.set_jog_step` accepts only a positive finite number: motion
  takes the increment as a magnitude and the direction from the velocity's sign
  (command.c:873-877), so a negative step would reverse the jog.
- **Velocity:** `[DISPLAY]DEFAULT_LINEAR_VELOCITY`, falling back to
  `[TRAJ]DEFAULT_LINEAR_VELOCITY` — axis.py's lookup (:3400-3403); 1.0 mm/s on
  the rig. If neither is readable the jog is refused, never run at an invented
  speed.
- Refused while the machine is not ON, and while a program or MDI command runs.

The jog card's disabled banner and button titles show the view model's
`jog_disabled_reason`: `Makine devre dışı — jog kapalı.`, or `Makine durumu
bilinmiyor — jog kapalı.` while LinuxCNC's status is unknown — never a machine
state that was not read. `MockAdapter` moves the position by one step at once.

### H. Programs, feed override and coordinate systems

**Program start** (both adapters): refused with the first of
`MachineState.start_blocks` — hardware cut, status unknown or software estop,
unhomed (or no `[JOINT_n]` at all), no link — then without a loaded program, and
on `LinuxCncAdapter` while one already runs. It then switches to AUTO (bounded,
checked) and sends `auto(AUTO_RUN, 0)`.

**Program stop:** `abort()` first, unconditionally — stopping must not wait on a
read — then a bounded wait and a check that the interpreter is idle (`Durdurma
gönderildi — LinuxCNC henüz boşta değil.` otherwise).

**Program load** (`LinuxCncAdapter.load_program`, as axis.py's open_file_guts):
refused while a program runs, while any joint is homing, and, with the machine
ON, while motion is not in position (`stat.inpos`, i.e. a joint jog still
moving); out of ON the flag is not consulted. Loading needs no ON: task takes
SET_MODE, PLAN_SYNCH and PLAN_OPEN in estop too (emctaskmain.cc:807-844). After
`program_open`, failure is RCS_ERROR or `stat.file` not naming the file. A wait
that timed out while `stat.file` names the file counts as loaded, because task
reports DONE only once motion is quiet; and `stat.file` alone is not proof,
because task never clears it on a failed open. A path over the 254-character NML
field is a refusal. **Not wired yet:** no HTTP action calls `load_program`,
because the Program screen lists no files, and `render_program` has no markup
for a file list either: its non-empty branch renders nothing.

**Feed override:** sent as `command.feedrate(scale)`, as axis.py does. Motion
clamps only negative values, so the ceiling `[DISPLAY]MAX_FEED_OVERRIDE` is
applied here, in whole percent and rounded as axis.py rounds it
(`int(x * 100 + 0.5)`, axis.py:3353-3355). The key missing from an existing INI
means 100 %; no INI at all means feed changes are refused. 0 % is valid;
negative values are refused. The UI offers 50 / 100 / 150 %; the rig's
`MAX_FEED_OVERRIDE = 1.0`, so on `LinuxCncAdapter` 150 % is clamped with
`Besleme %150 istendi; INI sınırı %100 uygulandı.` `MockAdapter` takes any number
(Known gaps).

**Coordinate system:** the UI offers G54–G56. `LinuxCncAdapter.set_wcs` accepts
G54–G59 and sends the code as an MDI line, so the MDI gate applies — it is
refused before homing. G59.1–G59.3 are refused by choice, though they are shown
when `stat.g5x_index` reports 7–9. `MockAdapter` accepts any string, with no
gate (Known gaps).

### I. Status unknown

`MockAdapter` always knows its state. `LinuxCncAdapter` may not: when
`stat.poll()` raises (LinuxCNC gone), the state carries `status_known=False` and
no positions, and the view refuses to show placeholders as readings:

- the header reads **DURUM BİLİNMİYOR**, in the error colour;
- the DRO keeps one row per INI axis, with `—` for work and machine position and
  a muted `—` in the homed column instead of `ref yok`;
- the WCS reads `—`, no feed-override chip is marked active, the homing chips and
  the software-estop row say `bilinmiyor`, the program fields are empty;
- program start is blocked with `Makine durumu bilinmiyor — LinuxCNC durumu
  okunamıyor.`, and jog and MDI are off with `Makine durumu bilinmiyor — jog
  kapalı.` / `… MDI kabul edilmiyor.`;
- the link card and drive power still come from the HAL pins, read separately,
  and keep their own meaning.

The outage is logged once as an ERROR alarm and the recovery once as INFO.
Actions refuse as described under "How `LinuxCncAdapter` drives LinuxCNC".

---

## Tests

```bash
make test          # 808 tests, everything incl. scripts/test_grbl2ini.py
make coverage      # 99.6 %
```

`ui/tests/run_all.py` discovers both `ui/tests` and `scripts`, so the
pre-existing `test_grbl2ini.py` runs in the same command. No test is an expected
failure.

| File | Tests | Covers |
|---|---|---|
| `test_app_cli.py` | 26 | `main()` and `serve()` with a stub server (no socket is bound); `--dump` for every profile × screen, the same body the live server sends; exit 2 and the list of valid screens for an unknown or empty name; `python -m bettercnc.app --dump` in a subprocess; inline JSON cannot close its `<script>`; no file:line citations into production code in the server's files (`app.py`, `render.py` and their two test files) |
| `test_capabilities.py` | 26 | the never-hidden contract, per capability; profile completeness; TODO vs ABSENT |
| `test_config_traceability.py` | 25 | every hardware number vs the INI, read with an independent parser |
| `test_edge_cases.py` | 74 | edited, sparse and half-measured INIs (continuation lines, repeats, section names, empty values, HOME_IGNORE_LIMITS parsing, comment runs); measured travel reaching every screen and the profile; the status-unknown display; a wired estop input; status-label priority; link trouble reaching the gate; the adapter contract; the LinuxCNC citations re-checked against `reference/` |
| `test_estop.py` | 27 | hardware/software separation at three levels |
| `test_homing_gate.py` | 32 | the gate, order from config, the drift detector, and the docs read from disk |
| `test_inputmap_and_link.py` | 46 | verified input map, Z asymmetry, `.in_not`, link health incl. unreadable counters and packet loss alone |
| `test_linuxcnc_adapter.py` | 171 | `LinuxCncAdapter` against the fake: the exact command sequence of every action and that LinuxCNC (the fake) refused nothing; joint vs teleop jog, re-homing, the homing refusal, the estop channel, reset deadline and rig latch, program load/start/stop, feed ceiling, WCS, INI values flowing through, bounded waits, the status channel lost and every action under it; the fake checked against the binding when `reference/` is present |
| `test_mdi_tools_adapter.py` | 76 | MDI entry/history; the shared gate (`_needs_homing`, `mdi_refusal`); probe-is-not-a-tool; `map_linuxcnc_state` (24 tests); the mock following the INI; the mock's own lock |
| `test_prototype_corrections.py` | 14 | table-driven guard on all 24 corrections |
| `test_server_and_iniload.py` | 53 | HTTP routes, malformed input, body limits, the request timeout, screen validation on POST; `Session` locking (the estop outside the lock, with a blocked jog); the status-unknown DRO; INI edge cases |
| `test_unmeasured.py` | 36 | travel, latency and servo period unmeasured; no fabricated default leaks |
| `scripts/test_grbl2ini.py` | 49 | the pre-existing GRBL translation tooling |

Support files, not tests:

| File | What it is |
|---|---|
| `fake_linuxcnc.py` | fake `linuxcnc` and `hal` modules derived from the LinuxCNC 2.9 source, every rule cited: a task-planner and motion model that refuses what LinuxCNC refuses and reports it on the error channel; `stat` changes only on `poll()`; `wait_complete()` times out while motion moves, and `wait_gate` can hold it open for a threaded test; motion drops a teleop request while not in position; the rig's `estop_latch` (`fault_in`, `user_enable_out_in_chain` = False as `test-rig.hal` wires it, `estop_clear_after_polls`); `channels_valid = False` makes the status and error channels raise; `stat_polls` counts `poll()` calls; `installed()` always restores `sys.modules` |
| `helpers.py` | `view` / `html` / `all_html`; `ini_raw_values`, an INI reader independent of iniload; `rig_profile`, the test-rig profile on a temp copy of `test-rig.ini` with line-by-line edits, inserts into the first `[SECTION]` and a transform, where every edit must hit an existing key; `strip_markers`, the "travel was measured" edit |
| `run_all.py` | discovers `ui/tests` and `scripts` |

The fidelity tests (in `test_linuxcnc_adapter.py` and `test_edge_cases.py`) read
`reference/linuxcnc`, the gitignored 2.9 clone: constants, command method names
and argument formats, stat attributes, joint dict keys, message type numbers, the
`hal` functions, the `estop_latch` component and the rig's HAL wiring, the GIL in
`wait_complete`, every refusal text the fake emits, and the LinuxCNC facts the code cites.
They match content, not line numbers: a citation's line range is not checked. Without `reference/` those 18 tests skip and the rest still run.

### Coverage: 99.6 %

```
module                   lines  covered      pct
------------------------------------------------
bettercnc.capabilities      94       93    98.9%
bettercnc.adapter          771      763    99.0%
bettercnc.app              185      184    99.5%
bettercnc.configmodel      187      186    99.5%
bettercnc.render           500      499    99.8%
bettercnc.iniload          139      139   100.0%
bettercnc.mdi               59       59   100.0%
bettercnc.profiles         293      293   100.0%
bettercnc.state            233      233   100.0%
bettercnc.viewmodel        449      449   100.0%
------------------------------------------------
TOTAL                     2910     2898    99.6%
```

`ui/tools/covreport.py` discovers the package's modules from the directory; its
hand-kept list had silently left out `bettercnc.mdi` when that module was added.

The 12 uncovered lines: the eight `...` bodies of `MachineAdapter`'s abstract
methods; `sys.exit(main())` under `app.py`'s `__main__` guard, which runs only in
`test_app_cli.py`'s subprocess, out of the tracer's reach; `configmodel`'s
TYPE_CHECKING-only import; `render_program`'s non-empty file branch, unreachable
because `viewmodel.program_screen` always returns no files; and the message of
the import-time assert in `capabilities.py`, evaluated only if that assert fails.

### Mutation testing

Line coverage says a line ran, not that a test would notice it changing. So the
suite was mutation-tested over three rounds, on scratch copies of the tree (the
harness is not in the repo): each mutant one small change to production code or
the fake, each run against the full suite. Round 3 killed 136 of 168 mutants
(81.0 %). Of the 32 survivors, 11 are strictly equivalent and 11 near-equivalent
(for example a joint both homing and homed, which LinuxCNC never reports), so
136 of the 146 left once the equivalent and near-equivalent ones are set aside
were killed (93.2 %). The ten that
survive are listed under What is NOT covered.

### What is NOT covered — plainly

**1. `LinuxCncAdapter` on a real machine.** It is now tested —
`test_linuxcnc_adapter.py` (171 tests) drives every action against
`fake_linuxcnc.py` and asserts the exact command sequence and that the fake
refused nothing — but it has never run against LinuxCNC. The
fake is only as right as our reading of the source. It does not model echo
latency or real time (commands complete synchronously), the GIL hold of a real
wait (a fake wait releases it), soft and hard limits, velocity clamping, the
homing switch search, the interpreter, spindle, coolant, tools or volatile
homing; `stat.inpos` is false only for free-mode jogs and homing, and leaving
teleop is instantaneous. So timing is unmeasured: `WAIT_TIMEOUT_S` = 0.25 s, the
3 × `[EMCIO]CYCLE_TIME` reset deadline, the 64-message drain cap and the 10 s
request timeout are chosen values. This is still the code most likely to be wrong
on first contact with the real machine — now in its timing more than in its
sequencing.

**2. The mock is not the real adapter.** It homes and jogs instantly, shows
simulated link counters and seed alarms, and accepts some inputs the real
adapter refuses (Known gaps). Tests that pass on the mock prove the rules, not
the machine.

**3. The browser.** `app.js` has no tests — no test runner for JS without
dependencies. It is deliberately about 100 lines that forward clicks and swap
`innerHTML`; all logic worth testing is in Python. A failed request is no
longer swallowed (2026-10-20): three failed polls, or one failed action, cover
the screen with `BAĞLANTI YOK` and "the values are stale", and in kiosk mode
(`--boot-url`, deploy/README.md) the page goes back to the boot screen - seen
working in `sim/sim.sh kiosk`. Exercised by hand once, in a real browser against `sim/` (2026-10-19):
typed MDI text and focus survive the 1 Hz re-render, and two bugs were found
and fixed - a click into the MDI field posted an empty MDI line (the click
handler matched the MDI `<form>`'s data-action), and the re-focus after each
poll scrolled the page back to the MDI field, so the estop button could not be
scrolled to. Both are pinned as text in `test_app_cli.TestClickRouting`.

**4. CSS and visual appearance.** No screenshot or layout testing. A stylesheet
change could make the UI unreadable with every test passing.

**5. Anything about the real machine.** These tests prove the UI agrees with the
repo's config and docs. They cannot prove the config is right about the machine —
travel is unmeasured, Y's DIR wiring is a known active fault
(`docs/DURUM.md:12`), and latency was never measured.

**6. Concurrency, partly.** Tested: `TestSessionLocking` blocks a jog inside the
adapter and shows the software estop still reaches it while every other action
waits for the lock; `TestEstop` holds a fake `wait_complete` open and sends the
estop from another thread; `TestMockLock` covers the mock's own lock. Not
tested: the GIL. A real `wait_complete` or command send holds it, so the estop
thread cannot run until that call returns; a fake wait releases it. That limit is
argued from the source (a fidelity test checks the binding's single
`Py_BEGIN_ALLOW_THREADS`), not exercised.

**7. Ten surviving mutants.** Behaviour no test pins yet: a tab or CR inside an
MDI word (`X\t10`); an O-word whose number is an expression or parameter
(`O#1 call`); the home-all refusal when only the last joint is homing (Z on the
rig, which homes first); a key line with no value leaving a pending ASSUMPTION
block in force; `stat.inpos` being ignored in ESTOP_RESET; OPERATOR_ERROR
continuing the error drain; the unbalanced-bracket MDI test, which passes through
G1 instead of the bracket; and three self-checks of the fake (the `stat_polls`
counter, and the estop latch's start and reset states).

---

## Known gaps and next steps

Open, and not to be read as fixed:

- **No on-screen simulation marker.** `MockAdapter`, the default adapter, shows
  simulated values — link counters, a DRO, seeded `hm2_eth` and stepgen events
  stamped with the current time, even on the marble profile — and nothing on
  any screen says SİMÜLASYON. Only the server's stderr says it is the mock.
- **The estop message can be overwritten.** An action that finishes after a
  concurrent software estop writes its own message last, so the message line can
  read e.g. `Referans alma başlatıldı.` right after the estop. `task_state` and
  the header still show the estop.
- **Keyboard focus does not survive the 1 Hz re-render.** `swap()` restores
  focus only to a field with a `name` (the MDI input); a button reached with
  Tab loses focus at the next poll, so slow Tab navigation goes nowhere
  (seen in `sim/` on 2026-10-20). The operator uses touch or a mouse.
- **The alarm list has no cap.** A persistent error would add a row on every
  1 Hz poll; the 64-message drain cap bounds one drain, not the growth across
  drains.
- **Joints are numbered by position.** `map_linuxcnc_state` enumerates the joint
  list instead of using the joint numbers, so with a `[JOINT_n]` missing, the
  real adapter reads a later axis's homing from the wrong `stat.joint` entry and
  gives it the wrong joint number and the wrong (or a default) HOME_SEQUENCE and
  HOME_IGNORE_LIMITS, so a chip can show HOMED for an unhomed axis, while
  configmodel and `MockAdapter` keep the real joint numbers.
- **MDI gate gaps (latent).** `_needs_homing` does not see values written as a
  unary function (`XSIN[30]`, `GFUP[27.5]`, which is G28) and does not treat M98
  as motion, nor M6 when `[EMCIO]TOOL_CHANGE_AT_G30`, `TOOL_CHANGE_POSITION` or
  `TOOL_CHANGE_QUILL_UP` makes a tool change move (rs274ngc_pre.cc:869-870,
  taskclass.cc:468), nor remapped codes. A non-motion MDI line sent while homing runs is not refused up
  front, and the switch to MDI mode runs emcTaskAbort (emctask.cc:286-289),
  which can cancel the homing. All of this matters only with
  `[TRAJ]NO_FORCE_HOMING`, which no config sets.
- **`MockAdapter` accepts inputs the real adapter refuses:** an unknown jog axis
  (it reports a jog and moves nothing), a NaN, infinite or non-positive jog step,
  a negative feed or one above the INI ceiling, any feed change with no INI, and
  any WCS string, with no MDI gate. It also skips a state check: it jogs and
  homes while a program runs, which `LinuxCncAdapter` refuses (`Program çalışırken
  …`); `MachineState.can_jog` does not look at `program_running`, so the jog
  buttons stay live then.
- **The estop's HTTP response can wait.** `Session.view` still takes the session
  lock, so the page answering an estop waits for a bounded action in progress.
  The command itself has gone out.
- **`LinuxCncAdapter` has never run on a real machine**, and its timing
  constants are chosen, not measured: `WAIT_TIMEOUT_S` = 0.25 s, the
  3 × `[EMCIO]CYCLE_TIME` reset deadline, the 64-message drain cap and the 10 s
  request timeout. They are the first things to check on the rig. (In `sim/`,
  on a non-realtime kernel, none of them caused a refusal; that says nothing
  about the rig.)

Found in `sim/` on 2026-10-19 and fixed on 2026-10-20, each re-checked in
`sim/` against LinuxCNC 2.9.9 (tests: `test_linuxcnc_adapter.py`
TestFeedChipsFollowTheCeiling, TestStopNotFromThisUi, TestStartupProbe,
TestToolMmapFidelity; `test_app_cli.TestAdapterChoice`):

- ~~**Feed buttons offered 150 %**~~ on the rig, whose
  `[DISPLAY]MAX_FEED_OVERRIDE` is 1.0 (the adapter clamped it). Now a chip
  above the ceiling renders disabled, with `INI sınırı %100
  ([DISPLAY]MAX_FEED_OVERRIDE)` written under the row; with no INI every chip
  is disabled. The chips and the adapter's clamp read one value,
  `configmodel.max_feed_override_pct`.
- ~~**An estop raised in HAL left the message line stale.**~~ A watchdog bite
  put LinuxCNC in ESTOP and the message line kept `Besleme %150 istendi...`.
  Now `LinuxCncAdapter._note_task_state` reports LinuxCNC leaving ON without
  an action of this UI, once, as an ERROR alarm and on the message line
  (`Watchdog ısırdı — LinuxCNC acil stopa geçti...` when the watchdog pin is
  set; otherwise `...acil stopa geçti — komut bu arayüzden gelmedi` or
  `...makineyi kapattı — ...`). The UI's own software estop is not reported
  again; LinuxCNC's own error text, drained in the same snapshot, is kept.
  "The estop message can be overwritten" above is still open.
- ~~**The UI started before LinuxCNC.**~~ The cause is in the binding, not
  in `stat()`: the first `poll()` in a process opens LinuxCNC's tool mmap
  (`$HOME/.tool.mmap`), and if it is missing, every later `poll()` of every
  `stat` in that process returns NULL without an exception - `SystemError`,
  for good (emcmodule.cc:286-305, tooldata_mmap.cc:74-80). Started first,
  the UI came up, said "bağlantı yok" once, then failed every request. Now
  `LinuxCncAdapter._probe_status` polls twice at construction and refuses
  with `LinuxCncUnavailable`, which `app.main` prints in Turkish, exit 1:
  `LinuxCNC çalışmıyor ...` (first poll), or the `.tool.mmap` / same-HOME
  message (second poll: LinuxCNC runs, but the UI runs as another user or
  HOME). A SystemError later is treated as lost status.

Hardened on 2026-10-20, each checked in `sim/` against LinuxCNC 2.9.9 and
real `halcmd` (values and reasons for every timeout: deploy/README.md,
"Süreler"):

- **A lost board link can now be reset.** The hm2 watchdog's `has_bit` and
  the driver's `io_error` stay set until someone clears them
  (watchdog.c:50-52, :259), and `has_bit` holds the rig's estop latch open,
  so «Yazılım sıfırla» could never succeed after a bite - only a LinuxCNC
  restart could. `LinuxCncAdapter._clear_link_faults` now clears both with
  `halcmd` (`hal.set_p` refuses a pin on a signal, halmodule.cc:970-975) and
  the reset then unhomes every joint (`unhome(-1)`, immediate in ESTOP,
  emctaskmain.cc:814): while the board was cut off its step generators kept
  counting with their outputs dropped (hostmot2.adoc:96-103). A link still
  down bites again and the reset says so (`LINK_NOT_RESTORED`). Tests:
  `TestLinkRecovery`.
- **No driver jargon on screen.** "Watchdog ısırdı", "sessiz", "io_error"
  are gone from labels, values, problems and messages: the operator reads
  what happened and what to do (`Kartla bağlantı koptu — makine durduruldu.
  Ethernet kablosunu ve kartın beslemesini kontrol edin, sonra «Yazılım
  sıfırla»ya basın.`); the HAL names stay on the rows for maintenance.
  Pinned by `test_no_driver_jargon_reaches_the_operator`.
- **A hung UI is noticed.** The overlay is now driven by time since the last
  good answer, every request has a time limit and only one poll is in
  flight; before, a server that hung (rather than failed) never triggered it.
- **Kiosk supervision** (`--exit-if-linuxcnc-lost 3`): when LinuxCNC's
  status no longer reads, or one of its processes (linuxcncsvr, task,
  iocontrol) has died, the UI exits, LinuxCNC shuts down and the kiosk
  starts it clean with the reason on the boot screen.
- **A task that hangs without dying is detected too.** Its heartbeat
  (emc_nml.hh:1172, emctask.cc:739) is not exported by the Python binding,
  so the kernel's count of its voluntary context switches stands in for it:
  task and iocontrol sleep once per cycle (in `sim/` ~87/s and ~9/s, 0 when
  frozen). No change for `LOOP_STALL_S` = 2 s and the status counts as lost:
  the screen shows DURUM BİLİNMİYOR instead of the stale values, actions
  refuse without sending (a send to a stuck task spins 5 s holding the GIL),
  the software estop still goes out, and the kiosk restarts LinuxCNC. In
  `sim/` with the task frozen: 3 s to DURUM BİLİNMİYOR; in the kiosk the UI
  exited at 5.8 s and was back at 13.7 s. Still not visible: a loop that
  keeps cycling while doing nothing useful. Tests: `TestStuckLoop`.
- **The browser never shows** (deploy/README.md, "Neler kilitli"): a kiosk
  keymap where Ctrl, Alt, Super, F-keys and browser keys produce nothing
  (checked through libxkbcommon, `TestKioskKeymap`), the page's own layer in
  kiosk mode (no context menu, drag, selection, zoom, back buttons), and
  Chromium policy (only our two origins and the wait page; new tab and home
  are our boot screen; no search). With the keymap bypassed, nine browser
  shortcuts sent into the kiosk changed nothing on screen. Tab is off too
  (keymap and page): it walks a focus ring across the buttons, which gives
  the browser away; in kiosk mode tooltips (`title`) are removed, no focus
  outline is drawn, the cursor stays an arrow and the scrollbar is themed.
- **Browser watch** (`bettercnc.browserwatch`): a page that stops asking
  both servers is restarted, judged by their `client_age_s`. Its first
  version restarted the browser on every UI restart; the page now gets
  `BOOT_HANDOFF_S` to reach the boot screen.
- **Chromium's own pages are kept off the screen** (`browserwatch.Screen`).
  They cannot be changed ("Aw, Snap", "can't connect"), so they are
  replaced: the browser runs with `--remote-debugging-pipe` (two pipes only
  the watch holds, no port), and with browser-level Target commands - the
  policy refuses attaching to a page - the watch lists the tabs every 0.5 s
  and hears of a crash at once. A crashed tab, a page on one of our servers
  that does not answer, or anything not ours for `NOT_OURS_S` is replaced
  by the UI if it answers, else the boot screen, else
  `deploy/files/wait.html` ("Bir hata oluştu — lütfen bekleyin"), which needs
  no server. `app.js` goes to the boot screen only once it answers, and the
  browser starts on the wait page if the boot screen does not. In `sim/`,
  with screenshots ~17 ms apart: a crashed tab was back on the UI in ~80 ms
  with one white frame, no "Aw, Snap" in any frame; with both servers down
  and the browser restarting, black, the wait page, then the UI. The guard's
  first version kept its tab table from events only, missed the title
  updates, took our own pages for foreign ones and closed them every 2 s.
  Tests: `TestScreen`, `TestScreenPipeFaults`.

**Deployment rule from the last one (Pi 4 included):** start the UI after
LinuxCNC, as the user that started LinuxCNC (same `HOME`). The kiosk
(deploy/README.md) does it by construction: the UI is LinuxCNC's own
`[DISPLAY]` program, `deploy/bettercnc-display`.

Also open, found while writing this:

- **Invented homing config without an INI (marble).** `adapter._joint_config`
  (and `configmodel.joint_config_from_profile`) invent HOME_SEQUENCE 0/1/2,
  HOME_IGNORE_LIMITS False and HOME_SEARCH_VEL 0.0. The Kontrol screen then shows
  `Gerekli sıra: X → Y → Z ([JOINT_n]HOME_SEQUENCE)`, `arama yok` on every homing
  chip and the start block `Referans alınmadı: X, Y, Z. Sıra: X → Y → Z.`: an
  invented order presented as read from the INI.
- **Jog reason when the link is down.** With the board link not connected but
  the machine ON, the jog card and MDI panel say `Makine devre dışı — …`
  (`viewmodel._motion_off_reason` names only the estop and the unknown status),
  while the header can read `HAZIR`.
- **No profile field names an estop-input pin.** When a machine wires one, the
  profile needs that field and `LinuxCncAdapter._read_hal` must read the pin into
  `hal_values["estop_input"]` — never `stat.estop`.

Next steps:

- **`LinuxCncAdapter` needs one session on the machine.** Start the UI with
  `--adapter linuxcnc` after LinuxCNC is up (the banner then says
  `LinuxCncAdapter — komutlar LinuxCNC'ye GİDİYOR`, decided from the adapter
  built: `app.adapter_banner`) and compare against `halmeter`. The checklist
  below passed in `sim/` against LinuxCNC 2.9.9 on 2026-10-19 - with simulated
  switches, a stand-in for the link pins, no steps and no realtime kernel - so
  on the rig it is a re-check, timing first: that the HAL component opens and
  the link pins read; a joint jog before homing (sim: X +1.000); the software
  estop (sim: ESTOP_RESET mid-jog, motion stopped, the reset sent ON only); a
  reset from a real ESTOP within the 0.3 s deadline (sim: watchdog bite ->
  ESTOP; reset refused while the fault held, accepted once cleared); homing
  without a wait (sim: 0.1 s), Z -> Y -> X, and a refused second press; whether
  0.25 s waits cause refusals that succeed on retry (sim: none); and that the
  UI process exits with LinuxCNC (sim: within about 2 s). There is no hardware
  estop on the rig: keep a hand on the power.
- **Y axis DIR fault** (`docs/DURUM.md:12`) is rendered as an error in the mock's
  alarm log and, from the profile's notes, in the Kontrol warnings. It is
  hardcoded there rather than read from a live pin, because it is a hardware
  fault (location not yet measured), not a machine state.
- **Marble profile is a skeleton.** Eleven TODOs, no INI, no input map. It exists
  now so the gaps are visible; fill it as `docs/01-machine-survey.md` fills.
  Its invented homing config is listed under "Also open".
- **No program selection and no G-code preview.** The Program screen lists no
  files, so `load_program` has no route. The prototype's path SVG was driven by
  an invented toolpath; real preview needs parsing a real `.ngc`, which is a
  separate piece of work.
