"""Generated Tranche 0 destructive-repair ledger.

This tool is intentionally small and seeded with already-audited families.
It exists to make repair ownership explicit before more statute-by-statute
bug work reopens the same mutation families implicitly.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DestructiveRepairLedgerEntry:
    family: str
    function: str
    file: str
    mutation_type: str
    target_region: str
    can_mutate_outside_target: bool
    finding_emitted: str
    strict_barrier: str
    known_corpus_examples: tuple[str, ...]
    status: str


LEDGER_ENTRIES: tuple[DestructiveRepairLedgerEntry, ...] = (
    DestructiveRepairLedgerEntry(
        family="constraint_filter_rejected_op",
        function="_filter_ops_by_constraints",
        file="src/lawvm/finland/constraints.py",
        mutation_type="drop_parsed_op",
        target_region="compiled_op_set",
        can_mutate_outside_target=False,
        finding_emitted="ELAB.REJECTED_OPERATION / ELAB.STRICT_REJECTED_OPERATION",
        strict_barrier="ELAB.STRICT_REJECTED_OPERATION",
        known_corpus_examples=("strict fallback gating", "typed rejected constraint families"),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="explicit_scope_rewrite",
        function="frontend scope provenance projection",
        file="src/lawvm/finland/frontend_observations.py",
        mutation_type="target_scope_rewrite",
        target_region="compiled_target_address",
        can_mutate_outside_target=False,
        finding_emitted="LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
        strict_barrier="LOWER.EXPLICIT_SCOPE_REWRITE_REQUIRED",
        known_corpus_examples=(
            "chapter_scope_stripped_unique_section",
            "chapter_scope_stripped_duplicate_label_outside_stated_chapter",
            "chapter_scope_stripped_subsection_insert",
            "chapter_scope_stripped_section_facet_insert",
        ),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="apply_mutation_boundary",
        function="_apply_mutation_boundary_violation_finding",
        file="src/lawvm/finland/grafter.py",
        mutation_type="post_apply_boundary_violation",
        target_region="declared_target_region",
        can_mutate_outside_target=True,
        finding_emitted="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        strict_barrier="REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
        known_corpus_examples=("boundary violation event stream",),
        status="should_fail",
    ),
    DestructiveRepairLedgerEntry(
        family="uncovered_body_skip",
        function="_uncovered_body_recovery_skipped_finding",
        file="src/lawvm/finland/grafter_uncovered.py",
        mutation_type="candidate_section_skip",
        target_region="uncovered_body_section_candidate",
        can_mutate_outside_target=False,
        finding_emitted="APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED",
        strict_barrier="",
        known_corpus_examples=(
            "chapter_payload_owned",
            "future_repeal",
            "duplicate_recovered_candidate",
        ),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="body_coverage_ignored_or_rejected",
        function="extract_body_coverage / collect_coverage_claims / analyze_coverage",
        file="src/lawvm/finland/body_coverage.py",
        mutation_type="drop_or_demote_coverage_input",
        target_region="coverage_units_and_claims",
        can_mutate_outside_target=False,
        finding_emitted="COVERAGE.BODY_UNIT_IGNORED / COVERAGE.CLAIM_REJECTED / COVERAGE.UNRESOLVED_BODY_GAP",
        strict_barrier="COVERAGE.UNRESOLVED_BODY_GAP",
        known_corpus_examples=("missing_num", "unsupported_target_unit_kind", "ambiguous_uncovered"),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="partial_whole_section_replace_skip",
        function="_drop_suspicious_partial_whole_section_replaces / _drop_suspicious_partial_subsection_shell_replaces",
        file="src/lawvm/finland/merge.py",
        mutation_type="drop_broad_replace_op",
        target_region="group_payload_section_shell",
        can_mutate_outside_target=False,
        finding_emitted="ELAB.REJECTED_OPERATION / ELAB.STRICT_REJECTED_OPERATION",
        strict_barrier="ELAB.STRICT_REJECTED_OPERATION",
        known_corpus_examples=(
            "PARTIAL_WHOLE_SECTION_REPLACE_REJECTED",
            "STALE_WHOLE_SECTION_SHELL_REJECTED",
        ),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="base_editorial_strip",
        function="normalize_source_ir",
        file="src/lawvm/finland/source_normalize.py",
        mutation_type="destructive_editorial_drop",
        target_region="base_source_ir",
        can_mutate_outside_target=False,
        finding_emitted="BASE_EDITORIAL_STRIP",
        strict_barrier="",
        known_corpus_examples=("image block removal", "editorial note stripping"),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="base_numbering_repair",
        function="normalize_source_ir",
        file="src/lawvm/finland/source_normalize.py",
        mutation_type="numbering_gap_witness",
        target_region="base_source_ir",
        can_mutate_outside_target=False,
        finding_emitted="BASE_NUMBERING_REPAIR",
        strict_barrier="",
        known_corpus_examples=("gap witness",),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="base_duplicate_sibling_drop",
        function="_detect_numbering_anomalies",
        file="src/lawvm/finland/source_normalize.py",
        mutation_type="structural_prune",
        target_region="base_source_ir",
        can_mutate_outside_target=False,
        finding_emitted="BASE_DUPLICATE_SIBLING_DROP",
        strict_barrier="",
        known_corpus_examples=("duplicate sibling drop",),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="base_digit_reset_split",
        function="_split_digit_reset_subparagraph_runs",
        file="src/lawvm/finland/source_normalize.py",
        mutation_type="structural_split",
        target_region="base_source_ir",
        can_mutate_outside_target=False,
        finding_emitted="BASE_DIGIT_RESET_SPLIT",
        strict_barrier="",
        known_corpus_examples=("digit reset split",),
        status="safe",
    ),
    DestructiveRepairLedgerEntry(
        family="base_duplicate_tail_split",
        function="_split_nonpenal_trailing_duplicate_paragraph",
        file="src/lawvm/finland/source_normalize.py",
        mutation_type="structural_split",
        target_region="base_source_ir",
        can_mutate_outside_target=False,
        finding_emitted="BASE_DUPLICATE_TAIL_SPLIT",
        strict_barrier="",
        known_corpus_examples=("duplicated-tail split",),
        status="safe",
    ),
)


def build_ledger() -> tuple[DestructiveRepairLedgerEntry, ...]:
    return LEDGER_ENTRIES


def render_markdown(entries: tuple[DestructiveRepairLedgerEntry, ...]) -> str:
    lines = [
        "# Destructive Repair Ledger",
        "",
        "| family | function | file | mutation_type | target_region | outside_target | finding_emitted | strict_barrier | examples | status |",
        "|---|---|---|---|---|---:|---|---|---|---|",
    ]
    for entry in entries:
        examples = "; ".join(entry.known_corpus_examples)
        lines.append(
            "| "
            + " | ".join(
                [
                    entry.family,
                    entry.function,
                    entry.file,
                    entry.mutation_type,
                    entry.target_region,
                    "yes" if entry.can_mutate_outside_target else "no",
                    entry.finding_emitted or "-",
                    entry.strict_barrier or "-",
                    examples or "-",
                    entry.status,
                ]
            )
            + " |"
        )
    return "\n".join(lines)


def main(args: Any) -> None:
    entries = build_ledger()
    if getattr(args, "json", False):
        print(json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2))
        return
    print(render_markdown(entries))
