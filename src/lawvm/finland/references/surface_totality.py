"""Surface-plane totality sweeps (registry rows SURF-04, SURF-05).

Two *per-unit totality* sweeps over the FI surface plane, in the spirit of the
audit-invariant registry's §0 generative principle: every owned unit is accepted,
typed as a residual, or recorded as a finding — never silently dropped. Both are
OBSERVATION-role: they assert the totality CONTRACT and surface a residual
population; over the real corpus the residual is the *expected, correct* outcome
(an orphan reference / an unclassified mention is a real surface fact about the
document, not a pipeline fault), so blocking would contradict tag-don't-guess.
The synthetic unit-level bite is the guard-liveness fire-drill.

SURF-04 — definition totality / uniqueness
==========================================
Over the FI defined-term BINDINGS for one statute (the carriers produced by
:func:`lawvm.finland.references.defined_terms.recognize_defined_term_bindings`,
assembled with their later USES by
:func:`lawvm.finland.references.definition_graph.build_definition_graph`):

  * ``DEFINITION.DUPLICATE_DEFINITION`` — a defined term bound more than once per
    ``(term, scope)``. The contract is "exactly ONE definition site per (PIT,
    scope)"; a collision is typed, not silently merged. (Per-PIT is implicit: one
    statute graph is built at one point-in-time, so a sweep over one graph's
    bindings is the per-(PIT, scope) cell.)
  * ``DEFINITION.ORPHAN_DEFINITION_REFERENCE`` — a USE whose surface matches a
    binding surface but has NO in-scope resolvable definition (the resolver's
    ``open`` status: every matching binding lies after the use, or none resolves).
    A reference to a term with no resolvable definition is typed, not silently
    dropped.

SURF-05 — citation-graph totality (intra-jurisdiction)
======================================================
Over one statute's :class:`ExtractionResult` (the combined output of
:func:`lawvm.finland.references.ref_mention_extractor.extract_all_reference_mentions`):

  * ``REFERENCE.UNCLASSIFIED_REFERENCE`` — an emitted ``ReferenceMention`` whose
    ``cite_confidence`` is not one of the closed CLASSIFIED states
    (resolved/statute_only/ambiguous/open/broken/unsupported). The
    ``ReferenceMention`` type already pins ``cite_confidence`` to a closed enum, so
    a structurally-unclassified mention is impossible to construct today; this
    sweep is the STANDING totality assertion that the closed classification set is
    never silently widened (a future enum member that is NOT a recognised
    classification, or a metadata edge that slips an out-of-set value past the
    constructor, fires). Rejected candidates are carried on
    ``ExtractionResult.rejected`` / preparatory ``RejectedPreparatoryCandidate``,
    so a non-empty rejected lane is the typed residue, never a silent drop.

Both sweeps are PURE (no side effects, no production-behavior change): they read
already-produced carriers and return typed finding records. They sit off the
replay/apply path.
"""
from __future__ import annotations

from dataclasses import dataclass

from typing import TYPE_CHECKING

from lawvm.core.reference_mention import CiteConfidence, ReferenceMention
from lawvm.finland.references.defined_terms import DefinedTermBinding
from lawvm.finland.references.definition_graph import DefinitionGraph
from lawvm.finland.references.term_use import STATUS_OPEN, TermUse

if TYPE_CHECKING:
    from lawvm.finland.references.ref_mention_extractor import ExtractionResult

# ---------------------------------------------------------------------------
# Finding codes (closed set; registered in core/observation_registry.py)
# ---------------------------------------------------------------------------

DEFINITION_DUPLICATE_DEFINITION = "DEFINITION.DUPLICATE_DEFINITION"
DEFINITION_ORPHAN_DEFINITION_REFERENCE = "DEFINITION.ORPHAN_DEFINITION_REFERENCE"
REFERENCE_UNCLASSIFIED_REFERENCE = "REFERENCE.UNCLASSIFIED_REFERENCE"

#: The closed set of CLASSIFIED citation confidences. SURF-05 asserts every
#: emitted mention carries one of these. Kept as a frozenset of the enum members
#: so a NEW ``CiteConfidence`` member that is not consciously added here is, by
#: construction, "unclassified" and fires the sweep — the closed-set membership is
#: the totality contract, not a stringly comparison.
_CLASSIFIED_CONFIDENCES: frozenset[CiteConfidence] = frozenset(
    {
        CiteConfidence.EXACT,
        CiteConfidence.APPROXIMATE,
        CiteConfidence.AMBIGUOUS,
        CiteConfidence.UNRESOLVED,
        CiteConfidence.BROKEN,
        CiteConfidence.STATUTE_ONLY,
        CiteConfidence.OPEN,
    }
)


# ---------------------------------------------------------------------------
# Typed sweep findings (self-evidencing per AGENTS.md §1.8 / EV-07)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefinitionTotalityFinding:
    """One SURF-04 surface fact about a statute's definition totality.

    Attributes:
        code:        ``DEFINITION.DUPLICATE_DEFINITION`` or
                     ``DEFINITION.ORPHAN_DEFINITION_REFERENCE``.
        statute_id:  The statute the sweep ran over.
        term:        The defined term (lowercased lemma) the finding is about.
        scope:       The binding scope of the colliding definitions
                     (``DUPLICATE_DEFINITION`` only; empty for an orphan use).
        detail:      SELF-EVIDENCING message embedding the term + offending
                     surface / byte offsets, so the finding is auditable from the
                     record alone (never an opaque code).
        byte_offset: Byte offset of the offending construct in the body text (the
                     first colliding binding for a duplicate; the use token for an
                     orphan reference).
    """

    code: str
    statute_id: str
    term: str
    scope: str
    detail: str
    byte_offset: int


@dataclass(frozen=True, slots=True)
class CitationTotalityFinding:
    """One SURF-05 surface fact about a statute's citation-graph totality.

    Attributes:
        code:        ``REFERENCE.UNCLASSIFIED_REFERENCE``.
        statute_id:  The statute the sweep ran over.
        confidence:  The out-of-closed-set ``cite_confidence`` value (its ``.value``
                     string) that made the mention unclassified.
        detail:      SELF-EVIDENCING message embedding the source/target ref + the
                     offending confidence, so the finding is auditable alone.
        byte_offset: Byte offset of the mention's source span, or ``-1`` when the
                     mention carries no span (metadata-derived edge).
    """

    code: str
    statute_id: str
    confidence: str
    detail: str
    byte_offset: int


# ---------------------------------------------------------------------------
# SURF-04 — definition totality
# ---------------------------------------------------------------------------


def sweep_definition_totality_from_bindings(
    bindings: tuple[DefinedTermBinding, ...] | list[DefinedTermBinding],
    uses: tuple[TermUse, ...] | list[TermUse],
    *,
    statute_id: str,
) -> tuple[DefinitionTotalityFinding, ...]:
    """Assert definition totality / uniqueness over one statute's carriers.

    Args:
        bindings:   The recognised definition NODES (per (PIT, scope) cell: one
                    statute graph is built at one PIT).
        uses:       The resolved USE nodes (the resolver tags an unresolvable use
                    ``open``).
        statute_id: The statute id (recorded on every finding).

    Returns:
        A tuple of :class:`DefinitionTotalityFinding`, sorted by ``byte_offset``
        then ``code``. Empty when every term has exactly one definition site per
        scope and every use resolves in scope.

    Discipline (tag-don't-guess): the sweep NEVER fabricates a definition. A
    duplicate is keyed on the binder's own ``(term, scope)``; an orphan is the
    resolver's own ``open`` status. Nothing is inferred beyond the carriers.
    """
    findings: list[DefinitionTotalityFinding] = []

    # -- DUPLICATE_DEFINITION: more than one binding per (term, scope) --------
    by_key: dict[tuple[str, str], list[DefinedTermBinding]] = {}
    for b in bindings:
        key = (b.term.strip().lower(), b.scope)
        by_key.setdefault(key, []).append(b)
    for (term, scope), group in by_key.items():
        if len(group) <= 1:
            continue
        offsets = sorted(g.source_span.byte_offset for g in group)
        first = min(group, key=lambda g: g.source_span.byte_offset)
        findings.append(
            DefinitionTotalityFinding(
                code=DEFINITION_DUPLICATE_DEFINITION,
                statute_id=statute_id,
                term=term,
                scope=scope,
                detail=(
                    f"term {term!r} is defined {len(group)} times in scope "
                    f"{scope!r} (byte offsets {offsets}); "
                    f"exactly one definition site per (PIT, scope) is required"
                ),
                byte_offset=first.source_span.byte_offset,
            )
        )

    # -- ORPHAN_DEFINITION_REFERENCE: a use with no in-scope definition -------
    #
    # The resolver tags a use ``open`` when its surface matched some binding but
    # no in-scope binding resolves it (every match lies after the use, or none
    # remains usable). That IS "a reference to a term with no resolvable
    # definition" — the SURF-04 orphan cell.
    for use in uses:
        if use.use_status != STATUS_OPEN:
            continue
        findings.append(
            DefinitionTotalityFinding(
                code=DEFINITION_ORPHAN_DEFINITION_REFERENCE,
                statute_id=statute_id,
                term=use.lemma.strip().lower() or use.term_surface.strip().lower(),
                scope="",
                detail=(
                    f"reference {use.term_surface!r} (definition lemma "
                    f"{use.lemma!r}) has no resolvable definition in scope at "
                    f"byte {use.source_span.byte_offset}"
                ),
                byte_offset=use.source_span.byte_offset,
            )
        )

    findings.sort(key=lambda f: (f.byte_offset, f.code))
    return tuple(findings)


def sweep_definition_totality(
    graph: DefinitionGraph,
) -> tuple[DefinitionTotalityFinding, ...]:
    """SURF-04 sweep over an assembled :class:`DefinitionGraph` (convenience)."""
    return sweep_definition_totality_from_bindings(
        graph.bindings, graph.uses, statute_id=graph.statute_id
    )


# ---------------------------------------------------------------------------
# SURF-05 — citation-graph totality
# ---------------------------------------------------------------------------


def sweep_citation_totality(
    result: ExtractionResult,
    *,
    statute_id: str,
) -> tuple[CitationTotalityFinding, ...]:
    """Assert citation-graph totality over one statute's extraction result.

    Every emitted ``ReferenceMention`` must carry a classification from the closed
    CLASSIFIED set (resolved/statute_only/ambiguous/open/broken/unsupported, here
    the ``CiteConfidence`` enum). A mention whose confidence is outside that set is
    typed ``REFERENCE.UNCLASSIFIED_REFERENCE`` rather than silently dropped.

    Args:
        result:     The combined extraction result
                    (``extract_all_reference_mentions``). Its ``rejected`` lane
                    (plus the preparatory ``RejectedPreparatoryCandidate`` records
                    folded into it) is the typed residue for candidates that
                    failed grammar — outside this sweep's mention totality but the
                    reason the sweep can hold "no silent drop".
        statute_id: The statute id (recorded on every finding).

    Returns:
        A tuple of :class:`CitationTotalityFinding`, sorted by ``byte_offset`` then
        confidence. Empty when every mention is classified (the structural norm).
    """
    findings: list[CitationTotalityFinding] = []
    for mention in result.mentions:
        if _is_classified(mention):
            continue
        span = mention.source_span
        offset = span.byte_offset if span is not None else -1
        src = mention.source_provision_ref.serialized()
        tgt = (
            mention.target_provision_ref.serialized()
            if mention.target_provision_ref is not None
            else "<none>"
        )
        findings.append(
            CitationTotalityFinding(
                code=REFERENCE_UNCLASSIFIED_REFERENCE,
                statute_id=statute_id,
                confidence=mention.cite_confidence.value,
                detail=(
                    f"reference {src!r} -> {tgt!r} ({mention.phrase_lemma}) carries "
                    f"an unclassified cite_confidence "
                    f"{mention.cite_confidence.value!r}; not in the closed "
                    f"classification set"
                ),
                byte_offset=offset,
            )
        )
    findings.sort(key=lambda f: (f.byte_offset, f.confidence))
    return tuple(findings)


def _is_classified(mention: ReferenceMention) -> bool:
    """True iff the mention's ``cite_confidence`` is in the closed CLASSIFIED set.

    Identity-based membership (``is``) rather than a frozenset ``in`` so an
    out-of-set forged value that is NOT hashable (the exact silent-widening this
    sweep guards against) is treated as unclassified instead of raising. A genuine
    ``CiteConfidence`` member is one of the closed singletons and matches by
    identity.
    """
    conf = mention.cite_confidence
    return any(conf is classified for classified in _CLASSIFIED_CONFIDENCES)


__all__ = [
    "DEFINITION_DUPLICATE_DEFINITION",
    "DEFINITION_ORPHAN_DEFINITION_REFERENCE",
    "REFERENCE_UNCLASSIFIED_REFERENCE",
    "CitationTotalityFinding",
    "DefinitionTotalityFinding",
    "sweep_citation_totality",
    "sweep_definition_totality",
    "sweep_definition_totality_from_bindings",
]
