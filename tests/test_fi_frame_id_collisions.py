"""Regression gate: frame-lens node-id collisions over the real Finlex corpus.

The H5/H6 frame lenses (delegation, procedure, sanction, exception/condition)
build each seed's ``local_discriminator`` from ``key|surface|offset``. When a
recognizer emits two co-located observations (same key + same surface + same
byte offset) with DIVERGENT payloads, that discriminator collides: two seeds
mint the SAME node_id with different payloads, and the fail-loud assembler
(correctly) raises :class:`SurfaceAssemblyError`. This crashed
``build_legal_surface_graph`` on ~2% of real statutes.

The fix appends a monotonic occurrence index (the seed's deterministic position
in recognizer + unit order) to each discriminator, so co-located observations
become DISTINCT nodes — neither is dropped, none collides.

This gate sweeps the first ``_SWEEP_N`` real corpus statutes through
``build_legal_surface_graph`` and asserts ZERO ``SurfaceAssemblyError`` (zero
collisions), and that ``graph_id`` is stable across two builds of the same
statute (the index is deterministic, so determinism is preserved).

The sweep is opt-in via ``LAWVM_CANONICAL_DATA_ROOT`` (the path-resolution env
var that ``get_corpus_store`` honours; the farchive is expected at
``$LAWVM_CANONICAL_DATA_ROOT/data/finlex.farchive``). When the corpus is not
available the sweep skips cleanly.
"""
from __future__ import annotations

import os

import pytest

from lawvm.core.legal_surface_assembler import SurfaceAssemblyError
from lawvm.core.legal_surface_lens import SurfaceAnalysisContext
from lawvm.core.reference_mention import SourceSpan
from lawvm.corpus_store import CorpusStore
from lawvm.finland.legal_surface.bundle import build_surface_bundle
from lawvm.finland.legal_surface.graph_build import build_legal_surface_graph
from lawvm.finland.legal_surface.lenses import exception_condition as exc_lens_mod
from lawvm.finland.legal_surface.lenses import procedure as proc_lens_mod
from lawvm.finland.legal_surface.lenses import sanction as sanc_lens_mod
from lawvm.finland.legal_surface.lenses.exception_condition import (
    ExceptionConditionLens,
)
from lawvm.finland.legal_surface.lenses.procedure import ProcedureLens
from lawvm.finland.legal_surface.lenses.sanction import SanctionLens
from lawvm.finland.references.procedure import (
    ProcedureFrame,
    ProcedureScan,
    ProcessKind,
)
from lawvm.finland.references.sanction import (
    SanctionFrame,
    SanctionKind,
    SanctionScan,
)

#: How many leading corpus statutes to sweep.
_SWEEP_N = 1000

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
_MINIMAL_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="{_AKN}">
  <act><body><section eId="sec_1"><num>1 §</num><content>
    <p>hakemus hakemus</p>
  </content></section></body></act>
</akomaNtoso>
""".encode("utf-8")


def _colocated_span() -> SourceSpan:
    """A single fixed span shared by two synthetic co-located observations."""
    return SourceSpan(source_file="", byte_offset=0, byte_len=7)


def _bundle_and_context():
    bundle = build_surface_bundle(_MINIMAL_XML, "1/2020")
    context = SurfaceAnalysisContext(options={})
    return bundle, context


def _assert_distinct_then_assembles(lens, bundle, context) -> None:
    """Two co-located divergent seeds → distinct discriminators → graph builds.

    Asserts the lens emitted two seeds (neither dropped), their
    ``local_discriminator`` values are distinct (the occurrence index broke the
    key|surface|offset tie), and the full assembler accepts them without raising
    SurfaceAssemblyError (no node_id collision).
    """
    result = lens.analyze(bundle, context=context)
    seeds = list(result.node_seeds) + list(result.residuals)
    assert len(seeds) == 2, f"expected 2 co-located seeds, got {len(seeds)}"
    discs = [s.local_discriminator for s in seeds]
    assert len(set(discs)) == 2, f"discriminators collided: {discs}"
    # End-to-end: the assembler (fail-loud on collision) accepts both.
    graph = build_legal_surface_graph(_MINIMAL_XML, "1/2020", lenses=(lens,))
    assert graph.graph_id


def _corpus_available() -> bool:
    return bool(os.environ.get("LAWVM_CANONICAL_DATA_ROOT"))


def _statute_xml(store: CorpusStore, sid: str) -> bytes | None:
    """The source (or, failing that, amendment) XML bytes for a statute id."""
    xml = store.read_source(sid)
    if not xml:
        xml = store.read_amendment(sid)
    return xml


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus frame-id sweep skipped",
)
@pytest.mark.slow
def test_no_frame_id_collisions_over_corpus_sweep() -> None:
    """No ``SurfaceAssemblyError`` over the first 1000 real corpus statutes."""
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store()
    all_ids = store.list_statute_ids()
    assert all_ids, "corpus store returned no statute ids"

    swept = 0
    collisions: list[str] = []
    for sid in all_ids[:_SWEEP_N]:
        xml_bytes = _statute_xml(store, sid)
        if not xml_bytes:
            continue
        try:
            build_legal_surface_graph(xml_bytes, sid)
        except SurfaceAssemblyError as exc:
            collisions.append(f"{sid}: {exc}")
        swept += 1

    assert swept > 0, "swept zero statutes (corpus empty or unreadable)"
    assert not collisions, (
        f"{len(collisions)} statute(s) raised SurfaceAssemblyError over a "
        f"{swept}-statute sweep (expected 0):\n  "
        + "\n  ".join(collisions[:10])
    )


@pytest.mark.skipif(
    not _corpus_available(),
    reason="LAWVM_CANONICAL_DATA_ROOT not set; real-corpus determinism check skipped",
)
@pytest.mark.slow
def test_frame_graph_id_stable_across_rebuilds() -> None:
    """The added occurrence index is deterministic: graph_id is rebuild-stable.

    Builds each of the first real statutes that assemble cleanly twice and
    asserts identical ``graph_id`` — proving the monotonic discriminator index
    (seed position in deterministic recognizer + unit order) is stable across
    reruns rather than introducing nondeterminism.
    """
    from lawvm.finland.corpus import get_corpus_store

    store = get_corpus_store()
    all_ids = store.list_statute_ids()
    assert all_ids, "corpus store returned no statute ids"

    checked = 0
    for sid in all_ids:
        if checked >= 5:
            break
        xml_bytes = _statute_xml(store, sid)
        if not xml_bytes:
            continue
        try:
            g1 = build_legal_surface_graph(xml_bytes, sid)
            g2 = build_legal_surface_graph(xml_bytes, sid)
        except SurfaceAssemblyError:
            continue
        assert g1.graph_id == g2.graph_id, (
            f"{sid}: graph_id not stable across rebuilds "
            f"({g1.graph_id} != {g2.graph_id})"
        )
        checked += 1

    assert checked >= 1, "no statute assembled cleanly for the determinism check"


# ── Synthetic, corpus-free collision regressions (one per fixed lens) ─────────
# These inject two CO-LOCATED observations (identical key + surface + offset) but
# DIVERGENT payloads, the exact shape that collided pre-fix. They run without the
# corpus, so the regression is locked in even where the sweep skips.


def test_procedure_colocated_divergent_frames_distinct(monkeypatch) -> None:
    span = _colocated_span()
    actor_a = SourceSpan(source_file="", byte_offset=10, byte_len=3)
    actor_b = SourceSpan(source_file="", byte_offset=20, byte_len=3)
    scan = ProcedureScan(
        frames=(
            ProcedureFrame(
                process_kind=ProcessKind.HAKEMUS,
                actor_span=actor_a,
                deadline_span=None,
                source_span=span,
                procedure_status="surface_fact_only",
                rule_id="test.proc",
            ),
            ProcedureFrame(
                process_kind=ProcessKind.HAKEMUS,
                actor_span=actor_b,  # divergent payload, same key+surface+offset
                deadline_span=None,
                source_span=span,
                procedure_status="surface_fact_only",
                rule_id="test.proc",
            ),
        ),
        residuals=(),
    )
    monkeypatch.setattr(proc_lens_mod, "scan_procedure", lambda _text, **_kw: scan)
    bundle, context = _bundle_and_context()
    _assert_distinct_then_assembles(ProcedureLens(), bundle, context)


def test_sanction_colocated_divergent_frames_distinct(monkeypatch) -> None:
    span = _colocated_span()
    trig_a = SourceSpan(source_file="", byte_offset=10, byte_len=3)
    trig_b = SourceSpan(source_file="", byte_offset=20, byte_len=3)
    scan = SanctionScan(
        frames=(
            SanctionFrame(
                sanction_kind=SanctionKind.SAKKO,
                marker_surface="sakko",
                target_actor_span=None,
                trigger_span=trig_a,
                source_span=span,
                sanction_status="surface_fact_only",
                rule_id="test.sanc",
            ),
            SanctionFrame(
                sanction_kind=SanctionKind.SAKKO,
                marker_surface="sakko",
                target_actor_span=None,
                trigger_span=trig_b,  # divergent payload, same key+surface+offset
                source_span=span,
                sanction_status="surface_fact_only",
                rule_id="test.sanc",
            ),
        ),
        residuals=(),
    )
    monkeypatch.setattr(
        sanc_lens_mod, "recognize_sanction_frames", lambda _text, **_kw: scan
    )
    bundle, context = _bundle_and_context()
    _assert_distinct_then_assembles(SanctionLens(), bundle, context)


def test_exception_condition_colocated_divergent_cues_distinct(monkeypatch) -> None:
    # The migrated token lens builds cues from ``_scan_phrases`` (tuples
    # ``(m_start, m_end, marker_text, scope_start, scope_end)``), not from the
    # former ``recognize_exception_condition_cues``. Force two cues at the SAME
    # marker span with DIVERGENT scope payload so the assembler must mint
    # distinct node ids (the colocation-discriminator regression).
    def fake_scan(_tape, _raw_text, _phrases, kind):  # noqa: ANN001
        if kind == "EXCEPTION":
            return [
                (10, 13, "jos", 5, 8),
                (10, 13, "jos", 20, 23),  # same span, divergent scope payload
            ]
        return []

    monkeypatch.setattr(exc_lens_mod, "_scan_phrases", fake_scan)
    bundle, context = _bundle_and_context()
    _assert_distinct_then_assembles(ExceptionConditionLens(), bundle, context)
