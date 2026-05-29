"""Performance and cache-correctness tests for UK recursive-descent target lookup.

Covers:
  - Short-circuit at ≥2 stops walking early (§19.1 performance discipline)
  - Per-replay cache hit avoids re-walking the subtree on repeated queries
  - Cache invalidation after tree mutation (stale-data prevention)
  - Wall-time ceiling on synthetic deep/wide tree to catch future regressions

Tree shapes used
----------------
  Unique: section:1 contains part:A (200 plain subsections) + part:B (unique
          FINDME subsection).  Direct path section:1/subsection:FINDME fails;
          recursive descent must walk part:A fully before finding FINDME in B.

  Ambiguous: section:1 contains part:A and part:B, each with a FINDME
             subsection.  Recursive descent must short-circuit at 2 matches
             and report ambiguous without walking the whole tree.
"""
from __future__ import annotations

import time

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.uk_legislation.replay_target_lookup import (
    UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID,
    UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID,
)
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor, replay_uk_ops


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2000/99", title="Perf Amending Act")


# ---------------------------------------------------------------------------
# Statute builders
# ---------------------------------------------------------------------------

def _statute_unique_wide(*, n_plain_subsections: int = 200) -> IRStatute:
    """Section 1 with two parts.

    Part A has *n_plain_subsections* subsections with numeric labels.
    Part B has one subsection labelled FINDME.

    The direct path section:1/subsection:FINDME fails (FINDME is not a direct
    child of section 1); recursive descent must walk all of part A before
    finding the unique FINDME in part B.
    """
    subs_a = tuple(
        IRNode(kind=IRNodeKind.SUBSECTION, label=str(i + 1), text=f"Sub {i}.")
        for i in range(n_plain_subsections)
    )
    part_a = IRNode(kind=IRNodeKind.PART, label="A", text="", children=subs_a)
    findme = IRNode(kind=IRNodeKind.SUBSECTION, label="FINDME", text="Original.")
    part_b = IRNode(kind=IRNodeKind.PART, label="B", text="", children=(findme,))
    section = IRNode(
        kind=IRNodeKind.SECTION, label="1", text="", children=(part_a, part_b)
    )
    return IRStatute(
        statute_id="ukpga/2000/perf",
        title="Perf Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=(section,)),
        supplements=(),
    )


def _statute_ambiguous_in_section(*, n_plain_subsections: int = 200) -> IRStatute:
    """Section 1 with two parts, each having a FINDME subsection.

    Direct path section:1/subsection:FINDME fails; recursive descent finds 2
    matches and must short-circuit (ambiguous, refuse).

    Each part also has *n_plain_subsections* plain subsections to make the
    full-tree walk expensive without short-circuit.
    """
    def _make_plain_subs(offset: int) -> tuple[IRNode, ...]:
        return tuple(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=str(offset + i + 1),
                text=f"Sub {offset}-{i}.",
            )
            for i in range(n_plain_subsections)
        )

    findme_a = IRNode(kind=IRNodeKind.SUBSECTION, label="FINDME", text="From part A.")
    part_a = IRNode(
        kind=IRNodeKind.PART,
        label="A",
        text="",
        children=(findme_a,) + _make_plain_subs(0),
    )
    findme_b = IRNode(kind=IRNodeKind.SUBSECTION, label="FINDME", text="From part B.")
    part_b = IRNode(
        kind=IRNodeKind.PART,
        label="B",
        text="",
        children=(findme_b,) + _make_plain_subs(1000),
    )
    section = IRNode(
        kind=IRNodeKind.SECTION, label="1", text="", children=(part_a, part_b)
    )
    return IRStatute(
        statute_id="ukpga/2000/perf2",
        title="Ambiguous Perf Test Act",
        body=IRNode(kind=IRNodeKind.BODY, children=(section,)),
        supplements=(),
    )


def _replace_op(
    path: tuple,
    *,
    seq: int = 1,
    text: str = "Replaced.",
) -> LegalOperation:
    label = path[-1][1]
    return LegalOperation(
        op_id=f"uk-perf-op-{seq}",
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=path),
        payload=IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text),
        source=_source(),
        sequence=seq,
    )


_TARGET_PATH = (("section", "1"), ("subsection", "FINDME"))


# ---------------------------------------------------------------------------
# Test: wall-time ceiling on wide tree with 50 repeated ops
# ---------------------------------------------------------------------------

def test_recursive_descent_perf_ceiling() -> None:
    """50 replace ops on a 200-plain-subsection tree must finish in < 5 s.

    Without caching the recursive-descent result, each op walks all 200+
    nodes under part A before finding FINDME in part B.  With the cache the
    walk runs once and subsequent ops are O(1) lookups.
    """
    statute = _statute_unique_wide(n_plain_subsections=200)
    ops = [
        _replace_op(_TARGET_PATH, seq=i, text=f"Replaced by op {i}.")
        for i in range(1, 51)
    ]

    adjudications: list[CompileAdjudication] = []
    start = time.perf_counter()
    replay_uk_ops(statute, ops, adjudications_out=adjudications)
    elapsed = time.perf_counter() - start

    assert elapsed < 5.0, (
        f"Recursive-descent perf regression: {elapsed:.2f}s for 50 ops on 200-node "
        "tree, ceiling 5 s.  Cache or short-circuit may be broken."
    )

    # Verify the recovery adjudication still fires (behavior preserved)
    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID in rule_ids, (
        f"Recovery adjudication must still fire after perf fix; got {rule_ids}"
    )


# ---------------------------------------------------------------------------
# Test: cache hit avoids re-walking on repeated identical lookups
# ---------------------------------------------------------------------------

def test_recursive_descent_all_cache_hit_on_repeated_query() -> None:
    """Repeated identical lookups must hit the cache rather than re-walking.

    We issue the same _find_node_by_target call twice on the same executor
    without any intervening mutation.  The second call must be measurably
    faster than the first (cold walk vs cache hit).
    """
    statute = _statute_unique_wide(n_plain_subsections=500)
    adjudications: list[CompileAdjudication] = []
    executor = UKReplayExecutor(statute, adjudications_out=adjudications)

    target = LegalAddress(path=_TARGET_PATH)
    op = _replace_op(_TARGET_PATH, seq=1)

    # Cold call — populates cache
    t0 = time.perf_counter()
    result1 = executor._find_node_by_target(
        target, allow_recursive_match=True, target_resolution_op=op
    )
    cold_elapsed = time.perf_counter() - t0

    # Warm call — should hit cache (no mutation between calls)
    t1 = time.perf_counter()
    result2 = executor._find_node_by_target(
        target, allow_recursive_match=True, target_resolution_op=op
    )
    warm_elapsed = time.perf_counter() - t1

    # Both calls must find the same FINDME node
    assert result1.node is not None, "First lookup must find FINDME node"
    assert result1.node is result2.node, (
        "Cold and warm lookups must return the same node object"
    )

    # Cache hit must be substantially faster than the cold walk.
    # We guard with a floor to avoid flakiness on very fast machines.
    if cold_elapsed > 0.00005:  # 50µs: only assert ratio if walk was measurable
        assert warm_elapsed < cold_elapsed / 5, (
            f"Cache hit not fast enough: "
            f"cold={cold_elapsed * 1000:.3f}ms warm={warm_elapsed * 1000:.3f}ms. "
            "The _recursive_match_all_cache may not be firing."
        )


# ---------------------------------------------------------------------------
# Test: cache invalidates after tree mutation
# ---------------------------------------------------------------------------

def test_recursive_descent_all_cache_invalidates_after_mutation() -> None:
    """After a tree mutation the cache must not return the stale pre-mutation result.

    Two sequential REPLACE ops target the same FINDME node.  The first op
    triggers a recursive-descent recovery and caches the result.  The apply
    mutates the tree, which must flush the cache.  The second op must find
    the (now-mutated) node via a fresh walk and apply correctly.

    Final text must reflect op2, not op1.
    """
    statute = _statute_unique_wide(n_plain_subsections=50)
    target_path = _TARGET_PATH

    op1 = _replace_op(target_path, seq=1, text="After op1.")
    op2 = _replace_op(target_path, seq=2, text="After op2.")

    adjudications: list[CompileAdjudication] = []
    result = replay_uk_ops(statute, [op1, op2], adjudications_out=adjudications)

    # Navigate: body → section[0] → part[1] (part B) → FINDME
    section_node = result.body.children[0]
    part_b = section_node.children[1]  # part B is the second child
    findme_nodes = [c for c in part_b.children if str(c.label or "") == "FINDME"]

    assert findme_nodes, "FINDME node must still exist after two replacements"
    assert findme_nodes[0].text == "After op2.", (
        f"Cache invalidation failure: expected 'After op2.' but got "
        f"{findme_nodes[0].text!r}.  The _recursive_match_all_cache may have "
        "served stale data."
    )


# ---------------------------------------------------------------------------
# Test: short-circuit at ≥2 — ambiguous walk terminates quickly
# ---------------------------------------------------------------------------

def test_short_circuit_on_ambiguous_walk() -> None:
    """A tree with two FINDME matches short-circuits before walking all nodes.

    The statute has part A (FINDME first, then 200 plain subsections) and
    part B (FINDME first, then 200 plain subsections).  Without short-circuit,
    both parts are walked in full.  With short-circuit, the walk stops as soon
    as the second FINDME is seen.

    Wall-time ceiling: < 1 s (substantially under the 5 s unique-case ceiling).
    """
    statute = _statute_ambiguous_in_section(n_plain_subsections=200)
    adjudications: list[CompileAdjudication] = []

    op = _replace_op(_TARGET_PATH, seq=1)

    start = time.perf_counter()
    replay_uk_ops(statute, [op], adjudications_out=adjudications)
    elapsed = time.perf_counter() - start

    assert elapsed < 1.0, (
        f"Short-circuit regression: ambiguous walk took {elapsed:.2f}s (ceiling 1 s). "
        "The ≥2 early-exit in uk_recursive_kind_match_all may be broken."
    )

    rule_ids = [a.kind for a in adjudications]
    assert UK_REPLAY_TARGET_AMBIGUOUS_RECURSIVE_DESCENT_RULE_ID in rule_ids, (
        f"Ambiguity adjudication must still fire after short-circuit fix; got {rule_ids}"
    )
    assert UK_REPLAY_TARGET_RESOLVED_BY_RECURSIVE_DESCENT_RULE_ID not in rule_ids, (
        "Recovery adjudication must NOT fire when the result is ambiguous"
    )
