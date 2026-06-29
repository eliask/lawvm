"""Tests for the typed LegalOperation action contract."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation, ProvisionVersion, ScopePredicate
from lawvm.core.provenance import compute_source_anchor
from lawvm.core.semantic_types import FacetKind, StructuralAction
from lawvm.core.canonical_intent import FacetTarget, NodeTarget


def _make_op(action: StructuralAction) -> LegalOperation:
    """Minimal LegalOperation factory for adapter testing."""
    addr = LegalAddress(path=(("section", "1"),))
    return LegalOperation(
        op_id="test-op",
        sequence=1,
        action=action,
        target=addr,
    )


class TestLegalOperationTypedAction:
    @pytest.mark.parametrize(
        ("action", "expected"),
        [
            (StructuralAction.REPLACE, StructuralAction.REPLACE),
            (StructuralAction.REPEAL, StructuralAction.REPEAL),
            (StructuralAction.INSERT, StructuralAction.INSERT),
            (StructuralAction.RENUMBER, StructuralAction.RENUMBER),
        ],
    )
    def test_structural_actions_stay_enums(
        self, action: StructuralAction, expected: StructuralAction
    ) -> None:
        op = _make_op(action)
        assert op.action is expected
        assert op.action == action

    def test_action_field_is_enum(self) -> None:
        op = _make_op(StructuralAction.REPEAL)
        assert op.action == StructuralAction.REPEAL
        assert isinstance(op.action, StructuralAction)

    def test_renumber_with_destination(self) -> None:
        src = LegalAddress(path=(("section", "3"),))
        dst = LegalAddress(path=(("section", "4"),))
        op = LegalOperation(
            op_id="rn-1",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=src,
            destination=dst,
        )
        assert op.action is StructuralAction.RENUMBER

    def test_raw_string_action_is_rejected(self) -> None:
        with pytest.raises(TypeError, match="must be StructuralAction"):
            LegalOperation(
                op_id="bad-1",
                sequence=1,
                action=cast(StructuralAction, "replace"),
                target=_make_op(StructuralAction.REPLACE).target,
            )

    def test_legal_operation_is_frozen(self) -> None:
        op = _make_op(StructuralAction.REPLACE)
        with pytest.raises((FrozenInstanceError, TypeError)):
            cast(Any, op).notes = ["mutated"]


class TestCanonicalIntentEnumNormalization:
    def test_node_target_uses_address_leaf_kind(self) -> None:
        addr = LegalAddress(path=(("section", "1"),))
        target = NodeTarget(address=addr)
        assert target.address is addr
        assert target.address.leaf_kind() == "section"

    def test_facet_target_accepts_facet_kind_enum(self) -> None:
        addr = LegalAddress(path=(("section", "1"),))
        target = FacetTarget(host=addr, facet=FacetKind.HEADING)
        assert target.facet is FacetKind.HEADING


class TestLegalOperationRawTextSourceAnchorFoundation:
    """Foundation tests for the per-op ``raw_text`` field (task #50 Option C).

    The field is the lightest source-anchor seam: each op carries the verbatim
    source-clause substring that produced it, distinct from the amendment-level
    ``OperationSource.raw_text`` and from the byte-span
    ``OperationSource.source_anchor``. Downstream threading into
    ``OperationSource.source_anchor`` happens at the frontend compile loop
    (out of scope for this task window); these tests pin the typed waist the
    field establishes.
    """

    def test_raw_text_defaults_to_empty_string(self) -> None:
        op = _make_op(StructuralAction.REPLACE)
        assert op.raw_text == ""
        assert isinstance(op.raw_text, str)

    def test_raw_text_round_trips_verbatim(self) -> None:
        op = LegalOperation(
            op_id="op-with-raw",
            sequence=7,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"),)),
            raw_text="sana \"A\" korvataan sanalla \"B\"",
        )
        assert op.raw_text == "sana \"A\" korvataan sanalla \"B\""

    def test_non_string_raw_text_is_rejected_fail_loud(self) -> None:
        # §1.10 fail-loud: a non-string raw_text at the semantic waist is a
        # producer bug, not a silent default. Reject with a named TypeError
        # rather than coercing/guessing.
        with pytest.raises(TypeError, match="raw_text must be a str"):
            LegalOperation(
                op_id="bad-raw",
                sequence=1,
                action=StructuralAction.REPLACE,
                target=LegalAddress(path=(("section", "1"),)),
                raw_text=cast(str, 123),  # type: ignore[arg-type]
            )

    def test_raw_text_is_frozen_after_construction(self) -> None:
        op = LegalOperation(
            op_id="frozen-raw",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            raw_text="verbatim clause text",
        )
        with pytest.raises((FrozenInstanceError, TypeError)):
            cast(Any, op).raw_text = "mutated"

    def test_distinct_per_op_raw_text_yields_distinct_per_op_anchors(self) -> None:
        """Two ops with distinct ``raw_text`` produce distinct per-op SourceAnchors.

        Demonstrates the end-to-end per-op anchor pattern is feasible at the
        typed-waist level (AGENTS.md §0 promotion chain): each op's verbatim
        ``raw_text`` is the ``clause_text`` input to ``compute_source_anchor``,
        which locates it verbatim in the raw amendment bytes and emits a
        distinct ``SourceAnchor`` per clause. Receipts then stamp a per-op
        anchor instead of the amendment-level span.
        """
        # Same raw amendment bytes carry BOTH verbatim clause substrings; each
        # appears exactly once so compute_source_anchor's uniqueness check
        # passes (§1.10 fail-loud: ambiguous => None, not guessed).
        raw_bytes = (
            b"<amendment>"
            b"<clauses>"
            b"<c>sana \"lupaviranomainen\" korvataan sanalla \"Lupa- ja valvontavirasto\"</c>"
            b"<c>sanat \"vanha nimi\" korvataan sanoilla \"uusi nimi\"</c>"
            b"</clauses>"
            b"</amendment>"
        )
        anchor_a = compute_source_anchor(
            source_artifact_id="2025/572",
            raw_bytes=raw_bytes,
            clause_text='sana "lupaviranomainen" korvataan sanalla "Lupa- ja valvontavirasto"',
        )
        anchor_b = compute_source_anchor(
            source_artifact_id="2025/572",
            raw_bytes=raw_bytes,
            clause_text='sanat "vanha nimi" korvataan sanoilla "uusi nimi"',
        )
        assert anchor_a is not None
        assert anchor_b is not None
        assert anchor_a != anchor_b
        # The anchor's byte span points at the SPECIFIC clause, not the whole
        # amendment envelope (the whole-amendment anchor would still be byte
        # 0..N — here each is a sub-span).
        assert anchor_a.byte_offset != anchor_b.byte_offset
        assert anchor_a.byte_len != anchor_b.byte_len
        # And each verbatim quote-hash differs (proof the source bytes differ).
        assert anchor_a.quote_hash != anchor_b.quote_hash


class TestProvisionVersionApplicabilitySequence:
    """D6 regression: ``applicability`` is annotated ``Sequence[ScopePredicate]``
    and runtime-coerced to ``tuple`` (the ``ProvisionTimeline.versions`` precedent
    from iter2 H5). Tests both the type-widening (list literals still accepted)
    and the runtime immutability guarantee (§1.9 immutable carriers).
    """

    def _predicate(self) -> ScopePredicate:
        return ScopePredicate(dimension="territory", includes=frozenset({"finland"}))

    def test_applicability_accepts_list_literal(self) -> None:
        pv = ProvisionVersion(
            effective="2025-01-01",
            applicability=[self._predicate()],
        )
        assert isinstance(pv.applicability, tuple)
        assert len(pv.applicability) == 1
        assert pv.applicability[0].dimension == "territory"

    def test_applicability_accepts_tuple_literal(self) -> None:
        pv = ProvisionVersion(
            effective="2025-01-01",
            applicability=(self._predicate(),),
        )
        assert isinstance(pv.applicability, tuple)
        assert pv.applicability[0].dimension == "territory"

    def test_applicability_defaults_to_empty_tuple(self) -> None:
        pv = ProvisionVersion(effective="2025-01-01")
        assert pv.applicability == ()
        assert isinstance(pv.applicability, tuple)

    def test_applicability_is_immutable_after_construction(self) -> None:
        pv = ProvisionVersion(
            effective="2025-01-01",
            applicability=[self._predicate()],
        )
        # No append/sort/pop on the stored value — tuple raises AttributeError.
        # The ty-suppression comment is needed because Sequence's static type
        # has no `append`, even though the test asserts exactly that runtime
        # AttributeError: the comment proves the type system itself forbids
        # mutation on the declared Sequence type (§1.9 immutable carriers).
        with pytest.raises(AttributeError):
            pv.applicability.append(self._predicate())  # ty: ignore[unresolved-attribute]
