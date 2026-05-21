"""Text-patch item construction for UK effect lowering."""

from __future__ import annotations

from typing import Any, Optional

from lawvm.core.ir import TextPatchSpec, TextSelector
from lawvm.core.semantic_types import TextPatchKindEnum
from lawvm.uk_legislation.text_rewrite_fragments import (
    _separate_all_occurrences_text_replace_fragments,
    _separate_definition_repeal_fragments,
    _separate_multi_quoted_word_repeal_fragments,
    _separate_occurrence_text_replace_fragments,
)


UKTextPatchItem = tuple[Optional[TextPatchSpec], Optional[list[dict[str, Any]]]]


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
    separate_occurrence_replacements = _separate_occurrence_text_replace_fragments(fragment_subs)
    separate_all_occurrences_replacements = _separate_all_occurrences_text_replace_fragments(
        fragment_subs
    )
    separate_multi_quoted_word_repeals = _separate_multi_quoted_word_repeal_fragments(
        fragment_subs
    )
    if curr_action == "text_repeal" and separate_definition_repeals:
        for fragment in separate_definition_repeals:
            text_patch_items.append(
                (
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
                (
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
                (
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
    elif curr_action == "text_replace" and separate_all_occurrences_replacements:
        for fragment in separate_all_occurrences_replacements:
            text_patch_items.append(
                (
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
    elif curr_action == "text_repeal" and op_text_match:
        text_patch_items.append(
            (
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
            (
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
            (
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
        text_patch_items.append((None, fragment_subs))
    return text_patch_items
