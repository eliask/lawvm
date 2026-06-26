"""Unit tests for the analyze-bill vertical (lawvm.tools.bill_analysis).

All tests run on SYNTHETIC ParsedOps + a synthetic LegalSurfaceGraph — there is
NO corpus dependency. The pure build_* / render_* functions are exercised
directly, plus a golden render + json snapshot of the assembled report.
"""

from __future__ import annotations

from typing import Any

from lawvm.core.legal_surface_graph import (
    AuthorityRole,
    LegalSurfaceGraph,
    SourceSpanRef,
    SurfaceGraphSubject,
    SurfaceNode,
)
from lawvm.core.stage_result import StageResult
from lawvm.finland.johtolause.types import ParsedOp
from lawvm.finland.legal_surface.graph_build import (
    _surface_graph_stage_account,
)
from lawvm.tools import bill_analysis as ba


def _stage(graph: LegalSurfaceGraph) -> StageResult[LegalSurfaceGraph]:
    """Wrap a synthetic graph in the real surface StageResult account.

    Uses the production status->coverage projection so the broken-ref branch is
    exercised through the SAME typed residual channel the production consumer
    reads (not a hand-rolled stub).
    """
    coverage, residuals = _surface_graph_stage_account(graph)
    return StageResult(value=graph, residuals=residuals, coverage=coverage)


# ---------------------------------------------------------------------------
# Synthetic fixture factories (corpus-free)
# ---------------------------------------------------------------------------

# A small synthetic body. Char spans below index into THIS string.
BODY = (
    "Valtioneuvoston asetuksella voidaan antaa tarkempia saannoksia. "  # 0..62
    "Tata lakia sovelletaan kuten 5 pykalassa saadetaan. "  # 63..114
    "Kumotaan 7 pykala. Vakuutusyhtiolla tarkoitetaan toimijaa."  # 115..
)


def _span(start: int, end: int) -> SourceSpanRef:
    return SourceSpanRef(
        source_unit_id="u#1",
        source_hash="h",
        work_id="2099/1",
        address=None,
        char_start=start,
        char_end=end,
        text_hash="t",
    )


def _node(
    node_id: str,
    node_kind: str,
    *,
    status: str,
    payload: dict[str, Any],
    span: SourceSpanRef | None,
    authority_role: AuthorityRole = "surface_fact",
) -> SurfaceNode:
    return SurfaceNode(
        node_id=node_id,
        node_kind=node_kind,
        authority_role=authority_role,
        jurisdiction="fi",
        source_ref=span,
        lens_id="test",
        rule_id="test",
        node_status=status,
        payload_hash="ph",
        payload=payload,
    )


def _graph(nodes: list[SurfaceNode]) -> LegalSurfaceGraph:
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="2099/1",
        scope={},
        surface_time=None,
        source_bundle_hash="b",
        language="fi",
    )
    return LegalSurfaceGraph(
        schema="test",
        graph_id="g#1",
        subject=subject,
        source_units=(),
        lens_runs=(),
        nodes={n.node_id: n for n in nodes},
        edges=(),
        build_diagnostics=(),
    )


def _op(verb: str, kind: str, number: str, **kw: Any) -> ParsedOp:
    return ParsedOp(
        verb=verb,
        kind=kind,
        chapter=kw.get("chapter", ""),
        number=number,
        momentti=kw.get("momentti", 0),
        item=kw.get("item", ""),
        raw=kw.get("raw", f"{verb} {kind} {number}"),
        part=kw.get("part", ""),
    )


def _rich_graph() -> LegalSurfaceGraph:
    """A graph with one delegation, several refs (mixed status), one def."""
    return _graph(
        [
            _node(
                "del#1",
                "delegation_frame",
                status="present",
                payload={
                    "delegate_actor": "Valtioneuvosto",
                    "instrument_kind": "asetus",
                    "binding_strength": "may",
                },
                span=_span(0, 62),
            ),
            _node(
                "ref#1",
                "reference_resolution",
                status="resolved",
                payload={
                    "surface_text": "5 pykalassa",
                    "work_id": "2099/1",
                    "candidates": [],
                },
                span=_span(92, 103),
            ),
            _node(
                "ref#2",
                "reference_resolution",
                status="open",
                payload={"surface_text": "asianomainen viranomainen", "candidates": []},
                span=None,
            ),
            _node(
                "ref#3",
                "reference_resolution",
                status="broken",
                payload={"surface_text": "7 pykala", "candidates": []},
                span=_span(124, 132),
            ),
            _node(
                "def#1",
                "definition_binding",
                status="resolved",
                payload={
                    "term": "vakuutusyhtio",
                    "scope": "laki",
                    "binding_kind": "tarkoitetaan",
                },
                span=_span(0, 30),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# build_op_summary
# ---------------------------------------------------------------------------


def test_op_summary_counts_and_labels() -> None:
    ops = [
        _op("M", "P", "5", momentti=2),
        _op("K", "P", "7"),
        _op("L", "P", "5a"),
    ]
    summary = ba.build_op_summary(ops)
    assert summary["n_ops"] == 3
    assert summary["by_verb"] == {"AMEND": 1, "INSERT": 1, "REPEAL": 1}
    verbs = [o["verb_label"] for o in summary["ops"]]
    assert verbs == ["AMEND", "REPEAL", "INSERT"]
    assert summary["ops"][0]["kind_label"] == "section §"
    assert summary["ops"][0]["momentti"] == 2


def test_op_summary_empty() -> None:
    summary = ba.build_op_summary([])
    assert summary["n_ops"] == 0
    assert summary["by_verb"] == {}
    assert summary["ops"] == []


def test_repealed_targets_only_repeals() -> None:
    ops = [_op("M", "P", "5"), _op("K", "P", "7"), _op("K", "P", "12", chapter="3")]
    targets = ba.repealed_targets(ops)
    assert [t["number"] for t in targets] == ["7", "12"]
    assert targets[1]["chapter"] == "3"


# ---------------------------------------------------------------------------
# build_delegation_delta
# ---------------------------------------------------------------------------


def test_delegation_delta_extracts_actor_and_instrument() -> None:
    delta = ba.build_delegation_delta(_rich_graph(), BODY)
    assert delta["count"] == 1
    d = delta["delegations"][0]
    assert d["delegate_actor"] == "Valtioneuvosto"
    assert d["instrument_kind"] == "asetus"
    assert d["binding_strength"] == "may"
    assert "asetuksella" in d["span_text"]


def test_delegation_delta_empty_when_no_frames() -> None:
    delta = ba.build_delegation_delta(_graph([]), BODY)
    assert delta["count"] == 0
    assert delta["delegations"] == []


# ---------------------------------------------------------------------------
# build_reference_delta
# ---------------------------------------------------------------------------


def test_reference_delta_groups_by_status() -> None:
    delta = ba.build_reference_delta(_rich_graph(), BODY)
    assert delta["count"] == 3
    assert delta["by_status"] == {"broken": 1, "open": 1, "resolved": 1}
    resolved = [r for r in delta["references"] if r["status"] == "resolved"][0]
    assert resolved["work_id"] == "2099/1"


# ---------------------------------------------------------------------------
# build_broken_ref_risk
# ---------------------------------------------------------------------------


def test_broken_ref_risk_status_broken_and_self_repeal() -> None:
    ops = [_op("K", "P", "7")]
    risk = ba.build_broken_ref_risk(ops, _stage(_rich_graph()), BODY)
    assert [t["number"] for t in risk["repealed_targets"]] == ["7"]
    # ref#3 surface "7 pykala" has graph status broken
    assert len(risk["status_broken"]) == 1
    assert risk["status_broken"][0]["surface_text"] == "7 pykala"
    # within-bill: "7 pykala" cites repealed number 7
    cited = risk["self_repeal_then_cited"]
    assert any(e["repealed_number"] == "7" for e in cited)


def test_surface_cites_number_is_digit_bounded() -> None:
    assert ba._surface_cites_number("7 pykala", "7") is True
    assert ba._surface_cites_number("17 pykala", "7") is False
    assert ba._surface_cites_number("kohta 7", "7") is True
    assert ba._surface_cites_number("", "7") is False
    assert ba._surface_cites_number("7 pykala", "") is False


# ---------------------------------------------------------------------------
# build_definition_delta
# ---------------------------------------------------------------------------


def test_definition_delta_extracts_term() -> None:
    delta = ba.build_definition_delta(_rich_graph(), BODY)
    assert delta["count"] == 1
    assert delta["definitions"][0]["term"] == "vakuutusyhtio"
    assert delta["definitions"][0]["scope"] == "laki"


# ---------------------------------------------------------------------------
# build_unowned_candidates
# ---------------------------------------------------------------------------


def test_unowned_candidates_flag_open_and_strand() -> None:
    ops = [_op("K", "P", "7")]
    out = ba.build_unowned_candidates(ops, _stage(_rich_graph()), BODY)
    rules = {c["rule"] for c in out["candidates"]}
    # open reference present -> open_reference_introduced
    assert "open_reference_introduced" in rules
    # broken ref + within-bill cite of repealed number -> repeal_strands_reference
    assert "repeal_strands_reference" in rules
    # BODY contains no accountability cue -> delegation_without_accountability fires
    assert "delegation_without_accountability" in rules
    # never adjudicated: no score / magnitude keys
    for c in out["candidates"]:
        assert "score" not in c
        assert "magnitude" not in c
    assert "JUDGMENT FRONTIER" in out["disclaimer"]


def test_delegation_candidate_suppressed_by_accountability_cue() -> None:
    body = BODY + " Viranomaisen on raportoitava toiminnastaan vuosittain."
    ops: list[ParsedOp] = []
    out = ba.build_unowned_candidates(ops, _stage(_rich_graph()), body)
    rules = {c["rule"] for c in out["candidates"]}
    assert "delegation_without_accountability" not in rules


def test_unowned_rule_catalog_is_closed_and_documented() -> None:
    out = ba.build_unowned_candidates([], _stage(_graph([])), BODY)
    assert set(out["rule_catalog"]) == {
        "delegation_without_accountability",
        "repeal_strands_reference",
        "open_reference_introduced",
    }
    for desc in out["rule_catalog"].values():
        assert "Candidate for" in desc


# ---------------------------------------------------------------------------
# build_bill_report + golden render / json
# ---------------------------------------------------------------------------


def _report() -> dict[str, Any]:
    ops = [_op("M", "P", "5", momentti=2), _op("K", "P", "7")]
    return ba.build_bill_report("2099/1", ops, _stage(_rich_graph()), BODY)


def test_bill_report_structure() -> None:
    report = _report()
    assert report["statute_id"] == "2099/1"
    assert set(report) == {
        "statute_id",
        "what_the_bill_does",
        "surface_delta",
        "unowned_channel_candidates",
    }
    assert set(report["surface_delta"]) == {
        "delegations",
        "references",
        "broken_ref_risk",
        "definitions",
    }


def test_bill_report_json_roundtrips() -> None:
    import json

    report = _report()
    s = json.dumps(report, default=str, ensure_ascii=False)
    back = json.loads(s)
    assert back["statute_id"] == "2099/1"
    assert back["surface_delta"]["delegations"]["count"] == 1


GOLDEN_RENDER = """\
BILL IMPACT REPORT — 2099/1
================================================================

WHAT THE BILL DOES (2 op(s))
  by verb: AMEND=1, REPEAL=1
    • AMEND    section §      '5' mom 2
    • REPEAL   section §      '7'

NEW DELEGATIONS — authority transfer (1)
    └─ 'Valtioneuvosto' -> asetus (may)  [present]
         · 'Valtioneuvoston asetuksella voidaan antaa tarkempia saannoksia'

REFERENCES IN NEW TEXT (3)
  by status: broken=1, open=1, resolved=1
    └─ [broken     ] '7 pykala'
    └─ [open       ] 'asianomainen viranomainen'
    └─ [resolved   ] '5 pykalassa' -> 2099/1

BROKEN / DANGLING-REFERENCE RISK
  (v0 scope: within-bill + graph-status only; corpus-wide back-reference scan deferred.)
  repeal ops in this bill: 1
    - repeals 'K P 7'
  references with graph status=broken: 1
    └─ '7 pykala'
  within-bill cites of a repealed number: 1
    └─ '7 pykala' cites repealed 7

NEW / CHANGED DEFINITIONS (1)
    └─ 'vakuutusyhtio'  scope=laki  [resolved]

CANDIDATES FOR JUDGMENT — unowned-channel frontier
  JUDGMENT FRONTIER — these are deterministic structural CANDIDATES for human/LLM judgment, NOT adjudicated findings. No score, no magnitude, target never guessed.
  total candidates: 4
  by rule: delegation_without_accountability=1, open_reference_introduced=1, repeal_strands_reference=2
    ? [delegation_without_accountability] Valtioneuvosto granted asetus power
        · 'Valtioneuvoston asetuksella voidaan antaa tarkempia saannoksia'
    ? [repeal_strands_reference] reference '7 pykala' (status=broken)
        · '7 pykal'
    ? [repeal_strands_reference] reference '7 pykala' (status=broken)
        · '7 pykal'
    ? [open_reference_introduced] open reference 'asianomainen viranomainen'"""


def test_bill_report_golden_render() -> None:
    rendered = ba.render_bill_report(_report())
    assert rendered == GOLDEN_RENDER
