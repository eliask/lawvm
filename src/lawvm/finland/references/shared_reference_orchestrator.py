"""Shared inline-(id) citation-construction lift — ONE canonical lifter.

Pro reference-family UNIFICATION: the inline-``(NUMBER/YEAR)`` plain-text
citation-construction lane had TWO rival lifters of the SAME grammar
(:func:`…sentence_parse.parse_citation_sentence`):

  * the PRODUCTION :class:`ReferenceLens` lane
    (``ref_mention_extractor.extract_inline_id_construction_mentions``): walks the
    AKN body ``<p>`` elements, runs the construction parse over each ``<p>``'s
    non-ref text, and hand-builds canonical :class:`ReferenceMention`s (cited-act
    id re-oriented YEAR/NUMBER, surface extended LEFT to the statute-name head,
    chapter-qualified provision path, per-target dedup, self-ref skip);
  * the FOREST projection (``reference_projection.project_forest_references``):
    reparsed each forest segment with the SAME construction parse but lifted via a
    DIFFERENT path (``sentence_parse.sentence_parse_to_mentions``), with a
    different cited-act orientation step, a different surface, a different
    provision-path convention, no per-target dedup and no self-ref skip.

Two lifters of one grammar = drift. This module is the single canonical lifter:
:func:`lift_inline_id_construction_mentions` takes the already-collected citation
text + the citing statute id and returns the canonical mention set + the
per-target dedup key set. The byte/char span coordinate is supplied by the caller
through ``span_for_surface`` (the lens passes a byte-offset-into-``xml_bytes``
locator; the forest passes a char-offset-into-segment-text locator), so BOTH call
sites share the IDENTICAL mention-building logic while keeping their own
coordinate system.

Surface-only: no apply / replay / bench import. Pure text → mentions.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Callable, Optional, Tuple

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.references.by_name import name_head_np_start_before_paren
from lawvm.finland.references.cross_refs import _make_statute_id
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    chapter_akn_path,
)
from lawvm.finland.legal_surface.sentence_parse import (
    SentenceParse,
    parse_citation_sentence,
)

#: The ``phrase_lemma`` every inline-(id) citation-construction mention carries —
#: the canonical lane key (the forest-owned subset key the differential filters
#: the lens to; see ``reference_projection.FOREST_OWNED_PHRASE_LEMMA``).
CITATION_CONSTRUCTION_PHRASE_LEMMA = "citation_construction"


# Single-token statute-name head immediately before the ``(id)`` paren — the
# fallback when the by-name NP recognizer finds no clean head abutting the paren.
_NAME_HEAD_BEFORE_PAREN_RE = re.compile(
    r"([A-Za-z\xe4\xf6\xe5\xc4\xd6\xc5\-]{1,60})\s?$"
)


def extend_surface_to_name_head(text: str, paren_start: int, anchor_end: int) -> str:
    """Surface for a construction cite, extended LEFT to the statute-name head.

    The construction parse's anchor begins at the ``(id)`` paren (the name head is
    owned as benign prose for total-ownership accounting). The canonical mention,
    however, must carry the name-head-inclusive surface (``arvonlisäverolain
    (1767/95) 128 §``) so downstream span-overlap consumers (the surface graph, the
    census by-name dedup) anchor it identically.

    The name head is parsed as a proper inflected NP via the shared by-name
    recognizer (``by_name.name_head_np_start_before_paren``), so an intervening
    modifier between the head and the paren is INCLUDED in the surface
    (``annettu opetusministeriön asetus (253/2001)``;
    ``valvotusta koevapaudesta annetun lain (629/2013)``). When the NP recognizer
    finds no clean name head abutting the paren, it falls back to the contiguous
    single-token left-scan (``arvonlisäverolain``, ``perintökaaren``); when even
    that finds nothing (a bare paren) the surface is unchanged (starts at the
    paren). Tag-don't-guess: the NP path never fabricates a boundary.
    """
    np_start = name_head_np_start_before_paren(text, paren_start)
    if np_start is not None:
        return text[np_start:anchor_end]
    left = text[max(0, paren_start - 61) : paren_start]
    m = _NAME_HEAD_BEFORE_PAREN_RE.search(left)
    if m is None:
        return text[paren_start:anchor_end]
    head_start = paren_start - (len(left) - m.start(1))
    return text[head_start:anchor_end]


def inline_id_provision_key(ref: Optional[ProvisionRef]) -> Optional[str]:
    """Statute+provision dedup key for one inline-(id) citation TARGET.

    Built from the final ``ProvisionRef`` fields (statute id + AKN provision path +
    section/momentti/kohta labels). Returns None for a target with no statute id
    (nothing to dedup on; the caller keeps such a mention).
    """
    if ref is None or not ref.statute_id:
        return None
    return "/".join(
        part
        for part in (
            ref.statute_id,
            ref.provision_path or "",
            ref.section_label or "",
            str(ref.subsection_num) if ref.subsection_num is not None else "",
            ref.item_label or "",
        )
    )


#: A caller-supplied span locator. Given the cite's name-inclusive surface text,
#: returns the :class:`SourceSpan` for it (or None when the surface cannot be
#: located) in the caller's OWN coordinate system. The lens passes a
#: byte-offset-into-``xml_bytes`` locator; the forest passes a
#: char-offset-into-segment-text locator. Keeping the coordinate out of the lift
#: is what lets ONE lifter serve both call sites without either losing its anchor.
SpanForSurface = Callable[[str], Optional[SourceSpan]]


def lift_inline_id_construction_mentions(
    text: str,
    statute_id: str,
    *,
    valid_at_interval: Tuple[Optional[date], Optional[date]] = (None, None),
    covered_statute_ids: Optional[set[str]] = None,
    span_for_surface: SpanForSurface,
    sentence_parse: Optional[SentenceParse] = None,
) -> Tuple[list[ReferenceMention], set[str]]:
    """Lift one citation-bearing TEXT span to canonical citation-construction mentions.

    THE single canonical lifter shared by the production :class:`ReferenceLens`
    citation lane and the forest reference projection. Runs the construction parse
    over ``text`` (or reuses a ``sentence_parse`` the caller already computed),
    re-orients each cited-act id NUMBER/YEAR → canonical YEAR/NUMBER via
    :func:`_make_statute_id`, extends the surface LEFT to the statute-name head, and
    builds one CROSS_STATUTE :class:`ReferenceMention` per expanded provision target
    (``phrase_lemma="citation_construction"``).

    Args:
        text:                The citation-bearing text (the lens passes a ``<p>``'s
                             non-ref text; the forest passes a structural segment's
                             body substring). Anchor offsets in the returned spans
                             are interpreted by ``span_for_surface``.
        statute_id:          The CITING statute id (canonical YEAR/NUMBER), used to
                             (a) skip self-citations and (b) bound the construction
                             parse's 2-digit-year century pivot causally.
        valid_at_interval:   (start, end) threaded onto each mention.
        covered_statute_ids: Cited target ids already owned by another lane (the
                             ``<ref>`` lane's CITES targets); a construction cite to
                             a covered act is skipped (single-source the occurrence).
        span_for_surface:    Locates the name-inclusive surface in the caller's
                             coordinate system (bytes / chars). Called once per
                             distinct surface; multiple targets from one anchor share
                             the span.
        sentence_parse:      An already-computed ``SentenceParse`` for ``text`` (the
                             caller may have parsed it for a guard); when None it is
                             computed here. Either way the citing id bounds the pivot.

    Returns:
        ``(mentions, covered_keys)`` — the canonical mentions and the per-target
        dedup keys (:func:`inline_id_provision_key`) this lift emitted, so the
        caller can filter a regex fallback to genuine residue.
    """
    covered = covered_statute_ids or set()
    valid_start, valid_end = valid_at_interval

    mentions: list[ReferenceMention] = []
    covered_keys: set[str] = set()

    sp = (
        sentence_parse
        if sentence_parse is not None
        else parse_citation_sentence(text, source_statute_id=statute_id)
    )
    if not sp.citations:
        return mentions, covered_keys

    # Per-text span cache so multiple targets from one anchor share a span (and do
    # not advance a byte/char cursor past one another).
    surface_span_cache: dict[str, Optional[SourceSpan]] = {}
    seen_keys: set[str] = set()

    for c in sp.citations:
        # The construction keys the cited act NUMBER/YEAR (canonical Finnish
        # surface). Re-orient to the canonical corpus key YEAR/NUMBER via the SAME
        # helper the <ref> and regex lanes use, so this lane dedups onto the SAME
        # entity node (no re-inverted id).
        num_str, year_str = c.statute_id.split("/", 1)
        target_statute_id = _make_statute_id(year_str, num_str)
        if target_statute_id in covered:
            continue
        if target_statute_id == statute_id:
            continue
        # Extend the surface LEFT to the statute-name head so the mention carries the
        # name-inclusive surface; ``c.anchor_start`` is the paren start.
        surface_text = extend_surface_to_name_head(text, c.anchor_start, c.anchor_end)
        if surface_text in surface_span_cache:
            span = surface_span_cache[surface_text]
        else:
            span = span_for_surface(surface_text)
            surface_span_cache[surface_text] = span
        targets = list(c.targets) or [BodyProvisionTarget(section_label="")]
        for tgt in targets:
            src_ref = ProvisionRef(
                statute_id=statute_id,
                provision_path="",
                section_label="",
            )
            target_provision_path = (
                chapter_akn_path(tgt.chapter, tgt.section_label)
                if tgt.chapter is not None
                else ""
            )
            tgt_ref = ProvisionRef(
                statute_id=target_statute_id,
                provision_path=target_provision_path,
                section_label=tgt.section_label,
                subsection_num=tgt.subsection_num,
                item_label=tgt.item_label,
            )
            key = inline_id_provision_key(tgt_ref)
            if key is not None:
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                covered_keys.add(key)

            cite_confidence = (
                CiteConfidence.EXACT
                if tgt.section_label
                else CiteConfidence.STATUTE_ONLY
            )
            mentions.append(
                ReferenceMention(
                    source_provision_ref=src_ref,
                    target_provision_ref=tgt_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=cite_confidence,
                    phrase_lemma=CITATION_CONSTRUCTION_PHRASE_LEMMA,
                    source_span=span,
                    valid_at_interval=(valid_start, valid_end),
                    edge_subtype="CITES",
                    surface_text=surface_text,
                )
            )

    return mentions, covered_keys
