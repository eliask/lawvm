"""lawvm claim — operator CLI for manual compilation claims (v3 graph-native).

Subcommands:
  propose         --claim-file FILE.json
  accept          ASSERTION_ID
  reject          ASSERTION_ID --reason "..."
  retract         ASSERTION_ID --reason "..."
  supersede       OLD_ID --with NEW_FILE.json --delta-reason "..."
  show            ASSERTION_ID [--profile PROFILE_NAME]
  list            [--kind ...] [--layer ...] [--has-attestation-kind ...]
  history         --target PROVISION_REF
  disputes        --statute STATUTE_ID
  taint-report    ASSERTION_ID | --list

All state is computed at query time from the attestation graph (§9).
No stored status fields on assertions.

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import json
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from typing import Any

from lawvm.core.manual_claims.native import (
    attest,
    manual_claim_lifecycle_status,
    manual_claim_review_status,
    query_state_from_store,
    submit_assertion,
)
from lawvm.core.retraction_taint_projection import (
    filter_retraction_taint_projection_by_build,
    project_retraction_taint,
    render_retraction_taint,
)
from lawvm.core.provenance_graph import (
    GraphBuilder,
    Interval,
    Producer,
    ProvenanceAssertion,
    ProvenanceAttestation,
    SourceRef,
    ArtifactRef,
    assertion_canonical_payload,
    attestation_kind_registry_hash,
    _sha256,
)
from lawvm.core.provenance_graph_storage import (
    GraphStore,
    _deserialize_assertion,
    _deserialize_attestation,
)

_DEFAULT_GRAPH_ROOT = "data/fi/v1/provenance_graph"
_MISSING_ARG = object()


def _arg_value(args: object, name: str, *, default: object = _MISSING_ARG) -> object:
    value = getattr(args, name, _MISSING_ARG)
    if value is _MISSING_ARG:
        if default is _MISSING_ARG:
            raise AttributeError(f"claim CLI parser did not provide expected argument: {name}")
        return default
    return value


def _arg_str(args: object, name: str) -> str:
    value = _arg_value(args, name)
    if not isinstance(value, str):
        raise TypeError(f"claim CLI argument {name!r} must be str, got {type(value).__name__}")
    return value


def _arg_optional_str(args: object, name: str) -> Optional[str]:
    value = _arg_value(args, name, default=None)
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"claim CLI argument {name!r} must be str or None, got {type(value).__name__}")
    return value


def _arg_bool(args: object, name: str, *, default: bool = False) -> bool:
    value = _arg_value(args, name, default=default)
    if not isinstance(value, bool):
        raise TypeError(f"claim CLI argument {name!r} must be bool, got {type(value).__name__}")
    return value


def _claim_id_arg(args: object) -> str:
    return _arg_optional_str(args, "claim_id") or _arg_str(args, "assertion_id")


def _get_store(graph_store_root: str) -> GraphStore:
    return GraphStore(Path(graph_store_root))


def _resolve_graph_store_root(args: object) -> str:
    """Resolve graph store root from args (claim_id/graph_store_root), env, or default.

    Priority: args.graph_store_root > LAWVM_GRAPH_STORE_ROOT env > _DEFAULT_GRAPH_ROOT.
    The environment fallback keeps smoke tests and embedded callers isolated
    even when they bypass the argparse surface.
    """
    import os
    return (
        getattr(args, "graph_store_root", None)
        or os.environ.get("LAWVM_GRAPH_STORE_ROOT")
        or _DEFAULT_GRAPH_ROOT
    )


def _cli_producer() -> Producer:
    return Producer(
        producer_id="lawvm.cli.operator",
        producer_kind="human",
        public_key=None,
        metadata={"environment": "lawvm-claim-cli"},
    )


def _load_assertion_from_file(path: Path) -> ProvenanceAssertion:
    """Load a ProvenanceAssertion from a JSON file, computing assertion_id."""
    d = json.loads(path.read_text(encoding="utf-8"))
    return _assertion_from_dict(d)


def _assertion_from_dict(d: dict[str, Any]) -> ProvenanceAssertion:
    """Build ProvenanceAssertion from JSON dict, recomputing assertion_id."""
    from datetime import date

    valid_at_d = d.get("valid_at", {})
    if isinstance(valid_at_d, dict):
        start_str = valid_at_d.get("start", "2000-01-01")
        end_str = valid_at_d.get("end")
    elif isinstance(valid_at_d, (list, tuple)) and len(valid_at_d) >= 1:
        start_str = str(valid_at_d[0]) if valid_at_d[0] else "2000-01-01"
        end_str = str(valid_at_d[1]) if len(valid_at_d) > 1 and valid_at_d[1] else None
    else:
        start_str = "2000-01-01"
        end_str = None

    valid_at = Interval(
        start=date.fromisoformat(start_str),
        end=date.fromisoformat(end_str) if end_str else None,
    )

    source_refs_raw = d.get("source_refs", [])
    source_refs = tuple(
        SourceRef(
            artifact_digest=r["artifact_digest"],
            structural_locator=r["structural_locator"],
            bounded_quote_hash=r["bounded_quote_hash"],
            normalization_policy_id=r["normalization_policy_id"],
            byte_range=(r["byte_range"][0], r["byte_range"][1]),
        )
        for r in source_refs_raw
    )

    dependency_refs_raw = d.get("dependency_refs", [])
    dependency_refs = tuple(
        ArtifactRef(
            artifact_type=r["artifact_type"],
            artifact_id=r["artifact_id"],
            content_hash=r["content_hash"],
        )
        for r in dependency_refs_raw
    )

    supersedes = tuple(str(x) for x in d.get("supersedes", []))
    disputes = tuple(str(x) for x in d.get("disputes", []))

    temp = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version=str(d.get("schema_version", "v1")),
        jurisdiction=str(d.get("jurisdiction", "fi")),
        kind=str(d["kind"]),
        layer=str(d.get("layer", "extraction")),
        scope=d.get("scope", {}),
        target=d.get("target", {}),
        value=d.get("value", {}),
        source_refs=source_refs,
        dependency_refs=dependency_refs,
        valid_at=valid_at,
        supersedes=supersedes,
        disputes=disputes,
        rationale=str(d.get("rationale", "")),
    )
    canonical = assertion_canonical_payload(temp)
    assertion_id = _sha256(canonical)

    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version=temp.schema_version,
        jurisdiction=temp.jurisdiction,
        kind=temp.kind,
        layer=temp.layer,
        scope=temp.scope,
        target=temp.target,
        value=temp.value,
        source_refs=temp.source_refs,
        dependency_refs=temp.dependency_refs,
        valid_at=temp.valid_at,
        supersedes=temp.supersedes,
        disputes=temp.disputes,
        rationale=temp.rationale,
    )


def _build_live_snapshot(store: GraphStore):
    """Build a ProvenanceGraph from all persisted objects, builds, and edges."""
    from lawvm.core.build_consumption import build_node_for_record

    reg_hash = attestation_kind_registry_hash()
    builder = GraphBuilder(attestation_kind_registry_hash_val=reg_hash)
    objects_dir = store._objects_dir()
    if objects_dir.exists():
        for f in sorted(objects_dir.glob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            if "assertion_id" in d and "kind" in d:
                a = _deserialize_assertion(d)
                builder.add_assertion(a)
            elif "attestation_id" in d and "attestation_kind" in d:
                a = _deserialize_attestation(d)
                builder.add_attestation(a)
    for record in store.load_build_record_index().values():
        builder.add_node(build_node_for_record(record))
    for edge in store.load_all_edges():
        builder.add_edge(edge)
    return builder.finalize()


def _write_live_snapshot(store: GraphStore) -> str:
    """Rebuild graph snapshot from all objects and write it. Returns snapshot_hash."""
    graph = _build_live_snapshot(store)
    store.write_graph(graph)
    return graph.snapshot_hash


def _read_assertion_from_store(store: GraphStore, assertion_id: str) -> Optional[ProvenanceAssertion]:
    obj_path = store._objects_dir() / f"{assertion_id}.json"
    if not obj_path.exists():
        return None
    d = json.loads(obj_path.read_text(encoding="utf-8"))
    if "assertion_id" not in d:
        return None
    return _deserialize_assertion(d)


def _read_all_attestations_for(store: GraphStore, assertion_id: str) -> list[ProvenanceAttestation]:
    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return []
    result = []
    for f in sorted(objects_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "attestation_id" in d and "attestation_kind" in d:
            if d.get("subject", {}).get("artifact_id") == assertion_id:
                result.append(_deserialize_attestation(d))
    result.sort(key=lambda a: a.produced_at)
    return result


def _load_all_assertions(store: GraphStore) -> list[ProvenanceAssertion]:
    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return []
    result = []
    for f in sorted(objects_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "assertion_id" in d and "kind" in d:
            result.append(_deserialize_assertion(d))
    return result


def _load_all_attestations(store: GraphStore) -> dict[str, ProvenanceAttestation]:
    """Returns dict: attestation_id -> ProvenanceAttestation."""
    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return {}
    result = {}
    for f in sorted(objects_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if "attestation_id" in d and "attestation_kind" in d:
            a = _deserialize_attestation(d)
            result[a.attestation_id] = a
    return result


def _default_policy(claim_kind: str):
    from lawvm.core.evidence_policy import registry_from_dict
    policy_path = Path("data/fi/v1/evidence_policy/lawvm.fi.v1.evidence_policy.v0.json")
    if policy_path.exists():
        reg = registry_from_dict(json.loads(policy_path.read_text(encoding="utf-8")))
        pred = reg.get_predicate_for_claim_kind(claim_kind)
        if pred is not None:
            return pred
    from lawvm.core.evidence_policy import EvidenceGraphPredicate, exists
    return EvidenceGraphPredicate(
        predicate_id=f"default.{claim_kind}",
        claim_kind=claim_kind,
        required=(exists("claim_submitted"),),
    )


def _default_profile(allows_attested_reference_resolution: bool = True):
    from lawvm.core.compile_result import StrictProfile
    return StrictProfile(
        name="fi_strict_with_attested_reference_resolution",
        allows_attested_reference_resolution=allows_attested_reference_resolution,
    )


def _profile_from_cli_name(profile_name: Optional[str]):
    from lawvm.core.compile_result import StrictProfile

    if profile_name in (None, "", "default", "fi_strict_with_attested_reference_resolution"):
        return _default_profile(allows_attested_reference_resolution=True)
    if profile_name in ("strict", "fi_strict", "deterministic_only"):
        return StrictProfile(
            name=str(profile_name),
            allows_attested_reference_resolution=False,
        )
    raise ValueError(
        "unsupported claim show profile "
        f"{profile_name!r}; expected default, fi_strict_with_attested_reference_resolution, "
        "strict, fi_strict, or deterministic_only"
    )


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def cmd_propose(args: object) -> int:
    """Load assertion from JSON; submit to graph store; emit claim_submitted attestation."""
    claim_file = Path(_arg_str(args, "claim_file"))
    if not claim_file.exists():
        print(f"error: claim file not found: {claim_file}", file=sys.stderr)
        return 1

    assertion = _load_assertion_from_file(claim_file)
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    producer = _cli_producer()
    assertion_id = submit_assertion(store, assertion, producer)
    _write_live_snapshot(store)
    print(f"proposed: {assertion_id}")
    return 0


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


def cmd_accept(args: object) -> int:
    """Emit reviewed attestation with accepted=True."""
    assertion_id = _claim_id_arg(args)
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    if _read_assertion_from_store(store, assertion_id) is None:
        print(f"error: assertion not found: {assertion_id}", file=sys.stderr)
        return 1

    producer = _cli_producer()
    attest_id = attest(store, assertion_id, "reviewed", {"accepted": True}, producer)
    _write_live_snapshot(store)
    print(f"accepted: {attest_id}")
    return 0


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def cmd_reject(args: object) -> int:
    """Emit reviewed attestation with accepted=False."""
    assertion_id = _claim_id_arg(args)
    reason = _arg_str(args, "reason")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    if _read_assertion_from_store(store, assertion_id) is None:
        print(f"error: assertion not found: {assertion_id}", file=sys.stderr)
        return 1

    producer = _cli_producer()
    attest_id = attest(store, assertion_id, "reviewed", {"accepted": False, "reason": reason}, producer)
    _write_live_snapshot(store)
    print(f"rejected: {attest_id}")
    return 0


# ---------------------------------------------------------------------------
# retract
# ---------------------------------------------------------------------------


def cmd_retract(args: object) -> int:
    """Emit retracted attestation; render the query-time retraction taint projection.

    Per-build states (computed from the build node + consumed_by_build edges):
      - no build node in graph            -> build_unknown (not clean)
      - record.consumption_instrumented=F -> build_consumption_uninstrumented (not clean)
      - consumed_subject_count == 0       -> clean
      - consumed_subject_count > 0        -> edge query (tainted / clean / invalid)
    """
    assertion_id = _claim_id_arg(args)
    reason = _arg_str(args, "reason")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    if _read_assertion_from_store(store, assertion_id) is None:
        print(f"error: assertion not found: {assertion_id}", file=sys.stderr)
        return 1

    producer = _cli_producer()
    attest_id = attest(store, assertion_id, "retracted", {"reason": reason}, producer)
    snapshot_hash = _write_live_snapshot(store)
    print(f"retracted: {attest_id}")

    graph = store.read_graph(snapshot_hash)
    attestation_index = _load_all_attestations(store)
    projection = project_retraction_taint(
        graph, (assertion_id,), attestation_index, store.load_build_record_index()
    )
    print()
    print(render_retraction_taint(projection))
    return 0


# ---------------------------------------------------------------------------
# supersede
# ---------------------------------------------------------------------------


def cmd_supersede(args: object) -> int:
    """Submit new assertion superseding old; emit superseded attestation."""
    old_id = _arg_str(args, "old_assertion_id")
    new_file = Path(_arg_str(args, "with_file"))
    delta_reason = _arg_optional_str(args, "delta_reason") or ""
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    if _read_assertion_from_store(store, old_id) is None:
        print(f"error: assertion not found: {old_id}", file=sys.stderr)
        return 1

    if not new_file.exists():
        print(f"error: new assertion file not found: {new_file}", file=sys.stderr)
        return 1

    raw = json.loads(new_file.read_text(encoding="utf-8"))
    raw_supersedes = list(raw.get("supersedes", []))
    if old_id not in raw_supersedes:
        raw_supersedes.append(old_id)
    raw["supersedes"] = raw_supersedes

    new_assertion = _assertion_from_dict(raw)
    producer = _cli_producer()
    new_id = submit_assertion(store, new_assertion, producer)
    attest_id = attest(
        store, old_id, "superseded",
        {"superseding_assertion_id": new_id, "delta_reason": delta_reason},
        producer,
    )
    _write_live_snapshot(store)
    print(f"superseded: old={old_id[:32]}... new={new_id[:32]}...")
    print(f"superseded attestation: {attest_id}")
    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def cmd_show(args: object) -> int:
    """Render assertion + attestations + authorization result."""
    assertion_id = _claim_id_arg(args)
    profile_name = _arg_optional_str(args, "profile")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    assertion = _read_assertion_from_store(store, assertion_id)
    if assertion is None:
        print(f"error: assertion not found: {assertion_id}", file=sys.stderr)
        return 1

    attestations = _read_all_attestations_for(store, assertion_id)

    print("=" * 72)
    print(f"ASSERTION PAYLOAD  ({assertion_id[:32]}...)")
    print("=" * 72)
    print(f"  kind:           {assertion.kind}")
    print(f"  layer:          {assertion.layer}")
    print(f"  jurisdiction:   {assertion.jurisdiction}")
    print(f"  schema_version: {assertion.schema_version}")
    print(f"  scope:          {dict(assertion.scope)}")
    print(f"  target:         {dict(assertion.target)}")
    print(f"  value:          {dict(assertion.value)}")
    print(f"  valid_at:       [{assertion.valid_at.start} .. {assertion.valid_at.end or 'open'}]")
    print(f"  supersedes:     {assertion.supersedes}")
    print(f"  disputes:       {assertion.disputes}")
    print(f"  rationale:      {assertion.rationale[:80]}")
    print(f"  source_refs:    {len(assertion.source_refs)} ref(s)")

    print()
    print("=" * 72)
    print(f"ATTESTATIONS  ({len(attestations)} total, chronological)")
    print("=" * 72)
    for attest_obj in attestations:
        print(
            f"  [{attest_obj.produced_at.isoformat()[:19]}] "
            f"{attest_obj.attestation_kind:<28} "
            f"id={attest_obj.attestation_id[:16]}..."
        )
        for k, v in attest_obj.payload.items():
            print(f"    {k}: {v}")

    print()
    print("=" * 72)
    print("AUTHORIZATION RESULT")
    print("=" * 72)
    snapshot_files = list((store._root / "snapshots").glob("*.json")) if (store._root / "snapshots").exists() else []
    if not snapshot_files:
        print("  (no snapshot; run 'claim propose' to create one)")
    else:
        latest_snap = max(snapshot_files, key=lambda p: p.stat().st_mtime)
        snap_hash = latest_snap.stem
        policy = _default_policy(assertion.kind)
        try:
            profile = _profile_from_cli_name(profile_name)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        result = query_state_from_store(
            store, snap_hash, assertion_id,
            policy=policy, profile=profile,
            at=datetime.now(tz=timezone.utc),
        )
        print(f"  authorized:           {result.authorized}")
        print(f"  policy_id:            {result.policy_id}")
        print(f"  profile_name:         {result.profile_name}")
        print(f"  satisfied_clauses:    {result.satisfied_clauses}")
        print(f"  unsatisfied_clauses:  {result.unsatisfied_clauses}")
        print(f"  forbidden_present:    {result.forbidden_present}")
        print(f"  evidence_bundle_hash: {result.evidence_bundle_hash[:32]}...")

    print()
    print("=" * 72)
    print("SOURCE PROVENANCE")
    print("=" * 72)
    for i, ref in enumerate(assertion.source_refs):
        print(f"  source_ref[{i}]:")
        print(f"    artifact_digest:        {ref.artifact_digest[:32]}...")
        print(f"    structural_locator:     {ref.structural_locator}")
        print(f"    normalization_policy:   {ref.normalization_policy_id}")
        print(f"    byte_range:             {ref.byte_range}")

    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: object) -> int:
    """List assertions with optional filters."""
    kind_filter: Optional[str] = getattr(args, "kind", None)
    layer_filter: Optional[str] = getattr(args, "layer", None)
    status_filter: Optional[str] = getattr(args, "status", None)
    review_status_filter: Optional[str] = getattr(args, "review_status", None)
    has_attestation_kind: Optional[str] = getattr(args, "has_attestation_kind", None)
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    assertions = _load_all_assertions(store)
    if not assertions:
        print("no assertions in graph store")
        return 0

    attestations_by_subject: dict[str, list[ProvenanceAttestation]] = {}
    for attest_obj in _load_all_attestations(store).values():
        subj = attest_obj.subject.artifact_id
        attestations_by_subject.setdefault(subj, []).append(attest_obj)

    rows = []
    for assertion in assertions:
        if kind_filter and assertion.kind != kind_filter:
            continue
        if layer_filter and assertion.layer != layer_filter:
            continue
        attestations = attestations_by_subject.get(assertion.assertion_id, [])
        status = manual_claim_lifecycle_status(attestations)
        review_status = manual_claim_review_status(attestations)
        if status_filter and status != status_filter:
            continue
        if review_status_filter and review_status != review_status_filter:
            continue
        if has_attestation_kind:
            kinds = [attestation.attestation_kind for attestation in attestations]
            if has_attestation_kind not in kinds:
                continue
        rows.append({
            "assertion_id": assertion.assertion_id[:16] + "...",
            "kind": assertion.kind,
            "layer": assertion.layer,
            "lifecycle_status": status,
            "review_status": review_status,
            "jurisdiction": assertion.jurisdiction,
            "valid_from": str(assertion.valid_at.start),
        })

    if not rows:
        print("no assertions match filters")
        return 0

    headers = [
        "assertion_id",
        "kind",
        "layer",
        "lifecycle_status",
        "review_status",
        "jurisdiction",
        "valid_from",
    ]
    widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
    fmt = "  ".join(f"{{:<{widths[h]}}}" for h in headers)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * widths[h] for h in headers)))
    for row in rows:
        print(fmt.format(*[row[h] for h in headers]))
    return 0


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def cmd_history(args: object) -> int:
    """Show all assertions targeting a provision_ref over time, chronologically."""
    target_ref = _arg_str(args, "target")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    assertions = _load_all_assertions(store)
    matched = [
        a for a in assertions
        if a.scope.get("provision_ref") == target_ref
        or dict(a.target).get("provision_ref") == target_ref
    ]
    matched.sort(key=lambda a: a.valid_at.start)

    if not matched:
        print(f"no assertions found targeting provision_ref={target_ref!r}")
        return 0

    print(f"history for provision_ref={target_ref!r}  ({len(matched)} assertions)")
    for a in matched:
        print(f"  [{a.valid_at.start}] {a.assertion_id[:32]}...  kind={a.kind}  layer={a.layer}")
        print(f"    value: {dict(a.value)}")
    return 0


# ---------------------------------------------------------------------------
# disputes
# ---------------------------------------------------------------------------


def cmd_disputes(args: object) -> int:
    """Show conflicting assertion pairs for a statute."""
    statute_id = _arg_str(args, "statute")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    assertions = [
        a for a in _load_all_assertions(store)
        if a.scope.get("statute_id") == statute_id
    ]

    pairs = []
    seen: set[tuple[str, str]] = set()
    for a in assertions:
        for disputed_id in a.disputes:
            left, right = sorted((a.assertion_id, disputed_id))
            key = (left, right)
            if key not in seen:
                seen.add(key)
                pairs.append((a.assertion_id, disputed_id))

    if not pairs:
        print(f"no dispute pairs found for statute {statute_id!r}")
        return 0

    print(f"disputes for statute {statute_id!r}  ({len(pairs)} pair(s))")
    for a_id, b_id in pairs:
        print(f"  {a_id[:32]}...  disputes  {b_id[:32]}...")
    return 0


# ---------------------------------------------------------------------------
# taint-report
# ---------------------------------------------------------------------------


def cmd_taint_report(args: object) -> int:
    """Compute retraction taint at query time (not from stored taint)."""
    assertion_id = _arg_optional_str(args, "claim_id") or _arg_optional_str(args, "assertion_id")
    list_all = _arg_bool(args, "list", default=False)
    build_filter = _arg_optional_str(args, "build")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    graph = _build_live_snapshot(store)
    attestation_index = _load_all_attestations(store)
    build_record_index = store.load_build_record_index()

    if list_all or (build_filter is not None and assertion_id is None):
        retracted_ids = [
            a.subject.artifact_id
            for a in attestation_index.values()
            if a.attestation_kind == "retracted"
        ]
        if not retracted_ids:
            print("no retracted assertions found")
            return 0
        unique_retracted_ids = tuple(sorted(set(retracted_ids)))
        rows: list[tuple[str, int]] = []
        for rid in unique_retracted_ids:
            projection = project_retraction_taint(
                graph, (rid,), attestation_index, build_record_index
            )
            if build_filter is not None:
                projection = filter_retraction_taint_projection_by_build(
                    projection,
                    build_filter,
                )
                if not projection.builds:
                    continue
            taint_count = sum(
                1
                for b in projection.builds
                for f in b.status_finding.findings
                if f.retracted_assertion_id == rid
            )
            rows.append((rid, taint_count))
        if build_filter is not None and not rows:
            print(f"no retracted assertions affect build {build_filter!r}")
            return 0
        if build_filter is not None:
            print(f"{len(rows)} retracted assertion(s) affecting build {build_filter!r}:")
        else:
            print(f"{len(unique_retracted_ids)} retracted assertion(s):")
        for rid, taint_count in rows:
            print(f"  {rid[:32]}...  taint_count={taint_count}")
        return 0

    if assertion_id is None:
        print("error: provide ASSERTION_ID, --list, or --build", file=sys.stderr)
        return 1

    projection = project_retraction_taint(
        graph, (assertion_id,), attestation_index, build_record_index
    )
    if build_filter is not None:
        projection = filter_retraction_taint_projection_by_build(projection, build_filter)
        print(f"taint report for {assertion_id[:32]}... affecting build {build_filter!r}")
    else:
        print(f"taint report for {assertion_id[:32]}...")
    print(render_retraction_taint(projection))
    return 0


# ---------------------------------------------------------------------------
# main dispatch
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    subcmd = _arg_optional_str(args, "claim_subcommand")
    dispatch: dict[str, Callable[[object], int]] = {
        "propose": cmd_propose,
        "accept": cmd_accept,
        "reject": cmd_reject,
        "retract": cmd_retract,
        "supersede": cmd_supersede,
        "show": cmd_show,
        "list": cmd_list,
        "history": cmd_history,
        "disputes": cmd_disputes,
        "taint-report": cmd_taint_report,
    }
    fn = dispatch.get(subcmd or "")
    if fn is None:
        print(f"unknown claim subcommand: {subcmd!r}", file=sys.stderr)
        sys.exit(1)
    rc = fn(args)
    if rc:
        sys.exit(rc)
