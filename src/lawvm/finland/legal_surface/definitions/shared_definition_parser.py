"""ONE canonical definiendum-entry recognition pipeline, shared by both the
production binder and the SourceSyntaxGraph forest.

WHY THIS MODULE EXISTS (Pro ruling — definitions parser UNIFICATION)
====================================================================
There used to be TWO rival definition recognisers:

  * the PRODUCTION binder
    (``references.defined_terms.recognize_defined_term_bindings`` →
    ``_recognize_tarkoitetaan`` / ``_recognize_enumerated_definitions``), the
    canonical producer of ``DefinedTermBinding`` records the ``DefinitionLens``
    emits; and
  * the FOREST construction parse
    (``legal_surface.definition_parse.parse_definition_block`` →
    ``_inline_entries`` / ``_parse_enumerated_block``), which re-derived the
    definiendum from the SAME shared regexes / helpers but DROPPED the binder's
    precision pipeline (the left-edge trim ``_trim_to_definiendum_np`` and the
    clean-NP decline ``_is_clean_definiendum_phrase``). The forest therefore
    OVER-CAPTURED clause fragments (``sekä vakuutusvuodella``, ``ja jäteöljyllä``,
    ``n säännösten nojalla``) the binder correctly trimmed/declined.

This module is the EXTRACTED canonical core (Pro's choice a′): the per-entry
recognition pipeline both lanes now call, so they cannot drift. The binder's
recognisers and the forest's construction parse both delegate the *decision*
(what surface is the definiendum, what scope governs it, what act it binds) to
the two functions here — :func:`enumerated_entry_from_item` (an enumerated-block
list item) and :func:`inline_entry_from_match` (an inline ``X:llä tarkoitetaan``
match). The pipeline is BYTE-IDENTICAL to the binder's prior in-line logic: it
is a pure relocation, not a behaviour change, so the ``DefinitionLens`` output is
unchanged.

The pipeline (identical for both shapes after the shape-specific candidate run is
isolated):

  1. isolate the candidate definiendum word run (shape-specific: the leading
     adessive-headed run of an enumerated item / post-verb run, or the pre-verb
     run of an inline match with leading scope-locatives stripped);
  2. trim leading non-definiendum material — prior-entry coordinators / adverbial
     clauses — via :func:`_trim_to_definiendum_np`, preserving medial
     coordination;
  3. DECLINE a swept clause fragment / cross-reference idiom via
     :func:`_is_clean_definiendum_phrase` (tag-don't-guess: mint nothing rather
     than a garbled multi-word term);
  4. join the surviving phrase into the canonical definiendum surface, compute the
     definiens, and resolve a bound act id via the shared act recognizer.

Surface-only: no attachment/composition decision, no replay. The functions return
a small typed record; the CALLER frames it (a ``DefinedTermBinding`` with a
``SourceSpan`` in the binder, a ``DefinitionEntry`` with block-local char offsets
in the forest).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, slots=True)
class RecognizedEntry:
    """One recognised definiendum entry, shape-agnostic.

    Attributes:
        term:          The canonical definiendum SURFACE (the trimmed, clean
                       multi-word adessive phrase, as written), e.g.
                       ``vakuutusvuodella`` / ``öljypitoisella seoksella``.
        definiens:     The right-hand-side expansion text (may be ``""``).
        scope:         The binding scope from the closed vocabulary.
        target_ref:    Canonical act id when the definiens is/contains an act cite,
                       else ``None``.
        head_word_count: Number of WORDS the definiendum phrase spans BEFORE the
                       left-edge trim (i.e. the count returned by the adessive head
                       detector / the pre-verb run). The forest uses this to fix
                       the definiens char boundary independently of the trim.
    """

    term: str
    definiens: str
    scope: str
    target_ref: Optional[str]
    head_word_count: int


def enumerated_entry_from_item(
    run: str,
    rest: str,
    *,
    scope: str,
    text_for_scope: str | None = None,
    scope_pos: int | None = None,
) -> Optional[RecognizedEntry]:
    """Recognise ONE enumerated-block list item, or ``None`` (no fabrication).

    ``run`` is the leading word-run captured by ``_ENUM_ITEM`` (the candidate
    ``<definiendum-adessive> <expansion-head>``); ``rest`` is the regex tail (the
    remainder of the definiens up to the item ``;``). ``scope`` is the header's
    scope (an enumerated item ALWAYS inherits the block header scope, so the scope
    look-back is NOT consulted here).

    Applies the FULL canonical pipeline: adessive head detection
    (``_adessive_phrase_from_run``) → left-edge trim (``_trim_to_definiendum_np``)
    → clean-NP decline (``_is_clean_definiendum_phrase``). Returns ``None`` when no
    adessive head is found, the trim empties the phrase, or the phrase is a swept
    clause fragment — exactly the binder's prior in-line decisions.
    """
    from lawvm.finland.references.defined_terms import (
        _act_id_in_expansion,
        _adessive_phrase_from_run,
        _is_clean_definiendum_phrase,
        _trim_to_definiendum_np,
    )

    run_words = run.split()
    head_phrase = _adessive_phrase_from_run(run_words)
    if head_phrase is None:
        return None
    head_len = len(head_phrase)
    phrase_words = _trim_to_definiendum_np(head_phrase)
    if phrase_words is None:
        return None
    if not _is_clean_definiendum_phrase(phrase_words):
        return None
    term_surface = " ".join(phrase_words)
    # Any word-run tokens AFTER the definiendum head belong to the expansion
    # (prepended to the regex's tail) — the head index, BEFORE the trim, fixes
    # the boundary so the left-trim never moves the definiens.
    trailing = run_words[head_len:]
    expansion_text = (
        " ".join(trailing) + (" " if trailing else "") + rest.strip()
    ).strip()
    act_id = _act_id_in_expansion(expansion_text)
    return RecognizedEntry(
        term=term_surface,
        definiens=expansion_text,
        scope=scope,
        target_ref=act_id,
        head_word_count=head_len,
    )


def inline_entry_from_match(
    text: str,
    raw_term: str,
    expansion_text: str,
    scope: str,
) -> Optional[RecognizedEntry]:
    """Recognise ONE inline ``X:llä tarkoitetaan Y`` definiendum, or ``None``.

    ``raw_term`` is the PRE-verb run captured by ``_TARKOITETAAN`` (group
    ``term``); ``expansion_text`` is the captured expansion (group ``expansion``,
    already stripped). ``scope`` is the scope the caller resolved from the nearest
    preceding definitions-header cue (the look-back is shape-3-specific and stays
    with the caller, which holds the absolute offset).

    Applies the FULL canonical pipeline: the HEAD (last word) must be a
    definitional adessive definiendum (``_is_definitional_definiendum``) — the
    referential idiom (``…, jota / N momentissa … tarkoitetaan``) is declined;
    leading scope-locatives / pronoun-adessives are stripped; the left-edge is
    trimmed (``_trim_to_definiendum_np``); a swept clause fragment is declined
    (``_is_clean_definiendum_phrase``). Returns ``None`` on any decline — the
    binder's prior in-line decisions, byte for byte.
    """
    from lawvm.finland.references.defined_terms import (
        _PRONOUN_ADESSIVE_FORMS,
        _SCOPE_LEADERS,
        _act_id_in_expansion,
        _is_clean_definiendum_phrase,
        _is_definitional_definiendum,
        _trim_to_definiendum_np,
    )

    words = raw_term.split()
    if not words:
        return None
    last_word = words[-1]
    if not _is_definitional_definiendum(last_word):
        return None
    # Drop any leading scope locative ("Tässä laissa X:llä tarkoitetaan") or
    # leading demonstrative/relative pronoun-adessive; never part of the surface.
    start_idx = 0
    for i, w in enumerate(words[:-1]):
        low = w.lower()
        if low in _SCOPE_LEADERS or low in _PRONOUN_ADESSIVE_FORMS:
            start_idx = i + 1
    phrase_words = words[start_idx:]
    trimmed = _trim_to_definiendum_np(phrase_words)
    if trimmed is None:
        return None
    phrase_words = trimmed
    if not _is_clean_definiendum_phrase(phrase_words):
        return None
    term_surface = " ".join(phrase_words)
    act_id = _act_id_in_expansion(expansion_text)
    return RecognizedEntry(
        term=term_surface,
        definiens=expansion_text,
        scope=scope,
        target_ref=act_id,
        head_word_count=len(phrase_words),
    )


# ---------------------------------------------------------------------------
# Heading-split recall window (D3)
# ---------------------------------------------------------------------------
#
# A common Finnish drafting shape puts the DEFINIENDUM on its own physical line —
# a ``heading`` / ``subheading`` segment — immediately ABOVE the line that carries
# the verb:
#
#     Taaja-asutuksella                       (heading segment)
#       tarkoitetaan tässä laissa …           (prose segment)
#
# The WHOLE-BODY production binder scans the contiguous text and catches
# ``Taaja-asutuksella tarkoitetaan …`` across the newline. The FOREST, however,
# parses PER SEGMENT, so the prose segment alone opens with ``tarkoitetaan`` (a
# definiens-FIRST clause with no pre-verb definiendum) and declines — the
# definiendum, stranded in the preceding heading, is dropped. The same split
# occurs for an enumerated block whose chapeau (``… tarkoitetaan:``) is followed by
# the definienda as separate heading segments.
#
# :func:`heading_split_prefix` returns the preceding heading text to PREPEND to a
# definition segment so the per-segment reparse sees the SAME contiguous window the
# whole-body binder sees — recovering the stranded definiendum WITHOUT changing any
# forest node identity (it only widens the text handed to the construction parse).

#: Definition binding cues a definition segment may open with (casefolded). A
#: segment opening with one of these is a definiens-first clause whose definiendum,
#: if any, was stranded in a preceding heading.
_OPENING_CUES: tuple[str, ...] = ("tarkoitetaan", "tarkoittaa")


def opens_definiens_first(segment_text: str) -> bool:
    """True iff ``segment_text`` opens with a bare definition cue (no pre-verb run).

    The heading-split tell: the segment's FIRST word is the binding verb itself
    (``tarkoitetaan`` / ``tarkoittaa``), so the definiendum — if the construct has
    one — must live in a preceding heading line, not in this segment.
    """
    stripped = segment_text.lstrip()
    low = stripped.casefold()
    return any(low.startswith(cue) for cue in _OPENING_CUES)


def heading_split_prefix(heading_text: str) -> str | None:
    """Return a one-word heading definiendum prefix to prepend, or ``None``.

    ``heading_text`` is the immediately-preceding heading/subheading segment text.
    A heading is a definiendum candidate only when it is a SHORT noun phrase (a
    bounded run of word tokens) — never a full sentence. We keep this conservative
    (≤4 words, no sentence punctuation) so an ordinary prose subheading is not
    fabricated into a definiendum; the downstream
    :func:`inline_entry_from_match` / :func:`enumerated_entry_from_item` pipeline
    still applies the FULL adessive-head + clean-NP discipline, so a heading that
    is not a genuine adessive definiendum yields no entry regardless.
    """
    h = heading_text.strip()
    if not h:
        return None
    if any(ch in h for ch in ".:;"):
        return None
    words = h.split()
    if not (1 <= len(words) <= 4):
        return None
    return h


__all__ = [
    "RecognizedEntry",
    "enumerated_entry_from_item",
    "heading_split_prefix",
    "inline_entry_from_match",
    "opens_definiens_first",
]
