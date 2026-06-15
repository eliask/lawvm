"""Tests for cmd_migrate_manual_claims CLI."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _make_fake_v2_claims(base_dir: Path, n: int = 2) -> list[str]:
    """Create n fake v2.2 claim files + events.jsonl. Returns v2 claim ids."""
    objects_dir = base_dir / "objects" / "sha256"
    objects_dir.mkdir(parents=True)
    events_path = base_dir / "events.jsonl"
    claim_ids = []
    events = []
    for i in range(n):
        cid = f"claim{i:04d}"
        claim_ids.append(cid)
        claim = {
            "claim_id": cid,
            "schema_version": "v1",
            "jurisdiction": "fi",
            "claim_kind": "fi.v1.INLINE_STATUTE_RESOLUTION",
            "claim_layer": "extraction",
            "claim_scope": {"statute_id": f"{100+i}/2024", "provision_ref": f"section:{i+1}"},
            "target": [["ref", f"section:{i+1}"]],
            "value": [["resolution", f"laki {i}/2024"]],
            "cited_source_hash": "d" * 64,
            "cited_source_locator": {"artifact_kind": "finlex_akn"},
            "cited_source_span": [0, 50],
            "dependency_fingerprint": [],
            "valid_at": ["2024-01-01", None],
            "supersedes": [],
            "disputes": [],
            "requested_profiles": [],
            "rationale": f"test claim {i}",
            "source_witness_type": "operator_filing",
            "producer": {
                "producer_kind": "operator",
                "handle": "tester",
                "model_id": None,
                "timestamp": "2024-01-01T00:00:00+00:00",
                "environment": "test",
            },
        }
        (objects_dir / f"{cid}.json").write_text(json.dumps(claim), encoding="utf-8")
        events.append({
            "claim_id": cid,
            "event_kind": "proposed",
            "timestamp": "2024-01-01T00:00:00+00:00",
            "producer": {
                "producer_kind": "operator",
                "handle": "tester",
                "model_id": None,
                "timestamp": "2024-01-01T00:00:00+00:00",
                "environment": "test",
            },
            "old_status": None,
            "new_status": "proposed",
            "reason": "filed",
        })
    with open(events_path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return claim_ids


def _load_graph_objects(tmp_path: Path) -> list[dict[str, Any]]:
    object_dir = tmp_path / "fi" / "v1" / "provenance_graph" / "objects" / "sha256"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(object_dir.glob("*.json"))
    ]


def test_migration_migrates_claims_to_graph(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    manual_dir = tmp_path / "fi" / "v1" / "manual_claims"
    _make_fake_v2_claims(manual_dir, n=3)

    summary = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))
    assert summary["assertions_migrated"] == 3
    assert summary["assertions_existing"] == 0


def test_migration_writes_graph_snapshot(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    manual_dir = tmp_path / "fi" / "v1" / "manual_claims"
    _make_fake_v2_claims(manual_dir, n=1)

    migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))

    snapshots_dir = tmp_path / "fi" / "v1" / "provenance_graph" / "snapshots"
    assert snapshots_dir.exists()
    snapshots = list(snapshots_dir.glob("*.json"))
    assert len(snapshots) >= 1


def test_migration_idempotent_on_second_run(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    manual_dir = tmp_path / "fi" / "v1" / "manual_claims"
    _make_fake_v2_claims(manual_dir, n=2)

    s1 = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))
    assert s1["assertions_migrated"] == 2

    s2 = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))
    assert s2["assertions_existing"] == 2
    assert s2["assertions_migrated"] == 0


def test_migration_dry_run_writes_nothing(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    manual_dir = tmp_path / "fi" / "v1" / "manual_claims"
    _make_fake_v2_claims(manual_dir, n=2)

    summary = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path), dry_run=True)
    assert summary["assertions_migrated"] == 2  # counted but not written

    provenance_dir = tmp_path / "fi" / "v1" / "provenance_graph"
    assert not provenance_dir.exists() or not (provenance_dir / "objects").exists()


def test_migration_empty_claims_dir(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    # Manual claims dir doesn't exist
    summary = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))
    assert summary["assertions_migrated"] == 0
    assert summary["assertions_existing"] == 0


def test_migration_graph_snapshot_readable_after_migration(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph
    from lawvm.core.provenance_graph_storage import GraphStore

    manual_dir = tmp_path / "fi" / "v1" / "manual_claims"
    _make_fake_v2_claims(manual_dir, n=2)

    migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))

    provenance_dir = tmp_path / "fi" / "v1" / "provenance_graph"
    snapshots_dir = provenance_dir / "snapshots"
    snapshots = list(snapshots_dir.glob("*.json"))
    assert len(snapshots) >= 1

    store = GraphStore(provenance_dir)
    snap_hash = snapshots[0].stem
    graph = store.read_graph(snap_hash)
    # Should have at least assertion nodes + attestation nodes
    assert len(graph.nodes) >= 2


def test_migration_preserves_claim_object_producer_when_event_log_missing(tmp_path):
    from lawvm.tools.cmd_migrate_manual_claims import migrate_manual_claims_to_graph

    objects_dir = tmp_path / "fi" / "v1" / "manual_claims" / "objects" / "sha256"
    objects_dir.mkdir(parents=True)
    claim = {
        "claim_id": "claim-no-event",
        "schema_version": "v1",
        "jurisdiction": "fi",
        "claim_kind": "fi.v1.INLINE_STATUTE_RESOLUTION",
        "claim_layer": "extraction",
        "claim_scope": {"statute_id": "100/2024", "provision_ref": "section:1"},
        "target": [["ref", "section:1"]],
        "value": [["resolution", "laki 100/2024"]],
        "cited_source_hash": "e" * 64,
        "cited_source_locator": {"structural_locator": "section:1"},
        "cited_source_span": [0, 10],
        "valid_at": ["2024-01-01", None],
        "producer": {
            "producer_kind": "operator",
            "handle": "legacy.operator",
            "timestamp": "2024-02-03T04:05:06+00:00",
            "environment": "legacy-test",
        },
        "rationale": "source-backed manual claim",
    }
    (objects_dir / "claim-no-event.json").write_text(json.dumps(claim), encoding="utf-8")

    summary = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))

    assert summary["assertions_migrated"] == 1
    assert summary["attestations_migrated"] == 1
    attestations = [
        obj for obj in _load_graph_objects(tmp_path)
        if obj.get("attestation_kind") == "claim_submitted"
    ]
    assert len(attestations) == 1
    attestation = attestations[0]
    assert attestation["producer"]["producer_id"] == "legacy.operator"
    assert attestation["producer"]["producer_kind"] == "human"
    assert attestation["producer"]["metadata"]["original_producer_kind"] == "operator"
    assert attestation["producer"]["metadata"]["original_environment"] == "legacy-test"
    assert attestation["produced_at"] == "2024-02-03T04:05:06+00:00"
    assert attestation["payload"]["migrated_from_claim_object"] is True
    assert attestation["payload"]["v2_claim_id"] == "claim-no-event"
    assert attestation["materials"][0]["structural_locator"] == "section:1"

    second = migrate_manual_claims_to_graph("fi", data_dir=str(tmp_path))
    assert second["assertions_existing"] == 1
    assert second["attestations_existing"] == 1
    assert second["attestations_migrated"] == 0
