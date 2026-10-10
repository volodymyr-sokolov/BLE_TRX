"""Packet record formats for ble_rx: screen text, JSONL, CSV and pcapng.

Every record names the device that received it (``device`` = its label,
plus ``port`` and USB ``serial``).  In pcapng each device is its own
interface (if_name = label), so Wireshark's "Interface" column/field
(``frame.interface_name``) tells the receivers apart.
"""

from __future__ import annotations

import csv
import datetime as _dt
import json
import struct
from typing import IO

from .crc import ADV_ACCESS_ADDRESS

CSV_FIELDS = ["host_time", "device", "port", "serial", "n", "dev_time", "ch", "freq_mhz",
              "rssi", "crc_ok", "aa", "type", "adva", "name", "summary", "pdu", "crc", "raw"]


def iso(t: float) -> str:
    return _dt.datetime.fromtimestamp(t).isoformat(timespec="microseconds")


def text_line(r: dict) -> str:
    crc = "OK " if r["crc_ok"] else "BAD"
    return (f"{iso(r['host_time'])[11:]} [{r['device']}] ch{r['ch']:>2} {r['rssi']:>4} dBm "
            f"CRC {crc} {r['summary']}\n      PDU {r['pdu_spaced']}  CRC {r['crc']}")


class Writer:
    """Base: one output file."""

    def __init__(self, path: str):
        self.path = path

    def add_device(self, label: str, port: str, serial: str) -> None:
        pass

    def write(self, r: dict) -> None:
        raise NotImplementedError

    def close(self) -> None:
        pass


class JsonlWriter(Writer):
    def __init__(self, path: str):
        super().__init__(path)
        self.f: IO = open(path, "w", encoding="utf-8", newline="\n")

    def write(self, r: dict) -> None:
        out = {k: v for k, v in r.items() if k not in ("pdu_spaced", "decoded")}
        out["host_iso"] = iso(r["host_time"])
        self.f.write(json.dumps(out) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


class CsvWriter(Writer):
    def __init__(self, path: str):
        super().__init__(path)
        self.f: IO = open(path, "w", encoding="utf-8", newline="")
        self.w = csv.DictWriter(self.f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        self.w.writeheader()

    def write(self, r: dict) -> None:
        row = dict(r)
        row["host_time"] = iso(r["host_time"])
        self.w.writerow(row)
        self.f.flush()

    def close(self) -> None:
        self.f.close()


class TextWriter(Writer):
    def __init__(self, path: str):
        super().__init__(path)
        self.f: IO = open(path, "w", encoding="utf-8", newline="\n")

    def write(self, r: dict) -> None:
        self.f.write(text_line(r) + "\n")
        self.f.flush()

    def close(self) -> None:
        self.f.close()


# ---- pcapng ---------------------------------------------------------------

LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR = 256

# LE packet pseudo-header flags (wireshark: btle_rf)
F_DEWHITENED = 0x0001
F_SIGNAL_VALID = 0x0002
F_REF_AA_VALID = 0x0010
F_CRC_CHECKED = 0x0400
F_CRC_VALID = 0x0800


def _opt(code: int, value: bytes) -> bytes:
    pad = (-len(value)) % 4
    return struct.pack("<HH", code, len(value)) + value + b"\0" * pad


def _block(btype: int, body: bytes) -> bytes:
    n = 12 + len(body)
    return struct.pack("<II", btype, n) + body + struct.pack("<I", n)


class PcapngWriter(Writer):
    def __init__(self, path: str):
        super().__init__(path)
        self.f: IO = open(path, "wb")
        shb = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1) + \
            _opt(4, b"ble_rx (nRF52840 ble_trx)") + _opt(0, b"")
        self.f.write(_block(0x0A0D0D0A, shb))
        self.ifaces: dict[str, int] = {}

    def add_device(self, label: str, port: str, serial: str) -> None:
        if label in self.ifaces:
            return
        body = struct.pack("<HHI", LINKTYPE_BLUETOOTH_LE_LL_WITH_PHDR, 0, 0)
        body += _opt(2, label.encode())                          # if_name
        body += _opt(3, f"{port} serial {serial}".encode())      # if_description
        body += _opt(9, bytes([6]))                               # if_tsresol: microseconds
        body += _opt(0, b"")
        self.f.write(_block(0x00000001, body))
        self.ifaces[label] = len(self.ifaces)

    def write(self, r: dict) -> None:
        iface = self.ifaces[r["device"]]
        aa = int(r["aa"], 16)
        rf_index = (r["freq_mhz"] - 2402) // 2           # Wireshark's "RF channel"
        flags = F_DEWHITENED | F_SIGNAL_VALID | F_REF_AA_VALID | F_CRC_CHECKED
        if r["crc_ok"]:
            flags |= F_CRC_VALID
        phdr = struct.pack("<BbbBIH", rf_index, r["rssi"], 0, 0, aa, flags)
        frame = phdr + struct.pack("<I", aa) + bytes.fromhex(r["pdu"]) + bytes.fromhex(r["crc"])
        ts = int(round(r["host_time"] * 1_000_000))
        body = struct.pack("<IIIII", iface, ts >> 32, ts & 0xFFFFFFFF, len(frame), len(frame))
        body += frame + b"\0" * ((-len(frame)) % 4)
        body += _opt(1, f"device {r['device']} n={r['n']} dev_time={r['dev_time']}".encode())
        body += _opt(0, b"")
        self.f.write(_block(0x00000006, body))
        self.f.flush()

    def close(self) -> None:
        self.f.close()


def open_writer(path: str) -> Writer:
    low = path.lower()
    if low.endswith((".jsonl", ".json", ".ndjson")):
        return JsonlWriter(path)
    if low.endswith(".csv"):
        return CsvWriter(path)
    if low.endswith(".pcapng"):
        return PcapngWriter(path)
    if low.endswith((".txt", ".log")):
        return TextWriter(path)
    raise ValueError(f"{path}: unknown output format (use .jsonl, .csv, .txt/.log or .pcapng)")


def read_pcapng(path: str) -> list[tuple[str, bytes]]:
    """Minimal reader for tests: [(interface name, frame bytes)]."""
    data = open(path, "rb").read()
    names: list[str] = []
    out = []
    i = 0
    while i < len(data):
        btype, blen = struct.unpack_from("<II", data, i)
        body = data[i + 8:i + blen - 4]
        if btype == 1:
            j = 8
            name = ""
            while j + 4 <= len(body):
                code, ln = struct.unpack_from("<HH", body, j)
                if code == 0:
                    break
                if code == 2:
                    name = body[j + 4:j + 4 + ln].decode()
                j += 4 + ln + ((-ln) % 4)
            names.append(name)
        elif btype == 6:
            iface, _, _, cap, _ = struct.unpack_from("<IIIII", body, 0)
            out.append((names[iface], body[20:20 + cap]))
        i += blen
    return out


ADV_AA_HEX = f"{ADV_ACCESS_ADDRESS:08x}"
