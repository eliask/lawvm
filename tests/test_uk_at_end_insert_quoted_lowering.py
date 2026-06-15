"""Tests for the quoted at-end ``insert`` overlap-substitution lowering.

UK source rows of the shape ``In <unit>(<label>), [at [the] end insert | insert
at [the] end] "<quoted>"`` previously fell through to the
``uk_effect_overlap_substitution_unlowered`` blocking residue because the overlap
substitution parser only handled the ``add`` verb.  This lowering is the ``insert``
verb counterpart of ``_effect_metadata_carried_at_end_add_insert_fragment``: the
payload is a single quoted string, the preimage anchor is the end of the feed
target's own text run, and any source-named unit/label is validated against the
resolved target leaf/path.  Ambiguous shapes (definition entries, appropriate-place
index inserts, table/step contexts, unquoted tails) are refused so they stay typed
residue rather than being appended to the wrong node.

Rule ID : uk_effect_metadata_carried_at_end_insert_quoted_text_patch
Append sentinel : TEXT_FROM__TO_END (maps to a true APPEND text patch)
"""
from __future__ import annotations

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.effect_text_fragment_lowering import (
    _effect_metadata_carried_at_end_insert_quoted_fragment,
    _parse_at_end_insert_quoted,
)
from lawvm.uk_legislation.text_rewrite_fragments import (
    UK_METADATA_CARRIED_AT_END_INSERT_QUOTED_RULE_ID,
)


def _effect(effect_type: str = "words inserted") -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="key-at-end-insert-0001",
        effect_type=effect_type,
        applied=True,
        requires_applied=True,
        modified="2024-01-01",
        affected_uri="/id/ukpga/2003/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2003",
        affected_number="1",
        affected_provisions="x",
        affecting_uri="/id/ukpga/2003/14",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year="2003",
        affecting_number="14",
        affecting_provisions="x",
        affecting_title="Test Amending Act 2003",
    )


# ---------------------------------------------------------------------------
# Parser unit tests (_parse_at_end_insert_quoted)
# ---------------------------------------------------------------------------


def test_parse_anchor_then_verb_no_the() -> None:
    """``at end insert "<q>"`` (anchor then verb, no 'the') parses the quoted tail."""
    parsed = _parse_at_end_insert_quoted(
        '16 In paragraph 58(1), at end insert “ , as originally enacted. ”'
    )
    assert parsed is not None
    parent_label, source_kind, source_label, inserted = parsed
    assert inserted == ", as originally enacted."
    assert source_kind == ""  # unit named before the anchor, not "of <unit>"


def test_parse_verb_then_anchor_with_dash() -> None:
    """``insert at the end— "<q>"`` (verb then anchor) parses the quoted tail."""
    parsed = _parse_at_end_insert_quoted(
        "4 In subsection (4), insert at the end— "
        "“ section 169A (van available to more than one member). ”"
    )
    assert parsed is not None
    parent_label, _, _, inserted = parsed
    assert parent_label == "4"
    assert inserted == "section 169A (van available to more than one member)."


def test_parse_at_end_of_unit_binds_leaf() -> None:
    """``at the end of subsection (2) insert "<q>"`` binds the source leaf unit/label."""
    parsed = _parse_at_end_insert_quoted(
        'at the end of subsection (2) insert “ extra text. ”'
    )
    assert parsed is not None
    _, source_kind, source_label, inserted = parsed
    assert source_kind == "subsection"
    assert source_label == "2"
    assert inserted == "extra text."


def test_parse_unquoted_tail_returns_none() -> None:
    """An unquoted tail (``at the end insert ; section 13...``) does not parse."""
    parsed = _parse_at_end_insert_quoted(
        "3 In subsection (2), at the end insert ; section 13 of FA 2020."
    )
    assert parsed is None


def test_parse_no_at_end_returns_none() -> None:
    """A row without an at-end anchor does not parse."""
    assert _parse_at_end_insert_quoted('In subsection (1) insert “ words ”') is None


# ---------------------------------------------------------------------------
# Positive lowering — sound single-quoted tail appends
# ---------------------------------------------------------------------------


def test_lower_paragraph_at_end_insert_quoted() -> None:
    """``In paragraph 58(1), at end insert "<q>"`` lowers to a TEXT_FROM__TO_END append."""
    target = LegalAddress(
        path=(("schedule", "7"), ("paragraph", "58"), ("paragraph", "1"))
    )
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text='16 In paragraph 58(1), at end insert “ , as originally enacted. ”',
    )
    assert result == {
        "original": "TEXT_FROM__TO_END",
        "replacement": ", as originally enacted.",
        "rule_id": UK_METADATA_CARRIED_AT_END_INSERT_QUOTED_RULE_ID,
    }


def test_lower_subsection_insert_at_the_end_quoted() -> None:
    """``In subsection (4), insert at the end— "<q>"`` lowers; parent label validated."""
    target = LegalAddress(path=(("section", "114"), ("subsection", "4")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text=(
            "4 In subsection (4), insert at the end— "
            "“ section 169A (van available). ”"
        ),
    )
    assert result is not None
    assert result["original"] == "TEXT_FROM__TO_END"
    assert result["replacement"] == "section 169A (van available)."
    assert result["rule_id"] == UK_METADATA_CARRIED_AT_END_INSERT_QUOTED_RULE_ID


def test_lower_deictic_that_subsection() -> None:
    """``In that subsection, insert at the end— "<q>"`` lowers (no parent constraint)."""
    target = LegalAddress(path=(("section", "683"), ("subsection", "3")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text=(
            "3 In that subsection, insert at the end— "
            "“ section 636B (pension treated as arising). ”"
        ),
    )
    assert result is not None
    assert result["original"] == "TEXT_FROM__TO_END"
    assert result["replacement"] == "section 636B (pension treated as arising)."


# ---------------------------------------------------------------------------
# Negative lowering — ambiguous shapes refused (kept as typed residue)
# ---------------------------------------------------------------------------


def test_refuse_step_context() -> None:
    """``In step 3, insert at the end ...`` is refused: 'step' is not the target text end."""
    target = LegalAddress(path=(("section", "121"), ("subsection", "1")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text=(
            "2 In step 3, insert at the end— “ The resulting amount is the interim sum. ”"
        ),
    )
    assert result is None


def test_refuse_appropriate_place() -> None:
    """``at the appropriate places insert ...`` is refused (placement-ambiguous)."""
    target = LegalAddress(path=(("schedule", "1"), ("part", "2")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text="3 At the appropriate places insert— “ foo bar ”",
    )
    assert result is None


def test_refuse_definition_context() -> None:
    """``in the definition of "X" at the end insert ...`` is refused (definition path owns it)."""
    target = LegalAddress(path=(("section", "10"), ("subsection", "1")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text='In the definition of “asset”, at the end insert “ extra. ”',
    )
    assert result is None


def test_refuse_unquoted_tail() -> None:
    """An unquoted at-end insert is refused (joining/boundary ambiguity)."""
    target = LegalAddress(path=(("section", "655"), ("subsection", "2")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text=(
            "3 In subsection (2), at the end insert ; section 13 of FA 2020 (power)."
        ),
    )
    assert result is None


def test_refuse_target_mismatch() -> None:
    """A source-named unit/label inconsistent with the target is refused."""
    # Source scopes subsection (4); target leaf is subsection (9).
    target = LegalAddress(path=(("section", "114"), ("subsection", "9")))
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect(),
        target=target,
        extracted_text=(
            "4 In subsection (4), insert at the end— “ section 169A (van). ”"
        ),
    )
    assert result is None


def test_refuse_wrong_effect_type() -> None:
    """A non-insert effect type is refused (this lowering only covers inserts)."""
    target = LegalAddress(
        path=(("schedule", "7"), ("paragraph", "58"), ("paragraph", "1"))
    )
    result = _effect_metadata_carried_at_end_insert_quoted_fragment(
        effect=_effect("words substituted"),
        target=target,
        extracted_text='16 In paragraph 58(1), at end insert “ , as originally enacted. ”',
    )
    assert result is None
