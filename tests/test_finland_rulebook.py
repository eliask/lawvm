from dataclasses import FrozenInstanceError
from dataclasses import asdict
from typing import Any, cast

import pytest

from lawvm.finland.rulebook import (
    CLAUSE_SHARED_INTRO_OVER_CONJUNCTED_MOMENTTI,
    CLAUSE_JOLLOIN_RENUMBER_PAIR,
    CLAUSE_LUKUUN_O_OTTAMATTA_EXCEPTION_SCOPE,
    COMPARE_REPEAL_NOTICE_EDITORIAL,
    COMPARE_ORACLE_HTML_XML_TOPOLOGY_DRIFT,
    COMPARE_ORACLE_OMISSION_BLANK,
    COMPARE_ORACLE_STALE_SOURCE,
    FINLAND_RULEBOOK,
    ClauseAtom,
    CompareAtom,
    AuthorityTier,
    ClauseRule,
    RuleFamily,
    RuleFamilyId,
    RulePhase,
    RuleStrength,
    RulebookValidationError,
    RuleHeader,
    RuleExample,
    PayloadAtom,
    PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT,
    PAYLOAD_LETTERED_SUBITEMS_AMBIGUOUS_DEFAULT,
    PAYLOAD_INTRO_LIST_CONTINUATION,
    SourceAtom,
    SOURCE_EDITORIAL_HEADING_NOISE,
    SOURCE_EDITORIAL_SOURCE_TAG_RECLASSIFICATION,
    SOURCE_SCHEMA_INVALID_BODY,
    PAYLOAD_SPARSE_SUBSECTION_BODY,
    PAYLOAD_TABLE_WITH_NAMED_ROWS,
    EmitId,
    SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING,
    TemporalRule,
    TemporalAtom,
    RuleApplication,
    RuleApplicationLedger,
    SOURCE_OMIT_EDITORIAL_KUMOTTU_BANNER,
    render_rulebook_markdown,
)
from lawvm.finland.rulebook.rulebook import _validate_rulebook


def test_finland_rulebook_exports_a_frozen_example_rule() -> None:
    rule = CLAUSE_SHARED_INTRO_OVER_CONJUNCTED_MOMENTTI

    assert isinstance(rule, ClauseRule)
    assert rule.header.rule_id == "fi.clause.shared_intro_over_conjuncted_momentti"
    assert rule.header.phase is RulePhase.CLAUSE_PARSE
    assert rule.header.authority is AuthorityTier.ENACTED_TEXT
    assert rule.header.strength is RuleStrength.LITERAL
    assert rule.when == (
        ClauseAtom.SECTION_REF,
        ClauseAtom.MOMENTTI_LIST_GEN,
        ClauseAtom.JOHD,
    )
    assert rule.header.examples[0].expects == (
        "replace section:20/subsection:2 facet:intro",
        "replace section:20/subsection:3 facet:intro",
    )
    assert FINLAND_RULEBOOK.clause_rules.rules[0] == rule
    assert CLAUSE_JOLLOIN_RENUMBER_PAIR.when == (
        ClauseAtom.SECTION_REF,
        ClauseAtom.JOLLOIN_RENUMBER_PAIR,
    )
    assert CLAUSE_LUKUUN_O_OTTAMATTA_EXCEPTION_SCOPE.when == (
        ClauseAtom.SECTION_REF,
        ClauseAtom.LUKUUNOTTAMATTA,
    )
    assert PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT.when == (
        PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,
    )
    assert PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT.header.rule_id == (
        "fi.payload.lettered_subitems_attach_previous_if_explicit"
    )
    assert PAYLOAD_LETTERED_SUBITEMS_AMBIGUOUS_DEFAULT.when == (
        PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,
    )
    assert PAYLOAD_TABLE_WITH_NAMED_ROWS.when == (
        PayloadAtom.TABLE_WITH_NAMED_ROWS,
    )
    assert PAYLOAD_SPARSE_SUBSECTION_BODY.when == (
        PayloadAtom.SPARSE_SUBSECTION_BODY,
    )
    assert PAYLOAD_INTRO_LIST_CONTINUATION.when == (
        PayloadAtom.INTRO_LIST_CONTINUATION,
    )
    assert SOURCE_EDITORIAL_SOURCE_TAG_RECLASSIFICATION.when == (
        SourceAtom.EDITORIAL_SOURCE_TAG_RECLASSIFICATION,
    )
    assert SOURCE_EDITORIAL_HEADING_NOISE.when == (
        SourceAtom.EDITORIAL_HEADING_NOISE,
    )
    assert SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING.when == (
        SourceAtom.IMPOSSIBLE_SUBSECTION_NUMBERING,
    )
    assert SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING.emits[0].emit_id is EmitId.RECLASSIFY_NODE_KIND
    assert SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING.emits[1].emit_id is EmitId.RECORD_SOURCE_NORMALIZATION_FACT
    assert COMPARE_ORACLE_HTML_XML_TOPOLOGY_DRIFT.when == (
        CompareAtom.ORACLE_HTML_XML_TOPOLOGY_DRIFT,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[0].when == (
        PayloadAtom.SECTION_WITH_OMISSION,
        PayloadAtom.UNCLAIMED_NONOMISSION_SIBLINGS,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[1].when == (
        PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[2].when == (
        PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[3].when == (
        PayloadAtom.TABLE_WITH_NAMED_ROWS,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[4].when == (
        PayloadAtom.SPARSE_SUBSECTION_BODY,
    )
    assert FINLAND_RULEBOOK.payload_rules.rules[5].when == (
        PayloadAtom.INTRO_LIST_CONTINUATION,
    )
    assert FINLAND_RULEBOOK.temporal_rules.rules[0].when == (
        TemporalAtom.TEMPORAL_WORD,
        TemporalAtom.INSERT_TARGET_CLUSTER,
    )
    assert FINLAND_RULEBOOK.temporal_rules.rules[1].when == (
        TemporalAtom.COMMENCEMENT_WORD,
    )
    assert FINLAND_RULEBOOK.temporal_rules.rules[2].when == (
        TemporalAtom.EXPIRY_WORD,
    )
    assert FINLAND_RULEBOOK.temporal_rules.rules[3].when == (
        TemporalAtom.DEFERRED_COMMENCEMENT_WORD,
    )
    assert FINLAND_RULEBOOK.temporal_rules.rules[4].when == (
        TemporalAtom.PHASED_ACTIVATION_WORD,
    )
    assert FINLAND_RULEBOOK.source_rules.rules[0].when == (
        SourceAtom.EDITORIAL_HEADING_NOISE,
    )
    assert FINLAND_RULEBOOK.source_rules.rules[1].when == (
        SourceAtom.EDITORIAL_KUMOTTU_BANNER,
    )
    assert FINLAND_RULEBOOK.source_rules.rules[2].when == (
        SourceAtom.EDITORIAL_SOURCE_TAG_RECLASSIFICATION,
    )
    assert FINLAND_RULEBOOK.source_rules.rules[3].when == (
        SourceAtom.IMPOSSIBLE_SUBSECTION_NUMBERING,
    )
    assert FINLAND_RULEBOOK.source_rules.rules[4].when == (
        SourceAtom.SCHEMA_INVALID_BODY,
    )
    assert SOURCE_OMIT_EDITORIAL_KUMOTTU_BANNER.when == (
        SourceAtom.EDITORIAL_KUMOTTU_BANNER,
    )
    assert FINLAND_RULEBOOK.compare_rules.rules[0].when == (
        CompareAtom.ORACLE_REPEAL_NOTICE_TEXT,
        CompareAtom.REPLAY_REPEAL_PLACEHOLDER,
    )
    assert FINLAND_RULEBOOK.compare_rules.rules[1].when == (
        CompareAtom.ORACLE_HTML_XML_TOPOLOGY_DRIFT,
    )
    assert FINLAND_RULEBOOK.compare_rules.rules[2].when == (
        CompareAtom.ORACLE_OMISSION_BLANK,
    )
    assert FINLAND_RULEBOOK.compare_rules.rules[3].when == (
        CompareAtom.ORACLE_STALE_SOURCE,
    )
    assert COMPARE_ORACLE_OMISSION_BLANK.when == (
        CompareAtom.ORACLE_OMISSION_BLANK,
    )
    assert COMPARE_ORACLE_STALE_SOURCE.when == (
        CompareAtom.ORACLE_STALE_SOURCE,
    )
    assert SOURCE_SCHEMA_INVALID_BODY.when == (
        SourceAtom.SCHEMA_INVALID_BODY,
    )
    assert COMPARE_REPEAL_NOTICE_EDITORIAL.header.examples[0].rejects == (
        "false_positive_xml_topology_drift",
    )
    assert tuple(
        family.family_id
        for family in (
            FINLAND_RULEBOOK.clause_rules,
            FINLAND_RULEBOOK.payload_rules,
            FINLAND_RULEBOOK.temporal_rules,
            FINLAND_RULEBOOK.source_rules,
            FINLAND_RULEBOOK.compare_rules,
        )
    ) == (
        RuleFamilyId.CLAUSE,
        RuleFamilyId.PAYLOAD,
        RuleFamilyId.TEMPORAL,
        RuleFamilyId.SOURCE,
        RuleFamilyId.COMPARE,
    )


def test_finland_rulebook_objects_are_frozen_and_importable() -> None:
    with pytest.raises(FrozenInstanceError):
        cast(Any, FINLAND_RULEBOOK.clause_rules).family_id = "mutated"


def test_rulebook_validation_rejects_wrong_family_prefix() -> None:
    bad_rule = TemporalRule(
        header=RuleHeader(
            rule_id="fi.clause.bad_temporal_prefix",
            phase=RulePhase.TEMPORAL,
            priority=1,
            authority=AuthorityTier.ENACTED_TEXT,
            strength=RuleStrength.LITERAL,
            purpose="Bad prefix regression guard",
            examples=(RuleExample(label="bad"),),
        ),
        when=(TemporalAtom.TEMPORAL_WORD,),
    )

    with pytest.raises(
        RulebookValidationError,
        match=r"fi\.clause\.bad_temporal_prefix: expected rule prefix fi\.temporal\.",
    ):
        _validate_rulebook(
            FINLAND_RULEBOOK.__class__(
                clause_rules=FINLAND_RULEBOOK.clause_rules,
                payload_rules=FINLAND_RULEBOOK.payload_rules,
                temporal_rules=RuleFamily(
                    family_id=RuleFamilyId.TEMPORAL,
                    rules=(bad_rule,),
                    description="Temporal scope rules.",
                ),
                source_rules=FINLAND_RULEBOOK.source_rules,
                compare_rules=FINLAND_RULEBOOK.compare_rules,
            )
        )


def test_rule_application_ledger_is_frozen_and_serializable() -> None:
    application = RuleApplication(
        rule_id="fi.temporal.valiaikaisesti_immediate_target_cluster",
        phase=RulePhase.TEMPORAL,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        matched_spans=("tok[12:18]", "clause_target_cluster#2"),
        emitted_ids=("tempmark:section:21b",),
    )
    ledger = RuleApplicationLedger(applications=(application,))

    assert asdict(ledger) == {
        "applications": (
            {
                "rule_id": "fi.temporal.valiaikaisesti_immediate_target_cluster",
                "phase": RulePhase.TEMPORAL,
                "authority": AuthorityTier.ENACTED_TEXT,
                "strength": RuleStrength.LITERAL,
                "matched_spans": ("tok[12:18]", "clause_target_cluster#2"),
                "emitted_ids": ("tempmark:section:21b",),
            },
        ),
    }
    with pytest.raises(FrozenInstanceError):
        cast(Any, ledger).applications = ()


def test_rule_application_ledger_record_and_render_surface() -> None:
    ledger = RuleApplicationLedger().record(
        RuleApplication(
            rule_id="fi.temporal.valiaikaisesti_immediate_target_cluster",
            phase=RulePhase.TEMPORAL,
            authority=AuthorityTier.ENACTED_TEXT,
            strength=RuleStrength.LITERAL,
            matched_spans=("tok[12:18]",),
            emitted_ids=("tempmark:section:21b",),
        )
    )
    rendered = render_rulebook_markdown(FINLAND_RULEBOOK)

    assert ledger.applications[0].emitted_ids == ("tempmark:section:21b",)
    assert "# Finland Rulebook" in rendered
    assert "### fi.temporal.valiaikaisesti_immediate_target_cluster" in rendered
    assert "### fi.clause.jolloin_renumber_pair" in rendered
    assert "### fi.clause.lukuun_ottamatta_exception_scope" in rendered
    assert "### fi.payload.lettered_subitems_attach_previous_if_explicit" in rendered
    assert "### fi.payload.table_with_named_rows" in rendered
    assert "### fi.payload.sparse_subsection_body" in rendered
    assert "### fi.payload.intro_list_continuation" in rendered
    assert "### fi.temporal.commencement_extract" in rendered
    assert "### fi.temporal.deferred_commencement" in rendered
    assert "### fi.temporal.phased_activation" in rendered
    assert "### fi.compare.repeal_notice_editorial" in rendered
    assert "### fi.compare.oracle_html_xml_topology_drift" in rendered
    assert "### fi.compare.oracle_omission_blank" in rendered
    assert "### fi.compare.oracle_stale_source" in rendered
    assert "Carry non-claimed siblings beside omission markers as context, not payload" in rendered
