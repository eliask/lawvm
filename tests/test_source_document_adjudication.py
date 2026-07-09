"""Adjudication layer — producer-neutral composition of many candidates.

Pins the reframed model (the pdfplumber-vs-model dichotomy is false): assurance
is a property of ADJUDICATION over independent candidates, not of which producer
read the bytes. A reference ``Adjudicator`` here (deterministic, no LLM) is the
template the real workflow-LLM adjudicator implements — it consumes ALL
candidates, reports the DISTINCT corroborating producers, and lets
``assurance_for`` set the tier. It never stamps assurance by fiat, never
privileges a producer.
"""
from __future__ import annotations

from typing import Optional, Tuple

from lawvm.core.source_document import (
    Adjudication,
    AdjudicationMethod,
    AssuranceTier,
    ExtractionAssertion,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
    assurance_for,
    is_structurally_valid,
)

_DIGEST = "a" * 64


def _candidate(text: str, run_id: str, kind: str = "paragraph") -> ExtractionAssertion:
    return ExtractionAssertion(
        run_id=run_id,
        fragment_kind=kind,
        text=text,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=1", page_num=1),
    )


def _producer_of(run_id: str) -> str:
    """Producer id encoded as the run_id prefix (``<producer>:...``)."""
    return run_id.split(":", 1)[0]


class ReferenceAdjudicator:
    """A deterministic reference ``Adjudicator`` — the workflow-LLM's template.

    Real adjudication is an LLM workflow reconciling conflicting reads; this
    stand-in reconciles mechanically (longest structurally-valid candidate wins)
    but obeys the same contract: drop malformed candidates, count DISTINCT
    corroborating producers, and derive the tier via ``assurance_for`` — never
    stamping assurance, never privileging a producer.
    """

    adjudicator_id = "reference-adjudicator-v0"

    def adjudicate(
        self,
        region: SourceAnchor,
        candidates: Tuple[ExtractionAssertion, ...],
        *,
        prior: Optional[Adjudication] = None,
    ) -> Adjudication:
        valid = tuple(c for c in candidates if is_structurally_valid(c))
        run_ids = tuple(c.run_id for c in valid)
        producers = tuple(sorted({_producer_of(c.run_id) for c in valid}))
        if prior is not None:
            method = AdjudicationMethod.ITERATIVE_COMPOSED
            iteration = prior.iteration + 1
            # composing atop a prior layer counts the prior's producers too
            producers = tuple(sorted(set(producers) | set(prior.corroborating_producers)))
        elif len(producers) >= 2:
            method = AdjudicationMethod.MULTI_CANDIDATE_RECONCILED
            iteration = 0
        else:
            method = AdjudicationMethod.SINGLE_CANDIDATE
            iteration = 0
        adjudicated = method in (
            AdjudicationMethod.MULTI_CANDIDATE_RECONCILED,
            AdjudicationMethod.ITERATIVE_COMPOSED,
        )
        assurance = assurance_for(len(producers), adjudicated=adjudicated)
        # compose: longest valid text is the reconciled body (a stand-in policy)
        best = max(valid, key=lambda c: len(c.text), default=None)
        node = SourceDocumentNode(
            kind=SourceDocumentNodeKind.PARAGRAPH,
            assurance_tier=assurance,
            anchor=region,
            text=best.text if best else "",
        )
        return Adjudication(
            node=node,
            assurance=assurance,
            method=method,
            source_candidate_run_ids=run_ids,
            corroborating_producers=producers,
            adjudicator_id=self.adjudicator_id,
            iteration=iteration,
        )


_REGION = SourceAnchor(artifact_digest=_DIGEST, locator="page=1", page_num=1)


def test_single_candidate_is_single_witness() -> None:
    adj = ReferenceAdjudicator().adjudicate(_REGION, (_candidate("only read", "native_pdf:1"),))
    assert adj.assurance is AssuranceTier.SINGLE_WITNESS
    assert adj.method is AdjudicationMethod.SINGLE_CANDIDATE
    assert not adj.node.assurance_tier.admits_clean_text_state


def test_two_independent_producers_reconciled_is_clean() -> None:
    # A mechanical read and a model read of the same region, reconciled → clean.
    candidates = (
        _candidate("Pykalan 5 teksti kokonaisuudessaan", "native_pdf:1"),
        _candidate("Pykälän 5 teksti kokonaisuudessaan tarkistettuna", "vision:2"),
    )
    adj = ReferenceAdjudicator().adjudicate(_REGION, candidates)
    assert adj.method is AdjudicationMethod.MULTI_CANDIDATE_RECONCILED
    assert adj.assurance is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    assert adj.node.assurance_tier.admits_clean_text_state
    assert set(adj.corroborating_producers) == {"native_pdf", "vision"}


def test_producer_neutrality_swap_is_identical() -> None:
    # Which producer is "mechanical" vs "model" makes no difference to assurance.
    a = ReferenceAdjudicator().adjudicate(
        _REGION, (_candidate("x same words here now", "native_pdf:1"), _candidate("x same words here now too", "vision:2"))
    )
    b = ReferenceAdjudicator().adjudicate(
        _REGION, (_candidate("x same words here now", "vision:1"), _candidate("x same words here now too", "native_pdf:2"))
    )
    assert a.assurance is b.assurance is AssuranceTier.MULTI_WITNESS_ADJUDICATED


def test_two_candidates_from_the_same_producer_are_not_two_witnesses() -> None:
    # Two reads from the SAME producer are one witness, not corroboration.
    candidates = (_candidate("read one", "vision:1"), _candidate("read two longer", "vision:2"))
    adj = ReferenceAdjudicator().adjudicate(_REGION, candidates)
    assert adj.corroborating_producers == ("vision",)
    assert adj.assurance is AssuranceTier.SINGLE_WITNESS


def test_malformed_candidate_is_dropped_before_adjudication() -> None:
    candidates = (
        _candidate("real text", "native_pdf:1"),
        _candidate("   ", "vision:2"),  # empty → structurally invalid
    )
    adj = ReferenceAdjudicator().adjudicate(_REGION, candidates)
    # Only the native candidate survives → single witness, not multi.
    assert adj.corroborating_producers == ("native_pdf",)
    assert adj.assurance is AssuranceTier.SINGLE_WITNESS


def test_iteration_composes_a_higher_layer() -> None:
    adjudicator = ReferenceAdjudicator()
    layer0 = adjudicator.adjudicate(_REGION, (_candidate("first pass text", "native_pdf:1"),))
    layer1 = adjudicator.adjudicate(
        _REGION, (_candidate("second pass richer text", "vision:2"),), prior=layer0
    )
    assert layer1.method is AdjudicationMethod.ITERATIVE_COMPOSED
    assert layer1.iteration == 1
    assert set(layer1.corroborating_producers) == {"native_pdf", "vision"}
    assert layer1.assurance is AssuranceTier.MULTI_WITNESS_ADJUDICATED
