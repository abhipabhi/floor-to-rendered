import pytest

from app.units import fmt_ft, parse_all_lengths_ft, parse_length_ft, parse_room_dim


@pytest.mark.parametrize(
    "text,expected",
    [
        ("30'", 30.0),
        ("42'", 42.0),
        ("6'-8\"", 6 + 8 / 12),
        ("11'0\"", 11.0),
        ('10"', 10 / 12),
        ('5"', 5 / 12),
        ("+16' - 9\"", 16 + 9 / 12),
        ("12.5'", 12.5),
        ("nonsense", None),
        ("", None),
    ],
)
def test_parse_length(text, expected):
    got = parse_length_ft(text)
    if expected is None:
        assert got is None
    else:
        assert got == pytest.approx(expected, abs=1e-6)


def test_negative_sign():
    assert parse_length_ft("-3'-6\"") == pytest.approx(-3.5)


def test_all_lengths():
    vals = parse_all_lengths_ft("39'-10\", 39', 10'-5\"")
    assert vals == pytest.approx([39 + 10 / 12, 39.0, 10 + 5 / 12])


def test_feet_and_inches_may_be_separated_by_spaces():
    """Title blocks write cumulative dimensions as ``+ 9' - 5"``."""
    assert parse_length_ft("+ 9' - 5\"") == pytest.approx(9 + 5 / 12)
    # the flip side: two lengths written with only a space between them read as
    # one, which is why callers parse a single token at a time
    assert parse_all_lengths_ft("39' 5\"") == pytest.approx([39 + 5 / 12])


def test_room_dim():
    rd = parse_room_dim("11'0\"X12'0\"")
    assert (rd.a, rd.b) == (11.0, 12.0)
    assert parse_room_dim("4'6\"X6'5\"").a == pytest.approx(4.5)


def test_room_dim_rejects_volumes_and_prose():
    # three dimensions is a tank, not a room
    assert parse_room_dim("(14'-0\"X10'-0\"X8'-0\")") is None
    assert parse_room_dim("BED ROOM") is None
    # a plausible pair of numbers that are not room sized
    assert parse_room_dim("1\"X2\"") is None


def test_fmt_ft():
    assert fmt_ft(12.5) == "12'-6\""
    assert fmt_ft(30.0) == "30'"
    assert fmt_ft(0.4166666) == "5\"" or fmt_ft(0.4166666) == "0'-5\""
