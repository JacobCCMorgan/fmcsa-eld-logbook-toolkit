"""Command line entry point: ``eldfile <command> ...``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from . import checks, hos, parse, reconcile, report, validate


def cmd_verify(a):
    f = parse.read_file(a.file)
    bad = [no for no, _, t in f.data_lines if not checks.verify_line(t)]
    expected = checks.file_check([checks.split_check(t)[1] for _, _, t in f.data_lines])
    print(f"{len(f.data_lines)} data lines, {len(bad)} line checks fail, file check {f.file_check} (expected {expected}), delimiter {f.delimiter!r}")
    for no in bad[:20]:
        print(f"  line {no}: data check does not match")
    return 1 if bad or expected != f.file_check else 0


def cmd_validate(a):
    f = parse.read_file(a.file)
    v = validate.validate(f, ud_username=a.ud_username)
    for x in v.findings:
        print(f"{x.level:8s} {x.code:22s} {x.section:12s} {'' if x.line_no is None else x.line_no!s:>5} {x.message}")
    print(json.dumps(v.summary, indent=2, default=str))
    return 1 if v.errors else 0


def cmd_reconcile(a):
    f = parse.read_file(a.file)
    ex = pd.read_csv(a.export, dtype=str, keep_default_na=False)
    diffs, summary = reconcile.reconcile(f, ex)
    print(json.dumps(summary, indent=2))
    if len(diffs):
        print(diffs.head(a.limit).to_string(index=False))
    return 1 if summary["differences"] else 0


def cmd_hos(a):
    f = parse.read_file(a.file)
    segs = hos.timeline(f)
    day = hos.daily_summary(segs)
    print(day.round(2).to_string(index=False))
    basis = int(f.header.get("multiday_basis") or 8)
    v, m = hos.evaluate(segs, a.rules, basis)
    print(f"\n{hos.RULES[a.rules]['name']}: {len(v)} violation(s); max driving since reset {m['drive_since_reset'].max():.3f} h")
    if len(v):
        print(v.to_string(index=False))
    return 0


def cmd_report(a):
    f = parse.read_file(a.file)
    ex = pd.read_csv(a.export, dtype=str, keep_default_na=False) if a.export else None
    res = report.analyze(f, ex, ud_username=a.ud_username)
    path = report.write(f, res, Path(a.out), source_note=a.note)
    print(f"report written to {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="eldfile", description="FMCSA ELD output file toolkit")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("verify", help="recompute and compare every data check value")
    p.add_argument("file")
    p.set_defaults(fn=cmd_verify)
    p = sub.add_parser("validate", help="structure, domains, cross references and data checks")
    p.add_argument("file")
    p.add_argument("--ud-username", default=report.UD_USERNAME, help="username to use for unidentified-driver event checks (default: the published files' placeholder)")
    p.set_defaults(fn=cmd_validate)
    p = sub.add_parser("reconcile", help="compare the file with a portal event export")
    p.add_argument("file")
    p.add_argument("export")
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_reconcile)
    p = sub.add_parser("hos", help="duty summary and hours-of-service rules")
    p.add_argument("file")
    p.add_argument("--rules", choices=sorted(hos.RULES), default="property")
    p.set_defaults(fn=cmd_hos)
    p = sub.add_parser("report", help="write the Markdown report, figures and tables")
    p.add_argument("file")
    p.add_argument("--export", default=None)
    p.add_argument("--out", default="reports")
    p.add_argument("--ud-username", default=report.UD_USERNAME)
    p.add_argument("--note", default="", help="sentence appended to the input description")
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
