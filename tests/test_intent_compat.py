"""Tests for _assert_intent_compat — the cross-validation checker between
typed CanonicalIntent fields and late-waist ResolvedOp fields.

Covers:
  - Matching intent/op produces no warnings and no counter increments
  - Mismatching action family (op_type vs intent.kind) produces warning + counter
  - Mismatching unit_kind (NodeTarget) vs rop.target_unit_kind produces warning + counter
  - Mismatching facet (FacetTarget) vs rop.target_special produces warning + counter
  - FacetTarget with unknown rop.target_special produces warning + counter
  - Fine-grained unit_kinds (subsection, item) still pass when target_unit_kind is section

Run:
    uv run python -m pytest tests/test_intent_compat.py -v --override-ini="addopts="
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Optional, cast

import pytest

from lawvm.core.canonical_intent import (
    CoverageMode,
    ExecutionContract,
    FacetTarget,
    Insert,
    InsertOrder,
    IntentKind,
    NodeTarget,
    OccupancyPolicy,
    _IRNodeLike,
    Repeal,
    Replace,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.ops import (
    AmendmentOp,
    ResolvedOp,
    _assert_intent_compat,
    intent_compat_stats,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(*path_pairs: tuple) -> LegalAddress:
    """Build a LegalAddress from positional (kind, label) pairs."""
    return LegalAddress(path=tuple(path_pairs))


def _node_target(unit_kind: str, *path_pairs: tuple) -> NodeTarget:
    return NodeTarget(address=_addr(*path_pairs))


def _facet_target(facet: FacetKind, *path_pairs: tuple) -> FacetTarget:
    return FacetTarget(host=_addr(*path_pairs), facet=facet)


@dataclass(frozen=True)
class _PayloadNode:
    kind: object = IRNodeKind.CONTENT
    children: tuple["_PayloadNode", ...] = ()
    label: str | None = None
    text: str = "payload"


def _payload() -> _IRNodeLike:
    return cast(_IRNodeLike, _PayloadNode())


def _compat_upsert_policy() -> OccupancyPolicy:
    from lawvm.core.occupancy import OccupancyClass

    return OccupancyPolicy(
        primary_expected_from=frozenset(
            {
                OccupancyClass.ABSENT,
                OccupancyClass.SUBSTANTIVE,
                OccupancyClass.TOMBSTONE,
                OccupancyClass.SCAFFOLD,
            }
        ),
        allowed_from=frozenset(OccupancyClass),
        result=OccupancyClass.SUBSTANTIVE,
    )


def _contract() -> ExecutionContract:
    return ExecutionContract(
        occupancy=_compat_upsert_policy(),
        coverage=CoverageMode.EXACT,
    )


def _insert_contract() -> ExecutionContract:
    return ExecutionContract(
        occupancy=_compat_upsert_policy(),
        coverage=CoverageMode.EXACT,
        insert_order=InsertOrder.SORTED_FAMILY,
    )


def _op(
    op_type: Literal["REPLACE", "REPEAL", "INSERT", "RENUMBER"] = "REPLACE",
    target_unit_kind: TargetUnitKind | None = None,
    target_kind: TargetKind | None = None,
    target_special: Optional[str] = None,
) -> AmendmentOp:
    if target_unit_kind is None:
        target_unit_kind = (
            "chapter"
            if target_kind == TargetKind.CHAPTER
            else "part"
            if target_kind == TargetKind.PART
            else "section"
        )
    return AmendmentOp(
        op_id="test",
        op_type=op_type,
        target_section="1",
        target_unit_kind=target_unit_kind,
        target_special=target_special,
        source_statute="2020/1",
    )


def _rop(op: AmendmentOp) -> ResolvedOp:
    path: list[tuple[str, str]] = []
    if op.target_unit_kind == "chapter":
        path.append(("chapter", str(op.target_section)))
    elif op.target_unit_kind == "part":
        path.append(("part", str(op.target_section)))
    else:
        if op.target_chapter:
            path.append(("chapter", str(op.target_chapter)))
        path.append(("section", str(op.target_section)))
    if op.target_paragraph is not None:
        path.append(("subsection", str(op.target_paragraph)))
    if op.target_item is not None:
        path.append(("item", str(op.target_item)))
    special = None
    if op.target_special in {"otsikko", "otsikko_edella"}:
        special = FacetKind.HEADING
    elif op.target_special == "johd":
        special = FacetKind.INTRO

    return ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind=op.target_unit_kind,
        target_norm=op.target_section,
        _op_type_seed=op.op_type,
        _target_special_override=(
            op.target_special if op.target_special not in {None, "otsikko", "johd"} else None
        ),
        sec1_body_johto_fallback=op.sec1_body_johto_fallback,
        uncovered_body_recovery=op.uncovered_body_recovery,
        post_repeal_item_shift_label=op.post_repeal_item_shift_label,
        _source_statute_override=op.source_statute,
        _source_issue_date_override=op.source_issue_date,
        _source_title_override=op.source_title,
        _target_address_override=LegalAddress(path=tuple(path), special=special),
    )


# ---------------------------------------------------------------------------
# Tests: matching pairs produce no warnings and no counter increments
# ---------------------------------------------------------------------------


def test_amendment_op_projects_legacy_target_kind_from_explicit_unit_kind() -> None:
    op = AmendmentOp(op_type="REPLACE", target_unit_kind="chapter", target_section="5")

    assert op.target_unit_kind == "chapter"


def test_amendment_op_rejects_conflicting_legacy_target_kind_seed() -> None:
    with pytest.raises(ValueError, match="target_kind seed disagrees"):
        AmendmentOp(
            op_type="REPLACE",
            target_unit_kind="chapter",
            target_kind=TargetKind.SECTION,
            target_section="5",
        )


def test_matching_replace_section_no_warning(caplog) -> None:
    """REPLACE op + Replace(NodeTarget section) → no warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")
    after = intent_compat_stats.total

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == [], f"Unexpected warnings: {[r.message for r in mismatch_lines]}"
    assert after == before, "Counter should not increment on a match"


def test_matching_insert_section_no_warning(caplog) -> None:
    """INSERT op + Insert(NodeTarget section) → no warning."""
    op = _op(op_type="INSERT", target_unit_kind="section")
    rop = _rop(op)
    intent = Insert(
        kind=IntentKind.INSERT,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_insert_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_matching_repeal_section_no_warning(caplog) -> None:
    """REPEAL op + Repeal(NodeTarget section) → no warning."""
    op = _op(op_type="REPEAL", target_unit_kind="section")
    rop = _rop(op)
    intent = Repeal(
        kind=IntentKind.REPEAL,
        target=_node_target("section", ("section", "1")),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_matching_replace_chapter_no_warning(caplog) -> None:
    """REPLACE op + Replace(NodeTarget chapter) with target_kind=L → no warning."""
    op = _op(op_type="REPLACE", target_unit_kind="chapter")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("chapter", ("chapter", "3")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_matching_replace_heading_facet_no_warning(caplog) -> None:
    """REPLACE op with target_special=otsikko + Replace(FacetTarget heading) → no warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section", target_special="otsikko")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.HEADING, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_matching_replace_intro_facet_no_warning(caplog) -> None:
    """REPLACE op with target_special=johd + Replace(FacetTarget intro) → no warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section", target_special="johd")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.INTRO, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_insert_heading_facet_replace_carveout_no_warning(caplog) -> None:
    """INSERT otsikko lowered as Replace(FacetTarget) should not warn."""
    op = _op(op_type="INSERT", target_unit_kind="section", target_special="otsikko")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.HEADING, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    before_af = intent_compat_stats.action_family
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before
    assert intent_compat_stats.action_family == before_af


def test_insert_intro_facet_replace_carveout_no_warning(caplog) -> None:
    """INSERT johd lowered as Replace(FacetTarget) should not warn."""
    op = _op(op_type="INSERT", target_unit_kind="section", target_special="johd")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.INTRO, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    before_af = intent_compat_stats.action_family
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before
    assert intent_compat_stats.action_family == before_af


def test_matching_subsection_target_kind_p_no_warning(caplog) -> None:
    """NodeTarget subsection with target_kind=P → no warning (P is correct for subsections)."""
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("subsection", ("section", "1"), ("subsection", "2")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before


def test_matching_item_target_kind_p_no_warning(caplog) -> None:
    """NodeTarget item with target_kind=P → no warning (items live under sections)."""
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("item", ("section", "1"), ("subsection", "1"), ("item", "a")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []


def test_intent_compat_reads_resolvedop_mirrors_not_legacy_op(caplog) -> None:
    """Resolved late-waist target identity should govern compatibility checks."""
    op = _op(op_type="REPLACE", target_unit_kind="chapter", target_special="otsikko")
    rop = _rop(op)
    rop.target_unit_kind = "section"
    rop._target_address_override = LegalAddress(path=(("section", "1"),), special=FacetKind.INTRO)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.INTRO, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "test")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert mismatch_lines == []
    assert intent_compat_stats.total == before
    assert intent_compat_stats.total == before


# ---------------------------------------------------------------------------
# Tests: mismatching pairs produce warnings and counter increments
# ---------------------------------------------------------------------------


def test_action_family_mismatch_produces_warning(caplog) -> None:
    """op_type=REPEAL but intent.kind=replace → INTENT_COMPAT_MISMATCH action_family warning."""
    op = _op(op_type="REPEAL", target_unit_kind="section")
    rop = _rop(op)
    # Wrong: using Replace intent when op says REPEAL
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before_total = intent_compat_stats.total
    before_af = intent_compat_stats.action_family
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:REPEAL/replace_mismatch")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert len(mismatch_lines) >= 1, "Expected at least one mismatch warning"
    assert any("action_family" in r.message for r in mismatch_lines), (
        f"Expected action_family warning, got: {[r.message for r in mismatch_lines]}"
    )
    assert intent_compat_stats.action_family > before_af
    assert intent_compat_stats.total > before_total


def test_action_family_mismatch_emits_finding() -> None:
    op = _op(op_type="REPEAL", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )
    findings = []

    _assert_intent_compat(rop, intent, "ctx:REPEAL/replace_mismatch", findings_out=findings)

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "APPLY.INTENT_COMPAT_MISMATCH"
    assert finding.role == "observation"
    assert finding.stage == "apply"
    assert finding.source_statute == "2020/1"
    assert finding.blocking is False
    assert finding.detail["mismatch_kind"] == "action_family"
    assert finding.detail["op_id"] == "test"
    assert finding.detail["legacy_action"] == "REPEAL"
    assert finding.detail["expected_intent_kind"] == "repeal"
    assert finding.detail["actual_intent_kind"] == "replace"
    assert finding.detail["strict_disposition"] == "record"


def test_action_family_mismatch_insert_vs_replace(caplog) -> None:
    """op_type=INSERT but intent.kind=replace → action_family warning."""
    op = _op(op_type="INSERT", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before_af = intent_compat_stats.action_family
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:INSERT/replace_mismatch")

    assert intent_compat_stats.action_family > before_af


def test_unit_kind_mismatch_chapter_vs_p_produces_warning(caplog) -> None:
    """NodeTarget chapter with rop.target_unit_kind=section → unit_kind mismatch warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    # Wrong: intent says chapter but op says section (P)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("chapter", ("chapter", "3")),
        payload=_payload(),
        contract=_contract(),
    )

    before_uk = intent_compat_stats.unit_kind
    before_total = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:chapter_vs_P")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert any("unit_kind" in r.message for r in mismatch_lines), (
        f"Expected unit_kind warning, got: {[r.message for r in mismatch_lines]}"
    )
    assert intent_compat_stats.unit_kind > before_uk
    assert intent_compat_stats.total > before_total


def test_unit_kind_mismatch_emits_finding() -> None:
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("chapter", ("chapter", "3")),
        payload=_payload(),
        contract=_contract(),
    )
    findings = []

    _assert_intent_compat(rop, intent, "ctx:chapter_vs_P", findings_out=findings)

    assert len(findings) == 1
    assert findings[0].kind == "APPLY.INTENT_COMPAT_MISMATCH"
    assert findings[0].detail["mismatch_kind"] == "unit_kind"
    assert findings[0].detail["intent_leaf_kind"] == "chapter"
    assert findings[0].detail["expected_legacy_target_kind"] == "L"
    assert findings[0].detail["rop_target_unit_kind"] == "section"


def test_unit_kind_mismatch_section_vs_l_produces_warning(caplog) -> None:
    """NodeTarget section with rop.target_unit_kind=chapter → unit_kind mismatch warning."""
    op = _op(op_type="REPLACE", target_unit_kind="chapter")
    rop = _rop(op)
    # Wrong: intent says section but op says chapter (L)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before_uk = intent_compat_stats.unit_kind
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:section_vs_L")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert any("unit_kind" in r.message for r in mismatch_lines)
    assert intent_compat_stats.unit_kind > before_uk


def test_facet_mismatch_intro_vs_otsikko_produces_warning(caplog) -> None:
    """FacetTarget intro with op.target_special=otsikko → facet mismatch warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section", target_special="otsikko")
    rop = _rop(op)
    # Wrong: intent says intro but op says otsikko (heading)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.INTRO, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before_f = intent_compat_stats.facet
    before_total = intent_compat_stats.total
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:intro_vs_otsikko")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert any("facet" in r.message for r in mismatch_lines), (
        f"Expected facet warning, got: {[r.message for r in mismatch_lines]}"
    )
    assert intent_compat_stats.facet > before_f
    assert intent_compat_stats.total > before_total


def test_facet_mismatch_emits_finding() -> None:
    op = _op(op_type="REPLACE", target_unit_kind="section", target_special="otsikko")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.INTRO, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )
    findings = []

    _assert_intent_compat(rop, intent, "ctx:intro_vs_otsikko", findings_out=findings)

    assert len(findings) == 1
    assert findings[0].kind == "APPLY.INTENT_COMPAT_MISMATCH"
    assert findings[0].detail["mismatch_kind"] == "facet"
    assert findings[0].detail["target_special"] == "otsikko"
    assert findings[0].detail["expected_facet"] == "heading"
    assert findings[0].detail["actual_facet"] == "intro"


def test_facet_unknown_target_special_produces_warning(caplog) -> None:
    """FacetTarget with op.target_special not in known mapping → advisory facet warning."""
    op = _op(op_type="REPLACE", target_unit_kind="section", target_special="unknown_special")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_facet_target(FacetKind.HEADING, ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    before_f = intent_compat_stats.facet
    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop, intent, "ctx:unknown_special")

    mismatch_lines = [r for r in caplog.records if "INTENT_COMPAT_MISMATCH" in r.message]
    assert any("facet" in r.message for r in mismatch_lines), (
        f"Expected facet advisory warning, got: {[r.message for r in mismatch_lines]}"
    )
    assert intent_compat_stats.facet > before_f


# ---------------------------------------------------------------------------
# Tests: counter accumulation is additive across multiple mismatches
# ---------------------------------------------------------------------------


def test_multiple_mismatches_accumulate_in_stats(caplog) -> None:
    """Two separate mismatch calls → total counter increases by at least 2."""
    before_total = intent_compat_stats.total

    # Mismatch 1: action family
    op1 = _op(op_type="REPEAL", target_unit_kind="section")
    rop1 = _rop(op1)
    intent1 = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    # Mismatch 2: unit_kind
    op2 = _op(op_type="REPLACE", target_unit_kind="section")
    rop2 = _rop(op2)
    intent2 = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("chapter", ("chapter", "3")),
        payload=_payload(),
        contract=_contract(),
    )

    with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply"):
        _assert_intent_compat(rop1, intent1, "ctx:multi1")
        _assert_intent_compat(rop2, intent2, "ctx:multi2")

    assert intent_compat_stats.total >= before_total + 2


# ---------------------------------------------------------------------------
# Tests: non-error behaviour — function always returns None, never raises
# ---------------------------------------------------------------------------


def test_assert_intent_compat_returns_none_on_mismatch() -> None:
    """_assert_intent_compat must return None (never raises) even with mismatch."""
    op = _op(op_type="REPEAL", target_unit_kind="chapter")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    result = _assert_intent_compat(rop, intent, "ctx:return_value_check")
    assert result is None


def test_assert_intent_compat_returns_none_on_match() -> None:
    """_assert_intent_compat returns None even when everything matches."""
    op = _op(op_type="REPLACE", target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    result = _assert_intent_compat(rop, intent, "ctx:return_value_match")
    assert result is None
