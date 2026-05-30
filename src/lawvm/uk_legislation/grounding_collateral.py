"""UK oracle-grounding collateral measurement.

Grounding ``local_fallback`` can mint eIds for replay nodes that have no oracle
eId. Those eIds are useful diagnostics, but they should not be mistaken for
source-produced replay over-application when measuring replay-vs-oracle fidelity.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def grounding_collateral_eids(
    replayed_eids: set[str],
    oracle_eids: set[str],
    alignment_events: list[dict[str, Any]],
) -> tuple[str, ...]:
    """Return replay EIDs minted by local fallback and absent from the oracle."""

    minted = {
        str(event.get("after_eid")).lower()
        for event in alignment_events
        if event.get("match_method") == "local_fallback" and event.get("after_eid")
    }
    if not minted:
        return ()
    oracle_lower = {str(eid).lower() for eid in oracle_eids}
    return tuple(
        sorted(
            eid
            for eid in replayed_eids
            if eid.lower() in minted and eid.lower() not in oracle_lower
        )
    )


def eid_set_similarity(replay_eids: set[str], oracle_eids: set[str]) -> float:
    """Return LawVM's UK EID-set similarity score in the 0..1 range."""

    if not replay_eids and not oracle_eids:
        return 1.0
    common = replay_eids & oracle_eids
    return len(common) / max(len(replay_eids), len(oracle_eids), 1)


@dataclass(frozen=True)
class GroundingCollateralScore:
    raw_similarity: float
    collateral_excluded_similarity: float
    collateral_eids: tuple[str, ...]


def score_with_grounding_collateral_excluded(
    replayed_eids: set[str],
    oracle_eids: set[str],
    alignment_events: list[dict[str, Any]],
) -> GroundingCollateralScore:
    """Score raw and collateral-excluded replay/oracle EID similarity."""

    collateral = grounding_collateral_eids(replayed_eids, oracle_eids, alignment_events)
    replay_without_collateral = set(replayed_eids)
    replay_without_collateral.difference_update(collateral)
    return GroundingCollateralScore(
        raw_similarity=eid_set_similarity(replayed_eids, oracle_eids),
        collateral_excluded_similarity=eid_set_similarity(
            replay_without_collateral,
            oracle_eids,
        ),
        collateral_eids=collateral,
    )
