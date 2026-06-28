"""Invariants for the closed ``QuirksDisposition`` cross-jurisdiction vocabulary.

These pin the authority-firewall fix for the cross-jurisdiction
``quirks_disposition`` discriminant (the leakage documented in
``notes/ARCHITECTURE_LEAK_LEDGER.md``): ``quirks_disposition`` is a closed
``StrEnum`` so producer-set and consumer-set are the same checkable object, and
an unregistered member fails loud at the consumer boundary
(``coerce_quirks_disposition`` raises ``UnregisteredQuirksDisposition``) instead
of silently failing a consumer-side match.

Mirrors ``tests/test_fi_recovery_kind_enum.py`` (5 tests + AST-scan
producer-set == enum-set + lowercase-slug values + no-bare-string producer).
The AST scan recurses across the cross-jurisdiction producer set
(``core/``, ``estonia/``, ``eu/``, ``finland/``, ``new_zealand/``,
``uk_legislation/``, ``norway/``, ``sweden/``, ``us_federal/``, ``open_law/``,
``tools/``) rather than the Finland-only precedent because ``QuirksDisposition``
is shared across frontends.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from lawvm.core.adjudication_evidence import adjudication_record_diagnostic_detail
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.quirks_disposition import (
    QuirksDisposition,
    UnregisteredQuirksDisposition,
    coerce_quirks_disposition,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
# The producer/consumer files for the shared ``QuirksDisposition`` vocabulary.
# Producers may live anywhere a frontend emits an evidence/diagnostic finding;
# consumers route untyped stored values back through ``coerce_quirks_disposition``.
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

    Recursive — the producer set spans frontend subpackages (``finland/johtolause``,
    ``uk_legislation/effect_*`` etc.) so a flat glob would miss members.
    """
    files: list[Path] = []
    for rel in _VOCAB_DIRS:
        root = _REPO_ROOT / rel
        if not root.exists():
            continue
        files.extend(sorted(root.rglob("*.py")))
    return files


def _produced_quirks_disposition_members() -> set[str]:
    """AST-scan the producer dirs for every ``QuirksDisposition.MEMBER`` reference.

    After the type migration the producer set is the set of enum members that
    actually appear at a producer/consumer site (``QuirksDisposition.X``). This
    set must equal the closed enum membership: a member with no reference is
    dead vocabulary, and a producer cannot reference a non-existent member
    (that is a ``NameError`` at import, caught by the smoke import).
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
                and node.value.id == "QuirksDisposition"
            ):
                referenced.add(node.attr)
    return referenced


def _bare_string_quirks_disposition_producers() -> list[str]:
    """Find any remaining bare-string ``quirks_disposition`` producer.

    The firewall guarantee is that NO producer writes a free string into a
    ``quirks_disposition`` slot — they must reference the enum. A bare literal at
    such a slot is exactly the silent producer-side leak the closed vocabulary
    is supposed to make impossible.
    """
    offenders: list[str] = []
    for path in _vocab_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            # kwargs: foo(..., quirks_disposition="literal", ...)
            if isinstance(node, ast.keyword) and node.arg == "quirks_disposition":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT)}:{node.lineno} {node.value.value!r}"
                    )
            # dict entry: {"quirks_disposition": "literal"} / detail={...}
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values, strict=False):
                    if (
                        key is not None
                        and isinstance(key, ast.Constant)
                        and key.value == "quirks_disposition"
                        and isinstance(value, ast.Constant)
                        and isinstance(value.value, str)
                    ):
                        offenders.append(
                            f"{path.relative_to(_REPO_ROOT)}:{value.lineno} {value.value!r}"
                        )
    return offenders


def test_producer_set_equals_consumer_enum_set() -> None:
    """Producer-referenced quirks members == the closed enum membership.

    The enum is the single registry; producers and consumers both key off it.
    A member nobody references is dead vocabulary; a producer cannot reference a
    member that is not registered (``NameError`` at import). This set-equality is
    what makes producer-set == consumer-set checkable — the core of the
    firewall fix.
    """
    enum_members = {member.name for member in QuirksDisposition}
    produced = _produced_quirks_disposition_members()

    orphaned = enum_members - produced
    assert not orphaned, (
        f"QuirksDisposition members with no producer/consumer reference {sorted(orphaned)}; "
        f"remove them or restore the producer site"
    )
    # produced is a subset of enum_members by construction (NameError otherwise),
    # so equality here pins the no-dead-vocab invariant.
    assert produced == enum_members


def test_no_bare_string_quirks_disposition_producer_remains() -> None:
    """No producer may write a free string into a ``quirks_disposition`` slot."""
    offenders = _bare_string_quirks_disposition_producers()
    assert not offenders, (
        "free-string quirks_disposition producers remain; "
        f"route through lawvm.core.quirks_disposition.QuirksDisposition: {offenders}"
    )


def test_coerce_quirks_disposition_fails_loud_on_unregistered_member() -> None:
    """An unregistered string is a registration gap, never a silent no-match."""
    with pytest.raises(UnregisteredQuirksDisposition):
        coerce_quirks_disposition("not_a_registered_quirks_disposition")

    # A registered string round-trips to the singleton member.
    member = coerce_quirks_disposition("record_residual_without_repairing_to_oracle")
    assert member is QuirksDisposition.RECORD_RESIDUAL_WITHOUT_REPAIRING_TO_ORACLE


def test_unknown_quirks_disposition_raises_through_production_deserialize_path() -> None:
    """A malformed ``quirks_disposition`` through the production de-serializer must raise.

    §2.9 guard-liveness: the fail-loud path is reachable through the production
    ``adjudication_record_diagnostic_detail`` consumer boundary, not just the
    codec unit test. A replay adjudication envelope loaded from a stored mapping
    whose ``detail.quirks_disposition`` is unregistered must raise
    ``UnregisteredQuirksDisposition`` instead of silently producing a default-
    typed evidence row.
    """
    malformed_record = {
        "kind": "test_kind",
        "blocking": True,
        "phase": "parse",
        "detail": {"quirks_disposition": "not_a_registered_quirks_disposition"},
    }
    with pytest.raises(UnregisteredQuirksDisposition):
        adjudication_record_diagnostic_detail(malformed_record)


def test_known_quirks_disposition_value_round_trips_through_production_path() -> None:
    """A registered ``QuirksDisposition`` value round-trips through the production lane.

    Positive control for the negative fire-drill above: the same production
    ``adjudication_record_diagnostic_detail`` path accepts a registered
    ``QuirksDisposition`` member and surfaces it in the diagnostic envelope
    without raising. The diagnostic envelope re-serializes
    ``quirks_disposition`` to its bare boundary string (``str(member)``) —
    that round-trip is the byte-compatibility guarantee the closed vocabulary
    was added without breaking, so the round-trip is asserted against the
    bare string and against ``QuirksDisposition.RECORD`` via ``StrEnum``
    equality (NOT ``is`` — re-serialization returns a plain ``str``, not the
    singleton member).
    """
    detail = diagnostic_detail(
        rule_id="test_rule",
        phase="parse",
        blocking=True,
        quirks_disposition=QuirksDisposition.RECORD,
        detail={},
    )
    record = {
        "kind": "test_kind",
        "blocking": detail["blocking"],
        "phase": detail["phase"],
        "detail": detail,
    }
    envelope = adjudication_record_diagnostic_detail(record)
    # The round-trip produces a string at the envelope boundary, which
    # ``StrEnum`` treats as equal to the singleton member. The string form
    # is the byte-stable boundary slug.
    assert envelope["quirks_disposition"] == "record"
    assert envelope["quirks_disposition"] == QuirksDisposition.RECORD


def test_quirks_disposition_values_are_lowercase_slugs_or_unset_sentinel() -> None:
    """Enum values must remain serialization-stable lowercase slugs.

    ``UNSET`` is the allowed empty-string sentinel (``""``) — it round-trips with
    the prior ``str = ""`` default for evidence rows constructed before a
    disposition is known and must NOT be re-formatted. All other values are
    lowercase ASCII slug strings.
    """
    for member in QuirksDisposition:
        assert isinstance(member, str)
        if member is QuirksDisposition.UNSET:
            assert member.value == ""
            continue
        assert re.fullmatch(r"[a-z][a-z_]*", member.value), member.value


def test_enum_definition_has_no_duplicate_values() -> None:
    """No two members may share a value (would collapse distinct producers)."""
    values = [member.value for member in QuirksDisposition]
    assert len(values) == len(set(values))
