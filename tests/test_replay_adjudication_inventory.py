from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.inventory_replay_adjudications import (
    build_parser,
    build_inventory,
    build_surface_comparison,
    collect_adjudication_kinds,
)


def _write_fixture(path: Path) -> None:
    path.write_text(
        """
def _append_replay_adjudication(*, kind: str, sink):
    sink.append(kind)


def direct_call(sink):
    sink.append(
        CompileAdjudication(
            kind="first_replay_signal",
            source_statute="1/1",
            source_amendment="1/2",
            op_id="op-1",
            detail={},
        )
    )
    _append_replay_adjudication(kind="wrapped_signal", sink=sink)
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _write_fixture_with_repeats(path: Path, direct_repeats: int = 1, wrapped_repeats: int = 0) -> None:
    lines = [
        "def _append_replay_adjudication(*, kind: str, sink):",
        "    sink.append(kind)",
        "",
        "def direct_call(sink):",
    ]

    for _ in range(direct_repeats):
        lines.extend(
            [
                "    sink.append(",
                "        CompileAdjudication(",
                '            kind="first_replay_signal",',
                '            source_statute="1/1",',
                '            source_amendment="1/2",',
                '            op_id="op-1",',
                "            detail={},",
                "        )",
                "    )",
            ]
        )

    for _ in range(wrapped_repeats):
        lines.extend(
            [
                "    _append_replay_adjudication(kind=\"wrapped_signal\", sink=sink)",
            ]
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_replay_family_fixture(path: Path) -> None:
    path.write_text(
        """
def _append_replay_adjudication(*, kind: str, sink):
    sink.append(kind)


def emit_signals(sink):
    sink.append(
        CompileAdjudication(
            kind="semantic_non_replay_signal",
            source_statute="1/1",
            source_amendment="1/2",
            op_id="op-1",
            detail={},
        )
    )
    _append_replay_adjudication(kind="replay_noop", sink=sink)
    _append_replay_adjudication(kind="eu_replay_target_not_found", sink=sink)
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _write_direct_and_wrapped_replay_family_fixture(path: Path) -> None:
    path.write_text(
        """
def _append_replay_adjudication(*, kind: str, sink):
    sink.append(kind)


def emit_signals(sink):
    sink.append(
        CompileAdjudication(
            kind="replay_direct_hit",
            source_statute="1/1",
            source_amendment="1/2",
            op_id="op-1",
            detail={},
        )
    )
    _append_replay_adjudication(kind="eu_replay_target_not_found", sink=sink)
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _write_replay_family_fixture_with_repeats(
    path: Path,
    *,
    plain_replay_repeats: int = 0,
    prefixed_replay_repeats: int = 0,
    non_replay_repeats: int = 0,
) -> None:
    lines = [
        "def _append_replay_adjudication(*, kind: str, sink):",
        "    sink.append(kind)",
        "",
        "def emit_signals(sink):",
    ]

    for _ in range(non_replay_repeats):
        lines.extend(
            [
                "    sink.append(",
                "        CompileAdjudication(",
                '            kind="semantic_non_replay_signal",',
                '            source_statute="1/1",',
                '            source_amendment="1/2",',
                '            op_id="op-1",',
                "            detail={},",
                "        )",
                "    )",
            ]
        )

    for _ in range(plain_replay_repeats):
        lines.append('    _append_replay_adjudication(kind="replay_noop", sink=sink)')

    for _ in range(prefixed_replay_repeats):
        lines.append('    _append_replay_adjudication(kind="eu_replay_target_not_found", sink=sink)')

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_collect_adjudication_kinds_requires_compile_adjudication_default(tmp_path: Path) -> None:
    path = tmp_path / "src.py"
    _write_fixture(path)

    result = collect_adjudication_kinds(path)

    assert "first_replay_signal" in result
    assert "wrapped_signal" not in result
    assert result["first_replay_signal"][0].line == 7
    assert result["first_replay_signal"][0].function == "direct_call"


def test_collect_adjudication_kinds_includes_wrappers_when_requested(tmp_path: Path) -> None:
    path = tmp_path / "src.py"
    _write_fixture(path)

    result = collect_adjudication_kinds(path, include_wrappers=True)

    assert "wrapped_signal" in result
    assert result["wrapped_signal"][0].line == 15


def test_build_inventory_formats_by_jurisdiction(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    _write_fixture(first)
    _write_fixture(second)
    files = {"A": "a.py", "B": "b.py"}

    inventory = build_inventory(
        files=files,
        root=tmp_path,
        include_wrappers=True,
    )

    assert set(inventory["A"].keys()) == {"first_replay_signal", "wrapped_signal"}
    assert set(inventory["B"].keys()) == {"first_replay_signal", "wrapped_signal"}
    assert inventory["A"]["first_replay_signal"][0].function == "direct_call"


def test_direct_only_filter_stays_direct_when_requested(tmp_path: Path) -> None:
    first = tmp_path / "a.py"
    _write_fixture(first)
    files = {"A": "a.py"}

    inventory = build_inventory(
        files=files,
        root=tmp_path,
        include_wrappers=False,
    )

    assert set(inventory["A"].keys()) == {"first_replay_signal"}


def test_build_inventory_direct_only_respects_jurisdiction_and_replay_only(tmp_path: Path) -> None:
    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    _write_replay_family_fixture(first)
    _write_direct_and_wrapped_replay_family_fixture(second)

    inventory = build_inventory(
        files={"EU": "eu.py", "UK": "uk.py"},
        root=tmp_path,
        jurisdictions={"UK"},
        include_wrappers=False,
        replay_only=True,
    )

    assert set(inventory) == {"UK"}
    assert set(inventory["UK"]) == {"replay_direct_hit"}


def test_parser_flags_compatible_with_script_mode() -> None:
    parser = build_parser()
    args = parser.parse_args([])

    assert not args.all_kinds
    assert not args.direct_only
    assert not args.replay_only
    assert not args.compare_direct
    assert args.min_count is None


def test_parser_rejects_non_positive_min_count() -> None:
    parser = build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--min-count", "0"])
    with pytest.raises(SystemExit):
        parser.parse_args(["--min-count", "-1"])


def test_main_markdown_output(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture(first)
    second = tmp_path / "b.py"
    _write_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory_replay_adjudications.main(
        ["--format", "markdown", "--direct-only", "--root", str(tmp_path)]
    )

    output = capsys.readouterr().out
    assert "# Cross-Jurisdiction Adjudication Kind Inventory" in output
    assert "## A" in output
    assert "| kind | count | sample_lines |" in output


def test_main_markdown_output_to_file(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture(first)
    second = tmp_path / "b.py"
    _write_fixture(second)
    output = tmp_path / "adjudications.md"
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "markdown",
            "--output",
            str(output),
            "--direct-only",
            "--root",
            str(tmp_path),
        ]
    )

    output_text = output.read_text(encoding="utf-8")
    assert "# Cross-Jurisdiction Adjudication Kind Inventory" in output_text
    assert "## A" in output_text
    assert "| kind | count | sample_lines |" in output_text


def test_main_supports_kind_filter_filtering(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    _write_fixture(first)
    _write_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory_replay_adjudications.main(
        ["--format", "json", "--root", str(tmp_path), "--kind-filter", "wrapped"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert set(payload["inventories"]["A"]) == {"wrapped_signal"}
    assert set(payload["inventories"]["B"]) == {"wrapped_signal"}


def test_build_inventory_replay_only_matches_plain_and_prefixed_replay_kinds(tmp_path: Path) -> None:
    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)

    inventory = build_inventory(
        files={"A": "a.py"},
        root=tmp_path,
        include_wrappers=True,
        replay_only=True,
    )

    assert set(inventory["A"]) == {"replay_noop", "eu_replay_target_not_found"}


def test_main_replay_only_filters_out_non_replay_kinds(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(fixture)},
    )

    inventory_replay_adjudications.main(["--format", "json", "--root", str(tmp_path), "--replay-only"])
    payload = json.loads(capsys.readouterr().out)

    assert set(payload["inventories"]["A"]) == {"replay_noop", "eu_replay_target_not_found"}


def test_build_surface_comparison_distinguishes_wrapper_only_kinds(tmp_path: Path) -> None:
    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)

    comparison = build_surface_comparison(
        files={"A": "a.py"},
        root=tmp_path,
        replay_only=True,
    )

    assert comparison["A"]["wrapper_kind_count"] == 2
    assert comparison["A"]["direct_kind_count"] == 0
    assert comparison["A"]["wrapper_only_kind_count"] == 2
    assert comparison["A"]["wrapper_only_kinds"] == ["eu_replay_target_not_found", "replay_noop"]


def test_main_compare_direct_json_output(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(fixture)},
    )

    inventory_replay_adjudications.main(
        ["--format", "json", "--root", str(tmp_path), "--replay-only", "--compare-direct"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["summary"]["jurisdiction_count"] == 1
    assert payload["comparison"]["A"]["wrapper_only_kind_count"] == 2
    assert payload["comparison"]["A"]["direct_kind_count"] == 0


def test_main_compare_direct_json_output_to_file(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    fixture = tmp_path / "a.py"
    output = tmp_path / "comparison.json"
    _write_replay_family_fixture(fixture)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(fixture)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "json",
            "--root",
            str(tmp_path),
            "--replay-only",
            "--compare-direct",
            "--output",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert payload["summary"]["jurisdiction_count"] == 1
    assert payload["comparison"]["A"]["wrapper_only_kinds"] == [
        "eu_replay_target_not_found",
        "replay_noop",
    ]


def test_main_compare_direct_markdown_output(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(fixture)},
    )

    inventory_replay_adjudications.main(
        ["--format", "markdown", "--root", str(tmp_path), "--replay-only", "--compare-direct"]
    )
    output = capsys.readouterr().out

    assert "# Cross-Jurisdiction Adjudication Surface Comparison" in output
    assert "| jurisdiction | wrapper_kinds | wrapper_adjudications | direct_kinds |" in output
    assert "## A" in output
    assert "| replay_noop |" in output


def test_main_direct_only_respects_jurisdiction_and_replay_only(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    _write_replay_family_fixture(first)
    _write_direct_and_wrapped_replay_family_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"EU": str(first), "UK": str(second)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "markdown",
            "--root",
            str(tmp_path),
            "--jurisdiction",
            "UK",
            "--replay-only",
            "--direct-only",
        ]
    )
    output = capsys.readouterr().out

    assert "## EU" not in output
    assert "## UK" in output
    assert "| replay_direct_hit | 1 |" in output
    assert "eu_replay_target_not_found" not in output


def test_main_direct_only_json_output_to_file_with_filters(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    output = tmp_path / "direct.json"
    _write_replay_family_fixture(first)
    _write_direct_and_wrapped_replay_family_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"EU": str(first), "UK": str(second)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "json",
            "--root",
            str(tmp_path),
            "--jurisdiction",
            "UK",
            "--replay-only",
            "--direct-only",
            "--output",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert set(payload["inventories"]) == {"UK"}
    assert set(payload["inventories"]["UK"]) == {"replay_direct_hit"}


def test_build_surface_comparison_respects_jurisdiction_and_min_count(tmp_path: Path) -> None:
    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    _write_replay_family_fixture_with_repeats(first, prefixed_replay_repeats=1)
    _write_replay_family_fixture_with_repeats(second, plain_replay_repeats=2, prefixed_replay_repeats=1)

    comparison = build_surface_comparison(
        files={"EU": "eu.py", "UK": "uk.py"},
        root=tmp_path,
        jurisdictions={"UK"},
        replay_only=True,
        min_count=2,
    )

    assert set(comparison) == {"UK"}
    assert comparison["UK"]["wrapper_kind_count"] == 1
    assert comparison["UK"]["wrapper_adjudication_count"] == 2
    assert comparison["UK"]["wrapper_only_kinds"] == ["replay_noop"]


def test_main_min_count_json_output_to_file_with_filters(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    output = tmp_path / "min-count.json"
    _write_replay_family_fixture_with_repeats(first, prefixed_replay_repeats=1)
    _write_replay_family_fixture_with_repeats(second, plain_replay_repeats=2, prefixed_replay_repeats=1)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"EU": str(first), "UK": str(second)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "json",
            "--root",
            str(tmp_path),
            "--jurisdiction",
            "UK",
            "--replay-only",
            "--min-count",
            "2",
            "--output",
            str(output),
        ]
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert set(payload["inventories"]) == {"UK"}
    assert set(payload["inventories"]["UK"]) == {"replay_noop"}
    assert payload["summary"]["kind_count"] == 1


def test_main_compare_direct_respects_jurisdiction_and_min_count(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "eu.py"
    second = tmp_path / "uk.py"
    _write_replay_family_fixture_with_repeats(first, prefixed_replay_repeats=1)
    _write_replay_family_fixture_with_repeats(second, plain_replay_repeats=2, prefixed_replay_repeats=1)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"EU": str(first), "UK": str(second)},
    )

    inventory_replay_adjudications.main(
        [
            "--format",
            "json",
            "--root",
            str(tmp_path),
            "--jurisdiction",
            "UK",
            "--replay-only",
            "--min-count",
            "2",
            "--compare-direct",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert set(payload["comparison"]) == {"UK"}
    assert payload["comparison"]["UK"]["wrapper_kind_count"] == 1
    assert payload["comparison"]["UK"]["wrapper_only_kinds"] == ["replay_noop"]
    assert payload["summary"]["jurisdiction_count"] == 1


def test_main_rejects_compare_direct_with_direct_only(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    fixture = tmp_path / "a.py"
    _write_replay_family_fixture(fixture)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(fixture)},
    )

    with pytest.raises(SystemExit) as exc:
        inventory_replay_adjudications.main(
            ["--root", str(tmp_path), "--compare-direct", "--direct-only"]
        )
    assert "--compare-direct cannot be combined" in str(exc.value)


def test_main_rejects_unknown_jurisdiction(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture(first)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first)},
    )

    with pytest.raises(SystemExit) as exc:
        inventory_replay_adjudications.main(["--jurisdiction", "ZZ", "--root", str(tmp_path)])
    assert "Unknown jurisdictions: ZZ" in str(exc.value)
    assert "Supported jurisdictions: A" in str(exc.value)
    # argparse/SystemExit is the expected failure mode when an unsupported
    # jurisdiction is passed in through CLI filters.


def test_main_json_output_to_stdout_with_default_paths(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture(first)
    second = tmp_path / "b.py"
    _write_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory_replay_adjudications.main(["--root", str(tmp_path)])
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["generated_with"] == "scripts/inventory_replay_adjudications.py"
    assert "generated_at" in payload
    assert "inventories" in payload
    assert "A" in payload["inventories"]
    assert "first_replay_signal" in payload["inventories"]["A"]
    assert "summary" in payload


def test_build_inventory_min_count_filter(tmp_path: Path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    _write_fixture_with_repeats(first, direct_repeats=2)
    _write_fixture_with_repeats(second, direct_repeats=1, wrapped_repeats=1)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory = inventory_replay_adjudications.build_inventory(
        files=inventory_replay_adjudications.REPLAY_SOURCE_FILES,
        root=tmp_path,
        include_wrappers=True,
        min_count=2,
    )

    assert set(inventory["A"].keys()) == {"first_replay_signal"}
    assert len(inventory["A"]["first_replay_signal"]) == 2
    assert set(inventory["B"].keys()) == set()


def test_build_inventory_rejects_non_positive_min_count(tmp_path: Path) -> None:
    from scripts import inventory_replay_adjudications

    with pytest.raises(ValueError):
        inventory_replay_adjudications.build_inventory(
            files={},
            root=tmp_path,
            min_count=0,
        )

    with pytest.raises(ValueError):
        inventory_replay_adjudications.build_inventory(
            files={},
            root=tmp_path,
            min_count=-1,
        )


def test_main_supports_min_count_filter(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture_with_repeats(first, direct_repeats=3, wrapped_repeats=1)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first)},
    )

    inventory_replay_adjudications.main(
        ["--format", "json", "--root", str(tmp_path), "--min-count", "2"]
    )
    output = json.loads(capsys.readouterr().out)

    assert set(output["inventories"]["A"]) == {"first_replay_signal"}
    assert output["summary"]["kind_count"] == 1


def test_main_rejects_non_positive_min_count(tmp_path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    _write_fixture(first)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first)},
    )

    with pytest.raises(SystemExit):
        inventory_replay_adjudications.main(
            ["--root", str(tmp_path), "--min-count", "0"]
        )
    with pytest.raises(SystemExit):
        inventory_replay_adjudications.main(
            ["--root", str(tmp_path), "--min-count", "-1"]
        )


def test_build_inventory_respects_jurisdiction_filter(tmp_path: Path, monkeypatch) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    _write_fixture(first)
    _write_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory = inventory_replay_adjudications.build_inventory(
        files=inventory_replay_adjudications.REPLAY_SOURCE_FILES,
        root=tmp_path,
        jurisdictions={"B"},
    )

    assert set(inventory) == {"B"}
    assert set(inventory["B"].keys()) == {"first_replay_signal", "wrapped_signal"}


def test_main_supports_jurisdiction_filter(tmp_path, monkeypatch, capsys) -> None:
    from scripts import inventory_replay_adjudications

    first = tmp_path / "a.py"
    second = tmp_path / "b.py"
    _write_fixture(first)
    _write_fixture(second)
    monkeypatch.setattr(
        inventory_replay_adjudications,
        "REPLAY_SOURCE_FILES",
        {"A": str(first), "B": str(second)},
    )

    inventory_replay_adjudications.main(
        ["--format", "markdown", "--root", str(tmp_path), "--jurisdiction", "B"]
    )

    output = capsys.readouterr().out
    assert "## A" not in output
    assert "## B" in output
