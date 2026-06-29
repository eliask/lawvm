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
from lawvm.uk_legislation.replay_state import NodeIndexEntry, UKCoWAncestorChainLocateFailed
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


# ---------------------------------------------------------------------------
# iter2 W5 M3 — UKCoWAncestorChainLocateFailed fail-loud (silent-failure
# review). The unreachable-else tail of ``_remove_node`` /
# ``_do_replace_node_in_statute`` was a silent ``return False``; the caller at
# ``replay_repeal_apply.py:289-294`` discarded the boolean and unconditionally
# called ``_record_repealed_target(target)`` — recording a repeal that never
# landed against the live tree (AGENTS.md §0 over-repeal risk). These tests
# pin the fail-loud behaviour:
#
#   (1) ``_remove_node`` raises ``UKCoWAncestorChainLocateFailed`` (with the
#       right target / parent / idx) when BOTH the warm EID index CoW chain
#       AND the path-walk fallback fail to locate the target — a §2.9
#       guard-liveness fire-drill that drives the production path through the
#       live ``UKReplayExecutor`` (not a unit test of the exception class).
#   (2) ``_do_replace_node_in_statute`` raises the same exception when both
#       Cow paths fail.
#   (3) The production caller catches the exception via ``apply_op``, emits
#       a typed ``uk_replay_cow_chain_locate_failed`` adjudication, and DOES
#       NOT record the false repeal (over-repeal risk closed).
# ---------------------------------------------------------------------------


class TestUKCoWAncestorChainLocateFailed:
    """The unreachable-else tail of ``_remove_node`` /
    ``_do_replace_node_in_statute`` MUST raise when both CoW paths fail
    rather than silently returning ``False`` (AGENTS.md §0)."""

    def _resolve_live_section_five(
        self, executor: UKReplayExecutor
    ) -> tuple[IRNode, IRNode, int]:
        """Resolve section-5 + its parent + index through the warm EID
        lookup so the call has real production-lane identities (not orphans
        manufactured solely for the test)."""
        node, parent, idx = executor._find_node_and_parent_statute("section-5")
        assert node is not None, "section-5 must resolve via the warm EID index"
        assert parent is not None, "section-5's parent must resolve"
        assert idx is not None, "section-5's index must resolve"
        # Live identity check — the warm EID index returned the live node.
        assert node is executor.statute.body.children[4]
        return node, parent, idx

    def test_remove_node_raises_when_both_cow_paths_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§2.9 guard-liveness: drive the production ``UKReplayExecutor``
        path with both Cow paths forced to fail, assert the typed exception
        fires with the right target identity."""
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        node_5, parent_5, idx_5 = self._resolve_live_section_five(executor)

        # Force the unreachable-else tail: BOTH Cow paths return False so the
        # supplements loop is the only remaining success path — and section-5
        # is not a supplement root, so the loop yields no match either.
        monkeypatch.setattr(
            executor,
            "_cow_remove_in_parent_preserve_warm_index",
            lambda *, node, parent, idx: False,
        )
        monkeypatch.setattr(
            executor,
            "_cow_remove_via_path_walk",
            lambda _node: False,
        )

        with pytest.raises(UKCoWAncestorChainLocateFailed) as exc_info:
            executor._remove_node(node_5, parent_5, idx_5)

        # The typed carrier preserves the exact triple that failed — no
        # surrogate, no re-derivation from a lossy representation (§1.11 /
        # §1.12). Identity equality (``is``) is the load-bearing check: a
        # copy or rebuilt surrogate would defeat the audit.
        assert exc_info.value.target is node_5
        assert exc_info.value.parent is parent_5
        assert exc_info.value.idx == idx_5

    def test_replace_node_in_statute_raises_when_both_cow_paths_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The audit in §3 of the brief covers the same unreachable-else tail
        in ``_do_replace_node_in_statute`` — same fail-loud contract, same
        carrier, identical reasoning (the prior ``return False`` was silently
        swallowed by every ``self._replace_node_in_statute(node, rebuilt)``
        call site in replay_text_apply / replay_table_apply / replay_renumber
        _apply / replay_replace_apply)."""
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        node_5, _parent_5, _idx_5 = self._resolve_live_section_five(executor)

        replacement = IRNode(
            kind=node_5.kind,
            label=node_5.label,
            text="Replacement section five text.",
            attrs=dict(node_5.attrs),
        )

        monkeypatch.setattr(
            executor,
            "_cow_replace_in_subtree_preserve_warm_index",
            lambda *, old_node, new_node, parent, idx: False,
        )
        monkeypatch.setattr(
            executor,
            "_cow_replace_in_subtree_via_path_walk",
            lambda _old_node, _new_node: False,
        )

        with pytest.raises(UKCoWAncestorChainLocateFailed) as exc_info:
            executor._do_replace_node_in_statute(node_5, replacement)

        # ``old_node`` is the only identity available at this tail — the
        # warm-index lookup either returned None or its returned parent
        # failed to chain to a root, so the exception carries target only
        # (parent=None, idx=None). The audit witness is the target identity.
        assert exc_info.value.target is node_5
        assert exc_info.value.parent is None
        assert exc_info.value.idx is None

    def test_apply_repeal_op_catches_failure_and_does_not_record_false_repeal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """§2.9 production-lane fire-drill: drive ``executor.apply_op`` with a
        real REPEAL op + monkeypatched Cow paths that force the fail-loud. The
        caller at ``replay_repeal_apply.py:287-303`` MUST:
          (a) catch ``UKCoWAncestorChainLocateFailed``,
          (b) emit a typed ``uk_replay_cow_chain_locate_failed`` adjudication,
          (c) SKIP the false ``_record_repealed_target(target)`` call (over-
              repeal risk, AGENTS.md §0).
        """
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        # Warm the EID index so the production resolve path uses the Cow path
        # (matches what a real replay does after the first lookup).
        executor._ensure_eid_lookup_index()

        monkeypatch.setattr(
            executor,
            "_cow_remove_in_parent_preserve_warm_index",
            lambda *, node, parent, idx: False,
        )
        monkeypatch.setattr(
            executor,
            "_cow_remove_via_path_walk",
            lambda _node: False,
        )

        repeal_op = _repeal_op("5", op_id="uk-rel-cow-fail-drill")
        # The production apply_op path MUST NOT raise: the typed exception
        # MUST be caught at the application layer and routed into a typed
        # adjudication. If this raises, the M3 caller contract is broken.
        executor.apply_op(repeal_op)

        # (a) + (b): a ``uk_replay_cow_chain_locate_failed`` adjudication
        # was emitted, recording the over-repeal-prevention finding.
        cow_chain_adjudications = [
            adj
            for adj in executor.adjudications_out
            if "cow_chain_locate_failed" in str(getattr(adj, "kind", ""))
        ]
        assert cow_chain_adjudications, (
            "apply_op(repeal_op) under a forced CoW-chain failure MUST emit a "
            "uk_replay_cow_chain_locate_failed adjudication; got: "
            f"{[getattr(a, 'kind', '<no kind>') for a in executor.adjudications_out]}"
        )

        # (c): ``_record_repealed_target(target)`` was NOT called for section-5
        # — the over-repeal would have polluted ``_repealed_target_prefixes``
        # and started hiding future ops that target section-5 or its
        # descendants via ``_target_under_repealed_prefix``.
        target_text = str(repeal_op.target or "").strip()
        assert target_text, "repeal op must carry a non-empty target for the test"
        assert target_text not in executor._repealed_target_prefixes, (
            "executor._repealed_target_prefixes leaked a false "
            f"section-5 repeal ({target_text!r}) under the forced CoW-chain "
            "failure — the §0 over-repeal risk fired."
        )

        # Defence-in-depth: the live tree is unchanged. Section-5 is still
        # present in the body's children tuple because the failed remove
        # mutated nothing (CoW-chain rebuild never reached the body root).
        labels_after = [c.label for c in executor.statute.body.children]
        assert labels_after == ["1", "2", "3", "4", "5", "6", "7"], (
            "executor.statute.body.children labels changed under a forced "
            "CoW-chain failure — the fail-loud catch path leaked a tree "
            f"mutation: {labels_after}."
        )


# ---------------------------------------------------------------------------
# iter3 W1 Fix 1 — CoW REMOVE-path one-character bug in
# ``_rekey_node_tree_path_index_after_cow_chain`` (replay_state.py line 1674).
#
# The buggy ``range(1, len(chain))`` skipped ``chain[0]`` — SAFE for the
# REPLACE path (where ``chain[0] = (old_leaf, new_leaf)`` and the leaf's
# path-index entries were already popped upstream by
# ``_remove_eid_lookup_subtree``) but WRONG for the REMOVE path
# (``_cow_remove_in_parent_preserve_warm_index`` at line 1742 builds a chain
# whose ``chain[0] = (old_parent, new_parent)`` is the rebuilt parent of the
# popped leaf). For a top-level REPEAL the parent IS the body root, so:
#   * ``id(old_body)`` stayed in ``_node_tree_path_index`` forever (ghost
#     entry pinned the orphaned body object in memory).
#   * ``id(new_body)`` was NEVER inserted, so subsequent descendant-target
#     ops lost their warm fast-path resolution against the rebuilt body and
#     accumulated ghost entries ~1 per CoW-remove op (monotone across replays).
#
# Fix: ONE CHARACTER — ``range(1, ...)`` -> ``range(0, ...)``. The REPLACE
# path stays safe because ``pop(id(old_leaf))`` returns ``None`` (already
# popped by ``_remove_eid_lookup_subtree``) and the early-``continue`` skips
# harmlessly. The new leaf is then re-added by the caller's
# ``_add_eid_lookup_subtree(new_node, ...)``.
# ---------------------------------------------------------------------------


class TestRekeyNodeTreePathIndexRemovePathChain0:
    """Iter3 W1 Fix 1 regression: pins that ``_rekey_node_tree_path_index_after_cow_chain``
    correctly re-keys ``chain[0]`` for the REMOVE path (where ``chain[0] =
    (old_parent, new_parent)`` is the rebuilt parent of the popped leaf)."""

    def test_repeal_top_section_pops_old_body_id_and_inserts_new(self) -> None:
        """Drive a REPEAL op through the production ``UKReplayExecutor`` path
        against a warm ``_node_tree_path_index`` and assert post-op:
          * ``id(new_body)`` IS in ``_node_tree_path_index`` (rebuilt parent
            was correctly re-keyed with its fresh id).
          * ``id(old_body)`` is NOT in ``_node_tree_path_index`` (stale ghost
            entry was popped by the same iteration).
          * Subsequent descendant-target ops still hit the warm path-index
            fast-path against the rebuilt body (the lookup returns the cached
            path rather than None).
        With the prior ``range(1, len(chain))`` skip, ``id(old_body)`` stayed
        forever AND ``id(new_body)`` was never inserted — descendants survived
        only because their own ``id()``s were untouched, but lookups against
        the rebuilt body itself returned None (lost fast-path)."""
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        old_body = executor.statute.body
        # Warm the path index BEFORE the CoW-remove so the production path
        # executes ``_remove_node_tree_path_subtree`` + the
        # ``_rekey_node_tree_path_index_after_cow_chain`` chain rather than
        # returning early on the cold path (``_node_tree_path_index is None``).
        executor._ensure_node_tree_path_index()
        assert id(old_body) in executor._node_tree_path_index, (
            "Sanity check failed: pre-repeal body id must be in the warm path "
            "index — otherwise the regression is masked (cold path returns "
            "early in _rekey_node_tree_path_index_after_cow_chain)."
        )
        pre_op_entry_count = len(executor._node_tree_path_index)

        executor.apply_op(_repeal_op("5", op_id="uk-cow-remove-path-1"))

        new_body = executor.statute.body
        assert new_body is not old_body, (
            "Sanity check failed: REPEAL §5 did NOT CoW-rebuild the body root "
            "(bug is masked — chain[0] for the REMOVE path is the rebuilt body)."
        )
        # Fix invariant #1: the rebuilt parent's id IS indexed (chain[0] re-keyed).
        assert id(new_body) in executor._node_tree_path_index, (
            "REMOVE-path CoW re-key did NOT insert id(new_body) — stale ghost "
            "entry leaves subsequent descendant ops unable to hit the warm "
            "path-index fast-path for the rebuilt body. Iter3 W1 Fix 1 regression."
        )
        # Fix invariant #2: the old body's id was popped (no ghost accumulation).
        assert id(old_body) not in executor._node_tree_path_index, (
            "REMOVE-path CoW re-key left id(old_body) in the warm path index — "
            "stale ghost entry would accumulate (~1 per CoW-remove op, monotone "
            "across replays) and pin the old body in memory. "
            "Iter3 W1 Fix 1 regression."
        )
        # Fix invariant #3: subsequent descendant-target ops still hit the warm
        # fast-path. Looking up the rebuilt body's cached path returns ``()``
        # (root) — with the bug, this returned None because id(new_body) was
        # never inserted (fell back to ``_tree_path_for_mutable_node``'s
        # ``self.statute.body is node`` early-return, masking the warm-path loss
        # for the body itself but not for ops needing parent_path lookup).
        cached_body_path = executor._cached_node_tree_path_if_indexed(new_body)
        assert cached_body_path == (), (
            "Warm path-index fast-path returned "
            f"{cached_body_path!r} for the rebuilt body — expected () (root). "
            "Descendant ops that thread the body's cached path fall back to "
            "the slow path-walk + wholesale-drop lazy-rebuild. "
            "Iter3 W1 Fix 1 regression."
        )
        # Net entry-count delta = -1 (section-5's entry popped by
        # ``_remove_node_tree_path_subtree``; body's id re-keyed, not added —
        # zero net change for the body itself, only the repealed section
        # accounts for the count drop).
        post_op_entry_count = len(executor._node_tree_path_index)
        assert post_op_entry_count == pre_op_entry_count - 1, (
            f"Entry count changed by {post_op_entry_count - pre_op_entry_count} "
            "— expected -1 (section-5 popped; body re-keyed, not added). A "
            "delta of 0 indicates the bug: id(old_body) was never popped AND "
            "id(new_body) was never inserted (both stay or both missing from "
            "the count)."
        )

    def test_chained_repeal_replace_does_not_accumulate_ghost_entries(self) -> None:
        """§2.9 no-leak: drive a REMOVE + REPLACE sequence through the
        production path and assert the warm path index never accumulates stale
        body-id entries. With the prior ``range(1, ...)`` skip:
          * REPEAL §5 leaves ``id(body_v1)`` ghost + ``id(body_v2)`` absent.
          * REPLACE §4 then re-builds body_v2 → body_v3 with chain[1] =
            (body_v2, body_v3); ``pop(id(body_v2))`` returns None (never
            inserted), so the loop ``continue``s and ``id(body_v3)`` is also
            never inserted — ghosts stack one per CoW op, monotonically.
        With the fix, every rebuilt body id is correctly re-keyed, so after
        N ops the index carries exactly ONE body entry (the live ``id(body_vN)``).
        """
        executor = UKReplayExecutor(_multi_section_base_with_eids())
        body_v0 = executor.statute.body
        executor._ensure_node_tree_path_index()
        body_ids_seen: set[int] = {id(body_v0)}

        executor.apply_op(_repeal_op("5", op_id="uk-cow-remove-path-2a"))
        body_v1 = executor.statute.body
        body_ids_seen.add(id(body_v1))
        # After REPEAL §5 the rebuilt body's id MUST be in the index now
        # (not left as a future ghost after the next op).
        assert id(body_v1) in executor._node_tree_path_index, (
            "After REPEAL §5 the rebuilt body's id is NOT in the warm path "
            "index — the second op's chain re-key will be a no-op against "
            "the missing entry, accumulating the bug."
        )

        # Second op: REPLACE §4 forces a fresh CoW-rebuild of body_v1 → body_v2.
        executor.apply_op(_replace_op_with_eid("4", "Replaced four.", op_id="uk-cow-remove-path-2b"))
        body_v2 = executor.statute.body
        body_ids_seen.add(id(body_v2))

        # Final invariant: exactly ONE body-id entry is in the index (the live
        # body_v2); every prior body-id has been popped, never stacked.
        live_body_ids_in_index = {
            node_id
            for node_id, (node, _path) in (executor._node_tree_path_index or {}).items()
            if node is body_v2
        }
        assert live_body_ids_in_index == {id(body_v2)}, (
            f"Expected exactly one live body entry keyed by id(body_v2); "
            f"found {live_body_ids_in_index!r}. Prior body ids in index: "
            f"{body_ids_seen - {id(body_v2)}} (ghost stack SHOULD be empty)."
        )
        # Defence-in-depth: every prior body-id is gone (no ghost accumulation).
        prior_body_ids = body_ids_seen - {id(body_v2)}
        for prior_id in prior_body_ids:
            assert prior_id not in executor._node_tree_path_index, (
                f"Ghost entry survived: id of a prior body root ({prior_id}) is "
                "still in the warm path index — monotone accumulation across "
                "CoW-remove ops (Iter3 W1 Fix 1 regression)."
            )
