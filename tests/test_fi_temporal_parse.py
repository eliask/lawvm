"""Tests for the temporal/applicability construction parse + census (IR + projection).

Hand-built witness clauses for the temporal family: commencement (long-form date,
numeric date, placeholder), fixed-term validity, application/transition, mixed
prose, and a non-temporal decline. Asserts the IR, the total-ownership
postcondition, the projection key set, and the differential census classification
against the production temporal oracle.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.family_census import classify
from lawvm.finland.legal_surface.temporal_census import (
    _temporal_miss_shape,
    _temporal_oracle_keys_for_span,
    _temporal_projection,
    _temporal_segment_selector,
)
from lawvm.finland.legal_surface.temporal_parse import (
    ROLE_APPLICATION,
    ROLE_COMMENCEMENT,
    ROLE_VALIDITY,
    TEMPORAL_LANE_CONSTRUCTION_OWNED,
    TEMPORAL_LANE_DECLINED,
    assert_total_ownership,
    parse_temporal_sentence,
    projection_temporal_keys,
    temporal_key,
)


# --------------------------------------------------------------------------- #
# IR — commencement
# --------------------------------------------------------------------------- #


def test_commencement_long_form_date():
    text = "Tämä laki tulee voimaan 1 päivänä tammikuuta 2016."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    assert tp.parser_lane == TEMPORAL_LANE_CONSTRUCTION_OWNED
    assert len(tp.clauses) == 1
    c = tp.clauses[0]
    assert c.role == ROLE_COMMENCEMENT
    assert c.cue.lower() == "tulee voimaan"
    assert c.date == "2016-01-01"
    # the cue + the date span are both owned
    assert text[c.cue_start : c.cue_end].lower() == "tulee voimaan"
    assert c.date_start is not None and c.date_end is not None
    assert "tammikuuta" in text[c.date_start : c.date_end]
    assert_total_ownership(tp)


def test_commencement_numeric_date_no_date_extracted():
    # The production date extractor does not parse the numeric ``1.1.2027`` form;
    # the projection records the role with an EMPTY date (honest, not guessed).
    text = "Tämä laki tulee voimaan 1.1.2027."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    c = tp.clauses[0]
    assert c.role == ROLE_COMMENCEMENT
    assert c.date == ""
    assert c.date_start is None and c.date_end is None
    assert_total_ownership(tp)


def test_commencement_placeholder():
    text = "Tämä laki tulee voimaan päivänä kuuta 20 ."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    c = tp.clauses[0]
    assert c.role == ROLE_COMMENCEMENT
    assert c.date == ""
    assert_total_ownership(tp)


# --------------------------------------------------------------------------- #
# IR — validity / fixed-term
# --------------------------------------------------------------------------- #


def test_validity_fixed_term_date():
    text = "Tämä laki on voimassa 31 päivään joulukuuta 2025."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    c = tp.clauses[0]
    assert c.role == ROLE_VALIDITY
    assert "on voimassa" in c.cue.lower()
    assert c.date == "2025-12-31"
    assert c.date_start is not None
    assert_total_ownership(tp)


def test_validity_period_no_date():
    text = "Tämä laki on voimassa vuoden 2025 loppuun."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    c = tp.clauses[0]
    assert c.role == ROLE_VALIDITY
    # no NN päivä[äa]n Kkkuuta YYYY span → empty date, honestly recorded
    assert c.date == ""
    assert_total_ownership(tp)


# --------------------------------------------------------------------------- #
# IR — application / transition
# --------------------------------------------------------------------------- #


def test_application_clause():
    text = "Tätä lakia sovelletaan ensimmäisen kerran vuodelta 2016 toimitettavassa verotuksessa."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    c = tp.clauses[0]
    assert c.role == ROLE_APPLICATION
    assert c.date == ""
    assert_total_ownership(tp)


def test_validity_precedence_over_commencement():
    # A sentence carrying BOTH "on voimassa" and "tulee voimaan" classifies as
    # validity (production precedence: EXPIRY pattern checked before COMMENCEMENT).
    text = "Tämä laki tulee voimaan ja on voimassa 31 päivään joulukuuta 2025."
    tp = parse_temporal_sentence(text)
    assert tp.clauses[0].role == ROLE_VALIDITY


# --------------------------------------------------------------------------- #
# IR — decline / non-temporal
# --------------------------------------------------------------------------- #


def test_non_temporal_declines():
    text = "Tässä laissa säädetään eläinten hyvinvoinnista."
    tp = parse_temporal_sentence(text)
    assert tp.kind == "declined"
    assert tp.parser_lane == TEMPORAL_LANE_DECLINED
    assert tp.clauses == ()
    # the whole span is explicit residue → total ownership still holds
    assert_total_ownership(tp)
    assert tp.residuals[0].reason == "not_temporal_bearing"


def test_total_ownership_on_mixed_prose():
    text = (
        "Sen estämättä, mitä 1 momentissa säädetään, tämä laki tulee voimaan "
        "1 päivänä tammikuuta 2016 ja sitä sovelletaan taannehtivasti."
    )
    tp = parse_temporal_sentence(text)
    assert tp.kind == "temporal"
    # validity-cue absent; "sovelletaan" present but "tulee voimaan" wins? No —
    # application cue precedes commencement in precedence, so role==application.
    assert tp.clauses[0].role in {ROLE_APPLICATION, ROLE_COMMENCEMENT}
    assert_total_ownership(tp)


# --------------------------------------------------------------------------- #
# Projection keys
# --------------------------------------------------------------------------- #


def test_projection_keys_commencement():
    tp = parse_temporal_sentence("Tämä laki tulee voimaan 1 päivänä tammikuuta 2016.")
    assert projection_temporal_keys(tp) == {"commencement:2016-01-01"}


def test_projection_keys_validity_maps_to_expiry():
    # The family's "validity" role projects onto the production EXPIRY kind.
    tp = parse_temporal_sentence("Tämä laki on voimassa 31 päivään joulukuuta 2025.")
    assert projection_temporal_keys(tp) == {"expiry:2025-12-31"}


def test_projection_keys_application_maps_to_transition():
    tp = parse_temporal_sentence("Tätä lakia sovelletaan ensimmäisen kerran vuodelta 2016.")
    assert projection_temporal_keys(tp) == {"transition:"}


def test_temporal_key_form():
    assert temporal_key(ROLE_COMMENCEMENT, "2016-01-01") == "commencement:2016-01-01"
    assert temporal_key(ROLE_VALIDITY, "2025-12-31") == "expiry:2025-12-31"
    assert temporal_key(ROLE_APPLICATION, "") == "transition:"


# --------------------------------------------------------------------------- #
# Census classification — projection vs production oracle on the same span
# --------------------------------------------------------------------------- #


def _bucket(text: str) -> str:
    tp = parse_temporal_sentence(text)
    proj = projection_temporal_keys(tp)
    oracle = _temporal_oracle_keys_for_span(text)
    declined = tp.parser_lane == TEMPORAL_LANE_DECLINED
    return classify(proj, oracle, declined)


def test_census_match_commencement():
    # projection key == oracle key → match (parity by construction)
    assert _bucket("Tämä laki tulee voimaan 1 päivänä tammikuuta 2016.") == "match"


def test_census_match_validity():
    assert _bucket("Tämä laki on voimassa 31 päivään joulukuuta 2025.") == "match"


def test_census_match_application():
    assert _bucket("Tätä lakia sovelletaan ensimmäisen kerran vuodelta 2016.") == "match"


def test_census_match_multi_clause_unit():
    # Two production sub-sentences in one substrate sentence: commencement THEN
    # fixed-term validity. The parser mirrors the oracle's internal split and
    # emits BOTH clauses → match (this was the dominant census miss blocker).
    text = (
        "Tämä asetus tulee voimaan 1 päivänä tammikuuta 2015. "
        "Asetus on voimassa 31 päivään joulukuuta 2016."
    )
    tp = parse_temporal_sentence(text)
    assert {c.role for c in tp.clauses} == {ROLE_COMMENCEMENT, ROLE_VALIDITY}
    assert projection_temporal_keys(tp) == {
        "commencement:2015-01-01",
        "expiry:2016-12-31",
    }
    assert_total_ownership(tp)
    assert _bucket(text) == "match"


def test_census_match_numeric_commencement():
    # Both projection and oracle record commencement with empty date → match.
    assert _bucket("Tämä laki tulee voimaan 1.1.2027.") == "match"


def test_oracle_keys_shape():
    keys = _temporal_oracle_keys_for_span("Tämä laki tulee voimaan 1 päivänä tammikuuta 2016.")
    assert keys == {"commencement:2016-01-01"}


# --------------------------------------------------------------------------- #
# Miss-shape namer
# --------------------------------------------------------------------------- #


def test_miss_shape_dated_commencement():
    shape = _temporal_miss_shape({"commencement:2016-01-01"}, "sentence:commencement")
    assert shape == "commencement|dated"


def test_miss_shape_undated_application():
    shape = _temporal_miss_shape({"transition:"}, "sentence:application")
    assert shape == "transition|undated"


# --------------------------------------------------------------------------- #
# Selector — yields cue-bearing sentences, skips non-temporal, total ownership
# --------------------------------------------------------------------------- #


def test_selector_yields_cue_sentences_only():
    body = (
        "Eläinten hyvinvointilaki\n"
        "Tässä laissa säädetään eläinten hyvinvoinnista.\n"
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2016.\n"
    )
    units = list(_temporal_segment_selector("2015/100", body))
    # Only the commencement sentence carries a temporal cue.
    assert len(units) == 1
    u = units[0]
    assert u.declared_marker == "sentence:commencement"
    assert not u.declined
    assert u.totality_ok
    # projection on the unit text matches the oracle
    proj = _temporal_projection(u, "2015/100")
    assert proj == {"commencement:2016-01-01"}
