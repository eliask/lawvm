"""Typed FI-layer EU-directive **transposition edges** + **timeliness** verdict.

This is the FI-side projection layer that sits ON TOP of the transposition-CLAIM
extractor (:mod:`lawvm.finland.references.eu_transposition`). The extractor finds
the act's *verbal* claim to transpose a directive and binds the named directive
to a CELEX (READ-ONLY) via the deterministic ``eu_nickname`` registry. THIS
module turns each such claim into a single typed :class:`TranspositionEdge`:

    fi_provision/work  --[transposes]-->  eu_directive (CELEX)

carrying the two dates that decide *timeliness* — the directive's transposition
DEADLINE (the date member states had to bring the implementing measures into
force, from the curated demo seed in the extractor) and the citing FI act's
ENACTMENT date — plus a four-way typed :class:`Timeliness` verdict.

What is NEW here (the delta over the existing layers)
-----------------------------------------------------
The transposition-CLAIM extractor already binds CELEX + carries the deadline
seed, and a substrate-side bridge
(:mod:`lawvm.substrate.eu_transposition_bridge`) already emits a relation-edge
``timeliness_fact`` — but ONLY when a caller hands it a commencement date, and it
recognises just ``on_time`` / ``late`` / ``deadline_unknown`` (a MISSING FI date
is not modelled — the bridge requires one). This module is the self-contained FI
projection that:

  * carries ``fi_enactment_date`` as a FIRST-CLASS field of the edge, and
  * adds the fourth honest verdict :attr:`Timeliness.UNKNOWN_ENACTMENT` for the
    case the FI enactment date is not available — so timeliness is COMPUTED only
    when BOTH dates are known, and is otherwise a typed ``unknown_*``, NEVER
    guessed.

It does NOT touch the substrate, the replay/apply path, or any execution state:
it is a pure read-only projection of an already-extracted claim plus a caller-
supplied (verifiable) FI enactment date into a new typed object.

Honesty boundary (part of the result — read before consuming)
-------------------------------------------------------------
This layer COMPUTES, from deterministic evidence only:

  * the DECLARED transposition relation (the FI act SAYS, in its own text, that it
    transposes the named directive) and the directive's CELEX where the registry
    binds it;
  * a timeliness verdict that is a PURE date comparison (FI enactment date vs the
    directive's transposition deadline), and ONLY when both dates are known.

It does NOT, and must not be read to:

  * verify SUBSTANTIVE conformance — it asserts nothing about whether the
    transposition is correct, complete, or in breach; only the *declared
    relation* + *timing* are modelled (conformance is legal interpretation,
    outside the oracle — see the substrate's ``conformance_assessment`` residual);
  * guarantee deadline completeness — the deadline comes from a small curated demo
    seed; a directive with no seeded deadline yields :attr:`UNKNOWN_DEADLINE`,
    never a fabricated date. The seed is NOT a mined, complete deadline table;
  * model partial / minimum-harmonisation directives — a directive may set a floor
    a member state may exceed; "late vs deadline" says nothing about which
    provisions were in scope;
  * resolve transposition-by-multiple-acts — one directive is often transposed by
    several FI acts (and one act may cite many directives); each edge is per
    (claim, directive), so timeliness is per-act, not a directive-level verdict;
  * assert the FI enactment date — that date is supplied by the caller (e.g. the
    statute's säädöskokoelma issue date). When it is absent the verdict is the
    honest :attr:`UNKNOWN_ENACTMENT`, never assumed.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.finland.references.eu_transposition import (
    TranspositionClaim,
    TranspositionStatus,
    transposition_deadline,
)

# The single edge relation this layer emits. A transposition edge always means
# "the FI act DECLARES it transposes this directive"; it is NOT a conformance
# claim (see the module honesty boundary).
TRANSPOSES_EDGE_KIND = "transposes"


class Timeliness(Enum):
    """Four-way typed timeliness verdict for a transposition edge.

    The verdict is COMPUTED (a pure date comparison) only when BOTH the
    directive's transposition deadline AND the FI act's enactment date are known.
    Otherwise it is one of the two honest ``UNKNOWN_*`` residuals — NEVER guessed,
    NEVER defaulted to on_time/late.
    """

    ON_TIME = "on_time"
    """FI enactment date <= directive transposition deadline (both known)."""

    LATE = "late"
    """FI enactment date > directive transposition deadline (both known)."""

    UNKNOWN_DEADLINE = "unknown_deadline"
    """The directive's transposition deadline is not in the curated seed (or the
    directive is unbound), so no comparison is possible — honest absence, not a
    fabricated date."""

    UNKNOWN_ENACTMENT = "unknown_enactment"
    """The FI act's enactment date was not supplied, so no comparison is possible
    — honest absence, the FI date is never assumed."""


@dataclass(frozen=True, slots=True)
class TranspositionEdge:
    """One typed FI->EU directive transposition edge with a timeliness verdict.

    Attributes:
        fi_citing_engine_id: The FI act that DECLARES the transposition (the edge
            source — ``fi:act:<engine_id>``). It is the act's own claim, not an
            external assessment.
        eu_directive_celex: The bound directive CELEX, or ``None`` when the
            directive is named but unbound (``binding_status`` records why); the
            directive identity is then carried by ``directive_surface``.
        directive_surface: The directive nickname/name surface as it appeared.
        edge_kind: Always :data:`TRANSPOSES_EDGE_KIND` — the DECLARED transposition
            relation (never a conformance conclusion).
        transposition_deadline: The directive's transposition deadline (ISO date)
            from the curated seed, or ``None`` when unseeded/unbound (the honest
            absence behind an :attr:`Timeliness.UNKNOWN_DEADLINE` verdict).
        fi_enactment_date: The FI act's enactment date (ISO date) as supplied by
            the caller, or ``None`` when not available (the honest absence behind
            an :attr:`Timeliness.UNKNOWN_ENACTMENT` verdict).
        timeliness: The four-way typed verdict (see :class:`Timeliness`).
        binding_status: Why the directive binding resolved as it did (carried from
            the originating :class:`TranspositionClaim`; §0.3 fail-loud).
        claim_surface: The transposition-declaration phrase verbatim (the evidence
            the edge rests on).
    """

    fi_citing_engine_id: str
    eu_directive_celex: Optional[str]
    directive_surface: str
    edge_kind: str
    transposition_deadline: Optional[str]
    fi_enactment_date: Optional[str]
    timeliness: Timeliness
    binding_status: TranspositionStatus
    claim_surface: str


def _classify_timeliness(*, deadline: Optional[str], fi_enactment_date: Optional[str]) -> Timeliness:
    """Classify timeliness from the two dates — computed only when both are known.

    Precedence is honest, not optimistic: a missing FI enactment date dominates
    (``UNKNOWN_ENACTMENT``) because the FI date is the caller-supplied side that is
    most often absent and must never be assumed; a missing/unseeded deadline is
    ``UNKNOWN_DEADLINE``. Only with BOTH dates is a comparison made. ISO dates
    compare correctly as zero-padded ``YYYY-MM-DD`` strings.
    """
    if fi_enactment_date is None:
        return Timeliness.UNKNOWN_ENACTMENT
    if deadline is None:
        return Timeliness.UNKNOWN_DEADLINE
    return Timeliness.ON_TIME if fi_enactment_date <= deadline else Timeliness.LATE


def transposition_edge_for_claim(
    claim: TranspositionClaim, *, fi_enactment_date: Optional[str] = None
) -> TranspositionEdge:
    """Project ONE transposition claim into a typed :class:`TranspositionEdge`.

    The directive's transposition deadline is looked up from the curated seed ONLY
    when the claim bound a CELEX (an unbound directive has no deadline key, so the
    deadline is ``None`` → :attr:`Timeliness.UNKNOWN_DEADLINE`). The FI enactment
    date is the caller-supplied (verifiable) date — ``None`` when unavailable
    (→ :attr:`Timeliness.UNKNOWN_ENACTMENT`). Timeliness is COMPUTED only when both
    dates are known; otherwise it is the honest ``unknown_*`` typed residual.
    """
    deadline = transposition_deadline(claim.directive_celex) if claim.directive_celex is not None else None
    timeliness = _classify_timeliness(deadline=deadline, fi_enactment_date=fi_enactment_date)
    return TranspositionEdge(
        fi_citing_engine_id=claim.citing_engine_id,
        eu_directive_celex=claim.directive_celex,
        directive_surface=claim.directive_surface,
        edge_kind=TRANSPOSES_EDGE_KIND,
        transposition_deadline=deadline,
        fi_enactment_date=fi_enactment_date,
        timeliness=timeliness,
        binding_status=claim.transposition_status,
        claim_surface=claim.claim_surface,
    )


def build_transposition_edges(
    claims: list[TranspositionClaim], *, fi_enactment_date: Optional[str] = None
) -> list[TranspositionEdge]:
    """Project a list of transposition claims into typed transposition edges.

    One edge per claim, in input order. ``fi_enactment_date`` is the citing act's
    enactment date applied to every edge (all claims come from the SAME act). When
    it is ``None`` every edge's verdict is :attr:`Timeliness.UNKNOWN_ENACTMENT` —
    the FI date is never assumed.
    """
    return [transposition_edge_for_claim(claim, fi_enactment_date=fi_enactment_date) for claim in claims]


__all__ = [
    "TRANSPOSES_EDGE_KIND",
    "Timeliness",
    "TranspositionEdge",
    "build_transposition_edges",
    "transposition_edge_for_claim",
]
