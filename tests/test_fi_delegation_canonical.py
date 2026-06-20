"""Tests for the SHADOW canonical token-native delegation forward-grant parser.

The canonical parser (:mod:`lawvm.finland.legal_surface.delegation_canonical`)
is the single construction the B/C rivals will later call. These tests pin its
ADJUDICATED behavior: it accepts a grant for every shape that actually grants the
power to issue a lower instrument (the strict superset of the B+C adjudicated-
correct union) and residualizes — never silently drops, never guesses — every
grant-SHAPED-but-not-a-grant instrument mention.

SHADOW only: this parser is not wired into any lens / producer / replay path, so
these tests assert the parser directly.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.delegation_canonical import (
    KIND_AGENCY,
    KIND_ASETUS,
    KIND_MIN_ASETUS,
    KIND_PRES_ASETUS,
    KIND_VN_ASETUS,
    assert_total_ownership,
    parse_delegation_grants,
    projection_grant_keys,
)


def _grants(text: str):
    scan = parse_delegation_grants(text)
    assert_total_ownership(scan, text)
    return scan


# ---------------------------------------------------------------------------
# POSITIVE cases — one per instrument kind / issuer class.
# ---------------------------------------------------------------------------


def test_vn_asetus_grant() -> None:
    scan = _grants("Valtioneuvoston asetuksella säädetään tarkemmin menettelystä.")
    assert len(scan.grants) == 1
    g = scan.grants[0]
    assert g.kind == KIND_VN_ASETUS
    assert g.instrument == "asetus"
    assert g.binding_strength == "must"
    assert not g.holder_underspecified


def test_min_asetus_grant_registry_miss_still_binds() -> None:
    # ``Opetusministeriön`` is NOT a verbatim registry phrase — the OLD B
    # residualized this as delegation_without_actor. The canonical issuer-head
    # fallback binds + classifies it.
    scan = _grants("Opetusministeriön asetuksella säädetään tarkemmin.")
    assert len(scan.grants) == 1
    assert scan.grants[0].kind == KIND_MIN_ASETUS
    assert not scan.grants[0].holder_underspecified


def test_pres_asetus_grant() -> None:
    scan = _grants(
        "Tämän lain voimaantulosta säädetään tasavallan presidentin asetuksella."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].kind == KIND_PRES_ASETUS


def test_bare_asetus_holder_underspecified() -> None:
    # The C-side strength: a bare ``asetuksella säädetään`` IS a grant; the issuer
    # is underspecified, NOT absent. The old B dropped these (285 on the sample).
    scan = _grants("Asuntojen markkinoinnissa tiedoista säädetään asetuksella.")
    assert len(scan.grants) == 1
    g = scan.grants[0]
    assert g.kind == KIND_ASETUS
    assert g.instrument == "asetus"
    assert g.holder_underspecified
    assert g.holder_start is None


def test_sentence_initial_bare_asetus_grant() -> None:
    # Sentence-initial capitalized ``Asetuksella`` — case-insensitive instrument
    # match (the old token-verbatim test missed the capital A).
    scan = _grants("Asetuksella annetaan tarkemmat säännökset:")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"


def test_agency_maaraysia_grant() -> None:
    scan = _grants("Virasto voi antaa tarkempia määräyksiä soveltamisesta.")
    assert len(scan.grants) == 1
    g = scan.grants[0]
    assert g.kind == KIND_AGENCY
    assert g.instrument == "määräys"
    assert g.binding_strength == "may"


def test_agency_ohje_grant() -> None:
    scan = _grants("Valvontaviranomainen antaa tarvittaessa ohjeita.")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "ohje"
    assert scan.grants[0].kind == KIND_AGENCY


def test_ministerial_paatos_grant_c_gap() -> None:
    # ``päätös`` instrument — C's two-anchor model (asetuksella + määräyksiä/
    # ohjeita) MISSED these; B caught them as instrument nouns. Canonical keeps
    # B's breadth.
    scan = _grants("Alueista määrätään oikeusministeriön päätöksellä.")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "päätös"


def test_vahvistetaan_verb_grant() -> None:
    # ``vahvistetaan`` / ``määritellään`` are in the union verb set (old B lacked
    # them).
    scan = _grants(
        "Maa- ja metsätalousministeriön asetuksella vahvistetaan yksikköhinnat."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].kind == KIND_MIN_ASETUS


def test_maaritellaan_verb_grant() -> None:
    scan = _grants(
        "Mitä tässä laissa tarkoitetaan, määritellään valtioneuvoston asetuksella."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].kind == KIND_VN_ASETUS


# ---------------------------------------------------------------------------
# NEGATIVE / residual cases — grant-shaped but NOT a grant.
# ---------------------------------------------------------------------------


def test_self_reference_residualized() -> None:
    # ``Tällä asetuksella säädetään`` = the decree exercising its OWN power, not a
    # delegation. No grant; a typed self_reference residual.
    scan = _grants("Tällä asetuksella säädetään tarkemmin valtionavustuksista.")
    assert scan.grants == ()
    assert any(r.kind == "self_reference_instrument" for r in scan.residuals)


def test_section_path_cross_reference_residualized() -> None:
    # ``valtioneuvoston asetuksen 34 §:n 2 momentissa säädetään`` cites an
    # EXISTING decree's section — the dominant OLD-B FALSE POSITIVE. No grant.
    scan = _grants(
        "sovelletaan, mitä valtioneuvoston asetuksen 34 §:n 2 momentissa säädetään."
    )
    assert scan.grants == ()
    assert any(r.kind == "cross_reference_instrument" for r in scan.residuals)


def test_statute_id_cross_reference_residualized() -> None:
    # ``annetun asetuksen (575/1988) 1―22 §:ssä säädetään`` cites an existing
    # decree by id — never a forward grant.
    scan = _grants(
        "Sen lisäksi, mitä annetun asetuksen (575/1988) 1 §:ssä säädetään."
    )
    assert scan.grants == ()
    assert any(r.kind == "cross_reference_instrument" for r in scan.residuals)


def test_postposition_complement_residualized() -> None:
    # ``päätöksen mukaisesti säädetään`` = the enacting preamble; ``päätöksen`` is
    # the postposition complement, not a delegated instrument.
    scan = _grants(
        "Sosiaali- ja terveysministeriön päätöksen mukaisesti säädetään asioista."
    )
    assert scan.grants == ()
    assert any(r.kind == "postposition_complement" for r in scan.residuals)


def test_object_anchored_section_not_cross_reference() -> None:
    # ``antaa määräyksiä 14 §:ssä tarkoitetun …`` — the section is the SUBJECT of
    # the granted määräys (the order is ABOUT section 14), NOT an existing-
    # instrument cross-reference. Object/partitive forms are never cross-refs.
    scan = _grants(
        "Ympäristökeskus voi antaa määräyksiä 14 §:ssä tarkoitetun sataman omistajalle."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "määräys"


def test_grant_subject_demonstrative_does_not_block_grant() -> None:
    # ``antaa ohjeita tämän asetuksen soveltamisesta`` — ``tämän asetuksen`` is the
    # SUBJECT of a genuine ohje grant; it must NOT residualize the grant away, and
    # must NOT double-own the span (totality holds).
    scan = _grants("Virasto antaa ohjeita tämän asetuksen soveltamisesta.")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "ohje"
    # the in-grant demonstrative mention is owned by the grant frame, not residual
    assert scan.residuals == ()


def test_no_delegation_verb_residualized() -> None:
    # An instrument noun with no power verb is not a grant.
    scan = _grants("Tämä koskee asetuksen liitettä.")
    assert scan.grants == ()
    assert any(
        r.kind in ("instrument_without_power_verb", "cross_reference_instrument")
        for r in scan.residuals
    )


def test_empty_text() -> None:
    scan = parse_delegation_grants("")
    assert scan.grants == ()
    assert scan.residuals == ()


# ---------------------------------------------------------------------------
# Over-recognition guards — the two grant-SHAPED-but-not-a-grant shapes the bare
# instrument-noun + power-verb co-occurrence test minted as FALSE POSITIVES.
# ---------------------------------------------------------------------------


def test_anaphoric_reference_residualized() -> None:
    # ``siten kuin hallintolaissa säädetään`` — ``säädetään`` is a BACK-reference to
    # where the matter is ALREADY provided for (the Administrative Procedure Act),
    # not a forward grant. No decree anchor → guard 1 fires.
    scan = _grants("Päätös on annettava tiedoksi siten kuin hallintolaissa säädetään.")
    assert scan.grants == ()
    assert any(r.kind == "anaphoric_reference" for r in scan.residuals)


def test_mita_anaphor_residualized() -> None:
    # ``noudattaen soveltuvin osin, mitä 13 luvussa säädetään`` — a ``mitä …
    # säädetään`` back-reference. No grant.
    scan = _grants("Määräys annetaan noudattaen soveltuvin osin, mitä 13 luvussa säädetään.")
    assert scan.grants == ()
    assert any(r.kind == "anaphoric_reference" for r in scan.residuals)


def test_anaphor_with_decree_anchor_is_grant() -> None:
    # ``siten kuin asetuksella tarkemmin säädetään`` — the decree anchor
    # ``asetuksella`` means a decree power IS granted; guard 1 STANDS DOWN.
    scan = _grants(
        "Oikaisua haetaan päätökseen siten kuin asetuksella tarkemmin säädetään."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"


def test_anaphor_with_active_grant_verb_is_grant() -> None:
    # ``määräyksen antaa viranomainen noudattaen, mitä … säädetään`` — the active
    # grant verb ``antaa`` makes the ``noudattaen mitä … säädetään`` mere MANNER;
    # the grant survives.
    scan = _grants(
        "Määräyksen antaa mainitun lain mukainen viranomainen noudattaen, "
        "mitä ympäristönsuojelulaissa säädetään."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "määräys"


def test_subject_np_collision_residualized() -> None:
    # ``Päätös annetaan julkipanon jälkeen`` — ``Päätös`` is the clause SUBJECT of a
    # passive predicate, not the delegated object. No grant; subject-collision.
    scan = _grants("Päätös annetaan julkipanon jälkeen ja siitä tiedotetaan.")
    assert scan.grants == ()
    assert any(r.kind == "subject_np_collision" for r in scan.residuals)


def test_subject_np_collision_genitive_prefix_residualized() -> None:
    # ``Ministeriön päätös on annettava tiedoksi`` — the subject NP is a genitive
    # modifier + instrument head; still a subject-collision, not a grant. The
    # back-reference ``siten kuin … säädetään`` here makes guard 1 own it.
    scan = _grants("Ministeriön päätös on annettava tiedoksi viipymättä.")
    assert scan.grants == ()
    assert any(r.kind == "subject_np_collision" for r in scan.residuals)


def test_object_fronted_grant_survives_subject_guard() -> None:
    # ``Ohjeet … antaa viranomainen`` — the instrument is the FRONTED OBJECT of an
    # active ``antaa`` with an authority subject; a genuine grant, NOT a collision.
    scan = _grants(
        "Ohjeet hakemuksiin liitettävistä selvityksistä antaa valvontaviranomainen."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "ohje"


# ---------------------------------------------------------------------------
# Over-recognition guard 3 — procedural-duty object: an instrument that is the
# OBJECT of a one-off necessitive duty (``on annettava päätös``) to ISSUE it, NOT
# a delegated power to MAKE general subordinate rules.
# ---------------------------------------------------------------------------


def test_procedural_duty_paatos_object_residualized() -> None:
    # ``Palkkaturvahakemukseen on annettava kirjallinen päätös`` — "a written
    # decision MUST BE ISSUED on the application": a one-off procedural duty, not a
    # delegated decision-MAKING power (the canonical FP example, 2000/1108 §12).
    scan = _grants("Palkkaturvahakemukseen on annettava kirjallinen päätös.")
    assert scan.grants == ()
    assert any(r.kind == "procedural_duty_object" for r in scan.residuals)


def test_procedural_duty_maaraykset_object_residualized() -> None:
    # ``Luvassa on annettava tarpeelliset määräykset`` — "the permit must contain
    # the necessary conditions": the määräys is the permit's conditions (object of
    # the duty), not a delegated rule-MAKING power (2000/86).
    scan = _grants("Luvassa on annettava tarpeelliset määräykset.")
    assert scan.grants == ()
    assert any(r.kind == "procedural_duty_object" for r in scan.residuals)


def test_procedural_duty_with_intervening_adverbial_residualized() -> None:
    # Finnish allows an adverbial between ``on`` and the necessitive participle
    # (``on viran puolesta annettava`` / ``on viipymättä annettava``); the duty
    # frame still holds.
    scan = _grants("Veden käyttäjille on viipymättä annettava tarpeelliset ohjeet.")
    assert scan.grants == ()
    assert any(r.kind == "procedural_duty_object" for r in scan.residuals)


def test_necessitive_decree_by_means_is_grant() -> None:
    # ``Tarkemmat säännökset on annettava asetuksella`` — the detailed provisions
    # MUST BE GIVEN BY decree: the decree is the MEANS (instrument == asetus), a
    # genuine grant. Guard 3 EXCLUDES asetus, so it STANDS DOWN.
    scan = _grants("Tarkemmat säännökset on annettava valtioneuvoston asetuksella.")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"
    assert scan.grants[0].kind == KIND_VN_ASETUS


def test_necessitive_with_decree_anchor_is_grant() -> None:
    # A päätös duty clause that ALSO carries a forward decree anchor
    # (``asetuksella``) grants a decree power — guard 3 stands down on the anchor.
    scan = _grants(
        "Päätös on annettava ja menettelystä säädetään tarkemmin asetuksella."
    )
    assert any(g.instrument == "asetus" for g in scan.grants)


def test_active_antaa_grant_not_procedural_duty() -> None:
    # ``Virasto antaa määräyksiä`` — an ACTIVE present grant verb (not the
    # necessitive participle ``annettava``); a genuine rule-making grant that guard
    # 3 must NOT touch.
    scan = _grants("Virasto voi antaa tarkempia määräyksiä soveltamisesta.")
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "määräys"


# ---------------------------------------------------------------------------
# Over-recognition guard 4 — decision-issuance object: a ``päätös`` OBJECT issued
# in a single case by passive-present ``annetaan`` / modal ``voidaan antaa`` is a
# one-off decision, NOT a delegated rule-MAKING power (the passive/modal
# counterpart of guard 3's necessitive ``annettava`` duty).
# ---------------------------------------------------------------------------


def test_decision_issuance_passive_present_residualized() -> None:
    # ``Muuttamisesta annetaan pyynnöstä päätös`` — "a decision IS ISSUED on
    # request": a one-off decision issuance, not a delegated decision-MAKING power
    # (2000/1224, 2000/1226).
    scan = _grants("Kansaneläkkeen muuttamisesta annetaan pyynnöstä päätös.")
    assert scan.grants == ()
    assert any(r.kind == "decision_issuance_object" for r in scan.residuals)


def test_decision_issuance_kielteinen_paatos_residualized() -> None:
    # ``hakemukseen annetaan kielteinen päätös`` — "a negative decision is issued on
    # the application": a one-off, not a grant (2000/1065).
    scan = _grants(
        "Maksu peritään myös silloin, kun siinä tarkoitettuun hakemukseen "
        "annetaan kielteinen päätös."
    )
    assert scan.grants == ()
    assert any(r.kind == "decision_issuance_object" for r in scan.residuals)


def test_decision_issuance_modal_voidaan_antaa_residualized() -> None:
    # ``Perittävää määrää koskeva päätös voidaan antaa sen jälkeen`` — the decision
    # MAY BE ISSUED thereafter: a one-off, not a rule-making grant (2000/1276).
    scan = _grants(
        "Perittävää määrää koskeva päätös voidaan antaa sen jälkeen, kun "
        "henkilöllä on oikeus vanhuuseläkkeeseen."
    )
    assert scan.grants == ()
    assert any(r.kind == "decision_issuance_object" for r in scan.residuals)


def test_decision_instrumental_paatoksella_is_grant() -> None:
    # ``Tarkemmat määräykset … annetaan … päätöksellä`` — the INSTRUMENTAL
    # ``päätöksellä`` is the decision-as-MEANS rule-making grant (the historical
    # ministerial päätös decree); guard 4 EXCLUDES päätöksellä, so it STANDS DOWN.
    scan = _grants(
        "Tarkemmat muistiinpanoja koskevat säännökset annetaan verohallituksen "
        "päätöksellä."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "päätös"


def test_decision_object_with_decree_anchor_is_grant() -> None:
    # ``Valtioneuvoston asetuksella voidaan antaa tarkempia säännöksiä päätöksen
    # sisällöstä`` — the decree anchor ``asetuksella`` means a decree power IS
    # granted (päätöksen is a mere topic mention); guard 4 stands down (2011/646).
    scan = _grants(
        "Valtioneuvoston asetuksella voidaan antaa tarkempia säännöksiä "
        "päätöksen sisällöstä."
    )
    assert any(g.instrument == "asetus" for g in scan.grants)


# ---------------------------------------------------------------------------
# säädetä connegative — the negative-RESERVATION grant ``jollei [issuer]
# asetuksella toisin säädetä`` IS a (negative) decree delegation production A
# (``_PAT_BARE_ASETUS``) treats as a grant; the unanchored ``jollei muualla laissa
# toisin säädetä`` is a back-/cross-reference, NOT a grant (guard 5).
# ---------------------------------------------------------------------------


def test_saadeta_reservation_with_anchor_is_grant() -> None:
    # ``jollei asetuksella toisin säädetä`` — a decree-anchored negative
    # reservation: production A treats it as a grant; the canonical now matches
    # (2000/29, 2000/340).
    scan = _grants(
        "Tässä laissa ministeriöllä tarkoitetaan maa- ja "
        "metsätalousministeriötä, ellei asetuksella toisin säädetä."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"
    assert scan.grants[0].cue.lower() == "säädetä"


def test_saadeta_reservation_vn_anchor_is_grant() -> None:
    # ``jollei valtioneuvoston asetuksella toisin säädetä`` — VN-issuer reservation.
    scan = _grants(
        "Aluevalvojan valvonta-alueena on kihlakunta, jollei valtioneuvoston "
        "asetuksella toisin säädetä."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].kind == KIND_VN_ASETUS


def test_saadeta_reservation_without_anchor_residualized() -> None:
    # ``jollei tässä laissa toisin säädetä`` — a back-reference reservation with no
    # decree anchor; NOT a grant. The clause carries a ``määräyksen`` topic mention
    # (so the parser considers it), and the säädetä connegative is residualized as a
    # negative reservation rather than minted as a grant (2001/1489).
    scan = _grants(
        "Sääntöihin sisältyvän määräyksen sijasta noudatetaan uuden lain "
        "säännöksiä, jollei tässä laissa toisin säädetä."
    )
    assert all(g.cue.lower() != "säädetä" for g in scan.grants)
    assert any(r.kind == "negative_reservation" for r in scan.residuals)


def test_saadeta_self_reference_residualized() -> None:
    # ``jollei tässä asetuksessa toisin säädetä`` — a self-reference to the enacting
    # decree, not a new decree grant. ``Tämä asetus`` heads the clause subject so
    # the säädetä connegative reservation carries no decree-grant anchor.
    scan = _grants(
        "Tämä asetus koskee myös laitoksen suoritteita, jollei niistä erikseen "
        "muuta säädetä."
    )
    assert all(g.cue.lower() != "säädetä" for g in scan.grants)


# ---------------------------------------------------------------------------
# Projection key shape (census-comparable identity).
# ---------------------------------------------------------------------------


def test_projection_keys_shape() -> None:
    scan = parse_delegation_grants(
        "Valtioneuvoston asetuksella säädetään tarkemmin menettelystä."
    )
    keys = projection_grant_keys(scan)
    assert keys == {"grant:VN_ASETUS:asetus"}


def test_basis_reuse_references_subgrammar() -> None:
    # The ``nojalla`` provision-basis tail is parsed via the references sub-grammar
    # (reuse, not a re-implemented section regex).
    scan = _grants(
        "Valtioneuvoston asetuksella säädetään lain (629/1998) 36 §:n "
        "nojalla tarkemmin menettelystä."
    )
    assert len(scan.grants) >= 1
    # at least one grant carries a recognized provision basis target
    assert any("36" in g.basis_targets for g in scan.grants)
