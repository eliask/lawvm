"""Hermetic tests for the ``fi-sweep`` VoI-staged corpus reliability driver.

No real archive / no vision: the per-PDF processor is a scripted stub returning
canned ``RowResult`` rows, and the per-locator stratum is an injected dict. Asserts:

  * the stages ESCALATE with SUPERSET-NESTED, ~stratum-proportional prefixes;
  * ``--dry-run`` plans deterministically (two runs identical);
  * the gate PROCEEDS on a clean widening and STOPS on an injected NUMERIC
    regression (dropping — never running — the next, larger tranche);
  * the A/B verdict round-trips through the OUTPUT FARCHIVE (resume-by-construction:
    a durable verdict reconstructs a bit-identical row, keyed on source×pipeline×gold);
  * the report shape + the ranked residual-defect-class table.
"""
from __future__ import annotations

import json

from lawvm.tools.fi_parse_corpus import CorpusMember, RowResult
from lawvm.tools.fi_sweep import (
    plan_stages,
    render_report,
    report_to_json,
    resolve_stages,
    run_sweep,
    stratified_order,
)

# --------------------------------------------------------------------------- #
# A synthetic corpus: 30 PDFs across three strata (deterministic locators).    #
# --------------------------------------------------------------------------- #

_STRATA = ("born_digital", "mixed", "scanned")


def _loc(stratum: str, i: int) -> str:
    # A real finlex-shaped media locator so it pairs with a main.xml sibling.
    return f"finlex://sd/2020/{stratum}-{i:03d}/fin/media/x.pdf"


def _members() -> list[CorpusMember]:
    members: list[CorpusMember] = []
    # 18 born_digital, 9 mixed, 3 scanned → a 6:3:1 proportion.
    for stratum, n in (("born_digital", 18), ("mixed", 9), ("scanned", 3)):
        for i in range(n):
            loc = _loc(stratum, i)
            members.append(CorpusMember(loc, loc.replace("/media/x.pdf", "/main.xml")))
    return members


def _stratum_map() -> dict[str, str]:
    smap: dict[str, str] = {}
    for stratum, n in (("born_digital", 18), ("mixed", 9), ("scanned", 3)):
        for i in range(n):
            smap[_loc(stratum, i)] = stratum
    return smap


def _stratum_of(smap: dict[str, str]):
    return lambda loc: smap[loc]


def _clean_row(loc: str) -> RowResult:
    """A clean improving A/B row: EXTRA down, no MISSING/NUMERIC regression → accepted."""
    return RowResult(
        pdf_locator=loc,
        xml_locator=loc.replace("/media/x.pdf", "/main.xml"),
        status="ab",
        baseline_extra=4,
        baseline_structure=2,
        baseline_missing=1,
        baseline_numeric=1,
        extra_delta=-2,
        structure_delta=-1,
        missing_delta=0,
        numeric_delta=0,
        accepted=True,
    )


def _numeric_regressed_row(loc: str) -> RowResult:
    """An A/B row that INTRODUCES a numeric-exact error (positive numeric_delta)."""
    return RowResult(
        pdf_locator=loc,
        xml_locator=loc.replace("/media/x.pdf", "/main.xml"),
        status="ab",
        baseline_extra=4,
        baseline_structure=2,
        baseline_missing=1,
        baseline_numeric=1,
        extra_delta=-2,
        structure_delta=-1,
        missing_delta=0,
        numeric_delta=1,  # a euro/section token corrupted → the primary-gate failure
        accepted=False,
    )


def _clean_processor():
    return lambda m: _clean_row(m.pdf_locator)


# --------------------------------------------------------------------------- #
# Stage ladder + stratified nesting.                                           #
# --------------------------------------------------------------------------- #


def test_resolve_stages_ascending_clamped_full() -> None:
    assert resolve_stages("10,50,200,1000,full", 30) == [10, 30]
    # 'full' expands to total; numerics clamp; strictly increasing dedupe.
    assert resolve_stages("5,5,10,full", 12) == [5, 10, 12]
    assert resolve_stages("", 7) == [7]
    assert resolve_stages("100", 7) == [7]


def test_stratified_order_prefixes_are_nested_and_proportional() -> None:
    members = _members()
    smap = _stratum_map()
    order = stratified_order(members, _stratum_of(smap))
    assert len(order) == 30
    # Any prefix is ~proportional: the first 10 should carry every stratum, with
    # born_digital dominating (6:3:1 → ~6 born, ~3 mixed, ~1 scanned in 10).
    first10 = [smap[m.pdf_locator] for m in order[:10]]
    assert set(first10) == set(_STRATA)  # all strata represented early
    assert first10.count("born_digital") >= first10.count("mixed") >= first10.count(
        "scanned"
    )
    # Determinism: recompute → identical order.
    assert [m.pdf_locator for m in stratified_order(members, _stratum_of(smap))] == [
        m.pdf_locator for m in order
    ]


def test_plan_stages_are_supersets() -> None:
    members = _members()
    smap = _stratum_map()
    plans = plan_stages(members, _stratum_of(smap), resolve_stages("10,20,full", 30))
    assert [p.planned_size for p in plans] == [10, 20, 30]
    s0 = {m.pdf_locator for m in plans[0].members}
    s1 = {m.pdf_locator for m in plans[1].members}
    s2 = {m.pdf_locator for m in plans[2].members}
    assert s0 < s1 < s2  # strict superset nesting
    assert len(plans[0].members) == 10 and len(plans[2].members) == 30


# --------------------------------------------------------------------------- #
# The VoI gate: PROCEED clean, STOP on a numeric regression.                    #
# --------------------------------------------------------------------------- #


def test_gate_proceeds_when_clean_and_runs_all_stages() -> None:
    members = _members()
    smap = _stratum_map()
    report = run_sweep(
        members,
        _stratum_of(smap),
        _clean_processor(),
        stages=resolve_stages("10,20,full", 30),
        workers=4,
    )
    assert report.stopped_at is None
    assert [a.planned_size for a in report.stages] == [10, 20, 30]
    assert all(a.gate_ok for a in report.stages)
    assert report.dropped_count == 0
    # every A/B row accepted, numeric clean
    assert report.stages[-1].n_ab == 30
    assert report.stages[-1].numeric_regressions == 0
    assert report.stages[-1].acceptance_rate == 1.0


def test_gate_stops_on_injected_numeric_regression_and_drops_next_tranche() -> None:
    members = _members()
    smap = _stratum_map()
    order = stratified_order(members, _stratum_of(smap))
    # Poison ONE PDF that first appears in stage-1 (index 10..19) with a numeric
    # regression; stage-0 (first 10) stays clean → the gate proceeds past stage 0
    # then STOPS at stage 1, never running the full tranche.
    poisoned = order[12].pdf_locator

    def processor(m: CorpusMember) -> RowResult:
        if m.pdf_locator == poisoned:
            return _numeric_regressed_row(m.pdf_locator)
        return _clean_row(m.pdf_locator)

    report = run_sweep(
        members,
        _stratum_of(smap),
        processor,
        stages=resolve_stages("10,20,full", 30),
        numeric_tolerance=0,
        workers=4,
    )
    assert report.stopped_at == 1
    assert report.stages[0].gate_ok is True  # clean small stage proceeded
    assert report.stages[1].gate_ok is False
    assert any("numeric_regressions" in r for r in report.stages[1].stop_reasons)
    assert len(report.stages) == 2  # the full (30) tranche was NEVER run
    # The dropped tranche (never-run PDFs) is logged.
    assert report.dropped_count == 30 - 20
    assert sum(c for _s, c in report.dropped_stratum_counts) == report.dropped_count


def test_numeric_tolerance_allows_bounded_regression() -> None:
    members = _members()
    smap = _stratum_map()
    order = stratified_order(members, _stratum_of(smap))
    poisoned = order[2].pdf_locator

    def processor(m: CorpusMember) -> RowResult:
        if m.pdf_locator == poisoned:
            return _numeric_regressed_row(m.pdf_locator)
        return _clean_row(m.pdf_locator)

    # tolerance 1 → the single regression does not stop the sweep.
    report = run_sweep(
        members,
        _stratum_of(smap),
        processor,
        stages=resolve_stages("10,full", 30),
        numeric_tolerance=1,
        workers=4,
    )
    assert report.stopped_at is None
    assert all(a.gate_ok for a in report.stages)


# --------------------------------------------------------------------------- #
# Resume: skip completed PDFs.                                                  #
# --------------------------------------------------------------------------- #


def test_ab_verdict_roundtrips_through_output_farchive(tmp_path) -> None:
    """Resume-by-construction primitive: a member's A/B verdict is durable in the
    OUTPUT FARCHIVE and reconstructs a bit-identical RowResult (no side checkpoint).

    The real processor persists each verdict under a ``(source × pipeline × gold)``
    key and short-circuits on it, so a re-run re-walks completed members as fast
    farchive lookups. This exercises that persistence layer hermetically."""
    from lawvm.ingest.parsed_store import ParsedIrStore, defacsimile_ab_locator
    from lawvm.tools.fi_parse_corpus import _row_from_jsonable, _row_to_jsonable

    row = RowResult(
        pdf_locator="finlex://sd/2020/born_digital-001/fin/media/x.pdf",
        xml_locator="finlex://sd/2020/born_digital-001/fin/main.xml",
        status="ab",
        baseline_extra=3,
        baseline_structure=2,
        baseline_missing=1,
        baseline_numeric=0,
        extra_delta=-2,
        structure_delta=-1,
        missing_delta=0,
        numeric_delta=0,
        accepted=True,
    )
    assert _row_from_jsonable(_row_to_jsonable(row)) == row  # pure round-trip

    loc = defacsimile_ab_locator("srcdigest", "adjudicated_pdf", "v9", "golddigest")
    store = ParsedIrStore(str(tmp_path / "parsed.farchive"))
    try:
        assert store.get_ab(loc) is None  # absent before the run
        store.put_ab(loc, _row_to_jsonable(row))
        got = store.get_ab(loc)
    finally:
        store.close()
    assert got is not None
    assert _row_from_jsonable(got) == row  # durable + faithful → skippable next time


def test_ab_locator_keys_on_source_pipeline_and_gold() -> None:
    """A changed source, pipeline version, OR gold digest must MISS the prior verdict
    (so a re-crawl / pipeline bump never silently reuses a stale benchmark row)."""
    from lawvm.ingest.parsed_store import defacsimile_ab_locator

    base = defacsimile_ab_locator("srcA", "adjudicated_pdf", "v1", "goldA")
    assert base != defacsimile_ab_locator("srcB", "adjudicated_pdf", "v1", "goldA")
    assert base != defacsimile_ab_locator("srcA", "adjudicated_pdf", "v2", "goldA")
    assert base != defacsimile_ab_locator("srcA", "adjudicated_pdf", "v1", "goldB")
    assert base == defacsimile_ab_locator("srcA", "adjudicated_pdf", "v1", "goldA")


# --------------------------------------------------------------------------- #
# Determinism of the plan + report shape + ranked residual table.              #
# --------------------------------------------------------------------------- #


def test_dry_run_plan_is_deterministic() -> None:
    from lawvm.tools.fi_sweep import _plan_lines

    members = _members()
    smap = _stratum_map()
    stages = resolve_stages("10,20,full", 30)
    p1 = _plan_lines(plan_stages(members, _stratum_of(smap), stages), len(members))
    p2 = _plan_lines(plan_stages(members, _stratum_of(smap), stages), len(members))
    assert p1 == p2
    assert "--dry-run" in p1
    lines = p1.splitlines()
    # one header block + 3 stage rows
    assert lines[-1].startswith("2,30,30,")


def test_report_shape_and_ranked_residual() -> None:
    members = _members()
    smap = _stratum_map()
    report = run_sweep(
        members, _stratum_of(smap), _clean_processor(),
        stages=resolve_stages("10,full", 30), workers=4,
    )
    text = render_report(report)
    assert text.startswith("# fi-sweep")
    assert "## PER-STAGE" in text
    assert "## RANKED RESIDUAL DEFECT CLASSES" in text
    assert "## DROPPED" in text
    # residual ranking is worst-first: each clean row leaves residual
    # EXTRA=2,STRUCTURE=1,MISSING=1,NUMERIC=1 → EXTRA ranks first.
    ranking = report.residual_ranking
    assert ranking[0][0] == "EXTRA"
    assert [c for c, _n in ranking] == sorted(
        [c for c, _n in ranking],
        key=lambda c: (-dict(ranking)[c], c),
    )
    # two identical runs render byte-identically.
    report2 = run_sweep(
        members, _stratum_of(smap), _clean_processor(),
        stages=resolve_stages("10,full", 30), workers=1,
    )
    assert render_report(report) == render_report(report2)


def test_json_report_shape() -> None:
    members = _members()
    smap = _stratum_map()
    report = run_sweep(
        members, _stratum_of(smap), _clean_processor(),
        stages=resolve_stages("10,full", 30), workers=4,
    )
    payload = report_to_json(report)
    assert payload["n_selected"] == 30
    assert payload["stopped_at_stage"] is None
    # report_to_json() returns Dict[str, object] by design (a dynamic JSON payload); narrow here.
    assert [s["planned_size"] for s in payload["stages"]] == [10, 30]  # ty: ignore[not-iterable]
    assert payload["residual_ranking"][0]["defect_class"] == "EXTRA"  # ty: ignore[not-subscriptable]
    # round-trips through json cleanly (deterministic, no non-serializable types).
    assert json.loads(json.dumps(payload)) == payload


# --------------------------------------------------------------------------- #
# Optional token meter (guarded absence).                                      #
# --------------------------------------------------------------------------- #


def test_token_meter_is_recorded_when_present() -> None:
    members = _members()
    smap = _stratum_map()

    class _FakeMeter:
        def __init__(self) -> None:
            self._t = 0

        def snapshot(self):
            self._t += 1
            # cumulative tokens grow 100/step, wall 2.0s/step
            return (self._t * 100, self._t * 2.0)

    report = run_sweep(
        members, _stratum_of(smap), _clean_processor(),
        stages=resolve_stages("10,full", 30), workers=4, meter=_FakeMeter(),
    )
    # Each stage recorded a positive token delta + a throughput number.
    assert all(a.output_tokens is not None and a.output_tokens > 0 for a in report.stages)
    assert all(a.tokens_per_second is not None for a in report.stages)
    assert "## THROUGHPUT" in render_report(report)


def test_no_meter_omits_throughput_block() -> None:
    members = _members()
    smap = _stratum_map()
    report = run_sweep(
        members, _stratum_of(smap), _clean_processor(),
        stages=resolve_stages("10,full", 30), workers=4,
    )
    assert all(a.output_tokens is None for a in report.stages)
    assert "## THROUGHPUT" not in render_report(report)
