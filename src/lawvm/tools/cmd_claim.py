"""lawvm claim — operator CLI for manual compilation claims.

Subcommands (Slices 1-5):
  propose         --claim-file FILE.json [--validator span|entailment|all]
  accept          CLAIM_ID
  reject          CLAIM_ID --reason "..."
  retract         CLAIM_ID --reason "..."  (Slice 5: emits taint report)
  list            [--kind ...] [--layer ...] [--review-status ...] [--status ...]
  show            CLAIM_ID
  validate        CLAIM_ID [--validator ...]
  taint-report    CLAIM_ID | --list | --build BUILD_ID

Design:
  - NO projection mutation here (Slice 3 concern).
  - State transitions write ClaimStateEvents to events.jsonl.
  - Current state is materialized in states/current/.
  - 'retract' now emits ClaimRetractionTaintReport (Slice 5).
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
# retract (Slice 5 — emits taint report)
# ---------------------------------------------------------------------------


def _build_taint_report(
    claim_id: str,
    retraction_event: ClaimStateEvent,
    store: ClaimStore,
    claim: object,
) -> object:
    """Build ClaimRetractionTaintReport from consumed events for this claim."""
    from lawvm.core.manual_claims.taint_report import (
        AffectedBuild,
        ClaimRetractionTaintReport,
        InvalidatedPITInterval,
    )
    from datetime import date

    retraction_ts = retraction_event.timestamp
    retraction_event_id = f"{claim_id}:{retraction_ts.isoformat()}"

    # Scan event log for consumed events with this claim_id
    affected_builds = []
    seen_build_ids: set = set()

    for event in store.read_events(claim_id):
        if event.event_kind != "consumed":
            continue
        payload = {}
        try:
            payload = json.loads(event.reason)
        except (json.JSONDecodeError, TypeError):
            continue

        build_id = payload.get("build_id", "")
        if not build_id or build_id in seen_build_ids:
            continue
        seen_build_ids.add(build_id)

        profile = payload.get("profile", "")
        projection_path = payload.get("projection_artifact_path", "")
        row_hashes = tuple(payload.get("row_hashes", []))

        # Derive invalidated PIT intervals from claim's valid_at
        valid_at = getattr(claim, "valid_at", None)
        pit_intervals = ()
        if valid_at is not None:
            interval_start = valid_at[0]
            interval_end = valid_at[1]
            target_locator = getattr(claim, "claim_scope", None)
            statute_id_str = getattr(target_locator, "statute_id", "") if target_locator else ""
            provision_ref_str = getattr(target_locator, "provision_ref", "") if target_locator else ""
            locator = provision_ref_str or statute_id_str or "unknown"
            pit_intervals = (
                InvalidatedPITInterval(
                    target_locator=locator,
                    interval_start=interval_start,
                    interval_end=interval_end,
                ),
            )

        dependent_artifacts = tuple(payload.get("dependent_downstream_artifacts", []))

        affected_builds.append(AffectedBuild(
            build_id=build_id,
            profile=profile,
            projection_artifact_path=projection_path,
            affected_projection_row_hashes=row_hashes,
            invalidated_PIT_intervals=pit_intervals,
            dependent_downstream_artifacts=dependent_artifacts,
        ))

    return ClaimRetractionTaintReport(
        retracted_claim_id=claim_id,
        retraction_event_id=retraction_event_id,
        retraction_timestamp=retraction_ts,
        retraction_reason=retraction_event.reason,
        affected_builds=tuple(affected_builds),
    )


def cmd_retract(args: object) -> int:
    """Retract an accepted claim. Emits taint report for affected builds (Slice 5)."""
    claim_id: str = args.claim_id  # type: ignore[attr-defined]
    reason: str = args.reason  # type: ignore[attr-defined]
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    store = _get_store(data_dir)

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

    # Slice 5: build + write taint report
    claim = store.read_claim(claim_id)
    report = _build_taint_report(claim_id, event, store, claim)

    taint_reports_dir = store._base / "claim_taint_reports"
    from lawvm.core.manual_claims.taint_report import write_taint_report, report_to_dict
    write_taint_report(report, taint_reports_dir)  # type: ignore[arg-type]

    # Emit taint_report_emitted event
    report_path_str = str(taint_reports_dir)
    taint_event = ClaimStateEvent(
        claim_id=claim_id,
        event_kind="taint_report_emitted",
        timestamp=now,
        producer=producer,
        old_status=None,
        new_status=None,
        reason=json.dumps({
            "retraction_event_id": f"{claim_id}:{now.isoformat()}",
            "taint_reports_dir": report_path_str,
            "affected_builds": len(report.affected_builds),  # type: ignore[attr-defined]
        }),
    )
    store.append_event(taint_event)

    # Print the taint report
    affected = getattr(report, "affected_builds", ())
    if affected:
        print(f"\ntaint report: {len(affected)} affected build(s)")
        for ab in affected:
            print(f"  build: {ab.build_id}")
            print(f"  profile: {ab.profile}")
            print(f"  projection: {ab.projection_artifact_path}")
            print(f"  row hashes: {len(ab.affected_projection_row_hashes)}")
            for iv in ab.invalidated_PIT_intervals:
                end_str = iv.interval_end.isoformat() if iv.interval_end else "open"
                print(f"    PIT interval [{iv.interval_start} .. {end_str}] for {iv.target_locator}")
    else:
        print("taint report: no consumed builds found (claim was not used in any build)")

    return 0


# ---------------------------------------------------------------------------
# taint-report
# ---------------------------------------------------------------------------


def cmd_taint_report(args: object) -> int:
    """Show taint reports: for a specific claim, list all, or by build."""
    data_dir: str = getattr(args, "data_dir", _DEFAULT_DATA_DIR)
    claim_id: Optional[str] = getattr(args, "claim_id", None)
    list_all: bool = getattr(args, "list", False)
    build_id: Optional[str] = getattr(args, "build", None)

    taint_reports_dir = Path(data_dir) / _MANUAL_CLAIMS_SUBDIR / "claim_taint_reports"

    from lawvm.core.manual_claims.taint_report import (
        find_taint_reports_for_claim,
        find_taint_reports_for_build,
        list_all_taint_reports,
        read_taint_report,
        report_to_dict,
    )

    if claim_id and not list_all and not build_id:
        paths = find_taint_reports_for_claim(taint_reports_dir, claim_id)
        if not paths:
            print(f"no taint reports found for claim {claim_id}")
            return 0
        for p in paths:
            report = read_taint_report(p)
            print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
        return 0

    if list_all:
        paths = list_all_taint_reports(taint_reports_dir)
        if not paths:
            print("no taint reports found")
            return 0
        print(f"{len(paths)} taint report(s):")
        for p in paths:
            report = read_taint_report(p)
            ab_count = len(report.affected_builds)
            print(f"  {report.retracted_claim_id[:32]}... — {ab_count} affected build(s) — {p.name}")
        return 0

    if build_id:
        paths = find_taint_reports_for_build(taint_reports_dir, build_id)
        if not paths:
            print(f"no taint reports found for build {build_id}")
            return 0
        print(f"{len(paths)} taint report(s) for build {build_id}:")
        for p in paths:
            report = read_taint_report(p)
            print(json.dumps(report_to_dict(report), indent=2, sort_keys=True))
        return 0

    print("error: one of CLAIM_ID, --list, or --build BUILD_ID required", file=sys.stderr)
    return 1


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

    now = _now()
    producer = _cli_producer()
    rc = 0

    if validator_arg in ("span", "all") and spec.span_validator:
        result = spec.span_validator(claim, b"")
        status = "PASSED" if result.passed else "FAILED"
        print(f"span_verified: {status} — {result.reason}")
        if result.passed:
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
            rc = 1

    if validator_arg in ("entailment", "all") and spec.entailment_validator:
        result = spec.entailment_validator(claim, b"")
        status = "PASSED" if result.passed else "FAILED"
        print(f"entailment_verified: {status} — {result.reason}")
        if result.passed:
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
        "taint-report": cmd_taint_report,
    }
    fn = dispatch.get(subcmd)
    if fn is None:
        print(f"unknown claim subcommand: {subcmd!r}", file=sys.stderr)
        sys.exit(1)
    rc = fn(args)
    if rc:
        sys.exit(rc)
