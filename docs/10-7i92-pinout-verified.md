# 10 — Zhulong V2.0 (Mesa 7i92) Verified Pin Map

> ## ✅ VERIFIED against the hardware, 2026-08-05
>
> Every row below is transcribed from `mesaflash` output taken from the actual board.
> This is the first document in the repo whose pin data is measured rather than reasoned.

**This supersedes [03-7i96-pinout.md](03-7i96-pinout.md) entirely**, and the speculative
pin/IO parts of [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md). Wire from
this document.

Sources, both in `docs/board-dumps/` and never to be edited:

| File | Command | What it gives |
|---|---|---|
| [readhmid-10.10.10.10-2026-08-05.txt](board-dumps/readhmid-10.10.10.10-2026-08-05.txt) | `mesaflash --device 7i92 --addr 10.10.10.10 --readhmid` | IDROM, module inventory, pin-out grouped by connector |
| [printpd-10.10.10.10-2026-08-05.txt](board-dumps/printpd-10.10.10.10-2026-08-05.txt) | `mesaflash --device 7i92 --addr 10.10.10.10 --print-pd` | raw pin descriptors with primary/secondary tags |

---

## ⚠️ The correction this document exists to record

**The board is a Mesa 7i92 derivative, not a 7i96.** Everything in this repo written
before 2026-08-05 assumed 7i96 — inferred from the Spartan-6 XC6SLX9, the six AXIS
connectors, and the general resemblance. That inference was **wrong**.

The board says so itself. From `readhmid:4-6`:

> LBP16 board-info sorgusu kartın kendini "7I92" diye tanıttığını gösterdi
> (ham cevap: 37 49 39 32 00 ... = "7I92"). --device 7i96 ile "no 7I96
> board found" hatası alınıyordu; doğru parametre --device 7i92.

and `readhmid:12`: `BoardName : MESA7I92`.

| | Assumed (7i96) | **Actual (7i92)** | Source |
|---|---|---|---|
| Board name string | `7I96` | **`7I92`** | `readhmid:4-6`, `:12` |
| IO pins | 51 | **34** | `readhmid:15-16` |
| IO ports × width | 3 × 17 | **2 × 17** | `readhmid:15-16` |
| Connector names | P1, TB1, TB2, TB3 | **P2, P1** | `hm2_eth.c:1189-1190` |
| HAL prefix | `hm2_7i96.0` | **`hm2_7i92.0`** | `hm2_eth.c:1183`, `:1487` |
| Clock Low | 33 MHz (assumed) | **100 MHz** | `readhmid:17` |
| Clock High | 200 MHz | **200 MHz** ✓ | `readhmid:18` |
| FPGA | XC6SLX9 ✓ | **XC6SLX9** ✓ | `readhmid:13-14`, `hm2_eth.c:1191` |

The FPGA was the one thing we had right — and it is precisely what misled us, since the
7i92 and 7i96 both use a Spartan-6 XC6SLX9 in a 144-pin package. **Same chip, different
board.**

### It loads on the clean path

`hm2_eth` handles `7I92` at [hm2_eth.c:1183](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1183):

```c
} else if (strncmp(board_name, "7I92", 4) == 0) {
    strncpy(llio_name, board_name, 4);
    llio_name[1] = tolower(llio_name[1]);

    board->llio.num_ioport_connectors = 2;
    board->llio.pins_per_connector = 17;
    board->llio.ioport_connector_name[0] = "P2";
    board->llio.ioport_connector_name[1] = "P1";
    board->llio.fpga_part_number = "XC6SLX9";
    board->llio.num_leds = 4;
```

Note this branch compares only **4** characters, unlike the 8-character exact-match used
for the 7i96 family — so it is a genuine prefix test here.

The IDROM agrees with the hardcoded geometry — 2 ports, width 17 (`readhmid:15-16`) — so
the strict consistency checks in `hm2_read_idrom()` (`hostmot2.c:692`, `:698`, `:708`) all
pass. **This is Outcome A of [02-board-bringup.md](02-board-bringup.md)**: connector names
populated, LED pins present, no `"??"` labels, no fallback. The Outcome C hard-abort we
were braced for did not happen.

### Also corrected: the 50 MHz crystal was a red herring

[04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) flagged the observed
**50.000 MHz crystal** as suspicious against a declared 33 MHz, and
[02-board-bringup.md](02-board-bringup.md) Step 7 built a whole procedure around the
possibility that the firmware was misdeclaring its clock.

The IDROM reports **ClockLow 100 MHz, ClockHigh 200 MHz**. The FPGA synthesises both from
the 50 MHz reference via a PLL, which is entirely ordinary. **The crystal was never
evidence about the logic clock**, and treating it as such was over-reading a photograph.
The step-timing verification in docs/02 Step 7 remains sound advice; it is just no longer
urgent (see [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B5, downgraded).

---

## IDROM summary

From `readhmid:10-23`:

| Field | Value |
|---|---|
| Configuration Name | `HOSTMOT2` |
| BoardName | `MESA7I92` |
| FPGA | 9 KGates, 144 pins |
| Number of IO Ports | **2** |
| Width of one I/O port | **17** |
| Total IO pins | **34** |
| Clock Low | **100.0000 MHz** |
| Clock High | **200.0000 MHz** |
| IDROM Type | **3** (valid — driver accepts 2 or 3, `hostmot2.c:669`) |
| Instance Stride 0 / 1 | 4 / 64 |
| Register Stride 0 / 1 | 256 / 256 |

## Module inventory

From `readhmid:25-97`. Note the clock column — **PWM is the only module on the 200 MHz
clock**; everything else runs at 100 MHz.

| Module | Instances | Version | Base addr | Clock | IO pins used |
|---|---:|---:|---|---|---|
| StepGen | **6** | 2 | `2000` | 100 MHz | 4–15 |
| QCount (encoder) | **3** | 2 | `3000` | 100 MHz | 25–33 |
| PWM | **1** | 0 | `4100` | **200 MHz** | 16 |
| SSerial | **1** (2 channels) | 0 | `5B00` | 100 MHz | 0–3 |
| IOPort | 2 | 0 | `1000` | 100 MHz | — |
| LED | 1 (4 LEDs) | 0 | `0200` | 100 MHz | — |
| WatchDog | 1 | 0 | `0C00` | 100 MHz | — |
| DPLL | 1 | 0 | `7000` | 100 MHz | — |

**Naming note:** the firmware calls the quadrature counter **QCount**; the LinuxCNC driver
calls the same module **Encoder** (gtag `0x04`, `hostmot2.h:96`, and
`hm2_get_general_function_name()` returns `"Encoder"` at `hostmot2.c:297`). The config
string parameter is `num_encoders`. Do not go looking for a `num_qcounts`.

---

## The pin map — all 34 IO pins

**Port boundaries** follow from `port_num = i / port_width` with width 17
([pins.c:748](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L748)):

| Port | Connector | IO numbers |
|---:|---|---|
| 0 | **P2** | **IO 0 – 16** |
| 1 | **P1** | **IO 17 – 33** |

"Conn pin" is the DB25 pin number on that connector, as printed by mesaflash.

The **`?`** column marks how the vendor terminal label was established:

- **✓** — silkscreen verified against the vendor diagram (docs/08) *and* consistent with
  the dumps. These are certain.
- **~** — inferred from the module instance mapping in the dump. The FPGA function is
  certain; that the vendor routed that specific terminal to that specific pin is a
  reasonable inference, not a measurement.

"HAL pin, as configured" reflects **our** config string
(`num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx`), under which
everything except StepGen 0–2 falls back to GPIO.

| IO | Port | Conn pin | Secondary function (from dump) | Vendor terminal | ? | HAL pin, as configured |
|---:|---|---:|---|---|:-:|---|
| **0** | P2 | 1 | SSerial 0 · RXData0 (In) | Smart Serial RJ45 | ~ | `hm2_7i92.0.gpio.000.in` / `.in_not` / `.out` |
| **1** | P2 | 14 | SSerial 0 · TXData0 (Out) | Smart Serial RJ45 | ~ | `hm2_7i92.0.gpio.001.in` / `.in_not` / `.out` |
| **2** | P2 | 2 | SSerial 0 · RXData1 (In) | Smart Serial RJ45 | ~ | `hm2_7i92.0.gpio.002.in` / `.in_not` / `.out` |
| **3** | P2 | 15 | SSerial 0 · TXData1 (Out) | Smart Serial RJ45 | ~ | `hm2_7i92.0.gpio.003.in` / `.in_not` / `.out` |
| **4** | P2 | 3 | StepGen 0 · Step/Table1 (Out) | AXIS 0 STEP | ~ | `hm2_7i92.0.stepgen.00.*` |
| **5** | P2 | 16 | StepGen 0 · Dir/Table2 (Out) | AXIS 0 DIR | ~ | `hm2_7i92.0.stepgen.00.*` |
| **6** | P2 | 4 | StepGen 1 · Step/Table1 (Out) | AXIS 1 STEP | ~ | `hm2_7i92.0.stepgen.01.*` |
| **7** | P2 | 17 | StepGen 1 · Dir/Table2 (Out) | AXIS 1 DIR | ~ | `hm2_7i92.0.stepgen.01.*` |
| **8** | P2 | 5 | StepGen 2 · Step/Table1 (Out) | AXIS 2 STEP | ~ | `hm2_7i92.0.stepgen.02.*` |
| **9** | P2 | 6 | StepGen 2 · Dir/Table2 (Out) | AXIS 2 DIR | ~ | `hm2_7i92.0.stepgen.02.*` |
| **10** | P2 | 7 | StepGen 3 · Step/Table1 (Out) | AXIS 3 STEP | ~ | `hm2_7i92.0.gpio.010.in` / `.in_not` / `.out` |
| **11** | P2 | 8 | StepGen 3 · Dir/Table2 (Out) | AXIS 3 DIR | ~ | `hm2_7i92.0.gpio.011.in` / `.in_not` / `.out` |
| **12** | P2 | 9 | StepGen 4 · Step/Table1 (Out) | AXIS 4 STEP | ~ | `hm2_7i92.0.gpio.012.in` / `.in_not` / `.out` |
| **13** | P2 | 10 | StepGen 4 · Dir/Table2 (Out) | AXIS 4 DIR | ~ | `hm2_7i92.0.gpio.013.in` / `.in_not` / `.out` |
| **14** | P2 | 11 | StepGen 5 · Step/Table1 (Out) | AXIS 5 STEP | ~ | `hm2_7i92.0.gpio.014.in` / `.in_not` / `.out` |
| **15** | P2 | 12 | StepGen 5 · Dir/Table2 (Out) | AXIS 5 DIR | ~ | `hm2_7i92.0.gpio.015.in` / `.in_not` / `.out` |
| **16** | P2 | 13 | PWM 0 · PWM (Out) | 0-10V analog out | ~ | `hm2_7i92.0.gpio.016.in` / `.in_not` / `.out` |
| **17** | P1 | 1 | **none — permanently GPIO** | CW | ✓ | `hm2_7i92.0.gpio.017.in` / `.in_not` / `.out` |
| **18** | P1 | 14 | **none — permanently GPIO** | OUT 1 | ✓ | `hm2_7i92.0.gpio.018.in` / `.in_not` / `.out` |
| **19** | P1 | 2 | **none — permanently GPIO** | OUT 2 | ✓ | `hm2_7i92.0.gpio.019.in` / `.in_not` / `.out` |
| **20** | P1 | 15 | **none — permanently GPIO** | input 20 | ✓ | `hm2_7i92.0.gpio.020.in` / `.in_not` / `.out` |
| **21** | P1 | 3 | **none — permanently GPIO** | input 21 | ✓ | `hm2_7i92.0.gpio.021.in` / `.in_not` / `.out` |
| **22** | P1 | 16 | **none — permanently GPIO** | input 22 | ✓ | `hm2_7i92.0.gpio.022.in` / `.in_not` / `.out` |
| **23** | P1 | 4 | **none — permanently GPIO** | input 23 | ✓ | `hm2_7i92.0.gpio.023.in` / `.in_not` / `.out` |
| **24** | P1 | 17 | **none — permanently GPIO** | input 24 | ✓ | `hm2_7i92.0.gpio.024.in` / `.in_not` / `.out` |
| **25** | P1 | 5 | QCount 0 · Quad-A (In) | Encoder 0 A | ~ | `hm2_7i92.0.gpio.025.in` / `.in_not` / `.out` |
| **26** | P1 | 6 | QCount 0 · Quad-B (In) | Encoder 0 B | ~ | `hm2_7i92.0.gpio.026.in` / `.in_not` / `.out` |
| **27** | P1 | 7 | QCount 0 · Quad-IDX (In) | Encoder 0 Z | ~ | `hm2_7i92.0.gpio.027.in` / `.in_not` / `.out` |
| **28** | P1 | 8 | QCount 1 · Quad-A (In) | Encoder 1 A | ~ | `hm2_7i92.0.gpio.028.in` / `.in_not` / `.out` |
| **29** | P1 | 9 | QCount 1 · Quad-B (In) | Encoder 1 B | ~ | `hm2_7i92.0.gpio.029.in` / `.in_not` / `.out` |
| **30** | P1 | 10 | QCount 1 · Quad-IDX (In) | Encoder 1 Z | ~ | `hm2_7i92.0.gpio.030.in` / `.in_not` / `.out` |
| **31** | P1 | 11 | QCount 2 · Quad-A (In) | Encoder 2 A | ~ | `hm2_7i92.0.gpio.031.in` / `.in_not` / `.out` |
| **32** | P1 | 12 | QCount 2 · Quad-B (In) | Encoder 2 B | ~ | `hm2_7i92.0.gpio.032.in` / `.in_not` / `.out` |
| **33** | P1 | 13 | QCount 2 · Quad-IDX (In) | Encoder 2 Z | ~ | `hm2_7i92.0.gpio.033.in` / `.in_not` / `.out` |

### Reading the table

**IO 17–24 are special and worth understanding.** They are the only pins with **no
Secondary Tag at all** in the pin descriptors (`printpd:94-109`) — meaning there is no
module in this firmware that *can* claim them. They are permanently, unconditionally
GPIO, whatever the config string says. Every other GPIO in the table above is GPIO only
because we chose not to instantiate its owning module.

This is what makes the vendor's five isolated inputs and three outputs trustworthy: they
cannot be stolen by a config change.

**The vendor silkscreen numbers ARE global IO numbers.** docs/08 hypothesised this from
the fact that the input terminals were labelled `20 21 22 23 24` rather than `1`–`5`. The
dumps confirm it: IO 17 = `CW`, IO 18 = `OUT 1`, IO 19 = `OUT 2`, IO 20–24 = the five
isolated inputs. So `gpio.020.in` really is the terminal marked `20`. That is a genuinely
helpful piece of board design.

**Step comes before Dir**, ascending, on every channel: IO 4 = StepGen 0 Step, IO 5 =
StepGen 0 Dir, and so on. This is the *opposite* of the Dir-before-Step order that docs/03
found in pncconf's stock 7i96 data — one more reason that document does not transfer.

**Six StepGens, and we use three.** StepGen 3–5 (IO 10–15) are disabled by
`num_stepgens=3` and become GPIO. On the marble machine, if the dual-motor gantry Y turns
out to be real (blocker B1), StepGen 3 is available at IO 10/11 without any firmware
change.

---

## GPIO budget under our config

`CONFIG = "num_stepgens=3 num_encoders=0 num_pwmgens=0 sserial_port_0=xxxxxxxx"`

An IO pin whose owning module is not instantiated is exported as a full GPIO pin
([hostmot2.adoc:400-401](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L400-L401)):

| Pins | Why they are GPIO | Count |
|---|---|---:|
| IO 0–3 | SSerial disabled by `sserial_port_0=xxxxxxxx` | 4 |
| IO 10–15 | StepGen 3–5 not instantiated (`num_stepgens=3`) | 6 |
| IO 16 | PWM not instantiated (`num_pwmgens=0`) | 1 |
| IO 17–24 | **permanently GPIO — no secondary tag exists** | 8 |
| IO 25–33 | QCount not instantiated (`num_encoders=0`) | 9 |
| | **GPIO total** | **28** |
| IO 4–9 | consumed by StepGen 0–2 | 6 |
| | **Total** | **34** ✓ |

**28 of 34 pins are available as GPIO.** That is the finding that resolves the marble
machine's input-budget problem — see [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B2.

**The caveat that keeps B2 from being fully closed:** FPGA-level availability says nothing
about the *external circuitry* between the FPGA pin and the screw terminal. IO 20–24 are
behind opto-isolators designed for 24 V (docs/04). IO 25–33 come out at the encoder
terminals, whose analog front-end is **unverified** — they may be 5 V-only, may be
input-only, may carry a bias network we cannot defeat. Ring them out with a meter before
planning to use them as general inputs.

---

## What this means for the existing docs

| Doc | Status now |
|---|---|
| [03-7i96-pinout.md](03-7i96-pinout.md) | **Superseded.** Banner added. Its *driver-behaviour* analysis is still valid and still useful; its pin table describes a board we do not own. |
| [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) | **Partly superseded.** Connector/component observations from photographs stand. The IO-numbering hypothesis is now CONFIRMED. The I/O budget arithmetic assumed 51 pins and is wrong; use the table above. |
| [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md) | **Corrected** — was comparing against the stock 7i96 complement. |
| [02-board-bringup.md](02-board-bringup.md) | Still valid as procedure. Outcome A is what happened. Step 7 step-timing verification is now low priority. |

---

## Still not verified

The dumps describe the FPGA and its pin descriptors. They say nothing about the frame or
the wiring beyond the connector.

| Open | Why the dumps cannot answer it |
|---|---|
| Which physical end of which axis each sensor sits at | Requires halmeter and pushing switches by hand |
| Switch polarity (`.in` vs `.in_not`) | Opto-isolator inversion is a property of the input circuit |
| Encoder terminal front-end characteristics | Requires an ohmmeter on an unpowered board |
| Whether AXIS *n*'s terminals really route to StepGen *n*'s pins | Requires commanding one stepgen at a time, motors uncoupled |
| Actual axis travel | Requires a rule |
