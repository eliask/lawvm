"""Generic post-fold payload realization audit.

The audit checks a replay-facing totality heuristic: source-owned amendment
payload text should be visible in the immediate post-amendment folded state
unless a frontend classifies the unit as non-realizing context.  Core owns the
text realization comparison and finding shape; frontends own the source-unit
inventory and exception classification.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from math import ceil

from lawvm.core.phase_result import Finding, OBSERVATION_ROLE


_WS_RE = re.compile(r"\s+")
_NON_WORD_RE = re.compile(r"[^\w§]+", flags=re.UNICODE)
_MIN_CHUNK_CHARS = 24
_MIN_ORDERED_TOKENS = 4
_MIN_APPROX_TOKENS = 8
_MIN_APPROX_TOKEN_COVERAGE = 0.80


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

    if _all_chunks_exactly_realized(units, after_text):
        return ()
    normalized_after = _normalized_text(after_text)
    if not normalized_after:
        return ()
    after_tokens = tuple(normalized_after.split())

    gaps: list[PayloadRealizationGap] = []
    for unit in units:
        for index, chunk in enumerate(unit.text_chunks):
            if not _chunk_realized_in_text(chunk, normalized_after, after_tokens):
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


def drop_materialized_payload_realization_false_positives(
    findings: tuple[Finding, ...],
    *,
    materialized_text: str,
) -> tuple[Finding, ...]:
    """Drop payload-gap findings whose chunk is present in materialized text.

    Frontends may audit a replay fold before the final product surface projects
    timeline/materialization-owned descendants.  A reported realization gap is
    therefore a false positive when its owned chunk is visible in the materialized
    statute text used for comparison/export.
    """

    normalized_materialized = _normalized_text(materialized_text)
    if not normalized_materialized:
        return findings
    materialized_tokens = tuple(normalized_materialized.split())

    retained: list[Finding] = []
    for finding in findings:
        if finding.kind != "COVERAGE.PAYLOAD_REALIZATION_GAP":
            retained.append(finding)
            continue
        chunk = str(finding.detail.get("chunk_excerpt") or "")
        if chunk and _chunk_realized_in_text(
            chunk,
            normalized_materialized,
            materialized_tokens,
        ):
            continue
        retained.append(finding)
    return tuple(retained)


def _display_text(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


def _normalized_text(text: str) -> str:
    return _NON_WORD_RE.sub(" ", text.casefold()).strip()


def _chunk_realized_in_text(
    chunk: str,
    normalized_after: str,
    after_tokens: tuple[str, ...],
) -> bool:
    normalized_chunk = _normalized_text(chunk)
    if normalized_chunk in normalized_after:
        return True
    chunk_tokens = tuple(normalized_chunk.split())
    if len(chunk_tokens) < _MIN_ORDERED_TOKENS:
        return False
    return _ordered_tokens_in_bounded_window(chunk_tokens, after_tokens)


def _all_chunks_exactly_realized(
    units: tuple[PayloadRealizationUnit, ...],
    after_text: str,
) -> bool:
    saw_chunk = False
    for unit in units:
        for chunk in unit.text_chunks:
            saw_chunk = True
            if chunk not in after_text:
                return False
    return saw_chunk


def _ordered_tokens_in_bounded_window(
    chunk_tokens: tuple[str, ...],
    after_tokens: tuple[str, ...],
) -> bool:
    """Return whether ``chunk_tokens`` are realized within a local window.

    Finnish consolidated text can interleave editorial qualifiers, such as
    English degree translations, between source-owned Finnish tokens.  Exact
    substring matching would report those chunks missing even though the source
    text is present in order.  The bounded window keeps this as a locality check:
    a chunk cannot be satisfied by common words scattered across the statute.

    Later materialization or source normalization can also rewrite a small
    number of local tokens (renumbered internal references, agency names, OCR
    spelling).  For longer chunks, accept high-coverage local realization rather
    than requiring every token to survive byte-for-byte.
    """

    if not chunk_tokens or not after_tokens:
        return False
    first = chunk_tokens[0]
    max_window = max(len(chunk_tokens) * 4, len(chunk_tokens) + 80)
    for start, token in enumerate(after_tokens):
        if token != first:
            continue
        index = 1
        end_limit = min(len(after_tokens), start + max_window)
        for after_token in after_tokens[start + 1 : end_limit]:
            if after_token == chunk_tokens[index]:
                index += 1
                if index == len(chunk_tokens):
                    return True
        if _approx_tokens_realized_in_window(
            chunk_tokens,
            after_tokens[start:end_limit],
        ):
            return True
    return False


def _approx_tokens_realized_in_window(
    chunk_tokens: tuple[str, ...],
    window_tokens: tuple[str, ...],
) -> bool:
    """Return whether most chunk tokens occur in order in one local window."""

    if len(chunk_tokens) < _MIN_APPROX_TOKENS:
        return False
    required_matches = ceil(len(chunk_tokens) * _MIN_APPROX_TOKEN_COVERAGE)
    if not _has_minimum_token_overlap(
        chunk_tokens,
        window_tokens,
        required_matches=required_matches,
    ):
        return False
    matched = _ordered_lcs_len(chunk_tokens, window_tokens)
    return matched >= required_matches


def _has_minimum_token_overlap(
    chunk_tokens: tuple[str, ...],
    window_tokens: tuple[str, ...],
    *,
    required_matches: int,
) -> bool:
    """Return whether token multisets can possibly reach ``required_matches``."""

    if required_matches <= 0:
        return True
    window_counts = Counter(window_tokens)
    matched = 0
    for token in chunk_tokens:
        remaining = window_counts.get(token, 0)
        if not remaining:
            continue
        window_counts[token] = remaining - 1
        matched += 1
        if matched >= required_matches:
            return True
    return False


def _ordered_lcs_len(
    left: tuple[str, ...],
    right: tuple[str, ...],
) -> int:
    """Length of the longest ordered token overlap between two bounded windows."""

    width = len(right) + 1
    previous = [0] * width
    current = [0] * width
    for left_token in left:
        current[0] = 0
        for col, right_token in enumerate(right, start=1):
            if left_token == right_token:
                current[col] = previous[col - 1] + 1
            else:
                current[col] = previous[col] if previous[col] >= current[col - 1] else current[col - 1]
        previous, current = current, previous
    return previous[-1]


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
    "drop_materialized_payload_realization_false_positives",
    "payload_realization_gap_findings",
]
