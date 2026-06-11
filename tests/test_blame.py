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


def _fake_master_with_section(*, findings=()) -> SimpleNamespace:
    section = IRNode(kind=IRNodeKind.SECTION, label="30")
    chapter = IRNode(kind=IRNodeKind.CHAPTER, label="6", children=(section,))
    return SimpleNamespace(
        title="Synthetic statute",
        ir=IRNode(kind=IRNodeKind.BODY, children=(chapter,)),
        findings=tuple(findings),
    )


def _occupancy_violation_finding(amendment_id: str = "2025/1382"):
    from lawvm.core.phase_result import Finding

    return Finding(
        kind="APPLY.OCCUPANCY_POLICY_VIOLATION",
        role="observation",
        stage="apply",
        source_statute=amendment_id,
        detail={
            "target_label": "29e",
            "current_occupancy": "absent",
            "allowed_from": ["substantive", "tombstone"],
        },
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


def test_blame_never_reports_unmodified_when_timeline_broken(monkeypatch, capsys) -> None:
    """A timeline break must replace 'unmodified' with an explicit unverifiable state."""

    def fake_replay_xml(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = [
                {"statute_id": "2025/1382", "effective_date": "2026-01-01"}
            ]
        return _fake_master_with_section(findings=(_occupancy_violation_finding(),))

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


def test_blame_main_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    blame.main(
        Namespace(
            statute_id="1978/38",
            address="chapter:12/section:1e",
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
    assert row["status"] == "unmodified_base_text"
    assert "last_op" not in row
    assert "broken_at" not in row


def test_status_modified_by_op(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
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

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["status"] == "modified_by_op"
    assert row["last_op"]["source_statute"] == "2025/1382"
    assert "broken_at" not in row


def test_status_op_unapplied_address_scope_failed_op(monkeypatch, capsys) -> None:
    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        return _fake_master_with_section(findings=(_failed_op_finding(),))

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2010/1326", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["status"] == "op_unapplied_or_engine_error"
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
        return _fake_master_with_section(findings=(_occupancy_violation_finding(),))

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:30", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["status"] == "op_unapplied_or_engine_error"
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
    assert row["status"] == "address_unresolved"
    assert row["address"] == "section:999"


def test_status_address_unresolved_prefers_unverifiable_under_break(monkeypatch, capsys) -> None:
    """With a statute break present, an unresolved address is unprovable —
    prefer op_unapplied_or_engine_error (mirrors provision-state)."""

    def fake_replay(statute_id, *, mode, quiet=False, compiled_ops_out=None, replay_meta_out=None):
        if replay_meta_out is not None:
            replay_meta_out["lineage"] = [
                {"statute_id": "2025/1382", "effective_date": "2026-01-01"}
            ]
        return _fake_master_with_section(findings=(_occupancy_violation_finding(),))

    payload = _blame_json(
        monkeypatch, capsys, statute_id="2014/1429", address="section:999", fake_replay=fake_replay
    )
    [row] = payload["provisions"]
    assert row["status"] == "op_unapplied_or_engine_error"
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
def test_specimen_status_2014_1429_section_30_op_unapplied(capsys) -> None:
    """Live: §30 is op-attributed (2025/1382) under a statute break at 2025/1382
    → terminal status op_unapplied_or_engine_error, attributed op still listed."""
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
    [row] = payload["provisions"]
    assert row["status"] == "op_unapplied_or_engine_error"
    assert row["broken_at"] == "2025/1382"
    assert row["last_op"]["source_statute"] == "2025/1382"


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
    assert row["status"] == "unmodified_base_text"
    assert "last_op" not in row
