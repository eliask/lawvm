"""Exhaustive enumeration tests for LawVM's trusted kernels.

Unlike the hypothesis property-based tests (test_kernel_properties.py) that
sample randomly, these tests enumerate ALL possible inputs for small input
spaces. This provides certainty (not just statistical confidence) that
invariants hold for the tested domain.

Complement to:
  - test_kernel_properties.py (hypothesis random sampling)
  - test_stateful_properties.py (hypothesis stateful machines)

Run:
    uv run pytest tests/test_exhaustive_enumeration.py -v
"""

from __future__ import annotations

import itertools
from typing import Any, List, Literal, Sequence, cast

import pytest

from lawvm.core.ir import IRNode, LegalAddress, OperationSource, ProvisionTimeline, ProvisionVersion
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core.tree_ops import (
    _default_sort_key,
    check_invariants,
    find,
    resolve,
)
from lawvm.core.timeline import (
    select_active_version,
    select_background_version,
    select_temporary_version,
)


# ============================================================================
# Test 1: select_active_version exhaustive over small timelines
# ============================================================================


# Small date universe for exhaustive enumeration
DATES = ["2000-01-01", "2005-06-15", "2010-03-01", "2015-09-20", "2025-12-31"]
QUERY_DATES = DATES + ["1999-01-01", "2030-06-01"]  # include before/after all


def _make_version(
    effective: str,
    variant: Literal["permanent", "temporary"] = "permanent",
    expires: str = "",
) -> ProvisionVersion:
    """Helper to build a ProvisionVersion with minimal boilerplate."""
    return ProvisionVersion(
        effective=effective,
        enacted=effective,
        expires=expires,
        variant_kind=variant,
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text=f"v@{effective}"),
        source=OperationSource(statute_id="test/1"),
    )


def _make_timeline(versions: List[ProvisionVersion]) -> ProvisionTimeline:
    """Build a timeline with sorted versions."""
    sorted_vs = sorted(versions, key=lambda v: (v.effective, v.enacted))
    addr = LegalAddress(path=(("section", "1"),))
    return ProvisionTimeline(address=addr, versions=sorted_vs)


class TestSelectActiveVersionExhaustive:
    """Enumerate ALL 1-2 version timelines with dates from a small set."""

    def test_single_permanent_version_all_dates(self) -> None:
        """For each of 5 possible effective dates x 7 query dates, verify properties."""
        count = 0
        for eff in DATES:
            tl = _make_timeline([_make_version(eff)])
            for qd in QUERY_DATES:
                result = select_active_version(tl, qd)
                if result is not None:
                    assert result.effective <= qd, f"eff={eff}, qd={qd}: result.effective={result.effective} > qd"
                    if result.expires:
                        assert result.expires > qd
                else:
                    # No result means query date is before the version's effective
                    assert qd < eff, f"eff={eff}, qd={qd}: expected result for permanent version"
                count += 1
        assert count == len(DATES) * len(QUERY_DATES)

    def test_single_temporary_version_all_dates(self) -> None:
        """Temporary version: check expires exclusion for all date pairs."""
        count = 0
        for eff_idx, eff in enumerate(DATES):
            for exp_idx in range(eff_idx, len(DATES)):
                exp = DATES[exp_idx]
                if exp < eff:
                    continue  # invalid: expires before effective
                tl = _make_timeline([_make_version(eff, "temporary", exp)])
                for qd in QUERY_DATES:
                    result = select_active_version(tl, qd)
                    if result is not None:
                        assert result.effective <= qd
                        assert result.expires > qd, f"eff={eff}, exp={exp}, qd={qd}: expired version returned"
                    else:
                        # Either query is before effective or after/at expiry
                        assert qd < eff or qd >= exp, (
                            f"eff={eff}, exp={exp}, qd={qd}: expected result for active temporary"
                        )
                    count += 1
        assert count > 0

    def test_two_permanent_versions_all_combos(self) -> None:
        """Two permanent versions: the later eligible one should always win."""
        count = 0
        for eff1, eff2 in itertools.combinations(DATES, 2):
            tl = _make_timeline([_make_version(eff1), _make_version(eff2)])
            for qd in QUERY_DATES:
                result = select_active_version(tl, qd)
                if result is not None:
                    assert result.effective <= qd
                    # Should be the most recent eligible version
                    eligible = [v for v in tl.versions if v.effective <= qd]
                    assert len(eligible) >= 1
                    best = max(eligible, key=lambda v: (v.effective, v.enacted))
                    assert result.effective == best.effective, (
                        f"eff1={eff1}, eff2={eff2}, qd={qd}: expected {best.effective}, got {result.effective}"
                    )
                else:
                    # Query date before both versions
                    assert qd < eff1 and qd < eff2
                count += 1
        assert count == len(list(itertools.combinations(DATES, 2))) * len(QUERY_DATES)

    def test_permanent_plus_temporary_overlay(self) -> None:
        """A temporary overlay should win over a permanent version when active."""
        count = 0
        for perm_eff in DATES[:3]:  # permanent from early dates
            for temp_eff_idx in range(len(DATES)):
                temp_eff = DATES[temp_eff_idx]
                for temp_exp_idx in range(temp_eff_idx, len(DATES)):
                    temp_exp = DATES[temp_exp_idx]
                    if temp_exp < temp_eff:
                        continue
                    perm = _make_version(perm_eff, "permanent")
                    temp = _make_version(temp_eff, "temporary", temp_exp)
                    tl = _make_timeline([perm, temp])

                    for qd in QUERY_DATES:
                        result = select_active_version(tl, qd)
                        bg = select_background_version(tl, qd)
                        tmp = select_temporary_version(tl, qd)

                        if result is not None:
                            assert result.effective <= qd
                            if result.expires:
                                assert result.expires > qd
                            # Result should be either bg or tmp
                            assert result is bg or result is tmp, "Active is neither bg nor tmp"
                            # If temporary is active, it should win
                            if tmp is not None:
                                assert result is tmp, "Temporary active but not returned"
                        count += 1
        assert count > 0

    def test_all_expired_returns_none(self) -> None:
        """When all versions have expired before query date, result is None."""
        expired = [
            _make_version("2000-01-01", "temporary", "2005-06-15"),
            _make_version("2005-06-15", "temporary", "2010-03-01"),
        ]
        tl = _make_timeline(expired)
        # Query after all expire
        for qd in ["2010-03-01", "2015-09-20", "2025-12-31", "2030-06-01"]:
            result = select_active_version(tl, qd)
            assert result is None, f"qd={qd}: expected None when all versions expired"


# ============================================================================
# Test 2: tree_ops.find exhaustive over small trees
# ============================================================================

TREE_LABELS = ["1", "2", "3", "a", "b"]
TREE_KINDS = ["section", "subsection"]


def _make_flat_body(labels: Sequence[str]) -> IRNode:
    """Create a body with sections having the given labels, sorted."""
    sorted_labels = sorted(labels, key=_default_sort_key)
    children = tuple(IRNode(kind=IRNodeKind.SECTION, label=lbl, text=f"text-{lbl}") for lbl in sorted_labels)
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=children)


def _make_nested_body(section_labels: Sequence[str], subsection_labels: Sequence[str]) -> IRNode:
    """Create a body with one section containing subsections."""
    if not section_labels:
        return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=())
    sec_label = section_labels[0]
    sorted_sub_labels = sorted(subsection_labels, key=_default_sort_key)
    sub_children = tuple(IRNode(kind=IRNodeKind.SUBSECTION, label=lbl, text=f"sub-{lbl}") for lbl in sorted_sub_labels)
    section = IRNode(kind=IRNodeKind.SECTION, label=sec_label, children=sub_children)
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=(section,))


class TestFindExhaustiveSmallTrees:
    """Enumerate ALL possible trees with 1-4 children from a small label set."""

    def test_find_in_flat_body_all_subsets(self) -> None:
        """For every subset of TREE_LABELS (size 1-4), verify find for all labels."""
        count = 0
        for size in range(1, min(5, len(TREE_LABELS) + 1)):
            for labels in itertools.combinations(TREE_LABELS, size):
                body = _make_flat_body(labels)
                label_set = set(labels)
                for query_label in TREE_LABELS:
                    result = find(body, "section", query_label)
                    if query_label in label_set:
                        assert result is not None, (
                            f"labels={labels}, query={query_label}: find returned None for existing label"
                        )
                        # Verify resolve returns the correct node
                        node = resolve(body, result)
                        assert node is not None
                        assert node.label == query_label
                        assert node.kind == IRNodeKind.SECTION
                    else:
                        assert result is None, (
                            f"labels={labels}, query={query_label}: find returned path for non-existing label"
                        )
                    count += 1
        assert count > 0

    def test_find_wrong_kind_always_none(self) -> None:
        """Searching for a subsection in a flat section body always returns None."""
        for size in range(1, 4):
            for labels in itertools.combinations(TREE_LABELS, size):
                body = _make_flat_body(labels)
                for query_label in TREE_LABELS:
                    result = find(body, "subsection", query_label)
                    assert result is None, (
                        f"labels={labels}, query=subsection:{query_label}: find should return None for wrong kind"
                    )

    def test_find_nested_subsections(self) -> None:
        """For all subsection subsets under a section, verify find with depth."""
        count = 0
        for sec_label in ["1", "2"]:
            for sub_size in range(1, 4):
                for sub_labels in itertools.combinations(TREE_LABELS, sub_size):
                    body = _make_nested_body((sec_label,), sub_labels)
                    sub_label_set = set(sub_labels)
                    for query_label in TREE_LABELS:
                        result = find(body, "subsection", query_label)
                        if query_label in sub_label_set:
                            assert result is not None, (
                                f"sec={sec_label}, subs={sub_labels}, query=subsection:{query_label}: not found"
                            )
                            node = resolve(body, result)
                            assert node is not None
                            assert node.label == query_label
                            assert node.kind == IRNodeKind.SUBSECTION
                            # Path should go through the section
                            assert len(result) == 2
                            assert result[0] == ("section", sec_label)
                        else:
                            assert result is None, (
                                f"sec={sec_label}, subs={sub_labels}, "
                                f"query=subsection:{query_label}: "
                                f"found non-existing subsection"
                            )
                        count += 1
        assert count > 0

    def test_find_scoped_search(self) -> None:
        """Verify scope_kind/scope_label restricts search correctly."""
        # Build a body with two sections, each with different subsections
        sec1_subs = [
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="s1-sub1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="s1-sub2"),
        ]
        sec2_subs = [
            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="s2-sub1"),
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="s2-sub3"),
        ]
        body = IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            text="",
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", children=tuple(sec1_subs)),
                IRNode(kind=IRNodeKind.SECTION, label="2", children=tuple(sec2_subs)),
            ),
        )

        # Scoped find: subsection "1" in section "1"
        result = find(body, "subsection", "1", scope_kind="section", scope_label="1")
        assert result is not None
        node = resolve(body, result)
        assert node is not None
        assert node.text == "s1-sub1"

        # Scoped find: subsection "3" in section "1" — should not exist
        result = find(body, "subsection", "3", scope_kind="section", scope_label="1")
        assert result is None

        # Scoped find: subsection "3" in section "2" — should exist
        result = find(body, "subsection", "3", scope_kind="section", scope_label="2")
        assert result is not None
        node = resolve(body, result)
        assert node is not None
        assert node.text == "s2-sub3"

    def test_empty_tree_find_always_none(self) -> None:
        """Find on an empty body always returns None."""
        body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=())
        for label in TREE_LABELS:
            for kind in TREE_KINDS:
                result = find(body, kind, label)
                assert result is None


# ============================================================================
# Test 3: IRNode kind/label validation exhaustive
# ============================================================================


class TestIRNodeValidationExhaustive:
    """Exhaustive edge cases for IRNode construction."""

    def test_empty_kind_raises(self) -> None:
        """IRNode with empty kind must raise ValueError."""
        with pytest.raises(ValueError, match="kind must be non-empty"):
            IRNode(kind=cast(Any, ""), label="1")

    def test_all_valid_structural_kinds(self) -> None:
        """Every standard structural kind creates a valid IRNode."""
        structural_kinds = [
            "body",
            "chapter",
            "part",
            "section",
            "subsection",
            "paragraph",
            "subparagraph",
            "block",
            "hcontainer",
            "content",
            "intro",
            "heading",
            "num",
            "p",
            "i",
            "omission",
        ]
        for kind in structural_kinds:
            node = IRNode(kind=cast(Any, kind), label="1", text="test")
            assert node.kind == kind
            assert node.label == "1"

    def test_irnode_children_preserved(self) -> None:
        """Children are preserved exactly as given."""
        for n_children in range(5):
            children = [IRNode(kind=IRNodeKind.SUBSECTION, label=str(i + 1), text=f"c{i}") for i in range(n_children)]
            parent = IRNode(kind=IRNodeKind.SECTION, label="1", children=tuple(children))
            assert len(parent.children) == n_children
            for i, child in enumerate(parent.children):
                assert child.label == str(i + 1)


class TestLegalAddressExhaustive:
    """Exhaustive tests for LegalAddress construction and methods."""

    def test_empty_kind_in_path_raises(self) -> None:
        """Any path element with empty kind must raise ValueError."""
        # Single empty kind
        with pytest.raises(ValueError, match="empty kind"):
            LegalAddress(path=(("", "1"),))

        # Second element empty
        with pytest.raises(ValueError, match="empty kind"):
            LegalAddress(path=(("section", "1"), ("", "2")))

    def test_all_valid_single_element_paths(self) -> None:
        """Single-element paths with non-empty kind are valid."""
        kinds = ["section", "chapter", "part", "subsection", "paragraph", "schedule", "article"]
        labels = ["1", "2", "a", "I", "3a"]
        for kind, label in itertools.product(kinds, labels):
            addr = LegalAddress(path=((kind, label),))
            assert addr.depth() == 1
            assert addr.leaf_kind() == kind
            assert addr.leaf_label() == label
            assert addr.parent() is None

    def test_two_element_paths_parent(self) -> None:
        """Two-element paths have correct parent and leaf."""
        outer_kinds = ["chapter", "part"]
        inner_kinds = ["section", "subsection"]
        labels = ["1", "2", "a"]
        count = 0
        for ok, ik in itertools.product(outer_kinds, inner_kinds):
            for ol, il in itertools.product(labels, labels):
                addr = LegalAddress(path=((ok, ol), (ik, il)))
                assert addr.depth() == 2
                assert addr.leaf_kind() == ik
                assert addr.leaf_label() == il
                parent = addr.parent()
                assert parent is not None
                assert parent.depth() == 1
                assert parent.leaf_kind() == ok
                assert parent.leaf_label() == ol
                count += 1
        assert count == len(outer_kinds) * len(inner_kinds) * len(labels) ** 2

    def test_special_field_combinations(self) -> None:
        """Special field is independent of path."""
        specials = [None, FacetKind.HEADING, FacetKind.INTRO]
        for special in specials:
            addr = LegalAddress(path=(("section", "1"),), special=special)
            assert addr.special == special

    def test_empty_path_leaf_accessors(self) -> None:
        """Empty path returns empty string for leaf_kind and leaf_label."""
        addr = LegalAddress(path=())
        assert addr.leaf_kind() == ""
        assert addr.leaf_label() == ""
        assert addr.depth() == 0
        assert addr.parent() is None


# ============================================================================
# Test 4: ProvisionVersion __post_init__ exhaustive
# ============================================================================


class TestProvisionVersionPostInitExhaustive:
    """Enumerate all combinations of validation-relevant fields."""

    EFFECTIVE_VALUES = ["2020-01-01", ""]
    EXPIRES_VALUES = ["", "2019-06-01", "2020-01-01", "2025-12-31"]
    VARIANT_KINDS: tuple[Literal["permanent", "temporary"], ...] = ("permanent", "temporary")

    def test_all_combinations(self) -> None:
        """Enumerate all (effective, expires, variant_kind) combinations.

        Verify that invalid combinations raise ValueError and valid ones succeed.
        """
        content = IRNode(kind=IRNodeKind.SECTION, label="1", text="test")
        tested = 0
        for effective, expires, variant in itertools.product(
            self.EFFECTIVE_VALUES, self.EXPIRES_VALUES, self.VARIANT_KINDS
        ):
            tested += 1
            # Predict validity
            should_fail = False
            fail_reason = ""

            if not effective:
                should_fail = True
                fail_reason = "empty effective"
            elif expires and effective > expires:
                should_fail = True
                fail_reason = "expires before effective"

            if should_fail:
                with pytest.raises(ValueError):
                    ProvisionVersion(
                        effective=effective,
                        enacted=effective or "2020-01-01",
                        expires=expires,
                        variant_kind=variant,
                        content=content,
                    )
            else:
                pv = ProvisionVersion(
                    effective=effective,
                    enacted=effective,
                    expires=expires,
                    variant_kind=variant,
                    content=content,
                )
                assert pv.effective == effective
                assert pv.expires == expires
                assert pv.variant_kind == variant

        # Verify we tested all combinations
        expected = len(self.EFFECTIVE_VALUES) * len(self.EXPIRES_VALUES) * len(self.VARIANT_KINDS)
        assert tested == expected, f"Expected {expected} combos, tested {tested}"

    def test_permanent_with_expires_is_valid(self) -> None:
        """A permanent version with expires set is valid (rare but legal)."""
        pv = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            expires="2025-12-31",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="test"),
        )
        assert pv.variant_kind == "permanent"
        assert pv.expires == "2025-12-31"

    def test_temporary_with_same_effective_and_expires(self) -> None:
        """Temporary where expires == effective is valid (zero-duration window)."""
        pv = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            expires="2020-01-01",
            variant_kind="temporary",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="test"),
        )
        assert pv.expires == pv.effective

    def test_tombstone_version(self) -> None:
        """Version with content=None (tombstone/repeal) is valid."""
        pv = ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            variant_kind="permanent",
            content=None,
        )
        assert pv.content is None

    def test_retroactive_version(self) -> None:
        """Retroactive: effective < enacted is valid."""
        pv = ProvisionVersion(
            effective="2019-01-01",
            enacted="2020-06-15",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="retroactive"),
        )
        assert pv.effective < pv.enacted


# ============================================================================
# Test 5: check_invariants exhaustive over small trees
# ============================================================================


class TestCheckInvariantsExhaustive:
    """Verify check_invariants on all small trees with known properties."""

    def test_all_unique_sorted_subsets_pass(self) -> None:
        """Every tree with unique sorted section labels passes invariants."""
        numeric_labels = ["1", "2", "3"]
        for size in range(1, len(numeric_labels) + 1):
            for labels in itertools.combinations(numeric_labels, size):
                # Labels are already sorted because we use combinations
                body = _make_flat_body(list(labels))
                violations = check_invariants(body)
                assert violations == [], f"labels={labels}: unexpected violations: {violations}"

    def test_duplicate_label_always_detected(self) -> None:
        """Every tree with a duplicated section label is detected."""
        for label in ["1", "2", "3"]:
            children = [
                IRNode(kind=IRNodeKind.SECTION, label=label, text="first"),
                IRNode(kind=IRNodeKind.SECTION, label=label, text="second"),
            ]
            body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children))
            violations = check_invariants(body)
            assert any("duplicate" in v for v in violations), (
                f"label={label}: duplicate not detected. violations={violations}"
            )

    def test_reversed_order_detected(self) -> None:
        """Reversed section order is always detected for all label pairs."""
        labels_pairs = [
            ("2", "1"),
            ("3", "1"),
            ("3", "2"),
            ("10", "2"),
            ("10", "9"),
        ]
        for first, second in labels_pairs:
            # Verify these are actually reversed
            assert _default_sort_key(first) > _default_sort_key(second), f"{first} should sort after {second}"
            children = [
                IRNode(kind=IRNodeKind.SECTION, label=first, text="first"),
                IRNode(kind=IRNodeKind.SECTION, label=second, text="second"),
            ]
            body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children))
            violations = check_invariants(body)
            assert any("out of order" in v for v in violations), (
                f"first={first}, second={second}: order violation not detected. violations={violations}"
            )
