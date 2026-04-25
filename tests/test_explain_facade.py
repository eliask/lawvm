"""Tests for CompileFacade integration in lawvm explain.

Verifies that _build_facade_from_replay correctly packages replay out-params
into a CompileFacade, and that _print_facade_summary writes expected lines.

Run:
    uv run pytest tests/test_explain_facade.py -v
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lawvm.core.compile_facade import CompileFacade
from lawvm.core.compile_result import CanonicalBundle
from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding, PhaseBuilder, PhaseResult
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.tools.explain import (
    _diagnose,
    _explain_sync,
    _oracle_selector_from_args,
    _print_compile_summary,
    _print_facade_summary,
    _print_temporal_debug_block,
    main as explain_main,
)


# ---------------------------------------------------------------------------
# Minimal stub types to avoid importing heavy grafter internals
# ---------------------------------------------------------------------------


@dataclass
class _StubProjectionRow:
    kind: str = "some_adjudication"
    message: str = "test message"
    source_statute: str = "2020/1"
    detail: dict[str, object] | None = None


@dataclass
class _StubFailedOp:
    amendment_id: str = "2021/2"
    description: str = "test op"
    reason: str = "unresolved_target"


def _build_facade_from_replay(
    *,
    failed_ops: list,
    projection_rows: list,
    replay_mode: str,
) -> CompileFacade:
    """Local test helper mirroring explain.py's replay-to-facade packaging."""
    builder = PhaseBuilder()
    for row in projection_rows:
        row_detail = {}
        raw_detail = getattr(row, "detail", {})
        if isinstance(raw_detail, dict):
            row_detail.update(raw_detail)
        row_detail.setdefault("message", str(getattr(row, "message", "")))
        row_detail.setdefault("source_statute", str(getattr(row, "source_statute", "")))
        kind = str(getattr(row, "kind", "unknown"))
        spec = get_finding_spec(kind)
        if spec is not None and spec.is_barrier:
            row_detail.setdefault("barrier_code", kind)
            builder.add_findings(
                (
                    Finding(
                        kind="RUNTIME.VIOLATION",
                        role="violation",
                        stage="replay_xml",
                        detail=row_detail,
                        blocking=True,
                        source_statute=str(getattr(row, "source_statute", "")),
                    ),
                )
            )
        elif spec is not None and spec.role == "violation":
            row_detail.setdefault("barrier_code", kind)
            builder.add_findings(
                (
                    Finding(
                        kind=kind,
                        role="violation",
                        stage="replay_xml",
                        detail=row_detail,
                        blocking=True,
                        source_statute=str(getattr(row, "source_statute", "")),
                    ),
                )
            )
        elif spec is not None and spec.is_obligation:
            builder.oblige(kind=kind, stage="replay_xml", detail=row_detail, blocking=True)
        else:
            builder.observe(kind=kind, stage="replay_xml", detail=row_detail)
    for fop in failed_ops:
        builder.add_findings(
            (
                Finding(
                    kind="APPLY.FAILED_OPERATION",
                    role="obligation",
                    stage="replay_xml",
                    detail={
                        "amendment_id": str(getattr(fop, "amendment_id", "")),
                        "description": str(getattr(fop, "description", "")),
                        "reason": str(getattr(fop, "reason", "")),
                        "barrier_code": "APPLY.FAILED_OPERATION",
                    },
                    blocking=True,
                ),
            )
        )
    phase_result = PhaseResult(
        output=None,
        findings=builder.finish(None).findings(),
    )
    return CompileFacade.from_phase_result(phase_result, replay_mode=replay_mode)


# ---------------------------------------------------------------------------
# _build_facade_from_replay
# ---------------------------------------------------------------------------


class TestBuildFacadeFromReplay:
    def test_empty_lists_produce_clean_facade(self):
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )
        assert isinstance(facade, CompileFacade)
        assert facade.replay_mode == "finlex_oracle"
        assert facade.finding_ledger == ()
        assert facade.bundle.structural_ops == ()

    def test_replay_mode_stored(self):
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="legal_pit",
        )
        assert facade.replay_mode == "legal_pit"

    def test_projection_row_violation_is_projected_as_runtime_violation(self):
        adj = _StubProjectionRow(kind="APPLY.TREE_INVARIANT_VIOLATION", message="boom")
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[adj],
            replay_mode="finlex_oracle",
        )
        violations = tuple(f for f in facade.finding_ledger if f.role == "violation")
        assert len(violations) == 1
        assert violations[0].kind == "APPLY.TREE_INVARIANT_VIOLATION"
        assert violations[0].detail.get("barrier_code") == "APPLY.TREE_INVARIANT_VIOLATION"

    def test_failed_op_becomes_blocking_obligation(self):
        fop = _StubFailedOp(amendment_id="2022/3", reason="unresolved_target")
        facade = _build_facade_from_replay(
            failed_ops=[fop],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )
        obligations = tuple(f for f in facade.finding_ledger if f.role == "obligation")
        assert len(obligations) == 1
        assert obligations[0].kind == "APPLY.FAILED_OPERATION"
        assert obligations[0].detail.get("barrier_code") == "APPLY.FAILED_OPERATION"
        assert obligations[0].blocking is True
        assert obligations[0].detail.get("amendment_id") == "2022/3"

    def test_projection_row_detail_is_preserved_on_observation(self):
        adj = _StubProjectionRow(
            kind="PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER",
            message="destinationless move/relabel observed",
            detail={
                "collapse_kind": "destinationless_move_relabel",
                "destination_missing": True,
            },
        )
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[adj],
            replay_mode="finlex_oracle",
        )
        obs = tuple(f for f in facade.finding_ledger if f.role == "observation")[0]
        assert obs.kind == "PARSE.SEMANTIC_COLLAPSE_MOVE_RENUMBER"
        assert obs.detail.get("collapse_kind") == "destinationless_move_relabel"
        assert obs.detail.get("destination_missing") is True
        assert obs.detail.get("message") == "destinationless move/relabel observed"
        assert obs.detail.get("source_statute") == "2020/1"

    def test_projection_row_and_failed_op_accumulate(self):
        adj = _StubProjectionRow(
            kind="APPLY.TREE_INVARIANT_VIOLATION",
            message="boom",
        )
        fop = _StubFailedOp()
        facade = _build_facade_from_replay(
            failed_ops=[fop],
            projection_rows=[adj],
            replay_mode="finlex_oracle",
        )
        assert len([f for f in facade.finding_ledger if f.role == "violation"]) == 1
        assert len([f for f in facade.finding_ledger if f.role == "obligation"]) == 1

    def test_public_facade_stays_temporal_event_only(self):
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )
        assert facade.bundle.temporal_events == ()

    def test_no_obligations_strict(self):
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )
        assert facade.has_blocking is False

    def test_blocking_obligation_fails_strict(self):
        adj = _StubProjectionRow(kind="APPLY.TREE_INVARIANT_VIOLATION")
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[adj],
            replay_mode="finlex_oracle",
        )
        assert facade.has_blocking is True

    def test_facade_is_frozen(self):
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )
        with pytest.raises((AttributeError, TypeError)):
            setattr(facade, "replay_mode", "other")

# ---------------------------------------------------------------------------
# _print_facade_summary
# ---------------------------------------------------------------------------


def _capture_facade_summary(facade: CompileFacade) -> str:
    """Capture _print_facade_summary output as a string."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        _print_facade_summary(facade)
    return buf.getvalue()


def _capture_compile_summary(
    *,
    report_record: object,
) -> str:
    """Capture _print_compile_summary output as a string."""
    buf = io.StringIO()
    with patch("sys.stdout", buf):
        _print_compile_summary(
            report_record=report_record,
        )
    return buf.getvalue()


class TestPrintFacadeSummary:
    def _clean_facade(self) -> CompileFacade:
        return _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[],
            replay_mode="finlex_oracle",
        )

    def test_strict_yes_in_output(self):
        facade = self._clean_facade()
        out = _capture_facade_summary(facade)
        assert "strict=YES" in out

    def test_strict_no_when_blocking(self):
        adj = _StubProjectionRow(kind="APPLY.TREE_INVARIANT_VIOLATION")
        facade = _build_facade_from_replay(
            failed_ops=[],
            projection_rows=[adj],
            replay_mode="finlex_oracle",
        )
        out = _capture_facade_summary(facade)
        assert "strict=NO" in out

    def test_observation_count_in_output(self):
        facade = self._clean_facade()
        out = _capture_facade_summary(facade)
        assert "findings=0" in out

    def test_temporal_events_count_in_output(self):
        facade = self._clean_facade()
        out = _capture_facade_summary(facade)
        assert "temporal_events=0" in out

    def test_quirks_count_in_output(self):
        facade = self._clean_facade()
        out = _capture_facade_summary(facade)
        assert "quirks_used=0" in out

    def test_source_completeness_count_in_output(self):
        facade = self._clean_facade()
        out = _capture_facade_summary(facade)
        assert "source_completeness_issues=0" in out

    def test_quirks_line_shown_when_quirks_present(self):
        # Build a PhaseResult with a quirks observation, then facade it
        from lawvm.core.phase_result import PhaseBuilder

        builder = PhaseBuilder()
        builder.observe(
            kind="ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
            stage="test",
            detail={},
        )
        pr = builder.finish(None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="finlex_oracle")
        out = _capture_facade_summary(facade)
        assert "Quirks" in out
        assert "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE" in out

    def test_sc_issues_line_shown_when_sc_issues_present(self):
        from lawvm.core.phase_result import PhaseBuilder

        builder = PhaseBuilder()
        builder.observe(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="test",
            detail={},
        )
        pr = builder.finish(None)
        facade = CompileFacade.from_phase_result(pr, replay_mode="finlex_oracle")
        out = _capture_facade_summary(facade)
        assert "SC issues" in out


class TestPrintCompileSummary:
    def test_summary_uses_single_canonical_op_count(self) -> None:
        report_record = SimpleNamespace(
            canonical_ops=[
                SimpleNamespace(op_id="replace_1"),
                SimpleNamespace(op_id="uncovered_replace_2"),
            ],
            failed_ops=[SimpleNamespace(reason="x")],
            projection_rows=lambda: (),
            strict_fail_reasons=[],
        )

        out = _capture_compile_summary(report_record=report_record)

        assert "canonical=2" in out
        assert "failed=1" in out
        assert "recovered=" not in out

    def test_adjudication_target_detail_is_rendered(self):
        from lawvm.core.phase_result import PhaseBuilder

        builder = PhaseBuilder()
        builder.add_findings(
            (
                Finding(
                    kind="RUNTIME.VIOLATION",
                    role="violation",
                    stage="replay_xml",
                    detail={
                        "message": "Compilation required context-dependent anchor resolution.",
                        "source_statute": "2020/1",
                        "tag": "chapter_scope_from_johtolause",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "5",
                        "barrier_code": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    },
                    blocking=True,
                ),
            )
        )
        facade = CompileFacade.from_phase_result(
            builder.finish(CanonicalBundle()),
            replay_mode="legal_pit",
        )
        report_record = SimpleNamespace(
            canonical_ops=[],
            failed_ops=[],
            projection_rows=lambda: (
                {
                    "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    "message": "Compilation required context-dependent anchor resolution.",
                    "source": "2020/1",
                    "detail": {
                        "tag": "chapter_scope_from_johtolause",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "5",
                    },
                },
            ),
            source_adjudication=None,
            strict_fail_reasons=list(facade.to_wire_artifact().status.blockers or []),
        )
        out = _capture_compile_summary(report_record=report_record)

        assert "Projection rows: LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in out
        assert (
            "- LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION  [target(kind=P, norm=35, chapter=5); tag=chapter_scope_from_johtolause]"
            in out
        )

    def test_source_pathology_detail_and_summary_are_rendered(self):
        from lawvm.core.phase_result import PhaseBuilder

        builder = PhaseBuilder()
        builder.observe(
            kind="ELAB.SOURCE_PATHOLOGY",
            stage="replay_xml",
            detail={
                "message": "Replay encountered a source pathology.",
                "source_statute": "2001/748",
                "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                "target_unit_kind": "section",
                "target_label": "6 §",
                "diagnostic_reason": "partial_body_only",
            },
        )
        facade = CompileFacade.from_phase_result(
            builder.finish(CanonicalBundle()),
            replay_mode="legal_pit",
        )
        report_record = SimpleNamespace(
            canonical_ops=[],
            failed_ops=[],
            projection_rows=lambda: (
                {
                    "kind": "ELAB.SOURCE_PATHOLOGY",
                    "message": "Replay encountered a source pathology.",
                    "source": "2001/748",
                    "detail": {
                        "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                        "target_unit_kind": "section",
                        "target_label": "6 §",
                        "diagnostic_reason": "partial_body_only",
                    },
                },
            ),
            source_pathologies=(
                {
                    "code": "DESTRUCTIVE_SHAPE_LOSS_RISK",
                    "message": "Replay encountered a source pathology.",
                    "source_statute": "2001/748",
                    "target_unit_kind": "section",
                    "target_label": "6 §",
                    "detail": {"diagnostic_reason": "partial_body_only"},
                },
            ),
            strict_fail_reasons=list(facade.to_wire_artifact().status.blockers or []),
        )
        out = _capture_compile_summary(report_record=report_record)

        assert "Projection rows: ELAB.SOURCE_PATHOLOGY" in out
        assert (
            "- ELAB.SOURCE_PATHOLOGY  [code=DESTRUCTIVE_SHAPE_LOSS_RISK; target(kind=P); target_label=6 §; diagnostic_reason=partial_body_only]"
            in out
        )
        assert "Source pathologies: DESTRUCTIVE_SHAPE_LOSS_RISK" in out


class TestPrintTemporalDebugBlock:
    def test_temporal_events_print_when_present(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_temporal_debug_block(
                temporal_events=("event-1",),
            )
        out = buf.getvalue()
        assert "TemporalEvents (1):" in out
        assert "event-1" in out

    def test_temporal_debug_block_is_silent_when_no_events(self):
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            _print_temporal_debug_block(temporal_events=())
        out = buf.getvalue()
        assert out == ""


def test_explain_main_ignores_removed_effect_intents_flag(monkeypatch) -> None:
    called: list[SimpleNamespace] = []

    def _fake_explain_sync(**kwargs) -> None:
        called.append(SimpleNamespace(**kwargs))

    monkeypatch.setattr("lawvm.tools.explain._explain_sync", _fake_explain_sync)

    args = SimpleNamespace(
        statute_id="1991/1",
        section=None,
        threshold=1.0,
        mode="finlex_oracle",
        compile_summary=False,
        facade=False,
        strict=False,
    )

    explain_main(args)
    assert len(called) == 1


def test_explain_main_uses_explicit_oracle_selector(monkeypatch) -> None:
    called: list[SimpleNamespace] = []

    def _fake_explain_sync(**kwargs) -> None:
        called.append(SimpleNamespace(**kwargs))

    monkeypatch.setattr("lawvm.tools.explain._explain_sync", _fake_explain_sync)

    args = SimpleNamespace(
        statute_id="1991/1",
        section=None,
        threshold=1.0,
        mode="finlex_oracle",
        oracle_selector_mode="bench_comparable",
        oracle_version_amendment_id="",
        compile_summary=False,
        facade=False,
        strict=False,
    )

    explain_main(args)

    assert len(called) == 1
    assert called[0].oracle_selector == ConsolidatedArtifactSelector.bench_comparable()


def test_diagnose_treats_bench_comparable_temporary_residue_stub_as_editorial() -> None:
    replay = "3 b § Perintäkulut"
    oracle = "3 b § 3 b § oli väliaikaisesti voimassa 1.7.2021–30.4.2022 L:lla 539/2021."

    diagnosis, explanation = _diagnose(
        replay,
        oracle,
        None,
        oracle_selector_mode="bench_comparable",
    )

    assert diagnosis == "EDITORIAL_CONVENTION"
    assert "temporary-law editorial residue" in explanation


def test_explain_sync_suppresses_raw_replay_failed_chatter_for_1978_38(capsys) -> None:
    _explain_sync(
        "1978/38",
        "chapter:12/section:1e",
        1.0,
        "legal_pit",
    )

    out = capsys.readouterr().out

    assert "REPLACE 10 luku otsikko → FAILED" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED" not in out
    assert "REPLACE 10 luku otsikko → FAILED (master chapter:10 not found)" not in out
    assert "INSERT 10 luku 16 § 2 mom → FAILED (master §16 not found)" not in out


def test_explain_sync_classifies_future_effective_missing_section_as_oracle_stale_for_2019_213(
    capsys,
) -> None:
    _explain_sync(
        "2019/213",
        "part:4/chapter:10/section:6",
        1.0,
        "finlex_oracle",
    )

    out = capsys.readouterr().out

    assert (
        "6 § — ORACLE_STALE" in out
        or "All sections at or above threshold (100%) — no divergence to explain" in out
    )
    if "6 § — ORACLE_STALE" in out:
        assert "MISSING from replay" not in out


def test_diagnose_classifies_moderate_extra_replay_text_as_replay_extra() -> None:
    replay = (
        "8 § Voimaantulo Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 "
        "ja on voimassa vuoden 2021 loppuun. Tämä asetus tulee voimaan 1 "
        "päivänä tammikuuta 2020."
    )
    oracle = (
        "8 § Voimaantulo Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 "
        "ja on voimassa vuoden 2023 loppuun."
    )

    diagnosis, explanation = _diagnose(
        replay,
        oracle,
        None,
        oracle_selector_mode="latest_cached_editorial",
    )

    assert diagnosis == "REPLAY_EXTRA"
    assert "significantly more content" in explanation


def test_diagnose_treats_same_section_oracle_duplicate_sentence_as_oracle_stale() -> None:
    replay = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )
    oracle = (
        "10 § Kohdeyhtiön julkistamisvelvollisuus Kun kohdeyhtiö saa liputusilmoituksen, "
        "sen on ilman aiheetonta viivytystä julkistettava liputusilmoituksessa olevat tiedot. "
        "Kohdeyhtiöllä ei ole julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. "
        "Julkistettaessa on myös mainittava, jos kohdeyhtiön tiedossa ei ole kaikkia "
        "liputusilmoituksen säädettyjä tietoja. Jos liputusilmoituksessa on lisäksi annettu "
        "muita tietoja, nämäkin tiedot on julkistettava samassa yhteydessä. Kohdeyhtiöllä ei ole "
        "julkistamisvelvollisuutta, ellei osakkeenomistajalla ole ilmoitusvelvollisuutta. Kohdeyhtiön on "
        "julkistettava liputusilmoitukseen sisältyvät tiedot sen oman omistus- tai ääniosuuden "
        "muutoksista 5–7 §:ssä tarkoitetulla tavalla ilman aiheetonta viivytystä."
    )

    diagnosis, explanation = _diagnose(
        replay,
        oracle,
        None,
        oracle_selector_mode="latest_cached_editorial",
    )

    assert diagnosis == "ORACLE_STALE"
    assert "duplicates one same-section sentence fragment" in explanation


def test_explain_sync_classifies_repeal_banner_missing_section_as_oracle_stale_for_2016_768(
    capsys,
) -> None:
    _explain_sync(
        "2016/768",
        "chapter:9/section:53",
        1.0,
        "finlex_oracle",
    )

    out = capsys.readouterr().out

    assert "53 § — ORACLE_STALE" in out
    assert "MISSING from replay" not in out


def test_explain_sync_demotes_2012_916_section_1_unknown_to_source_pathology(
    capsys,
) -> None:
    _explain_sync(
        "2012/916",
        "chapter:13/section:1",
        1.0,
        "finlex_oracle",
    )

    out = capsys.readouterr().out

    assert "Diagnosis    : SOURCE_PATHOLOGY" in out
    assert "ITEM_TARGET_STRUCTURE_ABSENT" in out
    assert "degraded uncovered-body coverage" in out


def test_oracle_selector_helper_prefers_explicit_version_id() -> None:
    selector = _oracle_selector_from_args(
        "bench_comparable",
        "2019/112",
    )

    assert selector == ConsolidatedArtifactSelector.exact_embedded_version("20190112")
