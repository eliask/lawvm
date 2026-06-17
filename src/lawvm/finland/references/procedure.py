"""Surface recognizer for procedural process objects (the H5 procedure lens).

This module implements the **H5 "procedure frame" surface lens** of the Legal
Surface Algebra (a sibling of the H4 actor/modal lens in
:mod:`lawvm.finland.references.actor_modal` and the H3 temporal lens in
:mod:`lawvm.finland.references.temporal`). It scans Finnish statutory prose for
the PROCESS objects of administrative/judicial procedure — application,
decision, notice, statement, appeal, hearing, deadline, report — and records
each as a TYPED SURFACE FACT.

    <PROCESS-NOUN> [<ACTOR who must do it>] [<DEADLINE cue>]

e.g. "hakijan on toimitettava **hakemus**" → a HAKEMUS frame whose actor span
covers "hakijan"; "**päätös** on tehtävä **30 päivän kuluessa**" → a PAATOS
frame whose deadline span covers "30 päivän kuluessa" (and the deadline cue
itself is recognised as its own MAARAAIKA frame).

CRITICAL SAFETY BOUNDARY (non-negotiable, mirrors actor_modal.py / temporal.py):
==============================================================================
This layer records SURFACE FACTS ONLY. It NEVER emits a legal conclusion — no
"valid", "void", "enforceable", "binding", no "duty", "obligation", "right",
"power". The presence of "hakemus" is the surface fact ``process_kind=HAKEMUS``
and NOT "an application has been validly made". Legal interpretation begins in a
LATER consuming layer; this recognizer stops at

    typed surface fact + source span (+ a typed Residual for a process-shaped
    token it sees but cannot type to the closed kind set).

It is consequently STANDALONE: it does not edit or depend on
``ref_mention_extractor.py`` and is not wired into any graph. Assembler
integration comes later. The actor vocabulary, when used, is sourced READ-ONLY
from :data:`lawvm.finland.canonical_actor_registry.REGISTRY` plus a small CLOSED
list of generic legal role-actors.

Closed-list discipline (mirrors vague.py §1.11 / actor_modal.py):
  - The process-noun vocabulary is a CLOSED, audited mapping of inflection
    STEMS to a :class:`ProcessKind` enum value. A token outside it never fires.
  - Matching uses bounded, module-scope compiled patterns with cheap substring
    pre-guards.
  - A process-SHAPED token (a member of an audited "looks procedural" family)
    that cannot be typed to the closed kind set is emitted as a typed
    :class:`ProcedureResidual` — self-evidencing (embeds the offending text),
    never a silent drop, never a guess.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Tuple

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import (
    expand_role_actor_phrases,
)

# ---------------------------------------------------------------------------
# Closed process-kind enum (NORMATIVE)
# ---------------------------------------------------------------------------


class ProcessKind(Enum):
    """The closed vocabulary of procedural process objects.

    Each member is a SURFACE process noun, not a legal status. ``HAKEMUS`` is
    "the text speaks of an application", NOT "a valid application exists".
    """

    HAKEMUS = "hakemus"
    """Application / request (hakemus)."""

    PAATOS = "paatos"
    """Decision (päätös)."""

    ILMOITUS = "ilmoitus"
    """Notice / declaration / notification (ilmoitus)."""

    LAUSUNTO = "lausunto"
    """Statement / opinion (lausunto)."""

    VALITUS = "valitus"
    """Appeal (valitus)."""

    KUULEMINEN = "kuuleminen"
    """Hearing (kuuleminen)."""

    MAARAAIKA = "maaraaika"
    """Deadline / time-limit (määräaika)."""

    KERTOMUS = "kertomus"
    """Report (kertomus / raportti)."""


# ---------------------------------------------------------------------------
# Closed process-noun stem table (NORMATIVE)
# ---------------------------------------------------------------------------
#
# Each entry maps an inflection STEM (lowercase, as it appears at the front of
# an inflected form) to its ProcessKind. Finnish inflection drops or mutates the
# final stem vowel/consonant, so we match the invariant prefix and then allow a
# bounded suffix of Finnish word characters. Stems are ordered longest-first at
# compile time so a more specific stem wins over a shorter overlapping one.
#
# These are SURFACE stems, not legal categories.
_PROCESS_STEMS: tuple[tuple[str, ProcessKind], ...] = (
    ("hakemu", ProcessKind.HAKEMUS),       # hakemus, hakemuksen, hakemusta ...
    ("päätö", ProcessKind.PAATOS),         # päätös, päätöksen, päätöstä ...
    ("ilmoitu", ProcessKind.ILMOITUS),     # ilmoitus, ilmoituksen ...
    ("lausunno", ProcessKind.LAUSUNTO),    # lausunnon, lausunnossa ...
    ("lausunto", ProcessKind.LAUSUNTO),    # lausunto, lausuntoa ...
    ("valitu", ProcessKind.VALITUS),       # valitus, valituksen ...
    ("kuulemis", ProcessKind.KUULEMINEN),  # kuulemis-, kuulemista ...
    ("kuuleminen", ProcessKind.KUULEMINEN),  # kuuleminen
    ("määräaja", ProcessKind.MAARAAIKA),   # määräajan, määräajassa ...
    ("määräaika", ProcessKind.MAARAAIKA),  # määräaika, määräaikaa ...
    ("kertomu", ProcessKind.KERTOMUS),     # kertomus, kertomuksen ...
    ("raporti", ProcessKind.KERTOMUS),     # raportin, raportissa ...
    ("raportti", ProcessKind.KERTOMUS),    # raportti, raporttia ...
    ("raportu", ProcessKind.KERTOMUS),     # raportus-shaped (rare) — keep typed
)

#: Cheap substring pre-guards. If none of these appears, no process noun can
#: match. Each is the longest stable ASCII-or-Finnish prefix of a stem family.
_PROCESS_GUARDS: tuple[str, ...] = (
    "hakemu",
    "päätö",
    "ilmoitu",
    "lausun",
    "valitu",
    "kuulemi",
    "määräa",
    "kertomu",
    "raport",
)

# ---------------------------------------------------------------------------
# Process-SHAPED family (for fail-loud residuals).
# ---------------------------------------------------------------------------
#
# A CLOSED set of prefixes that LOOK procedural (the morphology of an
# administrative/judicial act noun) but are NOT in the typed stem table. A token
# starting with one of these and not matched by a typed stem is a process-shaped
# token we cannot type → a typed residual (never a guess). This keeps the
# fail-loud boundary explicit: only audited "looks procedural" families trip a
# residual; ordinary prose does not.
_PROCESS_SHAPED_PREFIXES: tuple[str, ...] = (
    "anomu",      # anomus (an application-like noun NOT in the closed set)
    "vaatimu",    # vaatimus (claim/demand — procedural-shaped, untyped)
    "selvity",    # selvitys (account/clarification — untyped)
    "todistu",    # todistus (certificate — untyped)
    "muistutu",   # muistutus (objection/reminder — untyped)
    "esity",      # esitys (proposal — untyped)
)

# ---------------------------------------------------------------------------
# Closed generic role-actor list (NORMATIVE) — for actor-span detection.
# ---------------------------------------------------------------------------
#
# The closed generic role/class actors NOT carried by the institutional registry
# are sourced from the shared :mod:`lawvm.finland.references.role_actors` module
# (imported above as ``_ROLE_ACTORS``), in the nominative and the common
# genitive forms that head an actor noun phrase that "must do" a procedural act
# ("hakijan on toimitettava ...").

# ---------------------------------------------------------------------------
# Deadline cue family (NORMATIVE, closed) — for deadline-span detection.
# ---------------------------------------------------------------------------
#
# Surface cues marking a time-limit attached to a procedural act. These detect
# the SPAN of a deadline phrase; they do not parse a date (the H3 temporal lens
# owns date parsing). Matching is bounded.
#
# Pattern shapes:
#   "<N> päivän kuluessa"  /  "<N> päivän kuluttua"   (within N days)
#   "<N> viikon/kuukauden/vuoden kuluessa/kuluttua"
#   "viimeistään ..."      (at the latest ...)
#   "määräajassa" / "määräaikaan mennessä"            (within the time-limit)
_DEADLINE_RE = re.compile(
    r"\d{1,4}\s+(?:päivän|viikon|kuukauden|vuoden)\s+(?:kuluessa|kuluttua)"
    r"|viimeistään\b[^.;:\n]{0,80}"
    r"|määräaja(?:ssa|n\s+kuluessa)"
    r"|määräaikaan\s+mennessä",
    re.IGNORECASE,
)

#: Cheap substring guards for the deadline detector.
_DEADLINE_GUARDS: tuple[str, ...] = (
    "kuluessa",
    "kuluttua",
    "viimeistään",
    "määräaja",
    "mennessä",
)


# ---------------------------------------------------------------------------
# Build the compiled actor alternation (READ-ONLY registry + closed role list).
# ---------------------------------------------------------------------------


def _build_actor_phrases() -> Tuple[str, ...]:
    phrases = set(REGISTRY.all_phrases_longest_first())
    phrases.update(expand_role_actor_phrases(_ROLE_ACTORS))
    return tuple(sorted(phrases, key=len, reverse=True))


_ACTOR_PHRASES_LONGEST_FIRST: Tuple[str, ...] = _build_actor_phrases()
_actor_alternation = "|".join(
    re.escape(phrase) for phrase in _ACTOR_PHRASES_LONGEST_FIRST
)
_ACTOR_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _actor_alternation + r")(?![\wäöåÄÖÅ])"
)

# ---------------------------------------------------------------------------
# Build the compiled process-noun matcher.
# ---------------------------------------------------------------------------
#
# Order stems longest-first so the more specific stem wins. After the stem we
# allow a bounded run of Finnish word characters (the inflectional tail). A
# leading boundary stops a stem matching inside a longer unrelated word.
_PROCESS_STEMS_SORTED: tuple[tuple[str, ProcessKind], ...] = tuple(
    sorted(_PROCESS_STEMS, key=lambda pair: len(pair[0]), reverse=True)
)
_process_stem_alternation = "|".join(
    re.escape(stem) for stem, _ in _PROCESS_STEMS_SORTED
)
_PROCESS_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?P<stem>"
    + _process_stem_alternation
    + r")(?P<tail>[\wäöåÄÖÅ]{0,12})",
    re.IGNORECASE,
)

# Process-shaped (untypeable) family matcher — same morphology, audited
# prefixes not in the typed table.
_shaped_alternation = "|".join(re.escape(p) for p in _PROCESS_SHAPED_PREFIXES)
_PROCESS_SHAPED_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _shaped_alternation + r")[\wäöåÄÖÅ]{0,12}",
    re.IGNORECASE,
)

#: Max gap (chars) between an actor head and the process noun for them to be
#: read as the same surface frame.
_MAX_ACTOR_GAP = 80

#: Max gap (chars) between a process noun and a following deadline cue.
_MAX_DEADLINE_GAP = 80

_RULE_ID = "fi.surface.procedure.v1"


# ---------------------------------------------------------------------------
# Frozen output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProcedureFrame:
    """A surface procedural-process frame. SURFACE FACT ONLY.

    Records that a known process noun appears in the text, optionally with an
    adjacent actor surface (who must do it) and/or an adjacent deadline cue.
    This is NOT an assertion that the act is valid, made, or enforceable —
    interpretation happens downstream.

    Attributes:
        process_kind:  The closed :class:`ProcessKind` typed from the noun stem.
        actor_span:    Byte span of an adjacent actor surface, or None.
        deadline_span: Byte span of an adjacent deadline cue, or None.
        source_span:   Byte span of the matched process noun (verbatim head).
        status:        Always "surface_fact_only".
        rule_id:       The recognizer rule that fired.
    """

    process_kind: ProcessKind
    actor_span: Optional[SourceSpan]
    deadline_span: Optional[SourceSpan]
    source_span: SourceSpan
    status: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class ProcedureResidual:
    """A typed residual: a process-SHAPED token that cannot be typed.

    Self-evidencing — the offending verbatim text is embedded in both
    ``surface_text`` and ``detail`` so the residual stands on its own. Never a
    silent drop, never a guessed kind.

    Attributes:
        surface_text: The verbatim offending process-shaped token.
        source_span:  Byte span of the offending token.
        detail:       Human-readable description embedding the offending text.
        rule_id:      The recognizer rule that flagged the residual.
    """

    surface_text: str
    source_span: SourceSpan
    detail: str
    rule_id: str


@dataclass(frozen=True, slots=True)
class ProcedureScan:
    """The full result of scanning one text: typed frames + typed residuals."""

    frames: Tuple[ProcedureFrame, ...]
    residuals: Tuple[ProcedureResidual, ...]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _span(source_file: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_file=source_file,
        byte_offset=start,
        byte_len=end - start,
    )


def _nearest_preceding_actor(
    actor_matches: List[re.Match[str]], proc_start: int
) -> Optional[re.Match[str]]:
    """Return the nearest actor ending within the gap window before ``proc_start``."""
    best: Optional[re.Match[str]] = None
    for m in actor_matches:
        if m.end() > proc_start:
            break
        if proc_start - m.end() <= _MAX_ACTOR_GAP:
            best = m
    return best


def _nearest_following_deadline(
    deadline_matches: List[re.Match[str]], proc_end: int
) -> Optional[re.Match[str]]:
    """Return the nearest deadline starting within the gap window after ``proc_end``."""
    for m in deadline_matches:
        if m.start() < proc_end:
            continue
        if m.start() - proc_end <= _MAX_DEADLINE_GAP:
            return m
        break
    return None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def recognize_procedure_frames(
    text: str, source_file: str = ""
) -> List[ProcedureFrame]:
    """Recognise procedural process frames in ``text`` (typed frames only).

    Convenience wrapper over :func:`scan_procedure` returning just the typed
    :class:`ProcedureFrame` rows in document order. Every frame carries
    ``status="surface_fact_only"`` and records a SURFACE FACT, never a legal
    conclusion. Process-shaped tokens that cannot be typed are NOT returned here
    (use :func:`scan_procedure` to also receive the typed residuals); they are
    never silently swallowed into a guessed frame.
    """
    return list(scan_procedure(text, source_file=source_file).frames)


def scan_procedure(text: str, source_file: str = "") -> ProcedureScan:
    """Scan ``text`` for procedural process frames and typed residuals.

    Returns a :class:`ProcedureScan` carrying:

      - ``frames``: one :class:`ProcedureFrame` per recognised process noun
        (typed to the closed :class:`ProcessKind` set), with an adjacent actor
        span and/or deadline span captured when present.
      - ``residuals``: one :class:`ProcedureResidual` per process-SHAPED token
        from the audited "looks procedural" family that cannot be typed —
        fail-loud, self-evidencing, never a guess.

    SURFACE FACTS ONLY: no validity/duty/enforceability conclusion is ever
    produced. Frames are ordered by source offset.
    """
    # Substring guards are case-folded: a clause-leading capitalized process
    # noun ("Hakemus ...") must still trip its guard.
    lowered = text.lower()
    has_proc_guard = any(g in lowered for g in _PROCESS_GUARDS)
    has_shaped = any(p in lowered for p in _PROCESS_SHAPED_PREFIXES)
    has_deadline_guard = any(g in lowered for g in _DEADLINE_GUARDS)
    if not has_proc_guard and not has_shaped and not has_deadline_guard:
        return ProcedureScan(frames=(), residuals=())

    actor_matches = list(_ACTOR_RE.finditer(text))
    deadline_matches = (
        list(_DEADLINE_RE.finditer(text)) if has_deadline_guard else []
    )

    frames: List[ProcedureFrame] = []
    typed_spans: List[Tuple[int, int]] = []

    # Deadline-cue spans (a deadline phrase is itself a MAARAAIKA process). A
    # määräaika process-noun match that coincides with a deadline cue (e.g.
    # "määräajassa") is the same surface object — track these spans so the noun
    # loop does not double-emit a MAARAAIKA already produced by the cue loop.
    deadline_spans: set[Tuple[int, int]] = {
        (dm.start(), dm.end()) for dm in deadline_matches
    }

    if has_proc_guard:
        for m in _PROCESS_RE.finditer(text):
            stem = m.group("stem").lower()
            kind: Optional[ProcessKind] = None
            for cand_stem, cand_kind in _PROCESS_STEMS_SORTED:
                if stem == cand_stem:
                    kind = cand_kind
                    break
            if kind is None:  # pragma: no cover - alternation guarantees a hit
                continue

            proc_start, proc_end = m.start(), m.end()
            typed_spans.append((proc_start, proc_end))

            # A MAARAAIKA noun that IS a deadline cue is emitted by the deadline
            # loop below (which captures the full cue span); skip it here.
            if kind is ProcessKind.MAARAAIKA and any(
                ds <= proc_start < de for (ds, de) in deadline_spans
            ):
                continue

            actor_m = _nearest_preceding_actor(actor_matches, proc_start)
            deadline_m = _nearest_following_deadline(deadline_matches, proc_end)

            actor_span = (
                _span(source_file, actor_m.start(), actor_m.end())
                if actor_m is not None
                else None
            )
            deadline_span = (
                _span(source_file, deadline_m.start(), deadline_m.end())
                if deadline_m is not None
                else None
            )

            frames.append(
                ProcedureFrame(
                    process_kind=kind,
                    actor_span=actor_span,
                    deadline_span=deadline_span,
                    source_span=_span(source_file, proc_start, proc_end),
                    status="surface_fact_only",
                    rule_id=_RULE_ID,
                )
            )

    # Every deadline cue is itself a MAARAAIKA process frame, whether or not it
    # was also attached as the deadline span of a preceding process noun. So
    # "päätös ... 30 päivän kuluessa" yields BOTH a PAATOS frame (with the cue
    # as its deadline span) AND a standalone MAARAAIKA frame for the cue itself.
    for dm in deadline_matches:
        actor_m = _nearest_preceding_actor(actor_matches, dm.start())
        actor_span = (
            _span(source_file, actor_m.start(), actor_m.end())
            if actor_m is not None
            else None
        )
        frames.append(
            ProcedureFrame(
                process_kind=ProcessKind.MAARAAIKA,
                actor_span=actor_span,
                deadline_span=_span(source_file, dm.start(), dm.end()),
                source_span=_span(source_file, dm.start(), dm.end()),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )

    # FAIL-LOUD residuals: process-shaped tokens we cannot type.
    residuals: List[ProcedureResidual] = []
    if has_shaped:
        for m in _PROCESS_SHAPED_RE.finditer(text):
            # Skip if this span coincides with a typed process noun (defensive;
            # the shaped prefixes are disjoint from the typed stems by audit).
            if any(s <= m.start() < e for (s, e) in typed_spans):
                continue
            surface = m.group(0)
            residuals.append(
                ProcedureResidual(
                    surface_text=surface,
                    source_span=_span(source_file, m.start(), m.end()),
                    detail=(
                        f"process-shaped token {surface!r} is not in the closed "
                        f"process-kind vocabulary; refusing to guess a kind"
                    ),
                    rule_id=_RULE_ID,
                )
            )

    frames.sort(key=lambda f: f.source_span.byte_offset)
    residuals.sort(key=lambda r: r.source_span.byte_offset)
    return ProcedureScan(frames=tuple(frames), residuals=tuple(residuals))
