"""lawvm validate-claims — re-run validators on filed claims (Slice 4).

Subcommands:
  --claim-id CLAIM_ID         re-run all validators on one claim
  --all [--kind ...] [--status proposed]  re-run on multiple claims

Writes validator events to the event log for each run.
Exit code 0 if all validated claims pass; 1 if any fail.

AGENTS.md §1.10: no broad try/except in non-test code.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from lawvm.core.manual_claims.primitive import (
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    Producer,
    ValidatorStatus,
)
from lawvm.core.manual_claims.state import project_state
from lawvm.core.manual_claims.storage import ClaimStore

_DEFAULT_DATA_DIR = "data/fi/v1"
_MANUAL_CLAIMS_SUBDIR = "manual_claims"


def _get_store(data_dir: str) -> ClaimStore:
    return ClaimStore(Path(data_dir) / _MANUAL_CLAIMS_SUBDIR)


def _tool_producer() -> Producer:
    return Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=datetime.now(tz=timezone.utc),
        environment="lawvm-validate-claims",
    )


def _validate_one_claim(
    claim_id: str,
    store: ClaimStore,
    source_bytes: bytes = b"",
    *,
    verbose: bool = True,
) -> bool:
    """Run span + entailment validators on one claim. Returns True if all pass."""
    import lawvm.finland.claim_kinds  # noqa: F401 — ensure kinds are registered
    from lawvm.core.manual_claims.kind_registry import get_claim_kind_spec

    if not store.claim_exists(claim_id):
        if verbose:
            print(f"error: claim not found: {claim_id}", file=sys.stderr)
        return False

    claim = store.read_claim(claim_id)
    spec = get_claim_kind_spec(claim.claim_kind)
    if spec is None:
        if verbose:
            print(f"  warning: unknown claim kind {claim.claim_kind!r} — no validators", file=sys.stderr)
        return True

    now = datetime.now(tz=timezone.utc)
    producer = _tool_producer()
    all_passed = True
    final_validator_status = ValidatorStatus.UNVALIDATED

    if spec.span_validator:
        result = spec.span_validator(claim, source_bytes)
        status_str = "PASSED" if result.passed else "FAILED"
        if verbose:
            print(f"  span_verified: {status_str} — {result.reason}")
        if result.passed:
            final_validator_status = ValidatorStatus.SPAN_VERIFIED
            event = ClaimStateEvent(
                claim_id=claim_id,
                event_kind="span_verified",
                timestamp=now,
                producer=producer,
                old_status=None,
                new_status=None,
                reason=result.reason,
            )
            store.append_event(event)
        else:
            all_passed = False

    if spec.entailment_validator:
        result = spec.entailment_validator(claim, source_bytes)
        status_str = "PASSED" if result.passed else "FAILED"
        if verbose:
            print(f"  entailment_verified: {status_str} — {result.reason}")
        if result.passed:
            final_validator_status = ValidatorStatus.ENTAILMENT_VERIFIED
            event = ClaimStateEvent(
                claim_id=claim_id,
                event_kind="entailment_verified",
                timestamp=now,
                producer=producer,
                old_status=None,
                new_status=None,
                reason=result.reason,
            )
            store.append_event(event)
        else:
            all_passed = False

    # Update current state to reflect new validator_status
    current_state = store.read_state(claim_id)
    if current_state is not None and final_validator_status != ValidatorStatus.UNVALIDATED:
        events = list(store.read_events(claim_id))
        projected = project_state(claim_id, events)
        if projected is not None:
            store.write_state(projected)

    return all_passed


def cmd_validate_one(args: object) -> int:
    """--claim-id CLAIM_ID handler."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    store = _get_store(data_dir)

    passed = _validate_one_claim(claim_id, store, b"", verbose=True)
    return 0 if passed else 1


def cmd_validate_all(args: object) -> int:
    """--all handler."""
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    kind_filter: Optional[str] = getattr(args, "kind", None)
    status_filter: Optional[str] = getattr(args, "status", None)

    store = _get_store(data_dir)

    claim_ids = store.list_all_claim_ids()
    if not claim_ids:
        print("no claims filed")
        return 0

    all_ok = True
    validated = 0
    for claim_id in sorted(claim_ids):
        claim = store.read_claim(claim_id)
        state = store.read_state(claim_id)

        if kind_filter and claim.claim_kind != kind_filter:
            continue
        if status_filter and state and state.status.value != status_filter:
            continue

        print(f"\nvalidating {claim_id[:32]}... ({claim.claim_kind})")
        passed = _validate_one_claim(claim_id, store, b"", verbose=True)
        if not passed:
            all_ok = False
        validated += 1

    print(f"\nvalidated {validated} claim(s)")
    return 0 if all_ok else 1


def main(args: object) -> None:
    claim_id: Optional[str] = getattr(args, "claim_id", None)
    all_flag: bool = getattr(args, "all", False)

    if claim_id:
        rc = cmd_validate_one(args)
    elif all_flag:
        rc = cmd_validate_all(args)
    else:
        print("error: one of --claim-id or --all required", file=sys.stderr)
        rc = 1

    if rc:
        sys.exit(rc)
