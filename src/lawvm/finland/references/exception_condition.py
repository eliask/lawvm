"""Surface-level exception/condition cue recognition (the H6 exception/condition lens).

This module implements the **H6 "exception/condition surface cue"** lens of the
Legal Surface Algebra. It scans Finnish statutory prose for closed-list cue words
that *grammatically mark* an exception ("ei kuitenkaan", "sen estämättä",
"poiketen siitä mitä", "lukuun ottamatta", "jollei", "paitsi", "ellei") or a
condition ("jos", "kun", "mikäli", "siltä osin kuin", "edellyttäen että",
"sillä edellytyksellä että"), and records each as a TYPED SURFACE FACT.

CRITICAL SAFETY BOUNDARY (non-negotiable — this lens is the EASIEST to overclaim):
================================================================================
This layer records SURFACE FACTS ONLY. It does the following and NOTHING more:

  - tag that a closed-list cue word appears;
  - record its verbatim marker text and its byte span;
  - attach a COARSE ``scope_hint`` — the short following clause span up to the
    next clause boundary (``,`` / ``;`` / ``.`` / newline), as a pointer only.

It NEVER:

  - resolves WHICH rule is excepted/overridden;
  - asserts WHETHER a rule is overridden, invalid, or unenforceable;
  - classifies the cue as a legal "exception" vs "limitation" vs "proviso";
  - links the cue to a target provision;
  - parses what is actually excepted or conditioned.

The ``scope_hint`` is a coarse SURFACE pointer, NOT a parse. It says "the
exception/condition cue is here and the text it modifies is roughly over there";
it makes no claim about the semantic content of that span. A cue we match but for
which we cannot bound a scope (nothing but boundary/whitespace follows) is still a
valid cue and is emitted with ``scope_hint=None`` — we NEVER guess a scope.

Precision tradeoff for the common condition cues ``jos`` and ``kun``:
====================================================================
``jos`` ("if") and ``kun`` ("when") are extremely common Finnish words and also
appear as substrings of unrelated tokens (e.g. ``jos`` inside ``josta``,
``jossa``, ``jostakin``; ``kun`` inside ``kunta``, ``kunnes``, ``kuntoutus``).
To keep noise down we require, for ALL cues, word boundaries on both sides, and
for ``jos`` / ``kun`` SPECIFICALLY we additionally require a *clause-initial-ish*
position: the cue must be preceded only by start-of-text, a clause boundary
(``,`` / ``;`` / ``.`` / ``:`` / newline / opening paren), or whitespace
following one of those. This deliberately UNDER-matches: a ``jos`` buried
mid-clause that a human would still read as a conditional may be skipped. We
prefer precision over recall here because this lens is surface-only and a missed
cue is a typed silence, whereas a false cue is an overclaim. Multi-word condition
cues (``mikäli``, ``siltä osin kuin``, ``edellyttäen että`` …) are unambiguous
enough that only the word-boundary guard applies to them.

Closed-list discipline (mirrors ``vague.py`` §1.11 / ``actor_modal.py``):
  - The exception and condition marker sets are CLOSED, audited tuples. A token
    outside them never fires. New markers are added by editing the tuples, never
    by heuristic.
  - Matching is longest-first within each list so e.g. "sillä edellytyksellä
    että" beats nothing nested and "siltä osin kuin" is preferred over any
    shorter overlap.
  - One alternation pattern is compiled at module scope over the fixed phrase
    set; the caller may do a cheap substring pre-guard before invoking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.legal_surface.clause_segment import (
    bound_scope_hint as _shared_bound_scope_hint,
)
from lawvm.finland.legal_surface.clause_segment import (
    is_clause_initial_ish as _shared_is_clause_initial_ish,
)

CueKind = Literal["EXCEPTION", "CONDITION"]

# ---------------------------------------------------------------------------
# Closed cue lists (NORMATIVE — surface markers, NOT legal categories)
# ---------------------------------------------------------------------------
#
# Glosses describe the SURFACE form only. Membership means "this token
# grammatically marks an exception/condition in Finnish statutory prose"; it does
# NOT mean the recognizer asserts an exception/condition holds legally.
#
# EXCEPTION cues:
#   ei kuitenkaan          "not however" (carve-out of the preceding rule)
#   sen estämättä          "notwithstanding it / despite it"
#   poiketen siitä mitä    "in derogation from what (is provided)"
#   lukuun ottamatta       "with the exception of / excluding"
#   jollei                 "unless / if not" (negative conditional carve-out)
#   paitsi                 "except / save"
#   ellei                  "unless / if not"
_EXCEPTION_MARKERS: tuple[str, ...] = (
    "poiketen siitä mitä",
    "ei kuitenkaan",
    "lukuun ottamatta",
    "sen estämättä",
    "jollei",
    "paitsi",
    "ellei",
)

# CONDITION cues:
#   sillä edellytyksellä että  "on the condition that"
#   edellyttäen että           "provided that"
#   siltä osin kuin            "insofar as / to the extent that"
#   mikäli                     "if / in case"
#   jos                        "if"
#   kun                        "when"
_CONDITION_MARKERS: tuple[str, ...] = (
    "sillä edellytyksellä että",
    "edellyttäen että",
    "siltä osin kuin",
    "mikäli",
    "jos",
    "kun",
)

#: Cheap substring pre-guards; if none of these appears, no cue can match.
_GUARDS: tuple[str, ...] = (
    "jos",
    "kun",
    "mikäli",
    "siltä",
    "edellyt",
    "kuitenkaan",
    "estämättä",
    "poiketen",
    "lukuun",
    "jollei",
    "paitsi",
    "ellei",
)

#: Cues that need the stricter clause-initial-ish guard because they are common,
#: short, and appear as substrings of unrelated words.
_CLAUSE_INITIAL_CUES: frozenset[str] = frozenset({"jos", "kun"})

#: Finnish word-char negative-lookaround class (ASCII word chars + äöå/ÄÖÅ).
_WC = r"[\wäöåÄÖÅ]"

# The clause-boundary logic (boundary char set + clause-initial / scope-bounding
# rules) is the SHARED authority in
# ``lawvm.finland.legal_surface.clause_segment``, consumed via the delegating
# ``_is_clause_initial_ish`` / ``_bound_scope_hint`` helpers below. This
# recognizer no longer keeps its own copy (rule-of-three: it had become the 2nd
# in-tree clause splitter).

#: Maximum scope-hint span length (characters). The scope hint is a SURFACE
#: pointer only; it is not parsed and is bounded to avoid runaway spans.
_MAX_SCOPE_HINT = 200

_RULE_ID = "fi.surface.exception_condition.v1"


def _build_re(markers: tuple[str, ...]) -> re.Pattern[str]:
    """Compile a longest-first, word-bounded alternation over ``markers``."""
    phrases_longest_first = sorted(markers, key=len, reverse=True)
    alternation = "|".join(
        r"\s+".join(re.escape(word) for word in phrase.split(" "))
        for phrase in phrases_longest_first
    )
    return re.compile(
        r"(?<!" + _WC + r")(?:" + alternation + r")(?!" + _WC + r")",
        re.IGNORECASE,
    )


_EXCEPTION_RE = _build_re(_EXCEPTION_MARKERS)
_CONDITION_RE = _build_re(_CONDITION_MARKERS)


# ---------------------------------------------------------------------------
# Frozen output type
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExceptionConditionCue:
    """A surface exception/condition cue. SURFACE FACT ONLY.

    Records that a closed-list cue word appears, with a COARSE pointer to the
    clause it modifies. This is NOT an assertion that a rule is excepted,
    overridden, limited, invalid, or unenforceable — interpretation happens in a
    later layer that consumes these surface facts.

    Attributes:
        cue_kind:    "EXCEPTION" or "CONDITION" (which closed list fired).
        marker_text: The matched cue surface, VERBATIM (case preserved — a
                     sentence-initial cue keeps its capital, e.g. "Mikäli"),
                     with inter-word whitespace normalised to a single space.
        scope_hint:  Byte span of the short following clause (a coarse pointer up
                     to the next clause boundary), or None if no scope could be
                     bounded. NEVER a parse of what is excepted/conditioned.
        source_span: Byte span of the cue marker itself in the source text.
        status:      Always "surface_fact_only".
        rule_id:     The recognizer rule that fired.
    """

    cue_kind: CueKind
    marker_text: str
    scope_hint: Optional[SourceSpan]
    source_span: SourceSpan
    exception_status: Literal["surface_fact_only"]
    rule_id: str


# ---------------------------------------------------------------------------
# Recognizer
# ---------------------------------------------------------------------------


def _span(source_file: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_file=source_file,
        byte_offset=start,
        byte_len=end - start,
    )


def _is_clause_initial_ish(text: str, start: int) -> bool:
    """Is the cue at ``start`` clause-initial-ish?

    Delegates to the SHARED clause-boundary authority
    (``lawvm.finland.legal_surface.clause_segment.is_clause_initial_ish``):
    everything before ``start`` back to the most recent non-whitespace char is a
    clause boundary, an opening paren, or start-of-text. SURFACE check only —
    used to suppress mid-clause ``jos``/``kun`` noise. Byte-identical to the
    previous private copy.
    """
    return _shared_is_clause_initial_ish(text, start)


def _bound_scope_hint(text: str, after: int) -> Optional[Tuple[int, int]]:
    """Bound a coarse scope-hint span starting after offset ``after``.

    Delegates to the SHARED clause-boundary authority
    (``lawvm.finland.legal_surface.clause_segment.bound_scope_hint``), passing
    this recognizer's :data:`_MAX_SCOPE_HINT`. SURFACE ONLY: the run of text from
    the first non-whitespace char after the cue up to (not including) the next
    clause boundary, bounded by ``_MAX_SCOPE_HINT``. Returns (start, end) or None
    when nothing but whitespace/boundary follows. Byte-identical to the previous
    private copy.
    """
    return _shared_bound_scope_hint(text, after, max_len=_MAX_SCOPE_HINT)


def _scan(
    text: str,
    pattern: re.Pattern[str],
    cue_kind: CueKind,
    source_file: str,
) -> List[ExceptionConditionCue]:
    out: List[ExceptionConditionCue] = []
    for m in pattern.finditer(text):
        norm_marker = re.sub(r"\s+", " ", m.group(0))
        if norm_marker.lower() in _CLAUSE_INITIAL_CUES and not _is_clause_initial_ish(
            text, m.start()
        ):
            continue  # mid-clause jos/kun: precision over recall, skip
        bounds = _bound_scope_hint(text, m.end())
        scope_hint = (
            _span(source_file, bounds[0], bounds[1]) if bounds is not None else None
        )
        out.append(
            ExceptionConditionCue(
                cue_kind=cue_kind,
                marker_text=norm_marker,
                scope_hint=scope_hint,
                source_span=_span(source_file, m.start(), m.end()),
                exception_status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )
    return out


def recognize_exception_condition_cues(
    text: str, source_file: str = ""
) -> List[ExceptionConditionCue]:
    """Recognise closed-list exception/condition surface cues in ``text``.

    Returns one :class:`ExceptionConditionCue` per matched cue, in document
    order, each ``status="surface_fact_only"``. The result records SURFACE FACTS
    ONLY: the cue marker, its span, and a COARSE ``scope_hint`` pointer to the
    following clause. It NEVER resolves which rule is overridden, never classifies
    the cue legally, and never links to a target provision. A matched cue whose
    scope cannot be bounded is emitted with ``scope_hint=None`` — a scope is never
    guessed.

    ``jos`` / ``kun`` only fire when clause-initial-ish (see module docstring): a
    deliberate precision-over-recall choice for these very common words.
    """
    lowered = text.lower()
    if not any(guard in lowered for guard in _GUARDS):
        return []
    cues = _scan(text, _EXCEPTION_RE, "EXCEPTION", source_file)
    cues += _scan(text, _CONDITION_RE, "CONDITION", source_file)
    cues.sort(key=lambda c: c.source_span.byte_offset)
    return cues
