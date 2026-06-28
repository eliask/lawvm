from __future__ import annotations

import datetime as dt

import lxml.etree as etree

from lawvm.core.ir import LegalAddress
from lawvm.core.semantic_types import FacetKind
from lawvm.finland.metadata import (
    TemporarySectionExpiryOverride,
    _amendment_effective_date_with_step,
    _amendment_expiry_date,
    _chapter_commencement_effective_overrides,
    _commencement_expiry_override,
    _expiry_date_precedes_effective_date,
    _infer_expiry_date_from_temporary_payload_text,
    _normalize_fi_parse_text,
    _section_commencement_effective_override,
    _section_subsection_application_commencement_effective_override,
    _section_subsection_commencement_effective_override,
    _temporary_provision_expiry_overrides,
    _temporary_section_applicability_windows,
    _temporary_section_expiry_overrides,
    _temporary_section_expiry_override,
)


def _assert_section_expiry_override(
    override: TemporarySectionExpiryOverride,
    *,
    target_mid: str,
    labels: set[str],
    expiry: dt.date,
) -> None:
    assert override.target_mid == target_mid
    assert override.labels == labels
    assert override.expiry == expiry


def _tree(text: str) -> etree._Element:
    return etree.fromstring(f"<doc>{text}</doc>".encode("utf-8"))


def test_normalize_fi_parse_text_em_dash_and_spaces() -> None:
    """_normalize_fi_parse_text must map em-dash and horizontal-space variants."""
    raw = "43\u00a0a\u201443\u00a0c\u2009\xa7"   # "43 a—43 c§" with NBSP and thin space
    result = _normalize_fi_parse_text(raw)
    assert '\u2014' not in result, "em-dash must be normalised"
    assert '\u00a0' not in result, "NBSP must be normalised"
    assert '\u2009' not in result, "thin space must be normalised"
    assert '\u2013' in result, "en-dash must be present after normalisation"
    assert result == "43 a\u201343 c \xa7".replace('\xa0', ' ')


def test_amendment_expiry_date_matches_whole_act_expiry_only() -> None:
    tree = _tree(
        "Tämä asetus tulee voimaan 3 päivänä huhtikuuta 2020 ja on voimassa 31 "
        "päivään joulukuuta 2020."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is not None
    assert expiry.isoformat() == "2020-12-31"


def test_amendment_expiry_date_section_scoped_en_dash() -> None:
    """_amendment_expiry_date returns the expiry date for section-scoped patterns.

    Regression: 2012/991 amending 1996/931 contains
    "Lain 43 a\u2014 43 c\u2009\u00a7 ovat voimassa 31 päivään joulukuuta 2016."
    This has no whole-act "Tämä laki on voimassa" clause, so the old regex
    returned None and the sections never expired.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2013. "
        "Lain 43 a\u201443 c\u2009\xa7 ovat voimassa 31 päivään joulukuuta 2016."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is not None
    assert expiry.isoformat() == "2016-12-31"


def test_amendment_expiry_date_section_scoped_ja_connector() -> None:
    """_amendment_expiry_date handles 'ja' connective in section-scoped expiry."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020. "
        "Lain 2 ja 5 \xa7 ovat voimassa 31 päivään joulukuuta 2022."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is not None
    assert expiry.isoformat() == "2022-12-31"


def test_amendment_expiry_date_whole_act_still_works() -> None:
    """Whole-act expiry pattern still returns correctly after extending the function."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2020 ja on voimassa "
        "31 päivään joulukuuta 2020."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is not None
    assert expiry.isoformat() == "2020-12-31"


def test_amendment_expiry_date_does_not_cross_sentence_boundary() -> None:
    """Pattern 1 must not match 'on voimassa' from a DIFFERENT sentence.

    Regression: 2009/315 amending 2004/421 has voimaantulo text:
      "Tämä asetus tulee voimaan 15 päivänä toukokuuta 2009.
       Puutiaisaivotulehdusrokotusta koskeva 2 a § on voimassa 31 päivään joulukuuta 2010."

    Before the fix, re.DOTALL caused Pattern 1 to match across the newline,
    returning 2010-12-31 as a whole-act expiry.  This incorrectly tagged ALL
    sections from 2009/315 as VÄLIAIKAISESTI (temporary), reverting permanent
    changes to sections 1, 2, 4, and 5 of 2004/421 after 2010-12-31.
    The function must return None for this input (only 2 a § is temporary).
    """
    tree = _tree(
        "Tämä asetus tulee voimaan 15 päivänä toukokuuta 2009.\n"
        "Puutiaisaivotulehdusrokotusta koskeva 2 a \xa7 on voimassa 31 päivään joulukuuta 2010."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        f"Pattern 1 must not cross sentence boundary; got {expiry!r} (expected None)"
    )


def test_amendment_expiry_date_two_sentence_bare_subject_is_target_reference() -> None:
    """A follow-on sentence with a bare act-word subject must NOT parse.

    1992/884 (amending 1990/912) states:
      "Tämä asetus tulee voimaan 1 päivänä lokakuuta 1992.
       Asetus on voimassa 31 päivään joulukuuta 1992."

    Like "Laki on voimassa vuoden 1993 loppuun" in 1992/272 (see
    test_amendment_expiry_date_vuoden_loppuun_not_matched_for_target_statute),
    the bare-subject sentence states the TARGET statute's validity, not the
    amendment's. Stamping the amendment temporary from it erroneously reverts
    its ops at the target's expiry. Whole-act amendment expiry requires the
    explicit "Tämä ..." subject in the same sentence as "on voimassa".
    """
    tree = _tree(
        "Tämä asetus tulee voimaan 1 päivänä lokakuuta 1992. "
        "Asetus on voimassa 31 päivään joulukuuta 1992."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        f"bare-subject follow-on sentence must not set amendment expiry; got {expiry!r}"
    )


def test_temporary_section_expiry_override_parses_direct_source_clause() -> None:
    tree = _tree(
        "Tämä asetus tulee voimaan 19 päivänä lokakuuta 2020. "
        "Asetuksen 5, 8 b, 11 ja 12 § ovat voimassa 31 päivään joulukuuta 2020."
    )
    override = _temporary_section_expiry_override(tree, "2020/697")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2020/697",
        labels={"5", "8b", "11", "12"},
        expiry=dt.date(2020, 12, 31),
    )


def test_temporary_section_expiry_override_accepts_laki_wording() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 19 päivänä lokakuuta 2020. "
        "Lain 5, 8 b, 11 ja 12 § ovat voimassa 31 päivään joulukuuta 2020."
    )
    override = _temporary_section_expiry_override(tree, "2020/697")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2020/697",
        labels={"5", "8b", "11", "12"},
        expiry=dt.date(2020, 12, 31),
    )


def test_temporary_section_expiry_override_parses_subsection_scoped_sunset() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2023. "
        "Lain 51 §:n 5 momentti on voimassa 31 päivään joulukuuta 2023."
    )

    override = _temporary_section_expiry_override(tree, "2022/1151")

    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2022/1151",
        labels={"51"},
        expiry=dt.date(2023, 12, 31),
    )


def test_temporary_section_expiry_override_parses_subsection_scoped_year_end_sunset() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2011. "
        "Lain 11 §:n 3 momentti on voimassa vuoden 2014 loppuun."
    )

    override = _temporary_section_expiry_override(tree, "2010/1172")

    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2010/1172",
        labels={"11"},
        expiry=dt.date(2014, 12, 31),
    )


def test_temporary_provision_expiry_overrides_parse_mixed_heading_and_moment_scope() -> None:
    tree = _tree(
        "Tämä asetus tulee voimaan 26 päivänä toukokuuta 2025. "
        "Asetuksen 3 §:n otsikko sekä 3 ja 4 momentti, "
        "4 §:n 3 ja 4 momentti, 5 §:n 4–6 momentti, "
        "6 §:n 4–6 momentti, 7 §:n 4 momentti ja 8 §:n 3 momentti "
        "ovat voimassa 31 päivään joulukuuta 2025."
    )

    overrides = _temporary_provision_expiry_overrides(tree, "2025/236")

    got = {
        (override.section, override.subsection, override.special, override.expiry.isoformat())
        for override in overrides
    }
    assert ("3", None, "otsikko", "2025-12-31") in got
    assert ("3", 3, None, "2025-12-31") in got
    assert ("3", 4, None, "2025-12-31") in got
    assert ("4", 3, None, "2025-12-31") in got
    assert ("4", 4, None, "2025-12-31") in got
    assert ("5", 4, None, "2025-12-31") in got
    assert ("5", 5, None, "2025-12-31") in got
    assert ("5", 6, None, "2025-12-31") in got
    assert ("6", 4, None, "2025-12-31") in got
    assert ("6", 5, None, "2025-12-31") in got
    assert ("6", 6, None, "2025-12-31") in got
    assert ("7", 4, None, "2025-12-31") in got
    assert ("8", 3, None, "2025-12-31") in got


def test_temporary_provision_expiry_overrides_do_not_cross_sentence_boundaries() -> None:
    tree = _tree(
        "Tämä päätös tulee voimaan 21 päivänä kesäkuuta 2021. "
        "Päätöksen 2 §:n 1 ja 2 momentti sekä 7 §:n 1 momentti ovat voimassa "
        "31 päivään elokuuta 2021. "
        "Päätöksen 6 §:n 2 momentti on voimassa 31 päivään joulukuuta 2021."
    )

    overrides = _temporary_provision_expiry_overrides(tree, "2021/575")

    got = {
        (override.section, override.subsection, override.expiry.isoformat())
        for override in overrides
    }
    assert got == {
        ("2", 1, "2021-08-31"),
        ("2", 2, "2021-08-31"),
        ("7", 1, "2021-08-31"),
        ("6", 2, "2021-12-31"),
    }


def test_temporary_provision_expiry_overrides_parse_added_mixed_clause() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2009. "
        "Lakiin väliaikaisesti lisätty 43 a § on voimassa 31 päivään joulukuuta 2009. "
        "Lakiin väliaikaisesti lisätyt 43 b §:n 1 momentti ja 43 c § ovat voimassa "
        "31 päivään joulukuuta 2011 sekä 43 b §:n 2 ja 3 momentti "
        "31 päivään joulukuuta 2013."
    )

    overrides = _temporary_provision_expiry_overrides(tree, "2008/1085")

    got = {
        (override.section, override.subsection, override.special, override.expiry.isoformat())
        for override in overrides
    }
    assert ("43b", 1, None, "2011-12-31") in got
    assert ("43b", 2, None, "2013-12-31") in got
    assert ("43b", 3, None, "2013-12-31") in got


def test_temporary_section_expiry_overrides_collect_multiple_clauses() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä toukokuuta 2020. "
        "Lain 90 a § on voimassa 31 päivään heinäkuuta 2020 ja "
        "99 a § 31 päivään toukokuuta 2021."
    )
    overrides = _temporary_section_expiry_overrides(tree, "2020/292")
    assert {(row.target_mid, row.labels, row.expiry) for row in overrides} == {
        ("2020/292", frozenset({"90a"}), dt.date(2020, 7, 31)),
        ("2020/292", frozenset({"99a"}), dt.date(2021, 5, 31)),
    }


def test_temporary_section_expiry_overrides_parse_heading_chained_tail() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2010. "
        "Lain 69 c § ja sen edellä oleva väliotsikko ovat voimassa "
        "31 päivään joulukuuta 2012 ja 69 d § ja sen edellä oleva "
        "väliotsikko 31 päivään joulukuuta 2010."
    )

    overrides = _temporary_section_expiry_overrides(tree, "2009/887")

    rows = {(row.target_mid, row.labels, row.expiry) for row in overrides}
    assert ("2009/887", frozenset({"69c"}), dt.date(2012, 12, 31)) in rows
    assert ("2009/887", frozenset({"69d"}), dt.date(2010, 12, 31)) in rows


def test_temporary_section_expiry_overrides_parse_added_sections() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2009. "
        "Lakiin väliaikaisesti lisätty 43 a § on voimassa 31 päivään joulukuuta 2009. "
        "Lakiin väliaikaisesti lisätyt 43 b §:n 1 momentti ja 43 c § ovat voimassa "
        "31 päivään joulukuuta 2011 sekä 43 b §:n 2 ja 3 momentti "
        "31 päivään joulukuuta 2013."
    )

    overrides = _temporary_section_expiry_overrides(tree, "2008/1085")

    rows = {(row.target_mid, row.labels, row.expiry) for row in overrides}
    assert ("2008/1085", frozenset({"43a"}), dt.date(2009, 12, 31)) in rows
    assert ("2008/1085", frozenset({"43c"}), dt.date(2011, 12, 31)) in rows
    assert all(row.labels != {"43b"} for row in overrides)


def test_temporary_section_expiry_overrides_aggregate_added_section_subsections() -> None:
    tree = etree.fromstring(
        """
        <act>
          <body>
            <section><num>43 b §</num>
              <subsection><content><p>Ensimmäinen.</p></content></subsection>
              <subsection><content><p>Toinen.</p></content></subsection>
              <subsection><content><p>Kolmas.</p></content></subsection>
            </section>
          </body>
          <hcontainer name="entryIntoForce"><content>
            <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2009.</p>
            <p>Lakiin väliaikaisesti lisätyt 43 b §:n 1 momentti ovat voimassa
            31 päivään joulukuuta 2011 sekä 43 b §:n 2 ja 3 momentti
            31 päivään joulukuuta 2013.</p>
          </content></hcontainer>
        </act>
        """.encode()
    )

    overrides = _temporary_section_expiry_overrides(tree, "2008/1085")

    assert ("2008/1085", frozenset({"43b"}), dt.date(2013, 12, 31)) in {
        (row.target_mid, row.labels, row.expiry) for row in overrides
    }


def test_infer_expiry_date_from_temporary_payload_text_plural_tax_years() -> None:
    expiry = _infer_expiry_date_from_temporary_payload_text(
        "Vuosilta 1982 ja 1983 toimitettavissa verotuksissa katsotaan ..."
    )
    assert expiry is not None
    assert expiry.isoformat() == "1983-12-31"


def test_infer_expiry_date_from_temporary_payload_text_singular_tax_year() -> None:
    expiry = _infer_expiry_date_from_temporary_payload_text(
        "Vuodelta 1984 toimitettavassa verotuksessa katsotaan ..."
    )
    assert expiry is not None
    assert expiry.isoformat() == "1984-12-31"


def test_infer_expiry_date_from_temporary_payload_text_current_tax_year() -> None:
    expiry = _infer_expiry_date_from_temporary_payload_text(
        "Vuoden 2000 verotuksessa myönnetään kuorma-autoille vapautus."
    )
    assert expiry is not None
    assert expiry.isoformat() == "2000-12-31"


def test_temporary_section_expiry_override_infers_tax_year_window_from_payload() -> None:
    tree = _tree(
        """
        <preface><p><docTitle>Laki maatilatalouden tuloverolain väliaikaisesta muuttamisesta</docTitle></p></preface>
        <body>
          <section>
            <num>12 a §</num>
            <content><p>Vuosilta 1982 ja 1983 toimitettavissa verotuksissa katsotaan ...</p></content>
          </section>
        </body>
        """
    )
    override = _temporary_section_expiry_override(tree, "1982/1035")
    assert override is None


def test_infer_expiry_date_from_temporary_payload_text_uses_latest_tax_year() -> None:
    expiry = _infer_expiry_date_from_temporary_payload_text(
        "Vuodelta 1982 toimitettavassa verotuksessa katsotaan ... "
        "Vuodelta 1983 toimitettavassa verotuksessa vähennetään ..."
    )
    assert expiry is not None
    assert expiry.isoformat() == "1983-12-31"


def test_expiry_date_precedes_effective_date_flags_born_expired_interval() -> None:
    assert _expiry_date_precedes_effective_date(dt.date(1982, 12, 31), "1983-04-01") is True
    assert _expiry_date_precedes_effective_date(dt.date(1983, 12, 31), "1983-04-01") is False


def test_amendment_effective_date_prefers_entry_into_force_container_over_body_replace_text() -> None:
    """Whole-body scans must not miss the amendment's own deferred commencement.

    Regression shape from 2021/1199 amending 2016/258: the amendment body
    replaces 8 § with text beginning ``Tämä asetus tulee voimaan 1 päivänä
    toukokuuta 2016...`` and the amendment's own entry-into-force clause later
    says ``Tämä asetus tulee voimaan 31 päivänä joulukuuta 2021.`` Searching the
    full document text first hits the replaced section text, rejects it as older
    than issuance, and then incorrectly falls back to the issue date instead of
    the amendment's real commencement date.
    """
    tree = etree.fromstring(
        """
        <act xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <meta>
            <identification source="">
              <FRBRManifestation>
                <FRBRdate date="2021-12-17" name="dateIssued"/>
              </FRBRManifestation>
            </identification>
          </meta>
          <body>
            <section>
              <num>8 §</num>
              <content>
                <p>Tämä asetus tulee voimaan 1 päivänä toukokuuta 2016 ja on voimassa vuoden 2023 loppuun.</p>
              </content>
            </section>
            <hcontainer name="entryIntoForce">
              <content>
                <p>Tämä asetus tulee voimaan 31 päivänä joulukuuta 2021.</p>
              </content>
            </hcontainer>
          </body>
        </act>
        """.encode("utf-8")
    )

    effective, step = _amendment_effective_date_with_step(tree)

    assert effective is not None
    assert effective.isoformat() == "2021-12-31"
    assert step == "text_regex"


def test_temporary_section_expiry_override_parses_amendment_of_amendment_clause() -> None:
    tree = _tree(
        "muutetaan asetuksen (697/2020) voimaantulosäännös seuraavasti: "
        "Asetuksen 5, 8 b, 11 ja 12 § ovat voimassa 31 päivään joulukuuta 2021."
    )
    override = _temporary_section_expiry_override(tree, "2021/582")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2020/697",
        labels={"5", "8b", "11", "12"},
        expiry=dt.date(2021, 12, 31),
    )


def test_temporary_section_expiry_override_parses_lakkaa_olemasta_voimassa_clause() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 31 päivänä tammikuuta 2022. "
        "Tämän lain 21 b § lakkaa olemasta voimassa, kun tämä laki tulee muilta osin voimaan."
    )
    override = _temporary_section_expiry_override(tree, "2021/984")
    assert override is not None
    assert override.target_mid == "2021/984"
    assert override.labels == {"21b"}
    # The clause names the cessation day (2022-01-31 = first day NOT in force).
    # The override contract carries the INCLUSIVE last in-force day, so the
    # branch returns the preceding day; the stamp-site conversion (+1) restores
    # the cessation day as the kernel's exclusive cutoff.
    assert override.expiry.isoformat() == "2022-01-30"


def test_temporary_section_expiry_override_uses_title_scoped_temporary_target_in_mixed_amendment() -> None:
    tree = _tree(
        """
        <preface><p><docTitle>Laki yleisestä asumistuesta annetun lain 25 §:n muuttamisesta ja 51 §:n väliaikaisesta muuttamisesta</docTitle></p></preface>
        <body>
          <hcontainer name="entryIntoForce">
            <content>
              <p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2023 ja on voimassa 31 päivään joulukuuta 2023.</p>
            </content>
          </hcontainer>
        </body>
        """
    )
    override = _temporary_section_expiry_override(tree, "2022/1151")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2022/1151",
        labels={"51"},
        expiry=dt.date(2023, 12, 31),
    )


def test_temporary_section_expiry_override_en_dash_range() -> None:
    """Amendment 2021/876 style: en-dash ranges, sekä separator, NBSP in section numbers."""
    tree = _tree(
        "Lain 16\u00a0a\u201316\u00a0g ja 58\u00a0i\u201358\u00a0k \xa7, "
        "79 \xa7:n 3 momentti sekä 87\u00a0a ja 89\u00a0a \xa7 ovat voimassa "
        "31 päivään joulukuuta 2021."
    )
    override = _temporary_section_expiry_override(tree, "2021/876")
    assert override is not None
    assert override.target_mid == "2021/876"
    # Ranges 16a–16g and 58i–58k must be fully expanded
    labels = override.labels
    assert "16a" in labels
    assert "16b" in labels
    assert "16c" in labels
    assert "16d" in labels
    assert "16e" in labels
    assert "16f" in labels
    assert "16g" in labels
    assert "58i" in labels
    assert "58j" in labels
    assert "58k" in labels
    # Individual sections from 'sekä' list
    assert "87a" in labels
    assert "89a" in labels
    assert override.expiry.isoformat() == "2021-12-31"


def test_temporary_section_expiry_override_em_dash_range() -> None:
    """Amendment 2012/991 style: em-dash U+2014 with thin space U+2009 before §.

    "Lain 43 a\u2014 43 c\u2009§ ovat voimassa 31 päivään joulukuuta 2016."
    The em-dash (U+2014) must be accepted in _sec_chars and the range 43a–43c
    expanded correctly.  Without this fix the regex fails to match and the
    override returns None, causing all 2012/991 ops (including permanently-modified
    sections 16/18/20/21) to receive an erroneous expires='2016-12-31'.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2013. "
        "Lain 43 a\u201443 c\u2009\xa7 ovat voimassa 31 päivään joulukuuta 2016."
    )
    override = _temporary_section_expiry_override(tree, "2012/991")
    assert override is not None
    assert override.target_mid == "2012/991"
    assert "43a" in override.labels
    assert "43b" in override.labels
    assert "43c" in override.labels
    assert override.expiry.isoformat() == "2016-12-31"


def test_temporary_section_expiry_override_parses_applicability_transfer_window() -> None:
    """Amendment 2007/171 style: section applicability is limited by a transfer window."""
    text = (
        "Tämä laki tulee voimaan 23 päivänä helmikuuta 2007. "
        "Lain 43 b ja 43 c §:ää sovelletaan luovutukseen, joka tapahtuu "
        "1 päivän tammikuuta 2007 ja 31 päivän joulukuuta 2012 välisenä aikana."
    )
    tree = _tree(text)
    windows = _temporary_section_applicability_windows(text, "2007/171")
    assert len(windows) == 1
    window = windows[0]
    assert window.target_mid == "2007/171"
    assert window.sections == frozenset({"43b", "43c"})
    assert window.start.isoformat() == "2007-01-01"
    assert window.expiry.isoformat() == "2012-12-31"
    assert window.rule_id == "fi_temporary_section_applicability_window"

    override = _temporary_section_expiry_override(tree, "2007/171")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2007/171",
        labels={"43b", "43c"},
        expiry=dt.date(2012, 12, 31),
    )


def test_amendment_expiry_date_phased_entry_lakkaa_returns_none() -> None:
    """Section-selective 'lakkaa olemasta voimassa' must NOT set whole-amendment expiry.

    Models amendment 2021/984 where only 21 b § expires (when the main act enters
    force), but sections 4a, 5a, 7a, 18a-c, 21a, 21c, 22b are permanent inserts.
    Returning the main entry date would mark all ops as temporary → born-expired.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 31 päivänä tammikuuta 2022. "
        "Tämän lain 21 a ja 21\xa0b \xa7 ja 21\xa0c \xa7:n 1\u20133 momentti tulevat kuitenkin voimaan jo "
        "24 päivänä marraskuuta 2021. "
        "Tämän lain 6\xa0a \xa7 kumoutuu samana päivänä, kun 21 a ja 21\xa0b \xa7 ja 21\xa0c \xa7:n "
        "1\u20133 momentti tulevat voimaan ja lain 21\xa0b \xa7 lakkaa olemasta voimassa, "
        "kun tämä laki tulee muilta osin voimaan."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, "section-selective lakkaa must not set whole-amendment expiry"


def test_temporary_section_expiry_override_real_2021_984_clause_only_expires_21b() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 31 päivänä tammikuuta 2022. "
        "Tämän lain 21 a ja 21 b § ja 21 c §:n 1–3 momentti tulevat kuitenkin voimaan jo "
        "24 päivänä marraskuuta 2021. "
        "Tämän lain 6 a § kumoutuu samana päivänä, kun 21 a ja 21 b § ja 21 c §:n "
        "1–3 momentti tulevat voimaan ja lain 21 b § lakkaa olemasta voimassa, "
        "kun tämä laki tulee muilta osin voimaan."
    )

    override = _temporary_section_expiry_override(tree, "2021/984")

    assert override is not None
    assert override.target_mid == "2021/984"
    assert override.labels == {"21b"}
    # 2022-01-31 is the cessation day (first day NOT in force); the override
    # contract carries the INCLUSIVE last in-force day (see the lakkaa branch
    # in _temporary_section_expiry_overrides), so the day before is returned.
    assert override.expiry.isoformat() == "2022-01-30"


def test_section_commencement_effective_override_ignores_subsection_targets_and_keeps_whole_section() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 10 päivänä kesäkuuta 2019. "
        "Sen 15 luvun 2 §:n 1 ja 5 momentti sekä 16 luvun 1 § tulevat kuitenkin voimaan "
        "vasta 22 päivänä heinäkuuta 2019."
    )

    override = _section_commencement_effective_override(tree, "2019/511")

    assert override is not None
    target_mid, chapter_section_map, effective = override
    assert target_mid == "2019/511"
    assert chapter_section_map == {"16": {"1"}}
    assert effective.isoformat() == "2019-07-22"


def test_amendment_expiry_date_whole_act_vuoden_loppuun() -> None:
    """Whole-act 'vuoden YYYY loppuun' shorthand must return YYYY-12-31.

    Regression: 2018/523 amending 1998/555 has voimaantulo text:
      "Tämä laki tulee voimaan 1 päivänä tammikuuta 2019 ja on voimassa
       vuoden 2019 loppuun."
    Before the fix, _amendment_expiry_date returned None for this input,
    causing the temporary changes to persist indefinitely instead of expiring
    on 2019-12-31.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2019 "
        "ja on voimassa vuoden 2019 loppuun."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is not None
    assert expiry.isoformat() == "2019-12-31"


def test_temporary_section_expiry_override_section_scoped_vuoden_loppuun() -> None:
    """Section-scoped 'vuoden YYYY loppuun' must be handled by _temporary_section_expiry_override.

    Regression: 2016/1457 adding section 12b to chapter 2a of 2002/1290 has:
      "Tämä laki tulee voimaan 1 päivänä tammikuuta 2017. Lain 2 a luvun 12 b § on
       voimassa vuoden 2018 loppuun."
    Before the fix, section 12b never expired, appearing indefinitely in the replay
    as EXTRA (not in oracle, which dropped it after 2018-12-31).
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2017. "
        "Lain 2 a luvun 12 b § on voimassa vuoden 2018 loppuun."
    )
    override = _temporary_section_expiry_override(tree, "2016/1457")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2016/1457",
        labels={"12b"},
        expiry=dt.date(2018, 12, 31),
    )


def test_temporary_section_expiry_override_section_scoped_vuoden_loppuun_no_chapter_qualifier() -> None:
    """Section-scoped 'vuoden YYYY loppuun' without chapter qualifier."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2020. "
        "Lain 3 § on voimassa vuoden 2020 loppuun."
    )
    override = _temporary_section_expiry_override(tree, "2020/123")
    assert override is not None
    _assert_section_expiry_override(
        override,
        target_mid="2020/123",
        labels={"3"},
        expiry=dt.date(2020, 12, 31),
    )


def test_amendment_expiry_date_section_scoped_vuoden_loppuun_returns_none() -> None:
    """Section-scoped 'vuoden YYYY loppuun' must NOT be returned by _amendment_expiry_date.

    _amendment_expiry_date intentionally does NOT implement section-scoped
    "vuoden YYYY loppuun" matching.  Returning a date here would cause
    _enrich_ops_from_amendment_tree to stamp ALL ops from the amendment with
    that expiry date when _temporary_section_expiry_override also doesn't match,
    incorrectly expiring permanent sections.

    Example regression: 2013/262 amending 2006/693 has entryIntoForce text
    "Lain 11§:n 2 momentin 1 kohta on voimassa vuoden 2014 loppuun."
    Only section 11's 2nd subsection 1st kohta should expire.  If all ops
    received expires="2014-12-31", permanently-modified sections would be
    erroneously reverted after 2014.

    Section-scoped "vuoden YYYY loppuun" is deferred to
    _temporary_section_expiry_override once that function is extended.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2017. "
        "Lain 3 \xa7:n 1 momentti on voimassa vuoden 2019 loppuun."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        "Section-scoped 'vuoden YYYY loppuun' must not be returned by "
        "_amendment_expiry_date; use _temporary_section_expiry_override instead. "
        f"Got: {expiry!r}"
    )


def test_amendment_expiry_date_vuoden_loppuun_only_commencement_sentence() -> None:
    """'Tämä laki tulee voimaan DATE' without 'on voimassa' returns None.

    Pattern 3 requires "Tämä laki ... on voimassa vuoden YYYY loppuun" in the
    same sentence (no period between "Tämä laki" and "on voimassa").  A bare
    commencement sentence without an expiry clause must return None.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2017.\n"
        "Lain 3 \xa7:n 1 momentti on voimassa vuoden 2019 loppuun."
    )
    expiry = _amendment_expiry_date(tree)
    # Pattern 3 does not cross sentence boundary (period stops it);
    # section-scoped "vuoden YYYY loppuun" is not handled here at all → None
    assert expiry is None


def test_amendment_expiry_date_vuoden_loppuun_not_matched_for_target_statute() -> None:
    """'Laki on voimassa vuoden YYYY loppuun' after another sentence must not match.

    Regression: amendment 1992/272 amending 1990/1105 has entryIntoForce text:
      "Tämä laki tulee voimaan 1 päivänä huhtikuuta 1992.
       Lain 1 §:n 1 momentti tulee kuitenkin voimaan 1 päivänä lokakuuta 1992.
       Laki on voimassa vuoden 1993 loppuun, mutta jos kokeiluaika…"
    The third sentence "Laki on voimassa vuoden 1993 loppuun" refers to the TARGET
    statute (1990/1105), not to 1992/272 itself.  Pattern 3 must not match it.
    1992/272 is a PERMANENT amendment; marking it temporary caused its replayed ops
    to be erroneously reverted after 1993-12-31.
    """
    # Exact text from 1992/272 entryIntoForce (period stops Pattern 3 at first sentence)
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä huhtikuuta 1992. "
        "Lain 1 \xa7:n 1 momentti tulee kuitenkin voimaan 1 päivänä "
        "lokakuuta 1992. "
        "Laki on voimassa vuoden 1993 loppuun, mutta jos kokeiluaika harkitaan "
        "tarkoituksenmukaiseksi jatkaa, on siitä tätä ennen annettava laki."
    )
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        "Target-statute 'Laki on voimassa vuoden 1993 loppuun' must not set "
        f"amendment expiry; got {expiry!r} (expected None)"
    )


def test_amendment_expiry_date_vuoden_loppuun_not_matched_from_body_content() -> None:
    """Modified-target voimaantulo in an amendment body must NOT set whole-act expiry.

    Regression: amendment 2009/1362 is a PERMANENT amendment that changes another
    statute's voimaantulo to 'on voimassa vuoden 2012 loppuun'.  That modified text
    appears in the amendment body (inside a regular section element), BEFORE the
    amendment's own entryIntoForce element which says 'Tämä asetus tulee voimaan
    1 päivänä tammikuuta 2010.' (no expiry).

    Before the fix, Pattern 3 matched the body content and returned 2012-12-31 as
    2009/1362's own expiry, incorrectly tagging it as a temporary amendment.  This
    caused its replayed ops to be reverted at 2012-12-31 in official_consolidation mode,
    producing a regression in statutes that include 2009/1362 in their chain.

    The fix: Patterns 3 and 4 must search ONLY the <hcontainer name="entryIntoForce">
    element, not the full document text.
    """
    import lxml.etree as etree_local
    # Minimal AKN-style XML mirroring 2009/1362's structure:
    # - body section contains the modified target voimaantulo (a replacement clause)
    # - entryIntoForce contains the amendment's OWN commencement (no expiry)
    xml_str = (
        '<act xmlns:finlex="http://www.finlex.fi/ns/1.0">'
        '<body>'
        '<section><num>6 \xa7</num><subsection><content>'
        '<p>Tämä asetus tulee voimaan 1 päivänä'
        ' tammikuuta 2001 ja on voimassa vuoden 2012 loppuun.</p>'
        '</content></subsection></section>'
        '<hcontainer name="entryIntoForce"><content>'
        '<p>Tämä asetus tulee voimaan 1 päivänä'
        ' tammikuuta 2010.</p>'
        '</content></hcontainer>'
        '</body>'
        '</act>'
    )
    tree = etree_local.fromstring(xml_str.encode("utf-8"))
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        "Modified-target voimaantulo in amendment body must not set amendment expiry; "
        f"got {expiry!r} (expected None)"
    )


def test_amendment_expiry_date_day_month_year_not_matched_from_body_content() -> None:
    """Pattern 1 (day-month-year) must NOT match replacement body text.

    Regression: amendment 2016/87 is a PERMANENT amendment that replaces section 12
    of 2009/738 with text 'Tämä laki tulee voimaan 1 päivänä tammikuuta 2010 ja on
    voimassa 31 päivään joulukuuta 2020.'  That text refers to 2009/738 (the base
    law), not to 2016/87 (the amending act).

    Before the fix, Pattern 1 searched full_text and matched the body replacement
    content, returning 2020-12-31 as 2016/87's own expiry.  This stamped all of
    2016/87's ops with expires=2020-12-31, generating spurious expire TemporalEvents
    that reverted permanent changes (sections 2, 5, 8, 9, 10, 11, 12 of 2009/738)
    after 2020-12-31, causing a 58% bench error on 2009/738.

    The fix: Pattern 1 must search ONLY the <hcontainer name="entryIntoForce">
    element (eit_text), not the full document text.
    """
    import lxml.etree as etree_local
    # Minimal AKN-style XML mirroring 2016/87's structure:
    # - body section contains the replaced section 12 of 2009/738 (which mentions
    #   the BASE LAW's own commencement + expiry)
    # - entryIntoForce contains 2016/87's OWN commencement (no expiry)
    xml_str = (
        '<act xmlns:finlex="http://www.finlex.fi/ns/1.0">'
        '<body>'
        '<section><num>12 §</num><subsection><content>'
        '<p>Tämä laki tulee voimaan 1 päivänä tammikuuta 2010'
        ' ja on voimassa 31 päivään joulukuuta 2020.</p>'
        '</content></subsection></section>'
        '<hcontainer name="entryIntoForce"><content>'
        '<p>Tämä laki tulee voimaan 1 päivänä helmikuuta 2016.</p>'
        '</content></hcontainer>'
        '</body>'
        '</act>'
    )
    tree = etree_local.fromstring(xml_str.encode("utf-8"))
    expiry = _amendment_expiry_date(tree)
    assert expiry is None, (
        "Body replacement text 'Tämä laki on voimassa 31 päivään joulukuuta 2020' "
        "must not set amendment expiry for the amending act; "
        f"got {expiry!r} (expected None)"
    )


def test_commencement_expiry_override_parses_whole_act_target() -> None:
    tree = _tree(
        "muutetaan sosiaalihuoltolain väliaikaisesta muuttamisesta annetun lain "
        "(1428/2004) voimaantulosäännös, sellaisena kuin se on laissa 1105/2008, "
        "seuraavasti: Tämä laki tulee voimaan 1 päivänä tammikuuta 2005 ja on "
        "voimassa 31 päivään joulukuuta 2014."
    )
    override = _commencement_expiry_override(tree, "2010/1314")
    assert override is not None
    assert override.target_mid == "2004/1428"
    assert override.labels is None
    assert override.fallback_effective is not None
    assert override.fallback_effective.isoformat() == "2005-01-01"
    assert override.expiry.isoformat() == "2014-12-31"


def test_section_commencement_effective_override_enumerated_sections_share_terminal_sign() -> None:
    """An enumerated whole-section list shares one terminal § sign.

    Regression (2010/1326 ← 2023/116): "Lain 51 a ja 51 b § tulevat kuitenkin
    voimaan 1 päivänä marraskuuta 2024." defers BOTH 51 a and 51 b. The
    previous parser required § directly after each label and captured only
    51 b, leaving 51 a stamped with the amendment-wide date — which made the
    temporary twin 2023/117's gap-filler INSERT look like an occupancy
    violation against a same-day substantive occupant.
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä syyskuuta 2023. "
        "Lain 51 a ja 51 b § tulevat kuitenkin voimaan 1 päivänä marraskuuta 2024."
    )

    override = _section_commencement_effective_override(tree, "2023/116")

    assert override is not None
    target_mid, chapter_section_map, effective = override
    assert target_mid == "2023/116"
    assert chapter_section_map == {None: {"51a", "51b"}}
    assert effective.isoformat() == "2024-11-01"


def test_section_commencement_effective_override_comma_enumeration() -> None:
    """Comma+ja enumerations defer every listed whole-section label."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2025. "
        "Lain 5, 7 ja 9 § tulevat kuitenkin voimaan vasta 1 päivänä maaliskuuta 2025."
    )

    override = _section_commencement_effective_override(tree, "2025/1")

    assert override is not None
    _target_mid, chapter_section_map, effective = override
    assert chapter_section_map == {None: {"5", "7", "9"}}
    assert effective.isoformat() == "2025-03-01"


def test_section_commencement_effective_override_single_section_unchanged() -> None:
    """The single-section form (2022/1281: 'Sen 78 c §') keeps working."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2023. "
        "Sen 78 c § tulee kuitenkin voimaan vasta 1 päivänä heinäkuuta 2023."
    )

    override = _section_commencement_effective_override(tree, "2022/1281")

    assert override is not None
    _target_mid, chapter_section_map, effective = override
    assert chapter_section_map == {None: {"78c"}}
    assert effective.isoformat() == "2023-07-01"


def test_chapter_commencement_effective_overrides_parse_staged_chapter_clauses() -> None:
    tree = _tree(
        "Tämän lain 1, 6 ja 6 a luku tulevat voimaan 19 päivänä kesäkuuta 2026. "
        "Lain 7 ja 7 a luku tulevat kuitenkin voimaan vasta 20 päivänä marraskuuta 2026."
    )

    overrides = _chapter_commencement_effective_overrides(tree, "2026/31")

    assert overrides == (
        ("2026/31", frozenset({"1", "6", "6a"}), dt.date(2026, 6, 19)),
        ("2026/31", frozenset({"7", "7a"}), dt.date(2026, 11, 20)),
    )


def test_subsection_commencement_effective_override_mixed_enumeration() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2023. "
        "Lain 3 a §:n 1 momentti, 14 §:n 1 momentti sekä 14 a ja 15 b § "
        "tulevat kuitenkin voimaan vasta 1 päivänä tammikuuta 2028."
    )

    override = _section_subsection_commencement_effective_override(tree, "2022/876")

    assert override is not None
    target_mid, addresses, effective = override
    assert target_mid == "2022/876"
    assert {str(address) for address in addresses} == {
        "section:3a/subsection:1",
        "section:14/subsection:1",
    }
    assert effective.isoformat() == "2028-01-01"


def test_subsection_commencement_effective_override_chapter_range() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 10 päivänä kesäkuuta 2019. "
        "Sen 15 luvun 2 §:n 1 ja 5 momentti sekä 16 luvun 1 § tulevat kuitenkin "
        "voimaan vasta 22 päivänä heinäkuuta 2019."
    )

    override = _section_subsection_commencement_effective_override(tree, "2019/511")

    assert override is not None
    _target_mid, addresses, effective = override
    assert {str(address) for address in addresses} == {
        "chapter:15/section:2/subsection:1",
        "chapter:15/section:2/subsection:5",
    }
    assert effective.isoformat() == "2019-07-22"


def test_subsection_commencement_effective_override_parses_mixed_child_and_repeal_scopes() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2025. "
        "Sen 5 a §:n kumoaminen sekä 4 §:n 2, 3 ja 5 kohta, "
        "5 §:n otsikko sekä 1 ja 2 momentti, 6 ja 8 § sekä 12 §:n 1 momentti "
        "tulevat kuitenkin voimaan vasta 1 päivänä tammikuuta 2026."
    )

    override = _section_subsection_commencement_effective_override(tree, "2025/212")

    assert override is not None
    target_mid, addresses, effective = override
    assert target_mid == "2025/212"
    assert set(addresses) == {
        LegalAddress(path=(("section", "5a"),)),
        LegalAddress(path=(("section", "4"), ("item", "2"))),
        LegalAddress(path=(("section", "4"), ("item", "3"))),
        LegalAddress(path=(("section", "4"), ("item", "5"))),
        LegalAddress(path=(("section", "5"),), special=FacetKind.HEADING),
        LegalAddress(path=(("section", "5"), ("subsection", "1"))),
        LegalAddress(path=(("section", "5"), ("subsection", "2"))),
        LegalAddress(path=(("section", "12"), ("subsection", "1"))),
    }
    assert effective.isoformat() == "2026-01-01"


def test_subsection_application_commencement_effective_override_parses_scoped_application_date() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan valtioneuvoston asetuksella säädettävänä ajankohtana. "
        "Lain 4 §:n 2 momenttia sovelletaan kuitenkin 1 päivänä tammikuuta 2007 "
        "tai sen jälkeen aiheutuneista kustannuksista maksettavaan tukeen."
    )

    override = _section_subsection_application_commencement_effective_override(tree, "2006/1322")

    assert override is not None
    target_mid, addresses, effective = override
    assert target_mid == "2006/1322"
    assert addresses == (LegalAddress(path=(("section", "4"), ("subsection", "2"))),)
    assert effective.isoformat() == "2007-01-01"


def test_subsection_application_commencement_effective_override_rejects_fixed_transition() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. "
        "Lain 4 §:n 2 momenttia sovelletaan kuitenkin 1 päivänä tammikuuta 2024 "
        "tai sen jälkeen vireille tuleviin asioihin."
    )

    override = _section_subsection_application_commencement_effective_override(tree, "2023/1")

    assert override is None


def test_temporary_provision_expiry_overrides_compose_subref_grammar_and_canonical_date() -> None:
    """#22B: the scoped temporary-expiry sentence composes the shared sub-ref
    grammar (subject) with the canonical temporal expiry-date extractor (date).

    The subject's section + momentti coordination/range/letter-suffix is parsed by
    references.sections.parse_body_provision_tail; the date by
    temporal_lowering._extract_expiry_date_from_text. Only ``§:n``-bodied facets
    (otsikko + momentti) are owned here — a bare whole-section ``§`` mention in
    the same subject is NOT emitted as a provision override (it is section-scoped).
    """
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä tammikuuta 2024. "
        "Lain 12 a §:n 2 ja 3 momentti, 13 §:n otsikko sekä 99 § "
        "ovat voimassa 30 päivään kesäkuuta 2025."
    )

    overrides = _temporary_provision_expiry_overrides(tree, "2024/100")
    got = {
        (o.section, o.subsection, o.special, o.expiry.isoformat()) for o in overrides
    }
    assert got == {
        ("12a", 2, None, "2025-06-30"),
        ("12a", 3, None, "2025-06-30"),
        ("13", None, "otsikko", "2025-06-30"),
    }
    # The bare whole-section ``99 §`` (no ``§:n`` facet) is NOT a provision override.
    assert all(o.section != "99" for o in overrides)


def test_q3_temporal_lens_shares_canonical_month_table() -> None:
    """Q3: the references/temporal surface lens and the production date extractor
    share ONE canonical Finnish month table (no rival copy that can drift)."""
    from lawvm.finland.fi_dates import (
        FI_MONTH_PARTITIVE_TO_NUMBER,
        fi_partitive_month_number,
        parse_fi_day_month_year,
    )
    from lawvm.finland.references.temporal import _MONTHS_PARTITIVE
    from lawvm.finland.temporal_lowering import _MONTH_MAP

    assert _MONTHS_PARTITIVE is FI_MONTH_PARTITIVE_TO_NUMBER
    assert _MONTH_MAP is FI_MONTH_PARTITIVE_TO_NUMBER
    assert fi_partitive_month_number("joulukuu-ta", tolerate_finlex_typos=True) == 12
    assert fi_partitive_month_number("joulukuutta", tolerate_finlex_typos=True) == 12
    assert fi_partitive_month_number("joulukuu-ta") is None
    parsed = parse_fi_day_month_year(
        "31",
        "joulukuu-ta",
        "2026",
        tolerate_finlex_typos=True,
    )
    assert parsed is not None
    assert parsed.isoformat() == "2026-12-31"
