from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from lawvm.tools.step_attribution import (
    StepAttributionResult,
    _print_single,
    _run_single,
    _save_corpus_csv,
    _section_keys_from_recovery_findings,
)


def test_section_keys_from_recovery_findings_uses_section_scoped_detail() -> None:
    findings = [
        SimpleNamespace(
            kind="APPLY.UNCOVERED_BODY_RECOVERY",
            detail={
                "target_unit_kind": "section",
                "target_norm": "14",
                "target_chapter": "5",
            },
        ),
        SimpleNamespace(
            kind="APPLY.FALLBACK_WHOLE_SECTION_REPLACE",
            detail={
                "target_unit_kind": "section",
                "target_norm": "21 a",
            },
        ),
    ]

    assert _section_keys_from_recovery_findings(findings) == {
        "chapter:5/section:14",
        "section:21a",
    }


def test_section_keys_from_recovery_findings_ignores_nonsection_and_unknown() -> None:
    findings = [
        SimpleNamespace(
            kind="APPLY.UNCOVERED_BODY_RECOVERY",
            detail={
                "target_unit_kind": "chapter",
                "target_norm": "5",
            },
        ),
        SimpleNamespace(
            kind="APPLY.LEGACY_DISPATCH_FALLBACK",
            detail={
                "target_unit_kind": "section",
                "target_norm": "14",
            },
        ),
    ]

    assert _section_keys_from_recovery_findings(findings) == set()


def test_print_single_omits_recovered_split(capsys) -> None:
    result = StepAttributionResult(
        statute_id="2000/1",
        n_amendments=1,
        n_compiled_ops=3,
        n_canonical_ops=3,
        n_failed_ops=1,
        n_sections_match=2,
        n_content_drift=1,
        n_replay_extra=0,
        n_replay_missing=0,
        n_sections_total=3,
        overall_score=0.9,
        attr_extraction_pct=100.0,
        attr_application_pct=0.0,
        attr_oracle_pct=0.0,
        attr_unknown_pct=0.0,
    )

    _print_single(result, verbose=False)
    out = capsys.readouterr().out

    assert "Compilation   : 3 canonical  1 failed" in out
    assert "recovered" not in out


def test_save_corpus_csv_omits_n_recovered_ops(tmp_path: Path) -> None:
    result = StepAttributionResult(
        statute_id="2000/1",
        n_amendments=1,
        n_compiled_ops=3,
        n_canonical_ops=3,
        n_failed_ops=1,
        n_sections_match=2,
        n_content_drift=1,
        n_replay_extra=0,
        n_replay_missing=0,
        n_sections_total=3,
        overall_score=0.9,
        attr_extraction_pct=100.0,
        attr_application_pct=0.0,
        attr_oracle_pct=0.0,
        attr_unknown_pct=0.0,
    )
    path = tmp_path / "step_attr.csv"

    _save_corpus_csv([result], path)

    header = path.read_text().splitlines()[0]
    assert "n_recovered_ops" not in header


def test_run_single_prefers_typed_source_adjudication_over_conflicting_replay_meta(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=SimpleNamespace(
            lineage=(
                {"statute_id": "1993/805", "included": True, "effective_date": "1993-01-01"},
            )
        ),
        findings=(),
        materialized_state=SimpleNamespace(ir=object()),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id, mode="finlex_oracle", compiled_ops_out=None, replay_meta_out=None, lo_ops_out=None, failed_ops_out=None: (
            replay_meta_out.update(
                {"lineage": [{"statute_id": "2000/999", "included": False, "effective_date": ""}]}
            ) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr("lawvm.finland.grafter.get_ground_truth_tree", lambda statute_id: object())
    monkeypatch.setattr("lawvm.tools.step_attribution._extract_replay_sections", lambda _ir: {})
    monkeypatch.setattr("lawvm.tools.step_attribution._extract_oracle_sections", lambda _root: {})
    monkeypatch.setattr("lawvm.tools.step_attribution.reconcile_unique_unscoped_aliases", lambda a, b: (a, b))

    result = _run_single("1990/1295")

    assert result.error == ""
    assert result.n_amendments == 1


def test_run_single_hydrates_source_adjudication_from_replay_meta(monkeypatch) -> None:
    fake_master = SimpleNamespace(
        source_adjudication=None,
        findings=(),
        materialized_state=SimpleNamespace(ir=object()),
    )

    monkeypatch.setattr(
        "lawvm.finland.grafter.replay_xml",
        lambda statute_id, mode="finlex_oracle", compiled_ops_out=None, replay_meta_out=None, lo_ops_out=None, failed_ops_out=None: (
            replay_meta_out.update(
                {
                    "lineage": [
                        {"statute_id": "1993/805", "included": True, "effective_date": "1993-01-01"},
                        {"statute_id": "1994/1000", "included": False, "effective_date": ""},
                    ],
                    "oracle_version_amendment_id": "raw-mid",
                }
            ) if replay_meta_out is not None else None,
            fake_master,
        )[-1],
    )
    monkeypatch.setattr("lawvm.finland.grafter.get_ground_truth_tree", lambda statute_id: object())
    monkeypatch.setattr("lawvm.tools.step_attribution._extract_replay_sections", lambda _ir: {})
    monkeypatch.setattr("lawvm.tools.step_attribution._extract_oracle_sections", lambda _root: {})
    monkeypatch.setattr("lawvm.tools.step_attribution.reconcile_unique_unscoped_aliases", lambda a, b: (a, b))

    result = _run_single("1990/1295")

    assert result.error == ""
    assert result.n_amendments == 2
