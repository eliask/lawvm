"""lawvm invariant-bisect — find the first amendment that introduces a structural violation.

Scans the amendment chain for one statute, applying each amendment cumulatively
via process_muutoslaki.  After each step, runs the selected structural detector
and records whether the result is clean or bad.

Reports:
  first_bad_amendment   — source_id where the detector first fires
  first_clean_amendment — source_id immediately preceding the first bad
  monotone_failure      — once bad, stays bad through the remaining chain
  transient_failure     — fires for some amendments but clears later
  failure_count         — number of steps in the scan window where detector fires

Uses the lightweight process_muutoslaki loop (same as lawvm bisect-section)
rather than a full replay_xml per step.  For statutes with chapter-seeding or
repeal pre-scanning the results may differ slightly from a full replay, but
for duplicate_label and illegal_edge detectors the difference is usually
negligible.

The scan window can be bounded with --after / --before to focus on a suspected
region of the amendment chain.

Detectors:
  duplicate_label        duplicate (kind, label) among siblings
  illegal_edge           impossible parent→child nesting
  all_tree               all check_invariants violations (covers both above)
  text_duplication       large duplicated text blocks (lint-level)
  flattened_sublist_family repeated letter/roman/digit families suggesting
                           nested sublists were merged into one flat list

Usage:
    lawvm invariant-bisect 1995/398
    lawvm invariant-bisect 1995/398 --target chapter:4/section:20
    lawvm invariant-bisect 1995/398 --detector illegal_edge
    lawvm invariant-bisect 1995/398 --after 2010/100 --before 2015/200
    lawvm invariant-bisect 1995/398 --mode finlex_oracle
    lawvm invariant-bisect 1995/398 --json
    lawvm invariant-bisect 1995/398 --verbose
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from lawvm.core.invariant_detectors import run_invariant_detector_messages

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_UK_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"


# ---------------------------------------------------------------------------
# UK invariant bisect
# ---------------------------------------------------------------------------

def build_uk_invariant_bisect_bundle(
    statute_id: str,
    target_path: str = "",
    detector: str = "duplicate_label",
    after_mid: str = "",
    before_mid: str = "",
    db_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Scan the UK amendment chain for *statute_id* and find the first bad amendment.

    Compiles all ops for *statute_id*, groups them by source amendment (in
    pipeline sequence order), then applies each group cumulatively, running
    *detector* after each group.  Reports the first amendment whose group
    introduces a structural violation.

    The ``after_mid`` / ``before_mid`` window operates on source amendment IDs
    (i.e. ``op.source.statute_id`` values in pipeline order).

    Parameters
    ----------
    statute_id:
        Target statute, e.g. "ukpga/1978/30".
    target_path:
        Optional structural path filter; only violations whose path contains
        this segment are reported.
    detector:
        Which structural detector to run.
    after_mid:
        Start scan after this amendment ID (exclusive).
    before_mid:
        Stop scan before this amendment ID (exclusive).
    db_path:
        Path to uk_legislation.farchive; defaults to the repo default.
    """
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module

    resolved_db = db_path if db_path is not None else _DEFAULT_UK_DB
    if not resolved_db.exists():
        raise SystemExit(f"UK archive not found at {resolved_db}")

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(resolved_db) as archive:
        # 1. Load enacted base IR
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            raise SystemExit(
                f"enacted XML missing from archive for {statute_id!r}: {enacted_url}"
            )
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        # 2. Load oracle EID map (for oracle alignment during apply_ops)
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        eid_map: dict[str, str] = {}
        text_map: dict[str, str] = {}
        if oracle_bytes is not None:
            oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
            eid_map = oracle_data.get("eid_map", {})
            text_map = oracle_data.get("text_map", {})

        # 3. Compile all ops
        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        all_ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

    # 4. Build ordered amendment list (stable partition preserving pipeline order)
    amendment_ids: list[str] = []
    seen_mid: set[str] = set()
    for op in all_ops:
        mid = op.source.statute_id if op.source is not None else "__no_source__"
        if mid not in seen_mid:
            amendment_ids.append(mid)
            seen_mid.add(mid)

    # Group ops by source amendment (list of lists, in amendment_ids order)
    groups: dict[str, list] = {mid: [] for mid in amendment_ids}
    for op in all_ops:
        mid = op.source.statute_id if op.source is not None else "__no_source__"
        groups[mid].append(op)

    # 5. Resolve scan window
    if after_mid:
        if after_mid in amendment_ids:
            start_idx = amendment_ids.index(after_mid) + 1
        else:
            raise SystemExit(
                f"--after amendment {after_mid!r} not found in pipeline op order for {statute_id!r}"
            )
    else:
        start_idx = 0

    if before_mid:
        if before_mid in amendment_ids:
            end_idx = amendment_ids.index(before_mid)
        else:
            raise SystemExit(
                f"--before amendment {before_mid!r} not found in pipeline op order for {statute_id!r}"
            )
    else:
        end_idx = len(amendment_ids)

    # 6. Build running IR up to start of scan window (apply pre-window ops in one shot)
    pre_window_mids = amendment_ids[:start_idx]
    pre_window_ops = [op for mid in pre_window_mids for op in groups[mid]]

    current_ir = pipeline.apply_ops(
        base_ir,
        pre_window_ops,
        eid_map=eid_map,
        text_map=text_map,
        allow_oracle_alignment=True,
    )

    # Check pre-window state
    initial_violations = run_invariant_detector_messages(current_ir.body, detector, target_path)
    initial_clean = len(initial_violations) == 0

    # 7. Scan window: apply one amendment group at a time
    scan_ids = amendment_ids[start_idx:end_idx]
    steps: List[Dict[str, Any]] = []

    for mid in scan_ids:
        group_ops = groups[mid]
        if group_ops:
            current_ir = pipeline.apply_ops(
                current_ir,
                group_ops,
                eid_map=eid_map,
                text_map=text_map,
                allow_oracle_alignment=True,
            )
        violations = run_invariant_detector_messages(current_ir.body, detector, target_path)
        steps.append({
            "source_id": mid,
            "clean": len(violations) == 0,
            "violation_count": len(violations),
            "violations": violations[:10],
        })

    # 8. Classify (same logic as Finland path)
    first_bad_idx_raw: Optional[int] = next(
        (i for i, s in enumerate(steps) if not s["clean"]), None
    )

    if not initial_clean:
        first_clean_in_window = next((i for i, s in enumerate(steps) if s["clean"]), None)
        if first_clean_in_window is not None:
            re_bad_idx = next(
                (i for i, s in enumerate(steps) if i > first_clean_in_window and not s["clean"]),
                None,
            )
            first_bad_idx: Optional[int] = re_bad_idx
        else:
            first_bad_idx = None
    else:
        first_bad_idx = first_bad_idx_raw

    first_bad = steps[first_bad_idx] if first_bad_idx is not None else None

    if first_bad_idx is not None:
        if first_bad_idx > 0:
            first_clean_amendment = steps[first_bad_idx - 1]["source_id"]
        elif after_mid:
            first_clean_amendment = after_mid
        else:
            first_clean_amendment = ""
    elif not initial_clean and first_bad_idx_raw == 0:
        first_clean_amendment = ""
    else:
        first_clean_amendment = scan_ids[-1] if scan_ids else ""

    monotone_failure = False
    transient_failure = False
    if first_bad_idx_raw is not None:
        post_bad_clean_flags = [s["clean"] for s in steps[first_bad_idx_raw:]]
        if all(not c for c in post_bad_clean_flags):
            monotone_failure = True
        else:
            transient_failure = True
    elif not initial_clean:
        monotone_failure = True

    return {
        "statute_id": statute_id,
        "jurisdiction": "uk",
        "target_path": target_path or "(all)",
        "detector": detector,
        "scan_window": {
            "after": after_mid or "",
            "before": before_mid or "",
            "count": len(scan_ids),
            "total_in_chain": len(amendment_ids),
        },
        "initial_clean": initial_clean,
        "initial_violations": initial_violations[:10] if not initial_clean else [],
        "first_bad_amendment": first_bad["source_id"] if first_bad else "",
        "first_clean_amendment": first_clean_amendment,
        "monotone_failure": monotone_failure,
        "transient_failure": transient_failure,
        "failure_count": sum(1 for s in steps if not s["clean"]),
        "total_scanned": len(steps),
        "first_bad_violations": (
            first_bad["violations"] if first_bad else initial_violations[:10]
        ),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def build_invariant_bisect_bundle(
    statute_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    target_path: str = "",
    detector: str = "duplicate_label",
    after_mid: str = "",
    before_mid: str = "",
) -> Dict[str, Any]:
    """Scan the amendment chain of statute_id and find the first bad amendment.

    Parameters
    ----------
    statute_id:
        Parent statute identifier, e.g. "1995/398".
    mode:
        Replay mode, "legal_pit" or "finlex_oracle".
    target_path:
        Optional structural path filter, e.g. "chapter:4/section:20".
        Only violations whose path contains this segment are considered.
    detector:
        Which detector to run: "duplicate_label", "illegal_edge",
        "all_tree", or "text_duplication".
    after_mid:
        Start scan after this amendment ID (exclusive).  Amendments up to
        and including after_mid are applied silently to reach the correct
        starting state.
    before_mid:
        Stop scan before this amendment ID (exclusive).
    """
    from lawvm.finland.grafter import (
        get_corpus,
        process_muutoslaki,
        _resolve_applicable_amendment_records,
    )
    from lawvm.finland.statute import ReplayState, StatuteContext
    from lawvm.finland.helpers import _fi_label_postprocessor

    cs = get_corpus()
    xml_bytes = cs.read_source(statute_id)
    if xml_bytes is None:
        raise SystemExit(f"statute not found in corpus: {statute_id!r}")

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    records, cutoff_date, _oracle_version = _resolve_applicable_amendment_records(statute_id, mode)
    amendment_ids = [str(r["statute_id"]) for r in records]

    # Resolve scan window bounds
    if after_mid:
        if after_mid in amendment_ids:
            start_idx = amendment_ids.index(after_mid) + 1
        else:
            raise SystemExit(f"--after amendment {after_mid!r} not in chain for {statute_id!r}")
    else:
        start_idx = 0

    if before_mid:
        if before_mid in amendment_ids:
            end_idx = amendment_ids.index(before_mid)
        else:
            raise SystemExit(f"--before amendment {before_mid!r} not in chain for {statute_id!r}")
    else:
        end_idx = len(amendment_ids)

    # Build fold state up to start of scan window
    state = ReplayState(ir=ctx.base_ir)
    for mid in amendment_ids[:start_idx]:
        state = process_muutoslaki(
            mid, state, ctx,
            replay_mode=mode, parent_id=statute_id, corpus=cs,
        ).output

    # Check state before scan window
    initial_violations = run_invariant_detector_messages(state.ir, detector, target_path)
    initial_clean = len(initial_violations) == 0

    # Scan window
    scan_ids = amendment_ids[start_idx:end_idx]
    steps: List[Dict[str, Any]] = []

    for mid in scan_ids:
        state = process_muutoslaki(
            mid, state, ctx,
            replay_mode=mode, parent_id=statute_id, corpus=cs,
        ).output
        violations = run_invariant_detector_messages(state.ir, detector, target_path)
        steps.append({
            "source_id": mid,
            "clean": len(violations) == 0,
            "violation_count": len(violations),
            "violations": violations[:10],
        })

    # Find first bad step where state transitioned from clean.
    # If the pre-window state was already bad, we look for the first step where
    # the state NEWLY becomes bad after a clean step — i.e. a real state flip.
    # If the pre-window was bad and step 0 is also bad, no amendment in the
    # window introduced the violation: attribute to the pre-window state.

    # Find first index where steps go bad (regardless of initial state)
    first_bad_idx_raw: Optional[int] = next(
        (i for i, s in enumerate(steps) if not s["clean"]), None
    )

    if not initial_clean:
        # Pre-window state already bad.  Only report a first_bad_amendment if
        # we see a clean→bad transition INSIDE the window (meaning an early
        # amendment in the window temporarily fixed the violation and a later
        # one re-introduced it).
        first_clean_in_window = next((i for i, s in enumerate(steps) if s["clean"]), None)
        if first_clean_in_window is not None:
            # There was a clean period inside the window.
            re_bad_idx = next(
                (i for i, s in enumerate(steps) if i > first_clean_in_window and not s["clean"]),
                None,
            )
            first_bad_idx: Optional[int] = re_bad_idx
        else:
            # All steps bad and pre-window was bad: violation predates the window.
            first_bad_idx = None
    else:
        first_bad_idx = first_bad_idx_raw

    first_bad = steps[first_bad_idx] if first_bad_idx is not None else None

    # first_clean_amendment = last clean step before the first bad
    if first_bad_idx is not None:
        if first_bad_idx > 0:
            first_clean_amendment = steps[first_bad_idx - 1]["source_id"]
        elif after_mid:
            first_clean_amendment = after_mid
        else:
            first_clean_amendment = ""
    elif not initial_clean and first_bad_idx_raw == 0:
        # Violation predates window and never cleared.
        first_clean_amendment = ""
    else:
        # All clean — report the last amendment scanned
        first_clean_amendment = scan_ids[-1] if scan_ids else ""

    # Monotone / transient classification (over the whole scan window)
    monotone_failure = False
    transient_failure = False
    if first_bad_idx_raw is not None:
        post_bad_clean_flags = [s["clean"] for s in steps[first_bad_idx_raw:]]
        if all(not c for c in post_bad_clean_flags):
            monotone_failure = True
        else:
            transient_failure = True
    elif not initial_clean:
        # All steps bad because base was bad.
        monotone_failure = True

    return {
        "statute_id": statute_id,
        "mode": mode,
        "target_path": target_path or "(all)",
        "detector": detector,
        "scan_window": {
            "after": after_mid or "",
            "before": before_mid or "",
            "count": len(scan_ids),
            "total_in_chain": len(amendment_ids),
        },
        "initial_clean": initial_clean,
        "initial_violations": initial_violations[:10] if not initial_clean else [],
        "first_bad_amendment": first_bad["source_id"] if first_bad else "",
        "first_clean_amendment": first_clean_amendment,
        "monotone_failure": monotone_failure,
        "transient_failure": transient_failure,
        "failure_count": sum(1 for s in steps if not s["clean"]),
        "total_scanned": len(steps),
        "first_bad_violations": (
            first_bad["violations"] if first_bad else initial_violations[:10]
        ),
        "steps": steps,
    }


# ---------------------------------------------------------------------------
# Text formatter
# ---------------------------------------------------------------------------

def _format_text(bundle: Dict[str, Any], verbose: bool = False) -> str:
    jurisdiction = str(bundle.get("jurisdiction") or "fi")
    header = [
        f"Statute    : {bundle['statute_id']}",
    ]
    if jurisdiction == "uk":
        header.append("Jurisdiction: uk")
    else:
        header.append(f"Mode       : {bundle.get('mode', 'legal_pit')}")
    header += [
        f"Target     : {bundle['target_path']}",
        f"Detector   : {bundle['detector']}",
        f"Scan window: {bundle['scan_window']['count']} amendments "
        f"(of {bundle['scan_window']['total_in_chain']} total)",
        "",
    ]
    lines = header

    first_bad = bundle["first_bad_amendment"]

    if not bundle["initial_clean"] and not first_bad:
        # Violation predates the scan window and persists across all scanned steps.
        lines.append("Pre-window state already bad — violation predates this scan window.")
        lines.append("Run without --after to scan from the base statute, or use")
        lines.append("diagnose-phase on the earliest amendment in the chain.")
        lines.append("")
        lines.append("Pre-window violations:")
        for v in bundle["initial_violations"][:8]:
            lines.append(f"  {v}")
        lines.append(
            f"Failure count: {bundle['failure_count']} / {bundle['total_scanned']} "
            f"({'monotone' if bundle['monotone_failure'] else 'transient'})"
        )
    elif not bundle["initial_clean"] and first_bad:
        # Pre-window bad, but there was a clean period inside the window before
        # the re-introduction.
        lines.append(
            "NOTE: tree had violations before scan window, but a clean period "
            "inside the window was found."
        )
        for v in bundle["initial_violations"][:3]:
            lines.append(f"  (pre-window) {v}")
        lines.append("")
        lines.append(f"First re-introduced bad amendment : {first_bad}")
        lines.append(f"Last clean before re-introduction : {bundle['first_clean_amendment'] or '(none)'}")
        lines.append(f"Failure type                      : {'monotone' if bundle['monotone_failure'] else 'transient'}")
        lines.append(f"Failure count                     : {bundle['failure_count']} / {bundle['total_scanned']}")
        lines.append("")
        lines.append("First bad violations (at re-introduction):")
        for v in bundle["first_bad_violations"][:8]:
            lines.append(f"  {v}")
        remaining = len(bundle["first_bad_violations"]) - 8
        if remaining > 0:
            lines.append(f"  ... ({remaining} more — use --json)")
    elif first_bad:
        lines.append(f"First bad amendment  : {first_bad}")
        lines.append(f"First clean before   : {bundle['first_clean_amendment'] or '(none — bad from start)'}")
        lines.append(f"Failure type         : {'monotone' if bundle['monotone_failure'] else 'transient'}")
        lines.append(f"Failure count        : {bundle['failure_count']} / {bundle['total_scanned']}")
        lines.append("")
        lines.append("First bad violations:")
        for v in bundle["first_bad_violations"][:8]:
            lines.append(f"  {v}")
        remaining = len(bundle["first_bad_violations"]) - 8
        if remaining > 0:
            lines.append(f"  ... ({remaining} more — use --json)")
    else:
        lines.append(
            f"No violations found across {bundle['total_scanned']} scanned amendments."
        )

    if verbose and bundle["steps"]:
        lines.append("")
        lines.append("Per-amendment results:")
        for step in bundle["steps"]:
            status = "clean" if step["clean"] else f"BAD ({step['violation_count']})"
            lines.append(f"  {step['source_id']}  {status}")
            if not step["clean"]:
                for v in step["violations"][:3]:
                    lines.append(f"    {v}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    jurisdiction = getattr(args, "jurisdiction", "fi") or "fi"
    if jurisdiction == "uk":
        bundle = build_uk_invariant_bisect_bundle(
            statute_id=args.statute_id,
            target_path=getattr(args, "target", "") or "",
            detector=getattr(args, "detector", "duplicate_label") or "duplicate_label",
            after_mid=getattr(args, "after", "") or "",
            before_mid=getattr(args, "before", "") or "",
        )
    elif jurisdiction == "fi":
        bundle = build_invariant_bisect_bundle(
            statute_id=args.statute_id,
            mode=getattr(args, "mode", "legal_pit"),
            target_path=getattr(args, "target", "") or "",
            detector=getattr(args, "detector", "duplicate_label") or "duplicate_label",
            after_mid=getattr(args, "after", "") or "",
            before_mid=getattr(args, "before", "") or "",
        )
    else:
        import sys
        print(
            f"ERROR: invariant-bisect does not yet support -j {jurisdiction}",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if getattr(args, "json", False):
        print(json.dumps(bundle, ensure_ascii=False, indent=2, default=str))
        return
    print(_format_text(bundle, verbose=getattr(args, "verbose", False)))
