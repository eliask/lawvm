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
    uv run python -m pytest tests/test_fi_intent_compat.py -v --override-ini="addopts="
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Optional, cast

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
from lawvm.core.phase_result import Finding
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.target_kind import TargetKind
from lawvm.finland.ops import (
    OpType,
    AmendmentOp,
    ResolvedOp,
    _assert_intent_compat,
    _build_canonical_intent,
    intent_compat_stats,
)
from lawvm.finland.apply_policy import _check_occupancy_policy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PathPair = tuple[str, str]


def _addr(*path_pairs: PathPair) -> LegalAddress:
    """Build a LegalAddress from positional (kind, label) pairs."""
    return LegalAddress(path=tuple(path_pairs))


def _node_target(unit_kind: str, *path_pairs: PathPair) -> NodeTarget:
    return NodeTarget(address=_addr(*path_pairs))


def _facet_target(facet: FacetKind, *path_pairs: PathPair) -> FacetTarget:
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
    op_type: OpType = OpType.REPLACE,
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
    if op.target_cols.target_unit_kind == "chapter":
        path.append(("chapter", str(op.target_cols.target_section)))
    elif op.target_cols.target_unit_kind == "part":
        path.append(("part", str(op.target_cols.target_section)))
    else:
        if op.target_cols.target_chapter:
            path.append(("chapter", str(op.target_cols.target_chapter)))
        path.append(("section", str(op.target_cols.target_section)))
    if op.target_cols.target_paragraph is not None:
        path.append(("subsection", str(op.target_cols.target_paragraph)))
    if op.target_cols.target_item is not None:
        path.append(("item", str(op.target_cols.target_item)))
    special = None
    if op.target_cols.target_special in {"otsikko", "otsikko_edella"}:
        special = FacetKind.HEADING
    elif op.target_cols.target_special == "johd":
        special = FacetKind.INTRO

    return ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind=op.target_cols.target_unit_kind,
        target_norm=op.target_cols.target_section,
        _op_type_seed=op.op_type,
        _target_special_override=(
            op.target_cols.target_special if op.target_cols.target_special not in {None, "otsikko", "johd"} else None
        ),
        _stamped_recognizers=op._stamped_recognizers,
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
    op = AmendmentOp(op_type=OpType.REPLACE, target_unit_kind="chapter", target_section="5")

    assert op.target_cols.target_unit_kind == "chapter"


def test_amendment_op_rejects_conflicting_legacy_target_kind_seed() -> None:
    with pytest.raises(ValueError, match="target_kind seed disagrees"):
        AmendmentOp(
            op_type=OpType.REPLACE,
            target_unit_kind="chapter",
            target_kind=TargetKind.SECTION,
            target_section="5",
        )


def test_matching_replace_section_no_warning(caplog) -> None:
    """REPLACE op + Replace(NodeTarget section) → no warning."""
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.INSERT, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPEAL, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="chapter")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section", target_special="otsikko")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section", target_special="johd")
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
    op = _op(op_type=OpType.INSERT, target_unit_kind="section", target_special="otsikko")
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
    op = _op(op_type=OpType.INSERT, target_unit_kind="section", target_special="johd")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="chapter", target_special="otsikko")
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
    op = _op(op_type=OpType.REPEAL, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPEAL, target_unit_kind="section")
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
    op = _op(op_type=OpType.INSERT, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="chapter")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section", target_special="otsikko")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section", target_special="otsikko")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section", target_special="unknown_special")
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


def test_occupancy_policy_violation_emits_finding() -> None:
    from lawvm.core.occupancy import OccupancyClass

    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=ExecutionContract(
            occupancy=OccupancyPolicy(
                primary_expected_from=frozenset({OccupancyClass.SUBSTANTIVE}),
                allowed_from=frozenset({OccupancyClass.SUBSTANTIVE}),
                result=OccupancyClass.SUBSTANTIVE,
            ),
            coverage=CoverageMode.EXACT,
        ),
    )
    findings = []

    _check_occupancy_policy(
        cast(Any, SimpleNamespace(ir=None)),
        rop,
        intent,
        None,
        "ctx:absent_replace",
        findings_out=findings,
    )

    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"
    assert finding.role == "observation"
    assert finding.stage == "apply"
    assert finding.source_statute == "2020/1"
    assert finding.blocking is False
    assert finding.detail["op_id"] == "test"
    assert finding.detail["legacy_action"] == "REPLACE"
    assert finding.detail["target_label"] == "1"
    assert finding.detail["current_occupancy"] == "absent"
    assert finding.detail["allowed_from"] == ("substantive",)
    assert finding.detail["primary_expected_from"] == ("substantive",)
    assert finding.detail["strict_disposition"] == "record"


def test_occupancy_policy_violation_quiet_replay_suppresses_warning(caplog) -> None:
    from lawvm.core.occupancy import OccupancyClass
    from lawvm.finland.replay_notices import reset_replay_verbose, set_replay_verbose

    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=ExecutionContract(
            occupancy=OccupancyPolicy(
                primary_expected_from=frozenset({OccupancyClass.SUBSTANTIVE}),
                allowed_from=frozenset({OccupancyClass.SUBSTANTIVE}),
                result=OccupancyClass.SUBSTANTIVE,
            ),
            coverage=CoverageMode.EXACT,
        ),
    )
    findings = []

    token = set_replay_verbose(False)
    try:
        with caplog.at_level(logging.WARNING, logger="lawvm.finland.apply_policy"):
            _check_occupancy_policy(
                cast(Any, SimpleNamespace(ir=None)),
                rop,
                intent,
                None,
                "ctx:absent_replace",
                findings_out=findings,
            )
    finally:
        reset_replay_verbose(token)

    assert [finding.kind for finding in findings] == ["APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert not any("occupancy policy violation" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# Tests: counter accumulation is additive across multiple mismatches
# ---------------------------------------------------------------------------


def test_multiple_mismatches_accumulate_in_stats(caplog) -> None:
    """Two separate mismatch calls → total counter increases by at least 2."""
    before_total = intent_compat_stats.total

    # Mismatch 1: action family
    op1 = _op(op_type=OpType.REPEAL, target_unit_kind="section")
    rop1 = _rop(op1)
    intent1 = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    # Mismatch 2: unit_kind
    op2 = _op(op_type=OpType.REPLACE, target_unit_kind="section")
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
    op = _op(op_type=OpType.REPEAL, target_unit_kind="chapter")
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
    op = _op(op_type=OpType.REPLACE, target_unit_kind="section")
    rop = _rop(op)
    intent = Replace(
        kind=IntentKind.REPLACE,
        target=_node_target("section", ("section", "1")),
        payload=_payload(),
        contract=_contract(),
    )

    result = _assert_intent_compat(rop, intent, "ctx:return_value_match")
    assert result is None


# ---------------------------------------------------------------------------
# Tests: production intent builder assigns per-action occupancy policies
# (no production intent may carry allowed_from == every OccupancyClass).
# ---------------------------------------------------------------------------


def _production_section_rop(op_type: OpType) -> ResolvedOp:
    """A lowered, address-bearing ResolvedOp with a real section payload.

    The intent is then built by the production ``_build_canonical_intent``,
    not hand-assembled, so the occupancy contract is the one the production
    builder assigns per action.
    """
    from lawvm.core.ir import IRNode

    op = AmendmentOp(
        op_id="prod",
        op_type=op_type,
        target_unit_kind="section",
        target_section="1",
        source_statute="2020/1",
    )
    return ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label="1"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="1",
        _op_type_seed=op_type,
        _source_statute_override="2020/1",
        _target_address_override=LegalAddress(path=(("section", "1"),)),
    )


def test_production_intents_never_allow_all_occupancy_classes() -> None:
    """No production-built section intent carries allowed_from == all classes."""
    from lawvm.core.occupancy import OccupancyClass

    all_classes = frozenset(OccupancyClass)
    for op_type in (OpType.REPLACE, OpType.INSERT, OpType.REPEAL):
        intent = _build_canonical_intent(_production_section_rop(op_type))
        assert intent is not None, f"production builder returned None for {op_type}"
        policy = intent.contract.occupancy
        assert policy.allowed_from != all_classes, (
            f"{op_type} intent permits every occupancy class — vacuous policy"
        )


def test_production_per_action_occupancy_policy_shapes() -> None:
    """Each action's production occupancy policy has the decided shape."""
    from lawvm.core.occupancy import OccupancyClass

    replace_policy_intent = _build_canonical_intent(
        _production_section_rop(OpType.REPLACE)
    )
    assert replace_policy_intent is not None
    replace_policy = replace_policy_intent.contract.occupancy
    assert replace_policy.primary_expected_from == frozenset({OccupancyClass.SUBSTANTIVE})
    assert replace_policy.allowed_from == frozenset(
        {OccupancyClass.SUBSTANTIVE, OccupancyClass.TOMBSTONE}
    )

    insert_policy_intent = _build_canonical_intent(
        _production_section_rop(OpType.INSERT)
    )
    assert insert_policy_intent is not None
    insert_policy = insert_policy_intent.contract.occupancy
    assert insert_policy.primary_expected_from == frozenset({OccupancyClass.ABSENT})
    assert insert_policy.allowed_from == frozenset(
        {OccupancyClass.ABSENT, OccupancyClass.TOMBSTONE, OccupancyClass.SCAFFOLD}
    )

    repeal_policy_intent = _build_canonical_intent(
        _production_section_rop(OpType.REPEAL)
    )
    assert repeal_policy_intent is not None
    repeal_policy = repeal_policy_intent.contract.occupancy
    assert repeal_policy.primary_expected_from == frozenset({OccupancyClass.SUBSTANTIVE})
    assert repeal_policy.allowed_from == frozenset(
        {OccupancyClass.SUBSTANTIVE, OccupancyClass.TOMBSTONE}
    )


def test_production_lane_replace_on_tombstone_emits_observation() -> None:
    """A production REPLACE landing on a tombstone records the occupancy note.

    The intent comes from the production builder (REPLACE allows tombstone as
    the non-primary reenactment lane), and the production occupancy guard
    records an observational, non-blocking finding for triage.
    """
    from lawvm.core.ir import IRNode
    from lawvm.finland.statute import ReplayState

    rop = _production_section_rop(OpType.REPLACE)
    intent = _build_canonical_intent(rop)
    assert intent is not None

    tombstone = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="1",
                attrs={"lawvm_repeal_placeholder": "1"},
            ),
        ),
    )
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=tombstone),
        rop,
        intent,
        (("section", "1"),),
        "ctx:prod_tombstone_replace",
        findings_out=findings,
    )

    hits = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert len(hits) == 1
    finding = hits[0]
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.detail["current_occupancy"] == "tombstone"
    assert finding.detail["allowed_non_primary"] is True


def test_production_lane_replace_on_absent_emits_violation() -> None:
    """A production REPLACE on an absent slot falls outside allowed_from."""
    from lawvm.core.ir import IRNode
    from lawvm.finland.statute import ReplayState

    rop = _production_section_rop(OpType.REPLACE)
    intent = _build_canonical_intent(rop)
    assert intent is not None

    # Empty body: the §1 slot has never existed → ABSENT, outside allowed_from.
    empty_body = IRNode(kind=IRNodeKind.BODY)
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=empty_body),
        rop,
        intent,
        (("section", "1"),),
        "ctx:prod_absent_replace",
        findings_out=findings,
    )

    hits = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert len(hits) == 1
    finding = hits[0]
    assert finding.role == "observation"
    assert finding.blocking is False
    assert finding.detail["current_occupancy"] == "absent"
    assert "allowed_non_primary" not in finding.detail


def test_production_lane_move_rider_replace_evaluates_origin_occupancy() -> None:
    """A destination-scoped REPLACE with a typed move rider is not a violation.

    Regression (2014/1429 ← 2025/1382): "29 e §, joka samalla siirretään
    5 b lukuun" compiles to REPLACE targeted at the DESTINATION chapter 5b
    where §29e is absent until the move lands. With the typed
    ``move_clause_target_unit_kind`` rider present and a unique live origin
    (§29e in chapter 5a), the occupancy policy must evaluate the ORIGIN slot
    (substantive → primary REPLACE expectation) instead of reporting
    REPLACE-on-absent at the destination.
    """
    from lawvm.core.ir import IRNode
    from lawvm.finland.statute import ReplayState

    op = AmendmentOp(
        op_id="prod",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="29e",
        target_chapter="5b",
        source_statute="2025/1382",
        move_clause_target_unit_kind="chapter",
    )
    from lawvm.core.semantic_types import IRNodeKind as _K

    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=_K.SECTION, label="29e"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="29e",
        _op_type_seed=OpType.REPLACE,
        move_clause_target_unit_kind="chapter",
        _source_statute_override="2025/1382",
        _target_address_override=LegalAddress(
            path=(("chapter", "5b"), ("section", "29e"))
        ),
    )
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert isinstance(intent, Replace)

    # Live state: §29e lives in chapter 5a (the move origin); 5b is empty.
    live = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5a",
                children=(IRNode(kind=IRNodeKind.SECTION, label="29e"),),
            ),
            IRNode(kind=IRNodeKind.CHAPTER, label="5b"),
        ),
    )
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=live),
        rop,
        intent,
        None,
        "ctx:move_rider_replace",
        findings_out=findings,
    )

    assert findings == [], [f.detail for f in findings]


def test_production_lane_move_rider_replace_without_origin_still_violates() -> None:
    """A move rider with no live origin anywhere is still REPLACE-on-absent."""
    from lawvm.core.ir import IRNode
    from lawvm.finland.statute import ReplayState

    op = AmendmentOp(
        op_id="prod",
        op_type=OpType.REPLACE,
        target_unit_kind="section",
        target_section="29e",
        target_chapter="5b",
        source_statute="2025/1382",
        move_clause_target_unit_kind="chapter",
    )
    from lawvm.core.semantic_types import IRNodeKind as _K

    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=_K.SECTION, label="29e"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="29e",
        _op_type_seed=OpType.REPLACE,
        move_clause_target_unit_kind="chapter",
        _source_statute_override="2025/1382",
        _target_address_override=LegalAddress(
            path=(("chapter", "5b"), ("section", "29e"))
        ),
    )
    intent = _build_canonical_intent(rop)
    assert intent is not None

    empty_body = IRNode(kind=IRNodeKind.BODY)
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=empty_body),
        rop,
        intent,
        None,
        "ctx:move_rider_no_origin",
        findings_out=findings,
    )

    hits = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert len(hits) == 1
    assert hits[0].detail["current_occupancy"] == "absent"


def test_production_lane_temporally_disjoint_twin_insert_is_not_a_violation() -> None:
    """A temporary gap-filler INSERT under a later-effective occupant is typed.

    Regression (2010/1326 ← 2022/1281 + 2022/1282): the permanent twin's
    §78c is deferred ("tulee kuitenkin voimaan vasta 1.7.2023") but lands in
    the document-order fold before the temporary twin's INSERT (in force
    1.1.2023–30.6.2023). The two occupancies are disjoint in legal time, so
    the INSERT must record the typed disjoint-window observation instead of
    an occupancy policy violation.
    """
    from lawvm.core.ir import IRNode, LegalOperation, OperationSource
    from lawvm.core.semantic_types import IRNodeKind as _K, StructuralAction
    from lawvm.finland.statute import ReplayState

    op = AmendmentOp(
        op_id="twin",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="78c",
        target_chapter="8",
        source_statute="2022/1282",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=_K.SECTION, label="78c"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="78c",
        _op_type_seed=OpType.INSERT,
        _source_statute_override="2022/1282",
        _target_address_override=LegalAddress(
            path=(("chapter", "8"), ("section", "78c"))
        ),
        _op_source_override=OperationSource(
            statute_id="2022/1282",
            title="väliaikainen",
            effective="2023-01-01",
            # Exclusive kernel cutoff: prose "voimassa 30.6.2023" ⇒ 2023-07-01.
            expires="2023-07-01",
        ),
    )
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert isinstance(intent, Insert)

    live = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                children=(IRNode(kind=IRNodeKind.SECTION, label="78c"),),
            ),
        ),
    )
    history = [
        LegalOperation(
            op_id="perm",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "8"), ("section", "78c"))),
            source=OperationSource(
                statute_id="2022/1281",
                title="pysyvä",
                effective="2023-07-01",
            ),
        )
    ]
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=live),
        rop,
        intent,
        (("chapter", "8"), ("section", "78c")),
        "ctx:twin_insert",
        findings_out=findings,
        replay_history_ops=history,
    )

    violations = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert violations == [], [f.detail for f in violations]
    notes = [
        f for f in findings if f.kind == "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT"
    ]
    assert len(notes) == 1
    note = notes[0]
    assert note.blocking is False
    assert note.detail["incoming_effective"] == "2023-01-01"
    assert note.detail["incoming_expires"] == "2023-07-01"
    assert note.detail["occupant_effective"] == "2023-07-01"
    assert note.detail["occupant_source_statute"] == "2022/1281"


def test_production_lane_same_effective_temporary_insert_is_window_observation() -> None:
    """A temporary same-day overlay records a bounded window, not a permanent break."""
    from lawvm.core.ir import IRNode, LegalOperation, OperationSource
    from lawvm.core.semantic_types import IRNodeKind as _K, StructuralAction
    from lawvm.finland.statute import ReplayState

    op = AmendmentOp(
        op_id="temporary_overlay",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="69d",
        target_chapter="5",
        source_statute="2009/887",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=_K.SECTION, label="69d"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="69d",
        _op_type_seed=OpType.INSERT,
        _source_statute_override="2009/887",
        _target_address_override=LegalAddress(
            path=(("chapter", "5"), ("section", "69d"))
        ),
        _op_source_override=OperationSource(
            statute_id="2009/887",
            title="väliaikainen",
            effective="2010-01-01",
            expires="2011-01-01",
        ),
    )
    intent = _build_canonical_intent(rop)
    assert intent is not None
    assert isinstance(intent, Insert)

    live = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(IRNode(kind=IRNodeKind.SECTION, label="69d"),),
            ),
        ),
    )
    history = [
        LegalOperation(
            op_id="permanent_69d",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "5"), ("section", "69d"))),
            source=OperationSource(
                statute_id="2009/886",
                title="pysyvä",
                effective="2010-01-01",
            ),
        )
    ]
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=live),
        rop,
        intent,
        (("chapter", "5"), ("section", "69d")),
        "ctx:temporary_overlay_insert",
        findings_out=findings,
        replay_history_ops=history,
    )

    assert [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"] == []
    notes = [
        f for f in findings if f.kind == "APPLY.OCCUPANCY_TEMPORALLY_DISJOINT_INSERT"
    ]
    assert len(notes) == 1
    assert notes[0].detail["rule_id"] == "temporally_bounded_overlay_insert"
    assert notes[0].detail["incoming_effective"] == "2010-01-01"
    assert notes[0].detail["incoming_expires"] == "2011-01-01"
    assert notes[0].detail["occupant_effective"] == "2010-01-01"


def test_production_lane_overlapping_twin_insert_still_violates() -> None:
    """An INSERT whose window overlaps the occupant's in-force period stays a violation."""
    from lawvm.core.ir import IRNode, LegalOperation, OperationSource
    from lawvm.core.semantic_types import IRNodeKind as _K, StructuralAction
    from lawvm.finland.statute import ReplayState

    op = AmendmentOp(
        op_id="twin",
        op_type=OpType.INSERT,
        target_unit_kind="section",
        target_section="78c",
        target_chapter="8",
        source_statute="2022/1282",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=_K.SECTION, label="78c"),
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="78c",
        _op_type_seed=OpType.INSERT,
        _source_statute_override="2022/1282",
        _target_address_override=LegalAddress(
            path=(("chapter", "8"), ("section", "78c"))
        ),
        # Expires AFTER the occupant becomes effective → real overlap.
        _op_source_override=OperationSource(
            statute_id="2022/1282",
            title="väliaikainen",
            effective="2023-01-01",
            expires="2023-08-31",
        ),
    )
    intent = _build_canonical_intent(rop)
    assert intent is not None

    live = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                children=(IRNode(kind=IRNodeKind.SECTION, label="78c"),),
            ),
        ),
    )
    history = [
        LegalOperation(
            op_id="perm",
            sequence=0,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("chapter", "8"), ("section", "78c"))),
            source=OperationSource(
                statute_id="2022/1281",
                title="pysyvä",
                effective="2023-07-01",
            ),
        )
    ]
    findings: list[Finding] = []
    _check_occupancy_policy(
        ReplayState(ir=live),
        rop,
        intent,
        (("chapter", "8"), ("section", "78c")),
        "ctx:overlapping_insert",
        findings_out=findings,
        replay_history_ops=history,
    )

    violations = [f for f in findings if f.kind == "APPLY.OCCUPANCY_POLICY_VIOLATION"]
    assert len(violations) == 1
    assert violations[0].detail["current_occupancy"] == "substantive"
