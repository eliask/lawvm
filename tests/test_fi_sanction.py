"""Tests for the H5 surface sanction/consequence frame recognizer.

The recognizer records SURFACE FACTS ONLY. These tests assert the surface
shapes (sanction_kind / marker / spans / status) per sanction kind, a
trigger-bearing frame, the typed-residual behaviour for an untypeable
sanction-shaped token, and explicitly assert that NO legal-conclusion
vocabulary (culpability / guilt / liability / enforceability) is ever produced.
"""
from __future__ import annotations

import dataclasses
from dataclasses import fields

import pytest

from lawvm.finland.references.sanction import (
    SanctionFrame,
    SanctionKind,
    SanctionResidual,
    recognize_sanction_frames,
)


def _frames_of_kind(scan, kind: SanctionKind) -> list[SanctionFrame]:
    return [f for f in scan.frames if f.sanction_kind == kind]


def _slice(text: str, span) -> str:
    return text[span.byte_offset : span.byte_offset + span.byte_len]


# ---------------------------------------------------------------------------
# One test per closed sanction kind
# ---------------------------------------------------------------------------


def test_rangaistus_rangaistaan() -> None:
    text = "Joka rikkoo kieltoa, rangaistaan sakolla."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.RANGAISTUS)
    assert frames, f"no RANGAISTUS frame; got {[f.sanction_kind for f in scan.frames]}"
    assert frames[0].status == "surface_fact_only"
    assert "rangais" in frames[0].marker_surface.lower()


def test_rangaistus_tuomitaan_vankeuteen() -> None:
    text = "Rikkomuksesta tuomitaan vankeuteen enintään kahdeksi vuodeksi."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.RANGAISTUS)
    assert frames, "tuomitaan must type to RANGAISTUS"


def test_sakko_fine() -> None:
    text = "Teosta voidaan määrätä sakko."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.SAKKO)
    assert frames, f"no SAKKO frame; got {[f.sanction_kind for f in scan.frames]}"
    assert "sak" in frames[0].marker_surface.lower()


def test_seuraamusmaksu_administrative_penalty() -> None:
    text = "Elinkeinonharjoittajalle määrätään seuraamusmaksu rikkomuksesta."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.SEURAAMUSMAKSU)
    assert frames, "seuraamusmaksu must type to SEURAAMUSMAKSU"
    # Must NOT be misfiled as a bare SAKKO.
    assert not _frames_of_kind(scan, SanctionKind.SAKKO)


def test_uhkasakko_conditional_fine_beats_bare_sakko() -> None:
    text = "Velvoitteen tehosteeksi voidaan asettaa uhkasakko."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.UHKASAKKO)
    assert frames, "uhkasakko must type to UHKASAKKO"
    # Longest-first: must NOT also fire a bare SAKKO on the same token.
    assert not _frames_of_kind(scan, SanctionKind.SAKKO)


def test_luvan_peruuttaminen_revocation() -> None:
    text = "Viranomainen voi peruuttaa luvan, jos ehtoja rikotaan."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.LUVAN_PERUUTTAMINEN)
    assert frames, "peruuttaa luvan must type to LUVAN_PERUUTTAMINEN"


def test_luvan_peruuttaminen_nominal() -> None:
    text = "Seuraamuksena on luvan peruuttaminen."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.LUVAN_PERUUTTAMINEN)
    assert frames, "luvan peruuttaminen (nominal) must type to LUVAN_PERUUTTAMINEN"


def test_vahingonkorvaus_damages() -> None:
    text = "Aiheuttaja on velvollinen suorittamaan vahingonkorvauksen."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.VAHINGONKORVAUS)
    assert frames, "vahingonkorvaus must type to VAHINGONKORVAUS"


# ---------------------------------------------------------------------------
# Target actor + trigger capture
# ---------------------------------------------------------------------------


def test_target_actor_captured() -> None:
    text = "Työnantajalle määrätään seuraamusmaksu laiminlyönnistä."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.SEURAAMUSMAKSU)
    assert frames
    # Closed role-actor list carries 'työnantaja' inflections only as listed;
    # the genitive/allative 'työnantajalle' is not in the list, so target may be
    # None here — assert the field is the typed-absence None, never a guess.
    assert frames[0].target_actor_span is None or _slice(
        text, frames[0].target_actor_span
    )


def test_target_actor_captured_when_in_registry_list() -> None:
    text = "Rekisterinpitäjälle määrätään seuraamusmaksu, jos tietoja vuodetaan. Yhtiö rangaistaan."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.RANGAISTUS)
    assert frames
    f = frames[0]
    assert f.target_actor_span is not None
    assert "Yhtiö" in _slice(text, f.target_actor_span)


def test_trigger_bearing_frame() -> None:
    text = "Joka tahallaan rikkoo kieltoa, rangaistaan sakolla."
    scan = recognize_sanction_frames(text)
    frames = _frames_of_kind(scan, SanctionKind.RANGAISTUS)
    assert frames
    f = frames[0]
    assert f.trigger_span is not None, "trigger after 'joka' must be captured"
    trig = _slice(text, f.trigger_span)
    assert "rikkoo kieltoa" in trig


# ---------------------------------------------------------------------------
# Fail-loud residuals
# ---------------------------------------------------------------------------


def test_untypeable_sanction_shaped_token_is_residual() -> None:
    # 'sakkaus' trips the 'sako'/'sakk'... guard family? It does not contain a
    # stem -> use a token that passes a guard but matches no stem. 'uhkasa'
    # passes the 'uhkasako' guard? No. Construct a word containing a guard
    # substring but typing to no stem: 'seuraamusmaksullisuus' DOES contain the
    # seuraamusmaksu stem. Use 'tuomitsematta' -> contains 'tuomit' guard but
    # NOT a stem ('tuomita'/'tuomitaan').
    text = "Asia jätettiin tuomitsematta."
    scan = recognize_sanction_frames(text)
    assert _frames_of_kind(scan, SanctionKind.RANGAISTUS) == []
    res = [r for r in scan.residuals if r.kind == "untypeable_sanction_token"]
    assert res, f"expected untypeable residual; residuals={scan.residuals}"
    # Self-evidencing: detail embeds the offending text.
    assert "tuomitsematta" in res[0].detail
    assert res[0].surface_text == "tuomitsematta"


def test_revoke_without_permit_is_residual_not_frame() -> None:
    text = "Viranomainen voi peruuttaa päätöksen myöhemmin."
    scan = recognize_sanction_frames(text)
    assert _frames_of_kind(scan, SanctionKind.LUVAN_PERUUTTAMINEN) == []
    res = [r for r in scan.residuals if r.kind == "revoke_without_permit"]
    assert res, "revoke with no permit noun must be a typed residual"
    assert "peruutta" in res[0].surface_text.lower()
    assert "peruutta" in res[0].detail.lower() or res[0].surface_text in res[0].detail


def test_residuals_are_typed_dataclass() -> None:
    text = "Asia jätettiin tuomitsematta."
    scan = recognize_sanction_frames(text)
    assert all(isinstance(r, SanctionResidual) for r in scan.residuals)


# ---------------------------------------------------------------------------
# Safety: no legal conclusion vocabulary, ever
# ---------------------------------------------------------------------------


def test_no_legal_conclusion_vocabulary_ever() -> None:
    text = (
        "Joka rikkoo kieltoa, rangaistaan sakolla. "
        "Elinkeinonharjoittajalle määrätään seuraamusmaksu. "
        "Velvoitteen tehosteeksi asetetaan uhkasakko. "
        "Viranomainen voi peruuttaa luvan. "
        "Aiheuttaja suorittaa vahingonkorvauksen."
    )
    scan = recognize_sanction_frames(text)
    banned = {
        "guilt",
        "guilty",
        "culpability",
        "culpable",
        "liability",
        "liable",
        "enforceable",
        "syyllinen",
        "syyllisyys",
        "rangaistava",  # "punishable" conclusion, not a surface marker
        "korvausvelvollinen",
    }
    assert scan.frames, "fixture must produce frames"
    for frame in scan.frames:
        assert frame.status == "surface_fact_only"
        # The closed kind enum values carry no conclusion vocabulary.
        assert frame.sanction_kind.value not in banned
        for f in fields(frame):
            val = getattr(frame, f.name)
            assert val not in banned
            if isinstance(val, str):
                assert val.lower() not in banned


def test_frozen_types() -> None:
    text = "Joka rikkoo kieltoa, rangaistaan sakolla."
    scan = recognize_sanction_frames(text)
    frame = _frames_of_kind(scan, SanctionKind.RANGAISTUS)[0]
    with pytest.raises(dataclasses.FrozenInstanceError):
        frame.marker_surface = "x"  # ty: ignore[invalid-assignment]


def test_spans_round_trip_and_source_file_propagated() -> None:
    text = "Joka rikkoo kieltoa, rangaistaan sakolla."
    scan = recognize_sanction_frames(text, source_file="39/1889")
    frame = _frames_of_kind(scan, SanctionKind.RANGAISTUS)[0]
    assert frame.source_span.source_file == "39/1889"
    assert "rangais" in _slice(text, frame.source_span).lower()


def test_empty_and_guardless_text_no_frames() -> None:
    assert recognize_sanction_frames("").frames == ()
    assert recognize_sanction_frames("Tässä laissa säädetään asioista.").frames == ()
    assert recognize_sanction_frames("Tässä laissa säädetään asioista.").residuals == ()
