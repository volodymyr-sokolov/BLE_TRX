"""Decode BLE link-layer PDUs (2-byte header + payload) into readable fields.

Advertising-channel PDUs (legacy, Core spec Vol 6 Part B 2.3) and data-channel
PDUs (2.4) are both handled.  Which kind a PDU is follows from the access
address it was received on: the advertising AA 0x8E89BED6, or anything else.
"""

from __future__ import annotations

from .crc import ADV_ACCESS_ADDRESS

ADV_TYPES = {
    0x0: "ADV_IND", 0x1: "ADV_DIRECT_IND", 0x2: "ADV_NONCONN_IND",
    0x3: "SCAN_REQ", 0x4: "SCAN_RSP", 0x5: "CONNECT_IND",
    0x6: "ADV_SCAN_IND", 0x7: "ADV_EXT_IND", 0x8: "AUX_CONNECT_RSP",
}

LLID = {0: "RFU", 1: "LL_DATA_CONT", 2: "LL_DATA_START", 3: "LL_CONTROL"}

AD_TYPES = {
    0x01: "Flags", 0x02: "UUID16 (incomplete)", 0x03: "UUID16",
    0x04: "UUID32 (incomplete)", 0x05: "UUID32", 0x06: "UUID128 (incomplete)",
    0x07: "UUID128", 0x08: "Short name", 0x09: "Name", 0x0A: "TX power",
    0x16: "Service data", 0x19: "Appearance", 0xFF: "Manufacturer data",
}


def mac(b: bytes) -> str:
    """On-air (LSB-first) 6 bytes -> human 'AA:BB:CC:DD:EE:FF'."""
    return ":".join(f"{x:02X}" for x in reversed(bytes(b)))


def parse_ad(data: bytes) -> list[dict]:
    """AD structures of an advertising payload (stops at malformed data)."""
    out = []
    i = 0
    while i < len(data):
        n = data[i]
        if n == 0 or i + 1 + n > len(data):
            break
        t = data[i + 1]
        v = bytes(data[i + 2:i + 1 + n])
        item = {"type": t, "name": AD_TYPES.get(t, f"0x{t:02X}"), "value": v.hex()}
        if t in (0x08, 0x09):
            item["text"] = v.decode("utf-8", "replace")
        elif t == 0xFF and len(v) >= 2:
            item["company"] = f"0x{v[0] | (v[1] << 8):04X}"
        elif t == 0x0A and len(v) == 1:
            item["dbm"] = v[0] - 256 if v[0] > 127 else v[0]
        out.append(item)
        i += 1 + n
    return out


def decode(pdu: bytes, access_address: int = ADV_ACCESS_ADDRESS) -> dict:
    """Fields of one PDU.  Never raises on short or odd input."""
    pdu = bytes(pdu)
    if len(pdu) < 2:
        return {"kind": "short", "summary": "short PDU"}
    h0, length = pdu[0], pdu[1]
    payload = pdu[2:2 + length]
    d: dict = {"length": length, "length_ok": len(payload) == length}

    if access_address == ADV_ACCESS_ADDRESS:
        t = h0 & 0x0F
        d.update(kind="adv", type=ADV_TYPES.get(t, f"RFU_{t}"),
                 chsel=(h0 >> 5) & 1, txadd=(h0 >> 6) & 1, rxadd=(h0 >> 7) & 1)
        parts = [d["type"]]
        if t in (0x0, 0x2, 0x4, 0x6) and len(payload) >= 6:
            d["adva"] = mac(payload[:6])
            d["ad"] = parse_ad(payload[6:])
            parts.append(d["adva"] + ("(r)" if d["txadd"] else ""))
            names = [a["text"] for a in d["ad"] if "text" in a]
            if names:
                d["name"] = names[0]
                parts.append(repr(names[0]))
        elif t == 0x1 and len(payload) >= 12:
            d["adva"], d["targeta"] = mac(payload[:6]), mac(payload[6:12])
            parts += [d["adva"], "->", d["targeta"]]
        elif t == 0x3 and len(payload) >= 12:
            d["scana"], d["adva"] = mac(payload[:6]), mac(payload[6:12])
            parts += [d["scana"], "->", d["adva"]]
        elif t == 0x5 and len(payload) >= 34:
            d["inita"], d["adva"] = mac(payload[:6]), mac(payload[6:12])
            ll = payload[12:]
            d["conn_aa"] = f"0x{int.from_bytes(ll[0:4], 'little'):08X}"
            d["conn_crcinit"] = f"0x{int.from_bytes(ll[4:7], 'little'):06X}"
            d["hop"] = ll[21] & 0x1F
            parts += [d["inita"], "->", d["adva"], "AA", d["conn_aa"]]
        d["summary"] = " ".join(parts)
    else:
        llid = h0 & 0x03
        d.update(kind="data", llid=LLID[llid], nesn=(h0 >> 2) & 1,
                 sn=(h0 >> 3) & 1, md=(h0 >> 4) & 1, cp=(h0 >> 5) & 1)
        s = f"{d['llid']} SN={d['sn']} NESN={d['nesn']} MD={d['md']} len={length}"
        if llid == 3 and payload:
            d["opcode"] = f"0x{payload[0]:02X}"
            s += f" opcode={d['opcode']}"
        elif llid == 1 and length == 0:
            s = f"LL empty SN={d['sn']} NESN={d['nesn']} MD={d['md']}"
        d["summary"] = s
    return d
