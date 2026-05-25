"""UK text-rewrite fragment provenance helpers."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _addr_container
from lawvm.uk_legislation.effects import UKEffectRecord
from lawvm.uk_legislation.lowering_records import (
    _append_uk_effect_lowering_observation,
    _append_uk_effect_lowering_rejection,
)
from lawvm.uk_legislation.nlp_parser import (
    US,
    UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID,
    UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID,
    _COMPOUND_LETTERED_TEXT_PATCH_RULE_ID,
)
from lawvm.uk_legislation.provenance_notes import NOTE_FRAGMENT_SUB, NOTE_TEXT_REWRITE_RULE
from lawvm.uk_legislation.source_amendment_program_fragments import (
    UK_AMENDMENT_PROGRAM_INSERTED_PARENT_CHILD_INSERT_RULE_ID,
)
from lawvm.uk_legislation.witness_sidecars import _witness_for_op


UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID = "uk_effect_multi_quoted_word_repeal_text_patches"
UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID = (
    "uk_effect_metadata_carried_quoted_words_repeal_text_patch"
)
UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID = (
    "uk_effect_metadata_carried_after_ordinal_insert_text_patch"
)
UK_CONTEXTUAL_ADJACENT_WORD_OMIT_RULE_ID = "uk_effect_contextual_adjacent_word_omit_text_patch"
UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID = "uk_effect_range_to_end_there_is_substituted_text_patch"
UK_AFTER_ANCHOR_TO_END_OMISSION_RULE_ID = "uk_effect_after_anchor_to_end_omission_text_patch"
UK_RANGE_INDEPENDENT_END_OCCURRENCE_REPEAL_RULE_ID = (
    "uk_effect_range_independent_end_occurrence_repeal_text_patch"
)
UK_SOURCE_CARRIED_CHILD_TAIL_REPEAL_RULE_ID = "uk_effect_source_carried_child_tail_repeal_text_patch"
UK_SOURCE_CARRIED_FOLLOWING_WORDS_REPEAL_RULE_ID = (
    "uk_effect_source_carried_following_words_repeal_text_patch"
)
UK_SOURCE_CARRIED_SUBPARAGRAPH_TAIL_REPEAL_RULE_ID = (
    "uk_effect_source_carried_subparagraph_tail_repeal_text_patch"
)
UK_SOURCE_CARRIED_CHILD_TAIL_SUBSTITUTION_RULE_ID = (
    "uk_effect_source_carried_child_tail_substitution_text_patch"
)
UK_SOURCE_CARRIED_MULTI_SUBUNIT_REPEAL_RULE_ID = (
    "uk_effect_source_carried_multi_subunit_repeal_text_patch"
)
UK_DEFINITION_CHILD_AND_TAIL_SUBSTITUTION_RULE_ID = (
    "uk_effect_definition_child_and_tail_substitution_text_patch"
)
UK_AMENDMENT_INSERTED_TEXT_SUBSTITUTION_RULE_ID = (
    "uk_effect_amendment_inserted_text_substitution_text_patch"
)
UK_RANGE_INDEPENDENT_END_OCCURRENCE_RULE_ID = (
    "uk_effect_range_independent_end_occurrence_text_patch"
)
UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID = (
    "uk_effect_source_range_definition_entry_insert_text_patch"
)
UK_COMPOUND_LETTERED_TEXT_PATCH_RULE_ID = _COMPOUND_LETTERED_TEXT_PATCH_RULE_ID

UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS = frozenset(
    {
        "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        "uk_effect_after_quoted_anchor_each_occasion_insert_text_patch",
        "uk_effect_all_occurrences_substitution_text_patch",
        "uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch",
        "uk_effect_respectively_all_occurrences_substitution_text_patch",
        "uk_effect_source_parent_grouped_after_anchor_all_occurrences_insert_text_patch",
        "uk_effect_wherever_occurring_substitution_text_patch",
    }
)


@dataclass(frozen=True)
class UKLabeledChildEndRangeLowering:
    primary: dict[str, Any]
    curr_action: Optional[str]
    skip_effect: bool


def _multi_quoted_word_repeal_fragments(
    *,
    extracted_text: Optional[str],
    effect_type: str,
) -> tuple[dict[str, str], ...]:
    norm_effect_type = (effect_type or "").strip().lower()
    if norm_effect_type not in {"words repealed", "word repealed", "words omitted", "word omitted"}:
        return ()
    text = " ".join((extracted_text or "").split()).strip()
    if not text:
        return ()
    if not re.search(r"\bthe\s+words?\b", text, flags=re.I):
        return ()
    if not re.search(r"\b(?:are|is)\s+(?:repealed|omitted)\b", text, flags=re.I):
        return ()
    quoted = tuple(
        match.group("curly") if match.group("curly") is not None else match.group("double")
        for match in re.finditer(r"(?:\u201c(?P<curly>.*?)\u201d|\"(?P<double>.*?)\")", text)
    )
    quoted = tuple(" ".join(fragment.split()).strip() for fragment in quoted if " ".join(fragment.split()).strip())
    if len(quoted) < 2:
        return ()
    return tuple(
        {
            "original": fragment,
            "replacement": "",
            "rule_id": UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID,
        }
        for fragment in quoted
    )


def _fragment_substitution(op: LegalOperation) -> Optional[list]:
    """Return typed fragment-substitution data from the lowered witness."""
    witness = _witness_for_op(op)
    text_rewrite_witness = getattr(witness, "text_rewrite_witness", None)
    if text_rewrite_witness is not None and getattr(text_rewrite_witness, "alternatives", None):
        fragments: list[dict[str, str]] = []
        for original, replacement in text_rewrite_witness.alternatives:
            if not original:
                continue
            fragment = {"original": original, "replacement": replacement}
            if text_rewrite_witness.occurrence:
                fragment["occurrence"] = str(text_rewrite_witness.occurrence)
            if text_rewrite_witness.end_occurrence:
                fragment["end_occurrence"] = str(text_rewrite_witness.end_occurrence)
            fragments.append(fragment)
        return fragments
    for note in getattr(op, "provenance_tags", ()) or ():
        if not str(note).startswith(NOTE_FRAGMENT_SUB):
            continue
        try:
            payload = json.loads(str(note)[len(NOTE_FRAGMENT_SUB) :])
        except json.JSONDecodeError:
            return None
        if isinstance(payload, list):
            fragments: list[dict[str, str]] = []
            for item in payload:
                if not isinstance(item, dict) or not str(item.get("original") or ""):
                    continue
                fragment = {
                    "original": str(item.get("original") or ""),
                    "replacement": str(item.get("replacement") or ""),
                }
                if item.get("occurrence"):
                    fragment["occurrence"] = str(item.get("occurrence") or "")
                if item.get("end_occurrence"):
                    fragment["end_occurrence"] = str(item.get("end_occurrence") or "")
                fragments.append(fragment)
            return fragments
    return None


def _text_rewrite_rule_ids_for_op(op: LegalOperation) -> tuple[str, ...]:
    rule_ids: list[str] = []
    witness = _witness_for_op(op)
    text_rewrite_witness = getattr(witness, "text_rewrite_witness", None)
    rewrite_source = getattr(text_rewrite_witness, "rewrite_source", "")
    if rewrite_source:
        rule_ids.append(str(rewrite_source))
    for note in getattr(op, "provenance_tags", ()) or ():
        note_text = str(note)
        if not note_text.startswith(NOTE_TEXT_REWRITE_RULE):
            continue
        rule_id = note_text[len(NOTE_TEXT_REWRITE_RULE) :]
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)
    return tuple(rule_ids)


def _fragment_rule_ids(fragment_subs: Optional[list]) -> tuple[str, ...]:
    if not fragment_subs:
        return ()
    rule_ids: list[str] = []
    for item in fragment_subs:
        if not isinstance(item, dict):
            continue
        rule_id = str(item.get("rule_id") or "")
        if rule_id and rule_id not in rule_ids:
            rule_ids.append(rule_id)
    return tuple(rule_ids)


def append_all_occurrences_text_rewrite_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    for rewrite_rule_id in _fragment_rule_ids(fragment_subs):
        if rewrite_rule_id not in UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS:
            continue
        rewrite_fragments = [
            item
            for item in fragment_subs or []
            if str(item.get("rule_id") or "") == rewrite_rule_id
        ]
        if not rewrite_fragments:
            rewrite_fragments = [
                {
                    "original": op_text_match,
                    "replacement": op_text_replacement,
                    "occurrence": str(op_text_occurrence),
                }
            ]
        for rewrite_fragment in rewrite_fragments:
            _append_uk_effect_lowering_observation(
                lowering_rejections_out,
                rule_id=rewrite_rule_id,
                family="text_rewrite_lowering",
                reason_code="explicit_all_occurrences_text_patch",
                reason=(
                    "UK effect source explicitly applies a word-level "
                    "text rewrite wherever/in each place it occurs; "
                    "lowering preserves that as an all-occurrences "
                    "text patch scoped to the affected target."
                ),
                effect=effect,
                extracted_el=extracted_el,
                extracted_text=extracted_text,
                detail={
                    "target_ref": target_ref,
                    "target": str(target),
                    "text_match": str(rewrite_fragment.get("original") or ""),
                    "replacement": str(rewrite_fragment.get("replacement") or ""),
                    "occurrence": int(str(rewrite_fragment.get("occurrence") or "0") or "0"),
                },
            )


def append_basic_text_rewrite_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    rule_ids = _fragment_rule_ids(fragment_subs)
    if UK_CONTEXTUAL_ADJACENT_WORD_OMIT_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_CONTEXTUAL_ADJACENT_WORD_OMIT_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_contextual_adjacent_word_omission_lowered",
            reason=(
                "UK source text explicitly omits a quoted word following "
                "a named local child; lowering preserves that child anchor "
                "instead of deleting the quoted word from the whole parent."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
            },
        )
    if UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_RANGE_TO_END_THERE_IS_SUBSTITUTED_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_range_to_end_there_is_substituted_text_patch",
            reason=(
                "UK source text uses the drafting form 'there is substituted' "
                "for a word-level range ending at the end of the target; lowering "
                "preserves that as a bounded TEXT_FROM_*_TO_END text patch."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
            },
        )
    if UK_AFTER_ANCHOR_TO_END_OMISSION_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_AFTER_ANCHOR_TO_END_OMISSION_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_after_anchor_to_end_omission",
            reason=(
                "UK source text explicitly omits the words after a quoted "
                "anchor; lowering preserves that as a bounded TEXT_AFTER_*_TO_END "
                "deletion scoped to the affected target."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    if UK_RANGE_INDEPENDENT_END_OCCURRENCE_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_RANGE_INDEPENDENT_END_OCCURRENCE_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_range_repeal_end_occurrence",
            reason=(
                "UK source text explicitly repeals a quoted word range and "
                "qualifies the end anchor by ordinal occurrence; lowering "
                "preserves the independent end occurrence instead of broadening "
                "the range."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "end_occurrence": op_text_end_occurrence,
            },
        )
    if UK_COMPOUND_LETTERED_TEXT_PATCH_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_COMPOUND_LETTERED_TEXT_PATCH_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="compound_lettered_text_patch_instruction_split",
            reason=(
                "UK source text carries multiple lettered word-level amendment "
                "instructions in one source paragraph; lowering emits one "
                "bounded text patch per extracted lettered instruction instead "
                "of treating the paragraph as one broad replacement."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "fragment_count": len(fragment_subs or []),
            },
        )
    if UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_METADATA_CARRIED_QUOTED_WORDS_REPEAL_RULE_ID,
            family="effect_feed_elaboration",
            reason_code="metadata_action_source_quoted_words_repeal",
            reason=(
                "The official UK effect feed supplies the word-level repeal "
                "action and affected target, while the source row carries the "
                "quoted words and local target context; lowering combines those "
                "source surfaces into a bounded TEXT_REPEAL instead of treating "
                "the row as a standalone amendment instruction."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
            },
        )
    if UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_METADATA_CARRIED_AFTER_ORDINAL_INSERT_RULE_ID,
            family="effect_feed_elaboration",
            reason_code="metadata_action_source_after_ordinal_insert",
            reason=(
                "The official UK effect feed supplies the word-level insertion "
                "action while the source row carries the quoted anchor, ordinal "
                "occurrence, and quoted insertion payload; lowering combines "
                "those source surfaces into a bounded TEXT_REPLACE."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
            },
        )
    if UK_DEFINITION_CHILD_AND_TAIL_SUBSTITUTION_RULE_ID in rule_ids:
        primary = next(
            (
                fragment
                for fragment in fragment_subs or []
                if str(fragment.get("rule_id") or "")
                == UK_DEFINITION_CHILD_AND_TAIL_SUBSTITUTION_RULE_ID
            ),
            {},
        )
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_DEFINITION_CHILD_AND_TAIL_SUBSTITUTION_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="definition_child_and_tail_substitution_lowered",
            reason=(
                "UK source text explicitly substitutes a named definition child "
                "and the connector at the end of that child; lowering preserves "
                "the claim as a bounded definition-child replacement instead of "
                "rewriting the whole definition or subsection."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
                "tail_connector": str(primary.get("tail_connector") or ""),
            },
        )
    if UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID in rule_ids:
        fragments = [
            fragment
            for fragment in fragment_subs or []
            if str(fragment.get("rule_id") or "")
            == UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID
        ]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_ordinal_places_after_anchor_insert",
            reason=(
                "UK source text explicitly inserts quoted words after a quoted "
                "anchor in one or more named ordinal places; lowering preserves "
                "each ordinal as a bounded text patch scoped to the affected target."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrences": [
                    int(str(fragment.get("occurrence") or "0") or "0")
                    for fragment in fragments
                ],
            },
        )
    if UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID in rule_ids:
        fragments = [
            fragment
            for fragment in fragment_subs or []
            if str(fragment.get("rule_id") or "")
            == UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID
        ]
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_ordinal_occurrences_quoted_substitution",
            reason=(
                "UK source text explicitly substitutes quoted words at one or "
                "more named ordinal occurrences; lowering preserves each "
                "ordinal as a bounded text patch scoped to the affected target."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrences": [
                    int(str(fragment.get("occurrence") or "0") or "0")
                    for fragment in fragments
                ],
            },
        )


def append_source_carried_tail_rewrite_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    primary: dict[str, Any],
    op_text_match: Optional[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    rule_ids = _fragment_rule_ids(fragment_subs)
    if UK_SOURCE_CARRIED_CHILD_TAIL_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_CARRIED_CHILD_TAIL_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_child_tail_repeal_lowered",
            reason=(
                "UK source text explicitly repeals the words following "
                "a named paragraph inside the affected subsection; lowering "
                "preserves that as a bounded child-tail text selector instead "
                "of deleting from the whole parent."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "source_anchor_child_label": str(primary.get("source_anchor_child_label") or ""),
                "source_subsection_label": str(primary.get("source_subsection_label") or ""),
            },
        )


def append_source_carried_substitution_rewrite_observations(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    primary: dict[str, Any],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> None:
    rule_ids = _fragment_rule_ids(fragment_subs)
    if UK_SOURCE_CARRIED_CHILD_TAIL_SUBSTITUTION_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_CARRIED_CHILD_TAIL_SUBSTITUTION_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_child_tail_substitution_lowered",
            reason=(
                "UK source text explicitly substitutes the words after "
                "a named paragraph inside the affected subsection; lowering "
                "preserves that as a bounded child-tail text selector instead "
                "of replacing the whole parent."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "source_anchor_child_label": str(primary.get("source_anchor_child_label") or ""),
                "source_subsection_label": str(primary.get("source_subsection_label") or ""),
            },
        )
    if UK_SOURCE_CARRIED_MULTI_SUBUNIT_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_CARRIED_MULTI_SUBUNIT_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_multi_subunit_repeal_lowered",
            reason=(
                "UK source text explicitly repeals quoted words where "
                "they occur in named child subsections; lowering preserves "
                "those child labels in a synthetic selector rather than "
                "deleting from the whole parent section."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "source_child_labels": str(primary.get("source_child_labels") or ""),
                "source_section_label": str(primary.get("source_section_label") or ""),
            },
        )
    if UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="multi_quoted_word_repeal_split",
            reason=(
                "UK source text repeals multiple separately quoted word "
                "fragments; lowering emits one bounded text delete per "
                "quoted fragment instead of replaying a collapsed selector."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "fragments": tuple(str(item.get("original") or "") for item in fragment_subs or []),
            },
        )
    if UK_AMENDMENT_INSERTED_TEXT_SUBSTITUTION_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_AMENDMENT_INSERTED_TEXT_SUBSTITUTION_RULE_ID,
            family="amendment_program_lowering",
            reason_code="source_targets_inserted_text_in_amendment_instruction",
            reason=(
                "UK source text substitutes text inserted by a named amendment "
                "instruction; lowering preserves that as a bounded rewrite of "
                "the target amendment instruction's inserted payload, not as a "
                "base-law text guess."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "source_paragraph_label": str(primary.get("source_paragraph_label") or ""),
                "source_item_label": str(primary.get("source_item_label") or ""),
            },
        )
    if UK_AMENDMENT_PROGRAM_INSERTED_PARENT_CHILD_INSERT_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_AMENDMENT_PROGRAM_INSERTED_PARENT_CHILD_INSERT_RULE_ID,
            family="amendment_program_lowering",
            reason_code="source_targets_inserted_parent_child_in_amendment_instruction",
            reason=(
                "UK source text inserts a child into text created by an earlier "
                "amendment instruction; lowering preserves that as a bounded "
                "target-local amendment-program text patch rather than applying "
                "it to unrelated live base law."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "source_subparagraph_label": str(
                    primary.get("source_subparagraph_label") or ""
                ),
                "source_item_label": str(primary.get("source_item_label") or ""),
                "inserted_parent_label": str(
                    primary.get("inserted_parent_label") or ""
                ),
                "direction": str(primary.get("direction") or ""),
                "anchor_label": str(primary.get("anchor_label") or ""),
                "inserted_label": str(primary.get("inserted_label") or ""),
            },
        )
    if op_text_end_occurrence:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_RANGE_INDEPENDENT_END_OCCURRENCE_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="explicit_independent_end_occurrence_text_range",
            reason=(
                "UK source text gives separate ordinal occurrences for "
                "the start and end anchors of a word-level range; lowering "
                "preserves both ordinals in a typed text selector rather than "
                "guessing the first end anchor after the start."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "replacement": op_text_replacement,
                "occurrence": op_text_occurrence,
                "end_occurrence": op_text_end_occurrence,
            },
        )
    if UK_SOURCE_CARRIED_FOLLOWING_WORDS_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_CARRIED_FOLLOWING_WORDS_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_following_words_repeal_lowered",
            reason=(
                "UK source parent says the following words are repealed "
                "and the BlockAmendment carries only those words; lowering "
                "preserves the block payload as the exact deletion preimage."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "source_parent_id": str(primary.get("source_parent_id") or ""),
            },
        )
    if UK_SOURCE_CARRIED_SUBPARAGRAPH_TAIL_REPEAL_RULE_ID in rule_ids:
        _append_uk_effect_lowering_observation(
            lowering_rejections_out,
            rule_id=UK_SOURCE_CARRIED_SUBPARAGRAPH_TAIL_REPEAL_RULE_ID,
            family="text_rewrite_lowering",
            reason_code="source_carried_subparagraph_tail_repeal_lowered",
            reason=(
                "UK source text explicitly repeals the words following "
                "a named subparagraph inside the affected paragraph; lowering "
                "preserves that as a bounded child-tail text selector instead "
                "of deleting from the whole paragraph."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "text_match": op_text_match,
                "source_anchor_child_kind": str(primary.get("source_anchor_child_kind") or ""),
                "source_anchor_child_label": str(primary.get("source_anchor_child_label") or ""),
                "source_parent_kind": str(primary.get("source_parent_kind") or ""),
                "source_parent_label": str(primary.get("source_parent_label") or ""),
            },
        )
def _fragment_target_suffix(fragment: object) -> tuple[str, str] | None:
    if not isinstance(fragment, dict):
        return None
    kind = str(fragment.get("target_suffix_kind") or "").strip().lower().replace("-", "")
    label = str(fragment.get("target_suffix_label") or "").strip()
    if not kind or not label:
        return None
    return kind, label


def _labeled_child_end_range_selector(
    target: LegalAddress,
    fragment: object,
    suffix: tuple[str, str],
) -> str:
    """Return a parent-scoped selector for ranges ending at an explicit child."""
    if target.special is not None or not isinstance(fragment, dict):
        return ""
    original = str(fragment.get("original") or "")
    if not original.startswith("TEXT_FROM_") or not original.endswith("_TO_END"):
        return ""
    suffix_kind, suffix_label = suffix
    leaf_kind = target.leaf_kind()
    compatible = (
        _addr_container(target) != "schedule"
        and (
            (leaf_kind == "subsection" and suffix_kind == "paragraph")
            or (leaf_kind == "paragraph" and suffix_kind == "subparagraph")
        )
    )
    if not compatible:
        return ""
    start = original[len("TEXT_FROM_") : -len("_TO_END")].strip()
    if not start:
        return ""
    return f"TEXT_FROM_CHILD_END{US}{suffix_kind}{US}{suffix_label}{US}{start}"


def lower_labeled_child_end_range_selector(
    *,
    effect: UKEffectRecord,
    target: LegalAddress,
    target_ref: str,
    primary: dict[str, Any],
    curr_action: Optional[str],
    extracted_el: Optional[ET.Element],
    extracted_text: Optional[str],
    lowering_rejections_out: Optional[list[dict[str, Any]]],
) -> UKLabeledChildEndRangeLowering:
    target_suffix = _fragment_target_suffix(primary)
    if target_suffix is None:
        return UKLabeledChildEndRangeLowering(
            primary=primary,
            curr_action=curr_action,
            skip_effect=False,
        )

    labeled_child_end_selector = _labeled_child_end_range_selector(
        target,
        primary,
        target_suffix,
    )
    if not labeled_child_end_selector:
        _append_uk_effect_lowering_rejection(
            lowering_rejections_out,
            rule_id="uk_effect_labeled_child_end_range_target_rejected",
            family="target_resolution_recovery",
            reason_code="unsupported_labeled_end_range_target_suffix",
            reason=(
                "UK source text bounds a text range to a labelled child target, "
                "but the affected provision target could not safely carry the "
                "parent-scoped child-end selector without widening or changing "
                "the source scope."
            ),
            effect=effect,
            extracted_el=extracted_el,
            extracted_text=extracted_text,
            detail={
                "target_ref": target_ref,
                "target": str(target),
                "target_suffix_kind": target_suffix[0],
                "target_suffix_label": target_suffix[1],
            },
        )
        return UKLabeledChildEndRangeLowering(
            primary=primary,
            curr_action=None,
            skip_effect=True,
        )

    _append_uk_effect_lowering_observation(
        lowering_rejections_out,
        rule_id="uk_effect_labeled_child_end_range_text_patch",
        family="text_rewrite_lowering",
        reason_code="source_bounded_text_range_names_child_endpoint",
        reason=(
            "UK source text bounds a range from a parent text anchor to "
            "the end of a labelled child provision; lowering preserves the "
            "parent target and encodes the explicit child endpoint in the "
            "text selector instead of retargeting to the child."
        ),
        effect=effect,
        extracted_el=extracted_el,
        extracted_text=extracted_text,
        detail={
            "target_ref": target_ref,
            "target": str(target),
            "text_match": labeled_child_end_selector,
            "source_text_match": str(primary.get("original") or ""),
            "target_suffix_kind": target_suffix[0],
            "target_suffix_label": target_suffix[1],
        },
    )
    return UKLabeledChildEndRangeLowering(
        primary={**primary, "original": labeled_child_end_selector},
        curr_action=curr_action,
        skip_effect=False,
    )


def _separate_definition_repeal_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if (
            rule_id != "uk_effect_definition_entry_repeal_text_patch"
            or replacement
            or not original.startswith("TEXT_DEFINITION_ENTRY_")
        ):
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": "",
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)


def _separate_occurrence_text_replace_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        occurrence = str(item.get("occurrence") or "")
        rule_id = str(item.get("rule_id") or "")
        if rule_id not in {
            "uk_effect_first_second_occurrence_substitution_text_patch",
            UK_AFTER_QUOTED_ANCHOR_ORDINAL_PLACES_INSERT_RULE_ID,
            UK_QUOTED_WORD_WHERE_ORDINAL_OCCURRENCES_SUBSTITUTION_RULE_ID,
        } or not original or not occurrence.isdigit():
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": replacement,
                "occurrence": occurrence,
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)


def _separate_source_range_definition_entry_insert_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if (
            rule_id != UK_SOURCE_RANGE_DEFINITION_ENTRY_INSERT_RULE_ID
            or not original.startswith("TEXT_AFTER_DEFINITION_")
            or not replacement
        ):
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": replacement,
                "rule_id": rule_id,
                "source_anchor_definition_term": str(
                    item.get("source_anchor_definition_term") or ""
                ),
                "source_inserted_definition_terms": str(
                    item.get("source_inserted_definition_terms") or ""
                ),
                "source_payload_additional_definition_terms": str(
                    item.get("source_payload_additional_definition_terms") or ""
                ),
            }
        )
    return tuple(fragments)


def _separate_compound_lettered_text_replace_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if (
            rule_id != UK_COMPOUND_LETTERED_TEXT_PATCH_RULE_ID
            or not original
            or not replacement
        ):
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": replacement,
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)


def _separate_all_occurrences_text_replace_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if rule_id not in UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS or not original:
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": replacement,
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)


def _separate_multi_quoted_word_repeal_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if rule_id != UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID or replacement or not original:
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": "",
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)


def _separate_definition_child_repeal_fragments(
    fragment_subs: Optional[list],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    fragments: list[dict[str, str]] = []
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if (
            rule_id != "uk_effect_definition_child_repeal_text_patch"
            or replacement
            or not original.startswith("TEXT_DEFINITION_CHILD_")
        ):
            return ()
        fragments.append(
            {
                "original": original,
                "replacement": "",
                "rule_id": rule_id,
            }
        )
    return tuple(fragments)
