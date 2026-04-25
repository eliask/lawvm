"""Payload-family Finland rulebook constants."""

from __future__ import annotations

from lawvm.finland.rulebook.atoms import PayloadAtom
from lawvm.finland.rulebook.common import (
    AuthorityTier,
    CitationRef,
    EmitRef,
    GuardRef,
    RuleExample,
    RuleHeader,
    RulePhase,
    RuleStrength,
)
from lawvm.finland.rulebook.families import PayloadRule
from lawvm.finland.rulebook.registries import EmitId, GuardId


PAYLOAD_OMISSION_SIBLING_CONTEXT = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.omission_sibling_context",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=140,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Carry non-claimed siblings beside omission markers as context, not payload",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND.md", locator="payload spec"),),
        examples=(
            RuleExample(
                label="omission sibling context",
                input_xml="<section><kohta>1 kohta</kohta><omissio>2-4</omissio></section>",
                expects=("item:1=context_carried", "omission=omitted_context"),
            ),
        ),
    ),
    when=(
        PayloadAtom.SECTION_WITH_OMISSION,
        PayloadAtom.UNCLAIMED_NONOMISSION_SIBLINGS,
    ),
    guards=(
        GuardRef(GuardId.SIBLINGS_SAME_LEVEL_AS_OMISSION),
        GuardRef(GuardId.SIBLINGS_NOT_CLAIMED_BY_CLAUSE_TARGET),
    ),
    emits=(
        EmitRef(
            EmitId.CLASSIFY_CONTEXT_CARRIED_SIBLINGS,
            (("section", "sec"), ("siblings", "ctx")),
        ),
    ),
)

PAYLOAD_LETTERED_SUBITEMS_ATTACH_PREVIOUS_IF_EXPLICIT = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.lettered_subitems_attach_previous_if_explicit",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=135,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.CONVENTIONAL,
        purpose="Attach a lettered subitem run to the preceding numbered item only when the host signal is explicit",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="payload family"),),
        examples=(
            RuleExample(
                label="explicit parent host",
                input_text="4) ...; a) ...; b) ...; c) ...; 5) ...",
                expects=("item:a=parented_to_1", "item:b=parented_to_1"),
                rejects=("auto_attach_to_5",),
            ),
        ),
    ),
    when=(PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,),
    guards=(GuardRef(GuardId.PRECEDING_ITEM_ENDS_WITH_HOST_SIGNAL),),
    emits=(
        EmitRef(
            EmitId.LINK_LETTERED_SUBITEM_RUN_TO_PARENT,
            (("run", "run"), ("parent", "parent")),
        ),
    ),
)

PAYLOAD_LETTERED_SUBITEMS_AMBIGUOUS_DEFAULT = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.lettered_subitems_ambiguous_default",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=120,
        authority=AuthorityTier.LAWVM_POLICY,
        strength=RuleStrength.POLICY,
        purpose="Leave lettered subitem parentage unresolved when the host signal is not explicit enough",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="payload family"),),
        defeaters=("fi.payload.lettered_subitems_attach_previous_if_explicit",),
        examples=(
            RuleExample(
                label="ambiguous lettered run",
                input_text="4) ...; a) ...; b) ...; c) ...; 5) ...",
                expects=("unresolved_subitem_parentage:run",),
            ),
        ),
    ),
    when=(PayloadAtom.DIRECT_LETTERED_SUBITEM_RUN,),
    emits=(
        EmitRef(
            EmitId.EMIT_UNRESOLVED_SUBITEM_PARENTAGE,
            (("run", "run"),),
        ),
    ),
)

PAYLOAD_TABLE_WITH_NAMED_ROWS = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.table_with_named_rows",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=130,
        authority=AuthorityTier.FINLEX_AKN,
        strength=RuleStrength.LITERAL,
        purpose="Preserve table rows with explicit names as named row payload, not anonymous text",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="payload family"),),
        examples=(
            RuleExample(
                label="table row names",
                input_xml="<table><tr><th>a</th><td>1</td></tr></table>",
                expects=("table_rows:named",),
            ),
        ),
    ),
    when=(PayloadAtom.TABLE_WITH_NAMED_ROWS,),
    emits=(
        EmitRef(
            EmitId.CLASSIFY_TABLE_WITH_NAMED_ROWS,
            (("table", "table"),),
        ),
    ),
)

PAYLOAD_SPARSE_SUBSECTION_BODY = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.sparse_subsection_body",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=128,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Keep sparse subsection bodies explicit instead of collapsing them into surrounding prose",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="payload family"),),
        examples=(
            RuleExample(
                label="sparse subsection",
                input_xml="<section><momentti>1 mom.</momentti><p>...</p></section>",
                expects=("subsection:sparse_body",),
            ),
        ),
    ),
    when=(PayloadAtom.SPARSE_SUBSECTION_BODY,),
    emits=(
        EmitRef(
            EmitId.CLASSIFY_SPARSE_SUBSECTION_BODY,
            (("subsection", "subsection"),),
        ),
    ),
)

PAYLOAD_INTRO_LIST_CONTINUATION = PayloadRule(
    header=RuleHeader(
        rule_id="fi.payload.intro_list_continuation",
        phase=RulePhase.PAYLOAD_NORMALIZE,
        priority=125,
        authority=AuthorityTier.ENACTED_TEXT,
        strength=RuleStrength.LITERAL,
        purpose="Carry intro/list continuations as structured continuation payload",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="payload family"),),
        examples=(
            RuleExample(
                label="intro continuation",
                input_text="edellä 1 momentissa tarkoitetussa kohdassa ...",
                expects=("intro_continuation:structured",),
            ),
        ),
    ),
    when=(PayloadAtom.INTRO_LIST_CONTINUATION,),
    emits=(
        EmitRef(
            EmitId.CLASSIFY_INTRO_LIST_CONTINUATION,
            (("continuation", "intro_list"),),
        ),
    ),
)
