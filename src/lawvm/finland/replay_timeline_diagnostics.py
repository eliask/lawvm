"""Opt-in timeline invariant diagnostics for Finland replay."""

from __future__ import annotations

import os
from datetime import date
from typing import TYPE_CHECKING, Callable, Optional, Union

from lawvm.core.invariant_profiles import ReplayInvariantProfile
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode
    from lawvm.core.timeline import Timelines

PitDateInput = Union[str, date, None]

_TIMELINE_INVARIANTS_ON = frozenset({"1", "true", "on"})
_TIMELINE_INVARIANTS_OFF = frozenset({"0", "false", "off"})


def fi_timeline_invariants_opt_in_enabled() -> bool:
    """True when replay should run timeline invariant families on product projection."""
    return os.environ.get("LAWVM_FI_ENABLE_TIMELINE_INVARIANTS", "") in _TIMELINE_INVARIANTS_ON


def fi_bench_timeline_invariants_enabled(*, diagnostic_replay: bool) -> bool:
    """Bench policy: diagnostic replay enables timeline invariants unless explicitly off."""
    if fi_timeline_invariants_opt_in_enabled():
        return True
    if not diagnostic_replay:
        return False
    return os.environ.get("LAWVM_FI_BENCH_TIMELINE_INVARIANTS", "1") not in _TIMELINE_INVARIANTS_OFF


def _pit_date_iso(pit_date: PitDateInput) -> str:
    """Normalize replay PIT input to the ISO string timeline checks expect."""
    if pit_date is None:
        return date.today().isoformat()
    if isinstance(pit_date, date):
        return pit_date.isoformat()
    text = str(pit_date).strip()
    if not text:
        return date.today().isoformat()
    return text


def project_timeline_invariant_findings(
    *,
    ir: "IRNode",
    timelines: Optional["Timelines"],
    pit_date: PitDateInput,
    profile: ReplayInvariantProfile,
    replay_findings: list[Finding],
    replay_meta_out: dict[str, object] | None,
    replay_print: Callable[[str], None],
    source_statute: str = "",
) -> None:
    """Run timeline invariant families declared on *profile* (opt-in only)."""
    if not profile.timeline_invariants or timelines is None:
        return

    from lawvm.core.timeline_invariants import check_all_timeline_invariants_typed

    pit_iso = _pit_date_iso(pit_date)
    violations = check_all_timeline_invariants_typed(ir, timelines, pit_iso)
    if not violations:
        return

    if replay_meta_out is not None:
        replay_meta_out["timeline_invariant_violations"] = [
            {
                "kind": violation.kind,
                "code": violation.kind,
                "address": violation.address_path,
                "section_label": violation.section_label,
                "message": violation.message,
                "pit_date": pit_iso,
                **dict(violation.detail),
            }
            for violation in violations
        ]

    seen = {
        (
            finding.kind,
            str(finding.detail.get("code") or ""),
            str(finding.detail.get("address") or ""),
        )
        for finding in replay_findings
        if finding.kind == "timeline_invariant_violation"
    }
    for violation in violations:
        code = str(violation.kind)
        address = str(violation.address_path)
        replay_print(f"WARNING timeline invariant: {code} {address}")
        key = ("timeline_invariant_violation", code, address)
        if key in seen:
            continue
        replay_findings.append(
            Finding(
                kind="timeline_invariant_violation",
                role=OBSERVATION_ROLE,
                stage="timeline_invariants",
                blocking=False,
                source_statute=source_statute,
                detail={
                    "message": "Replay timeline invariant violated.",
                    "phase": "replay",
                    "code": code,
                    "kind": code,
                    "address": address,
                    "section_label": violation.section_label,
                    "pit_date": pit_iso,
                    "violation_message": violation.message,
                    **dict(violation.detail),
                },
            )
        )
        seen.add(key)


__all__ = [
    "fi_bench_timeline_invariants_enabled",
    "fi_timeline_invariants_opt_in_enabled",
    "project_timeline_invariant_findings",
]
