"""Text-patch item construction for UK effect lowering."""

from __future__ import annotations

from typing import Any, NamedTuple, Optional

from lawvm.core.ir import TextPatchSpec, TextSelector
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.uk_legislation.text_rewrite_fragments import (
    _separate_all_occurrences_text_replace_fragments,
    _separate_compound_lettered_text_replace_fragments,
    _separate_definition_repeal_fragments,
    _separate_definition_child_repeal_fragments,
    _separate_listed_word_and_range_to_end_repeal_fragments,
    _separate_multi_quoted_word_repeal_fragments,
    _separate_occurrence_text_replace_fragments,
    _separate_source_range_definition_entry_insert_fragments,
)


class UKTextPatchItem(NamedTuple):
    text_patch: Optional[TextPatchSpec]
    witness_fragments: Optional[list[dict[str, Any]]]


def build_uk_text_patch_items(
    *,
    curr_action: str,
    fragment_subs: Optional[list[dict[str, Any]]],
    op_text_match: Optional[str],
    op_text_replacement: Optional[str],
    op_text_occurrence: int,
    op_text_end_occurrence: int,
) -> list[UKTextPatchItem]:
    text_patch_items: list[UKTextPatchItem] = []
    separate_definition_repeals = _separate_definition_repeal_fragments(fragment_subs)
    separate_definition_child_repeals = _separate_definition_child_repeal_fragments(fragment_subs)
    separate_occurrence_replacements = _separate_occurrence_text_replace_fragments(fragment_subs)
    separate_source_range_definition_inserts = (
        _separate_source_range_definition_entry_insert_fragments(fragment_subs)
    )
    separate_all_occurrences_replacements = _separate_all_occurrences_text_replace_fragments(
        fragment_subs
    )
    separate_compound_lettered_replacements = (
        _separate_compound_lettered_text_replace_fragments(fragment_subs)
    )
    separate_compound_text_insertions = _separate_compound_target_local_text_insertions(
        fragment_subs
    )
    separate_multi_quoted_word_repeals = _separate_multi_quoted_word_repeal_fragments(
        fragment_subs
    )
    separate_listed_word_and_range_to_end_repeals = (
        _separate_listed_word_and_range_to_end_repeal_fragments(fragment_subs)
    )
    if curr_action == "text_repeal" and separate_definition_repeals:
        for fragment in separate_definition_repeals:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.DELETE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_repeal" and separate_definition_child_repeals:
        for fragment in separate_definition_child_repeals:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.DELETE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_repeal" and separate_multi_quoted_word_repeals:
        for fragment in separate_multi_quoted_word_repeals:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.DELETE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_repeal" and separate_listed_word_and_range_to_end_repeals:
        for fragment in separate_listed_word_and_range_to_end_repeals:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.DELETE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_replace" and separate_occurrence_replacements:
        for fragment in separate_occurrence_replacements:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=int(fragment["occurrence"]),
                        ),
                        replacement=fragment["replacement"],
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_replace" and separate_source_range_definition_inserts:
        for fragment in separate_source_range_definition_inserts:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                        replacement=fragment["replacement"],
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_replace" and separate_all_occurrences_replacements:
        for fragment in separate_all_occurrences_replacements:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                        replacement=fragment["replacement"],
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_replace" and separate_compound_lettered_replacements:
        for fragment in separate_compound_lettered_replacements:
            text_patch_items.append(
                UKTextPatchItem(
                    TextPatchSpec(
                        kind=TextPatchKindEnum.REPLACE,
                        selector=TextSelector(
                            match_text=fragment["original"],
                            occurrence=0,
                        ),
                        replacement=fragment["replacement"],
                    ),
                    [fragment],
                )
            )
    elif curr_action == "text_replace" and separate_compound_text_insertions:
        for fragment in separate_compound_text_insertions:
            original = fragment["original"]
            if original == "TEXT_FROM__TO_END":
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.APPEND,
                    selector=TextSelector(match_text="TEXT_END", occurrence=0),
                    replacement=fragment["replacement"],
                )
            else:
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(match_text=original, occurrence=0),
                    replacement=fragment["replacement"],
                )
            text_patch_items.append(UKTextPatchItem(text_patch, [fragment]))
    elif curr_action == "text_repeal" and op_text_match:
        text_patch_items.append(
            UKTextPatchItem(
                TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=op_text_match,
                        occurrence=op_text_occurrence,
                        end_occurrence=op_text_end_occurrence,
                    ),
                ),
                fragment_subs,
            )
        )
    elif (
        curr_action == "text_replace"
        and op_text_match == "TEXT_FROM__TO_END"
        and op_text_replacement is not None
    ):
        text_patch_items.append(
            UKTextPatchItem(
                TextPatchSpec(
                    kind=TextPatchKindEnum.APPEND,
                    selector=TextSelector(
                        match_text="TEXT_END",
                        occurrence=0,
                    ),
                    replacement=op_text_replacement,
                ),
                fragment_subs,
            )
        )
    elif curr_action == "text_replace" and op_text_match and op_text_replacement is not None:
        text_patch_items.append(
            UKTextPatchItem(
                TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=op_text_match,
                        occurrence=op_text_occurrence,
                        end_occurrence=op_text_end_occurrence,
                    ),
                    replacement=op_text_replacement,
                ),
                fragment_subs,
            )
        )
    else:
        text_patch_items.append(UKTextPatchItem(None, fragment_subs))
    return text_patch_items


def _separate_compound_target_local_text_insertions(
    fragment_subs: Optional[list[dict[str, Any]]],
) -> tuple[dict[str, str], ...]:
    if not fragment_subs or len(fragment_subs) <= 1:
        return ()
    allowed_rules = {
        "uk_effect_after_quoted_anchor_insert_text_patch",
        "uk_effect_at_end_text_insertion_patch",
        "uk_effect_compound_lettered_text_patch_instruction",
    }
    fragments: list[dict[str, str]] = []
    saw_append = False
    for item in fragment_subs:
        original = str(item.get("original") or "")
        replacement = str(item.get("replacement") or "")
        rule_id = str(item.get("rule_id") or "")
        if rule_id not in allowed_rules or not original or not replacement:
            return ()
        if original == "TEXT_FROM__TO_END":
            saw_append = True
        fragments.append(
            {
                "original": original,
                "replacement": replacement,
                "rule_id": rule_id,
            }
        )
    if not saw_append:
        return ()
    return tuple(fragments)
