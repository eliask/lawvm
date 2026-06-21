"""Generic post-fold payload realization audit.

The audit checks a replay-facing totality heuristic: source-owned amendment
payload text should be visible in the immediate post-amendment folded state
unless a frontend classifies the unit as non-realizing context.  Core owns the
text realization comparison and finding shape; frontends own the source-unit
inventory and exception classification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from lawvm.core.phase_result import Finding, OBSERVATION_ROLE


_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^\w§]+", flags=re.UNICODE)
_MIN_CHUNK_CHARS = 24


@dataclass(frozen=True, slots=True)
class PayloadRealizationUnit:
    """One source-owned amendment payload unit to audit after replay apply."""

    unit_id: str
    unit_kind: str
    observed_label: str
    parent_label: str = ""
    text_chunks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.unit_id:
            raise ValueError("PayloadRealizationUnit.unit_id must be non-empty")
        if not self.unit_kind:
            raise ValueError("PayloadRealizationUnit.unit_kind must be non-empty")
        chunks = tuple(_display_text(chunk) for chunk in self.text_chunks)
        chunks = tuple(chunk for chunk in chunks if _is_substantive(chunk))
        object.__setattr__(self, "text_chunks", tuple(dict.fromkeys(chunks)))


@dataclass(frozen=True, slots=True)
class PayloadRealizationGap:
    """One source payload chunk missing from the post-amendment folded state."""

    unit_id: str
    unit_kind: str
    observed_label: str
    parent_label: str
    chunk_index: int
    chunk_text: str


def audit_payload_realization(
    *,
    units: tuple[PayloadRealizationUnit, ...],
    after_text: str,
) -> tuple[PayloadRealizationGap, ...]:
    """Return source payload chunks absent from ``after_text``.

    The comparison is intentionally realization-only.  A gap says the source
    payload text did not survive into the folded state; it does not infer a
    target address, authorize a repair, or change action family.
    """

    normalized_after = _normalized_text(after_text)
    if not normalized_after:
        return ()

    gaps: list[PayloadRealizationGap] = []
    for unit in units:
        for index, chunk in enumerate(unit.text_chunks):
            if _normalized_text(chunk) not in normalized_after:
                gaps.append(
                    PayloadRealizationGap(
                        unit_id=unit.unit_id,
                        unit_kind=unit.unit_kind,
                        observed_label=unit.observed_label,
                        parent_label=unit.parent_label,
                        chunk_index=index,
                        chunk_text=chunk,
                    )
                )
    return tuple(gaps)


def payload_realization_gap_findings(
    gaps: tuple[PayloadRealizationGap, ...],
    *,
    source_ref: str,
) -> tuple[Finding, ...]:
    """Project realization gaps onto the shared finding rail."""

    return tuple(_finding(gap, source_ref=source_ref) for gap in gaps)


def _display_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _normalized_text(text: str) -> str:
    return _NON_WORD_RE.sub(" ", _display_text(text).casefold()).strip()


def _is_substantive(text: str) -> bool:
    normalized = _normalized_text(text)
    if len(normalized) < _MIN_CHUNK_CHARS:
        return False
    return any(ch.isalpha() for ch in normalized)


def _finding(gap: PayloadRealizationGap, *, source_ref: str) -> Finding:
    return Finding(
        kind="COVERAGE.PAYLOAD_REALIZATION_GAP",
        role=OBSERVATION_ROLE,
        stage="post_apply_payload_realization",
        source_statute=source_ref,
        blocking=False,
        detail={
            "unit_id": gap.unit_id,
            "unit_kind": gap.unit_kind,
            "observed_label": gap.observed_label,
            "parent_label": gap.parent_label,
            "chunk_index": gap.chunk_index,
            "chunk_excerpt": gap.chunk_text[:240],
            "disposition": "source_payload_text_not_realized_in_post_fold_state",
        },
    )


__all__ = [
    "PayloadRealizationGap",
    "PayloadRealizationUnit",
    "audit_payload_realization",
    "payload_realization_gap_findings",
]
