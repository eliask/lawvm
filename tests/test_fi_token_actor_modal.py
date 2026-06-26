"""Token-native unit tests for the H4 actor/modal recognizer (decision B rewrite).

The recognizer now consumes a :class:`TokenTape` and emits WHOLE-TOKEN-aligned
spans. These tests assert: token-aligned spans, correct frame PAYLOAD fields,
case-sensitivity (``VM`` vs ``vm``), multi-word / hyphenated actor matching,
determinism, and lens == recognizer pass-through.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
)
from lawvm.finland.legal_surface.lenses.actor_modal import ActorModalLens
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.references.actor_modal import recognize_actor_modal_frames


def _tape(text: str):
    return build_token_tape("u#body", text)


def test_actor_modal_frame_spans_are_token_aligned() -> None:
    text = "Valtioneuvosto voi antaa asetuksen tarkemmista säännöksistä."
    scan = recognize_actor_modal_frames(_tape(text))
    assert len(scan.frames) == 1
    fr = scan.frames[0]
    # actor span is whole-token aligned: text[start:end] == the verbatim actor
    a = fr.actor_span
    assert text[a.byte_offset : a.byte_offset + a.byte_len] == "Valtioneuvosto"
    assert fr.actor_surface == "Valtioneuvosto"
    # modal payload fields
    assert fr.modal.token == "voi"
    assert fr.modal.polarity == "positive"
    assert fr.modal.voice == "active"
    m = fr.modal.source_span
    assert text[m.byte_offset : m.byte_offset + m.byte_len] == "voi"
    assert fr.actor_status == "surface_fact_only"


def test_actor_modal_object_span_is_surface_only() -> None:
    text = "Viranomainen ei saa luovuttaa tietoja sivulliselle."
    scan = recognize_actor_modal_frames(_tape(text))
    assert len(scan.frames) == 1
    fr = scan.frames[0]
    assert fr.actor_surface == "Viranomainen"
    assert fr.modal.token == "ei saa"
    assert fr.modal.polarity == "negative"
    assert fr.object_span is not None
    obj = fr.object_span
    captured = text[obj.byte_offset : obj.byte_offset + obj.byte_len]
    assert captured == "luovuttaa tietoja sivulliselle"


def test_multiword_modal_reconstructed_from_tokens() -> None:
    text = "Hakija on velvollinen toimittamaan selvityksen."
    scan = recognize_actor_modal_frames(_tape(text))
    assert len(scan.frames) == 1
    assert scan.frames[0].modal.token == "on velvollinen"
    assert scan.frames[0].actor_surface == "Hakija"


def test_hyphenated_multiword_actor_matches_as_one_span() -> None:
    # A hyphenated, multi-word ministry registry phrase must match a run of
    # word + dash + whitespace + word tokens as ONE actor span.
    text = "liikenne- ja viestintäministeriö voi antaa määräyksiä asiasta."
    scan = recognize_actor_modal_frames(_tape(text))
    assert scan.frames, "expected a frame for the hyphenated ministry actor"
    fr = scan.frames[0]
    assert fr.actor_surface == "liikenne- ja viestintäministeriö"
    a = fr.actor_span
    assert (
        text[a.byte_offset : a.byte_offset + a.byte_len]
        == "liikenne- ja viestintäministeriö"
    )


def test_case_sensitive_actor_VM_matches_lowercase_vm_does_not() -> None:
    # "VM" is the registered ministry abbreviation; "vm" is NOT registered, so a
    # lowercase token must not produce an actor frame.
    upper = recognize_actor_modal_frames(_tape("VM antaa asetuksen asiasta."))
    assert any(fr.actor_surface == "VM" for fr in upper.frames)

    lower = recognize_actor_modal_frames(_tape("vm antaa asetuksen asiasta."))
    assert not any(fr.actor_surface == "vm" for fr in lower.frames)


def test_actor_does_not_match_inside_a_longer_word() -> None:
    # "kunta" is a role actor, but "kuntalainen" is a single word token, so the
    # role surface must NOT match inside it (token boundary == word boundary).
    text = "kuntalainen voi tehdä hakemuksen."
    scan = recognize_actor_modal_frames(_tape(text))
    assert not any(fr.actor_surface == "kunta" for fr in scan.frames)


def test_modal_without_actor_is_a_typed_residual() -> None:
    text = "Tästä asiasta säädetään erikseen."
    scan = recognize_actor_modal_frames(_tape(text))
    assert not scan.frames
    assert any(r.kind == "modal_without_actor" for r in scan.residuals)


def test_recognizer_is_deterministic() -> None:
    text = "Valtioneuvosto voi antaa asetuksen. Viranomainen ei saa luovuttaa tietoja."
    a = recognize_actor_modal_frames(_tape(text))
    b = recognize_actor_modal_frames(_tape(text))
    assert a == b


def _bundle(text: str) -> SourceSurfaceBundle:
    tape = build_token_tape("u#body", text)
    ref = SourceSpanRef(
        source_unit_id="u#body",
        source_hash="h",
        work_id="w",
        address=None,
        char_start=0,
        char_end=len(text),
        text_hash=tape.text_hash,
    )
    unit = SourceSurfaceUnit(
        source_unit_id="u#body",
        work_id="w",
        address=None,
        raw_text=text,
        source_hash="h",
        source_ref=ref,
        token_tape=tape,
    )
    subject = SurfaceGraphSubject(
        jurisdiction="fi",
        work_id="w",
        scope={"kind": "whole_work"},
        surface_time=None,
        source_bundle_hash="h",
        language="fi",
    )
    return SourceSurfaceBundle(jurisdiction="fi", subject=subject, units=(unit,))


def test_lens_passthrough_matches_recognizer() -> None:
    text = "Valtioneuvosto voi antaa asetuksen tarkemmista säännöksistä."
    scan = recognize_actor_modal_frames(build_token_tape("u#body", text))
    lens = ActorModalLens()
    result = lens.analyze(_bundle(text), context=SurfaceAnalysisContext())
    assert len(result.node_seeds) == len(scan.frames) == 1
    seed = result.node_seeds[0]
    fr = scan.frames[0]
    assert seed.node_kind == "actor_modal_frame"
    assert seed.payload["actor_surface"] == fr.actor_surface
    assert seed.payload["modal_token"] == fr.modal.token
    assert seed.payload["polarity"] == fr.modal.polarity
    assert seed.payload["voice"] == fr.modal.voice
    # span ref anchors the whole-frame span verbatim
    assert seed.source_ref is not None
    assert seed.source_ref.char_start == fr.source_span.byte_offset
    assert (
        seed.source_ref.char_end
        == fr.source_span.byte_offset + fr.source_span.byte_len
    )


def test_lens_requires_token_tape_view() -> None:
    lens = ActorModalLens()
    assert lens.required_views == ("token_tape",)
