from __future__ import annotations

from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ScopePredicate,
    StructuralAction,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.phase_result import Finding, PhaseResult
from lawvm.tools.verify import _build_verify_facade
from lawvm.tools.verify import verify_full


def test_build_verify_facade_carries_temporal_events_into_timeline_execution() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
    )
    target = LegalAddress(path=(("section", "1"),))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(target_statute="test/verify-facade"),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
            TemporalEvent(
                event_id="verify:scope",
                group_id="g:verify",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    predicates=(ScopePredicate(dimension="territory", includes=frozenset({"AX"})),),
                ),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    assert len(facade.bundle.temporal_events) == 2

    materialized = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized.status == "degraded_missing_scope"
    assert materialized.required_dimensions == ("territory",)

    selected = facade.materialize_pit_ex(
        base,
        "2011-01-01",
        base_date="2000-01-01",
        territory="AX",
    )
    assert selected.status == "materialized"
    assert selected.statute.body.children[0].text == "Updated"


def test_build_verify_facade_dedupes_duplicate_findings() -> None:
    finding = Finding(
        kind="ELAB.SOURCE_PATHOLOGY",
        role="observation",
        stage="test",
        detail={"message": "duplicate"},
        blocking=False,
    )
    phase_result_a = PhaseResult(output=None, findings=(finding,))
    phase_result_b = PhaseResult(output=None, findings=(finding,))

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[],
        phase_results=[phase_result_a, phase_result_b],
    )

    assert facade.finding_ledger == (finding,)


def test_verify_full_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    verify_full("1978/38", "legal_pit")

    captured = capsys.readouterr()
    merged = captured.out + captured.err

    assert "REPLACE 10 luku otsikko → FAILED" not in merged
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in merged
    assert "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED" not in merged


def test_build_verify_facade_ignores_temporal_events_with_nonmatching_group_id() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
    )
    target = LegalAddress(path=(("section", "1"),))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:other",
                kind="commence",
                scope=TemporalScope(target_statute="test/verify-facade"),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
            TemporalEvent(
                event_id="verify:scope",
                group_id="g:other",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    predicates=(ScopePredicate(dimension="territory", includes=frozenset({"AX"})),),
                ),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized.status == "materialized"
    assert materialized.required_dimensions == ()
    assert materialized.statute.body.children[0].text == "Base"


def test_build_verify_facade_ignores_temporal_events_with_nonmatching_target_statute() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
    )
    target = LegalAddress(path=(("section", "1"),))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(target_statute="test/other-statute"),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/other-statute", effective="2010-01-01"),
            ),
            TemporalEvent(
                event_id="verify:scope",
                group_id="g:verify",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/other-statute",
                    predicates=(ScopePredicate(dimension="territory", includes=frozenset({"AX"})),),
                ),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized.status == "materialized"
    assert materialized.required_dimensions == ()
    assert materialized.statute.body.children[0].text == "Base"


def test_build_verify_facade_ignores_temporal_events_with_nonmatching_exact_address() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),)),
    )
    target = LegalAddress(path=(("section", "1"),))
    other_target = LegalAddress(path=(("section", "2"),))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    exact_addresses=(other_target,),
                ),
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
            TemporalEvent(
                event_id="verify:scope",
                group_id="g:verify",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    exact_addresses=(other_target,),
                    predicates=(ScopePredicate(dimension="territory", includes=frozenset({"AX"})),),
                ),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized.status == "materialized"
    assert materialized.required_dimensions == ()
    assert materialized.statute.body.children[0].text == "Base"


def test_build_verify_facade_honors_temporal_events_with_exact_address() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),),
                ),
            ),
        ),
    )
    resolved_target = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=resolved_target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    exact_addresses=(resolved_target,),
                ),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized_2007 = facade.materialize_pit_ex(base, "2007-01-01", base_date="2000-01-01")
    assert materialized_2007.status == "materialized"
    chapter_2007 = next(child for child in materialized_2007.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2007.children[0].text == "Base"

    materialized_2011 = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized_2011.status == "materialized"
    chapter_2011 = next(child for child in materialized_2011.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2011.children[0].text == "Updated"


def test_build_verify_facade_honors_temporal_events_with_address_prefix() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base"),),
                ),
            ),
        ),
    )
    chapter_prefix = LegalAddress(path=(("chapter", "1"),))
    resolved_target = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    op = LegalOperation(
        op_id="replace_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=resolved_target,
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    address_prefixes=(chapter_prefix,),
                ),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized_2007 = facade.materialize_pit_ex(base, "2007-01-01", base_date="2000-01-01")
    assert materialized_2007.status == "materialized"
    chapter_2007 = next(child for child in materialized_2007.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2007.children[0].text == "Base"

    materialized_2011 = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized_2011.status == "materialized"
    chapter_2011 = next(child for child in materialized_2011.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2011.children[0].text == "Updated"


def test_build_verify_facade_honors_exact_address_descendants_when_opted_in() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            text="Section 1",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base"),),
                        ),
                    ),
                ),
            ),
        ),
    )
    resolved_section = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    resolved_subsection = LegalAddress(path=(("chapter", "1"), ("section", "1"), ("subsection", "1")))
    op = LegalOperation(
        op_id="replace_subsection_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=resolved_subsection,
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    exact_addresses=(resolved_section,),
                    include_future_descendants=True,
                ),
                effective="2010-01-01",
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized_2007 = facade.materialize_pit_ex(base, "2007-01-01", base_date="2000-01-01")
    assert materialized_2007.status == "materialized"
    chapter_2007 = next(child for child in materialized_2007.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2007.children[0].children[0].text == "Base"

    materialized_2011 = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized_2011.status == "materialized"
    chapter_2011 = next(child for child in materialized_2011.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2011.children[0].children[0].text == "Updated"


def test_build_verify_facade_does_not_honor_exact_address_descendants_without_opt_in() -> None:
    base = IRStatute(
        statute_id="test/verify-facade",
        title="Verify facade test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            text="Section 1",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base"),),
                        ),
                    ),
                ),
            ),
        ),
    )
    raw_target = LegalAddress(path=(("section", "1"), ("subsection", "1")))
    resolved_section = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    op = LegalOperation(
        op_id="replace_subsection_1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=raw_target,
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Updated"),
        group_id="g:verify",
        source=OperationSource(
            statute_id="2010/100",
            enacted="2005-01-01",
            effective="2005-01-01",
        ),
    )
    phase_result = PhaseResult(
        output=None,
        findings=(
            Finding(
                kind="ELAB.SOURCE_PATHOLOGY",
                role="observation",
                stage="test",
                detail={},
                blocking=False,
            ),
        ),
        temporal_events=(
            TemporalEvent(
                event_id="verify:commence",
                group_id="g:verify",
                kind="commence",
                scope=TemporalScope(
                    target_statute="test/verify-facade",
                    exact_addresses=(resolved_section,),
                ),
                source=OperationSource(statute_id="test/verify-facade", effective="2010-01-01"),
            ),
        ),
    )

    facade = _build_verify_facade(
        replay_mode="legal_pit",
        structural_ops=[op],
        phase_results=[phase_result],
    )

    materialized_2007 = facade.materialize_pit_ex(base, "2007-01-01", base_date="2000-01-01")
    assert materialized_2007.status == "materialized"
    chapter_2007 = next(child for child in materialized_2007.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2007.children[0].children[0].text == "Base"

    materialized_2011 = facade.materialize_pit_ex(base, "2011-01-01", base_date="2000-01-01")
    assert materialized_2011.status == "materialized"
    chapter_2011 = next(child for child in materialized_2011.statute.body.children if child.kind == IRNodeKind.CHAPTER)
    assert chapter_2011.children[0].children[0].text == "Base"
