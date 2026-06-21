"""Invariants for the closed ``RecoveryKind`` allowance vocabulary.

These pin the authority-firewall fix for the apply-time mutation-allowance
boundary (``notes/ARCHITECTURE_LEAK_LEDGER.md`` rank 10): the ``recovery_kind``
/ ``rebound_kind`` discriminant is a closed ``StrEnum`` so the producer set and
the consumer set are the same checkable object, and an unregistered member fails
loud instead of silently failing the allowance match.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lawvm.core.recovery_kind import (
    RecoveryKind,
    UnregisteredRecoveryKind,
    coerce_recovery_kind,
)
from lawvm.finland import apply_typed_dispatch
from lawvm.finland.source_pathology import (
    build_destructive_shape_loss_risk_pathology,
)

_FINLAND_DIR = Path(apply_typed_dispatch.__file__).parent
# The producer files that emit recovery/rebound kinds plus the apply-time
# consumer; the only files allowed to reference RecoveryKind members.
_VOCAB_FILES = tuple(sorted(_FINLAND_DIR.glob("*.py")))


def _produced_recovery_members() -> set[str]:
    """AST-scan finland for every ``RecoveryKind.MEMBER`` a producer references.

    After the type migration the producer set is the set of enum members that
    actually appear at a producer/consumer site (``RecoveryKind.X``). This set
    must equal the closed enum membership: a member with no reference is dead
    vocabulary, and a producer cannot reference a non-existent member (that is a
    NameError at import, caught by the smoke import).
    """
    referenced: set[str] = set()
    for path in _VOCAB_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "RecoveryKind"
            ):
                referenced.add(node.attr)
    return referenced


def _bare_string_recovery_producers() -> list[str]:
    """Find any remaining bare-string recovery_kind/rebound_kind producer.

    The firewall guarantee is that NO producer writes a free string into a
    recovery_kind/rebound_kind slot -- they must reference the enum. A bare
    literal at such a slot is exactly the leak rank 10 describes.
    """
    offenders: list[str] = []
    for path in _VOCAB_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in ("recovery_kind", "rebound_kind"):
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    offenders.append(f"{path.name}:{node.lineno} {node.value.value!r}")
            # dict entry: {"recovery_kind": "literal"} / {"rebound_kind": "literal"}
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=True):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value in ("recovery_kind", "rebound_kind")
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        offenders.append(f"{path.name}:{value.lineno} {value.value!r}")
    return offenders


def test_producer_set_equals_consumer_enum_set() -> None:
    """Producer-referenced recovery/rebound members == the closed enum membership.

    The enum is the single registry; producers and the consumer both key off it.
    A member nobody references is dead vocabulary; a producer cannot reference a
    member that is not registered (NameError). This set-equality is what makes
    producer-set == consumer-set checkable -- the core of the leak rank 10 fix.
    """
    enum_members = {member.name for member in RecoveryKind}
    produced = _produced_recovery_members()

    orphaned = enum_members - produced
    assert not orphaned, (
        f"RecoveryKind members with no producer/consumer reference {sorted(orphaned)}; "
        f"remove them or restore the producer site"
    )
    # produced is a subset of enum_members by construction (NameError otherwise),
    # so equality here pins the no-dead-vocab invariant.
    assert produced == enum_members


def test_no_bare_string_recovery_kind_producer_remains() -> None:
    """No producer may write a free string into a recovery/rebound slot."""
    offenders = _bare_string_recovery_producers()
    assert not offenders, (
        "free-string recovery_kind/rebound_kind producers remain (leak rank 10); "
        f"route through lawvm.core.recovery_kind.RecoveryKind: {offenders}"
    )


def test_coerce_recovery_kind_fails_loud_on_unregistered_member() -> None:
    """An unregistered string is a registration gap, never a silent no-match."""
    with pytest.raises(UnregisteredRecoveryKind):
        coerce_recovery_kind("not_a_registered_recovery_kind")

    # A registered string round-trips to the singleton member.
    member = coerce_recovery_kind("sparse_item_tail_subsection_prune")
    assert member is RecoveryKind.SPARSE_ITEM_TAIL_SUBSECTION_PRUNE


def test_known_recovery_path_still_allowed_by_consumer() -> None:
    """A pathology carrying a registered kind matches the consumer allowance.

    Mirrors the apply-time check: a DESTRUCTIVE_SHAPE_LOSS_RISK pathology whose
    detail carries a recovery_kind in the landed-recovery rule set is recognised
    by ``_new_pathologies_include_recovery_kind`` keyed on the enum.
    """
    kind = RecoveryKind.SPARSE_ITEM_TAIL_SUBSECTION_PRUNE
    pathology = build_destructive_shape_loss_risk_pathology(
        source_statute="2000/1",
        target_unit_kind="section",
        target_label="1 §",
        recovery_kind=kind,
    )
    # The stored detail value stays string-compatible (serialization safe).
    assert pathology.detail["recovery_kind"] == "sparse_item_tail_subsection_prune"

    assert apply_typed_dispatch._new_pathologies_include_recovery_kind((pathology,), kind)
    # A different registered kind does not spuriously match.
    assert not apply_typed_dispatch._new_pathologies_include_recovery_kind(
        (pathology,), RecoveryKind.SUBSECTION_REPLACE_APPEND
    )


def test_recovery_kind_values_are_lowercase_slugs() -> None:
    """Enum values must remain serialization-stable lowercase slugs."""
    for member in RecoveryKind:
        assert re.fullmatch(r"[a-z][a-z_]*", member.value), member.value
        assert isinstance(member, str)


def test_enum_definition_has_no_duplicate_values() -> None:
    """No two members may share a value (would collapse distinct producers)."""
    values = [member.value for member in RecoveryKind]
    assert len(values) == len(set(values))
