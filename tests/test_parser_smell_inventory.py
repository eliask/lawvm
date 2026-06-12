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
    assert inventory["summary"]["category_count"] == 7
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
    assert inventory["category_counts"]["bounded_wildcard_gap"] == 0
    assert inventory["category_counts"]["fallback_heuristics"] == 0
    assert inventory["category_counts"]["regex_structural_heuristic"] == 0
    assert inventory["category_counts"]["regex_coverage_surface"] == 0
    assert inventory["category_counts"]["row_target_normalization"] == 0
    assert inventory["category_counts"]["text_selector_sentinel"] == 0


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
    assert inventory["category_counts"]["bounded_wildcard_gap"] == 0
    assert inventory["category_counts"]["regex_coverage_surface"] == 0
    assert inventory["category_counts"]["text_selector_sentinel"] == 0


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
        if line.startswith("## ") or line.startswith("| Bounded Wildcard Coverage Status |")
    )
    category_rows = [
        line
        for line in lines[detail_rows_start:detail_rows_end]
        if line.startswith("| ") and line != "| --- | ---: |"
    ]

    categories = [row.split("|")[1].strip() for row in category_rows if row]
    assert categories == sorted(categories)
    assert categories == [
        "bounded_wildcard_gap",
        "clause_modifier_filter",
        "fallback_heuristics",
        "regex_coverage_surface",
        "regex_structural_heuristic",
        "row_target_normalization",
        "text_selector_sentinel",
    ]


def test_build_inventory_reports_bounded_gap_coverage_and_text_sentinel(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "from lawvm.core.regex_recognition_coverage import RegexRecognitionCoverage",
                "RX = re.compile(r'for .{0,240}? substitute')",
                "selector = 'TEXT_FROM_X_TO_END'",
                "coverage_status = 'unclassified_gap'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path])

    assert inventory["category_counts"]["bounded_wildcard_gap"] == 1
    assert inventory["category_counts"]["regex_coverage_surface"] == 2
    assert inventory["category_counts"]["text_selector_sentinel"] == 1
    assert inventory["summary"]["bounded_wildcard_coverage_status_counts"] == {
        "coverage_function_reference": 0,
        "file_level_coverage_surface": 0,
        "missing_coverage_surface": 0,
        "nearby_coverage_surface": 1,
    }
    assert inventory["summary"]["bounded_wildcard_semantic_role_counts"] == {
        "drafting_classifier": 1,
        "semantic_payload_capture": 0,
        "unknown_pattern_bound": 0,
    }
    assert inventory["summary"]["bounded_wildcard_soundness_risk_counts"] == {
        "covered_by_regex_coverage_surface": 1,
        "needs_classifier_safety_review": 0,
        "needs_triage": 0,
        "needs_typed_coverage_or_grammar": 0,
    }
    assert "not semantic exhaustiveness proofs" in inventory["summary"][
        "bounded_wildcard_soundness_note"
    ]


def test_markdown_reports_bounded_wildcard_soundness_caveat(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "def parse_fragment_substitution(text):",
                "    RX = re.compile(r'insert (?P<inserted>.{0,240}?) substitute')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    markdown = _to_markdown(build_inventory([path]))

    assert "Bounded wildcard note" in markdown
    assert "Bounded Wildcard Soundness Risk" in markdown
    assert "not a semantic exhaustiveness proof" in markdown
    assert "function=parse_fragment_substitution" in markdown
    assert "family=unclassified_semantic_payload_instruction" in markdown


def test_bounded_wildcard_sensor_reports_missing_coverage_surface(tmp_path) -> None:
    path = tmp_path / "normalize.py"
    path.write_text(
        "\n".join(
            [
                "RX = re.compile(",
                "    r'foo .{0,240}? bar'",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path])

    hit = inventory["by_file"][str(path)][0]
    assert hit["category"] == "bounded_wildcard_gap"
    assert hit["coverage_sensor"] == {
        "status": "missing_coverage_surface",
        "recognizer_name": "RX",
        "owner_symbol": "RX",
        "owner_function": "",
        "semantic_role": "unknown_pattern_bound",
        "soundness_risk": "needs_triage",
        "grammar_family": "lexical_or_classifier",
        "nearest_coverage_line": None,
        "nearest_coverage_distance": None,
        "nearby_line_window": 80,
    }
    assert inventory["summary"]["bounded_wildcard_coverage_status_counts"][
        "missing_coverage_surface"
    ] == 1


def test_bounded_wildcard_sensor_detects_coverage_function_reference(tmp_path) -> None:
    path = tmp_path / "normalize.py"
    path.write_text(
        "\n".join(
            [
                "RX = re.compile(",
                "    r'foo .{0,240}? bar'",
                ")",
                "",
                "def _extract_with_coverage(text):",
                "    coverage_rows = []",
                "    for match in RX.finditer(text):",
                "        coverage_rows.append(match)",
                "    return coverage_rows",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path])

    bounded_hits = [
        hit
        for hit in inventory["by_file"][str(path)]
        if hit["category"] == "bounded_wildcard_gap"
    ]
    assert len(bounded_hits) == 1
    assert bounded_hits[0]["coverage_sensor"]["status"] == "coverage_function_reference"
    assert bounded_hits[0]["coverage_sensor"]["recognizer_name"] == "RX"
    assert bounded_hits[0]["coverage_sensor"]["owner_symbol"] == "RX"
    assert bounded_hits[0]["coverage_sensor"]["owner_function"] == ""
    assert bounded_hits[0]["coverage_sensor"]["semantic_role"] == "unknown_pattern_bound"
    assert bounded_hits[0]["coverage_sensor"]["soundness_risk"] == (
        "covered_by_regex_coverage_surface"
    )
    assert inventory["summary"]["bounded_wildcard_coverage_status_counts"][
        "coverage_function_reference"
    ] == 1


def test_bounded_wildcard_sensor_prioritizes_payload_capture_without_coverage(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "RX = re.compile(",
                "    r'insert (?P<payload>.{0,2000})$'",
                ")",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path])

    hit = inventory["by_file"][str(path)][0]
    assert hit["coverage_sensor"]["owner_symbol"] == "RX"
    assert hit["coverage_sensor"]["grammar_family"] == (
        "unclassified_semantic_payload_instruction"
    )
    assert hit["coverage_sensor"]["semantic_role"] == "semantic_payload_capture"
    assert hit["coverage_sensor"]["soundness_risk"] == (
        "needs_typed_coverage_or_grammar"
    )
    assert inventory["summary"]["bounded_wildcard_soundness_risk_counts"][
        "needs_typed_coverage_or_grammar"
    ] == 1


def test_bounded_wildcard_sensor_uses_coverage_lines_under_category_filter(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "RX = re.compile(r'foo .{0,240}? bar')",
                "coverage_status = 'unclassified_gap'",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path], categories={"bounded_wildcard_gap"})

    hit = inventory["by_file"][str(path)][0]
    assert hit["category"] == "bounded_wildcard_gap"
    assert hit["coverage_sensor"]["status"] == "nearby_coverage_surface"
    assert inventory["category_counts"] == {"bounded_wildcard_gap": 1}


def test_bounded_wildcard_sensor_ignores_comment_only_mentions(tmp_path) -> None:
    path = tmp_path / "effect_lowering_tail.py"
    path.write_text(
        "\n".join(
            [
                "# Bound each segment to .{0,400}? for backtracking safety.",
                "RX = re.compile(r'foo .{0,240}? bar')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path], categories={"bounded_wildcard_gap"})

    assert inventory["category_counts"] == {"bounded_wildcard_gap": 1}
    assert inventory["by_file"][str(path)][0]["line"] == 2


def test_bounded_wildcard_sensor_classifies_semantic_payload_captures(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "RX = re.compile(r'insert (?P<inserted>.{1,1200}?)(?:\\\\s+\\\\.)?$')",
                "CLASSIFIER = re.compile(r'\\\\bomit\\\\b.{0,240}?\\\\bentries\\\\b')",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path], categories={"bounded_wildcard_gap"})
    roles = [
        hit["coverage_sensor"]["semantic_role"]
        for hit in inventory["by_file"][str(path)]
    ]

    assert roles == ["semantic_payload_capture", "drafting_classifier"]
    assert inventory["summary"]["bounded_wildcard_semantic_role_counts"] == {
        "drafting_classifier": 1,
        "semantic_payload_capture": 1,
        "unknown_pattern_bound": 0,
    }


def test_bounded_wildcard_sensor_reports_local_owner_and_grammar_family(tmp_path) -> None:
    path = tmp_path / "nlp_parser.py"
    path.write_text(
        "\n".join(
            [
                "def parse_fragment_substitution(text):",
                "    matches_definition_at_end_insert = re.finditer(",
                "        r'at the end of the definition (?P<inserted>.{1,1200}?)$',",
                "        text,",
                "    )",
                "",
            ]
        ),
        encoding="utf-8",
    )

    inventory = build_inventory([path], categories={"bounded_wildcard_gap"})

    [hit] = inventory["by_file"][str(path)]
    sensor = hit["coverage_sensor"]
    assert sensor["owner_symbol"] == "matches_definition_at_end_insert"
    assert sensor["recognizer_name"] == "matches_definition_at_end_insert"
    assert sensor["owner_function"] == "parse_fragment_substitution"
    assert sensor["grammar_family"] == (
        "definition_entry_or_definition_body_instruction"
    )
    assert sensor["soundness_risk"] == "needs_typed_coverage_or_grammar"
