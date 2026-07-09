"""Workflow-LLM adjudicator — reconcile many candidates → composed node + tier.

Unit tests drive a fake transport (``_chat`` overridden) so the reconcile /
corroboration / tier / iteration / truncation logic is pinned without a server.
One ``network`` test runs the real llama.cpp server at :8080 end to end.
"""
from __future__ import annotations

from typing import List

import pytest

from lawvm.core.source_document import (
    Adjudication,
    AdjudicationMethod,
    AssuranceTier,
    ExtractionAssertion,
    SourceAnchor,
)
from lawvm.finland.llm_backends.llm_adjudicator import (
    AdjudicationTruncated,
    LlmWorkflowAdjudicator,
)

_DIGEST = "a" * 64
_REGION = SourceAnchor(artifact_digest=_DIGEST, locator="page=3;block=1", page_num=3)


def _cand(text: str, run_id: str, kind: str = "paragraph") -> ExtractionAssertion:
    return ExtractionAssertion(
        run_id=run_id,
        fragment_kind=kind,
        text=text,
        anchor=_REGION,
    )


class _ScriptedAdjudicator(LlmWorkflowAdjudicator):
    """LlmWorkflowAdjudicator with a scripted ``_chat`` — no network."""

    def __init__(self, response: str, *, verify_pass: bool = False, raise_truncated: bool = False) -> None:
        super().__init__(verify_pass=verify_pass)
        self._response = response
        self._raise_truncated = raise_truncated
        self.calls: List[str] = []

    def _chat(self, system: str, user: str, *, region_locator: str) -> str:  # type: ignore[override]
        self.calls.append(user)
        if self._raise_truncated:
            raise AdjudicationTruncated(region_locator=region_locator, detail="test truncation")
        return self._response


def test_single_producer_is_passthrough_no_llm_call() -> None:
    adj = _ScriptedAdjudicator("unused")
    result = adj.adjudicate(_REGION, (_cand("only pdfplumber read", "native_pdf:1"),))
    assert result.assurance is AssuranceTier.SINGLE_WITNESS
    assert result.method is AdjudicationMethod.SINGLE_CANDIDATE
    assert adj.calls == []  # one witness: no reconciliation needed


def test_two_producers_agree_reaches_multi_witness() -> None:
    resp = "CORROBORATE: native_pdf vision\nTEXT:\nLaki muuttamisesta"
    adj = _ScriptedAdjudicator(resp)
    result = adj.adjudicate(
        _REGION,
        (
            _cand("Laki muuttamisesta", "native_pdf:1"),
            _cand("Laki muuttamiseta", "vision:2"),  # noisy variant
        ),
    )
    assert result.method is AdjudicationMethod.MULTI_CANDIDATE_RECONCILED
    assert result.assurance is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    assert result.node.text == "Laki muuttamisesta"
    assert set(result.corroborating_producers) == {"native_pdf", "vision"}
    assert result.node.assurance_tier.admits_clean_text_state
    assert len(adj.calls) == 1


def test_model_cannot_conjure_a_witness() -> None:
    # The model names a producer that never read the region → it is ignored.
    resp = "CORROBORATE: native_pdf ghost_ocr\nTEXT:\ncomposed"
    adj = _ScriptedAdjudicator(resp)
    result = adj.adjudicate(
        _REGION,
        (_cand("a b c", "native_pdf:1"), _cand("a b d", "vision:2")),
    )
    # only native_pdf is a real corroborator → 1 witness → qualified, not clean.
    assert result.corroborating_producers == ("native_pdf",)
    assert result.assurance is AssuranceTier.SINGLE_WITNESS


def test_only_one_producer_agrees_stays_single_witness() -> None:
    resp = "CORROBORATE: native_pdf\nTEXT:\nthe native reading"
    adj = _ScriptedAdjudicator(resp)
    result = adj.adjudicate(
        _REGION,
        (_cand("the native reading", "native_pdf:1"), _cand("garbled vision", "vision:2")),
    )
    assert result.assurance is AssuranceTier.SINGLE_WITNESS


def test_empty_composed_text_parks_as_unadjudicated() -> None:
    resp = "CORROBORATE: native_pdf vision\nTEXT:\n   "
    adj = _ScriptedAdjudicator(resp)
    result = adj.adjudicate(
        _REGION,
        (_cand("x y z", "native_pdf:1"), _cand("x y z", "vision:2")),
    )
    assert result.assurance is AssuranceTier.UNADJUDICATED_PROPOSAL


def test_truncation_raises_not_silent() -> None:
    adj = _ScriptedAdjudicator("irrelevant", raise_truncated=True)
    with pytest.raises(AdjudicationTruncated):
        adj.adjudicate(
            _REGION,
            (_cand("a b c", "native_pdf:1"), _cand("a b d", "vision:2")),
        )


def test_iteration_composes_next_layer() -> None:
    layer0_resp = "CORROBORATE: native_pdf vision\nTEXT:\nfirst composed"
    adj0 = _ScriptedAdjudicator(layer0_resp)
    layer0 = adj0.adjudicate(
        _REGION,
        (_cand("first", "native_pdf:1"), _cand("first-ish", "vision:2")),
    )
    layer1_resp = "CORROBORATE: ocr\nTEXT:\nricher composed"
    adj1 = _ScriptedAdjudicator(layer1_resp)
    layer1 = adj1.adjudicate(_REGION, (_cand("richer", "ocr:3"),), prior=layer0)
    assert layer1.method is AdjudicationMethod.ITERATIVE_COMPOSED
    assert layer1.iteration == 1
    # corroborating producers accumulate across the composed layers.
    assert set(layer1.corroborating_producers) == {"native_pdf", "vision", "ocr"}
    assert isinstance(layer1, Adjudication)


def test_no_valid_candidates_is_unadjudicated() -> None:
    adj = _ScriptedAdjudicator("unused")
    result = adj.adjudicate(_REGION, (_cand("   ", "native_pdf:1"),))  # empty → invalid
    assert result.assurance is AssuranceTier.UNADJUDICATED_PROPOSAL
    assert adj.calls == []


# --------------------------------------------------------------------------- #
# Live end-to-end against the local llama.cpp server at :8080                  #
# --------------------------------------------------------------------------- #


@pytest.mark.network
def test_live_adjudication_reconciles_two_noisy_reads() -> None:
    adjudicator = LlmWorkflowAdjudicator(verify_pass=False, max_tokens=512)
    if not adjudicator.is_available():
        pytest.skip("no llama.cpp server at :8080")
    native = _cand("Laki oikeudenkäymiskaaren muuttamisesta", "native_pdf:1")
    vision = _cand("Laki oikeudenkaymiskaaren muuttamisesta.", "vision:2")  # OCR-noisy variant
    result = adjudicator.adjudicate(_REGION, (native, vision))
    # The model should compose a non-empty reading and find the two independent
    # reads corroborate → a clean, multi-witness-adjudicated node.
    assert result.node.text.strip()
    assert "muuttamisesta" in result.node.text.lower()
    assert len(result.corroborating_producers) >= 1
    if len(result.corroborating_producers) >= 2:
        assert result.assurance is AssuranceTier.MULTI_WITNESS_ADJUDICATED
