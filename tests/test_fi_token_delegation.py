"""Token-native unit tests for the H5 delegation recognizer (decision B rewrite).

The recognizer now consumes a :class:`TokenTape` and emits WHOLE-TOKEN-aligned
spans. These tests assert: token-aligned spans, correct frame PAYLOAD fields
(delegate_actor / instrument_kind / binding_strength / subject_span), the shared
case-sensitive token-actor matcher, residual typing, determinism, and lens ==
recognizer pass-through.
"""
from __future__ import annotations

from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
)
from lawvm.finland.legal_surface.lenses.delegation import DelegationLens
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.references.delegation import recognize_delegation_frames


def _tape(text: str):
    return build_token_tape("u#body", text)


def test_delegation_frame_fields_and_token_aligned_span() -> None:
    text = "Valtioneuvoston asetuksella säädetään tarkemmin asian käsittelystä."
    scan = recognize_delegation_frames(_tape(text))
    assert len(scan.frames) == 1
    fr = scan.frames[0]
    assert fr.delegate_actor == "Valtioneuvoston"
    assert fr.instrument_kind == "asetus"
    assert fr.binding_strength == "must"
    assert fr.delegation_status == "surface_fact_only"
    # whole-frame (clause) span is whole-token aligned
    s = fr.source_span
    clause = text[s.byte_offset : s.byte_offset + s.byte_len]
    assert clause.startswith("Valtioneuvoston asetuksella säädetään")


def test_delegation_subject_span_is_surface_only() -> None:
    text = "Valtioneuvoston asetuksella säädetään tarkemmin menettelystä."
    scan = recognize_delegation_frames(_tape(text))
    assert len(scan.frames) == 1
    subj = scan.frames[0].subject_span
    assert subj is not None
    captured = text[subj.byte_offset : subj.byte_offset + subj.byte_len]
    assert captured == "tarkemmin menettelystä"


def test_permissive_modal_yields_may_binding() -> None:
    text = "Ministeriö voi antaa määräyksiä tarkemmista seikoista."
    scan = recognize_delegation_frames(_tape(text))
    assert len(scan.frames) == 1
    fr = scan.frames[0]
    assert fr.instrument_kind == "määräys"
    assert fr.binding_strength == "may"
    # bare generic "Ministeriö" resolves to the generic role (not an arbitrary id)
    assert fr.delegate_actor == "Ministeriö"


def test_hyphenated_ministry_delegate_actor() -> None:
    text = (
        "liikenne- ja viestintäministeriön asetuksella säädetään "
        "tarkemmin asiasta."
    )
    scan = recognize_delegation_frames(_tape(text))
    assert scan.frames, "expected a delegation frame for the hyphenated ministry"
    fr = scan.frames[0]
    assert fr.delegate_actor == "liikenne- ja viestintäministeriön"
    assert fr.instrument_kind == "asetus"


def test_instrument_noun_without_delegation_verb_is_not_a_frame() -> None:
    # A bare cross-reference to an instrument (no delegation verb) is not a
    # delegation clause and emits neither a frame nor a residual.
    text = "Tätä lakia sovelletaan asetuksen 3 §:ssä tarkoitettuihin asioihin."
    scan = recognize_delegation_frames(_tape(text))
    assert not scan.frames


def test_bare_asetuksella_is_a_grant_with_underspecified_holder() -> None:
    # DELEGATION-UNIFY-VERDICT step 4 / FRONTIER adjudication (old_C_correct):
    # a bare / impersonal ``asetuksella säädetään`` (no overt issuer) DOES grant
    # the power to issue a decree — the issuer is UNDERSPECIFIED, not absent. The
    # old B residualized this as ``delegation_without_actor`` and LOST the genuine
    # grant; the canonical parser (B now calls it) emits the grant with an empty
    # ``delegate_actor`` (underspecified issuer). This was an adjudicated old-B
    # MISS, so the test now pins the corrected behavior.
    text = "Tarkemmista seikoista asetuksella säädetään myöhemmin."
    scan = recognize_delegation_frames(_tape(text))
    assert len(scan.frames) == 1
    assert scan.frames[0].instrument_kind == "asetus"
    assert scan.frames[0].delegate_actor == ""  # issuer underspecified, not absent
    assert scan.residuals == ()


def test_recognizer_is_deterministic() -> None:
    text = (
        "Valtioneuvoston asetuksella säädetään tarkemmin asiasta. "
        "Ministeriö voi antaa ohjeita soveltamisesta."
    )
    a = recognize_delegation_frames(_tape(text))
    b = recognize_delegation_frames(_tape(text))
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
    text = "Valtioneuvoston asetuksella säädetään tarkemmin asian käsittelystä."
    scan = recognize_delegation_frames(build_token_tape("u#body", text))
    lens = DelegationLens()
    result = lens.analyze(_bundle(text), context=SurfaceAnalysisContext())
    assert len(result.node_seeds) == len(scan.frames) == 1
    seed = result.node_seeds[0]
    fr = scan.frames[0]
    assert seed.node_kind == "delegation_frame"
    assert seed.payload["delegate_actor"] == fr.delegate_actor
    assert seed.payload["instrument_kind"] == fr.instrument_kind
    assert seed.payload["binding_strength"] == fr.binding_strength
    assert seed.source_ref is not None
    assert seed.source_ref.char_start == fr.source_span.byte_offset


def test_lens_requires_token_tape_view() -> None:
    lens = DelegationLens()
    assert lens.required_views == ("token_tape",)
