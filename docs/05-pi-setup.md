# 05 — Raspberry Pi Platform Setup

Getting the Pi to a state where it can talk to the Zhulong board. This is the
*platform* runbook — OS image, realtime kernel, latency validation, network. Board
bring-up itself is [02-board-bringup.md](02-board-bringup.md).

**Status: DERIVED.** Nothing here has been executed on real hardware.

Every technical claim is cited to `reference/`. Where the LinuxCNC source and docs do
**not** settle a question, that is stated explicitly — those are the points to confirm
against linuxcnc.org before acting.

---

## a) Image selection

### What the source settles definitively

**1. We need PREEMPT_RT, not RTAI. This is not a preference — it is the only option.**

From [docs/src/getting-started/system-requirements.adoc:72-82](../reference/linuxcnc/docs/src/getting-started/system-requirements.adoc#L72-L82):

> === Preempt-RT with 'linuxcnc-uspace' package
>
> […] Preempt-RT will generally have the best driver support and **is the only
> option for systems using the Mesa ethernet-connected hardware driver cards.**
> In general preempt-rt has the worst latency of the available systems, but there
> are exceptions.

Corroborated by the driver's own man page
([docs/man/man9/hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9)):

> hm2_eth is only available when LinuxCNC is configured with "uspace" realtime.

So: **PREEMPT_RT kernel + the `linuxcnc-uspace` package.** RTAI is ruled out for us
regardless of its better latency figures, and in any case
[getting-linuxcnc.adoc:256-259](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L256-L259)
lists RTAI as amd64-only — there is no ARM RTAI build.

**2. The package name is `linuxcnc-uspace`.** Identify which kernel is running with
`uname -a` and look for `-rt-` in the name
([system-requirements.adoc:55-67](../reference/linuxcnc/docs/src/getting-started/system-requirements.adoc#L55-L67)).

**3. Supported platforms**, from the table at
[getting-linuxcnc.adoc:253-260](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L253-L260):

| Distribution | Architecture | Kernel | Package | Use |
|---|---|---|---|---|
| Debian Trixie (13) | amd64 & **arm64** | preempt-rt | `linuxcnc-uspace` | machine control & simulation |
| Debian Bookworm (12) | amd64 & **arm64** | preempt-rt | `linuxcnc-uspace` | machine control & simulation |
| Debian Bullseye (11) | amd64 | preempt-rt | `linuxcnc-uspace` | machine control & simulation |
| Debian Trixie / Bookworm | amd64 only | RTAI | `linuxcnc` | machine control |
| Any | Any | stock | `linuxcnc-uspace` | **simulation only** |

(The source table has two cosmetic defects — a `Debian Troxie` typo and the header row
placed third. Content is unambiguous.)

**arm64 + preempt-rt + `linuxcnc-uspace` is a supported combination.** That is our
target.

**4. Minimum hardware**, from
[system-requirements.adoc:29](../reference/linuxcnc/docs/src/getting-started/system-requirements.adoc#L29):

> * 1.2 GHz 64-bit x86 processor or **Raspberry Pi 4 or better.**

**5. Use the LinuxCNC Pi image, not Raspberry Pi OS.** Two explicit warnings:

[getting-linuxcnc.adoc:47-49](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L47-L49):

> NOTE: Do not use the regular Raspbian distribution for LinuxCNC that may have shipped
> with your RPi starter kit - that will not have the real-time kernel and you cannot
> migrate from Raspbian to Debian's kernel image.

and, more bluntly,
[getting-linuxcnc.adoc:371-373](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L371-L373):

> === Installing on Raspbian 12
>
> **Don't do that.** The latencies are too bad with the default kernel and the
> PREEMPT_RT (the RT is important) kernel of Debian does not boot on the Pi
> (as of 1/2024).

**6. The Pi image is a full SD-card image**, not a hybrid ISO
([getting-linuxcnc.adoc:117-120](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L117-L120)):

> The Raspbery Pi image is a complete SD card image and should be written to an SD card
> with the Raspberry Pi Imager App. Note that the imager app can open the .zip file
> directly, no need to expand.

**7. There are separate images for Pi 4 and Pi 5**
([getting-linuxcnc.adoc:44-45](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L44-L45)):

> For the Raspberry Pi, multiple images are provided to address differences between the
> RPi4 and RPi5.

**Pick the image matching your exact Pi model.** The source does not say what happens if
you use the wrong one, and we have no reason to find out.

### What the source does NOT settle — confirm on linuxcnc.org

⚠️ **The exact current image version.** This clone is at
**VERSION 2.9.10** (`reference/linuxcnc/VERSION`, and
`linuxcnc (1:2.9.10) UNRELEASED` in `debian/changelog`), but the *documentation* in the
same tree still names **2.9.8** throughout its download examples — e.g.
[getting-linuxcnc.adoc:42](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L42)
gives `linuxcnc_2.9.8-amd64.hybrid.iso`. The docs lag the branch.

**Neither number is necessarily what is on the download page today.** Check
<https://linuxcnc.org/downloads/> yourself.

⚠️ **The arm64 image checksums.** The tree lists, at
[getting-linuxcnc.adoc:99-101](../reference/linuxcnc/docs/src/getting-started/getting-linuxcnc.adoc#L99-L101):

```
arm64 (Pi)
md5sum: 4547e8a72433efb033f0a5cf166a5cd2
sha256sum: ff3ba9b8dfb93baf1e2232746655f8521a606bc0fab91bffc04ba74cc3be6bf0
```

These correspond to whatever release the docs were written against — **almost certainly
not the image you will download.** Verify against the checksum published next to the
actual download, not against these.

⚠️ **Which Debian base the current Pi image uses.** The table permits both Bookworm and
Trixie on arm64. The source does not state which one the shipped Pi image is built on.
Determine it after flashing:

```bash
cat /etc/os-release
uname -a          # must contain "-rt"
uname -v          # must report PREEMPT RT
dpkg -l | grep linuxcnc
```

⚠️ **Whether the Pi image ships `mesaflash`.** [00-upstream-reference-map.md](00-upstream-reference-map.md) §f
established that mesaflash is a separate upstream project, not vendored in this tree, and
there is no `mesaflash(1)` man page here. `scripts/detect-board.sh` checks for it and
exits 2 with install guidance if absent.

### Summary

| Item | Value | Confidence |
|---|---|---|
| Realtime kernel | **PREEMPT_RT** | **Settled by source** — only option for Mesa Ethernet cards |
| LinuxCNC package | **`linuxcnc-uspace`** | **Settled by source** |
| Architecture | **arm64** | **Settled by source** — supported on Bookworm and Trixie |
| Minimum board | **Raspberry Pi 4** | **Settled by source** |
| Image type | Full SD-card image via Raspberry Pi Imager | **Settled by source** |
| Pi 4 vs Pi 5 image | Separate images — match your hardware | **Settled by source** |
| Debian base | Bookworm **or** Trixie | **Not settled** — check after flashing |
| LinuxCNC version | tree says 2.9.10, docs say 2.9.8 | **Not settled** — check linuxcnc.org |
| Image checksum | listed values are stale | **Not settled** — use the published checksum |

---

## b) Latency validation

### The three tools

All in `reference/linuxcnc/scripts/`, documented at
[docs/src/install/latency-test.adoc](../reference/linuxcnc/docs/src/install/latency-test.adoc)
with man pages in `docs/man/man1/`:

| Tool | What it does |
|---|---|
| `latency-test` | GUI showing max jitter for a base and a servo thread |
| `latency-plot` | Strip chart over time — good for spotting periodic spikes |
| `latency-histogram` | Distribution of jitter — shows *how often* excursions happen |

**Stop HAL first.** From
[latency-test.adoc:173](../reference/linuxcnc/docs/src/install/latency-test.adoc#L173):

> Linuxcnc and Hal should not be running, stop with `halrun -U`.

### What "latency" means here

From [latency-test.adoc:12-13](../reference/linuxcnc/docs/src/install/latency-test.adoc#L12-L13):

> Latency is how long it takes the PC to stop what it is doing and respond to an
> external request, such as running one of LinuxCNC's periodic realtime threads.

Two threads get measured:

- **Base thread** (default 25 µs) — the fast thread that exists *only* to generate step
  pulses in software.
- **Servo thread** (default 1 ms) — motion planning, PID, and — for us — the hm2_eth
  packet exchange.

### Which number matters for us — and which does not

The LinuxCNC docs are written almost entirely for software step generation. From
[latency-test.adoc:87](../reference/linuxcnc/docs/src/install/latency-test.adoc#L87):

> The important number for **software stepping** is the 'max jitter' of the **base
> thread**.

**We do not generate steps in software.** The Zhulong's FPGA has hardware StepGen
modules; the host only sends a position or velocity command once per servo period, and
the FPGA produces the pulse train from its own clock. So:

> ## ⚠️ For our machine, the base thread is irrelevant. Run the servo thread only.

```bash
halrun -U                                   # make sure nothing else is running
latency-histogram --nobase --servo 1000000
```

`--nobase` is documented as "servo thread only" at
[latency-test.adoc:168](../reference/linuxcnc/docs/src/install/latency-test.adoc#L168)
and [latency-test.adoc:175](../reference/linuxcnc/docs/src/install/latency-test.adoc#L175).
For a headless Pi, `--nox` prints `elapsed, min, max, sdev` per thread as text
([latency-test.adoc:170](../reference/linuxcnc/docs/src/install/latency-test.adoc#L170)).

### Why our jitter budget is far looser than a parallel-port machine's

This is the part worth internalising, because it changes what counts as "good enough".

**On a software-stepping machine**, every step pulse edge is emitted by the base thread.
Jitter directly modulates the *timing of individual pulses*. A 20 µs late base thread on
a 25 µs period means a pulse that should have been 25 µs wide is nearly double that — the
step train is physically distorted, and at high step rates the thread can miss its
deadline entirely and drop steps. Jitter is therefore in the *signal path*, and the
maximum usable step rate is set directly by it.

**On our hardware-stepgen machine**, the servo thread does something completely
different: it writes a velocity/position command into an FPGA register and reads back an
accumulator. The FPGA generates every pulse from its own crystal, with **zero** dependence
on host timing. Host jitter does not reach the step pulses at all.

What host jitter *can* do is make the servo thread late enough that the Ethernet
round-trip misses its deadline. The relevant mechanism is the driver's read timeout
([hm2_eth.c:914-920](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L914-L920)):

```c
long read_timeout = board->hal ? board->hal->read_timeout : 1600000;
if(read_timeout <= 0)   // less than or equal to 0, use 80% of the thread period.
    read_timeout = 80;
if(read_timeout < 100)  // less than 100 is interpreted as a percentage of the thread period.
    read_timeout = rtapi_div_s64(read_timeout * (unsigned long long)board->llio.period, 100);
if(read_timeout < 100000)  // Interpret as nanoseconds
    read_timeout = 100000;
```

The default is `80`, i.e. **80 % of the thread period**
([hm2_eth.c:1524](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1524)).
At a 1 ms servo period that is a **800 µs** budget for the board to answer.

So the practical question is not "is jitter under 20 µs" but **"does jitter plus network
round-trip stay under ~800 µs"** — a budget roughly *forty times* larger than the
software-stepping threshold. And a single overrun is not fatal: it asserts `packet-error`
and increments `packet-error-level`, which must reach `packet-error-limit` (default **10**,
[hm2_eth.c:1532](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c#L1532))
before the driver declares a permanent I/O error.

### Thresholds

The LinuxCNC docs give exactly one set of numbers, at
[latency-test.adoc:99-108](../reference/linuxcnc/docs/src/install/latency-test.adoc#L99-L108):

> If your Max Jitter number is less than about 15-20 microseconds […] the computer should
> give very nice results with **software stepping**. If the max latency is more like 30-50
> microseconds, you can still get good results, but your maximum step rate might be a
> little disappointing […] If the numbers are 100 us or more […] then the PC is **not a
> good candidate for software stepping**. Numbers over 1 millisecond (1,000,000
> nanoseconds) mean the PC is **not a good candidate for LinuxCNC, regardless of whether
> you use software stepping or not.**

Read that carefully. **The first three thresholds are all explicitly about software
stepping and do not apply to us.** Only the last sentence is unconditional.

> ### ⚠️ The LinuxCNC docs in `reference/` do not state a servo-thread jitter threshold for hardware-stepgen Ethernet setups.
>
> There is no such number in this tree. Anyone who quotes you one is quoting folklore or
> the linuxcnc.org forum, not the documentation. What follows is **our own reasoning from
> the driver's timeout arithmetic**, labelled as such.

| Servo-thread max jitter | Assessment | Basis |
|---|---|---|
| **> 1 ms** | **Unusable.** Stop. | Documented, unconditional — [latency-test.adoc:107-109](../reference/linuxcnc/docs/src/install/latency-test.adoc#L107-L109) |
| **> 800 µs** | Exceeds the default read deadline at a 1 ms period; expect constant packet errors | **Our inference** from `read_timeout = 80 %` |
| **100 µs – 800 µs** | Inside budget but uncomfortably close; leaves little room for network delay | **Our inference** |
| **< 100 µs** | Comfortable. Roughly 8× margin on the read deadline | **Our inference** |

**Our working acceptance criterion: servo-thread max jitter under 100 µs, sustained over
a long abused run.** That is a judgement call, not a documented figure — chosen to leave
an order of magnitude of headroom on the 800 µs deadline.

If it lands between 100 µs and 800 µs, the machine is not necessarily unusable, but
budget time for the tuning in
[latency-test.adoc:225-256](../reference/linuxcnc/docs/src/install/latency-test.adoc#L225-L256):
`isolcpus`, `irqaffinity`, `rcu_nocbs`, `nohz_full`, and
`sysctl.kernel.sched_rt_runtime_us = -1`. The stated goal there is

> to reserve a CPU for the exclusive use of LinuxCNC's realtime tasks.

### Run it properly

From [latency-test.adoc:76-82](../reference/linuxcnc/docs/src/install/latency-test.adoc#L76-L82):

> While the test is running, you should 'abuse' the computer. Move windows around on the
> screen. Surf the web. Copy some large files around on the disk. Play some music. Run an
> OpenGL program such as glxgears.

and [latency-test.adoc:89-96](../reference/linuxcnc/docs/src/install/latency-test.adoc#L89-L96):

> You should run the test for at least several minutes; sometimes the worst case latency
> doesn't happen very often […] one Intel motherboard worked pretty well most of the
> time, but every 64 seconds it had a very bad 300 us latency.

**Run for at least 30 minutes under load.** A quiet two-minute run proves nothing. Save
the result:

```bash
latency-histogram --nobase --servo 1000000 --nox 2>&1 | tee docs/board-dumps/latency-$(date +%Y%m%d-%H%M%S).txt
```

---

## c) Network configuration for hm2_eth

### What the docs actually say — and what they don't

> ### ⚠️ There is no hm2_eth network guidance in `docs/src/`.
>
> Searching the whole asciidoc tree finds only a component-index entry and an unrelated
> haltcl example. **All authoritative network guidance for this driver lives in the nroff
> man page** [docs/man/man9/hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9),
> which is quoted throughout below.

**On MTU and offload settings specifically:** grepping the entire `docs/` tree for
`ethtool`, `coalesce`, `MTU`, `offload` and `jumbo` returns **three hits, all in
`hm2_eth.9`, and none of them about MTU or offload.** The only network-tuning knob
LinuxCNC documents is `hardware-irq-coalesce-rx-usecs`.

That absence is consistent with the protocol: LBP16 packets are tiny —
`LBP16_MAX_PACKET_DATA_SIZE` is `0x7F` = **127 bytes**
([lbp16.h:34](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/lbp16.h#L34)) — so a
standard 1500-byte MTU is never a constraint and jumbo frames buy nothing. **Leave MTU at
the default.** Any advice to change it is not from the LinuxCNC docs.

### Dedicated interface — why

From [hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9):

> hm2_eth should be used on a dedicated network interface, with only a cable between the
> PC and the board. Wireless and USB network interfaces are not suitable.
>
> Use of the dedicated ethernet interface while LinuxCNC is running can cause violation
> of realtime guarantees.

The mechanism, concretely: the driver exchanges a request/response pair **every servo
period**, and must get its answer inside the 800 µs read deadline computed above. Any
other traffic on that interface — a DHCP renew, mDNS, an ARP for something else, an
`apt` download — queues ahead of the response. That is a missed deadline, a
`packet-error`, and ten of those in a row is a hard I/O error.

**A USB-Ethernet dongle is explicitly unsuitable.** On a Pi 4 the built-in NIC is a
genuine PCIe device — use it, and put any general-purpose networking on Wi-Fi instead.
Note that this means the Pi's *only* wired port is committed to the board.

The driver also installs an iptables chain while HAL runs:

> hm2_eth uses an iptables chain called "hm2-eth-rules-output. […] At (normal) exit,
> hm2_eth will remove the rules. After a crash, you can manually clear the rules with
> `sudo iptables -F hm2-eth-rules-output`; the rules are also removed by a reboot.

Worth knowing before it confuses you after a crash.

### Static address

The board's address determines the Pi's. Documented options, both from
[hm2_eth.9](../reference/linuxcnc/docs/man/man9/hm2_eth.9):

| Board | Pi | Note |
|---|---|---|
| `192.168.1.121` | `192.168.1.1/24` | *"As shipped, the board address is 192.168.1.121"* |
| `10.10.10.10` | `10.10.10.1/24` | *"One common alternative is PC address 10.10.10.1, hostmot2 address 10.10.10.10"* |

**The Zhulong's factory address is UNVERIFIED** — see
[04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) Q3.

The man page warns specifically about the first option:

> It is particularly important to check that the network 192.168.1/24 is not already the
> private network used by your internet router, because this is a commonly-used value.

**Prefer `10.10.10.x`** unless the board turns out to insist otherwise.

#### Temporary, for testing

```bash
sudo ip address add 10.10.10.1/24 dev eth0
sudo ip link set eth0 up
ip -brief address show
```

Gone on reboot. Correct for bring-up — do not commit to a scheme before Q3 is answered.

#### Persistent — NetworkManager

The LinuxCNC Pi image's network stack is **UNVERIFIED** (we have not run it). Debian 12/13
desktop installs normally use NetworkManager. Check with
`systemctl is-active NetworkManager`, then:

```bash
# create a dedicated static profile for the board link
sudo nmcli connection add type ethernet ifname eth0 con-name mesa \
     ipv4.method manual ipv4.addresses 10.10.10.1/24

# no gateway, no DNS - this link goes nowhere but the board
sudo nmcli connection modify mesa ipv4.never-default yes
sudo nmcli connection modify mesa ipv6.method disabled

# no route to the internet, so don't wait for one at boot
sudo nmcli connection modify mesa connection.autoconnect yes

sudo nmcli connection up mesa
nmcli -f NAME,DEVICE,STATE connection show
```

`ipv4.never-default yes` matters: without it NetworkManager may install a default route
via a link that reaches nothing, breaking general connectivity. Disabling IPv6 removes
router-solicitation and multicast chatter from an interface that must stay quiet.

#### Persistent — `/etc/network/interfaces`

If the image uses `ifupdown` instead, the man page gives the form directly:

```
auto eth1
iface eth1 inet static
    address 192.168.1.1
    hardware-irq-coalesce-rx-usecs 0
```

with its own caveat:

> "hardware-irq-coalesce-rx-usecs" decreases time waiting to receive a packet on most
> systems, but on at least some Marvel-chipset NICs it is harmful. If the line does not
> improve system performance, then remove it. A reboot is required for the value to be
> set back to its power-on default. This requires the ethtool package to be installed.

**Whether this helps on the Pi's NIC is UNVERIFIED.** Treat it as a tuning experiment
*after* a baseline works, not part of initial setup. Measure with and without.

### Quieten the interface

Not documented by LinuxCNC — **our inference** from the dedicated-interface requirement.
Worth doing, but verify each against the running system rather than pasting blindly:

- No DHCP client on that interface (the static profile above handles it).
- IPv6 disabled on it.
- No mDNS/Avahi advertising on it, if Avahi is installed.

---

## d) Verification checklist — before LinuxCNC is involved at all

Work top to bottom. **Do not skip ahead**; each step's failure mode is masked by the next.

### Platform

- [ ] `uname -a` contains `-rt`
- [ ] `uname -v` reports `PREEMPT RT`
- [ ] `cat /etc/os-release` — record the Debian base
- [ ] `dpkg -l | grep linuxcnc` shows **`linuxcnc-uspace`** (not `linuxcnc`)
- [ ] Image matches the Pi model (Pi 4 image on a Pi 4, Pi 5 image on a Pi 5)
- [ ] `which mesaflash` succeeds, or mesaflash has been installed separately

### Latency

- [ ] `halrun -U` run first
- [ ] `latency-histogram --nobase --servo 1000000` run for **≥ 30 minutes under load**
- [ ] Servo-thread max jitter recorded
- [ ] Jitter **< 1 ms** — hard requirement, documented
- [ ] Jitter **< 100 µs** — our working target
- [ ] Result saved to `docs/board-dumps/latency-<timestamp>.txt`

### Physical link

- [ ] Board powered, current draw sane (see [02-board-bringup.md](02-board-bringup.md) §1.3)
- [ ] Cable in the **Ethernet** RJ45 — the one with LEDs in the jack — **not** a Smart
      Serial port ([04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) connector 18)
- [ ] Link LEDs lit at both ends
- [ ] `ip -brief link show` — interface `UP`, carrier present

### Network

- [ ] Pi has a static address on the board's subnet
- [ ] `ip -brief address show` confirms it
- [ ] `ip route get <board-ip>` resolves via the right interface
- [ ] No DHCP client, no IPv6, no default route on that interface

### Board responds

- [ ] `ping -c 3 <board-ip>` answers
- [ ] `./scripts/detect-board.sh` exits **0**
- [ ] `readhmid` and `info` dumps are in `docs/board-dumps/`
- [ ] Dumps committed
- [ ] The seven questions in [02-board-bringup.md](02-board-bringup.md) §3.4 answered
- [ ] Outcome A / B / C determined

**Only when every box is ticked does it make sense to load LinuxCNC.**

---

## e) Record the stepgen `clock_frequency`

> This is a small step that prevents a genuinely nasty class of bug. Do not skip it.

### The step

The driver prints the clock frequency each HostMot2 module uses — **but only when
`debug_modules` is enabled.** From
[hostmot2.c:1672-1675](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L1672-L1675):

```c
if (debug_modules) {
    HM2_PRINT("HM2 Modules used:\n");
    hm2_print_modules(hm2);
}
```

`debug_modules` is a module parameter of `hostmot2`, default 0
([hostmot2.c:53-54](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L53-L54)).
So load with it on, and with `debug_idrom` too — that one gates the IDROM dump at
[hostmot2.c:742-744](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L742-L744):

```bash
halrun -U
sudo dmesg -C                          # clear, so the capture is clean

halrun -I
halcmd: loadrt hostmot2 debug_idrom=1 debug_modules=1
halcmd: loadrt hm2_eth board_ip="10.10.10.10"
halcmd: exit

dmesg > docs/board-dumps/dmesg-hm2-$(date +%Y%m%d-%H%M%S).txt
grep -i 'clock' docs/board-dumps/dmesg-hm2-*.txt
```

The line to capture comes from
[stepgen.c:1225](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L1225):

```c
HM2_PRINT("    clock_frequency: %d Hz (%s MHz)\n",
          hm2->stepgen.clock_frequency, hm2_hz_to_mhz(hm2->stepgen.clock_frequency));
```

`HM2_PRINT` prefixes `hm2/<board>: `
([hostmot2.h:51](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.h#L51)), so
you are looking for something like:

```
hm2/hm2_7i96.0: StepGen: 6
hm2/hm2_7i96.0:     clock_frequency: 33333333 Hz (33.33 MHz)
```

**Record that number in `docs/board-dumps/` and in
[04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)'s resolution log.**

Also capture the per-module `Clock Tag` line from
[hostmot2.c:818](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L818),
because the stepgen clock is *not* automatically ClockLow — the module descriptor chooses
([hostmot2.c:780-785](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hostmot2.c#L780-L785)):

```c
if (md->clock_tag == 1) {
    md->clock_freq = hm2->idrom.clock_low;
} else if (md->clock_tag == 2) {
    md->clock_freq = hm2->idrom.clock_high;
}
```

ClockTag 1 → ClockLow, ClockTag 2 → ClockHigh. Note which one StepGen uses.

### Why this number matters

**All four step-timing parameters are converted to register counts by multiplying by this
clock.** Every one, in `stepgen.c`:

| HAL parameter | Conversion | Line |
|---|---|---|
| `dirsetup` | `dir_setup_time_reg = dirsetup × (clock_frequency / 1e9)` | [348](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L348) |
| `dirhold` | `dir_hold_time_reg = dirhold × (clock_frequency / 1e9)` | [359](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L359) |
| `stepspace` | `pulse_idle_width_reg = stepspace × (clock_frequency / 1e9)` | [370](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L370) |
| `steplen` | `pulse_width_reg = steplen × (clock_frequency / 1e9)` | [381](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L381) |

The step *rate* too, via a different route
([stepgen.c:325](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L325)):

```c
hm2->stepgen.step_rate_reg[i] =
    (uint32_t)(int32_t)(steps_per_sec_cmd * (4294967296.0 / (double)hm2->stepgen.clock_frequency));
```

And the power-on defaults are derived from it as well — all four are initialised to the
maximum register value `0x3FFF` scaled back into nanoseconds
([stepgen.c:1169-1172](../reference/linuxcnc/src/hal/drivers/mesa-hostmot2/stepgen.c#L1169-L1172)).

**`clock_frequency` is taken from the IDROM. The driver has no way to check it.** If the
firmware declares a clock it does not actually run at, every timing parameter is wrong by
that ratio, silently. The full argument, with worked arithmetic and the measurement
procedure, is in
[02-board-bringup.md](02-board-bringup.md) → **"Verifying step timing empirically"**.

Given that the Zhulong carries a **50.000 MHz crystal** (OBSERVED) while the stock 7i96
IDROM declares 33 MHz / 200 MHz, this is not a hypothetical concern for us.

---

## Next

[02-board-bringup.md](02-board-bringup.md) — power-on, identification, the A/B/C decision
tree, and step-timing verification.
