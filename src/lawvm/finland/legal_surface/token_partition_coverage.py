"""The ``TokenPartitionCoverage`` + coverage-certifier (Pro ruling D2).

This module makes the per-provision construction parse FOREST
(:class:`~lawvm.finland.legal_surface.source_syntax_graph.SourceSyntaxGraph`) the
DETERMINISTIC-CORE TOTALITY CERTIFIER: a cheap, additive certificate over a
forest's token-coverage partition, plus a cross-check that every
:class:`~lawvm.core.legal_surface_graph.LegalSurfaceGraph` node was produced from
tokens the total parse ACCOUNTED FOR.

The reframe (Pro D2)
====================
The forest already computes a token-coverage partition (the L0 union-ownership
census, carried verbatim on :class:`SyntaxCoverage`). Pro ruling D2 settles the
vocabulary and the discipline:

  * the FOURTH class is a **FAILURE state, not an accepted bucket**. The four
    classes are::

        owned                — claimed by >=1 family construction
        benign_uninterpreted — unowned, no cheap legal signal (prose filler)
        typed_residual       — unowned, but inside an EXPLICIT typed residual span
        unowned_violation    — unowned, non-benign, carrying a cheap legal signal
                               (the L0 "silent" class, RENAMED — a VIOLATION)

  * the target invariant is ``unowned_violation == 0``. We are NOT there today
    (~0.79% of signal tokens). The certificate's JOB is to SURFACE that count as
    a violation to drive down — it does NOT assert 0 today and never silently
    re-buckets a violation away.

  * because the forest certifies TOTALITY, the lenses can stay the PRODUCERS:
    instead of forcing the forest to be the sole producer, we cross-check that
    every lens-produced graph node's source span is OWNED in the forest
    partition (the :func:`certify_graph_coverage` cross-check). A lens node whose
    span the total parse did NOT account for is a FINDING.

This composes the EXISTING census — it re-parses NOTHING. ``build_*`` takes an
already-assembled forest (or graph); the certificate is a pure projection of it.
Rendering mirrors the read-only viewer discipline
(:mod:`lawvm.tools.fi_parse_view`): pure functions taking the certificate and
returning text / dict, no side effects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping

from lawvm.core.legal_surface_graph import LegalSurfaceGraph
from lawvm.finland.legal_surface.source_syntax_graph import SourceSyntaxGraph

# ---------------------------------------------------------------------------
# The four Pro D2 classes (the CLOSED partition vocabulary).
# ---------------------------------------------------------------------------

#: Pro ruling D2: the four token-partition classes, in surface order. The fourth
#: (``unowned_violation``) is a FAILURE state, not an accepted bucket. This is the
#: Pro-named view of the L0 census buckets carried on
#: :class:`…source_syntax_graph.SyntaxCoverage`:
#:   owned                <- owned_tokens
#:   benign_uninterpreted <- benign_tokens
#:   typed_residual       <- residual_tokens
#:   unowned_violation    <- silent_tokens   (RENAMED — a violation, drive to 0)
PARTITION_CLASSES: tuple[str, str, str, str] = (
    "owned",
    "benign_uninterpreted",
    "typed_residual",
    "unowned_violation",
)

PartitionClass = Literal[
    "owned", "benign_uninterpreted", "typed_residual", "unowned_violation"
]

#: The forest construction-leaf node kinds whose char spans are OWNED (claimed by
#: >=1 family construction). Mirrors ``…source_syntax_graph._FAMILY_TO_NODE_KIND``
#: image set; kept here as the explicit owned-span source for span-level coverage.
_OWNED_LEAF_KINDS: frozenset[str] = frozenset(
    {
        "reference_np",
        "definition_entry",
        "temporal_phrase",
        "modal_predicate",
        "condition_clause",
        "exception_clause",
        "delegation_frame",
    }
)


# ---------------------------------------------------------------------------
# The certificate.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UnownedViolationToken:
    """One ``unowned_violation`` surface (self-evidencing — the no-silent-drop witness).

    Carries the verbatim offending span text + its exact char span, so a non-zero
    ``unowned_violation`` count is auditable down to the offending clause text
    (never an opaque count).

    Attributes:
        char_start: 0-based inclusive offset into the provision body text.
        char_end:   0-based exclusive offset.
        shape:      The cheap-legal-signal SHAPE (the grammar-growth rank key).
        text:       Verbatim offending span text.
        context:    A short verbatim window around the span.
    """

    char_start: int
    char_end: int
    shape: str
    text: str
    context: str


@dataclass(frozen=True, slots=True)
class TokenPartitionCoverage:
    """A typed certificate over ONE provision's forest token-coverage partition.

    A pure projection of an already-assembled forest's
    :class:`…source_syntax_graph.SyntaxCoverage` (it re-parses NOTHING) into the
    Pro D2 four-class vocabulary, plus the self-evidencing
    ``unowned_violation`` token list.

    Attributes:
        graph_id:         The forest ``graph_id`` this certifies (drift anchor).
        statute_id:       The provision/statute id (for reporting).
        total_tokens:     Non-whitespace (signal-bearing) tokens classified.
        owned:            Tokens claimed by >=1 family construction.
        benign_uninterpreted: Unowned tokens with no cheap legal signal.
        typed_residual:   Unowned tokens inside an explicit typed residual span.
        unowned_violation: Unowned, non-benign tokens carrying a cheap legal
                          signal. A FAILURE class (Pro D2) — target 0, never an
                          accepted bucket.
        violations:       The self-evidencing ``unowned_violation`` token spans
                          (verbatim text + offsets), surfaced from the forest's
                          residual witnesses.
        parse_status:     The forest's whole-provision ``parse_status``.
        family_token_counts: family_id -> tokens it owned (overlaps allowed).
    """

    graph_id: str
    statute_id: str
    total_tokens: int
    owned: int
    benign_uninterpreted: int
    typed_residual: int
    unowned_violation: int
    violations: tuple[UnownedViolationToken, ...]
    parse_status: str
    family_token_counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def partition_total(self) -> int:
        return (
            self.owned
            + self.benign_uninterpreted
            + self.typed_residual
            + self.unowned_violation
        )

    def is_partition(self) -> bool:
        """The four classes sum to the classified-token total (no leak)."""
        return self.partition_total == self.total_tokens

    @property
    def is_clean(self) -> bool:
        """The Pro D2 target invariant: no ``unowned_violation`` tokens."""
        return self.unowned_violation == 0

    def class_counts(self) -> dict[str, int]:
        """The four Pro D2 classes -> token counts (closed vocabulary)."""
        return {
            "owned": self.owned,
            "benign_uninterpreted": self.benign_uninterpreted,
            "typed_residual": self.typed_residual,
            "unowned_violation": self.unowned_violation,
        }


def build_token_partition_coverage(
    forest: SourceSyntaxGraph,
    *,
    statute_id: str = "",
) -> TokenPartitionCoverage:
    """Project an already-assembled forest into a :class:`TokenPartitionCoverage`.

    Pure: reads ONLY ``forest.coverage`` (the L0 census carried on the forest) and
    the forest's ``residual_span`` nodes (the self-evidencing
    ``unowned_violation`` witnesses). It re-parses NOTHING and makes NO ownership
    decision — the partition is exactly what the forest already computed, re-named
    into the Pro D2 four-class vocabulary.

    ``unowned_violation`` is the L0 ``silent`` count RE-TAGGED as a failure state.
    The witness spans are taken from the forest's ``residual_span`` nodes whose
    reason marks an unowned cheap signal (``unowned_cheap_signal:<shape>``), which
    are exactly the forest's surfacing of the silent class.
    """
    cov = forest.coverage
    violations: list[UnownedViolationToken] = []
    for node in sorted(
        forest.syntax_nodes.values(), key=lambda n: (n.char_start, n.char_end)
    ):
        if node.kind != "residual_span":
            continue
        reason = node.residual_reason
        if not reason.startswith("unowned_cheap_signal:"):
            continue
        shape = reason.split(":", 1)[1]
        violations.append(
            UnownedViolationToken(
                char_start=node.char_start,
                char_end=node.char_end,
                shape=shape,
                text=node.residual_text,
                context=node.residual_text,
            )
        )
    return TokenPartitionCoverage(
        graph_id=forest.graph_id,
        statute_id=statute_id,
        total_tokens=cov.total_tokens,
        owned=cov.owned_tokens,
        benign_uninterpreted=cov.benign_uninterpreted_tokens,
        typed_residual=cov.typed_residual_tokens,
        unowned_violation=cov.unowned_violation_tokens,
        violations=tuple(violations),
        parse_status=str(getattr(forest.parse_status, "value", forest.parse_status)),
        family_token_counts=dict(cov.family_token_counts),
    )


# ---------------------------------------------------------------------------
# The coverage-certifier cross-check (the reframe's core invariant).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CoverageViolation:
    """One LegalSurfaceGraph node whose source span is NOT forest-owned.

    The reframe's finding type: a lens produced a node from tokens the total parse
    did not account for. Self-evidencing — carries the node + its source span so
    the finding is auditable to the offending statute / node / span.

    Attributes:
        node_id:        The offending graph node id.
        node_kind:      Its node kind (which lens family produced it).
        lens_id:        The lens that produced it (or ``None``).
        source_unit_id: The source unit the span is into.
        char_start:     0-based inclusive offset into the unit body.
        char_end:       0-based exclusive offset.
        reason:         Why the span is not owned (``"no_forest_for_unit"`` when no
                        forest covers the unit; ``"span_not_owned"`` when the
                        forest exists but the span is not inside an owned leaf).
    """

    node_id: str
    node_kind: str
    lens_id: str | None
    source_unit_id: str
    char_start: int
    char_end: int
    reason: str


@dataclass(frozen=True, slots=True)
class GraphCoverageCrossCheck:
    """Result of the coverage-certifier cross-check over a graph + its forests.

    NOT a coverage PARTITION account (that is the core
    :class:`lawvm.core.stage_result.CoverageCertificate`, which the
    StageResult endgame row #4 token-partition view maps onto). This is the
    lens-vs-forest cross-CHECK RESULT: how many graph nodes were checked /
    skipped and which nodes' source spans are NOT forest-owned. It was renamed
    from ``CoverageCertificate`` (the StageResult-endgame name-clash resolution,
    4D) — verified not persisted under that symbol name (no JSON/parquet/cert
    schema carries the class name; the ``*_to_dict`` projections key on fields,
    not the type name), so the rename is schema-safe.

    Attributes:
        nodes_checked:   Graph nodes with a source span that were checked.
        nodes_skipped:   Graph nodes with no source span (nothing to certify).
        violations:      Nodes whose span is NOT forest-owned (empty ⇒ pass).
    """

    nodes_checked: int
    nodes_skipped: int
    violations: tuple[CoverageViolation, ...]

    @property
    def passes(self) -> bool:
        """The reframe invariant: every checked node's span is forest-owned."""
        return not self.violations


def _owned_intervals(forest: SourceSyntaxGraph) -> list[tuple[int, int]]:
    """The forest's OWNED char intervals (construction-leaf spans), span-sorted.

    These are the spans the total parse ACCOUNTED FOR as owned (claimed by >=1
    family construction). A lens node whose span is not covered by these is a
    coverage violation (it was produced from tokens the total parse did not own).
    """
    intervals = [
        (n.char_start, n.char_end)
        for n in forest.syntax_nodes.values()
        if n.kind in _OWNED_LEAF_KINDS and n.char_end > n.char_start
    ]
    intervals.sort()
    return intervals


def _span_is_covered(
    intervals: list[tuple[int, int]], start: int, end: int
) -> bool:
    """Is ``[start, end)`` fully covered by the union of ``intervals``?

    ``intervals`` is span-sorted. A zero-width query span is treated as covered
    iff it falls inside (or at the boundary of) some owned interval.
    """
    if end <= start:
        # zero-width: covered iff a point inside some interval
        return any(lo <= start <= hi for lo, hi in intervals)
    cursor = start
    for lo, hi in intervals:
        if lo > cursor:
            break  # gap before the next interval — not covered
        if hi >= cursor:
            cursor = max(cursor, hi)
            if cursor >= end:
                return True
    return cursor >= end


def certify_graph_coverage(
    graph: LegalSurfaceGraph,
    forests: Mapping[str, SourceSyntaxGraph],
) -> GraphCoverageCrossCheck:
    """Cross-check: every graph node's source span is OWNED in the forest partition.

    The reframe's core invariant (Pro D2): the lenses stay the PRODUCERS, and the
    forest CERTIFIES totality. For each :class:`~lawvm.core.legal_surface_graph.SurfaceNode`
    carrying a source span, assert its ``[char_start, char_end)`` is covered by the
    union of the forest's OWNED construction-leaf spans for that source unit. A
    node whose span is NOT owned is surfaced as a :class:`CoverageViolation` (it
    was produced from tokens the total parse did not account for) — NEVER raised,
    so the violation surface is visible.

    ``forests`` maps ``source_unit_id`` -> the assembled forest for that unit. A
    node whose unit has no forest is a violation with reason
    ``no_forest_for_unit`` (we cannot certify it — fail-loud, do not silently
    pass).
    """
    violations: list[CoverageViolation] = []
    checked = 0
    skipped = 0
    owned_cache: dict[str, list[tuple[int, int]]] = {}
    for node in graph.nodes.values():
        ref = node.source_ref
        if ref is None:
            skipped += 1
            continue
        checked += 1
        forest = forests.get(ref.source_unit_id)
        if forest is None:
            violations.append(
                CoverageViolation(
                    node_id=node.node_id,
                    node_kind=node.node_kind,
                    lens_id=node.lens_id,
                    source_unit_id=ref.source_unit_id,
                    char_start=ref.char_start,
                    char_end=ref.char_end,
                    reason="no_forest_for_unit",
                )
            )
            continue
        intervals = owned_cache.get(ref.source_unit_id)
        if intervals is None:
            intervals = _owned_intervals(forest)
            owned_cache[ref.source_unit_id] = intervals
        if not _span_is_covered(intervals, ref.char_start, ref.char_end):
            violations.append(
                CoverageViolation(
                    node_id=node.node_id,
                    node_kind=node.node_kind,
                    lens_id=node.lens_id,
                    source_unit_id=ref.source_unit_id,
                    char_start=ref.char_start,
                    char_end=ref.char_end,
                    reason="span_not_owned",
                )
            )
    return GraphCoverageCrossCheck(
        nodes_checked=checked,
        nodes_skipped=skipped,
        violations=tuple(violations),
    )


# ---------------------------------------------------------------------------
# Rendering (pure; text + dict/json). Mirrors the read-only viewer discipline.
# ---------------------------------------------------------------------------


def certificate_to_dict(cert: TokenPartitionCoverage) -> dict[str, object]:
    """Machine-readable projection of a :class:`TokenPartitionCoverage`."""
    return {
        "graph_id": cert.graph_id,
        "statute_id": cert.statute_id,
        "total_tokens": cert.total_tokens,
        "classes": cert.class_counts(),
        "is_partition": cert.is_partition(),
        "is_clean": cert.is_clean,
        "parse_status": cert.parse_status,
        "violations": [
            {
                "char_start": v.char_start,
                "char_end": v.char_end,
                "shape": v.shape,
                "text": v.text,
                "context": v.context,
            }
            for v in cert.violations
        ],
        "family_token_counts": dict(cert.family_token_counts),
    }


def render_certificate(cert: TokenPartitionCoverage) -> str:
    """Human-readable rendering of a :class:`TokenPartitionCoverage`."""
    total = max(1, cert.total_tokens)
    lines: list[str] = []
    sid = cert.statute_id or cert.graph_id
    lines.append(f"TOKEN PARTITION CERTIFICATE — {sid}")
    lines.append("=" * 60)
    lines.append(f"  total signal tokens   : {cert.total_tokens}")
    lines.append(
        f"  owned                 : {cert.owned}  "
        f"({100 * cert.owned / total:.2f}%)"
    )
    lines.append(f"  benign_uninterpreted  : {cert.benign_uninterpreted}")
    lines.append(f"  typed_residual        : {cert.typed_residual}")
    lines.append(
        f"  unowned_violation     : {cert.unowned_violation}  "
        f"({100 * cert.unowned_violation / total:.3f}%)  "
        f"[{'CLEAN' if cert.is_clean else 'VIOLATION'}]"
    )
    lines.append(f"  partition ok          : {cert.is_partition()}")
    lines.append(f"  parse_status          : {cert.parse_status}")
    top = sorted(cert.family_token_counts.items(), key=lambda kv: -kv[1])[:8]
    if top:
        lines.append(
            "  family token counts   : " + ", ".join(f"{k}={v}" for k, v in top)
        )
    if cert.violations:
        lines.append("")
        lines.append(f"unowned_violation spans ({len(cert.violations)}):")
        for v in cert.violations:
            txt = v.text.replace("\n", "\\n")
            lines.append(
                f"  [{v.char_start}:{v.char_end}] shape={v.shape}  {txt[:72]!r}"
            )
    return "\n".join(lines)


def coverage_certificate_to_dict(cert: GraphCoverageCrossCheck) -> dict[str, object]:
    """Machine-readable projection of a :class:`GraphCoverageCrossCheck`."""
    return {
        "nodes_checked": cert.nodes_checked,
        "nodes_skipped": cert.nodes_skipped,
        "passes": cert.passes,
        "violations": [
            {
                "node_id": v.node_id,
                "node_kind": v.node_kind,
                "lens_id": v.lens_id,
                "source_unit_id": v.source_unit_id,
                "char_start": v.char_start,
                "char_end": v.char_end,
                "reason": v.reason,
            }
            for v in cert.violations
        ],
    }


def render_coverage_certificate(cert: GraphCoverageCrossCheck) -> str:
    """Human-readable rendering of a :class:`GraphCoverageCrossCheck`."""
    lines: list[str] = []
    lines.append("COVERAGE CERTIFIER (lens nodes vs forest ownership)")
    lines.append("=" * 60)
    lines.append(f"  nodes checked : {cert.nodes_checked}")
    lines.append(f"  nodes skipped : {cert.nodes_skipped}  (no source span)")
    lines.append(
        f"  result        : {'PASS' if cert.passes else 'FAIL'}  "
        f"({len(cert.violations)} not forest-owned)"
    )
    if cert.violations:
        lines.append("")
        lines.append("out-of-partition lens nodes:")
        for v in cert.violations:
            lines.append(
                f"  {v.node_kind} {v.node_id[:16]}… "
                f"[{v.source_unit_id} {v.char_start}:{v.char_end}] "
                f"lens={v.lens_id} reason={v.reason}"
            )
    return "\n".join(lines)
