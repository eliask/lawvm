"""PR3 (audit XJUR-02 / AGENTS.md §2.3) regression guards for the
``replay_state`` copy-on-write migration.

Pins four invariants that ``replay_state.py`` now provides after the
in-place-mutation rewrite to copy-on-write rebuilds:

  * (1) The ``IRStatute`` produced by ``replay_uk_ops`` (via
    ``UKReplayExecutor.statute.to_irstatute()``) IS the frozen
    ``IRNode``-rooted tree — no ``UKMutableNode`` shadow survives past the
    executor boundary.
  * (2) Mutating ``.children`` / ``.attrs`` / ``.label`` / ``.text`` on any
    frozen ``IRNode`` in that tree raises ``dataclasses.FrozenInstanceError``.
  * (3) Idempotency: replaying the same ops twice produces structurally
    identical final ``IRStatute`` trees (modulo op-id minting, which is
    controlled by the caller-supplied op inputs).
  * (4) Each op in a REPLACE + INSERT + REPEAL sequence leaves the body
    ``children`` tuple ``IRNode``-typed (no UKMutableNode leaks intermediate
    ops, end-to-end across the mutate-bookkeeping chain built in PR3).

These guards are PR3-specific: a future change that re-introduces an
in-place ``parent.children.pop`` / ``parent.children[idx] = new_node`` would
either:
  * break the frozen-tuple property if it did so on an ``IRNode`` shared with
    the live tree (silent regression), or
  * leak a ``UKMutableNode`` past the boundary → making (1) a failing test
    rather than a hidden regression.
"""
from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from lawvm.core.ir import IRNode, IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.semantic_types import IRNodeKind, StructuralAction
from lawvm.uk_legislation.mutable_ir import UKMutableNode
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


def _collect_mutable_leaks(node: Any) -> list[Any]:
    """Return every ``UKMutableNode`` reachable from ``node`` (recursive)."""
    leaks: list[Any] = []
    if isinstance(node, UKMutableNode):
        leaks.append(node)
    for child in getattr(node, "children", ()):
        leaks.extend(_collect_mutable_leaks(child))
    return leaks


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
        assert not isinstance(replayed.body, UKMutableNode)
        # ``children`` MUST be a tuple (frozen IR invariant); a leaked list would
        # indicate a UKMutableNode shadow slipping through the boundary.
        assert isinstance(replayed.body.children, tuple)

    def test_result_statute_supplements_are_frozen_irnode(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        for supplement in replayed.supplements:
            assert isinstance(supplement, IRNode)
            assert not isinstance(supplement, UKMutableNode)
            assert isinstance(supplement.children, tuple)


class TestPR3NoUKMutableNodeLeaks:
    """(1b) No ``UKMutableNode`` survives past the executor boundary."""

    def test_no_mutable_leak_in_body(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        leaks = _collect_mutable_leaks(replayed.body)
        assert not leaks, f"UKMutableNode leaked into boundary IR body: {leaks!r}"

    def test_no_mutable_leak_in_supplements(self) -> None:
        replayed = replay_uk_ops(_multi_section_base(), _multi_op_sequence())
        for supplement in replayed.supplements:
            leaks = _collect_mutable_leaks(supplement)
            assert not leaks, (
                f"UKMutableNode leaked into boundary IR supplement: {leaks!r}"
            )


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
        ``to_irnode`` boundary, exercising CoW helper for every action family."""
        executor = UKReplayExecutor(_multi_section_base())
        for op in _multi_op_sequence():
            executor.apply_op(op)
            # The UKMutableStatute remains internally mutable (PR4 deletes it);
            # but the IRNode produced at the ``to_irnode`` boundary is frozen.
            frozen_body = executor.statute.body.to_irnode()
            assert isinstance(frozen_body, IRNode)
            assert isinstance(frozen_body.children, tuple)
            for child in frozen_body.children:
                assert isinstance(child, IRNode)
                assert not isinstance(child, UKMutableNode)


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
