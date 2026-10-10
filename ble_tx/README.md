# ble_tx — BLE packet generator

`ble_tx.py` configures and runs the TX side of the [ble_trx firmware](../firmware/README.md)
on one nRF52840 board. Every firmware TX setting has a command-line option.
On top of those, it adds:

- **raw packet transmission**, with automatic whitespace removal:
  `" 80 60 64 1c ae 7a"` → `8060641cae7a`;
- **replay** of a packet captured by [`ble_rx`](../ble_rx/README.md) (`--raw-from`);
- counted / timed runs with per-run statistics;
- an interactive shell and a pass-through for any firmware command.

Requirements: Python 3.9+ (tested with 3.14) and `pyserial` (`pip install -r requirements.txt`).

```text
python ble_tx/ble_tx.py --port PORT [options]
python ble_tx/ble_tx.py --list                  # find the boards
python ble_tx/ble_tx.py -h                      # every option
```

`--port` takes `COM7`, `/dev/ttyACM0`, etc., or `auto` when exactly one ble_trx
board is connected. Options are applied in a fixed order, whatever order you
type them in:

`--defaults`/`--load` → identity and PDU → radio → timing → twin → raw →
`--erase`/`--boot`/`--save` → `--cmd` → run (`--count` / `--start` / `--duration`) →
`--stop` → `--listen` → `--info` → `--shell` → `--reboot`/`--dfu`.

Settings stay on the board between invocations (in RAM; also across power
cycles after `--save`). Add `--defaults` to start from a clean configuration.

## Options

| Group | Option | Firmware command | Notes |
|---|---|---|---|
| device | `--port`, `--list`, `-q/--quiet` | | |
| reset | `--defaults`, `--load` | `defaults`, `load` | |
| identity / PDU | `--addr AA:BB:CC:DD:EE:FF` | `addr` | AdvA, human order |
| | `--type public\|random` | `type` | TxAdd |
| | `--pdu adv_ind\|adv_direct_ind\|adv_nonconn_ind\|adv_scan_ind` | `pdu` | |
| | `--direct ADDR` | `direct` | TargetA of ADV_DIRECT_IND |
| | `--data HEX` | `data` | AD structures ≤ 31 bytes, spaces allowed |
| | `--name TEXT` | `name` | Flags + Complete Local Name; names with spaces are built on the host |
| | `--counter on\|off` | `counter` | 16-bit event counter AD |
| radio | `--channels CH…` | `channels` | 0–39, up to 8; 37/38/39 = advertising |
| | `--power DBM` | `power` | 8 … −40 |
| | `--phy ble1m\|nrf1m` | `phy` | |
| | `--aa 0x…`, `--crcinit 0x…` | `aa`, `crcinit` | for data channels / custom links |
| | `--crcerr PCT` | `crcerr` | % of events with a corrupted CRC |
| timing | `--interval MS`, `--gap US`, `--adv-delay on\|off` | `interval`, `gap`, `advdelay` | |
| twin | `--twin on\|off`, `--twin-addr ADDR\|same`, `--twin-phy`, `--twin-power`, `--twin-pct` | `twin …` | second transmitter personality |
| raw | `--raw HEX` | `raw` | see below |
| | `--raw-from FILE` (+ `--pick N`, `--pick-adva ADDR`) | `raw` | PDU from a capture |
| | `--raw-length fix\|keep\|error` | | LENGTH-field policy |
| | `--raw-off`, `--raw-on` | `raw off/on` | back to the built PDU / re-enable the stored raw PDU |
| persistence | `--save`, `--erase`, `--boot on\|off\|rx` | `save`, `erase`, `boot` | |
| run | `--start`, `--stop` | `start`, `stop` + `rx off` | |
| | `--count N` | `start N` | waits for the `done` event, prints this run's counters |
| | `--duration S` | `start` … `stop` | prints this run's counters |
| | `--cmd LINE` (repeatable) | anything | prints the firmware's reply |
| | `--listen S`, `--info`, `--fw-help`, `--shell`, `--reboot`, `--dfu` | | |

## Raw packet transmission

`--raw` takes a **PDU**: the 2-byte link-layer header followed by the
payload, exactly as it follows the access address on air. The radio adds the
preamble, access address, CRC and whitening for the configured channel.

The input is cleaned up before sending:

| You type | Sent |
|---|---|
| `" 80 60 64 1c ae 7a"` (spaces, leading blank) | `8060641cae7a` (then the LENGTH policy below) |
| `80:60:64:1C:AE:7A`, `80-60-64…`, `0x80 0x60 …`, multi-line text | the same bytes |
| hexdump lines (`0000  60 13 e1 …  ascii`) as copied from a sniffer | the bytes, without offsets or the ASCII column |
| a sniffer RAW field **with** the 3 CRC bytes at the end | CRC removed (the tool reports whether it matched) |

The radio transmits as many payload bytes as the header's LENGTH byte (byte 1)
says. When LENGTH disagrees with the bytes you gave, `--raw-length` decides:

- `fix` (default): LENGTH is rewritten to match, so `80 60 64 1c ae 7a` goes out as
  `80 04 64 1c ae 7a`.
- `keep`: the bytes are sent as given. With LENGTH 0x60, the radio sends a 98-byte
  PDU: your 6 bytes plus 92 zero bytes.
- `error`: the tool refuses.

The firmware itself also ignores whitespace in `raw` and `data`, so
`raw 80 60 64 1c ae 7a` typed in a terminal or `--shell` works too.

## Examples

Every example below was run on the hardware (see [TEST_REPORT](../docs/TEST_REPORT.md)).

```powershell
# Plain advertiser: ADV_IND "blesim" with an event counter on 37/38/39, every 30 ms, for 5 s
python ble_tx/ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --type random `
    --name blesim --counter on --channels 37 38 39 --interval 30 --duration 5

# Non-connectable, public address, custom AD data (spaces allowed), -8 dBm, exactly 60 events
python ble_tx/ble_tx.py --port COM7 --defaults --pdu adv_nonconn_ind --type public `
    --addr 11:22:33:44:55:66 --data "02 01 06 05 ff 34 12 ab cd" --counter off `
    --power -8 --channels 37 --interval 20 --count 60

# Directed advertising
python ble_tx/ble_tx.py --port COM7 --defaults --pdu adv_direct_ind --direct AA:BB:CC:00:11:22 --start

# 30 % of events with a broken CRC
python ble_tx/ble_tx.py --port COM7 --defaults --crcerr 30 --channels 37 --interval 15 --duration 5

# Impersonation test signal: twin with the same address, nrf1m modulation, +4 dBm, half the events
python ble_tx/ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --twin on --twin-addr same `
    --twin-phy nrf1m --twin-power 4 --twin-pct 50 --channels 37 --interval 20 --count 200
```

### Raw packets

```powershell
# Raw ADV_NONCONN_IND (random address 11:22:33:44:55:66, name "RAW"), typed with spaces
python ble_tx/ble_tx.py --port COM7 --defaults --raw " 42 0b 66 55 44 33 22 11 04 09 52 41 57" `
    --channels 37 --interval 20 --duration 4

# The whitespace example: LENGTH fixed to 4 (default) ...
python ble_tx/ble_tx.py --port COM7 --defaults --raw " 80 60 64 1c ae 7a" --channels 37 --duration 3
# ... or sent exactly as typed (radio sends the 98-byte PDU LENGTH=0x60 asks for)
python ble_tx/ble_tx.py --port COM7 --defaults --raw " 80 60 64 1c ae 7a" --raw-length keep --channels 37 --duration 3

# Data-channel packets: LL_DATA "Hello" on ch 5 and an LL_CONTROL PDU on ch 22, custom AA / CRC init
python ble_tx/ble_tx.py --port COM7 --defaults --raw "0e 05 48 65 6c 6c 6f" --channels 5 `
    --aa 0x9E8B4B27 --crcinit 0x123456 --interval 20 --duration 4
python ble_tx/ble_tx.py --port COM7 --defaults --raw "03 02 02 13" --channels 22 `
    --aa 0x9E8B4B27 --crcinit 0x123456 --interval 20 --duration 3

# Sniffer RAW pasted with its CRC: the CRC is recognised and removed
python ble_tx/ble_tx.py --port COM7 --defaults --channels 37 --duration 4 `
    --raw "60 1f 7f 3f 8a 16 df 7c 02 01 1a 03 03 0a 18 11 07 24 e6 7f a8 8a b7 1c 8e 7d 42 3e 7e cd 50 1d 3e 46 9c df"
```

### Receive an advertising packet and retransmit it

```powershell
# 1. capture with the other board (see ble_rx)
python ble_rx/ble_rx.py --dev COM5,ch=37 --duration 5 -o capture.jsonl
# 2. replay the first packet of one advertiser, byte for byte
python ble_tx/ble_tx.py --port COM7 --defaults --raw-from capture.jsonl --pick-adva C0:FF:EE:00:00:01 `
    --channels 37 38 39 --interval 20 --duration 10
#    or the 5th packet in the file, whatever its sender
python ble_tx/ble_tx.py --port COM7 --raw-from capture.jsonl --pick 4 --start
```

`--raw-from` reads ble_rx `.jsonl` and `.csv` captures (`pdu` column), or any
text file containing one hex PDU.

### Status, persistence, shell

```powershell
python ble_tx/ble_tx.py --list
python ble_tx/ble_tx.py --port COM7 --info                         # full configuration + counters
python ble_tx/ble_tx.py --port COM7 --fw-help                      # firmware command list
python ble_tx/ble_tx.py --port COM7 --cmd "rx info" --cmd "raw"    # any firmware command / query
python ble_tx/ble_tx.py --port COM7 --name beacon --start --save --boot on   # advertise at every power-up
python ble_tx/ble_tx.py --port COM7 --stop --boot off --save
python ble_tx/ble_tx.py --port COM7 --erase                        # forget the saved configuration
python ble_tx/ble_tx.py --port COM7 --listen 5                     # print what the board says for 5 s
python ble_tx/ble_tx.py --port COM7 --shell                        # type firmware commands; 'quit' to leave
python ble_tx/ble_tx.py --port COM7 --dfu                          # enter the bootloader (for flashing)
```

In `--shell`, `raw …` and `data …` lines are whitespace-normalised before
they are sent, the same as `--raw`.

## Output

Every command prints its acknowledgement. A run ends with **this run's**
counters (the firmware's counters are cumulative since power-up):

```text
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 37                              -> channels
  raw 42 0a 66 55 44 33 22 11 03 09 41 42  -> 12 bytes, on air 12 (ADV_NONCONN_IND 11:22:33:44:55:66(r) 'AB')
  advertising
  stopped after 1 s: events 10, packets 10, twin events 0, CRC-corrupted 0
```

Exit status: 0 on success; 1 when the board rejects a command, does not reply,
or the hex is invalid; 2 for usage errors.
