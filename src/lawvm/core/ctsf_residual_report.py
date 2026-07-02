"""CTSF parallel residual-set report — task #197 (CTSF Phase 2), READ-ONLY.

The shape a future Phase-3 GATE would consume (FABLE §5.2 / §5.5): for a statute's
replay-vs-oracle pair, run the pipeline in the FABLE-mandated order

    1. commensurability FIRST (STATE_INDEX) — is the pair even comparable?
    2. CTSF projection + equality — for the commensurable part;
    3. typed residual inventory — a "residual verdict" counting by FAMILY.

The residual verdict is a family-count multiset drawn from the SINGLE unified
taxonomy (``AgreementResidualFamily``): ``replay_bug`` / ``oracle_editorial_pathology``
/ ``temporal_mismatch`` / ``state_index`` (the STATE_INDEX kinds, folded to their
families) / ``cnf_unsupported`` (CTSF v0 typed residuals) / ``unknown``. A future
gate FAILS on any new ``replay_bug`` or ``unknown``; everything else is typed,
evidence-backed, and non-billable to replay.

Phase-2 discipline (out of scope, deferred to Phase 3): this is NOT wired into any
gate or the bench headline. Building or importing it leaves default bench output
byte-identical — it computes over CTSF/STATE_INDEX objects only and touches no
bench code path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lawvm.core.ctsf import (
    CTSFResidual,
    collect_residuals,
    ctsf_equal,
    to_ctsf,
)
from lawvm.core.ctsf_state_index import (
    StateIndex,
    StateIndexResidual,
    commensurability_first,
)
from lawvm.semantic.model import SemanticStructureNode

# The residual-verdict families. ``state_index`` and ``cnf_unsupported`` are
# report-level roll-ups; the STATE_INDEX kinds each also carry their
# AgreementResidual family (temporal_mismatch / extent_branch_mismatch) for the
# unified sink, but the verdict groups them under one bucket for the gate view.
RESIDUAL_VERDICT_FAMILIES: tuple[str, ...] = (
    "replay_bug",
    "oracle_editorial_pathology",
    "temporal_mismatch",
    "state_index",
    "cnf_unsupported",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class CTSFResidualReport:
    """The residual verdict for one replay-vs-oracle statute comparison.

    ``commensurable`` reflects the STATE_INDEX short-circuit; when False the CTSF
    content comparison was NOT run (``ctsf_equal`` is ``None``) and the divergence
    is fully attributed to ``state_index_residuals`` — the commensurability-first
    ordering. ``verdict`` is the family-count multiset a Phase-3 gate reads.
    """

    sid: str
    commensurable: bool
    ctsf_equal: Optional[bool]
    state_index_residuals: tuple[StateIndexResidual, ...]
    replay_cnf_residuals: tuple[CTSFResidual, ...]
    oracle_cnf_residuals: tuple[CTSFResidual, ...]
    verdict: dict[str, int]

    @property
    def has_replay_bug_or_unknown(self) -> bool:
        """The Phase-3 gate predicate (defined here, NOT wired to any gate)."""
        return bool(self.verdict.get("replay_bug", 0) or self.verdict.get("unknown", 0))

    def to_dict(self) -> dict[str, Any]:
        return {
            "sid": self.sid,
            "commensurable": self.commensurable,
            "ctsf_equal": self.ctsf_equal,
            "verdict": dict(self.verdict),
            "state_index_residuals": [r.to_dict() for r in self.state_index_residuals],
            "replay_cnf_residuals": [r.to_dict() for r in self.replay_cnf_residuals],
            "oracle_cnf_residuals": [r.to_dict() for r in self.oracle_cnf_residuals],
        }


def _empty_verdict() -> dict[str, int]:
    return {family: 0 for family in RESIDUAL_VERDICT_FAMILIES}


def residual_set_report(
    replay: SemanticStructureNode | None,
    oracle: SemanticStructureNode | None,
    *,
    sid: str = "",
    replay_index: StateIndex | None = None,
    oracle_index: StateIndex | None = None,
) -> CTSFResidualReport:
    """Run commensurability-first → CTSF → typed residual inventory for one pair.

    ``replay_index`` / ``oracle_index`` are the per-side STATE_INDEX coordinates;
    when both are omitted the pair is treated as commensurable (no state-index
    evidence to convict it) and the pipeline proceeds straight to CTSF content
    comparison — the fail-open default, so an unindexed pair is never silently
    laundered into a state-index residual.

    The verdict is built as follows (single unified taxonomy):
    * any STATE_INDEX residual ⇒ ``state_index`` bucket (+ the pair is
      incommensurable and CTSF is NOT run);
    * each side's ``CNF_UNSUPPORTED_*`` residual ⇒ ``cnf_unsupported`` bucket;
    * a commensurable pair that is CTSF-UNEQUAL ⇒ one ``unknown`` residual (the
      Phase-2 stub for "content diverges and nothing above typed it"; Phase 3's
      touch-relation attribution refines this into replay_bug/oracle_* — the
      bucket exists so the gate predicate is already defined).
    """
    verdict = _empty_verdict()

    ri = replay_index if replay_index is not None else StateIndex()
    oi = oracle_index if oracle_index is not None else StateIndex()

    # -- CTSF projection is deferred behind the commensurability short-circuit --
    replay_ctsf = to_ctsf(replay) if replay is not None else None
    oracle_ctsf = to_ctsf(oracle) if oracle is not None else None

    def _content_equal() -> bool:
        if replay_ctsf is None or oracle_ctsf is None:
            return replay_ctsf is None and oracle_ctsf is None
        return ctsf_equal(replay_ctsf, oracle_ctsf)

    outcome = commensurability_first(ri, oi, _content_equal, address=sid)

    # STATE_INDEX residuals → the state_index bucket (folded from their families).
    for _r in outcome.state_index_residuals:
        verdict["state_index"] += 1

    # CNF typed residuals (both sides), regardless of commensurability — an
    # unsupported construct is a standing capability gap, not a content diff.
    replay_cnf = collect_residuals(replay_ctsf) if replay_ctsf is not None else ()
    oracle_cnf = collect_residuals(oracle_ctsf) if oracle_ctsf is not None else ()
    verdict["cnf_unsupported"] += len(replay_cnf) + len(oracle_cnf)

    # Only a COMMENSURABLE pair reaches content comparison; an unequal one is the
    # (Phase-2) untyped residue. An incommensurable pair contributes NO content
    # residual — its whole divergence is already attributed to state_index.
    if outcome.commensurable and outcome.content_equal is False:
        verdict["unknown"] += 1

    return CTSFResidualReport(
        sid=sid,
        commensurable=outcome.commensurable,
        ctsf_equal=outcome.content_equal,
        state_index_residuals=outcome.state_index_residuals,
        replay_cnf_residuals=replay_cnf,
        oracle_cnf_residuals=oracle_cnf,
        verdict=verdict,
    )
