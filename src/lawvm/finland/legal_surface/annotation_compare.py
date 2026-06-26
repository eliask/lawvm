"""Grammar-vs-annotation comparison pass (grammar7 §13-B, §14 NEUTRAL).

This edge pass contrasts the GRAMMAR-induced reference set (the ``reference_expr``
nodes the H1 reference lens mints) against the ANNOTATION-witness surface (the
``annotation_reference_witness`` nodes the annotation-witness lens mints), per
statute, and emits one ``grammar_annotation_compared`` edge per comparison
carrying a NEUTRAL verdict in ``payload["comparison_status"]``.

THE SEVEN NEUTRAL STATUSES (grammar7 §13-B / §4):

  both_same_target            — grammar mention and ``<ref>`` witness overlap in
                                source span AND resolve to the same target.
  both_same_span_diff_target  — same source span, but divergent targets (the
                                grammar and the markup disagree on what the
                                citation points at).
  both_same_target_diff_span  — same target, but the matched spans differ (the
                                two surfaces name the same act at different
                                offsets).
  grammar_only                — a grammar mention with no overlapping ``<ref>``
                                witness (self-edge on the reference_expr).
  annotation_only             — a ``<ref>`` witness with no overlapping grammar
                                mention (self-edge on the witness).
  both_present_noncomparable  — both present and span-matched, but the comparison
                                cannot be decided (e.g. an unparseable href → no
                                target to compare, or neither side carries a
                                comparable target key).

PROVENANCE NOTE: at the GRAPH level the "grammar" side is the production
``reference_expr`` set, which for the explicit_id family still CONSUMES ``<ref>``
(the §10-not-yet-retired dependence — see the reference lens). So for explicit_id
this pass is closer to an annotation-vs-annotation consistency check (it catches
span/target-granularity divergences). For the text-only families (by_name /
internal / eu / treaty / vague) the ``reference_expr`` set is purely
grammar-derived, so the contrast there IS grammar-vs-annotation. The RIGOROUS
grammar-text-vs-annotation reliability measurement lives in
``annotation_witness_census`` (which drives the grammar side with
``ignore_annotations=True`` — the text lanes ALONE). This graph pass is the
per-statute QA affordance; the census is the corpus measurement.

CRUCIAL FRAMING (grammar7 §14): the statuses are a CONTRAST, never a conclusion.
``grammar_only`` is NOT an "annotation bug" — some grammar_only is genuine recall
the markup lacks, some is parser overreach. ``annotation_only`` is NOT a "parser
miss" — some is a real reference the grammar missed, some is an annotation the
grammar correctly declines. Adjudicating which side is right is a downstream,
per-case act; this pass only reports the delta. Every edge is surface_only and
candidate-status (the authority firewall, §D7): it is a QA affordance, never a
legal claim.

MATCHING: on the AUTHORITATIVE BYTE SPAN both node kinds carry in their payload
(``source_span_byte_offset`` / ``source_span_len`` into the same ``xml_bytes``).
Byte spans are the reliable shared coordinate — the reference lens often carries
only a degenerate char ``source_ref`` (the byte origin rides the payload), and
the witness lens does likewise. Two spans MATCH when they overlap. Each grammar
mention is matched to at most one witness (greedy, lowest byte offset first) and
vice versa; the leftover on each side is the one-sided bucket. Deterministic:
both sides are sorted by (byte_offset, node_id) before pairing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from lawvm.core.legal_surface_graph import LegalSurfaceGraph, SurfaceNode
from lawvm.core.legal_surface_lens import SurfaceEdgeSeed

PASS_ID = "fi.annotation_compare.v0"
RULE_COMPARE = "fi.annotation_compare.v0.grammar_annotation_compared"

# The seven NEUTRAL comparison statuses (grammar7 §13-B). Closed set.
COMPARISON_STATUSES: frozenset[str] = frozenset(
    {
        "both_same_target",
        "both_same_span_diff_target",
        "both_same_target_diff_span",
        "grammar_only",
        "annotation_only",
        "both_present_noncomparable",
    }
)


@dataclass(frozen=True, slots=True)
class _SpanNode:
    """A grammar mention or annotation witness reduced to its byte span + target."""

    node_id: str
    byte_offset: Optional[int]
    byte_len: int
    target_key: Optional[str]  # None when no comparable target (unparsed/open)

    @property
    def byte_end(self) -> Optional[int]:
        if self.byte_offset is None:
            return None
        return self.byte_offset + self.byte_len

    def overlaps(self, other: "_SpanNode") -> bool:
        if self.byte_offset is None or other.byte_offset is None:
            return False
        s_end = self.byte_end
        o_end = other.byte_end
        assert s_end is not None and o_end is not None
        # Overlap (touching counts) of two byte intervals.
        return self.byte_offset < o_end and other.byte_offset < s_end


def _as_int(value: object) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _grammar_target_key(payload: Mapping[str, object]) -> Optional[str]:
    """Comparable target identity for a grammar reference_expr, or None.

    Uses the resolved provision ref string when present, else the bare target id.
    None when the grammar named no concrete target (vague/open) — then there is
    nothing to compare a witness target against (→ noncomparable on a span match).
    """
    prov = payload.get("target_provision_ref")
    if isinstance(prov, str) and prov.strip():
        return prov.strip()
    tid = payload.get("target_id")
    if isinstance(tid, str) and tid.strip():
        return tid.strip()
    return None


def _witness_target_key(payload: Mapping[str, object]) -> Optional[str]:
    """Comparable target identity for an annotation witness, or None.

    Combines the href-resolved statute id and provision path. None when the href
    did not parse (``parsed_ok`` False / no target) — nothing to compare.
    """
    tid = payload.get("target_id")
    if not (isinstance(tid, str) and tid.strip()):
        return None
    section = payload.get("target_section")
    if isinstance(section, str) and section.strip():
        return f"{tid.strip()}#{section.strip()}"
    return tid.strip()


def _span_node(node: SurfaceNode, *, target_key: Optional[str]) -> _SpanNode:
    return _SpanNode(
        node_id=node.node_id,
        byte_offset=_as_int(node.payload.get("source_span_byte_offset")),
        byte_len=_as_int(node.payload.get("source_span_len")) or 0,
        target_key=target_key,
    )


def _grammar_nodes(graph: LegalSurfaceGraph) -> list[_SpanNode]:
    out: list[_SpanNode] = []
    for nid, n in graph.nodes.items():
        if n.node_kind != "reference_expr":
            continue
        out.append(_span_node(n, target_key=_grammar_target_key(n.payload)))
    out.sort(key=lambda s: ((s.byte_offset if s.byte_offset is not None else -1), s.node_id))
    return out


def _witness_nodes(graph: LegalSurfaceGraph) -> list[_SpanNode]:
    out: list[_SpanNode] = []
    for nid, n in graph.nodes.items():
        if n.node_kind != "annotation_reference_witness":
            continue
        out.append(_span_node(n, target_key=_witness_target_key(n.payload)))
    out.sort(key=lambda s: ((s.byte_offset if s.byte_offset is not None else -1), s.node_id))
    return out


def _two_sided_status(g: _SpanNode, w: _SpanNode) -> str:
    """Verdict for a matched grammar/witness pair (span already overlaps).

    same-target → also report whether the spans coincide exactly (diff_span when
    they merely overlap); divergent targets → both_same_span_diff_target;
    no comparable target on either side → both_present_noncomparable.
    """
    if g.target_key is None or w.target_key is None:
        return "both_present_noncomparable"
    if g.target_key == w.target_key:
        same_span = (
            g.byte_offset == w.byte_offset and g.byte_len == w.byte_len
        )
        return "both_same_target" if same_span else "both_same_target_diff_span"
    return "both_same_span_diff_target"


def _match(
    grammar: list[_SpanNode], witnesses: list[_SpanNode]
) -> tuple[list[tuple[_SpanNode, _SpanNode]], list[_SpanNode], list[_SpanNode]]:
    """Greedy one-to-one byte-span matching of grammar mentions ↔ witnesses.

    Deterministic: both lists are pre-sorted by (byte_offset, node_id). Each
    witness is consumed at most once. Returns (pairs, grammar_only, annot_only).
    """
    used_w: set[str] = set()
    pairs: list[tuple[_SpanNode, _SpanNode]] = []
    g_only: list[_SpanNode] = []
    for g in grammar:
        matched: Optional[_SpanNode] = None
        for w in witnesses:
            if w.node_id in used_w:
                continue
            if g.overlaps(w):
                matched = w
                break
        if matched is None:
            g_only.append(g)
        else:
            used_w.add(matched.node_id)
            pairs.append((g, matched))
    a_only = [w for w in witnesses if w.node_id not in used_w]
    return pairs, g_only, a_only


def _edge(
    src: str, dst: str, comparison_status: str, *, extra: Mapping[str, object]
) -> SurfaceEdgeSeed:
    payload: dict[str, object] = {"comparison_status": comparison_status, **dict(extra)}
    return SurfaceEdgeSeed(
        edge_kind="grammar_annotation_compared",
        src_local=src,
        dst_local=dst,
        rule_id=RULE_COMPARE,
        # NEUTRAL contrast, never an asserted fact (§14, §D7).
        surface_edge_status="candidate",
        payload=payload,
    )


class GrammarAnnotationComparePass:
    """Edge pass: contrast grammar references vs annotation witnesses (§13-B).

    Satisfies the ``SurfaceEdgePass`` protocol. Emits ``grammar_annotation_
    compared`` edges with NEUTRAL ``comparison_status``. One-sided statuses
    (grammar_only / annotation_only) are self-edges on the single present node so
    the verdict is queryable as a graph edge for every reference on either side.
    """

    pass_id: str = PASS_ID
    reads_node_kinds: tuple[str, ...] = (
        "reference_expr",
        "annotation_reference_witness",
    )
    emits_edge_kinds: tuple[str, ...] = ("grammar_annotation_compared",)

    def run(self, graph: LegalSurfaceGraph) -> tuple[SurfaceEdgeSeed, ...]:
        grammar = _grammar_nodes(graph)
        witnesses = _witness_nodes(graph)
        if not grammar and not witnesses:
            return ()

        pairs, g_only, a_only = _match(grammar, witnesses)
        seeds: list[SurfaceEdgeSeed] = []

        for g, w in pairs:
            status = _two_sided_status(g, w)
            seeds.append(
                _edge(
                    g.node_id,
                    w.node_id,
                    status,
                    extra={
                        "grammar_target": g.target_key,
                        "annotation_target": w.target_key,
                        "grammar_byte_offset": g.byte_offset,
                        "annotation_byte_offset": w.byte_offset,
                    },
                )
            )
        for g in g_only:
            seeds.append(
                _edge(
                    g.node_id,
                    g.node_id,
                    "grammar_only",
                    extra={
                        "grammar_target": g.target_key,
                        "grammar_byte_offset": g.byte_offset,
                    },
                )
            )
        for w in a_only:
            seeds.append(
                _edge(
                    w.node_id,
                    w.node_id,
                    "annotation_only",
                    extra={
                        "annotation_target": w.target_key,
                        "annotation_byte_offset": w.byte_offset,
                    },
                )
            )
        return tuple(seeds)
