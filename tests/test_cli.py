"""The command line runs end to end on the published inputs."""
from pathlib import Path

from eldfile import cli

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_FILE = str(ROOT / "data" / "input" / "driver_output_file.csv")
EVENTS = str(ROOT / "data" / "input" / "driver_events_export.csv")


def test_verify_and_validate(capsys):
    assert cli.main(["verify", OUTPUT_FILE]) == 0
    assert "800 data lines, 0 line checks fail" in capsys.readouterr().out
    assert cli.main(["validate", OUTPUT_FILE]) == 0
    out = capsys.readouterr().out
    assert '"errors": 0' in out and "section_header_text" in out


def test_verify_fails_on_a_tampered_file(tmp_path, capsys):
    raw = Path(OUTPUT_FILE).read_bytes()
    bad = tmp_path / "bad.csv"
    bad.write_bytes(raw.replace(b"093026,", b"093126,", 1))
    assert cli.main(["verify", str(bad)]) == 1
    assert "1 line checks fail" in capsys.readouterr().out


def test_reconcile_and_hos(capsys):
    assert cli.main(["reconcile", OUTPUT_FILE, EVENTS]) == 0
    assert '"differences": 0' in capsys.readouterr().out
    assert cli.main(["hos", OUTPUT_FILE, "--rules", "passenger"]) == 0
    assert "395.5 passenger-carrying: 0 violation(s)" in capsys.readouterr().out
    assert cli.main(["hos", OUTPUT_FILE]) == 0
    assert "395.3 property-carrying: 1 violation(s)" in capsys.readouterr().out


def test_report(tmp_path):
    out = tmp_path / "reports"
    assert cli.main(["report", OUTPUT_FILE, "--export", EVENTS, "--out", str(out)]) == 0
    text = (out / "report.md").read_text(encoding="ascii")
    assert "790 sequence ids matched" in text and "0 differences" in text
    assert "under the passenger-carrying rules (395.5) 0" in text
    assert "margin of 0.8 minutes" in text
    for name in ("figures/daily_hours.png", "figures/duty_timeline.png", "tables/violations_property.csv", "tables/unidentified_driving.csv", "tables/reconcile_summary.json"):
        assert (out / name).exists()
