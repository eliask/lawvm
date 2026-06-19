"""Token-grammar tests for the rewritten H5 sanction recognizer (decision B).

The recognizer is now a TOKEN/GRAMMAR recognizer over the source-preserving
:class:`~lawvm.core.legal_surface_tokens.TokenTape`: the marker is a ``word``
TOKEN and the kind comes from matching closed sanction stems against ONE token's
``normalized`` (not a substring anywhere in a maximal ``\\w`` run). These tests
assert:

  * token-aligned marker spans (``marker_surface == token.text``, span ==
    ``token.char_start``/``.char_end``);
  * correct ``sanction_kind`` per closed kind;
  * the ``jos2sakko`` digit-glue artifact — ONE ``\\w`` run for the old regex —
    now classifies on the SPLIT ``sakko`` TOKEN (intended improvement);
  * lens adapter == recognizer pass-through (token spans flow to SourceSpanRef);
  * determinism.
"""
from __future__ import annotations

from lawvm.core.legal_surface_lens import (
    SourceSurfaceBundle,
    SourceSurfaceUnit,
    SurfaceAnalysisContext,
)
from lawvm.core.legal_surface_graph import SourceSpanRef, SurfaceGraphSubject
from lawvm.finland.legal_surface.lenses.sanction import SanctionLens
from lawvm.finland.legal_surface.tokenize import build_token_tape
from lawvm.finland.references.sanction import (
    SanctionKind,
    recognize_sanction_frames,
    sanction_kind,
)


def _frames_of_kind(scan, kind: SanctionKind):
    return [f for f in scan.frames if f.sanction_kind == kind]


# ---------------------------------------------------------------------------
# Token-aligned marker spans
# ---------------------------------------------------------------------------


def test_marker_span_is_token_aligned() -> None:
    text = "Teosta voidaan määrätä sakko."
    tape = build_token_tape("u#0", text)
    scan = recognize_sanction_frames(text, tape=tape)
    frames = _frames_of_kind(scan, SanctionKind.SAKKO)
    assert frames, "expected a SAKKO frame"
    f = frames[0]
    # marker surface is the verbatim token text
    assert f.marker_surface == "sakko"
    # the marker token's exact span exists on the tape
    sakko_tokens = [t for t in tape.tokens if t.normalized == "sakko"]
    assert sakko_tokens, "tokenizer must produce a 'sakko' word token"
    tok = sakko_tokens[0]
    assert text[tok.char_start : tok.char_end] == "sakko"


def test_rangaistaan_inflected_single_token_classifies() -> None:
    text = "Joka rikkoo kieltoa, rangaistaan sakolla."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.RANGAISTUS)
    assert frames, "rangaistaan (one token) must type to RANGAISTUS via 'rangais'"
    assert frames[0].marker_surface == "rangaistaan"


def test_uhkasakko_beats_bare_sakko_token() -> None:
    text = "Velvoitteen tehosteeksi voidaan asettaa uhkasakko."
    scan = recognize_sanction_frames(text)
    assert _frames_of_kind(scan, SanctionKind.UHKASAKKO)
    # longest-first stem ordering on the ONE 'uhkasakko' token: no bare SAKKO
    assert not _frames_of_kind(scan, SanctionKind.SAKKO)


# ---------------------------------------------------------------------------
# The jos2sakko digit-glue split (intended improvement over the old regex)
# ---------------------------------------------------------------------------


def test_digit_glued_marker_classifies_on_split_token() -> None:
    # Old regex: _WORD_RE = [\wäöåÄÖÅ]+ treats 'jos2sakko' as ONE run and the
    # 'sakko' substring matched anywhere -> a SAKKO frame whose marker_surface
    # was the whole glued run 'jos2sakko'. The tokenizer splits letters/digits:
    # 'jos' | '2' | 'sakko'. The frame now classifies on the 'sakko' TOKEN.
    text = "Rangaistuksena jos2sakko peritään."
    tape = build_token_tape("u#0", text)
    # tokenizer splits the glue into three tokens
    norms = [t.normalized for t in tape.tokens if t.category in ("word", "number")]
    assert "jos" in norms and "2" in norms and "sakko" in norms, norms

    scan = recognize_sanction_frames(text, tape=tape)
    frames = _frames_of_kind(scan, SanctionKind.SAKKO)
    assert frames, "the split 'sakko' token must type to SAKKO"
    f = frames[0]
    # marker is the SPLIT token, NOT the glued 'jos2sakko' run
    assert f.marker_surface == "sakko"
    sakko_tok = [t for t in tape.tokens if t.normalized == "sakko"][0]
    # the marker span covers exactly the 'sakko' token, not the digit glue
    assert text[sakko_tok.char_start : sakko_tok.char_end] == "sakko"
    # the source_span end aligns to the sakko token end (no trailing glue)
    assert f.source_span.byte_offset + f.source_span.byte_len == sakko_tok.char_end


def test_sanction_kind_helper_is_token_granular() -> None:
    # The exported classifier operates on ONE token's normalized text.
    assert sanction_kind("sakko") == SanctionKind.SAKKO
    assert sanction_kind("uhkasakko") == SanctionKind.UHKASAKKO
    assert sanction_kind("rangaistaan") == SanctionKind.RANGAISTUS
    assert sanction_kind("vahingonkorvauksen") == SanctionKind.VAHINGONKORVAUS
    # a non-sanction token types to nothing
    assert sanction_kind("säädetään") is None
    # revocation stems are owned by the compound rule, never the bare arm
    assert sanction_kind("peruuttaa") is None


# ---------------------------------------------------------------------------
# Permit-revocation compound rule on tokens
# ---------------------------------------------------------------------------


def test_permit_revocation_frame_token_marker() -> None:
    text = "Viranomainen voi peruuttaa luvan, jos ehtoja rikotaan."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.LUVAN_PERUUTTAMINEN)
    assert frames, "peruuttaa luvan must type to LUVAN_PERUUTTAMINEN"
    assert frames[0].marker_surface == "peruuttaa"


def test_revoke_without_permit_is_residual() -> None:
    text = "Viranomainen voi peruuttaa päätöksen myöhemmin."
    scan = recognize_sanction_frames(text)
    assert _frames_of_kind(scan, SanctionKind.LUVAN_PERUUTTAMINEN) == []
    res = [r for r in scan.residuals if r.kind == "revoke_without_permit"]
    assert res, "revoke with no permit noun is a typed residual"
    assert res[0].surface_text == "peruuttaa"


def test_untypeable_sanction_shaped_token_is_residual() -> None:
    text = "Asia jätettiin tuomitsematta."
    scan = recognize_sanction_frames(text)
    assert _frames_of_kind(scan, SanctionKind.RANGAISTUS) == []
    res = [r for r in scan.residuals if r.kind == "untypeable_sanction_token"]
    assert res, f"expected untypeable residual; residuals={scan.residuals}"
    assert res[0].surface_text == "tuomitsematta"


# ---------------------------------------------------------------------------
# Lens == recognizer pass-through
# ---------------------------------------------------------------------------


def _dummy_ref(raw_text: str) -> SourceSpanRef:
    import hashlib

    return SourceSpanRef(
        source_unit_id="u#0",
        source_hash="h0",
        work_id="w0",
        address=None,
        char_start=0,
        char_end=len(raw_text),
        text_hash=hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    )


def _unit(raw_text: str, *, with_tape: bool) -> SourceSurfaceUnit:
    return SourceSurfaceUnit(
        source_unit_id="u#0",
        work_id="w0",
        address=None,
        raw_text=raw_text,
        source_hash="h0",
        source_ref=_dummy_ref(raw_text),
        token_tape=build_token_tape("u#0", raw_text) if with_tape else None,
    )


def _bundle(unit: SourceSurfaceUnit) -> SourceSurfaceBundle:
    return SourceSurfaceBundle(
        jurisdiction="fi",
        subject=SurfaceGraphSubject(
            jurisdiction="fi",
            work_id="w0",
            scope={},
            surface_time=None,
            source_bundle_hash="h0",
            language="fi",
        ),
        units=(unit,),
    )


def test_lens_passes_through_recognizer_token_spans() -> None:
    text = "Joka rikkoo kieltoa, rangaistaan sakolla. Velvoitteeksi asetetaan uhkasakko."
    tape = build_token_tape("u#0", text)
    scan = recognize_sanction_frames(text, tape=tape)

    res = SanctionLens().analyze(
        _bundle(_unit(text, with_tape=True)), context=SurfaceAnalysisContext()
    )
    # one node per recognizer frame; payload kind + marker match
    assert len(res.node_seeds) == len(scan.frames)
    by_marker = {
        (s.payload["sanction_kind"], s.payload["marker_surface"]): s
        for s in res.node_seeds
    }
    for f in scan.frames:
        seed = by_marker[(f.sanction_kind.value, f.marker_surface)]
        ref = seed.source_ref
        assert ref is not None
        assert ref.char_start == f.source_span.byte_offset
        assert ref.char_end == f.source_span.byte_offset + f.source_span.byte_len


def test_lens_builds_tape_when_substrate_left_it_none() -> None:
    text = "Teosta voidaan määrätä sakko."
    # substrate did NOT populate token_tape -> lens builds on demand
    res = SanctionLens().analyze(
        _bundle(_unit(text, with_tape=False)), context=SurfaceAnalysisContext()
    )
    assert any(s.payload["sanction_kind"] == "sakko" for s in res.node_seeds)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_determinism() -> None:
    text = (
        "Joka rikkoo kieltoa, rangaistaan sakolla. "
        "Elinkeinonharjoittajalle määrätään seuraamusmaksu. "
        "Viranomainen voi peruuttaa luvan."
    )
    a = recognize_sanction_frames(text)
    b = recognize_sanction_frames(text)
    assert a == b
    # tape-provided vs tape-built coincide
    c = recognize_sanction_frames(text, tape=build_token_tape("u#0", text))
    assert a == c


def test_required_views_is_token_tape() -> None:
    assert SanctionLens().required_views == ("token_tape",)
