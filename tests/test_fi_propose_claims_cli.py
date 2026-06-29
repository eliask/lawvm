"""Tests for lawvm propose-claims CLI (v3 graph-native).

Mandatory acceptance criteria:
  1. test_mock_backend_proposes_valid_claim
  2. test_validator_catches_malformed_llm_output
  3. test_entailment_validator_catches_injection  (LOAD-BEARING)
  4. test_propose_from_frontier_skips_already_accepted_targets
  5. test_propose_claims_respects_limit
  6. test_duplicate_proposal_is_idempotent
  7. test_gap_discovery_finds_missed_citations
"""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path


importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.manual_claims.primitive import ExtractionFrontierRow
from lawvm.core.manual_claims.proposal_backend import (
    MockProposalBackend,
)
from lawvm.core.provenance_graph import Producer
from lawvm.core.provenance_graph_storage import GraphStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path: Path) -> GraphStore:
    store = GraphStore(tmp_path / "provenance_graph")
    store._objects_dir().mkdir(parents=True, exist_ok=True)
    return store


def _make_frontier_row(statute_id: str = "711/2022") -> ExtractionFrontierRow:
    return ExtractionFrontierRow(
        frontier_id=hashlib.sha256(f"frontier:{statute_id}".encode()).hexdigest(),
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id=statute_id,
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="test",
    )


def _make_producer() -> Producer:
    return Producer(
        producer_id="test.tool",
        producer_kind="script",
        public_key=None,
        metadata={},
    )


def _load_all_objects(tmp_path: Path) -> list[dict]:
    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    if not obj_dir.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(obj_dir.glob("*.json"))]


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
# Test 1: mock backend proposes valid claim
# ---------------------------------------------------------------------------


def test_mock_backend_proposes_valid_claim(tmp_path: Path):
    """Mock backend proposes a well-formed assertion; validators pass; stored in graph."""
    from lawvm.tools.cmd_propose_claims import _process_one_frontier

    store = _make_store(tmp_path)
    fr = _make_frontier_row()
    source_bytes = b"lain 1234/2020 on voimassa"
    cited_hash = hashlib.sha256(source_bytes).hexdigest()
    backend = MockProposalBackend()
    producer = _make_producer()

    assertion_id = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert assertion_id is not None, "Expected an assertion_id from successful proposal"

    all_objs = _load_all_objects(tmp_path)
    assertion_objs = [o for o in all_objs if "assertion_id" in o and "kind" in o]
    assert len(assertion_objs) == 1

    attestation_kinds = {o.get("attestation_kind") for o in all_objs if "attestation_kind" in o}
    assert "claim_submitted" in attestation_kinds


# ---------------------------------------------------------------------------
# Test 2: validator catches malformed LLM output
# ---------------------------------------------------------------------------


def test_validator_catches_malformed_llm_output(tmp_path: Path):
    """Mock backend simulates parse failure; assertion stored with schema_validated(success=False)."""
    from lawvm.tools.cmd_propose_claims import _process_one_frontier

    store = _make_store(tmp_path)
    fr = _make_frontier_row()
    source_bytes = b"lain 1234/2020 on voimassa"
    cited_hash = hashlib.sha256(source_bytes).hexdigest()
    backend = MockProposalBackend(canned_parse_error="JSON parse error: unexpected token")
    producer = _make_producer()

    assertion_id = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert assertion_id is None, "Expected None — malformed output returns None"

    all_objs = _load_all_objects(tmp_path)
    schema_validated = [
        o for o in all_objs
        if o.get("attestation_kind") == "schema_validated"
        and o.get("payload", {}).get("success") is False
    ]
    assert len(schema_validated) >= 1, "schema_validated(success=False) attestation must exist"


# ---------------------------------------------------------------------------
# Test 3: entailment validator catches injection (LOAD-BEARING)
# ---------------------------------------------------------------------------


def test_entailment_validator_catches_injection(tmp_path: Path):
    """Source span contains 1234/2020; backend returns 9999/9999 (injected).

    Entailment validator must catch this: citation_form=9999/9999 not in span.
    """
    from lawvm.tools.cmd_propose_claims import _process_one_frontier

    store = _make_store(tmp_path)
    source_bytes_clean = b"lain 1234/2020 on voimassa. Katso myos lain tarkoitus."
    cited_hash = hashlib.sha256(source_bytes_clean).hexdigest()
    backend = MockProposalBackend(inject_bad_statute_id="9999/9999")
    fr = _make_frontier_row()
    producer = _make_producer()

    assertion_id = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes_clean,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert assertion_id is None, (
        "Expected rejection: entailment validator must catch the injected statute_id 9999/9999"
    )

    all_objs = _load_all_objects(tmp_path)
    assertion_objs = [o for o in all_objs if "assertion_id" in o and "kind" in o]
    assert len(assertion_objs) >= 1, "Rejected proposal should be stored for audit"


# ---------------------------------------------------------------------------
# Test 4: propose-from-frontier skips already-accepted targets
# ---------------------------------------------------------------------------


def test_propose_from_frontier_skips_already_accepted_targets(tmp_path: Path):
    """A target with an accepted (reviewed+True) assertion is skipped."""
    from lawvm.tools.cmd_propose_claims import _process_one_frontier
    from lawvm.core.manual_claims.native import attest, submit_assertion
    from lawvm.tools.cmd_propose_claims import _build_assertion_from_proposed
    from lawvm.core.manual_claims.proposal_backend import QuotedSource

    store = _make_store(tmp_path)
    source_bytes = b"lain 1234/2020 on voimassa"
    cited_hash = hashlib.sha256(source_bytes).hexdigest()

    fr = _make_frontier_row("711/2022")
    producer = _make_producer()

    backend = MockProposalBackend()
    quoted_source = QuotedSource(
        artifact_kind="finlex_akn",
        statute_id="711/2022",
        he_id=None,
        cited_span_bytes=source_bytes,
        cited_span_hash=cited_hash,
    )
    from lawvm.core.manual_claims.proposal_backend import ClaimSchema
    schema = ClaimSchema(
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        required_value_fields=("resolved_statute_id", "citation_form"),
        json_schema_dict={},
        natural_language_description="",
    )
    proposed = backend.propose(fr, schema, quoted_source)
    assertion = _build_assertion_from_proposed(proposed, fr, quoted_source, producer)

    # Submit + mark as accepted (reviewed+True)
    assertion_id = submit_assertion(store, assertion, producer)
    attest(store, assertion_id, "reviewed", {"accepted": True}, producer)

    result = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert result is None, "Expected None — accepted assertion already covers this target"


# ---------------------------------------------------------------------------
# Test 5: propose-claims respects --limit
# ---------------------------------------------------------------------------


def test_propose_claims_respects_limit(tmp_path: Path, monkeypatch):
    """--limit 3 emits at most 3 proposals when multiple frontier rows exist."""
    from lawvm.core.manual_claims.source_provider import MockSourceProvider, register_source_provider

    register_source_provider("fi", MockSourceProvider())

    # Monkeypatch frontier scanner to return 10 synthetic rows
    def _fake_scan(data_dir, claim_kind, *, frontier_source=None):
        return [_make_frontier_row(f"{i}/2022") for i in range(1000, 1010)]

    monkeypatch.setattr(
        "lawvm.tools.cmd_propose_claims._scan_frontier_from_parquet",
        _fake_scan,
    )

    called = []

    def _fake_process(*args, **kwargs):
        called.append(1)
        return None

    monkeypatch.setattr(
        "lawvm.tools.cmd_propose_claims._process_one_frontier",
        _fake_process,
    )

    from lawvm.tools.cmd_propose_claims import cmd_propose_from_frontier

    args = _make_args(
        data_dir=str(tmp_path),
        graph_store_root=None,
        kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        limit=3,
        max_claims_no_cap=False,
        backend="mock",
    )
    rc = cmd_propose_from_frontier(args)
    assert rc == 0
    assert len(called) == 3, f"Expected 3 calls, got {len(called)}"


# ---------------------------------------------------------------------------
# Test 6: duplicate proposal is idempotent
# ---------------------------------------------------------------------------


def test_duplicate_proposal_is_idempotent(tmp_path: Path):
    """Same (claim_kind, target, value) proposed twice — second call returns existing id."""
    from lawvm.tools.cmd_propose_claims import _process_one_frontier

    store = _make_store(tmp_path)
    fr = _make_frontier_row()
    source_bytes = b"lain 1234/2020 on voimassa"
    cited_hash = hashlib.sha256(source_bytes).hexdigest()
    backend = MockProposalBackend()
    producer = _make_producer()

    id1 = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )
    assert id1 is not None

    id2 = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert id2 == id1, f"Expected idempotent: {id1[:16]}... but got {id2!r}"

    # Only one assertion in store
    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    assertion_objs = [
        json.loads(f.read_text())
        for f in obj_dir.glob("*.json")
        if "assertion_id" in json.loads(f.read_text()) and "kind" in json.loads(f.read_text())
    ]
    assert len(assertion_objs) == 1


# ---------------------------------------------------------------------------
# Test 7: gap discovery finds missed citations
# ---------------------------------------------------------------------------


def test_gap_discovery_finds_missed_citations(tmp_path: Path):
    """HE body with plain-text statute citations; gap-discovery emits GapDiscoveryRow."""
    import unittest.mock as mock
    import lawvm.tools.cmd_propose_claims as cpc

    he_xml = (
        b"<akn:doc><akn:body>"
        b"<p>Laki perustuu lakiin 1234/2020 seka lakiin 5678/2021.</p>"
        b"</akn:body></akn:doc>"
    )

    class _FakeStore:
        def read_oracle(self, statute_id):
            return he_xml

    fake_corpus_module = mock.MagicMock()
    fake_corpus_module.get_corpus_store.return_value = _FakeStore()

    with mock.patch.dict("sys.modules", {"lawvm.finland.corpus": fake_corpus_module}):
        gaps = cpc._discover_gaps_from_he(
            he_id="HE-123-2020",
            data_dir=str(tmp_path),
            claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        )

    assert len(gaps) >= 2, f"Expected at least 2 gaps (1234/2020 and 5678/2021), got {len(gaps)}"
    expected_keys = {g.expected_target_key for g in gaps}
    assert any("1234/2020" in k for k in expected_keys), "Expected gap for 1234/2020"
    assert any("5678/2021" in k for k in expected_keys), "Expected gap for 5678/2021"
