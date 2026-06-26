"""Finland corpus invariant audit worker and classifiers."""
from __future__ import annotations

import traceback
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from lawvm.core.invariant_surface_matrix import FI_REPLAY_FOLD_SURFACE
from lawvm.tools.invariant_harvest import (
    actionability_for_record,
    classify_typed_tree_violation,
    classify_violation,
    harvest_replay_invariants,
    InvariantHarvestRecord,
    records_to_audit_rows,
)


def _infer_phase(row: dict[str, str]) -> str:
    explicit_phase = str(row.get("phase") or "").strip()
    if explicit_phase:
        return explicit_phase
    adj_kind = str(row.get("adj_kind") or "")
    source = str(row.get("source") or "")
    if adj_kind == "APPLY.TREE_INVARIANT_VIOLATION":
        return "replay_fold"
    if adj_kind == "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION":
        return "materialized"
    if source == "replay_meta_tree":
        return "replay_fold"
    if source == "replay_meta_product":
        return "materialized"
    if source in {"replay_meta_lint", "finding_ledger_lint"}:
        return "materialized"
    return "unknown"


def _phase_scope_for(phases: set[str]) -> str:
    if not phases:
        return "unknown"
    norm = {phase for phase in phases if phase}
    if norm == {"replay_fold"}:
        return "replay_fold_only"
    if norm == {"materialized"}:
        return "materialized_only"
    if "replay_fold" in norm and "materialized" in norm:
        return "both"
    if len(norm) == 1:
        return next(iter(norm))
    return "mixed"


def _detector_family_for(row: dict[str, str]) -> str:
    violation_type = str(row.get("violation_type") or "")
    path = str(row.get("path") or "")
    detail = str(row.get("detail") or "")
    source = str(row.get("source") or "")
    phase_scope = str(row.get("phase_scope") or "")
    chain_length = str(row.get("chain_length") or "").strip()

    if chain_length == "0" and violation_type in {
        "duplicate_label",
        "normalized_duplicate",
        "illegal_edge",
        "nesting_violation",
    }:
        if (
            ("paragraph:" in detail or "subparagraph:" in detail)
            and "/subsection:" in path
        ):
            return "base_text_flattened_sublist_family"
        return "base_text_shape"

    if violation_type == "illegal_edge":
        if "paragraph inside section" in detail or "subparagraph inside section" in detail:
            return "illegal_edge_section_child"
        return "illegal_edge"

    if violation_type in {"duplicate_label", "normalized_duplicate"}:
        if (
            ("paragraph:" in detail or "subparagraph:" in detail)
            and "/subsection:" in path
        ):
            return "flattened_sublist_family"
        if source in {"finding_ledger", "replay_meta_tree", "replay_meta_product"} and (
            phase_scope in {"replay_fold_only", "both"}
            or source == "replay_meta_product"
        ):
            return "pre_dedup_duplicate_label"

    if violation_type == "nesting_violation":
        return "generic_nesting_violation"
    if violation_type == "sort_order":
        return "sort_order"
    if violation_type.startswith("flattened_sublist_"):
        return "flattened_sublist_family"
    if violation_type.startswith("label_sequence_"):
        return "label_sequence_gap"
    if violation_type == "mixed_hierarchy":
        if chain_length == "0":
            if path == "body" and (
                "alongside part:" in detail or "alongside chapter:" in detail
            ):
                return "editorial_flat_hcontainer"
            return "base_text_shape"
        return "mixed_hierarchy"
    return violation_type or "other"


def _record_from_audit_row(row: dict[str, str]) -> InvariantHarvestRecord:
    severity = "warning" if row.get("audit_status") == "warning" else "violation"
    return InvariantHarvestRecord(
        violation_type=str(row.get("violation_type") or ""),
        path=str(row.get("path") or ""),
        detail=str(row.get("detail") or ""),
        source=str(row.get("source") or ""),
        adj_kind=str(row.get("adj_kind") or ""),
        phase=str(row.get("phase") or ""),
        severity=severity,
        surface=str(row.get("surface") or ""),
        profile_id=str(row.get("profile_id") or ""),
        replay_profile_id=str(row.get("replay_profile_id") or ""),
    )


def annotate_phase_scope(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Annotate each row with inferred phase, scope, detector family, actionability."""
    grouped_phases: dict[tuple[str, str, str, str], set[str]] = {}
    for row in rows:
        inferred_phase = _infer_phase(row)
        row["inferred_phase"] = inferred_phase
        key = (
            row["statute_id"],
            row["violation_type"],
            row["path"],
            row["detail"],
        )
        grouped_phases.setdefault(key, set()).add(inferred_phase)

    for row in rows:
        key = (
            row["statute_id"],
            row["violation_type"],
            row["path"],
            row["detail"],
        )
        row["phase_scope"] = _phase_scope_for(grouped_phases.get(key, set()))
        row["detector_family"] = _detector_family_for(row)
        row["actionability"] = actionability_for_record(
            _record_from_audit_row(row),
            chain_length=str(row.get("chain_length") or ""),
            phase_scope=row["phase_scope"],
            detector_family=row["detector_family"],
        )
    return rows


def _replay_profile_ids_by_surface(raw: object) -> dict[str, str]:
    if not isinstance(raw, list):
        return {}
    grouped: dict[str, list[str]] = {}
    for profile in raw:
        if not isinstance(profile, dict):
            continue
        profile_dict = cast(dict[str, object], profile)
        surface = str(profile_dict.get("tree_surface") or profile_dict.get("surface_id") or "")
        profile_id = str(profile_dict.get("profile_id") or "")
        if surface and profile_id:
            grouped.setdefault(surface, []).append(profile_id)
    return {surface: ids[0] for surface, ids in grouped.items() if ids}


def _record_profile_gap_if_missing(replay_meta: dict[str, object]) -> None:
    replay_profile_by_surface = _replay_profile_ids_by_surface(
        replay_meta.get("replay_invariant_profiles")
    )
    expected_surface = FI_REPLAY_FOLD_SURFACE.surface_id
    expected_profile = FI_REPLAY_FOLD_SURFACE.replay_profile.profile_id
    if expected_surface not in replay_profile_by_surface:
        gaps_raw = replay_meta.get("audit_profile_gaps")
        gaps = list(gaps_raw) if isinstance(gaps_raw, list) else []
        gaps.append({"surface": expected_surface, "expected_profile_id": expected_profile})
        replay_meta["audit_profile_gaps"] = gaps


def audit_one_statute(norm_id: str) -> list[dict[str, str]]:
    """Replay one statute and collect tree/product invariant violations."""
    try:
        from lawvm.finland.replay_entrypoint import replay_xml
        from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks
        from lawvm.tools.replay_plan import build_replay_plan_inspection

        plan_bundle = build_replay_plan_inspection(
            SimpleNamespace(
                statute_id=norm_id,
                mode="official_consolidation",
                strict=False,
                oracle_selector_mode="bench_comparable",
            )
        )
        chain_length = str(len(plan_bundle.get("amendment_chain") or []))
        oracle_suspect = str(plan_bundle.get("oracle_suspect") or "")

        replay_meta: dict[str, object] = {}
        replay_result = replay_xml(
            request=ReplayXmlRequest(
                parent_id=norm_id,
                mode="legal_pit",
                quiet=True,
            ),
            sinks=ReplayXmlSinks(replay_meta_out=replay_meta),
        )
        _record_profile_gap_if_missing(replay_meta)

        records = harvest_replay_invariants(
            replay_meta=replay_meta,
            findings=getattr(replay_result, "findings", ()),
        )
        return records_to_audit_rows(
            norm_id,
            records,
            chain_length=chain_length,
            oracle_suspect=oracle_suspect,
        )

    except Exception:
        tb = traceback.format_exc().strip().splitlines()
        short_err = " | ".join(line.strip() for line in tb[-2:] if line.strip())
        return [{
            "statute_id": norm_id,
            "audit_status": "error",
            "violation_type": "ERROR",
            "path": "",
            "detail": short_err[:400],
            "source": "compile_error",
            "adj_kind": "",
            "phase": "",
            "chain_length": "",
            "oracle_suspect": "",
        }]


def _normalize_id(raw_id: str) -> str:
    if "-" in raw_id:
        parts = raw_id.rsplit("-", 1)
        if parts[1].isdigit():
            return parts[0]
    return raw_id


def load_corpus(corpus_path: Path) -> list[str]:
    """Load statute IDs from a bench CSV or plain-text list."""
    if corpus_path.suffix.lower() == ".csv":
        from lawvm.tools.bench import _load_corpus

        return [sid for _, sid in _load_corpus(str(corpus_path))]

    ids: list[str] = []
    seen: set[str] = set()
    with corpus_path.open(encoding="utf-8") as handle:
        for line in handle:
            sid = line.strip()
            if sid and not sid.startswith("#"):
                normalized = _normalize_id(sid)
                if normalized not in seen:
                    seen.add(normalized)
                    ids.append(normalized)
    return ids


__all__ = [
    "annotate_phase_scope",
    "audit_one_statute",
    "classify_typed_tree_violation",
    "classify_violation",
    "load_corpus",
]

# Back-compat aliases for scripts/tests
_classify_violation = classify_violation
_classify_typed_tree_violation = classify_typed_tree_violation
_annotate_phase_scope = annotate_phase_scope
_audit_one = audit_one_statute
