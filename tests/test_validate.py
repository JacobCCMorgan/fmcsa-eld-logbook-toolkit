from pathlib import Path

from eldfile import checks, parse, validate

ROOT = Path(__file__).resolve().parents[1]
PUBLISHED = ROOT / "data" / "input" / "driver_output_file.csv"
EXAMPLE = Path(__file__).parent / "fixtures" / "handbook_example.csv"


def codes(v, level=None):
    return [x.code for x in v.findings if level is None or x.level == level]


def test_published_file_has_no_errors():
    v = validate.validate(parse.read_file(PUBLISHED))
    assert v.errors == 0
    assert v.summary["line_checks_failed"] == 0 and v.summary["file_check_ok"] and v.summary["driver_event_checks_failed"] == 0
    assert v.summary["sequence_duplicates"] == 0 and v.summary["sequence_gaps"] == 0
    assert "83/83" in v.summary["unidentified_event_checks"]


def test_unidentified_checks_with_the_placeholder_username():
    v = validate.validate(parse.read_file(PUBLISHED), ud_username="UNIDENTIFIED")
    assert v.summary["unidentified_event_checks"].startswith("83/83")
    v2 = validate.validate(parse.read_file(PUBLISHED), ud_username="someone_else")
    assert "event_check" in codes(v2, "error")


def test_handbook_example_findings():
    # the example's CMV list (MA12345) disagrees with its header power unit (ZZZZZ12345), and its event
    # checks were computed with the latter; the example is also listed oldest first
    v = validate.validate(parse.read_file(EXAMPLE))
    assert codes(v, "error").count("event_check") == 4
    assert "order" in codes(v, "info")
    assert v.summary["file_check_ok"]


def mutate(line_no, fn):
    lines = PUBLISHED.read_bytes().decode("ascii").split("\r")
    lines[line_no - 1] = fn(lines[line_no - 1])
    return parse.read_text("\r".join(lines))


def test_tampered_line_is_caught():
    f = parse.read_file(PUBLISHED)
    no = int(f.table("events").iloc[0]["line_no"])
    # body edited, check value left alone: the line check fails, the file check (built from the line check values) still holds
    v = validate.validate(mutate(no, lambda s: s.replace(",1,1,1,1,", ",1,1,0,1,", 1)))
    assert "line_check" in codes(v, "error") and "file_check" not in codes(v, "error")

    # body edited and its line check recomputed: the line passes, the file check now disagrees
    def recomputed(s):
        body = s.replace(",1,1,1,1,", ",1,1,0,1,", 1).rsplit(",", 1)[0]
        return f"{body},{checks.line_check(body)}"

    v = validate.validate(mutate(no, recomputed))
    assert "line_check" not in codes(v, "error") and "file_check" in codes(v, "error")


def test_bad_event_code_and_duplicate_sequence():
    f = parse.read_file(PUBLISHED)
    no = int(f.table("events").iloc[1]["line_no"])

    def wrong_code(s):
        fields = s.split(",")
        fields[4] = "9"
        body = ",".join(fields[:-1])
        return f"{body},{checks.line_check(body)}"

    v = validate.validate(mutate(no, wrong_code))
    assert "domain" in codes(v, "error") and "line_check" not in codes(v, "error")

    def dup_seq(s):
        fields = s.split(",")
        fields[0] = f.table("events").iloc[0]["seq"]
        body = ",".join(fields[:-1])
        return f"{body},{checks.line_check(body)}"

    v = validate.validate(mutate(no, dup_seq))
    assert "sequence" in codes(v, "error")


def test_crlf_file_is_a_warning_not_an_error():
    text = PUBLISHED.read_bytes().decode("ascii").replace("\r", "\r\n")
    v = validate.validate(parse.read_text(text))
    assert "line_delimiter" in codes(v, "warning") and v.errors == 0


def test_event_check_computed_with_an_empty_power_unit_is_a_warning():
    # a vehicle renamed after the record was created: the stored check only verifies with an empty unit number
    f = parse.read_file(PUBLISHED)
    r = f.table("events").iloc[0]
    old_check = r["event_check"]
    new_check = checks.event_check(r.event_type, r.event_code, r.date, r.time, r.accum_miles, r.elapsed_hours, r.lat, r.lon, "", f.username)

    def rewrite(s):
        fields = s.split(",")
        assert fields[-2] == old_check
        fields[-2] = new_check
        body = ",".join(fields[:-1])
        return f"{body},{checks.line_check(body)}"

    v = validate.validate(mutate(int(r["line_no"]), rewrite))
    assert "event_check_power_unit" in codes(v, "warning") and "event_check" not in codes(v, "error")
    assert v.summary["driver_event_checks_failed"] == 0 and v.summary["driver_event_checks_power_unit_changed"] == 1
