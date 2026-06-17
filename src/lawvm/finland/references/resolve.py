"""Resolution PROJECTION: unresolved-by-identity mentions -> ResolvedReference.

The recognizer lanes (``references/by_name.py``, ``references/eu_directive.py``)
deliberately emit references whose target carries an UNRESOLVED-by-identity
placeholder rather than a fabricated statute id:

* by-name cross-statute refs carry
  ``target_provision_ref.statute_id = "fi-name:<normalized_name>"`` with
  ``cite_confidence = STATUTE_ONLY``;
* EU-by-nickname directive refs carry
  ``target_provision_ref.statute_id = "eu-nickname:<surface>"`` with
  ``cite_confidence`` in {AMBIGUOUS, STATUTE_ONLY, EXACT}.

This module is the downstream PROJECTION that resolves those placeholders against
the registries that already exist (``registries/statute_name.py``,
``registries/eu_nickname.py``). It is the point where the two-stage
``ReferenceExpr -> ResolvedReference`` model
(``notes_internal/FI_PARSE_OVERLAY_IR_MODEL.md``) materializes: the placeholder
mention is the ``ReferenceExpr`` (what the text SAYS), and a
:class:`ResolvedReference` is what it POINTS TO (status + work_id|None +
candidates + rejected_candidates + finding).

Discipline (fail-loud / tag-don't-guess, §0.3):

* A single registry candidate -> ``status=resolved`` and the placeholder is
  rewritten to the real statute/CELEX id in a NEW mention
  (``dataclasses.replace`` — the input mention is NEVER mutated).
* More than one candidate -> ``status=ambiguous``: ALL candidates are listed, a
  finding is emitted, and ``work_id`` stays ``None``. The registry/projection
  NEVER picks one.
* A registry MISS -> ``status=statute_only``: the act identity is textual but the
  id is pending. This is a coverage gap recorded as such, NOT a silent
  ``resolved`` to nothing.
* Already-resolved mentions (explicit ``NNN/YYYY`` id, internal self-reference,
  treaty with a SopS id) pass through ``status=unchanged`` with no registry call.
* OPEN (vague catch-all) mentions pass through ``status=open``.

This is a PURE downstream projection: it does not edit any recognizer or
registry module.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from lawvm.core.reference_mention import (
    AmbiguousReferenceFinding,
    CiteConfidence,
    ReferenceMention,
)
from lawvm.finland.references.registries import eu_nickname
from lawvm.finland.references.registries.statute_name import (
    StatuteNameRegistry,
    build_registry,
    sample_entries_from_farchive,
)

# Placeholder id prefixes emitted by the recognizer lanes (UNRESOLVED-by-identity).
_FI_NAME_PREFIX = "fi-name:"
_EU_NICKNAME_PREFIX = "eu-nickname:"

# Stable rule id + phase for the ambiguity finding (mirrors the existing
# cross_ref_extraction finding conventions in core.reference_mention).
_AMBIGUOUS_RULE_ID = "fi_ref_resolve_ambiguous_name"
_RESOLVE_PHASE = "reference_resolution"


# ---------------------------------------------------------------------------
# Resolution status + output record
# ---------------------------------------------------------------------------


class ResolutionStatus(Enum):
    """Outcome of resolving one placeholder mention against the registries.

    The fail-loud control signal of the projection stage (mirrors
    ``CiteConfidence`` but at the resolution layer, per
    ``FI_PARSE_OVERLAY_IR_MODEL.md`` ``ResolvedReference.status``).
    """

    RESOLVED = "resolved"
    """Exactly one registry candidate — placeholder rewritten to the real id."""

    AMBIGUOUS = "ambiguous"
    """>1 candidate — all listed, a finding emitted, never picked."""

    STATUTE_ONLY = "statute_only"
    """Registry miss — act identity textual, id pending (a coverage gap)."""

    OPEN = "open"
    """Vague catch-all reference — names no target by construction."""

    BROKEN = "broken"
    """Target was repealed/renumbered after the citation was written."""

    UNCHANGED = "unchanged"
    """Already resolved upstream (explicit id / internal / treaty) — pass-through."""


@dataclass(frozen=True, slots=True)
class ResolvedReference:
    """What a placeholder mention POINTS TO after registry resolution.

    The second stage of the ``ReferenceExpr -> ResolvedReference`` model: the
    ``mention`` is the (possibly rewritten) typed reference, ``status`` is the
    fail-loud resolution outcome, ``work_id`` is the resolved statute/CELEX id
    (``None`` unless ``status`` is RESOLVED or UNCHANGED), ``candidates`` lists
    every candidate the registry returned (the full set when AMBIGUOUS), and
    ``finding`` carries the audit record for an ambiguous resolution (``None``
    otherwise).

    Attributes:
        mention: The typed reference. For a RESOLVED placeholder this is a NEW
            mention (via ``dataclasses.replace``) whose target id is the real
            statute/CELEX id; in every other case it is the input mention,
            unmutated.
        status: The resolution outcome (:class:`ResolutionStatus`).
        work_id: The resolved statute/CELEX id, or ``None`` when not a single
            unambiguous resolution.
        candidates: All candidate ids the registry returned for this mention
            (empty on a miss / pass-through). For AMBIGUOUS, all are listed and
            none is chosen.
        rejected_candidates: Candidate ids considered but not selected. The
            projection never picks among multiple candidates, so this is empty
            here (reserved for downstream tier resolution); included for parity
            with the ``ResolvedReference`` model.
        finding: An :class:`AmbiguousReferenceFinding` when ``status`` is
            AMBIGUOUS, else ``None``.
    """

    mention: ReferenceMention
    status: ResolutionStatus
    work_id: Optional[str]
    candidates: tuple[str, ...]
    rejected_candidates: tuple[str, ...]
    finding: Optional[AmbiguousReferenceFinding]


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _placeholder_kind(mention: ReferenceMention) -> Optional[str]:
    """Return the placeholder prefix carried by ``mention``, or ``None``.

    A mention is an UNRESOLVED-by-identity placeholder iff its target id starts
    with ``fi-name:`` or ``eu-nickname:``. Returns the matched prefix so the
    caller routes to the right registry.
    """
    target = mention.target_provision_ref
    if target is None:
        return None
    sid = target.statute_id
    if sid.startswith(_FI_NAME_PREFIX):
        return _FI_NAME_PREFIX
    if sid.startswith(_EU_NICKNAME_PREFIX):
        return _EU_NICKNAME_PREFIX
    return None


def _rewrite_target_id(mention: ReferenceMention, work_id: str) -> ReferenceMention:
    """Return a NEW mention with the target's statute_id rewritten to ``work_id``.

    The input mention is never mutated (frozen dataclasses, ``replace``). The
    cite_confidence is promoted to EXACT — the identity is now resolved to a
    single real id.
    """
    target = mention.target_provision_ref
    assert target is not None  # guarded by caller
    new_target = dataclasses.replace(target, statute_id=work_id)
    return dataclasses.replace(
        mention,
        target_provision_ref=new_target,
        cite_confidence=CiteConfidence.EXACT,
    )


def _ambiguity_finding(
    mention: ReferenceMention,
    candidates: tuple[str, ...],
) -> AmbiguousReferenceFinding:
    """Build the audit finding for an ambiguous placeholder resolution."""
    src = mention.source_provision_ref
    target = mention.target_provision_ref
    surface = mention.surface_text or (target.statute_id if target else "")
    return AmbiguousReferenceFinding(
        rule_id=_AMBIGUOUS_RULE_ID,
        phase=_RESOLVE_PHASE,
        source_statute_id=src.statute_id,
        source_provision_ref_str=src.serialized(),
        candidate_target_ids=candidates,
        reason=(
            f"Reference surface {surface!r} resolves to "
            f"{len(candidates)} candidates; the registry refuses to pick one."
        ),
    )


def _resolve_fi_name(
    mention: ReferenceMention,
    statute_registry: StatuteNameRegistry,
    as_of: Optional[dt.date],
) -> ResolvedReference:
    """Resolve a ``fi-name:<name>`` placeholder against the statute registry."""
    target = mention.target_provision_ref
    assert target is not None
    name = target.statute_id[len(_FI_NAME_PREFIX) :]
    result = statute_registry.lookup(name, as_of)
    candidate_ids = tuple(c.statute_id for c in result.candidates)

    if result.status == "single":
        work_id = candidate_ids[0]
        return ResolvedReference(
            mention=_rewrite_target_id(mention, work_id),
            status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    if result.status == "multiple":
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, candidate_ids),
        )
    # "none" — a registry miss: the act is textual, the id is pending.
    return ResolvedReference(
        mention=mention,
        status=ResolutionStatus.STATUTE_ONLY,
        work_id=None,
        candidates=(),
        rejected_candidates=(),
        finding=None,
    )


def _resolve_eu_nickname(mention: ReferenceMention) -> ResolvedReference:
    """Resolve an ``eu-nickname:<surface>`` placeholder against the EU registry."""
    target = mention.target_provision_ref
    assert target is not None
    surface = target.statute_id[len(_EU_NICKNAME_PREFIX) :]
    result = eu_nickname.lookup(surface)
    candidate_ids = tuple(f"celex:{celex}" for celex in result.candidates)

    if result.status is eu_nickname.RegistryStatus.SINGLE:
        work_id = candidate_ids[0]
        return ResolvedReference(
            mention=_rewrite_target_id(mention, work_id),
            status=ResolutionStatus.RESOLVED,
            work_id=work_id,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=None,
        )
    if result.status is eu_nickname.RegistryStatus.MULTIPLE:
        return ResolvedReference(
            mention=dataclasses.replace(
                mention, cite_confidence=CiteConfidence.AMBIGUOUS
            ),
            status=ResolutionStatus.AMBIGUOUS,
            work_id=None,
            candidates=candidate_ids,
            rejected_candidates=(),
            finding=_ambiguity_finding(mention, candidate_ids),
        )
    # NONE — nickname-shaped but unknown to the registry: id pending.
    return ResolvedReference(
        mention=mention,
        status=ResolutionStatus.STATUTE_ONLY,
        work_id=None,
        candidates=(),
        rejected_candidates=(),
        finding=None,
    )


def _passthrough(mention: ReferenceMention) -> ResolvedReference:
    """Project a non-placeholder mention with no registry call.

    OPEN (vague catch-all) -> ``status=open`` (targetless by construction).
    BROKEN -> ``status=broken``. Everything else (explicit id, internal,
    treaty) is already resolved upstream -> ``status=unchanged`` with the
    existing target id as ``work_id``.
    """
    conf = mention.cite_confidence
    if conf is CiteConfidence.OPEN:
        return ResolvedReference(
            mention=mention,
            status=ResolutionStatus.OPEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    if conf is CiteConfidence.BROKEN:
        return ResolvedReference(
            mention=mention,
            status=ResolutionStatus.BROKEN,
            work_id=None,
            candidates=(),
            rejected_candidates=(),
            finding=None,
        )
    target = mention.target_provision_ref
    work_id = target.statute_id if target is not None else None
    return ResolvedReference(
        mention=mention,
        status=ResolutionStatus.UNCHANGED,
        work_id=work_id,
        candidates=(work_id,) if work_id else (),
        rejected_candidates=(),
        finding=None,
    )


def resolve_mention(
    mention: ReferenceMention,
    *,
    statute_registry: StatuteNameRegistry,
    eu_registry: object = eu_nickname,
    as_of: Optional[dt.date] = None,
) -> ResolvedReference:
    """Resolve a single mention's placeholder identity against the registries.

    See :func:`resolve_mentions` for the routing contract. ``eu_registry`` is
    accepted for interface symmetry with the statute registry; the EU lookup is
    a module-level pure function (``eu_nickname.lookup``), so the default is the
    module itself and no per-call state is threaded.
    """
    del eu_registry  # the eu_nickname module's lookup is a pure function
    kind = _placeholder_kind(mention)
    if kind == _FI_NAME_PREFIX:
        return _resolve_fi_name(mention, statute_registry, as_of)
    if kind == _EU_NICKNAME_PREFIX:
        return _resolve_eu_nickname(mention)
    return _passthrough(mention)


def resolve_mentions(
    mentions: list[ReferenceMention],
    *,
    statute_registry: StatuteNameRegistry,
    eu_registry: object = eu_nickname,
    as_of: Optional[dt.date] = None,
) -> list[ResolvedReference]:
    """Project placeholder mentions to :class:`ResolvedReference` records.

    Routing (per mention):

    * ``fi-name:<name>`` target -> look up in ``statute_registry``:
      single -> RESOLVED (placeholder rewritten to the real id in a NEW
      mention), multiple -> AMBIGUOUS (all candidates, a finding, no pick),
      none -> STATUTE_ONLY (registry miss = coverage gap, not a silent resolve).
    * ``eu-nickname:<surface>`` target -> same against the EU nickname registry
      (resolved id is ``celex:<CELEX>``).
    * already-resolved (explicit id / internal / treaty with a SopS id) ->
      UNCHANGED pass-through (no registry call), ``work_id`` = the existing id.
    * OPEN (vague) -> OPEN pass-through; BROKEN -> BROKEN pass-through.

    Fail-loud: never invents an id; >1 candidate is always AMBIGUOUS with every
    candidate listed; a registry miss is STATUTE_ONLY, never a silent RESOLVED.

    Args:
        mentions: The recognizer-emitted typed references to resolve.
        statute_registry: The built statute-name registry (Index B).
        eu_registry: The EU nickname registry module (default: the module).
        as_of: The validity instant the citations are read against
            (static-as-of-citing). ``None`` resolves against the whole timeline
            (and is allowed to be AMBIGUOUS).

    Returns:
        One :class:`ResolvedReference` per input mention, in input order.
    """
    return [
        resolve_mention(
            m,
            statute_registry=statute_registry,
            eu_registry=eu_registry,
            as_of=as_of,
        )
        for m in mentions
    ]


def build_default_registries(
    *,
    statute_sample_limit: int = 500,
) -> tuple[StatuteNameRegistry, object]:
    """Build the default (statute_name, eu_nickname) registry pair.

    Convenience for callers that want a ready-to-use registry pair. The
    statute-name registry is built from a SMALL sample of farchive titles
    (``statute_sample_limit``, default 500) — the full ~56k-title corpus
    registry is a later data-build artifact step, NOT done here (memory). The EU
    registry is the ``eu_nickname`` module (its ``lookup`` is a pure function).

    Returns ``(statute_registry, eu_nickname_module)``.
    """
    entries = sample_entries_from_farchive(limit=statute_sample_limit)
    statute_registry = build_registry(entries)
    return statute_registry, eu_nickname


__all__ = [
    "ResolutionStatus",
    "ResolvedReference",
    "build_default_registries",
    "resolve_mention",
    "resolve_mentions",
]
