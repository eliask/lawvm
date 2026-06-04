"""lawvm propose-claims — LLM-aided claim proposal pipeline (Slice 4).

Subcommands:
  --from-frontier [--kind ...] [--limit N] [--backend mock|qwen]
  --gap-discovery --he HE_ID [--kind ...] [--backend ...]
  --he HE_ID --kind fi.v1.INLINE_STATUTE_RESOLUTION [--backend ...]

§4.2 + §9 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2.

Quota rules (adversary #2 — claim flooding):
  - Default limit: 100 proposals per invocation. No-cap requires --max-claims-no-cap.
  - Skip if (claim_kind, target) already has an accepted claim (gap is closed).
  - One active proposed claim per (claim_kind, target, value) triple — duplicate
    proposals update the existing claim's producer field (not a new claim_id).

Validation pipeline (per §9 step 6):
  1. schema validation (shape correct; parse_error is None)
  2. span_existence_validator
  3. per-ClaimKind entailment_validator
  If all pass: write to proposed/ with validator_status=entailment_verified.
  If any fail: write rejection record with reason.

Source bytes (Piece 3):
  Each frontier row fetches real source bytes via the SourceBytesProvider
  registry (lawvm.core.manual_claims.source_provider). The 'fi' provider is
  registered at CLI startup via register_fi_source_provider(). Rows whose
  provider returns None are skipped and logged as frontier_skipped_no_source.

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple

from lawvm.core.manual_claims.hashing import compute_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ExtractionFrontierRow,
    GapDiscoveryRow,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)
from lawvm.core.manual_claims.proposal_backend import (
    ClaimSchema,
    MockProposalBackend,
    ProposedClaim,
    QuotedSource,
)
from lawvm.core.manual_claims.source_provider import (
    FetchedSource,
    SourceBytesProvider,
    get_source_provider,
    register_source_provider,
)
from lawvm.core.manual_claims.state import project_state
from lawvm.core.manual_claims.storage import ClaimStore

_DEFAULT_DATA_DIR = "data/fi/v1"
_MANUAL_CLAIMS_SUBDIR = "manual_claims"


# ---------------------------------------------------------------------------
# Source provider registration helper
# ---------------------------------------------------------------------------


def register_fi_source_provider(corpus_root=None) -> None:
    """Register the Finland FinlexSectionSourceProvider for jurisdiction 'fi'.

    Called at CLI startup (propose-claims and validate-claims handlers in cli.py).
    corpus_root: optional Path override for the finlex.farchive location.
    """
    from lawvm.finland.source_providers.finlex_section import FinlexSectionSourceProvider
    from pathlib import Path
    provider = FinlexSectionSourceProvider(
        corpus_root=Path(corpus_root) if corpus_root is not None else None
    )
    register_source_provider("fi", provider)

# Inline statute citation patterns reused from inline_statute_resolution.py
# (module-scope compile per AGENTS.md §1.11)
_PLAIN_STATUTE_RE = re.compile(r"\b(\d{1,4})/(\d{4})\b")
"""Plain NNNN/YYYY pattern for gap discovery — finds citations the <ref> extractor missed."""


# ---------------------------------------------------------------------------
# Backend factory
# ---------------------------------------------------------------------------


def _make_backend(backend_name: str) -> object:
    if backend_name == "mock":
        return MockProposalBackend()
    if backend_name == "qwen":
        from lawvm.finland.llm_backends.qwen_local import QwenLocalBackend
        return QwenLocalBackend()
    raise ValueError(f"Unknown backend: {backend_name!r}. Choose 'mock' or 'qwen'.")


def _get_store(data_dir: str, *, claim_store_root: Optional[str] = None) -> ClaimStore:
    if claim_store_root is not None:
        return ClaimStore(Path(claim_store_root))
    return ClaimStore(Path(data_dir) / _MANUAL_CLAIMS_SUBDIR)


def _fetch_source_for_frontier(
    frontier_row: object,
    jurisdiction: str = "fi",
) -> Optional[Tuple[bytes, str]]:
    """Fetch (source_bytes, cited_span_hash) for a frontier row via registered provider.

    Returns None if no provider is registered or the provider returns None for
    this row (e.g. statute not in corpus, section not found). Callers should
    log a frontier_skipped_no_source event and continue to the next row.
    """
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


def _cli_producer(model_id: Optional[str] = None) -> Producer:
    return Producer(
        producer_kind="llm" if model_id else "tool",
        handle=None,
        model_id=model_id,
        timestamp=datetime.now(tz=timezone.utc),
        environment="lawvm-propose-claims",
    )


# ---------------------------------------------------------------------------
# Frontier row producer: scan deterministic parquet for NULL target_statute_id
# ---------------------------------------------------------------------------


def _scan_frontier_from_parquet(
    data_dir: str,
    claim_kind: str,
) -> List[ExtractionFrontierRow]:
    """Scan fi_refs__deterministic_only.parquet for NULL target_statute_id_str rows.

    Returns ExtractionFrontierRow records for rows that need claim proposals.
    Falls back to JSONL if parquet not available.
    """
    import importlib.util

    frontier_rows: List[ExtractionFrontierRow] = []
    base = Path(data_dir)

    # Try parquet first
    parquet_path = base / "fi_refs__deterministic_only.parquet"
    jsonl_path = base / "fi_refs__deterministic_only.jsonl"

    def _process_row(row_dict: Dict) -> Optional[ExtractionFrontierRow]:
        target_id = row_dict.get("target_statute_id") or row_dict.get("target_statute_id_str")
        if target_id:
            return None  # gap is filled by deterministic extractor

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
            f"  warning: no deterministic refs output found at {parquet_path} or {jsonl_path}. "
            "Run export-fi-refs first to generate the deterministic projection.",
            file=sys.stderr,
        )

    return frontier_rows


# ---------------------------------------------------------------------------
# Gap discovery: regex scan of HE XML for plain-text citations
# ---------------------------------------------------------------------------


def _discover_gaps_from_he(
    he_id: str,
    data_dir: str,
    claim_kind: str,
) -> List[GapDiscoveryRow]:
    """Scan an HE's body for plain-text statute citations the deterministic extractor missed.

    Returns GapDiscoveryRow records for each citation pattern found in prose
    that has no corresponding accepted claim of claim_kind targeting that statute.
    """
    gap_rows: List[GapDiscoveryRow] = []

    # Try to load the HE XML from corpus store
    he_xml: Optional[bytes] = None
    try:
        from lawvm.finland.corpus import get_corpus_store
        store = get_corpus_store()
        he_xml = store.read_oracle(he_id)
    except Exception:
        pass

    if he_xml is None:
        print(f"  warning: HE {he_id!r} not found in corpus store — cannot run gap discovery", file=sys.stderr)
        return gap_rows

    # Decode and scan for plain statute citations
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
# Core proposal logic
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ClaimKey:
    claim_kind: str
    target_json: str
    value_json: str


def _make_claim_key(claim: ManualCompilationClaim) -> _ClaimKey:
    return _ClaimKey(
        claim_kind=claim.claim_kind,
        target_json=json.dumps(dict(claim.target), sort_keys=True),
        value_json=json.dumps(dict(claim.value), sort_keys=True),
    )


def _find_existing_claim_for_target(
    store: ClaimStore,
    claim_kind: str,
    target_json: str,
) -> Optional[str]:
    """Return claim_id if any accepted claim for this (kind, target) exists.

    Returns None if the gap is still open.
    """
    for claim_id in store.list_all_claim_ids():
        claim = store.read_claim(claim_id)
        if claim.claim_kind != claim_kind:
            continue
        state = store.read_state(claim_id)
        if state is None:
            continue
        if state.status == ClaimStatus.ACCEPTED:
            existing_target = json.dumps(dict(claim.target), sort_keys=True)
            if existing_target == target_json:
                return claim_id
    return None


def _find_existing_proposed_claim(
    store: ClaimStore,
    key: _ClaimKey,
) -> Optional[str]:
    """Return claim_id if an active proposed claim with same (kind, target, value) exists."""
    for claim_id in store.list_all_claim_ids():
        claim = store.read_claim(claim_id)
        if claim.claim_kind != key.claim_kind:
            continue
        if json.dumps(dict(claim.target), sort_keys=True) != key.target_json:
            continue
        if json.dumps(dict(claim.value), sort_keys=True) != key.value_json:
            continue
        state = store.read_state(claim_id)
        if state is None:
            continue
        if state.status == ClaimStatus.PROPOSED:
            return claim_id
    return None


def _build_claim_from_proposed(
    proposed: ProposedClaim,
    frontier_row: object,
    quoted_source: QuotedSource,
    producer: Producer,
) -> ManualCompilationClaim:
    statute_id = getattr(frontier_row, "statute_id", "unknown/0000")
    provision_ref = getattr(frontier_row, "provision_ref", None)
    now = datetime.now(tz=timezone.utc)

    # Build a placeholder to compute the content-addressed ID
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind=proposed.claim_kind,
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref=provision_ref,
            valid_at_start=date.today(),
            valid_at_end=None,
        ),
        target=proposed.target,
        value=proposed.value,
        source_witness_type=SourceWitnessType.LLM_PROPOSAL,
        producer=producer,
        cited_source_locator=SourceLocator(
            artifact_kind=quoted_source.artifact_kind,
            statute_id=quoted_source.statute_id,
            he_id=quoted_source.he_id,
            version_id=None,
        ),
        cited_source_span=proposed.cited_source_span,
        cited_source_hash=proposed.cited_source_hash,
        dependency_fingerprint=(
            ("statute_id", statute_id),
            ("provision_ref", provision_ref or ""),
        ),
        valid_at=(date.today(), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.NON_STRICT_WITH_CLAIMS,),
        rationale=proposed.rationale,
    )
    claim_id = compute_claim_id(partial)

    return ManualCompilationClaim(
        claim_id=claim_id,
        schema_version="v1",
        jurisdiction="fi",
        claim_kind=proposed.claim_kind,
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref=provision_ref,
            valid_at_start=date.today(),
            valid_at_end=None,
        ),
        target=proposed.target,
        value=proposed.value,
        source_witness_type=SourceWitnessType.LLM_PROPOSAL,
        producer=producer,
        cited_source_locator=SourceLocator(
            artifact_kind=quoted_source.artifact_kind,
            statute_id=quoted_source.statute_id,
            he_id=quoted_source.he_id,
            version_id=None,
        ),
        cited_source_span=proposed.cited_source_span,
        cited_source_hash=proposed.cited_source_hash,
        dependency_fingerprint=(
            ("statute_id", statute_id),
            ("provision_ref", provision_ref or ""),
        ),
        valid_at=(date.today(), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.NON_STRICT_WITH_CLAIMS,),
        rationale=proposed.rationale,
    )


def _run_validators(
    proposed: ProposedClaim,
    claim: ManualCompilationClaim,
    source_bytes: bytes,
) -> Tuple[bool, ValidatorStatus, str]:
    """Run schema + span + entailment validators.

    Returns (all_passed, validator_status, reason).
    """
    # 1. Schema validation: parse_error must be None
    if proposed.parse_error is not None:
        return False, ValidatorStatus.UNVALIDATED, f"schema: {proposed.parse_error}"

    # 2. Span existence validator
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
    spec = get_claim_kind_spec(claim.claim_kind)
    if spec is None:
        return False, ValidatorStatus.UNVALIDATED, f"unknown claim kind: {claim.claim_kind!r}"

    if spec.span_validator:
        span_result = spec.span_validator(claim, source_bytes)
        if not span_result.passed:
            return False, ValidatorStatus.UNVALIDATED, f"span: {span_result.reason}"

    # 3. Entailment validator
    if spec.entailment_validator:
        ent_result = spec.entailment_validator(claim, source_bytes)
        if not ent_result.passed:
            return False, ValidatorStatus.SPAN_VERIFIED, f"entailment: {ent_result.reason}"

    return True, ValidatorStatus.ENTAILMENT_VERIFIED, "ok"


def _write_rejection(
    store: ClaimStore,
    proposed: ProposedClaim,
    frontier_row: object,
    reason: str,
    producer: Producer,
) -> None:
    """Write a rejection record to the event log (no persistent claim object)."""
    # For rejected proposals we log the event without a full ManualCompilationClaim
    # (the proposed claim may be malformed). We record it in a rejection sidecar.
    rejection_dir = store._base / "proposal_rejections"
    rejection_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(tz=timezone.utc)
    rejection_id = hashlib.sha256(
        f"{proposed.claim_kind}:{getattr(frontier_row, 'frontier_id', 'gap')}:{now.isoformat()}".encode()
    ).hexdigest()[:32]
    rec = {
        "rejection_id": rejection_id,
        "claim_kind": proposed.claim_kind,
        "frontier_id": getattr(frontier_row, "frontier_id", getattr(frontier_row, "gap_id", "")),
        "statute_id": getattr(frontier_row, "statute_id", ""),
        "reason": reason,
        "raw_response": proposed.raw_response[:500],
        "timestamp": now.isoformat(),
        "producer_model_id": proposed.producer_model_id,
    }
    (rejection_dir / f"{rejection_id}.json").write_text(
        json.dumps(rec, indent=2), encoding="utf-8"
    )


def _process_one_frontier(
    frontier_row: object,
    store: ClaimStore,
    backend: object,
    claim_kind: str,
    source_bytes: bytes,
    cited_span_hash: str,
    statute_id: str,
    he_id: Optional[str],
    producer: Producer,
    *,
    verbose: bool = False,
) -> Optional[str]:
    """Propose a claim for one frontier row. Returns claim_id if proposed, else None."""

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

    proposed: ProposedClaim = backend.propose(frontier_row, schema, quoted_source)  # type: ignore[attr-defined]

    claim = _build_claim_from_proposed(proposed, frontier_row, quoted_source, producer)

    # Check (claim_kind, target) for existing accepted claim
    target_json = json.dumps(dict(claim.target), sort_keys=True)
    existing_accepted = _find_existing_claim_for_target(store, claim_kind, target_json)
    if existing_accepted is not None:
        if verbose:
            print(f"  skip: gap already closed by accepted claim {existing_accepted[:16]}...")
        return None

    # Check (claim_kind, target, value) for duplicate proposal
    key = _make_claim_key(claim)
    existing_proposed = _find_existing_proposed_claim(store, key)
    if existing_proposed is not None:
        # Idempotent: update producer field by appending an event, keep same claim_id
        now = datetime.now(tz=timezone.utc)
        event = ClaimStateEvent(
            claim_id=existing_proposed,
            event_kind="re_proposed",
            timestamp=now,
            producer=producer,
            old_status="proposed",
            new_status=None,
            reason=f"duplicate proposal — producer updated to {producer.model_id or 'tool'}",
        )
        store.append_event(event)
        if verbose:
            print(f"  idempotent: existing proposal {existing_proposed[:16]}... updated")
        return existing_proposed

    # Run validators against source bytes
    all_passed, validator_status, reason = _run_validators(proposed, claim, source_bytes)

    if not all_passed:
        _write_rejection(store, proposed, frontier_row, reason, producer)
        if verbose:
            print(f"  rejected: {reason}")
        return None

    # Write claim to storage
    store.write_claim(claim)
    store.write_by_kind(claim)

    now = datetime.now(tz=timezone.utc)
    state = ClaimState(
        claim_id=claim.claim_id,
        status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=validator_status,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=now,
    )
    store.write_state(state)

    event = ClaimStateEvent(
        claim_id=claim.claim_id,
        event_kind="proposed",
        timestamp=now,
        producer=producer,
        old_status=None,
        new_status="proposed",
        reason=f"proposed by {producer.model_id or 'tool'} via propose-claims",
    )
    store.append_event(event)

    if validator_status == ValidatorStatus.ENTAILMENT_VERIFIED:
        ev2 = ClaimStateEvent(
            claim_id=claim.claim_id,
            event_kind="entailment_verified",
            timestamp=now,
            producer=producer,
            old_status="proposed",
            new_status=None,
            reason="entailment validator passed",
        )
        store.append_event(ev2)

    if verbose:
        print(f"  proposed: {claim.claim_id[:32]}... ({validator_status.value})")

    return claim.claim_id


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def cmd_propose_from_frontier(args: object) -> int:
    """--from-frontier handler."""
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    claim_store_root: Optional[str] = getattr(args, "claim_store_root", None)
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION") or "fi.v1.INLINE_STATUTE_RESOLUTION"
    limit: int = getattr(args, "limit", 100) or 100
    no_cap: bool = getattr(args, "max_claims_no_cap", False)
    backend_name: str = getattr(args, "backend", "mock") or "mock"

    backend = _make_backend(backend_name)
    store = _get_store(data_dir, claim_store_root=claim_store_root)
    store.ensure_dirs()

    frontier_rows = _scan_frontier_from_parquet(data_dir, kind)
    if not frontier_rows:
        print("no frontier rows found (run export-fi-refs first to generate deterministic projection)")
        return 0

    effective_limit = len(frontier_rows) if no_cap else min(limit, len(frontier_rows))
    if len(frontier_rows) > limit and not no_cap:
        print(f"  info: {len(frontier_rows)} frontier rows, applying --limit {limit}")

    proposed_count = 0
    skipped_count = 0

    for fr in frontier_rows[:effective_limit]:
        fetched = _fetch_source_for_frontier(fr)
        if fetched is None:
            print(
                f"  frontier_skipped_no_source: {fr.statute_id!r} "
                f"provision={fr.provision_ref!r}",
                file=sys.stderr,
            )
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

    print(f"\npropose-claims: {proposed_count} proposed, {skipped_count} skipped/rejected")
    return 0


def cmd_propose_gap_discovery(args: object) -> int:
    """--gap-discovery handler."""
    he_id: str = getattr(args, "he", None)
    if not he_id:
        print("error: --gap-discovery requires --he HE_ID", file=sys.stderr)
        return 1

    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    claim_store_root: Optional[str] = getattr(args, "claim_store_root", None)
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION") or "fi.v1.INLINE_STATUTE_RESOLUTION"
    backend_name: str = getattr(args, "backend", "mock") or "mock"

    backend = _make_backend(backend_name)
    store = _get_store(data_dir, claim_store_root=claim_store_root)
    store.ensure_dirs()

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
            print(
                f"  frontier_skipped_no_source: {gr.statute_id!r} "
                f"provision={getattr(gr, 'provision_ref', None)!r}",
                file=sys.stderr,
            )
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

    print(f"\ngap-discovery: {proposed_count} proposed, {skipped_count} skipped")
    return 0


def cmd_propose_specific(args: object) -> int:
    """--he HE_ID --kind KIND handler."""
    he_id: str = getattr(args, "he", None)
    kind: str = getattr(args, "kind", "fi.v1.INLINE_STATUTE_RESOLUTION")
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    claim_store_root: Optional[str] = getattr(args, "claim_store_root", None)
    backend_name: str = getattr(args, "backend", "mock") or "mock"

    if not he_id:
        print("error: --he HE_ID is required for specific gap rescue", file=sys.stderr)
        return 1

    backend = _make_backend(backend_name)
    store = _get_store(data_dir, claim_store_root=claim_store_root)
    store.ensure_dirs()

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
        print(
            f"  frontier_skipped_no_source: {he_id!r} (no oracle in corpus)",
            file=sys.stderr,
        )
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
    return 0 if result is not None else 1


def main(args: object) -> None:
    # Activate Finland claim kinds
    import lawvm.finland.claim_kinds  # noqa: F401

    # Register the Finland source provider for jurisdiction 'fi'.
    # Uses the default corpus store path unless overridden by env var.
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
