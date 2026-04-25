"""lawvm verify — pipeline invariant checker.

Runs well-formedness checks at each pipeline stage. Like LLVM's verifier
that runs after every pass — catches bugs at the stage that introduced them,
not three stages later when the score drops.

Current implementation operates on the XML-level pipeline (pre-Phase 2 IR
migration). Post-migration these checks will be refactored to use IR types.

Invariants checked:
  PARSE:        tree well-formed, sections have nums, no duplicate sibling labels
  EXTRACT:      ops have valid verbs/kinds/targets (AmendmentOp-level)
  APPLY:        tree still well-formed after each amendment (no duplicates,
                no leaked omission nodes, no orphan sections)
  OBSERVATIONS: PhaseResult observation kinds are registered; temporal_events
                are present when executable temporal authority exists

Usage:
    lawvm verify <statute_id>                         # full pipeline check
    lawvm verify <statute_id> --stage parse           # base statute only
    lawvm verify <statute_id> --stage extract --source <amendment_id>
    lawvm verify <statute_id> --stage apply           # check after every amendment
    lawvm verify <statute_id> --stage observations    # check PhaseResult signals
"""
from __future__ import annotations

import contextlib
import io
import re
import sys
from dataclasses import dataclass
from typing import List, Literal, Optional, cast

from lxml import etree

from lawvm.finland.grafter import (
    get_corpus,
    _resolve_applicable_amendment_records,
    _normalize_johtolause_verbs,
    get_johtolause,
    parse_ops_fallback_heuristic,
    process_muutoslaki,
    OP_KEYWORDS,
)
from lawvm.finland.fallback_op_ids import stamp_fallback_op_ids
from lawvm.finland.statute import StatuteContext, ReplayState
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.core.observation_registry import finding_codes_by_role
from lawvm.core.effect_intent import (
    Commencement, Expiry, Suspension, Applicability, Revival,
)
from lawvm.core.compile_facade import CompileFacade
from lawvm.core.compile_result import (
    CanonicalBundle,
)
from lawvm.core.temporal import TemporalEvent
from lawvm.core.compile_views import (
    quirks_used_from_findings,
    source_completeness_issues_from_findings,
)

_EFFECT_INTENT_TYPES = (Commencement, Expiry, Suspension, Applicability, Revival)


# ---------------------------------------------------------------------------
# Issue type
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    stage: str
    severity: str   # "error" | "warning"
    code: str
    message: str
    context: str = ""  # amendment id, path, etc.


def _issue(stage, severity, code, message, context="") -> Issue:
    return Issue(stage=stage, severity=severity, code=code,
                 message=message, context=context)


def _build_verify_facade(
    *,
    replay_mode: Literal["finlex_oracle", "legal_pit"],
    structural_ops: List,
    phase_results: List,
) -> CompileFacade:
    """Build a bundle-aware facade for verifier replay output.

    The verifier already has both the structural replay ops and the phase-level
    findings/temporal signals. Build a facade directly from those planes so
    later timeline/PIT checks do not silently drop executable temporal events.
    """
    finding_ledger_list = []
    for phase_result in phase_results:
        for finding in phase_result.findings():
            if finding in finding_ledger_list:
                continue
            finding_ledger_list.append(finding)
    finding_ledger = tuple(finding_ledger_list)
    temporal_events = tuple(
        event
        for phase_result in phase_results
        for event in phase_result.temporal_events
    )
    migration_events = tuple(
        event
        for phase_result in phase_results
        for event in phase_result.migration_events
    )
    return CompileFacade(
        bundle=CanonicalBundle(
            structural_ops=tuple(structural_ops),
            temporal_events=temporal_events,
            migration_events=migration_events,
        ),
        finding_ledger=finding_ledger,
        replay_mode=replay_mode,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _norm_label(s: str) -> str:
    """Normalize a section label for comparison: strip §, spaces."""
    return re.sub(r'[\s§]', '', s).lower()


# ---------------------------------------------------------------------------
# PARSE stage checks (XML tree)
# ---------------------------------------------------------------------------

def check_parse(tree: etree._Element, context: str = "") -> List[Issue]:
    """Check base statute XML well-formedness."""
    issues: List[Issue] = []

    body = tree.find(".//{*}body")
    if body is None:
        issues.append(_issue("parse", "error", "parse.no_body",
                             "no <body> element found", context))
        return issues

    # Check every section
    all_sections = cast(List[etree._Element], body.xpath(".//*[local-name()='section']"))
    if not all_sections:
        issues.append(_issue("parse", "warning", "parse.no_sections",
                             "no <section> elements found in body", context))

    for sec in all_sections:
        num = _num_text(sec)
        if not num:
            issues.append(_issue("parse", "warning", "parse.section_missing_num",
                                 "section has no <num> element", context))

    # Check for duplicate sibling section labels under each parent
    for parent in cast(List[etree._Element], body.xpath(".//*[local-name()='chapter'] | .//*[local-name()='part'] "
                             "| .//*[local-name()='body']")):
        seen: dict[str, int] = {}
        for sec in parent:
            if _tag(sec) != "section":
                continue
            num = _num_text(sec)
            if not num:
                continue
            key = _norm_label(num)
            seen[key] = seen.get(key, 0) + 1
        for key, count in seen.items():
            if count > 1:
                parent_num = _num_text(parent) or _tag(parent)
                issues.append(_issue("parse", "error", "parse.duplicate_section_label",
                                     f"section {key!r} appears {count}x under {parent_num}",
                                     context))

    # Check for empty chapters (no section children)
    for ch in cast(List[etree._Element], body.xpath(".//*[local-name()='chapter']")):
        secs = cast(List[etree._Element], ch.xpath(".//*[local-name()='section']"))
        if not secs:
            num = _num_text(ch) or "(unlabelled)"
            issues.append(_issue("parse", "warning", "parse.empty_chapter",
                                 f"chapter {num} has no sections", context))

    return issues


# ---------------------------------------------------------------------------
# EXTRACT stage checks (AmendmentOp list)
# ---------------------------------------------------------------------------

VALID_OP_TYPES = {"REPLACE", "REPEAL", "INSERT", "RENUMBER"}
VALID_TARGET_KINDS = {"P", "L", "O"}  # section, chapter, part (N/A handled separately)


def check_extract(ops, context: str = "") -> List[Issue]:
    """Check AmendmentOp list from the EXTRACT stage.

    ops may be AmendmentOp (from grafter.parse_ops) or ParsedOp (from peg.py).
    We check whichever fields are present.
    """
    issues: List[Issue] = []

    if not ops:
        return issues

    for i, op in enumerate(ops):
        # AmendmentOp fields
        op_type = getattr(op, "op_type", None)
        target_kind = getattr(op, "target_kind", None)
        target_section = getattr(op, "target_section", None)
        target_special = getattr(op, "target_special", None)

        if op_type is not None:
            # AmendmentOp
            if op_type not in VALID_OP_TYPES:
                issues.append(_issue("extract", "error", "extract.invalid_op_type",
                                     f"op[{i}] has invalid op_type {op_type!r}", context))
            if target_kind not in VALID_TARGET_KINDS:
                issues.append(_issue("extract", "error", "extract.invalid_target_kind",
                                     f"op[{i}] has invalid target_kind {target_kind!r}", context))
            if not target_section and not target_special:
                issues.append(_issue("extract", "error", "extract.empty_target",
                                     f"op[{i}] has empty target_section and no target_special "
                                     f"(op_type={op_type})", context))
        else:
            # ParsedOp or unknown structure — check code() if available
            code = getattr(op, "code", None)
            if callable(code):
                code_str = code()
                parts = code_str.split()
                if len(parts) < 2:
                    issues.append(_issue("extract", "warning", "extract.short_op_code",
                                         f"op[{i}] code {code_str!r} has fewer than 2 parts",
                                         context))

    return issues


# ---------------------------------------------------------------------------
# APPLY stage checks (XML tree after amendment)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Verify: PARSE only
# ---------------------------------------------------------------------------

def verify_parse(sid: str) -> List[Issue]:
    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: statute {sid} not in zip", file=sys.stderr)
        sys.exit(1)

    tree = etree.fromstring(xml_bytes)
    return check_parse(tree, context=sid)


# ---------------------------------------------------------------------------
# Verify: EXTRACT only (one amendment)
# ---------------------------------------------------------------------------

def verify_extract(sid: str, source_amendment_id: str) -> List[Issue]:
    cs = get_corpus()
    xml_bytes = cs.read_source(source_amendment_id)
    if xml_bytes is None:
        print(f"ERROR: amendment {source_amendment_id} not in zip", file=sys.stderr)
        sys.exit(1)

    johto = get_johtolause(xml_bytes)
    if not johto:
        return []
    johto = _normalize_johtolause_verbs(johto)
    if not any(kw in johto.lower() for kw in OP_KEYWORDS):
        return []

    ops = stamp_fallback_op_ids(parse_ops_fallback_heuristic(johto), source_amendment_id)
    return check_extract(ops or [], context=source_amendment_id)


# ---------------------------------------------------------------------------
# Verify: full pipeline (PARSE + EXTRACT per amendment + APPLY per amendment)
# ---------------------------------------------------------------------------

def _verify_full(sid: str, mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle") -> List[Issue]:
    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: statute {sid} not in zip", file=sys.stderr)
        sys.exit(1)

    all_issues: List[Issue] = []

    # PARSE: base statute
    tree = etree.fromstring(xml_bytes)
    parse_issues = check_parse(tree, context=f"{sid}/base")
    all_issues.extend(parse_issues)

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    amendment_records, _, _ = _resolve_applicable_amendment_records(sid, mode)
    n = len(amendment_records)

    lo_ops_out: List = []
    phase_results: List = []

    for i, rec in enumerate(amendment_records, start=1):
        amendment_id = str(rec["statute_id"])

        # EXTRACT: check ops for this amendment
        ext_issues = verify_extract(sid, amendment_id)
        all_issues.extend(ext_issues)

        # APPLY: apply amendment, then check tree invariants on IR
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            phase_result = process_muutoslaki(
                amendment_id, state, ctx, replay_mode=mode, parent_id=sid,
                lo_ops_out=lo_ops_out,
            )
        phase_results.append(phase_result)
        state = phase_result.output
        from lawvm.core.tree_ops import check_invariants
        for violation in check_invariants(state.ir):
            all_issues.append(_issue(
                "apply", "error", "apply.tree_invariant",
                f"{violation} (after {amendment_id} [{i}/{n}])",
                f"{sid} after {amendment_id}",
            ))

    # TIMELINE: compile timelines from lo_ops_out and check temporal invariants
    if lo_ops_out:
        from lawvm.core.ir import IRStatute
        from lawvm.core.timeline_invariants import check_all_timeline_invariants

        base_ir = IRStatute(
            statute_id=sid,
            title=ctx.title,
            body=ctx.base_ir,
        )
        facade = _build_verify_facade(
            replay_mode=mode,
            structural_ops=lo_ops_out,
            phase_results=phase_results,
        )
        compiled_timelines = facade.compile_timelines_ex(base_ir)
        pit_date = "9999-12-31"  # TODO: differentiate by mode when needed
        materialized = facade.materialize_pit_ex(base_ir, pit_date)
        pit = materialized.statute
        if compiled_timelines.issues:
            for issue in compiled_timelines.issues:
                all_issues.append(_issue(
                    "timeline", "warning", f"timeline.{issue.kind}",
                    issue.message,
                    sid,
                ))
        if materialized.is_degraded:
            all_issues.append(_issue(
                "timeline", "warning", "timeline.degraded_missing_scope",
                (
                    "timeline/PIT materialization required missing scope: "
                    + ", ".join(materialized.required_dimensions)
                ),
                sid,
            ))

        for violation in check_all_timeline_invariants(pit, compiled_timelines.timelines, pit_date):
            all_issues.append(_issue(
                "timeline", "warning", "timeline.invariant",
                violation,
                sid,
            ))

    return all_issues


def verify_full(sid: str, mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle") -> List[Issue]:
    return _verify_full(sid, mode)


# ---------------------------------------------------------------------------
# Verify: OBSERVATIONS stage (PhaseResult signal validation)
# ---------------------------------------------------------------------------

def verify_observations(
    sid: str,
    mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    *,
    show_facade: bool = False,
) -> List[Issue]:
    """Validate PhaseResult finding kinds and temporal events for each amendment.

    For every amendment in the statute's chain:
      - Collect all observation-role Findings from the PhaseResult returned by
        process_muutoslaki.
      - Warn on any finding.kind not registered as an observation-role code.
      - Collect all temporal_events from the PhaseResult.
      - Error on any temporal_event that is not a TemporalEvent instance.

    Emits an INFO summary line with totals.

    If show_facade is True, builds a CompileFacade from the accumulated PhaseResults
    and prints a summary (observations, temporal_events, quirks, source_completeness).
    """
    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        print(f"ERROR: statute {sid} not in zip", file=sys.stderr)
        sys.exit(1)

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    amendment_records, _, _ = _resolve_applicable_amendment_records(sid, mode)

    all_issues: List[Issue] = []
    total_obs = 0
    distinct_kinds: set = set()
    total_unregistered = 0
    total_temporal_events = 0
    registered_observation_kinds = set(finding_codes_by_role("observation"))

    # Accumulate all PhaseResults for facade construction when requested.
    all_phase_results = [] if show_facade else None

    for rec in amendment_records:
        amendment_id = str(rec["statute_id"])
        with (
            contextlib.redirect_stdout(io.StringIO()),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            phase_result = process_muutoslaki(
                amendment_id, state, ctx, replay_mode=mode, parent_id=sid
            )
        state = phase_result.output

        if all_phase_results is not None:
            all_phase_results.append(phase_result)

        for finding in phase_result.findings():
            if finding.role != "observation":
                continue
            total_obs += 1
            distinct_kinds.add(finding.kind)
            if finding.kind not in registered_observation_kinds:
                total_unregistered += 1
                all_issues.append(_issue(
                    "observations", "warning",
                    "observations.unregistered_kind",
                    f"observation kind {finding.kind!r} is not in the observation-role registry "
                    f"(stage={finding.stage!r})",
                    f"{sid} after {amendment_id}",
                ))

        for event in phase_result.temporal_events:
            total_temporal_events += 1
            if not isinstance(event, TemporalEvent):
                all_issues.append(_issue(
                    "observations", "error",
                    "observations.invalid_temporal_event",
                    f"temporal_event {event!r} is not a recognised TemporalEvent "
                    f"(got {type(event).__name__})",
                    f"{sid} after {amendment_id}",
                ))

    all_issues.append(_issue(
        "observations", "info",
        "observations.summary",
        (
            f"{total_obs} observation(s) checked, "
            f"{len(distinct_kinds)} distinct kind(s), "
            f"{total_unregistered} unregistered, "
            f"{total_temporal_events} temporal_event(s)"
        ),
        sid,
    ))

    if show_facade and all_phase_results is not None:
        _print_facade_from_phase_results(all_phase_results, sid=sid, replay_mode=mode)

    return all_issues


def _print_facade_from_phase_results(
    phase_results: List,
    *,
    sid: str,
    replay_mode: Literal["finlex_oracle", "legal_pit"],
) -> None:
    """Merge a list of PhaseResults and print a CompileFacade summary.

    Called from verify_observations when --facade is active.
    """
    from lawvm.core.compile_facade import CompileFacade
    from lawvm.core.phase_result import PhaseResult

    if not phase_results:
        # No amendments — build an empty facade
        empty_pr = PhaseResult(output=None)
        facade = CompileFacade.from_phase_result(empty_pr, replay_mode=replay_mode)
    else:
        # Merge all phase results: accumulate findings plus transitional parse
        # rails / executable temporal events.
        merged = phase_results[0]
        for pr in phase_results[1:]:
            merged = merged.merge(pr)
        facade = CompileFacade.from_phase_result(merged, replay_mode=replay_mode)

    pass_label = "YES" if not facade.has_blocking else "NO"
    quirks = tuple(quirks_used_from_findings(facade.finding_ledger))
    source_completeness = tuple(
        source_completeness_issues_from_findings(facade.finding_ledger)
    )
    findings = len(getattr(facade, "finding_ledger", ()))
    bundle = getattr(facade, "bundle", None)
    temporal_events = len(getattr(bundle, "temporal_events", ()))
    quirks_used = len(quirks)
    source_completeness_issues = len(source_completeness)
    print()
    print(
        f"CompileFacade ({sid}) : strict={pass_label}"
        f"  findings={findings}"
        f"  temporal_events={temporal_events}"
        f"  quirks_used={quirks_used}"
        f"  source_completeness_issues={source_completeness_issues}"
    )
    if quirks_used:
        print(f"  Quirks       : {', '.join(sorted({str(item.kind) for item in quirks}))}")
    if source_completeness_issues:
        print(f"  SC issues    : {', '.join(sorted({str(item.kind) for item in source_completeness}))}")
    obligations = [
        finding
        for finding in getattr(facade, "finding_ledger", ())
        if getattr(finding, "role", "") == "obligation"
    ]
    if obligations:
        print(
            f"  Obligations  : {len(obligations)} "
            f"({sum(1 for finding in obligations if getattr(finding, 'blocking', False))} blocking)  "
            f"kinds: {', '.join(sorted({str(getattr(finding, 'kind', '')) for finding in obligations if str(getattr(finding, 'kind', ''))}))}"
        )
    print()


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _report(issues: List[Issue], sid: str, stage: Optional[str]) -> int:
    errors = [i for i in issues if i.severity == "error"]
    warnings = [i for i in issues if i.severity == "warning"]
    infos = [i for i in issues if i.severity == "info"]

    stage_label = f" (stage: {stage})" if stage else ""
    print(f"lawvm verify {sid}{stage_label}")
    print(f"  {len(errors)} error(s), {len(warnings)} warning(s), {len(infos)} info(s)")
    print()

    for issue in issues:
        if issue.severity == "error":
            marker = "ERROR"
        elif issue.severity == "warning":
            marker = "WARN "
        else:
            marker = "INFO "
        ctx = f"  [{issue.context}]" if issue.context else ""
        print(f"  {marker}  [{issue.code}] {issue.message}{ctx}")

    if not issues:
        print("  OK — no issues found")

    return 1 if errors else 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args) -> None:
    sid = args.statute_id
    stage = getattr(args, "stage", None)
    source = getattr(args, "source", None)
    mode = getattr(args, "mode", "finlex_oracle")
    show_facade = getattr(args, "facade", False)

    if stage == "parse":
        issues = verify_parse(sid)
        rc = _report(issues, sid, "parse")

    elif stage == "extract":
        if not source:
            print("ERROR: --stage extract requires --source <amendment_id>", file=sys.stderr)
            sys.exit(1)
        issues = verify_extract(sid, source)
        rc = _report(issues, sid, f"extract/{source}")

    elif stage == "observations":
        issues = verify_observations(sid, mode, show_facade=show_facade)
        rc = _report(issues, sid, "observations")

    else:
        # Full pipeline (default or --stage apply)
        issues = verify_full(sid, mode)
        rc = _report(issues, sid, stage)

    sys.exit(rc)
