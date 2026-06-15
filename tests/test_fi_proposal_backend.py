"""Tests for ClaimProposalBackend protocol + MockProposalBackend.

Covers:
  - MockProposalBackend basic produce
  - canned_parse_error path
  - inject_bad_statute_id (injection simulation)
  - ClaimSchema + QuotedSource construction
"""
from __future__ import annotations

import hashlib
from lawvm.core.manual_claims.proposal_backend import (
    ClaimSchema,
    MockProposalBackend,
    ProposedClaim,
    QuotedSource,
)
from lawvm.core.manual_claims.primitive import ExtractionFrontierRow
from datetime import datetime, timezone


def _make_frontier_row(statute_id: str = "711/2022") -> ExtractionFrontierRow:
    return ExtractionFrontierRow(
        frontier_id="test-frontier-id",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id=statute_id,
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="test",
    )


def _make_schema() -> ClaimSchema:
    return ClaimSchema(
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required_value_fields=("resolved_statute_id", "citation_form"),
        json_schema_dict=None,
        natural_language_description="Test schema",
    )


def _make_quoted_source(text: bytes = b"lain 1234/2020 on voimassa") -> QuotedSource:
    h = hashlib.sha256(text).hexdigest()
    return QuotedSource(
        artifact_kind="finlex_akn",
        statute_id="711/2022",
        he_id=None,
        cited_span_bytes=text,
        cited_span_hash=h,
    )


class TestMockProposalBackend:

    def test_default_propose_returns_well_formed_claim(self):
        backend = MockProposalBackend()
        fr = _make_frontier_row()
        schema = _make_schema()
        source = _make_quoted_source()

        result = backend.propose(fr, schema, source)

        assert isinstance(result, ProposedClaim)
        assert result.parse_error is None
        assert result.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION"
        assert result.producer_model_id == "mock"
        # Value must contain resolved_statute_id and citation_form
        value_dict = dict(result.value)
        assert "resolved_statute_id" in value_dict
        assert "citation_form" in value_dict

    def test_canned_parse_error_returns_error_claim(self):
        backend = MockProposalBackend(canned_parse_error="malformed JSON from LLM")
        fr = _make_frontier_row()
        schema = _make_schema()
        source = _make_quoted_source()

        result = backend.propose(fr, schema, source)

        assert result.parse_error is not None
        assert "malformed JSON" in result.parse_error
        assert result.target == ()
        assert result.value == ()

    def test_inject_bad_statute_id_returns_wrong_id(self):
        """Injection simulation: backend 'obeys' instruction to use bad statute ID."""
        backend = MockProposalBackend(inject_bad_statute_id="9999/9999")
        fr = _make_frontier_row()
        schema = _make_schema()
        # Source does NOT contain 9999/9999 — only 1234/2020
        source = _make_quoted_source(b"lain 1234/2020 on voimassa")

        result = backend.propose(fr, schema, source)

        assert result.parse_error is None
        value_dict = dict(result.value)
        assert value_dict["resolved_statute_id"] == "9999/9999"
        # The cited span does NOT contain 9999/9999 — entailment validator will catch this

    def test_canned_claim_is_returned_directly(self):
        span_bytes = b"lain 1234/2020"
        canned = ProposedClaim(
            claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
            target=(("statute_id", "711/2022"),),
            value=(("resolved_statute_id", "1234/2020"), ("citation_form", "lain 1234/2020")),
            cited_source_span=(0, len(span_bytes)),
            cited_source_hash=hashlib.sha256(span_bytes).hexdigest(),
            rationale="canned test claim",
            producer_model_id="mock-canned",
            raw_response='{"resolved_statute_id": "1234/2020"}',
            parse_error=None,
        )
        backend = MockProposalBackend(canned_claim=canned)
        fr = _make_frontier_row()
        schema = _make_schema()
        source = _make_quoted_source()

        result = backend.propose(fr, schema, source)
        assert result is canned
