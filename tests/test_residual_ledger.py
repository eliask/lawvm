from __future__ import annotations

import csv
import json
from pathlib import Path

from lawvm.tools.residual_ledger import (
    RESIDUAL_LEDGER_COLUMNS,
    build_row_from_phase_witness,
    validate_residual_ledger,
)


def test_validate_residual_ledger_accepts_valid_rows(tmp_path) -> None:
    path = tmp_path / "residual.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RESIDUAL_LEDGER_COLUMNS))
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "1962/184",
                "path": "section:17",
                "observed_symptom": "operative repeal text dropped",
                "suspected_first_bad_phase": "acquire",
                "confirmed_first_bad_phase": "",
                "interaction_family": "body_prose_only_repeal",
                "secondary_phase": "",
                "source_pathology_present": "unknown",
                "oracle_or_editorial_witness_drift": "no",
                "source_lane_used": "preamble",
                "fix_owner": "fi_frontend",
                "regression_ids": "1967/551",
                "status": "open",
                "notes": "",
            }
        )

    payload = validate_residual_ledger(path)
    assert payload["ok"] is True
    assert payload["rows_checked"] == 1
    assert payload["errors"] == []


def test_validate_residual_ledger_rejects_bad_phase_and_boolean(tmp_path) -> None:
    path = tmp_path / "residual.csv"
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(RESIDUAL_LEDGER_COLUMNS))
        writer.writeheader()
        writer.writerow(
            {
                "statute_id": "1962/184",
                "path": "section:17",
                "observed_symptom": "operative repeal text dropped",
                "suspected_first_bad_phase": "apply",
                "confirmed_first_bad_phase": "",
                "interaction_family": "",
                "secondary_phase": "",
                "source_pathology_present": "maybe",
                "oracle_or_editorial_witness_drift": "no",
                "source_lane_used": "preamble",
                "fix_owner": "",
                "regression_ids": "",
                "status": "",
                "notes": "",
            }
        )

    payload = validate_residual_ledger(path)
    assert payload["ok"] is False
    assert any("suspected_first_bad_phase" in error for error in payload["errors"])
    assert any("source_pathology_present" in error for error in payload["errors"])


def test_build_row_from_phase_witness_defaults_from_witness_target() -> None:
    witness = json.loads(
        json.dumps(
            {
                "statute_id": "1967/551",
                "source_id": "1967/551",
                "target_path": "section:3",
                "acquisition": {"source_lane_used": "sec1_fallback_post_routing"},
            }
        )
    )

    row = build_row_from_phase_witness(
        witness,
        observed_symptom="merged moments already in source witness",
        interaction_family="source_witness_with_merged_subsections",
        suspected_first_bad_phase="verification",
    )

    assert row["statute_id"] == "1967/551"
    assert row["path"] == "section:3"
    assert row["source_lane_used"] == "sec1_fallback_post_routing"
    assert row["interaction_family"] == "source_witness_with_merged_subsections"
    assert row["suspected_first_bad_phase"] == "verification"
    assert row["regression_ids"] == "1967/551"


def test_build_row_from_phase_witness_accepts_family_overrides() -> None:
    witness = json.loads(
        json.dumps(
            {
                "statute_id": "1967/550",
                "source_id": "2005/896",
                "target_path": "chapter:2/section:8",
                "acquisition": {"source_lane_used": "preamble"},
            }
        )
    )

    row = build_row_from_phase_witness(
        witness,
        observed_symptom="replay keeps the repealed subsection-1 family visible in chapter:2/section:8",
        interaction_family="sparse subsection / repeal visibility",
        suspected_first_bad_phase="replay_fold",
        confirmed_first_bad_phase="",
        secondary_phase="materialization",
        source_pathology_present="no",
        oracle_or_editorial_witness_drift="yes",
        fix_owner="finland",
        regression_ids="1967/550 §8",
        notes="proof artifact: notes/FINLAND_TRANCHE0_PHASE_WITNESS_NOTE_2026-04-18.md",
    )

    assert row["statute_id"] == "1967/550"
    assert row["path"] == "chapter:2/section:8"
    assert row["source_lane_used"] == "preamble"
    assert row["regression_ids"] == "1967/550 §8"
    assert row["fix_owner"] == "finland"
    assert row["secondary_phase"] == "materialization"
    assert row["source_pathology_present"] == "no"
    assert row["oracle_or_editorial_witness_drift"] == "yes"


def test_validate_residual_ledger_accepts_tranche0_example() -> None:
    path = Path(__file__).resolve().parent / "fixtures" / "residual_ledger_tranche0_example.csv"
    payload = validate_residual_ledger(path)
    assert payload["ok"] is True
    assert payload["rows_checked"] == 1
    assert payload["errors"] == []
