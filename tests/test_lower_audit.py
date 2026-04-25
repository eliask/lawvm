"""Tests for lawvm lower-audit: lowering pipeline preservation audit.

Covers:
  1. Simple replace johtolause -> 0 losses
  2. Mixed verb johtolause (replace + insert + repeal) -> all actions preserved
  3. Subsection-level targets -> targets preserved
  4. Known corpus statute (skipif no corpus)
"""

from __future__ import annotations

import os

import pytest

from lawvm.tools.lower_audit import (
    LoweringAuditResult,
    audit_lowering_preservation,
    _compare_target,
)
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.core.semantic_types import FacetKind, StructuralAction
from typing import Optional


def _make_parsed_op(
    verb: str = "M",
    kind: str = "P",
    number: str = "6",
    chapter: str = "",
    momentti: int = 0,
    item: str = "",
    facet: Optional[FacetKind] = None,
    part: str = "",
) -> ParsedOp:
    return ParsedOp(
        verb=verb,
        kind=kind,
        chapter=chapter,
        number=number,
        momentti=momentti,
        item=item,
        facet=facet,
        raw="",
        part=part,
    )


def _make_legal_op(
    action: str | StructuralAction = StructuralAction.REPLACE,
    path: tuple = (("section", "6"),),
    special: FacetKind | None = None,
) -> LegalOperation:
    return LegalOperation(
        op_id="",
        sequence=0,
        action=action if isinstance(action, StructuralAction) else StructuralAction(action),
        target=LegalAddress(path=path, special=special),
    )


# ---------------------------------------------------------------------------
# Test 1: Simple replace -> 0 losses
# ---------------------------------------------------------------------------


def test_simple_replace_zero_losses():
    """muutetaan 6 §: single replace op, 0 losses."""
    result = audit_lowering_preservation(
        "muutetaan 6 §",
        amendment_id="test/simple",
    )
    assert result.valid, f"Expected valid, got losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count == 1
    assert result.legal_op_count == 1
    assert result.actions_preserved == 1
    assert result.targets_preserved == 1
    assert result.losses == 0


# ---------------------------------------------------------------------------
# Test 2: Mixed verbs -> all actions preserved
# ---------------------------------------------------------------------------


def test_mixed_verbs_all_preserved():
    """muutetaan 3 §, kumotaan 5 §, lisätään uusi 7 §: three different verbs."""
    result = audit_lowering_preservation(
        "muutetaan 3 §, kumotaan 5 § sekä lisätään uusi 7 §",
        amendment_id="test/mixed",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count == 3
    assert result.legal_op_count == 3
    assert result.actions_preserved == 3
    assert result.targets_preserved == 3


# ---------------------------------------------------------------------------
# Test 3: Subsection-level targets -> targets preserved
# ---------------------------------------------------------------------------


def test_subsection_level_targets():
    """muutetaan 5 §:n 2 momentti: subsection target preserved."""
    result = audit_lowering_preservation(
        "muutetaan 5 §:n 2 momentti",
        amendment_id="test/subsection",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count >= 1
    assert result.targets_preserved >= 1


# ---------------------------------------------------------------------------
# Test 4: Item-level targets
# ---------------------------------------------------------------------------


def test_item_level_targets():
    """muutetaan 10 §:n 1 momentin 3 kohta: item-level target preserved."""
    result = audit_lowering_preservation(
        "muutetaan 10 §:n 1 momentin 3 kohta",
        amendment_id="test/item",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count >= 1
    assert result.targets_preserved >= 1


# ---------------------------------------------------------------------------
# Test 5: Chapter-scoped targets
# ---------------------------------------------------------------------------


def test_chapter_scoped_targets():
    """muutetaan 2 luvun 5 §: chapter context preserved in target."""
    result = audit_lowering_preservation(
        "muutetaan 2 luvun 5 §",
        amendment_id="test/chapter",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count >= 1
    assert result.targets_preserved >= 1


# ---------------------------------------------------------------------------
# Test 6: Heading replace -> facet preserved
# ---------------------------------------------------------------------------


def test_heading_replace_facet():
    """muutetaan 3 §:n otsikko: heading facet preserved as heading_replace."""
    result = audit_lowering_preservation(
        "muutetaan 3 §:n otsikko",
        amendment_id="test/heading",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count >= 1
    assert result.actions_preserved >= 1


# ---------------------------------------------------------------------------
# Test 7: Multiple sections under same verb
# ---------------------------------------------------------------------------


def test_multiple_sections_same_verb():
    """muutetaan 3, 5 ja 8 §: three sections, all preserved."""
    result = audit_lowering_preservation(
        "muutetaan 3, 5 ja 8 §",
        amendment_id="test/multi_section",
    )
    assert result.valid, f"Losses: {result.actions_lost + result.targets_lost}"
    assert result.parsed_op_count == 3
    assert result.legal_op_count == 3
    assert result.actions_preserved == 3
    assert result.targets_preserved == 3


# ---------------------------------------------------------------------------
# Test 8: Target comparison unit tests
# ---------------------------------------------------------------------------


def test_compare_target_match():
    """Matching target returns None (no mismatch)."""
    pop = _make_parsed_op(verb="M", kind="P", number="12", momentti=3, item="2")
    lop = _make_legal_op(
        action="replace",
        path=(("section", "12"), ("subsection", "3"), ("item", "2")),
    )
    assert _compare_target(pop, lop) is None


def test_compare_target_mismatch():
    """Mismatched target returns description string."""
    pop = _make_parsed_op(verb="M", kind="P", number="12", momentti=3)
    lop = _make_legal_op(
        action="replace",
        path=(("section", "12"), ("subsection", "5")),
    )
    result = _compare_target(pop, lop)
    assert result is not None
    assert "path" in result


def test_compare_target_special_match():
    """Heading special matches."""
    pop = _make_parsed_op(verb="M", kind="P", number="3", facet=FacetKind.HEADING)
    lop = _make_legal_op(
        action="heading_replace",
        path=(("section", "3"),),
        special=FacetKind.HEADING,
    )
    assert _compare_target(pop, lop) is None


# ---------------------------------------------------------------------------
# Test 9: LoweringAuditResult properties
# ---------------------------------------------------------------------------


def test_audit_result_valid_when_no_losses():
    r = LoweringAuditResult(
        amendment_id="x", parsed_op_count=3, legal_op_count=3, actions_preserved=3, targets_preserved=3
    )
    assert r.valid
    assert r.losses == 0


def test_audit_result_invalid_when_losses():
    r = LoweringAuditResult(
        amendment_id="x",
        parsed_op_count=3,
        legal_op_count=3,
        actions_preserved=2,
        targets_preserved=3,
        actions_lost=["test loss"],
    )
    assert not r.valid
    assert r.losses == 1


# ---------------------------------------------------------------------------
# Test 10: Known corpus statute (skipif no corpus)
# ---------------------------------------------------------------------------

_CORPUS_AVAILABLE = os.path.exists(
    os.path.join(os.path.dirname(__file__), "..", ".cache", "finlex_data")
) or os.environ.get("LAWVM_CORPUS_DIR")


@pytest.mark.skipif(not _CORPUS_AVAILABLE, reason="corpus not available")
def test_corpus_statute():
    """Run lower-audit on a known statute from the corpus."""
    from lawvm.tools.lower_audit import _run_for_statute

    exit_code = _run_for_statute("2009/953")
    # We just verify it runs without error; some statutes may have known losses
    assert exit_code in (0, 1)
