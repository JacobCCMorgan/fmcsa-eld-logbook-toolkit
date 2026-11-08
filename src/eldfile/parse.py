"""Read an ELD output file (49 CFR 395 Appendix A, 4.8.2.1) into tables.

The file is a sequence of lines delimited by a single carriage return. Eleven section headers appear in
a fixed order; every other line is a comma-separated data line whose last field is a line data check
value, except the single four-character file data check line after ``End of File:``.

The parser is deliberately tolerant: it records what it finds (unknown headers, wrong field counts, a
different line delimiter) as issues instead of stopping, so that a non-compliant file can still be
examined. Nothing is interpreted here beyond splitting; validation lives in :mod:`eldfile.validate`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

SECTIONS: list[tuple[str, str]] = [
    ("ELD File Header Segment:", "header"),
    ("User List:", "users"),
    ("CMV List:", "cmvs"),
    ("ELD Event List:", "events"),
    ("ELD Event Annotations or Comments:", "annotations"),
    ("Driver's Certification/Recertification Actions:", "certifications"),
    ("Malfunctions and Data Diagnostic Events:", "malfunctions"),
    ("ELD Login/Logout Report:", "logins"),
    ("CMV Engine Power-Up and Shut-Down Activity:", "power"),
    ("Unidentified Driver Profile Records:", "unidentified"),
    ("End of File:", "end"),
]
HEADER_TEXT = {key: text for text, key in SECTIONS}

HEADER_LINES: list[tuple[str, list[str]]] = [
    ("driver", ["last_name", "first_name", "username", "license_state", "license_number"]),
    ("codriver", ["codriver_last_name", "codriver_first_name", "codriver_username"]),
    ("power_unit", ["power_unit", "vin", "trailers"]),
    ("carrier", ["usdot", "carrier_name", "multiday_basis", "period_start", "utc_offset"]),
    ("shipping", ["shipping_document", "exempt_driver"]),
    ("timeplace", ["current_date", "current_time", "current_lat", "current_lon", "total_miles", "total_hours"]),
    ("eld", ["eld_registration_id", "eld_identifier", "eld_authentication", "output_comment"]),
]

FIELDS: dict[str, list[str]] = {
    "users": ["user_order", "account_type", "last_name", "first_name"],
    "cmvs": ["cmv_order", "power_unit", "vin"],
    "events": ["seq", "record_status", "record_origin", "event_type", "event_code", "date", "time", "accum_miles", "elapsed_hours", "lat", "lon", "distance_since_valid", "cmv_order", "user_order", "malfunction_ind", "diagnostic_ind", "event_check"],
    "annotations": ["seq", "username", "comment", "date", "time", "location"],
    "certifications": ["seq", "event_code", "date", "time", "certified_date", "cmv_order"],
    "malfunctions": ["seq", "event_code", "mal_diag_code", "date", "time", "total_miles", "total_hours", "cmv_order"],
    "logins": ["seq", "event_code", "username", "date", "time", "total_miles", "total_hours"],
    "power": ["seq", "event_code", "date", "time", "total_miles", "total_hours", "lat", "lon", "power_unit", "vin", "trailers", "shipping_document"],
    "unidentified": ["seq", "record_status", "record_origin", "event_type", "event_code", "date", "time", "accum_miles", "elapsed_hours", "lat", "lon", "distance_since_valid", "cmv_order", "malfunction_ind", "event_check"],
}


def _norm_header(text: str) -> str:
    return re.sub(r"[^a-z]", "", text.lower())


HEADER_LOOKUP = {_norm_header(text): key for text, key in SECTIONS}


@dataclass
class Issue:
    level: str  # "error" | "warning"
    code: str
    line_no: int | None
    message: str


@dataclass
class OutputFile:
    lines: list[str]
    delimiter: str
    header: dict[str, str] = field(default_factory=dict)
    header_lines: list[str] = field(default_factory=list)
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    section_order: list[str] = field(default_factory=list)
    section_headers_seen: dict[str, str] = field(default_factory=dict)
    file_check: str | None = None
    data_lines: list[tuple[int, str, str]] = field(default_factory=list)  # (line_no, section, text)
    issues: list[Issue] = field(default_factory=list)

    @property
    def username(self) -> str:
        return self.header.get("username", "")

    @property
    def power_unit(self) -> str:
        return self.header.get("power_unit", "")

    def table(self, key: str) -> pd.DataFrame:
        return self.tables.get(key, pd.DataFrame(columns=FIELDS.get(key, []) + ["line_check", "line_no"]))


def split_lines(text: str) -> tuple[list[str], str]:
    """Split on the delimiter actually used; report which one it was."""
    if "\r\n" in text:
        delim = "\r\n"
    elif "\r" in text:
        delim = "\r"
    else:
        delim = "\n"
    return text.split(delim), delim


def read_text(text: str) -> OutputFile:
    lines, delim = split_lines(text)
    out = OutputFile(lines=lines, delimiter=delim)
    if delim != "\r":
        out.issues.append(Issue("warning", "line_delimiter", None, f"line delimiter is {delim!r}; the specification uses a single carriage return"))
    section = None
    rows: dict[str, list[list[str]]] = {k: [] for k in FIELDS}
    header_count = 0
    for no, raw in enumerate(lines, 1):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if stripped == "":
            if no != len(lines):
                out.issues.append(Issue("warning", "empty_line", no, "unexpected empty line"))
            continue
        key = HEADER_LOOKUP.get(_norm_header(stripped)) if stripped.endswith(":") and "," not in stripped else None
        if key:
            if key in out.section_headers_seen:
                out.issues.append(Issue("error", "duplicate_section", no, f"section {HEADER_TEXT[key]!r} appears more than once"))
            out.section_headers_seen[key] = stripped
            out.section_order.append(key)
            if stripped != HEADER_TEXT[key]:
                out.issues.append(Issue("warning", "section_header_text", no, f"section header {stripped!r} differs from {HEADER_TEXT[key]!r}"))
            section = key
            continue
        if stripped.endswith(":") and "," not in stripped:
            out.issues.append(Issue("error", "unknown_section", no, f"unknown section header {stripped!r}"))
            section = None
            continue
        if section is None:
            out.issues.append(Issue("error", "line_outside_section", no, "data line before any section header"))
            continue
        if section == "end":
            if out.file_check is None and re.fullmatch(r"[0-9A-Fa-f]{4}", stripped):
                out.file_check = stripped.upper()
            else:
                out.issues.append(Issue("error", "end_of_file", no, f"unexpected content after End of File: {stripped!r}"))
            continue
        out.data_lines.append((no, section, line))
        if section == "header":
            if header_count < len(HEADER_LINES):
                name, names = HEADER_LINES[header_count]
                fields = line.split(",")
                body, check = fields[:-1], fields[-1]
                if len(body) != len(names):
                    out.issues.append(Issue("error", "field_count", no, f"header line {name!r}: {len(body)} fields, expected {len(names)}"))
                for i, n in enumerate(names):
                    out.header[n] = body[i] if i < len(body) else ""
                out.header[f"{name}_line_check"] = check
                out.header_lines.append(line)
            else:
                out.issues.append(Issue("error", "field_count", no, "more than seven header lines"))
            header_count += 1
            continue
        fields = line.split(",")
        names = FIELDS[section]
        if len(fields) != len(names) + 1:
            out.issues.append(Issue("error", "field_count", no, f"{section}: {len(fields)} fields, expected {len(names) + 1}"))
            fields = (fields + [""] * (len(names) + 1))[: len(names) + 1] if len(fields) < len(names) + 1 else fields[: len(names)] + [fields[-1]]
        rows[section].append(fields[:-1] + [fields[-1].upper(), no])
    if header_count < len(HEADER_LINES):
        out.issues.append(Issue("error", "field_count", None, f"header segment has {header_count} lines, expected {len(HEADER_LINES)}"))
    for key, names in FIELDS.items():
        out.tables[key] = pd.DataFrame(rows[key], columns=names + ["line_check", "line_no"])
    missing = [HEADER_TEXT[k] for _, k in SECTIONS if k not in out.section_headers_seen]
    for m in missing:
        out.issues.append(Issue("error", "missing_section", None, f"missing section {m!r}"))
    expected = [k for _, k in SECTIONS if k in out.section_headers_seen]
    if out.section_order != expected:
        out.issues.append(Issue("error", "section_order", None, "sections are not in the order of 4.8.2.1"))
    if out.file_check is None:
        out.issues.append(Issue("error", "end_of_file", None, "no file data check value after End of File:"))
    return out


def read_file(path) -> OutputFile:
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
        out = read_text(text)
        out.issues.insert(0, Issue("error", "encoding", None, "file contains non-ASCII bytes"))
        return out
    return read_text(text)


def to_text(header_lines: list[str], tables: dict[str, list[str]], file_check: str, delimiter: str = "\r", section_headers: dict[str, str] | None = None) -> str:
    """Serialize sections back to the file layout (used by the round-trip test and by the publisher).
    The delimiter separates lines and is not written after the last one: a carriage return at the very
    end of a file is dropped by tools that treat CR LF as a line ending, and the regulation asks for
    the delimiter between lines."""
    parts: list[str] = []
    for text, key in SECTIONS:
        parts.append((section_headers or {}).get(key, text))
        if key == "header":
            parts.extend(header_lines)
        elif key == "end":
            parts.append(file_check)
        else:
            parts.extend(tables.get(key, []))
    return delimiter.join(parts)
