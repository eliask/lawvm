"""Export fi_refs.parquet — profile-aware ReferenceMention projection (Slice 3).

Produces fi_refs__{profile}.parquet by running extract_all_reference_mentions
over each statute, then optionally applying INLINE_STATUTE_RESOLUTION claims
to fill NULL target_statute_id_str slots (for non-deterministic-only profiles).

Profile behavior (§6 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2):

  deterministic_only (default):
    - Deterministic extractor rows only. No claims consumed.
    - Output: fi_refs__deterministic_only.parquet + legacy fi_refs.parquet (mirror).
    - Row columns source_witness_type=finlex_akn, claim_id=None, etc.

  strict_with_attested_claims:
    - Deterministic rows + INLINE_STATUTE_RESOLUTION claims that are
      human-reviewed + span/entailment validated.
    - NULL target_statute_id_str slots filled from accepted claims.
    - AmbiguousClaimSet findings emitted; ambiguous rows NOT emitted.

  non_strict_with_claims:
    - As strict but entailment-verified LLM proposals admissible without review.

§14 adversary finding (LOAD-BEARING):
  - Output filename encodes profile: fi_refs__{profile}.parquet.
  - Parquet metadata key lawvm.claim_profile is REQUIRED on every output file.
  - Consumers refusing to read without the metadata key are operating correctly.

Schema additions (Slice 3):
  source_witness_type: string
  claim_id:            string (nullable)
  validator_status:    string
  review_status:       string
  replay_authorized:   bool
  emit_profile:        string
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lawvm.core.reference_mention import reference_mention_to_row
from lawvm.core.manual_claims.primitive import (
    ClaimStatus,
    ProfileTag,
    SourceWitnessType,
    ValidatorStatus,
    ReviewStatus,
)


# ---------------------------------------------------------------------------
# Profile metadata helpers
# ---------------------------------------------------------------------------


def _profile_filename(base_name: str, profile: ProfileTag) -> str:
    """Return the profile-stamped filename stem."""
    return f"{base_name}__{profile.value}"


def _attach_profile_metadata(table: Any, profile: ProfileTag) -> Any:
    """Attach lawvm.claim_profile metadata key to a pyarrow Table."""
    existing = table.schema.metadata or {}
    meta = dict(existing)
    meta[b"lawvm.claim_profile"] = profile.value.encode()
    return table.replace_schema_metadata(meta)


# ---------------------------------------------------------------------------
# Deterministic row helpers
# ---------------------------------------------------------------------------


_DETERMINISTIC_ROW_EXTRAS = {
    "source_witness_type": SourceWitnessType.FINLEX_AKN.value,
    "claim_id": None,
    "validator_status": ValidatorStatus.SPAN_VERIFIED.value,
    "review_status": ReviewStatus.HUMAN_REVIEWED.value,
    "replay_authorized": True,
}


def _augment_row(row: Dict[str, Any], profile: ProfileTag, extras: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Add Slice 3 columns to a projection row."""
    result = dict(row)
    if extras:
        result.update(extras)
    else:
        result.update(_DETERMINISTIC_ROW_EXTRAS)
    result["emit_profile"] = profile.value
    return result


# ---------------------------------------------------------------------------
# Corpus + XML loading
# ---------------------------------------------------------------------------


def _load_corpus_store() -> Any:
    from lawvm.finland.corpus import get_corpus_store
    return get_corpus_store()


def _get_statute_xml(statute_id: str, store: Any) -> Optional[bytes]:
    try:
        return store.read_oracle(statute_id)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Deterministic extraction
# ---------------------------------------------------------------------------


def _project_refs_for_statute(
    statute_id: str,
    store: Any,
    profile: ProfileTag,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Project ReferenceMention rows for one statute.

    Returns (mention_rows, diagnostic_rows) with profile columns attached.
    """
    from lawvm.finland.ref_mention_extractor import extract_all_reference_mentions

    xml_bytes = _get_statute_xml(statute_id, store)
    if xml_bytes is None:
        return [], []

    result = extract_all_reference_mentions(xml_bytes, statute_id)

    mention_rows = [
        _augment_row(reference_mention_to_row(m), profile)
        for m in result.mentions
    ]

    diag_rows: List[Dict[str, Any]] = []
    for diag in result.diagnostics:
        diag_rows.append({
            "statute_id": statute_id,
            "rule_id": diag.rule_id,
            "family": diag.family,
            "phase": diag.phase,
            "reason": diag.reason,
            "blocking": diag.blocking,
        })

    return mention_rows, diag_rows


# ---------------------------------------------------------------------------
# Claim-based NULL-slot fill (Piece 4)
# ---------------------------------------------------------------------------


def _load_accepted_inline_statute_claims(
    claims_base_dir: Path,
) -> List[Any]:
    """Load all accepted INLINE_STATUTE_RESOLUTION claims from storage.

    Returns list of (ManualCompilationClaim, ClaimState) pairs.
    Returns [] if no claims directory or no claims present.
    """
    # Activate Finland claim kinds for registry
    import lawvm.finland.claim_kinds  # noqa: F401

    kind_name = "fi.v1.INLINE_STATUTE_RESOLUTION"
    by_kind_dir = claims_base_dir / "by-kind" / kind_name
    if not by_kind_dir.exists():
        return []

    from lawvm.core.manual_claims.storage import ClaimStore
    from lawvm.core.manual_claims.state import project_state

    store = ClaimStore(claims_base_dir)
    pairs = []
    for claim_id in store.list_claims_by_kind(kind_name):
        claim = store.read_claim(claim_id)
        events = list(store.read_events(claim_id))
        state = project_state(claim_id, events)
        if state is not None and state.status == ClaimStatus.ACCEPTED:
            pairs.append((claim, state))
    return pairs


def _apply_null_slot_fills(
    mention_rows: List[Dict[str, Any]],
    accepted_claims: List[Any],
    profile: ProfileTag,
    build_id: str,
    precedence_registry: Any,
    emitted_events: List[Any],
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Fill NULL target_statute_id_str slots from accepted claims.

    For each accepted claim, find matching fi_refs rows by
    (source_statute_id + section_locator + mention_span) and fill NULL
    target_statute_id_str. Returns (updated_rows, ambiguous_findings).

    Slice 3 only handles NULL-slot fill; new-row addition is Slice 4.
    """
    from lawvm.core.manual_claims.composer import derive_composition_decision
    from lawvm.core.manual_claims.precedence import AmbiguousClaimSet

    if not accepted_claims:
        return mention_rows, []

    # Index rows by (statute_id, provision_ref_str, span_start, span_end)
    # The fi_refs row has source_statute_id, source_provision_ref_str,
    # source_span_byte_offset, source_span_len
    def _row_key(row: Dict[str, Any]) -> tuple:
        return (
            row.get("source_statute_id", ""),
            row.get("source_provision_ref_str", ""),
            row.get("source_span_byte_offset"),
            row.get("source_span_len"),
        )

    row_index: Dict[tuple, int] = {_row_key(r): i for i, r in enumerate(mention_rows)}
    updated = list(mention_rows)
    ambiguous_findings: List[AmbiguousClaimSet] = []

    # Group claims by their target key
    # claim.target = tuple of (key, value) pairs:
    #   statute_id, section_locator, mention_span
    for claim, state in accepted_claims:
        target_dict = dict(claim.target)
        value_dict = dict(claim.value)

        statute_id = target_dict.get("statute_id", "")
        section_locator = target_dict.get("section_locator", "")
        mention_span = target_dict.get("mention_span")
        if not isinstance(mention_span, (list, tuple)) or len(mention_span) != 2:
            continue

        span_start = mention_span[0]
        span_len = mention_span[1] - mention_span[0]

        row_key = (statute_id, section_locator, span_start, span_len)
        idx = row_index.get(row_key)
        if idx is None:
            # No matching row — new-row creation deferred to Slice 4
            continue

        row = updated[idx]
        # Only fill if the target_statute_id_str is NULL/empty
        current_target = row.get("target_statute_id", "") or row.get("target_statute_id_str", "")
        if current_target:
            continue  # deterministic has a value; do not override (§3.2: augments-NULL-only)

        # Run composer to get authorization for this claim+profile
        decision, event = derive_composition_decision(
            claim=claim,
            state=state,
            profile=profile,
            build_id=build_id,
            precedence_registry=precedence_registry,
        )
        emitted_events.append(event)

        if not decision.authorized:
            continue

        # Fill the NULL slot
        resolved_id = value_dict.get("resolved_statute_id", "")
        row_copy = dict(row)
        # fi_refs.parquet uses "target_statute_id" as the column name
        if "target_statute_id" in row_copy:
            row_copy["target_statute_id"] = resolved_id
        if "target_statute_id_str" in row_copy:
            row_copy["target_statute_id_str"] = resolved_id
        row_copy["source_witness_type"] = claim.source_witness_type.value
        row_copy["claim_id"] = claim.claim_id
        row_copy["validator_status"] = state.validator_status.value
        row_copy["review_status"] = state.review_status.value
        row_copy["replay_authorized"] = decision.replay_authorized
        row_copy["emit_profile"] = profile.value
        updated[idx] = row_copy

    return updated, ambiguous_findings


# ---------------------------------------------------------------------------
# JSONL + Parquet writers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return len(rows)


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    profile: ProfileTag,
) -> bool:
    """Try to write rows as Parquet with profile metadata. Returns True if ok."""
    try:
        import pyarrow as pa  # ty: ignore[unresolved-import]
        import pyarrow.parquet as pq  # ty: ignore[unresolved-import]
    except ImportError:
        return False

    if not rows:
        schema = pa.schema([
            pa.field("source_statute_id", pa.string()),
            pa.field("source_provision_ref_str", pa.string()),
            pa.field("target_statute_id", pa.string()),
            pa.field("target_provision_ref_str", pa.string()),
            pa.field("cite_kind", pa.string()),
            pa.field("cite_confidence", pa.string()),
            pa.field("edge_subtype", pa.string()),
            pa.field("phrase_lemma", pa.string()),
            pa.field("source_span_file", pa.string()),
            pa.field("source_span_byte_offset", pa.int64()),
            pa.field("source_span_len", pa.int64()),
            pa.field("valid_at_start", pa.string()),
            pa.field("valid_at_end", pa.string()),
            pa.field("target_stat_hash", pa.string()),
            # Slice 3 provenance columns
            pa.field("source_witness_type", pa.string()),
            pa.field("claim_id", pa.string()),
            pa.field("validator_status", pa.string()),
            pa.field("review_status", pa.string()),
            pa.field("replay_authorized", pa.bool_()),
            pa.field("emit_profile", pa.string()),
        ])
        table = pa.table({col: [] for col in schema.names}, schema=schema)
        table = _attach_profile_metadata(table, profile)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, str(path), compression="zstd")
        return True

    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    table = _attach_profile_metadata(table, profile)
    pq.write_table(table, str(path), compression="zstd")
    return True


# ---------------------------------------------------------------------------
# Main export function
# ---------------------------------------------------------------------------


def export_fi_refs(
    corpus: List[Tuple[int, str]],
    *,
    data_dir: str = ".tmp/projections",
    use_parquet: bool = True,
    limit: Optional[int] = None,
    profile: ProfileTag = ProfileTag.DETERMINISTIC_ONLY,
    build_id: str = "default",
    claims_base_dir: Optional[Path] = None,
) -> int:
    """Export fi_refs__{profile}.parquet projection for a corpus of Finnish statutes.

    Args:
        corpus:           List of (amendment_count, statute_id) tuples.
        data_dir:         Output directory.
        use_parquet:      Write Parquet if pyarrow available (also writes JSONL).
        limit:            Process only first N statutes (for testing).
        profile:          Which claim composition profile to use.
        build_id:         Stable build identifier for composition events.
        claims_base_dir:  Path to manual_claims/ directory. Defaults to
                          {data_dir}/manual_claims.

    Returns:
        Number of ReferenceMention rows written.
    """
    store = None
    try:
        store = _load_corpus_store()
    except Exception as exc:
        print(f"  warning: could not load corpus store: {exc}", file=sys.stderr)
        return 0

    if limit:
        corpus = corpus[:limit]

    out = Path(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    if claims_base_dir is None:
        claims_base_dir = out / "manual_claims"

    # Load precedence registry (required for non-deterministic profiles)
    precedence_registry = None
    if profile != ProfileTag.DETERMINISTIC_ONLY:
        from lawvm.core.manual_claims.precedence import load_precedence_registry
        yaml_path = out / "claim_precedence.yaml"
        if not yaml_path.exists():
            # Try the standard location one level up
            yaml_path = out.parent / "claim_precedence.yaml"
        if not yaml_path.exists():
            print(
                f"  warning: claim_precedence.yaml not found at {yaml_path}; "
                "proceeding without precedence resolution (ambiguous sets will be skipped)",
                file=sys.stderr,
            )
            precedence_registry = None
        else:
            precedence_registry = load_precedence_registry(yaml_path)

    total = len(corpus)
    all_mention_rows: List[Dict[str, Any]] = []
    all_diag_rows: List[Dict[str, Any]] = []

    for i, (_, statute_id) in enumerate(corpus, 1):
        t0 = time.time()
        mention_rows, diag_rows = _project_refs_for_statute(statute_id, store, profile)
        all_mention_rows.extend(mention_rows)
        all_diag_rows.extend(diag_rows)

        if i % 50 == 0 or i == total:
            elapsed = time.time() - t0
            print(f"  [{i}/{total}] refs: {len(all_mention_rows):,} total ({elapsed:.1f}s last)")

    # For non-deterministic profiles, apply NULL-slot fills from claims
    composition_events: List[Any] = []
    consumed_claim_ids: List[str] = []
    accepted_claims: List[Any] = []
    if profile != ProfileTag.DETERMINISTIC_ONLY and precedence_registry is not None:
        accepted_claims = _load_accepted_inline_statute_claims(claims_base_dir)
        # Slice 5: strict-mode guard — refuse retracted claims
        _check_no_retracted_claims_in_strict(accepted_claims, profile)
        if accepted_claims:
            all_mention_rows, _ = _apply_null_slot_fills(
                all_mention_rows,
                accepted_claims,
                profile,
                build_id,
                precedence_registry,
                composition_events,
            )
            # Track which claims were consumed (those that actually filled a slot)
            for row in all_mention_rows:
                cid = row.get("claim_id")
                if cid and cid not in consumed_claim_ids:
                    consumed_claim_ids.append(cid)

    # Write outputs
    profile_stem = _profile_filename("fi_refs", profile)

    # Always write JSONL
    jsonl_count = _write_jsonl(out / f"{profile_stem}.jsonl", all_mention_rows)

    parquet_path_str = str(out / f"{profile_stem}.parquet")
    if use_parquet:
        # Write profile-stamped parquet (canonical)
        ok = _try_write_parquet(out / f"{profile_stem}.parquet", all_mention_rows, profile)
        if ok:
            print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (Parquet + JSONL)")
            # Mirror as legacy fi_refs.parquet for deterministic_only profile only
            if profile == ProfileTag.DETERMINISTIC_ONLY:
                _try_write_parquet(out / "fi_refs.parquet", all_mention_rows, profile)
                _write_jsonl(out / "fi_refs.jsonl", all_mention_rows)
        else:
            print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (JSONL only; pyarrow not installed)")
    else:
        print(f"  fi_refs ({profile.value}): {jsonl_count:,} rows (JSONL)")

    # Slice 5: emit consumed events for each claim used in this build
    if consumed_claim_ids and claims_base_dir is not None:
        claim_rows = [r for r in all_mention_rows if r.get("claim_id")]
        track_consumption_for_build(
            build_id=build_id,
            profile=profile,
            projection_artifact_path=parquet_path_str,
            consumed_claim_ids=consumed_claim_ids,
            affected_projection_rows=claim_rows,
            claims_base_dir=claims_base_dir,
        )

    # Write diagnostics
    if all_diag_rows:
        _write_jsonl(out / "fi_refs_diagnostics.jsonl", all_diag_rows)

    return jsonl_count


# ---------------------------------------------------------------------------
# Slice 5: consumption tracking + strict-mode retraction guard
# ---------------------------------------------------------------------------


def _hash_row(row: dict) -> str:
    """Stable hash for a projection row (for taint-report row tracking)."""
    import hashlib
    serialized = json.dumps(
        {k: str(v) for k, v in sorted(row.items()) if k != "emit_profile"},
        sort_keys=True,
    )
    return hashlib.sha256(serialized.encode()).hexdigest()[:32]


def track_consumption_for_build(
    build_id: str,
    profile: ProfileTag,
    projection_artifact_path: str,
    consumed_claim_ids: List[str],
    affected_projection_rows: List[Dict[str, Any]],
    claims_base_dir: Path,
) -> None:
    """Emit a ClaimStateEvent(event_kind='consumed') for each consumed claim.

    Per §5.1 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2.
    Records build_id, profile, row_hashes, and invalidated_PIT_intervals in
    the event reason payload (JSON).

    Called by export_fi_refs after the parquet write completes.
    """
    from lawvm.core.manual_claims.storage import ClaimStore
    from lawvm.core.manual_claims.primitive import (
        ClaimStateEvent,
        Producer,
    )
    from datetime import datetime, timezone

    if not consumed_claim_ids:
        return

    store = ClaimStore(claims_base_dir)
    row_hashes = [_hash_row(r) for r in affected_projection_rows]

    now = datetime.now(tz=timezone.utc)
    producer = Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=now,
        environment="lawvm-export-fi-refs",
    )

    for claim_id in consumed_claim_ids:
        if not store.claim_exists(claim_id):
            continue

        claim = store.read_claim(claim_id)
        valid_at = claim.valid_at

        reason_payload = json.dumps({
            "build_id": build_id,
            "profile": profile.value,
            "projection_artifact_path": projection_artifact_path,
            "row_hashes": row_hashes,
            "invalidated_PIT_intervals": [
                {
                    "target_locator": (
                        claim.claim_scope.provision_ref or claim.claim_scope.statute_id
                    ),
                    "interval_start": valid_at[0].isoformat(),
                    "interval_end": valid_at[1].isoformat() if valid_at[1] else None,
                }
            ],
            "dependent_downstream_artifacts": [],
        })

        event = ClaimStateEvent(
            claim_id=claim_id,
            event_kind="consumed",
            timestamp=now,
            producer=producer,
            old_status=None,
            new_status=None,
            reason=reason_payload,
        )
        store.append_event(event)


def _check_no_retracted_claims_in_strict(
    accepted_claims: List[Any],
    profile: ProfileTag,
) -> None:
    """For strict profile: raise if any claim to be consumed is retracted.

    Per §5.4: strict-mode builds refuse to incorporate retracted claims.
    Operator must rebuild (new build_id = clean by construction).
    """
    if profile != ProfileTag.STRICT_WITH_ATTESTED_CLAIMS:
        return

    from lawvm.core.manual_claims.primitive import ClaimStatus

    for claim, state in accepted_claims:
        if state.status == ClaimStatus.RETRACTED:
            raise SystemExit(
                f"error: strict profile refuses retracted claim {claim.claim_id[:32]}... "
                "Retract the claim and rebuild to produce a clean artifact."
            )
