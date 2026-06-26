"""Tests for the reference-SET fold (range/coordination vs ambiguity vs open).

The flattened ``ReferenceMention`` relation emits one row per expanded target,
so a written range ("33—35 artiklassa", "69 d–69 g §:ssä") is indistinguishable
from a candidate ambiguity once emitted. :func:`fold_reference_set` restores the
set identity: one source expression -> ONE ``ReferenceExpression`` + ONE
``ReferenceResolution`` carrying the whole target set and its set semantics.

These tests prove:
  * the EU-directive range path ("33—35 artiklassa" -> 3 flattened mentions)
    folds to ONE expression with ALL_VALID + 3 targets;
  * a section range ("69 d–69 g §:ssä") folds the same way;
  * a candidate ambiguity folds to CANDIDATE_AMBIGUITY;
  * an open/unresolved-but-referent reference folds to OPEN;
  * ``surface_expr_id`` is stable and content-addressed;
  * the flattened ``ReferenceMention`` projection is unchanged (the emitter
    still produces N rows — the fold is additive).
"""
from __future__ import annotations

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceExpression,
    ReferenceMention,
    ReferenceResolutionStatus,
    ReferenceTargetSetSemantics,
    SourceSpan,
    compute_surface_expr_id,
)
from lawvm.finland.references.eu_directive import recognize_eu_directive_refs
from lawvm.finland.references.reference_sets import fold_reference_set


def _mention(
    target: ProvisionRef | None,
    *,
    confidence: CiteConfidence,
    surface: str,
    span: SourceSpan | None = None,
) -> ReferenceMention:
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="711/2022", section_label="5"),
        target_provision_ref=target,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=confidence,
        phrase_lemma="test_pattern",
        source_span=span,
        valid_at_interval=(None, None),
        edge_subtype=None,
        surface_text=surface,
    )


# ---------------------------------------------------------------------------
# EU-directive ARTICLE range (real emitter output)
# ---------------------------------------------------------------------------


def test_eu_directive_article_range_folds_to_one_all_valid_set() -> None:
    refs = recognize_eu_directive_refs("teollisuuspäästödirektiivin 33—35 artiklassa")
    # The flattened projection is unchanged: still one row per expanded article.
    assert len(refs) == 3
    articles = sorted(r.article for r in refs)
    assert articles == ["33", "34", "35"]

    mentions = [r.mention for r in refs]
    folded = fold_reference_set(mentions)

    # ONE expression, ONE resolution carrying the whole set.
    assert isinstance(folded.expression, ReferenceExpression)
    assert folded.expression.expression_kind == "range"
    assert folded.expression.surface_text == "33—35 artiklassa"

    res = folded.resolution
    assert res.target_set_semantics is ReferenceTargetSetSemantics.ALL_VALID
    assert res.reference_status is ReferenceResolutionStatus.RESOLVED
    assert len(res.target_set) == 3
    assert sorted(t.section_label for t in res.target_set) == ["33", "34", "35"]
    # The set is carried as targets, NOT N ungrouped rows.
    assert res.surface_expr_id == folded.expression.surface_expr_id


def test_eu_directive_coordination_folds_to_all_valid() -> None:
    refs = recognize_eu_directive_refs("teollisuuspäästödirektiivin 33 ja 35 artiklassa")
    assert len(refs) == 2
    folded = fold_reference_set([r.mention for r in refs])
    assert folded.expression.expression_kind == "coordination"
    # A coordination is ALSO ALL_VALID — every listed target is denoted.
    assert folded.resolution.target_set_semantics is ReferenceTargetSetSemantics.ALL_VALID
    assert sorted(t.section_label for t in folded.resolution.target_set) == ["33", "35"]


# ---------------------------------------------------------------------------
# SECTION range ("69 d–69 g §:ssä") — simulated emitter flattening
# ---------------------------------------------------------------------------


def test_section_range_folds_to_one_all_valid_set() -> None:
    surface = "69 d–69 g §:ssä"
    span = SourceSpan(source_file="711/2022.xml", byte_offset=100, byte_len=len(surface))
    members = ["69d", "69e", "69f", "69g"]
    mentions = [
        _mention(
            ProvisionRef(statute_id="711/2022", section_label=label),
            confidence=CiteConfidence.EXACT,
            surface=surface,
            span=span,
        )
        for label in members
    ]
    folded = fold_reference_set(mentions)
    assert folded.expression.expression_kind == "range"
    assert folded.resolution.target_set_semantics is ReferenceTargetSetSemantics.ALL_VALID
    assert folded.resolution.reference_status is ReferenceResolutionStatus.RESOLVED
    assert [t.section_label for t in folded.resolution.target_set] == members


# ---------------------------------------------------------------------------
# Ambiguity -> CANDIDATE_AMBIGUITY
# ---------------------------------------------------------------------------


def test_ambiguous_reference_folds_to_candidate_ambiguity() -> None:
    surface = "tietosuoja-asetuksen 6 artiklassa"
    mentions = [
        _mention(
            ProvisionRef(statute_id="eu-nickname:tietosuoja-asetus", section_label="6"),
            confidence=CiteConfidence.AMBIGUOUS,
            surface=surface,
        ),
    ]
    folded = fold_reference_set(mentions)
    assert (
        folded.resolution.target_set_semantics
        is ReferenceTargetSetSemantics.CANDIDATE_AMBIGUITY
    )
    assert folded.resolution.reference_status is ReferenceResolutionStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# Open / unresolved-but-referent -> OPEN
# ---------------------------------------------------------------------------


def test_open_vague_reference_folds_to_open() -> None:
    surface = "muussa laissa"
    mentions = [
        _mention(None, confidence=CiteConfidence.OPEN, surface=surface),
    ]
    folded = fold_reference_set(mentions)
    assert folded.resolution.target_set_semantics is ReferenceTargetSetSemantics.OPEN
    assert folded.resolution.target_set == ()
    assert folded.resolution.reference_status is ReferenceResolutionStatus.UNRESOLVED


def test_unresolved_no_referent_folds_to_no_enumerable_extension() -> None:
    surface = "999 §:ssä"
    mentions = [
        _mention(None, confidence=CiteConfidence.UNRESOLVED, surface=surface),
    ]
    folded = fold_reference_set(mentions)
    assert (
        folded.resolution.target_set_semantics
        is ReferenceTargetSetSemantics.NO_ENUMERABLE_EXTENSION
    )


# ---------------------------------------------------------------------------
# SINGLE
# ---------------------------------------------------------------------------


def test_single_target_folds_to_single() -> None:
    mentions = [
        _mention(
            ProvisionRef(statute_id="711/2022", section_label="7"),
            confidence=CiteConfidence.EXACT,
            surface="7 §:ssä",
        ),
    ]
    folded = fold_reference_set(mentions)
    assert folded.expression.expression_kind == "single"
    assert folded.resolution.target_set_semantics is ReferenceTargetSetSemantics.SINGLE
    assert len(folded.resolution.target_set) == 1


# ---------------------------------------------------------------------------
# surface_expr_id is stable + content-addressed
# ---------------------------------------------------------------------------


def test_surface_expr_id_is_content_addressed_and_stable() -> None:
    span = SourceSpan(source_file="711/2022.xml", byte_offset=5, byte_len=15)
    expr1 = ReferenceExpression.create("33—35 artiklassa", span, "range")
    expr2 = ReferenceExpression.create("33—35 artiklassa", span, "range")
    # Stable: same inputs -> same id.
    assert expr1.surface_expr_id == expr2.surface_expr_id
    assert expr1.surface_expr_id.startswith("sha256:")
    # Content-addressed: matches the standalone computation.
    assert expr1.surface_expr_id == compute_surface_expr_id(
        "33—35 artiklassa", span, "range"
    )
    # Distinct surface or span -> distinct id.
    assert expr1.surface_expr_id != compute_surface_expr_id(
        "33—36 artiklassa", span, "range"
    )
    other_span = SourceSpan(source_file="711/2022.xml", byte_offset=99, byte_len=15)
    assert expr1.surface_expr_id != compute_surface_expr_id(
        "33—35 artiklassa", other_span, "range"
    )


def test_reference_expression_rejects_forged_id() -> None:
    import pytest

    span = SourceSpan(source_file="x.xml", byte_offset=0, byte_len=1)
    with pytest.raises(ValueError, match="content address"):
        ReferenceExpression(
            surface_text="7 §:ssä",
            source_span=span,
            expression_kind="single",
            surface_expr_id="sha256:deadbeef",
        )


def test_fold_empty_mentions_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="at least one"):
        fold_reference_set([])
