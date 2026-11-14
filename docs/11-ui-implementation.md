# 11 — UI Implementation

Implementation of the Claude Design prototype in `design/`, rebuilt against the
verified hardware. Code lives in [ui/](../ui/), run instructions in
[ui/README.md](../ui/README.md).

**Status: implementation VERIFIED against the repo's own config and docs;
`LinuxCncAdapter` tested against a fake built from the LinuxCNC 2.9 source,
since 2026-10-19 run against a real LinuxCNC 2.9.9 in the simulator
([sim/](../sim/README.md)), and on 2026-11-12 run on the machine for the first time —
view-only, beside AXIS, through an out-of-repo viewer that cut its command paths
(what it showed: Known gaps). Its commands have never driven the machine.** The server
renders from a mock adapter by default (`--adapter linuxcnc` for the real one),
and says which only on the server's stderr; a `--dump` page carries no such
note.

---

## Summary

| | |
|---|---|
| Stack | Python 3 stdlib only + server-side HTML |
| Tests | 1574 on 2026-11-14, all passing, no expected failures, `make test` (1525 UI + 49 in `scripts/test_grbl2ini.py`; 4 keymap tests run only where libxkbcommon is, e.g. `sim/sim.sh test`); the suite also runs on arm64 + Python 3.13 in the simulator (`sim/sim.sh test`). End to end against LinuxCNC 2.9.9 in the simulator: `make sim-e2e` (Tests, below) - the baseline, state, stop, messages, limits, jog-rules, homing, program and run modules pass, with no expected failure left |
| Coverage | **99.6 %** (`make coverage`, 2026-11-14 after the P0 cleanup); `adapter.py` 99.0 %, `mdi.py`, `messages.py`, `motionrules.py`, `programs.py`, `render.py` and `viewmodel.py` 100 % |
| Screens | 6, as the prototype: Kontrol / Program / Probing / Takımlar / Alarmlar / Makine |
| Profiles | `test-rig` (verified) and `marble` (TODO placeholders) |
| Prototype facts corrected | 24, table below |
| Machine connection | none by default — the server runs `MockAdapter`; `--adapter linuxcnc` runs `LinuxCncAdapter`, which is tested against `ui/tests/fake_linuxcnc.py`, has run against LinuxCNC 2.9.9 in `sim/`, and on the machine only view-only beside AXIS (2026-11-12, an out-of-repo viewer with its commands cut); how to switch between AXIS and this UI on the rig, and the checks to run there: [12-operator-handover.md](12-operator-handover.md) |

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

That is why `render.py` returns strings and `app.js` is about 280 lines that
forward clicks, swap `innerHTML` and watch the connection (overlay, time limits,
the kiosk's boot hand-off). The view logic stayed in Python because that is
where it can be tested.

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

All eleven are TODO on marble. On the test rig ten are ABSENT — hardware the rig
does not have — and render disabled, capability by capability, on both profiles;
the eleventh follows the INI.

Ten of the test rig's eleven are fixed facts in `profiles.test_rig_profile`.
`soft_limits` is not: it is derived from the INI (`profiles._soft_limits_from_ini`),
through the same loader the screens use. While the travel limits carry their
provisional markers it is ABSENT, and its reason names the unmeasured axes
(`Strok ÖLÇÜLMEDİ (X, Y, Z)…`). Once the markers are removed it turns PRESENT
with no code change (see D below). The rig's travel was measured on 2026-11-12
(soft limits X 3–399, Y 3–387, Z −96.5–−3 mm) and its markers are removed, so on the
rig `soft_limits` is PRESENT.

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
                (server default)   (tested against a fake;
                                    machine: view-only so far)
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
module. 24 tests cover it in `TestLinuxCncMapping` (`test_mdi_tools_adapter.py`),
including partially-populated `stat` objects, the ESTOP_RESET state, a paused
program and the startup estop latch; `test_state_plumbing.py` covers the work
DRO and the motion fields below, pure, on the fake and in the view model.

Field names verified against the Python bindings
(`reference/linuxcnc/src/emc/usr_intf/axis/extensions/emcmodule.cc`):

| Field | Source | Used for |
|---|---|---|
| `stat.task_state` | emcmodule.cc:329 | software estop: anything but `STATE_ON` (4) is disabled, ESTOP_RESET included |
| `stat.estop` | emcmodule.cc:418 | **not read** — see A below |
| `stat.joint[n]["homed"]` | emcmodule.cc:599 | homing gate |
| `stat.joint[n]["homing"]` | emcmodule.cc:598 | in-progress phase |
| `stat.joint[n]["min_hard_limit"]` / `["max_hard_limit"]` | emcmodule.cc:604, :605 | `hard_limits`: motion's own switch flags, read every servo cycle with the machine off too (control.c:491-501) |
| `stat.joint[n]["override_limits"]` | emcmodule.cc:606 | `override_limits`: one value for every joint (taskintf.cc:963), read from joint 0 |
| `stat.actual_position` | emcmodule.cc:738 | machine DRO (the feedback) |
| `stat.position` | emcmodule.cc:756 | `AxisPosition.commanded`: motion's carte_pos_cmd (taskintf.cc:1600) |
| `stat.g5x_offset` / `.g92_offset` / `.tool_offset` / `.rotation_xy` | emcmodule.cc:753, :755, :765, :350 | work DRO (below); `g92_active` |
| `stat.g5x_index` | emcmodule.cc:754 | WCS name: 1–6 → G54–G59, 7–9 → G59.1–G59.3 (interp_convert.cc:1762-1789); anything else (0 before the first poll) → G54 |
| `stat.feedrate` | emcmodule.cc:372 | feed override %, rounded |
| `stat.file` / `.paused` / `.interp_state` | emcmodule.cc:342, :371, :337 | program state; `program_paused`, `interp_state` |
| `stat.task_mode` / `.motion_mode` | emcmodule.cc:328, :363 | `task_mode`, `motion_mode`, `mdi_running` |
| `stat.current_vel` / `.inpos` | emcmodule.cc:387, :366 | `moving` |
| `stat.motion_line` | emcmodule.cc:340 | `program_line` |

`LinuxCncAdapter` also reads `stat.kinematics_type` (:381), `.joints` (:360) and
`.homed` (:747), and the task mode, motion mode and `.inpos` for its own gates.
`stat.current_line` (:341) is deliberately not read: the interpreter reads
ahead, so in `sim/` it stood at a program's last motion line while
`motion_line` counted up to it. `fake_linuxcnc.STAT_ATTRIBUTES` is the checked
list: a test ties both the adapter module's docstring and the fake to the
binding, and another checks the emcmodule.cc line number of every row of that
docstring's table.

**The work DRO** is computed as AXIS computes it (`adapter.work_coordinates`,
after glcanon.py:1586-1596): the machine position minus the tool offset and the
G5x origin, X and Y turned by −`rotation_xy` about that origin, then minus the
G92 offset. Until 2026-11-13 it was the machine position minus G5x alone, so a
G92 offset did not show - one the MDI presets offer to clear (`G92.1`), and
one LinuxCNC keeps across restarts unless the INI disables that. The machine
column is still `stat.actual_position`.

**The motion fields** (plan WP02) were readings only when they were added; the
fields WP02 added to the stat read change none of `program_running`,
`status_label`, `can_jog` and the start gate
(`test_existing_semantics_unchanged`). Later plans act on them: the limit
recovery (WP05, L) and the jog gates (WP06, G: `MachineState.jog_block` reads
`program_paused`, `mdi_running` and the modes, the jog rule `moving`; a "not
read" value refuses a jog), and the program run (WP09, H: `program_running`,
`status_label` and `can_start_program` now read the task mode, the pause and
the MDI flag). Each has a "not read" value, `None` or `()`, which
`LinuxCncAdapter` reports while LinuxCNC's status is unknown
(`MachineState.as_status_unknown`, see I below). `MockAdapter` reports what
its own model defines (nothing moving, paused or running an MDI line, the
modes its machine is in) and "not read" for the rest:

- `hard_limits`: `HardLimit(joint, axis, min, max)` per joint, `True` = that
  end's switch input active. A home switch shared with a limit input reads
  `True` while its joint homes on it; an end with no switch (Z bottom) reads
  `False`. While the board link is down the flags keep their last value:
  hm2 stops refreshing its input pins (hostmot2.c:100-107).
- `override_limits`: motion's limit override is on.
- `moving`: ON, and `current_vel` above `MOVING_VEL_EPS` = 1e-6 or, in free
  (joint) mode, motion not in position. Only while ON: after a limit trip in
  the middle of a teleop jog the simulator sat in ESTOP_RESET with
  `current_vel` still at 3.000, and disabled motion leaves INPOS as it was
  (control.c:1463-1477). A teleop jog leaves INPOS true (control.c:1398-1461),
  so there only `current_vel` tells.
- `task_mode`, `interp_state`, `motion_mode`: `manual` / `auto` / `mdi`,
  `idle` / `reading` / `paused` / `waiting`, `free` / `coord` / `teleop`.
- `program_paused`: `stat.paused` or the interpreter PAUSED - a pause sets both
  (emctaskmain.cc:2312-2319), single-stepping pauses motion only.
- `mdi_running`: MDI mode with the interpreter not idle, paused included.
- `program_line`: `stat.motion_line`; 0 before any program line, and an MDI
  move carries none (seen in `sim/`).
- `g92_active`: a G92 component above `G92_ACTIVE_EPS` = 1e-9 in magnitude.
- `AxisPosition.commanded`: `stat.position`. In `sim/` it led
  `actual_position` by 0.009 mm through a 3 mm/s teleop jog (the cubic
  interpolator's delay) and met it at the target; in a joint jog the two were
  equal.

`MOVING_VEL_EPS` and `G92_ACTIVE_EPS` are chosen, not measured. The view model
carries the fields raw as `motion` (`viewmodel.motion_status`), so `GET
/api/state` shows them; no screen renders them yet.

One known flaw: `map_linuxcnc_state` numbers joints by their position in the
joint list, not by their joint number (see Known gaps); `hard_limits` shares it.

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
| home | MANUAL, teleop off, `home(-1)` - or `home(j)` for one axis (plan WP07, M) - no wait; with a joint homed, nothing sent until the operator confirms | :2693-2728, :1954-1957 |
| program load | MANUAL (ON not required), `task_plan_synch`, wait, `program_open(path)` with the absolute path `programs.resolve` built from the server's own list (plan WP08, H), wait | open_file_guts, :1177, :1206-1208 |
| program start | AUTO, stop check, `auto(AUTO_RUN, 0)`, poll (no wait: it would only run out while the program runs) - refused while anything runs or is paused (plan WP09, H) | task_run, :2345-2358 |
| pause | `auto(AUTO_PAUSE)` from AUTO (READING/WAITING) or MDI, no mode switch, no stop check, bounded wait, read back | task_pause, :2368-2372; task_pauseresume, :2399-2409 |
| resume | `auto(AUTO_RESUME)` with something paused in AUTO or MDI (the mode kept), stop check, bounded wait, read back | task_resume, :2390-2397 |
| step | AUTO only with nothing running, stop check, `auto(AUTO_STEP)`, bounded wait, read back | task_step, :2361-2366 |
| stop (DURDUR) | own command channel, `abort()`, no wait, no poll, without the session lock; then, under it, `abort()` again, bounded wait, read back | task_stop, :2411-2417 |
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
something had been (DURDUR's `stop_followup` always sends `abort()` first). The outage
and the recovery are each logged once. `software_estop`, DURDUR's first
abort (`stop_motion`) and the jog step never poll, so they are not refused: the
jog step only sets the UI's value, and the estop and the abort are sent anyway -
the estop is the one estop this rig has. A send to a task that
no longer answers can spin in emcSendCommand for up to 5 s holding the GIL
(emcmodule.cc:209, :233-250); that is why the feed override, a send too, is
guarded like the rest since 2026-10-21. Not measured.

### What is mocked and what is real

| Real (traced to a repo file) | Mocked (`MockAdapter`, the server default) |
|---|---|
| Board identity, IO count, clock — from `board-dumps/readhmid-*` | Axis positions (start at 0, a jog moves them at once) |
| SCALE, velocity, accel, step timing, HOME_SEQUENCE, HOME_IGNORE_LIMITS, SERVO_PERIOD — read from `test-rig.ini` at runtime | Link health counters (0 / `iyi` / `yok`), shown like readings |
| Input map, polarity, Z asymmetry — `docs/DURUM.md:69-80` | Alarm log seed entries (`hm2_eth: 7i92 bulundu…`, `4 stepgen aktif, 3 eksen jog ediyor` — counted from the INI's `num_stepgens` and axes), stamped with the current time |
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
GET  /healthz       {"ok": true, "client_age_s": seconds since a browser last asked (null: never)}
```

- **One lock, two exceptions.** `Session` runs every adapter call under one lock,
  one at a time — except `software_estop` and DURDUR's first abort
  (`stop_motion`), which never queue (see A and J). DURDUR's second half
  (`stop_followup`) runs under the lock, right after the action that held it. A
  motion action (`app.MOTION_ACTIONS`) - or a «Yazılım sıfırla» - still waiting
  for the lock when DURDUR or the estop arrives is dropped
  (`app.DROPPED_AFTER_A_STOP`). `Session.view` takes the lock too, so the page
  that shows an estop waits until the action in progress returns.
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

### Axis numbers invented (up to 10× off)

| Prototype | Reality | Source |
|---|---|---|
| X/Y 80.000 steps/mm | **100.085 / 98.592** — measured 2026-11-12; the GRBL values the INI first held, 833.300 / 823.500, were ~8.3× too high | test-rig.ini `[JOINT_0/1]SCALE` |
| Z 400.000 steps/mm | **122.274** — measured; GRBL's 1250.000 was ~10.2× too high | test-rig.ini `[JOINT_2]SCALE` |
| 40 / 40 / 8 mm/s | **10 / 10 / 5 mm/s** (2026-11-12; the first runs used 3 mm/s) | test-rig.ini `MAX_VELOCITY` |
| 500 / 500 / 300 mm/s² | **50 mm/s²** all axes (2026-11-12; was 10) | test-rig.ini `MAX_ACCELERATION` |

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
| PREEMPT_RT **arm64** | **`6.12.95+deb13-rt-amd64`** — a PC on a live USB (a laptop in 2026-08, an MSI desktop since 2026-11-12), not a Pi | DURUM.md:22 |
| max jitter **38 µs** | **`latency-test` never run** → renders `—`. (The servo thread's `tmax` was read off the machine on 2026-11-12 — about 0.52 ms in a GRUB failsafe boot, 3.4 ms earlier in a normal multi-core boot — but the UI does not read it.) | DURUM.md |
| latency 38 µs in the header | removed; header shows link health instead | — |

A test greps every screen for `\d+\s*µs` and fails on any match, so a fabricated
latency figure cannot creep back in.

### Travel limits

| Prototype | Reality |
|---|---|
| X 0–300 mm, Y 0–180 mm, Z −45–0 mm | **measured 2026-11-12:** soft limits X 3–399, Y 3–387, Z −96.5–−3 mm. Until then **not measured** → rendered `ölçülmedi` |

Until then the INI held 0–150 / −50–0, because LinuxCNC needs values, but they sat
under `# ASSUMPTION:` comments. `iniload.py` reads that marker and marks the
values provisional; the UI then refuses to display them. A test asserts that
provisional numbers appear on **no** screen.

Once travel is measured and the marker words (`ASSUMPTION:`,
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
| 5 program files, 4 812 lines, 18 dk | invented; since plan WP08 the list is the INI's `PROGRAM_PREFIX` directory as it is on disk (`nc_files/faz8-kare.ngc` on the rig), with sizes and dates read from the files and the loaded file's line count; no run time is shown |
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

**The software estop: on every screen, on the press, its own channel, outside
the lock.** On the test rig the GUI button is the only estop
(`configs/test-rig/test-rig.hal`, section "ESTOP CHAIN - GUI ONLY"). Since plan
WP03 it is the big red «ACİL STOP» of the stop rail, on every screen and fired
on pointerdown or the Pause key (J below); the Kontrol screen's estop card keeps
the two state rows and a small «Yazılım sıfırla» chip, and its note no longer
says the estop cuts drive power where no hardware estop input exists: `Bu
ekrandaki ACİL STOP yalnız yazılımdır: hareketi durdurur, motor gücünü KESMEZ.
Gerçek durdurma: güç anahtarı.` (`viewmodel.ESTOP_NOTE_SOFTWARE_ONLY`; a profile
with the `estop_input` capability keeps the old hardware text). It must never
queue behind another action: `Session.act`
calls `software_estop` without taking the session lock.
`LinuxCncAdapter.software_estop` sends `state(STATE_ESTOP)` on a dedicated
`linuxcnc.command()` channel, with no wait and no poll — so it still goes out
when LinuxCNC's status cannot be read — and touches nothing but that channel,
the message band (its message is an ERROR, so it is also the error box, K),
the alarm log and the stop count that keeps an action in progress from
sending motion after it (J). `MockAdapter` runs every method under its
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
takes the lock. All of this holds for DURDUR's first abort too (J).

**What the rig shows after a software estop: ESTOP_RESET, not ESTOP.** ESTOP
clears iocontrol's `user-enable-out` (ioControl.cc:1086), but the rig's chain
does not run through it: `user-request-enable → estop-latch.0.reset`,
`estop-latch.0.ok-out → emc-enable-in`, the hostmot2 watchdog → `fault-in`
(test-rig.hal, ESTOP section). `emc-enable-in` stays true, so LinuxCNC only
disables motion (determineState, emctask.cc:419-430). Both states refuse motion
and both render as disabled. The fake models the latch (estop_latch.comp) with
the rig's wiring as its default.

**The reset** (`LinuxCncAdapter.software_estop_reset`):

- a board link fault first (`io_error` or `has_bit` set; 2026-10-21): if the
  machine is still ON, ESTOP is sent (normally the snapshot did that the
  second the fault appeared, `_stop_on_link_fault`); `io_error` is cleared
  with halcmd, then up to `LINK_SETTLE_S` (0.5 s) for what the driver's next
  reads bring - `has_bit`, the bite the board recorded, cleared through its
  signal, or `io_error` again, which means the cable is still out
  (`LINK_NOT_RESTORED`, nothing else sent); every joint is unhomed; then the
  reset below, and `LINK_RESTORED` on success;
- already ON, no link fault: nothing is sent (`Makine zaten açık.`);
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
- a limit switch pressed, overridden or not (plan WP05, L): no `state(ON)` -
  motion would enable for one servo cycle and stop again - but `X sol uç
  switch'i basılı — LinuxCNC bir eksen limitteyken makineyi açmaz. «Limitten
  kurtar»a basın.`, after the link path and the ESTOP_RESET;
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

**On the real machine** (`LinuxCncAdapter.home_all`, and since plan WP07
`home_joint` for one axis, M): refused first of all while
a limit switch reads pressed (`… — önce «Limitten kurtar»: referans alma switch
üstünden başlayamaz.`, plan WP05, L), then unless ON, while a
program or MDI line runs or is paused, while any joint is still homing
(`Referans alma sürüyor — durdurmak için «Durdur».`) —
a home-all during homing clears joint 0's homed flag and does not restart the
sequence (homing.c:553-560), so the machine would end with joint 0 unhomed —
and while the machine moves (`motionrules.home_refusal`, M). With a joint
homed already the adapter sends nothing and returns the question the
confirmation band asks (M). Otherwise it switches to MANUAL, turns teleop off — motion homes only
in free mode, "must be in joint mode to home" (command.c:1374-1378), and after a
first homing MANUAL means teleop, so leaving teleop first is what makes
re-homing work — and sends `home(-1)`. Homing is not waited for: task reports
DONE only when the whole homing move is over, and a wait would hold the GIL for
its full timeout. Progress shows in `stat.joint[n]["homing"]` / `["homed"]` on
the next snapshots. `MockAdapter` homes instantly, under the same rules.
The homing card's buttons are off, with the reason as text, whenever the
adapters would refuse (M).

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
| Kayıp paket | `hm2_7i92.0.packet-error-total` |
| Kart bağlantısı (iyi / KOPTU) | `hm2_7i92.0.watchdog.has_bit` |
| İletişim hatası (yok / VAR) | `hm2_7i92.0.io_error` |
| Servo döngü | — (`[EMCMOT]SERVO_PERIOD` from the INI) |

Unreadable pins render `—` in the warning colour, not `0` — "we could not read
it" is not the same as "it is zero". **Any unreadable counter makes the link not
healthy** (`LinkHealth.healthy`): the header's link readout shows `—` in WARN and its state badge is at least WARN, and `Okunamayan gösterge:
… — bağlantı sağlığı bilinmiyor.` appears on the Makine card and in the gate
warnings. Counted packet loss alone also makes it unhealthy. A link with no
readable pin at all, or no `hal` module, is not connected (`Karta bağlantı
yok.`): that blocks program start in both adapters (`MachineState.start_blocks`)
and disables the jog and MDI controls (`MachineState.motion_enabled`, which
`can_jog` includes; both say `Kart bağlantısı okunamıyor — …`). The jog/MDI block
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

Seen since the P0 cleanup (were Known gaps): a value written as a unary
function with no bracket around the function (`XSIN[30]` is an X word,
`GFUP[27.5]` a G code that cannot be known), M98 subprogram calls and an M code
held in a parameter or written as an expression (`M#1`, `M[98]`), M6 when `[EMCIO]TOOL_CHANGE_AT_G30`,
`TOOL_CHANGE_POSITION` or `TOOL_CHANGE_QUILL_UP` makes a tool change move, and
the codes `[RS274NGC]REMAP` names (`mdi.MdiContext`, read once from the INI by
both adapters). And no MDI line at all goes while a joint homes
(`MDI_WHILE_HOMING`): the switch to MDI mode aborts (emctask.cc:286-289).

`LinuxCncAdapter` then switches to MDI mode (bounded, checked) and sends the
line. A line longer than the 254-character NML field is a refusal, not an
exception. A line sent while an earlier MDI command still runs is not refused:
LinuxCNC queues MDI lines.

**The history follows the line's result** (plan WP04; until then a line was
green `Gönderildi.` the moment it was sent, though motion refuses a target past
the soft limits only when task hands it the move, command.c:1002-1003). Each
entry has a status (`MdiEntry.status`), coloured in the history: `refused`
(red; the UI sent nothing), `sent` (yellow, `Gönderildi, sonuç bekleniyor.`),
`rejected` (red) and `done` (green, `Tamamlandı.`). `LinuxCncAdapter` follows
the newest line sent (`_note_mdi_result`, at every error drain: each snapshot,
before the next MDI send, and inside an action that drains):

- a new LinuxCNC error (not a follow-up, K) while task is in MDI mode, or
  within `MDI_RESULT_WINDOW_S` (2 s) of the send: `rejected`, with the error's
  Turkish text as the note - `Z hedefi yumuşak limit dışında (izinli −96.5…−3
  mm, makine) — reddedildi`;
- in MDI mode with the interpreter busy: still running, nothing yet;
- otherwise the line is over: `done`, unless DURDUR or the software estop came
  after its send, or the machine is no longer ON - then `rejected` with `Durdur
  basıldı — satır yarıda kalmış olabilir.` (`Acil stop basıldı — …`, `Makine
  kapandı — …`): it may not have completed;
- a `done` line still turns `rejected` if a new error comes within the 2 s
  with task still in MDI mode (task skips forwarding motion's error in a cycle
  whose status read fails, taskintf.cc:2007-2009).

Errors already queued when the next line is sent are drained before it goes
out, so they cannot be taken for its errors; likewise an action that leaves
MDI mode (a jog, Home All, a program) first settles the unresolved line from
that poll and the errors queued then (`_ensure_mode`), so its own errors are
not taken for the line's (review of WP04: a jog refused right after `G0 X10`
had run marked the line `rejected` with the jog's reason). LinuxCNC names no line for MDI
moves (they all carry line 0), so a line still unresolved when another is sent
keeps `sent` with `Gönderildi; sonucu izlenmedi (ardından yeni satır
gönderildi).` `MockAdapter` runs nothing: a line it accepts is `done` at once
(`Kabul edildi.`).

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
  setting — chips 0.01 / 0.1 / 1 / 10 / 50 mm (`motionrules.JOG_STEPS_MM`), 1 mm
  by default on both adapters. `set_jog_step` accepts only a positive finite
  number on both adapters: motion takes the increment as a magnitude and the
  direction from the velocity's sign (command.c:873-877), so a negative step
  would reverse the jog.
- **Velocity:** `[DISPLAY]DEFAULT_LINEAR_VELOCITY`, falling back to
  `[TRAJ]DEFAULT_LINEAR_VELOCITY` — axis.py's lookup (:3400-3403); 3.0 mm/s on
  the rig (`configs/test-rig/test-rig.ini`, raised on 2026-11-12). If neither is
  readable the jog is refused, never run at an invented speed.

**One rule decides each click before anything is sent (plan WP06).**
`motionrules.jog_refusal(state, profile, windows, axis, direction, step)` returns
a `JogDecision(refusal, step_mm, note)`. Both adapters send by it and the jog
card's buttons follow it, as `mdi_refusal` is shared for MDI. In order:

1. **Whether jogging is possible at all** — `MachineState.jog_block`, which also
   drives `MachineState.can_jog`:
   - status unknown (`Makine durumu bilinmiyor — jog yok.`), or the machine
     off (`Makine devre dışı — jog yok.`);
   - **a joint homing**: `Referans alma sürüyor — jog yok. Durdurmak için
     «Durdur».` Motion refuses any jog then ("Can't jog any joint while
     homing.", command.c:853-857) and sets the joint's error flag. Task reads
     that as motion's error (taskintf.cc:974-979, :2069-2086) and aborts
     (emctaskmain.cc:3473-3499), which cuts the homing short. In `sim/` it
     stopped mid-search, or Y went on and stopped on its switch. The UI now
     sends nothing, not even a mode change, and the homing ends normally
     (`sim/e2e/test_jog_rules.py` B);
   - a program or MDI line paused (`Program duraklatılmışken jog yok.`) or
     running (`Program çalışırken jog yok.`). A `program_paused` or
     `mdi_running` that was not read refuses too (`Program durumu okunamadı —
     jog yok.`).
2. **The machine moving**: `Hareket sürüyor — bitmesini bekleyin ya da
   «Durdur».` An increment adds to the planner's target, not to where the axis
   stands (command.c:873-877 free, axis.c:283-287 teleop), so a second click
   would extend the first. Refused, not queued: a fast run of 1 mm clicks costs
   about 0.45 s per click (plan design (b)). A `moving` that was not read
   refuses (`Hareket durumu okunamadı — jog yok.`).
3. **The click itself** (`motionrules.jog_plan`):
   - the step must be a positive finite number, and the axis one the machine
     has;
   - never onto a pressed switch (plan WP05, L), in any mode: `X sağ uç basılı
     — yalnız X−.` This is the same `toward_pressed_switch` the recovery uses;
   - **unhomed: the step is capped.** At most `UNHOMED_MAX_STEP_MM` = 10 mm
     (`X referanssız — jog adımı en çok 10 mm (seçili 50 mm).`). Toward an end
     the profile lists in `missing_switches` (the rig's Z bottom) the cap is
     `UNHOMED_NO_SWITCH_MAX_MM` = 1 mm (`Z referanssız ve Z alt switch'i yok —
     Z− adımı en çok 1 mm (seçili 10 mm).`). Unhomed, motion's only bound is
     the joint's position plus or minus its whole travel, re-centred at every
     jog (command.c:161-175), so repeated Z− clicks would go down without
     limit. The joint jog's target is also checked against that window; with
     these steps it never binds on the rig;
   - **homed, or any teleop jog: the target stays inside the soft limits.**
     Motion checks a teleop jog against the axis's limits whether the joint is
     homed or not (axis.c:289-290), and a homed joint jog against the joint's
     (command.c:879-885). The target counts from the commanded position
     (`stat.position`, where motion's planner stands). With less than
     `AT_SOFT_LIMIT_MM` = 0.001 mm of room: `Z yumuşak limitte (−3.000 mm) — Z+
     yönünde jog yok.` A longer step is **cut** to the room, ending
     `JOG_LIMIT_MARGIN_MM` = 0.0001 mm inside, and said so: `Z+ en çok 2.000 mm
     gidebilir (yumuşak limit −3.000 mm) — adım 2.000 mm'ye kısaltıldı.` (WARN).
     The margin is there because motion adds the step to its planner's target,
     which the read commanded position can trail by the planner's tiny_dp
     (simple_tp.c:33-45; 5e-8 mm on the rig) plus float rounding. A jog cut to
     exactly the limit could land a hair past it and be dropped. A cut below
     the DRO's last digit is not mentioned. An axis outside its limits (a homed
     recovery jog cut short can leave one there) gets no jog further out; inward, a step
     that would not reach back inside is refused with the least that would
     (`… X− adımı en az 3.721 mm olmalı.`).
   - The windows are `motionrules.axis_windows(config)`: the INI's
     `[JOINT_n]` MIN_LIMIT / MAX_LIMIT by axis letter. With trivkins the rig's
     `[AXIS_<L>]` limits are the same numbers (test_config_traceability ties
     them). An end the INI does not give is LinuxCNC's ±1e99 (inijoint.cc:
     131-140): no cut.

`LinuxCncAdapter.jog` applies the rule to its poll first, so a refusal sends
nothing. A poll that may be Home All between two joints of its sequence -
machine ON in free mode, some joints homed, the others not, none homing - is
looked at once more, `HOMING_GAP_RECHECK_S` = 0.03 s later, before the rule
decides (review of WP06, `_homing_sequence_gap_possible`). There no joint's
flag shows the homing for two servo cycles (the joint that ends clears its
flag, homing.c:1343-1352; the sequence starts the next one two cycles later,
:458-477, :435-456, :754-772), and task publishes motion's status once per
`[TASK]CYCLE_TIME` (emctaskmain.cc:3411, :3571), so a poll can show it for a
whole task cycle (`sim/e2e/harness.py` wait_homing_over). A jog sent then
would reach motion while it homes the next joint: refused, and task's abort
cancels that joint (command.c:510-519). A homing left partial costs only the
second look. After MANUAL and the jog mode it applies the rule again, on the
status after them and with the mode the jog actually goes in (before the
switch the mode is predicted: teleop when every joint is homed, since task's
MANUAL then puts motion in teleop, emctask.cc:276-280, or when motion is
already in teleop). Then comes the stop check (`_stopped_since`), then the
send. The line says `Jog X+1 mm.` only once the jog has gone out: no
"gönderildi", no "eklem"/"eksen". A cut jog says its note instead.
`MockAdapter.jog` applies the same rule and moves at once. Its `home_all` leaves
each axis at its `[JOINT_n]HOME` (5 / 5 / −5 on the rig, where LinuxCNC's
homing ends, homing.c:1250-1287), inside the soft limits. It reports what its
model defines (nothing moving, paused or running an MDI line, the modes), so
the rule reads it as it reads the real adapter. An unknown axis is a
`KeyError` and a bad direction a `ValueError` on both adapters, and
`app.Session` drops both.

**The jog card** (`viewmodel.control_screen`, `render.render_jog`):

- the whole card is off (`can_jog` False) with a banner while
  `MachineState.jog_block` holds: `Referans alma sürüyor — jog kapalı. Durdurmak
  için «Durdur».`, `Program çalışıyor — jog kapalı.`, `Program duraklatıldı —
  jog kapalı.`, `Makine devre dışı — jog kapalı.`, `Makine durumu bilinmiyor —
  jog kapalı.`. With the board link unread it says `Kart bağlantısı okunamıyor
  — jog kapalı.` (the MDI panel likewise). The banner never states a machine
  state that was not read;
- otherwise each pad button asks `jog_plan` for the jog step as it stands
  (`viewmodel.jog_pad`). With a 10 mm step while Z is unhomed only Z− is off.
  On a soft limit only that way is off. Each reason is written once as **text
  under the pad**, not only as a title: the kiosk removes title tooltips
  (`app.js` noTooltips). A cut step leaves its button on; the note comes with
  the click. Moving does not switch the pad off: a pad that changed with every
  jog would move under the finger;
- the 50 mm step chip is off while any joint is unhomed (`Referans yok — 50 mm
  adım kapalı; referanssız en çok 10 mm.`), and while the status is unknown.
  Its reason is written out under the chips;
- a line under the pad says whether the soft limits apply to a jog yet:
  `Yumuşak limitler: etkin.`, `Yumuşak limitler: etkin değil (referans yok) —
  adım en çok 10 mm, Z− en çok 1 mm (Z alt switch'i yok).`, or which axes when
  only some are homed;
- the feed chips stay where they are (plan WP14).

The MDI panel keeps its own rule, `MachineState.motion_enabled` (status known,
machine ON, link read). LinuxCNC queues an MDI line behind a running one
(emctaskmain.cc:1422-1439), so the panel stays live while a line runs even
though the jog card is off. Since the P0 cleanup it is off while a program or
an MDI line is paused, with `MDI_WHILE_PAUSED` as its reason - the condition
`mdi_refusal` refuses by.

The five numbers above (`UNHOMED_MAX_STEP_MM`, `UNHOMED_NO_SWITCH_MAX_MM`,
`AT_SOFT_LIMIT_MM`, `JOG_LIMIT_MARGIN_MM`, `HOMING_GAP_RECHECK_S`) are chosen,
not measured. They are listed in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B13 and
measured by the PC list in [12-operator-handover.md](12-operator-handover.md)
(steps 12, 14, 16).

### H. Programs, feed override and coordinate systems

**Program start** (both adapters): refused with the first of
`MachineState.start_blocks` — hardware cut, status unknown or software estop,
unhomed (or no `[JOINT_n]` at all), no link — then without a loaded program, and
on `LinuxCncAdapter` while one already runs. It then switches to AUTO (bounded,
checked) and sends `auto(AUTO_RUN, 0)`.

**Program stop:** the Program screen's «Durdur» (`program_stop`) is DURDUR, the
stop rail's own action (J): an `abort()` at once, without the lock, then a
second one under it, a bounded wait and a check that the interpreter is idle
and nothing moves (`Durdurma gönderildi — LinuxCNC henüz boşta değil.`
otherwise).

**Program list** (plan WP08, `programs.py`). The program directory is
`[DISPLAY]PROGRAM_PREFIX` joined to the directory of the UI's INI, `~`
expanded and normalised (`configmodel.program_dir` → `MachineConfig.program_dir`;
none without the key or an INI, with `program_dir_reason`). That is where
LinuxCNC looks: the linuxcnc script changes to the INI's directory first
(scripts/linuxcnc.in:777), AXIS opens its dialog there (axis.py:3347) and the
interpreter takes `realpath()` of it (rs274ngc_pre.cc:946-957). The kiosk passes
LinuxCNC's own INI and writes its kiosk INI next to the source, so a relative
prefix names the same directory there; on the rig `../../nc_files/` →
`<repo>/nc_files`, in `sim/` the same `/repo/nc_files` from both INIs.
`programs.list_programs` lists the regular files in it - a symlink only if it
leads inside, nothing from a subdirectory - named `*.ngc`, `*.nc` or `*.tap` in
any case, not hidden, with UTF-8-encodable, printable names; size and mtime;
newest first (then by name); at most `MAX_FILES` = 5000, the scan's bound (the
note says how many there are beyond it), shown `PAGE_SIZE` = 200 a page with
«Önceki» / «Sonraki» and `toplam N dosya` (P0 cleanup; the page is the
browser's view state, like the screen). The interpreter opens any name (Interp::open is a plain `fopen`,
rs274ngc_pre.cc:1387-1404); the extension list is the plan's. The listing is
cached by the directory's (device, inode, mtime): a render - the 1 Hz poll builds
every screen - costs one `os.stat` of the directory and no scan. A scan made
within `RACY_S` = 2 s of the directory's mtime is not trusted (a change in the
same mtime tick would not move it) and the next call scans again. A file edited
in place does not change the directory's mtime, so its size and date can stay
stale until «Listeyi yenile» (`refresh_programs`, `programs.forget`). An empty
list says why, each in its own words: `PROGRAM_PREFIX dizini yok: …`, `Program
dizini okunamadı: … (izin yok)`, `PROGRAM_PREFIX bir dizin değil: …`, `Dizin boş
— .ngc, .nc ya da .tap dosyası yok: …`, or no `PROGRAM_PREFIX` in the INI.

**Program load.** The browser sends only a file's name («Yükle»,
`load_program`, the name as the value). `Session._load` looks it up with
`programs.resolve(config.program_dir, name)`: a bare name - no `/` or `\`, not
starting with `.` (so neither `.` nor `..`), no NUL - present in the listing
exactly, whose `realpath` lies inside the directory's `realpath` (a symlink
swapped to point outside after the scan is refused) and is a regular file. Only
then does the adapter get a path, the absolute one built from the directory:
milltask's working directory is the INI's, so a relative path would open
against it. Anything else reaches no command: `adapter.refuse` (a new abstract
`MachineAdapter` method that only writes the message line, WARN) says `Program
listede yok: <ad> — yalnız program dizinindeki dosyalar yüklenir.`, the name
made safe for the line first (`programs.shown_name`: valid UTF-8, printable, at
most 80 characters - a lone surrogate from a hand-made request would otherwise
break every later JSON answer). «Yeniden yükle» (`reload_program`) takes the
loaded file from a fresh snapshot (`stat.file`) and its name in the directory
(`programs.listed_name`), then the same way; nothing loaded (`Yüklü program yok.`)
or a file loaded from elsewhere (`Yüklü dosya program dizininde değil: …`) is
refused. A load starts no motion, so it is not in `MOTION_ACTIONS` and checks no
stop sequence; observer mode drops all three actions.

Both adapters refuse by one rule, `programs.load_block`, which the screen's
buttons follow too: the status unknown, or whether a program or MDI line runs
not read (`Program durumu okunamadı — program yüklenemez.`); a program paused
(`Program duraklatılmış — yeni program için önce «Durdur».`); a program or MDI
line running (`Program çalışırken yeni program yüklenemez.`: leaving MDI aborts
the line, and task refuses to leave AUTO with the interpreter busy,
emctaskmain.cc:2125-2171); a joint homing (`Referans alma sürüyor — program
yüklenemez.`); the machine moving, `MachineState.moving` (`Hareket sürüyor —
bitmesini bekleyin; program yüklenemez.`: in free mode motion clears `stat.inpos`
while a joint's planner runs, control.c:1205-1272; a teleop jog shows only in
`current_vel`), or that not read. The load would not stop motion, but task
answers nothing DONE while motion is busy (emctaskmain.cc:3546-3561), so its
bounded waits could only run out. Before plan WP08 the adapter had its own
checks with one text for homing and a joint jog (`Referans alma ya da jog
sürüyor — program yüklenmedi.`), and missed a moving teleop jog. The machine may
be off: task takes SET_MODE, PLAN_SYNCH and PLAN_OPEN out of ON too
(emctaskmain.cc:807-844), as AXIS does.

`LinuxCncAdapter.load_program` then sends what axis.py's open_file_guts sends,
in MANUAL: MANUAL (bounded, checked; PLAN_SYNCH is not taken in AUTO idle,
emctaskmain.cc: 1013-1080), `task_plan_synch`, wait, `program_open(path)`,
wait. AXIS switches back to the mode it found before its `task_plan_synch`
(axis.py:1176-1177, :1198, :1206-1207), so from AUTO its synch is refused; the
adapter synchs in MANUAL and stays there (`program_start` switches to AUTO
itself; P0 cleanup corrected the docstring that said "as open_file_guts"). No `reset_interpreter` (PLAN_INIT) is sent: AXIS sends none on an
open, and the binding's `program_open` closes the open program first
(emcmodule.cc:1202-1204). After `program_open`, failure is RCS_ERROR or
`stat.file` not naming the file. A wait that timed out while `stat.file` names
the file counts as loaded, because task reports DONE only once motion is quiet;
and `stat.file` alone is not proof, because task never clears it on a failed
open. A path over the NML field - 254 bytes of UTF-8, not characters
(emcmodule.cc:1210-1214; the fake now counts bytes too) - is a refusal, and a
listed file whose path would be too long is shown off with that reason
(`programs.path_problem`). The message line says `<ad> yüklendi.` with the base
name (it said the whole path before plan WP08); `MockAdapter.load_program`
follows the same rule and says the same.

**The Program screen** (`viewmodel.program_screen`, `render.render_program`):
the directory, a row per file (name, size, date, «Yükle»; the loaded one marked
`YÜKLÜ`), the loaded file's base name, its whole path in small type and its
line count (`programs.line_count`, counted as `len(readlines())` counts and
cached by path, mtime and size; taken once at the load, so no poll reads the
file; at most `MAX_COUNT_BYTES` = 32 MiB, and never a FIFO or device:
`stat.file` can name anything AXIS opened), «Yeniden yükle», «Listeyi yenile»,
«Başlat» and «Durdur». Every reason a control is off is visible text, not only
a title (the kiosk removes titles): why loading is off, a file's own reason,
`Yeniden yükle kapalı: …`, and `Başlat kapalı: …` (the gate's first block, or
`Program yüklenmedi.`). The gate card (`gate_panel`, on Kontrol too) gains a
`program` line, `Program yüklenmedi — Program ekranından bir dosya seçip
«Yükle».`, while the status is known and nothing is loaded: with no block it
replaces `Program başlatılabilir.` (banner `PROGRAM YOK`), with blocks it follows
them. `MachineState.start_blocks` is unchanged. `loaded_lines` is the total the
run's progress uses (below).

**Running a program** (plan WP09). The Program screen's start row has one
primary button by what runs (`viewmodel.program_screen`'s `primary`):
«Başlat» (`program_start`) with nothing running, «Duraklat» (`program_pause`)
while a program or an MDI line runs, «Devam» (`program_resume`) while something
is paused - «Duraklat», an M0/M1, another GUI's pause; beside it «Adım»
(`program_step`) and «Durdur» (DURDUR, unchanged). Each one's reason when off is
visible text (`Başlat kapalı: …`, `Duraklat kapalı: …` / `Devam kapalı: …`,
`Adım kapalı: …`). The rules are `programs.start_busy`, `pause_refusal`,
`resume_refusal` and `step_refusal`, shared by both adapters and the buttons:

- «Başlat» is refused while a program runs (`Program çalışıyor.`), an MDI line
  runs, or anything is paused (`Program duraklatıldı — sürdürmek için «Devam»,
  bitirmek için «Durdur».`; before WP09 it said `Program zaten çalışıyor.` and
  the button stayed on). `MachineState.can_start_program` is off then too.
- «Duraklat» goes from AUTO with the interpreter READING or WAITING (axis.py's
  task_pause) and from MDI (its task_pauseresume); task pauses motion where it
  is and sets the interpreter PAUSED (emctaskmain.cc:2312-2320), so the
  position is kept. It starts no motion: no stop check; it runs under the
  session lock like any action - DURDUR stays the lockless stop.
- «Devam» goes only with something paused and the task in AUTO or MDI, the
  mode kept, as axis.py's task_resume (it returns otherwise). A pause with the
  task in MANUAL (task takes PLAN_PAUSE there too, emctaskmain.cc:947) has
  nothing to resume: `LinuxCNC MANUAL kipinde duraklatılmış — sürecek program
  yok; «Durdur» temizler.` (switching to AUTO would abort, emctask.cc:293-296;
  an abort clears motion's pause, command.c:530). The plan's
  `_ensure_mode(AUTO)` before a resume was not followed for that reason.
- «Adım» with nothing running switches to AUTO and sends AUTO_STEP: task runs
  the program and pauses it at once (emctaskmain.cc:1078-1091) - nothing moves
  (seen in `sim/`); with a program paused in AUTO each «Adım» runs one more
  line and pauses again (:1251-1261; EMCMOT_STEP sets motion's paused flag
  again while the step runs, command.c:1200-1212). Refused while a program
  runs unpaused (`Program çalışıyor — tek adım için önce «Duraklat».`) and for
  an MDI line (task takes no PLAN_STEP in MDI). In `sim/` the first «Adım» after
  the start-paused one moved nothing (the interpreter read up to the first
  motion line), and every one after it ran exactly one line.
- «Devam» and «Adım» set a program moving: start_blocks first (ON, homed, the
  link), the stop sequence right before the send, and both are in
  `app.MOTION_ACTIONS`, so a queued one is dropped after a stop.
- No MDI line while anything is paused (final P0 review): in MDI mode task
  takes a new line behind a paused one (emctaskmain.cc:1422-1439), so «Devam»
  would have run it too - motion the resume does not name. `mdi.mdi_refusal`
  (`paused`, both adapters) refuses it with `Duraklatılmış bir program ya da
  MDI satırı var — MDI kabul edilmedi. Önce «Devam» ya da «Durdur».`

`MachineState.program_running` is now AUTO with the interpreter busy and
nothing paused; an MDI line is not a program any more (until WP09 it counted
as running whenever a file was loaded), and a paused program is not running.
The header (`status_label`) reads `DURAKLATILDI` while paused (WARN), `TEK
ADIM` while a single step's motion runs (paused with motion moving and «Adım»
the run command this UI sent last - `MachineState.run_command`, P0 cleanup;
right after «Duraklat» motion's paused flag is set while it still
decelerates, command.c:1168-1175), `PROGRAM SONU · DURDUR` for the pause a
program stepped to its end leaves (`pause_left_at_end`),
`ÇALIŞIYOR · satır N` with motion's line (`stat.motion_line`, the file's line
number, 1-based - seen in `sim/`), `MDI YÜRÜTÜLÜYOR` for an MDI line. The gate
card shows the run (`DURAKLATILDI`, `ÇALIŞIYOR`, `TEK ADIM`, `MDI`) in place of
`Program başlatılabilir.`.

While a program runs or is paused (in AUTO), the screen shows `Satır N / M ·
%P` and that line's text: `MachineState.program_progress` is motion's line over
the line count (`programs.progress`, filled by `LinuxCncAdapter._map` from the
cached count - one `os.stat`, no read), and the text comes from the copy of the
lines `programs.line_count` keeps when it counts the file at load
(`programs.line_text`; files up to `MAX_TEXT_BYTES` = 1 MiB, each line cut to
`LINE_TEXT_MAX` = 120 characters; both chosen, not measured). Motion's line runs
behind the interpreter's, and loops visit lines out of order: an indication,
not time left.

The end. `LinuxCncAdapter` follows a run (`_follow_run`: from «Başlat» or «Adım»
once the read-back shows it running, or from the first snapshot that sees one
started elsewhere) and the snapshot that finds the interpreter idle again says
once: `Program bitti: <ad>` (INFO, also in the alarm log); after DURDUR
`Program durduruldu: <ad>`, only while the line still carries the stop's own
words; after any LinuxCNC error since the run began `Program hatayla durdu: <ad>
— ayrıntı hata kutusunda.` (WARN; the error stays in the box). "Since" counts the
errors the box took (`_errors_taken`): a late report of an error from before the
run (a follow-up) is none. «Başlat» and «Adım» drain the error channel before
they judge an end, as the snapshot does. Nothing when the
machine left ON (the estop or `_note_task_state` said why), and nothing in
observer mode (no error channel there to tell an error from an end). «Başlat»
now says `Program başlatıldı: <ad>` with the base name, or `Başlatma gönderildi
— LinuxCNC programı başlatmadı.` when nothing shows the program ran: since the
P0 cleanup an idle read-back is followed by a bounded wait, and the start is
"not taken" only on RCS_ERROR (PLAN_RUN's one refusal, emctaskmain.cc:
2294-2299) or with the task no longer in AUTO (a PLAN_RUN dropped in MANUAL is
DONE, :995-1000); DONE in AUTO is a program that ended within the send (an
"M2" alone in `sim/`), and its end is said at once.
`MockAdapter` follows the same rules (its program "runs" until DURDUR; «Adım»
keeps it paused).

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
- the motion fields (`moving`, the modes, `g92_active`, the limit flags, …) are
  `None` or empty (`MachineState.as_status_unknown`), and so is every value of
  `GET /api/state`'s `motion` - the view model checks `status_known` itself;
- program start is blocked with `Makine durumu bilinmiyor — LinuxCNC durumu
  okunamıyor.`, and jog and MDI are off with `Makine durumu bilinmiyor — jog
  kapalı.` / `… MDI kabul edilmiyor.`;
- the link card and drive power still come from the HAL pins, read separately,
  and keep their own meaning.

The outage is logged once as an ERROR alarm, and is the error box until
acknowledged (K); the recovery is logged once as INFO.
Actions refuse as described under "How `LinuxCncAdapter` drives LinuxCNC".

### J. The stop rail and DURDUR (plan WP03)

Until 2026-11-13 the estop was a 13 px outlined button on the Kontrol screen
alone, «Durdur» was on the Program screen alone, both fired on a click the 1 Hz
re-render could swallow (headless Chrome: 5 of 5 mouse presses spanning a swap
gave no click), and DURDUR queued behind the session lock.

**The rail** (`render.render_rail`, `viewmodel.stop_rail`). `render_page` puts
an `<aside id="rail">` before `#app`, once: the poll replaces `#app` only, so
the rail is on every screen, cannot be covered or moved by a screen, and a
press on it is never lost to a re-render. Fixed at the right edge, 132 px wide,
`#app` keeps clear of it (`margin-right`); at 1280 × 800 nothing of `#app`
reaches under it (browser check below). A solid red «ACİL STOP» 112 × 112 px
(`software_estop`) and an amber «DURDUR» 112 × 84 px (`stop`); on a screen
narrower than 700 px 96 × 96 and 96 × 72, the plan's floor. Under them the keys
(`Esc = Durdur`, `Pause = Acil stop`) and a band app.js fills on the press. In
observer mode DURDUR is disabled with its reason written under it (`Salt izleme
— Durdur AXIS'ten verilir; buradan yalnız ACİL STOP gider.`), the hint names
Pause only; the estop is never disabled - not in observer mode, not while the
status is unknown. Only the connection overlay (`.lost`) covers the rail, and it
has an estop of its own.

**app.js.** Buttons marked `data-instant` (the rail's two and the overlay's
estop) post on pointerdown, as AXIS's toolbar estop fires on the press; the
click that follows is not sent again, while a click with no pointerdown before
it (`event.detail` 0: Enter or Space) is the keyboard's and is sent, unless a
pointerdown came within `INSTANT_GUARD_MS` (0.5 s). The band says `ACİL STOP
GÖNDERİLDİ — onay bekleniyor` or `DURDUR gönderildi` at once and stays until the
server's answer is on the screen. While a pointer pressed inside `#app` is down,
a re-render waits - only the newest answer kept - until `SWAP_AFTER_UP_MS`
(0.05 s) after the release, never longer than `SWAP_HOLD_MAX_MS` (3 s, =
`LOST_MS`) from the press; a press on the rail holds nothing, so the estop's
answer shows while the operator still presses. Keys, at the document in the
capture phase, so the MDI field cannot keep them: Escape is DURDUR, as in AXIS
(axis.py:3116 binds it to task_stop), Pause is ACİL STOP; one command per press
(no key repeat); a disabled rail button means its key sends nothing too, so in
observer mode Esc sends nothing. The kiosk keymap leaves both keys alone (it
kills the F-keys, AXIS's F1). The three timings are chosen, not measured
(deploy/README.md, "Süreler").

**DURDUR in the server.** `Session.act("stop")` - and `"program_stop"`, the
same path - calls `adapter.stop_motion()` without the session lock, like the
estop, then `adapter.stop_followup()` under it. Observer mode drops both.
`LinuxCncAdapter.stop_motion` sends `abort()` on a third command channel of its
own (each `linuxcnc.command()` is its own NML channel pair, Command_init,
emcmodule.cc:817-838), with no wait and no poll, so it goes out while the status
cannot be read. Task takes EMC_TASK_ABORT in every state and mode
(emctaskmain.cc:832, :944, :1057, :1156, :1234, :1315, :1410); it ends every jog
(teleop: axis_jog_abort_all; free mode: each joint's planner), the coordinated
move of a program or an MDI line (tpAbort) and, in free mode, a homing in
progress, whose joints end unhomed (command.c:493-554, homing.c:1354-1362);
each planner brakes at its acceleration limit. `stop_followup` sends a second
`abort()` on the action channel, waits (bounded), reads back: `Durduruldu.` when
the interpreter is idle and nothing moves, else `Durdurma gönderildi — LinuxCNC
henüz boşta değil.` The stop is logged as an INFO event, `Durdur gönderildi`.

**Nothing that began before a stop moves after it.** `_note_stop` counts every
DURDUR and software estop before its command goes out; each motion action -
`home_all`, `jog`, `program_start`, MDI (`set_wcs` through it), since plan
WP05 `recover_limit` (before its override, its ON and its jog), and since
plan WP07 `home_joint` (M) - takes the count
as it begins and checks it right before its motion command (`_stopped_since`):
changed, it sends nothing and says `Durdur basıldı — komut gönderilmedi.` (or
`Acil stop basıldı — …`). A stop landing between that check and the send is
caught by `stop_followup`'s second abort, which follows as soon as the action
returns. In front of the adapter, `Session.act` drops a motion action
(`app.MOTION_ACTIONS`) still waiting for the lock when a stop or an estop
arrived - it must not run after the stop's second half, nor after an estop and
the reset that followed it (the plan checked inside the adapter only; this is
the queue before it). A setting such as the jog step is not dropped.
`MockAdapter.stop_motion` ends a program and leaves a homing joint unhomed,
under its own lock.

**The estop is not undone by a reset pressed before it** (review of plan WP03).
A «Yazılım sıfırla» still waiting for the lock when the estop or DURDUR arrives
is dropped as well (`app.DROPPED_AFTER_A_STOP`), and
`LinuxCncAdapter.software_estop_reset` checks the stop count right before its
`state(ON)`: an estop that lands while the reset waits for the latch sends no
ON after it. Without that, on the fake wired as the rig, the estop left
LinuxCNC in ESTOP_RESET (the rig's software estop does not open its latch),
the reset's wait ended and its ON switched the machine back on behind the
estop. The machine then stays off, with `Acil stop basıldı — komut
gönderilmedi.` (or the DURDUR text); the operator resets again.

**In the simulator** (`sim/e2e/test_stop.py`, three runs, all green): a stop
cancels Home All while Z searches, leaving joints unhomed, and Home All then
completes; it ends a 50 mm X jog and an MDI `G0 X200` within 0.5 s of the POST
(watched while the request is in flight), the interpreter idle; the page carries
the rail on all six screens; with nothing moving it answers `Durduruldu.` By
hand on 2026-11-13: from the POST to `current_vel` 0 (polled after the answer),
0.15-0.20 s for a 3 mm/s jog and 0.38-0.43 s for a 10 mm/s G0, the answer
itself taking as long; and a process holding three command channels, as the
adapter does, stopped a running jog with an `abort()` on the third alone (the
send took 0.057 s, the machine stood 0.087 s later) - the second abort cannot
mask a dead first one there.

### K. The message band (plan WP04)

Until 2026-11-13 every operator message, LinuxCNC's errors included, went to
one footer line - 12.5 px, grey, raw English, overwritten by the next message;
the drain overwrote the message with each error it read, so of LinuxCNC's
refusal of a target past the soft limits - `Linear move on line 0 would exceed
Z's positive limit`, the same for joint 2, then `invalid params in linear
command` (command.c:216-268, then :1003) - the generic last one won, the text
that confused the operator on the rig.

**The band** (`render.render_msgband`) opens every screen, inside `<main>`, in
one block with the observer banner that stays in view while the screen scrolls
(`.top-stick`, sticky, under the stop rail and the connection overlay; two
sticky blocks would slide over each other). On the left the operator message
(`MachineState.message`) in its severity's colour (`message_severity`: INFO
grey, WARN yellow, ERROR red); on the right, while there is one, the error box
(`MachineState.last_error`): `HATA` and the time to the second, the Turkish
text at 19 px bold red, LinuxCNC's own text small under it (only when the box
text is a translation), and «Tamam». The line is left empty while it says
exactly what the box says. The footer is gone. Headless Chrome 157 at
1280 × 800 (scratch check, 2026-11-13): scrolled 700 px, the block sits at the
top with the observer banner and the band both in view and nothing of the
screen drawn over them; with an error the stuck band is about 130 px tall.

**Severities.** Both adapters speak only through `_say(text, severity)`: a
refusal is WARN, news INFO, and an ERROR - LinuxCNC's errors, the software
estop, a lost board link, a stop this UI did not cause, the status channel
lost, a command whose result the lost status hides - also becomes the error
box. A later INFO or WARN, or a later action, does not clear the box; only
«Tamam» does (`ack_error`). So the software estop stays in the box whatever an
action finishing after it on another thread writes on the line - the Known gap
"the estop message can be overwritten" is closed. The four fields (message,
severity, box, the box's id counter) are written together under `_say_lock`,
held for the assignments alone: software_estop and DURDUR's first abort run on
another thread than the action holding the session lock (J).

**«Tamam»** (`ack_error`, the error's id as its value) is local: the adapter
forgets the box if it is still that error - one that arrived after the screen
was drawn has another id and stays - and sends nothing to LinuxCNC and reads
nothing from it. `Session.act` handles it before observer mode's drop, under
the lock; in observer mode it is the one control besides the estop that stays
enabled (test: in observer mode an acknowledgement sends no NML command and
polls nothing).

**One error per drain takes the box: the most specific.** `_drain_errors` logs
every message with LinuxCNC's own text, and makes the FIRST error kind that is
not a follow-up of the last one the box took the message and the box
(`messages.is_follow_up`): `invalid params in linear command` follows a move's
own report, and a second soft-limit report of the same move (same line)
follows the first - inRange reports every failing axis, then every failing
joint, so on the rig one target past X's window gives both. Task forwards
motion's errors one per loop at most (taskintf.cc:2024-2028), and in `sim/` the
POST's snapshot drained the first report alone and the rest came later - once
even after the next MDI line had been sent (`make sim-e2e`, 2026-11-13: G0
X50 ran but was marked rejected with G0 Z0's text, until this was fixed). So
the box keeps taking follow-ups across drains until LinuxCNC's last word on
that refusal (`messages.ends_report`: the generic linear text) or
`FOLLOW_UP_WINDOW_S` (2 s); after the last word the same report is a new
refusal (MDI moves all carry line 0). A follow-up is never news: no MDI line
takes it as its result. A stop LinuxCNC makes within the window of a LinuxCNC
error in the box (a limit trip's error can be drained a snapshot before the
status shows the machine off) is logged as before but does not cover the
error in the box - unless that error is a refusal (`messages.is_refusal`: a
move, jog, homing, file or mode change LinuxCNC would not take; it stays ON
after one, so a refusal explains no stop), or the board link is lost:
`Kartla bağlantı koptu …` with its action is said whatever LinuxCNC forwarded
around it (hostmot2's own `Watchdog has bit! (set the .has-bit pin to False
to resume)` reaches the error channel through motion, motion.c:207-214, as do
following errors). Both found in the review of WP04: an external stop 1 s
after a soft-limit refusal went to the log alone, the refusal kept in the box
and on the line; and a watchdog bite left hostmot2's English text in the box,
`Kartla bağlantı koptu …` in the log alone.

**Turkish** (`bettercnc.messages`, pure). LinuxCNC 2.9 has no Turkish
catalogue (`src/po` has no `tr.po`) and numbers joints where the operator
thinks in axes. Translated, each pattern tied to its format string at its
cited line when `reference/` is present (`test_messages`):

| LinuxCNC (source) | Box |
|---|---|
| `joint N on limit switch error` (control.c:821) | `X sol uç switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın.` (the switch whose input reads pressed now; without a reading `X limit switch'ine çarptı — makine durdu.`; the one text of a trip since plan WP05, L) |
| `<kind> move on line N would exceed <A>'s / joint N's positive\|negative limit` (command.c:216, :221, :261, :267) | `Satır N: Z hedefi yumuşak limit dışında (izinli −96.5…−3 mm, makine) — reddedildi` (no `Satır` for MDI's line 0; the window from `[AXIS_<L>]` or `[JOINT_<n>]` MIN/MAX_LIMIT, iniaxis.cc:84/95, inijoint.cc:132/140) |
| `invalid params in linear command` (command.c:1003) | `Geçersiz doğrusal hareket (çoğunlukla yumuşak limit dışı hedef) — reddedildi` |
| `Can't jog joint N further past max\|min hard limit.` (command.c:130, :135) | `X switch'e doğru jog yapılamaz — X sağ uç basılı, yalnız X−` |
| `... further past max\|min soft limit.` (command.c:141, :146) | `Z+ yönünde jog sınırına ulaşıldı — yalnız Z−` |
| `Can't jog any joint(s) while homing.` (command.c:786, :854, :933) | `Referans alma sürerken jog yapılamaz` |
| `can't open F` (emctaskmain.cc:2197), `Unable to open file <F>` (rs274ngc_return.hh:152) | `Dosya açılamadı: F` |
| `must be in joint mode to home` (command.c:1376) | `Referans için eklem moduna geçilemedi` |
| `Ignoring task mode change while jogging` (emctask.cc:270) | `Jog sürerken mod değiştirilmedi — jog bitince tekrar deneyin` |

Any other text stays LinuxCNC's own: `LinuxCNC: <text>`. A text LinuxCNC
rewords stops matching and falls back to that - never a wrong translation.

**The MDI result** follows from the same drain (E).

**Constants, chosen, not measured:** `FOLLOW_UP_WINDOW_S` = 2.0 and
`MDI_RESULT_WINDOW_S` = 2.0 (adapter.py).

**In the simulator** (`sim/e2e/test_messages.py`, three runs, all green): an
unhomed X driven onto its left switch shows `X sol uç switch'ine çarptı — X
limit switch'inde, makine durdu` in the box, kept over the next polls while
`Makine kapatıldı — komut bu ekrandan gelmedi` goes to the log only (since
plan WP05 the box reads `X sol uç switch'ine çarptı — makine durdu.
«Limitten kurtar» ile switch'ten çıkın.` and the stop is logged with that
same text, L); MDI `G0
Z0` on a homed machine shows the Z text with `izinli −96.5…−3`, the raw report
in the log and on the page under it, the history line `rejected`, and the box
unchanged for 2 s after the generic refusal arrives; `G0 X50` after it reads
`done`; «Tamam» with another id clears nothing, with the box's id clears it.

### L. Limit recovery (plan WP05)

On the rig every switch is a limit switch, and three are home switches too
(X−, Y−, Z+; home and limit share one input, so HOME_IGNORE_LIMITS = YES). An
axis standing on one keeps LinuxCNC from switching the machine on: motion
reads the switches every servo cycle, enabled or not (control.c:491-501), and
on an enabled machine trips on any flag that is neither overridden nor its
joint's homing - `joint N on limit switch error`, motion disabled, LinuxCNC
in ESTOP_RESET (control.c:807-826, emctask.cc:419-430). Until 2026-11-13
«Yazılım sıfırla» sent ON onto the switch in a loop (`Makine açılamadı`, then
LinuxCNC's English), jog and Home All need ON, and the operator had to go back
to AXIS. An unhomed jog onto a switch - the normal way to find the switches -
or DURDUR while a homing latches on its switch got there.

**One press: «Limitten kurtar».** The way off is LinuxCNC's limit override
(command.c:675-707): it masks the sides pressed at that instant, every
joint's, and clears itself when the next jog that ran under it ends
(control.c:1263-1283 free mode, :1404-1411 and :1452-1456 teleop) or when
motion enters coordinated mode (control.c:990-992) - nothing else clears it,
and the binding cannot withdraw it (it always sends joint 0,
emcmodule.cc:1113-1119). Task takes it out of ON only in MANUAL mode and drops
it without a word in any other (emctaskmain.cc:846-852). So the override, ON
and one jog away belong together: `LinuxCncAdapter.recover_limit` does all
three in one press, under the session lock, each step read back - no window
with the protection off and nothing moving, and no toggle. AXIS's checkbox is
the counter-example: its second click calls ensure_mode(AUTO)
(axis.py:2914-2921), which drops the mask by the side door - the trap the rig
met.

1. Poll; `motionrules.recovery_plan` decides from that status and the link
   pins whether and how far (below). Refused - nothing sent - with no switch
   pressed (`Basılı limit switch yok.`), with the board link lost (the flags
   are stale then, state.HardLimit: `Önce kart bağlantısını «Yazılım sıfırla»
   ile kurun …`), while a program, an MDI line or a homing runs or the machine
   moves, with both switches of one axis pressed (no direction is safe, and
   motion skips its own direction check then, command.c:118-124: `X ekseninde
   iki switch birden basılı — kablo ya da switch arızası; kurtarma
   yapılmadı.`), or when no single jog can bring a homed joint inside its
   soft limits.
2. Out of ESTOP: `state(ESTOP_RESET)` and the reset's latch wait (on the rig
   a trip leaves ESTOP_RESET already).
3. MANUAL (`_ensure_mode`, ON not required).
4. Out of ON: `override_limits()`, a bounded wait,
   `stat.joint[0]["override_limits"]` read back - `Limit aşma kabul edilmedi
   — kurtarma yapılmadı.` if it did not take. On every press, the status
   showing the override on already or not: a recovery jog an estop cut
   leaves motion's `overriding` set (the jog ran under the mask,
   control.c:1263-1270, and the disabled motion runs no end-of-jog check,
   control.c:1463-1477), so the status still says on, but the free planner
   drops the mask at the first servo cycle after enabling
   (control.c:1278-1283) and an ON then trips at once - seen in the
   simulator in the review of WP05, where it cost a press and a second
   `joint 0 on limit switch error`. The command clears `overriding` and
   rebuilds the mask from the switches pressed now (command.c:686-700).
5. Out of ON: `state(ON)` and read back. Enabling keeps the mask (control.c:
   896-924 clears only the joints' error flags).
6. Motion out of free mode (a homed machine is in teleop): teleop off, read
   back. A joint jog needs free mode, where motion itself refuses a jog onto
   the pressed side (joint_jog_ok, command.c:129-138); a teleop jog is not
   checked against the switches at all (axis.c:278-297).
7. The plan again, from the status now (`Switch serbest kaldı …` if the
   switch let go meanwhile).
8. One `JOG_INCREMENT` of that joint away from the switch at the jog speed,
   not waited for: `X switch'ten çıkarılıyor: +3 mm. Limit koruması bu
   hareket bitince kendiliğinden geri gelir.`

A press with the machine already ON and the override on (an earlier press
whose jog never went out) starts at 6. A stop or the software estop from
another thread ends the sequence before the override, the ON and the jog
(`_stopped_since`, the rule of J); the press is in `app.MOTION_ACTIONS`, so
one still queued for the lock when DURDUR or the estop comes is dropped. A
refusal that comes once the override is on says so: the protection stays off
until a jog away ends or MDI mode is entered (the header badge, below). The
recovery never goes through MDI or AUTO: entering coordinated mode would drop
the mask with the axis still on the switch.

**How far.** Unhomed, `RECOVERY_STEP_MM` = 3 mm: motion's window is then the
joint's position plus or minus its whole travel (command.c:161-175), and on
the rig a switch releases within 0.64 mm of where it trips with about 1.3 mm
of over-travel (test-rig.ini). Homed, the joint stands outside its soft limit
when the switch is the real one (3.5-3.7 mm beyond on the rig), and motion
drops a homed jog whose target is outside them without a word (command.c:
879-885, axis.c:289-290): the step is max(3 mm, the way in plus
`RECOVERY_INSIDE_MARGIN_MM` 0.5 mm), rounded up to 0.1 mm so the button says
what is sent, at most `RECOVERY_MAX_STEP_MM` 10 mm, and its target within the
`[JOINT_n]` limits - else refused. X tripped on its left switch at −0.64
(homed) gets 4.2 mm.

**Afterwards** (`_follow_recovery`): the snapshot that finds the override
over says how it went, once. The switch free and the machine ON: `X sol uç
serbest; limit koruması yeniden etkin.` (unhomed: `Referans alın.`). The
switch still pressed and the machine off - the jog was too short, the mask
went with it, motion stopped the machine again: `X hâlâ switch'te —
«Limitten kurtar»a tekrar basın.` (ERROR, in the box). Free but off - an
estop or DURDUR cut the jog - nothing: that stop said its own word. An estop
that cut the jog with the axis still on its switch leaves the override on in
the status (motion's `overriding`, step 4): the following waits, the card
offers the press again, and that press issues the override anew. Two axes
on switches: one override covers both, but the first jog that ends clears it
(sim lens (e)), so the first is left, the message names the other, and the
machine stops again on it for the next press.

**Around it:**

- `software_estop_reset` sends no ON onto a pressed switch (after the link
  path, which keeps its priority, and the ESTOP_RESET): `X sol uç switch'i
  basılı — LinuxCNC bir eksen limitteyken makineyi açmaz. «Limitten
  kurtar»a basın.` The override on in the status does not change that: it
  cannot say whether the mask outlives the enable, and after an estop cut a
  recovery jog it does not (step 4) - the reset's ON tripped at once in the
  simulator (review of WP05; `sim/e2e/test_limits.py` H). «Limitten
  kurtar» issues the override again and switches on itself.
- `home_all` (and since plan WP07 `home_joint`, M) refuses first of all while
  any switch reads pressed, overridden
  or not, machine on or off: `… — önce «Limitten kurtar»: referans alma switch
  üstünden başlayamaz.` Even with the override a Home All fails: the mask
  clears when the first joint's homing move pauses, and the joint on its
  switch trips (sim lens (d): X 1.6 s into Z's homing). Not while the link is
  lost (stale flags; the machine is off anyway).
- `jog` refuses a direction onto a pressed switch in every mode: `X sağ uç
  basılı — yalnız X−.` (the override window and teleop are where it matters).
  Since plan WP06 this is part of the one jog rule (`motionrules.jog_plan`),
  and the jog card turns that direction's button off with the reason as text
  (G).
- **The trip has one text** (`messages.limit_trip_text`): `X sol uç
  switch'ine çarptı — makine durdu. «Limitten kurtar» ile switch'ten çıkın.`,
  whether the drain made it of LinuxCNC's report (the switch whose input reads
  pressed) or `_note_task_state` of the stop motion's flags show (a switch
  pressed and not overridden in that snapshot's status) - a jog from this
  screen or not, never `komut bu ekrandan gelmedi`. The box is not said twice:
  LinuxCNC's report of the trip, drained within `FOLLOW_UP_WINDOW_S` with the
  same text, keeps its box (id and raw line). No switch read pressed: `X limit
  switch'ine çarptı — makine durdu.`; both ends: `… birden basılı — makine
  durdu; kablo ya da switch arızası olabilir.` In observer mode the way off
  named is AXIS's. This reconciles WP04's wording (`… — X limit switch'inde,
  makine durdu`) with WP05's; both e2e modules check the one text.

**The view.** Kontrol shows a limit card - only while a switch reads
pressed, first in the middle column, above the estop card - with each switch
by label and HAL pin (`X sol uç switch'i BASILI`, `hm2_7i92.0.gpio.021.in_not`)
and the button that says what will move, which way and how far: «Limitten
kurtar: X+ yönünde 3 mm» (`viewmodel.limit_panel`, the same
`motionrules.recovery_plan` over the INI's soft limits as the adapter). Off,
its reason is written under it (the plan's refusals, or observer mode): the
kiosk removes title tooltips. With the link lost a note says the switch
state is the last reading. No card while LinuxCNC's status is unknown (no
flag is read) or for `MockAdapter` (it reads no switches; its
`recover_limit` says `Basılı limit switch yok.`). The header shows a red
`LİMİT KORUMASI GEÇİCİ KAPALI` badge on every screen while
`stat.joint[0]["override_limits"]` is on.

**Shared rules** (`ui/bettercnc/motionrules.py`, pure): `limit_trips` (motion's
own flags, `MachineState.hard_limits`, labelled from the profile; a homing
joint's flags are not trips), `unmasked_trips`, `toward_pressed_switch`,
`away_direction`, `soft_windows`, `recovery_step`, `recovery_plan` and the
operator's words. Both adapters and the view model use them.

**The fake** (`fake_linuxcnc.py`) now models what motion does with the
switch flags a test sets: the trip on an enabled machine
(`FakeMachine.trip_on_limits`, at `state(ON)` and at every poll), the
`override_limits()` command (METH_NOARGS, emcmodule.cc:1467) taken out of ON
in MANUAL alone and dropped silently otherwise, refused in AUTO and MDI, the
mask of the sides pressed at that instant, its clearing when a jog run under
it ends (at once: jogs complete at once there) or coordinated mode is
entered, and motion's own refusal of a joint jog onto a pressed switch -
each rule cited and tied to the source by
`test_linuxcnc_adapter.TestOverrideFidelity`.

**Never:** an override that is toggled, removed by a mode change, or sent by
another action; any change to the limits, the HAL nets or the INI.

**Constants, chosen, not measured:** `RECOVERY_STEP_MM` = 3.0,
`RECOVERY_INSIDE_MARGIN_MM` = 0.5, `RECOVERY_MAX_STEP_MM` = 10.0
(motionrules.py). The PC checklist measures the switches' over-travel and
whether 3 mm leaves each switch.

**In the simulator** (`sim/e2e/test_limits.py`): see Tests below.

### M. Homing one axis, the question first, visible gates (plan WP07)

Until plan WP07 the UI had «Tümünü referansla» alone: `home(-1)`, the button
on whenever the UI was not in observer mode, and a homed machine re-homed on
one click - every joint unhomed at once (homing.c:401-410) and driven to its
switch with the limits ignored - where AXIS asks first (axis.py:2693-2701).
deploy/README.md counted the missing single-axis homing (AXIS's Machine ▸
Homing, used in Faz 6.2) among the kiosk's prerequisites.

**One axis alone.** Each homing chip on Kontrol carries its own button, «X
referansla» / «Y referansla» / «Z referansla» (`home_joint`, the axis letter
as the value). `LinuxCncAdapter.home_joint` sends what axis.py's home_joint
and go_home send (:2708-2728, :1954-1957): MANUAL, teleop off, `home(j)` for
the joint carrying the letter, each step read back, not waited for. Task
takes a home only ON in MANUAL (emctaskmain.cc:911) and motion only in free
mode (command.c:1366-1378). With a HOME_SEQUENCE that is not negative - the
rig's are Z 0, Y 1, X 2 - motion homes that joint alone (do_home_one_joint,
homing.c:265-281; only it is in the sequence, :342-353), and it stays in free
mode until every joint is homed (homing.c:1400, control.c:261-263), so the
next jog is a joint jog. An axis the machine lacks is a `KeyError` before
anything is read; a letter on two joints (a gantry) is refused alone, as AXIS
refuses it (axis.py:2715-2717). The line says `Referans alma başlatıldı:
yalnız Z.`

**One rule for every homing** (`motionrules.home_block`, `home_refusal`),
decided on a fresh poll before anything is sent, by both adapters and by the
homing card:

- the status unknown (`Makine durumu bilinmiyor — referans alınamaz.`);
- a switch pressed, a homing joint's own home switch not counted, named
  with the way off: `Y ön uç switch'i basılı — önce «Limitten kurtar»:
  referans alma switch üstünden başlayamaz.` (WP05's text; not while the
  board link is lost, when the flags are stale and the machine is off);
- the machine off (`Makine devre dışı — referans alınamaz.`);
- a program or MDI line paused or running (`Program duraklatılmışken …`,
  `Program çalışırken referans alınamaz.`), or that not read - task refuses
  to leave AUTO with the interpreter busy, and leaving MDI for MANUAL aborts
  the line (emctaskmain.cc:2125-2171);
- a joint homing: `Referans alma sürüyor — durdurmak için «Durdur».` (a
  Home All sent then clears joint 0's homed flag and starts nothing,
  homing.c:553-560; one joint's home would take the sequence over);
- per press only, not on the card: the machine moving (`Hareket sürüyor —
  bitmesini bekleyin ya da «Durdur».`; a homing would start under a jog
  still running), a `moving` not read.

A poll that may be Home All between two joints of its sequence is looked at
once more, `HOMING_GAP_RECHECK_S` later, as `jog` does (G): no joint's flag
shows the homing there for a moment. Then `_stopped_since`: a stop or the
estop since the press began sends no `home(j)`.

**The question first** (`motionrules.home_question`, plan design (g)). The
first homing - nothing homed - asks nothing. Asked, with nothing sent - not
even a mode change:

- Home All with any joint homed (AXIS asks only with all homed): `Yeniden
  referans: Z üst switch'ine (Z+), sonra Y ön uç switch'ine (Y−), sonra X sol
  uç switch'ine (X−) gidecek — yol açık mı?` - the moves in HOME_SEQUENCE
  order, each joint toward the switch its HOME_SEARCH_VEL points to, named
  by the profile's label (`home_route`, `home_move`);
- one axis homed already: `Y yeniden referanslanacak: Y ön uç switch'ine
  (Y−) gidecek — yol açık mı?`;
- X or Y alone while Z (`motionrules.TOOL_AXIS`, which the rig's INI homes
  first so the tool lifts clear) is unhomed: `Z referanssız: takım
  aşağıdaysa X hareketi çarpabilir. X sol uç switch'ine (X−) gidecek — yol
  açık mı?`

A refusal always comes before the question: the operator is never asked
about a homing that would be refused.

**The confirmation band** (`app.Session.request_confirm`,
`viewmodel.confirm_band`, `render.render_confirm`). The adapter returns the
question instead of acting (`home_all(confirmed=False)` /
`home_joint(axis, confirmed=False)` return it; `None` when they acted or
refused). The session keeps it - one at a time, this session's own state,
never the adapter's - with a random token, and every screen shows it under
the message band, in the block that stays in view: the question large,
«Evet» (`data-action="confirm"`, the token as the value) and «Vazgeç»
(`cancel_confirm`), and `15 s içinde «Evet»e basılmazsa ya da arada başka
bir komut verilirse iptal olur.` «Evet» runs the action with
`confirmed=True`, and the adapter checks every rule again on a fresh poll.
It counts only while the question stands: before `CONFIRM_TIMEOUT_S`, with
no stop or estop since it was asked (the question carries the session's
stop count from the press that asked it, so a stop during that press leaves
no question), and with no other action in between - every action under the
session lock ends the question standing, so an answer always belongs to the
question on the screen. Otherwise nothing is sent and, for
`CONFIRM_REFUSED_S`, the band says `Onay geçersiz — süresi doldu ya da arada
başka bir komut geldi; hiçbir şey gönderilmedi. Düğmeye yeniden basın.`
Nor does «Evet» count for the first `CONFIRM_MIN_S` after its question: the
band shows it off with `Önce soruyu okuyun — «Evet» 1 s sonra açılır.`
(`ready` in the band), and an «Evet» that comes anyway sends nothing and
leaves the question standing, deadline unchanged. The band opens at the top
of the block that stays in view, over whatever stood under it: on a narrow
screen (one column, under about 840 px) scrolled down to the homing card,
that is the homing buttons themselves, and a double tap's second press
landed on «Evet» - in the browser at 800x600 a second tap 0.25 s after
«Tümünü referansla» re-homed every joint (review of WP07).
«Evet» is a motion action (`app.MOTION_ACTIONS`, with `home_joint`): queued
behind a busy action when DURDUR or the estop comes, it is dropped. In
observer mode nothing is asked and no band shows. GET `/api/state` carries
the band as `confirm` (`sim/e2e/harness.py` pending_confirm reads its
token).

**The homing card** (`viewmodel.homing_panel`, `render.render_homing`):
«Tümünü referansla» and each axis's button are off whenever `home_block`
holds (or in observer mode), with the reason written once as text under
them - the kiosk removes title tooltips. After DURDUR in a homing's latch
phase the joint is left on its home switch, which on the rig is its limit
switch (X−, Y−, Z+; Known gaps): the buttons are then off saying the switch
must be cleared first with «Limitten kurtar», the limit card (L) offers it,
and no new motion path exists for it. While a joint homes, the card says
what DURDUR does and can leave (`motionrules.HOME_STOP_NOTE`: `«Durdur»
(Esc) referans almayı keser. Eksen switch'in üstündeyken kesilirse orada
kalır ve makine durur — o zaman «Limitten kurtar», sonra yeniden
referans.`). Moving does not switch the card off, as on the jog card.

**MockAdapter** follows the same rules: one axis homes at once at its HOME,
the same questions, and it no longer homes while its program runs (a Known
gap until WP07).

**The fake** models one joint homed alone (`FakeMachine.issue_home`) and that
a homing unhomes its joints as it starts (homing.c:401-410, :771-772), tied
to the source by `test_linuxcnc_adapter.TestHomingFidelity`, with task's
MANUAL-only home, motion's teleop switch only once every joint is homed and
AXIS's questions.

**Constants, chosen, not measured:** `app.CONFIRM_TIMEOUT_S` = 15 s,
`app.CONFIRM_REFUSED_S` = 5 s, `app.CONFIRM_MIN_S` = 1 s (longer than a
double click, short beside reading the question).

**In the simulator** (`sim/e2e/test_homing.py`): see Tests below.

---

## Tests

```bash
make test          # everything incl. scripts/test_grbl2ini.py (1574 tests on 2026-11-14)
make coverage      # 99.6 %
make sim-e2e       # end to end in the simulator (Docker): below
```

`ui/tests/run_all.py` discovers both `ui/tests` and `scripts`, so the
pre-existing `test_grbl2ini.py` runs in the same command. No test there is an
expected failure; the end-to-end modules hold the known gaps of later work as
expected failures (below).

| File | Tests | Covers |
|---|---|---|
| `test_app_cli.py` | 52 | `main()` and `serve()` with a stub server (no socket is bound); `--dump` for every profile × screen, the same body the live server sends; exit 2 and the list of valid screens for an unknown or empty name; `python -m bettercnc.app --dump` in a subprocess; inline JSON cannot close its `<script>`; no file:line citations into production code in the server's files (`app.py`, `render.py` and their two test files); the adapter choice, click routing, supervision and its exit code, client contact, the connection overlay and the kiosk flags |
| `test_deploy.py` | 131 | `deploy/` and the kiosk: INI derivation, the boot screen server, the screen guard over the DevTools pipe (incl. the real pipe), the browser watch, the kiosk keymap through libxkbcommon (in the container), the rules the scripts must hold (SSH key-only, the session's stop path, timings in the README); `sim/`: the sim INI's three changed keys (`PROGRAM_PREFIX` absolute), the switch inputs under the rig's pin names on the rig's signals, and the e2e harness's tables (tied to the profile, both HAL files and the INI), its switch rules, its runner and that it never opens a command or error channel |
| `test_capabilities.py` | 31 | the never-hidden contract, per capability; profile completeness; TODO vs ABSENT |
| `test_config_traceability.py` | 32 | every hardware number vs the INI, read with an independent parser |
| `test_edge_cases.py` | 80 | edited, sparse and half-measured INIs (continuation lines, repeats, section names, empty values, HOME_IGNORE_LIMITS parsing, comment runs); measured travel reaching every screen and the profile; the status-unknown display; a wired estop input; status-label priority; link trouble reaching the gate; the adapter contract; the LinuxCNC citations re-checked against `reference/`; P0 cleanup: joints read by their own numbers with a `[JOINT_n]` missing (pure mapping, the real adapter on the fake, the mock) |
| `test_estop.py` | 27 | hardware/software separation at three levels |
| `test_homing_gate.py` | 32 | the gate, order from config, the drift detector, and the docs read from disk |
| `test_homing_single.py` | 86 | plan WP07: `motionrules.home_block` / `home_refusal` pure, in their order - the status unknown, a pressed switch named with «Limitten kurtar» (not while the link is lost, not a homing joint's own switch), the machine off, a program or MDI line running or paused or not read, a joint homing, then per press the machine moving (the card stays on) and an axis homeable alone (no letter the machine lacks, none on two joints); `home_question` - nothing for the first homing, Home All with any joint homed saying the moves in the INI's order with the profile's switch labels, one axis homed again, X or Y alone while Z is unhomed, both, the route following an edited INI, joints of one step together, no switch named, no search; on the fake, `home_joint`'s exact sequence (from MANUAL, from AUTO, each axis its own joint), a `KeyError` before anything is read, only that joint homed at HOME and motion left in free mode, the homing shown and every press refused while it runs, the question returned with nothing sent and the confirmed homing then sent, a homed joint read unhomed while it homes again, a refusal before the question, each refusal sending nothing, the second look in Home All's gap, a stop or the estop during the mode switch; the mock under the same rules (one axis at HOME, the same questions, refused while its program runs) and one table for both adapters; `Session` - the band's token, «Evet» running it, «Vazgeç», the time limit with a fake clock and the refusal note, a wrong token, any other action, a newer question, a stop or the estop since, a stop during the asking press, the adapter's own rules again on «Evet», an axis the adapter lacks, observer mode, «Evet» queued behind the poll dropped by DURDUR, «Evet» within `CONFIRM_MIN_S` of the question no answer (nothing sent, the question standing, its deadline unchanged; a double tap on the fake sends nothing), a token JSON cannot encode refused, not raised; the homing card's buttons and reasons as visible text (off, unknown, homing, a program, paused, a switch, observer mode, a letter on two joints), the stop note while homing; the band on every screen in the sticky block, «Evet» off for its first second with the reason as text, in the JSON where the e2e harness reads it, clear of the rail; P0 cleanup: the homing gap re-look in the fake's real gap between groups; a negative HOME_SEQUENCE refusing the one-axis button (rule, adapter on the fake, card, mock); no INI - no invented order, search direction or one-axis homing; the band's `ready_in_ms` / `data-ready-in-ms` and app.js's one extra render |
| `test_inputmap_and_link.py` | 50 | verified input map, Z asymmetry, `.in_not`, link health incl. unreadable counters and packet loss alone |
| `test_jog_rules.py` | 68 | plan WP06: `motionrules.jog_refusal` pure, in its order - the status unknown or the machine off, a joint homing, a program or MDI line running or paused, a reading not made, the machine moving (the card stays on), a pressed switch, the unhomed caps (Z− 1 mm, else 10 mm, per joint, re-centred clicks each capped), the homed soft limits (cut to the room with its note, on the limit refused, a step ending on the limit a hair short, counted from the commanded position, every axis and end, teleop checked homed or not, no window no cut), outside the window (no further out, in only by enough), bad steps, axes and directions; on the fake, nothing sent during homing and the homing completing, nothing sent while busy or moving, the caps, the cut taken by motion and the uncut step dropped without a word (the fake's soft limits on), the second look after the mode switch, a stop during it; the mock under the same rule (at HOME after Home All, refused while its program runs); one table, both adapters, the same decisions; the jog card - the banner, each button's reason and the 50 mm chip's as visible text, the soft-limit line, no flicker while moving, observer mode, the MDI panel's own rule, the link named; P0 cleanup: the re-look in the fake's real gap between groups; the MDI panel off while a program, an MDI line or a step left over is paused, `MDI_WHILE_PAUSED` as text |
| `test_limit_recovery.py` | 105 | plan WP05: `motionrules` pure (the pressed switches from motion's flags, a homing joint's own switch not one, the step unhomed and homed - inside the soft limits, outside them, too far, past the far limit - every refusal in order, both ends of an axis, two axes); the one trip text; the fake's model of what motion does with the switches (the trip at ON and while ON, the override in MANUAL alone out of ON and refused in MDI, its mask, its clearing by a jog or coordinated mode, the joint jog onto a switch refused, the teleop jog not checked); on the fake, `recover_limit`'s exact sequence for every switch, from AUTO, from ESTOP, homed in teleop, again with the override on, two axes, and each refusal sending nothing (override not taken, ON not holding, MANUAL or free mode not reached, the link lost before or after the ON, a program running, no jog speed, the switch letting go); the UI's own stop over once it switches on; DURDUR or the estop during it, before the first send included; the reset sending no ON, Home All refused, a jog onto a switch refused in teleop and free mode; a homing joint's switch no trip; the trip said once and never `komut bu ekrandan gelmedi`; the follow-up (free, still on it, the instant before motion trips, an estop, the link); observer mode; DURDUR in a homing's latch phase on X−, Y− and Z+, Y left on its front switch after Home All; the card, its reasons as text, the badge on every screen; the mock; the press dropped from the lock queue by a stop |
| `test_linuxcnc_adapter.py` | 264 | `LinuxCncAdapter` against the fake: the exact command sequence of every action and that LinuxCNC (the fake) refused nothing; joint vs teleop jog, re-homing, the homing refusal, the estop and stop channels, reset deadline and rig latch, program load/start/stop, feed ceiling, WCS, INI values flowing through, bounded waits, the status channel lost and every action under it, the board link lost and recovered as the real driver does it, LinuxCNC half gone or stuck; the fake checked against the binding when `reference/` is present, including the line numbers of the adapter docstring's stat table, what abort does in task and motion, (plan WP05) the limit override and the trip in task and motion, and (plan WP06) what a jog meets in motion - the homing refusal and the abort after it, the increment added to the planner's target, the soft limits and jog limits, where the commanded position stands, homing ending at HOME, and (plan WP07) one joint homed alone, a homing unhoming its joints as it starts, task's MANUAL-only home, motion's teleop switch only once every joint is homed, AXIS's questions before a homing again; P0 cleanup: the fake's Home All group by group with the gap a poll sees, against homing.c (groups by abs(sequence), the end at a missing number, 999 left out, a jog refused in the gap), a negative sequence's group, teleop after a homing that leaves every joint homed (no extra `teleop_enable(1)`), `RIG_HOME_SEQUENCE` tied to the INI |
| `test_mdi_tools_adapter.py` | 96 | MDI entry/history (each line's status); the shared gate (`_needs_homing`, `mdi_refusal`); probe-is-not-a-tool; `map_linuxcnc_state` (24 tests); the mock following the INI; the mock's own lock; P0 cleanup: unary functions, M98, an M code in a parameter, M6 with a tool change that moves, remapped codes, `mdi_context_from_ini` (every REMAP line), no MDI while homing on both adapters (the gap looked at again), the facts against `reference/`; the mock's feed and WCS by the real adapter's rules |
| `test_messages.py` | 86 | plan WP04: `messages.translate` for every pattern and an unknown text, the follow-ups and LinuxCNC's last word, the refusals (which explain no stop), each pattern's format string at its cited line in `reference/`; on the fake, the first error of a drain taking the box, a report split across drains (as in `sim/`), a late follow-up after the next MDI line, the box surviving later messages and cleared only by «Tamam» with its id, the estop kept in the box over an action it overtook, a limit trip naming the pressed switch and its stop logged, not boxed over it, a stop after a refusal and a link loss said over what LinuxCNC forwarded around it; the MDI result - sent, rejected in Turkish, done, cut by DURDUR / the estop / the machine going off, superseded, a late error within the window, an error drained inside another action, the line settled before an action leaves MDI mode; «Tamam» in observer mode sending and polling nothing; the mock; the band on every screen, the box, its raw line, no footer, the sticky block and the history colours |
| `test_observe_and_live_hal.py` | 37 | observer mode (`--observe`): only the software estop leaves, no error channel, every screen says so; the live switch inputs on Alarmlar and the live step timing on Makine, read through `hal.get_value` |
| `test_program_load.py` | 63 | plan WP08: `configmodel.program_dir` (the rig's INI gives `<repo>/nc_files`; relative, absolute, `~`, missing or empty key, no INI, a relative INI path, the kiosk INI beside its source); `programs.list_programs` - newest first then by name, the extensions in any case, hidden files, subdirectories, symlinks leading out, broken or to a directory left out and one leading inside kept, Turkish names kept and unprintable or undecodable ones left out, the 200 cap and its note, each empty reason (missing, not a directory, unreadable, empty, no key) and OS errors as reasons; the cache - a second call and five renders scan nothing, a changed directory, a scan within `RACY_S`, `forget`; `programs.resolve` - `../x.ngc`, `/etc/passwd`, `a/b.ngc`, `a\b.ngc`, `.gizli.ngc`, `.`, `..`, NUL, another case, an unlisted name, the whole path, a symlink swapped to outside after the scan, a file gone since; `listed_name`, the 254-byte path limit, `shown_name`; `line_count` as `readlines()` counts, cached by version, never a FIFO, not past its size cap; `load_block` in its order (the machine off does not block); on the fake through `Session` - the exact sequence from AUTO (MANUAL, `task_plan_synch`, `program_open` with the absolute path), nothing sent for nine bad names (a lone surrogate included, the answer still encodes), reload of the loaded file, reload refused with nothing loaded or a file from elsewhere, each refusal while running, paused, MDI or moving sending nothing with the buttons off and the reason on the card, loading with the machine off, observer mode, the fake's byte limit; the mock under the same rule; `refresh_programs`; a load not in `MOTION_ACTIONS`; the screen - rows, the loaded mark, path and line count, escaping, empty reasons, reasons as visible text (running, unknown, a path too long, reload, «Başlat»), the gate's `PROGRAM YOK` line with start blocks unchanged, observer mode; the LinuxCNC facts it builds on against `reference/` (the binding's close-first and UTF-8 byte limit, open_file_guts' order and task's tables out of ON and in AUTO idle, `PROGRAM_PREFIX` read from the INI's directory); P0 cleanup: pages of `PAGE_SIZE`, the count line, «Önceki» / «Sonraki» (observer mode too), any listed file resolvable, the `MAX_FILES` scan bound; the page as view state (`_valid_page`, `Session.view`, app.js never posting it) |
| `test_program_run.py` | 81 | plan WP09: the fake's PLAN_PAUSE / PLAN_RESUME / PLAN_STEP (pause and resume in AUTO, MANUAL and MDI; step refused in MANUAL and MDI; step from AUTO idle runs the program paused, unhomed refused as a run; step while paused steps motion with motions queued, else the interpreter; a busy interpreter refuses a run; abort and leaving for MANUAL clear the pause; switching to AUTO or MDI aborts) and, against `reference/`, the planner's tables, the issue bodies, the binding and axis.py's gates; `programs`' run rules for every state (idle, running, waiting, paused, MDI, MDI paused, paused in MANUAL, unread, unknown), `progress`, `line_text` (cut, odd bytes, a large file keeps no copy); `status_label` (DURAKLATILDI, TEK ADIM, `ÇALIŞIYOR · satır N`, MDI YÜRÜTÜLÜYOR, off before all), a pause is WARN, `can_start_program` off while anything runs, is paused or unread; the mapping (MDI is not a program, a pause is not running); on the fake: pause only while running, again refused, from MDI, not taken; resume in the mode it found, an M0 pause, from MDI, nothing paused, paused in MANUAL with no mode switch, the machine unable to move, a stop between the checks and the send; step from idle (AUTO then step) and while paused, its gates, a mode switch task ignores, a stop, not taken; start refused while running, paused or MDI, not taken; line and progress; the end - bitti once, a short program, after DURDUR, after an error (box kept), an estop's words kept, a run started elsewhere, observer mode, a pause is no end; `Session`; the mock under the same rules; the screen - the primary button per state, «Adım»'s reason as text, the run line and the gate, unknown and observer mode, «Başlat»'s paused reason; P0 cleanup: START_NOT_TAKEN only on a refusal (RCS_ERROR, dropped in MANUAL), a program that ends within the send says `bitti` / `hatayla durdu`, one task picks up late; TEK ADIM only after «Adım» (`run_command`, observer mode as before, the mock); a pause left at the program's end - label, end message, every run button refused with nothing sent, DURDUR clears it, the screen's reason; a pause before «Devam» after steps, none after a plain pause, none sent after a stop |
| `test_prototype_corrections.py` | 14 | table-driven guard on all 24 corrections |
| `test_server_and_iniload.py` | 57 | HTTP routes, malformed input, body limits, the request timeout, screen validation on POST; `Session` locking (the estop and DURDUR's first abort outside the lock, with a blocked jog; the confirmation band's «Evet» and «Vazgeç» under it); the status-unknown DRO; INI edge cases; P0 cleanup: the estop's answer at once (`"html": null`) while an action holds the lock |
| `test_stop_rail.py` | 51 | plan WP03: the stop rail on every page outside `#app`, its sizes and layout rules (the buttons fit inside it), observer mode (DURDUR off with its reason, the estop never); the estop note; `Session` routing (`stop`, `program_stop`, observer) and dropping a motion action or a reset a stop overtook in the lock queue; a reset sending no ON after an estop that landed while it waited for the latch; on the fake, the first abort on its own channel while `home_all` holds the lock, the second after it, and no `home(-1)` / jog / `auto(RUN)` / MDI after a stop that came during a mode switch; `stop_motion`'s threading contract; abort ending a jog and a homing; the mock; `app.js`'s pointerdown, held re-render, Esc / Pause and band, as text |
| `test_state_plumbing.py` | 40 | plan WP02: the work DRO as AXIS computes it (G92, tool offset, XY rotation; glcanon's formula transcribed as the check); the motion fields - limit flags, override, `moving`, modes, paused, MDI running, program line, commanded position, G92 - pure, on the fake and in `GET /api/state`'s `motion`; their "not read" values while the status is unknown, and what `MockAdapter` reports of them (plan WP06: what its model defines); the LinuxCNC facts behind them re-checked against `reference/` |
| `test_unmeasured.py` | 42 | travel, latency and servo period unmeasured; no fabricated default leaks |
| `scripts/test_grbl2ini.py` | 49 | the pre-existing GRBL translation tooling |

Support files, not tests:

| File | What it is |
|---|---|
| `fake_linuxcnc.py` | fake `linuxcnc` and `hal` modules derived from the LinuxCNC 2.9 source, every rule cited: a task-planner and motion model that refuses what LinuxCNC refuses and reports it on the error channel; `stat` changes only on `poll()`; `wait_complete()` times out while motion moves, and `wait_gate` can hold it open for a threaded test; motion drops a teleop request while not in position; the rig's `estop_latch` (`fault_in`, `user_enable_out_in_chain` = False as `test-rig.hal` wires it, `estop_clear_after_polls`); `channels_valid = False` makes the status and error channels raise; `stat_polls` counts `poll()` calls; abort() ending a jog and, in free mode, cancelling a homing (its joints end unhomed); the hard-limit flags a test sets and what motion does with them (plan WP05: the trip on an enabled machine, `override_limits()` and its mask, the mask clearing at a jog's end or in coordinated mode, a joint jog onto a switch refused); homing ending with each joint at its HOME (`home_pos`, the rig's by default), one joint homed alone by `home(j)` and a homing unhoming its joints as it starts (plan WP07) and, when a test gives `soft_limits`, motion's own jog checks (plan WP06: a teleop jog past the axis's limits dropped, a joint jog against its jog limits); passive status a test sets and `stat` reports, with nothing reacting to it (`current_vel`, the program lines, the G92 / tool / rotation offsets, the active G codes, a commanded position apart from the actual one); `installed()` always restores `sys.modules` |
| `helpers.py` | `view` / `html` / `all_html`; `ini_raw_values`, an INI reader independent of iniload; `rig_profile`, the test-rig profile on a temp copy of `test-rig.ini` with line-by-line edits, inserts into the first `[SECTION]` and a transform, where every edit must hit an existing key; `strip_markers`, the "travel was measured" edit |
| `run_all.py` | discovers `ui/tests` and `scripts` |

The fidelity tests (in `test_linuxcnc_adapter.py` and `test_edge_cases.py`) read
`reference/linuxcnc`, the gitignored 2.9 clone: constants, command method names
and argument formats, stat attributes, joint dict keys, message type numbers, the
`hal` functions, the `estop_latch` component and the rig's HAL wiring, the GIL in
`wait_complete`, every refusal text the fake emits, and the LinuxCNC facts the code cites.
They match content, not line numbers: a citation's line range is not checked - except the emcmodule.cc line of each row of the adapter module docstring's stat table. Without `reference/` those 70 tests skip, with the check that `reference/` is at its pin (`make reference`), and the rest still run.

### End to end in the simulator (`make sim-e2e`)

`make sim-e2e` (`sim/e2e/run.sh`) drives this UI over HTTP against LinuxCNC
2.9.9 in the simulator and checks LinuxCNC itself, not the UI's picture of it.
Each module under `sim/e2e/` gets a fresh LinuxCNC (container `better-cnc-e2e`
on port 8771, so a developer's `make sim` is untouched); `E2E=test_x.py` runs
one. `sim/e2e/harness.py` runs inside the container: commands go only through
`POST /api/action` as `app.js` sends them, it reads `linuxcnc.stat()` and
`halcmd getp`, and it never opens `linuxcnc.command()` or
`linuxcnc.error_channel()` - the error channel is a queue, and a message read
there would never reach the UI's own drain (`test_deploy` pins that). LinuxCNC's
errors are asserted on the alarm log (each `GET /api/state` drains them), not
on a POST's answer, and a move is over at `current_vel == 0` plus its target
(`stat.inpos` stays True through a teleop jog). Limit switches are moved, not
forced: `place_switch` / `place_switch_ahead` / `press` set the simulated
switch's `home-pos`, never across motor 0 (its sign picks the switch's side),
because `halcmd sets` on a switch signal and `setp` on a joint's limit pin both
fail (sim/README.md, "Limit switch canlandırma"; also `sim/sim.sh press|release`).

`test_baseline.py` must pass and does, about 25 s of tests (30 s with the
container, 2026-11-13, several runs): the sim INI's program directory, the five
switch states on Alarmlar, reset + ON, Home All Z → Y → X to 5 / 5 / −5, ±1 mm
jogs on each axis, MDI inside the limits and `G0 Z0` refused by motion ("would
exceed Z's positive limit" in the alarm log), feed 50 %, the software estop
stopping an MDI move within 0.5 s with the homes kept, a pulled cable put in
ESTOP_RESET by one `GET`, and the cable back plus «Yazılım sıfırla» unhoming
every joint. Once in five runs on 2026-11-13 (while plan WP02 was checked; the
first module of a full `make sim-e2e`) the last step timed out: after the cable
was back and «Yazılım sıfırla» pressed, LinuxCNC had unhomed but stayed in
ESTOP. That points at the reset's 3 × `[EMCIO]CYCLE_TIME` deadline on the
simulator's non-realtime kernel (not measured); the four other runs passed.
Seen again once in two full runs while plan WP03 was checked, again as the
first module, in a run whose LinuxCNC needed a second start attempt (`LinuxCNC
hazır (2. deneme)`): a loaded host. Besides the deadline, the link stub raises
`has_bit` from a 5 ms userspace loop (`sim/hm2_link_stub.py`), and raised after
the reset's 0.5 s `LINK_SETTLE_S` it would trip the latch behind the reset with
the same result. `test_baseline` alone passed 3 of 3 then. Seen once more in
the review of WP04 (its one full run, first module: unhomed, stayed in ESTOP);
alone it passed right after. Plan WP05 changed `software_estop_reset` (no ON
onto a pressed switch, after the link path): its full run passed, the baseline
first, step 11 included. So did plan WP06's full run (236 s, every module
green, 6 expected failures left in three modules), and plan WP07's two
(277-278 s, every module green, the baseline first and step 11 included, 5
expected failures left in two modules), and plan WP08's (341 s, every module
green, step 11 included, 1 expected failure left in `test_run.py`). Plan
WP09's full run (391 s with the containers) had the baseline first and
step 11 timed out the same way (unhomed, stayed in ESTOP); every other module
was green, and `test_baseline` alone passed right after (WP09 changes neither
the reset nor the link path).

`test_state.py` must pass and does, about 19 s of tests (23 s with the
container; three runs alone and two full runs, 2026-11-13): with the machine
off and unhomed, X's left switch pressed shows as joint 0's `min` flag in `GET
/api/state` and trips nothing; after Home All the modes read manual / teleop /
idle and the DRO and the commanded position the home position; through a 10 mm
jog `moving` is true and then false, while LinuxCNC's `stat.position` leads
`actual_position` (0.009 mm at 3 mm/s) and the UI's `commanded` leads its DRO;
during an MDI move `mdi_running` and `moving` are true; `G92 X10` at machine X
20 makes the work X read 10 with `g92_active`, every axis matching AXIS's
formula on LinuxCNC's own numbers, and `G92.1` levels them again.

`test_stop.py` must pass and does since plan WP03, about 17 s of tests (21 s
with the container; three runs, 2026-11-13): Home All stopped while Z searches
leaves joints unhomed and then completes; a 50 mm X jog and an MDI `G0 X200`
stand still within 0.5 s of the POST, measured while the request is in flight
(its answer waits for the stop's second half and a re-render: 0.38-0.43 s for
the G0), the interpreter idle; the page the server sends carries the rail on all
six screens, outside `#app`; DURDUR with nothing moving answers `Durduruldu.`
and logs `Durdur gönderildi`.

`test_messages.py` must pass and does since plan WP04, about 24 s of tests
(28 s with the container; three runs alone and one full run, 2026-11-13): the error box names X's
left switch after an unhomed trip and keeps it while the stop goes to the log;
`G0 Z0` leaves LinuxCNC ON with nothing moved and the interpreter idle
(`linuxcnc.stat`, added in the review of WP04: a refusal explains no stop) and
shows the Z window in Turkish, the line `rejected`, the box unchanged
after the generic refusal arrives; `G0 X50` is `done`; «Tamam» clears only the
box it names (K). Its first run found a real ordering: G0 Z0's later reports
arrived after G0 X50 was sent, and G0 X50 was marked rejected with Z0's text -
fixed by keeping the follow-ups until LinuxCNC's last word (K). The review of
WP04 ran it three more times alone and once in a full run, all green; in that
full run `test_baseline`, the first module, hit its known last-step timeout
(above) and passed alone right after.

`test_limits.py` must pass and does since plan WP05, about 53 s of tests
(57 s with the container; 2026-11-13, three runs - two alone, one in a full
run - all green, and in the review of WP05 three more with H: two alone, one
in a full run):
unhomed X driven onto its left switch (moved 2 mm into its way), Z onto its
top switch and X onto its right one each show the one trip text in the box;
«Yazılım sıfırla» then sends no ON - no new limit error, no `açılamadı` - and
names the switch; the card offers «Limitten kurtar: X+ yönünde 3 mm» (Z−,
X−), one press switches the machine on - the page it answers carries the
override badge - and jogs the joint 3 mm off; the override ends with the jog,
the switch is free, the machine stays ON and the line says `… serbest; limit
koruması yeniden etkin. Referans alın.`; with Y on its front switch Home All
is refused naming «Limitten kurtar», and the card's Y+ frees it; homed, X
tripped at 55 by a 10 mm teleop jog (its right switch moved 5 mm ahead)
comes back 3 mm, inside the soft limits, with no `Referans alın.`, and the
next jog is a teleop jog again; DURDUR while Y, then Z, latches on its home
switch during Home All leaves it there with the machine off - Z homed in Y's
case, the state the jog click during Home All left in batch 1 - and the card
and its recovery free it; and (H, the review of WP05) the software estop
while the recovery jog is still on the switch leaves LinuxCNC showing the
override on (motion's `overriding`, L step 4), yet «Yazılım sıfırla» sends
no ON - no new limit error, no `açılamadı` - and one more press frees the
axis. Once in that review E's arrange timed out: no jog moved X, task still
in MDI mode right after the `G0 X50` move - most likely the line had not
ended and the UI refused the jog («Program çalışırken jog yok.»); E now
waits for the interpreter to go idle first, as `test_baseline` does. The
clean-up frees an axis left on a switch with the UI's «Limitten kurtar»
(`harness.rescue_from_switches` says which way on stderr; the simulator's way
stays as the fallback).

`test_jog_rules.py` must pass and does since plan WP06, about 32 s of tests
(35 s with the container; 2026-11-13, four runs alone and one in a full run,
all green, no clean-up rescue printed). Unhomed, a 50 mm X step and a 10 mm Z−
step are refused with nothing moving for a second and the reason on the
line (`en çok 10 mm`, `en çok 1 mm`), the 50 mm chip and the Z− button are off
with those reasons in `GET /api/state`, and a 10 mm X jog and a 1 mm Z− jog go
whole (`Jog X+10 mm.`). A jog click while Y searches for its switch during Home
All is refused before anything is sent: the page answering it shows the jog
card off (`Referans alma sürüyor — jog kapalı.`), and `linuxcnc.stat` shows the
homing ending normally - every joint homed at 5 / 5 / −5, ON, no switch
pressed, no flag up, no jog refusal from LinuxCNC in the alarm log. Homed, Z at
−5 with a 10 mm step goes up to −3.000 (the cut and its note), where
`stat.position` equals the teleop planner's target `axis.z.teleop-pos-cmd`
within 1e-6 mm (the plan's open item). On the limit Z+ is then refused with
nothing moving, and Z− still goes; X at 5 with a 10 mm X− step stops at 3; a
second 10 mm click while an X jog runs is refused (`Hareket sürüyor`) and X ends
10 mm on. With the rule switched off in a scratch copy, all six fail at their
gap. The homing one reproduces batch 1's: homed `[0, 0, 1]`, the clean-up
freeing Y from its front switch.

`test_homing.py` must pass and does since plan WP07, about 45 s of tests
(48 s with the container; 2026-11-14, three runs alone and two in full
runs, all green, no clean-up rescue printed). One scenario in steps on
one fresh LinuxCNC (`harness.OrderedSteps`; the UI cannot unhome, so the
unhomed steps come first): X alone with Z unhomed is asked about (`Z
referanssız: takım aşağıdaysa X hareketi çarpabilir. …`) and «Vazgeç» moves
nothing; Z alone asks nothing and homes only Z (homed `[0, 0, 1]`, Z at −5,
X and Y unmoved, motion still in free mode), the page answering it shows the
homing card off (`Referans alma sürüyor — durdurmak için «Durdur».`) with
the DURDUR note; Y alone with Z homed asks nothing (`[0, 1, 1]`); Y again is
asked about and «Evet» homes it again; Home All with joints homed is asked
about with the moves in order, nothing moves before «Evet», an «Evet» with a
wrong token sends nothing and says so, and the confirmed Home All ends at
5 / 5 / −5 with motion in teleop; DURDUR while Z searches during a Home All
leaves every joint unhomed (a homing unhomes its joints as it starts) and no
switch pressed, and a Home All then asks nothing and completes Z → Y → X.

`test_program.py` must pass and does since plan WP08, about 67 s of tests
(70 s with the container; 2026-11-14, two runs alone and one in a full run,
all green): `GET /api/state` lists `faz8-kare.ngc`
from `/repo/nc_files` (the UI runs on the repo's INI, LinuxCNC on the sim INI
with its absolute `PROGRAM_PREFIX`: the same directory), enabled; «Yükle» makes
`stat.file` `/repo/nc_files/faz8-kare.ngc`, the screen marks it with its whole
path and the line count `readlines()` gives, and the line says `faz8-kare.ngc
yüklendi.`; `../../etc/passwd`, `/etc/passwd` and `../nc_files/faz8-kare.ngc`
each leave `stat.file` unchanged and say `Program listede yok`; «Yeniden yükle»
opens it again; homed, the program runs (the list off with `Program çalışırken
yeni program yüklenemez.`, a load then refused with the program still running)
to its end in about 47 s, and DURDUR stops a second run within 0.2 s of the
POST. The
kiosk in `sim/` (`sim/sim.sh kiosk`, on the kiosk INI derived from the sim INI)
lists the same `/repo/nc_files` with loading enabled (checked by hand,
2026-11-14).

`test_run.py` must pass and does since plan WP09, about 65 s of tests (69 s
with the container; 2026-11-14, three runs alone and one in a full run, all
green): homed, `faz8-kare.ngc` loaded and started; for 5 s the header reads
`ÇALIŞIYOR · satır N` and the primary button is «Duraklat»; «Duraklat» stops
motion (`current_vel` 0, interpreter PAUSED) within 1 s, the header reads
`DURAKLATILDI`, the primary button «Devam», the position stays put for 2 s,
and «Başlat» is refused with nothing changed; «Devam» moves again; paused
once more, «Adım» runs exactly one line (`stat.motion_line` + 1, the screen's
`line` the same) and pauses; «Devam» runs it to the end, the message says
`Program bitti`, `program.line` never went back and moved on, and the screen
offers «Başlat» again. A second test: «Adım» with nothing running starts the
program paused in AUTO with nothing moving, and DURDUR ends it with `Program
durduruldu: faz8-kare.ngc`. A third (final P0 review, about 23 s): an MDI
`G1 X40 F300` paused in MDI, then `G0 Y60` typed - refused with
`Duraklatılmış …`, `stat.queued_mdi_commands` 0, still paused; «Devam» ends at
X 40 with Y unmoved. With the refusal switched off, LinuxCNC took the line
(the entry read `sent`).
Two more since the P0 cleanup (the module now about 171 s of tests, 175 s
with the container; 2026-11-14, green): «Adım», «Adım», «Devam» runs
`faz8-kare.ngc` to its end with `Program bitti` and no `File ended …` alarm
(the UI's pause before the resume), `stat.paused` 0 after; and «Adım» alone
to the very end leaves `stat.paused` 1 with the interpreter idle - the header
reads `PROGRAM SONU · DURDUR`, the message names «Durdur», «Devam» is off and
refused with nothing moving, and DURDUR clears the pause (`HAZIR`).

The known gaps of the plan's work were written there before each package, as
`@unittest.expectedFailure` in the module of the work that closes them, so each
removed only its own markers; since plan WP09 none is left.
An unexpected success fails the run. What works today of each scenario (a switch
put in the axis's way and the axis tripped on it, say) is in its `setUp`, where
a failure is an error, not an expected failure; each was run with its markers
off to see it fail at the gap itself. The simulator's limits hold here too: no
step generators (Y's second motor, deceleration and following error are not
exercised), no realtime kernel (timeouts about twice what was measured), and
LinuxCNC's state outlives a test (each module starts fresh).

### Coverage: 99.6 %

```
module                   lines  covered      pct
------------------------------------------------
bettercnc.browserwatch     345      340    98.6%
bettercnc.capabilities      94       93    98.9%
bettercnc.bootpage          99       98    99.0%
bettercnc.adapter         1905     1886    99.0%
bettercnc.configmodel      212      211    99.5%
bettercnc.state            356      355    99.7%
bettercnc.app              470      469    99.8%
bettercnc.iniload          145      145   100.0%
bettercnc.mdi              120      120   100.0%
bettercnc.messages         108      108   100.0%
bettercnc.motionrules      374      374   100.0%
bettercnc.profiles         298      298   100.0%
bettercnc.programs         326      326   100.0%
bettercnc.render           719      719   100.0%
bettercnc.viewmodel        908      908   100.0%
------------------------------------------------
TOTAL                     6479     6450    99.6%
```

(2026-11-14, after the P0 cleanup. `adapter.py`'s uncovered lines are the
bodies of `MachineAdapter`'s abstract methods and one defensive `return None`
of the stall detector's `/proc` reader; `LinuxCncAdapter._homing`, dead
before, now serves the MDI gate.)

`ui/tools/covreport.py` discovers the package's modules from the directory; its
hand-kept list had silently left out `bettercnc.mdi` when that module was added.

The uncovered lines (`make coverage-missing`, 2026-10-21): the `...` bodies of
`MachineAdapter`'s abstract methods; `sys.exit(main())` under the `__main__`
guards of `app.py`, `bootpage.py` and `browserwatch.py`, which run only in
subprocesses, out of the tracer's reach; `configmodel`'s TYPE_CHECKING-only
import; the message of the
import-time assert in `capabilities.py`, evaluated only if that assert fails;
and a few `continue`/`return None`/`pass` lines on defensive branches of the
halcmd helper, the screen guard's tab table and its pipe clean-up.

### Mutation testing

Line coverage says a line ran, not that a test would notice it changing. So the
suite was mutation-tested over three rounds, on scratch copies of the tree (the
harness is not in the repo): each mutant one small change to production code or
the fake, each run against the full suite. Round 3 killed 136 of 168 mutants
(81.0 %). Of the 32 survivors, 11 are strictly equivalent and 11 near-equivalent
(for example a joint both homing and homed, which LinuxCNC never reports), so
136 of the 146 left once the equivalent and near-equivalent ones are set aside
were killed (93.2 %). The ten that
survive are listed under What is NOT covered. Plan WP06's jog rule was
mutated the same way (2026-11-13, a scratch runner, the full suite per
mutant). All 40 mutants were killed, across the jog gates, the caps, the
soft-limit cut and its margin, the mode prediction, both adapters' sends and
messages, the mock, the jog card and the fake's soft-limit drop. Plan WP07's
homing rule likewise (2026-11-14, a scratch runner on a mirror of the repo,
the full UI suite per mutant): all 41 mutants were killed, across the gates
and their order, the questions and the route, both adapters' sequences (the
question, the second look, the stop check, the joint sent), the session's
confirmation (token, time limit, stops, any other action, «Evet» as a
motion action), the homing card, the band and the fake's homing.

### What is NOT covered — plainly

**1. `LinuxCncAdapter` on a real machine.** It is now tested —
`test_linuxcnc_adapter.py` (259 tests), `test_limit_recovery.py`,
`test_jog_rules.py` and `test_homing_single.py` drive
every action against `fake_linuxcnc.py` and assert the exact command sequence
and that the fake refused nothing — but on a real machine it has only read
one, view-only (2026-11-12, beside AXIS, its commands cut by an out-of-repo
viewer). The fake is only as right as our reading of the source. It does not
model echo latency or real time (commands complete synchronously), the GIL
hold of a real wait (a fake wait releases it), soft limits, switch positions
(a test sets the hard-limit flags; what motion does with them is modelled,
plan WP05), velocity clamping, the homing switch search, the interpreter,
spindle, coolant, tools or volatile homing; `stat.inpos` is false only for
free-mode jogs and homing, a jog and the limit override it ran under end at
once, and leaving teleop is instantaneous. So timing is unmeasured: `WAIT_TIMEOUT_S` = 0.25 s, the
3 × `[EMCIO]CYCLE_TIME` reset deadline, the 64-message drain cap and the 10 s
request timeout are chosen values. This is still the code most likely to be wrong
on first contact with the real machine — now in its timing more than in its
sequencing.

**2. The mock is not the real adapter.** It homes and jogs instantly, shows
simulated link counters and seed alarms, and accepts some inputs the real
adapter refuses (Known gaps). Tests that pass on the mock prove the rules, not
the machine.

**3. The browser.** `app.js` has no tests — no test runner for JS without
dependencies; `test_app_cli` and `test_stop_rail` pin its handlers as text. It
is deliberately small (about 370 lines) and forwards clicks, fires the stop
rail on the press, holds a re-render while a press is down, swaps `innerHTML`
and watches the connection; all logic worth testing is in Python. The stop rail
was checked once by hand in headless Chrome 157 over the DevTools pipe
(2026-11-13, a script outside the repo, two mock-adapter servers): at
1280 × 800 `#app` ends where the rail begins and nothing reaches under it; the
estop held 1.5 s across a re-render posted once, on the press, its band showed
at once and the answer was on screen while still pressed; a jog button held
1.5 s across a re-render still jogged (with the previous `app.js` the same
press jogged nothing - two re-renders fell inside it); Esc with the MDI field
focused sent DURDUR, Pause and Enter on the focused estop the estop, once each;
in observer mode DURDUR was disabled with its reason shown and neither Esc nor
a press sent anything, while Pause still sent the estop; with the server gone,
the overlay's estop fired on the press. Touch was not tried (no touch screen
here; the kiosk's input device is still open). A failed request is no longer swallowed (2026-10-20): `LOST_MS` (3 s)
without a good answer, or one failed action, covers the screen with
`BAĞLANTI YOK` and "the values are stale" - with an estop button on the
overlay, since the server acts on that one without its lock (2026-10-21) - and
in kiosk mode (`--boot-url`, deploy/README.md) the page goes back to the boot
screen once that answers - seen working in `sim/sim.sh kiosk`. Exercised by hand once, in a real browser against `sim/` (2026-10-19):
typed MDI text and focus survive the 1 Hz re-render, and two bugs were found
and fixed - a click into the MDI field posted an empty MDI line (the click
handler matched the MDI `<form>`'s data-action), and the re-focus after each
poll scrolled the page back to the MDI field, so the estop button could not be
scrolled to. Both are pinned as text in `test_app_cli.TestClickRouting`.

**4. CSS and visual appearance.** No screenshot or layout testing. A stylesheet
change could make the UI unreadable with every test passing.

**5. Anything about the real machine.** These tests prove the UI agrees with the
repo's config and docs. They cannot prove the config is right about the machine —
that rests on the rig's own measurements (scale, travel, homing, 2026-11-12,
`docs/DURUM.md`; the Y DIR fault is fixed, `docs/DURUM.md:12`), and `latency-test`
was never run.

**6. Concurrency, partly.** Tested: `TestSessionLocking` blocks a jog inside the
adapter and shows the software estop still reaches it while every other action
waits for the lock; `TestEstop` holds a fake `wait_complete` open and sends the
estop from another thread; `test_stop_rail` does the same with DURDUR (its first
abort out while `home_all` holds the lock, the second after it, nothing sent
after a stop that came mid-action) and with a motion action queued for the lock;
`TestMockLock` covers the mock's own lock. Not
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

State after the P0 packages (plan WP01-WP10, 2026-11-14): every item below
marked "tried in `sim/` only" or "not measured" has its check on the rig in
the PC list of [12-operator-handover.md](12-operator-handover.md), which also
gives the procedure to switch between AXIS and this UI. Every constant chosen
but not measured is collected in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B13.

Seen on the real machine on 2026-11-12. The UI ran view-only beside AXIS through
an out-of-repo viewer: the real `LinuxCncAdapter` with its command channel replaced
by a recorder, `_drain_errors` and `_stop_on_link_fault` off and the error channel
left to AXIS; only the software estop could go out. What it showed, and where
each item stands after phase 9:

- **Soft limits shown as unmeasured** — `ölçülmedi` and `Yumuşak limitler
  tanımsız`, because the INI still carried the travel markers. The travel was
  measured the same day (soft limits X 3–399, Y 3–387, Z −96.5–−3 mm); with the
  markers gone the UI shows the numbers with no code change (D above).
  → **Fixed:** the INI's travel markers are removed and the screens show those
  limits.
- **Stale profile notes** — the Y DIR fault note and the text that Y's switch ends
  are unknown (`profiles.py`). Both were settled that day: the fault is fixed and
  Y+ is rear. → **Fixed:** `profiles.py` has Y's ends (`gpio.022` +, `gpio.023` −)
  and updated Y notes.
- **No live switch state on Alarmlar** — the real adapter read only the link pins,
  not `gpio.020`–`024`. → **Added in phase 9:** each input's `.in_not` pin is read
  into a Durum column (`ui/tests/test_observe_and_live_hal.py`; fake LinuxCNC
  only, not yet seen on the machine).
- **Step timing shown from the INI, not live HAL** — `stepgen.NN.steplen` and the
  rest were not read back. → **Added in phase 9:** a `Canlı (HAL)` row on the
  Makine screen, read from `stepgen.00`–`03` `steplen`/`stepspace`/`dirsetup`/
  `dirhold` beside the INI's (same tests; fake LinuxCNC only, not yet seen on the
  machine).
- **The gate contradicts itself after homing** — `Program başlatılabilir` next to
  the `limitler tanımsız` warning. → **Fixed** with the markers: the warning no
  longer shows.
- **No observer mode in the repo.** The stock app sends `state(STATE_ESTOP)` on a
  page load when `io_error` is set (`_stop_on_link_fault`) and drains the NML error
  channel (`_drain_errors`); both conflict with running beside AXIS, which is why
  the viewer had to cut them. → **Added in phase 9:** `--observe` opens no error
  channel, refuses every action but the software estop and shows every other
  control disabled, with a banner on every screen. The link-fault estop is deliberately
  kept in `--observe`: on the real driver a cable pull raises `io_error` alone and
  LinuxCNC keeps the machine ON. Tested against fake LinuxCNC only (same tests);
  what remains open is one run beside AXIS on the machine
  ([OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B12).
- **Minor layout issues.** Still open (plan WP18).

Open, and not to be read as fixed:

- ~~**A jog click during homing can leave an axis on its limit switch with the
  machine off**~~ (`make sim-e2e`, 2026-11-13). **Closed by plan WP06 (G):** the
  UI refuses the click before anything is sent; `test_jog_rules.py` B checks
  through `linuxcnc.stat` that the homing then ends normally, and reproduces
  the old failure with the rule switched off. A click in the gap between two
  joints is looked at again `HOMING_GAP_RECHECK_S` later (fake only). AXIS
  beside the UI would still send such a click (its manual_ok does not check
  homing): an operator note in docs/12, Mod A.
- **The jog step caps and the soft-limit cut are not measured on the rig** (plan
  WP06, G). `UNHOMED_MAX_STEP_MM` 10 mm, `UNHOMED_NO_SWITCH_MAX_MM` 1 mm,
  `AT_SOFT_LIMIT_MM` 0.001 mm, `JOG_LIMIT_MARGIN_MM` 0.0001 mm and
  `HOMING_GAP_RECHECK_S` 0.03 s are chosen.
  The cut counts from `stat.position`. That this is where the teleop
  planner stands was seen in `sim/` (`test_jog_rules.py` C: within 1e-6 mm of
  `axis.z.teleop-pos-cmd` at rest), not on the rig. A homed axis left outside
  its soft limits (a recovery jog cut short, say) comes back only with a step
  that reaches inside. The 10 mm chip covers the 3.5–3.7 mm the rig's switches
  sit beyond the limits.
- **DURDUR at the end of a homing can leave the joint on its switch** (plan
  WP03; seen in `sim/` since plan WP05, not tried on the rig). Abort cancels a
  homing wherever it is (command.c:509-520); in the final, latch phase the
  joint stands on its home switch, which on the rig is also its limit switch
  (X−, Y−, Z+), homing no longer ignores the limit, and LinuxCNC trips `joint N
  on limit switch error` with the machine off. AXIS's Esc does the same. Since
  plan WP05 the Kontrol card offers «Limitten kurtar» and it frees the joint
  (`sim/e2e/test_limits.py` F and G stop Y and Z there on purpose); the
  homing itself must then be started again. Since plan WP07 (M) the homing
  card says so while a joint homes, and its buttons, off while the switch is
  pressed, name it and «Limitten kurtar»; there is no motion path of its own
  for it.
- **Single-axis homing and the question before a homing again are tried in
  `sim/` only** (plan WP07, M; `sim/e2e/test_homing.py`), not on the rig.
  The question is asked whenever any joint is homed - more often than AXIS,
  which asks only with every joint homed (plan design (g)) - and for X or Y
  alone while Z is unhomed; the tool axis is the letter Z
  (`motionrules.TOOL_AXIS`), not read from the INI. The confirmation band is
  the session's own state for one kiosk and one operator: a second browser
  would show the same question and could answer it. `CONFIRM_TIMEOUT_S` 15 s,
  `CONFIRM_REFUSED_S` 5 s and `CONFIRM_MIN_S` 1 s are chosen. ~~«Evet» turned
  on with the next 1 Hz re-render, 1-2 s after the question~~ (review of WP07):
  closed in the P0 cleanup - the band carries the time left
  (`data-ready-in-ms`) and app.js renders once more then (`READY_MARGIN_MS`
  30 ms, chosen); in the browser pane on the mock «Evet» turned on 1.035-1.038 s
  after the band, three tries (2026-11-14). The server's rule is unchanged.
  ~~A joint with a negative HOME_SEQUENCE~~ (review of WP07): its one-axis
  button would home its whole group (do_home_one_joint, homing.c:265-281);
  closed in the P0 cleanup - refused, naming the axes that would move
  (`motionrules.HOME_ONE_GROUP`; the rig has none). A joint whose sequence is
  not known (no INI) is not homed alone either (`HOME_ONE_SEQ_UNKNOWN`).
- **The program list and loading are tried in `sim/` only** (plan WP08, H;
  `sim/e2e/test_program.py`), not on the rig, and the kiosk's list only
  through `GET /api/state` in `sim/`. The list is cached by the directory's
  mtime: a file edited in place keeps its old size and date until «Listeyi
  yenile». ~~A file beyond the newest `MAX_FILES` = 200 cannot be loaded from
  the screen~~: closed in the P0 cleanup (B13's open decision, taken: page
  it) - the list shows `PAGE_SIZE` = 200 a page with «Önceki» / «Sonraki» and
  `toplam N dosya`, and any listed file loads; the scan stops at `MAX_FILES` =
  5000, beyond which the list says how many it left out (those cannot be
  loaded from the screen). The page is the browser's own view state, like the
  screen (sent with each render and action, never an action, nothing to
  LinuxCNC), so observer mode pages too and `test_observe_and_live_hal`'s
  ACTIONS are unchanged; one page per poll is rendered whatever the total. A
  file AXIS loaded from outside the directory shows as loaded but cannot be
  reloaded here. `PAGE_SIZE`, `MAX_FILES`, `RACY_S` = 2 s and `MAX_COUNT_BYTES` =
  32 MiB are chosen, not measured; whether a large program's line count (read
  once at its load, under the session lock) is noticeable on the single-core
  PC is not measured. Turkish (non-ASCII) file names are covered by unit tests
  on the fake only; the simulator and the rig have been given ASCII paths so
  far.
- **Pause, resume and single step are tried in `sim/` only** (plan WP09, H;
  `sim/e2e/test_run.py`), not on the rig, and not with M0/M1 in a program (the
  fake models the pause they cause; the programs on the rig have none yet).
  ~~In `sim/` (LinuxCNC 2.9.9), after «Adım» from idle, a second «Adım» and then
  «Devam», LinuxCNC reported `File ended with no percent sign (%) or program
  end (M2)`~~ - explained and closed in the P0 cleanup: the second step leaves
  the interpreter WAITING at the file's end (it read the short file to M2,
  emctaskmain.cc:524-534), and PLAN_RESUME restores the state the last
  PLAN_PAUSE saved (:2344-2352) - READING, from the first step
  (:1078-1091) - so task read past the end (interp_read.cc:3128-3143).
  `program_resume` now sends a PLAN_PAUSE first whenever motion is paused with
  the interpreter not PAUSED (it saves the current state, :2312-2320, and
  moves nothing): in `sim/` pause-then-resume after 2, 3 and 4 steps ended
  with no error (scratch probe, 2026-11-14; `sim/e2e/test_run.py`). AXIS's
  «Devam» still sends the bare resume. ~~TEK ADIM showed for a moment while a
  pause decelerated~~ (review of WP09): closed - paused-and-moving reads TEK
  ADIM only when this UI's last run command was «Adım»
  (`MachineState.run_command`, `single_step_moving`); in observer mode it
  reads as before. ~~START_NOT_TAKEN could be wrong for a program that ends
  within one send~~ (review of WP09): closed - an idle read-back is followed
  by a bounded wait, whose status by this command's serial tells a refusal
  (RCS_ERROR) from a run that ended (DONE with the task still in AUTO; an
  "M2" alone was idle at once in `sim/`), which then says `Program bitti`.
  ~~After stepping to the very end with «Adım» alone, motion's paused flag
  may stay set~~ (final P0 review): it does - in `sim/` `stat.paused` stayed
  1 with `interp_state` IDLE and `exec_state` DONE until an abort; then
  «Devam» made the interpreter read past the end, «Başlat» ran the program
  with the flag still set and «Adım» started it again. Closed: the header reads
  `PROGRAM SONU · DURDUR`, the end message says «Durdur» clears it, «Devam»,
  «Başlat», «Adım» and «Duraklat» are refused with `programs.PAUSED_AT_END`
  and nothing is sent to clear it (`MachineState.pause_left_at_end`;
  `sim/e2e/test_run.py`). ~~The MDI panel stayed enabled while something was
  paused~~ (final P0 review): closed - off with `MDI_WHILE_PAUSED` as its
  reason. The end is judged conservatively:
  any LinuxCNC error during the run - a refusal of something else pressed
  meanwhile too - makes it `hatayla durdu`, never `bitti`. «Duraklat» runs
  under the session lock: behind a busy action it waits like any action
  (DURDUR does not); a lockless pause changes the stop design and belongs to
  plan WP14 (feed hold) and its review. The Kontrol screen has no run strip yet (plan WP18):
  pause and resume are on the Program screen. `MAX_TEXT_BYTES` and
  `LINE_TEXT_MAX` are chosen, not measured.
- ~~**The fake homes all the joints of a Home All at once**~~ (closed in the P0
  cleanup): `FakeMachine` now homes a Home All group by group in HOME_SEQUENCE
  order (`home_all_groups`, homing.c:384-477), a poll sees the gap between
  groups (`advance_homing`; motion's `homing_active` still set, a jog there
  refused, command.c:853-857), a negative sequence homes its group
  (`home_one_targets`), and a homing that leaves every joint homed switches
  motion to teleop (homing.c:1400, control.c:261-263) - so the extra
  `teleop_enable(1)` is gone. The second look in Home All's gap
  (`HOMING_GAP_RECHECK_S`, M) is now tested in the fake's real gap between
  groups too; the e2e still cannot time a press into that window.
- **«Limitten kurtar» is unmeasured on the rig** (plan WP05, L). The 3 mm step
  is chosen from the INI's notes (release ≤ 0.64 mm, over-travel ~1.3 mm), not
  measured per switch; if it is short the machine stops again, safely, and the
  card says to press once more. A homed joint more than about 9.5 mm outside
  its soft limit (10 mm cap) is refused, with no way off in this UI.
- **A homed machine that trips mid-move in coordinated mode** (an MDI line or
  a program running into a switch that sits inside the soft limits). The
  recovery needs MANUAL - task takes the override out of ON only there
  (emctaskmain.cc:846-852) - and motion grants a mode change only in position
  (control.c:961-1000), while disabled motion leaves its INPOS flag as it was
  (control.c:1463-1477), false mid-move by the source. In `sim/` it did not
  stay false: a scratch probe (2026-11-13, LinuxCNC 2.9.9) ran `G1 X90 F300`
  through X's right switch moved to X65 - `stat.inpos` read false during the
  move and true at the first look after the trip - and «Limitten kurtar» went
  through (MANUAL, free mode, X−3 mm, the switch free, ON). Not explained from
  the source and not tried on the rig (the plan's "bayat INPOS" item); if
  MANUAL is ever not reached the recovery refuses (`MANUAL moduna geçilemedi
  …`), and only a LinuxCNC restart would help.
- **An override left on.** A recovery refused after its override went out (ON
  not holding, free mode not reached, a stop or estop meanwhile) leaves the
  protection off until a jog away from the switch ends or MDI mode is entered;
  the refusal and the header badge say so. LinuxCNC's own direction check still
  holds for a joint jog, and this UI refuses a jog onto the switch.
- **Pause is not on every keyboard.** Compact and laptop keyboards often lack
  it, and the kiosk's keyboard is not chosen yet. Esc works everywhere, and the
  rail's ACİL STOP is always on the screen.
- **The rail fires on any touch that lands on it**, a scroll that starts there
  included: the safe direction, by choice (the plan's risk note). Touch itself
  is untried on a real screen (no touch screen yet); with Chrome's touch
  emulation (2026-11-13, review of plan WP03) one tap on ACİL STOP or DURDUR
  sent it once, and a 0.3 s tap on a jog button across a re-render still
  jogged.
- **On a screen narrower than about 680 px the nav's last item** («Makine»)
  slides partly under the rail (Chrome at 640 px: its right 38 px). At 680 px
  and wider nothing of `#app` is under it. The kiosk's resolution is still open
  (layout: plan WP18).
- **The stop's answer waits** for its second half's bounded wait and a
  re-render (0.38-0.43 s for a G0 in `sim/`); the band covers the operator's
  feedback meanwhile, and the first abort went out as the request arrived.
- **No on-screen simulation marker.** `MockAdapter`, the default adapter, shows
  simulated values — link counters, a DRO, seeded `hm2_eth` and stepgen events
  stamped with the current time, even on the marble profile — and nothing on
  any screen says SİMÜLASYON. Only the server's stderr says it is the mock
  (plan WP20).
- ~~**The estop message can be overwritten.**~~ Closed by plan WP04 (K): the
  estop is an ERROR and stays in the error box until «Tamam».
- **An MDI line's result is a heuristic** (plan WP04, E). Only the newest line
  is followed (LinuxCNC names no line for MDI moves); a line superseded before
  its result says `sonucu izlenmedi`. A new error in MDI mode, or within 2 s of
  the send, is taken for the line's: an unrelated error then (from another GUI,
  or after a mode change this UI did not make) could mark a good line
  `rejected`; and a line settled `done` as this UI leaves MDI mode stays done
  if its error comes later still. Only the history's colour and note can be
  wrong; the raw error stays in the log and in the box.
- **A circular, probe or rigid-tap refusal has no last word** (command.c:
  1070-1074, :1424-1428, :1493-1497: no generic text after inRange's reports),
  so its reports are follow-ups only by the 2 s window: a second such refusal
  on the same line within 2 s would not take the box (it is in the log).
- **The error box holds the latest error only.** Earlier ones are in the alarm
  log, which has no acknowledgement yet (plan WP16).
- **The translations match LinuxCNC's English texts.** LinuxCNC translates its
  messages with gettext; started under a locale it has a catalogue for (de,
  fr, ...), its texts would not match and would show raw behind `LinuxCNC:`.
  On the rig they were English (2026-11-12: AXIS showed `invalid params in
  linear command`); its locale is not otherwise recorded.
- **With an error the stuck band is about 130 px tall** at 1280 × 800, over a
  screen that already scrolls (layout: plan WP18, WP19).
- **Keyboard focus does not survive the 1 Hz re-render.** `swap()` restores
  focus only to a field with a `name` (the MDI input); a button reached with
  Tab loses focus at the next poll, so slow Tab navigation goes nowhere
  (seen in `sim/` on 2026-10-20). The operator uses touch or a mouse (plan
  WP19).
- **The alarm list has no cap.** A persistent error would add a row on every
  1 Hz poll; the 64-message drain cap bounds one drain, not the growth across
  drains (plan WP16).
- ~~**Joints are numbered by position.**~~ Closed in the P0 cleanup:
  `map_linuxcnc_state` takes the INI's joint numbers (`joint_numbers`, sorted
  `[JOINT_n]` keys) and reads each joint's homing and hard-limit flags and
  homing config by its own number; a missing `[JOINT_n]` no longer shifts a
  later axis (`test_edge_cases.TestJointsByNumber`). On the rig the joints are
  0, 1, 2, so nothing changes there.
- ~~**MDI gate gaps (latent).**~~ Closed in the P0 cleanup, in the safe
  direction: `_needs_homing` drops a unary function's name that follows a
  word's letter, keeping the letter (`XSIN[30]` is an X word, `ACOS[0.5]` an A word; `GFUP[27.5]` a G code that cannot be known:
  motion; interp_read.cc:1391-1480), counts M98 and an M code held in a
  parameter or written as an expression (`M[98]`: read_m takes one,
  interp_read.cc:1119; added in the review of the cleanup) as motion, M6 when `[EMCIO]TOOL_CHANGE_AT_G30`,
  `TOOL_CHANGE_QUILL_UP` (rs274ngc_pre.cc:869-870) or `TOOL_CHANGE_POSITION`
  (taskclass.cc:463-480) makes a tool change move, and any code
  `[RS274NGC]REMAP` names (every REMAP line, `IniFile.get_all`;
  rs274ngc_pre.cc:1030-1035) - `mdi.MdiContext`, both adapters. Any MDI line
  is refused while a joint homes (`MDI_WHILE_HOMING`: entering MDI mode aborts,
  emctask.cc:286-289), the real adapter looking again in Home All's gap when
  NO_FORCE_HOMING is set. All of this matters only with
  `[TRAJ]NO_FORCE_HOMING`, which no config sets.
- ~~**`MockAdapter` accepts inputs the real adapter refuses**~~ (a negative feed
  or one above the INI ceiling, any feed change with no INI, any WCS string).
  Closed in the P0 cleanup: both adapters apply `adapter.feed_override_decision`
  and `wcs_refusal`, and the mock's WCS goes through its MDI gate
  (`mdi_refusal`, which it already had for MDI lines). Since plan WP06 (G) its
  jog follows the real adapter's rule, since WP07 (M) its homing.
- ~~**The estop's HTTP response can wait.**~~ Closed in the P0 cleanup:
  `do_POST` renders the estop's answer through `Session.view_unless_busy`,
  which does not wait for the session lock - with an action holding it the
  answer carries no page (`"html": null`), app.js leaves the page and the
  rail's band (`ACİL STOP GÖNDERİLDİ — onay bekleniyor`) as they are, and the
  next poll shows the machine. The lockless estop and DURDUR sends are
  untouched; DURDUR's answer still waits for its own second half (by design,
  above).
- **`LinuxCncAdapter` has never driven a real machine** (on 2026-11-12 it only
  read one, view-only — above), and its timing
  constants are chosen, not measured: `WAIT_TIMEOUT_S` = 0.25 s, the
  3 × `[EMCIO]CYCLE_TIME` reset deadline, the 64-message drain cap and the 10 s
  request timeout. They are the first things to check on the rig (OPEN-QUESTIONS
  B13; docs/12 steps 10-11). (In `sim/`, on a non-realtime kernel, none of them
  caused a refusal; that says nothing about the rig.)
- **Nothing stops a second commanding UI.** Started without `--observe` beside
  AXIS, this UI sends commands and drains LinuxCNC's error channel away from
  AXIS; and a hand-made POST is accepted like a click (plan WP12). docs/12
  makes `--observe` the rule beside AXIS and checks with `pgrep` which UI runs.
- ~~**A lone surrogate in an `mdi` or `wcs` value breaks every later response**~~
  (review of plan WP07): closed in the final P0 review. `do_POST` turns a lone
  surrogate in any posted field into U+FFFD (`app._wire_text`) before
  `Session.act`, and the page and JSON answers encode with `replace`
  (`test_server_and_iniload` TestHttpRoutes).
- **Mode B is not kiosk mode.** Started from a desktop with
  `linuxcnc configs/test-rig/test-rig-kiosk.ini` (docs/12), the UI runs
  without `--boot-url`: tooltips, the context menu and F12 stay, and no boot
  screen or restart loop exists (the UI still exits with LinuxCNC, and
  LinuxCNC with it). In `sim/sim.sh kiosk` the first Pause sent with `wtype`
  after the kiosk came up did not reach the page; Esc and three later Pause
  presses did (2026-11-14). The real keyboard is a rig check (docs/12,
  steps 10 and 22).

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
  (`Kartla bağlantı koptu — makine durduruldu` with the action to take when a
  link fault is set; otherwise `Makine acil stopa geçti — komut bu ekrandan
  gelmedi` or `Makine kapatıldı — ...`). The UI's own software estop is not
  reported again; LinuxCNC's own error text, drained in the same snapshot
  (since plan WP04: or within 2 s), is kept - unless it is a refusal or the
  link was lost (K).
  "The estop message can be overwritten" above is closed by plan WP04 (K).
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
  outline is drawn, the cursor is an arrow on plain text (a hand on buttons,
  an I-beam in the input) and the scrollbar is themed.
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

- **The board link, as the real driver loses it (audit, 2026-10-21).** A
  cable pull makes `hm2_eth` set `io_error` and nothing else (hm2_eth.c:883);
  while it is set the driver reads nothing, so `has_bit` never rises
  (watchdog.c:38), the HAL estop latch never trips and LinuxCNC goes on
  saying ON while the board's own watchdog has dropped the outputs. The sim
  stub had modelled the opposite (it held `has_bit`), and the recovery that
  passed in `sim/` would have answered "zaten açık" on the rig and cleared
  nothing. Now the adapter sends ESTOP the moment a snapshot sees `io_error`
  with the machine ON (`_stop_on_link_fault`), and «Yazılım sıfırla» clears
  `io_error` first, waits `LINK_SETTLE_S` for the bite the driver then reads,
  clears that `has_bit` too, unhomes, resets, switches on; `io_error` coming
  straight back means the cable is still out. Re-run in `sim/` with the stub
  rewritten to the driver's behaviour, with real `halcmd`. Tests:
  `TestLinkRecovery`.
- **The stall detector counts CPU time too** (2026-10-21): task does not sleep
  while it has queued work (emctaskmain.cc:3593), so a long run of program
  lines could have frozen the switch count and had the kiosk kill LinuxCNC
  mid-program. `rtapi_app` (uspace's realtime process) is now on the list of
  processes that must be there. `set_feed_override` refuses without the
  status like every other send.
- **The kiosk, after the audit (2026-10-21):** `browserwatch` survives its own
  errors and a server dying mid-answer (the browser exits with the watch), is
  itself restarted by `kiosk-session`, restarts a browser that stops
  answering the pipe, throttles the way back from the wait page and closes a
  duplicate of our page; a fresh UI that has not been asked yet is not
  "stale"; `serve()`'s exit code no longer races; the UI describes the INI
  LinuxCNC runs (`--ini`); why the control system last stopped reaches the
  new UI's first message (`--startup-note-file`); the unit waits for no
  network and keeps local time; `bootstrap-ssh.sh` locks the door before
  installing sshd; `machine.sh` keeps `docs/PLAN-BUGUN.md` on the Mac.

**Deployment rule from the last one (Pi 4 included):** start the UI after
LinuxCNC, as the user that started LinuxCNC (same `HOME`). The kiosk
(deploy/README.md) does it by construction: the UI is LinuxCNC's own
`[DISPLAY]` program, `deploy/bettercnc-display`.

Also open, found while writing this:

- ~~**Invented homing config without an INI (marble).**~~ Closed in the P0
  cleanup: with no INI `adapter._joint_config` and
  `configmodel.joint_config_from_profile` give None for HOME_SEQUENCE,
  HOME_IGNORE_LIMITS and HOME_SEARCH_VEL; the Kontrol screen reads `Gerekli
  sıra: INI yok — sıra bilinmiyor (INI'de [JOINT_n] yok)`, `sıra ?` and `arama
  yönü bilinmiyor (INI yok)` on the chips, the start block `… Sıra: INI yok —
  sıra bilinmiyor.`, `HOME_IGNORE_LIMITS bilinmiyor (INI yok)` (not the
  unreadable-value warning, which is about a bad value in an INI that
  exists; review of the cleanup), and no axis is homed alone (whether motion would take
  others along cannot be said). Home All stays.
- ~~**Jog reason when the link is down.**~~ With the board link not connected
  but the machine ON, the jog card and MDI panel said `Makine devre dışı — …`
  while the header could read `HAZIR`. **Closed by plan WP06 (G):** both now say
  `Kart bağlantısı okunamıyor — …` (`viewmodel._motion_off_reason`).
- **No profile field names an estop-input pin.** When a machine wires one, the
  profile needs that field and `LinuxCncAdapter._read_hal` must read the pin into
  `hal_values["estop_input"]` — never `stat.estop`.

Next steps:

- **`LinuxCncAdapter` needs one session driving the machine.** Its read path ran
  on the rig on 2026-11-12, view-only beside AXIS (above). The procedure (Mod A
  beside AXIS, then Mod B in its place) and the ordered list of checks are in
  [12-operator-handover.md](12-operator-handover.md). The first-session
  checklist passed in `sim/` against LinuxCNC 2.9.9 on 2026-10-19 (a joint jog
  before homing, the software estop mid-jog, a reset within the 0.3 s
  deadline, homing without a wait, Z -> Y -> X, the UI exiting with LinuxCNC),
  with simulated switches, no steps and no realtime kernel: on the rig it is a
  re-check, timing first. There is no hardware estop on the rig: keep a hand on
  the power.
- **Y axis DIR fault** (`docs/DURUM.md:12`) — fixed on the rig on 2026-11-12
  (Y's second driver moved to AXIS 3). It was rendered as an error in the mock's
  alarm log and, from the profile's notes, in the Kontrol warnings, hardcoded
  rather than read from a live pin because it was a hardware fault, not a
  machine state; those notes went stale with the fix (above).
- **Marble profile is a skeleton.** Eleven TODOs, no INI, no input map. It exists
  now so the gaps are visible; fill it as `docs/01-machine-survey.md` fills.
  Its homing config is no longer invented (P0 cleanup, "Also open").
- ~~**No program selection**~~ — closed by plan WP08 (H): the Program screen
  lists `[DISPLAY]PROGRAM_PREFIX` and loads the file picked. **No G-code
  preview and no extents check before a run** (plan WP15): the prototype's
  path SVG was driven by an invented toolpath; a real preview needs parsing a
  real `.ngc`, kept off the 1 Hz poll on the single-core PC.
