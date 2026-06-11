"""lawvm validate-claims — re-run validators on graph assertions (v3 graph-native).

Subcommands:
  --assertion-id ID         re-run validators on one assertion
  --all [--kind ...] [--missing-attestation-kind ...]  bulk re-validate

Emits new validator attestations on success or failure.
Does NOT mutate the assertion itself (immutable per spec §12.1).

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lawvm.core.manual_claims.native import attest
from lawvm.core.provenance_graph import (
    GraphBuilder,
    Producer,
    ProvenanceAssertion,
    attestation_kind_registry_hash,
)
from lawvm.core.provenance_graph_storage import (
    GraphStore,
    _deserialize_assertion,
    _deserialize_attestation,
)

_DEFAULT_GRAPH_ROOT = "data/fi/v1/provenance_graph"


def _get_store(graph_store_root: str) -> GraphStore:
    return GraphStore(Path(graph_store_root))


def _resolve_graph_store_root(args: object) -> str:
    """Resolve graph store root from args, env, or default.

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


def _required_arg_str(args: object, primary: str, fallback: str | None = None) -> str:
    """Return a required string CLI argument from argparse-like objects."""
    value = getattr(args, primary, None)
    if value is None and fallback is not None:
        value = getattr(args, fallback, None)
    if not isinstance(value, str) or not value:
        names = primary if fallback is None else f"{primary}/{fallback}"
        raise ValueError(f"missing required CLI argument: {names}")
    return value


def _tool_producer() -> Producer:
    return Producer(
        producer_id="lawvm.validate-claims.tool",
        producer_kind="script",
        public_key=None,
        metadata={"environment": "lawvm-validate-claims"},
    )


def _write_live_snapshot(store: GraphStore) -> str:
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
    graph = builder.finalize()
    store.write_graph(graph)
    return graph.snapshot_hash


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


def _attestation_kinds_for(store: GraphStore, assertion_id: str) -> set[str]:
    """Return set of attestation kinds already present for this assertion."""
    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return set()
    kinds: set[str] = set()
    for f in objects_dir.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        if (
            "attestation_id" in d
            and "attestation_kind" in d
            and d.get("subject", {}).get("artifact_id") == assertion_id
        ):
            kinds.add(d["attestation_kind"])
    return kinds


def _claim_lifecycle_status_for(store: GraphStore, assertion_id: str) -> str:
    """Derive v3 graph-native claim lifecycle status from attestations."""

    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return "proposed"
    attestations = []
    for f in sorted(objects_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        if (
            "attestation_id" in d
            and "attestation_kind" in d
            and d.get("subject", {}).get("artifact_id") == assertion_id
        ):
            attestations.append(_deserialize_attestation(d))
    status = "proposed"
    for attestation in sorted(attestations, key=lambda item: item.produced_at):
        if attestation.attestation_kind == "claim_submitted":
            status = "proposed"
        elif attestation.attestation_kind == "reviewed":
            if attestation.payload.get("accepted") is True:
                status = "accepted"
            elif attestation.payload.get("accepted") is False:
                status = "rejected"
        elif attestation.attestation_kind == "retracted":
            status = "retracted"
        elif attestation.attestation_kind == "superseded":
            status = "superseded"
    return status


def _build_compat_claim(assertion: ProvenanceAssertion):
    """Build a v2.2-compatible ManualCompilationClaim for validator use."""
    from lawvm.core.manual_claims.primitive import (
        ClaimLayer,
        ClaimScope,
        _ProfileTagDeprecated as ProfileTag,
        Producer as V2Producer,
        SourceLocator,
        SourceWitnessType,
    )
    from lawvm.core.manual_claims.hashing import compute_claim_id
    from lawvm.core.manual_claims.primitive import ManualCompilationClaim

    scope_obj = ClaimScope(
        statute_id=str(assertion.scope.get("statute_id", "")),
        provision_ref=str(assertion.scope.get("provision_ref", "")) or None,
        valid_at_start=assertion.valid_at.start,
        valid_at_end=assertion.valid_at.end,
    )
    v2_producer = V2Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        environment="validate-claims",
    )
    byte_range = assertion.source_refs[0].byte_range if assertion.source_refs else (0, 0)
    artifact_digest = assertion.source_refs[0].artifact_digest if assertion.source_refs else "unknown"

    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind=assertion.kind,
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=scope_obj,
        target=tuple(dict(assertion.target).items()),
        value=tuple(dict(assertion.value).items()),
        source_witness_type=SourceWitnessType.LLM_PROPOSAL,
        producer=v2_producer,
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=str(assertion.scope.get("statute_id", "")),
            he_id=None,
            version_id=None,
        ),
        cited_source_span=byte_range,
        cited_source_hash=artifact_digest,
        dependency_fingerprint=(),
        valid_at=(assertion.valid_at.start, assertion.valid_at.end),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.NON_STRICT_WITH_CLAIMS,),
        rationale=assertion.rationale,
    )
    claim_id = compute_claim_id(partial)
    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version=partial.schema_version,
        jurisdiction=partial.jurisdiction,
        claim_kind=partial.claim_kind,
        claim_layer=partial.claim_layer,
        claim_scope=partial.claim_scope,
        target=partial.target,
        value=partial.value,
        source_witness_type=partial.source_witness_type,
        producer=partial.producer,
        cited_source_locator=partial.cited_source_locator,
        cited_source_span=partial.cited_source_span,
        cited_source_hash=partial.cited_source_hash,
        dependency_fingerprint=partial.dependency_fingerprint,
        valid_at=partial.valid_at,
        supersedes=partial.supersedes,
        supersession_delta_reason=partial.supersession_delta_reason,
        disputes=partial.disputes,
        requested_profiles=partial.requested_profiles,
        rationale=partial.rationale,
    )


def _validate_one_assertion(
    assertion: ProvenanceAssertion,
    store: GraphStore,
    source_bytes: bytes = b"",
    *,
    verbose: bool = True,
) -> bool:
    """Re-run validators; emit new attestations. Assertion is NOT mutated."""
    importlib.import_module("lawvm.finland.claim_kinds")
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec

    spec = get_claim_kind_spec(assertion.kind)
    if spec is None:
        if verbose:
            print(f"  warning: unknown kind {assertion.kind!r} — no validators", file=sys.stderr)
        return True

    compat_claim = _build_compat_claim(assertion)
    producer = _tool_producer()
    all_passed = True

    if spec.span_validator:
        result = spec.span_validator(compat_claim, source_bytes)
        status_str = "PASSED" if result.passed else "FAILED"
        if verbose:
            print(f"  span_verified: {status_str} — {result.reason}")
        attest(store, assertion.assertion_id, "span_verified",
               {"passed": result.passed, "reason": result.reason}, producer)
        if not result.passed:
            all_passed = False

    if spec.entailment_validator:
        result = spec.entailment_validator(compat_claim, source_bytes)
        status_str = "PASSED" if result.passed else "FAILED"
        if verbose:
            print(f"  entailment_verified: {status_str} — {result.reason}")
        attest(store, assertion.assertion_id, "entailment_verified",
               {"passed": result.passed, "reason": result.reason}, producer)
        if not result.passed:
            all_passed = False

    return all_passed


def _source_bytes_for_assertion(assertion: ProvenanceAssertion) -> bytes:
    """Fetch source bytes for assertion validators.

    The validator contract is identical for single-claim and bulk validation:
    span/entailment checks must run against the cited XML/source artifact, not
    an empty byte string. Missing providers or missing source stay visible as
    warnings and cause the validators to fail normally.
    """

    from lawvm.core.manual_claims.primitive import ClaimScope
    from lawvm.core.manual_claims.source_provider import _PROVIDERS

    jurisdiction = str(assertion.jurisdiction or "fi")
    source_provider = _PROVIDERS.get(jurisdiction)
    if source_provider is None:
        print(
            f"  warning: no source provider for jurisdiction {jurisdiction!r} — validators run on empty bytes",
            file=sys.stderr,
        )
        return b""

    statute_id = str(assertion.scope.get("statute_id", ""))
    provision_ref = str(assertion.scope.get("provision_ref", "")) or None
    scope = ClaimScope(
        statute_id=statute_id,
        provision_ref=provision_ref,
        valid_at_start=assertion.valid_at.start,
        valid_at_end=assertion.valid_at.end,
    )
    fetched = source_provider.fetch(scope)
    if fetched is None:
        print(
            f"  warning: source not found for {statute_id!r} — validators will run on empty bytes",
            file=sys.stderr,
        )
        return b""
    return fetched.bytes_


def cmd_validate_one(args: object) -> int:
    assertion_id = _required_arg_str(args, "claim_id", fallback="assertion_id")
    graph_store_root = _resolve_graph_store_root(args)
    store = _get_store(graph_store_root)

    obj_path = store._objects_dir() / f"{assertion_id}.json"
    if not obj_path.exists():
        print(f"error: assertion not found: {assertion_id}", file=sys.stderr)
        return 1

    d = json.loads(obj_path.read_text(encoding="utf-8"))
    assertion = _deserialize_assertion(d)

    source_bytes = _source_bytes_for_assertion(assertion)
    passed = _validate_one_assertion(assertion, store, source_bytes, verbose=True)
    _write_live_snapshot(store)
    return 0 if passed else 1


def cmd_validate_all(args: object) -> int:
    graph_store_root = _resolve_graph_store_root(args)
    kind_filter: Optional[str] = getattr(args, "kind", None)
    status_filter: Optional[str] = getattr(args, "status", None)
    missing_kind: Optional[str] = getattr(args, "missing_attestation_kind", None)

    store = _get_store(graph_store_root)
    assertions = _load_all_assertions(store)

    if not assertions:
        print("no assertions in graph store")
        return 0

    all_ok = True
    validated = 0

    for assertion in assertions:
        if kind_filter and assertion.kind != kind_filter:
            continue
        if status_filter and _claim_lifecycle_status_for(store, assertion.assertion_id) != status_filter:
            continue
        if missing_kind:
            existing_kinds = _attestation_kinds_for(store, assertion.assertion_id)
            if missing_kind in existing_kinds:
                continue

        print(f"\nvalidating {assertion.assertion_id[:32]}... ({assertion.kind})")
        source_bytes = _source_bytes_for_assertion(assertion)
        passed = _validate_one_assertion(assertion, store, source_bytes, verbose=True)
        if not passed:
            all_ok = False
        validated += 1

    _write_live_snapshot(store)
    print(f"\nvalidated {validated} assertion(s)")
    return 0 if all_ok else 1


def main(args: object) -> None:
    # Register Finnish source provider so span/entailment validators can fetch source bytes.
    from lawvm.tools.cmd_propose_claims import register_fi_source_provider
    register_fi_source_provider()

    assertion_id: Optional[str] = (
        getattr(args, "claim_id", None)
        or getattr(args, "assertion_id", None)
    )
    all_flag: bool = getattr(args, "all", False)

    if assertion_id:
        rc = cmd_validate_one(args)
    elif all_flag:
        rc = cmd_validate_all(args)
    else:
        print("error: one of --assertion-id or --all required", file=sys.stderr)
        rc = 1

    if rc:
        sys.exit(rc)
