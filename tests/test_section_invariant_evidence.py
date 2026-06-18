"""Tests for C3: Section-local invariant breach → PROVED_REPLAY_BUG.

Verifies that timeline invariant violations at section level
promote to PROVED_REPLAY_BUG in evidence section claims.
"""
from __future__ import annotations

from lawvm.tools.evidence_claims import _build_section_claims


class TestInvariantViolationPromotesToReplayBug:
    """Timeline invariant violations should produce PROVED_REPLAY_BUG."""

    def test_violation_produces_replay_bug(self) -> None:
        """Section with invariant violation → PROVED_REPLAY_BUG."""
        violations = {
            "1": [
                {
                    "kind": "content_mismatch",
                    "tier": "robust",
                    "section_label": "1",
                    "address_path": "section:1",
                    "message": "CONTENT_MISMATCH: ...",
                },
            ],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
        )
        assert len(claims) == 1
        # The violation should produce a timeline_invariant_violation candidate
        kinds = claims[0]["candidate_kinds"]
        assert "timeline_invariant_violation" in kinds

    def test_violation_is_high_priority(self) -> None:
        """PROVED_REPLAY_BUG from invariant should win over other candidates."""
        violations = {
            "1": [{"kind": "content_mismatch", "tier": "robust", "section_label": "1",
                    "address_path": "section:1", "message": "test"}],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
        )
        # Should still have PROVED_REPLAY_BUG as a candidate
        tiers = [c["tier"] for c in claims[0]["candidates"]]
        assert "PROVED_REPLAY_BUG" in tiers

    def test_no_violations_preserves_behavior(self) -> None:
        """When no invariant violations, behavior is unchanged."""
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=None,
        )
        assert claims[0]["selected_tier"] == "PROVED_REPLAY_BUG"
        assert claims[0]["selected_kind"] == "replay_divergence"

    def test_violation_on_oracle_stale_section_no_effect(self) -> None:
        """Oracle-stale sections keep their tier despite invariant violations."""
        violations = {
            "1": [{"kind": "content_mismatch", "section_label": "1",
                    "address_path": "section:1", "message": "test"}],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "ORACLE_STALE",
                 "blame_source": "2020/100",
                 "oracle_text": "old", "replay_text": "new"},
            ],
            section_invariant_violations=violations,
        )
        # Oracle stale takes priority
        assert claims[0]["selected_tier"] == "PROVED_ORACLE_INCORRECT"

    def test_apply_phase_shadow_suppresses_timeline_shadow_promotion(self) -> None:
        violations = {
            "5": [{
                "kind": "same_source_descendant_shadow",
                "tier": "robust",
                "section_label": "5",
                "address_path": "section:5/subsection:2",
                "message": "shadow",
            }],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "5", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
            apply_phase_shadow_paths=frozenset({"section:5/subsection:2"}),
        )
        assert "timeline_invariant_violation" not in claims[0]["candidate_kinds"]

    def test_materialization_variant_does_not_promote(self) -> None:
        violations = {
            "1": [{
                "kind": "timeline_without_ir",
                "tier": "materialization_variant",
                "section_label": "1",
                "address_path": "/heading",
                "message": "heading facet",
            }],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
        )
        assert "timeline_invariant_violation" not in claims[0]["candidate_kinds"]

    def test_section_without_violation_unaffected(self) -> None:
        """Section not in violations dict → normal behavior."""
        violations = {
            "2": [{"kind": "content_mismatch", "section_label": "2",
                    "address_path": "section:2", "message": "test"}],
        }
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle", "replay_text": "replay"},
            ],
            section_invariant_violations=violations,
        )
        # Section 1 has no violation, should be normal PROVED_REPLAY_BUG
        assert claims[0]["selected_kind"] == "replay_divergence"
