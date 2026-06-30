"""EE consolidation-error candidate surface (the adoption wedge).

Read-only diagnostic. It mines the EE replay/consistency corpus for divergences
where LawVM is plausibly RIGHT and the official Riigi Teataja consolidation
(terviktekst) is plausibly WRONG, and presents them as a ranked findings report.

CORE PREMISE (AGENTS.md §2.1/§3, `[[reference-authoritative-oracle-not-correct]]`):
the official consolidated text is law-in-force, but legal force is NOT
consolidation-correctness. A terviktekst can mis-render the amendment acts and
stay in force until corrected, so LawVM replaying the primary amendment acts can
be RIGHT while the in-force consolidation is WRONG. Surfacing those cases is a
high-value finding, not a failure.

This module does NOT re-adjudicate. It CONSUMES:

* `replay_ee_to_pit(...)` (`.divergences`: address, divergence_type, ops_text,
  consolidated_text) — the replay-vs-consolidation divergence stream; and
* `build_ee_residual_summary(...)` (`estonia/residual_reporting.py`) — the
  already-built post-hoc residual adjudication mapping divergence addresses to
  residual buckets, including the two consolidation-side error buckets
  `source_oracle_drift` and `oracle_correction_notice`.

TIERING RULE (honest, never overclaiming):

* STRONG tier ("consolidation-wrong-in-force" candidates): divergences whose
  adjudicated residual bucket is a consolidation-side error
  (`source_oracle_drift` / `oracle_correction_notice`). These are backed by an
  adjudicated residual record and are ranked first.
* TRIAGE tier (`unadjudicated_needs_review`): divergences with NO residual
  record. The lawvm_wrong default is just 'suspect us first', not a verdict, so
  these are surfaced for triage only — never asserted as consolidation errors.

Determinism: candidates are emitted in a stable total order so the producer can
be run twice with an empty diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lawvm.estonia.residual_reporting import (
    EEResidualSummary,
    build_ee_residual_summary,
)

# ---------------------------------------------------------------------------
# Consolidation-side error buckets (the strong tier)
# ---------------------------------------------------------------------------

# These two adjudicated residual buckets mean "LawVM is right and the official
# consolidation is stale/editorial/wrong" (see EEResidualBucket in
# residual_inventory.py). Only an adjudicated residual record in one of these
# buckets may back a STRONG ("consolidation-wrong-in-force") candidate.
CONSOLIDATION_SIDE_ERROR_BUCKETS: frozenset[str] = frozenset(
    {"source_oracle_drift", "oracle_correction_notice"}
)

# Bucket label assigned to the lower-confidence triage tier. This is NOT an
# adjudicated bucket; it explicitly flags an unadjudicated divergence surfaced
# for human review, never asserted as a consolidation error.
UNADJUDICATED_TRIAGE_BUCKET = "unadjudicated_needs_review"

_EVIDENCE_SNIPPET_CHARS = 200


@dataclass(frozen=True, slots=True)
class ConsolidationErrorEvidence:
    """A short replay-vs-consolidation text-evidence snippet for one candidate."""

    replay_text: Optional[str]
    consolidated_text: Optional[str]
    replay_snippet: str
    consolidated_snippet: str


@dataclass(frozen=True, slots=True)
class ConsolidationErrorCandidate:
    """One ranked consolidation-error candidate for an EE (base, oracle) pair.

    `tier` is "strong" only when `residual_bucket` is an adjudicated
    consolidation-side error bucket; otherwise it is "triage" and
    `residual_bucket` is `UNADJUDICATED_TRIAGE_BUCKET`.
    """

    base_id: str
    oracle_id: str
    address: str
    divergence_type: str
    tier: str  # "strong" | "triage"
    residual_bucket: str
    residual_evidence: Optional[str]
    witness_rule_id: Optional[str]
    amending_act: Optional[str]
    amending_act_title: Optional[str]
    evidence: ConsolidationErrorEvidence


@dataclass(frozen=True, slots=True)
class ConsolidationErrorCandidateReport:
    """Ranked consolidation-error candidates for one EE (base, oracle) pair."""

    base_id: str
    oracle_id: str
    statute_title: str
    comparison_class: str
    has_residual_adjudication: bool
    strong_candidates: tuple[ConsolidationErrorCandidate, ...] = ()
    triage_candidates: tuple[ConsolidationErrorCandidate, ...] = ()

    @property
    def strong_count(self) -> int:
        return len(self.strong_candidates)

    @property
    def triage_count(self) -> int:
        return len(self.triage_candidates)

    def ranked_candidates(self) -> tuple[ConsolidationErrorCandidate, ...]:
        """All candidates, strong tier first (the ranked findings order)."""
        return self.strong_candidates + self.triage_candidates


# ---------------------------------------------------------------------------
# Address + attribution helpers (read-only, no behavior change)
# ---------------------------------------------------------------------------


def _address_to_str(address: Any) -> str:
    """Canonical EE address string, matching the residual-inventory convention.

    Mirrors the `"/".join(f"{kind}:{label}" ...)` form used across ee_explain /
    ee_replay so divergence addresses key into the residual summary records.
    """
    path = getattr(address, "path", ())
    return "/".join(f"{kind}:{label}" for kind, label in path)


def _snippet(text: Optional[str]) -> str:
    """One-line, length-bounded evidence snippet."""
    if not text:
        return ""
    flat = " ".join(text.split())
    if len(flat) <= _EVIDENCE_SNIPPET_CHARS:
        return flat
    return flat[: _EVIDENCE_SNIPPET_CHARS - 1] + "…"


@dataclass(frozen=True, slots=True)
class _OpAttribution:
    """Witness-rule / amending-act attribution derived from compiled ops."""

    witness_rule_id: Optional[str]
    amending_act: Optional[str]
    amending_act_title: Optional[str]


def _build_attribution_index(result: Any) -> dict[str, _OpAttribution]:
    """Index address -> attribution from the replay result's compiled ops.

    Reuses `result.compiled_ops` / `result.applied_snapshot_ops` (each a
    `LegalOperation` carrying `target`, `witness_rule_id`, and `source`). When
    multiple ops touch the same address the last-sequenced op wins (it is the
    op that produced the surviving text-state at that address), so attribution
    is deterministic. This reads ops only; it never mutates them.
    """
    ops: list[Any] = []
    ops.extend(getattr(result, "compiled_ops", ()) or ())
    ops.extend(getattr(result, "applied_snapshot_ops", ()) or ())

    # Stable: sort by sequence then op_id so equal-sequence ops resolve
    # deterministically; later entries overwrite earlier ones.
    def _sort_key(op: Any) -> tuple[int, str]:
        return (getattr(op, "sequence", 0) or 0, str(getattr(op, "op_id", "")))

    index: dict[str, _OpAttribution] = {}
    for op in sorted(ops, key=_sort_key):
        target = getattr(op, "target", None)
        if target is None:
            continue
        address = _address_to_str(target)
        if not address:
            continue
        source = getattr(op, "source", None)
        index[address] = _OpAttribution(
            witness_rule_id=getattr(op, "witness_rule_id", None),
            amending_act=getattr(source, "statute_id", None) if source is not None else None,
            amending_act_title=getattr(source, "title", None) if source is not None else None,
        )
    return index


def _attribution_for(
    address: str, index: dict[str, _OpAttribution]
) -> _OpAttribution:
    """Resolve attribution for an address, falling back to nearest ancestor.

    A divergence may sit at a descendant of the op target (e.g. an item under a
    replaced subsection). Walk up the `/`-segmented address to the nearest
    attributed ancestor rather than guessing.
    """
    exact = index.get(address)
    if exact is not None:
        return exact
    segments = address.split("/")
    for cut in range(len(segments) - 1, 0, -1):
        ancestor = "/".join(segments[:cut])
        record = index.get(ancestor)
        if record is not None:
            return record
    return _OpAttribution(witness_rule_id=None, amending_act=None, amending_act_title=None)


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------


def _candidate_sort_key(candidate: ConsolidationErrorCandidate) -> tuple[Any, ...]:
    """Deterministic per-candidate ordering.

    Strong tier ranks first (tier_rank), then by bucket name, then by address,
    then by divergence type — a stable total order with no Python-iteration
    dependence.
    """
    tier_rank = 0 if candidate.tier == "strong" else 1
    bucket_rank = candidate.residual_bucket
    return (tier_rank, bucket_rank, candidate.address, candidate.divergence_type)


def _make_candidate(
    *,
    base_id: str,
    oracle_id: str,
    address: str,
    divergence: Any,
    tier: str,
    residual_bucket: str,
    residual_evidence: Optional[str],
    attribution: _OpAttribution,
) -> ConsolidationErrorCandidate:
    ops_text = getattr(divergence, "ops_text", None)
    con_text = getattr(divergence, "consolidated_text", None)
    evidence = ConsolidationErrorEvidence(
        replay_text=ops_text,
        consolidated_text=con_text,
        replay_snippet=_snippet(ops_text),
        consolidated_snippet=_snippet(con_text),
    )
    return ConsolidationErrorCandidate(
        base_id=base_id,
        oracle_id=oracle_id,
        address=address,
        divergence_type=getattr(divergence, "divergence_type", ""),
        tier=tier,
        residual_bucket=residual_bucket,
        residual_evidence=residual_evidence,
        witness_rule_id=attribution.witness_rule_id,
        amending_act=attribution.amending_act,
        amending_act_title=attribution.amending_act_title,
        evidence=evidence,
    )


def consolidation_error_candidates(
    base_id: str,
    as_of: str,
    *,
    oracle_id: Optional[str] = None,
    result: Any = None,
    residual_summary: Optional[EEResidualSummary] = None,
    archive: Any = None,
) -> ConsolidationErrorCandidateReport:
    """Build the ranked consolidation-error candidate report for one EE pair.

    Reuses `replay_ee_to_pit` for the divergence stream and
    `build_ee_residual_summary` for the adjudication; it never re-adjudicates and
    never mutates replay state.

    Args:
        base_id, as_of, oracle_id: passed through to `replay_ee_to_pit` when
            `result` is not supplied.
        result: a pre-computed `EEPitResult` (the divergence + ops source). When
            None, this calls `replay_ee_to_pit`. Tests inject a fake result to
            avoid touching the archive.
        residual_summary: a pre-computed `EEResidualSummary`. When None, this
            calls `build_ee_residual_summary` against the result's pair. Tests
            inject a fake summary.
        archive: optional Farchive passed through to `replay_ee_to_pit`.

    Returns:
        A `ConsolidationErrorCandidateReport` with strong candidates (adjudicated
        consolidation-side error) ranked first and triage candidates
        (unadjudicated, flagged `unadjudicated_needs_review`) second.
    """
    if result is None:
        from lawvm.estonia.replay import replay_ee_to_pit  # lazy: archive-dependent

        result = replay_ee_to_pit(
            base_id=base_id,
            as_of=as_of,
            oracle_id=oracle_id,
            archive=archive,
        )

    result_base = getattr(result, "base_id", base_id) or base_id
    result_oracle = getattr(result, "oracle_id", oracle_id) or oracle_id or ""

    divergences = list(getattr(result, "divergences", ()) or ())
    addressed = [(div, _address_to_str(getattr(div, "address", None))) for div in divergences]
    divergence_addresses = [address for _, address in addressed]

    if residual_summary is None:
        residual_summary = build_ee_residual_summary(
            base_id=result_base,
            oracle_id=result_oracle,
            divergence_addresses=divergence_addresses,
        )

    record_by_address: dict[str, Any] = {}
    statute_title = getattr(result, "base_title", "") or ""
    comparison_class = getattr(result, "comparison_class", "") or ""
    if residual_summary is not None:
        record_by_address = dict(residual_summary.record_by_address)
        statute_title = residual_summary.statute_title or statute_title
        comparison_class = residual_summary.comparison_class or comparison_class

    attribution_index = _build_attribution_index(result)

    strong: list[ConsolidationErrorCandidate] = []
    triage: list[ConsolidationErrorCandidate] = []

    for divergence, address in addressed:
        attribution = _attribution_for(address, attribution_index)
        record = record_by_address.get(address)
        bucket = getattr(record, "bucket", None) if record is not None else None

        if bucket in CONSOLIDATION_SIDE_ERROR_BUCKETS:
            strong.append(
                _make_candidate(
                    base_id=result_base,
                    oracle_id=result_oracle,
                    address=address,
                    divergence=divergence,
                    tier="strong",
                    residual_bucket=bucket,
                    residual_evidence=getattr(record, "evidence", None),
                    attribution=attribution,
                )
            )
        elif record is None:
            # No adjudicated residual record at all: the lower-confidence triage
            # tier. NOT asserted as a consolidation error — surfaced for review.
            triage.append(
                _make_candidate(
                    base_id=result_base,
                    oracle_id=result_oracle,
                    address=address,
                    divergence=divergence,
                    tier="triage",
                    residual_bucket=UNADJUDICATED_TRIAGE_BUCKET,
                    residual_evidence=None,
                    attribution=attribution,
                )
            )
        # Adjudicated-but-not-consolidation-side buckets (replay_bug,
        # source_pathology, presentation_punctuation_whitespace, etc.) are
        # deliberately excluded: they are not consolidation-wrong-in-force
        # candidates and must not be surfaced as such.

    strong.sort(key=_candidate_sort_key)
    triage.sort(key=_candidate_sort_key)

    return ConsolidationErrorCandidateReport(
        base_id=result_base,
        oracle_id=result_oracle,
        statute_title=statute_title,
        comparison_class=comparison_class,
        has_residual_adjudication=residual_summary is not None,
        strong_candidates=tuple(strong),
        triage_candidates=tuple(triage),
    )


# ---------------------------------------------------------------------------
# Bench-run aggregation (the ranked candidate surface over a whole run)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConsolidationCandidatePairInput:
    """One EE (base, oracle) pair to mine for consolidation-error candidates.

    `as_of` is the consolidated-version effective date the replay targets; when
    empty the caller's replay path resolves it from the oracle XML.
    """

    base_id: str
    oracle_id: str
    title: str = ""
    as_of: str = ""


@dataclass(frozen=True, slots=True)
class ConsolidationCandidatePairError:
    """A pair whose replay raised, recorded rather than silently swallowed."""

    base_id: str
    oracle_id: str
    title: str
    error: str


@dataclass(frozen=True, slots=True)
class ConsolidationCandidateRunReport:
    """Ranked consolidation-error candidates aggregated over a whole bench run.

    Per-pair reports are retained (each already strong-first); the flat
    `ranked_candidates` view is the run-wide findings order: all strong-tier
    candidates first (across pairs), then all triage-tier candidates, each
    block ordered by the per-candidate sort key plus its (base, oracle) pair so
    the surface is a stable total order across the whole run.
    """

    run_label: str
    pair_count: int
    scored_pair_count: int
    strong_total: int
    triage_total: int
    pair_reports: tuple[ConsolidationErrorCandidateReport, ...] = ()
    errors: tuple[ConsolidationCandidatePairError, ...] = ()

    def ranked_candidates(self) -> tuple[ConsolidationErrorCandidate, ...]:
        """All candidates run-wide, strong tier first then triage, stable order."""

        def _key(item: tuple[ConsolidationErrorCandidate, str, str]) -> tuple[Any, ...]:
            candidate, base_id, oracle_id = item
            return _candidate_sort_key(candidate) + (base_id, oracle_id)

        tagged: list[tuple[ConsolidationErrorCandidate, str, str]] = []
        for report in self.pair_reports:
            for candidate in report.ranked_candidates():
                tagged.append((candidate, report.base_id, report.oracle_id))
        tagged.sort(key=_key)
        return tuple(candidate for candidate, _, _ in tagged)

    def strong_candidates(self) -> tuple[ConsolidationErrorCandidate, ...]:
        return tuple(c for c in self.ranked_candidates() if c.tier == "strong")

    def triage_candidates(self) -> tuple[ConsolidationErrorCandidate, ...]:
        return tuple(c for c in self.ranked_candidates() if c.tier == "triage")


def build_consolidation_candidate_run_report(
    pairs: tuple[ConsolidationCandidatePairInput, ...],
    *,
    run_label: str = "",
    archive: Any = None,
    replay: Any = None,
) -> ConsolidationCandidateRunReport:
    """Rank consolidation-error candidates across a whole set of EE pairs.

    Reuses the per-pair `consolidation_error_candidates` entry point for every
    pair (so the strong/triage tiering is never reinvented), then exposes a
    stable run-wide ranking. Pairs whose replay raises are recorded as typed
    `ConsolidationCandidatePairError` rows rather than silently dropped
    (AGENTS.md §1.10): one bad pair must not hide the rest of the surface.

    Args:
        pairs: the (base, oracle, title, as_of) pairs to mine.
        run_label: human label for the originating bench run, carried for report.
        archive: optional Farchive threaded into the per-pair replay.
        replay: optional injected per-pair callable with the signature of
            `consolidation_error_candidates` (tests pass a fake; production
            leaves it None to use the real entry point).
    """
    entry = replay if replay is not None else consolidation_error_candidates

    pair_reports: list[ConsolidationErrorCandidateReport] = []
    errors: list[ConsolidationCandidatePairError] = []

    for pair in pairs:
        try:
            report = entry(
                base_id=pair.base_id,
                as_of=pair.as_of,
                oracle_id=pair.oracle_id,
                archive=archive,
            )
        except Exception as exc:  # noqa: BLE001 — recorded as a typed error row, not swallowed
            errors.append(
                ConsolidationCandidatePairError(
                    base_id=pair.base_id,
                    oracle_id=pair.oracle_id,
                    title=pair.title,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        pair_reports.append(report)

    pair_reports.sort(key=lambda r: (r.base_id, r.oracle_id))
    errors.sort(key=lambda e: (e.base_id, e.oracle_id))

    strong_total = sum(r.strong_count for r in pair_reports)
    triage_total = sum(r.triage_count for r in pair_reports)

    return ConsolidationCandidateRunReport(
        run_label=run_label,
        pair_count=len(pairs),
        scored_pair_count=len(pair_reports),
        strong_total=strong_total,
        triage_total=triage_total,
        pair_reports=tuple(pair_reports),
        errors=tuple(errors),
    )


# ---------------------------------------------------------------------------
# JSON projection (deterministic)
# ---------------------------------------------------------------------------


def candidate_to_jsonable(candidate: ConsolidationErrorCandidate) -> dict[str, Any]:
    """Deterministic JSON projection of one candidate."""
    return {
        "base_id": candidate.base_id,
        "oracle_id": candidate.oracle_id,
        "address": candidate.address,
        "divergence_type": candidate.divergence_type,
        "tier": candidate.tier,
        "residual_bucket": candidate.residual_bucket,
        "residual_evidence": candidate.residual_evidence,
        "witness_rule_id": candidate.witness_rule_id,
        "amending_act": candidate.amending_act,
        "amending_act_title": candidate.amending_act_title,
        "evidence": {
            "replay_snippet": candidate.evidence.replay_snippet,
            "consolidated_snippet": candidate.evidence.consolidated_snippet,
        },
    }


def report_to_jsonable(report: ConsolidationErrorCandidateReport) -> dict[str, Any]:
    """Deterministic JSON projection of one pair report."""
    return {
        "base_id": report.base_id,
        "oracle_id": report.oracle_id,
        "statute_title": report.statute_title,
        "comparison_class": report.comparison_class,
        "has_residual_adjudication": report.has_residual_adjudication,
        "strong_count": report.strong_count,
        "triage_count": report.triage_count,
        "strong_candidates": [candidate_to_jsonable(c) for c in report.strong_candidates],
        "triage_candidates": [candidate_to_jsonable(c) for c in report.triage_candidates],
    }


def run_report_to_jsonable(report: ConsolidationCandidateRunReport) -> dict[str, Any]:
    """Deterministic JSON projection of a whole-run ranked candidate report."""
    return {
        "run_label": report.run_label,
        "pair_count": report.pair_count,
        "scored_pair_count": report.scored_pair_count,
        "strong_total": report.strong_total,
        "triage_total": report.triage_total,
        "ranked_candidates": [candidate_to_jsonable(c) for c in report.ranked_candidates()],
        "pair_reports": [report_to_jsonable(r) for r in report.pair_reports],
        "errors": [
            {
                "base_id": err.base_id,
                "oracle_id": err.oracle_id,
                "title": err.title,
                "error": err.error,
            }
            for err in report.errors
        ],
    }


__all__ = [
    "CONSOLIDATION_SIDE_ERROR_BUCKETS",
    "UNADJUDICATED_TRIAGE_BUCKET",
    "ConsolidationErrorEvidence",
    "ConsolidationErrorCandidate",
    "ConsolidationErrorCandidateReport",
    "ConsolidationCandidatePairInput",
    "ConsolidationCandidatePairError",
    "ConsolidationCandidateRunReport",
    "consolidation_error_candidates",
    "build_consolidation_candidate_run_report",
    "candidate_to_jsonable",
    "report_to_jsonable",
    "run_report_to_jsonable",
]
