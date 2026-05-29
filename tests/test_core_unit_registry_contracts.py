from __future__ import annotations

from typing import Any, cast

import pytest

from lawvm.core.canonical_intent import FacetTarget, NodeTarget, TextTarget
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.core.unit_registry import (
    IntentTargetValidationError,
    UnitRegistry,
    UnitSpec,
    validate_intent_target,
)


def _addr(*path_pairs: tuple[str, str], special: FacetKind | None = None) -> LegalAddress:
    return LegalAddress(path=tuple(path_pairs), special=special)


def _registry() -> UnitRegistry:
    return UnitRegistry(
        unit_specs={
            "section": UnitSpec("section", "Section", can_have_heading=True, can_have_intro=True),
            "subsection": UnitSpec("subsection", "Subsection", can_have_intro=True),
            "item": UnitSpec("item", "Item"),
        },
        valid_facets=frozenset({"heading", "intro"}),
        jurisdiction="TEST",
    )


def test_validate_intent_target_accepts_valid_node_target() -> None:
    target = NodeTarget(address=_addr(("section", "5")))
    assert validate_intent_target(target, _registry()) is None


def test_validate_intent_target_rejects_unknown_unit_kind() -> None:
    target = NodeTarget(address=_addr(("widget", "5")))
    with pytest.raises(IntentTargetValidationError, match="Unknown unit_kind"):
        validate_intent_target(target, _registry())


def test_validate_intent_target_rejects_incompatible_facet_host() -> None:
    target = FacetTarget(
        host=_addr(("section", "5"), ("subsection", "1"), ("item", "a")),
        facet=FacetKind.HEADING,
    )
    with pytest.raises(IntentTargetValidationError, match="not valid for host unit_kind"):
        validate_intent_target(target, _registry())


def test_validate_intent_target_accepts_text_target_without_registry_shape() -> None:
    selector = cast(Any, type("_Selector", (), {"match_text": "needle", "occurrence": 1})())
    target = TextTarget(host=_addr(("section", "5")), selector=selector)
    assert validate_intent_target(target, _registry()) is None


def test_validate_intent_target_rejects_unknown_facet() -> None:
    target = FacetTarget(host=_addr(("section", "5")), facet=FacetKind.TABLE)
    with pytest.raises(IntentTargetValidationError, match="Unknown facet"):
        validate_intent_target(target, _registry())


def test_unit_registry_normalizes_specs_and_facets() -> None:
    specs = {"section": UnitSpec("section", "Section")}
    facets = ["heading"]
    registry = UnitRegistry(
        unit_specs=specs,
        valid_facets=cast(Any, facets),
        jurisdiction="TEST",
    )

    specs["subsection"] = UnitSpec("subsection", "Subsection")
    facets.append("intro")

    assert registry.is_valid_unit_kind("section")
    assert not registry.is_valid_unit_kind("subsection")
    assert registry.valid_facets == frozenset({"heading"})
    with pytest.raises(TypeError):
        cast(Any, registry.unit_specs)["item"] = UnitSpec("item", "Item")


def test_unit_spec_rejects_invalid_identity_values() -> None:
    with pytest.raises(ValueError, match="identity_class"):
        UnitSpec(
            unit_kind="section",
            display_name="Section",
            identity_class=cast(Any, "address_order"),
        )


def test_unit_registry_rejects_mismatched_spec_key() -> None:
    with pytest.raises(ValueError, match="keys must match"):
        UnitRegistry(unit_specs={"section": UnitSpec("item", "Item")})
