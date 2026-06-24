"""Shared compile/evidence record classification helpers.

The central authority predicate here decides whether a compile/evidence row
*blocks* strict replay. That decision is an authority-boundary act, so its input
is a typed carrier (`CompileRecord`) rather than an untyped ``dict[str, Any]``:
the two fields the decision actually consumes — an explicit ``blocking`` flag and
the ``strict_disposition`` — are made canonical and self-evidencing.

Legacy callers that still hold raw diagnostic rows convert at the boundary via
``CompileRecord.from_mapping``; the predicate's ``Mapping`` overload is a thin
back-compat adapter for those rows (recorded as residue, not the load-bearing
path).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any


class BlockingDisposition(str, Enum):
    """How a compile/evidence record relates to strict-replay authority.

    ``BLOCK`` rows block strict replay; ``RECORD`` rows are observation-only and
    opt out. The disposition is the typed authority verdict the predicate emits.
    """

    BLOCK = "block"
    RECORD = "record"


# The legacy ``strict_disposition`` opt-out string. A record opts out of blocking
# either with ``blocking=False`` or with this disposition string.
_RECORD_DISPOSITION = "record"


@dataclass(frozen=True, slots=True)
class CompileRecord:
    """Typed carrier for the authority-relevant facts of a compile/evidence row.

    Only the fields the blocking decision depends on are carried canonically:

    * ``blocking`` — explicit blocking flag. ``None`` means *unspecified*, which
      preserves the legacy "key absent" semantics (fall back to the disposition).
    * ``strict_disposition`` — the strict-replay disposition string; ``"record"``
      marks an observation row that opts out of blocking.

    The full diagnostic payload the row may also carry is retained in ``extra`` so
    no information is dropped when a raw row is adapted at the boundary, while the
    authority-relevant facts stay typed.
    """

    blocking: bool | None = None
    strict_disposition: str | None = None
    extra: Mapping[str, Any] | None = None

    @classmethod
    def from_mapping(cls, record: Mapping[str, Any]) -> CompileRecord:
        """Adapt a raw compile/evidence row into the typed carrier."""

        blocking_present = "blocking" in record
        blocking_value = bool(record.get("blocking")) if blocking_present else None
        disposition_raw = record.get("strict_disposition")
        disposition = str(disposition_raw) if disposition_raw is not None else None
        extra = {
            key: value
            for key, value in record.items()
            if key not in {"blocking", "strict_disposition"}
        }
        return cls(
            blocking=blocking_value,
            strict_disposition=disposition,
            extra=extra or None,
        )

    @property
    def is_blocking(self) -> bool:
        """Whether this record blocks strict replay.

        An explicit ``blocking`` flag wins. Otherwise legacy records without a
        flag remain blocking unless their disposition opts out with ``"record"``.
        """

        if self.blocking is not None:
            return self.blocking
        return str(self.strict_disposition or "") != _RECORD_DISPOSITION

    @property
    def disposition(self) -> BlockingDisposition:
        """The typed authority verdict for this record."""

        return BlockingDisposition.BLOCK if self.is_blocking else BlockingDisposition.RECORD


def is_blocking_compile_record(record: CompileRecord | Mapping[str, Any]) -> bool:
    """Return whether a compile/evidence record blocks strict replay.

    The typed ``CompileRecord`` carrier is the canonical input. A ``Mapping`` is
    accepted as a thin back-compat adapter for legacy raw rows and converted at
    the boundary; new code should pass a ``CompileRecord``.

    Legacy records without an explicit disposition remain blocking for safety.
    Observation rows can opt out with either ``blocking=False`` or
    ``strict_disposition="record"``.
    """
    if isinstance(record, CompileRecord):
        return record.is_blocking
    return CompileRecord.from_mapping(record).is_blocking
