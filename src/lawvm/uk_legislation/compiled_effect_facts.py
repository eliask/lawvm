"""Shared UK compiled-operation evidence facts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from lawvm.core.ir import LegalOperation
from lawvm.uk_legislation.addressing import _action_name


@dataclass(frozen=True)
class UKCompiledEffectFacts:
    op_actions: tuple[str, ...]
    payload_kinds: tuple[str, ...]
    payload_texts: tuple[str, ...]
    target_paths: tuple[str, ...]
    lowering_rule_ids: tuple[str, ...]


def uk_compiled_effect_facts(
    *,
    ops: Iterable[LegalOperation],
    lowering_rejections: Sequence[Mapping[str, Any]] = (),
    lowering_rejection_start_index: int = 0,
) -> UKCompiledEffectFacts:
    """Return stable evidence inputs for UK source-pathology classification."""
    compiled_ops = tuple(ops)
    payloads = tuple(op.payload for op in compiled_ops if op.payload is not None)
    return UKCompiledEffectFacts(
        op_actions=tuple(_action_name(op.action) for op in compiled_ops),
        payload_kinds=tuple(str(payload.kind) for payload in payloads),
        payload_texts=tuple(payload.text or "" for payload in payloads),
        target_paths=tuple(
            "/".join(f"{kind}:{label}" for kind, label in op.target.path)
            for op in compiled_ops
        ),
        lowering_rule_ids=tuple(
            str(row.get("rule_id") or "")
            for row in lowering_rejections[lowering_rejection_start_index:]
        ),
    )
