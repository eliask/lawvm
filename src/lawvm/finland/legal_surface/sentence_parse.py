"""Citation-bearing-sentence construction parse — Pilot A of the SourceSyntaxGraph.

This is the FIRST proof that the johtolause strangle/census discipline works ONE
LEVEL UP, at the sentence level, for the highest-base-rate body-text sentence
family after plain prose: the **citation-bearing sentence** (a sentence/clause
that names one or more other statutes and the provisions it points at, e.g.
``... noudatetaan, mitä ympäristönsuojelulaissa (527/2014) 5 a §:ssä
säädetään``).

Position in the stack
======================
The endgame ``SourceSyntaxGraph`` deterministically parses legal-text
sublanguages with TOTAL TOKEN OWNERSHIP (every span is a typed construction, a
child, benign-uninterpreted, or an explicit residual; the invariant is
"no silent drop", NOT "no residue") as a parse FOREST over paragraph / list /
sentence, sitting BELOW the existing ``LegalSurfaceGraph`` and producing its
nodes.

This module is the cheapest first slice of that: a sentence-frame construction
for ONE family. It is purely ADDITIVE and ``surface_only`` — it makes NO
attachment or composition decisions, authorizes NO replay, and is NOT wired into
the production reference extractor or the assembler. It REUSES the already-shared
body recognizer (``references.sections.parse_body_provision_tail`` + the
production plain-text statute-citation recognizer) for the reference parsing
inside the sentence frame; it does NOT reimplement reference parsing.

The construction
================
A citation-bearing sentence is framed as:

  * zero or more **citation constructions** — each a statute-name + ``(id)``
    anchor plus the structural-tail provision targets the shared body recognizer
    extracts from the run that follows the anchor;
  * an optional **declaration marker** — the predicate cue that makes this a
    *declaration about* the cited provisions (``säädetään`` / ``tarkoitetaan`` /
    ``sovelletaan`` / ``noudatetaan`` / ``viitataan`` …). Surface-only tag; ``""``
    when no closed-list cue is present (a bare in-prose citation);
  * an explicit **residual** span list — every char of the sentence NOT owned by
    a citation construction or the declaration marker, typed by reason
    (``benign_uninterpreted_prose`` for ordinary connective text; the no-silent-
    drop invariant is satisfied because the residual is EXPLICIT, not dropped).

TOTAL TOKEN OWNERSHIP is asserted over the sentence's char range: the union of
the citation-construction spans, the declaration-marker span, and the residual
spans partitions ``[seg_start, seg_end)`` exactly. :func:`assert_total_ownership`
is the checkable postcondition the unit tests and the census raw-tape mode use.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.finland.references.sections import (
    BodyProvisionTarget,
    chapter_akn_path,
    parse_body_provision_tail,
)

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors johtolause api.parse_clause's parser_lane.
# A closed string set (not a stringly-typed free field): which lane produced the
# sentence parse, so a consumer cannot mistake a declined / fallback frame for a
# fully-owned construction parse.
# ---------------------------------------------------------------------------
#: The citation-construction grammar owned the frame (the in-scope, no-silent-drop
#: path). The only PRODUCING lane for v0.
SENTENCE_LANE_CONSTRUCTION_OWNED = "citation_construction_owned"
#: The frame declined (no recognizable citation construction in the segment); the
#: segment is handed back as typed residue rather than a guessed parse.
SENTENCE_LANE_DECLINED = "citation_construction_declined"

#: Closed list of declaration-predicate cues (casefolded lemma stems) that mark a
#: citation-bearing sentence as a *declaration about* the cited provisions. An
#: audited tuple — a new cue is a deliberate edit, never a heuristic. Matched as a
#: substring of the segment's casefolded text (surface-only; the cue's exact span
#: is recorded for ownership). Stems are chosen to cover the common Finnish
#: inflections (``säädetään``/``säädetty``, ``tarkoitetaan``/``tarkoitettu`` …).
_DECLARATION_CUE_STEMS: tuple[str, ...] = (
    "säädet",        # säädetään / säädetty / säädettyä
    "tarkoitet",     # tarkoitetaan / tarkoitettu
    "sovellet",      # sovelletaan / sovellettava
    "noudatet",      # noudatetaan / noudatettava
    "viitat",        # viitataan
    "mainit",        # mainitaan / mainittu
    "määrät",        # määrätään
)

#: A statute-id parenthetical ``(NUMBER/YEAR)`` — the mandatory anchor marker of a
#: cross-statute citation in body text (same shape the production plain-text lane
#: keys on). Bounded quantifiers only (§1.11).
_ID_PAREN_RE = re.compile(r"\(\s*(\d{1,6})\s*/\s*(\d{2,4})\s*\)")

#: How far past the ``(id)`` paren the structural tail can run before the body
#: recognizer is asked to stop. Mirrors the production plain-text lane's 120-char
#: tail window so the construction sees the same span the oracle does.
_TAIL_WINDOW = 120


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the sentence (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str  # e.g. "benign_uninterpreted_prose"


@dataclass(frozen=True)
class CitationConstruction:
    """One cross-statute citation the sentence carries.

    Attributes:
        statute_id:     Canonical ``NUMBER/YEAR`` of the cited act.
        anchor_start:   Char offset (into the SENTENCE-LOCAL text) where the cited
                        statute-name/``(id)`` anchor's recognized run begins. For
                        v0 the anchor span is taken as the ``(id)`` paren itself
                        plus the consumed structural tail (the body recognizer
                        consumes from just after the paren); the statute NAME that
                        precedes the paren is owned as benign prose (it is not in
                        the body recognizer's coordinate space).
        anchor_end:     One-past the furthest char the citation construction owns
                        (the end of the consumed structural tail, or the end of the
                        ``(id)`` paren when no tail parsed).
        targets:        The expanded provision targets the shared body recognizer
                        produced from the structural tail. Empty list means a
                        statute-level (section-less) citation.
    """

    statute_id: str
    anchor_start: int
    anchor_end: int
    targets: tuple[BodyProvisionTarget, ...]


@dataclass(frozen=True)
class SentenceParse:
    """A citation-bearing-sentence construction parse (the SentenceParse-lite IR).

    Attributes:
        seg_start / seg_end: The char range of the parsed segment, in the
                             coordinate system of the text passed to
                             :func:`parse_citation_sentence` (sentence-local).
        text:                The exact segment text (``text[0:len]`` == the span).
        kind:                ``"citation_bearing"`` when >=1 citation construction
                             was recognized; ``"declined"`` otherwise.
        declaration_marker:  The casefolded declaration cue stem present, or ``""``.
        marker_span:         (start, end) of the declaration cue occurrence, or None.
        citations:           The recognized citation constructions, in order.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    declaration_marker: str
    marker_span: tuple[int, int] | None
    citations: tuple[CitationConstruction, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = SENTENCE_LANE_CONSTRUCTION_OWNED


def _find_declaration_marker(text_low: str) -> tuple[str, tuple[int, int]] | None:
    """Find the FIRST declaration-cue stem occurrence (casefolded). Surface-only."""
    best: tuple[int, str] | None = None
    for stem in _DECLARATION_CUE_STEMS:
        i = text_low.find(stem)
        if i >= 0 and (best is None or i < best[0]):
            best = (i, stem)
    if best is None:
        return None
    start, stem = best
    return stem, (start, start + len(stem))


def parse_citation_sentence(text: str) -> SentenceParse:
    """Parse one sentence/clause span into a citation-bearing construction frame.

    ``text`` is the EXACT segment text (a sentence or clause span produced by the
    :class:`SegmentationGraph` / ``build_clause_index``). Single deterministic
    pass: locate every ``(NUMBER/YEAR)`` anchor, run the SHARED body recognizer
    (``parse_body_provision_tail``) over the structural tail that follows each,
    record the consumed span as the citation construction's owned region, tag the
    declaration cue if present, and emit EXPLICIT residuals for every other span
    so the whole segment is owned (no silent drop).

    Declines (``kind="declined"``, ``parser_lane=DECLINED``, the whole segment as
    one residual) when no ``(id)`` anchor is present — the segment is not in this
    family and is handed back as typed residue, never a guessed parse.
    """
    n = len(text)
    text_low = text.casefold()

    # Locate every statute-id anchor and parse its structural tail.
    citations: list[CitationConstruction] = []
    for m in _ID_PAREN_RE.finditer(text):
        num = int(m.group(1))
        year_raw = m.group(2)
        if len(year_raw) == 2:
            # Two-digit year expansion, identical convention to the production
            # plain-text lane (yy <= current => 20xx else 19xx).
            from datetime import date

            yy = int(year_raw)
            current_yy = date.today().year % 100
            century = 2000 if yy <= current_yy else 1900
            year = str(century + yy)
        else:
            year = year_raw
        year_int = int(year)
        if year_int < 1700 or year_int > 2100 or num <= 0 or num > 999999:
            continue
        statute_id = f"{num}/{year}"

        paren_end = m.end()
        tail = text[paren_end : paren_end + _TAIL_WINDOW]
        targets = tuple(parse_body_provision_tail(tail))

        # The construction owns the ``(id)`` paren plus the consumed structural
        # tail. ``parse_body_provision_tail`` does not report a span here, so we
        # re-derive the consumed length via the spanned variant for accuracy.
        from lawvm.finland.references.sections import (
            parse_body_provision_tail_spanned,
        )

        spanned = parse_body_provision_tail_spanned(tail)
        consumed = len(spanned.consumed_text)
        anchor_start = m.start()
        anchor_end = paren_end + consumed
        # The body recognizer trims trailing whitespace off its consumed slice but
        # the consumed_text is measured against the whitespace-normalized tail;
        # clamp to the raw segment bounds.
        if anchor_end > n:
            anchor_end = n
        citations.append(
            CitationConstruction(
                statute_id=statute_id,
                anchor_start=anchor_start,
                anchor_end=anchor_end,
                targets=targets,
            )
        )

    if not citations:
        # Not in the citation-bearing family — decline (typed residue), do not
        # fabricate a parse. The whole segment is one explicit residual.
        return SentenceParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            declaration_marker="",
            marker_span=None,
            citations=(),
            residuals=(Residual(0, n, "not_citation_bearing"),),
            parser_lane=SENTENCE_LANE_DECLINED,
        )

    marker = _find_declaration_marker(text_low)
    declaration_marker = marker[0] if marker else ""
    marker_span = marker[1] if marker else None

    # ── Total-token-ownership: every char in [0, n) is owned by a citation
    # construction, the declaration marker, or an explicit residual. Build the
    # owned-interval set, then fill every gap with a benign-prose residual. The
    # declaration marker is owned ONLY where it does not already sit inside a
    # citation-construction span (citation spans take precedence).
    owned: list[tuple[int, int]] = [(c.anchor_start, c.anchor_end) for c in citations]
    if marker_span is not None:
        ms, me = marker_span
        # only own the marker portion outside any citation span
        inside = any(s <= ms < e for (s, e) in owned)
        if not inside:
            owned.append((ms, me))
    owned.sort()
    residuals: list[Residual] = []
    cursor = 0
    for s, e in _merge_intervals(owned):
        if s > cursor:
            residuals.append(Residual(cursor, s, "benign_uninterpreted_prose"))
        cursor = max(cursor, e)
    if cursor < n:
        residuals.append(Residual(cursor, n, "benign_uninterpreted_prose"))

    return SentenceParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="citation_bearing",
        declaration_marker=declaration_marker,
        marker_span=marker_span,
        citations=tuple(citations),
        residuals=tuple(residuals),
        parser_lane=SENTENCE_LANE_CONSTRUCTION_OWNED,
    )


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Merge overlapping/adjacent [start, end) intervals (sorted input)."""
    out: list[tuple[int, int]] = []
    for s, e in intervals:
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def assert_total_ownership(sp: SentenceParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of citation-construction spans, the declaration-marker span (where
    not subsumed by a citation), and the explicit residual spans must cover every
    char of the segment with NO gap and NO silent drop. Raises ``AssertionError``
    on violation (used by the unit tests and the census raw-tape totality mode).
    """
    n = sp.seg_end - sp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = [(c.anchor_start, c.anchor_end) for c in sp.citations]
    if sp.marker_span is not None:
        spans.append(sp.marker_span)
    spans.extend((r.char_start, r.char_end) for r in sp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in segment "
            f"(first gap at {missing[0]}); SILENT DROP. text={sp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: SentenceParse -> [ReferenceMention]
# ---------------------------------------------------------------------------


def _target_to_provision_ref(
    statute_id: str, tgt: BodyProvisionTarget
) -> ProvisionRef:
    """Lift a ``BodyProvisionTarget`` to a target ``ProvisionRef``.

    Mirrors the production internal/by-name lanes' lifting: a chapter-qualified
    target gets a ``chp_N__sec_M`` AKN provision path; a bare section gets
    ``sec_N``; a chapter-only target gets ``chp_N``. The human label fields carry
    section/momentti/kohta.
    """
    if tgt.chapter is not None:
        provision_path = chapter_akn_path(tgt.chapter, tgt.section_label)
    elif tgt.section_label:
        provision_path = f"sec_{tgt.section_label}"
    else:
        provision_path = ""
    return ProvisionRef(
        statute_id=statute_id,
        provision_path=provision_path,
        section_label=tgt.section_label,
        subsection_num=tgt.subsection_num,
        item_label=tgt.item_label,
    )


def sentence_parse_to_mentions(
    sp: SentenceParse,
    source_statute_id: str,
    *,
    source_file: str = "",
) -> list[ReferenceMention]:
    """Project a citation-bearing :class:`SentenceParse` to ``ReferenceMention``s.

    One mention per (citation, expanded provision target). A statute-level
    citation (no parsed section tail) yields one ``STATUTE_ONLY`` mention; a
    provision-precise citation yields one ``EXACT`` cross-statute mention per
    target. The lifting mirrors the production plain-text statute lane
    (``cite_kind=CROSS_STATUTE``), so the projected set is directly comparable to
    the production extraction oracle for the same span.
    """
    source_ref = ProvisionRef(statute_id=source_statute_id)
    out: list[ReferenceMention] = []
    for c in sp.citations:
        targets = list(c.targets) or [BodyProvisionTarget(section_label="")]
        for tgt in targets:
            target_ref = _target_to_provision_ref(c.statute_id, tgt)
            statute_only = not tgt.section_label and tgt.chapter is None
            confidence = (
                CiteConfidence.STATUTE_ONLY if statute_only else CiteConfidence.EXACT
            )
            span = (
                SourceSpan(
                    source_file=source_file or source_statute_id,
                    byte_offset=c.anchor_start,
                    byte_len=max(0, c.anchor_end - c.anchor_start),
                )
                if source_file or source_statute_id
                else None
            )
            out.append(
                ReferenceMention(
                    source_provision_ref=source_ref,
                    target_provision_ref=target_ref,
                    cite_kind=CiteKind.CROSS_STATUTE,
                    cite_confidence=confidence,
                    phrase_lemma="citation_sentence_construction",
                    source_span=span,
                    valid_at_interval=(None, None),
                    edge_subtype="CITES",
                    surface_text=sp.text[c.anchor_start : c.anchor_end],
                )
            )
    return out


# ---------------------------------------------------------------------------
# Production reference-extraction oracle, restricted to a sentence span.
# ---------------------------------------------------------------------------


def oracle_reference_keys_for_span(text: str) -> set[str]:
    """Run the PRODUCTION plain-text statute-citation recognizer over a span.

    Wraps the segment text in a synthetic ``<p>`` element and runs the production
    ``PlainTextStatuteCitationRecognizer.scan_precise`` — the SAME recognizer the
    production reference extractor uses for plain-text body cross-statute
    citations. Returns the set of provision keys (``ProvisionRef.serialized()``
    form) the production lane finds in that span. This is the differential-census
    ORACLE: the current production reference-extraction output for the segment.

    Coordinate systems match (both the projection and the oracle operate on the
    same segment text), so the comparison is honest — no byte/char remapping.
    """
    from lawvm.finland.references.ref_mention_extractor import _PLAIN_TEXT_RECOGNIZER

    p_el: ET.Element[str] = ET.Element("p")
    p_el.text = text
    keys: set[str] = set()
    for hit in _PLAIN_TEXT_RECOGNIZER.scan_precise(p_el):
        tgt = BodyProvisionTarget(
            section_label=hit.section_label,
            subsection_num=hit.subsection_num,
            item_label=hit.item_label,
            chapter=hit.chapter,
        )
        ref = _target_to_provision_ref(hit.statute_id, tgt)
        keys.add(ref.serialized())
    return keys


def projection_reference_keys(sp: SentenceParse, source_statute_id: str) -> set[str]:
    """The projected reference set as ``ProvisionRef.serialized()`` keys."""
    keys: set[str] = set()
    for m in sentence_parse_to_mentions(sp, source_statute_id):
        if m.target_provision_ref is not None:
            keys.add(m.target_provision_ref.serialized())
    return keys
