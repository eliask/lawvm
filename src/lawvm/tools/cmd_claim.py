"""lawvm claim — operator CLI for manual compilation claims.

Subcommands (Slice 1+2 only; Slices 3-5 deferred):
  propose   --claim-file FILE.json [--validator span|entailment|all]
  accept    CLAIM_ID
  reject    CLAIM_ID --reason "..."
  retract   CLAIM_ID --reason "..."
  list      [--kind ...] [--layer ...] [--review-status ...] [--status ...]
  show      CLAIM_ID

Design:
  - NO projection mutation here (Slice 3 concern).
  - State transitions write ClaimStateEvents to events.jsonl.
  - Current state is materialized in states/current/.
  - 'show' renders all four records: claim + state + event history +
    composition decisions (empty in Slice 2).
  - Self-authorization is impossible: the CLI is the only path to state
    transitions; claim files asserting review_status=human_reviewed cannot
    self-promote (test_self_authorization_impossible).

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from lawvm.core.manual_claims.hashing import compute_claim_id, verify_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    Producer,
    ReviewStatus,
    ValidatorStatus,
)
from lawvm.core.manual_claims.state import project_state
from lawvm.core.manual_claims.storage import ClaimStore, _dict_to_claim


_DEFAULT_DATA_DIR = "data/fi/v1"
_MANUAL_CLAIMS_SUBDIR = "manual_claims"


def _get_store(data_dir: str) -> ClaimStore:
    base = Path(data_dir) / _MANUAL_CLAIMS_SUBDIR
    return ClaimStore(base)


def _cli_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle=None,
        model_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        environment="lawvm-cli",
    )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ---------------------------------------------------------------------------
# propose
# ---------------------------------------------------------------------------


def cmd_propose(args: object) -> int:
    """Load and file a proposed claim from a JSON file.

    1. Parse JSON.
    2. Build ManualCompilationClaim (from content; NOT trusting stored claim_id).
    3. Recompute claim_id from canonical payload.
    4. Verify the stored claim_id matches (hard fail on mismatch).
    5. Run requested validators.
    6. Write claim + initial event + initial state.
    """
    import json as _json

    claim_file = Path(args.claim_file)  # type: ignore[attr-defined]
    if not claim_file.exists():
        print(f"error: claim file not found: {claim_file}", file=sys.stderr)
        return 1

    raw = _json.loads(claim_file.read_text(encoding="utf-8"))

    # Build claim from the payload — this will catch bad enum values etc.
    claim = _dict_to_claim(raw)

    # Load-time hash check: stored claim_id must match recomputed value.
    verify_claim_id(claim)

    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))
    store.ensure_dirs()

    if store.claim_exists(claim.claim_id):
        print(f"claim already filed: {claim.claim_id}")
        return 0

    # Determine validator to run
    validator_arg = getattr(args, "validator", None)
    validator_status = ValidatorStatus.UNVALIDATED

    if validator_arg in ("span", "all"):
        spec = _get_kind_spec(claim.claim_kind)
        if spec and spec.span_validator:
            result = spec.span_validator(claim, b"")
            if result.passed:
                validator_status = ValidatorStatus.SPAN_VERIFIED
                print(f"span validator: PASSED")
            else:
                print(f"span validator: FAILED — {result.reason}", file=sys.stderr)
                if validator_arg == "span":
                    return 1

    if validator_arg in ("entailment", "all"):
        spec = _get_kind_spec(claim.claim_kind)
        if spec and spec.entailment_validator:
            result = spec.entailment_validator(claim, b"")
            if result.passed:
                validator_status = ValidatorStatus.ENTAILMENT_VERIFIED
                print(f"entailment validator: PASSED")
            else:
                print(f"entailment validator: FAILED — {result.reason}", file=sys.stderr)
                if validator_arg == "entailment":
                    return 1

    # Write claim to objects/sha256/ and by-kind/
    store.write_claim(claim)
    store.write_by_kind(claim)

    # Initial event
    producer = _cli_producer()
    now = _now()
    event = ClaimStateEvent(
        claim_id=claim.claim_id,
        event_kind="proposed",
        timestamp=now,
        producer=producer,
        old_status=None,
        new_status="proposed",
        reason="filed via lawvm claim propose",
    )
    store.append_event(event)

    # Initial state
    state = ClaimState(
        claim_id=claim.claim_id,
        status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=validator_status,
        confidence=claim.requested_profiles[0].value if claim.requested_profiles else "medium",  # type: ignore[assignment]
        last_updated=now,
    )
    # Use confidence from claim — we stored it as a placeholder; rebuild properly
    from lawvm.core.manual_claims.primitive import ClaimConfidence
    state = ClaimState(
        claim_id=claim.claim_id,
        status=ClaimStatus.PROPOSED,
        review_status=ReviewStatus.PROPOSED,
        validator_status=validator_status,
        confidence=ClaimConfidence.MEDIUM,
        last_updated=now,
    )
    store.write_state(state)

    print(f"proposed: {claim.claim_id}")
    return 0


def _get_kind_spec(claim_kind: str):
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec
    return get_claim_kind_spec(claim_kind)


# ---------------------------------------------------------------------------
# accept
# ---------------------------------------------------------------------------


def cmd_accept(args: object) -> int:
    """Accept a proposed claim (human review decision)."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    if not store.claim_exists(claim_id):
        print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return 1

    current_state = store.read_state(claim_id)
    if current_state is None:
        print(f"error: no state for claim {claim_id} — was it proposed?", file=sys.stderr)
        return 1

    if current_state.status != ClaimStatus.PROPOSED:
        print(
            f"error: claim {claim_id} is in status {current_state.status.value!r}, "
            "expected 'proposed'",
            file=sys.stderr,
        )
        return 1

    now = _now()
    producer = _cli_producer()

    event = ClaimStateEvent(
        claim_id=claim_id,
        event_kind="accepted",
        timestamp=now,
        producer=producer,
        old_status="proposed",
        new_status="accepted",
        reason="accepted by operator via lawvm claim accept",
    )
    store.append_event(event)

    new_state = ClaimState(
        claim_id=claim_id,
        status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.HUMAN_REVIEWED,
        validator_status=current_state.validator_status,
        confidence=current_state.confidence,
        last_updated=now,
    )
    store.write_state(new_state)

    print(f"accepted: {claim_id}")
    return 0


# ---------------------------------------------------------------------------
# reject
# ---------------------------------------------------------------------------


def cmd_reject(args: object) -> int:
    """Reject a proposed claim."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    reason: str = args.reason  # type: ignore[attr-defined]
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    if not store.claim_exists(claim_id):
        print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return 1

    current_state = store.read_state(claim_id)
    if current_state is None:
        print(f"error: no state for claim {claim_id}", file=sys.stderr)
        return 1

    if current_state.status != ClaimStatus.PROPOSED:
        print(
            f"error: claim {claim_id} is in status {current_state.status.value!r}, "
            "expected 'proposed'",
            file=sys.stderr,
        )
        return 1

    now = _now()
    producer = _cli_producer()

    event = ClaimStateEvent(
        claim_id=claim_id,
        event_kind="rejected",
        timestamp=now,
        producer=producer,
        old_status="proposed",
        new_status="rejected",
        reason=reason,
    )
    store.append_event(event)

    new_state = ClaimState(
        claim_id=claim_id,
        status=ClaimStatus.REJECTED,
        review_status=current_state.review_status,
        validator_status=current_state.validator_status,
        confidence=current_state.confidence,
        last_updated=now,
    )
    store.write_state(new_state)

    print(f"rejected: {claim_id}")
    return 0


# ---------------------------------------------------------------------------
# retract
# ---------------------------------------------------------------------------


def cmd_retract(args: object) -> int:
    """Retract an accepted claim. (Taint report is Slice 5.)"""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    reason: str = args.reason  # type: ignore[attr-defined]
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    if not store.claim_exists(claim_id):
        print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return 1

    current_state = store.read_state(claim_id)
    if current_state is None:
        print(f"error: no state for claim {claim_id}", file=sys.stderr)
        return 1

    if current_state.status != ClaimStatus.ACCEPTED:
        print(
            f"error: claim {claim_id} is in status {current_state.status.value!r}, "
            "expected 'accepted'",
            file=sys.stderr,
        )
        return 1

    now = _now()
    producer = _cli_producer()

    event = ClaimStateEvent(
        claim_id=claim_id,
        event_kind="retracted",
        timestamp=now,
        producer=producer,
        old_status="accepted",
        new_status="retracted",
        reason=reason,
    )
    store.append_event(event)

    new_state = ClaimState(
        claim_id=claim_id,
        status=ClaimStatus.RETRACTED,
        review_status=current_state.review_status,
        validator_status=current_state.validator_status,
        confidence=current_state.confidence,
        last_updated=now,
    )
    store.write_state(new_state)

    print(f"retracted: {claim_id}")
    print("note: taint report for affected builds is deferred to Slice 5")
    return 0


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


def cmd_list(args: object) -> int:
    """List claims with optional filters."""
    kind_filter: Optional[str] = getattr(args, "kind", None)
    layer_filter: Optional[str] = getattr(args, "layer", None)
    review_status_filter: Optional[str] = getattr(args, "review_status", None)
    status_filter: Optional[str] = getattr(args, "status", None)
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    claim_ids = store.list_all_claim_ids()
    if not claim_ids:
        print("no claims filed")
        return 0

    rows = []
    for claim_id in sorted(claim_ids):
        claim = store.read_claim(claim_id)
        state = store.read_state(claim_id)

        if kind_filter and claim.claim_kind != kind_filter:
            continue
        if layer_filter and claim.claim_layer.value != layer_filter:
            continue
        if state and review_status_filter and state.review_status.value != review_status_filter:
            continue
        if state and status_filter and state.status.value != status_filter:
            continue

        rows.append({
            "claim_id": claim.claim_id[:16] + "...",
            "kind": claim.claim_kind,
            "layer": claim.claim_layer.value,
            "status": state.status.value if state else "?",
            "review": state.review_status.value if state else "?",
            "validator": state.validator_status.value if state else "?",
        })

    if not rows:
        print("no claims match filters")
        return 0

    # Simple table output
    headers = ["claim_id", "kind", "layer", "status", "review", "validator"]
    widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
    fmt = "  ".join(f"{{:<{widths[h]}}}" for h in headers)
    print(fmt.format(*headers))
    print(fmt.format(*("-" * widths[h] for h in headers)))
    for row in rows:
        print(fmt.format(*[row[h] for h in headers]))

    return 0


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def cmd_show(args: object) -> int:
    """Show all four records for a claim: payload + state + events + composition."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    if not store.claim_exists(claim_id):
        print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return 1

    claim = store.read_claim(claim_id)
    state = store.read_state(claim_id)
    events = list(store.read_events(claim_id))

    print("=" * 72)
    print(f"CLAIM PAYLOAD  ({claim.claim_id[:32]}...)")
    print("=" * 72)
    print(f"  claim_kind:         {claim.claim_kind}")
    print(f"  claim_layer:        {claim.claim_layer.value}")
    print(f"  jurisdiction:       {claim.jurisdiction}")
    print(f"  schema_version:     {claim.schema_version}")
    print(f"  source_witness_type:{claim.source_witness_type.value}")
    print(f"  statute_id:         {claim.claim_scope.statute_id}")
    print(f"  provision_ref:      {claim.claim_scope.provision_ref}")
    print(f"  cited_source_hash:  {claim.cited_source_hash[:16]}...")
    print(f"  cited_source_span:  {claim.cited_source_span}")
    print(f"  target:             {dict(claim.target)}")
    print(f"  value:              {dict(claim.value)}")
    print(f"  rationale:          {claim.rationale[:80]}")
    print(f"  supersedes:         {claim.supersedes}")
    print(f"  disputes:           {claim.disputes}")

    print()
    print("=" * 72)
    print("CURRENT STATE")
    print("=" * 72)
    if state:
        print(f"  status:           {state.status.value}")
        print(f"  review_status:    {state.review_status.value}")
        print(f"  validator_status: {state.validator_status.value}")
        print(f"  confidence:       {state.confidence.value}")
        print(f"  last_updated:     {state.last_updated.isoformat()}")
    else:
        print("  (no state materialized)")

    print()
    print("=" * 72)
    print(f"EVENT HISTORY  ({len(events)} events)")
    print("=" * 72)
    for evt in events:
        print(
            f"  [{evt.timestamp.isoformat()[:19]}] "
            f"{evt.event_kind:<20} "
            f"{evt.old_status or '?'} → {evt.new_status or '?'}  "
            f"reason: {evt.reason[:60]}"
        )

    print()
    print("=" * 72)
    print("COMPOSITION DECISIONS  (Slice 3 concern — none in Slice 1+2)")
    print("=" * 72)
    print("  (empty — composition decisions are derived by composer at build time)")

    return 0


# ---------------------------------------------------------------------------
# validate (standalone, re-run validators on an already-filed claim)
# ---------------------------------------------------------------------------


def cmd_validate(args: object) -> int:
    """Re-run validators on an already-filed claim."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    validator_arg: str = getattr(args, "validator", "all")
    store = _get_store(getattr(args, "data_dir", _DEFAULT_DATA_DIR))

    if not store.claim_exists(claim_id):
        print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return 1

    claim = store.read_claim(claim_id)
    spec = _get_kind_spec(claim.claim_kind)

    if spec is None:
        print(f"warning: unknown claim kind {claim.claim_kind!r} — no validators available")
        return 0

    rc = 0
    if validator_arg in ("span", "all") and spec.span_validator:
        result = spec.span_validator(claim, b"")
        status = "PASSED" if result.passed else "FAILED"
        print(f"span_verified: {status} — {result.reason}")
        if not result.passed:
            rc = 1

    if validator_arg in ("entailment", "all") and spec.entailment_validator:
        result = spec.entailment_validator(claim, b"")
        status = "PASSED" if result.passed else "FAILED"
        print(f"entailment_verified: {status} — {result.reason}")
        if not result.passed:
            rc = 1

    return rc


# ---------------------------------------------------------------------------
# main dispatch
# ---------------------------------------------------------------------------


def main(args: object) -> None:
    subcmd = getattr(args, "claim_subcommand", None)
    dispatch = {
        "propose": cmd_propose,
        "accept": cmd_accept,
        "reject": cmd_reject,
        "retract": cmd_retract,
        "list": cmd_list,
        "show": cmd_show,
        "validate": cmd_validate,
    }
    fn = dispatch.get(subcmd)
    if fn is None:
        print(f"unknown claim subcommand: {subcmd!r}", file=sys.stderr)
        sys.exit(1)
    rc = fn(args)
    if rc:
        sys.exit(rc)
