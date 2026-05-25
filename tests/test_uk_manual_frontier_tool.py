from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from lawvm.tools import uk_effects
from lawvm.tools import uk_manual_frontier
from lawvm.uk_legislation import effects as uk_legislation_effects


class _FakeArchive:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __enter__(self) -> "_FakeArchive":
        return self

    def __exit__(self, *_args: object) -> bool:
        return False


def test_validate_manual_frontier_rows_marks_stale_and_still_blocked(
    monkeypatch,
    tmp_path: Path,
) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    effects = (
        SimpleNamespace(effect_id="eff-now-supported"),
        SimpleNamespace(effect_id="eff-still-frontier"),
    )

    monkeypatch.setattr(farchive, "Farchive", _FakeArchive)
    monkeypatch.setattr(
        uk_legislation_effects,
        "load_effects_for_statute_from_archive",
        lambda *_args, **_kwargs: list(effects),
    )
    monkeypatch.setattr(
        uk_effects,
        "build_uk_effect_summary_context",
        lambda *_args, **_kwargs: object(),
    )

    def fake_summarize(effect: object, *_args: object, **_kwargs: object) -> uk_effects._EffectSummary:
        if getattr(effect, "effect_id") == "eff-now-supported":
            return uk_effects._EffectSummary(
                source_pathology="",
                compare_shape="commensurable",
                n_ops=2,
                candidate=True,
                resolver_eids=("section-1",),
                lowering_rejections=(),
                manual_compile_status="deterministic_frontend_supported",
                manual_compile_rule_id="uk_manual_frontier_deterministic_supported",
                replay_applicable=True,
                structural_for_replay=True,
            )
        return uk_effects._EffectSummary(
            source_pathology="cross_container_renumber_unsupported",
            compare_shape="commensurable",
            n_ops=0,
            candidate=False,
            resolver_eids=(),
            lowering_rejections=(
                {"rule_id": "uk_effect_metadata_cross_container_renumber_rejected"},
            ),
            manual_compile_status="manual_compile_candidate",
            manual_compile_rule_id="uk_manual_frontier_cross_container_renumber_candidate",
            manual_compile_blocking_lowering_rule_ids=(
                "uk_effect_metadata_cross_container_renumber_rejected",
            ),
            replay_applicable=True,
            structural_for_replay=True,
        )

    monkeypatch.setattr(uk_effects, "summarize_uk_effect", fake_summarize)
    monkeypatch.setattr(
        uk_effects,
        "_manual_compile_suggested_claim_template",
        lambda *, statute_id, row: (
            {"schema": "lawvm.uk_semantic_compile_claim_template.v1"}
            if row.summary.manual_compile_status == "manual_compile_candidate"
            else {}
        ),
    )

    rows = uk_manual_frontier.validate_manual_frontier_rows(
        (
            {
                "line_number": 1,
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-now-supported",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_definition_list_end_insert_candidate",
            },
            {
                "line_number": 2,
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-still-frontier",
                "manual_compile_status": "manual_compile_candidate",
                "manual_compile_rule_id": "uk_manual_frontier_cross_container_renumber_candidate",
            },
            {
                "line_number": 3,
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-missing",
            },
        ),
        db_path=db_path,
    )

    assert [row["validator_status"] for row in rows] == [
        "resolved_deterministic_supported",
        "still_manual_frontier",
        "effect_not_found",
    ]
    assert rows[0]["rule_id"] == (
        "uk_manual_frontier_validator_currently_deterministic_supported"
    )
    assert rows[1]["current_blocking_lowering_rule_ids"] == [
        "uk_effect_metadata_cross_container_renumber_rejected"
    ]
    assert rows[1]["current_suggested_claim_template_status"] == "available"
    assert rows[2]["blocking"] is True


def test_uk_manual_frontier_validate_main_emits_json(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    input_path = tmp_path / "frontier.jsonl"
    validation_path = tmp_path / "validation.jsonl"
    remaining_path = tmp_path / "remaining.jsonl"
    input_path.write_text(
        json.dumps(
            {
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        uk_manual_frontier,
        "validate_manual_frontier_rows",
        lambda rows, *, db_path: (
            {
                "schema": "lawvm.uk_manual_frontier_validation.v1",
                "rule_id": "uk_manual_frontier_validator_currently_compiles",
                "validator_status": "still_manual_frontier",
                "statute_id": rows[0]["statute_id"],
                "effect_id": rows[0]["effect_id"],
                "current_manual_compile_status": "manual_compile_candidate",
                "current_manual_compile_rule_id": "uk_manual_frontier_heading_facet_candidate",
                "current_compiled_op_count": 0,
                "current_blocking_lowering_rule_ids": [
                    "uk_effect_heading_only_ref_rejected"
                ],
                "current_suggested_claim_template_status": "available",
                "current_suggested_claim_template": {
                    "required_ownership": ["fresh_current_claim"]
                },
            },
        ),
    )

    uk_manual_frontier.main(
        Namespace(
            input=str(input_path),
            db=str(db_path),
            json=True,
            summary_only=False,
            validation_jsonl=str(validation_path),
            remaining_jsonl=str(remaining_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["report_kind"] == "uk_manual_frontier_validation_report"
    assert payload["summary"]["validator_status_counts"] == {
        "still_manual_frontier": 1
    }
    assert payload["summary"]["remaining_row_count"] == 1
    assert payload["summary"]["stale_row_count"] == 0
    assert payload["summary"]["validation_error_count"] == 0
    assert payload["summary"]["remaining_manual_rule_counts"] == {
        "uk_manual_frontier_heading_facet_candidate": 1
    }
    assert payload["summary"]["current_suggested_claim_template_status_counts"] == {
        "available": 1
    }
    assert payload["summary"]["remaining_suggested_claim_template_status_counts"] == {
        "available": 1
    }
    assert payload["summary"]["remaining_blocking_lowering_rule_counts"] == {
        "uk_effect_heading_only_ref_rejected": 1
    }
    assert payload["validation_jsonl"] == {
        "path": str(validation_path),
        "rows": 1,
    }
    assert payload["remaining_jsonl"] == {
        "path": str(remaining_path),
        "rows": 1,
        "statuses": ["still_manual_frontier"],
    }
    assert payload["rows"][0]["effect_id"] == "eff-1"
    assert json.loads(validation_path.read_text(encoding="utf-8"))["effect_id"] == "eff-1"
    remaining_row = json.loads(remaining_path.read_text(encoding="utf-8"))
    assert remaining_row["effect_id"] == "eff-1"
    assert remaining_row["validator_current_manual_compile_rule_id"] == (
        "uk_manual_frontier_heading_facet_candidate"
    )
    assert remaining_row["suggested_claim_template"] == {
        "required_ownership": ["fresh_current_claim"]
    }


def test_read_jsonl_rows_preserves_decode_errors_as_input_rows(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text('{"statute_id": "ukpga/2020/1"}\n{"bad"\n', encoding="utf-8")

    rows = uk_manual_frontier._read_jsonl_rows(path)

    assert rows[0]["line_number"] == 1
    assert rows[1]["line_number"] == 2
    assert rows[1]["validator_status"] == "input_error"
    assert rows[1]["validator_rule_id"] == "uk_manual_frontier_jsonl_decode_error"


def test_remaining_workqueue_rows_keep_only_live_manual_frontier_rows() -> None:
    original_rows = (
        {
            "line_number": 1,
            "statute_id": "ukpga/2020/1",
            "effect_id": "eff-resolved",
            "source": {"text_preview": "resolved"},
        },
        {
            "line_number": 2,
            "statute_id": "ukpga/2020/1",
            "effect_id": "eff-live",
            "source": {"text_preview": "live"},
        },
    )
    validation_rows = (
        {
            "validator_status": "resolved_deterministic_supported",
            "rule_id": "uk_manual_frontier_validator_currently_deterministic_supported",
            "current_compiled_op_count": 1,
        },
        {
            "validator_status": "still_manual_frontier",
            "rule_id": "uk_manual_frontier_validator_still_manual_frontier",
            "current_manual_compile_status": "manual_compile_candidate",
            "current_manual_compile_rule_id": "uk_manual_frontier_table_appropriate_place_candidate",
            "current_compiled_op_count": 0,
            "current_blocking_lowering_rule_ids": [
                "uk_effect_table_entry_instruction_rejected"
            ],
            "current_suggested_claim_template_status": "available",
            "current_suggested_claim_template": {
                "required_ownership": [
                    "source_named_table_surface",
                    "table_ordering_rule_or_anchor_claim",
                ],
                "required_validator_checks": [
                    "claim_identifies_table_ordering_rule_or_anchor"
                ],
            },
        },
    )

    remaining = uk_manual_frontier._remaining_workqueue_rows(
        original_rows,
        validation_rows,
    )

    assert len(remaining) == 1
    assert remaining[0]["effect_id"] == "eff-live"
    assert remaining[0]["source"] == {"text_preview": "live"}
    assert remaining[0]["validator_status"] == "still_manual_frontier"
    assert remaining[0]["validator_current_manual_compile_rule_id"] == (
        "uk_manual_frontier_table_appropriate_place_candidate"
    )
    assert remaining[0]["validator_current_blocking_lowering_rule_ids"] == [
        "uk_effect_table_entry_instruction_rejected"
    ]
    assert remaining[0]["suggested_claim_template_status"] == "available"
    assert remaining[0]["suggested_claim_template"]["required_ownership"] == [
        "source_named_table_surface",
        "table_ordering_rule_or_anchor_claim",
    ]
    assert remaining[0]["validation"] == validation_rows[1]


def test_print_text_report_includes_export_paths(capsys) -> None:
    uk_manual_frontier._print_text_report(
        {
            "summary": {
                "row_count": 1,
                "remaining_row_count": 1,
                "stale_row_count": 0,
                "validation_error_count": 0,
                "validator_status_counts": {"still_manual_frontier": 1},
                "validator_rule_counts": {
                    "uk_manual_frontier_validator_still_manual_frontier": 1
                },
                "remaining_manual_rule_counts": {
                    "uk_manual_frontier_table_appropriate_place_candidate": 1
                },
                "current_suggested_claim_template_status_counts": {"available": 1},
                "remaining_suggested_claim_template_status_counts": {"available": 1},
                "remaining_blocking_lowering_rule_counts": {
                    "uk_effect_table_entry_instruction_rejected": 1
                },
            },
            "validation_jsonl": {"path": ".tmp/validation.jsonl", "rows": 1},
            "remaining_jsonl": {
                "path": ".tmp/remaining.jsonl",
                "rows": 1,
                "statuses": ["still_manual_frontier"],
            },
            "rows": (),
        }
    )

    out = capsys.readouterr().out
    assert "Triage: remaining=1 stale=0 validation_errors=0" in out
    assert (
        "Remaining manual rules: "
        "uk_manual_frontier_table_appropriate_place_candidate=1"
    ) in out
    assert "Current claim templates: available=1" in out
    assert "Remaining claim templates: available=1" in out
    assert (
        "Remaining blocking lowering: "
        "uk_effect_table_entry_instruction_rejected=1"
    ) in out
    assert "Validation JSONL: .tmp/validation.jsonl rows=1" in out
    assert (
        "Remaining JSONL: .tmp/remaining.jsonl rows=1 "
        "statuses=still_manual_frontier"
    ) in out


def test_uk_manual_frontier_validate_fail_on_stale_exits_after_report(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    input_path = tmp_path / "frontier.jsonl"
    input_path.write_text(
        json.dumps({"statute_id": "ukpga/2020/1", "effect_id": "eff-1"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        uk_manual_frontier,
        "validate_manual_frontier_rows",
        lambda rows, *, db_path: (
            {
                "schema": "lawvm.uk_manual_frontier_validation.v1",
                "rule_id": "uk_manual_frontier_validator_currently_deterministic_supported",
                "validator_status": "resolved_deterministic_supported",
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-1",
            },
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_manual_frontier.main(
            Namespace(
                input=str(input_path),
                db=str(db_path),
                json=False,
                summary_only=False,
                validation_jsonl="",
                remaining_jsonl="",
                fail_on_stale=True,
                fail_on_validation_error=False,
            )
        )

    assert excinfo.value.code == 1
    assert "Triage: remaining=0 stale=1 validation_errors=0" in capsys.readouterr().out


def test_uk_manual_frontier_validate_fail_on_validation_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    input_path = tmp_path / "frontier.jsonl"
    input_path.write_text(
        json.dumps({"statute_id": "ukpga/2020/1", "effect_id": "eff-missing"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        uk_manual_frontier,
        "validate_manual_frontier_rows",
        lambda rows, *, db_path: (
            {
                "schema": "lawvm.uk_manual_frontier_validation.v1",
                "rule_id": "uk_manual_frontier_validator_effect_not_found",
                "validator_status": "effect_not_found",
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-missing",
            },
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_manual_frontier.main(
            Namespace(
                input=str(input_path),
                db=str(db_path),
                json=True,
                summary_only=False,
                validation_jsonl="",
                remaining_jsonl="",
                fail_on_stale=False,
                fail_on_validation_error=True,
            )
        )

    assert excinfo.value.code == 1


def test_uk_manual_frontier_validate_fail_on_remaining_exits_after_report(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")
    input_path = tmp_path / "frontier.jsonl"
    input_path.write_text(
        json.dumps({"statute_id": "ukpga/2020/1", "effect_id": "eff-live"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        uk_manual_frontier,
        "validate_manual_frontier_rows",
        lambda rows, *, db_path: (
            {
                "schema": "lawvm.uk_manual_frontier_validation.v1",
                "rule_id": "uk_manual_frontier_validator_still_manual_frontier",
                "validator_status": "still_manual_frontier",
                "statute_id": "ukpga/2020/1",
                "effect_id": "eff-live",
                "current_manual_compile_status": "manual_compile_candidate",
                "current_manual_compile_rule_id": (
                    "uk_manual_frontier_cross_container_renumber_candidate"
                ),
                "current_blocking_lowering_rule_ids": [
                    "uk_effect_metadata_cross_container_renumber_rejected"
                ],
            },
        ),
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_manual_frontier.main(
            Namespace(
                input=str(input_path),
                db=str(db_path),
                json=False,
                summary_only=False,
                validation_jsonl="",
                remaining_jsonl="",
                fail_on_stale=False,
                fail_on_validation_error=False,
                fail_on_remaining=True,
            )
        )

    assert excinfo.value.code == 1
    assert "Triage: remaining=1 stale=0 validation_errors=0" in capsys.readouterr().out


def test_validation_report_summary_only_omits_rows() -> None:
    report = uk_manual_frontier._validation_report_jsonable(
        input_path=Path("frontier.jsonl"),
        db_path=Path("uk.farchive"),
        rows=(
            {
                "rule_id": "uk_manual_frontier_validator_currently_deterministic_supported",
                "validator_status": "resolved_deterministic_supported",
                "original_manual_compile_rule_id": "uk_manual_frontier_definition_list_end_insert_candidate",
            },
        ),
        summary_only=True,
    )

    assert "rows" not in report
    assert report["summary"]["stale_row_count"] == 1
    assert report["summary"]["stale_original_manual_rule_counts"] == {
        "uk_manual_frontier_definition_list_end_insert_candidate": 1
    }


def test_print_text_report_summary_only_omits_row_lines(capsys) -> None:
    uk_manual_frontier._print_text_report(
        {
            "summary": {
                "row_count": 1,
                "remaining_row_count": 0,
                "stale_row_count": 1,
                "validation_error_count": 0,
                "validator_status_counts": {"resolved_deterministic_supported": 1},
                "validator_rule_counts": {
                    "uk_manual_frontier_validator_currently_deterministic_supported": 1
                },
                "remaining_manual_rule_counts": {},
                "current_suggested_claim_template_status_counts": {},
                "remaining_suggested_claim_template_status_counts": {},
                "remaining_blocking_lowering_rule_counts": {},
                "stale_original_manual_rule_counts": {
                    "uk_manual_frontier_definition_list_end_insert_candidate": 1
                },
            },
            "rows": (
                {
                    "validator_status": "resolved_deterministic_supported",
                    "statute_id": "ukpga/2020/1",
                    "effect_id": "eff-1",
                },
            ),
        },
        summary_only=True,
    )

    out = capsys.readouterr().out
    assert "Triage: remaining=0 stale=1 validation_errors=0" in out
    assert "Remaining manual rules: {}" in out
    assert (
        "Stale original manual rules: "
        "uk_manual_frontier_definition_list_end_insert_candidate=1"
    ) in out
    assert "ukpga/2020/1" not in out
