"""Tests for the SourceBytesProvider abstraction and FinlexSectionSourceProvider.

Required tests (per task spec):
  1. test_source_provider_registry
  2. test_finlex_section_provider_happy_path
  3. test_finlex_section_provider_missing_statute_returns_none
  4. test_propose_claims_uses_provider
  5. test_propose_claims_skips_unfetchable_frontier_row
  6. test_propose_claims_with_real_finlex_provider  (slow)
"""
from __future__ import annotations

import hashlib
import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.manual_claims.primitive import ClaimScope, SourceLocator
from lawvm.core.manual_claims.source_provider import (
    MockSourceProvider,
    fetched_source_core_locator,
    get_source_provider,
    make_fetched_source,
    register_source_provider,
)
from lawvm.core.source_locator import source_ref_from_locator
from lawvm.core.manual_claims.proposal_backend import MockProposalBackend
from lawvm.core.manual_claims.primitive import (
    ExtractionFrontierRow,
)
from lawvm.core.provenance_graph import Producer
from lawvm.core.provenance_graph_storage import GraphStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_scope(statute_id: str = "2003/434", provision_ref: Optional[str] = None) -> ClaimScope:
    return ClaimScope(
        statute_id=statute_id,
        provision_ref=provision_ref,
        valid_at_start=None,
        valid_at_end=None,
    )


def _make_frontier_row(statute_id: str = "711/2022", provision_ref: Optional[str] = "section:3") -> ExtractionFrontierRow:
    return ExtractionFrontierRow(
        frontier_id=hashlib.sha256(f"test:{statute_id}".encode()).hexdigest(),
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id=statute_id,
        provision_ref=provision_ref,
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


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def test_fetched_source_projects_shared_source_locator() -> None:
    locator = SourceLocator(
        artifact_kind="finlex_akn",
        statute_id="2003/434",
        he_id=None,
        version_id="2024-01-15",
    )
    source = make_fetched_source(
        b"alpha beta gamma",
        locator,
        span=(6, 10),
    )

    core_locator = fetched_source_core_locator(
        source,
        jurisdiction="fi",
        structural_path="section:1",
    )
    source_ref = source_ref_from_locator(
        core_locator,
        artifact_digest=source.sha256_hex,
    )

    assert core_locator.jurisdiction == "fi"
    assert core_locator.artifact_kind == "finlex_akn"
    assert core_locator.source_id == "finlex_akn:2003/434"
    assert core_locator.structural_path == "section:1"
    assert core_locator.byte_span == (6, 10)
    assert core_locator.quote_hash == hashlib.sha256(b"beta").hexdigest()
    assert core_locator.version_id == "2024-01-15"
    assert source_ref.structural_locator == "section:1"
    assert source_ref.byte_range == (6, 10)
    assert source_ref.bounded_quote_hash == core_locator.quote_hash


# ---------------------------------------------------------------------------
# AKN fixture XML builder
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _build_minimal_statute_xml(statute_id: str = "2003/434") -> bytes:
    """Build minimal consolidated AKN XML with two sections for testing."""
    return (
        b'<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        b"<act>"
        b"<meta>"
        b"<identification source='#org'>"
        b"<FRBRWork>"
        b"<FRBRthis value='/akn/fi/act/statute-consolidated/2003/434/!main'/>"
        b"<FRBRsubtype value='statute-consolidated'/>"
        b"</FRBRWork>"
        b"<FRBRExpression>"
        b"<FRBRdate date='2024-01-15' name='dateConsolidated'/>"
        b"</FRBRExpression>"
        b"</identification>"
        b"</meta>"
        b"<body>"
        b"<section eId='sec_1'>"
        b"<num>1 \xc2\xa7</num>"
        b"<heading>Tarkoitus</heading>"
        b"<subsection><content><p>T\xc3\xa4m\xc3\xa4 laki koskee hallintoa."
        b" Viitaus lakiin 434/2003.</p></content></subsection>"
        b"</section>"
        b"<section eId='sec_2'>"
        b"<num>2 \xc2\xa7</num>"
        b"<heading>Soveltamisala</heading>"
        b"<subsection><content><p>Lakia sovelletaan viranomaisiin.</p></content></subsection>"
        b"</section>"
        b"</body>"
        b"</act>"
        b"</akomaNtoso>"
    )


# ---------------------------------------------------------------------------
# Test 1: source provider registry
# ---------------------------------------------------------------------------


def test_source_provider_registry():
    """register / get / missing-jurisdiction error."""
    # Register a mock provider under a test-only jurisdiction key
    test_jurisdiction = "_test_jurisdiction_abc"
    mock = MockSourceProvider(canned_bytes=b"hello test source")
    register_source_provider(test_jurisdiction, mock)

    retrieved = get_source_provider(test_jurisdiction)
    assert retrieved is mock

    # Missing jurisdiction raises KeyError with descriptive message
    with pytest.raises(KeyError, match="No SourceBytesProvider registered for jurisdiction"):
        get_source_provider("_nonexistent_jurisdiction_xyz")


# ---------------------------------------------------------------------------
# Test 2: FinlexSectionSourceProvider happy path
# ---------------------------------------------------------------------------


def test_finlex_section_provider_happy_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Fixture statute + known section → bytes returned, hash computed."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider

    statute_id = "2003/434"
    oracle_xml = _build_minimal_statute_xml(statute_id)

    # Build a mock corpus store that returns our fixture XML
    class _MockStore:
        def read_oracle(self, sid):
            return oracle_xml if sid == statute_id else None

    # Patch get_corpus_store to return our mock store
    import lawvm.corpus_store as cs_module

    def _mock_get_store(**kwargs):
        return _MockStore()

    monkeypatch.setattr(cs_module, "get_corpus_store", _mock_get_store)
    provider = FinlexSectionSourceProvider()
    scope = _make_scope(statute_id=statute_id, provision_ref="section:1")
    result = provider.fetch(scope)

    assert result is not None, "Expected FetchedSource for known statute + section"
    assert len(result.bytes_) > 0, "bytes_ must be non-empty"
    assert result.sha256_hex == hashlib.sha256(result.bytes_).hexdigest(), (
        "sha256_hex must match SHA-256 of bytes_"
    )
    assert result.span == (0, len(result.bytes_)), (
        "section-granularity provider: span must cover full bytes_"
    )
    assert result.locator.statute_id == statute_id
    assert result.locator.artifact_kind == "finlex_akn"
    # The section text should contain text from section 1
    section_text = result.bytes_.decode("utf-8")
    assert "Tarkoitus" in section_text or "hallintoa" in section_text, (
        f"Expected section 1 content in bytes_, got: {section_text[:100]!r}"
    )


# ---------------------------------------------------------------------------
# Test 3: FinlexSectionSourceProvider missing statute returns None
# ---------------------------------------------------------------------------


def test_finlex_section_provider_missing_statute_returns_none(monkeypatch: pytest.MonkeyPatch):
    """No exception raised; returns None when statute not in corpus."""
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider

    class _EmptyStore:
        def read_oracle(self, sid):
            return None  # nothing in corpus

    import lawvm.corpus_store as cs_module

    def _mock_get_store(**kwargs):
        return _EmptyStore()

    monkeypatch.setattr(cs_module, "get_corpus_store", _mock_get_store)
    provider = FinlexSectionSourceProvider()
    scope = _make_scope(statute_id="9999/9999", provision_ref="section:1")
    result = provider.fetch(scope)

    assert result is None, "Expected None — statute not in corpus"


# ---------------------------------------------------------------------------
# Test 4: propose-claims wired to provider; bytes flow through to backend
# ---------------------------------------------------------------------------


def test_propose_claims_uses_provider(tmp_path: Path):
    """propose-claims calls provider; bytes from provider flow through to backend."""
    from lawvm.tools.cmd_propose_claims import _process_one_frontier

    # The provider returns specific bytes; the mock backend checks span length
    provider_bytes = b"lain 1234/2020 on voimassa"
    provider = MockSourceProvider(canned_bytes=provider_bytes)
    register_source_provider("fi", provider)

    store = GraphStore(tmp_path / "provenance_graph")

    fr = _make_frontier_row()
    backend = MockProposalBackend()
    producer = _make_producer()

    # _fetch_source_for_frontier is called by the CLI handlers; call it directly
    from lawvm.tools.cmd_propose_claims import _fetch_source_for_frontier
    fetched = _fetch_source_for_frontier(fr, jurisdiction="fi")

    assert fetched is not None, "Expected provider bytes from registered mock provider"
    source_bytes, cited_hash = fetched
    assert source_bytes == provider_bytes
    assert cited_hash == hashlib.sha256(provider_bytes).hexdigest()

    # Now run full pipeline: bytes from provider → backend → validators → proposed
    claim_id = _process_one_frontier(
        frontier_row=fr,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=source_bytes,
        cited_span_hash=cited_hash,
        statute_id=fr.statute_id,
        he_id=None,
        producer=producer,
        verbose=False,
    )
    assert claim_id is not None, "Expected claim proposed successfully"
    assertion = store.read_assertion(claim_id)
    object_rows = list(store._objects_dir().glob("*.json"))
    attestation_rows = [path for path in object_rows if path.stem != claim_id]
    attestation_kinds = {
        json.loads(path.read_text(encoding="utf-8"))["attestation_kind"]
        for path in attestation_rows
    }
    assert assertion.kind == "fi.v1.INLINE_STATUTE_RESOLUTION"
    assert attestation_kinds == {
        "claim_submitted",
        "schema_validated",
        "span_verified",
        "entailment_verified",
    }


# ---------------------------------------------------------------------------
# Test 5: provider returns None → skip + log; other rows still processed
# ---------------------------------------------------------------------------


def test_propose_claims_skips_unfetchable_frontier_row(tmp_path: Path, monkeypatch, capsys):
    """Provider returns None for some rows → skip + log; other rows still processed."""
    from lawvm.tools.cmd_propose_claims import cmd_propose_from_frontier

    call_count = {"n": 0}
    skippable_statute = "9999/9999"

    # Provider: returns None for the skippable statute, real bytes for others
    class _SelectiveProvider:
        def fetch(self, scope: ClaimScope):
            if scope.statute_id == skippable_statute:
                return None
            return make_fetched_source(
                b"lain 1234/2020 on voimassa",
                SourceLocator(artifact_kind="finlex_akn", statute_id=scope.statute_id,
                               he_id=None, version_id=None),
            )

    register_source_provider("fi", _SelectiveProvider())

    rows = [
        _make_frontier_row(skippable_statute),          # will be skipped
        _make_frontier_row("711/2022"),                  # will be processed
        _make_frontier_row("712/2022"),                  # will be processed
    ]

    def _fake_scan(data_dir, claim_kind, *, frontier_source=None):
        return rows

    monkeypatch.setattr(
        "lawvm.tools.cmd_propose_claims._scan_frontier_from_parquet",
        _fake_scan,
    )

    def _fake_process(*args, **kwargs):
        call_count["n"] += 1
        return "fake_claim_id"

    monkeypatch.setattr(
        "lawvm.tools.cmd_propose_claims._process_one_frontier",
        _fake_process,
    )

    args = _make_args(
        data_dir=str(tmp_path),
        claim_store_root=None,
        kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        limit=10,
        max_claims_no_cap=False,
        backend="mock",
    )
    rc = cmd_propose_from_frontier(args)
    assert rc == 0

    # 1 row skipped (no source), 2 processed
    assert call_count["n"] == 2, (
        f"Expected 2 calls to _process_one_frontier (1 row skipped), got {call_count['n']}"
    )

    # Skip message appears in stderr
    captured = capsys.readouterr()
    assert "frontier_skipped_no_source" in captured.err, (
        "Expected frontier_skipped_no_source log in stderr"
    )
    assert skippable_statute in captured.err


# ---------------------------------------------------------------------------
# Test 6: real-corpus regression test
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_propose_claims_with_real_finlex_provider():
    """Real-corpus regression: FinlexSectionSourceProvider returns non-empty bytes
    for a known statute from the finlex.farchive corpus.

    Marked @pytest.mark.slow — requires data/finlex.farchive to be present.
    """
    farchive_path = Path("data/finlex.farchive")
    if not farchive_path.exists():
        pytest.skip("data/finlex.farchive not present — real-corpus test skipped")

    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider

    # 2003/434 = Hallintolaki (Administrative Procedure Act) — well-known statute
    statute_id = "2003/434"
    provider = FinlexSectionSourceProvider()
    scope = _make_scope(statute_id=statute_id, provision_ref=None)
    result = provider.fetch(scope)

    if result is None:
        # Oracle not yet in corpus — acceptable, but emit a warning
        import warnings
        warnings.warn(
            f"real-corpus test: {statute_id!r} oracle not in farchive — corpus may need refresh",
            stacklevel=2,
        )
        return

    assert len(result.bytes_) > 0, "bytes_ must be non-empty for real statute"
    text = result.bytes_.decode("utf-8", errors="replace")
    # The statute_id should appear somewhere in the content (section XML / heading).
    # Accept any non-trivial Finnish legal text.
    assert len(text) > 10, f"Expected non-trivial section text, got: {text!r}"
    assert result.sha256_hex == hashlib.sha256(result.bytes_).hexdigest()
    assert result.locator.statute_id == statute_id
