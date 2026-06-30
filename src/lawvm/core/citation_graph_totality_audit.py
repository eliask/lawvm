"""``lawvm.core.citation_graph_totality_audit`` — D6 ``SURFACE.CITATION_GRAPH_TOTALITY``.

Per :file:`notes/LAWVM_AUDIT_REGISTRY_ROADMAP.md` D6 / §A7 (and the
``REFERENCE.UNCLASSIFIED_REFERENCE`` registry row): every emitted
:class:`~lawvm.core.reference_mention.ReferenceMention` MUST carry a typed
classification — it is either self-terminal (its ``cite_confidence`` is one of
the closed, no-companion-receipt-needed states ``EXACT`` / ``STATUTE_ONLY`` /
``OPEN`` / ``UNRESOLVED``), or it is a finding-requiring state (``AMBIGUOUS`` /
``APPROXIMATE`` / ``BROKEN``) whose companion classification receipt
(:class:`~lawvm.core.reference_mention.ReferenceResolution` over the same
surface, or a typed reference finding) is present. A mention that the pipeline
forgets to classify — a finding-requiring confidence with NO receipt, or a
``cite_confidence`` outside the audit's closed recognized set (the closed set
silently widened) — is otherwise silently dropped. This audit makes the
``mentions == self_classified + receipt_backed`` parity explicit and surfaces
the residue as a typed ``REFERENCE.UNCLASSIFIED_REFERENCE``
:class:`~lawvm.core.phase_result.Observation`.

PLANE & DISCIPLINE (AGENTS.md §0, §2.10). This module lives in the
evidence-plane audit lane: it inspects passed ``ReferenceMention`` and
``ReferenceResolution`` carriers, returns
:class:`~lawvm.core.phase_result.Observation` tuples, and **never mutates legal
state**. The audit does not resolve a reference, re-tag a confidence, or drop a
mention — it reports that a mention reached the citation surface without a
typed classification. The wire consumer decides whether the observation becomes
a finding (quirks default) or a strict-mode barrier; this function emits
observations only, never raises on shape-valid input.

JURISDICTION-NEUTRAL. This audit consumes the shared reference carriers
verbatim (``reference_mention.py``); it holds no frontend imports. The first
consumer probe is the UK replay fold-exit
(``lawvm.uk_legislation.citation_graph_totality_probe``), but the contract is
universal.

INPUT CONTRACT (why these two streams). The audit is fed exactly the surfaces
a frontend exposes at replay fold-exit:
  * ``mentions`` — every ``ReferenceMention`` the frontend emitted (the
    flattened per-target citation relation). This is the population whose
    totality is asserted.
  * ``resolutions`` — the set-level ``ReferenceResolution`` receipts keyed by
    ``surface_expr_id``. A finding-requiring mention is "covered" when a
    resolution over its surface expression is present (the receipt that the
    classification was actually emitted, not silently skipped).
A frontend that emits NO references at all feeds two empty streams and the
audit is a clean no-op — the correct outcome for a surface with nothing to
classify (tag-don't-guess; an empty citation graph is total over zero
mentions).

WHAT THIS DOES **NOT** DO. It does not re-resolve a reference (that is the
extractor's job upstream), does not assert the resolution's TARGET actually
exists in a timeline (that is the cross-statute graph-edges question, D6's
sibling, see roadmap §316), and does not assert universe totality — only
present-mention totality. A mention that never reaches the fold-exit surface
(filtered earlier) is invisible here; that gap belongs to the upstream filter's
receipt accounting (AGENTS.md §1.8).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Optional

from lawvm.core.phase_result import Observation
from lawvm.core.reference_mention import (
    CiteConfidence,
    ReferenceMention,
    ReferenceResolution,
    compute_surface_expr_id,
)


# Public finding code, also registered in
# :data:`lawvm.core.observation_registry.FINDING_REGISTRY`.
REFERENCE_UNCLASSIFIED_REFERENCE = "REFERENCE.UNCLASSIFIED_REFERENCE"

# Audit-stage / owner used in the emitted Observations. Stage mirrors the
# registry phase for ``REFERENCE.UNCLASSIFIED_REFERENCE`` (``surface_totality``).
_CITATION_AUDIT_STAGE = "surface_totality"
_CITATION_AUDIT_OWNER = "citation_graph_totality_audit"


# ``CiteConfidence`` states that ARE their own typed classification: a mention
# in one of these states needs NO companion resolution/finding receipt — the
# confidence value is the terminal classification.
#   EXACT / STATUTE_ONLY: resolved (fully, or to act-only with a deferred path).
#   OPEN:                 vague catch-all by construction (no target by design).
#   UNRESOLVED:           typed residual (target not resolvable; the residual is
#                         the correct, surfaced outcome).
# Closed set: adding a state here is a typed contract change, not silent
# acceptance (AGENTS.md §1.10 fail-loud).
_SELF_CLASSIFIED_CONFIDENCES: frozenset[CiteConfidence] = frozenset(
    {
        CiteConfidence.EXACT,
        CiteConfidence.STATUTE_ONLY,
        CiteConfidence.OPEN,
        CiteConfidence.UNRESOLVED,
    }
)

# ``CiteConfidence`` states that MUST be backed by a companion classification
# receipt (a ``ReferenceResolution`` over the same surface, per the carriers'
# documented pairing — see ``BrokenReferenceFinding`` / ``AmbiguousReferenceFinding``
# / ``ApproximateReferenceFinding``). A mention in one of these states with NO
# receipt is the silent-drop the audit surfaces.
_RECEIPT_REQUIRED_CONFIDENCES: frozenset[CiteConfidence] = frozenset(
    {
        CiteConfidence.AMBIGUOUS,
        CiteConfidence.APPROXIMATE,
        CiteConfidence.BROKEN,
    }
)

# The full closed recognized set. The union must cover every ``CiteConfidence``
# member; a member appearing in neither partition is a closed-set widening bug
# and surfaces as an unclassified residue with reason
# ``unrecognized_classification`` (fail-loud, never silent).
_RECOGNIZED_CONFIDENCES: frozenset[CiteConfidence] = (
    _SELF_CLASSIFIED_CONFIDENCES | _RECEIPT_REQUIRED_CONFIDENCES
)

# Reason codes carried in the observation detail so a triager can answer
# "why unclassified" without re-running extraction.
_REASON_MISSING_RECEIPT = "receipt_required_confidence_without_resolution"
_REASON_UNRECOGNIZED = "unrecognized_classification_confidence"


def _format_source_address(mention: ReferenceMention) -> str:
    """Human-readable source provision address for the observation detail."""
    src = mention.source_provision_ref
    formatted = src.serialized()
    return formatted if formatted else repr(src)


def _build_observation(
    mention: ReferenceMention,
    *,
    source_statute: str,
    reason: str,
) -> Observation:
    """Build the typed ``REFERENCE.UNCLASSIFIED_REFERENCE`` observation.

    Detail carries the mention identity (source address, raw cite text, target
    address when present, the offending confidence, the phrase class, and the
    source span) so a triager can answer "which mention, what did it cite, where
    in source" without re-running extraction. Byte spans are carried as plain
    ints (not the SourceSpan object) to keep the observation JSON-safe and stable
    across source-byte revisions of unrelated provisions.
    """
    span = mention.source_span
    tgt = mention.target_provision_ref
    detail: dict[str, Any] = {
        "source_statute_id": mention.source_provision_ref.statute_id,
        "source_provision_ref": _format_source_address(mention),
        "raw_cite_text": mention.surface_text,
        "target_provision_ref": tgt.serialized() if tgt is not None else "",
        "cite_kind": mention.cite_kind.value,
        "cite_confidence": mention.cite_confidence.value,
        "phrase_lemma": mention.phrase_lemma,
        "edge_subtype": mention.edge_subtype or "",
        "source_span_file": span.source_file if span is not None else "",
        "source_span_byte_offset": span.byte_offset if span is not None else -1,
        "source_span_len": span.byte_len if span is not None else -1,
        "reason": reason,
        "owner": _CITATION_AUDIT_OWNER,
    }
    return Observation(
        kind=REFERENCE_UNCLASSIFIED_REFERENCE,
        stage=_CITATION_AUDIT_STAGE,
        detail=detail,
        source_statute=source_statute,
    )


def assert_citation_graph_totality(
    mentions: Sequence[ReferenceMention],
    resolutions: Sequence[ReferenceResolution] = (),
    *,
    source_statute: str = "",
) -> tuple[Observation, ...]:
    """One :class:`Observation` per ``ReferenceMention`` lacking a typed classification.

    A mention is classified iff EITHER:
      * its ``cite_confidence`` is self-terminal
        (:data:`_SELF_CLASSIFIED_CONFIDENCES`) — the confidence value IS the
        classification, no companion receipt needed; OR
      * its ``cite_confidence`` is receipt-required
        (:data:`_RECEIPT_REQUIRED_CONFIDENCES`) AND a companion
        :class:`ReferenceResolution` over the mention's surface
        (``surface_expr_id`` match) is present in ``resolutions``.

    Any other mention — a receipt-required confidence with no resolution, or a
    confidence outside the closed recognized set
    (:data:`_RECOGNIZED_CONFIDENCES`) — surfaces as a typed
    ``REFERENCE.UNCLASSIFIED_REFERENCE`` observation.

    Args:
        mentions: every ``ReferenceMention`` the frontend emitted at the surface
            under audit. A mention filtered out earlier is invisible here — that
            filter owns its receipt accounting per AGENTS.md §1.8.
        resolutions: the set-level ``ReferenceResolution`` receipts. Indexed by
            ``surface_expr_id``; a finding-requiring mention is covered when a
            resolution over its surface expression is present. Empty by default
            (a frontend that emits no resolutions but only self-terminal
            mentions is total).
        source_statute: the base statute id of the surface under audit. Carried
            into each observation so a multi-statute bench run routes the finding
            back to its source statute.

    Returns:
        Tuple of Observations, one per unclassified mention, in mention-stream
        order. The caller decides whether these become findings (quirks default)
        or strict-mode barriers (a future strict_profile) — this function emits
        observations only, never raises on shape-valid input, never mutates
        legal state.

    Per AGENTS.md §0 ``over-retention is the safe wrong``: a mention with no
    classification surfaces as an observation here rather than being silently
    dropped from the citation graph. The audit never resolves the reference,
    re-tags its confidence, or removes the mention.
    """
    # Index resolution receipts by the surface they resolve. One expression →
    # one resolution (per the carrier's documented invariant), so a set is the
    # right shape: presence is all the audit needs.
    resolved_surface_ids: set[str] = {
        res.surface_expr_id for res in resolutions if res.surface_expr_id
    }

    findings: list[Observation] = []
    for mention in mentions:
        confidence = mention.cite_confidence
        if confidence in _SELF_CLASSIFIED_CONFIDENCES:
            continue
        if confidence in _RECEIPT_REQUIRED_CONFIDENCES:
            surface_id = _mention_join_key(mention)
            if surface_id is not None and surface_id in resolved_surface_ids:
                continue
            findings.append(
                _build_observation(
                    mention,
                    source_statute=source_statute,
                    reason=_REASON_MISSING_RECEIPT,
                )
            )
            continue
        # Confidence in neither closed partition: the recognized set was
        # silently widened. Fail-loud as a surfaced residue, not a crash.
        findings.append(
            _build_observation(
                mention,
                source_statute=source_statute,
                reason=_REASON_UNRECOGNIZED,
            )
        )
    return tuple(findings)


def _mention_join_key(mention: ReferenceMention) -> Optional[str]:
    """Compute the ``surface_expr_id`` a resolution would carry for this mention.

    The resolution lane keys on
    :func:`lawvm.core.reference_mention.compute_surface_expr_id` over
    ``(surface_text, expression_kind, source_span)``. A flattened mention does
    not carry ``expression_kind`` (that lives on the ``ReferenceExpression``), so
    a robust mention→resolution join is not always reconstructable from the
    mention alone. To stay sound (never claim coverage we cannot prove), the
    audit only treats a mention as receipt-covered when the resolution set
    indexes a surface id derivable from the mention's own owned surface fields.

    We derive the key from the mention's ``surface_text`` + ``source_span`` using
    the SINGLE-expression convention (``expression_kind="single"``), which is the
    shape a per-target flattened mention projects from. When the mention owns no
    ``surface_text`` we cannot form a content address and return None (the
    mention is then treated as receipt-less — the §0 over-surfacing direction).
    """
    if not mention.surface_text:
        return None
    return compute_surface_expr_id(
        mention.surface_text,
        mention.source_span,
        "single",
    )


__all__ = [
    "REFERENCE_UNCLASSIFIED_REFERENCE",
    "assert_citation_graph_totality",
]
