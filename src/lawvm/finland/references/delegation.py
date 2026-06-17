"""Surface-level delegation-frame recognition (the H5 "delegation frame" lens).

This module implements the **H5 "delegation / authority surface frame"** lens of
the Legal Surface Algebra (Pro, ``pro_on_fi_theory_grammar4.txt`` §5 "Authority /
Power"). It scans Finnish statutory prose for the canonical delegation surface
shapes — a clause that empowers some actor to issue a subordinate legal
instrument — and records each as a TYPED SURFACE FACT:

    <DELEGATE ACTOR> may/must issue <INSTRUMENT KIND> [about <SUBJECT>]

Canonical surface shapes recognised (closed set):

    valtioneuvoston asetuksella säädetään [tarkemmin] ...   actor=valtioneuvosto instrument=asetus must
    ... voidaan säätää valtioneuvoston asetuksella ...        actor=valtioneuvosto instrument=asetus may
    ministeriön asetuksella säädetään ...                     actor=ministeriö     instrument=asetus must
    ministeriö voi antaa määräyksiä ...                       actor=ministeriö     instrument=määräys  may
    <viranomainen> antaa tarkempia määräyksiä ...             actor=<agency>       instrument=määräys  must
    <viranomainen> voi antaa ohjeita ...                      actor=<agency>       instrument=ohje     may

CRITICAL SAFETY BOUNDARY (non-negotiable, Pro r4 — mirrors actor_modal.py):
============================================================================
This layer records SURFACE FACTS ONLY. It surfaces *who is empowered to issue
what subordinate instrument* as a syntactic surface relation. It NEVER emits a
legal conclusion — no "valid delegation", no "discretion", no "power", no
"ultra vires", no "constitutionality". ``binding_strength="may"`` is the surface
fact that the modal surface was ``voidaan``/``voi``; it is NOT a finding of
"discretionary power". Legal interpretation begins in a LATER layer that consumes
these surface facts; this recognizer stops at

    typed surface fact + source span (+ a typed Residual for delegation-shaped
    clauses it sees but cannot type safely).

It is consequently STANDALONE: it does not edit or depend on
``ref_mention_extractor.py`` and is not wired into any graph. The delegate-actor
vocabulary is sourced READ-ONLY from
:data:`lawvm.finland.canonical_actor_registry.REGISTRY` (institutional actors:
ministries, agencies, government levels) plus a small CLOSED list of generic
role-actors (``ministeriö``, ``viranomainen`` …) that can head a delegation
clause but that the institutional registry does not carry unambiguously.

Closed-list discipline (mirrors ``actor_modal.py`` / ``vague.py`` §1.11):
  - The instrument-kind vocabulary is a CLOSED, audited set:
    {asetus, määräys, ohje, päätös}. A token outside it never fires.
  - The binding-strength vocabulary is the CLOSED set {must, may}; it is read
    off the modal surface (``voidaan``/``voi`` → may; ``säädetään``/``annetaan``/
    ``antaa`` → must).
  - The delegate actor comes from the actor registry / the closed role list.
  - A delegation-shaped clause whose actor or instrument cannot be typed safely
    is emitted as a typed :class:`DelegationResidual` — never silently dropped,
    never guessed into a frame.

TOKEN-NATIVE REWRITE (decision B)
=================================
This recognizer is a TOKEN/GRAMMAR recognizer over a :class:`TokenTape`, NOT a
regex over raw text. It shares the actor-matching core with the H4 actor/modal
lens via :class:`lawvm.finland.references.token_actor_match.TokenActorMatcher`
(one token-actor matcher, not two). Instrument nouns, delegation verbs and
permissive modals are matched as closed-set ``word`` tokens; clause windows are
token-index ranges between clause-terminator tokens; nearest-actor selection and
subject capture are token-index operations. Emitted spans are whole-token aligned
(re-baselined vs. the old char-regex spans). The frame PAYLOAD shape is UNCHANGED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.legal_surface_tokens import Token, TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    DELEGATION_ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import (
    expand_role_actor_phrases,
)
from lawvm.finland.references.token_actor_match import TokenActorMatcher

# ---------------------------------------------------------------------------
# Closed vocabularies (NORMATIVE)
# ---------------------------------------------------------------------------

#: Closed instrument-kind vocabulary. The surface noun that names the
#: subordinate legal instrument being delegated. Glosses are descriptive of the
#: SURFACE form only:
#:   asetus    "decree / regulation"        (asetuksella / asetuksen / säätää)
#:   määräys   "binding order / regulation" (määräyksiä / määrätään)
#:   ohje      "guideline / instruction"    (ohjeita / ohjeet)
#:   päätös    "decision"                    (päätöksellä / päättää)
InstrumentKind = Literal["asetus", "määräys", "ohje", "päätös"]

_INSTRUMENT_KINDS: Tuple[InstrumentKind, ...] = ("asetus", "määräys", "ohje", "päätös")

#: Closed binding-strength vocabulary, read off the modal surface ONLY. This is
#: a SURFACE classification of the modal token, NOT a legal force assertion:
#:   must  the clause uses säädetään / annetaan / antaa / määrätään / päättää
#:         (passive "is provided" or neutral active "issues") — no permissive modal
#:   may   the clause uses voidaan / voi (permissive modal surface)
BindingStrength = Literal["must", "may"]

#: Permissive modal surfaces → binding_strength="may". Longest-first matters so
#: "voidaan" is preferred over a hypothetical shorter prefix.
_MAY_MODALS: Tuple[str, ...] = ("voidaan", "voi")

# ---------------------------------------------------------------------------
# Closed generic role-actor list (NORMATIVE)
# ---------------------------------------------------------------------------
#
# The generic delegate-actor role classes that can head a delegation clause are
# the NARROW subset :data:`DELEGATION_ROLE_ACTORS` of the shared
# :mod:`lawvm.finland.references.role_actors` list (imported above as
# ``_ROLE_ACTORS``). This lens uses a narrower set than the full shared union on
# purpose: the role list here also gates the "treat a registry-ambiguous bare
# surface as the generic role" decision in :func:`_resolve_actor`, so it must be
# restricted to surfaces that can actually head a delegation clause
# ("ministeriön asetuksella ...", "viranomainen antaa määräyksiä ..."). Surface
# forms include the nominative and the genitive variant. The bare generic
# "ministeriö" is deliberately AMBIGUOUS in the institutional registry (it maps
# to multiple ministry ids), so a bare-ministeriö delegation resolves to the
# generic role here rather than an arbitrary single ministry.


def _build_actor_phrases() -> Tuple[str, ...]:
    """Union of registry phrase variants and closed role actors, longest-first.

    The institutional vocabulary is read READ-ONLY from the shared ``REGISTRY``;
    we do not mutate it. Role actors are the narrow delegation subset of the
    shared closed list, expanded with their sentence-initial capitalized variant.
    """
    phrases = set(REGISTRY.all_phrases_longest_first())
    phrases.update(expand_role_actor_phrases(_ROLE_ACTORS))
    return tuple(sorted(phrases, key=len, reverse=True))


_ACTOR_PHRASES_LONGEST_FIRST: Tuple[str, ...] = _build_actor_phrases()

# Shared token-native actor matcher (longest-first, case-sensitive verbatim text).
_ACTOR_MATCHER = TokenActorMatcher(_ACTOR_PHRASES_LONGEST_FIRST)

# ---------------------------------------------------------------------------
# Delegation surface shape patterns (CLOSED set)
# ---------------------------------------------------------------------------
#
# Each pattern captures the actor surface and instrument-kind surface. Binding
# strength is derived from whether a permissive modal (voidaan/voi) is present.
# We require the actor and the instrument noun to be in the SAME clause window
# (no clause terminator between them); a delegation-shaped clause that names an
# instrument but cannot tie it to a typeable actor becomes a residual.
#
# A "delegation-shaped clause" is one carrying both an instrument-kind surface
# AND a delegation verb surface (säätää/säädetään/antaa/annetaan/määrätään/
# päättää) — that conjunction is what makes the clause a delegation candidate.

#: Delegation verb surfaces (CLOSED, lowercase) that, together with an instrument
#: noun, mark the clause as a delegation candidate. Matched as exact ``word``
#: tokens (verbatim Token.text), mirroring the old case-sensitive regex.
_DELEGATION_VERBS: frozenset[str] = frozenset(
    {
        "säädetään",
        "säätää",
        "säädettävä",
        "annetaan",
        "antaa",
        "annettava",
        "määrätään",
        "määrätä",
        "päättää",
        "päätetään",
    }
)

# Map an instrument surface token (verbatim) to its canonical InstrumentKind.
# Used after a match to canonicalize. Longest roots checked first.
_INSTRUMENT_SURFACE_TO_KIND: Tuple[Tuple[str, InstrumentKind], ...] = (
    ("asetuks", "asetus"),
    ("asetus", "asetus"),
    ("määräy", "määräys"),
    ("ohje", "ohje"),
    ("päätö", "päätös"),
)


def _instrument_kind_for_surface(surface: str) -> Optional[InstrumentKind]:
    low = surface.lower()
    for root, kind in _INSTRUMENT_SURFACE_TO_KIND:
        if low.startswith(root):
            return kind
    return None


#: Instrument-noun surfaces (the noun naming the instrument), CLOSED lowercase
#: set. Verb-only forms ("säädetään"/"määrätään") are NOT instrument nouns and are
#: matched separately as delegation verbs. Matched as exact ``word`` tokens.
_INSTRUMENT_NOUNS: frozenset[str] = frozenset(
    {
        "asetuksella",
        "asetuksen",
        "asetus",
        "määräyksiä",
        "määräyksen",
        "määräykset",
        "määräys",
        "ohjeita",
        "ohjeet",
        "ohjeen",
        "ohje",
        "päätöksellä",
        "päätöksen",
        "päätös",
    }
)

#: Permissive modal surfaces → binding_strength="may" (exact ``word`` tokens).
_MAY_MODAL_SET: frozenset[str] = frozenset(_MAY_MODALS)

#: Postposition surfaces (CLOSED, lowercase) that take a genitive complement. An
#: instrument noun in the genitive immediately FOLLOWED by one of these is the
#: complement of the postposition phrase, NOT the object of a delegation verb.
#: This excludes the standard enacting preamble
#: ``<actor> päätöksen mukaisesti säädetään`` (= "is provided in accordance with
#: the decision of <actor>"), where ``päätöksen`` is the postposition complement
#: and not a delegated instrument, and the ``… nojalla …`` authority-basis shape.
_POSTPOSITIONS: frozenset[str] = frozenset(
    {
        "mukaisesti",
        "mukaan",
        "nojalla",
        "perusteella",
        "estämättä",
    }
)

#: Demonstrative-determiner surfaces (CLOSED, lowercase) heading a cross-reference
#: to an EXISTING instrument (``tätä asetusta``, ``tämän asetuksen``, ``tässä
#: asetuksessa`` = "this decree"). An instrument noun immediately PRECEDED by one
#: of these is naming an already-existing instrument, not a newly-delegated one,
#: so it must not seed a second delegation frame in the clause.
_DEMONSTRATIVES: frozenset[str] = frozenset(
    {
        "tätä",
        "tämän",
        "tässä",
        "tästä",
        "tähän",
        "tällä",
        "tuota",
        "tuon",
        "tuossa",
        "sitä",
        "sen",
        "siinä",
        "siihen",
    }
)

#: Maximum subject-span length (characters) captured as the trailing subject
#: surface. The subject is a SURFACE span only; it is not parsed.
_MAX_SUBJECT_SPAN = 200


# ---------------------------------------------------------------------------
# Frozen output types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DelegationFrame:
    """A surface delegation frame. SURFACE FACT ONLY.

    Records that a typed delegate actor appears in the same clause as a closed-set
    instrument-kind surface under a delegation verb, optionally with a trailing
    subject span. This surfaces WHO is empowered to issue WHAT subordinate
    instrument as a *syntactic surface relation*. It is NOT a legal conclusion:
    no "valid delegation", no "discretion", no "power".

    Attributes:
        delegate_actor:   The matched delegate-actor surface phrase (verbatim).
        instrument_kind:  Canonical instrument kind from the closed set
                          {asetus, määräys, ohje, päätös}.
        binding_strength: "must" or "may", read off the modal surface ONLY
                          (voidaan/voi → may; otherwise must). A SURFACE fact.
        subject_span:     Byte span of the trailing subject surface (or None).
        source_span:      Byte span covering the whole frame.
        status:           Always "surface_fact_only".
        rule_id:          The recognizer rule that fired.
    """

    delegate_actor: str
    instrument_kind: InstrumentKind
    binding_strength: BindingStrength
    subject_span: Optional[SourceSpan]
    source_span: SourceSpan
    status: Literal["surface_fact_only"]
    rule_id: str


# Residual classes: delegation-shaped clauses the recognizer SEES but cannot
# type into a frame safely. Never silent, never guessed.
ResidualKind = Literal[
    "delegation_without_actor",
    "delegation_with_untypeable_actor",
    "ambiguous_delegate_actor",
]


@dataclass(frozen=True, slots=True)
class DelegationResidual:
    """A typed residual: a seen-but-untypeable delegation surface shape.

    Self-evidencing: ``surface_text`` embeds the verbatim offending clause and
    ``detail`` explains why it could not be typed. A guessed actor/instrument is
    NEVER emitted in its place.

    Attributes:
        kind:         Which residual class fired.
        surface_text: The verbatim offending clause fragment.
        source_span:  Byte span of the offending fragment.
        detail:       Human-readable self-evidencing description (embeds the
                      offending clause text), e.g. for an ambiguous actor the
                      candidate canonical ids it could be.
    """

    kind: ResidualKind
    surface_text: str
    source_span: SourceSpan
    detail: str


@dataclass(frozen=True, slots=True)
class DelegationScan:
    """The full result of scanning one text: typed frames + typed residuals."""

    frames: Tuple[DelegationFrame, ...]
    residuals: Tuple[DelegationResidual, ...]


# ---------------------------------------------------------------------------
# Recognizer
# ---------------------------------------------------------------------------

_RULE_ID = "fi.surface.delegation.v1"


def _span(source_file: str, start: int, end: int) -> SourceSpan:
    return SourceSpan(
        source_file=source_file,
        byte_offset=start,
        byte_len=end - start,
    )


def _is_terminator(tok: Token) -> bool:
    """A token that bounds a clause window (token-side mirror of ``.;:`` / newline)."""
    if tok.category == "punct" and tok.text in ".;:":
        return True
    if tok.category == "whitespace" and "\n" in tok.text:
        return True
    return False


def _clause_token_bounds(
    tokens: Tuple[Token, ...], idx: int
) -> Tuple[int, int]:
    """Return (lo, hi) token-index bounds of the clause containing token ``idx``.

    A clause is bounded by the nearest clause-terminator tokens on either side (or
    the tape boundaries); the terminator itself is excluded. Leading/trailing
    whitespace tokens inside the window are trimmed. This is a SURFACE window.
    """
    lo = idx
    while lo > 0 and not _is_terminator(tokens[lo - 1]):
        lo -= 1
    hi = idx
    n = len(tokens)
    while hi < n and not _is_terminator(tokens[hi]):
        hi += 1
    # trim whitespace tokens at the window edges
    while lo < hi and tokens[lo].category == "whitespace":
        lo += 1
    while hi > lo and tokens[hi - 1].category == "whitespace":
        hi -= 1
    return (lo, hi)


def _capture_subject_span(
    tokens: Tuple[Token, ...], after_index: int, clause_hi: int
) -> Optional[Tuple[int, int]]:
    """Capture a trailing subject surface span after the instrument/verb token.

    SURFACE ONLY: the run of tokens from ``after_index`` to the clause end token,
    bounded by :data:`_MAX_SUBJECT_SPAN` characters from the run start. Returns
    (char_start, char_end) or None if empty. Whole-token aligned, whitespace
    trimmed.
    """
    i = after_index
    while i < clause_hi and tokens[i].category == "whitespace":
        i += 1
    if i >= clause_hi:
        return None
    char_start = tokens[i].char_start
    limit = char_start + _MAX_SUBJECT_SPAN
    last_nonspace_end: Optional[int] = None
    j = i
    while j < clause_hi:
        tok = tokens[j]
        if tok.char_start >= limit:
            break
        if tok.category != "whitespace":
            last_nonspace_end = tok.char_end
        j += 1
    if last_nonspace_end is None:
        return None
    return (char_start, last_nonspace_end)


def _resolve_actor(
    actor_surface: str,
) -> Tuple[Optional[str], List[str], bool]:
    """Resolve an actor surface against the registry / closed role list.

    Returns (canonical_id, candidate_ids, is_role).
      - A registry phrase with exactly one candidate: (id, [id], False).
      - A registry phrase with >1 candidate: (None, candidates, False) — AMBIGUOUS.
      - A closed role-actor not (unambiguously) in the registry: (None, [], True).
    """
    canonical_id, candidates = REGISTRY.lookup(actor_surface)
    if len(candidates) == 1:
        return canonical_id, candidates, False
    if len(candidates) > 1:
        # Ambiguous registry phrase. But a generic role surface ("ministeriö")
        # is registered ambiguously on purpose; treat it as the generic role.
        if actor_surface.lower() in {r.lower() for r in _ROLE_ACTORS}:
            return None, [], True
        return None, candidates, False
    # No registry match: is it a closed role actor?
    if actor_surface.lower() in {r.lower() for r in _ROLE_ACTORS}:
        return None, [], True
    return None, [], False


def _prev_word_token(tokens: Tuple[Token, ...], idx: int) -> Optional[Token]:
    """The nearest preceding ``word`` token before ``idx`` (skipping whitespace)."""
    j = idx - 1
    while j >= 0:
        if tokens[j].category == "word":
            return tokens[j]
        if tokens[j].category != "whitespace":
            return None
        j -= 1
    return None


def _next_word_token(tokens: Tuple[Token, ...], idx: int) -> Optional[Token]:
    """The nearest following ``word`` token after ``idx`` (skipping whitespace)."""
    n = len(tokens)
    j = idx + 1
    while j < n:
        if tokens[j].category == "word":
            return tokens[j]
        if tokens[j].category != "whitespace":
            return None
        j += 1
    return None


def _is_cross_reference_instrument(
    tokens: Tuple[Token, ...], inst_idx: int
) -> bool:
    """True if the instrument-noun token names an EXISTING instrument, not a
    newly-delegated one — so it must NOT seed a delegation frame.

    Two CLOSED surface shapes are excluded:

      * postposition complement — the instrument is immediately FOLLOWED by a
        genitive-governing postposition (``päätöksen mukaisesti``, ``… nojalla``).
        This is the enacting preamble / authority-basis shape, where the noun is
        the complement of the postposition, not the object of the delegation verb.
      * demonstrative cross-reference — the instrument is immediately PRECEDED by
        a demonstrative determiner (``tätä asetusta``, ``tämän asetuksen``),
        naming an instrument that already exists rather than delegating a new one.
    """
    nxt = _next_word_token(tokens, inst_idx)
    if nxt is not None and nxt.text.lower() in _POSTPOSITIONS:
        return True
    prev = _prev_word_token(tokens, inst_idx)
    if prev is not None and prev.text.lower() in _DEMONSTRATIVES:
        return True
    return False


def _first_delegation_verb_index(
    tokens: Tuple[Token, ...], lo: int, hi: int
) -> Optional[int]:
    """First delegation-verb word-token index in [lo, hi), or None."""
    for j in range(lo, hi):
        tok = tokens[j]
        if tok.category == "word" and tok.text in _DELEGATION_VERBS:
            return j
    return None


def _clause_has_may_modal(
    tokens: Tuple[Token, ...], lo: int, hi: int
) -> bool:
    for j in range(lo, hi):
        tok = tokens[j]
        if tok.category == "word" and tok.text in _MAY_MODAL_SET:
            return True
    return False


def _scan_tape(tape: TokenTape, source_file: str) -> DelegationScan:
    tokens = tape.tokens

    frames: List[DelegationFrame] = []
    residuals: List[DelegationResidual] = []

    consumed_instrument_idx: set[int] = set()

    for inst_idx, inst_tok in enumerate(tokens):
        if inst_idx in consumed_instrument_idx:
            continue
        if inst_tok.category != "word" or inst_tok.text not in _INSTRUMENT_NOUNS:
            continue

        instrument_kind = _instrument_kind_for_surface(inst_tok.text)
        if instrument_kind is None:
            continue  # not a closed-set instrument; impossible by construction

        # The instrument noun must be a DELEGATED instrument (the object of the
        # delegation verb), not the complement of a postposition (the enacting
        # preamble ``päätöksen mukaisesti säädetään`` / ``… nojalla …``) nor a
        # demonstrative cross-reference to an already-existing instrument
        # (``tätä asetusta``, ``tämän asetuksen``). Such tokens are skipped: they
        # are not delegation candidates and must not seed a (possibly second)
        # frame in the clause.
        if _is_cross_reference_instrument(tokens, inst_idx):
            continue

        clause_lo, clause_hi = _clause_token_bounds(tokens, inst_idx)
        if clause_lo >= clause_hi:
            continue
        clause_char_start = tokens[clause_lo].char_start
        clause_char_end = tokens[clause_hi - 1].char_end
        clause_text = "".join(t.text for t in tokens[clause_lo:clause_hi])

        # A delegation-shaped clause requires a delegation verb in the clause.
        verb_idx = _first_delegation_verb_index(tokens, clause_lo, clause_hi)
        if verb_idx is None:
            # An instrument noun without a delegation verb is not a delegation
            # clause (e.g. a bare cross-reference to "asetuksen 3 §"). Skip.
            continue

        consumed_instrument_idx.add(inst_idx)

        # Binding strength from the modal surface in the clause.
        binding: BindingStrength = (
            "may" if _clause_has_may_modal(tokens, clause_lo, clause_hi) else "must"
        )

        # Find the delegate actor in the clause window.
        actor_matches = _ACTOR_MATCHER.find_in_window(tokens, clause_lo, clause_hi)
        if not actor_matches:
            residuals.append(
                DelegationResidual(
                    kind="delegation_without_actor",
                    surface_text=clause_text,
                    source_span=_span(
                        source_file, clause_char_start, clause_char_end
                    ),
                    detail=(
                        f"delegation-shaped clause names instrument "
                        f"{instrument_kind!r} under a delegation verb but no "
                        f"known delegate actor appears in the clause: "
                        f"{clause_text!r}"
                    ),
                )
            )
            continue

        # Prefer the actor nearest the instrument noun (by source-char distance).
        inst_char = inst_tok.char_start
        actor_m = min(
            actor_matches,
            key=lambda m: abs(m.char_start - inst_char),
        )
        actor_surface = actor_m.surface

        canonical_id, candidates, is_role = _resolve_actor(actor_surface)

        if not is_role and canonical_id is None and len(candidates) > 1:
            residuals.append(
                DelegationResidual(
                    kind="ambiguous_delegate_actor",
                    surface_text=clause_text,
                    source_span=_span(
                        source_file, clause_char_start, clause_char_end
                    ),
                    detail=(
                        f"delegate-actor surface {actor_surface!r} is ambiguous "
                        f"across {len(candidates)} canonical actors "
                        f"({', '.join(candidates)}) in clause: {clause_text!r}"
                    ),
                )
            )
            continue

        if not is_role and canonical_id is None and not candidates:
            # Actor surface matched the alternation but resolved to neither a
            # single registry id nor a closed role. This should not happen given
            # the alternation is built from exactly those sources, but fail loud.
            residuals.append(
                DelegationResidual(
                    kind="delegation_with_untypeable_actor",
                    surface_text=clause_text,
                    source_span=_span(
                        source_file, clause_char_start, clause_char_end
                    ),
                    detail=(
                        f"delegate-actor surface {actor_surface!r} could not be "
                        f"typed to a canonical actor or a closed role in clause: "
                        f"{clause_text!r}"
                    ),
                )
            )
            continue

        # Subject span: trailing surface after the later of instrument/verb end.
        subject_after = max(inst_idx + 1, verb_idx + 1)
        subj = _capture_subject_span(tokens, subject_after, clause_hi)
        subject_span = (
            _span(source_file, subj[0], subj[1]) if subj is not None else None
        )

        frames.append(
            DelegationFrame(
                delegate_actor=actor_surface,
                instrument_kind=instrument_kind,
                binding_strength=binding,
                subject_span=subject_span,
                source_span=_span(source_file, clause_char_start, clause_char_end),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )

    return DelegationScan(
        frames=tuple(frames),
        residuals=tuple(residuals),
    )


def recognize_delegation_frames(
    tape_or_text: TokenTape | str, source_file: str = ""
) -> DelegationScan:
    """Recognise surface delegation frames over a :class:`TokenTape`.

    Returns a :class:`DelegationScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a delegation-shaped clause (instrument noun + delegation verb) whose
    delegate actor cannot be typed becomes a typed :class:`DelegationResidual`.

    The recognizer records SURFACE FACTS ONLY and never asserts legal force or a
    delegation-validity / discretion conclusion.

    Accepts a :class:`TokenTape` (the token-native path the lens feeds) or, for
    convenience in tests/baselines, a raw ``str`` (tokenized internally via the
    Finnish tokenizer). Emitted spans are whole-token aligned.
    """
    if isinstance(tape_or_text, str):
        from lawvm.finland.legal_surface.tokenize import build_token_tape

        tape = build_token_tape(source_file or "delegation", tape_or_text)
    else:
        tape = tape_or_text
    return _scan_tape(tape, source_file)
