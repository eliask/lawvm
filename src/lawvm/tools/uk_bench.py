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
    lawvm bench -j uk --label uk_full --replay --worker-max-tasks 50
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
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Generator,
    Iterable,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Set,
)

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
_TEXT_RATIO_CACHE_MAX_CHARS = 4096
_CURATE_PRESET_SIZES = {
    "canary": 40,
    "tight": 200,
    "stress": 400,
    "modern-canary": 40,
    "modern-tight": 200,
    "hard-canary": 40,
    "hard-tight": 200,
    "hard-stress": 400,
}
_CURATE_PRESET_FILENAMES = {
    "canary": "bench_corpus_smoke.csv",
    "tight": "bench_corpus_tight.csv",
    "stress": "bench_corpus_stress.csv",
    "modern-canary": "bench_corpus_modern_smoke.csv",
    "modern-tight": "bench_corpus_modern_tight.csv",
    "hard-canary": "bench_corpus_hard_smoke.csv",
    "hard-tight": "bench_corpus_hard_tight.csv",
    "hard-stress": "bench_corpus_hard_stress.csv",
}
_CURATE_PRESET_MIN_YEARS = {
    "modern-canary": 1990,
    "modern-tight": 1990,
}
_CURATE_HARD_PRESETS = frozenset({"hard-canary", "hard-tight", "hard-stress"})
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

_PHASE_TIMING_KEYS = (
    "effect_counts",
    "source_load",
    "parse_enacted",
    "parse_oracle",
    "collect_enacted_eids",
    "score_enacted_eids",
    "text_score_enacted",
    "compile_ops",
    "compile_load_effects",
    "compile_filter_order_effects",
    "compile_source_select",
    "compile_source_required_check",
    "compile_source_context",
    "compile_source_extract_current",
    "compile_source_select_enacted",
    "compile_lower_effect",
    "compile_lower_prepare",
    "compile_lower_special",
    "compile_lower_target_prelude",
    "compile_lower_target_setup",
    "compile_lower_targets",
    "compile_lower_tail",
    "compile_source_pathology",
    "compile_filter_effect",
    "compile_final_order",
    "replay",
    "replay_prepare",
    "replay_executor_init",
    "replay_apply_insert",
    "replay_apply_repeal",
    "replay_apply_replace",
    "replay_apply_text_replace",
    "replay_apply_text_repeal",
    "replay_apply_renumber",
    "replay_apply_unknown",
    "replay_fold_text_duplication",
    "replay_to_ir",
    "oracle_align",
    "collect_replay_eids",
    "score_replay_eids",
    "replay_residuals",
    "text_score_replay",
    "replay_exception",
    "commencement",
    "commencement_exception",
)
_PHASE_TIMING_TOTAL_HEADER = "phase_total_s"
_PHASE_TIMING_HEADERS = tuple(f"phase_{name}_s" for name in _PHASE_TIMING_KEYS)

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
_WORKER_SCORE_TEXT: bool = True
_WORKER_RECORD_REPLAY_SUBPHASES: bool = False

_UK_REPLAY_HEAVY_EFFECT_THRESHOLD = 50
_UK_REPLAY_HEAVY_SOURCE_BYTES_THRESHOLD = 12 * 1024 * 1024


def _configure_uk_bench_worker(
    db_path: str,
    do_replay: bool,
    repo_root: str,
    do_commencement: bool,
    allow_metadata_backfill: bool,
    allow_oracle_alignment: bool,
    applicability_mode: str,
    authority_mode: str,
    allow_metadata_only_effects: bool,
    score_text: bool,
    record_replay_subphases: bool,
) -> None:
    """Configure module globals used by ProcessPool worker rows."""
    global _WORKER_DB_PATH, _WORKER_DO_REPLAY, _WORKER_REPO_ROOT, _WORKER_DO_COMMENCEMENT
    global _WORKER_ALLOW_METADATA_BACKFILL, _WORKER_ALLOW_ORACLE_ALIGNMENT
    global _WORKER_ALLOW_METADATA_ONLY_EFFECTS, _WORKER_SCORE_TEXT
    global _WORKER_RECORD_REPLAY_SUBPHASES
    global _WORKER_APPLICABILITY_MODE, _WORKER_AUTHORITY_MODE

    _WORKER_DB_PATH = db_path
    _WORKER_DO_REPLAY = do_replay
    _WORKER_REPO_ROOT = repo_root
    _WORKER_DO_COMMENCEMENT = do_commencement
    _WORKER_ALLOW_METADATA_BACKFILL = allow_metadata_backfill
    _WORKER_ALLOW_ORACLE_ALIGNMENT = allow_oracle_alignment
    _WORKER_APPLICABILITY_MODE = applicability_mode
    _WORKER_AUTHORITY_MODE = authority_mode
    _WORKER_ALLOW_METADATA_ONLY_EFFECTS = allow_metadata_only_effects
    _WORKER_SCORE_TEXT = score_text
    _WORKER_RECORD_REPLAY_SUBPHASES = record_replay_subphases


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
    "effect_feed_rejection_rules",
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
    "max_process_maxrss_kb",
    "max_process_maxrss_statute_id",
    "timestamp",
)


def _uk_bench_row_int(entry: dict[str, object], key: str) -> int:
    value = entry.get(key)
    if value is None or value == "":
        return 0
    try:
        return int(str(value).strip())
    except ValueError:
        return 0


def _uk_bench_parallel_submission_cost(entry: dict[str, object]) -> tuple[int, int, int, int]:
    """Estimate row cost for parallel scheduling, without changing result order."""
    source_size = _uk_bench_row_int(entry, "enacted_source_size") + _uk_bench_row_int(
        entry,
        "oracle_source_size",
    )
    return (
        _uk_bench_row_int(entry, "n_effects"),
        _uk_bench_row_int(entry, "n_effect_feed_pages"),
        source_size,
        _uk_bench_row_int(entry, "year"),
    )


def _uk_bench_replay_heavy_reasons(entry: dict[str, object]) -> tuple[str, ...]:
    """Return memory-risk reasons for rows that should not replay concurrently."""
    reasons: list[str] = []
    n_effects = max(
        _uk_bench_row_int(entry, "n_effects"),
        _uk_bench_row_int(entry, "n_effect_feed_pages"),
    )
    source_size = _uk_bench_row_int(entry, "enacted_source_size") + _uk_bench_row_int(
        entry,
        "oracle_source_size",
    )
    if n_effects >= _UK_REPLAY_HEAVY_EFFECT_THRESHOLD:
        reasons.append(f"effects>={_UK_REPLAY_HEAVY_EFFECT_THRESHOLD}")
    if source_size >= _UK_REPLAY_HEAVY_SOURCE_BYTES_THRESHOLD:
        reasons.append(f"source_mb>={_UK_REPLAY_HEAVY_SOURCE_BYTES_THRESHOLD // (1024 * 1024)}")
    return tuple(reasons)


def _format_uk_bench_row_start(
    *,
    index: int,
    total: int,
    entry: Mapping[str, object],
    do_replay: bool,
) -> str:
    entry_dict = dict(entry)
    statute_id = str(entry.get("statute_id") or "")
    n_effects = _uk_bench_row_int(entry_dict, "n_effects")
    n_pages = _uk_bench_row_int(entry_dict, "n_effect_feed_pages")
    source_bytes = _uk_bench_row_int(entry_dict, "enacted_source_size") + _uk_bench_row_int(
        entry_dict,
        "oracle_source_size",
    )
    source_mb = source_bytes / (1024 * 1024)
    replay_text = " replay" if do_replay else ""
    heavy_reasons = _uk_bench_replay_heavy_reasons(entry_dict) if do_replay else ()
    heavy_text = f" heavy={','.join(heavy_reasons)}" if heavy_reasons else ""
    return (
        f"  [start {index}/{total}] {statute_id:<30}{replay_text} "
        f"effects={n_effects} pages={n_pages} source_mb={source_mb:.1f}{heavy_text}"
    )


def _partition_uk_replay_heavy_entries(
    corpus: Sequence[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Split replay corpus rows into ordinary parallel work and heavy isolated work."""
    ordinary: list[dict[str, object]] = []
    heavy: list[dict[str, object]] = []
    for entry in corpus:
        if _uk_bench_replay_heavy_reasons(entry):
            heavy.append(dict(entry))
        else:
            ordinary.append(dict(entry))
    return ordinary, heavy


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

    def _walk(node: IRNode) -> str:
        parts: list[str] = []
        if node.text:
            parts.append(node.text.strip())
        for child in node.children:
            child_text = _walk(child)
            if child_text:
                parts.append(child_text)
        raw = " ".join(parts)
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid and eid in eids and raw:
            texts[eid] = _normalize_text(raw)
        return raw

    for child in ir.body.children:
        _walk(child)
    for sch in ir.supplements:
        _walk(sch)
    return texts


@lru_cache(maxsize=65536)
def _cached_short_text_ratio(source_text: str, oracle_text: str) -> float:
    return Levenshtein.ratio(source_text, oracle_text)


def _bench_text_ratio(source_text: str, oracle_text: str) -> float:
    if source_text == oracle_text:
        return 1.0
    if len(source_text) + len(oracle_text) <= _TEXT_RATIO_CACHE_MAX_CHARS:
        return _cached_short_text_ratio(source_text, oracle_text)
    return Levenshtein.ratio(source_text, oracle_text)


def _text_similarity_score(
    source_texts: Dict[str, str],
    oracle_texts: Dict[str, str],
) -> tuple[float, int]:
    """Average Levenshtein ratio across common EIDs that have non-empty text.

    Returns (score, n_compared).  score is -1.0 when no EIDs are comparable.
    """
    common = set(source_texts) & set(oracle_texts)
    total = 0.0
    compared = 0
    # Only compare EIDs where both sides have actual text.
    for eid in common:
        source_text = source_texts[eid]
        oracle_text = oracle_texts[eid]
        if not source_text or not oracle_text:
            continue
        total += _bench_text_ratio(source_text, oracle_text)
        compared += 1
    if not compared:
        return -1.0, 0
    return total / compared, compared


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


class _EffectRowCounts(NamedTuple):
    n_effect_rows: int
    rejection_count: int
    rejection_rule_counts: dict[str, int]
    observation_count: int
    observation_rule_counts: dict[str, int]
    observations: tuple[dict[str, Any], ...]


class _ResidualClaimClassification(NamedTuple):
    tier: str
    kind: str
    section_claim_count: int


def _coerce_effect_row_counts(value: _EffectRowCounts | tuple[Any, ...]) -> _EffectRowCounts:
    if isinstance(value, _EffectRowCounts):
        return value
    (
        n_effect_rows,
        rejection_count,
        rejection_rule_counts,
        observation_count,
        observation_rule_counts,
        observations,
    ) = value
    return _EffectRowCounts(
        n_effect_rows=int(n_effect_rows),
        rejection_count=int(rejection_count),
        rejection_rule_counts=dict(rejection_rule_counts),
        observation_count=int(observation_count),
        observation_rule_counts=dict(observation_rule_counts),
        observations=tuple(dict(obs) for obs in observations),
    )


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
    duration_s: float = 0.0
    process_maxrss_kb: int = 0
    phase_timings: dict[str, float] = field(default_factory=dict)


_REPORT_EVIDENCE_TUPLE_FIELDS = (
    "effect_feed_observations",
    "bench_exception_observations",
    "source_parse_observations",
    "effect_diagnostics",
    "uk_authority_observations",
    "lowering_rejections",
    "replay_adjudications",
    "score_witness_rows",
)


def _bench_result_report_copy(result: _BenchResult) -> _BenchResult:
    """Return a bounded-memory row copy for aggregate text reports.

    The full result is still streamed to CSV/JSONL sidecars before disposal.
    Aggregate report lists only need scalar counts, scores, status, URLs, and
    timings; retaining full diagnostic tuples for top-N lists can keep large
    per-row evidence payloads alive for an entire full-corpus run.
    """

    return replace(
        result,
        **{field_name: () for field_name in _REPORT_EVIDENCE_TUPLE_FIELDS},
    )


def _bench_primary_score(result: _BenchResult, *, has_commencement: bool) -> float:
    if has_commencement and result.commencement_score >= 0.0:
        return result.commencement_score
    return result.score


def _bench_primary_replay_score(result: _BenchResult, *, has_commencement: bool) -> float:
    if has_commencement and result.replay_commencement_score >= 0.0:
        return result.replay_commencement_score
    return result.replay_score


def _bench_compare_primary_score(result: _BenchResult) -> float:
    if result.replay_commencement_score >= 0.0:
        return result.replay_commencement_score
    if result.replay_score >= 0.0:
        return result.replay_score
    if result.commencement_score >= 0.0:
        return result.commencement_score
    return result.score


def _default_uk_bench_workers(*, do_replay: bool, cpu_count: int | None = None) -> int:
    """Return the memory-safe default worker count for UK bench runs.

    Replay rows can materialize large trees and effect programs in each worker.
    Keep the implicit default conservative for WSL2/full-corpus runs; callers
    can still opt into higher throughput with ``--parallel``.
    """
    cpus = max(1, cpu_count or os.cpu_count() or 1)
    if do_replay:
        return max(1, min(4, cpus // 2 or 1))
    return max(1, min(8, cpus))


def _process_maxrss_kb() -> int:
    """Return current process peak RSS in KiB where the platform exposes it."""
    try:
        import resource
    except ImportError:
        return 0
    try:
        maxrss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    except (OSError, AttributeError, ValueError):
        return 0
    if sys.platform == "darwin":
        return maxrss // 1024
    return maxrss


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


class _BenchRunAccumulator:
    def __init__(
        self,
        has_commencement: bool,
        replay_adjudication_sample_kinds: Sequence[str] = (),
        replay_adjudication_sample_limit: int = 5,
    ) -> None:
        self.has_commencement = has_commencement
        self.replay_adjudication_sample_kinds = {
            str(k).strip() for k in replay_adjudication_sample_kinds if str(k).strip()
        }
        self.replay_adjudication_sample_limit = replay_adjudication_sample_limit

        self.total_count = 0
        self.ok_count = 0
        self.core_ok_count = 0
        self.noncore_ok_count = 0
        self.core_count = 0
        self.replayed_count = 0

        self.primary_scores: list[float] = []
        self.raw_scores: list[float] = []
        self.replay_scores: list[float] = []
        self.replayed_enacted_scores: list[float] = []
        self.core_replayed_enacted_scores: list[float] = []
        self.commencement_scores: list[float] = []
        self.core_scores: list[float] = []
        self.core_replay_scores: list[float] = []
        self.core_comm_scores: list[float] = []
        self.text_scores: list[float] = []
        self.replay_text_scores: list[float] = []

        self.replay_commencement_scores: list[float] = []
        self.replay_commencement_enacted_scores: list[float] = []

        self.type_groups: dict[str, list[float]] = {}
        self.type_replay_groups: dict[str, list[float]] = {}
        self.type_perfect_counts: Counter[str] = Counter()
        self.type_counts: Counter[str] = Counter()

        self.row_status_counts: Counter[str] = Counter()
        self.comparison_class_counts: Counter[str] = Counter()
        self.noncore_comparison_class_counts: Counter[str] = Counter()
        self.enacted_source_counts: Counter[str] = Counter()
        self.oracle_source_counts: Counter[str] = Counter()

        self.source_parse_observation_rows = 0
        self.source_parse_observations_total = 0
        self.source_parse_observation_rule_counts: Counter[str] = Counter()
        self.source_parse_rejection_rows = 0
        self.source_parse_rejections_total = 0
        self.source_parse_rejection_rule_counts: Counter[str] = Counter()

        self.bench_exception_rows = 0
        self.bench_exceptions_total = 0
        self.bench_exception_rule_counts: Counter[str] = Counter()

        self.effect_feed_observation_rows = 0
        self.effect_feed_observations_total = 0
        self.effect_feed_observation_rule_counts: Counter[str] = Counter()
        self.effect_feed_rejection_rows = 0
        self.effect_feed_rejections_total = 0
        self.effect_feed_rejection_rule_counts: Counter[str] = Counter()

        self.authority_rejection_total = 0
        self.authority_rejection_rule_counts: Counter[str] = Counter()
        self.authority_observation_total = 0
        self.authority_observation_rule_counts: Counter[str] = Counter()

        self.replay_adjudication_total = 0
        self.replay_adjudication_kind_counts: Counter[str] = Counter()
        self.replay_adjudication_bucket_counts: Counter[str] = Counter()
        self.effect_source_pathology_counts: Counter[str] = Counter()
        self.manual_compile_status_counts: Counter[str] = Counter()
        self.manual_compile_rule_counts: Counter[str] = Counter()

        self.source_acquisition_observation_total = 0
        self.source_acquisition_observation_rule_counts: Counter[str] = Counter()
        self.source_acquisition_rejection_total = 0
        self.source_acquisition_rejection_rule_counts: Counter[str] = Counter()

        self.lowering_observation_total = 0
        self.lowering_rejection_total = 0
        self.blocking_lowering_rejection_total = 0
        self.lowering_observation_rule_counts: Counter[str] = Counter()
        self.lowering_rejection_rule_counts: Counter[str] = Counter()
        self.blocking_lowering_rejection_rule_counts: Counter[str] = Counter()

        self.residual_claim_tier_counts: Counter[str] = Counter()
        self.residual_claim_kind_counts: Counter[str] = Counter()

        self.with_effect_pages_count = 0
        self.with_effect_rows_count = 0
        self.avg_commenced_n_sum = 0
        self.total_ops = 0
        self.total_replay_adjudications = 0
        self.total_alignment_changes = 0
        self.total_alignment_oracle_assigned = 0
        self.total_alignment_local_fallback = 0
        self.total_alignment_transparent_wrapper_cleared = 0
        self.total_alignment_before_nodes = 0
        self.total_alignment_after_nodes = 0
        self.alignment_node_count_mismatch_rows = 0
        self.alignment_match_method_counts: Counter[str] = Counter()
        self.total_authority_rejections = 0
        self.total_authority_observations = 0
        self.replay_effect_source_pathology_counts: Counter[str] = Counter()
        self.replay_manual_compile_status_counts: Counter[str] = Counter()
        self.replay_manual_compile_rule_counts: Counter[str] = Counter()
        self.total_source_acquisition_observations = 0
        self.total_source_acquisition_rejections = 0
        self.total_lowering_observations = 0
        self.total_lowering_rejections = 0
        self.total_blocking_lowering_rejections = 0

        self.uk_residual_section_claims_total = 0

        self.regime_counts: Counter[tuple[bool, bool, bool, str, str]] = Counter()

        self.no_oracle_rows: list[_BenchResult] = []
        self.error_rows: list[_BenchResult] = []
        self.effect_feed_count_error_rows: list[_BenchResult] = []
        self.replay_errors: list[_BenchResult] = []
        self.commencement_errors: list[_BenchResult] = []
        self.replay_error_count = 0
        self.commencement_error_count = 0

        self.worst_core: list[_BenchResult] = []
        self.worst_replay_core: list[_BenchResult] = []
        self.worst_noncore: list[_BenchResult] = []
        self.slowest: list[_BenchResult] = []
        self.highest_rss: list[_BenchResult] = []
        self.slowest_phase_time: list[_BenchResult] = []
        self.regressions_comm: list[_BenchResult] = []
        self.improvements_comm: list[_BenchResult] = []
        self.regressions_raw: list[_BenchResult] = []
        self.improvements_raw: list[_BenchResult] = []

        self.phase_totals: Counter[str] = Counter()
        self.measured_total = 0.0
        self.row_total = 0.0
        self.timed_count = 0
        self.max_process_maxrss_kb = 0
        self.max_process_maxrss_statute_id = ""

        self.adjudication_samples_totals: Counter[str] = Counter()
        self.adjudication_samples: dict[str, list[tuple[_BenchResult, dict[str, Any]]]] = {
            kind: [] for kind in self.replay_adjudication_sample_kinds
        }

    def feed(self, r: _BenchResult) -> None:
        report_row: _BenchResult | None = None

        def _report_row() -> _BenchResult:
            nonlocal report_row
            if report_row is None:
                report_row = _bench_result_report_copy(r)
            return report_row

        self.total_count += 1

        self.row_status_counts[r.status] += 1
        self.comparison_class_counts[r.comparison_class or "unknown"] += 1
        if r.core_benchmark:
            self.core_count += 1
        self.enacted_source_counts[r.enacted_source_status] += 1
        self.oracle_source_counts[r.oracle_source_status] += 1

        if r.status in ("NO_ORACLE", "NO_ENACTED"):
            if len(self.no_oracle_rows) < 10:
                self.no_oracle_rows.append(_report_row())
        elif r.status == "ERR":
            if len(self.error_rows) < 10:
                self.error_rows.append(_report_row())

        if r.effect_feed_count_error:
            if len(self.effect_feed_count_error_rows) < 10:
                self.effect_feed_count_error_rows.append(_report_row())
        if r.replay_error:
            self.replay_error_count += 1
            if len(self.replay_errors) < 10:
                self.replay_errors.append(_report_row())
        if r.commencement_error:
            self.commencement_error_count += 1
            if len(self.commencement_errors) < 10:
                self.commencement_errors.append(_report_row())

        if r.phase_timings:
            self.timed_count += 1
            self.measured_total += sum(r.phase_timings.values())
            self.row_total += r.duration_s
            self.phase_totals.update(r.phase_timings)

            self.slowest_phase_time.append(_report_row())
            self.slowest_phase_time.sort(key=lambda x: sum(x.phase_timings.values()), reverse=True)
            self.slowest_phase_time = self.slowest_phase_time[:10]

        if r.duration_s > 0.0:
            self.slowest.append(_report_row())
            self.slowest.sort(key=lambda x: x.duration_s, reverse=True)
            self.slowest = self.slowest[:10]
        if r.process_maxrss_kb > 0:
            if r.process_maxrss_kb > self.max_process_maxrss_kb:
                self.max_process_maxrss_kb = r.process_maxrss_kb
                self.max_process_maxrss_statute_id = r.statute_id
            self.highest_rss.append(_report_row())
            self.highest_rss.sort(key=lambda x: x.process_maxrss_kb, reverse=True)
            self.highest_rss = self.highest_rss[:10]

        if r.source_parse_observation_count > 0:
            self.source_parse_observation_rows += 1
            self.source_parse_observations_total += r.source_parse_observation_count
            self.source_parse_observation_rule_counts.update(r.source_parse_observation_rule_counts)
        if r.source_parse_rejection_count > 0:
            self.source_parse_rejection_rows += 1
            self.source_parse_rejections_total += r.source_parse_rejection_count
            self.source_parse_rejection_rule_counts.update(r.source_parse_rejection_rule_counts)

        if r.bench_exception_count > 0:
            self.bench_exception_rows += 1
            self.bench_exceptions_total += r.bench_exception_count
            self.bench_exception_rule_counts.update(r.bench_exception_rule_counts)

        if r.effect_feed_observation_count > 0:
            self.effect_feed_observation_rows += 1
            self.effect_feed_observations_total += r.effect_feed_observation_count
            self.effect_feed_observation_rule_counts.update(r.effect_feed_observation_rule_counts)
        if r.effect_feed_rejection_count > 0:
            self.effect_feed_rejection_rows += 1
            self.effect_feed_rejections_total += r.effect_feed_rejection_count
            self.effect_feed_rejection_rule_counts.update(r.effect_feed_rejection_rule_counts)

        self.authority_rejection_total += r.uk_authority_rejection_count
        self.authority_rejection_rule_counts.update(r.uk_authority_rejection_rule_counts)
        self.authority_observation_total += r.uk_authority_observation_count
        self.authority_observation_rule_counts.update(r.uk_authority_observation_rule_counts)

        self.replay_adjudication_total += r.replay_adjudication_count
        self.replay_adjudication_kind_counts.update(r.replay_adjudication_kind_counts)
        self.replay_adjudication_bucket_counts.update(
            _replay_adjudication_bucket_counts(r.replay_adjudication_kind_counts)
        )

        self.effect_source_pathology_counts.update(r.effect_source_pathology_counts)
        self.manual_compile_status_counts.update(r.manual_compile_status_counts)
        self.manual_compile_rule_counts.update(r.manual_compile_rule_counts)

        self.source_acquisition_observation_total += r.source_acquisition_observation_count
        self.source_acquisition_observation_rule_counts.update(
            r.source_acquisition_observation_rule_counts
        )
        self.source_acquisition_rejection_total += r.source_acquisition_rejection_count
        self.source_acquisition_rejection_rule_counts.update(
            r.source_acquisition_rejection_rule_counts
        )

        self.lowering_observation_total += r.lowering_observation_count
        self.lowering_rejection_total += r.lowering_rejection_count
        self.blocking_lowering_rejection_total += r.blocking_lowering_rejection_count
        self.lowering_observation_rule_counts.update(r.lowering_observation_rule_counts)
        self.lowering_rejection_rule_counts.update(r.lowering_rejection_rule_counts)
        self.blocking_lowering_rejection_rule_counts.update(
            r.blocking_lowering_rejection_rule_counts
        )

        self.residual_claim_tier_counts[r.uk_residual_claim_tier or "UNRESOLVED"] += 1
        self.residual_claim_kind_counts[r.uk_residual_claim_kind or "unknown"] += 1
        self.uk_residual_section_claims_total += r.uk_residual_section_claim_count

        if r.status == "OK" and r.n_oracle_eids > 0:
            self.ok_count += 1

            p_score = _bench_primary_score(r, has_commencement=self.has_commencement)
            self.primary_scores.append(p_score)
            self.raw_scores.append(r.score)

            if r.core_benchmark:
                self.core_ok_count += 1
                self.core_scores.append(r.score)
                self.worst_core.append(_report_row())
                self.worst_core.sort(
                    key=lambda x: _bench_primary_score(x, has_commencement=self.has_commencement)
                )
                self.worst_core = self.worst_core[:15]
            else:
                self.noncore_ok_count += 1
                self.noncore_comparison_class_counts[r.comparison_class or "unknown"] += 1
                self.worst_noncore.append(_report_row())
                self.worst_noncore.sort(
                    key=lambda x: _bench_primary_score(x, has_commencement=self.has_commencement)
                )
                self.worst_noncore = self.worst_noncore[:10]

            if r.commencement_score >= 0.0:
                self.commencement_scores.append(r.commencement_score)
                if r.core_benchmark:
                    self.core_comm_scores.append(r.commencement_score)

            if r.replay_score >= 0.0:
                self.replayed_count += 1
                self.replay_scores.append(r.replay_score)
                self.replayed_enacted_scores.append(r.score)
                if r.core_benchmark:
                    self.core_replay_scores.append(r.replay_score)
                    self.core_replayed_enacted_scores.append(r.score)

            if r.text_score >= 0.0:
                self.text_scores.append(r.text_score)
                if r.replay_text_score >= 0.0:
                    self.replay_text_scores.append(r.replay_text_score)

            self.type_groups.setdefault(r.act_type, []).append(r.score)
            if r.replay_score >= 0.0:
                self.type_replay_groups.setdefault(r.act_type, []).append(r.replay_score)
            if r.score == 1.0:
                self.type_perfect_counts[r.act_type] += 1
            self.type_counts[r.act_type] += 1

            if r.commencement_score >= 0.0:
                self.avg_commenced_n_sum += r.n_commenced_eids

            if (r.n_effect_feed_pages or r.n_effects) > 0:
                self.with_effect_pages_count += 1
            if r.n_effect_rows > 0:
                self.with_effect_rows_count += 1

        if r.replay_score >= 0.0:
            self.total_ops += max(r.n_ops, 0)
            self.total_replay_adjudications += r.replay_adjudication_count
            self.total_alignment_changes += r.oracle_alignment_changed_count
            self.total_alignment_oracle_assigned += r.oracle_alignment_oracle_assigned_count
            self.total_alignment_local_fallback += r.oracle_alignment_local_fallback_count
            self.total_alignment_transparent_wrapper_cleared += (
                r.oracle_alignment_transparent_wrapper_cleared_count
            )
            self.total_alignment_before_nodes += r.oracle_alignment_before_node_count
            self.total_alignment_after_nodes += r.oracle_alignment_after_node_count
            if r.oracle_alignment_node_count_mismatch:
                self.alignment_node_count_mismatch_rows += 1
            self.alignment_match_method_counts.update(r.oracle_alignment_match_method_counts)

            self.total_authority_rejections += r.uk_authority_rejection_count
            self.total_authority_observations += r.uk_authority_observation_count
            self.replay_effect_source_pathology_counts.update(r.effect_source_pathology_counts)
            self.replay_manual_compile_status_counts.update(r.manual_compile_status_counts)
            self.replay_manual_compile_rule_counts.update(r.manual_compile_rule_counts)
            self.total_source_acquisition_observations += r.source_acquisition_observation_count
            self.total_source_acquisition_rejections += r.source_acquisition_rejection_count
            self.total_lowering_observations += r.lowering_observation_count
            self.total_lowering_rejections += r.lowering_rejection_count
            self.total_blocking_lowering_rejections += r.blocking_lowering_rejection_count

            if (
                r.core_benchmark
                and _bench_primary_replay_score(r, has_commencement=self.has_commencement) >= 0.0
            ):
                p_rep = _bench_primary_replay_score(r, has_commencement=self.has_commencement)
                if p_rep < 1.0:
                    self.worst_replay_core.append(_report_row())
                    self.worst_replay_core.sort(
                        key=lambda x: _bench_primary_replay_score(
                            x, has_commencement=self.has_commencement
                        )
                    )
                    self.worst_replay_core = self.worst_replay_core[:15]

            if r.commencement_score >= 0.0 and r.replay_commencement_score >= 0.0:
                self.replay_commencement_scores.append(r.replay_commencement_score)
                self.replay_commencement_enacted_scores.append(r.commencement_score)
                delta = r.replay_commencement_score - r.commencement_score
                if delta > 0.001:
                    self.improvements_comm.append(_report_row())
                    self.improvements_comm.sort(
                        key=lambda x: x.replay_commencement_score - x.commencement_score,
                        reverse=True,
                    )
                    self.improvements_comm = self.improvements_comm[:5]
                elif delta < -0.001:
                    self.regressions_comm.append(_report_row())
                    self.regressions_comm.sort(
                        key=lambda x: x.replay_commencement_score - x.commencement_score
                    )
                    self.regressions_comm = self.regressions_comm[:5]
            elif r.replay_score >= 0.0 and r.score >= 0.0:
                delta = r.replay_score - r.score
                if delta > 0.001:
                    self.improvements_raw.append(_report_row())
                    self.improvements_raw.sort(
                        key=lambda x: x.replay_score - x.score, reverse=True
                    )
                    self.improvements_raw = self.improvements_raw[:5]
                elif delta < -0.001:
                    self.regressions_raw.append(_report_row())
                    self.regressions_raw.sort(key=lambda x: x.replay_score - x.score)
                    self.regressions_raw = self.regressions_raw[:5]

            for adjudication in r.replay_adjudications:
                kind = str(adjudication.get("kind") or "")
                if kind in self.replay_adjudication_sample_kinds:
                    self.adjudication_samples_totals[kind] += 1
                    if (
                        len(self.adjudication_samples[kind])
                        < self.replay_adjudication_sample_limit
                    ):
                        self.adjudication_samples[kind].append((_report_row(), adjudication))

        self.regime_counts[
            (
                r.uk_metadata_backfill_enabled,
                r.uk_oracle_alignment_enabled,
                r.uk_metadata_only_effects_enabled,
                r.uk_applicability_mode,
                r.uk_authority_mode,
            )
        ] += 1



def _normalize_uk_bench_replay_regime(args: Any) -> UKReplayRegime:
    return normalize_uk_replay_regime(args)


# ---------------------------------------------------------------------------
# Score one statute
# ---------------------------------------------------------------------------


def _load_effect_row_counts(
    statute_id: str,
    archive: Farchive,
) -> _EffectRowCounts:
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
    return _EffectRowCounts(
        n_effect_rows=len(effects),
        rejection_count=len(blocking_observations),
        rejection_rule_counts=dict(feed_rejection_rule_counts),
        observation_count=len(feed_observations),
        observation_rule_counts=dict(feed_observation_rule_counts),
        observations=tuple(dict(obs) for obs in feed_observations),
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
    score_text: bool = True,
    record_replay_subphases: bool = False,
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
    phase_timings: dict[str, float] = {}
    phase_t0 = time.perf_counter()

    def _mark_phase(name: str) -> None:
        nonlocal phase_t0
        now = time.perf_counter()
        phase_timings[name] = phase_timings.get(name, 0.0) + (now - phase_t0)
        phase_t0 = now

    def _mark_external_phases(timings: Mapping[str, float]) -> None:
        nonlocal phase_t0
        for name, seconds in timings.items():
            if seconds <= 0:
                continue
            phase_timings[name] = phase_timings.get(name, 0.0) + seconds
        phase_t0 = time.perf_counter()

    try:
        try:
            effect_row_counts = _coerce_effect_row_counts(
                _load_effect_row_counts(sid, archive)
            )
            n_effect_rows = effect_row_counts.n_effect_rows
            effect_feed_rejection_count = effect_row_counts.rejection_count
            effect_feed_rejection_rule_counts = effect_row_counts.rejection_rule_counts
            effect_feed_observation_count = effect_row_counts.observation_count
            effect_feed_observation_rule_counts = effect_row_counts.observation_rule_counts
            effect_feed_observations = effect_row_counts.observations
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
        _mark_phase("effect_counts")

        enacted_bytes = archive.get(enacted_url)
        enacted_source_status, enacted_source_size = _source_state(enacted_bytes)
        enacted_source_sha256 = _source_sha256(enacted_bytes)
        oracle_bytes = archive.get(current_url)
        oracle_source_status, oracle_source_size = _source_state(oracle_bytes)
        oracle_source_sha256 = _source_sha256(oracle_bytes)
        _mark_phase("source_load")
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
                phase_timings=dict(phase_timings),
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
                phase_timings=dict(phase_timings),
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
        _mark_phase("parse_enacted")
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
        _mark_phase("parse_oracle")
        oracle_eids: Set[str] = set(oracle_eid_data.get("eid_map", {}).values())
        oracle_physical_eid_aliases = oracle_eid_data.get("physical_eid_aliases", {})
        oracle_visible_number_eid_aliases = oracle_eid_data.get("visible_number_eid_aliases", {})

        enacted_eids = _collect_eids(enacted_ir.body.children)
        for s in enacted_ir.supplements:
            enacted_eids.update(_collect_eids([s]))
        _mark_phase("collect_enacted_eids")

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
        _mark_phase("score_enacted_eids")

        # ── Text similarity: enacted vs oracle ─────────────────────────
        oracle_text_map: Dict[str, str] = oracle_eid_data.get("text_map", {})
        if score_text:
            enacted_texts = _extract_eid_texts(enacted_ir, common)
            text_score, n_text_compared = _text_similarity_score(enacted_texts, oracle_text_map)
            _mark_phase("text_score_enacted")
        else:
            text_score = -1.0
            n_text_compared = 0

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
                compile_phase_timings: dict[str, float] = {}
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
                    compile_phase_timings_out=compile_phase_timings,
                )
                if compile_phase_timings:
                    _mark_external_phases(compile_phase_timings)
                else:
                    _mark_phase("compile_ops")
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
                replay_phase_timings: dict[str, float] | None = (
                    {} if record_replay_subphases else None
                )
                replayed_ir = replay_uk_ops(
                    enacted_ir,
                    ops,
                    eid_map=eid_map,
                    text_map=text_map,
                    allow_oracle_alignment=allow_oracle_alignment,
                    adjudications_out=replay_adjudications,
                    replay_phase_timings_out=replay_phase_timings,
                )
                if replay_phase_timings:
                    _mark_external_phases(replay_phase_timings)
                else:
                    _mark_phase("replay")
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
                    _mark_phase("oracle_align")
                replayed_eids = _collect_eids(replayed_ir.body.children)
                for s in replayed_ir.supplements:
                    replayed_eids.update(_collect_eids([s]))
                _mark_phase("collect_replay_eids")
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
                _mark_phase("score_replay_eids")
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
                residual_claim = _classify_uk_residual_claim_for_bench(
                    comparison_class=uk_residual_claim_comparison_class,
                    only_in_replayed=only_in_replayed,
                    only_in_oracle=only_in_oracle,
                    replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                    lowering_observations=lowering_rejections,
                )
                uk_residual_claim_tier = residual_claim.tier
                uk_residual_claim_kind = residual_claim.kind
                uk_residual_section_claim_count = residual_claim.section_claim_count
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
                _mark_phase("replay_residuals")
                if score_text:
                    replayed_texts = _extract_eid_texts(replayed_ir, replayed_eids & oracle_eids)
                    replay_text_score, _ = _text_similarity_score(replayed_texts, oracle_text_map)
                    _mark_phase("text_score_replay")
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
                    residual_claim = _classify_uk_residual_claim_for_bench(
                        comparison_class="commensurable",
                        only_in_replayed=set(),
                        only_in_oracle=set(),
                        replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                        lowering_observations=lowering_rejections,
                    )
                    uk_residual_claim_tier = residual_claim.tier
                    uk_residual_claim_kind = residual_claim.kind
                    uk_residual_section_claim_count = residual_claim.section_claim_count
                    uk_residual_claim_comparison_class = "replay_exception"
                    uk_residual_claim_core_comparison = False
                    uk_residual_section_claim_emitted = bool(uk_residual_section_claim_count)
                replay_score = -1.0
                n_ops = -1  # signals error
                replay_error = f"{type(replay_exc).__name__}: {replay_exc}"[:200]
                _mark_phase("replay_exception")

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
                _mark_phase("commencement")
            except Exception as comm_exc:
                print(
                    f"  COMMENCEMENT ERROR {sid}: {type(comm_exc).__name__}: {comm_exc}",
                    file=sys.stderr,
                )
                commencement_error = f"{type(comm_exc).__name__}: {comm_exc}"[:200]
                _mark_phase("commencement_exception")
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
            residual_claim = _classify_uk_residual_claim_for_bench(
                comparison_class=uk_residual_claim_comparison_class,
                only_in_replayed=replay_compare_eids - oracle_compare_eids,
                only_in_oracle=oracle_compare_eids - replay_compare_eids,
                replay_adjudication_kind_counts=replay_adjudication_kind_counts,
                lowering_observations=lowering_rejections,
            )
            uk_residual_claim_tier = residual_claim.tier
            uk_residual_claim_kind = residual_claim.kind
            uk_residual_section_claim_count = residual_claim.section_claim_count
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
            phase_timings=dict(phase_timings),
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
            phase_timings=dict(phase_timings),
        )


# ---------------------------------------------------------------------------
# Run bench
# ---------------------------------------------------------------------------


def _score_statute_worker(entry: dict) -> _BenchResult:
    """Top-level picklable wrapper for parallel execution.

    Opens its own Farchive per worker process using module-level globals
    configured before fork, or by the worker initializer when recycling uses
    spawn-backed workers.
    """
    row_t0 = time.perf_counter()
    try:
        archive = Farchive(_WORKER_DB_PATH)
    except Exception as exc:
        result = _bench_exception_result(
            entry,
            exc,
            allow_metadata_backfill=_WORKER_ALLOW_METADATA_BACKFILL,
            allow_oracle_alignment=_WORKER_ALLOW_ORACLE_ALIGNMENT,
            applicability_mode=_WORKER_APPLICABILITY_MODE,
            authority_mode=_WORKER_AUTHORITY_MODE,
            allow_metadata_only_effects=_WORKER_ALLOW_METADATA_ONLY_EFFECTS,
        )
        result.duration_s = time.perf_counter() - row_t0
        result.process_maxrss_kb = _process_maxrss_kb()
        return result
    try:
        result = _score_statute(
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
            score_text=_WORKER_SCORE_TEXT,
            record_replay_subphases=_WORKER_RECORD_REPLAY_SUBPHASES,
        )
    except Exception as exc:
        result = _bench_exception_result(
            entry,
            exc,
            allow_metadata_backfill=_WORKER_ALLOW_METADATA_BACKFILL,
            allow_oracle_alignment=_WORKER_ALLOW_ORACLE_ALIGNMENT,
            applicability_mode=_WORKER_APPLICABILITY_MODE,
            authority_mode=_WORKER_AUTHORITY_MODE,
            allow_metadata_only_effects=_WORKER_ALLOW_METADATA_ONLY_EFFECTS,
        )
    finally:
        archive.close()
    result.duration_s = time.perf_counter() - row_t0
    result.process_maxrss_kb = _process_maxrss_kb()
    return result


_SCORE_WITNESS_HEADERS = [
    "schema",
    "label",
    "statute_id",
    "comparison_scope",
    "score_formula",
    "left_label",
    "right_label",
    "side",
    "eid",
    "rank",
    "category_total",
    "sample_limit",
    "truncated",
    "left_count",
    "right_count",
    "common_count",
    "score_value",
    "comparison_class",
    "core_benchmark",
    "enacted_source_status",
    "oracle_source_status",
    "enacted_source_size",
    "oracle_source_size",
    "enacted_source_sha256",
    "oracle_source_sha256",
    "enacted_source_url",
    "oracle_source_url",
    "uk_metadata_backfill_enabled",
    "uk_oracle_alignment_enabled",
    "uk_metadata_only_effects_enabled",
    "uk_applicability_mode",
    "uk_authority_mode",
]


def _get_csv_headers(
    has_commencement: bool,
    has_replay: bool,
    has_text: bool,
    has_commencement_error: bool,
) -> list[str]:
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
            "duration_s",
            "process_maxrss_kb",
            _PHASE_TIMING_TOTAL_HEADER,
            *_PHASE_TIMING_HEADERS,
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
            "duration_s",
            "process_maxrss_kb",
            _PHASE_TIMING_TOTAL_HEADER,
            *_PHASE_TIMING_HEADERS,
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
    return headers


def _get_csv_row(
    r: _BenchResult,
    has_commencement: bool,
    has_replay: bool,
    has_text: bool,
    has_commencement_error: bool,
) -> list[Any]:
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
            f"{r.duration_s:.3f}",
            r.process_maxrss_kb,
            *_phase_timing_csv_values(r),
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
            f"{r.duration_s:.3f}",
            r.process_maxrss_kb,
            *_phase_timing_csv_values(r),
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
    return row


def _get_score_witness_dict_rows(r: _BenchResult, label: str) -> list[dict[str, object]]:
    rows = []
    for witness in r.score_witness_rows:
        left_label, right_label = _score_witness_labels(witness.comparison_scope)
        rows.append({
            "schema": _SCORE_WITNESS_SCHEMA,
            "label": label,
            "statute_id": r.statute_id,
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
            "comparison_class": r.comparison_class,
            "core_benchmark": "1" if r.core_benchmark else "0",
            "enacted_source_status": r.enacted_source_status,
            "oracle_source_status": r.oracle_source_status,
            "enacted_source_size": r.enacted_source_size,
            "oracle_source_size": r.oracle_source_size,
            "enacted_source_sha256": r.enacted_source_sha256,
            "oracle_source_sha256": r.oracle_source_sha256,
            "enacted_source_url": r.enacted_source_url,
            "oracle_source_url": r.oracle_source_url,
            "uk_metadata_backfill_enabled": (
                "1" if r.uk_metadata_backfill_enabled else "0"
            ),
            "uk_oracle_alignment_enabled": "1" if r.uk_oracle_alignment_enabled else "0",
            "uk_metadata_only_effects_enabled": (
                "1" if r.uk_metadata_only_effects_enabled else "0"
            ),
            "uk_applicability_mode": r.uk_applicability_mode,
            "uk_authority_mode": r.uk_authority_mode,
        })
    return rows


def _run_bench_parallel_entries(
    entries: Sequence[dict],
    archive: Farchive,
    *,
    do_replay: bool,
    repo_root: Optional[Path],
    workers: int,
    do_commencement: bool,
    allow_metadata_backfill: bool,
    allow_oracle_alignment: bool,
    applicability_mode: str,
    authority_mode: str,
    allow_metadata_only_effects: bool,
    score_text: bool,
    record_replay_subphases: bool,
    worker_max_tasks_per_child: int | None,
) -> Generator[_BenchResult, None, None]:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    worker_config = (
        str(archive._db_path),
        do_replay,
        str(repo_root) if repo_root is not None else "",
        do_commencement,
        allow_metadata_backfill,
        allow_oracle_alignment,
        applicability_mode,
        authority_mode,
        allow_metadata_only_effects,
        score_text,
        record_replay_subphases,
    )
    # Fast fork-backed default: child workers inherit these globals.
    _configure_uk_bench_worker(*worker_config)
    pool_kwargs: dict[str, object] = {}
    if worker_max_tasks_per_child is not None:
        # max_tasks_per_child may use spawn; initializer makes worker config
        # explicit instead of relying on fork-inherited module globals.
        pool_kwargs = {
            "initializer": _configure_uk_bench_worker,
            "initargs": worker_config,
            "max_tasks_per_child": worker_max_tasks_per_child,
        }

    with ProcessPoolExecutor(max_workers=workers, **pool_kwargs) as pool:
        future_to_entry: dict[object, dict] = {}
        submission_order = sorted(
            entries,
            key=_uk_bench_parallel_submission_cost,
            reverse=True,
        )
        next_submission = 0
        max_in_flight = max(1, workers * 2)

        def _submit_next() -> _BenchResult | None:
            nonlocal next_submission
            if next_submission >= len(submission_order):
                return None
            entry = submission_order[next_submission]
            next_submission += 1
            try:
                future_to_entry[pool.submit(_score_statute_worker, entry)] = entry
            except Exception as exc:
                return _bench_exception_result(
                    entry,
                    exc,
                    allow_metadata_backfill=allow_metadata_backfill,
                    allow_oracle_alignment=allow_oracle_alignment,
                    applicability_mode=applicability_mode,
                    authority_mode=authority_mode,
                    allow_metadata_only_effects=allow_metadata_only_effects,
                )
            return None

        while next_submission < len(submission_order) and len(future_to_entry) < max_in_flight:
            submit_error = _submit_next()
            if submit_error is not None:
                yield submit_error

        while future_to_entry:
            completed_iter = iter(as_completed(future_to_entry))
            try:
                future = next(completed_iter)
            except StopIteration:
                break
            # Pop before calling result(): completed futures retain their
            # returned _BenchResult, so keeping them in this dict defeats
            # streaming and can exhaust memory on full-corpus WSL2 runs.
            entry = future_to_entry.pop(future)
            try:
                r = future.result()
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
            yield r
            while next_submission < len(submission_order) and len(future_to_entry) < max_in_flight:
                submit_error = _submit_next()
                if submit_error is not None:
                    yield submit_error


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
    score_text: bool = True,
    record_replay_subphases: bool = False,
    worker_max_tasks_per_child: int | None = None,
    progress_start: Callable[[int, int, Mapping[str, object]], None] | None = None,
) -> Generator[_BenchResult, None, None]:
    if workers > 1 or worker_max_tasks_per_child is not None:
        ordinary_corpus = list(corpus)
        heavy_corpus: list[dict[str, object]] = []
        if do_replay and workers > 1:
            ordinary_corpus, heavy_corpus = _partition_uk_replay_heavy_entries(corpus)
        if ordinary_corpus:
            yield from _run_bench_parallel_entries(
                ordinary_corpus,
                archive,
                do_replay=do_replay,
                repo_root=repo_root,
                workers=workers,
                do_commencement=do_commencement,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_oracle_alignment=allow_oracle_alignment,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
                allow_metadata_only_effects=allow_metadata_only_effects,
                score_text=score_text,
                record_replay_subphases=record_replay_subphases,
                worker_max_tasks_per_child=worker_max_tasks_per_child,
            )
        if heavy_corpus:
            yield from _run_bench_parallel_entries(
                heavy_corpus,
                archive,
                do_replay=do_replay,
                repo_root=repo_root,
                workers=1,
                do_commencement=do_commencement,
                allow_metadata_backfill=allow_metadata_backfill,
                allow_oracle_alignment=allow_oracle_alignment,
                applicability_mode=applicability_mode,
                authority_mode=authority_mode,
                allow_metadata_only_effects=allow_metadata_only_effects,
                score_text=score_text,
                record_replay_subphases=record_replay_subphases,
                worker_max_tasks_per_child=1,
            )
        return

    # Sequential fallback.
    total = len(corpus)
    for i, entry in enumerate(corpus, start=1):
        if progress_start is not None:
            progress_start(i, total, entry)
        row_t0 = time.perf_counter()
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
                score_text=score_text,
                record_replay_subphases=record_replay_subphases,
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
        r.duration_s = time.perf_counter() - row_t0
        r.process_maxrss_kb = _process_maxrss_kb()
        yield r


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
) -> _ResidualClaimClassification:
    """Classify replay residuals for bench reporting without changing scoring."""
    if not comparison_class:
        return _ResidualClaimClassification(
            tier="UNRESOLVED",
            kind="not_run",
            section_claim_count=0,
        )
    if not is_core_uk_comparison(comparison_class):
        return _ResidualClaimClassification(
            tier="UNRESOLVED",
            kind=comparison_class,
            section_claim_count=0,
        )
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
                return _ResidualClaimClassification(
                    tier="UNRESOLVED",
                    kind=source_backed_kind,
                    section_claim_count=0,
                )
        return _ResidualClaimClassification(
            tier=tier,
            kind=kind,
            section_claim_count=1 if tier == "PROVED_REPLAY_BUG" else 0,
        )
    return _ResidualClaimClassification(
        tier="UNRESOLVED",
        kind="no_strong_claim",
        section_claim_count=0,
    )


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
        f"duration_s={result.duration_s:.3f} "
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


def _print_slowest_rows(
    results: Sequence[_BenchResult] | _BenchRunAccumulator,
    *,
    has_commencement: bool,
    limit: int = 10,
) -> None:
    if isinstance(results, _BenchRunAccumulator):
        slowest = results.slowest[:limit]
    else:
        timed = [result for result in results if result.duration_s > 0.0]
        if not timed:
            return
        slowest = sorted(timed, key=lambda result: result.duration_s, reverse=True)[:limit]
    print(f"\nSlowest {len(slowest)} rows by wall time:")
    for result in slowest:
        score = _bench_primary_score(result, has_commencement=has_commencement)
        replay_score = _bench_primary_replay_score(result, has_commencement=has_commencement)
        replay_fragment = ""
        if replay_score >= 0.0:
            replay_fragment = f" replay={replay_score:.1%}"
        print(
            f"  {result.statute_id:<30} {result.duration_s:7.2f}s "
            f"score={score:.1%}{replay_fragment} "
            f"ops={result.n_ops:5d} "
            f"effect_rows={result.n_effect_rows:5d} "
            f"effect_pages={(result.n_effect_feed_pages or result.n_effects):5d} "
            f"source_mb={(result.enacted_source_size + result.oracle_source_size) / 1_000_000:.1f} "
            f"rss_mb={result.process_maxrss_kb / 1024:.0f} "
            f"class={result.comparison_class or 'unknown'} "
            f"status={result.status}"
        )


def _print_highest_rss_rows(
    results: Sequence[_BenchResult] | _BenchRunAccumulator,
    *,
    has_commencement: bool,
    limit: int = 10,
) -> None:
    if isinstance(results, _BenchRunAccumulator):
        rows = results.highest_rss[:limit]
    else:
        measured = [result for result in results if result.process_maxrss_kb > 0]
        if not measured:
            return
        rows = sorted(measured, key=lambda result: result.process_maxrss_kb, reverse=True)[:limit]
    if not rows:
        return
    print(f"\nRows after highest {len(rows)} process max RSS observations:")
    for result in rows:
        score = _bench_primary_score(result, has_commencement=has_commencement)
        replay_score = _bench_primary_replay_score(result, has_commencement=has_commencement)
        replay_fragment = ""
        if replay_score >= 0.0:
            replay_fragment = f" replay={replay_score:.1%}"
        print(
            f"  {result.statute_id:<30} "
            f"rss_mb={result.process_maxrss_kb / 1024:.0f} "
            f"score={score:.1%}{replay_fragment} "
            f"wall={result.duration_s:.2f}s "
            f"ops={result.n_ops:5d} "
            f"effect_rows={result.n_effect_rows:5d} "
            f"source_mb={(result.enacted_source_size + result.oracle_source_size) / 1_000_000:.1f} "
            f"class={result.comparison_class or 'unknown'} "
            f"status={result.status}"
        )


def _print_phase_timing_rows(
    results: Sequence[_BenchResult] | _BenchRunAccumulator,
    *,
    limit: int = 10,
) -> None:
    if isinstance(results, _BenchRunAccumulator):
        acc = results
    else:
        acc = _BenchRunAccumulator(has_commencement=False)
        for r in results:
            acc.feed(r)

    if acc.timed_count == 0:
        print("\nNo measured phase timings available in this run.")
        return

    top_phase_text = " ".join(
        f"{name}={seconds:.2f}s"
        for name, seconds in acc.phase_totals.most_common(8)
        if seconds > 0.001
    )
    print("\nPhase timing totals:")
    print(
        f"  rows={acc.timed_count} measured={acc.measured_total:.2f}s "
        f"row={acc.row_total:.2f}s"
    )
    if top_phase_text:
        print(f"  phases: {top_phase_text}")

    slowest_rows = acc.slowest_phase_time[:limit]
    print(f"\nSlowest {len(slowest_rows)} rows by measured phase time:")
    for result in slowest_rows:
        total = sum(result.phase_timings.values())
        phases = sorted(
            result.phase_timings.items(),
            key=lambda item: item[1],
            reverse=True,
        )
        phase_text = " ".join(
            f"{name}={seconds:.2f}s"
            for name, seconds in phases[:6]
            if seconds > 0.001
        )
        print(
            f"  {result.statute_id:<30} measured={total:7.2f}s "
            f"row={result.duration_s:7.2f}s ops={result.n_ops:5d} {phase_text}"
        )


def _phase_timing_csv_values(result: _BenchResult) -> list[str]:
    phase_timings = result.phase_timings
    total = sum(phase_timings.values())
    values = [f"{total:.3f}" if total > 0.0 else ""]
    values.extend(
        f"{phase_timings[key]:.3f}" if key in phase_timings else ""
        for key in _PHASE_TIMING_KEYS
    )
    return values


def _load_phase_timings(row: Mapping[str, str]) -> dict[str, float]:
    phase_timings: dict[str, float] = {}
    for key in _PHASE_TIMING_KEYS:
        raw_value = row.get(f"phase_{key}_s", "")
        if not raw_value:
            continue
        seconds = float(raw_value)
        if seconds > 0.0:
            phase_timings[key] = seconds
    return phase_timings


def _print_report(
    results: list[_BenchResult] | _BenchRunAccumulator,
    label: str,
    *,
    replay_adjudication_sample_kinds: Sequence[str] = (),
    replay_adjudication_sample_limit: int = 5,
    summary_only: bool = False,
) -> None:
    if isinstance(results, _BenchRunAccumulator):
        acc = results
    else:
        acc = _BenchRunAccumulator(
            has_commencement=any(r.commencement_score >= 0.0 for r in results if r.status == "OK" and r.n_oracle_eids > 0),
            replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
            replay_adjudication_sample_limit=replay_adjudication_sample_limit,
        )
        for r in results:
            acc.feed(r)

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

    all_rows_are_replayed = acc.replayed_count == acc.total_count

    print(f"\n=== UK Bench: {label} ===")
    print(
        f"Total: {acc.total_count}, Scored OK: {acc.ok_count}, "
        f"Source-unavailable: {len(acc.no_oracle_rows)}, Errors: {len(acc.error_rows)}"
    )
    if acc.row_status_counts.get("OK", 0) != acc.ok_count:
        print(f"Status OK rows: {acc.row_status_counts.get('OK', 0)}")
    print(f"Row statuses: {dict(sorted(acc.row_status_counts.items()))}")
    print(f"Comparison classes: {dict(sorted(acc.comparison_class_counts.items()))}")
    print(f"Core benchmark rows: {acc.core_count}")
    if acc.replayed_count > 0:
        print(f"Score mode: enacted baseline + replay ({acc.replayed_count} replayed rows)")
    else:
        print("Score mode: enacted baseline only (pass --replay for amendment replay)")
    print(
        "Source status: "
        f"enacted={dict(sorted(acc.enacted_source_counts.items()))} "
        f"oracle={dict(sorted(acc.oracle_source_counts.items()))}"
    )
    if summary_only:
        _print_summary_only_report(acc)
        return
    if acc.source_parse_observation_rows:
        print(
            f"Source parse observations: rows={acc.source_parse_observation_rows} "
            f"total={acc.source_parse_observations_total}"
        )
        if acc.source_parse_observation_rule_counts:
            print(
                "Source parse observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.source_parse_observation_rule_counts.items())
                )
            )
    if acc.source_parse_rejection_rows:
        print(
            f"Source parse blocking rejections: rows={acc.source_parse_rejection_rows} "
            f"total={acc.source_parse_rejections_total}"
        )
        if acc.source_parse_rejection_rule_counts:
            print(
                "Source parse rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.source_parse_rejection_rule_counts.items())
                )
            )
    if acc.bench_exception_rows:
        print(
            f"Bench exceptions: rows={acc.bench_exception_rows} "
            f"total={acc.bench_exceptions_total}"
        )
        if acc.bench_exception_rule_counts:
            print(
                "Bench exception rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.bench_exception_rule_counts.items())
                )
            )
    if acc.effect_feed_count_error_rows:
        print(f"Effect-feed count errors: rows={len(acc.effect_feed_count_error_rows)}")
        for r in acc.effect_feed_count_error_rows[:10]:
            print(f"  {r.statute_id}: {r.effect_feed_count_error}")
    if acc.effect_feed_observation_rows:
        print(
            f"Effect-feed observations: rows={acc.effect_feed_observation_rows} "
            f"total={acc.effect_feed_observations_total}"
        )
        if acc.effect_feed_observation_rule_counts:
            print(
                "Effect-feed observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.effect_feed_observation_rule_counts.items())
                )
            )
    if acc.effect_feed_rejection_rows:
        print(
            f"Effect-feed blocking rejections: rows={acc.effect_feed_rejection_rows} "
            f"total={acc.effect_feed_rejections_total}"
        )
        if acc.effect_feed_rejection_rule_counts:
            print(
                "Effect-feed rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.effect_feed_rejection_rule_counts.items())
                )
            )
    if acc.authority_observation_total and not all_rows_are_replayed:
        print(f"All-row authority observations: {acc.authority_observation_total}")
        if acc.authority_observation_rule_counts:
            print(
                "All-row authority observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.authority_observation_rule_counts.items())
                )
            )
    if acc.authority_rejection_total and not all_rows_are_replayed:
        print(f"All-row blocking authority rejections: {acc.authority_rejection_total}")
        if acc.authority_rejection_rule_counts:
            print(
                "All-row blocking authority rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.authority_rejection_rule_counts.items())
                )
            )
    if acc.replay_adjudication_total and not all_rows_are_replayed:
        print(f"All-row replay adjudications: {acc.replay_adjudication_total}")
        if acc.replay_adjudication_kind_counts:
            print(
                "All-row replay adjudication kinds: "
                + ", ".join(
                    f"{kind}={count}"
                    for kind, count in sorted(acc.replay_adjudication_kind_counts.items())
                )
            )
        if acc.replay_adjudication_bucket_counts:
            print(
                "All-row replay adjudication buckets: "
                + ", ".join(
                    f"{bucket}={count}"
                    for bucket, count in sorted(acc.replay_adjudication_bucket_counts.items())
                )
            )
    if acc.effect_source_pathology_counts and not all_rows_are_replayed:
        print(
            "All-row effect source pathologies: "
            + ", ".join(
                f"{pathology}={count}"
                for pathology, count in sorted(acc.effect_source_pathology_counts.items())
            )
        )
    if acc.manual_compile_status_counts and not all_rows_are_replayed:
        print(
            "All-row manual compile frontier statuses: "
            + ", ".join(
                f"{status}={count}"
                for status, count in sorted(acc.manual_compile_status_counts.items())
            )
        )
        if acc.manual_compile_rule_counts:
            print(
                "All-row manual compile frontier rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.manual_compile_rule_counts.items())
                )
            )
    if acc.source_acquisition_observation_total and not all_rows_are_replayed:
        print(f"All-row source acquisition observations: {acc.source_acquisition_observation_total}")
        if acc.source_acquisition_observation_rule_counts:
            print(
                "All-row source acquisition observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.source_acquisition_observation_rule_counts.items())
                )
            )
    if acc.source_acquisition_rejection_total and not all_rows_are_replayed:
        print(f"All-row source acquisition rejections: {acc.source_acquisition_rejection_total}")
        if acc.source_acquisition_rejection_rule_counts:
            print(
                "All-row source acquisition rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.source_acquisition_rejection_rule_counts.items())
                )
            )
    if acc.lowering_observation_total and not all_rows_are_replayed:
        print(f"All-row lowering observations: {acc.lowering_observation_total}")
        if acc.lowering_observation_rule_counts:
            print(
                "All-row lowering observation rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.lowering_observation_rule_counts.items())
                )
            )
    if acc.lowering_rejection_total and not all_rows_are_replayed:
        print(
            "All-row lowering rejections: "
            f"total={acc.lowering_rejection_total} "
            f"blocking={acc.blocking_lowering_rejection_total}"
        )
        if acc.lowering_rejection_rule_counts:
            print(
                "All-row lowering rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.lowering_rejection_rule_counts.items())
                )
            )
        if acc.blocking_lowering_rejection_rule_counts:
            print(
                "All-row blocking lowering rejection rules: "
                + ", ".join(
                    f"{rule_id}={count}"
                    for rule_id, count in sorted(acc.blocking_lowering_rejection_rule_counts.items())
                )
            )
    if acc.no_oracle_rows:
        print(f"Source unavailable rows ({len(acc.no_oracle_rows)}):")
        for r in acc.no_oracle_rows[:10]:
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
    if acc.error_rows:
        print(f"Error rows ({len(acc.error_rows)}):")
        for r in acc.error_rows[:10]:
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

    if not acc.ok_count:
        print("No valid results to report.")
        return

    # Determine whether commencement scores are available — use as primary when yes.
    has_commencement = len(acc.commencement_scores) > 0

    avg_raw = sum(acc.raw_scores) / len(acc.raw_scores)
    med_score_raw = sorted(acc.raw_scores)[len(acc.raw_scores) // 2]
    perfect_raw = sum(1 for score in acc.raw_scores if score == 1.0)
    ge90_raw = sum(1 for score in acc.raw_scores if score >= 0.9)
    ge80_raw = sum(1 for score in acc.raw_scores if score >= 0.8)
    with_effect_pages = acc.with_effect_pages_count
    with_effect_rows = acc.with_effect_rows_count
    if acc.core_ok_count > 0:
        core_avg_raw = sum(acc.core_scores) / len(acc.core_scores)
        print(f"Core raw avg: {core_avg_raw:.1%}")
    if acc.noncore_ok_count > 0:
        print("Non-core classes: " + ", ".join(f"{k}={v}" for k, v in sorted(acc.noncore_comparison_class_counts.items())))

    if has_commencement:
        # Commencement scores are primary; raw scores shown as secondary.
        avg_comm = sum(acc.commencement_scores) / len(acc.commencement_scores)
        med_comm = sorted(acc.commencement_scores)[len(acc.commencement_scores) // 2]
        perfect_comm = sum(1 for score in acc.commencement_scores if score == 1.0)
        ge90_comm = sum(1 for score in acc.commencement_scores if score >= 0.9)
        ge80_comm = sum(1 for score in acc.commencement_scores if score >= 0.8)
        avg_commenced_n = acc.avg_commenced_n_sum / len(acc.commencement_scores)
        print(f"\nEID score (commenced, N={len(acc.commencement_scores)}):")
        print(f"  Average:        {avg_comm:.1%}    (unfiltered: {avg_raw:.1%})")
        print(f"  Median:         {med_comm:.1%}    (unfiltered: {med_score_raw:.1%})")
        print(
            f"  Perfect (1.0):  {perfect_comm} ({100 * perfect_comm / len(acc.commencement_scores):.0f}%)"
            f"    (unfiltered: {perfect_raw})"
        )
        print(f"  >=90%:          {ge90_comm} ({100 * ge90_comm / len(acc.commencement_scores):.0f}%)    (unfiltered: {ge90_raw})")
        print(f"  >=80%:          {ge80_comm} ({100 * ge80_comm / len(acc.commencement_scores):.0f}%)    (unfiltered: {ge80_raw})")
        print(f"  Avg commenced EIDs: {avg_commenced_n:.0f}")
        print(f"  With parsed effect rows>0: {with_effect_rows}")
        print(f"  With effect-feed pages>0: {with_effect_pages}")
        if len(acc.core_comm_scores) > 0:
            avg_core_comm = sum(acc.core_comm_scores) / len(acc.core_comm_scores)
            print(f"  Core commenced avg: {avg_core_comm:.1%}")
    else:
        # No commencement data — show raw scores normally.
        print(f"\nEID similarity score (N={acc.ok_count}):")
        print(f"  Average:        {avg_raw:.1%}")
        print(f"  Median:         {med_score_raw:.1%}")
        print(f"  Perfect (1.0):  {perfect_raw} ({100 * perfect_raw / len(acc.raw_scores):.0f}%)")
        print(f"  >=90%:          {ge90_raw} ({100 * ge90_raw / len(acc.raw_scores):.0f}%)")
        print(f"  >=80%:          {ge80_raw} ({100 * ge80_raw / len(acc.raw_scores):.0f}%)")
        print(f"  With parsed effect rows>0: {with_effect_rows}")
        print(f"  With effect-feed pages>0: {with_effect_pages}")

    # Replay summary (only when --replay was active)
    if acc.replayed_count > 0:
        avg_replay_raw = sum(acc.replay_scores) / len(acc.replay_scores)
        avg_enacted_raw = sum(acc.replayed_enacted_scores) / len(acc.replayed_enacted_scores)
        perfect_replay_raw = sum(1 for score in acc.replay_scores if score == 1.0)
        if len(acc.core_replay_scores) > 0:
            core_enacted_raw = sum(acc.core_replayed_enacted_scores) / len(acc.core_replayed_enacted_scores)
            core_replay_raw = sum(acc.core_replay_scores) / len(acc.core_replay_scores)
        else:
            core_enacted_raw = core_replay_raw = 0.0

        if len(acc.regime_counts) == 1:
            (
                metadata_backfill,
                oracle_alignment,
                metadata_only_effects,
                applicability_mode,
                authority_mode,
            ), _count = next(iter(acc.regime_counts.items()))
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
            ), count in sorted(acc.regime_counts.items(), key=lambda item: (-item[1], item[0])):
                print(
                    f"  N={count}: metadata_backfill={metadata_backfill} "
                    f"oracle_alignment={oracle_alignment} "
                    f"metadata_only_effects={metadata_only_effects} "
                    f"applicability={applicability_mode} "
                    f"authority={authority_mode}"
                )
        if acc.total_authority_observations:
            print(f"  Authority observations: {acc.total_authority_observations}")
            if acc.authority_observation_rule_counts:
                print(
                    "  Authority observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.authority_observation_rule_counts.items())
                    )
                )
        if acc.total_authority_rejections:
            print(f"  Blocking authority rejections: {acc.total_authority_rejections}")
            if acc.authority_rejection_rule_counts:
                print(
                    "  Blocking authority rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.authority_rejection_rule_counts.items())
                    )
                )
        if acc.total_replay_adjudications:
            print(f"  Replay adjudications: {acc.total_replay_adjudications}")
            if acc.replay_adjudication_kind_counts:
                print(
                    "  Replay adjudication kinds: "
                    + ", ".join(
                        f"{kind}={count}"
                        for kind, count in sorted(acc.replay_adjudication_kind_counts.items())
                    )
                )
            if acc.replay_adjudication_bucket_counts:
                print(
                    "  Replay adjudication buckets: "
                    + ", ".join(
                        f"{bucket}={count}"
                        for bucket, count in sorted(acc.replay_adjudication_bucket_counts.items())
                    )
                )
            if acc.adjudication_samples_totals:
                print("  Replay adjudication samples:")
                for kind in sorted(acc.replay_adjudication_sample_kinds):
                    total = acc.adjudication_samples_totals.get(kind, 0)
                    if not total:
                        continue
                    samples = acc.adjudication_samples[kind]
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
        if acc.replay_effect_source_pathology_counts:
            print(
                "  Effect source pathologies: "
                + ", ".join(
                    f"{pathology}={count}"
                    for pathology, count in sorted(acc.replay_effect_source_pathology_counts.items())
                )
            )
        if acc.replay_manual_compile_status_counts:
            print(
                "  Manual compile frontier statuses: "
                + ", ".join(
                    f"{status}={count}"
                    for status, count in sorted(acc.replay_manual_compile_status_counts.items())
                )
            )
            if acc.replay_manual_compile_rule_counts:
                print(
                    "  Manual compile frontier rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.replay_manual_compile_rule_counts.items())
                    )
                )
        if acc.total_source_acquisition_observations:
            print(f"  Source acquisition observations: {acc.total_source_acquisition_observations}")
            if acc.source_acquisition_observation_rule_counts:
                print(
                    "  Source acquisition observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.source_acquisition_observation_rule_counts.items())
                    )
                )
        if acc.total_source_acquisition_rejections:
            print(f"  Source acquisition rejections: {acc.total_source_acquisition_rejections}")
            if acc.source_acquisition_rejection_rule_counts:
                print(
                    "  Source acquisition rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.source_acquisition_rejection_rule_counts.items())
                    )
                )
        if acc.total_lowering_observations:
            print(f"  Lowering observations: {acc.total_lowering_observations}")
            if acc.lowering_observation_rule_counts:
                print(
                    "  Lowering observation rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.lowering_observation_rule_counts.items())
                    )
                )
        if acc.total_lowering_rejections:
            print(
                "  Lowering rejections: "
                f"total={acc.total_lowering_rejections} "
                f"blocking={acc.total_blocking_lowering_rejections}"
            )
            if acc.lowering_rejection_rule_counts:
                print(
                    "  Lowering rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.lowering_rejection_rule_counts.items())
                    )
                )
            if acc.blocking_lowering_rejection_rule_counts:
                print(
                    "  Blocking lowering rejection rules: "
                    + ", ".join(
                        f"{rule_id}={count}"
                        for rule_id, count in sorted(acc.blocking_lowering_rejection_rule_counts.items())
                    )
                )

        if has_commencement:
            if acc.replay_commencement_scores:
                avg_replay_comm = sum(acc.replay_commencement_scores) / len(acc.replay_commencement_scores)
                avg_enacted_comm = sum(acc.replay_commencement_enacted_scores) / len(acc.replay_commencement_enacted_scores)
                delta_comm = avg_replay_comm - avg_enacted_comm
                perfect_replay_comm = sum(1 for score in acc.replay_commencement_scores if score == 1.0)
                print(f"\nReplay (commenced, N={len(acc.replay_commencement_scores)}, {acc.total_ops} ops total):")
                print(f"  Enacted avg:    {avg_enacted_comm:.1%}    (unfiltered: {avg_enacted_raw:.1%})")
                print(
                    f"  Replayed avg:   {avg_replay_comm:.1%} ({delta_comm:+.1%})    (unfiltered: {avg_replay_raw:.1%})"
                )
                print(
                    f"  Perfect replay: {perfect_replay_comm} ({100 * perfect_replay_comm / len(acc.replay_commencement_scores):.0f}%)"
                )
                print(f"  Improved:       {len(acc.improvements_comm)}  Regressed: {len(acc.regressions_comm)}")
                if acc.total_alignment_changes:
                    print(
                        f"  Oracle EID alignment: changed={acc.total_alignment_changes} "
                        f"oracle_assigned={acc.total_alignment_oracle_assigned} "
                        f"local_fallback={acc.total_alignment_local_fallback} "
                        f"transparent_wrapper_cleared={acc.total_alignment_transparent_wrapper_cleared} "
                        f"before_nodes={acc.total_alignment_before_nodes} "
                        f"after_nodes={acc.total_alignment_after_nodes} "
                        f"node_count_mismatch_rows={acc.alignment_node_count_mismatch_rows}"
                    )
                    if acc.alignment_match_method_counts:
                        print(
                            "  Oracle EID alignment methods: "
                            + ", ".join(
                                f"{method}={count}"
                                for method, count in sorted(acc.alignment_match_method_counts.items())
                            )
                        )
                if acc.regressions_comm:
                    print("\n  Top regressions:")
                    for r in acc.regressions_comm:
                        print(
                            f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                            f"  {_bench_row_evidence_context(r)}"
                        )
                if acc.improvements_comm:
                    print("\n  Top improvements:")
                    for r in acc.improvements_comm:
                        print(
                            f"    {r.statute_id:<30} {r.commencement_score:.1%} -> {r.replay_commencement_score:.1%}"
                            f"  {_bench_row_evidence_context(r)}"
                        )
        else:
            improved = len(acc.improvements_raw)
            regressed = len(acc.regressions_raw)
            delta = avg_replay_raw - avg_enacted_raw
            print(f"\nReplay score (N={acc.replayed_count}, {acc.total_ops} ops total):")
            print(f"  Enacted avg:    {avg_enacted_raw:.1%}")
            print(f"  Replayed avg:   {avg_replay_raw:.1%} ({delta:+.1%})")
            if acc.core_count > 0 and len(acc.core_replay_scores) > 0:
                print(
                    f"  Core replay avg: {core_replay_raw:.1%} "
                    f"({core_replay_raw - core_enacted_raw:+.1%}, N={len(acc.core_replay_scores)})"
                )
            print(f"  Perfect replay: {perfect_replay_raw} ({100 * perfect_replay_raw / acc.replayed_count:.0f}%)")
            print(f"  Improved:       {improved}  Regressed: {regressed}")
            if acc.total_alignment_changes:
                print(
                    f"  Oracle EID alignment: changed={acc.total_alignment_changes} "
                    f"oracle_assigned={acc.total_alignment_oracle_assigned} "
                    f"local_fallback={acc.total_alignment_local_fallback} "
                    f"transparent_wrapper_cleared={acc.total_alignment_transparent_wrapper_cleared} "
                    f"before_nodes={acc.total_alignment_before_nodes} "
                    f"after_nodes={acc.total_alignment_after_nodes} "
                    f"node_count_mismatch_rows={acc.alignment_node_count_mismatch_rows}"
                )
                if acc.alignment_match_method_counts:
                    print(
                        "  Oracle EID alignment methods: "
                        + ", ".join(
                            f"{method}={count}"
                            for method, count in sorted(acc.alignment_match_method_counts.items())
                        )
                    )
            if acc.regressions_raw:
                print("\n  Top regressions:")
                for r in acc.regressions_raw:
                    print(
                        f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  "
                        f"{_bench_row_evidence_context(r)}"
                    )
            if acc.improvements_raw:
                print("\n  Top improvements:")
                for r in acc.improvements_raw:
                    print(
                        f"    {r.statute_id:<30} {r.score:.1%} -> {r.replay_score:.1%}  "
                        f"{_bench_row_evidence_context(r)}"
                    )

    if acc.replay_errors:
        print(f"\nReplay errors ({len(acc.replay_errors)}):")
        for r in acc.replay_errors:
            print(f"  {r.statute_id}: {r.replay_error}")
            print(_source_line(r))

    if acc.commencement_errors:
        print(f"\nCommencement errors ({len(acc.commencement_errors)}):")
        for r in acc.commencement_errors:
            print(f"  {r.statute_id}: {r.commencement_error}")
            print(_source_line(r))

    # Text similarity summary
    if acc.text_scores:
        avg_text_enacted = sum(acc.text_scores) / len(acc.text_scores)
        print(f"\nText similarity (common EIDs, N={len(acc.text_scores)} EIDs):")
        print(f"  Enacted avg:    {avg_text_enacted:.1%}")
        if acc.replay_text_scores:
            avg_text_replay = sum(acc.replay_text_scores) / len(acc.replay_text_scores)
            avg_text_enacted_sub = sum(acc.text_scores[:len(acc.replay_text_scores)]) / len(acc.replay_text_scores)
            delta_text = avg_text_replay - avg_text_enacted_sub
            print(f"  Replayed avg:   {avg_text_replay:.1%} ({delta_text:+.1%})")

    # By type
    print("\nBy type:")
    for t in sorted(acc.type_counts):
        grp = acc.type_groups.get(t, [])
        a = sum(grp) / len(grp) if grp else 0.0
        p = acc.type_perfect_counts.get(t, 0)
        replay_grp = acc.type_replay_groups.get(t, [])
        if replay_grp:
            ar = sum(replay_grp) / len(replay_grp)
            print(f"  {t:<8} N={acc.type_counts[t]:5d}  enacted={a:.1%}  replay={ar:.1%}  perfect={p}")
        else:
            print(f"  {t:<8} N={acc.type_counts[t]:5d}  avg={a:.1%}  perfect={p}")

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
    worst_core = [r for r in acc.worst_core if _primary_score_for_row(r) < 1.0]
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
    if acc.worst_replay_core:
        print(f"\nWorst {len(acc.worst_replay_core)} core replay rows (by {worst_replay_score_label}):")
        for r in acc.worst_replay_core:
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

    if acc.worst_noncore:
        print(f"\nWorst {len(acc.worst_noncore)} non-core rows:")
        for r in acc.worst_noncore:
            print(
                f"  {r.statute_id:<30} {_score_fragment_for_row(r)} "
                f"enacted={r.n_enacted_eids:4d} oracle={r.n_oracle_eids:4d} "
                f"effect_rows={r.n_effect_rows:4d} effect_pages={(r.n_effect_feed_pages or r.n_effects):4d} "
                f"class={r.comparison_class}"
            )
            print(_source_line(r))

    _print_slowest_rows(acc, has_commencement=has_commencement)
    _print_highest_rss_rows(acc, has_commencement=has_commencement)


def _print_summary_only_report(acc: _BenchRunAccumulator) -> None:
    if not acc.ok_count:
        print("No valid results to report.")
        return
    has_commencement = len(acc.commencement_scores) > 0
    primary_scores = acc.commencement_scores if has_commencement else acc.raw_scores
    primary_label = "commenced" if has_commencement else "raw"
    if primary_scores:
        average = sum(primary_scores) / len(primary_scores)
        sorted_scores = sorted(primary_scores)
        mid = len(sorted_scores) // 2
        if len(sorted_scores) % 2:
            median = sorted_scores[mid]
        else:
            median = (sorted_scores[mid - 1] + sorted_scores[mid]) / 2
        print(f"EID score ({primary_label}, N={len(primary_scores)}): avg={average:.1%} median={median:.1%}")
    if acc.replay_scores:
        replay_average = sum(acc.replay_scores) / len(acc.replay_scores)
        print(
            f"Replay score (N={len(acc.replay_scores)}, ops={acc.total_ops}): "
            f"avg={replay_average:.1%}"
        )
    if acc.replay_commencement_scores:
        replay_comm_average = sum(acc.replay_commencement_scores) / len(
            acc.replay_commencement_scores
        )
        print(
            f"Replay commenced score (N={len(acc.replay_commencement_scores)}): "
            f"avg={replay_comm_average:.1%}"
        )
    print(
        "Evidence totals: "
        f"source_parse_obs={acc.source_parse_observations_total} "
        f"source_parse_rejections={acc.source_parse_rejections_total} "
        f"source_acquisition_obs={acc.source_acquisition_observation_total} "
        f"source_acquisition_rejections={acc.source_acquisition_rejection_total} "
        f"effect_feed_obs={acc.effect_feed_observations_total} "
        f"effect_feed_rejections={acc.effect_feed_rejections_total} "
        f"lowering_obs={acc.lowering_observation_total} "
        f"lowering_rejections={acc.lowering_rejection_total} "
        f"blocking_lowering={acc.blocking_lowering_rejection_total} "
        f"replay_adjudications={acc.replay_adjudication_total}"
    )
    if acc.highest_rss:
        peak = acc.highest_rss[0]
        print(f"Peak observed process RSS: {peak.process_maxrss_kb / 1024:.0f}MB after {peak.statute_id}")


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


def _format_file_size(path: Path) -> str:
    size = path.stat().st_size
    if size >= 1_000_000_000:
        return f"{size / 1_000_000_000:.1f}GB"
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f}MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f}KB"
    return f"{size}B"


def _save_score_witness_rows(results: list[_BenchResult], label: str) -> int:
    out_path = _score_witness_path(label)
    handle = None
    writer: csv.DictWriter | None = None
    count = 0
    for result in results:
        for row in _get_score_witness_dict_rows(result, label):
            if writer is None:
                handle = open(out_path, "w", newline="")
                writer = csv.DictWriter(handle, fieldnames=_SCORE_WITNESS_HEADERS)
                writer.writeheader()
            writer.writerow(row)
            count += 1
    if handle is not None:
        handle.close()
    if count == 0:
        if out_path.exists():
            out_path.unlink()
        return 0
    return count


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
    count = 0
    handle = None
    for result in results:
        for row in _bench_diagnostic_rows_for_result(result, label):
            if handle is None:
                handle = open(out_path, "w", encoding="utf-8")
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
            count += 1
    if handle is not None:
        handle.close()
    if count == 0:
        if out_path.exists():
            out_path.unlink()
        return 0
    return count


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


def _diagnostic_record_preview(record: Mapping[str, Any]) -> str:
    effect_id = str(record.get("effect_id") or "")
    affecting_act_id = str(record.get("affecting_act_id") or "")
    affecting_provisions = str(record.get("affecting_provisions") or "")
    locator = str(record.get("locator") or "")
    reason = " ".join(str(record.get("reason") or "").split())
    if len(reason) > 180:
        reason = reason[:177].rstrip() + "..."
    parts = []
    if effect_id:
        parts.append(f"effect={effect_id}")
    if affecting_act_id:
        parts.append(f"affecting={affecting_act_id}")
    if affecting_provisions:
        parts.append(f"provisions={affecting_provisions}")
    if locator:
        parts.append(f"locator={locator}")
    if reason:
        parts.append(f"reason={reason}")
    return " ".join(parts)


_DIAGNOSTIC_PREVIEW_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("literal_ellipsis_source", re.compile(r"^\s*\.\.\.\s*$")),
    ("definition_omission", re.compile(r"\bomit\s+the\s+definition\s+of\b", re.I)),
    (
        "entry_beginning_substitution",
        re.compile(r"\bfor\s+the\s+entry\s+beginning\s+[“\"'‘]", re.I),
    ),
    (
        "entry_substitution",
        re.compile(r"\bfor\s+the\s+entry\s+(?:relating\s+to\s+)?[“\"'‘]?", re.I),
    ),
    (
        "entry_omission",
        re.compile(r"\bomit\s+the\s+entry\s+[“\"'‘]", re.I),
    ),
    (
        "after_entry_insertion",
        re.compile(r"\bafter\s+(?:that\s+entry|(?:the\s+)?(?:first|second|third|final)\s+entry|the\s+entry\s+for)\b", re.I),
    ),
    (
        "beginning_each_child_insertion",
        re.compile(
            r"\bat\s+the\s+beginning\s+of\s+each\s+of\s+"
            r"(?:paragraphs|sub-paragraphs|subsections)\b",
            re.I,
        ),
    ),
    (
        "paragraph_substitution",
        re.compile(r"\bfor\s+paragraph\s+\([^)]+\)\s+substitute", re.I),
    ),
    (
        "structural_subunit_substitution",
        re.compile(r"\bfor\s+(?:sub-)?paragraphs?\s+\([^)]+\)\s+and\s+\([^)]+\)\s+substitute", re.I),
    ),
    (
        "referential_pronoun_substitution",
        re.compile(r"\bwhere\s+(?:it|they)\s+refers?\s+to\b", re.I),
    ),
    (
        "after_child_insertion",
        re.compile(r"\bafter\s+(?:paragraph|sub-paragraph|subsection)\s+\([^)]+\),?\s+insert\b", re.I),
    ),
    (
        "at_end_block_insertion",
        re.compile(r"\bat\s+the\s+end\s+insert\s*[—-]", re.I),
    ),
    (
        "malformed_quoted_substitution",
        re.compile(
            r"\bfor\s+(?:the\s+)?words?\s+[“\"'‘].+?[”\"'’]\s+"
            r"\bthere\s+(?:is|are|shall\s+be)\s+substituted\s+[“\"'‘][^”\"'’]*$",
            re.I,
        ),
    ),
    (
        "words_following_paragraphs_omission",
        re.compile(r"\bomit\s+the\s+words\s+following\s+the\s+paragraphs\b", re.I),
    ),
    (
        "range_to_end_substitution",
        re.compile(r"\bfor\s+(?:the\s+)?words?\s+from\b.+\bto\s+the\s+end\b", re.I),
    ),
    (
        "range_substitution",
        re.compile(r"\bfor\s+(?:the\s+)?words?\s+from\b.+\bto\b", re.I),
    ),
    (
        "quoted_word_substitution",
        re.compile(
            r"\bfor\s+(?:the\s+)?words?\s+[“\"'‘].+"
            r"\b(?:substitute|there\s+(?:is|are|shall\s+be)\s+substituted)\b",
            re.I,
        ),
    ),
    (
        "table_end_insertion",
        re.compile(r"\bin\s+the\s+table\b.+\bat\s+the\s+end\b", re.I),
    ),
    (
        "appropriate_place",
        re.compile(r"\bappropriate\s+place\b", re.I),
    ),
)


def _diagnostic_preview_pattern(record: Mapping[str, Any]) -> str:
    text = " ".join(str(record.get("extracted_text_preview") or "").split())
    if not text:
        return "no_extracted_preview"
    for name, pattern in _DIAGNOSTIC_PREVIEW_PATTERNS:
        if pattern.search(text):
            return name
    if re.search(r"\b(substitute|insert|omit|repeal|leave\s+out)\b", text, re.I):
        return "unclassified_instruction_text"
    return "other_source_shape"


def _print_bench_diagnostic_samples(
    label: str,
    *,
    lane: str,
    rule_id: str = "",
    pattern: str = "",
    blocking_only: bool = False,
    limit: int = 5,
    pattern_summary: bool = False,
) -> None:
    path = _bench_diagnostics_path(label)
    if not path.exists():
        print(f"\nDiagnostic samples: sidecar not found for {label}")
        return
    sample_limit = max(0, limit)
    matched = 0
    lane_total = 0
    rule_counts: Counter[str] = Counter()
    statute_counts: Counter[str] = Counter()
    pattern_counts: Counter[str] = Counter()
    samples: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            parsed_row = json.loads(line)
            if not isinstance(parsed_row, dict):
                continue
            if parsed_row.get("schema") != "uk_bench_diagnostic.v1":
                continue
            if str(parsed_row.get("diagnostic_lane") or "") != lane:
                continue
            lane_total += 1
            current_rule = str(parsed_row.get("rule_id") or "")
            if rule_id and current_rule != rule_id:
                continue
            if blocking_only and not bool(parsed_row.get("blocking")):
                continue
            record = parsed_row.get("record")
            current_pattern = (
                _diagnostic_preview_pattern(record) if isinstance(record, dict) else "unknown"
            )
            if pattern and current_pattern != pattern:
                continue
            matched += 1
            rule_counts[current_rule or "unknown"] += 1
            statute_counts[str(parsed_row.get("statute_id") or "unknown")] += 1
            if pattern_summary:
                pattern_counts[current_pattern] += 1
            if len(samples) < sample_limit:
                samples.append(parsed_row)

    print(
        "\nDiagnostic samples: "
        f"lane={lane} "
        f"rule={rule_id or '*'} "
        f"pattern={pattern or '*'} "
        f"blocking_only={blocking_only} "
        f"matched={matched} "
        f"lane_total={lane_total}"
    )
    if rule_counts:
        print(
            "  Rules: "
            + ", ".join(
                f"{key}={count}" for key, count in sorted(rule_counts.items())
            )
        )
    if statute_counts:
        print(
            "  Statutes: "
            + ", ".join(
                f"{key}={count}" for key, count in sorted(statute_counts.items())
            )
        )
    if pattern_summary and pattern_counts:
        print(
            "  Patterns: "
            + ", ".join(
                f"{key}={count}" for key, count in pattern_counts.most_common()
            )
        )
    for row in samples:
        record = row.get("record") if isinstance(row.get("record"), dict) else {}
        pattern_part = (
            f"pattern={_diagnostic_preview_pattern(record)} "
            if pattern_summary
            else ""
        )
        print(
            "  "
            f"{row.get('statute_id')} "
            f"rule={row.get('rule_id') or '-'} "
            f"blocking={bool(row.get('blocking'))} "
            f"{pattern_part}"
            + _diagnostic_record_preview(record)
        )


def _append_history(
    results: list[_BenchResult] | _BenchRunAccumulator,
    label: str,
    score_witness_count: int,
) -> None:
    if isinstance(results, _BenchRunAccumulator):
        acc = results
    else:
        acc = _BenchRunAccumulator(
            has_commencement=any(
                r.commencement_score >= 0.0
                for r in results
                if r.status == "OK" and r.n_oracle_eids > 0
            ),
        )
        for r in results:
            acc.feed(r)

    primary_scores = acc.primary_scores
    replay_scores = acc.replay_scores
    commencement_scores = acc.commencement_scores
    row_status_counts = acc.row_status_counts
    enacted_source_status_counts = acc.enacted_source_counts
    oracle_source_status_counts = acc.oracle_source_counts

    regime_counts: Counter[str] = Counter()
    for (mb, oa, moe, app, auth), count in acc.regime_counts.items():
        key = (
            f"metadata_backfill={int(mb)}"
            f";oracle_alignment={int(oa)}"
            f";metadata_only_effects={int(moe)}"
            f";applicability={app}"
            f";authority={auth}"
        )
        regime_counts[key] = count

    observation_rule_counts = acc.effect_feed_observation_rule_counts
    effect_feed_rejection_rule_counts = acc.effect_feed_rejection_rule_counts
    source_parse_observation_rule_counts = acc.source_parse_observation_rule_counts
    source_parse_rejection_rule_counts = acc.source_parse_rejection_rule_counts
    effect_source_pathology_counts = acc.effect_source_pathology_counts
    manual_compile_status_counts = acc.manual_compile_status_counts
    manual_compile_rule_counts = acc.manual_compile_rule_counts
    source_acquisition_observation_rule_counts = acc.source_acquisition_observation_rule_counts
    source_acquisition_rejection_rule_counts = acc.source_acquisition_rejection_rule_counts
    bench_exception_rule_counts = acc.bench_exception_rule_counts
    authority_observation_rule_counts = acc.authority_observation_rule_counts
    authority_rule_counts = acc.authority_rejection_rule_counts
    lowering_observation_rule_counts = acc.lowering_observation_rule_counts
    lowering_rule_counts = acc.lowering_rejection_rule_counts
    blocking_lowering_rule_counts = acc.blocking_lowering_rejection_rule_counts
    replay_adjudication_kind_counts = acc.replay_adjudication_kind_counts
    replay_adjudication_bucket_counts = acc.replay_adjudication_bucket_counts
    residual_claim_tier_counts = acc.residual_claim_tier_counts
    residual_claim_kind_counts = acc.residual_claim_kind_counts

    _HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(_HISTORY_CSV, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(_HISTORY_HEADERS))
        if _history_needs_header():
            writer.writeheader()
        writer.writerow(
            {
                "label": label,
                "n_total": acc.total_count,
                "n_ok": acc.ok_count,
                "n_core_ok": acc.core_ok_count,
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
                "score_mode": _primary_score_mode(acc) if acc.ok_count else "none",
                "avg_score": _format_history_average(primary_scores),
                "n_perfect": sum(1 for score in primary_scores if score == 1.0),
                "avg_raw_score": _format_history_average(acc.raw_scores),
                "avg_replay_score": _format_history_average(replay_scores),
                "avg_commencement_score": _format_history_average(commencement_scores),
                "n_commencement_scored": len(commencement_scores),
                "n_replay_scored": len(replay_scores),
                "n_replay_errors": acc.replay_error_count,
                "n_commencement_errors": acc.commencement_error_count,
                "source_parse_observations": acc.source_parse_observations_total,
                "source_parse_observation_rules": json.dumps(
                    dict(sorted(source_parse_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "source_parse_rejections": acc.source_parse_rejections_total,
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
                "source_acquisition_observations": acc.source_acquisition_observation_total,
                "source_acquisition_observation_rules": json.dumps(
                    dict(sorted(source_acquisition_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "source_acquisition_rejections": acc.source_acquisition_rejection_total,
                "source_acquisition_rejection_rules": json.dumps(
                    dict(sorted(source_acquisition_rejection_rule_counts.items())),
                    sort_keys=True,
                ),
                "bench_exceptions": acc.bench_exceptions_total,
                "bench_exception_rules": json.dumps(
                    dict(sorted(bench_exception_rule_counts.items())),
                    sort_keys=True,
                ),
                "effect_feed_observations": acc.effect_feed_observations_total,
                "effect_feed_observation_rules": json.dumps(
                    dict(sorted(observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "effect_feed_rejections": acc.effect_feed_rejections_total,
                "effect_feed_rejection_rules": json.dumps(
                    dict(sorted(effect_feed_rejection_rule_counts.items())),
                    sort_keys=True,
                ),
                "authority_observations": acc.authority_observation_total,
                "authority_observation_rules": json.dumps(
                    dict(sorted(authority_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "authority_rejections": acc.authority_rejection_total,
                "authority_rejection_rules": json.dumps(
                    dict(sorted(authority_rule_counts.items())),
                    sort_keys=True,
                ),
                "lowering_observations": acc.lowering_observation_total,
                "lowering_observation_rules": json.dumps(
                    dict(sorted(lowering_observation_rule_counts.items())),
                    sort_keys=True,
                ),
                "lowering_rejections": acc.lowering_rejection_total,
                "lowering_rejection_rules": json.dumps(
                    dict(sorted(lowering_rule_counts.items())),
                    sort_keys=True,
                ),
                "blocking_lowering_rejections": acc.blocking_lowering_rejection_total,
                "blocking_lowering_rejection_rules": json.dumps(
                    dict(sorted(blocking_lowering_rule_counts.items())),
                    sort_keys=True,
                ),
                "replay_adjudications": acc.replay_adjudication_total,
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
                "uk_residual_section_claims": acc.uk_residual_section_claims_total,
                "score_witness_rows": score_witness_count,
                "replay_regimes": json.dumps(dict(sorted(regime_counts.items())), sort_keys=True),
                "max_process_maxrss_kb": acc.max_process_maxrss_kb,
                "max_process_maxrss_statute_id": acc.max_process_maxrss_statute_id,
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


def _format_history_rss_mb(value: str) -> str:
    if not value:
        return "n/a"
    try:
        kb = int(value)
    except ValueError:
        return value
    if kb <= 0:
        return "n/a"
    return f"{kb / 1024:.0f}MB"


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
                if _is_current_history_header(raw_row):
                    schema = "current"
                elif _is_legacy_history_header(raw_row):
                    schema = "legacy"
                else:
                    schema = "unknown"
                continue
            if not header:
                continue
            padded_row = raw_row + [""] * max(len(header) - len(raw_row), 0)
            rows.append((schema, dict(zip(header, padded_row))))
    return rows


def _is_current_history_header(header: Sequence[str]) -> bool:
    fields = set(header)
    required_fields = {
        "label",
        "n_total",
        "n_ok",
        "n_core_ok",
        "row_status_counts",
        "score_mode",
        "avg_raw_score",
        "score_witness_rows",
        "replay_regimes",
        "timestamp",
    }
    return header[:4] == ["label", "n_total", "n_ok", "n_core_ok"] and required_fields <= fields


def _is_legacy_history_header(header: Sequence[str]) -> bool:
    return header[:6] == [
        "label",
        "n_total",
        "n_ok",
        "avg_score",
        "n_perfect",
        "timestamp",
    ]


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
                f"max_rss={_format_history_rss_mb(row.get('max_process_maxrss_kb', ''))} "
                f"rss_row={row.get('max_process_maxrss_statute_id', '') or 'n/a'} "
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
            feed_rejection_rules = row.get("effect_feed_rejection_rules", "")
            if feed_rejection_rules and feed_rejection_rules != "{}":
                print(f"  feed_rejection_rules: {feed_rejection_rules}")
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
                "duration_s",
                "process_maxrss_kb",
                _PHASE_TIMING_TOTAL_HEADER,
                *_PHASE_TIMING_HEADERS,
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
                "duration_s",
                "process_maxrss_kb",
                _PHASE_TIMING_TOTAL_HEADER,
                *_PHASE_TIMING_HEADERS,
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
                    f"{r.duration_s:.3f}",
                    r.process_maxrss_kb,
                    *_phase_timing_csv_values(r),
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
                    f"{r.duration_s:.3f}",
                    r.process_maxrss_kb,
                    *_phase_timing_csv_values(r),
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


def _load_run(label: str, *, include_diagnostics: bool = True) -> list[_BenchResult]:
    path = _BENCH_DIR / f"{label}.csv"
    if not path.exists():
        print(f"No saved run with label '{label}'. Available:", file=sys.stderr)
        for p in sorted(_BENCH_DIR.glob("*.csv")):
            print(f"  {p.stem}", file=sys.stderr)
        sys.exit(1)

    diagnostic_rows_by_statute = (
        _load_bench_diagnostic_rows(label) if include_diagnostics else {}
    )
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
            duration_s = float(row.get("duration_s", "0") or 0.0)
            process_maxrss_kb = int(row.get("process_maxrss_kb", "0") or 0)
            phase_timings = _load_phase_timings(row)
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
                    duration_s=duration_s,
                    process_maxrss_kb=process_maxrss_kb,
                    phase_timings=phase_timings,
                )
            )
    return results


def _show_run(
    label: str,
    *,
    phase_timings: bool = False,
    replay_adjudication_sample_kinds: Sequence[str] = (),
    replay_adjudication_sample_limit: int = 5,
    diagnostic_sample_lane: str = "",
    diagnostic_sample_rule: str = "",
    diagnostic_sample_pattern: str = "",
    diagnostic_sample_blocking: bool = False,
    diagnostic_sample_limit: int = 5,
    diagnostic_pattern_summary: bool = False,
    summary_only: bool = False,
) -> None:
    include_diagnostics = bool(replay_adjudication_sample_kinds) and not summary_only
    results = _load_run(
        label,
        include_diagnostics=include_diagnostics,
    )
    _print_report(
        results,
        label,
        replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
        summary_only=summary_only,
    )
    if phase_timings and not summary_only:
        _print_phase_timing_rows(results)
    score_witness_path = _score_witness_path(label)
    score_witness_count = _count_csv_data_rows(score_witness_path)
    if score_witness_count:
        print(f"\nScore witness sidecar: {score_witness_path} rows={score_witness_count}")
    diagnostics_path = _bench_diagnostics_path(label)
    if diagnostics_path.exists():
        if summary_only:
            print(
                f"Bench diagnostics sidecar: {diagnostics_path} "
                f"size={_format_file_size(diagnostics_path)} rows=not-counted"
            )
        else:
            diagnostics_count = _count_jsonl_rows(diagnostics_path)
            if diagnostics_count:
                print(f"Bench diagnostics sidecar: {diagnostics_path} rows={diagnostics_count}")
    if diagnostic_sample_lane:
        _print_bench_diagnostic_samples(
            label,
            lane=diagnostic_sample_lane,
            rule_id=diagnostic_sample_rule,
            pattern=diagnostic_sample_pattern,
            blocking_only=diagnostic_sample_blocking,
            limit=diagnostic_sample_limit,
            pattern_summary=diagnostic_pattern_summary,
        )


def _primary_score_mode(
    results: list[_BenchResult] | _BenchRunAccumulator,
    *,
    replay_primary: bool = False,
) -> str:
    if isinstance(results, _BenchRunAccumulator):
        commencement_count = len(results.commencement_scores)
        ok_count = results.ok_count
    else:
        commencement_count = sum(1 for r in results if r.commencement_score >= 0.0)
        ok_count = len(results)
        if replay_primary:
            replay_count = sum(
                1
                for r in results
                if r.replay_score >= 0.0 or r.replay_commencement_score >= 0.0
            )
            if replay_count == ok_count and ok_count > 0:
                return "replay-primary"
            if replay_count > 0:
                return "mixed replay/raw"
    if commencement_count == 0:
        return "raw"
    if commencement_count == ok_count:
        return "commencement"
    return "mixed"


def _compare_runs(label_a: str, label_b: str, *, summary_only: bool = False) -> None:
    def _primary_scores(results: list[_BenchResult]) -> dict[str, float]:
        return {r.statute_id: _bench_compare_primary_score(r) for r in results}

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

    def _format_seconds_delta(before: float, after: float) -> str:
        return f"{before:.2f}s -> {after:.2f}s ({after - before:+.2f}s)"

    def _phase_timing_summary(
        results: list[_BenchResult],
        statute_ids: set[str],
    ) -> tuple[int, float, float, dict[str, float]]:
        phase_totals: Counter[str] = Counter()
        measured_total = 0.0
        row_total = 0.0
        count = 0
        for result in results:
            if result.statute_id not in statute_ids or not result.phase_timings:
                continue
            count += 1
            measured_total += sum(result.phase_timings.values())
            row_total += result.duration_s
            phase_totals.update(result.phase_timings)
        return count, measured_total, row_total, dict(phase_totals)

    def _format_phase_delta_summary(
        phases_a: Mapping[str, float],
        phases_b: Mapping[str, float],
    ) -> str:
        keys = set(phases_a) | set(phases_b)
        if not keys:
            return ""
        ranked_keys = sorted(
            keys,
            key=lambda key: (abs(phases_b.get(key, 0.0) - phases_a.get(key, 0.0)), key),
            reverse=True,
        )
        return " ".join(
            f"{key}={phases_b.get(key, 0.0) - phases_a.get(key, 0.0):+.2f}s"
            for key in ranked_keys[:8]
        )

    results_a = _load_run(label_a, include_diagnostics=False)
    results_b = _load_run(label_b, include_diagnostics=False)
    score_mode_a = _primary_score_mode(results_a, replay_primary=True)
    score_mode_b = _primary_score_mode(results_b, replay_primary=True)
    score_witness_path_a = _score_witness_path(label_a)
    score_witness_path_b = _score_witness_path(label_b)
    score_witness_count_a = _count_csv_data_rows(score_witness_path_a)
    score_witness_count_b = _count_csv_data_rows(score_witness_path_b)
    results_by_id_a = {result.statute_id: result for result in results_a}
    results_by_id_b = {result.statute_id: result for result in results_b}

    def _missing_replay_examples(results: list[_BenchResult]) -> str:
        examples = [
            result.statute_id
            for result in results
            if result.replay_commencement_score < 0.0
            and result.replay_score < 0.0
            and (
                result.commencement_score >= 0.0
                or result.score >= 0.0
            )
        ][:5]
        return ", ".join(examples)

    if score_mode_a == "mixed replay/raw" or score_mode_b == "mixed replay/raw":
        print(f"\n=== UK Bench Compare: {label_a} -> {label_b} ===")
        print(f"Score mode: {score_mode_a} -> {score_mode_b}")
        if score_mode_a == "mixed replay/raw":
            print(
                "ERROR: baseline mixes replay-primary rows with fallback raw/"
                "commencement rows; compare would silently average different score lanes"
            )
            examples = _missing_replay_examples(results_a)
            if examples:
                print(f"  Missing baseline replay-primary examples: {examples}")
        if score_mode_b == "mixed replay/raw":
            print(
                "ERROR: current mixes replay-primary rows with fallback raw/"
                "commencement rows; compare would silently average different score lanes"
            )
            examples = _missing_replay_examples(results_b)
            if examples:
                print(f"  Missing current replay-primary examples: {examples}")
        return

    a = _primary_scores(results_a)
    b = _primary_scores(results_b)
    common = set(a) & set(b)
    only_a = set(a) - set(b)
    only_b = set(b) - set(a)

    improved = [(k, a[k], b[k]) for k in common if b[k] > a[k] + 0.001]
    regressed = [(k, a[k], b[k]) for k in common if b[k] < a[k] - 0.001]

    avg_a = sum(a[k] for k in common) / len(common) if common else 0
    avg_b = sum(b[k] for k in common) / len(common) if common else 0
    only_label_a = f"{label_a} (left)" if label_a == label_b else label_a
    only_label_b = f"{label_b} (right)" if label_a == label_b else label_b

    print(f"\n=== UK Bench Compare: {label_a} -> {label_b} ===")
    print(f"Score mode: {score_mode_a} -> {score_mode_b}")
    print(f"Common statutes: {len(common)}")
    print(f"Only in {only_label_a}: {len(only_a)}")
    print(f"Only in {only_label_b}: {len(only_b)}")
    print(
        "Score witness sidecars: "
        f"{label_a}={score_witness_path_a} rows={score_witness_count_a} -> "
        f"{label_b}={score_witness_path_b} rows={score_witness_count_b}"
    )
    if summary_only:
        print(
            "Row statuses: "
            f"{_status_counts(results_a, 'status')} -> {_status_counts(results_b, 'status')}"
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
        print(
            "Evidence totals: "
            f"source_parse_obs={_sum_field(results_a, 'source_parse_observation_count')}"
            f"->{_sum_field(results_b, 'source_parse_observation_count')} "
            f"source_acquisition_obs={_sum_field(results_a, 'source_acquisition_observation_count')}"
            f"->{_sum_field(results_b, 'source_acquisition_observation_count')} "
            f"lowering_rejections={_sum_field(results_a, 'lowering_rejection_count')}"
            f"->{_sum_field(results_b, 'lowering_rejection_count')} "
            f"blocking_lowering={_sum_field(results_a, 'blocking_lowering_rejection_count')}"
            f"->{_sum_field(results_b, 'blocking_lowering_rejection_count')} "
            f"replay_adjudications={_sum_field(results_a, 'replay_adjudication_count')}"
            f"->{_sum_field(results_b, 'replay_adjudication_count')}"
        )
        print(f"Average: {avg_a:.1%} -> {avg_b:.1%} ({avg_b - avg_a:+.1%})")
        print(f"Improved: {len(improved)}, Regressed: {len(regressed)}")
        return
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
    timed_common = {
        statute_id
        for statute_id in common
        if results_by_id_a[statute_id].phase_timings
        and results_by_id_b[statute_id].phase_timings
    }
    phase_count_a, measured_a, row_time_a, phases_a = _phase_timing_summary(
        results_a,
        timed_common,
    )
    phase_count_b, measured_b, row_time_b, phases_b = _phase_timing_summary(
        results_b,
        timed_common,
    )
    if phase_count_a and phase_count_b:
        print(
            "Phase timings: "
            f"common_rows={min(phase_count_a, phase_count_b)} "
            f"measured={_format_seconds_delta(measured_a, measured_b)} "
            f"row={_format_seconds_delta(row_time_a, row_time_b)}"
        )
        phase_delta_summary = _format_phase_delta_summary(phases_a, phases_b)
        if phase_delta_summary:
            print(f"Phase timing deltas: {phase_delta_summary}")
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


def _corpus_source_closure_summary(
    entries: Sequence[dict[str, object]],
    *,
    archive: Farchive,
    applicability_mode: str,
) -> dict[str, object]:
    from lawvm.uk_legislation.effects import (
        get_affecting_act_xml_from_archive,
        load_effects_for_statute_from_archive,
        uk_effect_requires_affecting_source_for_replay,
    )

    row_closure_counts: Counter[str] = Counter()
    row_closure_statuses: dict[str, str] = {}
    required_act_status_cache: dict[str, str] = {}
    required_effect_count = 0
    effect_row_count = 0
    required_act_ref_count = 0
    feed_observation_count = 0
    feed_rejection_count = 0
    feed_observation_rule_counts: Counter[str] = Counter()
    feed_rejection_rule_counts: Counter[str] = Counter()
    for entry in entries:
        statute_id = str(entry.get("statute_id") or "")
        if not statute_id:
            row_closure_counts["missing_statute_id"] += 1
            continue
        feed_observations: list[dict[str, Any]] = []
        effects = load_effects_for_statute_from_archive(
            statute_id,
            archive,
            parse_rejections_out=feed_observations,
        )
        effect_row_count += len(effects)
        feed_observation_count += len(feed_observations)
        for observation in feed_observations:
            rule_id = str(observation.get("rule_id") or "unknown")
            feed_observation_rule_counts[rule_id] += 1
            if is_blocking_compile_record(observation):
                feed_rejection_count += 1
                feed_rejection_rule_counts[rule_id] += 1

        required_act_ids: set[str] = set()
        for effect in effects:
            if not uk_effect_requires_affecting_source_for_replay(
                effect,
                applicability_mode=applicability_mode,
            ):
                continue
            required_effect_count += 1
            act_id = str(effect.affecting_act_id or "")
            if act_id:
                required_act_ids.add(act_id)
        required_act_ref_count += len(required_act_ids)
        if not required_act_ids:
            closure_status = "not_required"
            row_closure_counts[closure_status] += 1
            row_closure_statuses[statute_id] = closure_status
            continue

        statuses: list[str] = []
        for act_id in sorted(required_act_ids):
            if act_id not in required_act_status_cache:
                required_act_status_cache[act_id] = _source_state(
                    get_affecting_act_xml_from_archive(act_id, archive)
                )[0]
            statuses.append(required_act_status_cache[act_id])
        if statuses and all(status == "available" for status in statuses):
            closure_status = "full"
        elif any(status == "available" for status in statuses):
            closure_status = "partial"
        else:
            closure_status = "missing"
        row_closure_counts[closure_status] += 1
        row_closure_statuses[statute_id] = closure_status

    return {
        "effect_row_count": effect_row_count,
        "required_effect_count": required_effect_count,
        "required_affecting_act_ref_count": required_act_ref_count,
        "unique_required_affecting_act_count": len(required_act_status_cache),
        "row_closure_counts": dict(sorted(row_closure_counts.items())),
        "row_closure_statuses": dict(sorted(row_closure_statuses.items())),
        "required_affecting_act_source_status_counts": dict(
            sorted(Counter(required_act_status_cache.values()).items())
        ),
        "effect_feed_observation_count": feed_observation_count,
        "effect_feed_observation_rule_counts": dict(
            sorted(feed_observation_rule_counts.items())
        ),
        "effect_feed_rejection_count": feed_rejection_count,
        "effect_feed_rejection_rule_counts": dict(
            sorted(feed_rejection_rule_counts.items())
        ),
    }


def _print_corpus_stats(
    entries: Sequence[dict[str, object]],
    *,
    source_closure_summary: Mapping[str, object] | None = None,
) -> None:
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
    if source_closure_summary is not None:
        print("Affecting source closure: current XML required for replay")
        print(f"  Effect rows inspected: {source_closure_summary['effect_row_count']}")
        print(
            "  Replay source-required effects: "
            f"{source_closure_summary['required_effect_count']}"
        )
        print(
            "  Required affecting-act refs: "
            f"{source_closure_summary['required_affecting_act_ref_count']}"
        )
        print(
            "  Unique required affecting acts: "
            f"{source_closure_summary['unique_required_affecting_act_count']}"
        )
        print(f"  Rows by closure: {source_closure_summary['row_closure_counts']}")
        print(
            "  Required affecting-act source status: "
            f"{source_closure_summary['required_affecting_act_source_status_counts']}"
        )
        print(
            "  Effect feed observations: "
            f"{source_closure_summary['effect_feed_observation_count']} "
            f"{source_closure_summary['effect_feed_observation_rule_counts']}"
        )
        print(
            "  Effect feed rejections: "
            f"{source_closure_summary['effect_feed_rejection_count']} "
            f"{source_closure_summary['effect_feed_rejection_rule_counts']}"
        )


def _filter_replay_source_closed_entries(
    entries: Sequence[dict[str, object]],
    *,
    source_closure_summary: Mapping[str, object],
) -> list[dict[str, object]]:
    raw_statuses = source_closure_summary.get("row_closure_statuses")
    if not isinstance(raw_statuses, Mapping):
        return list(entries)
    allowed_statuses = {"full", "not_required"}
    selected: list[dict[str, object]] = []
    for entry in entries:
        statute_id = str(entry.get("statute_id") or "")
        if str(raw_statuses.get(statute_id) or "") in allowed_statuses:
            selected.append(dict(entry))
    return selected


def _stratified_source_complete_sample(
    entries: Sequence[dict[str, object]],
    *,
    size: int,
    hard: bool = False,
) -> list[dict[str, object]]:
    if size < 1:
        raise ValueError("--curate-size must be a positive integer")
    groups: dict[tuple[str, str, str], list[dict[str, object]]] = {}
    for entry in entries:
        if not _is_source_complete_entry(entry):
            continue
        if hard and _effect_bucket(entry) == "0":
            continue
        key = (
            str(entry.get("type") or "unknown"),
            _entry_decade(entry),
            _effect_bucket(entry),
        )
        groups.setdefault(key, []).append(entry)
    for group in groups.values():
        if hard:
            group.sort(
                key=lambda entry: (
                    *(-value for value in _uk_bench_parallel_submission_cost(dict(entry))),
                    str(entry.get("statute_id") or ""),
                )
            )
        else:
            group.sort(key=lambda entry: str(entry.get("statute_id") or ""))

    def hard_group_key(key: tuple[str, str, str]) -> tuple[int, int, int, int, tuple[str, str, str]]:
        head = groups[key][0]
        return (
            *(-value for value in _uk_bench_parallel_submission_cost(dict(head))),
            key,
        )

    selected: list[dict[str, object]] = []
    group_keys = sorted(groups, key=hard_group_key) if hard else sorted(groups)
    while len(selected) < size and group_keys:
        next_keys: list[tuple[str, str, str]] = []
        for key in group_keys:
            group = groups[key]
            if group and len(selected) < size:
                selected.append(group.pop(0))
            if group:
                next_keys.append(key)
        group_keys = sorted(next_keys, key=hard_group_key) if hard else next_keys
    return selected


def _write_curated_corpus(
    entries: Sequence[dict[str, object]],
    *,
    output: Path,
    size: int,
    hard: bool = False,
) -> list[dict[str, object]]:
    selected = _stratified_source_complete_sample(entries, size=size, hard=hard)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_CORPUS_FIELDNAMES)
        writer.writeheader()
        for entry in selected:
            writer.writerow({field: entry.get(field, "") for field in _CORPUS_FIELDNAMES})
    return selected


class _CuratedCorpusRequest(NamedTuple):
    output: Path | None
    size: int
    preset: str


def _curated_corpus_request(args: Any) -> _CuratedCorpusRequest:
    preset = str(getattr(args, "curate_preset", "") or "")
    if preset and preset not in _CURATE_PRESET_SIZES:
        raise ValueError(f"unknown UK curate preset: {preset}")
    raw_size = getattr(args, "curate_size", None)
    size = int(raw_size) if raw_size is not None else _CURATE_PRESET_SIZES.get(preset, 200)
    raw_output = getattr(args, "curate_corpus", None)
    if raw_output:
        output = Path(raw_output)
    elif preset:
        output = _CORPUS_CSV.parent / _CURATE_PRESET_FILENAMES[preset]
    else:
        output = None
    return _CuratedCorpusRequest(output=output, size=size, preset=preset)


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
    diagnostic_sample_limit = int(
        getattr(args, "diagnostic_sample_limit", 5) or 0
    )
    if diagnostic_sample_limit < 0:
        print(
            "error: --diagnostic-sample-limit must be zero or a positive integer",
            file=sys.stderr,
        )
        sys.exit(2)
    replay_adjudication_sample_kinds = tuple(
        getattr(args, "replay_adjudication_samples", None) or ()
    )

    if args.show:
        _show_run(
            args.show,
            phase_timings=getattr(args, "phase_timings", False),
            replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
            replay_adjudication_sample_limit=replay_adjudication_sample_limit,
            diagnostic_sample_lane=str(getattr(args, "diagnostic_sample_lane", "") or ""),
            diagnostic_sample_rule=str(getattr(args, "diagnostic_sample_rule", "") or ""),
            diagnostic_sample_pattern=str(
                getattr(args, "diagnostic_sample_pattern", "") or ""
            ),
            diagnostic_sample_blocking=bool(
                getattr(args, "diagnostic_sample_blocking", False)
            ),
            diagnostic_sample_limit=diagnostic_sample_limit,
            diagnostic_pattern_summary=bool(
                getattr(args, "diagnostic_pattern_summary", False)
            ),
            summary_only=getattr(args, "summary_only", False),
        )
        return

    if args.compare:
        _compare_runs(
            args.compare[0],
            args.compare[1],
            summary_only=getattr(args, "summary_only", False),
        )
        return

    limit = getattr(args, "limit", None)
    if limit is not None and limit < 0:
        print("error: --limit must be zero or a positive integer", file=sys.stderr)
        sys.exit(2)
    curated_corpus_request = _curated_corpus_request(args)
    if curated_corpus_request.output is not None and curated_corpus_request.size < 1:
        print("error: --curate-size must be a positive integer", file=sys.stderr)
        sys.exit(2)
    _par = getattr(args, "parallel", None)
    if _par is not None and _par < 1:
        print("error: --parallel must be a positive integer", file=sys.stderr)
        sys.exit(2)
    worker_max_tasks = getattr(args, "worker_max_tasks", None)
    if worker_max_tasks is not None and worker_max_tasks < 1:
        print("error: --worker-max-tasks must be a positive integer", file=sys.stderr)
        sys.exit(2)
    min_year = getattr(args, "min_year", None)
    max_year = getattr(args, "max_year", None)
    if min_year is None and curated_corpus_request.preset in _CURATE_PRESET_MIN_YEARS:
        min_year = _CURATE_PRESET_MIN_YEARS[curated_corpus_request.preset]
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

    if curated_corpus_request.output is not None and not getattr(args, "corpus", None):
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
        source_closure_summary = None
        if getattr(args, "source_closure_stats", False):
            source_closure_summary = _corpus_source_closure_summary(
                corpus,
                archive=archive,
                applicability_mode=str(
                    getattr(args, "uk_applicability_mode", None)
                    or "effective_date_plus_feed_applied"
                ),
            )
        _print_corpus_stats(
            corpus,
            source_closure_summary=source_closure_summary,
        )
        archive.close()
        return

    if curated_corpus_request.output is not None:
        curate_require_source_closure = bool(
            getattr(args, "curate_require_source_closure", False)
        )
        source_closure_summary = None
        if curate_require_source_closure:
            source_closure_summary = _corpus_source_closure_summary(
                corpus,
                archive=archive,
                applicability_mode=str(
                    getattr(args, "uk_applicability_mode", None)
                    or "effective_date_plus_feed_applied"
                ),
            )
            before_count = len(corpus)
            corpus = _filter_replay_source_closed_entries(
                corpus,
                source_closure_summary=source_closure_summary,
            )
            print(
                "  Replay source-closed filter: "
                f"{before_count} -> {len(corpus)} statutes"
            )
        hard_curate = curated_corpus_request.preset in _CURATE_HARD_PRESETS
        selected = _write_curated_corpus(
            corpus,
            output=curated_corpus_request.output,
            size=curated_corpus_request.size,
            hard=hard_curate,
        )
        if curated_corpus_request.preset:
            print(f"  Curated preset: {curated_corpus_request.preset}")
        if hard_curate:
            print(f"  Curated source-complete hard corpus: {curated_corpus_request.output}")
        else:
            print(f"  Curated source-complete corpus: {curated_corpus_request.output}")
        if curate_require_source_closure:
            print("  Required replay source closure: full/not_required")
        print(f"  Requested rows: {curated_corpus_request.size}")
        print(f"  Written rows: {len(selected)}")
        selected_source_closure_summary = None
        if curate_require_source_closure or getattr(args, "source_closure_stats", False):
            selected_source_closure_summary = _corpus_source_closure_summary(
                selected,
                archive=archive,
                applicability_mode=str(
                    getattr(args, "uk_applicability_mode", None)
                    or "effective_date_plus_feed_applied"
                ),
            )
        _print_corpus_stats(
            selected,
            source_closure_summary=selected_source_closure_summary,
        )
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
    score_text = not getattr(args, "no_text_scores", False)
    if not score_text:
        print("Text similarity scoring disabled (--no-text-scores); EID scores and replay diagnostics still run.")

    # Parallelism: None means --parallel was not passed → use a memory-safe
    # default. Pass --parallel explicitly to trade RAM for throughput.
    workers = _par if _par is not None else _default_uk_bench_workers(do_replay=do_replay)
    do_save = not getattr(args, "no_save", False)

    csv_file = None
    csv_writer = None
    witness_file = None
    witness_writer = None
    diag_file = None

    score_witness_count = 0
    diagnostic_count = 0

    headers = _get_csv_headers(
        has_commencement=do_commencement,
        has_replay=do_replay,
        has_text=score_text,
        has_commencement_error=do_commencement,
    )
    acc = _BenchRunAccumulator(
        has_commencement=do_commencement,
        replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
        replay_adjudication_sample_limit=replay_adjudication_sample_limit,
    )

    if do_save:
        _BENCH_DIR.mkdir(parents=True, exist_ok=True)
        csv_path = _BENCH_DIR / f"{label}.csv"
        witness_path = _score_witness_path(label)
        diag_path = _bench_diagnostics_path(label)

        csv_file = open(csv_path, "w", newline="")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(headers)

        witness_file = open(witness_path, "w", newline="")
        witness_writer = csv.DictWriter(witness_file, fieldnames=_SCORE_WITNESS_HEADERS)
        witness_writer.writeheader()

        diag_file = open(diag_path, "w", encoding="utf-8")

    worker_recycling = (
        f", worker_max_tasks={worker_max_tasks}" if worker_max_tasks is not None else ""
    )
    progress_total = acc.total_count + len(corpus)
    print(f"Scoring {len(corpus)} statutes (workers={workers}{worker_recycling})...")
    if do_replay and workers > 1:
        _, replay_heavy_entries = _partition_uk_replay_heavy_entries(corpus)
        if replay_heavy_entries:
            examples = ", ".join(
                str(entry.get("statute_id") or "") for entry in replay_heavy_entries[:5]
            )
            if len(replay_heavy_entries) > 5:
                examples += f", ... {len(replay_heavy_entries) - 5} more"
            print(
                "Replay memory guard: "
                f"{len(replay_heavy_entries)} heavy row(s) will run in a "
                f"single recycled worker lane ({examples})"
            )
    run_kwargs: dict[str, object] = {
        "do_replay": do_replay,
        "repo_root": _REPO_ROOT,
        "workers": workers,
        "do_commencement": do_commencement,
        "allow_metadata_backfill": replay_regime.allow_metadata_backfill,
        "allow_oracle_alignment": replay_regime.allow_oracle_alignment,
        "applicability_mode": replay_regime.applicability_mode,
        "authority_mode": replay_regime.authority_mode,
        "allow_metadata_only_effects": replay_regime.allow_metadata_only_effects,
        "score_text": score_text,
        "record_replay_subphases": getattr(args, "phase_timings", False),
    }
    if worker_max_tasks is not None:
        run_kwargs["worker_max_tasks_per_child"] = worker_max_tasks
    if workers == 1:
        def _print_row_start(index: int, total: int, entry: Mapping[str, object]) -> None:
            print(
                _format_uk_bench_row_start(
                    index=index,
                    total=total,
                    entry=entry,
                    do_replay=do_replay,
                ),
                file=sys.stderr,
                flush=True,
            )

        run_kwargs["progress_start"] = _print_row_start

    try:
        for r in _run_bench(corpus, archive, **run_kwargs):
            acc.feed(r)

            done = acc.total_count

            primary_score = _bench_primary_score(r, has_commencement=do_commencement)
            replay_fragment = ""
            if do_replay and r.status == "OK":
                rep_score = _bench_primary_replay_score(r, has_commencement=do_commencement)
                if rep_score >= 0.0:
                    replay_fragment = f" replay={rep_score:.1%}"

            print(
                f"  [{done}/{progress_total}] {r.statute_id:<30} "
                f"score={primary_score:.1%}{replay_fragment} "
                f"({r.duration_s:.2f}s) status={r.status}",
                file=sys.stderr,
            )

            if do_save:
                csv_row = _get_csv_row(
                    r,
                    has_commencement=do_commencement,
                    has_replay=do_replay,
                    has_text=score_text,
                    has_commencement_error=do_commencement,
                )
                csv_writer.writerow(csv_row)

                w_rows = _get_score_witness_dict_rows(r, label)
                for w_row in w_rows:
                    witness_writer.writerow(w_row)
                    score_witness_count += 1

                diag_rows = _bench_diagnostic_rows_for_result(r, label)
                for diag_row in diag_rows:
                    diag_file.write(json.dumps(diag_row, ensure_ascii=False, sort_keys=True) + "\n")
                    diagnostic_count += 1

            del r
    finally:
        archive.close()

        if csv_file:
            csv_file.close()
        if witness_file:
            witness_file.close()
        if diag_file:
            diag_file.close()

    if do_save:
        if score_witness_count == 0:
            witness_path = _score_witness_path(label)
            if witness_path.exists():
                witness_path.unlink()
        else:
            print(f"Score witnesses saved: {_score_witness_path(label)} rows={score_witness_count}")

        if diagnostic_count == 0:
            diag_path = _bench_diagnostics_path(label)
            if diag_path.exists():
                diag_path.unlink()
        else:
            print(f"Bench diagnostics saved: {_bench_diagnostics_path(label)} rows={diagnostic_count}")

        print(f"Results saved: {_BENCH_DIR / f'{label}.csv'}")

        _append_history(acc, label, score_witness_count)

    if replay_adjudication_sample_kinds:
        _print_report(
            acc,
            label,
            replay_adjudication_sample_kinds=replay_adjudication_sample_kinds,
            replay_adjudication_sample_limit=replay_adjudication_sample_limit,
            summary_only=getattr(args, "summary_only", False),
        )
    else:
        _print_report(acc, label, summary_only=getattr(args, "summary_only", False))

    if getattr(args, "phase_timings", False) and not getattr(args, "summary_only", False):
        _print_phase_timing_rows(acc)
