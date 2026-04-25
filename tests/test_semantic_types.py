"""Tests for Pro #16 Step 1: semantic enum types.

Verifies that enum values still match the legacy string constants where the
bridge remains, while requiring explicit typed comparisons for the enums that
are already meant to be non-string-comparable.
"""

from __future__ import annotations

import pytest

from lawvm.core.semantic_types import (
    BodyNodeRole,
    FacetKind,
    IRNodeKind,
    LabelAction,
    LabelForm,
    MetaClauseKind,
    SourceNormalizationFact,
    SourceNormalizationBasis,
    SourceNormalizationKind,
    SpanKind,
    StructureKind,
    StructuralAction,
    StructuralStatus,
    TextPatchKindEnum,
)
from lawvm.finland.johtolause.surface_model import (
    BackRefArity,
    ScopeKind,
)
from lawvm.finland.source_verb import SourceVerb
from lawvm.finland.johtolause.surface_resolve import ResolutionKind


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Core semantic types: StructuralAction
# ---------------------------------------------------------------------------

class TestStructuralAction:
    def test_values_match_action_kind_strings(self):
        """StructuralAction.value must match ActionKind literal values."""
        assert StructuralAction.REPLACE.value == "replace"
        assert StructuralAction.REPEAL.value == "repeal"
        assert StructuralAction.INSERT.value == "insert"
        assert StructuralAction.RENUMBER.value == "renumber"
        assert StructuralAction.META.value == "meta"
        assert StructuralAction.TEXT_REPLACE.value == "text_replace"
        assert StructuralAction.TEXT_REPEAL.value == "text_repeal"

    def test_all_members_present(self):
        assert len(StructuralAction) == 8


# ---------------------------------------------------------------------------
# Core semantic types: LabelAction
# ---------------------------------------------------------------------------

class TestLabelAction:
    def test_values_match_existing_strings(self):
        assert LabelAction.RENUMBER.value == "renumber"
        assert LabelAction.HEADING_REPLACE.value == "heading_replace"
        assert LabelAction.HEADING_INSERT.value == "heading_insert"

    def test_not_string_comparable(self):
        assert LabelAction.RENUMBER != "renumber"

    def test_all_members_present(self):
        assert len(LabelAction) == 3


# ---------------------------------------------------------------------------
# Core semantic types: StructureKind / FacetKind / LabelForm
# ---------------------------------------------------------------------------

class TestTypedOnlyEnums:
    def test_structure_kind_values_match_existing_strings(self):
        assert StructureKind.DOCUMENT.value == "document"
        assert StructureKind.TITLE.value == "title"
        assert StructureKind.PART.value == "part"
        assert StructureKind.DIVISION.value == "division"
        assert StructureKind.CHAPTER.value == "chapter"
        assert StructureKind.SUBCHAPTER.value == "subchapter"
        assert StructureKind.SECTION.value == "section"
        assert StructureKind.SUBSECTION.value == "subsection"
        assert StructureKind.ITEM.value == "item"
        assert StructureKind.SUBITEM.value == "subitem"
        assert StructureKind.ROW.value == "row"
        assert StructureKind.CELL.value == "cell"
        assert StructureKind.APPENDIX.value == "appendix"
        assert StructureKind.ANNEX_PART.value == "annex_part"
        assert StructureKind.DOCUMENT != "document"
        assert str(StructureKind.DOCUMENT) == "document"
        assert len(StructureKind) == 14

    def test_facet_kind_values_match_existing_strings(self):
        assert FacetKind.BODY.value == "body"
        assert FacetKind.HEADING.value == "heading"
        assert FacetKind.INTRO.value == "intro"
        assert FacetKind.TABLE.value == "table"
        assert FacetKind.TABLE_HEADER.value == "table_header"
        assert FacetKind.TABLE_BODY.value == "table_body"
        assert FacetKind.REPEAL_NOTICE.value == "repeal_notice"
        assert FacetKind.EDITORIAL_NOTICE.value == "editorial_notice"
        assert FacetKind.FOOTNOTE.value == "footnote"
        assert FacetKind.BODY != "body"
        assert str(FacetKind.BODY) == "body"
        assert len(FacetKind) == 11

    def test_span_kind_values_match_existing_strings(self):
        assert SpanKind.SUBSECTION.value == "subsection"
        assert SpanKind.PARAGRAPH.value == "paragraph"
        assert SpanKind.SENTENCE.value == "sentence"
        assert SpanKind.ITEM.value == "item"
        assert SpanKind.HEADING.value == "heading"
        assert SpanKind.INTRO.value == "intro"
        assert SpanKind.SUBPARAGRAPH.value == "subparagraph"
        assert SpanKind.HEADING != "heading"
        assert str(SpanKind.HEADING) == "heading"
        assert len(SpanKind) == 7

    def test_label_form_values_match_existing_strings(self):
        assert LabelForm.NONE.value == "none"
        assert LabelForm.ARABIC.value == "arabic"
        assert LabelForm.ARABIC_SUFFIX.value == "arabic_suffix"
        assert LabelForm.ROMAN.value == "roman"
        assert LabelForm.LETTER.value == "letter"
        assert LabelForm.COMPOUND_LETTER.value == "compound_letter"
        assert LabelForm.PAREN_ARABIC.value == "paren_arabic"
        assert LabelForm.PAREN_LETTER.value == "paren_letter"
        assert LabelForm.PAREN_COMPOUND_LETTER.value == "paren_compound_letter"
        assert LabelForm.PAREN_ROMAN.value == "paren_roman"
        assert LabelForm.TARIFF_CODE.value == "tariff_code"
        assert LabelForm.STARRED.value == "starred"
        assert LabelForm.FREE_TEXT.value == "free_text"
        assert LabelForm.NONE != "none"
        assert str(LabelForm.NONE) == "none"
        assert len(LabelForm) == 13


class TestNewTypedOnlyEnums:
    def test_body_node_role_values_match_existing_strings(self):
        assert BodyNodeRole.CANDIDATE_PAYLOAD.value == "candidate_payload"
        assert BodyNodeRole.CONTEXT_CARRIED.value == "context_carried"
        assert BodyNodeRole.OMITTED_CONTEXT.value == "omitted_context"
        assert BodyNodeRole.UNMATCHED.value == "unmatched"
        assert BodyNodeRole.CANDIDATE_PAYLOAD != "candidate_payload"
        assert str(BodyNodeRole.CANDIDATE_PAYLOAD) == "candidate_payload"
        assert len(BodyNodeRole) == 4

    def test_structural_status_values_match_existing_strings(self):
        assert StructuralStatus.LIVE.value == "live"
        assert StructuralStatus.REPEALED.value == "repealed"
        assert StructuralStatus.OMITTED.value == "omitted"
        assert StructuralStatus.RESERVED.value == "reserved"
        assert StructuralStatus.UNKNOWN.value == "unknown"
        assert StructuralStatus.LIVE != "live"
        assert str(StructuralStatus.LIVE) == "live"
        assert len(StructuralStatus) == 5

    def test_source_normalization_kind_values_match_existing_strings(self):
        assert SourceNormalizationKind.EDITORIAL_STRIP.value == "editorial_strip"
        assert SourceNormalizationKind.TAG_RECLASSIFY.value == "tag_reclassify"
        assert SourceNormalizationKind.WHITESPACE.value == "whitespace"
        assert SourceNormalizationKind.NUMBERING_REPAIR.value == "numbering_repair"
        assert SourceNormalizationKind.DUPLICATE_DROP.value == "duplicate_drop"
        assert SourceNormalizationKind.CROSS_HEADING_HOIST.value == "cross_heading_hoist"
        assert SourceNormalizationKind.SUSPICIOUS_SHAPE.value == "suspicious_shape"
        assert SourceNormalizationKind.EDITORIAL_STRIP != "editorial_strip"
        assert str(SourceNormalizationKind.EDITORIAL_STRIP) == "editorial_strip"
        assert len(SourceNormalizationKind) == 7

    def test_source_normalization_basis_values_match_existing_strings(self):
        assert SourceNormalizationBasis.SCHEMA_INVALID.value == "schema_invalid"
        assert SourceNormalizationBasis.PROFILE_INVALID.value == "profile_invalid"
        assert SourceNormalizationBasis.IMPOSSIBLE_NUMBERING.value == "impossible_numbering"
        assert SourceNormalizationBasis.EDITORIAL_CONTAMINATION.value == "editorial_contamination"
        assert SourceNormalizationBasis.MONOTONIC_LOCAL_REPAIR.value == "monotonic_local_repair"
        assert SourceNormalizationBasis.SCHEMA_INVALID != "schema_invalid"
        assert str(SourceNormalizationBasis.SCHEMA_INVALID) == "schema_invalid"
        assert len(SourceNormalizationBasis) == 5

    def test_source_normalization_fact_projects_kind_and_basis_values(self):
        fact = SourceNormalizationFact(
            statute_id="2020/1",
            kind=SourceNormalizationKind.TAG_RECLASSIFY,
            basis=SourceNormalizationBasis.SCHEMA_INVALID,
        )
        assert fact.kind_value == "tag_reclassify"
        assert fact.basis_value == "schema_invalid"


# ---------------------------------------------------------------------------
# Core semantic types: MetaClauseKind
# ---------------------------------------------------------------------------

class TestMetaClauseKind:
    def test_values_match_existing_strings(self):
        # English surface-pipeline vocabulary (meta_parse.py)
        assert MetaClauseKind.COMMENCEMENT.value == "commencement"
        assert MetaClauseKind.EXPIRY.value == "expiry"
        assert MetaClauseKind.TRANSITION.value == "transition"
        assert MetaClauseKind.DELEGATION.value == "delegation"
        assert MetaClauseKind.OTHER.value == "other"

    def test_not_string_comparable(self):
        assert MetaClauseKind.COMMENCEMENT != "commencement"
        assert MetaClauseKind.EXPIRY != "expiry"
        assert str(MetaClauseKind.COMMENCEMENT) == "commencement"

    def test_all_members_present(self):
        assert len(MetaClauseKind) == 5


# ---------------------------------------------------------------------------
# Finland-local semantic types: SourceVerb
# ---------------------------------------------------------------------------

class TestSourceVerb:
    def test_values_match_existing_strings(self):
        assert SourceVerb.MUUTTAA.value == "muuttaa"
        assert SourceVerb.KUMOTA.value == "kumota"
        assert SourceVerb.LISATA.value == "lisata"
        assert SourceVerb.SIIRTAA.value == "siirtaa"

    def test_not_string_comparable(self):
        assert SourceVerb.MUUTTAA != "muuttaa"
        assert SourceVerb.KUMOTA != "kumota"
        assert str(SourceVerb.MUUTTAA) == "muuttaa"

    def test_all_members_present(self):
        assert len(SourceVerb) == 4


# ---------------------------------------------------------------------------
# Core semantic types: TextPatchKindEnum
# ---------------------------------------------------------------------------

class TestTextPatchKindEnum:
    def test_values_match_existing_literal_strings(self):
        """TextPatchKindEnum.value must match the TextPatchKind Literal values."""
        assert TextPatchKindEnum.REPLACE.value == "replace"
        assert TextPatchKindEnum.DELETE.value == "delete"

    def test_not_string_comparable(self):
        assert TextPatchKindEnum.REPLACE != "replace"

    def test_all_members_present(self):
        assert len(TextPatchKindEnum) == 2


# ---------------------------------------------------------------------------
# Finland-specific: ScopeKind
# ---------------------------------------------------------------------------

class TestScopeKind:
    def test_values_match_scope_block_strings(self):
        """ScopeKind.value must match SurfaceScopeBlock.scope_kind strings."""
        assert ScopeKind.CHAPTER.value == "chapter"
        assert ScopeKind.PART.value == "part"

    def test_all_members_present(self):
        assert len(ScopeKind) == 2


# ---------------------------------------------------------------------------
# Finland-specific: BackRefArity
# ---------------------------------------------------------------------------

class TestBackRefArity:
    def test_values_match_backref_referent_type_strings(self):
        """BackRefArity.value must match SurfaceBackRef.referent_type strings."""
        assert BackRefArity.SINGULAR.value == "singular"
        assert BackRefArity.PLURAL.value == "plural"

    def test_all_members_present(self):
        assert len(BackRefArity) == 2


# ---------------------------------------------------------------------------
# Finland-specific: ResolutionKind
# ---------------------------------------------------------------------------

class TestResolutionKind:
    def test_values_match_resolution_witness_strings(self):
        """ResolutionKind.value must match ResolutionWitness.resolution_kind strings."""
        assert ResolutionKind.PASS_THROUGH.value == "pass_through"
        assert ResolutionKind.BACKREF_SINGULAR.value == "backref_singular"
        assert ResolutionKind.BACKREF_PLURAL.value == "backref_plural"
        assert ResolutionKind.VALIOTSIKKO_REF.value == "valiotsikko_ref"
        assert ResolutionKind.MOVE_TAIL_APPLIED.value == "move_tail_applied"
        assert ResolutionKind.RENUMBER_TAIL_APPLIED.value == "renumber_tail_applied"
        assert ResolutionKind.CROSS_VERB_MOVE_RETARGET.value == "cross_verb_move_retarget"
        assert ResolutionKind.RELABEL_FROM_CONTEXT.value == "relabel_from_context"

    def test_all_members_present(self):
        assert len(ResolutionKind) == 8


# ---------------------------------------------------------------------------
# Pro #16 Step 2: core enums expose string reprs without string equality
# ---------------------------------------------------------------------------

class TestEnumStringRepr:
    """Core enums keep readable string reprs, but not string equality."""

    def test_facet_kind_value_and_str(self):
        assert FacetKind.HEADING.value == "heading"
        assert FacetKind.INTRO.value == "intro"
        assert FacetKind.NONE.value == ""
        assert str(FacetKind.HEADING) == "heading"
        assert str(FacetKind.INTRO) == "intro"
        assert str(FacetKind.NONE) == ""

    def test_structural_action_value_and_str(self):
        assert StructuralAction.REPLACE.value == "replace"
        assert StructuralAction.REPEAL.value == "repeal"
        assert StructuralAction.INSERT.value == "insert"
        assert StructuralAction.RENUMBER.value == "renumber"
        assert StructuralAction.META.value == "meta"
        assert str(StructuralAction.REPLACE) == "replace"
        assert StructuralAction.REPLACE != "replace"

    def test_irnodekind_value_and_str(self):
        assert IRNodeKind.SECTION.value == "section"
        assert IRNodeKind.CHAPTER.value == "chapter"
        assert IRNodeKind.CROSS_HEADING.value == "crossHeading"
        assert str(IRNodeKind.SECTION) == "section"
        assert str(IRNodeKind.CROSS_HEADING) == "crossHeading"
        assert IRNodeKind.SECTION != "section"


# ---------------------------------------------------------------------------
# Cross-cutting: enum values are unique within each enum
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("enum_cls", [
    FacetKind, StructuralAction, LabelAction,
    MetaClauseKind, TextPatchKindEnum, ScopeKind, BackRefArity,
    ResolutionKind,
])
def test_enum_values_unique(enum_cls):
    """Each enum must have unique values (no accidental duplication)."""
    values = [m.value for m in enum_cls]
    assert len(values) == len(set(values)), f"Duplicate values in {enum_cls.__name__}"


# ---------------------------------------------------------------------------
# Cross-cutting: enums are importable from their declared modules
# ---------------------------------------------------------------------------

def test_core_enums_importable():
    """Core enums are importable from lawvm.core.semantic_types."""
    from lawvm.core.semantic_types import (
        LabelAction as _LA,
        MetaClauseKind as _MCK,
        StructuralAction as _SA,
        TextPatchKindEnum as _TPK,
    )
    # If we got here, imports succeeded
    assert _SA is StructuralAction
    assert _LA is LabelAction
    assert _MCK is MetaClauseKind
    assert _TPK is TextPatchKindEnum


def test_finland_enums_importable():
    """Finland-specific enums are importable from their declared modules."""
    from lawvm.finland.johtolause.surface_model import (
        BackRefArity as _BRA,
        ScopeKind as _SK,
    )
    from lawvm.finland.johtolause.surface_resolve import ResolutionKind as _RK
    assert _SK is ScopeKind
    assert _BRA is BackRefArity
    assert _RK is ResolutionKind
