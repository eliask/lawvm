"""Frozen Finland rulebook scaffold data."""

from __future__ import annotations

from dataclasses import dataclass

from lawvm.finland.rulebook.clause_rules import (
    CLAUSE_JOLLOIN_RENUMBER_PAIR,
    CLAUSE_LUKUUN_O_OTTAMATTA_EXCEPTION_SCOPE,
    CLAUSE_SHARED_INTRO_OVER_CONJUNCTED_MOMENTTI,
)
from lawvm.finland.rulebook.families import (
    ClauseRule,
    PayloadRule,
    RuleFamily,
    SourceRule,
    TemporalRule,
)
from lawvm.finland.rulebook.compare_policy_types import CompareRule
from lawvm.finland.rulebook.common import (
    RuleFamilyId,
    RulebookValidationError,
)
from lawvm.finland.rulebook.registries import (
    FINLAND_RULEBOOK_REGISTRIES,
    FINLAND_EMIT_REGISTRY,
    FINLAND_GUARD_REGISTRY,
)
from lawvm.finland.rulebook.payload_rules import (
    PAYLOAD_INTRO_LIST_CONTINUATION,
    PAYLOAD_LETTERED_SUBITEMS_AMBIGUOUS_DEFAULT,
    PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT,
    PAYLOAD_OMISSION_SIBLING_CONTEXT,
    PAYLOAD_SPARSE_SUBSECTION_BODY,
    PAYLOAD_TABLE_WITH_NAMED_ROWS,
)

from lawvm.finland.rulebook.source_rules import (
    SOURCE_EDITORIAL_HEADING_NOISE,
    SOURCE_EDITORIAL_SOURCE_TAG_RECLASSIFICATION,
    SOURCE_OMIT_EDITORIAL_KUMOTTU_BANNER,
    SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING,
    SOURCE_SCHEMA_INVALID_BODY,
)
from lawvm.finland.rulebook.compare_policy_rules import (
    COMPARE_ORACLE_HTML_XML_TOPOLOGY_DRIFT,
    COMPARE_ORACLE_OMISSION_BLANK,
    COMPARE_ORACLE_STALE_SOURCE,
    COMPARE_REPEAL_NOTICE_EDITORIAL,
)
from lawvm.finland.rulebook.temporal_rules import (
    TEMPORAL_COMMENCEMENT_EXTRACT,
    TEMPORAL_DEFERRED_COMMENCEMENT,
    TEMPORAL_EXPIRY_EXTRACT,
    TEMPORAL_PHASED_ACTIVATION,
    TEMPORAL_VALIAIKAISESTI_IMMEDIATE_CLUSTER,
)


@dataclass(frozen=True, slots=True)
class FinlandRulebook:
    clause_rules: RuleFamily[ClauseRule]
    payload_rules: RuleFamily[PayloadRule]
    temporal_rules: RuleFamily[TemporalRule]
    source_rules: RuleFamily[SourceRule]
    compare_rules: RuleFamily[CompareRule]

    def all_rules(self) -> tuple[
        ClauseRule | PayloadRule | TemporalRule | SourceRule | CompareRule, ...
    ]:
        return (
            *self.clause_rules.rules,
            *self.payload_rules.rules,
            *self.temporal_rules.rules,
            *self.source_rules.rules,
            *self.compare_rules.rules,
        )

    def __post_init__(self) -> None:
        all_rule_ids = [rule.header.rule_id for rule in self.all_rules()]
        if len(set(all_rule_ids)) != len(all_rule_ids):
            raise RulebookValidationError("FinlandRulebook: duplicate rule_id across families")


def _validate_rulebook(rulebook: FinlandRulebook) -> None:
    families = (
        rulebook.clause_rules,
        rulebook.payload_rules,
        rulebook.temporal_rules,
        rulebook.source_rules,
        rulebook.compare_rules,
    )
    family_ids = tuple(family.family_id for family in families)
    if family_ids != (
        RuleFamilyId.CLAUSE,
        RuleFamilyId.PAYLOAD,
        RuleFamilyId.TEMPORAL,
        RuleFamilyId.SOURCE,
        RuleFamilyId.COMPARE,
    ):
        raise RulebookValidationError("FinlandRulebook: family ids must match the governed set")
    if len(set(family_ids)) != len(family_ids):
        raise RulebookValidationError("FinlandRulebook: duplicate family ids")
    for family in families:
        for rule in family.rules:
            prefix = f"fi.{family.family_id}."
            if not rule.header.rule_id.startswith(prefix):
                raise RulebookValidationError(
                    f"{rule.header.rule_id}: expected rule prefix {prefix}"
                )
            if not rule.header.examples:
                raise RulebookValidationError(
                    f"{rule.header.rule_id}: rules must include at least one example"
                )
            for guard in rule.guards:
                if (
                    not FINLAND_RULEBOOK_REGISTRIES.has_guard(guard.guard_id)
                    or guard.guard_id not in FINLAND_GUARD_REGISTRY
                ):
                    raise RulebookValidationError(
                        f"{rule.header.rule_id}: unknown guard id {guard.guard_id}"
                    )
            for emit in rule.emits:
                if (
                    not FINLAND_RULEBOOK_REGISTRIES.has_emit(emit.emit_id)
                    or emit.emit_id not in FINLAND_EMIT_REGISTRY
                ):
                    raise RulebookValidationError(
                        f"{rule.header.rule_id}: unknown emit id {emit.emit_id}"
                    )


FINLAND_RULEBOOK = FinlandRulebook(
    clause_rules=RuleFamily(
        family_id=RuleFamilyId.CLAUSE,
        description="Clause parsing rules.",
        rules=(
            CLAUSE_SHARED_INTRO_OVER_CONJUNCTED_MOMENTTI,
            CLAUSE_JOLLOIN_RENUMBER_PAIR,
            CLAUSE_LUKUUN_O_OTTAMATTA_EXCEPTION_SCOPE,
        ),
    ),
    payload_rules=RuleFamily(
        family_id=RuleFamilyId.PAYLOAD,
        description="Payload shape rules.",
        rules=(
            PAYLOAD_OMISSION_SIBLING_CONTEXT,
            PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT,
            PAYLOAD_LETTERED_SUBITEMS_AMBIGUOUS_DEFAULT,
            PAYLOAD_TABLE_WITH_NAMED_ROWS,
            PAYLOAD_SPARSE_SUBSECTION_BODY,
            PAYLOAD_INTRO_LIST_CONTINUATION,
        ),
    ),
    temporal_rules=RuleFamily(
        family_id=RuleFamilyId.TEMPORAL,
        description="Temporal scope rules.",
        rules=(
            TEMPORAL_VALIAIKAISESTI_IMMEDIATE_CLUSTER,
            TEMPORAL_COMMENCEMENT_EXTRACT,
            TEMPORAL_EXPIRY_EXTRACT,
            TEMPORAL_DEFERRED_COMMENCEMENT,
            TEMPORAL_PHASED_ACTIVATION,
        ),
    ),
    source_rules=RuleFamily(
        family_id=RuleFamilyId.SOURCE,
        description="Source normalization rules.",
        rules=(
            SOURCE_EDITORIAL_HEADING_NOISE,
            SOURCE_OMIT_EDITORIAL_KUMOTTU_BANNER,
            SOURCE_EDITORIAL_SOURCE_TAG_RECLASSIFICATION,
            SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING,
            SOURCE_SCHEMA_INVALID_BODY,
        ),
    ),
    compare_rules=RuleFamily(
        family_id=RuleFamilyId.COMPARE,
        description="Comparison rules.",
        rules=(
            COMPARE_REPEAL_NOTICE_EDITORIAL,
            COMPARE_ORACLE_HTML_XML_TOPOLOGY_DRIFT,
            COMPARE_ORACLE_OMISSION_BLANK,
            COMPARE_ORACLE_STALE_SOURCE,
        ),
    ),
)

_validate_rulebook(FINLAND_RULEBOOK)
