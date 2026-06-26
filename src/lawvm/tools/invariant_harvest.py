"""Shared replay invariant + lint harvesting for corpus audit tools.

Both ``scripts/audit_invariants.py`` and ``lawvm self-consistency`` replay
Finnish statutes and need the same projection of tree/product invariant
violations and replay lint warnings from replay metadata and findings.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

# Patterns for classifying violation strings produced by check_invariants()
_DUPLICATE_RE = re.compile(r"duplicate\s+(\w+):(\S+)", re.IGNORECASE)
_NORM_DUPLICATE_RE = re.compile(r"normalized-duplicate\s+(\w+):(\S+)", re.IGNORECASE)
# Possessive repeats + a tempered operand class around the literal ``>`` delimiter
# remove the catastrophic backtracking the earlier ``(\S+)\s+>\s+(\S+)`` /
# ``(\S+)\s+alongside\s+(\S+)`` shapes carried (the variable repeats could re-split
# the same run across the ``\s+`` separators). Match-identical to the old patterns
# over 400k fuzzed strings; the operand before ``>`` excludes ``>`` (a label never
# contains one), so the capture is unchanged on real violation messages.
_OUT_OF_ORDER_RE = re.compile(
    r"(\w++)\s+out of order:\s+([^\s>]++)\s+>\s+(\S++)", re.IGNORECASE
)
_UNEXPECTED_NESTING_RE = re.compile(r"unexpected\s+(\w+)\s+inside\s+(\w+)", re.IGNORECASE)
_MIXED_HIERARCHY_RE = re.compile(
    r"direct\s+(\S++)\s+alongside\s+(\S++)",
    re.IGNORECASE,
)
_ILLEGAL_EDGE_PAIRS = frozenset(
    {
        ("paragraph", "section"),
        ("subparagraph", "section"),
        ("subsection", "chapter"),
        ("paragraph", "chapter"),
        ("subparagraph", "chapter"),
    }
)

_LINT_FINDING_KINDS = frozenset(
    {
        "flattened_sublist_family_warning",
        "label_sequence_gap_warning",
    }
)

_TREE_BARRIER_CODES = frozenset(
    {
        "APPLY.TREE_INVARIANT_VIOLATION",
        "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
    }
)


@dataclass(frozen=True, slots=True)
class InvariantHarvestRecord:
    """One harvested tree/product invariant or lint warning."""

    violation_type: str
    path: str
    detail: str
    source: str
    adj_kind: str
    phase: str
    severity: Literal["violation", "warning"]
    surface: str = ""
    profile_id: str = ""
    replay_profile_id: str = ""


def classify_violation(violation: str) -> tuple[str, str, str]:
    """Return (violation_type, path, detail) for a violation string."""
    last_slash = violation.rfind("/")
    search_from = last_slash + 1 if last_slash != -1 else 0
    sep = violation.find(": ", search_from)
    if sep != -1:
        path = violation[:sep].strip()
        message = violation[sep + 2 :].strip()
    else:
        path = ""
        message = violation.strip()

    match = _DUPLICATE_RE.search(message)
    if match:
        return "duplicate_label", path, f"{match.group(1)}:{match.group(2)}"

    match = _NORM_DUPLICATE_RE.search(message)
    if match:
        return "normalized_duplicate", path, f"{match.group(1)}:{match.group(2)}"

    match = _OUT_OF_ORDER_RE.search(message)
    if match:
        return "sort_order", path, f"{match.group(1)}: {match.group(2)} > {match.group(3)}"

    match = _UNEXPECTED_NESTING_RE.search(message)
    if match:
        child_kind = match.group(1)
        parent_kind = match.group(2)
        detail = f"{child_kind} inside {parent_kind}"
        if (child_kind.lower(), parent_kind.lower()) in _ILLEGAL_EDGE_PAIRS:
            return "illegal_edge", path, detail
        return "nesting_violation", path, detail

    match = _MIXED_HIERARCHY_RE.search(message)
    if match:
        return "mixed_hierarchy", path, f"{match.group(1)} alongside {match.group(2)}"

    return "other", path, message[:200]


def classify_typed_tree_violation(record: dict[str, object]) -> tuple[str, str, str]:
    """Return audit classification from typed TreeInvariantViolation metadata."""
    kind = str(record.get("kind") or "")
    path = str(record.get("path") or "")
    child_kind = str(record.get("child_kind") or "")
    parent_kind = str(record.get("parent_kind") or "")
    label = str(record.get("label") or "")
    normalized_label = str(record.get("normalized_label") or "")
    previous_label = str(record.get("previous_label") or "")
    next_label = str(record.get("next_label") or "")

    if kind == "duplicate_label":
        return "duplicate_label", path, f"{child_kind}:{label}"
    if kind == "normalized_duplicate_label":
        return "normalized_duplicate", path, f"{child_kind}:{normalized_label}"
    if kind == "sort_order":
        return "sort_order", path, f"{child_kind}: {previous_label} > {next_label}"
    if kind == "unexpected_child_kind":
        detail = f"{child_kind} inside {parent_kind}"
        if (child_kind.lower(), parent_kind.lower()) in _ILLEGAL_EDGE_PAIRS:
            return "illegal_edge", path, detail
        return "nesting_violation", path, detail
    if kind == "mixed_hierarchy_child":
        container_kind = str(record.get("container_kind") or "container")
        container_label = str(record.get("container_label") or "")
        label = str(record.get("label") or "")
        child = f"{child_kind}:{label}" if label else child_kind
        container = (
            f"{container_kind}:{container_label}"
            if container_label
            else container_kind
        )
        return "mixed_hierarchy", path, f"{child} alongside {container}"

    message = str(record.get("message") or "")
    return "other", path, message[:200]


def _coerce_typed_tree_violation_records(raw: object) -> list[dict[str, object]]:
    if not isinstance(raw, list):
        return []
    records: list[dict[str, object]] = []
    for record in raw:
        if isinstance(record, dict):
            records.append({str(key): value for key, value in record.items()})
    return records


def replay_profile_ids_by_surface(raw: object) -> dict[str, str]:
    """Return replay-invariant profile ids keyed by tree surface name."""
    if not isinstance(raw, list):
        return {}
    grouped: dict[str, list[str]] = {}
    for profile in raw:
        if not isinstance(profile, Mapping):
            continue
        profile_record = {str(key): value for key, value in profile.items()}
        profile_id = str(profile_record.get("profile_id") or "")
        if not profile_id:
            continue
        tree_profiles = profile_record.get("tree_profiles")
        if not isinstance(tree_profiles, list | tuple):
            continue
        for tree_profile in tree_profiles:
            if not isinstance(tree_profile, Mapping):
                continue
            tree_profile_record = {str(key): value for key, value in tree_profile.items()}
            surface = str(tree_profile_record.get("surface") or "")
            if not surface:
                continue
            ids = grouped.setdefault(surface, [])
            if profile_id not in ids:
                ids.append(profile_id)
    return {surface: ",".join(ids) for surface, ids in grouped.items()}


def phase_from_surface(surface: str) -> str:
    """Map typed invariant row surface names to audit phase buckets."""
    if surface == "replay_fold_tree":
        return "replay_fold"
    if surface == "materialized_tree":
        return "materialized"
    return ""


def _append_record(
    records: list[InvariantHarvestRecord],
    seen: set[tuple[str, str, str, str]],
    *,
    violation_type: str,
    path: str,
    detail: str,
    source: str,
    adj_kind: str,
    phase: str,
    severity: Literal["violation", "warning"],
    surface: str = "",
    profile_id: str = "",
    replay_profile_id: str = "",
) -> None:
    key = (severity, violation_type, path, detail)
    if key in seen:
        return
    records.append(
        InvariantHarvestRecord(
            violation_type=violation_type,
            path=path,
            detail=detail,
            source=source,
            adj_kind=adj_kind,
            phase=phase,
            severity=severity,
            surface=surface,
            profile_id=profile_id,
            replay_profile_id=replay_profile_id,
        )
    )
    seen.add(key)


def harvest_replay_invariants(
    *,
    replay_meta: Mapping[str, object],
    findings: Any = (),
) -> list[InvariantHarvestRecord]:
    """Collect tree/product invariant violations and lint warnings from one replay."""
    records: list[InvariantHarvestRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    replay_profile_by_surface = replay_profile_ids_by_surface(
        replay_meta.get("replay_invariant_profiles")
    )

    for finding in findings or ():
        finding_kind = str(getattr(finding, "kind", "") or "")
        if finding_kind == "RUNTIME.VIOLATION":
            raw_detail = dict(getattr(finding, "detail", {}) or {})
            barrier_code = str(raw_detail.get("barrier_code") or "")
            if barrier_code not in _TREE_BARRIER_CODES:
                continue
            violation_str = str(
                raw_detail.get("violation") or raw_detail.get("message") or ""
            )
            phase = str(raw_detail.get("phase") or "")
            vtype, path, detail = classify_violation(violation_str)
            _append_record(
                records,
                seen,
                violation_type=vtype,
                path=path,
                detail=detail,
                source="finding_ledger",
                adj_kind=barrier_code,
                phase=phase,
                severity="violation",
            )
            continue

        if finding_kind in _LINT_FINDING_KINDS:
            raw_detail = dict(getattr(finding, "detail", {}) or {})
            lint_kind = str(raw_detail.get("kind") or finding_kind)
            path = str(raw_detail.get("path") or "")
            node_kind = str(raw_detail.get("node_kind") or "")
            detail = f"{node_kind}:{lint_kind}" if node_kind else lint_kind
            _append_record(
                records,
                seen,
                violation_type=lint_kind,
                path=path,
                detail=detail,
                source="finding_ledger_lint",
                adj_kind=finding_kind,
                phase=str(raw_detail.get("phase") or "materialized"),
                severity="warning",
            )

    typed_replay_violations = _coerce_typed_tree_violation_records(
        replay_meta.get("typed_invariant_violations")
    )
    for record in typed_replay_violations:
        vtype, path, detail = classify_typed_tree_violation(record)
        surface = str(record.get("surface") or "")
        _append_record(
            records,
            seen,
            violation_type=vtype,
            path=path,
            detail=detail,
            source="replay_meta_tree",
            adj_kind="APPLY.TREE_INVARIANT_VIOLATION",
            phase=phase_from_surface(surface),
            severity="violation",
            surface=surface,
            profile_id=str(record.get("profile_id") or ""),
            replay_profile_id=replay_profile_by_surface.get(surface, ""),
        )

    typed_product_violations: list[dict[str, object]] = []
    typed_product_raw = replay_meta.get("typed_product_tree_invariant_violations")
    if isinstance(typed_product_raw, dict):
        for product_phase, phase_records in typed_product_raw.items():
            for record in _coerce_typed_tree_violation_records(phase_records):
                record = dict(record)
                record["product_phase"] = str(product_phase)
                typed_product_violations.append(record)
    for record in typed_product_violations:
        vtype, path, detail = classify_typed_tree_violation(record)
        product_phase = str(record.get("product_phase") or "")
        surface = str(record.get("surface") or product_phase)
        _append_record(
            records,
            seen,
            violation_type=vtype,
            path=path,
            detail=detail,
            source="replay_meta_product",
            adj_kind="APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            phase=phase_from_surface(surface) or phase_from_surface(product_phase),
            severity="violation",
            surface=surface,
            profile_id=str(record.get("profile_id") or ""),
            replay_profile_id=replay_profile_by_surface.get(surface, ""),
        )

    for source_name, barrier_code, violations_raw in (
        (
            "replay_meta_tree",
            "APPLY.TREE_INVARIANT_VIOLATION",
            None if typed_replay_violations else replay_meta.get("invariant_violations"),
        ),
        (
            "replay_meta_product",
            "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
            replay_meta.get("product_invariant_violations"),
        ),
    ):
        if not isinstance(violations_raw, list):
            continue
        for raw_violation in violations_raw:
            violation_str = str(raw_violation)
            if (
                source_name == "replay_meta_product"
                and typed_product_violations
                and (
                    violation_str.startswith("replay_fold_tree:")
                    or violation_str.startswith("materialized_tree:")
                )
            ):
                continue
            vtype, path, detail = classify_violation(violation_str)
            _append_record(
                records,
                seen,
                violation_type=vtype,
                path=path,
                detail=detail,
                source=source_name,
                adj_kind=barrier_code,
                phase="",
                severity="violation",
            )

    for meta_key, adj_kind in (
        ("flattened_sublist_warnings", "flattened_sublist_family_warning"),
        ("label_sequence_gap_warnings", "label_sequence_gap_warning"),
    ):
        warnings_raw = replay_meta.get(meta_key)
        if not isinstance(warnings_raw, list):
            continue
        for warning in warnings_raw:
            if not isinstance(warning, Mapping):
                continue
            warning_dict = cast(dict[str, object], warning)
            lint_kind = str(warning_dict.get("kind") or meta_key)
            path = str(warning_dict.get("path") or "")
            node_kind = str(warning_dict.get("node_kind") or "")
            detail = f"{node_kind}:{lint_kind}" if node_kind else lint_kind
            _append_record(
                records,
                seen,
                violation_type=lint_kind,
                path=path,
                detail=detail,
                source="replay_meta_lint",
                adj_kind=adj_kind,
                phase=str(warning_dict.get("phase") or "materialized"),
                severity="warning",
            )

    return records


def actionability_for_record(
    record: InvariantHarvestRecord,
    *,
    chain_length: str = "",
    phase_scope: str = "",
    detector_family: str = "",
) -> str:
    """Classify whether a harvested row is worth fixing versus audit noise."""
    if record.severity == "warning":
        if record.violation_type.startswith("label_sequence_"):
            return "informational"
        return "informational"

    if detector_family in {
        "pre_dedup_duplicate_label",
        "base_text_shape",
        "base_text_flattened_sublist_family",
        "editorial_flat_hcontainer",
    }:
        return "benign"

    if record.violation_type in {"duplicate_label", "normalized_duplicate"}:
        if record.source in {"replay_meta_product", "replay_meta_tree"} and phase_scope in {
            "replay_fold_only",
            "materialized_only",
            "both",
        }:
            return "benign"

    if record.violation_type == "mixed_hierarchy":
        if detector_family in {"base_text_shape", "editorial_flat_hcontainer"}:
            return "benign"
        if chain_length == "0":
            return "benign"
        return "investigate"

    if record.violation_type.startswith("label_sequence_"):
        return "informational"

    if chain_length.strip().lstrip("-").isdigit() and int(chain_length) > 0:
        return "fixable"
    return "investigate"


def records_to_audit_rows(
    statute_id: str,
    records: list[InvariantHarvestRecord],
    *,
    chain_length: str = "",
    oracle_suspect: str = "",
) -> list[dict[str, str]]:
    """Project harvested records into ``audit_invariants`` CSV row dicts."""
    rows: list[dict[str, str]] = []
    for record in records:
        rows.append(
            {
                "statute_id": statute_id,
                "audit_status": "violation" if record.severity == "violation" else "warning",
                "violation_type": record.violation_type,
                "path": record.path,
                "detail": record.detail,
                "source": record.source,
                "adj_kind": record.adj_kind,
                "phase": record.phase,
                "surface": record.surface,
                "profile_id": record.profile_id,
                "replay_profile_id": record.replay_profile_id,
                "chain_length": chain_length,
                "oracle_suspect": oracle_suspect,
                "actionability": "",
                "detector_family": "",
                "phase_scope": "",
                "inferred_phase": "",
            }
        )
    return rows


def records_to_self_consistency_rows(
    statute_id: str,
    records: list[InvariantHarvestRecord],
) -> list[dict[str, str]]:
    """Project harvested records into ``self-consistency`` signal row dicts."""
    rows: list[dict[str, str]] = []
    for record in records:
        signal_type = (
            "invariant_violation"
            if record.severity == "violation"
            else "invariant_lint_warning"
        )
        description = f"{record.path}: {record.detail}" if record.path else record.detail
        rows.append(
            {
                "statute_id": statute_id,
                "amendment_id": "",
                "signal_type": signal_type,
                "category": record.violation_type,
                "description": description,
                "target_scope": record.path,
                "reason": record.detail,
            }
        )
    return rows
