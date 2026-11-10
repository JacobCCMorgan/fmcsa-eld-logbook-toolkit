"""Hours-of-service rules on hand-built timelines, then on the published month."""
import datetime as dt
from pathlib import Path

import pytest

from eldfile import hos, parse

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = ROOT / "data" / "input" / "driver_output_file.csv"
D0 = dt.datetime(2026, 3, 2, 0, 0)   # a Monday


def T(day: int, hh: int, mm: int = 0) -> dt.datetime:
    return D0 + dt.timedelta(days=day, hours=hh, minutes=mm)


def build(spec):
    """spec: list of (day, hh, mm, status[, special]) status-change points, closed by an OFF point."""
    segs = []
    for i in range(len(spec) - 1):
        a, b = spec[i], spec[i + 1]
        segs.append(hos.Segment(T(a[0], a[1], a[2]), T(b[0], b[1], b[2]), a[3], a[4] if len(a) > 4 else "", f"S{i:03d}"))
    return segs


def rules(v):
    return sorted(v["rule"].tolist())


def test_clean_property_day_has_no_violation():
    segs = build([(0, 6, 0, "ON"), (0, 6, 30, "D"), (0, 11, 0, "ON"), (0, 11, 45, "D"), (0, 16, 0, "ON"), (0, 16, 30, "OFF"), (1, 6, 0, "OFF")])
    v, m = hos.evaluate(segs, "property")
    assert len(v) == 0
    assert m["drive_since_reset"].max() == pytest.approx(8.75)    # 4.5 + 4.25


def test_eleven_hour_driving_limit():
    segs = build([(0, 6, 0, "ON"), (0, 6, 30, "D"), (0, 10, 30, "ON"), (0, 11, 15, "D"), (0, 18, 45, "OFF"), (1, 6, 0, "OFF")])  # 4.0 + 7.5 = 11.5 h
    v, _ = hos.evaluate(segs, "property")
    assert rules(v) == ["driving_limit"]
    r = v.iloc[0]
    assert r["excess_hours"] == pytest.approx(0.5)
    assert r["start"] == T(0, 18, 15)      # the eleventh hour is reached 30 minutes before the segment ends


def test_fourteen_hour_window_counts_breaks():
    segs = build([(0, 6, 0, "ON"), (0, 6, 30, "D"), (0, 11, 0, "ON"), (0, 15, 0, "D"), (0, 20, 30, "OFF"), (1, 6, 0, "OFF")])  # window 06:00-20:30 = 14.5 h, driving 10 h
    v, _ = hos.evaluate(segs, "property")
    assert rules(v) == ["on_duty_window"]
    r = v.iloc[0]
    assert r["start"] == T(0, 20, 0) and r["excess_hours"] == pytest.approx(0.5)


def test_thirty_minute_break_rule():
    segs = build([(0, 8, 0, "D"), (0, 12, 30, "ON"), (0, 12, 50, "D"), (0, 17, 0, "OFF"), (1, 6, 0, "OFF")])  # 4.5 h + 20 min stop + 4.167 h
    v, _ = hos.evaluate(segs, "property")
    assert rules(v) == ["break_30min"]
    r = v.iloc[0]
    assert r["start"] == T(0, 16, 20) and r["excess_hours"] == pytest.approx(4 + 10 / 60 + 4.5 - 8, abs=1e-3)
    # the same day with a 30-minute stop is clean
    segs = build([(0, 8, 0, "D"), (0, 12, 30, "ON"), (0, 13, 0, "D"), (0, 17, 0, "OFF"), (1, 6, 0, "OFF")])
    v, _ = hos.evaluate(segs, "property")
    assert len(v) == 0


def test_passenger_rules():
    # 10.5 h driving: over the 10-hour limit; 14 h on duty: under 15; no break rule
    segs = build([(0, 6, 0, "ON"), (0, 6, 30, "D"), (0, 12, 0, "ON"), (0, 15, 0, "D"), (0, 20, 0, "OFF"), (1, 6, 0, "OFF")])
    v, _ = hos.evaluate(segs, "passenger")
    assert rules(v) == ["driving_limit"] and v.iloc[0]["excess_hours"] == pytest.approx(0.5)
    assert len(hos.evaluate(segs, "property")[0]) == 0        # 10.5 h driving and a 14 h window are legal for property carriers
    # 15.5 cumulative on-duty hours (9.5 driving) with a two-hour off-duty gap that does not count
    segs = build([(0, 5, 0, "ON"), (0, 6, 0, "D"), (0, 9, 0, "OFF"), (0, 11, 0, "ON"), (0, 16, 0, "D"), (0, 22, 30, "OFF"), (1, 6, 0, "OFF")])
    v, _ = hos.evaluate(segs, "passenger")
    assert rules(v) == ["on_duty_cumulative"]
    assert v.iloc[0]["excess_hours"] == pytest.approx(0.5) and v.iloc[0]["start"] == T(0, 22, 0)
    assert rules(hos.evaluate(segs, "property")[0]) == ["on_duty_window"]      # 05:00-22:30 is a 17.5-hour window


def test_personal_conveyance_is_off_duty_and_yard_move_is_on_duty():
    segs = build([(0, 5, 0, "OFF", "PC"), (0, 6, 0, "ON"), (0, 6, 30, "D"), (0, 11, 0, "ON"), (0, 11, 30, "D"), (0, 17, 30, "ON", "YM"), (0, 18, 30, "OFF", "PC"), (0, 19, 30, "OFF"), (1, 6, 0, "OFF")])
    day = hos.daily_summary(segs).iloc[0]
    assert day["OFF"] == pytest.approx(1 + 1 + 4.5) and day["D"] == pytest.approx(10.5) and day["ON"] == pytest.approx(2.0)
    assert day["PC"] == pytest.approx(2) and day["YM"] == pytest.approx(1)
    v, _ = hos.evaluate(segs, "property")
    assert len(v) == 0
    # the same 10.5 hours driven under a personal-conveyance flag never count
    segs = build([(0, 6, 0, "ON"), (0, 6, 30, "D", "PC"), (0, 17, 0, "OFF"), (1, 6, 0, "OFF")])
    assert len(hos.evaluate(segs, "passenger")[0]) == 0


def test_weekly_limits_and_restart():
    week = []
    for d in range(8):
        week += [(d, 6, 0, "ON"), (d, 6, 30, "D"), (d, 11, 0, "ON"), (d, 11, 45, "D"), (d, 15, 30, "OFF")]   # 9.5 h on duty, 8.25 h driving per day
    week.append((8, 6, 0, "OFF"))
    segs = build(week)
    v, m = hos.evaluate(segs, "property", multiday_basis=8)
    assert rules(v) == ["70h_8day"]                       # 8 x 9.5 = 76 h in 8 days; the limit is crossed 1.5 h into day 8's first drive
    assert v.iloc[0]["excess_hours"] == pytest.approx(6.0) and v.iloc[0]["start"] == T(7, 9, 30) and v.iloc[0]["end"] == T(7, 15, 30)
    v, _ = hos.evaluate(segs, "property", multiday_basis=7)
    assert rules(v) == ["60h_7day"]                       # days 1-7 = 66.5 h, days 2-8 = 66.5 h: one continuing violation from day 7
    assert v.iloc[0]["excess_hours"] == pytest.approx(6.5) and v.iloc[0]["start"] == T(6, 9, 0) and v.iloc[0]["end"] == T(7, 15, 30)
    # a 36-hour break between days 4 and 6 restarts the count
    week = []
    for d in [0, 1, 2, 3, 5, 6, 7]:
        week += [(d, 6, 0, "ON"), (d, 6, 30, "D"), (d, 11, 0, "ON"), (d, 11, 45, "D"), (d, 15, 30, "OFF")]
    week.append((8, 6, 0, "OFF"))
    v, m = hos.evaluate(build(week), "property", multiday_basis=7)
    assert len(v) == 0 and m["weekly_on_duty"].max() == pytest.approx(38.0)   # 4 x 9.5 before the restart, 3 x 9.5 after


def test_ten_hour_reset_needs_ten_consecutive_hours():
    # 9 hours off between two 8-hour driving days: no reset, 16 hours of driving since the last one
    segs = build([(0, 6, 0, "D"), (0, 14, 0, "OFF"), (0, 23, 0, "D"), (1, 7, 0, "OFF"), (2, 6, 0, "OFF")])
    v, _ = hos.evaluate(segs, "property")
    assert "driving_limit" in rules(v)
    segs = build([(0, 6, 0, "D"), (0, 14, 0, "OFF"), (1, 0, 0, "D"), (1, 8, 0, "OFF"), (2, 6, 0, "OFF")])
    v, _ = hos.evaluate(segs, "property")
    assert "driving_limit" not in rules(v)


def test_published_month():
    f = parse.read_file(OUTPUT_FILE)
    segs = hos.timeline(f)
    assert segs[0].special == "PC" and segs[0].effective == "OFF"
    assert all(s.special == "" or s.end == segs[i + 1].start for i, s in enumerate(segs[:-1]))
    assert segs[-1].end == dt.datetime(2026, 10, 1, 15, 45, 5)      # closed at the file's own timestamp
    day = hos.daily_summary(segs)
    worked = day[day["D"] > 0]
    assert len(worked) == 16
    assert worked["D"].max() < 10.0 and worked["D"].min() > 9.5
    assert worked["on_duty_total"].max() < 12.5
    v, m = hos.evaluate(segs, "property", int(f.header["multiday_basis"]))
    assert v["rule"].tolist() == ["break_30min"] and v.iloc[0]["seq"] == "09E7" and v.iloc[0]["start"].date() == dt.date(2026, 9, 4)
    assert v.iloc[0]["excess_hours"] == pytest.approx(1.878, abs=1e-3)
    v, m = hos.evaluate(segs, "passenger", int(f.header["multiday_basis"]))
    assert len(v) == 0
    assert m["drive_since_reset"].max() == pytest.approx(9.987, abs=1e-3)
    ud = hos.unidentified_summary(f)
    assert ud["records"] == 83 and ud["driving_records"] == 27 and ud["days"] == 11 and ud["open_driving_records"] == 0
    assert ud["driving_hours"] == pytest.approx(52.04, abs=0.01) and ud["driving_miles"] == 1424
    d = hos.unidentified_driving(f)
    assert (d["closed_by"] == "driver login").sum() == 3
