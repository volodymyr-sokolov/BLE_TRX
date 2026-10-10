"""argparse value checkers shared by ble_tx and ble_rx."""

from __future__ import annotations

import argparse


def access_address(text: str) -> int:
    """A 32-bit access address, e.g. 0x8E89BED6."""
    try:
        v = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number (use 0x...)") from None
    if not 0 < v <= 0xFFFFFFFF:
        hint = ""
        if v > 0xFFFFFFFF and v <= 0xFFFFFFFFFFFF:
            hint = (" -- that looks like a 48-bit device address; an access address is "
                    "32 bits (advertising: 0x8E89BED6). To select an advertiser use --adva")
        raise argparse.ArgumentTypeError(f"access address {text} is out of range 1..0xFFFFFFFF{hint}")
    return v


def crc_init(text: str) -> int:
    """A 24-bit CRC init, e.g. 0x555555."""
    try:
        v = int(text, 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number (use 0x...)") from None
    if not 0 <= v <= 0xFFFFFF:
        raise argparse.ArgumentTypeError(f"CRC init {text} is out of range 0..0xFFFFFF (24 bits)")
    return v
