"""Shared output types for Finnish johtolause extraction.

ParsedOp is the flat internal compatibility representation emitted by the PEG
frontend and related lowering helpers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional

from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.johtolause.surface_model import TargetKind

if TYPE_CHECKING:
    from lawvm.core.parse_witness import ParseWitness

_VERB_TO_ACTION = {"M": "replace", "K": "repeal", "L": "insert", "S": "renumber"}

@dataclass
class ParsedOp:
    """Flat internal representation of one amendment operation.


    Used by the PEG grammar and compatibility extraction helpers.

    Fields:
        verb:    Operation code M/K/L/S (muuttaa/kumota/lisätä/siirtää)
        kind:    Target type P=section §, L=chapter luku, O=part osa,
                  N=nimike, A=appendix liite
        part:    Part number string, "" if no part context
        chapter: Chapter number string, "" if no chapter context
        number:  Section or chapter number string
        momentti: Subsection ordinal (1-based), 0 = whole section
        item:    Item (kohta) identifier string, "" if none
        facet:   FacetKind for heading/intro, None for whole section
        raw:     Reconstructed op-code string (for debugging)
    """

    verb: str
    kind: str
    chapter: str
    number: str
    momentti: int
    item: str
    raw: str
    special: str = ""
    facet: Optional[FacetKind] = None
    part: str = ""
    notes: tuple[str, ...] = ()
    # Source span: (start, end) token indices in the filtered stream.
    # Populated by the parser when token tracking is active. None = legacy.
    source_tokens: tuple[int, int] | None = None
    # Typed renumber destination (Phase 3: replaces "renumber_destination=N" in notes).
    # For RENUMBER ops (verb="S"), this is the target label the provision is
    # being renumbered TO. None = not a renumber or destination unknown.
    renumber_dest: str = ""
    # Optional destination chapter for renumber/relabel families where the new
    # label alone is insufficient to reconstruct the destination path.
    renumber_dest_chapter: str = ""
    # Optional destination part for move/relabel families where the destination
    # path changes container scope without changing the leaf label.
    renumber_dest_part: str = ""
    # Parse witness: records which construction rule produced this op and which
    # source spans were consumed. None = legacy (not yet instrumented).
    witness: Optional[ParseWitness] = None
    move_clause_target_unit_kind: Optional[TargetUnitKind] = None

    @property
    def typed_kind(self) -> TargetKind:
        """Return the ParsedOp target kind as the johtolause target enum."""
        return TargetKind.from_code(self.kind)

    @property
    def target_leaf_kind(self) -> str:
        """Return the canonical leaf kind for this ParsedOp target."""
        return self.typed_kind.leaf_kind()

    def code(self) -> str:
        """Return canonical op-code string, e.g. 'M P O:II L:3 12 2 5a'."""
        parts = [self.verb, self.kind]
        if self.part:
            parts.append(f"O:{self.part}")
        if self.chapter:
            parts.append(f"L:{self.chapter}")
        parts.append(self.number)
        if self.momentti:
            parts.append(str(self.momentti))
            if self.item:
                parts.append(self.item)
        if self.facet:
            if self.facet == FacetKind.HEADING:
                parts.append("o")
            elif self.facet == FacetKind.INTRO:
                parts.append("j")
        return " ".join(parts)
