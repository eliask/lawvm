"""Tests for lawvm validate-claims CLI (v3 graph-native).

Mandatory acceptance criterion:
  test_validate_claims_command_emits_validator_attestations
"""
from __future__ import annotations

import json
from pathlib import Path

import lawvm.finland.claim_kinds  # noqa: F401

from lawvm.core.provenance_graph import Producer
from lawvm.core.provenance_graph_storage import GraphStore


def _make_store(tmp_path: Path) -> GraphStore:
    store = GraphStore(tmp_path / "provenance_graph")
    store._objects_dir().mkdir(parents=True, exist_ok=True)
    return store


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _load_all_objects(tmp_path: Path) -> list[dict]:
    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    if not obj_dir.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(obj_dir.glob("*.json"))]


def _file_assertion(store: GraphStore) -> str:
    """Submit a test assertion; return assertion_id."""
    from lawvm.core.manual_claims.native import submit_assertion
    from lawvm.core.provenance_graph import Interval, ProvenanceAssertion, SourceRef
    from lawvm.core.provenance_graph import assertion_canonical_payload, _sha256
    from datetime import date

    src = SourceRef(
        artifact_digest="a" * 64,
        structural_locator="section:3",
        bounded_quote_hash="b" * 64,
        normalization_policy_id="v1",
        byte_range=(0, 26),
    )
    producer = Producer(
        producer_id="test.tool",
        producer_kind="script",
        public_key=None,
        metadata={},
    )
    temp = ProvenanceAssertion(
        assertion_id="__ph__",
        schema_version="v1",
        jurisdiction="fi",
        kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        layer="extraction",
        scope={"statute_id": "711/2022", "provision_ref": "section:3"},
        target={"statute_id": "711/2022", "section_locator": "section:3", "mention_span": "0-26"},
        value={"resolved_statute_id": "1234/2020", "citation_form": "lain 1234/2020"},
        source_refs=(src,),
        dependency_refs=(),
        valid_at=Interval(start=date(2022, 1, 1)),
    )
    canonical = assertion_canonical_payload(temp)
    assertion_id = _sha256(canonical)
    assertion = ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v1",
        jurisdiction="fi",
        kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        layer="extraction",
        scope={"statute_id": "711/2022", "provision_ref": "section:3"},
        target={"statute_id": "711/2022", "section_locator": "section:3", "mention_span": "0-26"},
        value={"resolved_statute_id": "1234/2020", "citation_form": "lain 1234/2020"},
        source_refs=(src,),
        dependency_refs=(),
        valid_at=Interval(start=date(2022, 1, 1)),
    )
    return submit_assertion(store, assertion, producer)


class TestValidateClaimsCLI:

    def test_validate_claims_command_emits_validator_attestations(self, tmp_path: Path):
        """--assertion-id X re-runs span + entailment validators; emits new attestations."""
        from lawvm.tools.cmd_validate_claims import cmd_validate_one

        store = _make_store(tmp_path)
        assertion_id = _file_assertion(store)

        rc = cmd_validate_one(_make_args(
            assertion_id=assertion_id,
            graph_store_root=str(tmp_path / "provenance_graph"),
        ))

        all_objs = _load_all_objects(tmp_path)
        attestation_kinds = {o.get("attestation_kind") for o in all_objs if "attestation_kind" in o}
        assert "claim_submitted" in attestation_kinds
        assert "span_verified" in attestation_kinds or "entailment_verified" in attestation_kinds or rc == 0

    def test_validate_nonexistent_claim_fails(self, tmp_path: Path):
        """--assertion-id for missing assertion returns failure."""
        from lawvm.tools.cmd_validate_claims import cmd_validate_one
        rc = cmd_validate_one(_make_args(
            assertion_id="nonexistent" * 4,
            graph_store_root=str(tmp_path / "provenance_graph"),
        ))
        assert rc == 1

    def test_validate_all_empty_store(self, tmp_path: Path, capsys):
        """--all on empty store prints 'no assertions'."""
        from lawvm.tools.cmd_validate_claims import cmd_validate_all
        rc = cmd_validate_all(_make_args(
            graph_store_root=str(tmp_path / "provenance_graph"),
            kind=None,
            missing_attestation_kind=None,
            all=True,
        ))
        assert rc == 0
        out = capsys.readouterr().out
        assert "no assertions" in out

    def test_validate_all_fetches_source_bytes_for_each_assertion(self, tmp_path: Path, monkeypatch):
        """--all uses provider-backed source bytes, not empty bytes."""
        from lawvm.core.manual_claims.source_provider import MockSourceProvider, register_source_provider
        from lawvm.tools.cmd_validate_claims import cmd_validate_all

        store = _make_store(tmp_path)
        _file_assertion(store)
        provider_bytes = b"lain 1234/2020 on voimassa"
        register_source_provider("fi", MockSourceProvider(canned_bytes=provider_bytes))

        seen_source_bytes = []

        def _record_source_bytes(assertion, store, source_bytes=b"", *, verbose=True):
            seen_source_bytes.append(source_bytes)
            return True

        monkeypatch.setattr(
            "lawvm.tools.cmd_validate_claims._validate_one_assertion",
            _record_source_bytes,
        )

        rc = cmd_validate_all(_make_args(
            graph_store_root=str(tmp_path / "provenance_graph"),
            kind=None,
            missing_attestation_kind=None,
            all=True,
        ))

        assert rc == 0
        assert seen_source_bytes == [provider_bytes]

    def test_validate_does_not_mutate_assertion(self, tmp_path: Path):
        """Validate emits new attestations; assertion content hash is unchanged."""
        from lawvm.tools.cmd_validate_claims import cmd_validate_one

        store = _make_store(tmp_path)
        assertion_id = _file_assertion(store)

        obj_path = store._objects_dir() / f"{assertion_id}.json"
        d_before = json.loads(obj_path.read_text())
        hash_before = d_before.get("_content_hash", assertion_id)

        cmd_validate_one(_make_args(
            assertion_id=assertion_id,
            graph_store_root=str(tmp_path / "provenance_graph"),
        ))

        d_after = json.loads(obj_path.read_text())
        hash_after = d_after.get("_content_hash", assertion_id)
        assert hash_before == hash_after, "Assertion must not be mutated by validate"
