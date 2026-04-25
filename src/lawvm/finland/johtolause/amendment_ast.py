#!/usr/bin/env python3
"""Shared AST types for johtolause extraction.

All extractors (tree-walker, PEG, future ML, etc.) produce these types.
The aggregator consumes them. No single-letter codes in the typed interface.

Usage:
    from amendment_ast import AmendmentVerb, StructureKind, LegalRef, AmendmentOp

IR MIGRATION NOTE
=================
LegalRef (parent-linked chain of kind+number) is the Finland-specific precursor
to the core IR's target LegalAddress (see ir.py docstring). LegalAddress is
LegalRef serialized as a flat path list instead of a linked chain:

    LegalRef(MOMENTTI "2" < LegalRef(PYKALA "12" < LegalRef(LUKU "3")))
    → LegalAddress(path=[("chapter","3"), ("section","12"), ("subsection","2")])

LegalRef stays as the Finland frontend's internal type.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional
from lawvm.core.semantic_types import FacetKind


class AmendmentVerb(Enum):
    """Finnish legislative amendment verbs."""
    MUUTTAA = "muuttaa"     # modify
    KUMOTA = "kumota"       # repeal
    LISATA = "lisätä"       # insert/add
    SIIRTAA = "siirtää"     # move/transfer

    @classmethod
    def from_code(cls, code: str) -> "AmendmentVerb":
        return _CODE_TO_VERB[code]

    @property
    def code(self) -> str:
        return _VERB_TO_CODE[self]

    def __repr__(self):
        return self.value


class StructureKind(Enum):
    """Finnish statute structural element types."""
    PYKALA = "pykala"           # § (section)
    LUKU = "luku"              # chapter
    OSA = "osa"                # part
    NIMIKE = "nimike"          # title/heading of statute
    LIITE = "liite"            # appendix/annex
    MOMENTTI = "momentti"      # subsection (numbered paragraph within §)
    KOHTA = "kohta"            # item/point (within momentti)
    JOHDANTOKAPPALE = "johdantokappale"  # introductory paragraph of §
    OTSIKKO = "otsikko"        # heading of §/luku

    @classmethod
    def from_code(cls, code: str) -> "StructureKind":
        return _CODE_TO_KIND[code]

    @property
    def code(self) -> str:
        return _KIND_TO_CODE[self]

    def __repr__(self):
        return self.value


# Bidirectional mappings (legacy codes ↔ enums)
_VERB_TO_CODE = {
    AmendmentVerb.MUUTTAA: "M",
    AmendmentVerb.KUMOTA: "K",
    AmendmentVerb.LISATA: "L",
    AmendmentVerb.SIIRTAA: "S",
}
_CODE_TO_VERB = {v: k for k, v in _VERB_TO_CODE.items()}

_KIND_TO_CODE = {
    StructureKind.PYKALA: "P",
    StructureKind.LUKU: "L",
    StructureKind.OSA: "O",
    StructureKind.NIMIKE: "N",
    StructureKind.LIITE: "A",
    StructureKind.MOMENTTI: "m",
    StructureKind.KOHTA: "k",
    StructureKind.JOHDANTOKAPPALE: "j",
    StructureKind.OTSIKKO: "o",
}
_CODE_TO_KIND = {v: k for k, v in _KIND_TO_CODE.items()}


@dataclass
class LegalRef:
    """A reference to a legal structure element.

    Represents any addressable unit in Finnish statute structure.
    The parent chain encodes hierarchy: kohta → momentti → pykälä → luku.
    """
    kind: StructureKind
    number: str = ""          # '5', '47a', '' for unnumbered
    parent: Optional["LegalRef"] = None
    special: Optional[FacetKind] = None  # FacetKind.HEADING or INTRO

    def flat_address(self) -> str:
        """Stable flat address string for downstream use.

        Format: 'luku:3/pykala:5/momentti:2'
        """
        chain = []
        ref = self
        while ref:
            tag = ref.kind.value
            if ref.special:
                tag += f".{ref.special.value if ref.special else None}"
            chain.append(f"{tag}:{ref.number}" if ref.number else tag)
            ref = ref.parent
        chain.reverse()
        return "/".join(chain)

    def __repr__(self):
        parts = [self.kind.value]
        if self.number:
            parts.append(self.number)
        if self.special:
            parts.append(f"[{self.special.value if self.special else None}]")
        if self.parent:
            parts.append(f"< {self.parent!r}")
        return f"LegalRef({' '.join(parts)})"

    def __eq__(self, other):
        if not isinstance(other, LegalRef):
            return NotImplemented
        return (self.kind == other.kind and self.number == other.number
                and self.special == other.special and self.parent == other.parent)

    def __hash__(self):
        return hash((self.kind, self.number, self.special,
                     self.parent if self.parent is None else self.parent.__hash__()))


@dataclass
class AmendmentOp:
    """A single amendment operation extracted from a johtolause.

    This is the primary typed output of all extractors.
    """
    verb: AmendmentVerb
    target: LegalRef
    is_new: bool = False
    insertion_point: Optional[LegalRef] = None
    source: str = ""          # which extractor produced this
    confidence: float = 1.0   # extractor self-confidence

    def __repr__(self):
        parts = [self.verb.value, repr(self.target)]
        if self.is_new:
            parts.append("NEW")
        if self.insertion_point:
            parts.append(f"INTO {self.insertion_point!r}")
        return f"AmendmentOp({' '.join(parts)})"

    def __eq__(self, other):
        if not isinstance(other, AmendmentOp):
            return NotImplemented
        return (self.verb == other.verb and self.target == other.target
                and self.is_new == other.is_new)

    def __hash__(self):
        return hash((self.verb, self.target, self.is_new))


@dataclass
class Johtolause:
    """Complete parsed enacting clause."""
    ops: List[AmendmentOp] = field(default_factory=list)
    raw_text: str = ""

    def __repr__(self):
        lines = [f"Johtolause({len(self.ops)} ops):"]
        for op in self.ops:
            lines.append(f"  {op!r}")
        return "\n".join(lines)
