# 06 — GRBL → LinuxCNC Settings Translation

Reference for **Phase 1.5**: proving the Pi + Zhulong control chain on the small
Arduino/CNC-shield router before touching the marble machine. We replace the Arduino,
keep the shield and its stepper drivers.

The point of Phase 1.5 is that when we get to the marble machine, **its only unknown is
its own mechanics** — the controller chain will already be proven end to end.

**Status: DERIVED.** Nothing here has been run against the test machine.

---

## ⚠️ On what is and is not verifiable here

This document has two halves with **very different evidential standing**, and conflating
them would be a mistake.

**The LinuxCNC half is verified.** Every INI key name, section, and unit below is checked
against `reference/linuxcnc/docs/src/config/ini-config.adoc`, `ini-homing.adoc`, or the
driver source, and cited to file and line.

**The GRBL half is not, and cannot be.** Grepping the whole of `reference/` for `grbl`
returns exactly two incidental hits — an unrelated lathe SVG and a probe-dialect branch in
a G-code utility ([gcode_ripper.py:4970](../reference/linuxcnc/lib/python/qtvcp/lib/ripper/gcode_ripper.py#L4970)).
**There is no GRBL documentation in this repository.** The GRBL meanings and units below
come from the GRBL 1.1 settings documentation as I know it, and **cannot be checked against
`reference/`.**

Practical consequence: **treat the machine's own `$$` dump and `$I` banner as the
authority.** If a value looks implausible against the table below, the table is what is
wrong. Confirm the firmware version first (see "Obtaining the dump" at the end), because
GRBL 0.9 numbered several settings differently and various forks add their own.

---

## 🛑 The five conversion traps

These cause the overwhelming majority of GRBL→LinuxCNC conversion errors. Read them before
touching any numbers.

### Trap 1 — Velocities: mm/MINUTE → mm/SECOND. Divide by 60.

**This is the single most common error.**

`$110`/`$111`/`$112` ("max rate") are **mm per minute**. LinuxCNC's `MAX_VELOCITY` is
**machine units per second** — verified for both sections:

- `[AXIS_x]MAX_VELOCITY` — *"Maximum velocity for this axis in machine units per second"*
  ([ini-config.adoc:915](../reference/linuxcnc/docs/src/config/ini-config.adoc#L915))
- `[JOINT_n]MAX_VELOCITY` — *"Maximum velocity for this joint in machine units per second"*
  ([ini-config.adoc:988](../reference/linuxcnc/docs/src/config/ini-config.adoc#L988))

```
$110 = 2000.000 mm/min  →  MAX_VELOCITY = 2000 / 60 = 33.333   (mm/sec)
```

**Forgetting this gives a machine commanded to move 60× too fast.** It will not move 60×
too fast — it will stall, scream, lose steps, or slam into a hard stop. If your first
jog attempt behaves violently, this is the first thing to check.

### Trap 2 — Homing rates are also mm/minute. Divide by 60.

`$24` (homing feed) and `$25` (homing seek) are **mm/min**. Both LinuxCNC homing
velocities are per second:

- `HOME_SEARCH_VEL` — *"Initial homing velocity in machine units per second"*
  ([ini-config.adoc:1083](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1083))
- `HOME_LATCH_VEL` — *"Homing velocity in machine units per second to the home switch
  latch position"*
  ([ini-config.adoc:1087-1089](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1087-L1089))

Note the mapping is **crossed** relative to intuition:

| GRBL | Meaning | LinuxCNC |
|---|---|---|
| `$25` homing **seek** | fast approach to find the switch | `HOME_SEARCH_VEL` |
| `$24` homing **feed** | slow re-approach to latch precisely | `HOME_LATCH_VEL` |

GRBL calls the fast one "seek" and the slow one "feed"; LinuxCNC calls them "search" and
"latch". `$25` is the larger number and pairs with `HOME_SEARCH_VEL`.

### Trap 3 — Step pulse: MICROseconds → NANOseconds. Multiply by 1000.

`$0` is step pulse width in **microseconds** (typically 10). The hostmot2 stepgen
parameter is in **nanoseconds** — *"'steplen' - (u32, RW) Duration of the step signal, in
nanoseconds"*
([hostmot2.adoc:534](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L534)).

```
$0 = 10 µs  →  STEPLEN = 10000 ns
```

Same units for `stepspace`, `dirsetup` and `dirhold` — all nanoseconds
([hostmot2.adoc:511-540](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L511-L540)).

**Getting this wrong by 1000× in the wrong direction produces a 10 ns pulse**, far below
any driver's minimum, and the machine simply will not step. Which, mercifully, is the safe
direction to fail.

### Trap 4 — Acceleration passes through UNCHANGED. Do not convert it.

`$120`/`$121`/`$122` are **mm/sec²**. LinuxCNC `MAX_ACCELERATION` is *"machine units per
second squared"* ([ini-config.adoc:916](../reference/linuxcnc/docs/src/config/ini-config.adoc#L916),
[:989](../reference/linuxcnc/docs/src/config/ini-config.adoc#L989)).

**Same units. Copy the number verbatim.**

```
$120 = 500.000 mm/sec²  →  MAX_ACCELERATION = 500.0
```

This trap is the mirror of Trap 1: having just internalised "divide velocities by 60",
people helpfully divide acceleration too, and end up with an axis that takes forever to
get moving. GRBL is inconsistent — velocity in per-minute, acceleration in per-second² —
and you have to be inconsistent with it.

### Trap 5 — Steps/mm magnitude carries over; the SIGN may not.

`$100`/`$101`/`$102` (steps/mm) map to `[JOINT_n]SCALE` with the **same magnitude**.
`SCALE` is *"the number of pulses that corresponds to a move of one machine unit"*, and for
steppers *"the number of step pulses issued per machine unit"*
([ini-config.adoc:1280-1285](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1280-L1285)).

```
$100 = 250.000  →  SCALE = 250
```

**But GRBL's `$3` (direction port invert mask) has no INI equivalent.** In LinuxCNC the
idiomatic way to flip an axis direction is to **negate `SCALE`**:

```
SCALE = -250    # axis runs the other way
```

`SCALE` is a custom variable fed to `stepgen.NN.position-scale`
([hm2-stepper-eth.hal:87](../reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/hm2-stepper-eth.hal#L87)),
and `position = counts / position_scale`
([hostmot2.adoc:525-526](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L525-L526)),
so a negative scale reverses travel.

The alternative is inverting the direction *pin*, which the driver does support for
stepgen-owned output pins — `gpio.NNN.invert_output` is created when the pin is a full GPIO
**or** an output owned by a module
([ioport.c:276-292](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/ioport.c#L276-L292)),
and the stepgen docs confirm it: *"The Step and Direction pins of each StepGen have two
additional parameters […] 'invert_output'"*
([hostmot2.adoc:539-548](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L539-L548)).

**Prefer negating `SCALE`** — it lives in the INI where you can see it, rather than in a
HAL `setp` you will forget about.

**We cannot decode `$3` on paper anyway.** It is a bitmask over GRBL's internal axis
ordering, and whether bit 0 means "X is inverted relative to what LinuxCNC would do"
depends on the shield wiring. **Determine direction empirically**: jog each axis a small
distance and watch which way it goes. That takes two minutes and is not guesswork.

---

## The full settings table

`—` means no equivalent. **GRBL column: unverifiable against `reference/` (see the warning
at the top). LinuxCNC column: verified and cited.**

### Machine/AVR-specific settings

| GRBL | Meaning | Units | LinuxCNC equivalent | Conversion | Trap |
|---|---|---|---|---|---|
| `$0` | Step pulse width | **µs** | `[JOINT_n]STEPLEN` → `stepgen.NN.steplen` | **× 1000** → ns | **Trap 3** |
| `$1` | Step idle delay | ms | **none** | — | AVR-specific; see "What does not translate" |
| `$2` | Step port invert | mask | `gpio.NNN.invert_output` on the step pin | not decodable on paper | Rarely needed; step polarity is usually irrelevant to a driver that triggers on an edge |
| `$3` | Direction port invert | mask | negate `[JOINT_n]SCALE` | not decodable on paper | **Trap 5** — determine empirically |
| `$4` | Step enable invert | bool | HAL: invert the enable signal | via `invert_output` or a HAL `not` | Depends how the shield's EN pin is wired |
| `$5` | Limit pins invert | bool | HAL: use `gpio.NNN.in_not` instead of `.in` | — | LinuxCNC inverts *inputs* by choosing the negated pin, not a parameter |
| `$6` | Probe pin invert | bool | HAL: `gpio.NNN.in_not` | — | Test machine has no probe |

### Reporting and G-code interpretation

| GRBL | Meaning | Units | LinuxCNC equivalent | Conversion | Trap |
|---|---|---|---|---|---|
| `$10` | Status report | mask | **none** | — | GUI concern, not config |
| `$11` | Junction deviation | mm | **no clean equivalent** | — | LinuxCNC's path blending is controlled by G64 P-/Q- and `[TRAJ]` settings, a different model entirely. Do not try to map a number across. |
| `$12` | Arc tolerance | mm | **no clean equivalent** | — | Arc handling is in the interpreter, not a tunable of this kind |
| `$13` | Report in inches | bool | `[TRAJ]LINEAR_UNITS` (machine units) | — | **Not the same thing.** `$13` is display-only; `LINEAR_UNITS` ([ini-config.adoc:850](../reference/linuxcnc/docs/src/config/ini-config.adoc#L850)) defines the machine's actual unit system. Do not set metric machine units because `$13=0`. |
| `$32` | Laser mode | bool | **none** | — | No spindle on the test machine |

### Limits and homing

| GRBL | Meaning | Units | LinuxCNC equivalent | Conversion | Trap |
|---|---|---|---|---|---|
| `$20` | Soft limits enable | bool | implied by `MIN_LIMIT`/`MAX_LIMIT` | — | LinuxCNC has no on/off switch; soft limits exist whenever the keys are set ([ini-config.adoc:1032,1036](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1032)) |
| `$21` | Hard limits enable | bool | whether `joint.N.*-lim-sw-in` is netted in HAL | — | **Read this first** — it tells us whether the existing switches were ever used |
| `$22` | Homing cycle enable | bool | whether `HOME_SEARCH_VEL` is non-zero | — | *"If HOME_SEARCH_VEL is non-zero, then LinuxCNC assumes that there is a home switch"* ([ini-homing.adoc:134](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L134)) |
| `$23` | Homing dir invert | mask | **sign** of `HOME_SEARCH_VEL` / `HOME_LATCH_VEL` | not decodable on paper | Direction of travel is the sign ([ini-homing.adoc:137-138](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L137-L138)) — determine empirically |
| `$24` | Homing **feed** (slow) | **mm/min** | `[JOINT_n]HOME_LATCH_VEL` | **÷ 60** | **Trap 2** — and note feed↔latch, not feed↔search |
| `$25` | Homing **seek** (fast) | **mm/min** | `[JOINT_n]HOME_SEARCH_VEL` | **÷ 60** | **Trap 2** |
| `$26` | Homing debounce | ms | **none** | — | LinuxCNC debounces in HAL with the `debounce` component if needed, not via INI |
| `$27` | Homing pull-off | mm | `[JOINT_n]HOME_OFFSET` — **approximately** | see note | **Not a clean mapping.** `$27` is "back off this far after latching". `HOME_OFFSET` *"sets the joint coordinate position to HOME_OFFSET, thus defining the origin"* ([ini-homing.adoc:201](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L201)) — it defines a coordinate, not a retreat distance. Related in spirit, different in mechanism. Set it deliberately; do not copy `$27` blindly. |

### Spindle

| GRBL | Meaning | Units | LinuxCNC equivalent | Conversion |
|---|---|---|---|---|
| `$30` | Max spindle speed | RPM | `[SPINDLE_0]MAX_FORWARD_VELOCITY` | **Not applicable to Phase 1.5** — test machine has no spindle control |
| `$31` | Min spindle speed | RPM | `[SPINDLE_0]MIN_FORWARD_VELOCITY` | as above |

The `[SPINDLE_<num>]` section exists
([ini-config.adoc:1323+](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1323)) but
**we do no spindle work in Phase 1.5**, so `$30`/`$31` are recorded and ignored. Spindle
control arrives with the marble machine, where it goes over the Zhulong's 0–10 V output or
Modbus.

### Per-axis settings — the ones that matter

| GRBL | Meaning | Units | LinuxCNC equivalent | Conversion | Trap |
|---|---|---|---|---|---|
| `$100`/`$101`/`$102` | X/Y/Z steps per mm | steps/mm | `[JOINT_n]SCALE` | **magnitude unchanged**, sign may flip | **Trap 5** |
| `$110`/`$111`/`$112` | X/Y/Z max rate | **mm/min** | `[JOINT_n]MAX_VELOCITY` **and** `[AXIS_x]MAX_VELOCITY` | **÷ 60** | **Trap 1** |
| `$120`/`$121`/`$122` | X/Y/Z acceleration | **mm/sec²** | `[JOINT_n]MAX_ACCELERATION` **and** `[AXIS_x]MAX_ACCELERATION` | **unchanged** | **Trap 4** |
| `$130`/`$131`/`$132` | X/Y/Z max travel | mm | `[JOINT_n]MIN_LIMIT`/`MAX_LIMIT` **and** `[AXIS_x]` equivalents | see note | Sign and origin depend on homing |

**On `$130`–`$132` and the limit keys.** GRBL's max travel is a single positive length.
LinuxCNC wants two absolute coordinates per joint and per axis:

- `[JOINT_n]MIN_LIMIT` / `MAX_LIMIT` — *"The minimum/maximum limit for joint motion, in
  machine units. When this limit is reached, the controller aborts joint motion"*
  ([ini-config.adoc:1032-1040](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1032-L1040))
- `[AXIS_x]MIN_LIMIT` / `MAX_LIMIT` — *"soft limit for axis motion, in machine units"*
  ([ini-config.adoc:917,921](../reference/linuxcnc/docs/src/config/ini-config.adoc#L917))

Where the travel *sits* on the coordinate scale depends entirely on where you home and
what `HOME_OFFSET` you choose. If you home to the minimum end and set `HOME_OFFSET = 0`,
then `MIN_LIMIT = 0` and `MAX_LIMIT = $13x`. If you home to the maximum end, it is
`MIN_LIMIT = -$13x`, `MAX_LIMIT = 0`. **The converter cannot know which**, so it emits both
with a TODO rather than picking one.

Note that **both** `[JOINT_n]` and `[AXIS_x]` need velocity, acceleration and limits. They
are not redundant — see the joints/axes discussion below.

---

## What has NO GRBL source and must be decided by hand

Nothing in a `$$` dump determines these. The converter emits them as TODO.

| LinuxCNC key | Why GRBL has nothing to say | Reference |
|---|---|---|
| `FERROR` | GRBL is strictly open-loop with no position feedback concept, so it has no following-error notion at all. With hardware stepgen, `position-fb` comes from the FPGA accumulator, so following error is meaningful and must be set. | [ini-config.adoc:1063-1068](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1063-L1068) |
| `MIN_FERROR` | as above. Present ⇒ velocity-proportional error limits. | [ini-config.adoc:1058-1062](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1058-L1062) |
| `HOME_SEARCH_VEL` **sign** | `$23` is a mask over GRBL's internal ordering; not decodable on paper. | [ini-homing.adoc:126-142](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L126-L142) |
| `HOME_LATCH_VEL` **sign** | Same sign ⇒ latch in the same direction; opposite ⇒ back off. GRBL does not expose this choice. | [ini-homing.adoc:144-157](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L144-L157) |
| `HOME_SEQUENCE` | GRBL homes on a fixed built-in cycle (typically Z first, then X/Y together). LinuxCNC makes the order explicit and per-joint. | [ini-homing.adoc:248-267](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L248-L267) |
| `HOME` / `HOME_OFFSET` | Requires deciding where the origin sits. | [ini-homing.adoc:196-220](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L196-L220) |
| `STEPSPACE`, `DIRSETUP`, `DIRHOLD` | **GRBL has no equivalent settings at all.** It has only `$0` (pulse width) and `$1` (idle delay). Direction setup/hold timing is implicit in the AVR's step routine. These must come from the **stepper driver's datasheet.** | [hostmot2.adoc:511-540](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L511-L540) |
| `STEPGEN_MAX_VEL` / `STEPGEN_MAX_ACC` | Headroom for the stepgen position loop; a LinuxCNC-specific tuning concept. Docs advise 1–10 % above the joint limits. | [ini-config.adoc:1314-1320](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1314-L1320) |
| `[TRAJ]MAX_LINEAR_VELOCITY` | GRBL has no machine-wide velocity ceiling distinct from per-axis. | [ini-config.adoc:859](../reference/linuxcnc/docs/src/config/ini-config.adoc#L859) |

**`STEPSPACE`/`DIRSETUP`/`DIRHOLD` are the ones to take seriously.** They protect the
driver. Get them from the driver datasheet once the shield is photographed — that is a
listed unknown for this machine.

### ⚠️ A naming trap in the stepgen variables

`SCALE`, `STEPLEN`, `STEPSPACE`, `DIRSETUP`, `DIRHOLD`, `STEPGEN_MAX_VEL` and
`STEPGEN_MAX_ACC` are **not core INI keys.** LinuxCNC itself never parses them. They are
*custom variables* — the mechanism is documented at
[ini-config.adoc:128-166](../reference/linuxcnc/docs/src/config/ini-config.adoc#L128-L166),
and `SCALE` is literally the worked example there:

```ini
[JOINT_0]
TYPE = LINEAR
SCALE = 16000
```
```hal
setp stepgen.0.position-scale [JOINT_0]SCALE
```

`ini-config.adoc:1278` introduces them only as *"The following items might be used by a
StepGen component."*

**The name only has to match between your INI and your HAL file.** And upstream is
inconsistent about it: the docs describe `STEPGEN_MAXVEL` and `STEPGEN_MAXACCEL`
([ini-config.adoc:1314,1318](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1314)),
while the shipped sample INI and HAL use `STEPGEN_MAX_VEL` and `STEPGEN_MAX_ACC`
([7i96s.ini:110-111](../reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/7i96s.ini#L110-L111),
[hm2-stepper-eth.hal:89-90](../reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/hm2-stepper-eth.hal#L89-L90)).

Both work; neither is "the" name. `scripts/grbl2ini.py` emits the **sample-config
spelling** (`STEPGEN_MAX_VEL`) so the output drops straight into the stock
`hm2-stepper-eth.hal`. If you write your own HAL, match whatever you chose there. A
mismatch fails at load time with an INI-variable error, which is at least loud.

---

## Conceptual differences that are not settings

These are the things that bite you *after* the numbers are right.

### 1. GRBL has no joints/axes distinction. LinuxCNC does.

In GRBL, "X" is one thing: a motor, a coordinate, and a limit, fused.

LinuxCNC splits this in two:

- **`[JOINT_n]`** — a physical motor/actuator. `JOINTS = 3`
  ([ini-config.adoc:896](../reference/linuxcnc/docs/src/config/ini-config.adoc#L896))
  counts motors.
- **`[AXIS_x]`** — a coordinate in the workspace. `COORDINATES = X Y Z`
  ([ini-config.adoc:844](../reference/linuxcnc/docs/src/config/ini-config.adoc#L844))
  names them.

A kinematics module maps between them
([ini-config.adoc:900](../reference/linuxcnc/docs/src/config/ini-config.adoc#L900)). For a
simple router, `trivkins` makes it one-to-one:

```
JOINT_0 = X, JOINT_1 = Y, JOINT_2 = Z
```

So for our test machine the distinction is nearly invisible — **which is exactly why it is
worth understanding here rather than on the marble machine.** You must still set velocity,
acceleration and limits in **both** sections. They are enforced at different stages: joint
limits abort *joint* motion, axis limits bound *coordinated* motion.

**This is the concept that pays off later.** If the marble machine turns out to have a
dual-motor gantry Y — the open blocker B1 in [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) — it
becomes four joints mapped to three axes (`trivkins coordinates=XYYZ`), and the
joints/axes split stops being a formality. GRBL cannot express that machine at all.

### 2. GRBL's limit and home switches are the same physical input. LinuxCNC separates them — and supports sharing.

GRBL wires one switch per axis end and uses it for both hard limits (`$21`) and homing
(`$22`). There is no separate home switch.

LinuxCNC has three distinct motion HAL input pins per joint:

| Pin | Source |
|---|---|
| `joint.N.home-sw-in` | [homing.c:245](../reference/linuxcnc/src/emc/motion/homing.c#L245) |
| `joint.N.pos-lim-sw-in` | [motion.c:760](../reference/linuxcnc/src/emc/motion/motion.c#L760) |
| `joint.N.neg-lim-sw-in` | [motion.c:761](../reference/linuxcnc/src/emc/motion/motion.c#L761) |

**Separate pins do not mean separate wires.** One physical switch can drive several pins —
this is a documented, supported pattern, and it is exactly the GRBL topology.

**Two INI keys make it work:**

`HOME_IGNORE_LIMITS` — *"When you use the limit switch as a home switch […] this should be
set to YES. When set to YES the limit switch for this joint is ignored when homing"*
([ini-config.adoc:1101-1104](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1101-L1104)).
Without it, homing into a switch that is *also* a limit switch trips a limit fault.

The docs add a real warning: *"You must configure your homing so that at the end of your
home move the home/limit switch is not in the toggled state you will get a limit switch
error after the home move"*
([ini-config.adoc:1104](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1104)) — so
the final move must come off the switch.

`HOME_IS_SHARED` — *"If there is not a separate home switch input for this joint, but a
number of momentary switches wired to the same pin, set this value to 1 to prevent homing
from starting if one of the shared switches is already closed"*
([ini-homing.adoc:222-226](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L222-L226),
[ini-config.adoc:1105-1107](../reference/linuxcnc/docs/src/config/ini-config.adoc#L1105-L1107)).

The HAL pattern for all switches on one input is documented at
[stepper.adoc:123-131](../reference/linuxcnc/docs/src/config/stepper.adoc#L123-L131):

```hal
### Shared home switches all on one parallel port pin?
### that's ok, hook the same signal to all the axes, but be sure to
### set HOME_IS_SHARED and HOME_SEQUENCE in the INI file.

net homeswitches <= parport.0.pin-10-in
net homeswitches => joint.0.home-sw-in
net homeswitches => joint.1.home-sw-in
net homeswitches => joint.2.home-sw-in
```

Substitute a Zhulong `gpio.NNN.in` for the parport pin and this is our config. And note
that when switches are shared, `HOME_SEQUENCE`
([ini-homing.adoc:248-267](../reference/linuxcnc/docs/src/config/ini-homing.adoc#L248-L267))
stops being optional — joints must home one at a time.

**GRBL's `$5` (invert limit pins) becomes a HAL choice, not a parameter:** use
`gpio.NNN.in_not` instead of `gpio.NNN.in`. Both exist for every pin, including
module-owned inputs
([ioport.c:250,263](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/ioport.c#L250)),
and the docs confirm *"Both full GPIO pins and I/O pins used as inputs by active module
instances have this pin"*
([hostmot2.adoc:420-426](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L420-L426)).

**Read `$21` and `$22` before assuming anything.** The test machine has switches
physically present, but if `$21=0` and `$22=0` they were never wired up or never worked.
That is a fact about the machine we do not yet have.

### 3. Steps are generated in different places, which makes some GRBL settings meaningless.

**GRBL** generates every step pulse in an interrupt on the ATmega328P. The AVR's timing
*is* the step timing.

**LinuxCNC + Zhulong** commands a rate into an FPGA register once per servo period; the
FPGA emits pulses from its own clock. See
[02-board-bringup.md](02-board-bringup.md) Step 7.

Settings that therefore stop meaning anything:

| GRBL | Why it becomes meaningless |
|---|---|
| `$1` step idle delay | Tells the AVR how long to hold the drivers enabled after motion stops, working around AVR timer behaviour. LinuxCNC has no equivalent; if you want a disable delay it is a HAL `timedelay` on the enable signal, a deliberate design choice, not a translated setting. |
| `$0` step pulse width | **Survives, but changes character.** In GRBL it is a *software* delay the AVR busy-waits. On the FPGA it becomes a hardware counter value. Convert the number (Trap 3), but understand you are configuring different machinery. |
| `$11` junction deviation, `$12` arc tolerance | Compromises for a 16 MHz 8-bit CPU doing look-ahead in limited RAM. LinuxCNC's trajectory planner is a different algorithm on a different class of hardware. No mapping. |
| `$2` step port invert | GRBL toggles bits in a port register. On hostmot2 the stepgen owns the pin; inversion is `gpio.NNN.invert_output`. |

**And a consequence in our favour:** GRBL's maximum step rate is bounded by AVR interrupt
load — around 30 kHz on a good day, and it degrades as axes move together. The Zhulong's
FPGA has no such coupling. **If the test machine was step-rate-limited under GRBL, it may
run meaningfully faster after the retrofit.** So do not treat `$110` as a hardware ceiling
— it may have been an AVR ceiling. Re-derive the real maximum from the motor and driver
once the chain works.

**The one that does not get easier:** the FPGA clock question. `steplen` is scaled by a
clock the driver takes on faith from the IDROM
([02-board-bringup.md](02-board-bringup.md) Step 7). GRBL's `$0` on an AVR was at least
verifiable by knowing the crystal. **Phase 1.5 is the right time to put a scope on a step
pulse** — a cheap machine, no marble, no consequences.

---

## Obtaining the `$$` dump

### Connect

GRBL 1.1 uses **115200 baud** on the Arduino's USB-serial port. (GRBL 0.9 and earlier used
9600 — if 115200 produces garbage, try 9600, and note that a 0.9 machine has different
setting numbers than the table above.)

8 data bits, no parity, 1 stop bit, no flow control.

```bash
# Linux/macOS - find the port
ls /dev/ttyUSB* /dev/ttyACM* /dev/tty.usbserial* 2>/dev/null

# screen
screen /dev/ttyUSB0 115200
#   exit with Ctrl-A then k

# or picocom, which handles line endings more predictably
picocom -b 115200 --omap crlf /dev/ttyUSB0
```

**Line ending matters.** GRBL needs a **newline** to terminate a command. A terminal
configured to send nothing on Enter will appear to hang. If typing `$$` produces no
response, this is the first thing to check — before concluding the firmware is not GRBL.

Opening the port **resets the Arduino** (DTR toggle). Expect a banner, then wait a second
before typing.

### What you should see

On connect:

```
Grbl 1.1h ['$' for help]
```

Then `$$` returns one setting per line:

```
$0=10
$1=25
$2=0
$3=0
...
$100=250.000
$110=2000.000
$120=500.000
$130=200.000
ok
```

Some builds and most GUIs (UGS, bCNC, Candle) append descriptive comments:

```
$0=10 (Step pulse time, microseconds)
```

`scripts/grbl2ini.py` accepts both forms.

**Save it raw:**

```bash
# capture to the repo - do not retype values by hand
picocom -b 115200 --omap crlf /dev/ttyUSB0 | tee docs/board-dumps/grbl-dump-$(date +%Y%m%d-%H%M%S).txt
```

Then:

```bash
python3 scripts/grbl2ini.py docs/board-dumps/grbl-dump-*.txt
```

### Interpreting `$I`

`$I` reports the build info and version:

```
[VER:1.1h.20190825:]
[OPT:V,15,128]
ok
```

- **`VER`** — firmware version and build date. **Confirm this says `1.1`** before trusting
  the settings table above.
- **`OPT`** — compile-time options. Each letter is a build flag; `V` commonly indicates
  variable spindle, and the numbers are block-buffer and RX-buffer sizes.

**Record `$I` verbatim alongside the `$$` dump.** The `OPT` string tells us what the build
actually supports, which matters if a setting is missing from `$$` — a setting absent
because the feature was compiled out looks identical to a setting we failed to capture.

### If `$$` returns nothing

Work through these in order:

1. **Wrong baud.** Try 9600 (GRBL ≤ 0.9).
2. **No newline on Enter.** Configure the terminal to send LF or CRLF.
3. **Opened too early.** The reset-on-connect banner may have been missed; press Enter and
   wait.
4. **Wrong port.** Another `/dev/ttyUSB*` or a permissions problem — check you are in the
   `dialout` group.

If all four are excluded and there is still no response, **the firmware is not GRBL.**

In that case we need **the sketch source**, not a settings dump. Ask for the `.ino` and any
`config.h`, or read the flash back with `avrdude` and identify it. Possibilities include a
custom sketch, a GRBL fork with a changed protocol, Marlin (which answers `M503`, not
`$$`), or FluidNC (`$$` works but the settings are name-based, not numbered). **Do not
guess the parameters from the machine's behaviour** — a wrong steps/mm on a machine with
soft limits disabled drives it into its own frame.

---

## Phase 1.5 workflow

1. **`$I` and `$$`, saved raw** into `docs/board-dumps/`. Confirm GRBL 1.1.
2. **Photograph the shield** — the stepper driver identity is an open unknown, and
   `STEPSPACE`/`DIRSETUP`/`DIRHOLD` come from its datasheet, not from GRBL.
3. **Run `scripts/grbl2ini.py`** to get draft fragments. **Read its warning block** — it
   lists everything a human must supply.
4. **Fill in the TODOs**: driver timing from the datasheet, homing signs empirically,
   `HOME_SEQUENCE`, `FERROR`/`MIN_FERROR`, and the limit-coordinate decision.
5. **Bring up the Zhulong** on the bench first — [02-board-bringup.md](02-board-bringup.md).
   Do not combine an unproven board with an unproven config.
6. **Verify step timing with a scope** — Step 7 of the bring-up doc. This is the cheap
   opportunity.
7. **Assemble the real INI/HAL** in `configs/`, from proven fragments.
8. **Jog each axis a small distance** and confirm direction before homing. Fix direction by
   negating `SCALE`.
9. **Then home**, then run something.

---

## Verification log

| Date | What was confirmed | Evidence |
|---|---|---|
| _(pending)_ | Firmware is GRBL 1.1 | `$I` output |
| _(pending)_ | `$21`/`$22` — were limits/homing ever enabled? | `$$` dump |
| _(pending)_ | Stepper driver type | shield photograph |
| _(pending)_ | Step pulse width measured vs commanded | scope |
