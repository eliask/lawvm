from __future__ import annotations

from lawvm.core.adjudication_evidence import (
    adjudication_diagnostic_detail,
    adjudication_finding_evidence_rows,
    adjudication_record_diagnostic_detail,
)
from lawvm.core.diagnostic_records import validate_diagnostic_detail
from lawvm.replay_adjudication import CompileAdjudication


def test_adjudication_diagnostic_detail_defaults_to_blocking_compile_envelope() -> None:
    adjudication = CompileAdjudication(
        kind="uk_replay_target_not_found",
        message="target missing",
        source_statute="ukpga/2000/1",
        op_id="op-1",
        detail={"target": "section:99"},
    )

    detail = adjudication_diagnostic_detail(adjudication)

    assert detail["rule_id"] == "uk_replay_target_not_found"
    assert detail["phase"] == "replay"
    assert detail["blocking"] is True
    assert detail["strict_disposition"] == "block"
    assert detail["quirks_disposition"] == "record"
    assert detail["target"] == "section:99"
    assert validate_diagnostic_detail(detail) == ()


def test_adjudication_record_diagnostic_detail_preserves_nonblocking_detail() -> None:
    detail = adjudication_record_diagnostic_detail(
        {
            "kind": "text_duplication_warning",
            "detail": {
                "phase": "replay_fold",
                "blocking": False,
                "kind": "duplicate_suffix_text",
                "path": "body/section:1",
            },
        }
    )

    assert detail["rule_id"] == "text_duplication_warning"
    assert detail["phase"] == "replay_fold"
    assert detail["blocking"] is False
    assert detail["strict_disposition"] == "record"
    assert detail["kind"] == "duplicate_suffix_text"
    assert detail["path"] == "body/section:1"
    assert validate_diagnostic_detail(detail) == ()


def test_adjudication_finding_rows_expose_raw_and_normalized_details() -> None:
    adjudication = CompileAdjudication(
        kind="no_replay_missing_amendment_source",
        message="missing source",
        source_statute="no/lovtid/2025-02-02-5",
        op_id="no-op-1",
        detail={"rule_id": "no.replay.missing_amendment_source"},
    )

    rows = adjudication_finding_evidence_rows(
        (adjudication,),
        frontend_id="norway",
        base_id="no/lov/2025-01-01-1",
        as_of="2025-02-15",
    )

    row = rows[0].to_dict()
    assert row["rule_id"] == "no.replay.missing_amendment_source"
    assert row["phase"] == "acquisition"
    assert row["blocking"] is True
    assert row["strict_disposition"] == "block"
    assert row["evidence"]["detail"] == {"rule_id": "no.replay.missing_amendment_source"}
    assert row["evidence"]["diagnostic_detail"]["rule_id"] == "no.replay.missing_amendment_source"
    assert row["evidence"]["diagnostic_detail"]["blocking"] is True
