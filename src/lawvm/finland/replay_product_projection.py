"""Product-side validation and diagnostic projection for Finland replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Optional, cast

from lawvm.core import tree_ops as _tops
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings
from lawvm.core.tree_ops import iter_tree_invariant_violations
from lawvm.core.tree_ops import TreeInvariantKind
from lawvm.finland.replay_findings import (
    _emit_structural_dedup_warning,
    _replay_product_invariant_finding,
)
from lawvm.finland.replay_products import ReplayProducts, validate_replay_products
from lawvm.finland.statute import StatuteContext

_PRODUCT_TREE_INVARIANT_FAMILIES: tuple[TreeInvariantKind, ...] = (
    "duplicate_label",
    "unexpected_child_kind",
    "mixed_hierarchy_child",
)


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
    typed_product_tree_violations = {
        "replay_fold_tree": [
            violation.to_dict()
            for violation in iter_tree_invariant_violations(
                products.replay_fold_state.ir,
                families=_PRODUCT_TREE_INVARIANT_FAMILIES,
            )
        ],
        "materialized_tree": [
            violation.to_dict()
            for violation in iter_tree_invariant_violations(
                products.materialized_state.ir,
                families=_PRODUCT_TREE_INVARIANT_FAMILIES,
            )
        ],
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
    materialized_text_duplication_findings = build_text_duplication_findings(
        deduped_materialized_ir,
        phase="materialized",
        source_statute=request.parent_id,
    )
    if request.replay_meta_out is not None and materialized_text_duplication_findings:
        warnings = request.replay_meta_out.setdefault("text_duplication_warnings", [])
        cast(list[dict[str, object]], warnings).extend(
            {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            for finding in materialized_text_duplication_findings
        )
    if materialized_text_duplication_findings:
        seen_text_warnings = {
            (
                finding.kind,
                str(finding.detail.get("phase") or ""),
                str(finding.detail.get("kind") or ""),
                str(finding.detail.get("left") or ""),
                str(finding.detail.get("right") or ""),
            )
            for finding in request.replay_findings
            if finding.kind == "text_duplication_warning"
        }
        for finding in materialized_text_duplication_findings:
            warning = {
                key: value
                for key, value in finding.detail.items()
                if key != "message"
            }
            request.replay_print(
                f"WARNING text duplication: {warning['kind']} {warning['left']} <-> {warning['right']}"
            )
            key = (
                "text_duplication_warning",
                "materialized",
                str(warning.get("kind") or ""),
                str(warning.get("left") or ""),
                str(warning.get("right") or ""),
            )
            if key not in seen_text_warnings:
                request.replay_findings.append(finding)
                seen_text_warnings.add(key)

    return products
