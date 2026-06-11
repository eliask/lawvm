"""Core typed primitive for institutional actor mentions in provision text.

Promotes actor phrases found in Finnish statute provision text to a stable
typed primitive that can be materialized as ``fi_actors.parquet`` and queried
via ``lawvm actors`` and ``lawvm sql``.

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True — no stringly-typed dicts crossing
    phase boundaries.
  - ActorModalKind and ActorResolutionConfidence are closed enums — not strings.
  - actor_canonical_id is None only when resolution_confidence is UNRESOLVED
    (typed absence, not missing key).
  - Ambiguous phrases emit AmbiguousActorMention finding (AGENTS.md §1.1).
  - Lifecycle resolutions emit LifecycleActorObservation (AGENTS.md §1.6).
  - Rejected candidates emit RejectedActorCandidate (AGENTS.md §1.8).

This module has no Finland-specific imports. Finland extraction lives in
``lawvm.finland.actor_mention_extractor``.
This module only holds the shared typed primitive and observation types.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ActorModalKind(Enum):
    """Syntactic modal class of the actor mention context."""

    DUTY = "duty"
    """Obligation pattern: 'X:n on toimitettava', 'viranomaisen on'."""

    DISCRETION = "discretion"
    """Discretion pattern: 'X voi', 'viranomainen voi'."""

    PERMISSION = "permission"
    """Permission pattern: 'X saa', 'viranomainen saa'."""

    PROHIBITION = "prohibition"
    """Prohibition pattern: 'X ei saa', 'viranomainen ei saa'."""

    MENTION = "mention"
    """Actor named without a modal verb — plain reference."""

    PASSIVE_OBLIGATION = "passive_obligation"
    """Passive obligation: 'tehtävänä on', 'X:n tehtävänä on'."""

    UNRESOLVED = "unresolved"
    """Modal kind not classifiable from available context."""


class ActorResolutionConfidence(Enum):
    """How confidently the actor phrase was resolved to a canonical ID."""

    EXACT = "exact"
    """Canonical ID from a typed AKN TLCOrganization element."""

    REGISTRY_RESOLVED = "registry_resolved"
    """Matched against the canonical actor registry by phrase variant."""

    LIFECYCLE_RESOLVED = "lifecycle_resolved"
    """Resolved via agency lifecycle (e.g. Evira -> Ruokavirasto)."""

    UNRESOLVED = "unresolved"
    """Phrase not matched in registry; canonical ID is None."""


# ---------------------------------------------------------------------------
# ActorMention (the core typed primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActorMention:
    """A typed mention of an institutional actor within a provision.

    This does NOT interpret the normative force of the mention (responsibility
    allocation, authority transfer, capture shape) -- that is interpretation,
    downstream of LawVM.

    The primitive types:
      - where the mention is (source_provision_ref)
      - what text phrase was found (actor_phrase)
      - what canonical ID it resolves to (actor_canonical_id, or None)
      - what canonical display string it has (actor_canonical_show_as)
      - what syntactic modal class the context implies (modal_kind)
      - how confidently the resolution was made (resolution_confidence)
      - where in the source text it lives (source_span_*)
      - when this mention state holds (valid_at_*)

    Per AGENTS.md §1.1: ambiguous phrases emit AmbiguousActorMention finding,
    not a silent pick.
    Per AGENTS.md §1.6: lifecycle resolutions emit LifecycleActorObservation.
    Per AGENTS.md §1.8: rejected candidates emit RejectedActorCandidate.
    """

    source_provision_ref: str
    """Serialized provision reference, e.g. '2019/561/3' or '2003/314'."""

    actor_phrase: str
    """Literal phrase text found in the source, e.g. 'Ruokavirasto'."""

    actor_canonical_id: Optional[str]
    """Registry canonical ID, e.g. 'fi.agency.ruokavirasto'; None if UNRESOLVED."""

    actor_canonical_show_as: Optional[str]
    """Canonical display string, e.g. 'Ruokavirasto'; None if UNRESOLVED."""

    modal_kind: ActorModalKind
    """Syntactic modal class of this actor mention."""

    resolution_confidence: ActorResolutionConfidence
    """How the canonical ID was resolved."""

    source_span_file: Optional[str]
    """Source file path for provenance; None for metadata-derived mentions."""

    source_span_byte_offset: Optional[int]
    """Byte offset of the phrase in the source file; None if unavailable."""

    source_span_byte_len: Optional[int]
    """Byte length of the phrase; None if unavailable."""

    valid_at_start: Optional[date]
    """When this mention state begins; None = always valid."""

    valid_at_end: Optional[date]
    """When this mention state ends; None = currently valid."""

    def __post_init__(self) -> None:
        if self.resolution_confidence != ActorResolutionConfidence.UNRESOLVED:
            if self.actor_canonical_id is None:
                raise ValueError(
                    "ActorMention.actor_canonical_id may only be None "
                    "when resolution_confidence is UNRESOLVED; "
                    f"got {self.resolution_confidence!r}"
                )
        if not self.actor_phrase:
            raise ValueError("ActorMention.actor_phrase must be non-empty")


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.1, §1.6, §1.8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedActorCandidate:
    """An actor candidate that was pattern-matched but rejected.

    Emitted when text that LOOKS like an actor phrase fails sanity or registry
    checks. Per AGENTS.md §1.8: no parse candidate disappears silently.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    reason: str
    matched_text: str
    source_span_file: Optional[str]
    source_span_byte_offset: Optional[int]
    source_span_byte_len: Optional[int]
    blocking: bool = False
    strict_disposition: str = "record"


@dataclass(frozen=True, slots=True)
class AmbiguousActorMention:
    """Finding emitted when an actor phrase maps to multiple registry entries.

    Per AGENTS.md §1.1: ambiguity must remain visible. The phrase is not
    silently resolved to one of the candidates.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref: str
    actor_phrase: str
    candidate_canonical_ids: Tuple[str, ...]
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_canonical_ids", tuple(self.candidate_canonical_ids)
        )


@dataclass(frozen=True, slots=True)
class LifecycleActorObservation:
    """Observation emitted when a pre-merger phrase resolves via lifecycle.

    Per AGENTS.md §1.6: lifecycle resolutions (Evira -> Ruokavirasto) must
    emit a typed observation. resolution_confidence=LIFECYCLE_RESOLVED always
    pairs with this observation.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref: str
    actor_phrase: str
    predecessor_id: str
    successor_id: str
    lifecycle_date: date
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Parquet row serialization helpers
# ---------------------------------------------------------------------------


def actor_mention_to_row(mention: ActorMention) -> dict[str, object]:
    """Serialize an ActorMention to a flat dict for Parquet/JSONL output.

    Column names are stable per the brief's schema spec
    (ACTOR_MENTION_EXTRACTION.md §Projection export).
    Consumers must not depend on dict ordering; use column names.
    """
    return {
        "source_provision_ref_str": mention.source_provision_ref,
        "actor_phrase": mention.actor_phrase,
        "actor_canonical_id": mention.actor_canonical_id,
        "actor_canonical_show_as": mention.actor_canonical_show_as,
        "modal_kind": mention.modal_kind.value,
        "resolution_confidence": mention.resolution_confidence.value,
        "source_span_file": mention.source_span_file,
        "source_span_byte_offset": mention.source_span_byte_offset,
        "source_span_byte_len": mention.source_span_byte_len,
        "valid_at_start": mention.valid_at_start.isoformat() if mention.valid_at_start else None,
        "valid_at_end": mention.valid_at_end.isoformat() if mention.valid_at_end else None,
    }
