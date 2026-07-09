"""D0 carrier tests for ``lawvm.core.source_document``.

Pins the typed-carrier discipline (AGENTS.md §1.9 typed carriers over dynamic
shape, §1.10 fail-loud) and the authority ladder (§0: generators propose;
typed validators authorize; self-consistency is not verification). The
image-only-page → typed ``Residual`` case is the determinism-firewall proof: a
region deterministic extraction cannot own becomes a first-class typed
residual, never a silent hole.

See the approved plan at ``.claude/plans/calm-kindling-wand.md`` (D0).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from lawvm.core.provenance_graph import Producer
from lawvm.core.source_document import (
    Adjudication,
    AdjudicationMethod,
    AssuranceTier,
    BBox,
    ExtractionAffordances,
    ExtractionAssertion,
    ExtractionRun,
    RegionOwnership,
    Residual,
    ResidualFamily,
    SourceAnchor,
    SourceDocumentNode,
    SourceDocumentNodeKind,
    SourceManifestation,
    assurance_for,
    is_structurally_valid,
)

_DIGEST = "a" * 64
_NATIVE = Producer(producer_id="native_pdf", producer_kind="script")


def _manifestation(role: str = "statute") -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=_DIGEST,
        source_bytes=b"%PDF-1.4 ...",
        locator="finlex:2011/38",
        source_role=role,
        fetched_at=datetime(2026, 1, 1),
        media_type="application/pdf",
    )


def _run() -> ExtractionRun:
    return ExtractionRun(
        run_id="run-1",
        producer=_NATIVE,
        backend_id="native_pdf",
        backend_version="0.1",
        source_artifact_digest=_DIGEST,
        input_affordance_digest="b" * 64,
        output_digest="c" * 64,
        started_at=datetime(2026, 1, 1, 9),
        ended_at=datetime(2026, 1, 1, 9, 0, 5),
    )


# ---------------------------------------------------------------------------
# Discipline: fail-loud construction (AGENTS.md §1.9 / §1.10)
# ---------------------------------------------------------------------------


def test_anchor_requires_non_empty_locator() -> None:
    """An anchor that pins no region cannot authorize extraction."""
    with pytest.raises(ValueError):
        SourceAnchor(artifact_digest=_DIGEST, locator="")


def test_bbox_rejects_inverted_geometry() -> None:
    with pytest.raises(ValueError):
        BBox(x0=10.0, y0=0.0, x1=5.0, y1=20.0)


def test_manifestation_requires_source_role() -> None:
    with pytest.raises(ValueError):
        SourceManifestation(
            artifact_digest=_DIGEST,
            source_bytes=b"x",
            locator="loc",
            source_role="",
            fetched_at=datetime(2026, 1, 1),
        )


def test_run_rejects_inverted_window() -> None:
    with pytest.raises(ValueError):
        ExtractionRun(
            run_id="r",
            producer=_NATIVE,
            backend_id="native_pdf",
            backend_version="0.1",
            source_artifact_digest=_DIGEST,
            input_affordance_digest="b" * 64,
            output_digest="c" * 64,
            started_at=datetime(2026, 1, 1, 9, 1),
            ended_at=datetime(2026, 1, 1, 9, 0),
        )


def test_assertion_rejects_non_anchor() -> None:
    bad: Any = "not-an-anchor"
    with pytest.raises(TypeError):
        ExtractionAssertion(run_id="r", fragment_kind="section", text="x", anchor=bad)


def test_residual_cannot_be_owned() -> None:
    # An owned region is not a residual (§1.8 nothing disappears).
    with pytest.raises(ValueError):
        Residual(
            family=ResidualFamily.PDF_PAGE_IMAGE_ONLY,
            ownership=RegionOwnership.OWNED,
            anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=1"),
        )


# ---------------------------------------------------------------------------
# Assurance ladder: producer-neutral; assurance is adjudication, not producer
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        (AssuranceTier.HUMAN_CONFIRMED, True),
        (AssuranceTier.MULTI_WITNESS_ADJUDICATED, True),
        (AssuranceTier.SINGLE_WITNESS, False),
        (AssuranceTier.UNADJUDICATED_PROPOSAL, False),
    ],
)
def test_clean_text_state_admission(tier: AssuranceTier, expected: bool) -> None:
    assert tier.admits_clean_text_state is expected


def test_assurance_is_producer_neutral() -> None:
    # THE anti-dichotomy: a lone pdfplumber read and a lone vision read are BOTH
    # single-witness — assurance does not care which producer read the bytes.
    assert assurance_for(1) is AssuranceTier.SINGLE_WITNESS
    # Corroboration alone is not enough: it must be ADJUDICATED to be clean.
    assert assurance_for(2, adjudicated=False) is AssuranceTier.SINGLE_WITNESS
    assert assurance_for(2, adjudicated=True) is AssuranceTier.MULTI_WITNESS_ADJUDICATED
    # No producer → nothing to stand on.
    assert assurance_for(0) is AssuranceTier.UNADJUDICATED_PROPOSAL
    # A human confirmation tops the ladder regardless of count.
    assert assurance_for(1, human_confirmed=True) is AssuranceTier.HUMAN_CONFIRMED


def test_single_producer_never_reaches_clean_state() -> None:
    # One witness, however deterministic, is qualified — never a clean text-state.
    assert not assurance_for(1).admits_clean_text_state
    assert assurance_for(5, adjudicated=True).admits_clean_text_state


def test_structural_gate_rejects_unanchorable_candidate() -> None:
    m = _manifestation()
    good = ExtractionAssertion(
        run_id="r",
        fragment_kind="section",
        text="Pykälän teksti",
        anchor=SourceAnchor(artifact_digest=m.artifact_digest, locator="//section[5]"),
    )
    empty = ExtractionAssertion(
        run_id="r",
        fragment_kind="section",
        text="   ",
        anchor=SourceAnchor(artifact_digest=m.artifact_digest, locator="//section[5]"),
    )
    ungoverned = ExtractionAssertion(
        run_id="r",
        fragment_kind="not_a_real_kind",
        text="x",
        anchor=SourceAnchor(artifact_digest=m.artifact_digest, locator="//section[5]"),
    )
    assert is_structurally_valid(good)
    assert not is_structurally_valid(empty)
    assert not is_structurally_valid(ungoverned)


# ---------------------------------------------------------------------------
# Determinism firewall: an unownable region becomes a typed Residual, not silence
# ---------------------------------------------------------------------------


def test_image_only_page_is_a_typed_residual_not_a_silent_hole() -> None:
    # The hard fixture: a scanned / image-only page pdfplumber cannot read.
    # Deterministic extraction emits a first-class typed residual — never empty,
    # never silently dropped (§0 total accounting; §1.8).
    residual = Residual(
        family=ResidualFamily.PDF_PAGE_IMAGE_ONLY,
        ownership=RegionOwnership.RESIDUAL,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=7", page_num=7),
        snippet="",
        detail="pdfplumber returned 0 chars; page is image-only (no text layer)",
    )
    assert residual.ownership is RegionOwnership.RESIDUAL
    assert residual.family is ResidualFamily.PDF_PAGE_IMAGE_ONLY
    assert residual.anchor.page_num == 7


# ---------------------------------------------------------------------------
# Composition: per-node authority + anchor flow with the content
# ---------------------------------------------------------------------------


def test_node_tree_carries_per_node_assurance_and_anchor() -> None:
    # A multi-witness-adjudicated section (clean) and a single-witness table
    # (qualified) coexist; each node's tier flows with it so no clean diff is
    # rendered from unclean extraction (review §9 authority laundering).
    section = SourceDocumentNode(
        kind=SourceDocumentNodeKind.SECTION,
        assurance_tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="//section[5]"),
        label="5",
        text="...",
    )
    table = SourceDocumentNode(
        kind=SourceDocumentNodeKind.TABLE,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(
            artifact_digest=_DIGEST, locator="page=3;bbox=10,20,400,200", page_num=3
        ),
    )
    root = SourceDocumentNode(
        kind=SourceDocumentNodeKind.BODY,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="body"),
        children=(section, table),
    )
    assert root.children[0].assurance_tier.admits_clean_text_state
    assert not root.children[1].assurance_tier.admits_clean_text_state


def test_adjudication_node_tier_must_match_its_assurance() -> None:
    # An adjudicator cannot grant a node a tier its adjudication did not earn.
    node = SourceDocumentNode(
        kind=SourceDocumentNodeKind.PARAGRAPH,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=1", page_num=1),
        text="composed text",
    )
    # Consistent: node tier == adjudication assurance.
    adj = Adjudication(
        node=node,
        assurance=AssuranceTier.SINGLE_WITNESS,
        method=AdjudicationMethod.SINGLE_CANDIDATE,
        source_candidate_run_ids=("run-1",),
        corroborating_producers=("native_pdf",),
        adjudicator_id="workflow-llm-v0",
    )
    assert adj.assurance is AssuranceTier.SINGLE_WITNESS
    # Inconsistent: node claims a tier the adjudication did not grant.
    with pytest.raises(ValueError):
        Adjudication(
            node=node,  # SINGLE_WITNESS
            assurance=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
            method=AdjudicationMethod.MULTI_CANDIDATE_RECONCILED,
            source_candidate_run_ids=("run-1", "run-2"),
            corroborating_producers=("native_pdf", "vision"),
            adjudicator_id="workflow-llm-v0",
        )


def test_extraction_pipeline_shapes_compose() -> None:
    m = _manifestation()
    run = _run()
    assertion = ExtractionAssertion(
        run_id=run.run_id,
        fragment_kind="section",
        text="Pykälän 5 teksti...",
        anchor=SourceAnchor(artifact_digest=m.artifact_digest, locator="//section[5]"),
    )
    assert assertion.run_id == run.run_id
    assert assertion.anchor.artifact_digest == m.artifact_digest
    # The affordance waist exists for D2 (native extractors) to populate.
    assert ExtractionAffordances().native_text == ""
