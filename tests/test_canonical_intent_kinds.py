from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.canonical_intent import (
    ExecutionContract,
    FacetTarget,
    Insert,
    InsertOrder,
    IntentKind,
    Move,
    NodeTarget,
    OccupancyPolicy,
    Repeal,
    Relabel,
    Replace,
)
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind, StructureKind


def _contract() -> ExecutionContract:
    return ExecutionContract(occupancy=OccupancyPolicy.same_slot_replace())


def test_replace_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="Replace requires kind='replace'"):
        Replace(
            kind=cast(Any, IntentKind.INSERT),
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, object()),
            contract=_contract(),
        )


def test_insert_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="Insert requires kind='insert'"):
        Insert(
            kind=cast(Any, IntentKind.REPEAL),
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, object()),
            contract=ExecutionContract(
                occupancy=OccupancyPolicy.fresh_insert(),
                insert_order=InsertOrder.SORTED_FAMILY,
            ),
        )


def test_repeal_accepts_matching_kind() -> None:
    intent = Repeal(
        kind=IntentKind.REPEAL,
        target=NodeTarget(LegalAddress(path=(("section", "1"),))),
        contract=_contract(),
    )

    assert intent.kind == IntentKind.REPEAL


def test_move_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="Move requires kind='move'"):
        Move(
            kind=cast(Any, IntentKind.RELABEL),
            source=NodeTarget(LegalAddress(path=(("section", "1"),))),
            destination_parent=LegalAddress(path=(("chapter", "2"),)),
            contract=_contract(),
        )


def test_replace_rejects_missing_payload() -> None:
    with pytest.raises(ValueError, match="Replace requires non-None payload"):
        Replace(
            kind=IntentKind.REPLACE,
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, None),
            contract=_contract(),
        )


def test_insert_requires_insert_order() -> None:
    with pytest.raises(ValueError, match="Insert requires contract.insert_order"):
        Insert(
            kind=IntentKind.INSERT,
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, object()),
            contract=ExecutionContract(
                occupancy=OccupancyPolicy.fresh_insert(),
            ),
        )


def test_anchored_insert_requires_anchor() -> None:
    with pytest.raises(ValueError, match="Anchored Insert requires anchor"):
        Insert(
            kind=IntentKind.INSERT,
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, object()),
            contract=ExecutionContract(
                occupancy=OccupancyPolicy.fresh_insert(),
                insert_order=InsertOrder.BEFORE_ANCHOR,
            ),
        )


def test_replace_rejects_insert_contract_fields() -> None:
    with pytest.raises(ValueError, match="Replace cannot carry insert-order"):
        Replace(
            kind=IntentKind.REPLACE,
            target=NodeTarget(LegalAddress(path=(("section", "1"),))),
            payload=cast(Any, object()),
            contract=ExecutionContract(
                occupancy=OccupancyPolicy.same_slot_replace(),
                insert_order=InsertOrder.SORTED_FAMILY,
            ),
        )


def test_relabel_rejects_cross_parent_path() -> None:
    with pytest.raises(ValueError, match="Relabel source and destination must share the same parent path"):
        Relabel(
            kind=IntentKind.RELABEL,
            source=NodeTarget(LegalAddress(path=(("section", "1"), ("subsection", "2")))),
            destination=NodeTarget(LegalAddress(path=(("chapter", "2"), ("subsection", "2")))),
            contract=_contract(),
        )


def test_relabel_accepts_same_parent_path() -> None:
    intent = Relabel(
        kind=IntentKind.RELABEL,
        source=NodeTarget(LegalAddress(path=(("section", "1"), ("subsection", "2")))),
        destination=NodeTarget(LegalAddress(path=(("section", "1"), ("subsection", "3")))),
        contract=_contract(),
    )

    assert intent.source.address.parent() == intent.destination.address.parent()


def test_node_target_uses_address_authority() -> None:
    target = NodeTarget(LegalAddress(path=(("section", "1"), ("subsection", "2"))))

    assert target.address.leaf_kind() == "subsection"


def test_node_target_accepts_structure_kind_address_leaf() -> None:
    target = NodeTarget(LegalAddress(path=(("section", "1"),)))

    assert target.address.leaf_kind() == StructureKind.SECTION.value


def test_node_target_rejects_facet_special_address() -> None:
    with pytest.raises(ValueError, match="NodeTarget requires a structural address without facet special"):
        NodeTarget(LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING))


def test_facet_target_rejects_host_with_special() -> None:
    with pytest.raises(ValueError, match="FacetTarget.host must be a structural host address without facet special"):
        FacetTarget(
            host=LegalAddress(path=(("section", "1"),), special=FacetKind.HEADING),
            facet=FacetKind.HEADING,
        )


def test_facet_target_rejects_none_and_whole_act() -> None:
    with pytest.raises(ValueError, match="FacetTarget requires a concrete node facet"):
        FacetTarget(
            host=LegalAddress(path=(("section", "1"),)),
            facet=FacetKind.NONE,
        )

    with pytest.raises(ValueError, match="FacetTarget requires a concrete node facet"):
        FacetTarget(
            host=LegalAddress(path=(("section", "1"),)),
            facet=FacetKind.WHOLE_ACT,
        )


def test_move_rejects_same_parent_destination() -> None:
    with pytest.raises(ValueError, match="Move requires a destination_parent distinct from the source parent"):
        Move(
            kind=IntentKind.MOVE,
            source=NodeTarget(LegalAddress(path=(("section", "1"), ("subsection", "2")))),
            destination_parent=LegalAddress(path=(("section", "1"),)),
            contract=_contract(),
        )
