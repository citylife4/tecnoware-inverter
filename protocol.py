"""
Voltronic / Tecnoware SolarPower ASCII protocol primitives.

Everything in this module was reverse-engineered from the bundled
SolarPowerApp (specifically cn/com/voltronic/solar/util/CRCUtil.class and
cn/com/voltronic/solar/communicate/SerialHandler.class inside lib/SolarPower.jar)
by decompiling the Java .class files -- not guessed from generic docs.

CRC: 256-entry CRC-16/CCITT (XModem, poly=0x1021, init=0) lookup table, but
the device only ever needs the first 16 entries because the algorithm
consumes the command 4 bits (one nibble) at a time. After computing the
16-bit CRC, if the resulting high or low byte happens to equal ')'/CR/LF
-- 0x28, 0x0D, 0x0A -- that byte is incremented by one, mirroring firmware
behaviour that reserves those bytes as frame delimiters.
"""

from __future__ import annotations

CRC_TABLE = [
    0x0000, 0x1021, 0x2042, 0x3063, 0x4084, 0x50A5, 0x60C6, 0x70E7,
    0x8108, 0x9129, 0xA14A, 0xB16B, 0xC18C, 0xD1AD, 0xE1CE, 0xF1EF,
]

_ESCAPE_BYTES = (0x28, 0x0D, 0x0A)  # '(' , CR, LF


def calc_crc(data: bytes) -> int:
    """Port of CRCUtil.caluCRC(byte[]) -- nibble-wise nibble table CRC."""
    crc = 0
    for byte in data:
        da = ((crc >> 8) & 0xFF) >> 4
        crc = (crc << 4) & 0xFFFF
        crc ^= CRC_TABLE[da ^ (byte >> 4)]

        da = ((crc >> 8) & 0xFF) >> 4
        crc = (crc << 4) & 0xFFFF
        crc ^= CRC_TABLE[da ^ (byte & 0x0F)]

    low = crc & 0xFF
    high = (crc >> 8) & 0xFF
    if low in _ESCAPE_BYTES:
        low += 1
    if high in _ESCAPE_BYTES:
        high += 1
    return (high << 8) | low


def crc_bytes(command: str) -> bytes:
    """Port of CRCUtil.getCRCByte(String) -- returns [highByte, lowByte]."""
    crc = calc_crc(command.encode("ascii", errors="replace"))
    return bytes([(crc >> 8) & 0xFF, crc & 0xFF])


def check_crc(response: str) -> bool:
    """Port of CRCUtil.checkCRC(String) -- validates the trailing 2 CRC bytes."""
    if len(response) < 3:
        return False
    try:
        payload = response[:-2].encode("ascii", errors="replace")
        crc = calc_crc(payload)
        expected_high = (crc >> 8) & 0xFF
        expected_low = crc & 0xFF
        got_high = ord(response[-2])
        got_low = ord(response[-1])
        return got_high == expected_high and got_low == expected_low
    except Exception:
        return False


def strip_crc_if_valid(response: str) -> tuple[str, bool]:
    """Some firmware echoes a CRC on responses, some doesn't -- and it can
    vary command-by-command on the same device (confirmed against a real
    unit: QPI/QPIGS/QPIRI come back without a CRC suffix, but QMOD does
    carry one). So every response is checked independently rather than
    caching one yes/no decision for the whole session. If the trailing 2
    bytes check out as a valid CRC of the rest, strip them."""
    if len(response) >= 3 and check_crc(response):
        return response[:-2], True
    return response, False
