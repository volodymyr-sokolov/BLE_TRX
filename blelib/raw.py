"""Prepare a user-supplied raw PDU for transmission.

A raw PDU is the 2-byte link-layer header followed by the payload, exactly as
it follows the access address on air (the radio adds preamble, access address,
CRC and whitening).  The radio transmits as many payload bytes as the header's
LENGTH octet (byte 1) says, so the bytes supplied and that field must agree.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .crc import ADV_CRC_INIT, crc_bytes
from .hexutil import HexError, parse_hex

MAX_PDU = 257          # 2-byte header + 255-byte payload

LENGTH_MODES = ("fix", "keep", "error")


@dataclass
class RawPdu:
    pdu: bytes
    notes: list[str] = field(default_factory=list)

    @property
    def hex(self) -> str:
        return self.pdu.hex()


def prepare_raw(text: str, crc_init: int = ADV_CRC_INIT, length_mode: str = "fix") -> RawPdu:
    """Parse and check a raw PDU.

    * whitespace / separators / 0x / hexdump offsets are removed
      (" 80 60 64 1c ae 7a" -> 8060641cae7a);
    * a trailing 3-byte CRC (as in a sniffer's RAW field, PDU + CRC) is
      recognised by length and removed -- the radio computes its own;
    * if LENGTH still disagrees with the payload supplied, `length_mode`
      decides: "fix" rewrites LENGTH (default), "keep" sends the bytes as
      given (the radio zero-pads or truncates to LENGTH), "error" refuses.
    """
    if length_mode not in LENGTH_MODES:
        raise ValueError(f"length_mode must be one of {LENGTH_MODES}")
    b = bytearray(parse_hex(text))
    notes: list[str] = []
    if len(b) < 2:
        raise HexError("a raw PDU needs at least the 2-byte header")

    declared = b[1]
    if len(b) == 2 + declared + 3:
        body, tail = bytes(b[:-3]), bytes(b[-3:])
        ok = crc_bytes(body, crc_init) == tail
        notes.append("removed trailing CRC " + tail.hex() +
                     (" (it matched)" if ok else
                      f" (it does not match CRC init 0x{crc_init:06X}; the radio sends a fresh one)"))
        b = bytearray(body)

    if len(b) > MAX_PDU:
        raise HexError(f"raw PDU is {len(b)} bytes; the maximum is {MAX_PDU}")

    have = len(b) - 2
    if have != b[1]:
        msg = f"LENGTH field is {b[1]} but {have} payload bytes were given"
        if length_mode == "error":
            raise HexError(msg)
        if length_mode == "fix":
            b[1] = have
            notes.append(msg + f"; LENGTH set to {have} (use --raw-length keep to send as given)")
        else:
            notes.append(msg + f"; sent as given: the radio sends {b[1]} payload bytes"
                         + (" (zero-padded)" if b[1] > have else " (truncated)"))
    return RawPdu(bytes(b), notes)
