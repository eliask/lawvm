"""Fold flattened ``ReferenceMention`` rows back into expression + target SET.

The flattened ``ReferenceMention`` relation (``core.reference_mention``) emits
one row per expanded target. A written RANGE ("33—35 artiklassa") or
coordination ("1 ja 2 kohdassa") therefore lands as N rows that share only their
``surface_text`` / ``source_span``. At that point a range is indistinguishable
from a candidate ambiguity (one-of-N) or an open vague reference — the SET
SEMANTICS (every listed target denoted, vs pick-one-unknown, vs unenumerable)
has been lost. See ``notes_internal/DISTRIBUTABLE_LAW_SUBSTRATE_DESIGN.md`` §14.

This module restores the set identity WITHOUT touching the flattened projection:
:func:`fold_reference_set` takes the flattened mentions that share one surface
expression and produces ONE :class:`ReferenceExpression` + ONE
:class:`ReferenceResolution` carrying the whole target set and its
:class:`ReferenceTargetSetSemantics`.

Classification is deterministic and FAIL-LOUD: the semantics are read off the
mentions' own typed ``cite_confidence`` + surface shape, never guessed. When the
extension cannot be enumerated, it is classified explicitly (OPEN or
NO_ENUMERABLE_EXTENSION), never dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from lawvm.core.reference_mention import (
    CiteConfidence,
    ProvisionRef,
    ReferenceExpression,
    ReferenceMention,
    ReferenceResolution,
    ReferenceResolutionStatus,
    ReferenceTargetSetSemantics,
    SourceSpan,
)

# A surface whose numeric list uses a dash connector ("33—35", "69 d–69 g") is a
# RANGE; one using a word/comma connector ("33 ja 35", "1, 2 kohdassa") is a
# COORDINATION. Both denote every listed member (ALL_VALID); the distinction is
# only the surface ``expression_kind`` label, not the semantics. The dash class
# covers hyphen-minus, en dash, em dash and figure dash (Finnish drafting uses
# the en dash primarily; sources also carry hyphen-minus / em dash).
_RANGE_DASH_RE = re.compile(r"[\dA-Za-zÅÄÖåäö]\s*[-‐‑‒–—]\s*\d")
_COORDINATION_RE = re.compile(r"\bja\b|\bsekä\b|\btai\b|,", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ReferenceSet:
    """A folded reference set: one expression + its one resolution.

    The two halves are kept separate (expression = immutable surface fact,
    resolution = the target set under a scope) so a consumer can address either
    independently, matching the type model in ``core.reference_mention``.
    """

    expression: ReferenceExpression
    resolution: ReferenceResolution


def _surface_expression_kind(surface_text: str, member_count: int) -> str:
    """Classify the SURFACE shape of a citation expression.

    This is the syntactic class of the literal text (independent of resolution):
    ``"single"`` (one member), ``"range"`` (dash-connected number list),
    ``"coordination"`` (word/comma-connected list), else ``"single"`` for a
    lone multi? — a multi-member set with no recognised connector is still a
    coordination of some kind, so it is labelled ``"coordination"`` rather than
    silently mis-labelled ``"single"``.
    """
    if member_count <= 1:
        return "single"
    if _RANGE_DASH_RE.search(surface_text):
        return "range"
    if _COORDINATION_RE.search(surface_text):
        return "coordination"
    # Multiple members but no recognised connector surface (e.g. an enumeration
    # the extractor produced from a tail with no literal connector). It is still
    # a coordination of members; do not mislabel it "single".
    return "coordination"


def _semantics_and_status(
    mentions: Sequence[ReferenceMention],
    member_targets: Sequence[ProvisionRef],
) -> Tuple[ReferenceTargetSetSemantics, ReferenceResolutionStatus]:
    """Choose set semantics + status from the mentions' own typed confidences.

    Rule (deterministic, fail-loud — every branch is an explicit classification):

      * any member confidence is AMBIGUOUS  -> CANDIDATE_AMBIGUITY (one-of-N
        alternatives; the source refuses to pick). Status UNRESOLVED.
      * all members OPEN (vague catch-all, targetless by construction) and there
        is at least one member with NO concrete target -> OPEN (referent-bearing
        but not enumerable). Status UNRESOLVED.
      * an enumerable concrete target set exists:
          - exactly one member -> SINGLE
          - more than one       -> ALL_VALID (range/coordination: every member
            denoted)
        Status: RESOLVED if every member resolved to a concrete target,
        PARTIAL if some did and some did not.
      * no concrete targets and not classified above -> NO_ENUMERABLE_EXTENSION
        (e.g. all members UNRESOLVED/BROKEN, no referent and nothing to
        enumerate). Status UNRESOLVED.
    """
    confidences = [m.cite_confidence for m in mentions]

    if any(c is CiteConfidence.AMBIGUOUS for c in confidences):
        return (
            ReferenceTargetSetSemantics.CANDIDATE_AMBIGUITY,
            ReferenceResolutionStatus.UNRESOLVED,
        )

    if member_targets:
        concrete = len(member_targets)
        all_resolved = concrete == len(mentions) and all(
            c not in (CiteConfidence.UNRESOLVED, CiteConfidence.BROKEN, CiteConfidence.OPEN)
            for c in confidences
        )
        status = (
            ReferenceResolutionStatus.RESOLVED
            if all_resolved
            else ReferenceResolutionStatus.PARTIAL
        )
        if concrete == 1 and len(mentions) == 1:
            return (ReferenceTargetSetSemantics.SINGLE, status)
        return (ReferenceTargetSetSemantics.ALL_VALID, status)

    # No concrete targets. Distinguish OPEN (referent-bearing vague catch-all)
    # from NO_ENUMERABLE_EXTENSION (nothing to enumerate at all).
    if confidences and all(c is CiteConfidence.OPEN for c in confidences):
        return (
            ReferenceTargetSetSemantics.OPEN,
            ReferenceResolutionStatus.UNRESOLVED,
        )
    return (
        ReferenceTargetSetSemantics.NO_ENUMERABLE_EXTENSION,
        ReferenceResolutionStatus.UNRESOLVED,
    )


def fold_reference_set(
    mentions: Sequence[ReferenceMention],
    *,
    surface_text: Optional[str] = None,
    source_span: Optional[SourceSpan] = None,
    corpus_version: str = "",
    branch: str = "",
) -> ReferenceSet:
    """Fold flattened mentions of ONE surface expression into expression + set.

    ``mentions`` must all belong to the same surface citation (same source
    expression). They are folded into one :class:`ReferenceExpression` (the
    immutable surface fact) and one :class:`ReferenceResolution` carrying the
    full ordered target set and its :class:`ReferenceTargetSetSemantics`.

    ``surface_text`` / ``source_span`` override the per-mention surface fact when
    given (the extractor that owns the surface can pass the canonical span);
    otherwise they are taken from the first mention that carries them. NO silent
    drops: when the target extension cannot be enumerated, the semantics are
    classified explicitly (OPEN / NO_ENUMERABLE_EXTENSION).

    Raises:
        ValueError: if ``mentions`` is empty (nothing to fold).
    """
    if not mentions:
        raise ValueError("fold_reference_set requires at least one mention")

    # Surface fact: prefer the caller-supplied values, else the first mention's.
    text = surface_text
    if text is None:
        text = next((m.surface_text for m in mentions if m.surface_text), "")
    span = source_span
    if span is None:
        span = next((m.source_span for m in mentions if m.source_span is not None), None)

    # The concrete, ordered target set (preserve emission order, dedup identical
    # targets that the cartesian expansion may repeat). A None target is the
    # typed-correct outcome for OPEN/UNRESOLVED/BROKEN members and contributes no
    # enumerable extension.
    member_targets: list[ProvisionRef] = []
    seen: set[ProvisionRef] = set()
    for m in mentions:
        tgt = m.target_provision_ref
        if tgt is not None and tgt not in seen:
            seen.add(tgt)
            member_targets.append(tgt)

    semantics, status = _semantics_and_status(mentions, member_targets)
    expression_kind = _surface_expression_kind(text, len(member_targets))

    expression = ReferenceExpression.create(
        surface_text=text or "(no surface)",
        source_span=span,
        expression_kind=expression_kind,
    )
    resolution = ReferenceResolution(
        surface_expr_id=expression.surface_expr_id,
        target_set=tuple(member_targets),
        target_set_semantics=semantics,
        status=status,
        corpus_version=corpus_version,
        branch=branch,
    )
    return ReferenceSet(expression=expression, resolution=resolution)


__all__ = [
    "ReferenceSet",
    "fold_reference_set",
]
