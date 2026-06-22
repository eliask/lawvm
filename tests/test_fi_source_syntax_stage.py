"""Source-syntax / parse-forest waist (StageResult endgame row #4) conversion tests.

Covers ``assemble_source_syntax_graph_staged`` returning a
``StageResult[SourceSyntaxGraph]`` whose ``value`` is the SAME (cached) forest the
bare assembler returns, whose ``coverage`` is the forest token-partition projected
onto the core ``CoverageCertificate``, and whose ``residuals`` are blocking
``unowned_violation`` spans (the silent-unowned cheap-signal class, verbatim text)
plus non-blocking ``typed_residual`` spans — PLUS the production-consumer fire-drill
proving ``graph_build`` actually READS the forest coverage account and FAILS LOUD on
a silent-unowned span (a severed/never-read account is a FAIL).

The fire-drill uses a REAL synthesized body (an ``HE`` reference in plain prose — a
cheap legal signal NO construction family owns), driven through the production
``build_legal_surface_graph`` entrypoint, so a CALL-SITE revert (neutralizing the
``_gate_forest_coverage`` call in ``_assemble_surface_graph_value``) makes this test
go RED. An AST call-site ratchet backstops it (the production builder must call the
staged form / the gate and branch on ``.coverage``).
"""
from __future__ import annotations

import ast
import inspect
import os
from collections.abc import Callable

import pytest

from lawvm.core.legal_surface_graph import SurfaceGraphSubject
from lawvm.core.stage_result import CoverageCertificate, Residual, StageResult
from lawvm.finland.legal_surface import graph_build
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.source_syntax_graph import (
    SourceSyntaxGraph,
    assemble_source_syntax_graph_for_unit,
    assemble_source_syntax_graph_staged,
    clear_forest_cache,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"

# A clean body: every cheap legal signal is owned (definition / delegation /
# commencement). No silent-unowned span → coverage.violation == 0.
_CLEAN_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Jatteella tarkoitetaan poistettavaa ainetta.</p>
          <p>Valtioneuvosto voi antaa asetuksella tarkempia saannoksia.</p>
          <p>Tama laki tulee voimaan 1.1.2027.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

# A body carrying a SILENT-UNOWNED span: an ``HE`` (hallituksen esitys) reference in
# plain prose — a cheap legal signal NO body-text construction recognizer owns. This
# is the row #4 ``unowned_violation`` class. The production gate must fail loud.
_SILENT_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <p>Asia mainittiin valmistelussa HE 5/2019 yhteydessä laajasti.</p>
        </content>
      </section>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

_CLEAN_ID = "123/2020"
_SILENT_ID = "999/2099"

_SUBJECT = SurfaceGraphSubject(
    jurisdiction="fi",
    work_id="test/1",
    scope={},
    surface_time=None,
    source_bundle_hash="",
    language="fi",
)


def _clean_unit():
    bundle = build_surface_bundle(_CLEAN_XML, _CLEAN_ID)
    return bundle.subject, bundle.units[0]


# ---------------------------------------------------------------------------
# (a) staged shape: value identity + coverage projection.
# ---------------------------------------------------------------------------


def test_staged_value_is_the_bare_forest() -> None:
    clear_forest_cache()
    subject, unit = _clean_unit()
    stage = assemble_source_syntax_graph_staged(subject=subject, unit=unit)
    assert isinstance(stage, StageResult)
    assert isinstance(stage.value, SourceSyntaxGraph)
    # value path identical to the bare form (the forest is cached, so SAME object).
    bare = assemble_source_syntax_graph_for_unit(subject=subject, unit=unit)
    assert stage.value is bare


def test_coverage_is_the_token_partition() -> None:
    clear_forest_cache()
    subject, unit = _clean_unit()
    stage = assemble_source_syntax_graph_staged(subject=subject, unit=unit)
    cov = stage.coverage
    assert isinstance(cov, CoverageCertificate)
    assert cov.unit == "tokens"
    # the embedded SyntaxCoverage totalizes the signal-bearing token space.
    assert cov.total == stage.value.coverage.total_tokens
    assert cov.owned == stage.value.coverage.owned_tokens
    assert cov.violation == stage.value.coverage.unowned_violation_tokens
    assert cov.is_partition()


def test_authority_is_neutral_findings_empty_evidence_empty() -> None:
    clear_forest_cache()
    subject, unit = _clean_unit()
    stage = assemble_source_syntax_graph_staged(subject=subject, unit=unit)
    assert stage.authority.is_neutral
    assert stage.authority.replay_authorized is False
    assert stage.findings == ()
    assert stage.evidence.is_empty


# ---------------------------------------------------------------------------
# (b) clean unit → violation==0, residuals are typed_residual / non-blocking.
# ---------------------------------------------------------------------------


def test_clean_unit_has_no_violation_and_no_blocking_residual() -> None:
    clear_forest_cache()
    subject, unit = _clean_unit()
    stage = assemble_source_syntax_graph_staged(subject=subject, unit=unit)
    assert stage.coverage.violation == 0
    assert stage.coverage.is_clean
    for residual in stage.residuals:
        assert residual.kind == "typed_residual"
        assert residual.blocking is False
        assert residual.reason.startswith("forest_typed_residual:")
    assert not stage.has_blocking_residual


# ---------------------------------------------------------------------------
# (c) silent-unowned span → violation>0 + blocking residual w/ verbatim text.
# ---------------------------------------------------------------------------


def test_silent_unowned_span_is_blocking_residual_with_verbatim_text() -> None:
    clear_forest_cache()
    bundle = build_surface_bundle(_SILENT_XML, _SILENT_ID)
    unit = bundle.units[0]
    stage = assemble_source_syntax_graph_staged(subject=bundle.subject, unit=unit)
    assert stage.coverage.violation > 0
    assert not stage.coverage.is_clean
    blockers = [
        r
        for r in stage.residuals
        if r.kind == "unowned_violation" and r.blocking
    ]
    assert blockers, stage.residuals
    res = blockers[0]
    assert isinstance(res, Residual)
    assert res.reason.startswith("forest_silent_unowned_cheap_signal:")
    # self-evidencing: the verbatim offending span text is carried.
    assert "HE 5/2019" in res.text
    assert res.char_start is not None and res.char_end is not None
    assert stage.has_blocking_residual


# ---------------------------------------------------------------------------
# FIRE-DRILL: drive the PRODUCTION build entrypoint with a silent-unowned span and
# assert it RAISES with the forest-coverage violation. RED if the gate's call site
# in ``_assemble_surface_graph_value`` is severed back to a log/no-op.
# ---------------------------------------------------------------------------


def test_consumer_reads_coverage_clean_build() -> None:
    clear_forest_cache()
    g = graph_build.build_legal_surface_graph(
        _CLEAN_XML, _CLEAN_ID, surface_time="2026-01-01"
    )
    assert g is not None
    assert g.subject.work_id == _CLEAN_ID


def test_silent_unowned_does_not_block_in_normal_operation() -> None:
    # 0-delta contract: in normal operation (LAWVM_PARSE_TOTALITY unset) a
    # silent-unowned span is the surfaced, non-blocking no-silent-drop frontier
    # (mirrors union_ownership_census's treatment of the SAME silent_tokens bucket).
    # It must NOT block the graph build — the real corpus carries such spans
    # (e.g. bare "§" / applicability verbs), so a hard raise here would not be
    # 0-delta. The violation is still surfaced as a blocking StageResult residual.
    clear_forest_cache()
    os.environ.pop("LAWVM_PARSE_TOTALITY", None)
    g = graph_build.build_legal_surface_graph(
        _SILENT_XML, _SILENT_ID, surface_time="2026-01-01"
    )
    assert g is not None


def test_production_build_fails_loud_on_silent_unowned_forest_span(
    monkeypatch,
) -> None:
    # Under the strict totality contract (LAWVM_PARSE_TOTALITY set), a silent-
    # unowned span is the HARD no-silent-drop gate: the production builder fails
    # loud, embedding the verbatim offending span — the same semantics
    # union_ownership_census applies to the silent_tokens bucket under the flag.
    monkeypatch.setenv("LAWVM_PARSE_TOTALITY", "1")
    clear_forest_cache()
    with pytest.raises(ValueError, match="source-syntax forest coverage") as exc:
        graph_build.build_legal_surface_graph(
            _SILENT_XML, _SILENT_ID, surface_time="2026-01-01"
        )
    msg = str(exc.value)
    # the raise embeds the verbatim offending span (self-evidencing) + the unit.
    assert "HE 5/2019" in msg
    assert _SILENT_ID in msg
    assert "unowned violation" in msg
    clear_forest_cache()


# ---------------------------------------------------------------------------
# Structural-contract / AST call-site ratchet: the production builder MUST call the
# staged form (via the gate) and BRANCH on ``.coverage``. A call-site revert that
# made the behavioral fire-drill pass-only (e.g. moving the check into an isolated
# helper that production no longer calls) is caught here too.
# ---------------------------------------------------------------------------


def _src(func: Callable[..., object]) -> str:
    return inspect.getsource(func)


def test_gate_branches_on_forest_coverage() -> None:
    # The gate reads the staged form's .coverage and raises on violation / non-
    # partition — the load-bearing branch.
    src = _src(graph_build._gate_forest_coverage)
    tree = ast.parse(src)
    calls_staged = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "assemble_source_syntax_graph_staged"
        for n in ast.walk(tree)
    )
    assert calls_staged, "gate must call assemble_source_syntax_graph_staged"
    reads_coverage = any(
        isinstance(n, ast.Attribute) and n.attr in {"coverage", "violation", "is_partition"}
        for n in ast.walk(tree)
    )
    assert reads_coverage, "gate must read .coverage / .violation / .is_partition"
    raises = any(isinstance(n, ast.Raise) for n in ast.walk(tree))
    assert raises, "gate must raise on a forest-coverage violation"


def test_production_builder_calls_the_forest_gate() -> None:
    # The production build path (_assemble_surface_graph_value) must CALL the gate
    # — proving the account reaches production, not just an isolated helper.
    src = _src(graph_build._assemble_surface_graph_value)
    tree = ast.parse(src)
    calls_gate = any(
        isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name)
        and n.func.id == "_gate_forest_coverage"
        for n in ast.walk(tree)
    )
    assert calls_gate, "_assemble_surface_graph_value must call _gate_forest_coverage"
