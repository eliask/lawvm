"""Hermetic tests for the FROZEN Track-A ingest interface carriers (§5.5).

Locks the frozen carrier shapes (``SpanRef`` / ``FreeformRegion`` /
``ConvergenceInfo`` / ``PageSimulacrum`` / ``DeFacsimileOp`` / ``DeFacsimileClaim``)
and the ``NodeMetadata`` ↔ ``attrs`` codec, incl. the CLOSED-vocabulary rejection
of an unknown namespaced key (Decision 9). No network / no model / no fixtures.
"""
from __future__ import annotations

import pytest

from lawvm.core.source_document.anchors import BBox, SourceAnchor
from lawvm.core.source_document.ir import (
    AssuranceTier,
    SourceDocumentNode,
    SourceDocumentNodeKind,
)
from lawvm.ingest.defacsimile import DeFacsimileClaim, DeFacsimileOp
from lawvm.ingest.metadata import (
    META_VERSION,
    MetadataVocabError,
    NodeMetadata,
    decode_metadata,
    encode_metadata,
)
from lawvm.ingest.simulacrum import (
    ConvergenceInfo,
    FreeformRegion,
    PageSimulacrum,
    SpanRef,
)

_DIGEST = "a" * 64


def _node(kind: SourceDocumentNodeKind, **attrs: str) -> SourceDocumentNode:
    return SourceDocumentNode(
        kind=kind,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="page=1", page_num=1),
        text="x",
        attrs=dict(attrs),
    )


# --------------------------------------------------------------------------- #
# New additive IR / source-document node kinds (freeform escape hatches).
# --------------------------------------------------------------------------- #
def test_new_freeform_node_kinds_exist_and_are_governed() -> None:
    assert SourceDocumentNodeKind.MATH_REGION.value == "math_region"
    assert SourceDocumentNodeKind.VERBATIM_REGION.value == "verbatim_region"
    # Additive: the enum still round-trips by value.
    assert SourceDocumentNodeKind("math_region") is SourceDocumentNodeKind.MATH_REGION
    assert SourceDocumentNodeKind("verbatim_region") is SourceDocumentNodeKind.VERBATIM_REGION


def test_lowering_maps_freeform_kinds_to_block() -> None:
    from lawvm.core.semantic_types import IRNodeKind
    from lawvm.ingest.lowering import source_document_to_ir_node

    root = SourceDocumentNode(
        kind=SourceDocumentNodeKind.WORK_ROOT,
        assurance_tier=AssuranceTier.SINGLE_WITNESS,
        anchor=SourceAnchor(artifact_digest=_DIGEST, locator="manifestation"),
        children=(
            _node(SourceDocumentNodeKind.MATH_REGION, **{"freeform.reason": "image_baked"}),
            _node(SourceDocumentNodeKind.VERBATIM_REGION, **{"freeform.reason": "garbled_source"}),
        ),
    )
    ir = source_document_to_ir_node(root)
    kinds = [c.kind for c in ir.children]
    assert kinds == [IRNodeKind.BLOCK, IRNodeKind.BLOCK]
    # Freeform facts are carried through in attrs (mirrors IMAGE_REGION precedent).
    assert ir.children[0].attrs["freeform.reason"] == "image_baked"


# --------------------------------------------------------------------------- #
# Simulacrum carriers (frozen).
# --------------------------------------------------------------------------- #
def test_simulacrum_carriers_construct_and_are_frozen() -> None:
    ref = SpanRef(page_num=3, node_path=(0, 2))
    freeform = FreeformRegion(
        node_path=(0, 2),
        kind="math",
        reason="image_baked",
        bbox=BBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
    )
    conv = ConvergenceInfo(
        rounds=2,
        round_hashes=("h0", "h1"),
        termination="fixpoint",
        gate_reasons=("freeform_region",),
        patches_total=3,
    )
    page = PageSimulacrum(
        page_num=3,
        nodes=(_node(SourceDocumentNodeKind.PARAGRAPH),),
        freeform=(freeform,),
        convergence=conv,
        assurance=AssuranceTier.SINGLE_WITNESS,
        raw_wire_digests=("d0", "d1"),
    )
    assert page.page_num == 3
    assert page.convergence.termination == "fixpoint"
    assert page.freeform[0].kind == "math"
    with pytest.raises((AttributeError, TypeError)):
        ref.page_num = 9  # ty: ignore[invalid-assignment]


# --------------------------------------------------------------------------- #
# De-facsimile claim carriers (frozen; carriers only).
# --------------------------------------------------------------------------- #
def test_defacsimile_ops_are_the_closed_set() -> None:
    assert {o.value for o in DeFacsimileOp} == {
        "drop_furniture",
        "dedup_seam",
        "rejoin",
        "reorder",
        "keep",
    }


def test_defacsimile_claim_construct_with_defaults() -> None:
    claim = DeFacsimileClaim(
        op=DeFacsimileOp.REJOIN,
        targets=(SpanRef(1, (0,)), SpanRef(2, (0,))),
        tier=AssuranceTier.MULTI_WITNESS_ADJUDICATED,
        corroborating_producers=("defacsimile_adjudicator", "affordance:margin_band"),
        absorbed=(SpanRef(2, (0, 0)),),
    )
    assert claim.method == "model_adjudicated"
    assert claim.rationale == ""
    assert claim.absorbed == (SpanRef(2, (0, 0)),)
    with pytest.raises((AttributeError, TypeError)):
        claim.op = DeFacsimileOp.KEEP  # ty: ignore[invalid-assignment]


# --------------------------------------------------------------------------- #
# NodeMetadata codec round-trip through attrs (Decision 9).
# --------------------------------------------------------------------------- #
def test_metadata_codec_round_trip() -> None:
    meta = NodeMetadata(
        band="body",
        col=1,
        indent=2,
        y_order=7,
        caps=True,
        ends_terminal=True,
        starts_lower=False,
        hyphen_tail=True,
        list_marker="a)",
        section_number="12 §",
        band_count=4,
        numeric=True,
        section_ref=True,
        furniture=False,
        freeform_reason="marginalia",
        producer="vision.v1",
        converged=True,
    )
    attrs = encode_metadata(meta)
    assert attrs["meta.v"] == str(META_VERSION)
    # The furniture hint uses hint.furniture, NOT role= (taken by images).
    assert "hint.furniture" not in attrs  # false flag not emitted
    assert "role" not in attrs
    assert decode_metadata(attrs) == meta


def test_metadata_encode_is_sparse_for_clean_node() -> None:
    attrs = encode_metadata(NodeMetadata())
    # A clean node emits only the version stamp — output-sparse by construction.
    assert attrs == {"meta.v": "1"}


def test_metadata_codec_ignores_non_metadata_attrs() -> None:
    attrs = {"meta.v": "1", "hint.numeric": "1", "assurance_tier": "single_witness",
             "image_locator": "parsed/x/0001.png", "rowspan": "2"}
    meta = decode_metadata(attrs)
    assert meta.numeric is True
    # Non-namespaced attrs are untouched / ignored, never rejected.
    assert meta.band is None


def test_metadata_furniture_hint_key() -> None:
    attrs = encode_metadata(NodeMetadata(furniture=True))
    assert attrs["hint.furniture"] == "1"
    assert decode_metadata(attrs).furniture is True


def test_metadata_reserved_v2_keys_tolerated() -> None:
    # v2 typography keys are reserved now — recognized (not rejected), carried opaque.
    attrs = {"meta.v": "1", "typo.font": "Times", "typo.size_class": "median"}
    meta = decode_metadata(attrs)  # must not raise
    assert meta.band is None


def test_metadata_rejects_unknown_namespaced_key() -> None:
    # A key under an owned namespace that is NOT in the closed vocab is fail-loud.
    with pytest.raises(MetadataVocabError):
        decode_metadata({"meta.v": "1", "geom.nonsense": "1"})
    with pytest.raises(MetadataVocabError):
        decode_metadata({"meta.v": "1", "hint.bogus": "1"})


def test_metadata_rejects_out_of_vocab_band_and_reason() -> None:
    with pytest.raises(MetadataVocabError):
        NodeMetadata(band="middle")
    with pytest.raises(MetadataVocabError):
        NodeMetadata(freeform_reason="scribbled")
