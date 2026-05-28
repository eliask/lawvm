"""lawvm diagnose-phase — attribute a structural violation to its first bad phase.

For one statute and one amendment, materializes intermediate state products
at each pipeline phase and runs a structural detector on each.

Phase sequence checked:
  before_state         — replay-fold tree immediately before amendment A
  direct_applied       — tree produced by applying amendment A to before_state
                         via process_muutoslaki (raw, before post-process)
  replay_fold_after_A  — replay-fold tree after processing amendment A
                         (post-dedup, post-sort — via replay_xml stopped after A)
  materialized_after_A — PIT-materialized tree after processing amendment A

The first phase where the detector fires is the first_bad_phase.

Phase attribution:
  before_state bad       → current amendment is not the introducer
  direct_applied bad     → violation introduced in apply/compile phase
  replay_fold_after bad  → fold post-process step introduced or concealed
  materialized_after bad → timeline materialization seam
  all clean              → no violation detected at this amendment

Detectors:
  duplicate_label          duplicate (kind, label) among siblings
  illegal_edge             impossible parent→child nesting
  all_tree                 all check_invariants violations (covers both above)
  text_duplication         large duplicated text blocks (lint-level)
  flattened_sublist_family repeated letter/roman/digit families suggesting nested
                           sublists were merged into one flat list (lint-level)

Usage:
    lawvm diagnose-phase 1995/398 --source 2013/982
    lawvm diagnose-phase 1995/398 --source 2013/982 --target chapter:4/section:20
    lawvm diagnose-phase 1995/398 --source 2013/982 --detector illegal_edge
    lawvm diagnose-phase 1995/398 --source 2013/982 --mode finlex_oracle --json
    lawvm diagnose-phase 1995/398 --source 2013/982 --certificate
    lawvm diagnose-phase 1995/398 --source 2013/982 --certificate \\
        --first-bad-amendment 2013/982
"""

from __future__ import annotations

import contextlib
import io
import json
from typing import Any, Dict, List, Literal, Optional, Tuple


# ---------------------------------------------------------------------------
# Detector helpers
# ---------------------------------------------------------------------------

def _run_tree_detector(
    ir: Any,
    detector: str,
    target_path: str = "",
) -> List[str]:
    """Run the named structural detector on an IRNode tree.

    Returns a list of violation strings.  If *target_path* is given, only
    violations whose path contains the target components are returned.
    """
    from lawvm.core.invariant_detectors import run_invariant_detector_messages

    return run_invariant_detector_messages(ir, detector, target_path)


def _phase_result(
    ir: Any,
    detector: str,
    target_path: str,
    cap: int = 20,
) -> Dict[str, Any]:
    violations = _run_tree_detector(ir, detector, target_path)
    return {
        "clean": len(violations) == 0,
        "violation_count": len(violations),
        "violations": violations[:cap],
    }


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

def _attribute(
    first_bad_phase: Optional[str],
) -> Tuple[str, str]:
    """Return (confidence, explanation) for the given first_bad_phase."""
    if first_bad_phase is None:
        return "high", "all phases clean — no violation detected at this amendment"
    if first_bad_phase == "before_state":
        return "definite", (
            "before_state already bad — this amendment is not the introducer; "
            "use invariant-bisect to find an earlier amendment"
        )
    if first_bad_phase == "direct_applied_state":
        return "high", (
            "direct_applied_state is first bad — violation introduced in the "
            "apply/compile phase for this amendment"
        )
    if first_bad_phase == "replay_fold_after_A":
        return "medium", (
            "replay_fold_after_A is first bad but direct_applied_state is clean — "
            "deduplication or post-fold step is involved; "
            "compare direct_applied vs fold trees"
        )
    if first_bad_phase == "materialized_after_A":
        return "high", (
            "materialized_after_A is first bad; fold clean — "
            "violation introduced in timeline materialization"
        )
    return "underdetermined", f"unrecognised first_bad_phase: {first_bad_phase!r}"


# ---------------------------------------------------------------------------
# Main bundle builder
# ---------------------------------------------------------------------------

def build_diagnose_phase_bundle(
    statute_id: str,
    source_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    target_path: str = "",
    detector: str = "duplicate_label",
) -> Dict[str, Any]:
    """Produce a phase-by-phase diagnostic bundle for one statute+amendment."""
    from lawvm.finland.grafter import (
        get_corpus,
        process_muutoslaki,
        _resolve_applicable_amendment_records,
        replay_xml,
    )
    from lawvm.finland.statute import StatuteContext
    from lawvm.finland.helpers import _fi_label_postprocessor

    cs = get_corpus()

    xml_bytes = cs.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"statute not found in corpus: {statute_id!r}")

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    records, _cutoff, _oracle_version = _resolve_applicable_amendment_records(statute_id, mode)
    amendment_ids = [str(r["statute_id"]) for r in records]

    if source_id not in amendment_ids:
        raise SystemExit(
            f"amendment {source_id!r} not in replay chain for {statute_id!r}"
        )

    source_idx = amendment_ids.index(source_id)

    # ------------------------------------------------------------------
    # Phase 1: before_state
    # Full replay_xml stopped before source_id.
    # This state has been through dedup/sort/post-process — it is the
    # authoritative "clean baseline" for diagnosis.
    # ------------------------------------------------------------------
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        before_master = replay_xml(statute_id, mode=mode, stop_before=source_id, quiet=True)
    before_state = before_master.replay_fold_state
    before_result = _phase_result(before_state.ir, detector, target_path)

    # ------------------------------------------------------------------
    # Phase 2: direct_applied_state
    # Apply only amendment A via process_muutoslaki to before_state.
    # This is the "raw" fold result before post-process steps.
    # ------------------------------------------------------------------
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        pm = process_muutoslaki(
            source_id,
            before_state,
            ctx,
            replay_mode=mode,
            parent_id=statute_id,
            corpus=cs,
        )
    direct_applied_state = pm.output
    direct_result = _phase_result(direct_applied_state.ir, detector, target_path)

    # ------------------------------------------------------------------
    # Phases 3 + 4: replay_fold_after_A and materialized_after_A
    # Full replay_xml stopped before the amendment that follows source_id.
    # This gives us the post-dedup/sort fold state and the PIT-materialized
    # state after processing source_id.
    # ------------------------------------------------------------------
    next_mid = amendment_ids[source_idx + 1] if source_idx + 1 < len(amendment_ids) else ""
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
    ):
        if next_mid:
            after_master = replay_xml(statute_id, mode=mode, stop_before=next_mid, quiet=True)
        else:
            after_master = replay_xml(statute_id, mode=mode, quiet=True)

    fold_result = _phase_result(after_master.replay_fold_state.ir, detector, target_path)
    materialized_result = _phase_result(after_master.state.ir, detector, target_path)

    # ------------------------------------------------------------------
    # Identify first bad phase
    # ------------------------------------------------------------------
    phase_sequence = [
        ("before_state", before_result),
        ("direct_applied_state", direct_result),
        ("replay_fold_after_A", fold_result),
        ("materialized_after_A", materialized_result),
    ]

    first_bad_phase: Optional[str] = None
    for phase_name, result in phase_sequence:
        if not result["clean"]:
            first_bad_phase = phase_name
            break

    confidence, explanation = _attribute(first_bad_phase)

    return {
        "statute_id": statute_id,
        "source_id": source_id,
        "mode": mode,
        "target_path": target_path or "(all)",
        "detector": detector,
        "amendment_index": source_idx,
        "total_amendments": len(amendment_ids),
        "phases": {
            "before_state": before_result,
            "direct_applied_state": direct_result,
            "replay_fold_after_A": fold_result,
            "materialized_after_A": materialized_result,
        },
        "first_bad_phase": first_bad_phase or "none",
        "confidence": confidence,
        "explanation": explanation,
    }


# ---------------------------------------------------------------------------
# Certificate builder (Phase 4 / spec section 11)
# ---------------------------------------------------------------------------

def build_certificate(
    bundle: Dict[str, Any],
    first_bad_amendment: str = "",
) -> Dict[str, Any]:
    """Build a compact machine-readable certificate from a phase-diagnosis bundle.

    The certificate is suitable for manual review ledger inputs and handoff notes.

    Parameters
    ----------
    bundle:
        Output of build_diagnose_phase_bundle.
    first_bad_amendment:
        Optional pre-computed first-bad-amendment ID (from invariant-bisect).
        When provided, included verbatim.  When absent, the certificate records
        the amendment that was diagnosed (source_id) as the candidate.
    """
    phases = bundle["phases"]
    evidence: List[str] = []
    for phase_name, result in phases.items():
        if result["clean"]:
            evidence.append(f"{phase_name} clean")
        else:
            count = result["violation_count"]
            evidence.append(f"{phase_name} bad ({count} violation(s))")
            # Add the first violation as a witness line
            for v in result["violations"][:1]:
                evidence.append(f"  witness: {v}")

    # Normalise first_bad_phase name for the certificate (use short form)
    _phase_short = {
        "before_state": "before_state",
        "direct_applied_state": "apply",
        "replay_fold_after_A": "fold",
        "materialized_after_A": "materialization",
        "none": "none",
    }
    raw_phase = bundle["first_bad_phase"]
    phase_short = _phase_short.get(raw_phase, raw_phase)

    return {
        "statute_id": bundle["statute_id"],
        "target": bundle["target_path"],
        "detector": bundle["detector"],
        "first_bad_amendment": first_bad_amendment or bundle["source_id"],
        "first_bad_phase": phase_short,
        "confidence": bundle["confidence"],
        "evidence": evidence,
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

_PHASE_LABELS = {
    "before_state": "before_state        ",
    "direct_applied_state": "direct_applied      ",
    "replay_fold_after_A": "replay_fold_after_A ",
    "materialized_after_A": "materialized_after_A",
}

_VIOLATION_CAP_DISPLAY = 5


def _format_text(bundle: Dict[str, Any]) -> str:
    lines = [
        f"Statute    : {bundle['statute_id']}",
        f"Amendment  : {bundle['source_id']}  "
        f"(#{bundle['amendment_index'] + 1} / {bundle['total_amendments']})",
        f"Mode       : {bundle['mode']}",
        f"Target     : {bundle['target_path']}",
        f"Detector   : {bundle['detector']}",
        "",
        f"First bad phase : {bundle['first_bad_phase']}",
        f"Confidence      : {bundle['confidence']}",
        f"Explanation     : {bundle['explanation']}",
        "",
        "Phase results:",
    ]
    for phase_name, result in bundle["phases"].items():
        label = _PHASE_LABELS.get(phase_name, phase_name)
        status = "clean" if result["clean"] else f"BAD  ({result['violation_count']} violation(s))"
        lines.append(f"  {label}  {status}")
        for v in result.get("violations", [])[:_VIOLATION_CAP_DISPLAY]:
            lines.append(f"    {v}")
        remaining = result["violation_count"] - _VIOLATION_CAP_DISPLAY
        if remaining > 0:
            lines.append(f"    ... ({remaining} more — use --json for full list)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    bundle = build_diagnose_phase_bundle(
        statute_id=args.statute_id,
        source_id=args.source,
        mode=getattr(args, "mode", "legal_pit"),
        target_path=getattr(args, "target", "") or "",
        detector=getattr(args, "detector", "duplicate_label") or "duplicate_label",
    )
    if getattr(args, "certificate", False):
        cert = build_certificate(
            bundle,
            first_bad_amendment=getattr(args, "first_bad_amendment", "") or "",
        )
        print(json.dumps(cert, ensure_ascii=False, indent=2))
        return
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle))
