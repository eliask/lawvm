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
    StatuteLifecycle,
    StatuteLifecycleFinding,
    StatuteLifecycleUnverifiable,
    default_provision_present,
    detect_broken,
    detect_statute_lifecycle_broken,
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


def test_no_citation_start_is_unavailable_not_broken() -> None:
    """Open citation start: no temporal anchor for "existed-when-cited", so the
    as-of-citation tree cannot be materialized -> BrokenCheckUnavailable, never a
    finding. The detector must not reuse the current tree as the cited tree (doing
    so would either fabricate a verdict or, when the target is absent now, fire a
    false NEVER_EXISTED).
    """
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
    assert len(findings) == 1
    f = findings[0]
    assert isinstance(f, BrokenCheckUnavailable)
    assert not isinstance(f, BrokenReferenceFinding)
    assert f.unavailable_for == "cited"
    assert f.as_of is None
    # Only the current tree is materialized; no as-of-citation replay attempted.
    assert calls == [NOW]


def test_no_citation_start_with_target_absent_now_is_unavailable_not_never_existed() -> None:
    """BITE: target absent from the current tree AND no citing-date anchor.

    The old code aliased ``cited_tree = current_tree`` when ``cited_on is None``,
    so ``existed_when_cited`` and ``present_now`` were computed against the SAME
    tree. With the target absent now, both were False and the ``not
    existed_when_cited`` arm fired a false ``NEVER_EXISTED`` BROKEN finding — a
    verdict the detector had zero evidence for (the target could have existed when
    cited and been repealed/renumbered since). The fix emits
    ``BrokenCheckUnavailable`` instead.
    """
    target = ProvisionRef(statute_id="200/2000", section_label="9")

    def tree_as_of(statute_id: str, on: date) -> Optional[IRNode]:
        # Current tree carries §5/§6 but NOT the cited §9.
        return _tree_with_sections("5", "6")

    findings = detect_broken(
        [_mention(target, cited_start=None)],
        tree_as_of=tree_as_of,
        provision_present=_present,
        current_as_of=NOW,
    )
    assert len(findings) == 1
    f = findings[0]
    # The fix: undetermined, NOT a (false) NEVER_EXISTED BROKEN finding.
    assert isinstance(f, BrokenCheckUnavailable)
    assert not isinstance(f, BrokenReferenceFinding)
    assert f.unavailable_for == "cited"
    assert f.as_of is None
    # Guard against regression to the old aliasing verdict.
    assert not any(
        isinstance(x, BrokenReferenceFinding)
        and x.reason is BrokenReason.NEVER_EXISTED
        for x in findings
    )


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


# ---------------------------------------------------------------------------
# Statute-lifecycle (registry/lifecycle-driven) detector
# ---------------------------------------------------------------------------


def _lifecycle_table(table: dict[str, StatuteLifecycle]):
    """A LifecycleLookup over a dict; unknown ids -> known=False (fail-loud)."""

    def _lookup(statute_id: str) -> StatuteLifecycle:
        return table.get(
            statute_id, StatuteLifecycle(valid_from=None, valid_to=None, known=False)
        )

    return _lookup


def test_lifecycle_target_repealed_before_citing_is_broken() -> None:
    """Cited act's valid_to is on/before the citing date -> TARGET_STATUTE_REPEALED."""
    target = ProvisionRef(statute_id="200/2000", section_label="6")
    # Citation written 2015-01-01 but the target act was repealed 2010-01-01.
    table = {
        "200/2000": StatuteLifecycle(
            valid_from=date(2000, 1, 1), valid_to=date(2010, 1, 1)
        )
    }
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table(table),
    )
    assert len(results) == 1
    f = results[0]
    assert isinstance(f, StatuteLifecycleFinding)
    assert f.reason is BrokenReason.TARGET_STATUTE_REPEALED
    assert f.cited_on == date(2015, 1, 1)
    assert f.target_window == (date(2000, 1, 1), date(2010, 1, 1))
    assert f.source_span is not None


def test_lifecycle_target_in_force_at_citing_is_not_broken() -> None:
    """Cited act repealed AFTER the citing date -> still in force when cited -> no finding."""
    target = ProvisionRef(statute_id="200/2000", section_label="6")
    table = {
        "200/2000": StatuteLifecycle(
            valid_from=date(2000, 1, 1), valid_to=date(2020, 1, 1)
        )
    }
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table(table),
    )
    assert results == []


def test_lifecycle_open_valid_to_is_in_force_not_broken() -> None:
    """An open valid_to (no repeal date on record) means in force, not unknown."""
    target = ProvisionRef(statute_id="200/2000", section_label="6")
    table = {"200/2000": StatuteLifecycle(valid_from=date(2000, 1, 1), valid_to=None)}
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table(table),
    )
    assert results == []


def test_lifecycle_target_not_yet_in_force_is_broken() -> None:
    """Cited act's valid_from is after the citing date -> NOT_YET_IN_FORCE."""
    target = ProvisionRef(statute_id="900/2020", section_label="1")
    table = {"900/2020": StatuteLifecycle(valid_from=date(2020, 1, 1), valid_to=None)}
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table(table),
    )
    assert len(results) == 1
    f = results[0]
    assert isinstance(f, StatuteLifecycleFinding)
    assert f.reason is BrokenReason.TARGET_STATUTE_NOT_YET_IN_FORCE


def test_lifecycle_unknown_lifecycle_is_unverifiable_never_broken() -> None:
    """No registry entry -> StatuteLifecycleUnverifiable, never a false BROKEN."""
    target = ProvisionRef(statute_id="404/1999", section_label="1")
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table({}),
    )
    assert len(results) == 1
    u = results[0]
    assert isinstance(u, StatuteLifecycleUnverifiable)
    assert u.unavailable_for == "target_lifecycle"


def test_lifecycle_no_citing_date_is_unverifiable() -> None:
    """No citing-date anchor -> cannot compare -> unverifiable, not broken."""
    target = ProvisionRef(statute_id="200/2000", section_label="6")
    table = {
        "200/2000": StatuteLifecycle(
            valid_from=date(2000, 1, 1), valid_to=date(2010, 1, 1)
        )
    }
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=None)],
        lifecycle_of=_lifecycle_table(table),
    )
    assert len(results) == 1
    u = results[0]
    assert isinstance(u, StatuteLifecycleUnverifiable)
    assert u.unavailable_for == "citing_date"


def test_lifecycle_self_reference_skipped() -> None:
    """A target == source self-ref is not a cross-statute lifecycle question."""
    # _mention's source is 100/2010; target the same statute.
    target = ProvisionRef(statute_id="100/2010", section_label="2")
    table = {
        "100/2010": StatuteLifecycle(
            valid_from=date(2010, 1, 1), valid_to=date(2012, 1, 1)
        )
    }
    results = detect_statute_lifecycle_broken(
        [_mention(target, cited_start=date(2015, 1, 1))],
        lifecycle_of=_lifecycle_table(table),
    )
    assert results == []
