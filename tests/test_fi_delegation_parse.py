"""Tests for the delegation/authority construction parse + census.

Mirrors ``tests/test_fi_modal_parse.py`` discipline: IR + projection + total
token ownership + the issuer KIND classification + the two-anchor cue model +
``nojalla`` provision-basis recognition + census classification on hand-built
witnesses, INCLUDING witnesses for the known production-extractor weaknesses (a
wide modifier gap the ``{0,150}?`` window misses; a coordinated ``… ja … nojalla``
the conjunct distribution must split).

The parse is SURFACE-ONLY and ADDITIVE; these tests assert the construction-grammar
contract (closed-list anchors, kind classification, no silent drop, oracle-
comparable projection key), NOT any production behaviour change.
"""
from __future__ import annotations

from lawvm.finland.delegation import _DELEGATION_PATTERNS
from lawvm.finland.legal_surface.delegation_parse import (
    DELEGATION_LANE_CONSTRUCTION_OWNED,
    DELEGATION_LANE_DECLINED,
    INSTRUMENT_ASETUS,
    INSTRUMENT_MAARAYS,
    KIND_AGENCY,
    KIND_ASETUS,
    KIND_MIN_ASETUS,
    KIND_VN_ASETUS,
    assert_total_ownership,
    delegation_key,
    parse_delegation_sentence,
    projection_grant_keys,
)
from lawvm.finland.legal_surface.family_census import classify


# ---------------------------------------------------------------------------
# IR + total token ownership
# ---------------------------------------------------------------------------


def _check_total(text: str) -> None:
    dp = parse_delegation_sentence(text)
    assert_total_ownership(dp)


def test_total_ownership_holds_on_each_shape() -> None:
    for text in (
        "Valtioneuvoston asetuksella säädetään tarkemmin tämän lain täytäntöönpanosta.",
        "Tarkempia säännöksiä annetaan valtioneuvoston asetuksella.",
        "Liikennevirasto voi antaa tarkempia määräyksiä radan kunnossapidosta.",
        "Asetuksella säädetään tarkemmin.",
        "Voidaan asetuksella säätää poikkeuksia.",
        "Sosiaali- ja terveysministeriön asetuksella annetaan tarkempia säännöksiä.",
    ):
        _check_total(text)


def test_total_ownership_partitions_exactly() -> None:
    text = "Valtioneuvoston asetuksella säädetään tarkemmin täytäntöönpanosta."
    dp = parse_delegation_sentence(text)
    # Every char is owned by exactly one of: a core's cue/instrument/holder/basis
    # span, or a residual. The postcondition asserts no gap; here we also assert
    # the union covers the whole span.
    n = len(text)
    covered = [False] * n
    for c in dp.cores:
        spans = [
            (c.cue_start, c.cue_end),
            (c.instrument_start, c.instrument_end),
            (c.holder_start, c.holder_end),
            (c.basis_start, c.basis_end),
        ]
        for s, e in spans:
            if s is None or e is None:
                continue
            for i in range(s, e):
                covered[i] = True
    for r in dp.residuals:
        for i in range(r.char_start, r.char_end):
            covered[i] = True
    assert all(covered)


def test_declined_sentence_is_total_and_typed() -> None:
    # A non-delegation sentence (no instrument/verb co-occurrence) declines, with
    # the whole span as a single typed residual (no silent drop).
    dp = parse_delegation_sentence("Tämä laki tulee voimaan 1 päivänä tammikuuta 2020.")
    assert dp.kind == "declined"
    assert dp.parser_lane == DELEGATION_LANE_DECLINED
    assert dp.cores == ()
    assert len(dp.residuals) == 1
    assert dp.residuals[0].reason == "no_delegation_core"
    assert_total_ownership(dp)


# ---------------------------------------------------------------------------
# Issuer KIND classification
# ---------------------------------------------------------------------------


def test_kind_valtioneuvosto_holder_before_instrument() -> None:
    # Instrument-first shape: the issuer NP precedes the instrument, so classifying
    # off the bare cue would mis-key as generic ASETUS. Classification must see the
    # holder.
    dp = parse_delegation_sentence(
        "Valtioneuvoston asetuksella säädetään tarkemmin asiasta."
    )
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_VN_ASETUS
    assert dp.cores[0].instrument == INSTRUMENT_ASETUS


def test_kind_ministerio_compound_name() -> None:
    dp = parse_delegation_sentence(
        "Sosiaali- ja terveysministeriön asetuksella annetaan tarkempia säännöksiä."
    )
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_MIN_ASETUS


def test_kind_agency_maarays() -> None:
    dp = parse_delegation_sentence(
        "Liikennevirasto voi antaa tarkempia määräyksiä radan kunnossapidosta."
    )
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_AGENCY
    assert dp.cores[0].instrument == INSTRUMENT_MAARAYS


def test_kind_bare_asetus_holder_underspecified() -> None:
    dp = parse_delegation_sentence("Asetuksella säädetään tarkemmin.")
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_ASETUS
    assert dp.cores[0].holder_underspecified is True
    assert dp.cores[0].holder_start is None


# ---------------------------------------------------------------------------
# Two-anchor cue: the discontinuous (verb, instrument) constituent
# ---------------------------------------------------------------------------


def test_cue_is_two_disjoint_anchor_spans() -> None:
    text = "Valtioneuvoston asetuksella säädetään tarkemmin asiasta."
    dp = parse_delegation_sentence(text)
    c = dp.cores[0]
    assert text[c.cue_start : c.cue_end] == "säädetään"
    assert text[c.instrument_start : c.instrument_end] == "asetuksella"
    # The two anchors are disjoint (the cue is a discontinuous constituent).
    assert c.instrument_end <= c.cue_start or c.cue_end <= c.instrument_start


# ---------------------------------------------------------------------------
# WITNESS: wide modifier gap the production {0,150}? / {0,2}-word window misses
# ---------------------------------------------------------------------------


def test_witness_wide_modifier_gap_recovered() -> None:
    """A grant with a wide modifier run between ``asetuksella`` and the verb.

    The production extractor's bounded ``{0,N}`` gap windows do NOT span this run,
    so production emits NO edge. The construction's two-anchor co-occurrence
    recognizes it (the anchors are matched independently; only clause co-occurrence
    is required). This is a genuine recall SUPERSET, not a production bug.
    """
    text = (
        "Sosiaali- ja terveysministeriön asetuksella voidaan tämän lain "
        "täytäntöönpanon ja valvonnan järjestämiseksi sekä yhdenmukaisen "
        "soveltamiskäytännön turvaamiseksi antaa tarkempia säännöksiä."
    )
    # Production misses it (no pattern fires).
    prod_hits = [m.group(0) for pat in _DELEGATION_PATTERNS for m in pat.finditer(text)]
    assert prod_hits == [], f"production unexpectedly matched: {prod_hits}"
    # The construction recognizes the grant.
    dp = parse_delegation_sentence(text)
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_MIN_ASETUS
    assert_total_ownership(dp)


# ---------------------------------------------------------------------------
# WITNESS: coordinated ``… ja … nojalla`` conjunct distribution
# ---------------------------------------------------------------------------


def test_witness_coordinated_nojalla_distributes_all_conjuncts() -> None:
    """A single ``nojalla`` coordinating two authority bases.

    The construction distributes the single ``nojalla`` over BOTH conjuncts, so
    BOTH section bases (``36`` and ``8``) are recognized — not only the conjunct
    adjacent to ``nojalla``. (An earlier single-match approach dropped the first.)
    """
    text = (
        "Lukiolain (629/1998) 36 §:n ja valtion maksuperustelain (150/1992) 8 §:n "
        "nojalla valtioneuvoston asetuksella säädetään tarkemmin asiasta."
    )
    dp = parse_delegation_sentence(text)
    assert len(dp.cores) == 1
    c = dp.cores[0]
    assert c.kind == KIND_VN_ASETUS
    assert c.basis_targets == ("36", "8")
    assert_total_ownership(dp)


def test_nojalla_basis_single_conjunct() -> None:
    text = (
        "Terveydenhuoltolain (1326/2010) 8 §:n nojalla valtioneuvoston "
        "asetuksella säädetään tarkemmin asiasta."
    )
    dp = parse_delegation_sentence(text)
    assert len(dp.cores) == 1
    assert dp.cores[0].basis_targets == ("8",)
    assert dp.cores[0].basis_start is not None


def test_bare_mukaan_without_provision_is_not_a_basis() -> None:
    # "lain mukaan" with no provision id/section is NOT an authority basis.
    dp = parse_delegation_sentence("Lain mukaan asetuksella säädetään tarkemmin.")
    assert len(dp.cores) == 1
    assert dp.cores[0].basis_targets == ()
    assert dp.cores[0].basis_start is None


# ---------------------------------------------------------------------------
# Projection keys + census classification
# ---------------------------------------------------------------------------


def test_projection_grant_keys() -> None:
    dp = parse_delegation_sentence(
        "Valtioneuvoston asetuksella säädetään tarkemmin asiasta."
    )
    assert projection_grant_keys(dp) == {
        delegation_key(KIND_VN_ASETUS, INSTRUMENT_ASETUS)
    }


def test_census_classify_match_superset_miss() -> None:
    proj = {delegation_key(KIND_VN_ASETUS, INSTRUMENT_ASETUS)}
    # match: identical sets.
    assert classify(proj, set(proj), declined=False) == "match"
    # superset: projection finds a grant the (weak) oracle missed.
    assert classify(proj, set(), declined=False) == "superset"
    # miss: oracle has a key the projection lacks.
    assert (
        classify(set(), {delegation_key(KIND_AGENCY, INSTRUMENT_MAARAYS)}, declined=False)
        == "miss"
    )


def test_two_clauses_two_cores() -> None:
    # Two delegation clauses in one sentence (period boundary) → two cores.
    text = (
        "Valtioneuvoston asetuksella säädetään tarkemmin. "
        "Ministeriö voi antaa tarkempia määräyksiä."
    )
    dp = parse_delegation_sentence(text)
    assert dp.parser_lane == DELEGATION_LANE_CONSTRUCTION_OWNED
    assert len(dp.cores) == 2
    kinds = {c.kind for c in dp.cores}
    assert KIND_VN_ASETUS in kinds
    assert KIND_AGENCY in kinds
    assert_total_ownership(dp)
