"""Tests for compile_metadata_verify.py — consumer-side verification.

Covers:
  7. test_consumer_verifies_graph_snapshot_exists
  8. test_consumer_rejects_tampered_strict_profile_fingerprint
"""
from __future__ import annotations
from typing_extensions import override


from lawvm.core.compile_metadata_verify import (
    verify_artifact_metadata,
)
from lawvm.core.provenance_graph_storage import GraphStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_valid_dict(
    graph_hash: str = "a" * 64,
    profile_fp: str = "b" * 64,
    policy_fp: str = "c" * 64,
    bundle_hash: str = "d" * 64,
    reg_hash: str = "e" * 64,
) -> dict:
    return {
        "lawvm.provenance_graph_hash": graph_hash,
        "lawvm.strict_profile_fingerprint": profile_fp,
        "lawvm.evidence_policy_fingerprint": policy_fp,
        "lawvm.source_bundle_hash": bundle_hash,
        "lawvm.attestation_kind_registry_hash": reg_hash,
    }


# ---------------------------------------------------------------------------
# Basic behavior
# ---------------------------------------------------------------------------


def test_verify_artifact_metadata_no_keys_returns_no_metadata() -> None:
    result = verify_artifact_metadata({})
    assert not result.has_metadata
    assert result.metadata is None
    assert len(result.errors) > 0


def test_verify_artifact_metadata_valid_returns_parsed() -> None:
    d = _make_valid_dict()
    result = verify_artifact_metadata(d)
    assert result.has_metadata
    assert result.metadata is not None
    assert result.metadata.provenance_graph_hash == "a" * 64
    assert result.is_valid


def test_verify_artifact_metadata_missing_required_key() -> None:
    d = {"lawvm.provenance_graph_hash": "a" * 64}
    result = verify_artifact_metadata(d)
    assert result.has_metadata
    assert result.metadata is None
    assert not result.is_valid


# ---------------------------------------------------------------------------
# Test 7: consumer verifies graph snapshot exists
# ---------------------------------------------------------------------------


class _FakeGraphStore(GraphStore):
    """Minimal stub implementing snapshot_exists."""

    def __init__(self, known_hashes: frozenset[str]) -> None:
        self._known = known_hashes

    @override
    def snapshot_exists(self, snapshot_hash: str) -> bool:
        return snapshot_hash in self._known


def test_consumer_verifies_graph_snapshot_exists() -> None:
    graph_hash = "a" * 64
    d = _make_valid_dict(graph_hash=graph_hash)
    store = _FakeGraphStore(frozenset({graph_hash}))

    result = verify_artifact_metadata(d, graph_store=store)

    assert result.graph_snapshot_exists is True
    assert result.is_valid


def test_consumer_detects_missing_graph_snapshot() -> None:
    graph_hash = "a" * 64
    d = _make_valid_dict(graph_hash=graph_hash)
    store = _FakeGraphStore(frozenset())  # empty — no known snapshots

    result = verify_artifact_metadata(d, graph_store=store)

    assert result.graph_snapshot_exists is False
    assert not result.is_valid
    assert any("not found" in e for e in result.errors)


def test_graph_snapshot_check_skipped_when_no_store() -> None:
    d = _make_valid_dict()
    result = verify_artifact_metadata(d, graph_store=None)
    assert result.graph_snapshot_exists is None
    assert result.is_valid


# ---------------------------------------------------------------------------
# Test 8: consumer rejects tampered strict_profile_fingerprint
# ---------------------------------------------------------------------------


def test_consumer_rejects_tampered_strict_profile_fingerprint() -> None:
    d = _make_valid_dict(profile_fp="b" * 64)
    expected_fp = "c" * 64  # different from what's stored

    result = verify_artifact_metadata(
        d,
        expected_strict_profile_fingerprint=expected_fp,
    )

    assert result.matches_expected_profile is False
    assert not result.is_valid
    assert any("strict_profile_fingerprint mismatch" in e for e in result.errors)


def test_consumer_accepts_matching_strict_profile_fingerprint() -> None:
    profile_fp = "b" * 64
    d = _make_valid_dict(profile_fp=profile_fp)

    result = verify_artifact_metadata(
        d,
        expected_strict_profile_fingerprint=profile_fp,
    )

    assert result.matches_expected_profile is True
    assert result.is_valid


def test_consumer_skips_profile_check_when_not_provided() -> None:
    d = _make_valid_dict()
    result = verify_artifact_metadata(d, expected_strict_profile_fingerprint=None)
    assert result.matches_expected_profile is None


def test_consumer_rejects_tampered_evidence_policy_fingerprint() -> None:
    d = _make_valid_dict(policy_fp="c" * 64)
    expected = "d" * 64

    result = verify_artifact_metadata(
        d,
        expected_evidence_policy_fingerprint=expected,
    )

    assert result.matches_expected_policy is False
    assert not result.is_valid
    assert any("evidence_policy_fingerprint mismatch" in e for e in result.errors)


# ---------------------------------------------------------------------------
# All checks in combination
# ---------------------------------------------------------------------------


def test_verify_artifact_metadata_all_checks_pass() -> None:
    graph_hash = "a" * 64
    profile_fp = "b" * 64
    policy_fp = "c" * 64
    d = _make_valid_dict(graph_hash=graph_hash, profile_fp=profile_fp, policy_fp=policy_fp)
    store = _FakeGraphStore(frozenset({graph_hash}))

    result = verify_artifact_metadata(
        d,
        graph_store=store,
        expected_strict_profile_fingerprint=profile_fp,
        expected_evidence_policy_fingerprint=policy_fp,
    )

    assert result.is_valid
    assert result.graph_snapshot_exists is True
    assert result.matches_expected_profile is True
    assert result.matches_expected_policy is True
    assert result.errors == ()
