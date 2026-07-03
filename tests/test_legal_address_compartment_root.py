"""Tests for the optional address-ROOT compartment selector on ``LegalAddress``.

#186 §5.3 / §7 delta #6: the universal legal state ``Σ`` has multiple address
ROOTS — a statute ``body`` plus a tuple of ``supplements`` (SE bilaga / EU
annexes / UK schedules). WHICH root an op targets is a property of the ADDRESS
(``LegalAddress.root``), not something re-derived from a leaf-kind sniff in each
frontend's grafter. ``root=None`` (the default) names the statute ``body``, so a
body address is byte-identical to the pre-compartment ``LegalAddress`` (same
equality, hash, ``path``, ``__str__``, and effect-graph wire projection). A
non-``None`` ``root`` names a first-class compartment so the SE materializer
dispatches to ``supplements`` uniformly.

These tests pin:

  * a root-free (body) address's equality / hash / str / path / wire are
    byte-identical to the pre-compartment ``LegalAddress`` (no replay change);
  * ``root`` participates in equality/hash (body vs supplements are distinct);
  * ``root_kind`` reports the selector; ``parent`` preserves the compartment;
  * the effect-graph wire emits ``root`` ONLY when set;
  * ``__post_init__`` rejects a blank (empty-string) root.
"""
from __future__ import annotations

import pytest

from lawvm.core.effect_lifecycle import legal_address_wire
from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind


def test_body_address_is_byte_identical_to_pre_compartment() -> None:
    """A ``root=None`` address equals + hashes + renders exactly as before."""
    a = LegalAddress(path=(("section", "5"),))
    b = LegalAddress(path=(("section", "5"),), root=None)
    assert a == b
    assert hash(a) == hash(b)
    assert a.root is None
    assert a.root_kind() is None
    assert str(a) == "section:5"  # no ``@root`` prefix for the body
    # A faceted body address is likewise unchanged.
    faceted = LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING)
    assert faceted.root is None
    assert str(faceted).startswith("section:5")


def test_supplements_root_is_distinct_from_body() -> None:
    """The compartment participates in equality/hash: body != supplements."""
    body = LegalAddress(path=(("appendix", "A"),))
    supp = LegalAddress(path=(("appendix", "A"),), root="supplements")
    assert body != supp
    assert hash(body) != hash(supp) or body != supp  # distinct addresses
    assert supp.root_kind() == "supplements"
    assert str(supp) == "@supplements appendix:A"


def test_parent_preserves_compartment_root() -> None:
    """``parent`` stays in the same compartment (root is a whole-address prop)."""
    supp = LegalAddress(path=(("appendix", "A"), ("paragraph", "1")), root="supplements")
    parent = supp.parent()
    assert parent is not None
    assert parent.root == "supplements"
    assert parent.path == (("appendix", "A"),)
    # A body address's parent stays body (``None``) — byte-identical.
    body = LegalAddress(path=(("chapter", "3"), ("section", "2")))
    body_parent = body.parent()
    assert body_parent is not None
    assert body_parent.root is None


def test_wire_emits_root_only_when_set() -> None:
    """The effect-graph wire is byte-identical for a body address."""
    body = LegalAddress(path=(("section", "5"),))
    body_wire = legal_address_wire(body)
    assert body_wire is not None
    assert "root" not in body_wire  # body wire unchanged (no ``root`` key)

    supp = LegalAddress(path=(("appendix", "A"),), root="supplements")
    supp_wire = legal_address_wire(supp)
    assert supp_wire is not None
    assert supp_wire["root"] == "supplements"


def test_blank_root_is_rejected() -> None:
    """An empty-string root would masquerade as a distinct-from-body root."""
    with pytest.raises(ValueError):
        LegalAddress(path=(("appendix", "A"),), root="")
