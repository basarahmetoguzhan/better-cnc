# better-cnc — documentation index

Retrofit of a 3-axis industrial marble CNC from a Windows controller to
LinuxCNC on a PC — today an amd64 laptop booted from a LinuxCNC 2.9.10 PREEMPT_RT live USB
(a Raspberry Pi 4/5 remains a possible future path; the Pi 3 was ruled out) — driving a
**Zhulong V2.0** Ethernet motion board
(a Mesa **7i92** derivative — Spartan-6 XC6SLX9, Micrel KSZ8851 PHY, `hm2_eth`,
34 IO pins, HAL prefix `hm2_7i92.0`).

**Start here if you have been away from this project.** Read [DURUM.md](DURUM.md) first
(where we are), then this file, then [10](10-7i92-pinout-verified.md) for what our board
actually is, then [PLAN-BUGUN.md](PLAN-BUGUN.md) for what is left to do on the test rig
(phases 1–9, each with a gate).

---

## Status legend

| Tag | Meaning |
|---|---|
| **DERIVED** | Written from LinuxCNC source reading and/or photographs. Cited, internally consistent — but **never executed or measured against real hardware.** |
| **VERIFIED** | Confirmed against the physical board or a running system. |

> ## ⚠️ The BOARD is verified. The MACHINE only partly.
>
> **Updated 2026-08-05.** The board has been powered, detected and fully interrogated —
> dumps are in [board-dumps/](board-dumps/). Pin map, module inventory, HAL prefix and
> clock are now **measured facts**, recorded in [10](10-7i92-pinout-verified.md).
>
> On the **test rig**, as of 2026-08-05: the config runs, all three axes jog (X+ = right,
> Z+ = up), and every switch's position and polarity were verified by hand (idle `.in` = TRUE →
> the rig's HAL uses `.in_not` — a fact about this rig's switches, not the board: the NC chain
> planned for the marble machine reads the opposite way; Z has no bottom switch). See [DURUM.md](DURUM.md). Still DERIVED or
> unknown: travel limits, scale accuracy, Y direction (an active DIR fault) and therefore which
> Y switch is the + end, real step timing, homing (never attempted).
>
> ### 🛑 And the headline correction: the board is a **7i92**, not a 7i96.
>
> Every document written before 2026-08-05 assumed a Mesa **7i96** derivative. That was
> **wrong**. The board reports `7I92`; its IDROM says `MESA7I92`. It has **34 IO pins
> (2 × 17)**, not 51, and the HAL prefix is **`hm2_7i92.0`**. Docs 03, 04 and 08 are
> corrected or superseded — see their banners and the status column below.

---

## The documents

| # | Document | What it is | Status |
|---|---|---|---|
| — | [README.md](README.md) | This index. | **DERIVED** |
| — | [DURUM.md](DURUM.md) | **Session state — read this first.** Working environment, verified board facts, the hand-verified switch map, completed and next steps. In Turkish. Machine state as of 2026-08-05 (doc corrections and the Zhulong decision 2026-10-18); its next-steps list defers to [PLAN-BUGUN.md](PLAN-BUGUN.md). | **PARTLY VERIFIED** — board facts, switch map and X/Z jog directions were checked on the test rig; `SCALE` (calibrated under GRBL) is not yet verified on the TB6560, and its next steps are a plan |
| — | [PLAN-BUGUN.md](PLAN-BUGUN.md) | **Test-rig completion plan.** Nine phases from the Y DIR fault to a repeatable program run and a persisted config, each with a gate; records the X home-switch error. In Turkish. | **DERIVED** (plan) |
| — | [MERMER-GECIS.md](MERMER-GECIS.md) | **Marble-machine transition list.** Purchases, modules disabled on the test rig, config changes, input budget, safety rules. In Turkish. | **DERIVED** |
| 00 | [00-upstream-reference-map.md](00-upstream-reference-map.md) | Reconnaissance of the read-only clones in `reference/`: LinuxCNC 2.9.10 and hostmot2-firmware. Where the 7i96 driver support lives, the exact HAL pin/parameter naming for stepgen/encoder/pwmgen, how `hm2_eth` discovers a board, pncconf's board list, and where the authoritative HAL/INI/hostmot2 docs are for offline grep. **Its claims about the LinuxCNC source are solid and cited; its 7i96 module counts describe Mesa's board, not ours.** | **DERIVED** |
| 01 | [01-machine-survey.md](01-machine-survey.md) | Fill-in inventory checklist for the existing machine — motors, drives, switches, spindle/VFD, coolant, E-stop chain, and the old controller's settings. In Turkish. **Blank — this is a form to complete at the machine, and the settings on the old controller are unrecoverable once it is removed.** | **DERIVED** (empty) |
| 02 | [02-board-bringup.md](02-board-bringup.md) | **The field runbook.** Pre-power checks, Pi network setup, running the detection script, the seven questions the `readhmid` dump must answer, the A/B/C decision tree for board identification, empirical step-timing verification, and the do-not-do list. **Outcome A was confirmed on 2026-08-05**, but the `readhmid` dump was taken manually from the laptop with `mesaflash --device 7i92` ([board-dumps/](board-dumps/) readhmid:1-3), not via its Pi setup or the detection script. Step 7 (scope a step pulse) is still open. Its 2026-10-18 status note records the 7i92 correction and the laptop host; the procedure body (7i96 decision tree, Pi setup) is kept as history — wire from [10](10-7i92-pinout-verified.md). | **PARTLY VERIFIED** (Outcome A) |
| 03 | [03-7i96-pinout.md](03-7i96-pinout.md) | All 51 IO pins of the **stock Mesa 7i96** — **⚠ SUPERSEDED, WRONG BOARD FAMILY.** Our board is a 7i92. Kept because its analysis of *driver behaviour* is still correct and useful: how the board-name string becomes the HAL prefix, how `port_num = i / port_width` maps IO to connectors, the `ioport_connector_name[]` ordering bug, and the two `hm2_print_pin_usage()` forms you need to read a dump. The pin table itself does not apply. | ⚠️ **SUPERSEDED** by [10](10-7i92-pinout-verified.md) |
| 04 | [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) | **Our board, from photographs.** Connector layout by edge, inventory with OBSERVED/INFERRED/UNVERIFIED tags, the 24 V input analysis from the 4.7 kΩ/510 Ω resistors. Its IO-numbering hypothesis is now **CONFIRMED**. Its I/O budget assumed 51 pins and is **wrong** — use [10](10-7i92-pinout-verified.md). Photograph observations stand. | **PARTLY SUPERSEDED** |
| 05 | [05-pi-setup.md](05-pi-setup.md) | **The controller platform runbook.** PREEMPT_RT + `linuxcnc-uspace` (the only option for Mesa Ethernet cards), latency validation and why our jitter budget is ~40× looser than a parallel-port machine's, network configuration for `hm2_eth`, a pre-flight checklist, and capturing the stepgen `clock_frequency`. **Corrected 2026-08-23:** we run **amd64** on a laptop live USB, not arm64 on a Pi — the Pi 3 was ruled out because its Ethernet is behind USB. The arm64/Pi material is kept as the future-Pi path: **the final controller is a Raspberry Pi 4** (decided 2026-10-19). | **PARTLY VERIFIED** |
| 06 | [06-grbl-to-linuxcnc.md](06-grbl-to-linuxcnc.md) | **GRBL → LinuxCNC settings translation**, for Phase 1.5. Every GRBL 1.1 setting `$0`–`$132` with its LinuxCNC equivalent, units and conversion; the five conversion traps (÷60 velocities, ×1000 step pulse, acceleration unchanged); what has no GRBL source and must be decided by hand; the conceptual gaps that are not settings (joints vs axes, shared limit/home switches, AVR vs FPGA step generation); and how to capture `$$` and `$I`. **LinuxCNC side verified and cited; GRBL side cannot be — `reference/` contains no GRBL material.** | **DERIVED**, partly exercised — the test-rig config built from it loads, enables and jogs ([DURUM.md](DURUM.md)) |
| 07 | [07-test-machine-grbl-analysis.md](07-test-machine-grbl-analysis.md) | Analysis of the test router's GRBL EEPROM dump — the `SCALE` values we actually use (calibrated under GRBL with the old driver; not yet verified on the TB6560 — [PLAN-BUGUN.md](PLAN-BUGUN.md) phase 3), and which `$` settings were still untouched factory defaults (notably `$130`–`$132` travel). In Turkish. | **DERIVED** |
| 08 | [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md) | Board pinout from the vendor's own annotated diagram — connectors, terminals, silkscreen. In Turkish. **Corrected 2026-08-05**: the 7i96 comparison table was the wrong family and is now a 7i92 comparison. Its connector observations were right and cross-check cleanly against the dumps. | **VERIFIED** (connectors) |
| 09 | [09-tb6560-drivers.md](09-tb6560-drivers.md) | TB6560 stepper driver analysis — where `steplen`/`stepspace`/`dirsetup`/`dirhold` come from, and the 15 kHz STEP-frequency ceiling that caps axis velocity. In Turkish. | **DERIVED** |
| **10** | [**10-7i92-pinout-verified.md**](10-7i92-pinout-verified.md) | ⭐ **The real pin map.** All 34 IO pins from `mesaflash --readhmid` and `--print-pd`: IO number, port (P2 = 0–16, P1 = 17–33), secondary function, vendor terminal, HAL pin name. Plus IDROM summary, module inventory, the GPIO budget (28 of 34 available), and a full account of the 7i96→7i92 correction. **Wire from this document.** | ✅ **VERIFIED** |
| **11** | [11-ui-implementation.md](11-ui-implementation.md) | **Operator UI implementation.** Stack choice (Python stdlib + server-side HTML) and why; the data-driven capability model that renders absent features disabled rather than hidden; the LinuxCNC data boundary; what is mocked vs real; and a table of all **24 prototype facts corrected** with citations. Code in [ui/](../ui/), 808 tests, 99.5% coverage (2026-10-20). | **DERIVED**, run against a real LinuxCNC 2.9.9 in [sim/](../sim/README.md), not yet on the machine |
| — | [../sim/README.md](../sim/README.md) | **The simulator.** Real LinuxCNC 2.9.9 in Docker on the Mac, arm64 like the Pi 4; the rig's own INI with the board, steps and switches simulated. The UI and the kiosk are tried there before the machine. In Turkish. | runs (2026-10-19) |
| — | [../deploy/README.md](../deploy/README.md) | **Kiosk and remote access.** Power on → LinuxCNC → our UI, full screen, no way out to a desktop; the machine reached from the Mac over SSH (key only) instead of USB. In Turkish. | tried in [sim/](../sim/README.md); not yet installed on the machine |
| — | [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) | **Blocker register** — B1–B7, three now closed (B2, B3, B4) and B5 downgraded to non-blocking, each with who answers it, what it blocks, and space for the dated answer. Answers are struck through, never deleted, so the decision history survives. In Turkish. Organised by *what it blocks*; [04](04-zhulong-board-hardware.md)'s Q-list covers overlapping ground organised by *how `readhmid` settles it*. | **DERIVED** |
| — | [board-dumps/](board-dumps/) | **The evidence.** `readhmid` and `print-pd` from the board (2026-08-05), plus the test machine's GRBL EEPROM and gSender settings (2026-08-03). Never edited — these are the primary sources every VERIFIED claim cites. | ✅ **raw data** |

### The blockers, in one line each

From [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) — 🔴 blocks the next phase, 🟡 blocks wiring or commissioning:

| | Question | Status | Answered by |
|---|---|---|---|
| 🟡 **B1** | How many motors on Y? | open — **downgraded**, no longer a design constraint | Opening the cabinet and counting |
| ✅ **B2** | Can the encoder pins be used as GPIO? | **CLOSED — YES** on the FPGA side, 9 extra inputs (terminal front-end not yet checked with a meter) | `print-pd` (2026-08-05) |
| ✅ **B3** | Are the AXIS blocks differential or single-ended? | **CLOSED** — single-ended, `GND STEP DIR 5V` | vendor diagram |
| ✅ **B4** | Do the drives expect 5 V or 24 V signalling? | **CLOSED** for the test rig — TB6560 | driver markings |
| 🔵 **B5** | Is the declared clock frequency real? | **downgraded** — 100 MHz, IDROM self-consistent, non-blocking | scope, eventually |
| 🟡 **B6** | Do the isolated inputs take a 24 V sensor directly? | open | optocoupler P/N + meter |
| 🔴 **B7** | Second, isolated 24 V supply | open | procurement decision |

**The input-budget crisis is over** — on the FPGA side. B2 closed YES: `num_encoders=0` releases IO 25–33,
so we have **14 inputs** rather than 5; the nine at the encoder terminals sit behind an
unverified front-end, so check them with an ohmmeter before wiring any 24 V device to them. A dual-motor gantry Y needs 6 — it fits with room
to spare, and StepGen 3 is available at IO 10/11 for the fourth motor without any firmware
change. B1 still needs answering (it decides joint count and `trivkins coordinates=`), but
a wrong guess no longer forces a redesign — hence 🔴 → 🟡.

**B7 is now the only red in the register.** It is a procurement question, not a discovery
question. (The Y DIR fault is an active test-rig hardware fault, tracked in
[DURUM.md](DURUM.md) and [PLAN-BUGUN.md](PLAN-BUGUN.md) phase 1, not here.)

## Scripts

| Script | What it does |
|---|---|
| [../scripts/detect-board.sh](../scripts/detect-board.sh) | Finds the board and archives `--readhmid` and `--info` into `docs/board-dumps/`. **Strictly read-only** — never writes flash. Sweeps four candidate IPs, or takes one as an argument. Exits 0 found / 1 not found (with a hint list) / 2 usage or missing mesaflash. Tested against stubs; never run against real hardware. |
| [../scripts/grbl2ini.py](../scripts/grbl2ini.py) | Converts a GRBL `$$` dump into draft `[JOINT_n]`/`[AXIS_x]` INI fragments, with the arithmetic in a comment on every converted line. **Never emits a value it did not derive from the input** — anything undeterminable becomes an explicit TODO, summarised at the top and bottom of the output. Pure stdlib. `--joints N` (default 3). Reads stdin or a file. Exits 1 if no GRBL settings found, 2 on usage/IO error. |
| [../scripts/test_grbl2ini.py](../scripts/test_grbl2ini.py) | 49 unit tests for the converter — parsing, comment/whitespace tolerance, malformed lines, missing settings, and each unit conversion asserted numerically (including guards against the two plausible *wrong* answers for each). **All passing.** Run: `python3 scripts/test_grbl2ini.py`. |

---

## The findings that shape everything

If you remember nothing else from these documents, remember these.

**1. The board is a Mesa 7i92 — and we spent five documents believing it was a 7i96.**
It reports `7I92` over LBP16; its IDROM says `MESA7I92`; `mesaflash --device 7i96` fails
with *"no 7I96 board found"*. **34 IO pins (2 × 17)**, connectors **P2** and **P1**, HAL
prefix **`hm2_7i92.0`**, driver branch `hm2_eth.c:1183`.

What misled us: the FPGA. Both the 7i92 and 7i96 use a Spartan-6 XC6SLX9 in a 144-pin
package, and we reasoned from the chip plus the six AXIS connectors. Same chip, different
board.

The consolation is that the *mechanism* we documented was right — the board-name string
determines the HAL prefix, and reading it off the hardware rather than assuming is exactly
what caught this. The config needed **one** edit (`[HOSTMOT2]BOARD`) because the HAL
indirects through it. Full account in [10](10-7i92-pinout-verified.md).

**2. It is a genuine 7i92, not a clone pretending to be something.**
The IDROM's declared geometry matches what the driver hardcodes for a 7i92 exactly, so
`hm2_read_idrom()`'s strict checks pass and the board loads on the **clean exact-match
path** — Outcome A of [02](02-board-bringup.md), not the `"??"` fallback we were braced
for, and not the hard abort we feared. The 7i92 is a bare 2 × DB25 breakout with no fixed
module complement; the vendor supplied their own bitfile (6 StepGen, 3 QCount, 1 PWM,
1 SSerial) and their own I/O front-end. That is what a 7i92 is *for*. Still: do not flash
any stock Mesa bitfile — the pin assignments would not match the vendor's opto-isolators
and analog converter.

**3. 28 of the 34 IO pins are available as GPIO, which retired the input-budget crisis.**
IO 17–24 have **no secondary tag at all** in the pin descriptors — no module in this
firmware *can* claim them, so they are permanently GPIO whatever the config says. On top of
that, `num_encoders=0` releases IO 25–33, `num_stepgens=3` releases IO 10–15, and
`sserial_port_0=xxxxxxxx` releases IO 0–3. The marble machine's worst case (dual-motor
gantry Y) needs 6 inputs; we have 14. See [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B2.

The caveat: FPGA availability says nothing about the circuit behind the terminal. Ring the
encoder terminals out with a meter before trusting them as 24 V inputs.

**4. Step timing is still scaled by a clock the driver takes on faith — but the panic was
misplaced.**
`steplen`, `stepspace`, `dirsetup` and `dirhold` are all multiplied by the IDROM's declared
`clock_frequency`, nothing measures the real clock, and the HAL parameter reads back as
whatever you typed. That mechanism is unchanged and worth knowing.

But the clock is **100 MHz** (ClockLow; PWM alone runs on the 200 MHz ClockHigh), not the
33 MHz we assumed from the 7i96. And the **50.000 MHz crystal we treated as suspicious was
never evidence** — the FPGA synthesises 100/200 MHz from it via a PLL, which is entirely
ordinary. We over-read a photograph. Our ns values are unchanged and use 31 % of the
register range. Scope verification is now good hygiene rather than a blocker —
[OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B5, downgraded.

---

## Where we are, and what comes next

**Done:** upstream reconnaissance, the bring-up runbook, the detection script, the controller
platform runbook, GRBL translation tooling, the test router's GRBL analysis — and, as of
2026-08-05, **the board is powered, detected and fully interrogated**, and the config at
[configs/test-rig/](../configs/test-rig/) **runs**: `hm2_eth` loads cleanly, the machine
enables, all three stepgens are active, **all three axes jog** (X+ = right, Z+ = up),
0 packet errors in 10 s, and every switch's position and polarity were mapped by hand
([DURUM.md](DURUM.md)).

**Not done:** the Y axis DIR fault — Y+ and Y− move the same way, so Y's direction and
which Y switch is the + end are unknown; where the fault sits (board terminal, cable or
driver) is not yet measured. The switch nets are still commented out in the HAL, and the
commented plan puts X home on the wrong end: with X homing toward its minimum end
(`HOME_SEARCH_VEL = -2.0`, still an ASSUMPTION in the INI), X home + negative limit must be
`gpio.021` (left, minimum end), and `gpio.020` (right, maximum end) is the positive limit.
Scale is not verified (`SCALE` came from GRBL; the TB6560 microstep setting may differ).
Travel is not measured, so 150/150/50 mm is still an ASSUMPTION. Homing has never been
attempted and no program has been run. No hardware e-stop is wired on the test rig — keep
a hand on the power switch. All of this is tracked phase by phase in
[PLAN-BUGUN.md](PLAN-BUGUN.md).

### Phase 1.5 — prove the chain on the small router first

**Plan change.** Before the marble machine, we retrofit a small existing Arduino +
CNC-shield router: replace the Arduino with a LinuxCNC PC (an amd64 laptop on a LinuxCNC
2.9.10 live USB) + Zhulong; the Zhulong's AXIS blocks drive TB6560 stepper drivers directly
([09](09-tb6560-drivers.md); B3, B4).

**Why this is worth a detour:** it separates the two things that can go wrong. Debugging a
new controller chain *and* unfamiliar 2-tonne mechanics simultaneously is how projects
stall. After Phase 1.5, **the marble machine's main unknown is its own mechanics** — the
controller PC, the board, the `hm2_eth` driver and the HAL structure will all be proven
(step timing only for the TB6560; the marble machine's drives are still B4).

It is also the cheap place to make mistakes. A wrong `SCALE` on the test router costs a
scrapped bit; on the marble machine it costs a gantry.

Scope limits for Phase 1.5: **no spindle control of any kind** (the test machine has
none), so nothing exercises PWM or the 0–10 V output. That work waits for the marble
machine.

Known unknowns about the test machine, all since settled: the firmware is GRBL 1.1,
identified from the default-value fingerprint of its `$$` settings (gSender Firmware Tool export, captured 2026-08-03,
[07](07-test-machine-grbl-analysis.md), [board-dumps/](board-dumps/)); the drivers are
TB6560 ([09](09-tb6560-drivers.md), B4); and GRBL had hard limits off (`$21 = 0`) and
homing on (`$22 = 1`), so the switches were used for homing only
([07](07-test-machine-grbl-analysis.md)).

### Order of work

**Done (2026-08-05):** ~~capture `$$`~~ · ~~photograph the shield~~ · ~~run grbl2ini~~ ·
~~prepare the controller~~ (Pi 3 ruled out because its Ethernet is behind USB; LinuxCNC
2.9.10 live USB on an amd64 laptop instead) · ~~configure the network~~ ·
~~power the board and detect it~~ · ~~resolve Outcome A/B/C~~ (it was **A**) ·
~~write the first draft config~~ · ~~run it and command each stepgen~~ (all three axes jog
through `AXIS n → stepgen.0n`; STEP confirmed on all three, DIR on X and Z only) ·
~~map the switches by hand~~ (**A7** resolved: idle `.in` = TRUE, so `.in_not`; **A6**
resolved for X — `gpio.020` right = maximum end, `gpio.021` left = minimum end — and for Z —
`gpio.024` top, no bottom switch; for Y, which of rear/front is the + end waits for the
DIR fix)

Remaining — *list replaced 2026-10-18 with the [PLAN-BUGUN.md](PLAN-BUGUN.md) phase order,
which the to-do list in [configs/test-rig/README.md](../configs/test-rig/README.md) now mirrors.*
Every phase has a gate there; do not start the next phase before the gate passes. The test
rig must pass all nine gates before the marble machine (decision 2026-10-18).

1. **Y DIR fault** → Y does not change direction (Y+ and Y− move the same way). Compare
   the AXIS 1 wiring with X, then meter AXIS 1 `DIR` ↔ `GND` while jogging Y+/Y− to find
   where it breaks — board terminal, cable or driver. Both jogs drive Y toward the front
   and no limit is wired: start with Y near the rear, keep each jog short, and keep a hand
   on the power switch. Gate: Y+ and Y− move in opposite directions.
2. **Direction map** → record which way Y+ goes, and so which Y switch is at its + end.
   X+ = right and Z+ = up are already known. If an axis's + direction must be reversed,
   negate its `SCALE` here (not by rewiring) and redo this map — negating it later swaps
   which switch is the + end and the sign of every measured position.
3. **Scale check** → jog a measured distance on each axis. `SCALE` came from the GRBL
   EEPROM, calibrated under the old driver; the TB6560 microstep setting may differ. Gate:
   commanded and measured distance agree within 1 % on all three axes.
4. **Measure travel** → resolves **A5**. Very slowly, hand on the power switch. No switch
   is wired, and the soft limits do not protect an unhomed axis (LinuxCNC only bounds the
   jog to ±(full range) around the current position, `command.c:153-174`), so you stop it:
   jog X and Y until they touch each switch, Z down from the top switch
   only until close to the mechanical stop (there is no bottom switch). Replace the assumed
   150/150/50 mm in the INI, leaving 2–3 mm margin.
5. **Uncomment the switch nets** in `test-rig.hal` with `.in_not`, but **not** in their
   commented form. With X homing toward its minimum end (an INI assumption), X home +
   negative limit is `gpio.021` (left, minimum end), and `gpio.020` (right, maximum end) is
   X's positive limit. The Y nets follow phase 2. Test first with no motion: each switch reads
   TRUE when pressed. Then, slowly and with a hand on the power switch, check that a pressed
   limit blocks a jog in its direction.
6. **Home**. The motors are already coupled to the mechanics ([PLAN-BUGUN.md](PLAN-BUGUN.md), last section). Read
   `ini-homing.adoc:46` first: home and limit share a switch, so `HOME_IGNORE_LIMITS = YES`
   and nothing stops an axis that misses its switch. One joint at a time at reduced
   speed, Z → Y → X. Gate: two homings in a row give the same DRO.
7. **Prove the soft limits** → an MDI move past each limit is refused and the axis does
   not move.
8. **Run a program**: an air dry-run (no cutting) three times, with a repeatable DRO and no
   packet errors.
9. **Persist** → measured values into the INI, update [DURUM.md](DURUM.md), commit, and
   check after a reboot that the live-USB persistence kept the config.

Phase 1.5 is complete only when all nine gates pass. Then:

- *(low priority, no longer blocking)* **Scope a step pulse** → fully closes **B5**.
- **Then** the marble machine, which stays on the Zhulong board (decided 2026-10-18):
  answer **B1**, decide **B7** (the isolated 24 V supply), and fill in
  [01-machine-survey.md](01-machine-survey.md) *before* its old controller is removed.
  The transition list is [MERMER-GECIS.md](MERMER-GECIS.md).

The config exists now because the board is known. It was deliberately not written earlier:
the board name sets the HAL prefix and the pin map sets every signal name, so writing it
before the dumps would have meant writing it twice — and, as it turned out, writing it
against the wrong board family entirely.

---

## Conventions used in these documents

- **Source citations** are file-and-line into `reference/`, e.g.
  [hm2_eth.c:1319](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1319).
  All 58 such links were checked to resolve with in-range line numbers, and their content
  anchors verified, as of the last edit. `reference/` is gitignored — re-clone with the
  commands in [00](00-upstream-reference-map.md) if it is missing.
- **OBSERVED / INFERRED / UNVERIFIED** in [04](04-zhulong-board-hardware.md) distinguish
  photograph evidence from reasoning from guesswork. The distinction is enforced
  strictly.
- **"Not settled by source"** call-outs mark questions the LinuxCNC tree does not answer.
  These are deliberately *not* filled in with general knowledge.
- **`<B>`** in HAL names stands for the board prefix, e.g. `hm2_7i92.0` (documents written
  before 2026-08-05 show `hm2_7i96.0`, which was wrong).

## Updating this index

When a document's claims get confirmed against hardware, change its status to
**VERIFIED** and note what confirmed it. The first `readhmid` dump landed on 2026-08-05 and
has been applied: [03](03-7i96-pinout.md) superseded, [04](04-zhulong-board-hardware.md)
partly superseded, [08](08-zhulong-pinout-confirmed.md) corrected,
[10](10-7i92-pinout-verified.md) added, B2 closed and B5 downgraded (B3 and B4 had already
closed on 2026-08-03, from the vendor diagram and the driver markings). The next status
changes come from the [PLAN-BUGUN.md](PLAN-BUGUN.md) gates (scale, travel, homing).
