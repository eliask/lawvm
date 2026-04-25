"""lawvm bench-curate — partition Finland benchmark corpus by oracle comparability.

Buckets:
  core      — usable oracle truth and no known version-frontier mismatch
  suspect   — oracle exists, but version commensurability is suspect
  notruth   — no commensurable oracle truth
  pending   — operationally unresolved (e.g. API throttling during validation)

The main benchmark should measure only `core`. Other buckets stay as audit
artifacts rather than polluting the score with incomparable statutes.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from lawvm.finland.corpus import (
    get_consolidated_oracle_suspect_cache_only,
    get_ground_truth,
)


_DEFAULT_SUSPECT_MODE = "cache-only"


def _lawvm_dir() -> Path:
    here = Path(__file__).resolve()
    return here.parent.parent.parent.parent


def _default_input_corpus() -> Path:
    return _lawvm_dir() / "data" / "finland" / "bench_corpus.csv"


def _default_output_dir() -> Path:
    return _lawvm_dir() / "data" / "finland"


def _load_corpus(path: Path) -> List[Tuple[int, str]]:
    rows: List[Tuple[int, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) < 2:
                continue
            try:
                rows.append((int(row[0]), row[1].strip()))
            except ValueError:
                continue
    return rows


def _resolve_run_path(label_or_path: str) -> Path:
    p = Path(label_or_path)
    if p.exists():
        return p
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    matches = sorted(runs_dir.glob(f"*_{label_or_path}.csv"))
    if not matches:
        raise FileNotFoundError(f"bench run not found for label/path: {label_or_path}")
    return matches[-1]


def _resolve_strict_run_path(label_or_path: str) -> Path:
    p = Path(label_or_path)
    if p.exists():
        return p
    runs_dir = _lawvm_dir() / "data" / "strict_runs"
    matches = sorted(runs_dir.glob(f"*_{label_or_path}.csv"))
    if not matches:
        raise FileNotFoundError(f"strict run not found for label/path: {label_or_path}")
    return matches[-1]


def _load_run_statuses(path: Path) -> Dict[str, str]:
    statuses: Dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid = (row.get("statute_id") or "").strip()
            if not sid:
                continue
            status = (row.get("status") or "").strip()
            sim = (row.get("similarity") or "").strip()
            if sim == "ERR" and status == "OK":
                status = "ERR"
            statuses[sid] = status
    return statuses


def _normalize_run_args(run_arg) -> List[str]:
    if not run_arg:
        return []
    if isinstance(run_arg, str):
        return [run_arg]
    return [str(v) for v in run_arg if str(v).strip()]


def _parse_string_listish(raw: str) -> List[str]:
    raw = (raw or "").strip()
    if not raw:
        return []
    return [part for part in raw.split("|") if part]


def _parse_source_pathology_rows_json(raw: str) -> List[Dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]


def _format_source_pathology_detail(
    codes: List[str],
    rows: List[Dict[str, Any]],
) -> str:
    if codes:
        return "|".join(str(code) for code in codes if str(code))
    parts: List[str] = []
    for row in rows:
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        label = str(row.get("target_label") or "").strip()
        detail = row.get("detail")
        diagnostic_reason = (
            str(detail.get("diagnostic_reason") or "").strip()
            if isinstance(detail, dict)
            else ""
        )
        part = code
        if label:
            part += f"@{label}"
        if diagnostic_reason:
            part += f"#{diagnostic_reason}"
        parts.append(part)
    return "|".join(parts)


def _load_strict_signals(path: Path) -> Dict[str, Dict[str, Any]]:
    signals: Dict[str, Dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sid = (row.get("statute_id") or "").strip()
            if not sid:
                continue
            source_pathology_codes = _parse_string_listish(row.get("source_pathology_codes", ""))
            source_pathology_rows = _parse_source_pathology_rows_json(
                str(row.get("source_pathology_rows_json", "") or "")
            )
            fail_reasons = set(_parse_string_listish(row.get("fail_reasons", "")))
            signals[sid] = {
                "source_pathology": (
                    bool(source_pathology_codes)
                    or bool(source_pathology_rows)
                    or "APPLY.SOURCE_PATHOLOGY_DETECTED" in fail_reasons
                ),
                "source_pathology_codes": source_pathology_codes,
                "source_pathology_rows": source_pathology_rows,
                "source_pathology_detail": _format_source_pathology_detail(
                    source_pathology_codes,
                    source_pathology_rows,
                ),
                "contingent_effective_date": (
                    bool(_parse_string_listish(row.get("contingent_effective_sources", "")))
                    or "TIME.CONTINGENT_EFFECTIVE_DATE" in fail_reasons
                ),
                "contingent_effective_sources": _parse_string_listish(
                    row.get("contingent_effective_sources", "")
                ),
            }
    return signals


def _write_corpus(path: Path, rows: Iterable[Tuple[int, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for count, sid in rows:
            w.writerow([count, sid])


def main(args) -> None:
    corpus_path = Path(getattr(args, "corpus", "") or _default_input_corpus())
    output_dir = Path(getattr(args, "output_dir", "") or _default_output_dir())
    run_args = _normalize_run_args(getattr(args, "run", None))
    strict_run_args = _normalize_run_args(getattr(args, "strict_run", None))
    suspect_mode = getattr(args, "oracle_suspect_check", _DEFAULT_SUSPECT_MODE) or _DEFAULT_SUSPECT_MODE

    corpus = _load_corpus(corpus_path)
    if not corpus:
        raise SystemExit(f"ERROR: empty or unreadable corpus: {corpus_path}")

    run_statuses: Dict[str, str] = {}
    run_paths: List[Path] = []
    strict_signals: Dict[str, Dict[str, Any]] = {}
    strict_run_paths: List[Path] = []
    for run_arg in run_args:
        run_path = _resolve_run_path(run_arg)
        run_paths.append(run_path)
        run_statuses.update(_load_run_statuses(run_path))
    for strict_run_arg in strict_run_args:
        strict_run_path = _resolve_strict_run_path(strict_run_arg)
        strict_run_paths.append(strict_run_path)
        strict_signals.update(_load_strict_signals(strict_run_path))

    core: List[Tuple[int, str]] = []
    suspect: List[Tuple[int, str]] = []
    notruth: List[Tuple[int, str]] = []
    pending: List[Tuple[int, str]] = []
    audit_rows: List[Dict[str, str]] = []

    for count, sid in corpus:
        last_status = run_statuses.get(sid, "")
        bucket = "core"
        reason = ""
        detail = ""

        if last_status == "NO_TRUTH":
            bucket = "notruth"
            reason = "no_truth_from_run"
        elif last_status and last_status != "OK":
            bucket = "pending"
            reason = "operational_from_run"
            detail = last_status
        else:
            strict_signal = strict_signals.get(sid, {})
            if strict_signal.get("source_pathology"):
                bucket = "suspect"
                reason = "source_pathology"
                detail = str(strict_signal.get("source_pathology_detail", "") or "")
            elif strict_signal.get("contingent_effective_date"):
                bucket = "suspect"
                reason = "contingent_effective_date"
                detail = "|".join(
                    str(source)
                    for source in strict_signal.get("contingent_effective_sources", [])
                    if str(source)
                )
            elif suspect_mode == "cache-only":
                suspect_detail, pending_detail = get_consolidated_oracle_suspect_cache_only(sid)
                if suspect_detail:
                    bucket = "suspect"
                    reason = "oracle_suspect"
                    detail = suspect_detail
                elif pending_detail:
                    bucket = "pending"
                    reason = "oracle_version_check_pending"
                    detail = pending_detail
            elif not run_args:
                truth = get_ground_truth(sid)
                if not truth.strip():
                    bucket = "notruth"
                    reason = "no_truth_detected"

        if bucket == "core":
            core.append((count, sid))
        elif bucket == "suspect":
            suspect.append((count, sid))
        elif bucket == "notruth":
            notruth.append((count, sid))
        elif bucket == "pending":
            pending.append((count, sid))
        else:
            raise AssertionError(bucket)

        audit_rows.append(
            {
                "amendments": str(count),
                "statute_id": sid,
                "bucket": bucket,
                "reason": reason,
                "detail": detail,
                "last_status": last_status,
            }
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    core_path = output_dir / "bench_core.csv"
    suspect_path = output_dir / "bench_suspect.csv"
    notruth_path = output_dir / "bench_notruth.csv"
    pending_path = output_dir / "bench_pending.csv"
    audit_path = output_dir / "bench_partition_audit.csv"

    _write_corpus(core_path, core)
    _write_corpus(suspect_path, suspect)
    _write_corpus(notruth_path, notruth)
    _write_corpus(pending_path, pending)

    with audit_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["amendments", "statute_id", "bucket", "reason", "detail", "last_status"],
        )
        w.writeheader()
        w.writerows(audit_rows)

    print(f"Input corpus : {corpus_path}")
    for i, run_path in enumerate(run_paths, start=1):
        print(f"Bench run {i}  : {run_path}")
    for i, strict_run_path in enumerate(strict_run_paths, start=1):
        print(f"Strict run {i} : {strict_run_path}")
    print(f"Suspect mode : {suspect_mode}")
    print(f"Core         : {len(core)} -> {core_path}")
    print(f"Suspect      : {len(suspect)} -> {suspect_path}")
    print(f"No truth     : {len(notruth)} -> {notruth_path}")
    print(f"Pending      : {len(pending)} -> {pending_path}")
    print(f"Audit        : {audit_path}")
