from __future__ import annotations

import datetime as dt

import lxml.etree as etree

from lawvm.finland.metadata import (
    _amendment_effective_date_with_step,
    _amendment_expiry_date,
    _commencement_expiry_override,
    _expiry_date_precedes_effective_date,
    _infer_expiry_date_from_temporary_payload_text,
    _normalize_fi_parse_text,
    _section_commencement_effective_override,
    _temporary_section_expiry_overrides,
    _temporary_section_expiry_override,
)


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


def test_temporary_section_expiry_override_parses_direct_source_clause() -> None:
    tree = _tree(
        "Tämä asetus tulee voimaan 19 päivänä lokakuuta 2020. "
        "Asetuksen 5, 8 b, 11 ja 12 § ovat voimassa 31 päivään joulukuuta 2020."
    )
    override = _temporary_section_expiry_override(tree, "2020/697")
    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2020/697"
    assert labels == {"5", "8b", "11", "12"}
    assert expiry.isoformat() == "2020-12-31"


def test_temporary_section_expiry_override_accepts_laki_wording() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 19 päivänä lokakuuta 2020. "
        "Lain 5, 8 b, 11 ja 12 § ovat voimassa 31 päivään joulukuuta 2020."
    )
    override = _temporary_section_expiry_override(tree, "2020/697")
    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2020/697"
    assert labels == {"5", "8b", "11", "12"}
    assert expiry.isoformat() == "2020-12-31"


def test_temporary_section_expiry_overrides_collect_multiple_clauses() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä toukokuuta 2020. "
        "Lain 90 a § on voimassa 31 päivään heinäkuuta 2020 ja "
        "99 a § 31 päivään toukokuuta 2021."
    )
    overrides = _temporary_section_expiry_overrides(tree, "2020/292")
    assert overrides == (
        ("2020/292", {"90a"}, dt.date(2020, 7, 31)),
        ("2020/292", {"99a"}, dt.date(2021, 5, 31)),
    )


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
    target_mid, labels, expiry = override
    assert target_mid == "2020/697"
    assert labels == {"5", "8b", "11", "12"}
    assert expiry.isoformat() == "2021-12-31"


def test_temporary_section_expiry_override_parses_lakkaa_olemasta_voimassa_clause() -> None:
    tree = _tree(
        "Tämä laki tulee voimaan 31 päivänä tammikuuta 2022. "
        "Tämän lain 21 b § lakkaa olemasta voimassa, kun tämä laki tulee muilta osin voimaan."
    )
    override = _temporary_section_expiry_override(tree, "2021/984")
    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2021/984"
    assert labels == {"21b"}
    assert expiry.isoformat() == "2022-01-31"


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
    target_mid, labels, expiry = override
    assert target_mid == "2022/1151"
    assert labels == {"51"}
    assert expiry.isoformat() == "2023-12-31"


def test_temporary_section_expiry_override_en_dash_range() -> None:
    """Amendment 2021/876 style: en-dash ranges, sekä separator, NBSP in section numbers."""
    tree = _tree(
        "Lain 16\u00a0a\u201316\u00a0g ja 58\u00a0i\u201358\u00a0k \xa7, "
        "79 \xa7:n 3 momentti sekä 87\u00a0a ja 89\u00a0a \xa7 ovat voimassa "
        "31 päivään joulukuuta 2021."
    )
    override = _temporary_section_expiry_override(tree, "2021/876")
    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2021/876"
    # Ranges 16a–16g and 58i–58k must be fully expanded
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
    assert expiry.isoformat() == "2021-12-31"


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
    target_mid, labels, expiry = override
    assert target_mid == "2012/991"
    assert "43a" in labels
    assert "43b" in labels
    assert "43c" in labels
    assert expiry.isoformat() == "2016-12-31"


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
    target_mid, labels, expiry = override
    assert target_mid == "2021/984"
    assert labels == {"21b"}
    assert expiry.isoformat() == "2022-01-31"


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
    target_mid, labels, expiry = override
    assert target_mid == "2016/1457"
    assert labels == {"12b"}
    assert expiry.isoformat() == "2018-12-31"


def test_temporary_section_expiry_override_section_scoped_vuoden_loppuun_no_chapter_qualifier() -> None:
    """Section-scoped 'vuoden YYYY loppuun' without chapter qualifier."""
    tree = _tree(
        "Tämä laki tulee voimaan 1 päivänä maaliskuuta 2020. "
        "Lain 3 § on voimassa vuoden 2020 loppuun."
    )
    override = _temporary_section_expiry_override(tree, "2020/123")
    assert override is not None
    target_mid, labels, expiry = override
    assert target_mid == "2020/123"
    assert labels == {"3"}
    assert expiry.isoformat() == "2020-12-31"


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
    caused its replayed ops to be reverted at 2012-12-31 in finlex_oracle mode,
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
    target_mid, labels, expiry = override
    assert target_mid == "2004/1428"
    assert labels is None
    assert expiry.isoformat() == "2014-12-31"
