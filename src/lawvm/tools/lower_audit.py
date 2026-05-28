"""lawvm lower-audit — verify lowering pipeline preserves semantic information.

Audits the ParsedOp -> Finland LegalOperation lowering pipeline, checking that
actions and targets are preserved at each step.

Loss modes detected:
  1. Action collapse: verb M/K/L/S not mapping to replace/repeal/insert/renumber
  2. Target collapse: section/subsection/item not preserved in LegalAddress
  3. Facet loss: otsikko/johdantokappale facet dropped
  4. Count mismatch: different number of ParsedOps vs LegalOps

Usage:
    lawvm lower-audit <statute_id>
    lawvm lower-audit <statute_id> --source 2017/794
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from lawvm.core.semantic_types import FacetKind


# ---------------------------------------------------------------------------
# Action mapping: ParsedOp.verb -> expected LegalOperation.action
# ---------------------------------------------------------------------------

_VERB_TO_EXPECTED_ACTION = {
    "M": {"replace", "heading_replace"},
    "K": {"repeal"},
    "L": {"insert"},
    "S": {"renumber"},
}


# ---------------------------------------------------------------------------
# Audit result
# ---------------------------------------------------------------------------

@dataclass
class LoweringAuditResult:
    """Result of auditing one amendment's lowering pipeline."""
    amendment_id: str
    parsed_op_count: int
    legal_op_count: int
    actions_preserved: int = 0
    targets_preserved: int = 0
    actions_lost: List[str] = field(default_factory=list)
    targets_lost: List[str] = field(default_factory=list)

    @property
    def losses(self) -> int:
        return len(self.actions_lost) + len(self.targets_lost)

    @property
    def valid(self) -> bool:
        return self.losses == 0


# ---------------------------------------------------------------------------
# Target comparison helpers
# ---------------------------------------------------------------------------

def _parsed_op_to_expected_path(op) -> list[tuple[str, str]]:
    """Build expected LegalAddress path components from a ParsedOp."""
    path: list[tuple[str, str]] = []
    if op.part:
        path.append(("part", op.part))
    if op.chapter:
        path.append(("chapter", op.chapter))
    if op.kind == "P":
        path.append(("section", op.number))
        if op.momentti:
            path.append(("subsection", str(op.momentti)))
            if op.item:
                path.append(("item", op.item))
    elif op.kind == "L":
        path.append(("chapter", op.number))
    elif op.kind == "O":
        path.append(("part", op.number))
    elif op.kind == "N":
        path.append(("nimike", op.number))
    elif op.kind == "A":
        path.append(("appendix", op.number))
    return path


def _parsed_op_expected_special(op) -> Optional[str]:
    """Return expected LegalAddress.special from ParsedOp.facet or ParsedOp.special.

    Checks facet first (typed enum), falls back to legacy special string.
    """
    # Prefer the typed facet field if set
    if op.facet is not None:
        if op.facet == FacetKind.HEADING:
            return "heading"
        elif op.facet == FacetKind.INTRO:
            return "intro"
        # Other non-None facets: fall through to special
    if not op.special:
        return None
    return {"o": "heading", "j": "intro"}.get(op.special[0], op.special)


def _compare_target(parsed_op, legal_op) -> Optional[str]:
    """Compare a ParsedOp's target fields against a LegalOperation's target.

    Returns None if they match, or a description of the mismatch.
    """
    expected_path = tuple(_parsed_op_to_expected_path(parsed_op))
    actual_path = legal_op.target.path
    expected_special = _parsed_op_expected_special(parsed_op)
    actual_special = legal_op.target.special
    if isinstance(actual_special, FacetKind):
        actual_special = actual_special.value

    mismatches = []
    if expected_path != actual_path:
        mismatches.append(f"path: expected {expected_path}, got {actual_path}")
    if expected_special != actual_special:
        mismatches.append(f"special: expected {expected_special!r}, got {actual_special!r}")

    if mismatches:
        return f"op {parsed_op.code()}: " + "; ".join(mismatches)
    return None


# ---------------------------------------------------------------------------
# Core audit function
# ---------------------------------------------------------------------------

def audit_lowering_preservation(
    johto_text: str,
    amendment_id: str = "",
) -> LoweringAuditResult:
    """Audit that lowering pipeline preserves semantic information.

    Pipeline:
      1. parse_clause(text) -> parsed_ops (from ClauseParseResult)
      2. extract_legal_ops(text) -> Finland legal_ops
      3. Compare count, actions, targets
    """
    from lawvm.finland.johtolause import extract_legal_ops, parse_clause

    parse_result = parse_clause(johto_text)
    parsed_ops = parse_result.parsed_ops
    legal_ops = extract_legal_ops(johto_text)

    result = LoweringAuditResult(
        amendment_id=amendment_id,
        parsed_op_count=len(parsed_ops),
        legal_op_count=len(legal_ops),
    )

    # Count mismatch — report but still compare what we can
    if len(parsed_ops) != len(legal_ops):
        result.actions_lost.append(
            f"count mismatch: {len(parsed_ops)} parsed_ops vs {len(legal_ops)} legal_ops"
        )

    # Compare by position (zip stops at shorter)
    for i, (pop, lop) in enumerate(zip(parsed_ops, legal_ops, strict=False)):
        # Action check
        expected_actions = _VERB_TO_EXPECTED_ACTION.get(pop.verb, set())
        if lop.action.value in expected_actions:
            result.actions_preserved += 1
        else:
            result.actions_lost.append(
                f"op[{i}] {pop.code()}: verb={pop.verb!r} expected action in "
                f"{expected_actions}, got {lop.action!r}"
            )

        # Target check
        target_mismatch = _compare_target(pop, lop)
        if target_mismatch is None:
            result.targets_preserved += 1
        else:
            result.targets_lost.append(f"op[{i}] {target_mismatch}")

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _run_for_statute(
    sid: str,
    source_filter: Optional[str] = None,
) -> int:
    """Run lower-audit for a statute's amendments. Returns exit code."""
    import sys

    from lawvm.finland.grafter import (
        get_johtolause,
        _normalize_johtolause_verbs,
        _resolve_applicable_amendment_records,
        OP_KEYWORDS,
    )
    from lawvm.finland.corpus import get_corpus

    cs = get_corpus()

    if source_filter:
        # Audit one specific amendment
        amendment_ids = [source_filter]
    else:
        # Get all amendments for this statute
        records, _cutoff, _vmid = _resolve_applicable_amendment_records(
            sid, mode="finlex_oracle", corpus=cs,
        )
        amendment_ids = [r[0] if isinstance(r, tuple) else r.get("statute_id", r.get("amendment_id", ""))  # type: ignore[union-attr]
                         for r in records if r.get("included", True)]

    any_loss = False
    total_results: list[LoweringAuditResult] = []

    for amendment_id in amendment_ids:
        amendment_id_str = str(amendment_id)
        xml_bytes = cs.read_source(amendment_id_str)
        if xml_bytes is None:
            print(f"  {amendment_id_str}: (not in corpus)", file=sys.stderr)
            continue

        johto = get_johtolause(xml_bytes)
        if not johto or len(johto) < 20:
            continue

        johto = _normalize_johtolause_verbs(johto)

        if not any(kw in johto.lower() for kw in OP_KEYWORDS):
            continue

        result = audit_lowering_preservation(johto, amendment_id=amendment_id_str)
        total_results.append(result)

        # Format output line
        n_parsed = result.parsed_op_count
        n_legal = result.legal_op_count
        n_act_ok = result.actions_preserved
        n_tgt_ok = result.targets_preserved
        n_loss = result.losses
        status = "LOSSES: 0 ✓" if n_loss == 0 else f"LOSSES: {n_loss} ✗"

        print(
            f"{amendment_id_str}:\n"
            f"  parsed_ops: {n_parsed}  legal_ops: {n_legal}  "
            f"actions_ok: {n_act_ok}/{n_parsed}  targets_ok: {n_tgt_ok}/{n_parsed}  "
            f"{status}"
        )

        if n_loss > 0:
            any_loss = True
            for desc in result.actions_lost:
                print(f"    ACTION LOSS: {desc}")
            for desc in result.targets_lost:
                print(f"    TARGET LOSS: {desc}")

    if not total_results:
        print("(no amendments with johtolause found)")

    # Summary
    if len(total_results) > 1:
        total_p = sum(r.parsed_op_count for r in total_results)
        total_l = sum(r.legal_op_count for r in total_results)
        total_act = sum(r.actions_preserved for r in total_results)
        total_tgt = sum(r.targets_preserved for r in total_results)
        total_loss = sum(r.losses for r in total_results)
        print(
            f"\nSummary: {len(total_results)} amendments, "
            f"{total_p} parsed_ops, {total_l} legal_ops, "
            f"actions_ok: {total_act}/{total_p}, targets_ok: {total_tgt}/{total_p}, "
            f"total_losses: {total_loss}"
        )

    return 1 if any_loss else 0


def main(args) -> None:
    import sys

    sid = args.statute_id
    source = getattr(args, "source", None)

    print(f"Lower-audit: {sid}")
    if source:
        print(f"Source filter: {source}")
    print()

    exit_code = _run_for_statute(sid, source_filter=source)
    if exit_code != 0:
        sys.exit(exit_code)
