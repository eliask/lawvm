"""Source-normalization Finland rulebook family."""

from __future__ import annotations

from lawvm.finland.rulebook.atoms import SourceAtom
from lawvm.finland.rulebook.common import (
    AuthorityTier,
    CitationRef,
    EmitRef,
    RuleExample,
    RuleHeader,
    RulePhase,
    RuleStrength,
)
from lawvm.finland.rulebook.families import SourceRule
from lawvm.finland.rulebook.registries import EmitId


SOURCE_OMIT_EDITORIAL_KUMOTTU_BANNER = SourceRule(
    header=RuleHeader(
        rule_id="fi.source.omit_editorial_kumottu_banner",
        phase=RulePhase.SOURCE_NORMALIZE,
        priority=110,
        authority=AuthorityTier.ORACLE_EDITORIAL,
        strength=RuleStrength.CONVENTIONAL,
        purpose="Drop editorial kumottu banners from source normalization output",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND.md", locator="source normalization"),),
        examples=(
            RuleExample(
                label="kumottu banner",
                input_text="kumottu laki ...",
                expects=("source_normalization:drop_kumottu_banner",),
            ),
        ),
    ),
    when=(SourceAtom.EDITORIAL_KUMOTTU_BANNER,),
    emits=(EmitRef(EmitId.DROP_EDITORIAL_BANNER, (("kind", "kumottu_banner"),)),),
)

SOURCE_EDITORIAL_HEADING_NOISE = SourceRule(
    header=RuleHeader(
        rule_id="fi.source.editorial_heading_noise",
        phase=RulePhase.SOURCE_NORMALIZE,
        priority=108,
        authority=AuthorityTier.ORACLE_EDITORIAL,
        strength=RuleStrength.CONVENTIONAL,
        purpose="Drop editorial heading noise before source comparison",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND.md", locator="source normalization"),),
        examples=(
            RuleExample(
                label="editorial heading noise",
                input_text="Lain voimaantulo",
                expects=("source_normalization:drop_heading_noise",),
            ),
        ),
    ),
    when=(SourceAtom.EDITORIAL_HEADING_NOISE,),
    emits=(EmitRef(EmitId.DROP_EDITORIAL_HEADING_NOISE, (("kind", "heading_noise"),)),),
)

SOURCE_EDITORIAL_SOURCE_TAG_RECLASSIFICATION = SourceRule(
    header=RuleHeader(
        rule_id="fi.source.editorial_source_tag_reclassification",
        phase=RulePhase.SOURCE_NORMALIZE,
        priority=104,
        authority=AuthorityTier.ORACLE_EDITORIAL,
        strength=RuleStrength.CONVENTIONAL,
        purpose="Reclassify editorial source-tag wrappers instead of treating them as semantic content",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="source normalization"),),
        examples=(
            RuleExample(
                label="source tag wrapper",
                input_xml="<source>...</source>",
                expects=("source_normalization:reclassify_source_tag",),
            ),
        ),
    ),
    when=(SourceAtom.EDITORIAL_SOURCE_TAG_RECLASSIFICATION,),
    emits=(EmitRef(EmitId.RECLASSIFY_EDITORIAL_SOURCE_TAG, (("kind", "source_tag"),)),),
)

SOURCE_RECLASSIFY_SUBSECTION_WITH_ITEM_NUMBERING = SourceRule(
    header=RuleHeader(
        rule_id="fi.source.reclassify_subsection_with_item_numbering",
        phase=RulePhase.SOURCE_NORMALIZE,
        priority=102,
        authority=AuthorityTier.FINLEX_AKN,
        strength=RuleStrength.HEURISTIC,
        purpose="Reclassify impossible subsection numbering as paragraph-shaped source, while recording the normalization fact",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="source normalization"),),
        examples=(
            RuleExample(
                label="subsection carrying item numbering",
                input_xml="<subsection><num>9)</num><content>...</content></subsection>",
                expects=(
                    "reclassify subsection -> paragraph",
                    "record source_normalization_fact",
                ),
            ),
        ),
    ),
    when=(SourceAtom.IMPOSSIBLE_SUBSECTION_NUMBERING,),
    emits=(
        EmitRef(
            EmitId.RECLASSIFY_NODE_KIND,
            (("from", "subsection"), ("to", "paragraph")),
        ),
        EmitRef(
            EmitId.RECORD_SOURCE_NORMALIZATION_FACT,
            (("kind", "tag_reclassify"), ("basis", "impossible_numbering")),
        ),
    ),
)

SOURCE_SCHEMA_INVALID_BODY = SourceRule(
    header=RuleHeader(
        rule_id="fi.source.schema_invalid_body",
        phase=RulePhase.SOURCE_NORMALIZE,
        priority=100,
        authority=AuthorityTier.FINLEX_AKN,
        strength=RuleStrength.HEURISTIC,
        purpose="Flag malformed body trees as schema-invalid source instead of silently normalizing them away",
        citations=(CitationRef(source="notes/PRO_VPRI_FINLAND_RULEBOOK.md", locator="source pathology spec"),),
        examples=(
            RuleExample(
                label="invalid body tree",
                input_xml="<body><section><section></body>",
                expects=("source_normalization:flag_schema_invalid_source",),
            ),
        ),
    ),
    when=(SourceAtom.SCHEMA_INVALID_BODY,),
    emits=(EmitRef(EmitId.FLAG_SCHEMA_INVALID_SOURCE, (("reason", "schema_invalid_body"),)),),
)
