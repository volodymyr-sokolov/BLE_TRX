# nrf52840-ble-trx

**A BLE packet generator and a multi-device BLE sniffer built on nRF52840 boards.**

One firmware image (`ble_trx`) drives the nRF52840 RADIO directly, with no
SoftDevice and no Bluetooth stack. Two host scripts use it:

| | |
|---|---|
| [`ble_tx/ble_tx.py`](ble_tx/README.md) | **Transmit** advertising or data-channel packets with full control of every on-air field. Includes **raw PDU** transmission (whitespace is removed automatically: `" 80 60 64 1c ae 7a"` → `8060641cae7a`) and **replay** of captured packets. |
| [`ble_rx/ble_rx.py`](ble_rx/README.md) | **Sniff** with one or several boards **at the same time**, each on its own channel (advertising or data, any access address). Results go to the screen and/or `.jsonl` / `.csv` / `.txt` / `.pcapng`, and **every packet names the board that received it**. |
| [`firmware/`](firmware/README.md) | The Zephyr / nRF Connect SDK firmware sources, with build / flash scripts and the command reference. |
| [`firmware_bin/`](firmware_bin/README.md) | **Compiled firmware**, ready to flash: `.hex` / `.uf2` for each board, plus SHA-256 checksums. No SDK needed. |

```text
 ┌──────────── PC ─────────────┐   USB CDC (JSON lines)  ┌── nRF52840 #1 ──┐
 │ ble_tx.py ──────────────────┼────────────────────────►│ ble_trx  TX/RX  │ ))) 2.4 GHz
 │ ble_rx.py ◄─ merge, decode,─┼─────────────────────────│                 │
 │   label, save .jsonl/.csv/  │                         └─────────────────┘
 │   .txt/.pcapng              │◄────────────────────────┌── nRF52840 #2 ──┐
 └─────────────────────────────┘                         │ ble_trx  TX/RX  │ )))
                                                         └─────────────────┘
```

## Features

**Transmitter**
- Legacy advertising PDUs: ADV_IND, ADV_DIRECT_IND, ADV_NONCONN_IND, ADV_SCAN_IND.
  Settable address and type, AD data / Local Name, event counter.
- Any of the 40 channels (up to 8 per event) with any access address and CRC
  init, so data-channel packets work too.
- TX power from −40 to +8 dBm. BLE 1M or Nordic proprietary 1M modulation.
  Interval, inter-channel gap, advDelay.
- Deliberate CRC errors (`--crcerr`). A second "twin" transmitter personality
  that can share the main address (impersonation test signal).
- **Raw PDUs**, 2–257 bytes, sent verbatim. Input is cleaned of whitespace,
  separators, `0x` prefixes, hexdump offsets and a trailing CRC. The LENGTH
  field is fixed, kept or rejected as you choose.
- `--raw-from capture.jsonl`: retransmit a packet captured by `ble_rx`.
- Counted (`--count N`) or timed (`--duration S`) runs, save / load / autostart
  in flash, interactive shell.

**Receiver**
- Any number of boards at once, each with its own channel or hop list, access
  address, CRC init, PHY and CRC filter.
- Hop lists can mix advertising channels and data channels with their own access
  address, e.g. `ch=37/5:0x9E8B4B27:0x123456`.
- Hardware timestamps (1 µs, captured by PPI at the end of the access address),
  RSSI, CRC verdict and received CRC. Drop counters if the host falls behind.
- PDU decoding: advertising types, AdvA / TargetA / ScanA / InitA, AD
  structures (name, flags, manufacturer data, …), CONNECT_IND link parameters,
  and data PDUs (LLID, SN/NESN/MD, LL control opcode).
- Host-side filters by AdvA, PDU type, RSSI, or a byte pattern.
- Output: readable text, JSON lines, CSV, and pcapng for Wireshark, with one
  interface per board.

## Hardware

### Supported boards

| Board | Notes |
|---|---|
| **Adafruit Feather nRF52840 Express** | default build target (`adafruit_feather_nrf52840/nrf52840/uf2`) |
| **nice!nano v2** | same target plus `boards/nice_nano_v2.overlay` (LED on P0.15); image builds, not yet run on hardware |
| other nRF52840 boards with the **Adafruit nRF52 bootloader** (application at 0x26000) | use the Feather image; only the status LED pin may differ |

**Test setup:** two nRF52840 boards with the Adafruit nRF52 bootloader, both
running the Feather image. Each enumerated as USB `239A:00B3` in bootloader
mode and as `1915:520F` running ble_trx. Both were connected over USB to one
Windows 11 PC, used their on-board antennas, and shared the room with the BLE
devices that happened to be advertising nearby.

### nRF52840 SoC

| | |
|---|---|
| CPU | Arm Cortex-M4F, 64 MHz |
| Memory | 1 MB flash, 256 KB RAM. ble_trx uses ~95 KB flash and ~95 KB RAM |
| Radio | 2.4 GHz multiprotocol: Bluetooth LE 1M / 2M / Coded, IEEE 802.15.4, Nordic proprietary 1M/2M |
| Channels used | BLE channels 0–39, 2402–2480 MHz, 2 MHz spacing |
| TX power | −40 … +8 dBm (ble_trx steps: 8, 7, 6, 5, 4, 3, 2, 0, −4, −8, −12, −16, −20, −40) |
| RX sensitivity | about −95 dBm (BLE 1M, per the nRF52840 datasheet) |
| Radio features used | hardware whitening, CRC-24, RSSI sampling, PPI-triggered timer capture for timestamps |
| Host link | USB 2.0 full-speed device, CDC-ACM serial port |
| Clock | HFXO (32 MHz crystal) forced on for an accurate carrier |

### Flash layout (Adafruit bootloader)

| Address | Region |
|---|---|
| 0x000000 | MBR (4 KB) |
| 0x001000 | SoftDevice S140 v6 area (148 KB; present on the board, never enabled) |
| 0x026000 | **ble_trx application** (792 KB available) |
| 0x0EC000 | saved configuration (32 KB) |
| 0x0F4000 | Adafruit nRF52 bootloader (48 KB) |

### What you need

- 1 nRF52840 board to transmit or to sniff; **2 or more** to transmit and
  receive at once, or to sniff several channels simultaneously.
- A USB data cable for each board.
- A PC with Python 3.9+ (tested with 3.14 on Windows 11). The scripts only
  need `pyserial` and should also run on Linux and macOS, but this is untested.
  The build and flash helpers are PowerShell; the underlying `west` and
  `adafruit-nrfutil` commands are cross-platform.
- To **flash**: `adafruit-nrfutil` (pip). No SWD probe is needed.
- To **build** the firmware yourself: nRF Connect SDK v3.1.1. Otherwise, use
  the compiled images in [`firmware_bin/`](firmware_bin/README.md).

## Repository layout

```text
nrf52840-ble-trx/
├── README.md                ← this file
├── LICENSE                  MIT
├── requirements.txt         pyserial, adafruit-nrfutil, pytest
├── firmware/                ble_trx firmware sources (nRF Connect SDK v3.1.1 / Zephyr)
│   ├── README.md            build, flash, full command reference
│   ├── src/                 main.c hostif.c cmd.c adv.c rx.c store.c sim.h
│   ├── prj.conf  pm_static.yml  CMakeLists.txt  boards/
│   └── tools/               build.ps1  flash.ps1  ncs_env.ps1  talk.py
├── firmware_bin/            compiled firmware, ready to flash
│   ├── README.md            which file for which board, flashing, checksums
│   ├── ble_trx_feather.hex  ble_trx_feather.uf2
│   ├── ble_trx_nicenano.hex ble_trx_nicenano.uf2
│   └── SHA256SUMS.txt
├── ble_tx/                  packet generator
│   ├── ble_tx.py
│   └── README.md            options, raw transmission, examples
├── ble_rx/                  multi-device sniffer
│   ├── ble_rx.py
│   └── README.md            device specs, outputs, examples
├── blelib/                  shared Python library
│   ├── device.py            serial link to a board
│   ├── args.py              access-address / CRC-init argument checks
│   ├── hexutil.py           whitespace / separator / hexdump-tolerant hex parsing
│   ├── raw.py               raw PDU checks (LENGTH policy, trailing-CRC removal)
│   ├── crc.py               BLE CRC-24, on-air byte order
│   ├── pdu.py               PDU / AD decoder
│   └── output.py            text / JSONL / CSV / pcapng writers
├── tests/                   pytest unit tests (no hardware)
│   └── hw/run_hw_tests.py   hardware test suite for two boards
└── docs/TEST_REPORT.md      results of the last hardware test run
```

## Quick start

```powershell
pip install -r requirements.txt

# 1. Flash each board with the compiled firmware from firmware_bin\
#    (use -Prebuilt nicenano for a nice!nano). If the board already runs ble_trx,
#    or another app with a dfu command or the 1200-baud touch, the port is
#    enough; otherwise double-tap RESET and pass the bootloader's port.
.\firmware\tools\flash.ps1 -Port COM7 -Prebuilt feather
.\firmware\tools\flash.ps1 -Port COM5 -Prebuilt feather

# 2. Find them
python ble_tx/ble_tx.py --list

# 3. Transmit on board A while sniffing with board B
python ble_rx/ble_rx.py --dev COM5,label=B,ch=37 --adva DE:AD:BE:EF:12:34      # terminal 1
python ble_tx/ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --name blesim --start   # terminal 2
python ble_tx/ble_tx.py --port COM7 --stop

# 4. Raw packet; the spaces are removed automatically
python ble_tx/ble_tx.py --port COM7 --defaults --raw " 42 0b 66 55 44 33 22 11 04 09 52 41 57" --channels 37 --duration 5

# 5. Sniff with both boards at once and save everything
python ble_rx/ble_rx.py --dev COM7,label=A,ch=37 --dev COM5,label=B,ch=38/39,dwell=200 `
    -o capture.jsonl -o capture.pcapng --duration 30
```

To build the firmware instead of using the compiled images, see
[firmware/README.md](firmware/README.md#build):

```powershell
.\firmware\tools\build.ps1                       # Feather
.\firmware\tools\build.ps1 -Board nicenano       # nice!nano v2
.\firmware\tools\flash.ps1 -Port COM7            # flashes firmware\build_feather
.\firmware\tools\build.ps1 -Release              # both boards -> refresh firmware_bin\ and its checksums
```

## Testing

```powershell
python -m pytest                                    # 43 unit tests, no hardware needed
python tests/hw/run_hw_tests.py --a COM7 --b COM5   # 14 hardware tests with two boards
```

The hardware suite runs the real scripts exactly as documented and checks
the captured packets. [docs/TEST_REPORT.md](docs/TEST_REPORT.md) has the
latest run, with every command line and its output. It covers:

| | |
|---|---|
| T01–T06 | advertising TX → RX: PDU types, addresses, AD data, `--count`, power, CRC errors, nrf1m, twin, hopping receiver |
| T07–T08 | raw transmission, including the whitespace example and both LENGTH policies |
| T09 | data-channel packets (LL_DATA, LL_CONTROL) with a custom access address and CRC init |
| T10 | advertising **and** data packets received by one board in the same capture |
| T11 | receive an **external** advertiser's packet and retransmit it byte-exact (via `--raw-from`, and by pasting RAW + CRC) |
| T12–T13 | **both boards sniffing at once**, on different channels and on the same channel, with every format written and per-device labels checked (pcapng interfaces included) |
| T14 | save / load / erase, `boot rx` autostart, info, help, `--cmd`, shell |

## Responsible use

These tools transmit on the 2.4 GHz ISM band and can imitate other
devices' packets. Transmit only within your local regulations. Test only
devices you own or are authorised to test. Use a bench, cabled setup or
shielded enclosure where isolation matters. Sniffed captures can contain
other people's device addresses: the hardware test report masks them, and
`.gitignore` keeps captures out of the repository.

## License

MIT, see [LICENSE](LICENSE).
