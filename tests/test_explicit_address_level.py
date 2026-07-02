"""Explicit-address-level invariant (audit registry row LS-14).

Registry assertion (LAWVM_AUDIT_INVARIANT_REGISTRY.md LS-14, AGENTS.md §1.9):

    A bare label is never a legal address, and no address level
    (section / subsection / item / ...) is privileged as a default — an
    address carries its level explicitly or it is unresolved.

**Disposition: IMPL-by-construction.** LawVM does not need a runtime
``APPLY.IMPLICIT_ADDRESS_LEVEL`` finding-kind because the violation it would
name is structurally impossible to construct:

  * :class:`lawvm.core.ir.LegalAddress` stores its path as
    ``Tuple[Tuple[str, str], ...]`` — every path step is an explicit
    ``(kind, label)`` pair. There is no ``label``-only path step type, so a
    "bare label" cannot be expressed as an address step at all.
  * ``LegalAddress.__post_init__`` raises ``ValueError`` if any step's ``kind``
    is empty, so an address can never carry a *defaulted* / blank level: the
    level is explicit or the construction fails loud.

A would-be ``IMPLICIT_ADDRESS_LEVEL`` violation therefore cannot reach a stored
address — the type rejects it at construction. This module is the PINNING gate
for that invariant: it is not a new violation class but a regression tripwire.
It fails the day someone:

  * relaxes the ``(kind, label)`` step shape into a bare-label form, or
  * deletes / weakens the empty-kind ``ValueError`` in ``__post_init__`` (so a
    blank/defaulted level would be silently accepted), or
  * introduces a single-arg ``LegalAddress`` convenience that defaults the
    level.

Two arms, mirroring the determinism-spine ratchet shape:

  * **CONSTRUCTION (runtime):** every bare-label / blank-level construction
    attempt raises; every explicit-level construction succeeds and round-trips
    its level.
  * **STATIC (AST):** ``LegalAddress.__post_init__`` provably still contains the
    empty-``kind`` guard, so the construction arm cannot be silently defanged by
    deleting the check while keeping the field name.
"""
from __future__ import annotations

import ast
import inspect
import textwrap
from typing import Any, cast

import pytest

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind


# --------------------------------------------------------------------------- #
# CONSTRUCTION arm — bare label / blank level is structurally rejected.
# --------------------------------------------------------------------------- #


def test_blank_level_path_step_is_rejected() -> None:
    """A path step with an empty (defaulted/blank) level fails loud (§1.9).

    The level cannot be silently defaulted: a blank ``kind`` is the closest a
    caller can come to "a bare label with no level", and it raises rather than
    being admitted under a privileged default.
    """
    with pytest.raises(ValueError, match="empty kind"):
        LegalAddress(path=(("", "5"),))

    # Even when an earlier step is well-formed, a later blank-level step is
    # rejected — no per-step default rescues it.
    with pytest.raises(ValueError, match="empty kind"):
        LegalAddress(path=(("section", "5"), ("", "2")))


def test_bare_label_string_is_not_an_address_step() -> None:
    """A bare label string is not a legal address step at all.

    ``LegalAddress`` has no ``label``-only step type; a path of bare strings
    cannot be unpacked into the required ``(kind, label)`` shape, so it raises
    instead of silently inventing a level.
    """
    # A bare label string (no explicit level) is not a (kind, label) step.
    # Build the malformed path through an ``Any`` alias so this exercises the
    # RUNTIME rejection (the type system already forbids it statically).
    bare_label_path = cast("Any", ("5",))
    with pytest.raises(ValueError):
        LegalAddress(path=bare_label_path)


def test_explicit_level_constructs_and_round_trips() -> None:
    """The sanctioned form — an explicit ``(kind, label)`` level — works.

    This pins that the guard is not vacuously rejecting *everything*: an address
    that names its level explicitly constructs cleanly and the level is
    recoverable (no level was dropped or defaulted away).
    """
    addr = LegalAddress(path=(("section", "5"), ("subsection", "2")))
    assert addr.leaf_kind() == "subsection"
    assert addr.leaf_label() == "2"
    assert addr.path[0] == ("section", "5")
    # The level is part of the address identity surface (str carries kind:label).
    assert str(addr) == "section:5/subsection:2"
    # A facet rider does not substitute for a level.
    faceted = LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING)
    assert faceted.leaf_kind() == "section"


def test_no_single_arg_bare_label_convenience_constructor() -> None:
    """``LegalAddress`` must not gain a level-defaulting bare-label constructor.

    The construction surface is ``path`` (an explicit ``(kind, label)``
    sequence), the optional ``special`` facet, and the optional ``ordinals``
    duplicate-label disambiguator (#186 §5.4 — a sparse ``(path_index,
    ordinal)`` selector that presupposes an explicit ``path`` and never supplies
    a default level). If a future convenience constructor accepted a bare label
    and supplied a default level, LS-14 would be silently violated. Pin that the
    public construction signature still REQUIRES the explicit ``path`` (no
    bare-label positional alias) and exposes no unexpected level-bearing
    parameter.
    """
    params = inspect.signature(LegalAddress).parameters
    assert set(params) == {"path", "special", "ordinals"}, (
        "LegalAddress construction signature changed; a new bare-label / "
        "level-defaulting constructor would violate LS-14 (explicit level). "
        f"Parameters now: {sorted(params)}"
    )
    # ``path`` remains a required positional (no default): the level is never
    # implied. ``special`` / ``ordinals`` are the only optional riders and
    # neither carries a label/level.
    assert params["path"].default is inspect.Parameter.empty
    assert params["special"].default is None
    assert params["ordinals"].default == ()


# --------------------------------------------------------------------------- #
# STATIC arm — the empty-kind guard provably still exists in __post_init__.
# --------------------------------------------------------------------------- #


def _post_init_source() -> str:
    return textwrap.dedent(inspect.getsource(LegalAddress.__post_init__))


def test_post_init_still_enforces_non_empty_level_statically() -> None:
    """AST-prove the empty-``kind`` ``ValueError`` guard survives in source.

    The CONSTRUCTION arm above exercises behaviour; this arm proves the *guard
    code itself* is present, so a regression that deletes the check (while
    keeping the field) is caught even if some other path masked the behaviour.
    We look for a ``raise`` of an error whose message mentions an empty kind,
    guarded by a ``not kind`` / empty-kind test over the path steps.
    """
    tree = ast.parse(inspect.getsource(LegalAddress))

    class _GuardVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.raises_empty_kind = False
            self.tests_kind_emptiness = False

        def visit_Raise(self, node: ast.Raise) -> None:
            # Look for raise ValueError(... "empty kind" ...).
            for sub in ast.walk(node):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if "empty kind" in sub.value:
                        self.raises_empty_kind = True
            self.generic_visit(node)

        def visit_UnaryOp(self, node: ast.UnaryOp) -> None:
            # Look for `not kind` (the emptiness test on the unpacked level).
            if isinstance(node.op, ast.Not) and isinstance(node.operand, ast.Name):
                if node.operand.id == "kind":
                    self.tests_kind_emptiness = True
            self.generic_visit(node)

    visitor = _GuardVisitor()
    visitor.visit(tree)
    assert visitor.raises_empty_kind, (
        "LegalAddress.__post_init__ no longer raises on an empty (blank/"
        "defaulted) level — LS-14 explicit-level invariant lost its guard."
    )
    assert visitor.tests_kind_emptiness, (
        "LegalAddress.__post_init__ no longer tests the unpacked level for "
        "emptiness (`not kind`) — the empty-level guard was weakened."
    )


def test_path_step_shape_is_an_explicit_kind_label_pair() -> None:
    """Pin the address step shape: an explicit (kind, label) pair, not a label.

    The unpack ``for i, (kind, _label) in enumerate(self.path)`` in
    ``__post_init__`` is the structural proof that a step carries BOTH a level
    (``kind``) and a label. If a future edit collapsed steps to a single bare
    label, this two-name unpack would disappear.
    """
    src = _post_init_source()
    tree = ast.parse(src)
    found_pair_unpack = False
    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            target = node.target
            # Expect `(i, (kind, _label))` — a tuple whose second element is
            # itself a 2-tuple unpack of the step into (level, label).
            if isinstance(target, ast.Tuple) and len(target.elts) == 2:
                step = target.elts[1]
                if isinstance(step, ast.Tuple) and len(step.elts) == 2:
                    found_pair_unpack = True
    assert found_pair_unpack, (
        "LegalAddress.__post_init__ no longer unpacks each path step as an "
        "explicit (kind, label) pair — a bare-label step shape would violate "
        "LS-14 (no implicit/defaulted address level)."
    )
