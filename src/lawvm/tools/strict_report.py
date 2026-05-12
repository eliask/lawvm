"""lawvm strict-report — strict-path compilation report.

Two modes:

Single-statute (existing behaviour):
    lawvm strict-report 2009/953
    lawvm strict-report 2009/953 --verbose
    lawvm strict-report 2009/953 --json
    lawvm strict-report 2009/953 --facade

Corpus-wide (new):
    lawvm strict-report --parallel 4 --label strict_v1
    lawvm strict-report --show strict_v1

Corpus results are saved to LawVM/data/strict_runs/<timestamp>_<label>.csv.
"""
from __future__ import annotations

import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from lawvm.core.ir import LegalOperation
from lawvm.core.compile_result import (
    strict_fail_reasons_from_findings_and_verdict,
)
from lawvm.core.compile_views import (
    quirks_used_from_findings,
    source_completeness_issues_from_findings,
    projection_rows_from_findings,
    source_pathology_rows_from_findings,
)
from lawvm.finland.source_adjudication import build_source_adjudication
from lawvm.finland.ops import FailedOp
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.tools._compile_report_record import report_record_from_facade


# ---------------------------------------------------------------------------
# Single-statute formatting (original)
# ---------------------------------------------------------------------------

def _compiled_op_display_tag(op_dict: dict[str, Any], *, is_recovered: bool) -> str:
    """Display typed compiled-op provenance, else fall back to op class only."""
    typed_tags: list[str] = []
    extraction_tags = op_dict.get("extraction_provenance_tags")
    if isinstance(extraction_tags, list):
        typed_tags.extend(str(tag).strip() for tag in extraction_tags if str(tag).strip())
    target_guessing_tags = op_dict.get("target_guessing_provenance_tags")
    if isinstance(target_guessing_tags, list):
        typed_tags.extend(str(tag).strip() for tag in target_guessing_tags if str(tag).strip())
    scope_tags = op_dict.get("scope_provenance_tags")
    if isinstance(scope_tags, list):
        typed_tags.extend(str(tag).strip() for tag in scope_tags if str(tag).strip())
    if op_dict.get("voimaantulo_repeal"):
        typed_tags.append("voimaantulo_repeal")
    typed_tags = list(dict.fromkeys(typed_tags))
    if typed_tags:
        return ",".join(typed_tags)
    return "recovered" if is_recovered else "canonical"


def _projection_detail_suffix(detail: dict[str, Any]) -> str:
    """Render high-signal projection-row detail compactly for human reports."""
    parts: list[str] = []
    code = str(detail.get("code") or "").strip()
    if code:
        parts.append(f"code={code}")
    target_unit_kind = str(detail.get("target_unit_kind") or "").strip()
    target_norm = str(detail.get("target_norm") or "").strip()
    target_chapter = str(detail.get("target_chapter") or "").strip()
    if target_unit_kind or target_norm or target_chapter:
        target_parts: list[str] = []
        if target_unit_kind:
            target_parts.append(f"kind={target_unit_kind}")
        if target_norm:
            target_parts.append(f"norm={target_norm}")
        if target_chapter:
            target_parts.append(f"chapter={target_chapter}")
        parts.append("target(" + ", ".join(target_parts) + ")")
    target_label = str(detail.get("target_label") or "").strip()
    if target_label:
        parts.append(f"target_label={target_label}")
    diagnostic_reason = str(detail.get("diagnostic_reason") or "").strip()
    if diagnostic_reason:
        parts.append(f"diagnostic_reason={diagnostic_reason}")
    tag = str(detail.get("tag") or "").strip()
    if tag:
        parts.append(f"tag={tag}")
    if not parts:
        return ""
    return "  detail: " + "; ".join(parts)


def _field(record: Any, name: str, default: Any = None) -> Any:
    """Read *name* from either a dict-backed projection or an object."""
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _failed_op_to_jsonable(failed_op: Any) -> dict[str, Any]:
    """Serialize a failed operation without losing its stable rule/scope fields."""
    as_detail = _field(failed_op, "as_detail", None)
    if callable(as_detail):
        detail = dict(as_detail())
    else:
        detail = {
            "amendment_id": _field(failed_op, "amendment_id", ""),
            "description": _field(failed_op, "description", ""),
            "reason": _field(failed_op, "reason", ""),
            "reason_code": _field(failed_op, "reason_code", ""),
            "target_unit_kind": _field(failed_op, "target_unit_kind", ""),
            "target_section": _field(failed_op, "target_section", ""),
            "target_chapter": _field(failed_op, "target_chapter", None),
            "target_part": _field(failed_op, "target_part", None),
        }
    source = _field(failed_op, "source_statute", "") or detail.get("amendment_id", "")
    detail["source"] = source
    detail["target_kind"] = _field(failed_op, "target_kind", "") or _field(
        failed_op,
        "compat_target_kind_code",
        "",
    )
    return detail


def _strict_from_record(record: Any) -> bool:
    """Derive strictness locally from a summary row or presenter object."""
    if _field(record, "error", ""):
        return False
    strict_fail_reasons = _field(record, "strict_fail_reasons", None)
    if strict_fail_reasons is None:
        strict_fail_reasons = _field(record, "fail_reasons", ())
    return not bool(strict_fail_reasons)


def _projection_rows(record: Any) -> list[Any]:
    if isinstance(record, dict):
        rows = record.get("projection_rows", ()) or ()
        return list(rows)
    findings = getattr(record, "finding_ledger", None)
    if findings is not None:
        return list(projection_rows_from_findings(findings))
    projection = getattr(record, "projection_rows", None)
    if callable(projection):
        return list(projection() or [])
    raise TypeError("record must expose finding_ledger or projection_rows()")


def _source_pathologies(record: Any) -> list[Any]:
    if isinstance(record, dict):
        return list(record.get("source_pathologies", []) or [])
    findings = getattr(record, "finding_ledger", None)
    if findings is not None:
        return list(source_pathology_rows_from_findings(findings))
    source_pathology_rows = getattr(record, "source_pathology_rows", None)
    if callable(source_pathology_rows):
        return list(source_pathology_rows() or [])
    return [
        {
            "code": str((row.get("detail") or {}).get("code") or ""),
            "message": str(row.get("message") or ""),
            "source_statute": str(row.get("source") or ""),
            "target_unit_kind": str((row.get("detail") or {}).get("target_unit_kind") or ""),
            "target_label": str((row.get("detail") or {}).get("target_label") or ""),
            "detail": (
                dict((row.get("detail") or {}).get("detail") or {})
                if isinstance((row.get("detail") or {}).get("detail"), dict)
                else {
                    key: value
                    for key, value in dict(row.get("detail") or {}).items()
                    if key not in {"code", "target_unit_kind", "target_label", "message"}
                }
            ),
        }
        for row in _projection_rows(record)
        if isinstance(row, dict)
        and str(row.get("kind") or "") in {
            "ELAB.SOURCE_PATHOLOGY",
            "ELAB.STRICT_REJECTED_SOURCE_PATHOLOGY",
            "APPLY.SOURCE_PATHOLOGY_DETECTED",
        }
        and isinstance(row.get("detail"), dict)
        and str((row.get("detail") or {}).get("code") or "")
    ]


def _source_completeness_counts(record: Any) -> tuple[int, int, int]:
    """Derive source-completeness counts from the surviving lineage owner."""
    source_adjudication = _field(record, "source_adjudication", None)
    if source_adjudication is not None:
        lineage = list(getattr(source_adjudication, "lineage", ()) or ())
        return (
            len(lineage),
            sum(1 for row in lineage if isinstance(row, dict) and row.get("included")),
            sum(1 for row in lineage if isinstance(row, dict) and row.get("effective_date")),
        )
    if not isinstance(record, dict):
        source_completeness = getattr(record, "source_completeness", None)
        if source_completeness is not None:
            return (
                int(getattr(source_completeness, "chain_length", 0) or 0),
                int(getattr(source_completeness, "source_available", 0) or 0),
                int(getattr(source_completeness, "dates_available", 0) or 0),
            )
    return (0, 0, 0)


def _effective_source_adjudication(
    *,
    statute_id: str,
    replay_mode: str,
    replay_result: object | None,
    replay_meta: dict[str, object],
) -> SourceAdjudication | None:
    typed = getattr(replay_result, "source_adjudication", None)
    if typed is not None:
        return cast(SourceAdjudication, typed)

    raw_lineage = replay_meta.get("lineage")
    lineage: tuple[dict[str, Any], ...] = ()
    if isinstance(raw_lineage, (list, tuple)):
        lineage = cast(
            tuple[dict[str, Any], ...],
            tuple(row for row in raw_lineage if isinstance(row, dict)),
        )
    cutoff_date = str(replay_meta.get("cutoff_date") or "")
    oracle_version_amendment_id = str(replay_meta.get("oracle_version_amendment_id") or "")
    oracle_suspect = str(replay_meta.get("oracle_suspect") or "")
    html_noncommensurable_reason = str(replay_meta.get("html_noncommensurable_reason") or "")
    if not any(
        (
            cutoff_date,
            oracle_version_amendment_id,
            oracle_suspect,
            html_noncommensurable_reason,
            lineage,
        )
    ):
        return None
    return build_source_adjudication(
        statute_id=statute_id,
        replay_mode=replay_mode,
        cutoff_date=cutoff_date,
        oracle_version_amendment_id=oracle_version_amendment_id,
        oracle_suspect=oracle_suspect,
        html_noncommensurable_reason=html_noncommensurable_reason,
        lineage=lineage,
    )


def _format_report(cr: Any, *, verbose: bool = False) -> str:
    """Format a strict-report dossier view as human-readable text."""
    lines: list[str] = []

    compiled_ops = list(_field(cr, "compiled_ops", []) or [])
    canonical_ops = list(_field(cr, "canonical_ops", []) or [])
    failed_ops = list(_field(cr, "failed_ops", []) or [])
    projection_rows = _projection_rows(cr)
    strict_fail_reasons = list(_field(cr, "strict_fail_reasons", []) or [])
    source_pathologies = _source_pathologies(cr)
    sc_chain_length, sc_source_available, sc_dates_available = _source_completeness_counts(cr)

    n_canonical = len(canonical_ops)
    n_failed = len(failed_ops)
    n_total = n_canonical + n_failed
    strict_frac = f"{n_canonical}/{n_total} ({100*n_canonical/n_total:.1f}%)" if n_total else "0/0"

    profile = _field(cr, "profile", None)
    profile_name = str(getattr(profile, "name", profile or "") or "")
    strict_value = not bool(strict_fail_reasons)
    lines.append(f"Statute     : {_field(cr, 'statute_id', '')}")
    lines.append(f"Profile     : {profile_name}")
    lines.append(f"Strict      : {'YES' if bool(strict_value) else 'NO'}")
    lines.append("")

    # Source completeness
    lines.append("Source completeness")
    if sc_chain_length:
        lines.append(f"  chain_length     : {sc_chain_length}")
        lines.append(f"  source_available : {sc_source_available}  ({100*sc_source_available/sc_chain_length:.0f}%)")
        lines.append(f"  dates_available  : {sc_dates_available}  ({100*sc_dates_available/sc_chain_length:.0f}%)")
    else:
        lines.append(f"  chain_length     : {sc_chain_length}")
    if source_pathologies:
        lines.append(
            "  pathologies      : "
            + ", ".join(str(_field(p, "code", "")) for p in source_pathologies)
        )
    lines.append("")

    # Ops summary
    lines.append("Ops summary")
    lines.append(f"  canonical        : {n_canonical}")
    lines.append(f"  failed           : {n_failed}")
    lines.append(f"  total            : {n_total}")
    lines.append(f"  strict fraction  : {strict_frac}")
    lines.append("")

    # Failed ops
    if failed_ops:
        lines.append("Failed ops")
        for f in failed_ops:
            detail = _failed_op_to_jsonable(f)
            source_statute = str(detail.get("source") or detail.get("amendment_id") or "")
            reason_code = str(detail.get("reason_code") or "").strip()
            reason_suffix = f" [{reason_code}]" if reason_code else ""
            target_unit_kind = str(detail.get("target_unit_kind") or detail.get("target_kind") or "")
            lines.append(
                f"  {source_statute:12s} "
                f"{str(detail.get('reason') or ''):30s}{reason_suffix} "
                f"{target_unit_kind} "
                f"{str(detail.get('target_section') or '')}"
            )
        lines.append("")

    # Strict fail reasons
    if strict_fail_reasons:
        lines.append("Strict fail reasons")
        for reason in strict_fail_reasons:
            lines.append(f"  {reason}")
        lines.append("")

    # Compatibility projection rows
    if projection_rows:
        lines.append("Projection rows")
        for adj in projection_rows:
            source_statute = str(_field(adj, "source_statute", "") or _field(adj, "source", ""))
            src = f"  source: {source_statute}" if source_statute else ""
            detail = _field(adj, "detail", {})
            detail_suffix = _projection_detail_suffix(detail) if isinstance(detail, dict) else ""
            lines.append(
                f"  [{_field(adj, 'kind', '')}]  "
                f"{_field(adj, 'message', '')}{src}{detail_suffix}"
            )
        lines.append("")

    if source_pathologies:
        lines.append("Source pathologies")
        for pathology in source_pathologies:
            source_statute = str(_field(pathology, "source_statute", "") or "")
            target_label = str(_field(pathology, "target_label", "") or "")
            src = f"  source: {source_statute}" if source_statute else ""
            tgt = f"  target: {target_label}" if target_label else ""
            detail = _field(pathology, "detail", {}) or {}
            diag = str(detail.get("diagnostic_reason") or "") if isinstance(detail, dict) else ""
            diag_text = f"  diagnostic_reason: {diag}" if diag else ""
            lines.append(
                f"  [{_field(pathology, 'code', '')}]  "
                f"{_field(pathology, 'message', '')}{src}{tgt}{diag_text}"
            )
        lines.append("")

    # Verbose: per-op details
    if verbose and compiled_ops:
        lines.append("Compiled ops (all)")
        for i, op_dict in enumerate(compiled_ops, 1):
            op_id = op_dict.get("op_id", "?")
            desc = op_dict.get("description", "")
            tag = _compiled_op_display_tag(op_dict, is_recovered=False)
            lines.append(f"  [{i:3d}] {op_id:30s} {desc:40s} {tag}")
        lines.append("")

    return "\n".join(lines)


def _to_json(cr: Any) -> dict[str, Any]:
    """Serialize a strict-report dossier view to a JSON-safe dict."""
    canonical_ops = list(_field(cr, "canonical_ops", []) or [])
    failed_ops = list(_field(cr, "failed_ops", []) or [])
    projection_rows = _projection_rows(cr)
    strict_fail_reasons = list(_field(cr, "strict_fail_reasons", []) or [])
    source_pathologies = _source_pathologies(cr)
    sc_chain_length, sc_source_available, sc_dates_available = _source_completeness_counts(cr)
    n_canonical = len(canonical_ops)
    profile = _field(cr, "profile", None)
    profile_name = str(getattr(profile, "name", profile or "") or "")
    return {
        "statute_id": _field(cr, "statute_id", ""),
        "profile": profile_name,
        "ops": {
            "canonical": n_canonical,
            "failed": len(failed_ops),
            "total": n_canonical + len(failed_ops),
        },
        "source_completeness": {
            "chain_length": sc_chain_length,
            "source_available": sc_source_available,
            "dates_available": sc_dates_available,
        },
        "source_pathologies": [
            {
                "code": _field(p, "code", ""),
                "message": _field(p, "message", ""),
                "source_statute": _field(p, "source_statute", ""),
                "target_unit_kind": _field(p, "target_unit_kind", ""),
                "target_label": _field(p, "target_label", ""),
                "detail": dict(_field(p, "detail", {}) or {}),
            }
            for p in source_pathologies
        ],
        "strict_fail_reasons": strict_fail_reasons,
        "projection_rows": [
            {
                "kind": _field(a, "kind", ""),
                "message": _field(a, "message", ""),
                "source": _field(a, "source_statute", "") or _field(a, "source", ""),
                "detail": dict(_field(a, "detail", {}) or {}),
            }
            for a in projection_rows
        ],
        "failed_ops": [_failed_op_to_jsonable(f) for f in failed_ops],
    }


# ---------------------------------------------------------------------------
# Corpus-wide helpers
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    """LawVM/data/ — sibling of src/."""
    here = Path(__file__).resolve()
    # src/lawvm/tools/strict_report.py → src/lawvm/tools → src/lawvm → src → LawVM
    return here.parent.parent.parent.parent / "data"


def _strict_runs_dir() -> Path:
    d = _data_dir() / "strict_runs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _default_corpus_path() -> str:
    here = Path(__file__).resolve()
    lawvm_dir = here.parent.parent.parent.parent
    primary = lawvm_dir / "data" / "finland" / "bench_corpus.csv"
    if primary.exists():
        return str(primary)
    return str(lawvm_dir / ".tmp" / "batch_test_list.csv")


def _load_corpus(corpus_path: str) -> list[tuple[int, str]]:
    """Load corpus CSV. Format: N,YEAR/NUM."""
    with open(corpus_path, newline="") as f:
        rows = list(csv.reader(f))
    result = []
    for row in rows:
        if len(row) < 2:
            continue
        try:
            count = int(row[0])
            sid = row[1].strip()
        except (ValueError, IndexError):
            continue
        result.append((count, sid))
    return result


# CSV columns for strict run output
_STRICT_RUN_HEADER = [
    "statute_id",
    "n_canonical",
    "n_failed",
    "n_projection_rows",
    "n_source_pathologies",
    "n_contingent_effective_dates",
    "projection_kinds",
    "source_pathology_codes",
    "source_pathology_rows_json",
    "source_pathology_diagnostic_reasons",
    "html_noncommensurable_reason",
    "contingent_effective_sources",
    "fail_reasons",
    "source_incomplete",
    "chain_length",
    "source_available",
    "elapsed_s",
    "error",
]


def _compile_one(args: tuple[int, str]) -> dict[str, Any]:
    """Compile one statute and extract strict metrics. Designed for ProcessPoolExecutor."""
    _count, sid = args
    t0 = time.time()
    try:
        from lawvm.finland.compile import (
            compile_fi_facade_from_replay,
        )
        from lawvm.finland.grafter import replay_xml

        compiled_ops: list[dict[str, object]] = []
        replay_meta: dict[str, object] = {}
        canonical_ops: list[LegalOperation] = []
        failed_ops: list[FailedOp] = []
        master = replay_xml(
            sid,
            quiet=True,
            compiled_ops_out=compiled_ops,
            replay_meta_out=replay_meta,
            lo_ops_out=canonical_ops,
            failed_ops_out=failed_ops,
        )
        facade = compile_fi_facade_from_replay(
            parent_id=sid,
            replay_result=master,
            replay_mode="finlex_oracle",
            compiled_ops=compiled_ops,
            replay_meta=replay_meta,
            canonical_ops=canonical_ops,
            failed_ops=failed_ops,
        )
        elapsed = time.time() - t0
        source_adjudication = _effective_source_adjudication(
            statute_id=sid,
            replay_mode="finlex_oracle",
            replay_result=master,
            replay_meta=replay_meta,
        )
        lineage = (
            list(cast(Any, source_adjudication.lineage) or [])
            if source_adjudication is not None
            else []
        )
        source_pathologies = [dict(item) for item in source_pathology_rows_from_findings(facade.finding_ledger)]
        projection_rows = list(projection_rows_from_findings(facade.finding_ledger))
        contingent_sources = sorted(
            {
                str(row.get("source") or "").strip()
                for row in projection_rows
                if (
                    str(row.get("kind") or "") == "TIME.CONTINGENT_EFFECTIVE_DATE"
                    and str(row.get("source") or "").strip()
                )
            }
        )
        source_pathology_diagnostic_reasons = sorted(
            {
                str((p.get("detail") or {}).get("diagnostic_reason") or "")
                for p in source_pathologies
                if isinstance(p, dict) and str((p.get("detail") or {}).get("diagnostic_reason") or "")
            }
        )
        fail_reasons = list(
            strict_fail_reasons_from_findings_and_verdict(
                facade.finding_ledger,
                verdict=getattr(facade, "verdict", None),
            )
        )
        return {
            "sid": sid,
            "n_canonical": len(facade.bundle.structural_ops),
            "n_failed": len(failed_ops),
            "n_projection_rows": len(projection_rows),
            "n_source_pathologies": len(source_pathologies),
            "n_contingent_effective_dates": len(contingent_sources),
            "projection_kinds": sorted(
                {str(row.get("kind") or "") for row in projection_rows if str(row.get("kind") or "")}
            ),
            "source_pathology_codes": sorted(
                {str(p.get("code") or "") for p in source_pathologies if isinstance(p, dict) and str(p.get("code") or "")}
            ),
            "source_pathology_rows": source_pathologies,
            "source_pathology_diagnostic_reasons": source_pathology_diagnostic_reasons,
            "html_noncommensurable_reason": (
                str(source_adjudication.html_noncommensurable_reason or "")
                if source_adjudication is not None
                else ""
            ),
            "contingent_effective_sources": contingent_sources,
            "fail_reasons": fail_reasons,
            "source_incomplete": "APPLY.SOURCE_INCOMPLETE" in fail_reasons,
            "chain_length": len(lineage),
            "source_available": sum(1 for row in lineage if row.get("included")),
            "elapsed_s": elapsed,
            "error": "",
        }
    except Exception as exc:
        elapsed = time.time() - t0
        return {
            "sid": sid,
            "n_canonical": 0,
            "n_failed": 0,
            "n_projection_rows": 0,
            "n_source_pathologies": 0,
            "n_contingent_effective_dates": 0,
            "projection_kinds": [],
            "source_pathology_codes": [],
            "source_pathology_rows": [],
            "source_pathology_diagnostic_reasons": [],
            "html_noncommensurable_reason": "",
            "contingent_effective_sources": [],
            "fail_reasons": [],
            "source_incomplete": False,
            "chain_length": 0,
            "source_available": 0,
            "elapsed_s": elapsed,
            "error": str(exc)[:200],
        }


def _run_corpus(
    corpus: list[tuple[int, str]],
    workers: int = 1,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Run compile_one over corpus, optionally in parallel."""
    total = len(corpus)

    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor, as_completed

        results: list[dict[str, Any]] = [{}] * total
        with ProcessPoolExecutor(max_workers=workers) as pool:
            future_to_idx = {
                pool.submit(_compile_one, item): i
                for i, item in enumerate(corpus)
            }
            done = 0
            for future in as_completed(future_to_idx):
                idx = future_to_idx[future]
                rec = future.result()
                results[idx] = rec
                done += 1
                if verbose:
                    sp = "PASS" if _strict_from_record(rec) else "FAIL"
                    err = f"  ERR: {rec['error']}" if rec["error"] else ""
                    print(
                        f"[{done}/{total}] {rec['sid']:12s}  strict={sp}"
                        f"  can={rec['n_canonical']:3d}"
                        f" fail={rec['n_failed']:3d}  ({rec['elapsed_s']:.1f}s){err}",
                        flush=True,
                    )
        return results

    results = []
    for i, item in enumerate(corpus, start=1):
        rec = _compile_one(item)
        results.append(rec)
        if verbose:
            sp = "PASS" if _strict_from_record(rec) else "FAIL"
            err = f"  ERR: {rec['error']}" if rec["error"] else ""
            print(
                f"[{i}/{total}] {rec['sid']:12s}  strict={sp}"
                f"  can={rec['n_canonical']:3d}"
                f" fail={rec['n_failed']:3d}  ({rec['elapsed_s']:.1f}s){err}",
                flush=True,
            )
    return results


def _save_strict_run(results: list[dict[str, Any]], label: str, timestamp: str) -> Path:
    fname = f"{timestamp.replace(':', '').replace('-', '')[:15]}_{label}.csv"
    path = _strict_runs_dir() / fname
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(_STRICT_RUN_HEADER)
        for rec in results:
            w.writerow([
                rec["sid"],
                rec["n_canonical"],
                rec["n_failed"],
                rec["n_projection_rows"],
                rec["n_source_pathologies"],
                rec["n_contingent_effective_dates"],
                "|".join(rec["projection_kinds"]),
                "|".join(rec["source_pathology_codes"]),
                json.dumps(rec.get("source_pathology_rows", []), ensure_ascii=True, sort_keys=True),
                "|".join(rec.get("source_pathology_diagnostic_reasons", [])),
                str(rec.get("html_noncommensurable_reason", "") or ""),
                "|".join(rec["contingent_effective_sources"]),
                "|".join(rec["fail_reasons"]),
                "1" if rec["source_incomplete"] else "0",
                rec["chain_length"],
                rec["source_available"],
                f"{rec['elapsed_s']:.2f}",
                rec["error"],
            ])
    return path


def _load_strict_run(label: str) -> list[dict[str, Any]] | None:
    runs_dir = _strict_runs_dir()
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]
    rows = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["source_incomplete"] = row["source_incomplete"] == "1"
            projection_kinds_raw = str(row.get("projection_kinds", "") or "")
            row["projection_kinds"] = [k for k in projection_kinds_raw.split("|") if k]
            row["source_pathology_codes"] = [k for k in row.get("source_pathology_codes", "").split("|") if k]
            source_pathology_rows_raw = str(row.get("source_pathology_rows_json", "") or "")
            source_pathology_rows: list[dict[str, Any]] = []
            if source_pathology_rows_raw:
                try:
                    loaded = json.loads(source_pathology_rows_raw)
                except json.JSONDecodeError:
                    loaded = None
                if isinstance(loaded, list):
                    source_pathology_rows = [
                        item for item in loaded if isinstance(item, dict)
                    ]
            row["source_pathology_rows"] = source_pathology_rows
            row["source_pathology_diagnostic_reasons"] = [
                k for k in row.get("source_pathology_diagnostic_reasons", "").split("|") if k
            ]
            row["html_noncommensurable_reason"] = str(
                row.get("html_noncommensurable_reason", "") or ""
            )
            row["contingent_effective_sources"] = [
                k for k in row.get("contingent_effective_sources", "").split("|") if k
            ]
            row["fail_reasons"] = [r for r in row["fail_reasons"].split("|") if r]
            for int_col in ("n_canonical", "n_failed", "n_projection_rows",
                            "n_source_pathologies", "n_contingent_effective_dates",
                            "chain_length", "source_available"):
                try:
                    if int_col == "n_projection_rows":
                        row[int_col] = int(row.get("n_projection_rows", 0) or 0)
                    else:
                        row[int_col] = int(row[int_col])
                except (ValueError, KeyError):
                    row[int_col] = 0
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Summary display
# ---------------------------------------------------------------------------

def _show_corpus_summary(results: list[dict[str, Any]], label: str) -> None:
    total = len(results)
    errors = [r for r in results if r.get("error")]
    valid = [r for r in results if not r.get("error")]
    n_valid = len(valid)

    if n_valid == 0:
        print(f"=== STRICT REPORT: {label} ===")
        print(f"  ERROR: all {total} statutes failed with exceptions")
        return

    n_strict = sum(1 for r in valid if _strict_from_record(r))
    n_source_incomplete = sum(1 for r in valid if r["source_incomplete"])

    # Quirks pass: compiled with at least some canonical ops
    # or no amendments to apply (chain_length == 0, trivially passes)
    n_quirks_pass = sum(
        1 for r in valid
        if r["n_canonical"] > 0 or r["chain_length"] == 0
    )

    # Per-projection-row kind frequency
    kind_counter: Counter[str] = Counter()
    for r in valid:
        for k in r["projection_kinds"]:
            kind_counter[k] += 1

    # Fail reason frequency
    fail_reason_counter: Counter[str] = Counter()
    for r in valid:
        for fr in r["fail_reasons"]:
            fail_reason_counter[fr] += 1

    # Source-pathology code frequency
    source_pathology_counter: Counter[str] = Counter()
    for r in valid:
        for code in r.get("source_pathology_codes", []):
            source_pathology_counter[code] += 1

    source_pathology_diagnostic_counter: Counter[str] = Counter()
    for r in valid:
        for reason in r.get("source_pathology_diagnostic_reasons", []):
            source_pathology_diagnostic_counter[reason] += 1

    html_noncomm_counter: Counter[str] = Counter()
    for r in valid:
        reason = str(r.get("html_noncommensurable_reason", "") or "")
        if reason:
            html_noncomm_counter[reason] += 1

    contingent_counter: Counter[str] = Counter()
    for r in valid:
        for sid in r.get("contingent_effective_sources", []):
            contingent_counter[sid] += 1

    # Strictness vs bench score correlation: bucket into two groups
    # and show mean canonical fraction for each
    strict_yes_canonical: list[float] = []
    strict_no_canonical: list[float] = []
    for r in valid:
        total_ops = r["n_canonical"] + r["n_failed"]
        if total_ops > 0:
            frac = r["n_canonical"] / total_ops
        else:
            frac = 1.0  # no-amendment statutes — trivially canonical
        if _strict_from_record(r):
            strict_yes_canonical.append(frac)
        else:
            strict_no_canonical.append(frac)

    def _mean(lst: list[float]) -> float:
        return sum(lst) / len(lst) if lst else 0.0

    print()
    print(f"=== STRICT REPORT SUMMARY  label={label} ===")
    print(f"  Statutes total   : {total}  (valid={n_valid}, errors={len(errors)})")
    print()
    print(f"  1. Strict rate        : {n_strict}/{n_valid}  "
          f"({100*n_strict/n_valid:.1f}%)")
    print(f"  2. Quirks pass rate   : {n_quirks_pass}/{n_valid}  "
          f"({100*n_quirks_pass/n_valid:.1f}%)")
    print(f"  3. Source incomplete  : {n_source_incomplete}/{n_valid}  "
          f"({100*n_source_incomplete/n_valid:.1f}%)")
    print()

    print("  4. Per-projection-row kind frequency:")
    if kind_counter:
        for kind, cnt in kind_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {kind:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none)")
    print()

    print("  5. Strict fail reasons:")
    if fail_reason_counter:
        for reason, cnt in fail_reason_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {reason:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none — all statutes strict-pass)")
    print()

    print("  5a. Source pathology codes:")
    if source_pathology_counter:
        for code, cnt in source_pathology_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {code:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none)")
    print()

    print("  5b. Source pathology diagnostic reasons:")
    if source_pathology_diagnostic_counter:
        for reason, cnt in source_pathology_diagnostic_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {reason:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none)")
    print()

    print("  5c. Contingent effective-date sources:")
    if contingent_counter:
        for sid, cnt in contingent_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {sid:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none)")
    print()

    print("  5d. HTML/XML noncommensurable reasons:")
    if html_noncomm_counter:
        for reason, cnt in html_noncomm_counter.most_common():
            pct = 100 * cnt / n_valid
            print(f"       {reason:<50s} {cnt:5d}  ({pct:.1f}%)")
    else:
        print("       (none)")
    print()

    print("  5e. Strict vs canonical fraction (correlation proxy):")
    print(f"       strict=YES        mean canonical fraction: {_mean(strict_yes_canonical):.3f}"
          f"  (N={len(strict_yes_canonical)})")
    print(f"       strict=NO         mean canonical fraction: {_mean(strict_no_canonical):.3f}"
          f"  (N={len(strict_no_canonical)})")
    print()

    if errors:
        print(f"  Errors ({len(errors)}):")
        for r in errors[:10]:
            print(f"    {r['sid']:12s}  {r['error'][:80]}")
        if len(errors) > 10:
            print(f"    ... and {len(errors)-10} more")
        print()


# ---------------------------------------------------------------------------
# CompileFacade helpers (single-statute mode)
# ---------------------------------------------------------------------------

def _build_facade_for_statute(
    statute_id: str,
    *,
    mode: Literal["finlex_oracle", "legal_pit"],
) -> "Any":
    """Build a CompileFacade for one statute via Finland's native facade API."""
    from lawvm.finland.compile import compile_fi_facade

    return compile_fi_facade(statute_id, replay_mode=mode)


def _print_facade_summary(
    facade: "Any",
    *,
    html_noncommensurable_reason: str = "",
) -> None:
    """Print a short CompileFacade summary block for the strict-report view."""
    has_blocking = getattr(facade, "has_blocking", None)
    if has_blocking is None:
        fail_reasons = strict_fail_reasons_from_findings_and_verdict(
            getattr(facade, "finding_ledger", ()) or (),
            verdict=getattr(facade, "verdict", None),
        )
        has_blocking = bool(fail_reasons)
    pass_label = "YES" if not bool(has_blocking) else "NO"
    quirks = tuple(quirks_used_from_findings(getattr(facade, "finding_ledger", ()) or ()))
    source_completeness = tuple(
        source_completeness_issues_from_findings(getattr(facade, "finding_ledger", ()) or ())
    )
    source_pathologies = tuple(getattr(facade, "source_pathology_rows", lambda: ())() or ())
    findings = len(getattr(facade, "finding_ledger", ()))
    bundle = getattr(facade, "bundle", None)
    temporal_events = len(getattr(bundle, "temporal_events", ()))
    quirks_used = len(quirks)
    source_completeness_issues = len(source_completeness)
    print(
        f"CompileFacade : strict={pass_label}"
        f"  findings={findings}"
        f"  temporal_events={temporal_events}"
        f"  quirks_used={quirks_used}"
        f"  source_completeness_issues={source_completeness_issues}"
    )
    if quirks_used:
        print(f"  Quirks       : {', '.join(sorted({str(item.kind) for item in quirks}))}")
    if source_completeness_issues:
        print(f"  SC issues    : {', '.join(sorted({str(item.kind) for item in source_completeness}))}")
    finding_ledger = tuple(getattr(facade, "finding_ledger", ()) or ())
    fail_reasons = list(
        strict_fail_reasons_from_findings_and_verdict(
            finding_ledger,
            verdict=getattr(facade, "verdict", None),
        )
    )
    if fail_reasons:
        print(f"  Fail reasons : {', '.join(fail_reasons)}")
    pathology_codes = tuple(
        sorted({
            str(row.get("code") or "")
            for row in source_pathologies
            if str(row.get("code") or "")
        })
    )
    if pathology_codes:
        print(f"  Pathologies  : {', '.join(pathology_codes)}")
    pathology_reasons = tuple(
        sorted({
            str((row.get("detail") or {}).get("diagnostic_reason") or "")
            for row in source_pathologies
            if isinstance(row.get("detail"), dict)
            and str((row.get("detail") or {}).get("diagnostic_reason") or "")
        })
    )
    if pathology_reasons:
        print(f"  Pathology reasons : {', '.join(pathology_reasons)}")
    html_noncomm_reason = str(html_noncommensurable_reason or "").strip()
    if html_noncomm_reason:
        print(f"  HTML/XML reason : {html_noncomm_reason}")
    obligations = [
        finding
        for finding in getattr(facade, "finding_ledger", ())
        if getattr(finding, "role", "") == "obligation"
    ]
    if obligations:
        print(
            f"  Obligations  : {len(obligations)} "
            f"({sum(1 for finding in obligations if getattr(finding, 'blocking', False))} blocking)  "
            f"kinds: {', '.join(sorted({str(getattr(finding, 'kind', '')) for finding in obligations if str(getattr(finding, 'kind', ''))}))}"
        )
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(args: Any) -> None:
    # Corpus-wide mode: triggered by --label or --show (no statute_id)
    statute_id: str | None = getattr(args, "statute_id", None)
    label: str | None = getattr(args, "label", None)
    show_label: str | None = getattr(args, "show", None)
    corpus_path: str | None = getattr(args, "corpus", None)
    import os as _os
    _par = getattr(args, "parallel", None)
    workers: int = _par if _par is not None else max(8, _os.cpu_count() or 4)

    # --show LABEL: display a saved run without re-running
    if show_label:
        rows = _load_strict_run(show_label)
        if rows is None:
            print(f"ERROR: no strict run found for label '{show_label}'", file=sys.stderr)
            sys.exit(1)
        _show_corpus_summary(rows, show_label)
        return

    # Corpus mode: no statute_id supplied, or --label supplied alongside no single SID
    # (allow: lawvm strict-report --label foo   or   lawvm strict-report --parallel 4 --label foo)
    if statute_id is None or label is not None:
        # Corpus run
        if corpus_path is None:
            corpus_path = _default_corpus_path()
        if not Path(corpus_path).exists():
            print(f"ERROR: corpus file not found: {corpus_path}", file=sys.stderr)
            sys.exit(1)

        corpus = _load_corpus(corpus_path)
        if not corpus:
            print(f"ERROR: corpus empty or unparseable: {corpus_path}", file=sys.stderr)
            sys.exit(1)

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        if label is None:
            label = f"strict_{timestamp.replace(':', '').replace('-', '')[:13]}"

        print(f"Running strict-report: {len(corpus)} statutes  label={label}  workers={workers}")
        print()

        results = _run_corpus(corpus, workers=workers, verbose=True)

        _show_corpus_summary(results, label)

        run_path = _save_strict_run(results, label, timestamp)
        print(f"Run saved : {run_path}")
        return

    mode = getattr(args, "mode", "finlex_oracle")
    show_facade = getattr(args, "facade", False)
    from lawvm.finland.compile import compile_fi_facade_from_replay
    from lawvm.finland.grafter import replay_xml

    compiled_ops: list[dict[str, object]] = []
    replay_meta: dict[str, object] = {}
    canonical_ops: list[LegalOperation] = []
    failed_ops: list[FailedOp] = []
    master = replay_xml(
        statute_id,
        mode=mode,
        quiet=True,
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
        lo_ops_out=canonical_ops,
        failed_ops_out=failed_ops,
    )
    facade = compile_fi_facade_from_replay(
        parent_id=statute_id,
        replay_result=master,
        replay_mode=mode,
        compiled_ops=compiled_ops,
        replay_meta=replay_meta,
        canonical_ops=canonical_ops,
        failed_ops=failed_ops,
    )
    report_record = report_record_from_facade(
        statute_id=statute_id,
        facade=facade,
        compiled_ops=compiled_ops,
        failed_ops=failed_ops,
        source_adjudication=master.source_adjudication,
    )

    if getattr(args, "json_output", False):
        json.dump(_to_json(report_record), sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(_format_report(report_record, verbose=getattr(args, "verbose", False)))

    if show_facade:
        _print_facade_summary(
            facade,
            html_noncommensurable_reason=(
                str(master.source_adjudication.html_noncommensurable_reason or "")
                if master.source_adjudication is not None
                else ""
            ),
        )
