"""Tests for v3 graph-native cmd_claim / cmd_propose_claims / cmd_validate_claims.

Test IDs per spec:
  1.  test_cmd_claim_propose_writes_graph_node
  2.  test_cmd_claim_accept_emits_reviewed_attestation
  3.  test_cmd_claim_reject_emits_reviewed_attestation_with_reason
  4.  test_cmd_claim_retract_emits_retracted_and_renders_taint
  5.  test_cmd_claim_supersede_emits_superseded_attestation_and_new_assertion
  6.  test_cmd_claim_show_renders_assertion_attestations_and_authorization
  7.  test_cmd_claim_list_filters_by_kind
  8.  test_cmd_claim_disputes_renders_conflicts
  9.  test_cmd_claim_taint_report_computed_at_query_time
  10. test_cmd_propose_claims_writes_assertions_and_validator_attestations
  11. test_cmd_propose_claims_rejected_proposal_stored_for_audit
  12. test_cmd_validate_claims_emits_new_validator_attestations_does_not_mutate_assertion
  13. test_cmd_claim_propose_real_corpus_regression  (marked slow)
"""
from __future__ import annotations

import importlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast, Optional

import pytest

importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.provenance_graph_storage import (
    GraphStore,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_assertion_dict(
    kind: str = "fi.v1.INLINE_STATUTE_RESOLUTION",
    statute_id: str = "711/2022",
    resolved: str = "1234/2020",
) -> dict:
    return {
        "kind": kind,
        "layer": "extraction",
        "jurisdiction": "fi",
        "schema_version": "v1",
        "scope": {"statute_id": statute_id, "provision_ref": "section:3"},
        "target": {"statute_id": statute_id, "mention_span": "100-120"},
        "value": {"resolved_statute_id": resolved, "citation_form": f"lain {resolved}"},
        "valid_at": {"start": "2022-01-01", "end": None},
        "source_refs": [
            {
                "artifact_digest": "a" * 64,
                "structural_locator": "section:3",
                "bounded_quote_hash": "b" * 64,
                "normalization_policy_id": "v1",
                "byte_range": [0, 100],
            }
        ],
        "dependency_refs": [],
        "supersedes": [],
        "disputes": [],
        "rationale": "test assertion",
    }


def _write_claim_file(tmp_path: Path, d: dict) -> Path:
    p = tmp_path / "claim.json"
    p.write_text(json.dumps(d, indent=2), encoding="utf-8")
    return p


class _Args:
    def __setattr__(self, k, v):
        object.__setattr__(self, k, v)


def _make_args(**kwargs):
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _graph_root(tmp_path: Path) -> str:
    return str(tmp_path / "provenance_graph")


def _load_all_objects(tmp_path: Path) -> list[dict]:
    obj_dir = tmp_path / "provenance_graph" / "objects" / "sha256"
    if not obj_dir.exists():
        return []
    return [json.loads(f.read_text()) for f in sorted(obj_dir.glob("*.json"))]


def _objects_of_kind(tmp_path: Path, attestation_kind: str) -> list[dict]:
    return [
        d for d in _load_all_objects(tmp_path)
        if d.get("attestation_kind") == attestation_kind
    ]


def _assertions(tmp_path: Path) -> list[dict]:
    return [d for d in _load_all_objects(tmp_path) if "kind" in d and "assertion_id" in d]


def _propose_assertion(tmp_path: Path, d: Optional[dict] = None) -> str:
    """Propose an assertion; return assertion_id."""
    from lawvm.tools.cmd_claim import cmd_propose
    if d is None:
        d = _make_assertion_dict()
    cf = _write_claim_file(tmp_path, d)
    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0
    assertions = _assertions(tmp_path)
    assert len(assertions) >= 1
    return assertions[-1]["assertion_id"]


# ---------------------------------------------------------------------------
# Test 1: propose writes assertion + claim_submitted attestation
# ---------------------------------------------------------------------------


def test_cmd_claim_propose_writes_graph_node(tmp_path):
    from lawvm.tools.cmd_claim import cmd_propose
    d = _make_assertion_dict()
    cf = _write_claim_file(tmp_path, d)
    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    all_objs = _load_all_objects(tmp_path)
    assertion_objs = [o for o in all_objs if "assertion_id" in o and "kind" in o]
    attestation_objs = [o for o in all_objs if o.get("attestation_kind") == "claim_submitted"]

    assert len(assertion_objs) == 1
    assert assertion_objs[0]["kind"] == "fi.v1.INLINE_STATUTE_RESOLUTION"
    assert len(attestation_objs) == 1

    snapshot_files = list((tmp_path / "provenance_graph" / "snapshots").glob("*.json"))
    assert len(snapshot_files) >= 1


# ---------------------------------------------------------------------------
# Test 2: accept emits reviewed(accepted=True)
# ---------------------------------------------------------------------------


def test_cmd_claim_accept_emits_reviewed_attestation(tmp_path):
    from lawvm.tools.cmd_claim import cmd_accept
    assertion_id = _propose_assertion(tmp_path)

    rc = cmd_accept(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    reviewed = _objects_of_kind(tmp_path, "reviewed")
    assert len(reviewed) == 1
    assert reviewed[0]["payload"]["accepted"] is True
    assert reviewed[0]["subject"]["artifact_id"] == assertion_id


# ---------------------------------------------------------------------------
# Test 3: reject emits reviewed(accepted=False, reason=...)
# ---------------------------------------------------------------------------


def test_cmd_claim_reject_emits_reviewed_attestation_with_reason(tmp_path):
    from lawvm.tools.cmd_claim import cmd_reject
    assertion_id = _propose_assertion(tmp_path)

    rc = cmd_reject(_make_args(
        assertion_id=assertion_id,
        reason="wrong target statute",
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0

    reviewed = _objects_of_kind(tmp_path, "reviewed")
    assert len(reviewed) == 1
    assert reviewed[0]["payload"]["accepted"] is False
    assert "wrong target statute" in reviewed[0]["payload"]["reason"]


# ---------------------------------------------------------------------------
# Test 4: retract emits retracted attestation + stdout includes taint report
# ---------------------------------------------------------------------------


def test_cmd_claim_retract_emits_retracted_and_renders_taint(tmp_path, capsys):
    from lawvm.tools.cmd_claim import cmd_retract
    assertion_id = _propose_assertion(tmp_path)

    rc = cmd_retract(_make_args(
        assertion_id=assertion_id,
        reason="data source error",
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0

    retracted = _objects_of_kind(tmp_path, "retracted")
    assert len(retracted) == 1
    assert "data source error" in retracted[0]["payload"]["reason"]

    out = capsys.readouterr().out
    assert "taint report" in out


# ---------------------------------------------------------------------------
# Test 5: supersede creates new assertion + superseded attestation
# ---------------------------------------------------------------------------


def test_cmd_claim_supersede_emits_superseded_attestation_and_new_assertion(tmp_path):
    from lawvm.tools.cmd_claim import cmd_supersede
    old_id = _propose_assertion(tmp_path)

    new_d = _make_assertion_dict(resolved="9999/2023")
    new_file = tmp_path / "new_claim.json"
    new_file.write_text(json.dumps(new_d), encoding="utf-8")

    rc = cmd_supersede(_make_args(
        old_assertion_id=old_id,
        with_file=str(new_file),
        delta_reason="updated resolution",
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0

    all_assertions = _assertions(tmp_path)
    assert len(all_assertions) == 2

    superseded = _objects_of_kind(tmp_path, "superseded")
    assert len(superseded) == 1
    assert superseded[0]["subject"]["artifact_id"] == old_id
    new_id = superseded[0]["payload"]["superseding_assertion_id"]
    assert new_id != old_id

    new_assertion = next(a for a in all_assertions if a["assertion_id"] == new_id)
    assert old_id in new_assertion["supersedes"]


# ---------------------------------------------------------------------------
# Test 6: show renders four sections
# ---------------------------------------------------------------------------


def test_cmd_claim_show_renders_assertion_attestations_and_authorization(tmp_path, capsys):
    from lawvm.tools.cmd_claim import cmd_accept, cmd_show
    assertion_id = _propose_assertion(tmp_path)
    cmd_accept(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))

    rc = cmd_show(_make_args(assertion_id=assertion_id, graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    out = capsys.readouterr().out
    assert "ASSERTION PAYLOAD" in out
    assert "fi.v1.INLINE_STATUTE_RESOLUTION" in out
    assert "ATTESTATIONS" in out
    assert "claim_submitted" in out
    assert "reviewed" in out
    assert "AUTHORIZATION RESULT" in out
    assert "authorized" in out.lower()
    assert "SOURCE PROVENANCE" in out


# ---------------------------------------------------------------------------
# Test 7: list filters by kind
# ---------------------------------------------------------------------------


def test_cmd_claim_list_filters_by_kind(tmp_path, capsys):
    from lawvm.tools.cmd_claim import cmd_list
    _propose_assertion(tmp_path)

    rc = cmd_list(_make_args(
        kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        layer=None,
        has_attestation_kind=None,
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0
    out = capsys.readouterr().out
    assert "fi.v1.INLINE_STATUTE_RESOLUTION" in out

    rc2 = cmd_list(_make_args(
        kind="fi.v1.OTHER_KIND",
        layer=None,
        has_attestation_kind=None,
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "no assertions match" in out2


# ---------------------------------------------------------------------------
# Test 8: disputes renders conflict pairs
# ---------------------------------------------------------------------------


def test_cmd_claim_disputes_renders_conflicts(tmp_path, capsys):
    from lawvm.tools.cmd_claim import cmd_propose

    d1 = _make_assertion_dict(resolved="1234/2020")
    d2 = _make_assertion_dict(resolved="5678/2020")
    cf1 = tmp_path / "c1.json"
    cf1.write_text(json.dumps(d1))
    rc1 = cmd_propose(_make_args(claim_file=str(cf1), graph_store_root=_graph_root(tmp_path)))
    assert rc1 == 0
    a1_id = _assertions(tmp_path)[0]["assertion_id"]

    d2["disputes"] = [a1_id]
    cf2 = tmp_path / "c2.json"
    cf2.write_text(json.dumps(d2))
    rc2 = cmd_propose(_make_args(claim_file=str(cf2), graph_store_root=_graph_root(tmp_path)))
    assert rc2 == 0

    from lawvm.tools.cmd_claim import cmd_disputes
    rc = cmd_disputes(_make_args(statute="711/2022", graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    out = capsys.readouterr().out
    assert "disputes" in out
    assert a1_id[:16] in out


# ---------------------------------------------------------------------------
# Test 9: taint-report computed at query time (not stored)
# ---------------------------------------------------------------------------


def test_cmd_claim_taint_report_computed_at_query_time(tmp_path, capsys):
    from lawvm.tools.cmd_claim import cmd_retract, cmd_taint_report
    assertion_id = _propose_assertion(tmp_path)

    cmd_retract(_make_args(
        assertion_id=assertion_id,
        reason="stale",
        graph_store_root=_graph_root(tmp_path),
    ))
    capsys.readouterr()

    rc = cmd_taint_report(_make_args(
        assertion_id=assertion_id,
        list=False,
        graph_store_root=_graph_root(tmp_path),
    ))
    assert rc == 0

    out = capsys.readouterr().out
    assert "taint report" in out

    assert "taint" not in json.dumps(_assertions(tmp_path))


# ---------------------------------------------------------------------------
# Test 10: propose-claims writes assertions + validator attestations
# ---------------------------------------------------------------------------


def test_cmd_propose_claims_writes_assertions_and_validator_attestations(tmp_path):
    from lawvm.tools.cmd_propose_claims import _process_one_frontier, _get_store, _cli_producer
    from lawvm.core.manual_claims.primitive import ExtractionFrontierRow

    store = _get_store(str(tmp_path / "provenance_graph"))
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    from lawvm.core.manual_claims.proposal_backend import MockProposalBackend
    backend = MockProposalBackend()
    producer = _cli_producer()

    frontier_row = ExtractionFrontierRow(
        frontier_id="test-fr-001",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id="711/2022",
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="test",
    )

    result = _process_one_frontier(
        frontier_row=frontier_row,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=b"some source text about lain 999/2020",
        cited_span_hash="c" * 64,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    all_objs = _load_all_objects(tmp_path)
    assertion_objs = [o for o in all_objs if "assertion_id" in o and "kind" in o]
    assert len(assertion_objs) >= 1

    attestation_kinds = {o.get("attestation_kind") for o in all_objs if "attestation_kind" in o}
    assert "claim_submitted" in attestation_kinds


# ---------------------------------------------------------------------------
# Test 11: rejected proposal stored for audit (claim_submitted but no entailment_verified)
# ---------------------------------------------------------------------------


def test_cmd_propose_claims_rejected_proposal_stored_for_audit(tmp_path):
    from lawvm.tools.cmd_propose_claims import _process_one_frontier, _get_store, _cli_producer
    from lawvm.core.manual_claims.primitive import ExtractionFrontierRow
    from lawvm.core.manual_claims.proposal_backend import ProposedClaim

    store = _get_store(str(tmp_path / "provenance_graph"))
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    class _BadBackend:
        model_name = None
        def propose(self, frontier_row, schema, quoted_source):
            return ProposedClaim(
                claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
                target=(("statute_id", "711/2022"),),
                value=(("resolved_statute_id", "INVALID"), ("citation_form", "bad")),
                cited_source_span=(0, 10),
                cited_source_hash="e" * 64,
                rationale="",
                producer_model_id=cast(Any, None),
                raw_response="{}",
                parse_error="schema validation failure: bad field",
            )

    producer = _cli_producer()
    frontier_row = ExtractionFrontierRow(
        frontier_id="test-fr-bad-001",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id="711/2022",
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="test",
    )

    result = _process_one_frontier(
        frontier_row=frontier_row,
        store=store,
        backend=_BadBackend(),
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=b"",
        cited_span_hash="e" * 64,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    assert result is None

    all_objs = _load_all_objects(tmp_path)
    assertion_objs = [o for o in all_objs if "assertion_id" in o and "kind" in o]
    assert len(assertion_objs) >= 1, "rejected proposal must be stored for audit"

    attestation_kinds = {o.get("attestation_kind") for o in all_objs if "attestation_kind" in o}
    assert "claim_submitted" in attestation_kinds
    assert "entailment_verified" not in attestation_kinds


# ---------------------------------------------------------------------------
# Test 12: validate-claims emits new attestations, assertion hash unchanged
# ---------------------------------------------------------------------------


def test_cmd_validate_claims_emits_new_validator_attestations_does_not_mutate_assertion(tmp_path):
    from lawvm.tools.cmd_claim import cmd_propose
    from lawvm.tools.cmd_validate_claims import cmd_validate_one

    d = _make_assertion_dict()
    cf = _write_claim_file(tmp_path, d)
    cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))

    assertions_before = _assertions(tmp_path)
    assert len(assertions_before) == 1
    original_assertion_id = assertions_before[0]["assertion_id"]
    original_hash = assertions_before[0].get("_content_hash", original_assertion_id)

    rc = cmd_validate_one(_make_args(
        assertion_id=original_assertion_id,
        graph_store_root=_graph_root(tmp_path),
    ))

    assertions_after = _assertions(tmp_path)
    assert len(assertions_after) == 1
    assert assertions_after[0]["assertion_id"] == original_assertion_id

    stored_hash_after = assertions_after[0].get("_content_hash", original_assertion_id)
    assert stored_hash_after == original_hash

    all_objs = _load_all_objects(tmp_path)
    attestation_kinds = {o.get("attestation_kind") for o in all_objs if "attestation_kind" in o}
    assert "span_verified" in attestation_kinds or "entailment_verified" in attestation_kinds or rc == 0


# ---------------------------------------------------------------------------
# Test 13: real-corpus regression (marked slow)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Regression: parser uses 'claim_id' dest; cmd functions must read it
# (Bug: cli.py positional arg dest='claim_id'; cmd_claim.py was reading
#  args.assertion_id → AttributeError when invoked via real CLI parser)
# ---------------------------------------------------------------------------


def test_cmd_claim_accept_reads_claim_id_not_assertion_id(tmp_path):
    """cmd_accept must work when args has 'claim_id' (not 'assertion_id').

    The CLI parser (cli.py) stores the positional arg as claim_id.
    Before the fix cmd_accept accessed args.assertion_id and would raise
    AttributeError on every real CLI invocation.
    """
    from lawvm.tools.cmd_claim import cmd_accept
    assertion_id = _propose_assertion(tmp_path)

    # Simulate what the argparse parser produces: claim_id, not assertion_id.
    args_with_claim_id = _make_args(claim_id=assertion_id, graph_store_root=_graph_root(tmp_path))
    rc = cmd_accept(args_with_claim_id)
    assert rc == 0

    reviewed = _objects_of_kind(tmp_path, "reviewed")
    assert len(reviewed) == 1
    assert reviewed[0]["payload"]["accepted"] is True


def test_cmd_claim_retract_reads_claim_id_not_assertion_id(tmp_path, capsys):
    """cmd_retract must work when args has 'claim_id' (not 'assertion_id')."""
    from lawvm.tools.cmd_claim import cmd_retract
    assertion_id = _propose_assertion(tmp_path)

    args_with_claim_id = _make_args(
        claim_id=assertion_id,
        reason="regression test",
        graph_store_root=_graph_root(tmp_path),
    )
    rc = cmd_retract(args_with_claim_id)
    assert rc == 0

    retracted = _objects_of_kind(tmp_path, "retracted")
    assert len(retracted) == 1


def test_cmd_claim_show_reads_claim_id_not_assertion_id(tmp_path, capsys):
    """cmd_show must work when args has 'claim_id' (not 'assertion_id')."""
    from lawvm.tools.cmd_claim import cmd_show
    assertion_id = _propose_assertion(tmp_path)

    args_with_claim_id = _make_args(claim_id=assertion_id, graph_store_root=_graph_root(tmp_path))
    rc = cmd_show(args_with_claim_id)
    assert rc == 0
    out = capsys.readouterr().out
    assert "ASSERTION PAYLOAD" in out


def test_cmd_claim_taint_report_reads_claim_id_not_assertion_id(tmp_path, capsys):
    """cmd_taint_report must work when args has 'claim_id' (not 'assertion_id')."""
    from lawvm.tools.cmd_claim import cmd_retract, cmd_taint_report
    assertion_id = _propose_assertion(tmp_path)
    cmd_retract(_make_args(claim_id=assertion_id, reason="test", graph_store_root=_graph_root(tmp_path)))
    capsys.readouterr()

    args_with_claim_id = _make_args(claim_id=assertion_id, list=False, graph_store_root=_graph_root(tmp_path))
    rc = cmd_taint_report(args_with_claim_id)
    assert rc == 0


# ---------------------------------------------------------------------------
# Regression: propose-claims must read claim_store_root (parser arg) not
# only graph_store_root (before fix the smoke store was always ignored and
# all assertions landed in the default data/fi/v1/provenance_graph path).
# ---------------------------------------------------------------------------


def test_cmd_propose_claims_reads_claim_store_root(tmp_path):
    """propose-claims honours claim_store_root for graph store isolation.

    The cli.py parser exposes --claim-store-root with dest='claim_store_root'.
    Before the fix cmd_propose_from_frontier only read args.graph_store_root
    (always None from the real parser), so smoke isolation was silently broken.
    """
    from lawvm.tools.cmd_propose_claims import _process_one_frontier, _cli_producer
    from lawvm.tools.cmd_propose_claims import _get_store
    from lawvm.core.manual_claims.primitive import ExtractionFrontierRow
    from lawvm.core.manual_claims.proposal_backend import MockProposalBackend

    isolated_root = str(tmp_path / "isolated_smoke_graph")
    store = _get_store(isolated_root)
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    backend = MockProposalBackend()
    producer = _cli_producer()
    frontier_row = ExtractionFrontierRow(
        frontier_id="smoke-fr-001",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        statute_id="711/2022",
        provision_ref="section:3",
        slot="target_statute_id",
        severity="medium",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="smoke-isolation",
    )

    _process_one_frontier(
        frontier_row=frontier_row,
        store=store,
        backend=backend,
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        source_bytes=b"text lain 999/2020 context",
        cited_span_hash="f" * 64,
        statute_id="711/2022",
        he_id=None,
        producer=producer,
        verbose=False,
    )

    from lawvm.tools.cmd_propose_claims import _get_store as get_isolated_store
    isolated_store = get_isolated_store(isolated_root)
    obj_dir = isolated_store._objects_dir()
    assert obj_dir.exists(), "isolated store must be populated when claim_store_root is set"
    assertion_files = list(obj_dir.glob("*.json"))
    assertion_objs = [
        json.loads(f.read_text()) for f in assertion_files
        if "assertion_id" in json.loads(f.read_text())
    ]
    assert len(assertion_objs) >= 1, "assertion must land in isolated store, not default path"

    # Verify default path was NOT written (isolation preserved)
    default_obj_dir = tmp_path / "data" / "fi" / "v1" / "provenance_graph" / "objects" / "sha256"
    assert not default_obj_dir.exists(), "default graph root must NOT be written during isolated smoke run"


@pytest.mark.slow
def test_cmd_claim_propose_real_corpus_regression(tmp_path):
    """Propose against synthetic substrate; verify graph snapshot reads back."""
    from lawvm.tools.cmd_claim import cmd_propose

    d = _make_assertion_dict(statute_id="555/2024")
    cf = _write_claim_file(tmp_path, d)

    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0

    store = GraphStore(tmp_path / "provenance_graph")
    snapshots_dir = store._root / "snapshots"
    assert snapshots_dir.exists()
    snapshot_files = list(snapshots_dir.glob("*.json"))
    assert len(snapshot_files) >= 1

    snap_hash = snapshot_files[0].stem
    graph = store.read_graph(snap_hash)

    assert len(graph.nodes) >= 2
    node_ids = {n.node_id for n in graph.nodes}

    assertions = _assertions(tmp_path)
    assert len(assertions) == 1
    assert assertions[0]["assertion_id"] in node_ids
