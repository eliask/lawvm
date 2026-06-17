"""Tests for the bitemporal BROKEN-reference detector.

Uses synthetic in-memory IR trees and a fake ``tree_as_of`` / ``provision_present``
so the detector is exercised without the heavy replay/materialization engine.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from lawvm.core.ir import IRNode
from lawvm.core.reference_mention import (
    CiteConfidence,
    CiteKind,
    ProvisionRef,
    ReferenceMention,
    SourceSpan,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.references.broken_detection import (
    BrokenCheckUnavailable,
    BrokenReason,
    BrokenReferenceFinding,
    default_provision_present,
    detect_broken,
)

CITED_ON = date(2015, 1, 1)
NOW = date(2024, 1, 1)


# ---------------------------------------------------------------------------
# Synthetic tree helpers
# ---------------------------------------------------------------------------


def _section(label: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label)


def _tree_with_sections(*labels: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=tuple(_section(label) for label in labels),
    )


def _empty_tree() -> IRNode:
    """A repealed-statute placeholder: materializes but carries no sections."""
    return IRNode(kind=IRNodeKind.BODY)


def _mention(
    target: ProvisionRef,
    *,
    cited_start: Optional[date] = CITED_ON,
    confidence: CiteConfidence = CiteConfidence.EXACT,
) -> ReferenceMention:
    return ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="100/2010", section_label="1"),
        target_provision_ref=target,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=confidence,
        phrase_lemma="ref_element",
        source_span=SourceSpan(source_file="src.xml", byte_offset=10, byte_len=5),
        valid_at_interval=(cited_start, None),
        edge_subtype="CITES",
    )


def _present(tree: IRNode, ref: ProvisionRef) -> bool:
    """Fake provision-present: section label is in the tree's SECTION children."""
    labels = {c.label for c in tree.children if c.kind is IRNodeKind.SECTION}
    if not ref.section_label:
        return True
    return ref.section_label in labels


# ---------------------------------------------------------------------------
# Case 1: existed-then-repealed -> BROKEN / repealed_since
# ---------------------------------------------------------------------------


def test_existed_then_repealed_is_broken_repealed_since() -> None:
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        assert statute_id == "200/2000"
        if on == CITED_ON:
            return _tree_with_sections("5", "6")  # §5 existed when cited
        # current tree: statute repealed wholesale (empty placeholder)
        return _empty_tree()

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenReferenceFinding)
    assert f.reason is BrokenReason.REPEALED_SINCE
    assert f.target == target
    assert f.detected_interval == (CITED_ON, NOW)
    assert f.source_span is not None


def test_existed_then_provision_moved_is_renumbered_since() -> None:
    """Statute still has other sections, but the cited section is gone -> renumber."""
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        if on == CITED_ON:
            return _tree_with_sections("5", "6")
        # current tree: §5 gone but §6/§7 remain (statute alive, provision moved)
        return _tree_with_sections("6", "7")

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenReferenceFinding)
    assert f.reason is BrokenReason.RENUMBERED_SINCE


# ---------------------------------------------------------------------------
# Case 2: stable target -> no finding
# ---------------------------------------------------------------------------


def test_stable_target_yields_no_finding() -> None:
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return _tree_with_sections("5", "6")  # present at both times

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert findings == []


# ---------------------------------------------------------------------------
# Case 3: never existed at valid_at -> never_existed
# ---------------------------------------------------------------------------


def test_never_existed_at_valid_at() -> None:
    target = ProvisionRef(statute_id="200/2000", section_label="9")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        if on == CITED_ON:
            return _tree_with_sections("5", "6")  # no §9 when cited
        return _tree_with_sections("5", "6", "9")  # §9 added later

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenReferenceFinding)
    assert f.reason is BrokenReason.NEVER_EXISTED
    assert f.detected_interval == (CITED_ON, NOW)


# ---------------------------------------------------------------------------
# Case 4: tree_as_of None -> BrokenCheckUnavailable (NOT a false BROKEN)
# ---------------------------------------------------------------------------


def test_current_tree_unavailable_emits_unavailable_not_broken() -> None:
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        return None  # cannot materialize anything

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenCheckUnavailable)
    assert not isinstance(f, BrokenReferenceFinding)
    assert f.unavailable_for == "current"
    assert f.as_of == NOW


def test_cited_tree_unavailable_emits_unavailable_not_broken() -> None:
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        if on == CITED_ON:
            return None  # can't materialize the as-of-citation tree
        return _tree_with_sections("5")

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )

    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenCheckUnavailable)
    assert f.unavailable_for == "cited"
    assert f.as_of == CITED_ON


# ---------------------------------------------------------------------------
# Skips: non-resolved mentions and missing inputs
# ---------------------------------------------------------------------------


def test_unresolved_and_open_mentions_are_skipped() -> None:
    open_mention = ReferenceMention(
        source_provision_ref=ProvisionRef(statute_id="100/2010", section_label="1"),
        target_provision_ref=None,
        cite_kind=CiteKind.CROSS_STATUTE,
        cite_confidence=CiteConfidence.UNRESOLVED,
        phrase_lemma="ref_element",
        source_span=None,
        valid_at_interval=(CITED_ON, None),
        edge_subtype=None,
    )

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        raise AssertionError("tree_as_of must not be called for skipped mentions")

    findings = detect_broken(
        [open_mention],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )
    assert findings == []


def test_no_citation_start_falls_back_to_current_tree() -> None:
    """Open citation start: only the present-now check applies (no false NEVER_EXISTED)."""
    target = ProvisionRef(statute_id="200/2000", section_label="5")

    calls: list[date] = []

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        calls.append(on)
        return _tree_with_sections("5")

    findings = detect_broken(
        [_mention(target, cited_start=None)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )
    # present at NOW, no cited-on anchor -> no finding, and only NOW materialized.
    assert findings == []
    assert calls == [NOW]


def test_internal_target_without_statute_is_skipped() -> None:
    target = ProvisionRef(statute_id="", section_label="3")  # internal, no statute

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        raise AssertionError("must not materialize for statute-less target")

    findings = detect_broken(
        [_mention(target)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )
    assert findings == []


# ---------------------------------------------------------------------------
# default_provision_present adapter against real find_all
# ---------------------------------------------------------------------------


def test_default_provision_present_section_subsection_item() -> None:
    tree = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="2",
                        children=(IRNode(kind=IRNodeKind.ITEM, label="a"),),
                    ),
                ),
            ),
        ),
    )

    assert default_provision_present(tree, ProvisionRef(statute_id="x", section_label="5"))
    assert default_provision_present(
        tree, ProvisionRef(statute_id="x", section_label="5", subsection_num=2)
    )
    assert default_provision_present(
        tree,
        ProvisionRef(statute_id="x", section_label="5", subsection_num=2, item_label="a"),
    )
    # statute-level ref: present iff tree materialized
    assert default_provision_present(tree, ProvisionRef(statute_id="x"))

    # absent levels
    assert not default_provision_present(tree, ProvisionRef(statute_id="x", section_label="9"))
    assert not default_provision_present(
        tree, ProvisionRef(statute_id="x", section_label="5", subsection_num=7)
    )
    assert not default_provision_present(
        tree,
        ProvisionRef(statute_id="x", section_label="5", subsection_num=2, item_label="z"),
    )
