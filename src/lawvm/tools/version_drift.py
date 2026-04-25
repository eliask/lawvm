"""Content-based version drift detection via replay checkpoints.

Detects when a jurisdiction's oracle is behind by comparing intermediate
replay states against the oracle text.  Uses the generic
``ReplayCheckpointCallback`` protocol so the replay loop does not need to
be re-run — scores are collected during the single normal replay pass.

Algorithm:
  For each amendment step i (0..N-1), the checkpoint callback serializes
  the replay state and scores it against the oracle.  If step K produces
  a near-perfect match (≥ 0.9999) while the final step N-1 does not,
  the oracle is behind by (N-1 - K) amendments.

  The callback is lazy: ``serialize_text`` is only called when actually
  needed (always, in the drift-detection use case, but the protocol
  doesn't mandate it).

This replaces the old re-replay approach that was limited to ≤20
amendment chains and ≤3 backward steps.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import Levenshtein

from lawvm.core.replay_contracts import ReplayCheckpoint


def _clean(text: str) -> str:
    """Strip non-alphanumeric for scoring (mirrors oracle_check._clean)."""
    return re.sub(r"[^a-z0-9äöå]", "", text.lower())


@dataclass
class VersionDriftCollector:
    """Replay checkpoint consumer that detects oracle version drift.

    Usage::

        oracle_text = "..."  # full oracle text
        collector = VersionDriftCollector(oracle_text)
        replay_xml(..., checkpoint_callback=collector)
        drift = collector.result()
    """

    oracle_text: str
    _clean_oracle: str = field(init=False, repr=False)
    _scores: Dict[int, float] = field(default_factory=dict)
    _amendment_ids: list[str] = field(default_factory=list)
    _total_steps: int = 0
    _best_match_idx: Optional[int] = None
    _best_match_score: float = 0.0

    def __post_init__(self) -> None:
        self._clean_oracle = _clean(self.oracle_text)

    def __call__(self, checkpoint: ReplayCheckpoint) -> None:
        """ReplayCheckpointCallback protocol."""
        self._total_steps = checkpoint.total_steps
        idx = checkpoint.step_index
        # Track amendment IDs in order.
        while len(self._amendment_ids) <= idx:
            self._amendment_ids.append("")
        self._amendment_ids[idx] = checkpoint.amendment_id

        if not self._clean_oracle:
            return

        replay_text = _clean(checkpoint.serialize_text())
        if not replay_text:
            self._scores[idx] = 0.0
            return

        score = Levenshtein.ratio(replay_text, self._clean_oracle)
        self._scores[idx] = score

        if score > self._best_match_score:
            self._best_match_score = score
            self._best_match_idx = idx

    def result(self, final_score: Optional[float] = None) -> Optional[Dict[str, Any]]:
        """Return drift proof if detected, else None.

        Parameters
        ----------
        final_score:
            The overall score from the full replay.  If not provided,
            uses the score from the last checkpoint step.
        """
        if self._total_steps < 2:
            return None

        last_idx = self._total_steps - 1
        if final_score is None:
            final_score = self._scores.get(last_idx, 0.0)

        # No drift if final replay is already perfect.
        if final_score >= 0.9999:
            return None

        # No drift if no intermediate step beat the final score significantly.
        if self._best_match_idx is None:
            return None
        if self._best_match_idx >= last_idx:
            return None
        if self._best_match_score < 0.9999:
            return None

        behind_by = last_idx - self._best_match_idx
        matched_at = self._amendment_ids[self._best_match_idx] if self._best_match_idx < len(self._amendment_ids) else ""
        unapplied = self._amendment_ids[self._best_match_idx + 1:] if self._best_match_idx + 1 < len(self._amendment_ids) else []

        return {
            "matched_at": matched_at,
            "behind_by": behind_by,
            "unapplied": unapplied,
            "scores": {
                f"step_{k}": v for k, v in sorted(self._scores.items())
            },
            "detection_method": "checkpoint",
        }


def detect_content_version_drift(
    statute_id: str,
    full_score: float,
    *,
    corpus: Any = None,
) -> Optional[Dict[str, Any]]:
    """Detect oracle version drift via checkpoint-based replay.

    Drop-in replacement for the old re-replay approach.  Runs a single
    replay pass with a ``VersionDriftCollector`` callback.
    """
    if full_score >= 0.9999:
        return None

    from lawvm.finland.grafter import (
        _get_corpus_store,
        _resolve_applicable_amendment_records,
        get_ground_truth_tree,
        replay_xml,
    )
    from lxml import etree

    if corpus is None:
        corpus = _get_corpus_store()

    amendment_records, _cutoff, _oracle_version_amendment_id = _resolve_applicable_amendment_records(
        statute_id, "legal_pit", corpus=corpus
    )
    if len(amendment_records) < 2:
        return None

    oracle_root = get_ground_truth_tree(statute_id)
    if oracle_root is None:
        return None

    oracle_text = etree.tostring(oracle_root, method="text", encoding="unicode").strip()
    collector = VersionDriftCollector(oracle_text)

    try:
        replay_xml(
            statute_id,
            mode="legal_pit",
            corpus=corpus,
            quiet=True,
            checkpoint_callback=collector,
        )
    except Exception:
        return None

    return collector.result(final_score=full_score)
