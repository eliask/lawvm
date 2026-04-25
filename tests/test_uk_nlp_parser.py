from __future__ import annotations

from lawvm.uk_legislation.nlp_parser import parse_fragment_substitution


def test_parse_fragment_substitution_handles_there_is_inserted() -> None:
    subs = parse_fragment_substitution(
        'c in subsection (6) after “Agency,” there is inserted '
        '“by the Director General of the Scottish Crime and Drug Enforcement Agency,”.'
    )

    assert subs == [
        {
            "original": "Agency,",
            "replacement": (
                "Agency, by the Director General of the Scottish Crime and Drug "
                "Enforcement Agency,"
            ),
        }
    ]


def test_parse_fragment_substitution_handles_omit_words_to_end() -> None:
    subs = parse_fragment_substitution(
        '3 In subsection (4) omit the words from “; and references” to the end.'
    )

    assert subs == [
        {
            "original": "TEXT_FROM_; and references_TO_END",
            "replacement": "",
        }
    ]


def test_parse_fragment_substitution_handles_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        'a in subsections (1), (4) and (7), for “the Director” there is substituted “the OFT”;'
    )

    assert subs == [
        {
            "original": "the Director",
            "replacement": "the OFT",
        }
    ]


def test_parse_fragment_substitution_handles_parenthetical_before_there_is_substituted() -> None:
    subs = parse_fragment_substitution(
        'a for “the Director” (in each place) there is substituted “the OFT”;'
    )

    assert subs == [
        {
            "original": "the Director",
            "replacement": "the OFT",
        }
    ]


def test_parse_fragment_substitution_handles_there_shall_be_substituted() -> None:
    subs = parse_fragment_substitution(
        'b for the words “Act concerned” there shall be substituted “1998 Act”.'
    )

    assert subs == [
        {
            "original": "Act concerned",
            "replacement": "1998 Act",
        }
    ]


def test_parse_fragment_substitution_handles_from_beginning_to_substituted() -> None:
    subs = parse_fragment_substitution(
        '8 In subsection (13) for the words from the beginning to “in Northern Ireland,” '
        'substitute “ “Northern Ireland Social Security Commissioner” means”.'
    )

    assert subs == [
        {
            "original": "TEXT_FROM__TO_in Northern Ireland,",
            "replacement": "“Northern Ireland Social Security Commissioner” means",
        }
    ]


def test_parse_fragment_substitution_handles_is_replaced_with() -> None:
    subs = parse_fragment_substitution(
        "In subsection (2), the words “Alpha” is replaced with “Beta”."
    )

    assert subs == [{"original": "Alpha", "replacement": "Beta"}]
