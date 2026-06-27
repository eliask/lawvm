"""Product-side validation and diagnostic projection for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from lawvm.core import tree_ops as _tops
from lawvm.core.invariant_profiles import structural_product_hierarchical_profile
from lawvm.core.invariant_surface_matrix import (
    FI_MATERIALIZED_PRODUCT_SURFACE,
    FI_REPLAY_FOLD_SURFACE,
    project_replay_warning_findings,
    record_replay_profile,
)
from lawvm.core.phase_result import Finding

from lawvm.finland.definition_introducer import fi_definition_list_introducer_predicate
from lawvm.finland.replay_findings import (
    _emit_structural_dedup_warning,
    _replay_product_invariant_finding,
    cited_version_snapshot_drop_finding,
    editorial_repeal_notice_substring_finding,
    fold_timeline_backfill_finding,
    timeline_version_dedupe_finding,
)
from lawvm.finland.replay_timeline_diagnostics import project_timeline_invariant_findings
from lawvm.finland.replay_products import ReplayProducts
from lawvm.finland.replay_products import fi_product_tree_invariant_dicts
from lawvm.finland.replay_products import validate_replay_products
from lawvm.finland.statute import StatuteContext

_FI_REPLAY_FOLD_PRODUCT_TREE_PROFILE = structural_product_hierarchical_profile("replay_fold_tree")
_FI_MATERIALIZED_PRODUCT_TREE_PROFILE = structural_product_hierarchical_profile("materialized_tree")


@dataclass(frozen=True, slots=True)
class ReplayProductProjectionRequest:
    """Inputs for materialized-product validation and diagnostics."""

    ctx: StatuteContext
    products: ReplayProducts
    parent_id: str
    replay_findings: list[Finding]
    replay_meta_out: Optional[Dict[str, object]]
    replay_print: Callable[[str], None]
    debug_enabled: bool
    debug_log: Callable[[str, object], None]


def project_replay_products(request: ReplayProductProjectionRequest) -> ReplayProducts:
    """Validate replay products, dedup materialized IR, and project diagnostics."""
    products = request.products
    if products.fold_timeline_backfills:
        seen_backfills = {
            (
                finding.kind,
                str(finding.detail.get("address") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "REPLAY.FOLD_TIMELINE_BACKFILL"
        }
        for record in products.fold_timeline_backfills:
            key = ("REPLAY.FOLD_TIMELINE_BACKFILL", record.address)
            if key in seen_backfills:
                continue
            request.replay_findings.append(
                fold_timeline_backfill_finding(
                    source_statute=record.source_statute,
                    address=record.address,
                    effective=record.effective,
                    witness_rule_id=record.witness_rule_id,
                )
            )
            seen_backfills.add(key)
    if products.timeline_version_dedupes:
        seen_dedupes = {
            (
                finding.kind,
                str(finding.detail.get("address") or ""),
                str(finding.detail.get("witness_rule_id") or ""),
                str(finding.detail.get("effective") or ""),
                str(finding.detail.get("enacted") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "REPLAY.TIMELINE_VERSION_DEDUPE"
        }
        for record in products.timeline_version_dedupes:
            key = (
                "REPLAY.TIMELINE_VERSION_DEDUPE",
                record.address,
                record.witness_rule_id,
                record.effective,
                record.enacted,
            )
            if key in seen_dedupes:
                continue
            request.replay_findings.append(
                timeline_version_dedupe_finding(
                    source_statute=record.source_statute,
                    address=record.address,
                    effective=record.effective,
                    enacted=record.enacted,
                    variant_kind=record.variant_kind,
                    witness_rule_id=record.witness_rule_id,
                    removed_count=record.removed_count,
                )
            )
            seen_dedupes.add(key)
    if products.editorial_repeal_notice_substring_witnesses:
        seen_editorial = {
            (
                finding.kind,
                str(finding.detail.get("kind") or ""),
                str(finding.detail.get("label") or ""),
                str(finding.detail.get("witness_rule_id") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "REPLAY.EDITORIAL_REPEAL_NOTICE_SUBSTRING"
        }
        for witness in products.editorial_repeal_notice_substring_witnesses:
            key = (
                "REPLAY.EDITORIAL_REPEAL_NOTICE_SUBSTRING",
                witness.kind,
                witness.label,
                witness.witness_rule_id,
            )
            if key in seen_editorial:
                continue
            request.replay_findings.append(
                editorial_repeal_notice_substring_finding(
                    source_statute=request.parent_id,
                    kind=witness.kind,
                    label=witness.label,
                    clause_text=witness.clause_text,
                    witness_rule_id=witness.witness_rule_id,
                )
            )
            seen_editorial.add(key)
    if products.dropped_cited_version_snapshots:
        seen_cited_drops = {
            (
                finding.kind,
                str(finding.detail.get("op_id") or ""),
                str(finding.detail.get("witness_rule_id") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "REPLAY.CITED_VERSION_SNAPSHOT_DROP"
        }
        for drop in products.dropped_cited_version_snapshots:
            key = (
                "REPLAY.CITED_VERSION_SNAPSHOT_DROP",
                drop.op_id,
                drop.rule_id,
            )
            if key in seen_cited_drops:
                continue
            request.replay_findings.append(
                cited_version_snapshot_drop_finding(
                    source_statute=request.parent_id,
                    op_id=drop.op_id,
                    drop_source_statute=drop.source_statute,
                    effective=drop.effective,
                    target_path=tuple(
                        f"{kind}:{label}" for kind, label in drop.target_path
                    ),
                    witness_rule_id=drop.rule_id,
                )
            )
            seen_cited_drops.add(key)
    typed_product_tree_violations = {
        "replay_fold_tree": list(
            fi_product_tree_invariant_dicts(
                products.replay_fold_state.ir,
                _FI_REPLAY_FOLD_PRODUCT_TREE_PROFILE,
            )
        ),
        "materialized_tree": list(
            fi_product_tree_invariant_dicts(
                products.materialized_state.ir,
                _FI_MATERIALIZED_PRODUCT_TREE_PROFILE,
            )
        ),
    }
    product_violations = validate_replay_products(
        request.ctx,
        products,
        deep_materialization_check=request.debug_enabled,
    )
    if request.replay_meta_out is not None and product_violations:
        request.replay_meta_out["product_invariant_violations"] = list(product_violations)
        request.replay_meta_out["typed_product_tree_invariant_violations"] = typed_product_tree_violations
    if product_violations:
        for violation in product_violations:
            request.replay_findings.append(
                _replay_product_invariant_finding(
                    violation=violation,
                    source_statute=request.parent_id,
                )
            )
        seen_product_violations = {
            (
                finding.kind,
                str(finding.detail.get("violation") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION"
        }
        for violation in product_violations:
            request.replay_print(f"WARNING product invariant: {violation}")
            if ("APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION", violation) not in seen_product_violations:
                request.replay_findings.append(
                    _replay_product_invariant_finding(
                        violation=violation,
                        source_statute=request.parent_id,
                    )
                )
                seen_product_violations.add(("APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION", violation))
    if request.debug_enabled:
        for violation in product_violations:
            request.debug_log("  PRODUCT INVARIANT: %s", violation)

    deduped_materialized_ir = _tops.dedup_children_by_label(products.materialized_state.ir)
    deduped_materialized_ir = _emit_structural_dedup_warning(
        phase="materialized",
        before_ir=products.materialized_state.ir,
        after_ir=deduped_materialized_ir,
        source_statute=request.parent_id,
        replay_findings=request.replay_findings,
        replay_meta_out=request.replay_meta_out,
    )
    products.materialized_state = products.materialized_state.with_ir(deduped_materialized_ir)
    if request.replay_meta_out is not None:
        record_replay_profile(request.replay_meta_out, FI_MATERIALIZED_PRODUCT_SURFACE)
    project_replay_warning_findings(
        tree=deduped_materialized_ir,
        phase="materialized",
        source_statute=request.parent_id,
        warnings=FI_MATERIALIZED_PRODUCT_SURFACE.replay_profile.warnings,
        replay_findings=request.replay_findings,
        replay_meta_out=request.replay_meta_out,
        replay_print=request.replay_print,
        definition_introducer_predicate=fi_definition_list_introducer_predicate,
    )

    if request.replay_meta_out is not None and request.replay_meta_out.get(
        "enable_timeline_invariants"
    ):
        pit_date = (
            products.materialization_spec.as_of
            if products.materialization_spec is not None
            else None
        )
        project_timeline_invariant_findings(
            ir=products.materialized_state.ir,
            timelines=products.timelines,
            pit_date=pit_date,
            profile=FI_REPLAY_FOLD_SURFACE.replay_profile,
            replay_findings=request.replay_findings,
            replay_meta_out=request.replay_meta_out,
            replay_print=request.replay_print,
            source_statute=request.parent_id,
        )

    return products
