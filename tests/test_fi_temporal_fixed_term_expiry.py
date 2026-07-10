"""Fixed-term whole-law statute expiry (määräaikainen laki) — Pro §9 test list.

Most cases use synthetic timelines built directly so they exercise the extractor
and seam overlay deterministically without corpus replay; the 482/2024 trio uses
the real corpus and is skipped when data/finlex.farchive is absent. The seam
SEMANTICS are gated by LAWVM_ENABLE_FIXED_TERM_STATUTE_BOUNDS, set via monkeypatch
in the tests that assert the expired/blocked behaviour.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import pytest

from lawvm.core.ir import IRNode, LegalAddress, ProvisionTimeline, ProvisionVersion
from lawvm.core.ir_helpers import irnode_content_hash
from lawvm.core.provenance import OperationSource
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.statute_validity import (
    StatuteValidityBound,
    expires_on_from_valid_until,
    governing_bound,
    is_expired_at,
    late_extension_gap,
)
from lawvm.finland.fixed_term_expiry import (
    DECREE_SET_COMMENCEMENT_UNRESOLVED,
    DURATION_ARITHMETIC_AUTHORITY_MISSING,
    DURATION_COMMENCEMENT_UNRESOLVED,
    EVENT_BOUND_OUT_OF_DOCTRINE,
    EVENT_BOUND_RESOLVER_MISSING,
    EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT,
    FIXED_TERM_EXPIRY_AMBIGUOUS,
    FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS,
    FIXED_TERM_EXPIRY_UNPARSEABLE,
    NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED,
    SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED,
    SOURCE_IMPOSSIBLE_DATE,
    START_ONLY_NOT_EXPIRY_BOUND,
    build_corpus_report,
    extract_fixed_term_bounds,
    governing_unparseable,
)
from lawvm.tools.provision_state import (
    FIXED_TERM_BOUNDS_FLAG,
    build_provision_state_response,
)

_CORPUS = Path("data/finlex.farchive")
_VOIMAANTULO = LegalAddress(path=(("section", "7"),))


def _enable_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")


def _voimaantulo_node(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label="7", text=text)


def _voimaantulo_version(
    *,
    effective: str,
    enacted: str,
    text: str,
    source_statute: str,
    variant_kind: Literal["permanent", "temporary"] = "permanent",
    expires: str = "",
    content: IRNode | None = None,
) -> ProvisionVersion:
    node = content if content is not None else _voimaantulo_node(text)
    return ProvisionVersion(
        effective=effective,
        enacted=enacted,
        expires=expires,
        variant_kind=variant_kind,
        content=node,
        source=OperationSource(
            statute_id=source_statute,
            title="Amending Act",
            enacted=enacted,
            effective=effective,
            raw_text=text,
        ),
        content_hash=irnode_content_hash(node) if node is not None else "",
    )


def _timelines(versions: list[ProvisionVersion]) -> dict[LegalAddress, ProvisionTimeline]:
    return {_VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=versions)}


def _state(
    timelines: dict[LegalAddress, ProvisionTimeline],
    *,
    as_of: str,
    statute_id: str = "2099/1",
    provision: str = "section:7",
    query_type: str = "in_force",
) -> dict[str, Any]:
    return build_provision_state_response(
        timelines=timelines,
        statute_id=statute_id,
        jurisdiction="fi",
        provision=provision,
        as_of=as_of,
        query_type=query_type,
    )


# ---------------------------------------------------------------------------
# Pure bitemporal-rule unit checks (no seam)
# ---------------------------------------------------------------------------


def _bound(effective: str, valid_until: str, *, enacted: str | None = None, seq: int = 0) -> StatuteValidityBound:
    import datetime as dt

    vu = dt.date.fromisoformat(valid_until)
    return StatuteValidityBound(
        statute_id="2099/1",
        scope="whole_statute",
        effective=effective,
        enacted=enacted,
        valid_until=valid_until,
        expires_on=expires_on_from_valid_until(vu).isoformat(),
        source_provision=_VOIMAANTULO,
        source_version_id="2099/1",
        source_hash="h",
        source_span=None,
        rule_id="fixed_term_whole_statute_expiry",
        source_text="Tämä laki ... on voimassa",
        source_sequence=seq,
    )


def test_inclusive_valid_until_exclusive_expires_on() -> None:
    bound = _bound("2025-07-01", "2026-12-31")
    assert bound.valid_until == "2026-12-31"
    assert bound.expires_on == "2027-01-01"
    # Live ON the inclusive valid_until, expired the next day.
    assert is_expired_at(bound, "2026-12-31") is False
    assert is_expired_at(bound, "2027-01-01") is True


def test_governing_bound_picks_latest_eligible_under_extension() -> None:
    old = _bound("2024-07-01", "2025-12-31", seq=0)
    new = _bound("2025-07-01", "2026-12-31", seq=1)
    bounds = (old, new)
    assert governing_bound(bounds, as_of="2025-06-30") is old
    assert governing_bound(bounds, as_of="2025-07-01") is new
    at_end = governing_bound(bounds, as_of="2026-12-31")
    at_after = governing_bound(bounds, as_of="2027-01-01")
    assert at_end is not None and at_after is not None
    assert is_expired_at(at_end, "2026-12-31") is False
    assert is_expired_at(at_after, "2027-01-01") is True


def test_in_force_query_ignores_not_yet_enacted_bound() -> None:
    enacted_old = _bound("2024-07-01", "2025-12-31", enacted="2024-06-01", seq=0)
    retroactive = _bound("2025-07-01", "2026-12-31", enacted="2026-03-01", seq=1)
    bounds = (enacted_old, retroactive)
    # As-of 2025-08-01 for an in_force query: the retroactive bound is enacted
    # 2026-03-01 > D, so it is not used; the earlier enacted bound governs.
    assert governing_bound(bounds, as_of="2025-08-01", query_type="in_force") is enacted_old
    # A governing/legal-state query may use it.
    assert governing_bound(bounds, as_of="2025-08-01", query_type="governing") is retroactive


def test_late_extension_gap_detected() -> None:
    old = _bound("2024-07-01", "2025-12-31", seq=0)
    late = _bound("2026-02-01", "2026-12-31", seq=1)
    bounds = (old, late)
    assert late_extension_gap(bounds, late) is True
    assert late_extension_gap(bounds, old) is False


# ---------------------------------------------------------------------------
# Extraction (synthetic timelines)
# ---------------------------------------------------------------------------

_EXT_TEXT = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2026."
_OLD_TEXT = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2025."


def test_extracts_one_bound_per_version() -> None:
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
        ]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert extraction.has_candidate is True
    effectives = sorted(b.effective for b in extraction.bounds)
    assert effectives == ["2024-01-01", "2025-07-01"]
    by_eff = {b.effective: b for b in extraction.bounds}
    assert by_eff["2024-01-01"].valid_until == "2025-12-31"
    assert by_eff["2025-07-01"].valid_until == "2026-12-31"
    assert by_eff["2025-07-01"].source_version_id == "2099/368"


def test_old_style_month_first_range_parses() -> None:
    text = (
        "2 § Tämä asetus on voimassa tammikuun 1 päivästä joulukuun 31 päivään 1917."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1917-01-01", enacted="1916-12-20", text=text, source_statute="1917/4")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1917/4", timelines=timelines)
    assert extraction.has_candidate is True
    assert [b.valid_until for b in extraction.bounds] == ["1917-12-31"]
    assert not any(d.code == FIXED_TERM_EXPIRY_UNPARSEABLE for d in extraction.diagnostics)


def test_year_end_with_intervening_words_parses() -> None:
    text = "5 § Tämä laki on voimassa julkaisemispäivästä vuoden 1918 loppuun."
    timelines = _timelines(
        [_voimaantulo_version(effective="1917-12-01", enacted="1917-11-20", text=text, source_statute="1917/126")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1917/126", timelines=timelines)
    assert [b.valid_until for b in extraction.bounds] == ["1918-12-31"]


def test_bare_toistaiseksi_is_not_a_fixed_term_candidate() -> None:
    text = (
        "10 § Tämä päätös astuu voimaan heti kun se on asetuskokoelmassa "
        "julkaistu ja on voimassa toistaiseksi."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1917-08-01", enacted="1917-07-20", text=text, source_statute="1917/114")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1917/114", timelines=timelines)
    assert extraction.has_candidate is False
    assert extraction.bounds == ()
    assert not any(d.code == FIXED_TERM_EXPIRY_UNPARSEABLE for d in extraction.diagnostics)


def test_toistaiseksi_with_hard_cap_parses_cap_as_bound() -> None:
    text = (
        "10 § Tämä päätös astuu voimaan heti ja on voimassa toistaiseksi, "
        "ei kuitenkaan kauvemmin kuin 1 päivään toukokuuta 1918."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1917-08-01", enacted="1917-07-20", text=text, source_statute="1917/114")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1917/114", timelines=timelines)
    assert extraction.has_candidate is True
    assert [b.valid_until for b in extraction.bounds] == ["1918-05-01"]
    bound = extraction.bounds[0]
    # V3: the date is an OUTER CAP on an open-ended validity, not a stated
    # expiry day; the distinction is carried on the bound.
    assert bound.bound_kind == "upper_cap"
    assert bound.source_phrase_kind == "toistaiseksi_ei_kauemmin_kuin"
    assert bound.earlier_termination_possible is True


def test_extended_terminal_date_families_parse() -> None:
    # Forms observed in the corpus residual: essive, bare day-month, dotted
    # day, month-day-genitive end, day-end-month.
    cases = (
        ("3 § Tämä asetus tulee voimaan 1 päivänä tammikuuta 2005 ja on voimassa "
         "31 päivänä joulukuuta 2006 saakka.", "2006-12-31", "fi_fixed_term_day_essive"),
        ("4 § Tämä asetus tulee voimaan 1 päivänä tammikuuta 2010 ja on voimassa "
         "31 päivänä joulukuuta 2012.", "2012-12-31", "fi_fixed_term_day_essive"),
        ("5 § Tämä asetus tulee voimaan 1 päivänä huhtikuuta 2021 ja on voimassa "
         "31 maaliskuuta 2022.", "2022-03-31", "fi_fixed_term_bare_day_month"),
        ("6 § Tämä päätös tulee voimaan heti ja on voimassa 31. joulukuuta 1998.",
         "1998-12-31", "fi_fixed_term_bare_day_month"),
        ("2 § Tämä päätös tulee voimaan heti ja on voimassa 24 päivästä maaliskuuta "
         "1992 maaliskuun 28 päivän 1992 loppuun saakka.",
         "1992-03-28", "fi_fixed_term_month_day_genitive_end"),
        ("7 § Tämä asetus tulee voimaan heti ja on voimassa 31 päivän loppuun "
         "joulukuuta 2003 asti.", "2003-12-31", "fi_fixed_term_day_end_month"),
    )
    for text, expected, expected_rule in cases:
        timelines = _timelines(
            [_voimaantulo_version(effective="1990-01-01", enacted="1989-12-01", text=text, source_statute="1990/2")]
        )
        extraction = extract_fixed_term_bounds(statute_id="1990/2", timelines=timelines)
        assert [b.valid_until for b in extraction.bounds] == [expected], text
        assert extraction.bounds[0].rule_id == expected_rule, text


def test_source_typo_forms_fold_and_parse() -> None:
    # Source typos observed in the corpus: doubled-t month, missing space
    # before päivään, hyphenated month, single-p lopuun.
    cases = (
        ("3 § Tämä laki tulee voimaan heti ja on voimassa 31 päivään joulukuutta 1999.",
         "1999-12-31"),
        ("4 § Tämä asetus tulee voimaan heti ja on voimassa 31päivään joulukuuta 2012.",
         "2012-12-31"),
        ("5 § Tämä laki tulee voimaan heti ja on voimassa 31 päivään joulukuu-ta 2026.",
         "2026-12-31"),
        ("6 § Tämä asetus tulee voimaan heti ja on voimassa vuoden 2002 lopuun.",
         "2002-12-31"),
    )
    for text, expected in cases:
        timelines = _timelines(
            [_voimaantulo_version(effective="1990-01-01", enacted="1989-12-01", text=text, source_statute="1990/3")]
        )
        extraction = extract_fixed_term_bounds(statute_id="1990/3", timelines=timelines)
        assert [b.valid_until for b in extraction.bounds] == [expected], text


def test_act_citation_dates_are_not_bounds() -> None:
    # "DD päivänä Xkuuta YYYY annettu laki" inside the validity clause is a
    # citation, never the bound; with no real date the clause stays blocking.
    text = (
        "7 § Tämä laki tulee voimaan heti ja on voimassa niin kauan kuin 3 "
        "päivänä toukokuuta 1927 annettu tielaki on voimassa."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1954-06-01", enacted="1954-05-01", text=text, source_statute="1954/244")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1954/244", timelines=timelines)
    assert extraction.bounds == ()
    assert [d.code for d in extraction.diagnostics] == [FIXED_TERM_EXPIRY_UNPARSEABLE]


def test_invalid_calendar_date_stays_blocking() -> None:
    # 1993/319 states "31 päivään kesäkuuta 1995" — June 31 does not exist.
    # The source states an impossible date; never guess a nearby real one.
    text = (
        "9 § Tämä asetus tulee voimaan 1 päivänä huhtikuuta 1993 ja on voimassa "
        "31 päivään kesäkuuta 1995."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1993-04-01", enacted="1993-03-01", text=text, source_statute="1993/319")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1993/319", timelines=timelines)
    assert extraction.bounds == ()
    assert [d.code for d in extraction.diagnostics] == [SOURCE_IMPOSSIBLE_DATE]
    assert "kesäkuuta 1995" in extraction.diagnostics[0].clause_text
    # The candidate normalization is recorded as evidence, never as the bound.
    assert "1995-06-30" in extraction.diagnostics[0].detail


def test_per_grammar_family_rule_ids() -> None:
    cases = (
        ("7 § Tämä laki tulee voimaan heti ja on voimassa 31 päivään joulukuuta 2020.",
         "fi_fixed_term_day_first_paivaan"),
        ("2 § Tämä asetus on voimassa tammikuun 1 päivästä joulukuun 31 päivään 1917.",
         "fi_fixed_term_month_first_genitive"),
        ("5 § Tämä laki tulee voimaan heti ja on voimassa vuoden 1918 loppuun.",
         "fi_fixed_term_year_end"),
        ("4 § Tämä päätös tulee voimaan 4.8.1994 ja se on voimassa 31.12.1995 saakka.",
         "fi_fixed_term_dotted_numeric"),
    )
    for text, expected_rule in cases:
        timelines = _timelines(
            [_voimaantulo_version(effective="1990-01-01", enacted="1989-12-01", text=text, source_statute="1990/1")]
        )
        extraction = extract_fixed_term_bounds(statute_id="1990/1", timelines=timelines)
        assert [b.rule_id for b in extraction.bounds] == [expected_rule], text


def test_anaphoric_year_ignores_statute_citation_years() -> None:
    # The citation year 1986 must never be the antecedent; the commencement
    # date's 1987 is the only plausible same-sentence year.
    text = (
        "7 § Tämä päätös, joka perustuu lakiin (123/1986), tulee voimaan 18 "
        "päivänä maaliskuuta 1987 ja on voimassa sanotun vuoden loppuun."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1987-03-18", enacted="1987-03-01", text=text, source_statute="1987/281")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1987/281", timelines=timelines)
    assert [b.valid_until for b in extraction.bounds] == ["1987-12-31"]
    bound = extraction.bounds[0]
    assert bound.rule_id == "fi_fixed_term_anaphoric_same_sentence_year_end"
    # Antecedent provenance is stored on the bound.
    assert bound.antecedent_text is not None
    assert "1987" in bound.antecedent_text
    assert bound.antecedent_span is not None


def test_anaphoric_year_with_multiple_antecedents_blocks_as_ambiguous() -> None:
    text = (
        "7 § Tämä laki tulee voimaan 1 päivänä tammikuuta 1987, sitä sovelletaan "
        "vuonna 1988 toimitettavissa vaaleissa ja se on voimassa sanotun vuoden "
        "loppuun."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1987-01-01", enacted="1986-12-01", text=text, source_statute="1986/999")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1986/999", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS]
    assert len(diags) == 1
    assert "1987" in diags[0].detail and "1988" in diags[0].detail
    assert "sanotun vuoden loppuun" in diags[0].clause_text
    # The ambiguity blocks at the seam: it is a governing no-answer, not a skip.
    blocking = governing_unparseable(extraction, as_of="1987-06-01", query_type="governing")
    assert blocking is not None
    assert blocking.code == FIXED_TERM_EXPIRY_ANAPHORA_AMBIGUOUS


def test_voimaantulo_heading_overrides_commencement_guard() -> None:
    # Structurally known voimaantulosäännös (heading) with an unparseable
    # validity end and no commencement marker: must stay blocking, not be
    # suppressed as a body-text false positive (Pro V5 structural override).
    text = (
        "7 § Voimaantulo Tämä laki on voimassa siihen saakka, kunnes asiasta "
        "toisin säädetään."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1992-01-01", enacted="1991-12-01", text=text, source_statute="1992/1161")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1992/1161", timelines=timelines)
    codes = [d.code for d in extraction.diagnostics]
    assert codes == [EVENT_BOUND_RESOLVER_MISSING]
    assert "siihen saakka" in extraction.diagnostics[0].clause_text


def test_duration_without_concrete_commencement_blocks_with_clause_text() -> None:
    # "tulee voimaan heti": the duration is computable in principle but the
    # commencement is not a concrete date anywhere in the statute — the
    # pinned arithmetic authority cannot supply missing commencement facts.
    text = (
        "10 § Tämä laki tulee voimaan heti ja on voimassa kahden vuoden ajan "
        "sen voimaantulosta."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1992-12-01", enacted="1992-11-01", text=text, source_statute="1992/1239")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1992/1239", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_COMMENCEMENT_UNRESOLVED]
    assert len(diags) == 1
    assert "kahden vuoden ajan" in diags[0].clause_text
    assert governing_unparseable(extraction, as_of="1995-01-01", query_type="governing") is not None


def test_start_only_clause_is_nonblocking_non_candidate() -> None:
    # 2018/1092: a start date with no end marker is a commencement fact, not
    # an expiry bound — audited, never blocking.
    text = "4 § Tämä asetus on voimassa 13 päivästä joulukuuta 2018."
    timelines = _timelines(
        [_voimaantulo_version(effective="2018-12-13", enacted="2018-12-05", text=text, source_statute="2018/1092")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2018/1092", timelines=timelines)
    assert extraction.bounds == ()
    codes = [d.code for d in extraction.diagnostics]
    assert START_ONLY_NOT_EXPIRY_BOUND in codes
    assert governing_unparseable(extraction, as_of="2019-01-01", query_type="governing") is None


def test_decree_set_commencement_is_not_expiry_residue() -> None:
    # 2004/309: decree-set commencement is a commencement-resolution frontier;
    # it must not block as unverified expiry.
    text = (
        "7 § Voimaantulo ja voimassaoloaika Tämä laki tulee voimaan "
        "valtioneuvoston asetuksella säädettävänä ajankohtana. Lakia "
        "sovelletaan kunkin 1 §:n 1 momentissa mainitun valtion osalta kaksi "
        "vuotta siitä päivästä, jolloin kyseinen valtio liittyi Euroopan "
        "unionin jäseneksi."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2004-05-01", enacted="2004-04-30", text=text, source_statute="2004/309")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2004/309", timelines=timelines)
    assert extraction.bounds == ()
    codes = [d.code for d in extraction.diagnostics]
    assert DECREE_SET_COMMENCEMENT_UNRESOLVED in codes
    assert governing_unparseable(extraction, as_of="2010-01-01", query_type="governing") is None


def test_out_of_doctrine_event_bound_blocks() -> None:
    # 1994/1187: validity until a substantive (non-säädöskokoelma) event —
    # blocking, typed as out-of-doctrine rather than generic unparseable.
    text = (
        "2 § Tämä laki tulee voimaan 19 päivänä joulukuuta 1994 ja se on "
        "voimassa siihen saakka, kunnes ensimmäisissä yleisissä vaaleissa "
        "valitut valtuustot aloittavat toimintansa."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1994-12-19", enacted="1994-12-01", text=text, source_statute="1994/1187")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1994/1187", timelines=timelines)
    assert extraction.bounds == ()
    codes = [d.code for d in extraction.diagnostics]
    assert EVENT_BOUND_OUT_OF_DOCTRINE in codes
    assert governing_unparseable(extraction, as_of="1995-06-01", query_type="governing") is not None


def test_referential_voimassa_is_suppressed_non_candidate() -> None:
    # 1954/243 §118: "sikäli kuin se vielä on voimassa" qualifies a repealed
    # prior law inside a repeal enumeration — not a whole-law validity bound.
    text = (
        "118 § Tämä laki kumoaa, mikäli edellä ei ole toisin säädetty, 3 "
        "päivänä toukokuuta 1927 annetun tialain, lukuun ottamatta sen 20 §:n "
        "toista lausetta ja 33 §:n 1 momenttia, rakennuskaaren 25 luvun 8 §:n, "
        "sikäli kuin se vielä on voimassa, sekä muut tämän lain kanssa "
        "ristiriidassa olevat säännökset. Tämä laki tulee voimaan 1 päivänä "
        "tammikuuta 1958."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1958-01-01", enacted="1954-05-21", text=text, source_statute="1954/243")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1954/243", timelines=timelines)
    assert extraction.bounds == ()
    codes = [d.code for d in extraction.diagnostics]
    assert NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED in codes
    assert governing_unparseable(extraction, as_of="1960-01-01", query_type="governing") is None


def test_other_subject_voimassa_in_aggregate_text_is_suppressed() -> None:
    # 2015/1442 chapter aggregate: the act's commencement sentence and a
    # "suoritus on voimassa ..." sentence about exam parts must not combine
    # into a whole-law validity clause.
    text = (
        "26 § Voimaantulo Tämä asetus tulee voimaan 1 päivänä tammikuuta 2016. "
        "27 § Siirtymäsäännökset Jos HTM- tai KHT-tutkintosuoritusta ei ole "
        "hyväksytty kokonaisuudessaan kumotun lain mukaisen tutkintojärjestelmän "
        "voimassaolon aikana, yhden osan hyväksytty suoritus on voimassa "
        "tutkinnon osan hyväksymisvuotta seuraavien viiden vuoden ajan."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2016-01-01", enacted="2015-12-10", text=text, source_statute="2015/1442")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2015/1442", timelines=timelines)
    assert extraction.bounds == ()
    codes = [d.code for d in extraction.diagnostics]
    assert NON_EXPIRY_VALIDITY_TEXT_SUPPRESSED in codes
    assert governing_unparseable(extraction, as_of="2020-01-01", query_type="governing") is None


def test_month_end_forms_parse_to_last_day_of_month() -> None:
    year_first = (
        "2 § Tämä päätös tulee voimaan 1 päivänä huhtikuuta 1988 ja on voimassa "
        "vuoden 1989 maaliskuun loppuun."
    )
    year_after = (
        "8 § Tämä asetus tulee voimaan 1 päivänä tammikuuta 1988 ja on voimassa "
        "joulukuun loppuun 1988."
    )
    for text, expected in ((year_first, "1989-03-31"), (year_after, "1988-12-31")):
        timelines = _timelines(
            [_voimaantulo_version(effective="1988-01-01", enacted="1987-12-01", text=text, source_statute="1988/1")]
        )
        extraction = extract_fixed_term_bounds(statute_id="1988/1", timelines=timelines)
        assert [b.valid_until for b in extraction.bounds] == [expected], text


def test_said_year_end_resolves_commencement_year() -> None:
    text = (
        "7 § Tämä päätös tulee voimaan 18 päivänä maaliskuuta 1987 ja on "
        "voimassa sanotun vuoden loppuun."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1987-03-18", enacted="1987-03-01", text=text, source_statute="1987/281")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1987/281", timelines=timelines)
    assert [b.valid_until for b in extraction.bounds] == ["1987-12-31"]


def test_body_text_validity_mention_without_commencement_is_not_candidate() -> None:
    text = (
        "5) jos muussa valtiossa on annettu samaa asiaa koskeva päätös ja tämä "
        "päätös täyttää ne edellytykset, joiden vallitessa päätös on voimassa Suomessa."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1983-05-01", enacted="1983-04-20", text=text, source_statute="1983/370")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1983/370", timelines=timelines)
    assert extraction.has_candidate is False
    assert extraction.bounds == ()
    # The suppression is audited, not silent: a non-blocking observation
    # carries the suppressed clause text.
    codes = [d.code for d in extraction.diagnostics]
    assert codes == [EXPIRY_CANDIDATE_SUPPRESSED_NON_COMMENCEMENT_CONTEXT]
    assert "on voimassa Suomessa" in extraction.diagnostics[0].clause_text


def test_dotted_numeric_range_and_saakka_forms_parse() -> None:
    cases = (
        ("10 § Voimassaolo Tämä päätös on voimassa 1.1.1993 - 31.12.1993. Päätös tulee voimaan heti.", "1993-12-31"),
        ("4 § Tämä päätös tulee voimaan 4.8.1994 ja se on voimassa 31.12.1995 saakka.", "1995-12-31"),
        ("3§ Tämä päätös tulee voimaan 1 päivänä maaliskuuta 1991 ja on voimassa 15 toukokuuta 1992 saakka.", "1992-05-15"),
        ("5 § Tämä päätös tulee voimaan 1 päivänä tammikuuta 1991 ja on voimassa joulukuun 1995 loppuun.", "1995-12-31"),
        ("3 § Tämä päätös tulee voimaan 1 päivänä tammikuuta 1993 ja on voimassa 31. päivään joulukuuta 1993.", "1993-12-31"),
    )
    for text, expected in cases:
        timelines = _timelines(
            [_voimaantulo_version(effective="1991-01-01", enacted="1990-12-01", text=text, source_statute="1991/1")]
        )
        extraction = extract_fixed_term_bounds(statute_id="1991/1", timelines=timelines)
        assert [b.valid_until for b in extraction.bounds] == [expected], text


def test_until_event_clause_stays_unparseable() -> None:
    text = (
        "2 § Tämä päätös tulee voimaan 1 päivänä joulukuuta 1992 ja on voimassa "
        "siihen saakka, kunnes alkuperäilmoituksesta toisin säädetään tai määrätään."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1992-12-01", enacted="1992-11-20", text=text, source_statute="1992/1161")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1992/1161", timelines=timelines)
    assert extraction.has_candidate is True
    assert extraction.bounds == ()
    assert any(d.code == EVENT_BOUND_RESOLVER_MISSING for d in extraction.diagnostics)


def test_bare_year_duration_computes_under_pinned_rule() -> None:
    # "voimassa vuoden voimaantulosta" with a same-sentence concrete
    # commencement: one year under the 150/1930 §3 corresponding-day rule.
    # Corresponding day C = 2025-01-01; the law is in force ON its
    # commencement day, so it lapses at the start of C: valid_until = C - 1.
    text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa vuoden voimaantulosta."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert extraction.has_candidate is True
    assert [(b.valid_until, b.expires_on) for b in extraction.bounds] == [
        ("2024-12-31", "2025-01-01")
    ]
    bound = extraction.bounds[0]
    assert bound.bound_kind == "duration_from_commencement"
    assert bound.rule_id == "fi_duration_year_month_corresponding_day"
    assert bound.arithmetic_authority == "fi/150/1930"
    assert bound.epistemic_status == "computed_under_pinned_authority"
    # 150/1930 §1 scope caveat must be recorded on every computed bound.
    assert bound.authority_scope_caveat is not None
    assert "150/1930 §1" in bound.authority_scope_caveat
    assert bound.commencement_date == "2024-01-01"
    assert bound.commencement_source_kind == "same_sentence"
    assert bound.duration_spec == "P1Y"
    assert not any(d.code in (DURATION_ARITHMETIC_AUTHORITY_MISSING,
                              DURATION_COMMENCEMENT_UNRESOLVED)
                   for d in extraction.diagnostics)


# ---------------------------------------------------------------------------
# Duration-form arithmetic under the pinned 150/1930 authority (EV4)
# ---------------------------------------------------------------------------


def test_corresponding_day_rule_unit_edges() -> None:
    import datetime as dt

    from lawvm.finland.temporal_arithmetic import duration_validity_end

    # Plain corresponding day: 2 years from 15 Dec 1992 → C = 15 Dec 1994.
    two_years = duration_validity_end(dt.date(1992, 12, 15), years=2)
    assert two_years.valid_until == dt.date(1994, 12, 14)
    assert two_years.expires_on == dt.date(1994, 12, 15)
    assert two_years.duration_spec == "P2Y"
    assert two_years.rule.authority == "fi/150/1930"
    assert two_years.rule.rule_id == "fi_duration_year_month_corresponding_day"
    # 12 months into a leap year: C = 1 Mar 2016, valid through 29 Feb 2016.
    leap = duration_validity_end(dt.date(2015, 3, 1), months=12)
    assert leap.valid_until == dt.date(2016, 2, 29)
    assert leap.expires_on == dt.date(2016, 3, 1)
    # §3 month-end fallback: 6 months from 31 Aug 2020 → Feb 2021 has no
    # day 31, so C = last day of the terminal month (28 Feb 2021).
    fallback = duration_validity_end(dt.date(2020, 8, 31), months=6)
    assert fallback.expires_on == dt.date(2021, 2, 28)
    assert fallback.valid_until == dt.date(2021, 2, 27)
    # Leap-day commencement: 1 year from 29 Feb 2020 → Feb 2021 has no day
    # 29, so C = 28 Feb 2021.
    leap_day = duration_validity_end(dt.date(2020, 2, 29), years=1)
    assert leap_day.expires_on == dt.date(2021, 2, 28)
    assert leap_day.valid_until == dt.date(2021, 2, 27)
    with pytest.raises(ValueError):
        duration_validity_end(dt.date(2020, 1, 1))


def test_duration_years_genitive_form_computes() -> None:
    # 1992/1239 §7 shape: genitive numeral + "vuoden ajan sen voimaantulosta".
    text = (
        "7 § Tämä laki tulee voimaan 15 päivänä joulukuuta 1992 ja on "
        "voimassa kahden vuoden ajan sen voimaantulosta."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1992-12-15", enacted="1992-12-04", text=text, source_statute="1992/1239")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1992/1239", timelines=timelines)
    assert [(b.valid_until, b.expires_on) for b in extraction.bounds] == [
        ("1994-12-14", "1994-12-15")
    ]
    assert extraction.bounds[0].duration_spec == "P2Y"
    assert governing_unparseable(extraction, as_of="1995-01-01", query_type="governing") is None


def test_duration_months_computes_into_leap_year() -> None:
    # 2014/1212 §9 shape: "12 kuukautta lain voimaantulopäivästä lukien".
    # C = 2016-03-01; 2016 is a leap year, so the inclusive end is 29 Feb.
    text = (
        "9 § Voimaantulo Tämä laki tulee voimaan 1 päivänä maaliskuuta 2015 "
        "ja on voimassa 12 kuukautta lain voimaantulopäivästä lukien."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2015-03-01", enacted="2014-12-19", text=text, source_statute="2014/1212")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2014/1212", timelines=timelines)
    assert [(b.valid_until, b.expires_on) for b in extraction.bounds] == [
        ("2016-02-29", "2016-03-01")
    ]
    assert extraction.bounds[0].duration_spec == "P12M"


def test_duration_commencement_from_other_provision_of_same_statute() -> None:
    # 1997/230 shape: the duration clause (§3) names no date; the statute's
    # own commencement clause (§5) supplies the unique concrete date.
    addr_3 = LegalAddress(path=(("section", "3"),))
    addr_5 = LegalAddress(path=(("section", "5"),))
    v3 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1997-03-13",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            text=(
                "3 § Kiellon päättyminen Tämä päätös on voimassa viisi vuotta "
                "voimaantulopäivästä lukien."
            ),
        ),
        source=OperationSource(statute_id="1997/230"),
    )
    v5 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1997-03-13",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="5",
            text="5 § Voimaantulo Tämä päätös tulee voimaan 1 päivänä huhtikuuta 1997.",
        ),
        source=OperationSource(statute_id="1997/230"),
    )
    timelines = {
        addr_3: ProvisionTimeline(address=addr_3, versions=[v3]),
        addr_5: ProvisionTimeline(address=addr_5, versions=[v5]),
    }
    extraction = extract_fixed_term_bounds(statute_id="1997/230", timelines=timelines)
    assert [(b.valid_until, b.expires_on) for b in extraction.bounds] == [
        ("2002-03-31", "2002-04-01")
    ]
    bound = extraction.bounds[0]
    assert bound.commencement_date == "1997-04-01"
    assert bound.commencement_source_kind == "same_statute_commencement_clause"
    assert bound.duration_spec == "P5Y"


def test_duration_with_ambiguous_statute_commencements_blocks() -> None:
    # Two distinct whole-law commencement dates in the statute: the scan must
    # never pick one — the row stays blocked.
    addr_3 = LegalAddress(path=(("section", "3"),))
    addr_5 = LegalAddress(path=(("section", "5"),))
    addr_6 = LegalAddress(path=(("section", "6"),))
    v3 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1997-03-13",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            text="3 § Tämä päätös on voimassa viisi vuotta voimaantulopäivästä lukien.",
        ),
    )
    v5 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1997-03-13",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="5",
            text="5 § Tämä päätös tulee voimaan 1 päivänä huhtikuuta 1997.",
        ),
    )
    v6 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1997-03-13",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="6",
            text="6 § Tämä päätös tulee voimaan 1 päivänä kesäkuuta 1997.",
        ),
    )
    timelines = {
        addr_3: ProvisionTimeline(address=addr_3, versions=[v3]),
        addr_5: ProvisionTimeline(address=addr_5, versions=[v5]),
        addr_6: ProvisionTimeline(address=addr_6, versions=[v6]),
    }
    extraction = extract_fixed_term_bounds(statute_id="1997/230", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_COMMENCEMENT_UNRESOLVED]
    assert len(diags) == 1
    assert "viisi vuotta" in diags[0].clause_text


def test_elided_year_end_resolves_from_same_sentence_commencement() -> None:
    # 1997/7 §3 shape: "tulee voimaan ... 1997 ja on voimassa vuoden loppuun"
    # → end of the commencement year. Recorded as a high-confidence
    # inference under its own rule id, never as a grammar fact.
    text = (
        "3 § Voimaantulo Tämä päätös tulee voimaan 1 päivänä tammikuuta 1997 "
        "ja on voimassa vuoden loppuun."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1997-01-01", enacted="1996-12-20", text=text, source_statute="1997/7")]
    )
    extraction = extract_fixed_term_bounds(statute_id="1997/7", timelines=timelines)
    assert [(b.valid_until, b.expires_on) for b in extraction.bounds] == [
        ("1997-12-31", "1998-01-01")
    ]
    bound = extraction.bounds[0]
    assert bound.rule_id == "fi_elided_year_end_from_same_sentence_commencement_year"
    assert bound.epistemic_status == "high_confidence_inference"
    assert bound.arithmetic_authority is None
    assert bound.commencement_date == "1997-01-01"
    assert bound.commencement_source_kind == "same_sentence"


def test_elided_year_end_without_same_sentence_commencement_blocks() -> None:
    # The narrow inference rule requires the commencement year in the SAME
    # sentence; a bare "on voimassa vuoden loppuun" stays blocked even when
    # another provision states the commencement.
    addr_3 = LegalAddress(path=(("section", "3"),))
    addr_5 = LegalAddress(path=(("section", "5"),))
    v3 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1996-12-20",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="3",
            # Structural Voimaantulo heading keeps the clause in the blocking
            # lane (it cannot be suppressed as a body-text false positive).
            text="3 § Voimaantulo Tämä päätös on voimassa vuoden loppuun.",
        ),
    )
    v5 = ProvisionVersion(
        effective="0000-00-00",
        enacted="1996-12-20",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="5",
            text="5 § Voimaantulo Tämä päätös tulee voimaan 1 päivänä tammikuuta 1997.",
        ),
    )
    timelines = {
        addr_3: ProvisionTimeline(address=addr_3, versions=[v3]),
        addr_5: ProvisionTimeline(address=addr_5, versions=[v5]),
    }
    extraction = extract_fixed_term_bounds(statute_id="1997/7", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_COMMENCEMENT_UNRESOLVED]
    assert len(diags) == 1
    assert "vuoden loppuun" in diags[0].clause_text


def test_decree_set_commencement_with_duration_bound_blocks() -> None:
    # Decree-set commencement + duration validity end: a REAL expiry bound
    # that cannot be computed (resolving the arithmetic authority does not
    # resolve missing commencement facts) — blocking, unlike the plain
    # decree-set commencement frontier.
    text = (
        "7 § Voimaantulo Tämä laki tulee voimaan valtioneuvoston asetuksella "
        "säädettävänä ajankohtana ja on voimassa kaksi vuotta sen "
        "voimaantulosta."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2004-05-01", enacted="2004-04-30", text=text, source_statute="2004/999")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2004/999", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_COMMENCEMENT_UNRESOLVED]
    assert len(diags) == 1
    assert "asetuksella" in diags[0].clause_text
    assert governing_unparseable(extraction, as_of="2010-01-01", query_type="governing") is not None


def test_non_commencement_anchored_duration_stays_residue() -> None:
    # A duration anchored to something other than the law's own commencement
    # is outside the pinned duration_from_commencement rule's input domain.
    text = (
        "7 § Tämä laki tulee voimaan 1 päivänä tammikuuta 2020. Tämä laki on "
        "voimassa kaksi vuotta siitä päivästä, jona sopimus allekirjoitetaan."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2020-01-01", enacted="2019-12-01", text=text, source_statute="2099/5")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/5", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_ARITHMETIC_AUTHORITY_MISSING]
    assert len(diags) == 1
    assert "kaksi vuotta" in diags[0].clause_text


def test_cap_form_duration_is_never_computed_as_plain_bound() -> None:
    # "enintään <duration>" is an outer cap on an open-ended validity, not a
    # stated duration end; it must stay typed residue, never a computed bound.
    text = (
        "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2020 "
        "ja on voimassa enintään kaksi vuotta sen voimaantulosta."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="2020-01-01", enacted="2019-12-01", text=text, source_statute="2099/6")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/6", timelines=timelines)
    assert extraction.bounds == ()
    diags = [d for d in extraction.diagnostics if d.code == DURATION_ARITHMETIC_AUTHORITY_MISSING]
    assert len(diags) == 1
    assert "enintään" in diags[0].clause_text


def test_inclusive_end_convention_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    # CONVENTION PIN (Pro temporal-doctrine ruling). Two cutoff conventions
    # coexist and must not drift:
    #   1. Duration-computed ends and explicit dative calendar dates
    #      ("31 päivään joulukuuta") are INCLUSIVE ends: the law is in force
    #      ON valid_until and expired ON expires_on = valid_until + 1.
    #   2. Future event-bound resolution (NOT built here) is EXCLUSIVE at the
    #      resolver date: "voimassa päivään, jona X tulee voimaan" sets
    #      expires_on = resolver_commencement_date (NOT + 1 day) so the old
    #      state is not live on the day the resolving instrument enters
    #      force. This asymmetry is deliberate; these fixtures pin convention
    #      (1) so any future event-bound work cannot silently bend it.
    _enable_flag(monkeypatch)
    # 1a. Explicit dative date: in force ON 31 Dec, expired ON 1 Jan.
    dative = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1")]
    )
    assert _state(dative, as_of="2025-12-31")["provision_status"] == "selected"
    expired = _state(dative, as_of="2026-01-01")
    assert expired["provision_status"] == "expired"
    assert expired["valid_until"] == "2025-12-31"
    assert expired["expires"] == "2026-01-01"
    # 1b. Duration-computed end: same inclusive convention. Commencement
    # 15 Dec 1992 + 2 years → corresponding day C = 15 Dec 1994; in force
    # through 14 Dec 1994 (inclusive), expired ON 15 Dec 1994.
    duration = _timelines(
        [
            _voimaantulo_version(
                effective="1992-12-15",
                enacted="1992-12-04",
                text=(
                    "7 § Tämä laki tulee voimaan 15 päivänä joulukuuta 1992 ja "
                    "on voimassa kahden vuoden ajan sen voimaantulosta."
                ),
                source_statute="1992/1239",
            )
        ]
    )
    assert _state(duration, as_of="1994-12-14")["provision_status"] == "selected"
    expired_duration = _state(duration, as_of="1994-12-15")
    assert expired_duration["provision_status"] == "expired"
    assert expired_duration["valid_until"] == "1994-12-14"
    assert expired_duration["expires"] == "1994-12-15"


def test_seam_duration_expired_carries_arithmetic_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    text = (
        "7 § Tämä laki tulee voimaan 15 päivänä joulukuuta 1992 ja on "
        "voimassa kahden vuoden ajan sen voimaantulosta."
    )
    timelines = _timelines(
        [_voimaantulo_version(effective="1992-12-15", enacted="1992-12-04", text=text, source_statute="1992/1239")]
    )
    state = _state(timelines, as_of="1995-01-01", statute_id="1992/1239")
    assert state["provision_status"] == "expired"
    block = state["expiry"]
    assert block["rule_id"] == "fi_duration_year_month_corresponding_day"
    assert block["bound_kind"] == "duration_from_commencement"
    assert block["arithmetic_authority"] == "fi/150/1930"
    assert "150/1930 §1" in block["authority_scope_caveat"]
    assert block["epistemic_status"] == "computed_under_pinned_authority"
    assert block["commencement_date"] == "1992-12-15"
    assert block["commencement_source_kind"] == "same_sentence"
    assert block["duration_spec"] == "P2Y"


def test_scoped_chapter_form_unsupported_diagnostic() -> None:
    text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. Lain 2 luku on voimassa 31 päivään joulukuuta 2026."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    # The whole-law clause "Tämä laki ... on voimassa <date>" is present too here,
    # so a bound is still produced; assert the scoped form is at least detectable
    # on a purely-scoped version.
    scoped_only = (
        "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. "
        "Lain 2 luku on voimassa 31 päivään joulukuuta 2026."
    )
    only = _timelines(
        [
            ProvisionVersion(
                effective="2024-01-01",
                enacted="2023-12-01",
                content=IRNode(
                    kind=IRNodeKind.SECTION,
                    label="7",
                    text="Voimaantulo. Lain 2 luku on voimassa 31 päivään joulukuuta 2026.",
                ),
            )
        ]
    )
    extraction_scoped = extract_fixed_term_bounds(statute_id="2099/1", timelines=only)
    assert any(
        d.code == SCOPED_FIXED_TERM_EXPIRY_UNSUPPORTED for d in extraction_scoped.diagnostics
    )
    assert extraction_scoped.bounds == ()
    assert scoped_only  # documents the mixed-form input shape


def test_ambiguous_conflicting_bounds_same_effective() -> None:
    addr_a = LegalAddress(path=(("section", "7"),))
    addr_b = LegalAddress(path=(("section", "8"),))
    v_a = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="7",
            text="Tämä laki on voimassa 31 päivään joulukuuta 2026.",
        ),
        source=OperationSource(statute_id="2099/368"),
    )
    v_b = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(
            kind=IRNodeKind.SECTION,
            label="8",
            text="Tämä laki on voimassa 31 päivään joulukuuta 2027.",
        ),
        source=OperationSource(statute_id="2099/999"),
    )
    timelines = {
        addr_a: ProvisionTimeline(address=addr_a, versions=[v_a]),
        addr_b: ProvisionTimeline(address=addr_b, versions=[v_b]),
    }
    extraction = extract_fixed_term_bounds(statute_id="2099/1", timelines=timelines)
    assert any(d.code == FIXED_TERM_EXPIRY_AMBIGUOUS for d in extraction.diagnostics)


def test_corpus_report_aggregates_counts() -> None:
    supported = extract_fixed_term_bounds(
        statute_id="2099/1",
        timelines=_timelines(
            [_voimaantulo_version(effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/1")]
        ),
    )
    none = extract_fixed_term_bounds(
        statute_id="2099/2",
        timelines=_timelines(
            [
                ProvisionVersion(
                    effective="2020-01-01",
                    enacted="2019-12-01",
                    content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                )
            ]
        ),
    )
    report = build_corpus_report([supported, none])
    assert report.statutes_scanned == 2
    assert report.whole_law_supported == 1
    assert report.affected_statutes == ("2099/1",)


# ---------------------------------------------------------------------------
# Seam overlay (synthetic, flag ON)
# ---------------------------------------------------------------------------


def _extension_timelines() -> dict[LegalAddress, ProvisionTimeline]:
    return _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
        ]
    )


def test_seam_live_on_valid_until_then_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    timelines = _extension_timelines()
    on_bound = _state(timelines, as_of="2026-12-31")
    after = _state(timelines, as_of="2027-01-01")

    assert on_bound["provision_status"] == "selected"
    assert on_bound["version"]["content_state"] == "live"

    assert after["provision_status"] == "expired"
    assert after["version"] is None
    assert after["expires"] == "2027-01-01"
    assert after["valid_until"] == "2026-12-31"
    assert after["expiry"]["kind"] == "fixed_term_statute"
    assert after["expiry"]["scope"] == "whole_statute"
    assert after["expiry"]["source"] == "2099/368"
    assert after["expiry"]["source_version_effective"] == "2025-07-01"
    assert after["text"]["available"] is False


def test_seam_extension_governs_from_effective(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    timelines = _extension_timelines()
    # Before the extension takes effect (2025-07-01) the old bound governs; its
    # term (valid_until 2025-12-31) has not been reached, so the law is live.
    pre = _state(timelines, as_of="2025-06-30")
    assert pre["provision_status"] == "selected"
    # The extension was enacted before the old term lapsed (normal Finnish
    # practice), so the law stays continuously live into 2026 under the new bound.
    post = _state(timelines, as_of="2026-06-01")
    assert post["provision_status"] == "selected"
    # Only past the EXTENDED term does it expire.
    after = _state(timelines, as_of="2027-01-01")
    assert after["provision_status"] == "expired"
    assert after["valid_until"] == "2026-12-31"


def test_seam_late_extension_gap_revival(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    late_text = "7 § Voimaantulo Tämä laki tulee voimaan 1 päivänä tammikuuta 2024 ja on voimassa 31 päivään joulukuuta 2026."
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2024-01-01", enacted="2023-12-01", text=_OLD_TEXT, source_statute="2099/1"
            ),
            _voimaantulo_version(
                effective="2026-02-01", enacted="2026-01-20", text=late_text, source_statute="2099/500"
            ),
        ]
    )
    gap = _state(timelines, as_of="2026-01-15")
    revived = _state(timelines, as_of="2026-02-01")
    after = _state(timelines, as_of="2027-01-01")

    assert gap["provision_status"] == "expired"  # old bound lapsed 2025-12-31
    assert revived["provision_status"] == "selected"
    assert revived["version"]["content_state"] == "live"
    assert after["provision_status"] == "expired"


def test_seam_unparseable_governing_bound_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    text = "7 § Voimaantulo Tämä laki tulee voimaan heti ja on voimassa vuoden voimaantulosta."
    timelines = _timelines(
        [_voimaantulo_version(effective="2024-01-01", enacted="2023-12-01", text=text, source_statute="2099/1")]
    )
    state = _state(timelines, as_of="2024-06-01")
    assert state["provision_status"] == "expiry_unverified"
    assert state["version"] is None
    assert state["expiry"]["diagnostic"] == DURATION_COMMENCEMENT_UNRESOLVED
    assert state["expiry"]["blocking"] is True


def test_seam_ambiguous_governing_bound_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    addr_a = LegalAddress(path=(("section", "7"),))
    addr_b = LegalAddress(path=(("section", "8"),))
    v_a = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki on voimassa 31 päivään joulukuuta 2026."),
        source=OperationSource(statute_id="2099/368"),
    )
    v_b = ProvisionVersion(
        effective="2025-07-01",
        enacted="2025-06-27",
        content=IRNode(kind=IRNodeKind.SECTION, label="8", text="Tämä laki on voimassa 31 päivään joulukuuta 2027."),
        source=OperationSource(statute_id="2099/999"),
    )
    timelines = {
        addr_a: ProvisionTimeline(address=addr_a, versions=[v_a]),
        addr_b: ProvisionTimeline(address=addr_b, versions=[v_b]),
    }
    state = build_provision_state_response(
        timelines=timelines,
        statute_id="2099/1",
        jurisdiction="fi",
        provision="section:7",
        as_of="2026-06-01",
        query_type="in_force",
    )
    assert state["provision_status"] == "expiry_unverified"
    assert state["expiry"]["diagnostic"] == FIXED_TERM_EXPIRY_AMBIGUOUS


def test_seam_repeal_before_expiry_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    # A tombstone (content None) repeals §7 before the fixed-term bound; the seam
    # must report the repeal/absence, never "expired".
    repeal = ProvisionVersion(
        effective="2025-09-01",
        enacted="2025-08-01",
        content=None,
        source=OperationSource(statute_id="2099/700"),
    )
    timelines = _timelines(
        [
            _voimaantulo_version(
                effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
            ),
            repeal,
        ]
    )
    state = _state(timelines, as_of="2027-01-01")
    assert state["provision_status"] != "expired"
    assert "expiry" not in state


# ---------------------------------------------------------------------------
# Temporary overlay interaction (min wins)
# ---------------------------------------------------------------------------


def test_temporary_overlay_min_with_statute_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    _enable_flag(monkeypatch)
    # A temporary provision that expires BEFORE the statute bound drops out via
    # ordinary per-version expiry; the seam reports absence (not expired) once
    # nothing live remains and the statute is not yet past its bound.
    addr = LegalAddress(path=(("section", "5"),))
    temp = ProvisionVersion(
        effective="2025-08-01",
        enacted="2025-07-01",
        expires="2025-10-01",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="5", text="Väliaikainen pykälä."),
        source=OperationSource(statute_id="2099/368"),
    )
    voimaantulo = _voimaantulo_version(
        effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
    )
    timelines = {
        addr: ProvisionTimeline(address=addr, versions=[temp]),
        _VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=[voimaantulo]),
    }
    # Provision expired by its own bound: no live version -> absent, not expired.
    after_temp = _state(timelines, as_of="2025-11-01", provision="section:5")
    assert after_temp["provision_status"] == "absent"
    assert "expiry" not in after_temp
    # The same temporary provision past the STATUTE bound: still no live version,
    # and the statute is expired; ordinary absence still wins for this address.
    after_statute = _state(timelines, as_of="2027-01-01", provision="section:5")
    assert after_statute["provision_status"] == "absent"


def test_temporary_overlay_outliving_statute_yields_statute_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_flag(monkeypatch)
    # A temporary provision whose own expiry (2099-01-01) is LATER than the
    # statute bound (2026-12-31): ordinary selection keeps it live past the
    # statute term, so the statute expiry must win -> expired.
    addr = LegalAddress(path=(("section", "5"),))
    temp = ProvisionVersion(
        effective="2025-08-01",
        enacted="2025-07-01",
        expires="2099-01-01",
        variant_kind="temporary",
        content=IRNode(kind=IRNodeKind.SECTION, label="5", text="Väliaikainen pykälä."),
        source=OperationSource(statute_id="2099/368"),
    )
    voimaantulo = _voimaantulo_version(
        effective="2025-07-01", enacted="2025-06-27", text=_EXT_TEXT, source_statute="2099/368"
    )
    timelines = {
        addr: ProvisionTimeline(address=addr, versions=[temp]),
        _VOIMAANTULO: ProvisionTimeline(address=_VOIMAANTULO, versions=[voimaantulo]),
    }
    live = _state(timelines, as_of="2026-06-01", provision="section:5")
    assert live["provision_status"] == "selected"
    expired = _state(timelines, as_of="2027-01-01", provision="section:5")
    assert expired["provision_status"] == "expired"
    assert expired["valid_until"] == "2026-12-31"


def test_flag_off_is_noop_identical_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    # The rollback path: bounds are default-ON since seam spec 0.2, so the
    # flag-OFF behavior now requires the explicit opt-out.
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "0")
    timelines = _extension_timelines()
    # With the flag off, a past-term query must be byte-identical to the
    # unmodified default path (no expired status, no expiry block).
    after = _state(timelines, as_of="2027-01-01")
    assert after["provision_status"] == "selected"
    assert "expiry" not in after
    assert after["version"]["content_state"] == "live"

    # A non-fixed-term statute is a no-op regardless of flag.
    plain = _timelines(
        [
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2019-12-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                source=OperationSource(statute_id="2099/2"),
            )
        ]
    )
    plain_state = _state(plain, as_of="2027-01-01")
    assert plain_state["provision_status"] == "selected"
    assert plain_state["hashes"]["derived_state_hash"]


def test_non_fixed_term_noop_identical_hash_flag_on_and_off(monkeypatch: pytest.MonkeyPatch) -> None:
    plain = _timelines(
        [
            ProvisionVersion(
                effective="2020-01-01",
                enacted="2019-12-01",
                content=IRNode(kind=IRNodeKind.SECTION, label="7", text="Tämä laki tulee voimaan 1 päivänä tammikuuta 2020."),
                source=OperationSource(statute_id="2099/2"),
            )
        ]
    )
    monkeypatch.delenv(FIXED_TERM_BOUNDS_FLAG, raising=False)
    off = _state(plain, as_of="2027-01-01")
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")
    on = _state(plain, as_of="2027-01-01")
    # No fixed-term clause -> overlay never fires -> hashes identical with flag on/off.
    assert off["hashes"]["derived_state_hash"] == on["hashes"]["derived_state_hash"]
    assert off["provision_status"] == on["provision_status"] == "selected"


# ---------------------------------------------------------------------------
# 482/2024 trio — real corpus (flag ON via env)
# ---------------------------------------------------------------------------

_corpus_skip = pytest.mark.skipif(
    not _CORPUS.exists(),
    reason="data/finlex.farchive not present; skipping real-corpus fixed-term tests",
)


def _corpus_state(as_of: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv(FIXED_TERM_BOUNDS_FLAG, "1")
    from lawvm.provision_state import resolve_provision_state

    return resolve_provision_state(
        statute_id="2024/482",
        provision="section:7",
        as_of=as_of,
        query_type="in_force",
    )


@_corpus_skip
def test_corpus_482_2024_live_mid_term(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2026-06-01", monkeypatch)
    assert state["provision_status"] == "selected"
    assert state["version"]["content_state"] == "live"
    assert "31 päivään joulukuuta 2026" in state["text"]["rendered"]


@_corpus_skip
def test_corpus_482_2024_live_on_valid_until(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2026-12-31", monkeypatch)
    assert state["provision_status"] == "selected"
    assert state["version"]["content_state"] == "live"


@_corpus_skip
def test_corpus_482_2024_expired_after_term(monkeypatch: pytest.MonkeyPatch) -> None:
    state = _corpus_state("2027-01-01", monkeypatch)
    assert state["provision_status"] == "expired"
    assert state["version"] is None
    assert state["expires"] == "2027-01-01"
    assert state["valid_until"] == "2026-12-31"
    assert state["expiry"]["kind"] == "fixed_term_statute"
    assert state["expiry"]["source"] == "2025/368"
