"""ProposalPackage + ConditionalBranch — "if enacted, then …" product carriers.

A draft bill (Finnish HE luonnos, a draft SI, a COM proposal) is not enacted
law: it is a CONDITIONAL overlay. Its operative core (the lakiehdotus) lowers to
candidate operations that WOULD apply *if* the proposal is enacted; its reasoning
(the perustelut) is a bound, non-operative interpretive attachment (in Finland,
esityöt / travaux préparatoires — authoritative for READING the op, never for
APPLYING it). This module is the jurisdiction-neutral product these lower into.

The one hard invariant (the review §9 authority-laundering countermeasure, and
the branch model): a proposal is NEVER ``replay_authorized`` — a draft may not
mutate the enacted timeline. Only enactment flips that, via a different lane.

Discipline (AGENTS.md §1.9, §1.10): typed frozen carriers; the non-authorization
invariant is enforced in ``__post_init__``, not merely documented.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNode


class ProposalAuthorityStatus(Enum):
    """Lifecycle status of a proposal — rises from consultation draft to enacted.

    Only ``ENACTED`` may ever be replay-authorized (through the enactment lane,
    not this carrier). Every earlier status is a conditional overlay.
    """

    CONSULTATION_DRAFT = "consultation_draft"
    """A lausuntokierros draft (HE luonnos) — the lowest, most tentative status."""
    MINISTRY_DRAFT = "ministry_draft"
    GOVERNMENT_BILL = "government_bill"
    """A formally-introduced HE (annettu eduskunnalle)."""
    COMMITTEE_AMENDED = "committee_amended"
    ENACTED = "enacted"
    WITHDRAWN = "withdrawn"

    @property
    def is_enacted(self) -> bool:
        return self is ProposalAuthorityStatus.ENACTED

    @override
    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class CandidateOperation:
    """A single proposed operation — "if enacted, do this to that provision".

    A product-facing projection of the operation the deterministic johtolause
    parser (or an adjudicated extraction) recovered from the bill text. It names
    the target statute + provision, the action, and the new/replacement payload,
    and carries its ``assurance_tier`` (a lone deterministic parse is
    ``SINGLE_WITNESS``; a parse the LLM adjudication corroborates is
    ``MULTI_WITNESS_ADJUDICATED``). It is deliberately NOT a replay
    ``LegalOperation`` — it never executes; it describes a conditional effect.
    """

    action: str
    """``StructuralAction`` value: insert / replace / repeal / …"""
    target_statute_id: str
    """The amended statute, e.g. ``"603/2006"`` — empty if unresolved."""
    target_provision_ref: str
    """Human-readable provision locator, e.g. ``"4 §, 5 mom."``."""
    payload_text: str
    """The new / replacement provision text (empty for a repeal)."""
    source_anchor: SourceAnchor
    assurance_tier: AssuranceTier
    raw_johtolause: str = ""
    """The enacting-formula clause this op was parsed from (verbatim provenance)."""

    def __post_init__(self) -> None:
        if not self.action:
            raise ValueError("CandidateOperation.action must be non-empty")
        if not isinstance(self.source_anchor, SourceAnchor):
            raise TypeError("CandidateOperation.source_anchor must be a SourceAnchor")
        if not isinstance(self.assurance_tier, AssuranceTier):
            raise TypeError("CandidateOperation.assurance_tier must be an AssuranceTier")


@dataclass(frozen=True, slots=True)
class ConditionalBranch:
    """A non-authoritative overlay: "IF ``condition`` THEN ``candidate_ops`` apply".

    The "if enacted, then …" object. ``replay_authorized`` is fixed ``False`` and
    enforced — a conditional branch may never mutate the enacted timeline.
    ``commencement`` is the voimaantulo as read (``""`` = unresolved: a draft's
    date is typically blank or "mahdollisimman pian").
    """

    branch_id: str
    condition: str
    """The activating condition, e.g. "HE VM045:00/2026 enacted as introduced"."""
    candidate_ops: Tuple[CandidateOperation, ...]
    authority_status: ProposalAuthorityStatus
    replay_authorized: bool = False
    commencement: str = ""

    def __post_init__(self) -> None:
        if not self.branch_id:
            raise ValueError("ConditionalBranch.branch_id must be non-empty")
        if not self.condition:
            raise ValueError("ConditionalBranch.condition must be non-empty")
        if self.replay_authorized:
            raise ValueError(
                "ConditionalBranch.replay_authorized must be False — a proposal may "
                "not mutate the enacted timeline (the review §9; the branch model). "
                "Enactment authorizes replay through a different lane, not here."
            )
        if not isinstance(self.candidate_ops, tuple):
            raise TypeError("ConditionalBranch.candidate_ops must be a tuple")


@dataclass(frozen=True, slots=True)
class ProposalPackage:
    """A draft's operative branches + its bound interpretive-reasoning attachment.

    ``branches`` is the tuple of operative "if enacted" overlays — ONE per
    lakiehdotus law (a real HE proposes 1–44 laws, each with its own johtolause,
    target statute and voimaantulo). A single-law bill is a 1-tuple; a non-HE
    document is the empty tuple. ``reasoning_root`` is the perustelut subtree — a
    non-operative, source-anchored interpretive attachment (esityöt), bound to the
    branches but never lowered to an op. Together they answer "what would change,
    and why / with what expected effects".
    """

    proposal_id: str
    """Stable id, e.g. ``"fi:he:VM045:00/2026"``."""
    source_manifestation_digests: Tuple[str, ...]
    branches: Tuple[ConditionalBranch, ...]
    """One operative overlay per lakiehdotus law (empty for a non-HE document)."""
    reasoning_root: SourceDocumentNode
    """The perustelut (esityöt) subtree — interpretive attachment, non-operative."""
    authority_status: ProposalAuthorityStatus
    replay_authorized: bool = False
    findings: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.proposal_id:
            raise ValueError("ProposalPackage.proposal_id must be non-empty")
        if self.replay_authorized:
            raise ValueError(
                "ProposalPackage.replay_authorized must be False until the proposal "
                "is enacted (via the enactment lane, not this carrier)."
            )
        if not isinstance(self.branches, tuple):
            raise TypeError("ProposalPackage.branches must be a tuple")
        if not all(isinstance(b, ConditionalBranch) for b in self.branches):
            raise TypeError(
                "ProposalPackage.branches must be a tuple of ConditionalBranch"
            )
        if not isinstance(self.reasoning_root, SourceDocumentNode):
            raise TypeError("ProposalPackage.reasoning_root must be a SourceDocumentNode")
