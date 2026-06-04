"""ClaimRetractionTaintReport — Slice 5.

§7 (post-consumption error handling) + §5.2 of UNIFIED_MANUAL_CLAIMS_DESIGN.md v2.2.

When an accepted claim is retracted after being consumed by one or more builds:
  1. Scan event log for event_kind='consumed' events with this claim_id.
  2. Enumerate affected builds + row hashes + invalidated PIT intervals.
  3. Emit ClaimRetractionTaintReport to
     data/fi/v1/manual_claims/claim_taint_reports/<BUILD_ID>/retracted_<CLAIM_ID>.json
  4. Old artifacts stay on disk (history). New builds at strict profile refuse
     retracted claims (enforced in export_fi_refs.py).

invalidated_PIT_intervals (§A7 from skeptic v2):
  Row hashes alone are insufficient for a temporal compiler. Intervals tell
  consumers WHICH points-in-time of WHICH targets are now suspect.
  For INLINE_STATUTE_RESOLUTION with valid_at=(start, None): the interval is
  (start, None) on the target's PIT timeline — open-ended forward from that date.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional, Tuple


@dataclass(frozen=True, slots=True)
class InvalidatedPITInterval:
    """One provision's PIT interval that is now suspect due to retraction."""

    target_locator: str
    """provision_ref or statute_id — identifies the target whose PIT is suspect."""
    interval_start: date
    interval_end: Optional[date]
    """None = open-ended (the claim was valid indefinitely from interval_start)."""


@dataclass(frozen=True, slots=True)
class AffectedBuild:
    """One build that consumed the retracted claim."""

    build_id: str
    profile: str
    """ProfileTag.value, e.g. 'strict_with_attested_claims'."""
    projection_artifact_path: str
    affected_projection_row_hashes: Tuple[str, ...]
    invalidated_PIT_intervals: Tuple[InvalidatedPITInterval, ...]
    dependent_downstream_artifacts: Tuple[str, ...]
    """Composed reports / build-index-db files that JOINed against affected rows."""


@dataclass(frozen=True, slots=True)
class ClaimRetractionTaintReport:
    """Full taint report for one retracted claim."""

    retracted_claim_id: str
    retraction_event_id: str
    """Stable identifier for the retraction event (claim_id + retraction_timestamp ISO)."""
    retraction_timestamp: datetime
    retraction_reason: str
    affected_builds: Tuple[AffectedBuild, ...]


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _date_or_none(v: object) -> Optional[str]:
    if v is None:
        return None
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def _interval_to_dict(iv: InvalidatedPITInterval) -> dict:
    return {
        "target_locator": iv.target_locator,
        "interval_start": iv.interval_start.isoformat(),
        "interval_end": _date_or_none(iv.interval_end),
    }


def _affected_build_to_dict(ab: AffectedBuild) -> dict:
    return {
        "build_id": ab.build_id,
        "profile": ab.profile,
        "projection_artifact_path": ab.projection_artifact_path,
        "affected_projection_row_hashes": list(ab.affected_projection_row_hashes),
        "invalidated_PIT_intervals": [_interval_to_dict(iv) for iv in ab.invalidated_PIT_intervals],
        "dependent_downstream_artifacts": list(ab.dependent_downstream_artifacts),
    }


def report_to_dict(report: ClaimRetractionTaintReport) -> dict:
    return {
        "retracted_claim_id": report.retracted_claim_id,
        "retraction_event_id": report.retraction_event_id,
        "retraction_timestamp": report.retraction_timestamp.isoformat(),
        "retraction_reason": report.retraction_reason,
        "affected_builds": [_affected_build_to_dict(ab) for ab in report.affected_builds],
    }


# ---------------------------------------------------------------------------
# Deserialization
# ---------------------------------------------------------------------------


def _dict_to_interval(d: dict) -> InvalidatedPITInterval:
    end_raw = d.get("interval_end")
    return InvalidatedPITInterval(
        target_locator=d["target_locator"],
        interval_start=date.fromisoformat(d["interval_start"]),
        interval_end=date.fromisoformat(end_raw) if end_raw else None,
    )


def _dict_to_affected_build(d: dict) -> AffectedBuild:
    return AffectedBuild(
        build_id=d["build_id"],
        profile=d["profile"],
        projection_artifact_path=d["projection_artifact_path"],
        affected_projection_row_hashes=tuple(d.get("affected_projection_row_hashes", [])),
        invalidated_PIT_intervals=tuple(
            _dict_to_interval(iv) for iv in d.get("invalidated_PIT_intervals", [])
        ),
        dependent_downstream_artifacts=tuple(d.get("dependent_downstream_artifacts", [])),
    )


def report_from_dict(d: dict) -> ClaimRetractionTaintReport:
    return ClaimRetractionTaintReport(
        retracted_claim_id=d["retracted_claim_id"],
        retraction_event_id=d["retraction_event_id"],
        retraction_timestamp=datetime.fromisoformat(d["retraction_timestamp"]),
        retraction_reason=d["retraction_reason"],
        affected_builds=tuple(_dict_to_affected_build(ab) for ab in d.get("affected_builds", [])),
    )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def write_taint_report(report: ClaimRetractionTaintReport, taint_reports_dir: Path) -> Path:
    """Write taint report to taint_reports_dir/<BUILD_ID>/retracted_<CLAIM_ID>.json.

    If multiple builds are affected, the report is written once per build_id
    subdirectory (with the same content).
    Returns the path of the last-written file (or the single-build path).
    """
    last_path: Optional[Path] = None
    for ab in report.affected_builds:
        build_dir = taint_reports_dir / ab.build_id
        build_dir.mkdir(parents=True, exist_ok=True)
        path = build_dir / f"retracted_{report.retracted_claim_id}.json"
        path.write_text(
            json.dumps(report_to_dict(report), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        last_path = path

    if last_path is None:
        # No affected builds — write to a top-level location anyway for audit trail
        taint_reports_dir.mkdir(parents=True, exist_ok=True)
        last_path = taint_reports_dir / f"retracted_{report.retracted_claim_id}.json"
        last_path.write_text(
            json.dumps(report_to_dict(report), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    return last_path


def read_taint_report(path: Path) -> ClaimRetractionTaintReport:
    return report_from_dict(json.loads(path.read_text(encoding="utf-8")))


def find_taint_reports_for_claim(
    taint_reports_dir: Path,
    claim_id: str,
) -> Tuple[Path, ...]:
    """Find all taint report files for a given claim_id across all build subdirs."""
    if not taint_reports_dir.exists():
        return ()
    results = []
    filename = f"retracted_{claim_id}.json"
    for child in taint_reports_dir.iterdir():
        candidate = child / filename
        if candidate.exists():
            results.append(candidate)
    top = taint_reports_dir / filename
    if top.exists():
        results.append(top)
    return tuple(sorted(results))


def find_taint_reports_for_build(
    taint_reports_dir: Path,
    build_id: str,
) -> Tuple[Path, ...]:
    """Find all taint report files under taint_reports_dir/<build_id>/."""
    build_dir = taint_reports_dir / build_id
    if not build_dir.exists():
        return ()
    return tuple(sorted(p for p in build_dir.iterdir() if p.suffix == ".json"))


def list_all_taint_reports(taint_reports_dir: Path) -> Tuple[Path, ...]:
    """Find all taint report JSON files across all build subdirs."""
    if not taint_reports_dir.exists():
        return ()
    results = []
    for child in taint_reports_dir.iterdir():
        if child.is_dir():
            results.extend(p for p in child.iterdir() if p.suffix == ".json")
        elif child.suffix == ".json":
            results.append(child)
    return tuple(sorted(results))
