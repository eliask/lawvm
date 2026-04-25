"""Governed guard and emit registries for the Finland rulebook scaffold."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Callable, Mapping


class GuardId(StrEnum):
    MARKER_ATTACHES_TO_IMMEDIATE_CLUSTER = "marker_attaches_to_immediate_cluster"
    LETTERED_RUN_HAS_PARENT = "lettered_run_has_parent"
    PRECEDING_ITEM_ENDS_WITH_HOST_SIGNAL = "preceding_item_ends_with_host_signal"
    SIBLINGS_NOT_CLAIMED_BY_CLAUSE_TARGET = "siblings_not_claimed_by_clause_target"
    SIBLINGS_SAME_LEVEL_AS_OMISSION = "siblings_same_level_as_omission"


class EmitId(StrEnum):
    CLASSIFY_CONTEXT_CARRIED_SIBLINGS = "classify_context_carried_siblings"
    DROP_EDITORIAL_HEADING_NOISE = "drop_editorial_heading_noise"
    DROP_EDITORIAL_BANNER = "drop_editorial_banner"
    CLASSIFY_INTRO_LIST_CONTINUATION = "classify_intro_list_continuation"
    EMIT_UNRESOLVED_SUBITEM_PARENTAGE = "emit_unresolved_subitem_parentage"
    FLAG_SCHEMA_INVALID_SOURCE = "flag_schema_invalid_source"
    RECORD_SOURCE_NORMALIZATION_FACT = "record_source_normalization_fact"
    RECLASSIFY_NODE_KIND = "reclassify_node_kind"
    EMIT_INTRO_REPLACE_FOR_EACH_MOMENT = "emit_intro_replace_for_each_moment"
    EMIT_RENUMBER_PAIR_SCOPE = "emit_renumber_pair_scope"
    CLASSIFY_SPARSE_SUBSECTION_BODY = "classify_sparse_subsection_body"
    MARK_COMMENCEMENT_TARGETS = "mark_commencement_targets"
    MARK_COMPARE_EQUIVALENT = "mark_compare_equivalent"
    MARK_COMPARE_TOPOLOGY_DRIFT = "mark_compare_topology_drift"
    MARK_COMPARE_OMISSION_BLANK = "mark_compare_omission_blank"
    MARK_COMPARE_STALE_SOURCE = "mark_compare_stale_source"
    MARK_EXPIRY_TARGETS = "mark_expiry_targets"
    MARK_TARGETS_TEMPORARY = "mark_targets_temporary"
    CLASSIFY_TABLE_WITH_NAMED_ROWS = "classify_table_with_named_rows"
    LINK_LETTERED_SUBITEM_RUN_TO_PARENT = "link_lettered_subitem_run_to_parent"
    MARK_DEFERRED_COMMENCEMENT = "mark_deferred_commencement"
    MARK_PHASED_ACTIVATION = "mark_phased_activation"
    RECLASSIFY_EDITORIAL_SOURCE_TAG = "reclassify_editorial_source_tag"


@dataclass(frozen=True, slots=True)
class RulebookRegistries:
    guards: tuple[GuardId, ...]
    emits: tuple[EmitId, ...]

    def has_guard(self, guard_id: GuardId) -> bool:
        return guard_id in self.guards

    def has_emit(self, emit_id: EmitId) -> bool:
        return emit_id in self.emits


def _true_guard(**_: object) -> bool:
    return True


def _emit_record(**payload: object) -> dict[str, object]:
    return dict(payload)


FINLAND_GUARD_REGISTRY: Mapping[GuardId, Callable[..., bool]] = {
    GuardId.MARKER_ATTACHES_TO_IMMEDIATE_CLUSTER: _true_guard,
    GuardId.LETTERED_RUN_HAS_PARENT: _true_guard,
    GuardId.PRECEDING_ITEM_ENDS_WITH_HOST_SIGNAL: _true_guard,
    GuardId.SIBLINGS_NOT_CLAIMED_BY_CLAUSE_TARGET: _true_guard,
    GuardId.SIBLINGS_SAME_LEVEL_AS_OMISSION: _true_guard,
}

FINLAND_EMIT_REGISTRY: Mapping[EmitId, Callable[..., object]] = {
    EmitId.CLASSIFY_CONTEXT_CARRIED_SIBLINGS: _emit_record,
    EmitId.DROP_EDITORIAL_HEADING_NOISE: _emit_record,
    EmitId.DROP_EDITORIAL_BANNER: _emit_record,
    EmitId.CLASSIFY_INTRO_LIST_CONTINUATION: _emit_record,
    EmitId.EMIT_UNRESOLVED_SUBITEM_PARENTAGE: _emit_record,
    EmitId.FLAG_SCHEMA_INVALID_SOURCE: _emit_record,
    EmitId.RECORD_SOURCE_NORMALIZATION_FACT: _emit_record,
    EmitId.RECLASSIFY_NODE_KIND: _emit_record,
    EmitId.EMIT_INTRO_REPLACE_FOR_EACH_MOMENT: _emit_record,
    EmitId.EMIT_RENUMBER_PAIR_SCOPE: _emit_record,
    EmitId.CLASSIFY_SPARSE_SUBSECTION_BODY: _emit_record,
    EmitId.MARK_COMMENCEMENT_TARGETS: _emit_record,
    EmitId.LINK_LETTERED_SUBITEM_RUN_TO_PARENT: _emit_record,
    EmitId.MARK_COMPARE_EQUIVALENT: _emit_record,
    EmitId.MARK_COMPARE_TOPOLOGY_DRIFT: _emit_record,
    EmitId.MARK_COMPARE_OMISSION_BLANK: _emit_record,
    EmitId.MARK_COMPARE_STALE_SOURCE: _emit_record,
    EmitId.MARK_EXPIRY_TARGETS: _emit_record,
    EmitId.MARK_TARGETS_TEMPORARY: _emit_record,
    EmitId.CLASSIFY_TABLE_WITH_NAMED_ROWS: _emit_record,
    EmitId.RECLASSIFY_EDITORIAL_SOURCE_TAG: _emit_record,
    EmitId.MARK_DEFERRED_COMMENCEMENT: _emit_record,
    EmitId.MARK_PHASED_ACTIVATION: _emit_record,
}


FINLAND_RULEBOOK_REGISTRIES = RulebookRegistries(
    guards=(
        GuardId.MARKER_ATTACHES_TO_IMMEDIATE_CLUSTER,
        GuardId.LETTERED_RUN_HAS_PARENT,
        GuardId.PRECEDING_ITEM_ENDS_WITH_HOST_SIGNAL,
        GuardId.SIBLINGS_NOT_CLAIMED_BY_CLAUSE_TARGET,
        GuardId.SIBLINGS_SAME_LEVEL_AS_OMISSION,
    ),
    emits=(
        EmitId.CLASSIFY_CONTEXT_CARRIED_SIBLINGS,
        EmitId.DROP_EDITORIAL_HEADING_NOISE,
        EmitId.DROP_EDITORIAL_BANNER,
        EmitId.CLASSIFY_INTRO_LIST_CONTINUATION,
        EmitId.EMIT_UNRESOLVED_SUBITEM_PARENTAGE,
        EmitId.FLAG_SCHEMA_INVALID_SOURCE,
        EmitId.RECORD_SOURCE_NORMALIZATION_FACT,
        EmitId.RECLASSIFY_NODE_KIND,
        EmitId.EMIT_INTRO_REPLACE_FOR_EACH_MOMENT,
        EmitId.EMIT_RENUMBER_PAIR_SCOPE,
        EmitId.CLASSIFY_SPARSE_SUBSECTION_BODY,
        EmitId.MARK_COMMENCEMENT_TARGETS,
        EmitId.LINK_LETTERED_SUBITEM_RUN_TO_PARENT,
        EmitId.MARK_COMPARE_EQUIVALENT,
        EmitId.MARK_COMPARE_TOPOLOGY_DRIFT,
        EmitId.MARK_COMPARE_OMISSION_BLANK,
        EmitId.MARK_COMPARE_STALE_SOURCE,
        EmitId.MARK_EXPIRY_TARGETS,
        EmitId.MARK_TARGETS_TEMPORARY,
        EmitId.CLASSIFY_TABLE_WITH_NAMED_ROWS,
        EmitId.MARK_DEFERRED_COMMENCEMENT,
        EmitId.MARK_PHASED_ACTIVATION,
        EmitId.RECLASSIFY_EDITORIAL_SOURCE_TAG,
    ),
)


def guard_ids(rulebook: Any) -> tuple[GuardId, ...]:
    return tuple(
        guard.guard_id
        for family in (
            rulebook.clause_rules,
            rulebook.payload_rules,
            rulebook.temporal_rules,
            rulebook.source_rules,
            rulebook.compare_rules,
        )
        for rule in family.rules
        for guard in rule.guards
    )


def emit_ids(rulebook: Any) -> tuple[EmitId, ...]:
    return tuple(
        emit.emit_id
        for family in (
            rulebook.clause_rules,
            rulebook.payload_rules,
            rulebook.temporal_rules,
            rulebook.source_rules,
            rulebook.compare_rules,
        )
        for rule in family.rules
        for emit in rule.emits
    )


def validate_rulebook_vocabulary(rulebook: Any) -> None:
    from lawvm.finland.rulebook.common import RulebookValidationError

    for guard_id in guard_ids(rulebook):
        if guard_id not in FINLAND_GUARD_REGISTRY:
            raise RulebookValidationError(f"unknown guard id {guard_id}")
    for emit_id in emit_ids(rulebook):
        if emit_id not in FINLAND_EMIT_REGISTRY:
            raise RulebookValidationError(f"unknown emit id {emit_id}")
