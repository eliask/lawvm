"""Shared compile/evidence record classification helpers."""
from __future__ import annotations

from typing import Any


def is_blocking_compile_record(record: dict[str, Any]) -> bool:
    """Return whether a compile/evidence record blocks strict replay.

    Legacy records without an explicit disposition remain blocking for safety.
    Observation rows can opt out with either ``blocking=False`` or
    ``strict_disposition="record"``.
    """
    if "blocking" in record:
        return bool(record.get("blocking"))
    return str(record.get("strict_disposition") or "") != "record"
