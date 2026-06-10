"""Defence-in-depth regression for the recursive-match-all cache.

``_cached_recursive_match_all`` must not serve a detached node even when a
mutation path forgets to bump ``_structure_mutation_serial`` (the failure
class the ``UKReplayStateMixin`` docstring warns about).  The sibling caches
``_cached_recursive_match`` / ``_cached_target_lookup`` already re-validate
attachment on every hit; this exercises the same per-match re-validation for
the all-matches cache.
"""
from __future__ import annotations

from typing import Any, cast

from lawvm.core.ir import IRNode, IRStatute
from lawvm.core.semantic_types import IRNodeKind
from lawvm.uk_legislation.canonicalize import UKCanonicalNodeMatch
from lawvm.uk_legislation.mutable_ir import UKMutableNode
from lawvm.uk_legislation.replay_executor import UKReplayExecutor


def _statute() -> IRStatute:
    """Section 1 → part A → two subsections labelled 2A and 2B."""
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="1",
                    text="",
                    children=(
                        IRNode(
                            kind=IRNodeKind.PART,
                            label="A",
                            text="",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2A",
                                    text="Original 2A.",
                                ),
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2B",
                                    text="Original 2B.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
        supplements=(),
    )


def _executor_with_cached_match() -> tuple[UKReplayExecutor, tuple[int, str, str], object]:
    """Build an executor and populate the all-cache with one real match.

    Returns (executor, cache_key, matched_subsection_node).
    """
    executor = UKReplayExecutor(_statute())
    section = executor.statute.body.children[0]
    part = section.children[0]
    subsection_2a = part.children[0]

    key = (id(part), "subsection", "2A")
    match = UKCanonicalNodeMatch(cast(Any, subsection_2a), cast(Any, part), 0)
    executor._store_recursive_match_all_cache(key, (match,))

    # Sanity: a clean hit returns the stored match verbatim.
    cached = executor._cached_recursive_match_all(key)
    assert cached is not None
    assert cached[0].node is subsection_2a
    return executor, key, subsection_2a


def test_clean_hit_returns_cached_match() -> None:
    executor, key, subsection_2a = _executor_with_cached_match()
    cached = executor._cached_recursive_match_all(key)
    assert cached is not None and len(cached) == 1
    assert cached[0].node is subsection_2a


def test_detached_node_not_served_without_serial_bump() -> None:
    """Simulate the future-refactor bug: a matched node is detached/replaced
    WITHOUT bumping ``_structure_mutation_serial``.  The cache must refuse to
    serve the detached node and recompute (return None) instead.
    """
    executor, key, subsection_2a = _executor_with_cached_match()
    section = executor.statute.body.children[0]
    part = section.children[0]

    # Replace the matched subsection 2A with a fresh node at index 0 — this is
    # exactly the structural mutation that would normally bump the serial, but
    # the buggy refactor we are guarding against forgets to.
    serial_before = executor._structure_mutation_serial
    part.children[0] = UKMutableNode(
        kind=IRNodeKind.SUBSECTION,
        label="2A",
        text="Replacement 2A.",
    )
    # Deliberately do NOT call _note_structure_mutation(); serial is unchanged.
    assert executor._structure_mutation_serial == serial_before

    cached = executor._cached_recursive_match_all(key)
    assert cached is None, (
        "Cache must NOT serve the detached node when the serial was not bumped"
    )
    # The stale entry must have been dropped so a recompute happens.
    assert key not in executor._recursive_match_all_cache


def test_index_drift_self_heals_without_serial_bump() -> None:
    """If a matched node is still attached under the same parent but its index
    drifted (sibling inserted before it) without a serial bump, the cache heals
    the index in place and keeps serving the still-attached node — mirroring the
    sibling caches' ``parent.children.index(node)`` recovery.
    """
    executor, key, subsection_2a = _executor_with_cached_match()
    section = executor.statute.body.children[0]
    part = section.children[0]

    # Insert a new sibling before the matched node so its index shifts 0 -> 1,
    # again WITHOUT bumping the serial.
    serial_before = executor._structure_mutation_serial
    part.children.insert(
        0,
        UKMutableNode(kind=IRNodeKind.SUBSECTION, label="0", text="Inserted ahead."),
    )
    assert executor._structure_mutation_serial == serial_before
    assert part.children[1] is subsection_2a

    cached = executor._cached_recursive_match_all(key)
    assert cached is not None and len(cached) == 1
    assert cached[0].node is subsection_2a
    assert cached[0].index == 1, "Index must be healed to the new position"
    # The healed entry must persist for the next hit.
    assert executor._recursive_match_all_cache[key][1][0].index == 1


def test_partial_detachment_recomputes_whole_entry() -> None:
    """When an entry has multiple matches and one is detached, the whole entry
    is recomputed (None) rather than partially filtered.
    """
    executor = UKReplayExecutor(_statute())
    section = executor.statute.body.children[0]
    part = section.children[0]
    sub_2a = part.children[0]
    sub_2b = part.children[1]

    key = (id(part), "subsection", "2x")
    executor._store_recursive_match_all_cache(
        key,
        (
            UKCanonicalNodeMatch(cast(Any, sub_2a), cast(Any, part), 0),
            UKCanonicalNodeMatch(cast(Any, sub_2b), cast(Any, part), 1),
        ),
    )

    # Detach only the second match, without bumping the serial.
    serial_before = executor._structure_mutation_serial
    part.children.pop(1)
    assert executor._structure_mutation_serial == serial_before

    cached = executor._cached_recursive_match_all(key)
    assert cached is None, "A single detached match must invalidate the whole entry"
    assert key not in executor._recursive_match_all_cache


def test_serial_bump_still_invalidates() -> None:
    """The original serial-based invalidation still works."""
    executor, key, _subsection_2a = _executor_with_cached_match()
    executor._note_structure_mutation()
    assert executor._cached_recursive_match_all(key) is None
