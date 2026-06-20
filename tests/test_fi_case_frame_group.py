"""Tests for the PER-VERB-GROUP case-frame role layer (promoted spike).

The per-group entry point :func:`bind_roles_for_clause` consumes the johtolause
parser's already-segmented ``SurfaceClause`` verb groups and binds operative
roles PER group.  Per-group operation is what converts the raw-text spike's
whole-clause "refusals" on multi-verb compounds into per-group bindings: each
verb group carries exactly one operative verb.

POSITIVE tests assert the deterministic per-group bindings (one frame per group,
with the licensing case).  NEGATIVE tests prove the SAME fail-loud guarantees as
the raw-text engine now hold at verb-group granularity: a refused group binds
ZERO roles and carries the typed residue reason; the multi-payload/anaphora/
coordination limits stay typed residue, never guessed.
"""

from __future__ import annotations

from lawvm.finland.johtolause.api import parse_clause
from lawvm.finland.legal_surface.case_frame import (
    FrameRole,
    GroupRoleBinding,
    GroupRoleResidue,
    ResidueReason,
    bind_roles_for_clause,
)
from lawvm.finland.morphology.api import MorphCase


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _outcomes(text: str) -> tuple[GroupRoleBinding | GroupRoleResidue, ...]:
    return bind_roles_for_clause(parse_clause(text).surface_clause)


def _bindings(text: str) -> list[GroupRoleBinding]:
    return [o for o in _outcomes(text) if isinstance(o, GroupRoleBinding)]


def _residues(text: str) -> list[GroupRoleResidue]:
    return [o for o in _outcomes(text) if isinstance(o, GroupRoleResidue)]


def _roles(binding: GroupRoleBinding) -> dict[FrameRole, str]:
    return {b.role: b.span_text for b in binding.bindings}


def _case_of(binding: GroupRoleBinding, role: FrameRole) -> MorphCase | None:
    for b in binding.bindings:
        if b.role is role:
            return b.case
    raise AssertionError(f"role {role} not bound in group")


# ---------------------------------------------------------------------------
# POSITIVE — one frame per already-segmented verb group
# ---------------------------------------------------------------------------


def test_group_lisata_payload_and_illative_container() -> None:
    (b,) = _bindings("Lakiin lisätään uusi 5 a §.")
    assert b.verb_code == "L"
    assert b.frame_id == "LISATA"
    roles = _roles(b)
    assert "uusi 5 a §" in roles[FrameRole.PAYLOAD]
    assert roles[FrameRole.GOAL_CONTAINER].strip().lower() == "lakiin"
    assert _case_of(b, FrameRole.GOAL_CONTAINER) is MorphCase.ILL


def test_group_lisata_into_section_illative() -> None:
    (b,) = _bindings("lisätään 8 §:ään uusi 3 momentti")
    assert b.frame_id == "LISATA"
    roles = _roles(b)
    assert roles[FrameRole.GOAL_CONTAINER] == "8 §:ään"
    assert _case_of(b, FrameRole.GOAL_CONTAINER) is MorphCase.ILL
    assert "uusi 3 momentti" in roles[FrameRole.PAYLOAD]


def test_group_kumota_subsection_target() -> None:
    (b,) = _bindings("Kumotaan lain 3 §:n 2 momentti.")
    assert b.verb_code == "K"
    assert b.frame_id == "KUMOTA"
    assert _roles(b)[FrameRole.TARGET] == "3 §:n 2 momentti"


def test_group_korvata_target_and_adessive_replacement() -> None:
    # The adessive replacement FOLLOWS the structural node; the per-group window
    # is verb-anchored so it keeps the trailing adjunct.
    (b,) = _bindings("Korvataan 4 § uudella säännöksellä.")
    assert b.verb_code == "M"
    assert b.frame_id == "KORVATA"
    roles = _roles(b)
    assert roles[FrameRole.TARGET] == "4 §"
    assert "säännöksellä" in roles[FrameRole.REPLACEMENT]
    assert _case_of(b, FrameRole.REPLACEMENT) is MorphCase.ADE


def test_group_siirtaa_source_and_translative_destination() -> None:
    (b,) = _bindings("Siirretään 4 §:n 2 momentti 5 §:ksi.")
    assert b.verb_code == "S"
    assert b.frame_id == "SIIRTAA"
    roles = _roles(b)
    assert roles[FrameRole.SOURCE] == "4 §:n 2 momentti"
    assert roles[FrameRole.DESTINATION] == "5 §:ksi"
    assert _case_of(b, FrameRole.DESTINATION) is MorphCase.TRA


# ---------------------------------------------------------------------------
# THE PROMOTION WIN — multi-verb compound clauses bind PER GROUP
# (the raw-text spike refused these whole-clause; per-group recovers each verb)
# ---------------------------------------------------------------------------


def test_multi_verb_compound_binds_each_group() -> None:
    text = "kumotaan 3 §, korvataan 4 § uudella säännöksellä ja lisätään uusi 5 §"
    bindings = _bindings(text)
    by_frame = {b.frame_id: _roles(b) for b in bindings}
    assert set(by_frame) == {"KUMOTA", "KORVATA", "LISATA"}
    assert by_frame["KUMOTA"][FrameRole.TARGET] == "3 §"
    assert by_frame["KORVATA"][FrameRole.TARGET] == "4 §"
    assert "säännöksellä" in by_frame["KORVATA"][FrameRole.REPLACEMENT]
    assert "uusi 5 §" in by_frame["LISATA"][FrameRole.PAYLOAD]


def test_multi_verb_siirtaa_then_kumota() -> None:
    text = "Siirretään 4 §:n 2 momentti 5 §:ksi ja kumotaan 7 §."
    by_frame = {b.frame_id: _roles(b) for b in _bindings(text)}
    assert by_frame["SIIRTAA"][FrameRole.SOURCE] == "4 §:n 2 momentti"
    assert by_frame["SIIRTAA"][FrameRole.DESTINATION] == "5 §:ksi"
    assert by_frame["KUMOTA"][FrameRole.TARGET] == "7 §"


# ---------------------------------------------------------------------------
# SCOPE — META / out-of-scope verbs
# ---------------------------------------------------------------------------


def test_delegation_clause_has_no_structural_group() -> None:
    # `asetuksella säädetään` is a delegation meta clause: the parser emits NO
    # structural verb group, so the per-group layer yields nothing (kept distinct
    # from the structural frames per the design ruling).
    assert _outcomes("Valtioneuvoston asetuksella säädetään tarkemmin asiasta.") == ()


def test_out_of_scope_muutos_group_is_residue_not_guess() -> None:
    # A general `muutetaan` MUUTTAA group (not the korvata shape) carries no
    # frame-licensed predicate surface → honest NoLicensedFrame residue, never a
    # guessed binding.  The sibling KUMOTA group still binds.
    outcomes = _outcomes("kumotaan 3 luku, muutetaan 5 §")
    kinds = {(o.verb_code, type(o).__name__) for o in outcomes}
    assert ("K", "GroupRoleBinding") in kinds
    muutos = [o for o in outcomes if o.verb_code == "M"]
    assert muutos and isinstance(muutos[0], GroupRoleResidue)
    assert muutos[0].reason is ResidueReason.NO_LICENSED_FRAME


# ---------------------------------------------------------------------------
# NEGATIVE — fail-loud at verb-group granularity (same guarantees as raw engine)
# ---------------------------------------------------------------------------


def test_group_missing_required_replacement_is_residue() -> None:
    (r,) = _residues("korvataan 4 §")
    assert r.frame_id == "KORVATA"
    assert r.reason is ResidueReason.MISSING_REQUIRED_ROLE


def test_group_missing_required_destination_is_residue() -> None:
    (r,) = _residues("siirretään 3 §")
    assert r.frame_id == "SIIRTAA"
    assert r.reason is ResidueReason.MISSING_REQUIRED_ROLE


def test_group_elided_head_coordination_is_residue() -> None:
    (r,) = _residues("kumotaan 3, 5 ja 7 §")
    assert r.reason is ResidueReason.COORDINATION_SCOPE_UNRESOLVED


def test_group_multi_payload_container_pairing_stays_residue() -> None:
    # The multi-payload↔container pairing is a real linguistic limit: it MUST
    # remain typed residue at the group level, never a guessed pairing.
    text = (
        "lisätään asetukseen uusi 35 a § lukuun 5, 104 a § lukuun 6 "
        "ja 133 b § lukuun 7"
    )
    residues = _residues(text)
    assert any(r.reason is ResidueReason.CASE_FRAME_AMBIGUOUS for r in residues)
    assert not _bindings(text)  # no group bound a guessed pairing


def test_group_anaphora_clause_yields_no_binding() -> None:
    # An anaphoric operative clause (`siihen lisätään ...`) is declined by the
    # construction parser UPSTREAM — it produces no structural verb group at all,
    # so the per-group layer never fabricates a binding for it.  The anaphora is
    # refused before the role layer sees it (cleaner than the raw-text engine,
    # which has to flag UNSUPPORTED_ANAPHORA itself).
    assert not _bindings("siihen lisätään uusi 3 momentti")


def test_no_role_guessed_under_any_group_residue() -> None:
    # Soundness invariant at verb-group granularity: a refused in-scope group is
    # a GroupRoleResidue (no bindings), never a partial guess.  (Anaphoric
    # clauses are declined upstream and produce no group at all — covered above.)
    for text in (
        "korvataan 4 §",
        "siirretään 3 §",
        "kumotaan 3, 5 ja 7 §",
    ):
        for o in _outcomes(text):
            assert isinstance(o, GroupRoleBinding | GroupRoleResidue)
        assert not _bindings(text), text
        assert _residues(text), text


def test_additive_does_not_touch_parsed_ops() -> None:
    # ADDITIVE proof: running the role layer does not mutate the parse result's
    # parsed_ops (the replay-bearing projection) — it only READS verb groups.
    text = "kumotaan 3 §, korvataan 4 § uudella säännöksellä ja lisätään uusi 5 §"
    result = parse_clause(text)
    ops_before = list(result.parsed_ops)
    _ = bind_roles_for_clause(result.surface_clause)
    assert list(result.parsed_ops) == ops_before
