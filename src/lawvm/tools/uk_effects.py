"""lawvm uk-effects -- list/search UK effects-feed rows for one statute."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Mapping, Optional

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.uk_legislation.source_state import (
    uk_source_parse_observations_from_ir,
    uk_source_xml_parse_rejection,
    uk_source_state_wire_tuple as _source_state,
)

if TYPE_CHECKING:
    import argparse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_DEFAULT_APPLICABILITY_MODE = "effective_date_plus_feed_applied"


@dataclass
class _EffectSummaryContext:
    statute_id: str
    enacted_ir: Any
    oracle_ir: Any
    base_eids: set[str]
    oracle_eids: set[str]
    base_text_map: dict[str, str]
    oracle_eid_map: dict[str, str]
    oracle_text_map: dict[str, str]
    resolver: object | None
    affecting_xml_cache: dict[str, bytes | None]
    archive_path: str = ""
    enacted_url: str = ""
    oracle_url: str = ""
    enacted_missing: bool = False
    oracle_missing: bool = False
    enacted_source_status: str = "absent"
    oracle_source_status: str = "absent"
    enacted_source_size: int = 0
    oracle_source_size: int = 0
    enacted_source_sha256: str = ""
    oracle_source_sha256: str = ""
    enacted_source_parse_failed: bool = False
    oracle_source_parse_failed: bool = False
    source_parse_observations: tuple[dict[str, Any], ...] = ()


@dataclass
class _EffectSummary:
    source_pathology: str
    compare_shape: str
    n_ops: int
    candidate: bool
    resolver_eids: tuple[str, ...]
    lowering_rejections: tuple[dict[str, Any], ...]
    source_acquisition_rejections: tuple[dict[str, Any], ...] = ()
    effect_id: str = ""
    effect_type: str = ""
    affected_provisions: str = ""
    affecting_act_id: str = ""
    affecting_provisions: str = ""
    effective_date: str = ""
    source_extracted: bool = False
    source_extracted_tag: str = ""
    source_extracted_text_preview: str = ""
    affecting_source_status: str = "absent"
    affecting_source_size: int = 0
    affecting_source_sha256: str = ""
    replay_applicable: bool = False
    structural_for_replay: bool = False
    applicability_mode: str = _DEFAULT_APPLICABILITY_MODE
    manual_compile_status: str = ""
    manual_compile_rule_id: str = ""
    manual_compile_reason: str = ""
    manual_compile_lowering_rule_ids: tuple[str, ...] = ()
    manual_compile_blocking_lowering_rule_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class _EffectFilters:
    affected_contains: str = ""
    affecting_contains: str = ""
    effect_type_contains: str = ""
    source_pathology: str = ""
    lowering_rule: str = ""
    source_acquisition_rule: str = ""
    manual_compile_status: str = ""
    manual_compile_rule: str = ""
    applied_only: bool = False
    structural_only: bool = False
    candidate_only: bool = False
    non_candidate_only: bool = False
    limit: int | None = None
    applicability_mode: str = _DEFAULT_APPLICABILITY_MODE


@dataclass(frozen=True)
class _EffectReportRow:
    effect: Any
    summary: _EffectSummary


def build_uk_effect_summary_context(
    statute_id: str,
    *,
    archive,  # noqa: ANN001
) -> _EffectSummaryContext:
    from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.tools.uk_effect import _collect_statute_eids
    from lawvm.tools.uk_replay import _archive_url_for_statute

    enacted_ir = None
    oracle_ir = None
    base_eids: set[str] = set()
    oracle_eids: set[str] = set()
    base_text_map: dict[str, str] = {}
    oracle_eid_map: dict[str, str] = {}
    oracle_text_map: dict[str, str] = {}
    resolver = None
    source_parse_observations: list[dict[str, Any]] = []
    enacted_source_parse_failed = False
    oracle_source_parse_failed = False

    enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
    oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
    enacted_bytes = archive.get(enacted_url)
    enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
    enacted_source_sha256 = (
        hashlib.sha256(enacted_bytes).hexdigest() if enacted_bytes is not None else ""
    )
    if enacted_source_status == "available":
        assert enacted_bytes is not None
        try:
            enacted_maps = extract_eid_map_bytes(enacted_bytes)
            enacted_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=statute_id,
                version_label="enacted",
                pit_date=None,
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

    oracle_bytes = archive.get(oracle_url)
    oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
    oracle_source_sha256 = (
        hashlib.sha256(oracle_bytes).hexdigest() if oracle_bytes is not None else ""
    )
    if oracle_source_status == "available":
        assert oracle_bytes is not None
        try:
            oracle_ir = parse_uk_statute_ir_bytes(
                oracle_bytes,
                statute_id=statute_id,
                version_label="oracle",
                pit_date=None,
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
            oracle_eid_map = oracle_maps.get("eid_map", {})
            oracle_text_map = oracle_maps.get("text_map", {})
            resolver = UKReplayExecutor(
                oracle_ir,
                eid_map=oracle_eid_map,
                text_map=oracle_text_map,
            )

    return _EffectSummaryContext(
        statute_id=statute_id,
        enacted_ir=enacted_ir,
        oracle_ir=oracle_ir,
        base_eids=base_eids,
        oracle_eids=oracle_eids,
        base_text_map=base_text_map,
        oracle_eid_map=oracle_eid_map,
        oracle_text_map=oracle_text_map,
        resolver=resolver,
        affecting_xml_cache={},
        archive_path=str(getattr(archive, "_db_path", "")),
        enacted_url=enacted_url,
        oracle_url=oracle_url,
        enacted_missing=enacted_source_status != "available" or enacted_source_parse_failed,
        oracle_missing=oracle_source_status != "available" or oracle_source_parse_failed,
        enacted_source_status=enacted_source_status,
        oracle_source_status=oracle_source_status,
        enacted_source_size=enacted_source_size,
        oracle_source_size=oracle_source_size,
        enacted_source_sha256=enacted_source_sha256,
        oracle_source_sha256=oracle_source_sha256,
        enacted_source_parse_failed=enacted_source_parse_failed,
        oracle_source_parse_failed=oracle_source_parse_failed,
        source_parse_observations=tuple(source_parse_observations),
    )


def summarize_uk_effect(
    effect,  # noqa: ANN001
    *,
    archive,  # noqa: ANN001
    context: _EffectSummaryContext,
    applicability_mode: str = _DEFAULT_APPLICABILITY_MODE,
) -> _EffectSummary:
    from lawvm.uk_legislation.source_adjudication import (
        classify_uk_effect_compare_shape,
        classify_uk_effect_source_pathology,
        classify_uk_manual_compile_frontier,
        is_core_uk_effect_compare_candidate,
        is_core_uk_effect_source_candidate,
    )
    from lawvm.uk_legislation.effects import (
        get_affecting_act_xml_from_archive,
        uk_effect_requires_affecting_source_for_replay,
    )
    from lawvm.uk_legislation.uk_amendment_replay import (
        _build_affecting_source_context,
        _extract_from_affecting_source_context,
        _select_enacted_source_for_current_shell,
        append_source_pathology_filter_lowering_rejections,
        append_no_ops_lowering_rejections,
        compile_effect_to_ir_ops,
        mark_nonreplay_lowering_rejections_nonblocking,
    )
    from lawvm.tools.uk_effect import (
        _collect_target_shape,
        _resolve_descendant_presence,
        _resolve_parent_presence,
        _resolve_target_presence,
        affecting_act_xml_missing_rejection,
        affecting_act_xml_parse_rejection,
        affecting_act_xml_too_small_rejection,
        has_blocking_lowering_rejection,
    )

    source_required_for_replay = uk_effect_requires_affecting_source_for_replay(
        effect,
        applicability_mode=applicability_mode,
    )
    affecting_xml = None
    if source_required_for_replay:
        affecting_xml = context.affecting_xml_cache.get(effect.affecting_act_id)
        if effect.affecting_act_id not in context.affecting_xml_cache:
            affecting_xml = get_affecting_act_xml_from_archive(effect.affecting_act_id, archive)
            context.affecting_xml_cache[effect.affecting_act_id] = affecting_xml
    current_locator = (
        f"https://www.legislation.gov.uk/{effect.affecting_act_id}/data.xml"
        if effect.affecting_act_id
        else ""
    )
    source_context, parse_error = _build_affecting_source_context(
        xml_bytes=affecting_xml,
        locator=current_locator,
        authority_layer="AFFECTING_ACT_TEXT" if source_required_for_replay else "EFFECT_FEED_INDEX",
    )
    affecting_source_status = source_context.source_status
    affecting_source_size = source_context.source_size
    affecting_source_sha256 = (
        hashlib.sha256(affecting_xml).hexdigest() if affecting_xml else ""
    )
    source_acquisition_rejections: tuple[dict[str, Any], ...] = ()
    if source_required_for_replay and effect.affecting_act_id:
        if source_context.source_status == "absent":
            source_acquisition_rejections = (affecting_act_xml_missing_rejection(effect),)
        elif source_context.source_status == "too_small":
            source_acquisition_rejections = (
                affecting_act_xml_too_small_rejection(
                    effect,
                    source_size=source_context.source_size,
                ),
            )
        elif parse_error is not None:
            source_acquisition_rejections = (affecting_act_xml_parse_rejection(effect, parse_error),)

    extracted = None
    if source_context.xml_bytes and source_context.root is not None:
        extracted = _extract_from_affecting_source_context(
            source_context,
            effect.affecting_provisions,
        )
    source_context, extracted, source_lane_observations = _select_enacted_source_for_current_shell(
        effect=effect,
        archive=archive,
        current_context=source_context,
        current_el=extracted,
        enacted_context_cache={},
    )
    if source_lane_observations:
        source_acquisition_rejections = (*source_acquisition_rejections, *source_lane_observations)
    affecting_root = source_context.root
    lowering_rejections: list[dict[str, Any]] = []
    lowering_rejection_count_before = len(lowering_rejections)
    ops = compile_effect_to_ir_ops(
        effect,
        extracted,
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
    extracted_tag = extracted.tag.rsplit("}", 1)[-1] if extracted is not None else None
    extracted_text = " ".join(
        t.strip() for t in extracted.itertext() if t and t.strip()
    ) if extracted is not None else ""
    extracted_text_preview = (
        extracted_text if len(extracted_text) <= 500 else extracted_text[:497] + "..."
    )

    source_pathology = classify_uk_effect_source_pathology(
        extracted_tag=extracted_tag,
        extracted_text=extracted_text,
        op_actions=[op.action.value for op in ops],
        payload_kinds=[str(op.payload.kind) for op in ops if op.payload is not None],
        payload_texts=[op.payload.text or "" for op in ops if op.payload is not None],
        target_paths=["/".join(f"{kind}:{label}" for kind, label in op.target.path) for op in ops],
        lowering_rule_ids=[
            str(row.get("rule_id") or "")
            for row in lowering_rejections[lowering_rejection_count_before:]
        ],
        effect_type=effect.effect_type,
        is_structural=structural_for_replay,
    )
    append_source_pathology_filter_lowering_rejections(
        effect,
        source_pathology=source_pathology,
        structural_for_replay=structural_for_replay,
        compiled_ops=ops,
        lowering_rejections_out=lowering_rejections,
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
    for op in ops:
        op_actions.append(op.action.value)
        if op.payload is not None and op.payload.text:
            payload_texts.append(op.payload.text)
        if op.text_patch is not None:
            text_patch_matches.append(op.text_patch.selector.match_text)
            text_patch_replacements.append(op.text_patch.replacement or "")
        resolver_eid, base_hit, oracle_hit = _resolve_target_presence(
            op.target,
            resolver=context.resolver,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        if not resolver_eid:
            continue
        resolver_eids.append(resolver_eid)
        base_target_hits.append(base_hit)
        oracle_target_hits.append(oracle_hit)
        base_descendant_hit, oracle_descendant_hit = _resolve_descendant_presence(
            resolver_eid,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        base_descendant_hits.append(base_descendant_hit)
        oracle_descendant_hits.append(oracle_descendant_hit)
        parent_eid, base_parent_hit, oracle_parent_hit = _resolve_parent_presence(
            resolver_eid,
            base_eids=context.base_eids,
            oracle_eids=context.oracle_eids,
        )
        base_parent_hits.append(base_parent_hit)
        oracle_parent_hits.append(oracle_parent_hit)
        if base_hit:
            hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                context.enacted_ir,
                eid=resolver_eid,
                text_map=context.base_text_map,
                descendant_hit=base_descendant_hit,
            )
            base_has_text = base_has_text or hit_has_text
            base_has_children = base_has_children or hit_has_children
            base_target_texts.extend(hit_texts)
        if oracle_hit:
            hit_has_text, hit_has_children, hit_texts = _collect_target_shape(
                context.oracle_ir,
                eid=resolver_eid,
                text_map=context.oracle_text_map,
                descendant_hit=oracle_descendant_hit,
            )
            oracle_has_text = oracle_has_text or hit_has_text
            oracle_has_children = oracle_has_children or hit_has_children
            oracle_target_texts.extend(hit_texts)
        if base_parent_hit and context.base_text_map.get(parent_eid):
            base_parent_texts.append(context.base_text_map[parent_eid])
        if oracle_parent_hit and context.oracle_text_map.get(parent_eid):
            oracle_parent_texts.append(context.oracle_text_map[parent_eid])

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
        and is_core_uk_effect_compare_candidate(compare_shape)
        and not has_blocking_lowering_rejection(lowering_rejections)
    )
    manual_frontier = classify_uk_manual_compile_frontier(
        effect_type=effect.effect_type or "",
        source_pathology=source_pathology,
        extracted_tag=extracted_tag or "",
        extracted_text=extracted_text,
        lowering_rejections=lowering_rejections,
        compiled_op_count=len(ops),
        replay_applicable=effect.is_applicable_for_replay(applicability_mode=applicability_mode),
        structural_for_replay=structural_for_replay,
        compare_shape=compare_shape,
    )
    return _EffectSummary(
        source_pathology=source_pathology,
        compare_shape=compare_shape,
        n_ops=len(ops),
        candidate=candidate,
        resolver_eids=tuple(resolver_eids),
        lowering_rejections=tuple(dict(item) for item in lowering_rejections),
        source_acquisition_rejections=source_acquisition_rejections,
        effect_id=str(effect.effect_id or ""),
        effect_type=str(effect.effect_type or ""),
        affected_provisions=str(effect.affected_provisions or ""),
        affecting_act_id=str(effect.affecting_act_id or ""),
        affecting_provisions=str(effect.affecting_provisions or ""),
        effective_date=str(effect.effective_date or ""),
        source_extracted=extracted is not None,
        source_extracted_tag=extracted_tag or "",
        source_extracted_text_preview=extracted_text_preview,
        affecting_source_status=affecting_source_status,
        affecting_source_size=affecting_source_size,
        affecting_source_sha256=affecting_source_sha256,
        replay_applicable=effect.is_applicable_for_replay(applicability_mode=applicability_mode),
        structural_for_replay=structural_for_replay,
        applicability_mode=applicability_mode,
        manual_compile_status=manual_frontier["status"],
        manual_compile_rule_id=manual_frontier["rule_id"],
        manual_compile_reason=manual_frontier["reason"],
        manual_compile_lowering_rule_ids=tuple(
            sorted(
                {
                    str(row.get("rule_id") or "unknown")
                    for row in lowering_rejections
                }
            )
        ),
        manual_compile_blocking_lowering_rule_ids=tuple(
            sorted(
                {
                    str(row.get("rule_id") or "unknown")
                    for row in lowering_rejections
                    if is_blocking_compile_record(row)
                }
            )
        ),
    )


def uk_effects_summary_counts(
    rows: tuple[_EffectReportRow, ...],
    *,
    matched_effect_count_before_limit: int | None = None,
) -> dict[str, Any]:
    """Aggregate UK effect classifications without changing row semantics."""

    emitted_effect_count = len(rows)
    matched_effect_count = (
        emitted_effect_count
        if matched_effect_count_before_limit is None
        else matched_effect_count_before_limit
    )
    source_pathology_counts: dict[str, int] = {}
    compare_shape_counts: dict[str, int] = {}
    candidate_counts = {"candidate": 0, "not_candidate": 0}
    replay_applicability_counts = {"replay_applicable": 0, "not_replay_applicable": 0}
    structural_for_replay_counts = {"structural_for_replay": 0, "not_structural_for_replay": 0}
    lowering_rejection_rule_counts: dict[str, int] = {}
    lowering_observation_rule_counts: dict[str, int] = {}
    blocking_lowering_rejection_rule_counts: dict[str, int] = {}
    metadata_only_count = 0
    applied_count = 0
    requires_applied_count = 0
    total_ops = 0
    rows_with_lowering_observations = 0
    rows_with_lowering_rejections = 0
    rows_with_blocking_lowering_rejections = 0
    rows_with_source_acquisition_observations = 0
    rows_with_source_acquisition_rejections = 0
    source_acquisition_observation_rule_counts: dict[str, int] = {}
    source_acquisition_rejection_rule_counts: dict[str, int] = {}
    manual_compile_status_counts: dict[str, int] = {}
    manual_compile_rule_counts: dict[str, int] = {}
    rows_with_resolver_eids = 0
    for row in rows:
        effect = row.effect
        summary = row.summary
        source_key = summary.source_pathology or "__none__"
        compare_key = summary.compare_shape or "__none__"
        manual_status_key = summary.manual_compile_status or "__none__"
        manual_rule_key = summary.manual_compile_rule_id or "__none__"
        source_pathology_counts[source_key] = source_pathology_counts.get(source_key, 0) + 1
        compare_shape_counts[compare_key] = compare_shape_counts.get(compare_key, 0) + 1
        manual_compile_status_counts[manual_status_key] = (
            manual_compile_status_counts.get(manual_status_key, 0) + 1
        )
        manual_compile_rule_counts[manual_rule_key] = (
            manual_compile_rule_counts.get(manual_rule_key, 0) + 1
        )
        if summary.candidate:
            candidate_counts["candidate"] += 1
        else:
            candidate_counts["not_candidate"] += 1
        if effect.metadata_only:
            metadata_only_count += 1
        if effect.applied:
            applied_count += 1
        if effect.requires_applied:
            requires_applied_count += 1
        if summary.replay_applicable:
            replay_applicability_counts["replay_applicable"] += 1
        else:
            replay_applicability_counts["not_replay_applicable"] += 1
        if summary.structural_for_replay:
            structural_for_replay_counts["structural_for_replay"] += 1
        else:
            structural_for_replay_counts["not_structural_for_replay"] += 1
        total_ops += summary.n_ops
        if summary.resolver_eids:
            rows_with_resolver_eids += 1
        if summary.lowering_rejections:
            rows_with_lowering_observations += 1
        lowering_rejections = _blocking_rows(tuple(summary.lowering_rejections))
        if lowering_rejections:
            rows_with_lowering_rejections += 1
        if lowering_rejections:
            rows_with_blocking_lowering_rejections += 1
        source_acquisition_observations = tuple(summary.source_acquisition_rejections)
        source_acquisition_rejections = _blocking_rows(source_acquisition_observations)
        if source_acquisition_observations:
            rows_with_source_acquisition_observations += 1
        for observation in source_acquisition_observations:
            rule_id = str(observation.get("rule_id") or "unknown")
            source_acquisition_observation_rule_counts[rule_id] = (
                source_acquisition_observation_rule_counts.get(rule_id, 0) + 1
            )
        if source_acquisition_rejections:
            rows_with_source_acquisition_rejections += 1
        for rejection in source_acquisition_rejections:
            rule_id = str(rejection.get("rule_id") or "unknown")
            source_acquisition_rejection_rule_counts[rule_id] = (
                source_acquisition_rejection_rule_counts.get(rule_id, 0) + 1
            )
        for observation in summary.lowering_rejections:
            rule_id = str(observation.get("rule_id") or "unknown")
            lowering_observation_rule_counts[rule_id] = (
                lowering_observation_rule_counts.get(rule_id, 0) + 1
            )
        for rejection in lowering_rejections:
            rule_id = str(rejection.get("rule_id") or "unknown")
            lowering_rejection_rule_counts[rule_id] = lowering_rejection_rule_counts.get(rule_id, 0) + 1
            blocking_lowering_rejection_rule_counts[rule_id] = (
                blocking_lowering_rejection_rule_counts.get(rule_id, 0) + 1
            )
    return {
        "matched_effects": matched_effect_count,
        "matched_effect_count_before_limit": matched_effect_count,
        "emitted_effect_count": emitted_effect_count,
        "truncated": emitted_effect_count < matched_effect_count,
        "diagnostic_count_scope": "emitted_rows",
        "candidate_counts": candidate_counts,
        "replay_applicability_counts": replay_applicability_counts,
        "structural_for_replay_counts": structural_for_replay_counts,
        "metadata_only_count": metadata_only_count,
        "applied_count": applied_count,
        "requires_applied_count": requires_applied_count,
        "source_pathology_counts": dict(sorted(source_pathology_counts.items())),
        "compare_shape_counts": dict(sorted(compare_shape_counts.items())),
        "manual_compile_status_counts": dict(sorted(manual_compile_status_counts.items())),
        "manual_compile_rule_counts": dict(sorted(manual_compile_rule_counts.items())),
        "total_compiled_ops": total_ops,
        "rows_with_resolver_eids": rows_with_resolver_eids,
        "rows_with_lowering_observations": rows_with_lowering_observations,
        "lowering_observation_rule_counts": dict(
            sorted(lowering_observation_rule_counts.items())
        ),
        "rows_with_lowering_rejections": rows_with_lowering_rejections,
        "rows_with_blocking_lowering_rejections": rows_with_blocking_lowering_rejections,
        "rows_with_source_acquisition_observations": (
            rows_with_source_acquisition_observations
        ),
        "source_acquisition_observation_rule_counts": dict(
            sorted(source_acquisition_observation_rule_counts.items())
        ),
        "rows_with_source_acquisition_rejections": rows_with_source_acquisition_rejections,
        "source_acquisition_rejection_rule_counts": dict(
            sorted(source_acquisition_rejection_rule_counts.items())
        ),
        "lowering_rejection_rule_counts": dict(sorted(lowering_rejection_rule_counts.items())),
        "blocking_lowering_rejection_rule_counts": dict(
            sorted(blocking_lowering_rejection_rule_counts.items())
        ),
    }


def uk_effects_report_jsonable(
    *,
    statute_id: str,
    rows: tuple[_EffectReportRow, ...],
    filters: _EffectFilters,
    summary_only: bool = False,
    matched_effect_count_before_limit: int | None = None,
    source: dict[str, Any] | None = None,
    parse_rejections: tuple[dict[str, Any], ...] = (),
    source_parse_observations: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    parse_observation_rows = tuple(dict(item) for item in parse_rejections)
    parse_rejection_rows = _blocking_rows(parse_observation_rows)
    source_parse_observation_rows = tuple(dict(item) for item in source_parse_observations)
    source_parse_rejection_rows = _blocking_rows(source_parse_observation_rows)
    payload: dict[str, Any] = {
        "report_kind": "uk_effects_frontier_report",
        "statute_id": statute_id,
        "filters": _effect_filters_jsonable(filters),
        "summary": uk_effects_summary_counts(
            rows,
            matched_effect_count_before_limit=matched_effect_count_before_limit,
        ),
        "effect_feed_parse_rejections": {
            "count": len(parse_rejection_rows),
            "rule_counts": _rule_counts(parse_rejection_rows),
            "rows": list(parse_rejection_rows),
        },
        "effect_feed_observation_count": len(parse_observation_rows),
        "effect_feed_observation_rule_counts": _rule_counts(parse_observation_rows),
        "effect_feed_observations": list(parse_observation_rows),
        "source_parse_rejections": {
            "count": len(source_parse_rejection_rows),
            "rule_counts": _rule_counts(source_parse_rejection_rows),
            "rows": list(source_parse_rejection_rows),
        },
        "source_parse_observation_count": len(source_parse_observation_rows),
        "source_parse_observation_rule_counts": _rule_counts(source_parse_observation_rows),
        "source_parse_observations": list(source_parse_observation_rows),
    }
    if source is not None:
        payload["source"] = source
    if not summary_only:
        payload["rows"] = [_effect_report_row_jsonable(row) for row in rows]
    return payload


def _effect_context_source_jsonable(context: _EffectSummaryContext) -> dict[str, Any]:
    return {
        "archive_path": str(getattr(context, "archive_path", "")),
        "enacted_url": str(getattr(context, "enacted_url", "")),
        "oracle_url": str(getattr(context, "oracle_url", "")),
        "enacted_missing": bool(getattr(context, "enacted_missing", False)),
        "oracle_missing": bool(getattr(context, "oracle_missing", False)),
        "enacted_source_status": str(getattr(context, "enacted_source_status", "")),
        "oracle_source_status": str(getattr(context, "oracle_source_status", "")),
        "enacted_source_size": int(getattr(context, "enacted_source_size", 0) or 0),
        "oracle_source_size": int(getattr(context, "oracle_source_size", 0) or 0),
        "enacted_source_sha256": str(getattr(context, "enacted_source_sha256", "") or ""),
        "oracle_source_sha256": str(getattr(context, "oracle_source_sha256", "") or ""),
        "enacted_source_parse_failed": bool(
            getattr(context, "enacted_source_parse_failed", False)
        ),
        "oracle_source_parse_failed": bool(getattr(context, "oracle_source_parse_failed", False)),
    }


def _rule_counts(rows: tuple[dict[str, Any], ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        rule_id = str(row.get("rule_id") or "unknown")
        counts[rule_id] = counts.get(rule_id, 0) + 1
    return dict(sorted(counts.items()))


def _blocking_rows(rows: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if is_blocking_compile_record(row))


def _effect_filters_jsonable(filters: _EffectFilters) -> dict[str, Any]:
    return {
        "affected_contains": filters.affected_contains,
        "affecting_contains": filters.affecting_contains,
        "effect_type_contains": filters.effect_type_contains,
        "source_pathology": filters.source_pathology,
        "lowering_rule": filters.lowering_rule,
        "source_acquisition_rule": filters.source_acquisition_rule,
        "manual_compile_status": filters.manual_compile_status,
        "manual_compile_rule": filters.manual_compile_rule,
        "applied_only": filters.applied_only,
        "structural_only": filters.structural_only,
        "candidate_only": filters.candidate_only,
        "non_candidate_only": filters.non_candidate_only,
        "limit": filters.limit,
        "applicability_mode": filters.applicability_mode,
    }


def _effect_rows_to_summarize(
    rows: list[Any],
    *,
    limit: int | None,
    candidate_only: bool,
    non_candidate_only: bool,
    post_summary_filter: bool = False,
) -> list[Any]:
    if limit is None or candidate_only or non_candidate_only or post_summary_filter:
        return rows
    return rows[:limit]


def _effect_summary_matches_filters(
    summary: _EffectSummary,
    *,
    source_pathology: str = "",
    lowering_rule: str = "",
    source_acquisition_rule: str = "",
    manual_compile_status: str = "",
    manual_compile_rule: str = "",
) -> bool:
    if source_pathology:
        actual_source_pathology = summary.source_pathology or "__none__"
        if actual_source_pathology != source_pathology:
            return False
    if lowering_rule:
        lowering_rules = _rule_counts(tuple(summary.lowering_rejections))
        if lowering_rule not in lowering_rules:
            return False
    if source_acquisition_rule:
        source_acquisition_rules = _rule_counts(tuple(summary.source_acquisition_rejections))
        if source_acquisition_rule not in source_acquisition_rules:
            return False
    if manual_compile_status:
        actual_status = summary.manual_compile_status or "__none__"
        if actual_status != manual_compile_status:
            return False
    if manual_compile_rule:
        actual_rule = summary.manual_compile_rule_id or "__none__"
        if actual_rule != manual_compile_rule:
            return False
    return True


def _effect_report_row_jsonable(row: _EffectReportRow) -> dict[str, Any]:
    from lawvm.tools.uk_effect import (
        blocking_lowering_rejection_rule_counts,
        has_blocking_lowering_rejection,
        lowering_rejection_rule_counts,
    )

    effect = row.effect
    summary = row.summary
    source_acquisition_observations = tuple(summary.source_acquisition_rejections)
    source_acquisition_rejections = _blocking_rows(source_acquisition_observations)
    lowering_observations = tuple(summary.lowering_rejections)
    lowering_rejections = _blocking_rows(lowering_observations)
    return {
        "effect_id": effect.effect_id,
        "effect_type": effect.effect_type or "",
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "effective_date": effect.effective_date or "",
        "applied": effect.applied,
        "requires_applied": effect.requires_applied,
        "metadata_only": bool(getattr(effect, "metadata_only", False)),
        "replay_applicable": summary.replay_applicable,
        "structural": effect.is_structural,
        "structural_for_replay": summary.structural_for_replay,
        "applicability_mode": summary.applicability_mode,
        "source_pathology": summary.source_pathology or "",
        "source": {
            "extracted": summary.source_extracted,
            "tag": summary.source_extracted_tag,
            "text_preview": summary.source_extracted_text_preview,
        },
        "affecting_source_witness": {
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_status": summary.affecting_source_status,
            "source_size": summary.affecting_source_size,
            "source_sha256": summary.affecting_source_sha256,
        },
        "compare_shape": summary.compare_shape or "",
        "manual_compile_frontier": {
            "status": summary.manual_compile_status or "",
            "rule_id": summary.manual_compile_rule_id or "",
            "reason": summary.manual_compile_reason or "",
            "lowering_rule_ids": list(summary.manual_compile_lowering_rule_ids),
            "blocking_lowering_rule_ids": list(
                summary.manual_compile_blocking_lowering_rule_ids
            ),
        },
        "candidate": summary.candidate,
        "compiled_op_count": summary.n_ops,
        "resolver_eids": list(summary.resolver_eids),
        "lowering_observation_rule_counts": lowering_rejection_rule_counts(
            list(lowering_observations)
        ),
        "lowering_observations": [dict(item) for item in lowering_observations],
        "lowering_rejection_rule_counts": lowering_rejection_rule_counts(
            list(lowering_rejections)
        ),
        "source_acquisition_rejection_rule_counts": _rule_counts(
            source_acquisition_rejections
        ),
        "source_acquisition_rejections": [
            dict(item) for item in source_acquisition_rejections
        ],
        "source_acquisition_observation_rule_counts": _rule_counts(
            source_acquisition_observations
        ),
        "source_acquisition_observations": [
            dict(item) for item in source_acquisition_observations
        ],
        "blocking_lowering_rejection_rule_counts": blocking_lowering_rejection_rule_counts(
            lowering_rejections
        ),
        "has_blocking_lowering_rejection": has_blocking_lowering_rejection(
            lowering_rejections
        ),
        "lowering_rejections": [dict(item) for item in lowering_rejections],
    }


def _manual_compile_work_item_id(
    *,
    statute_id: str,
    effect: Any,
    summary: _EffectSummary,
) -> str:
    parts = (
        "uk_manual_compile_frontier",
        statute_id,
        str(getattr(effect, "effect_id", "") or ""),
        str(getattr(effect, "affecting_act_id", "") or ""),
        str(getattr(effect, "affected_provisions", "") or ""),
        summary.source_extracted_text_preview or "",
        summary.manual_compile_status or "",
        summary.manual_compile_rule_id or "",
    )
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"uk-manual-frontier-{digest}"


def _manual_compile_source_jsonable(source: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(source)
    preview = str(payload.get("text_preview") or "")
    payload["text_preview_sha256"] = (
        hashlib.sha256(preview.encode("utf-8")).hexdigest() if preview else ""
    )
    return payload


def _quoted_for_substitute_pair(source_preview: str) -> tuple[str, str]:
    """Return the quoted preimage/replacement pair from a simple formula."""
    replacement_match = re.search(
        r"\bfor\b.{0,240}?[\"“](?P<old>[^\"”]{1,240})[\"”]\s+substitute\s+[\"“](?P<new>[^\"”]{1,240})[\"”]",
        " ".join(source_preview.split()),
        flags=re.I,
    )
    if replacement_match is None:
        return "", ""
    return (
        " ".join(replacement_match.group("old").split()),
        " ".join(replacement_match.group("new").split()),
    )


def _surface_text_rewrite_claim_template(
    *,
    statute_id: str,
    row: _EffectReportRow,
    action_family: str,
    facet_family: str,
    placement_family: str,
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    text_match, replacement = _quoted_for_substitute_pair(source_preview)
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "facet_family": facet_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "text_match": text_match,
        "replacement": replacement,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def _bounded_mutation_claim_template(
    *,
    statute_id: str,
    row: _EffectReportRow,
    action_family: str,
    placement_family: str,
    required_ownership: list[str],
    required_validator_checks: list[str],
) -> dict[str, Any]:
    summary = row.summary
    effect = row.effect
    source_preview = " ".join((summary.source_extracted_text_preview or "").split())
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": action_family,
        "placement_family": placement_family,
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "candidate_target_surface": effect.affected_provisions,
        "candidate_source_preview": source_preview[:500],
        "required_ownership": required_ownership,
        "required_validator_checks": required_validator_checks,
        "executable": False,
    }


def _manual_compile_suggested_claim_template(
    *,
    statute_id: str,
    row: _EffectReportRow,
) -> dict[str, Any]:
    """Return a non-executable semantic-claim template for known manual families."""
    summary = row.summary
    effect = row.effect
    if summary.manual_compile_rule_id == "uk_manual_frontier_heading_facet_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="facet_text_rewrite",
            facet_family="heading_or_title",
            placement_family="explicit_facet_target_required",
            required_validator_checks=[
                "source_witness_targets_heading_title_or_sidenote_facet",
                "claim_identifies_exact_target_facet_not_host_body",
                "claim_preserves_host_body_text_and_children",
                "claim_text_preimage_matches_target_facet_surface",
                "changed_paths_are_within_declared_facet_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_crossheading_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="crossheading_text_rewrite",
            facet_family="crossheading",
            placement_family="explicit_crossheading_carrier_required",
            required_validator_checks=[
                "source_witness_targets_crossheading_surface",
                "claim_identifies_exact_crossheading_carrier",
                "claim_preserves_neighbouring_sections_and_body_text",
                "claim_text_preimage_matches_crossheading_surface",
                "changed_paths_are_within_declared_crossheading_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_note_candidate":
        return _surface_text_rewrite_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_note_text_rewrite",
            facet_family="schedule_note",
            placement_family="explicit_schedule_note_carrier_required",
            required_validator_checks=[
                "source_witness_targets_schedule_note_surface",
                "claim_identifies_exact_schedule_note_carrier",
                "claim_preserves_schedule_paragraph_body_structure",
                "claim_text_preimage_matches_schedule_note_surface",
                "changed_paths_are_within_declared_schedule_note_target",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_schedule_list_entry_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="schedule_list_entry_mutation",
            placement_family="entry_anchor_requires_carrier_claim",
            required_ownership=[
                "source_named_entry_anchor",
                "entry_carrier",
                "sibling_insertion_or_replacement_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_schedule_or_list_entry_anchor",
                "claim_identifies_exact_entry_carrier",
                "claim_identifies_predecessor_or_replaced_entry",
                "claim_preserves_unclaimed_sibling_entries",
                "changed_paths_are_within_claimed_entry_boundary",
            ],
        )
    if summary.manual_compile_rule_id in {
        "uk_manual_frontier_table_entry_candidate",
        "uk_manual_frontier_table_entry_deictic_candidate",
        "uk_manual_frontier_table_column_insert_candidate",
        "uk_manual_frontier_table_appropriate_place_candidate",
    }:
        placement_family_by_rule = {
            "uk_manual_frontier_table_entry_candidate": "table_entry_anchor_required",
            "uk_manual_frontier_table_entry_deictic_candidate": "deictic_table_entry_anchor_required",
            "uk_manual_frontier_table_column_insert_candidate": "table_column_boundary_required",
            "uk_manual_frontier_table_appropriate_place_candidate": "appropriate_place_table_entry_requires_ordering_claim",
        }
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_surface_mutation",
            placement_family=placement_family_by_rule[summary.manual_compile_rule_id],
            required_ownership=[
                "source_named_table_surface",
                "row_or_column_carrier",
                "cell_alignment_or_column_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_table_entry_or_column_surface",
                "claim_identifies_exact_table_carrier",
                "claim_identifies_row_or_column_boundary",
                "claim_preserves_unclaimed_rows_columns_and_cells",
                "changed_paths_are_within_claimed_table_surface",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_appropriate_place_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="appropriate_place_mutation",
            placement_family="appropriate_place_requires_anchor_claim",
            required_ownership=[
                "source_named_insertion_payload",
                "validated_predecessor_or_successor_anchor",
                "target_container_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_uses_appropriate_place_formula",
                "claim_supplies_exact_anchor_or_ordering_rule",
                "claim_identifies_target_container_surface",
                "claim_identifies_payload_units_owned_by_source",
                "claim_preserves_unclaimed_sibling_units",
                "changed_paths_are_within_claimed_insertion_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_structural_sibling_insert_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="structural_sibling_insert",
            placement_family="source_named_sibling_anchor_required",
            required_ownership=[
                "source_named_sibling_anchor",
                "inserted_sibling_payload",
                "sibling_order_boundary",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_names_before_or_after_sibling_anchor",
                "claim_identifies_exact_parent_and_anchor_sibling",
                "claim_identifies_each_inserted_sibling_payload",
                "claim_preserves_anchor_and_unclaimed_siblings",
                "changed_paths_are_within_declared_sibling_insertion_boundary",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_repeal_table_candidate":
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="table_repeal_or_omission",
            placement_family="source_named_table_or_row_boundary_required",
            required_ownership=[
                "source_named_table_or_row_surface",
                "repealed_row_column_or_cell_boundary",
                "unclaimed_table_surface_preservation",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_targets_table_repeal_or_omission",
                "claim_identifies_exact_table_carrier",
                "claim_identifies_every_repealed_row_column_or_cell",
                "claim_preserves_unclaimed_table_rows_columns_and_cells",
                "changed_paths_are_within_declared_table_repeal_boundary",
            ],
        )
    if (
        summary.manual_compile_rule_id
        == "uk_manual_frontier_source_carried_structured_text_patch_candidate"
    ):
        return _bounded_mutation_claim_template(
            statute_id=statute_id,
            row=row,
            action_family="source_carried_structured_text_patch",
            placement_family="parent_formula_anchor_with_structured_payload_required",
            required_ownership=[
                "source_parent_formula_anchor",
                "source_carried_payload_units",
                "child_target_boundaries",
                "mutation_boundary",
            ],
            required_validator_checks=[
                "source_witness_contains_parent_formula_and_structured_payload",
                "claim_binds_payload_units_to_named_child_targets",
                "claim_preserves_unclaimed_parent_and_sibling_text",
                "claim_rejects_flattening_structured_payload_into_host_text",
                "changed_paths_are_within_claimed_child_target_boundaries",
            ],
        )
    if summary.manual_compile_rule_id == "uk_manual_frontier_range_to_container_candidate":
        blocking_rows = tuple(
            row
            for row in summary.lowering_rejections
            if str(row.get("rule_id") or "") == "uk_effect_range_to_container_substitution_rejected"
        )
        detail = dict(blocking_rows[0]) if blocking_rows else {}
        return {
            "schema": "lawvm.uk_semantic_compile_claim_template.v1",
            "claim_kind": "semantic_compile",
            "claim_status": "template_only_not_validated",
            "action_family": "range_to_container_substitution",
            "placement_family": "requires_lineage_or_migration_claim",
            "jurisdiction": "uk",
            "statute_id": statute_id,
            "effect_id": effect.effect_id,
            "affected_provisions": effect.affected_provisions,
            "affecting_act_id": effect.affecting_act_id,
            "affecting_provisions": effect.affecting_provisions,
            "source_pathology": summary.source_pathology or "",
            "source_range_kind": detail.get("source_range_kind", ""),
            "source_range_start": detail.get("source_range_start", ""),
            "source_range_end": detail.get("source_range_end", ""),
            "target_container_surface": detail.get("target_container_ref", effect.affected_provisions),
            "compiled_targets": list(detail.get("compiled_targets") or ()),
            "payload_kinds": list(detail.get("payload_kinds") or ()),
            "required_ownership": list(detail.get("required_ownership") or ()),
            "required_validator_checks": [
                "source_witness_contains_range_to_container_substitution",
                "claim_identifies_every_replaced_source_unit_in_range",
                "claim_identifies_container_payload_root_and_all_owned_children",
                "claim_emits_lineage_or_migration_events_for_displaced_units",
                "claim_preserves_crossheading_or_heading_facet_scope",
                "changed_paths_are_within_source_range_or_declared_migration_paths",
            ],
            "executable": False,
        }
    if (
        summary.manual_compile_rule_id
        != "uk_manual_frontier_appropriate_place_definition_entry_candidate"
    ):
        return {}
    source_preview = summary.source_extracted_text_preview or ""
    source_norm = " ".join(source_preview.split())
    match = re.search(
        r"\bat\s+(?:an?|the)\s+appropriate\s+place,?\s+"
        r"(?:in\s+alphabetical\s+order,?\s+)?insert\s*[—–-]\s*(?P<payload>.+)$",
        source_norm,
        flags=re.I | re.S,
    )
    if match is None:
        return {}
    payload = " ".join(match.group("payload").split()).strip()
    term_match = re.search(r"[\"“]\s*(?P<term>[^\"”]{1,160}?)\s*[\"”]", payload)
    term = " ".join(str(term_match.group("term") if term_match else "").split()).strip()
    return {
        "schema": "lawvm.uk_semantic_compile_claim_template.v1",
        "claim_kind": "semantic_compile",
        "claim_status": "template_only_not_validated",
        "action_family": "definition_entry_insert",
        "placement_family": "appropriate_place_requires_anchor_claim",
        "jurisdiction": "uk",
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_act_id": effect.affecting_act_id,
        "affecting_provisions": effect.affecting_provisions,
        "source_pathology": summary.source_pathology or "",
        "source_preview_sha256": hashlib.sha256(source_preview.encode("utf-8")).hexdigest()
        if source_preview
        else "",
        "inserted_definition_term": term,
        "inserted_definition_entry_preview": payload[:500],
        "candidate_target_surface": effect.affected_provisions,
        "required_validator_checks": [
            "source_witness_contains_exact_appropriate_place_instruction",
            "payload_is_complete_definition_entry",
            "claim_supplies_exact_definition_entry_anchor_or_insertion_index",
            "target_subtree_contains_definition_list_surface",
            "inserted_term_is_not_already_present_in_target_at_effective_preimage",
            "changed_paths_remain_inside_claimed_interpretation_target",
        ],
        "executable": False,
    }


def _uk_replay_regime_jsonable(regime: Any) -> dict[str, Any]:
    return {
        "allow_metadata_backfill": bool(regime.allow_metadata_backfill),
        "allow_oracle_alignment": bool(regime.allow_oracle_alignment),
        "allow_metadata_only_effects": bool(regime.allow_metadata_only_effects),
        "applicability_mode": str(regime.applicability_mode),
        "authority_mode": str(regime.authority_mode),
    }


def _manual_compile_evidence_row_jsonable(
    *,
    statute_id: str,
    row: _EffectReportRow,
    context: _EffectSummaryContext,
    replay_regime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    effect_payload = _effect_report_row_jsonable(row)
    summary = row.summary
    effect = row.effect
    suggested_claim_template = _manual_compile_suggested_claim_template(
        statute_id=statute_id,
        row=row,
    )
    replay_regime_payload = {
        str(key): value
        for key, value in dict(replay_regime or {}).items()
        if value is not None
    }
    return {
        "schema": "lawvm.uk_manual_compile_frontier.v1",
        "rule_id": "uk_manual_compile_frontier_workqueue",
        "family": "manual_compile_frontier",
        "phase": "lowering",
        "jurisdiction": "uk",
        "work_item_kind": "semantic_compile_candidate",
        "claim_kind": "semantic_compile",
        "claim_status": "unresolved_work_item",
        "validator_status": "not_validated",
        "work_item_id": _manual_compile_work_item_id(
            statute_id=statute_id,
            effect=effect,
            summary=summary,
        ),
        "statute_id": statute_id,
        "effect_id": effect.effect_id,
        "affected_uri": str(getattr(effect, "affected_uri", "") or ""),
        "affecting_uri": str(getattr(effect, "affecting_uri", "") or ""),
        "affecting_act_id": effect.affecting_act_id,
        "affected_provisions": effect.affected_provisions,
        "affecting_provisions": effect.affecting_provisions,
        "effect_type": effect.effect_type or "",
        "effective_date": effect.effective_date or "",
        "applied": bool(effect.applied),
        "requires_applied": bool(effect.requires_applied),
        "metadata_only": bool(getattr(effect, "metadata_only", False)),
        "manual_compile_status": summary.manual_compile_status or "",
        "manual_compile_rule_id": summary.manual_compile_rule_id or "",
        "manual_compile_reason": summary.manual_compile_reason or "",
        "manual_compile_lowering_rule_ids": list(
            summary.manual_compile_lowering_rule_ids
        ),
        "manual_compile_blocking_lowering_rule_ids": list(
            summary.manual_compile_blocking_lowering_rule_ids
        ),
        "suggested_claim_template_status": (
            "available" if suggested_claim_template else "not_available"
        ),
        "suggested_claim_template": suggested_claim_template,
        "source_pathology": summary.source_pathology or "",
        "source": _manual_compile_source_jsonable(effect_payload["source"]),
        "affecting_source_witness": effect_payload["affecting_source_witness"],
        "target_context": {
            "surface": "effect_feed_affected_provisions",
            "affected_provisions": effect.affected_provisions,
            "resolver_eids": effect_payload["resolver_eids"],
            "compare_shape": effect_payload["compare_shape"],
        },
        "compiled_op_count": summary.n_ops,
        "replay_applicable": summary.replay_applicable,
        "structural_for_replay": summary.structural_for_replay,
        "lowering_observation_rule_counts": effect_payload["lowering_observation_rule_counts"],
        "lowering_observations": effect_payload["lowering_observations"],
        "lowering_rejection_rule_counts": effect_payload["lowering_rejection_rule_counts"],
        "lowering_rejections": effect_payload["lowering_rejections"],
        "blocking_lowering_rejection_rule_counts": (
            effect_payload["blocking_lowering_rejection_rule_counts"]
        ),
        "source_acquisition_rejection_rule_counts": (
            effect_payload["source_acquisition_rejection_rule_counts"]
        ),
        "source_acquisition_rejections": effect_payload["source_acquisition_rejections"],
        "source_acquisition_observation_rule_counts": (
            effect_payload["source_acquisition_observation_rule_counts"]
        ),
        "source_acquisition_observations": effect_payload["source_acquisition_observations"],
        "source_witness": _effect_context_source_jsonable(context),
        "replay_regime": replay_regime_payload,
        "blocking": False,
        "strict_disposition": "record",
        "quirks_disposition": "record",
    }


def _write_manual_compile_evidence_jsonl(
    path: Path,
    *,
    statute_id: str,
    rows: tuple[_EffectReportRow, ...],
    context: _EffectSummaryContext,
    replay_regime: Mapping[str, Any] | None = None,
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with open(path, "w", encoding="utf-8") as handle:
        for row in rows:
            payload = _manual_compile_evidence_row_jsonable(
                statute_id=statute_id,
                row=row,
                context=context,
                replay_regime=replay_regime,
            )
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    return count


def _format_count_map(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    return ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))


def _format_row_rule_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    return ",".join(f"{rule_id}={count}" for rule_id, count in sorted(counts.items()))


def _print_uk_effects_summary(summary_counts: dict[str, Any]) -> None:
    print(f"Matched effects: {summary_counts['matched_effects']}")
    if summary_counts["emitted_effect_count"] != summary_counts["matched_effects"]:
        print(f"Emitted effects: {summary_counts['emitted_effect_count']}")
    truncated = bool(
        summary_counts.get(
            "truncated",
            summary_counts["emitted_effect_count"] < summary_counts["matched_effects"],
        )
    )
    print(f"Truncated: {str(truncated).lower()}")
    if truncated:
        print(
            "Diagnostic counts scope: "
            f"{summary_counts.get('diagnostic_count_scope', 'emitted_rows')}"
        )
    print(
        "Candidates: "
        f"{summary_counts['candidate_counts']['candidate']} yes / "
        f"{summary_counts['candidate_counts']['not_candidate']} no"
    )
    print(
        "Replay-applicable: "
        f"{summary_counts['replay_applicability_counts']['replay_applicable']} yes / "
        f"{summary_counts['replay_applicability_counts']['not_replay_applicable']} no"
    )
    print(
        "Structural-for-replay: "
        f"{summary_counts['structural_for_replay_counts']['structural_for_replay']} yes / "
        f"{summary_counts['structural_for_replay_counts']['not_structural_for_replay']} no"
    )
    print(
        "Effect lanes: "
        f"metadata-only={summary_counts['metadata_only_count']} "
        f"applied={summary_counts['applied_count']} "
        f"requires-applied={summary_counts['requires_applied_count']}"
    )
    print(f"Compiled ops: {summary_counts['total_compiled_ops']}")
    print(
        "Rows with resolver EIDs: "
        f"{summary_counts.get('rows_with_resolver_eids', 0)}"
    )
    print(
        "Rows with lowering rejections: "
        f"{summary_counts.get('rows_with_lowering_rejections', 0)}"
    )
    source_pathology_counts = summary_counts.get("source_pathology_counts", {})
    if source_pathology_counts:
        print("Source pathology counts: " + _format_count_map(source_pathology_counts))
    compare_shape_counts = summary_counts.get("compare_shape_counts", {})
    if compare_shape_counts:
        print("Compare shape counts: " + _format_count_map(compare_shape_counts))
    manual_compile_status_counts = summary_counts.get("manual_compile_status_counts", {})
    if manual_compile_status_counts:
        print(
            "Manual compile frontier statuses: "
            + _format_count_map(manual_compile_status_counts)
        )
    manual_compile_rule_counts = summary_counts.get("manual_compile_rule_counts", {})
    if manual_compile_rule_counts:
        print("Manual compile frontier rules:")
        for rule_id, count in manual_compile_rule_counts.items():
            print(f"  {rule_id}: {count}")
    source_acquisition_rejection_rule_counts = summary_counts.get(
        "source_acquisition_rejection_rule_counts",
        {},
    )
    source_acquisition_observation_rule_counts = summary_counts.get(
        "source_acquisition_observation_rule_counts",
        {},
    )
    if source_acquisition_observation_rule_counts:
        print(
            "Rows with source acquisition observations: "
            f"{summary_counts.get('rows_with_source_acquisition_observations', 0)}"
        )
        print("Source acquisition observation rules:")
        for rule_id, count in source_acquisition_observation_rule_counts.items():
            print(f"  {rule_id}: {count}")
    if source_acquisition_rejection_rule_counts:
        print(
            "Rows with source acquisition rejections: "
            f"{summary_counts.get('rows_with_source_acquisition_rejections', 0)}"
        )
        print("Source acquisition rejection rules:")
        for rule_id, count in source_acquisition_rejection_rule_counts.items():
            print(f"  {rule_id}: {count}")
    if summary_counts.get("lowering_observation_rule_counts"):
        print(
            "Rows with lowering observations: "
            f"{summary_counts.get('rows_with_lowering_observations', 0)}"
        )
        print("Lowering observation rules:")
        for rule_id, count in summary_counts["lowering_observation_rule_counts"].items():
            print(f"  {rule_id}: {count}")
    if summary_counts["lowering_rejection_rule_counts"]:
        print("Lowering rejection rules:")
        for rule_id, count in summary_counts["lowering_rejection_rule_counts"].items():
            print(f"  {rule_id}: {count}")
    if summary_counts["blocking_lowering_rejection_rule_counts"]:
        print(
            "Rows with blocking lowering rejections: "
            f"{summary_counts.get('rows_with_blocking_lowering_rejections', 0)}"
        )
        print("Blocking lowering rejection rules:")
        for rule_id, count in summary_counts["blocking_lowering_rejection_rule_counts"].items():
            print(f"  {rule_id}: {count}")


def main(args: "argparse.Namespace") -> None:
    from farchive import Farchive
    from lawvm.tools.uk_replay_regime import normalize_uk_replay_regime
    from lawvm.uk_legislation.effects import (
        load_effects_for_statute_from_archive,
    )

    replay_regime = normalize_uk_replay_regime(args)
    replay_regime_payload = _uk_replay_regime_jsonable(replay_regime)
    statute_id: str = args.statute_id
    db_arg: Optional[str] = getattr(args, "db", None)
    affected_contains: str = (getattr(args, "affected_contains", "") or "").lower()
    affecting_contains: str = (getattr(args, "affecting_contains", "") or "").lower()
    effect_type_contains: str = (getattr(args, "effect_type_contains", "") or "").lower()
    source_pathology_filter: str = getattr(args, "source_pathology", "") or ""
    lowering_rule_filter: str = getattr(args, "lowering_rule", "") or ""
    source_acquisition_rule_filter: str = getattr(args, "source_acquisition_rule", "") or ""
    manual_compile_status_filter: str = getattr(args, "manual_compile_status", "") or ""
    manual_compile_rule_filter: str = getattr(args, "manual_compile_rule", "") or ""
    limit: Optional[int] = getattr(args, "limit", None)
    applied_only: bool = bool(getattr(args, "applied_only", False))
    structural_only: bool = bool(getattr(args, "structural_only", False))
    candidate_only: bool = bool(getattr(args, "candidate_only", False))
    non_candidate_only: bool = bool(getattr(args, "non_candidate_only", False))
    json_output: bool = bool(getattr(args, "json", False))
    summary_only: bool = bool(getattr(args, "summary_only", False))
    evidence_jsonl_arg: str = getattr(args, "evidence_jsonl", "") or ""
    applicability_mode: str = replay_regime.applicability_mode
    if candidate_only and non_candidate_only:
        print("error: --candidate-only cannot be combined with --non-candidate-only", file=sys.stderr)
        sys.exit(2)
    if limit is not None and limit < 0:
        print("error: --limit must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)
    if evidence_jsonl_arg and not (manual_compile_status_filter or manual_compile_rule_filter):
        print(
            "error: --evidence-jsonl requires --manual-compile-status or --manual-compile-rule",
            file=sys.stderr,
        )
        sys.exit(2)
    filters = _EffectFilters(
        affected_contains=affected_contains,
        affecting_contains=affecting_contains,
        effect_type_contains=effect_type_contains,
        source_pathology=source_pathology_filter,
        lowering_rule=lowering_rule_filter,
        source_acquisition_rule=source_acquisition_rule_filter,
        manual_compile_status=manual_compile_status_filter,
        manual_compile_rule=manual_compile_rule_filter,
        applied_only=applied_only,
        structural_only=structural_only,
        candidate_only=candidate_only,
        non_candidate_only=non_candidate_only,
        limit=limit,
        applicability_mode=applicability_mode,
    )

    db_path = Path(db_arg) if db_arg else _DEFAULT_DB
    if not db_path.exists():
        print(f"error: archive DB not found at {db_path}", file=sys.stderr)
        sys.exit(1)

    with Farchive(db_path) as archive:
        parse_rejections: list[dict[str, Any]] = []
        effects = load_effects_for_statute_from_archive(
            statute_id,
            archive,
            parse_rejections_out=parse_rejections,
        )
        context = build_uk_effect_summary_context(statute_id, archive=archive)

        def _matches(effect) -> bool:  # noqa: ANN001
            if applied_only and not effect.applied:
                return False
            if structural_only and not effect.is_structural:
                return False
            if affected_contains and affected_contains not in effect.affected_provisions.lower():
                return False
            if affecting_contains and affecting_contains not in effect.affecting_provisions.lower():
                return False
            if effect_type_contains and effect_type_contains not in (effect.effect_type or "").lower():
                return False
            return True

        rows = [effect for effect in effects if _matches(effect)]
        rows.sort(key=lambda effect: (effect.effective_date or "9999-99-99", effect.modified, effect.effect_id))
        rows_to_summarize = _effect_rows_to_summarize(
            rows,
            limit=limit,
            candidate_only=candidate_only,
            non_candidate_only=non_candidate_only,
            post_summary_filter=bool(
                source_pathology_filter
                or lowering_rule_filter
                or source_acquisition_rule_filter
                or manual_compile_status_filter
                or manual_compile_rule_filter
            ),
        )
        report_rows = tuple(
            _EffectReportRow(
                effect=effect,
                summary=summarize_uk_effect(
                    effect,
                    archive=archive,
                    context=context,
                    applicability_mode=applicability_mode,
                ),
            )
            for effect in rows_to_summarize
        )
        if candidate_only:
            report_rows = tuple(row for row in report_rows if row.summary.candidate)
        if non_candidate_only:
            report_rows = tuple(row for row in report_rows if not row.summary.candidate)
        if (
            source_pathology_filter
            or lowering_rule_filter
            or source_acquisition_rule_filter
            or manual_compile_status_filter
            or manual_compile_rule_filter
        ):
            report_rows = tuple(
                row
                for row in report_rows
                if _effect_summary_matches_filters(
                    row.summary,
                    source_pathology=source_pathology_filter,
                    lowering_rule=lowering_rule_filter,
                    source_acquisition_rule=source_acquisition_rule_filter,
                    manual_compile_status=manual_compile_status_filter,
                    manual_compile_rule=manual_compile_rule_filter,
                )
            )
        matched_effect_count_before_limit = (
            len(report_rows)
            if (
                candidate_only
                or non_candidate_only
                or source_pathology_filter
                or lowering_rule_filter
                or source_acquisition_rule_filter
                or manual_compile_status_filter
                or manual_compile_rule_filter
            )
            else len(rows)
        )
        if limit is not None:
            report_rows = report_rows[:limit]

        evidence_jsonl_path = Path(evidence_jsonl_arg) if evidence_jsonl_arg else None
        evidence_jsonl_count = 0
        if evidence_jsonl_path is not None:
            evidence_jsonl_count = _write_manual_compile_evidence_jsonl(
                evidence_jsonl_path,
                statute_id=statute_id,
                rows=report_rows,
                context=context,
                replay_regime=replay_regime_payload,
            )

        if json_output:
            report = uk_effects_report_jsonable(
                statute_id=statute_id,
                rows=report_rows,
                filters=filters,
                summary_only=summary_only,
                matched_effect_count_before_limit=matched_effect_count_before_limit,
                source=_effect_context_source_jsonable(context),
                parse_rejections=tuple(parse_rejections),
                source_parse_observations=context.source_parse_observations,
            )
            if evidence_jsonl_path is not None:
                report["manual_compile_evidence_jsonl"] = {
                    "path": str(evidence_jsonl_path),
                    "rows": evidence_jsonl_count,
                    "replay_regime": replay_regime_payload,
                }
            print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
            return

        print(f"Statute: {statute_id}")
        print(f"Archive: {context.archive_path or '(unknown)'}")
        print(f"Enacted URL: {context.enacted_url or '(unknown)'}")
        print(f"Oracle URL: {context.oracle_url or '(unknown)'}")
        print(
            "Enacted source: "
            f"{context.enacted_source_status} ({context.enacted_source_size} bytes)"
        )
        print(
            "Oracle source:  "
            f"{context.oracle_source_status} ({context.oracle_source_size} bytes)"
        )
        summary_counts = uk_effects_summary_counts(
            report_rows,
            matched_effect_count_before_limit=matched_effect_count_before_limit,
        )
        _print_uk_effects_summary(summary_counts)
        if evidence_jsonl_path is not None:
            print(
                "Manual compile evidence JSONL: "
                f"{evidence_jsonl_path} rows={evidence_jsonl_count}"
            )
        parse_observation_rows = tuple(dict(item) for item in parse_rejections)
        parse_rejection_rows = _blocking_rows(parse_observation_rows)
        if parse_observation_rows:
            print("Effect feed parse/acquisition observations:")
            for rule_id, count in _rule_counts(parse_observation_rows).items():
                print(f"  {rule_id}: {count}")
        if parse_rejection_rows:
            print("Blocking effect feed parse/acquisition rejections:")
            for rule_id, count in _rule_counts(parse_rejection_rows).items():
                print(f"  {rule_id}: {count}")
        source_parse_observation_rows = tuple(dict(item) for item in context.source_parse_observations)
        source_parse_rejection_rows = _blocking_rows(source_parse_observation_rows)
        if source_parse_observation_rows:
            print("Source parse observations:")
            for rule_id, count in _rule_counts(source_parse_observation_rows).items():
                print(f"  {rule_id}: {count}")
        if source_parse_rejection_rows:
            print("Blocking source parse rejections:")
            for rule_id, count in _rule_counts(source_parse_rejection_rows).items():
                print(f"  {rule_id}: {count}")
        if not report_rows or summary_only:
            return
        print()

        for row in report_rows:
            effect = row.effect
            summary = row.summary
            print(effect.effect_id)
            print(f"  type:       {effect.effect_type or '(empty)'}")
            print(f"  affected:   {effect.affected_provisions}")
            print(f"  affecting:  {effect.affecting_act_id} {effect.affecting_provisions}")
            print(f"  effective:  {effect.effective_date or '(none)'}")
            print(
                f"  applied:    {effect.applied}  "
                f"requires-applied: {effect.requires_applied}  "
                f"metadata-only: {bool(getattr(effect, 'metadata_only', False))}"
            )
            print(
                "  replay:     "
                f"mode={summary.applicability_mode}  "
                f"applicable={summary.replay_applicable}  "
                f"structural={effect.is_structural}  "
                f"structural-for-replay={summary.structural_for_replay}"
            )
            print(f"  source:     {summary.source_pathology or '(none)'}  ops={summary.n_ops}")
            print(
                "  manual:    "
                f"{summary.manual_compile_status or '(none)'}  "
                f"{summary.manual_compile_rule_id or '(none)'}"
            )
            if summary.lowering_rejections:
                from lawvm.tools.uk_effect import (
                    blocking_lowering_rejection_rule_counts,
                    lowering_rejection_rule_counts,
                )

                counts = lowering_rejection_rule_counts(list(summary.lowering_rejections))
                count_text = _format_row_rule_counts(counts)
                print(
                    f"  lowering rejections: {len(summary.lowering_rejections)}  "
                    f"{count_text}"
                )
                blocking_counts = blocking_lowering_rejection_rule_counts(
                    summary.lowering_rejections
                )
                if blocking_counts:
                    blocking_count = sum(blocking_counts.values())
                    blocking_text = _format_row_rule_counts(blocking_counts)
                    print(
                        f"  blocking lowering: {blocking_count}  "
                        f"{blocking_text}"
                    )
            source_acquisition_observations = tuple(summary.source_acquisition_rejections)
            source_acquisition_rejections = _blocking_rows(source_acquisition_observations)
            if source_acquisition_observations:
                counts = _rule_counts(source_acquisition_observations)
                count_text = _format_row_rule_counts(counts)
                print(
                    f"  source acquisition observations: "
                    f"{len(source_acquisition_observations)}  "
                    f"{count_text}"
                )
            if source_acquisition_rejections:
                counts = _rule_counts(source_acquisition_rejections)
                count_text = _format_row_rule_counts(counts)
                print(
                    f"  source acquisition rejections: "
                    f"{len(source_acquisition_rejections)}  "
                    f"{count_text}"
                )
            print(f"  compare:    {summary.compare_shape or '(none)'}")
            print(
                f"  candidate:  "
                f"{'yes' if summary.candidate else 'no'}"
            )
            print()
