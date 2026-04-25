"""Typed Finland source/oracle adjudication helpers.

This module turns the previous loose ``replay_meta`` source fields into an
explicit object that downstream tools can inspect without guessing key names.
"""
from __future__ import annotations

from typing import Any, Iterable

from lawvm.replay_adjudication import SourceAdjudication


def build_source_adjudication(
    statute_id: str,
    replay_mode: str,
    *,
    cutoff_date: str = "",
    oracle_version_amendment_id: str = "",
    oracle_suspect: str = "",
    html_noncommensurable_reason: str = "",
    lineage: Iterable[dict[str, Any]] = (),
) -> SourceAdjudication:
    """Build a typed Finland source adjudication object."""
    return SourceAdjudication(
        statute_id=statute_id,
        replay_mode=replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=tuple(lineage),
    )

__all__ = ["build_source_adjudication"]
