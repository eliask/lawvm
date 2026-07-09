"""Adjudication — producer-neutral composition of many extraction candidates.

The high-assurance extraction layer is an ADJUDICATOR (an LLM workflow, a human
tool) that ingests SEVERAL candidate extractions of a region — mechanical
(pdfplumber, OCR, layout) and model (vision) alike, plus a prior adjudicated
layer — reconciles their disagreements, and composes a higher-quality node. It
MAY iterate: each pass composes the next, better layer from the prior one plus a
fresh look. See ``notes_internal/pro_on_unstructured_input_ingest.md``.

The dichotomy "deterministic extractor = truth, model = proposal" is FALSE: every
producer is a noisy input. Assurance is a property of adjudication, not of which
producer read the bytes (``ir.AssuranceTier``).

CORE JUDGES NOTHING SEMANTIC. Whether candidates actually corroborate is the
adjudicator's judgment (it has the model / the human); it REPORTS the count of
independent corroborating producers, and ``assurance_for`` maps that count +
review to a tier. No text heuristics, no privileged producer, no domain
knowledge live in this waist (AGENTS.md §0, §2.4).

Discipline (AGENTS.md §1.9): typed frozen carriers; tuple fields, never lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional, Protocol, Tuple

from typing_extensions import override

from lawvm.core.source_document.anchors import SourceAnchor
from lawvm.core.source_document.extraction import ExtractionAssertion
from lawvm.core.source_document.ir import AssuranceTier, SourceDocumentNode


class AdjudicationMethod(Enum):
    """How an adjudicator arrived at its composed node."""

    SINGLE_CANDIDATE = "single_candidate"
    """Only one candidate covered the region — nothing to reconcile (single-witness)."""
    MULTI_CANDIDATE_RECONCILED = "multi_candidate_reconciled"
    """The adjudicator reconciled >=2 independent candidates into one composed node."""
    ITERATIVE_COMPOSED = "iterative_composed"
    """Composed atop a prior ``Adjudication`` layer plus a fresh look (higher quality)."""
    HUMAN_REVIEWED = "human_reviewed"
    """A human confirmed / corrected the composed node."""

    @override
    def __str__(self) -> str:
        return self.value


def assurance_for(
    corroborating_producers: int,
    *,
    human_confirmed: bool = False,
    adjudicated: bool = False,
) -> AssuranceTier:
    """Map INDEPENDENT-corroboration count + review to a producer-neutral tier.

    The sole assurance policy — and the whole point of the reframe: assurance is
    independent corroboration, adjudication, and human review, NEVER which
    producer read the bytes. ``corroborating_producers`` is the count of DISTINCT
    producers the adjudicator found to agree (its judgment, not a core heuristic).

    * ``human_confirmed`` → ``HUMAN_CONFIRMED``.
    * ``adjudicated`` and >=2 corroborating producers → ``MULTI_WITNESS_ADJUDICATED``.
    * >=1 producer → ``SINGLE_WITNESS`` (qualified; a lone pdfplumber OR a lone
      vision read both land here — no privilege).
    * else → ``UNADJUDICATED_PROPOSAL`` (never a clean text-state).
    """
    if corroborating_producers < 0:
        raise ValueError("corroborating_producers must be >= 0")
    if human_confirmed:
        return AssuranceTier.HUMAN_CONFIRMED
    if adjudicated and corroborating_producers >= 2:
        return AssuranceTier.MULTI_WITNESS_ADJUDICATED
    if corroborating_producers >= 1:
        return AssuranceTier.SINGLE_WITNESS
    return AssuranceTier.UNADJUDICATED_PROPOSAL


@dataclass(frozen=True, slots=True)
class Adjudication:
    """One adjudicator's composition over the candidates covering a region.

    ``node`` is the composed output; its ``assurance_tier`` MUST equal
    ``assurance`` (the node cannot claim a tier the adjudication did not grant).
    Provenance: the candidate runs consumed and the DISTINCT producers found to
    corroborate, the method, and the iteration index (0 = first layer; >0 =
    composed atop a prior ``Adjudication``). ``rationale`` is the adjudicator's
    short, human-readable account of how it reconciled the inputs.
    """

    node: SourceDocumentNode
    assurance: AssuranceTier
    method: AdjudicationMethod
    source_candidate_run_ids: Tuple[str, ...]
    corroborating_producers: Tuple[str, ...]
    adjudicator_id: str
    iteration: int = 0
    rationale: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.node, SourceDocumentNode):
            raise TypeError("Adjudication.node must be a SourceDocumentNode")
        if not isinstance(self.assurance, AssuranceTier):
            raise TypeError("Adjudication.assurance must be an AssuranceTier")
        if self.node.assurance_tier is not self.assurance:
            raise ValueError(
                "Adjudication.node.assurance_tier must equal Adjudication.assurance — "
                "the composed node may not claim a tier the adjudication did not grant"
            )
        if not isinstance(self.method, AdjudicationMethod):
            raise TypeError("Adjudication.method must be an AdjudicationMethod")
        if not self.adjudicator_id:
            raise ValueError("Adjudication.adjudicator_id must be non-empty")
        if self.iteration < 0:
            raise ValueError("Adjudication.iteration must be >= 0")
        if not isinstance(self.source_candidate_run_ids, tuple):
            raise TypeError("Adjudication.source_candidate_run_ids must be a tuple")
        if not isinstance(self.corroborating_producers, tuple):
            raise TypeError("Adjudication.corroborating_producers must be a tuple")


class Adjudicator(Protocol):
    """Composes candidates into an adjudicated node — an LLM workflow, a human tool.

    Producer-neutral by construction: it receives ALL candidates covering a
    region (mechanical + model) and MAY receive a prior ``Adjudication`` to
    compose the next, higher-quality layer (iteration). It reports the DISTINCT
    corroborating producers it found; ``assurance_for`` derives the tier. The
    adjudicator never stamps assurance by fiat, and privileges no producer.

    Implementations own the semantic judgment (reconciling conflicting reads,
    composing structure). They MUST anchor every composed node to a concrete
    region and MUST NOT invent content absent from all candidates + the region.
    """

    adjudicator_id: str

    def adjudicate(
        self,
        region: SourceAnchor,
        candidates: Tuple[ExtractionAssertion, ...],
        *,
        prior: Optional["Adjudication"] = None,
    ) -> "Adjudication":
        """Reconcile ``candidates`` (optionally atop ``prior``) into one ``Adjudication``."""
        ...
