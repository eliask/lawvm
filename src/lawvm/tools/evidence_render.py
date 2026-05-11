"""Rendering and output formatting for evidence bundles.

Extracted from evidence.py — Markdown rendering, text printing,
and file output helpers.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, cast

from lawvm.tools._evidence_helpers import (
    _section_similarity,
)


@dataclass(frozen=True)
class CompilerObservationSupportRow:
    section: str = ""
    blame_source: str = ""
    elaboration_kinds: tuple[str, ...] = ()
    apply_helpers: tuple[str, ...] = ()
    sparse_slot_binding_labels: tuple[str, ...] = ()
    sparse_leftover_labels: tuple[str, ...] = ()
    payload_completeness_kinds: tuple[str, ...] = ()
    payload_completeness_tail_policies: tuple[str, ...] = ()
    sparse_leftover_slot_count: int = 0


@dataclass(frozen=True)
class CompilerObservationsView:
    elaboration_observation_count: int = 0
    sparse_slot_binding_count: int = 0
    sparse_leftover_count: int = 0
    sparse_leftover_slot_count: int = 0
    sparse_leftover_labels: tuple[str, ...] = ()
    elaboration_projection_count: int = 0
    apply_mutation_event_count: int = 0
    provenance_projection_count: int = 0
    elaboration_kind_counts: dict[str, int] = field(default_factory=dict)
    payload_completeness_kind_counts: dict[str, int] = field(default_factory=dict)
    payload_completeness_tail_policy_counts: dict[str, int] = field(default_factory=dict)
    apply_helper_counts: dict[str, int] = field(default_factory=dict)
    provenance_projection_rows: tuple[dict[str, Any], ...] = ()
    section_bisect_rows_with_observation_support: tuple[CompilerObservationSupportRow, ...] = ()
    unowned_observation_count: int = 0
    unowned_observation_family_counts: dict[str, int] = field(default_factory=dict)
    unowned_observation_rows: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class ReviewRowView:
    statute_id: str = ""
    display_primary_tier: str = ""
    primary_proof_tier: str = ""
    proof_kinds: tuple[str, ...] = ()
    selected_section_claim_kinds: tuple[str, ...] = ()
    defeated_section_claim_kinds: tuple[str, ...] = ()
    selected_section_claim_rules: tuple[str, ...] = ()
    defeated_section_claim_rules: tuple[str, ...] = ()
    mixed_replay_risk: bool = False
    mixed_replay_risk_reasons: tuple[str, ...] = ()
    elaboration_observation_kinds: tuple[str, ...] = ()
    payload_concern_kinds: tuple[str, ...] = ()
    payload_concern_tail_policies: tuple[str, ...] = ()
    payload_completeness_kinds: tuple[str, ...] = ()
    payload_tail_policies: tuple[str, ...] = ()
    provenance_projection_rows: tuple[dict[str, Any], ...] = ()
    source_proof_kinds: tuple[str, ...] = ()
    source_pathologies: tuple[dict[str, Any], ...] = ()
    alternative_replay_sections: tuple[str, ...] = ()
    html_noncommensurable_reason: str = ""
    evidence_context_degradation_count: int = 0
    evidence_context_degradation_rails: tuple[str, ...] = ()
    sparse_leftover_labels: tuple[str, ...] = ()
    sparse_blockers: tuple[dict[str, Any], ...] = ()
    sparse_slot_binding_count: int = 0
    sparse_leftover_count: int = 0


@dataclass(frozen=True)
class ReviewSummaryView:
    artifact_count: int | None = None
    statute_count: int | None = None
    bundle_count: int = 0
    selected_count: int = 0
    filters: dict[str, Any] = field(default_factory=dict)
    by_primary_tier: dict[str, int] = field(default_factory=dict)
    by_display_primary_tier: dict[str, int] = field(default_factory=dict)
    by_claim_kind: dict[str, int] = field(default_factory=dict)
    by_section_claim_kind: dict[str, int] = field(default_factory=dict)
    by_section_claim_inference_rule: dict[str, int] = field(default_factory=dict)
    by_defeated_section_claim_kind: dict[str, int] = field(default_factory=dict)
    by_defeated_section_claim_inference_rule: dict[str, int] = field(default_factory=dict)
    by_strict_fail_reason: dict[str, int] = field(default_factory=dict)
    by_elaboration_observation_kind: dict[str, int] = field(default_factory=dict)
    by_sparse_blocker_source: dict[str, int] = field(default_factory=dict)
    by_sparse_blocker_section: dict[str, int] = field(default_factory=dict)
    by_payload_completeness_kind: dict[str, int] = field(default_factory=dict)
    by_payload_tail_policy: dict[str, int] = field(default_factory=dict)
    by_provenance_projection_kind: dict[str, int] = field(default_factory=dict)
    by_provenance_tag: dict[str, int] = field(default_factory=dict)
    by_provenance_source_statute: dict[str, int] = field(default_factory=dict)
    by_source_pathology_code: dict[str, int] = field(default_factory=dict)
    by_source_proof_kind: dict[str, int] = field(default_factory=dict)
    by_source_pathology_source: dict[str, int] = field(default_factory=dict)
    by_source_pathology_target_label: dict[str, int] = field(default_factory=dict)
    by_source_pathology_diagnostic_reason: dict[str, int] = field(default_factory=dict)
    by_alternative_replay_section: dict[str, int] = field(default_factory=dict)
    by_html_noncommensurable_reason: dict[str, int] = field(default_factory=dict)
    by_evidence_context_degradation_rail: dict[str, int] = field(default_factory=dict)
    by_evidence_context_degradation_exception: dict[str, int] = field(default_factory=dict)
    by_mixed_replay_risk_reason: dict[str, int] = field(default_factory=dict)
    evidence_context_degraded_count: int = 0
    rows: tuple[ReviewRowView, ...] = ()


def _coerce_str_tuple(values: object) -> tuple[str, ...]:
    if not isinstance(values, Iterable) or isinstance(values, (str, bytes)):
        return ()
    return tuple(str(item) for item in values if str(item))


def _coerce_int_map(values: object) -> dict[str, int]:
    if not isinstance(values, Mapping):
        return {}
    mapping = cast(Mapping[str, Any], values)
    return {
        str(key): int(value)
        for key, value in mapping.items()
        if str(key)
    }


def _coerce_compiler_observations(raw: object) -> CompilerObservationsView:
    if not isinstance(raw, Mapping):
        return CompilerObservationsView()
    mapping = cast(Mapping[str, Any], raw)
    rows_raw = mapping.get("section_bisect_rows_with_observation_support", ())
    rows: list[CompilerObservationSupportRow] = []
    if isinstance(rows_raw, Iterable) and not isinstance(rows_raw, (str, bytes)):
        for item in rows_raw:
            if not isinstance(item, Mapping):
                continue
            rows.append(
                CompilerObservationSupportRow(
                    section=str(item.get("section") or ""),
                    blame_source=str(item.get("blame_source") or ""),
                    elaboration_kinds=_coerce_str_tuple(
                        item.get("elaboration_kinds", ())
                    ),
                    apply_helpers=_coerce_str_tuple(item.get("apply_helpers", ())),
                    sparse_slot_binding_labels=_coerce_str_tuple(
                        item.get("sparse_slot_binding_labels", ())
                    ),
                    sparse_leftover_labels=_coerce_str_tuple(
                        item.get("sparse_leftover_labels", ())
                    ),
                    payload_completeness_kinds=_coerce_str_tuple(
                        item.get("payload_completeness_kinds", ())
                    ),
                    payload_completeness_tail_policies=_coerce_str_tuple(
                        item.get("payload_completeness_tail_policies", ())
                    ),
                    sparse_leftover_slot_count=int(
                        item.get("sparse_leftover_slot_count", 0) or 0
                    ),
                )
            )
    provenance_raw = mapping.get("provenance_projection_rows", ())
    provenance: tuple[dict[str, Any], ...] = ()
    if isinstance(provenance_raw, Iterable) and not isinstance(provenance_raw, (str, bytes)):
        provenance = tuple(item for item in provenance_raw if isinstance(item, dict))
    unowned_raw = mapping.get("normalized_unowned_observation_rows", ())
    unowned: tuple[dict[str, Any], ...] = ()
    if isinstance(unowned_raw, Iterable) and not isinstance(unowned_raw, (str, bytes)):
        unowned = tuple(item for item in unowned_raw if isinstance(item, dict))
    return CompilerObservationsView(
        elaboration_observation_count=int(mapping.get("elaboration_observation_count", 0) or 0),
        sparse_slot_binding_count=int(mapping.get("sparse_slot_binding_count", 0) or 0),
        sparse_leftover_count=int(mapping.get("sparse_leftover_count", 0) or 0),
        sparse_leftover_slot_count=int(mapping.get("sparse_leftover_slot_count", 0) or 0),
        sparse_leftover_labels=_coerce_str_tuple(mapping.get("sparse_leftover_labels", ())),
        elaboration_projection_count=int(mapping.get("elaboration_projection_count", 0) or 0),
        apply_mutation_event_count=int(mapping.get("apply_mutation_event_count", 0) or 0),
        provenance_projection_count=int(mapping.get("provenance_projection_count", 0) or 0),
        elaboration_kind_counts=_coerce_int_map(mapping.get("elaboration_kind_counts", {})),
        payload_completeness_kind_counts=_coerce_int_map(mapping.get("payload_completeness_kind_counts", {})),
        payload_completeness_tail_policy_counts=_coerce_int_map(mapping.get("payload_completeness_tail_policy_counts", {})),
        apply_helper_counts=_coerce_int_map(mapping.get("apply_helper_counts", {})),
        provenance_projection_rows=provenance,
        section_bisect_rows_with_observation_support=tuple(rows),
        unowned_observation_count=int(mapping.get("normalized_unowned_observation_count", 0) or 0),
        unowned_observation_family_counts=_coerce_int_map(
            mapping.get("normalized_unowned_observation_family_counts", {})
        ),
        unowned_observation_rows=unowned,
    )


def _coerce_review_rows(raw: object) -> tuple[ReviewRowView, ...]:
    if not isinstance(raw, Iterable) or isinstance(raw, (str, bytes)):
        return ()
    rows: list[ReviewRowView] = []
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        mapping = cast(Mapping[str, Any], item)
        rows.append(
            ReviewRowView(
                statute_id=str(mapping.get("statute_id") or ""),
                display_primary_tier=str(mapping.get("display_primary_tier") or ""),
                primary_proof_tier=str(mapping.get("primary_proof_tier") or ""),
                proof_kinds=_coerce_str_tuple(mapping.get("proof_kinds", ())),
                selected_section_claim_kinds=_coerce_str_tuple(mapping.get("selected_section_claim_kinds", ())),
                defeated_section_claim_kinds=_coerce_str_tuple(mapping.get("defeated_section_claim_kinds", ())),
                selected_section_claim_rules=_coerce_str_tuple(mapping.get("selected_section_claim_rules", ())),
                defeated_section_claim_rules=_coerce_str_tuple(mapping.get("defeated_section_claim_rules", ())),
                mixed_replay_risk=bool(mapping.get("mixed_replay_risk")),
                mixed_replay_risk_reasons=_coerce_str_tuple(mapping.get("mixed_replay_risk_reasons", ())),
                elaboration_observation_kinds=_coerce_str_tuple(mapping.get("elaboration_observation_kinds", ())),
                payload_concern_kinds=_coerce_str_tuple(mapping.get("payload_concern_kinds", mapping.get("payload_completeness_kinds", ()))),
                payload_concern_tail_policies=_coerce_str_tuple(mapping.get("payload_concern_tail_policies", mapping.get("payload_tail_policies", ()))),
                payload_completeness_kinds=_coerce_str_tuple(mapping.get("payload_completeness_kinds", ())),
                payload_tail_policies=_coerce_str_tuple(mapping.get("payload_tail_policies", ())),
                provenance_projection_rows=tuple(
                    item2
                    for item2 in (mapping.get("provenance_projection_rows", ()) or ())
                    if isinstance(item2, dict)
                ),
                source_proof_kinds=_coerce_str_tuple(mapping.get("source_proof_kinds", ())),
                source_pathologies=tuple(
                    item2 for item2 in mapping.get("source_pathologies", ()) or () if isinstance(item2, dict)
                ),
                alternative_replay_sections=_coerce_str_tuple(mapping.get("alternative_replay_sections", ())),
                html_noncommensurable_reason=str(mapping.get("html_noncommensurable_reason") or ""),
                evidence_context_degradation_count=int(
                    mapping.get("evidence_context_degradation_count", 0) or 0
                ),
                evidence_context_degradation_rails=_coerce_str_tuple(
                    mapping.get("evidence_context_degradation_rails", ())
                ),
                sparse_leftover_labels=_coerce_str_tuple(mapping.get("sparse_leftover_labels", ())),
                sparse_blockers=tuple(
                    item2 for item2 in mapping.get("sparse_blockers", ()) or () if isinstance(item2, dict)
                ),
                sparse_slot_binding_count=int(mapping.get("sparse_slot_binding_count", 0) or 0),
                sparse_leftover_count=int(mapping.get("sparse_leftover_count", 0) or 0),
            )
        )
    return tuple(rows)


def _coerce_review_summary(review: object) -> ReviewSummaryView:
    if not isinstance(review, Mapping):
        return ReviewSummaryView()
    review_map = cast(Mapping[str, Any], review)
    return ReviewSummaryView(
        artifact_count=(
            int(review_map["artifact_count"]) if "artifact_count" in review_map and review_map["artifact_count"] is not None else None
        ),
        statute_count=(
            int(review_map["statute_count"]) if "statute_count" in review_map and review_map["statute_count"] is not None else None
        ),
        bundle_count=int(review_map.get("bundle_count", 0) or 0),
        selected_count=int(review_map.get("selected_count", 0) or 0),
        filters={str(k): v for k, v in (review_map.get("filters") or {}).items()} if isinstance(review_map.get("filters"), Mapping) else {},
        by_primary_tier=_coerce_int_map(review_map.get("by_primary_tier", {})),
        by_display_primary_tier=_coerce_int_map(review_map.get("by_display_primary_tier", {})),
        by_claim_kind=_coerce_int_map(review_map.get("by_claim_kind", {})),
        by_section_claim_kind=_coerce_int_map(review_map.get("by_section_claim_kind", {})),
        by_section_claim_inference_rule=_coerce_int_map(review_map.get("by_section_claim_inference_rule", {})),
        by_defeated_section_claim_kind=_coerce_int_map(review_map.get("by_defeated_section_claim_kind", {})),
        by_defeated_section_claim_inference_rule=_coerce_int_map(review_map.get("by_defeated_section_claim_inference_rule", {})),
        by_strict_fail_reason=_coerce_int_map(review_map.get("by_strict_fail_reason", {})),
        by_elaboration_observation_kind=_coerce_int_map(review_map.get("by_elaboration_observation_kind", {})),
        by_sparse_blocker_source=_coerce_int_map(review_map.get("by_sparse_blocker_source", {})),
        by_sparse_blocker_section=_coerce_int_map(review_map.get("by_sparse_blocker_section", {})),
        by_payload_completeness_kind=_coerce_int_map(review_map.get("by_payload_completeness_kind", {})),
        by_payload_tail_policy=_coerce_int_map(review_map.get("by_payload_tail_policy", {})),
        by_provenance_projection_kind=_coerce_int_map(review_map.get("by_provenance_projection_kind", {})),
        by_provenance_tag=_coerce_int_map(review_map.get("by_provenance_tag", {})),
        by_provenance_source_statute=_coerce_int_map(review_map.get("by_provenance_source_statute", {})),
        by_source_pathology_code=_coerce_int_map(review_map.get("by_source_pathology_code", {})),
        by_source_proof_kind=_coerce_int_map(review_map.get("by_source_proof_kind", {})),
        by_source_pathology_source=_coerce_int_map(review_map.get("by_source_pathology_source", {})),
        by_source_pathology_target_label=_coerce_int_map(review_map.get("by_source_pathology_target_label", {})),
        by_source_pathology_diagnostic_reason=_coerce_int_map(review_map.get("by_source_pathology_diagnostic_reason", {})),
        by_alternative_replay_section=_coerce_int_map(review_map.get("by_alternative_replay_section", {})),
        by_html_noncommensurable_reason=_coerce_int_map(review_map.get("by_html_noncommensurable_reason", {})),
        by_evidence_context_degradation_rail=_coerce_int_map(
            review_map.get("by_evidence_context_degradation_rail", {})
        ),
        by_evidence_context_degradation_exception=_coerce_int_map(
            review_map.get("by_evidence_context_degradation_exception", {})
        ),
        by_mixed_replay_risk_reason=_coerce_int_map(review_map.get("by_mixed_replay_risk_reason", {})),
        evidence_context_degraded_count=int(review_map.get("evidence_context_degraded_count", 0) or 0),
        rows=_coerce_review_rows(review_map.get("rows", ())),
    )


def _snippet(text: str, *, limit: int = 700) -> str:
    value = " ".join(str(text or "").split())
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _format_provenance_projection_row(item: Mapping[str, Any]) -> str:
    kind = str(item.get("kind") or "").strip()
    source_statute = str(item.get("source_statute") or "").strip()
    tag = str(item.get("tag") or "").strip()
    target_unit_kind = str(item.get("target_unit_kind") or "").strip()
    target_kind = str(item.get("target_kind") or "").strip()
    if not target_kind:
        if target_unit_kind == "section":
            target_kind = "P"
        elif target_unit_kind == "chapter":
            target_kind = "L"
        elif target_unit_kind == "part":
            target_kind = "O"
    target_norm = str(item.get("target_norm") or "").strip()
    target_chapter = str(item.get("target_chapter") or "").strip()

    parts: list[str] = []
    if kind:
        parts.append(f"`{kind}`")
    if source_statute:
        parts.append(f"source=`{source_statute}`")
    target_parts: list[str] = []
    if target_kind:
        target_parts.append(f"kind={target_kind}")
    if target_norm:
        target_parts.append(f"norm={target_norm}")
    if target_chapter:
        target_parts.append(f"chapter={target_chapter}")
    if target_parts:
        parts.append(f"target({', '.join(target_parts)})")
    if tag:
        parts.append(f"tag=`{tag}`")
    return " ".join(parts)


def _summarize_provenance_projection_rows(
    items: Iterable[Mapping[str, Any]], *, limit: int = 3
) -> str:
    rendered: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").strip()
        if not kind:
            continue
        target_unit_kind = str(item.get("target_unit_kind") or "").strip()
        target_kind = str(item.get("target_kind") or "").strip()
        if not target_kind:
            if target_unit_kind == "section":
                target_kind = "P"
            elif target_unit_kind == "chapter":
                target_kind = "L"
            elif target_unit_kind == "part":
                target_kind = "O"
        target_norm = str(item.get("target_norm") or "").strip()
        target_chapter = str(item.get("target_chapter") or "").strip()
        tag = str(item.get("tag") or "").strip()
        target_bits: list[str] = []
        if target_kind:
            target_bits.append(target_kind)
        if target_norm:
            target_bits.append(target_norm)
        if target_chapter:
            target_bits.append(f"ch={target_chapter}")
        compact = kind
        if target_bits:
            compact += "@" + ":".join(target_bits)
        if tag:
            compact += f"/{tag}"
        rendered.append(compact)
    if len(rendered) <= limit:
        return ", ".join(rendered) or "-"
    return ", ".join(rendered[:limit]) + f", ...(+{len(rendered) - limit})"


def _format_unowned_observation_row(item: Mapping[str, Any]) -> str:
    target_unit_kind = str(item.get("target_unit_kind") or "").strip()
    target_kind = str(item.get("target_kind") or "").strip()
    target_norm = str(item.get("target_norm") or "").strip()
    target_chapter = str(item.get("target_chapter") or "").strip()
    target_path = item.get("target_path")
    parts: list[str] = []
    if target_unit_kind:
        parts.append(f"unit={target_unit_kind}")
    if target_kind:
        parts.append(f"kind={target_kind}")
    if target_norm:
        parts.append(f"norm={target_norm}")
    if target_chapter:
        parts.append(f"chapter={target_chapter}")
    if target_path not in (None, "", ()):
        parts.append(f"path={target_path}")
    return " ".join(parts) or "-"


def _summarize_unowned_observations(view: CompilerObservationsView) -> tuple[int, str]:
    count = view.unowned_observation_count
    if not count and view.unowned_observation_rows:
        count = sum(int(item.get("count", 0) or 0) for item in view.unowned_observation_rows)
        if not count:
            count = len(view.unowned_observation_rows)
    family_counts = dict(view.unowned_observation_family_counts)
    if not family_counts and view.unowned_observation_rows:
        counter: Counter[str] = Counter()
        for item in view.unowned_observation_rows:
            family = str(item.get("family") or "").strip()
            if family:
                counter[family] += int(item.get("count", 0) or 0)
        family_counts = dict(sorted(counter.items()))
    family_summary = ", ".join(f"{family}={family_counts[family]}" for family in sorted(family_counts)) or "-"
    return count, family_summary


def _summarize_source_pathologies(
    items: Iterable[Mapping[str, Any]], *, limit: int = 3
) -> str:
    rendered: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        source_statute = str(item.get("source_statute") or "").strip()
        target_label = str(item.get("target_label") or "").strip()
        diagnostic_reason = str(item.get("diagnostic_reason") or "").strip()
        if not code and not source_statute and not target_label and not diagnostic_reason:
            continue
        compact = code or "source_pathology"
        extras: list[str] = []
        if target_label:
            extras.append(target_label)
        if source_statute:
            extras.append(f"src={source_statute}")
        if extras:
            compact += "@" + "/".join(extras)
        if diagnostic_reason:
            compact += f"/diag={diagnostic_reason}"
        rendered.append(compact)
    if len(rendered) <= limit:
        return ", ".join(rendered) or "-"
    return ", ".join(rendered[:limit]) + f", ...(+{len(rendered) - limit})"


def _write_bundle_output(path: str, bundles: List[Dict]) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if len(bundles) == 1 and target.suffix.lower() != ".jsonl":
        target.write_text(json.dumps(bundles[0], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        with target.open("w", encoding="utf-8") as f:
            for bundle in bundles:
                f.write(json.dumps(bundle, ensure_ascii=False, sort_keys=False))
                f.write("\n")
    return target


def _render_markdown_bundle(bundle: Dict, *, oracle_only: bool = False) -> str:
    lines: List[str] = []
    lines.append(f"# {bundle['statute_id']}")
    if bundle.get("title"):
        lines.append("")
        lines.append(f"**Title:** {bundle['title']}")
    lines.append(f"**Mode:** {bundle['mode']}")
    contract = bundle.get("proof_contract") or {}
    if contract:
        lines.append(
            f"**Proof Contract:** {contract.get('version', '')} ({contract.get('status', '')})"
        )
    if not oracle_only:
        if "overall_score" in bundle and "section_score" in bundle:
            lines.append(
                f"**Scores:** overall {bundle['overall_score']:.3%}, section {bundle['section_score']:.3%}"
            )
        strict_value = not bool(bundle.get("strict_fail_reasons") or [])
        lines.append(f"**Strict:** {'PASS' if strict_value else 'FAIL'}")
        if bundle.get("strict_fail_reasons"):
            lines.append(f"**Strict Fail Reasons:** {', '.join(bundle['strict_fail_reasons'])}")
    lines.append(f"**Primary Tier:** {bundle.get('primary_proof_tier', 'UNRESOLVED')}")
    proof_tiers = bundle.get("proof_tiers") or []
    if proof_tiers:
        lines.append(f"**All Tiers:** {', '.join(proof_tiers)}")
    compiler_observations = _coerce_compiler_observations(bundle.get("compiler_observations"))
    if any(
        (
            compiler_observations.elaboration_observation_count,
            compiler_observations.sparse_slot_binding_count,
            compiler_observations.sparse_leftover_count,
            compiler_observations.elaboration_projection_count,
            compiler_observations.apply_mutation_event_count,
            compiler_observations.provenance_projection_count,
            compiler_observations.elaboration_kind_counts,
            compiler_observations.payload_completeness_kind_counts,
            compiler_observations.payload_completeness_tail_policy_counts,
            compiler_observations.apply_helper_counts,
            compiler_observations.provenance_projection_rows,
            compiler_observations.section_bisect_rows_with_observation_support,
            compiler_observations.unowned_observation_rows,
        )
    ):
        unowned_count, unowned_family_summary = _summarize_unowned_observations(compiler_observations)
        lines.append(
            "**Compiler Observations:** "
            f"elaboration={compiler_observations.elaboration_observation_count} "
            f"bindings={compiler_observations.sparse_slot_binding_count} "
            f"leftovers={compiler_observations.sparse_leftover_count} "
            f"projected={compiler_observations.elaboration_projection_count} "
            f"apply={compiler_observations.apply_mutation_event_count} "
            f"provenance={compiler_observations.provenance_projection_count}"
        )
        if unowned_count:
            lines.append(f"- unowned observation rows: {unowned_count}")
            lines.append(f"  - families: {unowned_family_summary}")
        if compiler_observations.unowned_observation_rows:
            lines.append("- unowned observations:")
            for item in compiler_observations.unowned_observation_rows[:10]:
                target_path = item.get("target_path")
                target_coords = _format_unowned_observation_row(item)
                lines.append(
                    "  - "
                    f"`{item.get('family', '')}` `{item.get('kind', '')}` "
                    f"stage=`{item.get('stage', '')}` source=`{item.get('source_statute', '')}` "
                    f"target=`{item.get('target_unit_kind', '')}` coords=`{target_coords}` "
                    f"path=`{target_path}` count={int(item.get('count', 0) or 0)}"
                )
    lines.append("")
    lines.append("## Proof Claims")
    for claim in bundle.get("proof_claims", []):
        lines.append("")
        lines.append(f"### {claim.get('tier', '')} / {claim.get('kind', '')}")
        lines.append(claim.get("summary", ""))
        if claim.get("inference_rule"):
            lines.append("")
            lines.append(f"- inference_rule: `{claim['inference_rule']}`")
        if claim.get("trigger_observations"):
            lines.append("- trigger_observations:")
            for obs in claim["trigger_observations"]:
                scope = f" ({obs.get('scope')})" if obs.get("scope") else ""
                lines.append(
                    f"  - `{obs.get('source','')}.{obs.get('field','')}`{scope}: `{json.dumps(obs.get('value'), ensure_ascii=False)}`"
                )
    if bundle.get("html_topology"):
        html = bundle["html_topology"]
        lines.append("")
        lines.append("## HTML/XML")
        lines.append(f"- url: {html.get('html_url', '')}")
        if html.get("noncommensurable_reason"):
            lines.append(f"- noncommensurable: `{html['noncommensurable_reason']}`")
        if html.get("missing_from_xml"):
            lines.append(f"- missing_from_xml: {', '.join(html['missing_from_xml'])}")
        if html.get("extra_in_xml"):
            lines.append(f"- extra_in_xml: {', '.join(html['extra_in_xml'])}")
    if oracle_only:
        if bundle.get("oracle_version_amendment_id"):
            lines.append("")
            lines.append("## Oracle State")
            lines.append(
                f"- oracle_version_amendment_id: `{bundle['oracle_version_amendment_id']}`"
            )
            if bundle.get("oracle_suspect_detail"):
                lines.append(f"- oracle_suspect_detail: `{bundle['oracle_suspect_detail']}`")
            if bundle.get("oracle_suspect_pending"):
                lines.append(f"- oracle_suspect_pending: `{bundle['oracle_suspect_pending']}`")
            consolidated_url = str(
                (bundle.get("verification_links") or {}).get("consolidated_url") or ""
            )
            if consolidated_url:
                lines.append(f"- consolidated_url: {consolidated_url}")
        artifact_summary = bundle.get("artifact_summary")
        if isinstance(artifact_summary, Mapping):
            lines.append("")
            lines.append("## Artifact Summary")
            lines.append(
                f"- section_artifact_count: `{int(artifact_summary.get('section_artifact_count', 0) or 0)}`"
            )
            lines.append(
                f"- ready_section_artifact_count: `{int(artifact_summary.get('ready_section_artifact_count', 0) or 0)}`"
            )
            lines.append(
                f"- statute_artifact_count: `{int(artifact_summary.get('statute_artifact_count', 0) or 0)}`"
            )
            lines.append(
                f"- ready_statute_artifact_count: `{int(artifact_summary.get('ready_statute_artifact_count', 0) or 0)}`"
            )
            lines.append(
                f"- ready_total_artifact_count: `{int(artifact_summary.get('ready_total_artifact_count', 0) or 0)}`"
            )
            by_family = artifact_summary.get("by_family") or {}
            if by_family:
                lines.append(
                    "- families: "
                    + ", ".join(
                        f"`{family}`={count}" for family, count in by_family.items()
                    )
                )
            by_complexity = artifact_summary.get("by_complexity") or {}
            if by_complexity:
                lines.append(
                    "- complexities: "
                    + ", ".join(
                        f"`{kind}`={count}" for kind, count in by_complexity.items()
                    )
                )
            verification_gaps = artifact_summary.get("verification_gaps") or {}
            if verification_gaps:
                lines.append(
                    "- verification_gaps: "
                    + ", ".join(
                        f"`{gap}`={count}" for gap, count in verification_gaps.items()
                    )
                )
        if bundle.get("section_claims") or bundle.get("section_results"):
            section_claims = {
                str(item.get("section") or ""): item
                for item in bundle.get("section_claims", []) or []
                if str(item.get("section") or "")
            }
            lines.append("")
            lines.append("## Oracle Proof Artifacts")
            for item in bundle.get("section_results", []) or []:
                section = str(item.get("section") or "")
                if not section:
                    continue
                claim = section_claims.get(section, {})
                lines.append("")
                lines.append(f"### `{section}`")
                if claim:
                    lines.append(
                        f"- selected_claim: `{claim.get('selected_kind', '')}` "
                        f"({claim.get('selected_tier', '')}) via `{claim.get('selected_inference_rule', '')}`"
                    )
                diagnosis = str(item.get("diagnosis") or "")
                if diagnosis:
                    lines.append(f"- diagnosis: `{diagnosis}`")
                blame_source = str(item.get("blame_source") or "")
                if blame_source:
                    lines.append(f"- blame_source: `{blame_source}`")
                blame_title = str(item.get("blame_source_title") or "")
                if blame_title:
                    lines.append(f"- blame_title: {blame_title}")
                section_url = str(item.get("section_url") or "")
                if section_url:
                    lines.append(f"- verify_finlex: {section_url}")
                blame_source_url = str(item.get("blame_source_url") or "")
                if blame_source_url:
                    lines.append(f"- verify_amendment: {blame_source_url}")
                blame_johtolause = _snippet(str(item.get("blame_source_johtolause") or ""))
                if blame_johtolause:
                    lines.append("- johtolause:")
                    lines.append("")
                    lines.append("```text")
                    lines.append(blame_johtolause)
                    lines.append("```")
                chain_amendments = item.get("chain_amendments") or []
                if chain_amendments:
                    lines.append("- chain_amendments:")
                    for chain_item in chain_amendments:
                        amendment_id = str(chain_item.get("amendment_id") or "")
                        source_title = str(chain_item.get("source_title") or "")
                        amendment_url = str(chain_item.get("amendment_url") or "")
                        chain_line = f"  - `{amendment_id}`"
                        if source_title:
                            chain_line += f" {source_title}"
                        if amendment_url:
                            chain_line += f" ({amendment_url})"
                        lines.append(chain_line)
                artifact_profile = item.get("artifact_profile") or {}
                family = str(artifact_profile.get("family") or "")
                if family:
                    lines.append(
                        f"- artifact_family: `{family}` "
                        f"({artifact_profile.get('complexity', '')})"
                    )
                gaps = artifact_profile.get("verification_gaps") or []
                if gaps:
                    lines.append(
                        "- artifact_gaps: " + ", ".join(f"`{gap}`" for gap in gaps)
                    )
                similarity = float(
                    item.get("similarity")
                    or _section_similarity(
                        str(item.get("replay_text") or ""),
                        str(item.get("oracle_text") or ""),
                    )
                )
                lines.append(f"- similarity: `{similarity:.6f}`")
                oracle_text = _snippet(str(item.get("oracle_text") or ""))
                replay_text = _snippet(str(item.get("replay_text") or ""))
                if oracle_text:
                    lines.append("- oracle:")
                    lines.append("")
                    lines.append("```text")
                    lines.append(oracle_text)
                    lines.append("```")
                if replay_text:
                    lines.append("- replay:")
                    lines.append("")
                    lines.append("```text")
                    lines.append(replay_text)
                    lines.append("```")
    if bundle.get("supporting_amendments"):
        lines.append("")
        lines.append("## Supporting Amendments")
        for item in bundle["supporting_amendments"]:
            lines.append(
                f"- `{item['amendment_id']}` official={item.get('official_item_count',0)} "
                f"verified={item.get('verified_in_source_count',0)} manual={item.get('manual_override_count',0)}"
            )
    if bundle.get("section_bisect"):
        lines.append("")
        lines.append("## Section Bisect")
        for item in bundle["section_bisect"]:
            first_drop = item.get("first_drop_source") or "(none)"
            lines.append(
                f"- `{item['section']}` baseline={item.get('baseline_score', 0.0):.3%} first_drop={first_drop}"
            )
    if bundle.get("section_claims"):
        lines.append("")
        lines.append("## Section Claims")
        for item in bundle.get("section_claims", [])[:20]:
            if not str(item.get("selected_kind") or ""):
                continue
            defeated = ", ".join(item.get("defeated_candidate_kinds", []) or []) or ""
            suffix = f" defeated=[{defeated}]" if defeated else ""
            reason = str(item.get("selected_inference_rule") or "")
            if reason:
                suffix += f" via=`{reason}`"
            alt = item.get("alternative_replay_match") or {}
            if alt:
                suffix += (
                    f" alt_replay=`{alt.get('best_replay_section', '')}`"
                    f"@{float(alt.get('best_replay_score') or 0.0):.6f}"
                )
            lines.append(
                f"- `{item['section']}` -> `{item.get('selected_kind', '')}` "
                f"({item.get('selected_tier', '')}){suffix}"
            )
    if compiler_observations:
        lines.append("")
        lines.append("## Compiler Observations")
        frontend_kinds = compiler_observations.elaboration_kind_counts
        if frontend_kinds:
            parts = [f"`{kind}`={count}" for kind, count in frontend_kinds.items()]
            lines.append(f"- elaboration kinds: {', '.join(parts)}")
        payload_kinds = compiler_observations.payload_completeness_kind_counts
        if payload_kinds:
            parts = [f"`{kind}`={count}" for kind, count in payload_kinds.items()]
            lines.append(f"- payload completeness kinds: {', '.join(parts)}")
        payload_tails = compiler_observations.payload_completeness_tail_policy_counts
        if payload_tails:
            parts = [f"`{kind}`={count}" for kind, count in payload_tails.items()]
            lines.append(f"- payload tail policies: {', '.join(parts)}")
        provenance_projection_rows = compiler_observations.provenance_projection_rows
        if provenance_projection_rows:
            lines.append("- provenance projection rows:")
            for item in provenance_projection_rows[:10]:
                rendered = _format_provenance_projection_row(item)
                if rendered:
                    lines.append(f"  - {rendered}")
        if (
            compiler_observations.sparse_slot_binding_count
            or compiler_observations.sparse_leftover_count
            or compiler_observations.sparse_leftover_labels
        ):
            leftover_labels = ", ".join(compiler_observations.sparse_leftover_labels) or "-"
            lines.append(
                "- sparse observations: "
                f"bindings={compiler_observations.sparse_slot_binding_count} "
                f"leftovers={compiler_observations.sparse_leftover_count} "
                f"leftover_slots={compiler_observations.sparse_leftover_slot_count} "
                f"leftover_labels=[{leftover_labels}]"
            )
        if compiler_observations.unowned_observation_rows:
            lines.append("- unowned observations:")
            for item in compiler_observations.unowned_observation_rows[:10]:
                lines.append(
                    "  - "
                    f"`{item.get('family', '')}` `{item.get('kind', '')}` "
                    f"stage=`{item.get('stage', '')}` source=`{item.get('source_statute', '')}` "
                    f"target=`{item.get('target_unit_kind', '')}` path=`{item.get('target_path', '')}` "
                    f"count={int(item.get('count', 0) or 0)}"
                )
        apply_helpers = compiler_observations.apply_helper_counts
        if apply_helpers:
            parts = [f"`{helper}`={count}" for helper, count in apply_helpers.items()]
            lines.append(f"- apply helpers: {', '.join(parts)}")
        for item in compiler_observations.section_bisect_rows_with_observation_support[:10]:
            frontend = ", ".join(item.elaboration_kinds) or "-"
            helpers = ", ".join(item.apply_helpers) or "-"
            bindings = ", ".join(item.sparse_slot_binding_labels) or "-"
            leftovers = item.sparse_leftover_slot_count
            leftover_labels = ", ".join(item.sparse_leftover_labels) or "-"
            blame_source = item.blame_source or "(none)"
            payload_kinds = ", ".join(item.payload_completeness_kinds) or "-"
            tail_policies = ", ".join(item.payload_completeness_tail_policies) or "-"
            lines.append(
                f"- `{item.section}` blame={blame_source} elaboration=[{frontend}] "
                f"payload=[{payload_kinds}] tails=[{tail_policies}] "
                f"bindings=[{bindings}] leftovers={leftovers} leftover_labels=[{leftover_labels}] "
                f"apply=[{helpers}]"
            )
    return "\n".join(lines).rstrip() + "\n"


def _write_markdown_output(path: str, bundles: List[Dict], *, oracle_only: bool = False) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    text = "\n\n---\n\n".join(
        _render_markdown_bundle(bundle, oracle_only=oracle_only).rstrip()
        for bundle in bundles
    ) + "\n"
    target.write_text(text, encoding="utf-8")
    return target




def _print_evidence_bundle(bundle: Dict) -> None:
    print(f"Statute      : {bundle['statute_id']}")
    print(f"Title        : {bundle['title']}")
    print(f"Mode         : {bundle['mode']}")
    print(f"Contract     : {bundle['proof_contract']['version']} ({bundle['proof_contract']['status']})")
    print(f"Scores       : overall={bundle['overall_score']:.3%}  section={bundle['section_score']:.3%}")
    strict_value = not bool(bundle.get("strict_fail_reasons") or [])
    print(f"Strict       : {'PASS' if strict_value else 'FAIL'}")
    if bundle["strict_fail_reasons"]:
        print(f"Fail reasons : {', '.join(bundle['strict_fail_reasons'])}")
    print(f"Primary tier : {bundle['primary_proof_tier']}")
    print(f"Tiers        : {', '.join(bundle['proof_tiers'])}")
    evidence_context_diagnostics = [
        item
        for item in bundle.get("evidence_context_diagnostics", []) or []
        if isinstance(item, dict)
    ]
    if evidence_context_diagnostics:
        print("Evidence context degraded:")
        for item in evidence_context_diagnostics:
            print(
                "  "
                f"{item.get('rail', '')} {item.get('exception_type', '')}: "
                f"{item.get('message', '')}"
            )
    compiler_observations = _coerce_compiler_observations(bundle.get("compiler_observations"))
    if any(
        (
            compiler_observations.elaboration_observation_count,
            compiler_observations.sparse_slot_binding_count,
            compiler_observations.sparse_leftover_count,
            compiler_observations.elaboration_projection_count,
            compiler_observations.apply_mutation_event_count,
            compiler_observations.provenance_projection_count,
            compiler_observations.elaboration_kind_counts,
            compiler_observations.payload_completeness_kind_counts,
            compiler_observations.payload_completeness_tail_policy_counts,
            compiler_observations.apply_helper_counts,
            compiler_observations.provenance_projection_rows,
            compiler_observations.section_bisect_rows_with_observation_support,
            compiler_observations.unowned_observation_rows,
        )
    ):
        unowned_count, unowned_family_summary = _summarize_unowned_observations(compiler_observations)
        print(
            "Compiler obs: "
            f"elaboration={compiler_observations.elaboration_observation_count} "
            f"bindings={compiler_observations.sparse_slot_binding_count} "
            f"leftovers={compiler_observations.sparse_leftover_count} "
            f"projected={compiler_observations.elaboration_projection_count} "
            f"apply={compiler_observations.apply_mutation_event_count} "
            f"provenance={compiler_observations.provenance_projection_count}"
        )
        if unowned_count:
            print(f"Unowned rows: {unowned_count}")
            print(f"  families: {unowned_family_summary}")
        if compiler_observations.unowned_observation_rows:
            print("Unowned obs:")
            for item in compiler_observations.unowned_observation_rows[:10]:
                target_coords = _format_unowned_observation_row(item)
                print(
                    f"  - {item.get('family', '')} {item.get('kind', '')} "
                    f"stage={item.get('stage', '')} source={item.get('source_statute', '')} "
                    f"target={item.get('target_unit_kind', '')} coords={target_coords} "
                    f"path={item.get('target_path', '')} "
                    f"count={int(item.get('count', 0) or 0)}"
                )
    print()

    print("Claims:")
    for claim in bundle["proof_claims"]:
        print(f"  {claim['tier']}  {claim['kind']}")
        print(f"    {claim['summary']}")
    print()

    if bundle.get("section_claims"):
        print("Section Claims:")
        for item in bundle["section_claims"][:20]:
            if not str(item.get("selected_kind") or ""):
                continue
            defeated = ", ".join(item.get("defeated_candidate_kinds", []) or []) or "-"
            rule = str(item.get("selected_inference_rule") or "") or "-"
            alt = item.get("alternative_replay_match") or {}
            alt_text = "-"
            if alt:
                alt_text = (
                    f"{alt.get('best_replay_section', '')}"
                    f"@{float(alt.get('best_replay_score') or 0.0):.6f}"
                )
            print(
                f"  {item['section']:<12} {item.get('selected_tier', ''):<24} "
                f"{item.get('selected_kind', '')} defeated=[{defeated}] via=[{rule}] "
                f"alt=[{alt_text}]"
            )
        print()

    print("Diagnoses:")
    for diagnosis, count in bundle["diagnosis_counts"].items():
        print(f"  {diagnosis:<22} {count}")
    print()

    if bundle["supporting_amendments"]:
        print("Supporting Amendments:")
        for item in bundle["supporting_amendments"][:20]:
            stats = (
                f"official={item['official_item_count']} "
                f"verified={item['verified_in_source_count']} "
                f"manual={item['manual_override_count']}"
            )
            print(f"  {item['amendment_id']:<12} {stats}")
    else:
        print("Supporting Amendments: none")
    print()

    if bundle.get("section_bisect"):
        print("Section Bisect:")
        for item in bundle["section_bisect"][:20]:
            first_drop = item.get("first_drop_source") or "(none)"
            print(
                f"  {item['section']:<12} baseline={item.get('baseline_score', 0.0):.1%} "
                f"first_drop={first_drop}"
            )
        print()

    if compiler_observations:
        provenance_projection_rows = compiler_observations.provenance_projection_rows
        if provenance_projection_rows:
            print("Provenance Projection Rows:")
            for item in provenance_projection_rows[:10]:
                rendered = _format_provenance_projection_row(item)
                if rendered:
                    print(f"  {rendered}")
            print()
        rows = compiler_observations.section_bisect_rows_with_observation_support
        if rows:
            print("Compiler Observation Support:")
            for item in rows[:10]:
                frontend = ", ".join(item.elaboration_kinds) or "-"
                helpers = ", ".join(item.apply_helpers) or "-"
                blame_source = item.blame_source or "(none)"
                print(
                    f"  {item.section:<12} blame={blame_source:<12} "
                    f"elaboration=[{frontend}] apply=[{helpers}]"
                )
            print()

    html_topology = bundle["html_topology"]
    if html_topology.get("noncommensurable_reason"):
        print(f"HTML/XML     : NONCOMMENSURABLE ({html_topology['noncommensurable_reason']})")
    elif html_topology.get("missing_from_xml") or html_topology.get("extra_in_xml"):
        print("HTML/XML     : TOPOLOGY DRIFT")
        if html_topology.get("missing_from_xml"):
            print(f"  Missing    : {', '.join(html_topology['missing_from_xml'])}")
        if html_topology.get("extra_in_xml"):
            print(f"  Extra      : {', '.join(html_topology['extra_in_xml'])}")
    else:
        print("HTML/XML     : no topology drift")


def _print_oracle_proof_bundle(bundle: Dict) -> None:
    print(f"Statute      : {bundle['statute_id']}")
    print(f"Title        : {bundle['title']}")
    print(f"Mode         : {bundle['mode']}")
    print(f"Proved       : {'YES' if bundle['proved'] else 'NO'}")
    print()
    if bundle["proof_claims"]:
        for claim in bundle["proof_claims"]:
            print(f"  {claim['kind']}: {claim['summary']}")
    else:
        print("No oracle-incorrectness proof claim derived from current evidence.")


def _print_review_summary(review: Dict) -> None:
    summary = _coerce_review_summary(review)
    if summary.artifact_count is not None:
        print(f"Artifacts     : {summary.artifact_count}")
    elif summary.statute_count is not None:
        print(f"Statutes      : {summary.statute_count}")
    print(f"Bundles       : {summary.bundle_count}")
    print(f"Selected      : {summary.selected_count}")
    active_filters = [
        f"{key}={value}"
        for key, value in summary.filters.items()
        if key != "limit" and str(value or "")
    ]
    if active_filters:
        print(f"Filters       : {', '.join(active_filters)}")
    print()

    print("Primary Tiers:")
    for tier, count in summary.by_primary_tier.items():
        print(f"  {tier:<34} {count}")
    print()

    if summary.by_display_primary_tier and summary.by_display_primary_tier != summary.by_primary_tier:
        print("Display Tiers:")
        for tier, count in summary.by_display_primary_tier.items():
            print(f"  {tier:<34} {count}")
        print()

    print("Claim Kinds:")
    for kind, count in summary.by_claim_kind.items():
        print(f"  {kind:<34} {count}")
    print()

    print("Section Claim Kinds:")
    for kind, count in summary.by_section_claim_kind.items():
        print(f"  {kind:<34} {count}")
    print()

    if summary.by_section_claim_inference_rule:
        print("Section Claim Rules:")
        for rule, count in summary.by_section_claim_inference_rule.items():
            print(f"  {rule:<34} {count}")
        print()

    if summary.by_defeated_section_claim_kind:
        print("Defeated Section Claim Kinds:")
        for kind, count in summary.by_defeated_section_claim_kind.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_defeated_section_claim_inference_rule:
        print("Defeated Section Claim Rules:")
        for rule, count in summary.by_defeated_section_claim_inference_rule.items():
            print(f"  {rule:<34} {count}")
        print()

    if summary.by_strict_fail_reason:
        print("Strict Fail Reasons:")
        for reason, count in summary.by_strict_fail_reason.items():
            print(f"  {reason:<34} {count}")
        print()

    if summary.by_elaboration_observation_kind:
        print("Elaboration Observation Kinds:")
        for kind, count in summary.by_elaboration_observation_kind.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_sparse_blocker_source:
        print("Sparse Blocker Sources:")
        for source, count in summary.by_sparse_blocker_source.items():
            print(f"  {source:<34} {count}")
        print()

    if summary.by_sparse_blocker_section:
        print("Sparse Blocker Sections:")
        for section, count in summary.by_sparse_blocker_section.items():
            print(f"  {section:<34} {count}")
        print()

    if summary.by_payload_completeness_kind:
        print("Payload Completeness Kinds:")
        for kind, count in summary.by_payload_completeness_kind.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_payload_tail_policy:
        print("Payload Tail Policies:")
        for kind, count in summary.by_payload_tail_policy.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_provenance_projection_kind:
        print("Provenance Projection Kinds:")
        for kind, count in summary.by_provenance_projection_kind.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_provenance_tag:
        print("Provenance Tags:")
        for tag, count in summary.by_provenance_tag.items():
            print(f"  {tag:<34} {count}")
        print()

    if summary.by_provenance_source_statute:
        print("Provenance Source Statutes:")
        for source_statute, count in summary.by_provenance_source_statute.items():
            print(f"  {source_statute:<34} {count}")
        print()

    if summary.by_source_pathology_code:
        print("Source Pathology Codes:")
        for code, count in summary.by_source_pathology_code.items():
            print(f"  {code:<34} {count}")
        print()

    if summary.by_source_proof_kind:
        print("Source Proof Kinds:")
        for kind, count in summary.by_source_proof_kind.items():
            print(f"  {kind:<34} {count}")
        print()

    if summary.by_source_pathology_source:
        print("Source Pathology Sources:")
        for source_statute, count in summary.by_source_pathology_source.items():
            print(f"  {source_statute:<34} {count}")
        print()

    if summary.by_source_pathology_target_label:
        print("Source Pathology Targets:")
        for target_label, count in summary.by_source_pathology_target_label.items():
            print(f"  {target_label:<34} {count}")
        print()

    if summary.by_source_pathology_diagnostic_reason:
        print("Source Pathology Diagnostic Reasons:")
        for reason, count in summary.by_source_pathology_diagnostic_reason.items():
            print(f"  {reason:<34} {count}")
        print()

    if summary.by_alternative_replay_section:
        print("Alternative Replay Sections:")
        for section, count in summary.by_alternative_replay_section.items():
            print(f"  {section:<34} {count}")
        print()

    if summary.by_html_noncommensurable_reason:
        print("HTML/XML Noncommensurable Reasons:")
        for reason, count in summary.by_html_noncommensurable_reason.items():
            print(f"  {reason:<34} {count}")
        print()

    if summary.by_evidence_context_degradation_rail:
        print("Evidence Context Degraded Rails:")
        for rail, count in summary.by_evidence_context_degradation_rail.items():
            print(f"  {rail:<34} {count}")
        print()

    if summary.by_evidence_context_degradation_exception:
        print("Evidence Context Degradation Exceptions:")
        for exception_type, count in summary.by_evidence_context_degradation_exception.items():
            print(f"  {exception_type:<34} {count}")
        print()

    if summary.by_mixed_replay_risk_reason:
        print("Mixed Replay Risk Reasons:")
        for reason, count in summary.by_mixed_replay_risk_reason.items():
            print(f"  {reason:<34} {count}")
        print()

    print("Top Rows:")
    for row in summary.rows:
        kinds = ", ".join(row.proof_kinds) or "-"
        section_kinds = ", ".join(row.selected_section_claim_kinds) or "-"
        defeated_kinds = ", ".join(row.defeated_section_claim_kinds) or "-"
        section_rules = ", ".join(row.selected_section_claim_rules) or "-"
        defeated_rules = ", ".join(row.defeated_section_claim_rules) or "-"
        mixed_replay = "yes" if row.mixed_replay_risk else "no"
        mixed_replay_reasons = ", ".join(row.mixed_replay_risk_reasons) or "-"
        frontend_obs = ", ".join(row.elaboration_observation_kinds) or "-"
        payload_kinds = ", ".join(row.payload_concern_kinds or row.payload_completeness_kinds) or "-"
        payload_tails = ", ".join(row.payload_concern_tail_policies or row.payload_tail_policies) or "-"
        provenance = _summarize_provenance_projection_rows(row.provenance_projection_rows)
        source_proof = ", ".join(row.source_proof_kinds) or "-"
        source_pathologies = _summarize_source_pathologies(row.source_pathologies)
        alternative_replay_sections = ", ".join(row.alternative_replay_sections) or "-"
        html_noncommensurable_reason = row.html_noncommensurable_reason or "-"
        evidence_context_rails = ", ".join(row.evidence_context_degradation_rails) or "-"
        leftover_labels = ", ".join(row.sparse_leftover_labels) or "-"
        sparse_blockers = ", ".join(
            f"{item.get('source_statute', '')}@{item.get('section', '')}"
            for item in row.sparse_blockers
            if str(item.get("source_statute", "") or "") or str(item.get("section", "") or "")
        ) or "-"
        print(
            f"  {row.statute_id:<12} {row.display_primary_tier or row.primary_proof_tier:<32} "
            f"claims=[{kinds}] sections=[{section_kinds}] defeated=[{defeated_kinds}] "
            f"section_rules=[{section_rules}] defeated_rules=[{defeated_rules}] "
            f"elaboration_obs=[{frontend_obs}] payload=[{payload_kinds}] tails=[{payload_tails}] "
            f"provenance=[{provenance}] source_proof=[{source_proof}] "
            f"source_pathology=[{source_pathologies}] "
            f"alternative_replay=[{alternative_replay_sections}] "
            f"html_noncommensurable=[{html_noncommensurable_reason}] "
            f"evidence_context_degraded={row.evidence_context_degradation_count} "
            f"evidence_context_rails=[{evidence_context_rails}] "
            f"sparse_bindings={row.sparse_slot_binding_count} "
            f"sparse_leftovers={row.sparse_leftover_count} "
            f"sparse_leftover_labels=[{leftover_labels}] "
            f"sparse_blockers=[{sparse_blockers}] "
            f"mixed_replay_risk={mixed_replay} mixed_replay_reasons=[{mixed_replay_reasons}]"
        )
