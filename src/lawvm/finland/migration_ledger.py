"""Append-only ledger for address-migration events during Finland replay.

Records renumber and move operations as typed ``MigrationEvent`` instances,
enabling lineage queries and address-chain resolution after replay completes.

The ledger is a simple accumulator — it does not mutate replay state itself.
Callers (apply_typed_dispatch.py) record events at the point where relabel/move
tree surgery succeeds, and the finished ledger is surfaced through the
``CanonicalBundle.migration_events`` slot.
"""

from __future__ import annotations

from collections.abc import Iterable
from functools import lru_cache

from lawvm.core.ir import LegalAddress
from lawvm.core.mutation_boundary import TreePath
from lawvm.core.provenance import MigrationEvent
from lawvm.core.timeline import current_address_from_migration_events
from lawvm.core.timeline_lineage import (
    PrefixMigrationEventSignature,
    current_address_with_prefix_migrations_from_event_signatures as _core_prefix_migration_signatures,
)
from lawvm.core.timeline_lineage import (
    current_address_with_prefix_migrations_from_events as _core_prefix_migrations,
)
from lawvm.core.timeline_lineage import prefix_migration_event_signatures
from lawvm.core.tree_ops import normalized_label_key
from lawvm.finland.helpers import _norm_num_token


_NUMERIC_ADDRESS_KINDS = frozenset({"part", "chapter", "section", "subsection"})
_ITEM_ADDRESS_KINDS = frozenset({"item", "paragraph", "subparagraph"})


def _normalize_migration_label(kind: str, label: str) -> str:
    if kind in _NUMERIC_ADDRESS_KINDS:
        return _norm_num_token(label)
    if kind in _ITEM_ADDRESS_KINDS:
        return normalized_label_key(label)
    return normalized_label_key(label)


def normalize_address_path(path: TreePath) -> TreePath:
    """Normalize address labels for Finland migration-wave matching.

    Numeric structural labels accept Roman/Arabic equivalence, but ``kohta``
    labels do not: Finnish item labels can be plain letters such as ``i``.
    Treating every path label as a numeric token silently retargets ``i kohta``
    to ``1 kohta``.
    """
    return tuple(
        (kind, _normalize_migration_label(kind, label))
        for kind, label in path
        if label
    )


@lru_cache(maxsize=65536)
def _normalize_address(address: LegalAddress) -> LegalAddress:
    return LegalAddress(path=normalize_address_path(address.path), special=address.special)


def migration_lower_bound_for_op(op: object) -> str:
    """Lower-bound enactment date for following prefix migrations of *op*'s address.

    Returns the op source's *enacted* date, used as ``not_before`` so the
    address does not inherit renumber/move waves that predate its content
    lineage (slot reuse / container rebirth). The anchor is the enactment date,
    not the effective/commencement date: a section's content lineage begins when
    it is enacted, and any renumber/move enacted-or-later applies to it even if
    the section's own commencement is delayed. Anchoring on the (possibly later)
    commencement date would wrongly drop relabels that land between enactment and
    a delayed commencement — e.g. a section enacted 2018, recodified 2019, that
    only commences 2020 must still follow the 2019 relabel. Falls back to the
    source effective date, then the raw legal-op source. Empty string disables
    the bound (legacy behaviour) when no date is recoverable.
    """
    resolved_source = getattr(op, "resolved_op_source", None)
    if resolved_source is not None:
        enacted = getattr(resolved_source, "enacted", "") or ""
        if enacted:
            return enacted
        effective = getattr(resolved_source, "effective", "") or ""
        if effective:
            return effective
    op_lo = getattr(op, "lo", None)
    if op_lo is not None:
        lo_source = getattr(op_lo, "source", None)
        if lo_source is not None:
            enacted = getattr(lo_source, "enacted", "") or ""
            if enacted:
                return enacted
            return getattr(lo_source, "effective", "") or ""
    return ""


def current_address_with_prefix_migrations_from_events(
    original_address: LegalAddress,
    migration_events: tuple[MigrationEvent, ...],
    as_of_date: str = "",
    not_before: str = "",
) -> LegalAddress:
    """Finland wrapper over the shared prefix/wave migration resolver.

    When a migration applies, the resolved (normalized) address is returned.
    When none applies the original address is preserved, except for a pure
    label canonicalization (Roman/Arabic equivalence) that leaves the path
    shape unchanged: a provision already sitting at its current address —
    including one born directly into a recodified container labelled with a
    Roman numeral — must key onto the same canonical address as a provision
    that migrates there, otherwise the two lineages split across a Roman/Arabic
    label mismatch (``part:I`` vs ``part:1``) and the destination occupant is
    duplicated instead of merged. Path-reshaping normalizations (e.g. dropping
    an empty ``hcontainer`` wrapper) are not applied to an unmigrated address.
    """
    normalized_original = _normalize_address(original_address)
    migrated = _core_prefix_migrations(
        original_address,
        migration_events,
        as_of_date=as_of_date,
        not_before=not_before,
        normalize_address_fn=_normalize_address,
    )
    if migrated == normalized_original:
        if len(normalized_original.path) == len(original_address.path):
            return normalized_original
        return original_address
    return migrated


def current_address_with_prefix_migrations_from_event_signatures(
    original_address: LegalAddress,
    migration_event_signatures: tuple[PrefixMigrationEventSignature, ...],
    as_of_date: str = "",
    not_before: str = "",
) -> LegalAddress:
    """Finland wrapper over precomputed shared prefix migration signatures."""
    normalized_original = _normalize_address(original_address)
    migrated = _core_prefix_migration_signatures(
        original_address,
        migration_event_signatures,
        as_of_date=as_of_date,
        not_before=not_before,
        normalize_address_fn=_normalize_address,
    )
    if migrated == normalized_original:
        if len(normalized_original.path) == len(original_address.path):
            return normalized_original
        return original_address
    return migrated


class MigrationLedger:
    """Accumulates MigrationEvent objects during replay.

    Thread-safety: not thread-safe.  Designed for single-threaded replay where
    one amendment-at-a-time application is the norm.
    """

    __slots__ = ("_events", "_prefix_cache", "_prefix_signature_cache")

    def __init__(self, events: Iterable[MigrationEvent] = ()) -> None:
        self._events: list[MigrationEvent] = list(events)
        self._prefix_cache: dict[tuple[LegalAddress, str, str], LegalAddress] = {}
        self._prefix_signature_cache: tuple[PrefixMigrationEventSignature, ...] | None = None

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    @staticmethod
    def _make_event_id(
        source_statute: str,
        from_address: LegalAddress,
        to_address: LegalAddress,
    ) -> str:
        """Deterministic event ID: ``mig:<source>:<from>→<to>``."""
        return f"mig:{source_statute}:{from_address}\u2192{to_address}"

    def record_renumber(
        self,
        from_addr: LegalAddress,
        to_addr: LegalAddress,
        effective: str = "",
        source_statute: str = "",
        *,
        witness: object | None = None,
    ) -> MigrationEvent:
        """Record a renumber (relabel-in-place) migration event."""
        normalized_from = _normalize_address(from_addr)
        normalized_to = _normalize_address(to_addr)
        event = MigrationEvent(
            event_id=self._make_event_id(source_statute, normalized_from, normalized_to),
            kind="renumber",
            from_address=normalized_from,
            to_address=normalized_to,
            effective=effective,
            source_statute=source_statute,
            witness=witness,
        )
        self._events.append(event)
        self._prefix_cache.clear()
        self._prefix_signature_cache = None
        return event

    def record_move(
        self,
        from_addr: LegalAddress,
        to_addr: LegalAddress,
        effective: str = "",
        source_statute: str = "",
        *,
        witness: object | None = None,
    ) -> MigrationEvent:
        """Record a move (cross-parent transfer) migration event."""
        normalized_from = _normalize_address(from_addr)
        normalized_to = _normalize_address(to_addr)
        event = MigrationEvent(
            event_id=self._make_event_id(source_statute, normalized_from, normalized_to),
            kind="move",
            from_address=normalized_from,
            to_address=normalized_to,
            effective=effective,
            source_statute=source_statute,
            witness=witness,
        )
        self._events.append(event)
        self._prefix_cache.clear()
        self._prefix_signature_cache = None
        return event

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def query_lineage(self, address: LegalAddress) -> list[MigrationEvent]:
        """Return all events where *address* appears as source or destination."""
        normalized_address = _normalize_address(address)
        return [
            e for e in self._events
            if e.from_address == normalized_address or e.to_address == normalized_address
        ]

    def current_address(
        self,
        original_address: LegalAddress,
        as_of_date: str = "",
    ) -> LegalAddress:
        """Follow the renumber/move chain forward from *original_address*.

        If *as_of_date* is non-empty, only events with ``effective <= as_of_date``
        are considered.  Returns the final address in the chain, or
        *original_address* unchanged if no migrations match.
        """
        return current_address_from_migration_events(original_address, tuple(self._events), as_of_date=as_of_date)

    def current_address_with_prefix_migrations(
        self,
        original_address: LegalAddress,
        as_of_date: str = "",
        not_before: str = "",
    ) -> LegalAddress:
        """Follow renumber/move links across any matching address prefix.

        This is stronger than ``current_address()``: it rewrites descendant
        addresses when one of their ancestor prefixes has been renumbered or
        moved. Example: ``part:III/chapter:2/section:159`` can migrate through
        ``part:III -> part:IV`` and ``part:IV/chapter:2 -> part:IV/chapter:18``
        even if there is no explicit section-level migration event for the full
        descendant path.

        ``not_before`` excludes renumber/move waves that predate this address's
        own content lineage, so a section born into a renumber-vacated slot does
        not inherit the prior occupant's stale renumber chain.
        """
        key = (original_address, as_of_date, not_before)
        cached = self._prefix_cache.get(key)
        if cached is not None:
            return cached
        if self._prefix_signature_cache is None:
            self._prefix_signature_cache = prefix_migration_event_signatures(
                tuple(self._events)
            )
        resolved = current_address_with_prefix_migrations_from_event_signatures(
            original_address,
            self._prefix_signature_cache,
            as_of_date,
            not_before=not_before,
        )
        self._prefix_cache[key] = resolved
        return resolved

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    @property
    def events(self) -> tuple[MigrationEvent, ...]:
        """Snapshot of accumulated events as an immutable tuple."""
        return tuple(self._events)

    def __len__(self) -> int:
        return len(self._events)

    def __bool__(self) -> bool:
        return bool(self._events)
