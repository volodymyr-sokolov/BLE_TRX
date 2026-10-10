# ble_trx firmware — nRF52840 BLE packet generator and sniffer

One Zephyr / nRF Connect SDK image that turns an nRF52840 board into either a
**BLE packet generator** (TX) or a **BLE link-layer sniffer** (RX), switchable
at run time over USB. There is no SoftDevice and no Bluetooth stack. The
firmware drives the RADIO peripheral directly, so every on-air parameter is
under your control:

- **TX:** address, PDU type, channel (advertising *or* data, any access
  address), interval, TX power, BLE 1M vs Nordic proprietary 1M modulation,
  deliberately broken CRCs, a second "twin" transmitter personality, and
  **raw PDUs** sent byte-for-byte.
- **RX:** one channel or a hop list, any access address / CRC init (also per
  channel), good-CRC-only or all packets. Each packet is reported with a
  hardware timestamp (µs), RSSI, CRC verdict and the received CRC.

The host tools [`ble_tx`](../ble_tx/README.md) and [`ble_rx`](../ble_rx/README.md)
drive this firmware; you can also type the commands below into any serial
terminal.

## Contents

| Path | What |
|---|---|
| `src/main.c` | boot, LED, autostart, fault → bootloader |
| `src/hostif.c` | USB CDC line I/O, output queue, DFU entry (`dfu` command / 1200-baud touch) |
| `src/cmd.c` | the command language (below) |
| `src/adv.c` | RADIO set-up and the TX (advertising) scheduler, raw PDUs |
| `src/rx.c` | the receiver: interrupt-driven RX, PPI timestamps, hopping, packet reports |
| `src/store.c` | `save`/`load`/`erase` of the configuration in flash |
| `src/sim.h` | shared configuration structure |
| `prj.conf`, `pm_static.yml`, `CMakeLists.txt` | build configuration; flash map for the Adafruit bootloader |
| `boards/nice_nano_v2.overlay` | LED pin for the nice!nano v2 |
| `tools/build.ps1`, `tools/flash.ps1`, `tools/ncs_env.ps1`, `tools/talk.py` | build, flash, toolchain environment, one-shot serial helper |

The **compiled images** are in a separate top-level folder,
[`../firmware_bin/`](../firmware_bin/README.md): `ble_trx_feather` /
`ble_trx_nicenano` as `.hex` (serial DFU) and `.uf2` (UF2 drive), with
`SHA256SUMS.txt`. To flash a board you don't need anything in this folder
except `tools/flash.ps1`.

## Build

You need the **nRF Connect SDK v3.1.1** (toolchain `c1a76fddb2`). Edit the two
paths in `tools/ncs_env.ps1` if your SDK is not at `C:\ncs`.

```powershell
# from the repository root
.\firmware\tools\build.ps1                    # Adafruit Feather nRF52840 Express  -> firmware\build_feather\
.\firmware\tools\build.ps1 -Board nicenano    # nice!nano v2 (LED on P0.15)        -> firmware\build_nicenano\
.\firmware\tools\build.ps1 -Pristine          # clean rebuild
.\firmware\tools\build.ps1 -Release           # build both boards, copy the images to firmware_bin\
                                              # and regenerate firmware_bin\SHA256SUMS.txt
```

Run `-Release` after changing the sources, so that `firmware_bin/` matches
them. The build is reproducible: a pristine rebuild of unchanged sources gives
byte-identical images.

Equivalent raw `west` commands:

```powershell
. .\firmware\tools\ncs_env.ps1
cd firmware
west build -b adafruit_feather_nrf52840/nrf52840/uf2 -d build_feather .
west build -b adafruit_feather_nrf52840/nrf52840/uf2 -d build_nicenano . -- -DEXTRA_DTC_OVERLAY_FILE=boards/nice_nano_v2.overlay
```

The image is linked at **0x26000**, behind the Adafruit nRF52 bootloader and
its S140 SoftDevice region (which this firmware never enables):

| Address | Region | Size |
|---|---|---|
| 0x000000 | MBR | 4 KB |
| 0x001000 | SoftDevice S140 v6 (present, unused) | 148 KB |
| 0x026000 | **application (ble_trx)** | 792 KB |
| 0x0EC000 | settings storage (`save`) | 32 KB |
| 0x0F4000 | Adafruit bootloader | 48 KB |

Resource use: about 95 KB flash and 95 KB RAM.

## Flash

Over USB, using the board's Adafruit serial DFU bootloader. No SWD probe is needed.

```powershell
pip install adafruit-nrfutil pyserial

.\firmware\tools\flash.ps1 -Port COM7 -Prebuilt feather                # compiled image from firmware_bin\
.\firmware\tools\flash.ps1 -Port COM7 -Prebuilt nicenano               # ... for a nice!nano v2
.\firmware\tools\flash.ps1 -Port COM7                                   # your own build (build_feather)
.\firmware\tools\flash.ps1 -Port COM7 -Build build_nicenano             # your own nice!nano build
.\firmware\tools\flash.ps1 -Port COM7 -Hex C:\path\to\image.hex         # any other image
```

`flash.ps1` resets the board into its bootloader and runs `adafruit-nrfutil`
against the port the bootloader appears on. The bootloader port is usually a
different COM number, and the script waits for it. To reset, the script first
sends `dfu`, which ble_trx and the sibling firmware understand, then tries the
1200-baud touch (Arduino, CircuitPython).

**If the board runs firmware that understands neither**, double-tap the
board's RESET button. The LED pulses and a new COM port appears. Then pass
that port:

```powershell
.\firmware\tools\flash.ps1 -Port COM12 -Prebuilt feather    # COM12 = the bootloader's port
```

The manual equivalent of what the script does:

```powershell
adafruit-nrfutil dfu genpkg --dev-type 0x0052 --application firmware_bin\ble_trx_feather.hex dfu.zip
adafruit-nrfutil dfu serial --package dfu.zip -p COM12 -b 115200 --singlebank --touch 0
```

On Linux or macOS, the same two `adafruit-nrfutil` commands work with
`/dev/ttyACM0` and similar ports. Boards whose bootloader exposes a UF2 drive
can instead take `firmware_bin/*.uf2` copied onto that drive.

After flashing, the board enumerates as USB **1915:520F** ("nRF52840 BLE TX/RX")
and its radio stays off until it receives a command. It never transmits unless you
ask it to (or you saved `boot on`). A fatal fault reboots the board into its
bootloader, so it can always be reflashed over the same cable.

## Command reference

Send one command per line (USB CDC; any baud rate except 1200, which means "enter the
bootloader"). Replies are JSON:
`{"ok":...}` / `{"err":...}` for commands, `{"ev":...}` for events and
queries. Values are decimal unless prefixed with `0x`.

### Transmitter (TX)

| Command | Effect |
|---|---|
| `start [N]` | advertise forever, or `N` events then stop (`{"ev":"done"}`); stops RX |
| `stop` | stop advertising |
| `addr AA:BB:CC:DD:EE:FF` | main advertiser address (human order) |
| `type public\|random` | address type (TxAdd bit) |
| `pdu adv_ind\|adv_direct_ind\|adv_nonconn_ind\|adv_scan_ind` | PDU type |
| `direct AA:BB:CC:DD:EE:FF` | TargetA for `adv_direct_ind` |
| `data <hex>` | AD structures, ≤ 31 bytes; spaces allowed, e.g. `data 02 01 06` |
| `name <text>` | shortcut: Flags + Complete Local Name (one word, ≤ 26 chars) |
| `counter on\|off` | append a 16-bit event counter AD (`03 FF lo hi`) so packets differ |
| `channels 37 38 39` | 1–8 channel indices 0–39 sent in each event (advertising and/or data) |
| `aa 0xNNNNNNNN` | access address (default `0x8E89BED6`) |
| `crcinit 0xNNNNNN` | CRC init (default `0x555555`) |
| `power <dBm>` | 8, 7, 6, 5, 4, 3, 2, 0, −4, −8, −12, −16, −20, −40 |
| `phy ble1m\|nrf1m` | modulation; `nrf1m` has a lower deviation (h ≈ 0.34 vs 0.5) |
| `interval <ms>` | between advertising events |
| `gap <us>` | between channels within one event |
| `advdelay on\|off` | random 0–10 ms advDelay per event (default on) |
| `crcerr <pct>` | send this % of events with a deliberately bad CRC |
| `twin on\|off` | enable the second transmitter personality |
| `twin addr AA:..` / `twin same` | the twin's address, or reuse main's (impersonation) |
| `twin phy ble1m\|nrf1m` / `twin power <dBm>` / `twin pct <0-100>` | twin modulation / power / share of events |
| `raw <hex>` | **raw PDU** (header + payload, 2–257 bytes) sent verbatim; spaces / `:` / `-` / `,` ignored |
| `raw on\|off` | switch between the stored raw PDU and the PDU built from the settings above |
| `raw` | show the raw PDU |

The radio adds the preamble, access address, CRC and whitening to a raw PDU. The
twin and `crcerr` still apply to it. The radio sends as many payload bytes as the
header's LENGTH byte says. The reply reports `len` (bytes given) and `on_air`
(bytes sent), so you can see a mismatch. `ble_tx` fixes LENGTH for you by default.

### Receiver (RX)

| Command | Effect |
|---|---|
| `rx on` / `rx start` | start receiving (stops TX) |
| `rx off` / `rx stop` | stop |
| `rx ch 37 [38 39]` | channel, or a hop list of up to 8 |
| `rx ch 37 5:0x9E8B4B27:0x123456` | a hop-list entry can carry its own access address and CRC init |
| `rx dwell <ms>` | time per channel when hopping (0 = stay on the first) |
| `rx aa 0xNNNNNNNN` / `rx crcinit 0xNNNNNN` | default access address / CRC init |
| `rx adv` | shortcut: advertising AA and CRC init |
| `rx phy ble1m\|nrf1m` | receiver modulation |
| `rx crc ok\|all` | report good-CRC packets only (default), or CRC failures too |
| `rx info` | settings and counters: `packets`, `crc_bad`, `dropped` (output queue full), `hops` |

A setting changed while receiving takes effect immediately. Each received
packet produces one line:

```json
{"ev":"pkt","n":7,"t":15.755773,"ch":37,"rssi":-62,"crc_ok":1,"aa":"8e89bed6","pdu":"0222c16f7aae1c64...","crc":"a8a9a4"}
```

| Field | Meaning |
|---|---|
| `n` | sequence number of reported packets (a gap means the host lost lines) |
| `t` | seconds since power-up. TIMER0 is captured by the RADIO ADDRESS event through PPI, so the timestamp marks the end of the access address with 1 µs resolution |
| `ch`, `rssi`, `crc_ok`, `aa` | channel, RSSI in dBm, CRC verdict, access address the packet matched |
| `pdu` | header + payload, hex |
| `crc` | the nRF `RXCRC` register value; `ble_rx` converts it to the on-air byte order |

### General

| Command | Effect |
|---|---|
| `info` | full configuration and counters (`info`, `twin`, `chan`, `stats`, `raw`, `rx`, `mode` events) |
| `help` | command list |
| `defaults` | reset the whole configuration (stops TX/RX; not saved) |
| `save` / `load` / `erase` | persist / restore / clear the configuration (flash, CRC-protected) |
| `boot on\|off\|rx` | at power-up: advertise / stay idle / start receiving (takes effect after `save`) |
| `dfu` | reboot into the serial bootloader for reflashing |
| `reboot` | cold reboot |

### Worked examples

A plain advertiser on all three advertising channels:

```
addr DE:AD:BE:EF:12:34
type random
name blesim
counter on
channels 37 38 39
interval 100
start
```

**Impersonator:** one address, two radios. The twin reuses the main address
but transmits with a lower modulation index:

```
twin on
twin same
twin phy nrf1m
twin pct 40
start
```

A data-channel packet (raw LL_DATA PDU "Hello") with a custom access address:

```
channels 5
aa 0x9E8B4B27
crcinit 0x123456
raw 0e 05 48 65 6c 6c 6f
start
```

A standalone sniffer that starts at power-up on the advertising channels:

```
rx ch 37 38 39
rx dwell 100
boot rx
save
```

## Implementation notes

**TX** runs the radio in polled mode from its own thread. A whole advertising
event goes out on every configured channel, `gap_us` apart, from one persona
(main or twin).

**RX** is interrupt-driven. On every END event, the ISR copies the PDU, CRC
status, RXCRC and RSSI into a queue and restarts reception straight away. A
lower-priority thread formats the JSON lines. If the host does not read fast
enough, the queue fills and the ISR counts `dropped` packets instead of
stalling the radio. Advertising traffic (hundreds of packets/s) never came
close to this limit in testing. TX and RX share the radio under a mutex, and
starting either one stops the other.

### Bugs found bringing up the TX path (kept as comments at the fix sites)

1. **`PCNF1` left at reset.** Without it, `BALEN=0` (a one-byte access
   address), `WHITEEN=0` and `MAXLEN=0`: the board "transmits", but nothing can
   decode the packets.
2. **Whitening IV missing bit 6.** The BLE whitening LFSR starts as
   `{1, ch5…ch0}`. The nRF takes that leading 1 from `DATAWHITEIV` bit 6. Writing
   the channel index alone de-whitens the first ~4 bytes correctly, and
   everything after them is wrong.
3. **`MAXLEN` overflow.** `PCNF1.MAXLEN` is 8 bits. A `258` constant wrapped to
   `2`, which truncated every packet to two payload bytes.

> Test only against devices you own or are authorised to test, and keep
> transmissions to a bench, cabled setup, or shielded enclosure where required.
