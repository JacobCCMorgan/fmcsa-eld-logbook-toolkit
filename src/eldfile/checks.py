"""Data check values of the ELD output file (49 CFR 395 Appendix A, section 4.4.5).

Three checks protect the file:

* the **event data check** is computed when an ELD event is recorded, over ten of its elements, and
  carried in the event record;
* the **line data check** is the last field of every data line of the output file;
* the **file data check** closes the file and is derived from all line data check values.

All three start from the same character mapping (Table 3 of Appendix A): digits 1-9 and letters A-Z,
a-z map to their ASCII code minus 48, every other character maps to zero. Sums are reduced to one
byte (or two for the file check), each byte is rotated left three times and XOR-ed with a constant.
"""
from __future__ import annotations

from collections.abc import Iterable

EVENT_XOR = 0xC3
LINE_XOR = 0x96
FILE_XOR = 0x969C


def char_value(ch: str) -> int:
    """Table 3 mapping: '1'-'9', 'A'-'Z', 'a'-'z' -> ASCII code - 48; anything else -> 0."""
    o = ord(ch)
    if 49 <= o <= 57 or 65 <= o <= 90 or 97 <= o <= 122:
        return o - 48
    return 0


def mapped_sum(text: str) -> int:
    return sum(char_value(c) for c in text)


def rol8(value: int, bits: int = 3) -> int:
    value &= 0xFF
    return ((value << bits) | (value >> (8 - bits))) & 0xFF


def line_check(line_without_check: str) -> str:
    """Line data check value of a data line, computed over everything before the final comma."""
    v = rol8(mapped_sum(line_without_check) & 0xFF) ^ LINE_XOR
    return f"{v:02X}"


def event_check(event_type: str, event_code: str, date: str, time: str, vehicle_miles: str, engine_hours: str, latitude: str, longitude: str, power_unit: str, username: str) -> str:
    """Event data check value over the ten elements of 4.4.5.1.1 (Appendix A clarification: the
    'CMV number' element is the CMV power unit number)."""
    total = sum(mapped_sum(str(x)) for x in (event_type, event_code, date, time, vehicle_miles, engine_hours, latitude, longitude, power_unit, username))
    return f"{rol8(total & 0xFF) ^ EVENT_XOR:02X}"


def file_check(line_checks: Iterable[str]) -> str:
    """File data check value: sum of the line data check values, low two bytes, each byte rotated
    left three times, XOR 0x969C."""
    total = sum(int(x, 16) for x in line_checks) & 0xFFFF
    hi, lo = total >> 8, total & 0xFF
    return f"{((rol8(hi) << 8) | rol8(lo)) ^ FILE_XOR:04X}"


def split_check(line: str) -> tuple[str, str]:
    """Split a data line into (body, check) at the last comma."""
    body, _, check = line.rpartition(",")
    return body, check


def verify_line(line: str) -> bool:
    body, check = split_check(line)
    return check.upper() == line_check(body)
