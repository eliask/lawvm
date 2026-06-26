"""Reference lints AS GRAPH QUERIES over the assembled Legal Surface Graph.

Pro r5 §D6 ("lints are graph queries", not lens outputs) + §D7 (firewall) +
the "Phase 5 — H1 reference lints" entry. Each pass here is a
:class:`lawvm.core.legal_surface_lints.SurfaceLintPass`: it reads the ASSEMBLED
graph (``reference_resolution`` / ``reference_expr`` nodes and their
``resolution_of`` / ``refers_to`` / ``has_candidate`` / ``unresolved_because``
edges produced by the H1 ``ReferenceLens``) and emits typed
:class:`SurfaceLint` observations.

Authority discipline (§D6/§D7): every lint is a SOURCE-SURFACE static-analysis
observation, NEVER a legal conclusion (``legal_conclusion=False``,
``surface_only=True``, ``replay_authorized`` impossible) and every lint declares
``forbidden_overclaims`` — the legal readings it must never be mistaken for. A
reference lint says "the source text contains a citation that did not resolve /
is ambiguous / points at a repealed target" — it NEVER says "this reference is
legally invalid" or "the citing provision is void". Messages are
self-evidencing: they embed the citation surface text so the finding is auditable
from the message alone (AGENTS.md §1.8).

Each pass and the graph query it runs:

  * ``reference.broken_reference``    — reference_resolution with status
                                        ``broken`` (target existed at citation
                                        time but is repealed/renumbered).
  * ``reference.open_reference``      — reference_resolution with status ``open``
                                        (vague catch-all naming no target; this
                                        is expected, hence severity info).
  * ``reference.unresolved_by_name``  — reference_resolution with status
                                        ``statute_only`` whose citing expr was a
                                        by-name / EU-nickname reference (a
                                        registry coverage gap, not a drafting
                                        defect).
  * ``reference.ambiguous_reference`` — reference_resolution with status
                                        ``ambiguous`` (multiple candidates;
                                        never silently picked, §D5).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from lawvm.core.legal_surface_graph import (
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceNode,
)
from lawvm.core.legal_surface_lints import SurfaceLint

JURISDICTION = "fi"

LINT_BROKEN_REFERENCE = "reference.broken_reference"
LINT_OPEN_REFERENCE = "reference.open_reference"
LINT_UNRESOLVED_BY_NAME = "reference.unresolved_by_name"
LINT_AMBIGUOUS_REFERENCE = "reference.ambiguous_reference"

# rule_id values (which query produced the lint). Closed set.
RULE_BROKEN_REFERENCE = "fi.lint.reference.broken_reference"
RULE_OPEN_REFERENCE = "fi.lint.reference.open_reference"
RULE_UNRESOLVED_BY_NAME = "fi.lint.reference.unresolved_by_name"
RULE_AMBIGUOUS_REFERENCE = "fi.lint.reference.ambiguous_reference"

# By-name / nickname target-id prefixes a ``statute_only`` outcome carries when
# the citing surface named a STATUTE by name (no numeric id) — the registry
# coverage gap this lint is about. A numeric ``statute_only`` (id known, exact
# provision pending) is NOT a by-name miss and is left alone.
_BY_NAME_TARGET_PREFIXES: tuple[str, ...] = ("fi-name:", "eu-nickname:")

# The conclusions a reference lint must NEVER be read as making. A surface lint
# about a citation's resolution status says nothing about legal validity or
# consequence (§D6/§D7).
_FORBIDDEN_OVERCLAIMS: tuple[str, ...] = (
    "this reference is legally invalid",
    "the citing provision is void",
    "the statute is legally defective",
    "any legal consequence follows",
    "the provision is unenforceable",
)


def _mint_lint_id(*parts: str) -> str:
    """Deterministic lint id over the lint kind + its subject/support node ids."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _surface_of(node: SurfaceNode) -> str:
    """Best self-evidencing citation surface for a node's message."""
    val = node.payload.get("surface_text")
    if isinstance(val, str) and val.strip():
        return val
    return node.node_id


def _refs_of(*nodes: SurfaceNode) -> tuple[SourceSpanRef, ...]:
    return tuple(n.source_ref for n in nodes if n.source_ref is not None)


@dataclass(frozen=True, slots=True)
class _GraphIndex:
    """Precomputed adjacency the reference queries share (one pass over edges)."""

    nodes: dict[str, SurfaceNode]
    # reference_resolution node id -> reference_expr node ids it resolves
    # (outgoing ``resolution_of``).
    resolution_of_exprs: dict[str, list[str]]
    # reference_resolution node ids that ASSERTED a concrete target (outgoing
    # ``refers_to``) — a resolution that committed an identity, never a miss.
    resolutions_with_refers_to: frozenset[str]


def _index(graph: LegalSurfaceGraph) -> _GraphIndex:
    nodes = dict(graph.nodes)
    resolution_of: dict[str, list[str]] = {}
    refers_to_src: set[str] = set()
    for edge in graph.edges:
        if edge.edge_kind == "resolution_of":
            resolution_of.setdefault(edge.src, []).append(edge.dst)
        elif edge.edge_kind == "refers_to":
            refers_to_src.add(edge.src)
    return _GraphIndex(
        nodes=nodes,
        resolution_of_exprs=resolution_of,
        resolutions_with_refers_to=frozenset(refers_to_src),
    )


def _resolved_to_target(index: _GraphIndex, res_id: str, res: SurfaceNode) -> bool:
    """True iff this resolution actually committed a concrete target.

    The reference_resolution NODE status mirrors the citation's cite_confidence,
    NOT the registry outcome — a by-name ``statute_only`` node that the registry
    later resolved still carries node status ``statute_only`` but asserts a
    ``refers_to`` edge / a ``work_id`` payload. Such a resolution is NOT a miss.
    """
    if res_id in index.resolutions_with_refers_to:
        return True
    work_id = res.payload.get("work_id")
    if isinstance(work_id, str) and work_id:
        return True
    resolution_status = res.payload.get("resolution_status")
    return resolution_status in ("resolved", "unchanged")


def _resolutions(index: _GraphIndex) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        (
            (nid, n)
            for nid, n in index.nodes.items()
            if n.node_kind == "reference_resolution"
        ),
        key=lambda kv: kv[0],
    )


def _expr_for(index: _GraphIndex, resolution_id: str) -> tuple[str, SurfaceNode] | None:
    """The reference_expr a resolution resolves (first by id; deterministic)."""
    expr_ids = sorted(index.resolution_of_exprs.get(resolution_id, []))
    for expr_id in expr_ids:
        expr = index.nodes.get(expr_id)
        if expr is not None and expr.node_kind == "reference_expr":
            return expr_id, expr
    return None


def _is_by_name_target(expr: SurfaceNode) -> bool:
    """True iff the citing expr named a statute by name (registry-gap candidate).

    The discriminator is the placeholder target id the by-name / EU-nickname
    recognizers mint: ``fi-name:<name>`` / ``eu-nickname:<surface>``. A numeric
    statute id means the act identity is already known (not a registry gap).
    """
    target_id = expr.payload.get("target_id")
    return isinstance(target_id, str) and target_id.startswith(_BY_NAME_TARGET_PREFIXES)


# ── Lint passes (one graph query each) ───────────────────────────────────────


class BrokenReferenceLintPass:
    """``reference.broken_reference``: reference_resolution with status broken."""

    lint_pass_id: str = "fi.lint.reference.broken_reference"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for res_id, res in _resolutions(index):
            if res.status != "broken":
                continue
            expr_pair = _expr_for(index, res_id)
            support = (expr_pair[0],) if expr_pair is not None else ()
            surface = _surface_of(expr_pair[1] if expr_pair is not None else res)
            support_nodes = (expr_pair[1],) if expr_pair is not None else ()
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_BROKEN_REFERENCE, res_id),
                    lint_kind=LINT_BROKEN_REFERENCE,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_BROKEN_REFERENCE,
                    severity="warning",
                    subject_node_id=res_id,
                    support_node_ids=support,
                    source_refs=_refs_of(res, *support_nodes),
                    message=(
                        f"reference {surface!r} points at a target that is "
                        f"repealed or renumbered after the citation "
                        f"(broken reference)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class OpenReferenceLintPass:
    """``reference.open_reference``: reference_resolution with status open."""

    lint_pass_id: str = "fi.lint.reference.open_reference"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for res_id, res in _resolutions(index):
            if res.status != "open":
                continue
            expr_pair = _expr_for(index, res_id)
            support = (expr_pair[0],) if expr_pair is not None else ()
            surface = _surface_of(expr_pair[1] if expr_pair is not None else res)
            support_nodes = (expr_pair[1],) if expr_pair is not None else ()
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_OPEN_REFERENCE, res_id),
                    lint_kind=LINT_OPEN_REFERENCE,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_OPEN_REFERENCE,
                    severity="info",
                    subject_node_id=res_id,
                    support_node_ids=support,
                    source_refs=_refs_of(res, *support_nodes),
                    message=(
                        f"reference {surface!r} is a vague catch-all that names "
                        f"no concrete target (open reference)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class StatuteOnlyMissLintPass:
    """``reference.unresolved_by_name``: statute_only resolution of a by-name expr."""

    lint_pass_id: str = "fi.lint.reference.unresolved_by_name"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for res_id, res in _resolutions(index):
            if res.status != "statute_only":
                continue
            if _resolved_to_target(index, res_id, res):
                continue  # the registry resolved it; not a coverage gap
            expr_pair = _expr_for(index, res_id)
            if expr_pair is None or not _is_by_name_target(expr_pair[1]):
                continue  # numeric-id statute_only is not a registry coverage gap
            expr_id, expr = expr_pair
            surface = _surface_of(expr)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_UNRESOLVED_BY_NAME, res_id),
                    lint_kind=LINT_UNRESOLVED_BY_NAME,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_UNRESOLVED_BY_NAME,
                    severity="info",
                    subject_node_id=res_id,
                    support_node_ids=(expr_id,),
                    source_refs=_refs_of(res, expr),
                    message=(
                        f"reference {surface!r} names a statute by name that the "
                        f"registry could not resolve to an identifier "
                        f"(unresolved by-name reference; registry coverage gap)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class AmbiguousReferenceLintPass:
    """``reference.ambiguous_reference``: reference_resolution status ambiguous."""

    lint_pass_id: str = "fi.lint.reference.ambiguous_reference"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for res_id, res in _resolutions(index):
            if res.status != "ambiguous":
                continue
            expr_pair = _expr_for(index, res_id)
            support = (expr_pair[0],) if expr_pair is not None else ()
            surface = _surface_of(expr_pair[1] if expr_pair is not None else res)
            support_nodes = (expr_pair[1],) if expr_pair is not None else ()
            candidates = res.payload.get("candidates")
            count = len(candidates) if isinstance(candidates, list) else None
            count_str = f"{count} " if isinstance(count, int) else ""
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_AMBIGUOUS_REFERENCE, res_id),
                    lint_kind=LINT_AMBIGUOUS_REFERENCE,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_AMBIGUOUS_REFERENCE,
                    severity="warning",
                    subject_node_id=res_id,
                    support_node_ids=support,
                    source_refs=_refs_of(res, *support_nodes),
                    message=(
                        f"reference {surface!r} matches {count_str}candidate "
                        f"targets; none was picked (ambiguous reference)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


def reference_lint_passes() -> tuple[
    BrokenReferenceLintPass,
    OpenReferenceLintPass,
    StatuteOnlyMissLintPass,
    AmbiguousReferenceLintPass,
]:
    """The full ordered set of reference lint passes."""
    return (
        BrokenReferenceLintPass(),
        OpenReferenceLintPass(),
        StatuteOnlyMissLintPass(),
        AmbiguousReferenceLintPass(),
    )


__all__ = [
    "AmbiguousReferenceLintPass",
    "BrokenReferenceLintPass",
    "LINT_AMBIGUOUS_REFERENCE",
    "LINT_BROKEN_REFERENCE",
    "LINT_OPEN_REFERENCE",
    "LINT_UNRESOLVED_BY_NAME",
    "OpenReferenceLintPass",
    "StatuteOnlyMissLintPass",
    "reference_lint_passes",
]
