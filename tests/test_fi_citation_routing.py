"""Tests for route_amendment — the citation routing pure function.

route_amendment decides whether an amendment act should be applied to a given
parent statute.  It is a pure function: string inputs → (bool, str) output, no
corpus access, no side effects.

Section layout
--------------
1. Unit tests with hand-crafted inputs — cover all four routing branches.
2. Gold-DB tests — replay real captured intermediates and verify consistency
   with the inline logic that was in place when the DB was populated.
   Skipped automatically when .cache/pipeline_gold.db is absent or empty.

Run fast (unit tests only):
    uv run pytest tests/test_fi_citation_routing.py -x -q

Run including gold-DB tests (requires populated .cache/pipeline_gold.db):
    uv run pytest tests/test_fi_citation_routing.py -x -q -m slow
"""
from __future__ import annotations

import os
import pytest

from lawvm.finland.citation_routing import route_amendment
from lawvm.finland.metadata import _normalize_johtolause_verbs
from lawvm.finland.citation_routing import (
    OP_KEYWORDS,
    extract_pending_amendment_target_id,
    johtolause_cited_target_ids,
    title_targets_pending_amendment_title,
)
from lawvm.finland.johtolause.affected_statute import (
    instrument_from_text,
    parse_affected_statute_head,
    parse_delegated_authority_lead_in,
    parse_routing_surface,
)


# ---------------------------------------------------------------------------
# Section 1: Unit tests — hand-crafted inputs
# ---------------------------------------------------------------------------


class TestJohtolauseCitedTargetIds:
    """johtolause_cited_target_ids names the statute(s) a preamble actually cites."""

    def test_routing_surface_owns_target_zone_citations(self) -> None:
        johto = (
            "muutetaan jonkin lain (100/58) 5 §, sellaisena kuin se on "
            "laissa (661/62)"
        )
        surface = parse_routing_surface(johto, source_year=1965)

        assert surface.normalized_target_ids() == ("1958/100",)
        assert surface.references_statute("1958/100")
        assert not surface.references_statute("1962/661")

    def test_routing_surface_owns_nojalla_authority_classification(self) -> None:
        johto = (
            "Opetusministerin esittelystä säädetään ammatillisista oppilaitoksista "
            "annetun lain (487/87) 60 §:n nojalla:"
        )
        surface = parse_routing_surface(johto, source_year=1992)

        assert surface.delegated_authority is not None
        assert surface.normalized_target_ids() == ("1987/487",)

    def test_nojalla_authority_prefix_does_not_hide_later_target_citation(self) -> None:
        johto = (
            "lisätään 30 päivänä huhtikuuta 1987 annetun pakkokeinolain "
            "(450/87) 5 a luvun 8 §:n nojalla, sellaisena kuin se on "
            "24 päivänä maaliskuuta 1995 annetussa laissa (402/95), "
            "esitutkinnasta ja pakkokeinoista 17 päivänä kesäkuuta 1988 "
            "annettuun asetukseen (575/88) uusi 25 a § ja sen edellä "
            "väliotsikko seuraavasti:"
        )
        surface = parse_routing_surface(johto, source_year=1995)

        assert surface.normalized_target_ids() == ("1988/575",)
        assert surface.references_statute("1988/575")
        assert not surface.references_statute("1987/450")

    def test_nojalla_comma_sellaisena_prefix_does_not_hide_later_target_citation(self) -> None:
        johto = (
            "lisätään 30 päivänä huhtikuuta 1987 annetun pakkokeinolain "
            "(450/87) 5 a luvun 8 §:n nojalla, sellaisena, kuin se on "
            "24 päivänä maaliskuuta 1995 annetussa laissa (402/95), "
            "esitutkinnasta ja pakkokeinoista 17 päivänä kesäkuuta 1988 "
            "annettuun asetukseen (575/88) uusi 25 a § seuraavasti:"
        )
        surface = parse_routing_surface(johto, source_year=1995)

        assert surface.normalized_target_ids() == ("1988/575",)
        assert surface.references_statute("1988/575")
        assert not surface.references_statute("1987/450")

    def test_nojalla_sellaisina_prefix_does_not_hide_later_target_citation(self) -> None:
        johto = (
            "muutetaan yleisestä oikeusavusta 6 päivänä helmikuuta 1998 annetun "
            "lain (104/1998) 4, 20 ja 30 §:ien sekä maksuttomasta oikeudenkäynnistä "
            "2 päivänä helmikuuta 1973 annetun lain (87/1973) 5, 5 a ja 28 §:ien "
            "nojalla sellaisina kuin niistä 5 ja 5 a § ovat laissa 105/1998, "
            "oikeusavusta ja maksuttomasta oikeudenkäynnistä 6 helmikuuta 1998 "
            "annetun asetuksen (358/1998) 2 § 1 ja 2 momentit, 5 § ja 6 § "
            "seuraavasti:"
        )
        surface = parse_routing_surface(johto, source_year=2001)

        assert surface.normalized_target_ids() == ("1998/358",)
        assert surface.references_statute("1998/358")
        assert not surface.references_statute("1998/104")
        assert not surface.references_statute("1973/87")

    def test_dropped_digit_typo_surfaces_cited_statute(self) -> None:
        # 1965/301 johtolause: a dropped digit makes rakennuslaki (370/58)
        # read as (70/58). The helper must surface the 1958/70 it cites so the
        # skip diagnostic can show it against the real parent 1958/370.
        johto = (
            "Eduskunnan päätöksen mukaisesti muutetaan 16 päivänä elokuuta 1958 "
            "annetun rakennuslain (70/58) 11 §:n 2 momentti"
        )
        assert johtolause_cited_target_ids(johto, 1965) == ["1958/70"]

    def test_four_digit_year_citation(self) -> None:
        johto = "muutetaan jonkin lain (1234/2001) 1 §"
        assert johtolause_cited_target_ids(johto, 2010) == ["2001/1234"]

    def test_four_digit_statute_number_citation(self) -> None:
        johto = "muutetaan jonkin lain (1597/1992) 1 §"
        assert johtolause_cited_target_ids(johto, 1997) == ["1992/1597"]

    def test_prior_amendment_citations_after_sellaisena_excluded(self) -> None:
        # Citations after "sellaisena kuin se on" are prior-amendment refs,
        # not the target statute — they must not be reported.
        johto = (
            "muutetaan jonkin lain (100/58) 5 §, sellaisena kuin se on laissa (661/62)"
        )
        assert johtolause_cited_target_ids(johto, 1965) == ["1958/100"]

    def test_prior_amendment_citations_after_comma_sellaisena_excluded(self) -> None:
        johto = (
            "muutetaan kirkkojärjestyksen 11 §:n 1 momentti sellaisena, kuin "
            "pykälä muutettiin marraskuun 28 päivänä 2017 (1160/2017) sekä "
            "kirkkojärjestyksen 12, 17, 19, 21 sekä 27 § seuraavasti:"
        )
        surface = parse_routing_surface(johto, source_year=2020)

        assert surface.normalized_target_ids() == ()
        assert surface.references_statute("2007/174")

    def test_typo_selaisena_kuin_still_cuts_provenance_citation(self) -> None:
        # 1978/676 has source typo "selaisena kuin"; the following 323/64 is
        # version provenance for the target item, not the amended parent act.
        johto = (
            "muutetaan yleisistä teistä 21 päivänä toukokuuta 1954 annetun lain "
            "11 §:n 2 momentin 1 kohta selaisena kuin se on 12 päivänä "
            "kesäkuuta 1964 annetussa laissa (323/64), näin kuuluvaksi:"
        )
        surface = parse_routing_surface(johto, source_year=1978)

        assert surface.normalized_target_ids() == ()
        assert surface.references_statute("1954/243")
        should_apply, reason = route_amendment(
            johto,
            "",
            johto,
            "1954/243",
            "1978/676",
            source_title="Laki yleisistä teistä annetun lain 11 §:n muuttamisesta",
            parent_title="Laki yleisistä teistä",
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_no_citation_returns_empty(self) -> None:
        assert johtolause_cited_target_ids("muutetaan 5 § seuraavasti:", 1965) == []


class TestInstrumentFromText:
    """Morphology-driven coarse instrument-kind classification.

    Replaces the old suffix-substring regex that hand-listed a few inflected
    forms (``asetuk(?:sen|sesta|seen|sessa)`` ...) and so inherited the
    consonant-gradation bug class: any inflection outside the hand-list (and any
    laki form beyond ``laki``/``lain``) was silently misclassified as unknown.
    """

    def test_genitive_compound_heads_classify(self) -> None:
        # Genitives the OLD regex already handled — must not regress.
        assert instrument_from_text("rakennuslain") == "laki"
        assert instrument_from_text("valtion eläkelain") == "laki"
        assert instrument_from_text("eläkeasetuksen") == "asetus"
        assert instrument_from_text("sisäasiainministeriön päätöksen") == "päätös"

    def test_nominative_parent_titles_classify(self) -> None:
        # ``instrument_from_text`` is also called on clean nominative parent
        # titles in the typo-rewrite gate.
        assert instrument_from_text("Rakennuslaki") == "laki"
        assert instrument_from_text("Eroraha-asetus") == "asetus"
        assert instrument_from_text("Tapaturmavakuutuslaki") == "laki"

    def test_inflections_beyond_old_handlist_now_classify(self) -> None:
        # REGRESSION GUARD: every form here was silently "" under the old
        # suffix-substring regex (`'asetus' not in 'asetuksella'`, and laki had
        # only `laki|lain`).  The morphology paradigm covers the full case set.
        assert instrument_from_text("asetuksella") == "asetus"  # adessive
        assert instrument_from_text("asetukseen") == "asetus"  # illative
        assert instrument_from_text("asetuksia") == "asetus"  # plural partitive
        assert instrument_from_text("tuloverolakiin") == "laki"  # illative compound
        assert instrument_from_text("lakiin") == "laki"  # illative
        assert instrument_from_text("tapaturmavakuutuslaissa") == "laki"  # inessive
        assert instrument_from_text("lakeja") == "laki"  # plural partitive
        assert instrument_from_text("lakien") == "laki"  # plural genitive

    def test_trailing_postmodifier_does_not_hide_head(self) -> None:
        # The affected-head capture can leak a trailing section reference; the
        # right-to-left scan still finds the real instrument head.
        assert instrument_from_text("lain 5 §:n") == "laki"
        assert instrument_from_text("tieliikennelain 66 §:n 5 momentin") == "laki"

    def test_trailing_head_wins_over_embedded_referent(self) -> None:
        # In an "X-sta annetun Y" implementation-law phrase, the affected statute
        # is the TRAILING instrument that bears the citation, not an earlier
        # instrument that is merely the referent of ``soveltamisesta``.  The old
        # left-priority regex picked the wrong (embedded) kind here.
        assert (
            instrument_from_text("asetuksen soveltamisesta annetun liikenneministeriön päätöksen")
            == "päätös"
        )
        assert (
            instrument_from_text("neuvoston asetuksen soveltamisesta annetun lain") == "laki"
        )

    def test_out_of_scope_and_unknown_heads_fail_loud(self) -> None:
        # Instrument families outside the laki/asetus/päätös coarse set, and
        # genuinely unanalyzable heads, return the honest "" — never a guess.
        assert instrument_from_text("työjärjestyksen") == ""
        assert instrument_from_text("ohjesäännön") == ""
        assert instrument_from_text("perintökaaren") == ""
        assert instrument_from_text("") == ""
        assert instrument_from_text("muutetaan 5 § seuraavasti") == ""


class TestRouteAmendmentNoGuard:
    """Cases where the guard condition is not met — return apply=True immediately."""

    def test_empty_parent_id(self) -> None:
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "", "2012/715")
        assert result == (True, "no_guard_needed")

    def test_empty_amendment_id(self) -> None:
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "2009/953", "")
        assert result == (True, "no_guard_needed")

    def test_non_numeric_amendment_year(self) -> None:
        # amendment_id year part is not purely digits → skip guard
        result = route_amendment("muutetaan 3 §", "", "muutetaan 3 §", "2009/953", "abc/715")
        assert result == (True, "no_guard_needed")

    def test_malformed_amendment_id_without_num_declines(self) -> None:
        """An amendment id missing its NUM part (no ``/``) must decline the guard.

        Regression for the swallowed-IndexError fallback: ``"2012"`` passes the
        leading guard (truthy, ``"2012".isdigit()``) but ``split("/")[1]`` would
        raise IndexError. The old code caught it, set the NUMs to ``""``, and ran
        the citation match anyway — scanning the johtolause against a malformed
        parent id. Here the johtolause cites a *different* statute (999/2020),
        so the empty-string path would have produced a spurious
        ``citation_mismatch_skip`` (apply=False) instead of cleanly declining.
        The fix declines to ``no_guard_needed`` (apply=True).
        """
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        # amendment_id "2012" has no NUM part → not a well-formed YEAR/NUM tuple.
        result = route_amendment(johto_norm, "", johto_raw, "2009/953", "2012")
        assert result == (True, "no_guard_needed")

    def test_malformed_parent_id_without_num_declines(self) -> None:
        """A parent id missing its NUM part (no ``/``) must decline the guard.

        Without the fix, ``parent_num`` becomes ``""`` and the routing match runs
        against the malformed parent id ``"2009"``; declining is the only correct
        behavior because there is no real parent NUM to match against.
        """
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        result = route_amendment(johto_norm, "", johto_raw, "2009", "2012/715")
        assert result == (True, "no_guard_needed")


class TestRouteAmendmentReferencesParent:
    """Cases where johtolause references the parent statute → apply."""

    def test_explicit_statute_ref_matches(self) -> None:
        # johto contains (280/66), parent is 1966/280
        johto_raw = (
            "kumotaan 20 päivänä toukokuuta 1966 annetun valtion eläkelain "
            "( 280/66 ) 8§:n 6 momentti"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1966/280", "1989/103"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_four_digit_year_ref_matches(self) -> None:
        # johto contains (1479/1993), parent is 1993/1479
        johto_raw = "muutetaan 30 päivänä joulukuuta 1993 annetun lain ( 1479/1993 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1993/1479", "2010/50"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_empty_citation_guard_johto_defaults_to_apply(self) -> None:
        # When no preamble text is available, routing defaults to apply.
        should_apply, reason = route_amendment(
            "", "", "muutetaan 3 §", "2009/953", "2012/715"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_present_tense_lisaa_counts_as_operative_keyword(self) -> None:
        """Finite present ``lisää`` must count as an operative keyword.

        Regression for Verohallinto clauses like ``Verohallinto lisää ... uuden
        4 a §:n``. The replay ingress pre-check uses ``OP_KEYWORDS`` before
        Phase 2; omitting ``lisää`` caused those amendments to be silently
        treated as non-operative and skipped.
        """
        assert "lisää" in OP_KEYWORDS

    def test_nojalla_authority_prefix_with_later_parent_target_applies(self) -> None:
        johto_raw = (
            "lisätään 30 päivänä huhtikuuta 1987 annetun pakkokeinolain "
            "(450/87) 5 a luvun 8 §:n nojalla, sellaisena kuin se on "
            "24 päivänä maaliskuuta 1995 annetussa laissa (402/95), "
            "esitutkinnasta ja pakkokeinoista 17 päivänä kesäkuuta 1988 "
            "annettuun asetukseen (575/88) uusi 25 a § ja sen edellä "
            "väliotsikko seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1988/575",
            "1995/407",
            source_title="Asetus esitutkinnasta ja pakkokeinoista annetun asetuksen muuttamisesta",
            parent_title="Asetus esitutkinnasta ja pakkokeinoista",
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_leading_prior_amendment_repeal_does_not_hide_base_statute_ops(self) -> None:
        johto_raw = (
            "kumotaan (579/1994), muutetaan lain nimike, 1 ja 2 §, "
            "3 §:n 1 momentti, 4 ja 5 §, 6 §:n 3 momentti, 7 §, "
            "8 §:n 1 ja 2 momentti, 9 §, 12 §:n 1 momentti ja 13 §,"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1992/1597",
            "1997/419",
            source_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten koulutuksen "
                "ja ammatillisen harjoittelun tunnustamisesta annetun lain "
                "muuttamisesta"
            ),
            parent_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten "
                "tutkintotodistusten tunnustamisesta"
            ),
        )

        assert should_apply is True
        assert reason == "leading_meta_repeal_then_parent_ops"

    def test_leading_prior_amendment_repeal_recovery_rejects_foreign_rest_citation(self) -> None:
        johto_raw = (
            "kumotaan (579/1994), muutetaan toisen lain (999/1992) "
            "1 § seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1992/1597",
            "1997/419",
            source_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten koulutuksen "
                "ja ammatillisen harjoittelun tunnustamisesta annetun lain "
                "muuttamisesta"
            ),
            parent_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten "
                "tutkintotodistusten tunnustamisesta"
            ),
        )

        assert should_apply is False
        assert reason == "citation_mismatch_skip"


class TestRouteAmendmentNumCollisionSkip:
    """NUM-collision: amendment and parent share the same statute number
    but the johtolause cites a different statute."""

    def test_same_num_different_year(self) -> None:
        # amendment 1966/611, parent 1960/611 — same NUM=611
        # johto cites a completely different statute (72/56)
        johto_raw = (
            "muutetaan 20 päivänä heinäkuuta 1961 annetun merimieseläkelain "
            "( 72/56 ) 3 §"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1960/611", "1966/611"
        )
        assert should_apply is False
        assert reason == "num_collision_skip"

    def test_num_collision_numeric_comparison(self) -> None:
        # Both have num=500, different years, johto explicitly cites (999/2010)
        # which is a different statute — parenthesized citation required for detection.
        johto_raw = "muutetaan joulukuuta 2010 annetun lain ( 999/2010 ) 5 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/500"
        )
        assert should_apply is False
        assert reason == "num_collision_skip"


class TestRouteAmendmentCitationMismatchSkip:
    """johtolause cites a different statute (not NUM-collision)."""

    def test_explicit_foreign_citation(self) -> None:
        # johto cites (280/66) but parent is 1966/611
        johto_raw = (
            "kumotaan 20 päivänä toukokuuta 1966 annetun valtion eläkelain "
            "( 280/66 ) 8§:n 6 momentti"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1966/611", "1989/103"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_nojalla_authority_clause_has_distinct_skip_reason(self) -> None:
        johto_raw = (
            "Opetusministerin esittelystä säädetään ammatillisista oppilaitoksista "
            "annetun lain (487/87) 60 §:n nojalla:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1987/491",
            "1992/1314",
            source_title="Asetus ammatillisista oppilaitoksista",
            parent_title="Asetus ammatillisista oppilaitoksista",
        )
        assert should_apply is False
        assert reason == "delegated_authority_nojalla_skip"
        assert parse_delegated_authority_lead_in(johto_raw) is not None

    def test_nojalla_maarannyt_authority_clause_preempts_typo_rewrite(self) -> None:
        johto_raw = (
            "Valtioneuvosto on tapaturmavakuutuslaissa säädettyjen markkamäärien "
            "korottamisesta 20 päivänä elokuuta 1948 annetun lain (610/48) nojalla "
            "sosiaaliministeriön esittelystä määrännyt,"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1948/608",
            "1950/642",
            source_title="Valtioneuvoston päätös tapaturmavakuutuslaissa säädettyjen markkamäärien korottamisesta",
            parent_title="Tapaturmavakuutuslaki",
            parent_issue_date="1948-08-20",
        )
        assert should_apply is False
        assert reason == "delegated_authority_nojalla_skip"
        assert parse_delegated_authority_lead_in(johto_raw) is not None

    def test_corrupt_citation_is_accepted_when_affected_head_matches_parent_metadata(self) -> None:
        johto_raw = (
            "Eduskunnan päätöksen mukaisesti muutetaan 16 päivänä elokuuta 1958 "
            "annetun rakennuslain (70/58) 11 §:n 2 momentti"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1958/370",
            "1965/301",
            source_title="Laki rakennuslain muuttamisesta",
            parent_title="Rakennuslaki",
            parent_issue_date="1958-08-16",
        )
        assert should_apply is True
        assert reason == "citation_typo_rewrite_parent_validated"
        head = parse_affected_statute_head(johto_raw)
        assert head is not None
        assert head.issue_date is not None and head.issue_date.isoformat() == "1958-08-16"
        assert head.instrument == "laki"

    def test_corrupt_citation_with_valtioneuvoston_paatos_head_matches_parent_metadata(self) -> None:
        johto_raw = (
            "muutetaan ylimääräisistä taiteilijaeläkkeistä 24 päivänä tammikuuta "
            "1974 annetun valtioneuvoston päätöksen (75/75) 3 §:n 1 momentin "
            "ja 5 §:n näin kuuluviksi:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1974/75",
            "1984/929",
            source_title=(
                "Valtioneuvoston päätös ylimääräisistä taiteilijaeläkkeistä "
                "annetun valtioneuvoston päätöksen 3 ja 5 §:n muuttamisesta"
            ),
            parent_title="Valtioneuvoston päätös ylimääräisistä taiteilijaeläkkeistä.",
            parent_issue_date="1974-01-24",
        )

        assert should_apply is True
        assert reason == "citation_typo_rewrite_parent_validated"
        head = parse_affected_statute_head(johto_raw)
        assert head is not None
        assert head.title_phrase == (
            "ylimääräisistä taiteilijaeläkkeistä annetun valtioneuvoston päätöksen"
        )
        assert head.issue_date is not None and head.issue_date.isoformat() == "1974-01-24"
        assert head.instrument == "päätös"

    def test_title_before_date_corrupt_citation_uses_amendment_title_metadata(self) -> None:
        johto_raw = (
            "kumotaan Euroopan talousalueen valtioiden kansalaisten koulutuksen "
            "ja ammatillisen harjoittelun tunnustamisesta 30 päivänä joulukuuta "
            "1992 annetun lain (159/1992) 3 §:n 3 momentti, sellaisena kuin se "
            "on 28 päivänä kesäkuuta 1994 annetussa laissa (579/1994), muutetaan "
            "lain nimike, 1 ja 2 §"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1992/1597",
            "1997/419",
            source_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten koulutuksen "
                "ja ammatillisen harjoittelun tunnustamisesta annetun lain muuttamisesta"
            ),
            parent_title=(
                "Laki Euroopan talousalueen valtioiden kansalaisten "
                "tutkintotodistusten tunnustamisesta"
            ),
            parent_issue_date="1992-12-30",
        )
        assert should_apply is True
        assert reason == "citation_typo_rewrite_parent_validated"
        head = parse_affected_statute_head(johto_raw)
        assert head is not None
        assert head.title_phrase == (
            "Euroopan talousalueen valtioiden kansalaisten koulutuksen "
            "ja ammatillisen harjoittelun tunnustamisesta annetun lain"
        )
        assert head.issue_date is not None and head.issue_date.isoformat() == "1992-12-30"
        assert head.instrument == "laki"

    def test_same_day_sibling_title_mismatch_blocks_typo_rewrite(self) -> None:
        johto_raw = (
            "kumotaan 13 päivänä kesäkuuta 1929 annetun avioliittolain "
            "(234/29) 55 §"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1929/235",
            "1987/411",
            source_title="Laki avioliittolain muuttamisesta",
            parent_title="Laki avioliittolain voimaanpanosta",
            parent_issue_date="1929-06-13",
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_amendment_of_amendment_head_blocks_typo_rewrite(self) -> None:
        johto_raw = (
            "muutetaan 17 päivänä helmikuuta 1989 annetun eroraha-asetuksen "
            "muuttamisesta annetun asetuksen (191/89) voimaantulosäännös"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1987/726",
            "1992/1346",
            source_title="Asetus eroraha-asetuksen muuttamisesta annetun asetuksen muuttamisesta",
            parent_title="Eroraha-asetus",
            parent_issue_date="1987-01-01",
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_meta_repeal_pattern(self) -> None:
        # johto contains "kumotaan ... muuttamisesta ... annetun lain (NUM" pattern
        # → this is a meta-repeal targeting a prior amendment act, not the parent
        meta_johto = (
            "kumotaan eräiden lakien muuttamisesta annetun lain ( 123/2010 ) 3 §"
        )
        meta_norm = _normalize_johtolause_verbs(meta_johto)
        should_apply, reason = route_amendment(
            meta_norm, "", meta_johto, "2008/500", "2012/600"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_pending_amendment_of_parent_title_is_distinct_skip_family(self) -> None:
        johto_raw = (
            "muutetaan valmiuslain muuttamisesta annetun lain ( 631/2022 ) "
            "88 ja 126 § seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2011/1552",
            "2022/1188",
            source_title="Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta",
            parent_title="Valmiuslaki",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2022/1188",
            "Laki valmiuslain muuttamisesta annetun lain 88 ja 126 §:n muuttamisesta",
            "Valmiuslaki",
        ) == "2022/631"

    def test_pending_amendment_of_parent_title_handles_section_scoped_form(self) -> None:
        johto_raw = (
            "muutetaan valmiuslain 109 §:n muuttamisesta annetun lain "
            "( 1233/2020 ) 109 §:n 1 momentti seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2011/1552",
            "2022/708",
            source_title="Laki valmiuslain 109 §:n muuttamisesta annetun lain muuttamisesta",
            parent_title="Valmiuslaki",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2022/708",
            "Laki valmiuslain 109 §:n muuttamisesta annetun lain muuttamisesta",
            "Valmiuslaki",
        ) == "2020/1233"

    def test_pending_amendment_of_parent_title_handles_annetun_lain_parent_titles(self) -> None:
        johto_raw = (
            "muutetaan yleisestä asumistuesta annetun lain muuttamisesta annetun lain "
            "( 1533/2016 ) 29 ja 41 § seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "2014/938",
            "2017/480",
            source_title="Laki yleisestä asumistuesta annetun lain muuttamisesta annetun lain 29 ja 41 §:n muuttamisesta",
            parent_title="Laki yleisestä asumistuesta",
        )
        assert should_apply is False
        assert reason == "pending_amendment_of_parent_skip"
        assert extract_pending_amendment_target_id(
            johto_raw,
            "2017/480",
            "Laki yleisestä asumistuesta annetun lain muuttamisesta annetun lain 29 ja 41 §:n muuttamisesta",
            "Laki yleisestä asumistuesta",
        ) == "2016/1533"

    def test_pending_amendment_title_match_handles_renamed_base_statute(self) -> None:
        assert title_targets_pending_amendment_title(
            "Laki ydinvastuulain muuttamisesta annetun lain muuttamisesta",
            "Laki ydinvastuulain muuttamisesta",
        )
        assert not title_targets_pending_amendment_title(
            "Laki ydinvastuulain muuttamisesta annetun lain muuttamisesta",
            "Atomivastuulaki",
        )


class TestRouteAmendmentSec1Fallback:
    """sec1 fallback: terse preamble with no op keywords, but sec1 cites parent."""

    def test_sec1_cites_parent_rescues_terse_preamble(self) -> None:
        # Preamble is terse (no citations, no op keywords) — mismatch from preamble alone.
        # sec1 does cite the parent → should apply.
        johto_terse = "Ympäristöministerin esittelystä säädetään:"
        johto_norm = _normalize_johtolause_verbs(johto_terse)
        sec1_text = "muutetaan rakennuslain ( 370/1958 ) 3 § seuraavasti:"
        sec1_norm = _normalize_johtolause_verbs(sec1_text)
        should_apply, reason = route_amendment(
            johto_norm, sec1_norm, johto_terse, "1958/370", "1993/949"
        )
        assert should_apply is True
        assert reason == "references_parent"

    def test_sec1_not_used_when_preamble_has_op_keywords(self) -> None:
        # Preamble has op keywords but no matching citation — sec1 is NOT consulted
        # (to avoid omnibus-repealer bleed). Result: citation_mismatch_skip.
        johto_with_ops = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_with_ops)
        sec1_cites_parent = "muutetaan ( 370/1958 ) 5 §"
        sec1_norm = _normalize_johtolause_verbs(sec1_cites_parent)
        should_apply, reason = route_amendment(
            johto_norm, sec1_norm, johto_with_ops, "1958/370", "2021/400"
        )
        # Preamble cites 999/2020 (not 370/1958), has op keywords → sec1 NOT consulted
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_empty_sec1_no_rescue(self) -> None:
        # Preamble fails citation check, sec1 is empty → still skip
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "1958/370", "2021/400"
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"


class TestRouteAmendmentTitleMismatch:
    """Title-based override: amendment title explicitly names a different statute."""

    def test_single_target_title_mismatch_does_not_reject_without_vts_context(self) -> None:
        johto_raw = (
            "kumotaan rikoslain 7 luvun 5 §:n 2 momentti, sellaisena kuin se on "
            "19 päivänä heinäkuuta 1974 annetussa laissa ( 613/74 ), sekä muutetaan "
            "rikoslain 2 luvun 2 §:n 1 momentti ja 6 luku, seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)

        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1953/317",
            "1976/466",
            source_title="Laki rikoslain muuttamisesta",
            parent_title="Laki vaarallisten rikoksenuusijain eristämisestä",
        )

        assert should_apply is True
        assert reason == "references_parent"

    def test_single_target_title_match_preserves_target_route(self) -> None:
        johto_raw = (
            "kumotaan rikoslain 7 luvun 5 §:n 2 momentti, sellaisena kuin se on "
            "19 päivänä heinäkuuta 1974 annetussa laissa ( 613/74 ), sekä muutetaan "
            "rikoslain 2 luvun 2 §:n 1 momentti ja 6 luku, seuraavasti:"
        )
        johto_norm = _normalize_johtolause_verbs(johto_raw)

        should_apply, reason = route_amendment(
            johto_norm,
            "",
            johto_raw,
            "1889/39",
            "1976/466",
            source_title="Laki rikoslain muuttamisesta",
            parent_title="Rikoslaki",
        )

        assert should_apply is True
        assert reason == "references_parent"

    def test_title_mismatch_overrides_citation_match(self) -> None:
        # johto references parent correctly, but title says it targets laki X (not asetus Y).
        # Use a concrete title pattern that _title_explicitly_targets_other_statute recognises.
        johto_raw = "muutetaan lain ( 500/2005 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        # source_title matches "laki <name> annetun lain muuttamisesta" but
        # names a different kind from parent (laki vs asetus)
        source_title = "laki luottolaitostoiminnasta annetun lain muuttamisesta"
        parent_title = "asetus luottolaitostoiminnasta"
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/300",
            source_title=source_title,
            parent_title=parent_title,
        )
        assert should_apply is False
        assert reason == "citation_mismatch_skip"

    def test_no_title_provided_does_not_skip(self) -> None:
        # When source/parent titles are empty, title check is no-op → apply if refs match.
        johto_raw = "muutetaan lain ( 500/2005 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        should_apply, reason = route_amendment(
            johto_norm, "", johto_raw, "2005/500", "2010/300",
            source_title="",
            parent_title="",
        )
        assert should_apply is True
        assert reason == "references_parent"


class TestRouteAmendmentReturnType:
    """Return type is always (bool, str) with a known reason string."""

    _KNOWN_REASONS = frozenset({
        "references_parent",
        "citation_typo_rewrite_parent_validated",
        "leading_meta_repeal_then_parent_ops",
        "delegated_authority_nojalla_skip",
        "pending_amendment_of_parent_skip",
        "no_guard_needed",
        "num_collision_skip",
        "citation_mismatch_skip",
    })

    def test_apply_case_returns_bool_str(self) -> None:
        result = route_amendment("", "", "", "2009/953", "2012/715")
        assert isinstance(result, tuple) and len(result) == 2
        apply, reason = result
        assert isinstance(apply, bool)
        assert reason in self._KNOWN_REASONS

    def test_skip_case_returns_bool_str(self) -> None:
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        result = route_amendment(johto_norm, "", johto_raw, "1958/370", "2021/400")
        assert isinstance(result, tuple) and len(result) == 2
        apply, reason = result
        assert isinstance(apply, bool)
        assert reason in self._KNOWN_REASONS

    def test_skip_implies_false(self) -> None:
        johto_raw = "muutetaan ( 999/2020 ) 3 §"
        johto_norm = _normalize_johtolause_verbs(johto_raw)
        apply, reason = route_amendment(johto_norm, "", johto_raw, "1958/370", "2021/400")
        if "skip" in reason:
            assert apply is False

    def test_no_guard_implies_true(self) -> None:
        apply, reason = route_amendment("", "", "", "", "2012/715")
        assert reason == "no_guard_needed"
        assert apply is True


# ---------------------------------------------------------------------------
# Section 2: Gold-DB consistency tests
#
# These tests verify that route_amendment produces results consistent with
# the inline routing logic that was in place when the captures were recorded.
# They are skipped automatically when the gold DB is absent or empty.
# ---------------------------------------------------------------------------

_DB_PATH = ".cache/pipeline_gold.db"
_COLLECT_SLOW_GOLD = os.environ.get("LAWVM_PYTEST_COLLECT_SLOW_GOLD") == "1"
_GOLD_AVAILABLE = False
_GOLD_CAPTURES: list = []

if _COLLECT_SLOW_GOLD and os.path.exists(_DB_PATH):
    try:
        from lawvm.core.pipeline_capture import AmendmentCapture, CaptureStore
        _store = CaptureStore(_DB_PATH)
        _stats = _store.stats()
        if _stats["total_amendments"] > 0:
            for _sid in _store.statutes():
                _GOLD_CAPTURES.extend(_store.load(_sid))
            _GOLD_AVAILABLE = True
    except Exception:
        pass  # DB exists but unreadable — skip gracefully


def _gold_id(cap: "AmendmentCapture") -> str:
    return f"{cap.statute_id}/{cap.amendment_id}"


@pytest.mark.slow
@pytest.mark.skipif(not _GOLD_AVAILABLE, reason="pipeline_gold.db absent or empty")
@pytest.mark.parametrize("cap", _GOLD_CAPTURES, ids=_gold_id)
def test_route_amendment_matches_captured_citation_action(cap: "AmendmentCapture") -> None:
    """route_amendment reproduces the captured citation_action for every amendment.

    The gold DB was populated by the inline routing logic.  This test verifies
    that the extracted function is behaviourally identical.

    Mapping from legacy citation_action values to route_amendment reasons:
      "pass"                    → should_apply=True
      "skip_num_collision"      → reason="num_collision_skip"
      "skip_citation_mismatch"  → reason="citation_mismatch_skip"
      ""  (not captured)        → any True result is acceptable
    """
    from lawvm.finland.metadata import _normalize_johtolause_verbs as _nvn

    johto_norm = _nvn(cap.preamble_raw)
    should_apply, reason = route_amendment(
        citation_guard_johto=johto_norm,
        citation_guard_sec1="",   # gold DB does not store citation_guard_sec1
        johto=cap.preamble_normalized,
        parent_id=cap.statute_id,
        amendment_id=cap.amendment_id,
        source_title=cap.source_title,
        parent_title="",          # gold DB does not store parent title
    )

    captured = cap.citation_action
    if not captured or captured == "pass":
        assert should_apply is True, (
            f"{cap.statute_id}/{cap.amendment_id}: expected apply=True "
            f"(captured action={captured!r}), got reason={reason!r}"
        )
    elif captured == "skip_num_collision":
        assert reason == "num_collision_skip" and not should_apply, (
            f"{cap.statute_id}/{cap.amendment_id}: expected num_collision_skip, "
            f"got ({should_apply}, {reason!r})"
        )
    elif captured == "skip_citation_mismatch":
        assert "mismatch" in reason and not should_apply, (
            f"{cap.statute_id}/{cap.amendment_id}: expected citation_mismatch_skip, "
            f"got ({should_apply}, {reason!r})"
        )
