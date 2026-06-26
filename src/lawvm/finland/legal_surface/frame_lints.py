"""EXPERIMENTAL frame lints AS GRAPH QUERIES (H5/H6 affordances).

THIS IS AN EXPERIMENTAL, CANDIDATE-STATUS SURFACE LINT — NOT settled semantics.

Pro r5 §D6 ("lints are graph queries", not lens outputs) + §D7 (firewall). The
single pass here is :class:`DelegationWithoutInstrumentLintPass`: it reads the
assembled graph's ``delegation_frame`` nodes and flags, conservatively, any whose
surface payload names NO instrument on the surface (no resolved
``instrument_kind``).

Authority discipline (§D6/§D7): this lint is a SOURCE-SURFACE static-analysis
observation, NEVER a legal conclusion. It says "the source delegates rulemaking
but names no instrument ON THE SURFACE", which is emphatically NOT "this
delegation is unconstitutional / ultra vires / invalid". ``legal_conclusion`` is
False, ``surface_only`` is True, ``replay_authorized`` is structurally
impossible, and ``forbidden_overclaims`` names exactly the legal readings the
lint must never be mistaken for. The message is self-evidencing (embeds the
delegating actor surface) so the finding is auditable from the message alone.

CONSERVATIVE / tag-don't-guess: it fires ONLY when the instrument is clearly
absent from the surface payload (missing or empty ``instrument_kind``). It never
guesses an instrument and never fires on a frame that already names one.
"""
from __future__ import annotations

import hashlib

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lints import SurfaceLint

JURISDICTION = "fi"

LINT_DELEGATION_WITHOUT_INSTRUMENT = "delegation.surface_without_instrument"
RULE_DELEGATION_WITHOUT_INSTRUMENT = (
    "fi.lint.delegation.surface_without_instrument"
)

# The legal readings this surface lint must NEVER be mistaken for (§D6). Naming
# no instrument on the surface is a drafting-surface observation, not a verdict
# on the delegation's legal validity.
_FORBIDDEN_OVERCLAIMS: tuple[str, ...] = (
    "this delegation is unconstitutional/ultra vires/invalid",
    "the delegation lacks legal authority",
    "the statute is legally defective",
    "any legal consequence follows",
)


def _mint_lint_id(*parts: str) -> str:
    """Deterministic lint id over the lint kind + its subject node id."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _instrument_present(node: SurfaceNode) -> bool:
    """True iff the delegation_frame payload names an instrument on the surface."""
    value = node.payload.get("instrument_kind")
    return isinstance(value, str) and bool(value.strip())


def _actor_label(node: SurfaceNode) -> str:
    """Self-evidencing delegate-actor surface for the message."""
    value = node.payload.get("delegate_actor")
    if isinstance(value, str) and value.strip():
        return value
    return node.node_id


def _refs_of(*nodes: SurfaceNode) -> tuple[SourceSpanRef, ...]:
    return tuple(n.source_ref for n in nodes if n.source_ref is not None)


class DelegationWithoutInstrumentLintPass:
    """EXPERIMENTAL ``delegation.surface_without_instrument`` (info severity).

    Implements ``lawvm.core.legal_surface_lints.SurfaceLintPass``. Flags a
    ``delegation_frame`` whose surface payload names NO instrument. Surface-only,
    never a legal conclusion (see ``forbidden_overclaims``). Conservative: fires
    only when the instrument is clearly absent.
    """

    lint_pass_id: str = "fi.lint.delegation.surface_without_instrument"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        frames = sorted(
            (
                (nid, n)
                for nid, n in graph.nodes.items()
                if n.node_kind == "delegation_frame"
            ),
            key=lambda kv: kv[0],
        )
        lints: list[SurfaceLint] = []
        for frame_id, frame in frames:
            if _instrument_present(frame):
                continue  # names an instrument on the surface → not flagged
            actor = _actor_label(frame)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(
                        LINT_DELEGATION_WITHOUT_INSTRUMENT, frame_id
                    ),
                    lint_kind=LINT_DELEGATION_WITHOUT_INSTRUMENT,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_DELEGATION_WITHOUT_INSTRUMENT,
                    severity="info",
                    subject_node_id=frame_id,
                    support_node_ids=(),
                    source_refs=_refs_of(frame),
                    message=(
                        "EXPERIMENTAL surface affordance: delegation frame for "
                        f"actor {actor!r} names no instrument on the surface "
                        "(no instrument_kind in payload). Surface observation "
                        "only; NOT a legal conclusion."
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


__all__ = [
    "DelegationWithoutInstrumentLintPass",
    "LINT_DELEGATION_WITHOUT_INSTRUMENT",
    "RULE_DELEGATION_WITHOUT_INSTRUMENT",
]
