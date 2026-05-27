"""Post-loop helpers for UK effect lowering."""

from __future__ import annotations

from dataclasses import dataclass
import re
import xml.etree.ElementTree as ET
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import StructuralAction
from lawvm.uk_legislation.canonicalize import canonicalize_uk_address
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.provision_extractor import (
    _instruction_text_before_amendment_container,
)
from lawvm.uk_legislation.source_parent_payloads import (
    UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
    UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID as _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID,
)
from lawvm.uk_legislation.source_context import _source_ancestor_chain
from lawvm.uk_legislation.source_definition_fragments import (
    _looks_like_appropriate_place_definition_entry_insert_text,
    _source_parent_appropriate_place_definition_entry_insert_context,
)
from lawvm.uk_legislation.source_payload_elaboration import (
    _extract_crossheading_payload_from_extracted,
)
from lawvm.uk_legislation.target_parser import _parse_affected_target
from lawvm.uk_legislation.target_anchors import (
    _source_after_insertion_anchor,
    _source_before_insertion_anchor,
)
from lawvm.uk_legislation.witness_builders import (
    _uk_target_expansion_witness,
    _uk_temporal_group_id,
)
from lawvm.uk_legislation.witness_sidecars import (
    _payload_with_rewrite_witness,
    _uk_lowered_op_provenance_tags,
)
from lawvm.uk_legislation.witnesses import (
    UKEffectWitness,
    UKLoweredOperationWitness,
    UKProvisionExtractionWitness,
)


@dataclass(frozen=True)
class UKInsertionAnchorContext:
    preceding_eid: Optional[str]
    preceding_eid_source: str
    following_eid: Optional[str]
    following_eid_source: Optional[str]
    used_chained_insert_anchor: bool


@dataclass(frozen=True)
class UnloweredOverlapSourceShapeClassification:
    rule_id: str
    family: str
    reason_code: str
    reason: str


def _looks_like_appropriate_place_insert_text(text: str) -> bool:
    normalized = " ".join((text or "").split())
    if not normalized:
        return False
    return bool(
        re.search(
            r"(?:\bat\s+(?:an?|the)\s+appropriate\s+place\b.*\binsert(?:ed)?\b|"
            r"\binsert(?:ed)?\s+at\s+(?:an?|the)\s+appropriate\s+place\b)",
            normalized,
            re.I,
        )
    )


def resolve_uk_insertion_anchor_context(
    *,
    effect: UKEffectRecord,
    curr_action: str,
    target: LegalAddress,
    chained_insert_preceding_eid: Optional[str],
    chained_insert_preceding_eid_source: str,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
) -> UKInsertionAnchorContext:
    preceding_eid = None
    preceding_eid_source = "effect_comments_after_clause"
    used_chained_insert_anchor = False
    if chained_insert_preceding_eid:
        preceding_eid = chained_insert_preceding_eid
        preceding_eid_source = chained_insert_preceding_eid_source
        used_chained_insert_anchor = True

    source_anchor_text = ""
    if extracted_el is not None:
        source_anchor_text = _instruction_text_before_amendment_container(extracted_el) or (
            extracted_text or ""
        )
    source_preceding_anchor = _source_after_insertion_anchor(
        source_anchor_text,
        target,
    )
    if source_preceding_anchor.eid and not preceding_eid:
        preceding_eid = source_preceding_anchor.eid
        preceding_eid_source = source_preceding_anchor.source or preceding_eid_source

    following_eid = None
    following_eid_source = None
    if curr_action == "insert":
        following_anchor = _source_before_insertion_anchor(
            source_anchor_text,
            target,
        )
        following_eid = following_anchor.eid
        following_eid_source = following_anchor.source

    if "after " in effect.comments.lower():
        rel_m = re.search(
            r"after (?:paragraph|section|ss\.|s\.)\s?\(?([0-9a-zA-Z]+)\)?",
            effect.comments,
            re.I,
        )
        if rel_m and not preceding_eid:
            num = rel_m.group(1)
            preceding_eid = (
                f"p1-{num}" if "paragraph" in effect.comments.lower() else f"section-{num}"
            )

    return UKInsertionAnchorContext(
        preceding_eid=preceding_eid,
        preceding_eid_source=preceding_eid_source,
        following_eid=following_eid,
        following_eid_source=following_eid_source,
        used_chained_insert_anchor=used_chained_insert_anchor,
    )


_UK_OVERLAP_ACTION_WORD_RE = re.compile(
    r"\b(?:insert|inserted|inserting|omit|omitted|omitting|repeal|repealed|"
    r"substitute|substituted|substituting|replace|replaced|replacing|"
    r"amend|amended|amending|change|changed|changing)\b",
    re.I,
)


def _source_payload_parent_instruction_context(
    *,
    extracted_el: Optional[ET.Element],
    source_root: Optional[ET.Element],
    extracted_text: Optional[str],
) -> dict[str, str]:
    """Return nearest source parent instruction context for payload-only failures."""
    payload_text = " ".join(str(extracted_text or "").split()).strip()
    if extracted_el is None or source_root is None or not payload_text:
        return {}
    ancestors = _source_ancestor_chain(source_root, extracted_el)
    for ancestor_index, ancestor in enumerate(ancestors):
        instruction_text = " ".join(
            _instruction_text_before_amendment_container(ancestor).split()
        ).strip()
        context_text = instruction_text or " ".join(" ".join(ancestor.itertext()).split()).strip()
        if not context_text or context_text == payload_text:
            continue
        if _UK_OVERLAP_ACTION_WORD_RE.search(context_text) is None:
            continue
        source_parent_id = str(ancestor.get("id") or ancestor.get("eId") or "")
        if not source_parent_id:
            source_parent_id = next(
                (
                    str(candidate.get("id") or candidate.get("eId"))
                    for candidate in ancestors[ancestor_index + 1 :]
                    if candidate.get("id") or candidate.get("eId")
                ),
                "",
            )
        return {
            "source_parent_id": source_parent_id,
            "source_parent_context_preview": context_text[:500],
        }
    return {}


def _unlowered_overlap_source_shape_classification(
    extracted_text: Optional[str],
    original_targets_str: list[str],
) -> UnloweredOverlapSourceShapeClassification:
    text = " ".join(str(extracted_text or "").split()).strip()
    lowered = text.lower()
    target_surface = " ".join(str(target or "") for target in original_targets_str).lower()
    source_or_target_names_table = "table" in lowered or "table" in target_surface
    if source_or_target_names_table and re.search(
        r"\b(?:at\s+the\s+end|after\s+the\s+final\s+entry|before\s+the\s+entry\s+for|"
        r"after\s+the\s+paragraph\s+at\s+the\s+end\s+of\s+the\s+table)\b"
        r".*\binsert(?:ed)?\b",
        lowered,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_table_entry_placement_insert_rejected",
            "source_table_elaboration",
            "table_entry_insert_requires_row_or_cell_placement_model",
            (
                "UK source inserts material into a table entry/list position; "
                "lowering requires an owned row/cell placement model and must "
                "not append the payload to the broad table carrier."
            ),
        )
    if re.search(
        r"\bin\s+the\s+definition\s+of\s+[“\"'‘][^”\"'’]+[”\"'’].*"
        r"\bafter\s+paragraph\s*\([0-9A-Za-z]+\).*"
        r"\bbefore\s+the\s+[“\"'‘](?:and|or)[”\"'’]\s+at\s+the\s+end\b.*"
        r"\binsert(?:ed)?\b",
        text,
        re.I,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_definition_child_structural_insert_rejected",
            "source_payload_elaboration",
            "definition_child_structural_insert_requires_child_and_tail_claim",
            (
                "UK source inserts a structural definition child and explicitly "
                "references the existing child-tail connector; lowering blocks "
                "until a compiler or claim owns the inserted child shape and "
                "connector boundary."
            ),
        )
    if re.search(
        r"\b(?:after|before)\s+(?:paragraph|sub-?paragraph|subsection)\s*"
        r"\([0-9A-Za-z]+\)\s+insert(?:\b|\s*[—-])",
        lowered,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_structural_sibling_insert_rejected",
            "source_payload_elaboration",
            "structural_sibling_insert_requires_owned_parent_anchor_payload",
            (
                "UK source inserts structural siblings after a named child, but "
                "lowering cannot prove the parent, anchor, and inserted child "
                "payload shape; replay must not append the payload to the anchor "
                "or broad target text."
            ),
        )
    if (
        re.search(r"\bomit\s+subsections?\b", lowered)
        and re.search(r"\bwords?\s+from\b", lowered)
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_mixed_structural_text_rewrite_rejected",
            "source_payload_elaboration",
            "mixed_structural_and_text_rewrite_requires_split",
            (
                "UK source combines a structural repeal with a text rewrite in "
                "one instruction; lowering requires an owned split into separate "
                "canonical operations before replay."
            ),
        )
    if (
        re.search(r"\bomit\s+subsections?\b", lowered)
        and re.search(r"\bdefinition\s+in\s+subsection\b", lowered)
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_mixed_structural_definition_repeal_rejected",
            "source_payload_elaboration",
            "mixed_structural_and_definition_repeal_requires_split",
            (
                "UK source combines a structural subsection repeal with a "
                "definition-entry repeal in one instruction; lowering requires "
                "an owned split into separate canonical operations before replay."
            ),
        )
    if (
        re.search(r"\bfor\s+[“\"'‘][^”\"'’]+[”\"'’].*\bsubstitute\b", text, re.I)
        and re.search(
            r"\bincluding\s+in\s+(?:the\s+)?(?:italic\s+)?heading\b",
            lowered,
        )
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_mixed_body_heading_text_substitution_rejected",
            "unsupported_target_facet",
            "mixed_body_heading_text_substitution_requires_split",
            (
                "UK source applies a quoted text substitution to both body text "
                "and a heading facet; lowering requires an owned split so replay "
                "does not silently apply heading claims to the host body text."
            ),
        )
    if source_or_target_names_table and re.search(
        r"\bthis\s+subsection\s+inserted\s+at\s+the\s+end\s+of\s+the\s+"
        r"(?:first|second)\s+column\s+of\s+the\s+table\b",
        lowered,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_table_deictic_this_subsection_insert_rejected",
            "source_table_elaboration",
            "table_deictic_this_subsection_insert_requires_source_context",
            (
                "UK source inserts a deictic reference to 'this subsection' "
                "into a table column; lowering requires source-section context "
                "and owned row/cell placement rather than a broad table text patch."
            ),
        )
    if re.search(r"\bin\s+the\s+specified\s+provisions\b", lowered) and re.search(
        r"\bfor\s+[“\"'‘][^”\"'’]+[”\"'’]\s+\(or\s+[“\"'‘][^”\"'’]+[”\"'’]\)\s+"
        r"substitute\s+[“\"'‘][^”\"'’]+[”\"'’]",
        text,
        re.I,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_multi_enactment_specified_provisions_text_patch_rejected",
            "source_payload_elaboration",
            "multi_enactment_specified_provisions_text_patch_requires_target_row_claim",
            (
                "UK source applies one text substitution across a table/list of "
                "specified provisions and supplies alternate preimages; lowering "
                "requires proving the affected provision is one listed row and "
                "selecting the matching preimage before replay."
            ),
        )
    if re.search(
        r"\bwhere\s+it\s+occurs\s+without\b.*\bsubstitute\b.*\bbut\s+this\s+"
        r"does\s+not\s+apply\b",
        lowered,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_scoped_occurrence_substitution_with_exclusions_rejected",
            "text_rewrite_lowering",
            "scoped_occurrence_substitution_with_exclusions_requires_selector_model",
            (
                "UK source substitutes occurrences only when a negative "
                "left-context condition holds and excludes named provisions; "
                "lowering requires an owned scoped-occurrence selector rather "
                "than all-occurrences replay."
            ),
        )
    if re.match(
        r"^(?:[0-9A-Za-z]+|[ivxlcdm]+)?\s*part\s+\d+\s+amendments?\s+of\b"
        r".*\bcolumn\s+1\b.*\bcolumn\s+2\b",
        lowered,
    ):
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_amendment_table_payload_without_row_context_rejected",
            "source_extraction_context",
            "amendment_table_payload_without_row_context",
            (
                "UK extracted source is an amendment table payload rather than "
                "the specific row for the affected target; lowering blocks "
                "until acquisition/extraction supplies row-level context."
            ),
        )
    if text and _UK_OVERLAP_ACTION_WORD_RE.search(text) is None:
        return UnloweredOverlapSourceShapeClassification(
            "uk_effect_source_payload_without_instruction_context_rejected",
            "source_extraction_context",
            "source_payload_without_instruction_context",
            (
                "UK extracted source appears to be a payload fragment rather "
                "than a complete operative instruction; lowering blocks instead "
                "of replaying the fragment as a broad text patch."
            ),
        )
    return UnloweredOverlapSourceShapeClassification(
        "uk_effect_overlap_substitution_unlowered",
        "lowering_filter",
        "overlap_substitution_parse_failed",
        (
            "UK word-level overlap substitution lowered to no replay operations "
            "because the source instruction could not be parsed into a safe text patch"
        ),
    )


def append_unlowered_overlap_substitution_rejection(
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    effect_type: str,
    original_targets_str: list[str],
    target_candidate_count: int,
    unlowered_overlap_substitution_targets: list[str],
    unlowered_overlap_substitution_reason: str,
    source_root: Optional[ET.Element] = None,
) -> None:
    source_parent_appropriate_place_definition_entry = (
        _source_parent_appropriate_place_definition_entry_insert_context(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
    )
    appropriate_place_definition_entry = (
        _looks_like_appropriate_place_definition_entry_insert_text(extracted_text or "")
        or source_parent_appropriate_place_definition_entry is not None
    )
    appropriate_place_insert = (
        not appropriate_place_definition_entry
        and _looks_like_appropriate_place_insert_text(extracted_text or "")
    )
    if appropriate_place_definition_entry:
        lowering_rule_id = "uk_effect_appropriate_place_definition_entry_insert_rejected"
        family = "lowering_filter"
        reason_code = "appropriate_place_definition_entry_requires_anchor_claim"
        reason = (
            "UK source inserts a definition entry at an appropriate place without "
            "naming an anchor; lowering requires a validated placement claim and "
            "must not infer an insertion point from live text or oracle order."
        )
    elif appropriate_place_insert:
        lowering_rule_id = "uk_effect_appropriate_place_insert_rejected"
        family = "lowering_filter"
        reason_code = "appropriate_place_insert_requires_anchor_claim"
        reason = (
            "UK source inserts material at an appropriate place without naming "
            "an anchor or ordering rule; lowering requires a validated placement "
            "claim and must not infer the insertion point from live text or oracle order."
        )
    elif unlowered_overlap_substitution_reason == "child_qualified_word_omission_target_mismatch":
        lowering_rule_id = "uk_effect_child_qualified_word_omission_target_mismatch_rejected"
        family = "target_resolution_recovery"
        reason_code = "child_qualified_word_omission_target_mismatch"
        reason = (
            "UK source explicitly scopes the quoted word omission to a child "
            "provision that does not match the effect-feed target; lowering "
            "blocks instead of applying a broad quoted-word deletion to the "
            "feed target."
        )
    else:
        source_shape_classification = _unlowered_overlap_source_shape_classification(
            extracted_text,
            original_targets_str,
        )
        lowering_rule_id = source_shape_classification.rule_id
        family = source_shape_classification.family
        reason_code = source_shape_classification.reason_code
        reason = source_shape_classification.reason
        if lowering_rule_id == "uk_effect_overlap_substitution_unlowered":
            reason_code = unlowered_overlap_substitution_reason
    source_payload_parent_context = (
        _source_payload_parent_instruction_context(
            extracted_el=extracted_el,
            source_root=source_root,
            extracted_text=extracted_text,
        )
        if (
            lowering_rule_id
            == "uk_effect_source_payload_without_instruction_context_rejected"
        )
        else {}
    )
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id=lowering_rule_id,
        family=family,
        reason_code=reason_code,
        reason=reason,
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "effect_type_normalized": effect_type,
            "original_affected_provisions": effect.affected_provisions,
            "original_target_candidates": original_targets_str,
            "unlowered_target_candidates": unlowered_overlap_substitution_targets,
            "target_candidate_count": target_candidate_count,
            "parser": "parse_fragment_substitution",
            "placement_family": (
                "appropriate_place_definition_entry_requires_anchor_claim"
                if appropriate_place_definition_entry
                else "appropriate_place_insert_requires_anchor_claim"
                if appropriate_place_insert
                else ""
            ),
            "source_parent_id": (
                source_parent_appropriate_place_definition_entry.get("source_parent_id", "")
                if source_parent_appropriate_place_definition_entry is not None
                else source_payload_parent_context.get("source_parent_id", "")
            ),
            "source_parent_context_preview": (
                source_parent_appropriate_place_definition_entry.get(
                    "source_parent_context_preview", ""
                )
                if source_parent_appropriate_place_definition_entry is not None
                else source_payload_parent_context.get("source_parent_context_preview", "")
            ),
        },
    )


def append_source_parent_at_end_added_observation(
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    source_parent_at_end_added_payload: Optional[dict[str, Any]],
) -> None:
    if source_parent_at_end_added_payload is None:
        return
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id=_UK_SOURCE_PARENT_AT_END_ADDED_PAYLOAD_RULE_ID,
        family="source_context_elaboration",
        reason_code="payload_fragment_combined_with_parent_at_end_added",
        reason=(
            "UK effect feed row has no effect type and the extracted "
            "BlockAmendment contains only an inserted structural payload, "
            "but the source-local parent instruction explicitly adds it at "
            "the end of the affected provision; lowering keeps the metadata "
            "target and payload identity as one source-owned insert."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            key: value
            for key, value in source_parent_at_end_added_payload.items()
            if key != "rule_id"
        },
    )


def append_no_targets_rejection(
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
) -> None:
    _append_uk_effect_lowering_rejection(
        lowering_rejections_out,
        rule_id="uk_effect_lowering_no_targets_rejected",
        family="target_resolution_recovery",
        reason_code="no_affected_targets",
        reason=(
            "UK effect lowered to no replay operations because affected "
            "provisions produced no target candidates"
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={"original_affected_provisions": effect.affected_provisions},
    )


def append_chained_insertion_anchor_observation(
    lowering_rejections_out: Optional[list[dict[str, Any]]],
    *,
    effect: UKEffectRecord,
    target_ref: str,
    target: LegalAddress,
    preceding_eid: Optional[str],
    preceding_eid_source: str,
    used_chained_insert_anchor: bool,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
) -> None:
    if not used_chained_insert_anchor:
        return
    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_chained_insertion_anchor_lowered",
        family="target_resolution_recovery",
        reason_code="same_effect_insert_targets_ordered_by_prior_generated_target",
        reason=(
            "UK effect expands one insertion instruction into multiple sibling "
            "insert operations; later operations are anchored after the prior "
            "generated target rather than the original source anchor."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "preceding_eid": preceding_eid,
            "preceding_eid_source": preceding_eid_source,
        },
    )


def build_crossheading_insert_ops(
    *,
    effect: UKEffectRecord,
    extracted_el: Optional[ET.Element],
    sequence: int,
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
) -> list[LegalOperation]:
    crossheading_payload = _extract_crossheading_payload_from_extracted(
        effect.affected_provisions,
        extracted_el,
    )
    if crossheading_payload is None:
        return []

    crossheading_target = canonicalize_uk_address(LegalAddress(path=(("crossheading", ""),)))
    crossheading_target_witness = _uk_target_expansion_witness(
        "cross-heading",
        ["cross-heading"],
    )
    crossheading_lowered_witness = UKLoweredOperationWitness(
        op_id=f"{effect.effect_id}_crossheading",
        sequence=sequence,
        action=StructuralAction.INSERT,
        target=crossheading_target,
        payload=crossheading_payload,
        source=OperationSource(
            statute_id=effect.affecting_act_id,
            title=effect.affecting_title,
            effective=effect_witness.applicability.effective_date or "",
            raw_text=extraction_witness.extracted_text,
        ),
        effect_witness=effect_witness,
        extraction_witness=extraction_witness,
        target_expansion_witness=crossheading_target_witness,
        text_rewrite_witness=None,
        insertion_anchor_witness=None,
    )
    return [
        LegalOperation(
            op_id=crossheading_lowered_witness.op_id,
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=crossheading_target,
            payload=_payload_with_rewrite_witness(
                crossheading_payload,
                crossheading_lowered_witness,
            ),
            source=crossheading_lowered_witness.source,
            group_id=_uk_temporal_group_id(effect),
            provenance_tags=_uk_lowered_op_provenance_tags(crossheading_lowered_witness),
        )
    ]


def build_trailing_repeal_ops(
    *,
    effect: UKEffectRecord,
    sequence: int,
    trailing_repeal_refs: list[str],
    effect_witness: UKEffectWitness,
    extraction_witness: UKProvisionExtractionWitness,
    original_targets_str: list[str],
    source_parent_substitution_range_payload: Optional[dict[str, Any]],
) -> list[LegalOperation]:
    src = OperationSource(
        statute_id=effect.affecting_act_id,
        title=effect.affecting_title,
        effective=effect_witness.applicability.effective_date or "",
        raw_text=extraction_witness.extracted_text,
    )
    ops: list[LegalOperation] = []
    for repeal_idx, repeal_ref in enumerate(trailing_repeal_refs):
        repeal_target = _parse_affected_target(repeal_ref)
        target_expansion_witness = _uk_target_expansion_witness(
            repeal_ref,
            [repeal_ref],
            original_targets_str=original_targets_str,
        )
        lowered_witness = UKLoweredOperationWitness(
            op_id=f"{effect.effect_id}_repeal_{repeal_idx}",
            sequence=sequence,
            action=StructuralAction.REPEAL,
            target=repeal_target,
            payload=None,
            source=src,
            effect_witness=effect_witness,
            extraction_witness=extraction_witness,
            target_expansion_witness=target_expansion_witness,
            text_rewrite_witness=None,
            insertion_anchor_witness=None,
        )
        ops.append(
            LegalOperation(
                op_id=lowered_witness.op_id,
                sequence=lowered_witness.sequence,
                action=lowered_witness.action,
                target=lowered_witness.target,
                payload=None,
                source=lowered_witness.source,
                group_id=_uk_temporal_group_id(effect),
                provenance_tags=_uk_lowered_op_provenance_tags(lowered_witness),
                witness_rule_id=(
                    _UK_SOURCE_PARENT_SUBSTITUTION_RANGE_PAYLOAD_RULE_ID
                    if source_parent_substitution_range_payload is not None
                    else None
                ),
            )
        )
    return ops
