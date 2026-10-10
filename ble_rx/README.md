# ble_rx — BLE sniffer for one or several nRF52840 boards

`ble_rx.py` puts one or more boards running the [ble_trx firmware](../firmware/README.md)
into receive mode at the same time, each with its own channel (or hop list),
access address, CRC init and PHY. It merges what they hear into one stream:

- **on screen** (readable text or JSON lines), and/or
- **in files**: `.jsonl`, `.csv`, `.txt`, `.pcapng` (several at once).

**Every packet entry names the device that received it.** Each entry carries
the device label (`--dev …,label=A`, default: the port name), its port and its
USB serial number. In pcapng, each device is a separate capture interface, so
Wireshark's *Interface* column / `frame.interface_name` tells the receivers apart.

Advertising channels (37/38/39, advertising access address) and data
channels (0–36, any access address and CRC init) are both supported. One
board can even hop between an advertising channel and a data channel that
uses a different access address.

Requirements: Python 3.9+ (tested with 3.14) and `pyserial`.

## Usage

```text
python ble_rx/ble_rx.py --dev SPEC [--dev SPEC ...] [options]
python ble_rx/ble_rx.py --list                 # connected ble_trx boards
```

### Device spec

`--dev PORT[,key=value...]` is repeatable, one per board. `--dev auto` adds
every connected ble_trx board; any keys after it apply to all of them.

| Key | Meaning | Default |
|---|---|---|
| `label=A` | name shown in every packet entry and used as the pcapng interface name | the port |
| `ch=37` | channel 0–39 | `--ch` (37) |
| `ch=37/38/39` | hop list, up to 8 channels (`+` also works as a separator) | |
| `ch=37/5:0x9E8B4B27:0x123456` | hop-list entry with its own access address `:aa[:crcinit]` | |
| `dwell=100` | ms per channel when hopping | `--dwell` (100) |
| `aa=0x9E8B4B27` | access address | `--aa` (0x8E89BED6) |
| `crcinit=0x123456` | CRC init | `--crcinit` (0x555555) |
| `phy=ble1m\|nrf1m` | receiver modulation | `--phy` (ble1m) |
| `crc=ok\|all` | good-CRC packets only, or CRC failures too | `--crc` (ok) |

The options `--ch`, `--dwell`, `--aa`, `--crcinit`, `--phy` and `--crc` set the
defaults for every board. Keys in a spec override them for that board.

### Filters (applied on the host)

| Option | Keeps |
|---|---|
| `--adva ADDR` (repeatable) | packets whose AdvA is ADDR |
| `--type T` (repeatable) | PDU types: `ADV_IND`, `ADV_NONCONN_IND`, `SCAN_REQ`, `SCAN_RSP`, `CONNECT_IND`, `ADV_EXT_IND`, … or `LL_DATA_START`, `LL_DATA_CONT`, `LL_CONTROL` |
| `--min-rssi DBM` | packets at least this strong |
| `--contains HEX` | PDUs containing these bytes (whitespace allowed) |

### Output and run control

| Option | |
|---|---|
| `-o FILE` / `--out FILE` (repeatable) | write to FILE; the format follows the extension: `.jsonl`/`.json`, `.csv`, `.txt`/`.log`, `.pcapng` |
| `--format text\|json\|none` | screen output (default `text`) |
| `-q` / `--quiet` | no per-packet screen output (files are still written) |
| `--duration S`, `--count N` | stop after S seconds / N packets (after filters); otherwise run until Ctrl-C |
| `--no-stats` | skip the summary |

When it stops, `ble_rx` turns the receivers off and prints a summary for each
device. It shows the packets the host displayed or saved and, from the
firmware, the packets received, CRC failures, packets **dropped** because the
USB link was too slow, the number of hops, and gaps in the firmware's packet
sequence numbers.

## Examples

All of these were run on two boards (see [TEST_REPORT](../docs/TEST_REPORT.md)).

```powershell
# One board, advertising channel 37, readable text on screen
python ble_rx/ble_rx.py --dev COM7

# Two boards at once on different advertising channels; screen + three files
python ble_rx/ble_rx.py --dev COM7,label=A,ch=37 --dev COM5,label=B,ch=38 `
    -o capture.jsonl -o capture.csv -o capture.pcapng

# Board A on 37, board B hopping 38/39 every 200 ms, files only, 10 seconds
python ble_rx/ble_rx.py --dev COM7,label=A,ch=37 --dev COM5,label=B,ch=38/39,dwell=200 `
    --duration 10 -q -o dual.jsonl -o dual.pcapng -o dual.txt

# Both boards on the same channel (compare two receive positions / antennas)
python ble_rx/ble_rx.py --dev COM7,label=A --dev COM5,label=B --ch 37 --duration 8 -o same.jsonl

# Every connected board, all three advertising channels, hopping
python ble_rx/ble_rx.py --dev auto --ch 37 38 39 --dwell 100

# Data channel 5 with a link's access address and CRC init
python ble_rx/ble_rx.py --dev COM5,ch=5,aa=0x9E8B4B27,crcinit=0x123456

# Advertising and data packets on ONE board: hop ch 37 (adv AA) <-> ch 5 (data AA)
python ble_rx/ble_rx.py --dev "COM5,ch=37/5:0x9E8B4B27:0x123456,dwell=100" -o mixed.jsonl

# Only one advertiser, only strong packets, include CRC failures
python ble_rx/ble_rx.py --dev COM7,crc=all --adva C0:FF:EE:00:00:01 --min-rssi -70

# Only scan requests, as JSON lines on screen
python ble_rx/ble_rx.py --dev COM7 --type SCAN_REQ --format json
```

### Receive an external advertising packet and retransmit it

```powershell
python ble_rx/ble_rx.py --dev COM5,ch=37 --duration 5 -o capture.jsonl            # 1. capture
python ble_tx/ble_tx.py --port COM7 --defaults --raw-from capture.jsonl `
    --pick-adva C0:FF:EE:00:00:01 --channels 37 --interval 20 --duration 10        # 2. replay
python ble_rx/ble_rx.py --dev COM5,ch=37 --adva C0:FF:EE:00:00:01 --duration 10    # 3. watch it
```

You can also copy the `PDU` from the text output, or the `raw` field (PDU +
CRC), and paste it into `ble_tx --raw "…"`. Spaces and the trailing CRC are
removed automatically.

## Output formats

**Screen / `.txt`**: two lines per packet: host time, `[device]`, channel,
RSSI, CRC verdict and a decoded summary, then the PDU and its CRC (on-air
byte order):

```text
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
15:14:37.954692 [B] ch37  -58 dBm CRC OK  ADV_IND DE:AD:BE:EF:12:34(r) 'blesim'
      PDU 40 15 34 12 ef be ad de 02 01 06 07 09 62 6c 65 73 69 6d 03 ff 0a 00  CRC 8d4713
15:14:42.019839 [B] ch37  -48 dBm CRC OK  ADV_NONCONN_IND 11:22:33:44:55:66(r) 'RAW'
      PDU 42 0b 66 55 44 33 22 11 04 09 52 41 57  CRC 87b2f3
```

**`.jsonl`**: one JSON object per packet. **`.csv`**: the same fields as columns.

| Field | Meaning |
|---|---|
| `device`, `port`, `serial` | which board received the packet: label, serial port, USB serial number |
| `host_time`, `host_iso` | packet time on the host clock (Unix seconds / ISO 8601) |
| `dev_time` | the board's own timestamp, in seconds since power-up, 1 µs resolution, taken in hardware at the end of the access address |
| `n` | the board's packet sequence number |
| `ch`, `freq_mhz`, `rssi`, `crc_ok`, `aa` | channel index, centre frequency, RSSI in dBm, CRC verdict, access address |
| `type`, `adva`, `name`, `summary` | decoded PDU type, advertiser address, Local Name, one-line summary |
| `pdu` | header + payload (what `ble_tx --raw` takes) |
| `crc` | the 3 CRC bytes in on-air order |
| `raw` | `pdu` + `crc`, the complete packet after the access address |

**`.pcapng`**: link type `BLUETOOTH_LE_LL_WITH_PHDR` (256). Wireshark decodes
it directly, with RSSI, channel, the access address and the CRC verdict in the
pseudo-header. Each board is one interface named after its label, described as
`<port> serial <usb serial>`. Each packet carries a comment with the device,
`n` and `dev_time`. The file was validated with scapy, which dissected every
frame and recomputed every CRC with no mismatch.

### Timing

Each board timestamps packets in hardware with 1 µs resolution (`dev_time`).
`host_time` maps that clock onto the PC clock for each board, using the
lowest-latency USB arrival seen so far. So:

- packets from one board keep their exact µs spacing;
- packets from different boards line up to within the USB latency, typically
  a few ms.

The screen output is sorted within each read cycle, so packets from
different boards can appear slightly out of order. Sort by `host_time` when
you need a strict cross-device order.

## Notes and limits

- A board receives on one channel at a time. To cover several channels
  continuously, use several boards; hop lists cover them in turn.
- `ble_rx` does not follow connections (channel hopping by the connection's
  channel map). Data channels are received at fixed frequencies with a given
  access address. Use a `CONNECT_IND` (decoded: access address, CRC init, hop
  increment) to configure a board for a connection's channel.
- Starting `ble_rx` on a board stops any transmission on it, and `ble_tx`
  stops reception.
- Throughput: hundreds of advertising packets/s per board were received with
  0 drops in testing. If the host falls behind, the `dropped` counter in the
  summary says so.
