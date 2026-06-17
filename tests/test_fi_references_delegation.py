"""Tests for the H5 surface delegation-frame recognizer
(``lawvm.finland.references.delegation``).

Note: ``tests/test_fi_delegation.py`` already exists and tests the unrelated
pre-existing AKN graph-edge delegation extractor (``lawvm.finland.delegation``).
This file tests the new H5 surface-fact lens in ``references/delegation.py``.

Covers each canonical delegation surface shape, the may/must distinction from
the modal surface, the untypeable-actor → residual fail-loud path, and the
hard safety boundary that NO legal-validity / discretion vocabulary is emitted.
"""
from __future__ import annotations

import dataclasses

from lawvm.finland.references.delegation import (
    DelegationFrame,
    DelegationResidual,
    DelegationScan,
    recognize_delegation_frames,
)


def _frames(text: str) -> tuple[DelegationFrame, ...]:
    return recognize_delegation_frames(text).frames


def _residuals(text: str) -> tuple[DelegationResidual, ...]:
    return recognize_delegation_frames(text).residuals


# ---------------------------------------------------------------------------
# Canonical shape coverage
# ---------------------------------------------------------------------------


def test_valtioneuvoston_asetuksella_saadetaan_is_must():
    text = "Valtioneuvoston asetuksella säädetään tarkemmin maksun perusteista."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor.lower().startswith("valtioneuvosto")
    assert f.instrument_kind == "asetus"
    assert f.binding_strength == "must"
    assert f.status == "surface_fact_only"
    assert f.rule_id == "fi.surface.delegation.v1"


def test_voidaan_saataa_valtioneuvoston_asetuksella_is_may():
    text = "Tarkemmista perusteista voidaan säätää valtioneuvoston asetuksella."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor.lower().startswith("valtioneuvosto")
    assert f.instrument_kind == "asetus"
    assert f.binding_strength == "may"


def test_ministerio_voi_antaa_maarayksia_is_maaray_may():
    text = "Ministeriö voi antaa määräyksiä lomakkeiden sisällöstä."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor.lower().startswith("ministeriö")
    assert f.instrument_kind == "määräys"
    assert f.binding_strength == "may"


def test_ministerion_asetuksella_saadetaan_is_asetus_must():
    text = "Ministeriön asetuksella säädetään hakemuksen liitteistä."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor.lower().startswith("ministeriö")
    assert f.instrument_kind == "asetus"
    assert f.binding_strength == "must"


def test_named_agency_antaa_tarkempia_maarayksia_is_maaray_must():
    text = "Verohallinto antaa tarkempia määräyksiä ilmoituksen antamisesta."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor == "Verohallinto"
    assert f.instrument_kind == "määräys"
    # No permissive modal → must.
    assert f.binding_strength == "must"


def test_agency_voi_antaa_ohjeita_is_ohje_may():
    text = "Energiavirasto voi antaa ohjeita mittauksen järjestämisestä."
    frames = _frames(text)
    assert len(frames) == 1
    f = frames[0]
    assert f.delegate_actor == "Energiavirasto"
    assert f.instrument_kind == "ohje"
    assert f.binding_strength == "may"


# ---------------------------------------------------------------------------
# The may vs must distinction (explicit)
# ---------------------------------------------------------------------------


def test_may_vs_must_distinction_from_modal_surface():
    must = "Valtioneuvoston asetuksella säädetään asiasta."
    may = "Asiasta voidaan säätää valtioneuvoston asetuksella."
    (mf,) = _frames(must)
    (yf,) = _frames(may)
    assert mf.binding_strength == "must"
    assert yf.binding_strength == "may"
    # Same actor + instrument, differing only in binding strength.
    assert mf.instrument_kind == yf.instrument_kind == "asetus"
    assert mf.delegate_actor.lower().startswith("valtioneuvosto")
    assert yf.delegate_actor.lower().startswith("valtioneuvosto")


# ---------------------------------------------------------------------------
# Subject span
# ---------------------------------------------------------------------------


def test_subject_span_captured_as_surface():
    text = "Valtioneuvoston asetuksella säädetään tarkemmin maksun perusteista."
    (f,) = _frames(text)
    assert f.subject_span is not None
    captured = text[
        f.subject_span.byte_offset : f.subject_span.byte_offset
        + f.subject_span.byte_len
    ]
    assert "perusteista" in captured


# ---------------------------------------------------------------------------
# Fail-loud: untypeable / missing actor → typed residual, never a guess
# ---------------------------------------------------------------------------


def test_delegation_without_known_actor_yields_residual():
    # "lautakunta" is not in the registry nor the closed role list, so the
    # delegation-shaped clause cannot be typed → residual, not a guessed frame.
    text = "Lautakunta antaa tarkempia määräyksiä asian käsittelystä."
    frames = _frames(text)
    residuals = _residuals(text)
    assert frames == ()
    assert len(residuals) == 1
    r = residuals[0]
    assert r.kind == "delegation_without_actor"
    # Self-evidencing: the offending clause text is embedded.
    assert "Lautakunta" in r.detail
    assert "Lautakunta" in r.surface_text
    assert "määräys" in r.detail


def test_residual_never_guesses_an_actor():
    text = "Lautakunta antaa tarkempia määräyksiä asian käsittelystä."
    # No frame at all means no actor was guessed.
    assert _frames(text) == ()


# ---------------------------------------------------------------------------
# Non-delegation instrument mentions do not fire
# ---------------------------------------------------------------------------


def test_bare_instrument_cross_reference_does_not_fire():
    # An instrument noun with no delegation verb is a cross-reference, not a
    # delegation clause.
    text = "Mitä asetuksen 3 §:ssä tarkoitetaan, sovelletaan myös tässä."
    assert _frames(text) == ()
    assert _residuals(text) == ()


def test_empty_and_irrelevant_text():
    assert recognize_delegation_frames("") == DelegationScan(frames=(), residuals=())
    assert recognize_delegation_frames(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2027."
    ) == DelegationScan(frames=(), residuals=())


# ---------------------------------------------------------------------------
# HARD SAFETY BOUNDARY: no legal-validity / discretion vocabulary anywhere
# ---------------------------------------------------------------------------


def test_no_legal_conclusion_vocabulary_emitted():
    """Surface facts only: no field may carry a legal-validity/discretion verdict."""
    texts = [
        "Valtioneuvoston asetuksella säädetään tarkemmin maksun perusteista.",
        "Asiasta voidaan säätää valtioneuvoston asetuksella.",
        "Ministeriö voi antaa määräyksiä lomakkeista.",
        "Verohallinto antaa tarkempia määräyksiä ilmoituksesta.",
        "Lautakunta antaa tarkempia määräyksiä asian käsittelystä.",
    ]
    # Vocabulary that would betray a legal conclusion (English + Finnish).
    forbidden = (
        "valid",
        "invalid",
        "discretion",
        "discretionary",
        "power",
        "ultra vires",
        "constitution",
        "unconstitutional",
        "lawful",
        "unlawful",
        "duty",
        "obligation",
        "pätevä",
        "pätemätön",
        "harkintavalta",
        "toimivalta",
        "perustuslain",
    )
    for text in texts:
        scan = recognize_delegation_frames(text)
        for item in (*scan.frames, *scan.residuals):
            for fld in dataclasses.fields(item):
                value = getattr(item, fld.name)
                if isinstance(value, str):
                    low = value.lower()
                    for word in forbidden:
                        assert word not in low, (
                            f"legal-conclusion token {word!r} leaked into "
                            f"{type(item).__name__}.{fld.name} = {value!r}"
                        )

    # The status literal itself is the only verdict, and it is surface-only.
    (f,) = _frames(texts[0])
    assert f.status == "surface_fact_only"


def test_binding_strength_is_closed_vocab():
    for text, expected in (
        ("Valtioneuvoston asetuksella säädetään asiasta.", "must"),
        ("Asiasta voidaan säätää valtioneuvoston asetuksella.", "may"),
    ):
        (f,) = _frames(text)
        assert f.binding_strength in {"must", "may"}
        assert f.binding_strength == expected


def test_instrument_kind_is_closed_vocab():
    closed = {"asetus", "määräys", "ohje", "päätös"}
    texts = [
        "Valtioneuvoston asetuksella säädetään asiasta.",
        "Ministeriö voi antaa määräyksiä asiasta.",
        "Energiavirasto voi antaa ohjeita asiasta.",
    ]
    for text in texts:
        for f in _frames(text):
            assert f.instrument_kind in closed
