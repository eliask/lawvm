"""Tests for ``core.citation_graph_totality_audit`` (D6 / §A7).

Per :file:`notes/LAWVM_AUDIT_REGISTRY_ROADMAP.md` D6 and the
``REFERENCE.UNCLASSIFIED_REFERENCE`` registry row — every emitted
``ReferenceMention`` MUST carry a typed classification:

* a self-terminal confidence (EXACT / STATUTE_ONLY / OPEN / UNRESOLVED) IS the
  classification — zero findings;
* a receipt-required confidence (AMBIGUOUS / APPROXIMATE / BROKEN) WITHOUT a
  companion ``ReferenceResolution`` over its surface → exactly one
  ``REFERENCE.UNCLASSIFIED_REFERENCE`` observation;
* a receipt-required confidence WITH a matching resolution → zero findings;
* deterministic mention-stream ordering; empty input → empty output.

Audit-plane-only contract: the function emits observations and never raises on
shape-valid input. ``Observation.kind`` is the registered FindingSpec code and
matches :data:`FINDING_REGISTRY`, so the anti-drift check at
``tests/test_finding_registry.py`` covers the wire-to-registry binding here.
"""

from __future__ import annotations

from lawvm.core.citation_graph_totality_audit import (
    REFERENCE_UNCLASSIFIED_REFERENCE,
    assert_citation_graph_totality,
)
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    ReferenceResolution,
    ReferenceResolutionStatus,
    ReferenceTargetSetSemantics,
    SourceSpan,
    compute_surface_expr_id,
)


_SRC = ProvisionRef(statute_id="ukpga/2020/1", section_label="3")
_TGT = ProvisionRef(statute_id="ukpga/1999/9", section_label="5")
_SPAN = SourceSpan(source_file="src.xml", byte_offset=10, byte_len=8)


def _mention(
    *,
    confidence: CiteConfidence,
    surface_text: str = "section 5 of the 1999 Act",
    target: ProvisionRef | None = _TGT,
    source_span: SourceSpan | None = _SPAN,
) -> ReferenceMention:
    # ReferenceMention requires a non-None target unless the confidence is one
    # of UNRESOLVED / BROKEN / OPEN; respect that invariant here.
    if confidence in (
        CiteConfidence.UNRESOLVED,
        CiteConfidence.BROKEN,
        CiteConfidence.OPEN,
    ):
        tgt = None
    else:
        tgt = target
    return ReferenceMention(
        source_provision_ref=_SRC,
        target_provision_ref=tgt,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=confidence,
        phrase_lemma="ref_element",
        source_span=source_span,
        valid_at_interval=(None, None),
        edge_subtype="CITES",
        surface_text=surface_text,
    )


def _resolution_for(mention: ReferenceMention) -> ReferenceResolution:
    """Build a ReferenceResolution receipt keyed to the mention's surface."""
    surface_id = compute_surface_expr_id(
        mention.surface_text, mention.source_span, "single"
    )
    return ReferenceResolution(
        surface_expr_id=surface_id,
        target_set=(),
        target_set_semantics=ReferenceTargetSetSemantics.NO_ENUMERABLE_EXTENSION,
        reference_status=ReferenceResolutionStatus.UNRESOLVED,
    )


# --------------------------------------------------------------------------- #
# Negative: a classified mention emits nothing.                               #
# --------------------------------------------------------------------------- #


def test_self_terminal_confidence_emits_zero_observations() -> None:
    """EXACT / STATUTE_ONLY / OPEN / UNRESOLVED are self-classifying."""
    for confidence in (
        CiteConfidence.EXACT,
        CiteConfidence.STATUTE_ONLY,
        CiteConfidence.OPEN,
        CiteConfidence.UNRESOLVED,
    ):
        findings = assert_citation_graph_totality(
            (_mention(confidence=confidence),),
            (),
            source_statute="ukpga/2020/1",
        )
        assert findings == (), (
            f"self-terminal confidence {confidence!r} must classify with no "
            f"companion receipt; got {findings}"
        )


def test_receipt_required_confidence_with_matching_resolution_emits_zero() -> None:
    """A finding-requiring confidence backed by a resolution is classified."""
    mention = _mention(confidence=CiteConfidence.AMBIGUOUS)
    findings = assert_citation_graph_totality(
        (mention,),
        (_resolution_for(mention),),
        source_statute="ukpga/2020/1",
    )
    assert findings == ()


# --------------------------------------------------------------------------- #
# Firing case — the load-bearing guard-liveness test.                          #
# --------------------------------------------------------------------------- #


def test_receipt_required_confidence_without_resolution_fires_one_observation() -> None:
    """A BROKEN mention with no companion resolution surfaces exactly one finding."""
    mention = _mention(confidence=CiteConfidence.BROKEN)
    findings = assert_citation_graph_totality(
        (mention,),
        (),
        source_statute="ukpga/2020/1",
    )
    assert len(findings) == 1, (
        "a receipt-required confidence with no resolution MUST surface exactly "
        f"one observation; got {findings}"
    )
    obs = findings[0]
    assert obs.kind == REFERENCE_UNCLASSIFIED_REFERENCE
    assert obs.stage == "surface_totality"
    assert obs.source_statute == "ukpga/2020/1"
    detail = obs.detail
    assert detail["source_statute_id"] == "ukpga/2020/1"
    assert detail["source_provision_ref"] == _SRC.serialized()
    assert detail["raw_cite_text"] == "section 5 of the 1999 Act"
    assert detail["cite_confidence"] == "broken"
    assert detail["reason"] == "receipt_required_confidence_without_resolution"
    assert detail["owner"] == "citation_graph_totality_audit"
    assert detail["source_span_file"] == "src.xml"
    assert detail["source_span_byte_offset"] == 10
    assert detail["source_span_len"] == 8


def test_ambiguous_and_approximate_also_require_a_receipt() -> None:
    """AMBIGUOUS and APPROXIMATE are receipt-required too — fire without one."""
    for confidence in (CiteConfidence.AMBIGUOUS, CiteConfidence.APPROXIMATE):
        findings = assert_citation_graph_totality(
            (_mention(confidence=confidence),),
            (),
            source_statute="ukpga/2020/1",
        )
        assert len(findings) == 1
        assert findings[0].detail["cite_confidence"] == confidence.value


# --------------------------------------------------------------------------- #
# Ordering + determinism + empty.                                              #
# --------------------------------------------------------------------------- #


def test_findings_are_in_mention_stream_order() -> None:
    """Mixed classified + unclassified mentions surface only the unclassified,
    in mention-stream order — no reorder, dedupe, or collapse."""
    classified = _mention(confidence=CiteConfidence.EXACT, surface_text="a")
    broken = _mention(confidence=CiteConfidence.BROKEN, surface_text="b")
    open_ref = _mention(confidence=CiteConfidence.OPEN, surface_text="c")
    ambiguous = _mention(confidence=CiteConfidence.AMBIGUOUS, surface_text="d")
    findings = assert_citation_graph_totality(
        (classified, broken, open_ref, ambiguous),
        (),
        source_statute="ukpga/2020/1",
    )
    assert [f.detail["raw_cite_text"] for f in findings] == ["b", "d"]
    assert all(
        f.kind == REFERENCE_UNCLASSIFIED_REFERENCE for f in findings
    )


def test_empty_input_returns_empty() -> None:
    """An empty mention stream returns zero observations without raising."""
    assert assert_citation_graph_totality((), ()) == ()
    assert assert_citation_graph_totality((), source_statute="x") == ()


def test_resolution_for_unrelated_surface_does_not_cover_mention() -> None:
    """A resolution keyed to a DIFFERENT surface does not classify the mention."""
    mention = _mention(confidence=CiteConfidence.BROKEN, surface_text="b")
    other = _mention(confidence=CiteConfidence.AMBIGUOUS, surface_text="OTHER")
    findings = assert_citation_graph_totality(
        (mention,),
        (_resolution_for(other),),
        source_statute="ukpga/2020/1",
    )
    assert len(findings) == 1, (
        "a resolution over an unrelated surface must NOT cover this mention"
    )


def test_mention_without_surface_text_is_receipt_less() -> None:
    """A receipt-required mention with no surface_text cannot be surface-joined
    and is therefore treated as unclassified (the §0 over-surfacing direction)."""
    mention = _mention(confidence=CiteConfidence.BROKEN, surface_text="")
    findings = assert_citation_graph_totality(
        (mention,),
        (),
        source_statute="ukpga/2020/1",
    )
    assert len(findings) == 1
