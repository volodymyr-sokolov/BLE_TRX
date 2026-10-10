#!/usr/bin/env python3
"""ble_rx -- sniff BLE packets with one or several nRF52840s (ble_trx firmware).

Each device is configured independently (channel or hop list, access address,
CRC init, PHY), all of them receive at the same time, and every packet record
says which device received it.  Output goes to the screen and/or to files
(.jsonl, .csv, .txt, .pcapng -- several at once).

Device spec:  PORT[,key=value...]   keys: label ch aa crcinit phy crc dwell
  ch=37            one channel          ch=37/38/39 + dwell=100   hop list
  ch=37/5:0x9E8B4B27:0x123456   a hop list where channel 5 has its own AA/CRC init
  aa=0x9E8B4B27    access address       crcinit=0x123456
  phy=ble1m|nrf1m  crc=ok|all           label=A (default: the port name)
Options given outside --dev (--ch, --aa, ...) are defaults for every device.

Examples (see ble_rx/README.md for more):
  python ble_rx/ble_rx.py --dev COM7                                  # ch 37, screen
  python ble_rx/ble_rx.py --dev COM7,ch=37,label=A --dev COM5,ch=38,label=B -o cap.jsonl
  python ble_rx/ble_rx.py --dev COM5,ch=5,aa=0x9E8B4B27,crcinit=0x123456 --duration 10
  python ble_rx/ble_rx.py --dev auto --ch 37 38 39 --dwell 200 -o cap.pcapng --quiet
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blelib.args import access_address, crc_init                          # noqa: E402
from blelib.crc import ADV_ACCESS_ADDRESS, ADV_CRC_INIT, register_to_air   # noqa: E402
from blelib.device import Device, DeviceError, find_ports                 # noqa: E402
from blelib.hexutil import spaced                                         # noqa: E402
from blelib.output import open_writer, text_line                          # noqa: E402
from blelib.pdu import decode                                             # noqa: E402


def channel_mhz(ch: int) -> int:
    if ch == 37:
        return 2402
    if ch == 38:
        return 2426
    if ch == 39:
        return 2480
    return 2404 + 2 * ch if ch <= 10 else 2428 + 2 * (ch - 11)


@dataclass
class DevSpec:
    port: str
    label: str = ""
    channels: list[str] = field(default_factory=list)   # "37" or "5:0xAA..:0xCRC"
    aa: int | None = None
    crcinit: int | None = None
    phy: str | None = None
    crc: str | None = None
    dwell: int | None = None


def check_channel(tok: str) -> str:
    """Validate "CH" or "CH:AA" or "CH:AA:CRCINIT"; return it normalised."""
    parts = str(tok).split(":")
    try:
        ch = int(parts[0])
    except ValueError:
        raise argparse.ArgumentTypeError(f"bad channel {tok!r}") from None
    if not 0 <= ch <= 39 or len(parts) > 3:
        raise argparse.ArgumentTypeError(f"channel {tok!r}: expected 0-39[:aa[:crcinit]]")
    if len(parts) == 1:
        return str(ch)
    try:
        aa = access_address(parts[1])
        crc = crc_init(parts[2]) if len(parts) > 2 else None
    except argparse.ArgumentTypeError as e:
        raise argparse.ArgumentTypeError(f"channel {tok!r}: {e}") from None
    out = f"{ch}:0x{aa:08X}"
    return out + (f":0x{crc:06X}" if crc is not None else "")


def parse_spec(text: str) -> DevSpec:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise argparse.ArgumentTypeError("empty device spec")
    s = DevSpec(port=parts[0])
    for kv in parts[1:]:
        k, sep, v = kv.partition("=")
        k = k.strip().lower()
        if not sep:
            raise argparse.ArgumentTypeError(f"{kv!r}: expected key=value")
        try:
            if k == "label":
                s.label = v
            elif k in ("ch", "channel", "channels"):
                s.channels = [c for c in v.replace("+", "/").replace(" ", "/").split("/") if c]
            elif k == "aa":
                s.aa = access_address(v)
            elif k == "crcinit":
                s.crcinit = crc_init(v)
            elif k == "phy":
                if v not in ("ble1m", "nrf1m"):
                    raise argparse.ArgumentTypeError("phy must be ble1m or nrf1m")
                s.phy = v
            elif k == "crc":
                if v not in ("ok", "all"):
                    raise argparse.ArgumentTypeError("crc must be ok or all")
                s.crc = v
            elif k == "dwell":
                s.dwell = int(v)
            else:
                raise argparse.ArgumentTypeError(f"unknown key {k!r}")
        except (ValueError, argparse.ArgumentTypeError) as e:
            raise argparse.ArgumentTypeError(f"{kv!r}: {e}") from None
    s.channels = [check_channel(c) for c in s.channels]
    if len(s.channels) > 8:
        raise argparse.ArgumentTypeError("at most 8 channels per device")
    return s


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="ble_rx.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dev", "-d", action="append", default=[], metavar="SPEC",
                   help="a receiving device (repeatable); 'auto' = every ble_trx device")
    p.add_argument("--list", action="store_true", help="list connected ble_trx devices and exit")

    g = p.add_argument_group("receiver defaults (overridden per device by --dev keys)")
    g.add_argument("--ch", nargs="+", type=check_channel, default=["37"], metavar="CH",
                   help="channel(s); several = hop list (default 37); CH:AA[:CRCINIT] "
                        "gives one channel its own access address")
    g.add_argument("--dwell", type=int, default=100, metavar="MS", help="ms per channel when hopping")
    g.add_argument("--aa", type=access_address, default=ADV_ACCESS_ADDRESS, metavar="0xNNNNNNNN",
                   help="32-bit access address (default 0x8E89BED6, advertising)")
    g.add_argument("--crcinit", type=crc_init, default=ADV_CRC_INIT, metavar="0xNNNNNN",
                   help="24-bit CRC init (default 0x555555)")
    g.add_argument("--phy", choices=("ble1m", "nrf1m"), default="ble1m")
    g.add_argument("--crc", choices=("ok", "all"), default="ok",
                   help="report good-CRC packets only (default) or all")

    g = p.add_argument_group("filters (applied on the host)")
    g.add_argument("--adva", action="append", default=[], metavar="ADDR",
                   help="only packets with this AdvA (repeatable)")
    g.add_argument("--type", action="append", default=[], metavar="PDU",
                   help="only this PDU type, e.g. ADV_IND, SCAN_RSP, LL_CONTROL (repeatable)")
    g.add_argument("--min-rssi", type=int, metavar="DBM", help="drop weaker packets")
    g.add_argument("--contains", metavar="HEX", help="only PDUs containing these bytes")

    g = p.add_argument_group("output")
    g.add_argument("--out", "-o", action="append", default=[], metavar="FILE",
                   help="write to FILE (.jsonl .csv .txt .pcapng); repeatable")
    g.add_argument("--format", choices=("text", "json", "none"), default="text",
                   help="screen format (default text)")
    g.add_argument("--quiet", "-q", action="store_true", help="same as --format none")
    g.add_argument("--duration", type=float, metavar="S", help="stop after S seconds")
    g.add_argument("--count", type=int, metavar="N", help="stop after N packets (after filters)")
    g.add_argument("--no-stats", action="store_true", help="skip the per-device summary at the end")
    return p


class Receiver:
    def __init__(self, spec: DevSpec, a: argparse.Namespace):
        self.spec = spec
        self.dev = Device(spec.port, spec.label or spec.port)
        self.label = self.dev.label
        self.channels = spec.channels or a.ch
        self.aa = spec.aa if spec.aa is not None else a.aa
        self.crcinit = spec.crcinit if spec.crcinit is not None else a.crcinit
        self.phy = spec.phy or a.phy
        self.crc = spec.crc or a.crc
        self.dwell = spec.dwell if spec.dwell is not None else (a.dwell if len(self.channels) > 1 else 0)
        self.seen = 0
        self.last_n = None
        self.gaps = 0
        # host clock - device clock; the smallest value seen is the one with
        # the least USB / OS latency, so it is the best estimate.
        self.offset: float | None = None

    def observe(self, dev_t: float, read_t: float) -> None:
        off = read_t - dev_t
        if self.offset is None or off < self.offset:
            self.offset = off

    def host_time(self, dev_t: float, read_t: float) -> float:
        """Per-packet host time from the device's microsecond timestamp.

        Lines arrive in USB bursts, so the time a line was read is shared by
        many packets; the device clock is exact, so map it onto the host clock
        (call observe() for a whole batch first).
        """
        self.observe(dev_t, read_t)
        return dev_t + self.offset

    def configure(self) -> None:
        d = self.dev
        d.info()                                    # also checks the firmware
        d.command("stop")
        d.command("rx off")
        d.command("rx ch " + " ".join(map(str, self.channels)))
        d.command(f"rx dwell {self.dwell}")
        d.command(f"rx aa 0x{self.aa:08X}")
        d.command(f"rx crcinit 0x{self.crcinit:06X}")
        d.command(f"rx phy {self.phy}")
        d.command(f"rx crc {self.crc}")

    def describe(self) -> str:
        hop = f" hop {self.dwell} ms" if len(self.channels) > 1 else ""
        return (f"[{self.label}] {self.dev.port} serial {self.dev.serial_number}: ch "
                f"{'/'.join(self.channels)}{hop}, AA 0x{self.aa:08X}, "
                f"CRC init 0x{self.crcinit:06X}, {self.phy}, CRC {self.crc}")


def make_record(rx: Receiver, e: dict) -> dict:
    pdu = bytes.fromhex(e["pdu"])
    aa = int(e["aa"], 16)
    d = decode(pdu, aa)
    crc_air = register_to_air(int(e["crc"], 16)).hex()
    return {
        "host_time": rx.host_time(e["t"], e["_host_time"]),
        "device": rx.label,
        "port": rx.dev.port,
        "serial": rx.dev.serial_number,
        "n": e["n"],
        "dev_time": e["t"],
        "ch": e["ch"],
        "freq_mhz": channel_mhz(e["ch"]),
        "rssi": e["rssi"],
        "crc_ok": bool(e["crc_ok"]),
        "aa": e["aa"],
        "type": d.get("type") or d.get("llid") or "",
        "adva": d.get("adva", ""),
        "name": d.get("name", ""),
        "summary": d.get("summary", ""),
        "pdu": e["pdu"],
        "crc": crc_air,
        "raw": e["pdu"] + crc_air,
        "pdu_spaced": spaced(pdu),
        "decoded": d,
    }


def keep(r: dict, a: argparse.Namespace) -> bool:
    if a.adva and r["adva"].upper() not in {x.upper() for x in a.adva}:
        return False
    if a.type and r["type"].upper() not in {x.upper() for x in a.type}:
        return False
    if a.min_rssi is not None and r["rssi"] < a.min_rssi:
        return False
    if a.contains and bytes.fromhex(a.contains) not in bytes.fromhex(r["pdu"]):
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    if a.quiet:
        a.format = "none"
    if a.contains:
        from blelib.hexutil import normalize_hex
        a.contains = normalize_hex(a.contains)

    if a.list or not a.dev:
        ports = find_ports()
        for pi in ports:
            print(f"{pi.device:8} serial {pi.serial_number}  {pi.description}")
        if not ports:
            print("no ble_trx devices (USB 1915:520F) found")
        if a.list:
            return 0
        print("give at least one --dev", file=sys.stderr)
        return 2

    specs: list[DevSpec] = []
    for t in a.dev:
        try:
            if t.split(",")[0].lower() == "auto":
                extra = t.partition(",")[2]
                specs += [parse_spec(pi.device + ("," + extra if extra else ""))
                          for pi in find_ports()]
            else:
                specs.append(parse_spec(t))
        except argparse.ArgumentTypeError as e:
            print(f"error: --dev {t}: {e}", file=sys.stderr)
            return 2
    if not specs:
        print("error: no devices", file=sys.stderr)
        return 2
    labels = [s.label or s.port for s in specs]
    if len(set(labels)) != len(labels):
        print("error: two devices share a label/port", file=sys.stderr)
        return 2

    try:
        writers = [open_writer(path) for path in a.out]
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rxs: list[Receiver] = []
    try:
        for s in specs:
            rxs.append(Receiver(s, a))
        for r in rxs:
            r.configure()
            for w in writers:
                w.add_device(r.label, r.dev.port, r.dev.serial_number)
            print(r.describe(), file=sys.stderr)
        # Start them back to back so they listen over the same interval.
        for r in rxs:
            r.dev.drain_events()
            r.dev.command("rx on")
    except DeviceError as e:
        print(f"error: {e}", file=sys.stderr)
        for r in rxs:
            r.dev.close()
        return 1

    print(f"receiving on {len(rxs)} device(s); Ctrl-C to stop", file=sys.stderr)
    t0 = time.time()
    total = 0
    try:
        while True:
            if a.duration and time.time() - t0 >= a.duration:
                break
            batch = []
            for r in rxs:
                events = r.dev.drain_events()
                for e in events:
                    if e.get("ev") == "pkt":
                        r.observe(e["t"], e["_host_time"])
                for e in events:
                    if e.get("ev") == "pkt":
                        if r.last_n is not None and e["n"] != r.last_n + 1:
                            r.gaps += 1
                        r.last_n = e["n"]
                        batch.append(make_record(r, e))
                    elif e.get("ev") == "fatal":
                        print(f"[{r.label}] firmware fault: {e}", file=sys.stderr)
            batch.sort(key=lambda x: x["host_time"])
            for rec in batch:
                if not keep(rec, a):
                    continue
                for r in rxs:
                    if r.label == rec["device"]:
                        r.seen += 1
                for w in writers:
                    w.write(rec)
                try:
                    if a.format == "text":
                        print(text_line(rec), flush=True)
                    elif a.format == "json":
                        print(json.dumps({k: v for k, v in rec.items()
                                          if k not in ("pdu_spaced", "decoded")}), flush=True)
                except OSError:            # stdout closed (e.g. piped into `head`)
                    a.format = "none"
                    raise KeyboardInterrupt from None
                total += 1
                if a.count and total >= a.count:
                    raise KeyboardInterrupt
            if not batch:
                time.sleep(0.02)
    except KeyboardInterrupt:
        pass
    finally:
        elapsed = time.time() - t0
        stats = {}
        for r in rxs:
            try:
                r.dev.command("rx off")
                for e in r.dev.query("rx info"):
                    if e.get("ev") == "rx":
                        stats[r.label] = e
            except DeviceError:
                pass
            r.dev.close()
        for w in writers:
            w.close()
        if not a.no_stats:
            print(f"--- {total} packets shown/saved in {elapsed:.1f} s", file=sys.stderr)
            for r in rxs:
                s = stats.get(r.label, {})
                print(f"[{r.label}] shown {r.seen}; firmware: received {s.get('packets', '?')}, "
                      f"CRC failures {s.get('crc_bad', '?')}, dropped (USB too slow) "
                      f"{s.get('dropped', '?')}, hops {s.get('hops', '?')}; host gaps {r.gaps}",
                      file=sys.stderr)
            for path in a.out:
                print(f"wrote {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
