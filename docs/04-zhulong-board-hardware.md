# 04 — Zhulong V2.0 Board Hardware

Physical description of **our actual board**, assembled from high-resolution
photographs.

> **⚠ Status, 2026-10-18 — partly superseded; the body is kept as history.** The board
> is a **Mesa 7i92 derivative** (`hm2_7i92.0`, 2 × 17 = 34 IO, ClockLow 100 MHz), not a
> 7i96 (`board-dumps/readhmid-10.10.10.10-2026-08-05.txt`). **Wire from
> [10-7i92-pinout-verified.md](10-7i92-pinout-verified.md)** and
> [08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md) (vendor diagram), not
> from this file. The connector and component observations from photographs still
> stand, and the IO-numbering hypothesis is CONFIRMED. The I/O budget below assumed 51
> pins and is wrong. The 50 MHz crystal worry was a red herring — the crystal is the PLL
> reference (docs/10). In the HAL pin names below, read `hm2_7i96.0` as `hm2_7i92.0`. The
> resolution log at the bottom shows what has closed.

Silkscreen reads **烛龙 V2.0** ("Zhúlóng", the torch-dragon of Chinese myth) alongside a
LinuxCNC logo.

**Related:** [03-7i96-pinout.md](03-7i96-pinout.md) describes the *stock Mesa 7i96* and
is **not** a wiring reference for this board. [02-board-bringup.md](02-board-bringup.md)
is the procedure for resolving what is still unknown here.

---

## Confidence tags — used strictly throughout

| Tag | Meaning |
|---|---|
| **OBSERVED** | Directly visible in a photograph. Silkscreen text, terminal counts, component markings, connector positions. |
| **INFERRED** | Reasoned from a silkscreen label, a component value, or 7i96 architecture. Plausible, not confirmed. |
| **UNVERIFIED** | A guess. We have no evidence either way and are recording the hypothesis so it can be tested. |

**We have no vendor documentation of any kind.** No schematic, no manual, no pinout, no
bitfile source. Every INFERRED and UNVERIFIED item below is a question for
`mesaflash --readhmid` or for a multimeter. (2026-10-18: superseded in part — the
vendor's annotated board diagram (2026-08-03) is transcribed in
[08-zhulong-pinout-confirmed.md](08-zhulong-pinout-confirmed.md).)

---

## Connector layout

```
                          TOP EDGE
  ┌──────┬──────┬──────┬──────┬──────┬──────┬─────────┬─────┬─────┐
  │AXIS 0│AXIS 1│AXIS 2│AXIS 3│AXIS 4│AXIS 5│0-10V +CW│OUT 1│OUT 2│
  │ 4 tm │ 4 tm │ 4 tm │ 4 tm │ 4 tm │ 4 tm │  4 tm   │2 tm │2 tm │
  └──────┴──────┴──────┴──────┴──────┴──────┴─────────┴─────┴─────┘

  ┌─────────────┐                                        ┌────────┐
  │ Smart       │                                        │24V GND │
  │ Serial 1    │   ┌─────────────┐    ┌──────────┐      │  2 tm  │
  │  (RJ45)     │   │ Spartan-6   │    │ KSZ8851  │      ├────────┤
  ├─────────────┤   │ XC6SLX9     │    │   PHY    │      │24V GND │
  │ Smart       │   │ TQG144      │    └──────────┘      │  2 tm  │
  │ Serial 0    │   └─────────────┘                      └────────┘
  │  (RJ45)     │      ▪ 50.000 MHz xtal                 RIGHT EDGE
  ├─────────────┤      ▪ B0505S-1WR3 isolated DC-DC
  │ ETHERNET    │      ▪ JTAG: TDI TCK TMS TDO GND 3V3
  │  (RJ45,     │      ▪ W1  W2   (silkscreen "3V" / "GND" adjacent)
  │  link LEDs) │
  └─────────────┘
   LEFT EDGE

  ┌────┬────┬────┬────┬────┬───┬────┐ ┌─────────┬─────────┬─────────┐
  │ 20 │ 21 │ 22 │ 23 │ 24 │ G │ 5V │ │Encoder 0│Encoder 1│Encoder 2│
  └────┴────┴────┴────┴────┴───┴────┘ └─────────┴─────────┴─────────┘
        single 7-terminal block
                          BOTTOM EDGE
```

`tm` = screw terminals. Diagram is schematic — relative positions along each edge are
OBSERVED, but it is not to scale and the vertical arrangement of the centre components
is approximate.

---

## Connector inventory

| # | Silkscreen | Terminals | Edge | Believed function | Confidence |
|---:|---|---:|---|---|---|
| 1 | `AXIS 0` | 4 | top | Step/dir output, channel 0 | **INFERRED** — from label + 7i96 architecture |
| 2 | `AXIS 1` | 4 | top | Step/dir output, channel 1 | **INFERRED** |
| 3 | `AXIS 2` | 4 | top | Step/dir output, channel 2 | **INFERRED** |
| 4 | `AXIS 3` | 4 | top | Step/dir output, channel 3 | **INFERRED** |
| 5 | `AXIS 4` | 4 | top | Step/dir output, channel 4 | **INFERRED** |
| 6 | `AXIS 5` | 4 | top | Step/dir output, channel 5 | **INFERRED** |
| — | *(terminal meaning within each AXIS block)* | — | — | `STEP+ STEP− DIR+ DIR−` **or** `STEP DIR GND 5V` | **CLOSED** (2026-08-03): `GND STEP DIR 5V`, single-ended — vendor diagram, [08](08-zhulong-pinout-confirmed.md), B3 |
| 7 | `0-10V` + `CW` | 4 (shared block) | top | Analog spindle speed + run/direction | **INFERRED** |
| — | *(the other 2 terminals of that block)* | — | — | Likely a ground/common and a second signal | **UNVERIFIED** |
| 8 | `OUT 1` | 2 | top | Digital output 1 | **INFERRED** |
| 9 | `OUT 2` | 2 | top | Digital output 2 | **INFERRED** |
| — | *(output type: SSR / relay / opto / open-collector)* | — | — | NMOS, 24 V / 50 W; no SSR module in the firmware. Needs the second isolated 24 V supply (B7) | per vendor diagram ([08](08-zhulong-pinout-confirmed.md)) — not measured (2026-10-18) |
| 10 | `20 21 22 23 24 G 5V` | 7 (one block) | bottom | 5 digital inputs + common + 5 V supply | **CONFIRMED** (vendor diagram 2026-08-03, dumps 2026-08-05; status tag added 2026-10-18) — labels are global IO numbers ([08](08-zhulong-pinout-confirmed.md), [10](10-7i92-pinout-verified.md)) |
| 11 | `Encoder 0` | ? | bottom | Quadrature encoder channel 0 | **INFERRED** |
| 12 | `Encoder 1` | ? | bottom | Quadrature encoder channel 1 | **INFERRED** |
| 13 | `Encoder 2` | ? | bottom | Quadrature encoder channel 2 | **INFERRED** |
| — | *(terminal count per encoder block)* | — | — | not legible in the photographs; A/B/Z + power expected | **UNVERIFIED** — per vendor diagram: 5, `A B Z GND 5V` ([08](08-zhulong-pinout-confirmed.md)); front-end unverified (2026-10-18) |
| 14 | `24V GND` | 2 | right | Main power input | **OBSERVED** (silkscreen) — but both terminals carry the same `24V GND` label; the main one sits next to the `470 16V` capacitor and `150` inductor (2026-10-18, [08](08-zhulong-pinout-confirmed.md)) |
| 15 | `24V GND` | 2 | right | Second power terminal — parallel feed or pass-through | **INFERRED — wrong** (2026-10-18 note): per the vendor diagram it is the *isolated* external-device 24 V for OUT 1 / OUT 2 / 0-10V; never feed it from the main supply or join the GNDs ([08](08-zhulong-pinout-confirmed.md), B7) |
| 16 | `Smart Serial 1` | RJ45 | left, top | RS-422 Smart Serial port 1 | **OBSERVED** (silkscreen) |
| 17 | `Smart Serial 0` | RJ45 | left, middle | RS-422 Smart Serial port 0 | **OBSERVED** (silkscreen) |
| 18 | *(unlabelled)* | RJ45 | left, bottom | **Ethernet** — has link/activity LEDs in the jack | **OBSERVED** |

### Notable absence

**There is no DB25 / P1 connector.** OBSERVED. On a stock 7i96 that connector carries
17 GPIO pins at IO 34–50. On this board they are not brought out anywhere. This is the
single largest physical difference from the stock design and is a major input to the
geometry question (Q5). (2026-10-18: moot — the board is a 7i92 with 2 × 17 = 34 IO, so
IO 34–50 do not exist; in the 7i92 driver `P1` names IO 17–33, which *are* brought out to
the CW / OUT / input / encoder terminals — [10](10-7i92-pinout-verified.md).)

---

## On-board components

| Component | Marking | Function | Confidence |
|---|---|---|---|
| FPGA | Xilinx Spartan-6 **XC6SLX9 TQG144** | Main logic | **OBSERVED** |
| Ethernet PHY/MAC | Micrel **KSZ8851** | Ethernet interface | **OBSERVED** |
| Crystal | **50.000 MHz** | Master clock source | **OBSERVED** |
| Isolated DC-DC | HI-LINK **B0505S-1WR3** | 5 V→5 V isolated, 1 W | **OBSERVED** |
| JTAG header | `TDI TCK TMS TDO GND 3V3` | FPGA programming/debug | **OBSERVED** |
| Jumpers | `W1`, `W2`, with `3V` and `GND` silkscreen adjacent | unknown | **UNVERIFIED** — see Q2 |
| Input resistors | chip resistors marked `472` and `511`, one pair per input | Optocoupler drive network | **OBSERVED** (markings); topology **UNVERIFIED** |

The **XC6SLX9 TQG144 matches** what `hm2_eth` hardcodes for the stock 7i96 —
`fpga_part_number = "6slx9tqg144"`
([hm2_eth.c:1338](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1338)).
So the FPGA is right even though the board around it is not.

The **B0505S-1WR3** is a 1 W isolated 5 V→5 V converter. Its presence means part of the
board is galvanically isolated from the rest — consistent with the optocoupled inputs.
Which side of the isolation barrier each connector sits on is **UNVERIFIED** and matters
for grounding decisions later.

**The 50.000 MHz crystal is worth flagging.** The stock 7i96 IDROM reports ClockLow
33 MHz / ClockHigh 200 MHz ([00-upstream-reference-map.md](00-upstream-reference-map.md) §e).
A 50 MHz reference does not obviously produce 33 MHz. The clocks that matter are
whatever the IDROM reports, not the crystal — but if the dump shows something other than
33/200, this crystal is the reason, and **every stepgen timing parameter
(`steplen`, `stepspace`, `dirsetup`, `dirhold`) is scaled by ClockLow.** Getting this
wrong means step pulses that silently violate the drives' timing requirements. Record
the real values.

> **Resolved (2026-08-05):** the IDROM reports ClockLow 100 MHz / ClockHigh 200 MHz
> (`readhmid:17-18`); StepGen runs at 100 MHz (`readhmid:68`). The crystal is the PLL
> reference and was never evidence about the logic clock
> ([10-7i92-pinout-verified.md](10-7i92-pinout-verified.md),
> [OPEN-QUESTIONS.md](OPEN-QUESTIONS.md) B5).

---

## Hypothesis: the input labels 20–24 are LinuxCNC global IO numbers

**Claim (INFERRED):** the silkscreen `20 21 22 23 24` on the input block are *HostMot2
global IO numbers*, not terminal positions. If true, the five inputs appear in HAL as:

```
hm2_7i96.0.gpio.020.in
hm2_7i96.0.gpio.021.in
hm2_7i96.0.gpio.022.in
hm2_7i96.0.gpio.023.in
hm2_7i96.0.gpio.024.in
```

### Why we think so

1. **They do not start at 1.** A terminal-position numbering on a 7-way block would run
   1–7 (or 1–5 for the signal terminals). Starting at 20 is not a position scheme.
2. **They share the block with `G` and `5V`.** Those are unambiguously *function* labels,
   not positions. A block that labels two of its terminals by function is most naturally
   read as labelling all seven by function.
3. **The run is contiguous and exactly the right length** — five labels, five input
   terminals.
4. **It is the obvious thing for a vendor to do.** Printing the HAL pin number on the
   silkscreen removes the single most annoying step of Mesa configuration. A board sold
   specifically for LinuxCNC has every reason to do this.
5. **It is consistent with the board running a custom bitfile,** which we established
   independently in [03-7i96-pinout.md](03-7i96-pinout.md). On the *stock* bitfile,
   IO 20–24 are StepGen pins (Step 1, Dir 2, Step 2, Dir 3, Step 3), so the hypothesis
   *requires* different firmware — and we already know the firmware is different. The
   two conclusions reinforce rather than contradict each other.

Point 5 is worth dwelling on: it means the hypothesis costs nothing in plausibility. It
would only be surprising if we still believed the stock bitfile were loaded.

### Exactly how `--readhmid` settles it

In the per-pin section of the dump, find the five lines for IO 20 through IO 24.
`hm2_print_pin_usage()` emits **two distinguishable forms**, and which one a pin gets is
exactly the test we need
([pins.c:887-913](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L887-L913)):

```c
if (pin->gtag == pin->sec_tag) {          // pin IS claimed by its secondary function
    ...  "    IO Pin %03d (%s): %s #%d, pin %s (%s)\n"      // line 899
} else {                                   // pin is NOT claimed — plain GPIO
    ...  "    IO Pin %03d (%s): %s\n"                        // line 909
}
```

So a **free** pin prints the short form — function name only:

```
    IO Pin 020 (??-10): IOPort
```

and a **claimed** pin prints the long form, naming the owning module, its instance
number, the signal and the direction:

```
    IO Pin 020 (??-10): StepGen #1, pin Step (Output)
```

| What you see for IO 020–024 | Verdict |
|---|---|
| All five in the **short form**, function `IOPort` | **CONFIRMED.** The silkscreen numbers are HAL IO numbers and those pins are free GPIO. |
| Any in the **long form** naming StepGen / Encoder / PWMGen | **REFUTED.** The numbers mean something else — most likely the vendor's own channel numbering. |

(A third form at [pins.c:890](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L890)
adds `(all)` instead of an instance number, for module-global pins. Treat it as "claimed".)

If refuted, recover the real mapping by scanning the whole per-pin listing for pins with
**no** secondary function. Those are the free GPIOs; there should be exactly five of them
if the vendor brought out five inputs, and their IO numbers are what we actually use.

**Then confirm physically, because readhmid cannot prove the silkscreen matches the
wiring.** With the board powered and the driver loaded but *nothing else connected*:

```bash
halrun -I
halcmd: loadrt hostmot2
halcmd: loadrt hm2_eth board_ip="10.10.10.10"
halcmd: start
halcmd: show pin *gpio.02*
```

Apply 24 V to terminal `20` (referenced to `G`) and watch which `gpio.NNN.in` flips.
That is the only unambiguous answer, and it takes two minutes. Do it for all five.

> **2026-10-18 — settled differently.** The label = IO-number question was closed by
> the vendor diagram and the dumps ([10-7i92-pinout-verified.md](10-7i92-pinout-verified.md)).
> On the test rig the five switches were then pressed by hand, each flipping one of
> `gpio.020`–`024` ([DURUM.md](DURUM.md), switch map). Nothing records 24 V being applied
> to an input; 24 V acceptance (B6) is still open.

---

## Hypothesis: the inputs are designed for 24 V

**OBSERVED:** each input has a pair of chip resistors marked `472` and `511`.

Standard EIA-96 three-digit chip resistor coding — first two digits are significant, the
third is the decade multiplier:

- `472` → 47 × 10² Ω = **4 700 Ω = 4.7 kΩ**
- `511` → 51 × 10¹ Ω = **510 Ω**

**Assumption (UNVERIFIED):** the input is an optocoupler LED driven through a series
resistor, and the LED has a forward drop of **V_f ≈ 1.2 V**, which is typical for the
phototransistor optocouplers used in this role. A design target of **~5 mA** forward
current is the industry norm.

### The calculation

Taking the 4.7 kΩ as the series element:

```
I = (V_in − V_f) / R

At 24 V:   I = (24 − 1.2) / 4700  =  22.8 / 4700  =  4.85 mA     ← on target
At 12 V:   I = (12 − 1.2) / 4700  =  10.8 / 4700  =  2.30 mA     ← marginal
At  5 V:   I = ( 5 − 1.2) / 4700  =   3.8 / 4700  =  0.81 mA     ← will not switch
```

Run the design backwards as a sanity check: for exactly 5 mA at 24 V you need
`R = 22.8 / 0.005 = 4560 Ω`. The nearest standard E24 value **is 4.7 kΩ**. The intent
is unmistakable.

**Conclusion (INFERRED): the inputs are designed for 24 V nominal.** 12 V would be
marginal and 5 V will not work through this path.

> **Update (2026-10-18, [DURUM.md](DURUM.md), B6 row):** on the test rig the five
> switches *do* work at 5 V, so "5 V will not work" is contradicted for that wiring.
> Which topology below is real is still unmeasured, and whether a 24 V sensor can be
> connected directly is still open (B6).

### What the 510 Ω does — genuinely undetermined

A photograph cannot show the topology. Three plausible arrangements, all common:

| Topology | Consequence at 24 V |
|---|---|
| **(a)** 510 Ω shunt across the opto LED, for noise immunity and a defined turn-on threshold | Shunt draws 1.2 / 510 = 2.35 mA; LED gets 4.85 − 2.35 = **2.5 mA** |
| **(b)** 510 Ω in series with the 4.7 kΩ, total 5.21 kΩ | I = 22.8 / 5210 = **4.38 mA** |
| **(c)** 510 Ω is a separate series path for 5 V operation, which would explain the `5V` terminal on the input block | (5 − 1.2) / 510 = **7.45 mA** — a sensible 5 V drive |

All three land the LED in a workable range, so the 24 V conclusion holds regardless. But
(a) and (c) have very different implications for whether these inputs can be driven from
a 5 V source, and (c) would change how we wire sensors.

**All of this is UNVERIFIED pending two things:**

1. **Read the optocoupler part number off the board** — under magnification, or from a
   better photograph. That gives the real V_f and the recommended forward current, and
   turns the assumption into a number.
2. **Ring out the network with a meter on the unpowered board** to establish whether the
   510 Ω is in series, in shunt, or on a separate path. Five minutes with an ohmmeter
   settles what no amount of reasoning will.

Until then: **plan for 24 V inputs, and do not connect a 5 V sensor** on the assumption
that (c) is correct. (2026-10-18: on the test rig the switches already work at 5 V — see
the update above. Which topology explains that is still unmeasured, so this caution
still applies to new sensors.)

---

## I/O budget

Counting FPGA IO pins consumed by the connectors we can see. Encoder terminal counts are
UNVERIFIED, so the encoder row assumes the conventional A/B/Z.

| Function | Blocks | FPGA IO pins each | Subtotal | Basis |
|---|---:|---:|---:|---|
| Step/dir, `AXIS 0`–`AXIS 5` | 6 | 2 | **12** | 1 pin each for step and dir |
| Encoder, `Encoder 0`–`2` | 3 | 3 | **9** | A, B, Z per channel |
| Digital inputs `20`–`24` | 5 | 1 | **5** | one pin per input |
| Digital outputs `OUT 1`, `OUT 2` | 2 | 1 | **2** | one pin per output |
| Spindle `0-10V` + `CW` | 1 | 2 | **2** | PWM + direction |
| Smart Serial 0 and 1 | 2 | 3 | **6** | RXDATA, TXDATA, TXEN per port |
| | | | | |
| **Total committed** | | | **36** | |
| **Available if IDROM reports 3 × 17** | | | **51** | |
| **Unaccounted for** | | | **15** | |

```
  12  step/dir      (6 × 2)
 + 9  encoder       (3 × 3)
 + 5  inputs
 + 2  outputs
 + 2  spindle PWM + direction
 + 6  smart serial  (2 × 3)
 ───
  36  committed
  51  available (3 ports × 17 pins)
 ───
  15  left over
```

### Reading the leftovers

**36 fits comfortably inside 51.** A 3 × 17 IDROM is therefore *possible*, which is the
good case — it means the board can report the stock geometry honestly and take the
driver's exact-match path.

But **15 unaccounted pins is a lot**, and there are two very different explanations:

1. **The vendor declared 3 ports anyway** and simply left 15 pins unconnected. The
   stock P1 carried 17; dropping that connector and reusing 2 of its pins elsewhere
   would land near 15. Under this reading the IDROM says 3 × 17 = 51 and everything is
   straightforward.
2. **The vendor declared fewer ports** — e.g. 2 × 17 = 34, which would *not* fit our 36
   committed pins, or some non-17 port width. **If the IDROM reports anything other than
   3 × 17 while the board still calls itself `7I96`, the driver hard-aborts.** That is
   Outcome C in [02-board-bringup.md](02-board-bringup.md), and this arithmetic is why it
   is a live risk rather than a theoretical one.

Note that 36 > 34, so explanation 2 with a 2-port declaration is arithmetically
impossible *if* all our connector inferences are right — which is itself a useful
cross-check on the dump.

**The step/dir count is robust to the differential question.** Whether each `AXIS` block
is `STEP+ STEP− DIR+ DIR−` or `STEP DIR GND 5V`, it consumes **2 FPGA pins** either way:
in the differential case an external line driver expands 1 FPGA pin into a terminal pair,
exactly as the stock 7i96 does (its pin labels are pairs like `TB1-02/TB1-03` — see
[03-7i96-pinout.md](03-7i96-pinout.md)). So Q1 changes how we *wire* the drives, not the
budget.

---

## Open questions

Each is phrased so that the `--readhmid` dump — or, where the dump cannot reach, a
specific bench measurement — settles it.

### Q1. Are the AXIS blocks differential or single-ended with power?

Four terminals per axis is consistent with **`STEP+ STEP− DIR+ DIR−`** (differential, as
the stock 7i96 does) or with **`STEP DIR GND 5V`** (single-ended plus a supply for the
drive's opto).

**What readhmid gives you:** the number of StepGen instances and their IO pin numbers. If
it reports 6 StepGens consuming 12 pins, each block is driven by 2 FPGA pins — which is
true under *both* hypotheses, so **readhmid narrows but does not settle this.**

**What settles it:** an ohmmeter on the unpowered board. Measure each of the four
terminals against board ground and against the 5 V rail. A `GND` terminal reads ≈ 0 Ω to
ground; a `5V` terminal reads ≈ 0 Ω to the 5 V rail; differential complements read to
neither. **Do this before connecting a single drive** — feeding 5 V into what you thought
was `STEP−` is how line drivers die.

### Q2. What do W1 and W2 do?

The adjacent `3V` and `GND` silkscreen suggests each jumper selects a logic level or pull
direction for an FPGA configuration or mode pin — possibly a 2-bit boot/mode selector,
possibly an IP-address selector as on some Mesa boards. **UNVERIFIED.**

**How to settle it:** photograph the as-received positions first. Then run
`scripts/detect-board.sh` and save the dump. Change **one** jumper, power-cycle, re-run,
and diff the two dumps. If the reachable IP changes, they are address selectors; if the
IDROM or config name changes, they select a boot source; if nothing changes, they are
something else (or unpopulated in function). Change one at a time and write down what you
tried.

### Q3. What is the board's default IP address?

`--readhmid` cannot answer this — you need the address to run it. Chicken and egg.

**How to settle it:** `scripts/detect-board.sh` sweeps `10.10.10.10`, `192.168.1.121`,
`192.168.1.10`, `192.168.0.10`. If one answers, that is the address. Once connected,
`mesaflash --addr <ip> --device <dev> --info` reports the board's stored network
configuration. If none answer, see the hint list in
[02-board-bringup.md](02-board-bringup.md) §3.2 and consider `tcpdump` on the interface
while power-cycling, or an `arp-scan` of the likely subnets.

### Q4. Can the three encoder channels be reassigned as GPIO?

We are retrofitting a 3-axis **open-loop stepper** machine. We need no encoders at all,
so all 9 encoder pins are potentially available as general-purpose I/O — useful, given
only 5 dedicated inputs are brought out.

**This is answerable from source, and the answer is yes in principle**
([hostmot2.adoc:400-401](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L400-L401)):

> General Purpose I/O pins on the board which are not used by a module
> instance are exported to HAL as 'full' GPIO pins.

and the mechanism is the `config=` string
([hostmot2.adoc:268](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L268)) — asking
for fewer instances than the firmware provides releases the surplus pins to GPIO. So
`config="num_encoders=0 ..."` should hand us all 9.

**What readhmid must confirm:** the IO numbers those encoder pins occupy, so we know
*which* GPIOs we gain. Look for pins whose secondary function is Encoder and note their
IO numbers.

**The caveat that keeps this open:** releasing a pin to GPIO makes it available *in the
FPGA*. It says nothing about the **external circuitry** between the FPGA pin and the
screw terminal. If the encoder inputs sit behind 5 V differential receivers or a
particular bias network, a "GPIO" there is still constrained by that hardware — it may
only accept 5 V, may be input-only, or may have a pull-up we cannot defeat. Confirm with
a meter and treat them as inputs until proven otherwise.

### Q5. Does the IDROM report 3 ports × 17 pins = 51?

The pivotal question, because of the missing P1 and the 15 unaccounted pins.

**How to settle it:** read `IOPorts`, `PortWidth` and `IOWidth` straight off the dump.

- **3 / 17 / 51** → geometry matches what the driver hardcodes. Combined with a board
  name of exactly `7I96`, this is Outcome A.
- **Anything else, with the name `7I96`** → Outcome C, hard abort, no config possible
  until resolved.
- **Anything else, with any other name** → Outcome B, the driver takes geometry from the
  IDROM and loads fine.

### Q6. What type are OUT 1 and OUT 2?

Two terminals each. Could be SSR (as the stock 7i96's TB3 outputs are), mechanical relay,
optocoupler, or open-collector. Determines what they can switch and whether they need an
external flyback diode or snubber.

**What readhmid gives you:** whether the driver sees an **SSR module** in the firmware. If
the module inventory lists SSR instances, the HAL names are
`<B>.ssr.NN.out-<MM>` ([ssr.c:163](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/ssr.c#L163))
and they are solid-state. If there is no SSR module, they are plain GPIO outputs driving
whatever the board fits.

**What it does not give you:** the voltage and current rating, or whether they are dry
contacts. Identify the output device by its markings before switching anything real.

### Q7. Which side of the isolation barrier is each connector on?

The B0505S-1WR3 means part of the board is galvanically isolated. Which part is
**UNVERIFIED**, and it determines the grounding scheme for the whole machine — whether
input commons, output commons, encoder grounds and the 24 V return are the same node.

**readhmid cannot answer this at all.** Continuity testing on the unpowered board, plus
tracing the DC-DC's output net. Do it before deciding how to earth the cabinet.

---

## Resolution log

Fill in as each question closes. Record the readhmid dump filename that settled it.

| Q | Question | Status | Answer | Evidence |
|---|---|---|---|---|
| Q1 | AXIS blocks differential? | **closed** 2026-08-03 | No — single-ended `GND STEP DIR 5V`; the 4th terminal is a 5 V supply for opto-isolated drives | vendor diagram, [08](08-zhulong-pinout-confirmed.md); OPEN-QUESTIONS B3 |
| Q2 | W1/W2 function | open | | |
| Q3 | Default IP | answered in practice | Board answers on `10.10.10.10`, PC `10.10.10.1/24`. Whether that is the factory default (or a jumper setting — see Q2) is not recorded | `readhmid-10.10.10.10-2026-08-05.txt`; [DURUM.md](DURUM.md) |
| Q4 | Encoder pins → GPIO | **closed at FPGA level** 2026-08-05 | Yes — `num_encoders=0` frees IO 25–33 (9 GPIO). The terminal front-end is still unverified: check with an ohmmeter before connecting a 24 V device | `printpd-10.10.10.10-2026-08-05.txt`:110-154; OPEN-QUESTIONS B2; [10](10-7i92-pinout-verified.md) |
| Q5 | IDROM geometry 3×17? | **closed** 2026-08-05 | No — 2 × 17 = 34, BoardName `MESA7I92`, prefix `hm2_7i92.0`; clean path (Outcome A per [10](10-7i92-pinout-verified.md)) | `readhmid-10.10.10.10-2026-08-05.txt`:12, :15-16 |
| Q6 | OUT 1/2 device type | answered from vendor diagram, not measured | NMOS, 24 V / 50 W, IO 18/19; no SSR module in the firmware. Needs the second isolated 24 V supply (B7) | [08](08-zhulong-pinout-confirmed.md); readhmid module inventory (:25-97) |
| Q7 | Isolation boundary | open | Partial, per vendor diagram: OUT 1 / OUT 2 and 0-10V run from the external-device 24 V, which must be isolated from the main supply (no common ground). Everything else unmeasured | [08](08-zhulong-pinout-confirmed.md); OPEN-QUESTIONS B7 |
| — | Input labels 20–24 are IO numbers? | **closed** 2026-08-03 (diagram) / 2026-08-05 (dumps) | Yes — the vendor diagram labels them `PIN20`…`PIN24`, and the dumps show IO 17–24 with no secondary tag (permanently GPIO). On the rig, each of the five switches flips one of `gpio.020`–`024` when pressed by hand | [08](08-zhulong-pinout-confirmed.md); OPEN-QUESTIONS (closed questions); `printpd`:94-109; [10](10-7i92-pinout-verified.md); [DURUM.md](DURUM.md) |
| — | Input voltage 24 V confirmed (opto P/N) | open (B6) | Not confirmed. On the test rig the switches work at 5 V | [DURUM.md](DURUM.md), B6 row |
