"""SurfaceLint type, lint-pass protocol, and a deterministic lint runner.

Authoritative design: ``notes_internal/pro_on_fi_theory_grammar5.txt`` §D6.

Lints are DERIVED graph-query results, not lens outputs. Core owns the
``SurfaceLint`` type and the query harness; Finland supplies the lint
definitions (Phase 5+). Phase 0 ships the harness only — no lint definitions.

Authority discipline (§D6/§D7): a lint is a *source-surface static-analysis*
observation, never a legal conclusion or replay authority. The type therefore
defaults:

    legal_conclusion = False
    replay_authorized = False
    surface_only      = True

and every lint must carry ``forbidden_overclaims`` naming the conclusions it
must never be read as making (e.g. "the statute is legally defective").
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from lawvm.core.legal_surface_graph import LegalSurfaceGraph, SourceSpanRef

LINT_SEVERITIES: frozenset[str] = frozenset({"info", "warning", "blocker", "bug"})
LINT_STATUSES: frozenset[str] = frozenset({"active", "suppressed", "not_applicable"})


class SurfaceLintError(Exception):
    """A lint pass produced an invalid SurfaceLint. Typed; never silent."""


@dataclass(frozen=True, slots=True)
class SurfaceLint:
    """A derived surface-static-analysis observation over the graph (§D6).

    NOT a legal conclusion and NOT replay authority — see the firewall defaults.
    """

    lint_id: str
    lint_kind: str
    jurisdiction: str
    rule_id: str
    severity: str  # info | warning | blocker | bug
    subject_node_id: str
    support_node_ids: tuple[str, ...]
    source_refs: tuple[SourceSpanRef, ...]
    message: str
    lint_status: Literal["active", "suppressed", "not_applicable"]
    surface_only: bool = True
    legal_conclusion: bool = False
    replay_authorized: bool = False
    forbidden_overclaims: tuple[str, ...] = ()


@runtime_checkable
class SurfaceLintPass(Protocol):
    """A registered graph-query lint pass (§D6).

    ``surface_only`` must be declared True: the pass certifies that its lints
    are source-surface observations, never legal/replay claims.
    """

    lint_pass_id: str
    jurisdiction: str | None
    surface_only: bool

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]: ...


def _validate_lint(lint: SurfaceLint, *, origin: str) -> None:
    if lint.severity not in LINT_SEVERITIES:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has unknown severity {lint.severity!r}; "
            f"allowed={sorted(LINT_SEVERITIES)}"
        )
    if lint.lint_status not in LINT_STATUSES:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has unknown status {lint.lint_status!r}; "
            f"allowed={sorted(LINT_STATUSES)}"
        )
    # Firewall (§D6/§D7): a lint can never claim legal/replay authority.
    if lint.replay_authorized:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has replay_authorized=True; "
            f"surface lints can never authorize replay (§D7)"
        )
    if lint.legal_conclusion:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has legal_conclusion=True; "
            f"surface lints are static-analysis observations, not legal conclusions (§D6)"
        )
    if not lint.surface_only:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has surface_only=False; "
            f"surface lints must be surface_only (§D6)"
        )
    if not lint.forbidden_overclaims:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} must declare forbidden_overclaims "
            f"(the conclusions it must never be read as making) (§D6)"
        )
    if not lint.subject_node_id:
        raise SurfaceLintError(
            f"{origin}: lint {lint.lint_id!r} has empty subject_node_id"
        )
    # subject/support node-id membership is validated against the graph in
    # run_lint_passes (the only place with both the lint and the graph).


@dataclass(frozen=True, slots=True)
class LintRunReport:
    """Result of running lint passes over a graph."""

    lints: tuple[SurfaceLint, ...]


def run_lint_passes(
    graph: LegalSurfaceGraph,
    lint_passes: tuple[SurfaceLintPass, ...],
) -> LintRunReport:
    """Run lint passes in declared order; validate each lint; fail loud.

    Subject/support node ids are validated against the graph here (the runner is
    the only place with both the lint and the graph). Lints are returned sorted
    by ``lint_id`` for determinism.
    """
    collected: dict[str, SurfaceLint] = {}
    node_ids = frozenset(graph.nodes)
    for lint_pass in lint_passes:
        origin = f"lint_pass {lint_pass.lint_pass_id!r}"
        if not getattr(lint_pass, "surface_only", False):
            raise SurfaceLintError(
                f"{origin}: lint passes must declare surface_only=True (§D6)"
            )
        for lint in lint_pass.run(graph):
            _validate_lint(lint, origin=origin)
            if lint.subject_node_id not in node_ids:
                raise SurfaceLintError(
                    f"{origin}: lint {lint.lint_id!r} subject_node_id "
                    f"{lint.subject_node_id!r} is not in the graph"
                )
            missing = [n for n in lint.support_node_ids if n not in node_ids]
            if missing:
                raise SurfaceLintError(
                    f"{origin}: lint {lint.lint_id!r} support_node_ids not in graph: {missing}"
                )
            existing = collected.get(lint.lint_id)
            if existing is not None and existing != lint:
                raise SurfaceLintError(
                    f"{origin}: lint id collision with divergent content for {lint.lint_id!r}"
                )
            collected[lint.lint_id] = lint
    return LintRunReport(lints=tuple(collected[lid] for lid in sorted(collected)))
