"""Closed vocabulary for replay-time recovery/rebound kinds.

``RecoveryKind`` is the single typed carrier for the ``recovery_kind`` /
``rebound_kind`` discriminant that flows through ``SourcePathology.detail`` and
gates mutation-allowance authorization (see
``lawvm.finland.apply_typed_dispatch._new_pathologies_include_recovery_kind``).

Authority firewall rationale (``notes/LAWVM_PIPELINE_CONTRACT.md`` §7,
``AGENTS.md`` §1.9): the allowance decision keys on this value. When it was a
free-form string a typo on either the producer or the consumer side silently
failed to match, so a landed mutation path became unexplained/unaccounted (or
the reverse). Promoting it to a closed ``StrEnum`` makes the producer set and
the consumer set the same checkable object and fails loud on an unregistered
member (``coerce_recovery_kind``).

This is a TYPE migration, not a value rename: every member value equals the
exact string previously stored in ``detail`` so serialized/persisted pathology
detail stays byte-compatible.
"""

from __future__ import annotations

from enum import StrEnum


class RecoveryKind(StrEnum):
    """Closed set of replay-time recovery/rebound discriminants.

    Members are the verbatim strings that producers write into
    ``SourcePathology.detail["recovery_kind"]`` / ``["rebound_kind"]`` and that
    the apply-time allowance consumer matches against. Adding a producer site
    that emits a new kind requires adding a member here (the unregistered-member
    coercion fails loud), keeping producer-set == consumer-set.
    """

    ABSORBED_TAIL_SUBSECTION_COLLAPSE = "absorbed_tail_subsection_collapse"
    BLOCKED_OOR_SUBSECTION_APPEND = "blocked_oor_subsection_append"
    COMPOUND_ITEM_INSERT_APPEND = "compound_item_insert_append"
    COMPOUND_LABEL_SUBPARAGRAPH_STRIP = "compound_label_subparagraph_strip"
    CONTAINER_INSERT_BASE_CHAPTER_MERGE = "container_insert_base_chapter_merge"
    CONTAINER_INSERT_BASE_CHAPTER_MERGE_DUPLICATE_LABELS = "container_insert_base_chapter_merge_duplicate_labels"
    CONTAINER_INSERT_NON_BASE_SCAFFOLD_CONSUME = "container_insert_non_base_scaffold_consume"
    CONTAINER_REPLACE_FRAGMENTARY_HEADING_MERGE = "container_replace_fragmentary_heading_merge"
    CONTAINER_REPLACE_FRAGMENTARY_HEADING_MERGE_DUPLICATE_LABELS = "container_replace_fragmentary_heading_merge_duplicate_labels"
    CONTAINER_SNAPSHOT_SPARSE_MISSING_CHILD_REPEAL_SKIP = "container_snapshot_sparse_missing_child_repeal_skip"
    CONTENT_ONLY_LETTER_ROW_MERGE = "content_only_letter_row_merge"
    CONTENT_ONLY_ROW_MERGE = "content_only_row_merge"
    CONTINUATION_FRAGMENT_SKIP = "continuation_fragment_skip"
    INTRO_LIST_MOMENT_SHAPE = "intro_list_moment_shape"
    INTRO_PREPEND_LETTER_LIST_MOMENT = "intro_prepend_letter_list_moment"
    ITEM_INSERT_SUFFIX_RENUMBER = "item_insert_suffix_renumber"
    ITEM_INSERT_TAIL_WRAPUP_ABSORB = "item_insert_tail_wrapup_absorb"
    ITEM_JOHD_CLAIMED_SUBPARAGRAPH_MERGE = "item_johd_claimed_subparagraph_merge"
    ITEM_REPLACE_STANDALONE_TAIL_PRUNE = "item_replace_standalone_tail_prune"
    ITEM_REPLACE_TAIL_SUBSECTION_ABSORB = "item_replace_tail_subsection_absorb"
    LETTER_ITEM_REPLACE_AS_INSERT = "letter_item_replace_as_insert"
    MISSING_EXACT_SUBSECTION_LABEL = "missing_exact_subsection_label"
    NUMERIC_ITEM_REPLACE_AS_INSERT = "numeric_item_replace_as_insert"
    OMISSION_BRACKETED_SINGLE_SUBSECTION_REWRITE = "omission_bracketed_single_subsection_rewrite"
    RECODIFICATION_OMISSION_ONLY_SECTION_SHELL = "recodification_omission_only_section_shell"
    SAME_EFFECTIVE_CONTAINER_REPEAL_SHADOWED = "same_effective_container_repeal_shadowed"
    SECTION_INSERT_CHAPTER_MERGE_ABSORB = "section_insert_chapter_merge_absorb"
    SECTION_INSERT_CHAPTER_MERGE_ABSORB_DUPLICATE_LABELS = "section_insert_chapter_merge_absorb_duplicate_labels"
    SECTION_INSERT_CHAPTER_MERGE_ABSORB_TRAILING_SIBLINGS = "section_insert_chapter_merge_absorb_trailing_siblings"
    SECTION_INSERT_CHAPTER_MERGE_LIVE_DUPLICATES_PRESERVE_UNIQUE_PAYLOAD = "section_insert_chapter_merge_live_duplicates_preserve_unique_payload"
    SECTION_INSERT_NON_BASE_SCAFFOLD_CONSUME = "section_insert_non_base_scaffold_consume"
    SECTION_INSERT_SAME_LABEL_REPLACE = "section_insert_same_label_replace"
    SECTION_INSERT_SAME_LABEL_REPLACE_CROSS = "section_insert_same_label_replace_cross"
    SECTION_MATERIALIZATION_ROOT_MOVE_DESTINATION_REBIND = "section_materialization_root_move_destination_rebind"
    SECTION_MATERIALIZATION_SCOPED_INSERT = "section_materialization_scoped_insert"
    SECTION_MATERIALIZATION_SCOPED_INSERT_SAME_LABEL_REPLACE = "section_materialization_scoped_insert_same_label_replace"
    SECTION_MOVE_DESTINATION_SAME_LABEL_REPLACE = "section_move_destination_same_label_replace"
    SECTION_MOVE_INSERT_DESTINATION_REBIND = "section_move_insert_destination_rebind"
    SECTION_MOVE_REPLACE_DESTINATION_REBIND = "section_move_replace_destination_rebind"
    SECTION_REPLACE_BOOTSTRAP_BASE_PRIOR_PARENT_INSERT = "section_replace_bootstrap_base_prior_parent_insert"
    SECTION_REPLACE_BOOTSTRAP_CITED_PARENT_SCAFFOLD = "section_replace_bootstrap_cited_parent_scaffold"
    SECTION_REPLACE_BOOTSTRAP_GAP_ESTABLISH = "section_replace_bootstrap_gap_establish"
    SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING = "section_replace_bootstrap_parent_missing"
    SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE = "section_replace_consume_unscoped_root_duplicate"
    SECTION_SNAPSHOT_DROP_ABSENT_CARRIED_SUBSECTION = "section_snapshot_drop_absent_carried_subsection"
    SECTION_SNAPSHOT_DROP_CARRIED_TARGET_SUBSECTION_TEXT = "section_snapshot_drop_carried_target_subsection_text"
    SECTION_SNAPSHOT_DROP_EXPIRED_TEMPORARY_SUBSECTION = "section_snapshot_drop_expired_temporary_subsection"
    SECTION_SNAPSHOT_DROP_SHIFTED_EXPIRED_TEMPORARY_SUBSECTION = "section_snapshot_drop_shifted_expired_temporary_subsection"
    SECTION_SNAPSHOT_FLATTENED_ITEM_PAYLOAD_MERGE = "section_snapshot_flattened_item_payload_merge"
    SECTION_SNAPSHOT_ITEM_PAYLOAD_FOLD_MERGE = "section_snapshot_item_payload_fold_merge"
    SECTION_SNAPSHOT_PRESERVE_FOLD_FOR_DESCENDANT_SCOPED_SOURCE = "section_snapshot_preserve_fold_for_descendant_scoped_source"
    SECTION_SNAPSHOT_PRESERVE_LIVE_FOLD_FOR_DESCENDANT_SCOPED_ITEM = "section_snapshot_preserve_live_fold_for_descendant_scoped_item"
    SECTION_SNAPSHOT_REBASE_ON_LATEST_EXACT_PARENT = "section_snapshot_rebase_on_latest_exact_parent"
    SECTION_SNAPSHOT_REPEAL_ABSENT_COMPLETE_REPLACEMENT_SUBSECTION = "section_snapshot_repeal_absent_complete_replacement_subsection"
    SECTION_SNAPSHOT_SCOPED_ITEM_PAYLOAD_BIND = "section_snapshot_scoped_item_payload_bind"
    SECTION_SNAPSHOT_SINGLE_SUBSECTION_SPARSE_MERGE = "section_snapshot_single_subsection_sparse_merge"
    SHARED_TAIL_ITEM_REPLACE_SANITIZE = "shared_tail_item_replace_sanitize"
    SINGLE_SUBSECTION_ITEM_FALLBACK = "single_subsection_item_fallback"
    SPARSE_ALAKOHTA_INSERT_MERGE = "sparse_alakohta_insert_merge"
    SPARSE_ALAKOHTA_REPLACE_MERGE = "sparse_alakohta_replace_merge"
    SPARSE_ITEM_REPLACE_MERGE = "sparse_item_replace_merge"
    SPARSE_ITEM_TAIL_SUBSECTION_PRUNE = "sparse_item_tail_subsection_prune"
    SPARSE_SUBSECTION_TAIL_PRESERVED = "sparse_subsection_tail_preserved"
    SUBSECTION_INSERT_EXPIRED_TEMPORARY_SLOT_REPLACE = "subsection_insert_expired_temporary_slot_replace"
    SUBSECTION_INSERT_RENUMBER = "subsection_insert_renumber"
    SUBSECTION_INSERT_REPEAL_PLACEHOLDER_REPLACE = "subsection_insert_repeal_placeholder_replace"
    SUBSECTION_INSERT_TEMPORARY_DUPLICATE_LABEL_REPLACE = "subsection_insert_temporary_duplicate_label_replace"
    SUBSECTION_REPLACE_APPEND = "subsection_replace_append"
    SUBSECTION_REPLACE_FORCED_APPEND = "subsection_replace_forced_append"
    SUBSECTION_REPLACE_OMISSION_MERGE_FALLBACK = "subsection_replace_omission_merge_fallback"
    SUBSECTION_REPLACE_PREDECESSOR_TAIL_EXTRACT_INSERT = "subsection_replace_predecessor_tail_extract_insert"
    SUBSECTION_REPLACE_SPARSE_GAP_INSERT = "subsection_replace_sparse_gap_insert"
    SUBSECTION_REPLACE_SPARSE_OMISSION_ITEM_MERGE = "subsection_replace_sparse_omission_item_merge"
    SUBSECTION_REPLACE_STANDALONE_TAIL_APPEND = "subsection_replace_standalone_tail_append"
    SUBSECTION_REPLACE_STANDALONE_TAIL_SIBLING_PRUNE = "subsection_replace_standalone_tail_sibling_prune"
    SUBSECTION_REPLACE_UNLABELED_SPARSE_ITEM_MERGE = "subsection_replace_unlabeled_sparse_item_merge"
    SUBSECTION_SNAPSHOT_DROP_ABSENT_CARRIED_PARAGRAPH = "subsection_snapshot_drop_absent_carried_paragraph"
    SUBSECTION_SNAPSHOT_DROP_EXPIRED_TEMPORARY_PARAGRAPH = "subsection_snapshot_drop_expired_temporary_paragraph"
    UNCOVERED_SECTION_INSERT_SOURCE_OWNED_PART_CHAPTER_SCAFFOLD = "uncovered_section_insert_source_owned_part_chapter_scaffold"
    UNIQUE_ITEM_LABEL_SUBSECTION_FALLBACK = "unique_item_label_subsection_fallback"


class UnregisteredRecoveryKind(ValueError):
    """A recovery/rebound kind string is not a registered ``RecoveryKind`` member.

    Raised instead of silently failing the allowance match. The fix is always
    "add the missing member to ``RecoveryKind``" so producer-set == consumer-set.
    """

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(
            f"unregistered recovery_kind/rebound_kind {value!r}; "
            f"add it to lawvm.core.recovery_kind.RecoveryKind so producer-set == consumer-set"
        )


def coerce_recovery_kind(value: object) -> RecoveryKind:
    """Coerce a stored detail string to a ``RecoveryKind``, failing loud.

    Used at the apply-time consumer boundary where the value re-enters from an
    untyped ``Mapping``. An unrecognized string is a registration gap, never a
    silent no-match: raise ``UnregisteredRecoveryKind``.
    """
    if isinstance(value, RecoveryKind):
        return value
    try:
        return RecoveryKind(str(value))
    except ValueError as exc:
        raise UnregisteredRecoveryKind(str(value)) from exc


__all__ = [
    "RecoveryKind",
    "UnregisteredRecoveryKind",
    "coerce_recovery_kind",
]
