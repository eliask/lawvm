"""U.S. federal dry-run evidence-pack export — one auditable row per residual.

This module does not re-run lowering, recompute dispositions, or repair residuals.
It is a faithful PROJECTION of what the dry-run kernel
(:func:`lawvm.us_federal.dry_run.build_us_dry_run_from_archive`) already emits for a
window: each per-section agreement/residual row, plus the boundary-level gaps
(``missing_source`` oracle-changed-but-not-claimed sections, ``sunset_reversion``
temporal reclassifications) and the typed refusals, become one shared
report-query-compatible evidence row.

The point is auditability: the bench currently reports ``lawvm_wrong`` /
``missing_source`` as opaque aggregate counts. Here each one is a sampleable row
carrying the offending text (the op's match_text / replacement, the
before/materialized/oracle text surfaces), its disposition, its rule_id, the
pinned USC section address, and the window it belongs to — so a residual can be
adjudicated (compare-shape / OLRC editorial normalization vs. a true replay
defect) instead of guessed at.

Honesty contract (mirrors the dry-run kernel and AGENTS.md §0/§9):

- Agreements are includable too (the pack is the full audit surface, not just
  failures); ``disposition`` is the filter key.
- No row is fabricated. A residual whose materialization the kernel refused to
  produce carries an empty ``materialized_text`` — never a guess.
- The oracle is a witness, never ground truth: the oracle text is carried as a
  comparison witness, never used to repair the materialized text.
- ``replay_claims`` is ``False`` always; this is a dry-run projection.

The exported rows are :class:`lawvm.core.evidence_contracts.CorpusOperationEvidenceRow`
(per-section agreements and materialized residuals) and
:class:`lawvm.core.evidence_contracts.CorpusFindingEvidenceRow` (the boundary
``missing_source`` gap, the ``sunset_reversion`` temporal reclassification, and the
typed refusals — rows that have no single materialized op text). Every finding's
``rule_id`` is an existing cataloged ``us_…`` witness rule; this module introduces
no new rule ids.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from lawvm.core.evidence_contracts import (
    CorpusFindingEvidenceRow,
    CorpusOperationEvidenceRow,
    CorpusRowStatus,
    evidence_row_kind,
    evidence_rule_ids,
)
from lawvm.us_federal.dry_run import (
    DISPOSITION_MISSING_SOURCE,
    US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    USDryRunReport,
    USDryRunRefusal,
    USDryRunSectionRow,
)
from lawvm.us_federal.sunset import (
    DISPOSITION_SUNSET_REVERSION,
    US_SUNSET_REVERSION_RULE_ID,
    SunsetClassification,
)

_FRONTEND_ID = "us_federal"
_OWNER_PHASE = "dry_run"
# A residual/finding carries a reason-bearing detail but never blocks (the dry-run
# gate never authorizes replay), so the shared blocking invariant stays trivial.
_STRICT_WARN = "warn"
_QUIRKS_RECORD = "record_residual_without_repairing_to_oracle"


def _window_dict(report: USDryRunReport) -> dict[str, Any]:
    return {
        "title": report.title,
        "before_year": report.before_year,
        "after_year": report.after_year,
        "key": f"title{report.title}:{report.before_year}->{report.after_year}",
    }


def _address_tuple(section_key: str) -> list[list[str]]:
    """The pinned USC ``[[title, T], [section, S]]`` address for a section key."""
    title, _, section = section_key.partition(":")
    return [["title", title], ["section", section]]


def _section_row_status(row: USDryRunSectionRow) -> CorpusRowStatus:
    if row.status == "agree":
        return CorpusRowStatus.MATCHED
    return CorpusRowStatus.DIVERGED


def _operation_row(report: USDryRunReport, row: USDryRunSectionRow) -> CorpusOperationEvidenceRow:
    """Project one per-section dry-run row into a shared operation evidence row.

    The offending-text surface (match_text / replacement / before / materialized /
    oracle text) is carried verbatim in ``detail``; nothing is recomputed.
    """
    window = _window_dict(report)
    status = _section_row_status(row)
    detail: dict[str, Any] = {
        "window": window,
        "address": _address_tuple(row.section_key),
        "section_key": row.section_key,
        "disposition": row.disposition,
        "op_id": row.op_id,
        "action": row.action,
        "rule_id": row.rule_id,
        "oracle_changed": row.oracle_changed,
        # Offending-text / diff surface (verbatim; never repaired to the oracle).
        "match_text": row.match_text,
        "replacement": row.replacement,
        "before_text": row.before_text,
        "materialized_text": row.materialized_text,
        "oracle_text": row.oracle_text,
        "before_text_len": len(row.before_text),
        "materialized_text_len": len(row.materialized_text),
        "oracle_text_len": len(row.oracle_text),
    }
    if status in (CorpusRowStatus.DIVERGED,):
        # A claim-bearing diverged status does not require finding_ids, but carry a
        # reason so the row is self-evidencing when sampled.
        detail["reason"] = row.rule_id
    return CorpusOperationEvidenceRow(
        row_id=f"us-dry-run:{window['key']}:{row.section_key}:{row.op_id}",
        frontend_id=_FRONTEND_ID,
        source_artifact_id=row.op_id,
        source_unit_id=row.section_key,
        source_locator=window["key"],
        effect_family=row.action,
        canonical_family=row.disposition or "agreement",
        original_target=row.target_address,
        resolved_target=row.section_key,
        evidence_status=status,
        blocking=False,
        strict_disposition=_STRICT_WARN,
        quirks_disposition=_QUIRKS_RECORD,
        finding_ids=(row.rule_id,) if row.status != "agree" else (),
        detail=detail,
    )


def _missing_source_finding(report: USDryRunReport, section_key: str) -> CorpusFindingEvidenceRow:
    """The honest lowering gap: oracle changed a section the kernel never claimed."""
    window = _window_dict(report)
    return CorpusFindingEvidenceRow(
        finding_id=f"us-dry-run:{window['key']}:{section_key}:missing_source",
        frontend_id=_FRONTEND_ID,
        family="source_footing_gap",
        rule_id=US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
        phase=_OWNER_PHASE,
        message=(
            f"oracle changed section {section_key} in window {window['key']} but the "
            "kernel emitted no op for it (missing_source lowering gap)"
        ),
        source_artifact_id=section_key,
        source_unit_id=section_key,
        blocking=False,
        strict_disposition=_STRICT_WARN,
        quirks_disposition=_QUIRKS_RECORD,
        evidence={
            "window": window,
            "address": _address_tuple(section_key),
            "section_key": section_key,
            "disposition": DISPOSITION_MISSING_SOURCE,
            "oracle_changed": True,
        },
    )


def _sunset_finding(report: USDryRunReport, classification: SunsetClassification) -> CorpusFindingEvidenceRow:
    """An otherwise-missing_source section explained by a temporary-provision expiry."""
    window = _window_dict(report)
    section = classification.section
    section_key = f"{report.title}:{section}"
    witness = classification.witness
    return CorpusFindingEvidenceRow(
        finding_id=f"us-dry-run:{window['key']}:{section_key}:sunset_reversion",
        frontend_id=_FRONTEND_ID,
        family="temporal_mismatch",
        rule_id=US_SUNSET_REVERSION_RULE_ID,
        phase=_OWNER_PHASE,
        message=(
            f"oracle change to section {section_key} in window {window['key']} is the "
            "expiry of a temporary provision reverting to a prior permanent form "
            "(sunset_reversion, owned by the temporal layer)"
        ),
        source_artifact_id=section_key,
        source_unit_id=section_key,
        blocking=False,
        strict_disposition=_STRICT_WARN,
        quirks_disposition=_QUIRKS_RECORD,
        evidence={
            "window": window,
            "address": _address_tuple(section_key),
            "section_key": section_key,
            "disposition": DISPOSITION_SUNSET_REVERSION,
            "sunset_date": witness.sunset_date,
            "reverts_to_edition_year": witness.reverts_to_edition_year,
            "note_head": witness.note_head,
        },
    )


def _refusal_finding(report: USDryRunReport, refusal: USDryRunRefusal) -> CorpusFindingEvidenceRow:
    """A typed refusal (no materialization attempted) carried verbatim."""
    window = _window_dict(report)
    return CorpusFindingEvidenceRow(
        finding_id=f"us-dry-run:{window['key']}:refusal:{refusal.op_id}:{refusal.rule_id}",
        frontend_id=_FRONTEND_ID,
        family="refusal",
        rule_id=refusal.rule_id,
        phase=_OWNER_PHASE,
        message=refusal.message,
        source_artifact_id=refusal.op_id,
        source_unit_id=refusal.target_address,
        blocking=False,
        strict_disposition=_STRICT_WARN,
        quirks_disposition=_QUIRKS_RECORD,
        evidence={
            "window": window,
            "target_address": refusal.target_address,
            "op_id": refusal.op_id,
            "detail": dict(refusal.detail),
        },
    )


@dataclass(frozen=True)
class USEvidencePackReport:
    """Evidence-pack projection of one or more dry-run windows.

    ``window_reports`` are the underlying :class:`USDryRunReport` objects (one per
    bench window). The pack flattens their per-section rows, boundary gaps, and
    refusals into one report-query-compatible row stream. It makes no replay claim
    and recomputes nothing.
    """

    window_reports: tuple[USDryRunReport, ...]

    def evidence_rows(self) -> tuple[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow, ...]:
        rows: list[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow] = []
        for report in self.window_reports:
            for section_row in report.rows:
                rows.append(_operation_row(report, section_row))
            # Boundary-level gaps: oracle-changed sections the kernel never claimed.
            # A sunset_reversion is an EXPLAINED change (temporal layer), surfaced as
            # its own finding; the remainder are missing_source.
            claimed = set(report.claimed_sections)
            sunset_keys = report.sunset_reversion_section_keys()
            for classification in report.sunset_reversions:
                rows.append(_sunset_finding(report, classification))
            for section_key in report.oracle_changed_sections:
                if section_key in claimed or section_key in sunset_keys:
                    continue
                rows.append(_missing_source_finding(report, section_key))
            for refusal in report.refusals:
                rows.append(_refusal_finding(report, refusal))
        return tuple(rows)

    def filtered_evidence_rows(
        self,
        *,
        row_kind: str = "",
        disposition: str = "",
        rule_id: str = "",
        title: str = "",
    ) -> tuple[CorpusOperationEvidenceRow | CorpusFindingEvidenceRow, ...]:
        rows = self.evidence_rows()
        return tuple(
            row
            for row in rows
            if _evidence_row_matches(
                row.to_dict(),
                row_kind=row_kind,
                disposition=disposition,
                rule_id=rule_id,
                title=title,
            )
        )

    def summary(self) -> dict[str, Any]:
        row_dicts = tuple(row.to_dict() for row in self.evidence_rows())
        return {
            "windows": [_window_dict(report)["key"] for report in self.window_reports],
            "window_count": len(self.window_reports),
            "total_evidence_rows": len(row_dicts),
            **_evidence_rows_summary(row_dicts),
            "replay_claims": False,
        }

    def to_jsonable(
        self,
        *,
        row_limit: int | None = None,
        row_kind: str = "",
        disposition: str = "",
        rule_id: str = "",
        title: str = "",
    ) -> dict[str, Any]:
        rows = self.filtered_evidence_rows(
            row_kind=row_kind, disposition=disposition, rule_id=rule_id, title=title
        )
        row_dicts = tuple(row.to_dict() for row in rows)
        selected = rows if row_limit is None else rows[:row_limit]
        payload: dict[str, Any] = {
            "jurisdiction": "us_federal",
            "report_kind": "dry_run_evidence_pack",
            "truth_claim": "dry_run_residual_evidence_rows_only_not_actual_replay",
            "replay_claims": False,
            "summary": self.summary(),
            "filters": _jsonable_filters(
                row_kind=row_kind, disposition=disposition, rule_id=rule_id, title=title
            ),
            "filtered_summary": _evidence_rows_summary(row_dicts),
            "filtered_evidence_rows": len(rows),
            "evidence_rows": [row.to_dict() for row in selected],
        }
        if row_limit is not None and len(rows) > row_limit:
            payload["rows_truncated"] = True
            payload["rows_omitted"] = len(rows) - row_limit
        return payload


def build_evidence_pack_report(
    *,
    window_reports: tuple[USDryRunReport, ...],
) -> USEvidencePackReport:
    """Bundle one or more dry-run window reports into an evidence-pack projection."""
    return USEvidencePackReport(window_reports=tuple(window_reports))


def build_single_window_evidence_pack(report: USDryRunReport) -> USEvidencePackReport:
    """Convenience builder for a single dry-run window."""
    return build_evidence_pack_report(window_reports=(report,))


def write_evidence_pack_jsonl(
    report: USEvidencePackReport,
    path: Path,
    *,
    row_kind: str = "",
    disposition: str = "",
    rule_id: str = "",
    title: str = "",
) -> int:
    """Write the (optionally filtered) evidence rows as report-query JSONL."""
    rows = [
        row.to_dict()
        for row in report.filtered_evidence_rows(
            row_kind=row_kind, disposition=disposition, rule_id=rule_id, title=title
        )
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )
    return len(rows)


# ---------------------------------------------------------------------------
# Archive-backed assembly (read-only farchive; no network)
# ---------------------------------------------------------------------------


def build_window_evidence_pack_from_archive(
    archive: Any,
    *,
    title: int,
    before_year: int,
    after_year: int,
    prior_edition_years: tuple[int, ...] = (),
) -> USEvidencePackReport:
    """Run one dry-run window from the farchive and project it into an evidence pack.

    Derives the window's public laws from the before/after USC edition witness delta
    (a fact of the editions), runs the dry-run kernel read-only, and projects the
    result. Raises :class:`ValueError` when an edition is missing (the kernel never
    runs a silently-partial window).
    """
    from lawvm.us_federal.bench import derive_window_law_locators
    from lawvm.us_federal.dry_run import build_us_dry_run_from_archive

    locators = derive_window_law_locators(
        archive, title=title, before_year=before_year, after_year=after_year
    )
    if locators is None:
        raise ValueError(
            f"before/after USC edition missing from the U.S. farchive for title "
            f"{title} ({before_year}->{after_year})"
        )
    report = build_us_dry_run_from_archive(
        archive,
        title=title,
        before_year=before_year,
        after_year=after_year,
        plaw_locators=locators,
        prior_edition_years=prior_edition_years,
    )
    return build_single_window_evidence_pack(report)


def build_bench_evidence_pack_from_archive(
    archive: Any,
    *,
    corpus_path: Path | None = None,
    title: int | None = None,
) -> USEvidencePackReport:
    """Run the full bench corpus (or one title's windows) and project every window.

    A re-run of all bench windows is bounded by the corpus; pass ``title`` to scope
    to one title's windows for a faster, smaller pack (mirrors NZ scoping by work).
    Only ``evaluated`` windows contribute reports; typed skips contribute nothing.
    """
    from lawvm.us_federal.bench import DEFAULT_CORPUS_PATH, load_corpus, run_bench

    corpus = corpus_path if corpus_path is not None else DEFAULT_CORPUS_PATH
    windows = load_corpus(corpus)
    if title is not None:
        windows = [w for w in windows if w.title == title]
    bench = run_bench(archive, windows, corpus_path=str(corpus))
    reports = tuple(r.report for r in bench.evaluated() if r.report is not None)
    return build_evidence_pack_report(window_reports=reports)


# ---------------------------------------------------------------------------
# Filter + summary helpers
# ---------------------------------------------------------------------------


def _evidence_row_matches(
    row: Mapping[str, Any],
    *,
    row_kind: str,
    disposition: str,
    rule_id: str,
    title: str,
) -> bool:
    if row_kind and evidence_row_kind(row) != row_kind:
        return False
    if disposition and _row_disposition(row) != disposition:
        return False
    if rule_id and rule_id not in evidence_rule_ids(row):
        return False
    if title and _row_title(row) != title:
        return False
    return True


def _row_disposition(row: Mapping[str, Any]) -> str:
    for source in (row.get("detail"), row.get("evidence")):
        if isinstance(source, Mapping):
            value = source.get("disposition")
            if isinstance(value, str) and value:
                return value
    return ""


def _row_title(row: Mapping[str, Any]) -> str:
    for source in (row.get("detail"), row.get("evidence")):
        if isinstance(source, Mapping):
            window = source.get("window")
            if isinstance(window, Mapping):
                title = window.get("title")
                if title is not None:
                    return str(title)
    return ""


def _jsonable_filters(
    *,
    row_kind: str,
    disposition: str,
    rule_id: str,
    title: str,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "row_kind": row_kind,
            "disposition": disposition,
            "rule_id": rule_id,
            "title": title,
        }.items()
        if value
    }


def _evidence_rows_summary(rows: tuple[Mapping[str, Any], ...]) -> dict[str, Any]:
    return {
        "row_kind_counts": _row_kind_counts(rows),
        "disposition_counts": _disposition_counts(rows),
        "rule_id_counts": _rule_id_counts(rows),
        "title_counts": _title_counts(rows),
        "total_evidence_rows": len(rows),
    }


def _row_kind_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts = Counter(evidence_row_kind(row) for row in rows)
    return dict(sorted(counts.items()))


def _disposition_label(row: Mapping[str, Any]) -> str:
    """The disposition bucket for a row.

    Operation rows with no disposition are agreements (the agree row carries an
    empty disposition). Finding rows with no disposition (e.g. typed refusals) are
    bucketed by their ``family`` so they are never miscounted as agreements.
    """
    disposition = _row_disposition(row)
    if disposition:
        return disposition
    if evidence_row_kind(row) == "finding":
        family = row.get("family")
        if isinstance(family, str) and family:
            return family
        return "finding"
    return "agreement"


def _disposition_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts = Counter(_disposition_label(row) for row in rows)
    return dict(sorted(counts.items()))


def _rule_id_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for rule_id in sorted(evidence_rule_ids(row)):
            counts[rule_id] += 1
    return dict(sorted(counts.items()))


def _title_counts(rows: tuple[Mapping[str, Any], ...]) -> dict[str, int]:
    counts = Counter(_row_title(row) for row in rows if _row_title(row))
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


def main(args: argparse.Namespace) -> None:
    from lawvm.us_federal.sources import open_us_federal_farchive

    archive = open_us_federal_farchive(readonly=True)
    try:
        if getattr(args, "bench", False):
            corpus_path = Path(args.corpus) if getattr(args, "corpus", None) else None
            report = build_bench_evidence_pack_from_archive(
                archive,
                corpus_path=corpus_path,
                title=int(args.title) if getattr(args, "title", None) else None,
            )
        else:
            if not getattr(args, "title", None) or not getattr(args, "before_year", None) or not getattr(
                args, "after_year", None
            ):
                raise SystemExit(
                    "us-evidence-pack: --title/--before/--after are required unless --bench is given"
                )
            report = build_window_evidence_pack_from_archive(
                archive,
                title=int(args.title),
                before_year=int(args.before_year),
                after_year=int(args.after_year),
            )
    finally:
        archive.close()

    title_filter = ""
    if getattr(args, "bench", False) and getattr(args, "title", None):
        title_filter = str(args.title)

    output_row_count: int | None = None
    if getattr(args, "output_jsonl", None):
        output_row_count = write_evidence_pack_jsonl(
            report,
            Path(args.output_jsonl),
            row_kind=getattr(args, "row_kind", "") or "",
            disposition=getattr(args, "disposition", "") or "",
            rule_id=getattr(args, "rule_id", "") or "",
            title=title_filter,
        )

    if getattr(args, "json", False):
        payload = report.to_jsonable(
            row_limit=getattr(args, "limit", None),
            row_kind=getattr(args, "row_kind", "") or "",
            disposition=getattr(args, "disposition", "") or "",
            rule_id=getattr(args, "rule_id", "") or "",
            title=title_filter,
        )
        if output_row_count is not None:
            payload["output_jsonl"] = {"path": args.output_jsonl, "rows": output_row_count}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    if output_row_count is not None:
        print(f"wrote_evidence_rows={output_row_count} path={args.output_jsonl}")
    summary = report.summary()
    print(
        f"windows={summary['window_count']} total_evidence_rows={summary['total_evidence_rows']} "
        f"row_kind_counts={summary['row_kind_counts']} "
        f"disposition_counts={summary['disposition_counts']}"
    )
