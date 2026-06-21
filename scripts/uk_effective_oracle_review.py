#!/usr/bin/env python3
"""Classify UK public divergence packets against page-declared current XML.

This is a compare/oracle review surface only.  It does not authorize replay and
does not change broad-baseline scoring.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    AgreementResidualStatus,
)
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport


_FORBIDDEN_SHORTCUTS = (
    "effective_oracle_witness_as_replay_authority",
    "current_page_xml_as_source_truth",
    "whole_act_current_xml_as_only_oracle_surface",
)


@dataclass(frozen=True)
class EffectiveOracleReviewRow:
    statute_id: str
    review_status: str
    retained_targets: tuple[str, ...]
    checked_current_page_urls: tuple[str, ...]
    unchecked_targets: tuple[str, ...]
    operation_fragments: tuple[str, ...]
    dated_current_xml_urls: tuple[str, ...]
    dated_current_xml_repealed_count: int
    dated_current_xml_repeal_markup_count: int
    dated_current_xml_repeal_note_only_count: int
    dated_current_xml_no_marker_count: int
    no_known_outstanding_effects_count: int
    simplest_public_check: tuple[str, ...]
    refutation_reason: str
    remaining_question: str
    agreement_residual: Mapping[str, Any]


def load_reviews(path: Path) -> list[EffectiveOracleReviewRow]:
    data = json.loads(path.read_text())
    packets = data.get("rows", data) if isinstance(data, Mapping) else data
    if not isinstance(packets, list):
        raise ValueError(f"{path} does not contain packet rows")
    return [_review_packet(packet) for packet in packets if isinstance(packet, Mapping)]


def _review_packet(packet: Mapping[str, Any]) -> EffectiveOracleReviewRow:
    targets = _string_tuple(packet.get("current_targets"))
    page_witnesses = _mapping_tuple(packet.get("current_page_status_witnesses"))
    xml_witnesses = _mapping_tuple(packet.get("current_timeline_xml_witnesses"))
    checked_urls = _unique(
        str(witness.get("current_page_url") or "")
        for witness in page_witnesses
    )
    dated_urls = _unique(
        str(witness.get("source_xml_url") or "") for witness in xml_witnesses
    )
    no_known = sum(
        1 for witness in page_witnesses if witness.get("no_known_outstanding_effects")
    )
    repealed = sum(
        1
        for witness in xml_witnesses
        if witness.get("effective_oracle_kind") == "dated_current_xml_repealed"
    )
    repeal_markup = sum(
        1
        for witness in xml_witnesses
        if witness.get("effective_oracle_kind")
        in {
            "dated_current_xml_repeal_markup",
            "dated_current_xml_repeal_markup_without_note",
        }
    )
    repeal_note_only = sum(
        1
        for witness in xml_witnesses
        if witness.get("effective_oracle_kind")
        == "dated_current_xml_repeal_note_without_dotted_text"
    )
    no_marker = sum(
        1
        for witness in xml_witnesses
        if witness.get("effective_oracle_kind") == "dated_current_xml_no_repeal_marker"
    )
    unchecked_targets = targets[len(checked_urls) :]
    operation_fragments = _operation_fragments(packet)
    status, refutation, remaining = _status(
        target_count=len(targets),
        witness_count=len(xml_witnesses),
        repealed=repealed,
        repeal_markup=repeal_markup,
        repeal_note_only=repeal_note_only,
        no_marker=no_marker,
    )
    agreement_residual = _agreement_residual(
        statute_id=str(packet.get("statute_id") or ""),
        review_status=status,
        target_count=len(targets),
        witness_count=len(xml_witnesses),
        repealed=repealed,
        repeal_markup=repeal_markup,
        repeal_note_only=repeal_note_only,
        no_marker=no_marker,
    ).to_dict()
    return EffectiveOracleReviewRow(
        statute_id=str(packet.get("statute_id") or ""),
        review_status=status,
        retained_targets=targets,
        checked_current_page_urls=checked_urls,
        unchecked_targets=unchecked_targets,
        operation_fragments=operation_fragments,
        dated_current_xml_urls=dated_urls,
        dated_current_xml_repealed_count=repealed,
        dated_current_xml_repeal_markup_count=repeal_markup,
        dated_current_xml_repeal_note_only_count=repeal_note_only,
        dated_current_xml_no_marker_count=no_marker,
        no_known_outstanding_effects_count=no_known,
        simplest_public_check=_public_check_steps(
            packet=packet,
            checked_urls=checked_urls,
            dated_urls=dated_urls,
            operation_fragments=operation_fragments,
        ),
        refutation_reason=refutation,
        remaining_question=remaining,
        agreement_residual=agreement_residual,
    )


def _status(
    *,
    target_count: int,
    witness_count: int,
    repealed: int,
    repeal_markup: int,
    repeal_note_only: int,
    no_marker: int,
) -> tuple[str, str, str]:
    if witness_count == 0:
        return (
            "insufficient_standalone_evidence",
            "No page-declared dated current XML witness was fetched.",
            "Fetch the provision page and its page-declared dated data.xml before review.",
        )
    if no_marker:
        status = (
            "plausible_true_divergence"
            if no_marker == witness_count and witness_count >= target_count
            else "partially_plausible_true_divergence"
        )
        return (
            status,
            "At least one page-declared dated current XML witness lacks a repeal marker.",
            "Check whether savings, extent, retained-law display, or later revival explains the live text.",
        )
    if repealed + repeal_markup == witness_count:
        return (
            "refuted_by_dated_current_xml",
            "Every fetched page-declared dated current XML witness already shows "
            "dotted repealed text or explicit repeal markup with commentary.",
            "The ordinary whole-act current XML mismatch should be treated as a compare/oracle projection issue, not an actual current-page divergence.",
        )
    if repeal_note_only == witness_count:
        return (
            "likely_not_divergence_because_repeal_note_present",
            "Every fetched page-declared dated current XML witness carries repeal "
            "commentary, although it does not dot out the text.",
            "Check whether legislation.gov.uk intentionally preserves historical wording with an F-note for this repeal family.",
        )
    return (
        "mixed_effective_oracle_witnesses",
        "Fetched dated current XML witnesses use mixed repeal-display conventions.",
        "Review target-by-target before treating this as an official consolidation divergence.",
    )


def _agreement_residual(
    *,
    statute_id: str,
    review_status: str,
    target_count: int,
    witness_count: int,
    repealed: int,
    repeal_markup: int,
    repeal_note_only: int,
    no_marker: int,
) -> AgreementResidual:
    return AgreementResidual(
        residual_id=f"uk-effective-oracle:{statute_id or 'unknown'}",
        jurisdiction="uk",
        agreement_surface="whole_act_current_xml_vs_page_declared_current_timeline_xml",
        family=_agreement_residual_family(review_status),
        agreement_residual_status=_agreement_residual_status(review_status),
        owner_phase="compare_oracle_classification",
        rule_id=f"uk_effective_oracle_{review_status}",
        source_artifact_id=statute_id,
        replay_count=target_count,
        oracle_count=witness_count,
        missing_proofs=_agreement_missing_proofs(review_status),
        safe_default="classify_effective_oracle_without_replay_promotion",
        forbidden_shortcuts=_FORBIDDEN_SHORTCUTS,
        detail={
            "review_status": review_status,
            "target_count": target_count,
            "dated_current_xml_witness_count": witness_count,
            "dated_current_xml_repealed_count": repealed,
            "dated_current_xml_repeal_markup_count": repeal_markup,
            "dated_current_xml_repeal_note_only_count": repeal_note_only,
            "dated_current_xml_no_marker_count": no_marker,
        },
    )


def _agreement_residual_family(review_status: str) -> AgreementResidualFamily:
    if review_status == "insufficient_standalone_evidence":
        return "source_footing_gap"
    if review_status == "plausible_true_divergence":
        return "oracle_editorial_pathology"
    if review_status == "partially_plausible_true_divergence":
        return "oracle_editorial_pathology"
    if review_status == "mixed_effective_oracle_witnesses":
        return "topology_granularity_mismatch"
    return "oracle_editorial_pathology"


def _agreement_residual_status(review_status: str) -> AgreementResidualStatus:
    if review_status in {
        "plausible_true_divergence",
        "partially_plausible_true_divergence",
    }:
        return "residual"
    return "frontier"


def _agreement_missing_proofs(review_status: str) -> tuple[str, ...]:
    if review_status == "insufficient_standalone_evidence":
        return ("page_declared_current_timeline_xml",)
    if review_status in {
        "plausible_true_divergence",
        "partially_plausible_true_divergence",
    }:
        return ("savings_extent_or_revival_review", "editorial_policy_review")
    if review_status == "mixed_effective_oracle_witnesses":
        return ("target_by_target_effective_oracle_review",)
    return ()


def _operation_fragments(packet: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for op in _mapping_tuple(packet.get("operation_evidence")):
        action = str(op.get("action") or "")
        affected = str(op.get("affected_provision") or "")
        source_id = str(op.get("affecting_source_id") or "")
        provisions = str(op.get("affecting_provisions") or "")
        preview = str(op.get("source_preview") or "")
        fragment = " ".join(
            part
            for part in (
                action,
                affected,
                f"source {source_id} {provisions}".strip(),
                preview,
            )
            if part
        )
        if fragment:
            out.append(fragment)
    return tuple(out[:5])


def _public_check_steps(
    *,
    packet: Mapping[str, Any],
    checked_urls: Sequence[str],
    dated_urls: Sequence[str],
    operation_fragments: Sequence[str],
) -> tuple[str, ...]:
    steps: list[str] = []
    if checked_urls:
        steps.append(f"Open current provision page: {checked_urls[0]}")
    if dated_urls:
        steps.append(f"Open the page-declared current XML: {dated_urls[0]}")
    public_sources = _unique(
        url
        for op in _mapping_tuple(packet.get("operation_evidence"))
        for url in _string_tuple(op.get("public_source_urls"))
    )
    if public_sources:
        steps.append(f"Open amending source: {public_sources[0]}")
    if operation_fragments:
        steps.append(f"Compare with source fragment: {operation_fragments[0]}")
    return tuple(steps)


def _emit_json(rows: Sequence[EffectiveOracleReviewRow]) -> str:
    status_counts: dict[str, int] = {}
    residual_family_counts: dict[str, int] = {}
    residual_status_counts: dict[str, int] = {}
    residual_rule_counts: dict[str, int] = {}
    for row in rows:
        status_counts[row.review_status] = status_counts.get(row.review_status, 0) + 1
        residual = row.agreement_residual
        family = str(residual.get("family") or "")
        status = str(residual.get("agreement_residual_status") or "")
        rule = str(residual.get("rule_id") or "")
        residual_family_counts[family] = residual_family_counts.get(family, 0) + 1
        residual_status_counts[status] = residual_status_counts.get(status, 0) + 1
        residual_rule_counts[rule] = residual_rule_counts.get(rule, 0) + 1
    summary = {
        "row_count": len(rows),
        "review_status_counts": status_counts,
        "plausible_true_divergence_count": sum(
            1
            for row in rows
            if row.review_status
            in {"plausible_true_divergence", "partially_plausible_true_divergence"}
        ),
        "agreement_residual_family_counts": residual_family_counts,
        "agreement_residual_status_counts": residual_status_counts,
        "agreement_residual_rule_counts": residual_rule_counts,
    }
    report = EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_effective_oracle_review",
        schema="lawvm.uk_effective_oracle_review.v1",
        truth_claim="page_declared_current_xml_review_not_replay_authority",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary,
        filtered_summary=summary,
        rows=tuple(asdict(row) for row in rows),
        detail={
            "source_truth_claims": False,
            "forbidden_shortcuts": list(_FORBIDDEN_SHORTCUTS),
        },
    )
    return json.dumps(report.to_dict(), indent=2, sort_keys=True)


def _emit_markdown(rows: Sequence[EffectiveOracleReviewRow]) -> str:
    plausible = [
        row
        for row in rows
        if row.review_status
        in {"plausible_true_divergence", "partially_plausible_true_divergence"}
    ]
    lines = [
        "# UK effective-current review candidates",
        "",
        "This is a review queue, not a legal conclusion.",
        "The page-declared current XML is used only to classify the oracle surface; it does not authorize replay.",
        "",
        f"Rows reviewed: {len(rows)}.",
        f"Clean likely divergence candidates: {len(plausible)}.",
        "",
    ]
    status_counts = _review_status_counts(rows)
    if status_counts:
        lines.append("Status counts:")
        for status, count in sorted(status_counts.items()):
            lines.append(f"- {status}: {count}")
        lines.append("")
    if not plausible:
        lines.append(
            "No packet currently survives the page-declared dated XML refutation "
            "check as a clean likely divergence."
        )
        lines.append("")
    if plausible:
        lines.append("Plausible candidates:")
    for index, row in enumerate(plausible, start=1):
        lines.append(f"{index}. {row.statute_id} — {row.review_status}.")
        lines.append(
            f"Targets checked: {', '.join(row.retained_targets) or 'not recorded'}."
        )
        lines.append(f"Why still plausible: {row.refutation_reason}")
        lines.append(f"Remaining question: {row.remaining_question}")
        for step in row.simplest_public_check:
            lines.append(f"Verify: {step}")
        lines.append("")
    non_plausible = [row for row in rows if row not in plausible]
    if non_plausible:
        lines.append("Representative refutations/frontiers:")
        for row in _representative_rows_by_status(non_plausible, per_status=2):
            targets = ", ".join(row.retained_targets[:5]) or "not recorded"
            if len(row.retained_targets) > 5:
                targets = f"{targets}, ..."
            lines.append(f"- {row.statute_id} — {row.review_status}.")
            lines.append(f"  Targets: {targets}.")
            lines.append(f"  Classification: {row.refutation_reason}")
            if row.simplest_public_check:
                lines.append(f"  First public check: {row.simplest_public_check[0]}")
        lines.append("")
    return "\n".join(lines)


def _review_status_counts(rows: Sequence[EffectiveOracleReviewRow]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.review_status] = counts.get(row.review_status, 0) + 1
    return counts


def _representative_rows_by_status(
    rows: Sequence[EffectiveOracleReviewRow],
    *,
    per_status: int,
) -> tuple[EffectiveOracleReviewRow, ...]:
    counts: dict[str, int] = {}
    selected: list[EffectiveOracleReviewRow] = []
    for row in rows:
        seen = counts.get(row.review_status, 0)
        if seen >= per_status:
            continue
        counts[row.review_status] = seen + 1
        selected.append(row)
    return tuple(selected)


def _mapping_tuple(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value if str(item))


def _unique(values: Sequence[str] | Any) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Classify UK public packets against page-declared current XML."
    )
    parser.add_argument("packets", type=Path, help="uk_public_divergence_packets JSON")
    parser.add_argument("--out", type=Path, help="Write JSON report to this path")
    parser.add_argument("--markdown", type=Path, help="Write concise Markdown review queue")
    args = parser.parse_args(argv)

    rows = load_reviews(args.packets)
    payload = _emit_json(rows)
    if args.out:
        args.out.write_text(payload)
    else:
        print(payload)
    if args.markdown:
        args.markdown.write_text(_emit_markdown(rows))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
