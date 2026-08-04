# 00 — Upstream Reference Map

Reconnaissance of the read-only upstream clones in `reference/`. Everything here was
read out of the trees listed below; nothing was inferred from outside sources. Where
something is **not present**, that is stated explicitly rather than guessed at.

All paths are relative to `better-cnc/`.

| Clone | Path | Branch | Commit | Size |
|---|---|---|---|---|
| LinuxCNC | `reference/linuxcnc` | `2.9` | `18c5bb5` | 340 MB |
| hostmot2-firmware | `reference/hostmot2-firmware` | `master` | `c15c0c3` | 5.9 MB |

Branch `2.9` existed on the remote, so no fallback to a `2.9.x` branch was needed.

---

> ### ⚠ Forward reference — the stock 7i96 pinout does NOT apply to our hardware
>
> This document was written before we had photographs of the actual board. It describes
> **upstream LinuxCNC and the stock Mesa 7i96**, and everything in it about the *source
> tree* remains accurate and cited.
>
> But we have since established that the **Zhulong V2.0 is not pin-compatible with a
> stock 7i96**. It has six AXIS blocks (stock: five step/dir), three encoder blocks
> (stock: one), a 0–10 V analog spindle output (stock `7i96d`: no PWMGen at all), two
> Smart Serial ports (stock: one), five inputs (stock: eleven) — and **no DB25/P1
> connector**, so the 17 GPIO pins at IO 34–50 discussed below do not exist on our board.
>
> A bitfile that instantiates 5 StepGens cannot drive 6 axis connectors, so **our board
> necessarily runs vendor-custom firmware** and its pin assignment is unknown until read
> from the hardware.
>
> **In particular, the module counts in §e — 5 StepGens, 1 Encoder, 0 PWMGens,
> 1 SmartSerial port — describe Mesa's `7i96d`, not ours.** They are still the right
> baseline for understanding the upstream data, and §e's caveats about pncconf's
> internally inconsistent GPIO and clock figures stand.
>
> - **Our board:** [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md)
> - **Stock pin map, with the same warning:** [03-7i96-pinout.md](03-7i96-pinout.md)
> - **How we resolve the unknowns:** [02-board-bringup.md](02-board-bringup.md)

---

## a) Exact LinuxCNC version

**2.9.10**, unreleased (i.e. the 2.9 stable branch tip, past the 2.9.9 release).

`reference/linuxcnc/VERSION`:

```
2.9.10
```

`reference/linuxcnc/debian/changelog` (first line):

```
linuxcnc (1:2.9.10) UNRELEASED; urgency=medium
```

The Debian epoch is `1:`, so the full package version is `1:2.9.10`. Note the
`UNRELEASED` distribution field — this branch tip is *ahead of* the last tagged 2.9
release. Whatever we install on the Pi from the official image/apt will most likely be
a released 2.9.x, so treat this tree as "2.9.10-or-slightly-newer-than-what-we-run".

---

## b) Sample configs mentioning 7i96 / 7i96s

Three directories, all under `reference/linuxcnc/configs/by_interface/mesa/`. **Every
7i96-family sample is for the 7i96S, not the original 7i96** — there is no `7i96.ini`
anywhere in the tree.

An important structural point: these directories share one HAL file across all boards.
The board-specific part is *only* the `.ini`, and the HAL file refers to the board
through the INI variable `[HOSTMOT2]BOARD`:

```hal
loadrt [HOSTMOT2](DRIVER) board_ip=[HOSTMOT2](BOARD_IP) config=[HOSTMOT2](CONFIG)
addf hm2_[HOSTMOT2](BOARD).0.read   servo-thread
net emcmot.00.pos-cmd => hm2_[HOSTMOT2](BOARD).0.stepgen.00.position-cmd
```

— `reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/hm2-stepper-eth.hal:38,47,74`

That indirection is directly useful to us: if our Zhulong clone enumerates under a
different HAL prefix, we change one INI line, not the whole HAL file.

### 1. `reference/linuxcnc/configs/by_interface/mesa/hm2-stepper/`

3-axis **open-loop stepper** mill (XYZ, trivkins, 3 joints). Step/dir only — no
encoders, no PWM, no limit switches. This is the closest starting point for our
machine.

Files:

```
3x20-small.ini   4i65.ini   4i68.ini   5i20.ini   5i22-big.ini   5i22-small.ini
5i23.ini   7i43-big.ini   7i43-small.ini   7i90-rpi-spi.ini   7i90.ini   7i93.ini
7i96s.ini   README   README_es
hm2-stepper-eth.hal   hm2-stepper-rpspi.hal   hm2-stepper.hal   tool.tbl
```

`7i96s.ini`:

```ini
[HOSTMOT2]
DRIVER=hm2_eth
BOARD_IP=192.168.1.121
BOARD=7i96s
CONFIG="num_encoders=0 num_pwmgens=0 num_stepgens=3"
```

```ini
COORDINATES = X Y Z
KINEMATICS  = trivkins
JOINTS      = 3
HALFILE     = hm2-stepper-eth.hal
```

`README`: *"This configuration drives a 3-axis stepper machine using a Mesa Anything IO
board with the HostMot2 firmware. No limit switches are used… These configs have 0
Encoders, 0 PWMs, and 3 StepGens"*

### 2. `reference/linuxcnc/configs/by_interface/mesa/hm2-servo/`

3-axis **closed-loop servo** mill (XYZ, trivkins, 3 joints): 3 encoders for feedback,
3 PWM outputs for command, closed through `pid.N`. Homing uses a single shared home
switch input; Y homes to encoder index, X and Z do not.

Files:

```
3x20-small.ini   4i65.ini   4i68.ini   5i20.ini   5i22-big.ini   5i22-small.ini
5i23.ini   7i43-big.ini   7i43-small.ini   7i90.ini   7i93.ini   7i96s.ini
README   README_es   hm2-servo-eth.hal   hm2-servo.hal   tool.tbl
```

`7i96s.ini`:

```ini
[HOSTMOT2]
DRIVER=hm2_eth
BOARD_IP=192.168.1.121
BOARD=7i96s
CONFIG="num_encoders=3 num_pwmgens=3 num_stepgens=0"
```

`README`: *"This configuration drives a 3-axis servo machine… All 3 axes use a home
switch. All home switches are connected to a single shared input pin. X and Z home
without encoder index, Y homes with encoder index. No limit switches are used. These
configs have 3 Encoders, 3 PWMs, and 0 StepGens"*

⚠️ Treat this one as a *generic template that has had a 7i96s INI dropped into it*,
not as a validated 7i96S config. It asks the firmware for 3 encoders and 3 pwmgens;
pncconf's own 7i96/7i96S data (section e) describes the shipping configurations as
having 1 encoder and 0–1 pwmgen. Do not assume a stock 7i96S bitfile will actually
provide 3+3.

### 3. `reference/linuxcnc/configs/by_interface/mesa/hm2-modbus/`

Three variants of the **same 3-axis open-loop stepper base** as `hm2-stepper`, plus
Modbus RTU over the board's PktUART. XYZ, trivkins, 3 joints in all three.

Files:

```
7i96s-modbus-bldc.ini              7i96s-modbus-bldc_and_wavebox.ini
7i96s-modbus-wavebox.ini           README
bldc_and_wavebox.mbccs             brushless-dc-motor-driver.hal
brushless-dc-motor-driver.mbccs    hm2-stepper-eth-modbus.hal
rs485io.mbccs                      waveshare-modbus-rtu-relay-d.mbccs
```

- `7i96s-modbus-bldc.ini` — stepper base + a Modbus BLDC spindle driver
  (`brushless-dc-motor-driver.hal`).
- `7i96s-modbus-wavebox.ini` — stepper base + a Waveshare Modbus RTU relay board.
- `7i96s-modbus-bldc_and_wavebox.ini` — both of the above.

All three use `BOARD_IP=10.10.10.10`, `BOARD=7i96s`, `CONFIG=""`.

`README`: *"The examples are based on the 7i96s Ethernet board, but should be easily
ported to other boards."* It also warns that the card must be flashed with a bitfile
providing **PktUART v3 or later**, and that `.mbccs` sources must be compiled to
`.mbccb` with `mesambccc(1)`.

---

## c) 7i96 board support in `src/hal/drivers/mesa-hostmot2/`

### Which files define the 7i96 pin descriptors

**Exactly one file: `reference/linuxcnc/src/hal/drivers/mesa-hostmot2/hm2_eth.c`.**

There is **no** `hm2_7i96.c` / `hm2_7i96.h`. (Contrast with `hm2_7i43.c/.h` and
`hm2_7i90.c/.h`, which do exist for those boards — but those are *low-level transport*
drivers, not pin tables.) `dtcboards.h`, `llio_info.c` and `hostmot2.h` contain no
7i96 references at all.

The whole of the 7i96 board support is:

| Location | What it is |
|---|---|
| `hm2_eth.c:103-159` | `hm2_7i96_pin_names[]` — 51 connector-pin label strings |
| `hm2_eth.c:1319-1339` | the `7I96` branch of `hm2_eth_probe()` |
| `hm2_eth.c:1341-1362` | the `7I96S` branch (shares the same pin-name array) |

`hm2_7i96_pin_names[]` has exactly **51 entries = 3 connectors × 17 pins**, laid out
TB3 (17) → TB1 (8) → TB2 (7) → 2 `"internal"` → P1/DB25 (17):

```c
static char *hm2_7i96_pin_names[] = {
    "TB3-01", … "TB3-11",
    "TB3-13/TB3-14", … "TB3-23/TB3-24",     /* SSR outputs */
    "TB1-02/TB1-03", … "TB1-22-TB1-23",     /* step/dir 0-3 */
    "TB2-02/TB2-03", … "TB2-18/TB2-19",     /* step/dir 4, enc, serial */
    "internal",  /* SSerial TXEN */
    "internal",  /* SSR AC Reference pin */
    "P1-01/DB25-01", … "P1-25/DB25-13",
};
```

(These are only *labels* used in log output and GPIO descriptions — they do not affect
function.)

The 7I96 branch itself:

```c
} else if (strncmp(board_name, "7I96", 8) == 0) {
    strncpy(llio_name, board_name, 8);
    llio_name[1] = tolower(llio_name[1]);
    board->llio.num_ioport_connectors = 3;
    board->llio.pins_per_connector = 17;
    board->llio.io_connector_pin_names = hm2_7i96_pin_names;
    board->llio.ioport_connector_name[0] = "P1";    // DB25, IO 34..50
    board->llio.ioport_connector_name[1] = "TB1";   // step/dir 0-3
    board->llio.ioport_connector_name[2] = "TB2";   // step/dir 4, enc A/B/Z, serial
    board->llio.ioport_connector_name[3] = "TB3";   // 11 inputs, 6 SSR outputs
    board->llio.fpga_part_number = "6slx9tqg144";
    board->llio.num_leds = 4;
}
```

— `hm2_eth.c:1319-1339`

Note `fpga_part_number = "6slx9tqg144"` — Spartan-6 XC6SLX9 in TQG144, which matches
the FPGA on our Zhulong board. The 7I96S branch is otherwise identical except
`fpga_part_number = "T20F256"` (an Efinix Trion). **So our Spartan-6 clone is a 7i96
derivative, not a 7i96S derivative**, despite the samples all being 7i96S.

(Minor: the branch writes `ioport_connector_name[3]` although it declares only 3
connectors. The array is `ANYIO_MAX_IOPORT_CONNECTORS = 8`
(`hostmot2-lowlevel.h:53,149`), so this is harmless — index 3 is simply never read.)

### HAL pin / parameter naming pattern

Everything is built from `hm2->llio->name`, which for an Ethernet board is constructed as:

```c
rtapi_snprintf(board->llio.name, sizeof(board->llio.name), "hm2_%.*s.%d",
               (int)strlen(llio_name), llio_name, llio_idx(llio_name));
```

— `hm2_eth.c:1487`

So the prefix is **`hm2_<llio_name>.<index>`**, e.g. `hm2_7i96.0` — and `llio_name` is
derived from the *board-reported name* (see below), lowercased at position 1. This is
the single most important fact for our clone board: **the HAL prefix is not a constant,
it is whatever the board calls itself.**

Throughout the tables below, `<B>` = `hm2_<board>.<idx>` (e.g. `hm2_7i96.0`) and
`NN` = a two-digit zero-padded instance number (`%02d`), starting at `00`.

#### Board-level functions and pins

| Name | Source |
|---|---|
| `<B>.read` | `hostmot2.c:1694` |
| `<B>.write` | `hostmot2.c:1703` |
| `<B>.read_gpio` | `hostmot2.c:1719` |
| `<B>.write_gpio` | `hostmot2.c:1727` |
| `<B>.read_request` | `hostmot2.c:1687` |

hm2_eth adds (documented in `reference/linuxcnc/docs/man/man9/hm2_eth.9`):
`<B>.packet-error` (bit out), `<B>.packet-error-level` (s32 out),
`<B>.packet-error-exceeded` (bit out), and params `<B>.packet-error-decrement`,
`<B>.packet-error-increment`, `<B>.packet-error-limit`, `<B>.packet-read-timeout`
(all s32 rw; `packet-read-timeout` is created at `hm2_eth.c:1518-1524`).

#### GPIO — `ioport.c`

Pattern `<B>.gpio.<III>.<field>` with a **three**-digit zero-padded *global* pin index
(`%03d`), not per-connector:

| Name | Type | Line |
|---|---|---|
| `<B>.gpio.III.in` | bit out | `ioport.c:250` |
| `<B>.gpio.III.in_not` | bit out | `ioport.c:263` |
| `<B>.gpio.III.out` | bit in | `ioport.c:323` |
| `<B>.gpio.III.invert_output` | param bit rw | `ioport.c:286` |
| `<B>.gpio.III.is_opendrain` | param bit rw | `ioport.c:299` |
| `<B>.gpio.III.is_output` | param bit rw | `ioport.c:339` |

Pins that carry a secondary function also get a HAL **alias** built from that function
name (`ioport.c:355-375`).

#### StepGen — `stepgen.c`

Pattern `<B>.stepgen.NN.<field>`. There are `num_stepgens` instances starting at `00`.

Pins:

| Name | Type | Dir | Line |
|---|---|---|---|
| `<B>.stepgen.NN.position-cmd` | float | IN | 883 |
| `<B>.stepgen.NN.velocity-cmd` | float | IN | 891 |
| `<B>.stepgen.NN.velocity-fb` | float | OUT | 899 |
| `<B>.stepgen.NN.position-fb` | float | OUT | 907 |
| `<B>.stepgen.NN.counts` | s32 | OUT | 915 |
| `<B>.stepgen.NN.enable` | bit | IN | 923 |
| `<B>.stepgen.NN.control-type` | bit | IN | 931 |
| `<B>.stepgen.NN.position-reset` | bit | IN | 939 |
| `<B>.stepgen.timer-number` | s32 | IN | 859 |

Conditional on `firmware_supports_index` (`stepgen.c:947`):
`position-latch` (float OUT), `index-enable` (bit IO), `probe-enable` (bit IO),
`index-invert` (bit IN), `probe-invert` (bit IN) — lines 949-981.

Debug pins (always created, lines 992-1032): `dbg_pos_minus_prev_cmd`, `dbg_ff_vel`,
`dbg_s_to_match`, `dbg_vel_error`, `dbg_err_at_match`, `dbg_step_rate`.

Parameters:

| Name | Type | Line |
|---|---|---|
| `<B>.stepgen.NN.position-scale` | float rw | 1042 |
| `<B>.stepgen.NN.maxvel` | float rw | 1050 |
| `<B>.stepgen.NN.maxaccel` | float rw | 1058 |
| `<B>.stepgen.NN.steplen` | u32 rw | 1066 |
| `<B>.stepgen.NN.stepspace` | u32 rw | 1074 |
| `<B>.stepgen.NN.dirsetup` | u32 rw | 1082 |
| `<B>.stepgen.NN.dirhold` | u32 rw | 1090 |
| `<B>.stepgen.NN.step_type` | u32 rw | 1098 |

Conditional: `swap_step_dir` (bit rw, if `firmware_supports_swap`, line 1107);
`table-data-0`…`table-data-3` (u32 rw, if `table_width > 2`, lines 1117-1138).

Note the inconsistent separator convention — `steplen`, `stepspace`, `dirsetup`,
`dirhold`, `step_type`, `swap_step_dir` use no hyphen (and two use underscores), while
`position-scale`, `maxvel`, `maxaccel` differ again. This is a real source of typos;
copy them verbatim.

#### Encoder — `encoder.c`

Pattern `<B>.encoder.NN.<field>`, `num_encoders` instances from `00`.

Pins: `count` (s32 OUT), `count-latched` (s32 OUT), `position` (float OUT),
`position-latched` (float OUT), `velocity` (float OUT), `velocity-rpm` (float OUT),
`rawcounts` (s32 OUT), `rawlatch` (s32 OUT), `reset` (bit IN),
`index-enable` (bit **IO**), `input-a` / `input-b` / `input-index` (bit OUT),
`quad-error` (bit OUT), `quad-error-enable` (bit IN),
`probe-enable` (bit IN), `probe-invert` (bit IN).

Parameters: `scale` (float rw), `vel-timeout` (float rw), `counter-mode` (bit rw),
`filter` (bit rw), `index-invert` (bit rw), `index-mask` (bit rw),
`index-mask-invert` (bit rw).

Module-global (no `NN`): `<B>.encoder.sample-frequency` (u32 IN pin),
`<B>.encoder.muxed-sample-frequency` (u32 IN pin), `<B>.encoder.muxed-skew` (u32 IN
pin), `<B>.encoder.hires-timestamp` (bit IN pin), `<B>.encoder.timer-number`
(s32 IN pin, `encoder.c:485`).

#### PWMGen — `pwmgen.c`

Pattern `<B>.pwmgen.NN.<field>`, `num_pwmgens` instances from `00`.

| Name | Kind | Line |
|---|---|---|
| `<B>.pwmgen.NN.value` | float IN pin | 487 |
| `<B>.pwmgen.NN.enable` | bit IN pin | 494 |
| `<B>.pwmgen.NN.offset-mode` | param bit rw | 502 |
| `<B>.pwmgen.NN.dither` | param bit rw (if `firmware_supports_dither`) | 509 |
| `<B>.pwmgen.NN.scale` | param float rw | 516 |
| `<B>.pwmgen.NN.output-type` | param s32 rw | 527 |
| `<B>.pwmgen.pwm_frequency` | param u32 rw, module-global, default 20000 | 460 |
| `<B>.pwmgen.pdm_frequency` | param u32 rw, module-global, default 20000 | 474 |

(The two module-global params use underscores, unlike everything else.)

### How hm2_eth discovers a board

There is **no broadcast/scan discovery**. You must tell the driver the IP address:

```
loadrt hm2_eth board_ip="10.10.10.10" config="num_stepgens=3 …"
```

Module parameters (`hm2_eth.c:88-95`) are only:

- `board_ip` — array of strings, IP address(es), `MAX_ETH_BOARDS` entries
- `config` — array of strings, the HostMot2 config string(s)
- `debug` — int

The `hm2_eth.9` synopsis also lists `board_mac=`, but **no `board_mac` module parameter
exists in `hm2_eth.c`** — the man page is stale on that point.

Sequence:

1. `init_board()` (`hm2_eth.c:668`) opens a UDP socket to `board_ip` on **port 27181**
   (`hm2_eth.c:451`), and ARPs for the board's MAC via `fetch_hwaddr()`
   (`hm2_eth.c:636`).
2. `hm2_eth_probe()` (`hm2_eth.c:1078`) sends an **LBP16** read of the *board info*
   space and reads back a 16-byte name:

   ```c
   char board_name[16] = {0, };
   LBP16_INIT_PACKET4(read_packet, CMD_READ_BOARD_INFO_ADDR16_INCR(16/2), 0);
   send = eth_socket_send(board->sockfd, (void*) &read_packet, sizeof(read_packet), 0);
   recv = eth_socket_recv_loop(board->sockfd, (void*) &board_name, 16, 0, 200*1000*1000);
   ```

   `LBP16_SPACE_BOARD_INFO` is `0x1C00` (`lbp16.h:56`).

   **This name comes from the LBP16 board-info space, *not* from the HostMot2 IDROM.**
   That distinction matters for us — they are two different things, and a clone can get
   one right and the other wrong.

3. The long `if/else if` chain matches `board_name` and hard-codes
   `num_ioport_connectors`, `pins_per_connector`, connector names, pin-name table,
   FPGA part number and LED count.
4. `hm2_register()` → `hm2_read_idrom()` (`hostmot2.c:646`) then reads the *real*
   HostMot2 IDROM from the FPGA: IDROM offset from `HM2_ADDR_IDROM_OFFSET`, then the
   IDROM struct, then module descriptors and pin descriptors.

`--readhmid` is a **mesaflash** flag, not a driver flag. It makes mesaflash dump the
same IDROM/module-descriptor data the driver parses in step 4, in human-readable form.
pncconf shells out to it (see section d). It is our best pre-flight check.

The IDROM is validated hard in `hm2_read_idrom()`:

- `idrom_type` must be **2 or 3**, else *"invalid IDROM type %d, expected 2 or 3,
  aborting load"* (`hostmot2.c:669-673`)
- **`idrom.port_width` must equal `llio->pins_per_connector`** →
  *"invalid IDROM PortWidth %d, this board has %d pins per connector, aborting load"*
  (`hostmot2.c:692-695`)
- **`idrom.io_ports` must equal `llio->num_ioport_connectors`** →
  *"IDROM IOPorts is %d but llio num_ioport_connectors is %d, driver and firmware are
  inconsistent, aborting driver load"* (`hostmot2.c:708-713`)
- `io_width == io_ports * port_width` (`hostmot2.c:698-706`)
- `io_width <= HM2_MAX_PIN_DESCRIPTORS` (`hostmot2.c:717-722`)
- `clock_low >= 1e6` (`hostmot2.c:726-729`)

Those middle two checks are the trap: `pins_per_connector` and `num_ioport_connectors`
are set from the **name match**, while `port_width` and `io_ports` come from the
**actual firmware**. If a board claims to be a `7I96` but is loaded with firmware whose
geometry isn't 3×17, the driver aborts.

`loadrt hostmot2 debug_idrom=1 debug_module_descriptors=1 debug_pin_descriptors=1
debug_modules=1` dumps all of this to dmesg — it is commented out ready to use at
`reference/linuxcnc/configs/by_interface/mesa/hm2-servo/hm2-servo-eth.hal:39`.

### Does the driver hard-check the board name string? — **Yes. This is our risk.**

The accepted strings for the 7i96 family are exactly:

| Test | Line |
|---|---|
| `strncmp(board_name, "7I96", 8) == 0` | `hm2_eth.c:1319` |
| `strncmp(board_name, "7I96S", 8) == 0` | `hm2_eth.c:1341` |

Three things to note:

1. **Uppercase `I`.** `"7i96"` in lowercase will not match.
2. **The length-8 compare makes these effectively exact matches.** `strncmp` stops at a
   NUL in either operand, and `board_name` is zero-initialised
   (`char board_name[16] = {0, }`). So `"7I96"` matches only if byte 4 is `\0`. A board
   reporting `"7I96 "` (space-padded), `"7I96-ETH"`, `"7I96V2"`, `"ZHULONG"`, or
   anything else will **not** match. Conversely `"7I96S"` correctly falls past the
   `"7I96"` test (byte 4 is `'S'` vs `'\0'`) and into its own branch.
3. **A non-match is not fatal.** The `else` branch at `hm2_eth.c:1460-1483` handles it:

   ```c
   } else {
       LL_PRINT("Unrecognized ethernet board found: %.16s -- port names will be wrong\n", board_name);
       strncpy(llio_name, board_name, 4);
       llio_name[1] = tolower(llio_name[1]);
       …
       hm2_eth_read(&board->llio, HM2_ADDR_IDROM_OFFSET, &read_data, 4);
       unsigned int idrom_address = read_data & 0xffff;
       hm2_idrom_t idrom;
       hm2_eth_read(&board->llio, idrom_address, &idrom, sizeof(idrom));
       board->llio.num_ioport_connectors = idrom.io_ports;
       board->llio.pins_per_connector = idrom.port_width;
       for(i=0; i<board->llio.num_ioport_connectors; i++)
           board->llio.ioport_connector_name[i] = "??";
       board->llio.fpga_part_number = "??";
       board->llio.num_leds = 0;
   }
   ```

**What this means for the Zhulong V2.0**, concretely:

- If it reports exactly `7I96`: we get the full 7i96 treatment, TB1/TB2/TB3/P1 names,
  4 LEDs, HAL prefix `hm2_7i96.0` — *and* the strict `port_width == 17` /
  `io_ports == 3` checks apply.
- If it reports anything else: we get a loud warning, connector names become `"??"`,
  `num_leds` drops to 0 (so no LED HAL pins), the FPGA part number is unknown — but
  **the driver still loads**, because it takes the geometry from the IDROM itself, so
  those two consistency checks can't fail. StepGen / Encoder / PWMGen all still work
  normally; only the cosmetic connector/pin labelling is lost.
- Either way, **the HAL prefix follows the reported name**: `llio_name` is the first 4
  bytes of the board name with byte 1 lowercased. A board reporting `MESA` would give
  `hm2_mESA.0`; one reporting `7I96` gives `hm2_7i96.0`. So the *first* thing to do
  after `mesaflash --readhmid` is note the exact reported name and set
  `[HOSTMOT2]BOARD` in our INI to match. The `hm2_[HOSTMOT2](BOARD)` indirection in the
  sample HAL files means that is the only place we need it.

The full accepted-name list for `hm2_eth` (all with the same uppercase/exact-match
caveats), for reference: `7I80DB-16`, `7I80DB-25`, `7I80HD-16`, `7I80HD-25`, `7I80HDT`,
`7I76E-16`, `7I76EU`, `7I92`, `7I92T`, `7I93`, `7I94`, `7I94T`, `7I95`, `7I95T`,
`7I96`, `7I96S`, `7I97`, `7I97T`, `7I98`, `MC04`, `8CSS` (`hm2_eth.c:1102-1458`), plus
a `litehm2` prefix test at `hm2_eth.c:1498`.

---

## d) pncconf board enumeration

### Is 7i96 in the list? Yes — both 7i96 and 7i96S.

Two separate structures in
`reference/linuxcnc/src/emc/usr_intf/pncconf/private_data.py`:

**1. `MESA_INTERNAL_FIRMWAREDATA`** (starts line 585) — 4 entries for 7i96 and 3 for
7i96S:

| Line | Board title | Firmware name |
|---|---|---|
| 1042 | `7i96-Internal Data` | `7i96d` |
| 1057 | `7i96-Internal Data` | `7i96dpl` |
| 1072 | `7i96-Internal Data` | `7i96d_1pwm` |
| 1087 | `7i96-Internal Data` | `7i96_7i74d` |
| 1096 | `7i96s-Internal Data` | `7i96s_d` |
| 1115 | `7i96s-Internal Data` | `7i96s_dpl` |
| 1149 | `7i96s-Internal Data` | `7i96s_7i74` |

**2. `MESA_BOARD_META`** — the geometry table:

```python
'7i96': {'DRIVER':'hm2_eth','PINS_PER_CONNECTOR':17,'TOTAL_CONNECTORS':3,
         'TAB_NUMS':[1,2,3],'TAB_NAMES':['TB3','TB1/TB2','P1']},
'7i96s':{'DRIVER':'hm2_eth','PINS_PER_CONNECTOR':17,'TOTAL_CONNECTORS':3,
         'TAB_NUMS':[1,2,3],'TAB_NAMES':['TB3','TB1/TB2','P1']},
```

— `private_data.py:1359-1360`

pncconf also contains a 7i96→7i96S fixup, which confirms that the two boards report
*the same* name over the wire and are told apart by gate count:

```python
# 7i96s thinks it is a 7i96, set it straight
if BOARDNAME == '7i96' and '20 KGATES' in info:
    BOARDNAME = '7i96s'
```

— `pncconf.py:1526-1528`

### Mechanisms for a board not in the list

There are four, in increasing order of usefulness to us:

**1. Discovery Option (live, via mesaflash)** — the real answer. Only offered when
"advanced options" is ticked (`pncconf.py:678-679`):

```python
if self.d.advanced_option:
    self._p.MESA_BOARDNAMES.append('Discovery Option')
```

It shells out to mesaflash (`pncconf.py:1430-1460`):

```python
cmd = """pkexec sh -c 'mesaflash %s;mesaflash %s --sserial;mesaflash %s --readhmid'  """ % (
        board_command, board_command, board_command)
```

then `parse_discovery()` (`pncconf.py:1486-1689`) parses that text into an XML firmware
description, writes it to **`~/mesa0_discovered.xml`**, and
`discovery_selection_update()` (`pncconf.py:1692-1709`) registers it as a new board
named `Discovered:<boardname>`:

```python
boardname = 'Discovered:%s' % boardname
firmdata = self.parse_xml(driver, boardname, firmname, path, bdnum)
self._p.MESA_FIRMWAREDATA.append(firmdata)
self._p.MESA_INTERNAL_FIRMWAREDATA.append(firmdata)
self._p.MESA_BOARDNAMES.append(boardname)
```

Everything — connector count, pin width, encoder/pwmgen/stepgen/sserial/SSR/INM/OUTM
counts, clock rates — comes from the card's own IDROM at that moment. **This is the
path for our clone board**, and it does not care whether the name is in any list.

**2. PIN-file paste mode** — the offline variant. Ticking `discovery_read_option`
lets you paste the card's PIN file text into the Help window's Input tab instead of
talking to hardware (`pncconf.py:1380-1393`). Useful before the board is wired up, if
we can get a PIN file for it.

**3. External firmware folder** — `add_external_folder_boardnames()`
(`pncconf.py:656-664`) walks `FIRMDIR = "/lib/firmware/hm2/"` (`private_data.py:103`)
and adds each subdirectory as a board name. A blacklist filters some out:
`MESABLACKLIST = ["5i22","7i43","4i65","4i68","SVST8_3P.xml"]` (`private_data.py:105`).
If the directory doesn't exist, pncconf warns and falls back to internal data
(`pncconf.py:665-672`).

**4. `EXTRA_MESA_FIRMWAREDATA`** — initialised empty (`private_data.py:63`), loaded from
the user's `.pncconf-preference` file, and merged in at `pncconf.py:681-685`. A place to
hand-write a board definition permanently.

**Caveat worth stating plainly:** pncconf is a *config generator*, not a runtime
component. Nothing here affects whether `hm2_eth` will drive the board — that is
entirely section (c). Given we are hand-writing our INI/HAL for a 3-axis machine, the
main value of pncconf to us is running its discovery path once to get a parsed dump of
what the clone's IDROM actually contains.

---

## e) 7i96 in `reference/hostmot2-firmware`

### There is nothing. This is a hard negative.

Searched by filename and by content (including a binary-safe `grep -a`) across the
whole clone: **zero matches for `7i96` / `7I96`, in any file, at any case.**

The repository does not contain 7i96 material of any kind:

- **No regmap file.** No 7i96 entry anywhere.
- **No pinout definition.** `src/` has 229 `PIN_*.vhd` files and 27 `*.ucf`
  constraint files; none is for the 7i96. The Ethernet boards it *does* cover are
  the 7i76E (`src/7i76e.ucf`, `src/PIN_7I76E_51.vhd`) and 7i80
  (`src/7i80db.ucf`, `src/7i80hd.ucf`).
- **No module instantiation counts**, because there is no 7i96 configuration to read
  them from.

The reason is in `reference/hostmot2-firmware/README.md`:

> **Note: This package is no longer maintained**
>
> In 2021, it became clear that the existing CI system that had built
> hostmot2-firmware was no longer viable, and LinuxCNC project did not have the
> developer time to rehabilitate it. […]
>
> For recent Mesa cards that store their own firmware in non-volatile storage (i.e., on
> an SPI Flash chip), there is no such requirement; a user can obtain the firmware files
> from Mesa Electronics, load them once with mesaflash, and go on their way.

`reference/hostmot2-firmware/firmwares.txt` lists every card this repo builds for, and
confirms the scope — all pre-Ethernet-era PCI/EPP/PC104 cards:

```
i90epp  i90spi  i80db25  i80hd25  i80db16  i80hd16  x20_1000
i22_1500  i22_1000  i23  i68  i43_400  i43_200  i20  i65  i24  i25
```

The last commit is literally `c15c0c3 Merge pull request #10 from
jepler/officially-unmaintain`.

There are also **no `.bit` bitfiles anywhere in the LinuxCNC clone** either (checked).

**Consequence for us:** the 7i96 bitfile is not open-source-buildable from this clone.
It lives in the board's SPI flash and comes from Mesa. For the Zhulong, whatever the
vendor flashed is what we have unless we obtain a Mesa 7i96 bitfile and reflash — which
is a real risk on a clone (different flash part, different FPGA config pinout). **Read
the flash contents before writing anything to it.**

### Substitute source for "what the standard 7i96 configuration instantiates"

Since the firmware repo can't answer this, the best in-tree answer is pncconf's
internal data. Field legend from `private_data.py:568-584`:

```
board title, boardname, firmwarename, firmware directory, Hal driver name,
max encoders, pins per encoder,   max resolvers, pins,
max pwmgens, pins,                max tppwmgens, pins,
max stepgens, pins per stepgen,   max smart serial ports, channels,
discovered sserial devices, 7×spare,
has watchdog, max GPIO, low frequency, hi frequency, connector numbers, …
```

Decoding the four 7i96 rows and three 7i96S rows:

| Firmware | Enc | Res | PWM | TPPWM | Step | SSerial | WD | GPIO | ClkLow | ClkHigh | Conns |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `7i96d` | 1 (3 pins) | 0 | 0 (3 pins) | 0 | **5** (2 pins) | 1 port / 1 ch | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96dpl` | 3 (1 pin) | 0 | 0 (3 pins) | 0 | 5 (2 pins) | 1/1 | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96d_1pwm` | 1 (3 pins) | 0 | **1** (2 pins) | 0 | **4** (2 pins) | 1/1 | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96_7i74d` | 1 (3 pins) | 0 | 0 (3 pins) | 0 | 5 (2 pins) | 1 port / **8** ch | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96s_d` | 1 (3 pins) | 0 | **1** (3 pins) | 0 | 5 (2 pins) | 1/1 | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96s_dpl` | 3 (1 pin) | 0 | 1 (3 pins) | 0 | 5 (2 pins) | 1/1 | 1 | 34 | 33 | 200 | 1,2,3 |
| `7i96s_7i74` | 1 (3 pins) | 0 (10) | 1 (3 pins) | 0 | 5 (2 pins) | **2** ports / 8 ch | 1 | **51** | **100.0** | 200.0 | 1,2,3 |

So the **standard/default 7i96 configuration (`7i96d`) instantiates: 5 StepGens,
1 Encoder, 0 PWMGens, 1 SmartSerial port (1 channel), 1 watchdog, plus GPIO/SSR** —
which matches the connector comments in `hm2_eth.c` ("step & dir 0-3" on TB1,
"step & dir 4, enc A, B, Z, serial Rx/Tx" on TB2, "11 inputs, 6 SSR outputs" on TB3).

> **⚠ These counts are Mesa's, not ours.** The Zhulong's connectors demand at least
> 6 StepGens, 3 Encoders and 1 PWMGen — none of which this configuration provides. Use
> these numbers as the *upstream baseline*, never as an expectation for our board. See
> [04-zhulong-board-hardware.md](04-zhulong-board-hardware.md) and the side-by-side
> contrast in [03-7i96-pinout.md](03-7i96-pinout.md).

The per-pin tables in the same records spell out the SSR and INM usage. For `7i96d`
(`private_data.py:1044-1055`) TB3 is 11 × `GPIOI` + 6 × `SSR0` (logical 100-105), TB2
is 5 × step/dir pairs then `ENCA/ENCB/ENCI` and `RXDATA0/TXDATA0/TXEN0`, and P1 is 17 ×
`GPIOI`. On the 7i96S (`private_data.py:1099+`) TB3's inputs become `INM0` (the
input-multiplexer module) rather than plain GPIO — a genuine hardware difference
between the two boards.

**Two caveats — do not take these numbers as ground truth for our board:**

1. `MAX GPIO = 34` is inconsistent with 3 connectors × 17 pins = 51. The `7i96s_7i74`
   row does say 51. `34` is also exactly the value in the 7i92 rows (2 × 17), which the
   7i96 records otherwise resemble; it looks like a copy-paste artefact. Verify against
   the real IDROM.
2. Likewise `ClkLow = 33` in the `7i96s_*` rows conflicts with `100.0` in
   `7i96s_7i74`. 33 MHz is the Spartan-6 7i96 figure and is the one relevant to our
   Spartan-6 clone, but confirm it from `mesaflash --readhmid`.

---

## f) Authoritative documentation paths (for offline grep)

### Primary references

| Topic | Path | Lines |
|---|---|---|
| **HAL reference** | `reference/linuxcnc/docs/src/hal/general-ref.adoc` | 161 |
| HAL basics / syntax | `reference/linuxcnc/docs/src/hal/basic-hal.adoc` | 566 |
| HAL component index | `reference/linuxcnc/docs/src/hal/components.adoc` | 438 |
| HAL realtime components | `reference/linuxcnc/docs/src/hal/rtcomps.adoc` | 710 |
| **INI reference** | `reference/linuxcnc/docs/src/config/ini-config.adoc` | 1395 |
| INI homing reference | `reference/linuxcnc/docs/src/config/ini-homing.adoc` | — |
| **hostmot2 driver** | `reference/linuxcnc/docs/src/drivers/hostmot2.adoc` | 788 |

Other HAL docs in `reference/linuxcnc/docs/src/hal/`: `intro.adoc`, `tutorial.adoc`,
`comp.adoc` (writing `.comp` components — we will need this for `components/`),
`halmodule.adoc`, `haltcl.adoc`, `tools.adoc`, `halshow.adoc`, `twopass.adoc`,
`hal-examples.adoc`, `canonical-devices.adoc`, `parallel-port.adoc`,
`halui-examples.adoc`.

Other config docs in `reference/linuxcnc/docs/src/config/`: `core-components.adoc`,
`stepper.adoc`, `stepper-quickstart.adoc`, `stepper-diagnostics.adoc`,
`integrator-concepts.adoc`, `pncconf.adoc`, `stepconf.adoc`, `lathe-config.adoc`,
`python-hal-interface.adoc`.

### Section map of `hostmot2.adoc`

Useful for jumping straight to the right part:

| Line | Section |
|---|---|
| 24 | Firmware Binaries |
| 53 | Installing Firmware |
| 61 | Loading HostMot2 |
| 89 | Watchdog |
| 124 | HostMot2 Functions |
| 150 | Pinouts |
| 229 | PIN Files |
| 246 | HAL Pins |
| 257 | Configurations (the `config=` string) |
| 398 | GPIO |
| 464 | **StepGen** (Pins 484, Parameters 509, Output Parameters 539) |
| 562 | PWMGen (Pins 580, Parameters 590, Output Parameters 621) |
| 637 | Encoder (Pins 651, Parameters 664) |
| 704 | 5I25 Configuration |
| 776 | Example Configurations |

### Man pages

Two parallel trees. **The nroff tree is the complete one:**

- `reference/linuxcnc/docs/man/man1/` — 101 files
- `reference/linuxcnc/docs/man/man3/` — 128 files
- `reference/linuxcnc/docs/man/man9/` — 48 files

Mesa-relevant, all in `reference/linuxcnc/docs/man/man9/`:
**`hm2_eth.9`** (the one we need most), `hostmot2.9`, `hm2_pci.9`, `hm2_spi.9`,
`hm2_rpspi.9`, `hm2_7i43.9`, `hm2_7i90.9`, `setsserial.9`.

The asciidoc man tree `reference/linuxcnc/docs/src/man/` is **sparse** — only
`man1/` (9 files), `man3/` (3), `man9/` (2: `hm2_modbus.9.adoc`, `hm2_spix.9.adoc`).
Don't grep there expecting coverage.

### Modbus (relevant if we go RS-485 for the spindle VFD)

`reference/linuxcnc/docs/src/drivers/mesa_modbus.adoc`,
`reference/linuxcnc/docs/src/man/man9/hm2_modbus.9.adoc`,
`reference/linuxcnc/docs/src/man/man3/hm2_pktuart.3.adoc`,
`reference/linuxcnc/docs/src/man/man1/mesambccc.1.adoc`.

### Not present

`mesaflash` is **not** in this tree — it is a separate upstream project. It is only
*referenced* by `docs/man/man9/{hm2_eth,hostmot2,hm2_spi,hm2_rpspi,hm2_7i90,setsserial}.9`
and `docs/src/drivers/mesa_modbus.adoc`. There is no `mesaflash(1)` man page here and
no source. We will install it separately on the Pi.

---

## Carry-forward for board bring-up

1. `mesaflash --device 7i96 --addr <ip> --readhmid` is the first command to run once the
   board is on the wire. Record: the **exact board name string**, `IDROM type`,
   `port width`, `number of IO ports`, `clock low/high`, and the module descriptor
   counts (StepGen / Encoder / PWMGen / SSerial / SSR / IOPort).
2. Compare the reported name byte-for-byte against `"7I96"` / `"7I96S"`. That single
   string decides whether we get the nice path or the `"??"` path, and it sets our HAL
   prefix.
3. Cross-check `port width == 17` and `io ports == 3`. If the board reports the name
   `7I96` but not that geometry, `hm2_eth` will refuse to load and the fix is *not*
   obvious from the error message.
4. Do not reflash. Read the flash first.
