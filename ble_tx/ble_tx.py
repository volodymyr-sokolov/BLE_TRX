#!/usr/bin/env python3
"""ble_tx -- BLE packet generator on an nRF52840 running the ble_trx firmware.

Every firmware TX setting has an option; options are applied in a fixed,
sensible order (defaults -> identity -> PDU -> radio -> timing -> twin -> raw
-> persistence -> start/stop), then the tool optionally waits, prints status,
or drops into an interactive shell.

Examples (see ble_tx/README.md for many more):

  python ble_tx/ble_tx.py --port COM7 --name blesim --start
  python ble_tx/ble_tx.py --port COM7 --raw " 42 0a 66 55 44 33 22 11 03 09 41 42" --channels 37 --duration 5
  python ble_tx/ble_tx.py --port COM7 --raw-from capture.jsonl --pick-adva C0:FF:EE:00:00:01 --start
  python ble_tx/ble_tx.py --port COM7 --info
  python ble_tx/ble_tx.py --port COM7 --shell
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blelib.args import access_address, crc_init     # noqa: E402
from blelib.crc import ADV_CRC_INIT                  # noqa: E402
from blelib.device import Device, DeviceError, find_ports   # noqa: E402
from blelib.hexutil import HexError, normalize_hex, spaced  # noqa: E402
from blelib.pdu import decode                        # noqa: E402
from blelib.raw import LENGTH_MODES, prepare_raw     # noqa: E402

POWERS = (8, 7, 6, 5, 4, 3, 2, 0, -4, -8, -12, -16, -20, -40)
PDUS = ("adv_ind", "adv_direct_ind", "adv_nonconn_ind", "adv_scan_ind")


def on_off(v: str) -> str:
    v = v.lower()
    if v not in ("on", "off"):
        raise argparse.ArgumentTypeError("on or off")
    return v




def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ble_tx.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    g = p.add_argument_group("device")
    g.add_argument("--port", help="serial port (COM7, /dev/ttyACM0); 'auto' = the only ble_trx device")
    g.add_argument("--list", action="store_true", help="list connected ble_trx devices and exit")
    g.add_argument("--quiet", "-q", action="store_true", help="print only errors and requested output")

    g = p.add_argument_group("configuration reset")
    g.add_argument("--defaults", action="store_true",
                   help="reset the whole configuration to firmware defaults first")
    g.add_argument("--load", action="store_true", help="load the configuration saved in flash first")

    g = p.add_argument_group("advertiser identity and PDU")
    g.add_argument("--addr", metavar="AA:BB:CC:DD:EE:FF", help="advertiser address (AdvA)")
    g.add_argument("--type", choices=("public", "random"), help="address type (TxAdd bit)")
    g.add_argument("--pdu", choices=PDUS, help="advertising PDU type")
    g.add_argument("--direct", metavar="AA:BB:CC:DD:EE:FF", help="TargetA for adv_direct_ind")
    g.add_argument("--data", metavar="HEX", help="AD structures (<= 31 bytes); spaces allowed, e.g. '02 01 06'")
    g.add_argument("--name", help="shortcut: Flags + Complete Local Name AD (<= 26 chars)")
    g.add_argument("--counter", type=on_off, metavar="on|off",
                   help="append a 16-bit event counter AD (len 3, type 0xFF) so packets differ")

    g = p.add_argument_group("radio")
    g.add_argument("--channels", nargs="+", type=int, metavar="CH",
                   help="channel indices 0-39 sent in each event (37 38 39 = advertising)")
    g.add_argument("--power", type=int, choices=POWERS, metavar="DBM", help=f"TX power, one of {POWERS}")
    g.add_argument("--phy", choices=("ble1m", "nrf1m"), help="modulation (nrf1m: h~0.34 instead of 0.5)")
    g.add_argument("--aa", type=access_address, metavar="0xNNNNNNNN",
                   help="32-bit access address (default 0x8E89BED6)")
    g.add_argument("--crcinit", type=crc_init, metavar="0xNNNNNN", help="24-bit CRC init (default 0x555555)")
    g.add_argument("--crcerr", type=int, metavar="PCT", help="percent of events sent with a bad CRC (all channels of the event)")

    g = p.add_argument_group("timing")
    g.add_argument("--interval", type=int, metavar="MS", help="advertising interval")
    g.add_argument("--gap", type=int, metavar="US", help="gap between channels within one event")
    g.add_argument("--adv-delay", type=on_off, metavar="on|off", help="random 0-10 ms advDelay per event")

    g = p.add_argument_group("twin (second transmitter personality)")
    g.add_argument("--twin", type=on_off, metavar="on|off", help="enable the twin")
    g.add_argument("--twin-addr", metavar="ADDR|same", help="twin address, or 'same' to reuse the main one")
    g.add_argument("--twin-phy", choices=("ble1m", "nrf1m"))
    g.add_argument("--twin-power", type=int, choices=POWERS, metavar="DBM")
    g.add_argument("--twin-pct", type=int, metavar="PCT", help="share of events sent by the twin")

    g = p.add_argument_group("raw packet transmission")
    g.add_argument("--raw", metavar="HEX",
                   help="send this PDU (header + payload) verbatim; whitespace is removed "
                        "(' 80 60 64 1c ae 7a' -> 8060641cae7a); a trailing CRC is removed")
    g.add_argument("--raw-from", metavar="FILE",
                   help="take the PDU from a ble_rx capture (.jsonl/.csv) or a text file of hex")
    g.add_argument("--pick", type=int, default=None, metavar="N",
                   help="with --raw-from: use the N-th packet (0-based; default: first match)")
    g.add_argument("--pick-adva", metavar="ADDR", help="with --raw-from: first packet from this AdvA")
    g.add_argument("--raw-length", choices=LENGTH_MODES, default="fix",
                   help="if LENGTH disagrees with the bytes given: fix it (default), keep it, or error")
    g.add_argument("--raw-off", action="store_true", help="go back to the PDU built from addr/pdu/data")
    g.add_argument("--raw-on", action="store_true", help="re-enable the raw PDU already stored")

    g = p.add_argument_group("persistence")
    g.add_argument("--save", action="store_true", help="save the configuration to flash")
    g.add_argument("--erase", action="store_true", help="erase the saved configuration")
    g.add_argument("--boot", choices=("on", "off", "rx"),
                   help="power-up action: on = advertise, rx = sniff, off = idle")

    g = p.add_argument_group("run")
    g.add_argument("--start", action="store_true", help="start advertising (until --stop)")
    g.add_argument("--count", type=int, metavar="N", help="send N events, wait for them, then report")
    g.add_argument("--duration", type=float, metavar="S", help="advertise for S seconds, then stop")
    g.add_argument("--stop", action="store_true", help="stop advertising (and the receiver)")
    g.add_argument("--cmd", action="append", default=[], metavar="LINE",
                   help="send a firmware command verbatim (repeatable), after the options above")
    g.add_argument("--listen", type=float, metavar="S", help="print everything the device says for S seconds")
    g.add_argument("--info", action="store_true", help="print the configuration and counters")
    g.add_argument("--fw-help", action="store_true", help="print the firmware's command list")
    g.add_argument("--shell", action="store_true", help="interactive: type firmware commands")
    g.add_argument("--reboot", action="store_true", help="reboot the board")
    g.add_argument("--dfu", action="store_true", help="reboot the board into its DFU bootloader")
    return p


# --------------------------------------------------------------------------

def pick_from_file(path: str, index: int | None, adva: str | None) -> tuple[str, str]:
    """(pdu hex, description) of one packet from a capture or hex text file."""
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    rows: list[dict] = []
    if path.lower().endswith(".csv"):
        rows = list(csv.DictReader(text.splitlines()))
    else:
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    rows = [r for r in rows if r.get("pdu")]
    if not rows:                                   # plain hex file
        return normalize_hex(text), f"{path} (hex text)"
    if adva:
        want = adva.upper()
        rows = [r for r in rows
                if (r.get("adva") or decode(bytes.fromhex(r["pdu"])).get("adva", "")).upper() == want]
        if not rows:
            raise SystemExit(f"no packet from {adva} in {path}")
    i = index or 0
    if i >= len(rows):
        raise SystemExit(f"{path} has only {len(rows)} matching packets")
    r = rows[i]
    return r["pdu"], f"{path} packet #{i} (device {r.get('device', '?')}, ch {r.get('ch', '?')})"


def apply(dev: Device, a: argparse.Namespace, say) -> None:
    def cmd(line: str) -> None:
        ack = dev.command(line)
        say(f"  {line:<40} -> {ack.get('ok')}")

    if a.defaults:
        cmd("defaults")
    if a.load:
        cmd("load")
    if a.addr:
        cmd(f"addr {a.addr}")
    if a.type:
        cmd(f"type {a.type}")
    if a.pdu:
        cmd(f"pdu {a.pdu}")
    if a.direct:
        cmd(f"direct {a.direct}")
    if a.name is not None:
        if " " in a.name or not a.name:
            # The firmware's `name` takes one word; build the same AD here.
            nb = a.name.encode()
            if len(nb) > 26:
                raise HexError("name longer than 26 bytes")
            cmd("data 020106" + bytes((len(nb) + 1, 0x09)).hex() + nb.hex())
        else:
            cmd(f"name {a.name}")
    if a.data is not None:
        cmd(f"data {normalize_hex(a.data)}")
    if a.counter:
        cmd(f"counter {a.counter}")
    if a.channels:
        cmd("channels " + " ".join(str(c) for c in a.channels))
    if a.power is not None:
        cmd(f"power {a.power}")
    if a.phy:
        cmd(f"phy {a.phy}")
    if a.aa is not None:
        cmd(f"aa 0x{a.aa:08X}")
    if a.crcinit is not None:
        cmd(f"crcinit 0x{a.crcinit:06X}")
    if a.crcerr is not None:
        cmd(f"crcerr {a.crcerr}")
    if a.interval is not None:
        cmd(f"interval {a.interval}")
    if a.gap is not None:
        cmd(f"gap {a.gap}")
    if a.adv_delay:
        cmd(f"advdelay {a.adv_delay}")
    if a.twin:
        cmd(f"twin {a.twin}")
    if a.twin_addr:
        cmd("twin same" if a.twin_addr.lower() == "same" else f"twin addr {a.twin_addr}")
    if a.twin_phy:
        cmd(f"twin phy {a.twin_phy}")
    if a.twin_power is not None:
        cmd(f"twin power {a.twin_power}")
    if a.twin_pct is not None:
        cmd(f"twin pct {a.twin_pct}")

    raw_text = a.raw
    if a.raw_from:
        raw_text, where = pick_from_file(a.raw_from, a.pick, a.pick_adva)
        say(f"  raw PDU from {where}")
    if raw_text is not None:
        crc_init = a.crcinit if a.crcinit is not None else ADV_CRC_INIT
        rp = prepare_raw(raw_text, crc_init, a.raw_length)
        for n in rp.notes:
            say(f"  note: {n}")
        ack = dev.command(f"raw {rp.hex}")
        say(f"  raw {spaced(rp.pdu):<36} -> {ack.get('len')} bytes, on air "
            f"{ack.get('on_air')} ({decode(rp.pdu, a.aa or 0x8E89BED6).get('summary')})")
    if a.raw_off:
        cmd("raw off")
    if a.raw_on:
        cmd("raw on")

    if a.erase:
        cmd("erase")
    if a.boot:
        cmd(f"boot {a.boot}")
    if a.save:
        cmd("save")
    for line in a.cmd:
        # Commands answer with {"ok"/"err"}, queries (info, rx info, raw,
        # help) with events or text: print whatever comes back.
        for reply in dev.exchange(line):
            print(f"  {line:<40} -> {reply}")


def print_info(dev: Device) -> None:
    for e in dev.query("info"):
        e.pop("_host_time", None)
        print(json.dumps(e))


def wait_done(dev: Device, timeout: float) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        for e in dev.drain_events():
            if e.get("ev") == "done":
                return True
        time.sleep(0.05)
    return False


STAT_KEYS = ("events", "packets", "twin_events", "crc_errors")


def read_stats(dev: Device) -> dict:
    for e in dev.query("info"):
        if e.get("ev") == "stats":
            return {k: e[k] for k in STAT_KEYS}
    return {}


def stats_line(dev: Device, before: dict) -> str:
    """This run's counters (the firmware's are cumulative since power-up)."""
    now = read_stats(dev)
    if not now:
        return "(no stats)"
    d = {k: now[k] - before.get(k, 0) for k in STAT_KEYS}
    return (f"events {d['events']}, packets {d['packets']}, twin events "
            f"{d['twin_events']}, CRC-corrupted {d['crc_errors']}")


def shell(dev: Device) -> None:
    print("ble_tx shell: firmware commands, 'help' for the list, Ctrl-C / 'quit' to leave")
    stop = threading.Event()

    def pump():
        while not stop.is_set():
            for e in dev.drain_events():
                e.pop("_host_time", None)
                print(json.dumps(e))
            while dev.text:
                print(dev.text.pop(0))
            while not dev._acks.empty():
                a = dev._acks.get_nowait()
                a.pop("_host_time", None)
                print(json.dumps(a))
            time.sleep(0.05)

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    try:
        while True:
            line = input()
            if line.strip() in ("quit", "exit"):
                break
            if line.strip().startswith(("raw ", "data ")):
                verb, _, rest = line.strip().partition(" ")
                try:
                    line = f"{verb} {normalize_hex(rest)}" if rest not in ("on", "off") else line
                except HexError as e:
                    print(f"hex error: {e}")
                    continue
            dev.send(line)
    except (EOFError, KeyboardInterrupt):
        pass
    time.sleep(0.6)            # let the replies to the last commands print
    stop.set()
    t.join(timeout=1)


def main(argv: list[str] | None = None) -> int:
    a = build_parser().parse_args(argv)
    say = (lambda *_: None) if a.quiet else print

    if a.list or not a.port:
        ports = find_ports()
        if not ports:
            print("no ble_trx devices (USB 1915:520F) found")
        for pi in ports:
            print(f"{pi.device:8} serial {pi.serial_number}  {pi.description}")
        if a.list:
            return 0
        if not a.port:
            print("give --port", file=sys.stderr)
            return 2
    if a.port == "auto":
        ports = find_ports()
        if len(ports) != 1:
            print(f"--port auto needs exactly one ble_trx device, found {len(ports)}", file=sys.stderr)
            return 2
        a.port = ports[0].device

    try:
        with Device(a.port) as dev:
            info = dev.info()
            say(f"{a.port}: ble_trx v{info['mode']['ver']} (serial {dev.serial_number}), "
                f"TX {'running' if info['mode']['tx'] else 'idle'}, "
                f"RX {'running' if info['mode']['rx'] else 'idle'}")
            apply(dev, a, say)

            if a.fw_help:
                print("\n".join(dev.help_text()))
            before = read_stats(dev) if (a.count or a.start or a.duration) else {}
            if a.count:
                dev.drain_events()
                dev.command(f"start {a.count}")
                say(f"  sending {a.count} events ...")
                # interval + max advDelay + per-channel airtime, generously
                if not wait_done(dev, 5 + a.count * 0.3):
                    say("  (no 'done' event before the timeout)")
                say("  " + stats_line(dev, before))
            elif a.start or a.duration:
                dev.command("start")
                say("  advertising")
                if a.duration:
                    time.sleep(a.duration)
                    dev.command("stop")
                    say(f"  stopped after {a.duration:g} s: " + stats_line(dev, before))
            if a.stop:
                dev.command("stop")
                dev.command("rx off")
                say("  stopped")
            if a.listen:
                end = time.time() + a.listen
                while time.time() < end:
                    for e in dev.drain_events():
                        e.pop("_host_time", None)
                        print(json.dumps(e))
                    time.sleep(0.05)
            if a.info:
                print_info(dev)
            if a.shell:
                shell(dev)
            if a.reboot:
                dev.send("reboot")
                say("  rebooting")
            if a.dfu:
                dev.send("dfu")
                say("  rebooting into the DFU bootloader")
    except (DeviceError, HexError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
