"""Frozen/slots discipline curtain for the five §1.9 in-scope carriers.

AGENTS.md §1.9 ("Typed carriers over dynamic shape") requires that
semantic-field dataclasses at phase boundaries be `@dataclass(frozen=True,
slots=True)` so the legal-state, evidence, and source planes stay
type-distinct and a value cannot silently mutate across a phase seam.

This is a targeted ratchet over the five semantic carriers named in the
§1.9 task ("Frozen/slots discipline for legal-state carriers"):

  - lawvm.core.ir:ProvisionVersion        (legal-state plane)
  - lawvm.core.ir:ProvisionTimeline       (legal-state plane)
  - lawvm.core.timeline_consistency:ConsistencyDivergence  (evidence plane)
  - lawvm.finland.fixed_term_expiry:FixedTermDiagnostic   (evidence plane)
  - lawvm.finland.fixed_term_expiry:FixedTermExtraction   (evidence plane)

Each in-scope carrier MUST be `@dataclass(frozen=True, slots=True)`,
unless it is registered in ``_BLOCKED_MIGRATION`` (a typed carrier that
MUST be frozen per §1.9 but is blocked by an in-place mutation consumer
outside the strict task scope) or ``_GENUINELY_MUTABLE`` (mutable
runtime-state by design — accumulator sinks, builder patterns, mutable
per-call contexts).

Adding NEW entries to ``_BLOCKED_MIGRATION`` is allowed only with a
consumer-site witness (file:line) in the justification text, so every
allowlisted deferral is bounded by an audit task — not forgetfulness.

Widening the curtain to all of ``src/lawvm/**/*.py`` is a follow-up task:
the codebase has many legacy ``@dataclass(frozen=True)`` (no ``slots=True``)
and plain ``@dataclass`` carriers; collecting them in one sweep requires
coordinated per-file cleanup that exceeds this task's strict scope.
"""

from __future__ import annotations

import ast
import pathlib
import re
from typing import NamedTuple, Optional


_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class Carrier(NamedTuple):
    """One in-scope @dataclass carrier to assert discipline over."""

    module: str  # dotted import path of the containing module
    cls: str
    file: str  # repo-relative path of the module file


_IN_SCOPE_CARRIERS: tuple[Carrier, ...] = (
    Carrier("lawvm.core.ir", "ProvisionVersion", "src/lawvm/core/ir.py"),
    Carrier("lawvm.core.ir", "ProvisionTimeline", "src/lawvm/core/ir.py"),
    Carrier(
        "lawvm.core.timeline_consistency",
        "ConsistencyDivergence",
        "src/lawvm/core/timeline_consistency.py",
    ),
    Carrier(
        "lawvm.finland.fixed_term_expiry",
        "FixedTermDiagnostic",
        "src/lawvm/finland/fixed_term_expiry.py",
    ),
    Carrier(
        "lawvm.finland.fixed_term_expiry",
        "FixedTermExtraction",
        "src/lawvm/finland/fixed_term_expiry.py",
    ),
)


# Each allowlist key is "<dotted.module>:<ClassName>" -> justification.
#
# BLOCKED-MIGRATION entries must, in their justification text, name at
# least one consumer-site witness in the form "<path>:<line>" so removing
# the entry is bounded by a concrete migration task. Each entry's text
# is verified by ``test_blocked_migration_allowlist_names_consumer_sites``.

_BLOCKED_MIGRATION: dict[str, str] = {
    # --- BLOCKED-MIGRATION ---
    # Task: "Frozen/slots discipline for legal-state carriers (§1.9)".
    # These two carriers embody the §1.9 enforcement target, but freezing
    # them now would break in-place mutation consumers that live in files
    # OUTSIDE the strict scope of that task — chiefly
    # finland/replay_products.py (forbidden WIP) and core/timeline*.py
    # (cross-cutting consumers). Migration to functional
    # ``dataclasses.replace`` rebuilds is a follow-up coordinated task;
    # this allowlist entry keeps the lint curtain alive for the three
    # in-scope carriers that DID freeze, until that migration lands.
    "lawvm.core.ir:ProvisionVersion": (
        "BLOCKED-MIGRATION: in-place writes to .expires and .variant_kind "
        "at src/lawvm/core/timeline_temporal_events.py:232-233 "
        "(_cap_substantive_versions_at). "
        "Migrate those writes to dataclasses.replace and remove this "
        "allowlist entry to freeze the class."
    ),
    "lawvm.core.ir:ProvisionTimeline": (
        "BLOCKED-MIGRATION: in-place mutation of .versions list at "
        "src/lawvm/core/timeline.py:357,665,1063; "
        "src/lawvm/core/timeline_lineage.py:1160,1165; "
        "src/lawvm/core/timeline_temporal_events.py:326,400; "
        "src/lawvm/core/timeline_consistency.py:35,57,70,79,92; "
        "src/lawvm/finland/replay_products.py:2048,2062 (forbidden WIP). "
        "Migrate all sites to dataclasses.replace(timeline, versions=...) "
        "and remove this allowlist entry to freeze the class."
    ),
}


_GENUINELY_MUTABLE: dict[str, str] = {
    # Empty as of this task — every in-scope §1.9 carrier is a legal-state
    # or evidence-plane carrier that should be immutable at the phase
    # boundary. Accumulator sinks / mutable per-call contexts go here when
    # the curtain is widened to other files.
}


# --- AST scan helpers -------------------------------------------------------


def _is_dataclass_decorator(node: ast.expr) -> bool:
    """True for ``@dataclass`` / ``@dataclass(...)`` / ``@dataclasses.dataclass``."""
    func = node.func if isinstance(node, ast.Call) else node
    return (
        isinstance(func, ast.Name) and func.id == "dataclass"
    ) or (
        isinstance(func, ast.Attribute) and func.attr == "dataclass"
    )


def _decorator_kwargs(node: ast.expr) -> dict[str, ast.expr]:
    if isinstance(node, ast.Call):
        return {kw.arg: kw.value for kw in node.keywords if kw.arg is not None}
    return {}


def _is_true_value(value: Optional[ast.expr]) -> bool:
    """Return True when ``value`` literal-evaluates to True.

    Recognises ``True``, ``1``, and other truthy ``ast.Constant`` values,
    plus the ``True`` name (which appears when ``from __future__ import
    annotations`` is mixed with old-style ``frozen=True`` kwargs).
    """
    if value is None:
        return False
    if isinstance(value, ast.Constant):
        return bool(value.value)
    if isinstance(value, ast.Name) and value.id == "True":
        return True
    return False


def _extract_dataclass_decoration(
    cls_node: ast.ClassDef,
) -> Optional[tuple[bool, bool]]:
    """Return ``(frozen, slots)`` for the first ``@dataclass`` on cls, or None."""
    for dec in cls_node.decorator_list:
        if not _is_dataclass_decorator(dec):
            continue
        kwargs = _decorator_kwargs(dec)
        return (
            _is_true_value(kwargs.get("frozen")),
            _is_true_value(kwargs.get("slots")),
        )
    return None


def _read_carrier_decoration(carrier: Carrier) -> tuple[bool, bool]:
    """Read the ``(frozen, slots)`` flags from ``carrier``'s @dataclass."""
    path = _REPO_ROOT / carrier.file
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != carrier.cls:
            continue
        result = _extract_dataclass_decoration(node)
        if result is not None:
            return result
    raise AssertionError(
        f"carrier {carrier.module}:{carrier.cls} not found in {carrier.file}; "
        "did the class move or rename? Update _IN_SCOPE_CARRIERS."
    )


# --- Tests ------------------------------------------------------------------


def test_in_scope_carriers_are_frozen_and_slot() -> None:
    """Every in-scope §1.9 carrier is ``@dataclass(frozen=True, slots=True)``.

    Allowlist exceptions:
      - ``_BLOCKED_MIGRATION``: typed carrier frozen-pending-consumer-migration.
      - ``_GENUINELY_MUTABLE``: mutable runtime-state by design.

    A NEW violation here is a regression: either freeze+slot the class or
    register it (with a consumer-site witness) in ``_BLOCKED_MIGRATION``.
    """
    bad: list[str] = []
    for carrier in _IN_SCOPE_CARRIERS:
        frozen, slots = _read_carrier_decoration(carrier)
        if frozen and slots:
            continue
        key = f"{carrier.module}:{carrier.cls}"
        if key in _BLOCKED_MIGRATION:
            continue
        if key in _GENUINELY_MUTABLE:
            continue
        bad.append(
            f"{carrier.file}: {carrier.cls} "
            f"@dataclass(frozen={frozen}, slots={slots}) must be "
            f"frozen=True AND slots=True per AGENTS.md §1.9."
        )
    assert not bad, (
        "§1.9 frozen/slots discipline violations:\n  "
        + "\n  ".join(bad)
        + "\n\nFix: add `frozen=True, slots=True` to the @dataclass decorator,"
        + " or register the carrier in _BLOCKED_MIGRATION "
        + "(with a file:line consumer-site witness) / _GENUINELY_MUTABLE."
    )


# Match "<path>.py:<line>" or "<path>.py:<line>-<line>" possibly with a
# trailing comma-separated list of further "<line>" tokens. Used to
# police that BLOCKED_MIGRATION justifications name the consumer sites
# that block freezing the carrier.
_WITNESS_PATTERN = re.compile(r"\b\S+\.py:\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*")


def test_blocked_migration_allowlist_names_consumer_sites() -> None:
    """Each ``_BLOCKED_MIGRATION`` entry must name at least one
    ``<path>:<line>`` consumer site, so removing the entry is bounded by
    an audit task — not forgetfulness."""
    for key, justification in _BLOCKED_MIGRATION.items():
        assert _WITNESS_PATTERN.search(justification), (
            f"_BLOCKED_MIGRATION entry {key!r} must name at least one "
            f"'<path>:<line>' consumer site in its justification "
            f"(got: {justification!r})."
        )
        # Confirm the carrier is currently in-scope (otherwise the
        # allowlist entry is stale and should be deleted).
        assert any(
            f"{c.module}:{c.cls}" == key for c in _IN_SCOPE_CARRIERS
        ), (
            f"_BLOCKED_MIGRATION entry {key!r} does not match any "
            "in-scope carrier — delete this stale entry."
        )
