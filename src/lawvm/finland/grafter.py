import logging
import re
import datetime as dt
from collections import defaultdict
from functools import lru_cache
import lxml.etree as etree
import copy
from pathlib import Path
from dataclasses import asdict, dataclass, replace as dc_replace
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterable, List, Literal, Optional, Protocol, Set, Tuple, cast

if TYPE_CHECKING:
    from lawvm.core.phase_result import PhaseResult
    from lawvm.core.elaboration_context import TargetContext
    from lawvm.core.provenance import MigrationEvent
    from lawvm.core.compile_result import ActivationRule

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
)
from lawvm.core.ir import LegalOperation as _LegalOperation
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.core.compile_result import (
    ActivationRule,
    SourcePathology,
    StrictProfile,
    TemporalEvent,
    TemporalScope,
)
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding
from lawvm.core.replay_lints import build_text_duplication_findings


from lawvm.core import tree_ops as _tops
from lawvm.core.tree_ops import check_invariants as _check_tree_invariants
from lawvm.core.tree_ops import iter_tree_invariant_violations as _iter_tree_invariant_violations
from lawvm.core.tree_ops import normalized_label_key
from lawvm.core.elaboration_context import (
    ReplayLookups,
    build_payload_elaboration_context,
    snapshot_replay_lookups,
    snapshot_target_context,
)
from lawvm.finland.source_normalize import normalize_source_ir
from lawvm.finland.ops import (  # noqa: E402, F401  (moved here; re-exported for backward compat)
    OpType,
    TargetKind,
    AmendmentOp,
    ResolvedOp,
    FailedOp,
    ReplayProfile,
    ScopeConfidence,
    get_replay_profile,
    LawLevelTextPatch,
    _apply_law_level_text_patches,
    _PATH_KINDS,
    _lo_target_fields,
    _lo_path_dict,
    _lo_with_path_update,
    _build_canonical_intent,
    scope_confidence_from_tags,
    normalize_scope_confidence,
    projection_scope_confidence,
)
from lawvm.finland.normalize import (  # noqa: E402, F401  (moved here; re-exported for backward compat)
    _extract_grouped_container_targets,
    _sec1_fallback_peg_skip_required,
    _extract_insert_section_ops_fallback,
    _extract_insert_subsection_ops_fallback,
    _extract_insert_container_ops_fallback,
    _extract_root_insert_ops_fallback,
    _extract_root_replace_ops_from_body_fallback,
    _dedupe_fallback_ops_ir,
    _extract_replace_ops_from_muutetaan_tail,
    _expand_numeric_section_list_ir,
    parse_ops_fallback_heuristic,
    parse_ops_title_fallback,
)
from lawvm.finland.johtolause import (
    extract_legal_ops as extract_johtolause_legal_ops,  # noqa: F401  (re-exported; tests import from grafter)
    extract_law_level_text_patch_los as _extract_law_level_patch_los,
    parse_clause as _parse_johtolause_clause,
)
from lawvm.finland.constraints import DEBUG
from lawvm.corpus_store import CorpusStore
from lawvm.finland.replay_notices import (
    replay_print as _replay_print,
    reset_replay_verbose as _reset_replay_verbose,
    set_replay_verbose as _set_replay_verbose,
)
from lawvm.finland.xml_ir import fi_xml_to_ir_node
from lawvm.finland.source_pathology import build_empty_operative_body_pathology
from lawvm.core.payload_surface import (
    GroupSurface,
    PayloadSurface,
    build_group_surface as _build_group_surface_factory,
    build_payload_surface as _build_payload_surface,
)
from lawvm.finland.helpers import may_attach_post_list_loppukappale
from lawvm.finland.elaborated_group import (
    ElaboratedGroup,
    build_elaborated_group as _build_elaborated_group_factory,
)


@dataclass(frozen=True)
class RepealTargetRef:
    """Typed repeal-target carrier for future-repeal suppression bookkeeping."""

    target_unit_kind: TargetUnitKind
    target_norm: str
    target_chapter: Optional[str] = None

    @classmethod
    def section(cls, target_norm: str, target_chapter: Optional[str] = None) -> "RepealTargetRef":
        return cls("section", target_norm, target_chapter)

    @classmethod
    def chapter(cls, target_norm: str) -> "RepealTargetRef":
        return cls("chapter", target_norm, None)

    @classmethod
    def part(cls, target_norm: str) -> "RepealTargetRef":
        return cls("part", target_norm, None)

logger = logging.getLogger(__name__)

AMENDMENT_PARENTS_CSV = Path(".cache/finland/amendment_parents.csv")  # internal cache, auto-built


def _hoist_trailing_wrapup_ir(node: IRNode) -> IRNode:
    """Promote trailing prose after numbered items to a wrapUp node."""
    node_kind = getattr(node.kind, "value", str(node.kind))
    if not node.children:
        return node

    new_children = [_hoist_trailing_wrapup_ir(child) for child in node.children]

    def _child_kind(child: IRNode) -> str:
        return getattr(child.kind, "value", str(child.kind))

    def _paragraph_has_num_ir(child: IRNode) -> bool:
        return any(_child_kind(grandchild) == "num" for grandchild in child.children)

    def _paragraph_is_content_only_ir(child: IRNode) -> bool:
        return _child_kind(child) == "paragraph" and not _paragraph_has_num_ir(child) and all(
            _child_kind(grandchild) == "content" for grandchild in child.children
        )

    if node_kind == "subsection":
        numbered_positions = [
            idx
            for idx, child in enumerate(new_children)
            if _child_kind(child) == "paragraph" and _paragraph_has_num_ir(child)
        ]
        if numbered_positions:
            last_numbered_idx = numbered_positions[-1]
            trailing = new_children[last_numbered_idx + 1 :]
            if trailing and may_attach_post_list_loppukappale(
                IRNode(kind=IRNodeKind.SUBSECTION, label=node.label, text=node.text, attrs=node.attrs, children=tuple(new_children))
            ) and all(
                _paragraph_is_content_only_ir(child) or _child_kind(child) == "content"
                for child in trailing
            ):
                rewritten: list[IRNode] = list(new_children[: last_numbered_idx + 1])
                for child in trailing:
                    wrap_text = irnode_to_text(child).strip()
                    if not wrap_text:
                        continue
                    rewritten.append(IRNode(kind=IRNodeKind.WRAP_UP, text=wrap_text, attrs=dict(child.attrs)))
                return _tops._with_children(node, rewritten)

    if tuple(new_children) == node.children:
        return node
    return _tops._with_children(node, new_children)


def _replay_product_invariant_finding(
    *,
    violation: str,
    source_statute: str,
    message: str = "Replay/materialization product invariant violated.",
) -> Finding:
    """Build Finland replay-product invariant findings before compatibility projection."""
    return Finding(
        kind="APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": message,
            "violation": violation,
            "barrier_code": "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION",
        },
    )


def _apply_mutation_boundary_violation_finding(
    *,
    violation: str,
    source_statute: str,
) -> Finding:
    """Build the replay finding emitted for apply mutation accounting violations."""
    barrier_code = violation.split(" ", 1)[0]
    return Finding(
        kind=barrier_code,
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": "Apply mutation boundary accounting violated.",
            "violation": violation,
            "barrier_code": barrier_code,
        },
    )


def _apply_mutation_invariant_report_finding(
    *,
    report: "ApplyMutationInvariantReport",
    result: "ApplyMutationAccountingResult",
    source_statute: str,
) -> Finding | None:
    """Project registered mutation-accounting results as native replay findings."""
    spec = get_finding_spec(result.code)
    if spec is None:
        return None

    finding = _apply_mutation_boundary_violation_finding(
        violation=result.as_violation_string(),
        source_statute=source_statute,
    )
    detail = {
        **dict(finding.detail),
        "op_id": report.op_id,
        "helper": report.helper,
        "outcome": report.outcome,
        "touched_paths": [list(path) for path in report.touched_paths],
        "changed_paths": [list(path) for path in report.changed_paths],
        "allowed_effect_region_paths": [list(path) for path in report.allowed_effect_region_paths],
        "declared_recovery_paths": [list(path) for path in report.declared_recovery_paths],
        "declared_recovery_rule_ids": list(report.declared_recovery_rule_ids),
        "declared_migration_paths": [list(path) for path in report.declared_migration_paths],
        "declared_migration_rule_ids": list(report.declared_migration_rule_ids),
        "permitted_paths": [list(path) for path in report.permitted_paths],
        "covered_changed_paths": [list(path) for path in report.covered_changed_paths],
        "unexplained_changed_paths": [list(path) for path in report.unexplained_changed_paths],
        "allowed_non_target_paths": [list(path) for path in report.allowed_non_target_paths],
        "out_of_scope_paths": [list(path) for path in result.out_of_scope_paths],
        "matched_allowance_rule_ids": list(result.matched_allowance_rule_ids),
        "path_set_invariant_holds": report.path_set_invariant_holds,
    }
    return Finding(
        kind=finding.kind,
        role=finding.role,
        stage=finding.stage,
        blocking=finding.blocking,
        source_statute=finding.source_statute,
        detail=detail,
    )


def _apply_mutation_fallback_event_finding(
    *,
    event: "ApplyMutationEvent",
    fallback_kind: str,
) -> Finding | None:
    """Project governed apply fallback tags as native replay findings."""
    spec = get_finding_spec(fallback_kind)
    if spec is None:
        return None

    fallback_tags = tuple(str(tag).strip() for tag in event.used_fallback_tags if str(tag).strip())
    if fallback_kind not in fallback_tags:
        return None
    reason_tag = next((tag for tag in fallback_tags if tag != fallback_kind), "")
    reason_code = str(event.reason_code or "").strip() or reason_tag
    resolved_target_path = [list(path) for path in event.resolved_target_path] if event.resolved_target_path else []
    message = "Apply used a governed fallback path."
    if fallback_kind == "APPLY.LEGACY_DISPATCH_FALLBACK":
        message = "Apply fell back to legacy field-based dispatch."
    elif fallback_kind == "APPLY.RELABEL_SKIPPED":
        message = "Typed relabel intent was skipped for a governed reason."
    elif fallback_kind == "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK":
        message = "Section path resolution fell back to a live unique match after scoped lookup failed."
    detail = {
        "message": message,
        "helper": event.helper,
        "reason_tag": reason_tag,
        "reason_code": reason_code,
        "used_fallback_tags": list(fallback_tags),
        "failure_reason": str(event.failure_reason or ""),
        "resolved_target_path": resolved_target_path,
        "op_id": event.op_id,
        "source_statute": event.source_statute,
    }
    if spec.role == "observation":
        return Finding(
            kind=fallback_kind,
            role="observation",
            stage=spec.phase,
            detail=detail,
            source_statute=event.source_statute,
            blocking=False,
        )
    if spec.role == "barrier":
        return Finding(
            kind="RUNTIME.VIOLATION",
            role="violation",
            stage=spec.phase,
            detail={**detail, "barrier_code": fallback_kind},
            source_statute=event.source_statute,
            blocking=True,
        )
    return Finding(
        kind=fallback_kind,
        role="obligation",
        stage=spec.phase,
        detail=detail,
        source_statute=event.source_statute,
        blocking=spec.default_enforcement in ("strict_fail", "hard_fail"),
    )


def _serialize_apply_mutation_event(event: "ApplyMutationEvent") -> dict[str, object]:
    payload = asdict(event)
    if not payload.get("declared_allowances"):
        payload.pop("declared_allowances", None)
    return payload


def _serialize_apply_mutation_invariant_report(
    report: "ApplyMutationInvariantReport",
) -> dict[str, object]:
    return asdict(report)


def _structural_dedup_applied_finding(
    *,
    phase: str,
    source_statute: str,
    duplicates: Optional[list[dict[str, str]]] = None,
) -> Finding:
    """Build the observation emitted when the global dedup backstop modifies a tree."""
    return Finding(
        kind="APPLY.GLOBAL_LABEL_DEDUP_APPLIED",
        role="observation",
        stage="apply",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Global same-kind+label dedup backstop modified the replay tree.",
            "phase": phase,
            "duplicates": list(duplicates or ()),
        },
    )


def _pre_dedup_duplicate_details(tree: IRNode) -> list[dict[str, str]]:
    """Extract duplicate-label details from a tree before the dedup backstop runs."""
    details: list[dict[str, str]] = []
    duplicate_re = re.compile(r"duplicate\s+(\w+):(\S+)", re.IGNORECASE)
    for violation in _check_tree_invariants(tree):
        last_slash = violation.rfind("/")
        search_from = last_slash + 1 if last_slash != -1 else 0
        sep = violation.find(": ", search_from)
        if sep == -1:
            continue
        path = violation[:sep].strip()
        message = violation[sep + 2 :].strip()
        match = duplicate_re.search(message)
        if match is None:
            continue
        details.append(
            {
                "path": path,
                "kind": match.group(1),
                "label": match.group(2),
            }
        )
    return details


def _strict_rejected_source_pathology_finding(
    pathology: SourcePathology,
    *,
    stage: str,
    fallback_source_statute: str = "",
) -> Finding:
    """Build the blocking finding for strict-profile source pathology rejection."""
    return Finding(
        kind="APPLY.SOURCE_PATHOLOGY_DETECTED",
        role="obligation",
        stage=stage,
        blocking=True,
        source_statute=pathology.source_statute or fallback_source_statute,
        detail={
            **pathology.scope_detail(),
            "code": pathology.code,
            "detail": dict(pathology.detail),
            "message": f"Strict profile rejected a suspicious non-literal source path: {pathology.code}",
        },
    )


def _base_observation_to_finding(obs_dict: Dict[str, object]) -> Optional[Finding]:
    """Convert a base observation dict to a Finding object.

    Base observations from T1b (BASE_UNNUMBERED_PARAGRAPH_PEER, LABEL_EID_DIVERGENCE)
    are collected during statute parsing and added to elaboration_observations.
    This converts them to Finding objects for the findings ledger.
    """
    obs_kind = str(obs_dict.get("kind", "")).strip()
    source_statute = str(obs_dict.get("source_statute", "")).strip()
    raw_detail = obs_dict.get("detail")
    detail_dict: dict[str, Any] = {}
    if isinstance(raw_detail, dict):
        for k, v in raw_detail.items():
            detail_dict[str(k)] = v
    stage = str(obs_dict.get("stage", "base_source_analysis")).strip()

    if not obs_kind:
        return None

    # Use registry to get the role and enforcement for this observation kind
    spec = get_finding_spec(obs_kind)
    if spec is None:
        return None

    # All base observations are registered as role="observation"
    return Finding(
        kind=obs_kind,
        role="observation",
        stage=stage,
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": f"Base statute observation: {obs_kind}",
            **detail_dict,
        },
    )


def _emit_structural_dedup_warning(
    *,
    phase: str,
    before_ir: IRNode,
    after_ir: IRNode,
    source_statute: str,
    replay_findings: list[Finding],
    replay_meta_out: Optional[Dict[str, object]],
) -> IRNode:
    """Surface a warning whenever the global dedup backstop modifies the tree."""
    if after_ir is before_ir:
        return after_ir

    duplicate_details = _pre_dedup_duplicate_details(before_ir)
    _replay_print(
        f"WARNING structural dedup: {phase} same-kind+label duplicates were removed"
    )
    if replay_meta_out is not None:
        dedup_warnings = replay_meta_out.setdefault("structural_dedup_warnings", [])
        warning_payload: dict[str, object] = {
            "phase": phase,
            "message": "Global same-kind+label dedup backstop modified the replay tree.",
        }
        if duplicate_details:
            warning_payload["duplicates"] = duplicate_details
        cast(list[dict[str, object]], dedup_warnings).append(warning_payload)
    replay_findings.append(
        _structural_dedup_applied_finding(
            phase=phase,
            source_statute=source_statute,
            duplicates=duplicate_details,
        )
    )
    return after_ir


# ---------------------------------------------------------------------------
# Uncovered body recovery cluster (moved to grafter_uncovered.py; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.grafter_uncovered import (  # noqa: E402, F401
    _recover_uncovered_body_ops,
    _apply_uncovered_kumotaan,
    _pre_scan_repeal_targets,
    _uncovered_body_recovery_finding,
    _strict_rejected_uncovered_body_finding,
)

from lawvm.finland.citation_routing import (  # noqa: E402
    OP_KEYWORDS,
    _johtolause_references_parent,  # noqa: F401  (re-exported)
    _title_explicitly_targets_other_statute,
    route_amendment,  # noqa: F401  (re-exported)
)
from lawvm.finland.acquisition import (  # noqa: E402
    build_amendment_acquisition_result,
    should_use_sec1_fallback_pre_routing as _should_use_sec1_fallback_pre_routing_impl,
    should_use_sec1_fallback_post_routing as _should_use_sec1_fallback_post_routing_impl,
)


# ---------------------------------------------------------------------------
# Johtolause supplement/tagging (moved to johtolause_supplements.py; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.johtolause_supplements import (  # noqa: E402, F401
    _tag_explicit_item_shift_after_repeal_hints,
    _supplement_missing_repeals_after_item_shift_clause,
    _supplement_named_table_row_mixed_clause_ops,
    _tag_named_table_row_single_clause_ops,
)

# ---------------------------------------------------------------------------
# Body pairing (assign_body_units re-exported for tests that import from grafter)
# ---------------------------------------------------------------------------
from lawvm.finland.body_pairing import (  # noqa: E402
    assign_body_units,  # noqa: F401  (re-exported for tests that import from grafter)
    build_observed_body_inventory as _build_observed_body_inventory,
)
from lawvm.finland.restructure_plan import (  # noqa: E402
    build_restructure_plan,
    deferred_plan_op_finding,
    execute_restructure_plan,
    move_skip_finding,
    relabel_skip_finding,
    relabel_skip_source_pathology_finding,
    StructuralTransformPlan,
    TransformOpKind,
    _parse_address,
)

# ---------------------------------------------------------------------------
# Corpus store + oracle access (moved to lawvm.finland.corpus; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.corpus import (  # noqa: E402, F401
    _get_corpus_store,
    get_corpus,
    _latest_consolidated_path_by_statute,
    get_oracle_path,
    _consolidated_oracle_version_amendment_id,
    get_consolidated_meta,
    get_consolidated_oracle_context,
    get_consolidated_oracle_suspect,
    get_consolidated_oracle_reflected_source_vts_children,
    _oracle_mode_sort_key,
    _oracle_version_label,
    get_ground_truth,
    get_ground_truth_tree,
)
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.consolidated_store import (  # noqa: E402
    select_cached_consolidated_artifact_with_info as _select_artifact_with_info,
)


@lru_cache(maxsize=1)
def _amendment_children_by_parent() -> Dict[str, List[str]]:
    from lawvm.finland.amendment_index import get_amendment_children

    return get_amendment_children()


def _source_normalization_fact_finding_kind(kind_value: str) -> str | None:
    raw = str(kind_value or "").strip()
    if not raw:
        return None
    candidate = raw.upper() if raw.startswith("base_") else f"BASE_{raw.upper()}"
    spec = get_finding_spec(candidate)
    if spec is None or not candidate.startswith("BASE_"):
        return None
    return candidate


# ---------------------------------------------------------------------------
# Pure helpers (moved to helpers.py; re-exported here for backward compat)
# ---------------------------------------------------------------------------
from lawvm.finland.helpers import (  # noqa: E402, F401
    _norm_num_token,
    _roman_label_to_arabic,
    _section_sort_key,
    _is_omission_ir,
    _previous_item_token,
    _parse_iso_date,
    _expand_section_range,
    _fi_label_postprocessor,  # moved to helpers; re-exported here for backward compat
    _norm_row_anchor_text,
)

# ---------------------------------------------------------------------------
# lxml helpers
# ---------------------------------------------------------------------------


def _tag(el: etree._Element) -> str:
    return str(el.tag).split("}")[-1]


def _find_muutos_ir(
    muutos_tree: etree._Element,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str] = None,
    target_part: Optional[str] = None,
) -> Tuple[Optional[IRNode], Optional[IRNode]]:
    """Find amendment section and preceding cross-heading as IRNodes.

    Returns (muutos_ir, cross_ir). Encapsulates all lxml→IRNode conversion
    for amendment section lookup.
    """
    muutos_sec = _find_muutos_node(
        muutos_tree,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
    )
    if muutos_sec is None:
        return None, None

    def _subsection_intro_numeric_label_ir(sub_ir: IRNode) -> Optional[str]:
        for child in sub_ir.children:
            if child.kind not in {IRNodeKind.INTRO, IRNodeKind.CONTENT}:
                continue
            text = (child.text or "").strip()
            m = re.match(r"^(\d+)\.\s", text)
            if m is not None and int(m.group(1)) > 1:
                return m.group(1)
        return None

    def _relabel_sparse_omission_subsections_from_intro_ir(node: IRNode) -> IRNode:
        if not node.children:
            return node

        changed = False
        new_children: List[IRNode] = []
        for child in node.children:
            if child.children:
                relabelled = _relabel_sparse_omission_subsections_from_intro_ir(child)
                if relabelled is not child:
                    changed = True
                new_children.append(relabelled)
            else:
                new_children.append(child)

        if node.kind is IRNodeKind.SECTION:
            seen_prior_omission = False
            seen_labels = {child.label for child in new_children if child.kind is IRNodeKind.SUBSECTION and child.label}
            adjusted_children: List[IRNode] = []
            for child in new_children:
                if _is_omission_ir(child):
                    seen_prior_omission = True
                    adjusted_children.append(child)
                    continue
                if seen_prior_omission and child.kind is IRNodeKind.SUBSECTION and (child.label or "").isdigit():
                    intro_label = _subsection_intro_numeric_label_ir(child)
                    if intro_label is not None and intro_label != child.label and intro_label not in seen_labels:
                        seen_labels.discard(child.label)
                        child = _relabel_subsection_ir(child, intro_label)
                        seen_labels.add(intro_label)
                        changed = True
                adjusted_children.append(child)
            new_children = adjusted_children

        if not changed:
            return node
        return _tops._with_children(node, new_children)

    def _subsection_with_flat_text_ir(sub_ir: IRNode, flat_text: str) -> IRNode:
        content_child = IRNode(kind=IRNodeKind.CONTENT, text=flat_text.strip())
        return IRNode(
            kind=sub_ir.kind,
            label=sub_ir.label,
            text=sub_ir.text,
            attrs=dict(sub_ir.attrs),
            children=(content_child,),
        )

    def _embedded_letter_suffix_section_ir(sec_el: etree._Element) -> Optional[IRNode]:
        if target_unit_kind != "section":
            return None
        num_el = sec_el.find("{*}num")
        base_norm = _norm_num_token(num_el.text if num_el is not None and num_el.text else "")
        if not base_norm or not base_norm.isdigit():
            return None

        subsections = sec_el.findall("./{*}subsection")
        if len(subsections) < 2:
            return None

        first_text = " ".join("".join(str(_t) for _t in subsections[0].itertext()).split())
        m = re.search(rf"\b{re.escape(base_norm)}\s*([a-z])\s*§\s*$", first_text, flags=re.I)
        if not m:
            return None

        suffix = m.group(1).lower()
        embedded_label = f"{base_norm}{suffix}"
        if target_norm not in {base_norm, embedded_label}:
            return None

        if target_norm == base_norm:
            # lxml elements are mutable and may be re-parented by the parser,
            # so clone the source subsection before handing it to the IR
            # converter.
            first_sub_ir = fi_xml_to_ir_node(copy.deepcopy(subsections[0]), _fi_label_postprocessor)
            trimmed_text = re.sub(
                rf"\s*{re.escape(base_norm)}\s*{re.escape(suffix)}\s*§\s*$",
                "",
                " ".join(irnode_to_text(first_sub_ir).split()),
                flags=re.I,
            ).strip()
            clean_first = _subsection_with_flat_text_ir(first_sub_ir, trimmed_text)
            num_text = (num_el.text or "").strip() if num_el is not None and num_el.text else f"{base_norm} §"
            return IRNode(
                kind=IRNodeKind.SECTION,
                label=base_norm,
                children=(IRNode(kind=IRNodeKind.NUM, text=num_text), clean_first),
            )

        embedded_subs = [
            # Same XML detachment boundary as above: each subsection is cloned
            # before conversion so the original subtree stays untouched.
            fi_xml_to_ir_node(copy.deepcopy(sub), _fi_label_postprocessor)
            for sub in subsections[1:]
        ]
        if not embedded_subs:
            return None
        return IRNode(
            kind=IRNodeKind.SECTION,
            label=embedded_label,
            children=(IRNode(kind=IRNodeKind.NUM, text=f"{base_norm} {suffix} §"), *embedded_subs),
        )

    muutos_ir = _embedded_letter_suffix_section_ir(muutos_sec)
    if muutos_ir is None:
        muutos_ir = fi_xml_to_ir_node(muutos_sec, _fi_label_postprocessor)
        muutos_ir = _relabel_sparse_omission_subsections_from_intro_ir(muutos_ir)
        # If this chapter is wrapped in a <part> element in the amendment body,
        # record the part label as a routing hint for multi-part statutes.
        # This fixes the case where letter-suffix chapters (e.g. 17a) are
        # explicitly placed in a different part than their numeric predecessor.
        if muutos_ir is not None and muutos_ir.kind is IRNodeKind.CHAPTER:
            _sec_parent = muutos_sec.getparent() if hasattr(muutos_sec, "getparent") else None
            if _sec_parent is not None and _tag(_sec_parent) == "part":
                _part_num_el = _sec_parent.find("{*}num")
                if _part_num_el is not None and _part_num_el.text:
                    _pnorm = _norm_num_token(_part_num_el.text.strip())
                    _pnorm = _pnorm.removesuffix("osasto").removesuffix("osa")
                    _phint_arabic = _roman_label_to_arabic(_pnorm)
                    # Use the Arabic string if conversion succeeded; otherwise
                    # keep the raw normalised form (e.g. "iva" for "IV A OSA").
                    _phint = str(_phint_arabic) if _phint_arabic is not None else (_pnorm or None)
                    if _phint:
                        # Collect sibling chapter labels under the same part element
                        # so apply_structure_ops can move them when creating the new part.
                        _sibling_labels: list[str] = []
                        for _sib_ch in _sec_parent.findall("{*}chapter"):
                            _sib_num_el = _sib_ch.find("{*}num")
                            if _sib_num_el is not None and _sib_num_el.text:
                                _sib_norm = _norm_num_token(_sib_num_el.text.strip()).removesuffix("luku")
                                if _sib_norm and _sib_norm != muutos_ir.label:
                                    _sibling_labels.append(_sib_norm)
                        _extra_attrs: dict[str, object] = {"lawvm_amendment_part_hint": _phint}
                        if _sibling_labels:
                            _extra_attrs["lawvm_amendment_part_sibling_chapters"] = tuple(_sibling_labels)
                        muutos_ir = IRNode(
                            kind=muutos_ir.kind,
                            label=muutos_ir.label,
                            text=muutos_ir.text,
                            attrs={**dict(muutos_ir.attrs), **_extra_attrs},
                            children=muutos_ir.children,
                        )
        m_suffix = re.fullmatch(r"(\d+)([a-z])", target_norm, flags=re.I)
        if m_suffix is not None and muutos_ir.kind is IRNodeKind.SECTION and muutos_ir.label == m_suffix.group(1):
            # Older malformed source sometimes encodes a newly inserted letter-suffix
            # section (e.g. `39 a §`) as a bare base section node (`39 §`) even though
            # the operative target is explicitly `39a`. Preserve the requested target
            # label so apply-time logic treats it as a section insert, not as a
            # fallback reinterpretation into `39 § 1 mom a kohta`.
            suffix = m_suffix.group(2).lower()
            num_text = next(
                (c.text for c in muutos_ir.children if c.kind is IRNodeKind.NUM and c.text),
                f"{m_suffix.group(1)} {suffix} §",
            )
            num_text = re.sub(
                rf"^{re.escape(m_suffix.group(1))}\s*§",
                f"{m_suffix.group(1)} {suffix} §",
                num_text,
                flags=re.I,
            )
            muutos_ir = IRNode(
                kind=IRNodeKind.SECTION,
                label=target_norm.lower(),
                text=muutos_ir.text,
                attrs=dict(muutos_ir.attrs),
                children=(
                    IRNode(kind=IRNodeKind.NUM, text=num_text),
                    *tuple(c for c in muutos_ir.children if c.kind is not IRNodeKind.NUM),
                ),
            )
    prev = muutos_sec.getprevious()
    cross_ir = (
        # crossHeading is also an lxml subtree boundary; clone before IR
        # conversion so the source document remains structurally intact.
        fi_xml_to_ir_node(copy.deepcopy(prev), _fi_label_postprocessor)
        if prev is not None and _tag(prev) == "crossHeading"
        else None
    )
    return muutos_ir, cross_ir


def _normalize_item_like_target(
    master_or_ctx,
    op: AmendmentOp,
    muutos_ir: Optional[IRNode],
) -> AmendmentOp:
    """Backward-compat wrapper for payload_normalize._normalize_item_like_target."""
    return _normalize_item_like_target_impl(master_or_ctx, op, muutos_ir)


# ---------------------------------------------------------------------------
# IRNode merge / omission-resolution functions (moved to finland/merge.py)
# ---------------------------------------------------------------------------
from lawvm.finland.merge import (  # noqa: E402, F401  (moved here; re-exported for backward compat)
    _has_section_omissions_ir,
    _merge_subsection_with_omission_ir,
    _merge_subsection_accumulate_inner_omission_ir,
    _merge_section_inner_subsection_omission_ir,
    _merge_section_with_omission_ir,
    _merge_same_numbered_container_insert_ir,
    _paragraph_signatures_ir,
    _single_subsection_paragraph_map_ir,
    _item_label_from_intro_like_ir,
    _sparse_section_item_update_map_ir,
    _sparse_item_section_replace_merge_ir,
    _paragraph_to_subparagraph_ir,
    _merge_sparse_alakohta_insert_ir,
    _merge_sparse_alakohta_replace_ir,
    _merge_letter_item_into_content_only_subsection_ir,
    _merge_letter_item_from_content_subsection_ir,
    _is_suspicious_partial_section_replace_ir,
    _drop_suspicious_partial_whole_section_replaces,
    _pre_resolve_omissions,
)


from lawvm.finland.frontend_compile import (  # noqa: E402, F401  (moved here; re-exported for backward compat)
    _tree_title,
    _enrich_ops_from_amendment_tree,
    normalize_and_compile_ops,
)


_OPERATIVE_BODY_TAGS = {
    "section",
    "chapter",
    "part",
    "article",
    "subsection",
    "paragraph",
    "point",
    "subparagraph",
    "table",
    "blocklist",
    "item",
}


def _localname(node: etree._Element) -> str:
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def _amendment_operative_structure_tags(tree: etree._Element) -> list[str]:
    body = tree.find(".//{*}body")
    root = body if body is not None else tree
    found: list[str] = []
    seen: set[str] = set()
    for node in root.iter():
        tag = _localname(node)
        if tag in _OPERATIVE_BODY_TAGS and tag not in seen:
            seen.add(tag)
            found.append(tag)
    return found


def _amendment_lacks_operative_structure(tree: etree._Element) -> tuple[bool, list[str]]:
    tags = _amendment_operative_structure_tags(tree)
    return (len(tags) == 0, tags)


def _target_group_key(op: AmendmentOp) -> Tuple[IRNodeKind, str, Optional[str], Optional[str]]:
    """Backward-compat wrapper for group_plan.target_group_key."""
    return _target_group_key_impl(op)


def _rewrite_lo_op_source_expiry(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    section_labels: Optional[Set[str]],
    expiry_date: dt.date,
    parent_statute_id: Optional[str] = None,
    replay_mode: str = "legal_pit",
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Update expires on lo_ops whose source matches ``target_source_statute``.

    When ``target_source_statute`` is the same as ``parent_statute_id`` (i.e. the
    override targets the master statute directly, not a specific amendment), the
    function falls back to extending ALL ops that already carry a finite expiry
    date earlier than the new one.  This handles the common Finnish pattern where
    an amendment amends only the *voimaantulosäännös* of the parent statute to
    extend all temporary sections' validity.

    In ``finlex_oracle`` mode the parent-statute fallback clears the expires field
    entirely (rather than updating it to the new date) so that oracle materialization
    stays anchored at the consolidation cutoff instead of reviving future text.
    """
    if lo_ops_out is None:
        return False
    expiry_iso = expiry_date.isoformat()
    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if _expiry_date_precedes_effective_date(expiry_date, src.effective):
            continue
        target_path = list(lo.target.path)
        sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
        if section_labels is not None and sec_label.lower() not in section_labels:
            continue
        # Chapter-scoped guard: when chapter_section_map is provided, only expire
        # ops whose (chapter, section) pair is covered by the map.  This prevents
        # cross-chapter contamination when the same section number is fully repealed
        # in one chapter but only partially repealed in another.
        if chapter_section_map is not None:
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, source=dc_replace(src, expires=expiry_iso))
        updated = True
    if updated:
        return True
    # Fallback: when the override targets the parent statute (voimaantulosäännös
    # amending the whole regulation), extend every op that has a finite expiry
    # earlier than the new one.  This covers the case where all lo_ops carry
    # amendment IDs (not the parent statute ID) as their source.
    if parent_statute_id is not None and target_source_statute == parent_statute_id:
        # In finlex_oracle mode clear the expires field so sections with extended
        # validity appear at the 9999-12-31 materialization horizon.  In legal_pit
        # mode keep the real expiry date so point-in-time queries remain accurate.
        new_expires = "" if replay_mode == "finlex_oracle" else expiry_iso
        for i, lo in enumerate(lo_ops_out):
            src = lo.source
            if src is None or not src.expires:
                continue
            if _expiry_date_precedes_effective_date(expiry_date, src.effective):
                continue
            if replay_mode != "finlex_oracle" and src.expires >= expiry_iso:
                continue
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            if section_labels is not None and sec_label.lower() not in section_labels:
                continue
            lo_ops_out[i] = dc_replace(lo, source=dc_replace(src, expires=new_expires))
            updated = True
    return updated


def _rewrite_lo_op_source_effective(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
    base_ir: Optional[IRNode] = None,
) -> bool:
    """Update effective on lo_ops whose source matches the scoped override."""
    if lo_ops_out is None:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if chapter_section_map is not None:
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        updated_lo = dc_replace(lo, source=dc_replace(src, effective=effective_iso))
        if (
            updated_lo.action is StructuralAction.REPLACE
            and updated_lo.target.path
            and (base_ir is None or _tops.resolve(base_ir, updated_lo.target.path) is None)
            and _timeline_target_exists(
                updated_lo.target.path,
                replay_history_ops=lo_ops_out[:i],
                base_ir=base_ir,
                before_effective=effective_iso,
            )
        ):
            updated_lo = dc_replace(updated_lo, action=StructuralAction.INSERT)
        lo_ops_out[i] = updated_lo
        updated = True
    return updated


def _rewrite_lo_op_group_id(
    lo_ops_out: Optional[List[_LegalOperation]],
    target_source_statute: str,
    new_group_id: str,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> tuple[LegalAddress, ...]:
    """Retarget matching lo_ops to a scoped temporal group and return addresses."""
    if lo_ops_out is None:
        return ()
    touched: list[LegalAddress] = []
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        if chapter_section_map is not None:
            target_path = list(lo.target.path)
            sec_label = next((v for k, v in reversed(target_path) if k == "section"), "")
            chap_label = next((v for k, v in reversed(target_path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label.lower() not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, group_id=new_group_id)
        if lo.target not in touched:
            touched.append(lo.target)
    return tuple(touched)


def _rewrite_compiled_op_activation_rule_effective(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    target_source_statute: str,
    effective_date: dt.date,
    *,
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Update compiled activation rules for scoped commencement overrides."""
    if compiled_ops_out is None:
        return False
    effective_iso = effective_date.isoformat()
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        if chapter_section_map is not None:
            sec_label = str(op.get("target_norm") or "").lower()
            chap_label_raw = str(op.get("target_chapter") or "").strip().lower() or None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_raw, set()) if chap_label_raw else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _rewrite_later_effective_lo_groups(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    target_source_statute: str,
    amendment_effective_date: dt.date,
) -> dict[str, tuple[LegalAddress, ...]]:
    """Scope later-effective ops away from the amendment-wide temporal group.

    Finland cited-version-bound ops can legitimately carry a later executable
    effective date than the amendment's own commencement date. If those ops
    keep the canonical ``finland-johto:<amendment>`` group id, core temporal
    matching will still activate them at the amendment-wide date. Rewrite only
    the later-effective ops into per-date scoped groups so replay emits an
    explicit, auditable temporal carrier for the deferred subset.
    """
    if lo_ops_out is None:
        return {}

    amendment_effective_iso = amendment_effective_date.isoformat()
    canonical_group_id = f"finland-johto:{target_source_statute}"
    touched_by_effective: dict[str, list[LegalAddress]] = {}

    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if (
            src is None
            or src.statute_id != target_source_statute
            or not src.effective
            or src.effective <= amendment_effective_iso
            or lo.group_id != canonical_group_id
        ):
            continue
        scoped_group_id = f"{canonical_group_id}:effective:{src.effective}"
        lo_ops_out[i] = dc_replace(lo, group_id=scoped_group_id)
        touched_by_effective.setdefault(src.effective, [])
        if lo.target not in touched_by_effective[src.effective]:
            touched_by_effective[src.effective].append(lo.target)

    return {
        effective_iso: tuple(addresses)
        for effective_iso, addresses in touched_by_effective.items()
        if addresses
    }


def _rewrite_compiled_op_activation_rule_effective_for_addresses(
    compiled_ops_out: Optional[List[Dict[str, object]]],
    *,
    target_source_statute: str,
    effective_date: dt.date,
    exact_addresses: tuple[LegalAddress, ...],
) -> bool:
    """Update compiled activation rules for one exact-address effective override."""
    if compiled_ops_out is None or not exact_addresses:
        return False
    effective_iso = effective_date.isoformat()
    address_keys = {tuple(address.path) for address in exact_addresses}
    updated = False
    for op in compiled_ops_out:
        if op.get("source_statute") != target_source_statute:
            continue
        target_path: list[tuple[str, str]] = []
        target_part = str(op.get("target_part") or "").strip()
        target_chapter = str(op.get("target_chapter") or "").strip()
        target_norm = str(op.get("target_norm") or "").strip()
        target_unit_kind = str(op.get("target_unit_kind") or "").strip()
        if target_part:
            target_path.append(("part", target_part))
        if target_chapter:
            target_path.append(("chapter", target_chapter))
        if target_unit_kind and target_norm:
            target_path.append((target_unit_kind, target_norm))
        if tuple(target_path) not in address_keys:
            continue
        op["activation_rule"] = {
            "kind": "fixed_date",
            "effective_date": effective_iso,
            "condition_ref": "",
        }
        op["is_contingent"] = False
        updated = True
    return updated


def _event_scope_section_and_chapter(
    event: TemporalEvent,
) -> tuple[str, Optional[str]]:
    """Extract section/chapter labels from a TemporalEvent scope when present."""
    addresses = tuple(event.scope.exact_addresses or ()) or tuple(event.scope.address_prefixes or ())
    if not addresses:
        return "", None
    address = addresses[0]
    path = list(address.path)
    section = next((value for kind, value in reversed(path) if kind == "section"), "")
    chapter = next((value for kind, value in reversed(path) if kind == "chapter"), None)
    return section.lower(), (chapter.lower() if chapter else None)


def _rewrite_temporal_event_expiry(
    temporal_events: list[TemporalEvent],
    *,
    target_source_statute: str,
    section_labels: Optional[Set[str]],
    expiry_date: dt.date,
    parent_statute_id: Optional[str] = None,
    replay_mode: str = "legal_pit",
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Rewrite emitted TemporalEvents when Finland expiry overrides retarget time."""
    expiry_iso = expiry_date.isoformat()
    updated = False

    def _scope_matches(event: TemporalEvent) -> bool:
        section_label, chapter_label = _event_scope_section_and_chapter(event)
        if section_labels is not None and section_label not in section_labels:
            return False
        if chapter_section_map is None:
            return True
        global_secs = chapter_section_map.get(None, set())
        chapter_secs = chapter_section_map.get(chapter_label, set()) if chapter_label else set()
        return section_label in (global_secs | chapter_secs)

    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None or source.statute_id != target_source_statute:
            continue
        if not _scope_matches(event):
            continue
        temporal_events[i] = dc_replace(
            event,
            expires=expiry_iso,
            source=dc_replace(source, expires=expiry_iso),
        )
        updated = True
    if updated:
        return True

    if parent_statute_id is None or target_source_statute != parent_statute_id:
        return False

    new_expires = "" if replay_mode == "finlex_oracle" else expiry_iso
    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None:
            continue
        event_expires = event.expires or source.expires
        if not event_expires:
            continue
        if replay_mode != "finlex_oracle" and event_expires >= expiry_iso:
            continue
        if not _scope_matches(event):
            continue
        temporal_events[i] = dc_replace(
            event,
            expires=new_expires,
            source=dc_replace(source, expires=new_expires),
        )
        updated = True
    return updated


def _clear_temporal_event_expiry(
    temporal_events: list[TemporalEvent],
    *,
    target_source_statute: str,
    section_labels: Set[str],
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Clear expiry payload on matched TemporalEvents after permanent repeal rewrites."""
    updated = False
    for i, event in enumerate(temporal_events):
        source = event.source
        if source is None or source.statute_id != target_source_statute:
            continue
        section_label, chapter_label = _event_scope_section_and_chapter(event)
        if section_label not in section_labels:
            continue
        if chapter_section_map is not None:
            global_secs = chapter_section_map.get(None, set())
            chapter_secs = chapter_section_map.get(chapter_label, set()) if chapter_label else set()
            if section_label not in (global_secs | chapter_secs):
                continue
        if not event.expires and not source.expires:
            continue
        temporal_events[i] = dc_replace(
            event,
            expires="",
            source=dc_replace(source, expires=""),
        )
        updated = True
    return updated


def _normalize_frontend_temporal_events(
    temporal_events: tuple[TemporalEvent, ...],
    *,
    amendment_id: str,
    target_statute: str,
) -> tuple[TemporalEvent, ...]:
    """Normalize frontend-emitted temporal carriers onto Finland replay batch ids."""
    if not temporal_events:
        return ()
    normalized_events: list[TemporalEvent] = []
    canonical_group_id = f"finland-johto:{amendment_id}"
    for event in temporal_events:
        normalized_scope = event.scope
        if normalized_scope.target_statute != target_statute:
            normalized_scope = TemporalScope(
                target_statute=target_statute,
                exact_addresses=normalized_scope.exact_addresses,
                address_prefixes=normalized_scope.address_prefixes,
                predicates=normalized_scope.predicates,
                include_future_descendants=normalized_scope.include_future_descendants,
            )
        normalized_group_id = (
            canonical_group_id
            if event.group_id in {None, "", amendment_id}
            else event.group_id
        )
        if normalized_scope is event.scope and normalized_group_id == event.group_id:
            normalized_events.append(event)
            continue
        normalized_events.append(
            dc_replace(
                event,
                scope=normalized_scope,
                group_id=normalized_group_id,
            )
        )
    return tuple(normalized_events)


def _base_chapter_expiry_temporal_events(
    *,
    target_statute: str,
    chapter_expiries: Optional[Dict[str, str]],
) -> tuple[TemporalEvent, ...]:
    """Project base-statute chapter expiry facts into explicit TemporalEvents."""
    if not chapter_expiries:
        return ()
    events: list[TemporalEvent] = []
    for chapter_label, expiry_iso in sorted(chapter_expiries.items()):
        chapter_address = LegalAddress(path=(("chapter", chapter_label),))
        events.append(
            TemporalEvent(
                event_id=f"fi-base-chapter-expiry:{target_statute}:chapter:{chapter_label}",
                kind="expire",
                scope=TemporalScope(
                    target_statute=target_statute,
                    address_prefixes=(chapter_address,),
                ),
                expires=expiry_iso,
                source=OperationSource(
                    statute_id=target_statute,
                    expires=expiry_iso,
                ),
            )
        )
    return tuple(events)


def _rewrite_kumotaan_snapshot_replaces_to_repeal(
    lo_ops_out: Optional[List[_LegalOperation]],
    *,
    target_source_statute: str,
    section_labels: Set[str],
    chapter_section_map: Optional[Dict[Optional[str], Set[str]]] = None,
) -> bool:
    """Turn zero-day section snapshots from whole-section kumotaan clauses into repeals.

    Some Finland amendments rewrite a section snapshot and also state in the
    same johtolause that the whole section is repealed on the amendment's
    effective date. If we leave only the expiring snapshot ``REPLACE`` op in
    the replay product stream, timeline materialization can revive an older
    permanent background version after the zero-day expiry. The honest fix is
    to emit a permanent repeal/tombstone at the replay-products boundary.

    This rewrite is intentionally narrow:
    - only section-root ``snapshot_section_*`` ops are considered
    - the op must already be a zero-day snapshot (effective == expires)
    - the same amendment must not have any non-snapshot ops under that section,
      otherwise we may be looking at a partial repeal / renumber family rather
      than a whole-section repeal
    """
    if lo_ops_out is None or not section_labels:
        return False

    eligible_indices: list[int] = []
    blocked_labels: set[str] = set()

    def _section_label(lo: _LegalOperation) -> str:
        return next((v for k, v in reversed(lo.target.path) if k == "section"), "").lower()

    def _unique_chapter_for_section(section_label: str) -> Optional[str]:
        if chapter_section_map is None:
            return None
        owners = [
            chapter_label
            for chapter_label, sections in chapter_section_map.items()
            if chapter_label is not None and section_label in sections
        ]
        if len(owners) != 1:
            return None
        if section_label in chapter_section_map.get(None, set()):
            return None
        return owners[0]

    def _scoped_target(lo: _LegalOperation) -> LegalAddress:
        if chapter_section_map is None or any(kind == "chapter" for kind, _ in lo.target.path):
            return lo.target
        sec_label = _section_label(lo)
        if not sec_label:
            return lo.target
        chapter_label = _unique_chapter_for_section(sec_label)
        if chapter_label is None:
            return lo.target
        path = list(lo.target.path)
        insert_at = next((i for i, (kind, _label) in enumerate(path) if kind == "section"), len(path))
        return LegalAddress(path=tuple(path[:insert_at] + [("chapter", chapter_label)] + path[insert_at:]))

    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels:
            continue
        is_snapshot = lo.op_id.startswith("snapshot_")
        if is_snapshot and lo.target.path:
            # Derived child snapshots are part of the same whole-section
            # snapshot family and must not block conversion of the root
            # section snapshot into a durable repeal/tombstone.
            continue
        if not lo.op_id.startswith("snapshot_section_"):
            blocked_labels.add(sec_label)
            continue
        if not lo.target.path or lo.target.path[-1][0] != "section":
            blocked_labels.add(sec_label)
            continue

    updated = False
    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels or sec_label in blocked_labels:
            continue
        if not lo.op_id.startswith("snapshot_section_"):
            continue
        if lo.action not in (StructuralAction.REPLACE, StructuralAction.INSERT):
            continue
        # The snapshot is eligible for REPEAL conversion if it is a zero-day
        # snapshot (effective == expires) *or* if its expires was set by
        # _rewrite_lo_op_source_expiry for a kumotaan clause (section already
        # confirmed to be in section_labels above).  We accept any snapshot for
        # a section in the kumotaan set regardless of expiry status; the only
        # disallowed case is a non-snapshot op appearing under a snapshot_section_
        # op_id prefix — but those were blocked by the not-startswith guard above.
        #
        # Original guard (zero-day only) was:
        #   if not src.effective or src.effective != src.expires:
        #       if src.expires: continue
        # That guard rejected expiry-rewritten snapshots where the kumotaan
        # effective date differs from the amendment's issue/publication date,
        # causing the repealed section to survive in the oracle surface.
        # Chapter-scoped guard: when chapter_section_map is provided, only convert
        # ops whose (chapter, section) pair is covered by the map.
        if chapter_section_map is not None:
            scoped_target = _scoped_target(lo)
            chap_label = next((v for k, v in reversed(scoped_target.path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        eligible_indices.append(i)

    for i in eligible_indices:
        lo = lo_ops_out[i]
        assert lo.source is not None
        target = _scoped_target(lo)
        lo_ops_out[i] = dc_replace(
            lo,
            action=StructuralAction.REPEAL,
            target=target,
            payload=None,
            source=dc_replace(lo.source, expires=""),
        )
        updated = True

    for i, lo in enumerate(lo_ops_out):
        src = lo.source
        if src is None or src.statute_id != target_source_statute:
            continue
        sec_label = _section_label(lo)
        if not sec_label or sec_label not in section_labels:
            continue
        if lo.action is not StructuralAction.REPEAL:
            continue
        if not src.expires:
            continue
        scoped_target = _scoped_target(lo)
        if chapter_section_map is not None:
            chap_label = next((v for k, v in reversed(scoped_target.path) if k == "chapter"), None)
            chap_label_norm = chap_label.lower() if chap_label else None
            global_secs = chapter_section_map.get(None, set())
            chap_secs = chapter_section_map.get(chap_label_norm, set()) if chap_label_norm else set()
            if sec_label not in (global_secs | chap_secs):
                continue
        lo_ops_out[i] = dc_replace(lo, target=scoped_target, source=dc_replace(src, expires=""))
        updated = True

    return updated


def _inject_pure_kumotaan_repeal_ops(
    lo_ops_out: List[_LegalOperation],
    *,
    amendment_id: str,
    kumotaan_labels: List[str],
    chap_map_sets: Optional[Dict[Optional[str], Set[str]]],
    amendment_effective_date: "dt.date",
    state: "ReplayState",
) -> int:
    """Inject REPEAL lo_ops for pure-kumotaan sections that have no existing lo_ops.

    When an amendment repeals a section purely via the kumotaan clause (no body
    text for that section), the normal path emits no lo_ops for it.
    ``_rewrite_lo_op_source_expiry`` returns False (nothing to rewrite), so no
    REPEAL tombstone is ever injected.

    This function closes that gap: for each kumotaan section that has zero ops
    from ``amendment_id`` in ``lo_ops_out`` AT THE TARGETED (chapter, section)
    address, and whose address exists in the parent ``state`` IR, a permanent
    REPEAL lo_op is appended.

    The coverage check is chapter-aware when ``chap_map_sets`` is provided:
    an op for section "9" in chapter "10" does NOT cover a kumotaan for section
    "9" in chapter "5".

    Returns the number of ops injected.
    """
    if not kumotaan_labels:
        return 0

    # Build chapter-aware coverage: set of (chapter_lower_or_None, section_lower)
    # pairs for which this amendment already has a REPEAL op.
    #
    # We only count REPEAL ops as "coverage" for kumotaan injection: if the
    # amendment has a REPLACE/INSERT op for a section that is also in the
    # kumotaan clause (e.g. an amendment that both rewrites and declares a
    # repeal), the REPLACE/INSERT does NOT suppress injection of the kumotaan
    # REPEAL tombstone — the repeal wins.
    #
    # The REPEAL op may have been created earlier in the same pipeline step
    # by ``_rewrite_kumotaan_snapshot_replaces_to_repeal``; sections for which
    # that conversion succeeded are already REPEAL-covered here.
    covered_chap_secs: Set[Tuple[Optional[str], str]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        if lo.action is not StructuralAction.REPEAL:
            continue
        sec_label = next((v for k, v in reversed(lo.target.path) if k == "section"), "")
        if not sec_label:
            continue
        chap_label = next((v for k, v in reversed(lo.target.path) if k == "chapter"), None)
        covered_chap_secs.add((chap_label.lower() if chap_label else None, sec_label.lower()))

    effective_iso = amendment_effective_date.isoformat()
    repeal_src = OperationSource(
        statute_id=amendment_id,
        effective=effective_iso,
    )

    injected = 0
    for label in kumotaan_labels:
        label_lower = label.lower()

        # Determine chapter(s) for this label.
        if chap_map_sets is not None:
            # Chapter-scoped: find which chapters list this section.
            target_chapters: List[Optional[str]] = [
                chap
                for chap, secs in chap_map_sets.items()
                if chap is not None and label_lower in secs
            ]
            if not target_chapters:
                # Possibly listed under global (None) key only.
                if label_lower in chap_map_sets.get(None, set()):
                    target_chapters = [None]
                else:
                    target_chapters = [None]
        else:
            target_chapters = [None]

        for chap in target_chapters:
            # Chapter-aware coverage check: skip only if there's already an op
            # from this amendment targeting THIS (chapter, section) address.
            chap_key = chap.lower() if chap is not None else None
            if (chap_key, label_lower) in covered_chap_secs:
                continue

            # Constraint: section must exist in the parent state.
            sec_path = state.find_section_path(label, chap)
            if sec_path is None:
                continue

            if chap is not None:
                target_path: Tuple[Tuple[str, str], ...] = (
                    ("chapter", chap),
                    ("section", label),
                )
            else:
                target_path = (("section", label),)

            op_id = (
                f"pure_repeal_ch{chap}_{label}_{amendment_id}"
                if chap is not None
                else f"pure_repeal_{label}_{amendment_id}"
            )
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=StructuralAction.REPEAL,
                    target=LegalAddress(path=target_path),
                    source=repeal_src,
                    group_id=f"finland-johto:{amendment_id}",
                )
            )
            injected += 1

    return injected


def _inject_pure_kumotaan_subsection_repeal_ops(
    lo_ops_out: List[_LegalOperation],
    *,
    amendment_id: str,
    kumotaan_subsection_map: dict[str, list[str]],
    amendment_effective_date: "dt.date",
    amendment_issue_date: Optional["dt.date"] = None,
    state: "ReplayState",
) -> int:
    """Inject REPLACE (repeal-placeholder) lo_ops for pure-kumotaan subsection ranges.

    Handles "N §:n M–P momentti" kumotaan clauses where the amendment contains no
    body text for those subsections (no REPLACE or INSERT op was produced for them).
    Injects explicit REPLACE ops carrying a repeal-placeholder IRNode so that
    compile_timelines creates a non-None version and materialize_pit renders the
    subsection as a tombstone node with lawvm_repeal_placeholder="1" rather than
    omitting it entirely.

    Only injects for subsections that:
    - are NOT already covered by a REPLACE or REPEAL op from this amendment, AND
    - exist in the current parent state (section exists, subsection exists).

    Returns the number of ops injected.
    """
    if not kumotaan_subsection_map:
        return 0

    # Build set of (section_lower, subsection_lower) pairs already covered by
    # any non-snapshot REPLACE op from this amendment targeting a subsection.
    # Snapshot REPEAL ops are deliberately excluded: if a snapshot created a
    # REPEAL for the same subsection, we still want to inject a REPLACE+placeholder
    # so that the subsection appears as a repeal marker rather than being absent
    # entirely. The REPLACE+placeholder will have a higher list index than the
    # snapshot REPEAL and will win via pick_latest's same_source_late_placeholder
    # tie-break (placeholder with higher index wins over non-placeholder).
    covered: set[tuple[str, str]] = set()
    for lo in lo_ops_out:
        src = lo.source
        if src is None or src.statute_id != amendment_id:
            continue
        # Skip snapshot-created ops (op_id starts with "snapshot_")
        if lo.op_id.startswith("snapshot_"):
            continue
        if lo.action not in (StructuralAction.REPEAL, StructuralAction.REPLACE):
            continue
        if lo.target is None:
            continue
        sec_label = next((v for k, v in reversed(lo.target.path) if k == "section"), "")
        sub_label = next((v for k, v in reversed(lo.target.path) if k == "subsection"), "")
        if sec_label and sub_label:
            covered.add((sec_label.lower(), sub_label.lower()))

    effective_iso = amendment_effective_date.isoformat()
    enacted_iso = amendment_issue_date.isoformat() if amendment_issue_date else effective_iso
    repeal_src = OperationSource(
        statute_id=amendment_id,
        enacted=enacted_iso,
        effective=effective_iso,
    )
    injected = 0
    for sec_label, sub_labels in kumotaan_subsection_map.items():
        # Find the section path (with chapter context if needed).
        sec_path = state.find_section_path(sec_label, None)
        if sec_path is None:
            continue
        # Build prefix path from resolved section path.  Strip empty-label
        # components (e.g. hcontainer wrappers with no label) so that the
        # resulting LegalAddress matches the timeline address space, which
        # only includes nodes whose label is non-empty.
        resolved_sec_path: tuple[tuple[str, str], ...] = tuple(
            (k, v) for k, v in sec_path if v
        )

        for sub_label in sub_labels:
            if (sec_label.lower(), sub_label.lower()) in covered:
                continue
            # Check that the subsection exists in the current IR.
            sec_node = state.find_section(sec_label)
            if sec_node is None:
                break  # Section itself doesn't exist; skip all its subsections.
            sub_exists = any(
                c.kind is IRNodeKind.SUBSECTION and c.label == sub_label
                for c in sec_node.children
            )
            if not sub_exists:
                continue

            target_path = resolved_sec_path + (("subsection", sub_label),)
            op_id = f"pure_subsec_repeal_{sec_label}_{sub_label}_{amendment_id}"
            # Use REPLACE with a repeal-placeholder payload so that
            # compile_timelines creates a version with non-None content and
            # materialize_pit renders the subsection as a tombstone node rather
            # than omitting it entirely.
            sub_placeholder = IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=sub_label,
                attrs={"lawvm_repeal_placeholder": "1"},
                children=(),
            )
            lo_ops_out.append(
                _LegalOperation(
                    op_id=op_id,
                    sequence=0,
                    action=StructuralAction.REPLACE,
                    target=LegalAddress(path=target_path),
                    payload=sub_placeholder,
                    source=repeal_src,
                    group_id=f"finland-johto:{amendment_id}",
                )
            )
            injected += 1

    return injected


def _emit_restructure_plan_renumber_legal_operations(
    *,
    lo_ops_out: Optional[List[_LegalOperation]],
    migration_events: tuple["MigrationEvent", ...],
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
) -> int:
    """Emit explicit RENUMBER LOs for restructure-plan migration events.

    The restructure-plan executor records migration events, but
    ``compile_timelines()`` only tombstones the source lineage when it also
    sees an executable ``RENUMBER`` operation. Emit those bounded LOs here so
    scope-changing relabels do not leave their source timeline alive.
    """
    if lo_ops_out is None or not migration_events:
        return 0

    source = OperationSource(
        statute_id=amendment_id,
        title=source_title,
        enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
        effective=amendment_effective_date.isoformat() if amendment_effective_date else "",
        raw_text="",
    )
    emitted = 0
    for index, event in enumerate(migration_events, start=1):
        if event.kind != "renumber":
            continue
        lo_ops_out.append(
            _LegalOperation(
                op_id=f"restructure_renumber_{amendment_id}_{index}",
                sequence=0,
                action=StructuralAction.RENUMBER,
                target=event.from_address,
                destination=event.to_address,
                source=source,
                group_id=f"finland-restructure:{amendment_id}",
            )
        )
        emitted += 1
    return emitted


def _oracle_version_future_repeal_only_uses_cutoff_date(
    *,
    compiled_ops: Iterable[Dict[str, object]],
    oracle_version_amendment_id: str,
    oracle_cutoff_iso: Optional[str],
) -> bool:
    """Return True when a future-effective oracle-version amendment is repeal-only.

    Finlex oracle materialization usually follows the oracle-version
    amendment's own effective date. That is correct for future-effective
    replacement families such as ``2016/258 <- 2021/1199``.

    Some oracle-version amendments are different: they are pure future repeals
    that Finlex still shows only as editorial future notice at the consolidated
    cutoff, without projecting the repeal into the selected XML. In that
    bounded family, materialization must stay at the oracle cutoff.
    """
    if not oracle_version_amendment_id or oracle_cutoff_iso is None:
        return False

    saw_oracle_version_op = False
    for op in compiled_ops:
        if str(op.get("source_statute") or "") != oracle_version_amendment_id:
            continue
        saw_oracle_version_op = True
        if str(op.get("action") or "").strip().lower() != "repeal":
            return False

    return saw_oracle_version_op


def _duplicate_section_labels_across_chapters(master_ir: IRNode) -> Set[str]:
    """Backward-compat wrapper for scope.duplicate_section_labels_across_chapters."""
    return _duplicate_section_labels_across_chapters_impl(master_ir)


def _chapter_chunks_from_johtolause(johto: str) -> List[Tuple[str, str]]:
    """Backward-compat wrapper for scope.chapter_chunks_from_johtolause."""
    return _chapter_chunks_from_johtolause_impl(johto)


def _strip_unjustified_chapter_scope_from_unique_sections(
    los: List["_LegalOperation"],
    johto: str,
    master: "ReplayState",
) -> List["_LegalOperation"]:
    """Backward-compat wrapper for scope.strip_unjustified_chapter_scope_from_unique_sections."""
    return _strip_unjustified_chapter_scope_from_unique_sections_impl(los, johto, master)


def _assign_chapter_scope_from_johtolause(
    los: List["_LegalOperation"], johto: str, master: "ReplayState"
) -> List["_LegalOperation"]:
    """Backward-compat wrapper for scope.assign_chapter_scope_from_johtolause."""
    return _assign_chapter_scope_from_johtolause_impl(los, johto, master)


def _find_body_section_chapter(
    muutos_tree: "etree._Element",
    section_norm: str,
) -> Optional[str]:
    """Return the chapter label for *section_norm* in the amendment body.

    Uses the body inventory (which handles pseudo-chapter-markers) to find
    which chapter a section belongs to in the amendment body.  Returns None
    if the section is not found or has no chapter context.

    Used to correct chapter scope when ``chapter_scope_carry_forward`` was
    applied and the amendment body places the section in a letter-suffix
    chapter created via pseudo-marker (e.g. "7 a luku" inside chapter 7).
    """
    inventory = _build_observed_body_inventory(muutos_tree)
    for bpu in inventory:
        if bpu.kind == "section" and _norm_num_token(bpu.label) == section_norm and bpu.chapter_label:
            return bpu.chapter_label
    return None


def _retarget_heading_insert_body_chapter_from_close_live_sibling(
    *,
    muutos_tree: "etree._Element",
    section_norm: str,
    body_chapter: str,
    master: "ReplayState",
) -> str:
    """Retarget a stale heading-only insert wrapper from a very close live sibling.

    This is intentionally narrow. It only applies to numeric section-heading
    inserts where the amendment body chapter wrapper is stale after a nearby
    chapter relabeling, such as `1962/420` + `2024/247` (`22 §` under stale
    `3 luku`, while the live neighbor `20 §` already lives under chapter `4`).
    """
    if not re.fullmatch(r"\d+", section_norm):
        return body_chapter

    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return body_chapter

    target_num = int(section_norm)
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if sec_label != section_norm:
            continue
        parent = sec.getparent()
        if parent is None or etree.QName(parent.tag).localname != "chapter":
            return body_chapter
        chapter_num = parent.find("{*}num")
        if chapter_num is None or not chapter_num.text:
            return body_chapter
        parent_label = _norm_num_token(chapter_num.text).removesuffix("luku")
        if parent_label != body_chapter:
            return body_chapter

        close_live_chapters: dict[int, set[str]] = defaultdict(set)
        for sibling in parent.findall("./{*}section"):
            sibling_num = sibling.find("{*}num")
            if sibling_num is None or not sibling_num.text:
                continue
            sibling_label = _norm_num_token(re.sub(r"\s*§.*$", "", sibling_num.text).strip())
            if not re.fullmatch(r"\d+", sibling_label):
                continue
            distance = abs(int(sibling_label) - target_num)
            if distance == 0 or distance > 2:
                continue
            live_path = master.find_section_path(sibling_label, None, None)
            if live_path is None:
                continue
            live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
            if live_chapter:
                close_live_chapters[distance].add(live_chapter)
        if close_live_chapters:
            nearest_distance = min(close_live_chapters)
            nearest_live_chapters = close_live_chapters[nearest_distance]
            if len(nearest_live_chapters) == 1:
                return next(iter(nearest_live_chapters))
        return body_chapter

    return body_chapter


def _retarget_duplicate_body_section_scope_from_close_live_siblings(
    *,
    muutos_tree: "etree._Element",
    section_norm: str,
    body_chapter: str,
    body_part: str | None,
    master: "ReplayState",
) -> tuple[str | None, str] | None:
    """Retarget stale duplicate-labelled body scope from nearby live siblings.

    This is intentionally narrow. It only applies when the amendment body places
    a numeric section under one chapter, that section label is duplicated in the
    live tree, and close numeric siblings in the same body chapter unanimously
    resolve to one different live chapter. This covers stale body wrappers like
    `1999/488 <- 2021/984`, where body chapter `3` carries sections `16–20`
    even though the live statute keeps that family in chapter `4`.
    """
    target_match = re.fullmatch(r"(\d+)[a-z]?", section_norm, re.I)
    if target_match is None:
        return None

    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return None

    target_num = int(target_match.group(1))
    # For letter-suffix sections (e.g. "16a", "16b", "17a"), the base-number
    # sibling (e.g. "16") at distance 0 is a *different* section and is the
    # most relevant anchor for chapter routing.  Allow distance-0 in that case.
    # For plain numeric sections, distance-0 would be self-reference — still excluded.
    is_letter_suffix_section = section_norm != str(target_num)

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "part":
                part_num = parent.find("{*}num")
                if part_num is None or not part_num.text:
                    return None
                raw = _norm_num_token(part_num.text).removesuffix("osa")
                arabic = _roman_label_to_arabic(raw.lower()) if raw else None
                return str(arabic) if arabic is not None else (raw or None)
            parent = parent.getparent()
        return None

    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if sec_label != section_norm:
            continue

        parent = sec.getparent()
        if parent is None or etree.QName(parent.tag).localname != "chapter":
            continue
        chapter_num = parent.find("{*}num")
        if chapter_num is None or not chapter_num.text:
            continue
        parent_label = _norm_num_token(chapter_num.text).removesuffix("luku")
        if parent_label != body_chapter:
            continue

        if _part_label_for_element(sec) != body_part:
            continue

        close_live_scopes: dict[int, set[tuple[str | None, str]]] = defaultdict(set)
        for sibling in parent.findall("./{*}section"):
            sibling_num = sibling.find("{*}num")
            if sibling_num is None or not sibling_num.text:
                continue
            sibling_label = _norm_num_token(re.sub(r"\s*§.*$", "", sibling_num.text).strip())
            sibling_match = re.fullmatch(r"(\d+)[a-z]?", sibling_label, re.I)
            if sibling_match is None:
                continue
            if sibling_label != sibling_match.group(1):
                continue
            distance = abs(int(sibling_match.group(1)) - target_num)
            if distance > 2:
                continue
            # For plain numeric sections exclude self (distance 0 = same number).
            # For letter-suffix sections the base section at distance 0 is a
            # different section and the best chapter anchor — include it.
            if distance == 0 and not is_letter_suffix_section:
                continue
            live_path = master.find_section_path(sibling_match.group(1), None, body_part)
            if live_path is None:
                continue
            live_part = next((label for kind, label in live_path if kind == "part"), None)
            live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
            if not live_chapter:
                continue
            if live_chapter == body_chapter and live_part == body_part:
                continue
            close_live_scopes[distance].add((live_part, live_chapter))

        if close_live_scopes:
            nearest_distance = min(close_live_scopes)
            nearest_live_scopes = close_live_scopes[nearest_distance]
            if len(nearest_live_scopes) == 1:
                return next(iter(nearest_live_scopes))
        return None

    return None


def _body_has_pseudo_chapter_marker(
    muutos_tree: "etree._Element",
    chapter_label: str,
) -> bool:
    """Return True if the amendment body contains a pseudo-chapter-marker for *chapter_label*.

    A pseudo-chapter-marker is a ``<section><num>X luku</num>...</section>`` element
    inside a ``<chapter>`` XML element, acting as a sub-chapter boundary.  This
    distinguishes structural reorganisation amendments (like 1996/473, which moves
    sections between sub-chapters via pseudo-markers) from ordinary amendments that
    happen to operate on a letter-suffix chapter (like 2008/732 operating on chapter 2a).
    """
    inventory = _build_observed_body_inventory(muutos_tree)
    for bpu in inventory:
        if bpu.kind == "chapter" and bpu.label == chapter_label and bpu.xml_element is not None:
            tag = getattr(bpu.xml_element, "tag", None)
            if tag is not None and etree.QName(tag).localname == "section":
                return True
    return False


def _body_has_real_chapter_container(
    muutos_tree: "etree._Element",
    chapter_label: str,
) -> bool:
    """Return True when the amendment body contains a real <chapter> for *chapter_label*."""
    inventory = _build_observed_body_inventory(muutos_tree)
    for bpu in inventory:
        if bpu.kind == "chapter" and bpu.label == chapter_label and bpu.xml_element is not None:
            tag = getattr(bpu.xml_element, "tag", None)
            if tag is not None and etree.QName(tag).localname == "chapter":
                return True
    return False


def _group_ops_by_target(
    ops: List[AmendmentOp],
) -> Dict[Tuple[IRNodeKind, str, Optional[str], Optional[str]], List[AmendmentOp]]:
    """Backward-compat wrapper for group_plan.group_ops_by_target."""
    return _group_ops_by_target_impl(ops)


def _coalesce_same_target_mixed_scope_section_groups(
    section_groups: Dict[Tuple[IRNodeKind, str, Optional[str], Optional[str]], List[AmendmentOp]],
    *,
    master: "ReplayState",
    muutos_tree: "etree._Element",
) -> Dict[Tuple[IRNodeKind, str, Optional[str], Optional[str]], List[AmendmentOp]]:
    """Merge mixed-scope section groups, but tag inherited scoped ownership.

    A bare section group must not silently inherit scoped ownership from a
    sibling group. When coalescing is needed to keep one sparse section payload
    coherent, inherited bare ops are tagged so the scope upgrade survives as a
    first-class witness instead of disappearing inside group formation.
    """
    merged = dict(section_groups)
    section_keys = [key for key in merged if key[0] is IRNodeKind.SECTION]
    buckets: dict[tuple[str, Optional[str]], list[Tuple[IRNodeKind, str, Optional[str], Optional[str]]]] = defaultdict(list)

    def _op_merge_signature(op: AmendmentOp) -> tuple[object, ...]:
        return (
            op.op_type,
            op.target_unit_kind,
            _norm_num_token(op.target_section or ""),
            _norm_num_token(op.target_chapter or "") if op.target_chapter else "",
            _norm_num_token(op.target_part or "") if op.target_part else "",
            op.target_paragraph,
            _norm_num_token(op.target_item or "") if op.target_item else "",
            str(op.target_special or "").strip(),
        )

    for key in section_keys:
        _unit_kind, target_norm, _target_chapter, target_part = key
        buckets[(target_norm, target_part)].append(key)

    for (target_norm, target_part), keys in buckets.items():
        unscoped_key = next((key for key in keys if not key[2]), None)
        scoped_keys = [key for key in keys if key[2]]
        if unscoped_key is None or len(scoped_keys) != 1:
            continue
        scoped_key = scoped_keys[0]
        scoped_chapter = scoped_key[2]
        if scoped_chapter is None:
            continue

        live_path = master.find_section_path(target_norm, None, target_part)
        if live_path is None:
            continue
        live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
        if live_chapter != scoped_chapter:
            continue

        body_chapter = _find_body_section_chapter(muutos_tree, target_norm)
        if body_chapter not in (None, scoped_chapter):
            continue

        scoped_ops = merged.get(scoped_key)
        unscoped_ops = merged.get(unscoped_key)
        if not scoped_ops or not unscoped_ops:
            continue

        scoped_signatures = {_op_merge_signature(op) for op in scoped_ops}
        tagged_unscoped_ops: list[AmendmentOp] = []
        unique_tagged_unscoped_ops: list[AmendmentOp] = []
        for op in unscoped_ops:
            merged_scope_confidence = normalize_scope_confidence(
                scope_confidence_from_tags(
                    (*op.scope_provenance_tags, "mixed_scope_group_merge"),
                    resolved_chapter=scoped_chapter,
                ),
                resolved_chapter=scoped_chapter,
            )
            tagged_op = dc_replace(
                op,
                target_chapter=scoped_chapter,
                scope_provenance_tags=tuple(op.scope_provenance_tags) + ("mixed_scope_group_merge",),
                scope_confidence=merged_scope_confidence,
                lo=_lo_with_path_update(op.lo, chapter=scoped_chapter) if op.lo is not None else op.lo,
            )
            object.__setattr__(tagged_op, "scope_confidence", merged_scope_confidence)
            tagged_unscoped_ops.append(tagged_op)
            if _op_merge_signature(tagged_op) not in scoped_signatures:
                unique_tagged_unscoped_ops.append(tagged_op)

        if not unique_tagged_unscoped_ops:
            del merged[unscoped_key]
            continue

        merged[scoped_key] = sorted(
            [*scoped_ops, *unique_tagged_unscoped_ops],
            key=lambda op: (
                op.lo.sequence if op.lo is not None else 10**9,
                op.target_paragraph or 0,
            ),
        )
        del merged[unscoped_key]

    return merged


# ---------------------------------------------------------------------------
# Constraint predicates + filter (moved to lawvm.finland.constraints; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.constraints import (  # noqa: E402, F401
    _find_muutos_node,
    _is_language_variant_only_johtolause,
    _johtolause_mentions_section,
    _FilterCtx,
    _c_language_variant,
    _c_false_positive_reference,
    _c_no_source_payload,
    _c_no_heading_payload,
    _c_whole_section_subsumes_children,
    _c_replace_when_insert_same_paragraph,
    _c_phantom_subsection,
    _OP_CONSTRAINTS,
    _filter_ops_by_constraints,
)

# ---------------------------------------------------------------------------
# Pure IR surgery helpers (moved to lawvm.finland.apply_ir_ops; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.apply_ir_ops import (  # noqa: E402, F401
    _kumottu_attribution,
    _build_repeal_placeholder_ir,
    _build_repeal_placeholder_from_label_ir,
    _relabel_section_ir,
    _relabel_paragraph_ir,
    _insert_item_with_suffix_renumber_ir,
    _relabel_subsection_ir,
    _rebuild_section_with_subsections_ir,
    _insert_subsection_with_renumber_ir,
    _strip_standalone_subsection_item_prefixes_ir,
)

# ---------------------------------------------------------------------------
# Amend-payload helpers (moved to lawvm.finland.apply_payload_ops; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.apply_payload_ops import (  # noqa: E402, F401
    _find_amend_paragraph,
    _find_amend_intro,
    _flattened_item_paragraph_from_subsection_ir,
    _has_single_intro_numbered_item_list_ir,
    _collapse_intro_list_amend_subsection_ir,
    _make_item_repeal_placeholder_ir,
    _has_intro_list_moment_shape_ir,
)

# ---------------------------------------------------------------------------
# Runtime-support helpers (moved to lawvm.finland.apply_runtime_support)
# ---------------------------------------------------------------------------
from lawvm.finland.apply_runtime_support import (  # noqa: E402, F401
    _snapshot_op_source,
    _emit_section_snapshot,
    _prefer_unique_substantive_section_path_over_placeholder,
    _resolved_destination_path_for_rop,
    _timeline_target_exists,
    _valid_target_group_path_hint,
    _find_insert_parent_path,
)

# ---------------------------------------------------------------------------
# Apply-cluster entrypoints and executor helpers
# ---------------------------------------------------------------------------
from lawvm.finland.apply import (  # noqa: E402, F401
    apply_op,
)
from lawvm.finland.apply_events import (  # noqa: E402, F401
    ApplyMutationAccountingResult,
    ApplyMutationEvent,
    ApplyMutationInvariantReport,
    build_apply_mutation_invariant_reports,
    check_apply_mutation_invariant_reports,
    check_apply_mutation_accounting,
)
from lawvm.finland.migration_ledger import MigrationLedger  # noqa: E402
from lawvm.finland.apply_ir_ops import (  # noqa: E402, F401
    _rewrite_bracketed_single_subsection_replace_ir,
)
from lawvm.finland.apply_subsection_dispatch import (  # noqa: E402, F401
    _apply_deterministic_subsection_op,
)
from lawvm.finland.apply_structure_ops import (  # noqa: E402, F401
    _apply_container_op,
    _apply_whole_section_op,
    _apply_materialization,
    _normalize_subsection_target_hint_ir,
)
from lawvm.finland.payload_normalize import (  # noqa: E402, F401
    GroupPayloadNormalizationResult,
    prepare_payload_surface,
    elaborate_payload_against_live,
    _normalize_item_like_target as _normalize_item_like_target_impl,
    _prune_container_payload_sections_shadowed_by_standalone_targets as _prune_container_payload_sections_shadowed_by_standalone_targets_impl,
)
from lawvm.finland.group_plan import (
    target_group_key as _target_group_key_impl,
    group_ops_by_target as _group_ops_by_target_impl,
)
from lawvm.finland.scope import (
    duplicate_section_labels_across_chapters as _duplicate_section_labels_across_chapters_impl,
    chapter_chunks_from_johtolause as _chapter_chunks_from_johtolause_impl,
    strip_unjustified_chapter_scope_from_unique_sections as _strip_unjustified_chapter_scope_from_unique_sections_impl,
    assign_chapter_scope_from_johtolause as _assign_chapter_scope_from_johtolause_impl,
    restrict_sec1_fallback_to_parent as _restrict_sec1_fallback_to_parent_impl,
)
from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.finland.replay_products import (
    build_replay_products,
    ReplayProducts,
    validate_replay_products,
)
from lawvm.finland.replay_pipeline import (
    build_tree_invariant_finding,
    execute_replay_plan,
    populate_replay_meta,
    prepare_replay_plan,
)
# --- group_ops functions (extracted to lawvm.finland.group_ops) ---
from lawvm.finland.group_ops import (
    normalize_group_ops_for_repeal_reenact as _normalize_group_ops_for_repeal_reenact,
    remap_body_root_replace_group_before_terminal_voimaantulo as _remap_body_root_replace_group_before_terminal_voimaantulo,
    sort_group_ops_for_apply as _sort_group_ops_for_apply,
    append_compiled_group_ops as _append_compiled_group_ops,
)


# Moved to lawvm.finland.post_process; re-exported here for backward compat.
from lawvm.finland.post_process import (  # noqa: E402, F401
    _consolidate_kumottu_range,
    _KUMOTTU_PLACEHOLDER_RE,
    _SECTION_KUMOTTU_PLACEHOLDER_RE,
)


def _pre_create_amendment_chapters(
    state: "ReplayState",
    muutos_body: etree._Element,
    amendment_id: str,
    *,
    required_labels: Optional[set[tuple[str, str]]] = None,
) -> "tuple[ReplayState, list[tuple[str, str]]]":
    """Pre-create chapters from the amendment body that don't exist in master.

    This must run before section-level uncovered-body recovery so that
    newly inserted sections land in the correct chapter (e.g. section 50c
    → chapter 5c) rather than falling back to body level.

    Returns ``(updated_state, created_chapter_refs)`` so the caller can emit
    chapter-level LegalOperations for the new chapters. Each created ref is
    ``(part_label, chapter_label)``; ``part_label`` is empty for body-level
    chapters. Without a chapter-level timeline entry the timeline
    materialization step cannot reconstruct the new chapter in the PIT output
    even when section-level entries exist.
    """
    created_refs: List[tuple[str, str]] = []

    def _part_label_for_element(el: etree._Element) -> str:
        parent = el.getparent() if hasattr(el, "getparent") else None
        while parent is not None:
            if _tag(parent) == "part":
                part_num = parent.find("{*}num")
                if part_num is not None and part_num.text:
                    raw = _norm_num_token(part_num.text.strip())
                    raw = raw.removesuffix("osasto").removesuffix("osa")
                    arabic = _roman_label_to_arabic(raw)
                    return str(arabic) if arabic is not None else raw
            parent = parent.getparent() if hasattr(parent, "getparent") else None
        return ""

    def _find_existing_chapter_path(chapter_label: str, part_label: str) -> Optional[tuple[tuple[str, str], ...]]:
        if part_label:
            part_path = state.find("part", part_label)
            part_node = _tops.resolve(state.ir, part_path) if part_path is not None else None
            if part_path is None or part_node is None:
                return None
            chapter_path = _tops.find(part_node, "chapter", chapter_label)
            return part_path + chapter_path if chapter_path is not None else None
        return state.find("chapter", chapter_label)

    for ch_el in muutos_body.findall(".//{*}chapter"):
        ch_num = ch_el.find("{*}num")
        if ch_num is None or not ch_num.text:
            continue
        ch_label = _norm_num_token(ch_num.text).removesuffix("luku")
        if not ch_label:
            continue
        part_label = _part_label_for_element(ch_el)
        chapter_ref = (part_label, ch_label)
        if required_labels is not None and chapter_ref not in required_labels:
            continue
        if _find_existing_chapter_path(ch_label, part_label) is not None:
            continue
        # Create a minimal chapter node and insert it
        ch_heading = ch_el.find("{*}heading")
        ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=ch_num.text.strip())]
        if ch_heading is not None and ch_heading.text:
            ch_children.append(IRNode(kind=IRNodeKind.HEADING, text=ch_heading.text.strip()))
        new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=ch_label, children=tuple(ch_children))
        part_path = state.find("part", part_label) if part_label else None
        if part_path is not None:
            parent = tuple(part_path)
        else:
            family = _tops.find_family(state.ir, "chapter", ch_label)
            if family is not None:
                parent = family[:-1]
            else:
                parent = (("body", ""),) if state.ir.kind is IRNodeKind.BODY else ()
        state = state.with_ir(_tops.insert_sorted(state.ir, parent, new_ch))
        created_refs.append(chapter_ref)
        logger.debug("  [%s] uncovered chapter CREATE %s/%s", amendment_id, part_label or "-", ch_label)
    return state, created_refs


def _pre_create_pseudo_marker_chapters(
    state: "ReplayState",
    muutos_body: etree._Element,
    amendment_id: str,
) -> "tuple[ReplayState, list[tuple[str, str]]]":
    """Pre-create letter-suffix chapters introduced via pseudo-chapter-marker sections.

    Some Finland amendment XML encodes a new sub-chapter (e.g. '7 a luku') as a
    ``<section><num>7 a luku</num>...</section>`` inside a regular chapter element
    rather than as a proper ``<chapter>`` element.  The body inventory
    (``build_observed_body_inventory``) correctly tracks which sections follow such
    pseudo-markers, but the chapters themselves are never created by
    ``_pre_create_amendment_chapters`` (which only processes real ``<chapter>`` nodes).

    This function must be called **before** the PEG-op apply loop so that PEG INSERT
    ops targeting sections in pseudo-chapters (e.g. section 53a → chapter 7a) land
    in the correct chapter rather than falling back to body level.

    Returns ``(updated_state, created_chapter_refs)``.
    """
    created_refs: List[tuple[str, str]] = []

    def _part_label_for_element(el: etree._Element) -> str:
        parent = el.getparent() if hasattr(el, "getparent") else None
        while parent is not None:
            if _tag(parent) == "part":
                part_num = parent.find("{*}num")
                if part_num is not None and part_num.text:
                    raw = _norm_num_token(part_num.text.strip())
                    raw = raw.removesuffix("osasto").removesuffix("osa")
                    arabic = _roman_label_to_arabic(raw)
                    return str(arabic) if arabic is not None else raw
            parent = parent.getparent() if hasattr(parent, "getparent") else None
        return ""

    def _find_existing_chapter_path(chapter_label: str, part_label: str) -> Optional[tuple[tuple[str, str], ...]]:
        if part_label:
            part_path = state.find("part", part_label)
            part_node = _tops.resolve(state.ir, part_path) if part_path is not None else None
            if part_path is None or part_node is None:
                return None
            chapter_path = _tops.find(part_node, "chapter", chapter_label)
            return part_path + chapter_path if chapter_path is not None else None
        return state.find("chapter", chapter_label)

    for ch_el in muutos_body.findall(".//{*}chapter"):
        for child in ch_el:
            child_tag = child.tag
            if not isinstance(child_tag, str):
                continue
            if etree.QName(child_tag).localname != "section":
                continue
            num_el = child.find("{*}num")
            if num_el is None or not num_el.text:
                continue
            raw_num = num_el.text.strip()
            if not _norm_num_token(raw_num).endswith("luku"):
                continue
            pseudo_label = _norm_num_token(raw_num).removesuffix("luku")
            if not pseudo_label:
                continue
            part_label = _part_label_for_element(child)
            if _find_existing_chapter_path(pseudo_label, part_label) is not None:
                continue
            # Create a minimal chapter node for the pseudo-marker chapter
            ch_children: List[IRNode] = [IRNode(kind=IRNodeKind.NUM, text=raw_num)]
            new_ch = IRNode(kind=IRNodeKind.CHAPTER, label=pseudo_label, children=tuple(ch_children))
            part_path = state.find("part", part_label) if part_label else None
            if part_path is not None:
                parent = tuple(part_path)
            else:
                family = _tops.find_family(state.ir, "chapter", pseudo_label)
                if family is not None:
                    parent = family[:-1]
                else:
                    parent = (("body", ""),) if state.ir.kind is IRNodeKind.BODY else ()
            state = state.with_ir(_tops.insert_sorted(state.ir, parent, new_ch))
            created_refs.append((part_label, pseudo_label))
            logger.debug("  [%s] pseudo-chapter CREATE %s/%s", amendment_id, part_label or "-", pseudo_label)
    return state, created_refs


# _recover_uncovered_body_ops and _apply_uncovered_kumotaan moved to
# grafter_uncovered; re-exported via the import block near line 319.


# Moved to lawvm.finland.metadata; re-exported here for backward compat.
from lawvm.finland.metadata import (  # noqa: E402, F401
    _normalize_johtolause_verbs,
)


# Moved to lawvm.finland.citation_routing; re-exported here for backward compat.
# OP_KEYWORDS, _johtolause_references_parent, _title_explicitly_targets_other_statute,
# and route_amendment are imported above from lawvm.finland.citation_routing.


def _restrict_sec1_fallback_to_parent(sec1_text: str, parent_id: str) -> str:
    """Backward-compat wrapper for scope.restrict_sec1_fallback_to_parent."""
    return _restrict_sec1_fallback_to_parent_impl(sec1_text, parent_id)


# ---------------------------------------------------------------------------
# Body-driven chapter seeding for partial-base statutes
# (moved to lawvm.finland.chapter_seed; re-exported here for backward compat)
# ---------------------------------------------------------------------------
from lawvm.finland.chapter_seed import (  # noqa: E402, F401
    seed_missing_chapters as _seed_missing_chapters,
    _find_chapter_containers_with_omissions,
    _last_chapter_label,
    _next_chapter_label,
    _chapters_in_gap,
    _rebuild_at_path,
    _op_targets_chapter,
)


# ---------------------------------------------------------------------------
# Pre-scan: build future-repeal index for uncovered-body suppression
# ---------------------------------------------------------------------------


# _pre_scan_repeal_targets moved to grafter_uncovered; re-exported via the import block near line 319.


def _build_future_repeal_suffix(
    per_amendment: List[Set[RepealTargetRef]],
) -> List[Set[RepealTargetRef]]:
    """Pre-compute suffix unions of REPEAL targets in O(A) time.

    ``result[i]`` is the union of ``per_amendment[i+1 .. N-1]``, i.e. all
    repeal targets from amendments *after* index ``i``.  The old
    ``_future_repeals_for_index`` recomputed this from scratch each call,
    making the overall replay loop O(A²) in set unions.
    """
    n = len(per_amendment)
    suffix: List[Set[RepealTargetRef]] = [set() for _ in range(n)]
    for i in range(n - 2, -1, -1):
        suffix[i] = suffix[i + 1] | per_amendment[i + 1]
    return suffix


def _future_repeals_for_index(
    per_amendment: List[Set[RepealTargetRef]],
    idx: int,
) -> Set[RepealTargetRef]:
    """Return the union of REPEAL targets for all amendments after ``idx``."""
    result: Set[RepealTargetRef] = set()
    for i in range(idx + 1, len(per_amendment)):
        result.update(per_amendment[i])
    return result


# --- XML DOM Grafter ---


class XMLStatute:
    """Thin convenience wrapper around a statute XML tree.

    This intentionally exposes only the minimal helpers replay needs:
    locating sections/chapters/parts by human numbering and serializing the
    operative body text while stripping non-operative containers such as
    signatures and attachments.
    """

    def __init__(self, xml_bytes: bytes):
        self._base_xml_bytes: bytes = xml_bytes  # retained for dump.py tree fallback
        self.tree = etree.fromstring(xml_bytes)
        self.id = self._get_id()
        self.title = self._get_title()
        # IRNode body: the primary state tree. All mutations should target
        # this via tree_ops. lxml tree stays for reading amendment XML and
        # as a mutation target during the transition period.
        _body_el = self.tree.find(".//{*}body")
        if _body_el is None:
            _body_el = self.tree
        self._ir: IRNode = fi_xml_to_ir_node(_body_el, _fi_label_postprocessor)
        # The parsed IR is immutable at the kernel boundary, so the base
        # snapshot can share structure with the parsed tree directly.
        self._base_ir: IRNode = self._ir
        self._label_index: Optional[_tops.LabelIndex] = None
        # Populated by replay_simple() after timeline compilation.
        self.timelines: Optional[dict] = None

    @property
    def ir(self) -> IRNode:
        return self._ir

    @ir.setter
    def ir(self, value: IRNode) -> None:
        self._ir = value
        self._label_index = None  # invalidate index on mutation

    def _get_label_index(self) -> "_tops.LabelIndex":
        if self._label_index is None:
            self._label_index = _tops.build_label_index(self._ir)
        return self._label_index

    def _get_id(self) -> str:
        num_el = self.tree.find(".//{*}docNumber")
        return num_el.text.strip() if num_el is not None else "0/0"

    def _get_title(self) -> str:
        title_el = self.tree.find(".//{*}docTitle")
        return (
            etree.tostring(title_el, method="text", encoding="unicode").strip() if title_el is not None else "Unknown"
        )

    def _find_path(
        self, kind: str, label: str, scope_kind: Optional[str] = None, scope_label: Optional[str] = None
    ) -> tuple[tuple[str, str], ...] | None:
        path = _tops.find(
            self._ir, kind, label, scope_kind=scope_kind, scope_label=scope_label, label_index=self._get_label_index()
        )
        if path is not None:
            return path
        return _tops.find(self._ir, kind, label, scope_kind=scope_kind, scope_label=scope_label)

    def _find_node(
        self, kind: str, label: str, scope_kind: Optional[str] = None, scope_label: Optional[str] = None
    ) -> Optional[IRNode]:
        path = self._find_path(kind, label, scope_kind, scope_label)
        return _tops.resolve(self.ir, path) if path is not None else None

    def find_section_path(
        self, sec_num: str, chapter_num: Optional[str] = None, part_num: Optional[str] = None
    ) -> tuple[tuple[str, str], ...] | None:
        if part_num:
            part_path = self._find_path("part", part_num)
            expected_part = str(part_num).strip().upper()
            if part_path is None or str(part_path[-1][1]).strip().upper() != expected_part:
                return None
            part_node = _tops.resolve(self.ir, part_path) if part_path is not None else None
            if part_path is not None and part_node is not None:
                if chapter_num:
                    chapter_path = _tops.find(part_node, "chapter", chapter_num)
                    chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                    if chapter_path is not None and chapter_node is not None:
                        section_path = _tops.find(chapter_node, "section", sec_num)
                        if section_path is not None:
                            return part_path + chapter_path + section_path
                    return None
                section_path = _tops.find(part_node, "section", sec_num)
                if section_path is not None:
                    return part_path + section_path
            return None
        result = self._find_path(
            "section", sec_num, scope_kind=IRNodeKind.CHAPTER.value if chapter_num else None, scope_label=chapter_num
        )
        return result

    def find_section(
        self, sec_num: str, chapter_num: Optional[str] = None, part_num: Optional[str] = None
    ) -> Optional[IRNode]:
        path = self.find_section_path(sec_num, chapter_num, part_num)
        return _tops.resolve(self.ir, path) if path is not None else None

    def find_base_section(
        self, sec_num: str, chapter_num: Optional[str] = None, part_num: Optional[str] = None
    ) -> Optional[IRNode]:
        if part_num:
            part_path = _tops.find(self._base_ir, "part", part_num)
            part_node = _tops.resolve(self._base_ir, part_path) if part_path is not None else None
            if part_path is not None and part_node is not None:
                if chapter_num:
                    chapter_path = _tops.find(part_node, "chapter", chapter_num)
                    chapter_node = _tops.resolve(part_node, chapter_path) if chapter_path is not None else None
                    if chapter_path is not None and chapter_node is not None:
                        section_path = _tops.find(chapter_node, "section", sec_num)
                        if section_path is not None:
                            return _tops.resolve(self._base_ir, part_path + chapter_path + section_path)
                section_path = _tops.find(part_node, "section", sec_num)
                if section_path is not None:
                    return _tops.resolve(self._base_ir, part_path + section_path)
        path = _tops.find(
            self._base_ir,
            "section",
            sec_num,
            scope_kind=IRNodeKind.CHAPTER.value if chapter_num else None,
            scope_label=chapter_num,
        )
        return _tops.resolve(self._base_ir, path) if path is not None else None

    def find_chapter(self, chap_num: str) -> Optional[IRNode]:
        return self._find_node("chapter", chap_num)

    def find_part(self, part_num: str) -> Optional[IRNode]:
        return self._find_node("part", part_num)

    def serialize_text(self) -> str:
        """Serialize operative body text from IRNode, excluding appendices."""
        _SKIP_NAMES = frozenset({"signatures", "attachments", "conclusions", "omission"})

        def _text(node: IRNode) -> str:
            if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") in _SKIP_NAMES:
                return ""
            if node.text:
                return node.text
            return " ".join(p for p in (_text(c) for c in node.children) if p)

        return _text(self.ir)

    # ------------------------------------------------------------------
    # Bridge methods: Phase 1 of StatuteContext refactor.


# ---------------------------------------------------------------------------
# Standalone serialize_text — operates on any IRNode, no XMLStatute needed
# ---------------------------------------------------------------------------


def serialize_text(ir: IRNode) -> str:
    """Serialize operative body text from an IRNode, excluding appendices.

    This is the module-level equivalent of XMLStatute.serialize_text().
    It only walks the tree and carries no state — suitable for use with
    ReplayState.ir or any other IRNode.
    """
    _SKIP_NAMES = frozenset({"signatures", "attachments", "conclusions", "omission"})

    def _text(node: IRNode) -> str:
        if node.kind == IRNodeKind.HCONTAINER and node.attrs.get("name") in _SKIP_NAMES:
            return ""
        if node.text:
            return node.text
        return " ".join(p for p in (_text(c) for c in node.children) if p)

    return _text(ir)


# ---------------------------------------------------------------------------
# StatuteContext + ReplayState (moved to lawvm.finland.statute; re-exported)
# ---------------------------------------------------------------------------
from lawvm.finland.statute import StatuteContext, ReplayState, ReplayResult, OracleSelectorInfo  # noqa: E402


class _ContainerLookupShim(Protocol):
    def find_chapter(self, chap_num: str) -> Optional[IRNode]: ...

    def find_part(self, part_num: str) -> Optional[IRNode]: ...

# ---------------------------------------------------------------------------
# Metadata helpers (moved to lawvm.finland.metadata; re-exported for compat)
# ---------------------------------------------------------------------------
from lawvm.finland.metadata import (  # noqa: E402
    get_johtolause,
    get_operative_body_repeal_candidate,
    _amendment_effective_date,
    _amendment_effective_date_with_step,
    _amendment_expiry_date,
    _chapter_expiry_from_base,
    _commencement_expiry_override,
    _expiry_date_precedes_effective_date,
    _section_commencement_effective_override,
    _temporary_section_expiry_overrides,
    _statute_issue_date,
    _statute_id_sort_key,
)


def _resolve_applicable_amendment_records(
    parent_id: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    corpus: Optional[CorpusStore] = None,
    selector: ConsolidatedArtifactSelector | None = None,
) -> Tuple[List[Dict[str, object]], Optional[dt.date], Optional[str]]:
    if corpus is None:
        corpus = _get_corpus_store()
    cutoff_date, oracle_version_amendment_id = get_consolidated_meta(
        parent_id,
        selector=selector or ConsolidatedArtifactSelector.latest_cached_editorial(),
    )

    muutoslait = list(_amendment_children_by_parent().get(parent_id, ()))

    dated_muutoslait = []
    for amendment_id in muutoslait:
        xml_bytes = corpus.read_source(amendment_id)
        if xml_bytes is None:
            continue
        m_tree = etree.fromstring(xml_bytes)
        eff_date = _amendment_effective_date(m_tree)
        issue_date = _statute_issue_date(m_tree)
        title_el = m_tree.find(".//{*}docTitle")
        title = " ".join("".join(str(_t) for _t in title_el.itertext()).split()) if title_el is not None else ""
        dated_muutoslait.append((amendment_id, eff_date, issue_date, title))

    min_date = dt.date.min
    selection_basis_by_amendment: Dict[str, str] = {}

    def _ordering_date(
        eff_date: Optional[dt.date],
        issue_date: Optional[dt.date],
    ) -> dt.date:
        return eff_date or issue_date or min_date

    if mode == "legal_pit":
        # Use oracle-version filtering (same amendment set as finlex_oracle)
        # but sort chronologically by effective date.  Derive PIT cutoff from
        # the oracle version's effective date — dateConsolidated (cutoff_date) can be
        # the ZIP packaging date, years after the actual oracle content PIT.
        if oracle_version_amendment_id is not None:
            version_key = _oracle_mode_sort_key(oracle_version_amendment_id)
            applicable = [item for item in dated_muutoslait if _oracle_mode_sort_key(item[0]) <= version_key]
            for amendment_id, eff_date, issue_date, _title in dated_muutoslait:
                if amendment_id == oracle_version_amendment_id:
                    cutoff_date = _ordering_date(eff_date, issue_date)
                    break
        elif cutoff_date is not None:
            applicable = [
                (amendment_id, eff_date, issue_date, title)
                for amendment_id, eff_date, issue_date, title in dated_muutoslait
                if _ordering_date(eff_date, issue_date) <= cutoff_date
            ]
        else:
            applicable = dated_muutoslait
        oracle_reflected = get_consolidated_oracle_reflected_source_vts_children(
            parent_id,
            corpus=corpus,
            selector=selector,
        )
        if oracle_reflected:
            applicable_ids = {item[0] for item in applicable}
            override_items = [
                item for item in dated_muutoslait if item[0] in oracle_reflected and item[0] not in applicable_ids
            ]
            applicable.extend(override_items)
            for amendment_id, eff_date, issue_date, _title in override_items:
                selection_basis_by_amendment[amendment_id] = "oracle_editorial_repeal_stub_override"
                override_date = _ordering_date(eff_date, issue_date)
                if cutoff_date is None or override_date > cutoff_date:
                    cutoff_date = override_date
        ordered = sorted(
            applicable,
            key=lambda item: (
                _ordering_date(item[1], item[2]),
                item[2] or min_date,
                _statute_id_sort_key(item[0]),
            ),
        )
    elif mode == "finlex_oracle":
        applicable = dated_muutoslait
        if oracle_version_amendment_id is not None:
            version_key = _oracle_mode_sort_key(oracle_version_amendment_id)
            applicable = [item for item in applicable if _oracle_mode_sort_key(item[0]) <= version_key]
        elif cutoff_date is not None:
            # Some consolidated artifacts have a cutoff date but no usable fin@ version id.
            # In that case, fall back to effectivity filtering rather than including all
            # known amendments indiscriminately.
            applicable = [item for item in applicable if _ordering_date(item[1], item[2]) <= cutoff_date]
        oracle_reflected = get_consolidated_oracle_reflected_source_vts_children(
            parent_id,
            corpus=corpus,
            selector=selector,
        )
        if oracle_reflected:
            applicable_ids = {item[0] for item in applicable}
            override_items = [
                item for item in dated_muutoslait if item[0] in oracle_reflected and item[0] not in applicable_ids
            ]
            applicable.extend(override_items)
            for amendment_id, eff_date, issue_date, _title in override_items:
                selection_basis_by_amendment[amendment_id] = "oracle_editorial_repeal_stub_override"
                override_date = _ordering_date(eff_date, issue_date)
                if cutoff_date is None or override_date > cutoff_date:
                    cutoff_date = override_date
        ordered = sorted(
            applicable,
            key=lambda item: (
                _ordering_date(item[1], item[2]),
                item[2] or min_date,
                _statute_id_sort_key(item[0]),
            ),
        )
    else:
        raise ValueError(f"Unknown mode: {mode}")

    return (
        [
            {
                "sequence": idx,
                "statute_id": amendment_id,
                "title": title,
                "effective_date": eff_date.isoformat() if eff_date else "",
                "issue_date": issue_date.isoformat() if issue_date else "",
                "sort_mode": mode,
                "included": True,
                "selection_basis": selection_basis_by_amendment.get(amendment_id, ""),
            }
            for idx, (amendment_id, eff_date, issue_date, title) in enumerate(ordered, start=1)
        ],
        cutoff_date,
        oracle_version_amendment_id,
    )


# ---------------------------------------------------------------------------
# VTS repeal extraction (moved to lawvm.finland.vts; re-exported for compat)
# ---------------------------------------------------------------------------
from lawvm.finland.vts import (  # noqa: E402, F401
    _voimaantulo_repeal_fragment_for_parent,
    _vts_extract_after_citation,
    _expand_section_range_vts,
    VtsSkippedTarget,
    extract_voimaantulo_repeals,
    extract_vts_cross_statute_repeals,
    extract_vts_repeals_fallback,
)


# Moved to lawvm.finland.kumotaan; re-exported here for backward compat.
from lawvm.finland.kumotaan import (  # noqa: E402, F401
    _extract_kumotaan_section_refs,
    _extract_kumotaan_chapter_section_map,
    _extract_kumotaan_container_refs,
    _extract_kumotaan_subsection_refs,
    _extract_muutetaan_section_refs,
    _extract_muutetaan_chapter_section_map,
)


def _prune_container_payload_sections_shadowed_by_standalone_targets(
    master_or_ctx,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    muutos_ir: Optional[IRNode],
    standalone_section_targets: Set[str],
) -> Tuple[Optional[IRNode], bool, List[str]]:
    """Backward-compat wrapper for payload_normalize shadow pruning.

    Accepts either a ``PayloadElaborationContext`` or a legacy ``ReplayState``
    (mock master).  In the legacy case, builds a minimal ctx for the impl.
    """
    from lawvm.core.elaboration_context import PayloadElaborationContext as _PEC
    from lawvm.core.elaboration_context import _ReplayLookupStateLike as _RLS
    from lawvm.core.elaboration_context import _TargetSnapshotStateLike as _TSS

    if isinstance(master_or_ctx, _PEC):
        return _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
            master_or_ctx,
            target_unit_kind,
            target_norm,
            muutos_ir,
            standalone_section_targets,
        )
    # Legacy path: build ctx from either real ReplayState or a narrow container lookup shim.
    master = master_or_ctx
    if isinstance(master, _RLS):
        typed_master = cast(_RLS, master)
        lookups = snapshot_replay_lookups(typed_master)
        ctx = build_payload_elaboration_context(
            snapshot_target_context(
                cast(_TSS, typed_master),
                target_unit_kind,
                target_norm,
                None,
                lookups,
            ),
            lookups,
            row_anchor_normalizer=_norm_row_anchor_text,
        )
    else:
        shim = cast(_ContainerLookupShim, master)
        if target_unit_kind == "chapter":
            live_node = shim.find_chapter(target_norm)
        elif target_unit_kind == "part":
            live_node = shim.find_part(target_norm)
        else:
            live_node = None
        ctx = _PEC(
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=None,
            live_node=live_node,
            parent_node=None,
            subsection_slots=(),
            live_subsections=(),
            subsection_by_label={},
            item_index={},
            row_anchor_index={},
            container_member_labels=None,
            lookups=ReplayLookups(
                snapshot_rev=0,
                unique_section_paths={},
                chapter_members={},
                part_members={},
                all_section_labels=frozenset(),
            ),
        )
    return _prune_container_payload_sections_shadowed_by_standalone_targets_impl(
        ctx,
        target_unit_kind,
        target_norm,
        muutos_ir,
        standalone_section_targets,
    )


def _build_group_surface(
    group_ops: List[AmendmentOp],
    muutos_tree: etree._Element,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
) -> "PhaseResult":
    """Stage 1: extract amendment-body payload. Pure of live state.

    Returns a PhaseResult where:
    - ``output``       — ``GroupSurface`` (muutos_ir + cross_ir + source info)
    - ``observations`` — malformed payload shape (missing muutos_ir despite
                         non-trivial ops)
    - ``obligations``  — (none produced at this stage)
    """
    from lawvm.core.phase_result import PhaseBuilder

    source_statute = next(
        (str(op.source_statute or "") for op in group_ops if op.source_statute),
        "",
    )
    surface_findings: list[Finding] = []

    def _renumber_destination_section_label() -> Optional[str]:
        labels = {
            dest_path["section"]
            for op in group_ops
            if op.op_type == "RENUMBER"
            and op.lo is not None
            and op.lo.destination is not None
            and (dest_path := dict(op.lo.destination.path)).get("section")
        }
        if len(labels) != 1:
            return None
        return next(iter(labels))

    def _is_sparse_source_shell(node: IRNode | None) -> bool:
        if node is None or node.kind is not IRNodeKind.SECTION:
            return False
        has_omission = any(_is_omission_ir(child) for child in node.children)
        has_substantive_child = any(
            child.kind
            not in {
                IRNodeKind.NUM,
                IRNodeKind.HEADING,
                IRNodeKind.OMISSION,
            }
            and bool(irnode_to_text(child).strip())
            for child in node.children
        )
        return has_omission and not has_substantive_child

    muutos_ir, cross_ir = _find_muutos_ir(
        muutos_tree,
        target_unit_kind,
        target_norm,
        target_chapter,
        target_part,
    )
    if target_unit_kind == "section":
        destination_section = _renumber_destination_section_label()
        has_same_group_relabel = any(op.op_type == "RENUMBER" for op in group_ops)
        has_followup_payload_op = any(
            op.op_type != "RENUMBER"
            and op.target_unit_kind == "section"
            and not (
                has_same_group_relabel
                and op.op_type == "REPLACE"
                and _norm_num_token(op.target_section or "") == target_norm
                and op.target_paragraph is None
                and not op.target_item
                and not op.target_special
                and op.target_chapter == target_chapter
                and op.target_part == target_part
            )
            for op in group_ops
        )
        source_shell = _is_sparse_source_shell(muutos_ir)
        source_surface = "missing" if muutos_ir is None else "sparse_omission_shell"
        if destination_section is not None and has_followup_payload_op and (muutos_ir is None or source_shell):
            destination_ir, destination_cross_ir = _find_muutos_ir(
                muutos_tree,
                target_unit_kind,
                destination_section,
                None,
                None,
            )
            if destination_ir is not None and not _is_sparse_source_shell(destination_ir):
                muutos_ir, cross_ir = destination_ir, destination_cross_ir
                surface_findings.append(
                    Finding(
                        kind="ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
                        role="observation",
                        stage="_build_group_surface",
                        detail={
                            "kind": "ELAB.RECODIFICATION_DESTINATION_PAYLOAD_SURFACE",
                            "message": (
                                "Same-group recodification payload surface selected from the destination "
                                "section because the source-number body is absent or an omission shell."
                            ),
                            "target_unit_kind": target_unit_kind,
                            "source_target_norm": target_norm,
                            "destination_target_norm": destination_section,
                            "target_chapter": target_chapter or "",
                            "target_part": target_part or "",
                            "source_surface": source_surface,
                        },
                        source_statute=source_statute,
                        blocking=False,
                    )
                )
    if muutos_ir is not None and source_statute:
        muutos_ir, _ = normalize_source_ir(muutos_ir, source_statute)
    group_surface = _build_group_surface_factory(
        body_ir=muutos_ir,
        cross_heading_ir=cross_ir,
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
    )

    b = PhaseBuilder()
    if surface_findings:
        b.add_findings(tuple(surface_findings))
    if group_surface.body_ir is None and any(op.op_type not in ("REPEAL", "ADD_HEADING") for op in group_ops):
        b.add_findings((
            Finding(
                kind="ELAB.MISSING_PAYLOAD_SURFACE",
                role="observation",
                stage="_build_group_surface",
                detail={
                    "target_unit_kind": target_unit_kind,
                    "target_norm": target_norm,
                    "target_chapter": target_chapter or "",
                    "op_count": len(group_ops),
                },
                source_statute=source_statute,
                blocking=False,
            ),
        ))
    return b.finish(group_surface)


def _internal_replay_scope_row(
    *,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str | None,
) -> Dict[str, object]:
    """Canonical neutral replay-meta scope row for internal Finland reporting."""
    return {
        "source_statute": source_statute,
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
        "target_chapter": str(target_chapter or ""),
    }


def _internal_elaboration_observation_row(
    *,
    kind: str,
    stage: str,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    detail: dict[str, object],
) -> dict[str, object]:
    return {
        "kind": kind,
        "stage": stage,
        "detail": dict(detail),
        **_internal_replay_scope_row(
            source_statute=source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
        ),
    }


def _drop_payloadless_source_replace_shadowed_by_same_group_relabel(
    group_ops: List[AmendmentOp],
    *,
    muutos_ir: IRNode | None,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
) -> tuple[List[AmendmentOp], List[FailedOp]]:
    """Reject payloadless whole-section REPLACE ops shadowed by same-group relabel.

    A direct source-address section relabel can produce a source section group
    with no body payload plus a companion ``RENUMBER``. In that shape a whole-
    section ``REPLACE`` has no executable payload of its own and only preserves
    the stale source host the relabel is moving away from.
    """
    if muutos_ir is not None or target_unit_kind != "section":
        return group_ops, []
    if not any(op.op_type == "RENUMBER" for op in group_ops):
        return group_ops, []

    kept_ops: List[AmendmentOp] = []
    rejected_ops: List[FailedOp] = []
    for op in group_ops:
        if (
            op.op_type == "REPLACE"
            and op.target_unit_kind == "section"
            and _norm_num_token(op.target_section or "") == target_norm
            and op.target_paragraph is None
            and not op.target_item
            and not op.target_special
            and op.target_chapter == target_chapter
            and op.target_part == target_part
        ):
            rejected_ops.append(
                FailedOp.from_scope(
                    amendment_id=str(op.source_statute or ""),
                    description=str(op.description()),
                    reason="payloadless_source_replace_shadowed_by_relabel",
                    reason_code="ELAB.PAYLOADLESS_REPLACE_SHADOWED_BY_RELABEL",
                    target_section=target_norm,
                    target_unit_kind="section",
                    target_chapter=target_chapter,
                    target_part=target_part,
                )
            )
            continue
        kept_ops.append(op)
    return kept_ops, rejected_ops


def _elaborate_group(
    target_ctx: "TargetContext",
    lookups: "ReplayLookups",
    group_surface: GroupSurface,
    group_ops: List[AmendmentOp],
    standalone_section_targets: Set[str],
    *,
    foreign_scoped_standalone_section_targets: Set[str],
    target_part: str | None,
    muutos_tree: etree._Element,
    johto: str,
    profile: "ReplayProfile",
    strict_profile: Optional[StrictProfile],
) -> "PhaseResult":
    """Stage 2: elaborate payload against live state.

    Takes a ``GroupSurface`` from Stage 1 and typed snapshots ``target_ctx``
    and ``lookups``.  Builds a ``PayloadElaborationContext`` from them and
    passes it to ``prepare_payload_surface`` and
    ``elaborate_payload_against_live`` — no raw ``master`` access below this
    point.

    Returns a PhaseResult where:
    - ``output``       — ``ElaboratedGroup`` (elaborated ops + resolved payload
                         + slot assignment; ``was_filtered=True`` when no ops)
    - ``observations`` — sparse slot bindings, source pathologies, ambiguity
                         resolution signals, elaboration observations
    - ``obligations``  — strict-profile obligations (blocking=True),
                         sparse payload leftovers (blocking=False)
    """
    from lawvm.core.phase_result import PhaseBuilder

    target_unit_kind = group_surface.target_unit_kind
    target_norm = group_surface.target_norm
    target_chapter = group_surface.target_chapter
    observation_source_statute = group_surface.source_statute
    muutos_ir = group_surface.body_ir
    payload_ctx = build_payload_elaboration_context(
        target_ctx,
        lookups,
        row_anchor_normalizer=_norm_row_anchor_text,
    )
    muutos_ir = prepare_payload_surface(
        payload_ctx,
        group_ops,
        muutos_ir,
        profile,
        strict_profile,
    )
    # Build typed intermediate: captures pre-elaboration shape without live state.
    surface: PayloadSurface = _build_payload_surface(
        muutos_ir,
        group_surface.cross_heading_ir,
        source_statute=observation_source_statute,
    )

    local_rejected_ops: List[FailedOp] = []
    # Filter ops by constraint predicates (phase 1: pre-subsec-map)
    fctx = _FilterCtx(muutos_ir=muutos_ir, muutos_tree=muutos_tree, johto=johto)
    group_ops = _filter_ops_by_constraints(group_ops, fctx, rejected_ops_out=local_rejected_ops)
    group_ops, shadowed_replace_rejections = _drop_payloadless_source_replace_shadowed_by_same_group_relabel(
        group_ops,
        muutos_ir=muutos_ir,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
    )
    local_rejected_ops.extend(shadowed_replace_rejections)
    if not group_ops:
        elaborated = _build_elaborated_group_factory(
            muutos_ir=None,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=[],
            remapped_target_norm=target_norm,
            slot_assignment=None,
            was_filtered=True,
            payload_surface=surface,
            payload_completeness=None,
        )
        b = PhaseBuilder()
        b.add_findings(
            Finding(
                kind="ELAB.STRICT_REJECTED_OPERATION",
                role="obligation",
                stage="_elaborate_group",
                detail={**failed.as_detail(), "message": "operation rejected before apply"},
                source_statute=failed.amendment_id,
                blocking=True,
            )
            for failed in local_rejected_ops
        )
        return b.finish(elaborated)

    payload_norm = elaborate_payload_against_live(
        payload_ctx,
        group_ops,
        muutos_ir,
        standalone_section_targets,
        foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
        surface=surface,
    )
    muutos_ir = payload_norm.muutos_ir
    group_ops = list(payload_norm.group_ops)

    local_source_pathologies: List[SourcePathology] = list(payload_norm.source_pathologies or [])
    local_elaboration_observations: List[Dict[str, object]] = [
        _internal_elaboration_observation_row(
            kind=str(observation.kind or ""),
            stage=str(observation.stage or ""),
            detail=dict(observation.detail or {}),
            source_statute=observation_source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
        )
        for observation in (payload_norm.elaboration_observations or [])
        if str(observation.kind or "").strip()
    ]
    slot_assignment = payload_norm.slot_assignment
    local_payload_completeness: List[Dict[str, object]] = (
        [
            _internal_elaboration_observation_row(
                kind="ELAB.PAYLOAD_COMPLETENESS",
                stage="group_payload_normalization",
                detail={
                    "payload_completeness_kind": str(payload_norm.payload_completeness.kind or ""),
                    "reasons": list(payload_norm.payload_completeness.reasons or []),
                    "tail_policy": str(payload_norm.payload_completeness.tail_policy or ""),
                    **dict(payload_norm.payload_completeness.detail or {}),
                },
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            )
        ]
        if payload_norm.payload_completeness is not None
        else []
    )
    local_sparse_slot_bindings: List[Dict[str, object]] = [
        {
            **_internal_replay_scope_row(
                source_statute=observation_source_statute,
                target_unit_kind=target_unit_kind,
                target_norm=target_norm,
                target_chapter=target_chapter,
            ),
            "op_description": binding.op_description,
            "op_type": binding.op_type,
            "target_paragraph": binding.target_paragraph,
            "target_item": binding.target_item or "",
            "target_special": binding.target_special or "",
            "payload_slot_index": binding.payload_slot_index,
            "payload_slot_label": binding.payload_slot_label,
        }
        for binding in (slot_assignment.sparse_slot_bindings if slot_assignment is not None else [])
    ]
    local_sparse_leftovers: List[Dict[str, object]] = (
        [
            {
                **_internal_replay_scope_row(
                    source_statute=observation_source_statute,
                    target_unit_kind=target_unit_kind,
                    target_norm=target_norm,
                    target_chapter=target_chapter,
                ),
                "unassigned_slots": list(slot_assignment.unassigned_payload_slots),
            }
        ]
        if slot_assignment is not None and slot_assignment.unassigned_payload_slots
        else []
    )
    local_rejected_ops.extend(payload_norm.rejected_ops or ())
    local_strict_rejection_findings: List[Finding] = []
    if strict_profile is not None and payload_norm.source_pathologies:
        local_strict_rejection_findings.extend(
            _strict_rejected_source_pathology_finding(
                pathology,
                stage="_elaborate_group",
                fallback_source_statute=observation_source_statute,
            )
            for pathology in payload_norm.source_pathologies
        )

    if not group_ops:
        elaborated = _build_elaborated_group_factory(
            muutos_ir=None,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=[],
            remapped_target_norm=target_norm,
            slot_assignment=None,
            was_filtered=True,
            payload_surface=surface,
            payload_completeness=payload_norm.payload_completeness,
        )
    else:
        # Phase 2: phantom subsection filter (consumes slot_assignment fallback mapping)
        fctx.slot_assignment = slot_assignment
        group_ops = _filter_ops_by_constraints(group_ops, fctx, rejected_ops_out=local_rejected_ops)
        group_ops = _normalize_group_ops_for_repeal_reenact(group_ops)
        # Uses target_ctx + lookups (no raw master access)
        remapped_target_norm, muutos_ir, group_ops = _remap_body_root_replace_group_before_terminal_voimaantulo(
            target_ctx, lookups, muutos_ir, group_ops
        )
        elaborated = _build_elaborated_group_factory(
            muutos_ir=muutos_ir,
            cross_ir=group_surface.cross_heading_ir,
            group_ops=group_ops,
            remapped_target_norm=remapped_target_norm,
            slot_assignment=slot_assignment,
            source_pathologies=local_source_pathologies,
            was_filtered=False,
            payload_surface=surface,
            payload_completeness=payload_norm.payload_completeness,
        )

    b = PhaseBuilder()

    b.add_findings(
        Finding(
            kind="ELAB.REJECTED_OPERATION",
            role="observation",
            stage="_elaborate_group",
            detail={**failed.as_detail(), "message": "operation rejected before apply"},
            source_statute=failed.amendment_id,
            blocking=False,
        )
        for failed in local_rejected_ops
    )

    b.add_findings(
        Finding(
            kind="ELAB.STRICT_REJECTED_OPERATION",
            role="obligation",
            stage="_elaborate_group",
            detail={**failed.as_detail(), "message": "operation rejected before apply"},
            source_statute=failed.amendment_id,
            blocking=True,
        )
        for failed in local_rejected_ops
    )

    # Source pathologies → findings
    b.add_findings(
        Finding(
            kind="ELAB.SOURCE_PATHOLOGY",
            role="observation",
            stage="_elaborate_group",
            detail=p.as_detail(),
            source_statute=p.source_statute or observation_source_statute,
            blocking=False,
        )
        for p in local_source_pathologies
    )

    # Elaboration observations → findings
    b.add_findings(
        Finding(
            kind=str(o.get("kind", "")),
            role="observation",
            stage="_elaborate_group",
            detail=dict(o),
            source_statute=str(o.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for o in local_elaboration_observations
        if str(o.get("kind", "")).strip()
    )

    # Payload completeness witness → findings
    b.add_findings(
        Finding(
            kind="ELAB.PAYLOAD_COMPLETENESS",
            role="observation",
            stage="_elaborate_group",
            detail=dict(witness),
            source_statute=str(witness.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for witness in local_payload_completeness
    )

    # Sparse slot bindings → findings (diagnostic trace, not blocking)
    b.add_findings(
        Finding(
            kind="ELAB.SPARSE_SLOT_BINDING",
            role="observation",
            stage="_elaborate_group",
            detail=dict(binding),
            source_statute=str(binding.get("source_statute", observation_source_statute)),
            blocking=False,
        )
        for binding in local_sparse_slot_bindings
    )

    # Sparse payload leftovers → findings (unresolved slot mapping, non-blocking)
    b.add_findings(
        Finding(
            kind="ELAB.SPARSE_PAYLOAD_LEFTOVER",
            role="obligation",
            stage="_elaborate_group",
            detail=dict(leftover),
            blocking=False,
        )
        for leftover in local_sparse_leftovers
    )

    # Strict source-pathology rejections already exist as canonical findings.
    b.add_findings(local_strict_rejection_findings)

    return b.finish(elaborated)


def _assert_intent_agrees_with_legacy(rop: ResolvedOp) -> None:
    """DEBUG-only: verify typed intent is consistent with legacy waist fields.

    Called only when DEBUG is True and rop.intent is not None. Raises
    AssertionError on mismatch so regressions are caught during development.
    """
    from lawvm.core.canonical_intent import (
        FacetTarget,
        Insert,
        IntentKind,
        NodeTarget,
        Repeal,
        Replace,
    )

    intent = rop.intent
    assert intent is not None

    # Action family must match op_type
    _KIND_TO_OP_TYPE = {
        IntentKind.REPLACE: "REPLACE",
        IntentKind.INSERT: "INSERT",
        IntentKind.REPEAL: "REPEAL",
        IntentKind.RELABEL: "RENUMBER",
    }
    if intent.kind in _KIND_TO_OP_TYPE:
        assert rop.resolved_action_type == _KIND_TO_OP_TYPE[intent.kind], (
            f"Intent kind {intent.kind} disagrees with op_type {rop.resolved_action_type} for {rop.op_id}"
        )

    # FacetTarget facet must agree with target_special
    if isinstance(intent, (Replace, Repeal)):
        target = intent.target
        if isinstance(target, FacetTarget):
            target_special = rop.effective_target_special
            if target.facet == "heading":
                assert target_special in ("otsikko", "otsikko_edella"), (
                    f"FacetTarget(heading) but target_special={target_special} for {rop.op_id}"
                )
            elif target.facet == "intro":
                assert target_special == "johd", (
                    f"FacetTarget(intro) but target_special={target_special} for {rop.op_id}"
                )
        elif isinstance(target, NodeTarget):
            # NodeTarget should have no target_special
            assert rop.effective_target_special is None, (
                f"NodeTarget but target_special={rop.effective_target_special} for {rop.op_id}"
            )

    # Insert must have insert_order set
    if isinstance(intent, Insert):
        assert intent.contract.insert_order is not None, f"Insert intent missing insert_order for {rop.op_id}"


def _mixed_subsection_group_requires_insert_first(
    ops: List[AmendmentOp],
    target_ctx: "TargetContext",
) -> bool:
    """True when mixed subsection ops only become valid after insert-driven renumbering.

    Family: a group inserts a lower-numbered new moment and also targets a
    later replacement moment that does not yet exist in the live tree. In that
    case the replacement is expressed in post-insert legal numbering, so the
    insert must execute first to shift the live slot into place.
    """
    live_numeric_labels = {
        int(slot.label)
        for slot in target_ctx.subsection_slots
        if slot.label is not None and str(slot.label).isdigit()
    }
    if not live_numeric_labels:
        return False

    subsec_inserts = [
        o
        for o in ops
        if o.op_type == "INSERT" and o.target_paragraph is not None and not o.target_item and not o.target_special
    ]
    subsec_replaces = [
        o
        for o in ops
        if o.op_type == "REPLACE" and o.target_paragraph is not None and not o.target_item and not o.target_special
    ]
    subsec_renumbers = [
        o
        for o in ops
        if o.op_type == "RENUMBER" and o.target_paragraph is not None and not o.target_item and not o.target_special
    ]
    if not subsec_inserts or not subsec_replaces:
        return False

    insert_targets = {int(o.target_paragraph or 0) for o in subsec_inserts}
    renumber_targets = {int(o.target_paragraph or 0) for o in subsec_renumbers}
    if insert_targets & renumber_targets:
        for replace_op in subsec_replaces:
            if "rebase_duplicate_target_shifted_replace" not in replace_op.target_guessing_provenance_tags:
                continue
            replace_target = int(replace_op.target_paragraph or 0)
            if any(insert_target + 1 == replace_target for insert_target in insert_targets):
                return True

    max_live_label = max(live_numeric_labels)
    for replace_op in subsec_replaces:
        replace_target = int(replace_op.target_paragraph or 0)
        if replace_target in live_numeric_labels:
            continue
        insert_count_before_target = sum(
            1 for insert_op in subsec_inserts if int(insert_op.target_paragraph or 0) <= replace_target
        )
        if insert_count_before_target <= 0:
            continue
        if replace_target <= max_live_label + insert_count_before_target:
            return True
    return False


def _stabilize_insert_order(ops: List[AmendmentOp], target_ctx: "TargetContext") -> List[AmendmentOp]:
    """Stabilize mixed subsection apply order using the live snapshot.

    The sort heuristic in ``sort_group_ops_for_apply`` may place subsection
    INSERTs in descending order (alongside descending REPLACEs).  Descending
    INSERT order produces wrong label assignments because each
    INSERT-with-renumber shifts later sibling labels -- earlier inserts must
    execute first so that later inserts see the shifted labels.

    Default policy keeps REPLACEs before INSERTs because some explicit
    ``jolloin ... siirtyy`` families compile later REPLACEs against the
    pre-insert live numbering. But when the live snapshot proves that a
    REPLACE target is currently absent and only becomes reachable after an
    earlier insert shifts numbering, the inserts must execute first.

    Only rearranges when the group contains at least one subsection INSERT
    alongside at least one subsection REPLACE; pure-INSERT groups are already
    handled correctly by ``sort_group_ops_for_apply``.
    """
    subsec_inserts = [
        o
        for o in ops
        if o.op_type == "INSERT" and o.target_paragraph is not None and not o.target_item and not o.target_special
    ]
    subsec_replaces = [
        o
        for o in ops
        if o.op_type == "REPLACE" and o.target_paragraph is not None and not o.target_item and not o.target_special
    ]
    if not subsec_inserts or not subsec_replaces:
        return ops

    other_ops = [o for o in ops if o not in subsec_inserts and o not in subsec_replaces]
    ordered_replaces = [o for o in ops if o in subsec_replaces]
    ascending_inserts = sorted(
        subsec_inserts,
        key=lambda o: (o.target_paragraph or 0, o.target_item or ""),
    )
    same_wave_shift_renumbers = [
        o
        for o in other_ops
        if (
            o.op_type == "RENUMBER"
            and o.target_paragraph is not None
            and not o.target_item
            and not o.target_special
            and any(int(ins.target_paragraph or 0) == int(o.target_paragraph or 0) for ins in subsec_inserts)
            and any(
                "rebase_duplicate_target_shifted_replace" in rep.target_guessing_provenance_tags
                for rep in subsec_replaces
            )
        )
    ]
    retained_other_ops = [o for o in other_ops if o not in same_wave_shift_renumbers]
    if _mixed_subsection_group_requires_insert_first(ops, target_ctx):
        return retained_other_ops + ascending_inserts + ordered_replaces + same_wave_shift_renumbers
    return retained_other_ops + ordered_replaces + ascending_inserts + same_wave_shift_renumbers


def _lower_group(
    target_ctx: "TargetContext",
    elaborated: ElaboratedGroup,
    compiled_ops_out: Optional[List[Dict[str, object]]],
    master: Optional["ReplayState"] = None,
    lookups: Optional["ReplayLookups"] = None,
) -> "PhaseResult":
    """Stage 3: lower elaborated ops to ResolvedOps. Pure of live state.

    Takes an ``ElaboratedGroup`` from Stage 2 and ``target_ctx`` for the sort
    heuristic (reads the snapshot, not the mutable tree).

    When remapping occurred (remapped_target_norm != original target), resolves
    the remapped target's live node for correct sort ordering.

    Returns a PhaseResult where:
    - ``output``       — ``List[ResolvedOp]`` ready for apply
    - ``observations`` — (reserved for future lowering lints)
    - ``obligations``  — (none produced at this stage)
    """
    from lawvm.core.phase_result import PhaseBuilder

    target_chapter = target_ctx.target_chapter
    group_ops = list(elaborated.group_ops)
    muutos_ir = elaborated.muutos_ir
    cross_ir = elaborated.cross_ir
    remapped_target_norm = elaborated.remapped_target_norm
    slot_assignment = elaborated.slot_assignment

    # When remapping occurred, resolve the remapped target's context for sorting.
    # The sort heuristic inspects subsection children of the target section;
    # after remap (e.g. REPLACE 4 → INSERT 3a), we need the subsection labels
    # from the remapped target, not the original.
    if remapped_target_norm != target_ctx.target_norm and master is not None and lookups is not None:
        sort_ctx = snapshot_target_context(
            cast(Any, master),
            target_ctx.target_unit_kind,
            remapped_target_norm,
            target_chapter,
            lookups,
        )
    else:
        sort_ctx = target_ctx

    # Produce ResolvedOps in apply order (uses sort_ctx, no raw master)
    sorted_ops = _sort_group_ops_for_apply(sort_ctx, group_ops)

    # Post-sort: ensure subsection INSERTs are applied in ascending order
    # after REPLACEs.  The existing sort heuristic uses descending order for
    # mixed REPLACE+INSERT groups, which causes INSERT content to land at
    # wrong labels because each INSERT-with-renumber shifts later siblings.
    # REPLACEs use label-based resolution (_resolve_subsection_index) so
    # their order is immaterial.  INSERTs must be ascending so that earlier
    # inserts shift siblings before later inserts are placed.
    sorted_ops = _stabilize_insert_order(sorted_ops, sort_ctx)

    resolved: List[ResolvedOp] = []
    for op in sorted_ops:
        target_address = op.lo.target if op.lo is not None else None
        destination_address = (op.lo.destination if op.lo is not None else None) or (op.lo.anchor if op.lo is not None else None)
        if (
            op.target_version_statute_id
            and op.lo is not None
            and op.target_unit_kind == "section"
            and op.target_section
            and tuple(op.lo.target.path) == (("section", op.target_section),)
            and master is not None
            and _norm_num_token(op.target_section) not in master.duplicate_section_labels
        ):
            cited_live_path = master.find_section_path(op.target_section, None, op.target_part)
            if cited_live_path is not None and any(kind in {"chapter", "part"} for kind, _label in cited_live_path):
                target_address = LegalAddress(path=tuple(cited_live_path))
        if op.move_clause_target_unit_kind is not None and op.lo is not None and op.target_unit_kind == "section":
            source_path = master.find_section_path(op.target_section, None, op.target_part) if master is not None else None
            if source_path is not None:
                target_address = LegalAddress(path=tuple(source_path))
            if target_address is not None:
                destination_address = op.lo.target
        rop = ResolvedOp.from_amendment_op(
            op,
            muutos_ir=muutos_ir,
            cross_ir=cross_ir,
            target_unit_kind=target_ctx.target_unit_kind,
            target_norm=remapped_target_norm,
            target_chapter=target_chapter,
            slot_assignment=slot_assignment,
            payload_completeness=elaborated.payload_completeness,
            target_address=target_address,
            destination_address=destination_address,
        )
        if DEBUG and rop.intent is not None:
            _assert_intent_agrees_with_legacy(rop)
        resolved.append(rop)
    _append_compiled_group_ops(compiled_ops_out, resolved)
    return PhaseBuilder().finish(resolved)


def _group_has_scope_source(group_ops: Iterable[AmendmentOp], source: str) -> bool:
    source_norm = str(source or "").strip()
    if not source_norm:
        return False
    return any(
        (
            (
                witness := projection_scope_confidence(
                    scope_confidence=op.scope_confidence,
                    scope_provenance_tags=op.scope_provenance_tags,
                    resolved_chapter=op.target_chapter,
                )
            )
            is not None
            and witness.source == source_norm
        )
        for op in group_ops
    )


def _allow_unscoped_live_section_retarget(
    group_ops: Iterable[AmendmentOp],
) -> str | None:
    if _group_has_scope_source(group_ops, "carry_forward"):
        return "carry_forward"
    if _group_has_scope_source(group_ops, "explicit_scope_rewrite"):
        return "explicit_scope_rewrite"
    if _group_has_scope_source(group_ops, "explicit_chunk"):
        return "explicit_chunk"
    return None


def _source_body_chapter_for_scoped_section_target(
    *,
    muutos_tree: etree._Element,
    target_norm: str,
    target_chapter: str,
    target_part: str | None,
) -> str | None:
    """Return the source body chapter that actually contains the target section.

    `_find_muutos_ir(...)` may legally fall back to a same-numbered section in a
    different chapter when the requested chapter is absent from the amendment
    body.  Compile-time scope preservation must distinguish that fallback from a
    true payload that already lives under the scoped target chapter.
    """
    node = _find_muutos_node(
        muutos_tree,
        "section",
        target_norm,
        target_chapter,
        target_part,
    )
    if node is None:
        return None
    parent = node.getparent() if hasattr(node, "getparent") else None
    while parent is not None:
        tag = str(parent.tag).rsplit("}", 1)[-1] if isinstance(parent.tag, str) else ""
        if tag == "chapter":
            num_el = parent.find("{*}num")
            if num_el is None or not num_el.text:
                return None
            return _norm_num_token(num_el.text).removesuffix("luku") or None
        parent = parent.getparent()
    return None


def _source_body_scope_for_section_target(
    *,
    muutos_tree: etree._Element,
    target_norm: str,
) -> tuple[str | None, str | None] | None:
    """Return the unique body-backed (part, chapter) scope for one section label."""
    body = (
        muutos_tree
        if etree.QName(muutos_tree.tag).localname == "body"
        else muutos_tree.find(".//{*}body")
    )
    if body is None:
        return None

    def _part_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "part":
                part_num = parent.find("{*}num")
                if part_num is None or not part_num.text:
                    return None
                raw = _norm_num_token(part_num.text).removesuffix("osa")
                arabic = _roman_label_to_arabic(raw.lower()) if raw else None
                return str(arabic) if arabic is not None else (raw or None)
            parent = parent.getparent()
        return None

    def _chapter_label_for_element(el: etree._Element) -> str | None:
        parent = el.getparent()
        while parent is not None:
            if str(parent.tag).rsplit("}", 1)[-1] == "chapter":
                chapter_num = parent.find("{*}num")
                if chapter_num is None or not chapter_num.text:
                    return None
                return _norm_num_token(chapter_num.text).removesuffix("luku") or None
            parent = parent.getparent()
        return None

    scopes: set[tuple[str | None, str | None]] = set()
    for sec in body.findall(".//{*}section"):
        num_el = sec.find("{*}num")
        if num_el is None or not num_el.text:
            continue
        sec_label = _norm_num_token(re.sub(r"\s*§.*$", "", num_el.text).strip())
        if sec_label != target_norm:
            continue
        scopes.add((_part_label_for_element(sec), _chapter_label_for_element(sec)))

    if len(scopes) != 1:
        return None
    return next(iter(scopes))


def _resolve_group_surface_scope(
    *,
    muutos_tree: etree._Element,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: str | None,
    target_part: str | None,
    group_ops: Iterable[AmendmentOp],
) -> tuple[str | None, str | None]:
    """Return the Stage-1 payload-extraction scope for one target group.

    This is intentionally source-facing. It may differ from the live/effective
    target scope when the amendment body still carries the section payload under
    an earlier chapter wrapper even though the lowering path has already been
    retargeted to the current live chapter.
    """
    surface_target_chapter = target_chapter
    surface_target_part = target_part
    carry_forward_scoped = _group_has_scope_source(group_ops, "carry_forward")

    if target_unit_kind != "section":
        return surface_target_chapter, surface_target_part

    body_scope = _source_body_scope_for_section_target(
        muutos_tree=muutos_tree,
        target_norm=target_norm,
    )
    if carry_forward_scoped and body_scope == (None, None):
        return None, None
    if target_chapter and body_scope is not None:
        body_part, body_chapter = body_scope
        scoped_node = _find_muutos_node(
            muutos_tree,
            "section",
            target_norm,
            target_chapter,
            target_part,
        )
        body_node = _find_muutos_node(
            muutos_tree,
            "section",
            target_norm,
            body_chapter,
            body_part,
        )
        if (
            scoped_node is None
            and body_node is not None
            and (body_chapter != target_chapter or body_part != target_part)
        ):
            return body_chapter, body_part

    return surface_target_chapter, surface_target_part


def _compile_group(
    master: "ReplayState",
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_chapter: Optional[str],
    target_part: Optional[str],
    group_ops: List[AmendmentOp],
    standalone_section_targets: Set[str],
    inserted_chapter_labels: Set[str],
    muutos_tree: etree._Element,
    johto: str,
    profile: "ReplayProfile",
    compiled_ops_out: Optional[List[Dict[str, object]]],
    strict_profile: Optional[StrictProfile],
    foreign_scoped_standalone_section_targets: Optional[Set[str]] = None,
    precomputed_lookups: Optional[Any] = None,
) -> "PhaseResult":
    """Compile one group of ops (same target section/chapter) into ResolvedOps.

    Structured as three stages per PRO_RESPONSE3_1 section 7:

    1. **build_group_surface** — extract payload from amendment body (pure of
       live state).
    2. **elaborate_group** — normalize payload against live state using typed
       snapshots ``target_ctx`` and ``lookups``.  Builds a
       ``PayloadElaborationContext`` and passes it to
       ``prepare_payload_surface`` and ``elaborate_payload_against_live`` —
       no raw ``master`` access below this point.
    3. **lower_group** — sort ops and construct ``ResolvedOp`` list (reads only
       from ``target_ctx`` snapshot, no raw ``master``).

    Returns a PhaseResult whose ``output`` is a ``List[ResolvedOp]`` (empty
    list if the group is filtered out entirely).  Observations and obligations
    from all three stages are merged into the result.
    """
    from lawvm.core.phase_result import PhaseResult
    foreign_scoped_standalone_section_targets = set(foreign_scoped_standalone_section_targets or ())

    effective_target_chapter = target_chapter
    effective_target_part = target_part
    surface_target_chapter, surface_target_part = _resolve_group_surface_scope(
        muutos_tree=muutos_tree,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        target_part=target_part,
        group_ops=group_ops,
    )
    effective_group_ops = group_ops
    compile_findings: tuple[Finding, ...] = ()
    carry_forward_scoped = _group_has_scope_source(group_ops, "carry_forward")
    # Chapter-remap: correct target_chapter when the section lives in a different
    # chapter in the master state (e.g. after a prior chapter renumbering).
    # Guard: do NOT remap pure-INSERT groups — an INSERT creates a new section
    # that does not yet exist in the target chapter; finding the same label in
    # another chapter indicates a different section, not a mismatch to correct.
    _is_pure_insert_group = all(op.op_type == "INSERT" for op in group_ops)
    # Body-chapter correction: route INSERT ops to the chapter the amendment body
    # places them in when the johtolause scope is absent or inaccurate.
    #
    # Two cases:
    # 1. No chapter scope (target_chapter=None): base section is not inside any chapter
    #    in master so _unique_base_section_chapter returned None.  The body inventory
    #    (which handles pseudo-chapter-markers) is authoritative.  Only apply when the
    #    body chapter exists in master.
    # 2. Carry-forward scope (chapter_scope_carry_forward tag): chapter was inferred
    #    from the base section's chapter in master, but the body places the new section
    #    under a pseudo-marker that creates a letter-suffix sub-chapter (e.g. section
    #    53a belongs to "7a luku" inside chapter 7 → effective chapter "7a", not "7").
    #
    # Also updates op.lo so resolved_target_address picks up the corrected chapter
    # (op.lo.target.path is the primary source for that field in ResolvedOp).
    if _is_pure_insert_group and target_unit_kind == "section":
        _body_chapter = _find_body_section_chapter(muutos_tree, target_norm)
        resolved_body_chapter = _body_chapter
        if _body_chapter is not None and (not target_chapter or carry_forward_scoped):
            sibling_consensus_scope = _retarget_duplicate_body_section_scope_from_close_live_siblings(
                muutos_tree=muutos_tree,
                section_norm=target_norm,
                body_chapter=_body_chapter,
                body_part=target_part,
                master=master,
            )
            if sibling_consensus_scope is not None:
                _sibling_part, _sibling_chapter = sibling_consensus_scope
                if _sibling_chapter != _body_chapter:
                    resolved_body_chapter = _sibling_chapter
        if (
            _body_chapter is not None
            and all(str(op.target_special or "").strip() == "otsikko" for op in effective_group_ops)
        ):
            resolved_body_chapter = _retarget_heading_insert_body_chapter_from_close_live_sibling(
                muutos_tree=muutos_tree,
                section_norm=target_norm,
                body_chapter=_body_chapter,
                master=master,
            )
        if resolved_body_chapter is not None and resolved_body_chapter != (target_chapter or ""):
            _apply_body_chapter_correction = False
            if (
                _body_chapter is not None
                and resolved_body_chapter != _body_chapter
                and master.find("chapter", resolved_body_chapter) is not None
            ):
                _apply_body_chapter_correction = True
            elif not target_chapter:
                # No chapter scope: route to body chapter when it exists in master
                _apply_body_chapter_correction = master.find("chapter", resolved_body_chapter) is not None
            elif carry_forward_scoped:
                # Carry-forward scope: only override when body chapter is a
                # letter-suffix sub-chapter of the inferred chapter (guards against
                # spurious remaps for sections placed after the last pseudo-marker).
                _apply_body_chapter_correction = (
                    re.fullmatch(rf"{re.escape(target_chapter)}[a-z]", resolved_body_chapter, re.I) is not None
                )
            if _apply_body_chapter_correction:
                logger.debug(
                    "  body-chapter correction: %s chapter %s → %s",
                    target_norm, target_chapter, resolved_body_chapter,
                )
                effective_target_chapter = resolved_body_chapter
                effective_group_ops = [
                    dc_replace(
                        op,
                        target_chapter=resolved_body_chapter,
                        scope_confidence=normalize_scope_confidence(
                            projection_scope_confidence(
                                scope_confidence=op.scope_confidence,
                                scope_provenance_tags=op.scope_provenance_tags,
                                resolved_chapter=resolved_body_chapter,
                            ),
                            resolved_chapter=resolved_body_chapter,
                        ),
                        lo=_lo_with_path_update(op.lo, chapter=resolved_body_chapter) if op.lo is not None else op.lo,
                    )
                    if (
                        op.target_unit_kind == "section"
                        and _norm_num_token(op.target_section or "") == target_norm
                        and op.target_chapter == target_chapter
                    )
                    else op
                    for op in effective_group_ops
                ]
    # Body-chapter correction for REPLACE groups: structural chapter splits.
    # When a REPLACE op's body places the section under a letter-suffix
    # sub-chapter (e.g. §55 moved from "7" to "7c" by amendment 1996/473, or
    # §§39–41 moved from "4" to "4a" by amendment 2016/533), convert the
    # REPLACE to INSERT so the apply layer can MOVE the section from its
    # current chapter to the new sub-chapter.
    #
    # This handles structural reorganisations such as chapter 7 → 7a/7b/7c
    # where existing sections are both content-replaced AND structurally moved.
    # The MOVE is performed in apply_structure_ops._apply_whole_section_op when
    # a section already exists in the "parent" chapter.
    if (
        not _is_pure_insert_group
        and target_unit_kind == "section"
        and target_chapter
        and any(op.op_type == "REPLACE" for op in group_ops)
    ):
        _replace_body_chapter = _find_body_section_chapter(muutos_tree, target_norm)
        _replace_to_insert_trigger_evidence = tuple(
            evidence
            for evidence, present in (
                (
                    "pseudo_chapter_marker",
                    _replace_body_chapter is not None
                    and _body_has_pseudo_chapter_marker(muutos_tree, _replace_body_chapter),
                ),
                (
                    "real_inserted_chapter",
                    _replace_body_chapter is not None
                    and master.find("chapter", _replace_body_chapter) is None
                    and _body_has_real_chapter_container(muutos_tree, _replace_body_chapter),
                ),
                (
                    "inserted_chapter_op",
                    _replace_body_chapter is not None and _replace_body_chapter in inserted_chapter_labels,
                ),
            )
            if present
        )
        if (
            _replace_body_chapter is not None
            and _replace_body_chapter != target_chapter
            and re.fullmatch(rf"{re.escape(target_chapter)}[a-z]+", _replace_body_chapter, re.I) is not None
            # Only apply for genuine chapter-split restructuring. Two known
            # source shapes qualify:
            # 1. a pseudo-chapter-marker section (<section><num>X luku</num>)
            # 2. a real <chapter> container for a brand-new subchapter
            # 3. an explicit inserted chapter op targeting the new chapter
            # This keeps ordinary amendments operating inside an existing
            # letter-suffix chapter from being rewritten as moves.
            and _replace_to_insert_trigger_evidence
        ):
            rule_id = "LOWER.BODY_CHAPTER_REPLACE_TO_INSERT_MOVE"
            replacement_ops = [
                op
                for op in effective_group_ops
                if (
                    op.target_unit_kind == "section"
                    and _norm_num_token(op.target_section or "") == target_norm
                    and op.target_chapter == target_chapter
                    and op.op_type == "REPLACE"
                )
            ]
            compile_findings += (
                Finding(
                    kind=rule_id,
                    role="observation",
                    stage="_compile_group",
                    detail={
                        "rule_id": rule_id,
                        "phase": "lowering",
                        "family": "action_family_recovery",
                        "reason": "body_chapter_suffix_restructure_requires_move_bridge",
                        "original_action": "REPLACE",
                        "lowered_action": "INSERT",
                        "target_unit_kind": target_unit_kind,
                        "target_norm": target_norm,
                        "target_chapter": target_chapter,
                        "target_part": target_part or "",
                        "body_chapter": _replace_body_chapter,
                        "trigger_evidence": _replace_to_insert_trigger_evidence,
                        "op_ids": tuple(str(op.op_id or "") for op in replacement_ops),
                        "blocking": True,
                        "strict_disposition": "block",
                        "quirks_disposition": "record",
                    },
                    source_statute=next(
                        (str(op.source_statute or "") for op in replacement_ops if op.source_statute),
                        "",
                    ),
                    blocking=False,
                ),
            )
            if (
                strict_profile is not None
                and not strict_profile.allows_context_dependent_anchor_resolution
            ):
                strict_failed_ops = [
                    FailedOp.from_scope(
                        amendment_id=str(op.source_statute or ""),
                        description=op.description(),
                        reason=(
                            "section REPLACE was lowered to INSERT+MOVE because the amendment body "
                            "placed the section under a new letter-suffix chapter"
                        ),
                        reason_code=rule_id,
                        target_section=op.target_section or target_norm,
                        target_unit_kind=op.target_unit_kind,
                        target_chapter=target_chapter,
                        target_part=target_part,
                    )
                    for op in replacement_ops
                ]
                compile_findings += tuple(
                    Finding(
                        kind="ELAB.REJECTED_OPERATION",
                        role="observation",
                        stage="_compile_group",
                        detail={
                            **failed.as_detail(),
                            "message": "operation rejected before apply",
                        },
                        source_statute=failed.amendment_id,
                        blocking=False,
                    )
                    for failed in strict_failed_ops
                )
                compile_findings += tuple(
                    Finding(
                        kind="ELAB.STRICT_REJECTED_OPERATION",
                        role="obligation",
                        stage="_compile_group",
                        detail={
                            **failed.as_detail(),
                            "message": "operation rejected before apply",
                        },
                        source_statute=failed.amendment_id,
                        blocking=True,
                    )
                    for failed in strict_failed_ops
                )
                return PhaseResult(
                    output=[],
                    findings=compile_findings,
                )
            logger.debug(
                "  body-chapter correction (REPLACE→INSERT+MOVE): %s chapter %s → %s",
                target_norm, target_chapter, _replace_body_chapter,
            )
            effective_target_chapter = _replace_body_chapter
            # Also update surface_target_chapter so _build_group_surface looks for
            # the section in the correct virtual chapter segment of the amendment body
            # (e.g. §55 is in the "7c" pseudo-chapter segment inside chapter "7",
            # so surface_target_chapter="7" would miss it).
            surface_target_chapter = _replace_body_chapter
            effective_group_ops = [
                    dc_replace(
                        op,
                        op_type="INSERT",
                        target_chapter=_replace_body_chapter,
                        body_chapter_move_from=target_chapter,
                        # Clear target_special so otsikko-only REPLACE ops become whole-section
                        # INSERTs; the apply layer's pseudo-chapter MOVE needs a section-level
                        # op to fire (FacetTarget heading inserts are silently skipped there).
                        target_special=None,
                        scope_confidence=normalize_scope_confidence(
                            projection_scope_confidence(
                                scope_confidence=op.scope_confidence,
                                scope_provenance_tags=op.scope_provenance_tags,
                                resolved_chapter=_replace_body_chapter,
                            ),
                            resolved_chapter=_replace_body_chapter,
                        ),
                        lo=(
                        dc_replace(
                            (_tmp_lo := _lo_with_path_update(op.lo, chapter=_replace_body_chapter)),
                            action=StructuralAction.INSERT,
                            # Clear the heading facet so the op is treated as a
                            # whole-section INSERT (not FacetTarget heading).
                            # Without this, effective_target_special reads
                            # lo.target.special=HEADING and builds FacetTarget
                            # which the apply layer's INSERT dispatch cannot handle.
                            target=dc_replace(_tmp_lo.target, special=None),
                        )
                        if op.lo is not None else op.lo
                    ),
                )
                if (
                    op.target_unit_kind == "section"
                    and _norm_num_token(op.target_section or "") == target_norm
                    and op.target_chapter == target_chapter
                    and op.op_type == "REPLACE"
                )
                else op
                for op in effective_group_ops
            ]

    if target_unit_kind == "section" and (target_chapter or target_part) and not _is_pure_insert_group:
        scoped_path = master.find_section_path(target_norm, target_chapter, target_part)
        if scoped_path is None and target_norm not in master.duplicate_section_labels:
            # If the amendment body already carries the payload under the scoped
            # target chapter, preserve that scope. This covers real section-move
            # groups like "29 e §, joka samalla siirretään 5 b lukuun" where the
            # live tree still resolves the section under the old chapter.
            if target_chapter:
                source_body_chapter = _source_body_chapter_for_scoped_section_target(
                    muutos_tree=muutos_tree,
                    target_norm=target_norm,
                    target_chapter=target_chapter,
                    target_part=target_part,
                )
                if source_body_chapter == target_chapter:
                    scoped_path = ()
        retarget_scope_source = (
            _allow_unscoped_live_section_retarget(group_ops)
            if scoped_path is None and target_norm not in master.duplicate_section_labels
            else None
        )
        sibling_consensus_live_scope: tuple[str | None, str] | None = None
        if scoped_path is None and target_norm in master.duplicate_section_labels:
            body_scope = _source_body_scope_for_section_target(
                muutos_tree=muutos_tree,
                target_norm=target_norm,
            )
            if body_scope is not None:
                body_part, body_chapter = body_scope
                sibling_consensus_live_scope = _retarget_duplicate_body_section_scope_from_close_live_siblings(
                    muutos_tree=muutos_tree,
                    section_norm=target_norm,
                    body_chapter=body_chapter or "",
                    body_part=body_part,
                    master=master,
                )
                if sibling_consensus_live_scope is not None:
                    retarget_scope_source = "close_live_sibling_consensus"
        if retarget_scope_source is not None:
            body_scope = _source_body_scope_for_section_target(
                muutos_tree=muutos_tree,
                target_norm=target_norm,
            )
            body_part = None
            body_chapter = None
            live_path = None
            if body_scope is not None:
                body_part, body_chapter = body_scope
                if sibling_consensus_live_scope is not None:
                    live_part_hint, live_chapter_hint = sibling_consensus_live_scope
                    live_path = master.find_section_path(target_norm, live_chapter_hint, live_part_hint)
                else:
                    live_path = master.find_section_path(target_norm, body_chapter, body_part)
            if live_path is None and sibling_consensus_live_scope is None:
                live_path = master.find_section_path(target_norm, None, target_part)
            if (
                live_path is None
                and sibling_consensus_live_scope is None
                and retarget_scope_source == "explicit_chunk"
            ):
                # Some later reparenting families leave the amendment body and
                # explicit johtolause chunk on the stale old chapter/part, even
                # though the live statute has already moved the section to one
                # unique new path. Keep this bounded to explicit-chunk groups
                # and the existing duplicate-label guard.
                live_path = master.find_section_path(target_norm, None, None)
            if live_path is not None:
                live_part = next((label for kind, label in live_path if kind == "part"), None)
                live_chapter = next((label for kind, label in live_path if kind == "chapter"), None)
                if live_chapter and (live_chapter != target_chapter or live_part != target_part):
                    retarget_detail = {
                        "target_unit_kind": target_unit_kind,
                        "target_norm": target_norm,
                        "target_chapter": target_chapter or "",
                        "target_part": target_part or "",
                        "body_part": body_part or "",
                        "body_chapter": body_chapter or "",
                        "resolved_live_part": live_part or "",
                        "resolved_live_chapter": live_chapter,
                        "scope_source": retarget_scope_source,
                    }
                    compile_findings += (
                        Finding(
                            kind="LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET",
                            role="observation",
                            stage="_compile_group",
                            detail=retarget_detail,
                            source_statute=next(
                                (str(op.source_statute or "") for op in effective_group_ops if op.source_statute),
                                "",
                            ),
                            blocking=False,
                        ),
                    )
                    if (
                        strict_profile is not None
                        and not strict_profile.allows_context_dependent_anchor_resolution
                    ):
                        strict_failed_ops = [
                            FailedOp.from_scope(
                                amendment_id=str(op.source_statute or ""),
                                description=op.description(),
                                reason=(
                                    "scoped section target rebounded to a body-backed unique live "
                                    "section path outside explicit source scope"
                                ),
                                reason_code="LOWER.CARRY_FORWARD_LIVE_SECTION_RETARGET",
                                target_section=op.target_section or target_norm,
                                target_unit_kind=op.target_unit_kind,
                                target_chapter=target_chapter,
                            )
                            for op in effective_group_ops
                            if (
                                op.target_unit_kind == "section"
                                and _norm_num_token(op.target_section or "") == target_norm
                                and op.target_chapter == target_chapter
                            )
                        ]
                        compile_findings += tuple(
                            Finding(
                                kind="ELAB.REJECTED_OPERATION",
                                role="observation",
                                stage="_compile_group",
                                detail={
                                    **failed.as_detail(),
                                    "message": "operation rejected before apply",
                                },
                                source_statute=failed.amendment_id,
                                blocking=False,
                            )
                            for failed in strict_failed_ops
                        )
                        compile_findings += tuple(
                            Finding(
                                kind="ELAB.STRICT_REJECTED_OPERATION",
                                role="obligation",
                                stage="_compile_group",
                                detail={
                                    **failed.as_detail(),
                                    "message": "operation rejected before apply",
                                },
                                source_statute=failed.amendment_id,
                                blocking=True,
                            )
                            for failed in strict_failed_ops
                        )
                        # Strict profile blocked the retarget — return early with
                        # empty output.  The retarget findings are already in
                        # compile_findings; no Stage 1/2/3 output should be emitted.
                        return PhaseResult(
                            output=[],
                            findings=compile_findings,
                        )
                    else:
                        stale_part = target_part
                        effective_target_chapter = live_chapter
                        effective_target_part = live_part
                        if body_scope is not None:
                            surface_target_part = body_part
                            surface_target_chapter = body_chapter
                        effective_group_ops = [
                            dc_replace(
                                op,
                                target_part=live_part,
                                target_chapter=live_chapter,
                                scope_confidence=(
                                    ScopeConfidence(
                                        tag="body_container_membership_rewrite",
                                        source="explicit_scope_rewrite",
                                        confidence="rewritten",
                                        resolved_chapter=live_chapter,
                                    )
                                    if retarget_scope_source == "explicit_chunk"
                                    else normalize_scope_confidence(
                                        projection_scope_confidence(
                                            scope_confidence=op.scope_confidence,
                                            scope_provenance_tags=op.scope_provenance_tags,
                                            resolved_chapter=live_chapter,
                                        ),
                                        resolved_chapter=live_chapter,
                                    )
                                ),
                                lo=(
                                    dc_replace(
                                        _lo_with_path_update(op.lo, part=live_part, chapter=live_chapter),
                                        provenance_tags=tuple(
                                            _lo_with_path_update(
                                                op.lo,
                                                part=live_part,
                                                chapter=live_chapter,
                                            ).provenance_tags
                                        )
                                        + tuple(
                                            tag
                                            for tag in (
                                                f"body_part_retargeted_from:{stale_part}" if stale_part else "",
                                                f"body_chapter_retargeted_from:{target_chapter}" if target_chapter else "",
                                            )
                                            if tag
                                        ),
                                    )
                                    if op.lo is not None
                                    else (
                                        _lo_with_path_update(op.lo, part=live_part, chapter=live_chapter)
                                        if op.lo is not None
                                        else op.lo
                                    )
                                ),
                            )
                            if (
                                op.target_unit_kind == "section"
                                and _norm_num_token(op.target_section or "") == target_norm
                                and op.target_chapter == target_chapter
                            )
                            else op
                            for op in effective_group_ops
                        ]

    # ── Snapshot boundary ────────────────────────────────────────────────
    # Build typed snapshots ONCE before any elaboration.  After this point,
    # no raw master access occurs — Stage 2 builds PayloadElaborationContext
    # from these snapshots, Stage 3 uses only target_ctx.
    lookups = precomputed_lookups if precomputed_lookups is not None else snapshot_replay_lookups(cast(Any, master))
    target_ctx = snapshot_target_context(
        cast(Any, master),
        target_unit_kind,
        target_norm,
        effective_target_chapter,
        lookups,
        target_part=effective_target_part,
    )

    # ── Stage 1: build_group_surface (pure of live state) ────────────────
    surface_result = _build_group_surface(
        effective_group_ops,
        muutos_tree,
        target_unit_kind,
        target_norm,
        surface_target_chapter,
        surface_target_part,
    )

    # ── Stage 2: elaborate_group (live-dependent, typed snapshots only) ─────
    elab_result = _elaborate_group(
        target_ctx,
        lookups,
        surface_result.output,
        effective_group_ops,
        standalone_section_targets,
        foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
        target_part=effective_target_part,
        muutos_tree=muutos_tree,
        johto=johto,
        profile=profile,
        strict_profile=strict_profile,
    )
    elaborated = elab_result.output
    if elaborated.was_filtered or not elaborated.group_ops:
        # Group filtered out — return early with accumulated signals, empty ops.
        return PhaseResult(
            output=[],
            findings=surface_result.findings() + elab_result.findings() + compile_findings,
        )

    # ── Stage 3: lower_group (pure of live state, reads target_ctx only) ──
    lower_result = _lower_group(target_ctx, elaborated, compiled_ops_out, master, lookups)

    return PhaseResult(
        output=lower_result.output,
        findings=surface_result.findings() + elab_result.findings() + lower_result.findings() + compile_findings,
    )


def _emit_granular_subsection_timeline_ops(
    state: "ReplayState",
    group_rops: List[ResolvedOp],
    lo_ops_out: List[_LegalOperation],
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
    base_ir: Optional[IRNode],
    path_hint: tuple[tuple[str, str], ...] | None = None,
) -> bool:
    """Emit subsection-addressed timeline ops for eligible pure moment-level groups.

    We only do this when the normal section-snapshot export would otherwise
    inherit a live temporary section expiry from an earlier snapshot. Without
    that guard, subsection-only export can lose older stable sibling moments
    that currently still depend on section snapshots.
    """
    if not group_rops:
        return False
    if len(group_rops) != 1:
        return False

    first = group_rops[0]
    first_unit_kind, first_target_norm, first_target_chapter, first_target_part = first.resolved_group_key
    if first_unit_kind != "section":
        return False
    if base_ir is None or _tops.find(base_ir, "section", first_target_norm) is None:
        return False

    for rop in group_rops:
        if (
            rop.resolved_group_key[0] != "section"
            or not rop.targets_subsection_only()
            or not rop.is_replace_action
        ):
            return False
        if not rop.has_assigned_subsection_payload():
            return False

    op_source = _snapshot_op_source(
        group_rops,
        amendment_id,
        source_title,
        amendment_issue_date,
        amendment_effective_date,
    )
    sec_path = _valid_target_group_path_hint(
        state,
        first_unit_kind,
        first_target_norm,
        first_target_chapter,
        first_target_part,
        path_hint,
    )
    if sec_path is None:
        sec_path = state.find_section_path(first_target_norm, first_target_chapter, first_target_part)
    if sec_path is None:
        return False

    tl_sec_path = tuple((k, v) for k, v in sec_path if v)
    if not tl_sec_path:
        return False
    if op_source.expires:
        return False

    effective_iso = amendment_effective_date.isoformat() if amendment_effective_date else ""
    prior_section_version = None
    for lo in reversed(lo_ops_out):
        if lo.target.path == tl_sec_path:
            prior_section_version = lo
            break
    if prior_section_version is None:
        return False
    prior_expires = (prior_section_version.source.expires if prior_section_version.source else "") or ""
    if not prior_expires or (effective_iso and prior_expires <= effective_iso):
        return False

    for seq, rop in enumerate(group_rops, start=1):
        payload: Optional[IRNode]
        action = StructuralAction.REPLACE
        amend_sub = rop.resolved_amend_sub_ir()
        assert amend_sub is not None
        target_subsection_label = rop.resolved_target_subsection_label
        assert target_subsection_label is not None
        target_label = str(target_subsection_label)
        payload = amend_sub if amend_sub.label == target_label else _relabel_subsection_ir(amend_sub, target_label)

        lo_ops_out.append(
            _LegalOperation(
                op_id=f"subsection_{amendment_id}_{first_target_norm}_{target_label}_{seq}",
                sequence=seq,
                action=action,
                target=LegalAddress(path=tl_sec_path + (("subsection", target_label),)),
                payload=payload,
                source=op_source,
                group_id=f"finland-johto:{amendment_id}",
            )
        )
    return True


def compile_amendment_ops(
    master: "ReplayState",
    ops: List[AmendmentOp],
    muutos_tree: etree._Element,
    johto: str,
    replay_mode: Literal["finlex_oracle", "legal_pit"],
    compiled_ops_out: Optional[List[Dict[str, object]]] = None,
    strict_profile: Optional[StrictProfile] = None,
    *,
    source_ref: str = "",
    source_title: str = "",
    target_statute: str = "",
) -> "PhaseResult":
    """Compile grouped amendment ops into resolved ops ready for application.

    Groups ops by target (section/chapter), then delegates each group to
    ``_compile_group``.  Returns a PhaseResult where:
    - ``output``         — ``List[ResolvedOp]`` in apply order
    - ``finding_ledger`` — source pathologies, elaboration observations,
                           sparse slot bindings, and strictness findings;
                           wrapper observations/obligations remain
                           compatibility projections over this ledger

    Design note: extraction and application cannot be cleanly split into two
    independent passes because payload resolution (``_pre_resolve_omissions``)
    reads the current master state.  ``_compile_group`` documents this coupling
    explicitly.
    """
    from lawvm.core.phase_result import PhaseResult as _PR
    from lawvm.core.effect_lowering import (
        lower_effect_intents_to_temporal_events as _lower_effect_intents_to_temporal_events,
    )
    from lawvm.finland.effect_lowering import (
        UnsupportedMetaClause as _UnsupportedMetaClause,
        lower_johto_effects as _lower_johto_effects,
    )

    profile = get_replay_profile(replay_mode)
    source_title = source_title or _tree_title(muutos_tree)
    amendment_issue_date = _statute_issue_date(muutos_tree)
    amendment_effective_date = _amendment_effective_date(muutos_tree)
    section_groups = _coalesce_same_target_mixed_scope_section_groups(
        _group_ops_by_target(ops),
        master=master,
        muutos_tree=muutos_tree,
    )
    inserted_chapter_labels = {
        _norm_num_token(op.target_section or "")
        for op in ops
        if op.target_unit_kind == "chapter" and op.op_type == "INSERT" and op.target_section
    }
    resolved: List[ResolvedOp] = []
    all_findings: list[Finding] = []

    # Pre-compute replay lookups once — reused across all groups within this
    # amendment since the master IR is not mutated between groups.
    _precomputed_lookups = snapshot_replay_lookups(cast(Any, master))

    for (target_unit_kind, target_norm, target_chapter, target_part), group_ops in section_groups.items():
        target_unit_kind_value = cast(TargetUnitKind, target_unit_kind.value)
        standalone_section_targets = _group_shadow_pruning_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_part=target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        foreign_scoped_standalone_section_targets = _group_shadow_pruning_foreign_scoped_section_targets(
            ops,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_part=target_part,
            duplicate_section_labels=frozenset(getattr(master, "duplicate_section_labels", ())),
        )
        group_result = _compile_group(
            master=master,
            target_unit_kind=target_unit_kind_value,
            target_norm=target_norm,
            target_chapter=target_chapter,
            target_part=target_part,
            group_ops=group_ops,
            standalone_section_targets=standalone_section_targets,
            foreign_scoped_standalone_section_targets=foreign_scoped_standalone_section_targets,
            inserted_chapter_labels=inserted_chapter_labels,
            muutos_tree=muutos_tree,
            johto=johto,
            profile=profile,
            compiled_ops_out=compiled_ops_out,
            strict_profile=strict_profile,
            precomputed_lookups=_precomputed_lookups,
        )
        resolved.extend(group_result.output)
        all_findings.extend(group_result.findings())

    # ── EffectIntent lowering (amendment-level, once per johtolause) ─────────
    # Extract and lower temporal/conditional clauses from the johtolause text.
    _lowered_temporal_events: tuple = ()
    _activation_rules: list["ActivationRule"] = []
    if johto:
        _unsupported_meta_clauses: list[_UnsupportedMetaClause] = []
        _lowered_effect_intents = tuple(
            _lower_johto_effects(
                johto,
                unsupported_out=_unsupported_meta_clauses,
            )
        )
        all_findings.extend(
            Finding(
                kind=record.rule_id,
                role="observation",
                stage=record.phase,
                detail=record.as_detail(),
                source_statute=source_ref,
                blocking=record.blocking,
            )
            for record in _unsupported_meta_clauses
        )
        # The lowering bridge now carries OperationSource provenance on each
        # TemporalEvent via source_ref/source_title/source_issue_date/
        # source_effective_date; keep group_id as the batch key for now.
        _lowered_temporal_events = tuple(
            _lower_effect_intents_to_temporal_events(
                _lowered_effect_intents,
                source_ref=source_ref,
                source_title=source_title,
                source_issue_date=amendment_issue_date,
                source_effective_date=amendment_effective_date,
                group_id_prefix=f"finland-johto:{source_ref or 'unknown'}",
                target_statute=target_statute,
            )
        )

        # ── Typed ActivationRule derivation (additive alongside legacy) ───────
        # Extract SurfaceMetaClause objects from the johto text and derive
        # typed ActivationRules.  These coexist with the existing EffectIntent
        # and TemporalEvent pipelines.
        from lawvm.finland.johtolause.meta_parse import (  # noqa: PLC0415
            extract_meta_surface_clauses as _extract_meta_surface_clauses,
        )
        from lawvm.finland.temporal_lowering import (  # noqa: PLC0415
            activation_rules_from_meta_clauses_with_findings as _activation_rules_from_meta_clauses,
            classify_contingent as _classify_contingent,
            default_activation_rule as _default_activation_rule,
        )

        _meta_clauses = _extract_meta_surface_clauses(johto)
        _activation_lowering = _activation_rules_from_meta_clauses(_meta_clauses)
        all_findings.extend(_activation_lowering.findings)
        _activation_rules = list(_activation_lowering.activation_rules)
        if not _activation_rules:
            _activation_rules = [_default_activation_rule()]

        # Bridge: annotate compiled_ops_out with the derived activation rule(s)
        # so downstream consumers can read typed activation info alongside
        # legacy fields.
        if compiled_ops_out is not None and _activation_rules:
            _rule = _activation_rules[0]
            for _cop_dict in compiled_ops_out:
                if "activation_rule" not in _cop_dict:
                    _cop_dict["activation_rule"] = {
                        "kind": _rule.kind,
                        "effective_date": _rule.effective_date,
                        "condition_ref": _rule.condition_ref,
                    }
                    _cop_dict["is_contingent"] = _classify_contingent(_rule)

    return _PR(
        output=resolved,
        findings=tuple(all_findings),
        temporal_events=_lowered_temporal_events,
    )


def _group_shadow_pruning_section_targets(
    ops: List[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return standalone section labels that may shadow a container payload."""
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        if section_label in duplicate_section_labels:
            continue
        if op.target_part == target_part and op.target_chapter == target_norm:
            continue
        out.add(section_label)
    return out


def _group_shadow_pruning_foreign_scoped_section_targets(
    ops: List[AmendmentOp],
    *,
    target_unit_kind: TargetUnitKind,
    target_norm: str,
    target_part: str | None,
    duplicate_section_labels: frozenset[str],
) -> set[str]:
    """Return shadowable section labels with explicit foreign container scope.

    This narrows container payload pruning for live heading-only container
    replaces: only prune carried "new" sections when the same amendment also
    owns the section as a standalone target in another explicit scope.
    """
    if target_unit_kind not in {"chapter", "part"}:
        return set()

    out: set[str] = set()
    for op in ops:
        section_label = _norm_num_token(op.target_section or "")
        if op.target_unit_kind != "section" or not section_label:
            continue
        # Only INSERT ops can shadow carry-forward content — REPLACE ops act on
        # already-existing sections and must not suppress container payload.
        if op.op_type != "INSERT":
            continue
        # Carry-forward INSERTs have inferred/stale chapter scope; they should
        # not shadow container payload since their chapter attribution is
        # unreliable.
        if (
            op.scope_confidence is not None
            and op.scope_confidence.source == "carry_forward"
        ):
            continue
        if section_label in duplicate_section_labels:
            continue
        if op.target_part == target_part and op.target_chapter == target_norm:
            continue
        if op.target_chapter is None and target_unit_kind == "chapter":
            continue
        if op.target_part is None and target_unit_kind == "part":
            continue
        out.add(section_label)
    return out


# normalize_and_compile_ops moved to lawvm.finland.frontend_compile; re-exported above.

from lawvm.finland.frontend_observations import (  # noqa: E402, F401  (moved here; re-exported for backward compat)
    _duplicate_frontend_target_observations,
    _semantic_collapse_move_or_renumber_observations,
    _scope_anchor_dependence_observations,
)


def _stabilize_same_parent_relabel_order(resolved: List[ResolvedOp]) -> List[ResolvedOp]:
    """Reorder same-parent RELABEL chains so consumers run before producers.

    This covers chapter relabel chains like ``10 luku -> 11 luku`` / ``11 luku -> 12 luku``
    and same-parent section relabel chains like ``9 § -> 10 §`` / ``10 § -> 11 §`` /
    ``11 § -> 12 §``. Applied naively in textual order, the first relabel can create the
    label that the second relabel then mistakenly consumes from the just-renamed node.

    We group relabels by (unit_kind, parent_path) so only genuine same-parent chains are
    reordered. Non-relabel ops remain in their original positions.
    """
    from lawvm.core.canonical_intent import Relabel

    def _relabel_key(rop: ResolvedOp) -> tuple[str, tuple[tuple[str, str], ...]] | None:
        if rop.resolved_action_type != "RENUMBER":
            return None
        intent = rop.intent
        if not isinstance(intent, Relabel):
            return None
        if intent.destination is None:
            return None
        unit_kind = rop.target_unit_kind
        if unit_kind not in {"chapter", "section"}:
            return None
        source_parent = intent.source.address.path[:-1]
        dest_parent = intent.destination.address.path[:-1]
        if source_parent != dest_parent:
            return None
        return unit_kind, source_parent

    def _relabel_dest(rop: ResolvedOp) -> Optional[str]:
        intent = rop.intent
        if not isinstance(intent, Relabel) or intent.destination is None:
            return None
        return intent.destination.address.leaf_label()

    keyed_positions: dict[tuple[str, tuple[tuple[str, str], ...]], list[int]] = {}
    keyed_ops: dict[tuple[str, tuple[tuple[str, str], ...]], list[ResolvedOp]] = {}
    keyed_dests: dict[tuple[str, tuple[tuple[str, str], ...]], list[str]] = {}
    for idx, rop in enumerate(resolved):
        key = _relabel_key(rop)
        dest = _relabel_dest(rop)
        if key is None or dest is None:
            continue
        keyed_positions.setdefault(key, []).append(idx)
        keyed_ops.setdefault(key, []).append(rop)
        keyed_dests.setdefault(key, []).append(dest)

    result = list(resolved)
    for key, relabel_ops in keyed_ops.items():
        if len(relabel_ops) < 2:
            continue
        relabel_positions = keyed_positions[key]
        relabel_dests = keyed_dests[key]

        source_to_rel_idx: dict[str, int] = {}
        for rel_idx, rop in enumerate(relabel_ops):
            source_to_rel_idx[rop.target_norm] = rel_idx

        n_rel = len(relabel_ops)
        before: list[set[int]] = [set() for _ in range(n_rel)]
        has_chain = False
        for rel_idx, dest in enumerate(relabel_dests):
            if dest in source_to_rel_idx:
                consumer_idx = source_to_rel_idx[dest]
                if consumer_idx != rel_idx:
                    before[rel_idx].add(consumer_idx)
                    has_chain = True

        if not has_chain:
            continue

        in_degree = [len(b) for b in before]
        unblocks: list[list[int]] = [[] for _ in range(n_rel)]
        for j in range(n_rel):
            for k in before[j]:
                unblocks[k].append(j)

        queue = [j for j in range(n_rel) if in_degree[j] == 0]
        topo_order: list[int] = []
        while queue:
            cur = queue.pop(0)
            topo_order.append(cur)
            for nxt in unblocks[cur]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(topo_order) != n_rel:
            continue

        for pos_in_list, rel_idx in zip(relabel_positions, topo_order, strict=True):
            result[pos_in_list] = relabel_ops[rel_idx]
    return result


def _stabilize_chapter_relabel_order(resolved: List[ResolvedOp]) -> List[ResolvedOp]:
    """Backward-compat alias for the broader same-parent relabel ordering helper."""
    return _stabilize_same_parent_relabel_order(resolved)


def _build_standalone_section_targets(
    ops: list[AmendmentOp],
) -> frozenset[tuple[str | None, str | None, str]]:
    """Collect standalone whole-section targets for container ownership guards.

    Container payload pruning and apply-time chapter-child stripping should only
    react to whole-section claims. Descendant-only section ops like ``1 § 5
    mom`` do not own the ``1 §`` shell and must not cause the parent chapter
    payload to drop that child section.
    """
    standalone_targets: set[tuple[str | None, str | None, str]] = set()
    for op in ops:
        if op.target_unit_kind != "section" or not op.target_section:
            continue
        if op.target_paragraph is not None or op.target_item or op.target_special:
            continue
        norm_label = _norm_num_token(op.target_section)
        standalone_targets.add((op.target_part, op.target_chapter, norm_label))
        if op.lo is None:
            continue
        for tag in op.lo.provenance_tags:
            if not tag.startswith("body_chapter_retargeted_from:"):
                continue
            orig_chapter = tag.split(":", 1)[1]
            standalone_targets.add((op.target_part, orig_chapter, norm_label))
    return frozenset(standalone_targets)


def _restructure_plan_owned_renumber_signatures(
    plan: "StructuralTransformPlan",
) -> set[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]]:
    """Return exact relabel signatures owned by one active restructure plan.

    StructuralTransformPlan execution is only authoritative for the relabels it
    actually encodes. Descendant renumbers produced by the ordinary lowering
    path must continue through typed/apply dispatch even when the same
    amendment also has a section/chapter relabel plan.
    """
    owned: set[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]] = set()
    for op in plan.ops:
        if op.kind != TransformOpKind.RELABEL or op.destination is None:
            continue
        target_path = tuple(_parse_address(op.target))
        dest_path = tuple(_parse_address(op.destination))
        if not target_path or not dest_path:
            continue
        owned.add((target_path, dest_path))
    return owned


def _resolved_op_is_owned_by_restructure_plan(
    rop: ResolvedOp,
    owned_relabels: set[tuple[tuple[tuple[str, str], ...], tuple[tuple[str, str], ...]]],
) -> bool:
    """True when the active restructure plan already owns this renumber op."""
    from lawvm.core.canonical_intent import Relabel

    if rop.resolved_action_type != "RENUMBER":
        return False
    if not isinstance(rop.intent, Relabel):
        return False
    destination = rop.intent.destination
    if destination is None:
        return False

    if not owned_relabels:
        return False

    def _path_scope_label(
        path: tuple[tuple[str, str], ...],
        kind: str,
    ) -> str | None:
        for seg_kind, seg_label in path:
            if seg_kind == kind:
                return seg_label
        return None

    def _trim_leading_part_scope(
        path: tuple[tuple[str, str], ...],
    ) -> tuple[tuple[str, str], ...]:
        if path and path[0][0] == "part":
            return path[1:]
        return path

    def _matches_legacy_scope(
        owned_source: tuple[tuple[str, str], ...],
        owned_destination: tuple[tuple[str, str], ...],
    ) -> bool:
        if not owned_source or not owned_destination:
            return False
        source_leaf_kind, source_leaf_label = owned_source[-1]
        dest_leaf_kind, dest_leaf_label = owned_destination[-1]
        if rop.target_unit_kind != source_leaf_kind or source_leaf_kind != dest_leaf_kind:
            return False
        if _norm_num_token(rop.target_norm) != source_leaf_label:
            return False
        if source_leaf_kind == "section":
            owned_chapter = _path_scope_label(owned_source, "chapter")
            owned_part = _path_scope_label(owned_source, "part")
            rop_chapter = _norm_num_token(rop.resolved_target_scope_chapter_label or "") if rop.resolved_target_scope_chapter_label else None
            rop_part = _norm_num_token(rop.resolved_target_scope_part_label or "") if rop.resolved_target_scope_part_label else None
            if rop_chapter != owned_chapter:
                return False
            if owned_part is not None and rop_part != owned_part:
                return False
        elif source_leaf_kind == "chapter":
            owned_part = _path_scope_label(owned_source, "part")
            rop_part = _norm_num_token(rop.resolved_target_scope_part_label or "") if rop.resolved_target_scope_part_label else None
            if owned_part is not None and rop_part != owned_part:
                return False
        resolved_destination = rop.resolved_destination_address
        if resolved_destination is None:
            return False
        return _norm_num_token(resolved_destination.leaf_label()) == dest_leaf_label

    source_path = tuple(rop.intent.source.address.path)
    destination_path = tuple(destination.address.path)
    for owned_source, owned_destination in owned_relabels:
        if _matches_legacy_scope(owned_source, owned_destination):
            return True
        candidate_pairs = (
            (source_path, destination_path, owned_source, owned_destination),
            (
                _trim_leading_part_scope(source_path),
                _trim_leading_part_scope(destination_path),
                _trim_leading_part_scope(owned_source),
                _trim_leading_part_scope(owned_destination),
            ),
        )
        for cand_source, cand_destination, cand_owned_source, cand_owned_destination in candidate_pairs:
            if len(cand_source) < len(cand_owned_source) or len(cand_destination) < len(cand_owned_destination):
                continue
            if cand_source[-len(cand_owned_source):] != cand_owned_source:
                continue
            if cand_destination[-len(cand_owned_destination):] != cand_owned_destination:
                continue
            return True
    return False


def apply_ops_to_tree(
    state: "ReplayState",
    ctx: "StatuteContext",
    resolved: List[ResolvedOp],
    ops: List[AmendmentOp],
    muutos_tree: "etree._Element",
    johto: str,
    amendment_id: str,
    source_title: str,
    amendment_issue_date: Optional[dt.date],
    amendment_effective_date: Optional[dt.date],
    amendment_expiry_date: Optional[dt.date],
    replay_mode: Literal["finlex_oracle", "legal_pit"],
    lo_ops_out: Optional[List[_LegalOperation]],
    failed_ops_out: Optional[List[FailedOp]],
    source_pathologies_out: Optional[List[SourcePathology]],
    strict_profile: Optional[StrictProfile],
    _vts_ops_enrich_done: bool,
    future_repeals: Optional[Set[RepealTargetRef]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    migration_ledger: Optional[MigrationLedger] = None,
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None,
    observations_out: Optional[List[Dict[str, object]]] = None,
    findings_out: Optional[List[Finding]] = None,
) -> "ReplayState":
    """Step 6: Apply resolved operations to IR tree as a pure fold.

    Accepts immutable ``ctx`` and current ``state``.  Returns the updated
    ``ReplayState`` after applying all ops, uncovered-body recovery, and
    kumotaan heuristics.  The input ``state`` is never modified.

    ``ctx`` is used by ``apply_op`` to resolve base-IR queries
    (e.g. find_base_section for kumotaan placeholder decisions) and by the
    ``_apply_uncovered_*`` heuristics.
    """
    prev_group_key: Optional[Tuple[TargetUnitKind, str, Optional[str], Optional[str]]] = None
    group_rops: List[ResolvedOp] = []
    group_path_hint: tuple[tuple[str, str], ...] | None = None

    def _refresh_group_path_hint(
        target_unit_kind: TargetUnitKind,
        target_norm: str,
        target_chapter: Optional[str],
        target_part: Optional[str],
        path_hint: tuple[tuple[str, str], ...] | None,
        rop: Optional[ResolvedOp],
        migration_ledger: Optional[MigrationLedger],
    ) -> tuple[tuple[str, str], ...] | None:
        def _unique_global_section_path(label: str) -> tuple[tuple[str, str], ...] | None:
            idx = state.provision_index
            raw_path = _tops.find(state.ir, "section", label, label_index=idx)
            if raw_path is None:
                return None
            label_norm = normalized_label_key(label)
            if len(idx.get(("section", label_norm), [])) != 1:
                return None
            return raw_path

        valid_hint = _valid_target_group_path_hint(
            state,
            target_unit_kind,
            target_norm,
            target_chapter,
            target_part,
            path_hint,
        )
        if valid_hint is not None:
            return tuple(valid_hint)
        if rop is not None:
            dest_path = _resolved_destination_path_for_rop(rop)
            if dest_path is not None:
                dest_path_tuple = tuple(dest_path)
                if migration_ledger is not None:
                    migrated = migration_ledger.current_address_with_prefix_migrations(
                        LegalAddress(path=dest_path_tuple)
                    )
                    migrated_path = migrated.path
                    if _tops.resolve(state.ir, migrated_path) is not None:
                        return migrated_path
                if _tops.resolve(state.ir, dest_path_tuple) is not None:
                    return dest_path_tuple
        if target_unit_kind == "part":
            return state.find("part", target_norm)
        if target_unit_kind == "chapter":
            return state.find("chapter", target_norm)
        if target_unit_kind == "section":
            raw_path = state.find_section_path(target_norm, target_chapter, target_part)
            raw_path = _prefer_unique_substantive_section_path_over_placeholder(
                state,
                target_norm=target_norm,
                target_chapter=target_chapter,
                target_part=target_part,
                raw_path=raw_path,
            )
            if raw_path is None and target_chapter is None and target_part is None:
                raw_path = _unique_global_section_path(target_norm)
            if raw_path is None and target_chapter is not None and target_part is None:
                # Cross-chapter/root-level unique global fallback for non-INSERT ops.
                # Finnish amendments sometimes group sections under a chapter heading
                # that differs from where the section lives in the live statute
                # (e.g. root hcontainer level). Only applicable when no part scope is
                # specified — a part mismatch is an authoritative scoping signal and
                # must not be bypassed.
                _is_non_insert = rop is None or rop.resolved_action_type != "INSERT"
                if _is_non_insert:
                    raw_path = _unique_global_section_path(target_norm)
            if raw_path is not None and migration_ledger is not None:
                migrated = migration_ledger.current_address_with_prefix_migrations(
                    LegalAddress(path=tuple(raw_path))
                )
                migrated_path = migrated.path
                if _tops.resolve(state.ir, migrated_path) is not None:
                    return migrated_path
            return raw_path
        return None

    # Pre-compute standalone section targets as (chapter, label) tuples for
    # container dedup/retention guards. When a section op was retargeted away
    # from a stale body chapter to the unique live chapter, also record the
    # original body chapter as an alias so chapter REPLACE payloads do not keep
    # the stale child shell around.
    _standalone_section_targets = _build_standalone_section_targets(ops)
    base_ir = ctx.base_ir

    # Stabilize same-parent RELABEL order: reverse forward chains so consumers
    # run before producers. Prevents both chapter chains like "10→11 then 11→12"
    # and section chains like "9→10, 10→11, 11→12" from consuming a label
    # created by a just-applied earlier relabel.
    resolved = _stabilize_same_parent_relabel_order(resolved)

    active_restructure_plan = None
    if restructure_plans_out:
        for _rp in restructure_plans_out:
            if _rp.amendment_id == amendment_id and _rp.has_unexecuted_ops:
                active_restructure_plan = _rp
                break
    executed_restructure_plan_ids: set[str] = set()
    if active_restructure_plan is not None:
        # Restructure-plan ownership must be singular. When a relabel plan is
        # active for this amendment, the main resolved-op loop must not also
        # mutate the exact same relabel chain or emit stale old-address
        # snapshots. Descendant renumbers outside the plan stay on the ordinary
        # typed/apply path.
        owned_relabels = _restructure_plan_owned_renumber_signatures(active_restructure_plan)
        resolved = [
            rop
            for rop in resolved
            if not _resolved_op_is_owned_by_restructure_plan(rop, owned_relabels)
        ]
        # Execute the pre-seeded relabel plan before the ordinary resolved-op
        # fold. Large renumber waves like 2019/371 can move containers later in
        # the same amendment; if the plan waits until uncovered-body recovery,
        # its old-address section relabels chase a tree that has already moved.
        _migration_events_before = len(migration_ledger) if migration_ledger is not None else 0
        _new_ir, _exec_ops = execute_restructure_plan(
            active_restructure_plan,
            state.ir,
            migration_ledger=migration_ledger,
            effective_date=amendment_effective_date.isoformat() if amendment_effective_date else "",
        )
        _executed_labels = [e.note for e in _exec_ops if e.success]
        _skipped_labels = [e.note for e in _exec_ops if not e.success]
        if _executed_labels:
            state = state.with_ir(_new_ir)
            executed_restructure_plan_ids.add(active_restructure_plan.amendment_id)
            if migration_ledger is not None:
                _emit_restructure_plan_renumber_legal_operations(
                    lo_ops_out=lo_ops_out,
                    migration_events=migration_ledger.events[_migration_events_before:],
                    amendment_id=amendment_id,
                    source_title=source_title,
                    amendment_issue_date=amendment_issue_date,
                    amendment_effective_date=amendment_effective_date,
                )
            _replay_print(
                f"  [{amendment_id}] early restructure_plan executed: "
                f"{len(_executed_labels)} ops"
            )
        if _skipped_labels:
            logger.debug(
                "  [%s] early restructure_plan skipped ops: %s",
                amendment_id, _skipped_labels,
            )
            if findings_out is not None:
                for _exec_op in _exec_ops:
                    finding = relabel_skip_finding(_exec_op, source_statute=amendment_id)
                    if finding is not None:
                        findings_out.append(finding)
                    finding = relabel_skip_source_pathology_finding(
                        _exec_op,
                        source_statute=amendment_id,
                    )
                    if finding is not None:
                        findings_out.append(finding)
                    finding = move_skip_finding(_exec_op, source_statute=amendment_id)
                    if finding is not None:
                        findings_out.append(finding)
                    finding = deferred_plan_op_finding(_exec_op, source_statute=amendment_id)
                    if finding is not None:
                        findings_out.append(finding)

    # Pre-create chapters introduced by the amendment body before the main
    # apply loop. Section INSERT ops can target both real new chapters and
    # pseudo-marker chapters in the same amendment, and both need their
    # chapter shell to exist before the section-level apply path runs.
    # Not run for VTS (cross-statute body) amendments.
    _pre_real_chapter_refs: List[tuple[str, str]] = []
    _pre_pseudo_chapter_refs: List[tuple[str, str]] = []
    if not _vts_ops_enrich_done:
        _muutos_body_early = muutos_tree.find(".//{*}body")
        if _muutos_body_early is not None:
            def _has_scoped_chapter(_part_label: str, _chapter_label: str) -> bool:
                _part_path = state.find("part", _part_label) if _part_label else None
                if _part_path is None:
                    return state.find("chapter", _chapter_label) is not None if not _part_label else False
                _part_node = _tops.resolve(state.ir, _part_path)
                if _part_node is None:
                    return False
                return _tops.find(_part_node, "chapter", _chapter_label) is not None

            _early_required_real_chapters = {
                (
                    _norm_num_token(_rop.resolved_target_scope_part_label or "") if _rop.resolved_target_scope_part_label else "",
                    _rop.resolved_target_chapter_label,
                )
                for _rop in resolved
                if _rop.target_unit_kind == "section"
                and _rop.resolved_target_chapter_label
                and not _has_scoped_chapter(
                    _norm_num_token(_rop.resolved_target_scope_part_label or "") if _rop.resolved_target_scope_part_label else "",
                    _rop.resolved_target_chapter_label,
                )
            }
            state, _pre_real_chapter_refs = _pre_create_amendment_chapters(
                state,
                _muutos_body_early,
                amendment_id,
                required_labels=_early_required_real_chapters,
            )
            state, _pre_pseudo_chapter_refs = _pre_create_pseudo_marker_chapters(
                state, _muutos_body_early, amendment_id
            )

    # Snapshot chapter-to-part mapping before the main apply loop.
    # Used after the loop to detect chapters that moved to a genuinely NEW part,
    # so we can emit tombstone+insert LO ops that keep the materialized PIT
    # consistent.  Only genuine part-creation moves are captured; part relabels
    # (where the old part label disappears) are excluded.
    _ch_to_part_before: dict[str, str] = {}
    _parts_before: set[str] = set()
    if lo_ops_out is not None:
        _pp_snap = _tops.find_provisions_parent(state.ir)
        _pp_snap_node = _tops.resolve(state.ir, _pp_snap) if _pp_snap else state.ir
        if _pp_snap_node is not None:
            for _snap_part in _pp_snap_node.children:
                if _snap_part.kind is IRNodeKind.PART and _snap_part.label:
                    _parts_before.add(_snap_part.label)
                    for _snap_ch in _snap_part.children:
                        if _snap_ch.kind is IRNodeKind.CHAPTER and _snap_ch.label:
                            _ch_to_part_before[_snap_ch.label] = _snap_part.label

    for rop in resolved:
        group_key = rop.resolved_group_key
        if group_key != prev_group_key:
            # Emit snapshot for previous group (if any)
            if group_rops and lo_ops_out is not None:
                _r = group_rops[0]
                _r_target_unit_kind, _r_target_norm, _r_target_chapter, _r_target_part = _r.resolved_group_key
                if not _emit_granular_subsection_timeline_ops(
                    state,
                    group_rops,
                    lo_ops_out,
                    amendment_id,
                    source_title,
                    amendment_issue_date,
                    amendment_effective_date,
                    base_ir,
                    path_hint=group_path_hint,
                ):
                    _emit_section_snapshot(
                        state,
                        _r_target_unit_kind,
                        _r_target_norm,
                        _r_target_chapter,
                        _r_target_part,
                        group_rops,
                        lo_ops_out,
                        amendment_id,
                        source_title,
                        amendment_issue_date,
                        amendment_effective_date,
                        base_ir=base_ir,
                        path_hint=group_path_hint,
                        standalone_section_targets=_standalone_section_targets,
                    )
            group_rops = []
            prev_group_key = group_key
            group_path_hint = None
        # Apply
        if rop.replay_requires_apply_pass:
            try:
                state = apply_op(
                    state,
                    None,
                    ctx,
                    None,
                    replay_mode=replay_mode,
                    failed_ops_out=failed_ops_out,
                    source_pathologies_out=source_pathologies_out,
                    mutation_events_out=mutation_events_out,
                    findings_out=findings_out,
                    path_hint=group_path_hint,
                    rop=rop,
                    replay_history_ops=lo_ops_out,
                    standalone_section_targets=_standalone_section_targets,
                    migration_ledger=migration_ledger,
                    strict_profile=strict_profile,
                )
                rop_target_unit_kind, rop_target_norm, rop_target_chapter, rop_target_part = rop.resolved_group_key
                group_path_hint = _refresh_group_path_hint(
                    rop_target_unit_kind,
                    rop_target_norm,
                    rop_target_chapter,
                    rop_target_part,
                    group_path_hint,
                    rop,
                    migration_ledger,
                )
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception as e:
                logger.debug("  [%s] %s → ERROR", amendment_id, rop.description(), exc_info=True)
                _replay_print(f"  [{amendment_id}] {rop.description()} → ERROR: {e}")
        group_rops.append(rop)

    # Emit snapshot for the last group
    if group_rops and lo_ops_out is not None:
        _r = group_rops[0]
        _r_target_unit_kind, _r_target_norm, _r_target_chapter, _r_target_part = _r.resolved_group_key
        if not _emit_granular_subsection_timeline_ops(
            state,
            group_rops,
            lo_ops_out,
            amendment_id,
            source_title,
            amendment_issue_date,
            amendment_effective_date,
            base_ir,
            path_hint=group_path_hint,
        ):
            _emit_section_snapshot(
                state,
                _r_target_unit_kind,
                _r_target_norm,
                _r_target_chapter,
                _r_target_part,
                group_rops,
                lo_ops_out,
                amendment_id,
                source_title,
                amendment_issue_date,
                amendment_effective_date,
                base_ir=base_ir,
                path_hint=group_path_hint,
                migration_ledger=migration_ledger,
                standalone_section_targets=_standalone_section_targets,
            )

    if ops or lo_ops_out is not None:
        if lo_ops_out is not None:
            # Reuse already-computed dates (amendment_effective_date /
            # amendment_issue_date) to avoid a redundant parse.
            _uncov_src = OperationSource(
                statute_id=amendment_id,
                title=source_title,
                effective=amendment_effective_date.isoformat() if amendment_effective_date else "",
                enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
                expires=amendment_expiry_date.isoformat() if amendment_expiry_date else "",
            )
        else:
            _uncov_src = None
        # Heuristics #18-21: _recover_uncovered_body_ops (MVR: emits ResolvedOps,
        # no longer mutates the tree directly) —
        # gated by allows_uncovered_body_recovery.
        # Suppressed for vts_repeal ops: the amendment body belongs to a
        # different law; its sections must not be injected into the parent.
        _uncov_allowed = not _vts_ops_enrich_done and (
            strict_profile is None or strict_profile.allows_uncovered_body_recovery
        )
        if ops and _uncov_allowed:
            _new_chapter_refs = list(_pre_real_chapter_refs)
            _muutos_body_el = muutos_tree.find(".//{*}body")
            if _muutos_body_el is not None:
                # Step 1: capture any late-created real chapters (normally none
                # now that pre-creation runs before the apply loop) and keep the
                # full label set for LO emission / uncovered routing.
                state, _late_new_chapter_refs = _pre_create_amendment_chapters(
                    state, _muutos_body_el, amendment_id
                )
                _new_chapter_refs = list(
                    dict.fromkeys((*_pre_real_chapter_refs, *_late_new_chapter_refs))
                )
                # Emit chapter-level LegalOperations for newly created chapters so
                # that compile_timelines seeds a timeline entry for each new chapter.
                # Without this, materialize_pit cannot reconstruct the chapter in the
                # PIT output even when its section-level timeline entries exist
                # (the new chapter has no depth-1 or depth-2 timeline entry to anchor
                # the body overlay).
                if lo_ops_out is not None and _uncov_src is not None and _new_chapter_refs:
                    for _new_ch_part, _new_ch_label in _new_chapter_refs:
                        if _new_ch_part:
                            _part_path = state.find("part", _new_ch_part)
                            _part_node = _tops.resolve(state.ir, _part_path) if _part_path is not None else None
                            _local_ch_path = _tops.find(_part_node, "chapter", _new_ch_label) if _part_node is not None else None
                            _ch_path = _part_path + _local_ch_path if _part_path is not None and _local_ch_path is not None else None
                        else:
                            _ch_path = state.find("chapter", _new_ch_label)
                        _ch_node = _tops.resolve(state.ir, _ch_path) if _ch_path else None
                        if _ch_path is not None and _ch_node is not None:
                            _ch_tl_path = tuple(
                                (k, v) for k, v in _ch_path if v
                            )
                            lo_ops_out.append(
                                _LegalOperation(
                                    op_id=f"uncov_chapter_create_{_new_ch_part or 'root'}_{_new_ch_label}",
                                    sequence=0,
                                    action=StructuralAction.INSERT,
                                    target=LegalAddress(path=_ch_tl_path),
                                    payload=_ch_node,
                                    source=_uncov_src,
                                    group_id=f"finland-johto:{amendment_id}",
                                )
                            )
                            logger.debug(
                                "  [%s] uncovered chapter LO INSERT %s/%s (path=%s)",
                                amendment_id, _new_ch_part or "-", _new_ch_label, _ch_tl_path,
                            )
                # Emit chapter-level LOs for pseudo-marker chapters that were
                # pre-created before the PEG apply loop (so PEG INSERT ops for
                # sections like 53a → chapter 7a could land in the right place).
                if lo_ops_out is not None and _uncov_src is not None and _pre_pseudo_chapter_refs:
                    for _pch_part, _pch_label in _pre_pseudo_chapter_refs:
                        if _pch_part:
                            _part_path = state.find("part", _pch_part)
                            _part_node = _tops.resolve(state.ir, _part_path) if _part_path is not None else None
                            _local_ch_path = _tops.find(_part_node, "chapter", _pch_label) if _part_node is not None else None
                            _pch_path = _part_path + _local_ch_path if _part_path is not None and _local_ch_path is not None else None
                        else:
                            _pch_path = state.find("chapter", _pch_label)
                        _pch_node = _tops.resolve(state.ir, _pch_path) if _pch_path else None
                        if _pch_path is not None and _pch_node is not None:
                            _pch_tl_path = tuple((k, v) for k, v in _pch_path if v)
                            lo_ops_out.append(
                                _LegalOperation(
                                    op_id=f"pseudo_chapter_create_{_pch_part or 'root'}_{_pch_label}",
                                    sequence=0,
                                    action=StructuralAction.INSERT,
                                    target=LegalAddress(path=_pch_tl_path),
                                    payload=_pch_node,
                                    source=_uncov_src,
                                    group_id=f"finland-johto:{amendment_id}",
                                )
                            )
                            logger.debug(
                                "  [%s] pseudo-chapter LO INSERT %s/%s (path=%s)",
                                amendment_id, _pch_part or "-", _pch_label, _pch_tl_path,
                            )
            _new_chapter_labels = [label for _, label in _new_chapter_refs]
            _pre_pseudo_chapter_labels = [label for _, label in _pre_pseudo_chapter_refs]
            # Step 2: collect section-level ResolvedOps (no direct tree_ops).
            _uncov_rops = _recover_uncovered_body_ops(
                state,
                ctx,
                ops,
                muutos_tree,
                amendment_id,
                future_repeals=future_repeals,
                op_source=_uncov_src,
                new_chapter_labels=set(_new_chapter_labels) | set(_pre_pseudo_chapter_labels),
                failed_ops_out=failed_ops_out,
                restructure_plans_out=restructure_plans_out,
                observations_out=observations_out,
                findings_out=findings_out,
            )
            # Step 2b: execute MOVE/RELABEL ops from any restructure plan
            # built during _recover_uncovered_body_ops.  These structural
            # transforms must be applied before leaf-level ops so that the
            # tree scaffold is correct when sections are inserted/replaced.
            if restructure_plans_out:
                for _rp in restructure_plans_out:
                    if (
                        _rp.amendment_id == amendment_id
                        and _rp.has_unexecuted_ops
                        and _rp.amendment_id not in executed_restructure_plan_ids
                    ):
                        _migration_events_before = len(migration_ledger) if migration_ledger is not None else 0
                        _new_ir, _exec_ops = execute_restructure_plan(
                            _rp,
                            state.ir,
                            migration_ledger=migration_ledger,
                            effective_date=amendment_effective_date.isoformat() if amendment_effective_date else "",
                        )
                        if _exec_ops:
                            _executed_labels = [
                                e.note for e in _exec_ops if e.success
                            ]
                            _skipped_labels = [
                                e.note for e in _exec_ops if not e.success
                            ]
                            if _executed_labels:
                                state = state.with_ir(_new_ir)
                                executed_restructure_plan_ids.add(_rp.amendment_id)
                                if migration_ledger is not None:
                                    _emit_restructure_plan_renumber_legal_operations(
                                        lo_ops_out=lo_ops_out,
                                        migration_events=migration_ledger.events[_migration_events_before:],
                                        amendment_id=amendment_id,
                                        source_title=source_title,
                                        amendment_issue_date=amendment_issue_date,
                                        amendment_effective_date=amendment_effective_date,
                                    )
                                _replay_print(
                                    f"  [{amendment_id}] restructure_plan executed: "
                                    f"{len(_executed_labels)} ops"
                                )
                            if _skipped_labels:
                                logger.debug(
                                    "  [%s] restructure_plan skipped ops: %s",
                                    amendment_id, _skipped_labels,
                                )
                                if findings_out is not None:
                                    for _exec_op in _exec_ops:
                                        finding = relabel_skip_finding(_exec_op, source_statute=amendment_id)
                                        if finding is not None:
                                            findings_out.append(finding)
                                        finding = relabel_skip_source_pathology_finding(
                                            _exec_op,
                                            source_statute=amendment_id,
                                        )
                                        if finding is not None:
                                            findings_out.append(finding)
                                        finding = move_skip_finding(_exec_op, source_statute=amendment_id)
                                        if finding is not None:
                                            findings_out.append(finding)
                                        finding = deferred_plan_op_finding(_exec_op, source_statute=amendment_id)
                                        if finding is not None:
                                            findings_out.append(finding)

            # Step 3: apply each ResolvedOp through the normal apply_op path,
            # emitting section snapshots via _emit_section_snapshot.
            _replaced_labels: List[str] = []
            _inserted_labels: List[str] = []
            for _rop in _uncov_rops:
                try:
                    _prev_state = state
                    state = apply_op(
                        state,
                        None,
                        ctx,
                        None,
                        replay_mode=replay_mode,
                        failed_ops_out=failed_ops_out,
                        source_pathologies_out=source_pathologies_out,
                        mutation_events_out=mutation_events_out,
                        findings_out=findings_out,
                        rop=_rop,
                        replay_history_ops=lo_ops_out,
                        migration_ledger=migration_ledger,
                        strict_profile=strict_profile,
                    )
                except (NameError, TypeError, AttributeError):
                    raise  # programming bugs — fail loud
                except Exception as e:
                    logger.debug("  [%s] uncovered rop %s → ERROR", amendment_id, _rop.description(), exc_info=True)
                    _replay_print(f"  [{amendment_id}] uncovered rop {_rop.description()} → ERROR: {e}")
                    continue
                if lo_ops_out is not None:
                    snapshot_unit_kind, snapshot_target_norm, snapshot_target_chapter, snapshot_target_part = _rop.resolved_group_key
                    _emit_section_snapshot(
                        state,
                        snapshot_unit_kind,
                        snapshot_target_norm,
                        snapshot_target_chapter,
                        snapshot_target_part,
                        [_rop],
                        lo_ops_out,
                        amendment_id,
                        source_title,
                        amendment_issue_date,
                        amendment_effective_date,
                        base_ir=base_ir,
                        migration_ledger=migration_ledger,
                        standalone_section_targets=_standalone_section_targets,
                    )
                if _rop.is_replace_action:
                    _replaced_labels.append(_rop.target_norm)
                else:
                    _inserted_labels.append(_rop.target_norm)
            if _replaced_labels:
                _replay_print(f"  [{amendment_id}] uncovered section replaces: {_replaced_labels}")
            if _inserted_labels:
                _replay_print(f"  [{amendment_id}] uncovered section inserts: {_inserted_labels}")
        elif ops and not _vts_ops_enrich_done and not _uncov_allowed:
            finding = _strict_rejected_uncovered_body_finding(
                source_statute=amendment_id,
                stage="apply",
            )
            if findings_out is not None:
                findings_out.append(finding)
        state = _apply_uncovered_kumotaan(
            state,
            ctx,
            ops,
            johto,
            amendment_id,
            lo_ops_out=lo_ops_out,
            op_source=_uncov_src,
            findings_out=findings_out,
        )

        # Emit chapter-part-move LO ops: tombstone old part address, insert at
        # new part address.  When a new part is created and existing chapters
        # are moved into it (via _create_part_and_move_siblings), the section-
        # level LO ops emitted before the move still carry the old part in
        # their paths.  Without correction the materialized PIT places those
        # chapters under the old part instead of the new one.
        if lo_ops_out is not None and _uncov_src is not None and _ch_to_part_before:
            _pp_after = _tops.find_provisions_parent(state.ir)
            _pp_after_node = _tops.resolve(state.ir, _pp_after) if _pp_after else state.ir
            if _pp_after_node is not None:
                # Build current part label set for the "old part still exists" check.
                _parts_after: set[str] = {
                    _p.label
                    for _p in _pp_after_node.children
                    if _p.kind is IRNodeKind.PART and _p.label
                }
                for _mp in _pp_after_node.children:
                    if _mp.kind is not IRNodeKind.PART or not _mp.label:
                        continue
                    for _mc in _mp.children:
                        if _mc.kind is not IRNodeKind.CHAPTER or not _mc.label:
                            continue
                        _old_part = _ch_to_part_before.get(_mc.label)
                        if _old_part is None or _old_part == _mp.label:
                            continue
                        # Guard: only emit for genuine new-part creation, not part
                        # relabeling.  New part label must not have existed before the
                        # apply loop, AND the old part label must still be present
                        # (if it disappeared the "move" was actually a relabel).
                        if _mp.label in _parts_before:
                            continue  # new part already existed → not a genuine move
                        if _old_part not in _parts_after:
                            continue  # old part gone → chapter was relabeled, not moved
                        # Chapter moved from _old_part to _mp.label this amendment.
                        _old_ch_tl = (("part", _old_part), ("chapter", _mc.label))
                        _new_ch_tl = (("part", _mp.label), ("chapter", _mc.label))
                        # Tombstone old address so base overlay omits ch from old part.
                        lo_ops_out.append(
                            _LegalOperation(
                                op_id=f"chapter_part_move_repeal_{_mc.label}_{amendment_id}",
                                sequence=0,
                                action=StructuralAction.REPEAL,
                                target=LegalAddress(path=_old_ch_tl),
                                source=_uncov_src,
                                group_id=f"finland-johto:{amendment_id}",
                            )
                        )
                        # Insert full chapter content at new address.
                        lo_ops_out.append(
                            _LegalOperation(
                                op_id=f"chapter_part_move_insert_{_mc.label}_{amendment_id}",
                                sequence=0,
                                action=StructuralAction.INSERT,
                                target=LegalAddress(path=_new_ch_tl),
                                payload=_mc,
                                source=_uncov_src,
                                group_id=f"finland-johto:{amendment_id}",
                            )
                        )
                        logger.debug(
                            "  [%s] chapter part-move LO: ch:%s part:%s → part:%s",
                            amendment_id, _mc.label, _old_part, _mp.label,
                        )

    return state


def should_use_sec1_fallback_pre_routing(johto: Optional[str]) -> bool:
    """Determine if section 1 body text should be used as the johtolause before routing."""
    return _should_use_sec1_fallback_pre_routing_impl(johto)


def should_use_sec1_fallback_post_routing(johto: str, sec1_text: str) -> bool:
    """Predicate to determine if section 1 body text should be used as the johtolause after routing."""
    return _should_use_sec1_fallback_post_routing_impl(johto, sec1_text)


def process_muutoslaki(
    amendment_id: str,
    state: "ReplayState",
    ctx: "StatuteContext",
    replay_mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    compiled_ops_out: Optional[List[Dict[str, object]]] = None,
    lo_ops_out: Optional[List[_LegalOperation]] = None,
    parent_id: str = "",
    failed_ops_out: Optional[List[FailedOp]] = None,
    strict_profile: Optional[StrictProfile] = None,
    chapter_seed_skip: Optional[Set[Tuple[str, str]]] = None,
    corpus: Optional[CorpusStore] = None,
    future_repeals: Optional[Set[RepealTargetRef]] = None,
    source_pathologies_out: Optional[List[SourcePathology]] = None,
    elaboration_observations_out: Optional[List[Dict[str, object]]] = None,
    sparse_slot_bindings_out: Optional[List[Dict[str, object]]] = None,
    sparse_leftovers_out: Optional[List[Dict[str, object]]] = None,
    commencement_expiry_overrides_out: Optional[List[Dict[str, object]]] = None,
    mutation_events_out: Optional[List[ApplyMutationEvent]] = None,
    mutation_invariant_reports_out: Optional[List[ApplyMutationInvariantReport]] = None,
    migration_events_out: Optional[List["MigrationEvent"]] = None,
    prior_migration_events: Optional[Iterable["MigrationEvent"]] = None,
    restructure_plans_out: Optional[List[StructuralTransformPlan]] = None,
    processed_amendment_titles: Optional[Dict[str, str]] = None,
) -> "PhaseResult":
    """Process one amendment statute end-to-end.

    Returns a PhaseResult where:
    - ``output``       — updated ``ReplayState`` after applying this amendment
    - ``findings``     — source pathologies, elaboration observations,
                         replay warnings/rejections, and failed-op obligations
    - ``temporal_events`` — executable amendment temporal authority

    The input state is never modified.

    Pipeline:
    - load amendment XML,
    - extract and normalize the `johtolause`,
    - ask the ops model for a compact operation list,
    - fall back to a narrow deterministic parser if the model yields nothing,
    - compile grouped operations via compile_amendment_ops,
    - apply resolved operations as a fold over state,
    - emit section snapshots after each group.

    Note: lo_ops_out, mutation_events_out, mutation_invariant_reports_out, and
    migration_events_out remain as out-params because they carry replay output
    artifacts rather than findings.
    Legacy out-params (source_pathologies_out, elaboration_observations_out,
    etc.) are still populated for backward compatibility, but callers should
    prefer the PhaseResult signals.
    """
    from lawvm.core.phase_result import PhaseResult as _PR

    # Accumulates executable amendment temporal authority from compile/apply phases.
    _amendment_temporal_events: list = []
    _process_findings: list[Finding] = []
    _compat_failed_ops: list[FailedOp] = []
    _compat_source_pathologies: list[SourcePathology] = []
    _compat_elaboration_observations: list[dict[str, object]] = []
    _compat_sparse_slot_bindings: list[dict[str, object]] = []
    _compat_sparse_leftovers: list[dict[str, object]] = []
    _commencement_expiry_override_notes: list[dict[str, object]] = []
    _vts_skipped_targets: list[VtsSkippedTarget] = []
    _effective_restructure_plans_out: list[StructuralTransformPlan] = (
        restructure_plans_out if restructure_plans_out is not None else []
    )
    _processed_amendment_titles = processed_amendment_titles or {}

    def _record_process_finding(
        *,
        kind: str,
        message: str,
        source_statute: str = "",
        detail: Optional[Dict[str, object]] = None,
        role: Literal["observation", "obligation", "violation"] = "obligation",
        blocking: bool = True,
    ) -> Finding:
        spec = get_finding_spec(kind)
        finding_kind = kind
        finding_role = role
        if spec is not None and spec.role == "barrier":
            finding_kind = "RUNTIME.VIOLATION"
            finding_role = "violation"
        finding = Finding(
            kind=finding_kind,
            role=finding_role,
            stage="process_muutoslaki",
            detail={
                "message": message,
                **(detail or {}),
                **({"barrier_code": kind} if finding_kind == "RUNTIME.VIOLATION" else {}),
            },
            source_statute=source_statute,
            blocking=blocking,
        )
        _process_findings.append(finding)
        return finding

    def _record_sec1_fallback(stage: str, previous_johto: str, sec1_fallback_text: str, *, applied: bool) -> None:
        kind = "ELAB.SEC1_PRE_ROUTING_FALLBACK" if stage == "pre_routing" else "ELAB.SEC1_POST_ROUTING_FALLBACK"
        message = (
            "Section 1 body text replaced the parsed johtolause before routing."
            if stage == "pre_routing"
            else "Section 1 body text replaced the parsed johtolause after routing."
        )
        _record_process_finding(
            kind=kind,
            message=message,
            source_statute=amendment_id,
            detail={
                "fallback_stage": stage,
                "fallback_applied": applied,
                "original_johtolause": previous_johto,
                "sec1_fallback_text": sec1_fallback_text,
            },
            role="obligation" if stage == "pre_routing" else "observation",
            blocking=(stage == "pre_routing"),
        )

    def _govern_failed_ops_by_recodification_source_chain_gap() -> None:
        """Move apply failures already owned by recodification source gaps out of failed_ops.

        Large recodification waves can name a pre-wave source provision that is
        missing from the executable live tree because the source chain itself is
        incomplete for that frame.  The restructure phase emits
        ``RECODIFICATION_SOURCE_CHAIN_GAP`` for that exact target; keeping a
        second generic ``section_not_found`` failure obscures the phase-local
        diagnosis without adding evidence.
        """
        if not _compat_failed_ops:
            return

        governed_targets: set[str] = set()
        pathology_details: list[dict[str, object]] = [
            dict(pathology.as_detail())
            for pathology in _compat_source_pathologies
            if pathology.source_statute == amendment_id
        ]
        for finding in _process_findings:
            if finding.kind != "ELAB.SOURCE_PATHOLOGY" or str(finding.source_statute or "") != amendment_id:
                continue
            pathology_details.append(dict(finding.detail))
        for detail in pathology_details:
            if detail.get("code") != "RECODIFICATION_SOURCE_CHAIN_GAP":
                continue
            target_label = str(detail.get("target_label") or "").strip()
            if target_label:
                governed_targets.add(target_label)
        if not governed_targets:
            return

        kept: list[FailedOp] = []
        governed: list[FailedOp] = []
        for failed in _compat_failed_ops:
            if failed.reason_code != "section_not_found":
                kept.append(failed)
                continue
            target_label = (
                f"{failed.target_chapter} luku {failed.target_section} §".strip()
                if failed.target_chapter
                else f"{failed.target_section} §"
            )
            if target_label in governed_targets:
                governed.append(failed)
            else:
                kept.append(failed)

        if not governed:
            return
        _compat_failed_ops[:] = kept
        for failed in governed:
            _record_process_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_SOURCE_CHAIN_GAP",
                message=(
                    "Apply failure is governed by a recodification source-chain gap "
                    "for the same target."
                ),
                source_statute=amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "source_pathology_code": "RECODIFICATION_SOURCE_CHAIN_GAP",
                },
                role="observation",
                blocking=False,
            )

    def _govern_failed_ops_by_same_wave_migration(output_state: "ReplayState") -> None:
        """Move transient old-frame failures behind exact same-wave lineage evidence.

        Some recodification formulas first relabel a source section and then
        refer to "the said section" in the old source frame. If the apply loop
        records a scoped ``section_not_found`` for that old frame, but the same
        amendment's migration ledger maps the exact address to a live final
        section, the failure is governed by the recorded lineage rather than an
        unresolved missing target.
        """
        if not _compat_failed_ops or len(_migration_ledger) <= _migration_ledger_initial_len:
            return

        kept: list[FailedOp] = []
        governed: list[tuple[FailedOp, LegalAddress, LegalAddress]] = []
        for failed in _compat_failed_ops:
            if failed.reason_code != "section_not_found" or not failed.target_section:
                kept.append(failed)
                continue
            source_path: list[tuple[str, str]] = []
            if failed.target_part:
                source_path.append(("part", failed.target_part))
            if failed.target_chapter:
                source_path.append(("chapter", failed.target_chapter))
            source_path.append(("section", failed.target_section))
            source_address = LegalAddress(path=tuple(source_path))
            migrated = _migration_ledger.current_address_with_prefix_migrations(source_address)
            if migrated == source_address:
                kept.append(failed)
                continue
            migrated_labels = {kind: label for kind, label in migrated.path}
            migrated_section = migrated_labels.get("section")
            if not migrated_section:
                kept.append(failed)
                continue
            migrated_path = output_state.find_section_path(
                migrated_section,
                migrated_labels.get("chapter"),
                migrated_labels.get("part"),
            )
            if migrated_path is None:
                kept.append(failed)
                continue
            governed.append((failed, source_address, migrated))

        if not governed:
            return
        _compat_failed_ops[:] = kept
        for failed, source_address, migrated in governed:
            _record_process_finding(
                kind="APPLY.FAILED_OPERATION_GOVERNED_BY_SAME_WAVE_MIGRATION",
                message=(
                    "Apply failure is governed by an exact same-wave migration "
                    "from the old target frame to a live final target."
                ),
                source_statute=amendment_id,
                detail={
                    "failed_description": failed.description,
                    "target_unit_kind": failed.target_unit_kind,
                    "target_part": failed.target_part,
                    "target_chapter": failed.target_chapter,
                    "target_section": failed.target_section,
                    "failed_reason_code": failed.reason_code,
                    "source_address": str(source_address),
                    "migrated_address": str(migrated),
                },
                role="observation",
                blocking=False,
            )

    def _project_compat_sinks() -> None:
        """Project local compatibility capture to caller sinks at the boundary."""
        if failed_ops_out is not None:
            failed_ops_out.extend(_compat_failed_ops)
        if source_pathologies_out is not None:
            source_pathologies_out.extend(_compat_source_pathologies)
        if elaboration_observations_out is not None:
            elaboration_observations_out.extend(_compat_elaboration_observations)
        if sparse_slot_bindings_out is not None:
            sparse_slot_bindings_out.extend(_compat_sparse_slot_bindings)
        if sparse_leftovers_out is not None:
            sparse_leftovers_out.extend(_compat_sparse_leftovers)
        if commencement_expiry_overrides_out is not None:
            commencement_expiry_overrides_out.extend(_commencement_expiry_override_notes)

    def _build_result(output_state: "ReplayState") -> "_PR":
        """Build PhaseResult from local phase-owned signals, then project compat sinks."""
        amendment_temporal_events = list(_amendment_temporal_events)
        merged_findings: list[Finding] = list(_process_findings)
        if _compat_source_pathologies:
            merged_findings.extend(
                Finding(
                    kind="ELAB.SOURCE_PATHOLOGY",
                    role="observation",
                    stage="process_muutoslaki",
                    detail=p.as_detail(),
                    source_statute=p.source_statute or amendment_id,
                    blocking=False,
                )
                for p in _compat_source_pathologies
            )
        if _compat_elaboration_observations:
            merged_findings.extend(
                Finding(
                    kind=str(o.get("kind", "")),
                    role=(
                        _spec.role
                        if (_spec := get_finding_spec(str(o.get("kind", "")).strip())) is not None
                        and _spec.role != "barrier"
                        else "observation"
                    ),
                    stage="process_muutoslaki",
                    detail=dict(o),
                    source_statute=str(o.get("source_statute", amendment_id)),
                    blocking=(
                        _spec.role != "observation"
                        and _spec.default_enforcement in ("strict_fail", "hard_fail")
                        if (_spec := get_finding_spec(str(o.get("kind", "")).strip())) is not None
                        else False
                    ),
                )
                for o in _compat_elaboration_observations
                if str(o.get("kind", "")).strip()
            )
        if _vts_skipped_targets:
            merged_findings.extend(
                Finding(
                    kind=record.rule_id,
                    role="observation",
                    stage=record.phase,
                    detail=record.as_detail(),
                    source_statute=record.source_statute or amendment_id,
                    blocking=record.blocking,
                )
                for record in _vts_skipped_targets
            )
        if _compat_failed_ops:
            merged_findings.extend(
                Finding(
                    kind="APPLY.FAILED_OPERATION",
                    role="obligation",
                    stage="process_muutoslaki",
                    detail={**f.as_detail(), "barrier_code": "APPLY.FAILED_OPERATION"},
                    blocking=True,
                    source_statute="",
                )
                for f in _compat_failed_ops
            )
        # Only check current amendment's events — not the entire accumulated list.
        # The full list is checked at the outer replay_xml level (line ~6788).
        _mutation_start = getattr(_build_result, "_mutation_cursor", 0)
        _current_events = (mutation_events_out or [])[_mutation_start:]
        cast(Any, _build_result)._mutation_cursor = len(mutation_events_out or [])
        mutation_invariant_reports = build_apply_mutation_invariant_reports(_current_events)
        if mutation_invariant_reports_out is not None:
            mutation_invariant_reports_out.extend(mutation_invariant_reports)
        seen_apply_mutation_findings: set[tuple[str, str, str]] = set()
        for report in mutation_invariant_reports:
            for accounting_result in report.results:
                finding = _apply_mutation_invariant_report_finding(
                    report=report,
                    result=accounting_result,
                    source_statute=amendment_id,
                )
                if finding is None:
                    continue
                dedupe_key = (finding.kind, report.op_id, report.helper)
                if dedupe_key in seen_apply_mutation_findings:
                    continue
                merged_findings.append(finding)
                seen_apply_mutation_findings.add(dedupe_key)
        seen_apply_fallback_findings: set[tuple[str, str, str, str]] = set()
        for event in mutation_events_out or []:
            for fallback_kind in (
                "APPLY.LEGACY_DISPATCH_FALLBACK",
                "APPLY.RELABEL_SKIPPED",
                "APPLY.SCOPE_CONFIDENCE_GLOBAL_FALLBACK",
            ):
                finding = _apply_mutation_fallback_event_finding(
                    event=event,
                    fallback_kind=fallback_kind,
                )
                if finding is None:
                    continue
                reason_code = str(finding.detail.get("reason_code") or finding.detail.get("reason_tag") or "")
                dedupe_key = (finding.kind, event.source_statute, event.op_id, reason_code)
                if dedupe_key in seen_apply_fallback_findings:
                    continue
                merged_findings.append(finding)
                seen_apply_fallback_findings.add(dedupe_key)
        boundary_violations = (
            check_apply_mutation_invariant_reports(mutation_invariant_reports)
            if mutation_invariant_reports
            else check_apply_mutation_accounting(mutation_events_out or [])
        )
        if boundary_violations and not mutation_invariant_reports:
            merged_findings.extend(
                _apply_mutation_boundary_violation_finding(
                    violation=violation,
                    source_statute=amendment_id,
                )
                for violation in boundary_violations
            )
        deduped_findings: list[Finding] = []
        seen_finding_keys: set[tuple[str, str, str, str, bool]] = set()
        for finding in merged_findings:
            key = (
                str(finding.kind or ""),
                str(finding.role or ""),
                str(finding.source_statute or ""),
                repr(finding.detail),
                bool(finding.blocking),
            )
            if key in seen_finding_keys:
                continue
            seen_finding_keys.add(key)
            deduped_findings.append(finding)
        _project_compat_sinks()
        return _PR(
            output=output_state,
            findings=tuple(deduped_findings),
            temporal_events=tuple(amendment_temporal_events),
            migration_events=tuple(_migration_ledger.events[_migration_ledger_initial_len:]),
        )

    if corpus is None:
        corpus = _get_corpus_store()
    _migration_ledger = MigrationLedger(prior_migration_events or ())
    _migration_ledger_initial_len = len(_migration_ledger)
    try:
        xml_bytes = corpus.read_source(amendment_id)
        if xml_bytes is None:
            _replay_print(f"  [{amendment_id}] not found in corpus — skipping")
            return _build_result(state)
        # Corrigendum patches (Population B): apply johtolause corrections in
        # both modes. The oracle already has the corrected result — applying the
        # corrigendum to the source johtolause makes PEG target the right provisions.
        # Heuristic #35: gated by strict_profile.allows_source_correction_rules (if set).
        _corr_gate = strict_profile is None or strict_profile.allows_source_correction_rules
        from lawvm.finland.corrigendum import get_patch_table as _get_corr_patch_table
        from lawvm.finland.corrigendum import extract_inline_corrections as _extract_inline_corr

        if _corr_gate:
            _, xml_bytes = _extract_inline_corr(xml_bytes, amendment_id)
            xml_bytes, _corr_applied = _get_corr_patch_table().patch_source_xml(xml_bytes, amendment_id)
            # Body text patches: fix SD amendment bodies with incomplete subsection
            # content (source pathology — sd-cons has full text, SD body truncated).
            xml_bytes, _body_patch_applied = _get_corr_patch_table().patch_source_body_xml(xml_bytes, amendment_id)
        else:
            _corr_applied = False
            _body_patch_applied = []
            _record_process_finding(
                kind="APPLY.STRICT_REJECTED_CORRIGENDUM_PATCH",
                message="Corrigendum Population B patch rejected by strict profile",
                source_statute=amendment_id,
            )
        muutos_tree = etree.fromstring(xml_bytes)
        lacks_operative_structure, operative_tags = _amendment_lacks_operative_structure(muutos_tree)
        johto = get_johtolause(xml_bytes)
        source_title = _tree_title(muutos_tree)

        acquisition = build_amendment_acquisition_result(
            xml_bytes=xml_bytes,
            parent_id=parent_id,
            amendment_id=amendment_id,
            source_title=source_title,
            parent_title=ctx.title,
            strict_profile=strict_profile,
            lacks_operative_structure=lacks_operative_structure,
            operative_structure_tags=operative_tags,
        )
        if acquisition.decision.route_reason == "pending_amendment_of_parent_skip":
            _pending_target_mid = str(acquisition.decision.route_target_amendment_id or "")
            _pending_target_title = str(_processed_amendment_titles.get(_pending_target_mid) or "")
            if _pending_target_mid and _pending_target_title:
                acquisition = build_amendment_acquisition_result(
                    xml_bytes=xml_bytes,
                    parent_id=_pending_target_mid,
                    amendment_id=amendment_id,
                    source_title=source_title,
                    parent_title=_pending_target_title,
                    strict_profile=strict_profile,
                    lacks_operative_structure=lacks_operative_structure,
                    operative_structure_tags=operative_tags,
                )
                _record_process_finding(
                    kind="APPLY.PENDING_AMENDMENT_COMPOSED_ON_PROCESSED_TARGET",
                    message="Pending amendment-of-amendment composed onto already-processed target amendment.",
                    source_statute=amendment_id,
                    detail={
                        "target_amendment_id": _pending_target_mid,
                        "target_amendment_title": _pending_target_title,
                        "base_parent_id": parent_id,
                    },
                    role="observation",
                    blocking=False,
                )
                _replay_print(
                    f"  [{amendment_id}] COMPOSED — pending amendment retargeted from {parent_id} onto processed target {_pending_target_mid}"
                )
        used_sec1_fallback = acquisition.decision.pre_routing_sec1_applied or acquisition.decision.post_routing_sec1_applied
        sec1_text = acquisition.sec1_text
        if acquisition.decision.pre_routing_sec1_requested and sec1_text:
            _record_sec1_fallback(
                "pre_routing",
                acquisition.preamble_text or "",
                sec1_text,
                applied=acquisition.decision.pre_routing_sec1_applied,
            )

        johto = acquisition.decision.chosen_normalized_text

        # Guard: misrouted amendment — amendment_parents.csv sometimes maps an
        # amendment to the wrong master statute.
        #
        # Two-tier check (see route_amendment for full logic):
        # 1. NUM-collision (strict, SKIP): amendment and parent share the same NUM
        #    but the johtolause cites a different statute entirely.
        # 2. Citation mismatch (SKIP): johtolause cites a different statute.
        # Only use the actual preamble text for parent-routing checks (citation_guard_johto),
        # not the sec1 fallback — see route_amendment docstring for rationale.
        _should_apply = acquisition.decision.should_apply
        _route_reason = acquisition.decision.route_reason
        if not _should_apply:
            if _route_reason == "num_collision_skip":
                _replay_print(
                    f"  [{amendment_id}] SKIPPED — NUM-collision false mapping: johtolause targets a different statute (not {parent_id})"
                )
                _record_process_finding(
                    kind="APPLY.SOURCE_INCOMPLETE",
                    message="Amendment skipped: lineage routing rejected by NUM collision.",
                    source_statute=amendment_id,
                    detail={"route_reason": "num_collision_skip"},
                    role="obligation",
                )
            elif _route_reason == "pending_amendment_of_parent_skip":
                _pending_target_mid = acquisition.decision.route_target_amendment_id
                _target_suffix = f" via pending {_pending_target_mid}" if _pending_target_mid else ""
                _replay_print(
                    f"  [{amendment_id}] SKIPPED — pending amendment of parent recognized but not yet composed into {parent_id}{_target_suffix}"
                )
                _record_process_finding(
                    kind="APPLY.SOURCE_INCOMPLETE",
                    message="Amendment skipped: pending amendment-of-amendment target not yet composed.",
                    source_statute=amendment_id,
                    detail={
                        "route_reason": "pending_amendment_of_parent_skip",
                        "target_amendment_id": _pending_target_mid,
                    },
                    role="obligation",
                )
            else:
                # citation_mismatch_skip — covers meta-repeal and title mismatch
                if re.search(
                    r"kumotaan\b.*muuttamisesta\s+.*annetun\s+lain\s*\(\s*\d", johto, re.IGNORECASE | re.DOTALL
                ):
                    logger.debug("  [%s] SKIPPED — meta-repeal targets prior amendment act, not %s", amendment_id, parent_id)
                elif _title_explicitly_targets_other_statute(source_title, ctx.title):
                    _replay_print(f"  [{amendment_id}] SKIPPED — title targets different statute (not {parent_id})")
                else:
                    _replay_print(
                        f"  [{amendment_id}] SKIPPED — citation mismatch: johtolause targets different statute (not {parent_id})"
                    )
                _record_process_finding(
                    kind="APPLY.SOURCE_INCOMPLETE",
                    message="Amendment skipped: citation routing rejected.",
                    source_statute=amendment_id,
                    detail={"route_reason": str(_route_reason or "citation_mismatch_skip")},
                    role="obligation",
                )
            _expiry_override = _commencement_expiry_override(muutos_tree, amendment_id)
            if _expiry_override is not None:
                _target_mid, _labels, _expiry = _expiry_override
                if _target_mid != amendment_id and _rewrite_lo_op_source_expiry(
                    lo_ops_out, _target_mid, _labels, _expiry,
                    parent_statute_id=parent_id, replay_mode=replay_mode,
                ):
                    _scope = sorted(_labels) if _labels else ["*"]
                    _replay_print(
                        f"  [{amendment_id}] voimaantulo_expiry_override: {_target_mid} {_scope} -> {_expiry.isoformat()}"
                    )
                    _commencement_expiry_override_notes.append(
                        {
                            "source_statute": amendment_id,
                            "target_statute": _target_mid,
                            "labels": _scope,
                            "expiry": _expiry.isoformat(),
                            "context": "skipped_amendment",
                        }
                    )
            # Heuristic #38: VTS cross-statute repeal (QUIRKS only).
            _vts_cross_ops = extract_vts_cross_statute_repeals(
                xml_bytes,
                parent_id,
                ctx.title,
                strict_profile,
                skipped_targets_out=_vts_skipped_targets,
            )
            if _vts_cross_ops:
                ops = _enrich_ops_from_amendment_tree(_vts_cross_ops, amendment_id, muutos_tree)
                _replay_print(f"  [{amendment_id}] voimaantulo_repeal (cross-statute): {[op.description() for op in ops]}")
                _vts_ops_enrich_done = True
                _skip_to_compile = True
            else:
                return _build_result(state)
        else:
            _skip_to_compile = False

        if not _skip_to_compile:
            # Skip voimaantuloasetukset and other non-amendment statutes.
            # Fallback: some amendments encode the op in section 1's body.
            if acquisition.decision.post_routing_sec1_applied and sec1_text:
                _record_sec1_fallback(
                    "post_routing",
                    acquisition.decision.citation_guard_johto,
                    sec1_text,
                    applied=True,
                )

            _vts_ops = extract_vts_repeals_fallback(
                johto,
                xml_bytes,
                parent_id,
                ctx.title,
                strict_profile,
                skipped_targets_out=_vts_skipped_targets,
            )
            if _vts_ops:
                ops = _enrich_ops_from_amendment_tree(_vts_ops, amendment_id, muutos_tree)
                _replay_print(f"  [{amendment_id}] voimaantulo_repeal: {[op.description() for op in ops]}")
                _vts_ops_enrich_done = True
            elif not any(kw in johto.lower() for kw in OP_KEYWORDS):
                # Special case: enacting-formula-only amendments with body sections
                # lacking eId attributes may still have new letter-suffix sections to
                # INSERT.  Let normalize_and_compile_ops handle them via the
                # _extract_enacting_formula_body_insert_ops_fallback path.
                _is_enacting_formula = (
                    re.sub(r"\s+", " ", johto).strip().lower()
                    == "eduskunnan päätöksen mukaisesti"
                )
                _has_eid_free_body_sections = bool(
                    muutos_tree.findall(".//{*}section[@eId]") or
                    muutos_tree.findall(".//{*}section")
                ) and not any(s.get("eId") for s in muutos_tree.findall(".//{*}section"))
                if not (_is_enacting_formula and _has_eid_free_body_sections):
                    if lacks_operative_structure and not sec1_text.strip():
                        _compat_source_pathologies.append(
                            build_empty_operative_body_pathology(
                                source_statute=amendment_id,
                                source_title=source_title,
                                has_sec1_fallback_text=False,
                                operative_tags_detected=operative_tags,
                            )
                        )
                    return _build_result(state)
                # Fall through to normalize_and_compile_ops (enacting formula + eid-free sections)
                _vts_ops_enrich_done = False
            else:
                _vts_ops_enrich_done = False

        # Phase 2: PEG extractor → LO normalization chain → AmendmentOp conversion
        # (skipped when _vts_ops_enrich_done is True — ops already populated above)
        if not _vts_ops_enrich_done:
            _phase2_parse_result = _parse_johtolause_clause(johto)
            _phase2_result = normalize_and_compile_ops(
                johto=johto,
                muutos_tree=muutos_tree,
                master=state,
                amendment_id=amendment_id,
                source_title=source_title,
                used_sec1_fallback=used_sec1_fallback,
                parent_id=parent_id,
                strict_profile=strict_profile,
                parse_result=_phase2_parse_result,
            )
            ops = _phase2_result.output
            # Propagate non-commence frontend temporal events (e.g. VÄLIAIKAINEN
            # expire events emitted by _tag_temporary_ops in
            # normalize_and_compile_ops) to the amendment temporal event
            # accumulator.  Only non-commence events are propagated here:
            # commence events are already covered by the wildcard
            # fi-temporal:...:commence event that _temporal_events_from_lo_ops
            # creates for every amendment group_id, and propagating them would
            # add the group_id to covered_commence_group_ids, blocking the
            # wildcard and causing all non-temporary INSERT ops to be skipped.
            if _phase2_result.temporal_events:
                _non_commence = [
                    ev for ev in _phase2_result.temporal_events
                    if ev.kind != "commence"
                ]
                if _non_commence:
                    _amendment_temporal_events.extend(
                        _normalize_frontend_temporal_events(
                            tuple(_non_commence),
                            amendment_id=amendment_id,
                            target_statute=parent_id,
                        )
                    )
            # Project normalized frontend findings to compatibility sinks only.
            for _finding in _phase2_result.findings():
                if _finding.role != "observation":
                    continue
                _compat_elaboration_observations.append(dict(_finding.detail))
            _process_findings.extend(
                finding
                for finding in _phase2_result.findings()
                if finding.role == "obligation"
            )

        # Skip ops for chapters that were already seeded from this amendment's
        # body XML.  The seeded content is already in state.ir — re-applying
        # the same ops would either fail (REPLACE on existing) or duplicate.
        if chapter_seed_skip and ops:
            seeded_labels_for_mid = {ch_label for ch_label, seed_mid in chapter_seed_skip if seed_mid == amendment_id}
            if seeded_labels_for_mid:
                pre_count = len(ops)
                dropped_ops = [op for op in ops if _op_targets_chapter(op, seeded_labels_for_mid)]
                ops = [op for op in ops if not _op_targets_chapter(op, seeded_labels_for_mid)]
                if len(ops) < pre_count:
                    _compat_elaboration_observations.append(
                        {
                            "kind": "ELAB.CHAPTER_SEED_SKIP",
                            "source_statute": amendment_id,
                            "seeded_chapters": sorted(seeded_labels_for_mid),
                            "dropped_count": len(dropped_ops),
                            "dropped_ops": [op.description() for op in dropped_ops],
                        }
                    )
                    _replay_print(
                        f"  [{amendment_id}] SEED-SKIP: dropped {pre_count - len(ops)} op(s) "
                        f"targeting seeded chapter(s) {sorted(seeded_labels_for_mid)}"
                    )

        # Pre-seed pure relabel restructure plans before the main apply loop so
        # same-act structural ownership is not split between the resolved-op
        # path and the restructure executor. Coverage-aware plans may still be
        # added later during uncovered-body analysis; exact duplicates are
        # suppressed there.
        if ops:
            _early_restructure_plan = build_restructure_plan(
                ctx.id,
                amendment_id,
                ops=list(ops),
                uncov_ratio=0.0,
                total_units=0,
                body_unit_ids_by_chapter=None,
            )
            if _early_restructure_plan is not None and not any(
                _existing.amendment_id == amendment_id and _existing.ops == _early_restructure_plan.ops
                for _existing in _effective_restructure_plans_out
            ):
                _effective_restructure_plans_out.append(_early_restructure_plan)

        amendment_effective_date, _eff_step = _amendment_effective_date_with_step(muutos_tree)
        amendment_expiry_date = _amendment_expiry_date(muutos_tree)
        amendment_issue_date = _statute_issue_date(muutos_tree)

        # ── Typed ActivationRule derivation (amendment-level) ─────────────────
        # Derive typed ActivationRules from the johtolause meta surface clauses.
        # This runs BEFORE projection-row emission so that classify_contingent()
        # is the execution authority for contingent detection, replacing the
        # legacy _eff_step == "contingent_text" boolean check.
        from lawvm.finland.johtolause.meta_parse import (  # noqa: PLC0415
            extract_meta_surface_clauses as _extract_meta_sc,
        )
        from lawvm.finland.temporal_lowering import (  # noqa: PLC0415
            activation_rules_from_meta_clauses as _arules_from_mc,
            classify_contingent as _classify_cont,
            default_activation_rule as _default_arule,
        )

        _amendment_meta_clauses = _extract_meta_sc(johto)
        _amendment_activation_rules = _arules_from_mc(_amendment_meta_clauses)
        if not _amendment_activation_rules:
            _amendment_activation_rules = [_default_arule()]

        _primary_rule = _amendment_activation_rules[0]
        _typed_contingent = _classify_cont(_primary_rule)

        # When no typed rules were derived from meta_clauses (empty johtolause
        # temporal info), fall back to the legacy _eff_step detection which
        # reads the amendment body's voimaantulo section.
        if not _typed_contingent and _eff_step == "contingent_text":
            _typed_contingent = True

        # Record date-estimation fallbacks as findings/projection rows (#33: hidden date estimation).
        # Contingent detection flows through ActivationRule when available;
        # falls back to legacy _eff_step for body-derived contingency.
        if _typed_contingent:
            _record_process_finding(
                kind="TIME.CONTINGENT_EFFECTIVE_DATE",
                message=(
                    "Effective date is contingent or decree-set in voimaantulo text; "
                    "publication date is not a trustworthy legal PIT proxy."
                ),
                source_statute=amendment_id,
                detail={
                    "step": _eff_step,
                    "activation_rule_kind": _primary_rule.kind,
                },
            )
        elif _eff_step in ("text_regex", "publication_date"):
            _record_process_finding(
                kind="TIME.ESTIMATED_EFFECTIVE_DATE",
                message=(
                    "Effective date estimated by voimaantulo text regex (step 2)."
                    if _eff_step == "text_regex"
                    else "Effective date substituted by publication date - dateEntryIntoForce absent (step 3)."
                ),
                source_statute=amendment_id,
                detail={"step": _eff_step},
                role="obligation",
                blocking=False,
            )

        # Bridge: log when typed model and legacy _eff_step disagree on contingency
        if _typed_contingent and _eff_step not in ("contingent_text",):
            logger.debug(
                "[%s] activation_rule=%s (contingent) but _eff_step=%s - typed model more specific",
                amendment_id,
                _primary_rule.kind,
                _eff_step,
            )
        elif not _typed_contingent and _eff_step == "contingent_text":
            logger.debug(
                "[%s] _eff_step=contingent_text but activation_rule=%s (not contingent) - legacy more specific",
                amendment_id,
                _primary_rule.kind,
            )

        # Compile: resolve all groups into a flat list of ResolvedOps.
        # Both normalize_and_compile_ops and compile_amendment_ops only read
        # from master (state); neither mutates it.
        _cao_result = compile_amendment_ops(
            state,
            ops,
            muutos_tree,
            johto,
            replay_mode,
            compiled_ops_out=compiled_ops_out,
            strict_profile=strict_profile,
            source_ref=amendment_id,
            source_title=source_title,
            target_statute=ctx.id,
        )
        resolved = _cao_result.output

        def _cover_temporal_coverage() -> None:
            """Emit bounded, non-blocking telemetry for missing johto-level temporal coverage."""
            _fi_johto_prefix = "finland-johto:"

            structural_groups: set[str] = set()
            for op in resolved:
                source_statute_for_group = str(getattr(op, "resolved_source_statute", ""))
                if not source_statute_for_group:
                    source_statute_for_group = str(getattr(op, "source_statute", ""))
                if not source_statute_for_group:
                    source_statute_for_group = str(
                        getattr(getattr(op, "op", None), "source_statute", "")
                    )
                if not source_statute_for_group:
                    source_statute_for_group = amendment_id
                structural_groups.add(f"{_fi_johto_prefix}{source_statute_for_group}")

            temporal_groups: set[str] = set()
            for event in _cao_result.temporal_events:
                group_id = getattr(event, "group_id", "")
                if isinstance(group_id, str) and group_id.startswith(_fi_johto_prefix):
                    temporal_groups.add(group_id)

            missing_groups = tuple(sorted(structural_groups - temporal_groups))
            if not structural_groups or not missing_groups:
                return

            _record_process_finding(
                kind="TIME.TRIGGER_COVERAGE_INCOMPLETE",
                role="obligation",
                blocking=True,
                message=(
                    "Temporal authority is missing for one or more Finland johto-grouped "
                    "structural operations and will remain a migration fallback for this "
                    "compile path."
                ),
                source_statute=amendment_id,
                detail={
                    "coverage_prefix": _fi_johto_prefix,
                    "missing_group_ids": list(missing_groups),
                    "structural_group_count": len(structural_groups),
                    "temporal_group_count": len(temporal_groups),
                },
            )

        # ── Propagate PhaseResult signals back to compatibility sinks ───────
        _amendment_temporal_events.extend(
            _normalize_frontend_temporal_events(
                _cao_result.temporal_events,
                amendment_id=amendment_id,
                target_statute=parent_id,
            )
        )

        _cover_temporal_coverage()

        for _finding in _cao_result.findings():
            if _finding.role == "observation" and _finding.kind == "ELAB.SOURCE_PATHOLOGY":
                detail = cast(dict[str, object], dict(_finding.detail))
                if "target_kind" in detail:
                    detail = dict(detail)
                    detail.pop("target_kind", None)
                _compat_source_pathologies.append(
                    SourcePathology.from_internal_detail(
                        source_statute=_finding.source_statute,
                        detail=detail,
                    )
                )
            elif (
                _finding.role == "observation"
                and _finding.kind == "ELAB.SPARSE_SLOT_BINDING"
            ):
                _compat_sparse_slot_bindings.append(dict(_finding.detail))
            elif (
                _finding.role == "observation"
                and _finding.kind not in (
                "ELAB.SOURCE_PATHOLOGY",
                "ELAB.SPARSE_SLOT_BINDING",
                "ELAB.MISSING_PAYLOAD_SURFACE",
                )
            ):
                _compat_elaboration_observations.append(dict(_finding.detail))

        for _finding in _cao_result.findings():
            if _finding.role != "obligation":
                continue
            if _finding.kind == "ELAB.SPARSE_PAYLOAD_LEFTOVER" and not _finding.blocking:
                _compat_sparse_leftovers.append(dict(_finding.detail))
            elif _finding.blocking:
                d = dict(_finding.detail)
                _record_process_finding(
                    kind=_finding.kind,
                    message=str(d.get("message", "")),
                    source_statute=str(d.get("source_statute", "")),
                    detail={k: v for k, v in d.items() if k not in ("message", "source_statute")},
                )

        _final_state = apply_ops_to_tree(
            state=state,
            ctx=ctx,
            resolved=resolved,
            ops=ops,
            muutos_tree=muutos_tree,
            johto=johto,
            amendment_id=amendment_id,
            source_title=source_title,
            amendment_issue_date=amendment_issue_date,
            amendment_effective_date=amendment_effective_date,
            amendment_expiry_date=amendment_expiry_date,
            replay_mode=replay_mode,
            lo_ops_out=lo_ops_out,
            failed_ops_out=_compat_failed_ops,
            source_pathologies_out=_compat_source_pathologies,
            mutation_events_out=mutation_events_out,
            strict_profile=strict_profile,
            _vts_ops_enrich_done=_vts_ops_enrich_done,
            future_repeals=future_repeals,
            migration_ledger=_migration_ledger,
            restructure_plans_out=_effective_restructure_plans_out,
            observations_out=_compat_elaboration_observations,
            findings_out=_process_findings,
        )
        if migration_events_out is not None and len(_migration_ledger) > _migration_ledger_initial_len:
            migration_events_out.extend(_migration_ledger.events[_migration_ledger_initial_len:])
        if _migration_ledger:
            logger.debug(
                "[%s] migration_ledger: %d event(s)",
                amendment_id,
                len(_migration_ledger),
            )
        # Collect law-level text patches (unscoped "sana X korvataan sanalla Y").
        # These are not structural ops and are skipped by AmendmentOp.from_lo(),
        # so they never reach the section-group compilation path.  We extract
        # them directly from the johtolause and add them to lo_ops_out so that
        # extract_law_level_text_patches() can pick them up after materialization
        # and apply global text replacements across the entire statute.
        if lo_ops_out is not None:
            _eff_iso = amendment_effective_date.isoformat() if amendment_effective_date else ""
            _ll_patches = _extract_law_level_patch_los(
                johto,
                amendment_id=amendment_id,
                effective=_eff_iso,
            )
            if _ll_patches:
                _replay_print(
                    f"  [{amendment_id}] {len(_ll_patches)} law-level text patch(es) collected"
                )
                lo_ops_out.extend(_ll_patches)
        # Commencement expiry override for ACCEPTED amendments.
        # A voimaantulosäännös-only amendment (e.g. amending only the
        # entry-into-force provision of the parent statute) will be accepted by
        # citation routing but produce no section-level lo_ops.  We still need
        # to propagate the new expiry date to existing lo_ops that were seeded
        # by earlier amendments.
        _expiry_override_accepted = _commencement_expiry_override(muutos_tree, amendment_id)
        if _expiry_override_accepted is not None:
            _target_mid_acc, _labels_acc, _expiry_acc = _expiry_override_accepted
            if _target_mid_acc != amendment_id and _rewrite_lo_op_source_expiry(
                lo_ops_out, _target_mid_acc, _labels_acc, _expiry_acc,
                parent_statute_id=parent_id, replay_mode=replay_mode,
            ):
                _scope_acc = sorted(_labels_acc) if _labels_acc else ["*"]
                _replay_print(
                    f"  [{amendment_id}] voimaantulo_expiry_override (accepted): "
                    f"{_target_mid_acc} {_scope_acc} -> {_expiry_acc.isoformat()}"
                )
                _commencement_expiry_override_notes.append(
                    {
                        "source_statute": amendment_id,
                        "target_statute": _target_mid_acc,
                        "labels": _scope_acc,
                        "expiry": _expiry_acc.isoformat(),
                        "context": "accepted_amendment",
                    }
                )
        for _target_mid_sec, _labels_sec, _expiry_sec in _temporary_section_expiry_overrides(
            muutos_tree,
            amendment_id,
        ):
            if _target_mid_sec == amendment_id and _rewrite_lo_op_source_expiry(
                lo_ops_out,
                _target_mid_sec,
                _labels_sec,
                _expiry_sec,
                parent_statute_id=parent_id,
                replay_mode=replay_mode,
            ):
                _scope_sec = sorted(_labels_sec) if _labels_sec else ["*"]
                _replay_print(
                    f"  [{amendment_id}] temporary_section_expiry_override (accepted): "
                    f"{_target_mid_sec} {_scope_sec} -> {_expiry_sec.isoformat()}"
                )
                _commencement_expiry_override_notes.append(
                    {
                        "source_statute": amendment_id,
                        "target_statute": _target_mid_sec,
                        "labels": _scope_sec,
                        "expiry": _expiry_sec.isoformat(),
                        "context": "accepted_section_temporary",
                    }
                )

        _section_commencement_override = _section_commencement_effective_override(
            muutos_tree,
            amendment_id,
        )
        if _section_commencement_override is not None:
            _target_mid_eff, _chapter_sec_map_eff, _effective_eff = _section_commencement_override
            _lo_updated = _rewrite_lo_op_source_effective(
                lo_ops_out,
                _target_mid_eff,
                _effective_eff,
                chapter_section_map=_chapter_sec_map_eff,
                base_ir=ctx.base_ir,
            )
            _scoped_group_id = f"finland-johto:{amendment_id}:section_commencement"
            _scoped_addresses = _rewrite_lo_op_group_id(
                lo_ops_out,
                _target_mid_eff,
                _scoped_group_id,
                chapter_section_map=_chapter_sec_map_eff,
            )
            _compiled_updated = _rewrite_compiled_op_activation_rule_effective(
                compiled_ops_out,
                _target_mid_eff,
                _effective_eff,
                chapter_section_map=_chapter_sec_map_eff,
            )
            if _scoped_addresses:
                _amendment_temporal_events.append(
                    TemporalEvent(
                        event_id=f"fi-temporal:{_scoped_group_id}",
                        kind="commence",
                        scope=TemporalScope(
                            target_statute=parent_id or ctx.id,
                            exact_addresses=_scoped_addresses,
                        ),
                        effective=_effective_eff.isoformat(),
                        source=OperationSource(
                            statute_id=amendment_id,
                            title=source_title,
                            enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
                            effective=_effective_eff.isoformat(),
                        ),
                        activation_rule=ActivationRule(
                            kind="fixed_date",
                            effective_date=_effective_eff.isoformat(),
                        ),
                        group_id=_scoped_group_id,
                    )
                )
            if _lo_updated or _compiled_updated:
                _scope_eff = sorted(
                    f"{chap + '/' if chap else ''}{sec}"
                    for chap, secs in _chapter_sec_map_eff.items()
                    for sec in secs
                )
                _replay_print(
                    f"  [{amendment_id}] section_commencement_effective_override (accepted): "
                    f"{_target_mid_eff} {_scope_eff} -> {_effective_eff.isoformat()}"
                )
                _commencement_expiry_override_notes.append(
                    {
                        "source_statute": amendment_id,
                        "target_statute": _target_mid_eff,
                        "labels": _scope_eff,
                        "effective": _effective_eff.isoformat(),
                        "context": "accepted_section_commencement",
                    }
                )

        if lo_ops_out is not None and amendment_effective_date is not None:
            _later_effective_groups = _rewrite_later_effective_lo_groups(
                lo_ops_out,
                target_source_statute=amendment_id,
                amendment_effective_date=amendment_effective_date,
            )
            for _effective_iso, _exact_addresses in sorted(_later_effective_groups.items()):
                _effective_dt = dt.date.fromisoformat(_effective_iso)
                _rewrite_compiled_op_activation_rule_effective_for_addresses(
                    compiled_ops_out,
                    target_source_statute=amendment_id,
                    effective_date=_effective_dt,
                    exact_addresses=_exact_addresses,
                )
                _amendment_temporal_events.append(
                    TemporalEvent(
                        event_id=f"fi-temporal:finland-johto:{amendment_id}:effective:{_effective_iso}",
                        kind="commence",
                        scope=TemporalScope(
                            target_statute=parent_id or ctx.id,
                            exact_addresses=_exact_addresses,
                        ),
                        effective=_effective_iso,
                        source=OperationSource(
                            statute_id=amendment_id,
                            title=source_title,
                            enacted=amendment_issue_date.isoformat() if amendment_issue_date else "",
                            effective=_effective_iso,
                        ),
                        activation_rule=ActivationRule(
                            kind="fixed_date",
                            effective_date=_effective_iso,
                        ),
                        group_id=f"finland-johto:{amendment_id}:effective:{_effective_iso}",
                    )
                )

        if lo_ops_out is not None and amendment_effective_date is not None:
            _kumotaan_labels = _extract_kumotaan_section_refs(johto)
            _kumotaan_chap_map = _extract_kumotaan_chapter_section_map(johto)
            # Recycle-and-rename guard: if a section appears in BOTH the
            # kumotaan clause (repealing old text) AND the muutetaan clause
            # (replacing with new text under the same number), the muutetaan
            # wins — do not apply the expiry-override to that section so the
            # new content is preserved permanently.
            # Chapter-aware: only mark as recycled when the same section
            # number appears in the SAME chapter in both clauses. This
            # prevents false positives when e.g. §4 is repealed in chapter 9
            # but §4 also appears in muutetaan in chapter 6 — those are
            # different sections that happen to share the same number.
            if _kumotaan_labels:
                _muutetaan_chap_map = _extract_muutetaan_chapter_section_map(johto)
                _kum_has_chapters = bool(
                    _kumotaan_chap_map and any(k is not None for k in _kumotaan_chap_map)
                )
                _mut_has_chapters = bool(
                    _muutetaan_chap_map and any(k is not None for k in _muutetaan_chap_map)
                )
                if _kum_has_chapters and _mut_has_chapters:
                    # Both chapter-scoped: compare same chapter only
                    _recycled: Set[str] = set()
                    for _chap, _kum_secs in _kumotaan_chap_map.items():
                        _mut_secs = {
                            s.lower()
                            for s in _muutetaan_chap_map.get(_chap, [])
                        }
                        for _sec in _kum_secs:
                            if _sec.lower() in _mut_secs:
                                _recycled.add(_sec)
                else:
                    # No chapter context in one or both — fall back to
                    # chapter-unaware comparison (original behaviour).
                    _muutetaan_secs = _extract_muutetaan_section_refs(johto)
                    _recycled = {l for l in _kumotaan_labels if l.lower() in _muutetaan_secs}
                if _recycled:
                    _replay_print(
                        f"  [{amendment_id}] kumotaan_muutetaan_recycle_guard: "
                        f"excluding {sorted(_recycled)} (appear in both kumotaan+muutetaan)"
                    )
                    _kumotaan_labels = [l for l in _kumotaan_labels if l not in _recycled]
            # Build chapter-scoped set map for guards (None key = global).
            _chap_map_sets: Optional[Dict[Optional[str], Set[str]]] = None
            if _kumotaan_chap_map and any(k is not None for k in _kumotaan_chap_map):
                _chap_map_sets = {
                    k: {s.lower() for s in secs}
                    for k, secs in _kumotaan_chap_map.items()
                }
            if _kumotaan_labels and _rewrite_lo_op_source_expiry(
                lo_ops_out,
                amendment_id,
                set(_kumotaan_labels),
                amendment_effective_date,
                parent_statute_id=parent_id,
                replay_mode=replay_mode,
                chapter_section_map=_chap_map_sets,
            ):
                _scope_kumotaan = sorted(set(_kumotaan_labels))
                _replay_print(
                    f"  [{amendment_id}] kumotaan_section_expiry_override: "
                    f"{amendment_id} {_scope_kumotaan} -> {amendment_effective_date.isoformat()}"
                )
                _commencement_expiry_override_notes.append(
                    {
                        "source_statute": amendment_id,
                        "target_statute": amendment_id,
                        "labels": _scope_kumotaan,
                        "expiry": amendment_effective_date.isoformat(),
                        "context": "repeal_clause",
                    }
                )
                _rewrite_kumotaan_snapshot_replaces_to_repeal(
                    lo_ops_out,
                    target_source_statute=amendment_id,
                    section_labels={label.lower() for label in _kumotaan_labels},
                    chapter_section_map=_chap_map_sets,
                )
            # Pure-repeal injection: sections in the kumotaan clause that have
            # no lo_ops from this amendment (no body text) need an explicit
            # REPEAL tombstone so the section is removed from the timeline.
            if _kumotaan_labels:
                _n_pure = _inject_pure_kumotaan_repeal_ops(
                    lo_ops_out,
                    amendment_id=amendment_id,
                    kumotaan_labels=_kumotaan_labels,
                    chap_map_sets=_chap_map_sets,
                    amendment_effective_date=amendment_effective_date,
                    state=state,
                )
                if _n_pure:
                    _replay_print(
                        f"  [{amendment_id}] pure_kumotaan_repeal_injected: "
                        f"{_n_pure} section(s)"
                    )
            # Pure-repeal injection for subsection-range kumotaan clauses
            # (N §:n M–P momentti). Only fires when no body text covered the
            # subsection already.
            # For "tällä asetuksella kumotaan" style amendments the operative
            # text is in the body prose paragraph rather than the formula-based
            # johtolause.  Supplement with the operative body repeal candidate
            # so that subsection ranges declared there are also captured.
            _johto_for_subsec = johto
            if not _extract_kumotaan_subsection_refs(johto):
                _body_repeal = get_operative_body_repeal_candidate(xml_bytes)
                if _body_repeal:
                    _johto_for_subsec = johto + " " + _body_repeal
            _kumotaan_subsection_map = _extract_kumotaan_subsection_refs(_johto_for_subsec)
            if _kumotaan_subsection_map and amendment_effective_date is not None:
                _n_pure_sub = _inject_pure_kumotaan_subsection_repeal_ops(
                    lo_ops_out,
                    amendment_id=amendment_id,
                    kumotaan_subsection_map=_kumotaan_subsection_map,
                    amendment_effective_date=amendment_effective_date,
                    amendment_issue_date=amendment_issue_date,
                    state=state,
                )
                if _n_pure_sub:
                    _replay_print(
                        f"  [{amendment_id}] pure_kumotaan_subsection_repeal_injected: "
                        f"{_n_pure_sub} subsection(s)"
                    )
        _govern_failed_ops_by_recodification_source_chain_gap()
        _govern_failed_ops_by_same_wave_migration(_final_state)
        return _build_result(_final_state)

    except KeyError:
        _replay_print(f"  [{amendment_id}] SKIPPED — not found in zip")
        return _build_result(state)


from lawvm.finland.post_process import post_process_tree  # noqa: E402  (re-export for backward compat)


def replay_xml(
    parent_id: str,
    mode: Literal["finlex_oracle", "legal_pit"] = "finlex_oracle",
    compiled_ops_out: Optional[List[Dict[str, object]]] = None,
    replay_meta_out: Optional[Dict[str, object]] = None,
    lo_ops_out: Optional[List[_LegalOperation]] = None,
    stop_before: str = "",
    failed_ops_out: Optional[List[FailedOp]] = None,
    strict_profile: Optional[StrictProfile] = None,
    corpus: Optional[CorpusStore] = None,
    quiet: bool = False,
    build_full_products: bool = True,
    temporal_events_out: Optional[List[Any]] = None,
    checkpoint_callback: Optional[Any] = None,
    as_of: str = "",
    strict_johto_temporal: bool = False,
    oracle_selector: ConsolidatedArtifactSelector | None = None,
):
    """Replay all applicable amendments for one parent statute.

    `mode` controls the meaning of "applicable":

    - `finlex_oracle`: try to reproduce Finlex consolidated XML for benchmarking.
      Amendment inclusion is based on the consolidated artifact/version conventions.
    - `legal_pit`: apply a strict point-in-time rule based on legal effective dates.

    The return value is an `XMLStatute` whose `master.ir` is the timeline-derived
    point-in-time materialization.  The replay tree is used internally for address
    resolution during compilation; the output is derived from the compiled timelines
    via materialize_pit.  Timelines are stored as `master.timelines`.

    `legal_pit` materializes at the replay cutoff date rather than an open-ended
    future date. This avoids expiring temporary snapshot placeholders and then
    resurrecting stale base text beyond the intended PIT boundary.

    Strict Johto temporal matching is available as an explicit opt-in. The
    permissive default keeps legacy replay callers working while the producer
    path finishes migrating to executable temporal authority.

    Executable ``temporal_events_out`` is the only remaining external temporal
    side-channel hook here. Replay internals no longer export a parallel
    parse-layer ``effect_intents`` rail.
    """
    if corpus is None:
        corpus = _get_corpus_store()
    verbose_token = _set_replay_verbose(not quiet)
    try:
        profile = get_replay_profile(mode)
        from lawvm.finland.corrigendum import extract_inline_corrections as _extract_inline_corr

        plan = prepare_replay_plan(
            parent_id,
            mode=mode,
            strict_profile=strict_profile,
            corpus=corpus,
            stop_before=stop_before,
            label_postprocessor=_fi_label_postprocessor,
            get_replay_profile=get_replay_profile,
            resolve_applicable_amendment_records=(
                lambda resolved_parent_id, resolved_mode, corpus=None: _resolve_applicable_amendment_records(
                    resolved_parent_id,
                    resolved_mode,
                    corpus=corpus,
                    selector=oracle_selector,
                )
            ),
            get_consolidated_oracle_suspect=(
                lambda resolved_parent_id, corpus=None: get_consolidated_oracle_suspect(
                    resolved_parent_id,
                    corpus=corpus,
                    selector=oracle_selector,
                )
            ),
            extract_inline_corrections=_extract_inline_corr,
        )
        capture_lo_ops_out = lo_ops_out
        if capture_lo_ops_out is None and build_full_products:
            capture_lo_ops_out = []
        capture_compiled_ops_out = compiled_ops_out
        if capture_compiled_ops_out is None and build_full_products:
            capture_compiled_ops_out = []
        source_pathologies: List[SourcePathology] = []
        elaboration_observations: List[Dict[str, object]] = []
        sparse_slot_bindings: List[Dict[str, object]] = []
        sparse_leftovers: List[Dict[str, object]] = []
        commencement_expiry_overrides: List[Dict[str, object]] = []
        replay_findings: List[Finding] = []
        mutation_events: List[ApplyMutationEvent] = []
        migration_events: List["MigrationEvent"] = []
        temporal_events: List[Any] = []
        _restructure_plans: List[StructuralTransformPlan] = []
        # Add base statute observations to the elaboration observations stream.
        for base_obs in (plan.ctx.base_observations or ()):
            elaboration_observations.append({
                "kind": str(base_obs.kind or ""),
                "stage": str(base_obs.stage or ""),
                "source_statute": parent_id,
                "target_unit_kind": "statute",
                "target_norm": parent_id,
                "target_chapter": "",
                "detail": dict(base_obs.detail or {}),
            })
        source_normalization_facts = (
            plan.ctx.source_normalization_facts
            if hasattr(plan.ctx, "source_normalization_facts")
            else ()
        )
        for norm_fact in (source_normalization_facts or ()):
            finding_kind = _source_normalization_fact_finding_kind(str(norm_fact.kind_value or ""))
            if finding_kind is None:
                continue
            elaboration_observations.append({
                "kind": finding_kind,
                "stage": "source_normalize",
                "source_statute": parent_id,
                "target_unit_kind": "statute",
                "target_norm": parent_id,
                "target_chapter": "",
                "detail": {
                    "path": list(norm_fact.path),
                    "before": norm_fact.before,
                    "after": norm_fact.after,
                    "basis": norm_fact.basis_value,
                    "confidence": norm_fact.confidence,
                    "explanation": norm_fact.explanation,
                },
            })
        _replay_print(f"Master {parent_id} rehydrated. Title: {plan.ctx.title}")
        if stop_before:
            expected_norm = stop_before.replace("-", "/")
            if "/" in expected_norm:
                parts = expected_norm.split("/")
                expected_norm = f"{parts[0]}/{parts[1]}" if len(parts[0]) == 4 else f"{parts[1]}/{parts[0]}"
            plan_lineage_ids = [str(rec["statute_id"]) for rec in plan.amendment_records]
            if expected_norm and expected_norm not in plan_lineage_ids:
                _replay_print(f"WARNING: --before {stop_before}: amendment not found in chain, ignoring")
            elif expected_norm:
                _replay_print(f"--before {stop_before}: replay truncated before {expected_norm}")
        populate_replay_meta(plan, replay_meta_out)
        if mode == "legal_pit" and plan.oracle_suspect:
            _replay_print(f"WARNING oracle suspect: {plan.oracle_suspect}")
        logger.debug(
            "Replay mode=%s cutoff=%s version=%s",
            mode,
            plan.cutoff_date.isoformat() if plan.cutoff_date else "-",
            plan.oracle_version_amendment_id or "-",
        )
        _replay_print(f"Applying {len(plan.amendment_ids)} muutoslait...")

        replay_fold_state = execute_replay_plan(
            plan,
            corpus=corpus,
            process_muutoslaki=process_muutoslaki,
            seed_missing_chapters=_seed_missing_chapters,
            pre_scan_repeal_targets=_pre_scan_repeal_targets,
            future_repeals_for_index=_build_future_repeal_suffix,
            post_process_tree=post_process_tree,
            check_tree_invariants=_check_tree_invariants,
            compiled_ops_out=capture_compiled_ops_out,
            lo_ops_out=capture_lo_ops_out,
            failed_ops_out=failed_ops_out,
            findings_out=replay_findings,
            source_pathologies_out=source_pathologies,
            elaboration_observations_out=elaboration_observations,
            sparse_slot_bindings_out=sparse_slot_bindings,
            sparse_leftovers_out=sparse_leftovers,
            commencement_expiry_overrides_out=commencement_expiry_overrides,
            mutation_events_out=mutation_events,
            migration_events_out=migration_events,
            temporal_events_out=temporal_events,
            strict_profile=strict_profile,
            logger=logger,
            checkpoint_callback=checkpoint_callback,
            restructure_plans_out=_restructure_plans,
        )
        if temporal_events_out is not None:
            temporal_events_out.extend(temporal_events)
        replay_fold_state = replay_fold_state.with_ir(
            _strip_standalone_subsection_item_prefixes_ir(replay_fold_state.ir)
        )
        # Materialize trailing wrap-up prose as a real structural node before
        # invariant checks and materialization. This preserves the subsection
        # tree shape instead of leaving the closing sentence as a flat content
        # leaf.
        replay_fold_state = replay_fold_state.with_ir(_hoist_trailing_wrapup_ir(replay_fold_state.ir))
        # Remove duplicate same-kind+label sections that can arise when
        # omission-merge expansions and explicit amendment replacements both
        # inject the same label into a body or chapter.  Must run before
        # invariant checking and text-duplication lint so those passes see
        # the cleaned tree.
        deduped_replay_fold_ir = _tops.dedup_children_by_label(replay_fold_state.ir)
        deduped_replay_fold_ir = _emit_structural_dedup_warning(
            phase="replay_fold",
            before_ir=replay_fold_state.ir,
            after_ir=deduped_replay_fold_ir,
            source_statute=parent_id,
            replay_findings=replay_findings,
            replay_meta_out=replay_meta_out,
        )
        replay_fold_state = replay_fold_state.with_ir(deduped_replay_fold_ir)
        # Sort labeled children back into canonical order.  Amendment ops can
        # leave siblings out of order (e.g. sections 5, 3, 7); this pass fixes
        # sort_order invariant violations before the invariant check runs.
        replay_fold_state = replay_fold_state.with_ir(
            _tops.resort_children(replay_fold_state.ir)
        )
        typed_invariant_violations = tuple(_iter_tree_invariant_violations(replay_fold_state.ir))
        invariant_violations = [violation.message for violation in typed_invariant_violations]
        if replay_meta_out is not None and invariant_violations:
            replay_meta_out["invariant_violations"] = list(invariant_violations)
            replay_meta_out["typed_invariant_violations"] = [
                violation.to_dict() for violation in typed_invariant_violations
            ]
        if invariant_violations:
            for violation in invariant_violations:
                replay_findings.append(
                    build_tree_invariant_finding(
                        violation=violation,
                        source_statute="",
                        phase="replay_fold",
                        message="Replay tree invariant violated.",
                    )
                )
            seen_tree_invariants = {
                (
                    finding.kind,
                    str(finding.detail.get("violation") or ""),
                    str(finding.detail.get("phase") or ""),
                    str(finding.source_statute or ""),
                )
                for finding in replay_findings
                if finding.kind == "APPLY.TREE_INVARIANT_VIOLATION"
            }
            for finding in replay_findings:
                if finding.kind != "APPLY.TREE_INVARIANT_VIOLATION":
                    continue
                violation = str(finding.detail.get("violation") or "")
                phase = str(finding.detail.get("phase") or "")
                _replay_print(f"WARNING tree invariant: {violation}")
                seen_tree_invariants.add(
                    ("APPLY.TREE_INVARIANT_VIOLATION", violation, phase, str(finding.source_statute or ""))
                )
        replay_text_duplication_findings = build_text_duplication_findings(
            replay_fold_state.ir,
            phase="replay_fold",
            source_statute=parent_id,
        )
        if replay_meta_out is not None and replay_text_duplication_findings:
            replay_meta_out["text_duplication_warnings"] = [
                {
                    key: value
                    for key, value in finding.detail.items()
                    if key != "message"
                }
                for finding in replay_text_duplication_findings
            ]
        if replay_text_duplication_findings:
            seen_text_warnings = {
                (
                    finding.kind,
                    str(finding.detail.get("phase") or ""),
                    str(finding.detail.get("kind") or ""),
                    str(finding.detail.get("left") or ""),
                    str(finding.detail.get("right") or ""),
                )
                for finding in replay_findings
                if finding.kind == "text_duplication_warning"
            }
            for finding in replay_text_duplication_findings:
                warning = {
                    key: value
                    for key, value in finding.detail.items()
                    if key != "message"
                }
                _replay_print(f"WARNING text duplication: {warning['kind']} {warning['left']} <-> {warning['right']}")
                key = (
                    "text_duplication_warning",
                    "replay_fold",
                    str(warning.get("kind") or ""),
                    str(warning.get("left") or ""),
                    str(warning.get("right") or ""),
                )
                if key not in seen_text_warnings:
                    replay_findings.append(finding)
                    seen_text_warnings.add(key)
        # Convert base observations (from T1b) to findings.
        # These are observations about the base statute source structure (unnumbered peers, label/eId divergences).
        seen_base_observations: Set[tuple[str, str, str, str, str]] = set()
        for obs_dict in elaboration_observations:
            obs_kind = str(obs_dict.get("kind", "")).strip()
            # Only convert base observation kinds (not other elaboration observations).
            if obs_kind != "LABEL_EID_DIVERGENCE" and not obs_kind.startswith("BASE_"):
                continue
            if get_finding_spec(obs_kind) is None:
                continue
            source_statute = str(obs_dict.get("source_statute", "")).strip()
            raw_detail = obs_dict.get("detail")
            detail_dict: dict[str, Any] = {}
            if isinstance(raw_detail, dict):
                for k, v in raw_detail.items():
                    detail_dict[str(k)] = v
            # Use (kind, source_statute, section_address, label, eId) as dedup key to preserve all observations.
            section_address = str(detail_dict.get("section_address", "")).strip()
            if not section_address:
                raw_path = detail_dict.get("path")
                if isinstance(raw_path, list):
                    section_address = "/".join(str(part) for part in raw_path)
                elif isinstance(raw_path, tuple):
                    section_address = "/".join(str(part) for part in raw_path)
            label = str(detail_dict.get("label", "")).strip()
            eId = str(detail_dict.get("eId", "")).strip()
            key = (obs_kind, source_statute, section_address, label, eId)
            if key in seen_base_observations:
                continue
            seen_base_observations.add(key)
            finding = _base_observation_to_finding(obs_dict)
            if finding is not None:
                replay_findings.append(finding)
        if replay_meta_out is not None and source_pathologies:
            replay_meta_out["source_pathologies"] = [
                {
                    "source_statute": pathology.source_statute,
                    **pathology.as_detail(),
                }
                for pathology in source_pathologies
            ]
        if replay_meta_out is not None and elaboration_observations:
            replay_meta_out["elaboration_observations"] = list(elaboration_observations)
        if replay_meta_out is not None and sparse_slot_bindings:
            replay_meta_out["sparse_slot_bindings"] = list(sparse_slot_bindings)
        if replay_meta_out is not None and sparse_leftovers:
            replay_meta_out["sparse_leftovers"] = list(sparse_leftovers)
        if replay_meta_out is not None and commencement_expiry_overrides:
            replay_meta_out["commencement_expiry_overrides"] = list(commencement_expiry_overrides)
        mutation_invariant_reports: tuple[ApplyMutationInvariantReport, ...] = ()
        if replay_meta_out is not None and mutation_events:
            mutation_invariant_reports = build_apply_mutation_invariant_reports(mutation_events)
            replay_meta_out["apply_mutation_events"] = [
                _serialize_apply_mutation_event(event) for event in mutation_events
            ]
            replay_meta_out["apply_mutation_invariant_reports"] = [
                _serialize_apply_mutation_invariant_report(report)
                for report in mutation_invariant_reports
            ]
            seen_apply_mutation_findings: set[tuple[str, str, str, str]] = set()
            for report in mutation_invariant_reports:
                for accounting_result in report.results:
                    finding = _apply_mutation_invariant_report_finding(
                        report=report,
                        result=accounting_result,
                        source_statute=parent_id,
                    )
                    if finding is None:
                        continue
                    dedupe_key = (finding.kind, report.op_id, report.helper, parent_id)
                    if dedupe_key in seen_apply_mutation_findings:
                        continue
                    replay_findings.append(finding)
                    seen_apply_mutation_findings.add(dedupe_key)
        apply_mutation_boundary_violations = (
            check_apply_mutation_invariant_reports(mutation_invariant_reports)
            if mutation_invariant_reports
            else check_apply_mutation_accounting(mutation_events)
        )
        if replay_meta_out is not None and apply_mutation_boundary_violations:
            replay_meta_out["apply_mutation_boundary_violations"] = list(apply_mutation_boundary_violations)
        if apply_mutation_boundary_violations:
            if not mutation_invariant_reports:
                seen_apply_boundary_findings = {
                    (
                        finding.kind,
                        str(finding.detail.get("violation") or ""),
                        parent_id,
                    )
                    for finding in replay_findings
                }
                for violation in apply_mutation_boundary_violations:
                    finding = _apply_mutation_boundary_violation_finding(
                        violation=violation,
                        source_statute=parent_id,
                    )
                    key = (
                        finding.kind,
                        str(finding.detail.get("violation") or ""),
                        parent_id,
                    )
                    if key in seen_apply_boundary_findings:
                        continue
                    replay_findings.append(finding)
                    seen_apply_boundary_findings.add(key)
            for violation in apply_mutation_boundary_violations:
                _replay_print(f"WARNING apply mutation boundary: {violation}")
        if replay_meta_out is not None and _restructure_plans:
            replay_meta_out["restructure_plans"] = [p.to_dict() for p in _restructure_plans]
        if source_pathologies:
            for pathology in source_pathologies:
                _replay_print(
                    f"WARNING source pathology: {pathology.code} {pathology.source_statute} {pathology.target_label}"
                )
        if strict_profile is not None and source_pathologies:
            existing_rejections = {
                (
                    finding.kind,
                    str(finding.detail.get("code") or ""),
                    str(finding.detail.get("target_label") or ""),
                )
                for finding in replay_findings
                if finding.kind == "APPLY.SOURCE_PATHOLOGY_DETECTED"
            }
            for pathology in source_pathologies:
                finding = _strict_rejected_source_pathology_finding(
                    pathology,
                    stage="replay_xml",
                )
                key = (
                    "APPLY.SOURCE_PATHOLOGY_DETECTED",
                    pathology.code,
                    pathology.target_label,
                )
                if key in existing_rejections:
                    continue
                replay_findings.append(finding)
                existing_rejections.add(key)
        source_adjudication = build_source_adjudication(
            parent_id,
            mode,
            cutoff_date=plan.cutoff_date.isoformat() if plan.cutoff_date else "",
            oracle_version_amendment_id=plan.oracle_version_amendment_id or "",
            oracle_suspect=plan.oracle_suspect or "",
            lineage=plan.amendment_records,
        )
        oracle_materialize_as_of: Optional[str] = None
        if mode == "finlex_oracle":
            # oracle_cutoff_iso is the ordering_date of the oracle version
            # (effective_date if present, else issue_date).  It represents the
            # date up to which the oracle XML was consolidated.
            _oracle_cutoff_iso: Optional[str] = (
                plan.cutoff_date.isoformat() if plan.cutoff_date is not None else None
            )
            _oracle_vid_id = plan.oracle_version_amendment_id or ""
            _oracle_vid_repeal_only_future = _oracle_version_future_repeal_only_uses_cutoff_date(
                compiled_ops=capture_compiled_ops_out or (),
                oracle_version_amendment_id=_oracle_vid_id,
                oracle_cutoff_iso=_oracle_cutoff_iso,
            )
            oracle_dates: list[str] = []
            for rec in plan.amendment_records:
                if not bool(rec.get("included", True)):
                    continue
                _oracle_effective = rec.get("effective_date")
                _oracle_issue = rec.get("issue_date")
                _rec_sid = rec.get("statute_id", "")
                # Normalise to ISO strings.
                _eff_iso: Optional[str] = None
                if isinstance(_oracle_effective, dt.date):
                    _eff_iso = _oracle_effective.isoformat()
                elif isinstance(_oracle_effective, str) and _oracle_effective:
                    _eff_iso = _oracle_effective
                _iss_iso: Optional[str] = None
                if isinstance(_oracle_issue, dt.date):
                    _iss_iso = _oracle_issue.isoformat()
                elif isinstance(_oracle_issue, str) and _oracle_issue:
                    _iss_iso = _oracle_issue
                _date_for_oracle = _eff_iso or _iss_iso
                if _date_for_oracle is None:
                    continue
                # Exclude non-oracle-version amendments whose issue_date is on or
                # after the oracle cutoff AND whose effective_date is strictly after
                # the cutoff.  These were published the same day (or later) the
                # oracle was consolidated, so their future effects are not yet in
                # the oracle.
                #
                # Exclude non-oracle-version amendments whose issue_date is on or
                # after the oracle cutoff AND whose effective_date is strictly after
                # the cutoff.  These were published the same day (or later) the
                # oracle was consolidated, so their future effects are not yet in
                # the oracle.
                #
                # The oracle_version_amendment itself is always included — it
                # defines the oracle's PIT and its own future effective_date is
                # what drives oracle_materialize_as_of in the normal case (e.g.
                # 2019/274 for 2009/953 with eff=2020-01-01).
                #
                # Example (exclude): 2023/739 issued 2023-04-14 (= oracle cutoff),
                # effective 2024-01-01.  Oracle @20230785 was consolidated on
                # 2023-04-14 before 2023/739 took effect → exclude so §11 of
                # 1992/785 is not stripped prematurely.
                #
                # Counter-example (keep): 2025/572 issued 2025-06-27 (< cutoff
                # 2025-08-01), effective 2026-01-01.  Finlex already incorporated
                # it into oracle @20250521 → include.
                #
                # Separate bounded family (defer): when the oracle-version
                # amendment itself is a pure future-effective repeal-only act,
                # Finlex may still expose the future repeal merely as editorial
                # notice at the consolidated cutoff instead of applying it in
                # the selected XML. In that family, keep finlex_oracle
                # materialization anchored to the cutoff rather than the later
                # effective date (e.g. 2022/213 <- 2026/45).
                if (
                    _rec_sid != _oracle_vid_id
                    and _oracle_cutoff_iso is not None
                    and _iss_iso is not None
                    and _iss_iso >= _oracle_cutoff_iso
                    and _eff_iso is not None
                    and _eff_iso > _oracle_cutoff_iso
                ):
                    continue
                if (
                    _rec_sid == _oracle_vid_id
                    and _oracle_vid_repeal_only_future
                    and _oracle_cutoff_iso is not None
                    and _eff_iso is not None
                    and _eff_iso > _oracle_cutoff_iso
                ):
                    _date_for_oracle = _oracle_cutoff_iso
                oracle_dates.append(_date_for_oracle)
            if oracle_dates:
                oracle_materialize_as_of = max(oracle_dates)
            # Save the base oracle PIT before any extension.  This value is used
            # for expires_as_of so that temporary sections are not prematurely
            # expired by a later as_of extension driven by kumotaan REPEAL ops.
            _oracle_base_pit = oracle_materialize_as_of
            # Extend oracle_materialize_as_of to cover future-dated REPEAL ops
            # emitted by kumotaan processing for the oracle-version amendment.
            #
            # Background: corpus records carry the amendment's publication/issue
            # date as ``effective_date``, but the kumotaan clause may specify a
            # later effective date for repeals (e.g. amendment 1999/694 for
            # 1992/1282 was published 1999-05-21 but its kumotaan clause repeals
            # sections as of 1999-12-01).  The REPEAL lo_ops produced by the
            # kumotaan pipeline carry the XML-derived effective date.  If
            # ``oracle_materialize_as_of`` is anchored only to the corpus record
            # date the REPEAL op will not be eligible at materialization time and
            # the repealed section will incorrectly survive in the oracle surface.
            #
            # This extension is applied even when ``_oracle_vid_repeal_only_future``
            # is True — that flag was designed for the case where Finlex only shows
            # the future repeal as an editorial notice without applying it in the
            # consolidated XML.  However, even "repeal-only" oracle amendments can
            # incorporate the repeal into the oracle surface (Finlex's choice).
            # Extending ``oracle_materialize_as_of`` here is safe because
            # ``expires_as_of`` is kept at ``_oracle_base_pit`` below, so temporary
            # sections that expire between the base cutoff and the repeal date are
            # not incorrectly expired.
            if _oracle_vid_id and capture_lo_ops_out:
                for _lo in capture_lo_ops_out:
                    _is_repeal_like = _lo.action is StructuralAction.REPEAL or (
                        _lo.action is StructuralAction.REPLACE
                        and _lo.payload is not None
                        and getattr(_lo.payload, "attrs", {}).get("lawvm_repeal_placeholder") == "1"
                    )
                    if not _is_repeal_like:
                        continue
                    _lo_src = _lo.source
                    if _lo_src is None or _lo_src.statute_id != _oracle_vid_id:
                        continue
                    _lo_eff = _lo_src.effective
                    if not _lo_eff:
                        continue
                    if oracle_materialize_as_of is None or _lo_eff > oracle_materialize_as_of:
                        oracle_materialize_as_of = _lo_eff
                        _replay_print(
                            f"  oracle_materialize_as_of extended to {_lo_eff}"
                            f" by REPEAL op {_lo.op_id!r} from {_oracle_vid_id}"
                        )
        else:
            _oracle_base_pit = oracle_materialize_as_of
        if as_of:
            materialize_as_of = as_of
        elif mode == "legal_pit" and plan.cutoff_date is not None:
            materialize_as_of = plan.cutoff_date.isoformat()
        elif mode == "finlex_oracle" and oracle_materialize_as_of is not None:
            materialize_as_of = oracle_materialize_as_of
        elif mode == "finlex_oracle" and plan.cutoff_date is not None:
            materialize_as_of = plan.cutoff_date.isoformat()
        else:
            materialize_as_of = "9999-12-31"
        expires_as_of = ""
        if mode == "finlex_oracle":
            # finlex_oracle expiry horizon tracks oracle_materialize_as_of (which
            # may have been extended to cover future kumotaan REPEAL dates).
            # Both as_of and expires_as_of use the same extended date so that
            # temporary sections expiring at or before the oracle date are
            # correctly treated as expired.
            if oracle_materialize_as_of is not None:
                expires_as_of = oracle_materialize_as_of
            elif plan.cutoff_date is not None:
                expires_as_of = plan.cutoff_date.isoformat()
            else:
                expires_as_of = dt.date.today().isoformat()

        # Detect chapter-scoped expiry from base statute voimaantulo.
        base_tree = etree.fromstring(plan.ctx.base_xml_bytes)
        ch_exp = _chapter_expiry_from_base(base_tree)
        if ch_exp is not None:
            ch_label, ch_date = ch_exp
            _replay_print(f"  base chapter expiry: luku {ch_label} → {ch_date.isoformat()}")

        products = build_replay_products(
            ctx=plan.ctx,
            statute_id=parent_id,
            replay_fold_state=replay_fold_state,
            lo_ops_out=capture_lo_ops_out,
            source_adjudication=source_adjudication,
            as_of=materialize_as_of,
            synthesize_repeal_placeholders=profile.synthesize_repeal_placeholders,
            repeal_placeholder_normalizer=cast(Callable[[object], object], _consolidate_kumottu_range),
            build_full_products=build_full_products,
            temporal_events=tuple(temporal_events),
            strict_johto_temporal=strict_johto_temporal,
            migration_events=tuple(migration_events),
            expires_as_of=expires_as_of,
        )
        # Keep wrap-up normalization visible on the published replay products
        # as well, since materialization paths may preserve the old flat content
        # leaf if they do not explicitly model the concluding prose facet.
        products = ReplayProducts(
            replay_fold_state=products.replay_fold_state.with_ir(
                _hoist_trailing_wrapup_ir(products.replay_fold_state.ir)
            ),
            materialized_state=products.materialized_state.with_ir(
                _hoist_trailing_wrapup_ir(products.materialized_state.ir)
            ),
            timelines=products.timelines,
            temporal_events=products.temporal_events,
            migration_events=products.migration_events,
            materialization_spec=products.materialization_spec,
            source_adjudication=products.source_adjudication,
        )
        # Apply law-level text replacements (global "sana X korvataan sanalla Y")
        # to the materialized IR tree.  These patches have empty target paths and
        # cannot be applied as section-level ops.
        if capture_lo_ops_out and build_full_products:
            law_level_patches = AmendmentOp.extract_law_level_text_patches(capture_lo_ops_out)
            if law_level_patches:
                _replay_print(
                    f"  Applying {len(law_level_patches)} law-level text replacement(s)"
                )
                patched_ir = _apply_law_level_text_patches(
                    products.materialized_state.ir, law_level_patches
                )
                products.materialized_state = products.materialized_state.with_ir(patched_ir)
        product_violations: List[str] = []
        if build_full_products:
            typed_product_tree_violations = {
                "replay_fold_tree": [
                    violation.to_dict()
                    for violation in _iter_tree_invariant_violations(products.replay_fold_state.ir)
                ],
                "materialized_tree": [
                    violation.to_dict()
                    for violation in _iter_tree_invariant_violations(products.materialized_state.ir)
                ],
            }
            product_violations = validate_replay_products(
                plan.ctx,
                products,
                deep_materialization_check=logger.isEnabledFor(logging.DEBUG),
            )
            if replay_meta_out is not None and product_violations:
                replay_meta_out["product_invariant_violations"] = list(product_violations)
                replay_meta_out["typed_product_tree_invariant_violations"] = typed_product_tree_violations
            if product_violations:
                for violation in product_violations:
                    replay_findings.append(
                        _replay_product_invariant_finding(
                            violation=violation,
                            source_statute=parent_id,
                        )
                    )
                seen_product_violations = {
                    (
                        finding.kind,
                        str(finding.detail.get("violation") or ""),
                    )
                    for finding in replay_findings
                    if finding.kind == "APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION"
                }
                for violation in product_violations:
                    _replay_print(f"WARNING product invariant: {violation}")
                    if ("APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION", violation) not in seen_product_violations:
                        replay_findings.append(
                            _replay_product_invariant_finding(
                                violation=violation,
                                source_statute=parent_id,
                            )
                        )
                        seen_product_violations.add(("APPLY.REPLAY_PRODUCT_INVARIANT_VIOLATION", violation))
            if logger.isEnabledFor(logging.DEBUG):
                for violation in product_violations:
                    logger.debug("  PRODUCT INVARIANT: %s", violation)
            # Dedup materialized state before text-duplication linting.
            # Materialization can create new duplicates that weren't present in
            # the replay fold state, so dedup must run again here.
            deduped_materialized_ir = _tops.dedup_children_by_label(products.materialized_state.ir)
            deduped_materialized_ir = _emit_structural_dedup_warning(
                phase="materialized",
                before_ir=products.materialized_state.ir,
                after_ir=deduped_materialized_ir,
                source_statute=parent_id,
                replay_findings=replay_findings,
                replay_meta_out=replay_meta_out,
            )
            products.materialized_state = products.materialized_state.with_ir(deduped_materialized_ir)
            materialized_text_duplication_findings = build_text_duplication_findings(
                deduped_materialized_ir,
                phase="materialized",
                source_statute=parent_id,
            )
            if replay_meta_out is not None and materialized_text_duplication_findings:
                warnings = replay_meta_out.setdefault("text_duplication_warnings", [])
                cast(list, warnings).extend(
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
                    for finding in replay_findings
                    if finding.kind == "text_duplication_warning"
                }
                for finding in materialized_text_duplication_findings:
                    warning = {
                        key: value
                        for key, value in finding.detail.items()
                        if key != "message"
                    }
                    _replay_print(
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
                        replay_findings.append(finding)
                        seen_text_warnings.add(key)

        # Capture oracle selector provenance when an explicit selector was used.
        # Calls select_cached_consolidated_artifact_with_info a second time so
        # the provenance record is always fresh (the archive access is cheap).
        _oracle_selector_info: Optional[OracleSelectorInfo] = None
        if oracle_selector is not None:
            _archive = getattr(corpus, "_archive", None)
            if _archive is not None and hasattr(_archive, "locators"):
                _artifact, _prov = _select_artifact_with_info(
                    _archive,
                    parent_id,
                    selector=oracle_selector,
                )
                _oracle_selector_info = OracleSelectorInfo(
                    selector_mode=_prov.selector_mode,
                    chosen_artifact_version=_prov.chosen_version_tag,
                    tolerance_applied=_prov.tolerance_applied,
                    rejected_candidates=_prov.rejected_version_tags,
                )

        return ReplayResult(
            ctx=plan.ctx,
            products=products,
            findings=tuple(replay_findings),
            oracle_selector_info=_oracle_selector_info,
        )
    finally:
        _reset_replay_verbose(verbose_token)


# _oracle_version_label, get_ground_truth, get_ground_truth_tree
# re-exported via the corpus import block near the top of this file.
