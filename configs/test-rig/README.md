# test-rig — Phase 1.5 config

Draft LinuxCNC configuration for the small 3-axis DIY router used to prove the
PC + Zhulong control chain before the marble machine (a Windows laptop booted from the
LinuxCNC 2.9.10 live USB, amd64 PREEMPT_RT kernel — not a Pi; see docs/DURUM.md).

---

## 🟡 Runs and jogs — Y DIR fault, scale, travel, switch nets and homing still to do

**Updated 2026-10-18.** The board has been detected and fully interrogated, and the config
has been run: `hm2_eth` loads cleanly, the machine enables, all three axes jog — Y in one
direction only, an open DIR fault (docs/DURUM.md). Five of the eight original assumptions
were resolved from the dumps and a sixth (A7, polarity) by hand; A6 is resolved for X and Z
but open for Y, and A5 is open — both about the *machine*, not the controller.

| | |
|---|---|
| **Board side** | ✅ **VERIFIED** — prefix, module inventory, stepgen mapping, IO numbers, clock |
| **Machine side** | 🟡 **PARTLY VERIFIED** — all three axes jog (Y one way only — DIR fault open); switch locations and polarity (`.in_not`) verified by hand. Still unverified: scale, travel limits, homing directions, Y's + end |

Dumps this is based on, both in `docs/board-dumps/`:

```
readhmid-10.10.10.10-2026-08-05.txt     IDROM, module inventory, pin-out by connector
printpd-10.10.10.10-2026-08-05.txt      raw pin descriptors, all 34 pins
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

### First run — done

The config has been run (docs/DURUM.md): `hm2_eth` loads cleanly (0 packet errors in a
10 s check), the machine enables, all three axes jog (Y one way only — see above). The motors are
now coupled to the mechanics (docs/PLAN-BUGUN.md:397), so the protections listed below no
longer apply: `SCALE` is unverified, Y's direction is unknown and no limits are wired, and a
wrong value now drives the gantry, not a bare shaft. Until docs/PLAN-BUGUN.md phases 1–4
pass, jog in short, slow moves with a hand on the power switch. The plan was to run it first
with the motors uncoupled, for these reasons:

- A wrong `SCALE` spins a motor, not a gantry into its frame.
- A wrong direction sign is something you *observe* rather than something that breaks a
  switch.
- A stepgen mapped to an unexpected axis is obvious and harmless.

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
| `test-rig.hal` | Wiring. Loads `hostmot2` with `debug_modules=1 debug_idrom=1`. Limit/home switch nets present but **commented out on purpose**. |

### Verification performed

- **107 INI keys** cross-checked against the reference docs — all documented or a known
  HAL-consumed custom variable.
- **57 HAL pin names** cross-checked against the LinuxCNC source tree — all located.
- **30 INI variable references** in the HAL — all resolve.
- **Board facts** cross-checked against both dumps and against `hm2_eth.c:1183-1192`.

Internally consistent and correct about the board, and it runs: loads, enables, jogs all
three axes — Y one way only (docs/DURUM.md). **Scale, travel, switch nets and homing are
not yet proven.**

---

## The config string, and one correction to it

```
CONFIG = "num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx"
```

What each does and which IO pins it releases to plain GPIO — a pin whose owning module is
not instantiated falls back to full GPIO (`hostmot2.adoc:400-401`):

| Setting | Effect | Frees |
|---|---|---:|
| `num_stepgens=3` | keeps StepGen 0–2 (IO 4–9), drops 3–5 | IO 10–15 (6) |
| `num_encoders=0` | drops all 3 QCount instances | IO 25–33 (9) |
| `num_pwmgens=0` | drops the single PWM instance | IO 16 (1) |
| `sserial_port_0=xxxxxxxx` | disables all SSerial channels | IO 0–3 (4) |

20 pins freed, plus the 8 permanently-GPIO pins (IO 17–24) = **28 GPIO available**, 6
consumed by the three stepgens. 6 + 28 = 34. ✓

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

## Assumptions — 6 resolved, 1 partly open, 1 open

```bash
grep -rn 'ASSUMPTION:' configs/test-rig/     # 7 markers (+3 lines quoting this grep): 3 travel (A5), 3 homing directions (stay until homing is proven), 1 HAL switch mapping (PLAN-BUGUN phase 5)
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

### ✅ A4 — stepgen → axis mapping — RESOLVED

`test-rig.hal:114+`. StepGen instance N = AXIS N connector, from `readhmid:108-119` and
`printpd:29-88`:

```
StepGen 0   IO  4 Step   IO  5 Dir     -> AXIS 0 -> X
StepGen 1   IO  6 Step   IO  7 Dir     -> AXIS 1 -> Y
StepGen 2   IO  8 Step   IO  9 Dir     -> AXIS 2 -> Z
StepGen 3-5 IO 10-15                   -> disabled by num_stepgens=3
```

Order is **Step then Dir, ascending** — *not* the Dir-before-Step order docs/03 found in
the stock 7i96 data. One more reason that document does not apply here.

Physically confirmed since: jogging drives AXIS 0 = X, AXIS 1 = Y, AXIS 2 = Z
(docs/DURUM.md:115, docs/PLAN-BUGUN.md:82) — the axis blocks are not swapped. The exception is
Y's direction, which does not change — Y+ and Y− move the same way (active DIR fault,
docs/DURUM.md:12); where the DIR path fails — board terminal, cable or driver — is not yet
measured (docs/PLAN-BUGUN.md phase 1).

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

### ❌ A5 — Travel limits: X 150 mm, Y 150 mm, Z 50 mm — OPEN

`test-rig.ini:384` (X), `:481` (Y), `:523` (Z)

Unchanged by the dumps — this is a property of the frame. GRBL's `$130`–`$132` were all
still at the factory default of 200 mm while the same EEPROM's `SCALE` values had clearly
been calibrated (docs/07), so they prove nothing. The values here are deliberate
under-estimates so a wrong guess stops an axis *short* of the frame.

Z uses the opposite sign convention: homes at the top, so travel is negative
(`MIN_LIMIT = -50`, `MAX_LIMIT = 0`).

**Resolves it:** measure. Widen the soft limits, jog each axis slowly end to end, read the
travel with a rule, then set these to measured-minus-margin. Blocker **T1** in docs/07.

### 🟡 A6 — Which sensor is at which end — RESOLVED for X and Z by hand; Y's + end waits on the Y DIR fault

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

**Verified by hand since** (docs/DURUM.md:69-82): `gpio.020` = X right, `gpio.021` = X left,
`gpio.022` = Y rear, `gpio.023` = Y front, `gpio.024` = Z top; Z bottom has no switch. With
X+ = right and Z+ = up, `gpio.020` is the X **maximum** end — not the home/minimum end the
commented-out HAL plan gives it (docs/PLAN-BUGUN.md, "X limit ataması ters") — and
`gpio.024` is the Z **+** end. Still unknown: which of Y rear/front is the + end, until Y's
DIR fault is fixed (docs/PLAN-BUGUN.md phases 1–2). The dumps describe the FPGA; the frame
side was answered by triggering each switch by hand.

**To re-check** (after any rewiring): halmeter, machine powered but **not enabled**, push
each switch by hand:
```bash
halrun -I
halcmd: loadrt hostmot2 debug_modules=1
halcmd: loadrt hm2_eth board_ip="10.10.10.10" config="num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx"
halcmd: start
halcmd: loadusr halmeter pin hm2_7i92.0.gpio.020.in
```
Record for each of the five: which switch moves it, and in which direction.

### ✅ A7 — Switch polarity: `.in` vs `.in_not` — RESOLVED: `.in_not`

`test-rig.hal:240-253`

The inputs are opto-isolated. **An optocoupler input stage commonly inverts**, so `.in` may
read TRUE when the switch is *open*. LinuxCNC inverts an input by choosing the negated pin,
not a parameter — every pin has both `.in` and `.in_not` (`ioport.c:250`, `:263`).

**This is why the switch nets were commented out at first.** Wrong polarity means LinuxCNC
believes an axis is permanently on its limit — or, worse, believes it never is. Polarity is
now known; the nets stay commented out until docs/PLAN-BUGUN.md phase 5, because Y's + end
is unknown and the planned X mapping in the HAL is backwards (`gpio.020` is the X maximum
end).

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
| `SCALE` X 833.300, Y 823.500, Z 1250.000 | The machine's own GRBL EEPROM, empirically calibrated under GRBL. `docs/board-dumps/grbl-eeprom-2026-08-03.json`, analysed in docs/07. All positive because `$3 = 0`. **Not yet verified under LinuxCNC + TB6560** — the microstep setting may differ from the original drivers' (docs/DURUM.md:88, docs/09 T5); checked in docs/PLAN-BUGUN.md phase 3. |
| `MAX_ACCELERATION = 10.0` | GRBL `$120`–`$122 = 10` — an untouched GRBL **factory default**, not a tuned value (docs/07 §2, docs/DURUM.md:94-96). Same units in both systems, no conversion (docs/06 Trap 4). Known to work only in the sense that the machine has run with it; its real capacity is unmeasured. |
| `steplen`/`stepspace` 10000 ns, `dirsetup`/`dirhold` 50000 ns | TB6560 datasheet, docs/09. GRBL has no equivalent settings, so these could not have come from the EEPROM. Verified to fit the register range at 100 MHz. |
| `FERROR = 0.050` / `MIN_FERROR = 0.005` | What every upstream hm2-stepper sample uses — `7i96s.ini:117-118`, identically in `7i93.ini`, `3x20-small.ini`, `4i65.ini`. |
| `HOME_IGNORE_LIMITS = YES` | Required because home and limit share one switch. `ini-homing.adoc:174`. |

`SCALE` X and Y differ by 1.2 % despite nominally identical mechanics. **That is what an
honest empirical calibration looks like — do not tidy them to match.**

---

## Velocity: why 3 mm/s and not 14

`MAX_VELOCITY = 3.0` everywhere is a first-run figure, not a hardware limit. From docs/09,
the TB6560 is rated **15 kHz** max STEP frequency — a limit of the *driver board*. With a
20 % margin (12 kHz):

| Axis | SCALE | ceiling at 12 kHz | mm/min |
|---|---:|---:|---:|
| X | 833.3 | 14.4 mm/s | 864 |
| Y | 823.5 | 14.6 mm/s | 874 |
| Z | 1250.0 | 9.6 mm/s | 576 |

The machine ran at 500 mm/min under GRBL, so headroom is modest and Z barely improves. The
lever that helps is **reducing microstepping** — 1/16 → 1/8 halves `SCALE` and doubles the
ceiling, but requires recalibrating `SCALE`. And the 15 kHz figure is the manufacturer's,
independently unverified: find the real ceiling by raising speed until steps are lost.

Raising a joint's `MAX_VELOCITY` means also raising the matching `[AXIS_x]` (or the axis
limit silently caps it) and `STEPGEN_MAX_VEL` (to stay ~20 % above).

---

## Suggested order of work

*Updated 2026-10-18.* Board-side steps are done, and so are the hand switch map (A6 for
X/Z, A7) and the first jog of all three axes (docs/DURUM.md). The remaining work follows
**[docs/PLAN-BUGUN.md](../../docs/PLAN-BUGUN.md)**, phases 1–9; each phase has a gate that
must pass before the next:

1. **Y DIR fault** — Y+ and Y− move the same way; find where the DIR signal is lost
   (board terminal, cable or driver).
2. **Direction map** — once Y reverses, jog it and record which way is Y+ → closes the Y
   part of **A6**.
3. **Scale check** — jog a known distance and measure; `SCALE` came from GRBL and is not
   yet verified under LinuxCNC/TB6560.
4. **Travel** — very slowly, hand on the power switch. No switch is wired, and the soft
   limits do not protect an unhomed axis: until homing, LinuxCNC only bounds a jog to ±(full
   range) around the current position (`command.c:153-174`), so you stop the axis yourself.
   Jog X and Y until they touch each switch, Z down from the top switch only until close
   to the mechanical stop (no bottom switch); leave 2–3 mm margin → resolves **A5**.
5. **Switch nets** — uncomment them with `.in_not`, X mapping corrected (`gpio.021` → home +
   negative limit, `gpio.020` → positive limit), Y per step 2.
6. **Homing** — first attempt; it has never been tried.
7. **Soft limits** — prove that a move beyond them is refused.
8. **Dry run** — a first G-code program end to end, Z above the work.
9. **Persist** — write the measured values into the INI and drop their markers, commit,
   and check that the live-USB persistence survives a reboot.

*(Optional, no longer blocking)* Scope a step pulse → fully closes **A8**.

> **Before that first homing attempt**, read `ini-homing.adoc:46`: starting homing on the
> wrong side of a home switch, *combined with `HOME_IGNORE_LIMITS`*, leads to a hard crash.
> With limits ignored during homing there is nothing to stop the axis. Know which side of
> each switch you are on.

---

## Status

**Board: VERIFIED. Config: runs — loads cleanly, enables, all three axes jog. Machine:
partly verified — switch map and polarity by hand. Open: Y DIR fault (Y moves one way
only); scale, travel, switch nets and homing not yet done.** *(2026-10-18)*

| ID | Assumption | Status | Resolved by |
|---|---|---|---|
| A1 | Board IP `10.10.10.10` | ✅ resolved | `readhmid:1` — dumps taken from it |
| A2 | HAL prefix | ✅ resolved — **was wrong**, `hm2_7i92.0` not `hm2_7i96.0` | `readhmid:4-6, :12`; `hm2_eth.c:1183` |
| A3 | Module inventory | ✅ resolved — 6 StepGen, 3 QCount, 1 PWM, 1 SSerial | `readhmid:25-97` |
| A4 | stepgen 00/01/02 → X/Y/Z | ✅ resolved — StepGen N = AXIS N | `readhmid:108-119`, `printpd:29-88` |
| A5 | Travel 150/150/50 mm | ❌ open | measure with a rule |
| A6 | Sensor-to-end mapping | 🟡 X and Z resolved by hand; Y's + end waits on the Y DIR fault | switches triggered by hand + jog directions, `docs/DURUM.md:69-82` |
| A7 | Switch polarity | ✅ resolved — idle TRUE, pressed FALSE → `.in_not` | switches triggered by hand, `docs/DURUM.md:71` |
| A8 | FPGA clock | ✅ resolved — 100 MHz, self-consistent | `readhmid:17-18, :68` |
