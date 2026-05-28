"""Test detection of source pathology observations.

Regression tests for BASE_UNNUMBERED_PARAGRAPH_PEER and LABEL_EID_DIVERGENCE.
"""

from lawvm.corpus_store import get_corpus_store
from lawvm.finland.grafter import XMLStatute
from lawvm.finland.xml_ir import (
    detect_unnumbered_paragraph_peers,
    detect_label_eid_divergence,
)
from lawvm.core.semantic_types import IRNodeKind
from tests.corpus_pin_helpers import pinned_replay


def test_2013_331_unnumbered_peer_detected():
    """Verify that 2013/331 § 3 / 1 mom. has unnumbered para_2 detected."""
    cs = get_corpus_store()
    xml_bytes = cs.read_source("2013/331")
    assert xml_bytes is not None
    doc = XMLStatute(xml_bytes)

    # Navigate to section:3/subsection:1 by finding any section with num 3
    def find_section_3(node):
        if (node.kind == IRNodeKind.SECTION and node.label == "3"):
            return node
        for child in node.children:
            result = find_section_3(child)
            if result:
                return result
        return None

    section3 = find_section_3(doc.ir)
    assert section3 is not None, "Could not find section 3"

    # Get first subsection
    subsec1 = next((c for c in section3.children if c.kind == IRNodeKind.SUBSECTION), None)
    assert subsec1 is not None
    assert subsec1.kind == IRNodeKind.SUBSECTION

    # Detect unnumbered paragraph peers
    violations = detect_unnumbered_paragraph_peers(
        subsec1,
        section_address="chapter:1/section:3"
    )

    # Should detect para_2 (unnumbered, with siblings that are numbered)
    assert len(violations) > 0, "Expected to find unnumbered peer para_2"
    eIds = [v[0] for v in violations]
    assert any("para_2" in eId for eId in eIds), f"Expected para_2 in violations, got {eIds}"


def test_2013_331_label_eid_divergence_detected():
    """Verify that 2013/331 § 3 / 1 mom. has label/eId mismatch detected."""
    cs = get_corpus_store()
    xml_bytes = cs.read_source("2013/331")
    assert xml_bytes is not None
    doc = XMLStatute(xml_bytes)

    # Navigate to section:3/subsection:1
    def find_section_3(node):
        if (node.kind == IRNodeKind.SECTION and node.label == "3"):
            return node
        for child in node.children:
            result = find_section_3(child)
            if result:
                return result
        return None

    section3 = find_section_3(doc.ir)
    assert section3 is not None

    # Get first subsection
    subsec1 = next((c for c in section3.children if c.kind == IRNodeKind.SUBSECTION), None)
    assert subsec1 is not None

    # Detect label/eId divergence
    divergences = detect_label_eid_divergence(
        subsec1,
        section_address="chapter:1/section:3"
    )

    # Should detect label='2' with eId='...para_3' (because para_2 was dropped)
    assert len(divergences) > 0, "Expected to find label/eId divergence"
    labels_and_eids = [(d[0], d[1]) for d in divergences]
    # Look for label=2 with eId containing para_3
    found = any(
        label == "2" and "para_3" in eId
        for label, eId in labels_and_eids
    )
    assert found, f"Expected label='2' with para_3 eId, got {labels_and_eids}"


def test_1974_412_clean_no_observations():
    """Verify that a clean statute (1974/412) has no pathology observations."""
    cs = get_corpus_store()
    xml_bytes = cs.read_source("1974/412")
    assert xml_bytes is not None
    doc = XMLStatute(xml_bytes)

    # Check all subsections for pathologies
    def walk_subsections(node):
        if node.kind == IRNodeKind.SUBSECTION:
            violations = detect_unnumbered_paragraph_peers(
                node,
                section_address="?"
            )
            divergences = detect_label_eid_divergence(
                node,
                section_address="?"
            )
            if violations or divergences:
                return (violations, divergences)
        for child in node.children:
            result = walk_subsections(child)
            if result:
                return result
        return None

    result = walk_subsections(doc.ir)
    assert result is None, f"Clean statute should have no pathologies, but got {result}"


def test_2013_331_base_observations_in_findings():
    """Verify that base observations (T1b) are threaded into ReplayResult.findings."""
    cs = get_corpus_store()
    m = pinned_replay("2013/331", mode="finlex_oracle", quiet=True, corpus=cs)

    # Verify base_observations are populated in ctx
    assert len(m.ctx.base_observations) == 13, (
        f"Expected 13 base observations in ctx, got {len(m.ctx.base_observations)}"
    )
    assert len(m.ctx.source_normalization_facts) == 1, (
        f"Expected 1 source normalization fact in ctx, got {len(m.ctx.source_normalization_facts)}"
    )

    # Verify findings include the base observations
    base_unnumbered = [
        f for f in m.findings if f.kind == "BASE_UNNUMBERED_PARAGRAPH_PEER"
    ]
    peer_reparent = [
        f for f in m.findings if f.kind == "BASE_UNNUMBERED_PEER_REPARENT"
    ]
    label_eid = [
        f for f in m.findings if f.kind == "LABEL_EID_DIVERGENCE"
    ]

    assert len(base_unnumbered) == 1, (
        f"Expected 1 BASE_UNNUMBERED_PARAGRAPH_PEER in findings, got {len(base_unnumbered)}"
    )
    assert len(peer_reparent) == 1, (
        f"Expected 1 BASE_UNNUMBERED_PEER_REPARENT in findings, got {len(peer_reparent)}"
    )
    assert len(label_eid) == 12, (
        f"Expected 12 LABEL_EID_DIVERGENCE in findings, got {len(label_eid)}"
    )

    # Verify detail fields are preserved
    unnumbered = base_unnumbered[0]
    assert unnumbered.detail.get("section_address") == "section:3/subsection:1"
    assert "para_2" in unnumbered.detail.get("eId", "")
    assert unnumbered.detail.get("intro_excerpt") == "kaatopaikkana ei kuitenkaan pidetä:"
    reparent = peer_reparent[0]
    assert reparent.detail.get("basis") == "profile_invalid"
    assert reparent.detail.get("path") == ("body:?", "hcontainer:?", "chapter:1", "section:3", "subsection:1")
    assert "wrap_up" in str(reparent.detail.get("explanation", "")).lower()

    # Verify label/eId findings have correct details
    first_label_eid = label_eid[0]
    assert first_label_eid.detail.get("section_address") == "section:3/subsection:1"
    assert "label" in first_label_eid.detail
    assert "eId" in first_label_eid.detail


def test_1974_412_clean_no_findings():
    """Verify that clean statute (1974/412) has no base observation findings."""
    cs = get_corpus_store()
    m = pinned_replay("1974/412", mode="finlex_oracle", quiet=True, corpus=cs)

    # Verify no base observations
    assert len(m.ctx.base_observations) == 0, (
        f"Expected 0 base observations for clean statute, got {len(m.ctx.base_observations)}"
    )

    # Verify findings don't include base observations
    base_unnumbered = [
        f for f in m.findings if f.kind == "BASE_UNNUMBERED_PARAGRAPH_PEER"
    ]
    peer_reparent = [
        f for f in m.findings if f.kind == "BASE_UNNUMBERED_PEER_REPARENT"
    ]
    label_eid = [
        f for f in m.findings if f.kind == "LABEL_EID_DIVERGENCE"
    ]

    assert len(base_unnumbered) == 0
    assert len(peer_reparent) == 0
    assert len(label_eid) == 0
