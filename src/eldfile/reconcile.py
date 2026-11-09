"""Reconcile the output file against the carrier portal's event export (the "eRODS" view).

Both views come from the same device records, so every sequence id should appear in both, carry the
same timestamp, the same vehicle and the same kind of event, and the totals (odometer, engine hours)
printed by the portal should agree with the totals in the records that carry them. Differences are
reported per sequence id with the field that disagrees.
"""
from __future__ import annotations

import datetime as dt

import pandas as pd

from .parse import OutputFile

# portal label -> (section, event_type, event_code); suffixes handled separately
LABELS = {
    "Off-duty": ("events", "1", "1"), "Sleeper berth": ("events", "1", "2"), "Driving": ("events", "1", "3"), "On-duty, not driving": ("events", "1", "4"),
    "Intermediate log": ("events", "2", "1"), "Intermediate log (reduced precision)": ("events", "2", "2"),
    "PC": ("events", "3", "1"), "YM": ("events", "3", "2"), "Special cleared": ("events", "3", "0"),
    "Login": ("logins", "5", "1"), "Logout": ("logins", "5", "2"),
    "Power-up": ("power", "6", "1"), "Power-up (reduced precision)": ("power", "6", "2"), "Shut-down": ("power", "6", "3"), "Shut-down (reduced precision)": ("power", "6", "4"),
    "Malfunction logged": ("malfunctions", "7", "1"), "Malfunction cleared": ("malfunctions", "7", "2"), "Diagnostic logged": ("malfunctions", "7", "3"), "Diagnostic cleared": ("malfunctions", "7", "4"),
}
STATUS_SUFFIX = {" (Inactive - Changed)": "2", " (Inactive - Change Requested)": "3", " (Inactive - Change Rejected)": "4"}


def parse_label(label: str) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (section, event_type, event_code, record_status) for a portal label."""
    status = "1"
    unidentified = False
    base = label.strip()
    for suffix, code in STATUS_SUFFIX.items():
        if base.endswith(suffix):
            status = code
            base = base[: -len(suffix)]
    if base.endswith(" (Unidentified)"):
        unidentified = True
        base = base[: -len(" (Unidentified)")]
    if base.startswith("First certification") or base.startswith("Certification"):
        return "certifications", "4", None, status
    if base not in LABELS:
        return None, None, None, status
    section, t, c = LABELS[base]
    if unidentified:
        section = "unidentified"
    return section, t, c, status


def _stamp_file(date: str, time: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(date + time, "%m%d%y%H%M%S")
    except ValueError:
        return None


def _stamp_export(date: str, time: str) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(date + " " + time, "%m/%d/%Y %H:%M:%S")
    except ValueError:
        return None


def file_records(f: OutputFile) -> pd.DataFrame:
    """Every sequenced record of the file in one frame with common columns."""
    frames = []
    for key in ("events", "unidentified", "certifications", "malfunctions", "logins", "power"):
        t = f.table(key).copy()
        if not len(t):
            continue
        t["section"] = key
        if key == "certifications":
            t["event_type"] = "4"
        elif key == "malfunctions":
            t["event_type"] = "7"
        elif key == "logins":
            t["event_type"] = "5"
        elif key == "power":
            t["event_type"] = "6"
        if "record_status" not in t:
            t["record_status"] = "1"
        for col in ("total_miles", "total_hours", "vin", "power_unit"):
            if col not in t:
                t[col] = None
        frames.append(t[["seq", "section", "event_type", "event_code", "record_status", "date", "time", "total_miles", "total_hours", "vin", "power_unit", "line_no"]])
    out = pd.concat(frames, ignore_index=True)
    out["stamp"] = [_stamp_file(d, t) for d, t in zip(out["date"], out["time"])]
    unit_vin = f.table("cmvs").set_index("power_unit")["vin"].to_dict()
    out["vin"] = out["vin"].where(out["vin"].notna(), f.header.get("vin"))
    out["power_unit"] = out["power_unit"].where(out["power_unit"].notna(), f.header.get("power_unit"))
    out["vin"] = [v if v else unit_vin.get(u, v) for v, u in zip(out["vin"], out["power_unit"])]
    return out


def reconcile(f: OutputFile, export: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Return (differences, summary). ``export`` has the columns of data/input/driver_events_export.csv."""
    rec = file_records(f).set_index("seq")
    ex = export.copy().fillna("")
    ex["seq"] = ex["seq"].astype(str).str.upper()
    parsed = [parse_label(x) for x in ex["event_label"]]
    ex["x_section"] = [p[0] or "" for p in parsed]
    ex["x_type"] = [p[1] or "" for p in parsed]
    ex["x_code"] = [p[2] or "" for p in parsed]
    ex["x_status"] = [p[3] or "" for p in parsed]
    ex["x_stamp"] = [_stamp_export(d, t) for d, t in zip(ex["date"], ex["time"])]
    ex = ex.set_index("seq")
    diffs = []
    only_export = sorted(set(ex.index) - set(rec.index))
    only_file = sorted(set(rec.index) - set(ex.index))
    for s in only_export:
        diffs.append({"seq": s, "field": "presence", "file": None, "export": ex.loc[s, "event_label"], "note": "in export only"})
    for s in only_file:
        diffs.append({"seq": s, "field": "presence", "file": rec.loc[s, "section"], "export": None, "note": "in file only"})
    both = sorted(set(ex.index) & set(rec.index))
    n_total_checked = 0
    for s in both:
        r, x = rec.loc[s], ex.loc[s]
        if not x["x_section"]:
            diffs.append({"seq": s, "field": "label", "file": r["section"], "export": x["event_label"], "note": "label not understood"})
            continue
        if x["x_section"] != r["section"]:
            diffs.append({"seq": s, "field": "section", "file": r["section"], "export": x["x_section"], "note": x["event_label"]})
        if x["x_type"] != r["event_type"] or (x["x_code"] and x["x_code"] != r["event_code"]):
            diffs.append({"seq": s, "field": "event", "file": f"{r['event_type']}/{r['event_code']}", "export": f"{x['x_type']}/{x['x_code']}", "note": x["event_label"]})
        if x["x_status"] != r["record_status"]:
            diffs.append({"seq": s, "field": "record_status", "file": r["record_status"], "export": x["x_status"], "note": x["event_label"]})
        if r["stamp"] != x["x_stamp"]:
            diffs.append({"seq": s, "field": "timestamp", "file": r["stamp"], "export": x["x_stamp"], "note": ""})
        if r["vin"] and x["vin"] and r["vin"] != x["vin"]:
            diffs.append({"seq": s, "field": "vin", "file": r["vin"], "export": x["vin"], "note": ""})
        if r["power_unit"] and x["power_unit"] and r["power_unit"] != x["power_unit"]:
            diffs.append({"seq": s, "field": "power_unit", "file": r["power_unit"], "export": x["power_unit"], "note": ""})
        if r["total_miles"] is not None and pd.notna(r["total_miles"]) and str(x["odometer"]).strip():
            n_total_checked += 1
            if int(float(r["total_miles"])) != int(float(x["odometer"])):
                diffs.append({"seq": s, "field": "odometer", "file": r["total_miles"], "export": x["odometer"], "note": r["section"]})
            if abs(float(r["total_hours"]) - float(x["engine_hours"])) > 0.05:
                diffs.append({"seq": s, "field": "engine_hours", "file": r["total_hours"], "export": x["engine_hours"], "note": r["section"]})
    d = pd.DataFrame(diffs, columns=["seq", "field", "file", "export", "note"])
    summary = {"file_records": int(len(rec)), "export_rows": int(len(ex)), "matched": len(both), "only_in_export": len(only_export), "only_in_file": len(only_file),
               "records_with_totals_compared": n_total_checked, "differences": int(len(d)), "differences_by_field": d["field"].value_counts().to_dict() if len(d) else {}}
    return d, summary
