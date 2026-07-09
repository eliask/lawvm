"""Frozen Level-2 interface carriers — the de-facsimile claim.

FROZEN at the end of Track A (§5.5 of ``notes/SOURCE_DOCUMENT_TWO_LEVEL_PIPELINE.md``).
CARRIERS ONLY — the fold / adjudication / ``verify_ledger`` logic is Track C.

Level 2 composes the stack of per-page ``PageSimulacrum`` evidence into one
coherent whole-document tree PLUS a ledger of these typed, reversible claims.
Each claim carries its own assurance tier (Decision 4) and ``SpanRef``
provenance back into the immutable simulacra, so nothing disappears silently
(AGENTS §1.8): every DROP / DEDUP / REJOIN / REORDER is auditable and reversible.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Tuple

from typing_extensions import override

from lawvm.core.source_document.ir import AssuranceTier
from lawvm.ingest.simulacrum import SpanRef


class DeFacsimileOp(Enum):
    """The four de-facsimile operations plus the explicit legitimate-repeat KEEP.

    - ``DROP_FURNITURE`` — running headers / page numbers / footers.
    - ``DEDUP_SEAM`` — collapse GENUINE cross-seam duplication (seam-adjacency,
      never string-identity).
    - ``REJOIN`` — content split across a page/column break.
    - ``REORDER`` — coherent cross-page reading order (mostly identity; explicit).
    - ``KEEP`` — an explicit claim that a legitimately-repeated node (e.g. a
      printed table's per-page header) is NOT a duplicate.
    """

    DROP_FURNITURE = "drop_furniture"
    DEDUP_SEAM = "dedup_seam"
    REJOIN = "rejoin"
    REORDER = "reorder"
    KEEP = "keep"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class DeFacsimileClaim:
    """One auditable, reversible de-facsimile claim over the page simulacra.

    ``targets`` are the nodes the claim owns (exactly one claim owns each node —
    ``verify_ledger`` enforces claim-disjointness, so fold-order can't matter,
    Decision 3). ``corroborating_producers`` names the independently-produced
    signals that agree (e.g. ``"defacsimile_adjudicator"``, ``"affordance:margin_band"``);
    the tier is ``MULTI_WITNESS_ADJUDICATED`` only when a deterministic affordance
    INDEPENDENTLY fires (Decision 4). ``absorbed`` carries a REJOIN header-absorb
    sub-claim (Decision 3). ``method`` is ``"model_adjudicated"`` or
    ``"deterministic_fallback"`` (the ``compose_pages`` fallback, Decision 8) —
    a typed method, not a route switch.
    """

    op: DeFacsimileOp
    targets: Tuple[SpanRef, ...]
    tier: AssuranceTier
    corroborating_producers: Tuple[str, ...]
    absorbed: Tuple[SpanRef, ...] = ()
    method: str = "model_adjudicated"
    rationale: str = ""
