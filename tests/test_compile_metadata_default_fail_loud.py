"""Tests for the §1.10 fail-loud behaviour of ``_default_evidence_policy``
and ``_discover_graph_hash`` (replacing the prior silent
``except Exception: pass`` handlers).

Per AGENTS.md §1.10, an unreadable / malformed on-disk policy file or graph
snapshot must NOT silently become the empty-registry / empty-graph fallback
— that is an invisible heuristic. Instead, the loader emits a distinct named
typed diagnostic (``EvidencePolicyLoadFailure`` /
``GraphSnapshotHashReadFailure``) carrying the offending first ~400 bytes as
a snippet field so a triager can audit the failure from the record alone
(triaging a residual must never require re-running extraction).

No findings sink is available at the CompileMetadata-construction stage, so
both failures are *raised* directly (the loader refuses to fabricate state
it cannot prove valid — AGENTS.md §0).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.core.compile_metadata_default import (
    EvidencePolicyLoadFailure,
    GraphSnapshotHashReadFailure,
    _SNIPPET_MAX_BYTES,
    _discover_graph_hash,
    _default_evidence_policy,
    _snippet_from_bytes,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

#: The on-disk filename pattern used by ``_default_evidence_policy``. Mirrors
#: the path layout in ``compile_metadata_default._default_evidence_policy``:
#: ``<graph_store_root>/v1/evidence_policy/lawvm.<jurisdiction>.v1.evidence_policy.v0.json``.
def _policy_path(graph_store_root: Path, jurisdiction: str = "fi") -> Path:
    return (
        graph_store_root
        / "v1"
        / "evidence_policy"
        / f"lawvm.{jurisdiction}.v1.evidence_policy.v0.json"
    )


#: The on-disk filename pattern used by ``_discover_graph_hash``:
#: ``<graph_store_root>/v1/graph_snapshots/<anything>.graph.json``.
def _snapshots_dir(graph_store_root: Path) -> Path:
    return graph_store_root / "v1" / "graph_snapshots"


def _write_malformed_policy(graph_store_root: Path, jurisdiction: str = "fi") -> Path:
    policy_path = _policy_path(graph_store_root, jurisdiction)
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_bytes(b"\xff\xfe this is not valid JSON \xff garbage {")
    return policy_path


def _write_malformed_snapshot(graph_store_root: Path, name: str = "broken") -> Path:
    snapshots_dir = _snapshots_dir(graph_store_root)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots_dir / f"{name}.graph.json"
    snapshot_path.write_bytes(b"\xff\xfe definitely not JSON \xff garbage")
    return snapshot_path


def _write_snapshot_missing_hash_field(graph_store_root: Path, name: str = "nohash") -> Path:
    snapshots_dir = _snapshots_dir(graph_store_root)
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshots_dir / f"{name}.graph.json"
    # Valid JSON object that lacks the snapshot_hash field — a malformed snapshot.
    snapshot_path.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
    return snapshot_path


# ---------------------------------------------------------------------------
# 1. _snippet_from_bytes helper — the snippet truncation used by both failures.
# ---------------------------------------------------------------------------


class TestSnippetHelper:
    def test_empty_bytes_returns_empty(self) -> None:
        assert _snippet_from_bytes(b"") == ""

    def test_short_text_round_trips_unchanged(self) -> None:
        assert _snippet_from_bytes(b"valid utf8 text") == "valid utf8 text"

    def test_truncates_to_max_bytes(self) -> None:
        big = b"X" * (_SNIPPET_MAX_BYTES * 3)
        result = _snippet_from_bytes(big)
        assert len(result) == _SNIPPET_MAX_BYTES
        assert result == "X" * _SNIPPET_MAX_BYTES

    def test_binary_garbage_decodes_with_replace(self) -> None:
        # Lone continuation bytes / invalid UTF-8 must not raise — a snippet
        # that fails to decode is still evidence the file was binary garbage.
        result = _snippet_from_bytes(b"\xff\xfe garbage \xff\xff end")
        assert isinstance(result, str)
        assert "garbage" in result
        assert "end" in result


# ---------------------------------------------------------------------------
# 2. EvidencePolicyLoadFailure — malformed policy file fires loud with
#    jurisdiction, path, exception_kind, and snippet.
# ---------------------------------------------------------------------------


class TestEvidencePolicyLoadFailure:
    def test_malformed_policy_raises_typed_failure(self, tmp_path: Path) -> None:
        policy_path = _write_malformed_policy(tmp_path, jurisdiction="fi")

        with pytest.raises(EvidencePolicyLoadFailure) as exc_info:
            _default_evidence_policy(jurisdiction="fi", graph_store_root=tmp_path)

        failure = exc_info.value
        assert failure.jurisdiction == "fi"
        assert failure.path == str(policy_path)
        # The original exception kind is embedded (e.g. JSONDecodeError).
        assert "JSONDecodeError" in failure.exception_kind or failure.exception_kind
        # The snippet carries the offending bytes — not just an opaque message.
        assert failure.snippet
        # The original exception is chained so a full traceback is preserved.
        assert exc_info.value.__cause__ is not None

    def test_malformed_policy_snippet_is_truncated(self, tmp_path: Path) -> None:
        # A long malformed file: snippet should be capped at _SNIPPET_MAX_BYTES.
        policy_path = _policy_path(tmp_path, jurisdiction="fi")
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_bytes(b"A" * (_SNIPPET_MAX_BYTES * 5))

        with pytest.raises(EvidencePolicyLoadFailure) as exc_info:
            _default_evidence_policy(jurisdiction="fi", graph_store_root=tmp_path)

        assert len(exc_info.value.snippet) <= _SNIPPET_MAX_BYTES

    def test_malformed_policy_exception_message_embeds_concrete_fix_and_jurisdiction(
        self, tmp_path: Path
    ) -> None:
        _write_malformed_policy(tmp_path, jurisdiction="fi")
        with pytest.raises(EvidencePolicyLoadFailure) as exc_info:
            _default_evidence_policy(jurisdiction="fi", graph_store_root=tmp_path)

        message = str(exc_info.value)
        # AGENTS.md §1.10: the diagnostic must state the concrete fix and
        # distinguish from neighbouring failures.
        assert "evidence-policy" in message.lower() or "policy" in message.lower()
        assert "fi" in message
        assert "snippet" in message  # the field is named explicitly

    def test_valid_policy_returns_registry_without_failure(self, tmp_path: Path) -> None:
        """Negative test (AGENTS.md §2.9): a valid policy file does NOT trip
        the fail-loud path — the loader returns a populated registry."""
        from lawvm.core.evidence_policy import EvidencePolicyRegistry

        valid = EvidencePolicyRegistry.build(
            registry_id="lawvm.fi.v1.evidence_policy.test",
            registry_version="v0.0.0",
            predicates=(),
        )
        # Round-trip the registry through its canonical JSON form.
        payload = {
            "registry_id": valid.registry_id,
            "registry_version": valid.registry_version,
            "registry_hash": valid.registry_hash,
            "predicates": [],
        }

        policy_path = _policy_path(tmp_path, jurisdiction="fi")
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        policy_path.write_text(json.dumps(payload), encoding="utf-8")

        result = _default_evidence_policy(jurisdiction="fi", graph_store_root=tmp_path)
        assert isinstance(result, EvidencePolicyRegistry)
        assert result.registry_hash == valid.registry_hash

    def test_missing_policy_falls_back_to_empty_registry(self, tmp_path: Path) -> None:
        """Negative test (AGENTS.md §2.9): explicit absence on disk is the
        SANCTIONED fallback path — empty-registry applies without raising."""
        from lawvm.core.evidence_policy import EvidencePolicyRegistry

        # No file at the policy_path — explicit absence.
        result = _default_evidence_policy(
            jurisdiction="fi", graph_store_root=tmp_path
        )
        assert isinstance(result, EvidencePolicyRegistry)
        assert result.registry_id == "lawvm.fi.v1.evidence_policy.empty"


# ---------------------------------------------------------------------------
# 3. GraphSnapshotHashReadFailure — malformed / hashless snapshot fires loud.
# ---------------------------------------------------------------------------


class TestGraphSnapshotHashReadFailure:
    def test_malformed_snapshot_raises_typed_failure(self, tmp_path: Path) -> None:
        snapshot_path = _write_malformed_snapshot(tmp_path)

        with pytest.raises(GraphSnapshotHashReadFailure) as exc_info:
            _discover_graph_hash(jurisdiction="fi", graph_store_root=tmp_path)

        failure = exc_info.value
        assert failure.path == str(snapshot_path)
        assert failure.exception_kind  # JSONDecodeError expected
        assert failure.snippet
        assert exc_info.value.__cause__ is not None

    def test_snapshot_missing_hash_field_raises_typed_failure(self, tmp_path: Path) -> None:
        """A JSON-shaped snapshot file with no ``snapshot_hash`` field is
        malformed — the prior silent fall-through to empty-graph hash is the
        §1.10 violation; the fail-loud replacement raises."""
        snapshot_path = _write_snapshot_missing_hash_field(tmp_path)

        with pytest.raises(GraphSnapshotHashReadFailure) as exc_info:
            _discover_graph_hash(jurisdiction="fi", graph_store_root=tmp_path)

        failure = exc_info.value
        assert failure.path == str(snapshot_path)
        assert "ValueError" == failure.exception_kind  # the inner raise
        assert failure.snippet

    def test_malformed_snapshot_snippet_is_truncated(self, tmp_path: Path) -> None:
        snapshots_dir = _snapshots_dir(tmp_path)
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        snapshot_path = snapshots_dir / "big.graph.json"
        snapshot_path.write_bytes(b"B" * (_SNIPPET_MAX_BYTES * 5))

        with pytest.raises(GraphSnapshotHashReadFailure) as exc_info:
            _discover_graph_hash(jurisdiction="fi", graph_store_root=tmp_path)

        assert len(exc_info.value.snippet) <= _SNIPPET_MAX_BYTES

    def test_valid_snapshot_returns_hash_without_failure(self, tmp_path: Path) -> None:
        """Negative test: a valid snapshot with a ``snapshot_hash`` field
        does NOT trip the fail-loud path."""
        snapshots_dir = _snapshots_dir(tmp_path)
        snapshots_dir.mkdir(parents=True, exist_ok=True)
        (snapshots_dir / "good.graph.json").write_text(
            json.dumps({"snapshot_hash": "a" * 64}), encoding="utf-8"
        )

        result = _discover_graph_hash(jurisdiction="fi", graph_store_root=tmp_path)
        assert result == "a" * 64

    def test_no_snapshots_falls_back_to_empty_graph_hash(self, tmp_path: Path) -> None:
        """Negative test: explicit absence on disk is the sanctioned
        fallback — empty-graph hash applies without raising."""
        from lawvm.core.compile_metadata_default import _canonical_empty_graph_hash

        result = _discover_graph_hash(jurisdiction="fi", graph_store_root=tmp_path)
        assert result == _canonical_empty_graph_hash()


# ---------------------------------------------------------------------------
# 4. Production-lane fire-drill (AGENTS.md §2.9) — the full ``build_default_compile_metadata``
#    path exercises both fail-loud sites; a malformed policy surfaces as the
#    typed finding (NOT a silent empty-registry substitution).
# ---------------------------------------------------------------------------


class TestProductionLaneFireDrill:
    def test_malformed_policy_in_build_default_compile_metadata_raises(
        self, tmp_path: Path
    ) -> None:
        from lawvm.core.compile_metadata_default import build_default_compile_metadata

        _write_malformed_policy(tmp_path, jurisdiction="fi")

        with pytest.raises(EvidencePolicyLoadFailure) as exc_info:
            build_default_compile_metadata(
                jurisdiction="fi",
                source_bundle_hash="sha256:" + "a" * 64,
                build_id="test.fire_drill.policy",
                graph_store_root=tmp_path,
            )

        assert exc_info.value.jurisdiction == "fi"
        assert exc_info.value.snippet

    def test_malformed_snapshot_in_build_default_compile_metadata_raises(
        self, tmp_path: Path
    ) -> None:
        from lawvm.core.compile_metadata_default import build_default_compile_metadata

        # Provide a valid policy file via override so the policy load is skipped
        # — the snapshot site is the target.
        from lawvm.core.evidence_policy import EvidencePolicyRegistry

        _write_malformed_snapshot(tmp_path)
        with pytest.raises(GraphSnapshotHashReadFailure) as exc_info:
            build_default_compile_metadata(
                jurisdiction="fi",
                source_bundle_hash="sha256:" + "b" * 64,
                build_id="test.fire_drill.snapshot",
                evidence_policy=EvidencePolicyRegistry.build(
                    registry_id="lawvm.fi.v1.evidence_policy.empty",
                    registry_version="v0.0.0",
                    predicates=(),
                ),
                graph_store_root=tmp_path,
            )

        assert exc_info.value.snippet
