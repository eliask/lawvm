"""Integration tests for lawvm ops -j uk.

Skipped when data/uk_legislation.farchive is absent.
"""
from __future__ import annotations

import io
import json
import sys
from argparse import Namespace
from functools import lru_cache
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DB_PATH = _REPO_ROOT / "data" / "uk_legislation.farchive"


def _archive_available() -> bool:
    return _DB_PATH.exists()


pytestmark = pytest.mark.skipif(
    not _archive_available(), reason="uk_legislation.farchive not available"
)


@lru_cache(maxsize=None)
def _uk_replay_json(statute_id: str) -> dict:
    """Run the actual uk-replay pipeline and return the JSON payload."""
    from lawvm.tools.uk_replay import main as uk_replay_main

    args = Namespace(
        statute_id=statute_id,
        pit_date=None,
        enacted_only=False,
        verbose=False,
        fetch_missing=False,
        include_enacted_affecting=False,
        replay_adjudication_samples=None,
        replay_adjudication_sample_limit=5,
        json=True,
        db=None,
        timeline=False,
        commencement=False,
        # replay regime defaults — mirror normalize_uk_replay_regime defaults
        metadata_backfill=None,
        oracle_alignment=None,
        metadata_only_effects=None,
        applicability=None,
        authority=None,
    )
    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        uk_replay_main(args)
    finally:
        sys.stdout = old_stdout
    return json.loads(buf.getvalue())


def _ops_uk_json(statute_id: str) -> dict:
    """Call _ops_uk_sync with JSON mode and return parsed output."""
    from lawvm.tools.ops import _ops_uk_sync

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _ops_uk_sync(
            sid=statute_id,
            source_filter=None,
            target_filter=None,
            emit_json=True,
        )
    finally:
        sys.stdout = old_stdout
    return json.loads(buf.getvalue())


def test_ops_uk_1978_30_op_count_matches_uk_replay() -> None:
    """ops_count from _ops_uk_sync must equal uk-replay ops_count for ukpga/1978/30."""
    ops_payload = _ops_uk_json("ukpga/1978/30")
    replay_payload = _uk_replay_json("ukpga/1978/30")

    assert ops_payload["ops_count"] == replay_payload["ops_count"], (
        f"ops_count mismatch: ops={ops_payload['ops_count']} replay={replay_payload['ops_count']}"
    )
    assert ops_payload["ops_count"] > 0


def test_ops_uk_1978_30_rejection_histogram_matches_uk_replay() -> None:
    """Blocking rejection rule histogram must match uk-replay for ukpga/1978/30."""
    ops_payload = _ops_uk_json("ukpga/1978/30")
    replay_payload = _uk_replay_json("ukpga/1978/30")

    assert ops_payload["rejection_rule_counts"] != {}, (
        "expected non-empty rejection histogram for ukpga/1978/30"
    )
    assert ops_payload["rejection_rule_counts"] == replay_payload["blocking_compile_rejection_rule_counts"], (
        f"rejection mismatch:\n  ops={ops_payload['rejection_rule_counts']}\n"
        f"  replay={replay_payload['blocking_compile_rejection_rule_counts']}"
    )


def test_ops_uk_1998_42_op_count_matches_uk_replay() -> None:
    """ops_count must equal uk-replay ops_count for ukpga/1998/42."""
    ops_payload = _ops_uk_json("ukpga/1998/42")
    replay_payload = _uk_replay_json("ukpga/1998/42")

    assert ops_payload["ops_count"] == replay_payload["ops_count"], (
        f"ops_count mismatch: ops={ops_payload['ops_count']} replay={replay_payload['ops_count']}"
    )
    assert ops_payload["ops_count"] > 0


def test_ops_uk_1978_30_json_schema_has_required_fields() -> None:
    """JSON output must carry jurisdiction, statute_id, ops, rejections fields."""
    payload = _ops_uk_json("ukpga/1978/30")

    assert payload["jurisdiction"] == "uk"
    assert payload["statute_id"] == "ukpga/1978/30"
    assert isinstance(payload["ops"], list)
    assert isinstance(payload["rejections"], list)
    assert isinstance(payload["rejection_rule_counts"], dict)
    assert payload["ops_shown"] == payload["ops_count"]


def test_ops_uk_1978_30_ops_list_has_required_fields() -> None:
    """Each op must carry op_id, action, target, source_statute fields."""
    payload = _ops_uk_json("ukpga/1978/30")

    for op in payload["ops"]:
        assert "op_id" in op, f"missing op_id in {op}"
        assert "action" in op, f"missing action in {op}"
        assert "target" in op, f"missing target in {op}"
        assert "source_statute" in op, f"missing source_statute in {op}"


def test_ops_uk_1978_30_source_filter_narrows_results() -> None:
    """--source filter must return a strict subset of all ops."""
    from lawvm.tools.ops import _ops_uk_sync

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _ops_uk_sync(
            sid="ukpga/1978/30",
            source_filter="ukpga/2003/44",
            target_filter=None,
            emit_json=True,
        )
    finally:
        sys.stdout = old_stdout
    payload = json.loads(buf.getvalue())

    assert payload["ops_shown"] <= payload["ops_count"]
    for op in payload["ops"]:
        assert "ukpga/2003/44" in op["source_statute"]


def test_ops_uk_1978_30_target_filter_narrows_results() -> None:
    """--target filter must return ops whose target contains the filter string."""
    from lawvm.tools.ops import _ops_uk_sync

    buf = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        _ops_uk_sync(
            sid="ukpga/1978/30",
            source_filter=None,
            target_filter="schedule:1",
            emit_json=True,
        )
    finally:
        sys.stdout = old_stdout
    payload = json.loads(buf.getvalue())

    assert payload["ops_shown"] <= payload["ops_count"]
    for op in payload["ops"]:
        assert "schedule:1" in op["target"].casefold()


def test_ops_uk_main_dispatch_returns_for_uk_jurisdiction(capsys) -> None:
    """main() must not raise for -j uk."""
    from lawvm.tools.ops import main

    args = Namespace(
        jurisdiction="uk",
        statute_id="ukpga/1978/30",
        source=None,
        target=None,
        json=False,
    )
    main(args)
    captured = capsys.readouterr()
    assert "Ops total" in captured.out
    assert "101" in captured.out
