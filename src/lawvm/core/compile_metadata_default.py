"""build_default_compile_metadata — jurisdiction-default CompileMetadata factory.

Step 5 of the v3 provenance graph transition
(notes_internal/UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5).

Convenience factory for CLI and rebuild_indexes callers that need a
CompileMetadata without manually wiring every fingerprint. Chooses
jurisdiction defaults for strict_profile and evidence_policy; discovers
the most-recent on-disk graph snapshot or falls back to an empty-graph hash.

Design rules (per AGENTS.md + reproducibility contract)
--------------------------------------------------------
* No datetime.now() calls — build_timestamp is caller-provided or None.
* All fingerprints are deterministic given the same inputs.
* Falls back to empty graph when no snapshots exist on disk (explicit, not silent).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from lawvm.core.compile_metadata import CompileMetadata
from lawvm.core.compile_result import StrictProfile
from lawvm.core.evidence_policy import EvidencePolicyRegistry
from lawvm.core.provenance_graph import attestation_kind_registry_hash
from lawvm.core.strict_profile_registry import get_default_strict_profile


# ---------------------------------------------------------------------------
# Typed fail-loud diagnostics for §1.10 — replacing silent ``except Exception: pass``
# ---------------------------------------------------------------------------
# A jurisdiction-defaults loader must NOT silently fall back to the empty
# registry / empty-graph hash when an on-disk policy file or snapshot is
# unreadable. AGENTS.md §1.10 requires a distinct named diagnostic with the
# offending snippet embedded (triaging a residual must never require re-running
# extraction). These frozen dataclasses subclass ``Exception`` so the loader
# raises them when no findings sink is wired; the surrounding context has no
# sink (it constructs the CompileMetadata that downstream stages will emit
# findings into).

#: Maximum byte length of the offending-file snippet embedded on a load
#: failure. AGENTS.md §1.10: ~300–400 chars; we encode bytes-as-utf8-replace
#: so binary garbage still produces a readable diagnostic.
_SNIPPET_MAX_BYTES = 400


def _snippet_from_bytes(data: bytes) -> str:
    """Truncate *data* to ``_SNIPPET_MAX_BYTES`` and decode best-effort to text."""
    if not data:
        return ""
    if len(data) > _SNIPPET_MAX_BYTES:
        data = data[:_SNIPPET_MAX_BYTES]
    return data.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class EvidencePolicyLoadFailure(Exception):
    """Raised when a jurisdiction's on-disk evidence-policy file is unreadable.

    Replaces the prior silent ``except Exception: pass`` in
    ``_default_evidence_policy`` (AGENTS.md §1.10). Carries the jurisdiction,
    on-disk path, the swallowed exception class name, and the first
    ``_SNIPPET_MAX_BYTES`` bytes of the offending file (UTF-8 best-effort
    decoded) so a triager can audit the failure from the record alone.
    """

    jurisdiction: str
    path: str
    exception_kind: str
    snippet: str

    def __str__(self) -> str:  # pragma: no cover - exercised via repr in tracebacks
        return (
            f"EvidencePolicyLoadFailure(jurisdiction={self.jurisdiction!r}, "
            f"path={self.path!r}, exception_kind={self.exception_kind!r}): "
            f"on-disk evidence-policy JSON is unreadable; the first "
            f"{_SNIPPET_MAX_BYTES} bytes are embedded in .snippet. Fix the "
            f"policy file or drop it from disk so the empty-registry fallback "
            f"applies explicitly."
        )


@dataclass(frozen=True, slots=True)
class GraphSnapshotHashReadFailure(Exception):
    """Raised when an on-disk graph snapshot's ``snapshot_hash`` cannot be read.

    Replaces the prior silent ``except Exception: pass`` in
    ``_discover_graph_hash`` (AGENTS.md §1.10). Carries the snapshot path, the
    swallowed exception class name, and the first ``_SNIPPET_MAX_BYTES`` bytes
    of the offending snapshot file so a triager can audit the failure from the
    record alone.
    """

    path: str
    exception_kind: str
    snippet: str

    def __str__(self) -> str:  # pragma: no cover - exercised via repr in tracebacks
        return (
            f"GraphSnapshotHashReadFailure(path={self.path!r}, "
            f"exception_kind={self.exception_kind!r}): on-disk graph snapshot "
            f"is unreadable; the first {_SNIPPET_MAX_BYTES} bytes are embedded "
            f"in .snippet. Fix the snapshot file or drop it from disk so the "
            f"canonical empty-graph hash fallback applies explicitly."
        )


def build_default_compile_metadata(
    *,
    jurisdiction: str,
    source_bundle_hash: str,
    build_id: str,
    build_timestamp: Optional[datetime] = None,
    strict_profile: Optional[StrictProfile] = None,
    evidence_policy: Optional[EvidencePolicyRegistry] = None,
    graph_store_root: Optional[Path] = None,
) -> CompileMetadata:
    """Construct CompileMetadata using jurisdiction defaults.

    Args:
        jurisdiction:       Jurisdiction code, e.g. "fi".
        source_bundle_hash: Caller-provided sha256 of the source farchive(s).
        build_id:           Opaque stable build identifier
                            (e.g. "cli.rebuild-indexes.fi").
        build_timestamp:    Caller-provided datetime or None. Never pass
                            datetime.now() from inside an emitter —
                            reproducibility contract violation.
        strict_profile:     Override the canonical strict profile for the
                            jurisdiction. Defaults to jurisdiction canonical.
        evidence_policy:    Override the EvidencePolicyRegistry. Defaults to
                            a minimal empty registry for the jurisdiction.
        graph_store_root:   Root path for GraphStore snapshot discovery.
                            Defaults to Path("data/{jurisdiction}").

    Returns:
        CompileMetadata with all 5 required fields populated.
    """
    from lawvm.core.compile_metadata import compute_strict_profile_fingerprint

    # --- strict_profile ---
    if strict_profile is None:
        strict_profile = _default_strict_profile(jurisdiction)

    # --- evidence_policy ---
    if evidence_policy is None:
        evidence_policy = _default_evidence_policy(
            jurisdiction=jurisdiction,
            graph_store_root=graph_store_root,
        )

    # --- provenance_graph_hash ---
    provenance_graph_hash = _discover_graph_hash(
        jurisdiction=jurisdiction,
        graph_store_root=graph_store_root,
    )

    return CompileMetadata(
        provenance_graph_hash=provenance_graph_hash,
        strict_profile_fingerprint=compute_strict_profile_fingerprint(strict_profile),
        evidence_policy_fingerprint=evidence_policy.registry_hash,
        source_bundle_hash=source_bundle_hash,
        attestation_kind_registry_hash=attestation_kind_registry_hash(),
        build_id=build_id,
        build_timestamp=build_timestamp,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _default_strict_profile(jurisdiction: str) -> StrictProfile:
    """Return the canonical strict profile for the given jurisdiction.

    Resolves via the StrictProfileRegistry — frontends register their
    ``default_*_strict_profile`` factory at package import time
    (e.g. ``finland/__init__.py`` registers ``default_finland_strict_profile``
    for ``"fi"``). Returns the generic ``StrictProfile(name=...)`` fallback when
    no factory is registered for the jurisdiction. No Python import of a
    frontend package is attempted here — core must not import a frontend
    (AGENTS.md §2.3).
    """
    profile = get_default_strict_profile(jurisdiction)
    if profile is not None:
        return profile
    # Generic fallback for jurisdictions without a registered profile. This
    # branch is reached when the frontend package has not been imported (so
    # its registration never fired) OR the jurisdiction genuinely ships no
    # canonical profile. The caller sees a deterministic named profile.
    return StrictProfile(name=f"{jurisdiction}_default_v1")


def _default_evidence_policy(
    *,
    jurisdiction: str,
    graph_store_root: Optional[Path],
) -> EvidencePolicyRegistry:
    """Return the evidence policy for the jurisdiction.

    Loads the canonical policy JSON from disk when it exists; falls back to a
    minimal empty registry when no policy file is present (explicit absence,
    not silent swallow). When the file *exists* but is unreadable / malformed,
    raises :class:`EvidencePolicyLoadFailure` with the offending snippet
    embedded (AGENTS.md §1.10 — a missing mapping / unreadable policy file
    must not silently become the empty registry).
    """
    if graph_store_root is None:
        graph_store_root = Path("data") / jurisdiction

    policy_path = (
        graph_store_root
        / "v1"
        / "evidence_policy"
        / f"lawvm.{jurisdiction}.v1.evidence_policy.v0.json"
    )
    if policy_path.exists():
        try:
            import json
            from lawvm.core.evidence_policy import registry_from_dict
            data = json.loads(policy_path.read_text(encoding="utf-8"))
            return registry_from_dict(data)
        except Exception as exc:  # noqa: BLE001 — fail-loud owned swallow
            # AGENTS.md §1.10: emit a distinct named diagnostic with the
            # offending snippet. We raise (no findings sink is available at
            # this stage of CompileMetadata construction) — the caller can
            # decide to warrant or fix.
            file_bytes = policy_path.read_bytes()
            raise EvidencePolicyLoadFailure(
                jurisdiction=jurisdiction,
                path=str(policy_path),
                exception_kind=type(exc).__name__,
                snippet=_snippet_from_bytes(file_bytes),
            ) from exc

    return EvidencePolicyRegistry.build(
        registry_id=f"lawvm.{jurisdiction}.v1.evidence_policy.empty",
        registry_version="v0.0.0",
        predicates=(),
    )


def _discover_graph_hash(
    *,
    jurisdiction: str,
    graph_store_root: Optional[Path],
) -> str:
    """Return the most-recent graph snapshot hash, or canonical empty-graph hash.

    Searches for *.graph.json snapshot files under graph_store_root. Returns
    the hash from the most-recently-modified snapshot. When no snapshots exist
    on disk, falls back to the canonical empty-graph hash (explicit absence).

    When a snapshot file *exists* but is unreadable / malformed / lacks a
    ``snapshot_hash`` field, raises :class:`GraphSnapshotHashReadFailure` with
    the offending snippet embedded (AGENTS.md §1.10 — an unreadable snapshot
    must not silently become the empty-graph fallback).
    """
    if graph_store_root is None:
        graph_store_root = Path("data") / jurisdiction

    snapshots_dir = graph_store_root / "v1" / "graph_snapshots"
    if snapshots_dir.exists():
        candidates = sorted(
            snapshots_dir.glob("*.graph.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if candidates:
            try:
                import json
                snapshot_path = candidates[0]
                data = json.loads(snapshot_path.read_text(encoding="utf-8"))
                h = str(data.get("snapshot_hash") or "")
                if h:
                    return h
                # No snapshot_hash field on a JSON-shaped file — still a
                # malformed snapshot, not an empty directory. Fail loud.
                raise ValueError(
                    f"snapshot file {snapshot_path} missing snapshot_hash"
                )
            except Exception as exc:  # noqa: BLE001 — fail-loud owned swallow
                file_bytes = candidates[0].read_bytes()
                raise GraphSnapshotHashReadFailure(
                    path=str(candidates[0]),
                    exception_kind=type(exc).__name__,
                    snippet=_snippet_from_bytes(file_bytes),
                ) from exc

    return _canonical_empty_graph_hash()


def _canonical_empty_graph_hash() -> str:
    """Return the sha256 hash of an empty ProvenanceGraph."""
    from lawvm.core.provenance_graph import GraphBuilder, attestation_kind_registry_hash
    empty = GraphBuilder(attestation_kind_registry_hash()).finalize()
    return empty.snapshot_hash
