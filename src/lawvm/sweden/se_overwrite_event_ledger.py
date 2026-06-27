"""Sweden (SE) archive-write monotonicity — KNOW-01 overwrite-event ledger.

Brings SE's ``--force-reextract`` overwrite path into the knowledge-monotonicity
invariant family (pro-note §8 KNOW-01: "Every external update creates a new
manifestation / assertion / attestation, never mutates prior matter").

WHY. ``fetch_se_official_artifacts`` and ``hydrate_se_bundle_live`` accept a
``force_reextract=True`` flag that re-extracts the official SFS PDF + pdftotext
+ cleaned-act-text and calls ``archive.store()`` on cached locators
(``se://sfs/<id>/official.pdf.txt`` / ``official.cleaned.txt``). Plain
``archive.store`` overwrites in place — the prior manifestation's bytes vanish
without any record. This is **§1.6 "No unstated migration"** in evidence-plane
terms: the cached act's source-footing identity mutates silently across
versions. KNOW-01's fix is the second-side of the same coin — each overwrite
MUST create a new manifestation/assertion record, never silently mutate.

WHAT THIS SHIPS.

* A typed :class:`SEOverwriteEvent` dataclass capturing
  ``(locator, prior_bytes_sha256, new_bytes_sha256, source_trigger,
  rule_id, sfs_id)`` — the per-overwrite-occurrence record.

* :func:`se_store_with_overwrite_event` — a small wrapper around
  ``archive.store`` that captures the prior bytes' hash BEFORE the overwrite
  and emits an :class:`SEOverwriteEvent` into a caller-passed accumulator.

* The closed call-site vocabulary is the SE ``force_reextract=True`` path —
  first-time writes (no prior bytes) emit NO event (there is no overwrite to
  account for); only re-extracts that overlay prior bytes do.

WHAT THIS DOES **NOT** YET DO (honesty boundary):

* It does NOT enable historical byte retrieval (the prior bytes are hashed for
  identity, not stored raw — that needs an ``archive.store_with_history`` API
  + schema bump).
* It does NOT itself wire every force_reextract call site — for adapter-by-
  adapter migration, callers adopt :func:`se_store_with_overwrite_event`
  instead of ``archive.store`` at the known overwrite sites (theFetcher block
  at fetch.py around line 1329 + the official.act.json overwrite).
* The accumulator is per-call; a future pack-manifest member may commit it
  across a corpus (the way ``assumption_register_root`` does).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

# Schema + domain for the SE overwrite-event ledger.
_SCHEMA_SE_OVERWRITE_EVENT = "lawvm.se_overwrite_event.v0"
_DOMAIN_SE_OVERWRITE_EVENT = "se_official_artifacts_overwrite_event"

# The closed set of trigger strings the overwrite-event ledger records. A
# new trigger path landing in the SE acquire/extract side MUST register here —
# fail-loud, mirrors the catalogue/assumption-register universe discipline.
_SE_OVERWRITE_VALID_TRIGGERS = frozenset(
    {
        "force_reextract",
        "manual_reingest",
        "coercion_refresh",
    }
)

# Uniform rule_id for every overwrite event: the SE believed_spec owns the
# hypothesis for the family ("a force_reextract overwrite emits an event whose
# prior_bytes_sha256 != new_bytes_sha256 OR is a no-op"). Cataloged in
# ``_SE_RULE_SPECS`` as ``se_official_artifacts_force_reextract_overwrite``.
_SE_OVERWRITE_RULE_ID = "se_official_artifacts_force_reextract_overwrite"


@dataclass(frozen=True, slots=True)
class SEOverwriteEvent:
    """One archived-locator overwrite occurrence (KNOW-01 monotonicity).

    Lives in the EVIDENCE plane — never enters any semantic-object hash,
    like :class:`lawvm.core.assumption_register.AssumptionRegister`.
    """

    sfs_id: str
    locator: str
    prior_bytes_sha256: str = ""
    new_bytes_sha256: str = ""
    source_trigger: str = ""
    rule_id: str = _SE_OVERWRITE_RULE_ID

    def __post_init__(self) -> None:
        if self.source_trigger and self.source_trigger not in _SE_OVERWRITE_VALID_TRIGGERS:
            raise ValueError(
                f"SE overwrite-event trigger {self.source_trigger!r} is not in the "
                f"closed set {sorted(_SE_OVERWRITE_VALID_TRIGGERS)}. Either add the "
                f"new trigger path here in _SE_OVERWRITE_VALID_TRIGGERS or fix the "
                f"call site to use one of the known trigger strings."
            )
        if not self.locator or not self.locator.strip():
            raise ValueError("locator must be non-empty")
        if not self.sfs_id or not self.sfs_id.strip():
            raise ValueError("sfs_id must be non-empty")

    def to_canonical_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA_SE_OVERWRITE_EVENT,
            "sfs_id": str(self.sfs_id or ""),
            "locator": str(self.locator or ""),
            "prior_bytes_sha256": str(self.prior_bytes_sha256 or ""),
            "new_bytes_sha256": str(self.new_bytes_sha256 or ""),
            "source_trigger": str(self.source_trigger or ""),
            "rule_id": str(self.rule_id or ""),
        }

    @property
    def event_id(self) -> str:
        """Content id of the overwrite event — its only hash (never enters a
        semantic hash, mirrors the evidence-plane invariant of
        :mod:`lawvm.core.assumption_register`)."""
        digest = hashlib.sha256()
        digest.update(b"se-overwrite-event-v0\x00")
        digest.update(f"sfs={self.sfs_id}".encode("utf-8"))
        digest.update(b"\x00")
        digest.update(f"loc={self.locator}".encode("utf-8"))
        digest.update(b"\x00")
        digest.update(f"prior={self.prior_bytes_sha256}".encode("utf-8"))
        digest.update(b"\x00")
        digest.update(f"new={self.new_bytes_sha256}".encode("utf-8"))
        digest.update(b"\x00")
        digest.update(f"trigger={self.source_trigger}".encode("utf-8"))
        return f"sha256:{digest.hexdigest()}"


def _sha256_hex(data: bytes | None) -> str:
    """``sha256:<hex>`` for ``data`` (or empty string for ``None``/empty)."""
    if not data:
        return ""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def se_store_with_overwrite_event(
    archive: "object",
    locator: str,
    new_data: bytes,
    *,
    sfs_id: str,
    source_trigger: str,
    events_out: list[SEOverwriteEvent] | None = None,
    storage_class: str | None = None,
) -> "SEOverwriteEvent":
    """Wrap ``archive.store`` with a KNOW-01 overwrite-event emission.

    Reads the prior bytes via ``archive.get(locator)`` BEFORE the overwrite,
    hashes both, and appends a typed :class:`SEOverwriteEvent` to
    ``events_out`` (if provided) so the prior manifestation's identity is
    preserved in the ledger even after the in-place overwrite.

    The event is emitted even when prior == new (re-extraction produced
    byte-identical output) — the monotonicity invariant is "every external
    update creates a new manifestation record"; the record is the proof the
    update happened, not that bytes changed.
    """
    if events_out is None:
        # No accumulator → no ledger to write to. Still store (do not crash the
        # caller); the overwrite is un-audited.
        cast(Any, archive).store(locator, new_data, storage_class=storage_class)
        return SEOverwriteEvent(
            sfs_id=sfs_id,
            locator=locator,
            prior_bytes_sha256="",
            new_bytes_sha256="",
            source_trigger=source_trigger,
        )
    prior_bytes = cast(Any, archive).get(locator)
    prior_hash = _sha256_hex(prior_bytes)
    new_hash = _sha256_hex(new_data)
    event = SEOverwriteEvent(
        sfs_id=sfs_id,
        locator=locator,
        prior_bytes_sha256=prior_hash,
        new_bytes_sha256=new_hash,
        source_trigger=source_trigger,
    )
    events_out.append(event)
    cast(Any, archive).store(locator, new_data, storage_class=storage_class)
    return event


def se_overwrite_event_root(events: Sequence[SEOverwriteEvent]) -> str:
    """Content-addressed MapRoot over overwrite event_ids — the committed
    ledger root over all overwrite occurrences in a single acquire/extract
    invocation.

    Order-independent (events are sorted by their (sfs_id, locator,
    prior_bytes_sha256) tuple before hashing so a future duplicate accumulator
    can't perturb the root by listing the same set differently). Empty input
    is a valid empty MapRoot (the v0 "no overwrites happened" case).
    """
    from lawvm.substrate.roots import map_root

    sorted_events = sorted(
        events,
        key=lambda e: (e.sfs_id, e.locator, e.prior_bytes_sha256, e.new_bytes_sha256),
    )
    return map_root(
        _DOMAIN_SE_OVERWRITE_EVENT,
        {e.event_id: e.event_id for e in sorted_events},
    )


__all__ = [
    "SEOverwriteEvent",
    "se_store_with_overwrite_event",
    "se_overwrite_event_root",
]
