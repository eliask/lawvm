"""Definition lints AS GRAPH QUERIES over the assembled Legal Surface Graph.

Pro r5 §D6 ("lints are graph queries", not lens outputs) + §D7 (firewall). Each
pass here is a :class:`lawvm.core.legal_surface_lints.SurfaceLintPass`: it reads
the ASSEMBLED graph (definition_binding / term_use / term_symbol_entity nodes and
defines_term / uses_term / term_use_resolves_to edges produced by the H2
``DefinitionLens`` + ``DefinitionClosurePass``) and emits typed
:class:`SurfaceLint` observations.

Authority discipline (§D6/§D7): every lint is a SOURCE-SURFACE static-analysis
observation, NEVER a legal conclusion (``legal_conclusion=False``,
``surface_only=True``, ``replay_authorized`` impossible) and every lint declares
``forbidden_overclaims`` — the legal readings it must never be mistaken for.
Messages are self-evidencing: they embed the term so the finding is auditable
from the message alone (AGENTS.md §1.8).

Each pass and the graph query it runs:

  * ``definition.unbound_term``        — term_use with status ``open`` AND no
                                         outgoing ``uses_term`` / ``term_use_resolves_to``
                                         edge (a use with no reachable binding).
  * ``definition.dead_definition``     — definition_binding with NO incoming
                                         ``uses_term`` edge (a defined term never
                                         used).
  * ``definition.duplicate_definition``— a term_symbol_entity with >1 incoming
                                         ``defines_term`` edge (one term, many
                                         bindings).
  * ``definition.used_before_definition`` — a resolved term_use whose
                                         ``uses_term`` target binding's source
                                         span STARTS AFTER the use's span (the
                                         binding is defined later than the use).
  * ``definition.ambiguous_term_use``  — term_use with status ``ambiguous``.
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

LINT_UNBOUND_TERM = "definition.unbound_term"
LINT_DEAD_DEFINITION = "definition.dead_definition"
LINT_DUPLICATE_DEFINITION = "definition.duplicate_definition"
LINT_USED_BEFORE_DEFINITION = "definition.used_before_definition"
LINT_AMBIGUOUS_TERM_USE = "definition.ambiguous_term_use"

# rule_id values (which query produced the lint). Closed set.
RULE_UNBOUND_TERM = "fi.lint.definition.unbound_term"
RULE_DEAD_DEFINITION = "fi.lint.definition.dead_definition"
RULE_DUPLICATE_DEFINITION = "fi.lint.definition.duplicate_definition"
RULE_USED_BEFORE_DEFINITION = "fi.lint.definition.used_before_definition"
RULE_AMBIGUOUS_TERM_USE = "fi.lint.definition.ambiguous_term_use"

# The conclusions a definition lint must NEVER be read as making. A surface lint
# about a document's own definitional structure says nothing about legal validity
# or consequence (§D6).
_FORBIDDEN_OVERCLAIMS: tuple[str, ...] = (
    "the statute is legally invalid",
    "the statute is legally defective",
    "any legal consequence follows",
    "the provision is unenforceable",
)


def _mint_lint_id(*parts: str) -> str:
    """Deterministic lint id over the lint kind + its subject/support node ids."""
    return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()


def _term_of(node: SurfaceNode) -> str:
    """Best self-evidencing term/surface label for a node's message."""
    for key in ("term", "term_surface", "lemma"):
        val = node.payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return node.node_id


def _refs_of(*nodes: SurfaceNode) -> tuple[SourceSpanRef, ...]:
    return tuple(n.source_ref for n in nodes if n.source_ref is not None)


@dataclass(frozen=True, slots=True)
class _GraphIndex:
    """Precomputed adjacency the queries share (one pass over edges)."""

    nodes: dict[str, SurfaceNode]
    # term_use node id -> binding node ids it uses (intrinsic + cross-lens)
    use_out_bindings: dict[str, list[str]]
    # binding node id -> term_use node ids that use it (incoming uses_term)
    binding_in_uses: dict[str, list[str]]
    # term_symbol_entity id -> binding node ids defining it (incoming defines_term)
    entity_in_bindings: dict[str, list[str]]


def _index(graph: LegalSurfaceGraph) -> _GraphIndex:
    nodes = dict(graph.nodes)
    use_out: dict[str, list[str]] = {}
    binding_in: dict[str, list[str]] = {}
    entity_in: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_kind in ("uses_term", "term_use_resolves_to"):
            use_out.setdefault(edge.src, []).append(edge.dst)
            binding_in.setdefault(edge.dst, []).append(edge.src)
        elif edge.edge_kind == "defines_term":
            entity_in.setdefault(edge.dst, []).append(edge.src)
    return _GraphIndex(
        nodes=nodes,
        use_out_bindings=use_out,
        binding_in_uses=binding_in,
        entity_in_bindings=entity_in,
    )


def _uses(index: _GraphIndex) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        ((nid, n) for nid, n in index.nodes.items() if n.node_kind == "term_use"),
        key=lambda kv: kv[0],
    )


def _bindings(index: _GraphIndex) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        (
            (nid, n)
            for nid, n in index.nodes.items()
            if n.node_kind == "definition_binding"
        ),
        key=lambda kv: kv[0],
    )


def _entities(index: _GraphIndex) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        (
            (nid, n)
            for nid, n in index.nodes.items()
            if n.node_kind == "term_symbol_entity"
        ),
        key=lambda kv: kv[0],
    )


# ── Lint passes (one graph query each) ───────────────────────────────────────


class UnboundTermLintPass:
    """``definition.unbound_term``: open term_use with no resolving edge."""

    lint_pass_id: str = "fi.lint.definition.unbound_term"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for use_id, use in _uses(index):
            if use.status != "open":
                continue
            if index.use_out_bindings.get(use_id):
                continue  # has a resolving edge → not unbound
            term = _term_of(use)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_UNBOUND_TERM, use_id),
                    lint_kind=LINT_UNBOUND_TERM,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_UNBOUND_TERM,
                    severity="warning",
                    subject_node_id=use_id,
                    support_node_ids=(),
                    source_refs=_refs_of(use),
                    message=(
                        f"term {term!r} is used but has no definition reachable "
                        f"in this surface (open term use)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class DeadDefinitionLintPass:
    """``definition.dead_definition``: binding with no incoming uses_term."""

    lint_pass_id: str = "fi.lint.definition.dead_definition"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for binding_id, binding in _bindings(index):
            if index.binding_in_uses.get(binding_id):
                continue  # used at least once
            term = _term_of(binding)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_DEAD_DEFINITION, binding_id),
                    lint_kind=LINT_DEAD_DEFINITION,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_DEAD_DEFINITION,
                    severity="info",
                    subject_node_id=binding_id,
                    support_node_ids=(),
                    source_refs=_refs_of(binding),
                    message=(
                        f"definition of {term!r} is never used in this surface "
                        f"(dead definition)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class DuplicateDefinitionLintPass:
    """``definition.duplicate_definition``: entity with >1 defines_term in-edges."""

    lint_pass_id: str = "fi.lint.definition.duplicate_definition"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for entity_id, entity in _entities(index):
            binding_ids = sorted(set(index.entity_in_bindings.get(entity_id, [])))
            if len(binding_ids) <= 1:
                continue
            term = _term_of(entity)
            support = tuple(binding_ids)
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(
                        LINT_DUPLICATE_DEFINITION, entity_id, *support
                    ),
                    lint_kind=LINT_DUPLICATE_DEFINITION,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_DUPLICATE_DEFINITION,
                    severity="warning",
                    subject_node_id=entity_id,
                    support_node_ids=support,
                    source_refs=_refs_of(
                        *(index.nodes[b] for b in binding_ids)
                    ),
                    message=(
                        f"term {term!r} is defined {len(binding_ids)} times "
                        f"in this surface (duplicate definition)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class UsedBeforeDefinitionLintPass:
    """``definition.used_before_definition``: resolved use whose binding span
    STARTS after the use span."""

    lint_pass_id: str = "fi.lint.definition.used_before_definition"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for use_id, use in _uses(index):
            use_ref = use.source_ref
            if use_ref is None:
                continue
            for binding_id in index.use_out_bindings.get(use_id, []):
                binding = index.nodes.get(binding_id)
                if binding is None or binding.source_ref is None:
                    continue
                # CONSERVATIVE common-word guard: a ``tarkoitetaan`` binding is a
                # definitions-section definition. A term defined there and used in
                # the operative provisions is normal Finnish drafting, not an order
                # violation; flagging it floods USED_BEFORE_DEFINITION with common
                # words (``auto`` / ``käyttö`` / ``palvelu``). The lint is reserved
                # for ALIAS bindings (parenthetical / jäljempänä), where a local
                # short-name used before it is introduced IS a genuine order
                # violation (the canonical true positive).
                if binding.payload.get("binding_kind") == "tarkoitetaan":
                    continue
                if binding.source_ref.char_start > use_ref.char_start:
                    term = _term_of(use)
                    lints.append(
                        SurfaceLint(
                            lint_id=_mint_lint_id(
                                LINT_USED_BEFORE_DEFINITION, use_id, binding_id
                            ),
                            lint_kind=LINT_USED_BEFORE_DEFINITION,
                            jurisdiction=JURISDICTION,
                            rule_id=RULE_USED_BEFORE_DEFINITION,
                            severity="warning",
                            subject_node_id=use_id,
                            support_node_ids=(binding_id,),
                            source_refs=_refs_of(use, binding),
                            message=(
                                f"term {term!r} is used at char "
                                f"{use_ref.char_start} before its definition at "
                                f"char {binding.source_ref.char_start} "
                                f"(used before definition)"
                            ),
                            lint_status="active",
                            forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                        )
                    )
        return tuple(lints)


class AmbiguousTermUseLintPass:
    """``definition.ambiguous_term_use``: term_use with status ambiguous."""

    lint_pass_id: str = "fi.lint.definition.ambiguous_term_use"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for use_id, use in _uses(index):
            if use.status != "ambiguous":
                continue
            term = _term_of(use)
            count = use.payload.get("candidate_count")
            count_str = f"{count} " if isinstance(count, int) else ""
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_AMBIGUOUS_TERM_USE, use_id),
                    lint_kind=LINT_AMBIGUOUS_TERM_USE,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_AMBIGUOUS_TERM_USE,
                    severity="warning",
                    subject_node_id=use_id,
                    support_node_ids=(),
                    source_refs=_refs_of(use),
                    message=(
                        f"term {term!r} matches {count_str}definitions; "
                        f"cannot pick one (ambiguous term use)"
                    ),
                    lint_status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


def definition_lint_passes() -> tuple[
    UnboundTermLintPass,
    DeadDefinitionLintPass,
    DuplicateDefinitionLintPass,
    UsedBeforeDefinitionLintPass,
    AmbiguousTermUseLintPass,
]:
    """The full ordered set of definition lint passes."""
    return (
        UnboundTermLintPass(),
        DeadDefinitionLintPass(),
        DuplicateDefinitionLintPass(),
        UsedBeforeDefinitionLintPass(),
        AmbiguousTermUseLintPass(),
    )


__all__ = [
    "AmbiguousTermUseLintPass",
    "DeadDefinitionLintPass",
    "DuplicateDefinitionLintPass",
    "LINT_AMBIGUOUS_TERM_USE",
    "LINT_DEAD_DEFINITION",
    "LINT_DUPLICATE_DEFINITION",
    "LINT_UNBOUND_TERM",
    "LINT_USED_BEFORE_DEFINITION",
    "UnboundTermLintPass",
    "UsedBeforeDefinitionLintPass",
    "definition_lint_passes",
]
