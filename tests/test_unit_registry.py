"""Tests for UnitRegistry validation and its wiring into _build_canonical_intent.

Covers:
  - UnitRegistry.is_valid_unit_kind: known and unknown unit kinds
  - UnitRegistry.is_valid_facet: known and unknown facets
  - validate_intent_target with NodeTarget: valid passes silently
  - validate_intent_target with NodeTarget: unknown unit_kind raises
  - validate_intent_target with FacetTarget: valid passes silently
  - validate_intent_target with FacetTarget: unknown facet raises
  - validate_intent_target with TextTarget: always passes (no unit_kind/facet)
  - Finland's frontend registry contains all standard Finnish unit kinds
  - Wiring: _build_canonical_intent calls validate_intent_target (via log capture)

Run:
    uv run python -m pytest tests/test_unit_registry.py -v --override-ini="addopts="
"""

from __future__ import annotations
from lawvm.core.ir import LegalAddress

import logging
from typing import Any, Optional, cast

import pytest

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import FacetKind, IRNodeKind


from lawvm.core.canonical_intent import FacetTarget, NodeTarget, Relabel, TextTarget
from lawvm.core.unit_registry import (
    IntentTargetValidationError,
    UnitRegistry,
    UnitSpec,
    validate_intent_target,
)
from lawvm.finland.ops import OpType, TargetKind
from lawvm.finland.unit_registry import FINLAND_REGISTRY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(*path_pairs: tuple[str, str], special: Optional[FacetKind] = None) -> LegalAddress:
    """Build a LegalAddress from (kind, label) pairs."""
    return LegalAddress(path=tuple(path_pairs), special=special)


def _section_addr(label: str = "5") -> LegalAddress:
    return _addr(("section", label))


def _node(unit_kind: str, label: str = "5") -> NodeTarget:
    return NodeTarget(address=_addr((unit_kind, label)))


def _facet(facet: FacetKind, host_label: str = "5") -> FacetTarget:
    return FacetTarget(host=_section_addr(host_label), facet=facet)


# ---------------------------------------------------------------------------
# UnitRegistry.is_valid_unit_kind
# ---------------------------------------------------------------------------


def test_registry_valid_unit_kinds():
    """Finland's frontend registry accepts all standard Finnish unit kinds."""
    standard_kinds = [
        "part",
        "chapter",
        "crossheading",
        "section",
        "subsection",
        "item",
        "subitem",
        "row",
        "annex",
    ]
    for kind in standard_kinds:
        assert FINLAND_REGISTRY.is_valid_unit_kind(kind), f"Expected {kind!r} to be valid in FINLAND_REGISTRY"


def test_registry_unknown_unit_kind():
    """Finland's frontend registry rejects an unknown unit_kind."""
    assert not FINLAND_REGISTRY.is_valid_unit_kind("widget")
    assert not FINLAND_REGISTRY.is_valid_unit_kind("")
    assert not FINLAND_REGISTRY.is_valid_unit_kind("Pykälä")  # wrong case


# ---------------------------------------------------------------------------
# UnitRegistry.is_valid_facet
# ---------------------------------------------------------------------------


def test_registry_valid_facets():
    """FINLAND_REGISTRY accepts the typed standard facet values."""
    for facet in (FacetKind.HEADING, FacetKind.INTRO):
        assert FINLAND_REGISTRY.is_valid_facet(facet), f"Expected {facet!r} to be a valid facet in FINLAND_REGISTRY"


def test_registry_unknown_facet():
    """FINLAND_REGISTRY rejects unknown typed facet values."""
    assert not FINLAND_REGISTRY.is_valid_facet(FacetKind.TABLE)
    assert not FINLAND_REGISTRY.is_valid_facet(FacetKind.BODY)
    assert not FINLAND_REGISTRY.is_valid_facet(FacetKind.EDITORIAL_NOTICE)


# ---------------------------------------------------------------------------
# validate_intent_target: NodeTarget
# ---------------------------------------------------------------------------


def test_validate_node_target_valid_returns_true():
    """Valid NodeTarget passes validation."""
    target = _node("section")
    assert validate_intent_target(target, FINLAND_REGISTRY) is None


def test_validate_node_target_all_standard_kinds():
    """All standard Finnish unit kinds pass NodeTarget validation."""
    for kind in [
        "part",
        "chapter",
        "crossheading",
        "section",
        "subsection",
        "item",
        "subitem",
        "row",
        "annex",
    ]:
        target = NodeTarget(address=_addr((kind, "5")))
        assert validate_intent_target(target, FINLAND_REGISTRY) is None, f"Expected {kind!r} to pass validation"


def test_validate_node_target_uses_address_leaf_kind_as_unit_kind():
    """Validation uses the target address leaf kind string as authority."""
    target = NodeTarget(address=_section_addr("5"))
    assert validate_intent_target(target, FINLAND_REGISTRY) is None
    assert target.address.leaf_kind() == "section"


def test_validate_node_target_unknown_unit_kind_raises():
    """Unknown unit_kind in NodeTarget raises an explicit validation error."""
    target = _node("widget")
    with pytest.raises(IntentTargetValidationError, match="widget"):
        validate_intent_target(target, FINLAND_REGISTRY)


# ---------------------------------------------------------------------------
# validate_intent_target: FacetTarget
# ---------------------------------------------------------------------------


def test_validate_facet_target_valid_heading_returns_true():
    """Valid FacetTarget with 'heading' passes validation."""
    target = _facet(FacetKind.HEADING)
    assert validate_intent_target(target, FINLAND_REGISTRY) is None


def test_validate_facet_target_valid_intro_returns_true():
    """Valid FacetTarget with 'intro' passes validation."""
    target = _facet(FacetKind.INTRO)
    assert validate_intent_target(target, FINLAND_REGISTRY) is None


def test_validate_facet_target_incompatible_host_raises():
    """Known facets must still respect the host unit's facet capabilities."""
    target = FacetTarget(
        host=_addr(("section", "5"), ("subsection", "1"), ("item", "a")),
        facet=FacetKind.HEADING,
    )
    with pytest.raises(IntentTargetValidationError, match="not valid for host unit_kind"):
        validate_intent_target(target, FINLAND_REGISTRY)


def test_validate_facet_target_reads_leaf_kind_from_nested_host() -> None:
    """Facet validation should follow the host's canonical leaf kind."""
    target = FacetTarget(
        host=_addr(("section", "5"), ("subsection", "1"), ("item", "a")),
        facet=FacetKind.INTRO,
    )
    assert validate_intent_target(target, FINLAND_REGISTRY) is None


# ---------------------------------------------------------------------------
# validate_intent_target: TextTarget (no unit_kind / facet — always passes)
# ---------------------------------------------------------------------------


def test_validate_text_target_always_passes():
    """TextTarget carries no unit_kind or facet — validation always passes."""
    selector = cast(Any, type("_Selector", (), {"match_text": "needle", "occurrence": 1})())
    target = TextTarget(host=_section_addr(), selector=selector)
    assert validate_intent_target(target, FINLAND_REGISTRY) is None


# ---------------------------------------------------------------------------
# Custom UnitRegistry
# ---------------------------------------------------------------------------


def test_custom_registry_validation():
    """Custom UnitRegistry with a minimal spec validates correctly."""
    custom_reg = UnitRegistry(
        unit_specs={"article": UnitSpec("article", "Article", can_have_heading=True)},
        valid_facets=frozenset({"heading"}),
    )
    assert FINLAND_REGISTRY.jurisdiction == "FI"
    assert custom_reg.jurisdiction == ""
    assert custom_reg.is_valid_unit_kind("article")
    assert not custom_reg.is_valid_unit_kind("section")
    assert custom_reg.is_valid_facet(FacetKind.HEADING)
    assert not custom_reg.is_valid_facet(FacetKind.INTRO)

    node_target = NodeTarget(address=_addr(("article", "1")))
    assert validate_intent_target(node_target, custom_reg) is None

    unknown_target = NodeTarget(address=_addr(("section", "1")))
    with pytest.raises(IntentTargetValidationError, match="Unknown unit_kind"):
        validate_intent_target(unknown_target, custom_reg)


# ---------------------------------------------------------------------------
# Wiring: _build_canonical_intent calls validate_intent_target
# ---------------------------------------------------------------------------


def _make_minimal_rop(
    op_type: OpType | str = "REPLACE",
    target_kind: TargetKind = TargetKind.SECTION,
    target_norm: str = "5",
    target_special: Optional[str] = None,
    target_paragraph: Optional[int] = None,
    target_item: Optional[str] = None,
):
    """Build a minimal ResolvedOp suitable for _build_canonical_intent."""
    from lawvm.finland.ops import AmendmentOp, ResolvedOp

    path: list[tuple[str, str]] = []
    if target_kind == TargetKind.CHAPTER:
        path.append(("chapter", target_norm))
    elif target_kind == TargetKind.PART:
        path.append(("part", target_norm))
    else:
        path.append(("section", target_norm))
    if target_paragraph is not None:
        path.append(("subsection", str(target_paragraph)))
    if target_item is not None:
        path.append(("item", str(target_item)))

    special = None
    if target_special in {"otsikko", "otsikko_edella"}:
        special = FacetKind.HEADING
    elif target_special == "johd":
        special = FacetKind.INTRO

    op = AmendmentOp(
        op_id="test_op",
        op_type=cast(Any, op_type),
        target_section=target_norm,
        target_kind=cast(Any, target_kind),
        target_special=target_special,
    )
    return ResolvedOp(
        op=op,
        muutos_ir=IRNode(kind=IRNodeKind.SECTION, label=target_norm, text="payload") if op_type == "REPLACE" else None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind=(
            "chapter" if target_kind == TargetKind.CHAPTER else "part" if target_kind == TargetKind.PART else "section"
        ),
        target_norm=target_norm,
        _op_type_seed=op_type,
        _target_special_override=(
            target_special if target_special not in {None, "otsikko", "johd"} else None
        ),
        sec1_body_johto_fallback=op.sec1_body_johto_fallback,
        uncovered_body_recovery=op.uncovered_body_recovery,
        post_repeal_item_shift_label=op.post_repeal_item_shift_label,
        _source_statute_override=op.source_statute,
        _source_issue_date_override=op.source_issue_date,
        _source_title_override=op.source_title,
        _target_address_override=LegalAddress(path=tuple(path), special=special),
    )


def test_build_canonical_intent_valid_replace_no_warning(caplog):
    """_build_canonical_intent for a standard REPLACE emits no validation warning."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(op_type="REPLACE", target_kind=TargetKind.SECTION, target_norm="5")
    with caplog.at_level(logging.WARNING, logger="lawvm.core.unit_registry"):
        intent = _build_canonical_intent(rop)

    assert intent is not None
    # No unit_registry warning should appear for a standard section REPLACE
    registry_warnings = [
        r for r in caplog.records if r.name == "lawvm.core.unit_registry" and r.levelno >= logging.WARNING
    ]
    assert registry_warnings == [], "Unexpected validation warnings for valid REPLACE: " + "\n".join(
        r.getMessage() for r in registry_warnings
    )


def test_build_canonical_intent_payloadless_replace_returns_none() -> None:
    """Payloadless REPLACE ops should degrade explicitly instead of building invalid core intent."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(op_type="REPLACE", target_kind=TargetKind.SECTION, target_norm="5")
    rop.muutos_ir = None

    assert _build_canonical_intent(rop) is None


def test_build_canonical_intent_heading_replace_no_warning(caplog):
    """_build_canonical_intent for a heading REPLACE uses 'heading' facet — no warning."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_norm="5",
        target_special="otsikko",
    )
    with caplog.at_level(logging.WARNING, logger="lawvm.core.unit_registry"):
        intent = _build_canonical_intent(rop)

    assert intent is not None
    registry_warnings = [
        r for r in caplog.records if r.name == "lawvm.core.unit_registry" and r.levelno >= logging.WARNING
    ]
    assert registry_warnings == [], "Unexpected validation warning for heading REPLACE: " + "\n".join(
        r.getMessage() for r in registry_warnings
    )


def test_build_canonical_intent_intro_replace_no_warning(caplog):
    """_build_canonical_intent for an intro (johd) REPLACE uses 'intro' facet — no warning."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_norm="3",
        target_special="johd",
    )
    with caplog.at_level(logging.WARNING, logger="lawvm.core.unit_registry"):
        intent = _build_canonical_intent(rop)

    assert intent is not None
    registry_warnings = [
        r for r in caplog.records if r.name == "lawvm.core.unit_registry" and r.levelno >= logging.WARNING
    ]
    assert registry_warnings == []


def test_build_canonical_intent_subsection_intro_replace_no_warning(caplog):
    """Subsection-level johd targets are valid Finland intro facets, not registry violations."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_norm="13",
        target_paragraph=1,
        target_special="johd",
    )
    with caplog.at_level(logging.WARNING, logger="lawvm.core.unit_registry"):
        intent = _build_canonical_intent(rop)

    assert intent is not None
    registry_warnings = [
        r for r in caplog.records if r.name == "lawvm.core.unit_registry" and r.levelno >= logging.WARNING
    ]
    assert registry_warnings == []


def test_build_canonical_intent_returns_none_does_not_crash(caplog):
    """_build_canonical_intent with an unknown op_type returns None without crashing."""
    from lawvm.finland.ops import _build_canonical_intent

    rop = _make_minimal_rop(op_type="UNKNOWN_FUTURE_OP", target_kind=TargetKind.SECTION, target_norm="5")
    # Should not raise; graceful degradation returns None
    intent = _build_canonical_intent(rop)
    assert intent is None


def test_build_canonical_intent_uses_resolvedop_addresses_without_lo() -> None:
    """ResolvedOp should be able to lower intent without reaching through AmendmentOp.lo."""
    from lawvm.finland.ops import AmendmentOp, ResolvedOp, _build_canonical_intent

    op = AmendmentOp(
        op_id="renumber_without_lo",
        op_type="RENUMBER",
        target_kind=TargetKind.SECTION,
        target_section="73",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="73",
        _op_type_seed="RENUMBER",
        _target_special_override=(
            op.target_special if op.target_special not in {None, "otsikko", "johd"} else None
        ),
        sec1_body_johto_fallback=op.sec1_body_johto_fallback,
        uncovered_body_recovery=op.uncovered_body_recovery,
        post_repeal_item_shift_label=op.post_repeal_item_shift_label,
        _source_statute_override=op.source_statute,
        _source_issue_date_override=op.source_issue_date,
        _source_title_override=op.source_title,
        _target_address_override=LegalAddress(path=(("chapter", "7"), ("section", "73"))),
        _destination_address_override=LegalAddress(path=(("chapter", "7"), ("section", "61"))),
    )

    intent = _build_canonical_intent(rop)

    assert intent is not None
    assert isinstance(intent, Relabel)
    assert intent.source.address.path == (("chapter", "7"), ("section", "73"))
    assert intent.destination.address.path == (("chapter", "7"), ("section", "61"))


def test_build_canonical_intent_without_target_address_gracefully_returns_none() -> None:
    """Target identity now belongs on ResolvedOp, not legacy field reconstruction."""
    from lawvm.finland.ops import AmendmentOp, ResolvedOp, _build_canonical_intent

    op = AmendmentOp(
        op_id="missing_target_address",
        op_type="REPLACE",
        target_kind=TargetKind.SECTION,
        target_section="5",
    )
    rop = ResolvedOp(
        op=op,
        muutos_ir=None,
        cross_ir=None,
        amend_sub_ir=None,
        op_id=op.op_id,
        target_unit_kind="section",
        target_norm="5",
        _op_type_seed="REPLACE",
        _target_special_override=(
            op.target_special if op.target_special not in {None, "otsikko", "johd"} else None
        ),
        sec1_body_johto_fallback=op.sec1_body_johto_fallback,
        uncovered_body_recovery=op.uncovered_body_recovery,
        post_repeal_item_shift_label=op.post_repeal_item_shift_label,
        _source_statute_override=op.source_statute,
        _source_issue_date_override=op.source_issue_date,
        _source_title_override=op.source_title,
    )

    assert _build_canonical_intent(rop) is None


# ---------------------------------------------------------------------------
# Identity policy: UnitSpec fields
# ---------------------------------------------------------------------------

_STABLE_LABEL_KINDS = ["part", "chapter", "crossheading", "section", "item", "subitem", "annex"]
_IMPLICIT_ORDINAL_KINDS = ["subsection", "row"]


def test_stable_label_units_have_correct_identity_class():
    """Family A units (osa, luku, pykälä, kohta, alakohta, liite, väliotsikko) are stable_label."""
    for kind in _STABLE_LABEL_KINDS:
        spec = FINLAND_REGISTRY.unit_specs[kind]
        assert spec.identity_class == "stable_label", (
            f"{kind!r}: expected identity_class='stable_label', got {spec.identity_class!r}"
        )


def test_implicit_ordinal_units_have_correct_identity_class():
    """Family B units (momentti, rivi) are implicit_ordinal."""
    for kind in _IMPLICIT_ORDINAL_KINDS:
        spec = FINLAND_REGISTRY.unit_specs[kind]
        assert spec.identity_class == "implicit_ordinal", (
            f"{kind!r}: expected identity_class='implicit_ordinal', got {spec.identity_class!r}"
        )


def test_subsection_is_the_only_implicit_ordinal_body_unit():
    """momentti (subsection) is the only body-chain unit with implicit_ordinal identity."""
    body_chain = ["part", "chapter", "section", "subsection", "item", "subitem"]
    implicit_ordinal_body = [
        k for k in body_chain if FINLAND_REGISTRY.unit_specs[k].identity_class == "implicit_ordinal"
    ]
    assert implicit_ordinal_body == ["subsection"], (
        f"Expected only 'subsection' as implicit_ordinal in body chain; got {implicit_ordinal_body}"
    )


def test_no_unit_has_repeal_compacts_true():
    """Finnish law never compacts on repeal: repeal_compacts must be False for every unit."""
    for kind, spec in FINLAND_REGISTRY.unit_specs.items():
        assert spec.repeal_compacts is False, (
            f"{kind!r}: repeal_compacts should be False (Finnish law never auto-compacts)"
        )


def test_stable_label_units_use_suffix_insertion():
    """Family A units use suffix insertion ('1 a §' style)."""
    for kind in _STABLE_LABEL_KINDS:
        spec = FINLAND_REGISTRY.unit_specs[kind]
        assert spec.insertion_policy == "suffix", (
            f"{kind!r}: expected insertion_policy='suffix', got {spec.insertion_policy!r}"
        )


def test_implicit_ordinal_units_use_shift_ordinal_insertion():
    """Family B units use shift_ordinal insertion."""
    for kind in _IMPLICIT_ORDINAL_KINDS:
        spec = FINLAND_REGISTRY.unit_specs[kind]
        assert spec.insertion_policy == "shift_ordinal", (
            f"{kind!r}: expected insertion_policy='shift_ordinal', got {spec.insertion_policy!r}"
        )


def test_display_name_set_for_all_units():
    """All Finland registry units have a non-empty display name."""
    for kind, spec in FINLAND_REGISTRY.unit_specs.items():
        assert spec.display_name, f"{kind!r}: display_name is empty; expected a localized name"


# ---------------------------------------------------------------------------
# Identity policy: UnitRegistry helper methods
# ---------------------------------------------------------------------------


def test_get_identity_class_known_units():
    """get_identity_class returns the correct class for known units."""
    assert FINLAND_REGISTRY.get_identity_class("section") == "stable_label"
    assert FINLAND_REGISTRY.get_identity_class("subsection") == "implicit_ordinal"
    assert FINLAND_REGISTRY.get_identity_class("item") == "stable_label"
    assert FINLAND_REGISTRY.get_identity_class("row") == "implicit_ordinal"


def test_get_identity_class_unknown_returns_stable_label():
    """get_identity_class returns 'stable_label' as safe default for unknown unit kinds."""
    assert FINLAND_REGISTRY.get_identity_class("widget") == "stable_label"
    assert FINLAND_REGISTRY.get_identity_class("") == "stable_label"


def test_allows_suffix_insertion():
    """allows_suffix_insertion returns True for stable_label units, False for shift_ordinal."""
    for kind in _STABLE_LABEL_KINDS:
        assert FINLAND_REGISTRY.allows_suffix_insertion(kind), f"{kind!r}: expected allows_suffix_insertion=True"
    for kind in _IMPLICIT_ORDINAL_KINDS:
        assert not FINLAND_REGISTRY.allows_suffix_insertion(kind), f"{kind!r}: expected allows_suffix_insertion=False"
    assert not FINLAND_REGISTRY.allows_suffix_insertion("widget")


def test_allows_ordinal_shift():
    """allows_ordinal_shift returns True for implicit_ordinal units, False for stable_label."""
    for kind in _IMPLICIT_ORDINAL_KINDS:
        assert FINLAND_REGISTRY.allows_ordinal_shift(kind), f"{kind!r}: expected allows_ordinal_shift=True"
    for kind in _STABLE_LABEL_KINDS:
        assert not FINLAND_REGISTRY.allows_ordinal_shift(kind), f"{kind!r}: expected allows_ordinal_shift=False"
    assert not FINLAND_REGISTRY.allows_ordinal_shift("widget")


def test_repeal_compacts_siblings_always_false():
    """repeal_compacts_siblings returns False for every Finnish unit and unknown units."""
    all_kinds = list(FINLAND_REGISTRY.unit_specs.keys()) + ["widget", ""]
    for kind in all_kinds:
        assert not FINLAND_REGISTRY.repeal_compacts_siblings(kind), (
            f"{kind!r}: repeal_compacts_siblings should be False"
        )


def test_crossheading_is_registered_and_stable_label():
    """crossheading (väliotsikko) is registered with stable_label identity."""
    assert FINLAND_REGISTRY.is_valid_unit_kind("crossheading")
    spec = FINLAND_REGISTRY.unit_specs["crossheading"]
    assert spec.identity_class == "stable_label"
    assert spec.display_name == "väliotsikko"
    assert not spec.can_have_heading  # väliotsikko IS a heading, it doesn't carry one
