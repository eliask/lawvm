from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools import blame


def test_blame_sync_replays_quietly(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(
        statute_id: str,
        *,
        mode: str,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
    ):
        called["statute_id"] = statute_id
        called["mode"] = mode
        called["quiet"] = quiet
        return SimpleNamespace(
            title="Quiet blame",
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
            findings=(),
        )

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)

    blame.main(
        Namespace(
            statute_id="1991/1",
            address=None,
            source=None,
            mode="legal_pit",
        )
    )

    assert called == {"statute_id": "1991/1", "mode": "legal_pit", "quiet": True}
    out = capsys.readouterr().out
    assert "Statute : 1991/1" in out


def test_blame_accepts_provision_alias_for_address() -> None:
    from lawvm.tools.cli import _build_parser

    args = _build_parser().parse_args(["blame", "2023/703", "--provision", "section:9"])
    assert args.address == "section:9"


def test_blame_rejects_malformed_finnish_address_before_replay(monkeypatch, capsys) -> None:
    def fail_replay(*_args, **_kwargs):
        raise AssertionError("invalid address filter should fail before replay")

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fail_replay)

    with pytest.raises(SystemExit) as raised:
        blame.main(
            Namespace(
                statute_id="1992/1535",
                jurisdiction="fi",
                address="section:127 a §",
                source=None,
                mode="official_consolidation",
                format="json",
            )
        )

    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "ERROR: invalid --address/--provision 'section:127 a §'" in err
    assert "help: try 'section:127a'" in err


def test_blame_rejects_finnish_prose_address_before_filter_drop(monkeypatch, capsys) -> None:
    def fail_replay(*_args, **_kwargs):
        raise AssertionError("prose address filter should fail before replay")

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fail_replay)

    with pytest.raises(SystemExit) as raised:
        blame.main(
            Namespace(
                statute_id="1992/1535",
                jurisdiction="fi",
                address="127 a §",
                source=None,
                mode="official_consolidation",
                format="json",
            )
        )

    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "this looks like Finnish pykälä notation" in err
    assert "help: try 'section:127a'" in err


def test_blame_selector_validation_is_fi_only(monkeypatch, capsys) -> None:
    called: dict[str, object] = {}

    def fake_replay_xml(
        statute_id: str,
        *,
        mode: str,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
    ):
        called["statute_id"] = statute_id
        return SimpleNamespace(
            title="UK-style selector smoke",
            ir=IRNode(kind=IRNodeKind.BODY, children=()),
            findings=(),
        )

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)

    blame.main(
        Namespace(
            statute_id="ukpga/2000/8",
            jurisdiction="uk",
            address="section:127 a §",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )

    assert called == {"statute_id": "ukpga/2000/8"}
    assert capsys.readouterr().err == ""


def _fake_master_with_section(*, findings=()) -> SimpleNamespace:
    section = IRNode(kind=IRNodeKind.SECTION, label="30")
    chapter = IRNode(kind=IRNodeKind.CHAPTER, label="6", children=(section,))
    return SimpleNamespace(
        title="Synthetic statute",
        ir=IRNode(kind=IRNodeKind.BODY, children=(chapter,)),
        findings=tuple(findings),
    )


def _fake_master_with_part_section(*, findings=()) -> SimpleNamespace:
    section = IRNode(kind=IRNodeKind.SECTION, label="9")
    chapter = IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(section,))
    part = IRNode(kind=IRNodeKind.PART, label="1", children=(chapter,))
    return SimpleNamespace(
        title="Synthetic statute",
        ir=IRNode(kind=IRNodeKind.BODY, children=(part,)),
        findings=tuple(findings),
    )


def _fake_master_with_ambiguous_part_sections(*, findings=()) -> SimpleNamespace:
    sections = []
    for part_label in ("1", "2"):
        section = IRNode(kind=IRNodeKind.SECTION, label="9")
        chapter = IRNode(kind=IRNodeKind.CHAPTER, label="2", children=(section,))
        sections.append(IRNode(kind=IRNodeKind.PART, label=part_label, children=(chapter,)))
    return SimpleNamespace(
        title="Synthetic statute",
        ir=IRNode(kind=IRNodeKind.BODY, children=tuple(sections)),
        findings=tuple(findings),
    )


def _occupancy_violation_finding(
    amendment_id: str = "2025/1382",
    *,
    target_label: str | None = "30",
    target_chapter: str = "6",
):
    from lawvm.core.phase_result import Finding

    detail: dict[str, object] = {
        "current_occupancy": "absent",
        "allowed_from": ["substantive", "tombstone"],
    }
    if target_label is not None:
        detail["target_label"] = target_label
    if target_chapter:
        detail["ctx_label"] = f"[{amendment_id}] INSERT {target_chapter} luku {target_label or '?'} §"
    return Finding(
        kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
        role="observation",
        stage="apply",
        source_statute=amendment_id,
        detail=detail,
        blocking=False,
    )


def test_blame_annotates_sections_from_flat_compiled_op_rows(monkeypatch, capsys) -> None:
    """Compiled ops are flat target_* rows; blame must not read them as empty.

    Regression: blame read op["target"] (a removed nested shape), so EVERY
    provision of EVERY statute reported "unmodified — base statute text".
    """

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "sequence": 1,
                    "action": "replace",
                    "source_statute": "2025/1382",
                    "source_title": "Amending act",
                    "target_unit_kind": "section",
                    "target_norm": "30",
                    "target_chapter": "6",
                }
            )
        return _fake_master_with_section()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(Namespace(statute_id="2014/1429", address="section:30", source=None, mode="official_consolidation"))

    out = capsys.readouterr().out
    assert "2025/1382" in out
    assert "REPLACE" in out
    assert "unmodified" not in out


def test_blame_matches_chapter_scoped_op_to_unique_part_prefixed_section(monkeypatch, capsys) -> None:
    """Ops may omit a higher container that replay has; match only as a unique suffix."""

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "sequence": 7,
                    "action": "insert",
                    "source_statute": "2026/376",
                    "source_title": "Amending act",
                    "target_unit_kind": "section",
                    "target_norm": "9",
                    "target_chapter": "2",
                }
            )
        return _fake_master_with_part_section()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(Namespace(statute_id="2023/703", address="section:9", source=None, mode="official_consolidation"))

    out = capsys.readouterr().out
    assert "2026/376" in out
    assert "INSERT" in out
    assert "unmodified" not in out


def test_blame_latest_unique_suffix_op_beats_older_exact_container_op(monkeypatch, capsys) -> None:
    """A later child op may be less container-scoped than an older section op.

    Exact-key matching must not make an older chapter-scoped op win over a
    later section-only op when the section-only key uniquely identifies the
    replayed section.
    """

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "sequence": 10,
                        "op_id": "op_old",
                        "action": "replace",
                        "source_statute": "2022/100",
                        "source_title": "Older exact amendment",
                        "target_unit_kind": "section",
                        "target_norm": "9",
                        "target_chapter": "2",
                    },
                    {
                        "sequence": 20,
                        "op_id": "op_new",
                        "action": "replace",
                        "source_statute": "2026/200",
                        "source_title": "Newer child amendment",
                        "target_unit_kind": "section",
                        "target_norm": "9",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["apply_mutation_events"] = [
                {
                    "source_statute": "2022/100",
                    "op_id": "op_old",
                    "outcome": "applied",
                },
                {
                    "source_statute": "2026/200",
                    "op_id": "op_new",
                    "outcome": "applied",
                },
            ]
        return _fake_master_with_part_section()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(
        Namespace(
            statute_id="2023/703",
            address="part:1/chapter:2/section:9",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )

    payload = _json.loads(capsys.readouterr().out)
    [row] = payload["provisions"]
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["source_statute"] == "2026/200"
    assert row["last_op"]["op_id"] == "op_new"


def test_blame_rejects_ambiguous_part_suffix_match(monkeypatch, capsys) -> None:
    """A chapter/section op without part scope must not pick between duplicate parts."""

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "sequence": 7,
                    "action": "insert",
                    "source_statute": "2026/376",
                    "source_title": "Amending act",
                    "target_unit_kind": "section",
                    "target_norm": "9",
                    "target_chapter": "2",
                }
            )
        return _fake_master_with_ambiguous_part_sections()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(
        Namespace(
            statute_id="2023/703",
            address="part:1/chapter:2/section:9",
            source=None,
            mode="official_consolidation",
        )
    )

    out = capsys.readouterr().out
    assert "2026/376" not in out
    assert "unmodified — base statute text" in out


def test_blame_never_reports_unmodified_when_timeline_broken(monkeypatch, capsys) -> None:
    """A timeline break must replace 'unmodified' with an explicit unverifiable state."""

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = [
                {"statute_id": "2025/1382", "effective_date": "2026-01-01"}
            ]
        return _fake_master_with_section(
            findings=(_occupancy_violation_finding(target_label=None),)
        )

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(Namespace(statute_id="2014/1429", address="section:30", source=None, mode="official_consolidation"))

    out = capsys.readouterr().out
    assert "TIMELINE BROKEN at 2025/1382" in out
    assert "APPLY.OCCUPANCY_POLICY_VIOLATION" in out
    assert "UNVERIFIABLE after 2025/1382" in out
    assert "unmodified" not in out


def test_blame_keeps_unmodified_wording_without_breaks(monkeypatch, capsys) -> None:
    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(Namespace(statute_id="2014/1429", address="section:30", source=None, mode="official_consolidation"))

    out = capsys.readouterr().out
    assert "unmodified — base statute text" in out
    assert "TIMELINE BROKEN" not in out


def test_blame_marks_failed_op_targets_unverifiable(monkeypatch, capsys) -> None:
    from lawvm.core.phase_result import Finding

    failed = Finding(
        kind="APPLY.FAILED_OPERATION",
        role="obligation",
        stage="process_muutoslaki",
        source_statute="",
        detail={
            "amendment_id": "2022/378",
            "reason_code": "section_not_found",
            "target_unit_kind": "section",
            "target_section": "30",
            "target_chapter": None,
        },
        blocking=True,
    )

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section(findings=(failed,))

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)
    blame.main(Namespace(statute_id="2010/1326", address="section:30", source=None, mode="official_consolidation"))

    out = capsys.readouterr().out
    assert "FAILED OPS: 1 compiled op(s) could not be applied" in out
    assert "op from 2022/378 FAILED to apply (section_not_found)" in out
    assert "unmodified" not in out


def test_blame_main_suppresses_raw_replay_failed_chatter_for_1978_38(monkeypatch, capsys) -> None:
    def fake_replay_xml(
        statute_id: str,
        *,
        mode: str,
        quiet: bool = False,
        compiled_ops_out=None,
        replay_meta_out=None,
    ):
        print("REPLACE 10 luku otsikko → FAILED")
        print("INSERT 10 luku 16 § 2 mom → FAILED")
        return SimpleNamespace(
            title="Noisy replay",
            ir=IRNode(
                kind=IRNodeKind.BODY,
                children=(IRNode(kind=IRNodeKind.SECTION, label="1e", text="Section text"),),
            ),
            findings=(),
        )

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay_xml)

    blame.main(
        Namespace(
            statute_id="1991/1",
            address="section:1e",
            source=None,
            mode="legal_pit",
        )
    )

    out = capsys.readouterr().out

    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out


# --- typed per-address status enum -------------------------------------------
# One synthetic case per enum value. The status field is the single source of
# truth; these pin its derivation directly off the JSON rows.

import json as _json


def _failed_op_finding(amendment_id: str = "2022/378", section: str = "30"):
    from lawvm.core.phase_result import Finding

    return Finding(
        kind="APPLY.FAILED_OPERATION",
        role="obligation",
        stage="process_muutoslaki",
        source_statute="",
        detail={
            "amendment_id": amendment_id,
            "reason_code": "section_not_found",
            "target_unit_kind": "section",
            "target_section": section,
            "target_chapter": None,
        },
        blocking=True,
    )


def _blame_json(monkeypatch, capsys, *, statute_id, address, fake_replay) -> dict:
    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay)
    blame.main(
        Namespace(
            statute_id=statute_id,
            address=address,
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )
    return _json.loads(capsys.readouterr().out)


def test_status_unmodified_base_text(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section()

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "unmodified_base_text"
    assert "last_op" not in row
    assert "broken_at" not in row


def test_status_modified_by_op(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "sequence": 1,
                    "op_id": "op_0",
                    "action": "replace",
                    "source_statute": "2025/1382",
                    "source_title": "Amending act",
                    "target_unit_kind": "section",
                    "target_norm": "30",
                    "target_chapter": "6",
                }
            )
        return _fake_master_with_section()

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["source_statute"] == "2025/1382"
    assert row["last_op"]["op_id"] == "op_0"
    assert "broken_at" not in row


def test_status_modified_by_op_ignores_later_skipped_apply_event(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "sequence": 1,
                        "op_id": "op_0",
                        "action": "insert",
                        "source_statute": "2026/376",
                        "source_title": "Amending act",
                        "target_unit_kind": "section",
                        "target_norm": "30",
                        "target_chapter": "6",
                    },
                    {
                        "sequence": 2,
                        "op_id": "op_1",
                        "action": "renumber",
                        "source_statute": "2026/376",
                        "source_title": "Amending act",
                        "target_unit_kind": "section",
                        "target_norm": "30",
                        "target_chapter": "6",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["apply_mutation_events"] = [
                {
                    "source_statute": "2026/376",
                    "op_id": "op_0",
                    "outcome": "applied",
                },
                {
                    "source_statute": "2026/376",
                    "op_id": "op_1",
                    "outcome": "skipped",
                },
            ]
        return _fake_master_with_section()

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["op_id"] == "op_0"
    assert row["last_op"]["action"] == "insert"


def test_status_modified_by_op_prefers_same_source_content_touch_over_relabel(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.extend(
                [
                    {
                        "sequence": 1,
                        "op_id": "op_0",
                        "action": "insert",
                        "source_statute": "2026/376",
                        "source_title": "Amending act",
                        "target_unit_kind": "section",
                        "target_norm": "30",
                        "target_chapter": "6",
                    },
                    {
                        "sequence": 2,
                        "op_id": "op_1",
                        "action": "renumber",
                        "source_statute": "2026/376",
                        "source_title": "Amending act",
                        "target_unit_kind": "section",
                        "target_norm": "30",
                        "target_chapter": "6",
                    },
                ]
            )
        if replay_meta_out is not None:
            replay_meta_out["apply_mutation_events"] = [
                {
                    "source_statute": "2026/376",
                    "op_id": "op_0",
                    "outcome": "applied",
                },
                {
                    "source_statute": "2026/376",
                    "op_id": "op_1",
                    "outcome": "applied",
                },
            ]
        return _fake_master_with_section()

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["op_id"] == "op_0"
    assert row["last_op"]["action"] == "insert"


def test_status_op_unapplied_address_scope_failed_op(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section(findings=(_failed_op_finding(),))

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2010/1326", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "op_unapplied_or_engine_error"
    assert row["broken_at"] == "2022/378"


def test_status_op_unapplied_precedence_over_modified_by_op(monkeypatch, capsys) -> None:
    """A statute-scope break is terminal: it overrides modified_by_op, and the
    attributed op stays listed (modification proven, current state unproven)."""

    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if compiled_ops_out is not None:
            compiled_ops_out.append(
                {
                    "sequence": 7,
                    "action": "replace",
                    "source_statute": "2020/100",
                    "source_title": "Earlier amendment",
                    "target_unit_kind": "section",
                    "target_norm": "30",
                    "target_chapter": "6",
                }
            )
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = [
                {"statute_id": "2025/1382", "effective_date": "2026-01-01"}
            ]
        return _fake_master_with_section(
            findings=(_occupancy_violation_finding(target_label=None),)
        )

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "op_unapplied_or_engine_error"
    assert row["broken_at"] == "2025/1382"
    # the proven 2020 modification is still carried
    assert row["last_op"]["source_statute"] == "2020/100"


def test_status_address_unresolved(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section()

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:999", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "address_unresolved"
    assert row["address"] == "section:999"


def test_status_address_unresolved_prefers_unverifiable_under_break(monkeypatch, capsys) -> None:
    """With a statute break present, an unresolved address is unprovable —
    prefer op_unapplied_or_engine_error (mirrors provision-state)."""

    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = [
                {"statute_id": "2025/1382", "effective_date": "2026-01-01"}
            ]
        return _fake_master_with_section(
            findings=(_occupancy_violation_finding(target_label=None),)
        )

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:999", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["blame_status"] == "op_unapplied_or_engine_error"
    assert row["broken_at"] == "2025/1382"


def test_status_human_text_derived_from_enum(monkeypatch, capsys) -> None:
    """The text renderer drives off the same rows: a clean unbroken section
    keeps the 'unmodified — base statute text' wording."""

    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section()

    monkeypatch.setattr("lawvm.tools.blame.replay_xml", fake_replay)
    blame.main(
        Namespace(
            statute_id="2014/1429",
            address="section:30",
            source=None,
            mode="official_consolidation",
            format="text",
        )
    )
    assert "unmodified — base statute text" in capsys.readouterr().out


# --- live-corpus specimen regression -------------------------------------------
# Consumer-reported specimen: blame 2010/1326 --address section:22 used to say
# "unmodified — base statute text" although the oracle shows 2025/2026-amended
# text (compiled-op shape drift made the blame map empty, and timeline breaks
# at 2022/1282+ were not surfaced). The assertions are consistency-based so the
# test stays green if/when the underlying replay breaks are fixed.

from pathlib import Path

import pytest

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_blame_2010_1326_section_22_never_reads_unmodified(capsys) -> None:
    blame.main(
        Namespace(
            statute_id="2010/1326",
            address="section:22",
            source=None,
            mode="official_consolidation",
        )
    )
    out = capsys.readouterr().out
    # §22 is amended (2022/581, 2025/113 in the compiled timeline); under a
    # broken timeline it must read unverifiable instead. Either way the output
    # must NEVER assert "unmodified — base statute text" for it.
    assert "22 §" in out
    assert "unmodified — base statute text" not in out
    if "TIMELINE BROKEN" in out:
        assert "UNVERIFIABLE" in out or "[" in out  # annotated op or unverifiable block


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_blame_2014_1429_section_30_attributes_amendment(capsys) -> None:
    blame.main(
        Namespace(
            statute_id="2014/1429",
            address="section:30",
            source=None,
            mode="official_consolidation",
        )
    )
    out = capsys.readouterr().out
    # 2025/1382's johtolause says "muutetaan … 30–32 §" and the op compiles:
    # blame must attribute §30 to 2025/1382, not claim base-statute text.
    assert "2025/1382" in out
    assert "unmodified — base statute text" not in out


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_status_2014_1429_section_30_modified_by_op(capsys) -> None:
    """Live: 2014/1429 replays clean since the move-rider occupancy fix, so §30
    is op-attributed (2025/1382 REPLACE) with no break — modified_by_op. This
    pins both the healed timeline and the JSON attribution; break precedence
    is covered by the synthetic tests above."""
    blame.main(
        Namespace(
            statute_id="2014/1429",
            address="section:30",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )
    payload = _json.loads(capsys.readouterr().out)
    assert payload["timeline_breaks"] == []
    [row] = payload["provisions"]
    assert row["blame_status"] == "modified_by_op"
    assert "broken_at" not in row
    assert row["last_op"]["source_statute"] == "2025/1382"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_blame_2023_703_section_9_attributes_2026_376(capsys) -> None:
    """Consumer-reported MeVM specimen: §9 must not be a grounded negative.

    The compiled 2026/376 ops target chapter:2/section:9 while replayed IR keys
    include the enclosing part. Blame should conservatively attribute the unique
    suffix match instead of reporting unmodified_base_text.
    """
    blame.main(
        Namespace(
            statute_id="2023/703",
            address="section:9",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )
    payload = _json.loads(capsys.readouterr().out)
    [row] = payload["provisions"]
    assert row["address"] == "part:1/chapter:2/section:9"
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["source_statute"] == "2026/376"
    assert row["last_op"]["action"] == "insert"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_blame_1997_1412_section_11_attributes_later_child_op(capsys) -> None:
    """MeVM notice specimen: later subsection/item ops must govern blame.

    The 2022 whole-section replacement has exact chapter context, but 2026/26
    later replaces child targets inside §11 with only section-level source
    scope. The suffix is unique in the replayed tree, so the later applied child
    op is the correct last-touch attribution for the section.
    """
    blame.main(
        Namespace(
            statute_id="1997/1412",
            address="section:11",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )
    payload = _json.loads(capsys.readouterr().out)
    [row] = payload["provisions"]
    assert row["address"] == "chapter:2/section:11"
    assert row["blame_status"] == "modified_by_op"
    assert row["last_op"]["source_statute"] == "2026/26"
    assert row["last_op"]["action"] == "replace"
    assert row["last_op"]["op_id"] == "op_8"


@pytest.mark.skipif(not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus not available")
def test_specimen_status_unbroken_statute_unmodified_base_text(capsys) -> None:
    """Live: an untouched section of an unbroken statute (perustuslaki 1999/731
    §1:2) is the grounded negative → unmodified_base_text, no break, no op."""
    blame.main(
        Namespace(
            statute_id="1999/731",
            address="chapter:1/section:2",
            source=None,
            mode="official_consolidation",
            format="json",
        )
    )
    payload = _json.loads(capsys.readouterr().out)
    assert payload["timeline_breaks"] == []
    [row] = payload["provisions"]
    assert row["blame_status"] == "unmodified_base_text"
    assert "last_op" not in row
