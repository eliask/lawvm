"""Finland concrete typed primitive for fiscal pool / budget-line / quantity mentions.

Promotes pool and quantity phrases found in Finnish statute provision text to a
stable typed primitive that can be materialized as ``fi_pools.parquet`` and
queried via ``lawvm pools`` and ``lawvm sql``.

Design principles (AGENTS.md §1.9, STRINGLY_TYPED_SURFACE_AUDIT.md):
  - Frozen dataclass with slots=True -- no stringly-typed dicts crossing
    phase boundaries.
  - QuantityKind and PoolResolutionConfidence are closed enums -- not strings.
  - pool_canonical_id is None only when resolution_confidence is UNRESOLVED
    (typed absence, not missing key).
  - Ambiguous budget-line matches emit AmbiguousPoolMention (AGENTS.md §1.1).
  - Momentti renumbering between years emits BudgetLineRenumberingObservation.
  - Rejected candidates emit RejectedPoolCandidate (AGENTS.md §1.8).

This module hosts the FI fiscal-doctrine that previously lived in
``lawvm.core.pool_mention`` (Finnish talousarviolaki / paaluokka.luku.momentti
address grammar, yleiskate reserve, ``fi.budget.N.N.N`` canonical-id grammar).
The move follows AGENTS.md §2.3 -- jurisdiction-local drafting idioms live in
the frontend, not in core. Core retains only the abstract ``ProvisionMention``
marker protocol (``lawvm.core.pool_mention``); the concrete ``PoolMention``
dataclass explicitly inherits it so the AST-scan parity check in
``tests/test_core_firewall_no_fi_fiscal_doctrine.py`` keeps the producer set
equal to the protocol-implementer set (mirrors the ``ScopeConfidence``
precedent).

Source: Finlex Akoma Ntoso consolidated XML. The extractor that produces
``PoolMention`` records lives in ``lawvm.finland.pool_mention_extractor``;
the canonical budget-line registry it consults lives in
``lawvm.finland.canonical_budget_line_registry``.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Optional, Tuple, TypedDict

from lawvm.core.pool_mention import ProvisionMention


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class QuantityKind(Enum):
    """What kind of fiscal/quantity object is mentioned."""

    BUDGET_LINE = "budget_line"
    """Resolves to a budget act (talousarviolaki) address
    (main class.chapter.line / paaluokka.luku.momentti)."""

    FISCAL_POOL = "fiscal_pool"
    """Named pool that does not map to a single budget line (e.g. 'yleiskate')."""

    CAPACITY_CAP = "capacity_cap"
    """Quantity ceiling -- an explicit upper limit with a numeric value + unit."""

    THRESHOLD = "threshold"
    """Quantity floor or trigger -- a numeric value + unit that activates something."""

    FORMULA_TERM = "formula_term"
    """A named term in a statutory funding formula."""

    UNRESOLVED = "unresolved"
    """Phrase looks pool/quantity-shaped but cannot be classified further."""


class PoolResolutionConfidence(Enum):
    """How confidently the pool phrase was resolved to a canonical budget-line ID."""

    EXACT = "exact"
    """Canonical ID resolved directly against the per-year registry."""

    APPROXIMATE = "approximate"
    """Registry lookup via cross-year lineage (budget line (momentti) renumbering heuristic)."""

    UNRESOLVED = "unresolved"
    """No registry hit; canonical ID is None."""


class PoolMentionRow(TypedDict, total=False):
    """Projection row for one Finland pool mention."""

    source_statute_id: str
    source_provision_ref_str: str
    quantity_phrase: str
    pool_canonical_id: Optional[str]
    quantity_kind: str
    resolution_confidence: str
    numeric_value: Optional[float]
    unit: Optional[str]
    source_span_file: Optional[str]
    source_span_byte_offset: Optional[int]
    source_span_byte_len: Optional[int]
    valid_at_start: Optional[str]
    valid_at_end: Optional[str]


# ---------------------------------------------------------------------------
# PoolMention (the concrete typed primitive)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PoolMention(ProvisionMention):
    """A typed mention of a fiscal pool, budget line, or quantity threshold.

    This is the stable typed primitive for pool/quantity phrases in Finnish
    statute provision prose. It does NOT interpret the legal or fiscal force of
    the mention (shared vs exclusive pool, allocation priority, fair vs unfair)
    -- that is interpretation, downstream of LawVM.

    The primitive types:
      - where the mention is (source_provision_ref)
      - what text phrase was found (quantity_phrase)
      - what canonical budget-line ID it resolves to (pool_canonical_id, or None)
      - what kind of quantity object it is (quantity_kind)
      - how confidently the resolution was made (resolution_confidence)
      - what numeric value was extracted (numeric_value, or None)
      - what unit was extracted (unit, or None)
      - where in the source text it lives (source_span_*)
      - when this mention state holds (valid_at_*)

    Per AGENTS.md §1.1: ambiguous budget-line -> AmbiguousPoolMention, not silent pick.
    Per AGENTS.md §1.6: budget line (momentti) renumbering -> BudgetLineRenumberingObservation.
    Per AGENTS.md §1.8: rejected candidates emit RejectedPoolCandidate.

    Explicitly inherits ``lawvm.core.pool_mention.ProvisionMention`` so the AST-scan
    parity check (``tests/test_core_firewall_no_fi_fiscal_doctrine.py``) keeps
    the producer set equal to the protocol-implementer set (mirrors the
    ``ScopeConfidence`` precedent).
    """

    source_provision_ref: str
    """Serialized provision reference, e.g. '711/2022/3' or '2003/314'."""

    quantity_phrase: str
    """Literal phrase text found in the source, e.g. 'momentilla 28.91.50'."""

    pool_canonical_id: Optional[str]
    """Registry canonical ID, e.g. 'fi.budget.28.91.50'; None if UNRESOLVED."""

    quantity_kind: QuantityKind
    """What kind of pool/quantity object this mention represents."""

    resolution_confidence: PoolResolutionConfidence
    """How the canonical ID was resolved."""

    numeric_value: Optional[float]
    """Extracted numeric value, e.g. 7.5 for '7,5 g Cd/ha/5 v'."""

    unit: Optional[str]
    """Extracted unit string, e.g. 'g/ha/v', 'EUR', '%'."""

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
        if self.resolution_confidence != PoolResolutionConfidence.UNRESOLVED:
            if self.pool_canonical_id is None:
                raise ValueError(
                    "PoolMention.pool_canonical_id may only be None "
                    "when resolution_confidence is UNRESOLVED; "
                    f"got {self.resolution_confidence!r}"
                )
        if not self.quantity_phrase:
            raise ValueError("PoolMention.quantity_phrase must be non-empty")


# ---------------------------------------------------------------------------
# Observation types (AGENTS.md §1.1, §1.6, §1.8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RejectedPoolCandidate:
    """A pool/quantity candidate that was pattern-matched but rejected.

    Emitted when text that LOOKS like a pool/quantity phrase fails sanity or
    registry checks. Per AGENTS.md §1.8: no parse candidate disappears silently.
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
class AmbiguousPoolMention:
    """Finding emitted when a budget-line phrase maps to multiple registry entries.

    Per AGENTS.md §1.1: ambiguity must remain visible. The phrase is not
    silently resolved to one of the candidates. PoolMention is NOT emitted;
    this finding IS emitted instead.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref: str
    quantity_phrase: str
    candidate_canonical_ids: Tuple[str, ...]
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "candidate_canonical_ids", tuple(self.candidate_canonical_ids)
        )


@dataclass(frozen=True, slots=True)
class BudgetLineRenumberingObservation:
    """Observation emitted when a budget line (momentti) resolves via cross-year lineage.

    Budget line (momentti) numbers occasionally renumber across fiscal years.
    When a phrase like '28.91.50' appears in a statute and the exact budget line
    (momentti) exists in one year but has been renumbered in another, this
    observation documents the cross-year lineage heuristic.

    resolution_confidence=APPROXIMATE always pairs with this observation.
    """

    rule_id: str
    phase: str
    source_statute_id: str
    source_provision_ref: str
    quantity_phrase: str
    original_canonical_id: str
    """The ID as extracted from the source phrase."""
    resolved_canonical_id: str
    """The canonical ID after cross-year lineage resolution."""
    lineage_year: int
    """The year in which the original budget line (momentti) was found."""
    resolution_year: int
    """The year to which the budget line (momentti) was mapped via lineage."""
    reason: str
    blocking: bool = False
    strict_disposition: str = "record"


# ---------------------------------------------------------------------------
# Canonical ID factory (Finland ``fi.budget.N.N.N`` grammar)
# ---------------------------------------------------------------------------


def pool_canonical_id(momentti_code: str) -> str:
    """Build a Finland canonical budget-line ID from a momentti code.

    The canonical-id grammar is the Finland fiscal address

        fi.budget.<paaluokka>.<luku>.<momentti>

    e.g. ``pool_canonical_id('28.91.50') == 'fi.budget.28.91.50'``. The input
    is the bare ``paaluokka.luku.momentti`` triple as it appears in Finnish
    statute prose; the function only assembles the canonical form.

    Lives here rather than in ``lawvm.core.pool_mention`` because the
    ``"fi.budget."`` prefix and the ``paaluokka.luku.momentti`` semantics are
    Finnish fiscal doctrine (AGENTS.md §2.3). The extractor in
    ``lawvm.finland.pool_mention_extractor`` calls this when probing lineage
    across years.
    """
    return "fi.budget." + momentti_code


# ---------------------------------------------------------------------------
# Parquet row serialization helpers
# ---------------------------------------------------------------------------


def pool_mention_to_row(mention: PoolMention) -> PoolMentionRow:
    """Serialize a PoolMention to a flat dict for Parquet/JSONL output.

    Column names are stable per the brief's schema spec
    (POOL_MENTION_EXTRACTION.md §Projection export).
    Consumers must not depend on dict ordering; use column names.
    """
    return {
        "source_provision_ref_str": mention.source_provision_ref,
        "quantity_phrase": mention.quantity_phrase,
        "pool_canonical_id": mention.pool_canonical_id,
        "quantity_kind": mention.quantity_kind.value,
        "resolution_confidence": mention.resolution_confidence.value,
        "numeric_value": mention.numeric_value,
        "unit": mention.unit,
        "source_span_file": mention.source_span_file,
        "source_span_byte_offset": mention.source_span_byte_offset,
        "source_span_byte_len": mention.source_span_byte_len,
        "valid_at_start": mention.valid_at_start.isoformat() if mention.valid_at_start else None,
        "valid_at_end": mention.valid_at_end.isoformat() if mention.valid_at_end else None,
    }


__all__ = [
    "AmbiguousPoolMention",
    "BudgetLineRenumberingObservation",
    "PoolMention",
    "PoolMentionRow",
    "PoolResolutionConfidence",
    "QuantityKind",
    "RejectedPoolCandidate",
    "pool_canonical_id",
    "pool_mention_to_row",
]
