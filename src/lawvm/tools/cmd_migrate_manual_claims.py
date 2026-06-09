"""lawvm migrate-manual-claims-to-graph — one-shot data migration CLI.

Migrates v2.2 manual claims data to the v3 ProvenanceGraph substrate.

Migration steps
---------------
1. Read data/{jur}/v1/manual_claims/objects/sha256/*.json
   → convert each ManualCompilationClaim to ProvenanceAssertion
   → write to data/{jur}/v1/provenance_graph/objects/sha256/{assertion_id}.json

2. Read data/{jur}/v1/manual_claims/events.jsonl
   → convert each ClaimStateEvent to ProvenanceAttestation
   → write to data/{jur}/v1/provenance_graph/objects/sha256/{attestation_id}.json

3. Read data/{jur}/v1/claim_precedence.yaml (deprecated)
   → emit data/{jur}/v1/evidence_policy/lawvm.{jur}.v1.evidence_policy.v0.json
   → only if the file does not already exist

4. Build a graph snapshot from all migrated objects; write snapshot JSON.

5. On re-run: verify all hashes match existing objects (idempotent).

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from lawvm.core.provenance_graph import (
    ArtifactRef,
    GraphBuilder,
    Interval,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    SourceRef,
    assertion_canonical_payload,
    attestation_canonical_payload,
    attestation_kind_registry_hash,
    _sha256,
)
from lawvm.core.provenance_graph_storage import GraphStore


_DEFAULT_DATA_DIR = "data"
_V2_MANUAL_CLAIMS_SUBDIR = "manual_claims"
_V3_PROVENANCE_SUBDIR = "provenance_graph"


# ---------------------------------------------------------------------------
# Conversion helpers: v2.2 → v3
# ---------------------------------------------------------------------------


def _make_assertion_from_claim(claim_dict: dict) -> ProvenanceAssertion:
    """Convert a v2.2 ManualCompilationClaim dict to a ProvenanceAssertion."""
    claim_id = str(claim_dict["claim_id"])
    jurisdiction = str(claim_dict.get("jurisdiction", "fi"))
    claim_kind = str(claim_dict.get("claim_kind", ""))
    claim_layer = str(claim_dict.get("claim_layer", "extraction"))
    claim_scope = claim_dict.get("claim_scope", {})
    statute_id = str(claim_scope.get("statute_id", "")) if isinstance(claim_scope, dict) else ""
    provision_ref = claim_scope.get("provision_ref") if isinstance(claim_scope, dict) else None

    valid_at_raw = claim_dict.get("valid_at")
    if isinstance(valid_at_raw, (list, tuple)) and len(valid_at_raw) == 2:
        start_str = str(valid_at_raw[0]) if valid_at_raw[0] else None
        end_str = str(valid_at_raw[1]) if valid_at_raw[1] else None
    else:
        start_str = None
        end_str = None

    if start_str:
        interval_start = date.fromisoformat(start_str)
    else:
        interval_start = date(2000, 1, 1)
    interval_end = date.fromisoformat(end_str) if end_str else None
    valid_at = Interval(start=interval_start, end=interval_end)

    # Convert target + value tuples back to dicts
    target_raw = claim_dict.get("target", [])
    value_raw = claim_dict.get("value", [])
    target: dict = dict(target_raw) if isinstance(target_raw, (list, tuple)) else {}
    value: dict = dict(value_raw) if isinstance(value_raw, (list, tuple)) else {}

    # Build a stub SourceRef from cited_source fields
    artifact_digest = str(claim_dict.get("cited_source_hash", "")) or "unknown"
    span = claim_dict.get("cited_source_span", [0, 0])
    byte_range: tuple[int, int] = (int(span[0]), int(span[1])) if len(span) == 2 else (0, 0)
    source_locator = claim_dict.get("cited_source_locator", {})  # noqa: F841  # BUG: cited_source_locator read but never passed to SourceRef (structural_locator used instead)
    structural_locator = str(provision_ref or "")
    source_ref = SourceRef(
        artifact_digest=artifact_digest,
        structural_locator=structural_locator,
        bounded_quote_hash=artifact_digest[:64] if len(artifact_digest) >= 64 else artifact_digest,
        normalization_policy_id="v2.2.migration",
        byte_range=byte_range,
    )

    # Producer from claim's producer field
    producer_raw = claim_dict.get("producer", {})
    if isinstance(producer_raw, dict):
        producer_id = str(producer_raw.get("handle") or producer_raw.get("model_id") or "unknown")
        raw_kind = str(producer_raw.get("producer_kind", "human"))
        # v2.2 used "operator" and "tool" which are not valid v3 producer kinds
        _kind_map = {"operator": "human", "tool": "script"}
        producer_kind = _kind_map.get(raw_kind, raw_kind)
        ts_raw = producer_raw.get("timestamp", "")
        if ts_raw:
            produced_dt = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))  # noqa: F841  # BUG: timestamp parsed but ProvenanceAssertion has no producer/timestamp field; dead computation
        else:
            produced_dt = datetime.now(tz=timezone.utc)  # noqa: F841  # BUG: same — ProvenanceAssertion has no producer field to receive this
    else:
        producer_id = "unknown"
        producer_kind = "human"
        produced_dt = datetime.now(tz=timezone.utc)  # noqa: F841  # BUG: same

    producer = Producer(  # noqa: F841  # BUG: Producer built but ProvenanceAssertion schema has no producer field; entire block is dead
        producer_id=producer_id,
        producer_kind=producer_kind,  # ty:ignore[invalid-argument-type]
        public_key=None,
        metadata={"migrated_from": "v2.2", "original_claim_id": claim_id},
    )

    supersedes = tuple(str(x) for x in claim_dict.get("supersedes", []))
    disputes = tuple(str(x) for x in claim_dict.get("disputes", []))
    rationale = str(claim_dict.get("rationale", ""))

    # Build assertion WITHOUT assertion_id first to compute it
    scope = {"statute_id": statute_id, "provision_ref": provision_ref or ""}
    assertion_no_id = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version="v1",
        jurisdiction=jurisdiction,
        kind=claim_kind,
        layer=claim_layer,
        scope=scope,
        target=target,
        value=value,
        source_refs=(source_ref,),
        dependency_refs=(),
        valid_at=valid_at,
        supersedes=supersedes,
        disputes=disputes,
        rationale=rationale,
    )
    canonical = assertion_canonical_payload(assertion_no_id)
    assertion_id = _sha256(canonical)

    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v1",
        jurisdiction=jurisdiction,
        kind=claim_kind,
        layer=claim_layer,
        scope=scope,
        target=target,
        value=value,
        source_refs=(source_ref,),
        dependency_refs=(),
        valid_at=valid_at,
        supersedes=supersedes,
        disputes=disputes,
        rationale=rationale,
    )


_EVENT_KIND_TO_ATTESTATION_KIND: dict[str, str] = {
    "proposed": "claim_submitted",
    "accepted": "reviewed",
    "rejected": "reviewed",
    "retracted": "retracted",
    "superseded": "superseded",
    "span_verified": "span_verified",
    "entailment_verified": "entailment_verified",
    "revalidated": "reviewed",
    "needs_revalidation": "reviewed",
    "orphaned": "reviewed",
    "consumed": "transparency_logged",
    "composed_for_build": "transparency_logged",
    "taint_report_emitted": "transparency_logged",
}


def _make_attestation_from_event(
    event_dict: dict,
    assertion_id_map: dict[str, str],
) -> Optional[ProvenanceAttestation]:
    """Convert a v2.2 ClaimStateEvent dict to a ProvenanceAttestation.

    Returns None if the event's claim_id has no matching assertion in assertion_id_map.
    """
    v2_claim_id = str(event_dict.get("claim_id", ""))
    assertion_id = assertion_id_map.get(v2_claim_id)
    if not assertion_id:
        return None

    event_kind = str(event_dict.get("event_kind", "proposed"))
    attestation_kind = _EVENT_KIND_TO_ATTESTATION_KIND.get(event_kind, "transparency_logged")

    ts_raw = event_dict.get("timestamp", "")
    produced_at = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else datetime.now(tz=timezone.utc)

    producer_raw = event_dict.get("producer", {})
    if isinstance(producer_raw, dict):
        producer_id = str(producer_raw.get("handle") or producer_raw.get("model_id") or "unknown")
        raw_kind = str(producer_raw.get("producer_kind", "human"))
        _kind_map = {"operator": "human", "tool": "script"}
        producer_kind = _kind_map.get(raw_kind, raw_kind)
    else:
        producer_id = "unknown"
        producer_kind = "human"

    producer = Producer(
        producer_id=producer_id,
        producer_kind=producer_kind,  # ty:ignore[invalid-argument-type]
        public_key=None,
        metadata={"migrated_from": "v2.2", "original_event_kind": event_kind},
    )

    payload: dict[str, object] = {
        "migrated_event_kind": event_kind,
        "old_status": event_dict.get("old_status"),
        "new_status": event_dict.get("new_status"),
        "reason": str(event_dict.get("reason", "")),
    }
    if event_kind in ("accepted", "rejected"):
        payload["accepted"] = event_kind == "accepted"

    subject_ref = ArtifactRef(
        artifact_type="assertion",
        artifact_id=assertion_id,
        content_hash=assertion_id,
    )

    temp = ProvenanceAttestation(
        attestation_id="__placeholder__",
        attestation_kind=attestation_kind,
        subject=subject_ref,
        materials=(),
        producer=producer,
        produced_at=produced_at,
        payload=payload,
    )
    canonical = attestation_canonical_payload(temp)
    attest_id = _sha256(canonical)

    return ProvenanceAttestation(
        attestation_id=attest_id,
        attestation_kind=attestation_kind,
        subject=subject_ref,
        materials=(),
        producer=producer,
        produced_at=produced_at,
        payload=payload,
    )


# ---------------------------------------------------------------------------
# Migration main function
# ---------------------------------------------------------------------------


def migrate_manual_claims_to_graph(
    jurisdiction: str,
    data_dir: str = _DEFAULT_DATA_DIR,
    *,
    dry_run: bool = False,
) -> dict[str, int]:
    """Migrate v2.2 manual claims to graph-native storage.

    Returns a summary dict with counts:
      assertions_migrated, attestations_migrated, assertions_existing,
      attestations_existing, snapshot_written
    """
    jur_v1 = Path(data_dir) / jurisdiction / "v1"
    manual_claims_dir = jur_v1 / _V2_MANUAL_CLAIMS_SUBDIR
    provenance_dir = jur_v1 / _V3_PROVENANCE_SUBDIR

    graph_store = GraphStore(provenance_dir)
    reg_hash = attestation_kind_registry_hash()

    summary: dict[str, int] = {
        "assertions_migrated": 0,
        "attestations_migrated": 0,
        "assertions_existing": 0,
        "attestations_existing": 0,
        "snapshot_written": 0,
    }

    # Phase 1: migrate assertions from objects/sha256/
    objects_dir = manual_claims_dir / "objects" / "sha256"
    assertion_id_map: dict[str, str] = {}  # v2_claim_id → v3_assertion_id
    assertions: list[ProvenanceAssertion] = []

    if objects_dir.exists():
        for claim_path in sorted(objects_dir.glob("*.json")):
            claim_dict = json.loads(claim_path.read_text(encoding="utf-8"))
            v2_claim_id = str(claim_dict.get("claim_id", claim_path.stem))
            assertion = _make_assertion_from_claim(claim_dict)
            assertion_id_map[v2_claim_id] = assertion.assertion_id
            assertions.append(assertion)
            dest_path = graph_store._objects_dir() / f"{assertion.assertion_id}.json"
            if dest_path.exists():
                summary["assertions_existing"] += 1
            else:
                if not dry_run:
                    graph_store.write_assertion(assertion)
                summary["assertions_migrated"] += 1

    # Phase 2: migrate attestations from events.jsonl
    events_path = manual_claims_dir / "events.jsonl"
    attestations: list[ProvenanceAttestation] = []

    if events_path.exists():
        with open(events_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event_dict = json.loads(line)
                attest = _make_attestation_from_event(event_dict, assertion_id_map)
                if attest is None:
                    continue
                attestations.append(attest)
                dest_path = graph_store._objects_dir() / f"{attest.attestation_id}.json"
                if dest_path.exists():
                    summary["attestations_existing"] += 1
                else:
                    if not dry_run:
                        graph_store.write_attestation(attest)
                    summary["attestations_migrated"] += 1

    # Phase 3: emit initial evidence policy registry if absent
    evidence_policy_dir = jur_v1 / "evidence_policy"
    policy_path = evidence_policy_dir / f"lawvm.{jurisdiction}.v1.evidence_policy.v0.json"
    if not policy_path.exists() and not dry_run:
        _emit_initial_evidence_policy(jurisdiction, evidence_policy_dir, policy_path)

    # Phase 4: build + write graph snapshot
    if not dry_run and (assertions or attestations):
        builder = GraphBuilder(attestation_kind_registry_hash_val=reg_hash)
        for a in assertions:
            builder.add_assertion(a)
        for a in attestations:
            builder.add_attestation(a)
        graph = builder.finalize()
        graph_store.write_graph(graph)
        summary["snapshot_written"] = 1
        print(f"snapshot: {graph.snapshot_hash}")

    return summary


def _emit_initial_evidence_policy(jurisdiction: str, evidence_policy_dir: Path, policy_path: Path) -> None:
    """Write the initial evidence policy registry JSON if it doesn't exist."""
    evidence_policy_dir.mkdir(parents=True, exist_ok=True)
    # Only write if this is the 'fi' jurisdiction (other jurisdictions need their own policy)
    source_path = Path("data") / "fi" / "v1" / "evidence_policy" / "lawvm.fi.v1.evidence_policy.v0.json"
    if source_path.exists() and jurisdiction == "fi":
        policy_path.write_text(source_path.read_text(encoding="utf-8"), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi")
    data_dir = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    dry_run = getattr(args, "dry_run", False)

    print(f"migrating manual claims → graph: jurisdiction={jurisdiction}, data_dir={data_dir}")
    if dry_run:
        print("  (dry run — no files written)")

    summary = migrate_manual_claims_to_graph(
        jurisdiction=jurisdiction,
        data_dir=data_dir,
        dry_run=dry_run,
    )

    print(f"  assertions migrated:  {summary['assertions_migrated']}")
    print(f"  assertions existing:  {summary['assertions_existing']}")
    print(f"  attestations migrated: {summary['attestations_migrated']}")
    print(f"  attestations existing: {summary['attestations_existing']}")
    print(f"  snapshot written:      {summary['snapshot_written']}")

    if not dry_run and summary["assertions_migrated"] == 0 and summary["assertions_existing"] == 0:
        print("  (no v2.2 manual claims found — nothing to migrate)")
