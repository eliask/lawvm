"""Offline smoke check for `lawvm eu-replay` output contract.

This script runs the eu-replay command path without any network calls.
"""
from __future__ import annotations

import argparse
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from lawvm.tools.eu_replay import main as eu_replay_main

import lawvm.eu.cellar as eu_cellar
import lawvm.eu.pipeline as eu_pipeline


_BASELINE_XHTML = """\
<html><body>
  <p class="oj-ti-art">Article 1</p>
  <p>Article 1 baseline text.</p>
  <p class="oj-ti-art">Article 2</p>
  <p>Article 2 baseline text.</p>
</body></html>
"""

_AMENDMENT_XHTML = """\
<html><body>
  <p class="oj-ti-art">Article 1</p>
  <p>Article 1 amendment text.</p>
</body></html>
"""

_DISCOVERY_XML = """\
<TREE>
  <AMENDED_BY_WORK>
    <URI><IDENTIFIER>32016R0679M1</IDENTIFIER></URI>
  </AMENDED_BY_WORK>
  <AMENDED_BY_WORK>
    <URI><IDENTIFIER>32016R0679M1</IDENTIFIER></URI>
  </AMENDED_BY_WORK>
</TREE>"""

_VALID_FORMATS = ("text", "json", "markdown")


def _parse_expected_kinds(values: list[str]) -> dict[str, int]:
    parsed: dict[str, int] = {}
    for entry in values:
        if "=" not in entry:
            raise ValueError(f"--expect-kind expects KIND=COUNT format, got {entry!r}")
        kind, raw_count = entry.split("=", 1)
        kind = kind.strip()
        if not kind:
            raise ValueError(f"--expect-kind requires a non-empty kind, got {entry!r}")
        raw_count = raw_count.strip()
        if not raw_count:
            raise ValueError(f"expected integer count for --expect-kind {entry!r}")
        try:
            count = int(raw_count)
        except ValueError as exc:
            raise ValueError(f"expected integer count for --expect-kind {entry!r}") from exc
        if count < 0:
            raise ValueError(f"expected non-negative count for --expect-kind {entry!r}")
        parsed[kind] = count
    return parsed


def _assert_expected_kind_counts(
    payload: dict[str, Any],
    expected_kinds: dict[str, int],
) -> None:
    adjudication_kinds = payload.get("adjudication_kinds")
    if not isinstance(adjudication_kinds, dict):
        raise AssertionError("adjudication_kinds must be a dict")

    for kind, expected_count in expected_kinds.items():
        actual_count = adjudication_kinds.get(kind, 0)
        if not isinstance(actual_count, int):
            raise AssertionError(f"kind count for {kind!r} is not int: {actual_count!r}")
        if actual_count != expected_count:
            raise AssertionError(
                f"unexpected adjudication kind count for {kind!r}: expected {expected_count}, got {actual_count}"
            )


def _validate_markdown_output(raw: str) -> None:
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    if not lines:
        raise AssertionError("markdown output was empty")
    content_lines = [
        line for line in lines
        if not line.startswith(("DEBUG:", "["))
    ]
    if not content_lines:
        raise AssertionError("markdown output had only debug lines")
    if not any(line == "# EU Replay Report" for line in content_lines):
        raise AssertionError("markdown output missing EU Replay Report header")
    if "## Summary" not in content_lines:
        raise AssertionError("markdown output missing summary section")
    for marker in ("| Metric | Value |", "| CELEX |", "| Ops |", "| Adjudications |"):
        if marker not in raw:
            raise AssertionError(f"markdown output missing marker: {marker}")


def _validate_text_output(raw: str) -> None:
    if not raw.strip():
        raise AssertionError("text output was empty")
    lines = [
        line.strip()
        for line in raw.splitlines()
        for line in [line.strip()]
        if line and not line.startswith(("DEBUG:", "["))
    ]
    if not lines:
        raise AssertionError("text output was empty")
    if not lines[0].startswith("EU Replay"):
        raise AssertionError("text output missing EU Replay header")
    if "CELEX:" not in raw:
        raise AssertionError("text output missing CELEX line")
    adjudications_line = next((line for line in lines if line.startswith("Adjudications:")), None)
    if adjudications_line:
        try:
            count = int(adjudications_line.split(":", 1)[1].strip())
        except ValueError:
            count = 0
        if count > 0 and "Kinds:" not in raw:
            raise AssertionError("text output missing Kinds section")


class _NoopEUOpsParser:
    """Offline-safe parser that intentionally emits no ops."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def extract_ops(self, _text: str) -> list[dict[str, Any]]:
        return []


def _celex_from_notice_path(path: Path) -> str:
    stem = path.stem
    if stem.endswith("_tree"):
        return stem[:-5]
    if stem.endswith("_baseline"):
        return stem[:-9]
    return stem


def _fake_request_notice(notice: Any) -> tuple[bytes, dict[str, Any]]:
    if notice.celex == "32016R0679":
        return _DISCOVERY_XML.encode("utf-8"), {"offline": True}
    return _BASELINE_XHTML.encode("utf-8"), {"offline": True}


def _fake_select_manifestation_option(_path: Path, language: str, manifestation_type: str) -> dict[str, Any]:
    if language != "ENG":
        raise ValueError(f"Expected ENG manifestation language, got {language}")
    if manifestation_type != "xhtml":
        raise ValueError(f"Expected xhtml manifestation, got {manifestation_type}")

    celex = _celex_from_notice_path(_path)
    url = f"https://offline.local/{celex}.xhtml"
    return {
        "items": [{"uri": {"value": url}}],
        "manifestation_uri": {"value": url},
    }


def _fake_request_url(url: str, accept: str | None = None) -> tuple[bytes, dict[str, Any]]:
    if "32016R0679M1" in url:
        return _AMENDMENT_XHTML.encode("utf-8"), {"offline": True}
    if "32016R0679" in url:
        return _BASELINE_XHTML.encode("utf-8"), {"offline": True}
    raise ValueError(f"unexpected offline URL: {url}")


def _validate_payload(payload: dict[str, Any]) -> None:
    required = {
        "celex",
        "ops",
        "adjudications",
        "adjudication_kinds",
        "text_duplication_phases",
        "adjudications_data",
    }
    if payload.keys() < required:
        missing = required - payload.keys()
        raise AssertionError(f"missing output keys: {sorted(missing)}")

    if not isinstance(payload["ops"], int):
        raise AssertionError(f"ops should be int, got {type(payload['ops']).__name__}")
    if not isinstance(payload["adjudications"], int):
        raise AssertionError("adjudications should be int")
    if not isinstance(payload["adjudication_kinds"], dict):
        raise AssertionError("adjudication_kinds should be dict")
    if not isinstance(payload["text_duplication_phases"], list):
        raise AssertionError("text_duplication_phases should be list")
    if not isinstance(payload["adjudications_data"], list):
        raise AssertionError("adjudications_data should be list")

    for item in payload["adjudications_data"]:
        for field in ("kind", "message", "source_statute", "op_id", "detail"):
            if field not in item:
                raise AssertionError(f"adjudication payload missing field {field!r}")


def _parse_last_json_line(raw: str) -> dict[str, Any]:
    for line in reversed(raw.splitlines()):
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    raise RuntimeError(f"No JSON payload found in eu-replay output: {raw!r}")


def _run_eu_replay_capture(
    celex: str,
    cache_dir: Path,
    output_format: str,
    pit_date: str | None = None,
) -> str:
    if output_format not in _VALID_FORMATS:
        raise ValueError(f"unsupported output format: {output_format!r}")

    old_request_notice = eu_pipeline._request_notice
    old_select_manifestation_option = eu_cellar.select_manifestation_option
    old_request_url = eu_cellar._request_url
    old_ops_parser = eu_pipeline.EUOpsParser

    try:
        setattr(eu_pipeline, "_request_notice", _fake_request_notice)
        setattr(eu_cellar, "select_manifestation_option", _fake_select_manifestation_option)
        setattr(eu_cellar, "_request_url", _fake_request_url)
        setattr(eu_pipeline, "EUOpsParser", _NoopEUOpsParser)

        args = argparse.Namespace(
            command="eu-replay",
            celex=celex,
            pit_date=pit_date,
            cache_dir=str(cache_dir),
            format=output_format,
            json=output_format == "json",
        )
        capture = io.StringIO()
        with redirect_stdout(capture):
            eu_replay_main(args)
        return capture.getvalue()
    finally:
        setattr(eu_pipeline, "_request_notice", old_request_notice)
        setattr(eu_cellar, "select_manifestation_option", old_select_manifestation_option)
        setattr(eu_cellar, "_request_url", old_request_url)
        setattr(eu_pipeline, "EUOpsParser", old_ops_parser)


def run_offline_smoke(
    celex: str,
    cache_dir: Path,
    output_format: str = "json",
    pit_date: str | None = None,
    expected_kinds: dict[str, int] | None = None,
) -> dict[str, Any]:
    output_format = output_format.lower()
    raw_json = _run_eu_replay_capture(
        celex,
        cache_dir,
        "json",
        pit_date=pit_date,
    )
    payload = _parse_last_json_line(raw_json)
    _validate_payload(payload)

    if output_format == "markdown":
        _validate_markdown_output(
            _run_eu_replay_capture(
                celex,
                cache_dir,
                "markdown",
                pit_date=pit_date,
            )
        )
    elif output_format == "text":
        _validate_text_output(
            _run_eu_replay_capture(
                celex,
                cache_dir,
                "text",
                pit_date=pit_date,
            )
        )
    elif output_format != "json":
        raise ValueError(f"unsupported output format: {output_format!r}")

    if expected_kinds is None:
        expected_kinds = {}
    _assert_expected_kind_counts(payload, expected_kinds)

    return payload


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run lawvm eu-replay in offline mode.")
    parser.add_argument("--celex", default="32016R0679")
    parser.add_argument("--cache-dir", default=".tmp/eu_replay_smoke_cache")
    parser.add_argument(
        "--format",
        choices=_VALID_FORMATS,
        default="json",
        help="Output format to validate (default: json).",
    )
    parser.add_argument(
        "--pit-date",
        default=None,
        help="Optional PIT date passed through to eu-replay.",
    )
    parser.add_argument(
        "--expect-kind",
        action="append",
        default=[],
        help="Expected adjudication kind counts as KIND=COUNT. Repeat for each kind.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        expected_kinds = _parse_expected_kinds(args.expect_kind)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    try:
        payload = run_offline_smoke(
            args.celex,
            Path(args.cache_dir),
            output_format=args.format,
            pit_date=args.pit_date,
            expected_kinds=expected_kinds,
        )
    except AssertionError as exc:
        raise SystemExit(str(exc)) from exc

    print("EU_REPLAY_SMOKE_OK", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
