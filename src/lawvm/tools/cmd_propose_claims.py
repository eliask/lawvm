"""lawvm propose-claims — LLM-aided claim proposal pipeline (v3 graph-native).

Subcommands:
  --from-frontier [--kind ...] [--limit N] [--backend mock|qwen]
  --gap-discovery --he HE_ID [--kind ...] [--backend ...]
  --he HE_ID --kind fi.v1.INLINE_STATUTE_RESOLUTION [--backend ...]

Quota rules:
  - Default limit: 100 proposals per invocation. --max-claims-no-cap removes it.
  - Skip if (claim_kind, target) already has a reviewed+accepted assertion.
  - One active proposed assertion per (claim_kind, target, value) triple.

Validation pipeline per proposal:
  1. schema validation (parse_error is None)
  2. span_existence_validator
  3. per-ClaimKind entailment_validator
  If all pass: submit_assertion + span_verified + entailment_verified attestations.
  If any fail: submit_assertion + schema_validated(success=False) only
               (rejected proposal stored for audit).

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from lawvm.core.manual_claims.native import (
    attest,
    submit_assertion,
)
from lawvm.core.manual_claims.primitive import (
    ClaimScope,
    ExtractionFrontierRow,
    GapDiscoveryRow,
)
from lawvm.core.manual_claims.proposal_backend import (
    ClaimProposalBackend,
    ClaimSchema,
    MockProposalBackend,
    ProposedClaim,
    QuotedSource,
)
from lawvm.core.manual_claims.source_provider import (
    get_source_provider,
    register_source_provider,
)
from lawvm.core.provenance_graph import (
    GraphBuilder,
    Interval,
    Producer,
    ProvenanceAssertion,
    SourceRef,
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

_PLAIN_STATUTE_RE = re.compile(r"\b(\d{1,4})/(\d{4})\b")


# ---------------------------------------------------------------------------
# Source provider registration
# ---------------------------------------------------------------------------


def register_fi_source_provider(corpus_root=None) -> None:
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    provider = FinlexSectionSourceProvider(
        corpus_root=Path(corpus_root) if corpus_root is not None else None
    )
    register_source_provider("fi", provider)


# ---------------------------------------------------------------------------
# Store + producer helpers
# ---------------------------------------------------------------------------


def _get_store(graph_store_root: Optional[str]) -> GraphStore:
    root = graph_store_root or _DEFAULT_GRAPH_ROOT
    return GraphStore(Path(root))


def _cli_producer(model_id: Optional[str] = None) -> Producer:
    return Producer(
        producer_id=model_id or "lawvm.propose-claims.tool",
        producer_kind="llm" if model_id else "script",
        public_key=None,
        metadata={"environment": "lawvm-propose-claims"},
    )


def _make_backend(backend_name: str) -> ClaimProposalBackend:
    if backend_name == "mock":
        return MockProposalBackend()
    if backend_name == "qwen":
        from lawvm.finland.llm_backends.qwen_local import QwenLocalBackend
        return QwenLocalBackend()
    raise ValueError(f"Unknown backend: {backend_name!r}. Choose 'mock' or 'qwen'.")


# ---------------------------------------------------------------------------
# Source fetch
# ---------------------------------------------------------------------------


def _fetch_source_for_frontier(
    frontier_row: object,
    jurisdiction: str = "fi",
) -> Optional[Tuple[bytes, str]]:
    statute_id = getattr(frontier_row, "statute_id", "")
    provision_ref = getattr(frontier_row, "provision_ref", None)
    scope = ClaimScope(
        statute_id=statute_id,
        provision_ref=provision_ref,
        valid_at_start=None,
        valid_at_end=None,
    )
    provider = get_source_provider(jurisdiction)
    fetched = provider.fetch(scope)
    if fetched is None:
        return None
    return fetched.bytes_, fetched.sha256_hex


# ---------------------------------------------------------------------------
# Dedup helpers (graph-native)
# ---------------------------------------------------------------------------


def _load_all_objects(store: GraphStore):
    """Yield all assertions and attestations from the objects dir."""
    objects_dir = store._objects_dir()
    if not objects_dir.exists():
        return
    for f in objects_dir.glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        yield d


def _accepted_target_exists(
    store: GraphStore,
    claim_kind: str,
    target_json: str,
) -> bool:
    """Return True if an assertion of claim_kind + target has a reviewed(accepted=True) attestation."""
    assertions_by_id: dict[str, dict] = {}
    attestations_for: dict[str, list[dict]] = {}

    for d in _load_all_objects(store):
        if "assertion_id" in d and "kind" in d:
            if d.get("kind") == claim_kind:
                assertions_by_id[d["assertion_id"]] = d
        elif "attestation_id" in d and "attestation_kind" in d:
            subj_id = d.get("subject", {}).get("artifact_id", "")
            if subj_id:
                attestations_for.setdefault(subj_id, []).append(d)

    for aid, ad in assertions_by_id.items():
        actual_target = json.dumps(dict(ad.get("target", {})), sort_keys=True)
        if actual_target != target_json:
            continue
        for atd in attestations_for.get(aid, []):
            if (
                atd.get("attestation_kind") == "reviewed"
                and atd.get("payload", {}).get("accepted") is True
            ):
                return True
    return False


def _proposed_triple_exists(
    store: GraphStore,
    claim_kind: str,
    target_json: str,
    value_json: str,
) -> Optional[str]:
    """Return assertion_id if a proposed (not reviewed) triple exists."""
    assertions_by_id: dict[str, dict] = {}
    reviewed_ids: set[str] = set()

    for d in _load_all_objects(store):
        if "assertion_id" in d and "kind" in d:
            if d.get("kind") == claim_kind:
                assertions_by_id[d["assertion_id"]] = d
        elif "attestation_id" in d and "attestation_kind" in d:
            if d.get("attestation_kind") == "reviewed":
                subj_id = d.get("subject", {}).get("artifact_id", "")
                if subj_id:
                    reviewed_ids.add(subj_id)

    for aid, ad in assertions_by_id.items():
        if aid in reviewed_ids:
            continue
        actual_target = json.dumps(dict(ad.get("target", {})), sort_keys=True)
        actual_value = json.dumps(dict(ad.get("value", {})), sort_keys=True)
        if actual_target == target_json and actual_value == value_json:
            return aid
    return None


# ---------------------------------------------------------------------------
# Build ProvenanceAssertion from ProposedClaim + frontier row
# ---------------------------------------------------------------------------


def _build_assertion_from_proposed(
    proposed: ProposedClaim,
    frontier_row: object,
    quoted_source: QuotedSource,
    producer: Producer,
    supersedes: tuple[str, ...] = (),
) -> ProvenanceAssertion:
    statute_id = getattr(frontier_row, "statute_id", "unknown/0000")
    provision_ref = getattr(frontier_row, "provision_ref", None)

    target_dict = dict(proposed.target) if proposed.target else {}
    value_dict = dict(proposed.value) if proposed.value else {}

    source_ref = SourceRef(
        artifact_digest=proposed.cited_source_hash or "unknown",
        structural_locator=provision_ref or statute_id,
        bounded_quote_hash=proposed.cited_source_hash or "unknown",
        normalization_policy_id="fi.v1.propose-claims",
        byte_range=(
            (proposed.cited_source_span[0], proposed.cited_source_span[1])
            if proposed.cited_source_span and len(proposed.cited_source_span) == 2
            else (0, 0)
        ),
    )

    temp = ProvenanceAssertion(
        assertion_id="__placeholder__",
        schema_version="v1",
        jurisdiction="fi",
        kind=proposed.claim_kind,
        layer="extraction",
        scope={"statute_id": statute_id, "provision_ref": provision_ref or ""},
        target=target_dict,
        value=value_dict,
        source_refs=(source_ref,),
        dependency_refs=(),
        valid_at=Interval(start=date.today()),
        supersedes=supersedes,
        disputes=(),
        rationale=proposed.rationale or "",
    )
    canonical = assertion_canonical_payload(temp)
    assertion_id = _sha256(canonical)

    return ProvenanceAssertion(
        assertion_id=assertion_id,
        schema_version="v1",
        jurisdiction="fi",
        kind=proposed.claim_kind,
        layer="extraction",
        scope={"statute_id": statute_id, "provision_ref": provision_ref or ""},
        target=target_dict,
        value=value_dict,
        source_refs=(source_ref,),
        dependency_refs=(),
        valid_at=Interval(start=date.today()),
        supersedes=supersedes,
        disputes=(),
        rationale=proposed.rationale or "",
    )


# ---------------------------------------------------------------------------
# Validator runner
# ---------------------------------------------------------------------------


def _run_validators(
    proposed: ProposedClaim,
    assertion: ProvenanceAssertion,
    source_bytes: bytes,
) -> Tuple[bool, bool, bool, str]:
    """Run schema + span + entailment.

    Returns (schema_ok, span_ok, entailment_ok, reason).
    """
    if proposed.parse_error is not None:
        return False, False, False, f"schema: {proposed.parse_error}"

    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
    from lawvm.core.manual_claims.primitive import ManualCompilationClaim

    spec = get_claim_kind_spec(proposed.claim_kind)
    if spec is None:
        return True, True, True, "no validator for kind"

    if spec.span_validator:
        from lawvm.core.manual_claims.primitive import (
            ClaimLayer,
            ClaimScope,
            _ProfileTagDeprecated as ProfileTag,
            SourceLocator,
            SourceWitnessType,
        )
        from lawvm.core.manual_claims.hashing import compute_claim_id

        scope_obj = ClaimScope(
            statute_id=str(assertion.scope.get("statute_id", "")),
            provision_ref=str(assertion.scope.get("provision_ref", "")) or None,
            valid_at_start=assertion.valid_at.start,
            valid_at_end=assertion.valid_at.end,
        )
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
            producer=_prim_module.Producer(
                producer_kind="llm",
                handle=None,
                model_id=None,
                timestamp=datetime.now(tz=timezone.utc),
                environment="propose-claims",
            ),
            cited_source_locator=SourceLocator(
                artifact_kind="finlex_akn",
                statute_id=str(assertion.scope.get("statute_id", "")),
                he_id=None,
                version_id=None,
            ),
            cited_source_span=assertion.source_refs[0].byte_range if assertion.source_refs else (0, 0),
            cited_source_hash=assertion.source_refs[0].artifact_digest if assertion.source_refs else "unknown",
            dependency_fingerprint=(),
            valid_at=(assertion.valid_at.start, assertion.valid_at.end),
            supersedes=(),
            supersession_delta_reason=None,
            disputes=(),
            requested_profiles=(ProfileTag.NON_STRICT_WITH_CLAIMS,),
            rationale=assertion.rationale,
        )
        claim_id = compute_claim_id(partial)
        compat_claim = ManualCompilationClaim(
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

        span_result = spec.span_validator(compat_claim, source_bytes)
        if not span_result.passed:
            return True, False, False, f"span: {span_result.reason}"

        if spec.entailment_validator:
            ent_result = spec.entailment_validator(compat_claim, source_bytes)
            if not ent_result.passed:
                return True, True, False, f"entailment: {ent_result.reason}"

    return True, True, True, "ok"


import lawvm.core.manual_claims.primitive as _prim_module


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------


def _normalize_fi_statute_resolution(
    proposed: ProposedClaim,
    *,
    verbose: bool = False,
) -> ProposedClaim:
    from lawvm.finland.claim_kinds.inline_statute_resolution import (
        _canonicalize_finnish_statute_id,
    )

    value_dict = dict(proposed.value)
    raw_id = value_dict.get("resolved_statute_id", "")
    if not isinstance(raw_id, str):
        return proposed
    canonical = _canonicalize_finnish_statute_id(raw_id)

    if canonical is None or canonical == raw_id:
        return proposed

    if verbose:
        print(f"  canonicalized {raw_id} → {canonical}")

    new_value = tuple(
        (k, canonical if k == "resolved_statute_id" else v)
        for k, v in proposed.value
    )
    return ProposedClaim(
        claim_kind=proposed.claim_kind,
        target=proposed.target,
        value=new_value,
        cited_source_span=proposed.cited_source_span,
        cited_source_hash=proposed.cited_source_hash,
        rationale=proposed.rationale,
        producer_model_id=proposed.producer_model_id,
        raw_response=proposed.raw_response,
        parse_error=proposed.parse_error,
    )


# ---------------------------------------------------------------------------
# Write live snapshot helper
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Core: process one frontier row
# ---------------------------------------------------------------------------


def _process_one_frontier(
    frontier_row: object,
    store: GraphStore,
    backend: ClaimProposalBackend,
    claim_kind: str,
    source_bytes: bytes,
    cited_span_hash: str,
    statute_id: str,
    he_id: Optional[str],
    producer: Producer,
    *,
    verbose: bool = False,
) -> Optional[str]:
    schema = ClaimSchema(
        claim_kind=claim_kind,
        required_value_fields=("resolved_statute_id", "citation_form"),
        json_schema_dict={
            "name": "inline_statute_resolution",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "resolved_statute_id": {"type": "string"},
                    "citation_form": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["resolved_statute_id", "citation_form"],
                "additionalProperties": False,
            },
        },
        natural_language_description=(
            "JSON with fields: resolved_statute_id (Finnish statute ID NNNN/YYYY), "
            "citation_form (exact citation phrase from source), rationale (optional)."
        ),
    )

    quoted_source = QuotedSource(
        artifact_kind="finlex_akn",
        statute_id=statute_id,
        he_id=he_id,
        cited_span_bytes=source_bytes,
        cited_span_hash=cited_span_hash,
    )

    proposed = backend.propose(frontier_row, schema, quoted_source)

    if claim_kind == "fi.v1.INLINE_STATUTE_RESOLUTION":
        proposed = _normalize_fi_statute_resolution(proposed, verbose=verbose)

    assertion = _build_assertion_from_proposed(proposed, frontier_row, quoted_source, producer)

    target_json = json.dumps(dict(assertion.target), sort_keys=True)
    value_json = json.dumps(dict(assertion.value), sort_keys=True)

    if _accepted_target_exists(store, claim_kind, target_json):
        if verbose:
            print("  skip: gap already closed by accepted assertion")
        return None

    existing_id = _proposed_triple_exists(store, claim_kind, target_json, value_json)
    if existing_id is not None:
        if verbose:
            print(f"  idempotent: existing proposal {existing_id[:16]}...")
        return existing_id

    schema_ok, span_ok, entailment_ok, reason = _run_validators(proposed, assertion, source_bytes)

    assertion_id = submit_assertion(store, assertion, producer)

    if not schema_ok:
        attest(store, assertion_id, "schema_validated", {"success": False, "reason": reason}, producer)
        if verbose:
            print(f"  rejected (stored for audit): {reason}")
        return None

    attest(store, assertion_id, "schema_validated", {"success": True}, producer)

    if not span_ok:
        attest(store, assertion_id, "span_verified", {"passed": False, "reason": reason}, producer)
        if verbose:
            print(f"  rejected (stored for audit): {reason}")
        return None

    attest(store, assertion_id, "span_verified", {"passed": True}, producer)

    if not entailment_ok:
        attest(store, assertion_id, "entailment_verified", {"passed": False, "reason": reason}, producer)
        if verbose:
            print(f"  rejected (stored for audit): {reason}")
        return None

    attest(store, assertion_id, "entailment_verified", {"passed": True}, producer)

    if verbose:
        print(f"  proposed: {assertion_id[:32]}... (entailment_verified)")

    return assertion_id


# ---------------------------------------------------------------------------
# Frontier row producers: scan parquet projections for NULL slots
# ---------------------------------------------------------------------------

_FRONTIER_SOURCE_INLINE_CITATIONS = "inline_citations"
_FRONTIER_SOURCE_FI_REFS = "fi_refs"
_FRONTIER_SOURCE_DETERMINISTIC_REFS = "deterministic_refs"


def _scan_frontier_inline_citations(
    data_dir: str,
    claim_kind: str,
) -> List[ExtractionFrontierRow]:
    """Scan fi_inline_citations.parquet for rows where canonical_id is NULL.

    These are inline-body citations the deterministic extractor recognized
    structurally but could not resolve to a canonical statute/document ID.
    The LLM-aided INLINE_STATUTE_RESOLUTION claim kind fills these gaps.

    Each returned ExtractionFrontierRow carries:
      - statute_id = source_doc_id (the source document containing the citation)
      - provision_ref = source_provision_ref (where in source; often empty)
      - citation_text = raw_text (the literal citation phrase for the LLM prompt)
      - slot = 'canonical_id'
    """
    import importlib.util

    frontier_rows: List[ExtractionFrontierRow] = []
    base = Path(data_dir)
    parquet_path = base / "fi_inline_citations.parquet"
    jsonl_path = base / "fi_inline_citations.jsonl"

    def _process_row(row_dict: Dict) -> Optional[ExtractionFrontierRow]:
        if row_dict.get("canonical_id"):
            return None  # already resolved by deterministic extractor

        source_doc_id = row_dict.get("source_doc_id", "")
        if not source_doc_id:
            return None

        provision_ref = row_dict.get("source_provision_ref") or None
        raw_text = row_dict.get("raw_text", "") or ""

        frontier_id_src = f"{claim_kind}:{source_doc_id}:{provision_ref or ''}:{raw_text}"
        frontier_id = hashlib.sha256(frontier_id_src.encode()).hexdigest()
        return ExtractionFrontierRow(
            frontier_id=frontier_id,
            claim_kind=claim_kind,
            statute_id=source_doc_id,
            provision_ref=provision_ref,
            slot="canonical_id",
            severity="medium",
            detected_at=datetime.now(tz=timezone.utc),
            pipeline_run_id="scan_inline_citations",
            citation_text=raw_text if raw_text else None,
        )

    has_pyarrow = importlib.util.find_spec("pyarrow") is not None

    if has_pyarrow and parquet_path.exists():
        import pyarrow.parquet as pq
        table = pq.read_table(str(parquet_path))
        for batch in table.to_batches():
            for i in range(batch.num_rows):
                row_dict = {col: batch.column(col)[i].as_py() for col in batch.schema.names}
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    elif jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row_dict = json.loads(line)
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    else:
        print(
            f"  warning: fi_inline_citations not found at {parquet_path} or {jsonl_path}. "
            "Run export-fi-inline-citations first to generate the projection.",
            file=sys.stderr,
        )

    return frontier_rows


def _scan_frontier_fi_refs(
    data_dir: str,
    claim_kind: str,
) -> List[ExtractionFrontierRow]:
    """Scan fi_refs.parquet for NULL target_statute_id rows.

    NOTE: the deterministic extractor drops unresolvable citations entirely
    rather than emitting NULL-target rows, so this source typically returns 0
    rows against a real corpus.  Retained for legacy compatibility and for
    synthetic fixture testing.  Falls back to JSONL if parquet not available.
    """
    import importlib.util

    frontier_rows: List[ExtractionFrontierRow] = []
    base = Path(data_dir)
    parquet_path = base / "fi_refs.parquet"
    jsonl_path = base / "fi_refs.jsonl"

    def _process_row(row_dict: Dict) -> Optional[ExtractionFrontierRow]:
        target_id = row_dict.get("target_statute_id") or row_dict.get("target_statute_id_str")
        if target_id:
            return None

        statute_id = row_dict.get("source_statute_id", "")
        if not statute_id:
            return None

        provision_ref = row_dict.get("source_provision_ref_str") or None
        span_offset = row_dict.get("source_span_byte_offset", 0) or 0
        span_len = row_dict.get("source_span_len", 0) or 0

        frontier_id_src = f"{claim_kind}:{statute_id}:{provision_ref or ''}:{span_offset}:{span_len}"
        frontier_id = hashlib.sha256(frontier_id_src.encode()).hexdigest()

        return ExtractionFrontierRow(
            frontier_id=frontier_id,
            claim_kind=claim_kind,
            statute_id=statute_id,
            provision_ref=provision_ref,
            slot="target_statute_id",
            severity="medium",
            detected_at=datetime.now(tz=timezone.utc),
            pipeline_run_id="scan_fi_refs",
        )

    has_pyarrow = importlib.util.find_spec("pyarrow") is not None

    if has_pyarrow and parquet_path.exists():
        import pyarrow.parquet as pq
        table = pq.read_table(str(parquet_path))
        for batch in table.to_batches():
            for i in range(batch.num_rows):
                row_dict = {col: batch.column(col)[i].as_py() for col in batch.schema.names}
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    elif jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row_dict = json.loads(line)
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    else:
        print(
            f"  warning: fi_refs not found at {parquet_path} or {jsonl_path}. "
            "Run export-fi-refs first to generate the projection.",
            file=sys.stderr,
        )

    return frontier_rows


def _scan_frontier_deterministic_refs(
    data_dir: str,
    claim_kind: str,
) -> List[ExtractionFrontierRow]:
    """Scan fi_refs__deterministic_only.parquet for NULL target_statute_id rows.

    The deterministic-only refs export retains rows the extractor recognized
    but could not resolve, keyed by source span.
    """
    import importlib.util

    frontier_rows: List[ExtractionFrontierRow] = []
    base = Path(data_dir)

    parquet_path = base / "fi_refs__deterministic_only.parquet"
    jsonl_path = base / "fi_refs__deterministic_only.jsonl"

    def _process_row(row_dict: Dict) -> Optional[ExtractionFrontierRow]:
        target_id = row_dict.get("target_statute_id") or row_dict.get("target_statute_id_str")
        if target_id:
            return None
        statute_id = row_dict.get("source_statute_id", "")
        if not statute_id:
            return None
        provision_ref = row_dict.get("source_provision_ref_str") or None
        span_offset = row_dict.get("source_span_byte_offset", 0) or 0
        span_len = row_dict.get("source_span_len", 0) or 0
        slot = "target_statute_id"
        frontier_id_src = f"{claim_kind}:{statute_id}:{provision_ref or ''}:{span_offset}:{span_len}"
        frontier_id = hashlib.sha256(frontier_id_src.encode()).hexdigest()
        return ExtractionFrontierRow(
            frontier_id=frontier_id,
            claim_kind=claim_kind,
            statute_id=statute_id,
            provision_ref=provision_ref,
            slot=slot,
            severity="medium",
            detected_at=datetime.now(tz=timezone.utc),
            pipeline_run_id="scan",
        )

    has_pyarrow = importlib.util.find_spec("pyarrow") is not None

    if has_pyarrow and parquet_path.exists():
        import pyarrow.parquet as pq
        table = pq.read_table(str(parquet_path))
        for batch in table.to_batches():
            for i in range(batch.num_rows):
                row_dict = {col: batch.column(col)[i].as_py() for col in batch.schema.names}
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    elif jsonl_path.exists():
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row_dict = json.loads(line)
                fr = _process_row(row_dict)
                if fr is not None:
                    frontier_rows.append(fr)
    else:
        print(
            f"  warning: no deterministic refs output found at {parquet_path} or {jsonl_path}.",
            file=sys.stderr,
        )

    return frontier_rows


def _scan_frontier_from_parquet(
    data_dir: str,
    claim_kind: str,
    frontier_source: str = _FRONTIER_SOURCE_INLINE_CITATIONS,
) -> List[ExtractionFrontierRow]:
    """Dispatch to the appropriate frontier scanner by source name.

    frontier_source:
      'inline_citations' (default) — scan fi_inline_citations.parquet for NULL
          canonical_id rows.  This is the correct source for
          fi.v1.INLINE_STATUTE_RESOLUTION: the inline-citations projection
          contains HE-prose / statute-body citations the deterministic extractor
          recognized structurally but could not resolve to canonical IDs.
      'fi_refs' — scan fi_refs.parquet for NULL target_statute_id rows.
          The deterministic extractor drops unresolvable citations entirely
          rather than emitting NULL rows, so this returns 0 rows against a
          real corpus.  Retained for legacy and synthetic-fixture use.
      'deterministic_refs' — scan fi_refs__deterministic_only.parquet, the
          deterministic-only export that retains unresolved span-keyed rows.
    """
    if frontier_source == _FRONTIER_SOURCE_FI_REFS:
        return _scan_frontier_fi_refs(data_dir, claim_kind)
    if frontier_source == _FRONTIER_SOURCE_DETERMINISTIC_REFS:
        return _scan_frontier_deterministic_refs(data_dir, claim_kind)
    return _scan_frontier_inline_citations(data_dir, claim_kind)


# ---------------------------------------------------------------------------
# Gap discovery: regex scan of HE XML for plain-text citations
# ---------------------------------------------------------------------------



def _discover_gaps_from_he(
    he_id: str,
    data_dir: str,
    claim_kind: str,
) -> List[GapDiscoveryRow]:
    gap_rows: List[GapDiscoveryRow] = []
    he_xml: Optional[bytes] = None
    try:
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        he_xml = store.read_oracle(he_id)
    except Exception:
        pass

    if he_xml is None:
        print(f"  warning: HE {he_id!r} not found in corpus store", file=sys.stderr)
        return gap_rows

    text = he_xml.decode("utf-8", errors="replace")
    found_ids: set = set()

    for m in _PLAIN_STATUTE_RE.finditer(text):
        stat_num = m.group(1)
        stat_year = m.group(2)
        statute_id = f"{stat_num}/{stat_year}"
        if statute_id in found_ids:
            continue
        found_ids.add(statute_id)
        gap_id_src = f"{claim_kind}:{he_id}:{statute_id}"
        gap_id = hashlib.sha256(gap_id_src.encode()).hexdigest()
        gap_rows.append(GapDiscoveryRow(
            gap_id=gap_id,
            claim_kind=claim_kind,
            statute_id=he_id,
            expected_target_key=f"resolved_statute_id={statute_id}",
            severity="low",
            detected_at=datetime.now(tz=timezone.utc),
            pipeline_run_id="gap-discovery",
        ))

    return gap_rows


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def cmd_propose_from_frontier(args: object) -> int:
    data_dir: str = getattr(args, "data_dir", "data/fi/v1")
    graph_store_root: Optional[str] = (
        getattr(args, "graph_store_root", None)
        or getattr(args, "claim_store_root", None)
    )
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION") or "fi.v1.INLINE_STATUTE_RESOLUTION"
    limit: int = getattr(args, "limit", 100) or 100
    no_cap: bool = getattr(args, "max_claims_no_cap", False)
    backend_name: str = getattr(args, "backend", "mock") or "mock"
    frontier_source: str = getattr(args, "frontier_source", _FRONTIER_SOURCE_INLINE_CITATIONS) or _FRONTIER_SOURCE_INLINE_CITATIONS

    backend = _make_backend(backend_name)
    store = _get_store(graph_store_root)
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    frontier_rows = _scan_frontier_from_parquet(data_dir, kind, frontier_source=frontier_source)
    if not frontier_rows:
        print(
            f"no frontier rows found in {frontier_source!r} "
            "(run rebuild-indexes or export-fi-inline-citations first)"
        )
        return 0

    effective_limit = len(frontier_rows) if no_cap else min(limit, len(frontier_rows))
    if len(frontier_rows) > limit and not no_cap:
        print(f"  info: {len(frontier_rows)} frontier rows, applying --limit {limit}")

    proposed_count = 0
    skipped_count = 0

    for fr in frontier_rows[:effective_limit]:
        fetched = _fetch_source_for_frontier(fr)
        if fetched is None:
            print(f"  frontier_skipped_no_source: {fr.statute_id!r}", file=sys.stderr)
            skipped_count += 1
            continue
        source_bytes, cited_span_hash = fetched
        producer = _cli_producer(model_id=getattr(backend, "model_name", None))

        result = _process_one_frontier(
            frontier_row=fr,
            store=store,
            backend=backend,
            claim_kind=kind,
            source_bytes=source_bytes,
            cited_span_hash=cited_span_hash,
            statute_id=fr.statute_id,
            he_id=None,
            producer=producer,
            verbose=True,
        )
        if result is not None:
            proposed_count += 1
        else:
            skipped_count += 1

    _write_live_snapshot(store)
    print(f"\npropose-claims: {proposed_count} proposed, {skipped_count} skipped/rejected")
    return 0


def cmd_propose_gap_discovery(args: object) -> int:
    he_arg = getattr(args, "he", None)
    he_id = he_arg if isinstance(he_arg, str) else ""
    if not he_id:
        print("error: --gap-discovery requires --he HE_ID", file=sys.stderr)
        return 1

    data_dir: str = getattr(args, "data_dir", "data/fi/v1")
    graph_store_root: Optional[str] = (
        getattr(args, "graph_store_root", None)
        or getattr(args, "claim_store_root", None)
    )
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION") or "fi.v1.INLINE_STATUTE_RESOLUTION"
    backend_name: str = getattr(args, "backend", "mock") or "mock"

    backend = _make_backend(backend_name)
    store = _get_store(graph_store_root)
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    gap_rows = _discover_gaps_from_he(he_id, data_dir, kind)
    if not gap_rows:
        print(f"no gaps discovered in {he_id!r}")
        return 0

    print(f"discovered {len(gap_rows)} gap(s) in {he_id!r}")
    proposed_count = 0
    skipped_count = 0

    for gr in gap_rows:
        fetched = _fetch_source_for_frontier(gr)
        if fetched is None:
            print(f"  frontier_skipped_no_source: {gr.statute_id!r}", file=sys.stderr)
            skipped_count += 1
            continue
        source_bytes, cited_span_hash = fetched
        producer = _cli_producer(model_id=getattr(backend, "model_name", None))

        result = _process_one_frontier(
            frontier_row=gr,
            store=store,
            backend=backend,
            claim_kind=kind,
            source_bytes=source_bytes,
            cited_span_hash=cited_span_hash,
            statute_id=gr.statute_id,
            he_id=he_id,
            producer=producer,
            verbose=True,
        )
        if result is not None:
            proposed_count += 1

    _write_live_snapshot(store)
    print(f"\ngap-discovery: {proposed_count} proposed, {skipped_count} skipped")
    return 0


def cmd_propose_specific(args: object) -> int:
    he_arg = getattr(args, "he", None)
    he_id = he_arg if isinstance(he_arg, str) else ""
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION")
    graph_store_root: Optional[str] = (
        getattr(args, "graph_store_root", None)
        or getattr(args, "claim_store_root", None)
    )
    backend_name: str = getattr(args, "backend", "mock") or "mock"

    if not he_id:
        print("error: --he HE_ID is required for specific gap rescue", file=sys.stderr)
        return 1

    backend = _make_backend(backend_name)
    store = _get_store(graph_store_root)
    store._objects_dir().mkdir(parents=True, exist_ok=True)

    gap_id_src = f"{kind}:{he_id}:specific"
    gap_id = hashlib.sha256(gap_id_src.encode()).hexdigest()
    frontier_row = ExtractionFrontierRow(
        frontier_id=gap_id,
        claim_kind=kind,
        statute_id=he_id,
        provision_ref=None,
        slot="target_statute_id",
        severity="high",
        detected_at=datetime.now(tz=timezone.utc),
        pipeline_run_id="specific",
    )

    fetched = _fetch_source_for_frontier(frontier_row)
    if fetched is None:
        print(f"  frontier_skipped_no_source: {he_id!r}", file=sys.stderr)
        return 1
    source_bytes, cited_span_hash = fetched
    producer = _cli_producer(model_id=getattr(backend, "model_name", None))

    result = _process_one_frontier(
        frontier_row=frontier_row,
        store=store,
        backend=backend,
        claim_kind=kind,
        source_bytes=source_bytes,
        cited_span_hash=cited_span_hash,
        statute_id=he_id,
        he_id=he_id,
        producer=producer,
        verbose=True,
    )
    _write_live_snapshot(store)
    return 0 if result is not None else 1


def main(args: object) -> None:
    importlib.import_module("lawvm.finland.claim_kinds")
    register_fi_source_provider()

    from_frontier: bool = getattr(args, "from_frontier", False)
    gap_discovery: bool = getattr(args, "gap_discovery", False)
    he_id: Optional[str] = getattr(args, "he", None)

    if from_frontier:
        rc = cmd_propose_from_frontier(args)
    elif gap_discovery:
        rc = cmd_propose_gap_discovery(args)
    elif he_id:
        rc = cmd_propose_specific(args)
    else:
        print("error: one of --from-frontier, --gap-discovery, or --he HE_ID required", file=sys.stderr)
        rc = 1

    if rc:
        sys.exit(rc)
