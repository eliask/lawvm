"""Estonia replayability-frontier classification.

A read-only DIAGNOSTIC sensor that, per EE corpus ``(base_id, oracle_id)`` pair,
classifies WHY the pair is or is not end-to-end replayable. It turns a bare
"replay error" count into a typed, actionable frontier — the EE analog of the UK
``acquisition_frontier`` / ``si_commencement_audit`` classifiers.

This module does not re-derive replayability. It reuses the EXISTING replay
signal: :func:`lawvm.estonia.replay.replay_ee_to_pit` returns an
:class:`~lawvm.estonia.replay.EEPitResult` whose populated fields already encode
the outcome of every replay phase (base load/parse, oracle resolution/parse,
amendment fetch/parse, op application). The classifier reads that result and
assigns exactly one typed state. It never fetches from the network beyond what
replay itself already did, and it never mutates replay, source state, residual
reporting, or any archive.

The taxonomy is deliberately limited to states the ``EEPitResult`` can actually
distinguish. Anything that does not map to a real, evidenced reason is assigned
the loud :data:`EE_REPLAYABILITY_UNCLASSIFIED` state — a pair is never silently
bucketed as replayable.
"""
from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from lawvm.core.diagnostic_records import diagnostic_detail

# ── State taxonomy ───────────────────────────────────────────────────────────
# Module-level constants + a frozenset, mirroring the UK
# ``si_commencement_audit`` / ``acquisition_frontier`` pattern. Each state is a
# stable reason-tag string surfaced in diagnostics and reports.

# Replay ran to completion: no error, a replayed tree AND an oracle tree were
# produced, and no amendment source in the window failed to fetch/parse. This is
# the only non-frontier state.
EE_REPLAYABILITY_REPLAYABLE = "replayable"
# The base terviktekst XML could not be loaded (fetch failure or missing local
# file) — there is no base to replay onto.
EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE = "base_source_unavailable"
# The base terviktekst XML loaded but failed to parse into an IR statute.
EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR = "base_source_parse_error"
# No oracle terviktekst could be resolved/parsed for the pair, so there is
# nothing to verify the replay against.
EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE = "oracle_source_unavailable"
# At least one amendment act in the replay window could not be fetched or parsed
# (``EEPitResult.amendments_failed`` is non-empty): the replay is missing a
# source lane, so its result is incomplete.
EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE = "amendment_source_unavailable"
# Replay completed without error but applied zero operations in the window: the
# base already equals the oracle for this date (no amendment delta to replay).
EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW = "no_amendments_in_window"
# Replay raised an error during op application (or any other replay-internal
# failure) that is not one of the more specific source-lane states above.
EE_REPLAYABILITY_REPLAY_ERROR_OTHER = "replay_error_other"
# Loud catch-all: the result did not match any known reason. A pair in this
# state is NOT replayable and demands inspection — it must never be silently
# treated as ``replayable``.
EE_REPLAYABILITY_UNCLASSIFIED = "unclassified"

EE_REPLAYABILITY_STATES: frozenset[str] = frozenset(
    {
        EE_REPLAYABILITY_REPLAYABLE,
        EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE,
        EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR,
        EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE,
        EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE,
        EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW,
        EE_REPLAYABILITY_REPLAY_ERROR_OTHER,
        EE_REPLAYABILITY_UNCLASSIFIED,
    }
)

# The single non-frontier state. Every other state is a stop-here replayability
# frontier.
_NON_FRONTIER_STATES: frozenset[str] = frozenset({EE_REPLAYABILITY_REPLAYABLE})

# Replay records its structural failures via a leading ``error`` banner. These
# prefixes are the verbatim ones produced by ``replay_ee_to_pit``; they are
# matched (not re-derived) so this sensor cannot disagree with replay about
# which phase failed.
_BASE_LOAD_ERROR_PREFIX = "Failed to load base"
_BASE_PARSE_ERROR_PREFIX = "Failed to parse base"
_APPLY_ERROR_PREFIX = "Failed to apply ops"


_REASON_TEXT: dict[str, str] = {
    EE_REPLAYABILITY_REPLAYABLE: (
        "Estonia replay ran to completion with a replayed tree and an oracle "
        "tree and no failed amendment source; this pair is not a replayability "
        "frontier."
    ),
    EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE: (
        "Estonia base terviktekst XML could not be loaded (fetch failure or "
        "missing source); there is no base to replay onto."
    ),
    EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR: (
        "Estonia base terviktekst XML loaded but failed to parse into an IR "
        "statute; the base cannot be replayed."
    ),
    EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE: (
        "No Estonia oracle terviktekst could be resolved or parsed for this "
        "pair; the replay cannot be verified against an oracle."
    ),
    EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE: (
        "At least one Estonia amendment act in the replay window could not be "
        "fetched or parsed; the replay is missing a source lane."
    ),
    EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW: (
        "Estonia replay completed without error but applied zero operations in "
        "the window; the base already equals the oracle for this date."
    ),
    EE_REPLAYABILITY_REPLAY_ERROR_OTHER: (
        "Estonia replay raised an error that is not a specific base/oracle/"
        "amendment source-lane failure."
    ),
    EE_REPLAYABILITY_UNCLASSIFIED: (
        "Estonia replay result did not match any known replayability reason; "
        "this pair requires inspection and must not be treated as replayable."
    ),
}


@dataclass(frozen=True)
class EEReplayabilityState:
    """Typed replayability-frontier classification for one EE corpus pair.

    The carrier is the EE analog of ``UKAcquisitionFrontierState`` /
    ``UKSICommencementAuditState``. ``state`` is the single dominant reason;
    ``reasons`` is the full ordered, de-duplicated set of contributing reason
    tags. The remaining fields record the replay signal that justified the
    classification so a diagnosis does not have to re-run replay to see why.
    """

    base_id: str
    oracle_id: str
    state: str
    reasons: tuple[str, ...]
    grupi_id: str = ""
    as_of: str = ""
    n_ops: int = 0
    n_amendments_total: int = 0
    n_amendments_applied: int = 0
    n_amendments_failed: int = 0
    amendments_failed: tuple[str, ...] = ()
    n_divergences: int = 0
    replay_error: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def is_replayability_frontier(self) -> bool:
        """True when this state is a STOP-HERE replayability frontier."""
        return self.state not in _NON_FRONTIER_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_id": self.base_id,
            "oracle_id": self.oracle_id,
            "grupi_id": self.grupi_id,
            "state": self.state,
            "is_replayability_frontier": self.is_replayability_frontier,
            "reasons": list(self.reasons),
            "as_of": self.as_of,
            "n_ops": self.n_ops,
            "n_amendments_total": self.n_amendments_total,
            "n_amendments_applied": self.n_amendments_applied,
            "n_amendments_failed": self.n_amendments_failed,
            "amendments_failed": list(self.amendments_failed),
            "n_divergences": self.n_divergences,
            "replay_error": self.replay_error,
            "detail": dict(sorted(self.detail.items())),
        }

    def to_diagnostic_detail(self) -> dict[str, Any]:
        """Project the classification as a nonblocking diagnostic record.

        The record is an observation, not a rejection: it carries
        ``strict_disposition='record'`` and does not block replay. It exists so
        strict mode and audit surfaces can SEE the replayability frontier as a
        typed class.
        """
        return diagnostic_detail(
            rule_id="ee_replayability_frontier_classified",
            family="source_pathology",
            phase="replay",
            reason=_REASON_TEXT[self.state],
            blocking=False,
            detail={
                "base_id": self.base_id,
                "oracle_id": self.oracle_id,
                "grupi_id": self.grupi_id,
                "replayability_state": self.state,
                "is_replayability_frontier": self.is_replayability_frontier,
                "replayability_reasons": list(self.reasons),
                "as_of": self.as_of,
                "n_ops": self.n_ops,
                "n_amendments_total": self.n_amendments_total,
                "n_amendments_applied": self.n_amendments_applied,
                "n_amendments_failed": self.n_amendments_failed,
                "amendments_failed": list(self.amendments_failed),
                "n_divergences": self.n_divergences,
                "replay_error": self.replay_error,
            },
        )


def _classify_replay_error(error: str) -> Optional[str]:
    """Map a populated ``EEPitResult.error`` banner to a structural state.

    Returns ``None`` when ``error`` is empty (no structural failure recorded).
    Unknown non-empty errors fall through to ``replay_error_other`` so they are
    never silently treated as classified-away.
    """
    text = (error or "").strip()
    if not text:
        return None
    if text.startswith(_BASE_LOAD_ERROR_PREFIX):
        return EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE
    if text.startswith(_BASE_PARSE_ERROR_PREFIX):
        return EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR
    if text.startswith(_APPLY_ERROR_PREFIX):
        return EE_REPLAYABILITY_REPLAY_ERROR_OTHER
    return EE_REPLAYABILITY_REPLAY_ERROR_OTHER


def classify_ee_replayability(result: Any) -> EEReplayabilityState:
    """Classify one EE replay result into exactly one replayability state.

    ``result`` is an :class:`~lawvm.estonia.replay.EEPitResult` (any object
    exposing the same fields). This function is TOTAL and read-only: every input
    maps to exactly one state in :data:`EE_REPLAYABILITY_STATES`. It only reads
    the replay signal; it does not re-run or mutate anything.

    Precedence (most-specific structural failure first):

    1. a populated ``error`` banner → base-load / base-parse / replay-error;
    2. a non-empty ``amendments_failed`` → amendment source-lane failure;
    3. a missing oracle tree → oracle source unavailable;
    4. a missing replayed tree with no recorded error → unclassified (loud);
    5. zero applied ops with a clean result → no-amendments-in-window;
    6. otherwise → replayable.
    """
    base_id = str(getattr(result, "base_id", "") or "")
    oracle_id = str(getattr(result, "oracle_id", "") or "")
    grupi_id = str(getattr(result, "grupi_id", "") or "")
    as_of = str(getattr(result, "as_of", "") or "")
    error = str(getattr(result, "error", "") or "")
    n_ops = int(getattr(result, "n_ops", 0) or 0)
    amendments_total = tuple(getattr(result, "amendments_total", ()) or ())
    amendments_applied = tuple(getattr(result, "amendments_applied", ()) or ())
    amendments_failed = tuple(getattr(result, "amendments_failed", ()) or ())
    replayed = getattr(result, "replayed", None)
    oracle = getattr(result, "oracle", None)
    divergences = tuple(getattr(result, "divergences", ()) or ())

    reasons: list[str] = []
    state: str

    error_state = _classify_replay_error(error)
    if error_state is not None:
        # A structural error banner was recorded by replay: this is the
        # dominant reason regardless of what partial state survived.
        state = error_state
        reasons.append(error_state)
    elif amendments_failed:
        # No fatal error, but replay could not fetch/parse one or more amendment
        # acts in the window (AGENTS §1.8: the dropped lane stays visible).
        state = EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE
        reasons.append(EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE)
    elif oracle is None:
        # Replay produced a tree but no oracle to verify against (oracle feed
        # empty, oracle id unresolved, or oracle XML parse warned-and-skipped).
        state = EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE
        reasons.append(EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE)
    elif replayed is None:
        # No error banner, an oracle present, yet no replayed tree: the replay
        # signal is internally inconsistent. Stay loud rather than guess.
        state = EE_REPLAYABILITY_UNCLASSIFIED
        reasons.append(EE_REPLAYABILITY_UNCLASSIFIED)
    elif n_ops == 0:
        # Clean replay, base already equals the oracle for this date.
        state = EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW
        reasons.append(EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW)
    else:
        state = EE_REPLAYABILITY_REPLAYABLE
        reasons.append(EE_REPLAYABILITY_REPLAYABLE)

    if state not in EE_REPLAYABILITY_STATES:
        # Defensive totality guard: an unexpected mapping is loud, never silent.
        reasons = [EE_REPLAYABILITY_UNCLASSIFIED]
        state = EE_REPLAYABILITY_UNCLASSIFIED

    return EEReplayabilityState(
        base_id=base_id,
        oracle_id=oracle_id,
        state=state,
        reasons=tuple(dict.fromkeys(reasons)),
        grupi_id=grupi_id,
        as_of=as_of,
        n_ops=n_ops,
        n_amendments_total=len(amendments_total),
        n_amendments_applied=len(amendments_applied),
        n_amendments_failed=len(amendments_failed),
        amendments_failed=tuple(str(a) for a in amendments_failed),
        n_divergences=len(divergences),
        replay_error=error,
        detail={},
    )


@dataclass(frozen=True)
class EECorpusPair:
    """One ``(base_id, oracle_id)`` pair read from a replayable-corpus CSV."""

    base_id: str
    oracle_id: str
    grupi_id: str = ""
    oracle_effective: str = ""
    title: str = ""


def read_ee_corpus_pairs(csv_path: Path | str) -> tuple[EECorpusPair, ...]:
    """Read ``(base_id, oracle_id)`` pairs from a replayable-corpus CSV.

    The corpus CSVs (``current_replayable_corpus.csv`` /
    ``replayable_corpus.csv``) carry ``grupi_id, base_id, oracle_id`` columns.
    Rows missing a base or oracle id are skipped. The returned tuple is sorted
    deterministically by ``(base_id, oracle_id)``.
    """
    path = Path(csv_path)
    pairs: list[EECorpusPair] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            base_id = str(row.get("base_id", "") or "").strip()
            oracle_id = str(row.get("oracle_id", "") or "").strip()
            if not base_id or not oracle_id:
                continue
            pairs.append(
                EECorpusPair(
                    base_id=base_id,
                    oracle_id=oracle_id,
                    grupi_id=str(row.get("grupi_id", "") or "").strip(),
                    oracle_effective=str(row.get("oracle_effective", "") or "").strip(),
                    title=str(row.get("title", "") or "").strip(),
                )
            )
    pairs.sort(key=lambda pair: (pair.base_id, pair.oracle_id))
    return tuple(pairs)


def ee_replayability_frontier_for_corpus(
    pairs: Iterable[EECorpusPair],
    *,
    replay_pair: Any,
    limit: int | None = None,
) -> tuple[EEReplayabilityState, ...]:
    """Classify every corpus pair into a replayability state.

    ``replay_pair`` is a callable ``(base_id, oracle_id, oracle_effective) ->
    EEPitResult`` injected by the caller. The classifier itself does no I/O —
    the entrypoint wires in the real replay-backed callable, while tests pass a
    fixture callable so the default-run test needs no archive. The returned
    tuple is sorted by ``(base_id, oracle_id)`` so two runs over the same inputs
    match.
    """
    ordered = sorted(pairs, key=lambda pair: (pair.base_id, pair.oracle_id))
    if limit is not None:
        ordered = ordered[:limit]
    states: list[EEReplayabilityState] = []
    for pair in ordered:
        result = replay_pair(pair.base_id, pair.oracle_id, pair.oracle_effective)
        state = classify_ee_replayability(result)
        if not state.grupi_id and pair.grupi_id:
            state = EEReplayabilityState(
                base_id=state.base_id,
                oracle_id=state.oracle_id,
                state=state.state,
                reasons=state.reasons,
                grupi_id=pair.grupi_id,
                as_of=state.as_of,
                n_ops=state.n_ops,
                n_amendments_total=state.n_amendments_total,
                n_amendments_applied=state.n_amendments_applied,
                n_amendments_failed=state.n_amendments_failed,
                amendments_failed=state.amendments_failed,
                n_divergences=state.n_divergences,
                replay_error=state.replay_error,
                detail=state.detail,
            )
        states.append(state)
    states.sort(key=lambda s: (s.base_id, s.oracle_id))
    return tuple(states)


def ee_replayability_states_to_report(
    states: Sequence[EEReplayabilityState],
) -> dict[str, Any]:
    """Build a deterministic report of replayability-frontier states.

    Rows are sorted by ``(base_id, oracle_id)``; the summary counts are sorted by
    state value; the frontier-pair list is sorted. No timestamps or set-ordered
    content appear in the body, so two runs over the same inputs diff empty.
    """
    sorted_states = sorted(states, key=lambda s: (s.base_id, s.oracle_id))
    counts: dict[str, int] = {}
    frontier_pairs: list[str] = []
    for s in sorted_states:
        counts[s.state] = counts.get(s.state, 0) + 1
        if s.is_replayability_frontier:
            frontier_pairs.append(f"{s.base_id}:{s.oracle_id}")
    # Ensure every taxonomy state appears in the counts for stable shape.
    for state_value in EE_REPLAYABILITY_STATES:
        counts.setdefault(state_value, 0)
    return {
        "pairs": [s.to_dict() for s in sorted_states],
        "pair_count": len(sorted_states),
        "replayability_frontier_pair_count": len(frontier_pairs),
        "replayability_frontier_pairs": sorted(frontier_pairs),
        "state_counts": dict(sorted(counts.items())),
    }


__all__ = [
    "EE_REPLAYABILITY_REPLAYABLE",
    "EE_REPLAYABILITY_BASE_SOURCE_UNAVAILABLE",
    "EE_REPLAYABILITY_BASE_SOURCE_PARSE_ERROR",
    "EE_REPLAYABILITY_ORACLE_SOURCE_UNAVAILABLE",
    "EE_REPLAYABILITY_AMENDMENT_SOURCE_UNAVAILABLE",
    "EE_REPLAYABILITY_NO_AMENDMENTS_IN_WINDOW",
    "EE_REPLAYABILITY_REPLAY_ERROR_OTHER",
    "EE_REPLAYABILITY_UNCLASSIFIED",
    "EE_REPLAYABILITY_STATES",
    "EEReplayabilityState",
    "EECorpusPair",
    "classify_ee_replayability",
    "read_ee_corpus_pairs",
    "ee_replayability_frontier_for_corpus",
    "ee_replayability_states_to_report",
]
