"""Invariants for the jurisdiction-neutral ``StructuralAction`` string codec.

These pin the fail-loud authority-firewall fix for the shared action-string
boundary in ``lawvm.core.semantic_types`` (``structural_action_from_str``):
an action string that names no ``StructuralAction`` member fails loud
(``ValueError``) rather than silently collapsing to ``META`` (the previous
per-frontend behaviour, an unowned mislabel channel that let a malformed or
typo'd producer flow through replay unverified).

Mirrors ``tests/test_fi_recovery_kind_enum.py`` (precedent):
- AST-scan producer-set == enum-set across the cross-jurisdiction producer dirs
- Codec unit tests for both ``on_unknown="raise"`` (fail-loud) and
  ``on_unknown="meta"`` (legacy permissive fallback)
- Production-lane fire-drills asserting the fail-loud path is reachable
  through each grafter's local action-wrapper, not just the codec unit test
  (the §2.9 guard-liveness worst-failure-class test: a check that exists but
  is unreachable from production is false confidence).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lawvm.core.semantic_types import (
    StructuralAction,
    structural_action_from_str,
    structural_action_value,
)
from lawvm.estonia import grafter as ee_grafter
from lawvm.norway import grafter as no_grafter
from lawvm.uk_legislation import lowering_actions as uk_lowering_actions

_REPO_ROOT = Path(__file__).resolve().parent.parent
# StructuralAction is the shared cross-jurisdiction action vocabulary.
_VOCAB_DIRS = (
    "src/lawvm/core",
    "src/lawvm/estonia",
    "src/lawvm/eu",
    "src/lawvm/finland",
    "src/lawvm/new_zealand",
    "src/lawvm/uk_legislation",
    "src/lawvm/norway",
    "src/lawvm/sweden",
    "src/lawvm/us_federal",
    "src/lawvm/open_law",
    "src/lawvm/tools",
)


def _vocab_files() -> list[Path]:
    """Yield every ``.py`` file under one of the vocabulary directories.

    Recursive — the StructuralAction producer set spans frontend subpackages
    (``finland/johtolause``, ``uk_legislation/effect_*`` etc.) so a flat glob
    would miss members.
    """
    files: list[Path] = []
    for rel in _VOCAB_DIRS:
        root = _REPO_ROOT / rel
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.py")))
    return files


def _produced_structural_action_members() -> set[str]:
    """AST-scan the producer dirs for every ``StructuralAction.MEMBER`` reference.

    After the type migration the producer set is the set of enum members that
    actually appear at a producer/consumer site (``StructuralAction.X``). This
    set must equal the closed enum membership: a member with no reference is
    dead vocabulary, and a producer cannot reference a non-existent member
    (that is a ``NameError`` at import).
    """
    referenced: set[str] = set()
    for path in _vocab_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "StructuralAction"
            ):
                referenced.add(node.attr)
    return referenced


def _bare_string_action_producers_at_legal_operation() -> list[str]:
    """Find ``LegalOperation(action="literal")`` calls — bare strings at the action slot.

    ``action`` is too common a kwarg name to scan unscoped, so this check
    narrows to ``LegalOperation(...)`` (the canonical structural carrier of the
    typed ``action=StructuralAction`` field). A bare-string literal at that
    position is exactly the silent producer-side leak the typed wrapper is
    supposed to make impossible.
    """
    offenders: list[str] = []
    for path in _vocab_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_legal_op = (
                (isinstance(func, ast.Name) and func.id == "LegalOperation")
                or (isinstance(func, ast.Attribute) and func.attr == "LegalOperation")
            )
            if not is_legal_op:
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "action"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT)}:{kw.lineno} {kw.value.value!r}"
                    )
    return offenders


def test_producer_set_equals_consumer_enum_set() -> None:
    """Producer-referenced ``StructuralAction`` members == the closed enum membership.

    The enum is the single registry; producers and consumers both key off it.
    A member nobody references is dead vocabulary; a producer cannot reference a
    member that is not registered (``NameError`` at import). This set-equality
    is what makes producer-set == consumer-set checkable.
    """
    enum_members = {member.name for member in StructuralAction}
    produced = _produced_structural_action_members()

    orphaned = enum_members - produced
    assert not orphaned, (
        f"StructuralAction members with no producer/consumer reference {sorted(orphaned)}; "
        f"remove them or restore the producer site"
    )
    assert produced == enum_members


def test_no_bare_string_action_producer_at_legal_operation_remains() -> None:
    """No producer may pass a free string ``action=`` to ``LegalOperation(...)``.

    A free string at this position bypasses the typed wrapper
    (``structural_action_from_str`` / frontend-local ``_to_structural_action``)
    and is exactly the unowned mislabel channel the codec was added to close.
    """
    offenders = _bare_string_action_producers_at_legal_operation()
    assert not offenders, (
        "free-string LegalOperation(action=...) producers remain; "
        f"route through lawvm.core.semantic_types.StructuralAction: {offenders}"
    )


def test_structural_action_from_str_raises_on_unknown_action_string() -> None:
    """An action string naming no ``StructuralAction`` member fails loud by default."""
    with pytest.raises(ValueError, match="unknown structural action string"):
        structural_action_from_str("_not_a_structural_action", on_unknown="raise")

    # An already-typed StructuralAction round-trips unchanged.
    assert (
        structural_action_from_str(StructuralAction.REPLACE, on_unknown="raise")
        is StructuralAction.REPLACE
    )
    # Known boundary strings map to their singleton members.
    assert (
        structural_action_from_str("text_replace", on_unknown="raise")
        is StructuralAction.TEXT_REPLACE
    )
    assert (
        structural_action_from_str("heading_replace", on_unknown="raise")
        is StructuralAction.HEADING_REPLACE
    )


def test_structural_action_from_str_meta_legacy_fallback_preserved() -> None:
    """``on_unknown="meta"`` preserves the legacy permissive fallback.

    Explicitly opted-in legacy call sites still receive ``StructuralAction.META``
    on an unrecognized string instead of raising — this is the permissive
    fallback the historical per-frontend behaviour relied on. The default
    ``on_unknown="raise"`` must NOT silently degrade to this; the meta fallback
    is only invoked when the caller asks for it.
    """
    result = structural_action_from_str("_not_a_structural_action", on_unknown="meta")
    assert result is StructuralAction.META

    # A recognized string still maps to the typed member under meta policy too
    # — the on_unknown path is only consulted when the enum constructor fails.
    assert (
        structural_action_from_str("repeal", on_unknown="meta") is StructuralAction.REPEAL
    )
    # An unknown on_unknown policy is itself an error — no silent third
    # behaviour. This is only reachable via an unknown string (a recognized
    # string exits before consulting on_unknown).
    with pytest.raises(ValueError, match="unknown on_unknown policy"):
        structural_action_from_str("_not_a_structural_action", on_unknown="bogus")


def test_structural_action_values_are_lowercase_slugs() -> None:
    """Enum values must remain serialization-stable lowercase slugs."""
    for member in StructuralAction:
        assert isinstance(member, StructuralAction)
        assert re.fullmatch(r"[a-z][a-z_]*", member.value), member.value
        # ``__str__`` returns the boundary string value (no Enum-image leakage).
        assert str(member) == member.value


# ---------------------------------------------------------------------------
# Production-lane fire-drills: each frontend's structured-action boundary must
# reach the codec's fail-loud path, not just exist as a dead wrapper.
#
# A guard that exists but is unreachable from the production lane looks real,
# passes review, and creates false confidence — the §2.9 worst failure class.
# Each fire-drill drives a known-violating input through the actual production
# grafter wrapper and asserts the typed diagnostic fires.
# ---------------------------------------------------------------------------


def test_estonia_grafter_to_structural_action_fires_loud_on_unknown() -> None:
    """EE grafter's ``_to_structural_action`` routes through ``structural_action_from_str``.

    Drives a malformed action string through the production wrapper and asserts
    the codec's ``ValueError`` reaches the caller instead of silently collapsing
    to ``StructuralAction.META``.
    """
    with pytest.raises(ValueError, match="unknown structural action string"):
        ee_grafter._to_structural_action("_not_a_structural_action")

    # A known string round-trips to the typed enum.
    assert ee_grafter._to_structural_action("replace") is StructuralAction.REPLACE
    # A typed StructuralAction passes through unchanged.
    assert (
        ee_grafter._to_structural_action(StructuralAction.HEADING_REPLACE)
        is StructuralAction.HEADING_REPLACE
    )


def test_uk_legislation_lowering_to_structural_action_fires_loud_on_unknown() -> None:
    """UK lowering's ``_to_structural_action`` routes through ``structural_action_from_str``.

    Drives a malformed action string through the production wrapper and asserts
    the codec's ``ValueError`` reaches the caller instead of silently collapsing
    to ``StructuralAction.META``.
    """
    with pytest.raises(ValueError, match="unknown structural action string"):
        uk_lowering_actions._to_structural_action("_not_a_structural_action")

    # A known string round-trips to the typed enum.
    assert (
        uk_lowering_actions._to_structural_action("text_repeal")
        is StructuralAction.TEXT_REPEAL
    )


def test_norway_grafter_no_action_value_fires_loud_on_unknown() -> None:
    """NO grafter's ``_no_action_value`` validates through ``structural_action_from_str``.

    The Norway wrapper is the inverse (string-serialization) direction, but it
    is still the only Norway action boundary and is reached with raw parsed
    action strings — so it must route through ``structural_action_from_str`` to
    fail loud on an unrecognized producer string instead of letting it pass
    through the comparison/serialization boundary unlabelled.

    Drives a malformed action string through the production wrapper and asserts
    the codec's ``ValueError`` reaches the caller. Without the source-side
    validation this fire-drill would NOT fire — that would be the §2.9
    false-confidence failure class.
    """
    with pytest.raises(ValueError, match="unknown structural action string"):
        no_grafter._no_action_value("_not_a_structural_action")

    # A known string round-trips to its boundary value.
    assert no_grafter._no_action_value("replace") == "replace"
    # A typed StructuralAction serializes to its boundary value unchanged.
    assert no_grafter._no_action_value(StructuralAction.RENUMBER) == "renumber"

    # Symmetric with the codec: structural_action_value is still the inverse
    # direction and is the boundary-string serializer; the producer-side guard
    # is the wrapper, not the codec primitive itself.
    assert structural_action_value(StructuralAction.REPLACE) == "replace"
    assert structural_action_value("replace") == "replace"
