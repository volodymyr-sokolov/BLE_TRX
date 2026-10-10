"""ble_tx / ble_rx host logic, without hardware (a fake device records commands)."""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ble_tx = _load("ble_tx")
ble_rx = _load("ble_rx")
EXT = "0222c16f7aae1c641bff75004204018060641cae7a6fc1661cae7a6fc0014f5300000000"


class FakeDevice:
    def __init__(self):
        self.sent = []

    def command(self, line, timeout=2.0, check=True):
        self.sent.append(line)
        if line.startswith("raw ") and line not in ("raw on", "raw off"):
            n = len(bytes.fromhex(line[4:]))
            return {"ok": "raw", "len": n, "on_air": n}
        return {"ok": line.split()[0]}

    def exchange(self, line):
        self.sent.append(line)
        return ["{}"]


def run_apply(argv):
    a = ble_tx.build_parser().parse_args(["--port", "X"] + argv)
    dev = FakeDevice()
    ble_tx.apply(dev, a, lambda *_: None)
    return dev.sent


def test_every_tx_option_maps_to_a_firmware_command():
    sent = run_apply([
        "--defaults", "--load", "--addr", "AA:BB:CC:DD:EE:FF", "--type", "public",
        "--pdu", "adv_scan_ind", "--direct", "11:22:33:44:55:66", "--name", "abc",
        "--data", "02 01 06", "--counter", "off", "--channels", "37", "5",
        "--power", "-4", "--phy", "nrf1m", "--aa", "0x9E8B4B27", "--crcinit", "0x123456",
        "--crcerr", "10", "--interval", "50", "--gap", "800", "--adv-delay", "off",
        "--twin", "on", "--twin-addr", "same", "--twin-phy", "ble1m", "--twin-power", "8",
        "--twin-pct", "30", "--raw", "02 01 00", "--erase", "--boot", "rx", "--save",
        "--cmd", "info"])
    assert sent == [
        "defaults", "load", "addr AA:BB:CC:DD:EE:FF", "type public", "pdu adv_scan_ind",
        "direct 11:22:33:44:55:66", "name abc", "data 020106", "counter off", "channels 37 5",
        "power -4", "phy nrf1m", "aa 0x9E8B4B27", "crcinit 0x123456", "crcerr 10",
        "interval 50", "gap 800", "advdelay off", "twin on", "twin same", "twin phy ble1m",
        "twin power 8", "twin pct 30", "raw 020100", "erase", "boot rx", "save", "info"]


def test_raw_whitespace_removed_before_sending():
    assert run_apply(["--raw", " 80 60 64 1c ae 7a", "--raw-length", "keep"]) == ["raw 8060641cae7a"]
    assert run_apply(["--raw", " 80 60 64 1c ae 7a"]) == ["raw 8004641cae7a"]


def test_name_with_spaces_built_on_host():
    assert run_apply(["--name", "my tag"]) == ["data 020106070" + "9" + b"my tag".hex()]


def test_twin_addr_and_raw_toggles():
    assert run_apply(["--twin-addr", "C0:00:00:00:00:09", "--raw-off"]) == \
        ["twin addr C0:00:00:00:00:09", "raw off"]
    assert run_apply(["--raw-on"]) == ["raw on"]


def test_raw_from_jsonl_csv_and_text(tmp_path):
    j = tmp_path / "c.jsonl"
    j.write_text("\n".join(json.dumps({"device": d, "ch": 37, "pdu": p}) for d, p in
                           [("A", "4006aaaaaaaaaaaa"), ("B", EXT)]) + "\n")
    assert ble_tx.pick_from_file(str(j), None, None)[0] == "4006aaaaaaaaaaaa"
    assert ble_tx.pick_from_file(str(j), 1, None)[0] == EXT
    assert ble_tx.pick_from_file(str(j), None, "64:1c:ae:7a:6f:c1")[0] == EXT
    c = tmp_path / "c.csv"
    c.write_text("device,pdu\nA," + EXT + "\n")
    assert ble_tx.pick_from_file(str(c), None, None)[0] == EXT
    t = tmp_path / "p.txt"
    t.write_text(" 02 22 c1 6f\n")
    assert ble_tx.pick_from_file(str(t), None, None)[0] == "0222c16f"
    with pytest.raises(SystemExit):
        ble_tx.pick_from_file(str(j), None, "00:00:00:00:00:00")


def test_rx_device_spec():
    s = ble_rx.parse_spec("COM7,ch=37/38/39,dwell=150,label=A,aa=0x9E8B4B27,crcinit=0x123456,phy=nrf1m,crc=all")
    assert (s.port, s.channels, s.dwell, s.label, s.aa, s.crcinit, s.phy, s.crc) == \
        ("COM7", ["37", "38", "39"], 150, "A", 0x9E8B4B27, 0x123456, "nrf1m", "all")
    s = ble_rx.parse_spec("COM7,ch=37/5:0x9e8b4b27:0x123456/6:0x11223344")
    assert s.channels == ["37", "5:0x9E8B4B27:0x123456", "6:0x11223344"]
    assert ble_rx.parse_spec("/dev/ttyACM0").port == "/dev/ttyACM0"
    for bad in ("COM7,ch=40", "COM7,foo=1", "COM7,ch", "COM7,ch=1/2/3/4/5/6/7/8/9",
                "COM7,ch=5:0", "COM7,ch=5:zz", "COM7,ch=5:1:2:3"):
        with pytest.raises(argparse.ArgumentTypeError):
            ble_rx.parse_spec(bad)


def test_rx_record_names_device_and_converts_crc():
    class R:
        label = "B"

        @staticmethod
        def host_time(dev_t, read_t):
            return read_t

        class dev:
            port = "COM5"
            serial_number = "XYZ"
    e = {"_host_time": 1.0, "n": 7, "t": 2.5, "ch": 38, "rssi": -55, "crc_ok": 1,
         "aa": "8e89bed6", "pdu": EXT, "crc": "a8a9a4"}
    r = ble_rx.make_record(R, e)
    assert r["device"] == "B" and r["port"] == "COM5" and r["serial"] == "XYZ"
    assert r["freq_mhz"] == 2426 and r["adva"] == "64:1C:AE:7A:6F:C1"
    assert r["crc"] == "159525" and r["raw"] == EXT + "159525"


def test_channel_frequencies():
    assert [ble_rx.channel_mhz(c) for c in (37, 0, 10, 11, 36, 38, 39)] == \
        [2402, 2404, 2424, 2428, 2478, 2426, 2480]


def test_access_address_and_crcinit_ranges():
    from blelib.args import access_address, crc_init
    assert access_address("0x8E89BED6") == 0x8E89BED6
    assert crc_init("0x555555") == 0x555555
    with pytest.raises(argparse.ArgumentTypeError, match="48-bit device address"):
        access_address("0xBE0FF3359A4E")          # an AdvA typed where an AA belongs
    for bad in ("0", "0x100000000", "zz"):
        with pytest.raises(argparse.ArgumentTypeError):
            access_address(bad)
    for bad in ("0x1000000", "-1", "x"):
        with pytest.raises(argparse.ArgumentTypeError):
            crc_init(bad)
    for argv in (["--aa", "0xBE0FF3359A4E"], ["--crcinit", "0x1234567"]):
        with pytest.raises(SystemExit):
            ble_tx.build_parser().parse_args(["--port", "X"] + argv)
        with pytest.raises(SystemExit):
            ble_rx.build_parser().parse_args(["--dev", "X"] + argv)
    for spec in ("X,aa=0xBE0FF3359A4E", "X,crcinit=0x1234567", "X,ch=5:0x1FFFFFFFF",
                 "X,phy=2m", "X,crc=bad"):
        with pytest.raises(argparse.ArgumentTypeError):
            ble_rx.parse_spec(spec)
