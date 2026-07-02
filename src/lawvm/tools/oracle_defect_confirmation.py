"""External-confirmation rail for oracle-defect (``oracle_suspect``) residuals.

LawVM's ``oracle_suspect`` verdict (see ``core/oracle_divergence.py`` and the
``oracle_editorial_pathology`` ``AgreementResidual`` family) claims *the official
consolidation is wrong here, not our replay*.  The strongest possible validation
of that claim is **third-party**: the keeper of the consolidation (Finlex /
legislation.gov.uk / Riigi Teataja / etc.) acknowledging or correcting the
defect after we report it.  This module is a **first-class, additive rail** that
records those keeper confirmations and ties each to the exact residual ids it
validates.

Design source: ``notes_internal/pro_on_fable_notes.txt`` (§5 "Make external
confirmation a separate validation rail") and ``notes_internal/
FABLE_PUBLICATION_THESIS.md``.  Reference memory: *authoritative oracle != correct*
— an in-force official consolidation can be wrong, so ``oracle_suspect`` is a
first-class finding and a keeper acknowledgment is the adoption wedge.

This module is **read-only telemetry**.  It never authorizes replay, never turns
an oracle surface into source truth, and never changes any scoring or gating.  It
only *records* that a human keeper agreed a residual was oracle-side, and reports
coverage over the residual inventory.

Store convention mirrors ``spec_authority`` / ``spec_authority_grounding.json``:
a diffable JSON file under ``data/`` with a ``_meta`` block and a deterministic,
sorted list of records; the loader raises on malformed input rather than silently
dropping a record, so a confirmation can never vanish unnoticed.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping, cast

# A keeper's disposition of a reported defect.  ``pending`` = reported, no reply
# yet; ``acknowledged`` = keeper agrees it is a defect; ``corrected`` = keeper
# has fixed the consolidation; ``rejected`` = keeper disputes the defect (which
# is itself signal: it re-opens whether the divergence is really oracle-side).
KeeperResponse = Literal["pending", "acknowledged", "corrected", "rejected"]
VALID_KEEPER_RESPONSES: frozenset[str] = frozenset(
    {"pending", "acknowledged", "corrected", "rejected"}
)

# A keeper response that counts as positive third-party validation that the
# divergence is oracle-side.  ``pending`` and ``rejected`` do NOT.
CONFIRMING_RESPONSES: frozenset[str] = frozenset({"acknowledged", "corrected"})

# Schema tag carried in the store's ``_meta`` block.
SCHEMA: str = "lawvm.oracle_defect_external_confirmation.v1"


@dataclass(frozen=True, slots=True)
class OracleDefectExternalConfirmation:
    """One keeper confirmation of one or more oracle-defect residuals.

    ``source`` is the jurisdiction/keeper key (e.g. ``"finlex"``,
    ``"legislation.gov.uk"``, ``"riigi_teataja"``).  ``ticket`` is the keeper's
    own reference/id for the report (an email thread id, a helpdesk ticket, an
    erratum number).  ``affected_residual_ids`` are the exact
    ``AgreementResidual.residual_id`` values this confirmation validates, so
    coverage can be computed against the live residual inventory without any
    fuzzy statute matching.

    This is an adjudication/telemetry record.  It never authorizes replay and
    never turns an oracle surface into source truth.
    """

    confirmation_id: str
    source: str
    ticket: str
    submitted_date: str
    keeper_response: KeeperResponse
    affected_residual_ids: tuple[str, ...]
    correction_date: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "confirmation_id", _required("confirmation_id", self.confirmation_id)
        )
        object.__setattr__(self, "source", _required("source", self.source))
        object.__setattr__(self, "ticket", _required("ticket", self.ticket))
        object.__setattr__(
            self, "submitted_date", _required_date("submitted_date", self.submitted_date)
        )
        response = _required("keeper_response", self.keeper_response)
        if response not in VALID_KEEPER_RESPONSES:
            raise ValueError(
                "OracleDefectExternalConfirmation.keeper_response must be one of "
                f"{sorted(VALID_KEEPER_RESPONSES)}; got {response!r}"
            )
        object.__setattr__(self, "keeper_response", response)
        residual_ids = _residual_id_tuple(self.affected_residual_ids)
        if not residual_ids:
            raise ValueError(
                "OracleDefectExternalConfirmation.affected_residual_ids is required "
                "(a confirmation must reference at least one residual id)"
            )
        object.__setattr__(self, "affected_residual_ids", residual_ids)
        correction_date = str(self.correction_date or "").strip()
        if correction_date:
            correction_date = _required_date("correction_date", correction_date)
        object.__setattr__(self, "correction_date", correction_date)
        # A ``corrected`` disposition should carry the correction date; a
        # non-corrected disposition must not claim one (that would misrepresent
        # the keeper's action).
        if response == "corrected" and not correction_date:
            raise ValueError(
                "keeper_response 'corrected' requires a correction_date "
                f"(confirmation {self.confirmation_id!r})"
            )
        if response != "corrected" and correction_date:
            raise ValueError(
                "correction_date is only valid when keeper_response is 'corrected' "
                f"(confirmation {self.confirmation_id!r} is {response!r})"
            )
        object.__setattr__(self, "note", str(self.note or "").strip())

    @property
    def is_confirming(self) -> bool:
        """True when the keeper positively validated the defect (ack/corrected)."""
        return self.keeper_response in CONFIRMING_RESPONSES

    def to_dict(self) -> dict[str, Any]:
        return {
            "confirmation_id": self.confirmation_id,
            "source": self.source,
            "ticket": self.ticket,
            "submitted_date": self.submitted_date,
            "keeper_response": self.keeper_response,
            "affected_residual_ids": list(self.affected_residual_ids),
            "correction_date": self.correction_date,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, row: Mapping[str, Any]) -> "OracleDefectExternalConfirmation":
        if not isinstance(row, Mapping):
            raise ValueError("confirmation record must be a mapping")
        unknown = set(row) - _KNOWN_FIELDS
        if unknown:
            raise ValueError(
                f"unknown field(s) in confirmation record: {sorted(unknown)}"
            )
        return cls(
            confirmation_id=str(row.get("confirmation_id") or ""),
            source=str(row.get("source") or ""),
            ticket=str(row.get("ticket") or ""),
            submitted_date=str(row.get("submitted_date") or ""),
            keeper_response=cast(KeeperResponse, str(row.get("keeper_response") or "")),
            affected_residual_ids=tuple(
                str(item) for item in _sequence(row.get("affected_residual_ids"))
            ),
            correction_date=str(row.get("correction_date") or ""),
            note=str(row.get("note") or ""),
        )

    def sort_key(self) -> tuple[str, str, str]:
        return (self.source, self.submitted_date, self.confirmation_id)


_KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "confirmation_id",
        "source",
        "ticket",
        "submitted_date",
        "keeper_response",
        "affected_residual_ids",
        "correction_date",
        "note",
    }
)


# ---------------------------------------------------------------------------
# On-disk store (diffable JSON under data/, deterministic ordering)
# ---------------------------------------------------------------------------

_STORE_META: dict[str, Any] = {
    "description": (
        "External-confirmation rail for oracle-defect (oracle_suspect) residuals. "
        "Each record is a third-party (keeper) confirmation that a LawVM "
        "oracle_suspect divergence was oracle-side, tied to the exact residual "
        "ids it validates. Read-only telemetry: never authorizes replay, never "
        "turns an oracle surface into source truth, never changes scoring/gating."
    ),
    "schema": SCHEMA,
    "keeper_responses": {
        "pending": "reported to the keeper; no reply yet",
        "acknowledged": "keeper agrees the consolidation is defective here",
        "corrected": "keeper has corrected the consolidation (carries correction_date)",
        "rejected": "keeper disputes the defect (re-opens the divergence)",
    },
    "confirming_responses": sorted(CONFIRMING_RESPONSES),
    "source_note": (
        "Design source: notes_internal/pro_on_fable_notes.txt section 5 and "
        "FABLE_PUBLICATION_THESIS.md. A keeper acknowledgment is third-party "
        "validation of oracle_suspect (the adoption wedge)."
    ),
}


def _store_path() -> Path:
    # src/lawvm/tools/oracle_defect_confirmation.py -> repo_root/data/...
    return (
        Path(__file__).resolve().parents[3]
        / "data"
        / "oracle_defect_confirmations.json"
    )


def load_confirmations(
    path: Path | None = None,
) -> tuple[OracleDefectExternalConfirmation, ...]:
    """Load all confirmations, deterministically ordered.

    Pure: no caching, no mutation.  Raises on a malformed file or a duplicate
    ``confirmation_id`` rather than silently dropping a record.  Returns an empty
    tuple when the store file does not yet exist.
    """
    src = path or _store_path()
    if not src.exists():
        return ()
    raw = json.loads(src.read_text(encoding="utf-8"))
    rows = raw.get("confirmations")
    if not isinstance(rows, list):
        raise ValueError(
            "oracle-defect confirmation store must have a 'confirmations' list"
        )
    seen: set[str] = set()
    records: list[OracleDefectExternalConfirmation] = []
    for row in rows:
        record = OracleDefectExternalConfirmation.from_dict(row)
        if record.confirmation_id in seen:
            raise ValueError(
                f"duplicate confirmation_id in store: {record.confirmation_id!r}"
            )
        seen.add(record.confirmation_id)
        records.append(record)
    return _sorted(records)


def write_confirmations(
    confirmations: Iterable[OracleDefectExternalConfirmation],
    path: Path | None = None,
) -> Path:
    """Write confirmations to the store with deterministic ordering.

    Rejects duplicate ``confirmation_id`` values.  The residual-id list inside
    each record preserves the caller's order (it is a set of validated ids, and
    stability there aids diffs); the top-level record list is sorted by
    ``(source, submitted_date, confirmation_id)``.
    """
    dst = path or _store_path()
    records = _sorted(list(confirmations))
    seen: set[str] = set()
    for record in records:
        if record.confirmation_id in seen:
            raise ValueError(
                f"duplicate confirmation_id: {record.confirmation_id!r}"
            )
        seen.add(record.confirmation_id)
    payload = {
        "_meta": _STORE_META,
        "confirmations": [record.to_dict() for record in records],
    }
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return dst


def add_confirmation(
    confirmation: OracleDefectExternalConfirmation,
    path: Path | None = None,
) -> Path:
    """Append one confirmation to the store, preserving determinism.

    Raises if the ``confirmation_id`` already exists.
    """
    existing = load_confirmations(path)
    if any(rec.confirmation_id == confirmation.confirmation_id for rec in existing):
        raise ValueError(
            f"confirmation_id already exists: {confirmation.confirmation_id!r}"
        )
    return write_confirmations((*existing, confirmation), path)


def _sorted(
    records: list[OracleDefectExternalConfirmation],
) -> tuple[OracleDefectExternalConfirmation, ...]:
    return tuple(sorted(records, key=lambda rec: rec.sort_key()))


# ---------------------------------------------------------------------------
# Coverage telemetry over the residual inventory (read-only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConfirmationCoverage:
    """Coverage of oracle-defect residuals by external confirmations.

    ``confirmed_residual_ids`` are the distinct residual ids that carry at least
    one *confirming* (acknowledged/corrected) keeper response AND are present in
    the supplied residual inventory — the publication-relevant
    "externally-validated oracle-defect" set.  ``dangling_residual_ids`` are ids
    referenced by confirmations but absent from the inventory (stale references),
    surfaced so a confirmation can never silently point at nothing.
    """

    inventory_residual_count: int
    confirmed_residual_ids: tuple[str, ...]
    pending_residual_ids: tuple[str, ...]
    dangling_residual_ids: tuple[str, ...]
    confirmations_total: int
    confirmations_confirming: int

    @property
    def externally_validated_count(self) -> int:
        return len(self.confirmed_residual_ids)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inventory_residual_count": self.inventory_residual_count,
            "externally_validated_count": self.externally_validated_count,
            "confirmed_residual_ids": list(self.confirmed_residual_ids),
            "pending_residual_ids": list(self.pending_residual_ids),
            "dangling_residual_ids": list(self.dangling_residual_ids),
            "confirmations_total": self.confirmations_total,
            "confirmations_confirming": self.confirmations_confirming,
        }


def compute_coverage(
    inventory_residual_ids: Iterable[str],
    confirmations: Iterable[OracleDefectExternalConfirmation],
) -> ConfirmationCoverage:
    """Compute externally-validated oracle-defect coverage over an inventory.

    ``inventory_residual_ids`` is the current set of oracle_suspect /
    ``oracle_editorial_pathology`` residual ids (the caller selects which
    residuals count as oracle-defect claims; this function does not re-classify).
    A residual is *confirmed* when it is in the inventory and referenced by at
    least one confirming (acknowledged/corrected) confirmation.  A residual is
    *pending* when it is in the inventory and referenced only by pending/rejected
    confirmations.  Dangling ids are referenced by confirmations but not in the
    inventory.
    """
    inventory = {str(rid) for rid in inventory_residual_ids if str(rid)}
    confirmed: set[str] = set()
    referenced: set[str] = set()
    confirmations_total = 0
    confirmations_confirming = 0
    for record in confirmations:
        confirmations_total += 1
        if record.is_confirming:
            confirmations_confirming += 1
        for rid in record.affected_residual_ids:
            referenced.add(rid)
            if record.is_confirming and rid in inventory:
                confirmed.add(rid)
    pending = {rid for rid in (referenced & inventory) if rid not in confirmed}
    dangling = referenced - inventory
    return ConfirmationCoverage(
        inventory_residual_count=len(inventory),
        confirmed_residual_ids=tuple(sorted(confirmed)),
        pending_residual_ids=tuple(sorted(pending)),
        dangling_residual_ids=tuple(sorted(dangling)),
        confirmations_total=confirmations_total,
        confirmations_confirming=confirmations_confirming,
    )


def annotate_residuals(
    inventory_residual_ids: Iterable[str],
    confirmations: Iterable[OracleDefectExternalConfirmation],
) -> dict[str, dict[str, Any]]:
    """Annotate each inventory residual id with its external-confirmation state.

    Read-only telemetry.  Returns a mapping ``residual_id -> {"externally_confirmed",
    "keeper_responses", "sources", "tickets"}`` for every id in the inventory.
    Does NOT change any scoring or gating; a consumer may render this as an extra
    column next to the residual inventory.
    """
    confirmations = tuple(confirmations)
    inventory = [str(rid) for rid in inventory_residual_ids if str(rid)]
    by_residual: dict[str, list[OracleDefectExternalConfirmation]] = {}
    for record in confirmations:
        for rid in record.affected_residual_ids:
            by_residual.setdefault(rid, []).append(record)
    annotated: dict[str, dict[str, Any]] = {}
    for rid in inventory:
        matches = by_residual.get(rid, [])
        annotated[rid] = {
            "externally_confirmed": any(rec.is_confirming for rec in matches),
            "keeper_responses": sorted({rec.keeper_response for rec in matches}),
            "sources": sorted({rec.source for rec in matches}),
            "tickets": sorted({rec.ticket for rec in matches}),
        }
    return annotated


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _required(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(
            f"OracleDefectExternalConfirmation.{field_name} is required"
        )
    return text


def _required_date(field_name: str, value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(
            f"OracleDefectExternalConfirmation.{field_name} is required"
        )
    # ISO-8601 calendar date (YYYY-MM-DD); deliberately strict so dates sort and
    # diff cleanly and a typo cannot masquerade as a valid record.
    parts = text.split("-")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        raise ValueError(
            f"OracleDefectExternalConfirmation.{field_name} must be YYYY-MM-DD; "
            f"got {text!r}"
        )
    year, month, day = parts
    if len(year) != 4 or len(month) != 2 or len(day) != 2:
        raise ValueError(
            f"OracleDefectExternalConfirmation.{field_name} must be YYYY-MM-DD; "
            f"got {text!r}"
        )
    if not (1 <= int(month) <= 12) or not (1 <= int(day) <= 31):
        raise ValueError(
            f"OracleDefectExternalConfirmation.{field_name} is not a valid date: "
            f"{text!r}"
        )
    return text


def _residual_id_tuple(values: Any) -> tuple[str, ...]:
    if isinstance(values, str):
        raise ValueError(
            "affected_residual_ids must be a sequence of ids, not a bare string"
        )
    if not isinstance(values, (tuple, list)):
        raise ValueError("affected_residual_ids must be a tuple/list")
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _sequence(value: Any) -> tuple[Any, ...]:
    if isinstance(value, str):
        return (value,) if value else ()
    if isinstance(value, (list, tuple)):
        return tuple(value)
    return ()


__all__ = [
    "CONFIRMING_RESPONSES",
    "SCHEMA",
    "VALID_KEEPER_RESPONSES",
    "ConfirmationCoverage",
    "KeeperResponse",
    "OracleDefectExternalConfirmation",
    "add_confirmation",
    "annotate_residuals",
    "compute_coverage",
    "load_confirmations",
    "write_confirmations",
]
