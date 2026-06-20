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
    # A genuine tasavallan presidentin asetus grant. (NB: a bare ``Tämän lain
    # voimaantulosta säädetään … presidentin asetuksella`` commencement clause is
    # NOT a grant — production A filters it and the canonical commencement guard
    # residualizes it; see test_commencement_voimaantulosta_pres_residualized.)
    scan = _grants(
        "Tarkempia säännöksiä tämän lain täytäntöönpanosta voidaan antaa "
        "tasavallan presidentin asetuksella."
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


# ---------------------------------------------------------------------------
# AGENCY-family precision guards (court / penal / single-case / appeal / bylaw /
# publishing / single-case-direction). The bare instrument-noun + power-verb
# co-occurrence test mints these as AGENCY grants; each is a grant-SHAPED-but-not-
# a-grant frame and
# must residualize, NEVER emit an AGENCY grant — while the genuine agency
# rule-making grants must SURVIVE untouched. Corpus-witnessed shapes.
# ---------------------------------------------------------------------------


def _agency_grants(scan) -> list:
    return [g for g in scan.grants if g.kind == KIND_AGENCY]


def test_court_power_paatos_residualized() -> None:
    # ``vakuutusoikeus voi … poistaa päätöksen ja määrätä asian uudelleen
    # käsiteltäväksi`` — an in-case adjudication, not a rule-making delegation
    # (2000/1276).
    scan = _grants(
        "Jos päätös on ilmeisesti lainvastainen, vakuutusoikeus voi rahaston "
        "hakemuksesta poistaa päätöksen ja määrätä asian uudelleen "
        "käsiteltäväksi."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "court_power" for r in scan.residuals)


def test_court_power_tuomioistuin_residualized() -> None:
    # ``tuomioistuin voi … määrätä, ettei päätöstä saa panna täytäntöön`` (2000/340).
    scan = _grants(
        "Kun kanne on pantu vireille, tuomioistuin voi kantajan vaatimuksesta "
        "ennen asian ratkaisemista määrätä, ettei päätöstä saa panna "
        "täytäntöön."
    )
    assert _agency_grants(scan) == []


def test_court_power_compound_tuomioistuin_issuer_residualized() -> None:
    # ``Hallintotuomioistuin voi … antaa … väliaikaisen määräyksen`` (2019/808 §123)
    # — a ``…tuomioistuin`` COMPOUND court issuer (not the bare ``tuomioistuin``)
    # exercising a case-specific power. The old guard recognized only the exact
    # ``tuomioistuin`` head and leaked the compound as an AGENCY edge.
    scan = _grants(
        "Hallintotuomioistuin voi myös muussa sen käsiteltävänä olevassa "
        "hallintolainkäyttöasiassa kuin valitusasiassa antaa asianosaisen oikeuden "
        "tai edun toteuttamista turvaavan väliaikaisen määräyksen."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "court_power" for r in scan.residuals)


def test_court_power_sentential_complement_maaraa_etta_residualized() -> None:
    # ``jollei tuomioistuin erityisestä syystä määrää, että …`` (2004/120 §11) — the
    # ``määrä…, että …`` sentential-complement court order. The matched FIRST power
    # verb is the trailing passive ``määrätään``, which the old bare-verb test did
    # not treat as adjudicative, so the päätös instrument leaked as AGENCY.
    scan = _grants(
        "Konkurssin alkamisen oikeusvaikutukset lakkaavat, jollei tuomioistuin "
        "erityisestä syystä määrää, että oikeusvaikutukset ovat voimassa, kunnes "
        "päätös on lainvoimainen tai asiassa toisin määrätään."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "court_power" for r in scan.residuals)


def test_court_power_clause_internal_adjudicative_verb_residualized() -> None:
    # ``ylempi tuomioistuin antaa asiassa uuden määräyksen`` (2007/705 §23) — the
    # adjudicative ``antaa`` is NOT the clause's first power verb (an unrelated
    # ``annettava`` precedes it), so the old first-power-verb test missed the
    # order frame and leaked the määräys instrument as AGENCY.
    scan = _grants(
        "Voimassaoloaikaa saadaan lyhentää tai pidentää, kuitenkin enintään kunnes "
        "annettava pääasiaratkaisu tulee lainvoimaiseksi tai ylempi tuomioistuin "
        "antaa asiassa uuden määräyksen."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "court_power" for r in scan.residuals)


def test_court_power_with_rulemaking_quantifier_is_grant() -> None:
    # STAND-DOWN: a court issuer ``antaa tarkempia määräyksiä [aiheesta]`` WOULD be
    # rule-making — the rule-making quantifier ``tarkempia`` keeps it a grant so the
    # guard never suppresses a genuine delegation merely because a court is named.
    scan = _grants(
        "Markkinaoikeus antaa tarkempia määräyksiä asioiden käsittelystä "
        "istunnossa."
    )
    assert any(g.kind == KIND_AGENCY for g in scan.grants)


def test_court_compound_issuer_rulemaking_quantifier_is_grant() -> None:
    # STAND-DOWN even for a ``…tuomioistuin`` COMPOUND issuer: a genuine
    # ``antaa tarkempia määräyksiä [aiheesta]`` rule-MAKING delegation must survive
    # — the widened compound-court issuer recognition must not suppress it.
    scan = _grants(
        "Hallintotuomioistuin antaa tarkempia määräyksiä asioiden käsittelystä "
        "istunnossa."
    )
    assert any(g.kind == KIND_AGENCY for g in scan.grants)


def test_penal_clause_reference_residualized() -> None:
    # ``Joka antaa rahalainan … viraston määräyksen vastaisesti … on tuomittava …
    # sakkoon tai vankeuteen`` — an offence definition referencing a norm, not a
    # grant. The clause carries a power verb (``antaa``) so it reaches the penal
    # guard rather than declining as instrument-without-power-verb (2000/340).
    scan = _grants(
        "Joka antaa rahalainan viraston määräyksen vastaisesti on tuomittava "
        "sakkoon tai vankeuteen enintään yhdeksi vuodeksi"
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "penal_clause_reference" for r in scan.residuals)


def test_single_case_order_antaneelle_residualized() -> None:
    # ``Valituskirjelmä voidaan antaa myös määräyksen antaneelle …`` — the genitive
    # ``määräyksen`` modifies ``antaneelle`` (the one who ISSUED the order); a
    # back-reference, not a grant (2000/199).
    scan = _grants(
        "Valituskirjelmä voidaan antaa myös määräyksen antaneelle kihlakunnan "
        "poliisilaitoksen päällikölle alioikeuteen toimittamista varten."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "single_case_order" for r in scan.residuals)


def test_permit_condition_residualized() -> None:
    # ``lupa voidaan antaa määräajaksi ja siihen on liitettävä … tarpeelliset
    # määräykset`` — the määräykset are the permit's CONDITIONS, not a rule-making
    # grant (2000/287, 2000/288).
    scan = _grants(
        "lupa voidaan antaa määräajaksi ja siihen on liitettävä yleisen ja "
        "yksityisen edun suojaamiseksi tarpeelliset määräykset."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "single_case_order" for r in scan.residuals)


def test_appeal_reference_residualized() -> None:
    # ``saavat valittaa päätöksestä korkeimpaan hallinto-oikeuteen`` — a right to
    # appeal an existing decision, not a rule-making instrument (2000/340).
    scan = _grants(
        "Yhdistys sekä muistutuksentekijä, joka katsoo viraston päätöksen "
        "loukkaavan oikeuttaan, saavat valittaa päätöksestä korkeimpaan "
        "hallinto-oikeuteen niin kuin hallintolainkäyttölaissa säädetään."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "appeal_reference" for r in scan.residuals)


def test_bylaw_provided_norm_tyojarjestys_residualized() -> None:
    # ``Tarkemmat määräykset … annetaan työjärjestyksessä`` — the norm is in an
    # internal bylaw, not a statutory decree / agency rule (2000/234).
    scan = _grants(
        "Tarkemmat määräykset hallinnon ja toimintojen järjestämisestä annetaan "
        "työjärjestyksessä, jonka pääjohtaja vahvistaa."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "bylaw_provided_norm" for r in scan.residuals)


def test_bylaw_provided_norm_taloussaanto_residualized() -> None:
    # ``Taloussäännössä voidaan lisäksi antaa muita … määräyksiä`` (2000/263).
    scan = _grants(
        "Taloussäännössä voidaan lisäksi antaa muita tiliviraston toimintaan "
        "liittyviä määräyksiä."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "bylaw_provided_norm" for r in scan.residuals)


def test_decision_paattaa_object_residualized() -> None:
    # ``voi myös erikseen päättää … merkitykseltään yleisen päätöksen
    # julkaisemisesta`` — a one-off administrative decision, not rule-making
    # (2000/188).
    scan = _grants(
        "Valtioneuvosto tai ministeriö voi myös erikseen päättää muun "
        "viranomaisen merkitykseltään yleisen päätöksen julkaisemisesta "
        "säädöskokoelmassa."
    )
    assert _agency_grants(scan) == []


def test_published_norm_reference_residualized() -> None:
    # ``Viranomaisen määräykset julkaistaan … säädöskokoelmassa`` — the clause
    # regulates WHERE existing norms are published, not the power to make them
    # (2000/188).
    scan = _grants(
        "Viranomaisen määräykset julkaistaan määräyskokoelman lisäksi tai "
        "sijasta säädöskokoelmassa, jos määräysten antamiseen valtuuttavassa "
        "laissa niin säädetään."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "published_norm_reference" for r in scan.residuals)


def test_single_case_direction_residualized() -> None:
    # ``antaa … yksittäisessä tapauksessa koskevia määräyksiä ja ohjeita`` — a
    # one-off direction in a single case, not a general rule (2000/204).
    scan = _grants(
        "Ulkoasiainministeriö voi toimialaansa kuuluvassa asiassa antaa "
        "edustuston toimintaa yksittäisessä tapauksessa koskevia määräyksiä ja "
        "ohjeita."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "single_case_direction" for r in scan.residuals)


def test_single_case_with_general_quantifier_is_grant() -> None:
    # STAND-DOWN: ``voi antaa YLEISIÄ määräyksiä … ja päättää … yksittäisessä
    # tapauksessa`` — the general ``yleisiä`` rule-making conjunct survives even
    # though a single-case clause is coordinated with it (2000/256).
    scan = _grants(
        "Ulkoasiainministeriö voi antaa yleisiä määräyksiä ulkomaanedustuksessa "
        "käytettävistä virka-arvoista ja päättää virka-arvosta yksittäisessä "
        "tapauksessa."
    )
    assert any(g.kind == KIND_AGENCY for g in scan.grants)


def test_published_norm_reference_quantified_grant_is_grant() -> None:
    # STAND-DOWN: ``voi antaa TARKEMPIA määräyksiä julkaistavista … tiedoista`` —
    # the ``julkais-`` word names the SUBJECT MATTER of a genuine general
    # rule-making grant, NOT a "[norms] julkaistaan säädöskokoelmassa" publishing
    # reference. The rule-making quantifier (``tarkempia``) must stand the
    # publishing guard down even though a publishing word is present
    # (2013/588 §48, 2017/587 §34 — energy-market acts the guard previously ate).
    scan = _grants(
        "Energiamarkkinavirasto voi antaa tarkempia määräyksiä julkaistavista "
        "ja ilmoitettavista tiedoista sekä julkaisu- ja ilmoitusmenettelystä."
    )
    assert any(g.kind == KIND_AGENCY for g in scan.grants)
    assert not any(
        r.kind == "published_norm_reference" for r in scan.residuals
    )


def test_published_norm_reference_unquantified_still_residualized() -> None:
    # NEGATIVE regression: a TRUE publishing reference carrying NO rule-making
    # quantifier (``määräykset julkaistaan … säädöskokoelmassa``) must STILL be
    # residualized — the stand-down keys on the quantifier, not on mere absence
    # of a publishing word (2000/188).
    scan = _grants(
        "Viranomaisen määräykset julkaistaan määräyskokoelman lisäksi tai "
        "sijasta säädöskokoelmassa, jos määräysten antamiseen valtuuttavassa "
        "laissa niin säädetään."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "published_norm_reference" for r in scan.residuals)


# --- GENUINE agency grants the guards must NOT suppress ---


def test_genuine_agency_ohje_grant_survives() -> None:
    scan = _grants(
        "Kansaneläkelaitos voi antaa tarkempia ohjeita tämän pykälän "
        "soveltamisesta."
    )
    assert any(g.kind == KIND_AGENCY and g.instrument == "ohje" for g in scan.grants)


def test_genuine_agency_maaraykset_grant_survives() -> None:
    scan = _grants(
        "Vakuutusvalvontavirasto antaa tarkemmat määräykset tämän momentin "
        "soveltamisesta."
    )
    assert any(
        g.kind == KIND_AGENCY and g.instrument == "määräys" for g in scan.grants
    )


def test_genuine_agency_voi_antaa_yleisia_ohjeita_survives() -> None:
    scan = _grants(
        "Ympäristöministeriö voi antaa yleisiä ohjeita tämän asetuksen "
        "täytäntöönpanosta ja valvonnasta."
    )
    assert any(g.kind == KIND_AGENCY for g in scan.grants)


def test_genuine_decision_as_means_paatoksella_survives() -> None:
    # The instrumental ``päätöksellä`` decision-as-MEANS rule-making grant (the
    # historical ministerial/agency päätös decree) must NOT be caught by the
    # one-off decision-issuance guard.
    scan = _grants(
        "Tarkemmat muistiinpanoja koskevat säännökset annetaan verohallituksen "
        "päätöksellä."
    )
    assert any(g.instrument == "päätös" for g in scan.grants)


# ---------------------------------------------------------------------------
# Commencement-clause guard (the flip gate). ``Tämän lain voimaantulosta
# säädetään … asetuksella`` is the standard commencement-by-decree section that
# production A FILTERS; the canonical must residualize it too (or the flip would
# inflate StatuteGraph forward grants by ~1 FP per statute). Mirrors A's EXACT
# ``voimaan(tulosta|panosta) säädetään`` filter — so ``voimaansaattamisesta
# säädetään`` (which A KEEPS) and ``täytäntöönpanosta voidaan antaa`` (a genuine
# decree grant) SURVIVE. Real statute witnesses below.
# ---------------------------------------------------------------------------


def test_commencement_voimaantulosta_pres_residualized() -> None:
    # 2002/474 §3: ``Tämän lain voimaantulosta säädetään tasavallan presidentin
    # asetuksella`` — A filters; canonical must residualize, not mint a grant.
    scan = _grants(
        "Tämän lain voimaantulosta säädetään tasavallan presidentin asetuksella."
    )
    assert scan.grants == ()
    assert any(r.kind == "commencement_clause" for r in scan.residuals)


def test_commencement_voimaantulosta_vn_residualized() -> None:
    # 2026/278 §3 / 2017/274 §2: ``… tämän lain voimaantulosta säädetään
    # valtioneuvoston asetuksella`` (the ``voimaantulosta`` directly governs
    # ``säädetään`` even with a coordinated preceding conjunct).
    scan = _grants(
        "Muutosten muiden määräysten voimaansaattamisesta ja tämän lain "
        "voimaantulosta säädetään valtioneuvoston asetuksella."
    )
    assert scan.grants == ()
    assert any(r.kind == "commencement_clause" for r in scan.residuals)


def test_commencement_voimaansaattamisesta_is_grant() -> None:
    # 2026/278 §2: ``… voimaansaattamisesta säädetään valtioneuvoston
    # asetuksella`` — bringing OTHER regulations into force. Production A KEEPS this
    # as a grant (its filter is ``voimaan(tulosta|panosta)``, not
    # ``voimaansaattamisesta``); the canonical must too (zero genuine-grant loss).
    scan = _grants(
        "Muutosten muiden kuin lainsäädännön alaan kuuluvien määräysten "
        "voimaansaattamisesta säädetään valtioneuvoston asetuksella."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"
    assert scan.grants[0].kind == KIND_VN_ASETUS


def test_commencement_tarkempia_saannoksia_antaa_is_grant() -> None:
    # 2002/474 §2: ``Tarkempia säännöksiä tämän lain täytäntöönpanosta voidaan
    # antaa tasavallan presidentin asetuksella`` — a genuine decree grant for
    # detailed implementation provisions. NOT the ``voimaantulosta säädetään``
    # commencement frame; the guard must STAND DOWN (A does not filter it).
    scan = _grants(
        "Tarkempia säännöksiä tämän lain täytäntöönpanosta voidaan antaa "
        "tasavallan presidentin asetuksella."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"
    assert scan.grants[0].kind == KIND_PRES_ASETUS


# ---------------------------------------------------------------------------
# Restriction power verbs (recall). ``rajoittaa`` / ``kieltää`` / ``rajoitetaan``
# / ``kielletään`` are in A's ``_PAT_DECREE_INVERTED``; the canonical verb set
# lacked them, residualizing the lone A-ONLY drop. Now a genuine grant.
# ---------------------------------------------------------------------------


def test_restriction_decree_grant_rajoittaa() -> None:
    # 2009/1194 §8.1: ``Valtioneuvoston asetuksella voidaan rajoittaa ilmailua tai
    # kieltää se`` — the power to issue a decree RESTRICTING / PROHIBITING an
    # activity is a genuine decree grant (the lone A-ONLY drop, now closed).
    scan = _grants(
        "Valtioneuvoston asetuksella voidaan rajoittaa ilmailua tai kieltää se "
        "maanpuolustuksen kannalta tärkeiden kohteiden läheisyydessä."
    )
    assert len(scan.grants) == 1
    assert scan.grants[0].instrument == "asetus"
    assert scan.grants[0].kind == KIND_VN_ASETUS
    assert scan.grants[0].cue.lower() == "rajoittaa"


# ---------------------------------------------------------------------------
# Idiom guards the AGENCY pass missed.
# ---------------------------------------------------------------------------


def test_cause_to_suspect_reference_residualized() -> None:
    # 2009/1194 §105, §149: ``… osoittanut sellaista yleistä piittaamattomuutta
    # säännöksistä tai määräyksistä, että se antaa aiheen epäillä …`` — the fixed
    # idiom ``antaa aiheen [epäillä]``; the elative ``määräyksistä`` is the norm
    # referenced, not a delegated rule-making power.
    scan = _grants(
        "On muulla toiminnallaan osoittanut sellaista yleistä piittaamattomuutta "
        "säännöksistä tai määräyksistä, että se antaa aiheen epäillä luvan "
        "haltijan kykyä tai halua noudattaa turvallisuuden kannalta olennaisia "
        "säännöksiä ja määräyksiä."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "cause_to_suspect_reference" for r in scan.residuals)


def test_cause_to_suspect_stands_down_on_genuine_grant() -> None:
    # STAND-DOWN: a rule-making quantifier (``tarkempia``) heading the object is a
    # genuine agency grant even if ``antaa aiheen`` text were nearby — the guard
    # must not suppress a real ``antaa tarkempia määräyksiä`` delegation.
    scan = _grants("Virasto antaa tarkempia määräyksiä asian käsittelystä.")
    assert any(g.kind == KIND_AGENCY and g.instrument == "määräys" for g in scan.grants)


def test_noncompliance_reference_residualized() -> None:
    # 2009/1194 §153: ``Jos … luvan haltija jättää noudattamatta … hyväksynnän
    # ehtoja tai muita määräyksiä …`` — the norm-violation idiom ``jättää
    # noudattamatta``; the määräykset are VIOLATED, not delegated. (The clause's
    # matched power verb is the unrelated heading ``annettava``.)
    scan = _grants(
        "Organisaatiolle annettava huomautus tai varoitus Jos organisaatiolle "
        "myönnetyn luvan haltija jättää noudattamatta tässä laissa tarkoitetun "
        "hyväksynnän ehtoja tai muita määräyksiä, luvan haltijalle voidaan antaa "
        "huomautus."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "noncompliance_reference" for r in scan.residuals)


def test_noncompliance_stands_down_on_genuine_grant() -> None:
    # STAND-DOWN: a genuine ``antaa tarkempia määräyksiä`` grant survives even
    # though a ``noudattamatta`` token sits elsewhere in a different sentence.
    scan = _grants("Virasto antaa tarkempia määräyksiä valvonnasta.")
    assert any(g.kind == KIND_AGENCY and g.instrument == "määräys" for g in scan.grants)


def test_bylaw_provided_norm_tutkintosaanto_residualized() -> None:
    # 1987/672 §30: ``Tarkemmat määräykset tämän asetuksen soveltamisesta annetaan
    # tutkintosäännössä …`` — the norm is in an internal ``tutkintosääntö`` charter
    # (the examination by-law), exactly the työjärjestys / ohjesääntö class, now in
    # the bylaw guard's noun set.
    scan = _grants(
        "Tarkemmat määräykset tämän asetuksen soveltamisesta annetaan "
        "tutkintosäännössä, jonka korkeakoulu hyväksyy."
    )
    assert _agency_grants(scan) == []
    assert any(r.kind == "bylaw_provided_norm" for r in scan.residuals)
