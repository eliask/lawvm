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

TOKEN/GRAMMAR RECOGNIZER (Phase 7, decision B):
==============================================================================
This recognizer runs over the source-preserving :class:`TokenTape` substrate
(``lawvm.core.legal_surface_tokens`` / ``lawvm.finland.legal_surface.tokenize``)
rather than regex-scanning raw text. The two old regex blockers are retired:

  - A process noun is matched by testing a ``word``-category token's
    ``normalized`` surface against the closed STEM table. The frame span is the
    WHOLE matched token (``Token.char_start``/``.char_end``) — there is no
    arbitrary 12-character inflectional-tail cap any more (the old
    ``tail[0,12]`` could end mid-token).
  - A deadline cue is matched as a TOKEN WINDOW: a trigger token plus a bounded
    run of following tokens up to a sentence-boundary token, rather than a
    ``viimeistään[^.;:\\n]{0,80}`` char-run that spanned many tokens.

Spans are therefore TOKEN-ALIGNED (re-baselined from the old char-cap spans);
this is expected and accepted. The frame/residual PAYLOAD SHAPE is unchanged
(``process_kind`` value, residual ``surface_text``/kind), so the lens adapter
and graph keep working.

The :class:`~lawvm.core.legal_surface_tokens.MorphOverlay` is consulted where a
process-noun head is in the closed lemma inventory (``päätös`` → ``päätös``);
most process nouns (``hakemus``, ``ilmoitus`` …) are NOT in that inventory, so
token-stem matching stays the primary path and the overlay is an additive
confirmation, never a gate.

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
``ref_mention_extractor.py`` and is not wired into any graph. The actor
vocabulary, when used, is sourced READ-ONLY from
:data:`lawvm.finland.canonical_actor_registry.REGISTRY` plus a small CLOSED list
of generic legal role-actors.

Closed-list discipline (mirrors vague.py §1.11 / actor_modal.py):
  - The process-noun vocabulary is a CLOSED, audited mapping of inflection
    STEMS to a :class:`ProcessKind` enum value. A token outside it never fires.
  - Matching is over token surfaces with a cheap substring pre-guard.
  - A process-SHAPED token (a member of an audited "looks procedural" family)
    that cannot be typed to the closed kind set is emitted as a typed
    :class:`ProcedureResidual` — self-evidencing (embeds the offending text),
    never a silent drop, never a guess.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Literal, Optional, Tuple

from lawvm.core.legal_surface_tokens import MorphOverlay, Token, TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.legal_surface.tokenize import build_token_tape
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
# an inflected word) to its ProcessKind. A ``word``-category token whose
# casefolded surface STARTS WITH one of these stems is typed to that kind.
# Stems are ordered longest-first so a more specific stem wins over a shorter
# overlapping one. These are SURFACE stems, not legal categories.
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

#: Stems ordered longest-first so a more specific stem wins.
_PROCESS_STEMS_SORTED: tuple[tuple[str, ProcessKind], ...] = tuple(
    sorted(_PROCESS_STEMS, key=lambda pair: len(pair[0]), reverse=True)
)

#: Cheap substring pre-guards. If none of these appears, no process noun can
#: match. Each is the longest stable prefix of a stem family.
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

#: MorphOverlay lemma → ProcessKind for the process-noun heads that are in the
#: closed lemma inventory. Used as an ADDITIVE confirmation of a stem hit (never
#: a gate): most process nouns are NOT in the inventory, so an absent annotation
#: is "unknown", never "not a process noun". Only ``päätös`` is currently a
#: known head; the others fall through to pure stem matching.
_PROCESS_LEMMA_KINDS: dict[str, ProcessKind] = {
    "päätös": ProcessKind.PAATOS,
}

# ---------------------------------------------------------------------------
# Process-SHAPED family (for fail-loud residuals).
# ---------------------------------------------------------------------------
#
# A CLOSED set of prefixes that LOOK procedural (the morphology of an
# administrative/judicial act noun) but are NOT in the typed stem table. A
# ``word`` token starting with one of these and not matched by a typed stem is a
# process-shaped token we cannot type → a typed residual (never a guess). This
# keeps the fail-loud boundary explicit: only audited "looks procedural"
# families trip a residual; ordinary prose does not.
_PROCESS_SHAPED_PREFIXES: tuple[str, ...] = (
    "anomu",      # anomus (an application-like noun NOT in the closed set)
    "vaatimu",    # vaatimus (claim/demand — procedural-shaped, untyped)
    "selvity",    # selvitys (account/clarification — untyped)
    "todistu",    # todistus (certificate — untyped)
    "muistutu",   # muistutus (objection/reminder — untyped)
    "esity",      # esitys (proposal — untyped)
)

#: Shaped prefixes ordered longest-first (deterministic surface capture).
_PROCESS_SHAPED_SORTED: tuple[str, ...] = tuple(
    sorted(_PROCESS_SHAPED_PREFIXES, key=len, reverse=True)
)

# ---------------------------------------------------------------------------
# Closed generic role-actor list (NORMATIVE) — for actor-span detection.
# ---------------------------------------------------------------------------
#
# The closed generic role/class actors NOT carried by the institutional registry
# are sourced from the shared :mod:`lawvm.finland.references.role_actors` module
# (imported above as ``_ROLE_ACTORS``), in the nominative and the common
# genitive forms that head an actor noun phrase that "must do" a procedural act
# ("hakijan on toimitettava ..."). Each phrase is stored as a list of its
# casefolded WORD tokens for token-window matching.


def _phrase_to_words(phrase: str) -> Tuple[str, ...]:
    """Split a vocabulary phrase into its casefolded ``word`` tokens.

    A vocabulary phrase is matched against the tape as a sequence of ``word``
    tokens separated by whitespace; punctuation/dash characters inside a phrase
    become their own tokens, so phrases with internal punctuation are split on
    those too. We mirror the tokenizer's word-char class by tokenizing the
    phrase itself and keeping only the ``word`` tokens.
    """
    tape = build_token_tape("phrase", phrase)
    return tuple(t.normalized for t in tape.tokens if t.category == "word")


def _build_actor_phrases() -> Tuple[Tuple[str, ...], ...]:
    phrases = set(REGISTRY.all_phrases_longest_first())
    phrases.update(expand_role_actor_phrases(_ROLE_ACTORS))
    word_phrases = {
        _phrase_to_words(p) for p in phrases
    }
    word_phrases.discard(())  # drop any phrase with no word tokens
    # longest-first by total word-character length so a more specific phrase wins
    return tuple(
        sorted(
            word_phrases,
            key=lambda ws: sum(len(w) for w in ws),
            reverse=True,
        )
    )


_ACTOR_PHRASES_LONGEST_FIRST: Tuple[Tuple[str, ...], ...] = _build_actor_phrases()
_ACTOR_PHRASES_BY_FIRST_WORD: dict[str, Tuple[Tuple[str, ...], ...]] = {
    first_word: tuple(
        phrase
        for phrase in _ACTOR_PHRASES_LONGEST_FIRST
        if phrase[0] == first_word
    )
    for first_word in {
        phrase[0] for phrase in _ACTOR_PHRASES_LONGEST_FIRST if phrase
    }
}

# ---------------------------------------------------------------------------
# Deadline cue family (NORMATIVE, closed) — for deadline-span detection.
# ---------------------------------------------------------------------------
#
# Surface cues marking a time-limit attached to a procedural act. These detect
# the SPAN of a deadline phrase as a TOKEN WINDOW; they do not parse a date (the
# H3 temporal lens owns date parsing). The closed cue grammar:
#
#   "<N> päivän|viikon|kuukauden|vuoden kuluessa|kuluttua"  (within N <unit>)
#   "viimeistään …"   — a TOKEN WINDOW: the trigger token plus a bounded run of
#                        following tokens up to a sentence-boundary token
#   "määräajassa"  /  "määräajan kuluessa"                  (within the time-limit)
#   "määräaikaan mennessä"

#: Time-unit words that follow a leading number in the "<N> <unit> kuluessa" cue.
_DEADLINE_UNITS: frozenset[str] = frozenset(
    {"päivän", "viikon", "kuukauden", "vuoden"}
)
#: Closing words of the "<N> <unit> …" cue.
_DEADLINE_KULU: frozenset[str] = frozenset({"kuluessa", "kuluttua"})

#: Punctuation surfaces that terminate a "viimeistään …" token window. Mirrors
#: the old ``[^.;:\n]`` char class (a sentence/clause boundary), expressed at
#: token granularity over ``punct`` token surfaces.
_DEADLINE_STOP_PUNCT: frozenset[str] = frozenset({".", ";", ":", "\n"})

#: Max number of NON-whitespace tokens to absorb after a "viimeistään" trigger
#: before forcing a stop (bounded window; mirrors the old 80-char cap intent).
_VIIMEISTAAN_MAX_TOKENS = 16

#: Cheap substring guards for the deadline detector.
_DEADLINE_GUARDS: tuple[str, ...] = (
    "kuluessa",
    "kuluttua",
    "viimeistään",
    "määräaja",
    "mennessä",
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
        actor_span:    Span of an adjacent actor surface, or None.
        deadline_span: Span of an adjacent deadline cue, or None.
        source_span:   Span of the matched process noun (verbatim head token).
        status:        Always "surface_fact_only".
        rule_id:       The recognizer rule that fired.
    """

    process_kind: ProcessKind
    actor_span: Optional[SourceSpan]
    deadline_span: Optional[SourceSpan]
    source_span: SourceSpan
    procedure_status: Literal["surface_fact_only"]
    rule_id: str


@dataclass(frozen=True, slots=True)
class ProcedureResidual:
    """A typed residual: a process-SHAPED token that cannot be typed.

    Self-evidencing — the offending verbatim text is embedded in both
    ``surface_text`` and ``detail`` so the residual stands on its own. Never a
    silent drop, never a guessed kind.

    Attributes:
        surface_text: The verbatim offending process-shaped token.
        source_span:  Span of the offending token.
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


def _type_process_token(
    tok: Token, overlay: Optional[MorphOverlay], token_index: int
) -> Optional[ProcessKind]:
    """Type a ``word`` token to a :class:`ProcessKind`, or None.

    Primary path: the casefolded surface starts with a closed stem (longest
    first). The MorphOverlay is consulted as an ADDITIVE confirmation when the
    head is in the closed lemma inventory (currently only ``päätös``); an absent
    annotation is "unknown", never a veto.
    """
    norm = tok.normalized
    for stem, kind in _PROCESS_STEMS_SORTED:
        if norm.startswith(stem):
            # Overlay confirmation where available (additive, never a gate).
            if overlay is not None:
                ann = overlay.annotations.get(token_index)
                if ann is not None:
                    for lemma in ann.lemmas:
                        lemma_kind = _PROCESS_LEMMA_KINDS.get(lemma)
                        if lemma_kind is not None:
                            return lemma_kind
            return kind
    return None


def _shaped_surface(tok: Token) -> Optional[str]:
    """If ``tok`` is a process-SHAPED (untypeable) token, return its surface."""
    norm = tok.normalized
    for prefix in _PROCESS_SHAPED_SORTED:
        if norm.startswith(prefix):
            return tok.text
    return None


def _scan_actors(tokens: Tuple[Token, ...]) -> List[Tuple[int, int]]:
    """Token-scan closed actor phrases. Returns (char_start, char_end) spans.

    A phrase matches as a run of its ``word`` tokens separated by whitespace
    tokens only. Longest-first; the first phrase that matches at a position
    wins, and no overlapping shorter match is taken inside it.
    """
    spans: List[Tuple[int, int]] = []
    n = len(tokens)
    i = 0
    while i < n:
        if tokens[i].category != "word":
            i += 1
            continue
        phrases = _ACTOR_PHRASES_BY_FIRST_WORD.get(tokens[i].normalized)
        if phrases is None:
            i += 1
            continue
        best_end_idx: Optional[int] = None
        for phrase in phrases:
            end_idx = _match_word_phrase(tokens, i, phrase)
            if end_idx is not None:
                best_end_idx = end_idx
                break
        if best_end_idx is not None:
            spans.append(
                (tokens[i].char_start, tokens[best_end_idx].char_end)
            )
            i = best_end_idx + 1
        else:
            i += 1
    return spans


def _match_word_phrase(
    tokens: Tuple[Token, ...], start_idx: int, phrase: Tuple[str, ...]
) -> Optional[int]:
    """Match ``phrase`` (word list) starting at token ``start_idx``.

    Returns the index of the LAST matched token (inclusive), or None. Phrase
    words are separated by whitespace tokens only.
    """
    if not phrase:
        return None
    idx = start_idx
    last_idx = start_idx
    n = len(tokens)
    for w_i, word in enumerate(phrase):
        if idx >= n:
            return None
        tok = tokens[idx]
        if tok.category != "word" or tok.normalized != word:
            return None
        last_idx = idx
        idx += 1
        if w_i < len(phrase) - 1:
            if idx >= n or tokens[idx].category != "whitespace":
                return None
            idx += 1
            while idx < n and tokens[idx].category == "whitespace":
                idx += 1
    return last_idx


def _scan_deadlines(tokens: Tuple[Token, ...]) -> List[Tuple[int, int]]:
    """Token-scan deadline cues as TOKEN WINDOWS. Returns (start, end) spans."""
    spans: List[Tuple[int, int]] = []
    n = len(tokens)
    i = 0
    while i < n:
        tok = tokens[i]
        if tok.category == "word":
            norm = tok.normalized
            # "viimeistään …" — token window up to a sentence boundary.
            if norm == "viimeistään":
                end_idx = _viimeistaan_window(tokens, i)
                spans.append((tok.char_start, tokens[end_idx].char_end))
                i = end_idx + 1
                continue
            # "määräajassa" (a single inflected määräaika-in-essive) — bare cue.
            if norm == "määräajassa":
                spans.append((tok.char_start, tok.char_end))
                i += 1
                continue
            # "määräajan kuluessa" — two-word cue.
            if norm == "määräajan":
                end_idx = _match_word_phrase(tokens, i, ("määräajan", "kuluessa"))
                if end_idx is not None:
                    spans.append((tok.char_start, tokens[end_idx].char_end))
                    i = end_idx + 1
                    continue
            # "määräaikaan mennessä" — two-word cue.
            if norm == "määräaikaan":
                end_idx = _match_word_phrase(
                    tokens, i, ("määräaikaan", "mennessä")
                )
                if end_idx is not None:
                    spans.append((tok.char_start, tokens[end_idx].char_end))
                    i = end_idx + 1
                    continue
        elif tok.category == "number":
            # "<N> päivän|viikon|kuukauden|vuoden kuluessa|kuluttua"
            end_idx = _numeric_deadline_window(tokens, i)
            if end_idx is not None:
                spans.append((tok.char_start, tokens[end_idx].char_end))
                i = end_idx + 1
                continue
        i += 1
    return spans


def _numeric_deadline_window(
    tokens: Tuple[Token, ...], start_idx: int
) -> Optional[int]:
    """Match "<N> <unit> <kuluessa|kuluttua>" from a number token.

    The old regex required exactly single whitespace between the three words;
    we mirror "one-or-more whitespace tokens" between them. Returns the last
    token index (inclusive) or None.
    """
    n = len(tokens)
    idx = start_idx + 1
    # whitespace
    if idx >= n or tokens[idx].category != "whitespace":
        return None
    idx += 1
    # unit word
    if idx >= n or tokens[idx].category != "word":
        return None
    if tokens[idx].normalized not in _DEADLINE_UNITS:
        return None
    idx += 1
    # whitespace
    if idx >= n or tokens[idx].category != "whitespace":
        return None
    idx += 1
    # closing word
    if idx >= n or tokens[idx].category != "word":
        return None
    if tokens[idx].normalized not in _DEADLINE_KULU:
        return None
    return idx


def _viimeistaan_window(tokens: Tuple[Token, ...], start_idx: int) -> int:
    """Token window for "viimeistään …": trigger + bounded run to a boundary.

    Absorbs following tokens until (whichever first): a ``punct`` whose surface
    is a sentence/clause boundary (``. ; : \\n``); the ``_VIIMEISTAAN_MAX_TOKENS``
    non-whitespace-token budget is spent; or the tape ends. Trailing whitespace
    is NOT included in the window (the span ends at the last absorbed
    non-whitespace token, mirroring the old run that excluded a trailing space).
    Returns the LAST included token index (inclusive); at minimum the trigger.
    """
    n = len(tokens)
    last_included = start_idx  # trigger token always included
    absorbed = 0
    idx = start_idx + 1
    while idx < n and absorbed < _VIIMEISTAAN_MAX_TOKENS:
        tok = tokens[idx]
        if tok.category == "whitespace":
            idx += 1
            continue
        if tok.category == "punct" and tok.text in _DEADLINE_STOP_PUNCT:
            break  # stop BEFORE the boundary punctuation
        last_included = idx
        absorbed += 1
        idx += 1
    return last_included


def _nearest_preceding(spans: List[Tuple[int, int]], anchor: int, gap: int) -> (
    Optional[Tuple[int, int]]
):
    """Nearest span ending within ``gap`` chars before ``anchor`` (or None)."""
    best: Optional[Tuple[int, int]] = None
    for s in spans:
        if s[1] > anchor:
            break
        if anchor - s[1] <= gap:
            best = s
    return best


def _nearest_following(spans: List[Tuple[int, int]], anchor: int, gap: int) -> (
    Optional[Tuple[int, int]]
):
    """Nearest span starting within ``gap`` chars after ``anchor`` (or None)."""
    for s in spans:
        if s[0] < anchor:
            continue
        if s[0] - anchor <= gap:
            return s
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


def scan_procedure(
    text: str,
    source_file: str = "",
    *,
    tape: Optional[TokenTape] = None,
    overlay: Optional[MorphOverlay] = None,
) -> ProcedureScan:
    """Scan ``text`` for procedural process frames and typed residuals.

    Token/grammar recognizer over the :class:`TokenTape` substrate. A caller
    that already holds a tape (and optionally a :class:`MorphOverlay`) for this
    text may pass them to avoid re-tokenizing; otherwise a tape is built on
    demand from ``text``. The tape MUST be the tape of ``text`` (its char spans
    index into ``text``); this is the lens's contract.

    Returns a :class:`ProcedureScan` carrying:

      - ``frames``: one :class:`ProcedureFrame` per recognised process noun
        (typed to the closed :class:`ProcessKind` set), with an adjacent actor
        span and/or deadline span captured when present. Spans are TOKEN-ALIGNED
        (the whole matched word/window).
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

    if tape is None:
        tape = build_token_tape(source_file or "procedure", text)
    tokens = tape.tokens

    actor_spans = _scan_actors(tokens)
    deadline_spans = _scan_deadlines(tokens) if has_deadline_guard else []
    deadline_starts: set[int] = {s[0] for s in deadline_spans}
    deadline_ranges = list(deadline_spans)

    frames: List[ProcedureFrame] = []
    typed_spans: List[Tuple[int, int]] = []

    if has_proc_guard:
        for token_index, tok in enumerate(tokens):
            if tok.category != "word":
                continue
            kind = _type_process_token(tok, overlay, token_index)
            if kind is None:
                continue

            proc_start, proc_end = tok.char_start, tok.char_end
            typed_spans.append((proc_start, proc_end))

            # A MAARAAIKA noun that IS a deadline cue is emitted by the deadline
            # loop below (which captures the full cue span); skip it here.
            if kind is ProcessKind.MAARAAIKA and proc_start in deadline_starts:
                continue

            actor = _nearest_preceding(actor_spans, proc_start, _MAX_ACTOR_GAP)
            deadline = _nearest_following(
                deadline_ranges, proc_end, _MAX_DEADLINE_GAP
            )

            actor_span = (
                _span(source_file, actor[0], actor[1])
                if actor is not None
                else None
            )
            deadline_span = (
                _span(source_file, deadline[0], deadline[1])
                if deadline is not None
                else None
            )

            frames.append(
                ProcedureFrame(
                    process_kind=kind,
                    actor_span=actor_span,
                    deadline_span=deadline_span,
                    source_span=_span(source_file, proc_start, proc_end),
                    procedure_status="surface_fact_only",
                    rule_id=_RULE_ID,
                )
            )

    # Every deadline cue is itself a MAARAAIKA process frame, whether or not it
    # was also attached as the deadline span of a preceding process noun. So
    # "päätös ... 30 päivän kuluessa" yields BOTH a PAATOS frame (with the cue
    # as its deadline span) AND a standalone MAARAAIKA frame for the cue itself.
    for ds, de in deadline_spans:
        actor = _nearest_preceding(actor_spans, ds, _MAX_ACTOR_GAP)
        actor_span = (
            _span(source_file, actor[0], actor[1])
            if actor is not None
            else None
        )
        frames.append(
            ProcedureFrame(
                process_kind=ProcessKind.MAARAAIKA,
                actor_span=actor_span,
                deadline_span=_span(source_file, ds, de),
                source_span=_span(source_file, ds, de),
                procedure_status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )

    # FAIL-LOUD residuals: process-shaped tokens we cannot type.
    residuals: List[ProcedureResidual] = []
    if has_shaped:
        for tok in tokens:
            if tok.category != "word":
                continue
            surface = _shaped_surface(tok)
            if surface is None:
                continue
            # Skip if this token coincides with a typed process noun (defensive;
            # the shaped prefixes are disjoint from the typed stems by audit).
            if any(s <= tok.char_start < e for (s, e) in typed_spans):
                continue
            residuals.append(
                ProcedureResidual(
                    surface_text=surface,
                    source_span=_span(source_file, tok.char_start, tok.char_end),
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
