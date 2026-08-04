# test-rig — Phase 1.5 config

Draft LinuxCNC configuration for the small 3-axis DIY router used to prove the
Pi + Zhulong control chain before the marble machine.

---

## 🛑 This config is UNTESTED and WILL NOT run correctly yet

**Nothing here has been validated against hardware.** The Zhulong board has not been
detected, so the HAL component prefix and every stepgen pin assignment is a guess with a
citation, not a fact.

### The first run must be with the motors mechanically UNCOUPLED

Take the belts off, or unbolt the motors from the leadscrews — whatever this frame
allows. Then:

- A wrong `SCALE` spins a motor, not a gantry into its frame.
- A wrong direction sign is something you *observe* rather than something that breaks a
  limit switch.
- A stepgen mapped to the wrong axis is obvious and harmless: you command X and the Z
  motor turns.

Only couple the mechanics after every ASSUMPTION below is resolved and each axis has been
seen to move the right way by the right amount.

### There is no hardware emergency stop

All five of the board's isolated inputs are consumed by limit/home switches, so no input
remains for an estop button. The GUI estop stops *motion*; it removes power from nothing.
**Have the power supply switch or the mains plug within arm's reach for the first run.**
Discussed at length in the ESTOP section of `test-rig.hal`.

---

## Files

| File | What it is |
|---|---|
| `test-rig.ini` | Machine parameters. Every key commented with its meaning, its units, and where the value came from. Key names and units verified against `ini-config.adoc` / `ini-homing.adoc` with inline line citations. |
| `test-rig.hal` | Wiring. Loads `hostmot2` with `debug_modules=1` and `debug_idrom=1` so the stepgen `clock_frequency` and IDROM header appear in dmesg. Limit/home switch nets are present but **commented out on purpose**. |

### What was verified while writing these

- **107 INI keys** cross-checked against the reference docs: all either documented or a
  known custom (HAL-consumed) variable. Zero unrecognised.
- **57 HAL pin names** cross-checked against the LinuxCNC source tree: all located.
- **30 INI variable references** in the HAL: all resolve to keys that exist in the INI.

That means the config is *internally consistent and syntactically plausible*. It does
**not** mean it is correct for this machine — that is what the ASSUMPTIONs below are.

---

## Every ASSUMPTION, and what resolves it

Grep them yourself at any time:

```bash
grep -rn 'ASSUMPTION:' configs/test-rig/
```

### A1 — Board IP address is `10.10.10.10`

`test-rig.ini:101`, `[HOSTMOT2]BOARD_IP`

`10.10.10.10` is the alternative documented in `hm2_eth(9)`; the factory default is
`192.168.1.121`. **The Zhulong's actual address is unverified** (docs/04 Q3).

**Resolves it:**
```bash
./scripts/detect-board.sh
```
Sweeps `10.10.10.10`, `192.168.1.121`, `192.168.1.10`, `192.168.0.10` and reports which
answers.

---

### A2 — HAL component prefix is `hm2_7i96.0`  ⚠️ highest-impact

`test-rig.ini:107` (`[HOSTMOT2]BOARD`), and `test-rig.hal:70`

Every `hm2_` pin in the HAL file is built from this. **We do not get to choose it** —
`hm2_eth` derives it from the board's own reported name string (`hm2_eth.c:1487`).
Anything beginning `7I96` yields `hm2_7i96.0`, *including* the unrecognised-board
fallback, which copies only four characters (`hm2_eth.c:1462`). A board reporting
`ZHULONG` would give `hm2_ZhUL.0` — note the mixed case, which is real.

**Resolves it:**
```bash
halrun -I
halcmd: loadrt hostmot2
halcmd: loadrt hm2_eth board_ip="10.10.10.10"
halcmd: show pin
```
The common prefix of every listed pin **is** the answer. Do not derive it by hand. Also
check `dmesg` for either line:

```
hm2_eth: discovered <name>
hm2_eth: Unrecognized ethernet board found: <name> -- port names will be wrong
```

The second line's presence tells you you are on the fallback path.

**Fix if wrong:** one edit to `[HOSTMOT2]BOARD` in the INI. The HAL file needs no changes
because it indirects through `hm2_[HOSTMOT2](BOARD)`.

---

### A3 — Firmware provides at least 3 stepgens, 0 encoders, 0 pwmgens

`test-rig.ini:116`, `[HOSTMOT2]CONFIG`

Asking for more instances than the bitfile provides fails at load. The Zhulong's
connectors imply at least 6 stepgens, but the firmware's real instance count is unknown.

**Resolves it:**
```bash
mesaflash --addr 10.10.10.10 --device 7I96 --readhmid | grep -iA2 'stepgen\|encoder\|pwmgen'
```
Read the module inventory. Alternatively the driver prints it — `debug_modules=1` is
already set in the HAL, so after starting: `dmesg | grep -A6 'Modules used'`.

---

### A4 — `stepgen.00/.01/.02` drive `AXIS 0/1/2`, i.e. X/Y/Z  ⚠️ highest-impact

`test-rig.hal:88`

**The single biggest assumption in the config.** The board has six AXIS connectors and the
stepgen-to-connector mapping is decided by the vendor's bitfile. There is no reason beyond
convention to expect `AXIS 0 == stepgen.00`. Note also that on the *stock* Mesa bitfile
the per-channel order is **Dir before Step** (docs/03), which is not the intuitive
order — and our board runs different firmware, so even that is no guide.

**Resolves it:**
```bash
mesaflash --addr 10.10.10.10 --device 7I96 --readhmid | grep -i stepgen
```
Look for per-pin lines of the form:
```
IO Pin NNN (...): StepGen #N, pin Step (Output)
IO Pin NNN (...): StepGen #N, pin Dir  (Output)
```
and record which IO number each instance and signal lands on.

**Then confirm physically, motors uncoupled**, one stepgen at a time:
```bash
halcmd setp hm2_7i96.0.stepgen.00.enable 1
halcmd setp hm2_7i96.0.stepgen.00.velocity-cmd 0.5
# watch which motor turns, then set it back to 0
```

---

### A5 — Travel limits: X 150 mm, Y 150 mm, Z 50 mm

`test-rig.ini:313` (X), `:383` (Y), `:424` (Z)

**Not derived from the machine.** GRBL's `$130`–`$132` were all still at the factory
default of 200 mm while the same EEPROM's `SCALE` values had clearly been calibrated
(docs/07) — so they say nothing about this frame. The values here are deliberate
under-estimates, chosen so a wrong guess stops an axis *short* of the frame rather than
into it.

Note Z uses the opposite sign convention: it homes at the top, so travel is negative
(`MIN_LIMIT = -50`, `MAX_LIMIT = 0`).

**Resolves it:** measure. With the config loaded but soft limits deliberately widened, jog
each axis slowly end to end and read off the actual travel with a rule. Then set these to
the measured value minus a few mm of margin. This is blocker **T1** in docs/07.

---

### A6 — Homing directions and which sensor is at which end

`test-rig.ini:338` (X), `:393` (Y), `:439` (Z), and `test-rig.hal:230`

Assumed: X and Y home toward their **minimum** end (negative `HOME_SEARCH_VEL`), Z homes
**up** toward its single top sensor (positive `HOME_SEARCH_VEL`).

docs/08 confirms the **IO numbers** — the silkscreen `20 21 22 23 24` really are global IO
numbers, hypothesis verified. It does **not** establish which physical end of which axis
each sensor sits at. Planned mapping:

| Input | Assumed role |
|---|---|
| `gpio.020` | joint.0 (X) home + one limit end |
| `gpio.021` | joint.0 (X) other limit end |
| `gpio.022` | joint.1 (Y) home + one limit end |
| `gpio.023` | joint.1 (Y) other limit end |
| `gpio.024` | joint.2 (Z) home + limit — Z has **one** sensor, at the top |

**Resolves it:** with the machine powered but **not enabled**, open halmeter and push each
switch by hand:
```bash
halrun -I
halcmd: loadrt hostmot2 debug_modules=1
halcmd: loadrt hm2_eth board_ip="10.10.10.10" config="num_encoders=0 num_pwmgens=0 num_stepgens=3"
halcmd: start
halcmd: loadusr halmeter pin hm2_7i96.0.gpio.020.in
```
Record, for each of the five inputs: which switch moves it, and **in which direction**.

---

### A7 — Switch polarity: `.in` versus `.in_not`

`test-rig.hal:230` and the LIMIT AND HOME SWITCHES section

The inputs are opto-isolated (docs/04). **An optocoupler input stage commonly inverts**, so
`.in` may read TRUE when the switch is *open*. LinuxCNC inverts an input by choosing the
negated pin, not by setting a parameter — every pin has both `.in` and `.in_not`
(`ioport.c:250`, `:263`).

**This is why the switch nets are commented out.** Connecting a switch with inverted
polarity means LinuxCNC believes the axis is permanently on its limit — or, far worse,
believes it never is.

**Resolves it:** the same halmeter session as A6. Pick whichever pin reads **FALSE** when
the axis is clear of the switch.

---

### A8 — The declared FPGA clock is the real one

Not marked `ASSUMPTION:` in the config because it is not a value we chose — it is a value
we *inherit* and cannot verify from the host. Covered at `test-rig.hal:33-54`.

`steplen`, `stepspace`, `dirsetup` and `dirhold` are all multiplied by the IDROM's
declared `clock_frequency` to get FPGA counts (`stepgen.c:348`, `:359`, `:370`, `:381`).
Nothing measures the real clock. The HAL parameter reads back as whatever you typed, so
there is **no feedback path at all**. Our board carries a **50.000 MHz** crystal while a
stock 7i96 declares 33 MHz.

**Resolves it, partially — record what the driver believes:**
```bash
dmesg | grep -i 'clock_frequency\|Clock Tag\|ClockLow\|ClockHigh'
```
`debug_modules=1` and `debug_idrom=1` are already set in the HAL so these lines appear.
Save the output into `docs/board-dumps/`.

**Resolves it properly — measure a pulse:** set `steplen` to something large and
unmistakable, put a scope or logic analyser on an AXIS STEP terminal, and compare. Full
procedure in docs/02 Step 7. This is blocker **B5**, and Phase 1.5 is the cheap place to
close it.

---

## Values that are NOT assumptions

For contrast — these have real provenance and should not be second-guessed without
evidence:

| Value | Source |
|---|---|
| `SCALE` X 833.300, Y 823.500, Z 1250.000 | The machine's own GRBL EEPROM, empirically calibrated by whoever built it. `docs/board-dumps/grbl-eeprom-2026-08-03.json`, analysed in docs/07. All positive because `$3 = 0`. |
| `MAX_ACCELERATION = 10.0` | GRBL `$120`–`$122 = 10`. GRBL and LinuxCNC use the **same** units for acceleration (mm/s²), so no conversion (docs/06 Trap 4). Known to work on this machine. |
| `steplen` / `stepspace` = 10000 ns, `dirsetup` / `dirhold` = 50000 ns | TB6560 datasheet analysis, docs/09. GRBL has no equivalent settings for stepspace/dirsetup/dirhold, so these could not have come from the EEPROM. |
| `FERROR = 0.050` / `MIN_FERROR = 0.005` | The values every upstream hm2-stepper sample uses — `7i96s.ini:117-118`, identically in `7i93.ini`, `3x20-small.ini`, `4i65.ini`. The precedent for exactly this config type. |
| `HOME_IGNORE_LIMITS = YES` | Required because home and limit share one switch. `ini-homing.adoc:174`: *"If you do not have a separate home switch set this to YES and connect the limit switch signal to the joint home switch input in HAL."* |

`SCALE` X and Y differ by 1.2 % despite nominally identical mechanics. **That is what an
honest empirical calibration looks like — do not tidy them to match.**

---

## Velocity: why 3 mm/s and not 14

`MAX_VELOCITY = 3.0` on every axis is a first-run figure, not a hardware limit.

The eventual target, from docs/09 — the TB6560 is rated **15 kHz** maximum STEP frequency,
a limit of the *driver board*, not the motor or the mechanics. With a 20 % margin (12 kHz)
against the calibrated scales:

| Axis | SCALE steps/mm | ceiling at 12 kHz | mm/min |
|---|---:|---:|---:|
| X | 833.3 | 14.4 mm/s | 864 |
| Y | 823.5 | 14.6 mm/s | 874 |
| Z | 1250.0 | 9.6 mm/s | 576 |

Two things worth knowing before chasing those numbers:

- **The machine ran at 500 mm/min under GRBL**, so the headroom is modest and Z barely
  improves at all. The lever that actually helps is **reducing microstepping** — dropping
  1/16 to 1/8 halves `SCALE` and doubles the ceiling (docs/09). That requires recalibrating
  `SCALE`.
- **The 15 kHz figure is the manufacturer's and is independently unverified** (docs/09).
  Find the real ceiling empirically: raise speed until steps are lost, then back off.

If you raise a joint's `MAX_VELOCITY`, **raise the matching `[AXIS_x]` too** or the axis
limit silently caps it. Also raise `STEPGEN_MAX_VEL` to stay ~20 % above.

---

## Suggested order of work

1. `./scripts/detect-board.sh` → resolves **A1**, produces the readhmid dump.
2. Read the dump → resolves **A3**, **A4**.
3. `halcmd show pin` → resolves **A2**.
4. `dmesg | grep -i clock` → records **A8** (partially).
5. halmeter on `gpio.020`–`024`, pushing switches by hand → resolves **A6**, **A7**.
6. Uncomment the switch nets in `test-rig.hal`, using whichever of `.in` / `.in_not` step 5
   established.
7. **Motors uncoupled**, command each stepgen individually → confirms **A4** physically.
8. Scope a step pulse → closes **A8** and blocker **B5**.
9. Jog each axis end to end and measure → resolves **A5**.
10. Only now couple the mechanics, and home for the first time.

> **Before that first homing attempt**, read `ini-homing.adoc:46`: it is possible to start
> homing on the wrong side of a home switch, which *combined with `HOME_IGNORE_LIMITS`*
> leads to a hard crash. With limits ignored during homing there is nothing to stop the
> axis. Know which side of each switch you are on before pressing Home.

---

## Status

**DERIVED — untested.** No part of this config has been run. Update this section as
assumptions close, and record what resolved each one.

| ID | Assumption | Status | Resolved by |
|---|---|---|---|
| A1 | Board IP `10.10.10.10` | open | |
| A2 | HAL prefix `hm2_7i96.0` | open | |
| A3 | ≥3 stepgens in firmware | open | |
| A4 | stepgen 00/01/02 → X/Y/Z | open | |
| A5 | Travel 150/150/50 mm | open | |
| A6 | Homing directions, sensor ends | open | |
| A7 | Switch polarity `.in` vs `.in_not` | open | |
| A8 | Declared FPGA clock is real | open | |
