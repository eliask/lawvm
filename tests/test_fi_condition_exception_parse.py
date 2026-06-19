"""Tests for the condition / exception construction parse + census.

Mirrors ``tests/test_fi_modal_parse.py`` discipline: IR + projection + total
token ownership + condition shapes + exception shapes + attachment to a deontic
(modal) core + ambiguous attachment (tag-don't-guess) + census classification on
hand-built witnesses.

The parse is SURFACE-ONLY and ADDITIVE; these tests assert the construction-grammar
contract (closed cue list mirrored from the production H6 lens, kind
classification, attachment status, no silent drop, oracle-comparable projection
key), NOT any production behaviour change.
"""
from __future__ import annotations

from lawvm.finland.legal_surface.condition_exception_census import (
    _condexc_oracle_keys_for_span,
    _condexc_segment_selector,
)
from lawvm.finland.legal_surface.condition_exception_parse import (
    ATTACH_AMBIGUOUS,
    ATTACH_CANDIDATE,
    ATTACH_RESOLVED,
    CONDEXC_LANE_CONSTRUCTION_OWNED,
    CONDEXC_LANE_DECLINED,
    KIND_CONDITION,
    KIND_EXCEPTION,
    assert_total_ownership,
    condexc_key,
    parse_condition_exception_sentence,
    projection_condexc_keys,
)
from lawvm.finland.legal_surface.family_census import classify


# ---------------------------------------------------------------------------
# IR + total token ownership
# ---------------------------------------------------------------------------


def _check_total(text: str) -> None:
    cp = parse_condition_exception_sentence(text)
    assert_total_ownership(cp)


def test_total_ownership_holds_on_each_shape() -> None:
    for text in (
        "Jos hakemus on puutteellinen, viranomaisen on pyydettävä täydennystä.",
        "Lupa voidaan myöntää, jollei hakija ole konkurssissa.",
        "Säännöstä ei kuitenkaan sovelleta valtion liikelaitokseen.",
        "Sen estämättä mitä 5 §:ssä säädetään, päätös voidaan panna täytäntöön.",
        "Asiakirja on toimitettava, mikäli viranomainen sitä pyytää.",
        "Etuus myönnetään, edellyttäen että hakija täyttää ehdot.",
        "Pelkkää proosaa ilman ehtoja tai poikkeuksia.",
        "",
    ):
        _check_total(text)


def test_total_ownership_holds_with_trailing_and_leading_prose() -> None:
    # Cue mid-sentence with prose on both sides; residual must own the rest.
    text = "Viranomaisen on tehtävä päätös, jollei asiaa ole jätettävä tutkimatta."
    cp = parse_condition_exception_sentence(text)
    assert_total_ownership(cp)
    assert cp.kind == "condexc"
    # Every char covered: union of owned spans + residuals == full range.
    covered = [False] * len(text)
    for q in cp.qualifiers:
        for i in range(q.cue_start, q.cue_end):
            covered[i] = True
        if q.clause_start is not None and q.clause_end is not None:
            for i in range(q.clause_start, q.clause_end):
                covered[i] = True
    for r in cp.residuals:
        for i in range(r.char_start, r.char_end):
            covered[i] = True
    assert all(covered)


# ---------------------------------------------------------------------------
# Condition shapes
# ---------------------------------------------------------------------------


def test_condition_jos_clause_initial() -> None:
    text = "Jos hakemus on puutteellinen, viranomaisen on pyydettävä täydennystä."
    cp = parse_condition_exception_sentence(text)
    assert cp.parser_lane == CONDEXC_LANE_CONSTRUCTION_OWNED
    quals = [q for q in cp.qualifiers if q.cue == "jos"]
    assert len(quals) == 1
    q = quals[0]
    assert q.kind == KIND_CONDITION
    # The qualified clause is the adjunct between the cue and the comma boundary.
    assert q.clause_start is not None and q.clause_end is not None
    assert text[q.clause_start : q.clause_end] == "hakemus on puutteellinen"


def test_condition_mikali_and_edellyttaen() -> None:
    cp = parse_condition_exception_sentence(
        "Etuus myönnetään, edellyttäen että hakija täyttää asetetut ehdot."
    )
    cues = {q.cue: q.kind for q in cp.qualifiers}
    assert cues.get("edellyttäen että") == KIND_CONDITION

    cp2 = parse_condition_exception_sentence(
        "Asiakirja on toimitettava, mikäli viranomainen sitä pyytää."
    )
    cues2 = {q.cue: q.kind for q in cp2.qualifiers}
    assert cues2.get("mikäli") == KIND_CONDITION


def test_jos_midclause_declines_precision_over_recall() -> None:
    # A 'jos' NOT clause-initial-ish (mid-clause, no boundary before) must be
    # skipped (mirrors the production guard) → no qualifier → declined.
    text = "Ei ole selvää josko ehto täyttyy."  # 'josko' is not 'jos' (word-bounded)
    cp = parse_condition_exception_sentence(text)
    assert cp.parser_lane == CONDEXC_LANE_DECLINED
    assert cp.qualifiers == ()


# ---------------------------------------------------------------------------
# Exception shapes
# ---------------------------------------------------------------------------


def test_exception_ei_kuitenkaan() -> None:
    cp = parse_condition_exception_sentence(
        "Säännöstä ei kuitenkaan sovelleta valtion liikelaitokseen."
    )
    quals = [q for q in cp.qualifiers if q.cue == "ei kuitenkaan"]
    assert len(quals) == 1
    assert quals[0].kind == KIND_EXCEPTION


def test_exception_sen_estamatta_and_poiketen() -> None:
    cp = parse_condition_exception_sentence(
        "Sen estämättä mitä 5 §:ssä säädetään, päätös pannaan täytäntöön."
    )
    cues = {q.cue for q in cp.qualifiers}
    assert "sen estämättä" in cues
    assert all(
        q.kind == KIND_EXCEPTION for q in cp.qualifiers if q.cue == "sen estämättä"
    )

    cp2 = parse_condition_exception_sentence(
        "Poiketen siitä mitä 3 §:ssä säädetään, lupa voidaan myöntää määräajaksi."
    )
    cues2 = {q.cue for q in cp2.qualifiers}
    assert "poiketen siitä mitä" in cues2


def test_exception_jollei_is_exception_kind() -> None:
    # The production lens classes 'jollei'/'ellei' ("unless") under EXCEPTION; we
    # mirror that mapping verbatim so the projection is comparable.
    cp = parse_condition_exception_sentence(
        "Lupa voidaan myöntää, jollei hakija ole konkurssissa."
    )
    quals = [q for q in cp.qualifiers if q.cue == "jollei"]
    assert len(quals) == 1
    assert quals[0].kind == KIND_EXCEPTION


# ---------------------------------------------------------------------------
# Attachment to a deontic (modal) core
# ---------------------------------------------------------------------------


def test_attachment_resolved_to_single_modal_core() -> None:
    # Exactly one deontic core ('voidaan' = permission) → resolved attachment.
    text = "Lupa voidaan myöntää, jollei hakija ole konkurssissa."
    cp = parse_condition_exception_sentence(text)
    assert len(cp.cores) == 1
    q = next(q for q in cp.qualifiers if q.cue == "jollei")
    assert q.attachment_status == ATTACH_RESOLVED
    assert q.attached_core_index == 0


def test_attachment_candidate_when_no_modal_core() -> None:
    # No deontic core in the matrix → candidate (target not yet typed), never
    # silently invented.
    text = "Jos sää on huono, tapahtuma siirtyy."
    cp = parse_condition_exception_sentence(text)
    assert cp.cores == ()
    q = next(q for q in cp.qualifiers if q.cue == "jos")
    assert q.attachment_status == ATTACH_CANDIDATE
    assert q.attached_core_index is None


def test_attachment_ambiguous_with_multiple_cores_tag_dont_guess() -> None:
    # Two deontic cores ('on tehtävä' obligation + 'voidaan' permission) → the
    # qualifier's target is genuinely ambiguous; the parse FLAGS it ambiguous
    # (records the nearest as candidate) rather than silently picking one.
    # Two cores separated by a clause boundary so the first object span
    # terminates before the second cue ('voidaan'): obligation + permission.
    text = (
        "Viranomaisen on tehtävä päätös; asia voidaan ratkaista, "
        "jos hakemus on täydellinen."
    )
    cp = parse_condition_exception_sentence(text)
    assert len(cp.cores) >= 2
    q = next(q for q in cp.qualifiers if q.cue == "jos")
    assert q.attachment_status == ATTACH_AMBIGUOUS
    # The nearest core is recorded as the candidate target, but flagged ambiguous.
    assert q.attached_core_index is not None


# ---------------------------------------------------------------------------
# Projection + census classification
# ---------------------------------------------------------------------------


def test_projection_keys_match_condexc_key() -> None:
    text = "Säännöstä ei kuitenkaan sovelleta, jos hakija on alaikäinen."
    cp = parse_condition_exception_sentence(text)
    keys = projection_condexc_keys(cp)
    assert condexc_key(KIND_EXCEPTION, "ei kuitenkaan") in keys
    assert condexc_key(KIND_CONDITION, "jos") in keys


def test_projection_keys_are_oracle_comparable() -> None:
    # The projection key form must match the lowered/casefolded production-oracle
    # key form so the census set differential is honest.
    text = "Etuus myönnetään, mikäli hakija täyttää ehdot."
    proj = projection_condexc_keys(parse_condition_exception_sentence(text))
    oracle = _condexc_oracle_keys_for_span(text)
    # Both should agree on the 'condition:mikäli' key (parity-by-construction).
    assert condexc_key(KIND_CONDITION, "mikäli") in proj
    assert condexc_key(KIND_CONDITION, "mikäli") in oracle


def test_census_classify_match_superset_miss() -> None:
    # match: identical sets.
    assert classify({"condition:jos"}, {"condition:jos"}, declined=False) == "match"
    # superset: projection has an extra cue the oracle lacked.
    assert (
        classify(
            {"condition:jos", "exception:ei kuitenkaan"},
            {"condition:jos"},
            declined=False,
        )
        == "superset"
    )
    # miss: oracle found a cue the projection lacked.
    assert (
        classify({"condition:jos"}, {"condition:jos", "exception:paitsi"}, declined=False)
        == "miss"
    )


def test_segment_selector_skips_non_family_sentences() -> None:
    # A body whose sentences carry no condition/exception cue yields no units;
    # a sentence with a cue yields exactly one unit (the family discriminator).
    body = "Tämä on tavallinen virke.\nJos ehto täyttyy, etuus myönnetään."
    units = list(_condexc_segment_selector("0000/000", body))
    assert len(units) == 1
    assert units[0].declared_marker == f"sentence:{KIND_CONDITION}"
    assert units[0].parser_lane == CONDEXC_LANE_CONSTRUCTION_OWNED
    assert units[0].totality_ok is True
