"""Tests for the scoped, fail-loud case-frame role binder (SPIKE).

POSITIVE tests assert the deterministic role assignments for the canonical
operative shapes from the design ruling.  NEGATIVE tests are the proof of
fail-loud soundness: under syncretism, a missing required role, unresolved
coordination, or anaphora the binder must emit TYPED RESIDUE and bind NO role.
"""

from __future__ import annotations

from lawvm.finland.legal_surface.case_frame import (
    FrameRole,
    ResidueReason,
    bind_case_frames,
)
from lawvm.finland.morphology.api import MorphCase


def _assignment(text: str):
    result = bind_case_frames(text)
    assert len(result.assignments) == 1, (text, result.residues)
    assert not [
        r for r in result.residues if r.reason is not ResidueReason.NO_LICENSED_FRAME
    ], (text, result.residues)
    return result.assignments[0]


def _roles(assignment) -> dict[FrameRole, str]:
    return {b.role: b.span_text for b in assignment.bindings}


def _case_of(assignment, role: FrameRole) -> MorphCase | None:
    for b in assignment.bindings:
        if b.role is role:
            return b.case
    raise AssertionError(f"role {role} not bound")


def _residue_reasons(text: str) -> set[ResidueReason]:
    result = bind_case_frames(text)
    assert not result.assignments, (text, result.assignments)
    return {r.reason for r in result.residues}


# ----------------------------------------------------------------------------
# POSITIVE — canonical operative shapes
# ----------------------------------------------------------------------------


def test_lisata_payload_and_illative_goal_container() -> None:
    a = _assignment("Lakiin lisätään uusi 5 a §.")
    assert a.frame_id == "LISATA"
    roles = _roles(a)
    assert "uusi 5 a §" in roles[FrameRole.PAYLOAD]
    assert roles[FrameRole.GOAL_CONTAINER].strip().lower() == "lakiin"
    assert _case_of(a, FrameRole.GOAL_CONTAINER) is MorphCase.ILL


def test_lisata_into_section_illative_pykala_suffix() -> None:
    a = _assignment("lisätään 8 §:ään uusi 3 momentti")
    assert a.frame_id == "LISATA"
    roles = _roles(a)
    assert roles[FrameRole.GOAL_CONTAINER] == "8 §:ään"
    assert _case_of(a, FrameRole.GOAL_CONTAINER) is MorphCase.ILL
    assert "uusi 3 momentti" in roles[FrameRole.PAYLOAD]


def test_lisata_payload_only_no_container() -> None:
    a = _assignment("lisätään uusi 5 a §")
    assert a.frame_id == "LISATA"
    roles = _roles(a)
    assert "uusi 5 a §" in roles[FrameRole.PAYLOAD]
    assert FrameRole.GOAL_CONTAINER not in roles  # optional, not present


def test_kumota_section_target() -> None:
    a = _assignment("kumotaan 7 §")
    assert a.frame_id == "KUMOTA"
    assert _roles(a)[FrameRole.TARGET] == "7 §"
    assert _case_of(a, FrameRole.TARGET) is MorphCase.NOM


def test_kumota_chapter_target() -> None:
    a = _assignment("kumotaan 3 luku")
    assert a.frame_id == "KUMOTA"
    assert _roles(a)[FrameRole.TARGET] == "3 luku"


def test_kumota_subsection_target_genitive_chain() -> None:
    a = _assignment("Kumotaan lain 3 §:n 2 momentti.")
    assert a.frame_id == "KUMOTA"
    assert _roles(a)[FrameRole.TARGET] == "3 §:n 2 momentti"


def test_korvata_target_and_adessive_replacement() -> None:
    a = _assignment("Korvataan 4 § uudella säännöksellä.")
    assert a.frame_id == "KORVATA"
    roles = _roles(a)
    assert roles[FrameRole.TARGET] == "4 §"
    assert "säännöksellä" in roles[FrameRole.REPLACEMENT]
    assert _case_of(a, FrameRole.REPLACEMENT) is MorphCase.ADE


def test_siirtaa_source_and_translative_destination() -> None:
    a = _assignment("Siirretään 4 §:n 2 momentti 5 §:ksi.")
    assert a.frame_id == "SIIRTAA"
    roles = _roles(a)
    assert roles[FrameRole.SOURCE] == "4 §:n 2 momentti"
    assert roles[FrameRole.DESTINATION] == "5 §:ksi"
    assert _case_of(a, FrameRole.DESTINATION) is MorphCase.TRA


def test_saataa_asetuksella_instrument_and_elative_topic() -> None:
    a = _assignment("Valtioneuvoston asetuksella säädetään tarkemmin asiasta.")
    assert a.frame_id == "SAATAA_ASETUKSELLA"
    roles = _roles(a)
    assert "asetuksella" in roles[FrameRole.INSTRUMENT]
    assert _case_of(a, FrameRole.INSTRUMENT) is MorphCase.ADE
    assert "asiasta" in roles[FrameRole.TOPIC]
    assert _case_of(a, FrameRole.TOPIC) is MorphCase.ELA


def test_saataa_asetuksella_instrument_only() -> None:
    a = _assignment("Valtioneuvoston asetuksella säädetään.")
    assert a.frame_id == "SAATAA_ASETUKSELLA"
    roles = _roles(a)
    assert "asetuksella" in roles[FrameRole.INSTRUMENT]
    assert FrameRole.TOPIC not in roles  # optional, absent


# ----------------------------------------------------------------------------
# NEGATIVE — fail-loud soundness (the proof)
# ----------------------------------------------------------------------------


def test_anaphora_lisata_is_unsupported_residue() -> None:
    # `siihen lisätään ...` — the goal container is an anaphoric pronoun the
    # spike will NOT resolve.  Must refuse, never guess a container.
    reasons = _residue_reasons("siihen lisätään uusi 3 momentti")
    assert ResidueReason.UNSUPPORTED_ANAPHORA in reasons


def test_anaphora_kumota_goal_is_unsupported_residue() -> None:
    # An explicit anaphoric pronoun (`niistä kumotaan ...`) blocks binding.
    reasons = _residue_reasons("niistä kumotaan 3 momentti")
    assert ResidueReason.UNSUPPORTED_ANAPHORA in reasons


def test_missing_required_destination_is_residue() -> None:
    # `siirretään 3 §` has a source but NO translative destination → refuse.
    reasons = _residue_reasons("siirretään 3 §")
    assert ResidueReason.MISSING_REQUIRED_ROLE in reasons


def test_missing_required_replacement_is_residue() -> None:
    # `korvataan 4 §` with no adessive replacement → refuse.
    reasons = _residue_reasons("korvataan 4 §")
    assert ResidueReason.MISSING_REQUIRED_ROLE in reasons


def test_missing_required_instrument_is_residue() -> None:
    # `säädetään asiasta` (no adessive instrument) → refuse.
    reasons = _residue_reasons("säädetään asiasta")
    assert ResidueReason.MISSING_REQUIRED_ROLE in reasons


def test_elided_head_coordination_is_residue() -> None:
    # `kumotaan 3, 5 ja 7 §` — bare 3 and 5 share the elided § head.  The spike
    # must refuse rather than silently binding only `7 §`.
    reasons = _residue_reasons("kumotaan 3, 5 ja 7 §")
    assert ResidueReason.COORDINATION_SCOPE_UNRESOLVED in reasons


def test_dash_range_is_not_coordination_residue() -> None:
    # `kumotaan 21–23 §` is a single recognized RANGE, not elided coordination.
    a = _assignment("kumotaan 21–23 §")
    assert a.frame_id == "KUMOTA"
    assert _roles(a)[FrameRole.TARGET] == "21–23 §"


def test_multiple_payloads_is_ambiguous_residue() -> None:
    # Two coordinated payloads under one lisätään: the spike does not resolve
    # which payload pairs with which container → CaseFrameAmbiguous.
    reasons = _residue_reasons("lisätään lakiin uusi 6 a luku sekä uusi 82 a ja 83 a §")
    assert ResidueReason.CASE_FRAME_AMBIGUOUS in reasons


def test_multiple_goal_containers_is_ambiguous_residue() -> None:
    # Several insertion sites in one clause: the goal_container role is
    # over-full → refuse, never pick one arbitrarily.
    reasons = _residue_reasons(
        "lisätään asetukseen uusi 35 a § lukuun 5, 104 a § lukuun 6 ja 133 b § lukuun 7"
    )
    assert ResidueReason.CASE_FRAME_AMBIGUOUS in reasons


def test_no_scoped_predicate_is_no_licensed_frame() -> None:
    # `muutetaan` is deliberately OUT of spike scope (MUUTTAA frame not built).
    reasons = _residue_reasons("muutetaan 12 §")
    assert reasons == {ResidueReason.NO_LICENSED_FRAME}


def test_empty_clause_is_no_licensed_frame() -> None:
    reasons = _residue_reasons("tämä laki tulee voimaan 1 päivänä tammikuuta 2024")
    assert reasons == {ResidueReason.NO_LICENSED_FRAME}


def test_no_role_is_guessed_under_any_residue() -> None:
    # Soundness invariant: whenever the binder refuses (any non-NO_LICENSED_FRAME
    # residue), it binds ZERO roles for that predicate — never a partial guess.
    for text in (
        "siihen lisätään uusi 3 momentti",
        "siirretään 3 §",
        "kumotaan 3, 5 ja 7 §",
        "lisätään lakiin uusi 6 a luku sekä uusi 82 a ja 83 a §",
    ):
        result = bind_case_frames(text)
        assert not result.assignments, (text, result.assignments)
        assert result.residues, text
