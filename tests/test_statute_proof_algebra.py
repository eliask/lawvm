"""Tests for A2 typed statute-level proof algebra (evidence_statute_rules.py).

Covers:
  1. Unit tests for each direct claim emitter rule
  2. Partition pipeline correctness
  3. Late fallback rules (Gap 1, Gap 4, Gap 3a, Gap 3b)
  4. Parity tests: legacy _build_proof_claims() vs build_proof_claims_typed()
"""
from __future__ import annotations

import json
from typing import Any, Dict

from lawvm.core.section_evidence_context import SectionEvidenceContext
from lawvm.tools.evidence_claims import _build_proof_claims
from lawvm.tools.evidence_claim_algebra import (
    PositiveClaim,
    ProofTier,
    ResolvedSectionClaims,
    UnresolvedSink,
)
from lawvm.tools.evidence_statute_rules import (
    StatuteClaimRecord,
    StatuteEvidenceContext,
    StatuteResolvedClaims,
    build_proof_claims_typed,
    build_statute_context,
    partition_replay_bug_sections,
    rule_all_sections_match,
    rule_contingent_effective_sources,
    rule_html_fetch_error,
    rule_html_noncommensurable_reason,
    rule_oracle_body_empty_with_proved_sections,
    rule_oracle_metadata_inconsistency,
    rule_source_pathologies,
    rule_trivially_empty,
    rule_unanimous_section_claims_oracle_or_noncomm,
    rule_no_strong_claim,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMPTY_HTML_TOPOLOGY: Dict[str, Any] = {
    "noncommensurable_reason": "",
    "missing_from_xml": [],
    "extra_in_xml": [],
}


def _base_kwargs(**overrides: Any) -> Dict[str, Any]:
    """Build minimal kwargs for _build_proof_claims / build_proof_claims_typed."""
    defaults: Dict[str, Any] = {
        "section_results": [],
        "source_pathologies": [],
        "html_topology": dict(_EMPTY_HTML_TOPOLOGY),
        "contingent_effective_sources": [],
        "corrigendum_support": [],
    }
    defaults.update(overrides)
    return defaults


def _section_result(
    section: str = "section:1",
    diagnosis: str = "REPLAY_MISSING",
    blame_source: str = "2020/100",
    blame_title: str = "Test",
    replay_text: str = "replay",
    oracle_text: str = "oracle",
    **extra: Any,
) -> Dict[str, Any]:
    return {
        "section": section,
        "diagnosis": diagnosis,
        "blame_source": blame_source,
        "blame_title": blame_title,
        "replay_text": replay_text,
        "oracle_text": oracle_text,
        **extra,
    }


def _assert_parity(kwargs: Dict[str, Any]) -> None:
    """Assert typed path produces bit-identical output to legacy path."""
    legacy = _build_proof_claims(**kwargs)
    typed = build_proof_claims_typed(**kwargs)
    assert typed == legacy, (
        f"Parity failure.\n"
        f"Legacy: {json.dumps(legacy, indent=2, default=str)}\n"
        f"Typed:  {json.dumps(typed, indent=2, default=str)}"
    )


def _ctx_from_kwargs(kwargs: Dict[str, Any]) -> StatuteEvidenceContext:
    """Build a StatuteEvidenceContext from _base_kwargs()-style dict."""
    return build_statute_context(
        section_results=kwargs.get("section_results", []),
        source_pathologies=kwargs.get("source_pathologies", []),
        html_topology=kwargs.get("html_topology", dict(_EMPTY_HTML_TOPOLOGY)),
        contingent_effective_sources=kwargs.get("contingent_effective_sources", []),
        corrigendum_support=kwargs.get("corrigendum_support", []),
        oracle_suspect_detail=kwargs.get("oracle_suspect_detail", ""),
        oracle_suspect_pending=kwargs.get("oracle_suspect_pending", ""),
        html_error=kwargs.get("html_error", ""),
        section_bisect=kwargs.get("section_bisect"),
        alternative_replay_matches=kwargs.get("alternative_replay_matches"),
        oracle_range_matches=kwargs.get("oracle_range_matches"),
        cross_chapter_oracle_matches=kwargs.get("cross_chapter_oracle_matches"),
        section_claims=kwargs.get("section_claims"),
        typed_section_results=kwargs.get("typed_section_results"),
    )


def _typed_section_result(
    *,
    section: str,
    selected_tier: ProofTier | None,
    selected_kind: str,
) -> ResolvedSectionClaims:
    ctx = SectionEvidenceContext(
        section_label=section,
        diagnosis="UNKNOWN",
        oracle_text="oracle",
        replay_text="replay",
    )
    if selected_tier is None:
        selected = None
        candidates = ()
    elif selected_tier is ProofTier.UNRESOLVED:
        selected = UnresolvedSink(
            rule_id="TEST.UNRESOLVED",
            kind=selected_kind,
            inference_rule="test_unresolved",
            observation_sources=("test",),
            support={},
        )
        candidates = (selected,)
    else:
        selected = PositiveClaim(
            rule_id="TEST.POS",
            tier=selected_tier,
            kind=selected_kind,
            inference_rule="test_positive",
            observation_sources=("test",),
            support={},
        )
        candidates = (selected,)
    return ResolvedSectionClaims(
        ctx=ctx,
        selected=selected,
        candidates=candidates,
        defeated_candidates=(),
    )


# =========================================================================
# Part 1: Unit tests for direct claim emitter rules
# =========================================================================


class TestDirectClaimRules:
    def _make_ctx(self, **overrides: Any) -> StatuteEvidenceContext:
        return _ctx_from_kwargs(_base_kwargs(**overrides))

    def test_html_noncommensurable_fires(self) -> None:
        ctx = self._make_ctx(
            html_topology={
                "noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:5",
                "missing_from_xml": [],
                "extra_in_xml": [],
            }
        )
        result = rule_html_noncommensurable_reason(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE
        assert result[0].kind == "html_xml_scope_noncommensurable"

    def test_html_fetch_error_fires(self) -> None:
        ctx = self._make_ctx(
            html_error="fetch/parse failed (https://example.test)",
        )
        result = rule_html_fetch_error(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED
        assert result[0].kind == "html_fetch_error"
        assert result[0].claim.support["html_error"] == "fetch/parse failed (https://example.test)"

    def test_html_noncommensurable_skips_when_empty(self) -> None:
        ctx = self._make_ctx()
        assert rule_html_noncommensurable_reason(ctx) == ()

    def test_oracle_cutoff_drift_fires(self) -> None:
        ctx = self._make_ctx(oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff")
        result = rule_oracle_metadata_inconsistency(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result[0].kind == "oracle_metadata_inconsistency"
        assert result[0].claim.support["suspect_detail"] == "2009/1710 eff 2010-01-01 > cutoff"

    def test_oracle_cutoff_drift_skips_when_empty(self) -> None:
        ctx = self._make_ctx()
        assert rule_oracle_metadata_inconsistency(ctx) == ()

    def test_source_pathologies_fires(self) -> None:
        ctx = self._make_ctx(
            source_pathologies=[
                {"code": "MISSING_SOURCE", "source_statute": "2020/50"},
                {"code": "MISSING_SOURCE", "source_statute": "2021/60"},
            ]
        )
        result = rule_source_pathologies(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_SOURCE_PATHOLOGY
        assert result[0].kind == "source_pathology"
        assert result[0].claim.support["codes"] == ["MISSING_SOURCE"]
        assert "2020/50" in result[0].claim.support["source_statutes"]

    def test_source_pathologies_skips_when_empty(self) -> None:
        ctx = self._make_ctx(source_pathologies=[])
        assert rule_source_pathologies(ctx) == ()

    def test_contingent_effective_fires(self) -> None:
        ctx = self._make_ctx(contingent_effective_sources=["2020/50", "2021/60"])
        result = rule_contingent_effective_sources(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_SOURCE_PATHOLOGY
        assert result[0].kind == "contingent_effective_date"
        assert "2020/50" in result[0].claim.support["source_statutes"]

    def test_contingent_effective_skips_when_empty(self) -> None:
        ctx = self._make_ctx(contingent_effective_sources=[])
        assert rule_contingent_effective_sources(ctx) == ()


# =========================================================================
# Part 2: Partition pipeline tests
# =========================================================================


class TestPartitionPipeline:
    def _make_ctx(self, **overrides: Any) -> StatuteEvidenceContext:
        return _ctx_from_kwargs(_base_kwargs(**overrides))

    def test_preexisting_removed_from_residual(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result()],
            section_bisect=[
                {
                    "section": "section:1",
                    "blame_source": "2020/100",
                    "preexisting_before_any_drop": True,
                    "baseline_score": 0.97,
                    "first_bad_source": "2019/50",
                }
            ],
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.preexisting_replay_sections) == 1
        assert len(partition.residual_replay_bug_sections) == 0

    def test_improved_in_bucket_not_residual(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result()],
            section_bisect=[
                {
                    "section": "section:1",
                    "blame_source": "2020/100",
                    "blame_source_improved_or_equal": True,
                    "blame_before_score": 0.8,
                    "blame_after_score": 0.85,
                }
            ],
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.improved_replay_sections) == 1
        assert len(partition.residual_replay_bug_sections) == 0

    def test_section_claims_gate_removes_residual_when_not_replay_divergence(self) -> None:
        """Gate: section_claims provided, but section has oracle-incorrect tier → removed."""
        ctx = self._make_ctx(
            section_results=[_section_result()],
            section_claims=[
                {
                    "section": "section:1",
                    "selected_kind": "oracle_section_stale",
                    "selected_tier": "PROVED_ORACLE_INCORRECT",
                }
            ],
        )
        partition = partition_replay_bug_sections(ctx)
        # Residual should be empty because section is not replay_divergence
        assert len(partition.residual_replay_bug_sections) == 0

    def test_section_claims_gate_keeps_replay_divergence(self) -> None:
        """Gate: section_claims provided and section has replay_divergence → kept."""
        ctx = self._make_ctx(
            section_results=[_section_result()],
            section_claims=[
                {
                    "section": "section:1",
                    "selected_kind": "replay_divergence",
                    "selected_tier": "PROVED_REPLAY_BUG",
                }
            ],
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.residual_replay_bug_sections) == 1

    def test_section_claims_gate_skipped_when_no_section_claims(self) -> None:
        """Gate absent: no section_claims → residual keeps all replay sections."""
        ctx = self._make_ctx(section_results=[_section_result()])
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.residual_replay_bug_sections) == 1

    def test_stale_sections_include_oracle_incorrect_diagnoses(self) -> None:
        ctx = self._make_ctx(
            section_results=[
                _section_result(diagnosis="ORACLE_STALE"),
                _section_result(section="section:2", diagnosis="REPLAY_MISSING"),
            ]
        )
        partition = partition_replay_bug_sections(ctx)
        # One oracle-incorrect in stale, one in replay pool
        stale_sections = [bm.seed.section for bm in partition.stale_sections]
        assert "section:1" in stale_sections
        assert len(partition.initial_pool) == 1  # only REPLAY_MISSING
        assert partition.initial_pool[0].section == "section:2"

    def test_oracle_range_drift_removed_from_pool(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result()],
            oracle_range_matches={
                "section:1": {
                    "oracle_range_section": "chapter:1/section:1-2",
                    "oracle_range_label": "1-2",
                }
            },
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.oracle_range_drift_sections) == 1
        assert len(partition.residual_replay_bug_sections) == 0

    def test_cross_chapter_drift_removed_from_pool(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result()],
            cross_chapter_oracle_matches={
                "section:1": {
                    "oracle_section": "chapter:2/section:1",
                    "oracle_section_score": 0.95,
                    "same_section_score": 0.1,
                }
            },
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.cross_chapter_oracle_drift_sections) == 1
        assert len(partition.residual_replay_bug_sections) == 0

    def test_same_chapter_drift_removed_from_pool(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result()],
            alternative_replay_matches={
                "section:1": {
                    "best_replay_section": "chapter:1/section:2",
                    "best_replay_score": 0.9,
                    "same_section_score": 0.3,
                }
            },
        )
        partition = partition_replay_bug_sections(ctx)
        assert len(partition.same_chapter_drift_sections) == 1
        assert len(partition.residual_replay_bug_sections) == 0


# =========================================================================
# Part 3: Late fallback rules
# =========================================================================


class TestLateFallbackRules:
    def _make_ctx(self, **overrides: Any) -> StatuteEvidenceContext:
        return _ctx_from_kwargs(_base_kwargs(**overrides))

    def test_unanimous_section_claims_oracle_fires(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result(diagnosis="ORACLE_STALE")],
            section_claims=[
                {
                    "section": "section:1",
                    "selected_kind": "oracle_section_stale",
                    "selected_tier": "PROVED_ORACLE_INCORRECT",
                }
            ],
        )
        result = rule_unanimous_section_claims_oracle_or_noncomm(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result[0].kind == "section_claims_unanimously_oracle_incorrect"

    def test_unanimous_section_claims_noncomm_fires(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result(diagnosis="ORACLE_STALE")],
            section_claims=[
                {
                    "section": "section:1",
                    "selected_kind": "html_xml_scope_noncommensurable",
                    "selected_tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                }
            ],
        )
        result = rule_unanimous_section_claims_oracle_or_noncomm(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE

    def test_unanimous_skips_when_mixed_tiers(self) -> None:
        ctx = self._make_ctx(
            section_claims=[
                {"section": "section:1", "selected_kind": "a", "selected_tier": "PROVED_ORACLE_INCORRECT"},
                {"section": "section:2", "selected_kind": "b", "selected_tier": "PROVED_REPLAY_BUG"},
            ]
        )
        assert rule_unanimous_section_claims_oracle_or_noncomm(ctx) == ()

    def test_unanimous_skips_when_no_section_claims(self) -> None:
        ctx = self._make_ctx()
        assert rule_unanimous_section_claims_oracle_or_noncomm(ctx) == ()

    def test_unanimous_section_claims_use_typed_outcomes_without_legacy_rows(self) -> None:
        ctx = self._make_ctx(
            typed_section_results=[
                _typed_section_result(
                    section="section:1",
                    selected_tier=ProofTier.PROVED_ORACLE_INCORRECT,
                    selected_kind="oracle_section_stale",
                )
            ]
        )
        result = rule_unanimous_section_claims_oracle_or_noncomm(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_oracle_body_empty_with_proved_sections_fires(self) -> None:
        """Gap 4: mixed proved + empty-unverified → PROVED_ORACLE_INCORRECT."""
        ctx = self._make_ctx(
            section_claims=[
                {
                    "section": "section:1",
                    "selected_kind": "extra_empty_oracle_explicit_content_absent",
                    "selected_tier": "PROVED_ORACLE_INCORRECT",
                },
                {
                    "section": "section:2",
                    "selected_kind": "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
                    "selected_tier": "UNRESOLVED",
                },
            ]
        )
        result = rule_oracle_body_empty_with_proved_sections(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result[0].kind == "oracle_body_empty_with_proved_sections"
        assert result[0].claim.support["proved_section_count"] == 1
        assert result[0].claim.support["empty_unverified_section_count"] == 1

    def test_oracle_body_empty_skips_when_unresolved_has_other_kinds(self) -> None:
        ctx = self._make_ctx(
            section_claims=[
                {"section": "section:1", "selected_kind": "a", "selected_tier": "PROVED_ORACLE_INCORRECT"},
                {"section": "section:2", "selected_kind": "some_other_kind", "selected_tier": "UNRESOLVED"},
            ]
        )
        assert rule_oracle_body_empty_with_proved_sections(ctx) == ()

    def test_oracle_body_empty_with_proved_sections_uses_typed_outcomes_without_legacy_rows(
        self,
    ) -> None:
        ctx = self._make_ctx(
            typed_section_results=[
                _typed_section_result(
                    section="section:1",
                    selected_tier=ProofTier.PROVED_ORACLE_INCORRECT,
                    selected_kind="oracle_section_stale",
                ),
                _typed_section_result(
                    section="section:2",
                    selected_tier=ProofTier.UNRESOLVED,
                    selected_kind="UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
                ),
            ]
        )
        result = rule_oracle_body_empty_with_proved_sections(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_all_sections_match_fires(self) -> None:
        """Gap 3a: all sections match → PROVED_ORACLE_INCORRECT."""
        ctx = self._make_ctx(
            section_results=[
                _section_result(section="section:1", diagnosis="MATCH"),
                _section_result(section="section:2", diagnosis="MATCH"),
            ]
        )
        result = rule_all_sections_match(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.PROVED_ORACLE_INCORRECT
        assert result[0].kind == "compilation_sections_correct"
        assert result[0].claim.support["section_count"] == 2

    def test_all_sections_match_skips_when_not_all_match(self) -> None:
        ctx = self._make_ctx(
            section_results=[
                _section_result(section="section:1", diagnosis="MATCH"),
                _section_result(section="section:2", diagnosis="REPLAY_MISSING"),
            ]
        )
        assert rule_all_sections_match(ctx) == ()

    def test_all_sections_match_skips_when_no_results(self) -> None:
        ctx = self._make_ctx(section_results=[])
        assert rule_all_sections_match(ctx) == ()

    def test_trivially_empty_fires(self) -> None:
        """Gap 3b: no section results at all."""
        ctx = self._make_ctx(section_results=[])
        result = rule_trivially_empty(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED
        assert result[0].kind == "trivially_empty"

    def test_trivially_empty_skips_when_has_results(self) -> None:
        ctx = self._make_ctx(
            section_results=[_section_result(diagnosis="MATCH")]
        )
        assert rule_trivially_empty(ctx) == ()

    def test_no_strong_claim_always_fires(self) -> None:
        ctx = self._make_ctx()
        result = rule_no_strong_claim(ctx)
        assert len(result) == 1
        assert result[0].tier == ProofTier.UNRESOLVED
        assert result[0].kind == "no_strong_claim"


# =========================================================================
# Part 4: StatuteResolvedClaims primary_tier
# =========================================================================


class TestStatuteResolvedClaims:
    def _make_record(self, tier: ProofTier, kind: str) -> StatuteClaimRecord:
        from lawvm.tools.evidence_statute_rules import _make_pos, _make_unresolved
        if tier == ProofTier.UNRESOLVED:
            claim = _make_unresolved("test", kind, "test_rule", {})
        else:
            claim = _make_pos("test", tier, kind, "test_rule", {})
        return StatuteClaimRecord(claim=claim, summary="test")

    def test_primary_tier_oracle_incorrect(self) -> None:
        resolved = StatuteResolvedClaims(
            claims=(
                self._make_record(ProofTier.PROVED_ORACLE_INCORRECT, "a"),
                self._make_record(ProofTier.PROVED_REPLAY_BUG, "b"),
            )
        )
        assert resolved.primary_tier == ProofTier.PROVED_ORACLE_INCORRECT

    def test_primary_tier_source_pathology_beats_replay(self) -> None:
        resolved = StatuteResolvedClaims(
            claims=(
                self._make_record(ProofTier.PROVED_REPLAY_BUG, "a"),
                self._make_record(ProofTier.PROVED_SOURCE_PATHOLOGY, "b"),
            )
        )
        assert resolved.primary_tier == ProofTier.PROVED_SOURCE_PATHOLOGY

    def test_primary_claims_filtered_correctly(self) -> None:
        resolved = StatuteResolvedClaims(
            claims=(
                self._make_record(ProofTier.PROVED_ORACLE_INCORRECT, "a"),
                self._make_record(ProofTier.PROVED_REPLAY_BUG, "b"),
                self._make_record(ProofTier.PROVED_ORACLE_INCORRECT, "c"),
            )
        )
        assert len(resolved.primary_claims) == 2
        assert all(c.tier == ProofTier.PROVED_ORACLE_INCORRECT for c in resolved.primary_claims)

    def test_to_legacy_claims_produces_correct_keys(self) -> None:
        resolved = StatuteResolvedClaims(
            claims=(self._make_record(ProofTier.UNRESOLVED, "no_strong_claim"),)
        )
        legacy = resolved.to_legacy_claims()
        assert len(legacy) == 1
        claim = legacy[0]
        assert "tier" in claim
        assert "kind" in claim
        assert "summary" in claim
        assert "inference_rule" in claim
        assert "trigger_observations" in claim
        assert "support" in claim


# =========================================================================
# Part 5: Parity tests — legacy vs typed
# =========================================================================


class TestParity:
    """Each test runs both legacy and typed paths and asserts identical output."""

    def test_source_pathology(self) -> None:
        """Source pathology → PROVED_SOURCE_PATHOLOGY."""
        _assert_parity(
            _base_kwargs(
                source_pathologies=[
                    {"code": "MISSING_SOURCE", "source_statute": "2020/50"},
                ]
            )
        )

    def test_html_xml_noncommensurable(self) -> None:
        _assert_parity(
            _base_kwargs(
                html_topology={
                    "noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:5",
                    "missing_from_xml": [],
                    "extra_in_xml": [],
                }
            )
        )

    def test_oracle_cutoff_version_drift(self) -> None:
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result()],
                oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff",
            )
        )

    def test_contingent_effective_sources(self) -> None:
        _assert_parity(
            _base_kwargs(contingent_effective_sources=["2020/50", "2021/60"])
        )

    def test_html_topology_drift(self) -> None:
        """HTML/XML topology drift → PROVED_ORACLE_INCORRECT."""
        _assert_parity(
            _base_kwargs(
                html_topology={
                    "noncommensurable_reason": "",
                    "missing_from_xml": ["4 a §"],
                    "extra_in_xml": [],
                }
            )
        )

    def test_html_fetch_error_is_separate_from_topology_drift(self) -> None:
        claims = _build_proof_claims(
            **_base_kwargs(
                html_topology={
                    "noncommensurable_reason": "",
                    "missing_from_xml": ["4 a §"],
                    "extra_in_xml": [],
                    "html_error": "fetch/parse failed (https://example.test)",
                }
            )
        )
        assert any(c["kind"] == "html_fetch_error" for c in claims)
        assert not any(c["kind"] == "xml_html_topology_drift" for c in claims)

    def test_oracle_stale_sections(self) -> None:
        """Oracle-incorrect diagnosis in section_results → oracle_section_stale."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(diagnosis="ORACLE_STALE"),
                ]
            )
        )

    def test_replay_bug_residual(self) -> None:
        """Replay-bug section → PROVED_REPLAY_BUG."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")]
            )
        )

    def test_preexisting_removes_from_residual(self) -> None:
        """Preexisting section → UNRESOLVED.preexisting.baseline_residue, no replay bug."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_EXTRA")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "preexisting_before_any_drop": True,
                        "baseline_score": 0.97,
                        "first_bad_source": "2019/50",
                    }
                ],
            )
        )

    def test_improved_replay_sections(self) -> None:
        """Improved section → UNRESOLVED.source_underdetermined.amendment_improves_section."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_source_improved_or_equal": True,
                        "blame_before_score": 0.8,
                        "blame_after_score": 0.85,
                    }
                ],
            )
        )

    def test_unsupported_repeal_only(self) -> None:
        """Repeal-only section → PROVED_SOURCE_PATHOLOGY.blamed_source_lacks_payload_support."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="chapter:1/section:1", diagnosis="REPLAY_MISSING")
                ],
                section_bisect=[
                    {
                        "section": "chapter:1/section:1",
                        "blame_source": "2020/100",
                        "blame_only_repeal_without_payload": True,
                        "blame_compiled_actions_for_section": ["repeal"],
                    }
                ],
            )
        )

    def test_payload_prefers_replay(self) -> None:
        """Payload prefers replay → PROVED_SOURCE_PATHOLOGY.blamed_source_payload_prefers_replay."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_payload_prefers_replay": True,
                        "blame_payload_vs_replay_score": 0.95,
                        "blame_payload_vs_oracle_score": 0.5,
                    }
                ],
            )
        )

    def test_oracle_range_drift(self) -> None:
        """Oracle range match → PROVED_ORACLE_INCORRECT.same_chapter_oracle_range_drift."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                oracle_range_matches={
                    "section:1": {
                        "oracle_range_section": "chapter:1/section:1-2",
                        "oracle_range_label": "1-2",
                    }
                },
            )
        )

    def test_cross_chapter_oracle_drift(self) -> None:
        """Cross-chapter oracle match → UNRESOLVED.cross_chapter_oracle_drift."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                cross_chapter_oracle_matches={
                    "section:1": {
                        "oracle_section": "chapter:2/section:1",
                        "oracle_section_score": 0.9,
                        "same_section_score": 0.1,
                    }
                },
            )
        )

    def test_same_chapter_drift(self) -> None:
        """Same-chapter alternative match → UNRESOLVED.same_chapter_replay_drift."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                alternative_replay_matches={
                    "section:1": {
                        "best_replay_section": "chapter:1/section:2",
                        "best_replay_score": 0.88,
                        "same_section_score": 0.3,
                    }
                },
            )
        )

    def test_section_claims_gate_removes_replay_bug(self) -> None:
        """section_claims gates residual → no PROVED_REPLAY_BUG when not selected."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="section:4", diagnosis="REPLAY_MISSING"),
                ],
                section_claims=[
                    {
                        "section": "section:4",
                        "selected_kind": "oracle_section_stale",
                        "selected_tier": "PROVED_ORACLE_INCORRECT",
                    }
                ],
            )
        )

    def test_all_sections_match_gap3a(self) -> None:
        """Gap 3a: all sections match → compilation_sections_correct."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="section:1", diagnosis="MATCH",
                                    replay_text="same", oracle_text="same"),
                    _section_result(section="section:2", diagnosis="MATCH",
                                    replay_text="other", oracle_text="other"),
                ]
            )
        )

    def test_trivially_empty_gap3b(self) -> None:
        """Gap 3b: no section results → trivially_empty."""
        _assert_parity(_base_kwargs())

    def test_no_strong_claim_fallback(self) -> None:
        """When section_claims provided but no replay sections remain → no_strong_claim."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="section:4", diagnosis="REPLAY_MISSING"),
                ],
                section_claims=[
                    {
                        "section": "section:4",
                        "selected_kind": "oracle_section_stale",
                        "selected_tier": "PROVED_ORACLE_INCORRECT",
                    }
                ],
            )
        )

    def test_unanimous_oracle_gap1(self) -> None:
        """Gap 1: all sections unanimously oracle-incorrect."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    # ORACLE_STALE is in _ORACLE_INCORRECT_DIAGNOSES
                    _section_result(section="section:1", diagnosis="ORACLE_STALE"),
                ],
                section_claims=[
                    {
                        "section": "section:1",
                        "selected_kind": "oracle_section_stale",
                        "selected_tier": "PROVED_ORACLE_INCORRECT",
                    }
                ],
            )
        )

    def test_gap4_mixed_proved_empty_unverified(self) -> None:
        """Gap 4: mixed proved + empty-unverified → oracle_body_empty_with_proved_sections."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="section:1", diagnosis="ORACLE_STALE"),
                    _section_result(section="section:2", diagnosis="EXTRA",
                                    oracle_text="", replay_text="content"),
                ],
                section_claims=[
                    {
                        "section": "section:1",
                        "selected_kind": "extra_empty_oracle_explicit_content_absent",
                        "selected_tier": "PROVED_ORACLE_INCORRECT",
                    },
                    {
                        "section": "section:2",
                        "selected_kind": "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
                        "selected_tier": "UNRESOLVED",
                    },
                ],
            )
        )

    def test_multiple_claim_types_combined(self) -> None:
        """Multiple claim types at once — order must match legacy."""
        _assert_parity(
            _base_kwargs(
                section_results=[
                    _section_result(section="section:1", diagnosis="ORACLE_STALE"),
                    _section_result(section="section:2", diagnosis="REPLAY_MISSING"),
                ],
                source_pathologies=[
                    {"code": "MISSING_SOURCE", "source_statute": "2020/50"},
                ],
                oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff",
            )
        )

    def test_deterministic_sparse_stale_section(self) -> None:
        """Deterministic sparse stale section moves to stale, not residual."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "preexisting_before_any_drop": False,
                        "blame_payload_prefers_replay": False,
                        "blame_only_repeal_without_payload": False,
                        "first_drop_source": "2018/50",
                        "worst_drops": [
                            {"source_id": "2018/50"},
                            {"source_id": "2020/100"},
                        ],
                        "first_drop_elaboration_kinds": [
                            "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                        ],
                        "blame_elaboration_kinds": [
                            "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                        ],
                        "first_drop_sparse_slot_binding_count": 1,
                        "blame_sparse_slot_binding_count": 1,
                        "first_drop_sparse_leftover_count": 0,
                        "blame_sparse_leftover_count": 0,
                        "first_drop_apply_helpers_for_section": [
                            "_apply_deterministic_subsection_op",
                        ],
                        "blame_apply_helpers_for_section": [
                            "_apply_deterministic_subsection_op",
                        ],
                    }
                ],
            )
        )

    def test_elaboration_ambiguity(self) -> None:
        """Same-section elaboration ambiguity → UNRESOLVED.elaboration_ambiguity."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "blame_sparse_elaboration": True,
                        "blame_elaboration_kinds": ["ELAB.A"],
                        "blame_sparse_slot_binding_count": 1,
                        "blame_sparse_slot_binding_labels": ["a"],
                        "blame_sparse_leftover_count": 0,
                        "blame_apply_helpers_for_section": ["h1"],
                    }
                ],
            )
        )

    def test_baseline_same_chapter_drift(self) -> None:
        """Baseline same-chapter drift → UNRESOLVED.address_projection.same_chapter_section_drift."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "baseline_alternative_replay_match": {
                            "best_replay_section": "chapter:1/section:3",
                            "best_replay_score": 0.88,
                            "same_section_score": 0.4,
                        },
                    }
                ],
            )
        )

    def test_baseline_same_section_structure_drift(self) -> None:
        """Baseline structure drift → UNRESOLVED.preexisting.same_section_structure_drift."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="REPLAY_MISSING")],
                section_bisect=[
                    {
                        "section": "section:1",
                        "blame_source": "2020/100",
                        "baseline_unmatched_oracle_subsections": {
                            "count": 2,
                            "oracle_text_excerpts": ["a", "b"],
                            "max_best_replay_score": 0.3,
                        },
                    }
                ],
            )
        )

    def test_corrigendum_support_in_oracle_rollup(self) -> None:
        """Corrigendum support included in oracle_support rollup."""
        _assert_parity(
            _base_kwargs(
                section_results=[_section_result(diagnosis="ORACLE_STALE")],
                corrigendum_support=[
                    {
                        "statute_id": "2020/100",
                        "official_item_count": 1,
                        "manual_override_count": 0,
                    }
                ],
            )
        )

    def test_improved_before_unsupported_in_emit_order(self) -> None:
        """Improved claim must appear BEFORE unsupported in the output (legacy emit order)."""
        # This tests the key ordering difference between partition and emit order.
        section_results = [
            _section_result(section="section:1", diagnosis="REPLAY_MISSING",
                            blame_source="2020/100"),
            _section_result(section="chapter:1/section:2", diagnosis="REPLAY_MISSING",
                            blame_source="2021/200"),
        ]
        kwargs = _base_kwargs(
            section_results=section_results,
            section_bisect=[
                {
                    "section": "section:1",
                    "blame_source": "2020/100",
                    "blame_source_improved_or_equal": True,
                    "blame_before_score": 0.7,
                    "blame_after_score": 0.8,
                },
                {
                    "section": "chapter:1/section:2",
                    "blame_source": "2021/200",
                    "chapter": "chapter:1",
                    "blame_only_repeal_without_payload": True,
                    "blame_compiled_actions_for_section": ["repeal"],
                },
            ],
        )
        legacy = _build_proof_claims(**kwargs)
        typed = build_proof_claims_typed(**kwargs)

        # Both must produce the same result
        assert typed == legacy

        # Improved must come before unsupported
        kinds = [c["kind"] for c in legacy]
        improved_idx = kinds.index("UNRESOLVED.source_underdetermined.amendment_improves_section")
        unsupported_idx = kinds.index("blamed_source_lacks_payload_support")
        assert improved_idx < unsupported_idx, (
            f"Expected improved ({improved_idx}) before unsupported ({unsupported_idx})"
        )
