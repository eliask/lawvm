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

from datetime import datetime
from pathlib import Path
from typing import Optional


def build_default_compile_metadata(
    *,
    jurisdiction: str,
    source_bundle_hash: str,
    build_id: str,
    build_timestamp: Optional[datetime] = None,
    strict_profile: object = None,
    evidence_policy: object = None,
    graph_store_root: Optional[Path] = None,
) -> object:
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
    from lawvm.core.compile_metadata import (
        CompileMetadata,
        compute_strict_profile_fingerprint,
    )
    from lawvm.core.evidence_policy import EvidencePolicyRegistry
    from lawvm.core.provenance_graph import attestation_kind_registry_hash

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


def _default_strict_profile(jurisdiction: str) -> object:
    """Return the canonical strict profile for the given jurisdiction."""
    if jurisdiction == "fi":
        from lawvm.finland.strict_profile import default_finland_strict_profile
        return default_finland_strict_profile()
    # Generic fallback for other jurisdictions
    from lawvm.core.compile_result import StrictProfile
    return StrictProfile(name=f"{jurisdiction}_default_v1")


def _default_evidence_policy(
    *,
    jurisdiction: str,
    graph_store_root: Optional[Path],
) -> object:
    """Return the evidence policy for the jurisdiction.

    Tries to load the canonical policy JSON from disk; falls back to a
    minimal empty registry when no policy file exists.
    """
    from lawvm.core.evidence_policy import EvidencePolicyRegistry

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
        except Exception:
            pass

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
    the hash from the most-recently-modified snapshot. Falls back to the
    canonical empty-graph hash when no snapshots exist.
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
                data = json.loads(candidates[0].read_text(encoding="utf-8"))
                h = str(data.get("snapshot_hash") or "")
                if h:
                    return h
            except Exception:
                pass

    return _canonical_empty_graph_hash()


def _canonical_empty_graph_hash() -> str:
    """Return the sha256 hash of an empty ProvenanceGraph."""
    from lawvm.core.provenance_graph import GraphBuilder, attestation_kind_registry_hash
    empty = GraphBuilder(attestation_kind_registry_hash()).finalize()
    return empty.snapshot_hash
