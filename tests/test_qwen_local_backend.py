"""Tests for QwenLocalBackend (Slice 4).

Test 9 (optional, marked slow + requires_local_llm):
  test_qwen_local_backend_smoke — actually hits http://localhost:11434.
  Skip with informative message if server not reachable.
"""
from __future__ import annotations

import hashlib

import pytest

import lawvm.finland.claim_kinds  # noqa: F401

from lawvm.core.manual_claims.primitive import ExtractionFrontierRow
from lawvm.core.manual_claims.proposal_backend import ClaimSchema, QuotedSource
from datetime import datetime, timezone


def _make_frontier_row() -> ExtractionFrontierRow:
    return ExtractionFrontierRow(
        frontier_id="smoke-test-frontier",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id="711/2022",
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="smoke",
    )


def _make_schema() -> ClaimSchema:
    return ClaimSchema(
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required_value_fields=("resolved_statute_id", "citation_form"),
        json_schema_dict=None,
        natural_language_description=(
            "JSON with resolved_statute_id (Finnish NNNN/YYYY) and citation_form."
        ),
    )


def _make_quoted_source() -> QuotedSource:
    text = b"Laki perustuu lakiin 434/2003 (hallintolaki)."
    return QuotedSource(
        artifact_kind="finlex_akn",
        statute_id="711/2022",
        he_id=None,
        cited_span_bytes=text,
        cited_span_hash=hashlib.sha256(text).hexdigest(),
    )


def _is_server_reachable() -> bool:
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://localhost:11434/v1/chat/completions",
            data=b'{"model":"test","messages":[{"role":"user","content":"ping"}],"max_tokens":1}',
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return True
    except Exception:
        return False


@pytest.mark.slow
@pytest.mark.requires_local_llm
def test_qwen_local_backend_smoke():
    """Actually hit http://localhost:11434. Skip if server not reachable."""
    if not _is_server_reachable():
        pytest.skip(
            "Local LLM server not reachable at http://localhost:11434. "
            "Start the llama.cpp server with Qwen3.6 27b before running this test."
        )

    from lawvm.finland.llm_backends.qwen_local import QwenLocalBackend

    backend = QwenLocalBackend()
    fr = _make_frontier_row()
    schema = _make_schema()
    source = _make_quoted_source()

    result = backend.propose(fr, schema, source)

    assert result.claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION"
    assert result.producer_model_id != ""

    if result.parse_error is None:
        value_dict = dict(result.value)
        assert "resolved_statute_id" in value_dict
        assert "citation_form" in value_dict
        assert "/" in value_dict.get("resolved_statute_id", ""), (
            "resolved_statute_id should be NNNN/YYYY format"
        )
    else:
        # Parse failure is acceptable in smoke test — we just verify no crash
        print(f"  parse_error: {result.parse_error}")
        print(f"  raw_response: {result.raw_response[:200]}")
