"""Tests for the consistency checker's PhaseResult integration.

Tests the to_phase_result() conversion on ConsistencyResult without invoking
replay_xml (no corpus/network needed).  Also tests the pure validator helpers
that operate on IRNode/timeline structures.

Run:
    uv run pytest tests/test_check_consistency.py -v
"""

from __future__ import annotations

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ScopePredicate,
    StructuralAction,
    ProvisionVersion,
    ProvisionTimeline,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.timeline import compile_timelines, materialize_pit, select_active_version_ex
from lawvm.core.phase_result import PhaseResult
from lawvm.core.observation_registry import FINDING_REGISTRY, finding_codes_by_role
from lawvm.core.compile_result import TemporalEvent, TemporalScope
from lawvm.tools.consistency import ConsistencyResult, ConsistencyIssue, _section_versions_from_timelines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_issue(kind: str, address: str, detail: str = "") -> ConsistencyIssue:
    return ConsistencyIssue(kind=kind, address=address, detail=detail)


def _findings_of_role(pr: PhaseResult, role: str):
    return tuple(finding for finding in pr.findings() if finding.role == role)


# ---------------------------------------------------------------------------
# Test: registered observation kinds exist
# ---------------------------------------------------------------------------


def test_consistency_observation_kinds_registered() -> None:
    """The three consistency observation kinds must be present in the registry query."""
    observation_codes = set(finding_codes_by_role("observation"))
    assert "TIME.SECTION_NO_TIMELINE" in observation_codes
    assert "TIME.TIMELINE_NO_SECTION" in observation_codes
    assert "TIME.CONTENT_DRIFT" in observation_codes


# ---------------------------------------------------------------------------
# Test: to_phase_result on clean result
# ---------------------------------------------------------------------------


def test_to_phase_result_clean_result() -> None:
    """A clean ConsistencyResult produces a PhaseResult with no observations."""
    result = ConsistencyResult(sid="test/1", replay_sections=5, timeline_entries=5)
    pr = result.to_phase_result()

    assert isinstance(pr, PhaseResult)
    assert pr.output is result
    assert _findings_of_role(pr, "observation") == ()
    assert _findings_of_role(pr, "obligation") == ()
    assert not pr.has_blocking


# ---------------------------------------------------------------------------
# Test: to_phase_result on error result
# ---------------------------------------------------------------------------


def test_to_phase_result_error_produces_blocking_obligation() -> None:
    """A ConsistencyResult with an error produces a blocking Obligation."""
    result = ConsistencyResult(sid="test/1", error="replay crashed: boom")
    pr = result.to_phase_result()

    assert pr.has_blocking
    obligations = _findings_of_role(pr, "obligation")
    assert obligations == ()
    violations = _findings_of_role(pr, "violation")
    assert len(violations) == 1
    vio = violations[0]
    assert vio.kind == "APPLY.TREE_INVARIANT_VIOLATION"
    assert vio.detail["barrier_code"] == "APPLY.TREE_INVARIANT_VIOLATION"
    assert vio.blocking is True
    assert "boom" in vio.detail["error"]
    assert _findings_of_role(pr, "observation") == ()


# ---------------------------------------------------------------------------
# Test: to_phase_result on SECTION_NO_TIMELINE issues
# ---------------------------------------------------------------------------


def test_to_phase_result_section_no_timeline_emits_observation() -> None:
    """SECTION_NO_TIMELINE issues map to consistency_section_no_timeline observations."""
    result = ConsistencyResult(
        sid="test/1",
        issues=[_make_issue("SECTION_NO_TIMELINE", "section:3")],
    )
    pr = result.to_phase_result()

    assert not pr.has_blocking
    obs = tuple(o for o in _findings_of_role(pr, "observation") if o.kind == "TIME.SECTION_NO_TIMELINE")
    assert len(obs) == 1
    assert obs[0].detail["address"] == "section:3"
    assert obs[0].detail["sid"] == "test/1"
    assert obs[0].stage == "check_consistency"


# ---------------------------------------------------------------------------
# Test: to_phase_result on TIMELINE_NO_SECTION issues
# ---------------------------------------------------------------------------


def test_to_phase_result_timeline_no_section_emits_observation() -> None:
    """TIMELINE_NO_SECTION issues map to consistency_timeline_no_section observations."""
    result = ConsistencyResult(
        sid="test/2",
        issues=[_make_issue("TIMELINE_NO_SECTION", "chapter:1/section:4")],
    )
    pr = result.to_phase_result()

    obs = tuple(o for o in _findings_of_role(pr, "observation") if o.kind == "TIME.TIMELINE_NO_SECTION")
    assert len(obs) == 1
    assert obs[0].detail["address"] == "chapter:1/section:4"


# ---------------------------------------------------------------------------
# Test: to_phase_result on CONTENT_DRIFT issues
# ---------------------------------------------------------------------------


def test_to_phase_result_content_drift_emits_observation() -> None:
    """CONTENT_DRIFT issues map to consistency_content_drift observations."""
    result = ConsistencyResult(
        sid="test/3",
        issues=[_make_issue("CONTENT_DRIFT", "section:5", detail="ir='abc' tl='xyz'")],
    )
    pr = result.to_phase_result()

    obs = tuple(o for o in _findings_of_role(pr, "observation") if o.kind == "TIME.CONTENT_DRIFT")
    assert len(obs) == 1
    assert obs[0].detail["detail"] == "ir='abc' tl='xyz'"


# ---------------------------------------------------------------------------
# Test: to_phase_result on REPLAY_EXTRA / REPLAY_MISSING issues
# ---------------------------------------------------------------------------


def test_to_phase_result_replay_extra_emits_source_pathology() -> None:
    """REPLAY_EXTRA issues produce source_pathology observations with sub_kind."""
    result = ConsistencyResult(
        sid="test/4",
        issues=[_make_issue("REPLAY_EXTRA", "section:99", "present in replay, absent in oracle")],
    )
    pr = result.to_phase_result()

    obs = tuple(o for o in _findings_of_role(pr, "observation") if o.kind == "ELAB.SOURCE_PATHOLOGY")
    assert len(obs) == 1
    assert obs[0].detail["sub_kind"] == "REPLAY_EXTRA"
    assert not pr.has_blocking


def test_to_phase_result_replay_missing_emits_source_pathology() -> None:
    """REPLAY_MISSING issues produce source_pathology observations with sub_kind."""
    result = ConsistencyResult(
        sid="test/5",
        issues=[_make_issue("REPLAY_MISSING", "section:2", "present in oracle, absent in replay")],
    )
    pr = result.to_phase_result()

    obs = tuple(o for o in _findings_of_role(pr, "observation") if o.kind == "ELAB.SOURCE_PATHOLOGY")
    assert len(obs) == 1
    assert obs[0].detail["sub_kind"] == "REPLAY_MISSING"


# ---------------------------------------------------------------------------
# Test: to_phase_result with multiple mixed issues
# ---------------------------------------------------------------------------


def test_to_phase_result_mixed_issues() -> None:
    """Multiple issues of different kinds all produce the expected observations."""
    result = ConsistencyResult(
        sid="test/6",
        issues=[
            _make_issue("SECTION_NO_TIMELINE", "section:1"),
            _make_issue("TIMELINE_NO_SECTION", "section:2"),
            _make_issue("CONTENT_DRIFT", "section:3", "ir='a' tl='b'"),
            _make_issue("REPLAY_EXTRA", "section:4"),
            _make_issue("REPLAY_MISSING", "section:5"),
        ],
    )
    pr = result.to_phase_result()

    observations = _findings_of_role(pr, "observation")
    assert len(tuple(o for o in observations if o.kind == "TIME.SECTION_NO_TIMELINE")) == 1
    assert len(tuple(o for o in observations if o.kind == "TIME.TIMELINE_NO_SECTION")) == 1
    assert len(tuple(o for o in observations if o.kind == "TIME.CONTENT_DRIFT")) == 1
    assert len(tuple(o for o in observations if o.kind == "ELAB.SOURCE_PATHOLOGY")) == 2
    assert len(observations) == 5
    assert not pr.has_blocking


def test_section_versions_from_timelines_preserves_ambiguous_scope_note() -> None:
    addr = LegalAddress(path=(("section", "1"),))
    tl = ProvisionTimeline(
        address=addr,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                variant_kind="permanent",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),
                applicability=[ScopePredicate(dimension="territory", includes=frozenset({"England"}))],
            ),
        ],
    )

    sections, notes = _section_versions_from_timelines({addr: tl})

    assert sections == {}
    assert notes["section:1"] == (
        "selection_status=ambiguous_missing_scope; required_dimensions=('territory',); candidate_count=1"
    )


# ---------------------------------------------------------------------------
# Test: verdict property
# ---------------------------------------------------------------------------


def test_verdict_clean() -> None:
    result = ConsistencyResult(sid="test/1")
    assert result.verdict == "CLEAN"


def test_verdict_internal_drift_on_section_no_timeline() -> None:
    result = ConsistencyResult(
        sid="test/1",
        issues=[_make_issue("SECTION_NO_TIMELINE", "section:1")],
    )
    assert result.verdict == "INTERNAL_DRIFT"


def test_verdict_internal_drift_on_content_drift() -> None:
    result = ConsistencyResult(
        sid="test/1",
        issues=[_make_issue("CONTENT_DRIFT", "section:1")],
    )
    assert result.verdict == "INTERNAL_DRIFT"


def test_verdict_oracle_only_when_only_replay_gaps() -> None:
    result = ConsistencyResult(
        sid="test/1",
        oracle_sections=5,
        issues=[_make_issue("REPLAY_MISSING", "section:1")],
    )
    assert result.verdict == "ORACLE_ONLY"


def test_verdict_error() -> None:
    result = ConsistencyResult(sid="test/1", error="something failed")
    assert result.verdict == "ERROR"


# ---------------------------------------------------------------------------
# Integration: compile_timelines + _sections_from_ir consistency check
# (pure in-memory, no replay_xml)
# ---------------------------------------------------------------------------


def _make_statute(sections: list[tuple[str, str]]) -> IRStatute:
    """Build a minimal flat IRStatute."""
    children = [IRNode(kind=IRNodeKind.SECTION, label=lbl, text=text) for lbl, text in sections]
    return IRStatute(
        statute_id="test/999",
        title="Test statute",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children)),
    )


def test_section_no_timeline_detected_via_timeline_api() -> None:
    """When a section exists in the PIT but has no timeline, detect it."""
    from lawvm.core.timeline import _iter_nodes_with_address
    from lawvm.tools.consistency import _addr_str

    statute = _make_statute([("1", "Section one."), ("2", "Section two.")])
    timelines = compile_timelines(statute, [], base_date="2000-01-01")
    pit = materialize_pit(timelines, "2025-01-01", base=statute)

    # Verify: all sections in the PIT body should appear in timelines
    pit_sections = {}
    for addr, node in _iter_nodes_with_address(pit.body):
        if node.kind == IRNodeKind.SECTION:
            pit_sections[_addr_str(addr.path)] = node

    tl_sections = {}
    for addr, tl in timelines.items():
        if addr.path and addr.path[-1][0] == "section":
            selection = select_active_version_ex(tl, "9999-12-31")
            if selection.version is not None and selection.status == "selected":
                tl_sections[_addr_str(addr.path)] = selection.version

    # All PIT sections should have a timeline entry
    missing_from_tl = {k for k in pit_sections if k not in tl_sections}
    assert missing_from_tl == set(), f"Sections in PIT but not in timelines: {missing_from_tl}"


def test_content_drift_detected_when_text_differs() -> None:
    """After a replace op, the timeline content should match the materialized PIT."""
    from lawvm.core.timeline import _iter_nodes_with_address
    from lawvm.tools.consistency import _addr_str, _irnode_text_clean

    statute = _make_statute([("1", "Original text."), ("2", "Section two.")])
    addr_s1 = LegalAddress(path=(("section", "1"),))

    ops = [
        LegalOperation(
            op_id="replace_s1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=addr_s1,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Amended text."),
            group_id="g:content-drift:replace_s1",
            source=OperationSource(
                statute_id="2010/1",
                title="amend",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        )
    ]

    timelines = compile_timelines(
        statute,
        ops,
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:content-drift:replace_s1",
                group_id="g:content-drift:replace_s1",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(statute_id="2010/1", effective="2010-01-01"),
                scope=TemporalScope(target_statute="test/999"),
            ),
        ),
    )
    pit = materialize_pit(timelines, "2025-01-01", base=statute)

    pit_sections = {}
    for addr, node in _iter_nodes_with_address(pit.body):
        if node.kind == IRNodeKind.SECTION:
            pit_sections[_addr_str(addr.path)] = node

    # PIT should have the amended text
    assert "Amended" in pit_sections["section:1"].text

    # Timeline should also have the amended text as latest version
    tl = timelines[addr_s1]
    selection = select_active_version_ex(tl, "2025-01-01")
    assert selection.status == "selected"
    assert selection.version is not None
    assert selection.version.content is not None
    pit_text = _irnode_text_clean(pit_sections["section:1"])
    tl_text = _irnode_text_clean(selection.version.content)
    # They should match (no drift)
    assert pit_text == tl_text, f"Content drift detected: PIT={pit_text!r} TL={tl_text!r}"


def test_repeal_removes_section_from_pit_and_timeline() -> None:
    """A repealed section should be absent from PIT and have a tombstone in timeline."""
    statute = _make_statute([("1", "Section one."), ("2", "Section two.")])
    addr_s2 = LegalAddress(path=(("section", "2"),))

    ops = [
        LegalOperation(
            op_id="repeal_s2",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=addr_s2,
            group_id="g:repeal:repeal_s2",
            source=OperationSource(
                statute_id="2015/1",
                title="repeal",
                enacted="2015-01-01",
                effective="2015-01-01",
            ),
        )
    ]

    timelines = compile_timelines(
        statute,
        ops,
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:repeal:repeal_s2",
                group_id="g:repeal:repeal_s2",
                kind="commence",
                effective="2015-01-01",
                source=OperationSource(statute_id="2015/1", effective="2015-01-01"),
                scope=TemporalScope(target_statute="test/999"),
            ),
        ),
    )
    pit = materialize_pit(timelines, "2025-01-01", base=statute)

    # Section 2 should have a tombstone in the timeline
    tl = timelines[addr_s2]
    selection = select_active_version_ex(tl, "2025-01-01")
    assert selection.status == "selected"
    assert selection.version is not None
    assert selection.version.content is None  # tombstone

    # PIT body should not contain section 2 as a live node
    section_2_nodes = [child for child in pit.body.children if child.kind == IRNodeKind.SECTION and child.label == "2"]
    # Tombstoned section is omitted from the PIT body
    assert len(section_2_nodes) == 0


def test_to_phase_result_observations_have_registered_kinds() -> None:
    """Every observation kind emitted by to_phase_result must be registered."""
    result = ConsistencyResult(
        sid="test/7",
        issues=[
            _make_issue("SECTION_NO_TIMELINE", "section:1"),
            _make_issue("TIMELINE_NO_SECTION", "section:2"),
            _make_issue("CONTENT_DRIFT", "section:3"),
            _make_issue("REPLAY_EXTRA", "section:4"),
            _make_issue("REPLAY_MISSING", "section:5"),
        ],
    )
    pr = result.to_phase_result()

    all_registered = set(finding_codes_by_role("observation")) | {
        code for code, spec in FINDING_REGISTRY.items() if spec.is_obligation
    }
    for obs in (finding for finding in pr.findings() if finding.role == "observation"):
        assert obs.kind in all_registered, f"Unregistered observation kind {obs.kind!r} emitted by to_phase_result"
    for obl in (finding for finding in pr.findings() if finding.role == "obligation"):
        assert obl.kind in all_registered, f"Unregistered obligation kind {obl.kind!r} emitted by to_phase_result"
