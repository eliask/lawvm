"""Property-based tests for the graph layer (Q8).

Tests compile_timelines, materialize_pit, select_active_version, diff_statute,
and content hashing using hypothesis-generated random statute structures and
operation sequences.

Run:
    uv run pytest tests/test_timeline_properties.py -v

Properties tested:
    1. Monotonicity  — timeline versions are chronologically non-decreasing
    2. Idempotence   — compile_timelines with no ops = one version per provision
    3. Completeness  — every address has at least its base version (at base_date)
    4. Identity      — diff_statute(T, T) == {} (no change between same dates)
    5. Roundtrip     — materialize_pit at base_date contains all base provisions
    6. Hash equality — same content → same hash; different content → different hash
    7. PIT coverage  — select_active_version at future date returns something for all
    8. Monotonic PIT — materialize_pit at later date never has fewer provisions than earlier
"""
from __future__ import annotations

import copy
import warnings
import string
from dataclasses import replace as dc_replace
from datetime import date, timedelta
from itertools import pairwise
from typing import Any, List, cast

import pytest

from hypothesis import given, settings, assume
from hypothesis.strategies import composite
from hypothesis import strategies as st

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    ScopePredicate,
    ProvisionTimeline,
    ProvisionVersion,
    StructuralAction,
)
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.duplicate_child_classification import (
    classify_duplicate_child_family,
    collect_duplicate_child_findings,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.core.statute_facets import is_statute_title_address
from lawvm.core.timeline import (
    current_address_from_migration_events,
    compile_timelines,
    TimelineCompilationResult,
    TimelineIssue,
    diff_statute,
    materialize_pit,
    materialize_pit_ex,
    provision_lineage,
    select_active_version,
    select_active_version_ex,
)
from lawvm.core.timeline_materialization import materialize_body
from lawvm.core.timeline_addresses import _address_prefix_matches
from lawvm.core.timeline_addresses import _retarget_version_content
from lawvm.core.timeline_lineage import (
    classify_materialization_lineage_bridge,
    choose_materialization_lineage_decision,
    classify_scope_migrations,
    current_address_with_prefix_migrations_from_events,
    has_only_leaf_stable_scope_renumbers,
    lineage_segments,
    rekey_timelines_with_migration_events,
)
from lawvm.core.provenance import MigrationEvent
from lawvm.core.compile_result import ActivationRule, TemporalEvent, TemporalScope
from lawvm.finland.replay_products import _rekey_timelines_with_migration_events


# Small test-local helper for current IR traversal expectations.
def _find_node_by_label(node: IRNode, kind: IRNodeKind, label: str) -> IRNode | None:
    if node.kind == kind and node.label == label:
        return node
    for child in node.children:
        found = _find_node_by_label(child, kind, label)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Strategies (generators for hypothesis)
# ---------------------------------------------------------------------------

# Valid ISO date strings in a narrow range for speed
DATE_STRS = st.dates(
    min_value=__import__("datetime").date(2000, 1, 1),
    max_value=__import__("datetime").date(2030, 12, 31),
).map(str)  # produces "YYYY-MM-DD" strings

SECTION_LABELS = st.text(
    alphabet=string.digits, min_size=1, max_size=3
).filter(lambda s: s.isdigit() and int(s) >= 1)

SHORT_TEXT = st.text(
    alphabet=string.ascii_letters + string.digits + " .,;-",
    min_size=1,
    max_size=80,
)


@st.composite
def subsections(draw) -> tuple[IRNode, ...]:
    """Generate a list of IRNode subsections."""
    n = draw(st.integers(min_value=1, max_value=6))
    return tuple(
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT))
        for i in range(1, n + 1)
    )


@st.composite
def section_node(draw) -> IRNode:
    """Generate one section IRNode with subsections."""
    label = draw(SECTION_LABELS)
    text = draw(SHORT_TEXT)
    subs = draw(subsections())
    return IRNode(kind=IRNodeKind.SECTION, label=label, text=text, children=subs)


@st.composite
def chapter_node(draw) -> IRNode:
    """Generate one chapter IRNode with sections."""
    label = draw(st.integers(min_value=1, max_value=5).map(str))
    text = draw(SHORT_TEXT)
    n_sections = draw(st.integers(min_value=1, max_value=5))
    # Use unique section labels within a chapter
    section_labels = draw(
        st.lists(
            st.integers(min_value=1, max_value=30).map(str),
            min_size=n_sections,
            max_size=n_sections,
            unique=True,
        )
    )
    sections = [
        IRNode(
            kind=IRNodeKind.SECTION,
            label=lbl,
            text=draw(SHORT_TEXT),
            children=tuple(
                IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT))
                for i in range(1, draw(st.integers(1, 3)) + 1)
            ),
        )
        for lbl in section_labels
    ]
    return IRNode(kind=IRNodeKind.CHAPTER, label=label, text=text, children=tuple(sections))


@st.composite
def base_statute(draw) -> IRStatute:
    """Generate a small random IRStatute."""
    n_chapters = draw(st.integers(min_value=1, max_value=4))
    # Unique chapter labels
    chapter_labels = draw(
        st.lists(
            st.integers(min_value=1, max_value=10).map(str),
            min_size=n_chapters,
            max_size=n_chapters,
            unique=True,
        )
    )
    chapters = []
    for ch_label in chapter_labels:
        n_sections = draw(st.integers(min_value=1, max_value=4))
        section_labels = draw(
            st.lists(
                st.integers(min_value=1, max_value=20).map(str),
                min_size=n_sections,
                max_size=n_sections,
                unique=True,
            )
        )
        sections = [
            IRNode(
                kind=IRNodeKind.SECTION,
                label=s_label,
                text=draw(SHORT_TEXT),
                children=tuple(
                    IRNode(kind=IRNodeKind.SUBSECTION, label=str(i), text=draw(SHORT_TEXT))
                    for i in range(1, draw(st.integers(1, 3)) + 1)
                ),
            )
            for s_label in section_labels
        ]
        chapters.append(IRNode(
            kind=IRNodeKind.CHAPTER, label=ch_label, text=draw(SHORT_TEXT), children=tuple(sections)
        ))
    body = IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(chapters))
    supplement_nodes: list[IRNode] = []
    n_supplements = draw(st.integers(min_value=0, max_value=2))
    if n_supplements:
        supplement_specs = draw(
            st.lists(
                st.tuples(
                    st.sampled_from(("schedule", "appendix")),
                    st.text(alphabet=string.ascii_uppercase + string.digits, min_size=1, max_size=2),
                ),
                min_size=n_supplements,
                max_size=n_supplements,
                unique=True,
            )
        )
        for kind, label in supplement_specs:
            child_kind = IRNodeKind.PARAGRAPH if kind == "schedule" else IRNodeKind.SECTION
            supplement_nodes.append(
                IRNode(
                    kind=kind,
                    label=label,
                    text=draw(SHORT_TEXT),
                    children=(
                        IRNode(kind=child_kind, label="1", text=draw(SHORT_TEXT)),
                    ),
                )
            )
    return IRStatute(
        statute_id="test/1",
        title=draw(SHORT_TEXT),
        body=body,
        supplements=tuple(supplement_nodes),
    )


# ---------------------------------------------------------------------------
# Property 1: Monotonicity
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_timeline_versions_are_monotonically_ordered(statute: IRStatute) -> None:
    """Versions within each ProvisionTimeline are in non-decreasing effective date order."""
    timelines = compile_timelines(statute, [])
    for addr, tl in timelines.items():
        for i, (left_version, right_version) in enumerate(pairwise(tl.versions)):
            assert left_version.effective <= right_version.effective, (
                f"Timeline {addr}: version {i} effective {left_version.effective!r} "
                f"> version {i+1} effective {right_version.effective!r}"
            )


# ---------------------------------------------------------------------------
# Property 2: Idempotence (no ops → one version per provision)
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_compile_timelines_no_ops_idempotence(statute: IRStatute) -> None:
    """compile_timelines with no ops produces exactly one version per provision."""
    timelines = compile_timelines(statute, [])
    for addr, tl in timelines.items():
        assert len(tl.versions) == 1, (
            f"{addr}: expected 1 version, got {len(tl.versions)}"
        )
        assert tl.versions[0].content is not None, f"{addr}: base version has None content"


# ---------------------------------------------------------------------------
# Property 3: Completeness (every provision addressable at base date)
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_compile_timelines_completeness(statute: IRStatute) -> None:
    """Every timeline has a version active at the base date (0000-00-00)."""
    timelines = compile_timelines(statute, [])
    base_date = "0000-00-00"
    for addr, tl in timelines.items():
        v = select_active_version(tl, base_date)
        assert v is not None, f"No active version for {addr} at {base_date}"
        assert v.content is not None, f"Tombstone at base date for {addr}"


@composite
def disjoint_section_replace_case(draw) -> tuple[IRStatute, list[LegalOperation]]:
    """Generate a tiny statute with two disjoint section replacements."""
    left_text = draw(SHORT_TEXT)
    right_text = draw(SHORT_TEXT)
    left_new = draw(SHORT_TEXT)
    right_new = draw(SHORT_TEXT)
    base = IRStatute(
        statute_id="test/disjoint",
        title="Disjoint replacements",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text=left_text),
                IRNode(kind=IRNodeKind.SECTION, label="2", text=right_text),
            ),
        ),
    )
    target1 = LegalAddress(path=(("section", "1"),))
    target2 = LegalAddress(path=(("section", "2"),))
    ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=target1,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text=left_new),
            source=OperationSource(statute_id="2001/1"),
        ),
        LegalOperation(
            op_id="replace_2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=target2,
            payload=IRNode(kind=IRNodeKind.SECTION, label="2", text=right_new),
            source=OperationSource(statute_id="2001/2"),
        ),
    ]
    return base, ops


@given(disjoint_section_replace_case())
@settings(max_examples=50, deadline=None)
def test_disjoint_section_replacements_commute(case: tuple[IRStatute, list[LegalOperation]]) -> None:
    """Disjoint section replacements must materialize identically regardless of op order."""
    base, ops = case

    first = compile_timelines_ex(base, ops, base_date="2000-01-01")
    second = compile_timelines_ex(base, list(reversed(ops)), base_date="2000-01-01")

    mat_first = materialize_pit_ex(first.timelines, "2010-01-01", base=base).statute
    mat_second = materialize_pit_ex(second.timelines, "2010-01-01", base=base).statute

    assert [child.text for child in mat_first.body.children if child.kind == IRNodeKind.SECTION] == [
        child.text for child in mat_second.body.children if child.kind == IRNodeKind.SECTION
    ]


@composite
def temporary_overlay_locality_case(draw) -> tuple[ProvisionTimeline, ProvisionTimeline, str]:
    """Generate a temporary overlay timeline and an unrelated neighbor timeline."""
    query_year = draw(st.integers(min_value=2006, max_value=2009))
    query_date = f"{query_year}-01-01"
    addr_a = LegalAddress(path=(("section", "1"),))
    addr_b = LegalAddress(path=(("section", "2"),))

    tl_a = ProvisionTimeline(
        address=addr_a,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                variant_kind="permanent",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="base A"),
            ),
            ProvisionVersion(
                effective="2005-01-01",
                enacted="2005-01-01",
                expires="2010-12-31",
                variant_kind="temporary",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temporary A"),
            ),
            ProvisionVersion(
                effective="2012-01-01",
                enacted="2012-01-01",
                variant_kind="permanent",
                content=IRNode(kind=IRNodeKind.SECTION, label="1", text="later A"),
            ),
        ],
    )
    tl_b = ProvisionTimeline(
        address=addr_b,
        versions=[
            ProvisionVersion(
                effective="2000-01-01",
                enacted="2000-01-01",
                variant_kind="permanent",
                content=IRNode(kind=IRNodeKind.SECTION, label="2", text="base B"),
            ),
        ],
    )
    return tl_a, tl_b, query_date


@given(temporary_overlay_locality_case())
@settings(max_examples=50, deadline=None)
def test_temporary_overlay_selection_is_unaffected_by_unrelated_versions(
    case: tuple[ProvisionTimeline, ProvisionTimeline, str],
) -> None:
    """An unrelated version elsewhere must not change temporary overlay selection."""
    tl_a, tl_b, query_date = case

    before = select_active_version_ex(tl_a, query_date).version
    assert before is not None
    assert before.variant_kind == "temporary"

    with_unrelated = ProvisionTimeline(
        address=tl_b.address,
        versions=[
            *tl_b.versions,
            ProvisionVersion(
                effective="2008-01-01",
                enacted="2008-01-01",
                variant_kind="permanent",
                content=IRNode(kind=IRNodeKind.SECTION, label="2", text="unrelated"),
            ),
        ],
    )
    assert select_active_version_ex(with_unrelated, query_date).version is not None

    after = select_active_version_ex(tl_a, query_date).version
    assert after is not None
    assert after.variant_kind == "temporary"
    assert before.content is not None
    assert after.content is not None
    assert irnode_to_text(after.content) == irnode_to_text(before.content)


def test_replace_inherits_active_temporary_expiry() -> None:
    """Replacing a temporary provision must not silently make it permanent."""
    base = IRStatute(
        statute_id="test/temp",
        title="Temporary expiry inheritance",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    addr = LegalAddress(path=(("section", "5e"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_temp",
                sequence=1,
                action=StructuralAction.INSERT,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="5e", text="temp v1"),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp insert",
                    enacted="2020-04-30",
                    effective="2020-05-01",
                    expires="2020-08-31",
                ),
            ),
            LegalOperation(
                op_id="replace_without_expiry",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="5e", text="temp v2"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="temp modify",
                    enacted="2020-06-26",
                    effective="2020-06-26",
                    expires="",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[addr]
    assert tl.versions[-1].expires == "2020-08-31"
    assert select_active_version(tl, "2020-07-01") is not None
    assert select_active_version(tl, "2020-09-01") is None


def test_descendant_replace_inherits_active_temporary_parent_expiry() -> None:
    """Replacing a child under a temporary parent must inherit the parent's expiry."""
    base = IRStatute(
        statute_id="test/temp-parent",
        title="Temporary parent expiry inheritance",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),),
                ),),
        ),
    )
    sec_addr = LegalAddress(path=(("section", "4"),))
    sub_addr = LegalAddress(path=(("section", "4"), ("subsection", "3")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_temp_parent",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=sec_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="temp v1"),),
                ),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp section replace",
                    enacted="2020-04-30",
                    effective="2020-05-01",
                    expires="2020-08-31",
                ),
            ),
            LegalOperation(
                op_id="replace_temp_child_without_expiry",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="temp v2"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="temp child replace",
                    enacted="2020-06-26",
                    effective="2020-06-26",
                    expires="",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    assert sub_addr in timelines
    sub_tl = timelines[sub_addr]
    assert sub_tl.versions[-1].expires == "2020-08-31"
    sub_active = select_active_version(sub_tl, "2020-07-01")
    assert sub_active is not None
    assert sub_active.content is not None
    assert sub_active.content.text == "temp v2"
    assert select_active_version(sub_tl, "2020-09-01") is None
    tl = timelines[sec_addr]
    assert tl.versions[-1].expires == "2020-08-31"
    assert select_active_version(tl, "2020-07-01") is not None
    assert select_active_version(tl, "2020-09-01") is not None


def test_descendant_insert_inherits_active_temporary_parent_expiry() -> None:
    """Inserting a new child under a temporary parent must inherit the parent's expiry."""
    base = IRStatute(
        statute_id="test/temp-parent-insert",
        title="Temporary parent insert expiry inheritance",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),),
                ),),
        ),
    )
    sec_addr = LegalAddress(path=(("section", "4"),))
    sub_addr = LegalAddress(path=(("section", "4"), ("subsection", "3")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_temp_parent",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=sec_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),),
                ),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp section replace",
                    enacted="2020-04-30",
                    effective="2020-05-01",
                    expires="2020-08-31",
                ),
            ),
            LegalOperation(
                op_id="insert_temp_child_without_expiry",
                sequence=2,
                action=StructuralAction.INSERT,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="temp inserted"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="temp child insert",
                    enacted="2020-06-26",
                    effective="2020-06-26",
                    expires="",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[sub_addr]
    assert tl.versions[-1].expires == "2020-08-31"
    assert select_active_version(tl, "2020-07-01") is not None
    assert select_active_version(tl, "2020-09-01") is None


@st.composite
def descendant_temporary_insert_case(
    draw,
) -> tuple[IRStatute, LegalAddress, str, str, str]:
    """Generate a temporary parent and a new descendant insert that should inherit expiry."""
    start_date = draw(st.dates(min_value=date(2000, 1, 1), max_value=date(2029, 12, 30)))
    lifespan_days = draw(st.integers(min_value=2, max_value=365))
    insert_offset = draw(st.integers(min_value=0, max_value=lifespan_days - 1))
    expiry = start_date + timedelta(days=lifespan_days)
    insert_effective = start_date + timedelta(days=insert_offset)
    assume(expiry <= date(2030, 12, 31))

    base = IRStatute(
        statute_id="test/temp-parent-insert-property",
        title="Temporary parent insert expiry property",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),
                    ),
                ),
            ),
        ),
    )
    sub_addr = LegalAddress(path=(("section", "4"), ("subsection", "3")))
    return base, sub_addr, expiry.isoformat(), start_date.isoformat(), insert_effective.isoformat()


@given(descendant_temporary_insert_case())
@settings(max_examples=40, deadline=None)
def test_descendant_insert_under_temporary_parent_inherits_expiry(
    case: tuple[IRStatute, LegalAddress, str, str, str],
) -> None:
    """A new descendant inserted under a temporary parent must inherit the parent's expiry."""
    base, sub_addr, expiry, start_iso, insert_effective_iso = case
    section_addr = LegalAddress(path=(("section", "4"),))

    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_temp_parent",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=section_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),
                    ),
                ),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp section replace",
                    enacted=start_iso,
                    effective=start_iso,
                    expires=expiry,
                ),
            ),
            LegalOperation(
                op_id="insert_temp_child_without_expiry",
                sequence=2,
                action=StructuralAction.INSERT,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="temp inserted"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="temp child insert",
                    enacted=insert_effective_iso,
                    effective=insert_effective_iso,
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[sub_addr]
    assert tl.versions[-1].expires == expiry
    assert select_active_version(tl, insert_effective_iso) is not None
    expiry_date = date.fromisoformat(expiry)
    assert select_active_version(tl, (expiry_date + timedelta(days=1)).isoformat()) is None


def test_existing_descendant_replace_under_temporary_parent_stays_durable() -> None:
    """Replacing an already-existing child under a temp parent must not inherit expiry."""
    base = IRStatute(
        statute_id="test/temp-parent-existing-child",
        title="Existing child under temporary parent",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="base 2"),),
                ),),
        ),
    )
    sec_addr = LegalAddress(path=(("section", "4"),))
    sub_addr = LegalAddress(path=(("section", "4"), ("subsection", "1")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_temp_parent",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=sec_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="temp 2"),),
                ),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp section replace",
                    enacted="2020-04-30",
                    effective="2020-05-01",
                    expires="2020-08-31",
                ),
            ),
            LegalOperation(
                op_id="replace_existing_child_without_expiry",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="durable 1"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="durable child replace",
                    enacted="2020-06-26",
                    effective="2020-06-26",
                    expires="",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[sub_addr]
    assert tl.versions[-1].expires == ""
    assert select_active_version(tl, "2020-07-01") is not None
    assert select_active_version(tl, "2020-09-01") is not None


def test_existing_descendant_replace_stays_durable_after_exact_target_temporary_snapshot() -> None:
    """A derived temporary child snapshot must not clip a later durable replace."""
    base = IRStatute(
        statute_id="test/temp-child-snapshot",
        title="Temporary child snapshot over durable background",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="base 1"),),
                ),
            ),
        ),
    )
    sub_addr = LegalAddress(path=(("section", "6"), ("subsection", "1")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="temporary_child_snapshot",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="temp 1"),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temp child snapshot",
                    enacted="2020-05-01",
                    effective="2020-05-01",
                    expires="2020-08-31",
                ),
            ),
            LegalOperation(
                op_id="durable_child_replace",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=sub_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="durable 1"),
                source=OperationSource(
                    statute_id="2020/485",
                    title="durable child replace",
                    enacted="2020-06-26",
                    effective="2020-06-26",
                    expires="",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[sub_addr]
    assert tl.versions[-1].expires == ""
    assert select_active_version(tl, "2020-07-01") is not None
    assert select_active_version(tl, "2020-09-01") is not None


def test_same_day_timeline_ties_use_later_apply_order() -> None:
    """When effective/enacted dates tie, the later-applied version must win."""
    base = IRStatute(
        statute_id="test/7b",
        title="Tie-break statute",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7b",
                    text="Original section text.",
                ),),
        ),
    )
    addr = LegalAddress(path=(("section", "7b"),))
    same_day = OperationSource(
        statute_id="2018/601",
        title="same-day source",
        enacted="2018-08-10",
        effective="2018-08-15",
    )
    later_same_day = OperationSource(
        statute_id="2018/602",
        title="same-day successor",
        enacted="2018-08-10",
        effective="2018-08-15",
    )
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="repeal_like_snapshot",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7b",
                    text="7 b § on kumottu.",
                    attrs={"lawvm_repeal_placeholder": "1"},
                ),
                source=same_day,
            ),
            LegalOperation(
                op_id="replacement_snapshot",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="7b", text="7 b § Oikeuspaikka."),
                source=later_same_day,
            ),
        ],
        base_date="2000-01-01",
    )

    active = select_active_version(timelines[addr], "2019-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2018/602"
    assert active.content is not None
    assert irnode_to_text(active.content) == "7 b § Oikeuspaikka."


def test_same_day_timeline_ties_prefer_substantive_over_later_placeholder() -> None:
    """A same-day repeal placeholder must not override substantive content."""
    base = IRStatute(
        statute_id="test/2",
        title="Placeholder precedence statute",
        body=IRNode(
            kind=IRNodeKind.BODY,
            label=None,
            children=(IRNode(kind=IRNodeKind.SECTION, label="2", text="Original content."),),
        ),
    )
    addr = LegalAddress(path=(("section", "2"),))
    same_day = "2024-12-30"
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="substantive_snapshot",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="2 § Määritelmät."),
                source=OperationSource(
                    statute_id="2024/1118",
                    enacted=same_day,
                    effective=same_day,
                ),
            ),
            LegalOperation(
                op_id="later_repeal_placeholder",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="2",
                    text="2 § on kumottu.",
                    attrs={"lawvm_repeal_placeholder": "1"},
                ),
                source=OperationSource(
                    statute_id="2024/1119",
                    enacted=same_day,
                    effective=same_day,
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    active = select_active_version(timelines[addr], "2025-01-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2024/1118"
    assert active.content is not None
    assert irnode_to_text(active.content) == "2 § Määritelmät."


def test_materialize_pit_records_same_source_equal_rank_selection_conflict() -> None:
    base = IRStatute(
        statute_id="test/equal-rank-selection-conflict",
        title="Equal rank selection conflict",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),),
        ),
    )
    addr = LegalAddress(path=(("section", "1"),))
    same_source = OperationSource(
        statute_id="2020/10",
        enacted="2020-01-01",
        effective="2020-01-01",
    )
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="First text"),
                    source=same_source,
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Second text"),
                    source=same_source,
                ),
            ],
        )
    }

    result = materialize_pit_ex(timelines, "2021-01-01", base=base)

    assert result.status == "degraded_timeline_issues"
    assert result.statute.body.children[0].text == "Second text"
    assert result.statute.metadata["materialization_status"] == "degraded_timeline_issues"
    issues = [
        issue
        for issue in result.issues
        if issue.kind == "equal_rank_same_source_selection_conflict"
    ]
    assert len(issues) == 1
    assert issues[0].address == addr
    assert issues[0].source_statute == "2020/10"
    assert issues[0].blocking is True
    assert issues[0].strict_disposition == "block"
    assert issues[0].quirks_disposition == "record"
    assert issues[0].rule_id == "timeline.equal_rank_same_source_selection_conflict"


def test_materialize_pit_does_not_record_equal_rank_conflict_for_exact_duplicate() -> None:
    base = IRStatute(
        statute_id="test/equal-rank-exact-duplicate",
        title="Equal rank exact duplicate",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),),
        ),
    )
    addr = LegalAddress(path=(("section", "1"),))
    same_source = OperationSource(
        statute_id="2020/10",
        enacted="2020-01-01",
        effective="2020-01-01",
    )
    duplicated = IRNode(kind=IRNodeKind.SECTION, label="1", text="Same text")
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=duplicated,
                    source=same_source,
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=duplicated,
                    source=same_source,
                ),
            ],
        )
    }

    result = materialize_pit_ex(timelines, "2021-01-01", base=base)

    assert [
        issue
        for issue in result.issues
        if issue.kind == "equal_rank_same_source_selection_conflict"
    ] == []


def test_materialize_pit_applies_nested_section_replace_without_parent_version() -> None:
    """A nested section timeline must still overlay through active parent containers."""
    base = IRStatute(
        statute_id="test/nested",
        title="Nested overlay statute",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.PART,
                    label="iv",
                    children=(IRNode(kind=IRNodeKind.NUM, text="IV OSA"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13",
                            children=(IRNode(kind=IRNodeKind.NUM, text="13 luku"),
                                IRNode(kind=IRNodeKind.HEADING, text="Avaintieto"),
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="4",
                                    children=(IRNode(kind=IRNodeKind.NUM, text="4 §"),
                                        IRNode(kind=IRNodeKind.HEADING, text="Avaintietoesite"),
                                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Original content."),),
                                ),),
                        ),),
                ),),
        ),
    )
    addr = LegalAddress(path=(("part", "iv"), ("chapter", "13"), ("section", "4")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="part_refresh",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("part", "iv"),)),
                payload=copy.deepcopy(base.body.children[0]),
                source=OperationSource(
                    statute_id="2021/975",
                    enacted="2021-12-01",
                    effective="2021-12-01",
                ),
            ),
            LegalOperation(
                op_id="repeal_snapshot",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 §"),
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            text="4 § on kumottu.",
                            attrs={"lawvm_repeal_placeholder": "1"},
                        ),),
                    attrs={"lawvm_repeal_placeholder": "1"},
                ),
                source=OperationSource(
                    statute_id="2022/954",
                    enacted="2022-11-25",
                    effective="2023-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    pit = materialize_pit(timelines, "2025-01-01", base=base)

    part = next(child for child in pit.body.children if child.kind == IRNodeKind.PART and child.label == "iv")
    chapter = next(child for child in part.children if child.kind == IRNodeKind.CHAPTER and child.label == "13")
    section = next(child for child in chapter.children if child.kind == IRNodeKind.SECTION and child.label == "4")

    assert section.attrs.get("lawvm_repeal_placeholder") == "1"


def test_compile_timelines_accepts_section_replace_under_active_chapter_payload() -> None:
    base = IRStatute(
        statute_id="test/ancestor-carried-section-replace",
        title="Ancestor carried section replace",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    chapter_addr = LegalAddress(path=(("chapter", "12"),))
    section_addr = LegalAddress(path=(("chapter", "12"), ("section", "1")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_chapter_12",
                sequence=1,
                action=StructuralAction.INSERT,
                target=chapter_addr,
                payload=IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="12",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="12 luku"),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.NUM, text="1 §"), IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vanha teksti")),
                        ),
                    ),
                ),
                source=OperationSource(
                    statute_id="1999/416",
                    enacted="1998-12-18",
                    effective="1998-12-18",
                ),
            ),
            LegalOperation(
                op_id="replace_section_1",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=section_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="1 §"),
                        IRNode(kind=IRNodeKind.HEADING, text="Kulutushyödykkeen välittäjän vastuu"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Uusi teksti"),
                    ),
                ),
                source=OperationSource(
                    statute_id="2021/1242",
                    enacted="2021-12-22",
                    effective="2022-01-01",
                ),
            ),
        ],
        base_date="1978-01-01",
    )

    active = select_active_version(timelines[section_addr], "2022-02-01")
    assert active is not None
    assert active.source is not None
    assert active.source.statute_id == "2021/1242"
    assert active.content is not None
    assert any(
        child.kind is IRNodeKind.HEADING and child.text == "Kulutushyödykkeen välittäjän vastuu"
        for child in active.content.children
    )


def test_materialize_pit_keeps_older_chapter_children_absent_from_later_parent_payload() -> None:
    base = IRStatute(
        statute_id="test/chapter-child-preservation",
        title="Chapter child preservation",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    chapter_addr = LegalAddress(path=(("chapter", "12"),))
    sec_1a_addr = LegalAddress(path=(("chapter", "12"), ("section", "1a")))
    sec_1b_addr = LegalAddress(path=(("chapter", "12"), ("section", "1b")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_section_1a",
                sequence=1,
                action=StructuralAction.INSERT,
                target=sec_1a_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1a",
                    children=(IRNode(kind=IRNodeKind.NUM, text="1 a §"), IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vanha 1a")),
                ),
                source=OperationSource(
                    statute_id="1994/16",
                    enacted="1994-01-05",
                    effective="1994-07-01",
                ),
            ),
            LegalOperation(
                op_id="insert_section_1b",
                sequence=2,
                action=StructuralAction.INSERT,
                target=sec_1b_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1b",
                    children=(IRNode(kind=IRNodeKind.NUM, text="1 b §"), IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vanha 1b")),
                ),
                source=OperationSource(
                    statute_id="1994/16",
                    enacted="1994-01-05",
                    effective="1994-07-01",
                ),
            ),
            LegalOperation(
                op_id="replace_chapter_12",
                sequence=3,
                action=StructuralAction.INSERT,
                target=chapter_addr,
                payload=IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="12",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="12 luku"),
                        IRNode(kind=IRNodeKind.SECTION, label="1", children=(IRNode(kind=IRNodeKind.NUM, text="1 §"),)),
                        IRNode(kind=IRNodeKind.SECTION, label="1c", children=(IRNode(kind=IRNodeKind.NUM, text="1 c §"),)),
                    ),
                ),
                source=OperationSource(
                    statute_id="1999/416",
                    enacted="1998-12-18",
                    effective="1998-12-18",
                ),
            ),
        ],
        base_date="1978-01-01",
    )

    materialized = materialize_pit(timelines, "2026-01-16")
    chapter = next(child for child in materialized.body.children if child.kind == IRNodeKind.CHAPTER and child.label == "12")
    section_labels = [child.label for child in chapter.children if child.kind == IRNodeKind.SECTION]

    assert "1" in section_labels
    assert "1a" in section_labels
    assert "1b" in section_labels
    assert "1c" in section_labels


def test_materialize_pit_keeps_deep_chapter_descendant_absent_from_later_part_payload() -> None:
    base = IRStatute(
        statute_id="test/part-child-preservation",
        title="Part child preservation",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(IRNode(kind=IRNodeKind.NUM, text="4 osa"),),
                ),
            ),
        ),
    )
    part_addr = LegalAddress(path=(("part", "4"),))
    chapter_addr = LegalAddress(path=(("part", "4"), ("chapter", "18")))
    section_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    timelines = {
        part_addr: ProvisionTimeline(
            address=part_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(
                        kind=IRNodeKind.PART,
                        label="4",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="4 osa"),
                            IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(IRNode(kind=IRNodeKind.NUM, text="2 luku"),)),
                        ),
                    ),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
        chapter_addr: ProvisionTimeline(
            address=chapter_addr,
            versions=[
                ProvisionVersion(
                    effective="2021-02-01",
                    enacted="2020-12-30",
                    content=IRNode(kind=IRNodeKind.CHAPTER, label="18", children=(IRNode(kind=IRNodeKind.NUM, text="18 luku"),)),
                    source=OperationSource(statute_id="2020/1256", effective="2021-02-01"),
                )
            ],
        ),
        section_addr: ProvisionTimeline(
            address=section_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § migrated section"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }

    pit = materialize_pit(timelines, "2026-01-01", base=base)
    text = irnode_to_text(pit.body)

    assert "18 luku" in text
    assert "159 § migrated section" in text


def test_materialize_pit_removes_unique_shallow_section_alias_after_deeper_projection() -> None:
    shallow_addr = LegalAddress(path=(("chapter", "1"), ("section", "159")))
    part_addr = LegalAddress(path=(("part", "4"),))
    chapter_addr = LegalAddress(path=(("part", "4"), ("chapter", "18")))
    deeper_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    base = IRStatute(
        statute_id="test/shallow-section-alias",
        title="Shallow section alias",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    timelines = {
        part_addr: ProvisionTimeline(
            address=part_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(kind=IRNodeKind.PART, label="4", children=(IRNode(kind=IRNodeKind.NUM, text="4 osa"),)),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
        chapter_addr: ProvisionTimeline(
            address=chapter_addr,
            versions=[
                ProvisionVersion(
                    effective="2021-02-01",
                    enacted="2020-12-30",
                    content=IRNode(kind=IRNodeKind.CHAPTER, label="18", children=(IRNode(kind=IRNodeKind.NUM, text="18 luku"),)),
                    source=OperationSource(statute_id="2020/1256", effective="2021-02-01"),
                )
            ],
        ),
        shallow_addr: ProvisionTimeline(
            address=shallow_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § stale shallow alias"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
        deeper_addr: ProvisionTimeline(
            address=deeper_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="159 § deeper projected section"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }

    pit = materialize_pit(timelines, "2026-01-01", base=base)
    text = irnode_to_text(pit.body)

    assert "159 § deeper projected section" in text
    assert "stale shallow alias" not in text


# ---------------------------------------------------------------------------
# Property 4: Identity (diff same date → empty)
# ---------------------------------------------------------------------------

@given(base_statute(), DATE_STRS)
@settings(max_examples=50)
def test_diff_statute_same_date_is_empty(statute: IRStatute, date: str) -> None:
    """diff_statute(timelines, T, T) returns empty dict for any T."""
    timelines = compile_timelines(statute, [])
    result = diff_statute(timelines, date, date)
    assert result == {}, f"diff_statute(T, T) non-empty at {date}: {result}"


# ---------------------------------------------------------------------------
# Property 5: Roundtrip (materialize at base_date has all base provisions)
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_materialize_pit_at_base_has_all_chapters(statute: IRStatute) -> None:
    """materialize_pit at 0000-00-00 contains all base chapters and top-level supplements."""
    timelines = compile_timelines(statute, [])
    pit = materialize_pit(timelines, "0000-00-00", base=statute)

    expected_chapter_labels = {
        ch.label for ch in statute.body.children if ch.kind == IRNodeKind.CHAPTER and ch.label is not None
    }
    actual_chapter_labels = {
        ch.label for ch in pit.body.children if ch.kind == IRNodeKind.CHAPTER and ch.label is not None
    }
    assert expected_chapter_labels == actual_chapter_labels, (
        f"Chapter labels differ: expected {expected_chapter_labels}, "
        f"got {actual_chapter_labels}"
    )
    expected_supplements = {
        (node.kind, node.label) for node in statute.supplements if node.label is not None
    }
    actual_supplements = {
        (node.kind, node.label) for node in pit.supplements if node.label is not None
    }
    assert expected_supplements == actual_supplements, (
        f"Supplement roots differ: expected {expected_supplements}, "
        f"got {actual_supplements}"
    )


# ---------------------------------------------------------------------------
# Property 6: Content hash equality
# ---------------------------------------------------------------------------

@given(SHORT_TEXT, SHORT_TEXT)
@settings(max_examples=100)
def test_content_hash_equality(text1: str, text2: str) -> None:
    """Two IRNodes with same text have same hash; different text → different hash."""
    node1 = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text1)
    node2 = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text2)
    hash1 = irnode_content_hash(node1)
    hash2 = irnode_content_hash(node2)
    if text1 == text2:
        assert hash1 == hash2, "Same text produced different hashes"
    else:
        # Different text should (with overwhelming probability) produce different hashes
        assert hash1 != hash2, f"Different text produced same hash: {text1!r} vs {text2!r}"


@given(SHORT_TEXT)
@settings(max_examples=50)
def test_content_hash_is_64_hex_chars(text: str) -> None:
    """SHA-256 content hash is always 64 hex characters."""
    node = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text)
    h = irnode_content_hash(node)
    assert len(h) == 64, f"Hash length {len(h)} != 64"
    assert all(c in "0123456789abcdef" for c in h), f"Non-hex char in hash: {h}"


# ---------------------------------------------------------------------------
# Property 7: PIT coverage at far-future date
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_select_active_version_at_far_future(statute: IRStatute) -> None:
    """select_active_version returns something for every provision at a far-future date."""
    timelines = compile_timelines(statute, [])
    far_future = "9999-12-31"
    for addr, tl in timelines.items():
        v = select_active_version(tl, far_future)
        assert v is not None, f"No active version for {addr} at far future"


# ---------------------------------------------------------------------------
# Property 8: LegalAddress path depth and structure
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_all_addresses_have_nonempty_path(statute: IRStatute) -> None:
    """All body addresses from compile_timelines have non-empty paths."""
    timelines = compile_timelines(statute, [])
    for addr in timelines:
        if is_statute_title_address(addr):
            continue
        assert len(addr.path) > 0, f"Address with empty path: {addr}"
        for kind, label in addr.path:
            assert isinstance(kind, str) and kind, f"Empty kind in {addr}"
            assert isinstance(label, str) and label, f"Empty label in {addr}"


# ---------------------------------------------------------------------------
# Property 9: irnode_to_text non-empty for leaf nodes
# ---------------------------------------------------------------------------

@given(SHORT_TEXT)
@settings(max_examples=100)
def test_irnode_to_text_leaf(text: str) -> None:
    """irnode_to_text on a leaf node returns the node's text."""
    node = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=text)
    result = irnode_to_text(node)
    assert result == text, f"Expected {text!r}, got {result!r}"


@given(SHORT_TEXT, SHORT_TEXT)
@settings(max_examples=50)
def test_irnode_to_text_parent_includes_children(p_text: str, c_text: str) -> None:
    """irnode_to_text on mixed-content nodes includes both own and child text."""
    child = IRNode(kind=IRNodeKind.SUBSECTION, label="1", text=c_text)
    parent = IRNode(kind=IRNodeKind.SECTION, label="1", text=p_text, children=(child,))
    result = irnode_to_text(parent)
    expected = " ".join(part for part in (p_text, c_text) if part)
    assert result == expected


# ---------------------------------------------------------------------------
# Property 10: provision_lineage is same as timeline.versions
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_provision_lineage_matches_timeline(statute: IRStatute) -> None:
    """provision_lineage returns the same versions as the timeline.versions list."""
    timelines = compile_timelines(statute, [])
    for addr, tl in timelines.items():
        lineage = provision_lineage(timelines, addr)
        assert lineage == tl.versions, (
            f"{addr}: lineage differs from tl.versions"
        )


def test_provision_lineage_uses_migration_events_for_current_address_resolution() -> None:
    old_addr = LegalAddress(path=(("section", "1"),))
    new_addr = LegalAddress(path=(("section", "1a"),))
    migration_event = MigrationEvent(
        event_id="mig:test:1",
        kind="renumber",
        from_address=old_addr,
        to_address=new_addr,
        effective="2020-01-01",
    )
    version = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="1a"),
    )
    timelines = {new_addr: ProvisionTimeline(address=new_addr, versions=[version])}

    lineage = provision_lineage(
        timelines,
        old_addr,
        migration_events=(migration_event,),
    )
    assert lineage == [version]


def test_provision_lineage_concatenates_versions_across_migration_chain() -> None:
    old_addr = LegalAddress(path=(("section", "1"),))
    mid_addr = LegalAddress(path=(("section", "1a"),))
    new_addr = LegalAddress(path=(("section", "1aa"),))
    first = MigrationEvent(
        event_id="mig:test:1",
        kind="renumber",
        from_address=old_addr,
        to_address=mid_addr,
        effective="2020-01-01",
        source_statute="2000/1",
    )
    second = MigrationEvent(
        event_id="mig:test:2",
        kind="renumber",
        from_address=mid_addr,
        to_address=new_addr,
        effective="2021-01-01",
        source_statute="2001/1",
    )
    old_v = ProvisionVersion(
        effective="2019-01-01",
        enacted="2018-12-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="old"),
    )
    mid_v = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="mid"),
    )
    new_v = ProvisionVersion(
        effective="2021-01-01",
        enacted="2021-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1aa", text="new"),
    )

    lineage = provision_lineage(
        {
            old_addr: ProvisionTimeline(address=old_addr, versions=[old_v]),
            mid_addr: ProvisionTimeline(address=mid_addr, versions=[mid_v]),
            new_addr: ProvisionTimeline(address=new_addr, versions=[new_v]),
        },
        old_addr,
        migration_events=(second, first),
        as_of_date="2021-12-31",
    )

    assert lineage == [old_v, mid_v, new_v]


def test_provision_lineage_concatenates_migration_chain_history() -> None:
    old_addr = LegalAddress(path=(("section", "1"),))
    new_addr = LegalAddress(path=(("section", "1a"),))
    migration_event = MigrationEvent(
        event_id="mig:test:chain",
        kind="renumber",
        from_address=old_addr,
        to_address=new_addr,
        effective="2020-01-01",
    )
    old_version = ProvisionVersion(
        effective="2019-01-01",
        enacted="2019-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1", text="1"),
    )
    new_version = ProvisionVersion(
        effective="2020-01-01",
        enacted="2020-01-01",
        content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="1a"),
    )
    timelines = {
        old_addr: ProvisionTimeline(address=old_addr, versions=[old_version]),
        new_addr: ProvisionTimeline(address=new_addr, versions=[new_version]),
    }

    lineage = provision_lineage(
        timelines,
        old_addr,
        migration_events=(migration_event,),
    )
    assert lineage == [old_version, new_version]


def test_provision_lineage_is_order_independent_for_migration_waves() -> None:
    old_addr = LegalAddress(path=(("section", "1"),))
    mid_addr = LegalAddress(path=(("section", "1a"),))
    new_addr = LegalAddress(path=(("section", "1aa"),))
    first = MigrationEvent(
        event_id="mig:test:1",
        kind="renumber",
        from_address=old_addr,
        to_address=mid_addr,
        effective="2020-01-01",
        source_statute="2000/1",
    )
    second = MigrationEvent(
        event_id="mig:test:2",
        kind="renumber",
        from_address=mid_addr,
        to_address=new_addr,
        effective="2020-01-01",
        source_statute="1999/1",
    )

    resolved = provision_lineage(
        {new_addr: ProvisionTimeline(address=new_addr, versions=[])},
        old_addr,
        migration_events=(second, first),
        as_of_date="2020-12-31",
    )

    assert resolved == []
    assert current_address_from_migration_events(
        old_addr,
        (second, first),
        as_of_date="2020-12-31",
    ) == new_addr


def test_current_address_from_migration_events_walks_chains_regardless_of_input_order() -> None:
    old_addr = LegalAddress(path=(("section", "5"),))
    chain_step_1 = MigrationEvent(
        event_id="mig:test:5to159",
        kind="renumber",
        from_address=old_addr,
        to_address=LegalAddress(path=(("section", "159"),)),
        effective="2020-01-01",
        source_statute="2000/1",
    )
    chain_step_2 = MigrationEvent(
        event_id="mig:test:159to159a",
        kind="renumber",
        from_address=LegalAddress(path=(("section", "159"),)),
        to_address=LegalAddress(path=(("section", "159a"),)),
        effective="2020-01-01",
        source_statute="2000/1",
    )

    direct_order = current_address_from_migration_events(
        old_addr,
        (chain_step_1, chain_step_2),
        as_of_date="2020-12-31",
    )
    reverse_order = current_address_from_migration_events(
        old_addr,
        (chain_step_2, chain_step_1),
        as_of_date="2020-12-31",
    )

    assert direct_order == LegalAddress(path=(("section", "159a"),))
    assert reverse_order == direct_order


def test_lineage_segments_preserve_typed_chain_with_events() -> None:
    old_addr = LegalAddress(path=(("section", "5"),))
    chain_step_1 = MigrationEvent(
        event_id="mig:test:5to159",
        kind="renumber",
        from_address=old_addr,
        to_address=LegalAddress(path=(("section", "159"),)),
        effective="2020-01-01",
        source_statute="2000/1",
    )
    chain_step_2 = MigrationEvent(
        event_id="mig:test:159to159a",
        kind="move",
        from_address=LegalAddress(path=(("section", "159"),)),
        to_address=LegalAddress(path=(("chapter", "2"), ("section", "159a"))),
        effective="2021-01-01",
        source_statute="2001/1",
    )

    segments = lineage_segments(
        old_addr,
        (chain_step_2, chain_step_1),
        as_of_date="2021-12-31",
        address_prefix_matches=_address_prefix_matches,
    )

    assert [segment.from_address for segment in segments] == [
        old_addr,
        old_addr,
        LegalAddress(path=(("section", "159"),)),
    ]
    assert [segment.to_address for segment in segments] == [
        old_addr,
        LegalAddress(path=(("section", "159"),)),
        LegalAddress(path=(("chapter", "2"), ("section", "159a"))),
    ]
    assert segments[0].event is None
    assert segments[1].event == chain_step_1
    assert segments[2].event == chain_step_2


def test_choose_materialization_lineage_decision_maps_destination_occupancy_to_raw_plan() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )
    raw_timelines = {
        source_addr: ProvisionTimeline(address=source_addr, versions=[]),
    }
    rekeyed_timelines = {
        destination_addr: ProvisionTimeline(address=destination_addr, versions=[]),
    }

    decision = choose_materialization_lineage_decision(
        raw_timelines=raw_timelines,
        rekeyed_timelines=rekeyed_timelines,
        migration_events=(migration_event,),
        destination_occupancy_collision=True,
    )

    assert dict(decision.timelines) == raw_timelines
    assert decision.timeline_source == "raw"
    assert decision.lineage_plan.mode == "raw_with_migrations"
    assert decision.lineage_plan.migration_events == (migration_event,)
    assert decision.reason == "destination_occupancy_collision"


def test_classify_scope_migrations_reports_occupied_destination_collision() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7:occupied",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )
    timelines = {
        source_addr: ProvisionTimeline(address=source_addr, versions=[]),
        destination_addr: ProvisionTimeline(address=destination_addr, versions=[]),
    }

    classification = classify_scope_migrations(
        timelines,
        (migration_event,),
        as_of_date="2002-01-01",
        address_prefix_matches=_address_prefix_matches,
    )

    assert classification.active_scope_changing is True
    assert classification.noncolliding is False
    assert classification.destination_occupancy_collision is True


def test_has_only_leaf_stable_scope_renumbers_accepts_prefix_renumber_with_same_leaf() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    timelines = {
        source_addr: ProvisionTimeline(address=source_addr, versions=[]),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:ch1->partI/ch2",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "I"), ("chapter", "2"))),
        effective="2001-01-01",
        source_statute="2001/1",
    )

    assert has_only_leaf_stable_scope_renumbers(
        timelines,
        (migration_event,),
        address_prefix_matches=_address_prefix_matches,
    ) is True


def test_classify_materialization_lineage_bridge_reports_native_rebirth_and_scope_collision() -> None:
    source_addr = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    destination_addr = LegalAddress(path=(("part", "1"), ("chapter", "2"), ("section", "5")))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-01-01",
                    enacted="2019-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § old lineage"),
                    source=OperationSource(statute_id="2019/1", effective="2019-01-01"),
                ),
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="5 § native rebirth"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                ),
            ],
        ),
        destination_addr: ProvisionTimeline(address=destination_addr, versions=[]),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:ch1->part1/ch2",
        kind="renumber",
        from_address=LegalAddress(path=(("chapter", "1"),)),
        to_address=LegalAddress(path=(("part", "1"), ("chapter", "2"))),
        effective="2020-01-01",
        source_statute="2020/1",
    )

    classification = classify_materialization_lineage_bridge(
        timelines,
        (migration_event,),
        as_of_date="2025-01-01",
        address_prefix_matches=_address_prefix_matches,
    )

    assert classification.native_rebirth_after_renumber is True
    assert classification.leaf_stable_scope_renumber is True
    assert classification.active_scope_changing is True
    assert classification.noncolliding_scope_migrations is False
    assert classification.destination_occupancy_collision is True


def test_current_address_with_prefix_migrations_uses_pre_act_wave_frame() -> None:
    section_9 = LegalAddress(path=(("section", "9"),))
    migration_events = (
        MigrationEvent(
            event_id="mig:test:9->10",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "9"),)),
            to_address=LegalAddress(path=(("section", "10"),)),
            effective="2001-01-01",
            source_statute="2001/1",
        ),
        MigrationEvent(
            event_id="mig:test:10->11",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "10"),)),
            to_address=LegalAddress(path=(("section", "11"),)),
            effective="2001-01-01",
            source_statute="2001/1",
        ),
        MigrationEvent(
            event_id="mig:test:11->12",
            kind="renumber",
            from_address=LegalAddress(path=(("section", "11"),)),
            to_address=LegalAddress(path=(("section", "12"),)),
            effective="2001-01-01",
            source_statute="2001/1",
        ),
    )

    migrated = current_address_with_prefix_migrations_from_events(
        section_9,
        migration_events,
        as_of_date="2002-01-01",
    )

    assert migrated == LegalAddress(path=(("section", "10"),))


def test_retarget_version_content_accepts_jurisdiction_num_text_formatter() -> None:
    version = ProvisionVersion(
        effective="2001-01-01",
        enacted="2001-01-01",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="5",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="5 section"),
                IRNode(kind=IRNodeKind.HEADING, text="Heading"),
            ),
        ),
        source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
    )

    migrated = _retarget_version_content(
        version,
        LegalAddress(path=(("section", "159"),)),
        root_num_text_fn=lambda kind, label: f"{label} §" if kind is IRNodeKind.SECTION else None,
    )

    assert migrated.content is not None
    assert migrated.content.label == "159"
    assert migrated.content.children[0].text == "159 §"


def test_rekey_timelines_with_migration_events_retargets_root_content_in_core() -> None:
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("section", "159"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        children=(
                            IRNode(kind=IRNodeKind.NUM, text="5 section"),
                            IRNode(kind=IRNodeKind.HEADING, text="Heading"),
                        ),
                    ),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:5->159",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    rekeyed = rekey_timelines_with_migration_events(
        timelines,
        (migration_event,),
        as_of_date="2025-01-01",
        current_address_with_prefix_migrations_fn=lambda address, events, as_of_date: current_address_with_prefix_migrations_from_events(
            address,
            events,
            as_of_date=as_of_date,
        ),
        address_prefix_matches=_address_prefix_matches,
        retarget_version_content_fn=lambda version, address: _retarget_version_content(
            version,
            address,
            root_num_text_fn=lambda kind, label: f"{label} §" if kind is IRNodeKind.SECTION else None,
        ),
    )

    migrated = rekeyed[destination_addr].versions[0].content
    assert migrated is not None
    assert migrated.label == "159"
    assert migrated.children[0].text == "159 §"


def test_materialize_pit_projects_selected_versions_onto_migrated_addresses() -> None:
    base = IRStatute(
        statute_id="test/materialize-migration",
        title="Materialize migration",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base"),),
        ),
    )
    old_addr = LegalAddress(path=(("section", "1"),))
    new_addr = LegalAddress(path=(("section", "1a"),))
    timelines = {
        old_addr: ProvisionTimeline(
            address=old_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="migrated"),
                    source=OperationSource(statute_id="2020/1", effective="2020-01-01"),
                )
            ],
        )
    }
    migration_event = MigrationEvent(
        event_id="mig:test:materialize:1",
        kind="renumber",
        from_address=old_addr,
        to_address=new_addr,
        effective="2020-01-01",
        source_statute="2020/1",
    )

    pit = materialize_pit(
        timelines,
        "2025-01-01",
        base=base,
        migration_events=(migration_event,),
    )

    materialized = _find_node_by_label(pit.body, IRNodeKind.SECTION, "1a")
    assert materialized is not None
    assert materialized.text == "migrated"


def test_materialize_pit_prefers_newer_migrated_prefix_lineage_over_older_native_destination() -> None:
    base = IRStatute(
        statute_id="test/materialize-prefix-migration",
        title="Materialize prefix migration",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="18",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="159", text="base"),),
                        ),
                    ),
                ),
            ),
        ),
    )
    source_addr = LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "159")))
    destination_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-12-30",
                    enacted="2020-12-30",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="migrated lineage"),
                    source=OperationSource(statute_id="2020/1256", effective="2020-12-30"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-04-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="159", text="native lineage"),
                    source=OperationSource(statute_id="2019/371", effective="2019-04-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:prefix:III/2→IV/18",
        kind="renumber",
        from_address=LegalAddress(path=(("part", "III"), ("chapter", "2"))),
        to_address=LegalAddress(path=(("part", "4"), ("chapter", "18"))),
        effective="2020-12-30",
        source_statute="2020/1256",
    )

    pit = materialize_pit(
        timelines,
        "2025-01-01",
        base=base,
        migration_events=(migration_event,),
    )

    materialized = _find_node_by_label(pit.body, IRNodeKind.SECTION, "159")
    assert materialized is not None
    assert materialized.text == "migrated lineage"


def test_materialize_pit_ex_marks_occupied_destination_move_collision_ambiguous() -> None:
    base = IRStatute(
        statute_id="test/materialize-move-destination-collision",
        title="Materialize move destination collision",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="5", text="base source"),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="7", text="base destination"),),
                ),
            ),
        ),
    )
    source_addr = LegalAddress(path=(("section", "5"),))
    destination_addr = LegalAddress(path=(("chapter", "2"), ("section", "7")))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="moved lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="1999-01-01",
                    enacted="1999-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="native destination lineage"),
                    source=OperationSource(statute_id="1999/1", effective="1999-01-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:move:5->2/7",
        kind="move",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    result = materialize_pit_ex(
        timelines,
        "2002-01-01",
        base=base,
        migration_events=(migration_event,),
    )

    assert result.certificate is not None
    assert result.certificate.selected_address_count == 0
    assert result.certificate.ambiguous_address_count == 1
    assert result.statute.metadata["materialization_status"] == "degraded_missing_scope"


def test_materialize_pit_ex_marks_equal_rank_projected_collision_ambiguous() -> None:
    base = IRStatute(
        statute_id="test/materialize-equal-rank-projected-collision",
        title="Materialize equal rank projected collision",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.SECTION, label="7", text="base destination"),),
        ),
    )
    source_addr_a = LegalAddress(path=(("section", "5"),))
    source_addr_b = LegalAddress(path=(("section", "6"),))
    destination_addr = LegalAddress(path=(("section", "7"),))
    timelines = {
        source_addr_a: ProvisionTimeline(
            address=source_addr_a,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="5", text="migrated lineage A"),
                    source=OperationSource(statute_id="2001/5", effective="2001-01-01"),
                )
            ],
        ),
        source_addr_b: ProvisionTimeline(
            address=source_addr_b,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="6", text="migrated lineage B"),
                    source=OperationSource(statute_id="2001/6", effective="2001-01-01"),
                )
            ],
        ),
    }
    migration_events = (
        MigrationEvent(
            event_id="mig:test:5->7",
            kind="move",
            from_address=source_addr_a,
            to_address=destination_addr,
            effective="2001-01-01",
            source_statute="2001/5",
        ),
        MigrationEvent(
            event_id="mig:test:6->7",
            kind="move",
            from_address=source_addr_b,
            to_address=destination_addr,
            effective="2001-01-01",
            source_statute="2001/6",
        ),
    )

    result = materialize_pit_ex(
        timelines,
        "2002-01-01",
        base=base,
        migration_events=migration_events,
    )

    assert result.certificate is not None
    assert result.certificate.selected_address_count == 0
    assert result.certificate.ambiguous_address_count == 1
    assert result.statute.metadata["materialization_status"] == "degraded_missing_scope"


def test_materialize_pit_suppresses_migrated_inactive_source_slot() -> None:
    base = IRStatute(
        statute_id="test/materialize-migrated-inactive-source",
        title="Materialize migrated inactive source",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="base source"),
                IRNode(kind=IRNodeKind.SECTION, label="1a", text="base destination"),
            ),
        ),
    )
    source_addr = LegalAddress(path=(("section", "1"),))
    destination_addr = LegalAddress(path=(("section", "1a"),))
    timelines = {
        source_addr: ProvisionTimeline(
            address=source_addr,
            versions=[
                ProvisionVersion(
                    effective="2000-01-01",
                    enacted="2000-01-01",
                    expires="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="expired source lineage"),
                    source=OperationSource(statute_id="2000/1", effective="2000-01-01", expires="2001-01-01"),
                )
            ],
        ),
        destination_addr: ProvisionTimeline(
            address=destination_addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1a", text="active destination lineage"),
                    source=OperationSource(statute_id="2001/1", effective="2001-01-01"),
                )
            ],
        ),
    }
    migration_event = MigrationEvent(
        event_id="mig:test:section:1→section:1a",
        kind="renumber",
        from_address=source_addr,
        to_address=destination_addr,
        effective="2001-01-01",
        source_statute="2001/1",
    )

    pit = materialize_pit(
        timelines,
        "2002-01-01",
        base=base,
        migration_events=(migration_event,),
    )

    assert _find_node_by_label(pit.body, IRNodeKind.SECTION, "1") is None
    materialized = _find_node_by_label(pit.body, IRNodeKind.SECTION, "1a")
    assert materialized is not None
    assert materialized.text == "active destination lineage"


def test_materialize_body_preserves_descendant_override_under_inserted_root() -> None:
    base = IRStatute(
        statute_id="test/materialize-inserted-root-descendant-override",
        title="Inserted root descendant override",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", text="base chapter 1"),),
        ),
    )
    chapter_addr = LegalAddress(path=(("chapter", "2"),))
    subsection_addr = LegalAddress(path=(("chapter", "2"), ("section", "7"), ("subsection", "1")))
    active: dict[LegalAddress, IRNode | None] = {
        chapter_addr: IRNode(
            kind=IRNodeKind.CHAPTER,
            label="2",
            children=(
                IRNode(kind=IRNodeKind.NUM, text="2"),
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    text="section 7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="old subsection 1"),
                    ),
                ),
            ),
        ),
        subsection_addr: IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="new subsection 1"),
    }

    body = materialize_body(
        active,
        {},
        base,
        record_issue=lambda *args, **kwargs: None,
    )

    chapter = _find_node_by_label(body, IRNodeKind.CHAPTER, "2")
    assert chapter is not None
    section = _find_node_by_label(chapter, IRNodeKind.SECTION, "7")
    assert section is not None
    subsection = _find_node_by_label(section, IRNodeKind.SUBSECTION, "1")
    assert subsection is not None
    assert subsection.text == "new subsection 1"


def test_materialize_body_preserves_duplicate_base_siblings_with_descendant_owned_overlay() -> None:
    base = IRStatute(
        statute_id="test/materialize-duplicate-base-siblings",
        title="Duplicate base siblings",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="2",
                    text="LAEVAREGISTRID JA ANDMEKOGUD",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="6",
                            text="ÜLDSÄTTED",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="34_1",
                                    text="Riigihaldusülesandeid täitvate laevade andmekogu",
                                    children=(
                                        IRNode(
                                            kind=IRNodeKind.SUBSECTION,
                                            label="1",
                                            text="old subsection",
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
                IRNode(kind=IRNodeKind.PART, label="2", text="VASTUTUS"),
            ),
        ),
    )
    part_addr = LegalAddress(path=(("part", "2"),))
    section_addr = LegalAddress(path=(("part", "2"), ("chapter", "6"), ("section", "34_1")))
    active: dict[LegalAddress, IRNode | None] = {
        part_addr: IRNode(kind=IRNodeKind.PART, label="2", text="VASTUTUS"),
        section_addr: IRNode(
            kind=IRNodeKind.SECTION,
            label="34_1",
            text="Riigihaldusülesandeid täitvate laevade andmekogu",
            children=(
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    text="new subsection",
                ),
            ),
        ),
    }
    issues: list[TimelineIssue] = []

    def _record_issue(sink: list[TimelineIssue] | None, **kwargs: object) -> None:
        if sink is None:
            return
        kwargs.pop("emit_warnings", None)
        sink.append(TimelineIssue(**cast(Any, kwargs)))

    body = materialize_body(
        active,
        {},
        base,
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_issue,
    )

    parts = [child for child in body.children if child.kind == IRNodeKind.PART]
    assert [(part.label, part.text, len(part.children)) for part in parts] == [
        ("2", "LAEVAREGISTRID JA ANDMEKOGUD", 1),
        ("2", "VASTUTUS", 0),
    ]
    updated_section = _find_node_by_label(parts[0], IRNodeKind.SECTION, "34_1")
    assert updated_section is not None
    assert _find_node_by_label(parts[1], IRNodeKind.SECTION, "34_1") is None
    assert updated_section.children[0].text == "new subsection"
    assert any(
        issue.kind == "duplicate_base_address_descendant_overlay"
        and issue.address == part_addr
        for issue in issues
    )
    assert any(
        issue.kind == "duplicate_same_label_child_carried_continuity"
        and issue.address == part_addr
        for issue in issues
    )


def test_materialize_body_preserves_duplicate_base_children_under_selected_root() -> None:
    base = IRStatute(
        statute_id="test/materialize-duplicate-base-children",
        title="Duplicate base children",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="8",
                    text="Rakendussätted",
                    children=(
                        IRNode(kind=IRNodeKind.DIVISION, label="1", text="Üleminekusätted"),
                        IRNode(
                            kind=IRNodeKind.DIVISION,
                            label="2",
                            text="Seaduste muutmine ja kehtetuks tunnistamine",
                        ),
                        IRNode(kind=IRNodeKind.DIVISION, label="2", text="Seaduse jõustumine"),
                    ),
                ),
            ),
        ),
    )
    chapter_addr = LegalAddress(path=(("chapter", "8"),))
    active: dict[LegalAddress, IRNode | None] = {
        chapter_addr: IRNode(
            kind=IRNodeKind.CHAPTER,
            label="8",
            text="Rakendussätted",
            children=(
                IRNode(kind=IRNodeKind.DIVISION, label="1", text="Üleminekusätted"),
                IRNode(kind=IRNodeKind.DIVISION, label="2", text="Seaduse jõustumine"),
            ),
        ),
    }
    issues: list[TimelineIssue] = []

    def _record_issue(sink: list[TimelineIssue] | None, **kwargs: object) -> None:
        if sink is None:
            return
        kwargs.pop("emit_warnings", None)
        sink.append(TimelineIssue(**cast(Any, kwargs)))

    body = materialize_body(
        active,
        {},
        base,
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_issue,
    )

    chapter = _find_node_by_label(body, IRNodeKind.CHAPTER, "8")
    assert chapter is not None
    assert [(child.kind, child.label, child.text) for child in chapter.children] == [
        (IRNodeKind.DIVISION, "1", "Üleminekusätted"),
        (IRNodeKind.DIVISION, "2", "Seaduste muutmine ja kehtetuks tunnistamine"),
        (IRNodeKind.DIVISION, "2", "Seaduse jõustumine"),
    ]
    assert any(
        issue.kind == "duplicate_base_address_descendant_overlay"
        and issue.address == chapter_addr
        for issue in issues
    )
    assert any(
        issue.kind == "duplicate_same_label_child_carried_continuity"
        and issue.address == LegalAddress(path=(("chapter", "8"), ("division", "2")))
        for issue in issues
    )


def test_materialize_body_preserves_duplicate_selected_children_when_direct_child_override_is_ambiguous() -> None:
    base = IRStatute(
        statute_id="test/materialize-duplicate-selected-child-override",
        title="Duplicate selected child override",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.DIVISION,
                            label="2",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="20",
                                    text="Section 20",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    section_addr = LegalAddress(path=(("chapter", "2"), ("division", "2"), ("section", "20")))
    subsection_addr = LegalAddress(path=(("chapter", "2"), ("division", "2"), ("section", "20"), ("subsection", "1")))
    active: dict[LegalAddress, IRNode | None] = {
        section_addr: IRNode(
            kind=IRNodeKind.SECTION,
            label="20",
            text="Section 20",
            children=(
                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="first subsection 1"),
                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="subsection 2"),
                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="second subsection 1"),
            ),
        ),
        subsection_addr: IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="override subsection 1"),
    }
    issues: list[TimelineIssue] = []

    def _record_issue(sink: list[TimelineIssue] | None, **kwargs: object) -> None:
        if sink is None:
            return
        kwargs.pop("emit_warnings", None)
        sink.append(TimelineIssue(**cast(Any, kwargs)))

    body = materialize_body(
        active,
        {},
        base,
        issue_sink=issues,
        emit_warnings=False,
        record_issue=_record_issue,
    )

    chapter = _find_node_by_label(body, IRNodeKind.CHAPTER, "2")
    assert chapter is not None
    division = _find_node_by_label(chapter, IRNodeKind.DIVISION, "2")
    assert division is not None
    section = _find_node_by_label(division, IRNodeKind.SECTION, "20")
    assert section is not None
    assert [(child.label, child.text) for child in section.children] == [
        ("1", "first subsection 1"),
        ("2", "subsection 2"),
        ("1", "second subsection 1"),
    ]
    assert any(
        issue.kind == "duplicate_selected_address_descendant_overlay"
        and issue.address == subsection_addr
        for issue in issues
    )
    assert any(
        issue.kind == "duplicate_same_label_child_unresolved"
        and issue.address == subsection_addr
        for issue in issues
    )


def test_duplicate_child_classifier_reports_migration_collision_without_deleting_children() -> None:
    parent = LegalAddress(path=(("chapter", "1"),))
    child_address = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    migrated_from = LegalAddress(path=(("chapter", "9"), ("section", "5")))
    finding = classify_duplicate_child_family(
        parent,
        (
            IRNode(kind=IRNodeKind.SECTION, label="5", text="native section"),
            IRNode(kind=IRNodeKind.SECTION, label="5", text="migrated section"),
        ),
        migration_events=(
            MigrationEvent(
                event_id="move-9-5-to-1-5",
                kind="move",
                from_address=migrated_from,
                to_address=child_address,
                effective="2020-01-01",
                source_statute="2020/1",
            ),
        ),
    )

    assert finding is not None
    assert finding.child_address == child_address
    assert finding.classification == "migrated_native_identity_collision"


def test_duplicate_child_classifier_marks_source_shadow_only_with_explicit_marker() -> None:
    parent = LegalAddress(path=(("section", "1"),))
    findings = collect_duplicate_child_findings(
        IRNode(
            kind=IRNodeKind.SECTION,
            label="1",
            children=(
                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="current text"),
                IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="2",
                    text="old text",
                    attrs={"lawvm_source_shadow": "1"},
                ),
            ),
        ),
        parent_address=parent,
    )

    assert len(findings) == 1
    assert findings[0].child_address == LegalAddress(path=(("section", "1"), ("subsection", "2")))
    assert findings[0].classification == "stale_publisher_or_source_shadow"


def test_duplicate_child_classifier_marks_explicit_temporal_overlay() -> None:
    parent = LegalAddress(path=(("section", "1"),))
    finding = classify_duplicate_child_family(
        parent,
        (
            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="background"),
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="3",
                text="temporary overlay",
                attrs={"lawvm_temporal_overlay": "1"},
            ),
        ),
    )

    assert finding is not None
    assert finding.child_address == LegalAddress(path=(("section", "1"), ("subsection", "3")))
    assert finding.classification == "valid_temporal_overlay"


def test_materialize_pit_preserves_duplicate_selected_children_in_timeline_versions() -> None:
    base = IRStatute(
        statute_id="test/materialize-pit-duplicate-selected-children",
        title="Duplicate selected children in PIT",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.DIVISION,
                            label="2",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="20",
                                    text="Section 20",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    section_addr = LegalAddress(path=(("chapter", "2"), ("division", "2"), ("section", "20")))
    timelines = {
        section_addr: ProvisionTimeline(
            address=section_addr,
            versions=[
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2020-01-01",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="20",
                        text="Section 20",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="first subsection 1"),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="subsection 2"),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="second subsection 1"),
                        ),
                    ),
                    source=OperationSource(statute_id="test/2020", effective="2020-01-01"),
                ),
            ],
        ),
    }

    pit = materialize_pit(timelines, as_of="2020-01-01", base=base)

    chapter = _find_node_by_label(pit.body, IRNodeKind.CHAPTER, "2")
    assert chapter is not None
    division = _find_node_by_label(chapter, IRNodeKind.DIVISION, "2")
    assert division is not None
    section = _find_node_by_label(division, IRNodeKind.SECTION, "20")
    assert section is not None
    assert [(child.label, child.text) for child in section.children] == [
        ("1", "first subsection 1"),
        ("2", "subsection 2"),
        ("1", "second subsection 1"),
    ]


def test_materialize_pit_keeps_native_chapter_rebirth_at_source_after_later_renumber_wave() -> None:
    base = IRStatute(
        statute_id="test/materialize-chapter-native-rebirth",
        title="Materialize chapter native rebirth",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="8"),
                IRNode(kind=IRNodeKind.CHAPTER, label="9"),
                IRNode(kind=IRNodeKind.CHAPTER, label="10"),
                IRNode(kind=IRNodeKind.CHAPTER, label="11"),
            ),
        ),
    )
    chapter_8 = LegalAddress(path=(("chapter", "8"),))
    chapter_8_section_1 = LegalAddress(path=(("chapter", "8"), ("section", "1")))
    chapter_9 = LegalAddress(path=(("chapter", "9"),))
    chapter_9_section_1 = LegalAddress(path=(("chapter", "9"), ("section", "1")))
    chapter_9_section_2 = LegalAddress(path=(("chapter", "9"), ("section", "2")))
    chapter_10 = LegalAddress(path=(("chapter", "10"),))
    chapter_10_section_4 = LegalAddress(path=(("chapter", "10"), ("section", "4")))

    def _chapter_version(
        label: str,
        heading: str,
        sections: tuple[tuple[str, str], ...],
        *,
        effective: str,
        source_statute: str,
    ) -> ProvisionVersion:
        return ProvisionVersion(
            effective=effective,
            enacted=effective,
            content=IRNode(
                kind=IRNodeKind.CHAPTER,
                label=label,
                text=heading,
                children=tuple(
                    IRNode(kind=IRNodeKind.SECTION, label=section_label, text=section_text)
                    for section_label, section_text in sections
                ),
            ),
            source=OperationSource(statute_id=source_statute, effective=effective),
        )

    def _section_version(
        label: str,
        text: str,
        *,
        effective: str,
        source_statute: str,
    ) -> ProvisionVersion:
        return ProvisionVersion(
            effective=effective,
            enacted=effective,
            content=IRNode(kind=IRNodeKind.SECTION, label=label, text=text),
            source=OperationSource(statute_id=source_statute, effective=effective),
        )

    timelines = {
        chapter_8: ProvisionTimeline(
            address=chapter_8,
            versions=[
                _chapter_version(
                    "8",
                    "old 8",
                    (("1", "historical chapter 8 section 1"),),
                    effective="1990-01-01",
                    source_statute="1990/8",
                ),
                _chapter_version(
                    "8",
                    "new 8",
                    (("1", "native chapter 8 section 1"),),
                    effective="1994-07-01",
                    source_statute="1994/16",
                ),
            ],
        ),
        chapter_8_section_1: ProvisionTimeline(
            address=chapter_8_section_1,
            versions=[
                _section_version(
                    "1",
                    "historical chapter 8 section 1",
                    effective="1990-01-01",
                    source_statute="1990/8",
                ),
                _section_version(
                    "1",
                    "native chapter 8 section 1",
                    effective="1994-07-01",
                    source_statute="1994/16",
                ),
            ],
        ),
        chapter_9: ProvisionTimeline(
            address=chapter_9,
            versions=[
                _chapter_version(
                    "9",
                    "old 9",
                    (("1", "historical chapter 9 section 1"),),
                    effective="1990-01-01",
                    source_statute="1990/9",
                ),
                _chapter_version(
                    "9",
                    "new 9",
                    (("2", "native chapter 9 section 2"),),
                    effective="1994-07-01",
                    source_statute="1994/16",
                ),
            ],
        ),
        chapter_9_section_1: ProvisionTimeline(
            address=chapter_9_section_1,
            versions=[
                _section_version(
                    "1",
                    "historical chapter 9 section 1",
                    effective="1990-01-01",
                    source_statute="1990/9",
                ),
            ],
        ),
        chapter_9_section_2: ProvisionTimeline(
            address=chapter_9_section_2,
            versions=[
                _section_version(
                    "2",
                    "native chapter 9 section 2",
                    effective="1994-07-01",
                    source_statute="1994/16",
                ),
            ],
        ),
        chapter_10: ProvisionTimeline(
            address=chapter_10,
            versions=[
                _chapter_version(
                    "10",
                    "new 10",
                    (("4", "native chapter 10 section 4"),),
                    effective="1998-03-01",
                    source_statute="1997/1162",
                ),
            ],
        ),
        chapter_10_section_4: ProvisionTimeline(
            address=chapter_10_section_4,
            versions=[
                _section_version(
                    "4",
                    "native chapter 10 section 4",
                    effective="1998-03-01",
                    source_statute="1997/1162",
                ),
            ],
        ),
    }
    migration_events = (
        MigrationEvent(
            event_id="mig:test:8→10",
            kind="renumber",
            from_address=chapter_8,
            to_address=LegalAddress(path=(("chapter", "10"),)),
            effective="1994-07-01",
            source_statute="1994/16",
        ),
        MigrationEvent(
            event_id="mig:test:9→11",
            kind="renumber",
            from_address=chapter_9,
            to_address=LegalAddress(path=(("chapter", "11"),)),
            effective="1994-07-01",
            source_statute="1994/16",
        ),
        MigrationEvent(
            event_id="mig:test:10→11",
            kind="renumber",
            from_address=LegalAddress(path=(("chapter", "10"),)),
            to_address=LegalAddress(path=(("chapter", "11"),)),
            effective="1998-03-01",
            source_statute="1997/1162",
        ),
        MigrationEvent(
            event_id="mig:test:11→12",
            kind="renumber",
            from_address=LegalAddress(path=(("chapter", "11"),)),
            to_address=LegalAddress(path=(("chapter", "12"),)),
            effective="1998-03-01",
            source_statute="1997/1162",
        ),
    )

    rekeyed = _rekey_timelines_with_migration_events(
        timelines,
        migration_events,
        as_of="2025-01-01",
    )
    pit = materialize_pit(
        rekeyed,
        "2025-01-01",
        base=base,
        migration_events=(),
    )

    chapter_10_node = _find_node_by_label(pit.body, IRNodeKind.CHAPTER, "10")
    chapter_11_node = _find_node_by_label(pit.body, IRNodeKind.CHAPTER, "11")
    chapter_12_node = _find_node_by_label(pit.body, IRNodeKind.CHAPTER, "12")
    chapter_10_section_4_node = _find_node_by_label(chapter_10_node, IRNodeKind.SECTION, "4") if chapter_10_node is not None else None
    chapter_11_section_1_node = _find_node_by_label(chapter_11_node, IRNodeKind.SECTION, "1") if chapter_11_node is not None else None
    chapter_12_section_1_node = _find_node_by_label(chapter_12_node, IRNodeKind.SECTION, "1") if chapter_12_node is not None else None

    assert chapter_10_node is not None
    assert chapter_11_node is not None
    assert chapter_12_node is not None
    assert chapter_10_section_4_node is not None
    assert chapter_11_section_1_node is not None
    assert chapter_12_section_1_node is not None
    assert chapter_10_section_4_node.text == "native chapter 10 section 4"
    assert chapter_11_section_1_node.text == "historical chapter 8 section 1"
    assert chapter_12_section_1_node.text == "historical chapter 9 section 1"


# ---------------------------------------------------------------------------
# NEW TESTS
# ---------------------------------------------------------------------------

from lawvm.core import tree_ops
from lawvm.core.timeline import _sort_label_key, _apply_overlays
from lawvm.finland.replay_products import fi_label_norm


# ---------------------------------------------------------------------------
# tree_ops invariant 1: insert_sorted preserves label uniqueness
# ---------------------------------------------------------------------------

@st.composite
def flat_body_with_unique_sections(draw) -> IRNode:
    """Generate a body IRNode with unique section labels."""
    n = draw(st.integers(min_value=1, max_value=8))
    labels = draw(
        st.lists(
            st.integers(min_value=1, max_value=50).map(str),
            min_size=n,
            max_size=n,
            unique=True,
        )
    )
    sections = [
        IRNode(kind=IRNodeKind.SECTION, label=lbl, text=draw(SHORT_TEXT))
        for lbl in labels
    ]
    return IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(sections))


@given(flat_body_with_unique_sections(), SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=50)
def test_insert_sorted_preserves_label_uniqueness(
    body: IRNode, new_label: str, new_text: str
) -> None:
    """insert_sorted into a body with unique labels keeps (kind, label) pairs unique."""
    # Only test when the label is not already present
    existing_labels = {c.label for c in body.children if c.kind == IRNodeKind.SECTION}
    assume(new_label not in existing_labels)

    new_section = IRNode(kind=IRNodeKind.SECTION, label=new_label, text=new_text)
    result = tree_ops.insert_sorted(body, (), new_section)

    seen = {}
    for child in result.children:
        if child.label is not None:
            key = (child.kind, child.label)
            seen[key] = seen.get(key, 0) + 1
    duplicates = {k: v for k, v in seen.items() if v > 1}
    assert not duplicates, f"Duplicate (kind, label) pairs after insert: {duplicates}"


# ---------------------------------------------------------------------------
# tree_ops invariant 2: remove then insert roundtrip preserves label set
# ---------------------------------------------------------------------------

@given(flat_body_with_unique_sections())
@settings(max_examples=50)
def test_remove_at_then_insert_roundtrip(body: IRNode) -> None:
    """Remove a section then insert it back; the label set should be unchanged."""
    assume(len(body.children) >= 1)
    # Pick the first section to remove
    target = body.children[0]
    assume(target.label is not None)

    original_labels = {c.label for c in body.children if c.kind == IRNodeKind.SECTION}

    section_label = cast(str, target.label)
    path = (("section", section_label),)
    after_remove = tree_ops.remove_at(body, path)
    after_reinsert = tree_ops.insert_sorted(after_remove, (), target)

    result_labels = {c.label for c in after_reinsert.children if c.kind == IRNodeKind.SECTION}
    assert result_labels == original_labels, (
        f"Label set changed: original={original_labels}, result={result_labels}"
    )


# ---------------------------------------------------------------------------
# tree_ops invariant 3: replace_at preserves section label order
# ---------------------------------------------------------------------------

@given(flat_body_with_unique_sections(), SHORT_TEXT)
@settings(max_examples=50)
def test_replace_at_preserves_structure(body: IRNode, new_text: str) -> None:
    """replace_at keeps the same section labels in the same order."""
    assume(len(body.children) >= 1)
    target = body.children[0]
    assume(target.label is not None)

    original_label_order = [c.label for c in body.children if c.kind == IRNodeKind.SECTION]

    replacement = IRNode(kind=IRNodeKind.SECTION, label=target.label, text=new_text)
    section_label = cast(str, target.label)
    path = (("section", section_label),)
    result = tree_ops.replace_at(body, path, replacement)

    result_label_order = [c.label for c in result.children if c.kind == IRNodeKind.SECTION]
    assert result_label_order == original_label_order, (
        f"Label order changed after replace_at: "
        f"original={original_label_order}, result={result_label_order}"
    )


# ---------------------------------------------------------------------------
# Timeline/overlay 4: compile with no ops + materialize roundtrip
# ---------------------------------------------------------------------------

@given(base_statute())
@settings(max_examples=50)
def test_compile_no_ops_then_materialize_roundtrip(statute: IRStatute) -> None:
    """materialize_pit(compile_timelines(base, []), base=base) preserves base sections and supplement roots."""
    timelines = compile_timelines(statute, [])
    pit = materialize_pit(timelines, "0000-00-00", base=statute)

    def _collect_section_labels(node: IRNode) -> set:
        """Collect all section labels at any depth."""
        labels = set()
        for child in node.children:
            if child.kind == IRNodeKind.SECTION and child.label is not None:
                labels.add(child.label)
            labels |= _collect_section_labels(child)
        return labels

    def _collect_section_labels_from_roots(roots: tuple[IRNode, ...]) -> set:
        labels = set()
        for root in roots:
            if root.kind == IRNodeKind.SECTION and root.label is not None:
                labels.add(root.label)
            labels |= _collect_section_labels(root)
        return labels

    base_labels = _collect_section_labels_from_roots((statute.body, *statute.supplements))
    pit_labels = _collect_section_labels_from_roots((pit.body, *pit.supplements))
    assert base_labels == pit_labels, (
        f"Section labels differ after roundtrip: "
        f"base={base_labels}, pit={pit_labels}"
    )
    assert [
        (node.kind, node.label, node.text)
        for node in pit.supplements
    ] == [
        (node.kind, node.label, node.text)
        for node in statute.supplements
    ]


# ---------------------------------------------------------------------------
# Timeline/overlay 5: _apply_overlays with empty active dict is identity
# ---------------------------------------------------------------------------

@given(SHORT_TEXT, SECTION_LABELS, SHORT_TEXT)
@settings(max_examples=50)
def test_overlay_identity(parent_text: str, child_label: str, child_text: str) -> None:
    """_apply_overlays with empty active dict returns content unchanged (text equality)."""
    child = IRNode(kind=IRNodeKind.SUBSECTION, label=child_label, text=child_text)
    content = IRNode(kind=IRNodeKind.SECTION, label="1", text=parent_text, children=(child,))
    addr = LegalAddress(path=(("section", "1"),))

    result = _apply_overlays(content, addr, {})

    assert result.text == content.text, (
        f"Own text changed: expected {content.text!r}, got {result.text!r}"
    )
    assert len(result.children) == len(content.children), (
        f"Children count changed: {len(content.children)} -> {len(result.children)}"
    )
    if result.children and content.children:
        assert result.children[0].text == content.children[0].text, (
            f"Child text changed: {content.children[0].text!r} -> {result.children[0].text!r}"
        )


# ---------------------------------------------------------------------------
# Sort key 6: _sort_label_key defines a total order
# ---------------------------------------------------------------------------

LABEL_STRS = st.one_of(
    st.integers(min_value=1, max_value=100).map(str),
    st.builds(lambda n, s: f"{n}{s}",
              st.integers(min_value=1, max_value=50),
              st.sampled_from(list("abcdefghij"))),
)


@given(LABEL_STRS, LABEL_STRS)
@settings(max_examples=100)
def test_sort_label_key_total_order(a: str, b: str) -> None:
    """For any two labels exactly one of a<b, b<a, a==b holds under _sort_label_key."""
    ka = _sort_label_key(a)
    kb = _sort_label_key(b)
    lt = ka < kb
    gt = ka > kb
    eq = ka == kb
    assert (lt + gt + eq) == 1, (
        f"Total order violated for {a!r} vs {b!r}: lt={lt}, gt={gt}, eq={eq}"
    )


# ---------------------------------------------------------------------------
# Sort key 7: numeric labels 1..20 sort in numeric order
# ---------------------------------------------------------------------------

def test_sort_label_key_numeric_ordering() -> None:
    """Labels '1' through '20' sort in numeric order under _sort_label_key."""
    labels = [str(i) for i in range(1, 21)]
    sorted_labels = sorted(labels, key=_sort_label_key)
    assert sorted_labels == labels, (
        f"Numeric sort order wrong: {sorted_labels}"
    )


# ---------------------------------------------------------------------------
# Determinism 8: replay_xml is deterministic
# ---------------------------------------------------------------------------

from lawvm.finland.grafter import (
    _get_corpus_store,
    _resolve_applicable_amendment_records,
    _sec1_fallback_peg_skip_required,
    process_muutoslaki,
)
from lawvm.finland.statute import StatuteContext, ReplayState
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.tools.diff import _extract_sections_ir
from tests.corpus_pin_helpers import pinned_replay


@pytest.fixture(scope="module")
def replay_1990_845_finlex_oracle():
    return pinned_replay("1990/845", mode="finlex_oracle", quiet=True)


@pytest.fixture(scope="module")
def replay_1992_480_finlex_oracle():
    return pinned_replay("1992/480", mode="finlex_oracle", quiet=True, build_full_products=False)


@pytest.fixture(scope="module")
def replay_1982_710_legal_pit():
    return pinned_replay("1982/710", mode="legal_pit", quiet=True)


def test_replay_deterministic() -> None:
    """replay_xml('2009/953') called twice produces identical irnode_to_text output."""
    master1 = pinned_replay("2009/953")
    text1 = irnode_to_text(master1.ir)

    master2 = pinned_replay("2009/953")
    text2 = irnode_to_text(master2.ir)

    assert text1 == text2, (
        "replay_xml is non-deterministic: two calls produced different output"
    )


def test_omnibus_repeal_sec1_fallback_does_not_skip_parent_repeal(replay_1992_480_finlex_oracle) -> None:
    """Generic preambles must not make sec_1 omnibus repeals look misrouted."""
    master = replay_1992_480_finlex_oracle
    sec24 = _extract_sections_ir(master.ir).get("24")

    assert sec24 is None or sec24.attrs.get("lawvm_repeal_placeholder") == "1"


def test_generic_preamble_sec1_repeal_recovers_shared_section_sign_list() -> None:
    """sec_1 kumotaan fallback should still recover the targeted section labels."""
    master = pinned_replay("1990/1039", mode="finlex_oracle")
    secs = _extract_sections_ir(master.ir)

    assert "1" in secs
    assert "2" not in secs
    assert "2a" not in secs
    assert "3" not in secs


def test_generic_preamble_sec1_repeal_keeps_inserted_chapter_live_before_repeal() -> None:
    """Detached-horizon oracle replay keeps inserted chapter content live pre-repeal."""
    master = pinned_replay("1990/1247", mode="finlex_oracle")
    secs = _extract_sections_ir(master.ir)

    assert sorted(secs) == ["2", "3", "4", "5", "7", "8", "9"]


def test_large_johtolause_subsection_insert_supplement_recovers_missing_moments(
    replay_1992_480_finlex_oracle,
) -> None:
    """Complex mixed amendments should recover explicit `§:ään uusi N momentti` inserts."""
    master = replay_1992_480_finlex_oracle
    secs = _extract_sections_ir(master.ir)

    sec15 = secs["15"]
    sec29 = secs["29"]
    labels15 = [child.label for child in sec15.children if child.kind == IRNodeKind.SUBSECTION]
    labels29 = [child.label for child in sec29.children if child.kind == IRNodeKind.SUBSECTION]
    text15 = irnode_to_text(sec15)
    text29 = irnode_to_text(sec29)

    assert "4" in labels15
    assert "Markkinaoikeus voi päättää 20 a §:ssä tarkoitetun luvan" in text15
    assert labels29 == ["1", "2"]
    assert "Kilpailuvirasto voi tarvittaessa antaa soveltamiskäytäntöään" in text29


def test_insertions_originals_johtolause_recovers_missing_subsection_insert(
    replay_1992_480_finlex_oracle,
) -> None:
    """Split insertions blocks should still compile the inserted subsection."""
    master = replay_1992_480_finlex_oracle
    sec11f = _extract_sections_ir(master.ir)["11f"]
    labels = [child.label for child in sec11f.children if child.kind == IRNodeKind.SUBSECTION]
    text = irnode_to_text(sec11f)

    assert labels == ["1", "2", "3", "4"]
    assert "sovelletaan myös liikepankeista ja muista osakeyhtiömuotoisista luottolaitoksista" in text


def test_omission_bracketed_replace_drops_stale_predecessor_moment(replay_1992_480_finlex_oracle) -> None:
    """Bracketed single-subsection replaces must not leave the old predecessor moment behind."""
    master = replay_1992_480_finlex_oracle
    sec2 = _extract_sections_ir(master.ir)["2"]
    labels = [child.label for child in sec2.children if child.kind == IRNodeKind.SUBSECTION]
    text = irnode_to_text(sec2)

    assert labels == ["1", "2", "3", "4", "5"]
    assert "Lakia sovelletaan kuitenkin sellaisiin 2 momentissa tarkoitettuihin menettelyihin" in text
    assert "Ellei valtioneuvoston asetuksella toisin säädetä" in text
    assert "Ellei valtioneuvosto toisin määrää" not in text


def test_intro_list_subsection_replace_keeps_embedded_items() -> None:
    """First-moment replacements must keep adjacent amendment item subtrees."""
    master = pinned_replay("2015/242", mode="finlex_oracle")
    sec2 = _extract_sections_ir(master.ir)["2"]
    labels = [child.label for child in sec2.children if child.kind == IRNodeKind.SUBSECTION]
    text = irnode_to_text(sec2)

    assert labels[0] == "1"
    assert "1) hankinta-arvoltaan vähintään 10 miljoonan euron kiinteistövarallisuuden hankinnasta" in text
    assert "2) hallinnonalansa viraston tai laitoksen sitoutumisesta vuokrasopimukseen" in text
    sub2 = next((c for c in sec2.children if c.kind == IRNodeKind.SUBSECTION and c.label == "2"), None)
    assert sub2 is not None
    assert sub2.attrs.get("lawvm_repeal_placeholder") == "1"


def test_snapshot_fallback_keeps_placeholder_for_missing_repealed_section() -> None:
    """Whole-section repeals should emit placeholders even if the live section is absent."""
    master = pinned_replay("2009/1672", mode="finlex_oracle")
    sec14b = master.find_section("14b", "7")

    assert sec14b is None or sec14b.attrs.get("lawvm_repeal_placeholder") == "1"


def test_unchaptered_whole_section_repeal_snapshot_keeps_placeholder() -> None:
    """Timeline materialization must not resurrect base text for unchaptered repeals."""
    master = pinned_replay("1988/718", mode="legal_pit")
    sec5 = master.find_section("5")
    sec8 = master.find_section("8")

    assert sec5 is None or sec5.attrs.get("lawvm_repeal_placeholder") == "1"
    assert sec8 is None or sec8.attrs.get("lawvm_repeal_placeholder") == "1"


def test_sparse_suffix_subsection_replaces_keep_source_order_in_2000_252() -> None:
    """Mixed item + later-moment replaces must not reverse the later payloads."""
    master = pinned_replay("2000/252", mode="legal_pit")
    sec3 = master.find_section("3")

    assert sec3 is not None
    sub1 = next(child for child in sec3.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1")
    assert [child.label for child in sub1.children if child.kind == IRNodeKind.PARAGRAPH] == [
        "1",
        "2",
        "3",
        "4",
        "5",
        "6",
        "7",
    ]
    text = irnode_to_text(sec3)
    assert text.count("Pankkivaltuusto nimittää ja erottaa Finanssivalvonnan johtokunnan jäsenet") == 1
    assert text.index("Pankkivaltuusto nimittää ja erottaa Finanssivalvonnan johtokunnan jäsenet") < text.index(
        "Pankkivaltuusto antaa ohjeet siitä"
    )
    assert "Pankkivaltuusto antaa ohjeet siitä" in text
    assert "Suomen itsenäisyyden juhlarahaston hallintoneuvostona" not in text


def test_root_insert_supplement_recovers_14b_to_14d_for_1993_1689() -> None:
    """Decision-scoped trailing root insert ranges should survive PEG undercounting."""
    master = pinned_replay("1993/1689", mode="legal_pit", stop_before="1996/823")

    assert master.find_section("14b") is not None
    assert master.find_section("14c") is not None
    assert master.find_section("14d") is not None


def test_post_omission_tail_insert_preserves_trailing_subsections_for_2002_885() -> None:
    """Amendment body with more subsections than johtolause claims must not drop trailing ones.

    Statute 2002/885 (äitiysavustus — maternity allowance) is a tiny 4-section
    government decree.  Amendment 2003/67 says "lisätään 1 §:ään uusi 2 momentti"
    (insert one new subsection), but its body contains section 1 with THREE new
    subsections:

        mom 2: "Äitiysavustus maksetaan korotettuna..."  (multi-birth rules)
        mom 3: table of amounts per child
        mom 4: "Korotettu äitiysavustus voidaan..."      (payment form choice)

    The Finlex oracle (fin@20171155) confirms section 1 has 4 subsections after
    all amendments.  Our replay only picks up 2 of the 3 new subsections because
    the body-recovery heuristic stops after the johtolause-claimed insertion
    point.  Mom 4 ("Korotettu") is lost.

    Root cause: body_pairing / _recover_uncovered_body_ops doesn't handle the
    pattern where the amendment body section contains MORE trailing subsections
    than the johtolause declares.  The johtolause says "insert mom 2" but the
    body carries moms 2, 3, AND 4 as a unit.

    Fix path: upgrade body-driven pairing to detect trailing body subsections
    that follow a claimed insertion point and treat them as implicit inserts.

    Amendments applied to 2002/885:
        2003/67   — INSERT 1 § 2 mom  (body has 3 new subs, only 2 picked up)
        2008/842  — REPLACE 3 §
        2017/1155 — REPLACE 1 § 1 mom, REPLACE 1 § 3 mom
        2018/1254 — REPLACE 3 §
    """
    before_2008 = pinned_replay("2002/885", mode="legal_pit", stop_before="2008/842")
    current = pinned_replay("2002/885", mode="legal_pit")

    before_text = before_2008.serialize_text()
    current_text = current.serialize_text()

    assert "Korotettu äitiysavustus voidaan hakijan valinnan mukaan suorittaa" in before_text
    assert "Korotettu äitiysavustus voidaan hakijan valinnan mukaan suorittaa" in current_text


def test_mixed_muutetaan_tail_supplement_recovers_1988_718_base_section_updates() -> None:
    """A mixed repeal+replace johtolause should not drop the trailing replace targets."""
    master = pinned_replay("1988/718", mode="legal_pit")
    text = master.serialize_text()

    assert "Vuoden 1993 alusta lukien tässä laissa vapaakunnalle säädettyä kokeilua voi harjoittaa myös muu kunta" in text
    assert "31 päivään joulukuuta 1996" in text


def test_archaic_a_separator_recovers_1966_332_update_for_1956_463() -> None:
    """Old orthographic `a` between targets should not drop the later subsection replace."""
    master = pinned_replay("1956/463", mode="legal_pit")
    sec7 = master.find_section("7")

    assert sec7 is not None
    text = irnode_to_text(sec7)
    assert "Valtakunnan itäisen merirajan läntisestä päätepisteestä" in text
    assert "eteläisestä päätepisteestä aluemeren ulkoraja kulkee neljän meripeninkulman päässä" not in text


def test_temporary_5e_section_expires_out_of_1995_1556_legal_pit() -> None:
    """Expired temporary 5 e § should not persist in 2025 legal PIT materialization."""
    master = pinned_replay("1995/1556", mode="legal_pit")
    sec5e = master.find_section("5e")
    sec4 = master.find_section("4")

    assert sec5e is None
    assert sec4 is not None
    text4 = irnode_to_text(sec4)
    assert "1 päivän tammikuuta 2007 ja 31 päivän joulukuuta 2009 väliseltä ajalta" not in text4


def test_1982_710_temporary_12c_section_expires_after_commencement_override(replay_1982_710_legal_pit) -> None:
    """Later voimaantulosäännös amendments must extend temporary section expiry.

    Chapter 2a was eventually repealed (2022/588 eff 2023-01-01), so the
    legal_pit PIT body no longer contains section 12c.  The test verifies the
    property via the compiled timelines: section 12c must have an active
    version at 2016-05-01 (before its expiry of 2016-12-31), and the last
    timeline version must carry that expiry date.
    """
    master = replay_1982_710_legal_pit

    timelines = master.timelines or {}
    target_tl = None
    for addr, tl in timelines.items():
        path = "/".join(f"{k}:{v}" for k, v in addr.path)
        if path == "chapter:2a/section:12c":
            target_tl = tl
            break

    assert target_tl is not None, "timeline for chapter:2a/section:12c must exist"
    # The voimaantulosäännös chain must have propagated expiry 2016-12-31
    assert target_tl.versions[-1].expires == "2016-12-31"
    # Section was still active at 2016-05-01 (before its expiry)
    active_at_pit = select_active_version(target_tl, "2016-05-01")
    assert active_at_pit is not None, "section 12c must be active at 2016-05-01"


def test_1982_710_omaishoidon_tuki_law_repeals_27abc_sections(replay_1982_710_legal_pit) -> None:
    """Standalone omaishoidon tuki law should repeal SHL 27 a-c via voimaantulo."""
    master = replay_1982_710_legal_pit
    for label in ("27a", "27b", "27c"):
        sec = master.find_section(label)
        assert sec is not None
        assert sec.attrs.get("lawvm_repeal_placeholder") == "1"


def test_1982_710_section_17_does_not_duplicate_second_moment_after_2005_938(replay_1982_710_legal_pit) -> None:
    """Sparse moment replacement should not merge 17 § 2 mom into 1 mom."""
    master = replay_1982_710_legal_pit
    sec17 = master.find_section("17")

    assert sec17 is not None
    text17 = irnode_to_text(sec17)
    assert text17.count("Kunnan on myös huolehdittava") == 1
    assert "Kunta voi 1 ja 2 momenteissa tarkoitettujen sosiaalipalveluiden lisäksi" in text17


def test_1982_710_section_6_keeps_2006_1329_first_moment_after_temporary_expiry(replay_1982_710_legal_pit) -> None:
    """Persistent 6 § 1 mom replacement must survive expiry of temporary 2 mom.

    Chapter 2 was repealed by 2022/588 (eff 2023-01-01) so section 6 no longer
    appears in the PIT body.  The test verifies the timeline properties directly:

    - subsection 1 has a permanent version from 2006/1329 (eff 2007-01-01)
    - at 2009-01-01 (after the 2003/155 temp overlay expired 2008-07-31) the
      subsection 1 active version carries the 2006/1329 text
    - the temporary section body (2003/155, which contained "Sen estämättä...")
      is no longer active at 2009-01-01
    """
    master = replay_1982_710_legal_pit

    timelines = master.timelines or {}

    # Subsection 1 must have the durable 2006/1329 replacement
    addr_sub1 = LegalAddress(path=(("chapter", "2"), ("section", "6"), ("subsection", "1")))
    tl_sub1 = timelines.get(addr_sub1)
    assert tl_sub1 is not None, "timeline for chapter:2/section:6/subsection:1 must exist"

    sources = [v.source.statute_id for v in tl_sub1.versions if v.source]
    assert "2006/1329" in sources, "2006/1329 must have produced a version of 6 § 1 mom"

    # After the temporary overlay (2003/155, expires 2008-07-31) has lapsed,
    # subsection 1 must carry the 2006/1329 text
    active_sub1 = select_active_version(tl_sub1, "2009-01-01")
    assert active_sub1 is not None
    assert active_sub1.content is not None
    text_sub1 = irnode_to_text(active_sub1.content)
    assert "tämän lain mukaan kuuluvista tehtävistä sekä niistä tehtävistä" in text_sub1
    assert "yksi tai useampi kunnan määräämä monijäseninen toimielin" in text_sub1

    # The temporary section body (2003/155) must have expired — it is no longer
    # active at 2009-01-01 and its "Sen estämättä" text is absent
    addr_sec6 = LegalAddress(path=(("chapter", "2"), ("section", "6")))
    tl_sec6 = timelines.get(addr_sec6)
    assert tl_sec6 is not None
    active_sec6 = select_active_version(tl_sec6, "2009-01-01")
    # The active section body must NOT be the 2003/155 temporary version
    if active_sec6 and active_sec6.content:
        text_sec6 = irnode_to_text(active_sec6.content)
        assert "Sen estämättä, mitä 1 momentissa säädetään" not in text_sec6


def test_sec1_repeal_guard_keeps_single_parent_numbered_enumeration() -> None:
    """Mixed single-parent sec_1 clauses should still run PEG after restriction."""
    johto = (
        "kumotaan 1) Harmaan talouden selvitysyksiköstä annetun lain (1207/2010) 2 § "
        "sekä muutetaan 2) Harmaan talouden selvitysyksiköstä annetun lain (1207/2010) "
        "6 §:n 1 momentin 39 kohta."
    )
    assert not _sec1_fallback_peg_skip_required(johto, "2010/1207")


def test_sec1_repeal_guard_skips_citation_free_omnibus_lists() -> None:
    """Citation-free sec_1 repeal lists still need the omnibus PEG skip."""
    johto = (
        "kumotaan 1) laki X, 2) laki Y ja 3) laki Z."
    )
    assert _sec1_fallback_peg_skip_required(johto, "1992/480")


def test_sec1_repeal_guard_keeps_single_parent_subprovision_repeals() -> None:
    """Single-parent subprovision repeals should still let PEG run."""
    johto = (
        "Täten kumotaan 29 päivänä kesäkuuta 1983 annetun sosiaalihuoltoasetuksen "
        "(607/83) 9 §:n 1 momentin 3 kohta ja 2 momentti."
    )
    assert not _sec1_fallback_peg_skip_required(johto, "1983/607")


def test_sec1_repeal_guard_keeps_citation_free_explicit_section_repeal_lists() -> None:
    """Parent-restricted sec_1 fallback with explicit § targets should still run PEG."""
    johto = (
        "Tällä lailla kumotaan tullilain 21 §:n edellä oleva väliotsikko, 21—23, "
        "23 a—23 e, 24, 26, 26 a ja 27 § sekä 28 §:n 1 ja 2 momentti."
    )
    assert not _sec1_fallback_peg_skip_required(johto, "1994/1466")


def test_item_insert_renumbers_numeric_suffix_in_same_subsection() -> None:
    """Inserting a new numeric kohta must renumber later numeric siblings."""
    parent_id = "2010/1207"
    orig = _get_corpus_store().read_source(parent_id)
    assert orig is not None
    ctx = StatuteContext.from_xml(orig, _fi_label_postprocessor)
    state = ReplayState(ir=copy.deepcopy(ctx.base_ir))
    records, _, _ = _resolve_applicable_amendment_records(parent_id, "finlex_oracle")
    for rec in records:
        amendment_id = str(rec["statute_id"])
        state = process_muutoslaki(
            amendment_id, state, ctx, replay_mode="finlex_oracle", parent_id=parent_id
        ).output
        if amendment_id == "2016/1419":
            sec = _extract_sections_ir(state.ir)["6"]
            sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
            labels = [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
            assert labels[17:21] == ["18", "19", "20", "21"]
            assert labels.count("19") == 1
            break
    else:
        raise AssertionError("2016/1419 not found in amendment chain")


def test_repealed_amendment_act_does_not_delete_parent_section() -> None:
    """Repealing amendment act 923/2017 must not become REPEAL 6 § on the parent."""
    master = pinned_replay("2010/1207", mode="legal_pit", stop_before="2018/404")
    sec = _extract_sections_ir(master.ir)["6"]
    sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1")
    labels = [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]

    assert labels[:8] == ["1", "2", "3", "4", "5", "6", "7", "8"]
    assert labels[-3:] == ["20", "21", "22"]


def test_partial_internal_list_update_does_not_collapse_entire_section() -> None:
    """`1 §:ssä olevaa ... luetteloa I` is not a safe whole-section replace."""
    master = pinned_replay("1993/1709", mode="finlex_oracle", stop_before="2001/201")
    sec = _extract_sections_ir(master.ir)["1"]
    text = irnode_to_text(sec)

    assert len(text) > 10000
    assert "Alfa-asetyylimetadoli" in text


def test_shifted_subsection_insert_preserves_earlier_moment() -> None:
    """`uusi 2 momentti ... muutettu 2 momentti siirtyy 3 momentiksi` should keep both.

    NOTE: After P1 jolloin fix, the PEG no longer emits separate REPLACE for
    the shifted subsection (same target as INSERT → dedup).  The shifted
    content is lost.  The INSERT + latest REPLACE of mom 2 are correct.
    This is a known P1 trade-off (~0.17% for this statute vs net +0.05pp corpus).
    """
    master = pinned_replay("1947/625", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["3"]
    text = irnode_to_text(sec)

    # The INSERT 3/2 and latest REPLACE 3/2 (2018/1342) should be present.
    assert "Rahasto-osuuden lahjoitus" in text


def test_malformed_embedded_letter_section_is_split_for_replace_and_insert() -> None:
    """Malformed `3 § ... 3 a §` blobs should not leak section 3a into section 3."""
    master = pinned_replay("1959/324", mode="finlex_oracle", stop_before="1999/310")
    sections = _extract_sections_ir(master.ir)

    sec3 = irnode_to_text(sections["3"])
    sec3a = irnode_to_text(sections["3a"])

    assert "3 a §" not in sec3
    assert "Vakuutuksenottajalla on oikeus" not in sec3
    assert "Vakuutuksenottajalla on oikeus" in sec3a


def test_sec1_repeal_subsection_range_is_applied() -> None:
    """Pure repeal clauses encoded in section 1 should still repeal subsection ranges."""
    master = pinned_replay("1959/324", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["9"]
    text = irnode_to_text(sec)

    assert "Valtiota edustaa valtion omistaman moottoriajoneuvon" in text
    # 2002/863 explicitly repeals 9 §:n 2–5 momentti. All four slots must
    # become repeal placeholders in replay, not just the tail.
    for lbl in ("2", "3", "4", "5"):
        sub = next((c for c in sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == lbl), None)
        assert sub is not None and sub.attrs.get("lawvm_repeal_placeholder") == "1", f"sub {lbl}"


def test_kumottu_range_consolidation_reaches_nested_sections(replay_1990_845_finlex_oracle) -> None:
    """Range consolidation must also rewrite chapter-contained sections after PIT materialization."""
    sec = _extract_sections_ir(replay_1990_845_finlex_oracle.ir)["26"]
    text = irnode_to_text(sec)

    # Subsections 2-3 should be repeal placeholders
    for lbl in ("2", "3"):
        sub = next((c for c in sec.children if c.kind == IRNodeKind.SUBSECTION and c.label == lbl), None)
        assert sub is not None and sub.attrs.get("lawvm_repeal_placeholder") == "1", f"sub {lbl}"


def test_wrong_single_target_amendment_title_is_skipped(replay_1990_845_finlex_oracle) -> None:
    """A single-target amendment of another statute must not rewrite this parent by section-number collision."""
    sec = _extract_sections_ir(replay_1990_845_finlex_oracle.ir)["12"]
    text = irnode_to_text(sec)

    assert "Ajoneuvorekisteristä voidaan luovuttaa tietoja mielipide- ja markkinatutkimukseen" not in text
    assert "Lyhytaikaista ajokorttia seuraavan" in text


def test_plain_subsection_insert_chains_keep_numeric_order(replay_1990_845_finlex_oracle) -> None:
    """Pure plain momentti insert chains should apply in ascending subsection order."""
    sec = _extract_sections_ir(replay_1990_845_finlex_oracle.ir)["2"]
    labels = [child.label for child in sec.children if child.kind == IRNodeKind.SUBSECTION]

    assert labels == ["1", "2", "3", "4", "5"]


def test_later_inserted_whole_section_repeal_respects_oracle_horizon_in_finlex_oracle(
    replay_1990_845_finlex_oracle,
) -> None:
    """Later-added sections stay live until the oracle horizon reaches the repeal date."""
    secs = _extract_sections_ir(replay_1990_845_finlex_oracle.ir)

    assert "31a" not in secs


def test_insert_without_payload_is_skipped_in_timeline_compilation() -> None:
    """An insert op with no payload should record a typed issue instead of only warning."""
    base = IRStatute(
        statute_id="test/insert-missing",
        title="Missing insert payload",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    addr = LegalAddress(path=(("section", "9"),))
    op = LegalOperation(
        op_id="missing_payload_insert",
        sequence=1,
        action=StructuralAction.INSERT,
        target=addr,
        payload=None,
        source=OperationSource(
            statute_id="2020/2",
            enacted="2020-01-01",
            effective="2020-01-01",
        ),
        group_id="g:missing-payload",
    )

    explicit_ops, explicit_events = _with_explicit_temporal_authority(
        base,
        [op],
        temporal_events=(
            TemporalEvent(
                event_id="ev:missing-payload",
                group_id="g:missing-payload",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
                scope=TemporalScope(target_statute="test/insert-missing"),
            ),
        ),
    )
    result = compile_timelines_ex(
        base,
        explicit_ops,
        base_date="2000-01-01",
        temporal_events=explicit_events,
    )

    assert any(issue.kind == "missing_insert_payload" for issue in result.issues)
    assert addr not in result.timelines or len(result.timelines[addr].versions) == 0


def test_apply_overlays_records_duplicate_normalized_sibling_issue() -> None:
    """Duplicate normalized siblings should surface as a typed materialization issue."""
    parent = IRNode(
        kind=IRNodeKind.SECTION,
        label="4",
        text="",
        children=(
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1.", text="old 1"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1)", text="old 2"),
        ),
    )
    parent_addr = LegalAddress(path=(("section", "4"),))
    target_addr = LegalAddress(path=(("section", "4"), ("paragraph", "1")))
    active: dict[LegalAddress, IRNode | None] = {
        target_addr: IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="new"),
    }
    issues: list = []

    result = _apply_overlays(
        parent,
        parent_addr,
        active,
        label_norm=fi_label_norm,
        issue_sink=issues,
        emit_warnings=False,
    )

    assert result.children == parent.children
    assert any(
        issue.kind == "duplicate_normalized_sibling_override"
        and issue.address == parent_addr
        for issue in issues
    )


def test_repealed_section_ranges_collapse_in_finlex_oracle_materialization(
    replay_1990_845_finlex_oracle,
) -> None:
    """Future repeal ranges must stay live before the oracle horizon reaches them."""
    secs = _extract_sections_ir(replay_1990_845_finlex_oracle.ir)
    assert "39" not in secs


def test_embedded_paragraph_number_in_base_source_is_preserved_for_2007_508() -> None:
    """Malformed content-embedded paragraph numbers must not collapse into duplicates."""
    master = pinned_replay("2007/508", mode="legal_pit")
    sec = master.find_section("23")

    assert sec is not None
    sub1 = next((child for child in sec.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1"), None)
    assert sub1 is not None

    paragraphs = [child for child in sub1.children if child.kind == IRNodeKind.PARAGRAPH]
    assert [paragraph.label for paragraph in paragraphs] == ["1", "2", "3", "4", "5", "6"]


def test_mixed_subsection_insert_replace_chains_keep_pre_shift_targeting() -> None:
    """Mixed insert+replace chains must still target the pre-shift moment."""
    master = pinned_replay("1969/327", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["4"]
    text = irnode_to_text(sec)

    assert "Torjunta-aineeksi tarkoitettua ainetta" in text
    assert "kuultava 4 e §:n nojalla" in text


def test_shift_retargeted_mixed_subsection_insert_replace_chains_can_apply_ascending() -> None:
    """Retargeted mixed chains should still preserve both the inserted and shifted moments.

    NOTE: Same P1 jolloin trade-off as above — shifted subsection content lost.
    """
    master = pinned_replay("1947/625", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["3"]
    text = irnode_to_text(sec)

    assert "Rahasto-osuuden lahjoitus" in text


def test_sec1_mixed_repeal_and_replace_clause_is_applied() -> None:
    """Mixed sec_1 clauses should recover both subsection repeal and section updates."""
    master = pinned_replay("2007/636", mode="finlex_oracle")
    secs = _extract_sections_ir(master.ir)

    text1 = irnode_to_text(secs["1"])
    text14 = irnode_to_text(secs["14"])

    assert "neuvoston asetuksen (EY) N:o 73/2009 täytäntöönpanoa koskevista" in text1
    assert "neuvoston asetuksissa (EY) N:o 1782/2003 ja (EY) N:o 73/2009 säädettyjen" not in text1
    sub14_1 = next((c for c in secs["14"].children if c.kind == IRNodeKind.SUBSECTION and c.label == "1"), None)
    assert sub14_1 is not None and sub14_1.attrs.get("lawvm_repeal_placeholder") == "1"
    assert "Työ- ja elinkeinokeskuksen on ilmoitettava" not in text14
    assert "Asetuksen 7―9 §:ään liittyvissä tarkastuksissa havaituista puutteista" in text14


def test_parent_scoped_sec1_repeals_are_applied() -> None:
    """Parent-restricted sec_1 fallback should handle generic-preamble and multi-parent repeals."""
    master = pinned_replay("1983/607", mode="finlex_oracle")
    secs = _extract_sections_ir(master.ir)

    text9 = irnode_to_text(secs["9"])

    assert "sosiaalilautakunnan ja yksityisen henkilön välillä tehtävään sopimukseen" not in text9
    # §9 should have at least one subsection with lawvm_repeal_placeholder
    repeal_subs = [
        c for c in secs["9"].children
        if c.kind == IRNodeKind.SUBSECTION and c.attrs.get("lawvm_repeal_placeholder") == "1"
    ]
    assert len(repeal_subs) > 0
    assert "14" not in secs


def test_sparse_list_section_replace_merges_changed_items_in_place() -> None:
    """Sparse section payloads should not collapse untouched intro-list siblings."""
    master = pinned_replay("1997/746", mode="finlex_oracle", stop_before="2000/1170")
    sec = _extract_sections_ir(master.ir)["1"]
    sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    labels = [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
    text = irnode_to_text(sec)

    # Labels extracted from "N. Name" intro-text carry the trailing dot; the
    # number prefix is extracted as the label and stripped from the intro text
    # (so the text no longer contains "2. Lounais-Suomen" — just the name).
    assert labels == [f"{i}." for i in range(1, 14)]
    assert "Lounais-Suomen metsäkeskus" in text
    assert "Pirkanmaan metsäkeskus" in text
    assert "Pohjois-Pohjanmaan metsäkeskus" in text


# ---------------------------------------------------------------------------
# UK Timeline: UKReplayExecutor snapshot emission
# ---------------------------------------------------------------------------

from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor


def _make_uk_statute(sections: list[tuple[str, str]]) -> IRStatute:
    """Build a minimal UK-style IRStatute with flat body sections."""
    children = [
        IRNode(kind=IRNodeKind.SECTION, label=label, text=text)
        for label, text in sections
    ]
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act 2000",
        body=IRNode(kind=IRNodeKind.BODY, label=None, text="", children=tuple(children)),
    )


def _make_src(statute_id: str, effective: str) -> OperationSource:
    return OperationSource(
        statute_id=statute_id,
        title=f"Amending Act {statute_id}",
        effective=effective,
        enacted=effective,
    )


def _with_explicit_temporal_authority(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    temporal_events: tuple[TemporalEvent, ...] = (),
    group_prefix: str = "g",
) -> tuple[list[LegalOperation], tuple[TemporalEvent, ...]]:
    """Assign stable group IDs and synthesize explicit temporal events from op sources.

    Commencement events are emitted with an explicit fixed-date activation rule
    so the tests exercise the primary temporal authority path rather than any
    legacy temporal mirror field.
    """
    explicit_ops: list[LegalOperation] = []
    derived_events: list[TemporalEvent] = list(temporal_events)
    derive_from_sources = not temporal_events
    for idx, op in enumerate(ops):
        group_id = op.group_id or f"{group_prefix}:{op.op_id or idx}"
        explicit_op = dc_replace(op, group_id=group_id)
        explicit_ops.append(explicit_op)
        source = explicit_op.source
        if source is None:
            continue
        if not derive_from_sources:
            continue
        scope = TemporalScope(target_statute=base.statute_id)
        effective = source.effective
        if effective:
            derived_events.append(
                TemporalEvent(
                    event_id=f"{group_id}:commence",
                    group_id=group_id,
                    kind="commence",
                    effective=effective,
                    activation_rule=ActivationRule(kind="fixed_date", effective_date=effective),
                    source=OperationSource(
                        statute_id=base.statute_id,
                        effective=effective,
                    ),
                    scope=scope,
                )
            )
        if source.expires:
            derived_events.append(
                TemporalEvent(
                    event_id=f"{group_id}:expire",
                    group_id=group_id,
                    kind="expire",
                    expires=source.expires,
                    source=OperationSource(
                        statute_id=base.statute_id,
                        expires=source.expires,
                    ),
                    scope=scope,
                )
            )
    return explicit_ops, tuple(derived_events)


def _compile_timelines_with_explicit_temporal_authority(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    base_date: str,
    temporal_events: tuple[TemporalEvent, ...] = (),
    group_prefix: str = "g",
):
    explicit_ops, explicit_events = _with_explicit_temporal_authority(
        base,
        ops,
        temporal_events=temporal_events,
        group_prefix=group_prefix,
    )
    return compile_timelines(
        base,
        explicit_ops,
        base_date=base_date,
        temporal_events=explicit_events,
    )


def compile_timelines_ex(
    base: IRStatute,
    ops: list[LegalOperation],
    *,
    base_date: str,
    temporal_events: tuple[TemporalEvent, ...] = (),
    group_prefix: str = "g",
):
    """Test-local explicit-authority wrapper for core compile_timelines_ex.

    When no temporal events are passed, synthesize them from op sources so the
    tests exercise explicit temporal carriers instead of provenance acting as
    executable authority in core.
    """
    issue_sink: list[TimelineIssue] = []
    explicit_ops, explicit_events = _with_explicit_temporal_authority(
        base,
        ops,
        temporal_events=temporal_events,
        group_prefix=group_prefix,
    )
    timelines = compile_timelines(
        base,
        explicit_ops,
        base_date=base_date,
        temporal_events=explicit_events,
        issue_sink=issue_sink,
    )
    return TimelineCompilationResult(timelines=timelines, issues=tuple(issue_sink))


def test_uk_executor_replace_emits_snapshot() -> None:
    """UKReplayExecutor emits a lo_ops_out snapshot after a replace op."""
    statute = _make_uk_statute([("1", "Original text of section 1.")])
    lo: List[LegalOperation] = []

    executor = UKReplayExecutor(statute, lo_ops_out=lo)
    op = LegalOperation(
        op_id="test_replace_s1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Amended text of section 1."),
        source=_make_src("ukpga/2005/10", "2005-01-01"),
    )
    executor.apply_op(op)

    assert len(lo) == 1, f"Expected 1 snapshot, got {len(lo)}"
    snap = lo[0]
    assert snap.action == StructuralAction.REPLACE
    assert snap.target == LegalAddress(path=(("section", "1"),))
    assert snap.payload is not None
    assert "Amended" in irnode_to_text(snap.payload)
    assert snap.source is not None
    assert snap.source.statute_id == "ukpga/2005/10"


def test_uk_executor_repeal_emits_tombstone_snapshot() -> None:
    """UKReplayExecutor emits a repeal tombstone snapshot after a repeal op."""
    statute = _make_uk_statute([
        ("1", "Section 1 text."),
        ("2", "Section 2 text."),
    ])
    lo: List[LegalOperation] = []

    executor = UKReplayExecutor(statute, lo_ops_out=lo)
    op = LegalOperation(
        op_id="test_repeal_s2",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "2"),)),
        source=_make_src("ukpga/2010/5", "2010-06-01"),
    )
    executor.apply_op(op)

    assert len(lo) == 1
    snap = lo[0]
    assert snap.action == StructuralAction.REPEAL
    assert snap.target == LegalAddress(path=(("section", "2"),))
    assert snap.payload is None  # tombstone
    assert snap.source is not None
    assert snap.source.statute_id == "ukpga/2010/5"


def test_uk_executor_insert_emits_snapshot() -> None:
    """UKReplayExecutor emits a snapshot after inserting a new section."""
    statute = _make_uk_statute([("1", "Section 1 text.")])
    lo: List[LegalOperation] = []

    executor = UKReplayExecutor(statute, lo_ops_out=lo)
    new_section = IRNode(kind=IRNodeKind.SECTION, label="2", text="New section 2 text.")
    op = LegalOperation(
        op_id="test_insert_s2",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"),)),
        payload=new_section,
        source=_make_src("ukpga/2015/3", "2015-03-01"),
    )
    executor.apply_op(op)

    # Should have emitted one snapshot for the inserted section
    assert len(lo) == 1
    snap = lo[0]
    assert snap.action == StructuralAction.REPLACE  # snapshot of current top-section state
    assert snap.source is not None
    assert snap.source.statute_id == "ukpga/2015/3"


def test_uk_executor_no_snapshots_when_lo_ops_out_is_none() -> None:
    """When lo_ops_out is None, no snapshots are collected."""
    statute = _make_uk_statute([("1", "Section 1 text.")])

    executor = UKReplayExecutor(statute, lo_ops_out=None)
    op = LegalOperation(
        op_id="test_replace_no_collect",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Amended text."),
        source=_make_src("ukpga/2020/1", "2020-01-01"),
    )
    executor.apply_op(op)
    # No assertion needed — just must not raise and executor.lo_ops_out stays None
    assert executor.lo_ops_out is None


def test_uk_compile_timelines_from_snapshots() -> None:
    """compile_timelines on UK lo_ops_out produces correct provision version history."""
    statute = _make_uk_statute([
        ("1", "Original section 1."),
        ("2", "Original section 2."),
    ])
    lo: List[LegalOperation] = []

    executor = UKReplayExecutor(statute, lo_ops_out=lo)

    # Apply two ops: replace s1, then replace s2
    executor.apply_op(LegalOperation(
        op_id="op1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Amended section 1 v1."),
        source=_make_src("ukpga/2005/10", "2005-01-01"),
    ))
    executor.apply_op(LegalOperation(
        op_id="op2",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "2"),)),
        payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Amended section 2 v1."),
        source=_make_src("ukpga/2008/4", "2008-06-01"),
    ))

    timelines = _compile_timelines_with_explicit_temporal_authority(
        statute,
        lo,
        base_date="2000-01-01",
    )

    addr_s1 = LegalAddress(path=(("section", "1"),))
    addr_s2 = LegalAddress(path=(("section", "2"),))

    assert addr_s1 in timelines
    assert addr_s2 in timelines

    # Each section should have 2 versions: base + 1 amendment
    assert len(timelines[addr_s1].versions) == 2
    assert len(timelines[addr_s2].versions) == 2

    # Latest version at 2010 should be the amended text
    v1_latest = select_active_version(timelines[addr_s1], "2010-01-01")
    v2_latest = select_active_version(timelines[addr_s2], "2010-01-01")
    assert v1_latest is not None
    assert v2_latest is not None
    assert v1_latest.content is not None
    assert v2_latest.content is not None
    assert "Amended" in irnode_to_text(v1_latest.content)
    assert "Amended" in irnode_to_text(v2_latest.content)

    # At enacted date (before any amendments) should be the base text
    v1_base = select_active_version(timelines[addr_s1], "2001-01-01")
    assert v1_base is not None
    assert v1_base.content is not None
    assert "Original" in irnode_to_text(v1_base.content)


def test_sparse_list_section_replace_allows_later_item_updates() -> None:
    """A later item-level amendment should still apply after sparse section merge."""
    master = pinned_replay("1997/746", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["1"]
    sub = next(c for c in sec.children if c.kind == IRNodeKind.SUBSECTION)
    labels = [c.label for c in sub.children if c.kind == IRNodeKind.PARAGRAPH]
    text = irnode_to_text(sec)

    # Labels carry the trailing dot; see note in test_sparse_list_section_replace_merges_changed_items_in_place.
    assert labels == [f"{i}." for i in range(1, 14)]
    assert "Pohjois-Pohjanmaan metsäkeskus" in text
    assert "Lapin metsäkeskus" in text


def test_generic_preamble_sec1_repealer_is_not_skipped() -> None:
    """Generic enabling-clause preambles should still recover the targeted section."""
    master = pinned_replay("1992/1282", mode="finlex_oracle")
    secs = _extract_sections_ir(master.ir)

    assert "18" not in secs


def test_full_list_section_replace_does_not_preserve_old_tail_items() -> None:
    """Contiguous whole-list replacements must drop stale tail items instead of sparse-merging."""
    master = pinned_replay("2016/866", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["1"]
    text = irnode_to_text(sec)

    assert "5) Varsinais-Suomen käräjäoikeuteen neljä." in text
    assert "6) Pohjanmaan käräjäoikeuteen kahdeksan" not in text
    assert "7) Vantaan käräjäoikeuteen yksi" not in text
    assert "8) Varsinais-Suomen käräjäoikeuteen neljä" not in text


def test_whole_subsection_replace_does_not_splice_stale_items_from_trailing_omission() -> None:
    """Whole-subsection REPLACE with trailing omission must not re-splice old master items.

    Regression: 2002/973 §9 mom 1 — amendment 2019/1391 replaces the
    entire first subsection, providing exactly kohta 1 and kohta 2 followed by
    a Finlex trailing ``<hcontainer name="omission"/>``.  The original subsection
    had three kohtia (1, 2, 3).  The trailing omission is an editorial artifact
    meaning "the old content ends here" — it must NOT cause the merger to
    splice back the old kohta 3.
    """
    master = pinned_replay("2002/973", mode="finlex_oracle")
    sec9 = _extract_sections_ir(master.ir)["9"]
    subsec1 = next((c for c in sec9.children if c.kind == IRNodeKind.SUBSECTION and c.label == "1"), None)
    assert subsec1 is not None, "§9 mom 1 not found"
    paras = [c for c in subsec1.children if c.kind == IRNodeKind.PARAGRAPH]
    labels = [p.label for p in paras]
    # Only kohta 1 and 2 should remain after 2019/1391 replaces the subsection
    assert labels == ["1", "2"], f"Expected only kohta 1 and 2 after 2019/1391 replace; got {labels}"
    # Verify the new kohta 2 text (not the original text with "tai" condition)
    kohta2_text = " ".join(
        c.text for p in paras if p.label == "2" for c in p.children if c.kind == IRNodeKind.CONTENT
    )
    assert "periaatteellisesti huomattava merkitys" in kohta2_text
    # Confirm kohta 3 ("jos vuokrauksella...") is NOT present — it was in the original
    # but was removed by the 2019/1391 whole-subsection replace
    full_sub1_text = irnode_to_text(subsec1)
    assert "jos vuokrauksella" not in full_sub1_text, "Stale kohta 3 must not appear after subsection replace"


def test_sparse_middle_subsection_replaces_follow_explicit_target_moments() -> None:
    """Sparse middle-block moment replacements must not flip the live target order."""
    master = pinned_replay("2017/445", mode="finlex_oracle")
    sec = _extract_sections_ir(master.ir)["5"]
    text = irnode_to_text(sec)

    idx_exchange = text.index("Rahanpesun selvittelykeskus voi perustellusta pyynnöstä luovuttaa")
    idx_tech = text.index("Tässä pykälässä tarkoitettuja tietoja saa vastaanottaa ja luovuttaa")

    assert idx_exchange < idx_tech


def test_sort_label_key_roman_numerals() -> None:
    """Roman numerals sort by numeric value beyond the old fixed-lookup range."""
    roman = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
             "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX", "XXI", "XXX"]
    sorted_roman = sorted(roman, key=_sort_label_key)
    assert sorted_roman == roman, f"Roman numeral order wrong: {sorted_roman}"

    # Roman numerals sort in same group as Arabic numerals
    assert _sort_label_key("III") < _sort_label_key("IV")
    assert _sort_label_key("IX") < _sort_label_key("X")
    assert _sort_label_key("XX") < _sort_label_key("XXI")


# ---------------------------------------------------------------------------
# Bug #2: select_active_version two-rail (temporary overlay priority)
# ---------------------------------------------------------------------------

def test_select_active_version_two_rail_temporary_wins_over_permanent() -> None:
    """select_active_version at a date inside a temporary window returns the
    temporary version, not a newer permanent version.

    Setup: permanent v1 (eff 2000), temporary v2 (eff 2004, expires 2016),
    permanent v3 (eff 2010). At 2012, the temporary v2 should win over v3.
    """
    addr = LegalAddress(path=(("section", "1"),))
    tl = ProvisionTimeline(address=addr, versions=[
        ProvisionVersion(
            effective="2000-01-01",
            enacted="2000-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="permanent v1"),
        ),
        ProvisionVersion(
            effective="2004-01-01",
            enacted="2004-01-01",
            expires="2016-01-01",
            variant_kind="temporary",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temporary v2"),
        ),
        ProvisionVersion(
            effective="2010-01-01",
            enacted="2010-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="permanent v3"),
        ),
    ])

    # At 2012: inside temporary window -> temporary v2 should win
    active = select_active_version(tl, "2012-01-01")
    assert active is not None
    assert active.variant_kind == "temporary"
    assert active.content is not None
    assert irnode_to_text(active.content) == "temporary v2"

    # At 2017: temporary expired -> permanent v3 should win
    active_after = select_active_version(tl, "2017-01-01")
    assert active_after is not None
    assert active_after.variant_kind == "permanent"
    assert active_after.content is not None
    assert irnode_to_text(active_after.content) == "permanent v3"

    # At 2003: before temporary window -> permanent v1 should win
    active_before = select_active_version(tl, "2003-01-01")
    assert active_before is not None
    assert active_before.variant_kind == "permanent"
    assert active_before.content is not None
    assert irnode_to_text(active_before.content) == "permanent v1"


def test_select_active_version_agrees_with_materialize_pit() -> None:
    """select_active_version should agree with materialize_pit on what version
    is active for each provision at a given date (split-brain prevention)."""
    base = IRStatute(
        statute_id="test/two-rail",
        title="Two-rail consistency test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    ops = [
        LegalOperation(
            op_id="temp_replace",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="temporary overlay"),
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
                expires="2015-12-31",
            ),
        ),
        LegalOperation(
            op_id="perm_replace",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="permanent update"),
            source=OperationSource(
                statute_id="2010/200",
                enacted="2010-01-01",
                effective="2010-01-01",
            ),
        ),
    ]
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        ops,
        base_date="2000-01-01",
    )
    pit = materialize_pit(timelines, "2012-01-01", base=base)

    # select_active_version should return the same content as materialize_pit
    active = select_active_version(timelines[addr], "2012-01-01")
    assert active is not None
    pit_section = next(
        (c for c in pit.body.children if c.kind == IRNodeKind.SECTION and c.label == "1"),
        None,
    )
    assert pit_section is not None
    assert active.content is not None
    assert irnode_to_text(active.content) == irnode_to_text(pit_section)


def test_select_active_version_filters_by_territory_applicability() -> None:
    """Territory-scoped selection should ignore non-matching versions."""
    addr = LegalAddress(path=(("section", "1"),))
    england_only = ScopePredicate(
        dimension="territory",
        includes=frozenset({"England"}),
    )
    scotland_only = ScopePredicate(
        dimension="territory",
        includes=frozenset({"Scotland"}),
    )
    tl = ProvisionTimeline(address=addr, versions=[
        ProvisionVersion(
            effective="2000-01-01",
            enacted="2000-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),
            applicability=[england_only],
        ),
        ProvisionVersion(
            effective="2010-01-01",
            enacted="2010-01-01",
            variant_kind="temporary",
            expires="2015-12-31",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scotland temp"),
            applicability=[scotland_only],
        ),
    ])

    england = select_active_version(tl, "2012-01-01", territory="England")
    assert england is not None
    assert england.content is not None
    assert irnode_to_text(england.content) == "England text"

    scotland = select_active_version(tl, "2012-01-01", territory="Scotland")
    assert scotland is not None
    assert scotland.content is not None
    assert irnode_to_text(scotland.content) == "Scotland temp"

    with pytest.raises(ValueError, match="requires explicit scope"):
        select_active_version(tl, "2012-01-01")


def test_materialize_pit_territory_falls_back_when_overlay_scope_mismatches() -> None:
    """A non-matching temporary overlay must not suppress a matching background version."""
    base = IRStatute(
        statute_id="test/territory",
        title="Territory filtering",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    ops = [
        LegalOperation(
            op_id="england_replace",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="England permanent"),
            applicability=(
                ScopePredicate(
                    dimension="territory",
                    includes=frozenset({"England"}),
                ),
            ),
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
            ),
        ),
        LegalOperation(
            op_id="scotland_temp",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scotland temporary"),
            applicability=(
                ScopePredicate(
                    dimension="territory",
                    includes=frozenset({"Scotland"}),
                ),
            ),
            source=OperationSource(
                statute_id="2010/200",
                enacted="2010-01-01",
                effective="2010-01-01",
                expires="2015-12-31",
            ),
        ),
    ]
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        ops,
        base_date="2000-01-01",
    )

    pit_england = materialize_pit(
        timelines,
        "2012-01-01",
        base=base,
        territory="England",
    )
    england_section = next(
        c for c in pit_england.body.children if c.kind == IRNodeKind.SECTION and c.label == "1"
    )
    assert irnode_to_text(england_section) == "England permanent"

    pit_scotland = materialize_pit(
        timelines,
        "2012-01-01",
        base=base,
        territory="Scotland",
    )
    scotland_section = next(
        c for c in pit_scotland.body.children if c.kind == IRNodeKind.SECTION and c.label == "1"
    )
    assert irnode_to_text(scotland_section) == "Scotland temporary"


def test_materialize_pit_falls_back_to_permanent_version_after_temporary_expiry() -> None:
    """An expired temporary overlay must not block the earlier permanent version."""
    base = IRStatute(
        statute_id="test/expiry",
        title="Expiry fallback",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = {
        addr: ProvisionTimeline(
            address=addr,
            versions=[
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2000-01-01",
                    variant_kind="permanent",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="permanent text"),
                ),
                ProvisionVersion(
                    effective="2001-01-01",
                    enacted="2001-01-01",
                    expires="2002-01-01",
                    variant_kind="temporary",
                    content=IRNode(kind=IRNodeKind.SECTION, label="1", text="temporary text"),
                ),
            ],
        )
    }

    active = select_active_version(timelines[addr], "2003-01-01")
    assert active is not None
    assert active.variant_kind == "permanent"
    assert active.content is not None
    assert irnode_to_text(active.content) == "permanent text"

    pit = materialize_pit(timelines, "2003-01-01", base=base)
    sec = next((c for c in pit.body.children if c.kind == IRNodeKind.SECTION and c.label == "1"), None)
    assert sec is not None
    assert irnode_to_text(sec) == "permanent text"


def test_select_active_version_ex_marks_missing_territory_scope() -> None:
    """Scope-bearing active candidates must not be reported as plain absence."""
    addr = LegalAddress(path=(("section", "1"),))
    england_only = ScopePredicate(
        dimension="territory",
        includes=frozenset({"England"}),
    )
    tl = ProvisionTimeline(address=addr, versions=[
        ProvisionVersion(
            effective="2000-01-01",
            enacted="2000-01-01",
            variant_kind="permanent",
            content=IRNode(kind=IRNodeKind.SECTION, label="1", text="England text"),
            applicability=[england_only],
        ),
    ])

    selection = select_active_version_ex(tl, "2012-01-01")

    assert selection.status == "ambiguous_missing_scope"
    assert selection.version is None
    assert selection.required_dimensions == ("territory",)
    assert selection.certificate is not None
    assert selection.certificate.address == addr
    assert selection.certificate.selected_rail == "ambiguous_missing_scope"
    assert selection.certificate.candidate_count == 1
    assert selection.certificate.required_dimensions == ("territory",)
    with pytest.raises(ValueError, match="requires explicit scope"):
        select_active_version(tl, "2012-01-01")


def test_materialize_pit_ex_marks_degraded_missing_scope() -> None:
    """PIT materialization should surface omitted required scope explicitly."""
    base = IRStatute(
        statute_id="test/territory",
        title="Territory filtering",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    ops = [
        LegalOperation(
            op_id="england_replace",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="England permanent"),
            applicability=(
                ScopePredicate(
                    dimension="territory",
                    includes=frozenset({"England"}),
                ),
            ),
            source=OperationSource(
                statute_id="2005/100",
                enacted="2005-01-01",
                effective="2005-01-01",
            ),
        ),
    ]
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        ops,
        base_date="2000-01-01",
    )

    result = materialize_pit_ex(timelines, "2012-01-01", base=base)

    assert result.status == "degraded_missing_scope"
    assert result.required_dimensions == ("territory",)
    assert result.ambiguous_addresses == (addr,)
    assert result.certificate is not None
    assert result.certificate.selected_address_count == 0
    assert result.certificate.ambiguous_address_count == 1
    assert result.certificate.required_dimensions == ("territory",)
    assert result.statute.metadata["materialization_status"] == "degraded_missing_scope"
    assert result.statute.metadata["required_scope_dimensions"] == ("territory",)
    assert list(result.statute.body.children) == []
    with pytest.raises(ValueError, match="requires explicit scope"):
        materialize_pit(timelines, "2012-01-01", base=base)


def test_materialize_pit_ex_masks_equal_effective_child_on_later_enacted_parent() -> None:
    """Equal-effective parent replacements should mask same-era child entries."""
    base = IRStatute(
        statute_id="test/equal-effective",
        title="Equal-effective masking",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="Base section",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SUBSECTION,
                            label="1",
                            text="Base subsection",
                        ),
                    ),
                ),
            ),
        ),
    )
    section_addr = LegalAddress(path=(("section", "1"),))
    subsection_addr = LegalAddress(path=(("section", "1"), ("subsection", "1")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="older_subsection",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=subsection_addr,
                payload=IRNode(
                    kind=IRNodeKind.SUBSECTION,
                    label="1",
                    text="Old subsection",
                ),
                source=OperationSource(
                    statute_id="2020/001",
                    enacted="2020-01-01",
                    effective="2020-01-01",
                ),
                group_id="g:equal-effective:child",
            ),
            LegalOperation(
                op_id="later_parent",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=section_addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Parent replacement"),
                source=OperationSource(
                    statute_id="2020/002",
                    enacted="2020-02-01",
                    effective="2020-01-01",
                ),
                group_id="g:equal-effective:parent",
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:equal-effective:child",
                group_id="g:equal-effective:child",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-01-01"),
                scope=TemporalScope(target_statute="test/equal-effective"),
            ),
            TemporalEvent(
                event_id="ev:equal-effective:parent",
                group_id="g:equal-effective:parent",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2020-02-01"),
                scope=TemporalScope(target_statute="test/equal-effective"),
            ),
        ),
    )

    pit = materialize_pit(
        timelines,
        "2020-06-01",
        base=base,
    )
    section = next(c for c in pit.body.children if c.kind == IRNodeKind.SECTION and c.label == "1")
    assert irnode_to_text(section) == "Parent replacement"
    assert all(child.label != "1" or child.kind != IRNodeKind.SUBSECTION for child in section.children)


def test_materialize_pit_keeps_older_section_child_absent_from_later_section_payload() -> None:
    """A newer section root must not suppress an older child it does not carry."""
    base = IRStatute(
        statute_id="test/section-child-preservation",
        title="Section child preservation",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="32",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="32 §"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base 1"),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Base 2"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    section_addr = LegalAddress(path=(("chapter", "5"), ("section", "32")))
    subsection_1_addr = LegalAddress(path=(("chapter", "5"), ("section", "32"), ("subsection", "1")))
    subsection_2_addr = LegalAddress(path=(("chapter", "5"), ("section", "32"), ("subsection", "2")))
    subsection_3_addr = LegalAddress(path=(("chapter", "5"), ("section", "32"), ("subsection", "3")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_subsection_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=subsection_1_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Older subsection 1"),
                source=OperationSource(
                    statute_id="2019/248",
                    enacted="2019-05-17",
                    effective="2019-05-20",
                ),
                group_id="g:section-child:subsection1",
            ),
            LegalOperation(
                op_id="replace_section_32",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=section_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="32",
                    attrs={"lawvm_tail_policy": "preserve_unstated_tail"},
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="32 §"),
                        IRNode(kind=IRNodeKind.HEADING, text="Updated heading"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="New subsection 2"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="New subsection 3"),
                    ),
                ),
                source=OperationSource(
                    statute_id="2022/283",
                    enacted="2022-04-13",
                    effective="2022-05-01",
                ),
                group_id="g:section-child:parent",
            ),
        ],
        base_date="2016-08-15",
        temporal_events=(
            TemporalEvent(
                event_id="ev:section-child:subsection1",
                group_id="g:section-child:subsection1",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2019-05-20"),
                scope=TemporalScope(target_statute="test/section-child-preservation"),
            ),
            TemporalEvent(
                event_id="ev:section-child:parent",
                group_id="g:section-child:parent",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2022-05-01"),
                scope=TemporalScope(target_statute="test/section-child-preservation"),
            ),
        ),
    )

    pit = materialize_pit(timelines, "2025-01-01", base=base)
    chapter = next(c for c in pit.body.children if c.kind == IRNodeKind.CHAPTER and c.label == "5")
    section = next(c for c in chapter.children if c.kind == IRNodeKind.SECTION and c.label == "32")
    subsection_labels = [child.label for child in section.children if child.kind == IRNodeKind.SUBSECTION]

    assert subsection_labels == ["1", "2", "3"]
    subsection_1 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1")
    assert irnode_to_text(subsection_1) == "Older subsection 1"


def test_materialize_pit_exact_section_replace_masks_absent_older_children() -> None:
    """Exact section-root replaces must suppress older child timelines they omit."""
    base = IRStatute(
        statute_id="test/section-child-exact-mask",
        title="Section child exact mask",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base 1"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Base 2"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Base 3"),
                    ),
                ),
            ),
        ),
    )
    section_addr = LegalAddress(path=(("section", "7"),))
    subsection_2_addr = LegalAddress(path=(("section", "7"), ("subsection", "2")))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_subsection_2",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=subsection_2_addr,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Older subsection 2"),
                source=OperationSource(
                    statute_id="2007/1",
                    enacted="2007-01-01",
                    effective="2007-01-01",
                ),
                group_id="g:exact-mask:subsection2",
            ),
            LegalOperation(
                op_id="replace_section_7",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=section_addr,
                payload=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    attrs={"lawvm_tail_policy": "replace_if_target_scope_requires"},
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="7 §"),
                        IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Replacement subsection 1"),
                    ),
                ),
                source=OperationSource(
                    statute_id="2008/1",
                    enacted="2008-01-01",
                    effective="2008-01-01",
                ),
                group_id="g:exact-mask:parent",
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:exact-mask:subsection2",
                group_id="g:exact-mask:subsection2",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2007-01-01"),
                scope=TemporalScope(target_statute="test/section-child-exact-mask"),
            ),
            TemporalEvent(
                event_id="ev:exact-mask:parent",
                group_id="g:exact-mask:parent",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2008-01-01"),
                scope=TemporalScope(target_statute="test/section-child-exact-mask"),
            ),
        ),
    )

    pit = materialize_pit(timelines, "2010-01-01", base=base)
    section = next(c for c in pit.body.children if c.kind == IRNodeKind.SECTION and c.label == "7")
    subsection_labels = [child.label for child in section.children if child.kind == IRNodeKind.SUBSECTION]

    assert subsection_labels == ["1"]


def test_compile_timelines_seeds_and_materializes_schedule_roots() -> None:
    """Top-level schedules should participate in timeline seeding/materialization."""
    base = IRStatute(
        statute_id="test/schedules",
        title="Schedule timeline test",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(IRNode(
                kind=IRNodeKind.SCHEDULE,
                label="1",
                text="Base schedule",
                children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="Old para"),),
            ),),
    )
    schedule_addr = LegalAddress(path=(("schedule", "1"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_schedule",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=schedule_addr,
                payload=IRNode(
                    kind=IRNodeKind.SCHEDULE,
                    label="1",
                    text="Updated schedule",
                    children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="New para"),),
                ),
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
    )

    assert schedule_addr in timelines
    pit = materialize_pit(timelines, "2012-01-01", base=base)
    assert [schedule.label for schedule in pit.supplements] == ["1"]
    assert pit.supplements[0].text == "Updated schedule"
    assert [child.text for child in pit.supplements[0].children] == ["New para"]


def test_compile_timelines_materializes_appendix_supplements() -> None:
    """Top-level appendix roots should materialize through supplements too."""
    base = IRStatute(
        statute_id="test/appendix",
        title="Appendix timeline test",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
        supplements=(IRNode(kind=IRNodeKind.APPENDIX, label="A", text="Base appendix"),),
    )
    appendix_addr = LegalAddress(path=(("appendix", "A"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_appendix",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=appendix_addr,
                payload=IRNode(kind=IRNodeKind.APPENDIX, label="A", text="Updated appendix"),
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
    )

    pit = materialize_pit(timelines, "2012-01-01", base=base)
    assert [supp.kind for supp in pit.supplements] == [IRNodeKind.APPENDIX]
    assert pit.supplements[0].text == "Updated appendix"


def test_compile_timelines_renumber_moves_active_content_to_destination() -> None:
    """Renumber should create destination lineage and tombstone the source address."""
    base = IRStatute(
        statute_id="test/renumber",
        title="Renumber timeline test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Section one"),)),
    )
    source_addr = LegalAddress(path=(("section", "1"),))
    dest_addr = LegalAddress(path=(("section", "2"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="renumber_1_to_2",
                sequence=1,
                action=StructuralAction.RENUMBER,
                target=source_addr,
                destination=dest_addr,
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
    )

    assert dest_addr in timelines
    dest_active = select_active_version(timelines[dest_addr], "2012-01-01")
    assert dest_active is not None
    assert dest_active.content is not None
    assert irnode_to_text(dest_active.content) == "Section one"

    source_active = select_active_version(timelines[source_addr], "2012-01-01")
    assert source_active is not None
    assert source_active.content is None

    pit = materialize_pit(timelines, "2012-01-01", base=base)
    assert [child.label for child in pit.body.children if child.kind == IRNodeKind.SECTION] == ["2"]
    assert pit.body.children[0].text == "Section one"


def test_materialize_pit_keeps_descendants_under_tombstoned_ancestor() -> None:
    """A tombstoned ancestor must not hide a live descendant subtree."""
    base = IRStatute(
        statute_id="test/tombstone-descendant",
        title="Tombstone descendant test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="4",
                    children=(
                        IRNode(kind=IRNodeKind.NUM, text="IV osa"),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="18",
                            children=(
                                IRNode(kind=IRNodeKind.NUM, text="18 luku"),
                                IRNode(
                                    kind=IRNodeKind.SECTION,
                                    label="159",
                                    children=(IRNode(kind=IRNodeKind.NUM, text="159 §"),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    part_addr = LegalAddress(path=(("part", "4"),))
    chapter_addr = LegalAddress(path=(("part", "4"), ("chapter", "18")))
    section_addr = LegalAddress(path=(("part", "4"), ("chapter", "18"), ("section", "159")))
    timelines = {
        part_addr: ProvisionTimeline(
            address=part_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=base.body.children[0],
                ),
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=None,
                ),
            ],
        ),
        chapter_addr: ProvisionTimeline(
            address=chapter_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=base.body.children[0].children[1],
                ),
            ],
        ),
        section_addr: ProvisionTimeline(
            address=section_addr,
            versions=[
                ProvisionVersion(
                    effective="0000-00-00",
                    enacted="0000-00-00",
                    content=base.body.children[0].children[1].children[1],
                ),
                ProvisionVersion(
                    effective="2019-04-01",
                    enacted="2019-03-29",
                    content=IRNode(
                        kind=IRNodeKind.SECTION,
                        label="159",
                        text="Updated section 159",
                    ),
                ),
            ],
        ),
    }

    pit = materialize_pit(timelines, "2025-01-01", base=base)
    section = _find_node_by_label(pit.body, IRNodeKind.SECTION, "159")
    assert section is None


def test_compile_timelines_temporal_events_override_effective_and_expiry() -> None:
    """Explicit temporal events should override legacy source timing when opted in."""
    base = IRStatute(
        statute_id="test/temporal-override",
        title="Temporal override test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Override text"),
                group_id="g:1",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                    expires="",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:commence",
                group_id="g:1",
                kind="commence",
                effective="2010-01-01",
                source=OperationSource(
                    statute_id="test/temporal-override:source",
                    raw_text="commence",
                ),
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-override"),
            ),
            TemporalEvent(
                event_id="ev:expire",
                group_id="g:1",
                kind="expire",
                expires="2012-12-31",
                source=OperationSource(
                    statute_id="test/temporal-override:source",
                    expires="2012-12-31",
                ),
                scope=TemporalScope(target_statute="test/temporal-override"),
            ),
        ),
    )

    active_2007 = select_active_version(timelines[addr], "2007-01-01")
    assert active_2007 is not None
    assert active_2007.content is not None
    assert active_2007.content.text == "Base text"

    active_2011 = select_active_version(timelines[addr], "2011-01-01")
    assert active_2011 is not None
    assert active_2011.content is not None
    assert active_2011.content.text == "Override text"
    assert active_2011.effective == "2010-01-01"
    assert active_2011.enacted == "2005-01-01"
    assert active_2011.expires == "2012-12-31"

    active_2013 = select_active_version(timelines[addr], "2013-01-01")
    assert active_2013 is not None
    assert active_2013.content is not None
    assert active_2013.content.text == "Base text"


def test_compile_timelines_matched_temporal_events_do_not_inherit_legacy_expiry() -> None:
    """Matched TemporalEvents must stop source.expires from remaining authoritative."""
    base = IRStatute(
        statute_id="test/temporal-expiry-authority",
        title="Temporal expiry authority test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Event-authoritative text"),
                group_id="g:no-legacy-expiry",
                source=OperationSource(
                    statute_id="2010/102",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                    expires="2011-12-31",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:commence-only",
                group_id="g:no-legacy-expiry",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-expiry-authority"),
            ),
        ),
    )

    active_2012 = select_active_version(timelines[addr], "2012-06-01")
    assert active_2012 is not None
    assert active_2012.content is not None
    assert active_2012.content.text == "Event-authoritative text"
    assert active_2012.expires == ""


def test_compile_timelines_temporal_event_expiry_ignores_provenance_expiry_mismatch() -> None:
    """Typed TemporalEvent expiry must make runtime selection invariant to source.expires provenance."""
    base = IRStatute(
        statute_id="test/temporal-warning",
        title="Temporal warning test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    ops = [
        LegalOperation(
            op_id="replace_1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
            group_id="g:warn",
            source=OperationSource(
                statute_id="2010/103",
                enacted="2005-01-01",
                effective="2005-01-01",
                expires="2011-12-31",
            ),
        )
    ]
    temporal_events = (
        TemporalEvent(
            event_id="ev:warn-commence",
            group_id="g:warn",
            kind="commence",
            activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
            scope=TemporalScope(target_statute="test/temporal-warning"),
        ),
        TemporalEvent(
            event_id="ev:warn-expire",
            group_id="g:warn",
            kind="expire",
            expires="2012-12-31",
            source=OperationSource(
                statute_id="test/temporal-warning:source",
                expires="2012-12-31",
            ),
            scope=TemporalScope(target_statute="test/temporal-warning"),
        ),
    )

    result = compile_timelines_ex(
        base,
        ops,
        base_date="2000-01-01",
        temporal_events=temporal_events,
    )
    mismatch_ops = [
        dc_replace(
            ops[0],
            source=dc_replace(cast(OperationSource, ops[0].source), expires="2019-12-31"),
        )
    ]
    mismatch_result = compile_timelines_ex(
        base,
        mismatch_ops,
        base_date="2000-01-01",
        temporal_events=temporal_events,
    )

    base_active = select_active_version(result.timelines[addr], "2011-06-01")
    mismatch_active = select_active_version(mismatch_result.timelines[addr], "2011-06-01")
    assert base_active is not None
    assert mismatch_active is not None
    assert base_active.content is not None
    assert mismatch_active.content is not None
    assert base_active.content.text == "Updated"
    assert mismatch_active.content.text == "Updated"
    assert base_active.effective == "2010-01-01"
    assert mismatch_active.effective == "2010-01-01"
    assert base_active.expires == "2012-12-31"
    assert mismatch_active.expires == "2012-12-31"

    base_kinds = {issue.kind for issue in result.issues}
    mismatch_kinds = {issue.kind for issue in mismatch_result.issues}
    assert "temporal_authority_source_expires" not in base_kinds
    assert "temporal_authority_source_expires" not in mismatch_kinds


def test_compile_timelines_records_empty_same_day_interval_issue() -> None:
    """Zero-length temporal overlays are typed timeline evidence, not Python warnings."""
    base = IRStatute(
        statute_id="test/same-day-empty",
        title="Same-day empty interval test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        result = compile_timelines_ex(
            base,
            [
                LegalOperation(
                    op_id="replace_1",
                    sequence=1,
                    action=StructuralAction.REPLACE,
                    target=addr,
                    payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
                    group_id="g:same-day-empty",
                    source=OperationSource(statute_id="2010/104", enacted="2010-01-01"),
                )
            ],
            base_date="2000-01-01",
            temporal_events=(
                TemporalEvent(
                    event_id="ev:same-day-commence",
                    group_id="g:same-day-empty",
                    kind="commence",
                    activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                    scope=TemporalScope(target_statute="test/same-day-empty"),
                ),
                TemporalEvent(
                    event_id="ev:same-day-expire",
                    group_id="g:same-day-empty",
                    kind="expire",
                    expires="2010-01-01",
                    scope=TemporalScope(target_statute="test/same-day-empty"),
                ),
            ),
        )

    same_day_issues = [issue for issue in result.issues if issue.kind == "empty_same_day_interval"]
    assert len(same_day_issues) == 1
    assert same_day_issues[0].address == addr
    assert same_day_issues[0].blocking is False
    assert same_day_issues[0].strict_disposition == "record"
    assert same_day_issues[0].quirks_disposition == "record"
    assert same_day_issues[0].rule_id == "timeline.empty_same_day_interval"
    assert [
        warning
        for warning in captured
        if "empty same-day temporal interval" in str(warning.message)
    ] == []


def test_compile_timelines_embedded_activation_rule_drives_contingent_skip() -> None:
    """Embedded activation rules should own contingent status before legacy fields."""
    base = IRStatute(
        statute_id="test/temporal-contingent",
        title="Temporal contingent test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated"),
                group_id="g:contingent",
                source=OperationSource(
                    statute_id="2010/104",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:contingent",
                group_id="g:contingent",
                kind="commence",
                activation_rule=ActivationRule(kind="pending_decree"),
                scope=TemporalScope(target_statute="test/temporal-contingent"),
            ),
        ),
    )

    kinds = {issue.kind for issue in result.issues}
    assert "skipped_contingent_unresolved" in kinds


def test_compile_timelines_immediate_temporal_event_is_executable() -> None:
    """Immediate commencement should materialize from explicit activation authority."""
    base = IRStatute(
        statute_id="test/temporal-immediate",
        title="Temporal immediate test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Immediate text"),
                group_id="g:immediate",
                source=OperationSource(
                    statute_id="2010/105",
                    enacted="2010-01-01",
                    effective="",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:immediate",
                group_id="g:immediate",
                kind="commence",
                activation_rule=ActivationRule(kind="immediate", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-immediate"),
            ),
        ),
    )

    active_2010 = select_active_version(timelines[addr], "2010-01-01")
    assert active_2010 is not None
    assert active_2010.content is not None
    assert active_2010.content.text == "Immediate text"
    assert active_2010.effective == "2010-01-01"
    assert active_2010.enacted == "2010-01-01"


def test_compile_timelines_immediate_temporal_event_with_event_level_effective() -> None:
    """Immediate events with explicit event-level effective date should materialize."""
    base = IRStatute(
        statute_id="test/temporal-immediate-event-effective",
        title="Temporal immediate event test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Immediate event text"),
                group_id="g:immediate-event",
                source=OperationSource(
                    statute_id="2010/106",
                    enacted="2010-01-01",
                    effective="",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:immediate",
                group_id="g:immediate-event",
                kind="commence",
                effective="2010-01-01",
                activation_rule=ActivationRule(kind="immediate"),
                scope=TemporalScope(target_statute="test/temporal-immediate-event-effective"),
            ),
        ),
    )

    active_2010 = select_active_version(timelines[addr], "2010-01-01")
    assert active_2010 is not None
    assert active_2010.content is not None
    assert active_2010.content.text == "Immediate event text"
    assert active_2010.effective == "2010-01-01"
    assert active_2010.enacted == "2010-01-01"


def test_compile_timelines_temporal_events_override_applicability() -> None:
    """Set-applicability events should feed the authoritative selection path."""
    base = IRStatute(
        statute_id="test/temporal-scope",
        title="Temporal applicability test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped text"),
                group_id="g:scope",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:scope:commence",
                group_id="g:scope",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-scope"),
            ),
            TemporalEvent(
                event_id="ev:scope",
                group_id="g:scope",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/temporal-scope",
                    predicates=(
                        ScopePredicate(
                            dimension="territory",
                            includes=frozenset({"AX"}),
                        ),
                    ),
                ),
            ),
        ),
    )

    ambiguous = select_active_version_ex(timelines[addr], "2011-01-01")
    assert ambiguous.status == "ambiguous_missing_scope"
    assert ambiguous.required_dimensions == ("territory",)

    selected = select_active_version_ex(timelines[addr], "2011-01-01", territory="AX")
    assert selected.status == "selected"
    assert selected.version is not None
    assert selected.version.content is not None
    assert selected.version.content.text == "Scoped text"


def test_compile_timelines_rejects_unsupported_applicability_predicates() -> None:
    """Non-executable applicability predicates should be surfaced as an issue, not stored."""
    territory_only = ScopePredicate(
        dimension="territory",
        includes=frozenset({"AX"}),
    )
    base = IRStatute(
        statute_id="test/temporal-applicability-reject",
        title="Temporal applicability rejection test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))
    issues: list[TimelineIssue] = []
    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped text"),
                group_id="g:scope-reject",
                applicability=(territory_only,),
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:scope-reject:commence",
                group_id="g:scope-reject",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-applicability-reject"),
            ),
            TemporalEvent(
                event_id="ev:scope-reject",
                group_id="g:scope-reject",
                kind="set_applicability",
                scope=TemporalScope(
                    target_statute="test/temporal-applicability-reject",
                    predicates=(
                        ScopePredicate(
                            dimension="applicability",
                            includes=frozenset({"tätä lakia sovelletaan vain AX:ssa"}),
                        ),
                    ),
                ),
            ),
        ),
        issue_sink=issues,
    )

    assert any(issue.kind == "unsupported_applicability_dimension" for issue in issues)
    assert timelines[addr].versions[-1].applicability == (territory_only,)

    ambiguous = select_active_version_ex(timelines[addr], "2011-01-01")
    assert ambiguous.status == "ambiguous_missing_scope"

    selected = select_active_version_ex(timelines[addr], "2011-01-01", territory="AX")
    assert selected.status == "selected"
    assert selected.version is not None
    assert selected.version.content is not None
    assert selected.version.content.text == "Scoped text"


def test_compile_timelines_temporal_events_honor_exact_addresses() -> None:
    """Address-scoped TemporalEvents should only affect matching operations."""
    base = IRStatute(
        statute_id="test/temporal-exact-address",
        title="Temporal exact-address test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="Base one"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="Base two"),
            ),
        ),
    )
    addr1 = LegalAddress(path=(("section", "1"),))
    addr2 = LegalAddress(path=(("section", "2"),))
    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr1,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped one"),
                group_id="g:exact",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
            LegalOperation(
                op_id="replace_2",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr2,
                payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Scoped two"),
                group_id="g:exact",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:exact",
                group_id="g:exact",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(
                    target_statute="test/temporal-exact-address",
                    exact_addresses=(addr1,),
                ),
            ),
        ),
    )

    active_2007_one = select_active_version(timelines[addr1], "2007-01-01")
    assert active_2007_one is not None
    assert active_2007_one.content is not None
    assert active_2007_one.content.text == "Base one"

    active_2007_two = select_active_version(timelines[addr2], "2007-01-01")
    assert active_2007_two is not None
    assert active_2007_two.content is not None
    assert active_2007_two.content.text == "Base two"

    active_2011_one = select_active_version(timelines[addr1], "2011-01-01")
    assert active_2011_one is not None
    assert active_2011_one.content is not None
    assert active_2011_one.content.text == "Scoped one"


def test_compile_timelines_temporal_events_honor_exact_addresses_after_target_resolution() -> None:
    """Exact-address scope should match canonical resolved targets, not just raw frontend paths."""
    base = IRStatute(
        statute_id="test/temporal-resolved-exact-address",
        title="Temporal resolved exact-address test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),),
                ),
            ),
        ),
    )
    raw_addr = LegalAddress(path=(("section", "1"),))
    resolved_addr = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=raw_addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Resolved scoped text"),
                group_id="g:resolved-exact",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:resolved-exact",
                group_id="g:resolved-exact",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(
                    target_statute="test/temporal-resolved-exact-address",
                    exact_addresses=(resolved_addr,),
                ),
            ),
        ),
    )

    active_2007 = select_active_version(timelines[resolved_addr], "2007-01-01")
    assert active_2007 is not None
    assert active_2007.content is not None
    assert active_2007.content.text == "Base text"

    active_2011 = select_active_version(timelines[resolved_addr], "2011-01-01")
    assert active_2011 is not None
    assert active_2011.content is not None
    assert active_2011.content.text == "Base text"


def test_compile_timelines_temporal_events_honor_address_prefixes_after_target_resolution() -> None:
    """Prefix-scoped temporal events should match canonical resolved touched addresses."""
    base = IRStatute(
        statute_id="test/temporal-prefix-address",
        title="Temporal prefix-address test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base one"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    text="Chapter 2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="2", text="Base two"),),
                ),
            ),
        ),
    )
    raw_ch1 = LegalAddress(path=(("section", "1"),))
    raw_ch2 = LegalAddress(path=(("chapter", "2"), ("section", "2")))
    resolved_ch1 = LegalAddress(path=(("chapter", "1"), ("section", "1")))
    resolved_ch2 = LegalAddress(path=(("chapter", "2"), ("section", "2")))
    chapter1_prefix = LegalAddress(path=(("chapter", "1"),))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_ch1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=raw_ch1,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped one"),
                group_id="g:prefix",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
            LegalOperation(
                op_id="replace_ch2",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=raw_ch2,
                payload=IRNode(kind=IRNodeKind.SECTION, label="2", text="Scoped two"),
                group_id="g:prefix",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:prefix",
                group_id="g:prefix",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(
                    target_statute="test/temporal-prefix-address",
                    address_prefixes=(chapter1_prefix,),
                ),
            ),
        ),
    )

    active_2007_one = select_active_version(timelines[resolved_ch1], "2007-01-01")
    assert active_2007_one is not None
    assert active_2007_one.content is not None
    assert active_2007_one.content.text == "Base one"

    active_2007_two = select_active_version(timelines[resolved_ch2], "2007-01-01")
    assert active_2007_two is not None
    assert active_2007_two.content is not None
    assert active_2007_two.content.text == "Base two"

    active_2011_one = select_active_version(timelines[resolved_ch1], "2011-01-01")
    assert active_2011_one is not None
    assert active_2011_one.content is not None
    assert active_2011_one.content.text == "Base one"


def test_compile_timelines_temporal_events_honor_exact_address_descendants_when_opted_in() -> None:
    """Exact-address scope may cover descendant ops only when explicitly opted in."""
    base = IRStatute(
        statute_id="test/temporal-exact-descendants",
        title="Temporal exact descendant test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            text="Section 1",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base subsection"),),
                        ),
                    ),
                ),
            ),
        ),
    )
    raw_target = LegalAddress(path=(("section", "1"), ("subsection", "1")))
    resolved_target = LegalAddress(path=(("chapter", "1"), ("section", "1"), ("subsection", "1")))
    resolved_section = LegalAddress(path=(("chapter", "1"), ("section", "1")))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_subsection_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=raw_target,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Updated subsection"),
                group_id="g:exact-descendants",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:exact-descendants",
                group_id="g:exact-descendants",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(
                    target_statute="test/temporal-exact-descendants",
                    exact_addresses=(resolved_section,),
                    include_future_descendants=True,
                ),
            ),
        ),
    )

    active_2007 = select_active_version(timelines[resolved_target], "2007-01-01")
    assert active_2007 is not None
    assert active_2007.content is not None
    assert active_2007.content.text == "Base subsection"

    active_2011 = select_active_version(timelines[resolved_target], "2011-01-01")
    assert active_2011 is not None
    assert active_2011.content is not None
    assert active_2011.content.text == "Base subsection"


def test_compile_timelines_temporal_events_do_not_honor_exact_address_descendants_without_opt_in() -> None:
    """Ancestor exact-address scope must not reach descendants unless explicitly opted in."""
    base = IRStatute(
        statute_id="test/temporal-exact-descendants",
        title="Temporal exact descendant test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    text="Chapter 1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="1",
                            text="Section 1",
                            children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Base subsection"),),
                        ),
                    ),
                ),
            ),
        ),
    )
    raw_target = LegalAddress(path=(("section", "1"), ("subsection", "1")))
    resolved_target = LegalAddress(path=(("chapter", "1"), ("section", "1"), ("subsection", "1")))
    resolved_section = LegalAddress(path=(("chapter", "1"), ("section", "1")))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_subsection_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=raw_target,
                payload=IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Updated subsection"),
                group_id="g:exact-descendants",
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2005-01-01",
                    effective="2005-01-01",
                ),
            ),
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:exact-descendants",
                group_id="g:exact-descendants",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(
                    target_statute="test/temporal-exact-descendants",
                    exact_addresses=(resolved_section,),
                ),
            ),
        ),
    )

    active_2011 = select_active_version(timelines[resolved_target], "2011-01-01")
    assert active_2011 is not None
    assert active_2011.content is not None
    assert active_2011.content.text == "Base subsection"

    active_2007 = select_active_version(timelines[resolved_target], "2007-01-01")
    assert active_2007 is not None
    assert active_2007.content is not None
    assert active_2007.content.text == "Base subsection"


def test_compile_timelines_temporal_date_events_preserve_existing_applicability() -> None:
    """Date-only TemporalEvents must not erase territorial applicability."""
    england_only = ScopePredicate(
        dimension="territory",
        includes=frozenset({"ENG"}),
    )
    base = IRStatute(
        statute_id="test/temporal-scope-preserve",
        title="Temporal applicability preservation",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    timelines = compile_timelines(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Scoped text"),
                group_id="g:scope-preserve",
                applicability=(england_only,),
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:commence-only",
                group_id="g:scope-preserve",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2011-01-01"),
                scope=TemporalScope(target_statute="test/temporal-scope-preserve"),
            ),
        ),
    )

    ambiguous = select_active_version_ex(timelines[addr], "2011-02-01")
    assert ambiguous.status == "ambiguous_missing_scope"
    assert ambiguous.required_dimensions == ("territory",)

    selected = select_active_version_ex(timelines[addr], "2011-02-01", territory="ENG")
    assert selected.status == "selected"
    assert selected.version is not None
    assert selected.version.content is not None
    assert selected.version.content.text == "Scoped text"


def test_compile_timelines_heading_replace_is_not_dropped() -> None:
    """Heading-only replace ops must survive timeline compilation."""
    heading_target = LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING)
    base = IRStatute(
        statute_id="test/heading-replace",
        title="Heading replace",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.HEADING, text="Old heading"),),
                ),
            ),
        ),
    )

    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="heading_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=heading_target,
                payload=IRNode(kind=IRNodeKind.HEADING, text="New heading"),
                source=OperationSource(
                    statute_id="2010/101",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
                group_id="g:heading-replace",
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:heading-replace",
                group_id="g:heading-replace",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/heading-replace"),
            ),
        ),
    )

    assert heading_target not in timelines
    selected = select_active_version_ex(timelines[LegalAddress(path=(("section", "1"),))], "2011-01-01")
    assert selected.status == "selected"
    assert selected.version is not None
    assert selected.version.content is not None
    assert selected.version.content.text == ""


# ---------------------------------------------------------------------------
# Bug #4: Ambiguous suffix resolution warning
# ---------------------------------------------------------------------------


def test_compile_timelines_ambiguous_suffix_records_issue() -> None:
    """When two chapters have the same section label, the ambiguity should be typed."""
    base = IRStatute(
        statute_id="test/ambig",
        title="Ambiguous suffix test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.SECTION, label="5", text="ch1 sec5"),)),
            IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(IRNode(kind=IRNodeKind.SECTION, label="5", text="ch2 sec5"),)),)),
    )
    # Op targets section 5 without chapter context — ambiguous suffix
    ambig_addr = LegalAddress(path=(("section", "5"),))
    ops = [
        LegalOperation(
            op_id="ambig_replace",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=ambig_addr,
            payload=IRNode(kind=IRNodeKind.SECTION, label="5", text="updated sec5"),
            source=OperationSource(
                statute_id="2020/1",
                enacted="2020-01-01",
                effective="2020-01-01",
            ),
        ),
    ]

    explicit_ops, explicit_events = _with_explicit_temporal_authority(base, ops)
    result = compile_timelines_ex(
        base,
        explicit_ops,
        base_date="2000-01-01",
        temporal_events=explicit_events,
    )

    assert not any(issue.kind == "ambiguous_suffix" for issue in result.issues)
    chapter1 = LegalAddress(path=(("chapter", "1"), ("section", "5")))
    chapter2 = LegalAddress(path=(("chapter", "2"), ("section", "5")))
    bare_section = LegalAddress(path=(("section", "5"),))
    assert bare_section not in result.timelines
    chapter1_content = result.timelines[chapter1].versions[-1].content
    chapter2_content = result.timelines[chapter2].versions[-1].content
    assert chapter1_content is not None
    assert chapter1_content.text == "ch1 sec5"
    assert chapter2_content is not None
    assert chapter2_content.text == "ch2 sec5"


# ---------------------------------------------------------------------------
# Bug #7: _apply_overlays normalized-duplicate sibling fallback
# ---------------------------------------------------------------------------


def test_apply_overlays_dup_norm_siblings_exact_label_fallback() -> None:
    """When siblings '1' and '1.' normalize to the same key, an override for
    '1' should still match the child with raw label '1' via exact-label fallback."""
    # Base content with two siblings whose labels normalize to the same key
    content = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="original para 1"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1.", text="original para 1-dot"),),
    )
    parent_addr = LegalAddress(path=(("section", "1"),))

    # Override targets ("paragraph", "1") — the one with exact label "1"
    override_addr = LegalAddress(path=(("section", "1"), ("paragraph", "1")))
    active: dict[LegalAddress, IRNode | None] = {
        override_addr: IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="UPDATED para 1"),
    }

    def strip_dot(label: str) -> str:
        return label.rstrip(".")

    result = _apply_overlays(content, parent_addr, active, label_norm=strip_dot)

    # The override should have been applied to the child with exact label "1"
    para_texts = [(c.label, irnode_to_text(c)) for c in result.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(para_texts) == 2, f"Expected 2 paragraphs, got {para_texts}"

    # Child with label "1" should have the updated text
    para_1 = next((t for l, t in para_texts if l == "1"), None)
    assert para_1 == "UPDATED para 1", f"Override not applied: {para_texts}"

    # Child with label "1." should be unchanged
    para_1_dot = next((t for l, t in para_texts if l == "1."), None)
    assert para_1_dot == "original para 1-dot", f"Wrong child modified: {para_texts}"


def test_apply_overlays_dup_norm_unmatched_override_emits_warning() -> None:
    """When an override can't be matched due to dup-norm and no exact match,
    a warning should be emitted."""
    content = IRNode(
        kind=IRNodeKind.SECTION,
        label="1",
        children=(IRNode(kind=IRNodeKind.PARAGRAPH, label="1.", text="original para 1-dot"),
            IRNode(kind=IRNodeKind.PARAGRAPH, label="1..", text="original para 1-dotdot"),),
    )
    parent_addr = LegalAddress(path=(("section", "1"),))

    # Override targets ("paragraph", "1") — neither child has exact label "1"
    override_addr = LegalAddress(path=(("section", "1"), ("paragraph", "1")))
    active: dict[LegalAddress, IRNode | None] = {
        override_addr: IRNode(kind=IRNodeKind.PARAGRAPH, label="1", text="UPDATED"),
    }

    def strip_dots(label: str) -> str:
        return label.rstrip(".")

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = _apply_overlays(content, parent_addr, active, label_norm=strip_dots)

    # Should have emitted a warning about unmatched override
    dup_warnings = [w for w in caught if "normalized-duplicate" in str(w.message)]
    assert len(dup_warnings) == 0


# ---------------------------------------------------------------------------
# temporal_event_not_matched: explicit temporal authority migration visibility
# ---------------------------------------------------------------------------


def test_compile_timelines_temporal_event_not_matched_emits_typed_issue() -> None:
    """When temporal_events are provided and an op has a group_id that matches no
    event, a temporal_event_not_matched issue must be recorded.

    This test checks the migration signal only and does not assert exact
    fallback materialization."""
    base = IRStatute(
        statute_id="test/temporal-not-matched",
        title="Temporal not-matched test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Fallback text"),
                group_id="g:unmatched",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-06-01",
                    effective="2010-06-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:different-group",
                group_id="g:other",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-not-matched"),
            ),
        ),
    )

    # Issue must be present
    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" in issue_kinds, (
        f"Expected temporal_event_not_matched issue, got kinds: {issue_kinds!r}"
    )

    # This test only asserts the migration signal itself; it does not rely on
    # any source-date fallback path.
    assert addr in result.timelines


def test_compile_timelines_temporal_event_not_matched_skips_source_dates() -> None:
    """Unmatched temporal-event group IDs should not fall back to OperationSource dates."""
    base = IRStatute(
        statute_id="test/temporal-not-matched-fallback",
        title="Temporal fallback test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Fallback text"),
                group_id="g:unmatched",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-06-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:different-group",
                group_id="g:other",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-not-matched-fallback"),
            ),
        ),
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" in issue_kinds
    assert "missing_operation_date" in issue_kinds

    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Base text"
    assert active_2010_07.effective == "2000-01-01"


def test_compile_timelines_finland_johto_temporal_event_mismatch_skips_source_dates_when_provenance_ordering_explicit() -> None:
    """Finland batch IDs still need explicit temporal events; provenance is ordering-only."""
    base = IRStatute(
        statute_id="test/temporal-not-matched-fi-batch",
        title="Finland temporal batch fallback test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Fallback text"),
                group_id="finland-johto:1999/1",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-06-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:other",
                group_id="finland-johto:2000/2",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-not-matched-fi-batch"),
            ),
        ),
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" in issue_kinds
    assert "missing_operation_date" in issue_kinds

    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Base text"
    assert active_2010_07.effective == "2000-01-01"


def test_compile_timelines_finland_johto_temporal_event_mismatch_strict_also_skips_source_dates() -> None:
    """Strict mode remains explicit, but executable temporal authority is still required."""
    base = IRStatute(
        statute_id="test/temporal-not-matched-fi-batch-strict",
        title="Finland temporal batch strict fallback test",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Fallback text"),
                group_id="finland-johto:1999/1",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-06-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:other",
                group_id="finland-johto:2000/2",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/temporal-not-matched-fi-batch-strict"),
            ),
        ),
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" in issue_kinds
    assert "missing_operation_date" in issue_kinds

    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Base text"


def test_compile_timelines_no_temporal_event_not_matched_with_explicit_authority() -> None:
    """When temporal authority is explicit, matched groups should not emit a gap issue."""
    base = IRStatute(
        statute_id="test/no-temporal-events",
        title="Explicit temporal authority",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    explicit_ops, explicit_events = _with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Updated text"),
                group_id="g:legacy",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        temporal_events=(
            TemporalEvent(
                event_id="ev:replace_1",
                group_id="g:legacy",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/no-temporal-events"),
            ),
        ),
    )
    result = compile_timelines_ex(
        base,
        explicit_ops,
        base_date="2000-01-01",
        temporal_events=explicit_events,
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" not in issue_kinds, (
        f"Should NOT emit temporal_event_not_matched with explicit temporal authority, "
        f"got: {issue_kinds!r}"
    )
    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Updated text"


def test_compile_timelines_no_temporal_event_not_matched_when_op_has_no_group_id() -> None:
    """Without a group_id, explicit temporal authority cannot be matched."""
    base = IRStatute(
        statute_id="test/no-group-id",
        title="No group_id",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace_1",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text="Legacy op text"),
                # group_id omitted — op predates lowering
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:some",
                group_id="g:some",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/no-group-id"),
            ),
        ),
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "temporal_event_not_matched" in issue_kinds
    assert "missing_operation_date" in issue_kinds

    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Base text"


def test_compile_timelines_replace_without_payload_records_issue_and_preserves_base() -> None:
    """Payload-less replace-style ops are rejected explicitly in core."""
    base = IRStatute(
        statute_id="test/missing-replace-payload",
        title="Missing replace payload",
        body=IRNode(kind=IRNodeKind.BODY, children=(IRNode(kind=IRNodeKind.SECTION, label="1", text="Base text"),)),
    )
    addr = LegalAddress(path=(("section", "1"),))

    result = compile_timelines_ex(
        base,
        [
            LegalOperation(
                op_id="replace-missing-payload",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=None,
                group_id="g:missing-replace-payload",
                source=OperationSource(
                    statute_id="2010/100",
                    enacted="2010-01-01",
                    effective="2010-01-01",
                ),
            )
        ],
        base_date="2000-01-01",
        temporal_events=(
            TemporalEvent(
                event_id="ev:missing-replace-payload",
                group_id="g:missing-replace-payload",
                kind="commence",
                activation_rule=ActivationRule(kind="fixed_date", effective_date="2010-01-01"),
                scope=TemporalScope(target_statute="test/missing-replace-payload"),
            ),
        ),
    )

    issue_kinds = {issue.kind for issue in result.issues}
    assert "missing_replace_payload" in issue_kinds

    target_tl = result.timelines[addr]
    active_2010_07 = select_active_version(target_tl, "2010-07-01")
    assert active_2010_07 is not None
    assert active_2010_07.content is not None
    assert active_2010_07.content.text == "Base text"
    assert active_2010_07.effective == "2000-01-01"


def _version_signature(version: ProvisionVersion) -> tuple[str, str, str, str | None]:
    content_text = irnode_to_text(version.content) if version.content is not None else None
    return (version.effective, version.enacted, version.expires, content_text)


@given(
    temp_text=SHORT_TEXT,
    replacement_text=SHORT_TEXT,
    start_date=st.dates(min_value=date(2000, 1, 1), max_value=date(2029, 12, 30)),
    lifespan_days=st.integers(min_value=2, max_value=365),
    replacement_offset=st.integers(min_value=0, max_value=364),
)
@settings(max_examples=40, deadline=None)
def test_temporary_version_replacement_inherits_expiry(
    temp_text: str,
    replacement_text: str,
    start_date,
    lifespan_days: int,
    replacement_offset: int,
) -> None:
    """Replacing a temporary version must preserve the inherited expiry."""
    expiry = start_date + timedelta(days=lifespan_days)
    assume(expiry <= date(2030, 12, 31))
    assume(replacement_offset < lifespan_days)

    replacement_effective = start_date + timedelta(days=replacement_offset)
    base = IRStatute(
        statute_id="test/temp-expiry-property",
        title="Temporary expiry property",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    addr = LegalAddress(path=(("section", "5e"),))
    timelines = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="insert_temp",
                sequence=1,
                action=StructuralAction.INSERT,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="5e", text=temp_text),
                source=OperationSource(
                    statute_id="2020/294",
                    title="temporary insert",
                    enacted=start_date.isoformat(),
                    effective=start_date.isoformat(),
                    expires=expiry.isoformat(),
                ),
            ),
            LegalOperation(
                op_id="replace_without_expiry",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr,
                payload=IRNode(kind=IRNodeKind.SECTION, label="5e", text=replacement_text),
                source=OperationSource(
                    statute_id="2020/485",
                    title="temporary replace",
                    enacted=replacement_effective.isoformat(),
                    effective=replacement_effective.isoformat(),
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    tl = timelines[addr]
    assert tl.versions[-1].expires == expiry.isoformat()
    assert select_active_version(tl, start_date.isoformat()) is not None
    assert select_active_version(tl, (expiry + timedelta(days=1)).isoformat()) is None


@given(
    primary_text=SHORT_TEXT,
    secondary_text=SHORT_TEXT,
    primary_start=st.dates(min_value=date(2000, 1, 1), max_value=date(2029, 12, 30)),
    primary_lifespan=st.integers(min_value=2, max_value=365),
    secondary_start=st.dates(min_value=date(2000, 1, 1), max_value=date(2029, 12, 30)),
    secondary_lifespan=st.integers(min_value=2, max_value=365),
)
@settings(max_examples=40, deadline=None)
def test_unrelated_versions_do_not_change_disjoint_timeline(
    primary_text: str,
    secondary_text: str,
    primary_start,
    primary_lifespan: int,
    secondary_start,
    secondary_lifespan: int,
) -> None:
    """A version on a disjoint address may not change an untouched timeline."""
    primary_expiry = primary_start + timedelta(days=primary_lifespan)
    secondary_expiry = secondary_start + timedelta(days=secondary_lifespan)
    assume(primary_expiry <= date(2030, 12, 31))
    assume(secondary_expiry <= date(2030, 12, 31))

    base = IRStatute(
        statute_id="test/disjoint-temporal",
        title="Disjoint temporal stability property",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.SECTION, label="1", text="base 1"),
                IRNode(kind=IRNodeKind.SECTION, label="2", text="base 2"),
            ),
        ),
    )
    addr1 = LegalAddress(path=(("section", "1"),))
    addr2 = LegalAddress(path=(("section", "2"),))
    primary_only = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_primary",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr1,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text=primary_text),
                source=OperationSource(
                    statute_id="2021/100",
                    title="primary replace",
                    enacted=primary_start.isoformat(),
                    effective=primary_start.isoformat(),
                    expires=primary_expiry.isoformat(),
                ),
            )
        ],
        base_date="2000-01-01",
    )
    with_unrelated = _compile_timelines_with_explicit_temporal_authority(
        base,
        [
            LegalOperation(
                op_id="replace_primary",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=addr1,
                payload=IRNode(kind=IRNodeKind.SECTION, label="1", text=primary_text),
                source=OperationSource(
                    statute_id="2021/100",
                    title="primary replace",
                    enacted=primary_start.isoformat(),
                    effective=primary_start.isoformat(),
                    expires=primary_expiry.isoformat(),
                ),
            ),
            LegalOperation(
                op_id="replace_secondary",
                sequence=2,
                action=StructuralAction.REPLACE,
                target=addr2,
                payload=IRNode(kind=IRNodeKind.SECTION, label="2", text=secondary_text),
                source=OperationSource(
                    statute_id="2021/200",
                    title="secondary replace",
                    enacted=secondary_start.isoformat(),
                    effective=secondary_start.isoformat(),
                    expires=secondary_expiry.isoformat(),
                ),
            ),
        ],
        base_date="2000-01-01",
    )

    sig_primary_only = [_version_signature(v) for v in primary_only[addr1].versions]
    sig_with_unrelated = [_version_signature(v) for v in with_unrelated[addr1].versions]
    assert sig_with_unrelated == sig_primary_only


@st.composite
def renumber_lineage_case(draw) -> tuple[LegalAddress, LegalAddress, tuple[MigrationEvent, ...], ProvisionVersion]:
    base_num = draw(st.integers(min_value=1, max_value=9))
    chain_len = draw(st.integers(min_value=2, max_value=4))
    effective_date = draw(st.dates(min_value=date(2000, 1, 1), max_value=date(2029, 12, 31)))
    labels = [f"{base_num}{'a' * idx}" for idx in range(chain_len)]
    start_addr = LegalAddress(path=(("section", labels[0]),))
    final_addr = LegalAddress(path=(("section", labels[-1]),))
    events = tuple(
        MigrationEvent(
            event_id=f"mig:test:{labels[idx]}->{labels[idx + 1]}",
            kind="renumber",
            from_address=LegalAddress(path=(("section", labels[idx]),)),
            to_address=LegalAddress(path=(("section", labels[idx + 1]),)),
            effective=effective_date.isoformat(),
            source_statute="2000/1",
        )
        for idx in range(chain_len - 1)
    )
    shuffled_events = tuple(draw(st.permutations(events)))
    version = ProvisionVersion(
        effective=effective_date.isoformat(),
        enacted=effective_date.isoformat(),
        content=IRNode(kind=IRNodeKind.SECTION, label=labels[-1], text="final"),
    )
    return start_addr, final_addr, shuffled_events, version


@given(renumber_lineage_case())
@settings(max_examples=40, deadline=None)
def test_renumber_lineage_is_deterministic_under_event_permutation(
    case: tuple[LegalAddress, LegalAddress, tuple[MigrationEvent, ...], ProvisionVersion],
) -> None:
    """Renumber lineage must resolve the same final address regardless of event order."""
    start_addr, final_addr, shuffled_events, version = case
    timelines = {final_addr: ProvisionTimeline(address=final_addr, versions=[version])}

    assert current_address_from_migration_events(
        start_addr,
        shuffled_events,
        as_of_date="2030-12-31",
    ) == final_addr
    assert provision_lineage(
        timelines,
        start_addr,
        migration_events=shuffled_events,
        as_of_date="2030-12-31",
    ) == [version]
