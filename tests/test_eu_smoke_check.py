from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import importlib.util
import sys
import pytest


def _load_smoke_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "eu_replay_smoke_check.py"
    spec = importlib.util.spec_from_file_location("eu_replay_smoke_check", module_path)
    if spec is None or spec.loader is None:  # pragma: no cover
        raise RuntimeError("Unable to load eu_replay_smoke_check module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["eu_replay_smoke_check"] = module
    spec.loader.exec_module(module)
    return module


def test_parse_last_json_line_prefers_final_payload() -> None:
    smoke = _load_smoke_module()

    raw = "DEBUG: fetching\n{\"ok\": true}\n{not-json}\n{\"final\": 1}"
    assert smoke._parse_last_json_line(raw) == {"final": 1}


def test_validate_markdown_output_contract() -> None:
    smoke = _load_smoke_module()

    valid_markdown = "\n".join(
        [
            "# EU Replay Report",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "| --- | ---: |",
            "| CELEX | 32016R0679 |",
            "| Ops | 0 |",
            "| Adjudications | 0 |",
        ]
    )
    smoke._validate_markdown_output(valid_markdown)

    invalid_markdown = "\n".join(["No header", "## Summary", "| Metric | Value |"])
    with pytest.raises(AssertionError):
        smoke._validate_markdown_output(invalid_markdown)


def test_run_offline_smoke_supports_markdown_and_text_formats(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()
    calls: list[str] = []

    def _fake_capture(
        _celex: str,
        _cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        calls.append(output_format)
        if output_format == "json":
            return '{"celex":"32016R0679","ops":0,"adjudications":1,"adjudication_kinds":{"eu_replay_parent_not_found":1},"text_duplication_phases":[],"adjudications_data":[{"kind":"eu_replay_parent_not_found","message":"","source_statute":"2026/1","op_id":"op","detail":{}}]}'
        if output_format == "markdown":
            return "# EU Replay Report\n\n## Summary\n\n| Metric | Value |\n| --- | ---: |\n| CELEX | 32016R0679 |\n| Ops | 0 |\n| Adjudications | 1 |\n"
        if output_format == "text":
            return "EU Replay\nCELEX: 32016R0679\nOps: 0\nAdjudications: 1\nKinds:\n  eu_replay_parent_not_found: 1\n"
        raise AssertionError(f"unexpected format: {output_format}")

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)
    payload = smoke.run_offline_smoke("32016R0679", tmp_path, output_format="markdown")
    assert payload["ops"] == 0
    assert payload["adjudications"] == 1
    assert calls == ["json", "markdown"]

    payload = smoke.run_offline_smoke("32016R0679", tmp_path, output_format="text")
    assert payload["adjudications_data"][0]["kind"] == "eu_replay_parent_not_found"
    assert calls[-1] == "text"


def test_parse_expected_kinds() -> None:
    smoke = _load_smoke_module()

    assert smoke._parse_expected_kinds(["eu_replay_target_not_found=2", "eu_replay_parent_not_found=0"]) == {
        "eu_replay_target_not_found": 2,
        "eu_replay_parent_not_found": 0,
    }

    with pytest.raises(ValueError):
        smoke._parse_expected_kinds(["bad-format"])

    with pytest.raises(ValueError):
        smoke._parse_expected_kinds(["=2"])

    with pytest.raises(ValueError):
        smoke._parse_expected_kinds(["eu_replay_target_not_found=bad"])


def test_parse_expected_kinds_rejects_negative_count() -> None:
    smoke = _load_smoke_module()

    with pytest.raises(ValueError):
        smoke._parse_expected_kinds(["eu_replay_parent_not_found=-1"])


def test_parse_expected_kinds_tolerates_whitespace_around_count() -> None:
    smoke = _load_smoke_module()

    assert smoke._parse_expected_kinds(["eu_replay_target_not_found = 3 "]) == {
        "eu_replay_target_not_found": 3,
    }


def test_main_forwards_cache_dir_argument(monkeypatch, tmp_path) -> None:
    smoke = _load_smoke_module()
    captured: dict[str, object] = {}

    def _fake_capture(
        _celex: str,
        cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        captured["cache_dir"] = str(cache_dir)
        assert output_format == "json"
        assert pit_date is None
        return (
            "{\"celex\":\"32016R0679\",\"ops\":0,\"adjudications\":1,"
            "\"adjudication_kinds\":{\"eu_replay_parent_not_found\":1},"
            "\"text_duplication_phases\":[],"
            "\"adjudications_data\":[{\"kind\":\"eu_replay_parent_not_found\",\"message\":\"parent missing\","
            "\"source_statute\":\"2026/1\",\"op_id\":\"op\",\"detail\":{}}]}"
        )

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    custom_cache = tmp_path / "custom-smoke-cache"
    smoke.main([
        "--celex",
        "32016R0679",
        "--format",
        "json",
        "--cache-dir",
        str(custom_cache),
    ])

    assert captured["cache_dir"] == str(custom_cache)


@pytest.mark.parametrize(
    "output_format,expected_calls",
    [
        ("json", [("32016R0679", "json")]),
        ("markdown", [("32016R0679", "json"), ("32016R0679", "markdown")]),
        ("text", [("32016R0679", "json"), ("32016R0679", "text")]),
    ],
)
def test_main_forwards_expect_kind_cli_to_runner_for_all_formats(
    tmp_path, monkeypatch, output_format: str, expected_calls: list[tuple[str, str]]
) -> None:
    smoke = _load_smoke_module()

    fixture = {
        "json": (
            '{"celex":"32016R0679","ops":0,"adjudications":1,'
            '"adjudication_kinds":{"eu_replay_parent_not_found":1},'
            '"text_duplication_phases":[],'
            '"adjudications_data":[{"kind":"eu_replay_parent_not_found",'
            '"message":"parent missing","source_statute":"2026/1","op_id":"op",'
            '"detail":{"parent_kind":"section"}}]}'
        ),
        "markdown": "# EU Replay Report\n\n## Summary\n\n| Metric | Value |\n| --- | ---: |\n| CELEX | 32016R0679 |\n| Ops | 0 |\n| Adjudications | 1 |\n",
        "text": "EU Replay\nCELEX: 32016R0679\nOps: 0\nAdjudications: 1\nKinds:\n  eu_replay_parent_not_found: 1\n",
    }

    capture_calls: list[tuple[str, str]] = []

    def _fake_capture(
        celex: str,
        cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        assert cache_dir == tmp_path
        assert pit_date is None
        capture_calls.append((celex, output_format))
        return fixture[output_format]

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    exit_code = smoke.main(
        [
            "--celex",
            "32016R0679",
            "--cache-dir",
            str(tmp_path),
            "--format",
            output_format,
            "--expect-kind",
            "eu_replay_parent_not_found=1",
        ]
    )

    assert exit_code == 0
    assert capture_calls == expected_calls


@pytest.mark.parametrize(
    "expect_kind_value,expected_fragment",
    [
        ("bad-format", "--expect-kind expects KIND=COUNT format"),
        ("eu_replay_parent_not_found=not-an-int", "expected integer count"),
        ("=1", "non-empty kind"),
        ("eu_replay_parent_not_found=-1", "non-negative count"),
    ],
)
def test_main_rejects_invalid_expect_kind_cli(
    expect_kind_value: str,
    expected_fragment: str,
    tmp_path,
    monkeypatch,
) -> None:
    smoke = _load_smoke_module()

    def _fake_capture(
        _celex: str,
        _cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        return (
            '{"celex":"32016R0679","ops":0,"adjudications":0,'
            '"adjudication_kinds":{},"text_duplication_phases":[],'
            '"adjudications_data":[]}'
        )

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    with pytest.raises(SystemExit) as exc:
        smoke.main(["--expect-kind", expect_kind_value, "--cache-dir", str(tmp_path)])
    assert expected_fragment in str(exc.value)


def test_run_offline_smoke_forwards_custom_cache_across_formats(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()
    calls: list[str] = []
    captured_cache: list[object] = []

    def _fake_capture(
        _celex: str,
        cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        calls.append(output_format)
        captured_cache.append(cache_dir)
        assert pit_date is None
        if output_format == "json":
            return '{"celex":"32016R0679","ops":0,"adjudications":0,"adjudication_kinds":{},"text_duplication_phases":[],"adjudications_data":[]}'
        if output_format == "markdown":
            return "# EU Replay Report\n\n## Summary\n\n| Metric | Value |\n| --- | ---: |\n| CELEX | 32016R0679 |\n| Ops | 0 |\n| Adjudications | 0 |\n"
        if output_format == "text":
            return "EU Replay\nCELEX: 32016R0679\nOps: 0\nAdjudications: 0\n"
        raise AssertionError(f"unexpected format: {output_format}")

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    custom_cache = tmp_path / "custom-eu-smoke-cache"
    smoke.run_offline_smoke("32016R0679", custom_cache, output_format="json")
    smoke.run_offline_smoke("32016R0679", custom_cache, output_format="markdown")
    smoke.run_offline_smoke("32016R0679", custom_cache, output_format="text")

    assert calls == ["json", "json", "markdown", "json", "text"]
    assert len(captured_cache) == 5
    assert all(str(path) == str(custom_cache) for path in captured_cache)


def test_run_offline_smoke_forwards_pit_date(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()
    captured: dict[str, object] = {}

    class _FakePipeline:
        def __init__(self, cache_dir: Path) -> None:
            self.cache_dir = cache_dir

        def replay_statute(self, celex: str, cutoff_date: str | None = None) -> object:
            captured["celex"] = celex
            captured["pit_date"] = cutoff_date
            return SimpleNamespace(
                celex=celex,
                ops=[],
                adjudications=[],
                timelines={},
                replayed=object(),
            )

    monkeypatch.setattr(smoke.eu_pipeline, "EUReplayPipeline", _FakePipeline)

    smoke.run_offline_smoke("32016R0679", tmp_path, output_format="json", pit_date="2024-12-31")

    assert captured["celex"] == "32016R0679"
    assert captured["pit_date"] == "2024-12-31"


def test_run_offline_smoke_validates_expected_kind_counts(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()

    class _FakePipeline:
        def __init__(self, cache_dir: Path) -> None:
            self.cache_dir = cache_dir

        def replay_statute(self, celex: str, cutoff_date: str | None = None) -> object:
            return SimpleNamespace(
                celex=celex,
                ops=[1, 2],
                adjudications=[
                    SimpleNamespace(
                        kind="eu_replay_parent_not_found",
                        message="parent missing",
                        source_statute="2026/2",
                        op_id="insert-1",
                        detail={"parent_kind": "section", "parent_label": "7"},
                    ),
                    SimpleNamespace(
                        kind="eu_replay_parent_not_found",
                        message="parent missing",
                        source_statute="2026/3",
                        op_id="insert-2",
                        detail={"parent_kind": "section", "parent_label": "8"},
                    ),
                ],
                timelines={"section:1": None},
                replayed=object(),
            )

    monkeypatch.setattr(smoke.eu_pipeline, "EUReplayPipeline", _FakePipeline)

    payload = smoke.run_offline_smoke(
        "32016R0679",
        tmp_path,
        output_format="json",
        expected_kinds={"eu_replay_parent_not_found": 2},
    )
    assert payload["adjudication_kinds"]["eu_replay_parent_not_found"] == 2

    with pytest.raises(AssertionError):
        smoke.run_offline_smoke(
            "32016R0679",
            tmp_path,
            output_format="json",
            expected_kinds={"eu_replay_parent_not_found": 1},
        )


def test_run_offline_smoke_validates_zero_expected_kind(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()

    class _FakePipeline:
        def __init__(self, cache_dir: Path) -> None:
            self.cache_dir = cache_dir

        def replay_statute(self, celex: str, cutoff_date: str | None = None) -> object:
            return SimpleNamespace(
                celex=celex,
                ops=[1],
                adjudications=[
                    SimpleNamespace(
                        kind="eu_replay_parent_not_found",
                        message="parent missing",
                        source_statute="2026/1",
                        op_id="insert-1",
                        detail={"parent_kind": "section", "parent_label": "7"},
                    )
                ],
                timelines={"section:1": None},
                replayed=object(),
            )

    monkeypatch.setattr(smoke.eu_pipeline, "EUReplayPipeline", _FakePipeline)

    payload = smoke.run_offline_smoke(
        "32016R0679",
        tmp_path,
        output_format="json",
        expected_kinds={"eu_replay_target_not_found": 0},
    )
    assert payload["adjudication_kinds"].get("eu_replay_parent_not_found") == 1

    with pytest.raises(AssertionError):
        smoke.run_offline_smoke(
            "32016R0679",
            tmp_path,
            output_format="json",
            expected_kinds={"eu_replay_parent_not_found": 0},
        )


def test_run_offline_smoke_validates_expected_kinds_with_markdown_output(tmp_path, monkeypatch) -> None:
    smoke = _load_smoke_module()
    calls: list[str] = []

    def _fake_capture(
        _celex: str,
        _cache_dir: object,
        output_format: str,
        pit_date: str | None = None,
    ) -> str:
        calls.append(output_format)
        if output_format == "json":
            return (
                "{\"celex\":\"32016R0679\",\"ops\":1,\"adjudications\":1,"
                "\"adjudication_kinds\":{\"eu_replay_parent_not_found\":1},"
                "\"text_duplication_phases\":[],\"adjudications_data\":[{\"kind\":\"eu_replay_parent_not_found\","
                "\"message\":\"parent missing\",\"source_statute\":\"2026/1\",\"op_id\":\"insert-1\","
                "\"detail\":{\"parent_kind\":\"section\",\"parent_label\":\"7\"}}]}"
            )
        return (
            "# EU Replay Report\n\n## Summary\n\n| Metric | Value |\n| --- | ---: |\n| CELEX | 32016R0679 |\n| Ops | 1 |\n| Adjudications | 1 |\n"
        )

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    payload = smoke.run_offline_smoke(
        "32016R0679",
        tmp_path,
        output_format="markdown",
        expected_kinds={"eu_replay_parent_not_found": 1},
    )

    assert calls == ["json", "markdown"]
    assert payload["adjudication_kinds"]["eu_replay_parent_not_found"] == 1


def test_main_exits_non_zero_on_expected_kind_mismatch(monkeypatch) -> None:
    smoke = _load_smoke_module()

    def _fake_capture(_celex: str, _cache_dir: object, output_format: str, pit_date: str | None = None) -> str:
        assert output_format == "json"
        assert pit_date is None
        return (
            "{\"celex\":\"32016R0679\",\"ops\":0,\"adjudications\":1,"
            "\"adjudication_kinds\":{\"eu_replay_parent_not_found\":1},"
            "\"text_duplication_phases\":[],\"adjudications_data\":[{\"kind\":\"eu_replay_parent_not_found\","
            "\"message\":\"parent missing\",\"source_statute\":\"2026/1\",\"op_id\":\"insert-1\","
            "\"detail\":{\"parent_kind\":\"section\",\"parent_label\":\"7\"}}]}"
        )

    monkeypatch.setattr(smoke, "_run_eu_replay_capture", _fake_capture)

    with pytest.raises(SystemExit) as exc:
        smoke.main([
            "--celex",
            "32016R0679",
            "--expect-kind",
            "eu_replay_parent_not_found=0",
        ])
    assert exc.value.code != 0


def test_run_offline_smoke_returns_expected_contract(tmp_path) -> None:
    smoke = _load_smoke_module()
    payload = smoke.run_offline_smoke("32016R0679", tmp_path)

    assert payload["celex"] == "32016R0679"
    assert payload["ops"] == 0
    assert payload["adjudications"] >= 0
    assert "adjudication_kinds" in payload
    assert set(payload.keys()) >= {
        "celex",
        "ops",
        "adjudications",
        "adjudication_kinds",
        "text_duplication_phases",
        "adjudications_data",
    }


def test_run_offline_smoke_stabilizes_adjudication_summary(monkeypatch, tmp_path) -> None:
    smoke = _load_smoke_module()

    class _FakePipeline:
        def __init__(self, cache_dir: Path) -> None:
            self.cache_dir = cache_dir

        def replay_statute(self, celex: str, cutoff_date: str | None = None) -> object:
            return SimpleNamespace(
                celex=celex,
                ops=[1, 2],
                adjudications=[
                    SimpleNamespace(
                        kind="eu_replay_target_not_found",
                        message="target missing",
                        source_statute="2026/1",
                        op_id="replace-1",
                        detail={"target": "section:9"},
                    ),
                    SimpleNamespace(
                        kind="eu_replay_parent_not_found",
                        message="parent missing",
                        source_statute="2026/2",
                        op_id="insert-1",
                        detail={"parent_kind": "section", "parent_label": "7"},
                    ),
                    SimpleNamespace(
                        kind="eu_replay_target_not_found",
                        message="target missing",
                        source_statute="2026/3",
                        op_id="replace-2",
                        detail={"target": "section:10"},
                    ),
                ],
                timelines={"section:1": None},
                replayed=object(),
            )

    monkeypatch.setattr(smoke.eu_pipeline, "EUReplayPipeline", _FakePipeline)
    payload = smoke.run_offline_smoke("32016R0679", tmp_path)

    assert payload["ops"] == 2
    assert payload["adjudications"] == 3
    assert payload["adjudication_kinds"] == {
        "eu_replay_parent_not_found": 1,
        "eu_replay_target_not_found": 2,
    }
    assert payload["text_duplication_phases"] == []
    assert payload["adjudications_data"][0]["op_id"] == "replace-1"
