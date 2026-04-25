from __future__ import annotations

import csv
import re
from pathlib import Path

from scripts.build_replay_debt_inventory import (
    build_parser,
    build_summary,
    count_source_rows,
    family_label,
    load_inventory_rows,
    main,
)


def _write_results_csv(path, rows: list[dict[str, str]]) -> None:
    headers = [
        "statute_id",
        "section",
        "diagnosis",
        "blame_source",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def test_family_label_categorizes_sections() -> None:
    assert family_label("81") == "numeric"
    assert family_label("117b") == "alpha-suffix"
    assert family_label("7a-8") == "section-range"
    assert family_label("7a–8") == "section-range"
    assert family_label("  12c  ") == "alpha-suffix"
    assert family_label("x") == "other"
    assert family_label("") == "other"


def test_load_inventory_rows_filters_and_tags_family_labels(tmp_path) -> None:
    path = tmp_path / "oracle_check_results.csv"
    _write_results_csv(
        path,
        [
            {"statute_id": "2017/320", "section": "81", "diagnosis": "REPLAY_MISSING", "blame_source": "2024/315"},
            {"statute_id": "2017/320", "section": "222a", "diagnosis": "MISSING", "blame_source": ""},
            {"statute_id": "1984/719", "section": "7a-8", "diagnosis": "ORDER", "blame_source": "2020/12"},
        ],
    )

    rows = load_inventory_rows(path)
    assert [row["diagnosis"] for row in rows] == ["REPLAY_MISSING", "MISSING"]
    assert rows[0]["family_label"] == "numeric"
    assert rows[1]["family_label"] == "alpha-suffix"


def test_count_source_rows_counts_csv_rows(tmp_path) -> None:
    path = tmp_path / "oracle_check_results.csv"
    _write_results_csv(
        path,
        [
            {"statute_id": "2017/320", "section": "81", "diagnosis": "REPLAY_MISSING", "blame_source": "2024/315"},
            {"statute_id": "2017/320", "section": "83", "diagnosis": "PASS", "blame_source": ""},
            {"statute_id": "1984/719", "section": "50", "diagnosis": "UNKNOWN", "blame_source": "1994/107"},
        ],
    )

    assert count_source_rows(path) == 3


def test_build_parser_accepts_inventory_flags() -> None:
    parser = build_parser()
    args = parser.parse_args([])
    assert args.format == "json"
    assert args.top_statutes == 25
    assert args.per_statute == 5
    assert args.input == "oracle_check_results.csv"

    args = parser.parse_args(
        [
            "--input",
            "oracle_check_results.csv",
            "--format",
            "markdown",
            "--top-statutes",
            "11",
            "--per-statute",
            "3",
            "--output",
            "/tmp/replay-debt.md",
        ]
    )
    assert args.format == "markdown"
    assert args.top_statutes == 11
    assert args.per_statute == 3
    assert args.output == "/tmp/replay-debt.md"


def test_build_summary_emits_expected_bounded_shape(tmp_path) -> None:
    rows = [
        {
            "statute_id": "2017/320",
            "section": "81",
            "diagnosis": "REPLAY_MISSING",
            "blame_source": "2024/315",
            "family_label": "numeric",
        },
        {
            "statute_id": "2017/320",
            "section": "105",
            "diagnosis": "REPLAY_EXTRA",
            "blame_source": "",
            "family_label": "numeric",
        },
        {
            "statute_id": "1984/719",
            "section": "50",
            "diagnosis": "MISSING",
            "blame_source": "1994/107",
            "family_label": "numeric",
        },
    ]
    summary = build_summary(rows, source_row_count=10, top_statutes=1, head_per_statute=1)

    assert summary["source_rows"] == 10
    assert summary["replay_tail_rows"] == 3
    assert summary["known_blame_source"] == 2
    assert summary["known_blame_source_rate"] == 2 / 3
    assert summary["diagnosis_counts"] == {"REPLAY_MISSING": 1, "REPLAY_EXTRA": 1, "MISSING": 1}
    assert summary["family_counts"] == {"numeric": 3}
    assert summary["top_statutes"] == [{"statute_id": "2017/320", "rows": 2}]
    assert len(summary["bounded_inventory"]) == 1
    assert summary["bounded_inventory"][0]["statute_id"] == "2017/320"
    assert summary["bounded_inventory"][0]["first_bad_amendment"] == "2024/315"


def test_main_json_output_to_stdout(tmp_path, capsys) -> None:
    path = tmp_path / "oracle_check_results.csv"
    _write_results_csv(
        path,
        [
            {"statute_id": "2017/320", "section": "81", "diagnosis": "REPLAY_MISSING", "blame_source": "2024/315"},
            {"statute_id": "1984/719", "section": "89b", "diagnosis": "REPLAY_EXTRA", "blame_source": ""},
            {"statute_id": "2016/549", "section": "7a", "diagnosis": "MISSING", "blame_source": "2025/791"},
            {"statute_id": "1901/15-001", "section": "13", "diagnosis": "PASS", "blame_source": ""},
        ]
    )

    main(["--input", str(path)])
    output = capsys.readouterr().out
    assert '"generated_with": "scripts/build_replay_debt_inventory.py"' in output
    assert '"replay_tail_rows": 3' in output


def test_main_markdown_output_to_file(tmp_path) -> None:
    path = tmp_path / "oracle_check_results.csv"
    _write_results_csv(
        path,
        [
            {"statute_id": "2017/320", "section": "81", "diagnosis": "REPLAY_MISSING", "blame_source": "2024/315"},
            {"statute_id": "1984/719", "section": "89b", "diagnosis": "MISSING", "blame_source": ""},
            {"statute_id": "2016/549", "section": "7a", "diagnosis": "UNKNOWN", "blame_source": "2025/791"},
            {"statute_id": "1901/15-001", "section": "13", "diagnosis": "PASS", "blame_source": ""},
        ]
    )
    output_path = path.parent / "replay_debt_inventory.md"

    main(["--input", str(path), "--format", "markdown", "--top-statutes", "2", "--output", str(output_path)])
    rendered = output_path.read_text(encoding="utf-8")
    assert "# Replay Debt Reduction Inventory (Generated)" in rendered
    assert "Top 2 statutes by failing rows" in rendered
    assert "| metric | value |" in rendered


def test_replay_debt_inventory_note_has_timestamp_and_metric_headers(capsys) -> None:
    path = Path(__file__).resolve().parent / "fixtures" / "oracle_check_results_replay_debt.csv"
    main(
        [
            "--input",
            str(path),
            "--format",
            "markdown",
            "--top-statutes",
            "2",
        ]
    )
    text = capsys.readouterr().out

    assert text.startswith("# Replay Debt Reduction Inventory (Generated)")
    assert re.search(r"^> generated_at: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$", text, re.M)
    assert "| rows in source file |" in text
    assert "| replay-tail rows |" in text
    assert "| dominant failure classes |" in text
