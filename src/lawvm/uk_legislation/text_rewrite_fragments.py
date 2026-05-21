"""UK text-rewrite fragment provenance helpers."""

from __future__ import annotations

import json
import re
from typing import Optional

from lawvm.core.ir import LegalAddress, LegalOperation
from lawvm.uk_legislation.addressing import _addr_container
from lawvm.uk_legislation.nlp_parser import US
from lawvm.uk_legislation.provenance_notes import NOTE_FRAGMENT_SUB, NOTE_TEXT_REWRITE_RULE
from lawvm.uk_legislation.witness_sidecars import _witness_for_op


UK_MULTI_QUOTED_WORD_REPEAL_RULE_ID = "uk_effect_multi_quoted_word_repeal_text_patches"

UK_ALL_OCCURRENCES_TEXT_REWRITE_RULE_IDS = frozenset(
    {
        "uk_effect_after_quoted_anchor_all_occurrences_insert_text_patch",
        "uk_effect_after_quoted_anchor_each_occasion_insert_text_patch",
        "uk_effect_all_occurrences_substitution_text_patch",
        "uk_effect_in_definition_after_anchor_all_occurrences_insert_text_patch",
        "uk_effect_respectively_all_occurrences_substitution_text_patch",
        "uk_effect_wherever_occurring_substitution_text_patch",
    }
)


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
        if (
            rule_id != "uk_effect_first_second_occurrence_substitution_text_patch"
            or not original
            or not occurrence.isdigit()
        ):
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
