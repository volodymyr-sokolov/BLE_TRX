"""BLE link-layer CRC-24 (Core spec Vol 6, Part B, 3.1.1).

The CRC is computed over the PDU (header + payload) LSB-first with the
polynomial x^24 + x^10 + x^9 + x^6 + x^4 + x^3 + x + 1, starting from the
24-bit CRC init (0x555555 on the advertising channels).  The value returned
here is in the same orientation as the nRF52840 RXCRC register, which is what
the ble_trx firmware reports as "crc".
"""

from __future__ import annotations

ADV_ACCESS_ADDRESS = 0x8E89BED6
ADV_CRC_INIT = 0x555555


def crc24(pdu: bytes, init: int = ADV_CRC_INIT) -> int:
    """CRC-24 of `pdu` as the nRF RADIO computes it (RXCRC orientation)."""
    # The nRF shift register is the spec's LFSR with the bit order reversed:
    # reflect the init in, run a reflected (LSB-first) CRC, reflect back out.
    state = _reflect24(init & 0xFFFFFF)
    for byte in pdu:
        for bit in range(8):
            fb = (state ^ (byte >> bit)) & 1
            state >>= 1
            if fb:
                state ^= 0xDA6000          # reflected 0x00065B
    return _reflect24(state)


def crc_bytes(pdu: bytes, init: int = ADV_CRC_INIT) -> bytes:
    """The three CRC octets as they follow the PDU on air.

    This is the byte order of a sniffer's RAW dump (PDU then CRC) and of the
    Wireshark BLUETOOTH_LE_LL link type.
    """
    return register_to_air(crc24(pdu, init))


def register_to_air(rxcrc: int) -> bytes:
    """nRF RXCRC register value -> the three CRC octets in on-air order."""
    r = _reflect24(rxcrc & 0xFFFFFF)
    return bytes((r & 0xFF, (r >> 8) & 0xFF, (r >> 16) & 0xFF))


def air_to_register(three: bytes) -> int:
    """Inverse of :func:`register_to_air`."""
    b = bytes(three)
    return _reflect24(b[0] | (b[1] << 8) | (b[2] << 16))


def _reflect24(v: int) -> int:
    r = 0
    for i in range(24):
        if v & (1 << i):
            r |= 1 << (23 - i)
    return r
