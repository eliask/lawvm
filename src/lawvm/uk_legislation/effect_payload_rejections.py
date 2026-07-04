"""Payload-gate rejection helpers for UK effect lowering."""

from __future__ import annotations

from lxml import etree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress
from lawvm.uk_legislation.addressing import _addr_container, _addr_leaf_kind, _addr_leaf_label
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import _append_uk_effect_lowering_rejection
from lawvm.uk_legislation.source_payload_elaboration import (
    _is_broad_schedule_flat_replace_payload,
    _is_non_substantive_structural_payload,
)
from lawvm.uk_legislation.uk_grafter import _clean_num
from lawvm.uk_legislation.xml_helpers import _tag
from lawvm.core.quirks_disposition import QuirksDisposition


def reject_mixed_heading_structural_insert_missing_payload(
    *,
    effect: UKEffectRecord,
    t_str: str,
    mixed_heading_source_ref_by_target: dict[str, str],
    content_ir: Optional[dict[str, Any]],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if content_ir is not None or t_str not in mixed_heading_source_ref_by_target:
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_mixed_heading_structural_insert_payload_unresolved",
        family="source_shape_filter",
        reason_code="mixed_heading_structural_insert_payload_missing",
        reason=(
            "UK mixed structural-plus-heading insert target was "
            "normalized to its structural component, but no matching "
            "source-owned structural payload was found; lowering must "
            "not synthesize inserted body text from the heading-qualified "
            "metadata string."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "original_target_ref": mixed_heading_source_ref_by_target[t_str],
            "structural_target_ref": t_str,
        },
    )
    return True


def reject_missing_structural_payload(
    *,
    effect: UKEffectRecord,
    action: str,
    t_str: str,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    use_metadata_fallback: bool,
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        extracted_el is None
        and action in ("replace", "insert")
        and not extracted_text
        and not use_metadata_fallback
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_missing_structural_payload_rejected",
        family="source_pathology_filter",
        reason_code="missing_extracted_payload",
        reason=(
            "UK structural effect has no extracted source payload; "
            "lowering cannot emit an empty replace or insert without "
            "risking destructive replay"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"target_ref": t_str, "action": action},
    )
    return True


def reject_non_substantive_structural_payload(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    t_str: str,
    payload_node_mut: Any,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (curr_action in ("insert", "replace") and _is_non_substantive_structural_payload(payload_node_mut)):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_non_substantive_payload_rejected",
        family="source_pathology_filter",
        reason_code="non_substantive_structural_payload",
        reason=(
            "UK structural effect payload contains only numbering "
            "or dot leaders, so replaying it would create a bogus "
            "legal unit"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "action": curr_action,
            "payload_kind": str(payload_node_mut.kind) if payload_node_mut is not None else "",
        },
    )
    return True


def reject_broad_schedule_flat_replace_payload(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    t_str: str,
    target: LegalAddress,
    payload_node_mut: Any,
    actual_el: Optional[ET._Element],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    if not (
        curr_action == "replace"
        and _is_broad_schedule_flat_replace_payload(
            target=target,
            payload_node=payload_node_mut,
            actual_source_el=actual_el,
        )
    ):
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_broad_schedule_flat_payload_rejected",
        family="payload_coverage_filter",
        reason_code="broad_schedule_or_part_replace_payload_undercovered",
        reason=(
            "UK structural replace targets a whole schedule or schedule part, "
            "but the extracted source payload is only flat text and does not "
            "claim the target's descendant structure."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "target": str(target),
            "payload_kind": str(payload_node_mut.kind),
            "payload_label": str(payload_node_mut.label or ""),
            "payload_text_preview": " ".join((payload_node_mut.text or "").split())[:240],
        },
    )
    return True


_UK_BODY_SECTION_LIKE_KINDS = frozenset({"section", "article", "rule", "regulation"})

# D1 (#211/#219): whole-provision REPLACE targets that a foreign-payload clobber
# would otherwise smuggle the entire affecting schedule text onto. A bare
# section or subsection leaf (``target.special is None``) with no facet.
_UK_WHOLE_PROVISION_REPLACE_LEAF_KINDS = frozenset({"section", "subsection"})

_UK_EFFECT_UNTYPED_FOREIGN_PAYLOAD_WHOLE_PROVISION_REPLACE_RULE_ID = (
    "uk_effect_untyped_foreign_payload_whole_provision_replace_rejected"
)


def reject_untyped_foreign_payload_whole_provision_replace(
    *,
    effect: UKEffectRecord,
    effect_type: str,
    action: str,
    t_str: str,
    target: LegalAddress,
    structural_extraction_found_source_node: bool,
    source_structural_payload_matches_target: bool,
    source_routes_to_text_patch: bool = False,
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    """Block untyped foreign-payload whole-provision REPLACE clobbers (D1).

    UK effect feeds carry untyped (``Type=""``) rows whose action can only be
    INFERRED from the affecting-source drafting verbs. When such a row is an
    inferred whole-section/subsection REPLACE and the structural-payload
    extraction found NO source node matching the target
    (``structural_extraction_found_source_node`` False, so
    ``source_structural_payload_matches_target`` is necessarily False), the
    downstream ``infer_source_payload_from_target`` fallback reuses the ENTIRE
    affecting schedule ``extracted_text`` as the section body — e.g. CRoW 2000
    Sch. 9 para. 1 substituting s. 28 of the Wildlife & Countryside Act 1981 (a
    DIFFERENT act) lowered as a ~41 kB flat payload identically onto NPACA 1949
    ss. 16/103/106/107, deleting every real subsection eId.

    The source carries no payload FOR this target, so replacing a live provision
    with the affecting schedule text is definitionally a clobber. Genuine
    untyped substitutions that DO carry a target-matching source node
    (``source_structural_payload_matches_target`` True, e.g. NPACA s. 20(2)/(3))
    are left untouched — those never reach this gate because a source node was
    found. Sibling of ``uk_effect_application_modification_table_rejected``
    (family ``applicability_scope``): the untyped foreign-payload row is an
    application/modification-adjacent artefact the feed exposes without a
    target-owned structural payload.

    FALSE-POSITIVE EXCLUSION (``source_routes_to_text_patch``): an untyped row
    whose extracted source parses into text-patch fragments (``after "X" there
    is inserted "Y"`` / ``for "X" substitute "Y"`` quoted-anchor word edits) is
    NOT a whole-body clobber. Its inferred ``action="replace"`` is a placeholder
    the downstream text-fragment lane splits into TEXT_PATCH ops (e.g. an
    empty-type compound quoted-anchor word-insert on a bare subsection). Those
    rows never reuse the whole affecting body — only rows whose extracted text
    parses into ZERO fragments hit the ``infer_source_payload_from_target``
    whole-body fallback that produces the clobber. The genuine 41 kB clobbers
    (NPACA ss. 16/103/106/107) parse to zero fragments; the caller passes True
    here for any row the text-patch lane would consume, and the guard abstains.
    """
    if action != "replace":
        return False
    # Untyped feed row: only rows with no effect type reach the source-verb
    # inference path (``_uk_effect_type_action`` returns None → inferred action).
    if str(effect_type or "").strip() != "":
        return False
    # Text-patch-eligible rows route to ``lower_uk_text_fragment_rewrite`` (their
    # ``replace`` action is a placeholder); they are not whole-body clobbers.
    if source_routes_to_text_patch:
        return False
    # A source-matching structural node WAS found → not a foreign-payload
    # clobber (this is the s. 20(2)/(3) legitimate-substitution guard).
    if structural_extraction_found_source_node or source_structural_payload_matches_target:
        return False
    # Whole-provision target only: a bare section/subsection leaf with no facet.
    # Deeper leaves (paragraph/subparagraph/item) and heading/other facets carry
    # their own narrower lowering lanes and are out of D1's scope.
    if target.special is not None:
        return False
    leaf_kind = (_addr_leaf_kind(target) or "").lower()
    if leaf_kind not in _UK_WHOLE_PROVISION_REPLACE_LEAF_KINDS:
        return False
    if _addr_container(target) == "schedule":
        return False
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id=_UK_EFFECT_UNTYPED_FOREIGN_PAYLOAD_WHOLE_PROVISION_REPLACE_RULE_ID,
        family="applicability_scope",
        reason_code="untyped_foreign_payload_whole_provision_replace_clobber",
        reason=(
            "UK untyped (Type=\"\") feed row inferred a whole-provision REPLACE, "
            "but the affecting source carries no structural node matching the "
            "target; lowering would reuse the entire affecting schedule text as "
            "the provision body, clobbering every real descendant eId. The "
            "source carries no payload FOR this target, so the replacement is "
            "definitionally a clobber and is rejected."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "target": str(target),
            "target_leaf_kind": leaf_kind,
            "effect_type": str(effect_type or ""),
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.REJECT,
        },
    )
    return True


def reject_body_section_replace_with_unmatched_schedule_payload(
    *,
    effect: UKEffectRecord,
    curr_action: Optional[str],
    t_str: str,
    target: LegalAddress,
    payload_node_mut: Any,
    actual_el: Optional[ET._Element],
    extracted_el: Optional[ET._Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> bool:
    """Reject replacing a body section with a schedule that does not contain it.

    UK effect feeds sometimes expose a whole SI schedule as the source payload for
    an "applied (with modifications)" row against one or more body sections (e.g.
    s. 2 and s. 4 of the Land Compensation Act 1961 being given the entire text of
    Schedule 2 to the Contaminated Land Regulations 2000).  The schedule's
    paragraphs are not body sections: replaying a schedule-paragraph 4 as section 4
    destroys the target section and blocks later amendments that depend on it.

    A schedule genuinely replaces a section only when it carries a structural
    child whose parsed kind is section-like (Section, Article, Rule) and whose
    label matches the target.  Schedule-paragraph (P1) children with matching
    numbers are not accepted as proxies for body sections.
    """
    if curr_action != "replace":
        return False
    if _addr_container(target) == "schedule":
        return False
    leaf_kind = (_addr_leaf_kind(target) or "").lower()
    if leaf_kind not in _UK_BODY_SECTION_LIKE_KINDS:
        return False
    if actual_el is None or _tag(actual_el) != "Schedule":
        return False
    if payload_node_mut is None or str(payload_node_mut.kind).lower() != "schedule":
        return False

    target_label = _addr_leaf_label(target) or ""
    target_clean = _clean_num(target_label)
    if not target_clean:
        return False

    def _has_section_like_child(node: Any) -> bool:
        for child in getattr(node, "children", ()):
            child_kind = str(child.kind).lower()
            child_label = _clean_num(str(child.label or ""))
            if child_kind in _UK_BODY_SECTION_LIKE_KINDS and child_label == target_clean:
                return True
            if _has_section_like_child(child):
                return True
        return False

    if _has_section_like_child(payload_node_mut):
        return False

    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_body_section_replace_schedule_unmatched_rejected",
        family="payload_coverage_filter",
        reason_code="schedule_payload_lacks_target_section_like_unit",
        reason=(
            "UK structural replace targets a body section but the extracted "
            "schedule payload contains no Section/P1/Article/Rule that would be "
            "parsed as a body section with a matching label; the effect is not a "
            "genuine section replacement and would destroy the target carrier."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": t_str,
            "target": str(target),
            "target_leaf_kind": leaf_kind,
            "target_leaf_label": target_label,
            "payload_tag": _tag(actual_el),
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.REJECT,
        },
    )
    return True
