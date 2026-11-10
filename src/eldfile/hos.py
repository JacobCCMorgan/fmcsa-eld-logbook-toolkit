"""Duty-status timeline and hours-of-service rules (49 CFR 395.3 property-carrying, 395.5 passenger-carrying).

The timeline is rebuilt from the active duty-status events (type 1) and the personal-conveyance /
yard-move indications (type 3) of the output file. Personal conveyance counts as off duty, a yard
move as on duty not driving. A special category stays in effect until the next duty-status record or
an explicit "indication cleared" record (type 3 code 0), whichever comes first: the device that
produced the sample file never writes the cleared record, every personal-conveyance period there ends
with the driver's next manual status change. Times are the home-terminal local times the file carries.

Rules implemented (per rule set):

* driving limit after a qualifying off-duty period (11 h after 10 h off / 10 h after 8 h off);
* on-duty limit: property, the 14 consecutive-hour window after coming on duty; passenger, 15 hours
  on duty (cumulative, off-duty time does not count) after 8 consecutive hours off;
* property only: no driving after 8 cumulative hours of driving without an interruption of at least
  30 consecutive minutes (any non-driving status);
* 60/70 hours on duty in 7/8 consecutive days with the 34-hour restart (multiday basis from the file header).

Not implemented: sleeper-berth splits (7/3, 8/2 and the passenger 8-hour split), the 16-hour
short-haul exception, the adverse-conditions extension and the 150 air-mile exemption. A sleeper
berth period is treated as off duty; splits would only make the engine more lenient, so violations
reported here are an upper bound for drivers who use the berth.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd

from .parse import OutputFile

STATUS = {"1": "OFF", "2": "SB", "3": "D", "4": "ON"}
SPECIAL = {"0": "", "1": "PC", "2": "YM"}

RULES = {
    "property": dict(name="395.3 property-carrying", drive_limit=11.0, duty_limit=14.0, duty_mode="window", reset_hours=10.0, break_after_driving=8.0, break_minutes=30),
    "passenger": dict(name="395.5 passenger-carrying", drive_limit=10.0, duty_limit=15.0, duty_mode="cumulative", reset_hours=8.0, break_after_driving=None, break_minutes=None),
}
H = dt.timedelta(hours=1)


@dataclass
class Segment:
    start: dt.datetime
    end: dt.datetime
    status: str      # OFF, SB, D, ON as recorded
    special: str     # "", PC, YM
    seq: str

    @property
    def effective(self) -> str:
        if self.special == "PC":
            return "OFF"
        if self.special == "YM":
            return "ON"
        return self.status

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _stamp(date: str, time: str) -> dt.datetime:
    return dt.datetime.strptime(date + time, "%m%d%y%H%M%S")


def file_time(f: OutputFile) -> dt.datetime | None:
    """The file's own timestamp (header current date and time), used to close the last segment."""
    try:
        return _stamp(f.header.get("current_date", ""), f.header.get("current_time", ""))
    except ValueError:
        return None


def timeline(f: OutputFile, end: dt.datetime | None = None) -> list[Segment]:
    ev = f.table("events")
    ev = ev[(ev["record_status"] == "1") & ev["event_type"].isin(["1", "3"])].copy()
    ev["stamp"] = [_stamp(d, t) for d, t in zip(ev["date"], ev["time"])]
    ev = ev.sort_values(["stamp", "seq"])
    segs: list[Segment] = []
    status, special, start, seq = None, "", None, ""
    for r in ev.itertuples():
        if r.event_type == "1":
            new_status, new_special = STATUS[r.event_code], ""     # a duty-status record ends any special category
        else:
            new_status, new_special = status, SPECIAL.get(r.event_code, "")
            if new_status is None:
                new_status = "OFF"     # special category indicated before any status record: the driver was off duty
        if status is not None and r.stamp > start:
            segs.append(Segment(start, r.stamp, status, special, seq))
        status, special, start, seq = new_status, new_special, r.stamp, r.seq
    if status is not None:
        last = end if end is not None else file_time(f)
        if last is not None and last > start:
            segs.append(Segment(start, last, status, special, seq))
    return segs


def daily_summary(segs: list[Segment], period_start: dt.time = dt.time(0, 0)) -> pd.DataFrame:
    """Hours by effective status per 24-hour period starting at ``period_start``."""
    rows: dict[dt.date, dict] = {}
    for s in segs:
        t = s.start
        while t < s.end:
            day_start = dt.datetime.combine(t.date(), period_start)
            if t < day_start:
                day_start -= dt.timedelta(days=1)
            day_end = day_start + dt.timedelta(days=1)
            piece_end = min(s.end, day_end)
            d = rows.setdefault(day_start.date(), {"OFF": 0.0, "SB": 0.0, "D": 0.0, "ON": 0.0, "PC": 0.0, "YM": 0.0, "driving_segments": 0})
            d[s.effective] += (piece_end - t).total_seconds() / 3600
            if s.special:
                d[s.special] += (piece_end - t).total_seconds() / 3600
            if s.effective == "D" and t == s.start:
                d["driving_segments"] += 1
            t = piece_end
    out = pd.DataFrame([{"day": k, **v} for k, v in sorted(rows.items())])
    if len(out):
        out["on_duty_total"] = out["D"] + out["ON"]
        out["recorded"] = out[["OFF", "SB", "D", "ON"]].sum(axis=1)
    return out


def evaluate(segs: list[Segment], rule_set: str = "property", multiday_basis: int = 8) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run the rules over the timeline. Returns (violations, driving_segments_with_metrics)."""
    R = RULES[rule_set]
    weekly_limit = 70.0 if multiday_basis == 8 else 60.0
    window_days = multiday_basis
    # consecutive effective segments merged
    merged: list[Segment] = []
    for s in segs:
        if merged and merged[-1].effective == s.effective and merged[-1].end == s.start:
            merged[-1] = Segment(merged[-1].start, s.end, merged[-1].status, merged[-1].special, merged[-1].seq)
        else:
            merged.append(Segment(s.start, s.end, s.status, s.special, s.seq))
    violations: list[dict] = []
    metrics: list[dict] = []
    reset_at: dt.datetime | None = merged[0].start if merged else None   # end of the last qualifying off-duty period
    drive_since_reset = 0.0
    duty_since_reset = 0.0        # cumulative on-duty (passenger mode)
    drive_since_break = 0.0
    last_restart: dt.datetime | None = merged[0].start if merged else None  # 34-hour restart
    on_duty_history: list[tuple[dt.datetime, dt.datetime]] = []  # (start, end) of D/ON pieces for the rolling week
    period = 1        # counts qualifying off-duty resets: the daily rules are reported once per duty period
    week = 1          # counts 34-hour restarts: the weekly rule is reported once per restart period

    def rolling_on_duty(until: dt.datetime) -> float:
        lo = max(until - dt.timedelta(days=window_days), last_restart or until)
        total = 0.0
        for a, b in on_duty_history:
            s, e = max(a, lo), min(b, until)
            if e > s:
                total += (e - s).total_seconds() / 3600
        return total

    for s in merged:
        eff = s.effective
        if eff in ("OFF", "SB"):
            if s.hours >= R["reset_hours"]:
                reset_at, drive_since_reset, duty_since_reset, drive_since_break = s.end, 0.0, 0.0, 0.0
                period += 1
            if s.hours >= 34:
                last_restart = s.end
                week += 1
            if R["break_minutes"] and s.hours * 60 >= R["break_minutes"]:
                drive_since_break = 0.0
            continue
        # on duty (D or ON)
        if eff == "ON" and R["break_minutes"] and s.hours * 60 >= R["break_minutes"]:
            drive_since_break = 0.0
        on_duty_history.append((s.start, s.end))
        if eff == "D":
            # evaluate at the end of the driving segment, and where limits are crossed inside it
            drive_before = drive_since_reset
            break_before = drive_since_break
            window_hours_at_end = (s.end - reset_at).total_seconds() / 3600 if reset_at else 0.0
            duty_at_end = duty_since_reset + s.hours if R["duty_mode"] == "cumulative" else window_hours_at_end
            weekly_at_end = rolling_on_duty(s.end)
            m = {"seq": s.seq, "start": s.start, "end": s.end, "hours": round(s.hours, 3), "drive_since_reset": round(drive_before + s.hours, 3),
                 "duty_since_reset": round(duty_at_end, 3), "drive_since_break": round(break_before + s.hours, 3), "weekly_on_duty": round(weekly_at_end, 3)}
            metrics.append(m)
            # driving limit
            if drive_before + s.hours > R["drive_limit"]:
                over_start = s.start + dt.timedelta(hours=max(R["drive_limit"] - drive_before, 0))
                violations.append({"rule": "driving_limit", "limit_hours": R["drive_limit"], "start": over_start, "end": s.end, "excess_hours": round(drive_before + s.hours - R["drive_limit"], 3), "seq": s.seq, "period": period})
            # on-duty limit
            if R["duty_mode"] == "window":
                if window_hours_at_end > R["duty_limit"]:
                    over_start = max(s.start, reset_at + dt.timedelta(hours=R["duty_limit"]))
                    violations.append({"rule": "on_duty_window", "limit_hours": R["duty_limit"], "start": over_start, "end": s.end, "excess_hours": round(window_hours_at_end - R["duty_limit"], 3), "seq": s.seq, "period": period})
            else:
                if duty_at_end > R["duty_limit"]:
                    over_start = s.start + dt.timedelta(hours=max(R["duty_limit"] - duty_since_reset, 0))
                    violations.append({"rule": "on_duty_cumulative", "limit_hours": R["duty_limit"], "start": over_start, "end": s.end, "excess_hours": round(duty_at_end - R["duty_limit"], 3), "seq": s.seq, "period": period})
            # 30-minute break
            if R["break_after_driving"] and break_before + s.hours > R["break_after_driving"]:
                over_start = s.start + dt.timedelta(hours=max(R["break_after_driving"] - break_before, 0))
                violations.append({"rule": "break_30min", "limit_hours": R["break_after_driving"], "start": over_start, "end": s.end, "excess_hours": round(break_before + s.hours - R["break_after_driving"], 3), "seq": s.seq, "period": period})
            # weekly
            if weekly_at_end > weekly_limit:
                over_start = s.end - dt.timedelta(hours=min(weekly_at_end - weekly_limit, s.hours))
                violations.append({"rule": f"{int(weekly_limit)}h_{window_days}day", "limit_hours": weekly_limit, "start": over_start, "end": s.end, "excess_hours": round(weekly_at_end - weekly_limit, 3), "seq": s.seq, "period": -week})
            drive_since_reset += s.hours
            drive_since_break += s.hours
        duty_since_reset += s.hours
    # one row per rule and duty period: later driving segments of the same period only extend the excess
    rows: list[dict] = []
    for x in violations:
        if rows and rows[-1]["rule"] == x["rule"] and rows[-1]["period"] == x["period"]:
            rows[-1]["end"], rows[-1]["excess_hours"] = x["end"], max(rows[-1]["excess_hours"], x["excess_hours"])
        else:
            rows.append(dict(x))
    v = pd.DataFrame(rows, columns=["rule", "limit_hours", "start", "end", "excess_hours", "seq", "period"]).drop(columns="period")
    m = pd.DataFrame(metrics, columns=["seq", "start", "end", "hours", "drive_since_reset", "duty_since_reset", "drive_since_break", "weekly_on_duty"])
    return v, m


def unidentified_driving(f: OutputFile) -> pd.DataFrame:
    """Driving periods of the unidentified driver profile. A driving record runs until the next
    unidentified duty-status record of the same day (normally the automatic on-duty record written
    when the vehicle stops) or until the file's driver logs in again, whichever comes first: in the
    sample file three unidentified driving records start in the same second as a logout of the driver
    and end when the driver logs back in. Miles come from the accumulated-miles counter when the next
    record is an unidentified record and the counter did not reset in between."""
    ud = f.table("unidentified")
    if not len(ud):
        return pd.DataFrame(columns=["seq", "start", "end", "hours", "miles", "closed", "closed_by"])
    ud = ud[(ud["record_status"] == "1") & (ud["event_type"] == "1")].copy()
    ud["stamp"] = [_stamp(d, t) for d, t in zip(ud["date"], ud["time"])]
    ud = ud.sort_values(["stamp", "seq"]).reset_index(drop=True)
    lg = f.table("logins")
    logins = sorted(_stamp(d, t) for c, d, t in zip(lg["event_code"], lg["date"], lg["time"]) if c == "1") if len(lg) else []
    rows = []
    for i, r in ud.iterrows():
        if r.event_code != "3":
            continue
        nxt = ud.iloc[i + 1] if i + 1 < len(ud) else None
        end, closed_by, miles = r.stamp, "", None
        if nxt is not None and nxt.stamp.date() == r.stamp.date():
            end, closed_by = nxt.stamp, "unidentified record"
            a, b = float(r.accum_miles or 0), float(nxt.accum_miles or 0)
            miles = b - a if b >= a else None
        login = next((t for t in logins if r.stamp < t <= end or (closed_by == "" and t > r.stamp and t.date() == r.stamp.date())), None)
        if login is not None and (closed_by == "" or login < end):
            end, closed_by, miles = login, "driver login", None
        rows.append({"seq": r.seq, "start": r.stamp, "end": end, "hours": round((end - r.stamp).total_seconds() / 3600, 3), "miles": miles, "closed": bool(closed_by), "closed_by": closed_by})
    return pd.DataFrame(rows, columns=["seq", "start", "end", "hours", "miles", "closed", "closed_by"])


def unidentified_summary(f: OutputFile) -> dict:
    d = unidentified_driving(f)
    ud = f.table("unidentified")
    active = ud[ud["record_status"] == "1"] if len(ud) else ud
    days = {_stamp(a, b).date() for a, b in zip(active["date"], active["time"])} if len(active) else set()
    return {"records": int(len(active)), "driving_records": int(len(d)), "driving_hours": round(float(d.loc[d["closed"], "hours"].sum()), 2) if len(d) else 0.0,
            "driving_miles": round(float(d["miles"].dropna().sum()), 1) if len(d) else 0.0, "open_driving_records": int((~d["closed"]).sum()) if len(d) else 0, "days": len(days)}
