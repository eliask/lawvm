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
from dataclasses import dataclass
from typing import Optional

from lawvm.core.reference_mention import SourceSpan

# ---------------------------------------------------------------------------
# Typed output
# ---------------------------------------------------------------------------

#: Default scope for in-document bindings.  Aliases introduced in a statute are
#: scoped to that statute ("tässä laissa …"); there is no narrower lexical scope
#: this recognizer commits to.
_SCOPE_STATUTE = "statute"

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
                      term to an act cite (Finnish ``NUMBER/YEAR`` or EU surface
                      form), else ``None``.
        expansion:    The definitional expansion text for a ``tarkoitetaan``
                      binding whose right-hand side is NOT an act cite, else
                      ``None``.
        scope:        Binding scope; always ``"statute"`` for this recognizer.
        source_span:  Byte range of the whole binding construct in the source
                      text.
        binding_kind: One of ``parenthetical_alias`` / ``jaljempana`` /
                      ``tarkoitetaan``.
        status:       ``"ok"`` when the term's morphology is supported (single
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
    status: str = STATUS_OK


# ---------------------------------------------------------------------------
# Act-cite recognition (self-contained; mirrors eu_reference / plain-text lanes)
# ---------------------------------------------------------------------------
#
# Finnish act id: "(NUMBER/YEAR)".  EU act id: "(FORM) N:o NUMBER/YEAR",
# "(FORM) YEAR/NUMBER", or "NUMBER/YEAR/FORM".  Bounded quantifiers throughout.

_EU_FORMS = r"EU|EY|EEY|ETY|EURATOM|ETA"

# EU "(FORM) N:o NUMBER/YEAR"
_EU_NNUM = re.compile(
    rf"\((?:{_EU_FORMS})\)\s{{0,3}}N:o\s{{0,3}}(\d{{1,6}})/(\d{{4}})",
    re.IGNORECASE,
)
# EU "(FORM) YEAR/NUMBER"  (GDPR-style)
_EU_YEARFIRST = re.compile(
    rf"\((?:{_EU_FORMS})\)\s{{0,3}}(\d{{4}})/(\d{{1,6}})\b",
    re.IGNORECASE,
)
# Finnish "(NUMBER/YEAR)" wrapped act id (closing paren required).
_FI_ID = re.compile(r"\((\d{1,6})/(\d{4})\)")
# Finnish act id followed by a separator (whitespace / comma / paren) — used when
# scanning prose for "the first act mentioned".
_FI_ID_LOOSE = re.compile(r"\((\d{1,6})/(\d{4})[\s,)]")

# A single token of a Finnish term: letters (incl. ä ö å) and internal hyphen.
_TERM_WORD = r"[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+(?:-[a-zA-Z\xe4\xf6\xe5\xc4\xd6\xc5]+)*"

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

_PAREN_ALIAS = re.compile(
    r"\(\s{0,3}([^()]{1,80}?)\s{0,3}\)",
)

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

_JALJEMPANA = re.compile(
    r"j\xe4ljemp\xe4n\xe4\s{1,3}"
    r"(?P<q>[\"“”])?"
    r"(?P<term>[^\")(,;]{1,80}?)"
    r"(?(q)[\"“”]|(?=[),;]|\s*$))",
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

_TARKOITETAAN = re.compile(
    rf"(?P<term>(?:{_TERM_WORD}\s+){{0,3}}{_TERM_WORD})\s+tarkoitetaan\b"
    r"(?P<expansion>[^.;]{0,200})",
    re.IGNORECASE,
)

# Adessive endings the defined term in shape 3 commonly carries; stripped to
# recover the nominative/stem term for morphology classification.
_ADESSIVE_SUFFIXES = ("ll\xe4", "lla")

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
        "jolla",     # joka
        "joilla",    # joka (plural)
        "mill\xe4",  # mikä
        "sill\xe4",  # se
        "t\xe4ll\xe4",   # tämä
        "niill\xe4",     # ne
        "kaikilla",  # kaikki
        "edell\xe4",  # edellä — adverb "above" (referential)
    }
)

# Substring guards (AGENTS.md §1.11)
_GUARD_PAREN = "("
_GUARD_JALJEMPANA = "j\xe4ljemp\xe4n\xe4"
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
        identify the act).
      * FI acts → "NUMBER/YEAR".
    Returns ``None`` if no act cite terminates at ``pos``.
    """
    start = max(0, pos - window)
    chunk = text[start:pos]
    n = len(chunk)
    # EU "(FORM) N:o NUMBER/YEAR" ending at pos.
    eu_n = None
    for m in _EU_NNUM.finditer(chunk):
        eu_n = m
    if eu_n is not None and eu_n.end() == n:
        return f"{eu_n.group(1)}/{eu_n.group(2)}"
    # EU "(FORM) YEAR/NUMBER" ending at pos.
    eu_y = None
    for m in _EU_YEARFIRST.finditer(chunk):
        eu_y = m
    if eu_y is not None and eu_y.end() == n:
        return f"{eu_y.group(1)}/{eu_y.group(2)}"
    # Finnish "(NUMBER/YEAR)" whose closing ')' ends at pos.
    fi = None
    for m in _FI_ID.finditer(chunk):
        if m.end() == n:
            fi = m
    if fi is not None:
        return f"{fi.group(1)}/{fi.group(2)}"
    return None


def _first_act_id_in(text: str) -> Optional[str]:
    """Return the FIRST act id appearing in ``text`` (EU preferred by position).

    Used to resolve the act a binding construct refers to when the act cite
    precedes the cue inside a bounded window.
    """
    candidates: list[tuple[int, str]] = []
    m = _EU_NNUM.search(text)
    if m:
        candidates.append((m.start(), f"{m.group(1)}/{m.group(2)}"))
    m = _EU_YEARFIRST.search(text)
    if m:
        candidates.append((m.start(), f"{m.group(1)}/{m.group(2)}"))
    for fm in _FI_ID_LOOSE.finditer(text):
        candidates.append((fm.start(), f"{fm.group(1)}/{fm.group(2)}"))
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
        r"(ss[a\xe4]|st[a\xe4]|ll[a\xe4]|lt[a\xe4]|lle|"
        r"[a\xe4]n|een|iin|jen|ien|ist[a\xe4])$"
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


def _strip_adessive(term: str) -> str:
    """Strip a trailing adessive (-llä/-lla) to recover the term's stem form.

    Used for shape 3 where the defined term precedes ``tarkoitetaan`` in the
    adessive (``sivutuotteella`` → ``sivutuote``-ish stem).  Conservative: only
    strips the suffix, does not attempt consonant-gradation reversal, so the
    recovered form is the citation stem, not a guaranteed nominative.
    """
    low = term.lower()
    for suf in _ADESSIVE_SUFFIXES:
        if low.endswith(suf) and len(term) > len(suf) + 1:
            return term[: -len(suf)]
    return term


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
    if not (low.endswith("lla") or low.endswith("ll\xe4")):
        return False
    # Closed pronoun / referential-adverb class: matched as full adessive surface
    # forms (exact equality), never by prefix/stem approximation.
    if low in _PRONOUN_ADESSIVE_FORMS:
        return False
    # A bare adessive too short to carry a content stem before the 3-char suffix
    # (e.g. a mangled token) is not a definiendum.
    if len(low) < 5:
        return False
    return True


# ---------------------------------------------------------------------------
# Recognizers
# ---------------------------------------------------------------------------


def _recognize_jaljempana(text: str, source_file: str) -> list[DefinedTermBinding]:
    out: list[DefinedTermBinding] = []
    for m in _JALJEMPANA.finditer(text):
        raw_term = m.group("term").strip()
        if not raw_term:
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
                status=status,
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
        alias_body = m.group(1).strip()
        if not alias_body:
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
                status=status,
            )
        )
    return out


def _recognize_tarkoitetaan(text: str, source_file: str) -> list[DefinedTermBinding]:
    out: list[DefinedTermBinding] = []
    for m in _TARKOITETAAN.finditer(text):
        raw_term = m.group("term").strip()
        if not raw_term:
            continue
        # The captured term may be a multi-word run; keep only the last word(s)
        # that form the defined head.  The defined term is the word directly
        # before "tarkoitetaan" (possibly a final-head compound run already in
        # the capture).  Strip a leading scope phrase like "tässä laissa".
        last_word = raw_term.split()[-1]
        # Only the DEFINITIONAL idiom (``X:llä tarkoitetaan``) introduces a term;
        # the REFERENTIAL idiom (``…, jota / N momentissa / N §:ssä tarkoitetaan``
        # = "referred to in …") binds nothing.  Reject the referential shape so it
        # cannot bind function words / scope locatives / truncated stems as phantom
        # defined terms (the USED_BEFORE_DEFINITION flood).
        if not _is_definitional_definiendum(last_word):
            continue
        term_for_class = _strip_adessive(last_word)
        # Recover full final-head run: take the last token only as the bound
        # term (conservative); multi-token runs with inflected modifiers were
        # captured but the head is the last token.
        expansion_text = m.group("expansion").strip()
        act_id = _act_id_in_expansion(expansion_text)
        status = _classify_term_morphology(term_for_class)
        out.append(
            DefinedTermBinding(
                term=term_for_class,
                target_ref=act_id,
                expansion=None if act_id is not None else (expansion_text or None),
                scope=_SCOPE_STATUTE,
                source_span=SourceSpan(source_file, m.start(), m.end() - m.start()),
                binding_kind=BINDING_TARKOITETAAN,
                status=status,
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
    construct, in source order.  CONSERVATIVE: only the three anchored shapes
    (parenthetical alias after a cite, ``jäljempänä X``, ``X tarkoitetaan Y``)
    are recognized.  A bare term used with no binding construct yields NO
    binding (no fabrication).

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

    # Shape 3: definitional "tarkoitetaan".
    if _GUARD_TARKOITETAAN in text.lower():
        bindings.extend(_recognize_tarkoitetaan(text, source_file))

    bindings.sort(key=lambda b: b.source_span.byte_offset)
    return bindings
