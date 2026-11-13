# test-rig — Phase 1.5 config

LinuxCNC configuration for the small 3-axis DIY router used to prove the PC + Zhulong
control chain before the marble machine (a PC booted from the LinuxCNC 2.9.10 live USB,
amd64 PREEMPT_RT kernel — not a Pi; since 2026-11-12 an MSI desktop, before that a Windows
laptop; see docs/DURUM.md:21).

---

## ✅ Measured on the rig — Y DIR fixed; scale, travel, switch nets and homing done

**Updated 2026-11-12.** Phases 1–8 of the test-rig plan ran on the machine on 2026-11-12,
and every number they produced is recorded in
[`docs/board-dumps/rig-measurements-2026-11-12.json`](../../docs/board-dumps/rig-measurements-2026-11-12.json)
(cited below as *the JSON*). The Y DIR fault is fixed (Y's second driver moved to its own
output, AXIS 3 = `stepgen.03`), the axis directions are known, `SCALE` and travel are
measured, the switch nets are connected, homing works and repeats, moves past the soft
limits are refused, and a first program (`nc_files/faz8-kare.ngc`) ran four times without
an error. All eight original assumptions are resolved; no `ASSUMPTION` marker is left.

| | |
|---|---|
| **Board side** | ✅ **VERIFIED** — prefix, module inventory, stepgen mapping (StepGen 3 = AXIS 3 included), IO numbers, clock |
| **Machine side** | ✅ **MEASURED 2026-11-12** — directions, `SCALE` (verified within 1 %), travel and soft limits, switch nets, homing, soft-limit refusal, first program. Still open, none of them a marker: Z drifts ~0.02 mm per run, the Y two-side racking check, X motor-cable durability |

Dumps this is based on, both in `docs/board-dumps/`:

```
readhmid-10.10.10.10-2026-08-05.txt     IDROM, module inventory, pin-out by connector
printpd-10.10.10.10-2026-08-05.txt      raw pin descriptors, all 34 pins
rig-measurements-2026-11-12.json        the machine: voltages, scale, travel, switches, homing
```

Readable pin map: **[docs/10-7i92-pinout-verified.md](../../docs/10-7i92-pinout-verified.md)**

### ⚠️ The headline correction: this is a 7i92, not a 7i96

Everything written in this repo before 2026-08-05 assumed the Zhulong was a **Mesa 7i96
derivative**. **That was wrong.** The board reports itself as `7I92` over LBP16 and its
IDROM says `BoardName MESA7I92` (`readhmid:4-6, :12`). `mesaflash --device 7i96` fails
outright with *"no 7I96 board found"*.

What actually changed:

| | Assumed (7i96) | Actual (7i92) |
|---|---|---|
| HAL prefix | `hm2_7i96.0` | **`hm2_7i92.0`** |
| IO pins | 51 (3 × 17) | **34 (2 × 17)** |
| Connectors | P1, TB1, TB2, TB3 | **P2 (IO 0–16), P1 (IO 17–33)** |
| Clock Low | 33 MHz (assumed) | **100 MHz** |
| Load path | uncertain | **clean `7I92` branch (4-character match, `hm2_eth.c:1183`), Outcome A** |

The 34-pin geometry means docs 03 and 04, and parts of 08, described a board we do not
have. They are corrected/superseded rather than deleted — see the docs index.

### Commissioning — done (2026-11-12)

The config has run since 2026-08 (docs/DURUM.md) and was commissioned on the rig on
2026-11-12. The motors are coupled to the mechanics, so know what protects what:

- The soft limits act **only after homing**. Until then LinuxCNC bounds a jog only to
  ±(full range) around the current position (`command.c:153-174`).
- Before homing, X and Y stop on their switches at both ends and Z on its top switch.
  **Nothing protects the table below Z** until Z is homed — there is no bottom switch.
- With the machine ON, a pressed switch trips `joint N on limit switch error`, and the
  machine will not enable while a joint rests on a limit. To get off one: Override Limits,
  then jog off **at once** — LinuxCNC 2.9 clears the override as soon as motion is next in
  position.

### Known start problem: an orphaned shared-memory segment

After a TERM shutdown LinuxCNC sometimes leaves its motion SysV shared-memory segment
behind (key `0x00000064`, `nattch` 0). The next start then fails, AXIS saying *"A
configuration error is preventing LinuxCNC from starting"* (an `s.joints` timeout). With no
LinuxCNC process running, remove that segment (`ipcrm`) before starting. The kiosk does this:
`deploy/kiosk-session` runs `deploy/kiosk.sh clean-shm` before every LinuxCNC start. By hand,
with AXIS: `deploy/kiosk.sh clean-shm`, or the commands in deploy/README.md.

### There is still no hardware emergency stop

Unchanged by the dumps, and now quantified: the board has **8 permanently-GPIO pins**
(IO 17–24) of which the vendor brings out 5 as isolated inputs and 3 as outputs
(`CW`, `OUT 1`, `OUT 2`). All five inputs are consumed by limit/home switches. The GUI
estop stops *motion*; it removes power from nothing. **Keep the supply switch within
arm's reach.**

Worth noting though: with `num_encoders=0` the board now offers **9 more GPIO-capable
pins** at IO 25–33, so on this machine a hardware estop input is no longer physically
impossible — see OPEN-QUESTIONS B2 and docs/10. It would need the encoder terminals'
front-end characterised first (ohmmeter, before any 24 V device is connected). Wiring the
estop into the drive-power chain, so it works even if the controller dies, remains the
better answer (see the estop section of `test-rig.hal`).

---

## Files

| File | What it is |
|---|---|
| `test-rig.ini` | Machine parameters. Every key commented with meaning, units, and provenance. Key names/units verified against `ini-config.adoc` / `ini-homing.adoc` with inline line citations. |
| `test-rig.hal` | Wiring. Loads `hostmot2` with `debug_modules=1 debug_idrom=1`. Limit/home switch nets **connected** (`.in_not`, since 2026-11-12); Y's second motor on `stepgen.03` at the end of the file. |

The measurements behind the values: `docs/board-dumps/rig-measurements-2026-11-12.json`.
The first program: `nc_files/faz8-kare.ngc` (`[DISPLAY]PROGRAM_PREFIX`).

### Verification performed

- **107 INI keys** cross-checked against the reference docs — all documented or a known
  HAL-consumed custom variable.
- **79 HAL pin and parameter names** (plus the hm2 read/write functions) cross-checked
  against the LinuxCNC source tree — all located. Recounted 2026-11-12 with `stepgen.03`
  and the switch nets (`ioport.c:263`, `homing.c:245`, `motion.c:760-761`).
- **90 INI variable references** (29 distinct) in the HAL — all resolve.
- **Board facts** cross-checked against both dumps and against `hm2_eth.c:1183-1192`.

Internally consistent, correct about the board, and proven on the machine: it loads,
enables, homes, jogs and runs a program inside the measured soft limits (2026-11-12).

---

## The config string, and one correction to it

```
CONFIG = "num_stepgens=4 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx"
```

What each does and which IO pins it releases to plain GPIO — a pin whose owning module is
not instantiated falls back to full GPIO (`hostmot2.adoc:400-401`):

| Setting | Effect | Frees |
|---|---|---:|
| `num_stepgens=4` | keeps StepGen 0–3 (IO 4–11), drops 4–5. StepGen 3 drives Y's second motor (AXIS 3, since 2026-11-12) | IO 12–15 (4) |
| `num_encoders=0` | drops all 3 QCount instances | IO 25–33 (9) |
| `num_pwmgens=0` | drops the single PWM instance | IO 16 (1) |
| `sserial_port_0=xxxxxxxx` | disables all SSerial channels | IO 0–3 (4) |

18 pins freed, plus the 8 permanently-GPIO pins (IO 17–24) = **26 GPIO available**, 8
consumed by the four stepgens. 8 + 26 = 34. ✓ (Until 2026-11-12 it was `num_stepgens=3`:
20 freed, 28 GPIO, 6 consumed.)

> ### ⚠️ Correction: `sserial_port_0=00000000` does not disable SSerial
>
> It **enables** all 8 channels in mode 0 — and is literally the documented default
> (`hostmot2.9:176`, *"default: 00000000 for all ports"*).
>
> The disable character is a **lowercase `x`**. From `hostmot2.9:183-184`:
>
> > A "0" in the string sets the corresponding channel to mode 0, a "1" to mode 1, and so
> > on up to mode 9. An "x" in any position disables that channel and makes the
> > corresponding FPGA pins available as GPIO.
>
> Confirmed in the parser at `hostmot2.c:490-498`, which accepts **only** `'0'`–`'9'` and
> lowercase `'x'` and **silently ignores everything else** — so the uppercase `0XXX` in
> the examples at `hostmot2.adoc:727,737` parses as just `0` with the X's dropped. That
> upstream inconsistency is a trap; use the man page's lowercase form.
>
> A useful side effect: the parser only raises `num_sserials` above 0 if it saw a *digit*
> (`hostmot2.c:493`, `:505-507`), so an all-`x` string leaves the module uninstantiated
> entirely. There is no `num_sserials=` config parameter — it exists only as an internal
> field — so `sserial_port_0=xxxxxxxx` is the way to do this.
>
> Why bother rather than leaving it alone — `hostmot2.9:188-189`: *"Unconnected channels
> will default to GPIO, but the pin values will vary semi-randomly during boot when
> card-detection runs, so it is best to actively disable any channel that is to be used
> for GPIO."*

Only 2 channels exist on this board; `hostmot2.9:185` says extra mode characters beyond
the channel count are ignored, so 8 `x`'s is harmless.

---

## Assumptions — all 8 resolved (A5 and A6 on 2026-11-12)

```bash
grep -rn 'ASSUMPTION[:]' configs/test-rig/     # prints nothing: no marker is left (2026-11-12)
```

### ✅ A1 — Board IP `10.10.10.10` — RESOLVED

`test-rig.ini:108-112`. Both dumps were taken from this address (`readhmid:1`).

### ✅ A2 — HAL prefix — RESOLVED, and it was WRONG

`test-rig.ini:114-132`, `test-rig.hal:93-101`

**`hm2_7i92.0`**, not `hm2_7i96.0`. Board reports `7I92`, IDROM says `MESA7I92`
(`readhmid:4-6, :12`). `hm2_eth` takes the 7I92 branch at `hm2_eth.c:1183` and builds the
name at `hm2_eth.c:1487`.

Geometry: `num_ioport_connectors = 2`, `pins_per_connector = 17`, connectors named `P2`
and `P1` (`hm2_eth.c:1187-1190`). The IDROM agrees — *"Number of IO Ports: 2"*, *"Width of
one I/O port: 17"* (`readhmid:15-16`) — so the strict checks in `hm2_read_idrom()` pass
and the board loads cleanly on hm2_eth's own `7I92` branch (a 4-character match,
`hm2_eth.c:1183`, not the 7i96 exact-match). That is Outcome A of docs/02, not
the fallback we were braced for.

Fixed in one place: `[HOSTMOT2]BOARD`. The HAL indirects through it, exactly as designed.

### ✅ A3 — Module inventory — RESOLVED

`test-rig.ini:134+`. From `readhmid:25-97`:

| Module | Instances | Clock | IO pins |
|---|---:|---|---|
| StepGen | 6 | 100 MHz | 4–15 |
| QCount (encoder) | 3 | 100 MHz | 25–33 |
| PWM | 1 | **200 MHz** | 16 |
| SSerial | 1 (2 ch) | 100 MHz | 0–3 |
| IOPort | 2 | 100 MHz | — |
| LED | 1 (4 LEDs) | 100 MHz | — |
| WatchDog | 1 | 100 MHz | — |
| DPLL | 1 | 100 MHz | — |

"QCount" is the HostMot2 firmware's name for what the LinuxCNC driver calls "Encoder" —
same module, gtag `0x04` (`hostmot2.h:96`). The config parameter is `num_encoders`.

### ✅ A4 — stepgen → axis mapping — RESOLVED (StepGen 3 confirmed 2026-11-12)

`test-rig.hal:114+`. StepGen instance N = AXIS N connector, from `readhmid:108-119` and
`printpd:29-88`:

```
StepGen 0   IO  4 Step   IO  5 Dir     -> AXIS 0 -> X
StepGen 1   IO  6 Step   IO  7 Dir     -> AXIS 1 -> Y, first motor
StepGen 2   IO  8 Step   IO  9 Dir     -> AXIS 2 -> Z
StepGen 3   IO 10 Step   IO 11 Dir     -> AXIS 3 -> Y, second motor (since 2026-11-12)
StepGen 4-5 IO 12-15                   -> disabled by num_stepgens=4
```

Order is **Step then Dir, ascending** — *not* the Dir-before-Step order docs/03 found in
the stock 7i96 data. One more reason that document does not apply here.

Physically confirmed: jogging drives AXIS 0 = X, AXIS 1 = Y, AXIS 2 = Z
(docs/DURUM.md:115) — the axis blocks are not swapped — and AXIS 3 = StepGen 3: in the
2026-11-12 scale check both Y gantry sides moved about 50 mm for a 50.005 mm command (left
50.3, right 49.7).

**The Y DIR fault is resolved (2026-11-12).** Y has two motors on two TB6560s, and both
hung on AXIS 1. With the two opto inputs in parallel, AXIS 1's DIR high level read 2.43 V
against 3.11 V on X's single-driver AXIS 0 (DIR low 0.2 V), and its STEP idle level 2.55 V
against X's 3.21 V. The board's outputs are 3.3 V logic and the rig is wired
**common-cathode** — driver `CLK−`/`CW−` to GND, the board's `5V` terminal unused, not the
common-anode wiring in docs/09:54-62 — and one 3.3 V output cannot drive two optocouplers.
The second Y driver now has its own output, AXIS 3 = `stepgen.03`, fed the same `[JOINT_1]`
values, command and enable as `stepgen.01` (end of `test-rig.hal`), with `num_stepgens=4`.
Y moves both ways and both step counters stay equal. One joint, two step generators: no
separate homing or squaring, and the two sides' racking has not been checked yet.

### ✅ A8 — FPGA clock — RESOLVED, and our suspicion was misplaced

`test-rig.hal:44-80`, `test-rig.ini` step-timing block

**Clock Low 100 MHz, Clock High 200 MHz** (`readhmid:17-18`). StepGen uses the 100 MHz
clock (`readhmid:68`); PWM is the only module on 200 MHz (`readhmid:77`).

We had feared 33 MHz vs the observed 50.000 MHz crystal. Both were wrong, and the worry
was over-reading the evidence: **the crystal is not the logic clock.** The FPGA
synthesises 100/200 MHz from it via a PLL, which is completely ordinary.

**The nanosecond values did not change** — `steplen` etc. are ns, and the driver multiplies
by the clock to get register counts (`stepgen.c:348, :359, :370, :381`). What changed is
the headroom:

| | steplen 10000 ns | dirsetup 50000 ns | max representable |
|---|---:|---:|---:|
| at 100 MHz (actual) | 1000 counts | 5000 counts | 163.8 µs |
| at 33 MHz (assumed) | 333 counts | 1666 counts | 491.5 µs |

Register limit is `0x3FFF` = 16383. Our largest value uses 31 % of the range, so no
overflow and no clamp warning. Had we needed `dirhold` above ~164 µs, 100 MHz would have
been a real constraint where 33 MHz was not — worth knowing, not an issue here.

**Scope verification is now low priority, not a blocker.** The driver reads the clock from
the IDROM and the IDROM is self-consistent (geometry checks passed, module clocks agree).
Still worth doing eventually — an IDROM can only tell you what the firmware *claims* — but
it is no longer gating anything. Downgraded from blocker B5.

---

### ✅ A5 — Travel limits — RESOLVED 2026-11-12 (phase 4)

`test-rig.ini:383-396` (X), `:481-484` (Y), `:522-530` (Z)

Each axis was jogged onto its switches and the trip points were read in the pre-homing
frame, in which the table sits at about 0 (Z has no bottom switch). The soft limits sit at
least 3.3 mm inside a switch trip or the table:

| Axis | Trips, pre-homing frame | Trip to trip | Machine frame | Soft limits | Usable | Margins |
|---|---|---:|---|---|---:|---|
| X | left −287.93 (3 approaches within 0.03 mm), right +115.43 | 403.4 mm | −0.64 / +402.72 | 3 … 399 | 396 mm | 3.64 / 3.72 mm |
| Y | front −271.82, rear +119.53 | 391.3 mm | −0.67 / +390.68 | 3 … 387 | 384 mm | 3.67 / 3.68 mm |
| Z | top +100.32 (release 99.79), table about 0 | about 100 mm to the table | top +0.52, table about −99.8 | −96.5 … −3 | 93.5 mm | 3.3 (table) / 3.52 (trip) mm |

The machine frame's origin is each home switch's latch **release** edge (`HOME_OFFSET = 0`).
The phase-4 release edges were read by polling and include its latency; homing later showed
only small switch hysteresis (Z trip to release about 0.06 mm), so the margin on the home
switch's side is a little smaller than listed and the far side's a little larger. GRBL's
`$130`–`$132 = 200` were factory defaults (docs/07) and played no part.

Moves beyond the limits are refused (phase 7): MDI `G1 X999`, `X-50`, `Y999`, `Y-50`,
`Z10` and `Z-200` at `F300` were all rejected by motion — *"Linear move on line N would
exceed joint J's positive/negative limit"* plus *"invalid params in linear command"*
(`command.c:261-267`, `:1003`) — and no axis moved. This closes blocker **T1** in docs/07.

Z uses the opposite sign convention: it homes at the top, so its travel is negative.

### ✅ A6 — Which sensor is at which end — RESOLVED (X and Z by hand; Y on 2026-11-12)

`test-rig.ini:429` (X), `:491` (Y), `:538` (Z), `test-rig.hal:255+`

**What is now verified:** IO 17–24 have **no Secondary Tag at all** in the pin descriptors
(`printpd:94-109`). They are not "GPIO until something claims them" — there is no module
that *can* claim them. Permanently GPIO regardless of config string. And the vendor
silkscreen maps straight onto global IO numbers, as docs/08 hypothesised:

| IO | Silkscreen | HAL pin |
|---:|---|---|
| 17 | `CW` | `hm2_7i92.0.gpio.017.out` |
| 18 | `OUT 1` | `hm2_7i92.0.gpio.018.out` |
| 19 | `OUT 2` | `hm2_7i92.0.gpio.019.out` |
| 20–24 | the 5 isolated inputs | `hm2_7i92.0.gpio.0NN.in` |

**Verified by hand** (docs/DURUM.md:69-82): `gpio.020` = X right, `gpio.021` = X left,
`gpio.022` = Y rear, `gpio.023` = Y front, `gpio.024` = Z top; Z bottom has no switch.
**Directions, measured 2026-11-12 (phase 2): X+ = right, Y+ = rear (away from the
operator), Z+ = up.** So the minimum (home) ends are `gpio.021` (X left) and `gpio.023`
(Y front), the maximum ends `gpio.020` (X right) and `gpio.022` (Y rear), and `gpio.024`
is the Z **+** end. The dumps describe the FPGA; the frame side was answered by triggering
each switch by hand and jogging each axis.

**To re-check** (after any rewiring): halmeter, machine powered but **not enabled**, push
each switch by hand:
```bash
halrun -I
halcmd: loadrt hostmot2 debug_modules=1
halcmd: loadrt hm2_eth board_ip="10.10.10.10" config="num_stepgens=4 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx"
halcmd: start
halcmd: loadusr halmeter pin hm2_7i92.0.gpio.020.in
```
Record for each of the five: which switch moves it, and in which direction.

### ✅ A7 — Switch polarity: `.in` vs `.in_not` — RESOLVED: `.in_not`; nets connected 2026-11-12

`test-rig.hal:240-253`

The inputs are opto-isolated. **An optocoupler input stage commonly inverts**, so `.in` may
read TRUE when the switch is *open*. LinuxCNC inverts an input by choosing the negated pin,
not a parameter — every pin has both `.in` and `.in_not` (`ioport.c:250`, `:263`).

**This is why the switch nets were commented out at first.** Wrong polarity means LinuxCNC
believes an axis is permanently on its limit — or, worse, believes it never is. Since
2026-11-12 the nets are connected with `.in_not` (`test-rig.hal:301-327`): `gpio.021` (X
left) → home + negative limit, `gpio.020` (X right) → positive limit, `gpio.023` (Y front)
→ home + negative, `gpio.022` (Y rear) → positive, `gpio.024` (Z top) → home + positive.
Phase 5 pressed each switch by hand with the machine ON, and each tripped
`joint N on limit switch error` for its own joint (N = 0 for both X switches, 1 for both Y
switches, 2 for Z top).

**Resolved (docs/DURUM.md:71), by triggering each switch by hand:** every input reads
`.in` = TRUE at idle and FALSE when pressed, so HAL uses **`.in_not`**. GRBL's `$5 = 0`
(docs/07, T3) supports — does not prove — that these are normally-open switches; if so, a
broken wire reads as "not pressed": the "never on its limit" failure above, which nothing
detects — check the switches before relying on them. `.in_not` is a fact about
*this* rig's switches, not machine-independent: the NC series chain planned for the marble
machine (docs/01-machine-survey.md) would read the opposite way.

---

## Values that are NOT assumptions

| Value | Source |
|---|---|
| `SCALE` X 100.085, Y 98.592, Z 122.274 steps/mm | **Measured on the rig 2026-11-12** (phase 3; the JSON, `faz3_scale`). A rough round, then a fine round — new = previous × commanded / measured: X 99.5 × 49.6902 / 49.4, Y 99.8 × 49.9873 / 50.6, Z 122.0 × 25.2566 / 25.2 — then a verification within 1 %: X 50.026 → 49.8 mm (−0.45 %), Y 50.005 → left 50.3 (+0.59 %) / right 49.7 (−0.61 %), Z 25.035 → 25.2 (+0.66 %). The GRBL EEPROM had `$100`–`$102` = 833.300 / 823.500 / 1250.000 (`docs/board-dumps/grbl-eeprom-2026-08-03.json`, docs/07): about 8.3× (X, Y) and 10.2× (Z) too high — never calibrated for these mechanics. All positive: + is right / rear / up. |
| `MAX_VELOCITY` X/Y 10, Z 5 mm/s; `MAX_ACCELERATION` 50 mm/s² | Chosen 2026-11-12, raised from 3 mm/s and GRBL's factory-default 10 mm/s² (docs/07 §2). With the old, too-high `SCALE` the rig had physically run at about 9 mm/s and about 84 mm/s² all day; a 5 mm/s homing search stops in 0.25 mm at 50 mm/s², against about 1.3 mm of switch overtravel. `[DISPLAY]`/`[TRAJ]` `MAX_LINEAR_VELOCITY` 10, `DEFAULT_LINEAR_VELOCITY` 3. |
| `STEPGEN_MAX_VEL` 12 / 12 / 6, `STEPGEN_MAX_ACC` 60 | 20 % above the joint limits (see the shared note in the INI). |
| `steplen`/`stepspace` 10000 ns, `dirsetup`/`dirhold` 50000 ns | TB6560 datasheet, docs/09. GRBL has no equivalent settings, so these could not have come from the EEPROM. Verified to fit the register range at 100 MHz. Unchanged on 2026-11-12: a runtime test with longer pulses changed nothing. |
| `FERROR = 0.500` / `MIN_FERROR = 0.050` | Ten times what every upstream hm2-stepper sample uses (0.050 / 0.005 — `7i96s.ini:117-118`, identically in `7i93.ini`, `3x20-small.ini`, `4i65.ini`). Raised 2026-11-12: with the measured `SCALE` one step is about 0.01 mm, so the old `MIN_FERROR` of 0.005 was below one step. |
| Soft limits X 3 … 399, Y 3 … 387, Z −96.5 … −3 | Measured, A5 above. |
| Homing: `HOME_SEARCH_VEL` X/Y −5, Z +4; `HOME_LATCH_VEL` ±0.2; `HOME` 5 / 5 / −5; `HOME_OFFSET` 0; `HOME_SEQUENCE` Z 0, Y 1, X 2 | Works since 2026-11-12 (phase 6): Home All runs Z → Y → X. Repeatability, read from `joint.N.motor-offset` because the DRO always reads `HOME` after homing: Z 0.0001 mm, Y 0.030 mm, X 0.0017 mm. |
| `HOME_IGNORE_LIMITS = YES` | Required because home and limit share one switch. `ini-homing.adoc:174`. |

`SCALE` X and Y differ by 1.5 % despite nominally identical mechanics. **Both are
measurements — do not tidy them to match.**

---

## Velocity: why 10 mm/s (Z 5) and not more

`MAX_VELOCITY` was raised from 3 to 10 mm/s (Z 5) on 2026-11-12, once `SCALE` was measured:
under the old, ~8.3× too high `SCALE` the rig had in fact run at about 9 mm/s and about
84 mm/s² all day. The TB6560 is rated **15 kHz** max STEP frequency (docs/09) — a limit of
the *driver board*. With a 20 % margin (12 kHz) and the measured `SCALE`:

| Axis | SCALE | ceiling at 12 kHz | mm/min |
|---|---:|---:|---:|
| X | 100.085 | 119.9 mm/s | 7194 |
| Y | 98.592 | 121.7 mm/s | 7303 |
| Z | 122.274 | 98.1 mm/s | 5888 |

So the step rate no longer limits anything here — 10 mm/s on X is about 1 kHz. Motor torque
and the mechanics do, and nothing above these values has been tried. The 15 kHz figure is
the manufacturer's, independently unverified: find the real ceiling by raising speed until
steps are lost.

Raising a joint's `MAX_VELOCITY` means also raising the matching `[AXIS_x]` (or the axis
limit silently caps it) and `STEPGEN_MAX_VEL` (to stay 20 % above).

**Open (phase 8):** homing motor-offsets after three program runs moved Y 0.0001 mm, X
0.036 mm and Z 0.066 mm — Z drifts one way, about 0.02 mm per run. Suspected cause: Z's
50 mm/s² is too high against gravity. Next: try 25 mm/s² on Z and repeat the runs.

---

## Done on 2026-11-12, and what is left

Phases 1–8 ran on the rig on 2026-11-12, each with a gate that had to pass before the next
(numbers: the JSON):

1. **Y DIR fault** — fixed: Y's second driver on its own output, AXIS 3 (`stepgen.03`); Y
   moves both ways and both step counters stay equal.
2. **Direction map** — X+ = right, Y+ = rear, Z+ = up → closed the Y part of **A6**.
3. **Scale** — measured and verified within 1 % (table above).
4. **Travel** — switch trip points measured, soft limits at least 3.3 mm inside → closed
   **A5**. The Y two-side racking check was deferred.
5. **Switch nets** — connected with `.in_not`; each switch trips its own joint.
6. **Homing** — Z → Y → X works. Repeatability Z 0.0001, Y 0.030, X 0.0017 mm — X only
   after an intermittent X motor-cable contact was repaired; before that X drifted 0.047 /
   0.132 mm in one direction, i.e. lost steps.
7. **Soft limits** — moves past them are refused by motion, and no axis moved.
8. **Dry run** — `nc_files/faz8-kare.ngc`, a 50 mm square at F300 starting at X10 Y10, ran
   four times without an error, hm2 packet errors staying 0. Its Z moves use `G53`: the
   G54 offsets are zero and the Z window is −96.5 … −3, so a `G0 Z5` would be refused.
   Accepted with the Z drift open (above).
9. **Persist** — the measured values are in the INI and the markers are gone; the commit
   and the check that the live-USB persistence survives a reboot remain.

Still open on the rig:

- **Z drift**, about 0.02 mm per run in one direction: try `MAX_ACCELERATION` 25 on Z and
  repeat the runs.
- **X motor cable** — an intermittent contact cost steps until it was repaired; its
  durability is unproven.
- **Y racking** — the two Y sides were never checked against each other.
- `pin_new(hm2_7i92.0.gpio.000.in_not)` and `(gpio.001.in) called with
  already-initialized memory` appear at every LinuxCNC start, good starts included — cause
  unknown, apparently harmless (docs/DURUM.md:156).
- *(Optional)* scope a step pulse → fully closes **A8**.

> **After any rewiring, before homing**, read `ini-homing.adoc:46`: starting homing on the
> wrong side of a home switch, *combined with `HOME_IGNORE_LIMITS`*, leads to a hard crash.
> With limits ignored during homing there is nothing to stop the axis. Know which side of
> each switch you are on.

---

## Status

**Board: VERIFIED. Config: runs, homes and runs programs — measured on the rig in phases
1–8. Machine: directions, scale, travel, switch nets and homing verified. Open: Z drifts
about 0.02 mm per run (try 25 mm/s² on Z), the Y racking check, X motor-cable
durability.** *(2026-11-12)*

| ID | Assumption | Status | Resolved by |
|---|---|---|---|
| A1 | Board IP `10.10.10.10` | ✅ resolved | `readhmid:1` — dumps taken from it |
| A2 | HAL prefix | ✅ resolved — **was wrong**, `hm2_7i92.0` not `hm2_7i96.0` | `readhmid:4-6, :12`; `hm2_eth.c:1183` |
| A3 | Module inventory | ✅ resolved — 6 StepGen, 3 QCount, 1 PWM, 1 SSerial | `readhmid:25-97` |
| A4 | stepgen 00/01/02 → X/Y/Z | ✅ resolved — StepGen N = AXIS N; StepGen 3 = AXIS 3 = Y's second motor (2026-11-12) | `readhmid:108-119`, `printpd:29-88`; both Y sides moved ~50 mm |
| A5 | Travel 150/150/50 mm | ✅ resolved 2026-11-12 — 396 / 384 / 93.5 mm usable | switch trip points, phase 4 (the JSON, `faz4_travel`) |
| A6 | Sensor-to-end mapping | ✅ resolved — X and Z by hand, Y+ = rear on 2026-11-12 | switches triggered by hand, `docs/DURUM.md:69-82`; phase 2 jog directions |
| A7 | Switch polarity | ✅ resolved — idle TRUE, pressed FALSE → `.in_not`; nets connected 2026-11-12 | switches triggered by hand, `docs/DURUM.md:71`; phase 5 |
| A8 | FPGA clock | ✅ resolved — 100 MHz, self-consistent | `readhmid:17-18, :68` |
