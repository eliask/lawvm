"""Temporal / applicability construction parse — the temporal family island.

The next net-new construction-grammar island after the citation-sentence pilot
(:mod:`lawvm.finland.legal_surface.sentence_parse`) and the definition-entry
pilot (:mod:`lawvm.finland.legal_surface.definition_parse`): the **temporal /
applicability family**. A temporal clause is a formulaic Finnish construction
that fixes WHEN a statute is in force, until when, and from when it applies:

  * commencement — ``Tämä laki tulee voimaan N päivänä Kkkuuta YYYY.`` / the
    numeric variant ``… tulee voimaan 1.1.2027.`` / the empty placeholder
    ``… tulee voimaan päivänä kuuta 20 .``;
  * validity / fixed-term — ``Tämä laki on voimassa … saakka`` / ``… on voimassa
    NN päivään Kkkuuta YYYY`` / ``voimassaoloaika …``;
  * application — ``Tätä lakia sovelletaan … alkaen`` / ``… sovelletaan
    ensimmäisen kerran …`` / a ``soveltamissäännös`` / ``siirtymäsäännös``.

Position in the stack
=====================
Same discipline as the two pilots, one family over: a sentence-frame
construction with TOTAL TOKEN OWNERSHIP (every char is a typed construction span
— the temporal-operator cue, the child date/period expression, or an EXPLICIT
residual; the invariant is "no silent drop", NOT "no residue"). It is purely
ADDITIVE and surface-only — it makes NO activation/expiry composition decisions,
authorizes NO replay, and is NOT wired into the production temporal lowering.

The CENSUS compares this projection against the PRODUCTION temporal primitive
(``johtolause.meta_parse.extract_meta_surface_clauses`` for the clause-role
classification + ``temporal_lowering._extract_date_from_text`` /
``_extract_expiry_date_from_text`` for the date), keyed identically. The parse
deliberately MIRRORS the production classifier's cue patterns and REUSES the
production date extractors (it does NOT reimplement date parsing), so where the
grammar matches the oracle the projection is in parity by construction and
genuine divergences surface as census miss / superset.

The construction
================
A temporal parse over a sentence span carries:

  * zero or more **temporal clauses** — each a ``cue`` (the temporal-operator
    surface, e.g. ``tulee voimaan`` / ``on voimassa`` / ``sovelletaan``), a
    ``role`` (closed list: ``commencement`` / ``validity`` / ``application`` /
    ``delegation``), the **date/period expression span** (the production date
    match, when one is recognizable; else ``None`` — an undated / placeholder /
    deferred clause), and the extracted ISO ``date`` (or ``""``);
  * an explicit **residual** span list — every char NOT owned by a clause's cue
    or date span, typed by reason. The no-silent-drop invariant holds because
    the residual is EXPLICIT.

:func:`assert_total_ownership` is the checkable postcondition (the union of the
clause cue spans, the date spans, and the residual spans partitions the sentence
char range exactly).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from lawvm.core.semantic_types import MetaClauseKind

# Reuse the PRODUCTION date extractors + month map; do NOT reimplement date
# parsing. The expiry pattern is the allative/essive ``NN päivä[äa]n/[aä]
# Kkkuuta YYYY`` form; the commencement pattern is the essive long form.
from lawvm.finland.temporal_lowering import (
    _COMMENCEMENT_DATE_PATTERN,
    _EXPIRY_DATE_PATTERN,
    _extract_date_from_text,
    _extract_expiry_date_from_text,
)

# ---------------------------------------------------------------------------
# Parser-lane provenance — mirrors sentence_parse / definition_parse.
# ---------------------------------------------------------------------------
#: The temporal-construction grammar owned the frame (in-scope, no-silent-drop).
TEMPORAL_LANE_CONSTRUCTION_OWNED = "temporal_construction_owned"
#: The frame declined: the span carried a temporal cue the family discriminator
#: keyed on, but NO recognizable temporal clause parsed. Handed back as typed
#: residue, never a guessed parse.
TEMPORAL_LANE_DECLINED = "temporal_construction_declined"

# ---------------------------------------------------------------------------
# Closed-list clause roles. Names the temporal semantics of the clause.
# Mapped 1:1 onto the production MetaClauseKind vocabulary so the projection key
# is directly comparable to the production oracle key.
# ---------------------------------------------------------------------------
ROLE_COMMENCEMENT = "commencement"
ROLE_VALIDITY = "validity"
ROLE_APPLICATION = "application"
ROLE_DELEGATION = "delegation"

#: The production sentence splitter the oracle (``meta_parse``) applies INTERNALLY
#: before classifying. A census unit (a SegmentationGraph sentence) can still
#: contain several production sub-sentences — the substrate's sentence rule and
#: this ``[.!?]`` + capital-letter rule differ (e.g. a long-form date period
#: ``…tammikuuta 2015. Asetus…`` the substrate does not cut). The construction
#: parse MIRRORS the oracle's split so a multi-clause unit produces one temporal
#: clause per sub-sentence, exactly as the oracle emits one meta clause per
#: sub-sentence — keeping the differential parity by construction.
_PROD_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÅ])")

#: Map each clause role to the production ``MetaClauseKind`` it corresponds to,
#: so :func:`temporal_key` produces the same key the oracle does. (The family
#: calls a fixed-term/expiry clause "validity" and an applicability/transition
#: clause "application"; production names them ``EXPIRY`` / ``TRANSITION``.)
_ROLE_TO_META_KIND: dict[str, MetaClauseKind] = {
    ROLE_COMMENCEMENT: MetaClauseKind.COMMENCEMENT,
    ROLE_VALIDITY: MetaClauseKind.EXPIRY,
    ROLE_APPLICATION: MetaClauseKind.TRANSITION,
    ROLE_DELEGATION: MetaClauseKind.DELEGATION,
}

# ---------------------------------------------------------------------------
# Cue recognizers. These MIRROR the production ``meta_parse._META_PATTERNS`` —
# same alternations, same precedence order (transition/application before
# expiry/validity before commencement before delegation) — so the family's role
# assignment matches the oracle's classification by construction. We keep the
# cue patterns separate (not imported) because we need the MATCH SPAN of the cue
# surface for total token ownership, which the production classifier discards.
# ---------------------------------------------------------------------------
_CUE_APPLICATION = re.compile(
    r"soveltamiss[aä][äa]nn[öo]s"
    r"|siirtymäs[aä][äa]nn[öo]s"
    r"|tätä\s+lakia\s+sovelletaan"
    r"|ennen\s+(?:tämän\s+lain|lain)\s+voimaantuloa\s+(?:vireille|käsitelty|myönnetty)",
    re.IGNORECASE,
)
_CUE_VALIDITY = re.compile(
    r"\bon\s+voimassa\b"
    r"|voimassaoloaika",
    re.IGNORECASE,
)
_CUE_COMMENCEMENT = re.compile(
    r"(?:tulee|tuli)\s+voimaan",
    re.IGNORECASE,
)
_CUE_DELEGATION = re.compile(
    r"(?:antaa|voidaan\s+antaa)\s+(?:tarkempia?\s+)?(?:säännöksiä|määräyksiä)",
    re.IGNORECASE,
)

#: Ordered (role, cue-pattern) pairs. Precedence MUST match the production
#: ``_META_PATTERNS`` order: a sentence is classified by the FIRST role whose
#: cue matches (production breaks after the first match per sentence).
_ROLE_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (ROLE_APPLICATION, _CUE_APPLICATION),
    (ROLE_VALIDITY, _CUE_VALIDITY),
    (ROLE_COMMENCEMENT, _CUE_COMMENCEMENT),
    (ROLE_DELEGATION, _CUE_DELEGATION),
)


@dataclass(frozen=True)
class Residual:
    """An explicit unowned span of the sentence (no-silent-drop typed residue)."""

    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True)
class TemporalClause:
    """One temporal/applicability clause the sentence carries.

    Attributes:
        role:        Closed-list clause role (``commencement`` / ``validity`` /
                     ``application`` / ``delegation``).
        cue:         The temporal-operator cue SURFACE (as matched).
        cue_start:   Char offset (sentence-local) where the cue begins.
        cue_end:     One-past the cue.
        date:        Extracted ISO-8601 date when a date/period expression is
                     recognizable (commencement/validity only), else ``""``.
                     Empty for numeric/placeholder/deferred/period clauses the
                     production date extractor does not parse — recorded honestly,
                     never guessed.
        date_start:  Char offset where the date/period expression begins, or
                     ``None`` when no date span is owned.
        date_end:    One-past the date/period expression, or ``None``.
    """

    role: str
    cue: str
    cue_start: int
    cue_end: int
    date: str
    date_start: int | None
    date_end: int | None


@dataclass(frozen=True)
class TemporalParse:
    """A temporal/applicability sentence construction parse (the lite IR).

    Attributes:
        seg_start / seg_end: Sentence char range (sentence-local; the parse runs
                             on ``text`` so ``seg_start == 0``).
        text:                The exact sentence text.
        kind:                ``"temporal"`` when >=1 clause parsed; ``"declined"``
                             when a temporal cue was present but no clause parsed.
        clauses:             The recognized temporal clauses, in source order.
        residuals:           Explicit unowned spans (the no-silent-drop residue).
        parser_lane:         Which lane produced this frame (closed set above).
    """

    seg_start: int
    seg_end: int
    text: str
    kind: str
    clauses: tuple[TemporalClause, ...]
    residuals: tuple[Residual, ...] = field(default_factory=tuple)
    parser_lane: str = TEMPORAL_LANE_CONSTRUCTION_OWNED


def _has_temporal_cue(text: str) -> bool:
    return any(p.search(text) is not None for _role, p in _ROLE_CUES)


def _merge_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(intervals):
        if e <= s:
            continue
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _fill_residuals(n: int, owned: list[tuple[int, int]], reason: str) -> list[Residual]:
    residuals: list[Residual] = []
    cursor = 0
    for s, e in _merge_intervals(owned):
        if s > cursor:
            residuals.append(Residual(cursor, s, reason))
        cursor = max(cursor, e)
    if cursor < n:
        residuals.append(Residual(cursor, n, reason))
    return residuals


def _classify_role(text: str) -> tuple[str, re.Match[str]] | None:
    """Return the (role, cue-match) for ``text``, mirroring production precedence.

    The FIRST role whose cue matches wins (production's per-sentence break-after-
    first-match). Returns ``None`` when no temporal cue is present.
    """
    for role, pattern in _ROLE_CUES:
        m = pattern.search(text)
        if m is not None:
            return role, m
    return None


def _date_span_and_value(text: str, role: str) -> tuple[str, int | None, int | None]:
    """Extract the ISO date + its (sentence-local) span for a clause role.

    REUSES the production date extractors and patterns: commencement/validity use
    the essive/allative ``NN päivä[äa]n[aä] Kkkuuta YYYY`` long form. Returns
    ``(date, start, end)`` where ``date`` is ``""`` and the span is ``(None, None)``
    when no date/period expression is recognizable (numeric ``1.1.2027``,
    placeholder ``päivänä kuuta 20``, deferred/period clauses) — recorded
    honestly, never fabricated.
    """
    if role == ROLE_COMMENCEMENT:
        date = _extract_date_from_text(text)
        if not date:
            return "", None, None
        m = _COMMENCEMENT_DATE_PATTERN.search(text)
        if m is None:
            return date, None, None
        return date, m.start(), m.end()
    if role == ROLE_VALIDITY:
        date = _extract_expiry_date_from_text(text)
        if not date:
            return "", None, None
        m = _EXPIRY_DATE_PATTERN.search(text)
        if m is None:
            return date, None, None
        return date, m.start(), m.end()
    # application / delegation carry no production-extracted single date.
    return "", None, None


def _prod_subsentence_spans(text: str) -> list[tuple[int, int]]:
    """Sub-sentence (start, end) spans of ``text`` per the production splitter.

    Mirrors ``meta_parse``'s internal ``_SENTENCE_SPLIT`` so the construction
    parse classifies the SAME sub-sentences the oracle does. The gaps between
    sub-sentences (the matched whitespace separators) are NOT covered here; they
    fall into the parse's explicit residual, preserving total ownership.
    """
    spans: list[tuple[int, int]] = []
    cursor = 0
    for m in _PROD_SENTENCE_SPLIT.finditer(text):
        spans.append((cursor, m.start()))
        cursor = m.end()
    spans.append((cursor, len(text)))
    return spans


def parse_temporal_sentence(text: str) -> TemporalParse:
    """Parse one temporal/applicability sentence span into a construction frame.

    ``text`` is the EXACT sentence span, in its own local coordinate system.
    Deterministic: split the span into the SAME sub-sentences the production
    oracle classifies (``_prod_subsentence_spans``), and for each sub-sentence
    carrying a temporal cue emit ONE temporal clause — classified by the
    production cue precedence, owning the cue surface span + the recognizable
    date/period span (offsets rebased into the full-span coordinate). Every other
    char is typed explicit residual. Declines (typed residue, never a guessed
    parse) when NO sub-sentence carries a temporal cue (the caller's family
    discriminator guarantees a cue for in-scope spans, so a decline here is the
    out-of-family case).

    A multi-clause unit (e.g. a commencement sentence followed by a fixed-term
    validity sentence the substrate did not split) therefore produces one clause
    per sub-sentence — exactly as the oracle emits one meta clause per
    sub-sentence — keeping the differential parity by construction.
    """
    n = len(text)
    clauses: list[TemporalClause] = []
    owned: list[tuple[int, int]] = []
    for sub_start, sub_end in _prod_subsentence_spans(text):
        sub = text[sub_start:sub_end]
        classified = _classify_role(sub)
        if classified is None:
            continue
        role, cue_match = classified
        date, d_start, d_end = _date_span_and_value(sub, role)
        cue_start = sub_start + cue_match.start()
        cue_end = sub_start + cue_match.end()
        date_start = sub_start + d_start if d_start is not None else None
        date_end = sub_start + d_end if d_end is not None else None
        clauses.append(
            TemporalClause(
                role=role,
                cue=cue_match.group(0),
                cue_start=cue_start,
                cue_end=cue_end,
                date=date,
                date_start=date_start,
                date_end=date_end,
            )
        )
        owned.append((cue_start, cue_end))
        if date_start is not None and date_end is not None:
            owned.append((date_start, date_end))

    if not clauses:
        return TemporalParse(
            seg_start=0,
            seg_end=n,
            text=text,
            kind="declined",
            clauses=(),
            residuals=(Residual(0, n, "not_temporal_bearing"),),
            parser_lane=TEMPORAL_LANE_DECLINED,
        )

    residuals = _fill_residuals(n, owned, "benign_uninterpreted_prose")
    return TemporalParse(
        seg_start=0,
        seg_end=n,
        text=text,
        kind="temporal",
        clauses=tuple(clauses),
        residuals=tuple(residuals),
        parser_lane=TEMPORAL_LANE_CONSTRUCTION_OWNED,
    )


def assert_total_ownership(tp: TemporalParse) -> None:
    """Checkable postcondition: the frame's spans partition ``[seg_start, seg_end)``.

    The union of clause cue spans, clause date spans, and the explicit residual
    spans must cover every char of the sentence with NO gap and NO silent drop.
    Raises ``AssertionError`` on violation.
    """
    n = tp.seg_end - tp.seg_start
    covered = [False] * n
    spans: list[tuple[int, int]] = []
    for c in tp.clauses:
        spans.append((c.cue_start, c.cue_end))
        if c.date_start is not None and c.date_end is not None:
            spans.append((c.date_start, c.date_end))
    spans.extend((r.char_start, r.char_end) for r in tp.residuals)
    for s, e in spans:
        for i in range(max(0, s), min(n, e)):
            covered[i] = True
    missing = [i for i, c in enumerate(covered) if not c]
    if missing:
        raise AssertionError(
            f"total-ownership violation: {len(missing)} unowned chars in sentence "
            f"(first gap at {missing[0]}); SILENT DROP. text={tp.text!r}"
        )


# ---------------------------------------------------------------------------
# Projection: TemporalParse -> [production temporal key]
# ---------------------------------------------------------------------------


def temporal_key(role: str, date: str) -> str:
    """Canonical census key for one temporal clause.

    Keyed on the load-bearing IDENTITY the production temporal primitive emits:
    the production ``MetaClauseKind`` value (commencement / expiry / transition /
    delegation) and the extracted ISO date (empty when none). This is the SAME
    identity :mod:`temporal_census` derives from the oracle's
    ``extract_meta_surface_clauses`` + date extractors, so the projected set is
    directly comparable to the production oracle for the same span.
    """
    meta_kind = _ROLE_TO_META_KIND[role]
    return f"{meta_kind.value}:{date}"


def projection_temporal_keys(tp: TemporalParse) -> set[str]:
    """The projected temporal set as canonical census keys."""
    return {temporal_key(c.role, c.date) for c in tp.clauses}
