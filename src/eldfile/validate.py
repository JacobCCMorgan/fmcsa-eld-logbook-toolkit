"""Validate a parsed output file: data checks, value domains, cross references, sequence and chronology.

Dates in the file are MMDDYY and times HHMMSS (Appendix A section 7; the handbook example 051217 is 12 May 2017).

Each finding is a row: level (error / warning / info), code, section, line number, message. The
domains follow Appendix A section 7 and the tables of the ICD development handbook (event types and
codes, record status and origin, malfunction and diagnostic codes).
"""
from __future__ import annotations

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field

import pandas as pd

from . import checks
from .parse import OutputFile

EVENT_CODES = {
    "1": {"1": "off_duty", "2": "sleeper_berth", "3": "driving", "4": "on_duty_not_driving"},
    "2": {"1": "intermediate_conventional", "2": "intermediate_reduced"},
    "3": {"0": "special_cleared", "1": "personal_conveyance", "2": "yard_move"},
    "4": {str(i): f"certification_{i}" for i in range(1, 10)},
    "5": {"1": "login", "2": "logout"},
    "6": {"1": "power_up_conventional", "2": "power_up_reduced", "3": "shut_down_conventional", "4": "shut_down_reduced"},
    "7": {"1": "malfunction_logged", "2": "malfunction_cleared", "3": "diagnostic_logged", "4": "diagnostic_cleared"},
}
RECORD_STATUS = {"1": "active", "2": "inactive_changed", "3": "inactive_change_requested", "4": "inactive_change_rejected"}
RECORD_ORIGIN = {"1": "eld", "2": "driver", "3": "other_user", "4": "unidentified_driver"}
MAL_DIAG_CODES = {"P": "power_compliance", "E": "engine_synchronization", "T": "timing", "L": "positioning", "R": "data_recording", "S": "data_transfer",
                  "1": "power_diagnostic", "2": "engine_synchronization_diagnostic", "3": "missing_required_elements", "4": "data_transfer_diagnostic", "5": "unidentified_driving", "6": "other"}


@dataclass
class Finding:
    level: str
    code: str
    section: str
    line_no: int | None
    message: str


@dataclass
class Validation:
    findings: list[Finding] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def add(self, level, code, section, line_no, message):
        self.findings.append(Finding(level, code, section, None if line_no is None else int(line_no), message))

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame([f.__dict__ for f in self.findings], columns=["level", "code", "section", "line_no", "message"])

    @property
    def errors(self) -> int:
        return sum(1 for f in self.findings if f.level == "error")


def _date(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%m%d%y").date()
    except ValueError:
        return None


def _time(s: str) -> dt.time | None:
    try:
        return dt.datetime.strptime(s, "%H%M%S").time()
    except ValueError:
        return None


def _is_coord(v: str, lo: float, hi: float) -> bool:
    if v in ("X", "M", "E"):
        return True
    try:
        x = float(v)
    except ValueError:
        return False
    return lo <= x <= hi and len(v.split(".")[-1]) in (1, 2) if "." in v else False


def _hex(v: str, max_len: int = 4) -> bool:
    try:
        int(v, 16)
    except ValueError:
        return False
    return 1 <= len(v) <= max_len


def validate(f: OutputFile, ud_username: str | None = None) -> Validation:
    v = Validation()
    for i in f.issues:
        v.add(i.level, i.code, "structure", i.line_no, i.message)

    # 1. data checks
    bad_lines = [no for no, _, text in f.data_lines if not checks.verify_line(text)]
    for no in bad_lines:
        v.add("error", "line_check", "checks", no, "line data check value does not match")
    expected_file = checks.file_check([checks.split_check(t)[1] for _, _, t in f.data_lines])
    if f.file_check is not None and expected_file != f.file_check:
        v.add("error", "file_check", "checks", None, f"file data check {f.file_check} does not match the lines ({expected_file})")
    unit = f.table("cmvs").set_index("cmv_order")["power_unit"].to_dict() if len(f.table("cmvs")) else {}
    ev = f.table("events")
    bad_ev = 0
    for r in ev.itertuples():
        exp = checks.event_check(r.event_type, r.event_code, r.date, r.time, r.accum_miles, r.elapsed_hours, r.lat, r.lon, unit.get(r.cmv_order, f.power_unit), f.username)
        if exp != r.event_check:
            bad_ev += 1
            v.add("error", "event_check", "events", r.line_no, f"event data check {r.event_check} does not match ({exp})")
    ud = f.table("unidentified")
    ud_result = "no records"
    if len(ud):
        need = Counter()
        for r in ud.itertuples():
            base = sum(checks.mapped_sum(str(x)) for x in (r.event_type, r.event_code, r.date, r.time, r.accum_miles, r.elapsed_hours, r.lat, r.lon, unit.get(r.cmv_order, f.power_unit)))
            target = ((int(r.event_check, 16) ^ checks.EVENT_XOR) >> 3 | (int(r.event_check, 16) ^ checks.EVENT_XOR) << 5) & 0xFF
            need[(target - base) % 256] += 1
        if ud_username is not None:
            bad_ud = sum(1 for r in ud.itertuples() if checks.event_check(r.event_type, r.event_code, r.date, r.time, r.accum_miles, r.elapsed_hours, r.lat, r.lon, unit.get(r.cmv_order, f.power_unit), ud_username) != r.event_check)
            ud_result = f"{len(ud) - bad_ud}/{len(ud)} verify with username {ud_username!r}"
            if bad_ud:
                v.add("error", "event_check", "unidentified", None, f"{bad_ud} unidentified-driver event data checks do not match with username {ud_username!r}")
        else:
            const, n = need.most_common(1)[0]
            ud_result = f"username not in file; {n}/{len(ud)} records agree on one username contribution ({const})"
            if n != len(ud):
                v.add("warning", "event_check", "unidentified", None, f"unidentified-driver event data checks are not consistent with a single username ({n}/{len(ud)})")
            else:
                v.add("info", "event_check", "unidentified", None, ud_result)

    # 2. header domains
    h = f.header
    if h.get("multiday_basis") not in ("7", "8"):
        v.add("error", "domain", "header", None, f"multiday basis {h.get('multiday_basis')!r} is not 7 or 8")
    if _time(h.get("period_start", "")) is None:
        v.add("error", "domain", "header", None, "24-hour period starting time is not HHMMSS")
    off = h.get("utc_offset", "")
    if not (off.isdigit() and 4 <= int(off) <= 11 and len(off) == 2):
        v.add("error", "domain", "header", None, f"time zone offset {off!r} is not a two-digit value between 04 and 11")
    if h.get("exempt_driver") not in ("E", "0"):
        v.add("error", "domain", "header", None, f"exempt driver configuration {h.get('exempt_driver')!r} is not E or 0")
    for k, (lo, hi) in {"current_lat": (-90, 90), "current_lon": (-179.99, 180)}.items():
        if not _is_coord(h.get(k, ""), lo, hi):
            v.add("error", "domain", "header", None, f"{k} {h.get(k)!r} out of range or precision")
    if _date(h.get("current_date", "")) is None or _time(h.get("current_time", "")) is None:
        v.add("error", "domain", "header", None, "current date/time is not MMDDYY/HHMMSS")
    if not (h.get("usdot", "").isdigit() and 1 <= len(h["usdot"]) <= 9):
        v.add("error", "domain", "header", None, "USDOT number is not a 1-9 digit integer")
    if len(h.get("vin", "")) not in (17, 18):
        v.add("error", "domain", "header", None, f"VIN length {len(h.get('vin', ''))}")
    if not (1 <= len(h.get("power_unit", "")) <= 10):
        v.add("error", "domain", "header", None, "power unit number length")
    if not (4 <= len(h.get("username", "")) <= 60):
        v.add("error", "domain", "header", None, "driver username length")
    if h.get("eld_registration_id", "") and len(h["eld_registration_id"]) != 4:
        v.add("error", "domain", "header", None, "ELD registration id is not 4 characters")
    if len(h.get("eld_authentication", "")) < 16:
        v.add("error", "domain", "header", None, "ELD authentication value shorter than 16 characters")

    # 3. list domains and cross references
    users = set(f.table("users")["user_order"])
    cmvs = set(f.table("cmvs")["cmv_order"])
    for r in f.table("users").itertuples():
        if r.account_type not in ("D", "S"):
            v.add("error", "domain", "users", r.line_no, f"account type {r.account_type!r}")
    for r in f.table("cmvs").itertuples():
        if len(r.vin) not in (17, 18):
            v.add("error", "domain", "cmvs", r.line_no, f"VIN length {len(r.vin)}")

    def check_event_rows(table: pd.DataFrame, section: str, allowed_types: set[str], has_user: bool):
        for r in table.itertuples():
            if not _hex(r.seq):
                v.add("error", "domain", section, r.line_no, f"sequence id {r.seq!r} is not 1-4 hex digits")
            if r.record_status not in RECORD_STATUS:
                v.add("error", "domain", section, r.line_no, f"record status {r.record_status!r}")
            if r.record_origin not in RECORD_ORIGIN:
                v.add("error", "domain", section, r.line_no, f"record origin {r.record_origin!r}")
            if r.event_type not in allowed_types:
                v.add("error", "domain", section, r.line_no, f"event type {r.event_type!r} not allowed in this section")
            elif r.event_code not in EVENT_CODES.get(r.event_type, {}):
                v.add("error", "domain", section, r.line_no, f"event code {r.event_code!r} for type {r.event_type}")
            if _date(r.date) is None or _time(r.time) is None:
                v.add("error", "domain", section, r.line_no, "date/time format")
            if not (r.accum_miles.isdigit() and 0 <= int(r.accum_miles) <= 9999):
                v.add("error", "domain", section, r.line_no, f"accumulated vehicle miles {r.accum_miles!r}")
            try:
                hrs = float(r.elapsed_hours)
                if not (0 <= hrs <= 99.9) or "." not in r.elapsed_hours:
                    raise ValueError
            except ValueError:
                v.add("error", "domain", section, r.line_no, f"elapsed engine hours {r.elapsed_hours!r}")
            if not _is_coord(r.lat, -90, 90) or not _is_coord(r.lon, -179.99, 180):
                v.add("error", "domain", section, r.line_no, f"coordinates {r.lat},{r.lon}")
            if not (r.distance_since_valid.isdigit() and 0 <= int(r.distance_since_valid) <= 6):
                v.add("error", "domain", section, r.line_no, f"distance since last valid coordinates {r.distance_since_valid!r}")
            if r.cmv_order not in cmvs:
                v.add("error", "xref", section, r.line_no, f"CMV order {r.cmv_order!r} not in CMV list")
            if has_user and r.user_order not in users:
                v.add("error", "xref", section, r.line_no, f"user order {r.user_order!r} not in user list")
            if r.malfunction_ind not in ("0", "1") or (has_user and r.diagnostic_ind not in ("0", "1")):
                v.add("error", "domain", section, r.line_no, "indicator flags must be 0 or 1")

    check_event_rows(ev, "events", {"1", "2", "3"}, True)
    check_event_rows(ud, "unidentified", {"1", "2", "3"}, False)
    for r in f.table("certifications").itertuples():
        if r.event_code not in EVENT_CODES["4"]:
            v.add("error", "domain", "certifications", r.line_no, f"certification code {r.event_code!r}")
        if _date(r.certified_date) is None:
            v.add("error", "domain", "certifications", r.line_no, "certified date format")
        if r.cmv_order not in cmvs:
            v.add("error", "xref", "certifications", r.line_no, f"CMV order {r.cmv_order!r} not in CMV list")
    for r in f.table("malfunctions").itertuples():
        if r.event_code not in EVENT_CODES["7"]:
            v.add("error", "domain", "malfunctions", r.line_no, f"malfunction/diagnostic event code {r.event_code!r}")
        if r.mal_diag_code not in MAL_DIAG_CODES:
            v.add("error", "domain", "malfunctions", r.line_no, f"malfunction/diagnostic code {r.mal_diag_code!r}")
        elif (r.event_code in ("1", "2") and r.mal_diag_code.isdigit()) or (r.event_code in ("3", "4") and r.mal_diag_code.isalpha()):
            v.add("error", "domain", "malfunctions", r.line_no, f"code {r.mal_diag_code} does not belong to event code {r.event_code} (letters are malfunctions, digits are diagnostics)")
    for r in f.table("logins").itertuples():
        if r.event_code not in EVENT_CODES["5"]:
            v.add("error", "domain", "logins", r.line_no, f"login/logout code {r.event_code!r}")
    for r in f.table("power").itertuples():
        if r.event_code not in EVENT_CODES["6"]:
            v.add("error", "domain", "power", r.line_no, f"power-up/shut-down code {r.event_code!r}")
        if len(r.vin) not in (17, 18):
            v.add("error", "domain", "power", r.line_no, f"VIN length {len(r.vin)}")

    # 4. sequence ids: unique across the file, and the event list newest first
    all_seq = []
    for key in ("events", "certifications", "malfunctions", "logins", "power", "unidentified"):
        t = f.table(key)
        all_seq += [(s, key, no) for s, no in zip(t["seq"], t["line_no"])]
    dup = {s for s, c in Counter(s for s, _, _ in all_seq).items() if c > 1}
    for s in sorted(dup):
        v.add("error", "sequence", "all", None, f"sequence id {s} appears more than once")
    if len(ev) > 1:
        seqs = [int(s, 16) for s in ev["seq"]]
        out_of_seq = sum(1 for a, b in zip(seqs, seqs[1:]) if a <= b)
        if out_of_seq:
            v.add("info", "order", "events", None, f"{out_of_seq} consecutive event pairs are not in descending sequence order (edited records keep their time position but carry a newer sequence id)")
        stamps = [dt.datetime.combine(_date(d) or dt.date(2000, 1, 1), _time(t) or dt.time()) for d, t in zip(ev["date"], ev["time"])]
        back = sum(1 for a, b in zip(stamps, stamps[1:]) if a < b)
        if back:
            v.add("warning", "order", "events", None, f"{back} consecutive event pairs are not in descending time order")
    nums = sorted(int(s, 16) for s, _, _ in all_seq)
    gaps = sum(b - a - 1 for a, b in zip(nums, nums[1:]) if b - a > 1)
    v.summary.update({
        "data_lines": len(f.data_lines), "line_checks_failed": len(bad_lines), "file_check_ok": f.file_check == expected_file,
        "driver_event_checks_failed": bad_ev, "unidentified_event_checks": ud_result,
        "sequence_ids": len(all_seq), "sequence_duplicates": len(dup), "sequence_gaps": gaps, "sequence_min": f"{nums[0]:04X}" if nums else None, "sequence_max": f"{nums[-1]:04X}" if nums else None,
        "errors": v.errors, "warnings": sum(1 for x in v.findings if x.level == "warning"),
    })
    return v
