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
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    DELEGATION_ROLE_ACTORS as _ROLE_ACTORS,
)
from lawvm.finland.references.role_actors import (
    expand_role_actor_phrases,
)

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

_actor_alternation = "|".join(
    re.escape(phrase) for phrase in _ACTOR_PHRASES_LONGEST_FIRST
)
_ACTOR_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:" + _actor_alternation + r")(?![\wäöåÄÖÅ])"
)

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

#: Cheap substring pre-guards: if no instrument surface root appears at all,
#: nothing can fire.
_INSTRUMENT_GUARDS: Tuple[str, ...] = (
    "asetuks",  # asetuksella, asetuksen, asetus
    "asetus",
    "määräy",  # määräyksiä, määräyksen, määräys
    "määrät",  # määrätään
    "ohje",  # ohjeita, ohjeet, ohje
    "päätö",  # päätöksellä, päätös
    "päättä",  # päättää
)

#: Delegation verb surfaces that, together with an instrument noun, mark the
#: clause as a delegation candidate (used for residual detection).
_DELEGATION_VERB_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])(?:säädetään|säätää|säädettävä|annetaan|antaa|"
    r"annettava|määrätään|määrätä|päättää|päätetään)(?![\wäöåÄÖÅ])"
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


# An instrument-noun surface (the noun naming the instrument). Verb-only forms
# like "säädetään"/"määrätään" are NOT instrument nouns and are matched
# separately as delegation verbs.
_INSTRUMENT_NOUN_RE = re.compile(
    r"(?<![\wäöåÄÖÅ])"
    r"(?:asetuksella|asetuksen|asetus|"
    r"määräyksiä|määräyksen|määräykset|määräys|"
    r"ohjeita|ohjeet|ohjeen|ohje|"
    r"päätöksellä|päätöksen|päätös)"
    r"(?![\wäöåÄÖÅ])"
)

#: Clause terminators that bound a delegation window. The actor, instrument and
#: verb must lie within a single clause (between terminators) to form a frame.
_CLAUSE_TERMINATORS = ".;:\n"

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


def _clause_bounds(text: str, pos: int) -> Tuple[int, int]:
    """Return (start, end) byte offsets of the clause containing ``pos``.

    A clause is bounded by the nearest clause terminators on either side (or the
    text boundaries). This is a SURFACE window, not a parse.
    """
    start = pos
    while start > 0 and text[start - 1] not in _CLAUSE_TERMINATORS:
        start -= 1
    end = pos
    while end < len(text) and text[end] not in _CLAUSE_TERMINATORS:
        end += 1
    # trim leading/trailing whitespace inside the window
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return (start, end)


def _capture_subject_span(
    text: str, after: int, clause_end: int
) -> Optional[Tuple[int, int]]:
    """Capture a trailing subject surface span after an instrument/verb.

    SURFACE ONLY: the run of text from ``after`` to the clause end, bounded by
    :data:`_MAX_SUBJECT_SPAN`. Returns (start, end) or None if empty.
    """
    start = after
    while start < clause_end and text[start].isspace():
        start += 1
    if start >= clause_end:
        return None
    end = min(clause_end, start + _MAX_SUBJECT_SPAN)
    while end > start and text[end - 1].isspace():
        end -= 1
    if end <= start:
        return None
    return (start, end)


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


def recognize_delegation_frames(
    text: str, source_file: str = ""
) -> DelegationScan:
    """Recognise surface delegation frames in ``text``.

    Returns a :class:`DelegationScan` carrying typed frames and typed residuals.
    Every emitted frame has ``status="surface_fact_only"``. Nothing is silently
    dropped: a delegation-shaped clause (instrument noun + delegation verb) whose
    delegate actor cannot be typed becomes a typed :class:`DelegationResidual`.

    The recognizer records SURFACE FACTS ONLY and never asserts legal force or a
    delegation-validity / discretion conclusion.
    """
    if not any(guard in text for guard in _INSTRUMENT_GUARDS):
        return DelegationScan(frames=(), residuals=())

    frames: List[DelegationFrame] = []
    residuals: List[DelegationResidual] = []

    consumed_instrument_spans: set[Tuple[int, int]] = set()

    for inst_m in _INSTRUMENT_NOUN_RE.finditer(text):
        key = (inst_m.start(), inst_m.end())
        if key in consumed_instrument_spans:
            continue

        instrument_kind = _instrument_kind_for_surface(inst_m.group(0))
        if instrument_kind is None:
            continue  # not a closed-set instrument; impossible by construction

        clause_start, clause_end = _clause_bounds(text, inst_m.start())
        clause_text = text[clause_start:clause_end]

        # A delegation-shaped clause requires a delegation verb in the clause.
        has_delegation_verb = _DELEGATION_VERB_RE.search(clause_text) is not None
        if not has_delegation_verb:
            # An instrument noun without a delegation verb is not a delegation
            # clause (e.g. a bare cross-reference to "asetuksen 3 §"). Skip.
            continue

        consumed_instrument_spans.add(key)

        # Binding strength from the modal surface in the clause.
        binding: BindingStrength = "must"
        for modal in _MAY_MODALS:
            if re.search(
                r"(?<![\wäöåÄÖÅ])" + re.escape(modal) + r"(?![\wäöåÄÖÅ])",
                clause_text,
            ):
                binding = "may"
                break

        # Find the delegate actor in the clause window.
        actor_matches = list(_ACTOR_RE.finditer(clause_text))
        if not actor_matches:
            residuals.append(
                DelegationResidual(
                    kind="delegation_without_actor",
                    surface_text=clause_text,
                    source_span=_span(source_file, clause_start, clause_end),
                    detail=(
                        f"delegation-shaped clause names instrument "
                        f"{instrument_kind!r} under a delegation verb but no "
                        f"known delegate actor appears in the clause: "
                        f"{clause_text!r}"
                    ),
                )
            )
            continue

        # Prefer the actor nearest the instrument noun. The instrument offset is
        # relative to the clause window.
        inst_rel = inst_m.start() - clause_start
        actor_m = min(
            actor_matches,
            key=lambda m: abs(m.start() - inst_rel),
        )
        actor_surface = actor_m.group(0)

        canonical_id, candidates, is_role = _resolve_actor(actor_surface)

        if not is_role and canonical_id is None and len(candidates) > 1:
            residuals.append(
                DelegationResidual(
                    kind="ambiguous_delegate_actor",
                    surface_text=clause_text,
                    source_span=_span(source_file, clause_start, clause_end),
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
                    source_span=_span(source_file, clause_start, clause_end),
                    detail=(
                        f"delegate-actor surface {actor_surface!r} could not be "
                        f"typed to a canonical actor or a closed role in clause: "
                        f"{clause_text!r}"
                    ),
                )
            )
            continue

        # Subject span: trailing surface after the later of instrument/verb end.
        verb_m = _DELEGATION_VERB_RE.search(clause_text)
        verb_abs_end = (clause_start + verb_m.end()) if verb_m is not None else inst_m.end()
        subject_after = max(inst_m.end(), verb_abs_end)
        subj = _capture_subject_span(text, subject_after, clause_end)
        subject_span = (
            _span(source_file, subj[0], subj[1]) if subj is not None else None
        )

        frames.append(
            DelegationFrame(
                delegate_actor=actor_surface,
                instrument_kind=instrument_kind,
                binding_strength=binding,
                subject_span=subject_span,
                source_span=_span(source_file, clause_start, clause_end),
                status="surface_fact_only",
                rule_id=_RULE_ID,
            )
        )

    return DelegationScan(
        frames=tuple(frames),
        residuals=tuple(residuals),
    )
