from lawvm.finland.rulebook import (
    FINLAND_EMIT_REGISTRY,
    FINLAND_GUARD_REGISTRY,
    FINLAND_RULEBOOK,
    emit_ids,
    guard_ids,
    validate_rulebook_vocabulary,
)
from lawvm.finland.rulebook.registries import EmitId, GuardId


def test_finland_rulebook_registry_covers_current_vocabulary() -> None:
    validate_rulebook_vocabulary(FINLAND_RULEBOOK)

    assert tuple(len(family.rules) for family in (
        FINLAND_RULEBOOK.clause_rules,
        FINLAND_RULEBOOK.payload_rules,
        FINLAND_RULEBOOK.temporal_rules,
        FINLAND_RULEBOOK.source_rules,
        FINLAND_RULEBOOK.compare_rules,
    )) == (3, 6, 5, 5, 4)
    assert len(FINLAND_RULEBOOK.all_rules()) == 23
    assert len({rule.header.rule_id for rule in FINLAND_RULEBOOK.all_rules()}) == 23
    assert guard_ids(FINLAND_RULEBOOK) == (
        GuardId.SIBLINGS_SAME_LEVEL_AS_OMISSION,
        GuardId.SIBLINGS_NOT_CLAIMED_BY_CLAUSE_TARGET,
        GuardId.PRECEDING_ITEM_ENDS_WITH_HOST_SIGNAL,
        GuardId.MARKER_ATTACHES_TO_IMMEDIATE_CLUSTER,
    )
    assert emit_ids(FINLAND_RULEBOOK) == (
        EmitId.EMIT_INTRO_REPLACE_FOR_EACH_MOMENT,
        EmitId.EMIT_RENUMBER_PAIR_SCOPE,
        EmitId.EMIT_RENUMBER_PAIR_SCOPE,
        EmitId.CLASSIFY_CONTEXT_CARRIED_SIBLINGS,
        EmitId.LINK_LETTERED_SUBITEM_RUN_TO_PARENT,
        EmitId.EMIT_UNRESOLVED_SUBITEM_PARENTAGE,
        EmitId.CLASSIFY_TABLE_WITH_NAMED_ROWS,
        EmitId.CLASSIFY_SPARSE_SUBSECTION_BODY,
        EmitId.CLASSIFY_INTRO_LIST_CONTINUATION,
        EmitId.MARK_TARGETS_TEMPORARY,
        EmitId.MARK_COMMENCEMENT_TARGETS,
        EmitId.MARK_EXPIRY_TARGETS,
        EmitId.MARK_DEFERRED_COMMENCEMENT,
        EmitId.MARK_PHASED_ACTIVATION,
        EmitId.DROP_EDITORIAL_HEADING_NOISE,
        EmitId.DROP_EDITORIAL_BANNER,
        EmitId.RECLASSIFY_EDITORIAL_SOURCE_TAG,
        EmitId.RECLASSIFY_NODE_KIND,
        EmitId.RECORD_SOURCE_NORMALIZATION_FACT,
        EmitId.FLAG_SCHEMA_INVALID_SOURCE,
        EmitId.MARK_COMPARE_EQUIVALENT,
        EmitId.MARK_COMPARE_EQUIVALENT,
        EmitId.MARK_COMPARE_OMISSION_BLANK,
        EmitId.MARK_COMPARE_STALE_SOURCE,
    )
    for guard_id in guard_ids(FINLAND_RULEBOOK):
        assert guard_id in FINLAND_GUARD_REGISTRY
    for emit_id in emit_ids(FINLAND_RULEBOOK):
        assert emit_id in FINLAND_EMIT_REGISTRY
