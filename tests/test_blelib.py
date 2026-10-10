import json

import pytest

from blelib.crc import air_to_register, crc24, crc_bytes, register_to_air
from blelib.hexutil import HexError, normalize_hex, parse_hex, spaced
from blelib.output import open_writer, read_pcapng
from blelib.pdu import decode, mac, parse_ad
from blelib.raw import prepare_raw

# Captured on air by the ble_trx receiver: (PDU, RXCRC register value).
CAPTURED = [
    ("0012062420b1e59002010608ff4d310000000000", 0xFA3371),
    ("47074618272d482600", 0x614EE2),
    ("42244534ad55f83d0201041aff4c0002152686f39cbada4658854aa62e7e5e8b8d00010000c9", 0x15FFA7),
]
EXT = "0222c16f7aae1c641bff75004204018060641cae7a6fc1661cae7a6fc0014f5300000000"


# ---- hex input --------------------------------------------------------------

@pytest.mark.parametrize("text", [
    " 80 60 64 1c ae 7a", "8060641cae7a", "80 60 64 1C AE 7A ", "80:60:64:1c:ae:7a",
    "80-60-64-1c-ae-7a", "0x80 0x60 0x64 0x1c 0xae 0x7a", "80,60,64,1c,ae,7a",
    "\t80 60\n64 1c\r\nae 7a\n", "8060 641c ae7a",
])
def test_whitespace_and_separators_removed(text):
    assert normalize_hex(text) == "8060641cae7a"


def test_hexdump_with_offsets_and_ascii_column():
    dump = ("0000  60 13 e1 c1 e0 a2 4f f7 02 01 86 03 82 0f fe 0d  `.....O.........\n"
            "0010  46 0f ff 68 18 df ff 02                          F..h....")
    assert normalize_hex(dump) == "6013e1c1e0a24ff702018603820ffe0d460fff6818dfff02"


@pytest.mark.parametrize("bad", ["", "   ", "80 6", "zz", "80 6g"])
def test_bad_hex_rejected(bad):
    with pytest.raises(HexError):
        normalize_hex(bad)


def test_spaced():
    assert spaced(parse_hex("8060")) == "80 60"


# ---- CRC ----------------------------------------------------------------------

@pytest.mark.parametrize("pdu,reg", CAPTURED)
def test_crc_matches_radio(pdu, reg):
    assert crc24(bytes.fromhex(pdu)) == reg


def test_crc_air_order_round_trip():
    for _, reg in CAPTURED:
        assert air_to_register(register_to_air(reg)) == reg
    # same convention as the bladeRF sniffer's RAW (PDU + CRC on air)
    assert crc_bytes(bytes.fromhex(CAPTURED[0][0])).hex() == "5fcc8e"


def test_crc_init_matters():
    pdu = bytes.fromhex(CAPTURED[0][0])
    assert crc24(pdu, 0x123456) != crc24(pdu)


# ---- raw PDU preparation ---------------------------------------------------

def test_raw_user_example_whitespace_and_length_fix():
    rp = prepare_raw(" 80 60 64 1c ae 7a")
    assert rp.hex == "8004641cae7a"            # LENGTH 0x60 -> 4 payload bytes
    assert any("LENGTH" in n for n in rp.notes)


def test_raw_keep_length_sends_as_given():
    rp = prepare_raw(" 80 60 64 1c ae 7a", length_mode="keep")
    assert rp.hex == "8060641cae7a"
    assert "zero-padded" in rp.notes[0]


def test_raw_error_mode():
    with pytest.raises(HexError):
        prepare_raw("80 60 64 1c ae 7a", length_mode="error")


def test_raw_consistent_pdu_untouched():
    rp = prepare_raw(EXT)
    assert rp.hex == EXT and rp.notes == []


def test_raw_trailing_crc_removed():
    pdu = bytes.fromhex(EXT)
    rp = prepare_raw(spaced(pdu + crc_bytes(pdu)))
    assert rp.pdu == pdu
    assert "matched" in rp.notes[0]


def test_raw_bounds():
    with pytest.raises(HexError):
        prepare_raw("80")
    with pytest.raises(HexError):
        prepare_raw("02ff" + "00" * 256)          # 258 bytes


# ---- decoding ---------------------------------------------------------------

def test_decode_adv_nonconn():
    d = decode(bytes.fromhex(EXT))
    assert d["type"] == "ADV_NONCONN_IND"
    assert d["adva"] == "64:1C:AE:7A:6F:C1"
    assert d["ad"][0]["name"] == "Manufacturer data" and d["ad"][0]["company"] == "0x0075"


def test_decode_name_and_flags():
    pdu = bytes.fromhex("400f3412efbeadde020106070962" + "6c6573696d".encode().hex()[:0] + "6c6573696d")
    pdu = bytes([0x40, 6 + 3 + 8]) + bytes.fromhex("3412efbeadde") + bytes.fromhex("020106") + \
        bytes([7, 9]) + b"blesim"
    d = decode(pdu)
    assert d["adva"] == "DE:AD:BE:EF:12:34" and d["txadd"] == 1 and d["name"] == "blesim"


def test_decode_data_channel():
    d = decode(bytes.fromhex("0e0548656c6c6f"), access_address=0x9E8B4B27)
    assert d["kind"] == "data" and d["llid"] == "LL_DATA_START" and d["sn"] == 1 and d["nesn"] == 1
    d = decode(bytes.fromhex("0302 0c08".replace(" ", "")), access_address=0x9E8B4B27)
    assert d["llid"] == "LL_CONTROL" and d["opcode"] == "0x0C"


def test_decode_never_raises():
    for junk in (b"", b"\x00", b"\x00\x20", bytes(range(40))):
        decode(junk)
    assert parse_ad(b"\x05\x09ab") == []           # truncated AD
    assert mac(bytes.fromhex("3412efbeadde")) == "DE:AD:BE:EF:12:34"


# ---- writers ---------------------------------------------------------------------

def _rec(dev, n, pdu=EXT, ch=37, ok=True):
    from blelib.crc import crc_bytes as cb
    return {"host_time": 1760000000.0 + n, "device": dev, "port": "COMx", "serial": "S",
            "n": n, "dev_time": 1.0 * n, "ch": ch, "freq_mhz": 2402, "rssi": -60,
            "crc_ok": ok, "aa": "8e89bed6", "type": "ADV_NONCONN_IND", "adva": "", "name": "",
            "summary": "s", "pdu": pdu, "crc": cb(bytes.fromhex(pdu)).hex(),
            "raw": "", "pdu_spaced": "", "decoded": {}}


def test_pcapng_one_interface_per_device(tmp_path):
    path = str(tmp_path / "x.pcapng")
    w = open_writer(path)
    w.add_device("A", "COM7", "1")
    w.add_device("B", "COM5", "2")
    w.write(_rec("A", 1))
    w.write(_rec("B", 2))
    w.write(_rec("A", 3))
    w.close()
    frames = read_pcapng(path)
    assert [f[0] for f in frames] == ["A", "B", "A"]
    f = frames[0][1]
    assert f[10:14] == bytes.fromhex("d6be898e")                 # AA after the 10-byte PHDR
    assert f[14:-3].hex() == EXT


def test_jsonl_and_csv(tmp_path):
    for ext in ("jsonl", "csv", "txt"):
        path = str(tmp_path / f"x.{ext}")
        w = open_writer(path)
        w.write(_rec("A", 1))
        w.close()
        text = open(path, encoding="utf-8").read()
        assert "A" in text and (EXT in text or ext == "txt")
    row = json.loads(open(tmp_path / "x.jsonl", encoding="utf-8").readline())
    assert row["device"] == "A" and row["pdu"] == EXT


def test_unknown_output_extension():
    with pytest.raises(ValueError):
        open_writer("x.bin")
