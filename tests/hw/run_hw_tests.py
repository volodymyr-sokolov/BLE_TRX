#!/usr/bin/env python3
"""Hardware test suite: two nRF52840 boards running ble_trx.

    python tests/hw/run_hw_tests.py --a COM7 --b COM5 [--only T07,T09]

Board A transmits (ble_tx.py) while board B receives (ble_rx.py), then both
receive at once.  Every test runs the real scripts as subprocesses exactly as
a user would, checks the captured packets, and the whole run is written to
docs/TEST_REPORT.md (commands, verdicts, sample output) plus the captures in
docs/test_captures/.
"""

from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from blelib.output import read_pcapng          # noqa: E402
from blelib.pdu import decode                  # noqa: E402

PY = sys.executable
TX = str(ROOT / "ble_tx" / "ble_tx.py")
RX = str(ROOT / "ble_rx" / "ble_rx.py")
CAP = ROOT / "docs" / "test_captures"
OWN = "DE:AD:BE:EF:12:34"


class Ctx:
    def __init__(self, a: str, b: str):
        self.a, self.b = a, b
        self.log: list[str] = []

    def run(self, args: list[str], timeout: float = 60) -> str:
        cmd = [PY] + args
        shown = "python " + " ".join(_q(x) for x in [str(Path(args[0]).relative_to(ROOT))] + args[1:])
        self.log.append(f"$ {shown}")
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        out = (p.stdout + p.stderr).strip()
        self.log.append(_clip(out))
        if p.returncode:
            raise AssertionError(f"exit code {p.returncode}: {out[-400:]}")
        return out

    def rx_bg(self, args: list[str]) -> subprocess.Popen:
        shown = "python " + " ".join(_q(x) for x in [str(Path(args[0]).relative_to(ROOT))] + args[1:])
        self.log.append(f"$ {shown}    # in the background")
        return subprocess.Popen([PY] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, cwd=ROOT)

    def wait(self, p: subprocess.Popen, timeout: float = 60) -> str:
        out, _ = p.communicate(timeout=timeout)
        self.log.append(_clip(out.strip()))
        if p.returncode:
            raise AssertionError(f"ble_rx exit code {p.returncode}")
        return out


def _q(x: str) -> str:
    return f'"{x}"' if (" " in x or not x) else x


def _clip(text: str, n: int = 14) -> str:
    lines = text.splitlines()
    if len(lines) <= n:
        return text
    return "\n".join(lines[:n - 4] + [f"... ({len(lines) - n + 1} lines omitted) ..."] + lines[-3:])


OWN_ADDRS = {OWN, "11:22:33:44:55:66", "AA:BB:CC:00:11:22", "C0:FF:EE:00:00:01"}
_MAC = re.compile(r"\b[0-9A-F]{2}(?::[0-9A-F]{2}){5}\b")


def redact(text: str) -> str:
    """Mask third-party device addresses (and those bytes in hex dumps).

    The tests sniff whatever advertises nearby; the report is meant to be
    published, so other people's device addresses are replaced by xx.
    """
    for mac in set(_MAC.findall(text)) - OWN_ADDRS:
        b = bytes.fromhex(mac.replace(":", ""))[::-1]          # on-air order
        text = text.replace(mac, "XX:XX:XX:XX:XX:" + mac[-2:])
        text = text.replace(" ".join(f"{x:02x}" for x in b), "xx xx xx xx xx xx")
        text = text.replace(b.hex(), "xxxxxxxxxxxx")
    return text


def load(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def txrx(c: Ctx, name: str, tx_args: list[str], rx_spec: str, rx_extra: list[str] = (),
         rx_seconds: float = 8, settle: float = 1.5) -> list[dict]:
    """Start ble_rx on B, run ble_tx on A, return B's packets."""
    out = CAP / f"{name}.jsonl"
    p = c.rx_bg([RX, "--dev", rx_spec, "--duration", str(rx_seconds), "-o", str(out.relative_to(ROOT)),
                 "--quiet", *rx_extra])
    time.sleep(settle)                       # receiver configured and listening
    c.run([TX, "--port", c.a, *tx_args])
    c.wait(p, timeout=rx_seconds + 30)
    return load(out)


def ad_counter(rec: dict) -> int | None:
    for ad in decode(bytes.fromhex(rec["pdu"])).get("ad", []):
        if ad["type"] == 0xFF and len(ad["value"]) == 4:
            v = bytes.fromhex(ad["value"])
            return v[0] | (v[1] << 8)
    return None


# ---------------------------------------------------------------------------- tests

def t01_adv_tx_rx(c: Ctx):
    """Advertising: ble_tx on A (ADV_IND, name, counter, ch 37/38/39) while ble_rx on B listens on 37."""
    pk = txrx(c, "t01_adv", ["--defaults", "--addr", OWN, "--type", "random", "--name", "blesim",
                             "--counter", "on", "--channels", "37", "38", "39", "--interval", "30",
                             "--duration", "5"],
              f"{c.b},label=B,ch=37", ["--adva", OWN])
    # A phone nearby may answer our scannable ADV_IND with SCAN_REQs, which
    # carry our address too; they are counted, not checked.
    scan_req = sum(p["type"] == "SCAN_REQ" for p in pk)
    pk = [p for p in pk if p["type"] != "SCAN_REQ"]
    assert len(pk) >= 80, f"only {len(pk)} packets"
    assert all(p["type"] == "ADV_IND" and p["name"] == "blesim" and p["crc_ok"] for p in pk)
    cnt = [ad_counter(p) for p in pk]
    assert all(b > a for a, b in zip(cnt, cnt[1:])), "event counter not increasing"
    lost = (cnt[-1] - cnt[0] + 1) - len(pk)
    return (f"{len(pk)} ADV_IND from {OWN} 'blesim', counter {cnt[0]}..{cnt[-1]} "
            f"({lost} events missed), all CRC OK; plus {scan_req} SCAN_REQ from external scanners")


def t02_rx_hop_all_adv(c: Ctx):
    """Receiver hopping 37/38/39 (dwell 50 ms) sees the transmitter on every advertising channel."""
    pk = txrx(c, "t02_hop", ["--defaults", "--addr", OWN, "--channels", "37", "38", "39",
                             "--interval", "20", "--duration", "5"],
              f"{c.b},label=B,ch=37/38/39,dwell=50", ["--adva", OWN])
    per = collections.Counter(p["ch"] for p in pk)
    assert set(per) == {37, 38, 39}, per
    return "packets per channel: " + ", ".join(f"ch{k}={v}" for k, v in sorted(per.items()))


def t03_tx_options(c: Ctx):
    """ADV_NONCONN_IND, public address, custom AD data with spaces, counter off, -8 dBm, --count 60."""
    addr = "11:22:33:44:55:66"
    pk = txrx(c, "t03_opts", ["--defaults", "--pdu", "adv_nonconn_ind", "--type", "public",
                              "--addr", addr, "--data", "02 01 06 05 ff 34 12 ab cd",
                              "--counter", "off", "--power", "-8", "--channels", "37",
                              "--interval", "20", "--count", "60"],
              f"{c.b},label=B,ch=37", ["--adva", addr])
    want = "020f" + "665544332211" + "020106" + "05ff3412abcd"   # LENGTH 15 = 6 + 3 + 6
    assert pk and all(p["pdu"] == want for p in pk), pk[:1]
    assert 50 <= len(pk) <= 60, len(pk)
    return f"{len(pk)}/60 packets, every PDU == {want} (header 0x02: NONCONN, TxAdd=0)"


def t04_direct(c: Ctx):
    """ADV_DIRECT_IND with --direct TargetA."""
    pk = txrx(c, "t04_direct", ["--defaults", "--addr", OWN, "--pdu", "adv_direct_ind",
                                "--direct", "AA:BB:CC:00:11:22", "--channels", "37",
                                "--interval", "20", "--duration", "3"],
              f"{c.b},label=B,ch=37", ["--type", "ADV_DIRECT_IND", "--adva", OWN], rx_seconds=6)
    d = decode(bytes.fromhex(pk[0]["pdu"]))
    assert len(pk) > 20 and d["targeta"] == "AA:BB:CC:00:11:22", d
    return f"{len(pk)} ADV_DIRECT_IND, AdvA {d['adva']} -> TargetA {d['targeta']}"


def t05_crcerr(c: Ctx):
    """--crcerr 30 with the receiver reporting all CRCs: ~30 % of packets fail the CRC."""
    pk = txrx(c, "t05_crcerr", ["--defaults", "--addr", OWN, "--crcerr", "30", "--channels", "37",
                                "--interval", "15", "--duration", "5"],
              f"{c.b},label=B,ch=37,crc=all", ["--contains", "3412efbeadde"])
    bad = sum(not p["crc_ok"] for p in pk)
    frac = bad / len(pk)
    assert len(pk) > 100 and 0.18 < frac < 0.42, (len(pk), bad)
    return f"{bad}/{len(pk)} = {frac:.0%} with a bad CRC (asked for 30 %)"


def t06_phy_twin(c: Ctx):
    """nrf1m modulation and the twin: main ble1m + twin nrf1m (same address, 50 %), receiver ble1m."""
    pk = txrx(c, "t06_twin", ["--defaults", "--addr", OWN, "--twin", "on", "--twin-addr", "same",
                              "--twin-phy", "nrf1m", "--twin-power", "4", "--twin-pct", "50",
                              "--channels", "37", "--interval", "20", "--count", "200"],
              f"{c.b},label=B,ch=37", ["--adva", OWN])
    pk2 = txrx(c, "t06_nrf1m", ["--defaults", "--addr", OWN, "--phy", "nrf1m", "--channels", "37",
                                "--interval", "20", "--count", "100"],
               f"{c.b},label=B,ch=37,phy=nrf1m", ["--adva", OWN])
    pk = [p for p in pk if p["type"] == "ADV_IND"]          # not external SCAN_REQs
    pk2 = [p for p in pk2 if p["type"] == "ADV_IND"]
    assert len(pk) > 150 and len(pk2) > 80, (len(pk), len(pk2))
    return (f"twin run (ble1m main + nrf1m twin, one address): {len(pk)}/200 events received "
            f"by a ble1m receiver; nrf1m TX -> nrf1m RX: {len(pk2)}/100")


def t07_raw_adv_whitespace(c: Ctx):
    """Raw advertising PDU typed with spaces is sent byte-exact."""
    raw = " 42 0b 66 55 44 33 22 11 04 09 52 41 57"
    pk = txrx(c, "t07_raw", ["--defaults", "--raw", raw, "--channels", "37", "--interval", "20",
                             "--duration", "4"],
              f"{c.b},label=B,ch=37", ["--adva", "11:22:33:44:55:66"])
    want = raw.replace(" ", "")
    assert len(pk) > 50 and all(p["pdu"] == want for p in pk), pk[:1]
    return f"{len(pk)} packets, PDU == {want} ({pk[0]['summary']})"


def t08_raw_user_example(c: Ctx):
    """The README example ' 80 60 64 1c ae 7a': LENGTH fixed (default) and sent as given (keep)."""
    pk = txrx(c, "t08_fix", ["--defaults", "--raw", " 80 60 64 1c ae 7a", "--channels", "37",
                             "--interval", "20", "--duration", "3"],
              f"{c.b},label=B,ch=37", ["--contains", "641cae7a"], rx_seconds=6)
    pk2 = txrx(c, "t08_keep", ["--defaults", "--raw", " 80 60 64 1c ae 7a", "--raw-length", "keep",
                               "--channels", "37", "--interval", "20", "--duration", "3"],
               f"{c.b},label=B,ch=37", ["--contains", "8060641cae7a"], rx_seconds=6)
    # 64:1C:AE:7A:6F:C1 nearby carries "...8060641cae7a..." in its own payload;
    # ours are the PDUs whose header byte is 0x80.
    pk = [p for p in pk if p["pdu"].startswith("80")]
    pk2 = [p for p in pk2 if p["pdu"].startswith("80")]
    assert pk and all(p["pdu"] == "8004641cae7a" for p in pk)
    assert pk2 and all(p["pdu"] == "8060641cae7a" + "00" * 92 for p in pk2)
    return (f"fix: {len(pk)} x 8004641cae7a; keep: {len(pk2)} x 8060641cae7a + 92 zero bytes "
            "(98-byte PDU as LENGTH=0x60 demands)")


def t09_data_packets(c: Ctx):
    """Data-channel packets: LL_DATA_START 'Hello' and an LL_CONTROL PDU with a custom AA / CRC init."""
    spec = f"{c.b},label=B,ch=5,aa=0x9E8B4B27,crcinit=0x123456"
    pk = txrx(c, "t09_data", ["--defaults", "--raw", "0e 05 48 65 6c 6c 6f", "--channels", "5",
                              "--aa", "0x9E8B4B27", "--crcinit", "0x123456", "--interval", "20",
                              "--duration", "4"], spec)
    pk2 = txrx(c, "t09_ctrl", ["--defaults", "--raw", "03 02 02 13", "--channels", "22",
                               "--aa", "0x9E8B4B27", "--crcinit", "0x123456", "--interval", "20",
                               "--duration", "3"],
               f"{c.b},label=B,ch=22,aa=0x9E8B4B27,crcinit=0x123456", rx_seconds=6)
    assert len(pk) > 50 and all(p["pdu"] == "0e0548656c6c6f" and p["type"] == "LL_DATA_START"
                                and p["aa"] == "9e8b4b27" for p in pk)
    assert len(pk2) > 30 and all(p["type"] == "LL_CONTROL" for p in pk2)
    return (f"{len(pk)} x '{pk[0]['summary']}' (payload 'Hello') on ch 5; "
            f"{len(pk2)} x '{pk2[0]['summary']}' on ch 22")


def t10_adv_and_data_one_receiver(c: Ctx):
    """One receiver hops between ch 37 (advertising AA) and ch 5 (data AA): both kinds in one capture."""
    pk = txrx(c, "t10_mixed", ["--defaults", "--raw", "0e 05 48 65 6c 6c 6f", "--channels", "5",
                               "--aa", "0x9E8B4B27", "--crcinit", "0x123456", "--interval", "10",
                               "--duration", "6"],
              f"{c.b},label=B,ch=37/5:0x9E8B4B27:0x123456,dwell=100")
    adv = [p for p in pk if p["aa"] == "8e89bed6"]
    data = [p for p in pk if p["aa"] == "9e8b4b27"]
    assert adv and data and all(p["ch"] == 37 for p in adv) and all(p["ch"] == 5 for p in data)
    advas = {p["adva"] for p in adv if p["adva"]}
    return (f"{len(adv)} advertising packets (ch 37, {len(advas)} external advertisers) and "
            f"{len(data)} data packets (ch 5, AA 0x9E8B4B27) in one capture")


def t11_retransmit_external(c: Ctx):
    """Capture an external advertiser with B, replay its PDU with A, receive the replay with B."""
    cap = CAP / "t11_external.jsonl"
    c.run([RX, "--dev", f"{c.b},label=B,ch=37", "--duration", "5", "-o", str(cap.relative_to(ROOT)),
           "--quiet"], timeout=40)
    pk = [p for p in load(cap) if p["adva"] and p["adva"] != OWN and p["type"] != "SCAN_REQ"]
    assert pk, "no external advertiser heard"
    best = collections.Counter(p["adva"] for p in pk).most_common(1)[0][0]
    orig = next(p for p in pk if p["adva"] == best)
    # the advertiser's own packets keep arriving on ch 37 during the replay;
    # they are identical only if it does not rotate its payload
    rotating = len({p["pdu"] for p in pk if p["adva"] == best and p["type"] == orig["type"]}) > 1
    # Replayed on data channel 10 with the advertising AA: the original device
    # never transmits there, so whatever B hears on ch 10 is A's replay.
    rp = txrx(c, "t11_replay_ch10", ["--defaults", "--raw-from", str(cap.relative_to(ROOT)),
                                     "--pick-adva", best, "--channels", "10", "--interval", "20",
                                     "--duration", "4"],
              f"{c.b},label=B,ch=10", ["--adva", best, "--type", orig["type"]])
    assert len(rp) > 50 and all(p["pdu"] == orig["pdu"] for p in rp)
    # And the RAW field (PDU + CRC, spaced) pasted directly, replayed on ch 37.
    spaced = " ".join(orig["raw"][i:i + 2] for i in range(0, len(orig["raw"]), 2))
    rp2 = txrx(c, "t11_replay_ch37", ["--defaults", "--raw", spaced, "--channels", "37",
                                      "--interval", "20", "--duration", "4"],
               f"{c.b},label=B,ch=37", ["--adva", best, "--type", orig["type"]])
    rate_before = sum(p["adva"] == best and p["type"] == orig["type"] for p in pk) / 5
    rate_after = len(rp2) / 7
    assert len(rp2) > 50
    assert rotating or all(p["pdu"] == orig["pdu"] for p in rp2)
    return (f"external {best} ({orig['type']}, {len(orig['pdu']) // 2}-byte PDU, "
            f"{orig['rssi']} dBm): replay on ch 10 received {len(rp)} times byte-exact; "
            f"RAW+CRC pasted with spaces -> CRC removed, ch 37 rate "
            f"{rate_before:.1f}/s -> {rate_after:.1f}/s, byte-exact")


def t12_dual_sniff(c: Ctx):
    """Both boards sniff at once (A: ch 37, B: hop 38/39), saved to .jsonl/.csv/.pcapng/.txt."""
    base = CAP / "t12_dual"
    outs = [f"{base}.{e}" for e in ("jsonl", "csv", "pcapng", "txt")]
    args = [RX, "--dev", f"{c.a},label=A,ch=37", "--dev", f"{c.b},label=B,ch=38/39,dwell=200",
            "--duration", "10", "--quiet"]
    for o in outs:
        args += ["-o", str(Path(o).relative_to(ROOT))]
    c.run(args, timeout=60)
    pk = load(Path(outs[0]))
    by = collections.defaultdict(list)
    for p in pk:
        by[p["device"]].append(p)
    assert set(by) == {"A", "B"}, set(by)
    assert {p["ch"] for p in by["A"]} == {37} and {p["ch"] for p in by["B"]} == {38, 39}
    both = {p["adva"] for p in by["A"]} & {p["adva"] for p in by["B"]} - {""}
    frames = read_pcapng(outs[2])
    assert collections.Counter(f[0] for f in frames) == {k: len(v) for k, v in by.items()}
    return (f"A: {len(by['A'])} packets on ch 37; B: {len(by['B'])} on ch 38/39; "
            f"{len(both)} advertisers heard by both; pcapng has interfaces A and B with matching counts")


def t13_dual_sniff_same_channel(c: Ctx):
    """Both boards on ch 37 simultaneously: the same packets are reported by both, tagged A and B."""
    out = CAP / "t13_same_ch.jsonl"
    c.run([RX, "--dev", f"{c.a},label=A", "--dev", f"{c.b},label=B", "--ch", "37",
           "--duration", "8", "-o", str(out.relative_to(ROOT)), "--quiet"], timeout=60)
    pk = load(out)
    seen = collections.defaultdict(set)
    for p in pk:
        seen[(p["pdu"], round(p["host_time"], 1))].add(p["device"])
    a = sum(p["device"] == "A" for p in pk)
    b = sum(p["device"] == "B" for p in pk)
    pdus_a = {p["pdu"] for p in pk if p["device"] == "A"}
    pdus_b = {p["pdu"] for p in pk if p["device"] == "B"}
    common = len(pdus_a & pdus_b) / max(1, len(pdus_a | pdus_b))
    assert a > 50 and b > 50 and common > 0.5, (a, b, common)
    return f"A {a} packets, B {b} packets; {common:.0%} of distinct PDUs seen by both receivers"


def t14_persistence(c: Ctx):
    """save / defaults / load / erase, --info, --fw-help, --cmd, --stop, --list, shell via stdin."""
    c.run([TX, "--port", c.a, "--defaults", "--addr", "C0:FF:EE:00:00:01", "--interval", "77",
           "--save", "-q"])
    c.run([TX, "--port", c.a, "--defaults", "-q"])
    out = c.run([TX, "--port", c.a, "--load", "--info", "-q"])
    assert "C0:FF:EE:00:00:01" in out and '"interval_ms": 77' in out, out
    c.run([TX, "--port", c.a, "--erase", "--defaults", "-q"])
    helptext = c.run([TX, "--port", c.a, "--fw-help", "-q"])
    assert "rx ch" in helptext and "raw <hex>" in helptext
    out = c.run([TX, "--port", c.a, "--cmd", "load", "--cmd", "rx info", "-q"])
    assert "no saved configuration" in out
    lst = c.run([TX, "--list"])
    assert c.a in lst and c.b in lst
    c.log.append(f"$ echo info | python ble_tx/ble_tx.py --port {c.a} --shell")
    p = subprocess.run([PY, TX, "--port", c.a, "--shell", "-q"], input="info\nquit\n",
                       capture_output=True, text=True, timeout=30, cwd=ROOT)
    c.log.append(_clip(p.stdout.strip(), 8))
    assert '"ev": "info"' in p.stdout
    c.run([TX, "--port", c.a, "--start", "-q"])
    c.run([TX, "--port", c.a, "--stop", "-q"])
    # boot rx: the board sniffs on its own after a power-up / reboot
    c.run([TX, "--port", c.a, "--defaults", "--boot", "rx", "--save", "--reboot", "-q"])
    time.sleep(6)                          # reboot + USB re-enumeration
    out = c.run([TX, "--port", c.a, "--info", "-q"])
    assert '"ev": "mode", "tx": 0, "rx": 1, "boot": "rx"' in out, out
    c.run([TX, "--port", c.a, "--stop", "--erase", "--defaults", "-q"])
    return ("saved config restored by load; erase -> 'no saved configuration'; boot rx -> receiving "
            "after reboot; help/info/list/shell OK")


TESTS = [t01_adv_tx_rx, t02_rx_hop_all_adv, t03_tx_options, t04_direct, t05_crcerr, t06_phy_twin,
         t07_raw_adv_whitespace, t08_raw_user_example, t09_data_packets,
         t10_adv_and_data_one_receiver, t11_retransmit_external, t12_dual_sniff,
         t13_dual_sniff_same_channel, t14_persistence]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--a", required=True, help="board A (transmitter) port")
    ap.add_argument("--b", required=True, help="board B (receiver) port")
    ap.add_argument("--only", help="comma-separated test ids, e.g. T07,T11")
    ap.add_argument("--report", default=str(ROOT / "docs" / "TEST_REPORT.md"))
    a = ap.parse_args()
    CAP.mkdir(parents=True, exist_ok=True)
    only = {x.strip().upper() for x in a.only.split(",")} if a.only else None

    results = []
    for i, t in enumerate(TESTS, 1):
        tid = f"T{i:02d}"
        if only and tid not in only:
            continue
        c = Ctx(a.a, a.b)
        t0 = time.time()
        try:
            msg, ok = t(c), True
        except Exception as e:                                  # noqa: BLE001
            msg, ok = f"{type(e).__name__}: {e}", False
        dt = time.time() - t0
        print(f"{tid} {'PASS' if ok else 'FAIL'} ({dt:.0f} s) {t.__doc__.splitlines()[0]}\n     {msg}",
              flush=True)
        results.append((tid, t, ok, msg, c.log))
        # leave both boards idle for the next test
        for port in (a.a, a.b):
            subprocess.run([PY, TX, "--port", port, "--stop", "-q"], capture_output=True, cwd=ROOT)

    passed = sum(r[2] for r in results)
    lines = [
        "# Hardware test report", "",
        f"Run {time.strftime('%Y-%m-%d %H:%M')} with `tests/hw/run_hw_tests.py --a {a.a} --b {a.b}`: "
        f"**{passed}/{len(results)} passed**.", "",
        f"Board A = `{a.a}` (transmitter, and the second sniffer in T12/T13), "
        f"board B = `{a.b}` (receiver). Captures are written to `docs/test_captures/` "
        "(git-ignored: they contain nearby third-party devices). Third-party device "
        "addresses are masked in this report.", "",
        "| Test | What | Result |", "|---|---|---|",
    ]
    for tid, t, ok, msg, _ in results:
        lines.append(f"| {tid} | {t.__doc__.splitlines()[0]} | {'PASS' if ok else '**FAIL**'}: {msg} |")
    lines.append("")
    for tid, t, ok, msg, log in results:
        lines += [f"## {tid} — {t.__doc__.splitlines()[0]}", "", f"**{'PASS' if ok else 'FAIL'}** — {msg}",
                  "", "```text", *log, "```", ""]
    Path(a.report).write_text(redact("\n".join(lines)), encoding="utf-8")
    print(f"{passed}/{len(results)} passed; report: {a.report}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
