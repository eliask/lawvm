"""Immutable scan artifacts for Finnish amendment clause parsing.

This module provides the annotation-based token view layer that replaces
destructive filter-and-skip patterns.  The key insight (from the Pro PEG3
review): instead of producing new token lists by deleting/replacing tokens,
keep one immutable token tape and overlay annotations.  The grammar then
consumes a view that hides annotated spans — eliminating the need for
manual span-token skip sites throughout the parser.

Architecture:
    tokenize(text) → list[Token]           (immutable tape, unchanged)
    annotate(tokens) → AnnotatedStream     (annotations over tape)
    stream.structural_view() → list[Token] (what the parser sees)

The structural view produces the SAME output as the current apply_filters()
pipeline.  This enables incremental migration: filters are converted to
annotation producers one at a time, with round-trip verification.

Design:
    - Annotations carry kind, span, and optional detail
    - Multiple annotations may overlap (nested provenance + citation)
    - The structural view replaces each annotation span with a single
      sentinel token (CITATION_SPAN, PROVENANCE_SPAN, etc.) — exactly
      what the current filters do, but derivable from annotations rather
      than baked into a mutated token list
    - Future: the parser can consume annotations directly (checking
      "is this position inside an annotation?" instead of "is this a
      span sentinel token?"), eliminating ALL skip sites
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import Optional
from lawvm.finland.johtolause.lexicon import Token


# ---------------------------------------------------------------------------
# Span and Annotation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open index range into the original token list."""

    start: int  # inclusive
    end: int  # exclusive

    def __len__(self) -> int:
        return self.end - self.start

    def __contains__(self, idx: int) -> bool:
        return self.start <= idx < self.end


@dataclass(frozen=True, slots=True)
class Annotation:
    """An overlay on the immutable token tape.

    Attributes:
        kind: Classification of the annotated span.  Current kinds:
            "citation"       — statute citation (YYYY/NNN) + backwards name
            "statute_name"   — statute name without citation anchor
            "provenance"     — reinstatement history clause
            "reinstatement"  — reinstatement preamble
            "jolloin"        — consequence clause
            "end_sentinel"   — seuraavasti: terminal
        span: Token index range covered by this annotation.
        sentinel_cat: Token category for the sentinel emitted in structural
            view.  Maps to the existing span token categories:
            "CITATION_SPAN", "PROVENANCE_SPAN", "REINST_SPAN", etc.
        detail: Optional structured data carried by the annotation
            (e.g. the citation text, the provenance clause text).
    """

    kind: str
    span: Span
    sentinel_cat: str
    detail: Optional[dict] = None


# ---------------------------------------------------------------------------
# Sentinel tokens — built from sentinels.py (single source of truth)
# ---------------------------------------------------------------------------

from lawvm.finland.johtolause.sentinels import _SENTINEL_SPECS

_SENTINEL_TOKENS: dict[str, Token] = {
    spec.cat: Token(text=spec.text, lemma=spec.lemma, cat=spec.cat, case="", verb_code=None) for spec in _SENTINEL_SPECS
}


# ---------------------------------------------------------------------------
# AnnotatedStream
# ---------------------------------------------------------------------------


@dataclass
class AnnotatedStream:
    """Immutable token tape with annotation overlays.

    The token list is never modified.  Annotations describe which spans
    are non-structural (provenance, citations, etc.).  The structural_view()
    method produces a filtered token list compatible with the current parser,
    replacing each annotation span with a single sentinel token.
    """

    tokens: list[Token]
    annotations: list[Annotation] = field(default_factory=list)

    def _build_covered_mask(self) -> list[Optional[Annotation]]:
        """Build per-position annotation mask (outer-wins for overlaps)."""
        n = len(self.tokens)
        covered: list[Optional[Annotation]] = [None] * n
        sorted_anns = sorted(self.annotations, key=lambda a: -(a.span.end - a.span.start))
        for ann in sorted_anns:
            for i in range(ann.span.start, min(ann.span.end, n)):
                if covered[i] is None:
                    covered[i] = ann
        return covered

    def structural_view(self) -> list[Token]:
        """Produce the token list the grammar sees.

        For each annotation, the spanned tokens are hidden and replaced
        by a single sentinel token at the span's start position.
        Non-annotated tokens pass through unchanged.

        When multiple annotations overlap, the outermost (longest) span
        wins — inner annotations are subsumed.
        """
        covered = self._build_covered_mask()
        n = len(self.tokens)
        result: list[Token] = []
        i = 0
        while i < n:
            ann = covered[i]
            if ann is None:
                result.append(self.tokens[i])
                i += 1
            else:
                sentinel = _SENTINEL_TOKENS.get(ann.sentinel_cat)
                if sentinel is not None:
                    result.append(sentinel)
                i = ann.span.end
        return result

    def structural_view_with_map(self) -> tuple[list[Token], list[tuple[int, int]]]:
        """Produce structural view with position mapping back to raw tape.

        Returns:
            (view_tokens, view_to_raw) where view_to_raw[i] = (raw_start, raw_end)
            gives the half-open range of raw token positions that map to view
            position i.  For pass-through tokens this is (pos, pos+1).  For
            annotation sentinels this is the full annotation span.
        """
        covered = self._build_covered_mask()
        n = len(self.tokens)
        result: list[Token] = []
        view_to_raw: list[tuple[int, int]] = []
        i = 0
        while i < n:
            ann = covered[i]
            if ann is None:
                result.append(self.tokens[i])
                view_to_raw.append((i, i + 1))
                i += 1
            else:
                sentinel = _SENTINEL_TOKENS.get(ann.sentinel_cat)
                if sentinel is not None:
                    result.append(sentinel)
                    view_to_raw.append((ann.span.start, ann.span.end))
                i = ann.span.end
        return result, view_to_raw


# ---------------------------------------------------------------------------
# Annotation producers — one per filter, same logic as peg3.strip_*
# ---------------------------------------------------------------------------


def _remap_annotation(
    ann: Annotation,
    view_to_raw: list[tuple[int, int]],
) -> Annotation:
    """Map an annotation from structural-view coordinates to raw-tape coordinates.

    Uses the view_to_raw position map from structural_view_with_map() to
    translate span boundaries.  The raw span is the union of raw ranges
    covered by view positions [ann.span.start, ann.span.end).
    """
    if ann.span.end <= 0 or ann.span.start >= len(view_to_raw):
        return ann
    raw_start = view_to_raw[ann.span.start][0]
    # ann.span.end is exclusive; last covered view position is end-1
    last_view = min(ann.span.end - 1, len(view_to_raw) - 1)
    raw_end = view_to_raw[last_view][1]
    return Annotation(
        kind=ann.kind,
        span=Span(raw_start, raw_end),
        sentinel_cat=ann.sentinel_cat,
        detail=ann.detail,
    )


_STRUCT_CATS = frozenset({"PYKALA", "MOMENTTI", "KOHTA", "LIITE", "OTSIKKO", "JOHD"})


def _normalize_cited_statute_id(raw: str) -> str:
    """Normalize a textual statute citation like ``575/2018`` to ``2018/575``."""
    value = str(raw or "").strip().strip("()")
    parts = value.split("/", 1)
    if len(parts) != 2:
        return value
    left, right = parts
    if left.isdigit() and right.isdigit():
        return f"{right}/{int(left)}"
    return value


def _section_labels_from_tokens(tokens: list[Token], start: int, end: int) -> tuple[str, ...]:
    """Return normalized section labels from one raw-token provenance fragment."""
    labels: list[str] = []
    pending: list[str] = []
    i = start
    while i < end:
        tok = tokens[i]
        if tok.cat == "NUM":
            label = tok.text
            j = i + 1
            if j < end and tokens[j].cat == "LETTER":
                label += tokens[j].text
                j += 1
            pending.append(label)
            i = j
            continue
        if tok.cat == "PYKALA":
            for label in pending:
                normalized = re.sub(r"\s+", "", label).lower()
                if normalized and normalized not in labels:
                    labels.append(normalized)
            pending.clear()
            i += 1
            continue
        if tok.cat == "CITE":
            pending.clear()
            i += 1
            continue
        i += 1
    return tuple(labels)


def extract_target_version_bindings(tokens: list[Token]) -> tuple["SurfaceTargetVersionBinding", ...]:
    """Extract explicit per-target cited-version selectors from raw tokens."""
    from lawvm.finland.johtolause.surface_model import SurfaceTargetVersionBinding, SurfaceWitness

    n = len(tokens)
    results: list[SurfaceTargetVersionBinding] = []
    i = 0
    while i < n:
        if tokens[i].cat != "PROV":
            i += 1
            continue
        j = i + 1
        while j < n and tokens[j].cat not in {"VERB", "END", "UUSI"}:
            j += 1
        span_end = j

        k = i + 1
        while k < span_end:
            labels_start = k
            while (
                k < span_end
                and not (
                    tokens[k].text.lower() == "laissa"
                    and k + 1 < span_end
                    and tokens[k + 1].cat == "CITE"
                )
            ):
                k += 1
            if k >= span_end:
                break
            target_labels = _section_labels_from_tokens(tokens, labels_start, k)
            if target_labels:
                cited_statute_id = _normalize_cited_statute_id(tokens[k + 1].text)
                results.append(
                    SurfaceTargetVersionBinding(
                        target_labels=target_labels,
                        cited_statute_id=cited_statute_id,
                        witness=SurfaceWitness(
                            rule_id="fi.target_version_binding",
                            source_span=(labels_start, k + 2),
                        ),
                    )
                )
            k += 2
            while k < span_end and tokens[k].cat in {"COMMA", "CONJ"}:
                k += 1
        i = span_end
    return tuple(results)


def annotate_statute_citations(tokens: list[Token]) -> list[Annotation]:
    """Produce citation annotations — annotation equivalent of strip_statute_citations.

    For each (YYYY/NNN) citation, walks backwards consuming the statute name
    until a VERB or structural keyword.  Returns one Annotation per citation span.
    """
    n = len(tokens)
    annotations: list[Annotation] = []

    i = 0
    while i < n:
        # Detect citation
        cite_start = cite_end = -1
        t = tokens[i]
        if t.cat == "CITE":
            cite_start, cite_end = i, i + 1
        elif t.text == "(" and i + 2 < n and tokens[i + 1].cat == "CITE" and tokens[i + 2].text == ")":
            cite_start, cite_end = i, i + 3
        elif t.text == "(" and i + 1 < n and tokens[i + 1].cat == "CITE":
            cite_start, cite_end = i, i + 2

        if cite_start < 0:
            i += 1
            continue

        # Walk backwards to find statute name start
        j = cite_start - 1
        _saw_structural_before_prov = False
        while j >= 0:
            bt = tokens[j]
            if bt.cat == "VERB":
                break
            if bt.cat == "REINST":
                break
            if bt.cat == "PROV":
                if _saw_structural_before_prov:
                    break
            if bt.cat in ("LUKU", "PYKALA"):
                _saw_structural_before_prov = True
            if bt.cat == "LUKU" and bt.case == "ILL":
                break
            if bt.cat in _STRUCT_CATS:
                if (
                    bt.cat == "PYKALA"
                    and bt.case == "NOM"
                    and bt.text.endswith((":ssä", ":stä", ":ssa", ":sta", ":ää", ":ksi"))
                ):
                    j -= 1
                    continue
                if j + 1 < n and tokens[j + 1].text.endswith(("sta", "stä", "ssa", "ssä")):
                    j -= 1
                    continue
                break
            j -= 1
        name_start = j + 1

        # Trim leading conj/comma
        while name_start < cite_start and tokens[name_start].cat in ("CONJ", "COMMA"):
            name_start += 1

        # Consume trailing comma
        if cite_end < n and tokens[cite_end].cat == "COMMA":
            cite_end += 1

        if name_start < cite_end:
            annotations.append(
                Annotation(
                    kind="citation",
                    span=Span(name_start, cite_end),
                    sentinel_cat="CITATION_SPAN",
                    detail={"cite_text": tokens[cite_start].text if cite_start < n else ""},
                )
            )
        i = cite_end

    return annotations


def annotate_statute_names(
    tokens: list[Token],
    prior_annotations: list[Annotation],
) -> list[Annotation]:
    """Produce statute-name annotations — annotation equivalent of strip_statute_names.

    Complements annotate_statute_citations: handles citation-less statute names
    by scanning forward from VERB tokens and masking WORD tokens until the
    first structural target.

    Unlike the legacy filter which reads CITATION_SPAN tokens, this takes
    prior_annotations explicitly to determine which positions are already claimed.
    """
    _STRUCT = frozenset({"PYKALA", "LUKU", "OSA", "LIITE", "NIMIKE"})
    n = len(tokens)

    # Build coverage mask from prior annotations
    _covered = set()
    for ann in prior_annotations:
        for k in range(ann.span.start, ann.span.end):
            _covered.add(k)

    def _is_structural_num(i: int) -> bool:
        if tokens[i].cat != "NUM":
            return False
        for k in range(i + 1, n):
            tk = tokens[k]
            if tk.cat in _STRUCT:
                return True
            if tk.cat in ("NUM", "LETTER", "DASH", "COMMA", "CONJ"):
                continue
            break
        return False

    annotations: list[Annotation] = []
    i = 0

    while i < n:
        t = tokens[i]
        if t.cat != "VERB":
            i += 1
            continue

        i += 1  # skip past VERB

        # Skip positions already covered by prior annotations (citations)
        while i < n and i in _covered:
            i += 1

        # Temporal modifiers like "väliaikaisesti" belong to the verb phrase,
        # not the following statute-name span. Leave them on the tape and let
        # the parser skip them as verb-local adverbs.
        while i < n and tokens[i].cat == "TEMPORAL":
            i += 1
            while i < n and i in _covered:
                i += 1

        # Check if we're already at a structural position
        if i < n:
            ti = tokens[i]
            if ti.cat in ("DOC", "UUSI") or ti.cat in _STRUCT:
                continue
            if ti.cat == "LUKU":
                continue
            if ti.cat == "NUM" and _is_structural_num(i):
                continue

        # Scan forward for statute name + structural target
        skip_to = None
        has_word_before = False
        j = i
        while j < n:
            if j in _covered:
                j += 1
                continue
            tj = tokens[j]
            if tj.cat == "VERB":
                break
            if tj.cat == "PROV":
                break
            if tj.cat == "DOC" and tj.case == "ILL":
                if has_word_before:
                    skip_to = j
                break
            if tj.cat == "UUSI":
                if has_word_before:
                    skip_to = j
                break
            if tj.cat == "WORD":
                has_word_before = True
            if _is_structural_num(j) and tj.text[:1].isdigit() and has_word_before:
                skip_to = j
                break
            j += 1

        if skip_to is not None and skip_to > i:
            annotations.append(
                Annotation(
                    kind="statute_name",
                    span=Span(i, skip_to),
                    sentinel_cat="STATUTE_NAME_SPAN",
                )
            )
            i = skip_to
        # else: i already advanced past VERB, continue loop

    return annotations


def annotate_provenance(
    tokens: list[Token],
    prior_annotations: list[Annotation] | None = None,
) -> list[Annotation]:
    """Produce provenance annotations — annotation equivalent of strip_provenance.

    Reuses the existing _skip_prov_span helper from peg3 for span boundary
    computation.  The detection logic mirrors strip_provenance exactly.

    In the legacy pipeline, provenance runs AFTER citation stripping.
    prior_annotations tells this annotator which positions are already claimed
    so that overlapping citation+provenance produces the same result.
    """
    from lawvm.finland.johtolause.surface_parse import _skip_prov_span

    _PROV_CONTINUATION = frozenset(
        {
            "mainitulla",
            "mainittu",
            "mainituilla",
            "mainitun",
            "annetulla",
            "annettu",
            "annetuilla",
            "annetun",
            "viimeksi",
            "osittain",
        }
    )

    n = len(tokens)
    annotations: list[Annotation] = []

    def _is_prov_start(i: int) -> bool:
        return i < n and tokens[i].cat == "PROV"

    def _is_prov_continuation(i: int) -> bool:
        if i >= n:
            return False
        t = tokens[i]
        if t.cat == "BACKREF":
            if i + 1 < n and tokens[i + 1].cat == "PYKALA":
                return False
            return True
        return t.lemma in _PROV_CONTINUATION or t.text.lower() in _PROV_CONTINUATION

    i = 0
    while i < n:
        t = tokens[i]
        span_start = i
        skip_to = None

        # CONJ before provenance
        if t.cat == "CONJ" and i + 1 < n and _is_prov_start(i + 1):
            skip_to = _skip_prov_span(tokens, i + 1, n)
        # COMMA before provenance
        elif t.cat == "COMMA" and i + 1 < n and _is_prov_start(i + 1):
            skip_to = _skip_prov_span(tokens, i + 1, n)
        # CONJ + continuation word
        elif t.cat == "CONJ" and i + 1 < n and _is_prov_continuation(i + 1):
            skip_to = _skip_prov_span(tokens, i + 1, n)
        # COMMA + CONJ + continuation
        elif (
            t.cat == "COMMA"
            and i + 1 < n
            and tokens[i + 1].cat == "CONJ"
            and i + 2 < n
            and _is_prov_continuation(i + 2)
        ):
            skip_to = _skip_prov_span(tokens, i + 2, n)
        # Direct PROV start
        elif _is_prov_start(i):
            skip_to = _skip_prov_span(tokens, i, n)

        if skip_to is not None:
            annotations.append(
                Annotation(
                    kind="provenance",
                    span=Span(span_start, skip_to),
                    sentinel_cat="PROVENANCE_SPAN",
                )
            )
            i = skip_to
        else:
            i += 1

    return annotations


def annotate_reinstatement(tokens: list[Token]) -> list[Annotation]:
    """Produce reinstatement annotations — annotation equivalent of strip_reinstatement."""
    n = len(tokens)
    annotations: list[Annotation] = []
    i = 0

    while i < n:
        t = tokens[i]
        # Pattern 1: REINST/kumota ... TILALLE/UUSI
        if t.cat == "REINST" or (t.lemma == "kumota" and t.cat != "VERB"):
            j = i + 1
            found_end = False
            while j < n:
                if tokens[j].cat == "TILALLE":
                    annotations.append(
                        Annotation(
                            kind="reinstatement",
                            span=Span(i, j + 1),
                            sentinel_cat="REINST_SPAN",
                        )
                    )
                    i = j + 1
                    found_end = True
                    break
                if tokens[j].cat == "UUSI":
                    annotations.append(
                        Annotation(
                            kind="reinstatement",
                            span=Span(i, j),  # stop before UUSI
                            sentinel_cat="REINST_SPAN",
                        )
                    )
                    i = j
                    found_end = True
                    break
                if tokens[j].cat == "VERB":
                    break
                j += 1
            if found_end:
                continue

        # Pattern 2: NUM [LETTER] PYKALA:GEN TILALLE (section-level)
        if t.cat == "NUM":
            k = i + 1
            if k < n and tokens[k].cat == "LETTER":
                k += 1
            if (
                k < n
                and tokens[k].cat == "PYKALA"
                and tokens[k].case == "GEN"
                and k + 1 < n
                and tokens[k + 1].cat == "TILALLE"
                and k + 2 < n
                and tokens[k + 2].cat in ("UUSI", "NUM")
            ):
                annotations.append(
                    Annotation(
                        kind="reinstatement",
                        span=Span(i, k + 2),  # consume through TILALLE
                        sentinel_cat="REINST_SPAN",
                    )
                )
                i = k + 2
                continue

        i += 1

    return annotations


def annotate_end_sentinels(tokens: list[Token]) -> list[Annotation]:
    """Produce end-sentinel annotations — covers END tokens to next VERB.

    The sentinel span runs from the first END token (e.g. 'seuraavasti')
    up to but NOT including the next VERB token.  Stopping before the next
    verb preserves compound johtolause verb groups such as
    'muutetaan X seuraavasti sekä lisätään Y' where the second verb group
    starts after the END.  When no VERB follows the END, the span extends
    to the end of the stream (original behaviour for simple clauses).
    """
    n = len(tokens)
    annotations: list[Annotation] = []

    for i in range(n):
        if tokens[i].cat == "END":
            # Stop the sentinel at the next VERB so subsequent verb groups
            # remain in the structural token stream.
            end = i + 1
            while end < n and tokens[end].cat != "VERB":
                end += 1
            annotations.append(
                Annotation(
                    kind="end_sentinel",
                    span=Span(i, end),
                    sentinel_cat="END_SENTINEL_SPAN",
                )
            )
            break

    return annotations


def annotate_qualifiers(tokens: list[Token]) -> list[Annotation]:
    """Produce qualifier annotations — annotation equivalent of strip_qualifiers.

    Handles:
    - Language qualifiers (LANGQUAL) → removed (no sentinel)
    - Temporal modifiers (TEMPORAL) → removed
    - Alakohta refinements (ALAKOHTA, LETTER+ALAKOHTA patterns) → removed
    - Participial adjectives (muutettu, kumotun, ...) → removed
    - Valiotsikko-qualifier phrases → replaced with VALIOTSIKKO sentinel

    Annotations with sentinel_cat="" produce no sentinel token in the
    structural view (pure removal).  VALIOTSIKKO annotations produce a
    replacement VALIOTSIKKO token.
    """
    _PARTICIPIAL_ADJS = frozenset(
        {
            "muutettu",
            "muutetun",
            "muutettujen",
            "kumottu",
            "kumotun",
            "kumottujen",
        }
    )
    _VALIOTSIKKO_SKIP = frozenset({"sen", "pykälän"})

    n = len(tokens)
    annotations: list[Annotation] = []
    i = 0
    while i < n:
        t = tokens[i]

        # Language qualifier: "suomenkielinen sanamuoto"
        if t.cat == "LANGQUAL":
            end = i + 1
            if end < n and tokens[end].cat == "LANGQUAL":
                end += 1
            annotations.append(Annotation(kind="qualifier", span=Span(i, end), sentinel_cat=""))
            i = end
            continue

        # Temporal modifier
        if t.cat == "TEMPORAL":
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 1), sentinel_cat=""))
            i += 1
            continue

        # Alakohta: single "ALAKOHTA"
        if t.cat == "ALAKOHTA":
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 1), sentinel_cat=""))
            i += 1
            continue

        # "LETTER ALAKOHTA"
        if t.cat == "LETTER" and i + 1 < n and tokens[i + 1].cat == "ALAKOHTA":
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 2), sentinel_cat=""))
            i += 2
            continue

        # "LETTER CONJ LETTER ALAKOHTA" — multi-letter alakohta
        if (
            t.cat == "LETTER"
            and i + 3 < n
            and tokens[i + 1].cat == "CONJ"
            and tokens[i + 2].cat == "LETTER"
            and tokens[i + 3].cat == "ALAKOHTA"
        ):
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 4), sentinel_cat=""))
            i += 4
            continue

        # Orphan letter before CONJ when alakohta pattern partially stripped
        if (
            t.cat == "LETTER"
            and i + 1 < n
            and tokens[i + 1].cat == "CONJ"
            and (i + 2 >= n or tokens[i + 2].cat in ("CONJ", "NUM", "PYKALA", "COMMA"))
            and i > 0
            and tokens[i - 1].cat == "KOHTA"
        ):
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 1), sentinel_cat=""))
            i += 1
            continue

        # Participial adjective
        if t.text.lower() in _PARTICIPIAL_ADJS:
            annotations.append(Annotation(kind="qualifier", span=Span(i, i + 1), sentinel_cat=""))
            i += 1
            continue

        # Valiotsikko heading: "sen/pykälän edellä olevan/oleva väliotsikon/väliotsikko"
        if (
            t.text.lower() in _VALIOTSIKKO_SKIP
            and i + 3 < n
            and tokens[i + 1].cat == "EDELLA"
            and tokens[i + 2].lemma == "olla"
            and tokens[i + 3].cat == "OTSIKKO"
        ):
            annotations.append(
                Annotation(
                    kind="valiotsikko_qualifier",
                    span=Span(i, i + 4),
                    sentinel_cat="VALIOTSIKKO",
                )
            )
            i += 4
            continue

        # CONJ + valiotsikko: "ja sen edellä olevan väliotsikon"
        # Keep CONJ (not annotated), replace rest with VALIOTSIKKO
        if (
            t.cat == "CONJ"
            and i + 4 < n
            and tokens[i + 1].text.lower() in _VALIOTSIKKO_SKIP
            and tokens[i + 2].cat == "EDELLA"
            and tokens[i + 3].lemma == "olla"
            and tokens[i + 4].cat == "OTSIKKO"
        ):
            # CONJ at position i passes through; annotate i+1..i+5
            annotations.append(
                Annotation(
                    kind="valiotsikko_qualifier",
                    span=Span(i + 1, i + 5),
                    sentinel_cat="VALIOTSIKKO",
                )
            )
            i += 5
            continue

        # "sekä pykälän edellä olevan väliotsikon"
        if (
            t.cat == "CONJ"
            and i + 4 < n
            and tokens[i + 1].cat == "PYKALA"
            and tokens[i + 1].case == "GEN"
            and tokens[i + 2].cat == "EDELLA"
            and tokens[i + 3].lemma == "olla"
            and tokens[i + 4].cat == "OTSIKKO"
        ):
            # CONJ at position i passes through; annotate i+1..i+5
            annotations.append(
                Annotation(
                    kind="valiotsikko_qualifier",
                    span=Span(i + 1, i + 5),
                    sentinel_cat="VALIOTSIKKO",
                )
            )
            i += 5
            continue

        i += 1

    return annotations


def annotate_punct(tokens: list[Token]) -> list[Annotation]:
    """Produce annotations for PUNCT tokens (pure removal, no sentinel).

    In the legacy pipeline, strip_end_sentinels removes PUNCT tokens.
    This annotation producer handles them separately so that PUNCT removal
    and END sentinel emission are independent concerns.
    """
    annotations: list[Annotation] = []
    for i, t in enumerate(tokens):
        if t.cat == "PUNCT":
            annotations.append(Annotation(kind="punct", span=Span(i, i + 1), sentinel_cat=""))
    return annotations


# Formal title-suffix words that indicate a prior amendment act is being cited
# by its Finnish legislative title: "N §:n kumoamisesta annetun lain"
_FORMAL_TITLE_SUFFIX_WORDS = frozenset({"kumoamisesta", "muuttamisesta"})
_FORMAL_TITLE_ANCHOR_WORDS = frozenset({"annetun", "annetuilla"})


def annotate_formal_title_suffix(tokens: list[Token]) -> list[Annotation]:
    """Annotate formal title-suffix citation patterns as CITATION_SPAN.

    Finnish amendment acts are often cited by their formal legislative title:
      "N §:n kumoamisesta annetun lain (YYYY/NNN)"
      "N §:n muuttamisesta annetun lain"
      "N §:n kumoamisesta DD päivänä MM YYYY annetun lain"

    In these phrases, the section number N is a structural reference to the
    base statute's section, NOT an operative target of the current amendment.
    The operative target follows the "annetun lain [citation?]" phrase.

    The Phase 1 annotation pipeline (citation + name stripping) absorbs the
    base statute name but leaves "N §:n kumoamisesta/muuttamisesta ... annetun
    lain" exposed when the citation is absent or comes AFTER the section ref.
    This producer catches those remaining spans.

    Pattern detected (in the Phase 1 structural view):
        [VERB | CITATION_SPAN | STATUTE_NAME_SPAN]
        NUM [LETTER?] PYKALA:GEN
        WORD(kumoamisesta|muuttamisesta)
        [NUM? WORD* ...]        # optional date words
        WORD(annetun|annetuilla)
        DOC:GEN                 # lain / asetuksen / säädöksen
        [CITE?]                 # optional trailing citation

    The annotated span covers NUM through DOC:GEN (and trailing CITE if present),
    and emits a CITATION_SPAN sentinel so the grammar sees nothing structural.
    """
    n = len(tokens)
    annotations: list[Annotation] = []

    _PRECEDING_CATS = frozenset({"VERB", "CITATION_SPAN", "STATUTE_NAME_SPAN"})

    i = 0
    while i < n:
        t = tokens[i]

        # We need to be positioned just after a VERB/CITATION_SPAN/STATUTE_NAME_SPAN,
        # OR at position 0 (start of stream).  The trigger is NUM at this position
        # where a structural reference would be unexpected as an operative target.
        # Gate: current token must be NUM and the preceding token (if any) must be
        # a VERB, CITATION_SPAN, STATUTE_NAME_SPAN, or COMMA/CONJ (for multi-target
        # clauses — but in those cases this pattern would be the first target so we
        # still need the preceding-context guard).
        if t.cat != "NUM":
            i += 1
            continue

        # Guard: check the preceding token is a known context boundary
        if i > 0:
            prev = tokens[i - 1]
            if prev.cat not in _PRECEDING_CATS:
                i += 1
                continue

        # Try to match: NUM [LETTER?] PYKALA:GEN WORD(kum|muut) ... WORD(annetun) DOC:GEN [CITE?]
        span_start = i
        j = i + 1  # next position after NUM

        # Optional LETTER suffix (e.g. "2 a §")
        if j < n and tokens[j].cat == "LETTER":
            j += 1

        # PYKALA:GEN required
        if j >= n or tokens[j].cat != "PYKALA" or tokens[j].case != "GEN":
            i += 1
            continue
        j += 1  # past PYKALA:GEN

        # WORD(kumoamisesta|muuttamisesta) required
        if j >= n or tokens[j].cat != "WORD" or tokens[j].text.lower() not in _FORMAL_TITLE_SUFFIX_WORDS:
            i += 1
            continue
        j += 1  # past kumoamisesta/muuttamisesta

        # Scan forward through date words, numbers, and plain words until we find
        # WORD(annetun|annetuilla) followed by DOC:GEN
        found_anchor = False
        while j < n:
            tj = tokens[j]
            # Stop conditions: another VERB, PYKALA (structural), or end
            if tj.cat == "VERB":
                break
            if tj.cat in ("PYKALA", "LUKU", "OSA", "LIITE", "NIMIKE") and tj.case != "GEN":
                break
            # Found anchor word?
            if tj.cat == "WORD" and tj.text.lower() in _FORMAL_TITLE_ANCHOR_WORDS:
                # Next must be DOC:GEN
                if j + 1 < n and tokens[j + 1].cat == "DOC" and tokens[j + 1].case == "GEN":
                    j += 2  # consume anchor + DOC:GEN
                    # Optionally consume a trailing CITE
                    if j < n and tokens[j].cat in ("CITE", "CITATION_SPAN"):
                        j += 1
                    found_anchor = True
                    break
            j += 1

        if not found_anchor:
            i += 1
            continue

        # Emit annotation covering NUM through DOC:GEN [+ optional CITE]
        annotations.append(
            Annotation(
                kind="formal_title_suffix",
                span=Span(span_start, j),
                sentinel_cat="CITATION_SPAN",
            )
        )
        i = j  # resume scan after the consumed span

    return annotations


def _extract_renumber_pairs_from_jolloin_tokens(
    tokens: list[Token], start: int, end: int
) -> list[tuple[str, str, str]]:
    """Extract renumber pairs from a jolloin token span.

    Handles three patterns:
      - Chapter renumber: NUM [CONJ NUM]* LUKU VERB NUM [CONJ NUM]* LUKU
        e.g. "jolloin nykyinen 3 luku siirtyy 4 luvuksi"
      - Section renumber: NUM [CONJ NUM [LETTER]]* PYKALA VERB NUM [LETTER] PYKALA
        e.g. "jolloin nykyinen 10 § siirtyy 10 a §:ksi"
      - Moment renumber: NUM [CONJ NUM]* MOMENTTI VERB NUM [CONJ NUM]* MOMENTTI
        e.g. "jolloin nykyinen 4 momentti siirtyy 5 momentiksi"

    Returns a list of (source, destination, kind) triples where kind is 'L'
    (chapter), 'P' (section), or 'M' (moment).

    This replaces the regex-based _extract_jolloin_chapter_renumber_pairs()
    in peg3.py — same semantics but operating on classified tokens instead
    of raw text, eliminating the dual-parse smell.
    """
    span = tokens[start:end]
    n = len(span)

    def _is_chapter_token(tok: Token) -> bool:
        text = (tok.text or "").lower()
        return tok.cat == "LUKU" or text.startswith(("luku", "luvu"))

    def _is_section_token(tok: Token) -> bool:
        text = (tok.text or "").lower()
        return tok.cat == "PYKALA" or "§" in text

    def _is_moment_token(tok: Token) -> bool:
        text = (tok.text or "").lower()
        return tok.cat == "MOMENTTI" or text.startswith("moment")

    # Find the movement verb (siirtyy/siirtyvät/siirretään)
    verb_idx = -1
    for i, t in enumerate(span):
        if t.text.lower() in ("siirtyvät", "siirtyy", "siirtyvat", "siirretään"):
            verb_idx = i
            break
    if verb_idx < 0:
        return []

    # Determine kind: CHAPTER (LUKU before verb), SECTION (PYKALA before verb),
    # or MOMENT (MOMENTTI before verb).
    has_luku_before_verb = any(_is_chapter_token(span[i]) for i in range(verb_idx))
    has_pykala_before_verb = any(_is_section_token(span[i]) for i in range(verb_idx))
    has_momentti_before_verb = any(_is_moment_token(span[i]) for i in range(verb_idx))
    if not has_luku_before_verb and not has_pykala_before_verb and not has_momentti_before_verb:
        return []

    if has_luku_before_verb:
        kind = "L"
    elif has_pykala_before_verb:
        kind = "P"
    else:
        kind = "M"

    # Collect number groups anchored immediately to the source/destination unit
    # tokens rather than flattening the whole jolloin span.  This keeps
    # appositive qualifiers like ``sellaisena kuin se on ... annetussa laissa``
    # from polluting the source-side label extraction.

    def _collect_groups_tokens(group_tokens: list[Token]) -> list[list[str]]:
        """Collect number groups from an anchored token run.

        Returns a list of groups, where each group is a list of labels.
        A single number → ["5"].  A range 3–5 → ["3", "4", "5"].
        """
        groups: list[list[str]] = []
        n_group = len(group_tokens)
        i = 0
        while i < n_group:
            if group_tokens[i].cat != "NUM":
                i += 1
                continue
            label = group_tokens[i].text
            # Check for letter suffix on this NUM
            letter = ""
            if i + 1 < n_group and group_tokens[i + 1].cat == "LETTER":
                letter = group_tokens[i + 1].text

            # Check if this is the start of a range: NUM DASH NUM
            # (look past optional LETTER suffix)
            next_pos = i + 1 + (1 if letter else 0)
            if (
                next_pos < n_group
                and group_tokens[next_pos].cat == "DASH"
                and next_pos + 1 < n_group
                and group_tokens[next_pos + 1].cat == "NUM"
            ):
                # Range: expand start..end inclusive
                range_end_pos = next_pos + 1
                end_label = group_tokens[range_end_pos].text
                end_letter = ""
                if range_end_pos + 1 < n_group and group_tokens[range_end_pos + 1].cat == "LETTER":
                    end_letter = group_tokens[range_end_pos + 1].text

                try:
                    start_num = int(label)
                    end_num = int(end_label)
                except ValueError:
                    # Non-integer range — treat as two separate labels
                    groups.append([label + letter])
                    i = next_pos  # will pick up end_label on next iteration
                    continue

                # Expand the numeric range; letter suffixes only on first/last
                expanded: list[str] = []
                for v in range(start_num, end_num + 1):
                    if v == start_num and letter:
                        expanded.append(str(v) + letter)
                    elif v == end_num and end_letter:
                        expanded.append(str(v) + end_letter)
                    else:
                        expanded.append(str(v))
                groups.append(expanded)
                # Advance past the range: NUM [LETTER] DASH NUM [LETTER]
                i = range_end_pos + 1 + (1 if end_letter else 0)
            else:
                # Single number
                groups.append([label + letter])
                i += 1 + (1 if letter else 0)
        return groups

    unit_matcher = {
        "L": _is_chapter_token,
        "P": _is_section_token,
        "M": _is_moment_token,
    }[kind]

    src_unit_indices = [i for i in range(verb_idx) if unit_matcher(span[i])]
    dst_unit_indices = [i for i in range(verb_idx + 1, n) if unit_matcher(span[i])]
    if not src_unit_indices or not dst_unit_indices:
        return []

    src_groups = _collect_groups_tokens(span[: src_unit_indices[-1]])
    dst_groups = _collect_groups_tokens(span[verb_idx + 1 : dst_unit_indices[0]])

    # Flatten groups into individual labels
    src_nums: list[str] = [lbl for g in src_groups for lbl in g]
    dst_nums: list[str] = [lbl for g in dst_groups for lbl in g]

    if len(src_nums) != len(dst_nums) or not src_nums:
        return []

    return [(src, dst, kind) for src, dst in zip(src_nums, dst_nums)]


def annotate_jolloin(tokens: list[Token]) -> list[Annotation]:
    """Produce jolloin consequence annotations — equivalent of strip_jolloin.

    Also extracts chapter renumber pairs from the jolloin span and stores
    them in annotation.detail["renumber_pairs"]. This unifies the two
    jolloin channels (strip_jolloin + _extract_jolloin_chapter_renumber_pairs).
    """
    n = len(tokens)
    annotations: list[Annotation] = []

    def _is_structural_after(i: int) -> bool:
        if i >= n:
            return False
        t = tokens[i]
        t_text = (t.text or "").lower()
        if t.cat == "CONJ" and i + 1 < n:
            t = tokens[i + 1]
            i = i + 1
            t_text = (t.text or "").lower()
        if t.cat in ("DOC", "UUSI", "VERB", "PYKALA", "LIITE"):
            return True
        if (t.cat == "LUKU" and t.case == "ILL") or t_text.startswith(("luku", "luvu")) and t_text.endswith("ksi"):
            return True
        if (t.cat == "MOMENTTI" and t.case == "ILL") or t_text.startswith("moment") and t_text.endswith("ksi"):
            return True
        if t.cat == "NUM":
            for k in range(i + 1, min(i + 6, n)):
                tk = tokens[k]
                tk_text = (tk.text or "").lower()
                if tk.cat == "PYKALA" or "§" in tk_text:
                    return True
                if (tk.cat == "LUKU" and tk.case == "ILL") or tk_text.startswith(("luku", "luvu")) and tk_text.endswith("ksi"):
                    return True
                if (tk.cat == "MOMENTTI" and tk.case == "ILL") or tk_text.startswith("moment") and tk_text.endswith("ksi"):
                    return True
                if tk.cat in ("LUKU", "MOMENTTI", "OSA"):
                    # Fresh structural targets may carry a chapter/part prefix
                    # after the leading number, e.g. "17 luvun 2 §:ään".
                    # Keep scanning through that prefix instead of treating it
                    # as a non-structural break.
                    continue
                if tk.cat in ("NUM", "LETTER", "DASH", "COMMA", "CONJ"):
                    continue
                break
        return False

    def _is_move_word(tok: Token) -> bool:
        text = tok.text.lower()
        return text in ("siirtyvät", "siirtyy", "siirtyvat", "siirretään")

    def _conj_belongs_to_jolloin_renumber(span_start: int, conj_idx: int) -> bool:
        saw_move = any(_is_move_word(tok) for tok in tokens[span_start:conj_idx])
        if not saw_move:
            return True

        k = conj_idx + 1
        while k < n:
            tk = tokens[k]
            tk_text = (tk.text or "").lower()
            if tk.cat == "NUM":
                k += 1
                continue
            if tk.cat in ("LETTER", "DASH", "COMMA", "CONJ"):
                k += 1
                continue
            if (
                tk.cat in ("PYKALA", "LUKU", "MOMENTTI")
                or "§" in tk_text
                or tk_text.startswith(("luku", "luvu", "moment"))
            ):
                if tk_text.endswith("ksi"):
                    return True
                if tk.cat == "LUKU" and tk.case == "ILL":
                    return True
                if tk.cat == "MOMENTTI" and tk.case == "ILL":
                    return True
                return False
            break
        return False

    def _comma_belongs_to_jolloin_renumber(span_start: int, comma_idx: int) -> bool:
        saw_move = any(_is_move_word(tok) for tok in tokens[span_start:comma_idx])
        saw_unit_before = any(
            tok.cat in ("PYKALA", "LUKU", "MOMENTTI")
            or "§" in (tok.text or "")
            or (tok.text or "").lower().startswith(("luku", "luvu", "moment"))
            for tok in tokens[span_start:comma_idx]
        )
        saw_num_after = False
        k = comma_idx + 1
        while k < n:
            tk = tokens[k]
            tk_text = (tk.text or "").lower()
            if _is_move_word(tk):
                return True
            if tk.cat == "NUM":
                saw_num_after = True
                k += 1
                continue
            if tk.cat in ("LETTER", "DASH", "CONJ", "COMMA"):
                k += 1
                continue
            if (
                tk.cat in ("PYKALA", "LUKU", "MOMENTTI")
                or "§" in tk_text
                or tk_text.startswith(("luku", "luvu", "moment"))
            ):
                if saw_move:
                    # After the movement verb, a comma belongs to the renumber
                    # clause only when the following structural run is still a
                    # destination list ending in translative form (e.g.
                    # ``11 ja 12 §:ksi`` / ``2 ja 3 momentiksi``). A fresh
                    # outer target such as ``7 §:ään`` must terminate the span.
                    return saw_num_after and tk_text.endswith("ksi")
                if saw_unit_before:
                    # Qualified renumber clauses can insert an appositive after
                    # the source unit before the movement verb, e.g.
                    # ``jolloin nykyinen 8 luku, sellaisena kuin se on ...,
                    # siirtyy 9 luvuksi``. Keep that comma inside the jolloin
                    # span instead of terminating the renumber scan early.
                    return True
                k += 1
                continue
            if tk.cat in ("UUSI", "VERB", "DOC", "LIITE", "END", "PROV", "CITE", "PROVENANCE_SPAN", "CITATION_SPAN"):
                return False
            if _is_structural_after(k):
                return False
            k += 1
        return False

    i = 0
    while i < n:
        if tokens[i].cat != "JOLLOIN":
            i += 1
            continue
        span_start = i
        i += 1
        while i < n:
            jt = tokens[i]
            if jt.cat == "VERB":
                break
            # No-comma trailing insert continuation:
            # "... jolloin nykyinen 4 momentti siirtyy 5 momentiksi ja uusi 6 momentti"
            # The trailing "ja uusi ..." belongs to the outer structural clause,
            # not the jolloin consequence span.
            if jt.cat == "CONJ" and i + 1 < n:
                next_tok = tokens[i + 1]
                if next_tok.cat == "UUSI":
                    break
                if next_tok.cat == "DOC" and i + 2 < n and tokens[i + 2].cat == "UUSI":
                    break
                if _is_structural_after(i + 1) and not _conj_belongs_to_jolloin_renumber(span_start, i):
                    break
            if (
                jt.cat == "COMMA"
                and i + 1 < n
                and _is_structural_after(i + 1)
                and not _comma_belongs_to_jolloin_renumber(span_start, i)
            ):
                break
            i += 1
        renumber_pairs = _extract_renumber_pairs_from_jolloin_tokens(tokens, span_start, i)
        annotations.append(
            Annotation(
                kind="jolloin",
                span=Span(span_start, i),
                sentinel_cat="JOLLOIN_MOVE",
                detail={"renumber_pairs": renumber_pairs} if renumber_pairs else None,
            )
        )

    return annotations


def apply_annotations(tokens: list[Token]) -> list[Token]:
    """Full annotation pipeline — equivalent of apply_filters().

    Pure annotation implementation: no legacy strip_* filter imports.
    All producers run as annotation generators; their spans are combined
    on the immutable raw token tape and the final structural_view() produces
    the output.

    Architecture
    ============

    Phase 1 — citations + names (raw tape)
        annotate_statute_citations and annotate_statute_names run on the raw
        token tape.  Their annotations are combined into a Phase 1
        AnnotatedStream, which produces a structural view with CITATION_SPAN
        and STATUTE_NAME_SPAN sentinels via structural_view_with_map().

    Phase 2 — remaining producers on the Phase 1 structural view
        annotate_provenance, annotate_reinstatement, annotate_jolloin,
        annotate_qualifiers, annotate_end_sentinels, and annotate_punct all
        run on the Phase 1 structural view (so they see CITATION_SPAN
        sentinels, matching the legacy ordering).  Their annotation spans
        are then mapped back to raw-tape coordinates using the position map
        from structural_view_with_map().

    Final assembly
        All annotations (Phase 1 + remapped Phase 2) are combined on the
        raw token tape.  A single structural_view() call produces the final
        output, which is token-by-token identical to the legacy
        apply_filters() pipeline.

    Returns:
        Token list equivalent to apply_filters(tokens).
    """
    # Phase 1: citation + names on raw tokens
    cite_anns = annotate_statute_citations(tokens)
    name_anns = annotate_statute_names(tokens, cite_anns)
    phase1_anns = cite_anns + name_anns
    phase1_stream = AnnotatedStream(tokens=tokens, annotations=phase1_anns)
    view, view_to_raw = phase1_stream.structural_view_with_map()

    # Phase 2: all remaining producers on the Phase 1 structural view
    # annotate_formal_title_suffix runs first in Phase 2 so that provenance,
    # jolloin, and other Phase-2 producers see clean CITATION_SPAN sentinels
    # where formal title suffixes ("N §:n kumoamisesta annetun lain") appeared.
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

    # Combine all annotations on raw tape → final structural view
    all_anns = phase1_anns + phase2_raw_anns
    final_stream = AnnotatedStream(tokens=tokens, annotations=all_anns)
    return final_stream.structural_view()


def apply_annotations_with_jolloin_pairs(
    tokens: list[Token],
) -> tuple[list[Token], dict[int, list[tuple[str, str, str]]]]:
    """Full annotation pipeline with jolloin renumber pair map.

    Runs the same pipeline as apply_annotations() but additionally returns
    a mapping from JOLLOIN_MOVE sentinel position (in the filtered output)
    to the renumber pairs extracted from that jolloin span.

    This enables the surface parser to emit SurfaceRenumberTail nodes natively
    when it encounters a JOLLOIN_MOVE token, rather than requiring post-parse
    enrichment in api.py.

    Returns:
        (filtered_tokens, jolloin_pair_map) where:
            filtered_tokens: same as apply_annotations(tokens)
            jolloin_pair_map: dict mapping JOLLOIN_MOVE token position in
                filtered_tokens → list of (src_label, dst_label, kind) tuples.
                kind is 'L' (chapter) or 'P' (section).
                Only positions with non-empty renumber_pairs appear in the dict.
    """
    # Phase 1: citation + names on raw tokens (identical to apply_annotations)
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

    phase2_view_anns = (
        title_suffix_anns_v + prov_anns_v + reinst_anns_v + jolloin_anns_v + qual_anns_v + end_anns_v + punct_anns_v
    )
    phase2_raw_anns = [_remap_annotation(a, view_to_raw) for a in phase2_view_anns]

    all_anns = phase1_anns + phase2_raw_anns
    final_stream = AnnotatedStream(tokens=tokens, annotations=all_anns)
    filtered_tokens = final_stream.structural_view()

    # Build the JOLLOIN_MOVE position → renumber_pairs map.
    # jolloin_anns_v contains jolloin annotations in token-stream order.
    # The JOLLOIN_MOVE sentinels in filtered_tokens appear in the same order.
    # We match them positionally.
    jolloin_pair_map: dict[int, list[tuple[str, str, str]]] = {}
    jm_ann_idx = 0
    for pos, tok in enumerate(filtered_tokens):
        if tok.cat == "JOLLOIN_MOVE":
            if jm_ann_idx < len(jolloin_anns_v):
                ann = jolloin_anns_v[jm_ann_idx]
                if ann.detail and ann.detail.get("renumber_pairs"):
                    jolloin_pair_map[pos] = ann.detail["renumber_pairs"]
            jm_ann_idx += 1

    return filtered_tokens, jolloin_pair_map
