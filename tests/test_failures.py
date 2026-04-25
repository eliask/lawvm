from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from lawvm.finland.source_pathology import (
    build_item_target_structure_absent_pathology,
    build_sparse_item_body_missing_pathology,
)
from lawvm.finland.grafter import FailedOp
from lawvm.tools import failures


def test_categorize_failure_prefers_typed_source_pathology_over_legacy_heuristic() -> None:
    master = SimpleNamespace(find_section=lambda section, chapter=None: None)
    failure = FailedOp(
        amendment_id="1995/451",
        description="REPLACE 16 luku 9 § 1 mom 5a kohta",
        reason="no deterministic path",
        target_section="9",
        target_chapter="16",
        target_part="2",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(
        failure,
        cast(Any, master),
        {("1995/451", "ITEM_TARGET_STRUCTURE_ABSENT", "9 § 1 mom 5a kohta")},
    )

    assert got == "source_pathology:ITEM_TARGET_STRUCTURE_ABSENT"


def test_item_level_source_pathologies_stay_section_scoped() -> None:
    sparse = build_sparse_item_body_missing_pathology(
        source_statute="1995/451",
        target_section="9",
        target_paragraph="1",
        target_item="5a",
    )
    absent = build_item_target_structure_absent_pathology(
        source_statute="1995/451",
        target_section="9",
        target_paragraph="1",
        target_item="5a",
        live_has_paragraphs=True,
        amend_has_paragraphs=False,
    )

    assert sparse.target_unit_kind == "section"
    assert absent.target_unit_kind == "section"


def test_failed_op_derives_neutral_target_unit_kind() -> None:
    failure = FailedOp(
        amendment_id="2024/1",
        description="REPEAL 3 luku",
        reason="missing target",
        target_section="3",
        target_part="V",
        target_unit_kind="chapter",
    )

    assert failure.target_unit_kind == "chapter"
    assert failure.compat_target_kind_code == "L"
    assert failure.scope_detail()["target_part"] == "V"


def test_save_failure_cache_writes_neutral_schema(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "failures_demo.json"
    monkeypatch.setattr(failures, "_cache_path", lambda _label: cache_path)
    failure = FailedOp(
        amendment_id="2024/1",
        description="REPEAL 3 luku",
        reason="missing target",
        reason_code="TARGET_NOT_FOUND",
        target_section="3",
        target_unit_kind="chapter",
        target_part="V",
    )

    failures._save_failure_cache("demo", [failure])

    records = json.loads(cache_path.read_text())
    assert records[0]["target_unit_kind"] == "chapter"
    assert records[0]["reason_code"] == "TARGET_NOT_FOUND"
    assert records[0]["target_part"] == "V"
    assert "target_kind" not in records[0]


def test_load_failure_cache_accepts_legacy_kind_only_cache(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "failures_demo.json"
    monkeypatch.setattr(failures, "_cache_path", lambda _label: cache_path)
    cache_path.write_text(
        json.dumps(
            [
                {
                    "amendment_id": "2024/1",
                    "description": "REPEAL 3 luku",
                    "reason": "missing target",
                    "target_kind": "L",
                    "target_section": "3",
                    "target_chapter": "",
                    "target_part": "V",
                }
            ]
        )
    )

    loaded = failures._load_failure_cache("demo")

    assert loaded is not None
    assert loaded[0].target_unit_kind == "chapter"
    assert loaded[0].reason_code == ""
    assert loaded[0].target_part == "V"


def test_load_failure_cache_preserves_reason_code(tmp_path, monkeypatch) -> None:
    cache_path = tmp_path / "failures_demo.json"
    monkeypatch.setattr(failures, "_cache_path", lambda _label: cache_path)
    cache_path.write_text(
        json.dumps(
            [
                {
                    "amendment_id": "2024/1",
                    "description": "REPEAL 3 luku",
                    "reason": "missing target",
                    "reason_code": "TARGET_NOT_FOUND",
                    "target_unit_kind": "chapter",
                    "target_section": "3",
                    "target_chapter": "",
                    "target_part": "V",
                }
            ]
        )
    )

    loaded = failures._load_failure_cache("demo")

    assert loaded is not None
    assert loaded[0].target_unit_kind == "chapter"
    assert loaded[0].reason_code == "TARGET_NOT_FOUND"
    assert loaded[0].target_part == "V"


def test_replay_one_for_failures_serializes_reason_code(monkeypatch) -> None:
    def fake_replay_xml(
        sid: str,
        *,
        failed_ops_out: list[FailedOp],
        quiet: bool,
    ) -> None:
        assert sid == "2024/1"
        assert quiet is True
        failed_ops_out.append(
            FailedOp(
                amendment_id="2024/2",
                description="REPLACE 3 §",
                reason="source missing",
                reason_code="SOURCE_NOT_FOUND",
                target_section="3",
                target_unit_kind="section",
                target_part="II",
            )
        )

    monkeypatch.setattr(failures, "replay_xml", fake_replay_xml)

    rows = failures._replay_one_for_failures("2024/1")

    assert rows == [
        {
            "sid": "2024/1",
            "amendment_id": "2024/2",
            "description": "REPLACE 3 §",
            "reason": "source missing",
            "reason_code": "SOURCE_NOT_FOUND",
            "target_section": "3",
            "target_chapter": None,
            "target_part": "II",
            "target_unit_kind": "section",
        }
    ]
