"""Definition SCOPE / SHADOWING lints AS GRAPH QUERIES over the Legal Surface
Graph (Pro r5 §D6 "lints are graph queries" + §D7 firewall).

Finnish statutes introduce defined terms with differing *scope shape*: a
definitions-section definition (``X:llä tarkoitetaan Y`` / ``Tässä laissa
tarkoitetaan…``) is the statute-wide canonical binding, whereas a parenthetical
or ``jäljempänä`` alias is a local naming convention introduced at a particular
site. When the SAME canonical term carries bindings of *different shape*, the
narrower-shape binding SHADOWS the broader-shape one — a structural observation
about the document's definitional layering, never a legal claim about which
definition "controls".

WHAT SCOPE IS ACTUALLY DERIVABLE FROM THE GRAPH
------------------------------------------------
The H2 binder (``finland/references/defined_terms.py``) does NOT differentiate
statute-scope from chapter-scope: it stamps every binding ``scope="statute"``
unconditionally (see its ``_SCOPE_STATUTE`` constant — there is no narrower
lexical scope it commits to). So an EXPLICIT chapter-vs-statute scope distinction
is NOT present in the graph today, and this lint does not fabricate one.

What IS derivable from graph node data (no XML re-parse) is the binding's
``binding_kind`` payload, which encodes the binding's *scope shape*:

  * ``tarkoitetaan``        — a definitions-section, statute-wide canonical
                              definition (the broad scope shape);
  * ``parenthetical_alias`` /
    ``jaljempana``          — a LOCAL alias introduced at a citation site (the
                              narrow scope shape).

This lint therefore scopes "shadowing" to: the same canonical term carries
bindings of ≥2 DISTINCT ``binding_kind`` values (a narrow-shape alias coexisting
with a broad-shape definition). It surfaces both binding spans + the term.

DISJOINTNESS FROM ``definition.duplicate_definition``
-----------------------------------------------------
``DuplicateDefinitionLintPass`` fires on a ``term_symbol_entity`` with >1
incoming ``defines_term`` edge — i.e. ANY term bound ≥2 times, REGARDLESS of
binding kind. To stay disjoint and never double-report the same structural fact,
this pass fires ONLY when the participating bindings span ≥2 DISTINCT
``binding_kind`` values. A pure exact-duplicate (≥2 bindings of the SAME kind,
e.g. two parenthetical aliases) is owned solely by the duplicate lint and does
NOT fire shadowing. The two lints partition the >1-binding space by
binding-kind-set cardinality: ``|kinds| == 1`` → duplicate only; ``|kinds| >= 2``
→ shadowing (the duplicate lint still also reports the raw multiplicity, but the
SHADOWING observation — the scope-shape difference — is unique to this pass).

Authority discipline (§D6/§D7): every lint is a SOURCE-SURFACE static-analysis
observation, NEVER a legal conclusion (``legal_conclusion=False``,
``surface_only=True``, ``replay_authorized`` impossible). Messages are
self-evidencing: they embed the term + each binding's scope shape so the finding
is auditable from the message alone. Deterministic ordering throughout.
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

LINT_SHADOWED_TERM = "definition.shadowed_term"
LINT_SCOPE_ANNOTATION = "definition.scope_annotation"

# rule_id values (which query produced the lint). Closed set.
RULE_SHADOWED_TERM = "fi.lint.definition.shadowed_term"
RULE_SCOPE_ANNOTATION = "fi.lint.definition.scope_annotation"

# Scope-shape classification of a binding_kind (the only scope signal the graph
# carries). Broad = a statute-wide definitions-section definition; narrow = a
# local alias introduced at a citation site. Used only to LABEL the observation
# in the self-evidencing message — never to claim which binding legally controls.
_BROAD_SHAPE_KINDS: frozenset[str] = frozenset({"tarkoitetaan"})
_NARROW_SHAPE_KINDS: frozenset[str] = frozenset(
    {"parenthetical_alias", "jaljempana"}
)


def _scope_shape(binding_kind: str | None) -> str:
    """Label a binding's scope shape from its ``binding_kind`` payload.

    A structural label only ("definitions-section definition" vs "local alias"),
    derived purely from graph node data. Unknown kinds get an explicit
    ``unknown`` label rather than a guessed scope (fail-loud, never fabricate).
    """
    if binding_kind in _BROAD_SHAPE_KINDS:
        return "statute_definition"
    if binding_kind in _NARROW_SHAPE_KINDS:
        return "local_alias"
    return "unknown"


# The conclusions a definition-scope lint must NEVER be read as making. A surface
# lint about a document's definitional layering says nothing about which
# definition legally controls or about legal validity (§D6).
_FORBIDDEN_OVERCLAIMS: tuple[str, ...] = (
    "the statute is legally invalid",
    "the statute is legally defective",
    "the narrower definition legally controls",
    "the broader definition legally controls",
    "either definition is overridden",
    "any legal consequence follows",
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
class _ScopeIndex:
    """Precomputed adjacency the scope queries share (one pass over edges)."""

    nodes: dict[str, SurfaceNode]
    # term_symbol_entity id -> binding node ids defining it (incoming defines_term)
    entity_in_bindings: dict[str, list[str]]


def _index(graph: LegalSurfaceGraph) -> _ScopeIndex:
    nodes = dict(graph.nodes)
    entity_in: dict[str, list[str]] = {}
    for edge in graph.edges:
        if edge.edge_kind == "defines_term":
            entity_in.setdefault(edge.dst, []).append(edge.src)
    return _ScopeIndex(nodes=nodes, entity_in_bindings=entity_in)


def _entities(index: _ScopeIndex) -> list[tuple[str, SurfaceNode]]:
    return sorted(
        (
            (nid, n)
            for nid, n in index.nodes.items()
            if n.node_kind == "term_symbol_entity"
        ),
        key=lambda kv: kv[0],
    )


def _binding_kind(node: SurfaceNode) -> str | None:
    val = node.payload.get("binding_kind")
    return val if isinstance(val, str) else None


def _declared_scope(node: SurfaceNode) -> str:
    """The binder's declared ``scope`` payload (currently always ``statute``)."""
    val = node.payload.get("scope")
    return val if isinstance(val, str) and val else "unknown"


def _shadowing_bindings(
    index: _ScopeIndex, entity_id: str
) -> list[str] | None:
    """The bindings of ``entity_id`` IFF they span ≥2 distinct binding kinds.

    Returns the deterministically-ordered binding node ids when the entity has
    bindings of ≥2 DISTINCT ``binding_kind`` values (the scope-shape difference
    that constitutes shadowing and keeps this pass disjoint from the duplicate
    lint), else ``None``.
    """
    binding_ids = sorted(set(index.entity_in_bindings.get(entity_id, [])))
    if len(binding_ids) <= 1:
        return None
    kinds = {_binding_kind(index.nodes[b]) for b in binding_ids}
    if len(kinds) <= 1:
        # Exact-duplicate territory (same scope shape) — owned by the duplicate
        # lint; never double-reported here.
        return None
    return binding_ids


class DefinitionShadowingLintPass:
    """``definition.shadowed_term``: one canonical term bound at ≥2 distinct
    scope shapes (a local alias coexisting with a statute-wide definition).

    Surface fact only: it observes that term T has bindings of differing scope
    shape at specific positions; it NEVER claims which binding legally controls.
    """

    lint_pass_id: str = "fi.lint.definition.shadowed_term"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for entity_id, entity in _entities(index):
            binding_ids = _shadowing_bindings(index, entity_id)
            if binding_ids is None:
                continue
            term = _term_of(entity)
            support = tuple(binding_ids)
            # Self-evidencing: term + each binding's scope shape + char position.
            shape_parts: list[str] = []
            for b in binding_ids:
                node = index.nodes[b]
                shape = _scope_shape(_binding_kind(node))
                ref = node.source_ref
                pos = f"char {ref.char_start}" if ref is not None else "char ?"
                shape_parts.append(f"{shape}@{pos}")
            lints.append(
                SurfaceLint(
                    lint_id=_mint_lint_id(LINT_SHADOWED_TERM, entity_id, *support),
                    lint_kind=LINT_SHADOWED_TERM,
                    jurisdiction=JURISDICTION,
                    rule_id=RULE_SHADOWED_TERM,
                    severity="info",
                    subject_node_id=entity_id,
                    support_node_ids=support,
                    source_refs=_refs_of(*(index.nodes[b] for b in binding_ids)),
                    message=(
                        f"term {term!r} has bindings at differing scope shapes "
                        f"({'; '.join(shape_parts)}); the narrower-shape binding "
                        f"shadows the broader-shape binding (surface observation)"
                    ),
                    status="active",
                    forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                )
            )
        return tuple(lints)


class DefinitionScopeAnnotationLintPass:
    """``definition.scope_annotation``: surface the declared scope + scope shape
    of each binding that participates in a shadowing set.

    A pure SURFACE OBSERVATION of the scope cue the graph carries (the binder's
    ``scope`` payload + the ``binding_kind`` scope shape) — derived only from
    graph node data, never re-parsed and never fabricated. Anchored to the
    shadowing finding so it documents each participating binding's scope rather
    than flooding every binding in the corpus with a constant value.
    """

    lint_pass_id: str = "fi.lint.definition.scope_annotation"
    jurisdiction: str | None = JURISDICTION
    surface_only: bool = True

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceLint, ...]:
        index = _index(graph)
        lints: list[SurfaceLint] = []
        for entity_id, _entity in _entities(index):
            binding_ids = _shadowing_bindings(index, entity_id)
            if binding_ids is None:
                continue
            for binding_id in binding_ids:
                node = index.nodes[binding_id]
                term = _term_of(node)
                shape = _scope_shape(_binding_kind(node))
                declared = _declared_scope(node)
                lints.append(
                    SurfaceLint(
                        lint_id=_mint_lint_id(LINT_SCOPE_ANNOTATION, binding_id),
                        lint_kind=LINT_SCOPE_ANNOTATION,
                        jurisdiction=JURISDICTION,
                        rule_id=RULE_SCOPE_ANNOTATION,
                        severity="info",
                        subject_node_id=binding_id,
                        support_node_ids=(),
                        source_refs=_refs_of(node),
                        message=(
                            f"binding of term {term!r} has declared scope "
                            f"{declared!r} and scope shape {shape!r} "
                            f"(surface observation)"
                        ),
                        status="active",
                        forbidden_overclaims=_FORBIDDEN_OVERCLAIMS,
                    )
                )
        return tuple(lints)


def definition_scope_lint_passes() -> tuple[
    DefinitionShadowingLintPass,
    DefinitionScopeAnnotationLintPass,
]:
    """The full ordered set of definition-scope lint passes."""
    return (
        DefinitionShadowingLintPass(),
        DefinitionScopeAnnotationLintPass(),
    )


__all__ = [
    "DefinitionScopeAnnotationLintPass",
    "DefinitionShadowingLintPass",
    "LINT_SCOPE_ANNOTATION",
    "LINT_SHADOWED_TERM",
    "RULE_SCOPE_ANNOTATION",
    "RULE_SHADOWED_TERM",
    "definition_scope_lint_passes",
]
