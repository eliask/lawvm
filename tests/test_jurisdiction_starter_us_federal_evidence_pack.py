"""U.S. federal dry-run evidence-pack export (offline, synthetic Title 99 window).

Mirrors the NZ evidence-pack test shape: a bounded, no-network window built from
the committed Title 99 fixtures is projected into the shared evidence-row stream,
then the row schema, JSONL round-trip, disposition filtering, and summary counts
are asserted. The pack is a faithful projection of the dry-run kernel: every row's
disposition/rule_id/offending text comes verbatim from
``USDryRunReport``; nothing is recomputed or repaired to the oracle.
"""

from __future__ import annotations

from pathlib import Path

from lawvm.core.evidence_contracts import (
    validate_corpus_finding_evidence_row,
    validate_corpus_operation_evidence_row,
)
from lawvm.tools.cli import _build_parser
from lawvm.tools.report_query import load_report_query_records
from lawvm.us_federal.dry_run import (
    DISPOSITION_LAWVM_WRONG,
    DISPOSITION_MISSING_SOURCE,
    US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID,
    US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID,
    USDryRunReport,
    build_us_dry_run,
)
from lawvm.us_federal.evidence_pack import (
    USEvidencePackReport,
    build_evidence_pack_report,
    build_single_window_evidence_pack,
    write_evidence_pack_jsonl,
)

FIXTURES = Path(__file__).parent / "fixtures" / "us_federal"
BEFORE_HTM = (FIXTURES / "usc-dryrun-before.htm").read_bytes()
AFTER_HTM = (FIXTURES / "usc-dryrun-after.htm").read_bytes()
PLAW_STRIKE_INSERT = (FIXTURES / "plaw-dryrun-strike-insert.xml").read_bytes()


def _build_report(plaw_blobs: dict[str, bytes] | None = None) -> USDryRunReport:
    return build_us_dry_run(
        before_htm=BEFORE_HTM,
        after_htm=AFTER_HTM,
        plaw_blobs={"PL 99-2": PLAW_STRIKE_INSERT} if plaw_blobs is None else plaw_blobs,
        title=99,
        before_year="2023",
        after_year="2024",
    )


def _plaw_strike(title: int, section: str, struck: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<uslm xmlns="http://schemas.gpo.gov/xml/uslm"><meta>'
        "<congress>99</congress><docNumber>3</docNumber>"
        "<approvedDate>2024-01-01</approvedDate></meta><main><section><num>1</num>"
        f'<content><ref href="/us/usc/t{title}/s{section}">Section {section} of title '
        f"{title}, United States Code</ref>, <amendingAction type=\"amend\">is amended</amendingAction>"
        ' by <amendingAction type="delete">striking</amendingAction> '
        f"“<quotedText>{struck}</quotedText>” and "
        '<amendingAction type="insert">inserting</amendingAction> '
        "“<quotedText>X</quotedText>”.</content></section></main></uslm>"
    ).encode("utf-8")


# ---------------------------------------------------------------------------
# Row schema + agreement/missing_source projection
# ---------------------------------------------------------------------------


def test_pack_projects_agreement_and_missing_source_gap_without_replay_claim() -> None:
    pack = build_single_window_evidence_pack(_build_report())
    summary = pack.summary()

    assert summary["replay_claims"] is False
    assert summary["window_count"] == 1
    assert summary["windows"] == ["title99:2023->2024"]
    # The agreeing section 10 -> one operation row; section 30 changed-but-unclaimed
    # -> one missing_source finding row.
    assert summary["row_kind_counts"] == {"finding": 1, "operation": 1}
    assert summary["disposition_counts"] == {"agreement": 1, DISPOSITION_MISSING_SOURCE: 1}
    assert summary["title_counts"] == {"99": 2}
    assert summary["rule_id_counts"][US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID] == 1

    rows = pack.evidence_rows()
    by_id = {row.to_dict().get("row_id") or row.to_dict().get("finding_id"): row.to_dict() for row in rows}

    # Agreement operation row: pinned address + offending/diff surface present.
    agree = next(r for r in by_id.values() if r.get("evidence_status") == "matched")
    assert agree["frontend_id"] == "us_federal"
    # ``detail`` is frozen on the shared row, so nested lists round-trip as tuples
    # in-memory (and back to lists once JSON-serialized).
    assert [list(seg) for seg in agree["detail"]["address"]] == [["title", "99"], ["section", "10"]]
    assert agree["detail"]["section_key"] == "99:10"
    assert agree["detail"]["window"]["key"] == "title99:2023->2024"
    assert "19-year" in agree["detail"]["materialized_text"]
    assert agree["detail"]["oracle_text"]

    # missing_source finding row carries the cataloged rule id + the address.
    miss = next(r for r in by_id.values() if r.get("finding_id", "").endswith("missing_source"))
    assert miss["rule_id"] == US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID
    assert miss["evidence"]["section_key"] == "99:30"
    assert miss["evidence"]["disposition"] == DISPOSITION_MISSING_SOURCE


def test_lawvm_wrong_residual_row_carries_offending_text_verbatim() -> None:
    # Strike "15-year" but insert WRONG replacement; oracle says 19-year.
    report = _build_report({"PL 99-3": _plaw_strike(99, "10", "15-year")})
    pack = build_single_window_evidence_pack(report)
    rows = [row.to_dict() for row in pack.evidence_rows()]

    diverged = next(r for r in rows if r.get("evidence_status") == "diverged")
    assert diverged["detail"]["disposition"] == DISPOSITION_LAWVM_WRONG
    assert diverged["detail"]["rule_id"] == US_DRY_RUN_RESIDUAL_TEXT_MISMATCH_RULE_ID
    # The offending materialized text is OUR op (not repaired to the oracle).
    assert "the X period" in diverged["detail"]["materialized_text"]
    assert "15-year" not in diverged["detail"]["materialized_text"]
    assert "19-year" in diverged["detail"]["oracle_text"]
    assert diverged["detail"]["match_text"] == "15-year"


# ---------------------------------------------------------------------------
# JSONL round-trip + report-query validation
# ---------------------------------------------------------------------------


def test_jsonl_round_trip_validates_as_report_query_rows(tmp_path) -> None:
    pack = build_single_window_evidence_pack(_build_report())
    path = tmp_path / "us_evidence_pack.jsonl"
    count = write_evidence_pack_jsonl(pack, path)

    records = load_report_query_records((path,), validate=True)
    assert count == 2
    assert len(records) == 2
    for record in records:
        assert record.validation_issues == ()
        row = record.evidence_row
        if record.row_kind == "finding":
            assert validate_corpus_finding_evidence_row(row) == ()
        else:
            assert validate_corpus_operation_evidence_row(row) == ()


def test_disposition_filter_scopes_jsonl_to_missing_source(tmp_path) -> None:
    pack = build_single_window_evidence_pack(_build_report())
    path = tmp_path / "us_evidence_pack_missing.jsonl"
    count = write_evidence_pack_jsonl(pack, path, disposition=DISPOSITION_MISSING_SOURCE)

    records = load_report_query_records((path,), validate=True)
    assert count == 1
    assert len(records) == 1
    assert records[0].evidence_row["evidence"]["section_key"] == "99:30"


def test_row_kind_and_rule_id_filters() -> None:
    pack = build_single_window_evidence_pack(_build_report())

    operations = pack.filtered_evidence_rows(row_kind="operation")
    findings = pack.filtered_evidence_rows(row_kind="finding")
    assert len(operations) == 1
    assert len(findings) == 1

    by_rule = pack.filtered_evidence_rows(rule_id=US_DRY_RUN_RESIDUAL_ORACLE_CHANGED_NOT_CLAIMED_RULE_ID)
    assert len(by_rule) == 1
    assert by_rule[0].to_dict()["finding_id"].endswith("missing_source")


def test_to_jsonable_truncates_and_reports_filters() -> None:
    pack = build_single_window_evidence_pack(_build_report())
    payload = pack.to_jsonable(row_limit=1)
    assert payload["replay_claims"] is False
    assert payload["filtered_evidence_rows"] == 2
    assert payload["rows_truncated"] is True
    assert payload["rows_omitted"] == 1
    assert len(payload["evidence_rows"]) == 1

    filtered = pack.to_jsonable(disposition=DISPOSITION_MISSING_SOURCE)
    assert filtered["filters"] == {"disposition": DISPOSITION_MISSING_SOURCE}
    assert filtered["filtered_evidence_rows"] == 1
    assert filtered["filtered_summary"]["total_evidence_rows"] == 1


def test_typed_refusal_is_a_finding_row_not_counted_as_agreement() -> None:
    # An off-title op is typed-refused by the kernel; the pack surfaces it as a
    # refusal finding bucketed by family, never miscounted as an agreement.
    report = _build_report({"PL 99-3": _plaw_strike(7, "100", "anything")})
    pack = build_single_window_evidence_pack(report)
    summary = pack.summary()

    assert summary["disposition_counts"].get("refusal", 0) >= 1
    # No agreement was manufactured for the off-title op (no section row at all).
    assert "agreement" not in summary["disposition_counts"]
    refusals = pack.filtered_evidence_rows(row_kind="finding")
    assert any(r.to_dict().get("family") == "refusal" for r in refusals)


def test_multi_window_pack_aggregates_rows() -> None:
    report = _build_report()
    pack = build_evidence_pack_report(window_reports=(report, report))
    assert isinstance(pack, USEvidencePackReport)
    summary = pack.summary()
    assert summary["window_count"] == 2
    # Each window contributes the same 2 rows (1 op + 1 finding).
    assert summary["total_evidence_rows"] == 4
    assert summary["row_kind_counts"] == {"finding": 2, "operation": 2}


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_us_evidence_pack_cli_parse_defaults() -> None:
    parser = _build_parser()
    args = parser.parse_args(
        ["us-evidence-pack", "--title", "11", "--before", "2023", "--after", "2024"]
    )
    assert args.command == "us-evidence-pack"
    assert args.title == 11
    assert args.before_year == 2023
    assert args.after_year == 2024
    assert args.bench is False
    assert args.row_kind == ""
    assert args.disposition == ""
    assert args.rule_id == ""
    assert args.limit == 40
    assert args.output_jsonl is None
    assert args.json is False


def test_us_evidence_pack_cli_bench_flag() -> None:
    parser = _build_parser()
    args = parser.parse_args(["us-evidence-pack", "--bench", "--title", "11", "--json"])
    assert args.bench is True
    assert args.title == 11
    assert args.json is True
