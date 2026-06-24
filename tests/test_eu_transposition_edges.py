"""Pins for the FI-layer EU-directive **transposition edge + timeliness** layer.

The layer (``lawvm.finland.references.eu_transposition_edges``) projects an
already-extracted :class:`TranspositionClaim` into a typed
:class:`TranspositionEdge` carrying ``fi_enactment_date`` and a four-way typed
:class:`Timeliness` verdict. These tests prove:

1. REAL WITNESS — Ympäristönsuojelulaki **527/2014** transposes the Industrial
   Emissions Directive (IED, ``32010L0075``). The witness fragment is the
   ACTUAL transposition clause from the consolidated act in the corpus
   (``finlex://sd-cons/2014/527/fin@20150423/main.xml``):
   "… teollisuuspäästödirektiivin III luvun ja liitteen V mukaisten
   velvoitteiden täytäntöönpanemiseksi." The extractor binds CELEX
   ``32010L0075``; the edge computes ``LATE`` because the act's real enactment
   (issue) date 2014-06-27 is AFTER the IED transposition deadline 2013-01-07
   (the IED Art. 80(1) deadline; Finland was historically late). The literal
   text + dates are pinned as fixtures so the test is independent of the
   gitignored ``.farchive`` corpus at gate time.
2. on_time — an enactment date on/before the deadline → ``ON_TIME``.
3. unknown_deadline — a RESOLVED directive whose CELEX has no seeded deadline →
   ``UNKNOWN_DEADLINE`` (honest absence, never a fabricated date).
4. unknown_enactment — no FI enactment date supplied → ``UNKNOWN_ENACTMENT``
   (the FI date is never assumed), and a missing FI date DOMINATES a missing
   deadline.
5. unbound directive — a STATUTE_ONLY / AMBIGUOUS claim keeps its surface,
   ``celex=None`` and a deadline of ``None`` → ``UNKNOWN_DEADLINE``; the
   binding_status is carried through (fail-loud, never dropped/guessed).
6. The edge_kind is always ``transposes`` (a DECLARED relation, never a
   conformance conclusion).
"""

from __future__ import annotations

from lawvm.finland.references.eu_transposition import (
    TranspositionStatus,
    recognize_transposition_claims,
)
from lawvm.finland.references.eu_transposition_edges import (
    TRANSPOSES_EDGE_KIND,
    Timeliness,
    build_transposition_edges,
    transposition_edge_for_claim,
)

# The ACTUAL IED transposition clause from Ympäristönsuojelulaki 527/2014
# (consolidated, corpus locator finlex://sd-cons/2014/527/fin@20150423/main.xml).
_YSL_527_2014_TRANSPOSITION_CLAUSE = (
    "Valtion valvontaviranomainen voi antaa toiminnanharjoittajalle "
    "polttolaitoksen toimintaa koskevia määräyksiä, jos se on tarpeen "
    "teollisuuspäästödirektiivin III luvun ja liitteen V mukaisten "
    "velvoitteiden täytäntöönpanemiseksi."
)
# Verified real dates for the witness act.
_YSL_527_2014_ENACTMENT_DATE = "2014-06-27"  # säädöskokoelma issue date
_IED_CELEX = "32010L0075"
_IED_DEADLINE = "2013-01-07"  # IED Art. 80(1)


def _ysl_claim():
    claims = recognize_transposition_claims(_YSL_527_2014_TRANSPOSITION_CLAUSE, citing_engine_id="2014/527")
    assert len(claims) == 1, claims
    claim = claims[0]
    # Sanity: the extractor binds the IED CELEX (this is the prerequisite the
    # edge layer projects — not re-tested deeply here, that is the extractor's
    # own test suite).
    assert claim.status is TranspositionStatus.RESOLVED
    assert claim.directive_celex == _IED_CELEX
    return claim


def test_real_witness_ysl_527_2014_transposes_ied_late() -> None:
    claim = _ysl_claim()
    edges = build_transposition_edges([claim], fi_enactment_date=_YSL_527_2014_ENACTMENT_DATE)
    assert len(edges) == 1
    edge = edges[0]
    assert edge.fi_citing_engine_id == "2014/527"
    assert edge.eu_directive_celex == _IED_CELEX
    assert edge.directive_surface == "teollisuuspäästödirektiivin"
    assert edge.edge_kind == TRANSPOSES_EDGE_KIND == "transposes"
    assert edge.transposition_deadline == _IED_DEADLINE
    assert edge.fi_enactment_date == _YSL_527_2014_ENACTMENT_DATE
    # 2014-06-27 > 2013-01-07 → the act was LATE transposing the IED.
    assert edge.timeliness is Timeliness.LATE
    assert edge.binding_status is TranspositionStatus.RESOLVED


def test_on_time_when_enacted_on_or_before_deadline() -> None:
    claim = _ysl_claim()
    # A hypothetical enactment date exactly on the deadline → ON_TIME.
    edge = transposition_edge_for_claim(claim, fi_enactment_date=_IED_DEADLINE)
    assert edge.timeliness is Timeliness.ON_TIME
    # And strictly before the deadline.
    edge_before = transposition_edge_for_claim(claim, fi_enactment_date="2012-12-31")
    assert edge_before.timeliness is Timeliness.ON_TIME


def test_unknown_deadline_when_celex_has_no_seed() -> None:
    # A directive that RESOLVES to a CELEX but whose CELEX is absent from the
    # curated deadline seed → UNKNOWN_DEADLINE (honest absence, never fabricated).
    # GDPR (32016R0679) is a regulation not in the seed; project the real claim
    # rebound to that CELEX to exercise the no-deadline-key branch.
    from dataclasses import replace

    claim = _ysl_claim()
    unseeded_claim = replace(claim, directive_celex="32016R0679")  # GDPR, unseeded
    edge = transposition_edge_for_claim(unseeded_claim, fi_enactment_date=_YSL_527_2014_ENACTMENT_DATE)
    assert edge.eu_directive_celex == "32016R0679"
    assert edge.transposition_deadline is None
    assert edge.timeliness is Timeliness.UNKNOWN_DEADLINE


def test_unknown_enactment_when_no_fi_date_and_it_dominates() -> None:
    claim = _ysl_claim()
    # No FI enactment date supplied → UNKNOWN_ENACTMENT even though the deadline
    # IS known (a missing FI date dominates a known deadline).
    edge = transposition_edge_for_claim(claim, fi_enactment_date=None)
    assert edge.transposition_deadline == _IED_DEADLINE
    assert edge.fi_enactment_date is None
    assert edge.timeliness is Timeliness.UNKNOWN_ENACTMENT

    # And missing FI date dominates a missing deadline too.
    from dataclasses import replace

    unbound = replace(
        claim,
        directive_celex=None,
        status=TranspositionStatus.STATUTE_ONLY,
    )
    edge2 = transposition_edge_for_claim(unbound, fi_enactment_date=None)
    assert edge2.timeliness is Timeliness.UNKNOWN_ENACTMENT


def test_unbound_directive_keeps_surface_and_is_unknown_deadline() -> None:
    # A NAMED-but-unknown directive: STATUTE_ONLY, celex=None, never guessed.
    text = "Säännökset annetaan päästökattodirektiivin täytäntöönpanemiseksi tarvittavilta osin."
    claims = recognize_transposition_claims(text, citing_engine_id="2099/1")
    assert len(claims) == 1
    edge = transposition_edge_for_claim(claims[0], fi_enactment_date="2099-01-01")
    assert edge.eu_directive_celex is None
    assert edge.directive_surface == "päästökattodirektiivin"
    assert edge.binding_status is TranspositionStatus.STATUTE_ONLY
    assert edge.transposition_deadline is None
    # Deadline unknown (no CELEX → no deadline key) even though FI date is known.
    assert edge.timeliness is Timeliness.UNKNOWN_DEADLINE
    assert edge.edge_kind == "transposes"


def test_build_edges_preserves_input_order_and_count() -> None:
    text = (
        "Tällä lailla pannaan täytäntöön teollisuuspäästödirektiivin "
        "velvoitteet. Lisäksi annetaan säännökset "
        "päästökattodirektiivin täytäntöönpanemiseksi."
    )
    claims = recognize_transposition_claims(text, citing_engine_id="2099/9")
    edges = build_transposition_edges(claims, fi_enactment_date="2020-01-01")
    assert len(edges) == len(claims)
    assert [e.directive_surface for e in edges] == [c.directive_surface for c in claims]
    assert all(e.edge_kind == "transposes" for e in edges)
