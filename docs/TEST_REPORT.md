# Hardware test report

Run 2026-10-10 15:19 with `tests/hw/run_hw_tests.py --a COM7 --b COM5`: **14/14 passed**.

Board A = `COM7` (transmitter, and the second sniffer in T12/T13), board B = `COM5` (receiver). Captures are written to `docs/test_captures/` (git-ignored: they contain nearby third-party devices). Third-party device addresses are masked in this report.

| Test | What | Result |
|---|---|---|
| T01 | Advertising: ble_tx on A (ADV_IND, name, counter, ch 37/38/39) while ble_rx on B listens on 37. | PASS: 122 ADV_IND from DE:AD:BE:EF:12:34 'blesim', counter 50..177 (6 events missed), all CRC OK; plus 0 SCAN_REQ from external scanners |
| T02 | Receiver hopping 37/38/39 (dwell 50 ms) sees the transmitter on every advertising channel. | PASS: packets per channel: ch37=58, ch38=57, ch39=55 |
| T03 | ADV_NONCONN_IND, public address, custom AD data with spaces, counter off, -8 dBm, --count 60. | PASS: 59/60 packets, every PDU == 020f66554433221102010605ff3412abcd (header 0x02: NONCONN, TxAdd=0) |
| T04 | ADV_DIRECT_IND with --direct TargetA. | PASS: 117 ADV_DIRECT_IND, AdvA DE:AD:BE:EF:12:34 -> TargetA AA:BB:CC:00:11:22 |
| T05 | --crcerr 30 with the receiver reporting all CRCs: ~30 % of packets fail the CRC. | PASS: 79/255 = 31% with a bad CRC (asked for 30 %) |
| T06 | nrf1m modulation and the twin: main ble1m + twin nrf1m (same address, 50 %), receiver ble1m. | PASS: twin run (ble1m main + nrf1m twin, one address): 181/200 events received by a ble1m receiver; nrf1m TX -> nrf1m RX: 100/100 |
| T07 | Raw advertising PDU typed with spaces is sent byte-exact. | PASS: 155 packets, PDU == 420b6655443322110409524157 (ADV_NONCONN_IND 11:22:33:44:55:66(r) 'RAW') |
| T08 | The README example ' 80 60 64 1c ae 7a': LENGTH fixed (default) and sent as given (keep). | PASS: fix: 119 x 8004641cae7a; keep: 117 x 8060641cae7a + 92 zero bytes (98-byte PDU as LENGTH=0x60 demands) |
| T09 | Data-channel packets: LL_DATA_START 'Hello' and an LL_CONTROL PDU with a custom AA / CRC init. | PASS: 157 x 'LL_DATA_START SN=1 NESN=1 MD=0 len=5' (payload 'Hello') on ch 5; 121 x 'LL_CONTROL SN=0 NESN=0 MD=0 len=2 opcode=0x02' on ch 22 |
| T10 | One receiver hops between ch 37 (advertising AA) and ch 5 (data AA): both kinds in one capture. | PASS: 224 advertising packets (ch 37, 7 external advertisers) and 196 data packets (ch 5, AA 0x9E8B4B27) in one capture |
| T11 | Capture an external advertiser with B, replay its PDU with A, receive the replay with B. | PASS: external XX:XX:XX:XX:XX:DC (ADV_NONCONN_IND, 39-byte PDU, -67 dBm): replay on ch 10 received 158 times byte-exact; RAW+CRC pasted with spaces -> CRC removed, ch 37 rate 9.6/s -> 32.6/s, byte-exact |
| T12 | Both boards sniff at once (A: ch 37, B: hop 38/39), saved to .jsonl/.csv/.pcapng/.txt. | PASS: A: 562 packets on ch 37; B: 646 on ch 38/39; 9 advertisers heard by both; pcapng has interfaces A and B with matching counts |
| T13 | Both boards on ch 37 simultaneously: the same packets are reported by both, tagged A and B. | PASS: A 446 packets, B 440 packets; 97% of distinct PDUs seen by both receivers |
| T14 | save / defaults / load / erase, --info, --fw-help, --cmd, --stop, --list, shell via stdin. | PASS: saved config restored by load; erase -> 'no saved configuration'; boot rx -> receiving after reboot; help/info/list/shell OK |

## T01 — Advertising: ble_tx on A (ADV_IND, name, counter, ch 37/38/39) while ble_rx on B listens on 37.

**PASS** — 122 ADV_IND from DE:AD:BE:EF:12:34 'blesim', counter 50..177 (6 events missed), all CRC OK; plus 0 SCAN_REQ from external scanners

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 8 -o docs\test_captures\t01_adv.jsonl --quiet --adva DE:AD:BE:EF:12:34    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --type random --name blesim --counter on --channels 37 38 39 --interval 30 --duration 5
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  type random                              -> type
  name blesim                              -> name
  counter on                               -> counter
  channels 37 38 39                        -> channels
  interval 30                              -> interval
  advertising
  stopped after 5 s: events 128, packets 384, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 122 packets shown/saved in 8.0 s
[B] shown 122; firmware: received 19877, CRC failures 3668, dropped (USB too slow) 0, hops 584; host gaps 0
wrote docs\test_captures\t01_adv.jsonl
```

## T02 — Receiver hopping 37/38/39 (dwell 50 ms) sees the transmitter on every advertising channel.

**PASS** — packets per channel: ch37=58, ch38=57, ch39=55

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37/38/39,dwell=50 --duration 8 -o docs\test_captures\t02_hop.jsonl --quiet --adva DE:AD:BE:EF:12:34    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --channels 37 38 39 --interval 20 --duration 5
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  channels 37 38 39                        -> channels
  interval 20                              -> interval
  advertising
  stopped after 5 s: events 173, packets 519, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37/38/39 hop 50 ms, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 170 packets shown/saved in 8.0 s
[B] shown 170; firmware: received 20560, CRC failures 3713, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t02_hop.jsonl
```

## T03 — ADV_NONCONN_IND, public address, custom AD data with spaces, counter off, -8 dBm, --count 60.

**PASS** — 59/60 packets, every PDU == 020f66554433221102010605ff3412abcd (header 0x02: NONCONN, TxAdd=0)

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 8 -o docs\test_captures\t03_opts.jsonl --quiet --adva 11:22:33:44:55:66    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --pdu adv_nonconn_ind --type public --addr 11:22:33:44:55:66 --data "02 01 06 05 ff 34 12 ab cd" --counter off --power -8 --channels 37 --interval 20 --count 60
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr 11:22:33:44:55:66                   -> addr
  type public                              -> type
  pdu adv_nonconn_ind                      -> pdu
  data 02010605ff3412abcd                  -> data
  counter off                              -> counter
  channels 37                              -> channels
  power -8                                 -> power
  interval 20                              -> interval
  sending 60 events ...
  events 60, packets 60, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 59 packets shown/saved in 8.0 s
[B] shown 59; firmware: received 21063, CRC failures 3754, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t03_opts.jsonl
```

## T04 — ADV_DIRECT_IND with --direct TargetA.

**PASS** — 117 ADV_DIRECT_IND, AdvA DE:AD:BE:EF:12:34 -> TargetA AA:BB:CC:00:11:22

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 6 -o docs\test_captures\t04_direct.jsonl --quiet --type ADV_DIRECT_IND --adva DE:AD:BE:EF:12:34    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --pdu adv_direct_ind --direct AA:BB:CC:00:11:22 --channels 37 --interval 20 --duration 3
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  pdu adv_direct_ind                       -> pdu
  direct AA:BB:CC:00:11:22                 -> direct
  channels 37                              -> channels
  interval 20                              -> interval
  advertising
  stopped after 3 s: events 118, packets 118, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 117 packets shown/saved in 6.0 s
[B] shown 117; firmware: received 21513, CRC failures 3789, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t04_direct.jsonl
```

## T05 — --crcerr 30 with the receiver reporting all CRCs: ~30 % of packets fail the CRC.

**PASS** — 79/255 = 31% with a bad CRC (asked for 30 %)

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37,crc=all --duration 8 -o docs\test_captures\t05_crcerr.jsonl --quiet --contains 3412efbeadde    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --crcerr 30 --channels 37 --interval 15 --duration 5
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  channels 37                              -> channels
  crcerr 30                                -> crcerr
  interval 15                              -> interval
  advertising
  stopped after 5 s: events 241, packets 241, twin events 0, CRC-corrupted 80
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC all
receiving on 1 device(s); Ctrl-C to stop
--- 255 packets shown/saved in 8.0 s
[B] shown 255; firmware: received 22259, CRC failures 3891, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t05_crcerr.jsonl
```

## T06 — nrf1m modulation and the twin: main ble1m + twin nrf1m (same address, 50 %), receiver ble1m.

**PASS** — twin run (ble1m main + nrf1m twin, one address): 181/200 events received by a ble1m receiver; nrf1m TX -> nrf1m RX: 100/100

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 8 -o docs\test_captures\t06_twin.jsonl --quiet --adva DE:AD:BE:EF:12:34    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --twin on --twin-addr same --twin-phy nrf1m --twin-power 4 --twin-pct 50 --channels 37 --interval 20 --count 200
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  channels 37                              -> channels
  interval 20                              -> interval
  twin on                                  -> twin
  twin same                                -> twin
  twin phy nrf1m                           -> twin_phy
  twin power 4                             -> twin_power
  twin pct 50                              -> twin_pct
  sending 200 events ...
  events 200, packets 200, twin events 101, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 181 packets shown/saved in 8.0 s
[B] shown 181; firmware: received 22857, CRC failures 3947, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t06_twin.jsonl
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37,phy=nrf1m --duration 8 -o docs\test_captures\t06_nrf1m.jsonl --quiet --adva DE:AD:BE:EF:12:34    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr DE:AD:BE:EF:12:34 --phy nrf1m --channels 37 --interval 20 --count 100
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  addr DE:AD:BE:EF:12:34                   -> addr
  channels 37                              -> channels
  phy nrf1m                                -> phy
  interval 20                              -> interval
  sending 100 events ...
  events 100, packets 100, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, nrf1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 100 packets shown/saved in 8.0 s
[B] shown 100; firmware: received 23362, CRC failures 3962, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t06_nrf1m.jsonl
```

## T07 — Raw advertising PDU typed with spaces is sent byte-exact.

**PASS** — 155 packets, PDU == 420b6655443322110409524157 (ADV_NONCONN_IND 11:22:33:44:55:66(r) 'RAW')

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 8 -o docs\test_captures\t07_raw.jsonl --quiet --adva 11:22:33:44:55:66    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw " 42 0b 66 55 44 33 22 11 04 09 52 41 57" --channels 37 --interval 20 --duration 4
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 37                              -> channels
  interval 20                              -> interval
  raw 42 0b 66 55 44 33 22 11 04 09 52 41 57 -> 13 bytes, on air 13 (ADV_NONCONN_IND 11:22:33:44:55:66(r) 'RAW')
  advertising
  stopped after 4 s: events 156, packets 156, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 155 packets shown/saved in 8.0 s
[B] shown 155; firmware: received 23963, CRC failures 3977, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t07_raw.jsonl
```

## T08 — The README example ' 80 60 64 1c ae 7a': LENGTH fixed (default) and sent as given (keep).

**PASS** — fix: 119 x 8004641cae7a; keep: 117 x 8060641cae7a + 92 zero bytes (98-byte PDU as LENGTH=0x60 demands)

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 6 -o docs\test_captures\t08_fix.jsonl --quiet --contains 641cae7a    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw " 80 60 64 1c ae 7a" --channels 37 --interval 20 --duration 3
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 37                              -> channels
  interval 20                              -> interval
  note: LENGTH field is 96 but 4 payload bytes were given; LENGTH set to 4 (use --raw-length keep to send as given)
  raw 80 04 64 1c ae 7a                    -> 6 bytes, on air 6 (ADV_IND)
  advertising
  stopped after 3 s: events 121, packets 121, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 155 packets shown/saved in 6.0 s
[B] shown 155; firmware: received 24391, CRC failures 3999, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t08_fix.jsonl
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 6 -o docs\test_captures\t08_keep.jsonl --quiet --contains 8060641cae7a    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw " 80 60 64 1c ae 7a" --raw-length keep --channels 37 --interval 20 --duration 3
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 37                              -> channels
  interval 20                              -> interval
  note: LENGTH field is 96 but 4 payload bytes were given; sent as given: the radio sends 96 payload bytes (zero-padded)
  raw 80 60 64 1c ae 7a                    -> 6 bytes, on air 98 (ADV_IND)
  advertising
  stopped after 3 s: events 118, packets 118, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 154 packets shown/saved in 6.0 s
[B] shown 154; firmware: received 24849, CRC failures 4012, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t08_keep.jsonl
```

## T09 — Data-channel packets: LL_DATA_START 'Hello' and an LL_CONTROL PDU with a custom AA / CRC init.

**PASS** — 157 x 'LL_DATA_START SN=1 NESN=1 MD=0 len=5' (payload 'Hello') on ch 5; 121 x 'LL_CONTROL SN=0 NESN=0 MD=0 len=2 opcode=0x02' on ch 22

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=5,aa=0x9E8B4B27,crcinit=0x123456 --duration 8 -o docs\test_captures\t09_data.jsonl --quiet    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw "0e 05 48 65 6c 6c 6f" --channels 5 --aa 0x9E8B4B27 --crcinit 0x123456 --interval 20 --duration 4
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 5                               -> channels
  aa 0x9E8B4B27                            -> aa
  crcinit 0x123456                         -> crcinit
  interval 20                              -> interval
  raw 0e 05 48 65 6c 6c 6f                 -> 7 bytes, on air 7 (LL_DATA_START SN=1 NESN=1 MD=0 len=5)
  advertising
  stopped after 4 s: events 157, packets 157, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 5, AA 0x9E8B4B27, CRC init 0x123456, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 157 packets shown/saved in 8.0 s
[B] shown 157; firmware: received 25006, CRC failures 4012, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t09_data.jsonl
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=22,aa=0x9E8B4B27,crcinit=0x123456 --duration 6 -o docs\test_captures\t09_ctrl.jsonl --quiet    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw "03 02 02 13" --channels 22 --aa 0x9E8B4B27 --crcinit 0x123456 --interval 20 --duration 3
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 22                              -> channels
  aa 0x9E8B4B27                            -> aa
  crcinit 0x123456                         -> crcinit
  interval 20                              -> interval
  raw 03 02 02 13                          -> 4 bytes, on air 4 (LL_CONTROL SN=0 NESN=0 MD=0 len=2 opcode=0x02)
  advertising
  stopped after 3 s: events 121, packets 121, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 22, AA 0x9E8B4B27, CRC init 0x123456, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 121 packets shown/saved in 6.0 s
[B] shown 121; firmware: received 25127, CRC failures 4012, dropped (USB too slow) 0, hops 745; host gaps 0
wrote docs\test_captures\t09_ctrl.jsonl
```

## T10 — One receiver hops between ch 37 (advertising AA) and ch 5 (data AA): both kinds in one capture.

**PASS** — 224 advertising packets (ch 37, 7 external advertisers) and 196 data packets (ch 5, AA 0x9E8B4B27) in one capture

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37/5:0x9E8B4B27:0x123456,dwell=100 --duration 8 -o docs\test_captures\t10_mixed.jsonl --quiet    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw "0e 05 48 65 6c 6c 6f" --channels 5 --aa 0x9E8B4B27 --crcinit 0x123456 --interval 10 --duration 6
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 5                               -> channels
  aa 0x9E8B4B27                            -> aa
  crcinit 0x123456                         -> crcinit
  interval 10                              -> interval
  raw 0e 05 48 65 6c 6c 6f                 -> 7 bytes, on air 7 (LL_DATA_START SN=1 NESN=1 MD=0 len=5)
  advertising
  stopped after 6 s: events 387, packets 387, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37/5:0x9E8B4B27:0x123456 hop 100 ms, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 420 packets shown/saved in 8.0 s
[B] shown 420; firmware: received 25549, CRC failures 4015, dropped (USB too slow) 0, hops 825; host gaps 0
wrote docs\test_captures\t10_mixed.jsonl
```

## T11 — Capture an external advertiser with B, replay its PDU with A, receive the replay with B.

**PASS** — external XX:XX:XX:XX:XX:DC (ADV_NONCONN_IND, 39-byte PDU, -67 dBm): replay on ch 10 received 158 times byte-exact; RAW+CRC pasted with spaces -> CRC removed, ch 37 rate 9.6/s -> 32.6/s, byte-exact

```text
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 5 -o docs\test_captures\t11_external.jsonl --quiet
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 284 packets shown/saved in 5.0 s
[B] shown 284; firmware: received 25835, CRC failures 4024, dropped (USB too slow) 0, hops 825; host gaps 0
wrote docs\test_captures\t11_external.jsonl
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=10 --duration 8 -o docs\test_captures\t11_replay_ch10.jsonl --quiet --adva XX:XX:XX:XX:XX:DC --type ADV_NONCONN_IND    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw-from docs\test_captures\t11_external.jsonl --pick-adva XX:XX:XX:XX:XX:DC --channels 10 --interval 20 --duration 4
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 10                              -> channels
  interval 20                              -> interval
  raw PDU from docs\test_captures\t11_external.jsonl packet #0 (device B, ch 37)
  raw 42 25 xx xx xx xx xx xx 1e ff 06 00 01 09 20 22 3b cb 78 ae d9 b8 b4 2f 29 89 b6 d6 78 80 4f bf e0 aa ff 95 2c 82 7a -> 39 bytes, on air 39 (ADV_NONCONN_IND XX:XX:XX:XX:XX:DC(r))
  advertising
  stopped after 4 s: events 158, packets 158, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 10, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 158 packets shown/saved in 8.0 s
[B] shown 158; firmware: received 25993, CRC failures 4024, dropped (USB too slow) 0, hops 825; host gaps 0
wrote docs\test_captures\t11_replay_ch10.jsonl
$ python ble_rx\ble_rx.py --dev COM5,label=B,ch=37 --duration 8 -o docs\test_captures\t11_replay_ch37.jsonl --quiet --adva XX:XX:XX:XX:XX:DC --type ADV_NONCONN_IND    # in the background
$ python ble_tx\ble_tx.py --port COM7 --defaults --raw "42 25 xx xx xx xx xx xx 1e ff 06 00 01 09 20 22 3b cb 78 ae d9 b8 b4 2f 29 89 b6 d6 78 80 4f bf e0 aa ff 95 2c 82 7a 31 7b a2" --channels 37 --interval 20 --duration 4
COM7: ble_trx v3 (serial 4EE1B5358EC2A22E), TX idle, RX idle
  defaults                                 -> defaults
  channels 37                              -> channels
  interval 20                              -> interval
  note: removed trailing CRC 317ba2 (it matched)
  raw 42 25 xx xx xx xx xx xx 1e ff 06 00 01 09 20 22 3b cb 78 ae d9 b8 b4 2f 29 89 b6 d6 78 80 4f bf e0 aa ff 95 2c 82 7a -> 39 bytes, on air 39 (ADV_NONCONN_IND XX:XX:XX:XX:XX:DC(r))
  advertising
  stopped after 4 s: events 157, packets 157, twin events 0, CRC-corrupted 0
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 1 device(s); Ctrl-C to stop
--- 228 packets shown/saved in 8.0 s
[B] shown 228; firmware: received 26591, CRC failures 4059, dropped (USB too slow) 0, hops 825; host gaps 0
wrote docs\test_captures\t11_replay_ch37.jsonl
```

## T12 — Both boards sniff at once (A: ch 37, B: hop 38/39), saved to .jsonl/.csv/.pcapng/.txt.

**PASS** — A: 562 packets on ch 37; B: 646 on ch 38/39; 9 advertisers heard by both; pcapng has interfaces A and B with matching counts

```text
$ python ble_rx\ble_rx.py --dev COM7,label=A,ch=37 --dev COM5,label=B,ch=38/39,dwell=200 --duration 10 --quiet -o docs\test_captures\t12_dual.jsonl -o docs\test_captures\t12_dual.csv -o docs\test_captures\t12_dual.pcapng -o docs\test_captures\t12_dual.txt
[A] COM7 serial 4EE1B5358EC2A22E: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
[B] COM5 serial FA55E7E8F1D2D19B: ch 38/39 hop 200 ms, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 2 device(s); Ctrl-C to stop
--- 1208 packets shown/saved in 10.0 s
[A] shown 562; firmware: received 1435, CRC failures 331, dropped (USB too slow) 0, hops 0; host gaps 0
[B] shown 646; firmware: received 27274, CRC failures 4103, dropped (USB too slow) 0, hops 877; host gaps 0
wrote docs\test_captures\t12_dual.jsonl
wrote docs\test_captures\t12_dual.csv
wrote docs\test_captures\t12_dual.pcapng
wrote docs\test_captures\t12_dual.txt
```

## T13 — Both boards on ch 37 simultaneously: the same packets are reported by both, tagged A and B.

**PASS** — A 446 packets, B 440 packets; 97% of distinct PDUs seen by both receivers

```text
$ python ble_rx\ble_rx.py --dev COM7,label=A --dev COM5,label=B --ch 37 --duration 8 -o docs\test_captures\t13_same_ch.jsonl --quiet
[A] COM7 serial 4EE1B5358EC2A22E: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
[B] COM5 serial FA55E7E8F1D2D19B: ch 37, AA 0x8E89BED6, CRC init 0x555555, ble1m, CRC ok
receiving on 2 device(s); Ctrl-C to stop
--- 886 packets shown/saved in 8.0 s
[A] shown 446; firmware: received 1882, CRC failures 455, dropped (USB too slow) 0, hops 0; host gaps 0
[B] shown 440; firmware: received 27743, CRC failures 4132, dropped (USB too slow) 0, hops 877; host gaps 0
wrote docs\test_captures\t13_same_ch.jsonl
```

## T14 — save / defaults / load / erase, --info, --fw-help, --cmd, --stop, --list, shell via stdin.

**PASS** — saved config restored by load; erase -> 'no saved configuration'; boot rx -> receiving after reboot; help/info/list/shell OK

```text
$ python ble_tx\ble_tx.py --port COM7 --defaults --addr C0:FF:EE:00:00:01 --interval 77 --save -q

$ python ble_tx\ble_tx.py --port COM7 --defaults -q

$ python ble_tx\ble_tx.py --port COM7 --load --info -q
{"ev": "info", "running": 0, "addr": "C0:FF:EE:00:00:01", "random": 1, "phy": "ble1m", "power": 0, "pdu": 0, "adlen": 11, "counter": 1}
{"ev": "twin", "on": 0, "same_addr": 0, "pct": 50, "addr": "XX:XX:XX:XX:XX:02", "phy": "nrf1m", "power": 0}
{"ev": "chan", "n": 3, "first": 37, "aa": 2391391958, "crc_init": 5592405, "interval_ms": 77, "gap_us": 1500, "crc_err_pct": 0, "target": "XX:XX:XX:XX:XX:00"}
{"ev": "stats", "events": 2445, "packets": 3047, "twin_events": 101, "crc_errors": 80}
{"ev": "raw", "on": 0, "len": 0, "pdu": ""}
{"ev": "rx", "running": 0, "channels": [37], "phy": "ble1m", "aa": "8e89bed6", "crc_init": "555555", "dwell_ms": 0, "bad_crc": 0, "packets": 1882, "crc_bad": 455, "dropped": 0, "hops": 0}
{"ev": "mode", "tx": 0, "rx": 0, "boot": "off", "adv_delay": 1, "fw": "ble_trx", "ver": 3, "out_dropped": 0}
$ python ble_tx\ble_tx.py --port COM7 --erase --defaults -q

$ python ble_tx\ble_tx.py --port COM7 --fw-help -q
commands (one per line; values are decimal unless 0x-prefixed):
start [N]            advertise forever, or N events then stop
stop
addr AA:BB:..:FF     main advertiser address (human order)
type public|random   main address type
pdu adv_ind|adv_direct_ind|adv_nonconn_ind|adv_scan_ind
power <dBm>          8,7,6,5,4,3,2,0,-4,-8,-12,-16,-20,-40
phy ble1m|nrf1m      modulation (nrf1m has a lower mod index)
channels 37 38 39    advertising and/or data channel indices
aa 0xNNNNNNNN        access address (data channels)
... (27 lines omitted) ...
save | load | erase  persist / restore / clear the configuration
boot on|off|rx       at power-up: advertise / nothing / receive
info | help | dfu | reboot
$ python ble_tx\ble_tx.py --port COM7 --cmd load --cmd "rx info" -q
load                                     -> {"err": "no saved configuration"}
  rx info                                  -> {"ev": "rx", "running": 0, "channels": [37], "phy": "ble1m", "aa": "8e89bed6", "crc_init": "555555", "dwell_ms": 0, "bad_crc": 0, "packets": 1882, "crc_bad": 455, "dropped": 0, "hops": 0}
$ python ble_tx\ble_tx.py --list
COM5     serial FA55E7E8F1D2D19B  Serielles USB-Gerät (COM5)
COM7     serial 4EE1B5358EC2A22E  Serielles USB-Gerät (COM7)
$ echo info | python ble_tx/ble_tx.py --port COM7 --shell
ble_tx shell: firmware commands, 'help' for the list, Ctrl-C / 'quit' to leave
{"ev": "info", "running": 0, "addr": "DE:AD:BE:EF:12:34", "random": 1, "phy": "ble1m", "power": 0, "pdu": 0, "adlen": 11, "counter": 1}
{"ev": "twin", "on": 0, "same_addr": 0, "pct": 50, "addr": "XX:XX:XX:XX:XX:02", "phy": "nrf1m", "power": 0}
{"ev": "chan", "n": 3, "first": 37, "aa": 2391391958, "crc_init": 5592405, "interval_ms": 100, "gap_us": 1500, "crc_err_pct": 0, "target": "XX:XX:XX:XX:XX:00"}
{"ev": "stats", "events": 2445, "packets": 3047, "twin_events": 101, "crc_errors": 80}
{"ev": "raw", "on": 0, "len": 0, "pdu": ""}
{"ev": "rx", "running": 0, "channels": [37], "phy": "ble1m", "aa": "8e89bed6", "crc_init": "555555", "dwell_ms": 0, "bad_crc": 0, "packets": 1882, "crc_bad": 455, "dropped": 0, "hops": 0}
{"ev": "mode", "tx": 0, "rx": 0, "boot": "off", "adv_delay": 1, "fw": "ble_trx", "ver": 3, "out_dropped": 0}
$ python ble_tx\ble_tx.py --port COM7 --start -q

$ python ble_tx\ble_tx.py --port COM7 --stop -q

$ python ble_tx\ble_tx.py --port COM7 --defaults --boot rx --save --reboot -q

$ python ble_tx\ble_tx.py --port COM7 --info -q
{"ev": "info", "running": 0, "addr": "DE:AD:BE:EF:12:34", "random": 1, "phy": "ble1m", "power": 0, "pdu": 0, "adlen": 11, "counter": 1}
{"ev": "twin", "on": 0, "same_addr": 0, "pct": 50, "addr": "XX:XX:XX:XX:XX:02", "phy": "nrf1m", "power": 0}
{"ev": "chan", "n": 3, "first": 37, "aa": 2391391958, "crc_init": 5592405, "interval_ms": 100, "gap_us": 1500, "crc_err_pct": 0, "target": "XX:XX:XX:XX:XX:00"}
{"ev": "stats", "events": 0, "packets": 0, "twin_events": 0, "crc_errors": 0}
{"ev": "raw", "on": 0, "len": 0, "pdu": ""}
{"ev": "rx", "running": 1, "channels": [37], "phy": "ble1m", "aa": "8e89bed6", "crc_init": "555555", "dwell_ms": 0, "bad_crc": 0, "packets": 355, "crc_bad": 55, "dropped": 0, "hops": 0}
{"ev": "mode", "tx": 0, "rx": 1, "boot": "rx", "adv_delay": 1, "fw": "ble_trx", "ver": 3, "out_dropped": 0}
$ python ble_tx\ble_tx.py --port COM7 --stop --erase --defaults -q

```
