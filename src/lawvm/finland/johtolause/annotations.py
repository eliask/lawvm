"""annotations — Span/tag recognizers producing Phase 2 Annotation objects.

This module owns:
  - extract_annotations(): raw tokens → (TokenTape, AnnotationSet)
  - tape_from_tokens(): list[Token] → TokenTape
  - Conversion from scan.py Annotation objects to token_model.Annotation

Architecture:
    The scan.py module already implements all annotation producers (citation,
    provenance, reinstatement, jolloin, qualifier, end-sentinel, etc.) and
    produces scan.Annotation objects with Span ranges.  This module converts
    those into the Phase 2 token_model types (Lexeme, TokenTape, Annotation,
    AnnotationSet) so that StructuralTokenView can be built from them.

    No structural parse, no op emission.
"""

from __future__ import annotations

from lawvm.finland.johtolause.lexicon import Token
from lawvm.finland.johtolause.token_model import (
    Annotation as TmAnnotation,
    AnnotationSet,
    Lexeme,
    TokenTape,
)
from lawvm.finland.johtolause.scan import (
    Annotation as ScanAnnotation,
    AnnotatedStream,
    annotate_statute_citations,
    annotate_statute_names,
    annotate_provenance,
    annotate_reinstatement,
    annotate_jolloin,
    annotate_qualifiers,
    annotate_end_sentinels,
    annotate_punct,
    annotate_formal_title_suffix,
    _remap_annotation,
)


# ---------------------------------------------------------------------------
# Token → Lexeme conversion
# ---------------------------------------------------------------------------


def _token_to_lexeme(tok: Token) -> Lexeme:
    """Convert a peg3 Token to a Phase 2 Lexeme."""
    return Lexeme(
        text=tok.text,
        lemma=tok.lemma,
        category=tok.cat,
        gram_case=tok.case,
        verb_code=tok.verb_code,
        char_start=tok.char_start,
        char_end=tok.char_end,
    )


def tape_from_tokens(tokens: list[Token], source_text: str = "") -> TokenTape:
    """Build a TokenTape from a list of peg3 Tokens.

    Args:
        tokens: Raw token list from tokenize().
        source_text: The normalized input text.  If empty, reconstructed
            from token text (lossy — whitespace not preserved).
    """
    lexemes = tuple(_token_to_lexeme(t) for t in tokens)
    if not source_text:
        source_text = " ".join(t.text for t in tokens)
    return TokenTape(source_text=source_text, lexemes=lexemes)


# ---------------------------------------------------------------------------
# scan.Annotation → token_model.Annotation conversion
# ---------------------------------------------------------------------------

# Mapping from scan.py sentinel_cat to token_model annotation kind.
# Built from sentinels.py (single source of truth).

from lawvm.finland.johtolause.sentinels import _SENTINEL_SPECS as _ALL_SENTINEL_SPECS

_SENTINEL_TO_KIND: dict[str, str] = {spec.cat: spec.kind for spec in _ALL_SENTINEL_SPECS}
_SENTINEL_TO_KIND[""] = "qualifier"


def _convert_scan_annotation(ann: ScanAnnotation) -> TmAnnotation:
    """Convert a scan.py Annotation to a Phase 2 token_model.Annotation."""
    kind = _SENTINEL_TO_KIND.get(ann.sentinel_cat, ann.kind)
    detail = dict(ann.detail) if ann.detail else {}
    # Preserve the sentinel_cat for downstream consumers that need it
    detail["sentinel_cat"] = ann.sentinel_cat
    detail["scan_kind"] = ann.kind
    return TmAnnotation(
        kind=kind,
        start=ann.span.start,
        end=ann.span.end,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Full annotation extraction pipeline
# ---------------------------------------------------------------------------


def extract_annotations(
    tokens: list[Token],
    source_text: str = "",
) -> tuple[TokenTape, AnnotationSet]:
    """Run all annotation producers on raw tokens and return Phase 2 types.

    This mirrors scan.py::apply_annotations() but produces TokenTape +
    AnnotationSet instead of a filtered token list.  The annotation spans
    are in raw-tape coordinates (indices into the original token list).

    The pipeline ordering matches apply_annotations() exactly:
      Phase 1: citations + names on raw tokens
      Phase 2: remaining producers on the Phase 1 structural view,
               then remapped back to raw-tape coordinates

    Args:
        tokens: Raw token list from tokenize().
        source_text: Normalized input text for TokenTape.

    Returns:
        (tape, annotation_set) where tape is the immutable lexical record
        and annotation_set carries all recognized non-structural spans.
    """
    tape = tape_from_tokens(tokens, source_text)

    # Phase 1: citation + names on raw tokens
    cite_anns = annotate_statute_citations(tokens)
    name_anns = annotate_statute_names(tokens, cite_anns)
    phase1_anns = cite_anns + name_anns
    phase1_stream = AnnotatedStream(tokens=tokens, annotations=phase1_anns)
    view, view_to_raw = phase1_stream.structural_view_with_map()

    # Phase 2: all remaining producers on the Phase 1 structural view
    title_suffix_anns_v = annotate_formal_title_suffix(view)
    prov_anns_v = annotate_provenance(view)
    reinst_anns_v = annotate_reinstatement(view)
    jolloin_anns_v = annotate_jolloin(view)
    qual_anns_v = annotate_qualifiers(view)
    end_anns_v = annotate_end_sentinels(view)
    punct_anns_v = annotate_punct(view)

    # Map Phase 2 annotations from view coordinates → raw-tape coordinates
    phase2_view_anns = (
        title_suffix_anns_v + prov_anns_v + reinst_anns_v + jolloin_anns_v + qual_anns_v + end_anns_v + punct_anns_v
    )
    phase2_raw_anns = [_remap_annotation(a, view_to_raw) for a in phase2_view_anns]

    # Combine all scan annotations and convert to Phase 2 types
    all_scan_anns = phase1_anns + phase2_raw_anns
    tm_annotations = tuple(_convert_scan_annotation(a) for a in all_scan_anns)

    return tape, AnnotationSet(annotations=tm_annotations)


def extract_annotations_with_scan(
    tokens: list[Token],
    source_text: str = "",
) -> tuple[TokenTape, AnnotationSet, list[ScanAnnotation]]:
    """Like extract_annotations but also returns the raw scan annotations.

    Useful for consumers that need both the Phase 2 types AND the original
    scan.py annotations (e.g. for jolloin renumber pair extraction).
    """
    tape = tape_from_tokens(tokens, source_text)

    cite_anns = annotate_statute_citations(tokens)
    name_anns = annotate_statute_names(tokens, cite_anns)
    phase1_anns = cite_anns + name_anns
    phase1_stream = AnnotatedStream(tokens=tokens, annotations=phase1_anns)
    view, view_to_raw = phase1_stream.structural_view_with_map()

    title_suffix_anns_v = annotate_formal_title_suffix(view)
    prov_anns_v = annotate_provenance(view)
    reinst_anns_v = annotate_reinstatement(view)
    jolloin_anns_v = annotate_jolloin(view)
    qual_anns_v = annotate_qualifiers(view)
    end_anns_v = annotate_end_sentinels(view)
    punct_anns_v = annotate_punct(view)

    phase2_view_anns = (
        title_suffix_anns_v + prov_anns_v + reinst_anns_v + jolloin_anns_v + qual_anns_v + end_anns_v + punct_anns_v
    )
    phase2_raw_anns = [_remap_annotation(a, view_to_raw) for a in phase2_view_anns]

    all_scan_anns = phase1_anns + phase2_raw_anns
    tm_annotations = tuple(_convert_scan_annotation(a) for a in all_scan_anns)

    return tape, AnnotationSet(annotations=tm_annotations), all_scan_anns
