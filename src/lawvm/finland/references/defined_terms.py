"""Finnish defined-term / alias BINDING recognizer (catalogue family
``defined_term.alias_binding``).

A statute frequently introduces a SHORT local name for an act it cites, then
uses that name (inflected) throughout the rest of the text:

  ``… eläimistä saatavista sivutuotteista … annetussa asetuksessa (EY) N:o
  1069/2009 (sivutuoteasetus) …``

  → ``sivutuoteasetus`` now denotes ``(EY) N:o 1069/2009`` for the remainder of
    the document.

This module recognises the BINDING site (where the local term is introduced and
tied to a target), not the later uses.  Capturing the binding deterministically
turns every later inflected use of the term into a resolvable reference instead
of opaque residue — that is the residue-reducing value (FI_REFERENCE_CATALOGUE
``defined_term.alias_binding``, T2).

It is a SELF-CONTAINED recognizer over plain text: it does not depend on the
``<ref>`` markup or the ReferenceMention pipeline.  Integration into
``ref_mention_extractor`` is a later step; this module only produces the typed
``DefinedTermBinding`` records.

Three CONSERVATIVE shapes (catalogue canonical examples):

  1. parenthetical alias after an act cite:
       ``asetuksessa (EY) N:o 1069/2009 (sivutuoteasetus)``
     → bind ``sivutuoteasetus`` → ``(EY) N:o 1069/2009``  (parenthetical_alias)

  2. ``jäljempänä X`` / ``jäljempänä "X"`` after an act cite:
       ``ympäristönsuojelulaissa (527/2014, jäljempänä ympäristönsuojelulaki)``
       ``… (527/2014) (jäljempänä "ympäristönsuojelulaki")``
     → bind ``ympäristönsuojelulaki`` → ``527/2014``        (jaljempana)

  3. definitional ``X:llä tarkoitetaan Y`` / ``X tarkoitetaan Y``:
       ``sivutuotteella tarkoitetaan …``
       ``tässä laissa tarkoitetaan sivutuoteasetuksella (EY) N:o 1069/2009``
     → bind term X → expansion Y (and term → act when Y is an act cite)
                                                            (tarkoitetaan)

CONSERVATIVE morphology boundary (AGENTS.md fail-loud, FI_PARSE_OVERLAY_IR_MODEL
"tag-don't-guess"):

  A bound term is only marked as morphologically supported (``status="ok"``) when
  it is

    * a single word, OR
    * a final-head compound: a run of word-tokens where exactly ONE token (the
      LAST) carries the inflectable head and every preceding token is invariant
      (here approximated as: all tokens but the last are simple lowercase stems,
      no embedded case/agreement markers, no quotes, no commas).

  A complex NP — multiple tokens that look like an agreeing modifier + noun, an
  embedded case phrase, hyphenated coordination, etc. — is still EMITTED (so the
  binding is never silently dropped, AGENTS.md §1.8) but flagged
  ``status="unsupported_morphology"``: the binding target is known, but we refuse
  to guess how to inflect the term for later-use matching.

NEGATIVE discipline: a bare term used with NO binding construct yields NO
binding.  Recognition is anchored on the binding cue (parenthesis-after-cite,
``jäljempänä``, ``tarkoitetaan``); a term that merely appears in prose is not
fabricated into a binding.

Per AGENTS.md §1.11: all patterns compiled at module scope, bounded quantifiers,
substring guards before regex on long text.
"""
from __future__ import annotations

import re
from lawvm.core.regex_safety import compile_classifier_regex
from dataclasses import dataclass
from typing import Optional

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.legal_surface.definitions.shared_definition_parser import (
    enumerated_entry_from_item,
    inline_entry_from_match,
)
from lawvm.finland.references.cross_refs import _make_statute_id
from lawvm.finland.references.eu_reference import (
    DIALECT_DEFINED_TERMS,
    recognize_eu_act_ids,
)
from lawvm.finland.references.lemma_gate import (
    definitions_header_unit_alternation,
    definitions_header_unit_scope_map,
)

# ---------------------------------------------------------------------------
# Typed output
# ---------------------------------------------------------------------------

#: Closed scope vocabulary for a binding's ``scope`` field.  These are the only
#: values this recognizer emits — no free-form scope text.
#:
#:   * ``statute``    — statute-wide ("Tässä laissa …" / "Tätä lakia
#:                      sovellettaessa …"), AND the CONSERVATIVE fail-safe default
#:                      when no narrower cue is recognised (so the prior behaviour
#:                      — every binding ``statute`` — is the never-regress floor).
#:   * ``chapter``    — chapter-scoped ("Tässä luvussa …").
#:   * ``section``    — section-scoped ("Tässä pykälässä …").
#:   * ``subsection`` — subsection-scoped ("Tässä momentissa …").
_SCOPE_STATUTE = "statute"
_SCOPE_CHAPTER = "chapter"
_SCOPE_SECTION = "section"
_SCOPE_SUBSECTION = "subsection"

#: The closed set of allowed ``scope`` values (for callers / validation).
SCOPE_VALUES: frozenset[str] = frozenset(
    {_SCOPE_STATUTE, _SCOPE_CHAPTER, _SCOPE_SECTION, _SCOPE_SUBSECTION}
)

#: binding_kind values (closed set; kept as strings to match the task contract).
BINDING_PARENTHETICAL_ALIAS = "parenthetical_alias"
BINDING_JALJEMPANA = "jaljempana"
BINDING_TARKOITETAAN = "tarkoitetaan"

#: status values.
STATUS_OK = "ok"
STATUS_UNSUPPORTED_MORPHOLOGY = "unsupported_morphology"


@dataclass(frozen=True, slots=True)
class DefinedTermBinding:
    """A local term/alias introduced and bound to a target in a statute.

    Attributes:
        term:         The local term being defined (surface form, nominative as
                      written at the binding site), e.g. ``sivutuoteasetus``.
        target_ref:   Canonical act id the term denotes when the binding ties the
                      term to an act cite, else ``None``. Finnish ids are the
                      CANONICAL ``YEAR/NUMBER`` orientation (same authority as the
                      ``<ref>`` / cross-ref lane and the corpus store keys), NOT
                      the Finnish visible ``(NUMBER/YEAR)`` convention; EU ids keep
                      their source surface orientation.
        expansion:    The definitional expansion text for a ``tarkoitetaan``
                      binding whose right-hand side is NOT an act cite, else
                      ``None``.
        scope:        Binding scope from the closed vocabulary
                      :data:`SCOPE_VALUES` (``"statute"`` / ``"chapter"`` /
                      ``"section"`` / ``"subsection"``).  For a ``tarkoitetaan``
                      definition it is the scope implied by the nearest preceding
                      definitions-block cue ("Tässä laissa/luvussa/pykälässä/
                      momentissa …" / "Tätä lakia sovellettaessa …"), defaulting
                      to ``"statute"`` when no narrower cue is present.  An
                      act-level alias (parenthetical / ``jäljempänä``) is always
                      ``"statute"`` (document-wide naming convention).
        source_span:  Byte range of the whole binding construct in the source
                      text.
        binding_kind: One of ``parenthetical_alias`` / ``jaljempana`` /
                      ``tarkoitetaan``.
        binding_status: ``"ok"`` when the term's morphology is supported (single
                      word or final-head compound), else
                      ``"unsupported_morphology"`` (target known, inflection not
                      guessed).
    """

    term: str
    target_ref: Optional[str]
    expansion: Optional[str]
    scope: str
    source_span: SourceSpan
    binding_kind: str
    binding_status: str = STATUS_OK


# ---------------------------------------------------------------------------
# Act-cite recognition (self-contained; mirrors eu_reference / plain-text lanes)
# ---------------------------------------------------------------------------
#
# Finnish act id: "(NUMBER/YEAR)".  EU act id: "(FORM) N:o NUMBER/YEAR",
# "(FORM) YEAR/NUMBER", or "NUMBER/YEAR/FORM".  Bounded quantifiers throughout.

# EU "(FORM) N:o NUMBER/YEAR" and "(FORM) YEAR/NUMBER" act-id SHAPES are shared
# with the cross-ref / preparatory waist via
# ``eu_reference.recognize_eu_act_ids(DIALECT_DEFINED_TERMS)`` (this lane's exact
# form set / bounded "\s{0,3}" spacing / case-insensitivity preserved there);
# the positional lowering (cite ending at an offset / first cite in window) stays
# in this lane.
# Finnish "(NUMBER/YEAR)" wrapped act id (closing paren required).
_FI_ID = compile_classifier_regex(r"\((\d{1,6})/(\d{4})\)", classifier_id="fi.references.defined_terms.fi_id")
# Finnish act id followed by a separator (whitespace / comma / paren) — used when
# scanning prose for "the first act mentioned".
_FI_ID_LOOSE = compile_classifier_regex(r"\((\d{1,6})/(\d{4})[\s,)]", classifier_id="fi.references.defined_terms.fi_id_loose")

# A single token of a Finnish term: letters (incl. ä ö å) and internal hyphen.
_TERM_WORD = r"[a-zA-ZäöåÄÖÅ]+(?:-[a-zA-ZäöåÄÖÅ]+)*"

# CELEX number surface (e.g. "32020L0284"): a sector digit, 4-digit year, a
# document-type letter, then a 4-digit running number.  These appear as a
# parenthetical RIGHT AFTER an EU act cite ("(EU) 2020/284 (32020L0284)") and are
# the machine id of the SAME act, never a Finnish defined-term surface.
_CELEX = re.compile(r"^\s*3\d{4}[A-Z]\d{4}\s*$", re.IGNORECASE)

# Inline markup tags (e.g. "<i>rakennetukilaki</i>") that leak into the raw
# statute text; stripped from a captured term surface before classification.
_MARKUP_TAG = re.compile(r"</?[a-zA-Z][a-zA-Z0-9]*\s*/?>")


def _strip_markup(s: str) -> str:
    """Remove inline HTML/markup tags (e.g. ``<i>…</i>``) from a term surface."""
    return _MARKUP_TAG.sub("", s).strip()

# ---------------------------------------------------------------------------
# Shape 1: parenthetical alias right after an act cite
# ---------------------------------------------------------------------------
#
#   "(EY) N:o 1069/2009 (sivutuoteasetus)"
#   "(527/2014) (ympäristönsuojelulaki)"
#
# The alias parenthesis must IMMEDIATELY follow an act cite.  An act cite ends
# either in a ')' (Finnish "(527/2014)" or EU "(EY) …" wrapping) OR in a digit
# (EU "N:o 1069/2009").  We therefore match any "(BODY)" group and verify that an
# act cite terminates in the short window directly before it.  The alias
# parenthesis must NOT itself be an act cite or a "jäljempänä …" group (handled
# by their own shapes).  Bounded alias body (1..80 chars).

_PAREN_ALIAS = compile_classifier_regex(r"\(\s{0,3}([^()]{1,80}?)\s{0,3}\)", classifier_id="fi.references.defined_terms.paren_alias")

# ---------------------------------------------------------------------------
# Shape 2: "jäljempänä X" / 'jäljempänä "X"'
# ---------------------------------------------------------------------------
#
#   "(527/2014, jäljempänä ympäristönsuojelulaki)"
#   '(jäljempänä "sivutuoteasetus")'
#   "… 1069/2009, jäljempänä sivutuoteasetus,"
#
# Anchored on the cue word "jäljempänä".  Captures the term up to a closing
# paren / comma / quote / end-of-window.  Bounded term body.

# A sentence-ending '.' BOUNDS an unquoted alias: the alias is a single naming
# token, never a clause, so it can never span a period into the next sentence.
# (Observed: "…laissa (1137/2016), jäljempänä markkinavalvontalaki. Markkinavalvonnan
# …" must yield ``markkinavalvontalaki``, not ``markkinavalvontalaki. Markkinavalvonnan``.)
# The period is excluded from the term body AND admitted as a terminator so the
# lazy capture stops at it.  A quoted alias is bounded by its closing quote.
_JALJEMPANA = re.compile(
    r"jäljempänä\s{1,3}"
    r"(?P<q>[\"“”])?"
    r"(?P<term>[^\")(,;.]{1,80}?)"
    r"(?(q)[\"“”]|(?=[).,;]|\s*$))",
)

# ---------------------------------------------------------------------------
# Shape 3: "X:llä tarkoitetaan Y" / "X tarkoitetaan Y"
# ---------------------------------------------------------------------------
#
#   "sivutuotteella tarkoitetaan tuotetta …"
#   "sivutuoteasetuksella tarkoitetaan asetusta (EY) N:o 1069/2009 …"
#
# The defined term X precedes "tarkoitetaan", typically in the adessive
# (-llä/-lla).  We capture the single word (or final-head compound run) directly
# before the verb, and the expansion Y is the clause after it up to a sentence
# boundary.  Bounded windows throughout.

# A term-run token that also admits a DANGLING-HYPHEN compound prefix
# ("Lammas-" in "Lammas- ja vuohirekisterillä", an elliptical coordination of
# ``lammasrekisteri`` + ``vuohirekisteri``).  A bare ``_TERM_WORD`` requires a
# letter after every hyphen, so it silently dropped the clipped head; the trailing
# ``-?`` lets the head be captured as part of the coordinated definiendum.  The
# left-boundary trim (`_trim_to_definiendum_np`) still removes any leading
# non-definiendum material, so widening the capture never over-binds.
_TERM_RUN_WORD = r"[a-zA-ZäöåÄÖÅ]+(?:-[a-zA-ZäöåÄÖÅ]+)*-?"

_TARKOITETAAN = re.compile(
    rf"(?P<term>(?:{_TERM_RUN_WORD}\s+){{0,3}}{_TERM_RUN_WORD})\s+tarkoitetaan\b"
    r"(?P<expansion>[^.;]{0,200})",
    re.IGNORECASE,
)
_TARKOITETAAN_LOOKBACK = 512
_TARKOITETAAN_LOOKAHEAD = len("tarkoitetaan") + 200

# Leading scope locatives that may precede the definiendum in an inline shape-3
# capture ("Tässä laissa X:llä tarkoitetaan …") — never part of the term surface.
_SCOPE_LEADERS: frozenset[str] = frozenset(
    {
        "tässä",
        "tätä",
        "laissa",
        "lakia",
        "luvussa",
        "pykälässä",
        "momentissa",
        # Decree / government-decision scope words: a definitions block in an
        # asetus / päätös opens "Tässä asetuksessa/päätöksessä tarkoitetaan",
        # exactly mirroring "Tässä laissa". The scope-word is statute-wide and is
        # never part of the definiendum surface (same as "laissa").
        "asetuksessa",
        "päätöksessä",
    }
)

# ---------------------------------------------------------------------------
# Scope cue for a definitional ``tarkoitetaan`` (shape 3)
# ---------------------------------------------------------------------------
#
# A definitions block declares the lexical reach of its definitions with a
# DEFINITIONS-HEADER cue — a locative that BINDS the definition verb itself:
#
#   "Tässä laissa tarkoitetaan:"        → statute-wide
#   "Tätä lakia sovellettaessa …"       → statute-wide
#   "Tässä luvussa tarkoitetaan:"       → chapter
#   "Tässä pykälässä tarkoitetaan:"     → section
#   "Tässä momentissa tarkoitetaan:"    → subsection
#
# PRECISION DISCIPLINE — the cue must GOVERN THIS DEFINITION, not be stray prose.
# A bare ``Tässä luvussa`` / ``Tässä pykälässä`` is AMBIGUOUS: it appears far more
# often in the REFERENTIAL idiom ``Tässä pykälässä säädetään …`` ("provided for in
# this section"), which says nothing about a definition's scope.  Adopting a
# narrower scope from such stray prose is a false positive (observed on real
# corpus: a ``Tässä pykälässä säädetään`` clause one section above mislabelled a
# later unrelated definition as ``section``).  We therefore require the cue to be
# a TRUE definitions header: ``Tässä <unit>`` IMMEDIATELY governing the definition
# verb ``tarkoitetaan`` — i.e. ``Tässä <unit> [definiendum] tarkoitetaan`` with at
# most a short definiendum run between the locative and the verb, and NO other
# finite verb / sentence break intervening.  ``Tätä lakia sovellettaessa`` is the
# statute-wide application cue (also tied to the definition construct).
#
# Both forms are matched as a single contiguous cue ENDING at ``tarkoitetaan``
# (or the application clause), so a stray ``Tässä luvussa säädetään`` can never
# fire: ``säädetään`` is not ``tarkoitetaan`` and breaks the contiguity.
#
# Bounded look-back window (chars): the cue's ``tarkoitetaan`` is the SAME verb
# the binding sits on (inline ``Tässä <unit> X:llä tarkoitetaan``) OR the block
# header a short distance above the enumerated definiendum.  Kept tight so the
# header cannot leak across an unrelated section.
_SCOPE_CUE_WINDOW = 400

# Closed unit vocabulary mapped to the closed scope vocabulary.
#
# M1-DERIVED (paradigm inversion, not a hand-typed surface table): the
# inessive-singular header units (``laissa`` / ``luvussa`` / ``pykälässä`` /
# ``momentissa`` / ``asetuksessa`` / ``päätöksessä``) and their scope mapping are
# generated from the closed head set + scope assignment in
# ``lemma_gate.definitions_header_unit_scope_map`` — the same M1 template as
# ``chapter_head_alternation``.  This kills the consonant-gradation substring bug
# class (``päätös`` -> ``päätökse-`` is GENERATED, never inferred from a ``päätös``
# substring) and removes the unit table duplicated across ``_SCOPE_CUE_TASSA`` and
# ``_ENUM_HEADER``.  The scope tokens M1 returns are the very ``_SCOPE_*`` strings.
# (A law / decree / government decision all reach the WHOLE instrument, hence the
# same statute scope; chapter/section/subsection are the narrower structural
# units.)
_SCOPE_CUE_UNITS: dict[str, str] = dict(definitions_header_unit_scope_map())
assert set(_SCOPE_CUE_UNITS.values()) <= SCOPE_VALUES, (
    "definitions-header unit scopes escape the closed SCOPE_VALUES vocabulary"
)
# The M1-derived alternation body (longest-first) shared by both header regexes.
_SCOPE_UNIT_ALTERNATION = definitions_header_unit_alternation()

# "Tässä <unit> [up to a short definiendum run] tarkoitetaan" — the cue must lead
# directly into the definition verb, with only a bounded run of
# word/punctuation/enumeration tokens (the definiendum + list marker) between the
# locative and ``tarkoitetaan``, and crucially NO sentence break or other finite
# verb.  We approximate "no intervening sentence/verb" by forbidding '.' and by
# bounding the gap to a short span of letters, digits, ':', ')', commas and
# spaces only (the shapes seen in "Tässä luvussa tarkoitetaan:" and "Tässä
# pykälässä X:llä tarkoitetaan").
# ``asetuksessa`` (decree) and ``päätöksessä`` (government decision) are admitted
# alongside ``laissa`` because an asetus / päätös opens its definitions block with
# the identical header ("Tässä asetuksessa/päätöksessä tarkoitetaan …", scope =
# the whole instrument).  They inherit the SAME ambiguity guard: the cue is
# matched only when it leads CONTIGUOUSLY into ``tarkoitetaan``, so the far more
# common referential idiom "Tässä asetuksessa/päätöksessä säädetään/määrätään …"
# ("provided for / prescribed in this decree") can never fire (``säädetään`` /
# ``määrätään`` is not ``tarkoitetaan`` and breaks the contiguity).
_SCOPE_CUE_TASSA = re.compile(
    rf"\bTässä\s{{1,3}}(?P<unit>{_SCOPE_UNIT_ALTERNATION})\b"
    r"(?:[A-Za-zäöåÄÖÅ0-9:)\s,–-]{0,40})?"
    r"tarkoitetaan\b",
    re.IGNORECASE,
)
# "Tätä lakia sovellettaessa" — statute-wide application cue.
_SCOPE_CUE_SOVELLETTAESSA = compile_classifier_regex(r"\bTätä\s{1,3}lakia\s{1,3}sovellettaessa\b", re.IGNORECASE, classifier_id="fi.references.defined_terms.scope_cue_sovellettaessa")
_GUARD_TASSA = "tässä"
_GUARD_SOVELLETTAESSA = "sovellettaessa"

# ---------------------------------------------------------------------------
# Shape 3b: ENUMERATED definitions block governed by a header cue
# ---------------------------------------------------------------------------
#
#   "Tässä laissa tarkoitetaan:
#       sivutuotteella kuollutta eläintä;
#       jätteellä ainetta …;"
#
# This is the CANONICAL Finnish definitions block: the header
# ``Tässä <unit> tarkoitetaan:`` declares the scope (statute / chapter / section /
# subsection), then a SEMICOLON-SEPARATED list of items, each opening with the
# definiendum in the ADESSIVE followed by its expansion:
# ``<definiendum-adessive> <expansion>;``.  (The list enumerator ``N)`` lives in
# its own markup node and is NOT present in the paragraph text the binder sees,
# so items are delimited by the header ``:`` and the item-terminating ``;``.)
# Unlike inline shape 3 the definiendum follows the verb, so the per-item
# ``X:llä`` is the binding site.  Each item inherits the header's scope from its
# unit; statute by default.  CONSERVATIVE: anchored on the header, each item's
# leading word must be a genuine adessive definiendum
# (``_is_definitional_definiendum``) — a list item that does not open with an
# adessive definiendum binds nothing (no fabrication).

# The block header that opens an enumerated definitions list.  The unit decides
# the scope of EVERY item in the block.
# Decree / decision units (``asetuksessa`` / ``päätöksessä``) are admitted here
# too: the dominant decree definitions block is the enumerated form
# "Tässä asetuksessa tarkoitetaan:\n<definiendum-adessive> <expansion>;".  The
# ``tarkoitetaan\s{0,3}:`` tail is the ambiguity guard — a referential
# "Tässä asetuksessa säädetään …" lacks it and never opens a block.
#
# A SECOND header arm covers the APPLICATION-cue enumerated block:
# "… sovellettaessa tarkoitetaan:" — e.g. "Tätä lakia sovellettaessa
# tarkoitetaan:", "Valvontalakia ja tätä asetusta sovellettaessa tarkoitetaan:",
# "… säännöksiä ja määräyksiä sovellettaessa tarkoitetaan:".  This is the SAME
# statute-wide application cue already recognised inline by
# ``_SCOPE_CUE_SOVELLETTAESSA`` (scope = statute), but it also OPENS an enumerated
# definitions block whose items were previously dropped by BOTH lanes because the
# ``Tässä <unit>`` arm did not match.  The arm anchors on the cue word
# ``sovellettaessa`` immediately leading into ``tarkoitetaan:`` (the same ambiguity
# guard as the ``Tässä <unit>`` arm), so a referential "… sovellettaessa
# noudatetaan …" never fires.  ``unit`` is absent on this arm; the caller maps an
# absent unit to the statute scope (an application cue reaches the whole
# instrument, identical to ``Tätä lakia sovellettaessa``).
_ENUM_HEADER = re.compile(
    r"(?:"
    rf"\bTässä\s{{1,3}}(?P<unit>{_SCOPE_UNIT_ALTERNATION})\s{{1,3}}"
    r"tarkoitetaan\s{0,3}:"
    r"|"
    r"\bsovellettaessa\s{1,3}tarkoitetaan\s{0,3}:"
    r")",
    re.IGNORECASE,
)


def _enum_header_scope(unit: str | None) -> str:
    """Scope of an enumerated-block header.

    ``unit`` is the captured ``Tässä <unit>`` locative (mapped through the closed
    :data:`_SCOPE_CUE_UNITS` vocabulary) or ``None`` for the application-cue arm
    (``… sovellettaessa tarkoitetaan:``), which is statute-wide (the whole
    instrument, identical to ``Tätä lakia sovellettaessa``).
    """
    if unit is None:
        return _SCOPE_STATUTE
    return _SCOPE_CUE_UNITS[unit.lower()]
# A single list item inside the block: a delimiter ('``:``' opening the list or a
# preceding item's terminating '``;``'), optional whitespace / stripped
# enumerator, then the leading definiendum word, then the expansion up to the next
# '``;``' (item end).  Bounded throughout (AGENTS.md §1.11).
# A bounded leading run of word-tokens at the start of an enumerated item
# (e.g. "palkansaajaan rinnastettavalla yrittäjällä luonnollista henkilöä"),
# followed by the remaining expansion.  The definiendum phrase is the LEADING run
# of words ENDING at the LAST adessive-marked token (the head); the recognizer
# splits the run at that head, so the multi-word definiendum is preserved as a
# full surface and the expansion starts after it.  Bounded run length.
_ENUM_ITEM = re.compile(
    r"[:;]\s{0,80}"
    r"(?:\d{1,3}[a-z]?\)\s{0,5})?"
    rf"(?P<run>{_TERM_WORD}(?:\s+{_TERM_WORD}){{0,8}})\s+"
    r"(?P<rest>[^;]{0,400})",
    re.IGNORECASE,
)


def _adessive_phrase_from_run(words: list[str]) -> Optional[list[str]]:
    """Return the leading definiendum phrase from a word run, or ``None``.

    The definiendum is the LEADING run of words up to and INCLUDING the last
    adessive-marked token within the (bounded) prefix that is a genuine
    definiendum head.  We scan the prefix: the head is the FIRST adessive token
    that is a genuine definiendum (``_is_definitional_definiendum``); we extend
    through any further consecutive adessive tokens (agreeing modifier + head,
    e.g. ``rinnastettavalla yrittäjällä``).  Words after the last adessive token
    belong to the expansion.  ``None`` when no adessive definiendum head is found
    (item is not a definition — no fabrication).
    """
    last_head_idx = -1
    for i, w in enumerate(words):
        low = w.lower()
        if low.endswith("lla") or low.endswith("llä"):
            if _is_definitional_definiendum(w):
                last_head_idx = i
        elif last_head_idx >= 0:
            # First non-adessive word after we have at least one adessive head:
            # the definiendum phrase ends at the last adessive token.
            break
    if last_head_idx < 0:
        return None
    return words[: last_head_idx + 1]
# How far past a header an enumerated block may extend before we stop scanning
# items (a definitions block is long but bounded; this caps the scan window).
#
# This is a SAFETY CAP, not the list terminator: the real end of the definitions
# block is the first OPERATIVE-PROSE boundary (``_gap_has_operative_prose`` below),
# reached well before this cap in every observed block. The cap only bounds the
# regex scan on pathological input (AGENTS.md §1.11).
_ENUM_BLOCK_WINDOW = 12000

# ---------------------------------------------------------------------------
# Structural end-of-list terminator for an enumerated definitions block
# ---------------------------------------------------------------------------
#
# An enumerated definitions block is a CONTIGUOUS run of ``;``-terminated items
# directly under its ``Tässä <unit> tarkoitetaan:`` header. The block has NO
# explicit closing marker, so a fixed byte window (the prior 12 KB span) swept in
# UNRELATED operative ``;``-lists that appear later in the same section — an
# operative ``… on esitettävä:\n<item>;\n…`` / ``… edellytyksenä on, että:\n…;``
# list whose items happen to open with an adessive-headed phrase was minted as a
# phantom definition (the F1 over-capture).
#
# The structural list-end is the first OPERATIVE-PROSE paragraph: the definitions
# list is item-after-item (each ``<definiendum-adessive> <definiens>;``), with the
# only intervening text being a new definiendum line or an item's own definiens.
# An OPERATIVE PROVISION is a free-standing full sentence — a substantial run of
# words ending in a sentence period — that is neither a list item nor a definiendum
# line. Once such a provision sentence appears BETWEEN two candidate items, the
# contiguous definition list has ended and every later ``;``-item belongs to an
# operative list, not the definitions block.
#
# decode_body_text joins each ``<p>`` element by '\n', so a '\n' is a paragraph
# (``<p>``) boundary; a genuine definiens may itself contain internal '. ' (a
# two-sentence definiens) but stays within ONE ``;``-terminated item, so the test
# is applied to the GAP BETWEEN accepted items (the text from the previous item's
# end to the next candidate's start), not to an item's own body. Embedded
# colon-introduced sub-lists inside a single definiens (e.g. a formula with
# ``…; …;`` parts) are kept because the sub-list items end in ';' / are short, not
# in a substantial sentence period.

#: An operative provision sentence: a run of letters/spaces/commas of at least
#: this many chars ending in a sentence period. Tuned so a genuine short definiens
#: fragment / formula line / sub-list item never trips it, but a real provision
#: sentence ("Sen lisäksi, mitä 6 luvussa säädetään, …", "Tuen myöntämisen
#: edellytyksenä on, että:") does.
_OPERATIVE_SENTENCE_MIN_CHARS = 45
_OPERATIVE_SENTENCE_MIN_WORDS = 6

# A segment delimiter splitting a between-items gap into its candidate sentences:
# the ';' item terminator and the '\n' paragraph (``<p>``) boundary.
_GAP_SEGMENT_SPLIT = re.compile(r"[;\n]")


def _gap_has_operative_prose(gap_text: str) -> bool:
    """True iff the text BETWEEN two enumerated items contains an operative
    provision sentence — i.e. the contiguous definitions list has ended.

    The gap between two genuine consecutive definition items is only list
    structure (whitespace, a new definiendum line, the prior item's ';'). An
    OPERATIVE PROVISION that intervenes is a free-standing full sentence: a
    substantial word run ending in a sentence period. Split the gap on the ';'
    item terminator and '\\n' paragraph boundary and look for any such sentence.

    Conservative by construction: a short definiens fragment, a formula line, or a
    ';'-terminated sub-list item is below the length/word floor and never trips
    this, so a genuine multi-item list (including one with an embedded sub-list in
    a definiens) is never truncated.
    """
    for seg in _GAP_SEGMENT_SPLIT.split(gap_text):
        s = seg.strip()
        if not s or s[-1] != ".":
            continue
        if len(s) >= _OPERATIVE_SENTENCE_MIN_CHARS and (
            len(s.split()) >= _OPERATIVE_SENTENCE_MIN_WORDS
        ):
            return True
    return False


def _scope_cue_before(text: str, pos: int) -> str:
    """Return the scope of the NEAREST recognised definitions-header cue governing
    a binding whose definiendum ends at ``pos``, defaulting to ``statute``.

    The cue must be a TRUE definitions header — ``Tässä <unit> … tarkoitetaan``
    with the locative directly governing the definition verb (or ``Tätä lakia
    sovellettaessa``) — never a bare ``Tässä <unit>`` that may belong to the
    referential ``… säädetään`` idiom.  The cue closest to the definiendum (its
    ``tarkoitetaan`` end nearest ``pos``) wins.  Fail-safe: no recognised header
    cue → the conservative ``statute`` default (prior behaviour, never a
    regression).
    """
    start = max(0, pos - _SCOPE_CUE_WINDOW)
    chunk = text[start:pos]
    low = chunk.lower()
    best_offset = -1
    best_scope = _SCOPE_STATUTE
    if _GUARD_TASSA in low:
        for m in _SCOPE_CUE_TASSA.finditer(chunk):
            # Rank by where the cue's verb ENDS (closest-governing wins).
            if m.end() > best_offset:
                best_offset = m.end()
                best_scope = _SCOPE_CUE_UNITS[m.group("unit").lower()]
    if _GUARD_SOVELLETTAESSA in low:
        for m in _SCOPE_CUE_SOVELLETTAESSA.finditer(chunk):
            if m.end() > best_offset:
                best_offset = m.end()
                best_scope = _SCOPE_STATUTE
    return best_scope

# ---------------------------------------------------------------------------
# Definitional vs REFERENTIAL ``tarkoitetaan``
# ---------------------------------------------------------------------------
#
# Finnish ``tarkoitetaan`` is used in TWO unrelated idioms:
#
#   * DEFINITIONAL — ``X:llä tarkoitetaan Y`` ("by X is meant Y"): the definiendum
#     X immediately precedes the verb in the ADESSIVE (``-lla`` / ``-llä``).  This
#     is the only shape that introduces a defined term.
#   * REFERENTIAL  — ``…, jota / jossa N momentissa / N luvun M §:ssä
#     tarkoitetaan`` ("… which is referred to in subsection N / § M of chapter N").
#     Here the word before the verb is a relative pronoun (``jota`` / ``jolla`` /
#     ``jossa`` …) or a structural-unit cross-reference in the inessive
#     (``momentissa`` / ``luvussa`` / ``kohdassa`` / ``§:ssä`` / ``laissa`` /
#     ``asetuksessa`` / ``direktiivissä`` / ``artiklassa``).  It introduces NOTHING;
#     it points at an existing provision.
#
# The referential idiom is what flooded the definition lints: it bound truncated
# stems (``ssä``, ``jo``, ``ede``), function words (``jota`` / ``joita`` /
# ``säädetään``) and scope locatives (``momentissa`` / ``luvussa`` / ``laissa``)
# as if they were defined terms, then every later occurrence of those ubiquitous
# tokens fired USED_BEFORE_DEFINITION.  We therefore recognise a binding ONLY for
# the definitional (adessive) shape, and never for a relative pronoun in the
# adessive (``jolla`` / ``millä`` / ``sillä`` / ``tällä`` …).

# Adessive surface forms of the closed pronoun / referential-adverb class:
# grammatically adessive (``-lla`` / ``-llä``) but never a definiendum (``jolla``
# = "by which", ``edellä`` = "above").  Finnish pronouns are a small CLOSED and
# IRREGULAR class, so we match the FULL surface form by exact equality — no
# suffix-stripping and no consonant-gradation guessing.  (Reverse morphological
# analysis ``jolla`` → ``joka`` is unavailable: M1 morphology is generation-only.
# Generating these via M1 would buy nothing — the class is closed, so there is
# nothing to generalise over — and is unreliable on irregular pronoun paradigms.)
_PRONOUN_ADESSIVE_FORMS: frozenset[str] = frozenset(
    {
        "jolla",    # joka
        "joilla",   # joka (plural)
        "millä",    # mikä
        "sillä",    # se
        "tällä",    # tämä
        "niillä",   # ne
        "näillä",   # nämä
        "noilla",   # nuo
        "kaikilla",  # kaikki
        "edellä",   # edellä — adverb "above" (referential)
    }
)

# Substring guards (AGENTS.md §1.11)
_GUARD_PAREN = "("
_GUARD_JALJEMPANA = "jäljempänä"
_GUARD_TARKOITETAAN = "tarkoitetaan"


# ---------------------------------------------------------------------------
# Act-cite helpers
# ---------------------------------------------------------------------------


def _act_id_ending_before(text: str, pos: int, window: int = 90) -> Optional[str]:
    """Return the act id whose cite ENDS at ``pos`` (exclusive), if any.

    ``pos`` is the index in ``text`` immediately AFTER the last character of a
    candidate act cite (e.g. the index of the whitespace/'(' that follows an EU
    "N:o 1069/2009", or one past a Finnish "(527/2014)" ')').  We look back a
    bounded window for an act-cite pattern terminating exactly at ``pos``.

    Canonical id surface returned:
      * EU acts → "NUMBER/YEAR" / "YEAR/NUMBER" (form prefix dropped; the digits
        identify the act, oriented as written in the source).
      * FI acts → CANONICAL "YEAR/NUMBER" (via :func:`cross_refs._make_statute_id`,
        the single orientation authority shared with the ``<ref>`` / cross-ref
        lane and the corpus store keys). The Finnish VISIBLE convention is
        ``(NUMBER/YEAR)``; the id this returns is the canonical target id, not the
        visible surface.
    Returns ``None`` if no act cite terminates at ``pos``.
    """
    start = max(0, pos - window)
    chunk = text[start:pos]
    n = len(chunk)
    # EU paren act id ("(FORM) N:o NUMBER/YEAR" / "(FORM) YEAR/NUMBER") ending at
    # pos. recognize_eu_act_ids returns N:o-form spans before year-first spans, so
    # taking the LAST span that ends at n preserves the prior NNUM-then-YEARFIRST
    # precedence (the two shapes never both end at the same offset).
    eu_at_end = [
        s for s in recognize_eu_act_ids(chunk, dialect=DIALECT_DEFINED_TERMS)
        if s.end == n
    ]
    if eu_at_end:
        return eu_at_end[-1].id_surface
    # Finnish "(NUMBER/YEAR)" whose closing ')' ends at pos. The id is
    # CANONICALIZED to "YEAR/NUMBER" — group(1) is NUMBER, group(2) is YEAR.
    fi = None
    for m in _FI_ID.finditer(chunk):
        if m.end() == n:
            fi = m
    if fi is not None:
        return _make_statute_id(fi.group(2), fi.group(1))
    return None


def _first_act_id_in(text: str) -> Optional[str]:
    """Return the FIRST act id appearing in ``text`` (EU preferred by position).

    Used to resolve the act a binding construct refers to when the act cite
    precedes the cue inside a bounded window. EU ids keep their source
    orientation; FI ids are CANONICALIZED to "YEAR/NUMBER" via
    :func:`cross_refs._make_statute_id` (the one orientation authority shared with
    the ``<ref>`` / cross-ref lane), so the same act never splits into an inverted
    "NUMBER/YEAR" entity.
    """
    candidates: list[tuple[int, str]] = []
    # All EU paren act ids (both shapes). The function returns the candidate with
    # the smallest start, so adding every EU span (not just the first of each
    # shape, as the prior per-pattern .search() did) yields the identical winner:
    # the earliest EU span is min(first N:o-form, first year-first), exactly what
    # the old two-candidate min selected.
    for s in recognize_eu_act_ids(text, dialect=DIALECT_DEFINED_TERMS):
        candidates.append((s.start, s.id_surface))
    for fm in _FI_ID_LOOSE.finditer(text):
        # group(1) is NUMBER, group(2) is YEAR → canonical "YEAR/NUMBER".
        candidates.append((fm.start(), _make_statute_id(fm.group(2), fm.group(1))))
    if not candidates:
        return None
    candidates.sort(key=lambda c: c[0])
    return candidates[0][1]


def _act_id_in_expansion(text: str) -> Optional[str]:
    """Return an act id if the expansion text Y is (or contains) an act cite."""
    return _first_act_id_in(text)


# ---------------------------------------------------------------------------
# Morphology boundary classification
# ---------------------------------------------------------------------------

_SIMPLE_STEM = re.compile(rf"^{_TERM_WORD}$")


def _classify_term_morphology(term: str) -> str:
    """Classify whether the bound term's morphology is supported.

    Supported (``STATUS_OK``):
      * a single word, OR
      * a final-head compound: every token but the last is a simple invariant
        lowercase stem and the last token is a simple word (the inflectable
        head).

    Unsupported (``STATUS_UNSUPPORTED_MORPHOLOGY``):
      * quotes/commas/parens embedded,
      * any non-final token that looks like it carries a case/agreement marker
        (we approximate: a non-final token ending in a long case suffix),
      * empty.
    """
    t = term.strip()
    if not t:
        return STATUS_UNSUPPORTED_MORPHOLOGY
    if any(ch in t for ch in '"(),;“”'):
        return STATUS_UNSUPPORTED_MORPHOLOGY

    tokens = t.split()
    if len(tokens) == 1:
        return STATUS_OK if _SIMPLE_STEM.match(tokens[0]) else STATUS_UNSUPPORTED_MORPHOLOGY

    # Multi-token: final-head compound only if every NON-final token is a simple
    # invariant stem AND does not itself look inflected (agreeing modifier).
    # Approximation of "agreeing modifier": a non-final token carrying a typical
    # case-ending cluster (e.g. -ssa/-sta/-lla/-lta/-een/-iin/-jen/-ien/-ista).
    case_marker = re.compile(
        r"(ss[aä]|st[aä]|ll[aä]|lt[aä]|lle|"
        r"[aä]n|een|iin|jen|ien|ist[aä])$"
    )
    head = tokens[-1]
    if not _SIMPLE_STEM.match(head):
        return STATUS_UNSUPPORTED_MORPHOLOGY
    for tok in tokens[:-1]:
        if not _SIMPLE_STEM.match(tok):
            return STATUS_UNSUPPORTED_MORPHOLOGY
        if case_marker.search(tok.lower()):
            # An agreeing/inflected modifier → complex NP, not a final-head
            # compound; refuse to guess inflection.
            return STATUS_UNSUPPORTED_MORPHOLOGY
    return STATUS_OK


def _is_definitional_definiendum(last_word: str) -> bool:
    """True iff ``last_word`` (the token directly before ``tarkoitetaan``) is a
    genuine DEFINITIONAL definiendum, not the REFERENTIAL idiom.

    The definitional idiom is ``X:llä tarkoitetaan Y`` — the definiendum is in the
    ADESSIVE (``-lla`` / ``-llä``).  We accept ONLY adessive definienda and reject:

      * non-adessive words (``momentissa`` / ``luvussa`` / ``§:ssä`` / ``laissa``
        are inessive cross-references — "referred to IN …", never definienda);
      * relative / demonstrative pronouns even in the adessive (``jolla`` /
        ``millä`` / ``sillä`` / ``tällä`` = "by which / by that" — referential).

    This is the conservative discriminator that keeps every real definition
    (``Vesialueella`` / ``autolla`` / ``sivutuotteella`` …) while refusing the
    referential idiom that bound function words, truncated stems and scope
    locatives as phantom defined terms.
    """
    low = last_word.lower()
    if not (low.endswith("lla") or low.endswith("llä")):
        return False
    # Closed pronoun / referential-adverb class: matched as full adessive surface
    # forms (exact equality), never by prefix/stem approximation.
    if low in _PRONOUN_ADESSIVE_FORMS:
        return False
    # A bare adessive too short to carry a content stem before the 3-char suffix
    # (e.g. a mangled token) is not a definiendum.
    if len(low) < 5:
        return False
    # The CROSS-REFERENCE idiom ``N §:ssä/laissa tarkoitetulla tavalla`` ("in the
    # manner referred to in § N") puts the adessive PARTICIPLE ``tarkoitetulla``
    # (and its paradigm ``tarkoitetuilla`` / ``tarkoitetussa``) right before a head
    # noun.  That participle is itself the reference verb, never a definiendum — it
    # points at an existing provision.
    if low.startswith("tarkoitet"):
        return False
    return True


# Postpositions / cross-reference connectives that, appearing ANYWHERE in a
# captured run, mark it as a referential clause that spilled across a boundary
# ("… N §:n MUKAAN katsota …", "… ja niiden NOJALLA annettujen säännösten …", "…
# olevien alusten OSALTA …"). These never occur inside a genuine defined-term NP.
# NOTE: plain coordinators (``ja`` / ``tai``) are DELIBERATELY excluded — Finnish
# definitions routinely coordinate two definienda ("Korkomenolla ja korkotulolla
# tarkoitetaan …", "Pintaverkolla ja pintaverkkopyydyksellä tarkoitetaan …"), so a
# coordinator is NOT evidence of a swept clause.  Likewise adessive nouns such as
# ``avulla`` ("Henkilökohtaisella avulla") / ``perusteella`` are genuine heads in
# context and are NOT rejected as heads — only their use as a CONNECTIVE inside a
# longer swept run (after a genitive) is caught below by ``_BARE_CASE_SUFFIX`` /
# the verb list.  Matched by exact lowercase token equality (closed set).
_CLAUSE_BOUNDARY_TOKENS: frozenset[str] = frozenset(
    {
        # Cross-reference postpositions governing a genitive ("§:n mukaan",
        # "niiden nojalla", "säännösten perusteella", "alusten osalta").
        "mukaan", "nojalla", "perusteella", "osalta", "mukaisesti",
        # Relative / interrogative pronouns (the referential ", jota … tarkoitetaan"
        # spill): nominative / partitive / genitive / inessive surfaces.
        "joka", "jota", "jonka", "jossa", "joita", "joiden", "mikä",
        # Bare infinitive / finite verbs observed in swept referential clauses.
        "katsota", "säädetään",
    }
)

# A token that is ONLY a Finnish case suffix (no content stem before it) — the
# tell-tale debris of a split ``§:ssä`` / ``EU:n`` / ``X:llä`` where the colon and
# the stem were lost to tokenization, leaving a bare suffix fragment as the first
# "word" of a swept run ("ssä tarkoitetulla …", "n geenivara-asetuksen …"). A
# genuine definiendum's first token is a content word, never a bare suffix.
_BARE_CASE_SUFFIX: frozenset[str] = frozenset(
    {
        "n", "ssa", "ssä", "sta", "stä", "lla", "llä", "lta", "ltä", "lle",
        "ksi", "na", "nä", "ta", "tä", "a", "ä", "en", "in", "tta", "ttä",
        "han", "hän", "kin", "kaan", "kään",
    }
)


def _is_clean_definiendum_phrase(words: list[str]) -> bool:
    """True iff the captured ``words`` run is a clean defined-term NP, not a swept
    clause fragment.

    A genuine multi-word definiendum is a short noun phrase (optional agreeing
    modifiers + an adessive head): ``palkansaajaan rinnastettavalla yrittäjällä``,
    ``Palkkatuella katettavilla palkkakustannuksilla``. It

      * begins with a CONTENT word (never a bare case-suffix fragment left by a
        split ``§:ssä`` / ``EU:n``), and
      * contains NO clause-boundary token (cross-reference postposition /
        relative pronoun / finite-or-infinitive verb) — i.e. does not span a
        clause boundary.  Plain coordinators (``ja`` / ``tai``) are allowed: a
        coordinated definiendum (``Korkomenolla ja korkotulolla``) is genuine.

    A run failing either test is the cross-reference idiom or a clause fragment
    swept by a stray ``:`` / ``;`` delimiter (``ssä tarkoitetulla``, ``n mukaan
    katsota kilpailluilla markkinoilla``, ``n geenivara-asetuksen sekä niiden
    nojalla``); the recognizer DECLINES it (no garbled term minted, no
    fabrication), per the tag-don't-guess discipline.
    """
    if not words:
        return False
    # (a) A bare case-suffix fragment as the leading token = split-token debris.
    if words[0].lower() in _BARE_CASE_SUFFIX:
        return False
    # (b) Any clause-boundary token anywhere in the run = the run spilled across a
    #     clause boundary (cross-reference postposition / relative pronoun / verb).
    for w in words:
        if w.lower() in _CLAUSE_BOUNDARY_TOKENS:
            return False
    return True


# ---------------------------------------------------------------------------
# Definiendum LEFT-boundary trim (adverbial / connector strip)
# ---------------------------------------------------------------------------
#
# A captured run may carry NON-definiendum material on its LEFT edge that the
# regex window swept in:
#
#   * a PRIOR-ENTRY trailing connector — a leading ``ja`` / ``tai`` / ``sekä`` that
#     terminated the PREVIOUS list item, not part of this definiendum
#     ("…komissiota; sekä [\n] tavaralla …" → term is ``tavaralla``, not
#     ``sekä tavaralla``); and
#   * a leading ADVERBIAL CLAUSE — an adverb / pro-form / gerund / temporal
#     infinitive that modifies the definition sentence, not the definiendum NP
#     ("… rajausmahdollisuus huomioon ottaen kasvulohkolla …" → ``kasvulohkolla``;
#     "Tällöin maatilan yritystoiminnan tulolla …" → ``maatilan yritystoiminnan
#     tulolla``).
#
# CRITICAL — this MUST NOT eat a legitimate COORDINATED head.  A coordinator that
# sits BETWEEN two definienda ("Pienellä ja keskisuurella yrityksellä",
# "Lammas- ja vuohirekisterillä") is INSIDE the term and is kept; only a coordinator
# that LEADS the whole run (a prior-entry connector) is stripped.  The adverbial cut
# is anchored on a CLOSED set of clause markers (exact lowercase equality) plus the
# unambiguous temporal-infinitive suffix ``-ttaessa`` / ``-ttäessä`` (a finite
# subordinate clause, never a noun case), so a genitive modifier ending in ``-en``
# ("Rakennuksen", "Maataloustuotteiden") is NEVER misread as a gerund.

#: Plain coordinators.  A LEADING one is a prior-entry connector and is stripped;
#: a MEDIAL one (between two definienda) is genuine coordination and is kept.
_LEADING_COORDINATORS: frozenset[str] = frozenset({"ja", "tai", "sekä", "taikka"})

#: Closed adverb / pro-form / gerund markers that open an adverbial clause and are
#: never part of a definiendum noun phrase.  Matched by exact lowercase equality
#: (no suffix guessing) — the only morphological rule admitted is the temporal
#: second-infinitive inessive (``-ttaessa`` / ``-ttäessä``) handled separately.
_ADVERBIAL_CLAUSE_MARKERS: frozenset[str] = frozenset(
    {
        # leading adverbs / pro-forms
        "tällöin", "jolloin", "muuten", "muutoin", "kuitenkin", "kuitenkaan",
        "siten", "näin", "lisäksi", "mitä",
        # "huomioon ottaen" idiom (illative + gerund) and bare -en gerunds
        "huomioon", "ottaen", "lukien", "noudattaen", "ottamatta", "lukematta",
    }
)

#: Temporal second-infinitive inessive suffix ("sovellettaessa", "laskettaessa"):
#: a subordinate-clause verb, never a noun case.  Distinct from any genitive (a
#: genitive never ends ``-ttaessa`` / ``-ttäessä``), so this is safe to strip.
_TEMPORAL_INFINITIVE = re.compile(r"(ttaessa|ttäessä)$", re.IGNORECASE)


def _trim_to_definiendum_np(words: list[str]) -> Optional[list[str]]:
    """Strip leading non-definiendum material (prior-entry connector / adverbial
    clause) from a captured definiendum run, preserving genuine coordination.

    Returns the trimmed definiendum word list, or ``None`` if nothing survives.
    The HEAD (last word) is never touched; only the LEFT edge is trimmed:

      1. drop leading plain coordinators (``ja`` / ``tai`` / ``sekä``) — a
         prior-entry connector;
      2. cut everything up to and including the LAST adverbial-clause marker
         (closed set / temporal infinitive), so a leading adverbial clause is
         removed but a medial coordinator between two definienda is kept;
      3. drop any coordinator the cut newly exposed at the left edge.
    """
    ws = list(words)
    while ws and ws[0].lower() in _LEADING_COORDINATORS:
        ws = ws[1:]
    if not ws:
        return None
    # Find the LAST adverbial-clause marker in the prefix (never the head itself);
    # everything up to and including it is the adverbial clause, not the term.
    cut = -1
    for i, w in enumerate(ws[:-1]):
        low = w.lower()
        if low in _ADVERBIAL_CLAUSE_MARKERS or _TEMPORAL_INFINITIVE.search(low):
            cut = i
    if cut >= 0:
        ws = ws[cut + 1 :]
    while ws and ws[0].lower() in _LEADING_COORDINATORS:
        ws = ws[1:]
    return ws or None


# ---------------------------------------------------------------------------
# Recognizers
# ---------------------------------------------------------------------------


#: Manner-adverb / conjunction leaders of the ``jäljempänä`` ADVERBIAL idiom
#: ("…, jollei jäljempänä TOISIN säädetä" = "as hereinafter otherwise provided").
#: A genuine alias is a NOUN PHRASE; these open a clause, never an alias. Closed
#: set, matched by exact lowercase equality.
_JALJEMPANA_IDIOM_LEADERS: frozenset[str] = frozenset(
    {"toisin", "muuten", "muutoin", "erikseen", "toisaalla"}
)

#: Finite/infinitive verb surfaces seen as the HEAD (last word) or the LEADING
#: word of the ``jäljempänä`` adverbial idiom ("jäljempänä toisin SÄÄDETÄ",
#: "jäljempänä SÄÄDETÄÄN", "jäljempänä SANOTAAN … :ksi", "jäljempänä tässä luvussa
#: SÄÄDETÄÄN"). A genuine alias surface is a noun phrase whose head is a noun, so a
#: verb at either edge marks the clause idiom, not a binding. Closed set, exact
#: lowercase equality (the productive Finnish verb morphology is NOT generated —
#: this is the observed idiom vocabulary, not a paradigm).
_JALJEMPANA_VERB_TOKENS: frozenset[str] = frozenset(
    {
        "säädetä", "säädetään", "säädetty", "säädettyä",
        "määrätä", "määrätään", "määrätty",
        "poiketa", "poiketen",
        "tarkoitetaan", "sanotaan", "todetaan", "mainitaan",
        "luetellut", "lueteltu", "tarkoitettu", "tarkoitettua",
    }
)


def _is_jaljempana_verb_idiom(term: str) -> bool:
    """True iff a ``jäljempänä`` term is the ADVERBIAL/clause idiom, not an alias.

    A genuine ``jäljempänä`` alias is a NOUN PHRASE (single noun or a final-head
    compound / modifier+noun whose head is a noun): ``ympäristönsuojelulaki``,
    ``yleinen tietosuoja-asetus``, ``Schengenin rajasäännöstö``. The false mint is
    the adverbial idiom ``jäljempänä toisin säädetä`` ("as hereinafter otherwise
    provided") / ``jäljempänä säädetään`` / ``jäljempänä sanotaan … :ksi``, whose
    captured surface is an adverb/verb clause fragment, NOT an NP. We reject when
    the LEADING word is a manner-adverb idiom leader or a finite verb, or when the
    HEAD (last word) is a verb form. Both are closed-set / exact-surface tests
    (tag-don't-guess: no productive verb-morphology inference).
    """
    words = term.split()
    if not words:
        return False
    lead = words[0].lower()
    if lead in _JALJEMPANA_IDIOM_LEADERS or lead in _JALJEMPANA_VERB_TOKENS:
        return True
    if words[-1].lower() in _JALJEMPANA_VERB_TOKENS:
        return True
    return False


def _recognize_jaljempana(text: str, source_file: str) -> list[DefinedTermBinding]:
    out: list[DefinedTermBinding] = []
    for m in _JALJEMPANA.finditer(text):
        # Strip inline markup (e.g. ``jäljempänä <i>rakennetukilaki</i>``) so the
        # term surface is the bare Finnish word, not an HTML-wrapped fragment.
        raw_term = _strip_markup(m.group("term"))
        if not raw_term:
            continue
        # A CELEX number ("32020L0284") is the machine id of the cited act, not a
        # Finnish alias surface — never mint a binding for it.
        if _CELEX.match(raw_term):
            continue
        # The ADVERBIAL idiom ``jäljempänä toisin säädetä`` / ``jäljempänä
        # säädetään`` / ``jäljempänä sanotaan … :ksi`` is a clause, not an alias:
        # its captured surface is an adverb/verb fragment, not a noun phrase. An act
        # cite that happens to sit in the 90-char look-back (e.g. "… (688/2001)
        # säädetään, jollei jäljempänä toisin säädetä") would otherwise satisfy the
        # act-cite guard and bind the verb phrase to the unrelated act (F2). Decline
        # it (no fabrication) — never bind a verb-idiom fragment as a term.
        if _is_jaljempana_verb_idiom(raw_term):
            continue
        # The act this alias refers to is the act cite preceding the cue inside a
        # bounded look-back window (typ. same parenthetical group).
        lb_start = max(0, m.start() - 90)
        target = _first_act_id_in(text[lb_start : m.start()])
        quoted = m.group("q") is not None
        # An ALIAS binding introduces a short name for a CITED act, so the act
        # cite must be present (or the alias must be explicitly quoted).  Without
        # either, ``jäljempänä X`` is the adverbial "hereinafter provided" idiom
        # (``jäljempänä säädetään`` / ``jäljempänä on säädetty``) — referential,
        # not a binding — and must not bind a verb/clause as a phantom term.
        if target is None and not quoted:
            continue
        status = _classify_term_morphology(raw_term)
        out.append(
            DefinedTermBinding(
                term=raw_term,
                target_ref=target,
                expansion=None,
                scope=_SCOPE_STATUTE,
                source_span=SourceSpan(source_file, m.start(), m.end() - m.start()),
                binding_kind=BINDING_JALJEMPANA,
                binding_status=status,
            )
        )
    return out


def _recognize_parenthetical_alias(
    text: str, source_file: str, claimed: set[int]
) -> list[DefinedTermBinding]:
    """Parenthetical alias immediately after an act cite.

    ``claimed`` holds start offsets of parens already consumed by the jäljempänä
    shape, so ``(jäljempänä "X")`` is not double-bound as a bare alias.
    """
    out: list[DefinedTermBinding] = []
    for m in _PAREN_ALIAS.finditer(text):
        alias_paren_open = m.start()  # index of this group's '('
        if alias_paren_open in claimed:
            continue
        # Strip inline markup before inspecting the alias body.
        alias_body = _strip_markup(m.group(1))
        if not alias_body:
            continue
        # A CELEX number ("32020L0284") parenthetical is the machine id of the
        # preceding EU act cite ("(EU) 2020/284 (32020L0284)"), not an alias — it
        # must not be minted as a defined-term surface.
        if _CELEX.match(alias_body):
            continue
        # Skip alias-parens that are themselves an act cite or a jäljempänä group
        # (handled elsewhere).
        if _GUARD_JALJEMPANA in alias_body.lower():
            continue
        if _first_act_id_in("(" + alias_body + ")") is not None:
            continue
        # The act is the cite that terminates immediately before this '('
        # (only whitespace may separate the cite end from the alias paren).
        pre = text[max(0, alias_paren_open - 4) : alias_paren_open]
        cite_end = alias_paren_open - (len(pre) - len(pre.rstrip()))
        target = _act_id_ending_before(text, cite_end)
        if target is None:
            # Parenthetical not preceded by an act cite → not an alias binding.
            continue
        status = _classify_term_morphology(alias_body)
        out.append(
            DefinedTermBinding(
                term=alias_body,
                target_ref=target,
                expansion=None,
                scope=_SCOPE_STATUTE,
                source_span=SourceSpan(source_file, m.start(), m.end() - m.start()),
                binding_kind=BINDING_PARENTHETICAL_ALIAS,
                binding_status=status,
            )
        )
    return out


def _recognize_tarkoitetaan(text: str, source_file: str) -> list[DefinedTermBinding]:
    out: list[DefinedTermBinding] = []
    low = text.lower()
    cursor = 0
    seen: set[tuple[int, int]] = set()
    while True:
        verb_pos = low.find(_GUARD_TARKOITETAAN, cursor)
        if verb_pos < 0:
            break
        window_start = max(0, verb_pos - _TARKOITETAAN_LOOKBACK)
        window_end = min(len(text), verb_pos + _TARKOITETAAN_LOOKAHEAD)
        window = text[window_start:window_end]
        for m in _TARKOITETAAN.finditer(window):
            # Keep exactly the match anchored on this literal occurrence; a
            # bounded window may include a neighbouring definition verb too.
            match_verb_pos = window_start + m.start("expansion") - len(_GUARD_TARKOITETAAN)
            if match_verb_pos != verb_pos:
                continue
            match_start = window_start + m.start()
            match_end = window_start + m.end()
            match_key = (match_start, match_end)
            if match_key in seen:
                continue
            seen.add(match_key)
            raw_term = m.group("term").strip()
            if not raw_term:
                continue
            expansion_text = m.group("expansion").strip()
            # Scope inherits from the nearest preceding definitions-header cue
            # ("Tässä laissa/luvussa/pykälässä/momentissa … tarkoitetaan" / "Tätä
            # lakia sovellettaessa …"); conservative ``statute`` default when no such
            # header cue is recognised.  Anchor the look-back on the END of THIS
            # binding's ``tarkoitetaan`` verb (start of the expansion group) so an
            # INLINE cue whose verb is this very binding's verb — "Tässä pykälässä
            # viranomaisella tarkoitetaan …" — is matched contiguously, while a block
            # header above an enumerated definiendum is still seen within the window.
            scope = _scope_cue_before(text, window_start + m.start("expansion"))
            # The CANONICAL inline pipeline (shared with the forest): the HEAD must be
            # a definitional adessive definiendum (the referential idiom is declined),
            # leading scope-locatives are stripped, the left edge is trimmed, and a
            # swept clause fragment is declined.  ``None`` = no binding (no fabrication).
            entry = inline_entry_from_match(text, raw_term, expansion_text, scope)
            if entry is None:
                continue
            act_id = entry.target_ref
            # The definiendum surface is an INFLECTED (adessive) form; M1 is
            # generation-only and cannot reverse it to a nominative, so the term is
            # matched by its exact written surface, not generated inflections.
            status = STATUS_UNSUPPORTED_MORPHOLOGY
            out.append(
                DefinedTermBinding(
                    term=entry.term,
                    target_ref=act_id,
                    expansion=None if act_id is not None else (entry.definiens or None),
                    scope=entry.scope,
                    source_span=SourceSpan(source_file, match_start, match_end - match_start),
                    binding_kind=BINDING_TARKOITETAAN,
                    binding_status=status,
                )
            )
        cursor = verb_pos + len(_GUARD_TARKOITETAAN)
    return out


def _recognize_enumerated_definitions(
    text: str, source_file: str
) -> list[DefinedTermBinding]:
    """Recognize a header-governed enumerated definitions block (shape 3b).

    Anchored on a ``Tässä <unit> tarkoitetaan:`` header; each following enumerated
    item ``N) <definiendum-adessive> <expansion>;`` becomes a binding inheriting
    the header's scope (statute / chapter / section / subsection).  CONSERVATIVE:
    only items whose definiendum is a genuine adessive definiendum
    (``_is_definitional_definiendum``) bind; the scan is bounded to a window after
    the header and STOPS at the structural list-end — the first operative-prose
    boundary (``_gap_has_operative_prose``) — so an unrelated operative ``;``-list
    later in the section is not minted as a phantom definition (F1).
    """
    out: list[DefinedTermBinding] = []
    headers = list(_ENUM_HEADER.finditer(text))
    for i, h in enumerate(headers):
        scope = _enum_header_scope(h.group("unit"))
        # Start the block at the header's ':' so the FIRST item's ':' delimiter is
        # in scope (the item regex anchors each item on a ':' / ';' delimiter).
        block_start = h.end() - 1
        # Block runs until the next header or the bounded window, whichever first.
        block_end = min(len(text), block_start + _ENUM_BLOCK_WINDOW)
        if i + 1 < len(headers):
            block_end = min(block_end, headers[i + 1].start())
        block = text[block_start:block_end]
        # Walk the items in source order; the contiguous definitions list ENDS at
        # the first accepted item whose preceding gap contains an operative-prose
        # provision sentence — everything from there on is a later operative list,
        # not the definitions block (F1 terminator). The gap is measured from the
        # previous ACCEPTED item's end (or the header ':') so a declined non-item
        # never resets the contiguity check.
        prev_item_end = 0
        for it in _ENUM_ITEM.finditer(block):
            run = it.group("run").strip()
            rest = it.group("rest").strip()
            if not run:
                continue
            # The CANONICAL enumerated pipeline (shared with the forest): the
            # leading adessive-headed phrase is detected, the left edge is trimmed
            # (prior-entry connector / adverbial clause), and a swept clause
            # fragment is declined.  An item that is not a genuine adessive
            # definiendum binds nothing (no fabrication).  Each item inherits the
            # block header's scope.
            entry = enumerated_entry_from_item(run, rest, scope=scope)
            if entry is None:
                continue
            # STRUCTURAL LIST-END: if operative prose intervenes between the
            # previous accepted item and this candidate, the definitions list has
            # ended; stop the block here (no later operative-list item is minted).
            if _gap_has_operative_prose(block[prev_item_end : it.start()]):
                break
            prev_item_end = it.end()
            act_id = entry.target_ref
            # The definiendum surface is an INFLECTED (adessive) form; M1 is
            # generation-only and cannot reverse it to a nominative, so the term
            # is matched by its exact written surface, not generated inflections.
            status = STATUS_UNSUPPORTED_MORPHOLOGY
            abs_start = block_start + it.start()
            abs_end = block_start + it.end()
            out.append(
                DefinedTermBinding(
                    term=entry.term,
                    target_ref=act_id,
                    expansion=None if act_id is not None else (entry.definiens or None),
                    scope=entry.scope,
                    source_span=SourceSpan(
                        source_file, abs_start, abs_end - abs_start
                    ),
                    binding_kind=BINDING_TARKOITETAAN,
                    binding_status=status,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def recognize_defined_term_bindings(
    text: str,
    *,
    source_file: str = "",
) -> list[DefinedTermBinding]:
    """Recognize defined-term / alias bindings in Finnish statute prose.

    Returns a list of :class:`DefinedTermBinding`, one per recognized binding
    construct, in source order.  CONSERVATIVE: only the anchored shapes are
    recognized — parenthetical alias after a cite, ``jäljempänä X``, inline
    ``X tarkoitetaan Y``, and the header-governed enumerated definitions block
    (``Tässä <unit> tarkoitetaan: 1) X:llä … ; 2) …``), whose items inherit the
    header's scope.  A bare term used with no binding construct yields NO binding
    (no fabrication).

    Args:
        text:        Plain text of a statute (or fragment thereof).
        source_file: Identifier recorded in each binding's ``SourceSpan``.

    Per AGENTS.md §1.11: substring guards short-circuit before regex on text
    lacking the binding cues.
    """
    if not text:
        return []

    bindings: list[DefinedTermBinding] = []
    claimed_alias_parens: set[int] = set()

    # Shape 2 first, so its parens can be excluded from the bare-alias shape.
    if _GUARD_JALJEMPANA in text.lower():
        jaljempana = _recognize_jaljempana(text, source_file)
        bindings.extend(jaljempana)
        # Mark the '(' that opens any "(jäljempänä …)" group as claimed.
        low = text.lower()
        idx = 0
        while True:
            pos = low.find(_GUARD_JALJEMPANA, idx)
            if pos < 0:
                break
            paren = text.rfind("(", 0, pos)
            if paren >= 0 and pos - paren < 12:
                claimed_alias_parens.add(paren)
            idx = pos + 1

    # Shape 1: parenthetical alias after a cite.
    if _GUARD_PAREN in text:
        bindings.extend(
            _recognize_parenthetical_alias(text, source_file, claimed_alias_parens)
        )

    # Shape 3 + 3b: definitional "tarkoitetaan".  3b (header-governed enumerated
    # block) and 3 (inline "X tarkoitetaan Y") bind disjoint sites — an
    # enumerated item carries no "tarkoitetaan" of its own — so they do not
    # double-bind in practice.  Belt-and-braces: drop any shape-3 binding whose
    # span falls inside an enumerated-block binding span.
    if _GUARD_TARKOITETAAN in text.lower():
        enum = _recognize_enumerated_definitions(text, source_file)
        bindings.extend(enum)
        enum_spans = [
            (b.source_span.byte_offset, b.source_span.byte_offset + b.source_span.byte_len)
            for b in enum
        ]
        for b in _recognize_tarkoitetaan(text, source_file):
            off = b.source_span.byte_offset
            if any(lo <= off < hi for lo, hi in enum_spans):
                continue
            bindings.append(b)

    bindings.sort(key=lambda b: b.source_span.byte_offset)
    return bindings
