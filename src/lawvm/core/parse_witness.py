"""ParseWitness — provenance record for every parsed operation.

Every op produced by the parser should be explainable: which construction
rule fired, which source spans were consumed, and which resolution rule
(if any) supplied inherited context.

This is the "universal debugging/evidence/UI substrate" from the Pro PEG3
review.  With witnesses:
  - every op knows which source spans justified it
  - effect intents know which clause spans produced them
  - UI can highlight source spans directly
  - review tools can say "this chapter renumber came from this consequence clause"

Architecture:
    parser → ParseWitness per op → attached to SurfaceTarget / ClauseAST / LegalOperation
    witnesses flow through the entire pipeline, never lost or reconstructed

Design:
    - Immutable (frozen dataclass)
    - References rule IDs from rule_registry.py (fi.* namespace)
    - Carries source span (token indices in filtered stream)
    - Optionally carries resolution context (for resolver-produced ops)

API tier
--------
Stable provenance substrate. This is intended to survive frontend migrations
and remain the cross-cutting witness contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class ParseWitness:
    """Provenance record for a single parsed operation.

    Attributes:
        rule_id:     Parse rule that produced this op (e.g. "fi.section_ref").
                     References IDs from rule_registry.py.
        source_span: (start, end) token indices in the filtered stream that were
                     consumed to produce this op.  Half-open: [start, end).
        resolution:  If this op came from a resolution pass (backref, valiotsikko ref,
                     anaphoric), records the resolution rule and context.
    """
    rule_id: str
    source_span: Optional[Tuple[int, int]] = None
    resolution: Optional[ResolutionWitness] = None

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValueError("ParseWitness.rule_id must be non-empty")
        if self.source_span is not None:
            start, end = self.source_span
            if start < 0 or end <= start:
                raise ValueError("ParseWitness.source_span must be a non-empty half-open token span")


@dataclass(frozen=True)
class ResolutionWitness:
    """Records how a resolution rule produced an op.

    Attributes:
        resolver_rule_id: The resolution rule that fired (e.g. "resolution.backref_singular").
        antecedent_span:  Source span of the ops that the resolution referenced
                          (the preceding section ops for a backref).
    context:          Optional structured context (e.g. {"is_singular": True}).
    """
    resolver_rule_id: str
    antecedent_span: Optional[Tuple[int, int]] = None
    context: Optional[Mapping[str, Any]] = None

    def __post_init__(self) -> None:
        if not self.resolver_rule_id:
            raise ValueError("ResolutionWitness.resolver_rule_id must be non-empty")
        if self.antecedent_span is not None:
            start, end = self.antecedent_span
            if start < 0 or end <= start:
                raise ValueError(
                    "ResolutionWitness.antecedent_span must be a non-empty half-open token span"
                )
