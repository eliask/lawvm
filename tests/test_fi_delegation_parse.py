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
    extract_authority_bases,
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


def test_basis_adjacency_guard_rejects_longrange_anaphoric_nojalla() -> None:
    # The minor enrichment FP: a ``varhaiskasvatuslain (540/2018) 1 §:n`` ref under
    # ``tämän lain mukaista`` sitting far to the LEFT of a bare anaphoric ``sen
    # nojalla`` must NOT be grabbed as the authority basis (the prose between the id
    # and the terminal fails the adjacency guard).
    text = (
        "Jos tämän lain mukaista esiopetusta järjestetään varhaiskasvatuslain "
        "(540/2018) 1 §:n 2 momentin 1 tai 2 kohdassa tarkoitetussa päiväkodissa, "
        "esiopetukseen sovelletaan, jollei tässä laissa tai sen nojalla "
        "asetuksella toisin säädetä, mitä siellä säädetään."
    )
    dp = parse_delegation_sentence(text)
    assert all(c.basis_targets == () for c in dp.cores)


# ---------------------------------------------------------------------------
# Standalone authority-basis recognizer (the … nojalla REVERSE direction)
# ---------------------------------------------------------------------------


def test_extract_authority_bases_single_act_basis() -> None:
    bases = extract_authority_bases(
        "Opetusministerin esittelystä säädetään ammatillisista oppilaitoksista "
        "annetun lain (487/87) nojalla:"
    )
    assert [(b.num, b.year, b.name_word, b.section_labels) for b in bases] == [
        ("487", "87", "lain", ())
    ]


def test_extract_authority_bases_coordinated_with_sections() -> None:
    bases = extract_authority_bases(
        "säädetään lukiolain (629/1998) 36 §:n ja valtion maksuperustelain "
        "(150/1992) 8 §:n nojalla:"
    )
    triples = {(b.num, b.year, b.section_labels) for b in bases}
    assert ("629", "1998", ("36",)) in triples
    assert ("150", "1992", ("8",)) in triples


def test_extract_authority_bases_decree_kind() -> None:
    bases = extract_authority_bases("Säädetään esimerkkiasetuksen (1248/2005) 3 §:n nojalla:")
    assert [(b.num, b.year, b.name_word) for b in bases] == [("1248", "2005", "esimerkkiasetuksen")]


def test_extract_authority_bases_anaphoric_sen_nojalla_yields_nothing() -> None:
    # ``sen nojalla`` (no own id) is not an authority basis.
    assert extract_authority_bases("jollei tässä laissa tai sen nojalla toisin säädetä") == []


def test_extract_authority_bases_sellaisena_kuin_interjection_single() -> None:
    # The ``, sellaisena kuin se on laissa NNN/YYYY,`` amendment-version
    # interjection sits between the provision path and ``nojalla``. The basis is
    # the OUTER act (150/1992); the inner ``348/1994`` is the AMENDING act and must
    # NOT be bound as a basis.
    bases = extract_authority_bases(
        "Säädetään valtion maksuperustelain (150/1992) 8 §:n, sellaisena kuin se "
        "on laissa 348/1994, nojalla:"
    )
    assert [(b.num, b.year, b.section_labels) for b in bases] == [("150", "1992", ("8",))]


def test_extract_authority_bases_sellaisena_kuin_interjection_coordinated() -> None:
    # Each coordinated conjunct carries its OWN interjection; every interjection is
    # skipped and only the two OUTER bases (with their sections) are emitted — never
    # the amending acts 1/2000 / 348/1994.
    bases = extract_authority_bases(
        "säädetään lukiolain (629/1998) 36 §:n, sellaisena kuin se on laissa "
        "1/2000, ja maksuperustelain (150/1992) 8 §:n, sellaisena kuin se on "
        "laissa 348/1994, nojalla:"
    )
    triples = {(b.num, b.year, b.section_labels) for b in bases}
    assert triples == {("629", "1998", ("36",)), ("150", "1992", ("8",))}


def test_extract_authority_bases_sellaisena_interjection_keeps_following_coordinated_basis() -> None:
    # An interjection with NO closing comma before a ``sekä``-coordinated NEXT basis
    # must NOT swallow that basis. Here the amending act 365/92 is in the interjection
    # for 255/88 §13, but 364/92 §1 (after ``sekä``) is a SEPARATE basis — both the
    # outer 255/88 §13 AND the coordinated 364/92 §1 must survive (no recall loss),
    # and the amending 365/92 must NOT be bound.
    bases = extract_authority_bases(
        "annetun asetuksen (255/88) 13 §:n 2 momentin, sellaisena kuin se on "
        "24 päivänä huhtikuuta 1992 annetussa asetuksessa (365/92) sekä "
        "Kansainvälisen Itämeren kalastuskomission suositusten mukaisten "
        "saaliskiintiöiden voimaansaattamisesta annetun asetuksen (364/92) 1 §:n "
        "nojalla päättänyt:"
    )
    triples = {(b.num, b.year, b.section_labels) for b in bases}
    assert ("255", "88", ("13",)) in triples
    assert ("364", "92", ("1",)) in triples
    # the amending act inside the interjection is NOT a basis
    assert not any(b.num == "365" and b.year == "92" for b in bases)


def test_extract_authority_bases_sellaisena_kuin_muutettuna_parenthetical_amenders() -> None:
    # ``sellaisena kuin se on muutettuna … laeilla (639/66 sekä 599 ja 1347/90),``
    # carries MULTIPLE amending ids in a paren; none may be bound. The basis is the
    # outer eläkelaki (395/61) 12 §.
    bases = extract_authority_bases(
        "annetun työntekijäin eläkelain (395/61) 12 §:n, sellaisena kuin se on "
        "muutettuna 16 päivänä joulukuuta 1966 sekä annetuilla laeilla "
        "(639/66 sekä 599 ja 1347/90), nojalla päättänyt"
    )
    assert [(b.num, b.year, b.section_labels) for b in bases] == [("395", "61", ("12",))]


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


# ---------------------------------------------------------------------------
# Multi-core: coordinated instruments sharing one verb in ONE clause
# ---------------------------------------------------------------------------


def test_multicore_two_coordinated_asetus_anchors() -> None:
    # One clause, one verb, TWO coordinated ``asetuksella`` decree anchors → TWO
    # cores (one per instrument anchor), sharing the single power verb, with total
    # ownership preserved. (Issuer-class precision on tightly-coordinated dual-
    # asetus is bounded by the holder NP recognizer and not the comparison axis —
    # the census collapses asetus issuer classes onto one instrument-granular key.)
    text = (
        "Tarkemmat säännökset annetaan valtioneuvoston asetuksella "
        "ja ministeriön asetuksella."
    )
    dp = parse_delegation_sentence(text)
    assert len(dp.cores) == 2
    assert all(c.instrument == INSTRUMENT_ASETUS for c in dp.cores)
    # The two cores own two DISTINCT instrument anchors.
    anchors = {(c.instrument_start, c.instrument_end) for c in dp.cores}
    assert len(anchors) == 2
    assert_total_ownership(dp)


def test_multicore_coordinated_bare_asetus_not_misbound_to_later_issuer() -> None:
    # The canonical coordinated multi-instrument clause (repro 1995/1062): one verb
    # delegates to a BARE ``asetuksella`` plus a ministry ``päätöksellä`` and a
    # municipal ``järjestyksellä``. The bare ``asetuksella`` is a GENERIC asetus —
    # its issuer must NOT be the ``ympäristöministeriön`` genitive that binds the
    # later ``päätöksellä`` (adjacency rule). The non-modelled päätös/järjestys
    # instruments stay benign residual.
    text = (
        "Tarkempia säännöksiä ja määräyksiä rakentamisesta annetaan asetuksella, "
        "ympäristöministeriön päätöksellä ja kunnan rakennusjärjestyksellä."
    )
    dp = parse_delegation_sentence(text)
    assert len(dp.cores) == 1
    assert dp.cores[0].kind == KIND_ASETUS  # bare asetus, NOT MIN_ASETUS
    assert dp.cores[0].holder_underspecified is True
    assert_total_ownership(dp)


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


# ---------------------------------------------------------------------------
# Production flip: construction-primary authority-basis mention lane
# ---------------------------------------------------------------------------


def _preamble_xml(preamble_text: str) -> bytes:
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><preamble><p>" + preamble_text + "</p></preamble>"
        "<body><section><num>1 §</num></section></body></act>"
        "</akomaNtoso>"
    ).encode("utf-8")


def test_authority_mention_lane_lifts_construction_basis_canonical_orientation() -> None:
    from lawvm.core.reference_mention import CiteKind
    from lawvm.finland.references.ref_mention_extractor import (
        extract_delegation_construction_authority_mentions,
    )

    xml = _preamble_xml(
        "Maa- ja metsätalousministeriön päätöksen mukaisesti säädetään "
        "annetun lain (1048/2016) 37 §:n nojalla:"
    )
    res, covered = extract_delegation_construction_authority_mentions(xml, "2018/1158")
    assert covered == {"2016/1048"}  # canonical YEAR/NUMBER, not inverted
    assert len(res.mentions) == 1
    m = res.mentions[0]
    assert m.edge_subtype == "ISSUED_UNDER"
    assert m.phrase_lemma == "delegation_construction"
    assert m.cite_kind == CiteKind.CROSS_STATUTE  # a laki basis
    assert m.target_provision_ref is not None
    assert m.target_provision_ref.statute_id == "2016/1048"
    assert m.target_provision_ref.section_label == "37"


def test_authority_mention_lane_decree_basis_is_non_statutory() -> None:
    from lawvm.core.reference_mention import CiteKind
    from lawvm.finland.references.ref_mention_extractor import (
        extract_delegation_construction_authority_mentions,
    )

    xml = _preamble_xml("Säädetään esimerkkiasetuksen (1248/2005) 3 §:n nojalla:")
    res, _covered = extract_delegation_construction_authority_mentions(xml, "2099/1")
    assert len(res.mentions) == 1
    assert res.mentions[0].cite_kind == CiteKind.NON_STATUTORY_INSTRUMENT
