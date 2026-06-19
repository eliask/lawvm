"""Tests for lawvm.tools.bench_triage — the bench-divergence A/B/C triage.

These exercise the deterministic classification logic (diagnosis -> class,
statute verdict, closeable-fraction aggregation, worst-N CSV selection)
without invoking replay/oracle-check, so they are fast and hermetic.
"""

from __future__ import annotations

from pathlib import Path

from lawvm.tools.bench_triage import (
    CLASS_LABELS,
    SectionTriage,
    StatuteTriage,
    TriageReport,
    classify_diagnosis,
    load_worst_statutes,
)


def _sec(diagnosis: str, blame: str = "", section: str = "section:1") -> SectionTriage:
    tclass, just = classify_diagnosis(diagnosis, blame)
    return SectionTriage(
        section=section,
        diagnosis=diagnosis,
        triage_class=tclass,
        justification=just,
        blame_source=blame,
    )


# ---------------------------------------------------------------------------
# Diagnosis -> triage class
# ---------------------------------------------------------------------------


def test_oracle_diagnoses_map_to_B():
    assert classify_diagnosis("ORACLE_STALE", "2011/250")[0] == "B"
    assert classify_diagnosis("CORRIGENDUM_APPLIED", "")[0] == "B"


def test_ambiguous_and_source_limited_map_to_C():
    for diag in (
        "EDITORIAL_CONVENTION",
        "SOURCE_PATHOLOGY",
        "SOURCE_INCOMPLETE",
        "RECODIFICATION_SOURCE_CHAIN_GAP",
        "RECODIFICATION_OMISSION_ONLY_SECTION_SHELL",
    ):
        assert classify_diagnosis(diag, "")[0] == "C", diag


def test_unblamed_replay_bugs_map_to_A():
    for diag in ("REPLAY_MISSING", "MISSING", "REPLAY_EXTRA", "EXTRA", "UNKNOWN", "REPLAY_UNREPEALED"):
        assert classify_diagnosis(diag, "")[0] == "A", diag


def test_blamed_replay_bug_is_needs_human_not_A():
    # A parser-gap diagnosis that retains a blame source was not conclusively
    # promoted to ORACLE_STALE by oracle_check -> undecidable, never guess A.
    cls, just = classify_diagnosis("REPLAY_MISSING", "1999/213")
    assert cls == "needs_human"
    assert "1999/213" in just


def test_liite_diffs_are_needs_human():
    assert classify_diagnosis("LIITE_DIFF", "")[0] == "needs_human"
    assert classify_diagnosis("LIITE_BODY_DIFF", "")[0] == "needs_human"


def test_unmapped_diagnosis_fails_loud_to_needs_human():
    cls, just = classify_diagnosis("SOME_BRAND_NEW_DIAGNOSIS", "")
    assert cls == "needs_human"
    assert "SOME_BRAND_NEW_DIAGNOSIS" in just


# ---------------------------------------------------------------------------
# Statute-level verdict
# ---------------------------------------------------------------------------


def test_verdict_A_when_any_fixable_section_present():
    st = StatuteTriage(
        statute_id="x/1",
        similarity=0.5,
        amendments=3,
        sections=[_sec("ORACLE_STALE", "a/1"), _sec("REPLAY_MISSING")],
    )
    assert st.verdict == "A"
    assert st.fixable_sections == 1


def test_verdict_B_when_all_oracle_stale():
    st = StatuteTriage(
        statute_id="x/2",
        similarity=0.8,
        amendments=2,
        sections=[_sec("ORACLE_STALE", "a/1"), _sec("ORACLE_STALE", "a/2")],
    )
    assert st.verdict == "B"
    assert st.fixable_sections == 0


def test_verdict_needs_human_on_tie_between_B_and_C():
    st = StatuteTriage(
        statute_id="x/3",
        similarity=0.7,
        amendments=1,
        sections=[_sec("ORACLE_STALE", "a/1"), _sec("EDITORIAL_CONVENTION")],
    )
    assert st.verdict == "needs_human"


def test_verdict_error_passthrough():
    st = StatuteTriage(statute_id="x/4", similarity=-1.0, amendments=0, error="boom")
    assert st.verdict == "error"


def test_ev_score_weights_fixable_by_error():
    st = StatuteTriage(
        statute_id="x/5",
        similarity=0.6,  # 0.4 error
        amendments=10,
        sections=[_sec("REPLAY_MISSING", section="section:1"), _sec("MISSING", section="section:2")],
    )
    assert abs(st.ev_score - (2 * 0.4)) < 1e-9


# ---------------------------------------------------------------------------
# Report aggregation
# ---------------------------------------------------------------------------


def _report() -> TriageReport:
    statutes = [
        StatuteTriage(
            "a/1", 0.5, 3,
            sections=[_sec("MISSING", section="section:1"), _sec("ORACLE_STALE", "z/1", "section:2")],
        ),
        StatuteTriage(
            "a/2", 0.8, 2,
            sections=[_sec("ORACLE_STALE", "z/2", "section:1"), _sec("ORACLE_STALE", "z/3", "section:2")],
        ),
        StatuteTriage(
            "a/3", 0.7, 1,
            sections=[_sec("EDITORIAL_CONVENTION", section="section:1")],
        ),
    ]
    return TriageReport("dummy.csv", "official_consolidation", 3, statutes)


def test_report_section_class_counts():
    r = _report()
    cc = r.section_class_counts
    assert cc["A"] == 1
    assert cc["B"] == 3
    assert cc["C"] == 1


def test_report_closeable_fraction():
    r = _report()
    cf = r.closeable_fraction()
    assert cf["total_sections"] == 5
    assert abs(cf["closeable_fraction"] - 1 / 5) < 1e-9
    # upper bound (A + needs_human) == A here (no needs_human in fixture)
    assert abs(cf["closeable_fraction_hi"] - 1 / 5) < 1e-9


def test_report_a_list_is_ev_ranked():
    r = _report()
    a_list = r.a_list
    assert [s.statute_id for s in a_list] == ["a/1"]


def test_report_to_dict_is_json_clean():
    import json

    r = _report()
    d = r.to_dict()
    # round-trips through JSON without error
    json.loads(json.dumps(d, ensure_ascii=False))
    assert d["sample_size"] == 3
    assert d["closeable"]["A"] == 1


# ---------------------------------------------------------------------------
# Worst-N CSV selection
# ---------------------------------------------------------------------------


def test_load_worst_statutes_ranks_and_filters(tmp_path: Path):
    csv_text = (
        "amendments,statute_id,similarity,lev_similarity,status,elapsed_s\n"
        "1,perfect/1,1.000000,1.000000,OK,0.1\n"
        "2,no_truth/1,NO_TRUTH,NO_TRUTH,NO_TRUTH,0.1\n"
        "3,worst/1,0.500000,0.900000,OK,0.1\n"
        "4,mid/1,0.800000,0.950000,OK,0.1\n"
        "5,tie_a/1,0.800000,0.700000,OK,0.1\n"
    )
    runs = tmp_path / "bench_runs"
    runs.mkdir()
    (runs / "20260101T0000_run_test.csv").write_text(csv_text, encoding="utf-8")

    rows, path = load_worst_statutes("test", top=10, runs_dir=runs)
    ids = [r["statute_id"] for r in rows]
    # perfect (sim==1) and NO_TRUTH excluded; ranked ascending similarity,
    # lev tie-break (tie_a has lower lev than mid so comes first).
    assert ids == ["worst/1", "tie_a/1", "mid/1"]
    assert path.name == "20260101T0000_run_test.csv"


def test_load_worst_statutes_respects_top(tmp_path: Path):
    csv_text = (
        "amendments,statute_id,similarity,lev_similarity,status,elapsed_s\n"
        "1,a/1,0.500000,0.900000,OK,0.1\n"
        "1,b/1,0.600000,0.900000,OK,0.1\n"
        "1,c/1,0.700000,0.900000,OK,0.1\n"
    )
    runs = tmp_path / "bench_runs"
    runs.mkdir()
    (runs / "20260101T0000_run_test.csv").write_text(csv_text, encoding="utf-8")
    rows, _ = load_worst_statutes("test", top=2, runs_dir=runs)
    assert [r["statute_id"] for r in rows] == ["a/1", "b/1"]


def test_class_labels_cover_all_classes():
    assert set(CLASS_LABELS) == {"A", "B", "C", "needs_human"}
