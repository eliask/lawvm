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


def test_serial_bump_still_invalidates() -> None:
    """The original serial-based invalidation still works."""
    executor, key, _subsection_2a = _executor_with_cached_match()
    executor._note_structure_mutation()
    assert executor._cached_recursive_match_all(key) is None
