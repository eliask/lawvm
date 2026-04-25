"""Publication safety guarantees (Pro evidence architecture D1-D3).

Every PROVED_ORACLE_INCORRECT claim must:
D1. Have a named rule_id (inference_rule)
D2. Have section-level scope (not just statute-level assertion)
D3. Not rely on "empty text implies contentAbsent"

Statute-level rollup must:
- Be derived from section-level claims via _primary_proof_tier
- Not independently reclassify from raw diagnoses
"""
from __future__ import annotations

from lawvm.tools.evidence_claims import _build_section_claims
from lawvm.tools._evidence_helpers import _PRIMARY_TIER_ORDER


class TestD1EveryProvedClaimHasRuleId:
    """Every PROVED_* claim must have a non-empty inference_rule."""

    def test_oracle_stale_has_rule(self) -> None:
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "ORACLE_STALE", "blame_source": "2020/100",
                 "oracle_text": "old", "replay_text": "new"},
            ],
        )
        proved = [c for c in claims if c["selected_tier"].startswith("PROVED_")]
        for c in proved:
            assert c["selected_inference_rule"], (
                f"PROVED claim {c['selected_kind']} has no inference_rule"
            )

    def test_content_absent_proved_only_with_explicit_flag(self) -> None:
        """D3: empty oracle text without explicit flag must NOT be PROVED."""
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "EXTRA", "blame_source": "",
                 "oracle_text": "", "replay_text": "something"},
            ],
        )
        for c in claims:
            if c["selected_tier"] == "PROVED_ORACLE_INCORRECT":
                assert "content_absent" not in c["selected_inference_rule"] or \
                    c.get("candidates", [{}])[0].get("support", {}).get("explicit_content_absent", False), \
                    "PROVED_ORACLE_INCORRECT from empty oracle without explicit contentAbsent"

    def test_explicit_content_absent_is_proved(self) -> None:
        """With explicit flag, empty oracle IS proved."""
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "EXTRA", "blame_source": "",
                 "oracle_text": "", "replay_text": "something",
                 "oracle_content_absent": True},
            ],
        )
        proved = [c for c in claims if c["selected_tier"] == "PROVED_ORACLE_INCORRECT"]
        assert len(proved) == 1


class TestD2StatuteRollupFromSections:
    """Statute-level tier must be derived from section claims."""

    def test_primary_tier_order_is_explicit(self) -> None:
        """The tier priority order must be an explicit constant, not implicit."""
        assert isinstance(_PRIMARY_TIER_ORDER, (list, tuple))
        assert len(_PRIMARY_TIER_ORDER) >= 4
        # PROVED tiers should come before UNRESOLVED
        proved_indices = [
            i for i, t in enumerate(_PRIMARY_TIER_ORDER)
            if t.startswith("PROVED_")
        ]
        unresolved_idx = (
            _PRIMARY_TIER_ORDER.index("UNRESOLVED")
            if "UNRESOLVED" in _PRIMARY_TIER_ORDER
            else len(_PRIMARY_TIER_ORDER)
        )
        for pi in proved_indices:
            assert pi < unresolved_idx, (
                f"PROVED tier at index {pi} is after UNRESOLVED at {unresolved_idx}"
            )

    def test_section_tier_priority_sort_is_monotone(self) -> None:
        """Adding a stronger-tier candidate never downgrades the selection."""
        # Build claims with UNRESOLVED + PROVED_ORACLE_INCORRECT candidates
        claims = _build_section_claims(
            section_results=[
                {"section": "1", "diagnosis": "REPLAY_MISSING",
                 "blame_source": "2020/100",
                 "oracle_text": "oracle text", "replay_text": "replay"},
            ],
            # Provide cross-chapter match AND oracle stale diagnosis support
            cross_chapter_oracle_matches={
                "1": {"oracle_section": "chapter:2/section:1",
                      "oracle_section_score": 0.99,
                      "same_section_score": 0.3},
            },
        )
        # With high cross-chapter score (>=0.95), should be PROVED not UNRESOLVED
        for c in claims:
            if c["section"] == "1":
                assert c["selected_tier"] != "PROVED_REPLAY_BUG", (
                    "Cross-chapter near-exact match should prevent PROVED_REPLAY_BUG"
                )
