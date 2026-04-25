"""Cross-jurisdiction replay lint helpers.

API tier
--------
Internal lint/helper surface for replay diagnostics. Useful across tools, but
not a primary persisted/public contract.
"""

from __future__ import annotations

from typing import List

from lawvm.core.ir import IRNode
from lawvm.core.phase_result import Finding, OBSERVATION_ROLE
from lawvm.core.tree_ops import find_text_duplication_warnings


def build_text_duplication_findings(
    tree: IRNode,
    *,
    phase: str,
    source_statute: str = "",
    min_token_run: int = 12,
    min_char_run: int = 80,
) -> List[Finding]:
    """Convert duplicated-text lints into finding-ledger observations.

    These warnings are intentionally heuristic. They are useful across
    frontends because suspicious large shared tracts often indicate replay or
    materialization bugs even when structural invariants still pass.
    """

    message = (
        "Replay output contains a suspicious duplicated text tract."
        if phase == "replay_fold"
        else "Materialized output contains a suspicious duplicated text tract."
    )
    return [
        Finding(
            kind="text_duplication_warning",
            role=OBSERVATION_ROLE,
            stage="replay_lints",
            blocking=False,
            source_statute=source_statute,
            detail={"message": message, "phase": phase, **warning},
        )
        for warning in find_text_duplication_warnings(
            tree,
            min_token_run=min_token_run,
            min_char_run=min_char_run,
        )
    ]
__all__ = ["build_text_duplication_findings"]
