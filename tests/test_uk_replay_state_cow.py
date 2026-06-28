"""PR3 (audit XJUR-02 / AGENTS.md §2.3) regression guards for the
``replay_state`` copy-on-write migration.

Pins four invariants that ``replay_state.py`` now provides after the
in-place-mutation rewrite to copy-on-write rebuilds:

  * (1) The ``IRStatute`` produced by ``replay_uk_ops`` IS the frozen
    ``IRNode``-rooted tree (post-Sub-PR-F: the prior ``to_irstatute()``
    boundary converter and ``UKMutableNode`` shadow were deleted; the executor
    statute IS the immutable core ``IRStatute`` directly).
  * (2) Mutating ``.children`` / ``.attrs`` / ``.label`` / ``.text`` on any
    frozen ``IRNode`` in that tree raises ``dataclasses.FrozenInstanceError``.
  * (3) Idempotency: replaying the same ops twice produces structurally
    identical final ``IRStatute`` trees (modulo op-id minting, which is
    controlled by the caller-supplied op inputs).
  * (4) Each op in a REPLACE + INSERT + REPEAL sequence leaves the body
    ``children`` tuple ``IRNode``-typed end-to-end across the mutate-bookkeeping
    chain built in PR3.

Sub-PR F (mutable_ir Wave N3d, final) removed the prior
``TestPR3NoUKMutableNodeLeaks`` class — once ``mutable_ir.py`` was deleted
there is no shadow class to detect, so the leak-detection guard no longer
adds coverage. The frozen-IRNode-type and frozen-field assertions below
remain load-bearing: a future change that re-introduces an in-place
``parent.children.pop`` / ``parent.children[idx] = new_node`` would still
break the frozen-tuple property if it did so on an ``IRNode`` shared with
the live tree (silent regression), making (1) a failing test rather than a
hidden regression.
"""
from __future__ import annotations

import copy
import dataclasses
import time
from typing import Any

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.replay_state import NodeIndexEntry
from lawvm.uk_legislation.uk_amendment_replay import UKReplayExecutor, replay_uk_ops


# ---------------------------------------------------------------------------
# Synthetic statute + op builders
# ---------------------------------------------------------------------------


def _source() -> OperationSource:
    return OperationSource(statute_id="ukpga/2026/99", title="Amending Act")


def _multi_section_base() -> IRStatute:
    """Base statute with sections 1-7 for the multi-op replay tests."""
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                IRNode(kind=IRNodeKind.SECTION, label=str(n), text=f"Section {n} original text.")
                for n in range(1, 8)
            ),
        ),
        supplements=(),
    )


def _multi_section_base_with_eids() -> IRStatute:
    """Sibling base assigning an explicit ``eId`` to every top-level section so
    Sub-PR C+D warm-EID-index tests can drive a real warm-index lookup through
    the CoW chain."""
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Test Act",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=tuple(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label=str(n),
                    text=f"Section {n} original text.",
                    attrs={"eId": f"section-{n}"},
                )
                for n in range(1, 8)
            ),
        ),
        supplements=(),
    )


def _replace_op_with_eid(section_label: str, text: str, *, op_id: str) -> LegalOperation:
    """Sibling to ``_replace_op`` whose payload preserves the eId of the
    section being replaced, so the warm EID index entry survives the CoW
    replace and a subsequent ``_find_node_and_parent_statute(eid)`` resolves
    to the replacement node."""
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=section_label,
            text=text,
            attrs={"eId": f"section-{section_label}"},
        ),
        source=_source(),
        sequence=1,
    )


def _replace_op(section_label: str, text: str, *, op_id: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=section_label,
            text=text,
        ),
        source=_source(),
        sequence=1,
    )


def _insert_op(section_label: str, text: str, *, op_id: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", section_label),)),
        payload=IRNode(
            kind=IRNodeKind.SECTION,
            label=section_label,
            text=text,
        ),
        source=_source(),
        sequence=2,
    )


def _repeal_op(section_label: str, *, op_id: str) -> LegalOperation:
    return LegalOperation(
        op_id=op_id,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", section_label),)),
        source=_source(),
        sequence=3,
    )


def _multi_op_sequence() -> list[LegalOperation]:
    """REPLACE §5 + INSERT §8 + REPEAL §7."""
    return [
        _replace_op("5", "Replaced section five text.", op_id="uk-cow-replace-5"),
        _insert_op("8", "Inserted section eight.", op_id="uk-cow-insert-8"),
        _repeal_op("7", op_id="uk-cow-repeal-7"),
    ]


# ---------------------------------------------------------------------------
# Tree-walk helpers
# ---------------------------------------------------------------------------


def _collect_irnode_kinds(node: Any) -> list[str]:
    """Recursively collect ``str(node.kind)`` values for structural equality."""
    kinds = [str(getattr(node, "kind", ""))]
    for child in getattr(node, "children", ()):
        kinds.extend(_collect_irnode_kinds(child))
    return kinds


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPR3BoundaryIsFrozenIRNode:
    """(1) ``replay_uk_ops`` produces an ``IRStatute`` whose body is the frozen
    core ``IRNode`` rather than the UK-local mutable workspace."""

    def test_result_statute_body_is_frozen_irnode(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        assert isinstance(replayed.body, IRNode)
        # ``children`` MUST be a tuple (frozen IR invariant); a leaked list would
        # indicate a mutable shadow slipping through the boundary.
        assert isinstance(replayed.body.children, tuple)

    def test_result_statute_supplements_are_frozen_irnode(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        for supplement in replayed.supplements:
            assert isinstance(supplement, IRNode)
            assert isinstance(supplement.children, tuple)


class TestPR3MutationRaisesFrozenInstanceError:
    """(2) Mutating a frozen IRNode field raises ``FrozenInstanceError``."""

    @pytest.mark.parametrize(
        "field",
        ["label", "text", "children", "attrs"],
    )
    def test_setattr_on_irnode_field_raises_frozen(self, field: str) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        with pytest.raises(dataclasses.FrozenInstanceError):
            # ``setattr`` (not direct ``=`` assignment) keeps the test compatible
            # with the typed frozen ``IRNode`` API checked by ty.
            setattr(replayed.body, field, [])


class TestPR3MultiOpShape:
    """(4) Each op in REPLACE + INSERT + REPEAL produces the expected tree
    shape end-to-end, exercising every PR3 CoW helper path
    (``_replace_node_in_statute`` → ``_remove_node`` → ``_cow_*``)."""

    def test_replace_insert_repeal_yields_expected_labels(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        labels = [c.label for c in replayed.body.children]
        # Section 5 replaced in place; section 7 repealed; section 8 inserted
        # (sorted after section 7 → so labels are 1..6, 8 with 7 gone).
        assert labels == ["1", "2", "3", "4", "5", "6", "8"], (
            f"Multi-op replay shape changed: {labels!r}"
        )
        # Section 5 carries the replaced text; section 8 carries the inserted
        # text; section 7 is gone (not present in the body).
        sec5 = next(c for c in replayed.body.children if c.label == "5")
        assert sec5.text == "Replaced section five text."
        sec8 = next(c for c in replayed.body.children if c.label == "8")
        assert sec8.text == "Inserted section eight."
        assert not any(c.label == "7" for c in replayed.body.children)

    def test_each_intermediate_op_yields_frozen_irnode_boundary(self) -> None:
        """Run each op through the executor directly (not via ``replay_uk_ops``)
        so the ``executor.statute.body`` after each op is asserted against the
        frozen IRNode boundary, exercising the CoW helper for every action family."""
        executor = UKReplayExecutor(_multi_section_base())
        for op in _multi_op_sequence():
            executor.apply_op(op)
            # Sub-PR C+D/F: ``executor.statute`` IS the immutable ``IRStatute``
            # (the prior ``UKMutableStatute`` mirror was deleted); assert the
            # frozen-IRNode + tuple-children invariant directly.
            frozen_body = executor.statute.body
            assert isinstance(frozen_body, IRNode)
            assert isinstance(frozen_body.children, tuple)
            for child in frozen_body.children:
                assert isinstance(child, IRNode)


class TestPR3Idempotency:
    """(3) Replaying the same ops twice on the same base produces a
    structurally identical final IRStatute (regardless of new node identities
    minted per replay)."""

    def test_two_replays_produce_structurally_identical_results(self) -> None:
        ops = _multi_op_sequence()
        replay_a = replay_uk_ops(_multi_section_base(), ops)
        replay_b = replay_uk_ops(_multi_section_base(), ops)
        # Structural shape: (kind, label, text, attrs) tuples of every node.
        # ``IRNode`` is a frozen dataclass — value equality compares field by
        # field. Two CoW replays should produce structurally identical trees.
        assert _collect_irnode_kinds(replay_a.body) == _collect_irnode_kinds(replay_b.body)
        # Same labels, in same order.
        labels_a = [c.label for c in replay_a.body.children]
        labels_b = [c.label for c in replay_b.body.children]
        assert labels_a == labels_b
        # Same text where the labels match (sections 1-4, 6 unchanged; section 5
        # replaced).
        for a, b in zip(replay_a.body.children, replay_b.body.children, strict=True):
            assert a.label == b.label
            assert a.text == b.text, (
                f"Section {a.label!r} text diverged between two replays: "
                f"{a.text!r} vs {b.text!r}"
            )

    def test_full_value_equality_between_two_replays(self) -> None:
        """Sibling test to the structural-shape assertions: full
        ``IRStatute.__eq__`` is ``@dataclass(frozen=True)`` so a stable
        deterministic replay must compare equal across two CoW runs."""
        ops = _multi_op_sequence()
        replay_a = replay_uk_ops(_multi_section_base(), ops)
        replay_b = replay_uk_ops(_multi_section_base(), ops)
        assert replay_a == replay_b, (
            "IRStatute value equality broke — two deterministic CoW replays of "
            "the same op stream on the same base produced different trees."
        )


class TestWaveN3dCoWChainPreservesWarmEIDIndex:
    """Sub-PR C+D regression: pins that after the ``replay_executor.py`` IRStatute
    boundary switch + the warm-EID-index CoW chain (``_cow_replace_in_subtree_preserve_warm_index``
    + ``_rekey_eid_index_after_cow_chain``):

      * The post-replay ``executor.statute`` IS the immutable ``IRStatute`` (no
        ``UKMutableStatute`` shadow reaches the boundary).
      * ``executor.statute.body`` is a frozen ``IRNode`` (immutable attrs+children).
      * The warm EID lookup index STAYS WARM across each replace — subsequent
        ``_find_node_and_parent_statute(eid)`` calls hit the warm-index fast
        path and return the LIVE node objects in the rebuilt tree (not stale
        orphan references from before a replace).
      * End-to-end replay on a multi-op sequence produces the expected labels
        (REPLACE §5 + INSERT §8 + REPEAL §7 leaves labels [1,2,3,4,5,6,8]).
    """

    def test_executor_statute_is_ir_statute_after_replay(self) -> None:
        executor = UKReplayExecutor(_multi_section_base())
        for op in _multi_op_sequence():
            executor.apply_op(op)
        assert isinstance(executor.statute, IRStatute)
        # Sub-PR C+D: ``UKMutableStatute`` had a ``to_irstatute()`` boundary
        # converter; the IRStatute IS the boundary. Assert that legacy shim
        # is gone so the executor doesn't expose a mutable mirror past the
        # replacement work in this regression suite.
        assert not hasattr(executor.statute, "to_irstatute")

    def test_warm_eid_index_resolves_live_nodes_after_multi_op_replace(self) -> None:
        """Drive a multi-op CoW replay (against a base whose sections carry
        explicit ``eId`` attrs so the warm index has real entries), then verify
        that the warm EID lookup still resolves each surviving EID to the node
        currently in the live ``IRStatute`` tree (``executor.statute.body``)."""
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        for op in _multi_op_sequence():
            executor.apply_op(op)
        # Section 5 was replaced; section 7 was repealed; section 8 was inserted
        # (sorted after section 6 — labels are 1..6, 8 with 7 gone).
        labels = [c.label for c in executor.statute.body.children]
        assert labels == ["1", "2", "3", "4", "5", "6", "8"], labels
        # Warm EID lookup for section-5 should resolve to the LIVE node in the
        # body's children tuple, not a stale orphan reference from a pre-CoW state.
        node_5, parent_5, idx_5 = executor._find_node_and_parent_statute("section-5")
        assert node_5 is not None
        # ``is`` identity: the warm-index return value IS exactly the live body
        # root's child at index 4 (post-CoW chain re-key + add).
        assert node_5 is executor.statute.body.children[4]
        assert parent_5 is executor.statute.body
        assert idx_5 == 4
        # Section 7 was repealed — its EID should not resolve via warm lookup.
        node_7, _parent_7, _idx_7 = executor._find_node_and_parent_statute("section-7")
        assert node_7 is None

    def test_post_replace_lookups_stay_warm_across_replaces(self) -> None:
        """Pin that the warm EID lookup survives a CoW replace and continues
        to find sibling nodes that were NOT replaced — i.e. the warm index
        re-key path (``_rekey_eid_index_after_cow_chain``) preserved entries
        whose parent was CoW-rebuilt (so they now point at the new parent)."""
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        # Replace only section 5; sections 1-4, 6, 7 should remain findable via
        # the warm index without a rebuild.
        executor.apply_op(_replace_op_with_eid("5", "Replaced section five text.", op_id="uk-rel5-eid"))
        # Subsequent EID lookup for an unchanged sibling (section 6) must
        # return the LIVE post-CoW node — verifying the warm index was re-keyed
        # so the entry's parent points at the rebuilt body root.
        node_6, parent_6, idx_6 = executor._find_node_and_parent_statute("section-6")
        assert node_6 is not None
        assert node_6 is executor.statute.body.children[5]
        assert parent_6 is executor.statute.body
        assert idx_6 == 5
        # Section 5 was replaced → its EID lookup returns the replacement node
        # (the new node IS in the live tree, post-CoW).
        node_5_after, _parent_5_after, _idx_5_after = executor._find_node_and_parent_statute("section-5")
        assert node_5_after is not None
        assert node_5_after is executor.statute.body.children[4]
        assert node_5_after.text == "Replaced section five text."


# ---------------------------------------------------------------------------
# iter2 W1 perf invariant: patch-in-place CoW re-key vs. prior O(W) rebuild
# ---------------------------------------------------------------------------


def _build_deep_statute_with_eids(
    n_sections: int = 10,
    n_subsections_per_section: int = 5,
    n_paragraphs_per_subsection: int = 5,
) -> IRStatute:
    """Synthetic statute with explicit ``eId`` attrs on every node so the warm
    EID index is populated to ~W = ``n_sections * (1 + n_subsections *
    (1 + n_paragraphs))`` entries. Used by the perf regression to drive
    deep-node REPLACE ops through the CoW chain."""
    sections: list[IRNode] = []
    for s in range(1, n_sections + 1):
        subsecs: list[IRNode] = []
        for ss in range(1, n_subsections_per_section + 1):
            paras: list[IRNode] = []
            for p in range(1, n_paragraphs_per_subsection + 1):
                paras.append(IRNode(
                    kind=IRNodeKind.PARAGRAPH,
                    label=str(p),
                    text=f"Section {s} / subsec {ss} / para {p} original.",
                    attrs={"eId": f"section-{s}/subsection-{ss}/paragraph-{p}"},
                ))
            subsecs.append(IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=str(ss),
                text=f"Section {s} / subsec {ss} intro.",
                attrs={"eId": f"section-{s}/subsection-{ss}"},
                children=tuple(paras),
            ))
        sections.append(IRNode(
            kind=IRNodeKind.SECTION,
            label=str(s),
            text=f"Section {s} intro.",
            attrs={"eId": f"section-{s}"},
            children=tuple(subsecs),
        ))
    return IRStatute(
        statute_id="ukpga/2000/1",
        title="Perf Act",
        body=IRNode(kind=IRNodeKind.BODY, children=tuple(sections)),
        supplements=(),
    )


def _make_subsection_replace_ops(
    statute: IRStatute,
    n_ops: int,
    *,
    n_sections: int,
    n_subsections_per_section: int,
    n_paragraphs_per_subsection: int,
) -> list[LegalOperation]:
    """Build ``n_ops`` REPLACE ops targeting alternating subsections of the
    given deep statute. Each replacement subsection payload mirrors the
    original paragraph children so the warm EID index stays populated across
    ops (otherwise each replace would shrink the index, masking the per-op
    cost gap we want to measure)."""
    ops: list[LegalOperation] = []
    for i in range(n_ops):
        s = (i % n_sections) + 1
        ss = ((i // n_sections) % n_subsections_per_section) + 1
        paragraphs = tuple(
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label=str(p),
                text=f"Section {s} / subsec {ss} / para {p} REPLACED v{i}.",
                attrs={"eId": f"section-{s}/subsection-{ss}/paragraph-{p}"},
            )
            for p in range(1, n_paragraphs_per_subsection + 1)
        )
        ops.append(LegalOperation(
            op_id=f"perf-replace-{i}",
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(
                ("section", str(s)),
                ("subsection", str(ss)),
            )),
            payload=IRNode(
                kind=IRNodeKind.SUBSECTION,
                label=str(ss),
                text=f"Section {s} / subsec {ss} REPLACED v{i}.",
                attrs={"eId": f"section-{s}/subsection-{ss}"},
                children=paragraphs,
            ),
            source=OperationSource(statute_id="ukpga/2026/99", title="Amending Act"),
            sequence=i + 1,
        ))
    return ops


def _make_naive_rekey_eid_closure(executor: UKReplayExecutor):
    """Captured O(W) wholesale-rebuild of ``_rekey_eid_index_after_cow_chain``
    that the patch-in-place replaced. Pinned here as a synthetic stub so the
    regression guard can run the "before" baseline on the same process / same
    statute / same op stream as the patched production path. The prior impl
    re-allocated every ``NodeIndexEntry`` per CoW chain — ``W`` fresh
    allocations per op regardless of how many entries were actually touched
    by the chain — and wholesale-dropped the path index."""

    def _naive_rekey(chain: list[tuple[IRNode, IRNode]]) -> None:
        if not chain:
            return
        remap = {id(old): new for old, new in chain}
        if executor._eid_lookup_index is not None:
            new_index: dict[str, NodeIndexEntry] = {}
            for eid, entry in executor._eid_lookup_index.items():
                node, parent, idx = entry
                updated_node = remap.get(id(node), node)
                updated_parent = (
                    remap.get(id(parent), parent) if parent is not None else None
                )
                new_index[eid] = NodeIndexEntry(
                    node=updated_node, parent=updated_parent, index=idx
                )
            executor._eid_lookup_index = new_index
        if executor._eid_suffix_lookup_index is not None:
            new_suffix: dict[tuple[str, str], NodeIndexEntry] = {}
            for key, entry in executor._eid_suffix_lookup_index.items():
                node, parent, idx = entry
                updated_node = remap.get(id(node), node)
                updated_parent = (
                    remap.get(id(parent), parent) if parent is not None else None
                )
                new_suffix[key] = NodeIndexEntry(
                    node=updated_node, parent=updated_parent, index=idx
                )
            executor._eid_suffix_lookup_index = new_suffix
        # Prior path-index behaviour was a wholesale drop (the patched
        # production path replaces this with ``_rekey_node_tree_path_index_after_cow_chain``).
        executor._node_tree_path_index = None

    return _naive_rekey


def _make_naive_rekey_path_closure(executor: UKReplayExecutor):
    """Captured prior path-index handler: wholesale-drop. The patched
    production path replaces this with patch-in-place ancestor re-key."""

    def _naive_rekey_path(chain: list[tuple[IRNode, IRNode]]) -> None:
        executor._node_tree_path_index = None

    return _naive_rekey_path


class TestWaveN3dCoWReKeyPerfInRegression:
    """iter2 W1 perf regression guard: pins that the patch-in-place re-key
    path (``_rekey_eid_index_after_cow_chain`` + the new
    ``_rekey_node_tree_path_index_after_cow_chain``) is NOT SLOWER than the
    prior O(W) wholesale-rebuild baseline on the same multi-op workload.

    Per §2.9 the assertion is RELATIVE (naive ≥ patched, with a noise
    tolerance for CI jitter) rather than an absolute wall-time ceiling —
    both implementations run on the same workload in the same process, so
    timing noise affects both equally. The "before" implementation is
    captured as synthetic stub closures (monkeypatched onto the executor
    instance via plain attribute assignment, which Python resolves before
    the class-level production helpers) that mirror the prior O(W) full
    rebuild + wholesale path-index drop.

    The workload (deep-node REPLACE): ``N_OPS`` REPLACE ops on subsections of
    a ``N_SECTIONS`` × ``N_SUBSECTIONS`` × ``N_PARAGRAPHS`` statute with
    explicit ``eId`` attributes on every node. Each CoW chain reaches
    ``[subsection, section, body]`` (depth 3) so survivor paragraphs under
    sibling subsections (whose parent is NOT in the chain remap) keep their
    existing ``NodeIndexEntry`` tuples in the patched path — vs. the prior
    wholesale rebuild that re-allocated ALL ~W entries per op."""

    N_SECTIONS = 10
    N_SUBSECTIONS_PER_SECTION = 5
    N_PARAGRAPHS_PER_SUBSECTION = 5
    N_OPS = 80
    # Wall-time comparison noise tolerance: patched must not be more than
    # ``NAIVE_TOLERANCE`` slower than naive on the same workload (the patched
    # path strictly skips work, so it should always be ≤ naive; 20% is
    # generously defensive against first-run GC pauses / page faults on the
    # patched trial happening to land first).
    NAIVE_TOLERANCE = 1.20
    N_TRIALS = 3

    def _build_statute(self) -> IRStatute:
        return _build_deep_statute_with_eids(
            n_sections=self.N_SECTIONS,
            n_subsections_per_section=self.N_SUBSECTIONS_PER_SECTION,
            n_paragraphs_per_subsection=self.N_PARAGRAPHS_PER_SUBSECTION,
        )

    def _build_ops(self, statute: IRStatute) -> list[LegalOperation]:
        return _make_subsection_replace_ops(
            statute,
            self.N_OPS,
            n_sections=self.N_SECTIONS,
            n_subsections_per_section=self.N_SUBSECTIONS_PER_SECTION,
            n_paragraphs_per_subsection=self.N_PARAGRAPHS_PER_SUBSECTION,
        )

    def _time_replay(
        self,
        statute: IRStatute,
        ops: list[LegalOperation],
        *,
        use_naive: bool,
    ) -> float:
        executor = UKReplayExecutor(statute)
        if use_naive:
            # Plain instance-attribute assignment: Python resolves instance
            # attributes before class methods, so when the production CoW
            # chain calls ``self._rekey_eid_index_after_cow_chain(chain)``
            # it invokes our closure instead of the patched production method.
            executor._rekey_eid_index_after_cow_chain = _make_naive_rekey_eid_closure(executor)
            executor._rekey_node_tree_path_index_after_cow_chain = _make_naive_rekey_path_closure(executor)
        t0 = time.perf_counter()
        for op in ops:
            executor.apply_op(op)
        return time.perf_counter() - t0

    def test_patched_rekey_beats_naive_wholesale_rebuild(self) -> None:
        """Drive the same deep-node REPLACE workload through (a) the captured
        O(W) wholesale-rebuild ``_naive`` closure and (b) the production
        patched patch-in-place ``_rekey_*`` helpers; assert production is at
        least as fast as naive."""
        statute_template = self._build_statute()
        ops = self._build_ops(statute_template)

        # Sanity check: the workload actually executes CoW chains (otherwise
        # the perf gap is masked). The warm EID index has entries post-replay
        # and each op mutates the statute body's identity (CoW chain reached
        # the body root).
        sanity_executor = UKReplayExecutor(copy.deepcopy(statute_template))
        body_id_before = id(sanity_executor.statute.body)
        for op in ops[:5]:
            sanity_executor.apply_op(op)
        body_id_after = id(sanity_executor.statute.body)
        assert body_id_before != body_id_after, (
            "Sanity check failed: workload did NOT mutate the executor statute — "
            "the perf gap is masked because CoW chains are not being driven "
            "through the re-key helpers."
        )
        warm_index = sanity_executor._ensure_eid_lookup_index()
        assert len(warm_index) > 0, (
            "Sanity check failed: warm EID index is empty after replay — "
            "addressing or payload shape is wrong, masking the perf gap."
        )

        # Multi-trial min wall time filters CI jitter (e.g. transient GC
        # pauses). Same process, same workload, same statute template —
        # noise affects both implementations equally so the relative
        # comparison is robust.
        naive_times = [
            self._time_replay(copy.deepcopy(statute_template), ops, use_naive=True)
            for _ in range(self.N_TRIALS)
        ]
        patched_times = [
            self._time_replay(copy.deepcopy(statute_template), ops, use_naive=False)
            for _ in range(self.N_TRIALS)
        ]
        naive_min = min(naive_times)
        patched_min = min(patched_times)

        # Relative perf invariant. The patched path strictly skips
        # ``NodeIndexEntry`` re-allocations for survivor subtrees outside the
        # rebuilt ancestor chain (chain depth=3, so ~15 entries touched per
        # op vs. ~310 re-allocated by the prior full rebuild). It must be at
        # least as fast as the naive wholesale rebuild.
        assert patched_min <= naive_min * self.NAIVE_TOLERANCE, (
            f"Patched CoW re-key was materially slower than the prior O(W) "
            f"wholesale rebuild on a {self.N_SECTIONS}x"
            f"{self.N_SUBSECTIONS_PER_SECTION}x"
            f"{self.N_PARAGRAPHS_PER_SUBSECTION} statute with "
            f"{self.N_OPS} deep-node subsection replaces: "
            f"naive_min={naive_min*1000:.2f}ms "
            f"patched_min={patched_min*1000:.2f}ms "
            f"ratio={patched_min / naive_min:.3f} "
            f"(expected <= {self.NAIVE_TOLERANCE:.2f}). "
            f"Full naive times (ms): {[f'{t*1000:.2f}' for t in naive_times]}. "
            f"Full patched times (ms): {[f'{t*1000:.2f}' for t in patched_times]}."
        )
        # Sanity ceiling: no pathological latency regression independent of
        # the relative comparison. 5s on a ~310-node / ~80-op workload is
        # generously defensive; if the patched path crosses it, the relative
        # invariant above is also failing for the wrong reason.
        assert patched_min < 5.0, (
            f"Patched CoW re-key took >5s on a {self.N_SECTIONS}x"
            f"{self.N_SUBSECTIONS_PER_SECTION}x{self.N_PARAGRAPHS_PER_SUBSECTION} "
            f"statute with {self.N_OPS} replaces — performance regression."
        )
