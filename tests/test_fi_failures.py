from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.source_pathology import (
    build_item_target_structure_absent_pathology,
    build_sparse_item_body_missing_pathology,
)
from lawvm.finland.ops import FailedOp
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


def test_categorize_failure_prefers_failed_op_section_not_found_over_final_tree() -> None:
    master = SimpleNamespace(
        find_section=lambda section, chapter=None: SimpleNamespace(
            children=[SimpleNamespace(kind="subsection")]
        )
    )
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 § 2 mom",
        reason="master §3 not found",
        reason_code="section_not_found",
        target_section="3",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(failure, cast(Any, master))

    assert got == "failed_op:section_not_found"


def test_categorize_failure_names_absent_detail_master_section() -> None:
    master = SimpleNamespace(find_section=lambda section, chapter=None: None)
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 § 1 mom 2 kohta",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="3",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(failure, cast(Any, master))

    assert got == "target_section_absent_in_detail_master"


def test_categorize_failure_accepts_irnodekind_enum_values() -> None:
    paragraph = SimpleNamespace(kind=IRNodeKind.PARAGRAPH, label="1")
    subsection = SimpleNamespace(kind=IRNodeKind.SUBSECTION, children=[paragraph])
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=[subsection]))
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 § 1 mom 2 kohta",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="3",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(failure, cast(Any, master))

    assert got == "kohta_label_gap(max=1,want=2)"


def test_categorize_failure_distinguishes_missing_item_label_from_gap() -> None:
    paragraphs = [
        SimpleNamespace(kind=IRNodeKind.PARAGRAPH, label="1"),
        SimpleNamespace(kind=IRNodeKind.PARAGRAPH, label="3"),
        SimpleNamespace(kind=IRNodeKind.PARAGRAPH, label="4"),
    ]
    subsection = SimpleNamespace(kind=IRNodeKind.SUBSECTION, children=paragraphs)
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=[subsection]))
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 § 1 mom 2 kohta",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="3",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(failure, cast(Any, master))

    assert got == "kohta_label_missing(count=3,want=2)"


def test_categorize_failure_names_existing_momentti_apply_failure() -> None:
    subsections = [
        SimpleNamespace(kind=IRNodeKind.SUBSECTION, children=[]),
        SimpleNamespace(kind=IRNodeKind.SUBSECTION, children=[]),
    ]
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=subsections))
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 § 2 mom",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="3",
        target_unit_kind="section",
    )

    got = failures._categorize_failure(failure, cast(Any, master))

    assert got == "mom_amend_extract_fail"


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
        target_statute_id="2023/9",
    )

    failures._save_failure_cache("demo", [failure])

    records = json.loads(cache_path.read_text())
    assert records[0]["target_unit_kind"] == "chapter"
    assert records[0]["reason_code"] == "TARGET_NOT_FOUND"
    assert records[0]["target_part"] == "V"
    assert records[0]["target_statute_id"] == "2023/9"
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
    assert loaded[0].target_statute_id is None


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
                    "target_statute_id": "2023/9",
                }
            ]
        )
    )

    loaded = failures._load_failure_cache("demo")

    assert loaded is not None
    assert loaded[0].target_unit_kind == "chapter"
    assert loaded[0].reason_code == "TARGET_NOT_FOUND"
    assert loaded[0].target_part == "V"
    assert loaded[0].target_statute_id == "2023/9"


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
            "target_statute_id": "2024/1",
            "target_section": "3",
            "target_chapter": None,
            "target_part": "II",
            "target_subsection": None,
            "target_item": None,
            "target_unit_kind": "section",
        }
    ]


def test_print_summary_includes_target_statute_id(capsys) -> None:
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 §",
        reason="source missing",
        reason_code="SOURCE_NOT_FOUND",
        target_section="3",
        target_unit_kind="section",
        target_statute_id="2024/1",
    )

    failures._print_summary([failure], pattern=None, top=5)

    out = capsys.readouterr().out
    assert "=== Target statutes" in out
    assert "2024/1" in out
    assert "[2024/2] target=2024/1 REPLACE 3 §" in out


def test_print_detail_includes_target_statute_and_reason(capsys) -> None:
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 §",
        reason="master §3 not found",
        reason_code="section_not_found",
        target_section="3",
        target_unit_kind="section",
        target_statute_id="2024/1",
    )

    failures._print_detail([failure], masters_by_sid={}, pathologies_by_sid={}, pattern=None, top=5)

    out = capsys.readouterr().out
    assert "[2024/2] target=2024/1 REPLACE 3 §" in out
    assert "reason=section_not_found" in out
    assert "failed_op:section_not_found" in out
    assert "fi.v1.FAILED_OPERATION_RESOLUTION" in out
    assert "frontier=fi_failed_operation_resolution/failed_operation_frontier" in out


def test_print_detail_projects_source_pathology_to_claim_kind(capsys) -> None:
    failure = FailedOp(
        amendment_id="1995/451",
        description="REPLACE 16 luku 9 § 1 mom 5a kohta",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="9",
        target_chapter="16",
        target_unit_kind="section",
        target_statute_id="1987/1250",
    )
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=[]))
    pathologies = {
        "1987/1250": {
            ("1995/451", "ITEM_TARGET_STRUCTURE_ABSENT", "9 § 1 mom 5a kohta")
        }
    }

    failures._print_detail(
        [failure],
        masters_by_sid={"1987/1250": cast(Any, master)},
        pathologies_by_sid=pathologies,
        pattern=None,
        top=5,
    )

    out = capsys.readouterr().out
    assert "source_pathology:ITEM_TARGET_STRUCTURE_ABSENT" in out
    assert "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION" in out
    assert "owner_phase=replay_apply" in out
    assert "frontier=fi_item_target_structure_absent/target_resolution_frontier" in out
    assert "=== Final materialized target status ===" in out
    assert "target_subsection_absent" in out


def test_materialization_probe_marks_final_item_present() -> None:
    paragraph = SimpleNamespace(kind=IRNodeKind.PARAGRAPH, label="1")
    subsection = SimpleNamespace(kind=IRNodeKind.SUBSECTION, children=[paragraph])
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=[subsection]))
    failure = FailedOp(
        amendment_id="2014/190",
        description="REPLACE 1 luku 5 § 1 mom 1 kohta",
        reason="item target subsection has no paragraph children",
        reason_code="item_no_paragraphs",
        target_section="5",
        target_chapter="1",
        target_unit_kind="section",
        target_statute_id="1987/1250",
    )

    probe = failures._materialization_probe_for_failure(failure, cast(Any, master))

    assert probe.probe_status == "target_item_present"
    assert probe.target_present is True


def test_detail_json_emits_machine_readable_proof_lane(capsys) -> None:
    failure = FailedOp(
        amendment_id="1995/451",
        description="REPLACE 16 luku 9 § 1 mom 5a kohta",
        reason="no deterministic path",
        reason_code="no_deterministic_path",
        target_section="9",
        target_chapter="16",
        target_unit_kind="section",
        target_statute_id="1987/1250",
    )
    master = SimpleNamespace(find_section=lambda section, chapter=None: SimpleNamespace(children=[]))
    pathologies = {
        "1987/1250": {
            ("1995/451", "ITEM_TARGET_STRUCTURE_ABSENT", "9 § 1 mom 5a kohta")
        }
    }

    failures._print_detail(
        [failure],
        masters_by_sid={"1987/1250": cast(Any, master)},
        pathologies_by_sid=pathologies,
        pattern=None,
        top=5,
        json_output=True,
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["total_failures"] == 1
    assert payload["materialized_target_present"] == 0
    assert payload["materialized_target_status_counts"] == {"target_subsection_absent": 1}
    row = payload["failures"][0]
    assert row["amendment_id"] == "1995/451"
    assert row["target_statute_id"] == "1987/1250"
    assert row["category"] == "source_pathology:ITEM_TARGET_STRUCTURE_ABSENT"
    assert row["required_claim_kind"] == "fi.v1.SPARSE_SLOT_PAYLOAD_RESOLUTION"
    assert row["owner_phase"] == "replay_apply"
    assert row["frontier_family"] == "fi_item_target_structure_absent"
    assert row["frontier_status"] == "target_resolution_frontier"
    assert row["materialized_target_status"] == "target_subsection_absent"
    assert row["materialized_target_present"] is False


def test_detail_mode_uses_cached_failures_without_full_replay(monkeypatch) -> None:
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 §",
        reason="source missing",
        target_section="3",
        target_unit_kind="section",
        target_statute_id="2024/1",
    )
    calls: dict[str, Any] = {}

    monkeypatch.setattr(failures, "_load_failure_cache", lambda _label: [failure])

    def fail_collect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("cached detail must not replay the whole bench")

    monkeypatch.setattr(failures, "_collect_failures", fail_collect)

    def fake_detail_masters(
        got: list[FailedOp],
        *,
        verbose: bool = False,
    ) -> tuple[dict[str, Any], dict[str, set[tuple[str, str, str]]]]:
        calls["detail_failures"] = got
        calls["verbose"] = verbose
        return {}, {}

    monkeypatch.setattr(failures, "_collect_detail_masters", fake_detail_masters)
    def fake_print_detail(got: list[FailedOp], *_args: object, **_kwargs: object) -> None:
        calls.setdefault("printed", got)

    monkeypatch.setattr(failures, "_print_detail", fake_print_detail)

    assert failures.main(from_bench="demo", detail=True, parallel=8, verbose=True) == 0
    assert calls["detail_failures"] == [failure]
    assert calls["printed"] == [failure]
    assert calls["verbose"] is True


def test_detail_mode_keeps_parallel_failure_scan_before_master_context(monkeypatch) -> None:
    failure = FailedOp(
        amendment_id="2024/2",
        description="REPLACE 3 §",
        reason="source missing",
        target_section="3",
        target_unit_kind="section",
        target_statute_id="2024/1",
    )
    calls: dict[str, Any] = {}

    monkeypatch.setattr(failures, "_load_failure_cache", lambda _label: None)
    monkeypatch.setattr(failures, "_load_imperfect_sids_from_bench", lambda _label: ["2024/1", "2024/3"])

    def fake_collect(
        sids: list[str],
        *,
        verbose: bool,
        need_masters: bool,
        parallel: int,
    ) -> tuple[list[FailedOp], dict[str, Any], dict[str, set[tuple[str, str, str]]]]:
        calls["collect"] = (sids, verbose, need_masters, parallel)
        return [failure], {}, {}

    monkeypatch.setattr(failures, "_collect_failures", fake_collect)

    def fake_detail_masters(
        got: list[FailedOp],
        *,
        verbose: bool = False,
    ) -> tuple[dict[str, Any], dict[str, set[tuple[str, str, str]]]]:
        calls["detail"] = (got, verbose)
        return {}, {}

    monkeypatch.setattr(failures, "_collect_detail_masters", fake_detail_masters)
    monkeypatch.setattr(failures, "_print_detail", lambda *_args, **_kwargs: None)

    assert failures.main(from_bench="demo", detail=True, parallel=8, verbose=True) == 0
    assert calls["collect"] == (["2024/1", "2024/3"], True, False, 8)
    assert calls["detail"] == ([failure], True)
