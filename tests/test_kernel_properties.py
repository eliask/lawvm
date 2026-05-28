"""Hypothesis property-based tests for the 5 trusted kernels.

Kernels under test:
  1. tree_ops: replace_at, remove_at, insert_sorted, find, check_invariants
  2. Timeline selection: select_active_version, select_background_version, select_temporary_version
  3. Tree invariant checker: check_invariants validity/detection
  4. Timeline invariant checker: check_no_overlapping_permanent_versions, check_temporary_overlay_consistency
  5. Evidence classifier ordering: _primary_proof_tier

Run:
    uv run pytest tests/test_kernel_properties.py -v
"""

from __future__ import annotations

import string
from typing import List, cast

from hypothesis import given, settings, assume
from hypothesis import strategies as st

from lawvm.core.ir import (
    IRNode,
    LegalAddress,
    OperationSource,
    ProvisionTimeline,
    ProvisionVersion,
)
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.tree_ops import (
    Path,
    _default_sort_key,
    check_invariants,
    find,
    insert_sorted,
    remove_at,
    replace_at,
    resolve,
)
from lawvm.core.timeline import (
    select_active_version,
    select_background_version,
    select_temporary_version,
)
from lawvm.core.timeline_invariants import (
    check_no_overlapping_permanent_versions,
    check_temporary_overlay_consistency,
)
from lawvm.tools.evidence_claims import _primary_proof_tier
from lawvm.tools._evidence_helpers import _PRIMARY_TIER_ORDER


# ============================================================================
# Strategies
# ============================================================================

SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=40,
)

SECTION_LABELS = st.one_of(
    st.integers(min_value=1, max_value=50).map(str),
    st.builds(
        lambda n, s: f"{n}{s}",
        st.integers(min_value=1, max_value=30),
        st.sampled_from(list("abc")),
    ),
)

DATE_STRS = st.dates(
    min_value=__import__("datetime").date(1990, 1, 1),
    max_value=__import__("datetime").date(2030, 12, 31),
).map(str)


@st.composite
def well_formed_body(draw) -> IRNode:
    """Generate a body -> chapter -> section -> subsection tree that passes check_invariants."""
    n_chapters = draw(st.integers(min_value=1, max_value=3))
    chapter_labels = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=10).map(str),
                min_size=n_chapters,
                max_size=n_chapters,
                unique=True,
            )
        ),
        key=_default_sort_key,
    )
    chapters: List[IRNode] = []
    for cl in chapter_labels:
        n_sections = draw(st.integers(min_value=1, max_value=4))
        section_labels = sorted(
            draw(
                st.lists(
                    st.integers(min_value=1, max_value=40).map(str),
                    min_size=n_sections,
                    max_size=n_sections,
                    unique=True,
                )
            ),
            key=_default_sort_key,
        )
        sections: List[IRNode] = []
        for sl in section_labels:
            n_sub = draw(st.integers(min_value=1, max_value=3))
            subs = [
                IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT)) for i in range(1, n_sub + 1)
            ]
            sections.append(IRNode(kind=IRNodeKind.SECTION, label=sl, children=tuple(subs)))
        ch_heading = IRNode(kind=IRNodeKind.HEADING, label=None, text=draw(SHORT_TEXT))
        chapters.append(IRNode(kind=IRNodeKind.CHAPTER, label=cl, children=tuple([ch_heading, *sections])))
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(chapters))


@st.composite
def flat_body_unique_sections(draw) -> IRNode:
    """Generate a body with directly nested sections (no chapters), sorted and unique labels."""
    n = draw(st.integers(min_value=1, max_value=5))
    labels = sorted(
        draw(
            st.lists(
                st.integers(min_value=1, max_value=50).map(str),
                min_size=n,
                max_size=n,
                unique=True,
            )
        ),
        key=_default_sort_key,
    )
    sections = tuple(IRNode(kind=IRNodeKind.SECTION, label=lbl, text=draw(SHORT_TEXT)) for lbl in labels)
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=sections)


@st.composite
def provision_version_st(draw) -> ProvisionVersion:
    """Generate a random ProvisionVersion."""
    eff_year = draw(st.integers(min_value=1990, max_value=2025))
    eff = f"{eff_year}-{draw(st.integers(1, 12)):02d}-{draw(st.integers(1, 28)):02d}"
    variant = draw(st.sampled_from(["permanent", "temporary"]))
    expires = ""
    if variant == "temporary":
        exp_year = draw(st.integers(min_value=eff_year, max_value=2030))
        exp_month = draw(st.integers(1, 12))
        exp_day = draw(st.integers(1, 28))
        expires = f"{exp_year}-{exp_month:02d}-{exp_day:02d}"
        # Ensure expires >= effective
        if expires < eff:
            expires = f"{eff_year + 1}-12-31"
    return ProvisionVersion(
        effective=eff,
        enacted=eff,
        expires=expires,
        variant_kind=variant,
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text=draw(SHORT_TEXT)),
        source=OperationSource(statute_id=f"{eff_year}/1"),
    )


@st.composite
def provision_timeline_st(draw, min_versions: int = 1, max_versions: int = 5) -> ProvisionTimeline:
    """Generate a random ProvisionTimeline with sorted versions."""
    n = draw(st.integers(min_value=min_versions, max_value=max_versions))
    versions = sorted(
        [draw(provision_version_st()) for _ in range(n)],
        key=lambda v: (v.effective, v.enacted),
    )
    addr = LegalAddress(path=(("section", draw(st.integers(1, 50).map(str))),))
    return ProvisionTimeline(address=addr, versions=versions)


# ============================================================================
# KERNEL 1: Tree operations
# ============================================================================

# ---------------------------------------------------------------------------
# K1.1: replace_at preserves sibling count at parent level
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=200, deadline=None)
def test_k1_replace_at_preserves_sibling_count(body: IRNode, new_text: str) -> None:
    """After replacing a section, the parent chapter has the same number of children."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)
    ch_label = cast(str, ch.label)
    sec_label = cast(str, sec.label)

    path: Path = (("chapter", ch_label), ("section", sec_label))
    replacement = IRNode(kind=IRNodeKind.SECTION, label=sec_label, text=new_text)
    result = replace_at(body, path, replacement)

    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    assert len(result_ch.children) == len(ch.children)


# ---------------------------------------------------------------------------
# K1.2: replace_at preserves other siblings unchanged
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=200, deadline=None)
def test_k1_replace_at_preserves_other_siblings(body: IRNode, new_text: str) -> None:
    """Siblings not at the target path are identical objects (shared, not copied)."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 2)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)
    ch_label = cast(str, ch.label)
    sec_label = cast(str, sec.label)

    path: Path = (("chapter", ch_label), ("section", sec_label))
    replacement = IRNode(kind=IRNodeKind.SECTION, label=sec_label, text=new_text)
    result = replace_at(body, path, replacement)

    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    # All sections except the replaced one should be the exact same object
    original_other_sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION and c.label != sec.label]
    result_other_sections = [c for c in result_ch.children if c.kind == IRNodeKind.SECTION and c.label != sec.label]
    for orig, res in zip(original_other_sections, result_other_sections, strict=True):
        assert orig is res, f"Sibling section {orig.label} was copied instead of shared"


# ---------------------------------------------------------------------------
# K1.3: remove_at reduces sibling count by 1
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=200, deadline=None)
def test_k1_remove_at_reduces_count(body: IRNode) -> None:
    """After removing a section, the parent chapter has one fewer section."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None and ch.label is not None)
    ch_label = cast(str, ch.label)
    sec_label = cast(str, sec.label)

    path: Path = (("chapter", ch_label), ("section", sec_label))
    result = remove_at(body, path)

    result_ch = next(c for c in result.children if c.kind == IRNodeKind.CHAPTER and c.label == ch.label)
    result_sections = [c for c in result_ch.children if c.kind == IRNodeKind.SECTION]
    assert len(result_sections) == len(sections) - 1


# ---------------------------------------------------------------------------
# K1.4: insert_sorted maintains sort order and check_invariants passes
# ---------------------------------------------------------------------------


@given(well_formed_body(), SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=200, deadline=None)
def test_k1_insert_sorted_maintains_order_and_invariants(body: IRNode, new_label: str, new_text: str) -> None:
    """After inserting, check_invariants returns no ordering violations for the affected parent."""
    assume(check_invariants(body) == [])
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    assume(ch.label is not None)
    ch_label = cast(str, ch.label)

    existing = {c.label for c in ch.children if c.kind == IRNodeKind.SECTION}
    assume(new_label not in existing)

    new_section = IRNode(
        kind=IRNodeKind.SECTION,
        label=new_label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    result = insert_sorted(body, [("chapter", ch_label)], new_section)

    violations = check_invariants(result)
    assert violations == [], f"Invariant violations after insert_sorted: {violations}"


# ---------------------------------------------------------------------------
# K1.5: find round-trips with replace_at
# ---------------------------------------------------------------------------


@given(well_formed_body(), SHORT_TEXT)
@settings(max_examples=200, deadline=None)
def test_k1_find_roundtrips_with_replace_at(body: IRNode, new_text: str) -> None:
    """If find(tree, kind, label) returns a path, replace_at(tree, path, new) succeeds."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)
    sec = sections[0]
    assume(sec.label is not None)
    sec_label = cast(str, sec.label)

    found_path = find(body, "section", sec_label)
    assume(found_path is not None)
    found_path = cast(Path, found_path)

    replacement = IRNode(
        kind=IRNodeKind.SECTION,
        label=sec_label,
        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=new_text),),
    )
    result = replace_at(body, found_path, replacement)

    # Verify the replacement is in place
    found_node = resolve(result, found_path)
    assert found_node is not None
    assert found_node.label == sec.label
    assert len(found_node.children) == 1
    assert found_node.children[0].text == new_text


# ---------------------------------------------------------------------------
# K1.6: check_invariants is idempotent
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=200, deadline=None)
def test_k1_check_invariants_is_idempotent(body: IRNode) -> None:
    """Running check_invariants twice gives the same result."""
    v1 = check_invariants(body)
    v2 = check_invariants(body)
    assert v1 == v2


# ============================================================================
# KERNEL 2: Timeline selection
# ============================================================================

# ---------------------------------------------------------------------------
# K2.1: select_active_version returns eligible version
# ---------------------------------------------------------------------------


@given(provision_timeline_st(), DATE_STRS)
@settings(max_examples=200, deadline=None)
def test_k2_select_active_version_returns_eligible(tl: ProvisionTimeline, date: str) -> None:
    """If result is not None, result.effective <= date and (no expires or date < result.expires)."""
    result = select_active_version(tl, date)
    if result is not None:
        assert result.effective <= date, f"Active version effective {result.effective} > query date {date}"
        if result.expires:
            assert result.expires > date, f"Active version expires {result.expires} <= query date {date}"


# ---------------------------------------------------------------------------
# K2.2: select_background_version returns non-temporary
# ---------------------------------------------------------------------------


@given(provision_timeline_st(), DATE_STRS)
@settings(max_examples=200, deadline=None)
def test_k2_select_background_version_returns_permanent(tl: ProvisionTimeline, date: str) -> None:
    """If result is not None, result.variant_kind == 'permanent'."""
    result = select_background_version(tl, date)
    if result is not None:
        assert result.variant_kind == "permanent", (
            f"Background version has variant_kind={result.variant_kind!r}, expected 'permanent'"
        )


# ---------------------------------------------------------------------------
# K2.3: select_temporary_version returns temporary
# ---------------------------------------------------------------------------


@given(provision_timeline_st(), DATE_STRS)
@settings(max_examples=200, deadline=None)
def test_k2_select_temporary_version_returns_temporary(tl: ProvisionTimeline, date: str) -> None:
    """If result is not None, result.variant_kind == 'temporary'."""
    result = select_temporary_version(tl, date)
    if result is not None:
        assert result.variant_kind == "temporary", (
            f"Temporary version has variant_kind={result.variant_kind!r}, expected 'temporary'"
        )


# ---------------------------------------------------------------------------
# K2.4: No version selected after all expire
# ---------------------------------------------------------------------------


@given(st.data())
@settings(max_examples=200, deadline=None)
def test_k2_no_version_after_all_expire(data) -> None:
    """If all versions have expired before date D, result should be None."""
    # Generate 1-3 temporary versions that all expire before 2010
    n = data.draw(st.integers(1, 3))
    versions = []
    for _ in range(n):
        eff_year = data.draw(st.integers(1995, 2003))
        exp_year = data.draw(st.integers(eff_year, 2008))
        eff = f"{eff_year}-06-15"
        exp = f"{exp_year}-12-31"
        if exp < eff:
            exp = f"{eff_year + 1}-12-31"
        versions.append(
            ProvisionVersion(
                effective=eff,
                enacted=eff,
                expires=exp,
                variant_kind="temporary",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temp"),
            )
        )
    versions.sort(key=lambda v: (v.effective, v.enacted))
    addr = LegalAddress(path=(("section", "1"),))
    tl = ProvisionTimeline(address=addr, versions=versions)

    # Query well after all expire
    result = select_active_version(tl, "2020-01-01")
    assert result is None, "Expected None when all versions have expired"


# ---------------------------------------------------------------------------
# K2.5: Active version is from the union of background + temporary
# ---------------------------------------------------------------------------


@given(provision_timeline_st(), DATE_STRS)
@settings(max_examples=200, deadline=None)
def test_k2_active_decomposes_into_background_or_temporary(tl: ProvisionTimeline, date: str) -> None:
    """select_active_version result is either the background or temporary result (or None)."""
    active = select_active_version(tl, date)
    bg = select_background_version(tl, date)
    tmp = select_temporary_version(tl, date)

    if active is None:
        # If active is None, both bg and tmp should be None
        assert bg is None and tmp is None, f"Active is None but bg={bg is not None}, tmp={tmp is not None}"
    else:
        # Active should be either bg or tmp
        assert active is bg or active is tmp, "Active version is neither background nor temporary"


# ============================================================================
# KERNEL 3: Tree invariant checker
# ============================================================================

# ---------------------------------------------------------------------------
# K3.1: Valid trees pass check_invariants
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=200, deadline=None)
def test_k3_valid_trees_pass_invariants(body: IRNode) -> None:
    """Trees generated by well_formed_body (unique sorted labels) pass check_invariants."""
    violations = check_invariants(body)
    assert violations == [], f"Valid tree has violations: {violations}"


# ---------------------------------------------------------------------------
# K3.2: Duplicate labels detected
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=200, deadline=None)
def test_k3_duplicate_labels_detected(body: IRNode) -> None:
    """If we duplicate a child, check_invariants reports a violation."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 1)

    # Duplicate the first section
    dup = IRNode(
        kind=sections[0].kind,
        label=sections[0].label,
        text="duplicate",
        children=tuple(sections[0].children),
    )

    # Create a new chapter with the duplicate added
    new_ch_children = list(ch.children) + [dup]
    new_ch = IRNode(kind=ch.kind, label=ch.label, text=ch.text, children=tuple(new_ch_children))
    new_body_children = [new_ch if c is ch else c for c in body.children]
    new_body = IRNode(kind=body.kind, label=body.label, text=body.text, children=tuple(new_body_children))

    violations = check_invariants(new_body)
    assert any("duplicate" in v for v in violations), f"Expected duplicate label violation, got: {violations}"


# ---------------------------------------------------------------------------
# K3.3: Unsorted labels detected
# ---------------------------------------------------------------------------


@given(well_formed_body())
@settings(max_examples=200, deadline=None)
def test_k3_unsorted_labels_detected(body: IRNode) -> None:
    """If we reverse section order within a chapter, check_invariants reports ordering violation."""
    chapters = [c for c in body.children if c.kind == IRNodeKind.CHAPTER]
    assume(len(chapters) >= 1)
    ch = chapters[0]
    sections = [c for c in ch.children if c.kind == IRNodeKind.SECTION]
    assume(len(sections) >= 2)

    # Separate non-section children (heading, num) from sections
    non_sections = [c for c in ch.children if c.kind != IRNodeKind.SECTION]
    reversed_sections = list(reversed(sections))

    # Only flag if the reversal actually changes order
    orig_keys = [_default_sort_key(s.label) for s in sections]
    rev_keys = [_default_sort_key(s.label) for s in reversed_sections]
    assume(orig_keys != rev_keys)

    new_ch = IRNode(
        kind=ch.kind,
        label=ch.label,
        text=ch.text,
        children=tuple(non_sections + reversed_sections),
    )
    new_body_children = [new_ch if c is ch else c for c in body.children]
    new_body = IRNode(kind=body.kind, label=body.label, text=body.text, children=tuple(new_body_children))

    violations = check_invariants(new_body)
    assert any("out of order" in v for v in violations), f"Expected ordering violation, got: {violations}"


# ============================================================================
# KERNEL 4: Timeline invariant checker
# ============================================================================

# ---------------------------------------------------------------------------
# K4.1: Non-overlapping permanent versions pass
# ---------------------------------------------------------------------------


@given(st.data())
@settings(max_examples=200, deadline=None)
def test_k4_non_overlapping_permanents_pass(data) -> None:
    """Generated timelines with non-overlapping permanents have 0 violations."""
    n = data.draw(st.integers(1, 4))
    # Generate unique effective dates
    years = sorted(
        data.draw(
            st.lists(
                st.integers(2000, 2025),
                min_size=n,
                max_size=n,
                unique=True,
            )
        )
    )
    versions = [
        ProvisionVersion(
            effective=f"{y}-01-01",
            enacted=f"{y}-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text=f"v{y}"),
        )
        for y in years
    ]
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {addr: ProvisionTimeline(address=addr, versions=versions)}

    violations = check_no_overlapping_permanent_versions(timelines)
    assert violations == [], f"Non-overlapping permanents had violations: {violations}"


# ---------------------------------------------------------------------------
# K4.2: Overlapping permanents detected
# ---------------------------------------------------------------------------


def test_k4_overlapping_permanents_detected() -> None:
    """Two permanent versions with same effective and enacted date produce a violation."""
    addr = LegalAddress(path=(("section", "1"),))
    versions = [
        ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="v1"),
        ),
        ProvisionVersion(
            effective="2020-01-01",
            enacted="2020-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="v2"),
        ),
    ]
    timelines = {addr: ProvisionTimeline(address=addr, versions=versions)}

    violations = check_no_overlapping_permanent_versions(timelines)
    assert len(violations) >= 1
    assert any("permanent" in v.lower() for v in violations)


# ---------------------------------------------------------------------------
# K4.3: Temporary without expires allowed
# ---------------------------------------------------------------------------


def test_k4_temporary_without_expires_allowed() -> None:
    """A temporary version without expires is a valid unknown-expiry carrier."""
    ver = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        expires="",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temp"),
    )
    assert ver.variant_kind == "temporary"
    assert ver.expires == ""


# ---------------------------------------------------------------------------
# K4.4: Well-formed temporary timelines pass
# ---------------------------------------------------------------------------


@given(st.data())
@settings(max_examples=200, deadline=None)
def test_k4_well_formed_temporary_timelines_pass(data) -> None:
    """Non-overlapping temporaries with valid expires pass consistency check."""
    n = data.draw(st.integers(1, 3))
    # Generate non-overlapping temporal ranges
    versions = []
    year = 2000
    for _ in range(n):
        eff = f"{year}-01-01"
        exp = f"{year}-12-31"
        versions.append(
            ProvisionVersion(
                effective=eff,
                enacted=eff,
                expires=exp,
                variant_kind="temporary",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text=f"temp{year}"),
            )
        )
        year += 2  # gap ensures no overlap

    addr = LegalAddress(path=(("section", "1"),))
    timelines = {addr: ProvisionTimeline(address=addr, versions=versions)}

    violations = check_temporary_overlay_consistency(timelines)
    assert violations == [], f"Well-formed temporary had violations: {violations}"


# ============================================================================
# KERNEL 5: Evidence classifier ordering
# ============================================================================

# ---------------------------------------------------------------------------
# K5.1: Tier ordering is respected
# ---------------------------------------------------------------------------

# The priority order (highest to lowest):
# PROVED_HTML_XML_NONCOMMENSURABLE > PROVED_SOURCE_PATHOLOGY > PROVED_ORACLE_INCORRECT
# > PROVED_REPLAY_BUG > UNRESOLVED

TIER_NAMES = [
    "PROVED_HTML_XML_NONCOMMENSURABLE",
    "PROVED_SOURCE_PATHOLOGY",
    "PROVED_ORACLE_INCORRECT",
    "PROVED_REPLAY_BUG",
    "UNRESOLVED",
]


@given(st.data())
@settings(max_examples=200, deadline=None)
def test_k5_tier_ordering_respected(data) -> None:
    """For any set of claims with mixed tiers, _primary_proof_tier returns the highest-priority one."""
    # Pick 1-4 tiers to include
    n = data.draw(st.integers(1, len(TIER_NAMES)))
    chosen_tiers = data.draw(
        st.lists(
            st.sampled_from(TIER_NAMES),
            min_size=n,
            max_size=n,
        )
    )
    assume(len(chosen_tiers) >= 1)

    claims = [{"tier": t, "kind": "test"} for t in chosen_tiers]
    result = _primary_proof_tier(claims)

    # The result should be the highest-priority tier present
    expected_idx = min(TIER_NAMES.index(t) for t in chosen_tiers)
    expected = TIER_NAMES[expected_idx]
    assert result == expected, f"Expected {expected}, got {result} for tiers {chosen_tiers}"


# ---------------------------------------------------------------------------
# K5.2: Single tier returns itself
# ---------------------------------------------------------------------------


@given(st.sampled_from(TIER_NAMES))
@settings(max_examples=200, deadline=None)
def test_k5_single_tier_returns_itself(tier: str) -> None:
    """A single claim always returns its own tier."""
    claims = [{"tier": tier, "kind": "test"}]
    result = _primary_proof_tier(claims)
    assert result == tier


# ---------------------------------------------------------------------------
# K5.3: Empty claims return UNRESOLVED
# ---------------------------------------------------------------------------


def test_k5_empty_claims_return_unresolved() -> None:
    """No claims returns UNRESOLVED."""
    result = _primary_proof_tier([])
    assert result == "UNRESOLVED"


# ---------------------------------------------------------------------------
# K5.4: Higher tier always wins regardless of count
# ---------------------------------------------------------------------------


@given(st.data())
@settings(max_examples=200, deadline=None)
def test_k5_higher_tier_wins_regardless_of_count(data) -> None:
    """A single high-tier claim beats many low-tier claims."""
    high_idx = data.draw(st.integers(0, len(TIER_NAMES) - 2))
    low_idx = data.draw(st.integers(high_idx + 1, len(TIER_NAMES) - 1))

    high_tier = TIER_NAMES[high_idx]
    low_tier = TIER_NAMES[low_idx]

    # 1 high-tier + many low-tier
    n_low = data.draw(st.integers(1, 10))
    claims = [{"tier": high_tier, "kind": "test"}] + [{"tier": low_tier, "kind": "test"} for _ in range(n_low)]
    # Shuffle to ensure order doesn't matter
    shuffled = data.draw(st.permutations(claims))

    result = _primary_proof_tier(list(shuffled))
    assert result == high_tier, f"Expected {high_tier} to beat {low_tier}, got {result}"


# ---------------------------------------------------------------------------
# K5.5: _PRIMARY_TIER_ORDER is consistent with TIER_NAMES
# ---------------------------------------------------------------------------


def test_k5_tier_order_matches_constant() -> None:
    """Our test TIER_NAMES list matches the actual _PRIMARY_TIER_ORDER."""
    assert TIER_NAMES == _PRIMARY_TIER_ORDER, f"TIER_NAMES mismatch: test={TIER_NAMES}, actual={_PRIMARY_TIER_ORDER}"
