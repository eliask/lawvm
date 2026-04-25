"""Deterministic IDs for admitted Finland fallback ops and tool-side wrappers."""

from __future__ import annotations

import hashlib
from dataclasses import replace as dc_replace
from typing import List

from lawvm.finland.ops import AmendmentOp


def mint_fallback_op_id(scope_id: str, op: AmendmentOp, *, prefix: str = "fi") -> str:
    """Mint a deterministic fallback-op id from a scope id and op shape."""
    signature = "|".join(
        [
            scope_id,
            op.op_type,
            op.target_unit_kind or "",
            op.target_section or "",
            str(op.target_paragraph) if op.target_paragraph is not None else "",
            op.target_chapter or "",
            op.target_special or "",
        ]
    )
    digest = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}:{scope_id}:{digest}"


def stamp_fallback_op_ids(ops: List[AmendmentOp], scope_id: str, *, prefix: str = "fi") -> List[AmendmentOp]:
    """Ensure fallback ops carry deterministic ids instead of blank placeholders."""
    stamped: List[AmendmentOp] = []
    for op in ops:
        if op.op_id:
            stamped.append(op)
        else:
            stamped.append(dc_replace(op, op_id=mint_fallback_op_id(scope_id, op, prefix=prefix)))
    return stamped
