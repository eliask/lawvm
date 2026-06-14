"""Tests for the parse-characterize golden-corpus tool (corpus-independent core)."""

from __future__ import annotations

from lawvm.tools.parse_characterize import CharRow, _fingerprint


def test_fingerprint_is_order_sensitive_and_stable() -> None:
    """The fingerprint pins both the op codes and their witness rules, in order."""
    fp_a = _fingerprint(("M P 5", "L P 9"), ("fi.section_ref", "fi.insertion_section"))
    fp_b = _fingerprint(("M P 5", "L P 9"), ("fi.section_ref", "fi.insertion_section"))
    assert fp_a == fp_b  # stable

    # Reordered ops -> different fingerprint.
    assert fp_a != _fingerprint(("L P 9", "M P 5"), ("fi.insertion_section", "fi.section_ref"))
    # Same ops, different witness rule -> different fingerprint (catches a rule
    # regression even when the op code is unchanged).
    assert fp_a != _fingerprint(("M P 5", "L P 9"), ("fi.other", "fi.insertion_section"))


def test_char_row_roundtrips_through_fingerprint() -> None:
    """A CharRow's fp is a pure function of its ops + rules."""
    row = CharRow(
        sid="1999/123",
        ops=("M P 5 1", "M P 9"),
        rules=("fi.section_ref", "fi.section_ref"),
        n_ops=2,
        clean=True,
        fp=_fingerprint(("M P 5 1", "M P 9"), ("fi.section_ref", "fi.section_ref")),
    )
    assert row.fp == _fingerprint(row.ops, row.rules)
