"""Unit tests for the free-text-scanning grammar driver.

Covers the shapes the prior naive ``recognize_sub_refs`` flip regressed on (the
26-regression catalogue): prose-led fragments, glued tokens (``1§:n``, ``ja37``),
Roman-numeral parts, partitive ``lukuun ottamatta`` clauses, missing-colon ``§n``,
and the spelled-out ``pykälän`` § marker. Each fixture asserts that the grammar
driver recovers the SAME-or-MORE targets the legacy ``address_parse`` regex did,
never fewer.
"""

from lawvm.finland.references.freetext_addresses import scan_legal_addresses


def _sections(text):
    return [(a.section, a.subsection, a.item, a.subitem) for a in scan_legal_addresses(text) if a.section]


def _chapters(text):
    return [a.chapter for a in scan_legal_addresses(text) if a.chapter and not a.section]


# ---------------------------------------------------------------------------
# Glued tokens
# ---------------------------------------------------------------------------


def test_glued_section_genitive() -> None:
    # ``1§:n`` (no space between number and §) tokenizes through the shared lexer.
    assert _sections("1§:n 2 momentti") == [("1", 2, None, None)]


def test_glued_section_bare() -> None:
    assert _sections("2§.") == [("2", None, None, None)]


def test_glued_conjunction_before_digit() -> None:
    # ``ja37`` (conjunction glued to the next section number) is de-glued by the
    # site-scan normalization so both sections are recovered.
    assert _sections("36 ja37 §.") == [("36", None, None, None), ("37", None, None, None)]


def test_glued_conjunction_after_range() -> None:
    got = _sections("35―40 ja43 §")
    labels = {s for s, *_ in got}
    assert {"35", "40", "43"} <= labels


def test_missing_colon_section_marker() -> None:
    # ``10 §n`` (missing colon in ``§:n``) still recovers section 10.
    assert ("10", None, None, None) in _sections("10 §n 2 momentti.") or (
        "10",
        2,
        None,
        None,
    ) in _sections("10 §n 2 momentti.")


# ---------------------------------------------------------------------------
# Spelled-out ``pykälä`` marker
# ---------------------------------------------------------------------------


def test_spelled_out_pykala_marker() -> None:
    # ``2 pykälän 1 momentin 73 kohta`` uses the word ``pykälän`` instead of ``§``.
    assert _sections("2 pykälän 1 momentin 73 kohta") == [("2", 1, "73", None)]


# ---------------------------------------------------------------------------
# Roman-numeral parts + coordinated section/chapter lists
# ---------------------------------------------------------------------------


def test_roman_part_then_chapter_then_section_list() -> None:
    # ``II, III ja IV osa sekä 14 ja 15 luku samoin kuin 2, 13, 23 ja 30 ynnä
    # 116–128 §`` — the grammar keeps the leading coordinated section list
    # (2, 13, 23, 30) the legacy regex dropped, AND the chapter list.
    text = "II, III ja IV osa sekä 14 ja 15 luku samoin kuin 2, 13, 23 ja 30 ynnä 116–128 §,"
    labels = {s for s, *_ in _sections(text)}
    assert {"2", "13", "23", "30", "116", "128"} <= labels
    assert {"14", "15"} <= set(_chapters(text))


def test_roman_part_chapter() -> None:
    assert "3" in _chapters("II osan 3 luku.")


# ---------------------------------------------------------------------------
# Momentti / kohta range expansion (legacy regex emitted false whole-section)
# ---------------------------------------------------------------------------


def test_momentti_range_binds_to_section() -> None:
    # ``9 §:n 2―5 momentti`` — the momentti range is bound to § 9, with NO bogus
    # whole-section repeal and NO orphan momentti (the legacy regex emitted both).
    got = _sections("9 §:n 2―5 momentti,")
    assert got == [("9", 2, None, None), ("9", 3, None, None), ("9", 4, None, None), ("9", 5, None, None)]


def test_kohta_list_under_momentti() -> None:
    assert _sections("13 §:n 2 momentin 2 ja 3 kohta") == [
        ("13", 2, "2", None),
        ("13", 2, "3", None),
    ]


def test_kohta_without_momentin() -> None:
    # ``26 §:n 1 kohta`` (no ``momentin``): the kohta is parsed directly.
    assert _sections("26 §:n 1 kohta.") == [("26", None, "1", None)]


def test_alakohta_subitem_carried() -> None:
    got = scan_legal_addresses("6 §:n 1 momentin 3 kohdan a alakohta.")
    assert len(got) == 1
    assert (got[0].section, got[0].subsection, got[0].item, got[0].subitem) == ("6", 1, "3", "a")


# ---------------------------------------------------------------------------
# Partitive ``lukuun ottamatta`` clauses (prose-led exception lists)
# ---------------------------------------------------------------------------


def test_partitive_exception_list() -> None:
    # ``lukuun ottamatta sen 45―55 §:ää ja 73 §:n 5, 6 ja 7 momenttia`` — the
    # range and the momentti list both parse; the leading ``sen`` prose is skipped.
    text = "siihen myöhemmin tehtyine muutoksineen, lukuun ottamatta sen 45―55 §:ää ja 73 §:n 5, 6 ja 7 momenttia"
    got = _sections(text)
    labels = {s for s, *_ in got}
    assert {"45", "55", "73"} <= labels
    assert ("73", 5, None, None) in got
    assert ("73", 7, None, None) in got


def test_prose_led_section() -> None:
    # A section cite embedded mid-prose (``… sekä lain 6 §:n 1 momentti``).
    assert ("6", 1, None, None) in _sections(
        "niihin myöhemmin tehtyine muutoksineen sekä lain 6 §:n 1 momentti"
    )


# ---------------------------------------------------------------------------
# Sole production consumer: johto_scope_mentions.collect_johto_moment_targets
# (the Q1 demotion migrated this off the legacy address_parse regex). The driver
# is strictly MORE correct for it: a kohta-only phrase no longer leaks a phantom
# whole-momentti target the legacy regex emitted.
# ---------------------------------------------------------------------------


def test_moment_targets_genuine_whole_momentti() -> None:
    from lawvm.finland.johto_scope_mentions import collect_johto_moment_targets

    # ``129 §:n 2 momentti`` names a whole momentti → registered as a target.
    assert collect_johto_moment_targets("129 §:n 2 momentti") == {"129": frozenset({2})}


def test_moment_targets_kohta_only_is_not_a_whole_momentti() -> None:
    from lawvm.finland.johto_scope_mentions import collect_johto_moment_targets

    # ``2 §:n 1 momentin 7 ja 8 kohta`` names only specific kohtas, NOT the whole
    # momentti. The grammar driver parses item-level rows (item is not None), so
    # the consumer correctly registers NO whole-momentti target. The legacy regex
    # leaked a phantom ``(2, 1)`` bare-momentti here.
    assert collect_johto_moment_targets("2 §:n 1 momentin 7 ja 8 kohta") == {}
