from pathlib import Path

import pandas as pd

from eldfile import parse, reconcile

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "input" / "driver_output_file.csv"
EXPORT = ROOT / "data" / "input" / "driver_events_export.csv"


def test_labels():
    assert reconcile.parse_label("Driving") == ("events", "1", "3", "1")
    assert reconcile.parse_label("Driving (Unidentified)") == ("unidentified", "1", "3", "1")
    assert reconcile.parse_label("PC (Inactive - Changed)") == ("events", "3", "1", "2")
    assert reconcile.parse_label("First certification") == ("certifications", "4", None, "1")
    assert reconcile.parse_label("Something new")[0] is None


def test_published_views_agree():
    f = parse.read_file(PUBLISHED)
    ex = pd.read_csv(EXPORT, dtype=str)
    d, s = reconcile.reconcile(f, ex)
    assert s["matched"] == 790 and s["only_in_export"] == 0 and s["only_in_file"] == 0
    assert s["records_with_totals_compared"] == 267
    assert s["differences"] == 0, d.head()


def test_differences_are_reported():
    f = parse.read_file(PUBLISHED)
    ex = pd.read_csv(EXPORT, dtype=str)
    ex.loc[0, "time"] = "08:16:45"          # one second off
    ex.loc[1, "odometer"] = "619446"        # odometer off by one on a shut-down record
    ex.loc[2, "event_label"] = "Shut-down"  # a power-up relabelled
    ex = pd.concat([ex, pd.DataFrame([{**ex.iloc[3].to_dict(), "seq": "FFFF"}])], ignore_index=True)
    d, s = reconcile.reconcile(f, ex)
    assert set(d["field"]) == {"timestamp", "odometer", "event", "presence"}
    assert s["only_in_export"] == 1 and s["differences"] == 4
