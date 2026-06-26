"""Surface recognizer for temporal / applicability conditions in body prose.

This is the **H3 temporal/applicability lens** of the Legal Surface Algebra: a
deterministic SURFACE recognizer over Finnish statute body text. It recognises
the temporal-condition cues that appear *inline in prose* (commencement,
fixed-date bounds, durations reckoned from commencement, open-ended validity,
and until-an-event bounds) and emits typed :class:`TemporalExpr` surface facts
with a source span and an explicit ``status``.

It is the surface-IR ``TemporalExpr[]`` node from FI_PARSE_OVERLAY_IR_MODEL.md
(a domain surface parse peer over the source tape — "commencement / expiry /
event bounds"). Like :mod:`lawvm.finland.references.treaty` and
:mod:`lawvm.finland.references.vague`, it is a closed-list, substring-guarded,
frozen-dataclass, fail-loud recognizer.

SCOPE BOUNDARY (non-negotiable, FI_PARSE_OVERLAY_IR_MODEL.md §"Surface ≠ replay
authority"): this is a SURFACE lens. It produces typed surface facts + source
spans + residuals. It does NOT authorize replay and it is NOT the
expiry/commencement *engine*. It must not touch or import the replay machinery
(``legal_pit``, fixed-term expiry apply path, ``core.temporal`` activation
rules). A recognised ``COMMENCEMENT`` cue is the statement "the text says
something commences here", NOT a state change. A ``FIXED_DATE`` bound is a
parsed surface date, NOT an authority that the statute is in force on that date.

FAIL-LOUD (AGENTS.md §1.1, tag-don't-guess): a temporal cue that cannot be typed
to a determinate ``bound`` is emitted as a residual — ``status`` one of
``event_bound`` / ``open`` / ``unsupported``, with ``bound=None``. A date is
NEVER guessed. A cue is never silently dropped.

§1.11 hot-path regex discipline: every pattern is compiled at module scope with
bounded quantifiers; the public entry point does a cheap substring pre-guard
before running any matcher. The Finnish month-name table is a small closed list
(``tammikuuta`` .. ``joulukuuta``) enumerated explicitly.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import List, Optional

from lawvm.core.reference_mention import SourceSpan

# Q3 node-identity reconciliation: ONE canonical Finnish month table for the
# temporal family. This surface lens imports it rather than keeping a rival
# copy, so the temporal recognizers cannot drift on the month vocabulary.
from lawvm.finland.fi_dates import FI_MONTH_PARTITIVE_TO_NUMBER as _MONTHS_PARTITIVE

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class TemporalKind(Enum):
    """What temporal surface construct was recognised."""

    FIXED_DATE = "fixed_date"
    """A determinate calendar date in the prose (``1.1.2027`` or the Finnish
    long form ``1 päivänä tammikuuta 2027``). Parses to an ISO date in
    ``bound``."""

    DURATION_FROM_COMMENCEMENT = "duration_from_commencement"
    """A bound reckoned relative to commencement / a start point
    (``... alkaen``, ``voimaantulosta lukien``). The anchor is structural, not
    a calendar date, so ``bound`` stays None (residual ``unsupported``)."""

    EVENT_BOUND = "event_bound"
    """An until-an-event bound (``kunnes ...``). The terminating event is not
    structurally resolved here, so ``bound`` is None (residual
    ``event_bound``)."""

    COMMENCEMENT = "commencement"
    """A commencement cue (``tulee voimaan`` / ``voimaantulo``). It marks that
    the text speaks of entry into force; any accompanying date is recognised
    separately as its own ``FIXED_DATE`` expr."""

    VALIDITY_OPEN = "validity_open"
    """An open-ended validity statement (``on voimassa`` with no determinate
    end). No end bound exists, so ``status`` is ``open`` and ``bound`` is
    None."""

    FIXED_TERM_EXPIRY = "fixed_term_expiry"
    """A determinate validity end stated inline: ``on voimassa <date> saakka/asti``
    ("is in force until <date>"). Unlike VALIDITY_OPEN this carries a determinate
    end bound, so ``status`` is ``resolved`` and ``bound`` is the parsed end date.
    The ``siihen saakka, kunnes …`` shape (no date, terminated by an event) stays
    a VALIDITY_OPEN / EVENT_BOUND residual — it is genuinely open."""


class TemporalStatus(Enum):
    """How determinately the temporal cue was typed to a bound.

    ``resolved`` is the only non-residual status: a concrete bound was parsed.
    Everything else is a typed residual — an owned failure to pin a determinate
    bound, never a silent drop and never a guessed date.
    """

    RESOLVED = "resolved"
    """A concrete bound (a calendar date) was parsed into ``bound``."""

    EVENT_BOUND = "event_bound"
    """Bounded by an event whose date is not structurally known (``kunnes``)."""

    OPEN = "open"
    """Open-ended; no determinate end exists (``on voimassa``)."""

    UNSUPPORTED = "unsupported"
    """A temporal cue was recognised but cannot be typed to a determinate bound
    (e.g. a duration whose anchor is structural, not a calendar date). Owned
    residual — the cue is reported, never dropped, never guessed."""


# ---------------------------------------------------------------------------
# The typed surface node
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TemporalExpr:
    """A typed temporal surface fact recognised in body prose.

    Attributes:
        kind:         The recognised :class:`TemporalKind`.
        surface_text: The exact matched substring (verbatim from the source).
        source_span:  Provenance back to the byte range in the source text.
        bound:        The parsed calendar date, present ONLY when ``temporal_status``
                      is ``resolved``; ``None`` for every residual status.
        temporal_status: The :class:`TemporalStatus` — ``resolved`` iff ``bound``
                      is not None.
        rule_id:      Stable id of the recognizer rule that fired (for audit /
                      witness attribution).
    """

    kind: TemporalKind
    surface_text: str
    source_span: SourceSpan
    bound: Optional[date]
    temporal_status: TemporalStatus
    rule_id: str

    def __post_init__(self) -> None:
        # The fail-loud contract: a parsed bound may exist ONLY on a RESOLVED
        # expr (no guessed dates on residuals), and a RESOLVED FIXED_DATE MUST
        # carry one (a recognised date that didn't parse is a residual, not a
        # resolved-with-no-date). A RESOLVED non-date cue (COMMENCEMENT) is
        # determinate yet legitimately dateless, so bound=None is allowed there.
        if self.bound is not None and self.temporal_status is not TemporalStatus.RESOLVED:
            raise ValueError(
                "residual TemporalExpr must not carry a bound (no guessed dates)"
            )
        if (
            self.kind is TemporalKind.FIXED_DATE
            and self.temporal_status is TemporalStatus.RESOLVED
            and self.bound is None
        ):
            raise ValueError("RESOLVED FIXED_DATE must carry a parsed bound")


# The canonical Finnish month-name table (``_MONTHS_PARTITIVE``) is imported
# above from ``temporal_lowering`` — partitive forms as they appear in long-form
# dates ("1 päivänä tammikuuta 2027"). The long-form patterns and parse below
# read it directly, so the lens and the production extractor share one table.

# ---------------------------------------------------------------------------
# Cheap substring pre-guards (§1.11) — if none appears, no matcher can fire.
# ---------------------------------------------------------------------------

_GUARDS: tuple[str, ...] = (
    ".",  # numeric date separator (1.1.2027)
    "päivänä",  # long-form date marker
    "voima",  # voimaan / voimassa / voimaantulo
    "alkaen",  # duration anchor
    "lukien",  # duration anchor
    "kunnes",  # event bound
)

# ---------------------------------------------------------------------------
# Compiled patterns (module scope — §1.11; all bounded quantifiers)
# ---------------------------------------------------------------------------

# Numeric Finnish date: d.m.yyyy with 1-2 digit day/month, exactly 4-digit year.
# A trailing dot after the year (sentence punctuation) is not captured.
_FIXED_NUMERIC_RE = re.compile(
    r"\b(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})\b"
)

# Long-form Finnish date: "1 päivänä tammikuuta 2027". Day 1-2 digits, month
# from the closed partitive table, year exactly 4 digits.
_MONTH_ALT = "|".join(sorted(_MONTHS_PARTITIVE, key=len, reverse=True))
_FIXED_LONG_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+päivänä\s+(?P<month>" + _MONTH_ALT + r")\s+(?P<year>\d{4})\b"
)

# Commencement cue: "tulee voimaan" (enters into force) or the noun
# "voimaantulo" (entry into force). Bounded — fixed phrase set.
_COMMENCEMENT_RE = re.compile(
    r"tulee\s+voimaan|voimaantulo[a-zäö]{0,12}", re.IGNORECASE
)

# Duration-from-commencement anchors: "... alkaen", "voimaantulosta lukien",
# "voimaantulosta alkaen". The bound is a structural anchor, not a date.
#
# The "<noun>sta alkaen" arm is restricted to a CLOSED set of temporal-noun
# stems. The old open "[a-zäö0-9]+sta alkaen" arm over-fired on any elative noun
# ("sopimuksesta alkaen", "josta alkaen", "saamisesta alkaen") that is not a
# temporal anchor. The closed stem set covers the genuine reckoning-point nouns
# seen in commencement prose: commencement (voimaantulo…), day/date markers
# (päivä-, ajankohta-), and period starts (vuosi-, jakso-, vaihe-, alku-).
_TEMPORAL_ANCHOR_STEMS: tuple[str, ...] = (
    "voimaantulo",  # voimaantulosta / voimaantulopäivästä / voimaantuloajankohdasta
    "päivä",  # päivästä / maksupäivästä / voimaantulopäivästä
    "ajankohda",  # ajankohdasta / voimaantuloajankohdasta
    "ajankohta",
    "vuode",  # verovuodesta / kalenterivuodesta (vuosi -> vuode-)
    "vuosi",
    "jakso",  # maksujaksosta
    "vaihe",  # vaiheesta
    "alusta",  # alusta (period start)
    "hetke",  # toteamishetkestä / hetkestä
)

_ANCHOR_ALT = "|".join(
    sorted(_TEMPORAL_ANCHOR_STEMS, key=len, reverse=True)
)

_DURATION_RE = re.compile(
    r"voimaantulosta\s+lukien|voimaantulosta\s+alkaen|"
    r"voimaantulosta|"
    r"[a-zäö0-9]*(?:" + _ANCHOR_ALT + r")[a-zäö0-9]*st[aä]\s+alkaen",
    re.IGNORECASE,
)

# Open-ended validity: "on voimassa" not immediately continued by a determinate
# end ("toistaiseksi" = until further notice keeps it open; an explicit end date
# is recognised separately as its own FIXED_DATE expr).
_VALIDITY_OPEN_RE = re.compile(r"on\s+voimassa(?:\s+toistaiseksi)?", re.IGNORECASE)

# Closed end-bound cue set: a determinate validity end is "on voimassa <date>
# saakka/asti" ("in force until <date>"). The date may be the numeric form
# (1.1.2027) or the long form (1 päivään joulukuuta 2027 / 31 päivänä …). The
# day-of-month case in this construction is illative ("päivään"), distinct from
# the partitive ("päivänä") of a plain FIXED_DATE, so a dedicated pattern handles
# both. ``saakka``/``asti`` are the closed terminal cues. A date-less "siihen
# saakka, kunnes …" does NOT match (no date group) and stays genuinely open.
_END_CUES: tuple[str, ...] = ("saakka", "asti")

_FIXED_TERM_NUMERIC_END_RE = re.compile(
    r"on\s+voimassa\b[^.;\n]{0,40}?"
    r"(?P<day>\d{1,2})\.(?P<month>\d{1,2})\.(?P<year>\d{4})"
    r"\s+(?:saakka|asti)\b",
    re.IGNORECASE,
)

_FIXED_TERM_LONG_END_RE = re.compile(
    r"on\s+voimassa\b[^.;\n]{0,40}?"
    r"(?P<day>\d{1,2})\s+päivä(?:än|nä)\s+(?P<month>" + _MONTH_ALT + r")"
    r"\s+(?P<year>\d{4})\s+(?:saakka|asti)\b",
    re.IGNORECASE,
)

# Event bound: "kunnes ..." (until <event>). The terminating event is captured
# only as surface text up to clause-ending punctuation; it is NOT resolved.
_EVENT_BOUND_RE = re.compile(r"kunnes\b[^.;\n]*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(text: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(source_file="", byte_offset=start, byte_len=end - start)


def _try_date(year: int, month: int, day: int) -> Optional[date]:
    """Construct a calendar date, returning None on an impossible date.

    A syntactically date-shaped but calendrically impossible value (``32.13``)
    is a residual, not a guess: we return None so the cue is typed UNSUPPORTED
    rather than fabricating a nearby valid date.
    """
    try:
        return date(year, month, day)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def recognize_temporal_exprs(text: str) -> List[TemporalExpr]:
    """Recognise temporal / applicability surface cues in ``text``.

    Returns typed :class:`TemporalExpr` rows in document order (by start
    offset). Each row carries a verbatim ``surface_text``, a ``source_span``,
    and a ``status``:

      - ``FIXED_DATE``   → ``resolved`` with the parsed ISO date in ``bound``
        (or ``unsupported`` if the date is calendrically impossible).
      - ``COMMENCEMENT`` → ``resolved`` (the cue itself is determinate; any
        accompanying date is a separate ``FIXED_DATE`` row).
      - ``DURATION_FROM_COMMENCEMENT`` → ``unsupported`` (anchor is structural,
        not a calendar date — no determinate bound to parse here).
      - ``EVENT_BOUND``  → ``event_bound`` (``bound`` None — event unresolved).
      - ``VALIDITY_OPEN``→ ``open`` (``bound`` None — no determinate end).

    FAIL-LOUD: a recognised cue that cannot be typed to a determinate bound is
    emitted as a residual; a date is never guessed and a cue is never silently
    dropped.
    """
    if not any(g in text for g in _GUARDS):
        return []

    out: List[TemporalExpr] = []

    # --- FIXED_DATE: long form first (so a long-form match isn't shadowed) ---
    long_spans: list[tuple[int, int]] = []
    for m in _FIXED_LONG_RE.finditer(text):
        day = int(m.group("day"))
        month = _MONTHS_PARTITIVE[m.group("month").lower()]
        year = int(m.group("year"))
        d = _try_date(year, month, day)
        long_spans.append((m.start(), m.end()))
        out.append(
            TemporalExpr(
                kind=TemporalKind.FIXED_DATE,
                surface_text=m.group(0),
                source_span=_span(text, m.start(), m.end()),
                bound=d,
                temporal_status=(
                    TemporalStatus.RESOLVED if d is not None else TemporalStatus.UNSUPPORTED
                ),
                rule_id="fixed_date.long_form",
            )
        )

    # --- FIXED_DATE: numeric form (skip the trailing "2027" inside a long
    #     form already matched, so we don't double-count the year). ---
    for m in _FIXED_NUMERIC_RE.finditer(text):
        if any(s <= m.start() < e for (s, e) in long_spans):
            continue
        day = int(m.group("day"))
        month = int(m.group("month"))
        year = int(m.group("year"))
        d = _try_date(year, month, day)
        out.append(
            TemporalExpr(
                kind=TemporalKind.FIXED_DATE,
                surface_text=m.group(0),
                source_span=_span(text, m.start(), m.end()),
                bound=d,
                temporal_status=(
                    TemporalStatus.RESOLVED if d is not None else TemporalStatus.UNSUPPORTED
                ),
                rule_id="fixed_date.numeric",
            )
        )

    # --- COMMENCEMENT ---
    for m in _COMMENCEMENT_RE.finditer(text):
        out.append(
            TemporalExpr(
                kind=TemporalKind.COMMENCEMENT,
                surface_text=m.group(0),
                source_span=_span(text, m.start(), m.end()),
                bound=None,
                temporal_status=TemporalStatus.RESOLVED,
                rule_id="commencement.cue",
            )
        )

    # --- DURATION_FROM_COMMENCEMENT (residual: structural anchor, no date) ---
    for m in _DURATION_RE.finditer(text):
        out.append(
            TemporalExpr(
                kind=TemporalKind.DURATION_FROM_COMMENCEMENT,
                surface_text=m.group(0),
                source_span=_span(text, m.start(), m.end()),
                bound=None,
                temporal_status=TemporalStatus.UNSUPPORTED,
                rule_id="duration_from_commencement.anchor",
            )
        )

    # --- FIXED_TERM_EXPIRY / VALIDITY_OPEN ---
    # "on voimassa <date> saakka/asti" is a DETERMINATE end (fixed-term expiry),
    # not an open-ended validity. Detect the determinate-end shape first and span
    # it from the "on voimassa" cue through the terminal cue; only emit
    # VALIDITY_OPEN for an "on voimassa" with NO such determinate end (the
    # genuinely-open "toistaiseksi" / "siihen saakka, kunnes …" shapes).
    fixed_term_spans: list[tuple[int, int]] = []
    for end_re, rule in (
        (_FIXED_TERM_LONG_END_RE, "fixed_term_expiry.long_form"),
        (_FIXED_TERM_NUMERIC_END_RE, "fixed_term_expiry.numeric"),
    ):
        for m in end_re.finditer(text):
            # a numeric date inside a long-form span is already owned
            if any(s <= m.start() < e for (s, e) in fixed_term_spans):
                continue
            day = int(m.group("day"))
            month_grp = m.group("month")
            month = (
                int(month_grp)
                if month_grp.isdigit()
                else _MONTHS_PARTITIVE[month_grp.lower()]
            )
            year = int(m.group("year"))
            d = _try_date(year, month, day)
            fixed_term_spans.append((m.start(), m.end()))
            out.append(
                TemporalExpr(
                    kind=TemporalKind.FIXED_TERM_EXPIRY,
                    surface_text=m.group(0),
                    source_span=_span(text, m.start(), m.end()),
                    bound=d,
                    temporal_status=(
                        TemporalStatus.RESOLVED
                        if d is not None
                        else TemporalStatus.UNSUPPORTED
                    ),
                    rule_id=rule,
                )
            )

    # --- VALIDITY_OPEN (residual: open-ended, no determinate end) ---
    for m in _VALIDITY_OPEN_RE.finditer(text):
        # Suppress the false-open: an "on voimassa" that opens a determinate
        # fixed-term-expiry span (handled above) is NOT open.
        if any(s <= m.start() < e for (s, e) in fixed_term_spans):
            continue
        out.append(
            TemporalExpr(
                kind=TemporalKind.VALIDITY_OPEN,
                surface_text=m.group(0),
                source_span=_span(text, m.start(), m.end()),
                bound=None,
                temporal_status=TemporalStatus.OPEN,
                rule_id="validity_open.cue",
            )
        )

    # --- EVENT_BOUND (residual: until-an-event, event unresolved) ---
    for m in _EVENT_BOUND_RE.finditer(text):
        out.append(
            TemporalExpr(
                kind=TemporalKind.EVENT_BOUND,
                surface_text=m.group(0).rstrip(),
                source_span=_span(text, m.start(), len(m.group(0).rstrip()) + m.start()),
                bound=None,
                temporal_status=TemporalStatus.EVENT_BOUND,
                rule_id="event_bound.kunnes",
            )
        )

    out.sort(key=lambda e: e.source_span.byte_offset)
    return out
