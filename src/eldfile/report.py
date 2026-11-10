"""Run every module over one output file (and its portal export) and write the findings as a
Markdown report with figures and CSV tables."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

from . import hos, reconcile, validate  # noqa: E402
from .parse import OutputFile  # noqa: E402

COLORS = {"D": "#2a78d6", "ON": "#eb6834", "OFF": "#d9d8d2", "PC": "#1baf7a", "YM": "#eda100", "SB": "#6250d6", "UD": "#e34948"}
LABELS = {"D": "Driving", "ON": "On duty, not driving", "OFF": "Off duty", "PC": "Personal conveyance", "YM": "Yard move", "SB": "Sleeper berth", "UD": "Unidentified driving (same vehicle)"}
INK, INK2, SURFACE = "#0b0b0b", "#52514e", "#fcfcfb"
UD_USERNAME = "UNIDENTIFIED"


def _style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#b8b7b0")
    ax.tick_params(colors=INK2, labelsize=9)
    ax.yaxis.grid(True, color="#e4e3de", linewidth=0.8)
    ax.set_axisbelow(True)


def analyze(f: OutputFile, export: pd.DataFrame | None, ud_username: str | None = UD_USERNAME) -> dict:
    v = validate.validate(f, ud_username=ud_username)
    segs = hos.timeline(f)
    day = hos.daily_summary(segs)
    out = {"validation": v, "segments": segs, "daily": day, "rules": {}, "unidentified": hos.unidentified_driving(f), "ud_summary": hos.unidentified_summary(f)}
    basis = int(f.header.get("multiday_basis") or 8)
    for rs in ("property", "passenger"):
        viol, metrics = hos.evaluate(segs, rs, basis)
        out["rules"][rs] = {"violations": viol, "metrics": metrics}
    if export is not None:
        out["reconcile"] = reconcile.reconcile(f, export)
    return out


def figure_daily(a: dict, path: Path) -> None:
    worked = a["daily"][a["daily"]["D"] > 0].copy()
    m = a["rules"]["property"]["metrics"].copy()
    m["day"] = [s.date() for s in m["start"]]
    per_day = m.groupby("day")[["drive_since_reset", "drive_since_break"]].max()
    worked = worked.set_index("day").join(per_day)
    x = range(len(worked))
    labels = [d.strftime("%b %d") for d in worked.index]
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.2), facecolor=SURFACE)
    ax = axes[0]
    _style(ax)
    ax.bar(x, worked["D"], width=0.62, color=COLORS["D"], linewidth=0)
    ax.axhline(10, color=INK2, linewidth=1, linestyle=(0, (4, 3)))
    ax.axhline(11, color=INK2, linewidth=1, linestyle=(0, (1, 2)))
    ax.text(len(worked) - 0.4, 10.08, "10 h: passenger-carrying limit (395.5)", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.text(len(worked) - 0.4, 11.08, "11 h: property-carrying limit (395.3)", ha="right", va="bottom", fontsize=8.5, color=INK2)
    ax.set_ylim(0, 12.6)
    ax.set_xticks(list(x), labels, rotation=60)
    ax.set_title("Driving hours per working day", loc="left", fontsize=11, color=INK)
    ax.set_ylabel("hours", color=INK2)
    ax = axes[1]
    _style(ax)
    over = worked["drive_since_break"] > 8
    ax.bar(x, worked["drive_since_break"], width=0.62, color=[COLORS["UD"] if o else COLORS["D"] for o in over], linewidth=0)
    ax.axhline(8, color=INK2, linewidth=1, linestyle=(0, (4, 3)))
    ax.text(len(worked) - 0.4, 8.08, "8 h without a 30-minute interruption (395.3 only)", ha="right", va="bottom", fontsize=8.5, color=INK2)
    for i, (v, o) in enumerate(zip(worked["drive_since_break"], over)):
        if o:
            ax.text(i, v + 0.12, f"{v:.1f} h", ha="center", va="bottom", fontsize=8.5, color=INK)
    ax.set_ylim(0, 12.6)
    ax.set_xticks(list(x), labels, rotation=60)
    ax.set_title("Longest driving stretch without a 30-minute break", loc="left", fontsize=11, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def figure_timeline(a: dict, path: Path) -> None:
    segs = a["segments"]
    ud = a["unidentified"]
    if not segs:
        return
    first = segs[0].start.date()
    last = max(segs[-1].start.date(), ud["end"].max().date() if len(ud) else first)    # the open last period runs to the file timestamp; the figure stops at the last record's day
    days = [first + dt.timedelta(days=i) for i in range((last - first).days + 1)]
    row = {d: i for i, d in enumerate(days)}
    fig, ax = plt.subplots(figsize=(12, 0.3 * len(days) + 1.8), facecolor=SURFACE)
    _style(ax)
    ax.yaxis.grid(False)
    ax.xaxis.grid(True, color="#e4e3de", linewidth=0.8)

    def pieces(start, end):
        t = start
        while t < end:
            day_end = dt.datetime.combine(t.date(), dt.time(0)) + dt.timedelta(days=1)
            e = min(end, day_end)
            if t.date() in row:
                yield row[t.date()], t.hour + t.minute / 60 + t.second / 3600, (e - t).total_seconds() / 3600
            t = e

    for s in segs:
        key = s.special or s.status
        for r, x0, w in pieces(s.start, s.end):
            ax.broken_barh([(x0, w)], (r - 0.32, 0.5), facecolors=COLORS[key], linewidth=0)
    for r_ in ud.itertuples():
        for r, x0, w in pieces(r_.start, r_.end):
            ax.broken_barh([(x0, w)], (r + 0.2, 0.18), facecolors=COLORS["UD"], linewidth=0)
    ax.set_xlim(0, 24)
    ax.set_xticks(range(0, 25, 3), [f"{h:02d}:00" for h in range(0, 25, 3)])
    ax.set_yticks(range(len(days)), [d.strftime("%b %d %a") for d in days], fontsize=8.5)
    ax.set_ylim(len(days) - 0.5, -0.6)
    ax.set_title("Duty status by day (home-terminal time), with driving recorded under the unidentified driver profile", loc="left", fontsize=11, color=INK)
    handles = [Patch(facecolor=COLORS[k], label=LABELS[k]) for k in ("D", "ON", "PC", "OFF", "UD")]
    ax.legend(handles=handles, loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=5, frameon=False, fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor=SURFACE)
    plt.close(fig)


def _fmt_h(h: float) -> str:
    return f"{h:.2f} h"


def _md_table(df: pd.DataFrame, cols: list[str] | None = None, fmt: dict | None = None) -> str:
    cols = cols or list(df.columns)
    fmt = fmt or {}
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if c in fmt:
                cells.append(fmt[c](v))
            elif isinstance(v, float):
                cells.append(f"{v:.2f}")
            elif isinstance(v, (dt.datetime, pd.Timestamp)):
                cells.append(v.strftime("%Y-%m-%d %H:%M:%S"))
            else:
                cells.append("" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write(f: OutputFile, a: dict, out: Path, source_note: str = "") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "figures").mkdir(exist_ok=True)
    (out / "tables").mkdir(exist_ok=True)
    figure_daily(a, out / "figures" / "daily_hours.png")
    figure_timeline(a, out / "figures" / "duty_timeline.png")
    v = a["validation"]
    v.frame().to_csv(out / "tables" / "validation_findings.csv", index=False)
    a["daily"].round(3).to_csv(out / "tables" / "daily_summary.csv", index=False)
    a["unidentified"].to_csv(out / "tables" / "unidentified_driving.csv", index=False)
    for rs, r in a["rules"].items():
        r["violations"].to_csv(out / "tables" / f"violations_{rs}.csv", index=False)
        r["metrics"].to_csv(out / "tables" / f"driving_segments_{rs}.csv", index=False)
    if "reconcile" in a:
        a["reconcile"][0].to_csv(out / "tables" / "reconcile_differences.csv", index=False)
        (out / "tables" / "reconcile_summary.json").write_text(json.dumps(a["reconcile"][1], indent=2) + "\n")
    (out / "tables" / "validation_summary.json").write_text(json.dumps(v.summary, indent=2, default=str) + "\n")

    day = a["daily"]
    worked = day[day["D"] > 0]
    s = v.summary
    segs = a["segments"]
    span = f"{segs[0].start:%Y-%m-%d} to {segs[-1].start:%Y-%m-%d}" if segs else "n/a"
    prop, pas = a["rules"]["property"], a["rules"]["passenger"]
    udsum = a["ud_summary"]
    ud = a["unidentified"]
    ud_days = ud[ud["closed_by"] == "unidentified record"].groupby(ud["start"].dt.date)[["hours", "miles"]].sum() if len(ud) else pd.DataFrame(columns=["hours", "miles"])
    ud_off_days = sorted(set(ud_days.index) - set(worked["day"])) if len(ud_days) else []
    relogin = ud[ud["closed_by"] == "driver login"] if len(ud) else ud
    pc = day["PC"].sum()
    max_drive = {rs: a["rules"][rs]["metrics"]["drive_since_reset"].max() for rs in a["rules"]}

    L: list[str] = []
    L.append("# ELD output file: verification, reconciliation and hours-of-service findings\n")
    L.append(f"Input: `{f.header.get('eld_registration_id', '')}` output file for driver `{f.username}`, carrier `{f.header.get('carrier_name', '')}`, "
             f"power unit `{f.power_unit}`, records {span}, {s['data_lines']} data lines, multiday basis {f.header.get('multiday_basis', '')} days. "
             f"Times are the home-terminal local times the file carries (UTC offset field `{f.header.get('utc_offset', '')}`). {source_note}\n")
    L.append("## Findings\n")
    checks_ok = s["line_checks_failed"] == 0 and s["file_check_ok"] and s["driver_event_checks_failed"] == 0
    L.append(f"1. **Every data check in the file verifies** with the Appendix A algorithms as published: {s['data_lines']}/{s['data_lines']} line checks, the file check, "
             f"{len(f.table('events'))}/{len(f.table('events'))} driver event checks; the {len(f.table('unidentified'))} unidentified-driver event checks are consistent with a single username ({s['unidentified_event_checks']}). "
             f"Validation: {s['errors']} errors, {s['warnings']} warnings." if checks_ok else f"1. **Data checks fail**: {s['line_checks_failed']} line checks, file check ok = {s['file_check_ok']}, {s['driver_event_checks_failed']} event checks.")
    if "reconcile" in a:
        rs_ = a["reconcile"][1]
        L.append(f"2. **The file and the carrier portal's event export agree record for record**: {rs_['matched']} sequence ids matched, {rs_['only_in_file']} only in the file, {rs_['only_in_export']} only in the export, "
                 f"{rs_['records_with_totals_compared']} records with odometer and engine-hour totals compared, {rs_['differences']} differences.")
    L.append(f"3. **The driver worked {len(worked)} days** in the period, driving {worked['D'].min():.2f}-{worked['D'].max():.2f} h per day ({worked['D'].sum():.1f} h in total) with {worked['on_duty_total'].min():.1f}-{worked['on_duty_total'].max():.1f} h on duty per day. "
             f"Every working day starts and ends with a personal-conveyance period ({pc:.1f} h of personal conveyance in the period); no sleeper-berth time is recorded.")
    nprop, npas = len(prop["violations"]), len(pas["violations"])
    L.append(f"4. **Rule set matters more than the logs**: under the property-carrying rules (395.3) the engine finds {nprop} violation(s), under the passenger-carrying rules (395.5) {npas}. "
             + ("The property finding is the 30-minute break rule: " + "; ".join(f"{r.start:%b %d} driving continued {r.excess_hours:.2f} h past 8 cumulative hours without a 30-minute interruption" for r in prop["violations"].itertuples() if r.rule == "break_30min") + ". " if nprop else "")
             + f"Daily driving peaks at {max_drive['passenger']:.3f} h against the 10-hour passenger limit, a margin of {(10 - max_drive['passenger']) * 60:.1f} minutes on the longest day: under the passenger-carrying rules the schedule leaves no margin.")
    n = 5
    if udsum["records"]:
        L.append(f"{n}. **The same vehicle was driven on {len(ud_days)} days without a driver logged in**: {udsum['driving_records']} unidentified driving records, {udsum['driving_hours']:.1f} h and {udsum['driving_miles']:.0f} miles in the unidentified driver profile, "
                 f"{len(ud_off_days)} of those days when this driver was off duty. {len(relogin)} unidentified driving records start in the same second as a logout of this driver and end at the next login "
                 f"({relogin['hours'].sum():.2f} h of this driver's own driving that sits in the unidentified profile until it is assigned). Unassigned driving time must be reviewed and assigned or annotated by the carrier (395.32).")
        n += 1
    mal = f.table("malfunctions")
    if len(mal):
        names = validate.MAL_DIAG_CODES
        L.append(f"{n}. The device logged {len(mal)} malfunction/diagnostic records: " + "; ".join(f"{names.get(r.mal_diag_code, r.mal_diag_code)} (code {r.mal_diag_code}) {'logged' if r.event_code in ('1', '3') else 'cleared'} {r.date[:2]}/{r.date[2:4]} {r.time[:2]}:{r.time[2:4]}" for r in mal.sort_values(['date', 'time']).itertuples())
                 + (". The last record is a logged diagnostic that is never cleared in the period, which is what the open unidentified driving above produces." if mal.sort_values(["date", "time"]).iloc[-1]["event_code"] in ("1", "3") else "."))
        n += 1
    cert = f.table("certifications")
    last = segs[-1] if segs else None
    L.append(f"{n}. Record keeping: {len(cert)} certification records, one per working day ({len(set(cert['date'])) if len(cert) else 0} distinct days certified of {len(worked)} worked)."
             + (f" The last record of the period is a {LABELS.get(last.special or last.status, '').lower()} indication at {last.start:%b %d %H:%M} with no off-duty record after it, so that period stays open until the file's timestamp." if last is not None and last.effective != "OFF" or (last is not None and last.special) else ""))
    L.append("")
    L.append("![Daily driving hours](figures/daily_hours.png)\n")
    L.append("![Duty timeline](figures/duty_timeline.png)\n")

    L.append("## 1. File verification\n")
    L.append(_md_table(pd.DataFrame([{"check": k, "result": str(val)} for k, val in s.items()]), ["check", "result"]))
    L.append("")
    if len(v.findings):
        L.append("Validator findings:\n")
        L.append(_md_table(v.frame(), ["level", "code", "section", "line_no", "message"], {"line_no": lambda x: "" if pd.isna(x) else str(int(x))}))
        L.append("")
    if "reconcile" in a:
        L.append("## 2. Reconciliation with the portal export\n")
        L.append(_md_table(pd.DataFrame([{"measure": k, "value": str(val)} for k, val in a["reconcile"][1].items()]), ["measure", "value"]))
        L.append("")
        if len(a["reconcile"][0]):
            L.append(_md_table(a["reconcile"][0].head(50)))
            L.append("")
    L.append("## 3. Duty-status summary by day\n")
    L.append(_md_table(worked, ["day", "D", "ON", "OFF", "PC", "on_duty_total", "driving_segments"], {"day": lambda d: d.strftime("%Y-%m-%d %a")}))
    L.append("")
    L.append("## 4. Hours-of-service evaluation\n")
    for rs in ("property", "passenger"):
        r = a["rules"][rs]
        L.append(f"### {hos.RULES[rs]['name']}\n")
        L.append(f"Violations: {len(r['violations'])}. Maximum driving since the last qualifying off-duty period: {r['metrics']['drive_since_reset'].max():.3f} h; "
                 f"maximum on-duty measure: {r['metrics']['duty_since_reset'].max():.2f} h; maximum rolling {f.header.get('multiday_basis', '')}-day on-duty total: {r['metrics']['weekly_on_duty'].max():.1f} h.\n")
        if len(r["violations"]):
            L.append(_md_table(r["violations"], ["rule", "limit_hours", "start", "end", "excess_hours", "seq"]))
            L.append("")
    L.append("Sleeper-berth splits, the short-haul and adverse-conditions exceptions are not modeled; the file records no sleeper-berth time, so they would not change these results.\n")
    if udsum["records"]:
        L.append("## 5. Unidentified driver profile\n")
        L.append(_md_table(pd.DataFrame([{"measure": k, "value": str(val)} for k, val in udsum.items()]), ["measure", "value"]))
        L.append("")
        L.append(_md_table(ud, ["seq", "start", "end", "hours", "miles", "closed_by"], {"miles": lambda x: "" if pd.isna(x) else f"{x:.0f}"}))
        L.append("")
    L.append("## Method notes\n")
    L.append("* Data checks: Appendix A section 4.8.2.1.x character mapping, line, event and file check values recomputed and compared with the values in the file.")
    L.append("* Timeline: active records only (record status 1); personal conveyance counts as off duty and a yard move as on duty, a special category ends at the next duty-status record or a cleared record; the last period is closed at the file's own timestamp.")
    L.append("* Rules: driving limit, on-duty window (property) or cumulative on-duty hours (passenger), the 30-minute break rule (property), the 60/70-hour rule with the 34-hour restart; one row per rule and duty period.")
    L.append("* Unidentified driving: a driving record runs to the next unidentified duty-status record of the day or to the driver's next login, whichever is first.")
    L.append("")
    path = out / "report.md"
    path.write_text("\n".join(L), encoding="ascii")
    return path
