"""Turn whatever a user pastes as "hex" into bytes.

Accepted, in any mix:

* plain or spaced hex              ``8060641cae7a``, `` 80 60 64 1c ae 7a``
* separators                       ``80:60:64``, ``80-60-64``, ``80,60,64``
* ``0x`` prefixes                   ``0x80 0x60 0x64``
* line breaks and tabs
* hexdump lines with an offset and an ASCII column, as the bladeRF sniffer's
  hex view and ``xxd``/Wireshark copy them::

      0000  60 13 e1 c1 e0 a2 4f f7 02 01 86 03 82 0f fe 0d  `.....O.........
      0010  46 0f ff 68 18 df ff 02                          F..h....
"""

from __future__ import annotations

import re

_DUMP_LINE = re.compile(r"^\s*([0-9a-fA-F]{4,8})[:\s]\s*((?:[0-9a-fA-F]{2}\s+){0,15}[0-9a-fA-F]{2})(?:\s{2,}.*)?$")
_SEPARATORS = re.compile(r"[\s:,\-_.;]+")


class HexError(ValueError):
    pass


def normalize_hex(text: str) -> str:
    """Return the compact lower-case hex digits of `text` (see module doc).

    >>> normalize_hex(" 80 60 64 1c ae 7a")
    '8060641cae7a'
    """
    if text is None:
        raise HexError("no hex given")
    lines = [ln for ln in str(text).replace("\r", "\n").split("\n") if ln.strip()]
    if len(lines) > 0 and all(_is_dump_line(ln) for ln in lines):
        digits = "".join(_dump_bytes(ln) for ln in lines)
    else:
        s = str(text).strip()
        s = re.sub(r"0[xX](?=[0-9a-fA-F])", " ", s)   # 0x prefixes
        s = _SEPARATORS.sub("", s)
        digits = s
    if not digits:
        raise HexError("no hex digits given")
    bad = re.search(r"[^0-9a-fA-F]", digits)
    if bad:
        raise HexError(f"not a hex digit: {bad.group(0)!r}")
    if len(digits) % 2:
        raise HexError(f"odd number of hex digits ({len(digits)})")
    return digits.lower()


def parse_hex(text: str) -> bytes:
    """Bytes of `text` after :func:`normalize_hex`."""
    return bytes.fromhex(normalize_hex(text))


def spaced(b: bytes) -> str:
    """``b'\\x80\\x60'`` -> ``'80 60'``."""
    return " ".join(f"{x:02x}" for x in b)


def _is_dump_line(line: str) -> bool:
    m = _DUMP_LINE.match(line)
    if not m:
        return False
    # "8060 641c" would otherwise look like an offset plus one byte; require
    # the offset to be followed by at least two spaces or a colon, as dumps are.
    return bool(re.match(r"^\s*[0-9a-fA-F]{4,8}(:|\s{2,})", line))


def _dump_bytes(line: str) -> str:
    m = _DUMP_LINE.match(line)
    return re.sub(r"\s+", "", m.group(2)) if m else ""
