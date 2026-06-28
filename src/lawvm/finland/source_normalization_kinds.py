"""Finland-specific source-normalization kind values.

These kinds are frontend-local and should not live in the shared core enum host.
They are carried through ``SourceNormalizationFact.kind`` as string values.
"""

from __future__ import annotations

from typing import Final

UNNUMBERED_PEER_REPARENT: Final[str] = "unnumbered_peer_reparent"
BASE_TAIL_PROSE_ABSORB: Final[str] = "base_tail_prose_absorb"
BASE_PENAL_SENTENCING_WRAPUP_FOLD: Final[str] = "base_penal_sentencing_wrapup_fold"
BASE_NUM_IN_INTRO_RECOVERED: Final[str] = "base_num_in_intro_recovered"
BASE_NUM_IN_INTRO_MISMATCH: Final[str] = "base_num_in_intro_mismatch"
BASE_DIGIT_RESET_SPLIT: Final[str] = "base_digit_reset_split"
BASE_DUPLICATE_TAIL_SPLIT: Final[str] = "base_duplicate_tail_split"
BASE_DUPLICATE_SIBLING_DROP: Final[str] = "base_duplicate_sibling_drop"
BASE_INTRO_LIST_RESTART_SPLIT: Final[str] = "base_intro_list_restart_split"
BASE_INTRO_LIST_TAIL_MOMENT_SPLIT: Final[str] = "base_intro_list_tail_moment_split"
BASE_SECTION_ITEM_SUBSECTION_FOLD: Final[str] = "base_section_item_subsection_fold"
BASE_TABLE_NOTE_SUBSECTION_FOLD: Final[str] = "base_table_note_subsection_fold"
BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION: Final[str] = "base_dotted_paragraph_subsection_promotion"
BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT: Final[str] = "base_unnumbered_subparagraph_moment_split"
BASE_HEADING_BODY_SUBSECTION_SPLIT: Final[str] = "base_heading_body_subsection_split"
HEADING_BODY_SUBSECTION_SPLIT_RULE_ATTR: Final[str] = "fi_heading_body_subsection_split_v1"
BASE_TABLE_CONTINUATION_SUBSECTION_MERGE: Final[str] = "base_table_continuation_subsection_merge"
TABLE_CONTINUATION_SUBSECTION_MERGE_RULE_ATTR: Final[str] = "fi_table_continuation_subsection_merge_v1"
BASE_TABLE_CONTINUATION_HEADER_REPAIR: Final[str] = "base_table_continuation_header_repair"
TRAILING_CHAPTER_REPARENT: Final[str] = "trailing_chapter_reparent"

FINLAND_SOURCE_NORMALIZATION_KINDS: Final[tuple[str, ...]] = (
    UNNUMBERED_PEER_REPARENT,
    BASE_TAIL_PROSE_ABSORB,
    BASE_NUM_IN_INTRO_RECOVERED,
    BASE_NUM_IN_INTRO_MISMATCH,
    BASE_DIGIT_RESET_SPLIT,
    BASE_DUPLICATE_TAIL_SPLIT,
    BASE_DUPLICATE_SIBLING_DROP,
    BASE_INTRO_LIST_RESTART_SPLIT,
    BASE_INTRO_LIST_TAIL_MOMENT_SPLIT,
    BASE_SECTION_ITEM_SUBSECTION_FOLD,
    BASE_TABLE_NOTE_SUBSECTION_FOLD,
    BASE_DOTTED_PARAGRAPH_SUBSECTION_PROMOTION,
    BASE_UNNUMBERED_SUBPARAGRAPH_MOMENT_SPLIT,
    BASE_HEADING_BODY_SUBSECTION_SPLIT,
    BASE_TABLE_CONTINUATION_SUBSECTION_MERGE,
    BASE_TABLE_CONTINUATION_HEADER_REPAIR,
    TRAILING_CHAPTER_REPARENT,
)
