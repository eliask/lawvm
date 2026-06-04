"""Tests for Slice 5 retraction lifecycle + taint reports.

Mandatory acceptance criteria:
  10. test_consumption_event_logged_per_consumed_claim
  11. test_retraction_emits_taint_report
  12. test_retraction_lists_multiple_affected_builds
  13. test_invalidated_PIT_intervals_present_in_report
  14. test_strict_rebuild_refuses_retracted_claim (actually: strict build properly
      observes retraction — retracted claims leave NULL slots as NULL)
  15. test_taint_report_cli_renders
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

import lawvm.finland.claim_kinds  # noqa: F401

from lawvm.core.manual_claims.hashing import compute_claim_id
from lawvm.core.manual_claims.primitive import (
    ClaimConfidence,
    ClaimLayer,
    ClaimScope,
    ClaimState,
    ClaimStateEvent,
    ClaimStatus,
    ManualCompilationClaim,
    Producer,
    ProfileTag,
    ReviewStatus,
    SourceLocator,
    SourceWitnessType,
    ValidatorStatus,
)
from lawvm.core.manual_claims.storage import ClaimStore


def _make_producer() -> Producer:
    return Producer(
        producer_kind="operator",
        handle="test",
        model_id=None,
        timestamp=datetime(2026, 6, 4, 12, 0, 0, tzinfo=timezone.utc),
        environment="test",
    )


def _make_args(**kwargs):
    class _Args:
        pass
    a = _Args()
    for k, v in kwargs.items():
        setattr(a, k, v)
    return a


def _make_and_accept_claim(
    store: ClaimStore,
    statute_id: str = "711/2022",
    citation_form: str = "lain 1234/2020",
    resolved_statute_id: str = "1234/2020",
) -> str:
    """Create, file, and accept an INLINE_STATUTE_RESOLUTION claim. Returns claim_id."""
    source_bytes = citation_form.encode()
    partial = ManualCompilationClaim(
        claim_id="placeholder",
        schema_version="v1",
        jurisdiction="fi",
        claim_kind="fi.v1.INLINE_STATUTE_RESOLUTION",
        claim_layer=ClaimLayer.EXTRACTION,
        claim_scope=ClaimScope(
            statute_id=statute_id,
            provision_ref="section:3",
            valid_at_start=date(2020, 1, 1),
            valid_at_end=None,
        ),
        target=(
            ("statute_id", statute_id),
            ("section_locator", "section:3"),
            ("mention_span", (0, len(source_bytes))),
        ),
        value=(
            ("resolved_statute_id", resolved_statute_id),
            ("citation_form", citation_form),
        ),
        source_witness_type=SourceWitnessType.OPERATOR_FILING,
        producer=_make_producer(),
        cited_source_locator=SourceLocator(
            artifact_kind="finlex_akn",
            statute_id=statute_id,
            he_id=None,
            version_id=None,
        ),
        cited_source_span=(0, len(source_bytes)),
        cited_source_hash=hashlib.sha256(source_bytes).hexdigest(),
        dependency_fingerprint=(("target_hash", "abc"),),
        valid_at=(date(2020, 1, 1), None),
        supersedes=(),
        supersession_delta_reason=None,
        disputes=(),
        requested_profiles=(ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,),
        rationale="lifecycle test claim",
    )
    claim_id = compute_claim_id(partial)
    claim = ManualCompilationClaim(
        claim_id=claim_id,
        **{k: getattr(partial, k) for k in partial.__dataclass_fields__ if k != "claim_id"},
    )

    store.ensure_dirs()
    store.write_claim(claim)
    store.write_by_kind(claim)
    now = datetime.now(tz=timezone.utc)
    producer = _make_producer()

    store.append_event(ClaimStateEvent(
        claim_id=claim_id, event_kind="proposed", timestamp=now,
        producer=producer, old_status=None, new_status="proposed", reason="test",
    ))
    store.append_event(ClaimStateEvent(
        claim_id=claim_id, event_kind="accepted", timestamp=now,
        producer=producer, old_status="proposed", new_status="accepted", reason="test accept",
    ))
    store.write_state(ClaimState(
        claim_id=claim_id,
        status=ClaimStatus.ACCEPTED,
        review_status=ReviewStatus.HUMAN_REVIEWED,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=now,
    ))
    return claim_id


def _emit_consumed_event(
    store: ClaimStore,
    claim_id: str,
    build_id: str,
    profile: str = "strict_with_attested_claims",
    projection_path: str = "/data/fi_refs.parquet",
    row_hashes: List[str] = None,
) -> None:
    """Emit a consumed event for a claim (simulates export_fi_refs calling track_consumption)."""
    claim = store.read_claim(claim_id)
    valid_at = claim.valid_at

    reason = json.dumps({
        "build_id": build_id,
        "profile": profile,
        "projection_artifact_path": projection_path,
        "row_hashes": row_hashes or ["rowhashabc123"],
        "invalidated_PIT_intervals": [
            {
                "target_locator": claim.claim_scope.provision_ref or claim.claim_scope.statute_id,
                "interval_start": valid_at[0].isoformat(),
                "interval_end": valid_at[1].isoformat() if valid_at[1] else None,
            }
        ],
        "dependent_downstream_artifacts": [],
    })

    now = datetime.now(tz=timezone.utc)
    producer = Producer(
        producer_kind="tool",
        handle=None,
        model_id=None,
        timestamp=now,
        environment="test",
    )

    store.append_event(ClaimStateEvent(
        claim_id=claim_id,
        event_kind="consumed",
        timestamp=now,
        producer=producer,
        old_status=None,
        new_status=None,
        reason=reason,
    ))


# ---------------------------------------------------------------------------
# Test 10: consumption event logged per consumed claim
# ---------------------------------------------------------------------------


def test_consumption_event_logged_per_consumed_claim(tmp_path: Path):
    """After export_fi_refs consumes a claim, event log contains a 'consumed' event."""
    from lawvm.tools.export_fi_refs import track_consumption_for_build

    claims_dir = tmp_path / "manual_claims"
    store = ClaimStore(claims_dir)
    claim_id = _make_and_accept_claim(store)

    track_consumption_for_build(
        build_id="build-test-001",
        profile=ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        projection_artifact_path="/data/fi_refs__strict_with_attested_claims.parquet",
        consumed_claim_ids=[claim_id],
        affected_projection_rows=[{"target_statute_id": "1234/2020", "source_statute_id": "711/2022"}],
        claims_base_dir=claims_dir,
    )

    events = list(store.read_events(claim_id))
    consumed_events = [e for e in events if e.event_kind == "consumed"]
    assert len(consumed_events) == 1

    payload = json.loads(consumed_events[0].reason)
    assert payload["build_id"] == "build-test-001"
    assert payload["profile"] == "strict_with_attested_claims"


# ---------------------------------------------------------------------------
# Test 11: retraction emits taint report
# ---------------------------------------------------------------------------


def test_retraction_emits_taint_report(tmp_path: Path):
    """Claim accepted → consumed → retracted. Taint report file exists with build_id + row hashes."""
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)

    # Simulate consumption
    _emit_consumed_event(store, claim_id, "build-abc-001", row_hashes=["row_hash_xyz"])

    # Retract via CLI
    from lawvm.tools.cmd_claim import cmd_retract
    args = _make_args(
        claim_id=claim_id,
        reason="bad claim detected",
        data_dir=str(tmp_path),
    )
    rc = cmd_retract(args)
    assert rc == 0

    # Check taint report was created
    taint_dir = tmp_path / "manual_claims" / "claim_taint_reports"
    from lawvm.core.manual_claims.taint_report import find_taint_reports_for_claim, read_taint_report
    paths = find_taint_reports_for_claim(taint_dir, claim_id)
    assert len(paths) >= 1, "Taint report file must be created after retraction"

    report = read_taint_report(paths[0])
    assert report.retracted_claim_id == claim_id
    assert len(report.affected_builds) == 1
    ab = report.affected_builds[0]
    assert ab.build_id == "build-abc-001"
    assert "row_hash_xyz" in ab.affected_projection_row_hashes


# ---------------------------------------------------------------------------
# Test 12: retraction lists multiple affected builds
# ---------------------------------------------------------------------------


def test_retraction_lists_multiple_affected_builds(tmp_path: Path):
    """Claim consumed by 2 builds; retraction enumerates both."""
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)

    _emit_consumed_event(store, claim_id, "build-001", projection_path="/data/build001.parquet")
    _emit_consumed_event(store, claim_id, "build-002", projection_path="/data/build002.parquet")

    from lawvm.tools.cmd_claim import cmd_retract
    args = _make_args(
        claim_id=claim_id,
        reason="multi-build retraction test",
        data_dir=str(tmp_path),
    )
    rc = cmd_retract(args)
    assert rc == 0

    taint_dir = tmp_path / "manual_claims" / "claim_taint_reports"
    from lawvm.core.manual_claims.taint_report import find_taint_reports_for_claim, read_taint_report
    paths = find_taint_reports_for_claim(taint_dir, claim_id)
    assert paths

    report = read_taint_report(paths[0])
    build_ids = {ab.build_id for ab in report.affected_builds}
    assert "build-001" in build_ids
    assert "build-002" in build_ids


# ---------------------------------------------------------------------------
# Test 13: invalidated PIT intervals present in report
# ---------------------------------------------------------------------------


def test_invalidated_PIT_intervals_present_in_report(tmp_path: Path):
    """Claim with valid_at=(2020-01-01, None) retracted; report contains interval (2020-01-01, None)."""
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)  # valid_at=(2020-01-01, None)

    _emit_consumed_event(store, claim_id, "build-pit-001")

    from lawvm.tools.cmd_claim import cmd_retract
    args = _make_args(claim_id=claim_id, reason="PIT interval test", data_dir=str(tmp_path))
    cmd_retract(args)

    taint_dir = tmp_path / "manual_claims" / "claim_taint_reports"
    from lawvm.core.manual_claims.taint_report import find_taint_reports_for_claim, read_taint_report
    paths = find_taint_reports_for_claim(taint_dir, claim_id)
    assert paths

    report = read_taint_report(paths[0])
    assert len(report.affected_builds) >= 1
    ab = report.affected_builds[0]
    assert len(ab.invalidated_PIT_intervals) >= 1
    iv = ab.invalidated_PIT_intervals[0]
    assert iv.interval_start == date(2020, 1, 1)
    assert iv.interval_end is None  # open-ended


# ---------------------------------------------------------------------------
# Test 14: strict rebuild observes retracted claim (slot remains NULL)
# ---------------------------------------------------------------------------


def test_strict_rebuild_refuses_retracted_claim(tmp_path: Path):
    """After retraction, export_fi_refs in strict mode does NOT use the retracted claim.

    Spec §5.4: new builds at strict profile refuse to incorporate retracted claims.
    Since the retracted claim is no longer in ClaimStatus.ACCEPTED,
    _load_accepted_inline_statute_claims won't load it, and the NULL slot stays NULL.

    This test verifies _check_no_retracted_claims_in_strict raises for explicitly
    constructed retracted state.
    """
    from lawvm.tools.export_fi_refs import _check_no_retracted_claims_in_strict
    from lawvm.core.manual_claims.primitive import ClaimStatus

    # Build a (claim, state) pair where state.status = RETRACTED
    # (simulates what _load_accepted_inline_statute_claims might return if
    # the filter was removed — we test the guard function directly)
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)

    # Manually transition to retracted state
    now = datetime.now(tz=timezone.utc)
    producer = _make_producer()
    store.append_event(ClaimStateEvent(
        claim_id=claim_id,
        event_kind="retracted",
        timestamp=now,
        producer=producer,
        old_status="accepted",
        new_status="retracted",
        reason="test retraction",
    ))
    store.write_state(ClaimState(
        claim_id=claim_id,
        status=ClaimStatus.RETRACTED,
        review_status=ReviewStatus.HUMAN_REVIEWED,
        validator_status=ValidatorStatus.SPAN_VERIFIED,
        confidence=ClaimConfidence.HIGH,
        last_updated=now,
    ))

    claim = store.read_claim(claim_id)
    state = store.read_state(claim_id)

    # _check_no_retracted_claims_in_strict should raise SystemExit for strict profile
    with pytest.raises(SystemExit):
        _check_no_retracted_claims_in_strict(
            [(claim, state)],
            ProfileTag.STRICT_WITH_ATTESTED_CLAIMS,
        )

    # For non-strict profile, no error
    _check_no_retracted_claims_in_strict(
        [(claim, state)],
        ProfileTag.NON_STRICT_WITH_CLAIMS,
    )  # must not raise


# ---------------------------------------------------------------------------
# Test 15: taint-report CLI renders
# ---------------------------------------------------------------------------


def test_taint_report_cli_renders(tmp_path: Path, capsys):
    """lawvm claim taint-report CLAIM_ID + --list + --build BUILD_ID all render."""
    store = ClaimStore(tmp_path / "manual_claims")
    claim_id = _make_and_accept_claim(store)
    _emit_consumed_event(store, claim_id, "build-cli-001")

    # Retract to create taint report
    from lawvm.tools.cmd_claim import cmd_retract
    cmd_retract(_make_args(
        claim_id=claim_id,
        reason="CLI render test",
        data_dir=str(tmp_path),
    ))

    # Test: taint-report CLAIM_ID
    from lawvm.tools.cmd_claim import cmd_taint_report
    rc = cmd_taint_report(_make_args(
        claim_id=claim_id,
        list=False,
        build=None,
        data_dir=str(tmp_path),
    ))
    assert rc == 0
    captured = capsys.readouterr()
    assert claim_id in captured.out

    # Test: taint-report --list
    rc = cmd_taint_report(_make_args(
        claim_id=None,
        list=True,
        build=None,
        data_dir=str(tmp_path),
    ))
    assert rc == 0
    captured = capsys.readouterr()
    # Should show count or claim_id prefix
    assert len(captured.out) > 0

    # Test: taint-report --build BUILD_ID
    rc = cmd_taint_report(_make_args(
        claim_id=None,
        list=False,
        build="build-cli-001",
        data_dir=str(tmp_path),
    ))
    assert rc == 0
    captured = capsys.readouterr()
    assert "build-cli-001" in captured.out or claim_id[:10] in captured.out
