from __future__ import annotations

import json
from pathlib import Path

import pytest

from lawvm.finland import corrigendum as corr
from lawvm.finland import corrigendum_records
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import StructuralAction
from lawvm.tools.section_keys import extract_ir_sections
from tests.corpus_pin_helpers import replay_xml_for_test


def test_patch_table_loads_from_text_corpus(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2013/23/media/corrigenda/sk20160442_1.pdf",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1, johtolause",
                "wrong_text": "18 §:n 4 ja 5 momentti ja 31 § ja",
                "correct_text": "18 §:n 4 ja 5 momentti, 31 §:n 1 momentti sekä",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table.amendment_count() == 1
    ops = table._patches["2016/442"]
    assert len(ops) == 1
    assert ops[0].op_id == "corr/442/2016/0"
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "18 §:n 4 ja 5 momentti ja 31 § ja"
    assert ops[0].text_patch.replacement == "18 §:n 4 ja 5 momentti, 31 §:n 1 momentti sekä"
    assert ops[0].text_patch is not None
    assert ops[0].payload is None


def test_manual_base_source_patch_1986_762_corrects_two_ocr_typos() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrong = (
        "Valtion palvelussuhteen jatkuvuuden turvaaminen voidaan hallitusmuodon "
        "86 §:ssä säädettyjen perusteiden ja muutoin säädettyjen "
        "kelpoisuusvaatimusten ohella ottaa huomioon täytettäessä valtion virkaa, "
        "johon nimittää muu virnaomainen kuin tasavallan presidentti, "
        "valtioneuvosta, korkein oikeus tai korkein hallinto-oikeus."
    )
    correct = wrong.replace("virnaomainen", "viranomainen").replace(
        "valtioneuvosta", "valtioneuvosto"
    )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<akomaNtoso><act><body><section eId="sec_1"><content><p>'
        f"{wrong}"
        "</p></content></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1986/762")

    assert applied == ["body_patch/1986/762/0"]
    assert correct.encode("utf-8") in patched
    assert wrong.encode("utf-8") not in patched


def test_manual_base_source_patch_1965_41_corrects_base_ocr_typos() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrongs = [
        (
            "Milloin laissa tai asetuksessa on viitattu lainkohtaan, jonka sijaan "
            "on tullut uuden perintökaaren säännös, on tätä sen asemasta sovellettava."
        ),
        (
            "Mitä avioliittolain voimaanpanosta annetun lain 6 §:n 2 momentissa "
            "säädetään eräiden ennen avioliittolain voimaantuloa siitettyjen lasten "
            "oikeudellisesta asemasta, on edelleen voimassa myös täilaisten lasten "
            "perintöoikeudesta ja oikeudesta periä tällainen lapsi."
        ),
        "VaLtion oikeutta kuolleen henkilön jäämistöön valvoo valtiokonttori.",
        (
            "Mitä uuden perintökaaren 9 luvun 2 §:ssä säädetään sen hyväksi tehdystä "
            "testamentista, joka testamentin tekijän kuollessa ei vielä ole syntynyt "
            "eikä siitetty, on, jollei erityisistä seikoista muuta johdu, vastaavasti "
            "sovellettava lahjaan tal muuhun sen hyväksi tehtyyn oikeustoimeen, joka "
            "oikeustointa päätettäessä ei vielä ele syntynyt eikä siitetty."
        ),
        (
            "Luovutus konkurssiin tai pesänselvittäjän hallittavaksi käsittää "
            "puolisoiden pesän koko omaisuuden, mikäli puolin oikeudesta omaisuuden "
            "erottamiseen ei muuta johdu. Pesänselvittäjäksi on määrättävä "
            "eloonjäänyt puoliso, jollei erityisiä vastasyitä ole."
        ),
        (
            "Perukirjan on merkittävä puolisoiden pesän omaisuus ja velat. Uuden "
            "perintökaaren 20 luvun 4 §:n 3 momentin säännöstä ennakon merkitsemisestä "
            "perukirjaan on sovellettava myös ennakkoon, joka on annettu yhteisestä "
            "omaisuudesta."
        ),
        (
            "Mitä uuden perintökaaren 21 luvussa sanotaan, on sovellettava puolisoiden "
            "pesän varoihin ja velkoihin. Jos puolisoiden pesän omaisuus laissa "
            "säädetyssä ajassa luovutetaan konkurssiin tai pesänselvittäjän "
            "hallittavaksi, on kuitenkin eloonjääneen puolison velkavastuusta voimassa, "
            "mitä siitä konkurssin varalta on onnen 1 päivää tammikuuta 1930 voimassa "
            "olleessa laissa säädetty. Omaisuuden erilleen ottamisesta konkurssissa on "
            "niin ikään ennen mainittua päivää voimassa ollutta lakia noudatettava."
        ),
        (
            "Omaisuuden osoituksessa saakoon eloonjäänyt puoliso hänelle tulevan "
            "etuosan jakamattomasta pesästä ja sen jälkeen naimaosansa."
        ),
        (
            "Sopimuksen kuolinpesän yhteishallinnosta on, jolloi toisin ole sovittu, "
            "katsottava käsittävän puolisoiden pesän koko omaisuuden."
        ),
    ]
    corrects = [
        wrongs[0].replace("asemasta", "asemesta"),
        wrongs[1].replace("täilaisten", "tällaisten"),
        wrongs[2].replace("VaLtion", "Valtion"),
        wrongs[3].replace("tal", "tai").replace("ele", "ole"),
        wrongs[4].replace("puolin oikeudesta", "puolison oikeudesta"),
        wrongs[5].replace("Perukirjan", "Perukirjaan"),
        wrongs[6].replace("onnen 1 päivää", "ennen 1 päivää"),
        wrongs[7].replace("osoituksessa", "osituksessa"),
        wrongs[8].replace("jolloi", "jollei"),
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><body>"
        + "".join(f"<section><content><p>{wrong}</p></content></section>" for wrong in wrongs)
        + "</body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1965/41")

    assert applied == [f"body_patch/1965/41/{idx}" for idx in range(9)]
    for wrong, correct in zip(wrongs, corrects, strict=True):
        assert wrong.encode("utf-8") not in patched
        assert correct.encode("utf-8") in patched


def test_manual_base_source_patch_1981_494_corrects_owned_source_defects() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrongs = [
        (
            "Valtion on pyrittävä huolehtimaan siitä, että saariston vakinaisella "
            "väestöllä on käytettävissään asumisen, toimeentulon ja välttämättömän "
            "asioina kannalta tarpeelliset liikenne- ja kuljetuspalvelut, sekä siitä, "
            "että nämä palvelut ovat mahdollisimman joustavat ja ilmaiset tai "
            "hinnaltaan kohtuulliset."
        ),
        (
            "Milloin saariston vakinaiselle väestölle korvataan valtion varoista "
            "1 momentissa tarkoitetuista matkoista aiheutuneita kustannuksia, on "
            "vesitse tehty matka otettava huomioon lisäkustannuksena siten kuin "
            "erikseen säädetä."
        ),
        (
            "Edellä 1 momentissa tarkoitetuiksi peruspalveluiksi katsotaan terveys- "
            "ja sosiaalitoimen, koulu- ja kulttuuritoimen, kaupan ja tietoliikenteen "
            "tavanomaiset lakipalvelut sekä sähköenergia."
        ),
        (
            "Valtioneuvosto määrää saaristokunniksi ne kunnat, joissa saaristo-olot "
            "ovat olennaisena esteenä kunnan kehitykselle. Valtioneuvosto voi "
            "erityisestä syystä päättää, että saaristokuntaa koskevia säännöksiä "
            "sovelletaan myös muun"
        ),
    ]
    corrects = [
        wrongs[0].replace("asioina", "asioinnin"),
        wrongs[1].replace("säädetä.", "säädetään."),
        wrongs[2].replace("lakipalvelut", "lähipalvelut"),
        wrongs[3] + " kunnan saaristo-osaan.",
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><body>"
        + "".join(f"<section><content><p>{wrong}</p></content></section>" for wrong in wrongs)
        + "</body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1981/494")

    assert applied == [f"body_patch/1981/494/{idx}" for idx in range(4)]
    for wrong, correct in zip(wrongs[:3], corrects[:3], strict=True):
        assert wrong.encode("utf-8") not in patched
        assert correct.encode("utf-8") in patched
    assert (wrongs[3] + "</p>").encode("utf-8") not in patched
    assert corrects[3].encode("utf-8") in patched


def test_replay_xml_1981_494_applies_owned_source_defects_without_overpatching() -> None:
    replay = replay_xml_for_test(
        "1981/494",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)

    section_5 = " ".join(irnode_to_text(sections["section:5"]).split())
    section_6 = " ".join(irnode_to_text(sections["section:6"]).split())
    section_9 = " ".join(irnode_to_text(sections["section:9"]).split())
    section_10 = " ".join(irnode_to_text(sections["section:10"]).split())

    assert "välttämättömän asioinnin kannalta" in section_5
    assert "siten kuin erikseen säädetään" in section_5
    assert "tavanomaiset lähipalvelut" in section_6
    assert "sovelletaan myös muun kunnan saaristo-osaan" in section_9
    assert "tuen määrään vaikuttavana" in section_10


def test_manual_base_source_patch_1982_182_corrects_section_57_source_defects() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrongs = [
        (
            "merkit 375 (taksiasema-alue), 571 (taajama) ja 572 (taajama päättyy) "
            "on otettava käyttöön heti tämän asetuksen tullessa voi- maan;"
        ),
        (
            "merkit 322 (polkupyörällä ja mopolla ajo kielletty), 323 (jalankulku "
            "kielletty), 416 (pakollinen kiertosuunta), 563 (moottoriliikennetie) "
            "ja 564 (moottoriliikennetie päättyy) on otettava käyttöön vuoden "
            "1983 loppuun men- nessä; sekä"
        ),
        (
            "merkit 312 (moottorikäyttöisellä ajoneuvolla ajo kielletty), 313 "
            "(kuorma- ja pakettiautolla ajo kielletty), 316 (moottoripyörällä ajo "
            "kielletty), 551 (yksisuuntainen tie), 621 ja 622 (ajokaistaopastus) "
            "ja merkki 623 (ajokaistan päättyminen) on otettava käyttöön vuoden"
        ),
        (
            "Aikaisempien määräysten mukaisia ryhmitysmerkkejä voidaan käyttää "
            "merkkien 412-415 (pakollinen ajosuunta) sekä 40 §:n mukaisten "
            "ajokaistanuolten asemesta vuoden 1990 loppuun saakka."
        ),
    ]
    corrects = [
        wrongs[0].replace("voi- maan", "voimaan"),
        wrongs[1].replace("men- nessä", "mennessä"),
        wrongs[2] + " 1990 loppuun mennessä.",
        wrongs[3].replace("412-415", "412–415"),
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><body>"
        + "".join(f"<section><content><p>{wrong}</p></content></section>" for wrong in wrongs)
        + "</body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1982/182")

    assert applied == [f"body_patch/1982/182/{idx}" for idx in range(4)]
    for idx, (wrong, correct) in enumerate(zip(wrongs, corrects, strict=True)):
        if idx == 2:
            assert (wrong + "</p>").encode("utf-8") not in patched
        else:
            assert wrong.encode("utf-8") not in patched
        assert correct.encode("utf-8") in patched


def test_replay_xml_1982_182_applies_owned_section_57_source_defects() -> None:
    replay = replay_xml_for_test(
        "1982/182",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_57 = " ".join(irnode_to_text(sections["section:57"]).split())

    assert "tullessa voimaan" in section_57
    assert "1983 loppuun mennessä" in section_57
    assert "käyttöön vuoden 1990 loppuun mennessä." in section_57
    assert "merkkien 412–415" in section_57


def test_manual_amendment_source_patch_2007_491_fills_missing_fee_rows() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrong_row = """<tr>
                                        <td class="align-left colsep-0 rowsep-0 valign-TOP">
                                            <p>2) hirvenvasa</p>
                                        </td>
                                        <td class="align-left colsep-0 rowsep-0 valign-TOP">
                                            <p>50 euroa</p>
                                        </td>
                                    </tr>"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><body><section><subsection><paragraph><content><table>"
        "<tr><td><p>1) aikuinen hirvi</p></td><td><p>120 euroa</p></td></tr>"
        f"{wrong_row}"
        "</table></content></paragraph></subsection></section></body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "2007/491")

    assert applied == ["body_patch/2007/491/0"]
    assert "3) aikuinen kuusipeura".encode("utf-8") in patched
    assert "4) kuusipeuran, saksanhirven".encode("utf-8") in patched


def test_replay_xml_2001_823_applies_owned_2007_491_fee_row_source_defect() -> None:
    replay = replay_xml_for_test(
        "2001/823",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_2 = " ".join(irnode_to_text(sections["section:2"]).split())

    assert "1) aikuinen hirvi 120 euroa" in section_2
    assert "2) hirvenvasa 50 euroa" in section_2
    assert (
        "3) aikuinen kuusipeura, saksanhirvi, japaninpeura, "
        "valkohäntäpeura tai metsäpeura 17 euroa"
    ) in section_2
    assert (
        "4) kuusipeuran, saksanhirven, japaninpeuran, "
        "valkohäntäpeuran tai metsäpeuran vasa 8 euroa."
    ) in section_2


def test_manual_base_source_patch_1991_1161_fills_empty_section_9_shell() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrong = """<section eId="sec_9">
                        <num>9 § </num>
                    </section>"""
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<akomaNtoso><act><body>{wrong}</body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1991/1161")

    assert applied == ["body_patch/1991/1161/0"]
    assert wrong.encode("utf-8") not in patched
    assert b'eId="sec_9__heading">Voimaantulo' in patched
    assert "Tämä asetus tulee voimaan 1 päivänä lokakuuta 1991.".encode("utf-8") in patched
    assert "täytäntöönpanon edellyttämiin toimenpiteisiin.".encode("utf-8") in patched


def test_replay_xml_1991_1161_applies_owned_section_9_source_defect() -> None:
    replay = replay_xml_for_test(
        "1991/1161",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_9 = " ".join(irnode_to_text(sections["section:9"]).split())

    assert "Voimaantulo" in section_9
    assert "Tämä asetus tulee voimaan 1 päivänä lokakuuta 1991." in section_9
    assert "täytäntöönpanon edellyttämiin toimenpiteisiin" in section_9


def test_manual_base_source_patch_1991_1208_corrects_owned_ocr_defects() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    wrongs = [
        "koko naan luovuttu",
        "Taajamaalueilla luokitus",
        "rantaalueiden tuulisuus",
        "varustet tujen rakennettujen teiden",
        "vastaava pätevyys(<i>veroluokittaja</i>)",
        "vuodelta 1991 toimirerravassa verotuksessa. Asetuksen 17§:ää sovelletan",
        "maatilaatalouden tuloveroastesus",
    ]
    corrects = [
        "kokonaan luovuttu",
        "Taajama-alueilla luokitus",
        "ranta-alueiden tuulisuus",
        "varustettujen rakennettujen teiden",
        "vastaava pätevyys (<i>veroluokittaja</i>)",
        "vuodelta 1991 toimitettavassa verotuksessa. Asetuksen 17 §:ää sovelletaan",
        "maatilatalouden tuloveroasetus",
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><body>"
        + "".join(f"<section><content><p>{wrong}</p></content></section>" for wrong in wrongs)
        + "</body></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_body_xml(xml, "1991/1208")

    assert applied == [f"body_patch/1991/1208/{idx}" for idx in range(7)]
    for wrong, correct in zip(wrongs, corrects, strict=True):
        assert wrong.encode("utf-8") not in patched
        assert correct.encode("utf-8") in patched


def test_replay_xml_1991_1208_applies_owned_ocr_defects() -> None:
    replay = replay_xml_for_test(
        "1991/1208",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    joined = {
        key: " ".join(irnode_to_text(sections[key]).split())
        for key in ("section:2", "section:5", "section:7", "section:8", "section:12", "section:25")
    }

    assert "kokonaan luovuttu" in joined["section:2"]
    assert "koko naan luovuttu" not in joined["section:2"]
    assert "varustettujen rakennettujen teiden" in joined["section:5"]
    assert "varustet tujen rakennettujen teiden" not in joined["section:5"]
    assert "ranta-alueiden tuulisuus" in joined["section:7"]
    assert "rantaalueiden tuulisuus" not in joined["section:7"]
    assert "Taajama-alueilla luokitus" in joined["section:8"]
    assert "Taajamaalueilla luokitus" not in joined["section:8"]
    assert "pätevyys (veroluokittaja)" in joined["section:12"]
    assert "pätevyys(veroluokittaja)" not in joined["section:12"]
    assert "toimitettavassa verotuksessa" in joined["section:25"]
    assert "17 §:ää sovelletaan" in joined["section:25"]
    assert "maatilatalouden tuloveroasetus" in joined["section:25"]
    assert "toimirerravassa" not in joined["section:25"]
    assert "tuloveroastesus" not in joined["section:25"]


def test_manual_source_patches_1992_1578_correct_base_and_amendment_typos() -> None:
    table = corr.CorrigendumPatchTable.load_from_source()
    base_wrong = "konkursissa varojen tilittämiseen"
    amendment_wrong = "ei saada sen vakuutema olevasta pantista"

    base_patched, base_applied = table.patch_source_body_xml(
        f"<body><p>{base_wrong}</p></body>".encode("utf-8"),
        "1992/1578",
    )
    amendment_patched, amendment_applied = table.patch_source_body_xml(
        f"<body><p>{amendment_wrong}</p></body>".encode("utf-8"),
        "1995/1776",
    )

    assert base_applied == ["body_patch/1992/1578/0"]
    assert amendment_applied == ["body_patch/1995/1776/0"]
    assert base_wrong.encode("utf-8") not in base_patched
    assert amendment_wrong.encode("utf-8") not in amendment_patched
    assert "konkurssissa varojen tilittämiseen".encode("utf-8") in base_patched
    assert "ei saada sen vakuutena olevasta pantista".encode("utf-8") in amendment_patched


def test_replay_xml_1992_1578_applies_owned_source_typos() -> None:
    replay = replay_xml_for_test(
        "1992/1578",
        mode="official_consolidation",
        quiet=True,
        oracle_selector=ConsolidatedArtifactSelector.bench_comparable(),
    )
    sections = extract_ir_sections(replay.materialized_state.ir)
    section_3 = " ".join(irnode_to_text(sections["section:3"]).split())
    section_9 = " ".join(irnode_to_text(sections["section:9"]).split())

    assert "konkurssissa varojen tilittämiseen" in section_3
    assert "konkursissa varojen tilittämiseen" not in section_3
    assert "ei saada sen vakuutena olevasta pantista" in section_9
    assert "ei saada sen vakuutema olevasta pantista" not in section_9


def test_patch_table_keeps_johtolauseen_jalkeen_in_body_patch_lane(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "official#0",
                "source_pdf": "x",
                "statute_id": "2011/715",
                "amendment_id": "33/2024",
                "lang": "fi",
                "correction_index": 1,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, johtolauseen jälkeen",
                "wrong_text": "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
                "correct_text": "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert "2024/33" not in table._patches
    assert table._body_patches["2024/33"] == [
        (
            "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "Sivulla 1, johtolauseen jälkeen",
        )
    ]


def test_patch_table_routes_section_num_before_heading_corrigendum_to_body_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "official#0",
                "source_pdf": "x",
                "statute_id": "1990/848",
                "amendment_id": "377/2017",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivulla 1, pykälän otsikon edellä",
                "wrong_text": "5 §.",
                "correct_text": "35 §",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert "2017/377" not in table._patches
    assert table._body_patches["2017/377"] == [
        ("5 §.", "35 §", "Sivulla 1, pykälän otsikon edellä")
    ]


def test_patch_table_dedupes_whitespace_variant_section_num_corrigenda(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    rows = [
        {
            "stable_id": "official#0",
            "source_pdf": "x",
            "statute_id": "2003/359",
            "amendment_id": "359/2003",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 1792, pykälän numero",
            "wrong_text": "9§",
            "correct_text": "59 §",
            "parse_error": None,
        },
        {
            "stable_id": "official#1",
            "source_pdf": "x",
            "statute_id": "2003/359",
            "amendment_id": "359/2003",
            "lang": "fi",
            "correction_index": 1,
            "correction_type": "johtolause",
            "location_desc": "Sivulla 1792, oikea palsta, pykälän numero",
            "wrong_text": "9 §",
            "correct_text": "59 §",
            "parse_error": None,
        },
    ]
    records_path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._body_patches["2003/359"] == [
        ("9§", "59 §", "Sivulla 1792, pykälän numero")
    ]


def test_patch_source_body_xml_corrects_unique_section_num_before_heading() -> None:
    xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body>
  <section>
    <num>5 \xc2\xa7</num>
    <heading>Vahingonkorvausvastuu</heading>
    <hcontainer name="omission"/>
    <subsection><content><p>uusi kolmas momentti</p></content></subsection>
  </section>
</body></act></akomaNtoso>"""
    table = corr.CorrigendumPatchTable()
    table._body_patches["2017/377"] = [
        ("5 §.", "35 §", "Sivulla 1, pykälän otsikon edellä")
    ]
    corr.clear_misapplied_records()

    patched, applied = table.patch_source_body_xml(xml, "2017/377")

    assert applied == ["body_patch/2017/377/0"]
    assert b"<num>35 \xc2\xa7</num>" in patched
    assert b"<num>5 \xc2\xa7</num>" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_body_xml_uses_sibling_sequence_for_ambiguous_section_num() -> None:
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body>
  <chapter eId="chp_2"><num>2 luku</num>
    <section eId="chp_2__sec_9"><num>9 §</num><heading>Lapsi</heading></section>
    <section eId="chp_2__sec_10"><num>10 §</num><heading>Adoptiolapsi</heading></section>
  </chapter>
  <chapter eId="chp_9"><num>9 luku</num>
    <section eId="chp_9__sec_58"><num>58 §</num><heading>Ilmoitus</heading></section>
    <section eId="chp_9__sec_9"><num>9 §</num><heading>Alle 12-vuotiaan ottolapsen kansalaisuusilmoitus</heading></section>
    <section eId="chp_9__sec_60"><num>60 §</num><heading>Määräaika</heading></section>
  </chapter>
</body></act></akomaNtoso>""".encode("utf-8")
    table = corr.CorrigendumPatchTable()
    table._body_patches["2003/359"] = [
        ("9§", "59 §", "Sivulla 1792, pykälän numero")
    ]
    corr.clear_misapplied_records()

    patched, applied = table.patch_source_body_xml(xml, "2003/359")

    assert applied == ["body_patch/2003/359/0"]
    assert '<section eId="chp_2__sec_9"><num>9 §</num>'.encode("utf-8") in patched
    assert '<section eId="chp_9__sec_9"><num>59 §</num>'.encode("utf-8") in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_body_xml_rejects_ambiguous_section_num_without_sequence_witness() -> None:
    xml = """\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body>
  <chapter><num>2 luku</num>
    <section><num>9 §</num></section>
    <section><num>10 §</num></section>
  </chapter>
  <chapter><num>9 luku</num>
    <section><num>9 §</num></section>
    <section><num>61 §</num></section>
  </chapter>
</body></act></akomaNtoso>""".encode("utf-8")
    table = corr.CorrigendumPatchTable()
    table._body_patches["2003/359"] = [
        ("9§", "59 §", "Sivulla 1792, pykälän numero")
    ]
    corr.clear_misapplied_records()

    patched, applied = table.patch_source_body_xml(xml, "2003/359")

    assert applied == []
    assert patched == xml
    assert corr.get_misapplied_records()[0]["reason"] == "miss"


def test_patch_table_preserves_unsupported_table_corrections(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "table",
                "location_desc": "Sivu 2, taulukko 1",
                "wrong_text": "1 | old",
                "correct_text": "1 | new",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._patches == {}
    assert table._body_patches == {}
    assert len(table._unsupported_patches) == 1
    assert table._unsupported_patches[0]["reason"] == "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED"
    assert table._unsupported_patches[0]["correction_type"] == "table"
    assert table.unsupported_patches() == (
        {
            "reason": "FINLAND.CORRIGENDUM_TABLE_UNSUPPORTED",
            "amendment_id": "2016/442",
            "source_amendment_id": "442/2016",
            "statute_id": "2013/23",
            "correction_type": "table",
            "location_desc": "Sivu 2, taulukko 1",
            "wrong_text": "1 | old",
            "correct_text": "1 | new",
        },
    )


def test_load_from_source_routes_prose_johtolause_location_to_johtolause_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "akn/fi/act/statute-consolidated/2012/980/media/corrigenda/sk20220604_1.pdf#0",
                "source_pdf": "akn/fi/act/statute-consolidated/2012/980/media/corrigenda/sk20220604_1.pdf",
                "statute_id": "2012/980",
                "amendment_id": "604/2022",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, lain johtolauseessa",
                "wrong_text": "2 §:n 2 momentti ja 9 §",
                "correct_text": "2 §:n 3 momentti ja 9 §",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert "2022/604" in table._patches
    assert "2022/604" not in table._body_patches
    op = table._patches["2022/604"][0]
    assert op.target == corr.LegalAddress(path=(("johtolause", ""),))
    assert op.text_patch is not None
    assert op.text_patch.selector.match_text == "2 §:n 2 momentti ja 9 §"
    assert op.text_patch.replacement == "2 §:n 3 momentti ja 9 §"


def test_load_from_source_skips_manual_expanded_duplicate_johtolause_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records = [
        {
            "stable_id": "official#0",
            "source_pdf": "x",
            "statute_id": "2014/1194",
            "amendment_id": "821/2017",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "johtolause",
            "location_desc": "Sivulla 1, johtolauseessa",
            "wrong_text": "… 6 luvun otsikko, 1 § sekä 1 §:n otsikko ja 1, 2 ja 5 momentti sekä…",
            "correct_text": "… 6 luvun otsikko, 1 §:n otsikko ja 1, 2 ja 5 momentti sekä…",
            "extraction_source": "both+vision",
            "parse_error": None,
        },
        {
            "stable_id": "expanded#3013",
            "source_pdf": "unknown",
            "statute_id": "2014/1194",
            "amendment_id": "2017/821",
            "lang": "fi",
            "correction_index": 3013,
            "correction_type": "johtolause",
            "location_desc": "",
            "wrong_text": "6 luvun otsikko, 1 § sekä 1 §:n otsikko ja 1, 2 ja 5 momentti sekä",
            "correct_text": "6 luvun otsikko, 1 §:n otsikko ja 1, 2 ja 5 momentti sekä",
            "extraction_source": "manual_expanded",
            "parse_error": None,
        },
    ]
    records_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table.amendment_count() == 1
    ops = table._patches["2017/821"]
    assert len(ops) == 1
    assert ops[0].op_id == "corr/821/2017/0"


def test_parse_corrigendum_populates_structured_text_replace_fields() -> None:
    pdf_text = (
        "Oikaisuja Suomen säädöskokoelmaan\n\n"
        "Suomen säädöskokoelmaan n:o 442/2016\n"
        "Sivulla 1, johtolause on:\n"
        "väärä teksti\n"
        "Pitää olla:\n"
        "oikea teksti\n"
    )

    ops = corr.parse_corrigendum(pdf_text, "442/2016")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "väärä teksti"
    assert ops[0].text_patch.replacement == "oikea teksti"
    assert ops[0].text_patch is not None
    assert ops[0].payload is None


def test_parse_corrigendum_preserves_unsupported_add_blocks() -> None:
    pdf_text = (
        "Oikaisuja Suomen säädöskokoelmaan\n\n"
        "Suomen säädöskokoelmaan n:o 442/2016\n"
        "Sivulla 1, johtolauseesta puuttuu virke, joka kuuluu:\n"
        "lisätty teksti\n"
    )

    result = corr.parse_corrigendum(pdf_text, "442/2016")

    assert len(result) == 0
    assert len(result.unsupported_patches) == 1
    assert result.unsupported_patches[0].reason == "FINLAND.CORRIGENDUM_ADD_UNSUPPORTED"
    assert result.unsupported_patches[0].correction_kind == "ADD"
    assert result.unsupported_patches[0].correct_text == "lisätty teksti"


def test_extract_inline_corrections_strips_only_corrigendum_authorial_notes() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">
  <act>
    <body>
      <section eId="sec_1">
        <num>1 §</num>
        <content>
          <span class="corrigendum">oikea teksti
            <authorialNote>
              <p>Alkuperainen teksti.</p>
              <p>vaara teksti</p>
            </authorialNote>
          </span>
        </content>
      </section>
      <hcontainer name="editorial">
        <authorialNote>
          <p>legitimate note outside corrigendum</p>
        </authorialNote>
      </hcontainer>
    </body>
  </act>
</akomaNtoso>
""".encode("utf-8")

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")

    assert len(ops) == 1
    assert b"vaara teksti" not in cleaned
    assert b"legitimate note outside corrigendum" in cleaned


def test_extract_inline_corrections_records_missing_authorial_note() -> None:
    corr.clear_misapplied_records()
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_1"><content><span class="corrigendum">oikea teksti</span></content></section></body></act></akomaNtoso>"""

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")
    records = corr.get_misapplied_records()

    assert ops == []
    assert b"oikea teksti" in cleaned
    assert records[-1]["reason"] == "FINLAND.INLINE_CORRIGENDUM_MISSING_AUTHORIAL_NOTE"


def test_extract_inline_corrections_records_missing_wrong_text() -> None:
    corr.clear_misapplied_records()
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_1"><content><span class="corrigendum">oikea teksti<authorialNote><p>Merkitty kohta oikaistu (v. 2001).</p></authorialNote></span></content></section></body></act></akomaNtoso>"""

    ops, cleaned = corr.extract_inline_corrections(xml, "2000/1")
    records = corr.get_misapplied_records()

    assert ops == []
    assert b"authorialNote" not in cleaned
    assert b"oikea teksti" in cleaned
    assert records[-1]["reason"] == "FINLAND.INLINE_CORRIGENDUM_MISSING_WRONG_TEXT"


def test_apply_text_replace_with_mode_exact() -> None:
    patched, mode = corr._apply_text_replace_with_mode(
        b"<body><p>vaara teksti</p></body>",
        "vaara teksti",
        "oikea teksti",
    )

    assert mode == "exact"
    assert b"oikea teksti" in patched


def test_apply_text_replace_with_mode_tag_tolerant() -> None:
    patched, mode = corr._apply_text_replace_with_mode(
        b"<body><p>alpha <ref>beta</ref> gamma delta</p></body>",
        "alpha beta gamma delta",
        "korjattu teksti",
    )

    assert mode == "tag_tolerant"
    assert b"korjattu teksti" in patched


def test_apply_text_replace_deterministic_does_not_use_fuzzy_recovery() -> None:
    original = b"<body><p>alpha beta gamma delta</p></body>"
    patched, mode = corr._apply_text_replace_deterministic(
        original,
        "alpha beta gammb delta",
        "korjattu teksti",
    )

    assert mode is None
    assert patched == original


def test_apply_text_replace_fuzzy_uses_later_anchor_when_first_token_absent() -> None:
    original = (
        "<body><p>"
        + ("taustateksti " * 500)
        + "jos ajoneuvo täyttää kaikkien auton mallivuotta koskevien "
        "FMVSS-standardien vaatimukset, sitä pidetään hyväksyttävänä."
        + (" loppu " * 500)
        + "</p></body>"
    ).encode("utf-8")

    patched, mode = corr._apply_text_replace_with_mode(
        original,
        "xxxjos ajoneuvo täyttää kaikkien auton mallivuotta koskevien "
        "FMVSS-standardien vaatimukset, sitä pidetään hyväksyttävänä.",
        "jos ajoneuvo täyttää uuden tarkistetun vaatimuksen.",
    )

    assert mode == "fuzzy_window"
    assert "uuden tarkistetun vaatimuksen".encode("utf-8") in patched


def test_apply_text_replace_fuzzy_large_no_anchor_skips_sequence_matcher(monkeypatch) -> None:
    class _ForbiddenSequenceMatcher:
        def __init__(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("large no-anchor fuzzy scan must be skipped")

    monkeypatch.setattr(corr.difflib, "SequenceMatcher", _ForbiddenSequenceMatcher)
    original = ("<body><p>" + ("taustateksti " * 1000) + "</p></body>").encode("utf-8")

    patched, mode = corr._apply_text_replace_with_mode(
        original,
        "puuttuva ankkuri jota dokumentissa ei esiinny",
        "korjattu teksti",
    )

    assert mode is None
    assert patched == original


def test_load_patch_records_merges_official_and_adjudication_files(tmp_path: Path) -> None:
    official_path = tmp_path / "corrigendum_official_fi.jsonl"
    adjudication_path = tmp_path / "corrigendum_adjudications_fi.jsonl"
    official_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1",
                "wrong_text": "wrong",
                "correct_text": "correct",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    adjudication_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "verified_in_source": 0,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    rows = corrigendum_records.load_patch_records(official_path)

    assert len(rows) == 1
    assert rows[0]["stable_id"] == "x#0"
    assert rows[0]["verified_in_source"] == 0
    assert rows[0]["wrong_text"] == "wrong"


def test_load_patch_records_does_not_implicitly_fallback_to_sqlite(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(corrigendum_records, "_OFFICIAL_JSONL", tmp_path / "missing_official.jsonl")
    monkeypatch.setattr(corrigendum_records, "_ADJUDICATIONS_JSONL", tmp_path / "missing_adjudications.jsonl")

    rows = corrigendum_records.load_patch_records()

    assert rows == []


# ---------------------------------------------------------------------------
# _parse_location_section tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("location_desc, expected_sec, expected_subsec", [
    # "2 ja 3 rivi" means rows 2 and 3, not subsection — no 'moment' keyword → subsec=None
    ("Sivulla 2707, 32 §:n 2 ja 3 rivi", "32", None),
    ("Sivulla 4455, 24 §:n 2 momentti", "24", "2"),
    ("Sivulla 12, 15 a §:ssä", "15a", None),
    ("Sivulla 8, 6 §:n 2 momentissa", "6", "2"),
    ("Sivulla 5, 3 §:n 1 momentin 3 kohdassa", "3", "1"),
    ("Sivulla 1772, 5 §:n 4 a kohta", "5", None),  # no 'moment' keyword
    ("Sivulla 3, 17 c §:n 2 momentin riveillä 2-4", "17c", "2"),
    ("Sivulla 4515, 15 b §:n 1 momentti", "15b", "1"),
    # No section at all
    ("Sivulla 1, johtolause", None, None),
])
def test_parse_location_section(
    location_desc: str, expected_sec: str | None, expected_subsec: str | None
) -> None:
    sec, subsec = corr._parse_location_section(location_desc)
    assert sec == expected_sec
    assert subsec == expected_subsec


# ---------------------------------------------------------------------------
# _find_element_range tests
# ---------------------------------------------------------------------------

_SAMPLE_BODY_XML = b"""\
<body>
  <section eId="sec_6">
    <subsection eId="sec_6__subsec_1"><content><p>subsec 1 text</p></content></subsection>
    <subsection eId="sec_6__subsec_2"><content><p>subsec 2 old text</p></content></subsection>
  </section>
  <section eId="sec_15a">
    <subsection eId="sec_15a__subsec_1"><content><p>15a content</p></content></subsection>
  </section>
  <section eId="chp_3__sec_10">
    <subsection eId="chp_3__sec_10__subsec_1"><content><p>chapter-prefixed content</p></content></subsection>
  </section>
</body>"""


def test_find_element_range_section_only() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "15a", None)
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"15a content" in chunk
    assert b"sec_6" not in chunk


def test_find_element_range_section_with_subsec() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "6", "2")
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"subsec 2 old text" in chunk
    assert b"subsec 1 text" not in chunk


def test_find_element_range_chapter_prefixed() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "10", "1")
    assert result is not None
    start, end = result
    chunk = _SAMPLE_BODY_XML[start:end]
    assert b"chapter-prefixed content" in chunk


def test_find_element_range_missing_returns_none() -> None:
    result = corr._find_element_range(_SAMPLE_BODY_XML, "99", None)
    assert result is None


# ---------------------------------------------------------------------------
# patch_source_body_xml blocked location-scoped retry test
# ---------------------------------------------------------------------------

def test_patch_source_body_xml_blocks_location_scoped_retry(tmp_path: Path, monkeypatch) -> None:
    """Failed full-body replace must stay failed instead of retrying on a guessed section."""
    xml = b"""\
<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body>
  <section eId="sec_6">
    <subsection eId="sec_6__subsec_1"><content><p>other content</p></content></subsection>
    <subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection>
  </section>
  <section eId="sec_7">
    <subsection eId="sec_7__subsec_1"><content><p>irrelevant</p></content></subsection>
  </section>
</body></act></akomaNtoso>"""

    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps({
            "stable_id": "x#0",
            "source_pdf": "x",
            "statute_id": "2005/671",
            "amendment_id": "671/2000",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 1772, 6 §:n 2 momentissa",
            "wrong_text": "old text here",
            "correct_text": "new text here",
            "parse_error": None,
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    corr.clear_misapplied_records()

    original_apply = corr._apply_text_replace
    calls = {"n": 0}

    def _patched_apply(xml_bytes: bytes, wrong: str, correct: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return xml_bytes, False
        return original_apply(xml_bytes, wrong, correct)

    monkeypatch.setattr(corr, "_apply_text_replace", _patched_apply)

    # Confirm location_desc stored in tuple
    patches = table._body_patches.get("2000/671", [])
    assert len(patches) == 1
    assert patches[0][2] == "Sivulla 1772, 6 §:n 2 momentissa"

    patched, applied = table.patch_source_body_xml(xml, "2000/671")
    records = corr.get_misapplied_records()
    assert calls["n"] == 1
    assert applied == []
    assert patched == xml
    blocked = next(
        record for record in records if record["reason"] == "FINLAND.CORRIGENDUM_BODY_LOCATION_FALLBACK_BLOCKED"
    )
    assert blocked["section"] == "6"
    assert blocked["subsection"] == "2"


def test_patch_source_body_xml_full_body_success_still_applies(tmp_path: Path, monkeypatch) -> None:
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_6"><subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection></section></body></act></akomaNtoso>"""
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records_path.write_text(
        json.dumps({
            "stable_id": "x#0",
            "source_pdf": "x",
            "statute_id": "2005/671",
            "amendment_id": "671/2000",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 1772, 6 §:n 2 momentissa",
            "wrong_text": "old text here",
            "correct_text": "new text here",
            "parse_error": None,
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    corr.clear_misapplied_records()

    patched, applied = table.patch_source_body_xml(xml, "2000/671")

    assert applied == ["body_patch/2000/671/0"]
    assert b"new text here" in patched
    assert b"old text here" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_records_invalid_candidate_xml() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2016/442"] = "2013/23"
    table._patches["2016/442"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/442/2016/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text="vaara teksti",
            correct_text="<broken",
            source=corr.OperationSource(
                statute_id="corr/442/2016",
                raw_text="Sivulla 1, johtolause",
                corrected_by="442/2016",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><preface><preamble><block name="substitutions"><p>vaara teksti</p></block></preamble></preface><body><section eId="sec_1"><num>1 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2016/442")
    records = corr.get_misapplied_records()

    assert patched == xml
    assert applied == []
    assert records[-1]["reason"] == "post_patch_xml_invalid"
    assert records[-1]["op_id"] == "corr/442/2016/0"


def test_patch_source_xml_recovers_with_single_text_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2013/426"] = "2010/297"
    table._patches["2013/426"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/426/2013/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseen rivillä 2", "johtolause"),
            wrong_text="muutetaan maksulaitoslain (297/2010) 21 a §, 37 §:n 2 momentti, 46 §:n 3 momentti ja",
            correct_text="muutetaan maksulaitoslain (297/2010) 21 a §, 37 §:n 3 momentti, 46 §:n 3 momentti ja",
            source=corr.OperationSource(
                statute_id="corr/426/2013",
                raw_text="Sivulla 1, johtolauseen rivillä 2",
                corrected_by="426/2013",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i> maksulaitoslain (<affectedDocument href="/akn/fi/act/statute/2010/297">297/2010</affectedDocument>) 21 a \xc2\xa7, 37 \xc2\xa7:n 2 momentti, 46 \xc2\xa7:n 3 momentti ja 47 \xc2\xa7:n 2 momentti, sellaisena kuin niista on 21 a \xc2\xa7 laeissa 899/2011 ja 764/2012, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_37"><num>37 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2013/426")

    assert applied == ["corr/426/2013/0"]
    assert b"37 \xc2\xa7:n 3 momentti" in patched
    assert b"37 \xc2\xa7:n 2 momentti" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_with_whitespace_tolerant_single_tail_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2022/642"] = "2005/390"
    table._patches["2022/642"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/642/2022/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text=(
                "muutetaan vaarallisten kemikaalien ja räjähteiden käsittelyn turvallisuudesta annetun lain (390/2005) 6\n"
                "§:n 21 kohta sekä 126 ja 131 §, sellaisina kuin niistä ovat 6 §:n 21 kohta laissa 358/2015 ja 126 § laissa\n"
                "795/2020, seuraavasti:"
            ),
            correct_text=(
                "muutetaan vaarallisten kemikaalien ja räjähteiden käsittelyn turvallisuudesta annetun lain (390/2005) 6\n"
                "§:n 21 kohta ja 131 §, sellaisena kuin niistä on 6 §:n 21 kohta laissa 358/2015, sekä\n"
                "lisätään 126 §:ään, sellaisena kuin se on laissa 795/2020, uusi 2 ja 3 momentti seuraavasti:"
            ),
            source=corr.OperationSource(
                statute_id="corr/642/2022",
                raw_text="Sivulla 1, johtolause",
                corrected_by="642/2022",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i>
 vaarallisten kemikaalien ja rajahteiden kasittelyn turvallisuudesta annetun lain (<affectedDocument href="/akn/fi/act/statute/2005/390">390/2005</affectedDocument>)
 ) 6 \xc2\xa7:n 21 kohta sek\xc3\xa4 126 ja 131 \xc2\xa7, sellaisina kuin niist\xc3\xa4 ovat 6 \xc2\xa7:n 21 kohta laissa 358/2015 ja 126 \xc2\xa7 laissa 795/2020, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_126"><num>126 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2022/642")

    assert applied == ["corr/642/2022/0"]
    assert b"lis\xc3\xa4t\xc3\xa4\xc3\xa4n 126 \xc2\xa7:\xc3\xa4\xc3\xa4n" in patched
    assert b"sek\xc3\xa4 126 ja 131 \xc2\xa7" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_insertion_only_single_text_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2019/979"] = "2017/519"
    table._patches["2019/979"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/979/2019/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text=(
                "muutetaan 14, 20, 28, 29 ja 52 §, näistä 28, 29 ja 52 § "
                "sellaisina kuin ne ovat asetuksessa 1158/2017,"
            ),
            correct_text=(
                "muutetaan 14, 15, 20, 28, 29 ja 52 §, näistä 28, 29 ja 52 § "
                "sellaisina kuin ne ovat asetuksessa 1158/2017,"
            ),
            source=corr.OperationSource(
                statute_id="corr/979/2019",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="979/2019",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Sosiaali- ja terveysministeri\xc3\xb6n p\xc3\xa4\xc3\xa4t\xc3\xb6ksen mukaisesti</p><blockContainer><block name="insertions"><i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i> lakiiin uusi 10 \xc2\xa7, jolloin nykyinen 10 \xc2\xa7 siirtyy 10 a \xc2\xa7:ksi, sek\xc3\xa4</block></blockContainer><blockContainer><block name="substitutions"><i>muutetaan</i> 14, 20, 28, 29 ja 52 \xc2\xa7, n\xc3\xa4ist\xc3\xa4 28, 29 ja 52 \xc2\xa7 sellaisina kuin ne ovat asetuksessa 1158/2017, seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_14"><num>14 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2019/979")

    assert applied == ["corr/979/2019/0"]
    assert b"14, 15, 20, 28, 29 ja 52" in patched
    assert b"14, 20, 28, 29 ja 52" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_with_visible_text_delta_single_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2024/33"] = "2011/715"
    table._patches["2024/33"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/33/2024/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text=(
                "lisätään luvan saaneista oikeudenkäyntiavustajista annettuun lakiin "
                "(715/2011) uusi 5 b § seuraavasti:"
            ),
            correct_text=(
                "lisätään luvan saaneista oikeudenkäyntiavustajista annettuun lakiin "
                "(715/2011) uusi 5 a § seuraavasti:"
            ),
            source=corr.OperationSource(
                statute_id="corr/33/2024",
                raw_text="Sivulla 1, johtolause",
                corrected_by="33/2024",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="insertions"><i>lisataan</i> luvan saaneista oikeudenkayntiavustajista annettuun lakiin (<affectedDocument href="/akn/fi/act/statute/2011/715">715/2011</affectedDocument>) uusi 5 b \xc2\xa7 seuraavasti:</block></blockContainer></formula></preamble><body><section><num>5 b \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2024/33")

    assert applied == ["corr/33/2024/0"]
    assert b"uusi 5 a \xc2\xa7 seuraavasti:" in patched
    assert b"uusi 5 b \xc2\xa7 seuraavasti:" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_preserves_later_insertions_when_inserting_into_johtolause() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2022/283"] = "2016/549"
    wrong = (
        "muutetaan tupakkalain (549/2016) 95 §, 96 §:n 1 momentti, "
        "97 §:n 1 momentin 8 kohta ja 117 §,"
    )
    correct = (
        "muutetaan tupakkalain (549/2016) 95 §, 96 §:n otsikko ja 1 momentti, "
        "97 §:n 1 momentin 8 kohta ja 117 §,"
    )
    table._patches["2022/283"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/283/2022/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text=wrong,
            correct_text=correct,
            source=corr.OperationSource(
                statute_id="corr/283/2022",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="283/2022",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><blockContainer><block name="substitutions">muutetaan tupakkalain (<affectedDocument href="/akn/fi/act/statute/2016/549">549/2016</affectedDocument>) 95 \xc2\xa7, 96 \xc2\xa7:n 1 momentti, 97 \xc2\xa7:n 1 momentin 8 kohta ja 117 \xc2\xa7,</block></blockContainer><blockContainer><block name="insertions">lis\xc3\xa4t\xc3\xa4\xc3\xa4n 32 \xc2\xa7:\xc3\xa4\xc3\xa4n uusi 4 ja 5 momentti sek\xc3\xa4 lakiin uusi 35 a \xc2\xa7 seuraavasti:</block></blockContainer></formula></preamble><body/></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2022/283")

    assert applied == ["corr/283/2022/0"]
    assert b"96 \xc2\xa7:n otsikko ja 1 momentti" in patched
    assert b"uusi 4 ja 5 momentti" in patched
    assert b"uusiotsikko" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_recovers_single_ellipsis_witness_against_visible_johtolause() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2025/854"] = "2013/599"
    table._patches["2025/854"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/854/2025/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolauseessa", "johtolause"),
            wrong_text="lisätään 5 §:n 1 momenttiin … uusi 1 kohta seuraavasti:",
            correct_text="lisätään 5 §:n 1 momenttiin … uusi 17 kohta seuraavasti:",
            source=corr.OperationSource(
                statute_id="corr/854/2025",
                raw_text="Sivulla 1, johtolauseessa",
                corrected_by="854/2025",
            ),
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><preamble><formula name="enactingClause"><p>Eduskunnan paatoksen mukaisesti</p><blockContainer><block name="substitutions"><i>muutetaan</i> kemikaalilain (<affectedDocument href="/akn/fi/act/statute/2013/599">599/2013</affectedDocument>) 5 \xc2\xa7:n 1 momentin 16 kohta, sellaisena kuin se on laissa 193/2025, ja</block></blockContainer><blockContainer><block name="insertions"><i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i> 5 \xc2\xa7:n 1 momenttiin, sellaisena kuin se on osaksi laeissa 554/2014, 711/2020, 547/2023 ja 193/2025, uusi 1 kohta seuraavasti:</block></blockContainer></formula></preamble><body><section eId="sec_5"><num>5 \xc2\xa7</num></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_xml(xml, "2025/854")

    assert applied == ["corr/854/2025/0"]
    assert b"uusi 17 kohta seuraavasti:" in patched
    assert b"uusi 1 kohta seuraavasti:" not in patched
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_strips_paired_context_ellipsis_witness() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2017/821"] = "2014/1194"
    table._patches["2017/821"] = [
        corr._corrigendum_text_replace_op(
            op_id="corr/821/2017/0",
            sequence=0,
            target=corr._location_to_address("Sivulla 1, johtolause", "johtolause"),
            wrong_text="… 6 luvun otsikko, 1 § sekä 1 §:n otsikko …",
            correct_text="… 6 luvun otsikko, 1 §:n otsikko …",
            source=corr.OperationSource(
                statute_id="corr/821/2017",
                raw_text="Sivulla 1, johtolause",
                corrected_by="821/2017",
            ),
        )
    ]
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        "<akomaNtoso><act><preamble><formula name=\"enactingClause\">"
        '<blockContainer><block name="substitutions">'
        "muutetaan lain 6 luvun otsikko, 1 § sekä 1 §:n otsikko ja 2 § seuraavasti:"
        "</block></blockContainer></formula></preamble><body/></act></akomaNtoso>"
    ).encode("utf-8")

    patched, applied = table.patch_source_xml(xml, "2017/821")

    assert applied == ["corr/821/2017/0"]
    patched_text = patched.decode("utf-8")
    assert "6 luvun otsikko, 1 §:n otsikko ja 2 §" in patched_text
    assert "1 § sekä 1 §:n otsikko" not in patched_text
    assert "…" not in patched_text
    assert "..." not in patched_text
    assert corr.get_misapplied_records() == []


def test_patch_source_xml_2021_669_preserves_full_johtolause_after_context_corrigenda() -> None:
    from lawvm.corpus_store import get_corpus_store
    from lawvm.finland.johtolause.api import parse_clause
    from lawvm.finland.metadata import get_johtolause

    corr.clear_misapplied_records()
    xml = get_corpus_store().read_source("2021/669")
    assert xml is not None
    patched, applied = corr.get_patch_table().patch_source_xml(xml, "2021/669")
    johtolause = get_johtolause(patched)

    assert applied == [
        "retry/2021/669/0",
        "retry/2021/669/1",
        "corr/2021/669/0",
        "corr/2021/669/3",
        "corr/2021/669/4",
    ]
    assert "..." not in johtolause
    assert "…" not in johtolause
    assert "11 a, 12, 13, 14 ja 15 §" in johtolause
    assert "7 a luvun 1–5 §" in johtolause
    assert "7 lukuun uusi 12 a §" in johtolause

    parsed = parse_clause(johtolause, statute_id="2009/1672")
    op_codes = {op.code() for op in parsed.parsed_ops}
    assert "M L 9 o" in op_codes
    assert {"M P L:9 1", "M P L:9 8", "M P L:10 1", "M P L:10 5"} <= op_codes


def test_apply_visible_text_delta_multi_slot_recovers_two_slot_johtolause_corrigendum() -> None:
    fragment = b"""
<p>Eduskunnan paatoksen mukaisesti</p>
<blockContainer>
  <block name="repeals"><i>kumotaan</i> ik\xc3\xa4\xc3\xa4ntyneen v\xc3\xa4est\xc3\xb6n toimintakyvyn tukemisesta sek\xc3\xa4 i\xc3\xa4kk\xc3\xa4iden sosiaali- ja terveyspalveluista annetun lain (<affectedDocument href="/akn/fi/act/statute/2012/980">980/2012</affectedDocument>) 2 \xc2\xa7:n 2 momentti ja 9 \xc2\xa7, </block>
  <block name="repeals-originals">sellaisena kuin niist\xc3\xa4 on 2 \xc2\xa7:n 2 momentti laissa 267/2015,</block>
</blockContainer>
"""
    wrong = (
        "kumotaan ikääntyneen väestön toimintakyvyn tukemisesta sekä iäkkäiden sosiaali- ja\n"
        "terveyspalveluista annetun lain (980/2012) 2 §:n 2 momentti ja 9 §,\n"
        "sellaisena kuin niistä on 2 §:n 2 momentti laissa 267/2015"
    )
    correct = (
        "kumotaan ikääntyneen väestön toimintakyvyn tukemisesta sekä iäkkäiden sosiaali- ja\n"
        "terveyspalveluista annetun lain (980/2012) 2 §:n 3 momentti ja 9 §,\n"
        "sellaisena kuin niistä on 2 §:n 3 momentti laissa 267/2015"
    )

    patched, ok = corr._apply_visible_text_delta_multi_slot(fragment, wrong, correct)

    assert ok is True
    assert b"2 \xc2\xa7:n 3 momentti ja 9 \xc2\xa7" in patched
    assert b"2 \xc2\xa7:n 3 momentti laissa 267/2015" in patched
    assert b"2 \xc2\xa7:n 2 momentti ja 9 \xc2\xa7" not in patched


def test_apply_visible_text_delta_multi_slot_preserves_three_block_johtolause_corrigendum() -> None:
    from lawvm.finland.corpus import get_corpus

    xml_bytes = get_corpus().read_source("2018/306")
    assert xml_bytes is not None
    start, end = corr._johtolause_byte_range(xml_bytes)
    fragment = xml_bytes[start:end]
    wrong = (
        "muutetaan 3 §:n 6 ja 30 kohta, 27 a §:n 1 momentin 7 kohta ja 94 §,\n"
        "sellaisina kuin ne ovat, 3 §:n 6 kohta laissa 226/2009, 3 §:n 30 kohta ja 27 a §:n\n"
        "1 momentin 7 kohta laissa 507/2017 ja 94 § laissa 961/2013, sekä\n"
        "lisätään 27 a §:n 1 momenttiin, sellaisena kuin se on laissa 507/2017, uusi 8 kohta seuraavasti:"
    )
    correct = (
        "muutetaan 3 §:n 6 ja 30 kohta, 27 a §:n 2 momentin 7 kohta ja 94 §,\n"
        "sellaisina kuin ne ovat, 3 §:n 6 kohta laissa 226/2009, 3 §:n 30 kohta ja 27 a §:n\n"
        "2 momentin 7 kohta laissa 507/2017 ja 94 § laissa 961/2013, sekä\n"
        "lisätään 27 a §:n 2 momenttiin, sellaisena kuin se on laissa 507/2017, uusi 8 kohta seuraavasti:"
    )

    patched, ok = corr._apply_visible_text_delta_multi_slot(fragment, wrong, correct)

    assert ok is True
    assert b"<i>muutetaan</i>" in patched
    assert b"<i>lis\xc3\xa4t\xc3\xa4\xc3\xa4n</i>" in patched
    assert b"27 a \xc2\xa7:n 2 momentin 7 kohta" in patched
    assert b"27 a \xc2\xa7:n 2 momenttiin" in patched
    assert b"27 a \xc2\xa7:n 1 momentin 7 kohta" not in patched
    assert b"27 a \xc2\xa7:n 1 momenttiin" not in patched


def test_patch_source_body_xml_records_invalid_candidate_xml() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2000/671"] = "2005/671"
    table._body_patches["2000/671"] = [("old text here", "<broken", "Sivulla 1772, 6 §:n 2 momentissa")]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?><akomaNtoso><act><body><section eId="sec_6"><subsection eId="sec_6__subsec_2"><content><p>old text here</p></content></subsection></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_body_xml(xml, "2000/671")
    records = corr.get_misapplied_records()

    assert patched == xml
    assert applied == []
    assert records[-1]["reason"] == "post_patch_xml_invalid"
    assert records[-1]["op_id"] == "body_patch/2000/671/0"


def test_patch_source_body_xml_recovers_with_visible_text_delta_single_slot_fallback() -> None:
    corr.clear_misapplied_records()
    table = corr.CorrigendumPatchTable()
    table._amendment_to_statute["2024/33"] = "2011/715"
    table._body_patches["2024/33"] = [
        (
            "5 b §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "5 a §\nOikeudenkäyntiavustajalautakunnan henkilöstö",
            "Sivulla 1, johtolauseen jälkeen",
        )
    ]
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<akomaNtoso><act><body><section><num>5 b \xc2\xa7</num>
<heading>Oikeudenk\xc3\xa4yntiavustajalautakunnan henkil\xc3\xb6st\xc3\xb6</heading></section></body></act></akomaNtoso>"""

    patched, applied = table.patch_source_body_xml(xml, "2024/33")

    assert applied == ["body_patch/2024/33/0"]
    assert b"<num>5 a \xc2\xa7</num>" in patched
    assert b"<num>5 b \xc2\xa7</num>" not in patched
    assert corr.get_misapplied_records() == []


def test_load_from_source_skips_duplicate_manual_body_patch_family(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text(
        "- amendment_id: 2018/541\n"
        "  correction_type: body_text\n"
        "  wrong_text: |\n"
        "    Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
        "    varhaiskasvatuksesta voidaan maarata kuukausimaksu.\n"
        "  correct_text: |\n"
        "    Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
        "    varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan peri\u00e4 enint\u00e4\u00e4n yhdelt\u00e4toista\n"
        "    kalenterikuukaudelta toimintavuoden aikana.\n",
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "official#0",
                "source_pdf": "x",
                "statute_id": "2016/1503",
                "amendment_id": "541/2018",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "prose",
                "location_desc": "Sivulla 1, 4 §:n 1 momentti",
                "wrong_text": (
                    "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja "
                    "perhepaivahoitona jarjestettavasta\n"
                    "varhaiskasvatuksesta voidaan maarata kuukausimaksu."
                ),
                "correct_text": (
                    "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja "
                    "perhepaivahoitona jarjestettavasta\n"
                    "varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan peri\u00e4 "
                    "enint\u00e4\u00e4n yhdelt\u00e4toista\n"
                    "kalenterikuukaudelta toimintavuoden aikana."
                ),
                "llm_confidence": "high",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._body_patches["2018/541"] == [
        (
            "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
            "varhaiskasvatuksesta voidaan maarata kuukausimaksu.",
            "Varhaiskasvatuslaissa tarkoitetusta paivakotitoimintana ja perhepaivahoitona jarjestettavasta\n"
            "varhaiskasvatuksesta voidaan maarata kuukausimaksu. Maksu voidaan periä enintään yhdeltätoista\n"
            "kalenterikuukaudelta toimintavuoden aikana.",
            "Sivulla 1, 4 §:n 1 momentti",
        )
    ]


def test_load_from_source_skips_near_duplicate_body_patch_variant_for_same_location(
    tmp_path: Path,
    monkeypatch,
) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    source_defect_path.write_text("[]\n", encoding="utf-8")
    records = [
        {
            "stable_id": "official#0",
            "source_pdf": "x",
            "statute_id": "2021/616",
            "amendment_id": "616/2021",
            "lang": "fi",
            "correction_index": 0,
            "correction_type": "prose",
            "location_desc": "Sivulla 26, 69 §",
            "wrong_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 3 § tulee kuitenkin voimaanvasta 1 päivänä\n"
                "tammikuuta 2023."
            ),
            "correct_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 2 § tulee kuitenkin voimaanvasta 1 päivänä\n"
                "tammikuuta 2023."
            ),
            "parse_error": None,
        },
        {
            "stable_id": "official#1",
            "source_pdf": "x",
            "statute_id": "2021/616",
            "amendment_id": "616/2021",
            "lang": "fi",
            "correction_index": 1,
            "correction_type": "prose",
            "location_desc": "Sivulla 26, 69 §",
            "wrong_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 3 § tulee kuitenkin voimaavasta 1 päivänä tammikuuta 2023."
            ),
            "correct_text": (
                "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
                "Lain 2 § tulee kuitenkin voimaavasta 1 päivänä tammikuuta 2023."
            ),
            "parse_error": None,
        },
    ]
    records_path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)

    table = corr.CorrigendumPatchTable.load_from_source(records_path)

    assert table._body_patches["2021/616"] == [
        (
            "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
            "Lain 3 § tulee kuitenkin voimaanvasta 1 päivänä\n"
            "tammikuuta 2023.",
            "Tämä laki tulee voimaan 1 päivänä heinäkuuta 2021. "
            "Lain 2 § tulee kuitenkin voimaanvasta 1 päivänä\n"
            "tammikuuta 2023.",
            "Sivulla 26, 69 §",
        )
    ]


def test_load_from_source_records_manual_yaml_failure(tmp_path: Path, monkeypatch) -> None:
    records_path = tmp_path / "corrigendum_official_fi.jsonl"
    source_defect_path = tmp_path / "source_defect_fixes.yaml"
    records_path.write_text(
        json.dumps(
            {
                "stable_id": "x#0",
                "source_pdf": "x",
                "statute_id": "2013/23",
                "amendment_id": "442/2016",
                "lang": "fi",
                "correction_index": 0,
                "correction_type": "johtolause",
                "location_desc": "Sivu 1, johtolause",
                "wrong_text": "wrong",
                "correct_text": "correct",
                "llm_confidence": "high",
                "date_published": "2016-06-01",
                "raw_llm_json": "{}",
                "parse_error": None,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    source_defect_path.write_text(":\n", encoding="utf-8")
    corr.clear_misapplied_records()

    monkeypatch.setattr(corr, "_SOURCE_DEFECT_YAML", source_defect_path)
    table = corr.CorrigendumPatchTable.load_from_source(records_path)
    records = corr.get_misapplied_records()

    assert table._loaded is True
    assert records[-1]["reason"] == "FINLAND.CORRIGENDUM_SOURCE_DEFECT_YAML_LOAD_FAILED"
    assert records[-1]["fallback"] == "db_only"
