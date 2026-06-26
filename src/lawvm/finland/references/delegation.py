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

CANONICAL CUTOVER (DELEGATION-UNIFY-VERDICT step 4)
==================================================
The forward-grant RECOGNITION is no longer this module's own token walk. The
single canonical forward-grant construction parser
(:func:`lawvm.finland.legal_surface.delegation_canonical.parse_delegation_grants`,
substrate decision Q1: B's TokenTape wins) is now the SOLE producer of the
forward-grant fact; ``recognize_delegation_frames`` is a thin ADAPTER that calls
it and projects each canonical :class:`DelegationGrant` back to the existing
:class:`DelegationFrame` shape (delegate_actor / instrument_kind /
binding_strength / subject_span / source_span – PAYLOAD UNCHANGED), preserving
``delegation_frame`` node identity for every grant B already recognized.

Adjudicated DELTAS vs. the old self-contained B walk (each is allowed by the
DELEGATION_UNIFY_FRONTIER_2026_06_20 adjudication, never a silent change):

  * **old_C_correct additions** -- the old B's typed-actor REQUIREMENT
    residualized a bare / impersonal ``asetuksella säädetään`` (no registered
    actor), the ``Opetusministeriön`` issuer the registry lacks verbatim, the
    ``vahvistetaan`` / ``määritellään`` verb shapes, and the sentence-initial
    capitalized ``Asetuksella`` as ``delegation_without_actor`` – emitting NO
    frame and LOSING the genuine grant. The canonical parser binds those issuers
    (registry → issuer-head fallback → underspecified-NEVER-absent) and emits
    the grant. The adapter therefore now emits a frame for them; ``delegate_actor``
    carries the bound issuer surface, or ``""`` when the issuer is underspecified
    (the impersonal register: the issuer EXISTS in the grant, left unfixed by the
    text – NOT absent).
  * **old_B_false_positive removals** -- the old B keyed off the bare genitive
    ``asetuksen`` and emitted a FALSE grant for an existing-instrument
    cross-reference (``valtioneuvoston asetuksen 34 §:n … säädetään``). The
    canonical section-path / statute-id cross-reference guard correctly DECLINES
    them (typed ``cross_reference_instrument`` residue, no frame).

The ``ambiguous_delegate_actor`` residual is PRESERVED: a canonical grant whose
bound holder surface resolves to >1 registry candidate (and is not a closed role)
is still handed back as a typed residual rather than a frame with a guessed id.
Canonical residuals for the shapes the OLD B silently skipped (cross-reference,
postposition complement, instrument-without-verb) are NOT re-surfaced as B
residuals – the old B emitted no residual for them, so neither does the adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, Tuple

from lawvm.core.legal_surface_tokens import TokenTape
from lawvm.core.reference_mention import SourceSpan
from lawvm.finland.canonical_actor_registry import REGISTRY
from lawvm.finland.references.role_actors import (
    DELEGATION_ROLE_ACTORS as _ROLE_ACTORS,
)

# The single canonical forward-grant construction parser (substrate Q1: token-
# native). Unguarded import = fail loud (a missing canonical parser is a real
# defect, never a silent fallback to a private token walk).
from lawvm.finland.legal_surface.delegation_canonical import (
    parse_delegation_grants,
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

#: Closed binding-strength vocabulary, read off the modal surface ONLY. This is
#: a SURFACE classification of the modal token, NOT a legal force assertion:
#:   must  the clause uses säädetään / annetaan / antaa / määrätään / päättää
#:         (passive "is provided" or neutral active "issues") — no permissive modal
#:   may   the clause uses voidaan / voi (permissive modal surface)
BindingStrength = Literal["must", "may"]

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
    delegation_status: Literal["surface_fact_only"]
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


def _scan_tape(tape: TokenTape, source_file: str) -> DelegationScan:
    """Adapt the canonical forward-grant scan into B's frame/residual shape.

    DELEGATION-UNIFY-VERDICT step 4: the forward-grant recognition is the
    canonical parser's; this adapter projects each canonical
    :class:`~lawvm.finland.legal_surface.delegation_canonical.DelegationGrant`
    back to a :class:`DelegationFrame`, preserving the PAYLOAD shape and the
    ``delegation_frame`` node identity for grants B already recognized.

    The ``ambiguous_delegate_actor`` residual is preserved: a grant whose bound
    holder surface resolves to >1 registry candidate (and is not a closed role)
    is handed back as a typed residual, never a frame with a guessed id.
    """
    source_text = "".join(t.text for t in tape.tokens)
    scan = parse_delegation_grants(tape, source_text)

    frames: List[DelegationFrame] = []
    residuals: List[DelegationResidual] = []

    for grant in scan.grants:
        instrument_kind = _instrument_kind_for_surface(grant.instrument)
        if instrument_kind is None:
            # impossible: canonical instrument vocab is the same closed set.
            continue

        binding: BindingStrength = (
            "may" if grant.binding_strength == "may" else "must"
        )

        clause_text = source_text[grant.frame_start : grant.frame_end]
        actor_surface = grant.holder_surface

        # AMBIGUOUS-ACTOR PRESERVATION. The old B residualized a clause whose
        # nearest actor surface resolved to >1 registry candidate (and was not a
        # closed role). The canonical parser binds the same surface as the holder
        # and emits a grant; to keep B's identity we re-apply B's actor typing to
        # the bound holder and residualize the ambiguous case instead of emitting
        # a frame with a guessed canonical id. An underspecified holder ("") and
        # an issuer-head-fallback holder are NOT registry-ambiguous (they never
        # resolve to >1 candidate), so they flow through as frames (the
        # adjudicated old_C_correct additions).
        if actor_surface:
            canonical_id, candidates, is_role = _resolve_actor(actor_surface)
            if not is_role and canonical_id is None and len(candidates) > 1:
                residuals.append(
                    DelegationResidual(
                        kind="ambiguous_delegate_actor",
                        surface_text=clause_text,
                        source_span=_span(
                            source_file, grant.frame_start, grant.frame_end
                        ),
                        detail=(
                            f"delegate-actor surface {actor_surface!r} is "
                            f"ambiguous across {len(candidates)} canonical actors "
                            f"({', '.join(candidates)}) in clause: {clause_text!r}"
                        ),
                    )
                )
                continue

        subject_span = (
            _span(source_file, grant.subject_start, grant.subject_end)
            if grant.subject_start is not None and grant.subject_end is not None
            else None
        )

        frames.append(
            DelegationFrame(
                delegate_actor=actor_surface,
                instrument_kind=instrument_kind,
                binding_strength=binding,
                subject_span=subject_span,
                source_span=_span(
                    source_file, grant.frame_start, grant.frame_end
                ),
                delegation_status="surface_fact_only",
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
