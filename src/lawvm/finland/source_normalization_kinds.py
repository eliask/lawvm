"""Finland-specific source-normalization kind values.

These kinds are frontend-local and should not live in the shared core enum host.
They are carried through ``SourceNormalizationFact.kind`` as string values.
"""

from __future__ import annotations

from typing import Final

UNNUMBERED_PEER_REPARENT: Final[str] = "unnumbered_peer_reparent"
BASE_TAIL_PROSE_ABSORB: Final[str] = "base_tail_prose_absorb"
BASE_NUM_IN_INTRO_RECOVERED: Final[str] = "base_num_in_intro_recovered"
BASE_NUM_IN_INTRO_MISMATCH: Final[str] = "base_num_in_intro_mismatch"
BASE_DIGIT_RESET_SPLIT: Final[str] = "base_digit_reset_split"
BASE_DUPLICATE_TAIL_SPLIT: Final[str] = "base_duplicate_tail_split"
BASE_DUPLICATE_SIBLING_DROP: Final[str] = "base_duplicate_sibling_drop"
BASE_INTRO_LIST_RESTART_SPLIT: Final[str] = "base_intro_list_restart_split"
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
    TRAILING_CHAPTER_REPARENT,
)
