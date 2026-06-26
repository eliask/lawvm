"""Tests for the build-consumption recorder and graph-native retraction taint.

Ports the four legacy retraction-lifecycle taint tests (formerly asserting
file-based ClaimRetractionTaintReport artifacts) onto the v3 graph-native
consumption model: builds register through the production recorder
(``persist_taintable_build_artifact`` / ``record_build_in_store``), taint is
a query-time projection over consumed_by_build edges, and the CLI renders
that projection — no filesystem taint module exists.

Plus the guard-liveness case: a known build that DECLARES consumption
(``consumed_subject_count > 0``) but has no edges must be invalid, never
clean — distinguishing "consumed nothing" from "emitter forgot".
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Optional, cast

import pytest

importlib.import_module("lawvm.finland.claim_kinds")

from lawvm.core.build_consumption import (
    BuildConsumptionError,
    BuildKind,
    BuildConsumptionStatus,
    BuildRef,
    BuildRecord,
    build_consumption_status,
    build_node_for_record,
    build_record_content_hash,
    consumed_subject_root,
    consumption_edge,
    persist_taintable_build_artifact,
    query_retraction_taint_for_build_refs,
    record_build_in_store,
    validate_build_consumption,
    BuildConsumptionEdgePayload,
)
from lawvm.core.provenance_graph import (
    ArtifactRef,
    GraphBuilder,
    attestation_kind_registry_hash,
)
from lawvm.core.provenance_graph_storage import GraphStore
from lawvm.core.retraction_taint_projection import (
    project_retraction_taint,
    render_retraction_taint,
)

JsonObj = dict[str, Any]


# ---------------------------------------------------------------------------
# Helpers (graph-store CLI fixtures, mirroring test_cmd_claim_v3)
# ---------------------------------------------------------------------------


def _make_assertion_dict(resolved: str = "1234/2020") -> JsonObj:
    return {
        "kind": "fi.v1.INLINE_STATUTE_RESOLUTION",
        "layer": "extraction",
        "jurisdiction": "fi",
        "schema_version": "v1",
        "scope": {"statute_id": "711/2022", "provision_ref": "section:3"},
        "target": {"statute_id": "711/2022", "mention_span": "100-120"},
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


class _Args:
    pass


def _make_args(**kwargs: Any) -> _Args:
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _graph_root(tmp_path: Path) -> str:
    return str(tmp_path / "provenance_graph")


def _store(tmp_path: Path) -> GraphStore:
    return GraphStore(Path(_graph_root(tmp_path)))


def _propose_assertion(tmp_path: Path, d: Optional[JsonObj] = None) -> str:
    from lawvm.tools.cmd_claim import cmd_propose

    if d is None:
        d = _make_assertion_dict()
    cf = tmp_path / f"claim_{abs(hash(json.dumps(d, sort_keys=True)))}.json"
    cf.write_text(json.dumps(d, indent=2), encoding="utf-8")
    rc = cmd_propose(_make_args(claim_file=str(cf), graph_store_root=_graph_root(tmp_path)))
    assert rc == 0
    obj_dir = Path(_graph_root(tmp_path)) / "objects" / "sha256"
    assertions = [
        json.loads(f.read_text())
        for f in sorted(obj_dir.glob("*.json"))
        if "assertion_id" in json.loads(f.read_text())
    ]
    # The newest assertion matching this dict's value
    for a in assertions:
        if a.get("value", {}).get("resolved_statute_id") == d["value"]["resolved_statute_id"]:
            return a["assertion_id"]
    raise AssertionError("proposed assertion not found in store")


def _artifact_ref(seed: str) -> ArtifactRef:
    import hashlib

    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return ArtifactRef(
        artifact_type="export_artifact",
        artifact_id=f"test-artifact-{seed}",
        content_hash=f"sha256:{digest}",
    )


def _record_build(
    tmp_path: Path,
    seed: str,
    consumed: tuple[str, ...],
    *,
    time_scope: Optional[JsonObj] = None,
    scope: Optional[JsonObj] = None,
) -> BuildRef:
    """Register a build through the PRODUCTION recorder."""
    return record_build_in_store(
        _store(tmp_path),
        artifact_ref=_artifact_ref(seed),
        build_kind="export",
        build_schema="lawvm.test_export.v0",
        consumed_assertion_ids=consumed,
        scope=scope or {"statute_id": "711/2022", "provision_ref": "section:3"},
        time_scope=time_scope or {"kind": "open_interval", "from": "2020-01-01", "to": None},
    )


def _retract(tmp_path: Path, assertion_id: str, capsys) -> str:
    from lawvm.tools.cmd_claim import cmd_retract

    rc = cmd_retract(
        _make_args(
            assertion_id=assertion_id,
            reason="ported lifecycle test retraction",
            graph_store_root=_graph_root(tmp_path),
        )
    )
    assert rc == 0
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# BuildRef / record / edge unit invariants
# ---------------------------------------------------------------------------


def test_build_ref_mint_is_content_derived() -> None:
    ref = _artifact_ref("x")
    a = BuildRef.mint(build_kind="cert", schema="lawvm.certificate.v0.4.1",
                      content_hash=ref.content_hash, artifact_ref=ref)
    b = BuildRef.mint(build_kind="cert", schema="lawvm.certificate.v0.4.1",
                      content_hash=ref.content_hash, artifact_ref=ref)
    assert a.build_id == b.build_id
    assert a.build_id == f"cert:lawvm.certificate.v0.4.1:{ref.content_hash}"


def test_build_ref_mint_rejects_unprefixed_hash_and_bad_kind() -> None:
    ref = _artifact_ref("x")
    with pytest.raises(BuildConsumptionError):
        BuildRef.mint(build_kind="cert", schema="s", content_hash="deadbeef",
                      artifact_ref=ref)
    bad_kind = cast(BuildKind, "nightly")  # runtime validation target
    with pytest.raises(BuildConsumptionError):
        BuildRef.mint(build_kind=bad_kind, schema="s",
                      content_hash=ref.content_hash, artifact_ref=ref)


def test_consumption_edge_refuses_build_id_mismatch() -> None:
    payload = BuildConsumptionEdgePayload(
        schema="lawvm.consumed_by_build.v0",
        build_id="export:s:sha256:aa",
        build_kind="export",
        build_schema="s",
        build_artifact_id="art",
        build_content_hash="sha256:aa",
        consumption_role="direct_assertion",
    )
    with pytest.raises(BuildConsumptionError):
        consumption_edge(
            consumed_subject_id="subj",
            build_node_id="export:s:sha256:bb",  # != payload.build_id
            payload=payload,
        )


def test_recorder_writes_node_record_and_edges(tmp_path: Path) -> None:
    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    captured: list[BuildRecord] = []
    ref = persist_taintable_build_artifact(
        graph_builder=builder,
        artifact_ref=_artifact_ref("rec"),
        build_kind="export",
        build_schema="lawvm.test_export.v0",
        consumed_assertion_ids=("assert-a", "assert-b"),
        dependency_assertion_ids=("assert-b", "assert-dep"),
        record_sink=captured.append,
    )
    graph = builder.finalize()
    (record,) = captured
    assert record.consumption_instrumented is True
    # assert-b is both direct and dependency -> one edge, direct role wins
    assert record.consumed_subject_count == 3
    assert record.consumed_subject_root == consumed_subject_root(
        ("assert-a", "assert-b", "assert-dep")
    )
    edges = [e for e in graph.edges if e.edge_type == "consumed_by_build"]
    assert len(edges) == 3
    roles = {e.src_node_id: e.payload["consumption_role"] for e in edges}
    assert roles == {
        "assert-a": "direct_assertion",
        "assert-b": "direct_assertion",
        "assert-dep": "dependency_assertion",
    }
    assert all(e.dst_node_id == ref.build_id for e in edges)
    validate_build_consumption(
        graph,
        ref,
        {"consumed_subject_ids": ("assert-a", "assert-b", "assert-dep")},
        build_record=record,
    )
    # Undeclared subject without expansion permission -> INVALID
    with pytest.raises(BuildConsumptionError):
        validate_build_consumption(
            graph,
            ref,
            {"consumed_subject_ids": ("assert-a", "assert-b")},
            build_record=record,
        )


# ---------------------------------------------------------------------------
# Ported: test_retraction_emits_taint_report
# (legacy asserted a written taint-report file; now the production recorder
# writes consumed_by_build edges and cmd_retract reports the tainted build)
# ---------------------------------------------------------------------------


def test_retraction_emits_taint_report(tmp_path: Path, capsys) -> None:
    assertion_id = _propose_assertion(tmp_path)
    build_ref = _record_build(tmp_path, "build-one", (assertion_id,))

    out = _retract(tmp_path, assertion_id, capsys)

    assert "taint report: 1 tainted build(s)" in out
    assert build_ref.build_id in out
    assert "status: tainted" in out
    # No filesystem taint-report artifacts anywhere (query-time projection only)
    assert not list(Path(_graph_root(tmp_path)).rglob("retracted_*.json"))


# ---------------------------------------------------------------------------
# Ported: test_retraction_lists_multiple_affected_builds
# ---------------------------------------------------------------------------


def test_retraction_lists_multiple_affected_builds(tmp_path: Path, capsys) -> None:
    assertion_id = _propose_assertion(tmp_path)
    b1 = _record_build(tmp_path, "build-b1", (assertion_id,))
    b2 = _record_build(tmp_path, "build-b2", (assertion_id,))
    assert b1.build_id != b2.build_id

    out = _retract(tmp_path, assertion_id, capsys)

    assert "taint report: 2 tainted build(s)" in out
    assert b1.build_id in out
    assert b2.build_id in out
    # Deterministic order: builds sorted by build_id
    first, second = sorted([b1.build_id, b2.build_id])
    assert out.index(first) < out.index(second)
    # Re-running the projection yields the same order
    out2 = _projection_output(tmp_path, assertion_id)
    assert out2.index(first) < out2.index(second)


def _projection_output(tmp_path: Path, assertion_id: str) -> str:
    from lawvm.tools.cmd_claim import _build_live_snapshot, _load_all_attestations

    store = _store(tmp_path)
    graph = _build_live_snapshot(store)
    projection = project_retraction_taint(
        graph,
        (assertion_id,),
        _load_all_attestations(store),
        store.load_build_record_index(),
    )
    return render_retraction_taint(projection)


# ---------------------------------------------------------------------------
# Ported: test_invalidated_PIT_intervals_present_in_report
# (legacy asserted invalidated_PIT_intervals in the stored report; now the
# scope/time_scope carried on the consumption edge is rendered at query time)
# ---------------------------------------------------------------------------


def test_invalidated_PIT_intervals_present_in_report(tmp_path: Path, capsys) -> None:
    assertion_id = _propose_assertion(tmp_path)
    _record_build(
        tmp_path,
        "build-pit",
        (assertion_id,),
        scope={"statute_id": "711/2022", "provision_ref": "section:3"},
        time_scope={"kind": "open_interval", "from": "2020-01-01", "to": None},
    )

    out = _retract(tmp_path, assertion_id, capsys)

    # The projection renders the consumed scope and PIT interval of the edge
    assert "time_scope:" in out
    assert '"from":"2020-01-01"' in out
    assert '"to":null' in out
    assert "scope:" in out
    assert '"provision_ref":"section:3"' in out


# ---------------------------------------------------------------------------
# Ported: test_taint_report_cli_renders
# ---------------------------------------------------------------------------


def test_taint_report_cli_renders(tmp_path: Path, capsys) -> None:
    from lawvm.tools.cmd_claim import cmd_taint_report

    assertion_id = _propose_assertion(tmp_path)
    build_ref = _record_build(tmp_path, "build-cli", (assertion_id,))
    _retract(tmp_path, assertion_id, capsys)

    rc = cmd_taint_report(
        _make_args(
            assertion_id=assertion_id,
            list=False,
            graph_store_root=_graph_root(tmp_path),
        )
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "taint report" in out
    assert build_ref.build_id in out
    assert "status: tainted" in out

    # --list aggregates taint counts from the same query-time projection
    rc = cmd_taint_report(
        _make_args(assertion_id=None, list=True, graph_store_root=_graph_root(tmp_path))
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "taint_count=1" in out


def test_taint_report_cli_filters_by_build(tmp_path: Path, capsys) -> None:
    from lawvm.tools.cmd_claim import cmd_taint_report

    assertion_id = _propose_assertion(tmp_path)
    kept_build = _record_build(tmp_path, "build-filter-kept", (assertion_id,))
    other_build = _record_build(tmp_path, "build-filter-other", (assertion_id,))
    _retract(tmp_path, assertion_id, capsys)

    rc = cmd_taint_report(
        _make_args(
            assertion_id=assertion_id,
            list=False,
            build=kept_build.build_id,
            graph_store_root=_graph_root(tmp_path),
        )
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert kept_build.build_id in out
    assert other_build.build_id not in out
    assert "status: tainted" in out

    rc = cmd_taint_report(
        _make_args(
            assertion_id=None,
            list=False,
            build=kept_build.build_id,
            graph_store_root=_graph_root(tmp_path),
        )
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert f"affecting build {kept_build.build_id!r}" in out
    assert assertion_id[:32] in out
    assert "taint_count=1" in out

    rc = cmd_taint_report(
        _make_args(
            assertion_id=None,
            list=False,
            build="missing-build",
            graph_store_root=_graph_root(tmp_path),
        )
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "no retracted assertions affect build 'missing-build'" in out


# ---------------------------------------------------------------------------
# Guard liveness: declared consumption without edges is never clean
# ---------------------------------------------------------------------------


def test_declared_consumption_without_edges_is_invalid_never_clean(
    tmp_path: Path, capsys
) -> None:
    assertion_id = _propose_assertion(tmp_path)
    store = _store(tmp_path)

    # Forge the failure mode the record exists to catch: a build node +
    # record DECLARING one consumed subject, with no edges written.
    artifact = _artifact_ref("forgot-edges")
    ref = BuildRef.mint(
        build_kind="export",
        schema="lawvm.test_export.v0",
        content_hash=artifact.content_hash,
        artifact_ref=artifact,
    )
    record = BuildRecord(
        build_id=ref.build_id,
        build_kind=ref.build_kind,
        schema=ref.schema,
        artifact_ref=ref.artifact_ref,
        consumption_instrumented=True,
        consumed_subject_count=1,
        consumed_subject_root=consumed_subject_root((assertion_id,)),
    )
    store.write_build_record(record)
    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    builder.add_node(build_node_for_record(record))
    store.write_graph(builder.finalize())

    from lawvm.tools.cmd_claim import _build_live_snapshot, _load_all_attestations

    graph = _build_live_snapshot(store)
    finding = build_consumption_status(
        graph, ref.build_id, _load_all_attestations(store), store.load_build_record_index()
    )
    assert finding.taint_status == BuildConsumptionStatus.INVALID_CONSUMPTION
    assert finding.taint_status != BuildConsumptionStatus.CLEAN
    assert "never clean" in finding.detail

    # validate_build_consumption refuses too
    with pytest.raises(BuildConsumptionError):
        validate_build_consumption(
            graph,
            ref,
            {"consumed_subject_ids": (assertion_id,)},
            build_record=record,
        )


# ---------------------------------------------------------------------------
# Four-state machine over BuildRefs
# ---------------------------------------------------------------------------


def test_four_state_machine_unknown_uninstrumented_clean(tmp_path: Path) -> None:
    store = _store(tmp_path)

    # Known clean build: instrumented, zero consumption
    clean_ref = _record_build(tmp_path, "build-clean", ())

    # Uninstrumented build: node present, record says not instrumented
    artifact = _artifact_ref("uninstrumented")
    unin_ref = BuildRef.mint(
        build_kind="export", schema="lawvm.test_export.v0",
        content_hash=artifact.content_hash, artifact_ref=artifact,
    )
    unin_record = BuildRecord(
        build_id=unin_ref.build_id,
        build_kind=unin_ref.build_kind,
        schema=unin_ref.schema,
        artifact_ref=unin_ref.artifact_ref,
        consumption_instrumented=False,
        consumed_subject_count=0,
        consumed_subject_root=consumed_subject_root(()),
    )
    store.write_build_record(unin_record)
    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    builder.add_node(build_node_for_record(unin_record))
    store.write_graph(builder.finalize())

    # Unknown build: never registered
    ghost_artifact = _artifact_ref("ghost")
    ghost_ref = BuildRef.mint(
        build_kind="export", schema="lawvm.test_export.v0",
        content_hash=ghost_artifact.content_hash, artifact_ref=ghost_artifact,
    )

    from lawvm.tools.cmd_claim import _build_live_snapshot, _load_all_attestations

    graph = _build_live_snapshot(store)
    findings = query_retraction_taint_for_build_refs(
        graph,
        (clean_ref, unin_ref, ghost_ref),
        _load_all_attestations(store),
        store.load_build_record_index(),
    )
    by_id = {f.build_id: f for f in findings}
    assert by_id[clean_ref.build_id].taint_status == BuildConsumptionStatus.CLEAN
    assert (
        by_id[unin_ref.build_id].taint_status
        == BuildConsumptionStatus.BUILD_CONSUMPTION_UNINSTRUMENTED
    )
    assert by_id[ghost_ref.build_id].taint_status == BuildConsumptionStatus.BUILD_UNKNOWN


def test_edge_payload_build_id_mismatch_refuses_query(tmp_path: Path) -> None:
    """Structural pre-query validation: dst/payload disagreement raises."""
    from lawvm.core.provenance_graph import GraphEdge, _sha256

    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    builder.add_edge(
        GraphEdge(
            edge_id=_sha256("tampered-edge"),
            edge_type="consumed_by_build",
            src_node_id="some-assertion",
            dst_node_id="export:s:sha256:real",
            payload={"build_id": "export:s:sha256:other"},
        )
    )
    graph = builder.finalize()
    with pytest.raises(BuildConsumptionError):
        project_retraction_taint(graph, ("some-assertion",), {}, {})


def test_record_content_hash_commits_to_count(tmp_path: Path) -> None:
    """Tampering the stored record (count) is caught against the node hash."""
    artifact = _artifact_ref("tamper")
    ref = BuildRef.mint(
        build_kind="export", schema="lawvm.test_export.v0",
        content_hash=artifact.content_hash, artifact_ref=artifact,
    )
    honest = BuildRecord(
        build_id=ref.build_id, build_kind=ref.build_kind, schema=ref.schema,
        artifact_ref=ref.artifact_ref, consumption_instrumented=True,
        consumed_subject_count=1, consumed_subject_root=consumed_subject_root(("a",)),
    )
    tampered = BuildRecord(
        build_id=ref.build_id, build_kind=ref.build_kind, schema=ref.schema,
        artifact_ref=ref.artifact_ref, consumption_instrumented=True,
        consumed_subject_count=0, consumed_subject_root=consumed_subject_root(()),
    )
    assert build_record_content_hash(honest) != build_record_content_hash(tampered)

    builder = GraphBuilder(
        attestation_kind_registry_hash_val=attestation_kind_registry_hash()
    )
    builder.add_node(build_node_for_record(honest))
    graph = builder.finalize()
    with pytest.raises(BuildConsumptionError):
        build_consumption_status(graph, ref.build_id, {}, {ref.build_id: tampered})
