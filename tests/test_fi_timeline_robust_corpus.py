"""Corpus pins for robust timeline invariant behavior."""

from __future__ import annotations

import pytest

from lawvm.core.invariant_profiles import core_replay_strict_profile
from lawvm.core.invariant_surface_matrix import FI_REPLAY_FOLD_SURFACE
from lawvm.core.phase_result import Finding
from lawvm.core.timeline_invariants import (
    check_all_timeline_invariants_typed,
    filter_promotable_timeline_invariant_rows,
    is_promotable_timeline_invariant_row,
    timeline_invariant_violation_row,
)
from lawvm.finland.replay_timeline_diagnostics import project_timeline_invariant_findings
from lawvm.tools.fi_timeline_robust_sweep import sweep_robust_timeline_invariants
from tests.corpus_pin_helpers import pinned_replay, replay_xml_for_test


@pytest.fixture(scope="module")
def replay_2009_953_legal_pit():
    return pinned_replay("2009/953", mode="legal_pit", quiet=True)


@pytest.fixture(scope="module")
def replay_1993_1054_legal_pit():
    return replay_xml_for_test("1993/1054", mode="legal_pit", quiet=True)


def test_robust_profile_reports_zero_hits_for_2009_953(replay_2009_953_legal_pit) -> None:
    products = replay_2009_953_legal_pit.products
    assert products.timelines is not None
    assert products.materialization_spec is not None

    violations = check_all_timeline_invariants_typed(
        products.materialized_state.ir,
        products.timelines,
        str(products.materialization_spec.as_of),
        families=FI_REPLAY_FOLD_SURFACE.replay_profile.timeline_invariants,
    )
    assert violations == []


def test_1993_1054_tail_statute_has_promotable_robust_hits(replay_1993_1054_legal_pit) -> None:
    products = replay_1993_1054_legal_pit.products
    assert products.timelines is not None
    assert products.materialization_spec is not None

    violations = check_all_timeline_invariants_typed(
        products.materialized_state.ir,
        products.timelines,
        str(products.materialization_spec.as_of),
        families=core_replay_strict_profile("corpus_pin").timeline_invariants,
    )
    robust_rows = [
        timeline_invariant_violation_row(violation)
        for violation in violations
        if violation.detail.get("tier") == "robust"
    ]
    promotable = filter_promotable_timeline_invariant_rows(robust_rows)
    assert promotable
    assert any(row["kind"] == "same_source_descendant_shadow" for row in promotable)


def test_timeline_hook_emits_robust_kinds_including_observation_only(replay_1993_1054_legal_pit) -> None:
    products = replay_1993_1054_legal_pit.products
    findings: list[Finding] = []
    project_timeline_invariant_findings(
        ir=products.materialized_state.ir,
        timelines=products.timelines,
        pit_date=products.materialization_spec.as_of,
        profile=FI_REPLAY_FOLD_SURFACE.replay_profile,
        replay_findings=findings,
        replay_meta_out={},
        replay_print=lambda _message: None,
        source_statute="1993/1054",
    )
    timeline_findings = [f for f in findings if f.kind == "timeline_invariant_violation"]
    assert timeline_findings
    assert all(f.detail.get("tier") == "robust" for f in timeline_findings)
    codes = {str(f.detail.get("code") or "") for f in timeline_findings}
    assert "same_source_descendant_shadow" in codes or "content_mismatch" in codes
    promotable = [
        finding
        for finding in timeline_findings
        if is_promotable_timeline_invariant_row(finding.detail)
    ]
    assert promotable
    assert not any(
        str(finding.detail.get("code")) == "overlapping_permanent" for finding in promotable
    )
    assert "overlapping_permanent" not in codes


def test_sweep_head_slice_is_clean() -> None:
    report = sweep_robust_timeline_invariants([(1, "2009/953"), (1, "1964/387")])
    assert report["statutes_with_robust_hits"] == 0
    assert report["statutes_with_promotable_hits"] == 0
