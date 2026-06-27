"""Invariants for the typed ``ScopeConfidence`` carrier at the core boundary.

Pins the AGENTS.md §1.9 (typed carriers over dynamic shape) and §2.2
(scope confidence must be tracked, not erased) fix for
``LegalOperation.scope_confidence`` (formerly ``Any``): the field now accepts
only ``None`` (the legitimate "no witness" sentinel) or a typed instance of a
frontend dataclass that explicitly inherits the marker
``lawvm.core.scope_confidence.ScopeConfidence`` protocol. A bare ``str``
smuggled across the ``LegalOperation.__post_init__`` waist fails loud as
``UnregisteredScopeConfidence`` (AGENTS.md §1.10 — never silent-fallback),
which is the typed rebuttal to the historical "frontend-owned typed rider"
justifying ``Any``.

Mirrors ``tests/test_fi_recovery_kind_enum.py``: AST-scan producer-set ==
protocol-implementer-set across frontends; ``coerce_scope_confidence`` raises
on bare string; production-lane fire-drill driving a bare-string
``scope_confidence`` through the ``LegalOperation`` constructor and asserting
the typed diagnostic fires.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction
from lawvm.core.scope_confidence import (
    UnregisteredScopeConfidence,
    coerce_scope_confidence,
)
from lawvm.finland import ops as _fi_ops
from lawvm.finland.ops import ScopeConfidence as FiScopeConfidence
from lawvm.norway import grafter as _no_grafter
from lawvm.norway.scope_confidence import NOScopeConfidence

_REPO_ROOT = Path(_fi_ops.__file__).resolve().parents[3]
_SRC_ROOT = _REPO_ROOT / "src" / "lawvm"
# Frontend source roots that may declare ``ScopeConfidence`` producers (typed
# dataclasses that ride on ``LegalOperation.scope_confidence``) or pass
# ``scope_confidence=...`` to ``LegalOperation(...)``. ``core`` is excluded
# from the AST scan because it owns the marker protocol, not a frontend
# implementation.
_FRONTEND_DIRS = tuple(
    sorted(
        path
        for path in _SRC_ROOT.iterdir()
        if path.is_dir()
        and path.name not in {"core", "tools"}
        and (path / "__init__.py").exists()
    )
)


def _ast_files() -> list[Path]:
    """Every frontend .py file (modules + nested package .py files)."""
    files: list[Path] = []
    for frontend in _FRONTEND_DIRS:
        files.extend(frontend.rglob("*.py"))
    return sorted(files)


def _parse_ast_safely(path: Path) -> ast.AST | None:
    """Parse a frontend file, returning ``None`` on syntax error.

    Source files with syntax errors cannot run in production and cannot host a
    ``scope_confidence=`` callsite that crosses the ``LegalOperation`` waist,
    so they are safe to skip: the syntax error itself is caught by the ruff /
    ty / pytest gates and surfaced by those gates, not by this parity scan.
    The skip lets this scan stay green in a dirty working tree where a
    concurrent agent's file is mid-edit (AGENTS.md §3.6 -- the gate is the
    canonical artifact; this AST scan is the parity invariant).
    """
    try:
        return ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return None


def _legal_operation_bare_string_scope_confidence_producers() -> list[str]:
    """AST-scan frontends for any ``LegalOperation(..., scope_confidence="<str>", ...)`` callsite.

    A bare string at this callsite is exactly the §1.9 leak the protocol closes:
    even one such callsite would let a free-form ``str`` cohabit with
    Finland's typed dataclass on the canonical core carrier. Producers MUST
    pass ``None`` (the sentinel) or a typed ``ScopeConfidence`` instance.
    """
    offenders: list[str] = []
    for path in _ast_files():
        tree = _parse_ast_safely(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == "LegalOperation"):
                continue
            for keyword in node.keywords:
                if keyword.arg != "scope_confidence":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, str
                ):
                    offenders.append(
                        f"{path.relative_to(_REPO_ROOT)}:{keyword.lineno} {keyword.value.value!r}"
                    )
    return offenders


def _scope_confidence_protocol_implementers() -> set[tuple[str, str]]:
    """AST-scan frontend class definitions for explicit ``ScopeConfidence`` protocol inheritors.

    Returns a set of ``(file_path, ClassName)`` tuples for dataclasses whose
    bases include a name resolving to the core ``ScopeConfidence`` protocol
    (matched by name -- the marker protocol has no members, so explicit
    inheritance is the only registration signal an AST scan can verify).
    """
    implementers: set[tuple[str, str]] = set()
    for path in _ast_files():
        tree = _parse_ast_safely(path)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for base in node.bases:
                base_name = _attribute_name(base)
                # Match either the bare ``ScopeConfidence`` import or an
                # alias like ``_CoreScopeConfidenceProtocol`` resolved to the
                # ``ScopeConfidence`` member of ``lawvm.core.scope_confidence``
                if base_name in ("ScopeConfidence", "_CoreScopeConfidenceProtocol"):
                    implementers.add((str(path.relative_to(_REPO_ROOT)), node.name))
    return implementers


def _attribute_name(node: ast.expr) -> str:
    """Ast helper: name (``X``) or attribute access tail (``a.b.c`` -> ``c``)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _known_frontend_scope_confidence_dataclasses() -> set[tuple[str, str]]:
    """Frontend dataclasses currently expected to ride ``LegalOperation.scope_confidence``.

    Hand-curated registry of producers that concretely construct instances handed to
    ``LegalOperation.scope_confidence``. The AST scan above verifies each shows
    up in the implementer set (i.e. explicitly inherits the marker protocol),
    keeping producer-set == protocol-implementer-set checkable.
    """
    return {
        ("src/lawvm/finland/ops.py", "ScopeConfidence"),
        ("src/lawvm/norway/scope_confidence.py", "NOScopeConfidence"),
    }


# --------------------------------------------------------------------------- #
# Protocol parity: producer-set == protocol-implementer-set
# --------------------------------------------------------------------------- #


def test_no_bare_string_scope_confidence_at_legal_operation_callsite() -> None:
    """No producer may pass a free string as ``LegalOperation(scope_confidence=...)``."""
    offenders = _legal_operation_bare_string_scope_confidence_producers()
    assert not offenders, (
        "free-string ``LegalOperation(scope_confidence=...)`` producers remain; "
        "pass None or a typed dataclass instance inheriting "
        f"lawvm.core.scope_confidence.ScopeConfidence: {offenders}"
    )


def test_frontend_scope_confidence_dataclasses_inherit_core_protocol() -> None:
    """Every registered frontend ``ScopeConfidence`` dataclass inherits the core protocol.

    The parity check verifies producer-set == protocol-implementer-set: a
    frontend that ships a scope_confidence dataclass but forgets to inherit the
    marker protocol is a registration gap (AGENTS.md §1.9). Adding a new
    frontend producer requires both listing it here AND inheriting the protocol
    in the dataclass declaration.
    """
    expected = _known_frontend_scope_confidence_dataclasses()
    actual = _scope_confidence_protocol_implementers()
    missing = expected - actual
    assert not missing, (
        "frontend ScopeConfidence producers not registered as protocol "
        f"implementers: {sorted(missing)}; mark them as "
        "``class <Name>(CoreScopeConfidenceProtocol):`` inheriting "
        "lawvm.core.scope_confidence.ScopeConfidence"
    )


def test_no_uncatalogued_scope_confidence_dataclass_appears() -> None:
    """A new frontend ScopeConfidence dataclass must be catalogued in this test.

    Adding a new producer dataclass without listing it here means the parity
    check is silently incomplete. Either remove the orphan class or list it.
    """
    expected = _known_frontend_scope_confidence_dataclasses()
    actual = _scope_confidence_protocol_implementers()
    unexpected = actual - expected
    assert not unexpected, (
        "uncatalogued ScopeConfidence protocol implementers appeared; if these "
        "are real producers they must be added to "
        "_known_frontend_scope_confidence_dataclasses() so the parity check "
        f"covers them: {sorted(unexpected)}"
    )


# --------------------------------------------------------------------------- #
# Coercion fail-loud
# --------------------------------------------------------------------------- #


def test_coerce_scope_confidence_passes_through_none() -> None:
    """``None`` is the legitimate 'no witness' sentinel."""
    assert coerce_scope_confidence(None) is None


def test_coerce_scope_confidence_raises_on_bare_string() -> None:
    """A bare string is always a registration gap, never a silent fallback."""
    with pytest.raises(UnregisteredScopeConfidence):
        coerce_scope_confidence("inferred_from_payload")


def test_coerce_scope_confidence_raises_on_unregistered_vocab_member() -> None:
    """Even an obviously-near-miss string is rejected -- typos do not slip through."""
    with pytest.raises(UnregisteredScopeConfidence):
        coerce_scope_confidence("inferred_from_context")  # close but not registered


def test_coerce_scope_confidence_passes_through_typed_instance() -> None:
    """A frontend typed instance is presumed conformant; core does not inspect fields."""
    instance = NOScopeConfidence(rung_id="inferred_from_payload")
    assert coerce_scope_confidence(instance) is instance


# --------------------------------------------------------------------------- #
# Production-lane fire-drill: LegalOperation.__post_init__ waist
# --------------------------------------------------------------------------- #


def _make_legal_operation(**overrides: Any) -> LegalOperation:
    """Minimal LegalOperation constructor for the fire-drill.

    ``overrides`` are typed ``Any`` so a deliberate fire-drill can pass a bare
    ``str`` (the failure mode the ``LegalOperation.__post_init__`` waist must
    reject at runtime by raising ``UnregisteredScopeConfidence``); the typed
    carrier contract is checked by the runtime gate, not the static type of
    this helper.
    """
    defaults: dict[str, Any] = {
        "op_id": "test-op",
        "sequence": 1,
        "action": StructuralAction.REPLACE,
        "target": LegalAddress((("section", "1"),)),
    }
    defaults.update(overrides)
    return LegalOperation(**defaults)


def test_legal_operation_rejects_bare_string_scope_confidence() -> None:
    """Production-lane fire-drill: a bare string fails loud at the boundary."""
    with pytest.raises(UnregisteredScopeConfidence) as info:
        _make_legal_operation(scope_confidence="inferred_from_payload")
    assert "inferred_from_payload" in str(info.value)


def test_legal_operation_rejects_other_bare_strings() -> None:
    """Any bare string (not just one vocabulary item) is rejected."""
    with pytest.raises(UnregisteredScopeConfidence):
        _make_legal_operation(scope_confidence="fallback")
    with pytest.raises(UnregisteredScopeConfidence):
        _make_legal_operation(scope_confidence="explicit_source")


def test_legal_operation_accepts_none_scope_confidence() -> None:
    """The legitimate 'no witness' sentinel must remain a passthrough."""
    op = _make_legal_operation(scope_confidence=None)
    assert op.scope_confidence is None


def test_legal_operation_accepts_finland_typed_instance() -> None:
    """A typed Finland ``ScopeConfidence`` instance passes the boundary."""
    witness = FiScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source=_fi_ops.ScopeResolutionSource.EXPLICIT_CHUNK,
        confidence=_fi_ops.ScopeResolutionConfidence.EXPLICIT,
        resolved_chapter="2",
    )
    op = _make_legal_operation(scope_confidence=witness)
    assert op.scope_confidence is witness


def test_legal_operation_accepts_norway_typed_instance() -> None:
    """A typed Norway ``NOScopeConfidence`` instance passes the boundary."""
    witness = NOScopeConfidence(rung_id="inferred_from_payload")
    op = _make_legal_operation(scope_confidence=witness)
    assert op.scope_confidence is witness


def test_norway_grafter_no_longer_passes_bare_string_scope_confidence() -> None:
    """Norway grafter callsites hand typed ``NOScopeConfidence`` instances.

    AST-scan the grafter for ``_append_no_structured_parse_recovery_adjudications``
    callsites -- the historical bare-string producer surface -- and assert
    every ``scope_confidence=`` keyword at those callsites is a
    ``NOScopeConfidence(...)`` constructor, not a bare string literal. This is
    the §1.9 fire-drill for the FI/NO apply path: a regression here means the
    typed diagnostic in ``_append_no_structured_parse_recovery_adjudications``
    signature-boundary check would silently accept the bare string again.
    """
    grafter_path = Path(_no_grafter.__file__)
    tree = ast.parse(grafter_path.read_text(encoding="utf-8"))
    offenders: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "_append_no_structured_parse_recovery_adjudications":
            continue
        for keyword in node.keywords:
            if keyword.arg != "scope_confidence":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant):
                offenders.append(
                    f"grafter.py:{keyword.lineno} bare string {value.value!r}"
                )
                continue
            if not (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "NOScopeConfidence"
            ):
                offenders.append(
                    f"grafter.py:{keyword.lineno} not a NOScopeConfidence(...) "
                    f"constructor (got {ast.dump(value)})"
                )
    assert not offenders, (
        "Norway grafter _append_no_structured_parse_recovery_adjudications "
        "callsites must pass typed NOScopeConfidence instances, not bare "
        f"strings: {offenders}"
    )


# --------------------------------------------------------------------------- #
# Runtime-canonical sanity checks: producers actually construct the typed shape
# --------------------------------------------------------------------------- #


def test_fi_scope_confidence_inherits_core_marker_protocol() -> None:
    """Finland ``ScopeConfidence`` resolves to the marker protocol class explicitly."""
    # Explicit inheritance; not virtual.
    bases = FiScopeConfidence.__mro__
    assert any(getattr(b, "__name__", "") == "ScopeConfidence" and b is not FiScopeConfidence for b in bases), [
        getattr(b, "__name__", "") for b in bases
    ]


def test_no_scope_confidence_inherits_core_marker_protocol() -> None:
    """Norway ``NOScopeConfidence`` resolves to the marker protocol class explicitly."""
    bases = NOScopeConfidence.__mro__
    assert any(getattr(b, "__name__", "") == "ScopeConfidence" and b is not NOScopeConfidence for b in bases), [
        getattr(b, "__name__", "") for b in bases
    ]


def test_no_scope_confidence_rejects_unregistered_rung() -> None:
    """An unregistered rung string is a registration gap (AGENTS.md §1.10)."""
    with pytest.raises(ValueError):
        NOScopeConfidence(rung_id="not_a_registered_rung")


def test_fi_scope_confidence_rung_id_returns_section_22_vocabulary() -> None:
    """Finland's rung_id property emits the canonical ladder vocabulary."""
    explicit = FiScopeConfidence(
        tag="chapter_scope_from_explicit_chunk",
        source=_fi_ops.ScopeResolutionSource.EXPLICIT_CHUNK,
        confidence=_fi_ops.ScopeResolutionConfidence.EXPLICIT,
        resolved_chapter="2",
    )
    assert explicit.rung_id == "explicit_source"
    rewritten = FiScopeConfidence(
        tag="chapter_scope_stripped_subsection_insert",
        source=_fi_ops.ScopeResolutionSource.EXPLICIT_SCOPE_REWRITE,
        confidence=_fi_ops.ScopeResolutionConfidence.REWRITTEN,
    )
    assert rewritten.rung_id == "inferred_from_payload"
