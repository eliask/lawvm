"""Pins for the FI EU-directive transposition-CLAIM extractor (design §25.8).

The extractor (``lawvm.finland.references.eu_transposition``) detects an explicit
transposition declaration in FI statute prose and binds the named directive to a
CELEX via the existing deterministic ``eu_nickname`` registry (READ-ONLY). These
tests prove:

1. A genuine transposition claim with a registry-known directive nickname binds
   to the right CELEX (``RESOLVED``).
2. A NAMED-but-unknown directive is emitted with an explicit ``STATUTE_ONLY``
   status and ``celex=None`` — never dropped, never guessed (§0.3 fail-loud).
3. An ambiguous nickname (>1 CELEX) is emitted ``AMBIGUOUS`` with ``celex=None``
   — the registry never picks.
4. A BARE anaphoric head (``direktiivin`` with no instrument name) co-located
   with a transposition word is NOT a transposition claim — declined.
5. The standalone NOUN ``täytäntöönpano`` (an implementing-ACT reference, not the
   act's own claim to transpose) does NOT produce a transposition claim.
6. The curated deadline seed returns the verified demo dates and ``None`` for an
   unseeded CELEX (the honest absence behind an ``open`` timeliness fact).
"""

from __future__ import annotations

from lawvm.finland.references.eu_transposition import (
    TRANSPOSITION_DEADLINE_SEED,
    TranspositionStatus,
    recognize_transposition_claims,
    transposition_deadline,
)


def test_resolved_claim_binds_known_directive_celex() -> None:
    text = (
        "Tällä lailla pannaan täytäntöön teollisuuspäästödirektiivin "
        "III luvun mukaiset velvoitteet."
    )
    claims = recognize_transposition_claims(text, citing_engine_id="2014/527")
    assert len(claims) == 1
    claim = claims[0]
    assert claim.transposition_status is TranspositionStatus.RESOLVED
    assert claim.directive_celex == "32010L0075"  # IED 2010/75/EU
    assert claim.directive_surface == "teollisuuspäästödirektiivin"
    assert claim.citing_engine_id == "2014/527"
    assert "täytäntöön" in claim.claim_surface


def test_named_unknown_directive_is_statute_only_not_dropped() -> None:
    # "päästökattodirektiivin" is a NAMED EU instrument (compound EU-head) NOT in
    # the registry seed → committed STATUTE_ONLY with celex=None, never dropped.
    text = (
        "Säännökset annetaan päästökattodirektiivin täytäntöönpanemiseksi "
        "tarvittavilta osin."
    )
    claims = recognize_transposition_claims(text, citing_engine_id="2099/1")
    assert len(claims) == 1
    claim = claims[0]
    assert claim.transposition_status is TranspositionStatus.STATUTE_ONLY
    assert claim.directive_celex is None  # tag, don't guess
    assert claim.directive_surface == "päästökattodirektiivin"


def test_ambiguous_directive_is_ambiguous_celex_none() -> None:
    # "jätedirektiivi" maps to >1 CELEX in the registry → AMBIGUOUS, never picked.
    text = (
        "Tällä lailla pannaan täytäntöön jätedirektiivin mukaiset "
        "jätehuollon velvoitteet."
    )
    claims = recognize_transposition_claims(text, citing_engine_id="2099/2")
    assert len(claims) == 1
    claim = claims[0]
    assert claim.transposition_status is TranspositionStatus.AMBIGUOUS
    assert claim.directive_celex is None  # registry refuses to pick


def test_bare_anaphoric_head_is_not_a_transposition_claim() -> None:
    # A bare ``direktiivin`` (no glued instrument name) co-located with a
    # transposition word carries no instrument identity → NOT a claim.
    text = "Tällä lailla pannaan täytäntöön mainitun direktiivin säännökset."
    claims = recognize_transposition_claims(text, citing_engine_id="2099/3")
    assert claims == []


def test_standalone_noun_taytantoonpano_is_not_a_claim() -> None:
    # The NOUN ``täytäntöönpanosta`` names an implementing ACT of an EU
    # instrument, NOT the FI act's own claim to transpose a directive → excluded.
    text = (
        "asetuksen (EY) N:o 1069/2009 täytäntöönpanosta annettu komission "
        "asetus (EU) N:o 142/2011 sisältää tarkemmat säännökset."
    )
    claims = recognize_transposition_claims(text, citing_engine_id="2099/4")
    assert claims == []


def test_deadline_seed_has_verified_demo_dates() -> None:
    # The hand-seeded demo deadlines (verified against the directives' own
    # transposition articles).
    assert transposition_deadline("32010L0075") == "2013-01-07"  # IED Art. 80
    assert transposition_deadline("32006L0123") == "2009-12-28"  # Services Art. 44
    assert transposition_deadline("32004L0035") == "2007-04-30"  # ELD Art. 19
    # An unseeded CELEX → None (the honest absence behind an OPEN timeliness fact).
    assert transposition_deadline("32016R0679") is None
    assert "32010L0075" in TRANSPOSITION_DEADLINE_SEED
