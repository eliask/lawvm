"""lawvm bench -j uk — UK legislation replay/oracle benchmark.

Scores each statute by comparing enacted, replayed, and optionally commenced
EID sets against the consolidated-current oracle XML. The benchmark is an
evidence surface: source status, replay regime, rejection counts, adjudications,
and bounded score witnesses are part of the result.

Scoring formula: |enacted_eids ∩ oracle_eids| / max(|enacted|, |oracle|)
(Jaccard-style, also used for replay and commencement score lanes).

Usage (from LawVM/):
    lawvm bench -j uk --label v1
    lawvm bench -j uk --label v2 --types ukpga asp
    lawvm bench -j uk --corpus data/uk/bench_corpus_smoke.csv --replay --no-save
    lawvm bench -j uk --statute ukpga/2000/1 --no-save
    lawvm bench -j uk --limit 1 --parallel 1 --no-save
    lawvm bench -j uk --show v1
    lawvm bench -j uk --compare v1 v2
    lawvm bench -j uk --history
    lawvm bench -j uk --corpus-csv   # build/refresh data/uk/bench_corpus.csv
"""

from __future__ import annotations

import csv
import json
import hashlib
import os
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Set, cast

if TYPE_CHECKING:
    from lawvm.core.ir import IRStatute

import Levenshtein

from lawvm.core.compile_records import is_blocking_compile_record
from lawvm.core.ir import IRNode, LegalAddress
from lawvm.core.ir_helpers import is_zombie
from lawvm.replay_adjudication import CompileAdjudication
from farchive import Farchive
from lawvm.uk_legislation.uk_grafter import (
    extract_eid_map_bytes,
    parse_uk_statute_ir_bytes,
)
from lawvm.uk_legislation.source_adjudication import (
    classify_uk_bench_comparison,
    classify_uk_commencement_current_projection,
    classify_uk_current_projection_eid_shape,
    classify_uk_replay_adjudication_bucket,
    classify_uk_replay_residual,
    is_core_uk_comparison,
    normalize_uk_replay_compare_eids,
)
from lawvm.uk_legislation.source_state import (
    is_uk_affecting_act_xml_source_observation,
    uk_source_parse_observations_from_ir,
    uk_source_xml_parse_rejection,
    uk_source_state_wire_tuple as _source_state,
)
from lawvm.uk_legislation.target_anchors import _fallback_target_eid
from lawvm.tools.uk_replay_regime import UKReplayRegime, normalize_uk_replay_regime

_REPO_ROOT = Path(__file__).resolve().parents[3]  # LawVM/
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"
_BENCH_DIR = _REPO_ROOT / "data" / "uk_bench_runs"
_HISTORY_CSV = _REPO_ROOT / "data" / "uk_benchmark_history.csv"
_CORPUS_CSV = _REPO_ROOT / "data" / "uk" / "bench_corpus.csv"
_CORPUS_FIELDNAMES = [
    "statute_id",
    "type",
    "year",
    "has_enacted",
    "has_consolidated",
    "n_effects",
    "n_effect_feed_pages",
    "enacted_url",
    "current_url",
    "enacted_source_status",
    "oracle_source_status",
    "enacted_source_size",
    "oracle_source_size",
    "enacted_source_sha256",
    "oracle_source_sha256",
]

# Module-level state for worker processes (set before spawning ProcessPoolExecutor).
_WORKER_DB_PATH: str = ""
_WORKER_DO_REPLAY: bool = False
_WORKER_REPO_ROOT: str = ""
_WORKER_DO_COMMENCEMENT: bool = False
_WORKER_ALLOW_METADATA_BACKFILL: bool = True
_WORKER_ALLOW_ORACLE_ALIGNMENT: bool = True
_WORKER_APPLICABILITY_MODE: str = "effective_date_plus_feed_applied"
_WORKER_AUTHORITY_MODE: str = "current_mixed"
_WORKER_ALLOW_METADATA_ONLY_EFFECTS: bool = True

_LEG_BASE = "https://www.legislation.gov.uk"

# Primary act types to include by default
_DEFAULT_TYPES = frozenset(["ukpga", "asp", "asc", "nia"])
_SCORE_WITNESS_LIMIT = 10
_SCORE_WITNESS_SCHEMA = "uk_bench_score_witness.v1"
_SCORE_WITNESS_FORMULA = "common/max(left,right)"
_HISTORY_HEADERS = (
    "label",
    "n_total",
    "n_ok",
    "n_core_ok",
    "row_status_counts",
    "enacted_source_status_counts",
    "oracle_source_status_counts",
    "score_mode",
    "avg_score",
    "n_perfect",
    "avg_raw_score",
    "avg_replay_score",
    "avg_commencement_score",
    "n_commencement_scored",
    "n_replay_scored",
    "n_replay_errors",
    "n_commencement_errors",
    "source_parse_observations",
    "source_parse_observation_rules",
    "source_parse_rejections",
    "source_parse_rejection_rules",
    "effect_source_pathology_counts",
    "manual_compile_status_counts",
    "manual_compile_rule_counts",
    "source_acquisition_observations",
    "source_acquisition_observation_rules",
    "source_acquisition_rejections",
    "source_acquisition_rejection_rules",
    "bench_exceptions",
    "bench_exception_rules",
    "effect_feed_observations",
    "effect_feed_observation_rules",
    "effect_feed_rejections",
    "authority_observations",
    "authority_observation_rules",
    "authority_rejections",
    "authority_rejection_rules",
    "lowering_observations",
    "lowering_observation_rules",
    "lowering_rejections",
    "lowering_rejection_rules",
    "blocking_lowering_rejections",
    "blocking_lowering_rejection_rules",
    "replay_adjudications",
    "replay_adjudication_kinds",
    "replay_adjudication_buckets",
    "uk_residual_claim_tiers",
    "uk_residual_claim_kinds",
    "uk_residual_section_claims",
    "score_witness_rows",
    "replay_regimes",
    "timestamp",
)


# ---------------------------------------------------------------------------
# EID helpers
# ---------------------------------------------------------------------------


def _collect_eids(nodes: Sequence[IRNode], pit_date: Optional[str] = None) -> Set[str]:
    """Recursively collect all non-zombie eId/id attrs from an IR tree."""
    eids: Set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            eids.add(eid)
        eids.update(_collect_eids(n.children, pit_date=pit_date))
    return eids


def _score_eids(enacted_eids: Set[str], oracle_eids: Set[str]) -> float:
    """Jaccard-style EID similarity score."""
    common = enacted_eids & oracle_eids
    denom = max(len(enacted_eids), len(oracle_eids), 1)
    return len(common) / denom


def _score_commenced_eids(commenced_eids: Set[str], oracle_eids: Set[str]) -> float:
    """Score commenced EIDs only when the commencement lane has evidence."""
    if not commenced_eids:
        return -1.0
    return _score_eids(commenced_eids, oracle_eids)


def _commenced_oracle_eids(oracle_eids: Set[str], commenced_eids: Set[str]) -> Set[str]:
    """Return oracle EIDs within the same commencement lens as the left side.

    UK current XML may still expose not-yet-commenced provisions.  The
    commencement benchmark is therefore a temporal comparison lane, not a raw
    current-XML EID comparison with only one side filtered.
    """
    if not commenced_eids:
        return set()
    return oracle_eids & commenced_eids


def _build_eid_score_witness_rows(
    *,
    comparison_scope: str,
    left_side: str,
    left_eids: Set[str],
    right_eids: Set[str],
    sample_limit: int = _SCORE_WITNESS_LIMIT,
) -> tuple[_BenchScoreWitnessRow, ...]:
    common = left_eids & right_eids
    left_only = sorted(left_eids - right_eids)
    right_only = sorted(right_eids - left_eids)
    score_value = _score_eids(left_eids, right_eids)
    rows: list[_BenchScoreWitnessRow] = []
    for side, eids in ((left_side, left_only), ("only_in_oracle", right_only)):
        category_total = len(eids)
        for rank, eid in enumerate(eids[:sample_limit], start=1):
            rows.append(
                _BenchScoreWitnessRow(
                    comparison_scope=comparison_scope,
                    side=side,
                    eid=eid,
                    rank=rank,
                    category_total=category_total,
                    sample_limit=sample_limit,
                    truncated=category_total > sample_limit,
                    left_count=len(left_eids),
                    right_count=len(right_eids),
                    common_count=len(common),
                    score_value=score_value,
                )
            )
    return tuple(rows)


def _source_sha256(blob: bytes | None) -> str:
    if blob is None:
        return ""
    return hashlib.sha256(blob).hexdigest()


def _rule_counts(rows: Sequence[dict[str, Any]]) -> dict[str, int]:
    return dict(Counter(str(row.get("rule_id") or "unknown") for row in rows))


def _blocking_source_parse_rows(rows: Sequence[dict[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(row for row in rows if is_blocking_compile_record(row))


def _uk_bench_exception_observation(statute_id: str, exc: Exception) -> dict[str, Any]:
    return {
        "rule_id": "uk_bench_unclassified_exception",
        "family": "benchmark_execution",
        "phase": "benchmark",
        "statute_id": statute_id,
        "exception_type": type(exc).__name__,
        "exception_message": str(exc),
        "reason": "UK benchmark row failed outside a narrower source/replay diagnostic lane.",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
    }


def _bench_exception_result(
    entry: dict,
    exc: Exception,
    *,
    allow_metadata_backfill: bool = True,
    allow_oracle_alignment: bool = True,
    applicability_mode: str = "effective_date_plus_feed_applied",
    authority_mode: str = "current_mixed",
    allow_metadata_only_effects: bool = True,
) -> _BenchResult:
    statute_id = str(entry.get("statute_id") or "")
    observations = (_uk_bench_exception_observation(statute_id, exc),)
    return _BenchResult(
        statute_id=statute_id,
        act_type=str(entry.get("type") or ""),
        year=int(entry.get("year") or 0),
        n_effects=int(entry.get("n_effects") or 0),
        n_effect_feed_pages=int(entry.get("n_effect_feed_pages") or entry.get("n_effects") or 0),
        n_enacted_eids=0,
        n_oracle_eids=0,
        n_common=0,
        score=0.0,
        status="ERR",
        bench_exception_count=len(observations),
        bench_exception_rule_counts=_rule_counts(observations),
        bench_exception_observations=observations,
        enacted_source_status=str(entry.get("enacted_source_status") or "unknown"),
        oracle_source_status=str(entry.get("oracle_source_status") or "unknown"),
        enacted_source_size=int(entry.get("enacted_source_size") or 0),
        oracle_source_size=int(entry.get("oracle_source_size") or 0),
        enacted_source_sha256=str(entry.get("enacted_source_sha256") or ""),
        oracle_source_sha256=str(entry.get("oracle_source_sha256") or ""),
        enacted_source_url=str(entry.get("enacted_url") or ""),
        oracle_source_url=str(entry.get("current_url") or ""),
        uk_metadata_backfill_enabled=allow_metadata_backfill,
        uk_oracle_alignment_enabled=allow_oracle_alignment,
        uk_applicability_mode=applicability_mode,
        uk_authority_mode=authority_mode,
        uk_metadata_only_effects_enabled=allow_metadata_only_effects,
        **_uk_bench_replay_regime_result_fields(
            enacted_only=False,
            oracle_alignment_enabled=allow_oracle_alignment,
            metadata_backfill_op_count=0,
            allow_metadata_backfill=allow_metadata_backfill,
            allow_metadata_only_effects=allow_metadata_only_effects,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
        ),
        error=f"{type(exc).__name__}: {exc}"[:200],
        comparison_class="exception",
        core_benchmark=False,
    )


def _uk_metadata_backfill_op_count(ops: Sequence[object]) -> int:
    count = 0
    for op in ops:
        witness = getattr(op, "witness", None)
        extraction_witness = getattr(witness, "extraction_witness", None)
        if bool(getattr(extraction_witness, "metadata_fallback_used", False)):
            count += 1
    return count


def _uk_bench_replay_regime_result_fields(
    *,
    enacted_only: bool,
    oracle_alignment_enabled: bool,
    metadata_backfill_op_count: int,
    allow_metadata_backfill: bool,
    allow_metadata_only_effects: bool,
    applicability_mode: str,
    authority_mode: str,
    source_unavailable_reason: str = "",
) -> dict[str, object]:
    if source_unavailable_reason:
        source_purity_lane = "not_run_source_unavailable"
    else:
        source_purity_lane = (
            "metadata_backfilled_with_oracle_adapter"
            if metadata_backfill_op_count and oracle_alignment_enabled
            else "metadata_backfilled_source_semantics"
            if metadata_backfill_op_count
            else "source_backed_with_oracle_adapter"
            if oracle_alignment_enabled
            else "source_backed_effects_assisted"
        )
    source_first_candidate_reasons: list[str] = []
    if source_unavailable_reason:
        source_first_candidate_reasons.append("source_unavailable")
    else:
        if enacted_only:
            source_first_candidate_reasons.append("enacted_only_baseline")
        if metadata_backfill_op_count:
            source_first_candidate_reasons.append("metadata_backfill_ops_present")
        if oracle_alignment_enabled:
            source_first_candidate_reasons.append("oracle_alignment_adapter_active")
        if allow_metadata_only_effects:
            source_first_candidate_reasons.append("metadata_only_effects_enabled")
        if applicability_mode != "effective_date_plus_feed_applied":
            source_first_candidate_reasons.append("applicability_selection_not_feed_applied")
        if authority_mode != "source_text_only":
            source_first_candidate_reasons.append("authority_mode_not_source_text_only")
    return {
        "uk_source_purity_lane": source_purity_lane,
        "uk_source_semantics_clean": bool(
            not source_unavailable_reason
            and not enacted_only
            and authority_mode == "source_text_only"
            and not metadata_backfill_op_count
            and not allow_metadata_only_effects
            and not oracle_alignment_enabled
        ),
        "uk_source_first_candidate": not source_first_candidate_reasons,
        "uk_source_first_candidate_reasons": tuple(source_first_candidate_reasons),
    }


# ---------------------------------------------------------------------------
# Text-similarity helpers
# ---------------------------------------------------------------------------


def _normalize_text(text: str) -> str:
    """Normalize text for comparison: lowercase, strip punctuation, collapse whitespace.

    Mirrors the normalization used by extract_eid_map / _normalize_text_for_grounding
    in uk_grafter.py so enacted/replayed and oracle texts are on equal footing.
    """
    text = re.sub(r"[^\w\s]", "", text.lower())
    return " ".join(text.split())


def _collect_text(node: IRNode) -> str:
    """Concatenate all text from node and descendants."""
    parts = []
    if node.text:
        parts.append(node.text.strip())
    for child in node.children:
        child_text = _collect_text(child)
        if child_text:
            parts.append(child_text)
    return " ".join(parts)


def _extract_eid_texts(ir: "IRStatute", eids: Set[str]) -> Dict[str, str]:
    """Map EID → normalized text content for the given set of EIDs.

    Walks body children and schedules; only collects nodes whose eId/id
    attr is in *eids*.  The returned text is normalized with _normalize_text
    to match the oracle text_map produced by extract_eid_map.
    """
    texts: Dict[str, str] = {}

    def _walk(node: IRNode) -> None:
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid and eid in eids:
            raw = _collect_text(node)
            if raw:
                texts[eid] = _normalize_text(raw)
        for child in node.children:
            _walk(child)

    for child in ir.body.children:
        _walk(child)
    for sch in ir.supplements:
        _walk(sch)
    return texts


def _text_similarity_score(
    source_texts: Dict[str, str],
    oracle_texts: Dict[str, str],
) -> tuple[float, int]:
    """Average Levenshtein ratio across common EIDs that have non-empty text.

    Returns (score, n_compared).  score is -1.0 when no EIDs are comparable.
    """
    common = set(source_texts) & set(oracle_texts)
    # Only compare EIDs where both sides have actual text
    pairs = [(source_texts[e], oracle_texts[e]) for e in common if source_texts[e] and oracle_texts[e]]
    if not pairs:
        return -1.0, 0
    total = sum(Levenshtein.ratio(s, o) for s, o in pairs)
    return total / len(pairs), len(pairs)


# ---------------------------------------------------------------------------
# Corpus enumeration from Farchive
# ---------------------------------------------------------------------------


def _extract_sid_from_url(url: str, suffix: str) -> Optional[str]:
    """Extract 'type/year/num' from a legislation.gov.uk URL with a given suffix."""
    prefix = f"{_LEG_BASE}/"
    if not url.startswith(prefix):
        return None
    path = url[len(prefix) :]
    if not path.endswith(suffix):
        return None
    sid = path[: -len(suffix)]
    # Must match type/year/num pattern
    if re.fullmatch(r"[a-z]+/\d{4}/\d+", sid):
        return sid
    return None


def _build_corpus_index(
    archive: Farchive,
    types: Optional[frozenset[str]] = None,
) -> list[dict]:
    """Enumerate statutes in the archive that have both enacted and current XML.

    Returns list of dicts: {statute_id, type, year, has_enacted, has_consolidated,
                            n_effects, n_effect_feed_pages, enacted_url, current_url}.
    """
    conn = archive._conn

    # Find all enacted XML URLs
    enacted_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE '%/enacted/data.xml'"
    ).fetchall()

    enacted_sids: dict[str, str] = {}  # sid -> url
    for (url,) in enacted_rows:
        sid = _extract_sid_from_url(url, "/enacted/data.xml")
        if sid:
            act_type = sid.split("/")[0]
            if types is None or act_type in types:
                enacted_sids[sid] = url

    # Find all current (consolidated) XML URLs — not /enacted/ and not /changes/
    current_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span "
        "WHERE locator LIKE '%/data.xml' "
        "  AND locator NOT LIKE '%/enacted/%' "
        "  AND locator NOT LIKE '%/changes/%'"
    ).fetchall()

    current_sids: dict[str, str] = {}  # sid -> url
    for (url,) in current_rows:
        sid = _extract_sid_from_url(url, "/data.xml")
        if sid:
            act_type = sid.split("/")[0]
            if types is None or act_type in types:
                current_sids[sid] = url

    # Find statutes with effects feeds (any page)
    effects_rows = conn.execute(
        "SELECT DISTINCT locator FROM locator_span WHERE locator LIKE '%/data.feed%'"
    ).fetchall()

    effect_feed_page_counts: Counter[str] = Counter()
    for (url,) in effects_rows:
        # URL: /changes/affected/TYPE/YEAR/NUM/data.feed
        m = re.search(r"/changes/affected/([^/]+)/(\d+)/(\d+)/data\.feed", url)
        if m:
            sid = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
            effect_feed_page_counts[sid] += 1

    # Build corpus: only statutes with both enacted AND current
    both = set(enacted_sids) & set(current_sids)
    entries = []
    for sid in sorted(both):
        parts = sid.split("/")
        act_type, year = parts[0], parts[1]
        enacted_bytes = archive.get(enacted_sids[sid])
        oracle_bytes = archive.get(current_sids[sid])
        enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
        oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
        entries.append(
            {
                "statute_id": sid,
                "type": act_type,
                "year": int(year),
                "has_enacted": True,
                "has_consolidated": True,
                # Historical compatibility: n_effects currently means archived
                # effect-feed pages, not parsed effect rows.
                "n_effects": effect_feed_page_counts.get(sid, 0),
                "n_effect_feed_pages": effect_feed_page_counts.get(sid, 0),
                "enacted_url": enacted_sids[sid],
                "current_url": current_sids[sid],
                "enacted_source_status": enacted_source_status,
                "oracle_source_status": oracle_source_status,
                "enacted_source_size": enacted_source_size,
                "oracle_source_size": oracle_source_size,
                "enacted_source_sha256": _source_sha256(enacted_bytes),
                "oracle_source_sha256": _source_sha256(oracle_bytes),
            }
        )

    return entries


# ---------------------------------------------------------------------------
# Bench result
# ---------------------------------------------------------------------------


@dataclass
class _BenchScoreWitnessRow:
    comparison_scope: str
    side: str
    eid: str
    rank: int
    category_total: int
    sample_limit: int
    truncated: bool
    left_count: int
    right_count: int
    common_count: int
    score_value: float


@dataclass
class _BenchResult:
    statute_id: str
    act_type: str
    year: int
    n_effects: int
    n_enacted_eids: int
    n_oracle_eids: int
    n_common: int
    score: float
    status: str
    n_effect_feed_pages: int = 0
    n_effect_rows: int = 0
    effect_feed_rejection_count: int = 0
    effect_feed_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    effect_feed_observation_count: int = 0
    effect_feed_observation_rule_counts: dict[str, int] = field(default_factory=dict)
    effect_feed_observations: tuple[dict[str, Any], ...] = ()
    effect_feed_count_error: str = ""
    bench_exception_count: int = 0
    bench_exception_rule_counts: dict[str, int] = field(default_factory=dict)
    bench_exception_observations: tuple[dict[str, Any], ...] = ()
    source_parse_observations: tuple[dict[str, Any], ...] = ()
    source_parse_rejection_count: int = 0
    source_parse_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    source_parse_observation_count: int = 0
    source_parse_observation_rule_counts: dict[str, int] = field(default_factory=dict)
    effect_diagnostics: tuple[dict[str, Any], ...] = ()
    effect_source_pathology_counts: dict[str, int] = field(default_factory=dict)
    manual_compile_status_counts: dict[str, int] = field(default_factory=dict)
    manual_compile_rule_counts: dict[str, int] = field(default_factory=dict)
    source_acquisition_observation_count: int = 0
    source_acquisition_observation_rule_counts: dict[str, int] = field(default_factory=dict)
    source_acquisition_rejection_count: int = 0
    source_acquisition_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    enacted_source_status: str = "unknown"
    oracle_source_status: str = "unknown"
    enacted_source_size: int = 0
    oracle_source_size: int = 0
    enacted_source_sha256: str = ""
    oracle_source_sha256: str = ""
    enacted_source_url: str = ""
    oracle_source_url: str = ""
    oracle_alignment_changed_count: int = 0
    oracle_alignment_oracle_assigned_count: int = 0
    oracle_alignment_local_fallback_count: int = 0
    oracle_alignment_transparent_wrapper_cleared_count: int = 0
    oracle_alignment_before_node_count: int = 0
    oracle_alignment_after_node_count: int = 0
    oracle_alignment_node_count_mismatch: bool = False
    oracle_alignment_match_method_counts: dict[str, int] = field(default_factory=dict)
    uk_metadata_backfill_enabled: bool = True
    uk_oracle_alignment_enabled: bool = True
    uk_applicability_mode: str = "effective_date_plus_feed_applied"
    uk_authority_mode: str = "current_mixed"
    uk_metadata_only_effects_enabled: bool = True
    uk_source_purity_lane: str = ""
    uk_source_semantics_clean: bool = False
    uk_source_first_candidate: bool = False
    uk_source_first_candidate_reasons: tuple[str, ...] = ()
    uk_authority_observation_count: int = 0
    uk_authority_observation_rule_counts: dict[str, int] = field(default_factory=dict)
    uk_authority_rejection_count: int = 0
    uk_authority_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    uk_authority_observations: tuple[dict[str, Any], ...] = ()
    lowering_observation_count: int = 0
    lowering_observation_rule_counts: dict[str, int] = field(default_factory=dict)
    lowering_rejection_count: int = 0
    lowering_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    blocking_lowering_rejection_count: int = 0
    blocking_lowering_rejection_rule_counts: dict[str, int] = field(default_factory=dict)
    lowering_rejections: tuple[dict[str, Any], ...] = ()
    error: str = ""
    # Replay fields (populated only when --replay is active)
    n_replayed_eids: int = 0
    n_replay_common: int = 0
    replay_score: float = -1.0  # -1 = not attempted
    n_ops: int = 0
    replay_error: str = ""
    replay_adjudication_count: int = 0
    replay_adjudication_kind_counts: dict[str, int] = field(default_factory=dict)
    replay_adjudications: tuple[dict[str, Any], ...] = ()
    uk_residual_claim_tier: str = "UNRESOLVED"
    uk_residual_claim_kind: str = "not_run"
    uk_residual_claim_comparison_class: str = ""
    uk_residual_claim_core_comparison: bool = False
    uk_residual_only_in_replayed_count: int = 0
    uk_residual_only_in_oracle_count: int = 0
    uk_residual_section_claim_count: int = 0
    uk_residual_section_claim_emitted: bool = False
    score_witness_rows: tuple[_BenchScoreWitnessRow, ...] = ()
    # Text-similarity fields (common EIDs, Levenshtein ratio)
    text_score: float = -1.0  # enacted vs oracle; -1 = not computed
    n_text_compared: int = 0  # number of EIDs compared for text_score
    replay_text_score: float = -1.0  # replayed vs oracle; -1 = not computed
    # Commencement-filtered fields (populated only when --commencement is active)
    commencement_score: float = -1.0  # enacted vs oracle within commencement lens; -1 = not computed
    n_commenced_eids: int = 0  # how many enacted EIDs are commenced
    replay_commencement_score: float = -1.0  # replay vs oracle within commencement lens; -1 = not computed
    commencement_error: str = ""
    comparison_class: str = ""
    core_benchmark: bool = True


def _bench_primary_score(result: _BenchResult, *, has_commencement: bool) -> float:
    if has_commencement and result.commencement_score >= 0.0:
        return result.commencement_score
    return result.score


def _bench_primary_replay_score(result: _BenchResult, *, has_commencement: bool) -> float:
    if has_commencement and result.replay_commencement_score >= 0.0:
        return result.replay_commencement_score
    return result.replay_score


def _average_primary_ok_score(
    results: Sequence[_BenchResult],
    *,
    has_commencement: bool,
) -> float:
    ok_results = [
        result
        for result in results
        if result.status == "OK" and result.n_oracle_eids > 0
    ]
    if not ok_results:
        return 0.0
    return sum(
        _bench_primary_score(result, has_commencement=has_commencement)
        for result in ok_results
    ) / len(ok_results)


def _normalize_uk_bench_replay_regime(args: Any) -> UKReplayRegime:
    return normalize_uk_replay_regime(args)


# ---------------------------------------------------------------------------
# Score one statute
# ---------------------------------------------------------------------------


def _load_effect_row_counts(
    statute_id: str,
    archive: Farchive,
) -> tuple[int, int, dict[str, int], int, dict[str, int], tuple[dict[str, Any], ...]]:
    """Return parsed effect rows plus visible feed rejection/observation counts.

    ``n_effects`` in old bench CSVs means archived effect-feed pages.  Benchmark
    triage needs the post-parse row count because a present but malformed/empty
    feed is materially different from an actionable effect row.
    """
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive

    feed_observations: list[dict[str, Any]] = []
    effects = load_effects_for_statute_from_archive(
        statute_id,
        archive,
        parse_rejections_out=feed_observations,
    )
    blocking_observations = [obs for obs in feed_observations if is_blocking_compile_record(obs)]
    feed_rejection_rule_counts = Counter(str(obs.get("rule_id") or "unknown") for obs in blocking_observations)
    feed_observation_rule_counts = Counter(str(obs.get("rule_id") or "unknown") for obs in feed_observations)
    return (
        len(effects),
        len(blocking_observations),
        dict(feed_rejection_rule_counts),
        len(feed_observations),
        dict(feed_observation_rule_counts),
        tuple(dict(obs) for obs in feed_observations),
    )


def _score_statute(
    entry: dict,
    archive: Farchive,
    do_replay: bool = False,
    repo_root: Optional[Path] = None,
    do_commencement: bool = False,
    allow_metadata_backfill: bool = True,
    allow_oracle_alignment: bool = True,
    applicability_mode: str = "effective_date_plus_feed_applied",
    authority_mode: str = "current_mixed",
    allow_metadata_only_effects: bool = True,
) -> _BenchResult:
    sid = entry["statute_id"]
    act_type = entry["type"]
    year = entry["year"]
    n_effects = entry["n_effects"]
    n_effect_feed_pages = entry.get("n_effect_feed_pages", n_effects)
    n_effect_rows = 0
    effect_feed_rejection_count = 0
    effect_feed_rejection_rule_counts: dict[str, int] = {}
    effect_feed_observation_count = 0
    effect_feed_observation_rule_counts: dict[str, int] = {}
    effect_feed_observations: tuple[dict[str, Any], ...] = ()
    effect_feed_count_error = ""
    source_parse_observations: list[dict[str, Any]] = []

    enacted_url = entry["enacted_url"]
    current_url = entry["current_url"]
    enacted_source_status = "unknown"
    oracle_source_status = "unknown"
    enacted_source_size = 0
    oracle_source_size = 0
    enacted_source_sha256 = ""
    oracle_source_sha256 = ""
    authority_rejections: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    uk_authority_rejection_count = 0
    uk_authority_rejection_rule_counts: dict[str, int] = {}
    uk_authority_observation_count = 0
    uk_authority_observation_rule_counts: dict[str, int] = {}
    lowering_rejection_count = 0
    lowering_rejection_rule_counts: dict[str, int] = {}
    lowering_observation_count = 0
    lowering_observation_rule_counts: dict[str, int] = {}
    blocking_lowering_rejection_count = 0
    blocking_lowering_rejection_rule_counts: dict[str, int] = {}
    effect_source_pathology_counts: dict[str, int] = {}
    manual_compile_status_counts: dict[str, int] = {}
    manual_compile_rule_counts: dict[str, int] = {}
    source_acquisition_observation_count = 0
    source_acquisition_observation_rule_counts: dict[str, int] = {}
    source_acquisition_rejection_count = 0
    source_acquisition_rejection_rule_counts: dict[str, int] = {}

    try:
        try:
            (
                n_effect_rows,
                effect_feed_rejection_count,
                effect_feed_rejection_rule_counts,
                effect_feed_observation_count,
                effect_feed_observation_rule_counts,
                effect_feed_observations,
            ) = _load_effect_row_counts(sid, archive)
        except Exception as effect_count_exc:
            # Bench row scoring should survive acquisition/parse diagnostics,
            # but the source-fact loss must remain visible in saved runs.
            effect_feed_rejection_count = 1
            effect_feed_rejection_rule_counts = {"uk_effect_feed_count_error": 1}
            effect_feed_observation_count = 1
            effect_feed_observation_rule_counts = {"uk_effect_feed_count_error": 1}
            effect_feed_count_error = f"{type(effect_count_exc).__name__}: {effect_count_exc}"[:200]
            effect_feed_observations = (
                {
                    "rule_id": "uk_effect_feed_count_error",
                    "family": "source_pathology",
                    "phase": "acquisition",
                    "statute_id": sid,
                    "reason": "UK effect feed count failed during benchmark preflight.",
                    "exception_type": type(effect_count_exc).__name__,
                    "exception_message": str(effect_count_exc),
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                },
            )
            print(
                f"  EFFECT FEED COUNT ERROR {sid}: {effect_feed_count_error}",
                file=sys.stderr,
            )

        enacted_bytes = archive.get(enacted_url)
        enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
        enacted_source_sha256 = _source_sha256(enacted_bytes)
        oracle_bytes = archive.get(current_url)
        oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
        oracle_source_sha256 = _source_sha256(oracle_bytes)
        if enacted_source_status != "available":
            return _BenchResult(
                statute_id=sid,
                act_type=act_type,
                year=year,
                n_effects=n_effects,
                n_effect_feed_pages=n_effect_feed_pages,
                n_enacted_eids=0,
                n_oracle_eids=0,
                n_common=0,
                score=0.0,
                status="NO_ENACTED",
                n_effect_rows=n_effect_rows,
                effect_feed_rejection_count=effect_feed_rejection_count,
                effect_feed_rejection_rule_counts=effect_feed_rejection_rule_counts,
                effect_feed_observation_count=effect_feed_observation_count,
                effect_feed_observation_rule_counts=effect_feed_observation_rule_counts,
                effect_feed_observations=effect_feed_observations,
                effect_feed_count_error=effect_feed_count_error,
                enacted_source_status=enacted_source_status,
                oracle_source_status=oracle_source_status,
                enacted_source_size=enacted_source_size,
                oracle_source_size=oracle_source_size,
                enacted_source_sha256=enacted_source_sha256,
                oracle_source_sha256=oracle_source_sha256,
                enacted_source_url=enacted_url,
                oracle_source_url=current_url,
                uk_metadata_backfill_enabled=allow_metadata_backfill,
                uk_oracle_alignment_enabled=allow_oracle_alignment,
                uk_applicability_mode=applicability_mode,
                uk_authority_mode=authority_mode,
                uk_metadata_only_effects_enabled=allow_metadata_only_effects,
                **_uk_bench_replay_regime_result_fields(
                    enacted_only=False,
                    oracle_alignment_enabled=allow_oracle_alignment,
                    metadata_backfill_op_count=0,
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_metadata_only_effects=allow_metadata_only_effects,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                    source_unavailable_reason="enacted_xml_unavailable",
                ),
                error="enacted XML missing or empty",
                comparison_class="no_enacted_eids",
                core_benchmark=False,
            )
        assert enacted_bytes is not None

        if oracle_source_status != "available":
            return _BenchResult(
                statute_id=sid,
                act_type=act_type,
                year=year,
                n_effects=n_effects,
                n_effect_feed_pages=n_effect_feed_pages,
                n_enacted_eids=0,
                n_oracle_eids=0,
                n_common=0,
                score=0.0,
                status="NO_ORACLE",
                n_effect_rows=n_effect_rows,
                effect_feed_rejection_count=effect_feed_rejection_count,
                effect_feed_rejection_rule_counts=effect_feed_rejection_rule_counts,
                effect_feed_observation_count=effect_feed_observation_count,
                effect_feed_observation_rule_counts=effect_feed_observation_rule_counts,
                effect_feed_observations=effect_feed_observations,
                effect_feed_count_error=effect_feed_count_error,
                enacted_source_status=enacted_source_status,
                oracle_source_status=oracle_source_status,
                enacted_source_size=enacted_source_size,
                oracle_source_size=oracle_source_size,
                enacted_source_sha256=enacted_source_sha256,
                oracle_source_sha256=oracle_source_sha256,
                enacted_source_url=enacted_url,
                oracle_source_url=current_url,
                uk_metadata_backfill_enabled=allow_metadata_backfill,
                uk_oracle_alignment_enabled=allow_oracle_alignment,
                uk_applicability_mode=applicability_mode,
                uk_authority_mode=authority_mode,
                uk_metadata_only_effects_enabled=allow_metadata_only_effects,
                **_uk_bench_replay_regime_result_fields(
                    enacted_only=False,
                    oracle_alignment_enabled=allow_oracle_alignment,
                    metadata_backfill_op_count=0,
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_metadata_only_effects=allow_metadata_only_effects,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                    source_unavailable_reason="oracle_xml_unavailable",
                ),
                error="current XML missing or empty",
                comparison_class="no_oracle_eids",
                core_benchmark=False,
            )
        assert oracle_bytes is not None

        try:
            enacted_ir = parse_uk_statute_ir_bytes(
                enacted_bytes,
                statute_id=sid,
                version_label="enacted",
                source_path=enacted_url,
            )
            source_parse_observations.extend(uk_source_parse_observations_from_ir(enacted_ir))
        except Exception as enacted_parse_exc:
            source_parse_observations.append(
                uk_source_xml_parse_rejection(
                    statute_id=sid,
                    side="enacted",
                    source_url=enacted_url,
                    exc=enacted_parse_exc,
                )
            )
            raise
        try:
            oracle_eid_data = extract_eid_map_bytes(oracle_bytes)
        except Exception as oracle_parse_exc:
            source_parse_observations.append(
                uk_source_xml_parse_rejection(
                    statute_id=sid,
                    side="oracle",
                    source_url=current_url,
                    exc=oracle_parse_exc,
                )
            )
            raise
        oracle_eids: Set[str] = set(oracle_eid_data.get("eid_map", {}).values())
        oracle_physical_eid_aliases = oracle_eid_data.get("physical_eid_aliases", {})
        oracle_visible_number_eid_aliases = oracle_eid_data.get("visible_number_eid_aliases", {})

        enacted_eids = _collect_eids(enacted_ir.body.children)
        for s in enacted_ir.supplements:
            enacted_eids.update(_collect_eids([s]))

        common = enacted_eids & oracle_eids
        score = _score_eids(enacted_eids, oracle_eids)
        score_witness_rows = list(
            _build_eid_score_witness_rows(
                comparison_scope="raw",
                left_side="only_in_enacted",
                left_eids=enacted_eids,
                right_eids=oracle_eids,
            )
        )

        # ── Text similarity: enacted vs oracle ─────────────────────────
        oracle_text_map: Dict[str, str] = oracle_eid_data.get("text_map", {})
        enacted_texts = _extract_eid_texts(enacted_ir, common)
        text_score, n_text_compared = _text_similarity_score(enacted_texts, oracle_text_map)

        # ── Optional replay ────────────────────────────────────────────
        n_ops = 0
        n_replayed_eids = 0
        n_replay_common = 0
        replay_score = -1.0
        replay_text_score = -1.0
        replay_error = ""
        replay_compare_eids: set[str] = set()
        oracle_compare_eids: set[str] = set()
        replay_adjudication_count = 0
        replay_adjudication_kind_counts: dict[str, int] = {}
        replay_adjudications: list[Any] = []
        replay_adjudication_records: tuple[dict[str, Any], ...] = ()
        uk_residual_claim_tier = "UNRESOLVED"
        uk_residual_claim_kind = "not_run"
        uk_residual_claim_comparison_class = ""
        uk_residual_claim_core_comparison = False
        uk_residual_only_in_replayed_count = 0
        uk_residual_only_in_oracle_count = 0
        uk_residual_section_claim_count = 0
        uk_residual_section_claim_emitted = False
        oracle_alignment_changed_count = 0
        oracle_alignment_oracle_assigned_count = 0
        oracle_alignment_local_fallback_count = 0
        oracle_alignment_transparent_wrapper_cleared_count = 0
        oracle_alignment_before_node_count = 0
        oracle_alignment_after_node_count = 0
        oracle_alignment_node_count_mismatch = False
        oracle_alignment_match_method_counts: dict[str, int] = {}
        lowering_rejection_count = 0
        lowering_rejection_rule_counts: dict[str, int] = {}
        lowering_observation_count = 0
        lowering_observation_rule_counts: dict[str, int] = {}
        blocking_lowering_rejection_count = 0
        blocking_lowering_rejection_rule_counts: dict[str, int] = {}
        replayed_ir = None  # may be set below if do_replay succeeds
        uk_replay_regime_fields = _uk_bench_replay_regime_result_fields(
            enacted_only=not do_replay,
            oracle_alignment_enabled=allow_oracle_alignment,
            metadata_backfill_op_count=0,
            allow_metadata_backfill=allow_metadata_backfill,
            allow_metadata_only_effects=allow_metadata_only_effects,
            applicability_mode=applicability_mode,
            authority_mode=authority_mode,
        )

        if do_replay and repo_root is not None:
            def _record_compile_diagnostics() -> None:
                nonlocal effect_source_pathology_counts
                nonlocal manual_compile_status_counts
                nonlocal manual_compile_rule_counts
                nonlocal source_acquisition_observation_count
                nonlocal source_acquisition_observation_rule_counts
                nonlocal source_acquisition_rejection_count
                nonlocal source_acquisition_rejection_rule_counts
                nonlocal uk_authority_observation_count
                nonlocal uk_authority_observation_rule_counts
                nonlocal uk_authority_rejection_count
                nonlocal uk_authority_rejection_rule_counts
                nonlocal lowering_rejection_count
                nonlocal lowering_rejection_rule_counts
                nonlocal lowering_observation_count
                nonlocal lowering_observation_rule_counts
                nonlocal blocking_lowering_rejection_count
                nonlocal blocking_lowering_rejection_rule_counts

                effect_source_pathology_counts = dict(
                    Counter(
                        str(row.get("source_pathology") or "__none__")
                        for row in effect_diagnostics
                        if row.get("rule_id") == "uk_effect_source_pathology_classified"
                    )
                )
                manual_compile_status_counts = dict(
                    Counter(
                        str(row.get("manual_compile_status") or "__none__")
                        for row in effect_diagnostics
                        if row.get("rule_id") == "uk_manual_compile_frontier_classified"
                    )
                )
                manual_compile_rule_counts = dict(
                    Counter(
                        str(row.get("manual_compile_rule_id") or "__none__")
                        for row in effect_diagnostics
                        if row.get("rule_id") == "uk_manual_compile_frontier_classified"
                    )
                )
                source_acquisition_observations = [
                    row
                    for row in effect_diagnostics
                    if is_uk_affecting_act_xml_source_observation(row)
                ]
                source_acquisition_observation_count = len(source_acquisition_observations)
                source_acquisition_observation_rule_counts = dict(
                    Counter(
                        str(observation.get("rule_id") or "unknown")
                        for observation in source_acquisition_observations
                    )
                )
                source_acquisition_rejections = [
                    row for row in source_acquisition_observations if is_blocking_compile_record(row)
                ]
                source_acquisition_rejection_count = len(source_acquisition_rejections)
                source_acquisition_rejection_rule_counts = dict(
                    Counter(
                        str(rejection.get("rule_id") or "unknown")
                        for rejection in source_acquisition_rejections
                    )
                )
                uk_authority_observation_count = len(authority_rejections)
                uk_authority_observation_rule_counts = dict(
                    Counter(str(rejection.get("rule_id") or "unknown") for rejection in authority_rejections)
                )
                blocking_authority_rejections = [
                    rejection for rejection in authority_rejections if is_blocking_compile_record(rejection)
                ]
                uk_authority_rejection_count = len(blocking_authority_rejections)
                uk_authority_rejection_rule_counts = dict(
                    Counter(
                        str(rejection.get("rule_id") or "unknown")
                        for rejection in blocking_authority_rejections
                    )
                )
                lowering_observation_count = len(lowering_rejections)
                lowering_observation_rule_counts = dict(
                    Counter(str(rejection.get("rule_id") or "unknown") for rejection in lowering_rejections)
                )
                # Compatibility: historical CSV/history fields named all lowering
                # diagnostic rows as "rejections". Keep them as aliases while
                # exposing the explicit observation lane above.
                lowering_rejection_count = len(lowering_rejections)
                lowering_rejection_rule_counts = dict(lowering_observation_rule_counts)
                blocking_lowering_rejection_count = sum(
                    1 for rejection in lowering_rejections if is_blocking_compile_record(rejection)
                )
                blocking_lowering_rejection_rule_counts = dict(
                    Counter(
                        str(rejection.get("rule_id") or "unknown")
                        for rejection in lowering_rejections
                        if is_blocking_compile_record(rejection)
                    )
                )

            try:
                from lawvm.uk_legislation.uk_amendment_replay import (
                    UKReplayPipeline,
                    replay_uk_ops,
                )
                from lawvm.uk_legislation.oracle_align import align_uk_replay_to_oracle_with_report

                pipeline = UKReplayPipeline(repo_root)
                ops = pipeline.compile_ops_for_statute(
                    sid,
                    archive=archive,
                    allow_metadata_backfill=allow_metadata_backfill,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                    allow_metadata_only_effects=allow_metadata_only_effects,
                    authority_rejections_out=authority_rejections,
                    lowering_rejections_out=lowering_rejections,
                    effect_diagnostics_out=effect_diagnostics,
                )
                _record_compile_diagnostics()
                n_ops = len(ops)
                uk_replay_regime_fields = _uk_bench_replay_regime_result_fields(
                    enacted_only=False,
                    oracle_alignment_enabled=allow_oracle_alignment,
                    metadata_backfill_op_count=_uk_metadata_backfill_op_count(ops),
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_metadata_only_effects=allow_metadata_only_effects,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                )
                eid_map = oracle_eid_data.get("eid_map", {})
                text_map = oracle_eid_data.get("text_map", {})
                replayed_ir = replay_uk_ops(
                    enacted_ir,
                    ops,
                    eid_map=eid_map,
                    text_map=text_map,
                    allow_oracle_alignment=allow_oracle_alignment,
                    adjudications_out=replay_adjudications,
                )
                replay_adjudication_count = len(replay_adjudications)
                replay_adjudication_records = tuple(
                    _replay_adjudication_record(adjudication)
                    for adjudication in replay_adjudications
                )
                replay_adjudication_kind_counts = dict(
                    Counter(
                        str(record.get("kind") or "unknown")
                        for record in replay_adjudication_records
                    )
                )
                _apply_replay_preimage_frontier_to_effect_diagnostics(
                    effect_diagnostics,
                    replay_adjudication_records,
                )
                _record_compile_diagnostics()
                if allow_oracle_alignment:
                    alignment_result = align_uk_replay_to_oracle_with_report(
                        replayed_ir,
                        eid_map=eid_map,
                        text_map=text_map,
                    )
                    replayed_ir = alignment_result.statute
                    oracle_alignment_changed_count = alignment_result.report.changed_count
                    oracle_alignment_oracle_assigned_count = alignment_result.report.oracle_assigned_count
                    oracle_alignment_local_fallback_count = alignment_result.report.local_fallback_count
                    oracle_alignment_transparent_wrapper_cleared_count = (
                        alignment_result.report.transparent_wrapper_cleared_count
                    )
                    oracle_alignment_before_node_count = alignment_result.report.before_node_count
                    oracle_alignment_after_node_count = alignment_result.report.after_node_count
                    oracle_alignment_node_count_mismatch = alignment_result.report.node_count_mismatch
                    oracle_alignment_match_method_counts = dict(
                        alignment_result.report.match_method_counts
                    )
                replayed_eids = _collect_eids(replayed_ir.body.children)
                for s in replayed_ir.supplements:
                    replayed_eids.update(_collect_eids([s]))
                n_replayed_eids = len(replayed_eids)
                replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
                    replayed_eids,
                    oracle_eids,
                    oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                    oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
                )
                replay_common = replay_compare_eids & oracle_compare_eids
                n_replay_common = len(replay_common)
                replay_score = _score_eids(replay_compare_eids, oracle_compare_eids)
                only_in_replayed = replay_compare_eids - oracle_compare_eids
                only_in_oracle = oracle_compare_eids - replay_compare_eids
                uk_residual_claim_comparison_class = classify_uk_bench_comparison(
                    n_enacted_eids=len(enacted_eids),
                    n_oracle_eids=len(oracle_eids),
                    n_effects=n_effect_rows,
                    raw_score=replay_score,
                    effect_source_pathology_counts=effect_source_pathology_counts,
                )
                current_projection_shape = classify_uk_current_projection_eid_shape(
                    enacted_eids=enacted_eids,
                    oracle_eids=oracle_eids,
                )
                if (
                    current_projection_shape
                    and uk_residual_claim_comparison_class == "commensurable"
                    and replay_score < 1.0
                ):
                    uk_residual_claim_comparison_class = current_projection_shape
                uk_residual_claim_core_comparison = is_core_uk_comparison(
                    uk_residual_claim_comparison_class
                )
                (
                    uk_residual_claim_tier,
                    uk_residual_claim_kind,
                    uk_residual_section_claim_count,
                ) = _classify_uk_residual_claim_for_bench(
                    comparison_class=uk_residual_claim_comparison_class,
                    only_in_replayed=only_in_replayed,
                    only_in_oracle=only_in_oracle,
                    replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                    lowering_observations=lowering_rejections,
                )
                uk_residual_only_in_replayed_count = len(only_in_replayed)
                uk_residual_only_in_oracle_count = len(only_in_oracle)
                uk_residual_section_claim_emitted = bool(uk_residual_section_claim_count)
                score_witness_rows.extend(
                    _build_eid_score_witness_rows(
                        comparison_scope="replay",
                        left_side="only_in_replay",
                        left_eids=replay_compare_eids,
                        right_eids=oracle_compare_eids,
                    )
                )
                replayed_texts = _extract_eid_texts(replayed_ir, replayed_eids & oracle_eids)
                replay_text_score, _ = _text_similarity_score(replayed_texts, oracle_text_map)
            except Exception as replay_exc:
                # Replay failure is non-fatal — record it but keep enacted score.
                # Log the error so it is visible in bench output (not silently swallowed).
                print(
                    f"  REPLAY ERROR {sid}: {type(replay_exc).__name__}: {replay_exc}",
                    file=sys.stderr,
                )
                _record_compile_diagnostics()
                if replay_adjudications:
                    replay_adjudication_records = tuple(
                        _replay_adjudication_record(adjudication)
                        for adjudication in replay_adjudications
                    )
                    replay_adjudication_count = len(replay_adjudication_records)
                    replay_adjudication_kind_counts = dict(
                        Counter(
                            str(record.get("kind") or "unknown")
                            for record in replay_adjudication_records
                        )
                    )
                    _apply_replay_preimage_frontier_to_effect_diagnostics(
                        effect_diagnostics,
                        replay_adjudication_records,
                    )
                    _record_compile_diagnostics()
                    (
                        uk_residual_claim_tier,
                        uk_residual_claim_kind,
                        uk_residual_section_claim_count,
                    ) = _classify_uk_residual_claim_for_bench(
                        comparison_class="commensurable",
                        only_in_replayed=set(),
                        only_in_oracle=set(),
                        replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                        lowering_observations=lowering_rejections,
                    )
                    uk_residual_claim_comparison_class = "replay_exception"
                    uk_residual_claim_core_comparison = False
                    uk_residual_section_claim_emitted = bool(uk_residual_section_claim_count)
                replay_score = -1.0
                n_ops = -1  # signals error
                replay_error = f"{type(replay_exc).__name__}: {replay_exc}"[:200]

        # ── Optional commencement filtering ─────────────────────────────
        commencement_score = -1.0
        n_commenced_eids = 0
        replay_commencement_score = -1.0
        commencement_error = ""
        commenced_replayed: set[str] = set()
        commenced_oracle_for_replay: set[str] = set()

        if do_commencement:
            commencement_feed_observations: list[dict[str, Any]] = []
            try:
                from lawvm.uk_legislation.effects import (
                    load_effects_for_statute_from_archive,
                )
                from lawvm.uk_legislation.uk_amendment_replay import (
                    commencement_eid_set,
                )

                all_effects = load_effects_for_statute_from_archive(
                    sid,
                    archive,
                    parse_rejections_out=commencement_feed_observations,
                )
                commenced = commencement_eid_set(
                    all_effects,
                    enacted_ir,
                    applicability_mode=applicability_mode,
                    observations_out=commencement_feed_observations,
                )
                commenced_enacted = enacted_eids & commenced
                commenced_oracle = _commenced_oracle_eids(oracle_eids, commenced)
                n_commenced_eids = len(commenced_enacted)
                commencement_score = _score_commenced_eids(commenced_enacted, commenced_oracle)
                score_witness_rows.extend(
                    _build_eid_score_witness_rows(
                        comparison_scope="commencement",
                        left_side="only_in_commenced_enacted",
                        left_eids=commenced_enacted,
                        right_eids=commenced_oracle,
                    )
                )
                if replayed_ir is not None:
                    _replayed_eids_all = _collect_eids(replayed_ir.body.children)
                    for s in replayed_ir.supplements:
                        _replayed_eids_all.update(_collect_eids([s]))
                    commenced_replayed_raw = _replayed_eids_all & commenced
                    commenced_replayed, commenced_oracle_for_replay = normalize_uk_replay_compare_eids(
                        commenced_replayed_raw,
                        commenced_oracle,
                        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
                    )
                    replay_commencement_score = _score_commenced_eids(
                        commenced_replayed,
                        commenced_oracle_for_replay,
                    )
                    score_witness_rows.extend(
                        _build_eid_score_witness_rows(
                            comparison_scope="replay_commencement",
                            left_side="only_in_commenced_replay",
                            left_eids=commenced_replayed,
                            right_eids=commenced_oracle_for_replay,
                        )
                    )
            except Exception as comm_exc:
                print(
                    f"  COMMENCEMENT ERROR {sid}: {type(comm_exc).__name__}: {comm_exc}",
                    file=sys.stderr,
                )
                commencement_error = f"{type(comm_exc).__name__}: {comm_exc}"[:200]
            if commencement_feed_observations:
                commencement_blocking_feed_observations = [
                    obs for obs in commencement_feed_observations if is_blocking_compile_record(obs)
                ]
                effect_feed_rejection_count += len(commencement_blocking_feed_observations)
                effect_feed_observation_count += len(commencement_feed_observations)
                effect_feed_observations = (
                    *effect_feed_observations,
                    *(dict(obs) for obs in commencement_feed_observations),
                )
                effect_feed_rejection_counter = Counter(effect_feed_rejection_rule_counts)
                effect_feed_rejection_counter.update(
                    str(obs.get("rule_id") or "unknown")
                    for obs in commencement_blocking_feed_observations
                )
                effect_feed_rejection_rule_counts = dict(effect_feed_rejection_counter)
                effect_feed_observation_counter = Counter(effect_feed_observation_rule_counts)
                effect_feed_observation_counter.update(
                    str(obs.get("rule_id") or "unknown") for obs in commencement_feed_observations
                )
                effect_feed_observation_rule_counts = dict(effect_feed_observation_counter)

        comparison_class = classify_uk_bench_comparison(
            n_enacted_eids=len(enacted_eids),
            n_oracle_eids=len(oracle_eids),
            n_effects=n_effect_rows,
            raw_score=score,
            effect_source_pathology_counts=effect_source_pathology_counts,
        )
        current_projection_shape = classify_uk_current_projection_eid_shape(
            enacted_eids=enacted_eids,
            oracle_eids=oracle_eids,
        )
        if current_projection_shape and comparison_class == "commensurable" and score < 1.0:
            comparison_class = current_projection_shape
        commencement_projection_shape = classify_uk_commencement_current_projection(
            replay_compare_eids=replay_compare_eids,
            oracle_compare_eids=oracle_compare_eids,
            commenced_replay_eids=commenced_replayed,
            commenced_oracle_eids=commenced_oracle_for_replay,
        )
        if commencement_projection_shape and comparison_class == "commensurable":
            comparison_class = commencement_projection_shape
        if (
            commencement_projection_shape
            and uk_residual_claim_comparison_class == "commensurable"
        ):
            uk_residual_claim_comparison_class = commencement_projection_shape
            uk_residual_claim_core_comparison = is_core_uk_comparison(
                uk_residual_claim_comparison_class
            )
            (
                uk_residual_claim_tier,
                uk_residual_claim_kind,
                uk_residual_section_claim_count,
            ) = _classify_uk_residual_claim_for_bench(
                comparison_class=uk_residual_claim_comparison_class,
                only_in_replayed=replay_compare_eids - oracle_compare_eids,
                only_in_oracle=oracle_compare_eids - replay_compare_eids,
                replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                lowering_observations=lowering_rejections,
            )
            uk_residual_section_claim_emitted = bool(uk_residual_section_claim_count)
        source_parse_rejections = _blocking_source_parse_rows(source_parse_observations)

        return _BenchResult(
            statute_id=sid,
            act_type=act_type,
            year=year,
            n_effects=n_effects,
            n_effect_feed_pages=n_effect_feed_pages,
            n_enacted_eids=len(enacted_eids),
            n_oracle_eids=len(oracle_eids),
            n_common=len(common),
            score=score,
            status="OK",
            n_effect_rows=n_effect_rows,
            effect_feed_rejection_count=effect_feed_rejection_count,
            effect_feed_rejection_rule_counts=effect_feed_rejection_rule_counts,
            effect_feed_observation_count=effect_feed_observation_count,
            effect_feed_observation_rule_counts=effect_feed_observation_rule_counts,
            effect_feed_observations=effect_feed_observations,
            effect_feed_count_error=effect_feed_count_error,
            source_parse_rejection_count=len(source_parse_rejections),
            source_parse_rejection_rule_counts=_rule_counts(source_parse_rejections),
            source_parse_observation_count=len(source_parse_observations),
            source_parse_observation_rule_counts=_rule_counts(source_parse_observations),
            source_parse_observations=tuple(source_parse_observations),
            enacted_source_status=enacted_source_status,
            oracle_source_status=oracle_source_status,
            enacted_source_size=enacted_source_size,
            oracle_source_size=oracle_source_size,
            enacted_source_sha256=enacted_source_sha256,
            oracle_source_sha256=oracle_source_sha256,
            enacted_source_url=enacted_url,
            oracle_source_url=current_url,
            n_replayed_eids=n_replayed_eids,
            n_replay_common=n_replay_common,
            replay_score=replay_score,
            n_ops=n_ops,
            replay_error=replay_error,
            replay_adjudication_count=replay_adjudication_count,
            replay_adjudication_kind_counts=replay_adjudication_kind_counts,
            replay_adjudications=replay_adjudication_records,
            uk_residual_claim_tier=uk_residual_claim_tier,
            uk_residual_claim_kind=uk_residual_claim_kind,
            uk_residual_claim_comparison_class=uk_residual_claim_comparison_class,
            uk_residual_claim_core_comparison=uk_residual_claim_core_comparison,
            uk_residual_only_in_replayed_count=uk_residual_only_in_replayed_count,
            uk_residual_only_in_oracle_count=uk_residual_only_in_oracle_count,
            uk_residual_section_claim_count=uk_residual_section_claim_count,
            uk_residual_section_claim_emitted=uk_residual_section_claim_emitted,
            oracle_alignment_changed_count=oracle_alignment_changed_count,
            oracle_alignment_oracle_assigned_count=oracle_alignment_oracle_assigned_count,
            oracle_alignment_local_fallback_count=oracle_alignment_local_fallback_count,
            oracle_alignment_transparent_wrapper_cleared_count=(
                oracle_alignment_transparent_wrapper_cleared_count
            ),
            oracle_alignment_before_node_count=oracle_alignment_before_node_count,
            oracle_alignment_after_node_count=oracle_alignment_after_node_count,
            oracle_alignment_node_count_mismatch=oracle_alignment_node_count_mismatch,
            oracle_alignment_match_method_counts=oracle_alignment_match_method_counts,
            uk_metadata_backfill_enabled=allow_metadata_backfill,
            uk_oracle_alignment_enabled=allow_oracle_alignment,
            uk_applicability_mode=applicability_mode,
            uk_authority_mode=authority_mode,
            uk_metadata_only_effects_enabled=allow_metadata_only_effects,
            **uk_replay_regime_fields,
            uk_authority_observation_count=uk_authority_observation_count,
            uk_authority_observation_rule_counts=uk_authority_observation_rule_counts,
            uk_authority_rejection_count=uk_authority_rejection_count,
            uk_authority_rejection_rule_counts=uk_authority_rejection_rule_counts,
            uk_authority_observations=tuple(authority_rejections),
            lowering_rejection_count=lowering_rejection_count,
            lowering_rejection_rule_counts=lowering_rejection_rule_counts,
            lowering_observation_count=lowering_observation_count,
            lowering_observation_rule_counts=lowering_observation_rule_counts,
            blocking_lowering_rejection_count=blocking_lowering_rejection_count,
            blocking_lowering_rejection_rule_counts=blocking_lowering_rejection_rule_counts,
            lowering_rejections=tuple(lowering_rejections),
            effect_diagnostics=tuple(effect_diagnostics),
            effect_source_pathology_counts=effect_source_pathology_counts,
            manual_compile_status_counts=manual_compile_status_counts,
            manual_compile_rule_counts=manual_compile_rule_counts,
            source_acquisition_observation_count=source_acquisition_observation_count,
            source_acquisition_observation_rule_counts=source_acquisition_observation_rule_counts,
            source_acquisition_rejection_count=source_acquisition_rejection_count,
            source_acquisition_rejection_rule_counts=source_acquisition_rejection_rule_counts,
            text_score=text_score,
            n_text_compared=n_text_compared,
            replay_text_score=replay_text_score,
            commencement_score=commencement_score,
            n_commenced_eids=n_commenced_eids,
            replay_commencement_score=replay_commencement_score,
            commencement_error=commencement_error,
            comparison_class=comparison_class,
            core_benchmark=is_core_uk_comparison(comparison_class),
            score_witness_rows=tuple(score_witness_rows),
        )

    except Exception as exc:
        bench_exception_observations: tuple[dict[str, Any], ...] = ()
        if not source_parse_observations:
            bench_exception_observations = (_uk_bench_exception_observation(sid, exc),)
        source_parse_rejections = _blocking_source_parse_rows(source_parse_observations)
        # One bad statute must not abort the whole bench run, so we catch broadly
        # here.  But the error must be visible — include the exception type so
        # programming bugs (NameError, TypeError, …) are distinguishable from
        # expected failures (ET.ParseError, FileNotFoundError).
        return _BenchResult(
            statute_id=sid,
            act_type=act_type,
            year=year,
            n_effects=n_effects,
            n_effect_feed_pages=n_effect_feed_pages,
            n_enacted_eids=0,
            n_oracle_eids=0,
            n_common=0,
            score=0.0,
            status="ERR",
            n_effect_rows=n_effect_rows,
            effect_feed_rejection_count=effect_feed_rejection_count,
            effect_feed_rejection_rule_counts=effect_feed_rejection_rule_counts,
            effect_feed_observation_count=effect_feed_observation_count,
            effect_feed_observation_rule_counts=effect_feed_observation_rule_counts,
            effect_feed_observations=effect_feed_observations,
            effect_feed_count_error=effect_feed_count_error,
            bench_exception_count=len(bench_exception_observations),
            bench_exception_rule_counts=_rule_counts(bench_exception_observations),
            bench_exception_observations=bench_exception_observations,
            source_parse_rejection_count=len(source_parse_rejections),
            source_parse_rejection_rule_counts=_rule_counts(source_parse_rejections),
            source_parse_observation_count=len(source_parse_observations),
            source_parse_observation_rule_counts=_rule_counts(source_parse_observations),
            source_parse_observations=tuple(source_parse_observations),
            effect_diagnostics=tuple(effect_diagnostics),
            enacted_source_status=enacted_source_status,
            oracle_source_status=oracle_source_status,
            enacted_source_size=enacted_source_size,
            oracle_source_size=oracle_source_size,
            enacted_source_sha256=enacted_source_sha256,
            oracle_source_sha256=oracle_source_sha256,
            enacted_source_url=enacted_url,
            oracle_source_url=current_url,
            uk_metadata_backfill_enabled=allow_metadata_backfill,
            uk_oracle_alignment_enabled=allow_oracle_alignment,
            uk_applicability_mode=applicability_mode,
            uk_authority_mode=authority_mode,
            uk_metadata_only_effects_enabled=allow_metadata_only_effects,
            **_uk_bench_replay_regime_result_fields(
                enacted_only=not do_replay,
                oracle_alignment_enabled=allow_oracle_alignment,
                metadata_backfill_op_count=0,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_metadata_only_effects=allow_metadata_only_effects,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
            ),
            uk_authority_observation_count=uk_authority_observation_count,
            uk_authority_observation_rule_counts=uk_authority_observation_rule_counts,
            uk_authority_rejection_count=uk_authority_rejection_count,
            uk_authority_rejection_rule_counts=uk_authority_rejection_rule_counts,
            uk_authority_observations=tuple(authority_rejections),
            lowering_observation_count=lowering_observation_count,
            lowering_observation_rule_counts=lowering_observation_rule_counts,
            lowering_rejection_count=lowering_rejection_count,
            lowering_rejection_rule_counts=lowering_rejection_rule_counts,
            blocking_lowering_rejection_count=blocking_lowering_rejection_count,
            blocking_lowering_rejection_rule_counts=blocking_lowering_rejection_rule_counts,
            lowering_rejections=tuple(lowering_rejections),
            effect_source_pathology_counts=effect_source_pathology_counts,
            manual_compile_status_counts=manual_compile_status_counts,
            manual_compile_rule_counts=manual_compile_rule_counts,
            source_acquisition_observation_count=source_acquisition_observation_count,
            source_acquisition_observation_rule_counts=source_acquisition_observation_rule_counts,
            source_acquisition_rejection_count=source_acquisition_rejection_count,
            source_acquisition_rejection_rule_counts=source_acquisition_rejection_rule_counts,
            error=f"{type(exc).__name__}: {exc}"[:200],
            comparison_class="exception",
            core_benchmark=False,
        )


# ---------------------------------------------------------------------------
# Run bench
# ---------------------------------------------------------------------------


def _score_statute_worker(entry: dict) -> _BenchResult:
    """Top-level picklable wrapper for parallel execution.

    Opens its own Farchive per worker process using module-level globals
    set before the ProcessPoolExecutor is spawned.
    """
    try:
        archive = Farchive(_WORKER_DB_PATH)
    except Exception as exc:
        return _bench_exception_result(
            entry,
            exc,
            allow_metadata_backfill=_WORKER_ALLOW_METADATA_BACKFILL,
            allow_oracle_alignment=_WORKER_ALLOW_ORACLE_ALIGNMENT,
            applicability_mode=_WORKER_APPLICABILITY_MODE,
            authority_mode=_WORKER_AUTHORITY_MODE,
            allow_metadata_only_effects=_WORKER_ALLOW_METADATA_ONLY_EFFECTS,
        )
    try:
        return _score_statute(
            entry,
            archive,
            do_replay=_WORKER_DO_REPLAY,
            repo_root=Path(_WORKER_REPO_ROOT) if _WORKER_REPO_ROOT else None,
            do_commencement=_WORKER_DO_COMMENCEMENT,
            allow_metadata_backfill=_WORKER_ALLOW_METADATA_BACKFILL,
            allow_oracle_alignment=_WORKER_ALLOW_ORACLE_ALIGNMENT,
            applicability_mode=_WORKER_APPLICABILITY_MODE,
            authority_mode=_WORKER_AUTHORITY_MODE,
            allow_metadata_only_effects=_WORKER_ALLOW_METADATA_ONLY_EFFECTS,
        )
    finally:
        archive.close()


def _run_bench(
    corpus: list[dict],
    archive: Farchive,
    do_replay: bool = False,
    repo_root: Optional[Path] = None,
    workers: int = 1,
    do_commencement: bool = False,
    allow_metadata_backfill: bool = True,
    allow_oracle_alignment: bool = True,
    applicability_mode: str = "effective_date_plus_feed_applied",
    authority_mode: str = "current_mixed",
    allow_metadata_only_effects: bool = True,
) -> list[_BenchResult]:
    total = len(corpus)
    t0 = time.time()

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        # Communicate config to worker processes via module globals.
        global _WORKER_DB_PATH, _WORKER_DO_REPLAY, _WORKER_REPO_ROOT, _WORKER_DO_COMMENCEMENT
        global _WORKER_ALLOW_METADATA_BACKFILL, _WORKER_ALLOW_ORACLE_ALIGNMENT
        global _WORKER_ALLOW_METADATA_ONLY_EFFECTS
        global _WORKER_APPLICABILITY_MODE, _WORKER_AUTHORITY_MODE
        _WORKER_DB_PATH = str(archive._db_path)
        _WORKER_DO_REPLAY = do_replay
        _WORKER_REPO_ROOT = str(repo_root) if repo_root is not None else ""
        _WORKER_DO_COMMENCEMENT = do_commencement
        _WORKER_ALLOW_METADATA_BACKFILL = allow_metadata_backfill
        _WORKER_ALLOW_ORACLE_ALIGNMENT = allow_oracle_alignment
        _WORKER_APPLICABILITY_MODE = applicability_mode
        _WORKER_AUTHORITY_MODE = authority_mode
        _WORKER_ALLOW_METADATA_ONLY_EFFECTS = allow_metadata_only_effects

        results: list[Optional[_BenchResult]] = [None] * total
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {}
            done = 0
            for idx, entry in enumerate(corpus):
                try:
                    future_to_idx[pool.submit(_score_statute_worker, entry)] = idx
                except Exception as exc:
                    results[idx] = _bench_exception_result(
                        entry,
                        exc,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_oracle_alignment=allow_oracle_alignment,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                        allow_metadata_only_effects=allow_metadata_only_effects,
                    )
                    done += 1
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                try:
                    results[idx] = future.result()
                except Exception as exc:
                    results[idx] = _bench_exception_result(
                        corpus[idx],
                        exc,
                        allow_metadata_backfill=allow_metadata_backfill,
                        allow_oracle_alignment=allow_oracle_alignment,
                        applicability_mode=applicability_mode,
                        authority_mode=authority_mode,
                        allow_metadata_only_effects=allow_metadata_only_effects,
                    )
                done += 1
                if done % 50 == 0 or done == total:
                    elapsed = time.time() - t0
                    ok = sum(1 for x in results if x is not None and x.status == "OK")
                    completed_results = [
                        x for x in results if x is not None and x.status == "OK"
                    ]
                    avg = _average_primary_ok_score(
                        completed_results,
                        has_commencement=do_commencement,
                    )
                    rate = done / elapsed if elapsed > 0 else 0
                    print(
                        f"  [{done}/{total}] {elapsed:.0f}s  {rate:.1f}/s  ok={ok}  avg={avg:.1%}",
                        file=sys.stderr,
                    )
        return cast(List[_BenchResult], results)

    # Sequential fallback.
    results_seq = []
    for i, entry in enumerate(corpus):
        try:
            r = _score_statute(
                entry,
                archive,
                do_replay=do_replay,
                repo_root=repo_root,
                do_commencement=do_commencement,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_oracle_alignment=allow_oracle_alignment,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
                allow_metadata_only_effects=allow_metadata_only_effects,
            )
        except Exception as exc:
            r = _bench_exception_result(
                entry,
                exc,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_oracle_alignment=allow_oracle_alignment,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
                allow_metadata_only_effects=allow_metadata_only_effects,
            )
        results_seq.append(r)

        if (i + 1) % 100 == 0:
            elapsed = time.time() - t0
            ok = sum(1 for x in results_seq if x.status == "OK")
            avg = _average_primary_ok_score(
                results_seq,
                has_commencement=do_commencement,
            )
            print(
                f"  [{i + 1}/{total}] {elapsed:.0f}s  ok={ok}  avg={avg:.1%}",
                file=sys.stderr,
            )

    return results_seq


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _compact_rule_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "none"
    return ",".join(f"{rule_id}:{counts[rule_id]}" for rule_id in sorted(counts))


def _replay_adjudication_bucket_counts(
    kind_counts: dict[str, int] | Counter[str],
) -> Counter[str]:
    bucket_counts: Counter[str] = Counter()
    for kind, count in kind_counts.items():
        bucket_counts[classify_uk_replay_adjudication_bucket(str(kind))] += int(count)
    return bucket_counts


_UK_SOURCE_BACKED_RENUMBER_RULE_IDS = frozenset(
    {
        "uk_effect_metadata_sibling_renumber_lowered",
        "uk_effect_source_text_renumber_destination_corrected",
    }
)


def _uk_address_string_to_compare_eid(address: str) -> str:
    path: list[tuple[str, str]] = []
    for segment in str(address or "").split("/"):
        if not segment or ":" not in segment:
            return ""
        kind, label = segment.split(":", 1)
        kind = kind.strip()
        label = label.strip()
        if not kind:
            return ""
        path.append((kind, label))
    if not path:
        return ""
    return _fallback_target_eid(LegalAddress(path=tuple(path), special=None))


def _source_backed_renumber_residual_kind(
    *,
    only_in_replayed: Set[str],
    only_in_oracle: Set[str],
    lowering_observations: Sequence[dict[str, Any]],
) -> str:
    if not only_in_oracle:
        return ""
    for observation in lowering_observations:
        if str(observation.get("rule_id") or "") not in _UK_SOURCE_BACKED_RENUMBER_RULE_IDS:
            continue
        source_eid = _uk_address_string_to_compare_eid(str(observation.get("source_target") or ""))
        if not source_eid or source_eid not in only_in_oracle:
            continue
        if only_in_replayed:
            return "uk_source_backed_renumber_oracle_branch_mixed_residual_eids"
        return "uk_source_backed_renumber_oracle_only_residual_eids"
    return ""


def _classify_uk_residual_claim_for_bench(
    *,
    comparison_class: str,
    only_in_replayed: Set[str],
    only_in_oracle: Set[str],
    replay_adjudication_kind_counts: dict[str, int],
    lowering_observations: Sequence[dict[str, Any]] = (),
) -> tuple[str, str, int]:
    """Classify replay residuals for bench reporting without changing scoring."""
    if not comparison_class:
        return ("UNRESOLVED", "not_run", 0)
    if not is_core_uk_comparison(comparison_class):
        return ("UNRESOLVED", comparison_class, 0)
    adjudication_kinds = tuple(
        kind for kind, count in replay_adjudication_kind_counts.items() if int(count) > 0
    )
    if only_in_replayed or only_in_oracle or adjudication_kinds:
        tier, kind = classify_uk_replay_residual(
            only_in_replayed=only_in_replayed,
            only_in_oracle=only_in_oracle,
            adjudication_kinds=adjudication_kinds,
        )
        if tier != "PROVED_REPLAY_BUG":
            source_backed_kind = _source_backed_renumber_residual_kind(
                only_in_replayed=only_in_replayed,
                only_in_oracle=only_in_oracle,
                lowering_observations=lowering_observations,
            )
            if source_backed_kind:
                return ("UNRESOLVED", source_backed_kind, 0)
        return (tier, kind, 1 if tier == "PROVED_REPLAY_BUG" else 0)
    return ("UNRESOLVED", "no_strong_claim", 0)


_UK_REPLAY_TEXT_PATCH_PREIMAGE_FRONTIER_KINDS = frozenset(
    {
        "uk_replay_heading_text_preimage_gap",
        "uk_replay_text_insert_anchor_preimage_gap",
        "uk_replay_text_monetary_amount_preimage_gap",
        "uk_replay_text_parenthetical_omission_preimage_gap",
        "uk_replay_text_patch_preimage_drift",
        "uk_replay_text_patch_preimage_drift_multi_prior_same_target",
    }
)
_UK_MANUAL_TEXT_PATCH_PREIMAGE_CHAIN_GAP_RULE_ID = (
    "uk_manual_frontier_text_patch_preimage_chain_gap"
)
_UK_MANUAL_TEXT_PATCH_PREIMAGE_CHAIN_GAP_REASON = (
    "Replay proved that the compiled text patch's quoted preimage is absent at "
    "apply time; acquire or prove the missing intermediate source chain before "
    "treating this row as deterministic frontend support."
)


def _apply_replay_preimage_frontier_to_effect_diagnostics(
    effect_diagnostics: list[dict[str, Any]],
    replay_adjudication_records: Iterable[dict[str, Any]],
) -> None:
    """Align bench manual-frontier diagnostics with replay preimage evidence.

    Compile-time manual-frontier classification cannot see the live replay
    state. If replay later proves an exact text-patch preimage gap for the
    same effect/op ID, the row is not deterministic support; it is a visible
    source-chain frontier.
    """
    preimage_gap_by_op_id = {
        str(record.get("op_id") or ""): str(record.get("kind") or "")
        for record in replay_adjudication_records
        if str(record.get("kind") or "") in _UK_REPLAY_TEXT_PATCH_PREIMAGE_FRONTIER_KINDS
        and str(record.get("op_id") or "")
    }
    if not preimage_gap_by_op_id:
        return
    for row in effect_diagnostics:
        if row.get("rule_id") != "uk_manual_compile_frontier_classified":
            continue
        effect_id = str(row.get("effect_id") or "")
        replay_kind = preimage_gap_by_op_id.get(effect_id)
        if not replay_kind:
            continue
        row["manual_compile_status"] = "source_insufficient"
        row["manual_compile_rule_id"] = _UK_MANUAL_TEXT_PATCH_PREIMAGE_CHAIN_GAP_RULE_ID
        row["manual_compile_reason"] = _UK_MANUAL_TEXT_PATCH_PREIMAGE_CHAIN_GAP_REASON
        row["replay_adjudication_kind"] = replay_kind


def _bench_row_evidence_context(result: _BenchResult) -> str:
    regime = (
        f"metadata_backfill={int(result.uk_metadata_backfill_enabled)}"
        f";oracle_alignment={int(result.uk_oracle_alignment_enabled)}"
        f";metadata_only_effects={int(result.uk_metadata_only_effects_enabled)}"
        f";applicability={result.uk_applicability_mode}"
        f";authority={result.uk_authority_mode}"
    )
    source_hashes = ""
    if result.enacted_source_sha256 or result.oracle_source_sha256:
        source_hashes = (
            f"source_hashes=enacted:{result.enacted_source_sha256 or '(none)'}"
            f"/oracle:{result.oracle_source_sha256 or '(none)'} "
        )
    return (
        f"status={result.status} "
        f"class={result.comparison_class or 'unknown'} "
        f"sources=enacted:{result.enacted_source_status}/oracle:{result.oracle_source_status} "
        f"source_sizes=enacted:{result.enacted_source_size}/oracle:{result.oracle_source_size} "
        f"source_urls=enacted:{result.enacted_source_url or '(none)'}"
        f"/oracle:{result.oracle_source_url or '(none)'} "
        f"{source_hashes}"
        f"regime={regime} "
        f"source_purity={result.uk_source_purity_lane or 'unknown'} "
        f"source_clean={int(result.uk_source_semantics_clean)} "
        f"source_first={int(result.uk_source_first_candidate)} "
        f"source_first_reasons={_compact_rule_counts(dict(Counter(result.uk_source_first_candidate_reasons)))} "
        f"ops={result.n_ops} "
        f"source_parse_observations={result.source_parse_observation_count} "
        f"source_parse_rejections={result.source_parse_rejection_count} "
        f"source_pathologies={_compact_rule_counts(result.effect_source_pathology_counts)} "
        f"manual_frontier={_compact_rule_counts(result.manual_compile_status_counts)} "
        f"source_acquisition_observations={result.source_acquisition_observation_count} "
        f"source_acquisition_rejections={result.source_acquisition_rejection_count} "
        f"bench_exceptions={result.bench_exception_count} "
        f"feed_observations={result.effect_feed_observation_count} "
        f"feed_rejections={result.effect_feed_rejection_count} "
        f"feed_count_error={int(bool(result.effect_feed_count_error))} "
        f"authority_observations={result.uk_authority_observation_count} "
        f"authority_blocking_rejections={result.uk_authority_rejection_count} "
        f"lowering_observations={result.lowering_observation_count} "
        f"lowering_rejections={result.lowering_rejection_count} "
        f"blocking_lowering={result.blocking_lowering_rejection_count} "
        f"residual_claim={result.uk_residual_claim_tier}/{result.uk_residual_claim_kind} "
        f"residual_comparison={result.uk_residual_claim_comparison_class or 'unknown'} "
        f"residual_sides=replayed:{result.uk_residual_only_in_replayed_count}"
        f"/oracle:{result.uk_residual_only_in_oracle_count} "
        f"residual_section_claims={result.uk_residual_section_claim_count} "
        f"adjudication_buckets={_compact_rule_counts(dict(_replay_adjudication_bucket_counts(result.replay_adjudication_kind_counts)))} "
        f"adjudications={result.replay_adjudication_count}"
    )


def _short_sample_value(value: object, *, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _print_replay_adjudication_samples(
    results: Sequence[_BenchResult],
    *,
    kinds: Sequence[str],
    limit: int,
) -> None:
    requested = {str(kind or "").strip() for kind in kinds if str(kind or "").strip()}
    if not requested or limit <= 0:
        return
    samples_by_kind: dict[str, list[tuple[_BenchResult, dict[str, Any]]]] = {
        kind: [] for kind in sorted(requested)
    }
    totals: Counter[str] = Counter()
    for result in results:
        for adjudication in result.replay_adjudications:
            kind = str(adjudication.get("kind") or "")
            if kind not in requested:
                continue
            totals[kind] += 1
            if len(samples_by_kind[kind]) < limit:
                samples_by_kind[kind].append((result, adjudication))
    if not totals:
        print("  Replay adjudication samples: none for requested kinds")
        return
    print("  Replay adjudication samples:")
    for kind in sorted(requested):
        total = totals.get(kind, 0)
        if not total:
            continue
        samples = samples_by_kind[kind]
        omitted = max(0, total - len(samples))
        print(f"    {kind}: shown={len(samples)} total={total} omitted={omitted}")
        for result, adjudication in samples:
            detail = adjudication.get("detail")
            detail_map = detail if isinstance(detail, dict) else {}
            parts = [
                result.statute_id,
                f"source={adjudication.get('source_statute') or ''}",
                f"op={adjudication.get('op_id') or ''}",
            ]
            target = detail_map.get("target")
            if target:
                parts.append(f"target={target}")
            text_match = detail_map.get("text_match")
            if text_match:
                parts.append(f"text_match={_short_sample_value(text_match)}")
            replacement = detail_map.get("replacement_text")
            if replacement:
                parts.append(f"replacement={_short_sample_value(replacement)}")
            source_shape = detail_map.get("source_shape")
            if source_shape:
                parts.append(f"source_shape={source_shape}")
            print("      " + " ".join(parts))


def _print_report(
    results: list[_BenchResult],
    label: str,
    *,
    replay_adjudication_sample_kinds: Sequence[str] = (),
    replay_adjudication_sample_limit: int = 5,
) -> None:
    def _source_line(r: _BenchResult) -> str:
        source_hashes = ""
        if r.enacted_source_sha256 or r.oracle_source_sha256:
            source_hashes = (
                f" hashes=enacted:{r.enacted_source_sha256 or '(none)'}"
                f" oracle:{r.oracle_source_sha256 or '(none)'}"
            )
        return (
            f"    sources: enacted={r.enacted_source_status} ({r.enacted_source_size} bytes) "
            f"url={r.enacted_source_url or '(none)'} "
            f"oracle={r.oracle_source_status} ({r.oracle_source_size} bytes) "
            f"url={r.oracle_source_url or '(none)'}"
            f"{source_hashes}"
        )

    ok = [r for r in results if r.status == "OK" and r.n_oracle_eids > 0]
    core = [r for r in ok if r.core_benchmark]
    noncore = [r for r in ok if not r.core_benchmark]
    no_oracle = [r for r in results if r.status in ("NO_ORACLE", "NO_ENACTED")]
    errs = [r for r in results if r.status == "ERR"]
    replayed = [r for r in ok if r.replay_score >= 0.0]
    all_rows_are_replayed = len(replayed) == len(results)

    row_status_counts = Counter(r.status for r in results)
    comparison_class_counts = Counter(r.comparison_class or "unknown" for r in results)
    print(f"\n=== UK Bench: {label} ===")
    print(
        f"Total: {len(results)}, Scored OK: {len(ok)}, "
        f"Source-unavailable: {len(no_oracle)}, Errors: {len(errs)}"
    )
    if row_status_counts.get("OK", 0) != len(ok):
        print(f"Status OK rows: {row_status_counts.get('OK', 0)}")
    print(f"Row statuses: {dict(sorted(row_status_counts.items()))}")
    print(f"Comparison classes: {dict(sorted(comparison_class_counts.items()))}")
    print(f"Core benchmark rows: {sum(1 for r in results if r.core_benchmark)}")
    if replayed:
        print(f"Score mode: enacted baseline + replay ({len(replayed)} replayed rows)")
    else:
        print("Score mode: enacted baseline only (pass --replay for amendment replay)")
    enacted_source_counts = Counter(r.enacted_source_status for r in results)
    oracle_source_counts = Counter(r.oracle_source_status for r in results)
    source_parse_observation_rows = sum(1 for r in results if r.source_parse_observation_count > 0)
    source_parse_observations_total = sum(r.source_parse_observation_count for r in results)
    source_parse_observation_rule_counts: Counter[str] = Counter()
    source_parse_rejection_rows = sum(1 for r in results if r.source_parse_rejection_count > 0)
    source_parse_rejections_total = sum(r.source_parse_rejection_count for r in results)
    source_parse_rejection_rule_counts: Counter[str] = Counter()
    bench_exception_rows = sum(1 for r in results if r.bench_exception_count > 0)
    bench_exceptions_total = sum(r.bench_exception_count for r in results)
    bench_exception_rule_counts: Counter[str] = Counter()
    effect_feed_observation_rows = sum(1 for r in results if r.effect_feed_observation_count > 0)
    effect_feed_observations_total = sum(r.effect_feed_observation_count for r in results)
    effect_feed_observation_rule_counts: Counter[str] = Counter()
    effect_feed_rejection_rows = sum(1 for r in results if r.effect_feed_rejection_count > 0)
    effect_feed_rejections_total = sum(r.effect_feed_rejection_count for r in results)
    effect_feed_rejection_rule_counts: Counter[str] = Counter()
    authority_rejection_total = sum(r.uk_authority_rejection_count for r in results)
    authority_rejection_rule_counts: Counter[str] = Counter()
    authority_observation_total = sum(r.uk_authority_observation_count for r in results)
    authority_observation_rule_counts: Counter[str] = Counter()
    replay_adjudication_total = sum(r.replay_adjudication_count for r in results)
    replay_adjudication_kind_counts: Counter[str] = Counter()
    replay_adjudication_bucket_counts: Counter[str] = Counter()
    effect_source_pathology_counts: Counter[str] = Counter()
    manual_compile_status_counts: Counter[str] = Counter()
    manual_compile_rule_counts: Counter[str] = Counter()
    source_acquisition_observation_total = sum(
        r.source_acquisition_observation_count for r in results
    )
    source_acquisition_observation_rule_counts: Counter[str] = Counter()
    source_acquisition_rejection_total = sum(r.source_acquisition_rejection_count for r in results)
    source_acquisition_rejection_rule_counts: Counter[str] = Counter()
    lowering_observation_total = sum(r.lowering_observation_count for r in results)
    lowering_rejection_total = sum(r.lowering_rejection_count for r in results)
    blocking_lowering_rejection_total = sum(r.blocking_lowering_rejection_count for r in results)
    lowering_observation_rule_counts: Counter[str] = Counter()
    lowering_rejection_rule_counts: Counter[str] = Counter()
    blocking_lowering_rejection_rule_counts: Counter[str] = Counter()
    effect_feed_count_error_rows = [r for r in results if r.effect_feed_count_error]
    for r in results:
        source_parse_rejection_rule_counts.update(r.source_parse_rejection_rule_counts)
        bench_exception_rule_counts.update(r.bench_exception_rule_counts)
        effect_feed_observation_rule_counts.update(r.effect_feed_observation_rule_counts)
        effect_feed_rejection_rule_counts.update(r.effect_feed_rejection_rule_counts)
        authority_observation_rule_counts.update(r.uk_authority_observation_rule_counts)
        authority_rejection_rule_counts.update(r.uk_authority_rejection_rule_counts)
        replay_adjudication_kind_counts.update(r.replay_adjudication_kind_counts)
        replay_adjudication_bucket_counts.update(
            _replay_adjudication_bucket_counts(r.replay_adjudication_kind_counts)
        )
        effect_source_pathology_counts.update(r.effect_source_pathology_counts)
        manual_compile_status_counts.update(r.manual_compile_status_counts)
        manual_compile_rule_counts.update(r.manual_compile_rule_counts)
        source_acquisition_observation_rule_counts.update(
            r.source_acquisition_observation_rule_counts
        )
        source_acquisition_rejection_rule_counts.update(r.source_acquisition_rejection_rule_counts)
        lowering_observation_rule_counts.update(r.lowering_observation_rule_counts)
        lowering_rejection_rule_counts.update(r.lowering_rejection_rule_counts)
        blocking_lowering_rejection_rule_counts.update(r.blocking_lowering_rejection_rule_counts)
    print(
        "Source status: "
        f"enacted={dict(sorted(enacted_source_counts.items()))} "
        f"oracle={dict(sorted(oracle_source_counts.items()))}"
    )
    if source_parse_observation_rows:
        print(
            f"Source parse observations: rows={source_parse_observation_rows} "
            f"total={source_parse_observations_total}"
        )
        for r in results:
            source_parse_observation_rule_counts.update(r.source_parse_observation_rule_counts)
        if source_parse_observation_rule_counts:
            print(
                "Source parse observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(source_parse_observation_rule_counts.items())
                )
            )
    if source_parse_rejection_rows:
        print(
            f"Source parse blocking rejections: rows={source_parse_rejection_rows} "
            f"total={source_parse_rejections_total}"
        )
        if source_parse_rejection_rule_counts:
            print(
                "Source parse rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(source_parse_rejection_rule_counts.items())
                )
            )
    if bench_exception_rows:
        print(
            f"Bench exceptions: rows={bench_exception_rows} "
            f"total={bench_exceptions_total}"
        )
        if bench_exception_rule_counts:
            print(
                "Bench exception rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(bench_exception_rule_counts.items())
                )
            )
    if effect_feed_count_error_rows:
        print(f"Effect-feed count errors: rows={len(effect_feed_count_error_rows)}")
        for r in effect_feed_count_error_rows[:10]:
            print(f"  {r.statute_id}: {r.effect_feed_count_error}")
    if effect_feed_observation_rows:
        print(
            f"Effect-feed observations: rows={effect_feed_observation_rows} "
            f"total={effect_feed_observations_total}"
        )
        if effect_feed_observation_rule_counts:
            print(
                "Effect-feed observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(effect_feed_observation_rule_counts.items())
                )
            )
    if effect_feed_rejection_rows:
        print(
            f"Effect-feed blocking rejections: rows={effect_feed_rejection_rows} "
            f"total={effect_feed_rejections_total}"
        )
        if effect_feed_rejection_rule_counts:
            print(
                "Effect-feed rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(effect_feed_rejection_rule_counts.items())
                )
            )
    if authority_observation_total and not all_rows_are_replayed:
        print(f"All-row authority observations: {authority_observation_total}")
        if authority_observation_rule_counts:
            print(
                "All-row authority observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(authority_observation_rule_counts.items())
                )
            )
    if authority_rejection_total and not all_rows_are_replayed:
        print(f"All-row blocking authority rejections: {authority_rejection_total}")
        if authority_rejection_rule_counts:
            print(
                "All-row blocking authority rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(authority_rejection_rule_counts.items())
                )
            )
    if replay_adjudication_total and not all_rows_are_replayed:
        print(f"All-row replay adjudications: {replay_adjudication_total}")
        if replay_adjudication_kind_counts:
            print(
                "All-row replay adjudication kinds: "
                + ", ".join(
                    f"{kind}={count}"
                    for kind, count in sorted(replay_adjudication_kind_counts.items())
                )
            )
        if replay_adjudication_bucket_counts:
            print(
                "All-row replay adjudication buckets: "
                + ", ".join(
                    f"{bucket}={count}"
                    for bucket, count in sorted(replay_adjudication_bucket_counts.items())
                )
            )
    if effect_source_pathology_counts and not all_rows_are_replayed:
        print(
            "All-row effect source pathologies: "
            + ", ".join(
                f"{pathology}={count}"
                for pathology, count in sorted(effect_source_pathology_counts.items())
            )
        )
    if manual_compile_status_counts and not all_rows_are_replayed:
        print(
            "All-row manual compile frontier statuses: "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(manual_compile_status_counts.items())
            )
        )
        if manual_compile_rule_counts:
            print(
                "All-row manual compile frontier rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(manual_compile_rule_counts.items())
                )
            )
    if source_acquisition_observation_total and not all_rows_are_replayed:
        print(f"All-row source acquisition observations: {source_acquisition_observation_total}")
        if source_acquisition_observation_rule_counts:
            print(
                "All-row source acquisition observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(source_acquisition_observation_rule_counts.items())
                )
            )
    if source_acquisition_rejection_total and not all_rows_are_replayed:
        print(f"All-row source acquisition rejections: {source_acquisition_rejection_total}")
        if source_acquisition_rejection_rule_counts:
            print(
                "All-row source acquisition rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(source_acquisition_rejection_rule_counts.items())
                )
            )
    if lowering_observation_total and not all_rows_are_replayed:
        print(f"All-row lowering observations: {lowering_observation_total}")
        if lowering_observation_rule_counts:
            print(
                "All-row lowering observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(lowering_observation_rule_counts.items())
                )
            )
    if lowering_rejection_total and not all_rows_are_replayed:
        print(
            "All-row lowering rejections: "
            f"total={lowering_rejection_total} "
            f"blocking={blocking_lowering_rejection_total}"
        )
        if lowering_rejection_rule_counts:
            print(
                "All-row lowering rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(lowering_rejection_rule_counts.items())
                )
            )
        if blocking_lowering_rejection_rule_counts:
            print(
                "All-row blocking lowering rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(blocking_lowering_rejection_rule_counts.items())
                )
            )
    if no_oracle:
        print(f"Source unavailable rows ({len(no_oracle)}):")
        for r in no_oracle[:10]:
            print(
                f"  {r.statute_id}: status={r.status} "
                f"enacted={r.enacted_source_status} ({r.enacted_source_size} bytes) "
                f"oracle={r.oracle_source_status} ({r.oracle_source_size} bytes)"
            )
            if r.enacted_source_url or r.oracle_source_url:
                print(
                    f"    sources: enacted={r.enacted_source_url or '(none)'} "
                    f"oracle={r.oracle_source_url or '(none)'}"
                    + (
                        f" hashes=enacted:{r.enacted_source_sha256 or '(none)'}"
                        f" oracle:{r.oracle_source_sha256 or '(none)'}"
                        if r.enacted_source_sha256 or r.oracle_source_sha256
                        else ""
                    )
                )
    if errs:
        print(f"Error rows ({len(errs)}):")
        for r in errs[:10]:
            print(
                f"  {r.statute_id}: {r.error} "
                f"enacted={r.enacted_source_status} ({r.enacted_source_size} bytes) "
                f"oracle={r.oracle_source_status} ({r.oracle_source_size} bytes)"
            )
            if r.bench_exception_rule_counts:
                rules = ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(r.bench_exception_rule_counts.items())
                )
                print(f"    bench_exception_rules: {rules}")
            if r.enacted_source_url or r.oracle_source_url:
                print(
                    f"    sources: enacted={r.enacted_source_url or '(none)'} "
                    f"oracle={r.oracle_source_url or '(none)'}"
                    + (
                        f" hashes=enacted:{r.enacted_source_sha256 or '(none)'}"
                        f" oracle:{r.oracle_source_sha256 or '(none)'}"
                        if r.enacted_source_sha256 or r.oracle_source_sha256
                        else ""
                    )
                )

    if not ok:
        print("No valid results to report.")
        return

    # Determine whether commencement scores are available — use as primary when yes.
    comm_scored = [r for r in ok if r.commencement_score >= 0.0]
    has_commencement = bool(comm_scored)

    avg_raw = sum(r.score for r in ok) / len(ok)
    med_score_raw = sorted(r.score for r in ok)[len(ok) // 2]
    perfect_raw = sum(1 for r in ok if r.score == 1.0)
    ge90_raw = sum(1 for r in ok if r.score >= 0.9)
    ge80_raw = sum(1 for r in ok if r.score >= 0.8)
    with_effect_pages = sum(1 for r in ok if (r.n_effect_feed_pages or r.n_effects) > 0)
    with_effect_rows = sum(1 for r in ok if r.n_effect_rows > 0)
    if core:
        core_avg_raw = sum(r.score for r in core) / len(core)
        print(f"Core raw avg: {core_avg_raw:.1%}")
    if noncore:
        counts = Counter(r.comparison_class for r in noncore)
        print("Non-core classes: " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    if has_commencement:
        # Commencement scores are primary; raw scores shown as secondary.
        avg_comm = sum(r.commencement_score for r in comm_scored) / len(comm_scored)
        med_comm = sorted(r.commencement_score for r in comm_scored)[len(comm_scored) // 2]
        perfect_comm = sum(1 for r in comm_scored if r.commencement_score == 1.0)
        ge90_comm = sum(1 for r in comm_scored if r.commencement_score >= 0.9)
        ge80_comm = sum(1 for r in comm_scored if r.commencement_score >= 0.8)
        avg_commenced_n = sum(r.n_commenced_eids for r in comm_scored) / len(comm_scored)
        print(f"\nEID score (commenced, N={len(comm_scored)}):")
        print(f"  Average:        {avg_comm:.1%}    (unfiltered: {avg_raw:.1%})")
        print(f"  Median:         {med_comm:.1%}    (unfiltered: {med_score_raw:.1%})")
        print(
            f"  Perfect (1.0):  {perfect_comm} ({100 * perfect_comm / len(comm_scored):.0f}%)"
            f"    (unfiltered: {perfect_raw})"
        )
        print(f"  >=90%:          {ge90_comm} ({100 * ge90_comm / len(comm_scored):.0f}%)    (unfiltered: {ge90_raw})")
        print(f"  >=80%:          {ge80_comm} ({100 * ge80_comm / len(comm_scored):.0f}%)    (unfiltered: {ge80_raw})")
        print(f"  Avg commenced EIDs: {avg_commenced_n:.0f}")
        print(f"  With parsed effect rows>0: {with_effect_rows}")
        print(f"  With effect-feed pages>0: {with_effect_pages}")
        if core:
            core_comm = [r for r in core if r.commencement_score >= 0.0]
            if core_comm:
                avg_core_comm = sum(r.commencement_score for r in core_comm) / len(core_comm)
                print(f"  Core commenced avg: {avg_core_comm:.1%}")
    else:
        # No commencement data — show raw scores normally.
        print(f"\nEID similarity score (N={len(ok)}):")
        print(f"  Average:        {avg_raw:.1%}")
        print(f"  Median:         {med_score_raw:.1%}")
        print(f"  Perfect (1.0):  {perfect_raw} ({100 * perfect_raw / len(ok):.0f}%)")
        print(f"  >=90%:          {ge90_raw} ({100 * ge90_raw / len(ok):.0f}%)")
        print(f"  >=80%:          {ge80_raw} ({100 * ge80_raw / len(ok):.0f}%)")
        print(f"  With parsed effect rows>0: {with_effect_rows}")
        print(f"  With effect-feed pages>0: {with_effect_pages}")

    # Replay summary (only when --replay was active)
    if replayed:
        regime_counts = Counter(
            (
                r.uk_metadata_backfill_enabled,
                r.uk_oracle_alignment_enabled,
                r.uk_metadata_only_effects_enabled,
                r.uk_applicability_mode,
                r.uk_authority_mode,
            )
            for r in replayed
        )
        avg_replay_raw = sum(r.replay_score for r in replayed) / len(replayed)
        avg_enacted_raw = sum(r.score for r in replayed) / len(replayed)
        perfect_replay_raw = sum(1 for r in replayed if r.replay_score == 1.0)
        core_replayed = [r for r in replayed if r.core_benchmark]
        total_ops = sum(r.n_ops for r in replayed if r.n_ops >= 0)
        total_replay_adjudications = sum(r.replay_adjudication_count for r in replayed)
        total_alignment_changes = sum(r.oracle_alignment_changed_count for r in replayed)
        total_alignment_oracle_assigned = sum(r.oracle_alignment_oracle_assigned_count for r in replayed)
        total_alignment_local_fallback = sum(r.oracle_alignment_local_fallback_count for r in replayed)
        total_alignment_transparent_wrapper_cleared = sum(
            r.oracle_alignment_transparent_wrapper_cleared_count for r in replayed
        )
        total_alignment_before_nodes = sum(r.oracle_alignment_before_node_count for r in replayed)
        total_alignment_after_nodes = sum(r.oracle_alignment_after_node_count for r in replayed)
        alignment_node_count_mismatch_rows = sum(
            1 for r in replayed if r.oracle_alignment_node_count_mismatch
        )
        alignment_match_method_counts: Counter[str] = Counter()
        for r in replayed:
            alignment_match_method_counts.update(r.oracle_alignment_match_method_counts)
        total_authority_rejections = sum(r.uk_authority_rejection_count for r in replayed)
        total_authority_observations = sum(r.uk_authority_observation_count for r in replayed)
        replay_effect_source_pathology_counts: Counter[str] = Counter()
        replay_manual_compile_status_counts: Counter[str] = Counter()
        replay_manual_compile_rule_counts: Counter[str] = Counter()
        total_source_acquisition_observations = sum(
            r.source_acquisition_observation_count for r in replayed
        )
        source_acquisition_observation_rule_counts: Counter[str] = Counter()
        total_source_acquisition_rejections = sum(r.source_acquisition_rejection_count for r in replayed)
        source_acquisition_rejection_rule_counts: Counter[str] = Counter()
        total_lowering_observations = sum(r.lowering_observation_count for r in replayed)
        total_lowering_rejections = sum(r.lowering_rejection_count for r in replayed)
        total_blocking_lowering_rejections = sum(r.blocking_lowering_rejection_count for r in replayed)
        authority_rejection_rule_counts: Counter[str] = Counter()
        authority_observation_rule_counts: Counter[str] = Counter()
        replay_adjudication_kind_counts: Counter[str] = Counter()
        replay_adjudication_bucket_counts: Counter[str] = Counter()
        lowering_observation_rule_counts: Counter[str] = Counter()
        lowering_rejection_rule_counts: Counter[str] = Counter()
        blocking_lowering_rejection_rule_counts: Counter[str] = Counter()
        for r in replayed:
            replay_adjudication_kind_counts.update(r.replay_adjudication_kind_counts)
            replay_adjudication_bucket_counts.update(
                _replay_adjudication_bucket_counts(r.replay_adjudication_kind_counts)
            )
            authority_observation_rule_counts.update(r.uk_authority_observation_rule_counts)
            authority_rejection_rule_counts.update(r.uk_authority_rejection_rule_counts)
            replay_effect_source_pathology_counts.update(r.effect_source_pathology_counts)
            replay_manual_compile_status_counts.update(r.manual_compile_status_counts)
            replay_manual_compile_rule_counts.update(r.manual_compile_rule_counts)
            source_acquisition_observation_rule_counts.update(
                r.source_acquisition_observation_rule_counts
            )
            source_acquisition_rejection_rule_counts.update(r.source_acquisition_rejection_rule_counts)
            lowering_observation_rule_counts.update(r.lowering_observation_rule_counts)
            lowering_rejection_rule_counts.update(r.lowering_rejection_rule_counts)
            blocking_lowering_rejection_rule_counts.update(r.blocking_lowering_rejection_rule_counts)
        if len(regime_counts) == 1:
            (
                metadata_backfill,
                oracle_alignment,
                metadata_only_effects,
                applicability_mode,
                authority_mode,
            ), _count = next(iter(regime_counts.items()))
            print(
                "\nReplay regime: "
                f"metadata_backfill={metadata_backfill} "
                f"oracle_alignment={oracle_alignment} "
                f"metadata_only_effects={metadata_only_effects} "
                f"applicability={applicability_mode} "
                f"authority={authority_mode}"
            )
        else:
            print("\nReplay regimes:")
            for (
                metadata_backfill,
                oracle_alignment,
                metadata_only_effects,
                applicability_mode,
                authority_mode,
            ), count in sorted(regime_counts.items(), key=lambda item: (-item[1], item[0])):
                print(
                    f"  N={count}: metadata_backfill={metadata_backfill} "
                    f"oracle_alignment={oracle_alignment} "
                    f"metadata_only_effects={metadata_only_effects} "
                    f"applicability={applicability_mode} "
                    f"authority={authority_mode}"
                )
        if total_authority_observations:
            print(f"  Authority observations: {total_authority_observations}")
            if authority_observation_rule_counts:
                print(
                    "  Authority observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(authority_observation_rule_counts.items())
                    )
                )
        if total_authority_rejections:
            print(f"  Blocking authority rejections: {total_authority_rejections}")
            if authority_rejection_rule_counts:
                print(
                    "  Blocking authority rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(authority_rejection_rule_counts.items())
                    )
                )
        if total_replay_adjudications:
            print(f"  Replay adjudications: {total_replay_adjudications}")
            if replay_adjudication_kind_counts:
                print(
                    "  Replay adjudication kinds: "
                    + ", ".join(
                        f"{kind}={count}"
                        for kind, count in sorted(replay_adjudication_kind_counts.items())
                    )
                )
            if replay_adjudication_bucket_counts:
                print(
                    "  Replay adjudication buckets: "
                    + ", ".join(
                        f"{bucket}={count}"
                        for bucket, count in sorted(replay_adjudication_bucket_counts.items())
                    )
                )
            _print_replay_adjudication_samples(
                replayed,
                kinds=replay_adjudication_sample_kinds,
                limit=replay_adjudication_sample_limit,
            )
        if replay_effect_source_pathology_counts:
            print(
                "  Effect source pathologies: "
                + ", ".join(
                    f"{pathology}={count}"
                    for pathology, count in sorted(replay_effect_source_pathology_counts.items())
                )
            )
        if replay_manual_compile_status_counts:
            print(
                "  Manual compile frontier statuses: "
                + ", ".join(
                    f"{status}={count}"
                    for status, count in sorted(replay_manual_compile_status_counts.items())
                )
            )
            if replay_manual_compile_rule_counts:
                print(
                    "  Manual compile frontier rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(replay_manual_compile_rule_counts.items())
                    )
                )
        if total_source_acquisition_observations:
            print(f"  Source acquisition observations: {total_source_acquisition_observations}")
            if source_acquisition_observation_rule_counts:
                print(
                    "  Source acquisition observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(source_acquisition_observation_rule_counts.items())
                    )
                )
        if total_source_acquisition_rejections:
            print(f"  Source acquisition rejections: {total_source_acquisition_rejections}")
            if source_acquisition_rejection_rule_counts:
                print(
                    "  Source acquisition rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(source_acquisition_rejection_rule_counts.items())
                    )
                )
        if total_lowering_observations:
            print(f"  Lowering observations: {total_lowering_observations}")
            if lowering_observation_rule_counts:
                print(
                    "  Lowering observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(lowering_observation_rule_counts.items())
                    )
                )
        if total_lowering_rejections:
            print(
                "  Lowering rejections: "
                f"total={total_lowering_rejections} "
                f"blocking={total_blocking_lowering_rejections}"
            )
            if lowering_rejection_rule_counts:
                print(
                    "  Lowering rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(lowering_rejection_rule_counts.items())
                    )
                )
            if blocking_lowering_rejection_rule_counts:
                print(
                    "  Blocking lowering rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(blocking_lowering_rejection_rule_counts.items())
                    )
                )

        if has_commencement:
            replay_comm_scored = [r for r in replayed if r.replay_commencement_score >= 0.0]
            if replay_comm_scored:
                avg_replay_comm = sum(r.replay_commencement_score for r in replay_comm_scored) / len(replay_comm_scored)
                avg_enacted_comm = sum(
                    r.commencement_score for r in replay_comm_scored if r.commencement_score >= 0.0
                ) / max(sum(1 for r in replay_comm_scored if r.commencement_score >= 0.0), 1)
                delta_comm = avg_replay_comm - avg_enacted_comm
                # Use commencement scores for improved/regressed counts and delta ranking.
                # Previously r.replay_score (raw) was compared against r.score (commencement),
                # producing phantom regressions for recently-enacted 0-ops statutes where the
                # raw replay score is low (many enacted EIDs not in oracle) but the commencement
                # score is fine (only commenced EIDs compared, which match well).
                improved_comm = sum(
                    1
                    for r in replay_comm_scored
                    if r.commencement_score >= 0.0 and r.replay_commencement_score > r.commencement_score + 0.001
                )
                regressed_comm = sum(
                    1
                    for r in replay_comm_scored
                    if r.commencement_score >= 0.0 and r.replay_commencement_score < r.commencement_score - 0.001
                )
                perfect_replay_comm = sum(1 for r in replay_comm_scored if r.replay_commencement_score == 1.0)
                print(f"\nReplay (commenced, N={len(replay_comm_scored)}, {total_ops} ops total):")
                print(f"  Enacted avg:    {avg_enacted_comm:.1%}    (unfiltered: {avg_enacted_raw:.1%})")
                print(
                    f"  Replayed avg:   {avg_replay_comm:.1%} ({delta_comm:+.1%})    (unfiltered: {avg_replay_raw:.1%})"
                )
                print(
                    f"  Perfect replay: {perfect_replay_comm} ({100 * perfect_replay_comm / len(replay_comm_scored):.0f}%)"
                )
                print(f"  Improved:       {improved_comm}  Regressed: {regressed_comm}")
                if total_alignment_changes:
                    print(
                        f"  Oracle EID alignment: changed={total_alignment_changes} "
                        f"oracle_assigned={total_alignment_oracle_assigned} "
                        f"local_fallback={total_alignment_local_fallback} "
                        f"transparent_wrapper_cleared={total_alignment_transparent_wrapper_cleared} "
                        f"before_nodes={total_alignment_before_nodes} "
                        f"after_nodes={total_alignment_after_nodes} "
                        f"node_count_mismatch_rows={alignment_node_count_mismatch_rows}"
                    )
                    if alignment_match_method_counts:
                        print(
                            "  Oracle EID alignment methods: "
                            + ", ".join(
                                f"{method}={count}"
                                for method, count in sorted(alignment_match_method_counts.items())
                            )
                        )
                # Show biggest improvements and regressions by commencement score delta.
                by_delta = sorted(
                    replay_comm_scored,
                    key=lambda r: r.replay_commencement_score - r.commencement_score
                    if r.commencement_score >= 0.0
                    else 0.0,
                )
                if regressed_comm:
                    print("\n  Top regressions:")
                    for r in by_delta[:5]:
                        if r.commencement_score >= 0.0 and r.replay_commencement_score < r.commencement_score - 0.001:
                            print(
                                f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                                f"  {_bench_row_evidence_context(r)}"
                            )
                if improved_comm:
                    print("\n  Top improvements:")
                    for r in reversed(by_delta[-5:]):
                        if r.commencement_score >= 0.0 and r.replay_commencement_score > r.commencement_score + 0.001:
                            print(
                                f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                                f"  {_bench_row_evidence_context(r)}"
                            )
        else:
            improved = sum(1 for r in replayed if r.replay_score > r.score + 0.001)
            regressed = sum(1 for r in replayed if r.replay_score < r.score - 0.001)
            delta = avg_replay_raw - avg_enacted_raw
            print(f"\nReplay score (N={len(replayed)}, {total_ops} ops total):")
            print(f"  Enacted avg:    {avg_enacted_raw:.1%}")
            print(f"  Replayed avg:   {avg_replay_raw:.1%} ({delta:+.1%})")
            if core_replayed and len(core_replayed) != len(replayed):
                core_enacted_raw = sum(r.score for r in core_replayed) / len(core_replayed)
                core_replay_raw = sum(r.replay_score for r in core_replayed) / len(core_replayed)
                print(
                    f"  Core replay avg: {core_replay_raw:.1%} "
                    f"({core_replay_raw - core_enacted_raw:+.1%}, N={len(core_replayed)})"
                )
            print(f"  Perfect replay: {perfect_replay_raw} ({100 * perfect_replay_raw / len(replayed):.0f}%)")
            print(f"  Improved:       {improved}  Regressed: {regressed}")
            if total_alignment_changes:
                print(
                    f"  Oracle EID alignment: changed={total_alignment_changes} "
                    f"oracle_assigned={total_alignment_oracle_assigned} "
                    f"local_fallback={total_alignment_local_fallback} "
                    f"transparent_wrapper_cleared={total_alignment_transparent_wrapper_cleared} "
                    f"before_nodes={total_alignment_before_nodes} "
                    f"after_nodes={total_alignment_after_nodes} "
                    f"node_count_mismatch_rows={alignment_node_count_mismatch_rows}"
                )
                if alignment_match_method_counts:
                    print(
                        "  Oracle EID alignment methods: "
                        + ", ".join(
                            f"{method}={count}"
                            for method, count in sorted(alignment_match_method_counts.items())
                        )
                    )
            by_delta = sorted(replayed, key=lambda r: r.replay_score - r.score)
            if regressed:
                print("\n  Top regressions:")
                for r in by_delta[:5]:
                    if r.replay_score < r.score - 0.001:
                        print(
                            f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  "
                            f"{_bench_row_evidence_context(r)}"
                        )
            if improved:
                print("\n  Top improvements:")
                for r in reversed(by_delta[-5:]):
                    if r.replay_score > r.score + 0.001:
                        print(
                            f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  "
                            f"{_bench_row_evidence_context(r)}"
                        )

    replay_errors = [r for r in ok if r.replay_error]
    if replay_errors:
        print(f"\nReplay errors ({len(replay_errors)}):")
        for r in replay_errors[:10]:
            print(f"  {r.statute_id}: {r.replay_error}")
            print(_source_line(r))

    commencement_errors = [r for r in ok if r.commencement_error]
    if commencement_errors:
        print(f"\nCommencement errors ({len(commencement_errors)}):")
        for r in commencement_errors[:10]:
            print(f"  {r.statute_id}: {r.commencement_error}")
            print(_source_line(r))

    # Text similarity summary
    text_scored = [r for r in ok if r.text_score >= 0.0]
    if text_scored:
        n_compared_total = sum(r.n_text_compared for r in text_scored)
        avg_text_enacted = sum(r.text_score for r in text_scored) / len(text_scored)
        print(f"\nText similarity (common EIDs, N={n_compared_total} EIDs across {len(text_scored)} statutes):")
        print(f"  Enacted avg:    {avg_text_enacted:.1%}")
        replay_text_scored = [r for r in text_scored if r.replay_text_score >= 0.0]
        if replay_text_scored:
            avg_text_replay = sum(r.replay_text_score for r in replay_text_scored) / len(replay_text_scored)
            avg_text_enacted_sub = sum(r.text_score for r in replay_text_scored) / len(replay_text_scored)
            delta_text = avg_text_replay - avg_text_enacted_sub
            print(f"  Replayed avg:   {avg_text_replay:.1%} ({delta_text:+.1%})")

    # By type
    type_groups: dict[str, list[_BenchResult]] = {}
    for r in ok:
        type_groups.setdefault(r.act_type, []).append(r)
    print("\nBy type:")
    for t, grp in sorted(type_groups.items()):
        a = sum(x.score for x in grp) / len(grp)
        p = sum(1 for x in grp if x.score == 1.0)
        replay_grp = [x for x in grp if x.replay_score >= 0.0]
        if replay_grp:
            ar = sum(x.replay_score for x in replay_grp) / len(replay_grp)
            print(f"  {t:<8} N={len(grp):5d}  enacted={a:.1%}  replay={ar:.1%}  perfect={p}")
        else:
            print(f"  {t:<8} N={len(grp):5d}  avg={a:.1%}  perfect={p}")

    def _primary_score_for_row(r: _BenchResult) -> float:
        return _bench_primary_score(r, has_commencement=has_commencement)

    def _primary_replay_score_for_row(r: _BenchResult) -> float:
        return _bench_primary_replay_score(r, has_commencement=has_commencement)

    def _score_fragment_for_row(r: _BenchResult) -> str:
        primary = _primary_score_for_row(r)
        if has_commencement and r.commencement_score >= 0.0:
            return f"score={primary:.1%} raw={r.score:.1%}"
        return f"score={primary:.1%}"

    def _replay_fragment_for_row(r: _BenchResult) -> str:
        primary_replay = _primary_replay_score_for_row(r)
        if primary_replay < 0.0:
            return ""
        if has_commencement and r.replay_commencement_score >= 0.0:
            return f"  replay={primary_replay:.1%} raw_replay={r.replay_score:.1%} ops={r.n_ops}"
        return f"  replay={primary_replay:.1%} ops={r.n_ops}"

    # Worst rows: separate core replay frontier from non-core structural/no-truth rows.
    worst_score_label = "commenced EID score" if has_commencement else "EID score"
    worst_core = sorted([r for r in core if _primary_score_for_row(r) < 1.0], key=_primary_score_for_row)[:15]
    if worst_core:
        print(f"\nWorst {len(worst_core)} core rows (by {worst_score_label}):")
        for r in worst_core:
            base = (
                f"  {r.statute_id:<30} {_score_fragment_for_row(r)}  "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"common={r.n_common:4d} effect_rows={r.n_effect_rows:4d} "
                f"effect_pages={(r.n_effect_feed_pages or r.n_effects):4d} "
                f"class={r.comparison_class}"
            )
            base += _replay_fragment_for_row(r)
            print(base)
            print(_source_line(r))

    worst_replay_score_label = "replay commenced EID score" if has_commencement else "replay EID score"
    replay_core = [r for r in core if _primary_replay_score_for_row(r) >= 0.0]
    worst_replay_core = sorted(
        [r for r in replay_core if _primary_replay_score_for_row(r) < 1.0],
        key=_primary_replay_score_for_row,
    )[:15]
    if worst_replay_core:
        print(f"\nWorst {len(worst_replay_core)} core replay rows (by {worst_replay_score_label}):")
        for r in worst_replay_core:
            base = (
                f"  {r.statute_id:<30} {_score_fragment_for_row(r)}  "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"common={r.n_common:4d} effect_rows={r.n_effect_rows:4d} "
                f"effect_pages={(r.n_effect_feed_pages or r.n_effects):4d} "
                f"class={r.comparison_class}"
            )
            base += _replay_fragment_for_row(r)
            print(base)
            print(_source_line(r))

    worst_noncore = sorted(noncore, key=_primary_score_for_row)[:10]
    if worst_noncore:
        print(f"\nWorst {len(worst_noncore)} non-core rows:")
        for r in worst_noncore:
            print(
                f"  {r.statute_id:<30} {_score_fragment_for_row(r)} "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"effect_rows={r.n_effect_rows:4d} effect_pages={(r.n_effect_feed_pages or r.n_effects):4d} "
                f"class={r.comparison_class}"
            )
            print(_source_line(r))


def _score_witness_path(label: str) -> Path:
    return _BENCH_DIR / f"{label}.score_witnesses.csv"


def _bench_diagnostics_path(label: str) -> Path:
    return _BENCH_DIR / f"{label}.diagnostics.jsonl"


def _score_witness_labels(comparison_scope: str) -> tuple[str, str]:
    if comparison_scope == "raw":
        return "enacted", "oracle"
    if comparison_scope == "replay":
        return "replay", "oracle"
    if comparison_scope == "commencement":
        return "commenced_enacted", "commenced_oracle"
    if comparison_scope == "replay_commencement":
        return "commenced_replay", "commenced_oracle"
    return comparison_scope or "left", "oracle"


def _count_csv_data_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, newline="") as handle:
        return max(sum(1 for _row in csv.reader(handle)) - 1, 0)


def _count_jsonl_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with open(path, encoding="utf-8") as handle:
        return sum(1 for line in handle if line.strip())


def _save_score_witness_rows(results: list[_BenchResult], label: str) -> int:
    out_path = _score_witness_path(label)
    rows: list[dict[str, object]] = []
    for result in results:
        for witness in result.score_witness_rows:
            left_label, right_label = _score_witness_labels(witness.comparison_scope)
            rows.append({
                "schema": _SCORE_WITNESS_SCHEMA,
                "label": label,
                "statute_id": result.statute_id,
                "comparison_scope": witness.comparison_scope,
                "score_formula": _SCORE_WITNESS_FORMULA,
                "left_label": left_label,
                "right_label": right_label,
                "side": witness.side,
                "eid": witness.eid,
                "rank": witness.rank,
                "category_total": witness.category_total,
                "sample_limit": witness.sample_limit,
                "truncated": "1" if witness.truncated else "0",
                "left_count": witness.left_count,
                "right_count": witness.right_count,
                "common_count": witness.common_count,
                "score_value": f"{witness.score_value:.4f}",
                "comparison_class": result.comparison_class,
                "core_benchmark": "1" if result.core_benchmark else "0",
                "enacted_source_status": result.enacted_source_status,
                "oracle_source_status": result.oracle_source_status,
                "enacted_source_size": result.enacted_source_size,
                "oracle_source_size": result.oracle_source_size,
                "enacted_source_sha256": result.enacted_source_sha256,
                "oracle_source_sha256": result.oracle_source_sha256,
                "enacted_source_url": result.enacted_source_url,
                "oracle_source_url": result.oracle_source_url,
                "uk_metadata_backfill_enabled": (
                    "1" if result.uk_metadata_backfill_enabled else "0"
                ),
                "uk_oracle_alignment_enabled": "1" if result.uk_oracle_alignment_enabled else "0",
                "uk_metadata_only_effects_enabled": (
                    "1" if result.uk_metadata_only_effects_enabled else "0"
                ),
                "uk_applicability_mode": result.uk_applicability_mode,
                "uk_authority_mode": result.uk_authority_mode,
            })
    if not rows:
        if out_path.exists():
            out_path.unlink()
        return 0
    fieldnames = list(rows[0])
    with open(out_path, "w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def _replay_adjudication_record(adjudication: Any) -> dict[str, Any]:
    if isinstance(adjudication, CompileAdjudication):
        return {
            "kind": adjudication.kind,
            "message": adjudication.message,
            "source_statute": adjudication.source_statute,
            "op_id": adjudication.op_id,
            "detail": dict(adjudication.detail),
        }
    # Compatibility for older tests/fakes that expose only a kind-like shape.
    return {
        "kind": str(getattr(adjudication, "kind", "") or "unknown"),
        "message": str(getattr(adjudication, "message", "") or ""),
        "source_statute": str(getattr(adjudication, "source_statute", "") or ""),
        "op_id": str(getattr(adjudication, "op_id", "") or ""),
        "detail": dict(getattr(adjudication, "detail", {}) or {}),
    }


def _bench_diagnostic_rows_for_result(result: _BenchResult, label: str) -> list[dict[str, Any]]:
    def _effect_diagnostic_lane(record: dict[str, Any]) -> str:
        rule_id = str(record.get("rule_id") or "")
        if is_uk_affecting_act_xml_source_observation(record):
            return "source_acquisition"
        if rule_id == "uk_effect_source_pathology_classified":
            return "effect_source_pathology"
        if rule_id == "uk_manual_compile_frontier_classified":
            return "manual_compile_frontier"
        return "effect_diagnostic"

    def _diagnostic_row_blocking(lane: str, record: dict[str, Any]) -> bool:
        if lane in {
            "effect_feed",
            "source_acquisition",
            "effect_source_pathology",
            "manual_compile_frontier",
        }:
            return is_blocking_compile_record(record)
        if "blocking" in record or record.get("strict_disposition"):
            return is_blocking_compile_record(record)
        return False

    leading_lanes: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
        ("source_parse", result.source_parse_observations),
        ("effect_feed", result.effect_feed_observations),
    )
    trailing_lanes: tuple[tuple[str, tuple[dict[str, Any], ...]], ...] = (
        ("authority", result.uk_authority_observations),
        ("lowering", result.lowering_rejections),
        ("replay_adjudication", result.replay_adjudications),
        ("bench_exception", result.bench_exception_observations),
    )
    rows: list[dict[str, Any]] = []

    def _append_row(lane: str, index: int, record: dict[str, Any]) -> None:
        rule_id = str(record.get("rule_id") or record.get("kind") or "")
        replay_adjudication_bucket = (
            classify_uk_replay_adjudication_bucket(rule_id)
            if lane == "replay_adjudication" and rule_id
            else ""
        )
        rows.append(
            {
                "schema": "uk_bench_diagnostic.v1",
                "label": label,
                "statute_id": result.statute_id,
                "diagnostic_lane": lane,
                "index": index,
                "rule_id": rule_id,
                "replay_adjudication_bucket": replay_adjudication_bucket,
                "blocking": _diagnostic_row_blocking(lane, record),
                "record": dict(record),
            }
        )

    for lane, records in leading_lanes:
        for index, record in enumerate(records):
            _append_row(lane, index, record)
    effect_lane_indexes: Counter[str] = Counter()
    for record in result.effect_diagnostics:
        lane = _effect_diagnostic_lane(record)
        index = effect_lane_indexes[lane]
        effect_lane_indexes[lane] += 1
        _append_row(lane, index, record)
    for lane, records in trailing_lanes:
        for index, record in enumerate(records):
            _append_row(lane, index, record)
    return rows


def _save_bench_diagnostic_rows(results: list[_BenchResult], label: str) -> int:
    out_path = _bench_diagnostics_path(label)
    rows: list[dict[str, Any]] = []
    for result in results:
        rows.extend(_bench_diagnostic_rows_for_result(result, label))
    if not rows:
        if out_path.exists():
            out_path.unlink()
        return 0
    with open(out_path, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
    return len(rows)


def _format_history_average(values: list[float]) -> str:
    if not values:
        return ""
    return f"{sum(values) / len(values):.4f}"


def _history_needs_header() -> bool:
    if not _HISTORY_CSV.exists() or _HISTORY_CSV.stat().st_size == 0:
        return True
    latest_header: list[str] | None = None
    with open(_HISTORY_CSV, newline="") as handle:
        for raw_row in csv.reader(handle):
            if raw_row and raw_row[0] == "label":
                latest_header = raw_row
    return tuple(latest_header or ()) != _HISTORY_HEADERS


def _parse_json_rule_counts(raw_counts: str) -> dict[str, int]:
    if not raw_counts:
        return {}
    parsed_counts = json.loads(raw_counts)
    if not isinstance(parsed_counts, dict):
        return {}
    return {
        str(rule_id): int(count)
        for rule_id, count in parsed_counts.items()
        if isinstance(count, int)
    }


def _parse_json_observation_rows(raw_rows: str) -> tuple[dict[str, Any], ...]:
    if not raw_rows:
        return ()
    parsed_rows = json.loads(raw_rows)
    if not isinstance(parsed_rows, list):
        return ()
    return tuple(dict(row) for row in parsed_rows if isinstance(row, dict))


def _load_bench_diagnostic_rows(label: str) -> dict[str, dict[str, tuple[dict[str, Any], ...]]]:
    path = _bench_diagnostics_path(label)
    if not path.exists():
        return {}
    rows_by_statute_lane: dict[str, dict[str, list[tuple[int, dict[str, Any]]]]] = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            parsed_row = json.loads(line)
            if not isinstance(parsed_row, dict):
                continue
            if parsed_row.get("schema") != "uk_bench_diagnostic.v1":
                continue
            statute_id = str(parsed_row.get("statute_id") or "")
            lane = str(parsed_row.get("diagnostic_lane") or "")
            record = parsed_row.get("record")
            if not statute_id or not lane or not isinstance(record, dict):
                continue
            index_raw = parsed_row.get("index", 0)
            index = index_raw if isinstance(index_raw, int) else 0
            lane_rows = rows_by_statute_lane.setdefault(statute_id, {}).setdefault(lane, [])
            lane_rows.append((index, dict(record)))
    return {
        statute_id: {
            lane: tuple(record for _index, record in sorted(records, key=lambda item: item[0]))
            for lane, records in lane_rows.items()
        }
        for statute_id, lane_rows in rows_by_statute_lane.items()
    }


def _append_history(results: list[_BenchResult], label: str, score_witness_count: int) -> None:
    ok = [r for r in results if r.status == "OK" and r.n_oracle_eids > 0]
    has_commencement = any(r.commencement_score >= 0.0 for r in ok)
    primary_scores = [
        _bench_primary_score(r, has_commencement=has_commencement)
        for r in ok
    ]
    replay_scores = [r.replay_score for r in ok if r.replay_score >= 0.0]
    commencement_scores = [r.commencement_score for r in ok if r.commencement_score >= 0.0]
    row_status_counts = Counter(r.status for r in results)
    enacted_source_status_counts = Counter(r.enacted_source_status for r in results)
    oracle_source_status_counts = Counter(r.oracle_source_status for r in results)
    regime_counts: Counter[str] = Counter(
        (
            f"metadata_backfill={int(r.uk_metadata_backfill_enabled)}"
            f";oracle_alignment={int(r.uk_oracle_alignment_enabled)}"
            f";metadata_only_effects={int(r.uk_metadata_only_effects_enabled)}"
            f";applicability={r.uk_applicability_mode}"
            f";authority={r.uk_authority_mode}"
        )
        for r in results
    )
    observation_rule_counts: Counter[str] = Counter()
    source_parse_observation_rule_counts: Counter[str] = Counter()
    source_parse_rejection_rule_counts: Counter[str] = Counter()
    effect_source_pathology_counts: Counter[str] = Counter()
    manual_compile_status_counts: Counter[str] = Counter()
    manual_compile_rule_counts: Counter[str] = Counter()
    source_acquisition_observation_rule_counts: Counter[str] = Counter()
    source_acquisition_rejection_rule_counts: Counter[str] = Counter()
    bench_exception_rule_counts: Counter[str] = Counter()
    authority_observation_rule_counts: Counter[str] = Counter()
    authority_rule_counts: Counter[str] = Counter()
    lowering_observation_rule_counts: Counter[str] = Counter()
    lowering_rule_counts: Counter[str] = Counter()
    blocking_lowering_rule_counts: Counter[str] = Counter()
    replay_adjudication_kind_counts: Counter[str] = Counter()
    replay_adjudication_bucket_counts: Counter[str] = Counter()
    residual_claim_tier_counts: Counter[str] = Counter()
    residual_claim_kind_counts: Counter[str] = Counter()
    for r in results:
        observation_rule_counts.update(r.effect_feed_observation_rule_counts)
        source_parse_observation_rule_counts.update(r.source_parse_observation_rule_counts)
        source_parse_rejection_rule_counts.update(r.source_parse_rejection_rule_counts)
        effect_source_pathology_counts.update(r.effect_source_pathology_counts)
        manual_compile_status_counts.update(r.manual_compile_status_counts)
        manual_compile_rule_counts.update(r.manual_compile_rule_counts)
        source_acquisition_observation_rule_counts.update(
            r.source_acquisition_observation_rule_counts
        )
        source_acquisition_rejection_rule_counts.update(r.source_acquisition_rejection_rule_counts)
        bench_exception_rule_counts.update(r.bench_exception_rule_counts)
        authority_observation_rule_counts.update(r.uk_authority_observation_rule_counts)
        authority_rule_counts.update(r.uk_authority_rejection_rule_counts)
        lowering_observation_rule_counts.update(r.lowering_observation_rule_counts)
        lowering_rule_counts.update(r.lowering_rejection_rule_counts)
        blocking_lowering_rule_counts.update(r.blocking_lowering_rejection_rule_counts)
        replay_adjudication_kind_counts.update(r.replay_adjudication_kind_counts)
        replay_adjudication_bucket_counts.update(
            _replay_adjudication_bucket_counts(r.replay_adjudication_kind_counts)
        )
        residual_claim_tier_counts[r.uk_residual_claim_tier or "UNRESOLVED"] += 1
        residual_claim_kind_counts[r.uk_residual_claim_kind or "unknown"] += 1
    _HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(_HISTORY_CSV, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_HISTORY_HEADERS))
        if _history_needs_header():
            writer.writeheader()
        writer.writerow(
            {
                "label": label,
                "n_total": len(results),
                "n_ok": len(ok),
                "n_core_ok": sum(1 for r in ok if r.core_benchmark),
                "row_status_counts": json.dumps(
                    dict(sorted(row_status_counts.items())),
                    sort_keys=True,
                ),
                "enacted_source_status_counts": json.dumps(
                    dict(sorted(enacted_source_status_counts.items())),
                    sort_keys=True,
                ),
                "oracle_source_status_counts": json.dumps(
                    dict(sorted(oracle_source_status_counts.items())),
                    sort_keys=True,
                ),
                "score_mode": _primary_score_mode(ok) if ok else "none",
                "avg_score": _format_history_average(primary_scores),
                "n_perfect": sum(1 for score in primary_scores if score == 1.0),
                "avg_raw_score": _format_history_average([r.score for r in ok]),
                "avg_replay_score": _format_history_average(replay_scores),
                "avg_commencement_score": _format_history_average(commencement_scores),
                "n_commencement_scored": len(commencement_scores),
                "n_replay_scored": len(replay_scores),
                "n_replay_errors": sum(1 for r in results if r.replay_error),
                "n_commencement_errors": sum(1 for r in results if r.commencement_error),
                "source_parse_observations": sum(r.source_parse_observation_count for r in results),
                "source_parse_observation_rules": json.dumps(
                    dict(sorted(source_parse_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "source_parse_rejections": sum(r.source_parse_rejection_count for r in results),
                "source_parse_rejection_rules": json.dumps(
                    dict(sorted(source_parse_rejection_rule_counts.items())),
                    sort_keys=True,
                ),
                "effect_source_pathology_counts": json.dumps(
                    dict(sorted(effect_source_pathology_counts.items())),
                    sort_keys=True,
                ),
                "manual_compile_status_counts": json.dumps(
                    dict(sorted(manual_compile_status_counts.items())),
                    sort_keys=True,
                ),
                "manual_compile_rule_counts": json.dumps(
                    dict(sorted(manual_compile_rule_counts.items())),
                    sort_keys=True,
                ),
                "source_acquisition_observations": sum(
                    r.source_acquisition_observation_count for r in results
                ),
                "source_acquisition_observation_rules": json.dumps(
                    dict(sorted(source_acquisition_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "source_acquisition_rejections": sum(
                    r.source_acquisition_rejection_count for r in results
                ),
                "source_acquisition_rejection_rules": json.dumps(
                    dict(sorted(source_acquisition_rejection_rule_counts.items())),
                    sort_keys=True,
                ),
                "bench_exceptions": sum(r.bench_exception_count for r in results),
                "bench_exception_rules": json.dumps(
                    dict(sorted(bench_exception_rule_counts.items())),
                    sort_keys=True,
                ),
                "effect_feed_observations": sum(r.effect_feed_observation_count for r in results),
                "effect_feed_observation_rules": json.dumps(
                    dict(sorted(observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "effect_feed_rejections": sum(r.effect_feed_rejection_count for r in results),
                "authority_observations": sum(
                    r.uk_authority_observation_count for r in results
                ),
                "authority_observation_rules": json.dumps(
                    dict(sorted(authority_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "authority_rejections": sum(r.uk_authority_rejection_count for r in results),
                "authority_rejection_rules": json.dumps(
                    dict(sorted(authority_rule_counts.items())),
                    sort_keys=True,
                ),
                "lowering_observations": sum(r.lowering_observation_count for r in results),
                "lowering_observation_rules": json.dumps(
                    dict(sorted(lowering_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "lowering_rejections": sum(r.lowering_rejection_count for r in results),
                "lowering_rejection_rules": json.dumps(
                    dict(sorted(lowering_rule_counts.items())),
                    sort_keys=True,
                ),
                "blocking_lowering_rejections": sum(
                    r.blocking_lowering_rejection_count for r in results
                ),
                "blocking_lowering_rejection_rules": json.dumps(
                    dict(sorted(blocking_lowering_rule_counts.items())),
                    sort_keys=True,
                ),
                "replay_adjudications": sum(r.replay_adjudication_count for r in results),
                "replay_adjudication_kinds": json.dumps(
                    dict(sorted(replay_adjudication_kind_counts.items())),
                    sort_keys=True,
                ),
                "replay_adjudication_buckets": json.dumps(
                    dict(sorted(replay_adjudication_bucket_counts.items())),
                    sort_keys=True,
                ),
                "uk_residual_claim_tiers": json.dumps(
                    dict(sorted(residual_claim_tier_counts.items())),
                    sort_keys=True,
                ),
                "uk_residual_claim_kinds": json.dumps(
                    dict(sorted(residual_claim_kind_counts.items())),
                    sort_keys=True,
                ),
                "uk_residual_section_claims": sum(
                    r.uk_residual_section_claim_count for r in results
                ),
                "score_witness_rows": score_witness_count,
                "replay_regimes": json.dumps(dict(sorted(regime_counts.items())), sort_keys=True),
                "timestamp": time.strftime("%Y-%m-%d %H:%M"),
            }
        )


def _format_history_percent(value: str) -> str:
    if not value:
        return "n/a"
    try:
        return f"{float(value):.1%}"
    except ValueError:
        return value


def _history_rows() -> list[tuple[str, dict[str, str]]]:
    rows: list[tuple[str, dict[str, str]]] = []
    header: list[str] = []
    schema = "unknown"
    with open(_HISTORY_CSV, newline="") as handle:
        for raw_row in csv.reader(handle):
            if not raw_row:
                continue
            if raw_row[0] == "label":
                header = raw_row
                previous_current_header = [
                    field
                    for field in _HISTORY_HEADERS
                    if field
                    not in {
                        "uk_residual_claim_tiers",
                        "uk_residual_claim_kinds",
                        "uk_residual_section_claims",
                    }
                ]
                previous_bucket_header = [
                    field
                    for field in previous_current_header
                    if field != "replay_adjudication_buckets"
                ]
                if (
                    tuple(raw_row) == _HISTORY_HEADERS
                    or raw_row == previous_current_header
                    or raw_row == previous_bucket_header
                ):
                    schema = "current"
                elif raw_row[:6] == ["label", "n_total", "n_ok", "avg_score", "n_perfect", "timestamp"]:
                    schema = "legacy"
                else:
                    schema = "unknown"
                continue
            if not header:
                continue
            padded_row = raw_row + [""] * max(len(header) - len(raw_row), 0)
            rows.append((schema, dict(zip(header, padded_row))))
    return rows


def _show_history() -> None:
    rows = _history_rows()
    if not rows:
        print("No UK bench history rows.")
        return
    print("\n=== UK Bench History ===")
    for schema, row in rows:
        if schema == "current":
            print(
                f"{row.get('label', '')}: "
                f"score={_format_history_percent(row.get('avg_score', ''))} "
                f"mode={row.get('score_mode', 'unknown')} "
                f"ok={row.get('n_ok', '0')}/{row.get('n_total', '0')} "
                f"core={row.get('n_core_ok', '0')} "
                f"perfect={row.get('n_perfect', '0')} "
                f"raw={_format_history_percent(row.get('avg_raw_score', ''))} "
                f"replay={_format_history_percent(row.get('avg_replay_score', ''))} "
                f"commencement={_format_history_percent(row.get('avg_commencement_score', ''))} "
                f"witness_rows={row.get('score_witness_rows', '0')} "
                f"at={row.get('timestamp', '')}"
            )
            print(
                "  evidence: "
                f"source_parse_obs={row.get('source_parse_observations', '0')} "
                f"source_parse_rejections={row.get('source_parse_rejections', '0')} "
                f"source_acquisition_obs={row.get('source_acquisition_observations', '0')} "
                f"source_acquisition_rejections={row.get('source_acquisition_rejections', '0')} "
                f"bench_exceptions={row.get('bench_exceptions', '0')} "
                f"feed_obs={row.get('effect_feed_observations', '0')} "
                f"feed_rejections={row.get('effect_feed_rejections', '0')} "
                f"authority_obs={row.get('authority_observations', '0')} "
                f"authority_blocking_rejections={row.get('authority_rejections', '0')} "
                f"lowering_obs={row.get('lowering_observations', '0')} "
                f"lowering_rejections={row.get('lowering_rejections', '0')} "
                f"blocking_lowering={row.get('blocking_lowering_rejections', '0')} "
                f"adjudications={row.get('replay_adjudications', '0')} "
                f"residual_section_claims={row.get('uk_residual_section_claims', '0')} "
                f"replay_errors={row.get('n_replay_errors', '0')} "
                f"commencement_errors={row.get('n_commencement_errors', '0')}"
            )
            row_status_counts = row.get("row_status_counts", "")
            enacted_source_counts = row.get("enacted_source_status_counts", "")
            oracle_source_counts = row.get("oracle_source_status_counts", "")
            if (
                (row_status_counts and row_status_counts != "{}")
                or (enacted_source_counts and enacted_source_counts != "{}")
                or (oracle_source_counts and oracle_source_counts != "{}")
            ):
                print(
                    "  source_status: "
                    f"rows={row_status_counts or '{}'} "
                    f"enacted={enacted_source_counts or '{}'} "
                    f"oracle={oracle_source_counts or '{}'}"
                )
            observation_rules = row.get("effect_feed_observation_rules", "")
            source_parse_observation_rules = row.get("source_parse_observation_rules", "")
            if source_parse_observation_rules and source_parse_observation_rules != "{}":
                print(f"  source_parse_observation_rules: {source_parse_observation_rules}")
            source_parse_rejection_rules = row.get("source_parse_rejection_rules", "")
            if source_parse_rejection_rules and source_parse_rejection_rules != "{}":
                print(f"  source_parse_rejection_rules: {source_parse_rejection_rules}")
            effect_source_pathology_counts = row.get("effect_source_pathology_counts", "")
            if effect_source_pathology_counts and effect_source_pathology_counts != "{}":
                print(f"  effect_source_pathology_counts: {effect_source_pathology_counts}")
            manual_compile_status_counts = row.get("manual_compile_status_counts", "")
            if manual_compile_status_counts and manual_compile_status_counts != "{}":
                print(f"  manual_compile_status_counts: {manual_compile_status_counts}")
            manual_compile_rule_counts = row.get("manual_compile_rule_counts", "")
            if manual_compile_rule_counts and manual_compile_rule_counts != "{}":
                print(f"  manual_compile_rule_counts: {manual_compile_rule_counts}")
            source_acquisition_observation_rules = row.get(
                "source_acquisition_observation_rules",
                "",
            )
            if (
                source_acquisition_observation_rules
                and source_acquisition_observation_rules != "{}"
            ):
                print(
                    "  source_acquisition_observation_rules: "
                    f"{source_acquisition_observation_rules}"
                )
            source_acquisition_rejection_rules = row.get("source_acquisition_rejection_rules", "")
            if source_acquisition_rejection_rules and source_acquisition_rejection_rules != "{}":
                print(f"  source_acquisition_rejection_rules: {source_acquisition_rejection_rules}")
            bench_exception_rules = row.get("bench_exception_rules", "")
            if bench_exception_rules and bench_exception_rules != "{}":
                print(f"  bench_exception_rules: {bench_exception_rules}")
            if observation_rules and observation_rules != "{}":
                print(f"  feed_observation_rules: {observation_rules}")
            authority_observation_rules = row.get("authority_observation_rules", "")
            if authority_observation_rules and authority_observation_rules != "{}":
                print(f"  authority_observation_rules: {authority_observation_rules}")
            authority_rules = row.get("authority_rejection_rules", "")
            if authority_rules and authority_rules != "{}":
                print(f"  authority_blocking_rejection_rules: {authority_rules}")
            lowering_observation_rules = row.get("lowering_observation_rules", "")
            if lowering_observation_rules and lowering_observation_rules != "{}":
                print(f"  lowering_observation_rules: {lowering_observation_rules}")
            lowering_rules = row.get("lowering_rejection_rules", "")
            if lowering_rules and lowering_rules != "{}":
                print(f"  lowering_rejection_rules: {lowering_rules}")
            blocking_lowering_rules = row.get("blocking_lowering_rejection_rules", "")
            if blocking_lowering_rules and blocking_lowering_rules != "{}":
                print(f"  blocking_lowering_rejection_rules: {blocking_lowering_rules}")
            adjudication_kinds = row.get("replay_adjudication_kinds", "")
            if adjudication_kinds and adjudication_kinds != "{}":
                print(f"  replay_adjudication_kinds: {adjudication_kinds}")
            adjudication_buckets = row.get("replay_adjudication_buckets", "")
            if adjudication_buckets and adjudication_buckets != "{}":
                print(f"  replay_adjudication_buckets: {adjudication_buckets}")
            residual_claim_tiers = row.get("uk_residual_claim_tiers", "")
            if residual_claim_tiers and residual_claim_tiers != "{}":
                print(f"  uk_residual_claim_tiers: {residual_claim_tiers}")
            residual_claim_kinds = row.get("uk_residual_claim_kinds", "")
            if residual_claim_kinds and residual_claim_kinds != "{}":
                print(f"  uk_residual_claim_kinds: {residual_claim_kinds}")
            regimes = row.get("replay_regimes", "")
            if regimes and regimes != "{}":
                print(f"  regimes: {regimes}")
        elif schema == "legacy":
            print(
                f"{row.get('label', '')}: "
                f"score={_format_history_percent(row.get('avg_score', ''))} "
                f"ok={row.get('n_ok', '0')}/{row.get('n_total', '0')} "
                f"perfect={row.get('n_perfect', '0')} "
                f"at={row.get('timestamp', '')} "
                "schema=legacy"
            )
        else:
            print(", ".join(f"{key}={value}" for key, value in row.items()))


def _save_results(results: list[_BenchResult], label: str) -> None:
    _BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _BENCH_DIR / f"{label}.csv"

    has_replay = any(
        r.replay_score >= 0.0 or r.n_ops != 0 or r.replay_error or r.replay_adjudication_count
        for r in results
    )
    has_text = any(r.text_score >= 0.0 for r in results)
    has_commencement = any(r.commencement_score >= 0.0 for r in results)
    has_commencement_error = any(r.commencement_error for r in results)

    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        # When commencement is active, lead with commencement_score as primary
        # "score" column and keep raw EID score as "raw_score".
        if has_commencement:
            headers = [
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_effect_feed_pages",
                "n_effect_rows",
                "effect_feed_rejection_count",
                "effect_feed_rejection_rule_counts",
                "effect_feed_observation_count",
                "effect_feed_observation_rule_counts",
                "effect_feed_count_error",
                "bench_exception_count",
                "bench_exception_rule_counts",
                "bench_exception_observations",
                "source_parse_rejection_count",
                "source_parse_rejection_rule_counts",
                "source_parse_observation_count",
                "source_parse_observation_rule_counts",
                "effect_source_pathology_counts",
                "manual_compile_status_counts",
                "manual_compile_rule_counts",
                "source_acquisition_observation_count",
                "source_acquisition_observation_rule_counts",
                "source_acquisition_rejection_count",
                "source_acquisition_rejection_rule_counts",
                "enacted_source_status",
                "oracle_source_status",
                "enacted_source_size",
                "oracle_source_size",
                "enacted_source_sha256",
                "oracle_source_sha256",
                "enacted_source_url",
                "oracle_source_url",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "raw_score",
                "n_commenced_eids",
                "status",
                "error",
                "comparison_class",
                "core_benchmark",
            ]
        else:
            headers = [
                "statute_id",
                "act_type",
                "year",
                "n_effects",
                "n_effect_feed_pages",
                "n_effect_rows",
                "effect_feed_rejection_count",
                "effect_feed_rejection_rule_counts",
                "effect_feed_observation_count",
                "effect_feed_observation_rule_counts",
                "effect_feed_count_error",
                "bench_exception_count",
                "bench_exception_rule_counts",
                "bench_exception_observations",
                "source_parse_rejection_count",
                "source_parse_rejection_rule_counts",
                "source_parse_observation_count",
                "source_parse_observation_rule_counts",
                "effect_source_pathology_counts",
                "manual_compile_status_counts",
                "manual_compile_rule_counts",
                "source_acquisition_observation_count",
                "source_acquisition_observation_rule_counts",
                "source_acquisition_rejection_count",
                "source_acquisition_rejection_rule_counts",
                "enacted_source_status",
                "oracle_source_status",
                "enacted_source_size",
                "oracle_source_size",
                "enacted_source_sha256",
                "oracle_source_sha256",
                "enacted_source_url",
                "oracle_source_url",
                "n_enacted_eids",
                "n_oracle_eids",
                "n_common",
                "score",
                "status",
                "error",
                "comparison_class",
                "core_benchmark",
            ]
        if has_replay:
            if has_commencement:
                headers += [
                    "n_replayed_eids",
                    "n_replay_common",
                    "replay_score",
                    "replay_commencement_score",
                    "n_ops",
                    "replay_error",
                    "replay_adjudication_count",
                    "replay_adjudication_kind_counts",
                    "uk_residual_claim_tier",
                    "uk_residual_claim_kind",
                    "uk_residual_claim_comparison_class",
                    "uk_residual_claim_core_comparison",
                    "uk_residual_only_in_replayed_count",
                    "uk_residual_only_in_oracle_count",
                    "uk_residual_section_claim_count",
                    "uk_residual_section_claim_emitted",
                    "oracle_alignment_changed_count",
                    "oracle_alignment_oracle_assigned_count",
                    "oracle_alignment_local_fallback_count",
                    "oracle_alignment_transparent_wrapper_cleared_count",
                    "oracle_alignment_before_node_count",
                    "oracle_alignment_after_node_count",
                    "oracle_alignment_node_count_mismatch",
                    "oracle_alignment_match_method_counts",
                    "uk_metadata_backfill_enabled",
                    "uk_oracle_alignment_enabled",
                    "uk_metadata_only_effects_enabled",
                    "uk_applicability_mode",
                    "uk_authority_mode",
                    "uk_source_purity_lane",
                    "uk_source_semantics_clean",
                    "uk_source_first_candidate",
                    "uk_source_first_candidate_reasons",
                    "uk_authority_observation_count",
                    "uk_authority_observation_rule_counts",
                    "uk_authority_rejection_count",
                    "uk_authority_rejection_rule_counts",
                    "lowering_observation_count",
                    "lowering_observation_rule_counts",
                    "lowering_rejection_count",
                    "lowering_rejection_rule_counts",
                    "blocking_lowering_rejection_count",
                    "blocking_lowering_rejection_rule_counts",
                ]
            else:
                headers += [
                    "n_replayed_eids",
                    "n_replay_common",
                    "replay_score",
                    "n_ops",
                    "replay_error",
                    "replay_adjudication_count",
                    "replay_adjudication_kind_counts",
                    "uk_residual_claim_tier",
                    "uk_residual_claim_kind",
                    "uk_residual_claim_comparison_class",
                    "uk_residual_claim_core_comparison",
                    "uk_residual_only_in_replayed_count",
                    "uk_residual_only_in_oracle_count",
                    "uk_residual_section_claim_count",
                    "uk_residual_section_claim_emitted",
                    "oracle_alignment_changed_count",
                    "oracle_alignment_oracle_assigned_count",
                    "oracle_alignment_local_fallback_count",
                    "oracle_alignment_transparent_wrapper_cleared_count",
                    "oracle_alignment_before_node_count",
                    "oracle_alignment_after_node_count",
                    "oracle_alignment_node_count_mismatch",
                    "oracle_alignment_match_method_counts",
                    "uk_metadata_backfill_enabled",
                    "uk_oracle_alignment_enabled",
                    "uk_metadata_only_effects_enabled",
                    "uk_applicability_mode",
                    "uk_authority_mode",
                    "uk_source_purity_lane",
                    "uk_source_semantics_clean",
                    "uk_source_first_candidate",
                    "uk_source_first_candidate_reasons",
                    "uk_authority_observation_count",
                    "uk_authority_observation_rule_counts",
                    "uk_authority_rejection_count",
                    "uk_authority_rejection_rule_counts",
                    "lowering_observation_count",
                    "lowering_observation_rule_counts",
                    "lowering_rejection_count",
                    "lowering_rejection_rule_counts",
                    "blocking_lowering_rejection_count",
                    "blocking_lowering_rejection_rule_counts",
                ]
        if has_commencement_error:
            headers += ["commencement_error"]
        if has_text:
            headers += ["text_score", "n_text_compared", "replay_text_score"]
        w.writerow(headers)
        for r in results:
            # Primary score = commencement_score when available, else raw score.
            primary_score = _bench_primary_score(r, has_commencement=has_commencement)
            if has_commencement:
                row = [
                    r.statute_id,
                    r.act_type,
                    r.year,
                    r.n_effects,
                    r.n_effect_feed_pages or r.n_effects,
                    r.n_effect_rows,
                    r.effect_feed_rejection_count,
                    json.dumps(r.effect_feed_rejection_rule_counts, sort_keys=True),
                    r.effect_feed_observation_count,
                    json.dumps(r.effect_feed_observation_rule_counts, sort_keys=True),
                    r.effect_feed_count_error,
                    r.bench_exception_count,
                    json.dumps(r.bench_exception_rule_counts, sort_keys=True),
                    json.dumps(list(r.bench_exception_observations), sort_keys=True),
                    r.source_parse_rejection_count,
                    json.dumps(r.source_parse_rejection_rule_counts, sort_keys=True),
                    r.source_parse_observation_count,
                    json.dumps(r.source_parse_observation_rule_counts, sort_keys=True),
                    json.dumps(r.effect_source_pathology_counts, sort_keys=True),
                    json.dumps(r.manual_compile_status_counts, sort_keys=True),
                    json.dumps(r.manual_compile_rule_counts, sort_keys=True),
                    r.source_acquisition_observation_count,
                    json.dumps(r.source_acquisition_observation_rule_counts, sort_keys=True),
                    r.source_acquisition_rejection_count,
                    json.dumps(r.source_acquisition_rejection_rule_counts, sort_keys=True),
                    r.enacted_source_status,
                    r.oracle_source_status,
                    r.enacted_source_size,
                    r.oracle_source_size,
                    r.enacted_source_sha256,
                    r.oracle_source_sha256,
                    r.enacted_source_url,
                    r.oracle_source_url,
                    r.n_enacted_eids,
                    r.n_oracle_eids,
                    r.n_common,
                    f"{primary_score:.4f}",
                    f"{r.score:.4f}",
                    r.n_commenced_eids,
                    r.status,
                    r.error,
                    r.comparison_class,
                    "1" if r.core_benchmark else "0",
                ]
            else:
                row = [
                    r.statute_id,
                    r.act_type,
                    r.year,
                    r.n_effects,
                    r.n_effect_feed_pages or r.n_effects,
                    r.n_effect_rows,
                    r.effect_feed_rejection_count,
                    json.dumps(r.effect_feed_rejection_rule_counts, sort_keys=True),
                    r.effect_feed_observation_count,
                    json.dumps(r.effect_feed_observation_rule_counts, sort_keys=True),
                    r.effect_feed_count_error,
                    r.bench_exception_count,
                    json.dumps(r.bench_exception_rule_counts, sort_keys=True),
                    json.dumps(list(r.bench_exception_observations), sort_keys=True),
                    r.source_parse_rejection_count,
                    json.dumps(r.source_parse_rejection_rule_counts, sort_keys=True),
                    r.source_parse_observation_count,
                    json.dumps(r.source_parse_observation_rule_counts, sort_keys=True),
                    json.dumps(r.effect_source_pathology_counts, sort_keys=True),
                    json.dumps(r.manual_compile_status_counts, sort_keys=True),
                    json.dumps(r.manual_compile_rule_counts, sort_keys=True),
                    r.source_acquisition_observation_count,
                    json.dumps(r.source_acquisition_observation_rule_counts, sort_keys=True),
                    r.source_acquisition_rejection_count,
                    json.dumps(r.source_acquisition_rejection_rule_counts, sort_keys=True),
                    r.enacted_source_status,
                    r.oracle_source_status,
                    r.enacted_source_size,
                    r.oracle_source_size,
                    r.enacted_source_sha256,
                    r.oracle_source_sha256,
                    r.enacted_source_url,
                    r.oracle_source_url,
                    r.n_enacted_eids,
                    r.n_oracle_eids,
                    r.n_common,
                    f"{r.score:.4f}",
                    r.status,
                    r.error,
                    r.comparison_class,
                    "1" if r.core_benchmark else "0",
                ]
            if has_replay:
                if has_commencement:
                    row += [
                        r.n_replayed_eids,
                        r.n_replay_common,
                        f"{r.replay_score:.4f}" if r.replay_score >= 0.0 else "",
                        f"{r.replay_commencement_score:.4f}" if r.replay_commencement_score >= 0.0 else "",
                        r.n_ops,
                        r.replay_error,
                        r.replay_adjudication_count,
                        json.dumps(r.replay_adjudication_kind_counts, sort_keys=True),
                        r.uk_residual_claim_tier,
                        r.uk_residual_claim_kind,
                        r.uk_residual_claim_comparison_class,
                        "1" if r.uk_residual_claim_core_comparison else "0",
                        r.uk_residual_only_in_replayed_count,
                        r.uk_residual_only_in_oracle_count,
                        r.uk_residual_section_claim_count,
                        "1" if r.uk_residual_section_claim_emitted else "0",
                        r.oracle_alignment_changed_count,
                        r.oracle_alignment_oracle_assigned_count,
                        r.oracle_alignment_local_fallback_count,
                        r.oracle_alignment_transparent_wrapper_cleared_count,
                        r.oracle_alignment_before_node_count,
                        r.oracle_alignment_after_node_count,
                        "1" if r.oracle_alignment_node_count_mismatch else "0",
                        json.dumps(r.oracle_alignment_match_method_counts, sort_keys=True),
                        "1" if r.uk_metadata_backfill_enabled else "0",
                        "1" if r.uk_oracle_alignment_enabled else "0",
                        "1" if r.uk_metadata_only_effects_enabled else "0",
                        r.uk_applicability_mode,
                        r.uk_authority_mode,
                        r.uk_source_purity_lane,
                        "1" if r.uk_source_semantics_clean else "0",
                        "1" if r.uk_source_first_candidate else "0",
                        json.dumps(list(r.uk_source_first_candidate_reasons), sort_keys=True),
                        r.uk_authority_observation_count,
                        json.dumps(r.uk_authority_observation_rule_counts, sort_keys=True),
                        r.uk_authority_rejection_count,
                        json.dumps(r.uk_authority_rejection_rule_counts, sort_keys=True),
                        r.lowering_observation_count,
                        json.dumps(r.lowering_observation_rule_counts, sort_keys=True),
                        r.lowering_rejection_count,
                        json.dumps(r.lowering_rejection_rule_counts, sort_keys=True),
                        r.blocking_lowering_rejection_count,
                        json.dumps(r.blocking_lowering_rejection_rule_counts, sort_keys=True),
                    ]
                else:
                    row += [
                        r.n_replayed_eids,
                        r.n_replay_common,
                        f"{r.replay_score:.4f}" if r.replay_score >= 0.0 else "",
                        r.n_ops,
                        r.replay_error,
                        r.replay_adjudication_count,
                        json.dumps(r.replay_adjudication_kind_counts, sort_keys=True),
                        r.uk_residual_claim_tier,
                        r.uk_residual_claim_kind,
                        r.uk_residual_claim_comparison_class,
                        "1" if r.uk_residual_claim_core_comparison else "0",
                        r.uk_residual_only_in_replayed_count,
                        r.uk_residual_only_in_oracle_count,
                        r.uk_residual_section_claim_count,
                        "1" if r.uk_residual_section_claim_emitted else "0",
                        r.oracle_alignment_changed_count,
                        r.oracle_alignment_oracle_assigned_count,
                        r.oracle_alignment_local_fallback_count,
                        r.oracle_alignment_transparent_wrapper_cleared_count,
                        r.oracle_alignment_before_node_count,
                        r.oracle_alignment_after_node_count,
                        "1" if r.oracle_alignment_node_count_mismatch else "0",
                        json.dumps(r.oracle_alignment_match_method_counts, sort_keys=True),
                        "1" if r.uk_metadata_backfill_enabled else "0",
                        "1" if r.uk_oracle_alignment_enabled else "0",
                        "1" if r.uk_metadata_only_effects_enabled else "0",
                        r.uk_applicability_mode,
                        r.uk_authority_mode,
                        r.uk_source_purity_lane,
                        "1" if r.uk_source_semantics_clean else "0",
                        "1" if r.uk_source_first_candidate else "0",
                        json.dumps(list(r.uk_source_first_candidate_reasons), sort_keys=True),
                        r.uk_authority_observation_count,
                        json.dumps(r.uk_authority_observation_rule_counts, sort_keys=True),
                        r.uk_authority_rejection_count,
                        json.dumps(r.uk_authority_rejection_rule_counts, sort_keys=True),
                        r.lowering_observation_count,
                        json.dumps(r.lowering_observation_rule_counts, sort_keys=True),
                        r.lowering_rejection_count,
                        json.dumps(r.lowering_rejection_rule_counts, sort_keys=True),
                        r.blocking_lowering_rejection_count,
                        json.dumps(r.blocking_lowering_rejection_rule_counts, sort_keys=True),
                    ]
            if has_commencement_error:
                row += [r.commencement_error]
            if has_text:
                row += [
                    f"{r.text_score:.4f}" if r.text_score >= 0.0 else "",
                    r.n_text_compared,
                    f"{r.replay_text_score:.4f}" if r.replay_text_score >= 0.0 else "",
                ]
            w.writerow(row)

    print(f"\nResults saved: {out_path}")
    score_witness_count = _save_score_witness_rows(results, label)
    if score_witness_count:
        print(
            f"Score witnesses saved: {_score_witness_path(label)} "
            f"rows={score_witness_count}"
        )
    diagnostic_count = _save_bench_diagnostic_rows(results, label)
    if diagnostic_count:
        print(
            f"Bench diagnostics saved: {_bench_diagnostics_path(label)} "
            f"rows={diagnostic_count}"
        )

    _append_history(results, label, score_witness_count)


# ---------------------------------------------------------------------------
# Show / compare
# ---------------------------------------------------------------------------


def _load_run(label: str) -> list[_BenchResult]:
    path = _BENCH_DIR / f"{label}.csv"
    if not path.exists():
        print(f"No saved run with label '{label}'. Available:", file=sys.stderr)
        for p in sorted(_BENCH_DIR.glob("*.csv")):
            print(f"  {p.stem}", file=sys.stderr)
        sys.exit(1)

    diagnostic_rows_by_statute = _load_bench_diagnostic_rows(label)
    results = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            statute_id = row["statute_id"]
            diagnostic_rows = diagnostic_rows_by_statute.get(statute_id, {})
            rs_raw = row.get("replay_score", "")
            replay_score = float(rs_raw) if rs_raw else -1.0
            n_ops_raw = row.get("n_ops", "0")
            n_ops = int(n_ops_raw) if n_ops_raw else 0
            replay_adjudication_count = int(row.get("replay_adjudication_count", "0") or 0)
            raw_replay_adjudication_kind_counts = row.get("replay_adjudication_kind_counts", "")
            replay_adjudication_kind_counts: dict[str, int] = {}
            if raw_replay_adjudication_kind_counts:
                parsed_replay_adjudication_kind_counts = json.loads(raw_replay_adjudication_kind_counts)
                if isinstance(parsed_replay_adjudication_kind_counts, dict):
                    replay_adjudication_kind_counts = {
                        str(kind): int(count)
                        for kind, count in parsed_replay_adjudication_kind_counts.items()
                        if isinstance(count, int)
                    }
            residual_claim_fields_present = "uk_residual_claim_tier" in row
            uk_residual_claim_tier = row.get("uk_residual_claim_tier", "") or "UNRESOLVED"
            uk_residual_claim_kind = (
                row.get("uk_residual_claim_kind", "")
                or ("unknown_legacy_missing" if not residual_claim_fields_present else "unknown")
            )
            uk_residual_claim_comparison_class = row.get("uk_residual_claim_comparison_class", "")
            uk_residual_claim_core_comparison = row.get(
                "uk_residual_claim_core_comparison",
                "0",
            ) in ("1", "True", "true")
            uk_residual_only_in_replayed_count = int(
                row.get("uk_residual_only_in_replayed_count", "0") or 0
            )
            uk_residual_only_in_oracle_count = int(
                row.get("uk_residual_only_in_oracle_count", "0") or 0
            )
            uk_residual_section_claim_count = int(
                row.get("uk_residual_section_claim_count", "0") or 0
            )
            uk_residual_section_claim_emitted = row.get(
                "uk_residual_section_claim_emitted",
                "0",
            ) in ("1", "True", "true")
            ts_raw = row.get("text_score", "")
            text_score = float(ts_raw) if ts_raw else -1.0
            rts_raw = row.get("replay_text_score", "")
            replay_text_score = float(rts_raw) if rts_raw else -1.0
            n_text_cmp_raw = row.get("n_text_compared", "0")
            n_text_compared = int(n_text_cmp_raw) if n_text_cmp_raw else 0
            # commencement_score is stored in the 'score' column (primary) when the
            # CSV has a 'raw_score' column (commencement-mode run).  A dedicated
            # 'commencement_score' column does not exist in the CSV — the save/load
            # convention is: score=commencement, raw_score=raw EID score when
            # commencement was active.
            has_raw_score_col = "raw_score" in (reader.fieldnames or [])
            if has_raw_score_col:
                # score column = commencement score; raw_score column = raw EID score
                commencement_score = float(row["score"])
            else:
                cs_raw = row.get("commencement_score", "")
                commencement_score = float(cs_raw) if cs_raw else -1.0
            n_commenced_raw = row.get("n_commenced_eids", "0")
            n_commenced_eids = int(n_commenced_raw) if n_commenced_raw else 0
            rcs_raw = row.get("replay_commencement_score", "")
            replay_commencement_score = float(rcs_raw) if rcs_raw else -1.0
            # When raw_score column is present, r.score holds the raw EID score
            # so that _print_report comparisons (r.replay_score vs r.score) remain
            # consistent (both are raw scores in the non-commencement branch).
            raw_score_val = float(row["raw_score"]) if has_raw_score_col else float(row["score"])
            n_effects = int(row["n_effects"])
            n_effect_feed_pages = int(row.get("n_effect_feed_pages", n_effects) or n_effects)
            n_effect_rows = int(row.get("n_effect_rows", n_effects) or n_effects)
            effect_feed_rejection_count = int(row.get("effect_feed_rejection_count", "0") or 0)
            effect_feed_rejection_rule_counts = _parse_json_rule_counts(
                row.get("effect_feed_rejection_rule_counts", "")
            )
            effect_feed_observation_count = int(row.get("effect_feed_observation_count", "0") or 0)
            effect_feed_observation_rule_counts = _parse_json_rule_counts(
                row.get("effect_feed_observation_rule_counts", "")
            )
            effect_feed_count_error = row.get("effect_feed_count_error", "")
            bench_exception_count = int(row.get("bench_exception_count", "0") or 0)
            bench_exception_rule_counts = _parse_json_rule_counts(
                row.get("bench_exception_rule_counts", "")
            )
            bench_exception_observations = _parse_json_observation_rows(
                row.get("bench_exception_observations", "")
            )
            if not bench_exception_observations:
                bench_exception_observations = diagnostic_rows.get("bench_exception", ())
            source_parse_observations = diagnostic_rows.get("source_parse", ())
            effect_feed_observations = diagnostic_rows.get("effect_feed", ())
            effect_diagnostics = (
                diagnostic_rows.get("source_acquisition", ())
                + diagnostic_rows.get("effect_source_pathology", ())
                + diagnostic_rows.get("manual_compile_frontier", ())
                + diagnostic_rows.get("effect_diagnostic", ())
            )
            uk_authority_observations = diagnostic_rows.get("authority", ())
            lowering_rejections = diagnostic_rows.get("lowering", ())
            replay_adjudications = diagnostic_rows.get("replay_adjudication", ())
            source_parse_rejection_count = int(row.get("source_parse_rejection_count", "0") or 0)
            source_parse_rejection_rule_counts = _parse_json_rule_counts(
                row.get("source_parse_rejection_rule_counts", "")
            )
            source_parse_observation_count = int(row.get("source_parse_observation_count", "0") or 0)
            source_parse_observation_rule_counts = _parse_json_rule_counts(
                row.get("source_parse_observation_rule_counts", "")
            )
            effect_source_pathology_counts = _parse_json_rule_counts(
                row.get("effect_source_pathology_counts", "")
            )
            manual_compile_status_counts = _parse_json_rule_counts(
                row.get("manual_compile_status_counts", "")
            )
            manual_compile_rule_counts = _parse_json_rule_counts(
                row.get("manual_compile_rule_counts", "")
            )
            source_acquisition_observation_count = int(
                row.get("source_acquisition_observation_count", "0") or 0
            )
            source_acquisition_observation_rule_counts = _parse_json_rule_counts(
                row.get("source_acquisition_observation_rule_counts", "")
            )
            source_acquisition_rejection_count = int(row.get("source_acquisition_rejection_count", "0") or 0)
            source_acquisition_rejection_rule_counts = _parse_json_rule_counts(
                row.get("source_acquisition_rejection_rule_counts", "")
            )
            enacted_source_status = row.get("enacted_source_status") or "unknown"
            oracle_source_status = row.get("oracle_source_status") or "unknown"
            enacted_source_size = int(row.get("enacted_source_size", "0") or 0)
            oracle_source_size = int(row.get("oracle_source_size", "0") or 0)
            enacted_source_sha256 = row.get("enacted_source_sha256") or ""
            oracle_source_sha256 = row.get("oracle_source_sha256") or ""
            enacted_source_url = (
                row.get("enacted_source_url")
                or f"{_LEG_BASE}/{row['statute_id']}/enacted/data.xml"
            )
            oracle_source_url = row.get("oracle_source_url") or f"{_LEG_BASE}/{row['statute_id']}/data.xml"
            oracle_alignment_changed_count = int(row.get("oracle_alignment_changed_count", "0") or 0)
            oracle_alignment_oracle_assigned_count = int(
                row.get("oracle_alignment_oracle_assigned_count", "0") or 0
            )
            oracle_alignment_local_fallback_count = int(
                row.get("oracle_alignment_local_fallback_count", "0") or 0
            )
            oracle_alignment_transparent_wrapper_cleared_count = int(
                row.get("oracle_alignment_transparent_wrapper_cleared_count", "0") or 0
            )
            oracle_alignment_before_node_count = int(
                row.get("oracle_alignment_before_node_count", "0") or 0
            )
            oracle_alignment_after_node_count = int(
                row.get("oracle_alignment_after_node_count", "0") or 0
            )
            oracle_alignment_node_count_mismatch = row.get(
                "oracle_alignment_node_count_mismatch",
                "0",
            ) in (
                "1",
                "True",
                "true",
            )
            raw_alignment_match_method_counts = row.get("oracle_alignment_match_method_counts", "")
            oracle_alignment_match_method_counts: dict[str, int] = {}
            if raw_alignment_match_method_counts:
                parsed_method_counts = json.loads(raw_alignment_match_method_counts)
                if isinstance(parsed_method_counts, dict):
                    oracle_alignment_match_method_counts = {
                        str(method): int(count)
                        for method, count in parsed_method_counts.items()
                        if isinstance(count, int)
                    }
            uk_metadata_backfill_enabled = row.get("uk_metadata_backfill_enabled", "1") in (
                "1",
                "True",
                "true",
            )
            uk_oracle_alignment_enabled = row.get("uk_oracle_alignment_enabled", "1") in (
                "1",
                "True",
                "true",
            )
            uk_metadata_only_effects_enabled = row.get(
                "uk_metadata_only_effects_enabled",
                "1",
            ) in (
                "1",
                "True",
                "true",
            )
            uk_applicability_mode = row.get("uk_applicability_mode") or "effective_date_plus_feed_applied"
            uk_authority_mode = row.get("uk_authority_mode") or "current_mixed"
            uk_source_purity_lane = row.get("uk_source_purity_lane", "")
            uk_source_semantics_clean = row.get("uk_source_semantics_clean", "0") in (
                "1",
                "True",
                "true",
            )
            uk_source_first_candidate = row.get("uk_source_first_candidate", "0") in (
                "1",
                "True",
                "true",
            )
            raw_source_first_candidate_reasons = row.get(
                "uk_source_first_candidate_reasons",
                "",
            )
            uk_source_first_candidate_reasons: tuple[str, ...] = ()
            if raw_source_first_candidate_reasons:
                parsed_source_first_candidate_reasons = json.loads(
                    raw_source_first_candidate_reasons
                )
                if isinstance(parsed_source_first_candidate_reasons, list):
                    uk_source_first_candidate_reasons = tuple(
                        str(reason) for reason in parsed_source_first_candidate_reasons
                    )
            uk_authority_observation_count = int(row.get("uk_authority_observation_count", "0") or 0)
            raw_authority_observation_rule_counts = row.get("uk_authority_observation_rule_counts", "")
            uk_authority_observation_rule_counts: dict[str, int] = {}
            if raw_authority_observation_rule_counts:
                parsed_authority_observation_rule_counts = json.loads(
                    raw_authority_observation_rule_counts
                )
                if isinstance(parsed_authority_observation_rule_counts, dict):
                    uk_authority_observation_rule_counts = {
                        str(rule_id): int(count)
                        for rule_id, count in parsed_authority_observation_rule_counts.items()
                        if isinstance(count, int)
                    }
            uk_authority_rejection_count = int(row.get("uk_authority_rejection_count", "0") or 0)
            raw_authority_rejection_rule_counts = row.get("uk_authority_rejection_rule_counts", "")
            uk_authority_rejection_rule_counts: dict[str, int] = {}
            if raw_authority_rejection_rule_counts:
                parsed_authority_rule_counts = json.loads(raw_authority_rejection_rule_counts)
                if isinstance(parsed_authority_rule_counts, dict):
                    uk_authority_rejection_rule_counts = {
                        str(rule_id): int(count)
                        for rule_id, count in parsed_authority_rule_counts.items()
                        if isinstance(count, int)
                    }
            lowering_rejection_count = int(row.get("lowering_rejection_count", "0") or 0)
            raw_lowering_rejection_rule_counts = row.get("lowering_rejection_rule_counts", "")
            lowering_rejection_rule_counts: dict[str, int] = {}
            if raw_lowering_rejection_rule_counts:
                parsed_lowering_rule_counts = json.loads(raw_lowering_rejection_rule_counts)
                if isinstance(parsed_lowering_rule_counts, dict):
                    lowering_rejection_rule_counts = {
                        str(rule_id): int(count)
                        for rule_id, count in parsed_lowering_rule_counts.items()
                        if isinstance(count, int)
                    }
            lowering_observation_count = int(
                row.get("lowering_observation_count", "") or lowering_rejection_count
            )
            raw_lowering_observation_rule_counts = row.get(
                "lowering_observation_rule_counts",
                "",
            )
            lowering_observation_rule_counts: dict[str, int] = dict(lowering_rejection_rule_counts)
            if raw_lowering_observation_rule_counts:
                parsed_lowering_observation_rule_counts = json.loads(
                    raw_lowering_observation_rule_counts
                )
                if isinstance(parsed_lowering_observation_rule_counts, dict):
                    lowering_observation_rule_counts = {
                        str(rule_id): int(count)
                        for rule_id, count in parsed_lowering_observation_rule_counts.items()
                        if isinstance(count, int)
                    }
            blocking_lowering_rejection_count = int(row.get("blocking_lowering_rejection_count", "0") or 0)
            raw_blocking_lowering_rejection_rule_counts = row.get(
                "blocking_lowering_rejection_rule_counts",
                "",
            )
            blocking_lowering_rejection_rule_counts: dict[str, int] = {}
            if raw_blocking_lowering_rejection_rule_counts:
                parsed_blocking_lowering_rule_counts = json.loads(
                    raw_blocking_lowering_rejection_rule_counts
                )
                if isinstance(parsed_blocking_lowering_rule_counts, dict):
                    blocking_lowering_rejection_rule_counts = {
                        str(rule_id): int(count)
                        for rule_id, count in parsed_blocking_lowering_rule_counts.items()
                        if isinstance(count, int)
                    }
            comparison_class = row.get("comparison_class", "")
            core_benchmark_raw = row.get("core_benchmark", "")
            if core_benchmark_raw:
                core_benchmark = core_benchmark_raw in ("1", "True", "true")
            elif comparison_class:
                core_benchmark = is_core_uk_comparison(comparison_class)
            else:
                core_benchmark = True
            results.append(
                _BenchResult(
                    statute_id=statute_id,
                    act_type=row["act_type"],
                    year=int(row["year"]),
                    n_effects=n_effects,
                    n_effect_feed_pages=n_effect_feed_pages,
                    n_effect_rows=n_effect_rows,
                    effect_feed_rejection_count=effect_feed_rejection_count,
                    effect_feed_rejection_rule_counts=effect_feed_rejection_rule_counts,
                    effect_feed_observation_count=effect_feed_observation_count,
                    effect_feed_observation_rule_counts=effect_feed_observation_rule_counts,
                    effect_feed_observations=effect_feed_observations,
                    effect_feed_count_error=effect_feed_count_error,
                    bench_exception_count=bench_exception_count,
                    bench_exception_rule_counts=bench_exception_rule_counts,
                    bench_exception_observations=bench_exception_observations,
                    source_parse_rejection_count=source_parse_rejection_count,
                    source_parse_rejection_rule_counts=source_parse_rejection_rule_counts,
                    source_parse_observation_count=source_parse_observation_count,
                    source_parse_observation_rule_counts=source_parse_observation_rule_counts,
                    source_parse_observations=source_parse_observations,
                    effect_diagnostics=effect_diagnostics,
                    effect_source_pathology_counts=effect_source_pathology_counts,
                    manual_compile_status_counts=manual_compile_status_counts,
                    manual_compile_rule_counts=manual_compile_rule_counts,
                    source_acquisition_observation_count=source_acquisition_observation_count,
                    source_acquisition_observation_rule_counts=(
                        source_acquisition_observation_rule_counts
                    ),
                    source_acquisition_rejection_count=source_acquisition_rejection_count,
                    source_acquisition_rejection_rule_counts=source_acquisition_rejection_rule_counts,
                    enacted_source_status=enacted_source_status,
                    oracle_source_status=oracle_source_status,
                    enacted_source_size=enacted_source_size,
                    oracle_source_size=oracle_source_size,
                    enacted_source_sha256=enacted_source_sha256,
                    oracle_source_sha256=oracle_source_sha256,
                    enacted_source_url=enacted_source_url,
                    oracle_source_url=oracle_source_url,
                    n_enacted_eids=int(row["n_enacted_eids"]),
                    n_oracle_eids=int(row["n_oracle_eids"]),
                    n_common=int(row["n_common"]),
                    score=raw_score_val,
                    status=row["status"],
                    error=row.get("error", ""),
                    n_replayed_eids=int(row.get("n_replayed_eids", 0) or 0),
                    n_replay_common=int(row.get("n_replay_common", 0) or 0),
                    replay_score=replay_score,
                    n_ops=n_ops,
                    replay_error=row.get("replay_error", ""),
                    replay_adjudication_count=replay_adjudication_count,
                    replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                    replay_adjudications=replay_adjudications,
                    uk_residual_claim_tier=uk_residual_claim_tier,
                    uk_residual_claim_kind=uk_residual_claim_kind,
                    uk_residual_claim_comparison_class=uk_residual_claim_comparison_class,
                    uk_residual_claim_core_comparison=uk_residual_claim_core_comparison,
                    uk_residual_only_in_replayed_count=uk_residual_only_in_replayed_count,
                    uk_residual_only_in_oracle_count=uk_residual_only_in_oracle_count,
                    uk_residual_section_claim_count=uk_residual_section_claim_count,
                    uk_residual_section_claim_emitted=uk_residual_section_claim_emitted,
                    oracle_alignment_changed_count=oracle_alignment_changed_count,
                    oracle_alignment_oracle_assigned_count=oracle_alignment_oracle_assigned_count,
                    oracle_alignment_local_fallback_count=oracle_alignment_local_fallback_count,
                    oracle_alignment_transparent_wrapper_cleared_count=(
                        oracle_alignment_transparent_wrapper_cleared_count
                    ),
                    oracle_alignment_before_node_count=oracle_alignment_before_node_count,
                    oracle_alignment_after_node_count=oracle_alignment_after_node_count,
                    oracle_alignment_node_count_mismatch=oracle_alignment_node_count_mismatch,
                    oracle_alignment_match_method_counts=oracle_alignment_match_method_counts,
                    uk_metadata_backfill_enabled=uk_metadata_backfill_enabled,
                    uk_oracle_alignment_enabled=uk_oracle_alignment_enabled,
                    uk_metadata_only_effects_enabled=uk_metadata_only_effects_enabled,
                    uk_applicability_mode=uk_applicability_mode,
                    uk_authority_mode=uk_authority_mode,
                    uk_source_purity_lane=uk_source_purity_lane,
                    uk_source_semantics_clean=uk_source_semantics_clean,
                    uk_source_first_candidate=uk_source_first_candidate,
                    uk_source_first_candidate_reasons=uk_source_first_candidate_reasons,
                    uk_authority_observation_count=uk_authority_observation_count,
                    uk_authority_observation_rule_counts=uk_authority_observation_rule_counts,
                    uk_authority_rejection_count=uk_authority_rejection_count,
                    uk_authority_rejection_rule_counts=uk_authority_rejection_rule_counts,
                    uk_authority_observations=uk_authority_observations,
                    lowering_observation_count=lowering_observation_count,
                    lowering_observation_rule_counts=lowering_observation_rule_counts,
                    lowering_rejection_count=lowering_rejection_count,
                    lowering_rejection_rule_counts=lowering_rejection_rule_counts,
                    blocking_lowering_rejection_count=blocking_lowering_rejection_count,
                    blocking_lowering_rejection_rule_counts=blocking_lowering_rejection_rule_counts,
                    lowering_rejections=lowering_rejections,
                    text_score=text_score,
                    n_text_compared=n_text_compared,
                    replay_text_score=replay_text_score,
                    commencement_score=commencement_score,
                    n_commenced_eids=n_commenced_eids,
                    replay_commencement_score=replay_commencement_score,
                    commencement_error=row.get("commencement_error", ""),
                    comparison_class=comparison_class,
                    core_benchmark=core_benchmark,
                )
            )
    return results


def _show_run(
    label: str,
    *,
    replay_adjudication_sample_kinds: Sequence[str] = (),
    replay_adjudication_sample_limit: int = 5,
) -> None:
    results = _load_run(label)
    _print_report(
        results,
        label,
        replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
    )
    score_witness_path = _score_witness_path(label)
    score_witness_count = _count_csv_data_rows(score_witness_path)
    if score_witness_count:
        print(f"\nScore witness sidecar: {score_witness_path} rows={score_witness_count}")
    diagnostics_path = _bench_diagnostics_path(label)
    diagnostics_count = _count_jsonl_rows(diagnostics_path)
    if diagnostics_count:
        print(f"Bench diagnostics sidecar: {diagnostics_path} rows={diagnostics_count}")


def _primary_score_mode(results: list[_BenchResult]) -> str:
    commencement_count = sum(1 for r in results if r.commencement_score >= 0.0)
    if commencement_count == 0:
        return "raw"
    if commencement_count == len(results):
        return "commencement"
    return "mixed"


def _compare_runs(label_a: str, label_b: str) -> None:
    def _primary_scores(results: list[_BenchResult]) -> dict[str, float]:
        # Prefer commencement score as primary when available.
        return {
            r.statute_id: (r.commencement_score if r.commencement_score >= 0.0 else r.score)
            for r in results
        }

    def _status_counts(results: list[_BenchResult], field: str) -> dict[str, int]:
        return dict(sorted(Counter(str(getattr(r, field) or "unknown") for r in results).items()))

    def _regime_counts(results: list[_BenchResult]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for r in results:
            key = (
                f"metadata_backfill={int(r.uk_metadata_backfill_enabled)}"
                f";oracle_alignment={int(r.uk_oracle_alignment_enabled)}"
                f";metadata_only_effects={int(r.uk_metadata_only_effects_enabled)}"
                f";applicability={r.uk_applicability_mode}"
                f";authority={r.uk_authority_mode}"
            )
            counts[key] += 1
        return dict(sorted(counts.items()))

    def _sum_field(results: list[_BenchResult], field: str) -> int:
        return sum(int(getattr(r, field) or 0) for r in results)

    def _merged_rule_counts(results: list[_BenchResult], field: str) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for r in results:
            counts.update(getattr(r, field) or {})
        return dict(sorted(counts.items()))

    def _alignment_counts(results: list[_BenchResult]) -> dict[str, int]:
        return {
            "changed": _sum_field(results, "oracle_alignment_changed_count"),
            "oracle_assigned": _sum_field(results, "oracle_alignment_oracle_assigned_count"),
            "local_fallback": _sum_field(results, "oracle_alignment_local_fallback_count"),
            "transparent_wrapper_cleared": _sum_field(
                results,
                "oracle_alignment_transparent_wrapper_cleared_count",
            ),
            "before_nodes": _sum_field(results, "oracle_alignment_before_node_count"),
            "after_nodes": _sum_field(results, "oracle_alignment_after_node_count"),
            "node_mismatch_rows": sum(1 for r in results if r.oracle_alignment_node_count_mismatch),
        }

    def _average_common_field(
        results: list[_BenchResult],
        field: str,
        statute_ids: set[str],
    ) -> tuple[int, float | None]:
        values = [
            float(getattr(result, field))
            for result in results
            if result.statute_id in statute_ids and float(getattr(result, field)) >= 0.0
        ]
        if not values:
            return 0, None
        return len(values), sum(values) / len(values)

    def _format_score_summary(summary: tuple[int, float | None]) -> str:
        count, average = summary
        if average is None:
            return f"n={count} avg=n/a"
        return f"n={count} avg={average:.1%}"

    results_a = _load_run(label_a)
    results_b = _load_run(label_b)
    score_witness_path_a = _score_witness_path(label_a)
    score_witness_path_b = _score_witness_path(label_b)
    score_witness_count_a = _count_csv_data_rows(score_witness_path_a)
    score_witness_count_b = _count_csv_data_rows(score_witness_path_b)
    results_by_id_b = {result.statute_id: result for result in results_b}
    a = _primary_scores(results_a)
    b = _primary_scores(results_b)
    common = set(a) & set(b)
    only_a = set(a) - set(b)
    only_b = set(b) - set(a)

    improved = [(k, a[k], b[k]) for k in common if b[k] > a[k] + 0.001]
    regressed = [(k, a[k], b[k]) for k in common if b[k] < a[k] - 0.001]

    avg_a = sum(a[k] for k in common) / len(common) if common else 0
    avg_b = sum(b[k] for k in common) / len(common) if common else 0

    print(f"\n=== UK Bench Compare: {label_a} -> {label_b} ===")
    print(f"Score mode: {_primary_score_mode(results_a)} -> {_primary_score_mode(results_b)}")
    print(f"Common statutes: {len(common)}")
    print(f"Only in {label_a}: {len(only_a)}")
    print(f"Only in {label_b}: {len(only_b)}")
    print(
        "Score witness sidecars: "
        f"{label_a}={score_witness_path_a} rows={score_witness_count_a} -> "
        f"{label_b}={score_witness_path_b} rows={score_witness_count_b}"
    )
    print(f"Row statuses: {_status_counts(results_a, 'status')} -> {_status_counts(results_b, 'status')}")
    print(
        "Comparison classes: "
        f"{_status_counts(results_a, 'comparison_class')} -> "
        f"{_status_counts(results_b, 'comparison_class')}"
    )
    print(
        "Core benchmark rows: "
        f"{sum(1 for r in results_a if r.core_benchmark)} -> "
        f"{sum(1 for r in results_b if r.core_benchmark)}"
    )
    print(
        "Source status enacted: "
        f"{_status_counts(results_a, 'enacted_source_status')} -> "
        f"{_status_counts(results_b, 'enacted_source_status')}"
    )
    print(
        "Source status oracle: "
        f"{_status_counts(results_a, 'oracle_source_status')} -> "
        f"{_status_counts(results_b, 'oracle_source_status')}"
    )
    print(f"Replay regimes: {_regime_counts(results_a)} -> {_regime_counts(results_b)}")
    print(
        "Source parse observations: "
        f"{_sum_field(results_a, 'source_parse_observation_count')} "
        f"{_merged_rule_counts(results_a, 'source_parse_observation_rule_counts')} -> "
        f"{_sum_field(results_b, 'source_parse_observation_count')} "
        f"{_merged_rule_counts(results_b, 'source_parse_observation_rule_counts')}"
    )
    print(
        "Source parse rejections: "
        f"{_sum_field(results_a, 'source_parse_rejection_count')} "
        f"{_merged_rule_counts(results_a, 'source_parse_rejection_rule_counts')} -> "
        f"{_sum_field(results_b, 'source_parse_rejection_count')} "
        f"{_merged_rule_counts(results_b, 'source_parse_rejection_rule_counts')}"
    )
    print(
        "Effect source pathologies: "
        f"{_merged_rule_counts(results_a, 'effect_source_pathology_counts')} -> "
        f"{_merged_rule_counts(results_b, 'effect_source_pathology_counts')}"
    )
    print(
        "Manual compile frontier statuses: "
        f"{_merged_rule_counts(results_a, 'manual_compile_status_counts')} -> "
        f"{_merged_rule_counts(results_b, 'manual_compile_status_counts')}"
    )
    print(
        "Manual compile frontier rules: "
        f"{_merged_rule_counts(results_a, 'manual_compile_rule_counts')} -> "
        f"{_merged_rule_counts(results_b, 'manual_compile_rule_counts')}"
    )
    print(
        "Source acquisition observations: "
        f"{_sum_field(results_a, 'source_acquisition_observation_count')} "
        f"{_merged_rule_counts(results_a, 'source_acquisition_observation_rule_counts')} -> "
        f"{_sum_field(results_b, 'source_acquisition_observation_count')} "
        f"{_merged_rule_counts(results_b, 'source_acquisition_observation_rule_counts')}"
    )
    print(
        "Source acquisition rejections: "
        f"{_sum_field(results_a, 'source_acquisition_rejection_count')} "
        f"{_merged_rule_counts(results_a, 'source_acquisition_rejection_rule_counts')} -> "
        f"{_sum_field(results_b, 'source_acquisition_rejection_count')} "
        f"{_merged_rule_counts(results_b, 'source_acquisition_rejection_rule_counts')}"
    )
    print(
        "Bench exceptions: "
        f"{_sum_field(results_a, 'bench_exception_count')} "
        f"{_merged_rule_counts(results_a, 'bench_exception_rule_counts')} -> "
        f"{_sum_field(results_b, 'bench_exception_count')} "
        f"{_merged_rule_counts(results_b, 'bench_exception_rule_counts')}"
    )
    print(
        "Effect-feed observations: "
        f"{_sum_field(results_a, 'effect_feed_observation_count')} "
        f"{_merged_rule_counts(results_a, 'effect_feed_observation_rule_counts')} -> "
        f"{_sum_field(results_b, 'effect_feed_observation_count')} "
        f"{_merged_rule_counts(results_b, 'effect_feed_observation_rule_counts')}"
    )
    print(
        "Effect-feed rejections: "
        f"{_sum_field(results_a, 'effect_feed_rejection_count')} "
        f"{_merged_rule_counts(results_a, 'effect_feed_rejection_rule_counts')} -> "
        f"{_sum_field(results_b, 'effect_feed_rejection_count')} "
        f"{_merged_rule_counts(results_b, 'effect_feed_rejection_rule_counts')}"
    )
    print(
        "Effect-feed count errors: "
        f"{sum(1 for r in results_a if r.effect_feed_count_error)} -> "
        f"{sum(1 for r in results_b if r.effect_feed_count_error)}"
    )
    print(
        "Authority observations: "
        f"{_sum_field(results_a, 'uk_authority_observation_count')} "
        f"{_merged_rule_counts(results_a, 'uk_authority_observation_rule_counts')} -> "
        f"{_sum_field(results_b, 'uk_authority_observation_count')} "
        f"{_merged_rule_counts(results_b, 'uk_authority_observation_rule_counts')}"
    )
    print(
        "Blocking authority rejections: "
        f"{_sum_field(results_a, 'uk_authority_rejection_count')} "
        f"{_merged_rule_counts(results_a, 'uk_authority_rejection_rule_counts')} -> "
        f"{_sum_field(results_b, 'uk_authority_rejection_count')} "
        f"{_merged_rule_counts(results_b, 'uk_authority_rejection_rule_counts')}"
    )
    print(
        "Replay adjudications: "
        f"{_sum_field(results_a, 'replay_adjudication_count')} "
        f"{_merged_rule_counts(results_a, 'replay_adjudication_kind_counts')} -> "
        f"{_sum_field(results_b, 'replay_adjudication_count')} "
        f"{_merged_rule_counts(results_b, 'replay_adjudication_kind_counts')}"
    )
    print(
        "Lowering observations: "
        f"{_sum_field(results_a, 'lowering_observation_count')} "
        f"{_merged_rule_counts(results_a, 'lowering_observation_rule_counts')} -> "
        f"{_sum_field(results_b, 'lowering_observation_count')} "
        f"{_merged_rule_counts(results_b, 'lowering_observation_rule_counts')}"
    )
    print(
        "Lowering rejections: "
        f"total={_sum_field(results_a, 'lowering_rejection_count')} "
        f"blocking={_sum_field(results_a, 'blocking_lowering_rejection_count')} "
        f"{_merged_rule_counts(results_a, 'lowering_rejection_rule_counts')} "
        "-> "
        f"total={_sum_field(results_b, 'lowering_rejection_count')} "
        f"blocking={_sum_field(results_b, 'blocking_lowering_rejection_count')} "
        f"{_merged_rule_counts(results_b, 'lowering_rejection_rule_counts')}"
    )
    print(
        "Blocking lowering rules: "
        f"{_merged_rule_counts(results_a, 'blocking_lowering_rejection_rule_counts')} -> "
        f"{_merged_rule_counts(results_b, 'blocking_lowering_rejection_rule_counts')}"
    )
    print(f"Oracle alignment: {_alignment_counts(results_a)} -> {_alignment_counts(results_b)}")
    print(
        "Oracle alignment methods: "
        f"{_merged_rule_counts(results_a, 'oracle_alignment_match_method_counts')} -> "
        f"{_merged_rule_counts(results_b, 'oracle_alignment_match_method_counts')}"
    )
    print(
        "Text scores: "
        f"{_format_score_summary(_average_common_field(results_a, 'text_score', common))} -> "
        f"{_format_score_summary(_average_common_field(results_b, 'text_score', common))}"
    )
    print(
        "Replay text scores: "
        f"{_format_score_summary(_average_common_field(results_a, 'replay_text_score', common))} -> "
        f"{_format_score_summary(_average_common_field(results_b, 'replay_text_score', common))}"
    )
    print(f"Average: {avg_a:.1%} -> {avg_b:.1%} ({avg_b - avg_a:+.1%})")
    print(f"Improved: {len(improved)}, Regressed: {len(regressed)}")

    if regressed:
        regressed.sort(key=lambda x: x[1] - x[2], reverse=True)
        print(f"\nRegressions (top {min(10, len(regressed))}):")
        for sid, va, vb in regressed[:10]:
            print(
                f"  {sid}: {va:.1%} -> {vb:.1%} ({vb - va:+.1%}) "
                f"{_bench_row_evidence_context(results_by_id_b[sid])}"
            )

    if improved:
        improved.sort(key=lambda x: x[2] - x[1], reverse=True)
        print(f"\nImprovements (top {min(10, len(improved))}):")
        for sid, va, vb in improved[:10]:
            print(
                f"  {sid}: {va:.1%} -> {vb:.1%} ({vb - va:+.1%}) "
                f"{_bench_row_evidence_context(results_by_id_b[sid])}"
            )


# ---------------------------------------------------------------------------
# Corpus CSV build
# ---------------------------------------------------------------------------


def _build_corpus_csv(archive: Farchive, types: Optional[frozenset[str]] = None) -> None:
    """Build or refresh data/uk/bench_corpus.csv from the archive."""
    print("Building UK bench corpus index from archive...")
    entries = _build_corpus_index(archive, types=types)
    print(f"  Found {len(entries)} statutes with enacted + consolidated XML")

    _CORPUS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(_CORPUS_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_CORPUS_FIELDNAMES)
        w.writeheader()
        for e in entries:
            w.writerow(
                {
                    "statute_id": e["statute_id"],
                    "type": e["type"],
                    "year": e["year"],
                    "has_enacted": str(e["has_enacted"]),
                    "has_consolidated": str(e["has_consolidated"]),
                    "n_effects": e["n_effects"],
                    "n_effect_feed_pages": e["n_effect_feed_pages"],
                    "enacted_url": e["enacted_url"],
                    "current_url": e["current_url"],
                    "enacted_source_status": e["enacted_source_status"],
                    "oracle_source_status": e["oracle_source_status"],
                    "enacted_source_size": e["enacted_source_size"],
                    "oracle_source_size": e["oracle_source_size"],
                    "enacted_source_sha256": e["enacted_source_sha256"],
                    "oracle_source_sha256": e["oracle_source_sha256"],
                }
            )

    print(f"  Written: {_CORPUS_CSV}")
    tc = Counter(e["type"] for e in entries)
    for t, n in sorted(tc.items()):
        print(f"    {t}: {n}")


def _parse_uk_statute_id(statute_id: str) -> tuple[str, int]:
    parts = statute_id.split("/")
    if len(parts) < 3 or not parts[1].isdigit():
        raise ValueError(f"invalid UK statute id in corpus CSV: {statute_id!r}")
    return parts[0], int(parts[1])


def _bool_cell(value: object, *, default: bool) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _int_cell(value: object, *, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(str(value).strip())


def _uk_corpus_entry_from_row(row: dict[str, object]) -> dict[str, object]:
    statute_id = str(row.get("statute_id") or row.get("id") or "").strip()
    if not statute_id:
        raise ValueError("UK corpus CSV row is missing statute_id")
    act_type, year = _parse_uk_statute_id(statute_id)
    act_type = str(row.get("type") or act_type)
    year = _int_cell(row.get("year"), default=year)
    n_effects = _int_cell(row.get("n_effects"), default=0)
    n_effect_feed_pages = _int_cell(row.get("n_effect_feed_pages"), default=n_effects)
    return {
        "statute_id": statute_id,
        "type": act_type,
        "year": year,
        "has_enacted": _bool_cell(row.get("has_enacted"), default=True),
        "has_consolidated": _bool_cell(row.get("has_consolidated"), default=True),
        "n_effects": n_effects,
        "n_effect_feed_pages": n_effect_feed_pages,
        "enacted_url": row.get("enacted_url") or f"{_LEG_BASE}/{statute_id}/enacted/data.xml",
        "current_url": row.get("current_url") or f"{_LEG_BASE}/{statute_id}/data.xml",
        "enacted_source_status": row.get("enacted_source_status") or "unknown",
        "oracle_source_status": row.get("oracle_source_status") or "unknown",
        "enacted_source_size": _int_cell(row.get("enacted_source_size"), default=0),
        "oracle_source_size": _int_cell(row.get("oracle_source_size"), default=0),
        "enacted_source_sha256": row.get("enacted_source_sha256") or "",
        "oracle_source_sha256": row.get("oracle_source_sha256") or "",
    }


def _load_default_corpus_index() -> dict[str, dict[str, object]]:
    if not _CORPUS_CSV.exists():
        return {}
    entries: dict[str, dict[str, object]] = {}
    with open(_CORPUS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames or "statute_id" not in reader.fieldnames:
            return {}
        for row in reader:
            entry = _uk_corpus_entry_from_row(dict(row))
            entries[str(entry["statute_id"])] = entry
    return entries


def _default_corpus_has_source_status_fields() -> bool:
    if not _CORPUS_CSV.exists():
        return False
    with open(_CORPUS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        fields = set(reader.fieldnames or ())
    return {"enacted_source_status", "oracle_source_status"}.issubset(fields)


def _load_corpus_csv(
    types: Optional[frozenset[str]] = None,
    archive: Optional[Farchive] = None,
    corpus_csv: Optional[Path] = None,
) -> list[dict]:
    """Load bench corpus from CSV.

    The default UK corpus can be built from the archive. A custom corpus is an
    explicit curated benchmark input and must already exist. It may be either a
    full generated UK corpus CSV or a simple one-column ``statute_id`` list.
    """
    corpus_path = corpus_csv or _CORPUS_CSV
    if not corpus_path.exists():
        if corpus_csv is None and archive is not None:
            _build_corpus_csv(archive, types=types)
        else:
            hint = "\nRun: lawvm bench -j uk --corpus-csv" if corpus_csv is None else ""
            raise FileNotFoundError(f"Corpus CSV not found: {corpus_path}{hint}")

    entries = []
    with open(corpus_path, newline="") as f:
        first_line = f.readline()
        f.seek(0)
        first_cells = [cell.strip() for cell in next(csv.reader([first_line]), [])]
        has_header = "statute_id" in first_cells or "id" in first_cells
        if has_header:
            reader = csv.DictReader(f)
            for row in reader:
                entry = _uk_corpus_entry_from_row(dict(row))
                if types and entry["type"] not in types:
                    continue
                entries.append(entry)
        else:
            default_index = _load_default_corpus_index()
            reader = csv.reader(f)
            for row in reader:
                if not row:
                    continue
                statute_id = row[0].strip()
                if not statute_id or statute_id.startswith("#"):
                    continue
                entry = dict(default_index.get(statute_id) or _uk_corpus_entry_from_row({"statute_id": statute_id}))
                if types and entry["type"] not in types:
                    continue
                entries.append(entry)
    return entries


def _effect_bucket(entry: dict[str, object]) -> str:
    n_effects = _int_cell(entry.get("n_effect_feed_pages"), default=_int_cell(entry.get("n_effects"), default=0))
    if n_effects == 0:
        return "0"
    if n_effects <= 5:
        return "1-5"
    if n_effects <= 25:
        return "6-25"
    return "26+"


def _entry_decade(entry: dict[str, object]) -> str:
    year = _int_cell(entry.get("year"), default=0)
    return f"{(year // 10) * 10}s" if year else "unknown"


def _is_source_complete_entry(entry: dict[str, object]) -> bool:
    return (
        str(entry.get("enacted_source_status") or "unknown") == "available"
        and str(entry.get("oracle_source_status") or "unknown") == "available"
    )


def _print_corpus_stats(entries: Sequence[dict[str, object]]) -> None:
    by_type = Counter(str(entry.get("type") or "unknown") for entry in entries)
    by_decade = Counter(_entry_decade(entry) for entry in entries)
    by_effect_bucket = Counter(_effect_bucket(entry) for entry in entries)
    enacted_status = Counter(str(entry.get("enacted_source_status") or "unknown") for entry in entries)
    oracle_status = Counter(str(entry.get("oracle_source_status") or "unknown") for entry in entries)
    source_complete = sum(1 for entry in entries if _is_source_complete_entry(entry))
    effectful = sum(1 for entry in entries if _effect_bucket(entry) != "0")
    source_complete_effectful = sum(
        1 for entry in entries if _is_source_complete_entry(entry) and _effect_bucket(entry) != "0"
    )
    print("\n=== UK Corpus Stats ===")
    print(f"Total rows: {len(entries)}")
    print(f"Source-complete rows: {source_complete}")
    print(f"Effectful rows: {effectful}")
    print(f"Source-complete effectful rows: {source_complete_effectful}")
    print(f"By type: {dict(sorted(by_type.items()))}")
    print(f"By decade: {dict(sorted(by_decade.items()))}")
    print(f"By effect pages: {dict(sorted(by_effect_bucket.items()))}")
    print(f"Enacted source status: {dict(sorted(enacted_status.items()))}")
    print(f"Oracle source status: {dict(sorted(oracle_status.items()))}")


def _stratified_source_complete_sample(
    entries: Sequence[dict[str, object]],
    *,
    size: int,
) -> list[dict[str, object]]:
    if size < 1:
        raise ValueError("--curate-size must be a positive integer")
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for entry in entries:
        if not _is_source_complete_entry(entry):
            continue
        key = (
            str(entry.get("type") or "unknown"),
            _entry_decade(entry),
            _effect_bucket(entry),
        )
        groups.setdefault(key, []).append(entry)
    for group in groups.values():
        group.sort(key=lambda entry: str(entry.get("statute_id") or ""))

    selected: list[dict[str, object]] = []
    group_keys = sorted(groups)
    while len(selected) < size and group_keys:
        next_keys: list[tuple[str, str, str]] = []
        for key in group_keys:
            group = groups[key]
            if group and len(selected) < size:
                selected.append(group.pop(0))
            if group:
                next_keys.append(key)
        group_keys = next_keys
    return selected


def _write_curated_corpus(entries: Sequence[dict[str, object]], *, output: Path, size: int) -> list[dict[str, object]]:
    selected = _stratified_source_complete_sample(entries, size=size)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CORPUS_FIELDNAMES)
        writer.writeheader()
        for entry in selected:
            writer.writerow({field: entry.get(field, "") for field in _CORPUS_FIELDNAMES})
    return selected


# ---------------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------------


def main(args) -> None:  # noqa: ANN001
    if args.history:
        if not _HISTORY_CSV.exists():
            print("No UK bench history yet. Run a bench first.")
            return
        _show_history()
        return

    replay_adjudication_sample_limit = int(
        getattr(args, "replay_adjudication_sample_limit", 5) or 0
    )
    if replay_adjudication_sample_limit < 0:
        print(
            "error: --replay-adjudication-sample-limit must be zero or a positive integer",
            file=sys.stderr,
        )
        sys.exit(2)
    replay_adjudication_sample_kinds = tuple(
        getattr(args, "replay_adjudication_samples", None) or ()
    )

    if args.show:
        _show_run(
            args.show,
            replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
            replay_adjudication_sample_limit=replay_adjudication_sample_limit,
        )
        return

    if args.compare:
        _compare_runs(args.compare[0], args.compare[1])
        return

    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        print("error: --limit must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)
    curate_size = int(getattr(args, "curate_size", 200) or 0)
    if getattr(args, "curate_corpus", None) and curate_size < 1:
        print("error: --curate-size must be a positive integer", file=sys.stderr)
        sys.exit(2)
    _par = getattr(args, "parallel", None)
    if _par is not None and _par < 1:
        print("error: --parallel must be a positive integer", file=sys.stderr)
        sys.exit(2)
    min_year = getattr(args, "min_year", None)
    max_year = getattr(args, "max_year", None)
    if min_year is not None and max_year is not None and min_year > max_year:
        print("error: --min-year must be less than or equal to --max-year", file=sys.stderr)
        sys.exit(2)

    db_path = Path(args.db) if getattr(args, "db", None) else _DEFAULT_DB
    if not db_path.exists():
        print(f"Archive not found: {db_path}", file=sys.stderr)
        print("Run: uv run python scripts/acquire_uk_corpus.py", file=sys.stderr)
        sys.exit(1)

    archive = Farchive(db_path)

    # Determine type filter
    types_arg = getattr(args, "types", None)
    types_filter: Optional[frozenset[str]] = frozenset(types_arg) if types_arg else _DEFAULT_TYPES

    # --corpus-csv: build/refresh corpus CSV and exit
    if getattr(args, "corpus_csv", False):
        _build_corpus_csv(archive, types=types_filter)
        archive.close()
        return

    if getattr(args, "curate_corpus", None) and not getattr(args, "corpus", None):
        if not _default_corpus_has_source_status_fields():
            print("Default UK corpus lacks source-status fields; rebuilding before curation...")
            _build_corpus_csv(archive, types=types_filter)

    label = getattr(args, "label", None) or time.strftime("uk_%Y%m%d_%H%M")

    # Load corpus (build default CSV if needed).  A custom --corpus path is a
    # curated benchmark input, not an acquisition target.
    corpus_arg = getattr(args, "corpus", None)
    corpus_csv = Path(corpus_arg) if corpus_arg else None
    print(f"Loading UK bench corpus (types: {sorted(types_filter or [])})...")
    if corpus_csv is not None:
        print(f"  Corpus path: {corpus_csv}")
        corpus = _load_corpus_csv(types=types_filter, archive=archive, corpus_csv=corpus_csv)
    else:
        corpus = _load_corpus_csv(types=types_filter, archive=archive)
    print(f"  Corpus: {len(corpus)} statutes")

    if not corpus:
        print("No statutes in corpus.", file=sys.stderr)
        archive.close()
        sys.exit(1)

    # Optional year filter
    if min_year:
        corpus = [e for e in corpus if e["year"] >= min_year]
    if max_year:
        corpus = [e for e in corpus if e["year"] <= max_year]
    if min_year or max_year:
        print(f"  Year filter: {min_year or '...'}-{max_year or '...'} → {len(corpus)} statutes")

    if getattr(args, "corpus_stats", False):
        _print_corpus_stats(corpus)
        archive.close()
        return

    curate_corpus = getattr(args, "curate_corpus", None)
    if curate_corpus:
        output = Path(curate_corpus)
        selected = _write_curated_corpus(corpus, output=output, size=curate_size)
        print(f"  Curated source-complete corpus: {output}")
        print(f"  Requested rows: {curate_size}")
        print(f"  Written rows: {len(selected)}")
        _print_corpus_stats(selected)
        archive.close()
        return

    statute_filter = (getattr(args, "statute", None) or "").strip()
    if statute_filter:
        corpus = [e for e in corpus if e["statute_id"] == statute_filter]
        print(f"  Statute filter: {statute_filter} → {len(corpus)} statutes")
        if not corpus:
            print(f"ERROR: statute {statute_filter!r} not found in UK bench corpus.", file=sys.stderr)
            archive.close()
            sys.exit(1)

    if not corpus and limit != 0:
        print("ERROR: no statutes remain after UK bench filters.", file=sys.stderr)
        archive.close()
        sys.exit(1)

    # Optional: apply --limit for quick smoke tests
    if limit is not None:
        corpus = corpus[:limit]
        print(f"  Limited to first {limit} statutes")

    do_replay = getattr(args, "replay", False)
    replay_regime = _normalize_uk_bench_replay_regime(args)
    if do_replay:
        print("Replay mode: will run amendment replay for each statute")
        print(
            "Replay regime: "
            f"metadata_backfill={replay_regime.allow_metadata_backfill} "
            f"oracle_alignment={replay_regime.allow_oracle_alignment} "
            f"metadata_only_effects={replay_regime.allow_metadata_only_effects} "
            f"applicability={replay_regime.applicability_mode} "
            f"authority={replay_regime.authority_mode}"
        )

    do_commencement = not getattr(args, "no_commencement", False)
    if do_commencement:
        print("Commencement mode: filtering EID scores to commenced provisions (use --no-commencement to disable)")

    # Parallelism: None means --parallel was not passed → default to cpu_count.
    # Pass --parallel 1 explicitly to force sequential (useful for debugging).
    workers = _par if _par is not None else max(8, os.cpu_count() or 4)
    print(f"Scoring {len(corpus)} statutes (workers={workers})...")
    results = _run_bench(
        corpus,
        archive,
        do_replay=do_replay,
        repo_root=_REPO_ROOT,
        workers=workers,
        do_commencement=do_commencement,
        allow_metadata_backfill=replay_regime.allow_metadata_backfill,
        allow_oracle_alignment=replay_regime.allow_oracle_alignment,
        applicability_mode=replay_regime.applicability_mode,
        authority_mode=replay_regime.authority_mode,
        allow_metadata_only_effects=replay_regime.allow_metadata_only_effects,
    )
    archive.close()

    if replay_adjudication_sample_kinds:
        _print_report(
            results,
            label,
            replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
            replay_adjudication_sample_limit=replay_adjudication_sample_limit,
        )
    else:
        _print_report(results, label)
    if getattr(args, "no_save", False):
        print("Results not saved (--no-save).")
    else:
        _save_results(results, label)
