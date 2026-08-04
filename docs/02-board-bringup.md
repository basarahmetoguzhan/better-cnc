# 02 — Board Bring-Up Runbook

Field procedure for first power-on and identification of the **Zhulong V2.0**
(Mesa 7i96 clone, Spartan-6 XC6SLX9, Micrel KSZ8851 PHY).

**Goal of this session:** get the board to answer `mesaflash --readhmid`, and
determine which of three outcomes we are in. Nothing else. No motors, no LinuxCNC,
no config files.

**Prerequisite:** [00-upstream-reference-map.md](00-upstream-reference-map.md) read.
Pin map is [03-7i96-pinout.md](03-7i96-pinout.md).

---

## ⚠ On the limits of this document

We have **no vendor documentation for the Zhulong V2.0**. Everything in this runbook
falls into one of two categories, and they are labelled throughout:

- **Verified from source** — cited with file and line from `reference/linuxcnc/`.
  These claims are solid.
- **UNCONFIRMED** — inferred from the Mesa 7i96 it clones, or general practice. These
  may be wrong for our board. Where a wrong guess could damage hardware, the
  instruction is to *measure* rather than assume.

Do not let the confident tone of a numbered procedure obscure that distinction.

---

## Step 1 — Pre-power checks (board on the bench, nothing connected)

Do all of this **before** the power supply is switched on.

### 1.1 Photograph everything first

Every silkscreen, both sides of the board, all jumper positions, all connectors.
You will want these later and the board will be in an enclosure by then.

### 1.2 Identify the power input and confirm polarity — **measure, do not assume**

- **UNCONFIRMED:** the Mesa 7i96 takes nominal 24 V DC on a screw terminal. The Zhulong
  is expected to match, but confirm against its own silkscreen.
- Find the `+` / `−` (or `V+` / `GND`) markings on the power terminal. Photograph them.
- Set the bench supply to 24 V, **current-limit it to ~500 mA**, and measure the output
  leads with a meter *before* connecting them. Confirm polarity at the wire ends, not
  at the supply's front panel.
- **Reverse polarity is the single most likely way to kill this board in the next ten
  minutes.** Many Chinese clones omit the input protection diode that Mesa fits.

### 1.3 Current draw expectation

- **UNCONFIRMED.** A bare 7i96-class board with a Spartan-6 XC6SLX9 and a KSZ8851 PHY,
  with nothing connected to the IO, should draw on the order of **100–250 mA at 24 V**.
  This is an order-of-magnitude expectation, not a spec.
- What matters is the *shape* of the reading, not the exact number:
  - **Draws roughly the expected amount and stays steady** → proceed.
  - **Instantly hits your current limit** → shut off immediately. Short, reversed
    polarity, or a dead regulator.
  - **Draws near zero** → nothing is powering up. Check the terminal is actually
    making contact.
- Leave the current limit in place for the whole of this session. There is no reason
  to remove it until motors are involved, which is a much later step.

### 1.4 Identify the Ethernet RJ45 — this is where people lose an afternoon

A 7i96-class board has **more than one RJ45-shaped socket**, and they are not
interchangeable:

- **The Ethernet port** has **link/activity LEDs built into the jack**. This is the one
  the Pi plugs into.
- **The other RJ45 sockets are RS-422 Smart Serial ports.** They carry the SSerial
  TXDATA/RXDATA/TXEN signals — IO 30, 31, 32 in
  [03-7i96-pinout.md](03-7i96-pinout.md). They will never respond to a ping, and
  plugging the Pi into one produces exactly the same symptom as a dead board.

**UNCONFIRMED for the Zhulong:** that its Ethernet jack also has the LEDs. If none of
the jacks have visible LEDs, the LED-in-jack rule does not apply and you will have to
identify the port by tracing it to the KSZ8851 PHY chip.

Verified from source: the driver speaks **UDP on port 27181**
([hm2_eth.c:451](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L451)).

### 1.5 The W1 / W2 jumpers — **UNCONFIRMED, photograph before touching**

- On the Mesa 7i96 these are believed to select **boot behaviour and/or IP address
  mode** (e.g. whether the board comes up on its stored address or a fixed fallback).
- **We have no vendor documentation confirming this for either board, and none at all
  for the Zhulong.** The clone's jumpers may do something entirely different, or
  nothing.
- **Do not change them yet.** Photograph their as-received positions. If the board does
  not respond later, that photo lets you get back to the starting state.
- If you do end up experimenting: change **one jumper at a time**, power-cycle between
  each change, and write down what you tried. Do not shotgun it.

### 1.6 Do not connect anything else

No stepper drivers, no limit switches, no spindle, no VFD. TB1/TB2/TB3 stay empty for
this entire session. The only two things connected to the board are **power** and
**one Ethernet cable to the Pi**.

---

## Step 2 — Host network setup on the Pi

### 2.1 Why a dedicated interface

From the `hm2_eth` man page
([docs/man/man9/hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9)):

> hm2_eth should be used on a dedicated network interface, with only a cable between
> the PC and the board. Wireless and USB network interfaces are not suitable.
>
> Use of the dedicated ethernet interface while LinuxCNC is running can cause violation
> of realtime guarantees.

The reason is timing, not bandwidth. The driver exchanges a request/response packet
pair **every servo period** (1 ms in the sample configs). Any other traffic on that
interface — a DHCP renew, an mDNS burst, ARP for something else — lands in the same
queue and delays the response past the read timeout. The driver detects the loss and
raises `packet-error`, and past a threshold it faults out.

The man page also documents that the driver installs an **iptables chain called
`hm2-eth-rules-output`** while HAL is running, and that after a crash you can clear it
with `sudo iptables -F hm2-eth-rules-output`. Worth knowing before it confuses you.

**For this bring-up session none of that applies yet** — we are not loading LinuxCNC.
But set the interface up correctly now so we do not have to redo it.

### 2.2 Give the Pi a static address on the board's subnet

Find the interface the board is plugged into:

```bash
ip -brief link show
ip -brief address show
```

The board's own address determines what the Pi needs. Two documented possibilities:

| Board address | Pi address to use | Source |
|---|---|---|
| `192.168.1.121` | `192.168.1.1/24` | *"As shipped, the board address is 192.168.1.121"* — [hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9) |
| `10.10.10.10` | `10.10.10.1/24` | *"One common alternative is PC address 10.10.10.1, hostmot2 address 10.10.10.10"* — [hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9) |

**The Zhulong's factory address is UNCONFIRMED.** `scripts/detect-board.sh` tries four
candidates for exactly this reason. If none work, the address is something else and you
will need to find it — see the fallback note in 2.4.

The man page warns specifically about `192.168.1.0/24`:

> It is particularly important to check that the network 192.168.1/24 is not already the
> private network used by your internet router, because this is a commonly-used value.

If your house router uses `192.168.1.0/24`, use the `10.10.10.x` scheme instead and
plan to change the board's address later with mesaflash — **a later session, not this
one.**

Quick, non-persistent way to set the address for testing (replace `eth0`):

```bash
sudo ip address add 10.10.10.1/24 dev eth0
sudo ip link set eth0 up
```

Make it persistent later. For now, temporary is correct — it costs nothing to redo and
avoids committing to a scheme before we know the board's address.

The man page's persistent form, for reference:

```
auto eth1
iface eth1 inet static
    address 192.168.1.1
    hardware-irq-coalesce-rx-usecs 0
```

with the caveat, from the same page, that `hardware-irq-coalesce-rx-usecs 0` *"decreases
time waiting to receive a packet on most systems, but on at least some Marvel-chipset
NICs it is harmful."* Leave it out until we are tuning.

### 2.3 Verify link before anything else

```bash
ip -brief link show          # interface must be UP with a carrier
```

No carrier = cable, jack, or power problem. Stop and fix that; no amount of IP
configuration will help.

### 2.4 Ping the board — plain ping, no LinuxCNC

```bash
ping -c 3 10.10.10.10
```

If it answers, go to Step 3.

If it does not, try the other candidate addresses by hand, then check the hint list in
Step 3.2. If you still cannot find it, the address is unknown — you can watch for the
board's own traffic with `sudo tcpdump -i eth0 -n` while power-cycling it, or scan the
likely subnets with `arp-scan`. **UNCONFIRMED** whether the Zhulong emits anything
unprompted at power-on; many boards are silent until spoken to, in which case tcpdump
will show nothing and only a scan will find it.

---

## Step 3 — Run the detection script

### 3.1 Run it

```bash
cd ~/better-cnc          # wherever the repo lives on the Pi
./scripts/detect-board.sh                 # tries the four default addresses
./scripts/detect-board.sh 10.10.10.10     # or a specific one
```

The script is **strictly read-only** — it runs only `--readhmid` and `--info`, never
anything that writes flash. That guarantee is stated at the top of the script and is
the reason it exists as a separate file from anything that might one day write.

It saves every successful dump to:

```
docs/board-dumps/readhmid-<ip>-<timestamp>.txt
docs/board-dumps/info-<ip>-<timestamp>.txt
```

Exit codes: **0** found something, **1** found nothing (prints the hint list),
**2** usage error or mesaflash not installed.

One thing the script handles for you: **mesaflash's `--device` string.** mesaflash is
not vendored in `reference/` (see [00-upstream-reference-map.md](00-upstream-reference-map.md) §f),
so its exact accepted device strings could not be verified from source. The script tries
`ETHER` first and falls back to `7I96` automatically, reporting which one worked.
Override with `MESAFLASH_DEVICE=... ./scripts/detect-board.sh` if neither is right.

### 3.2 If it finds nothing

The script prints its own hint list. In summary: check 24 V and polarity, check you are
in the **Ethernet** RJ45 and not a Smart Serial port, check the W1/W2 jumpers against
your photo, check the Pi has an address on the same subnet, check link state.

### 3.3 What a healthy readhmid dump looks like

A good dump has three parts. **The exact wording and field labels below are
UNCONFIRMED** — they come from mesaflash, which we have not read the source of. Match on
meaning, not on exact strings.

1. **A board identification block** — board name, FPGA type, firmware/config name.
2. **An IDROM block** — IDROM type, IO port count, port width, IO width, clock
   frequencies, instance/register strides.
3. **A module inventory** — one line per HostMot2 module type present
   (IOPort, StepGen, Encoder, PWMGen, SSerial, SSR, WatchDog…) with the number of
   instances of each, followed by a per-pin listing.

Signs it is *not* healthy:

- Empty output with exit 0.
- A board name that is blank or full of unprintable characters.
- IO width that is not IO ports × port width — the driver rejects this outright
  ([hostmot2.c:698-706](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L698-L706)).
- No StepGen instances at all. We need at least 3.

### 3.4 Questions this dump must answer

Photographs of the Zhulong show it is **not** pin-compatible with a stock 7i96 — six
AXIS blocks, three encoder blocks, five inputs, an analog spindle output, two Smart
Serial ports, and no DB25/P1 at all. Full description and reasoning in
[04-zhulong-board-hardware.md](04-zhulong-board-hardware.md).

That means this dump is not a formality. It is **the only source of truth** for how our
board is wired. Work through these seven questions before doing anything else with it,
and write each answer into 04's resolution log.

#### Q1 — Does the reported board name start with `7I96`?

**Look for:** the board-name line in the identification block, and in `dmesg` after
loading the driver:

```
hm2_eth: discovered <name>
hm2_eth: Unrecognized ethernet board found: <name> -- port names will be wrong
```

**Why it matters:** determines the HAL prefix on every line of our HAL files, and
whether we are in Outcome A, B1, B2 or C. Transcribe it character for character —
case and trailing spaces are significant. Full decoding table in Step 5.2.

#### Q2 — IOPorts / PortWidth / IOWidth — do we get 3 × 17 = 51?

**Look for:** three fields in the IDROM block, worded roughly
`Number of IO Ports`, `Width of one I/O port`, `Total IO Width`.

**Why it matters:** the pivotal question, because the missing P1 means 17 stock pins go
nowhere and the vendor may have declared fewer ports. Our committed I/O comes to 36 pins
(arithmetic in 04), which fits in 51 but *not* in 34.

- `3 / 17 / 51` **and** name is `7I96` → Outcome A.
- Anything else **and** name is `7I96` → **Outcome C, hard abort.**
- Anything else with any other name → Outcome B, loads fine.

#### Q3 — Are the inputs really at global IO 20–24?

**Look for:** the five per-pin lines `IO Pin 020` … `IO Pin 024`.

- **Short form** (`IO Pin 020 (…): IOPort`) for all five → hypothesis **confirmed**,
  the silkscreen numbers are HAL IO numbers and we can use
  `hm2_….gpio.020.in` … `gpio.024.in` directly.
- **Long form** naming a module (`… StepGen #1, pin Step (Output)`) → **refuted**;
  scan the whole listing for the pins that *are* free and use those numbers instead.

**Why it matters:** decides whether the silkscreen can be trusted as a HAL reference, and
determines the pin names for every limit switch, home switch and E-stop input.
Reasoning and the physical confirmation procedure are in 04.

#### Q4 — How many StepGen instances, and at which IO numbers?

**Look for:** the StepGen entry in the module inventory (instance count), then every
per-pin line whose function is `StepGen`, noting its IO number, instance number, and
whether the signal is `Step` or `Dir`.

**Why it matters:** we need 3 axes; the board offers 6 connectors. This tells us the
instance→connector mapping, i.e. which `stepgen.NN` drives which `AXIS n` block. Do not
assume `AXIS 0` is `stepgen.00`. Also note that on the stock bitfile **Dir precedes Step**
in IO order — do not assume our vendor kept that convention.

#### Q5 — How many Encoder instances, and are the encoder pins exposed as Encoder or as GPIO?

**Look for:** the Encoder count in the module inventory, plus the per-pin lines for the
encoder terminals — long form (`Encoder #0, pin A`) vs short form (`IOPort`).

**Why it matters:** we are open-loop and need no encoders, so those 9 pins are candidate
GPIO — valuable, since only 5 dedicated inputs are brought out. Unclaimed pins become
full GPIO ([hostmot2.adoc:400-401](../reference/linuxcnc/docs/src/drivers/hostmot2.adoc#L400-L401)),
released via `num_encoders=` in the config string. **But** see Q4 in 04: FPGA-level
availability says nothing about the external circuitry behind the terminal.

#### Q6 — Is there a PWMGen for the 0-10V output, and where?

**Look for:** a PWMGen entry in the module inventory, and per-pin lines with function
`PWMGen` — note the instance number and whether each pin is the PWM or the direction
signal.

**Why it matters:** the stock `7i96d` has **zero** PWMGens, so any PWMGen here is direct
proof of a custom bitfile. It also decides how we drive the spindle: a real PWMGen means
`<B>.pwmgen.NN.value` into an on-board filter, whereas no PWMGen would mean the 0-10 V
comes from somewhere else entirely — or that the `CW` terminal is a plain GPIO and the
analog output is driven over Modbus/Smart Serial instead.

#### Q7 — How many Smart Serial ports does the firmware actually instantiate?

**Look for:** the SSerial entry in the module inventory (ports and channels), and any
`Device at ... channel N` lines. `mesaflash --sserial` reports this too.

**Why it matters:** the board has two Smart Serial jacks, but connectors are hardware and
instances are firmware — they need not match. This determines whether both jacks are
usable and whether the `sserial_port_0=` config option is relevant to us. The stock
`7i96d` instantiates 1 port / 1 channel.

---

## Step 4 — Record the dump in the repo

Commit the raw dump files. Then, from them, pull out these fields and record them.
Each one matters for a specific reason:

| Field | Why it matters |
|---|---|
| **Board name string** (exact, including case and any trailing spaces) | Decides the whole decision tree below. It sets the HAL prefix for every line of our HAL files, and whether the driver takes the exact-match or the fallback path. Transcribe it *character for character* — a trailing space is significant. |
| **IDROM type** | Must be **2 or 3** or the driver aborts ([hostmot2.c:668-674](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L668-L674)). |
| **IOPorts** (number of IO ports) | Must equal 3 on the exact-match path or the driver aborts. On the fallback path it becomes `num_ioport_connectors` directly. |
| **PortWidth** (width of one IO port) | Must equal 17 on the exact-match path or the driver aborts. Also the divisor that maps global IO number to connector: `port_num = i / port_width` ([pins.c:748](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L748)). |
| **IOWidth** | Must equal IOPorts × PortWidth. Should be 51. |
| **ClockLow / ClockHigh** | These set the timing base for stepgen `steplen`/`stepspace`/`dirsetup`/`dirhold` and for encoder velocity. Getting them wrong means step timing silently violates the drivers' requirements. Phase 0 found pncconf's data internally inconsistent here (33 vs 100 MHz) — **the board's own IDROM is the only trustworthy source.** |
| **Module inventory with instance counts** (StepGen, Encoder, PWMGen, SSerial, SSR, IOPort, WatchDog) | Determines what we can actually ask for in the `config=` string. `num_stepgens=3` fails if the bitfile provides fewer. Also tells us whether the spindle can be driven by PWM or must go over Modbus. |
| **The per-pin listing** | The real answer to "which terminal is Step 0". Diff it against [03-7i96-pinout.md](03-7i96-pinout.md) and record deltas in that file's verification log. |

Paste the whole dump into the repo — do not summarise it. Later questions will be
answered by grepping it.

---

## Step 5 — Decode the board name

Before the decision tree, understand the two things the name controls.

### 5.1 The matching rule

From [hm2_eth.c:1319](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1319):

```c
} else if (strncmp(board_name, "7I96", 8) == 0) {
```

`board_name` is a 16-byte buffer, zero-initialised, filled from the board's LBP16
board-info space ([hm2_eth.c:1082-1092](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1082-L1092)).
`strncmp` stops at a NUL in either operand, so comparing 8 bytes against the 5-byte
literal `"7I96"` means **byte 4 must be `\0`** — this is an exact-match test, not a
prefix test.

Consequences:

- `7I96` → matches.
- `7I96S` → does **not** match this branch (byte 4 is `S`), falls through to its own
  branch at [hm2_eth.c:1341](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1341).
- `7I96D`, `7I96 ` (trailing space), `7i96` (lowercase), `ZHULONG`, `MESA7I96` →
  no match, fallback path.

### 5.2 How the HAL prefix is built — this is what you actually need

Both paths converge on
[hm2_eth.c:1487](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1487):

```c
rtapi_snprintf(board->llio.name, sizeof(board->llio.name), "hm2_%.*s.%d",
               (int)strlen(llio_name), llio_name, llio_idx(llio_name));
```

so the prefix is `hm2_<llio_name>.<index>`, where the index is a per-name counter
starting at 0 ([hm2_eth.c:1073-1076](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1073-L1076)).

`llio_name` is built differently on each path:

| Path | Construction | Source |
|---|---|---|
| Exact match `7I96` | `strncpy(llio_name, board_name, 8)`, then `llio_name[1] = tolower(...)` | [hm2_eth.c:1320-1321](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1320-L1321) |
| Exact match `7I96S` | same, plus `llio_name[4] = tolower(...)` | [hm2_eth.c:1342-1344](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1342-L1344) |
| Fallback | `strncpy(llio_name, board_name, 4)`, then `llio_name[1] = tolower(...)` | [hm2_eth.c:1462-1463](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1462-L1463) |

**Only the first 4 characters survive on the fallback path, and only index 1 is
lowercased.** Worked examples:

| Reported name | Path | `llio_name` | **HAL prefix** |
|---|---|---|---|
| `7I96` | exact | `7i96` | `hm2_7i96.0` |
| `7I96S` | exact (S branch) | `7i96s` | `hm2_7i96s.0` |
| `7I96D` | fallback | `7i96` | `hm2_7i96.0` |
| `7I96 ` (trailing space) | fallback | `7i96` | `hm2_7i96.0` |
| `MESA7I96` | fallback | `MeSA` | `hm2_MeSA.0` |
| `ZHULONG` | fallback | `ZhUL` | `hm2_ZhUL.0` |
| `zhulong` | fallback | `zhul` | `hm2_zhul.0` |

Note the mixed case in the `MESA7I96` and `ZHULONG` rows — that is not a typo. Only
index 1 gets lowercased, so an all-caps name yields a deliberately odd-looking prefix.
**HAL names are case-sensitive; you must reproduce it exactly.**

### 5.3 The authoritative check — do not compute the prefix by hand

Everything above is a prediction. The board tells you the answer directly. Load just the
driver and look:

```bash
# nothing else running, board powered and pinging
halrun -I
halcmd: loadrt hostmot2
halcmd: loadrt hm2_eth board_ip="10.10.10.10"
halcmd: show pin           # the common prefix of every pin IS the answer
halcmd: exit
```

and in another terminal:

```bash
dmesg | tail -60
```

Two lines to look for, both from
[hm2_eth.c:1461,1485](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1461):

```
hm2_eth: discovered <name>
hm2_eth: Unrecognized ethernet board found: <name> -- port names will be wrong
```

The first always appears. The **second appears only on the fallback path** — its
presence or absence is the cleanest single indicator of which outcome you are in.

---

## Step 6 — THE DECISION TREE

```
                        readhmid dump in hand
                                 │
                   ┌─────────────┴─────────────┐
                   │  Board name exactly       │
                   │  "7I96" (4 chars, upper)? │
                   └─────────────┬─────────────┘
                    yes │                 │ no
                        ▼                 ▼
        ┌───────────────────────┐   ╔═══════════════╗
        │ IDROM: IOPorts == 3   │   ║  OUTCOME B    ║
        │ PortWidth == 17       │   ║  fallback     ║
        │ IOWidth  == 51 ?      │   ║  (works)      ║
        └───────────┬───────────┘   ╚═══════════════╝
             yes │        │ no
                 ▼        ▼
        ╔═══════════╗  ╔═══════════════╗
        ║ OUTCOME A ║  ║  OUTCOME C    ║
        ║ clean     ║  ║  HARD ABORT   ║
        ╚═══════════╝  ╚═══════════════╝
```

---

### ╔═══════════╗ OUTCOME A — clean path ╚═══════════╝

**Condition:** board name is exactly `7I96`, **and** the IDROM reports IOPorts = 3,
PortWidth = 17, IOWidth = 51.

**What you will see in dmesg:** `hm2_eth: discovered 7I96`, and **no** "Unrecognized"
line.

**What it means:**

- HAL prefix is **`hm2_7i96.0`**.
- Connector names are populated (`P1`, `TB1`, `TB2`) and the 51 pin labels from
  [03-7i96-pinout.md](03-7i96-pinout.md) appear in the dmesg pin listing, so you can
  read straight from terminal label to IO number.
- `num_leds = 4` ([hm2_eth.c:1339](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1339)),
  so `hm2_7i96.0.led.CR01` … `.CR04` exist.
- FPGA reported as `6slx9tqg144`, matching the physical Spartan-6.
- The strict IDROM checks passed, which means driver and firmware agree on geometry.

**Concrete next action:**

1. Record the module inventory (StepGen / Encoder / PWMGen / SSerial counts) — that is
   what bounds our `config=` string.
2. Diff the per-pin listing against [03-7i96-pinout.md](03-7i96-pinout.md) and fill in
   that file's verification log. **Even in Outcome A, do not assume the function map
   matches** — the labels come from the driver, but the *functions* come from the
   clone's bitfile.
3. Proceed to writing the INI/HAL with `BOARD=7i96` in `[HOSTMOT2]`, which makes the
   `hm2_[HOSTMOT2](BOARD).0.*` pattern in the stock sample HAL files work unmodified.

---

### ╔═══════════════╗ OUTCOME B — fallback path ╚═══════════════╝

**Condition:** board name is anything other than exactly `7I96` or `7I96S`. Examples we
consider likely for a clone: `7I96D`, `7I96 ` with trailing space, `MESA7I96`,
`ZHULONG`, a lowercase variant.

**What you will see in dmesg:**

```
hm2_eth: Unrecognized ethernet board found: <name> -- port names will be wrong
hm2_eth: discovered <name>
```

**This is not a failure. The board will work.** Read
[hm2_eth.c:1460-1483](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1460-L1483):
the driver reads the IDROM itself and takes its geometry from there —

```c
board->llio.num_ioport_connectors = idrom.io_ports;
board->llio.pins_per_connector    = idrom.port_width;
```

Because those values now come *from* the IDROM, the consistency checks in
`hm2_read_idrom()` compare the IDROM against itself and **cannot fail**. Outcome B is
therefore strictly safer than Outcome A with respect to load-time aborts.

**What degrades — all cosmetic:**

| | Outcome A | Outcome B |
|---|---|---|
| Connector names | `P1`, `TB1`, `TB2` | `"??"` for every port |
| Pin labels in dmesg | `TB1-04/TB1-05` etc. | `??-01`, `??-14`, `??-02` … (see below) |
| FPGA part number | `6slx9tqg144` | `"??"` |
| `num_leds` | 4 → `led.CR01`–`CR04` exist | **0 → no LED HAL pins at all** |
| StepGen / Encoder / PWMGen / GPIO | full function | **full function, unchanged** |

The LED pins vanishing is the only one with any practical bite, and only if we had
planned to drive the board LEDs from HAL. We had not.

**About those `??-NN` labels — they are actively misleading, so know what you are
looking at.** The fallback branch never assigns `io_connector_pin_names`, and `boards[]`
is static storage ([hm2_eth.c:456](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L456)),
so the pointer stays NULL and `hm2_print_pin_usage()` takes its *other* branch
([pins.c:878-880](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L878-L880)):

```c
snprintf(connector_pin_name, sizeof(connector_pin_name), "%s-%02d",
         hm2->llio->ioport_connector_name[pin->port_num], pin->port_pin);
```

`ioport_connector_name[]` is `"??"` for every port, and `port_pin` comes from the DB25
lookup table, because port width is 17
([pins.c:702,768-769](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/pins.c#L702)):

```c
const rtapi_u8 DB25[] = {1,14,2,15,3,16,4,17,5,6,7,8,9,10,11,12,13};
...
case 17:    /* 25 pin 17 I/O parallel port type cards funny DB25 order */
    pin->port_pin = DB25[i % 17];
```

So dmesg shows `??-01, ??-14, ??-02, ??-15, …` repeating every 17 pins — **DB25
connector numbering applied to all three ports, including the two that are terminal
blocks, not DB25s.** This is precisely what *"port names will be wrong"* in the warning
means. Ignore the label entirely on this path and work from the **IO Pin number**, which
is always correct.

**Determining the HAL prefix — the critical part.**

Two sub-cases:

**B1 — the name starts with `7I96`** (e.g. `7I96D`, `7I96 `, `7I96-V2`).

The fallback copies only 4 bytes, so `llio_name` becomes `7i96` and the prefix is
**`hm2_7i96.0`** — identical to Outcome A. Every HAL line we would have written for
Outcome A works unchanged. Set `BOARD=7i96` in `[HOSTMOT2]` and carry on. This is the
happy version of Outcome B.

**B2 — the name does not start with `7I96`** (e.g. `ZHULONG`, `MESA7I96`).

The prefix is something else entirely, derived from the first four characters with only
index 1 lowercased — `hm2_ZhUL.0`, `hm2_MeSA.0`. Then:

- **Every** HAL line referring to the board must use that prefix. In practice this is
  one INI line, because the stock sample HAL files already indirect through
  `hm2_[HOSTMOT2](BOARD).0.*`
  ([hm2-stepper-eth.hal:47](../reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/hm2-stepper-eth.hal#L47)).
  Set `BOARD=ZhUL` and the whole file follows.
- **Get the case exactly right.** HAL names are case-sensitive and the mixed case is
  real, not a transcription error.
- Do not hand-derive it. Read it off `halcmd show pin` per Step 5.3 and copy it.
- Write the exact string into this repo — put it in the pinout doc's verification log —
  because it will not be obvious to anyone reading our HAL files six months from now
  why the prefix looks like that. Add a comment in the INI too.

**Concrete next action (both sub-cases):**

1. Run the `halrun` snippet in Step 5.3 and copy the prefix verbatim from
   `show pin` output.
2. Record it in [03-7i96-pinout.md](03-7i96-pinout.md)'s verification log alongside the
   exact board name string.
3. Because the dmesg pin labels are wrong on this path (`??-NN` in DB25 order), the
   **only** trustworthy source for which terminal is which is the readhmid per-pin
   listing keyed by **IO number**, plus physical continuity testing. Budget time for
   that, and do not let a plausible-looking `??-04` fool you into thinking it means
   terminal 4.
4. Proceed to INI/HAL with `BOARD=<the four characters>`.

---

### ╔═══════════════╗ OUTCOME C — hard abort ╚═══════════════╝

**Condition:** board name is exactly `7I96` (so the driver hardcodes 3 connectors × 17
pins) **but** the IDROM reports different geometry.

**This is the dangerous outcome — the driver refuses to load and the error message does
not obviously point at the cause.**

**Exact strings to look for in `dmesg`.** All are `HM2_ERR`, which formats as
`hm2/<board>: <message>`
([hostmot2.h:53](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.h#L53),
`HM2_NAME` = `"hm2"` at [hostmot2.h:36](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.h#L36)) —
so in practice `hm2/hm2_7i96.0: …`:

```
hm2/hm2_7i96.0: invalid IDROM PortWidth %d, this board has %d pins per connector, aborting load
```
→ [hostmot2.c:692-696](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L692-L696).
The second `%d` will be `17`. **Followed by a full IDROM dump** — `hm2_print_idrom()` is
called on this path only, which is a gift: it prints every IDROM field. Capture it.

```
hm2/hm2_7i96.0: IDROM IOPorts is %d but llio num_ioport_connectors is %d, driver and firmware are inconsistent, aborting driver load
```
→ [hostmot2.c:708-715](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L708-L715).
The second `%d` will be `3`.

Three more aborts from the same function, which would indicate a genuinely malformed
IDROM rather than a mismatch:

```
hm2/…: invalid IDROM type %d, expected 2 or 3, aborting load
hm2/…: IDROM IOWidth is %d, but IDROM IOPorts is %d and IDROM PortWidth is %d (inconsistent firmware), aborting driver load
hm2/…: IDROM IOWidth is %d but max is %d, aborting driver load
hm2/…: IDROM ClockLow is %d, that's too low, aborting driver load
```
→ [hostmot2.c:672](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L672),
[:696-704](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L698-L706),
[:715-722](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L717-L724),
[:724-730](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L726-L732).

You will also see LinuxCNC report that the board failed registration, from
[hm2_eth.c:1504](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1504):

```
board fails HM2 registration
```

**What it means:** the clone reports Mesa's board name but carries a bitfile with a
different pin geometry. The driver trusts the name, hardcodes 3 × 17, and the firmware
disagrees.

**Concrete next action — and what NOT to do:**

1. **Capture the full IDROM dump** that accompanies the PortWidth error. It tells us the
   real geometry, which is the input to every option below.
2. **DO NOT FLASH ANYTHING.** Specifically: **a stock Mesa 7i96 bitfile is not safe on
   this board.** The clone's FPGA-to-connector routing is unknown and may differ. A
   bitfile that drives a pin as an output where the clone has an input — or worse,
   where the clone has a supply rail — can destroy the FPGA, the buffer, or whatever is
   wired to that terminal. The pin assignment is a property of the *board layout*, and
   we have no schematic.
3. **Get the vendor's own bitfile before considering any write.** Ask the seller for the
   `.bit`/`.bin` for the Zhulong V2.0 specifically. Until we have it, flashing is
   off the table entirely.
4. **Read the existing flash first, always.** Whatever is on the board now is our only
   known-working configuration, and it may be the only copy in existence. Read it out
   and commit it before any write is even discussed. (This needs a mesaflash read-flash
   operation, which is deliberately *not* in `detect-board.sh` — put it in its own
   script when the time comes.)
5. **There may be a software-side workaround worth trying first.** If the geometry
   mismatch is the only problem, forcing the driver down the *fallback* path avoids the
   abort entirely, because Outcome B takes geometry from the IDROM. That would mean
   changing what the board reports as its name — which is a flash write, so it lands
   back at point 3. Note it as an option for later, not something to attempt now.
6. Record everything in the repo and stop. Outcome C is where we need vendor
   information, and guessing is how boards die.

---

## Step 7 — Verifying step timing empirically

> **Do this before connecting a single stepper drive.** It is the last thing standing
> between a plausible-looking configuration and step pulses that silently violate your
> drives' timing specification.

### Why this cannot be checked in software

The driver converts every step-timing parameter from nanoseconds into FPGA clock counts
by multiplying by `clock_frequency`. All four, in `stepgen.c`:

| HAL parameter | Conversion | Line |
|---|---|---|
| `dirsetup` | `dir_setup_time_reg = dirsetup × (clock_frequency / 1e9)` | [348](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L348) |
| `dirhold` | `dir_hold_time_reg = dirhold × (clock_frequency / 1e9)` | [359](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L359) |
| `stepspace` | `pulse_idle_width_reg = stepspace × (clock_frequency / 1e9)` | [370](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L370) |
| `steplen` | `pulse_width_reg = steplen × (clock_frequency / 1e9)` | [381](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L381) |

And `clock_frequency` comes from the **module descriptor**, which resolves to a value out
of the **IDROM** ([hostmot2.c:780-785](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L780-L785),
[stepgen.c:773](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L773)):

```c
if (md->clock_tag == 1) {
    md->clock_freq = hm2->idrom.clock_low;
} else if (md->clock_tag == 2) {
    md->clock_freq = hm2->idrom.clock_high;
}
```

**The IDROM is just numbers the firmware reports about itself.** Nothing measures the
FPGA's actual clock. If the bitfile declares 33.33 MHz while the logic is clocked at
50 MHz, the driver has no mechanism — none — to notice.

**It is worse than merely unverified: the HAL parameter reads back as correct.** Look at
what happens in the normal, non-clamped path
([stepgen.c:380-389](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L380-L389)):

```c
static void hm2_stepgen_update_pulse_width(hostmot2_t *hm2, int i) {
    hm2->stepgen.pulse_width_reg[i] = (double)hm2->stepgen.instance[i].hal.param.steplen
                                      * ((double)hm2->stepgen.clock_frequency / (double)1e9);
    if (hm2->stepgen.pulse_width_reg[i] > 0x3FFF) {
        HM2_ERR("stepgen %d has invalid steplen, resetting to max\n", i);
        hm2->stepgen.pulse_width_reg[i] = 0x3FFF;
        hm2->stepgen.instance[i].hal.param.steplen = ...;   // only rewritten HERE
    }
    hm2->stepgen.instance[i].written_steplen = hm2->stepgen.instance[i].hal.param.steplen;
}
```

The HAL parameter is **only** recomputed when the value overflows `0x3FFF` and gets
clamped. In every normal case `steplen` keeps exactly the number you set. So
`halcmd getp hm2_….stepgen.00.steplen` returns `2000` because *you typed 2000* — it is
not a readback of anything. **There is no feedback path from the FPGA's actual pulse
width to the host.** A scope is the only instrument that can see the truth.

#### One thing that *is* self-correcting — and why it does not save you

The step **rate** register is also scaled by the same clock
([stepgen.c:325](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L325)):

```c
hm2->stepgen.step_rate_reg[i] =
    (uint32_t)(int32_t)(steps_per_sec_cmd * (4294967296.0 / (double)hm2->stepgen.clock_frequency));
```

But `position-fb` is derived from the hardware **accumulator**, which counts steps the
FPGA actually emitted
([stepgen.c:45,68,117-120](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L117-L120)):

```c
*(hm2->stepgen.instance[i].hal.pin.position_fb) =
    ((double)hm2->stepgen.instance[i].subcounts / 65536.0)
    / hm2->stepgen.instance[i].hal.param.position_scale;
```

So the *position* loop closes around reality and will drag the axis to the right place
even with a wrong clock — you would see degraded following behaviour, not silent
mis-positioning.

**The four timing parameters have no such loop.** They are written once and never
checked. That asymmetry is the whole point: the parameters that protect your drives are
precisely the ones with no feedback.

### The procedure

Board powered, **drives disconnected**, nothing able to move.

1. Load the driver and capture the declared clock (full procedure in
   [05-pi-setup.md](05-pi-setup.md) §e):

   ```bash
   halrun -I
   halcmd: loadrt hostmot2 debug_idrom=1 debug_modules=1
   halcmd: loadrt hm2_eth board_ip="10.10.10.10" config="num_stepgens=3"
   ```

   Note the line `hm2/<board>:     clock_frequency: NNNNNNNN Hz (NN.NN MHz)`
   ([stepgen.c:1225](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L1225)).

2. Set a **deliberately large, easily measured** pulse width — large enough that scope
   resolution and probe effects are irrelevant:

   ```
   halcmd: setp hm2_7i96.0.stepgen.00.steplen    100000    # 100 µs
   halcmd: setp hm2_7i96.0.stepgen.00.stepspace  100000    # 100 µs
   halcmd: setp hm2_7i96.0.stepgen.00.position-scale 1000
   halcmd: setp hm2_7i96.0.stepgen.00.maxvel     1
   halcmd: setp hm2_7i96.0.stepgen.00.enable     1
   halcmd: setp hm2_7i96.0.stepgen.00.control-type 1        # velocity mode
   halcmd: setp hm2_7i96.0.stepgen.00.velocity-cmd 0.001    # a slow trickle of steps
   ```

   100 µs is chosen so it is far below the `0x3FFF` clamp at either candidate clock —
   16383 counts is 491 µs at 33.33 MHz and 328 µs at 50 MHz — while being trivially
   measurable.

3. Put a scope or logic analyser on the **STEP** terminal of `AXIS 0`. Which terminal
   that is remains open —
   see [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) Q1. If the block
   turns out to be differential, measure across the pair, not to ground.

4. **Measure the high-time of one pulse.** Compare against the 100 000 ns you asked for.

5. Compute the ratio and cross-check against the declared clock:

   ```
   actual_clock ≈ declared_clock × (requested_width / measured_width)
   ```

   Within a few percent → the declared clock is honest, and every timing parameter can be
   trusted. A clean ratio like 1.5 or 0.667 → the clock is misdeclared by exactly that
   factor, and you now know the real one.

6. Record the measured width, the declared clock and the derived actual clock in
   `docs/board-dumps/` and in 04's resolution log.

### Worked example of the failure mode

Suppose the IDROM declares **33.33 MHz** but the FPGA logic actually runs at **50 MHz** —
plausible for us, since the Zhulong carries a **50.000 MHz crystal** (OBSERVED,
[04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)) while the stock 7i96 IDROM
declares 33 MHz / 200 MHz.

You configure a drive that needs a 2 µs minimum step pulse, so you set `steplen = 2000`.

**What the driver computes** ([stepgen.c:381](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L381)):

```
pulse_width_reg = 2000 ns × (33 333 333 Hz / 1e9)
                = 2000 × 0.033333333
                = 66.6667
                → 66        (pulse_width_reg is rtapi_u32 — truncated, not rounded)
```

**What the hardware does with 66 counts at its real 50 MHz clock:**

```
66 / 50 000 000 Hz = 1.32 × 10⁻⁶ s = 1320 ns
```

**Result: you asked for 2000 ns and the wire carries 1320 ns — 66 % of the requested
width, and 34 % below your drive's stated minimum.**

Meanwhile:

- `halcmd getp hm2_….stepgen.00.steplen` still returns **2000**.
- No warning is printed. The clamp branch never fires — 66 is nowhere near `0x3FFF`.
- `position-fb` tracks correctly, because the accumulator counts real steps.
- Nothing anywhere in LinuxCNC indicates a problem.

The failure appears only as **intermittent lost steps under load** — the drive
occasionally fails to register a pulse that is too short for its input filter. That looks
exactly like a mechanical problem, a noise problem, or a tuning problem, and you can lose
weeks to it.

**The same error in the other direction** — declared 50 MHz, actual 33.33 MHz — gives
`2000 × 0.05 = 100` counts, and `100 / 33 333 333 = 3000 ns`, i.e. **150 %** of what you
asked. Harmless for pulse-width compliance, but it silently caps your maximum step rate
at two thirds of what you calculated.

### If you have no scope

An oscilloscope or logic analyser is genuinely the right tool, and a usable USB logic
analyser costs less than one marble slab. But if you truly cannot get one:

1. **Record the declared `clock_frequency` anyway** — [05-pi-setup.md](05-pi-setup.md) §e.
   You cannot verify it, but you can at least know what the driver believes, and diff it
   against the IDROM's ClockLow/ClockHigh and the ClockTag
   ([hostmot2.c:818](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L818)).

2. **Note the crystal mismatch as an open risk.** 50.000 MHz observed vs 33/200 MHz
   declared by the stock board is not proof of anything — the FPGA may well synthesise
   33.33 MHz from a 50 MHz reference via a PLL, which is completely normal — but it means
   the question is live and unanswered.

3. **Set `steplen` and friends generously.** If your drive needs 2 µs, ask for 5 µs. A
   66 % error on 5 µs still delivers 3.3 µs, which clears the requirement. You pay in
   maximum step rate, which for a 3-axis marble router is unlikely to be the binding
   constraint. **This is mitigation, not verification.**

4. **Treat any step-loss symptom during commissioning as a suspect for this cause.**
   Write it down now, in this document, so that when an axis drifts by a few tenths after
   an hour of cutting, you check the clock *before* you re-shim the gantry. Specifically
   suspect it if: losses scale with feed rate, affect all axes equally, worsen with
   shorter `steplen`, or vanish when you increase `steplen` with nothing else changed.

**The last of those is a poor man's test.** If doubling `steplen` makes an intermittent
step-loss problem disappear, you have strong evidence the real pulse was shorter than the
driver thought. Not conclusive, but actionable.

---

## Step 8 — Do NOT do these

1. **Do not flash anything.** Not the vendor bitfile, not a Mesa bitfile, not a
   "recovery" image. Not in this session, not until we have read the existing flash and
   have the vendor's own file in hand. See Outcome C point 2 for why a stock Mesa 7i96
   bitfile is specifically unsafe here.

2. **Do not connect the board and the old Windows controller to the same step/dir lines
   at the same time.** Two drivers fighting over one signal line is a short between two
   push-pull outputs. It can damage both controllers and the stepper drivers. If the old
   controller is staying in the cabinet during the transition, physically disconnect its
   outputs — unplug them, do not just power it down. A powered-off output stage is not
   guaranteed to be high-impedance.

3. **Do not connect motor power during first bring-up.** Drives unpowered, motors
   unpowered, the whole 48 V/80 V bus off. The first time step pulses come out of this
   board, nothing should be able to move. A miswired direction pin or an inverted enable
   discovered at 2000 mm/min into a hard stop is an expensive lesson on a marble
   machine.

4. **Do not skip the current limit** on the bench supply. It costs nothing and it is the
   difference between "the board did not power up" and "the board is dead".

5. **Do not change W1/W2 without photographing them first.** We have no documentation
   for what they do; the as-received position is the only known-good state.

6. **Do not put the board's Ethernet on your house LAN**, even temporarily. Beyond the
   realtime concerns, we do not know what the clone's network stack does with
   unexpected traffic, and the KSZ8851 firmware is not something we can inspect.

7. **Do not write INI or HAL files until the decision tree is resolved.** The board name
   determines the HAL prefix used on every single line. Writing them first means
   rewriting them.

---

## Session checklist

- [ ] Board photographed, all sides, jumpers included
- [ ] 24 V polarity measured at the wire ends
- [ ] Supply current-limited to ~500 mA
- [ ] Power applied, current draw sane and steady
- [ ] Ethernet RJ45 identified (the one with LEDs in the jack)
- [ ] Pi has a static address on the board's subnet
- [ ] Link up with carrier
- [ ] Board answers plain `ping`
- [ ] `scripts/detect-board.sh` exits 0
- [ ] Dumps committed to `docs/board-dumps/`
- [ ] Board name string transcribed exactly, including case and trailing spaces
- [ ] IDROM type / IOPorts / PortWidth / IOWidth / ClockLow / ClockHigh recorded
- [ ] Module inventory with instance counts recorded
- [ ] Outcome A / B / C determined and written down
- [ ] HAL prefix confirmed from `halcmd show pin`, not derived by hand
- [ ] `docs/03-7i96-pinout.md` verification log filled in
- [ ] Nothing was flashed

### Before any drive is connected

- [ ] `clock_frequency` captured from dmesg with `debug_modules=1` (Step 7,
      [05-pi-setup.md](05-pi-setup.md) §e)
- [ ] StepGen `Clock Tag` noted (1 = ClockLow, 2 = ClockHigh)
- [ ] Step pulse width measured on a scope and compared against the requested value
      (Step 7) — **or**, if no scope is available, the mitigation in Step 7 applied and
      the open risk recorded
- [ ] Measured-vs-declared clock recorded in `docs/board-dumps/` and in
      [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)'s resolution log
