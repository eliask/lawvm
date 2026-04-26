from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.tools.section_keys import (
    extract_ir_sections,
    extract_oracle_sections,
    normalize_address_filter,
    reconcile_unique_unscoped_aliases,
    section_key_from_target_dict,
    section_key_matches_filter,
    section_key_sort_text,
)


def test_section_key_extractors_preserve_duplicate_section_numbers_across_chapters() -> None:
    oracle = etree.fromstring(
        """
        <statute>
          <body>
            <chapter>
              <num>1 luku</num>
              <section><num>4 §</num><content>eka</content></section>
            </chapter>
            <chapter>
              <num>9 luku</num>
              <section><num>4 §</num><content>toka</content></section>
            </chapter>
          </body>
        </statute>
        """
    )
    replay = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.SECTION, label="4"),)),
            IRNode(kind=IRNodeKind.CHAPTER, label="9", children=(IRNode(kind=IRNodeKind.SECTION, label="4"),)),),
    )

    oracle_keys = extract_oracle_sections(oracle)
    replay_keys = extract_ir_sections(replay)

    assert set(oracle_keys) == {"chapter:1/section:4", "chapter:9/section:4"}
    assert set(replay_keys) == {"chapter:1/section:4", "chapter:9/section:4"}


def test_section_key_filter_supports_full_path_and_leaf_section_matching() -> None:
    key = "chapter:9/section:4"

    assert normalize_address_filter("chapter:9/section:4") == key
    assert section_key_matches_filter(key, ("path", key)) is True
    assert section_key_matches_filter(key, ("section", "4")) is True
    assert section_key_matches_filter(key, ("chapter", "9")) is True
    assert section_key_matches_filter(key, ("path", "chapter:1/section:4")) is False


def test_section_key_from_target_dict_uses_chapter_when_present() -> None:
    assert section_key_from_target_dict(
        {"container": "section", "chapter": "9", "section": "4"}
    ) == "chapter:9/section:4"
    assert section_key_from_target_dict({"container": "section", "section": "4"}) == "section:4"


def test_section_key_sort_text_orders_numerically_before_lexically() -> None:
    assert section_key_sort_text("chapter:1/section:2") < section_key_sort_text("chapter:1/section:17")
    assert section_key_sort_text("chapter:1/section:2") < section_key_sort_text("chapter:2/section:1")


def test_normalize_address_filter_normalizes_roman_numerals_beyond_twenty() -> None:
    assert normalize_address_filter("chapter:XXI/section:IV") == "chapter:21/section:4"


def test_extract_oracle_sections_strips_inline_prior_wording_duplicates() -> None:
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section eId="sec_2a">
              <num>2 a §</num>
              <heading>Otsikko</heading>
              <subsection eId="sec_2a__subsec_1v20161491">
                <content><p>nykyinen momentti</p></content>
              </subsection>
              <hcontainer name="noteAuthorial">
                <content><p>L:lla 1491/2016 muutettu 1 momentti tuli voimaan 1.1.2017. Aiempi sanamuoto kuuluu:</p></content>
              </hcontainer>
              <subsection eId="sec_2a__subsec_1v20151706">
                <content><p>vanha momentti</p></content>
              </subsection>
              <subsection eId="sec_2a__subsec_2v20080680">
                <content><p>toinen momentti</p></content>
              </subsection>
            </section>
          </body>
        </statute>
        """
    )

    sec = extract_oracle_sections(oracle)["section:2a"]
    text = etree.tostring(sec, method="text", encoding="unicode")

    assert "nykyinen momentti" in text
    assert "toinen momentti" in text
    assert "vanha momentti" not in text
    assert "Aiempi sanamuoto kuuluu" not in text


def test_extract_oracle_sections_skips_original_version_shadow_sections() -> None:
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" xmlns:finlex="http://data.finlex.fi/schema/finlex">
          <body>
            <section eId="chp_11__sec_75v20250743" finlex:originalVersion="@20250743" finlex:originalVersionLabel="27.6.2025/743">
              <num>75 §</num>
              <heading>Ahvenanmaan valtionviraston perimät maksut</heading>
              <subsection><content><p>shadow text</p></content></subsection>
            </section>
            <section eId="chp_11__sec_75">
              <num>75 §</num>
              <heading>Ahvenanmaan valtionviraston perimät maksut</heading>
              <subsection><content><p>current text</p></content></subsection>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert set(oracle_keys) == {"section:75"}
    text = etree.tostring(oracle_keys["section:75"], method="text", encoding="unicode")
    assert "current text" in text
    assert "shadow text" not in text


def test_extract_oracle_sections_keeps_versioned_current_section_when_it_is_the_only_current_copy() -> None:
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0" xmlns:finlex="http://data.finlex.fi/schema/finlex">
          <body>
            <section eId="sec_7v20191458" finlex:originalVersion="@20191458" finlex:originalVersionLabel="18.12.2019/1458">
              <num>7 §</num>
              <heading>Julkisuuslain perusteella annettavat suoritteet</heading>
              <subsection><content><p>current versioned text</p></content></subsection>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert set(oracle_keys) == {"section:7"}
    text = etree.tostring(oracle_keys["section:7"], method="text", encoding="unicode")
    assert "current versioned text" in text


def test_reconcile_unique_unscoped_aliases_maps_identical_unscoped_section_to_scoped_oracle() -> None:
    oracle = etree.fromstring(
        """
        <statute>
          <body>
            <chapter>
              <num>4 luku</num>
              <section><num>20 §</num><heading>Voimaantulo</heading><content><p>sama teksti</p></content></section>
            </chapter>
          </body>
        </statute>
        """
    )
    replay = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(
                kind=IRNodeKind.SECTION,
                label="20",
                children=(IRNode(kind=IRNodeKind.NUM, text="20 §"),
                    IRNode(kind=IRNodeKind.HEADING, text="Voimaantulo"),
                    IRNode(kind=IRNodeKind.CONTENT, text="sama teksti"),),
            ),),
    )

    replay_keys = extract_ir_sections(replay)
    oracle_keys = extract_oracle_sections(oracle)

    aligned_replay, aligned_oracle = reconcile_unique_unscoped_aliases(
        replay_keys, oracle_keys
    )

    assert set(aligned_replay) == {"chapter:4/section:20"}
    assert set(aligned_oracle) == {"chapter:4/section:20"}


def test_reconcile_unique_unscoped_aliases_aligns_unique_scoped_mismatches_for_comparison() -> None:
    oracle = etree.fromstring(
        """
        <statute>
          <body>
            <chapter>
              <num>4 luku</num>
              <section><num>20 §</num><content><p>oracle teksti</p></content></section>
            </chapter>
          </body>
        </statute>
        """
    )
    replay = IRNode(
        kind=IRNodeKind.BODY,
        children=(IRNode(
                kind=IRNodeKind.SECTION,
                label="20",
                children=(IRNode(kind=IRNodeKind.NUM, text="20 §"),
                    IRNode(kind=IRNodeKind.CONTENT, text="eri teksti"),),
            ),),
    )

    replay_keys = extract_ir_sections(replay)
    oracle_keys = extract_oracle_sections(oracle)

    aligned_replay, aligned_oracle = reconcile_unique_unscoped_aliases(
        replay_keys, oracle_keys
    )

    assert set(aligned_replay) == {"chapter:4/section:20"}
    assert set(aligned_oracle) == {"chapter:4/section:20"}


def test_extract_oracle_sections_normalizes_part_labels_without_osa_suffix() -> None:
    oracle = etree.fromstring(
        """
        <statute>
          <body>
            <part>
              <num>I osa</num>
              <chapter>
                <num>10 luku</num>
                <section><num>1 §</num><content><p>teksti</p></content></section>
              </chapter>
            </part>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert set(oracle_keys) == {"part:1/chapter:10/section:1"}


def test_dedup_versioned_children_preserves_distinct_provisions_sharing_eid_slot() -> None:
    """When Finlex inserts a new item between existing items, the new item reuses the
    same positional eId slot as the item that follows it (e.g. para_6v20251385 for
    the newly-added "5 a)" and para_6v20141432 for the pre-existing "6)").
    The dedup pass must keep both because they carry distinct <num> texts."""
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    sub = etree.fromstring(
        f"""<subsection xmlns="{ns}" eId="chp_2__sec_6v20141432__subsec_1v20141432">
          <paragraph eId="chp_2__sec_6v20141432__subsec_1v20141432__para_5v20141432">
            <num>5)</num><content><p>item 5</p></content>
          </paragraph>
          <paragraph eId="chp_2__sec_6v20141432__subsec_1v20141432__para_6v20251385">
            <num>5 a)</num><content><p>item 5a</p></content>
          </paragraph>
          <paragraph eId="chp_2__sec_6v20141432__subsec_1v20141432__para_6v20141432">
            <num>6)</num><content><p>item 6</p></content>
          </paragraph>
          <paragraph eId="chp_2__sec_6v20141432__subsec_1v20141432__para_7v20141432">
            <num>7)</num><content><p>item 7</p></content>
          </paragraph>
        </subsection>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sub, "paragraph")

    paras = sub.findall(f"{{{ns}}}paragraph")
    nums = [p.findtext(f"{{{ns}}}num") for p in paras]
    assert nums == ["5)", "5 a)", "6)", "7)"], (
        "item 6 must not be dropped: para_6v20141432 and para_6v20251385 share an "
        "eId slot but carry different <num> texts and represent distinct provisions"
    )


def test_dedup_versioned_children_removes_genuine_versioned_duplicates() -> None:
    """Genuine duplicate version snapshots of the same provision (same eId base AND
    same num text with near-identical content) must still be deduplicated — only
    the first occurrence is kept."""
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    sub = etree.fromstring(
        f"""<subsection xmlns="{ns}" eId="subsec_1">
          <paragraph eId="para_3v20140649"><num>3)</num><content><p>Maksu on 10 euroa.</p></content></paragraph>
          <paragraph eId="para_3v20230499"><num>3)</num><content><p>Maksu on 11 euroa.</p></content></paragraph>
          <paragraph eId="para_4v20141432"><num>4)</num><content><p>item 4</p></content></paragraph>
        </subsection>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sub, "paragraph")

    paras = sub.findall(f"{{{ns}}}paragraph")
    nums = [p.findtext(f"{{{ns}}}num") for p in paras]
    assert nums == ["3)", "4)"], "second snapshot of item 3 must be removed"
    assert paras[0].get("eId") == "para_3v20140649", "first (oldest) snapshot is kept"


def test_dedup_versioned_children_preserves_unnumbered_subsections_with_different_content() -> None:
    """Pro Q1: omission-elision encoding produces two unnumbered subsections sharing
    the same positional eId slot but carrying substantially different text bodies.

    E.g. statute 2012/316 amended by 2024/859 — the original subsection 1
    (subsec_1v20150795) and the new subsection 2 (subsec_1v20240859) both have no
    <num> text but carry completely different content. Both must be preserved.
    """
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    # Replicate the 2012/316@2024/859 pattern. subsec_1v20150795 is a long list
    # provision with an intro paragraph and 3 items; subsec_1v20240859 is a short
    # single-sentence provision. Cleaned-text length ratio ~0.07 < 0.5.
    sec = etree.fromstring(
        f"""<section xmlns="{ns}" eId="sec_1v20150795">
          <num>1 §</num>
          <subsection eId="sec_1v20150795__subsec_1v20150795">
            <intro><p>Seuraavat valtioneuvoston yleisistunnossa tehtävät päätökset ovat valtion maksuperustelain 6 pykälän 2 momentissa tarkoitettuja maksullisia julkisoikeudellisia suoritteita, joista peritään kiinteä omakustannusarvon mukainen maksu:</p></intro>
            <paragraph eId="sec_1v20150795__subsec_1v20150795__para_1v20150795">
              <num>1)</num><content><p>ammattikorkeakoululain tarkoitettu toimilupa ja toimiluvan muutos;</p></content>
            </paragraph>
            <paragraph eId="sec_1v20150795__subsec_1v20150795__para_2v20150795">
              <num>2)</num><content><p>perusopetuslain tarkoitettu lupa opetuksen järjestämiseen;</p></content>
            </paragraph>
            <paragraph eId="sec_1v20150795__subsec_1v20150795__para_3v20150795">
              <num>3)</num><content><p>opetusministeriön kelpoisuusvaatimuksia koskeva erivapauspäätös.</p></content>
            </paragraph>
          </subsection>
          <subsection eId="sec_1v20150795__subsec_1v20240859">
            <content><p>Maksun suuruus on 7 135 euroa.</p></content>
          </subsection>
        </section>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sec, "subsection")

    subsecs = sec.findall(f"{{{ns}}}subsection")
    assert len(subsecs) == 2, (
        "both subsections must be kept: subsec_1v20150795 and subsec_1v20240859 "
        "share the positional slot but carry substantially different content (Pro Q1)"
    )
    eids = [s.get("eId", "") for s in subsecs]
    assert "sec_1v20150795__subsec_1v20150795" in eids
    assert "sec_1v20150795__subsec_1v20240859" in eids


def test_dedup_versioned_children_removes_unnumbered_subsection_duplicates_with_similar_content() -> None:
    """When two unnumbered subsections share the same eId slot and have similar
    content (true version duplicates), only the first is retained."""
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    sec = etree.fromstring(
        f"""<section xmlns="{ns}" eId="sec_1">
          <num>1 §</num>
          <subsection eId="sec_1__subsec_1v20150795">
            <content><p>Maksun suuruus on toimiluvasta 5 000 euroa.</p></content>
          </subsection>
          <subsection eId="sec_1__subsec_1v20200123">
            <content><p>Maksun suuruus on toimiluvasta 6 000 euroa.</p></content>
          </subsection>
        </section>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sec, "subsection")

    subsecs = sec.findall(f"{{{ns}}}subsection")
    assert len(subsecs) == 1, (
        "two subsections with same eId base and similar content are version duplicates; "
        "only the first should be kept"
    )
    assert subsecs[0].get("eId") == "sec_1__subsec_1v20150795"


def test_dedup_versioned_children_preserves_same_slot_subsection_with_distinct_live_content() -> None:
    """Keep a live replacement subsection that reuses an earlier slot eId.

    2017/519@2025/1248 carries 28 § with an older unnumbered subsection 2 and a
    later live replacement subsection at the same slot base (`subsec_2v...`).
    Their lengths are similar, so length-ratio-only dedup wrongly drops the
    later live subsection.
    """
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    sec = etree.fromstring(
        f"""<section xmlns="{ns}" xmlns:finlex="http://data.finlex.fi/schema/finlex" eId="sec_28v20190979">
          <num>28 §</num>
          <subsection eId="sec_28v20190979__subsec_1v20190979">
            <content><p>Ministeriön virkamiesjohtoryhmä käsittelee hallinnonalan ja ministeriön toimintaan keskeisesti vaikuttavat yhteiskuntapoliittiset linjaukset.</p></content>
          </subsection>
          <subsection eId="sec_28v20190979__subsec_2v20190979">
            <content><p>Johtoryhmä arvioi myös toiminnan tuloksellisuutta sekä käsittelee ja sovittaa yhteen muut laajakantoiset asiat.</p></content>
          </subsection>
          <subsection eId="sec_28v20190979__subsec_3v20190979">
            <content><p>Johtoryhmä käsittelee myös hallinnonalan keskeiset EU-asiat ja muut kansainväliset asiat.</p></content>
          </subsection>
          <subsection eId="sec_28v20190979__subsec_2v20221048" finlex:originalVersion="@20221048">
            <content><p>Johtoryhmään kuuluvat kansliapäällikkö ja osastopäälliköt sekä erillisyksiköiden päälliköt. Valtiosihteeri voi osallistua johtoryhmän kokoukseen silloin, kun kokouksessa käsitellään EU-asioita. Ministerin erityisavustajalla on oikeus olla läsnä johtoryhmän kokouksessa. Henkilöstön edustajalla on oikeus olla läsnä johtoryhmän kokouksessa.</p></content>
          </subsection>
          <subsection eId="sec_28v20190979__subsec_5v20190979">
            <content><p>Johtoryhmän puheenjohtajana toimii kansliapäällikkö.</p></content>
          </subsection>
        </section>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sec, "subsection")

    subsecs = sec.findall(f"{{{ns}}}subsection")
    eids = [s.get("eId", "") for s in subsecs]
    assert len(subsecs) == 5
    assert "sec_28v20190979__subsec_2v20190979" in eids
    assert "sec_28v20190979__subsec_2v20221048" in eids


def test_dedup_versioned_children_prefers_versioned_same_slot_subsection_over_plain_shadow() -> None:
    ns = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
    sec = etree.fromstring(
        f"""<section xmlns="{ns}" xmlns:finlex="http://data.finlex.fi/schema/finlex" eId="sec_35">
          <num>35 §</num>
          <subsection eId="sec_35__subsec_4v20230661" finlex:originalVersion="@20230661">
            <content><p>nykyinen 4 momentti</p></content>
          </subsection>
          <subsection eId="sec_35__subsec_4">
            <content><p>aiempi 4 momentti</p></content>
          </subsection>
        </section>"""
    )
    from lawvm.tools.section_keys import _dedup_versioned_children

    _dedup_versioned_children(sec, "subsection")

    texts = etree.tostring(sec, method="text", encoding="unicode")
    assert "nykyinen 4 momentti" in texts
    assert "aiempi 4 momentti" not in texts


def test_extract_oracle_sections_prefers_versioned_same_slot_subsection_for_2016_768_section_35() -> None:
    from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
    from lawvm.finland.grafter import get_consolidated_oracle_context, get_corpus

    ctx = get_consolidated_oracle_context(
        "2016/768",
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
    )
    xml = get_corpus().read_locator(ctx.locator)
    assert xml is not None
    oracle = etree.fromstring(xml)

    section = extract_oracle_sections(oracle)["chapter:7/section:35"]
    text = etree.tostring(section, method="text", encoding="unicode")

    assert "ellei verovelvollisen kuuleminen ennen myöhästymismaksun määräämistä ole ilmeisen tarpeetonta" in text
    assert "ennen myöhästymismaksun määräämistä, jos se on erityisestä syystä tarpeen" not in text


def test_extract_oracle_sections_excludes_kumottu_tombstone_without_original_version() -> None:
    """Kumottu stubs without finlex:originalVersion (only <num>+<content>) must be excluded.

    Finlex XML embeds repeal notices like '5 § on kumottu A:lla 4.5.2023/815.'
    directly as a versioned section with only <num> and bare <content>.  LawVM's
    replay correctly omits expired sections, so these stubs must be filtered out
    to avoid spurious unit_missing_right divergences.
    """
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter>
              <num>2 luku</num>
              <section eId="chp_2__sec_4v19970343">
                <num>4 §</num>
                <content><p>4 § on kumottu A:lla 18.4.1997/343.</p></content>
              </section>
              <section eId="chp_2__sec_5v20230815">
                <num>5 §</num>
                <content><p><i>5 § on kumottu A:lla 4.5.2023/815.</i></p></content>
              </section>
              <section eId="chp_2__sec_6">
                <num>6 §</num>
                <heading>Voimassa oleva pykälä</heading>
                <subsection><content><p>live content</p></content></subsection>
              </section>
            </chapter>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    # Only the live section 6 should appear; kumottu stubs 4 and 5 must be excluded
    assert set(oracle_keys) == {"chapter:2/section:6"}


def test_extract_oracle_sections_excludes_kumottu_tombstone_with_original_version() -> None:
    """Kumottu stubs WITH finlex:originalVersion are also excluded.

    The originalVersion attribute is orthogonal to the kumottu tombstone check:
    some stubs carry it, some don't.  Both must be excluded.
    """
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0"
                 xmlns:finlex="http://data.finlex.fi/schema/finlex">
          <body>
            <chapter>
              <num>5 luku</num>
              <section eId="chp_5__sec_22v20230815" finlex:originalVersion="@20230815" finlex:originalVersionLabel="4.5.2023/815">
                <num>22 §</num>
                <content>
                  <p>22 § on kumottu A:lla <ref>4.5.2023/815</ref>.</p>
                </content>
              </section>
              <section eId="chp_5__sec_23">
                <num>23 §</num>
                <heading>Voimassa</heading>
                <subsection><content><p>live text</p></content></subsection>
              </section>
            </chapter>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert set(oracle_keys) == {"chapter:5/section:23"}


def test_extract_oracle_sections_keeps_2017_519_section_28_live_replacement_subsection() -> None:
    """Corpus pin: 2017/519 oracle 28 § must retain the 2022/1048 live subsection."""
    from lawvm.finland.grafter import get_ground_truth_tree

    oracle = get_ground_truth_tree("2017/519")
    assert oracle is not None
    oracle_keys = extract_oracle_sections(oracle)
    section = oracle_keys["chapter:5/section:28"]
    text = etree.tostring(section, method="text", encoding="unicode")

    assert "Valtiosihteeri voi osallistua johtoryhmän kokoukseen" in text
    assert "Henkilöstön edustajalla on oikeus olla läsnä johtoryhmän kokouksessa" in text


def test_extract_oracle_sections_keeps_future_repeal_overlay_section() -> None:
    """A section whose kumottu notice says 'tulee voimaan <future date>' is a
    future-repeal editorial overlay — NOT yet in force — and must be kept in
    the oracle so downstream comparison tools can diagnose ORACLE_STALE."""
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section eId="sec_11">
              <num>11 §</num>
              <content>
                <p>11 § on kumottu L:lla 5.12.2025/1159, joka tulee voimaan 1.5.2026. Aiempi sanamuoto kuuluu:</p>
              </content>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    # Future repeal overlay must remain so it can be classified as ORACLE_STALE
    assert set(oracle_keys) == {"section:11"}


def test_extract_oracle_sections_excludes_valiaikaisesti_tombstone() -> None:
    """A 'väliaikaisesti' tombstone section (N § oli voimassa väliaikaisesti DATES.) must be filtered.

    Finlex embeds past-tense temporal-expiry notices as section stubs once a
    temporary chapter/section expires.  LawVM correctly expires those sections
    entirely, so the oracle stub would create spurious MISSING divergences.
    """
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <chapter eId="chp_9">
              <num>9 luku</num>
              <section eId="chp_9__sec_63">
                <num>63 §</num>
                <subsection eId="chp_9__sec_63__subsec_1">
                  <content>
                    <p>9 luku, 63 § oli voimassa väliaikaisesti 1.1.2011–31.12.2013.</p>
                  </content>
                </subsection>
              </section>
              <section eId="chp_9__sec_64">
                <num>64 §</num>
                <subsection eId="chp_9__sec_64__subsec_1">
                  <content>
                    <p>9 luku, 64 § oli voimassa väliaikaisesti 1.1.2011–31.12.2013.</p>
                  </content>
                </subsection>
              </section>
            </chapter>
            <section eId="sec_80">
              <num>80 §</num>
              <subsection eId="sec_80__subsec_1">
                <content><p>Laki tulee voimaan 1 päivänä tammikuuta 2011.</p></content>
              </subsection>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert "chapter:9/section:63" not in oracle_keys
    assert "chapter:9/section:64" not in oracle_keys
    assert "section:80" in oracle_keys


def test_extract_oracle_sections_keeps_active_valiaikaisesti_section() -> None:
    """A section marked 'on voimassa väliaikaisesti' (present tense, still active) is NOT filtered."""
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section eId="sec_5">
              <num>5 §</num>
              <subsection eId="sec_5__subsec_1">
                <content><p>Erityinen maksu on 50 euroa.</p></content>
              </subsection>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert "section:5" in oracle_keys


def test_extract_oracle_sections_keeps_short_live_section_with_only_content() -> None:
    """A short live section (only <num>+<content>, no heading/subsection) that does
    NOT contain kumottu notice text must NOT be filtered out."""
    oracle = etree.fromstring(
        """
        <statute xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
          <body>
            <section eId="sec_15">
              <num>15 §</num>
              <content><p>Tätä lakia sovelletaan kunnallisiin sosiaalipalveluihin.</p></content>
            </section>
          </body>
        </statute>
        """
    )

    oracle_keys = extract_oracle_sections(oracle)

    assert set(oracle_keys) == {"section:15"}


def test_reconcile_unique_unscoped_aliases_aligns_unique_extra_part_prefix() -> None:
    replay = {
        "chapter:9a/section:1": "replay-1",
        "chapter:9a/section:2": "replay-2",
    }
    oracle = {
        "part:ii/chapter:9a/section:1": "oracle-1",
        "part:ii/chapter:9a/section:2": "oracle-2",
    }

    aligned_replay, aligned_oracle = reconcile_unique_unscoped_aliases(replay, oracle)

    assert set(aligned_replay) == {
        "part:ii/chapter:9a/section:1",
        "part:ii/chapter:9a/section:2",
    }
    assert set(aligned_oracle) == {
        "part:ii/chapter:9a/section:1",
        "part:ii/chapter:9a/section:2",
    }
