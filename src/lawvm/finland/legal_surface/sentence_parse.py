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

#: Parenthetical FRACTION surface decline. A ``(N/M)`` paren is NOT a statute id
#: when it spells a Finnish fraction — ``kymmenesosalla (1/10)`` ("one tenth"),
#: ``kahdeskymmenesosalla (1/20)``, ``kuusitoista seitsemättätoista (16/17) osaa``.
#: The construction anchor keys on the bare ``(N/Y)`` shape (it does NOT require the
#: production plain-text lane's statute-name HEAD), so without this guard a fraction
#: parenthetical would be read as a cross-statute id. The discriminator is a closed,
#: audited SURFACE fact — a fraction-noun (``-osa`` family) sits IMMEDIATELY against
#: the paren — never a numeric-magnitude heuristic (genuine low-numbered cites like
#: ``ampuma-aselaki (1/1998)`` / ``tavaramerkkilaki (7/1964)`` keep their statute
#: name against the paren and never match). Two adjacency shapes are declined:
#:   - LEFT:  a fraction-denominator word directly precedes the ``(`` —
#:            ``…kymmenesosalla (1/10)`` (the word ends in an ``-osa`` case form).
#:   - RIGHT: a fraction-numerator/part noun directly follows the ``)`` —
#:            ``(16/17) osaa`` ("sixteen seventeenths of a part").
#: Bounded look-around windows (§1.11): a short fixed slice either side of the paren.
_FRACTION_LEFT_RE = re.compile(
    r"osa(?:lla|lle|sta|ssa|an|a|n|ksi|lta|t)?\s*$",
    re.IGNORECASE,
)
_FRACTION_RIGHT_RE = re.compile(
    r"^\s*osa(?:lla|lle|sta|ssa|an|a|n|ksi|lta|t)?\b",
    re.IGNORECASE,
)
#: Look-around window (chars) scanned either side of the ``(N/Y)`` paren for the
#: fraction-noun adjacency. A fraction noun is glued to its paren in practice, so a
#: short window suffices and keeps the guard from reaching across intervening words.
_FRACTION_WINDOW = 24


def _is_fraction_paren(text: str, paren_start: int, paren_end: int) -> bool:
    """Whether a ``(N/Y)`` paren spells a Finnish fraction, not a statute id.

    Closed audited surface decline (see ``_FRACTION_LEFT_RE``/``_FRACTION_RIGHT_RE``):
    a fraction-denominator word (``-osa`` family) sits directly against the paren on
    the left (``kymmenesosalla (1/10)``) OR a part noun follows it on the right
    (``(16/17) osaa``). Never a magnitude heuristic — a statute name against the
    paren (``ampuma-aselaki (1/1998)``) is untouched.
    """
    left = text[max(0, paren_start - _FRACTION_WINDOW) : paren_start]
    if _FRACTION_LEFT_RE.search(left):
        return True
    right = text[paren_end : paren_end + _FRACTION_WINDOW]
    if _FRACTION_RIGHT_RE.search(right):
        return True
    return False


def _expand_year(year_raw: str) -> str:
    """Expand a 2-digit paren year to 4-digit (``95`` -> ``1995``); 4-digit as-is.

    Same convention the plain-text by-id lane and :func:`parse_citation_sentence`
    use: ``yy <= current two-digit year`` -> ``20yy`` else ``19yy``. Factored so the
    citation parse AND the segment-paren orientation lookup expand identically (a
    two-digit-year paren like ``(1767/95)`` must compare against the 4-digit
    ``1767/1995`` form the projection and the oracle both carry).
    """
    if len(year_raw) == 2:
        from datetime import date

        yy = int(year_raw)
        current_yy = date.today().year % 100
        century = 2000 if yy <= current_yy else 1900
        return str(century + yy)
    return year_raw


def _segment_id_parens(text: str) -> set[str]:
    """The inline ``(NUMBER/YEAR)`` parens in ``text``, as ``NUMBER/YEAR`` strings.

    Year normalized to 4-digit (:func:`_expand_year`) so a two-digit-year paren
    matches the canonical 4-digit orientation the oracle/projection keys carry.
    """
    return {
        f"{m.group(1)}/{_expand_year(m.group(2))}"
        for m in _ID_PAREN_RE.finditer(text)
    }


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
        # Two-digit year expansion, identical convention to the production
        # plain-text lane (yy <= current => 20xx else 19xx).
        year = _expand_year(m.group(2))
        year_int = int(year)
        if year_int < 1700 or year_int > 2100 or num <= 0 or num > 999999:
            continue
        # Fraction decline: ``kymmenesosalla (1/10)`` / ``(16/17) osaa`` is not a
        # statute id. Closed audited surface adjacency, never a magnitude rule, so
        # genuine low-numbered cites (``ampuma-aselaki (1/1998)``) are untouched.
        if _is_fraction_paren(text, m.start(), m.end()):
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
    # The recognizer's ``statute_id`` follows the eId ``YEAR/NUMBER`` convention
    # since the orientation canonicalization on master; the construction projection
    # keys the canonical Finnish ``NUMBER/YEAR``. Canonicalize the span-oracle key
    # the SAME way the full-extractor oracle does so the two sets compare honestly
    # (the segment's own inline parens resolve the rare both-years-ambiguous
    # orientation).
    seg_parens = _segment_id_parens(text)
    keys: set[str] = set()
    for hit in _PLAIN_TEXT_RECOGNIZER.scan_precise(p_el):
        tgt = BodyProvisionTarget(
            section_label=hit.section_label,
            subsection_num=hit.subsection_num,
            item_label=hit.item_label,
            chapter=hit.chapter,
        )
        ref = _target_to_provision_ref(hit.statute_id, tgt)
        keys.add(
            _canonicalize_statute_key_in_segment(ref.serialized(), seg_parens)
        )
    return keys


def projection_reference_keys(sp: SentenceParse, source_statute_id: str) -> set[str]:
    """The projected reference set as ``ProvisionRef.serialized()`` keys."""
    keys: set[str] = set()
    for m in sentence_parse_to_mentions(sp, source_statute_id):
        if m.target_provision_ref is not None:
            keys.add(m.target_provision_ref.serialized())
    return keys


# ---------------------------------------------------------------------------
# Full production reference-extraction oracle (whole statute, span-bucketed).
#
# The span-restricted ``oracle_reference_keys_for_span`` above runs ONLY the
# plain-text statute-citation recognizer — it EXCLUDES the ``<ref>``-element lane,
# the by-name (``-lain``/``-kaaren``) lanes, etc. That under-counts what
# production actually finds, which INFLATES the census ``superset`` bucket with
# citations production already binds via another lane (an ORACLE ARTIFACT, not a
# genuine construction-frame win).
#
# This oracle runs the FULL production extractor (``extract_all_reference_mentions``)
# over the whole statute XML once, keeps only the CROSS-STATUTE citations (the same
# family the construction projection emits — INTERNAL self-refs, EU, treaty and
# preparatory mentions are out of family and would otherwise create phantom
# misses), buckets each mention into a citation segment by SOURCE-SPAN OVERLAP, and
# keys it by ``ProvisionRef.serialized()`` (the same key-fn the projection uses).
#
# Offset basis: a mention's ``source_span.byte_offset`` is a byte offset into the
# raw statute ``xml_bytes`` (UTF-8), NOT a char offset into the decoded body text
# the SegmentationGraph segments. Rather than reconstruct a byte→char map, each
# mention is RE-LOCATED in the decoded body text by its ``surface_text`` (the exact
# substring the recognizer matched), advancing a per-surface char cursor
# left-to-right (the same fail-loud-by-absence discipline as
# ``bundle.locate_span`` / the extractor's own ``_find_with_left_boundary``). The
# located char span is then overlap-tested against each segment's char range.
#
# Redundant statute-only collapse: for ONE citation the production extractor
# commonly emits BOTH a coarse statute-only mention (``527/2014``) AND the
# section/chapter-precise mentions (``527/2014/142``). The construction projection
# emits only the precise key for that citation, never the redundant coarse one.
# Keeping both in the oracle would score the coarse ``527/2014`` a phantom ``miss``
# against a projection that found the SAME citation MORE precisely. So a bare
# resolved statute-only key ``S`` is dropped from a segment's oracle IFF the same
# segment's oracle also carries a strictly-more-precise key ``S/…`` (see
# :func:`_collapse_redundant_statute_only`). A statute-only key with no precise
# sibling is a real statute-level citation and is kept (a genuine miss if the
# projection lacks it).
#
# Same-site by-name dedup: the production by-name HEAD lane emits an UNRESOLVED
# ``fi-name:NAME`` key for a citation EVEN WHEN the resolved-id lane binds the SAME
# ``name (id)`` site to ``NUMBER/YEAR`` (``Valmiuslaissa (1552/2011)`` → both
# ``1552/2011`` AND ``fi-name:valmiuslaki``). The two describe ONE citation; the
# projection keys it only by the resolved id. So a ``fi-name:`` key whose located
# char span OVERLAPS a resolved-id span is dropped (same site, resolved key wins) —
# this is a span-overlap fact, never a name-guess. A ``fi-name:`` key with NO
# overlapping resolved id is a genuine by-name-WITHOUT-inline-id citation (e.g.
# ``hallintolainkäyttölaissa säädetään``): it is OUT of the inline-(id) construction
# family the projection serves and is KEPT as an honest miss/frontier item.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _FullOracleContext:
    """Per-statute prepared full-extractor oracle (built once per statute).

    ``by_segment`` maps a citation segment's exact text to the set of
    cross-statute reference keys the full production extractor binds within that
    segment (after redundant statute-only collapse). Segment texts are the keys
    because the census engine threads only the unit text to the oracle plug-point.
    """

    by_segment: dict[str, set[str]]


def _locate_surface_char_span(
    body: str, surface: str, cursor_by_surface: dict[str, int]
) -> tuple[int, int] | None:
    """Locate ``surface`` in ``body`` (char space) left-to-right; fail-loud by absence.

    Repeated identical surfaces map to successive occurrences via a per-surface
    cursor. Returns ``(char_start, char_end)`` or ``None`` when the surface does
    not round-trip into the decoded body text (e.g. whitespace-normalized by the
    recognizer) — never a fabricated offset.
    """
    if not surface:
        return None
    start = body.find(surface, cursor_by_surface.get(surface, 0))
    if start < 0:
        return None
    cursor_by_surface[surface] = start + 1
    return start, start + len(surface)


def build_full_extractor_oracle(statute_id: str, body: str) -> _FullOracleContext:
    """Build the whole-statute full-extractor citation oracle, bucketed to segments.

    Runs the FULL production extractor over the statute XML, keeps the
    cross-statute citations, re-locates each in the decoded body text by surface,
    buckets to citation segments by char-span overlap, collapses redundant
    statute-only keys, and keys by ``ProvisionRef.serialized()``.

    Requires the canonical corpus to read the statute XML (the extractor's
    ``<ref>``/by-name lanes need the markup; the segment text alone cannot
    reproduce them). Fails closed to an empty oracle for any statute whose XML is
    unavailable or unparseable — the census then treats every segment as
    oracle-empty for that statute (an honest under-count, never a fabrication).
    """
    from farchive import Farchive

    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    try:
        store = TransparentCorpusStore(Farchive(_archive_path()))
        xb = store.read_source(statute_id) or store.read_amendment(statute_id)
    except Exception:
        xb = None
    if not xb:
        return _FullOracleContext(by_segment={})
    return _bucket_full_extractor_oracle(statute_id, xb, body)


def _bucket_full_extractor_oracle(
    statute_id: str, xml_bytes: bytes, body: str
) -> _FullOracleContext:
    """Pure full-extractor oracle bucketing for explicit ``(xml_bytes, body)``.

    Split out from :func:`build_full_extractor_oracle` so the extraction +
    span-bucketing + redundant statute-only collapse can be exercised with
    synthetic XML (no corpus). See the module-level oracle docstring for the design.
    """
    from lawvm.core.reference_mention import CiteKind
    from lawvm.finland.references.ref_mention_extractor import (
        extract_all_reference_mentions,
    )

    by_segment: dict[str, set[str]] = {}

    try:
        result = extract_all_reference_mentions(xml_bytes, statute_id)
    except Exception:
        return _FullOracleContext(by_segment=by_segment)

    # Segment the body the SAME way the census selector does, so the segment text
    # keys line up exactly with the units the engine will hand the oracle.
    from lawvm.finland.legal_surface.clause_segment import build_clause_index

    index = build_clause_index(statute_id, body)
    segments: list[tuple[int, int, str]] = []
    for sent in index.sentences:
        seg_text = body[sent.char_start : sent.char_end]
        segments.append((sent.char_start, sent.char_end, seg_text))

    # Collect the in-family (cross-statute) mentions and locate each in body
    # char-space. A mention's ``source_span.byte_offset`` is a byte offset into the
    # raw xml_bytes; several mentions sharing one citation anchor (coordinated
    # ``8 ja 27 §`` -> two targets) share that byte offset, so we locate ONE char
    # span PER DISTINCT byte offset (ascending, left-to-right) and reuse it for
    # every mention at that offset — this matches the extractor's per-anchor span
    # caching and stops a per-mention cursor from skipping past shared surfaces.
    @dataclass(frozen=True)
    class _Cross:
        surface: str
        byte_offset: int | None
        key: str

    crosses: list[_Cross] = []
    for m in result.mentions:
        if m.cite_kind != CiteKind.CROSS_STATUTE:
            continue
        tgt = m.target_provision_ref
        if tgt is None:
            continue
        key = tgt.serialized()
        if not key:
            continue
        crosses.append(
            _Cross(
                surface=m.surface_text or "",
                byte_offset=m.source_span.byte_offset if m.source_span else None,
                key=_canonicalize_statute_key(key),
            )
        )

    # Char location, byte-offset-ordered. Per (surface) char cursor advances once
    # per DISTINCT byte offset for that surface. Mentions with no byte offset fall
    # back to a from-zero find on the surface (fail-loud by absence — an
    # unlocatable surface is simply not bucketed, an honest under-count).
    cursor_by_surface: dict[str, int] = {}
    span_by_offset: dict[tuple[str, int], tuple[int, int] | None] = {}
    # entry: (char_start, char_end, key)
    located: list[tuple[int, int, str]] = []
    for c in sorted(
        crosses, key=lambda c: (c.byte_offset if c.byte_offset is not None else 1 << 62)
    ):
        if not c.surface:
            continue
        if c.byte_offset is not None:
            ck = (c.surface, c.byte_offset)
            if ck not in span_by_offset:
                span_by_offset[ck] = _locate_surface_char_span(
                    body, c.surface, cursor_by_surface
                )
            cspan = span_by_offset[ck]
        else:
            pos = body.find(c.surface)
            cspan = (pos, pos + len(c.surface)) if pos >= 0 else None
        if cspan is None:
            continue
        located.append((cspan[0], cspan[1], c.key))

    # Same-site by-name dedup: the production by-name HEAD lane emits an UNRESOLVED
    # ``fi-name:NAME`` key for a citation EVEN WHEN the resolved-id lane also binds
    # the SAME ``name (id)`` site to ``NUMBER/YEAR`` (e.g. ``Valmiuslaissa
    # (1552/2011)`` -> both ``1552/2011`` and ``fi-name:valmiuslaki``). The two
    # describe ONE citation; the projection keys it only by the resolved id. A
    # by-name located span whose char range OVERLAPS a resolved-id located span is
    # therefore the SAME citation site and its ``fi-name:`` key is dropped (the
    # resolved key wins). A by-name span with NO overlapping resolved id is a
    # genuine by-name-WITHOUT-inline-id citation (out of the inline-(id)
    # construction family) and is KEPT — a real miss/frontier reported honestly.
    resolved_spans = [
        (s, e) for (s, e, k) in located if not k.startswith("fi-name:")
    ]

    def _by_name_is_same_site(s: int, e: int) -> bool:
        return any(s < re_ and rs < e for (rs, re_) in resolved_spans)

    for seg_start, seg_end, seg_text in segments:
        # The inline ``(NUMBER/YEAR)`` parens the source author actually wrote in
        # THIS segment. They disambiguate the orientation of a resolved-id key whose
        # two components are BOTH year-plausible (``1774/1995`` — the statute number
        # 1774 is itself in the enactment-year range), which the number-only
        # :func:`_canonicalize_statute_key` cannot resolve. See its segment-aware
        # variant below.
        seg_id_parens = _segment_id_parens(seg_text)
        keys: set[str] = set()
        for c_start, c_end, key in located:
            # overlap test (half-open intervals)
            if not (c_start < seg_end and seg_start < c_end):
                continue
            if key.startswith("fi-name:") and _by_name_is_same_site(c_start, c_end):
                continue
            keys.add(_canonicalize_statute_key_in_segment(key, seg_id_parens))
        if keys:
            by_segment[seg_text] = _collapse_redundant_statute_only(keys)

    return _FullOracleContext(by_segment=by_segment)


def _canonicalize_statute_key(key: str) -> str:
    """Canonicalize a resolved statute-id key head to ``NUMBER/YEAR``.

    The production ``<ref>``-element lane keys a citation from the AKN eId/href,
    which encodes the statute as ``YEAR/NUMBER`` (``(1385/2015)`` -> ``2015/1385``)
    and may carry a trailing instance suffix on the number (``(39/1889)`` ->
    ``1889/39-001``). The construction projection keys from the inline surface text,
    which is the canonical Finnish ``NUMBER/YEAR`` (``1385/2015`` / ``39/1889``).
    They are the SAME statute in two conventions. So when a resolved key's HEAD is
    ``A/B`` with ``A`` a plausible enactment year (1700–2100) and ``B`` NOT a
    plausible year, the head is the eId ``YEAR/NUMBER`` form: it is swapped to
    ``NUMBER/YEAR`` (``B/A``), stripping any ``-NNN`` instance suffix off the number;
    any provision tail is preserved. Non-statute keys (``fi-name:``…) and
    already-canonical heads are returned unchanged.
    """
    if key.startswith("fi-name:") or "/" not in key:
        return key
    parts = key.split("/")
    a, b = parts[0], parts[1]
    # strip a trailing eId instance suffix (``39-001`` -> ``39``) for the test/swap
    b_num = b.split("-", 1)[0]
    if not (a.isdigit() and b_num.isdigit()):
        return key

    def _year(x: str) -> bool:
        return 1700 <= int(x) <= 2100

    if _year(a) and not _year(b_num):
        return "/".join([b_num, a, *parts[2:]])
    return key


def _canonicalize_statute_key_in_segment(
    key: str, seg_id_parens: set[str]
) -> str:
    """Canonicalize a resolved-id key to ``NUMBER/YEAR``, segment-paren aware.

    First applies the number-only :func:`_canonicalize_statute_key`, which swaps an
    eId ``YEAR/NUMBER`` head to canonical ``NUMBER/YEAR`` whenever the orientation is
    UNAMBIGUOUS (the year component is year-plausible and the number component is
    not). That rule cannot fire when BOTH components are year-plausible — e.g. the
    ``<ref>``-lane key ``1995/1774`` for ``eläkesäätiölaki (1774/1995)``, whose
    statute number 1774 also lies in the 1700–2100 enactment-year range. For that
    ambiguous case the orientation is resolved from the SEGMENT's own inline parens:
    the ``<ref>`` lane keys ``YEAR/NUMBER``, so if the head's swapped form ``B/A``
    appears as a literal inline ``(B/A)`` paren the author wrote in this segment,
    ``A`` is the year and ``B`` the number — swap to the canonical ``NUMBER/YEAR``
    (``B/A``), preserving any provision tail. This is a SURFACE fact (the paren is
    literally present), never an orientation guess; with no matching inline paren
    the key is left as the number-only rule decided (an honest non-swap, not a
    fabrication).
    """
    canon = _canonicalize_statute_key(key)
    if canon != key or canon.startswith("fi-name:") or "/" not in canon:
        # already swapped (unambiguous) or not a swappable resolved id
        return canon
    parts = canon.split("/")
    a, b = parts[0], parts[1]
    b_num = b.split("-", 1)[0]
    if not (a.isdigit() and b_num.isdigit()):
        return canon

    def _year(x: str) -> bool:
        return 1700 <= int(x) <= 2100

    # Only the ambiguous both-years case remains (the unambiguous one was already
    # swapped by _canonicalize_statute_key above). Disambiguate by the segment's
    # inline paren: the author-written NUMBER/YEAR form is ``(B/A)``.
    if _year(a) and _year(b_num):
        # ``seg_id_parens`` carries each inline paren's ``NUMBER/YEAR`` content
        # (whitespace-normalized). The author-written NUMBER/YEAR form is ``B/A``.
        if f"{b_num}/{a}" in seg_id_parens:
            return "/".join([b_num, a, *parts[2:]])
    return canon


def _collapse_redundant_statute_only(keys: set[str]) -> set[str]:
    """Drop a bare statute-only oracle key when a same-statute precise key exists.

    For ONE citation the production extractor commonly emits BOTH a coarse
    statute-only mention (``527/2014``) AND the section/chapter-precise mentions
    (``527/2014/142``). The construction projection emits only the precise key for
    that citation, never the redundant coarse one. Keeping both in the oracle would
    score the redundant ``527/2014`` a phantom ``miss`` against a projection that
    found the SAME citation more precisely. So a bare statute-only key ``S`` is
    dropped IFF the same key set contains a strictly-more-precise key ``S/…`` for
    the same statute — they describe one citation. A statute-only key with NO
    precise sibling is a genuine statute-level citation and is KEPT (a real miss if
    the projection lacks it).
    """
    def _is_resolved_statute_only(k: str) -> bool:
        # ``NUMBER/YEAR`` resolved id, no provision tail. Excludes ``fi-name:`` and
        # other prefixed (EU/treaty) keys — only resolved statute ids collapse.
        parts = k.split("/")
        return (
            len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit()
        )

    statute_only = {k for k in keys if _is_resolved_statute_only(k)}
    # statute-id prefix of every precise resolved key (``S/Y/…`` -> ``S/Y``)
    precise_prefixes = {
        parts[0] + "/" + parts[1]
        for k in keys
        if len((parts := k.split("/"))) >= 3 and parts[0].isdigit() and parts[1].isdigit()
    }
    redundant = statute_only & precise_prefixes
    return keys - redundant


def full_oracle_reference_keys(unit_text: str, ctx: object) -> set[str]:
    """Oracle plug-point: the full-extractor cross-statute key set for a segment.

    Looks the unit's segment text up in the per-statute :class:`_FullOracleContext`
    the engine prepared. Returns the empty set for a segment the full extractor
    bound no cross-statute citation in (or when the context is absent).
    """
    if not isinstance(ctx, _FullOracleContext):
        return set()
    return set(ctx.by_segment.get(unit_text, set()))
