"""Shared UK compiled-operation evidence facts."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

from lawvm.core.ir import LegalAddress, LegalOperation
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
    target_formatter: Callable[[LegalAddress], str] | None = None,
    payload_text_formatter: Callable[[str], str] | None = None,
) -> UKCompiledEffectFacts:
    """Return stable evidence inputs for UK source-pathology classification."""
    compiled_ops = tuple(ops)
    payloads = tuple(op.payload for op in compiled_ops if op.payload is not None)
    format_target = target_formatter or _default_target_path
    format_payload_text = payload_text_formatter or _default_payload_text
    return UKCompiledEffectFacts(
        op_actions=tuple(_action_name(op.action) for op in compiled_ops),
        payload_kinds=tuple(str(payload.kind) for payload in payloads),
        payload_texts=tuple(
            format_payload_text(payload.text or "") for payload in payloads
        ),
        target_paths=tuple(format_target(op.target) for op in compiled_ops),
        lowering_rule_ids=tuple(
            str(row.get("rule_id") or "")
            for row in lowering_rejections[lowering_rejection_start_index:]
        ),
    )


def _default_target_path(target: LegalAddress) -> str:
    return "/".join(f"{kind}:{label}" for kind, label in target.path)


def _default_payload_text(text: str) -> str:
    return text
