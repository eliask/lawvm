"""Token/grammar tests for the H5 procedure recognizer (Phase 7, decision B).

These exercise the TOKEN-ALIGNED behavior of the rewritten recognizer:

  - a process-noun frame on a LONG inflected word spans the WHOLE token (no
    arbitrary mid-token char cap any more);
  - a ``viimeistään …`` deadline is captured as a bounded TOKEN WINDOW that
    stops at a sentence boundary (and does NOT swallow a following process
    noun, which now surfaces as its own frame);
  - the lens adapter passes the recognizer's output through unchanged
    (lens nodes == recognizer frames), consuming ``token_tape``;
  - the recognizer is deterministic.

Span re-baselining vs the old regex implementation is EXPECTED and accepted;
these tests assert the token-aligned spans directly.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SourceSpanRef
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
    SurfaceGraphSubject,
)
from lawvm.finland.legal_surface.lenses.procedure import ProcedureLens
from lawvm.finland.legal_surface.tokenize import (
    build_morph_overlay,
    build_token_tape,
)
from lawvm.finland.references.procedure import (
    ProcessKind,
    recognize_procedure_frames,
    scan_procedure,
)


def _frame_text(text: str, frame) -> str:
    s = frame.source_span.byte_offset
    return text[s : s + frame.source_span.byte_len]


# ---------------------------------------------------------------------------
# 1. Process-noun frame on a LONG word spans the WHOLE token (no 12-char cap).
# ---------------------------------------------------------------------------


def test_process_noun_span_is_whole_token_not_char_capped() -> None:
    # "päätöksentekomenettelyssä" is one long inflected word headed by the
    # päätö- stem. The old regex capped the tail at 12 chars (ending mid-token);
    # the token-aligned recognizer spans the WHOLE word token.
    text = "Asiassa noudatetaan päätöksentekomenettelyssä määräyksiä."
    frames = [
        f for f in recognize_procedure_frames(text)
        if f.process_kind is ProcessKind.PAATOS
    ]
    assert len(frames) == 1
    f = frames[0]
    # whole-token span — the verbatim head IS the entire word
    assert _frame_text(text, f) == "päätöksentekomenettelyssä"
    # and the span is strictly longer than the old stem+12 char cap would allow
    assert f.source_span.byte_len == len("päätöksentekomenettelyssä")
    assert f.source_span.byte_len > len("päätö") + 12


def test_process_noun_span_does_not_bleed_into_next_word() -> None:
    # The frame head stops at the word boundary; the following word is not
    # absorbed (the old tail run could not cross a non-word char anyway, but we
    # pin the token-aligned end explicitly).
    text = "hakemuksen liitteet"
    frames = recognize_procedure_frames(text)
    haku = [f for f in frames if f.process_kind is ProcessKind.HAKEMUS]
    assert len(haku) == 1
    assert _frame_text(text, haku[0]) == "hakemuksen"


# ---------------------------------------------------------------------------
# 2. "viimeistään …" deadline as a bounded TOKEN WINDOW.
# ---------------------------------------------------------------------------


def test_viimeistaan_deadline_is_token_window_to_sentence_boundary() -> None:
    text = "Hakemus on jätettävä viimeistään kolmen kuukauden kuluttua. Muuta."
    frames = recognize_procedure_frames(text)
    maaraaika = [f for f in frames if f.process_kind is ProcessKind.MAARAAIKA]
    assert maaraaika, "viimeistään cue must yield a MAARAAIKA frame"
    f = maaraaika[0]
    captured = _frame_text(text, f)
    # window starts at the trigger, stops BEFORE the sentence-final period
    assert captured.startswith("viimeistään")
    assert captured == "viimeistään kolmen kuukauden kuluttua"
    assert "Muuta" not in captured
    assert not captured.endswith(".")


def test_viimeistaan_window_does_not_swallow_following_process_noun() -> None:
    # The old unbounded ``viimeistään[^.;:\n]{0,80}`` char-run swallowed a
    # following process noun, suppressing its standalone frame. The bounded
    # token window stops at the boundary, so the noun surfaces as its own frame.
    text = "Toimitettava viimeistään päivänä, jona määräaika päättyi."
    frames = recognize_procedure_frames(text)
    maaraaika_nouns = [
        f for f in frames
        if f.process_kind is ProcessKind.MAARAAIKA
        and _frame_text(text, f).startswith("määräaika")
    ]
    assert maaraaika_nouns, (
        "the määräaika noun after the viimeistään window must surface as its "
        "own MAARAAIKA frame (window must not swallow it)"
    )


def test_numeric_deadline_window_spans_full_cue() -> None:
    text = "Päätös on tehtävä 30 päivän kuluessa."
    frames = recognize_procedure_frames(text)
    paatos = [f for f in frames if f.process_kind is ProcessKind.PAATOS][0]
    assert paatos.deadline_span is not None
    s = paatos.deadline_span.byte_offset
    assert text[s : s + paatos.deadline_span.byte_len] == "30 päivän kuluessa"


# ---------------------------------------------------------------------------
# 3. Lens adapter == recognizer pass-through (consuming token_tape).
# ---------------------------------------------------------------------------


def _make_bundle(text: str) -> SourceSurfaceBundle:
    sid = "test/1-000#body"
    tape = build_token_tape(sid, text)
    overlay = build_morph_overlay(tape)
    ref = SourceSpanRef(
        source_unit_id=sid,
        source_hash="h",
        work_id="test/1-000",
        address=None,
        char_start=0,
        char_end=len(text),
        text_hash="th",
    )
    unit = SourceSurfaceUnit(
        source_unit_id=sid,
        work_id="test/1-000",
        address=None,
        raw_text=text,
        source_hash="h",
        source_ref=ref,
        token_tape=tape,
        morph_overlay=overlay,
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="test/1-000",
        scope={"kind": "whole_work"},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    return SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))


def test_lens_requires_token_tape_view() -> None:
    assert ProcedureLens.required_views == ("token_tape",)


def test_lens_passthrough_matches_recognizer() -> None:
    text = "hakijan on toimitettava hakemus viimeistään 30 päivän kuluessa."
    bundle = _make_bundle(text)
    result = ProcedureLens().analyze(
        bundle, context=SurfaceAnalysisContext()
    )
    scan = scan_procedure(text)
    # one node per recognizer frame, same process_kind set, same head offsets
    node_kinds = sorted(
        str(n.payload["process_kind"]) for n in result.node_seeds
    )
    frame_kinds = sorted(f.process_kind.value for f in scan.frames)
    assert node_kinds == frame_kinds
    assert all(n.source_ref is not None for n in result.node_seeds)
    node_offsets = sorted(
        n.source_ref.char_start
        for n in result.node_seeds
        if n.source_ref is not None
    )
    frame_offsets = sorted(f.source_span.byte_offset for f in scan.frames)
    assert node_offsets == frame_offsets


def test_lens_tolerates_unpopulated_views() -> None:
    # If the substrate did not populate token_tape/morph_overlay, the lens
    # builds them on demand from raw_text rather than failing.
    text = "Viranomaisen on tehtävä päätös 14 päivän kuluttua."
    bundle = _make_bundle(text)
    bare_unit = SourceSurfaceUnit(
        source_unit_id=bundle.units[0].source_unit_id,
        work_id=bundle.units[0].work_id,
        address=None,
        raw_text=text,
        source_hash="h",
        source_ref=bundle.units[0].source_ref,
        token_tape=None,
        morph_overlay=None,
    )
    bare_bundle = SourceSurfaceBundle(
        jurisdiction="fi", subject=bundle.subject, units=(bare_unit,)
    )
    result = ProcedureLens().analyze(
        bare_bundle, context=SurfaceAnalysisContext()
    )
    kinds = {str(n.payload["process_kind"]) for n in result.node_seeds}
    assert "paatos" in kinds
    assert "maaraaika" in kinds


# ---------------------------------------------------------------------------
# 4. Determinism + tape-vs-text equivalence.
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    text = "päätös on tehtävä 30 päivän kuluessa; lausunnon antaa ministeriö."
    a = scan_procedure(text)
    b = scan_procedure(text)
    assert a == b


def test_prebuilt_tape_matches_on_demand_tape() -> None:
    # Passing a prebuilt tape (the lens path) yields the same frames as the
    # on-demand build (the bare-text path).
    text = "hakijan on toimitettava hakemus viimeistään 30 päivän kuluessa."
    on_demand = scan_procedure(text)
    tape = build_token_tape("u#body", text)
    overlay = build_morph_overlay(tape)
    prebuilt = scan_procedure(text, tape=tape, overlay=overlay)
    assert on_demand == prebuilt
