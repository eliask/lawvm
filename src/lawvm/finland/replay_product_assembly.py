"""Assemble Finland replay products after amendment replay has folded."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Literal, Optional, cast

from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.metadata import _chapter_expiry_from_base
from lawvm.finland.ops import AmendmentOp, _apply_law_level_text_patches
from lawvm.finland.corpus import (
    get_consolidated_oracle_absent_repeal_target_versions,
    get_consolidated_oracle_reflected_section_original_versions,
    get_consolidated_oracle_single_future_repeal_overlay_versions,
)
from lawvm.finland.replay_capture import ReplayCaptureSinks
from lawvm.finland.replay_horizon import ReplayHorizonRequest, choose_replay_horizon
from lawvm.finland.replay_pipeline import ReplayPlan, ReplaySignalBuffers
from lawvm.finland.replay_product_projection import (
    ReplayProductProjectionRequest,
    project_replay_products,
)
from lawvm.finland.replay_findings import (
    materialized_attachments_wrapper_split_finding,
    materialized_provisions_wrapper_projection_finding,
)
from lawvm.finland.replay_products import (
    ReplayProducts,
    project_materialized_provisions_wrapper,
    _split_operatives_from_attachments_wrapper,
    build_replay_products,
)
from lawvm.finland.replay_timeline_diagnostics import (
    fi_bench_timeline_invariants_enabled,
    fi_timeline_invariants_opt_in_enabled,
)
from lawvm.finland.replay_tree_normalize import hoist_trailing_wrapup_ir
from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.finland.post_process import _consolidate_kumottu_range
from lawvm.finland.temporal_rewrites import _base_chapter_expiry_temporal_events

_FI_MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT_RULE = "fi_materialized_attachments_wrapper_split_v1"
_FI_MATERIALIZED_PROVISIONS_WRAPPER_PROJECTION_RULE = (
    "fi_materialized_provisions_wrapper_projection_v1"
)


@dataclass(frozen=True, slots=True)
class ReplayProductAssemblyRequest:
    parent_id: str
    mode: str
    as_of: str | None
    profile: Any
    plan: ReplayPlan
    corpus: Any
    oracle_selector: Any
    replay_fold_state: Any
    capture_sinks: ReplayCaptureSinks
    signals: ReplaySignalBuffers
    build_full_products: bool
    strict_johto_temporal: bool
    replay_meta_out: Optional[Dict[str, object]]
    replay_print: Callable[[str], None]
    debug_enabled: bool
    debug_log: Callable[[str, object], None]
    quiet: bool = True


def assemble_replay_products(request: ReplayProductAssemblyRequest) -> ReplayProducts:
    """Build, normalize, patch, and optionally validate replay products."""
    plan = request.plan
    source_adjudication = build_source_adjudication(
        request.parent_id,
        request.mode,
        cutoff_date=plan.cutoff_date.isoformat() if plan.cutoff_date else "",
        oracle_version_amendment_id=plan.oracle_version_amendment_id or "",
        oracle_suspect=plan.oracle_suspect or "",
        lineage=plan.amendment_records,
    )
    horizon = choose_replay_horizon(
        ReplayHorizonRequest(
            mode=cast(Literal["official_consolidation", "legal_pit"], request.mode),
            as_of=request.as_of or "",
            cutoff_date=plan.cutoff_date,
            amendment_records=plan.amendment_records,
            oracle_version_amendment_id=plan.oracle_version_amendment_id or "",
            compiled_ops=request.capture_sinks.compiled_ops or (),
            legal_operations=request.capture_sinks.legal_operations or (),
            oracle_reflected_section_original_versions=(
                get_consolidated_oracle_reflected_section_original_versions(
                    request.parent_id,
                    corpus=request.corpus,
                    selector=request.oracle_selector,
                )
            ),
            oracle_single_future_repeal_overlay_versions=(
                get_consolidated_oracle_single_future_repeal_overlay_versions(
                    request.parent_id,
                    corpus=request.corpus,
                    selector=request.oracle_selector,
                )
            ),
            oracle_absent_repeal_target_versions=(
                get_consolidated_oracle_absent_repeal_target_versions(
                    request.parent_id,
                    tuple(request.capture_sinks.legal_operations or ()),
                    corpus=request.corpus,
                    selector=request.oracle_selector,
                )
            ),
            replay_print=request.replay_print,
        )
    )

    base_chapter_expiries = _base_chapter_expiries_from_base(
        plan.ctx.base_xml_bytes,
        request.replay_print,
    )
    request.signals.temporal_events.extend(
        _base_chapter_expiry_temporal_events(
            target_statute=request.parent_id,
            chapter_expiries=base_chapter_expiries,
        )
    )
    products = build_replay_products(
        ctx=plan.ctx,
        statute_id=request.parent_id,
        replay_fold_state=request.replay_fold_state,
        lo_ops_out=request.capture_sinks.legal_operations,
        source_adjudication=source_adjudication,
        as_of=horizon.materialize_as_of,
        synthesize_repeal_placeholders=request.profile.synthesize_repeal_placeholders,
        repeal_placeholder_normalizer=cast(Callable[[object], object], _consolidate_kumottu_range),
        build_full_products=request.build_full_products,
        temporal_events=tuple(request.signals.temporal_events),
        strict_johto_temporal=request.strict_johto_temporal,
        migration_events=tuple(request.signals.migration_events),
        source_effects=tuple(request.signals.source_effects),
        effect_relations=tuple(request.signals.effect_relations),
        effect_lifecycle_events=tuple(request.signals.effect_lifecycle_events),
        expires_as_of=horizon.expires_as_of,
    )
    products = _normalize_product_trees(products)
    products = _apply_law_level_patches_if_needed(products, request)
    projected_materialized_ir = project_materialized_provisions_wrapper(
        products.materialized_state.ir,
        products.replay_fold_state.ir,
    )
    if projected_materialized_ir is not products.materialized_state.ir:
        request.signals.findings.append(
            materialized_provisions_wrapper_projection_finding(
                source_statute=request.parent_id,
                witness_rule_id=_FI_MATERIALIZED_PROVISIONS_WRAPPER_PROJECTION_RULE,
            )
        )
        products.materialized_state = products.materialized_state.with_ir(
            projected_materialized_ir
        )
    if request.build_full_products:
        if request.replay_meta_out is not None and (
            fi_timeline_invariants_opt_in_enabled()
            or fi_bench_timeline_invariants_enabled(diagnostic_replay=not request.quiet)
        ):
            request.replay_meta_out["enable_timeline_invariants"] = True
        products = project_replay_products(
            ReplayProductProjectionRequest(
                ctx=plan.ctx,
                products=products,
                parent_id=request.parent_id,
                replay_findings=request.signals.findings,
                replay_meta_out=request.replay_meta_out,
                replay_print=request.replay_print,
                debug_enabled=request.debug_enabled,
                debug_log=request.debug_log,
            )
        )
    split_materialized_ir = _split_operatives_from_attachments_wrapper(
        products.materialized_state.ir,
        products.replay_fold_state.ir,
    )
    if split_materialized_ir is not products.materialized_state.ir:
        request.signals.findings.append(
            materialized_attachments_wrapper_split_finding(
                source_statute=request.parent_id,
                moved_section_labels=_split_provisions_section_labels(split_materialized_ir),
                witness_rule_id=_FI_MATERIALIZED_ATTACHMENTS_WRAPPER_SPLIT_RULE,
            )
        )
        products.materialized_state = products.materialized_state.with_ir(split_materialized_ir)
    return products


def _base_chapter_expiries_from_base(
    base_xml_bytes: bytes,
    replay_print: Callable[[str], None],
) -> dict[str, str]:
    base_tree = parse_corpus_xml(base_xml_bytes)
    chapter_expiry = _chapter_expiry_from_base(base_tree)
    if chapter_expiry is None:
        return {}
    chapter_label, chapter_date = chapter_expiry
    replay_print(f"  base chapter expiry: luku {chapter_label} -> {chapter_date.isoformat()}")
    return {chapter_label: chapter_date.isoformat()}


def _normalize_product_trees(products: ReplayProducts) -> ReplayProducts:
    return ReplayProducts(
        replay_fold_state=products.replay_fold_state.with_ir(
            hoist_trailing_wrapup_ir(products.replay_fold_state.ir)
        ),
        materialized_state=products.materialized_state.with_ir(
            hoist_trailing_wrapup_ir(products.materialized_state.ir)
        ),
        timelines=products.timelines,
        temporal_events=products.temporal_events,
        migration_events=products.migration_events,
        source_effects=products.source_effects,
        effect_relations=products.effect_relations,
        effect_lifecycle_events=products.effect_lifecycle_events,
        fold_timeline_backfills=products.fold_timeline_backfills,
        timeline_version_dedupes=products.timeline_version_dedupes,
        editorial_repeal_notice_substring_witnesses=(
            products.editorial_repeal_notice_substring_witnesses
        ),
        dropped_cited_version_snapshots=products.dropped_cited_version_snapshots,
        materialization_spec=products.materialization_spec,
        source_adjudication=products.source_adjudication,
        tombstones=products.tombstones,
    )


def _split_provisions_section_labels(ir: Any) -> tuple[str, ...]:
    for child in getattr(ir, "children", ()):
        if getattr(child, "attrs", {}).get("name") != "statuteProvisionsWrapper":
            continue
        labels = [
            str(grandchild.label)
            for grandchild in getattr(child, "children", ())
            if _is_section_node(grandchild)
            if str(getattr(grandchild, "label", "") or "")
        ]
        return tuple(labels)
    return ()


def _is_section_node(node: Any) -> bool:
    kind = getattr(node, "kind", None)
    return kind is IRNodeKind.SECTION or str(kind) == "section"


def _apply_law_level_patches_if_needed(
    products: ReplayProducts,
    request: ReplayProductAssemblyRequest,
) -> ReplayProducts:
    if not request.capture_sinks.legal_operations or not request.build_full_products:
        return products
    law_level_patches = AmendmentOp.extract_law_level_text_patches(
        request.capture_sinks.legal_operations
    )
    if not law_level_patches:
        return products
    request.replay_print(
        f"  Applying {len(law_level_patches)} law-level text replacement(s)"
    )
    patched_ir = _apply_law_level_text_patches(
        products.materialized_state.ir,
        law_level_patches,
    )
    products.materialized_state = products.materialized_state.with_ir(patched_ir)
    return products
