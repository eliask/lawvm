from __future__ import annotations

import re

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name, _addr_container, _addr_leaf_kind, _uk_kind_value
from lawvm.uk_legislation.provenance_notes import _schedule_list_entry_repeal_selector


def _looks_like_schedule_entry_repeal_text(text: str) -> bool:
    normalized = " ".join(str(text or "").split()).lower()
    if not re.search(r"\b(?:repeal\w*|omit\w*)\b", normalized):
        return False
    return bool(re.search(r"\b(?:entry|entries)\s+(?:for|relating\s+to|in\s+relation\s+to)\b", normalized))


def _is_unsafe_schedule_entry_repeal_op(op: LegalOperation) -> bool:
    if _action_name(op.action) != "repeal":
        return False
    if _schedule_list_entry_repeal_selector(op) is not None:
        return False
    if _addr_container(op.target) != "schedule":
        return False
    if _addr_leaf_kind(op.target) not in {"schedule", "part", "chapter", "division"}:
        return False
    payload = op.payload
    raw_text = op.source.raw_text if op.source is not None else ""
    payload_text = payload.text if payload is not None else ""
    if not _looks_like_schedule_entry_repeal_text(f"{raw_text} {payload_text}"):
        return False
    return payload is None or _uk_kind_value(payload.kind) == "schedule"
