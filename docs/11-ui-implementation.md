# 11 — UI Implementation

Implementation of the Claude Design prototype in `design/`, rebuilt against the
verified hardware. Code lives in [ui/](../ui/), run instructions in
[ui/README.md](../ui/README.md).

**Status: implementation VERIFIED against the repo's own config and docs; not yet
run against a live LinuxCNC instance.** It renders from a mock adapter.

---

## Summary

| | |
|---|---|
| Stack | Python 3 stdlib only + server-side HTML |
| Tests | 321, all passing, `make test` |
| Coverage | **94.1 %** (`make coverage`) |
| Screens | 6, as the prototype: Kontrol / Program / Probing / Takımlar / Alarmlar / Makine |
| Profiles | `test-rig` (verified) and `marble` (TODO placeholders) |
| Prototype facts corrected | 24, table below |
| Machine connection | none — `MockAdapter`; `LinuxCncAdapter` written and ready |

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

That is why `render.py` returns strings and `app.js` is 90 lines that only
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

All eleven are ABSENT on the test rig and TODO on marble — so on both profiles
every capability renders disabled, which is exactly the state the tests assert
capability by capability.

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
                  (this iteration)   (written, untested)
```

Nothing above `adapter.py` imports `linuxcnc` or `hal`, so the entire view layer
is testable off-machine.

### The mapping is a pure function

The riskiest code is translating LinuxCNC's status structure, so it is separated
out as `map_linuxcnc_state(stat, hal_values, profile, …) -> MachineState` and
tested against a hand-built `SimpleNamespace` — no machine, no `linuxcnc`
module. 17 tests cover it, including partially-populated `stat` objects.

Field names verified against the Python bindings
(`reference/linuxcnc/src/emc/usr_intf/axis/extensions/emcmodule.cc`):

| Field | Source | Used for |
|---|---|---|
| `stat.task_state` | emcmodule.cc:329 | software estop (`STATE_ON` = 4) |
| `stat.estop` | emcmodule.cc:418 | hardware chain, **only if an input exists** |
| `stat.joint[n]["homed"]` | emcmodule.cc:600 | homing gate |
| `stat.joint[n]["homing"]` | emcmodule.cc:599 | in-progress phase |
| `stat.actual_position` | emcmodule.cc:738 | machine DRO |
| `stat.g5x_offset` | emcmodule.cc:753 | work DRO |
| `stat.g5x_index` | emcmodule.cc:752 | WCS name (1 → G54) |
| `stat.feedrate` | emcmodule.cc | feed override % |
| `stat.file` / `.paused` / `.interp_state` | emcmodule.cc:337 | program state |

HAL pins, verified against the driver source:

| Pin | Source | Type |
|---|---|---|
| `hm2_7i92.0.packet-error-total` | hm2_eth.c:1558-1561 | u32 |
| `hm2_7i92.0.watchdog.has_bit` | watchdog.c:140-144 | bit |
| `hm2_7i92.0.io_error` | hostmot2.c:1446 | bit |

Read through `hal.get_value()` rather than a HAL component with connected pins,
so the UI is a passive observer and never appears in the HAL graph.

### What is mocked and what is real

| Real (traced to a repo file) | Mocked |
|---|---|
| Board identity, IO count, clock — from `board-dumps/readhmid-*` | Axis positions (start at 0, jog moves them) |
| SCALE, velocity, accel, step timing, HOME_SEQUENCE, HOME_IGNORE_LIMITS — read from `test-rig.ini` at runtime | Link health counters (0 / quiet / no error) |
| Input map, polarity, Z asymmetry — `docs/DURUM.md:69-80` | Alarm log seed entries |
| Kernel, NIC, host — `docs/DURUM.md:20-27` | Program progress |
| Capability reasons and requirements — `docs/MERMER-GECIS.md` §2 | |
| The estop and homing **rules** (enforced in `MockAdapter`, not faked) | |

The mock enforces the real rules rather than approximating them, so the tests
that matter — software cannot clear a hardware estop, program start is gated on
homing — exercise logic that will still be there when the adapter is swapped.

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
| `gpio.025` = flow sensor | **does not exist** — 5 inputs, not 6 | DURUM.md:69-80 |
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

When travel is finally measured and the ASSUMPTION comment deleted, the UI starts
showing numbers with no code change. That behaviour is tested both ways.

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
them clears a hardware cut.

### B. Homing as a gate

Three phases per joint (`not_homed` / `homing` / `homed`), and
`MachineState.start_blocks` returns an ordered list of reasons the program cannot
start. The button is genuinely `disabled` with the reason rendered, not just a
tooltip. Jogging stays allowed while unhomed — that is how you find the switches.

The required order comes from `[JOINT_n]HOME_SEQUENCE` in the INI, not from a
constant, and the shared home/limit warning is rendered prominently:

> Home ve limit aynı switch'te → HOME_IGNORE_LIMITS = YES. Referans alma
> sırasında limitler yok sayılır: switch'in yanlış tarafından başlanırsa ekseni
> durduracak hiçbir şey yoktur (ini-homing.adoc:46).

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

Unreadable pins render `—`, not `0` — "we could not read it" is not the same as
"it is zero". A disconnected link blocks program start.

### D. Soft limits not configured

`MachineConfig.soft_limits_configured` is False unless **every** axis has
measured travel. Surfaced three ways: a `KORUMA YOK` banner on the Makine
screen, an entry in the Kontrol screen's warning list, and the `soft_limits`
capability rendered disabled. The Z-bottom-switch absence is rendered alongside
it, since together they mean the bottom of Z travel has no protection at all.

### E. MDI text entry

A real `<input>` with Enter-to-submit, plus server-side history showing accepted
and rejected commands with the reason. Presets kept. Motion commands
(`G0`/`G1`/`G2`/`G3`/`G53`) are rejected while unhomed; `M5` and similar are
allowed. History is HTML-escaped — there is a test for `<script>`.

### F. Probe is not a tool

Removed from the tool table. Stated on both the Takımlar and Probing screens:
*"Prob bir HAL girişidir, takım tablosu kaydı DEĞİLDİR."*

---

## Tests

```bash
make test          # 321 tests, everything incl. scripts/test_grbl2ini.py
make coverage      # 94.1 %
```

`ui/tests/run_all.py` discovers both `ui/tests` and `scripts`, so the
pre-existing `test_grbl2ini.py` runs in the same command.

| File | Covers |
|---|---|
| `test_capabilities.py` | the never-hidden contract, per capability; profile completeness; TODO vs ABSENT |
| `test_config_traceability.py` | every hardware number vs the INI, read with an independent parser |
| `test_estop.py` | hardware/software separation at three levels |
| `test_homing_gate.py` | the gate, order from config, the drift detector, and the docs read from disk |
| `test_unmeasured.py` | travel and latency unmeasured; no fabricated default leaks |
| `test_inputmap_and_link.py` | verified input map, Z asymmetry, `.in_not`, link health |
| `test_mdi_tools_adapter.py` | MDI entry/history, probe-is-not-a-tool, the LinuxCNC mapping |
| `test_prototype_corrections.py` | table-driven guard on all 24 corrections |
| `test_server_and_iniload.py` | HTTP routes, malformed input, INI edge cases |

### Coverage: 94.1 %

```
bettercnc.adapter          366      284    77.6%
bettercnc.app              156      129    82.7%
bettercnc.configmodel      184      177    96.2%
bettercnc.state            209      204    97.6%
bettercnc.profiles         247      243    98.4%
bettercnc.iniload           90       89    98.9%
bettercnc.capabilities      94       93    98.9%
bettercnc.viewmodel        410      407    99.3%
bettercnc.render           487      484    99.4%
------------------------------------------------
TOTAL                     2243     2110    94.1%
```

### What is NOT covered — plainly

**1. `LinuxCncAdapter` (most of `adapter.py`'s 22 %).** Every method that touches
`linuxcnc` or `hal`. It cannot be executed without a machine, so it is written,
reviewed, cited to the bindings — and unproven. **This is the code most likely to
be wrong on first contact with the real machine.** Mitigation: the risky
translation is factored out into the pure `map_linuxcnc_state`, which *is* tested.
What remains untested is the plumbing around it: `command.mode()` sequencing,
`error_channel.poll()` draining, `hal.get_value()` behaviour when a pin is
missing.

**2. `serve()` and `main()` in `app.py`.** The blocking server loop and CLI
argument parsing. The request *handlers* are tested through a real HTTP server on
a random port; only the outer wiring is not.

**3. The browser.** `app.js` has no tests — no test runner for JS without
dependencies. It is deliberately ~90 lines that forward clicks and swap
`innerHTML`; all logic worth testing is in Python. Untested: focus/caret
preservation across a re-render, and `fetch` failure paths.

**4. CSS and visual appearance.** No screenshot or layout testing. A stylesheet
change could make the UI unreadable with every test passing.

**5. Anything about the real machine.** These tests prove the UI agrees with the
repo's config and docs. They cannot prove the config is right about the machine —
travel is unmeasured, Y's DIR wiring is a known active fault
(`docs/DURUM.md:12`), and latency was never measured.

**6. Concurrency.** `Session` has a lock around adapter access, but there is no
test for concurrent requests racing.

---

## Known gaps and next steps

- **Y axis DIR fault** (`docs/DURUM.md:12`) is rendered as an error in the alarm
  log, from the profile's notes. It is hardcoded there rather than read from a
  live pin, because it is a wiring fault, not a machine state.
- **`LinuxCncAdapter` needs one session on the machine.** Swap the line in
  `build_session`, run `make ui`, and compare against `halmeter`.
- **Marble profile is a skeleton.** Eleven TODOs, no INI, no input map. It exists
  now so the gaps are visible; fill it as `docs/01-machine-survey.md` fills.
- **No G-code preview.** The prototype's path SVG was driven by an invented
  toolpath. Real preview needs parsing a real `.ngc`, which is a separate piece
  of work.
