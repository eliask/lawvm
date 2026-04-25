from __future__ import annotations

from pathlib import Path

import json
import pytest

from scripts.inventory_parser_smells import _to_markdown, build_inventory
from scripts.inventory_parser_smells import build_parser as build_smells_parser


def _write_sample_parser_file(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "def apply_statute(payload):",
                "    if _sec1_fallback_peg_skip_required(payload):",
                "        return parse_ops_fallback_heuristic(payload)",
                "    if re.search(r'ARTICLE', payload):",
                "        return clause_modifier_blacklist",
                "    return allows_omission_expansion(payload)",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_build_inventory_reports_heavy_smells(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    inventory = build_inventory([path])

    assert inventory["hit_count"] == 7
    assert inventory["file_counts"][str(path)] == 7
    assert inventory["summary"]["category_count"] == 4
    assert inventory["category_counts"]["fallback_heuristics"] == 2
    assert inventory["category_counts"]["row_target_normalization"] == 3


def test_build_inventory_filters_by_category(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    inventory = build_inventory([path], categories={"fallback_heuristics"})

    assert inventory["summary"]["filtered_category_count"] == 1
    assert inventory["summary"]["hit_count"] == 2
    assert inventory["hit_count"] == 2
    assert list(inventory["category_counts"]) == ["fallback_heuristics"]
    assert inventory["category_counts"]["fallback_heuristics"] == 2


def test_build_inventory_filters_by_marker(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    inventory = build_inventory([path], marker_filter="black")

    assert inventory["summary"]["hit_count"] == 1
    assert inventory["hit_count"] == 1
    assert inventory["category_counts"]["clause_modifier_filter"] == 1
    assert inventory["category_counts"]["fallback_heuristics"] == 0
    assert inventory["category_counts"]["regex_structural_heuristic"] == 0
    assert inventory["category_counts"]["row_target_normalization"] == 0


def test_build_inventory_keeps_zero_hit_categories_in_summary(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    path.write_text(
        "\n".join(
            [
                "def apply_statute(payload):",
                "    return re.search(r'ARTICLE', payload)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path])

    assert inventory["hit_count"] == 1
    assert inventory["category_counts"]["regex_structural_heuristic"] == 1
    assert inventory["category_counts"]["clause_modifier_filter"] == 0
    assert inventory["category_counts"]["fallback_heuristics"] == 0
    assert inventory["category_counts"]["row_target_normalization"] == 0


def test_to_markdown_includes_grouped_hit_rows(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    markdown = _to_markdown(build_inventory([path]))

    assert "# Parser Smell Inventory (Generated)" in markdown
    assert "> generated_at: " in markdown
    assert f"## {path}" in markdown
    assert "| Line | Category | Label | Snippet |" in markdown
    assert "| 2 | fallback_heuristics | Fallback-path handling | if _sec1_fallback_peg_skip_required(payload): |" in markdown


def test_main_supports_category_and_marker_filters(tmp_path, capsys) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    from scripts import inventory_parser_smells

    inventory_parser_smells.main(
        [
            "--format",
            "json",
            "--category",
            "row_target_normalization",
            "--marker",
            "fallback",
            str(path),
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["summary"]["filtered_category_count"] == 1
    assert payload["summary"]["hit_count"] == 3
    assert payload["category_counts"]["row_target_normalization"] == 3
    assert payload["category_counts"].keys() == {"row_target_normalization"}


def test_main_writes_markdown_when_requested(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)
    output = tmp_path / "out.md"

    parser = build_smells_parser()
    args = parser.parse_args(["--format", "markdown", "--output", str(output), str(path)])
    assert args.format == "markdown"
    assert args.output == str(output)

    from scripts import inventory_parser_smells

    inventory_parser_smells.main(["--format", "markdown", "--output", str(output), str(path)])

    text = output.read_text(encoding="utf-8")
    assert "# Parser Smell Inventory (Generated)" in text
    assert "| File | Hits |" in text
    assert str(path) in text


def test_main_prints_json_to_stdout_with_default_output(tmp_path, capsys) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    from scripts import inventory_parser_smells

    inventory_parser_smells.main([str(path)])
    captured = capsys.readouterr().out
    assert '"generated_with": "scripts/inventory_parser_smells.py"' in captured
    assert '"generated_at"' in captured
    assert '"summary"' in captured
    assert str(path) in captured


def test_main_rejects_unknown_category(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)
    from scripts import inventory_parser_smells

    with pytest.raises(SystemExit):
        inventory_parser_smells.main(["--category", "does_not_exist", str(path)])


def test_main_rejects_invalid_marker_regex(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)
    from scripts import inventory_parser_smells

    with pytest.raises(SystemExit):
        inventory_parser_smells.main(["--marker", "[bad", str(path)])


def test_category_totals_match_hit_count(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    inventory = build_inventory([path])

    assert sum(inventory["category_counts"].values()) == inventory["hit_count"]
    assert sum(inventory["file_counts"].values()) == inventory["hit_count"]


def test_category_rows_are_stable_and_sorted_in_markdown_snapshot(tmp_path) -> None:
    path = tmp_path / "grafter.py"
    _write_sample_parser_file(path)

    markdown = _to_markdown(build_inventory([path]))
    lines = markdown.splitlines()
    category_header_index = lines.index("| Category | Count |")
    detail_rows_start = category_header_index + 2
    detail_rows_end = next(
        idx
        for idx, line in enumerate(lines[detail_rows_start:], start=detail_rows_start)
        if line.startswith("## ")
    )
    category_rows = [
        line
        for line in lines[detail_rows_start:detail_rows_end]
        if line.startswith("| ") and line != "| --- | ---: |"
    ]

    categories = [row.split("|")[1].strip() for row in category_rows if row]
    assert categories == sorted(categories)
    assert categories == [
        "clause_modifier_filter",
        "fallback_heuristics",
        "regex_structural_heuristic",
        "row_target_normalization",
    ]
