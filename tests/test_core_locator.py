from lawvm.core.locator import (
    HierarchicalLocator,
    LocatorSegment,
    parse_locator_string,
)


def test_parse_locator_string_three_segments() -> None:
    loc = parse_locator_string("part:5/chapter:11/section:10")
    assert loc == HierarchicalLocator(
        segments=(
            LocatorSegment("part", "5"),
            LocatorSegment("chapter", "11"),
            LocatorSegment("section", "10"),
        )
    )


def test_parse_locator_string_two_segments() -> None:
    loc = parse_locator_string("chapter:11/section:3")
    assert loc is not None
    assert loc.segments == (
        LocatorSegment("chapter", "11"),
        LocatorSegment("section", "3"),
    )


def test_parse_locator_string_single_section_top_level() -> None:
    loc = parse_locator_string("section:14")
    assert loc is not None
    assert loc.is_top_level_section is True


def test_parse_locator_string_lettered_label() -> None:
    loc = parse_locator_string("chapter:3/section:14b")
    assert loc is not None
    assert loc.segments[-1].label == "14b"


def test_parse_locator_string_rejects_bare_label() -> None:
    assert parse_locator_string("2 §") is None
    assert parse_locator_string("") is None
    assert parse_locator_string("bare-label") is None


def test_parse_locator_string_rejects_malformed_segments() -> None:
    assert parse_locator_string("part:/chapter:11") is None  # empty label
    assert parse_locator_string(":5") is None  # empty kind
    assert parse_locator_string("PART:5/chapter:11") is not None  # case normalized
    assert parse_locator_string("part 5/chapter:11") is None  # space in kind


def test_locator_str_round_trip() -> None:
    s = "part:5/chapter:11/section:10"
    assert str(parse_locator_string(s)) == s
