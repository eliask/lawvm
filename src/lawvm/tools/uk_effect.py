"""lawvm uk-effect -- inspect one UK effects-feed row end to end.

Archive-backed only. Shows the effect metadata, the extracted affecting-act
source node, and the compiled operations for one effect_id.
"""

from __future__ import annotations

import hashlib
import json
import sys
from lxml import etree as ET
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, NamedTuple, Optional

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.uk_legislation.compiled_effect_facts import uk_compiled_effect_facts
from lawvm.uk_legislation.source_state import (
    uk_affecting_act_xml_missing_rejection,
    uk_affecting_act_xml_parse_rejection,
    uk_affecting_act_xml_too_small_rejection,
    uk_source_parse_observations_from_ir,
    uk_source_xml_parse_rejection,
    uk_source_state_wire_tuple as _source_state,
)

if TYPE_CHECKING:
    import argparse
    from lawvm.core.ir import LegalAddress
    from lawvm.core.ir import IRStatute

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"
_DEFAULT_APPLICABILITY_MODE = "effective_date_plus_feed_applied"


class _EIDPresence(NamedTuple):
    eid: str
    base_present: bool
    oracle_present: bool


class _DescendantPresence(NamedTuple):
    base_present: bool
    oracle_present: bool


class _TargetShape(NamedTuple):
    has_text: bool
    has_children: bool
    texts: list[str]


def _tag(el: ET._Element) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _text_snippet(el: Optional[ET._Element], *, limit: int = 300) -> str:
    if el is None:
        return ""
    text = " ".join(t.strip() for t in el.itertext() if t and t.strip())  # type: ignore[union-attr]  # ty: ignore[no-matching-overload]
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _element_id(el: Optional[ET._Element]) -> str:
    if el is None:
        return ""
    return el.get("id") or el.get("eId") or ""


def _fmt_target(target) -> str:  # noqa: ANN001
    addr = "/".join(f"{kind}:{label}" for kind, label in target.path) or str(target)
    special = target.special
    if special is not None:
        special_value = special.value
        if special_value:
            addr = f"{addr}/{special_value}"
    return addr


def _print_payload(node, *, indent: str = "    ") -> None:  # noqa: ANN001
    label = f" {node.label}" if node.label else ""
    snippet = " ".join((node.text or "").split())
    if len(snippet) > 100:
        snippet = snippet[:97] + "..."
    if snippet:
        print(f"{indent}- {node.kind}{label}: {snippet}")
    else:
        print(f"{indent}- {node.kind}{label}")
    for child in node.children:
        _print_payload(child, indent=indent + "  ")


def _payload_jsonable(node) -> dict[str, Any]:  # noqa: ANN001
    return {
        "kind": str(node.kind),
        "label": node.label or "",
        "text": node.text or "",
        "attrs": dict(node.attrs),
        "children": [_payload_jsonable(child) for child in node.children],
    }


def _text_patch_jsonable(op) -> dict[str, Any] | None:  # noqa: ANN001
    patch = op.text_patch
    if patch is None:
        return None
    return {
        "kind": patch.kind.value,
        "match_text": patch.selector.match_text,
        "replacement": patch.replacement or "",
        "occurrence": patch.selector.occurrence,
    }


def rule_counts(rejections: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        rule_id = str(rejection.get("rule_id") or "unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def lowering_rejection_rule_counts(rejections: list[dict[str, Any]]) -> dict[str, int]:
    return rule_counts(rejections)


def lowering_observation_rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return rule_counts(rows)


def _blocking_rows(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if is_blocking_compile_record(row))


def affecting_act_xml_missing_rejection(effect) -> dict[str, Any]:  # noqa: ANN001
    act_id = str(getattr(effect, "affecting_act_id", "") or "")
    return uk_affecting_act_xml_missing_rejection(
        effect_id=str(getattr(effect, "effect_id", "") or ""),
        affecting_act_id=act_id,
        locator=f"{_LEG_BASE}/{act_id}/data.xml" if act_id else "",
    )


def affecting_act_xml_parse_rejection(effect, exc: Exception) -> dict[str, Any]:  # noqa: ANN001
    act_id = str(getattr(effect, "affecting_act_id", "") or "")
    return uk_affecting_act_xml_parse_rejection(
        effect_id=str(getattr(effect, "effect_id", "") or ""),
        affecting_act_id=act_id,
        locator=f"{_LEG_BASE}/{act_id}/data.xml" if act_id else "",
        exc=exc,
    )


def affecting_act_xml_too_small_rejection(effect, *, source_size: int) -> dict[str, Any]:  # noqa: ANN001
    act_id = str(getattr(effect, "affecting_act_id", "") or "")
    return uk_affecting_act_xml_too_small_rejection(
        effect_id=str(getattr(effect, "effect_id", "") or ""),
        affecting_act_id=act_id,
        locator=f"{_LEG_BASE}/{act_id}/data.xml" if act_id else "",
        source_size=source_size,
    )


def blocking_lowering_rejection_rule_counts(
    rejections: list[dict[str, Any]] | tuple[dict[str, Any], ...],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for rejection in rejections:
        if not is_blocking_compile_record(rejection):
            continue
        rule_id = str(rejection.get("rule_id") or "unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def has_blocking_lowering_rejection(rejections: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> bool:
    return any(is_blocking_compile_record(rejection) for rejection in rejections)


def _manual_compile_claim_template_for_effect_report(
    *,
    statute_id: str,
    effect,  # noqa: ANN001
    source_pathology: str,
    extracted: Optional[ET._Element],
    lowering_rejections: list[dict[str, Any]],
    manual_frontier: dict[str, Any],
    show_text: bool,
) -> dict[str, Any]:
    from lawvm.tools.uk_claim_templates import manual_compile_suggested_claim_template

    status = str(manual_frontier.get("status") or "")
    rule_id = str(manual_frontier.get("rule_id") or "")
    if status not in {
        "deterministic_frontend_candidate",
        "manual_compile_candidate",
        "source_insufficient",
    } or not rule_id:
        return {}
    summary = SimpleNamespace(
        source_pathology=source_pathology or "",
        source_extracted_text_preview=_text_snippet(
            extracted,
            limit=100000 if show_text else 300,
        ),
        lowering_rejections=tuple(lowering_rejections),
        manual_compile_status=status,
        manual_compile_rule_id=rule_id,
    )
    row = SimpleNamespace(effect=effect, summary=summary)
    return manual_compile_suggested_claim_template(statute_id=statute_id, row=row)


def print_lowering_rejections(rejections: list[dict[str, Any]], *, prefix: str = "") -> None:
    lowering_observation_rows = tuple(dict(item) for item in rejections)
    lowering_rejection_rows = _blocking_rows(lowering_observation_rows)
    print(f"{prefix}Lowering observations: {len(rejections)}")
    for rule_id, count in lowering_observation_rule_counts(rejections).items():
        print(f"{prefix}  {rule_id}: {count}")
    print(f"{prefix}Lowering rejections: {len(lowering_rejection_rows)}")
    for rule_id, count in lowering_rejection_rule_counts(list(lowering_rejection_rows)).items():
        print(f"{prefix}  {rule_id}: {count}")
    blocking_counts = blocking_lowering_rejection_rule_counts(lowering_rejection_rows)
    if blocking_counts:
        print(f"{prefix}Blocking lowering rejections: {sum(blocking_counts.values())}")
        for rule_id, count in blocking_counts.items():
            print(f"{prefix}  {rule_id}: {count}")


def uk_effect_report_jsonable(  # noqa: PLR0913
    *,
    statute_id: str,
    effect,  # noqa: ANN001
    source_pathology: str,
    extracted: Optional[ET._Element],
    lowering_rejections: list[dict[str, Any]],
    compare_shape: str,
    candidate: bool,
    op_rows: list[dict[str, Any]],
    applicability_mode: str = _DEFAULT_APPLICABILITY_MODE,
    show_text: bool = False,
    parse_rejections: tuple[dict[str, Any], ...] = (),
    source_surface: dict[str, Any] | None = None,
    source_acquisition_rejections: tuple[dict[str, Any], ...] = (),
    source_parse_observations: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    from lawvm.uk_legislation.execution_authorization import (
        uk_execution_authorization_from_manual_frontier,
    )
    from lawvm.uk_legislation.frontier_work_items import (
        uk_frontier_work_item_from_manual_frontier_row,
    )
    from lawvm.uk_legislation.phase_discipline import uk_phase_owner_for_manual_frontier
    from lawvm.uk_legislation.source_adjudication import classify_uk_manual_compile_frontier

    source_acquisition_observation_rows = tuple(
        dict(item) for item in source_acquisition_rejections
    )
    source_acquisition_rejection_rows = _blocking_rows(source_acquisition_observation_rows)
    lowering_observation_rows = tuple(dict(item) for item in lowering_rejections)
    lowering_rejection_rows = _blocking_rows(lowering_observation_rows)
    parse_observation_rows = tuple(dict(item) for item in parse_rejections)
    parse_rejection_rows = _blocking_rows(parse_observation_rows)
    source_parse_observation_rows = tuple(dict(item) for item in source_parse_observations)
    source_parse_rejection_rows = _blocking_rows(source_parse_observation_rows)
    manual_frontier = classify_uk_manual_compile_frontier(
        effect_type=effect.effect_type or "",
        source_pathology=source_pathology,
        extracted_tag=_tag(extracted) if extracted is not None else "",
        extracted_text=_text_snippet(extracted, limit=100000 if show_text else 300),
        lowering_rejections=lowering_rejections,
        compiled_op_count=len(op_rows),
        replay_applicable=effect.is_applicable_for_replay(applicability_mode=applicability_mode),
        structural_for_replay=effect.is_structural_for_replay(applicability_mode=applicability_mode),
        compare_shape=compare_shape,
    )
    suggested_claim_template = _manual_compile_claim_template_for_effect_report(
        statute_id=statute_id,
        effect=effect,
        source_pathology=source_pathology,
        extracted=extracted,
        lowering_rejections=lowering_rejections,
        manual_frontier=manual_frontier,
        show_text=show_text,
    )
    manual_frontier_owner_phase = uk_phase_owner_for_manual_frontier(
        manual_compile_status=manual_frontier["status"],
        manual_compile_rule_id=manual_frontier["rule_id"],
        source_pathology=source_pathology or "",
    )
    execution_authorization = uk_execution_authorization_from_manual_frontier(
        manual_compile_status=manual_frontier["status"],
        manual_compile_rule_id=manual_frontier["rule_id"],
        owner_phase=manual_frontier_owner_phase,
    ).to_dict()
    frontier_work_item: dict[str, Any] = {}
    if execution_authorization["replay_authorized"] is False:
        frontier_work_item = uk_frontier_work_item_from_manual_frontier_row(
            {
                "statute_id": statute_id,
                "effect_id": effect.effect_id,
                "affected_uri": str(getattr(effect, "affected_uri", "") or ""),
                "affecting_uri": str(getattr(effect, "affecting_uri", "") or ""),
                "affecting_act_id": effect.affecting_act_id,
                "affected_provisions": effect.affected_provisions,
                "affecting_provisions": effect.affecting_provisions,
                "manual_compile_status": manual_frontier["status"],
                "manual_compile_rule_id": manual_frontier["rule_id"],
                "owner_phase": manual_frontier_owner_phase,
                "source": {
                    "text_preview": _text_snippet(
                        extracted,
                        limit=100000 if show_text else 300,
                    ),
                },
                "target_context": {
                    "surface": "effect_feed_affected_provisions",
                    "affected_provisions": effect.affected_provisions,
                    "resolver_eids": [
                        str(row["resolver_eid"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "compare_shape": compare_shape,
                },
                "compare": {
                    "shape": compare_shape,
                    "resolver_eids": [
                        str(row["resolver_eid"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "base_target_hits": [
                        bool(row["base_target_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "oracle_target_hits": [
                        bool(row["oracle_target_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "base_descendant_hits": [
                        bool(row["base_descendant_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "oracle_descendant_hits": [
                        bool(row["oracle_descendant_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "base_parent_hits": [
                        bool(row["base_parent_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                    "oracle_parent_hits": [
                        bool(row["oracle_parent_present"])
                        for row in op_rows
                        if row["resolver_eid"]
                    ],
                },
                "suggested_claim_template": suggested_claim_template,
                "executable": execution_authorization["executable"],
                "replay_authorized": execution_authorization["replay_authorized"],
                "authorization_status": execution_authorization[
                    "authorization_status"
                ],
                "required_proofs": execution_authorization["required_proofs"],
                "safe_default": execution_authorization["safe_default"],
                "forbidden_shortcuts": execution_authorization[
                    "forbidden_shortcuts"
                ],
            }
        ).to_dict()
    legacy_payload = {
        "report_kind": "uk_effect_frontier_report",
        "statute_id": statute_id,
        "applicability_mode": applicability_mode,
        "source_surface": dict(source_surface or {}),
        "effect": {
            "effect_id": effect.effect_id,
            "effect_type": effect.effect_type or "",
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "modified": effect.modified,
            "effective_date": effect.effective_date or "",
            "applied": effect.applied,
            "requires_applied": effect.requires_applied,
            "metadata_only": bool(getattr(effect, "metadata_only", False)),
            "replay_applicable": effect.is_applicable_for_replay(applicability_mode=applicability_mode),
            "structural": effect.is_structural,
            "structural_for_replay": effect.is_structural_for_replay(applicability_mode=applicability_mode),
        },
        "source": {
            "pathology": source_pathology or "",
            "extracted": extracted is not None,
            "tag": _tag(extracted) if extracted is not None else "",
            "id": _element_id(extracted),
            "text": _text_snippet(extracted, limit=100000 if show_text else 300),
        },
        "manual_compile_frontier": manual_frontier,
        "execution_authorization": execution_authorization,
        "frontier_work_item": frontier_work_item,
        "suggested_claim_template_status": (
            "available" if suggested_claim_template else "not_available"
        ),
        "suggested_claim_template": suggested_claim_template,
        "lowering": {
            "compiled_op_count": len(op_rows),
            "observation_count": len(lowering_observation_rows),
            "observation_rule_counts": lowering_observation_rule_counts(
                list(lowering_observation_rows)
            ),
            "observations": list(lowering_observation_rows),
            "rejection_count": len(lowering_rejection_rows),
            "blocking_rejection_count": len(lowering_rejection_rows),
            "has_blocking_rejection": bool(lowering_rejection_rows),
            "rejection_rule_counts": lowering_rejection_rule_counts(list(lowering_rejection_rows)),
            "blocking_rejection_rule_counts": blocking_lowering_rejection_rule_counts(
                lowering_rejection_rows
            ),
            "rejections": list(lowering_rejection_rows),
        },
        "compare": {
            "shape": compare_shape,
            "resolver_eids": [str(row["resolver_eid"]) for row in op_rows if row["resolver_eid"]],
            "base_target_hits": [bool(row["base_target_present"]) for row in op_rows if row["resolver_eid"]],
            "oracle_target_hits": [bool(row["oracle_target_present"]) for row in op_rows if row["resolver_eid"]],
            "base_descendant_hits": [
                bool(row["base_descendant_present"]) for row in op_rows if row["resolver_eid"]
            ],
            "oracle_descendant_hits": [
                bool(row["oracle_descendant_present"]) for row in op_rows if row["resolver_eid"]
            ],
            "base_parent_hits": [bool(row["base_parent_present"]) for row in op_rows if row["resolver_eid"]],
            "oracle_parent_hits": [bool(row["oracle_parent_present"]) for row in op_rows if row["resolver_eid"]],
        },
        "effect_feed_parse_rejections": {
            "count": len(parse_rejection_rows),
            "rule_counts": rule_counts(list(parse_rejection_rows)),
            "rows": list(parse_rejection_rows),
        },
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_observation_rule_counts": rule_counts(list(parse_observation_rows)),
        "effect_feed_observations": list(parse_observation_rows),
        "source_parse_rejections": {
            "count": len(source_parse_rejection_rows),
            "rule_counts": rule_counts(list(source_parse_rejection_rows)),
            "rows": list(source_parse_rejection_rows),
        },
        "source_parse_observation_count": len(source_parse_observation_rows),
        "source_parse_observation_rule_counts": rule_counts(list(source_parse_observation_rows)),
        "source_parse_observations": list(source_parse_observation_rows),
        "source_acquisition_rejections": {
            "count": len(source_acquisition_rejection_rows),
            "rule_counts": rule_counts(list(source_acquisition_rejection_rows)),
            "rows": list(source_acquisition_rejection_rows),
        },
        "source_acquisition_observation_count": len(source_acquisition_observation_rows),
        "source_acquisition_observation_rule_counts": rule_counts(
            list(source_acquisition_observation_rows)
        ),
        "source_acquisition_observations": list(source_acquisition_observation_rows),
        "candidate": candidate,
        "ops": op_rows,
    }
    summary = {
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "effect_type": effect.effect_type or "",
        "applicability_mode": applicability_mode,
        "source_pathology": source_pathology or "",
        "source_extracted": extracted is not None,
        "compiled_op_count": len(op_rows),
        "lowering_observation_count": len(lowering_observation_rows),
        "lowering_rejection_count": len(lowering_rejection_rows),
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_parse_rejection_count": len(parse_rejection_rows),
        "source_parse_observation_count": len(source_parse_observation_rows),
        "source_parse_rejection_count": len(source_parse_rejection_rows),
        "source_acquisition_observation_count": len(source_acquisition_observation_rows),
        "source_acquisition_rejection_count": len(source_acquisition_rejection_rows),
        "manual_compile_status": manual_frontier["status"],
        "manual_compile_rule_id": manual_frontier["rule_id"],
        "owner_phase": manual_frontier_owner_phase,
        "authorization_status": execution_authorization["authorization_status"],
        "executable": execution_authorization["executable"],
        "replay_authorized": execution_authorization["replay_authorized"],
        "candidate": candidate,
    }
    report_row = {
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "effect_type": effect.effect_type or "",
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "manual_compile_frontier": manual_frontier,
        "execution_authorization": execution_authorization,
        "frontier_work_item": frontier_work_item,
        "compiled_op_count": len(op_rows),
        "lowering_rejection_count": len(lowering_rejection_rows),
        "candidate": candidate,
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_effect_frontier_report",
        schema="lawvm.uk_effect_frontier_report.v1",
        truth_claim="uk_single_effect_frontier_diagnostics_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "applicability_mode": applicability_mode,
        },
        filtered_summary=summary,
        rows=(report_row,),
        rows_truncated=False,
        detail={
            **legacy_payload,
            "safe_default": "classify_single_effect_without_authorizing_replay",
            "forbidden_shortcuts": (
                "effect_metadata_as_replay_authorization",
                "source_extraction_as_payload_identity",
                "candidate_flag_as_execution_authorization",
                "oracle_shape_as_source_truth",
            ),
            "next_promotion_requires": (
                "source_identity",
                "target_identity",
                "payload_identity",
                "temporal_extent_applicability",
                "mutation_boundary_proof",
            ),
        },
    ).to_dict()


def uk_effect_not_found_report_jsonable(
    *,
    statute_id: str,
    effect_id: str,
    loaded_effect_count: int,
    applicability_mode: str,
    parse_rejections: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    parse_observation_rows = tuple(dict(row) for row in parse_rejections)
    parse_rejection_rows = _blocking_rows(parse_observation_rows)
    legacy_payload = {
        "report_kind": "uk_effect_frontier_report",
        "error": "EFFECT_ID_NOT_FOUND",
        "statute_id": statute_id,
        "effect_id": effect_id,
        "loaded_effect_count": loaded_effect_count,
        "applicability_mode": applicability_mode,
        "effect_feed_parse_rejections": {
            "count": len(parse_rejection_rows),
            "rule_counts": rule_counts(list(parse_rejection_rows)),
            "rows": list(parse_rejection_rows),
        },
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_observation_rule_counts": rule_counts(list(parse_observation_rows)),
        "effect_feed_observations": list(parse_observation_rows),
    }
    summary = {
        "statute_id": statute_id,
        "effect_id": effect_id,
        "error": "EFFECT_ID_NOT_FOUND",
        "loaded_effect_count": loaded_effect_count,
        "applicability_mode": applicability_mode,
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_parse_rejection_count": len(parse_rejection_rows),
    }
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_effect_frontier_report",
        schema="lawvm.uk_effect_frontier_report.v1",
        truth_claim="uk_single_effect_frontier_diagnostics_only",
        replay_claims=False,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=False,
        summary=summary,
        filters={
            "statute_id": statute_id,
            "effect_id": effect_id,
            "applicability_mode": applicability_mode,
        },
        filtered_summary=summary,
        rows=(),
        rows_truncated=False,
        detail={
            **legacy_payload,
            "safe_default": "classify_missing_effect_without_authorizing_replay",
            "forbidden_shortcuts": (
                "missing_effect_as_no_op_success",
                "loaded_effect_count_as_source_completeness_proof",
                "effect_feed_observation_as_replay_authorization",
            ),
            "next_promotion_requires": (
                "effect_feed_source_identity",
                "effect_id_identity",
                "source_instruction_witness",
            ),
        },
    ).to_dict()


def _collect_statute_eids(statute: "IRStatute") -> set[str]:
    from lawvm.tools.uk_replay import _get_all_eids

    eids = set(_get_all_eids([statute.body]))
    for schedule in statute.supplements:
        eids.update(_get_all_eids([schedule]))
    return eids


def _eid_present(eid: str, candidates: set[str]) -> bool:
    norm = eid.lower()
    return any(candidate.lower() == norm for candidate in candidates)


def _resolve_target_presence(
    target: "LegalAddress",
    *,
    resolver,  # noqa: ANN001
    base_eids: set[str],
    oracle_eids: set[str],
) -> _EIDPresence:
    resolver_eid = resolver._derive_target_eid(target) if resolver is not None else ""
    if not resolver_eid:
        return _EIDPresence(eid="", base_present=False, oracle_present=False)
    return _EIDPresence(
        eid=resolver_eid,
        base_present=_eid_present(resolver_eid, base_eids),
        oracle_present=_eid_present(resolver_eid, oracle_eids),
    )


def _resolve_parent_presence(
    resolver_eid: str,
    *,
    base_eids: set[str],
    oracle_eids: set[str],
) -> _EIDPresence:
    parent_eid = _parent_eid(resolver_eid)
    if not parent_eid:
        return _EIDPresence(eid="", base_present=False, oracle_present=False)
    return _EIDPresence(
        eid=parent_eid,
        base_present=_eid_present(parent_eid, base_eids),
        oracle_present=_eid_present(parent_eid, oracle_eids),
    )


def _resolve_descendant_presence(
    resolver_eid: str,
    *,
    base_eids: set[str],
    oracle_eids: set[str],
) -> _DescendantPresence:
    if not resolver_eid:
        return _DescendantPresence(base_present=False, oracle_present=False)
    prefix = resolver_eid.lower() + "-"
    base_hit = any(eid.lower().startswith(prefix) for eid in base_eids)
    oracle_hit = any(eid.lower().startswith(prefix) for eid in oracle_eids)
    return _DescendantPresence(base_present=base_hit, oracle_present=oracle_hit)


def _find_node_by_eid(statute: "IRStatute", eid: str):  # noqa: ANN001
    found, _parent = _find_node_and_parent_by_eid(statute, eid)
    return found


def _find_node_and_parent_by_eid(statute: "IRStatute", eid: str):  # noqa: ANN001
    want = eid.lower()
    stack = [(statute.body, None), *((node, None) for node in statute.supplements)]
    while stack:
        node, parent = stack.pop()
        node_eid = node.attrs.get("eId") or node.attrs.get("id")
        if node_eid and node_eid.lower() == want:
            return node, parent
        stack.extend((child, node) for child in reversed(node.children))
    return None, None


def _target_heading_texts(node, parent) -> list[str]:  # noqa: ANN001
    """Return explicit heading-facet text for a target node, not body text."""
    title_bearing_kinds = {
        IRNodeKind.PART,
        IRNodeKind.CHAPTER,
        IRNodeKind.SCHEDULE,
        IRNodeKind.P1GROUP,
        IRNodeKind.BLOCK,
        IRNodeKind.CROSSHEADING,
        IRNodeKind.CROSS_HEADING,
    }
    sectionlike_kinds = {
        IRNodeKind.SECTION,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.SUBPARAGRAPH,
    }
    if node.kind in title_bearing_kinds:
        text = " ".join((node.text or "").split())
        return [node.text] if text else []
    direct_heading_children = [
        child
        for child in node.children
        if child.kind is IRNodeKind.HEADING and " ".join((child.text or "").split())
    ]
    if len(direct_heading_children) == 1:
        return [direct_heading_children[0].text]
    if (
        parent is not None
        and parent.kind in {IRNodeKind.P1GROUP, IRNodeKind.PGROUP}
        and node.kind in sectionlike_kinds
        and len(parent.children) == 1
    ):
        text = " ".join((parent.text or "").split())
        return [parent.text] if text else []
    return []


def _subtree_text_surface(node) -> str:  # noqa: ANN001
    parts: list[str] = []
    if node.text:
        parts.append(node.text)
    for child in node.children:
        child_text = _subtree_text_surface(child)
        if child_text:
            parts.append(child_text)
    return " ".join(" ".join(parts).split())


def _descendant_text_map_surface(*, eid: str, text_map: dict[str, str]) -> str:
    prefix = eid.lower() + "-"
    texts = [
        text
        for key, text in sorted(text_map.items())
        if key.lower().startswith(prefix) and " ".join((text or "").split())
    ]
    return " ".join(" ".join(texts).split())


def _text_map_get_casefold(text_map: dict[str, str], eid: str) -> str:
    if eid in text_map:
        return text_map[eid]
    want = eid.lower()
    for key, text in text_map.items():
        if key.lower() == want:
            return text
    return ""


def _collect_target_shape(
    statute: "IRStatute | None",
    *,
    eid: str,
    text_map: dict[str, str],
    descendant_hit: bool,
    target: "LegalAddress | None" = None,
) -> _TargetShape:
    has_text = False
    has_children = bool(descendant_hit)
    texts: list[str] = []

    node, parent = (
        _find_node_and_parent_by_eid(statute, eid) if statute is not None else (None, None)
    )
    if node is not None:
        if target is not None and target.special is FacetKind.HEADING:
            texts = _target_heading_texts(node, parent)
            return _TargetShape(
                has_text=bool(texts),
                has_children=bool(descendant_hit),
                texts=texts,
            )
        norm_text = " ".join((node.text or "").split())
        has_text = bool(norm_text)
        has_children = has_children or bool(node.children)
        if node.text:
            texts.append(node.text)
        mapped_text = _text_map_get_casefold(text_map, eid)
        norm_mapped = " ".join(mapped_text.split())
        if norm_mapped and not texts:
            has_text = True
            texts.append(mapped_text)
        if node.children and not texts:
            subtree_text = _subtree_text_surface(node)
            if subtree_text:
                has_text = True
                texts.append(subtree_text)
        if descendant_hit:
            descendant_text = _descendant_text_map_surface(eid=eid, text_map=text_map)
            if descendant_text and descendant_text not in texts:
                has_text = True
                texts.append(descendant_text)
        return _TargetShape(has_text=has_text, has_children=has_children, texts=texts)

    mapped_text = _text_map_get_casefold(text_map, eid)
    norm_mapped = " ".join(mapped_text.split())
    if norm_mapped:
        has_text = True
        texts.append(mapped_text)
    if descendant_hit:
        descendant_text = _descendant_text_map_surface(eid=eid, text_map=text_map)
        if descendant_text and descendant_text not in texts:
            has_text = True
            texts.append(descendant_text)
    return _TargetShape(has_text=has_text, has_children=has_children, texts=texts)


def _parent_eid(eid: str) -> str:
    if not eid or "-" not in eid:
        return ""
    return eid.rsplit("-", 1)[0]


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_effect_compare_shape,
        classify_uk_effect_source_pathology,
        is_core_uk_effect_compare_candidate,
        is_core_uk_effect_source_candidate,
    )
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation.effects import (
        get_affecting_act_xml_from_archive,
        load_effects_for_statute_from_archive,
        uk_effect_requires_affecting_source_for_replay,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        _build_affecting_source_context,
        _extract_from_affecting_source_context_with_observations,
        _select_enacted_source_for_current_shell,
        UKReplayExecutor,
        append_source_pathology_filter_lowering_rejections,
        append_no_ops_lowering_rejections,
        compile_effect_to_ir_ops,
        mark_nonreplay_lowering_rejections_nonblocking,
        mark_source_pathology_nonreplay_lowering_rejections_nonblocking,
    )
    from lawvm.tools.uk_replay import _archive_url_for_statute

    statute_id: str = args.statute_id
    effect_id: str = args.effect_id
    show_text: bool = getattr(args, "show_text", False)
    show_payload: bool = getattr(args, "show_payload", False)
    json_output: bool = bool(getattr(args, "json", False))
    db_arg: Optional[str] = getattr(args, "db", None)
    applicability_mode: str = (
        getattr(args, "uk_applicability_mode", None) or _DEFAULT_APPLICABILITY_MODE
    )

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    with Farchive(db_path) as archive:
        effect_feed_parse_rejections: list[dict[str, Any]] = []
        effects = load_effects_for_statute_from_archive(
            statute_id,
            archive,
            parse_rejections_out=effect_feed_parse_rejections,
        )
        effect = next((e for e in effects if e.effect_id == effect_id), None)
        if effect is None:
            if json_output:
                print(
                    json.dumps(
                        uk_effect_not_found_report_jsonable(
                            statute_id=statute_id,
                            effect_id=effect_id,
                            loaded_effect_count=len(effects),
                            applicability_mode=applicability_mode,
                            parse_rejections=tuple(effect_feed_parse_rejections),
                        ),
                        ensure_ascii=False,
                        indent=2,
                        sort_keys=True,
                    )
                )
            print(
                f"error: effect_id {effect_id!r} not found for {statute_id} ({len(effects)} effects loaded)",
                file=sys.stderr,
            )
            if effect_feed_parse_rejections:
                print(
                    f"effect feed parse/acquisition rejections: {len(effect_feed_parse_rejections)}",
                    file=sys.stderr,
                )
            sys.exit(1)

        source_required_for_replay = uk_effect_requires_affecting_source_for_replay(
            effect,
            applicability_mode=applicability_mode,
        )
        source_acquisition_rejections: list[dict[str, Any]] = []
        current_locator = (
            f"{_LEG_BASE}/{effect.affecting_act_id}/data.xml"
            if effect.affecting_act_id
            else ""
        )
        source_context, parse_error = _build_affecting_source_context(
            xml_bytes=(
                get_affecting_act_xml_from_archive(effect.affecting_act_id, archive)
                if source_required_for_replay
                else None
            ),
            locator=current_locator,
            authority_layer="AFFECTING_ACT_TEXT" if source_required_for_replay else "EFFECT_FEED_INDEX",
        )
        if source_required_for_replay and effect.affecting_act_id:
            if source_context.source_status == "absent":
                source_acquisition_rejections.append(affecting_act_xml_missing_rejection(effect))
            elif source_context.source_status == "too_small":
                source_acquisition_rejections.append(
                    affecting_act_xml_too_small_rejection(
                        effect,
                        source_size=source_context.source_size,
                    )
                )
            elif parse_error is not None:
                source_acquisition_rejections.append(affecting_act_xml_parse_rejection(effect, parse_error))
        extracted = None
        source_extraction_observations: tuple[dict[str, Any], ...] = ()
        if source_context.xml_bytes and source_context.root is not None:
            extracted, source_extraction_observations = _extract_from_affecting_source_context_with_observations(
                source_context,
                effect,
            )
        source_context, extracted, source_lane_observations = _select_enacted_source_for_current_shell(
            effect=effect,
            archive=archive,
            current_context=source_context,
            current_el=extracted,
            enacted_context_cache={},
        )
        source_acquisition_rejections.extend(source_extraction_observations)
        source_acquisition_rejections.extend(source_lane_observations)
        affecting_root = source_context.root

        lowering_rejections: list[dict[str, Any]] = []
        lowering_rejection_count_before = len(lowering_rejections)
        ops = compile_effect_to_ir_ops(
            effect,
            extracted,
            sequence=0,
            lowering_rejections_out=lowering_rejections,
            source_root=affecting_root,
            source_authority_layer=source_context.authority_layer,
        )
        structural_for_replay = effect.is_structural_for_replay(
            applicability_mode=applicability_mode
        )
        if not ops:
            append_no_ops_lowering_rejections(
                effect,
                structural_for_replay=structural_for_replay,
                lowering_rejections_out=lowering_rejections,
                compile_recorded_lowering_rejection=(
                    len(lowering_rejections) > lowering_rejection_count_before
                ),
                applicability_mode=applicability_mode,
            )
        mark_nonreplay_lowering_rejections_nonblocking(
            effect,
            structural_for_replay=structural_for_replay,
            applicability_mode=applicability_mode,
            lowering_rejections=lowering_rejections,
            start_index=lowering_rejection_count_before,
        )
        extracted_tag = _tag(extracted) if extracted is not None else None
        extracted_text = _text_snippet(extracted, limit=100000) if extracted is not None else ""
        enacted_bytes = archive.get(_archive_url_for_statute(statute_id, pit_date=None, enacted=True))
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
        oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
        enacted_source_sha256 = (
            hashlib.sha256(enacted_bytes).hexdigest() if enacted_bytes is not None else ""
        )
        oracle_source_sha256 = (
            hashlib.sha256(oracle_bytes).hexdigest() if oracle_bytes is not None else ""
        )
        source_parse_observations: list[dict[str, Any]] = []
        enacted_source_parse_failed = False
        oracle_source_parse_failed = False
        compiled_facts = uk_compiled_effect_facts(
            ops=ops,
            lowering_rejections=lowering_rejections,
            lowering_rejection_start_index=lowering_rejection_count_before,
            target_formatter=_fmt_target,
            payload_text_formatter=lambda text: " ".join(text.split()),
        )
        source_pathology = classify_uk_effect_source_pathology(
            extracted_tag=extracted_tag,
            extracted_text=extracted_text,
            op_actions=compiled_facts.op_actions,
            payload_kinds=compiled_facts.payload_kinds,
            payload_texts=compiled_facts.payload_texts,
            target_paths=compiled_facts.target_paths,
            lowering_rule_ids=compiled_facts.lowering_rule_ids,
            effect_type=effect.effect_type,
            is_structural=structural_for_replay,
        )
        mark_source_pathology_nonreplay_lowering_rejections_nonblocking(
            source_pathology=source_pathology,
            lowering_rejections=lowering_rejections,
            start_index=lowering_rejection_count_before,
        )
        append_source_pathology_filter_lowering_rejections(
            effect,
            source_pathology=source_pathology,
            structural_for_replay=structural_for_replay,
            compiled_ops=ops,
            lowering_rejections_out=lowering_rejections,
        )
        base_eids: set[str] = set()
        oracle_eids: set[str] = set()
        base_text_map: dict[str, str] = {}
        oracle_text_map: dict[str, str] = {}
        resolver = None
        enacted_ir = None
        oracle_ir = None
        if enacted_source_status == "available":
            assert enacted_bytes is not None
            try:
                enacted_maps = extract_eid_map_bytes(enacted_bytes)
                enacted_ir = parse_uk_statute_ir_bytes(
                    enacted_bytes,
                    statute_id=statute_id,
                    version_label="enacted",
                    source_path=enacted_url,
                )
                source_parse_observations.extend(uk_source_parse_observations_from_ir(enacted_ir))
            except Exception as exc:
                enacted_source_parse_failed = True
                source_parse_observations.append(
                    uk_source_xml_parse_rejection(
                        statute_id=statute_id,
                        side="enacted",
                        source_url=enacted_url,
                        exc=exc,
                    )
                )
            else:
                base_eids = _collect_statute_eids(enacted_ir)
                base_text_map = enacted_maps.get("text_map", {})
        if oracle_source_status == "available":
            assert oracle_bytes is not None
            try:
                oracle_ir = parse_uk_statute_ir_bytes(
                    oracle_bytes,
                    statute_id=statute_id,
                    version_label="oracle",
                    source_path=oracle_url,
                )
                source_parse_observations.extend(uk_source_parse_observations_from_ir(oracle_ir))
                oracle_maps = extract_eid_map_bytes(oracle_bytes)
            except Exception as exc:
                oracle_source_parse_failed = True
                source_parse_observations.append(
                    uk_source_xml_parse_rejection(
                        statute_id=statute_id,
                        side="oracle",
                        source_url=oracle_url,
                        exc=exc,
                    )
                )
            else:
                oracle_eids = _collect_statute_eids(oracle_ir)
                oracle_text_map = oracle_maps.get("text_map", {})
                resolver = UKReplayExecutor(
                    oracle_ir,
                    eid_map=oracle_maps.get("eid_map", {}),
                    text_map=oracle_text_map,
                )

    op_actions: list[str] = []
    payload_texts: list[str] = []
    resolver_eids: list[str] = []
    base_target_hits: list[bool] = []
    oracle_target_hits: list[bool] = []
    base_descendant_hits: list[bool] = []
    oracle_descendant_hits: list[bool] = []
    base_parent_hits: list[bool] = []
    oracle_parent_hits: list[bool] = []
    base_target_texts: list[str] = []
    oracle_target_texts: list[str] = []
    base_parent_texts: list[str] = []
    oracle_parent_texts: list[str] = []
    text_patch_matches: list[str] = []
    text_patch_replacements: list[str] = []
    base_has_text = False
    base_has_children = False
    oracle_has_text = False
    oracle_has_children = False
    op_rows: list[dict[str, Any]] = []

    for op in ops:
        payload_kind = op.payload.kind if op.payload is not None else "-"
        op_actions.append(op.action.value)
        if op.payload is not None and op.payload.text:
            payload_texts.append(op.payload.text)
        if op.text_patch is not None:
            text_patch_matches.append(op.text_patch.selector.match_text)
            text_patch_replacements.append(op.text_patch.replacement or "")
        target_presence = _resolve_target_presence(
            op.target,
            resolver=resolver,
            base_eids=base_eids,
            oracle_eids=oracle_eids,
        )
        base_descendant_hit = False
        oracle_descendant_hit = False
        parent_eid = ""
        base_parent_hit = False
        oracle_parent_hit = False
        if target_presence.eid:
            resolver_eids.append(target_presence.eid)
            base_target_hits.append(target_presence.base_present)
            oracle_target_hits.append(target_presence.oracle_present)
            descendant_presence = _resolve_descendant_presence(
                target_presence.eid,
                base_eids=base_eids,
                oracle_eids=oracle_eids,
            )
            base_descendant_hit = descendant_presence.base_present
            oracle_descendant_hit = descendant_presence.oracle_present
            base_descendant_hits.append(base_descendant_hit)
            oracle_descendant_hits.append(oracle_descendant_hit)
            parent_presence = _resolve_parent_presence(
                target_presence.eid,
                base_eids=base_eids,
                oracle_eids=oracle_eids,
            )
            parent_eid = parent_presence.eid
            base_parent_hit = parent_presence.base_present
            oracle_parent_hit = parent_presence.oracle_present
            base_parent_hits.append(base_parent_hit)
            oracle_parent_hits.append(oracle_parent_hit)
            if target_presence.base_present:
                target_shape = _collect_target_shape(
                    enacted_ir,
                    eid=target_presence.eid,
                    text_map=base_text_map,
                    descendant_hit=base_descendant_hit,
                    target=op.target,
                )
                base_has_text = base_has_text or target_shape.has_text
                base_has_children = base_has_children or target_shape.has_children
                base_target_texts.extend(target_shape.texts)
            if base_parent_hit and base_text_map.get(parent_eid):
                base_parent_texts.append(base_text_map[parent_eid])
            if target_presence.oracle_present:
                target_shape = _collect_target_shape(
                    oracle_ir,
                    eid=target_presence.eid,
                    text_map=oracle_text_map,
                    descendant_hit=oracle_descendant_hit,
                    target=op.target,
                )
                oracle_has_text = oracle_has_text or target_shape.has_text
                oracle_has_children = oracle_has_children or target_shape.has_children
                oracle_target_texts.extend(target_shape.texts)
            if oracle_parent_hit and oracle_text_map.get(parent_eid):
                oracle_parent_texts.append(oracle_text_map[parent_eid])
        op_rows.append({
            "op_id": op.op_id,
            "action": op.action.value,
            "target": _fmt_target(op.target),
            "payload_kind": str(payload_kind),
            "resolver_eid": target_presence.eid,
            "base_target_present": target_presence.base_present,
            "oracle_target_present": target_presence.oracle_present,
            "base_descendant_present": base_descendant_hit,
            "oracle_descendant_present": oracle_descendant_hit,
            "parent_eid": parent_eid,
            "base_parent_present": base_parent_hit,
            "oracle_parent_present": oracle_parent_hit,
            "payload": _payload_jsonable(op.payload) if op.payload is not None and show_payload else None,
            "text_patch": _text_patch_jsonable(op),
        })
    compare_shape = ""
    if ops:
        compare_shape = classify_uk_effect_compare_shape(
            affecting_title=effect.affecting_title,
            effect_type=effect.effect_type,
            op_actions=op_actions,
            payload_texts=payload_texts,
            resolver_eids=resolver_eids,
            base_target_hits=base_target_hits,
            oracle_target_hits=oracle_target_hits,
            base_descendant_hits=base_descendant_hits,
            oracle_descendant_hits=oracle_descendant_hits,
            base_parent_hits=base_parent_hits,
            oracle_parent_hits=oracle_parent_hits,
            base_target_texts=base_target_texts,
            oracle_target_texts=oracle_target_texts,
            base_parent_texts=base_parent_texts,
            oracle_parent_texts=oracle_parent_texts,
            text_patch_matches=text_patch_matches,
            text_patch_replacements=text_patch_replacements,
            lowering_rule_ids=[
                str(row.get("rule_id") or "")
                for row in lowering_rejections[lowering_rejection_count_before:]
            ],
            base_has_text=base_has_text,
            base_has_children=base_has_children,
            oracle_has_text=oracle_has_text,
            oracle_has_children=oracle_has_children,
        )
    candidate = (
        is_core_uk_effect_source_candidate(source_pathology)
        and (not ops or is_core_uk_effect_compare_candidate(compare_shape))
        and not has_blocking_lowering_rejection(lowering_rejections)
    )
    extracted_id = _element_id(extracted)

    if json_output:
        print(json.dumps(
            uk_effect_report_jsonable(
                statute_id=statute_id,
                effect=effect,
                source_pathology=source_pathology,
                extracted=extracted,
                lowering_rejections=lowering_rejections,
                compare_shape=compare_shape,
                candidate=candidate,
                op_rows=op_rows,
                applicability_mode=applicability_mode,
                show_text=show_text,
                parse_rejections=tuple(effect_feed_parse_rejections),
                source_acquisition_rejections=tuple(source_acquisition_rejections),
                source_surface={
                    "archive_path": str(db_path),
                    "enacted_url": enacted_url,
                    "oracle_url": oracle_url,
                    "enacted_missing": (
                        enacted_source_status != "available" or enacted_source_parse_failed
                    ),
                    "oracle_missing": (
                        oracle_source_status != "available" or oracle_source_parse_failed
                    ),
                    "enacted_source_status": enacted_source_status,
                    "oracle_source_status": oracle_source_status,
                    "enacted_source_size": enacted_source_size,
                    "oracle_source_size": oracle_source_size,
                    "enacted_source_sha256": enacted_source_sha256,
                    "oracle_source_sha256": oracle_source_sha256,
                    "enacted_source_parse_failed": enacted_source_parse_failed,
                    "oracle_source_parse_failed": oracle_source_parse_failed,
                },
                source_parse_observations=tuple(source_parse_observations),
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ))
        return

    print(f"Statute:            {statute_id}")
    print(f"Archive:            {db_path}")
    print(f"Enacted URL:        {enacted_url}")
    print(f"Oracle URL:         {oracle_url}")
    print(f"Enacted source:     {enacted_source_status} ({enacted_source_size} bytes)")
    print(f"Oracle source:      {oracle_source_status} ({oracle_source_size} bytes)")
    print(f"Enacted SHA-256:    {enacted_source_sha256 or '(none)'}")
    print(f"Oracle SHA-256:     {oracle_source_sha256 or '(none)'}")
    print(f"Effect ID:          {effect.effect_id}")
    print(f"Effect type:        {effect.effect_type or '(empty)'}")
    print(f"Affected provs:     {effect.affected_provisions}")
    print(f"Affecting act:      {effect.affecting_act_id}")
    print(f"Affecting provs:    {effect.affecting_provisions}")
    print(f"Modified:           {effect.modified}")
    print(f"Effective date:     {effect.effective_date or '(none)'}")
    print(f"Applied:            {effect.applied}")
    print(f"Requires applied:   {effect.requires_applied}")
    print(f"Structural:         {effect.is_structural}")
    print(f"Applicability mode: {applicability_mode}")
    print(f"Replay applicable:  {effect.is_applicable_for_replay(applicability_mode=applicability_mode)}")
    print(f"Structural replay:  {effect.is_structural_for_replay(applicability_mode=applicability_mode)}")
    print(f"Source pathology:   {source_pathology or '(none)'}")
    feed_observation_rows = tuple(dict(item) for item in effect_feed_parse_rejections)
    feed_rejection_rows = _blocking_rows(feed_observation_rows)
    if feed_observation_rows:
        print(f"Feed observations:  {len(feed_observation_rows)}")
        for rule_id, count in rule_counts(list(feed_observation_rows)).items():
            print(f"  {rule_id}: {count}")
    if feed_rejection_rows:
        print(f"Feed rejections:    {len(feed_rejection_rows)}")
        for rule_id, count in rule_counts(list(feed_rejection_rows)).items():
            print(f"  {rule_id}: {count}")
    source_acquisition_observation_rows = tuple(dict(item) for item in source_acquisition_rejections)
    source_acquisition_rejection_rows = _blocking_rows(source_acquisition_observation_rows)
    if source_acquisition_observation_rows:
        print(f"Source acquisition observations: {len(source_acquisition_observation_rows)}")
        for rule_id, count in rule_counts(list(source_acquisition_observation_rows)).items():
            print(f"  {rule_id}: {count}")
    if source_acquisition_rejection_rows:
        print(f"Source acquisition rejections:   {len(source_acquisition_rejection_rows)}")
        for rule_id, count in rule_counts(list(source_acquisition_rejection_rows)).items():
            print(f"  {rule_id}: {count}")
    source_parse_observation_rows = tuple(dict(item) for item in source_parse_observations)
    source_parse_rejection_rows = _blocking_rows(source_parse_observation_rows)
    if source_parse_observation_rows:
        print(f"Source parse observations: {len(source_parse_observation_rows)}")
        for rule_id, count in rule_counts(list(source_parse_observation_rows)).items():
            print(f"  {rule_id}: {count}")
    if source_parse_rejection_rows:
        print(f"Source parse rejections:   {len(source_parse_rejection_rows)}")
        for rule_id, count in rule_counts(list(source_parse_rejection_rows)).items():
            print(f"  {rule_id}: {count}")
    print()

    print("Extracted source:")
    if extracted is None:
        print("  none")
    else:
        print(f"  tag:    {_tag(extracted)}")
        if extracted_id:
            print(f"  id:     {extracted_id}")
        print(f"  text:   {_text_snippet(extracted)}")
        if show_text:
            print()
            print("  full text:")
            print(f"  {_text_snippet(extracted, limit=100000)}")
    print()

    print(f"Compiled ops: {len(ops)}")
    print_lowering_rejections(lowering_rejections)
    if not ops:
        print(f"Replay candidate:   {'yes' if candidate else 'no'}")
        return

    for index, op in enumerate(ops):
        row = op_rows[index]
        resolver_eid = row["resolver_eid"]
        base_hit = bool(row["base_target_present"])
        oracle_hit = bool(row["oracle_target_present"])
        base_descendant_hit = bool(row["base_descendant_present"])
        oracle_descendant_hit = bool(row["oracle_descendant_present"])
        parent_eid = str(row["parent_eid"])
        base_parent_hit = bool(row["base_parent_present"])
        oracle_parent_hit = bool(row["oracle_parent_present"])
        payload_kind = str(row["payload_kind"])
        print(f"  {op.op_id}  {op.action:<12}  {_fmt_target(op.target):<48}  payload={payload_kind}")
        if resolver_eid:
            print(
                f"    resolver_eid={resolver_eid}  "
                f"base={'yes' if base_hit else 'no'}  "
                f"oracle={'yes' if oracle_hit else 'no'}"
            )
            if base_descendant_hit or oracle_descendant_hit:
                print(
                    f"    descendants  "
                    f"base={'yes' if base_descendant_hit else 'no'}  "
                    f"oracle={'yes' if oracle_descendant_hit else 'no'}"
                )
            if parent_eid and (not base_hit or not oracle_hit):
                print(
                    f"    parent_eid={parent_eid}  "
                    f"base={'yes' if base_parent_hit else 'no'}  "
                    f"oracle={'yes' if oracle_parent_hit else 'no'}"
                )
        if show_payload and op.payload is not None:
            _print_payload(op.payload)

    print()
    print(f"Compare shape:      {compare_shape or '(none)'}")
    print(f"Replay candidate:   {'yes' if candidate else 'no'}")
