"""Finland adapter for the generic post-fold payload realization audit."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace

from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.payload_realization import (
    PayloadRealizationGap,
    PayloadRealizationUnit,
    audit_payload_realization,
    payload_realization_gap_findings,
)
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland._receipt_path_norm import _FI_KIND_ALIAS_TO_IR
from lawvm.finland.ops import ResolvedOp

_REALIZING_ACTION_TYPES = frozenset({"INSERT", "REPLACE"})


@dataclass(frozen=True, slots=True)
class _RealizationCandidate:
    op_id: str
    action_type: str
    target: LegalAddress | None
    unit: PayloadRealizationUnit


@dataclass(frozen=True, slots=True)
class _ApplyAuditObservation:
    source_statute: str
    detail: dict[str, object]
    sequence: int


def payload_realization_findings(
    *,
    resolved_ops: tuple[ResolvedOp, ...],
    after_ir: IRNode,
    amendment_id: str,
    apply_dispositions_by_op_id: Mapping[str, str] | None = None,
) -> tuple[Finding, ...]:
    """Return audit findings for claimed operation payload absent from ``after_ir``.

    The comparison is intentionally text-realization only.  A failure here says
    "a resolved operation's payload text did not survive the fold"; it does not
    infer a target address, change action family, or mutate replay output.
    """

    candidates = _payload_realization_candidates(resolved_ops)
    candidates = tuple(candidate for candidate in candidates if candidate.unit.text_chunks)
    if not candidates:
        return ()
    gaps = audit_payload_realization(
        units=tuple(candidate.unit for candidate in candidates),
        after_text=irnode_to_text(after_ir),
    )
    shadowed_by_unit_id = _same_amendment_shadowed_units(
        candidates,
        apply_dispositions_by_op_id=apply_dispositions_by_op_id,
    )
    shadowed_gaps = tuple(gap for gap in gaps if gap.unit_id in shadowed_by_unit_id)
    gaps = tuple(gap for gap in gaps if gap.unit_id not in shadowed_by_unit_id)
    findings = payload_realization_gap_findings(gaps, source_ref=amendment_id)
    findings = findings + _same_amendment_shadow_findings(
        shadowed_gaps,
        amendment_id=amendment_id,
        shadowed_by_unit_id=shadowed_by_unit_id,
    )
    if not apply_dispositions_by_op_id:
        return findings
    findings = tuple(
        _attach_apply_disposition(finding, apply_dispositions_by_op_id)
        for finding in findings
    )
    return _classify_apply_failed_payload_gaps(findings)


def _attach_apply_disposition(
    finding: Finding,
    apply_dispositions_by_op_id: Mapping[str, str],
) -> Finding:
    op_id = str(finding.detail.get("unit_id") or "")
    disposition = apply_dispositions_by_op_id.get(op_id)
    if not disposition:
        return finding
    return replace(
        finding,
        detail={
            **finding.detail,
            "apply_disposition": disposition,
            "apply_disposition_source": "APPLY.RESOLVED_OP_AUDIT",
        },
    )


def attach_payload_gap_apply_dispositions(
    findings: tuple[Finding, ...],
    *,
    materialized_as_of: str = "",
) -> tuple[Finding, ...]:
    """Annotate payload gaps with same-amendment apply audit dispositions."""

    dispositions_by_source_and_op: dict[tuple[str, str], str] = {}
    for finding in findings:
        if finding.kind != "APPLY.RESOLVED_OP_AUDIT":
            continue
        detail = finding.detail.get("detail", finding.detail)
        if not isinstance(detail, Mapping):
            continue
        op_id = str(detail.get("op_id") or "")
        disposition = str(detail.get("disposition") or "")
        if finding.source_statute and op_id and disposition:
            dispositions_by_source_and_op[(finding.source_statute, op_id)] = disposition

    if not dispositions_by_source_and_op:
        return findings

    annotated: list[Finding] = []
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            annotated.append(finding)
            continue
        op_id = str(finding.detail.get("unit_id") or "")
        disposition = dispositions_by_source_and_op.get((finding.source_statute, op_id))
        if not disposition:
            annotated.append(finding)
            continue
        annotated.append(
            _attach_apply_disposition(
                finding,
                {op_id: disposition},
            )
        )
    findings = _suppress_same_amendment_shadowed_payload_gaps(tuple(annotated))
    findings = _classify_expired_source_window_payload_gaps(
        findings,
        materialized_as_of=materialized_as_of,
    )
    findings = _classify_later_amendment_superseded_payload_gaps(findings)
    return _classify_apply_failed_payload_gaps(findings)


def _classify_apply_failed_payload_gaps(
    findings: tuple[Finding, ...],
) -> tuple[Finding, ...]:
    classified: list[Finding] = []
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            classified.append(finding)
            continue
        if str(finding.detail.get("apply_disposition") or "") != "APPLY_FAILED":
            classified.append(finding)
            continue
        classified.append(_apply_failure_finding_from_gap(finding))
    return tuple(classified)


def _classify_later_amendment_superseded_payload_gaps(
    findings: tuple[Finding, ...],
) -> tuple[Finding, ...]:
    apply_audits = _ordered_apply_audit_observations(findings)
    if not apply_audits:
        return findings

    classified: list[Finding] = []
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            classified.append(finding)
            continue
        if str(finding.detail.get("apply_disposition") or "") != "APPLIED":
            classified.append(finding)
            continue
        superseding = _later_amendment_applied_replace_supersession(finding, apply_audits)
        if superseding is None:
            classified.append(finding)
            continue
        classified.append(_later_supersession_finding_from_gap(finding, superseding))
    return tuple(classified)


def _classify_expired_source_window_payload_gaps(
    findings: tuple[Finding, ...],
    *,
    materialized_as_of: str,
) -> tuple[Finding, ...]:
    if not materialized_as_of:
        return findings
    apply_audits = _ordered_apply_audit_observations(findings)
    if not apply_audits:
        return findings

    classified: list[Finding] = []
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            classified.append(finding)
            continue
        if str(finding.detail.get("apply_disposition") or "") != "APPLIED":
            classified.append(finding)
            continue
        expired_audit = _expired_source_window_audit(
            finding,
            apply_audits,
            materialized_as_of=materialized_as_of,
        )
        if expired_audit is None:
            classified.append(finding)
            continue
        classified.append(
            _expired_source_window_finding_from_gap(
                finding,
                expired_audit,
                materialized_as_of=materialized_as_of,
            )
        )
    return tuple(classified)


def _expired_source_window_audit(
    gap: Finding,
    apply_audits: tuple[_ApplyAuditObservation, ...],
    *,
    materialized_as_of: str,
) -> _ApplyAuditObservation | None:
    gap_source = gap.source_statute or ""
    gap_op_id = str(gap.detail.get("unit_id") or "")
    if not gap_source or not gap_op_id:
        return None
    for audit in apply_audits:
        if audit.source_statute != gap_source:
            continue
        if str(audit.detail.get("op_id") or "") != gap_op_id:
            continue
        expires = str(audit.detail.get("source_expires") or "")
        if expires and expires <= materialized_as_of:
            return audit
    return None


def _ordered_apply_audit_observations(findings: tuple[Finding, ...]) -> tuple[_ApplyAuditObservation, ...]:
    observations: list[_ApplyAuditObservation] = []
    for sequence, finding in enumerate(findings):
        if finding.kind != "APPLY.RESOLVED_OP_AUDIT":
            continue
        detail = finding.detail.get("detail", finding.detail)
        if not isinstance(detail, Mapping):
            continue
        source = finding.source_statute or ""
        if not source:
            continue
        observations.append(
            _ApplyAuditObservation(
                source_statute=source,
                detail=dict(detail),
                sequence=sequence,
            )
        )
    return tuple(observations)


def _later_amendment_applied_replace_supersession(
    gap: Finding,
    apply_audits: tuple[_ApplyAuditObservation, ...],
) -> _ApplyAuditObservation | None:
    gap_source = gap.source_statute or ""
    gap_op_id = str(gap.detail.get("unit_id") or "")
    gap_target = _address_path_from_string(str(gap.detail.get("parent_label") or ""))
    if not gap_source or not gap_op_id or not gap_target:
        return None

    gap_audit = next(
        (
            audit
            for audit in apply_audits
            if audit.source_statute == gap_source
            and str(audit.detail.get("op_id") or "") == gap_op_id
        ),
        None,
    )
    if gap_audit is None:
        return None

    for audit in apply_audits:
        if audit.sequence <= gap_audit.sequence:
            continue
        if audit.source_statute == gap_source:
            continue
        if str(audit.detail.get("action_type") or "").upper() != "REPLACE":
            continue
        if str(audit.detail.get("disposition") or "") != "APPLIED":
            continue
        if str(audit.detail.get("target_special") or ""):
            continue
        audit_target = _address_path_from_apply_audit(audit.detail)
        if audit_target and _path_has_prefix(gap_target, audit_target):
            return audit
    return None


def _suppress_same_amendment_shadowed_payload_gaps(
    findings: tuple[Finding, ...],
) -> tuple[Finding, ...]:
    apply_audits_by_source: dict[str, list[dict[str, object]]] = {}
    for finding in findings:
        if finding.kind != "APPLY.RESOLVED_OP_AUDIT":
            continue
        detail = finding.detail.get("detail", finding.detail)
        if not isinstance(detail, Mapping):
            continue
        source = finding.source_statute or ""
        if not source:
            continue
        apply_audits_by_source.setdefault(source, []).append(dict(detail))

    if not apply_audits_by_source:
        return findings

    retained: list[Finding] = []
    shadow_findings: list[Finding] = []
    shadowed_unit_ids: set[tuple[str, str]] = set()
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            retained.append(finding)
            continue
        shadow = _same_source_later_applied_replace_shadow(finding, apply_audits_by_source)
        if shadow is None:
            retained.append(finding)
            continue
        shadow_key = (finding.source_statute or "", str(finding.detail.get("unit_id") or ""))
        if shadow_key in shadowed_unit_ids:
            continue
        shadowed_unit_ids.add(shadow_key)
        shadow_findings.append(_shadow_finding_from_gap(finding, shadow))
    return tuple(retained + shadow_findings)


def _same_source_later_applied_replace_shadow(
    gap: Finding,
    apply_audits_by_source: Mapping[str, list[dict[str, object]]],
) -> dict[str, object] | None:
    source = gap.source_statute or ""
    audits = apply_audits_by_source.get(source, [])
    if not audits:
        return None
    gap_detail = gap.detail
    if str(gap_detail.get("apply_disposition") or "") != "APPLIED":
        return None
    gap_op_id = str(gap_detail.get("unit_id") or "")
    gap_target = _address_path_from_string(str(gap_detail.get("parent_label") or ""))
    if not gap_op_id or not gap_target:
        return None
    gap_index = next(
        (index for index, audit in enumerate(audits) if str(audit.get("op_id") or "") == gap_op_id),
        None,
    )
    if gap_index is None:
        return None
    for audit in audits[gap_index + 1 :]:
        if str(audit.get("action_type") or "").upper() != "REPLACE":
            continue
        if str(audit.get("disposition") or "") != "APPLIED":
            continue
        if str(audit.get("target_special") or ""):
            continue
        audit_target = _address_path_from_apply_audit(audit)
        if audit_target and _path_has_prefix(gap_target, audit_target):
            return audit
    return None


def _address_path_from_apply_audit(
    audit: Mapping[str, object],
) -> tuple[tuple[str, str], ...]:
    path: list[tuple[str, str]] = []
    part = str(audit.get("target_part") or "")
    chapter = str(audit.get("target_chapter") or "")
    target_norm = str(audit.get("target_norm") or "")
    unit_kind = str(audit.get("target_unit_kind") or "")
    paragraph = str(audit.get("target_paragraph") or "")
    item = str(audit.get("target_item") or "")
    if part:
        path.append(("part", part))
    if chapter:
        path.append(("chapter", chapter))
    if unit_kind == "chapter":
        if not chapter and target_norm:
            path.append(("chapter", target_norm))
        return tuple(path)
    if target_norm:
        path.append(("section", target_norm))
    if paragraph:
        path.append(("subsection", paragraph))
    if item:
        path.append(("item", item))
    return tuple(path)


def _address_path_from_string(value: str) -> tuple[tuple[str, str], ...]:
    path: list[tuple[str, str]] = []
    for part in value.split("/"):
        if not part or ":" not in part:
            break
        kind, label = part.split(":", 1)
        if not kind or not label:
            return ()
        path.append((kind, label))
    return tuple(path)


def _path_has_prefix(
    path: tuple[tuple[str, str], ...],
    prefix: tuple[tuple[str, str], ...],
) -> bool:
    return len(prefix) <= len(path) and path[: len(prefix)] == prefix


def _shadow_finding_from_gap(gap: Finding, shadow: Mapping[str, object]) -> Finding:
    return Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_SHADOWED_BY_SAME_AMENDMENT",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute=gap.source_statute,
        blocking=False,
        detail={
            "unit_id": gap.detail.get("unit_id"),
            "unit_kind": gap.detail.get("unit_kind"),
            "observed_label": gap.detail.get("observed_label"),
            "parent_label": gap.detail.get("parent_label"),
            "shadowing_unit_id": shadow.get("op_id"),
            "shadowing_action_type": shadow.get("action_type"),
            "shadowing_target": "/".join(
                f"{kind}:{label}" for kind, label in _address_path_from_apply_audit(shadow)
            ),
            "disposition": "source_payload_shadowed_by_later_same_amendment_replace",
        },
    )


def _later_supersession_finding_from_gap(
    gap: Finding,
    superseding: _ApplyAuditObservation,
) -> Finding:
    return Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_SUPERSEDED_BY_LATER_AMENDMENT",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute=gap.source_statute,
        blocking=False,
        detail={
            **gap.detail,
            "superseding_source_statute": superseding.source_statute,
            "superseding_unit_id": superseding.detail.get("op_id"),
            "superseding_action_type": superseding.detail.get("action_type"),
            "superseding_target": "/".join(
                f"{kind}:{label}"
                for kind, label in _address_path_from_apply_audit(superseding.detail)
            ),
            "disposition": "source_payload_superseded_by_later_amendment_replace",
        },
    )


def _expired_source_window_finding_from_gap(
    gap: Finding,
    audit: _ApplyAuditObservation,
    *,
    materialized_as_of: str,
) -> Finding:
    return Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_EXPIRED_SOURCE_WINDOW",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute=gap.source_statute,
        blocking=False,
        detail={
            **gap.detail,
            "source_effective": audit.detail.get("source_effective"),
            "source_expires": audit.detail.get("source_expires"),
            "materialized_as_of": materialized_as_of,
            "disposition": "source_payload_expired_before_materialized_horizon",
        },
    )


def _apply_failure_finding_from_gap(gap: Finding) -> Finding:
    return Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_BLOCKED_BY_APPLY_FAILURE",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute=gap.source_statute,
        blocking=False,
        detail={
            **gap.detail,
            "disposition": "source_payload_realization_blocked_by_apply_failure",
        },
    )


def _payload_realization_units(
    resolved_ops: tuple[ResolvedOp, ...],
) -> tuple[PayloadRealizationUnit, ...]:
    return tuple(candidate.unit for candidate in _payload_realization_candidates(resolved_ops))


def _payload_realization_candidates(
    resolved_ops: tuple[ResolvedOp, ...],
) -> tuple[_RealizationCandidate, ...]:
    units: list[_RealizationCandidate] = []
    for index, rop in enumerate(resolved_ops):
        action_type = getattr(rop, "resolved_action_type", "")
        if action_type not in _REALIZING_ACTION_TYPES:
            continue
        payload_ir = rop.resolved_amend_sub_ir() or rop.muutos_ir or rop.cross_ir
        if payload_ir is None:
            continue
        payload_ir = _target_scoped_payload_ir(payload_ir, rop.resolved_target_address)
        if payload_ir is None:
            continue
        unit_id = rop.op_id or f"resolved_op_{index}"
        target = rop.resolved_target_address
        truncate_at_omission = _is_terminal_child_target(payload_ir, rop.resolved_target_address)
        unit = PayloadRealizationUnit(
            unit_id=unit_id,
            unit_kind=action_type,
            observed_label=rop.resolved_target_label,
            parent_label=str(target or ""),
            text_chunks=_payload_text_chunks(payload_ir, truncate_at_omission=truncate_at_omission),
        )
        units.append(
            _RealizationCandidate(
                op_id=unit_id,
                action_type=action_type,
                target=target,
                unit=unit,
            )
        )
    return tuple(units)


def _same_amendment_shadowed_units(
    candidates: tuple[_RealizationCandidate, ...],
    *,
    apply_dispositions_by_op_id: Mapping[str, str] | None,
) -> dict[str, _RealizationCandidate]:
    if not apply_dispositions_by_op_id:
        return {}

    shadowed: dict[str, _RealizationCandidate] = {}
    for index, candidate in enumerate(candidates):
        if candidate.target is None:
            continue
        for later in candidates[index + 1 :]:
            if later.action_type != "REPLACE" or later.target is None:
                continue
            if apply_dispositions_by_op_id.get(later.op_id) != "APPLIED":
                continue
            if _target_region_supersedes(later.target, candidate.target):
                shadowed[candidate.op_id] = later
                break
    return shadowed


def _target_region_supersedes(later_target: LegalAddress, earlier_target: LegalAddress) -> bool:
    """Return whether a later replace target owns the earlier target region."""

    return earlier_target.has_prefix(later_target)


def _same_amendment_shadow_findings(
    shadowed_gaps: tuple[PayloadRealizationGap, ...],
    *,
    amendment_id: str,
    shadowed_by_unit_id: Mapping[str, _RealizationCandidate],
) -> tuple[Finding, ...]:
    seen: set[str] = set()
    findings: list[Finding] = []
    for gap in shadowed_gaps:
        if gap.unit_id in seen:
            continue
        seen.add(gap.unit_id)
        shadow = shadowed_by_unit_id[gap.unit_id]
        findings.append(
            Finding(
                kind="COVERAGE.PAYLOAD_REALIZATION_SHADOWED_BY_SAME_AMENDMENT",
                role=OBSERVATION_ROLE,
                stage="post_apply_payload_realization",
                source_statute=amendment_id,
                blocking=False,
                detail={
                    "unit_id": gap.unit_id,
                    "unit_kind": gap.unit_kind,
                    "observed_label": gap.observed_label,
                    "parent_label": gap.parent_label,
                    "shadowing_unit_id": shadow.op_id,
                    "shadowing_action_type": shadow.action_type,
                    "shadowing_target": str(shadow.target or ""),
                    "disposition": "source_payload_shadowed_by_later_same_amendment_replace",
                },
            )
        )
    return tuple(findings)


def _target_scoped_payload_ir(payload_ir: IRNode, target: LegalAddress | None) -> IRNode | None:
    """Return the payload subtree owned by ``target`` when structurally provable."""

    if target is None or not target.path:
        return payload_ir
    if target.special in _FACET_NODE_KINDS:
        return _target_scoped_facet_payload_ir(payload_ir, target.special)
    descendant_payload = _target_scoped_descendant_payload_ir(payload_ir, target)
    if descendant_payload is not None:
        return descendant_payload
    terminal_kind, terminal_label = target.path[-1]
    if terminal_kind not in _TARGET_NODE_KINDS:
        return payload_ir
    if _node_matches_target(payload_ir, terminal_kind, terminal_label):
        return payload_ir
    matching_descendants = tuple(
        node
        for node in _walk_ir(payload_ir)
        if node is not payload_ir and _node_matches_target(node, terminal_kind, terminal_label)
    )
    if len(matching_descendants) == 1:
        return matching_descendants[0]
    if terminal_kind in _CHILD_TARGET_KINDS:
        return None
    return payload_ir


def _target_scoped_descendant_payload_ir(payload_ir: IRNode, target: LegalAddress) -> IRNode | None:
    item_label = _path_label(target, "item")
    subitem_label = _path_label(target, "subitem")
    if item_label and subitem_label:
        return _unique_payload_match(
            payload_ir,
            (
                lambda node, ancestors: _node_matches_target(node, "item", item_label + subitem_label)
                or (
                    _node_matches_target(node, "subitem", subitem_label)
                    and any(_node_matches_target(ancestor, "item", item_label) for ancestor in ancestors)
                )
            ),
        )
    if item_label:
        split_item_label = _split_item_subitem_label(item_label)
        if split_item_label is not None:
            split_item, split_subitem = split_item_label
            return _unique_payload_match(
                payload_ir,
                (
                    lambda node, ancestors: _node_matches_target(node, "item", item_label)
                    or (
                        _node_matches_target(node, "subitem", split_subitem)
                        and any(_node_matches_target(ancestor, "item", split_item) for ancestor in ancestors)
                    )
                ),
            )
    return None


def _path_label(target: LegalAddress, kind: str) -> str:
    for path_kind, label in reversed(target.path):
        if path_kind == kind:
            return label
    return ""


def _split_item_subitem_label(label: str) -> tuple[str, str] | None:
    digits = ""
    for char in label:
        if not char.isdigit():
            break
        digits += char
    suffix = label[len(digits):]
    if not digits or len(suffix) != 1 or not suffix.isalpha():
        return None
    return digits, suffix


def _unique_payload_match(
    payload_ir: IRNode,
    predicate: Callable[[IRNode, tuple[IRNode, ...]], bool],
) -> IRNode | None:
    matches = tuple(
        node
        for node, ancestors in _walk_ir_with_ancestors(payload_ir)
        if node is not payload_ir and predicate(node, ancestors)
    )
    if len(matches) != 1:
        return None
    return matches[0]


def _target_scoped_facet_payload_ir(payload_ir: IRNode, facet: FacetKind) -> IRNode | None:
    facet_kinds = _FACET_NODE_KINDS[facet]
    if payload_ir.kind in facet_kinds:
        return payload_ir
    matching_descendants = tuple(
        node for node in _walk_ir(payload_ir) if node is not payload_ir and node.kind in facet_kinds
    )
    if len(matching_descendants) == 1:
        return matching_descendants[0]
    return None


def _walk_ir(node: IRNode) -> tuple[IRNode, ...]:
    nodes = [node]
    for child in node.children:
        nodes.extend(_walk_ir(child))
    return tuple(nodes)


def _walk_ir_with_ancestors(
    node: IRNode,
    ancestors: tuple[IRNode, ...] = (),
) -> tuple[tuple[IRNode, tuple[IRNode, ...]], ...]:
    rows = [(node, ancestors)]
    for child in node.children:
        rows.extend(_walk_ir_with_ancestors(child, ancestors + (node,)))
    return tuple(rows)


# Finland-local legal-address vocabulary → IR-kind-vocabulary equivalence map
# for the payload-realization target-binding check. Single source of truth:
# imported from ``_receipt_path_norm`` so the receipt-side canonicalization
# (per ``BOUND_TARGET_PATH_NORMALIZATION_DESIGN`` PR1) and the payload
# realization audit share one fact (rule-of-three per §2.6).
_TARGET_NODE_KINDS: Mapping[str, frozenset[IRNodeKind]] = _FI_KIND_ALIAS_TO_IR

_FACET_NODE_KINDS: dict[FacetKind, frozenset[IRNodeKind]] = {
    FacetKind.HEADING: frozenset({IRNodeKind.HEADING, IRNodeKind.CROSS_HEADING}),
    FacetKind.INTRO: frozenset({IRNodeKind.INTRO}),
}

_CHILD_TARGET_KINDS = frozenset({"item", "subitem"})


def _node_matches_target(node: IRNode, target_kind: str, target_label: str) -> bool:
    return node.kind in _TARGET_NODE_KINDS[target_kind] and node.label == target_label


def _is_terminal_child_target(payload_ir: IRNode, target: LegalAddress | None) -> bool:
    """True when the scoped payload IS the terminal item/subitem target itself.

    In that scope an OMISSION boundary separates this target's own payload from
    later carried/rejected siblings owned by other targets, so chunk collection
    must stop at the first OMISSION. For coarser scopes
    (section/subsection sparse-omission restatements) post-omission siblings are
    legitimately owned source payload and must be collected.
    """

    if target is None or not target.path:
        return False
    terminal_kind, terminal_label = target.path[-1]
    if terminal_kind not in _CHILD_TARGET_KINDS:
        return False
    return _node_matches_target(payload_ir, terminal_kind, terminal_label)


def _payload_text_chunks(node: IRNode, *, truncate_at_omission: bool = False) -> tuple[str, ...]:
    chunks: list[str] = []
    _collect_chunks(node, chunks, truncate_at_omission=truncate_at_omission)
    return tuple(dict.fromkeys(chunks))


def _collect_chunks(node: IRNode, chunks: list[str], *, truncate_at_omission: bool) -> None:
    if node.kind is IRNodeKind.OMISSION:
        return
    if node.text:
        chunks.append(node.text)
    for child in node.children:
        if truncate_at_omission and child.kind is IRNodeKind.OMISSION:
            # Item/subitem scope: an OMISSION boundary marks the end of this
            # target's own payload; later siblings are carried/rejected content
            # belonging to other targets.
            break
        _collect_chunks(child, chunks, truncate_at_omission=truncate_at_omission)


__all__ = [
    "attach_payload_gap_apply_dispositions",
    "payload_realization_findings",
]
