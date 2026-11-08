"""The worked examples of the ELD ICD development handbook (Volpe, 2017), sections 4.3.1-4.3.4."""
from eldfile import checks


def test_character_mapping_example():
    # "012, ABC, xyz!" -> 0 1 2 0 0 17 18 19 0 0 72 73 74 0
    assert [checks.char_value(c) for c in "012, ABC, xyz!"] == [0, 1, 2, 0, 0, 17, 18, 19, 0, 0, 72, 73, 74, 0]


def test_event_data_check_example():
    assert checks.event_check("1", "1", "051217", "134410", "123", "2.4", "34.21", "-87.07", "ZZZZZ12345", "Jon_Doe") == "99"


def test_line_data_check_example():
    body = "0123,1,1,1,1,051217,134410,123,2.4,34.21,-87.07,1,1,1,0,0,99"
    assert checks.mapped_sum(body) == 104
    assert checks.line_check(body) == "D5"
    assert checks.verify_line(body + ",D5")
    assert not checks.verify_line(body + ",D4")


def test_empty_co_driver_line():
    assert checks.line_check(",,,") == "96"


def test_file_data_check_example():
    values = ["DF", "96", "C8", "58", "BC", "DD", "71", "33", "41", "BD", "A5", "AD", "D5", "71", "08", "4B", "EB"]
    assert sum(int(v, 16) for v in values) == 2470
    assert checks.file_check(values) == "DEA9"
