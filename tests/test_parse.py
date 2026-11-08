from pathlib import Path

from eldfile import checks, parse

FIXTURE = Path(__file__).parent / "fixtures" / "handbook_example.csv"


def test_handbook_example_parses_into_sections():
    f = parse.read_file(FIXTURE)
    assert f.delimiter == "\r"
    assert [k for _, k in parse.SECTIONS] == f.section_order
    assert f.header["last_name"] == "DOE" and f.header["username"] == "Jon_Doe" and f.header["usdot"] == "5555555"
    assert f.header["codriver_last_name"] == "" and f.header["driver_line_check"] == "DF"
    assert len(f.table("events")) == 4 and len(f.table("logins")) == 2 and len(f.table("power")) == 2
    assert f.table("events").iloc[0]["seq"] == "0120" and f.table("events").iloc[0]["event_check"] == "99"
    assert f.table("power").iloc[0]["vin"] == "ABCDEFG1534567890"
    assert f.file_check == "DEA9"
    codes = {i.code for i in f.issues}
    assert "missing_section" not in codes and "field_count" not in codes
    # the example spells "Shut Down" without the hyphen of 4.8.2.1
    assert "section_header_text" in codes


def test_every_data_line_of_the_example_verifies():
    f = parse.read_file(FIXTURE)
    assert all(checks.verify_line(text) for _, _, text in f.data_lines)
    assert checks.file_check([checks.split_check(t)[1] for _, _, t in f.data_lines]) == f.file_check


def test_round_trip_is_byte_identical():
    raw = FIXTURE.read_bytes().decode("ascii")
    f = parse.read_text(raw)
    tables = {}
    for key in parse.FIELDS:
        tables[key] = [text for _, sec, text in f.data_lines if sec == key]
    again = parse.to_text(f.header_lines, tables, f.file_check, f.delimiter, f.section_headers_seen)
    assert again == raw


def test_tolerant_of_a_broken_file():
    text = "ELD File Header Segment:\rDOE,JON,Jon_Doe,MA,S1326545,DF\r,,,96\rUser List:\r1,D,DOE,JON,33\rELD Event List:\r0120,1,1,1,1,051217,134410,123,2.4,34.21,-87.07,1,1,1,0,0,99\rEnd of File:\rDEA9\r"
    f = parse.read_text(text)
    codes = [i.code for i in f.issues]
    assert codes.count("missing_section") == 7
    assert "field_count" in codes  # header has 2 of 7 lines; the event line lacks its line check
    assert f.file_check == "DEA9"


def test_delimiter_warning():
    text = FIXTURE.read_bytes().decode("ascii").replace("\r", "\r\n")
    f = parse.read_text(text)
    assert f.delimiter == "\r\n"
    assert any(i.code == "line_delimiter" for i in f.issues)
    assert len(f.table("events")) == 4
