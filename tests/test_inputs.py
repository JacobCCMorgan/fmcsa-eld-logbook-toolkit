"""Invariants of the published inputs: they verify, and they carry no raw identifiers."""
import csv
import re
from pathlib import Path

from eldfile import checks, parse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "data" / "input" / "driver_output_file.csv"
EVENTS = ROOT / "data" / "input" / "driver_events_export.csv"


def test_published_output_file_verifies_completely():
    f = parse.read_file(OUTPUT_FILE)
    assert f.delimiter == "\r"
    assert not [i for i in f.issues if i.level == "error"]
    assert all(checks.verify_line(t) for _, _, t in f.data_lines)
    assert checks.file_check([checks.split_check(t)[1] for _, _, t in f.data_lines]) == f.file_check
    ev = f.table("events")
    unit = f.table("cmvs").set_index("cmv_order")["power_unit"]
    recomputed = [checks.event_check(r.event_type, r.event_code, r.date, r.time, r.accum_miles, r.elapsed_hours, r.lat, r.lon, unit[r.cmv_order], f.username) for r in ev.itertuples()]
    assert recomputed == ev["event_check"].tolist()


def test_published_inputs_are_pseudonymized():
    text = OUTPUT_FILE.read_bytes().decode("ascii") + EVENTS.read_text(encoding="ascii")
    assert "@" not in text
    assert not re.search(r"[^\x00-\x7f]", text)
    f = parse.read_file(OUTPUT_FILE)
    assert f.header["last_name"].startswith("DRV") and f.header["username"].startswith("drv_")
    assert f.header["carrier_name"].startswith("ORG-") and f.header["eld_registration_id"] == "DEID"
    assert re.fullmatch(r"VIN[0-9A-F]{14}", f.header["vin"]) and re.fullmatch(r"PU[0-9A-F]{6}", f.header["power_unit"])
    for col in ("lat", "lon"):
        vals = f.table("events")[col].astype(float)
        assert ((vals * 20).round() - vals * 20).abs().max() < 1e-6  # 0.05-degree grid


def test_events_export_matches_output_file_sequence_space():
    with open(EVENTS, newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 790 and set(rows[0]) == {"date", "time", "state", "odometer", "engine_hours", "event_label", "origin", "seq", "vin", "power_unit"}
    assert {r["state"] for r in rows} <= {"CA", "NV", ""}
    f = parse.read_file(OUTPUT_FILE)
    seqs = set(f.table("events")["seq"]) | set(f.table("unidentified")["seq"]) | set(f.table("logins")["seq"]) | set(f.table("power")["seq"]) | set(f.table("certifications")["seq"]) | set(f.table("malfunctions")["seq"])
    assert {r["seq"] for r in rows} == seqs
