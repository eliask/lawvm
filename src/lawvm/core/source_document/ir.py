"""SourceDocumentIR node carrier + governed kinds + authority tiers (D0).

``SourceDocumentNode`` is DISTINCT from the replay-facing
``lawvm.core.ir.IRNode`` (enacted text-state). ``SourceDocumentIR`` is the
addressable source-document tree — what a source document *says*, with
extraction provenance and an authority tier on every node. Product-specific
lowerers turn it into a ``LegalWorkTimeline`` InitialStateEvent (current law)
or a ``ProposalPackage`` (draft HE); it is not itself replay authority.

Assurance ladder (``AssuranceTier``): assurance is producer-NEUTRAL — a lone
pdfplumber read and a lone vision read are BOTH single-witness. Assurance rises
only when independent producers CORROBORATE and an adjudicator reconciles/
composes them (``MULTI_WITNESS_ADJUDICATED``), or a human confirms the result;
those two admit a clean text-state claim. The producer does NOT assign its own
tier — an ``Adjudicator`` does (``adjudication.py``; AGENTS.md §0). The
pdfplumber-vs-model dichotomy is false: both are noisy inputs to adjudication.

Discipline (AGENTS.md §1.9): typed frozen carrier; tuple children, never list;
no ``Any`` at this waist (``attrs`` is a closed ``Mapping[str, str]``).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping, Optional, Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor


class AssuranceTier(Enum):
    """Producer-neutral assurance of an extracted node — a property of ADJUDICATION.

    Assurance does NOT come from which extractor read the bytes (pdfplumber, OCR,
    a vision model, a prior layer are all just noisy inputs). It comes from
    INDEPENDENT corroboration + adjudication: several producers agreeing and an
    adjudicator (an LLM workflow, a human) reconciling and composing them. Only
    ``MULTI_WITNESS_ADJUDICATED`` and ``HUMAN_CONFIRMED`` may feed a clean
    (unqualified) text-state claim; a single witness is qualified; an
    unadjudicated proposal never materializes.
    """

    HUMAN_CONFIRMED = "human_confirmed"
    """A human attested the adjudicated output (signed / recorded) — highest assurance."""
    MULTI_WITNESS_ADJUDICATED = "multi_witness_adjudicated"
    """>=2 INDEPENDENT producers corroborated and an adjudicator composed the result."""
    SINGLE_WITNESS = "single_witness"
    """One producer read the region (mechanical OR model); anchored but uncorroborated — qualified."""
    UNADJUDICATED_PROPOSAL = "unadjudicated_proposal"
    """A raw candidate not accepted as a witness; never materializes as clean text-state."""

    @property
    def admits_clean_text_state(self) -> bool:
        """Whether this tier may feed a clean (unqualified) text-state claim."""
        return self in (
            AssuranceTier.HUMAN_CONFIRMED,
            AssuranceTier.MULTI_WITNESS_ADJUDICATED,
        )

    @override
    def __str__(self) -> str:
        return self.value


class SourceDocumentNodeKind(Enum):
    """Governed node kinds for ``SourceDocumentIR``.

    Structural kinds mirror ``lawvm.core.semantic_types.IRNodeKind`` values so
    product lowerers can map source-document nodes 1:1 onto enacted-text IR.
    Source-document-specific kinds (``RESIDUAL_REGION``, ``IMAGE_REGION``,
    ``WORK_ROOT``, ``PROPOSAL_SECTION``, ``BILL_TEXT``) carry source-document
    structure that has no enacted-text-state analogue.
    """

    WORK_ROOT = "work_root"
    BODY = "body"
    PART = "part"
    CHAPTER = "chapter"
    SECTION = "section"
    SUBSECTION = "subsection"
    PARAGRAPH = "paragraph"
    ITEM = "item"
    TABLE = "table"
    TABLE_ROW = "table_row"
    TABLE_CELL = "table_cell"
    HEADING = "heading"
    FOOTNOTE = "footnote"
    IMAGE_REGION = "image_region"
    RESIDUAL_REGION = "residual_region"
    PROPOSAL_SECTION = "proposal_section"
    BILL_TEXT = "bill_text"

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class SourceDocumentNode:
    """One node of an addressable source-document tree.

    Every node carries its ``assurance_tier`` and ``anchor`` so no extracted
    content reaches a product lowerer without provenance (AGENTS.md §0:
    evidence is not authority; §1.8: nothing disappears). ``assurance_tier`` is
    assigned by an ``Adjudicator`` (``adjudication.py``), not by the extractor
    that produced the node — a single producer is single-witness regardless of
    whether it is mechanical or a model.
    """

    kind: SourceDocumentNodeKind
    assurance_tier: AssuranceTier
    anchor: SourceAnchor
    label: Optional[str] = None
    text: str = ""
    children: Tuple["SourceDocumentNode", ...] = field(default_factory=tuple)
    attrs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SourceDocumentNodeKind):
            raise TypeError("SourceDocumentNode.kind must be a SourceDocumentNodeKind")
        if not isinstance(self.assurance_tier, AssuranceTier):
            raise TypeError("SourceDocumentNode.assurance_tier must be an AssuranceTier")
        if not isinstance(self.anchor, SourceAnchor):
            raise TypeError("SourceDocumentNode.anchor must be a SourceAnchor")
        if not isinstance(self.children, tuple):
            raise TypeError("SourceDocumentNode.children must be a tuple")
