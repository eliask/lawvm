from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from lawvm.estonia.fetch import fetch_rt_xml, open_rt_archive
from lawvm.estonia.ee_instruction_waist import (
    read_payload_rewrite_meta,
    read_section_selection_meta,
    read_sentence_target_meta,
    read_subsection_selection_meta,
    read_subsection_text_scope_meta,
)
from lawvm.estonia.grafter import (
    _extract_old_format_commencement_effects,
    _extract_intro_statute_fragment,
    _extract_subsection_text,
    _is_omnibus_amendment,
    _parse_generic_minister_rename_ops,
    _parse_generic_ministry_reorganization_ops,
    _parse_section_blocks,
    _parse_section_payload,
    _text_merge_signature,
    parse_ee_amendment_ops,
    parse_ee_statute,
    _title_matches_para,
)
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    OperationSource,
    TextPatchSpec,
    TextSelector,
    LegalOperation,
    LegalAddress,
    StructuralAction,
)
from lawvm.core.semantic_types import FacetKind, TextPatchKindEnum, IRNodeKind
from lawvm.estonia.peg import extract_ee_ops, parse_html_op_items, parse_target


def _payload(op):
    assert op.payload is not None
    return op.payload


def _source_witness(op):
    assert op.source is not None
    assert op.payload is not None
    witness = op.payload.attrs.get("rewrite_witness")
    assert witness is not None
    return witness


def test_extract_old_format_commencement_effects_handles_retroactive_application_clause() -> None:
    archive = open_rt_archive(readonly=True)
    root = ET.fromstring(fetch_rt_xml("118022016001", archive))

    item_effects, section_effects, whole_act_effective = _extract_old_format_commencement_effects(
        root,
        fallback_effective="2016-02-21",
    )

    assert whole_act_effective == ""
    assert item_effects[("6", "2")] == "2016-01-01"
    assert item_effects[("8", "1")] == "2016-07-01"
    assert section_effects["7"] == "2016-01-01"


def test_extract_ee_ops_keeps_rewrite_witness_on_payload_sidecar_only() -> None:
    ops = extract_ee_ops(
        (
            "Veterinaarkorralduse seaduses, välja arvatud §-s 50 1 ja § 50 2 lõikes 2, "
            "asendatakse sõnad „Veterinaar- ja Toiduamet” sõnadega "
            "„Põllumajandus- ja Toiduamet” vastavas käändes."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert ops
    assert ops[0].source is not None
    witness = _source_witness(ops[0])
    assert type(witness).__name__ == "EETextRewriteWitness"
    assert witness.rewrite.old_surface == "Veterinaar- ja Toiduamet"


def test_extract_ee_ops_parses_fused_valja_tekstiosa_delete() -> None:
    ops = extract_ee_ops(
        'paragrahvi 5 lõike 2 punktist 5 jäetakse väljatekstiosa „hangete korraldamise,”;',
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target.path == (("section", "5"), ("subsection", "2"), ("item", "5"))
    assert op.payload is not None
    assert op.payload.text == ""
    assert op.payload.attrs["old_text"] == "hangete korraldamise,"
    assert op.payload.attrs["rewrite_mode"] == "delete"


def test_extract_ee_ops_expands_multi_target_sentence_part_insert() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 5 lõiget 3 ja paragrahvi 8 lõike 1 punkti 7 täiendatakse "
            "lause teise osaga „, välja arvatud juhul, kui toetuse summat suurendatakse "
            "§ 10 lõike 3 kohaselt.”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [op.target.path for op in ops] == [
        (("section", "5"), ("subsection", "3")),
        (("section", "8"), ("subsection", "1"), ("item", "7")),
    ]
    for op in ops:
        assert op.action is StructuralAction.INSERT
        assert op.payload is not None
        assert op.payload.text.startswith(", välja arvatud juhul")
        assert op.payload.attrs["sentence_target_meta"].mode == "append_sentence_part"
        assert op.witness_rule_id == "ee_insert_multi_explicit_targets"


def test_extract_ee_ops_splits_bare_later_section_in_coordinated_text_replace_targets() -> None:
    text = (
        "paragrahvi 13 lõikes 6, § 15 lõike 1 punktides 5 ja 6 ning "
        "18 lõike 3 punktis 3 asendatakse sõnad „kasu saav” sõnadega "
        "„muu kasu saav” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "13"), ("subsection", "6")),
        (("section", "15"), ("subsection", "1"), ("item", "5")),
        (("section", "15"), ("subsection", "1"), ("item", "6")),
        (("section", "18"), ("subsection", "3"), ("item", "3")),
    ]
    assert all(op.payload is not None and op.payload.attrs["case_inflected"] is True for op in ops)


def test_extract_ee_ops_splits_mixed_subsection_and_item_replace_payload() -> None:
    text = (
        "paragrahvi 26 lõige 2 ja lõike 2 punkt 1 sõnastatakse järgmiselt: "
        "„(2) Lisaks lõikes 1 sätestatule on rakendusüksusel õigus tunnistada "
        "taotluse rahuldamise otsuse kehtetuks, kui esineb vähemalt üks järgmistest asjaoludest:\n"
        "1) toetuse saaja ei ole ehitustegevust sisaldava projekti puhul teinud "
        "§ 5 lõikes 6 nimetatud ehitustööde ettevalmistavaid töid;“;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "26"), ("subsection", "2"))),
        (StructuralAction.REPLACE, (("section", "26"), ("subsection", "2"), ("item", "1"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("Lisaks lõikes 1 sätestatule")
    assert ops[0].payload.attrs["ee_replace_subsection_intro_only"] is True
    assert ops[0].witness_rule_id == "ee_compound_subsection_intro_and_item_replace"
    assert ops[1].payload is not None
    assert ops[1].payload.text.startswith("1) toetuse saaja")
    assert ops[1].witness_rule_id == "ee_compound_subsection_intro_and_item_replace"


def test_extract_ee_ops_accepts_estonian_left_quote_close_in_text_replace() -> None:
    ops = extract_ee_ops(
        'paragrahvi 2 1 lõikes 1 asendatakse sõna „kaheksa“ sõnaga „seitse“;',
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert str(ops[0].target) == "section:2_1/subsection:1"
    payload = _payload(ops[0])
    assert payload.attrs["old_text"] == "kaheksa"
    assert payload.text == "seitse"
    assert ops[0].text_patch is not None


def test_extract_ee_ops_unescapes_html_quote_entities_in_text_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 2 1 lõikes 1 asendatakse sõna &#8222;kaheksa&#8220; "
        "sõnaga &#8222;seitse&#8220;;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    payload = _payload(ops[0])
    assert payload.attrs["old_text"] == "kaheksa"
    assert payload.text == "seitse"


def test_extract_ee_ops_accepts_left_right_curly_quote_text_replace() -> None:
    text = (
        "paragrahvi 34 lõikes 1 asendatakse sõna “põllumajandusliku” "
        "tekstiosaga “põllu- ja metsamajandusliku”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "34"), ("subsection", "1"))
    assert _payload(ops[0]).attrs["old_text"] == "põllumajandusliku"
    assert _payload(ops[0]).text == "põllu- ja metsamajandusliku"


def test_extract_ee_ops_emits_unscoped_many_old_single_new_text_replaces() -> None:
    text = (
        "sõnad „anum”, „proovipudel” ja „proovivõtupudel” "
        "asendatakse sõnaga „proovivõtuanum” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, ()),
        (StructuralAction.TEXT_REPLACE, ()),
        (StructuralAction.TEXT_REPLACE, ()),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("anum", "proovivõtuanum"),
        ("proovipudel", "proovivõtuanum"),
        ("proovivõtupudel", "proovivõtuanum"),
    ]
    assert all(_payload(op).attrs["case_inflected"] is True for op in ops)


def test_extract_ee_ops_emits_lauseosad_many_old_single_new_text_replaces() -> None:
    text = (
        "määruse tekstis asendatakse läbivalt lauseosad „sihtasutuse juhatus”, "
        "„sihtasutuse juhatus või juhatuse liige” ning "
        "„sihtasutuse juhatuse või juhatuse liikme poolt volitatud isik” "
        "sõnaga „sihtasutus” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, ()),
        (StructuralAction.TEXT_REPLACE, ()),
        (StructuralAction.TEXT_REPLACE, ()),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("sihtasutuse juhatuse või juhatuse liikme poolt volitatud isik", "sihtasutus"),
        ("sihtasutuse juhatus või juhatuse liige", "sihtasutus"),
        ("sihtasutuse juhatus", "sihtasutus"),
    ]
    assert all(_payload(op).attrs["case_inflected"] is True for op in ops)


def test_extract_ee_ops_taiendatakse_punktiga_sonastatakse_is_insert() -> None:
    text = (
        "paragrahvi 19 lõiget 5 täiendatakse punktiga 9 ja sõnastatakse "
        "järgmiselt: „9) nõukogu liige.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "19"), ("subsection", "5"), ("item", "9"))
    assert _payload(ops[0]).text == "9) nõukogu liige."


def test_extract_ee_ops_punkt_muudetakse_sonastatakse_remains_replace() -> None:
    text = (
        "paragrahvi 19 lõike 5 punkt 9 muudetakse ja sõnastatakse "
        "järgmiselt: „9) nõukogu liige.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "19"), ("subsection", "5"), ("item", "9"))


def test_extract_ee_ops_emits_statute_and_annex_global_text_replace_pairs() -> None:
    text = (
        "määruses ja selle lisades asendatakse sõna „Maanteeamet” ja "
        "sõnad „Veeteede Amet” sõnaga „Transpordiamet” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path, op.witness_rule_id) for op in ops] == [
        (
            StructuralAction.TEXT_REPLACE,
            (),
            "ee_global_text_replace_statute_and_annex_scope",
        ),
        (
            StructuralAction.TEXT_REPLACE,
            (),
            "ee_global_text_replace_statute_and_annex_scope",
        ),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("Maanteeamet", "Transpordiamet"),
        ("Veeteede Amet", "Transpordiamet"),
    ]
    assert all(_payload(op).attrs["case_inflected"] is True for op in ops)
    assert all(
        _payload(op).attrs["source_family"] == "ee_global_text_replace_statute_and_annex_scope"
        for op in ops
    )


def test_extract_ee_ops_handles_imperative_statute_section_insert() -> None:
    text = (
        "täiendada määrust paragrahviga 17 1 järgmises sõnastuses: "
        "„§ 17 1 . Tervisenõuded V grupi päästeteenistujale "
        "(1) V grupi päästeteenistujate terviseseisund peab võimaldama töötada.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "17_1"),)),
    ]
    assert _payload(ops[0]).text.startswith("§ 17 1 . Tervisenõuded V grupi")


def test_extract_ee_ops_targets_part_chapter_division_heading() -> None:
    text = (
        "seaduse 3. osa 6. peatüki 5. jao pealkiri muudetakse ja "
        "sõnastatakse järgmiselt: „5. jagu Keskkonnaagentuuri toimingud”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("part", "3"), ("chapter", "6"), ("division", "5"))
    assert ops[0].target.special is FacetKind.HEADING
    assert _payload(ops[0]).text == "5. jagu Keskkonnaagentuuri toimingud"


def test_extract_ee_ops_targets_section_heading_pealkiri_asendatakse_pealkirjaga() -> None:
    text = 'paragrahvi 10 pealkiri „Rakendussäte” asendatakse pealkirjaga „Rakendussätted”;'

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "10"),)
    assert ops[0].target.special is FacetKind.HEADING
    assert ops[0].witness_rule_id == "ee_section_heading_pealkiri_asendatakse_pealkirjaga"
    assert _payload(ops[0]).text == "Rakendussätted"
    assert _payload(ops[0]).attrs["old_heading"] == "Rakendussäte"


def test_extract_ee_ops_keeps_single_target_text_to_subsection_insert() -> None:
    text = (
        "paragrahvi 10 tekst loetakse lõikeks 1 ning paragrahvi täiendatakse "
        "lõikega 2 järgmises sõnastuses: „(2) Uus rakendussäte.”"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "10"), ("subsection", "2"))
    assert _payload(ops[0]).text == "(2) Uus rakendussäte."


def test_extract_ee_ops_targets_lahter_text_without_section_replace() -> None:
    text = (
        "paragrahvi 3 lahtri 23 tekst sõnastatakse järgmiselt: "
        "„Märgitakse lahtrisse 22 märgitud välisvaluuta kurss euro suhtes.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "3"),)
    assert ops[0].witness_rule_id == "ee_lahter_text_replace"
    assert _payload(ops[0]).attrs["ee_replace_lahter_text"] == "23"
    assert _payload(ops[0]).text == "Märgitakse lahtrisse 22 märgitud välisvaluuta kurss euro suhtes."


def test_extract_ee_ops_targets_bare_chapter_replace_as_chapter() -> None:
    text = (
        "3. peatükk sõnastatakse järgmiselt: "
        "„3. peatükk TOLLIDEKLARATSIOONI ESITAMINE JA AKTSEPTEERIMINE § 6. Tollideklaratsiooni esitamine”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("chapter", "3"),)
    assert _payload(ops[0]).text.startswith("3. peatükk TOLLIDEKLARATSIOONI")


def test_title_registry_matches_konsulaarametnik_renamed_basis() -> None:
    assert _title_matches_para(
        "Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu põhimäärus",
        "Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu pidamise kord",
    )


def test_parse_ee_amendment_ops_admits_konsulaarametnik_pre_rename_title() -> None:
    archive = open_rt_archive(readonly=True)
    source_xml = fetch_rt_xml("114012025005", archive)

    ops = parse_ee_amendment_ops(
        source_xml,
        "ee/114012025005",
        "Konsulaarametniku ametitoimingute ja diplomaatiliste passide andmekogu põhimäärus",
    )

    assert len(ops) == 10
    assert any(op.target.path == (("section", "16"),) and op.action is StructuralAction.REPLACE for op in ops)


def test_old_format_commencement_effects_parse_rakendussatted_item_slice() -> None:
    archive = open_rt_archive(readonly=True)
    root = ET.fromstring(fetch_rt_xml("104072013020", archive))

    item_effects, section_effects, whole_act_effective = _extract_old_format_commencement_effects(
        root,
        fallback_effective="2014-01-01",
    )

    assert item_effects == {("1", "1"): "2014-01-01", ("1", "3"): "2014-01-01"}
    assert section_effects == {}
    assert whole_act_effective == "2013-09-01"


def test_extract_ee_ops_accepts_left_right_curly_quote_heading_delete() -> None:
    text = 'Paragrahvi 18 pealkirjast jäetakse välja sõnad “ja projekteerimisnormid”;'

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "18"),)
    assert ops[0].target.special is FacetKind.HEADING
    assert _payload(ops[0]).attrs["old_text"] == "ja projekteerimisnormid"
    assert _payload(ops[0]).text == ""


def test_extract_ee_ops_accepts_left_right_curly_quote_viide_pairs() -> None:
    text = (
        "paragrahvi 10 lõikes 2 asendatakse viide lisa tabelitele “6, 7 ja 8” "
        "viitega lisa tabelitele “5, 6 ja 7” ning viide lisa tabelile “9” "
        "viitega lisa tabelile “8”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "10"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "10"), ("subsection", "2"))),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("6, 7 ja 8", "5, 6 ja 7"),
        ("9", "8"),
    ]


def test_extract_ee_ops_keeps_targets_after_first_quote_in_mixed_same_section_pairs() -> None:
    text = (
        "paragrahvi 21 lõike 1 punktis 3 asendatakse tekstiosa „§ 4 punktis 4” "
        "tekstiosaga „§ 4 lõike 1 punktis 4” ja lõikes 4 asendatakse tekstiosa "
        "„lähevad lepingust” tekstiosaga „lähevad jõustunud lepingust”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, _payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ((("section", "21"), ("subsection", "1"), ("item", "3")), "§ 4 punktis 4", "§ 4 lõike 1 punktis 4"),
        ((("section", "21"), ("subsection", "4")), "lähevad lepingust", "lähevad jõustunud lepingust"),
    ]


def test_extract_ee_ops_keeps_section_target_with_normalized_section_sign_dash() -> None:
    text = (
        "paragrahvi 11 lõikes 2, §-s 78 ning § 80 lõigetes 1 ja 3 "
        "asendatakse sõnad „kindlustuskohustuse täitmine” sõnadega "
        "„liikluskindlustuse olemasolu” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "11"), ("subsection", "2")),
        (("section", "78"),),
        (("section", "80"), ("subsection", "1")),
        (("section", "80"), ("subsection", "3")),
    ]


def test_extract_ee_ops_keeps_heading_pair_before_subsection_pair() -> None:
    text = (
        "paragrahvi 42 pealkirjas asendatakse sõna “eesvoolu” sõnaga “eesvoolule” "
        "ja lõikes 1 asendatakse sõna “Eesvoolu” sõnaga “Eesvoolule”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, op.target.special, _payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ((("section", "42"),), FacetKind.HEADING, "eesvoolu", "eesvoolule"),
        ((("section", "42"), ("subsection", "1")), None, "Eesvoolu", "Eesvoolule"),
    ]


def test_extract_ee_ops_preserves_multiple_sentence_targets_in_text_replace_meta() -> None:
    ops = extract_ee_ops(
        "paragrahvi 155 lõike 1 esimest ja teist lauset täiendatakse pärast "
        "sõnu „lapsendaja abikaasa” sõnadega „või registreeritud elukaaslane”;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert str(ops[0].target) == "section:155/subsection:1"
    payload = _payload(ops[0])
    meta = read_sentence_target_meta(payload)
    assert meta is not None
    assert meta.sentence_indexes == (0, 1)


def test_extract_ee_ops_keeps_grouped_sentence_scope_inside_own_section_span() -> None:
    ops = extract_ee_ops(
        "paragrahvi 21 lõigete 2 ja 4 esimesest lausest ja lõike 5 "
        "sissejuhatavast lauseosast, § 22 lõikest 1 ja lõike 4 teisest "
        "lausest ning § 25 lõikest 2 jäetakse välja sõna „kirjalikult”;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    meta_by_target = {
        str(op.target): read_sentence_target_meta(_payload(op))
        for op in ops
        if str(op.target) in {"section:21/subsection:4", "section:22/subsection:4"}
    }

    assert meta_by_target["section:21/subsection:4"] is not None
    assert meta_by_target["section:21/subsection:4"].sentence_indexes == (0,)
    assert meta_by_target["section:22/subsection:4"] is not None
    assert meta_by_target["section:22/subsection:4"].sentence_indexes == (1,)


def test_extract_ee_ops_recovers_nested_quote_tekstiosa_delete() -> None:
    text = (
        "paragrahvi 8 lõike 1 punktist 8 jäetakse välja tekstiosa "
        "„„Toiduseaduse” § 10 lõike 1 alusel tunnustatud”;"
    )
    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "8"), ("subsection", "1"), ("item", "8")))
    assert _payload(ops[0]).attrs["old_text"] == "„Toiduseaduse” § 10 lõike 1 alusel tunnustatud"
    assert _payload(ops[0]).text == ""


def test_extract_ee_ops_treats_tekstiosa_invalidation_as_text_delete() -> None:
    text = 'paragrahvi 9 lõikes 3 tunnistatakse kehtetuks tekstiosa „ning ülekande-”;'
    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target == LegalAddress(path=(("section", "9"), ("subsection", "3")))
    assert _payload(ops[0]).text == ""
    assert _payload(ops[0]).attrs["old_text"] == "ning ülekande-"
    assert _payload(ops[0]).attrs["rewrite_mode"] == "delete"
    assert _payload(ops[0]).attrs["source_family"] == "ee_textual_invalidation_as_text_delete"


def test_extract_ee_ops_recovers_compound_item_and_subsection_repeal() -> None:
    text = "§ 4 lõike 3 punkt 7 ja § 4 1 lõige 8 tunnistatakse kehtetuks."
    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target) for op in ops] == [
        (
            StructuralAction.REPEAL,
            LegalAddress(path=(("section", "4"), ("subsection", "3"), ("item", "7"))),
        ),
        (
            StructuralAction.REPEAL,
            LegalAddress(path=(("section", "4_1"), ("subsection", "8"))),
        ),
    ]
    assert all("ee_compound_section_item_subsection_repeal" in op.provenance_tags for op in ops)


def test_extract_ee_ops_recovers_coordinated_item_targets_with_elided_subsection() -> None:
    text = (
        "paragrahvi 11 lõikest 1, § 16 lõike 1 punktidest 3 ja 6 ning "
        "lõike 3 punktist 1 ja punkti 3 esimesest lausest, § 28 lõike 2 "
        "esimesest lausest ning § 32 lõike 1 punktidest 1 ja 2 jäetakse välja "
        "sõnad „volitatud veterinaararst või”;"
    )
    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert {op.target for op in ops} == {
        LegalAddress(path=(("section", "11"), ("subsection", "1"))),
        LegalAddress(path=(("section", "16"), ("subsection", "1"), ("item", "3"))),
        LegalAddress(path=(("section", "16"), ("subsection", "1"), ("item", "6"))),
        LegalAddress(path=(("section", "16"), ("subsection", "3"), ("item", "1"))),
        LegalAddress(path=(("section", "16"), ("subsection", "3"), ("item", "3"))),
        LegalAddress(path=(("section", "28"), ("subsection", "2"))),
        LegalAddress(path=(("section", "32"), ("subsection", "1"), ("item", "1"))),
        LegalAddress(path=(("section", "32"), ("subsection", "1"), ("item", "2"))),
    }


def test_extract_ee_ops_keeps_singular_item_delete_at_item_granularity() -> None:
    ops = extract_ee_ops(
        "paragrahvi 56 lõike 1 punktist 4 jäetakse läbivalt välja sõnad „ja kõlblikud kohustused” vastavas käändes;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(
        path=(("section", "56"), ("subsection", "1"), ("item", "4"))
    )
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["rewrite_mode"] == "delete"
    assert ops[0].payload.attrs["case_inflected"] is True


def test_extract_ee_ops_marks_labivalt_insert_after_as_all_occurrences() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 41 lõiget 8 täiendatakse läbivalt pärast sõna "
            "„abikaasade” sõnadega „või registreeritud elukaaslaste”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "41"), ("subsection", "8")))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["rewrite_mode"] == "insert_after"
    assert ops[0].payload.attrs["all_occurrences"] is True


def test_extract_ee_ops_marks_case_inflected_insert_after_as_all_occurrences() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 17 lõiget 4 täiendatakse pärast sõna "
            "„abikaasa” sõnadega „või registreeritud elukaaslane” vastavas käändes."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "17"), ("subsection", "4")))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["rewrite_mode"] == "insert_after"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[0].payload.attrs["all_occurrences"] is True


def test_extract_ee_ops_marks_case_inflected_replace_as_all_occurrences() -> None:
    text = (
        "paragrahvi 4 punktis 8 asendatakse sõna „teenuseosutaja” "
        "tekstiosaga „IOT või AOT” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["rewrite_mode"] == "replace"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[0].payload.attrs["all_occurrences"] is True


def test_extract_ee_ops_marks_plural_subsection_insert_after_as_all_occurrences() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 16 lõikeid 2 ja 3 täiendatakse pärast sõnu "
            "„täielik aadress“ sõnadega „ja soovitatavalt e-posti aadress“;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "16"), ("subsection", "2")),
        (("section", "16"), ("subsection", "3")),
    ]
    for op in ops:
        assert op.payload is not None
        assert op.payload.attrs["rewrite_mode"] == "insert_after"
        assert op.payload.attrs["all_occurrences"] is True
        assert op.payload.attrs["source_family"] == "ee_plural_subsection_insert_after_each_surface"


def test_extract_ee_ops_preserves_intro_only_subsection_scope_and_item_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 26 7 pealkirja, lõiget 1, lõike 2 sissejuhatavat lauseosa "
            "ning punkte 2, 4 ja 5 ning lõiget 3 täiendatakse pärast sõna "
            "„gaasivaru” sõnadega „ning veeldatud maagaasi terminali haalamiskai ja taristu”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert {str(op.target) for op in ops} == {
        "section:26_7/subsection:1",
        "section:26_7/heading",
        "section:26_7/subsection:2/item:2",
        "section:26_7/subsection:2/item:4",
        "section:26_7/subsection:2/item:5",
        "section:26_7/subsection:2",
        "section:26_7/subsection:3",
    }
    subsection_two_op = next(op for op in ops if str(op.target) == "section:26_7/subsection:2")
    assert subsection_two_op.payload is not None
    scope_meta = read_subsection_text_scope_meta(subsection_two_op.payload)
    assert scope_meta is not None
    assert scope_meta.intro_only is True


def test_extract_ee_ops_does_not_widen_subsection_intro_item_targets_to_section_items() -> None:
    text = (
        "paragrahvi 8 lõike 1 sissejuhatavas lauseosas ja punktides 5, 7 ja 8 "
        "ning lõikes 2 asendatakse sõnad „Veterinaar- ja Toiduamet” sõnadega "
        "„Põllumajandus- ja Toiduamet”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "8"), ("subsection", "1")),
        (("section", "8"), ("subsection", "1"), ("item", "5")),
        (("section", "8"), ("subsection", "1"), ("item", "7")),
        (("section", "8"), ("subsection", "1"), ("item", "8")),
        (("section", "8"), ("subsection", "2")),
    ]


def test_extract_ee_ops_splits_compound_subsection_intro_and_item_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 6 lõike 3 sissejuhatav lauseosa ja punkt 1 "
            "sõnastatakse järgmiselt: „(3) Kui taotlejal on õigus kasutada "
            "põllumajandusmaad, esitab ta järgmised dokumendid: 1) taotluse lisa;”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.witness_rule_id) for op in ops] == [
        (
            StructuralAction.REPLACE,
            (("section", "6"), ("subsection", "3")),
            "ee_compound_subsection_intro_and_item_replace",
        ),
        (
            StructuralAction.REPLACE,
            (("section", "6"), ("subsection", "3"), ("item", "1")),
            "ee_compound_subsection_intro_and_item_replace",
        ),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["ee_replace_subsection_intro_only"] is True
    assert ops[0].payload.text.endswith("järgmised dokumendid:")
    assert ops[1].payload is not None
    assert ops[1].payload.text == "1) taotluse lisa;"


def test_extract_ee_ops_keeps_sentence_scope_target_local_for_mixed_text_delete() -> None:
    text = (
        "paragrahvi 14 2 lõike 4 teisest lausest ja lõikest 5 jäetakse "
        "välja sõnad „või kohalik omavalitsus”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    by_target = {str(op.target): op for op in ops}
    scoped_payload = _payload(by_target["section:14_2/subsection:4"])
    bare_payload = _payload(by_target["section:14_2/subsection:5"])
    scoped_meta = read_sentence_target_meta(scoped_payload)
    assert scoped_meta is not None
    assert scoped_meta.sentence_indexes == (1,)
    assert read_sentence_target_meta(bare_payload) is None
    assert bare_payload.attrs["suppress_sentence_target_meta"] is True


def test_extract_ee_ops_does_not_mark_plain_subsection_text_replace_as_intro_only() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 26 7 lõiget 2 täiendatakse pärast sõna "
            "„gaasivaru” sõnadega „ning veeldatud maagaasi terminali haalamiskai ja taristu”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "26_7"), ("subsection", "2")))
    assert ops[0].payload is not None
    assert read_subsection_text_scope_meta(ops[0].payload) is None


def test_extract_ee_ops_marks_heading_insert_after_as_heading_special() -> None:
    ops = extract_ee_ops(
        "paragrahvi 96 pealkirja täiendatakse pärast sõna „nõuetega” sõnadega „ja seaduse kohaldamine”;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target == LegalAddress(path=(("section", "96"),), special=FacetKind.HEADING)


def test_extract_ee_ops_marks_plain_heading_reword_as_heading_special() -> None:
    text = "paragrahvi 6 pealkiri sõnastatakse järgmiselt: „§ 6. Asfalt- ja mustkattega tee”;"

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target == LegalAddress(path=(("section", "6"),), special=FacetKind.HEADING)
    assert ops[0].payload is not None
    assert ops[0].payload.text == "§ 6. Asfalt- ja mustkattega tee"


def test_extract_ee_ops_splits_text_replace_plus_last_sentence_insert() -> None:
    text = (
        "paragrahvi 6 lõikes 7 asendatakse tekstiosa „±0,5%” tekstiosaga „±1,0%” "
        "ning täiendatakse viimase lause järel lausega „Ühelgi juhul ei tohi "
        "teepeenra põikkalle olla väiksem kui tee põikkalle.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "6"), ("subsection", "7"))),
        (StructuralAction.INSERT, (("section", "6"), ("subsection", "7"))),
    ]
    replace_payload = _payload(ops[0])
    assert replace_payload.attrs["old_text"] == "±0,5%"
    assert replace_payload.text == "±1,0%"
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "±0,5%"
    assert ops[0].text_patch.replacement == "±1,0%"

    insert_payload = _payload(ops[1])
    assert insert_payload.text == "Ühelgi juhul ei tohi teepeenra põikkalle olla väiksem kui tee põikkalle."
    sentence_meta = read_sentence_target_meta(insert_payload)
    assert sentence_meta is not None
    assert sentence_meta.mode == "insert_after"
    assert sentence_meta.sentence_indexes == (1_000_000,)


def test_parse_ee_amendment_ops_supports_plaintext_old_format_omnibus_target_section() -> None:
    archive = open_rt_archive()
    base = parse_ee_statute(fetch_rt_xml("104112016005", archive), "ee/104112016005")
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("125102017001", archive),
        "ee/125102017001",
        target_title=base.title,
    )

    assert any(
        op.target == LegalAddress(path=(("section", "6_3"), ("subsection", "2")))
        for op in ops
    )


def test_parse_ee_amendment_ops_recovers_old_format_regulation_section_items() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128062014060", archive),
        "ee/128062014060",
        target_title="Marutaudi tõrje eeskiri",
    )

    assert [(op.action, op.target) for op in ops] == [
        (
            StructuralAction.TEXT_REPLACE,
            LegalAddress(path=(("section", "2"), ("subsection", "1"))),
        ),
        (
            StructuralAction.REPLACE,
            LegalAddress(path=(("section", "4"), ("subsection", "1"))),
        ),
        (
            StructuralAction.REPLACE,
            LegalAddress(path=(("section", "7"), ("subsection", "6"))),
        ),
        (
            StructuralAction.REPLACE,
            LegalAddress(path=(("section", "15"), ("subsection", "5"))),
        ),
        (
            StructuralAction.REPLACE,
            LegalAddress(path=(("section", "18"), ("subsection", "2"), ("item", "1"))),
        ),
        (
            StructuralAction.TEXT_REPLACE,
            LegalAddress(path=(("section", "20_1"),)),
        ),
    ]
    assert all("old_format_amendment_section:2" in op.provenance_tags for op in ops)
    assert all(
        any(tag.startswith("base_act: ") and "Marutaudi tõrje eeskiri" in tag for tag in op.provenance_tags)
        for op in ops
    )


def test_parse_ee_amendment_ops_decodes_escaped_old_format_target_header() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <sisuTekst><HTMLKonteiner><![CDATA[
          <p><b>&sect; 5.</b> P&otilde;llumajandusministri 14. juuli 2008. a m&auml;&auml;rust nr 72 &#132;Maaparandushoiukava sisu- ja vormin&otilde;uded ning kava koostamise kord&#148; (RTL&nbsp;2008, 61, 872) muudetakse j&auml;rgmiselt:</p>
          <p><b>1)</b> paragrahvi 2 l&otilde;ige 2 s&otilde;nastatakse j&auml;rgmiselt:</p>
          <p>&#132;(2) Uus tekst.&#148;;</p>
        ]]></HTMLKonteiner></sisuTekst>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Maaparandushoiukava sisu- ja vorminõuded ning kava koostamise kord",
    )

    assert len(ops) == 1
    assert ops[0].action == StructuralAction.REPLACE
    assert ops[0].target == LegalAddress(path=(("section", "2"), ("subsection", "2")))
    assert _payload(ops[0]).text == "(2) Uus tekst."
    assert "old_format_amendment_section:5" in ops[0].provenance_tags


def test_parse_ee_amendment_ops_excludes_later_scoped_old_text_from_global_lexical_replace() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("121042016001", archive),
        "ee/121042016001",
        target_title="Täiendavad juhised kauba sisenemis- ja väljumisformaalsuste teostamiseks",
    )

    global_op = next(op for op in ops if op.target.path == () and _payload(op).attrs.get("old_text") == "ühendus")
    rewrite = read_payload_rewrite_meta(global_op.payload).rewrite_witness

    assert rewrite is not None
    assert (("section", "2"), ("subsection", "1_1")) in rewrite.rewrite.exclude_paths
    assert (("section", "19"),) in rewrite.rewrite.exclude_paths
    item_5_replace = next(
        op
        for op in ops
        if op.target.path == (("section", "1"), ("subsection", "5"), ("item", "5"))
    )
    assert item_5_replace.payload is not None
    assert "liidu tollimaksuvabastuse süsteem" in (item_5_replace.payload.text or "")
    assert "ee_source_local_global_text_replace_payload_composition" in item_5_replace.provenance_tags


def test_parse_ee_amendment_ops_preserves_quoted_legal_title_during_source_local_composition() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <sisuTekst><HTMLKonteiner><![CDATA[
          <p><b>&sect; 1.</b> Testm&auml;&auml;rust &bdquo;Vesikondade ja alamvesikondade m&auml;&auml;ramine&ldquo; muudetakse j&auml;rgmiselt:</p>
          <p><b>1)</b> m&auml;&auml;ruse tekstis asendatakse l&auml;bivalt s&otilde;na &bdquo;alamvesikond&ldquo; s&otilde;naga &bdquo;vesikond&ldquo; vastavas k&auml;&auml;ndes;</p>
          <p><b>2)</b> paragrahvi 2 l&otilde;ige 2 s&otilde;nastatakse j&auml;rgmiselt:</p>
          <p>&bdquo;(2) Hoiukavad koostatakse Vabariigi Valitsuse 9. septembri 2010. a m&auml;&auml;ruse nr 132 &bdquo;Vesikondade ja alamvesikondade m&auml;&auml;ramine&ldquo; &sect;-s 1 nimetatud alamvesikondade kohta.&ldquo;;</p>
        ]]></HTMLKonteiner></sisuTekst>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Vesikondade ja alamvesikondade määramine",
    )

    subsection = next(
        op
        for op in ops
        if op.target.path == (("section", "2"), ("subsection", "2"))
    )

    assert subsection.payload is not None
    assert "Vesikondade ja alamvesikondade määramine" in subsection.payload.text
    assert "nimetatud vesikondade kohta" in subsection.payload.text
    assert "ee_source_local_global_text_replace_payload_composition" in subsection.provenance_tags
    assert "ee_source_local_payload_composition_quoted_title_skipped" in subsection.provenance_tags


def test_parse_ee_amendment_ops_composes_later_text_replace_selectors() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("131012018001", archive),
        "ee/131012018001",
        target_title="Tervisekaitsenõuded asenduskoduteenusele",
    )

    provider_rewrite = next(
        op
        for op in ops
        if op.action is StructuralAction.TEXT_REPLACE
        and op.payload is not None
        and op.payload.text == "teenuseosutaja"
    )

    assert provider_rewrite.payload is not None
    assert provider_rewrite.payload.attrs["old_text"] == "teenuse osutaja"
    assert provider_rewrite.payload.attrs["source_old_text"] == "asenduskoduteenuse osutaja"
    assert provider_rewrite.text_patch is not None
    assert provider_rewrite.text_patch.selector.match_text == "teenuse osutaja"
    assert "ee_source_local_global_text_replace_selector_composition" in provider_rewrite.provenance_tags


def test_parse_ee_statute_attaches_section_level_intro_to_structured_item_list() -> None:
    archive = open_rt_archive(readonly=True)
    statute = parse_ee_statute(fetch_rt_xml("129052012009", archive), "ee/129052012009")
    section_5 = next(
        node
        for node in statute.body.children
        if node.kind is IRNodeKind.SECTION and node.label == "5"
    )
    subsection = section_5.children[0]

    assert subsection.text == "Üldosakonna struktuuriüksused on:"
    assert subsection.attrs["section_level_intro_text"] == "Üldosakonna struktuuriüksused on:"
    assert "ee_section_level_intro_attached_to_first_subsection" in subsection.attrs["source_cleanup_rules"]


def test_parse_ee_chapter_payload_splits_repeated_heading_body_section() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128062017067", archive),
        "ee/128062017067",
        target_title=(
            "Ajutised püügikitsendused, harrastuspüügiõiguse tasu ja "
            "püügivahendite piirarv harrastuskalapüügil 2017. aastal"
        ),
    )
    chapter_insert = next(op for op in ops if op.action is StructuralAction.INSERT and op.target.path == (("chapter", "7_1"),))
    assert chapter_insert.payload is not None

    from lawvm.estonia.grafter import _parse_chapter_payload

    chapter = _parse_chapter_payload(chapter_insert.payload.text, "7_1")
    section = next(child for child in chapter.children if child.kind is IRNodeKind.SECTION and child.label == "15_2")

    assert section.text == "Ühe vähipüügivahendiga harrastuspüügiõiguse tasu"
    assert section.children[0].text.startswith("Ühe vähipüügivahendiga harrastuspüügiõiguse tasu üheks ööpäevaks")


def test_parse_ee_section_blocks_marks_repeated_heading_body_recovery() -> None:
    sections = _parse_section_blocks(
        (
            "§ 15 2 . Ühe vähipüügivahendiga harrastuspüügiõiguse tasu "
            "Ühe vähipüügivahendiga harrastuspüügiõiguse tasu üheks ööpäevaks on 3 eurot."
        )
    )
    section = sections[0]

    assert section.text == "Ühe vähipüügivahendiga harrastuspüügiõiguse tasu"
    assert "ee_repeated_section_heading_body_split" in section.children[0].attrs["source_cleanup_rules"]


def test_parse_ee_amendment_ops_splits_plaintext_maarust_item_after_text_replace() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128062017067", archive),
        "ee/128062017067",
        target_title=(
            "Ajutised püügikitsendused, harrastuspüügiõiguse tasu ja "
            "püügivahendite piirarv harrastuskalapüügil 2017. aastal"
        ),
    )

    assert len(ops) == 6
    assert any(
        op.action is StructuralAction.TEXT_REPLACE
        and op.target.path == (("section", "15"), ("subsection", "4"), ("item", "11"))
        and op.payload is not None
        and op.payload.text == "Kirikumäe"
        for op in ops
    )
    assert not any(op.target.path == (("section", "15_5"),) for op in ops)
    section_insert = next(op for op in ops if op.action is StructuralAction.INSERT and op.target.path == (("section", "16_1"),))
    assert section_insert.payload is not None
    parsed_section = _parse_section_payload(section_insert.payload.text, kind=IRNodeKind.SECTION)
    assert parsed_section.text == "Rakendussäte"
    assert parsed_section.children[0].text.startswith("Enne 1. juulit 2017")
    chapter_insert = next(op for op in ops if op.action is StructuralAction.INSERT and op.target.path == (("chapter", "7_1"),))
    assert "Kirikumõisa" not in chapter_insert.provenance_tags[0]


def test_parse_section_payload_accepts_subsection_marker_without_space() -> None:
    parsed = _parse_section_payload(
        (
            "(1)Vesikonna maaparandushoiu kokkuvõttes antakse ülevaade: "
            "1) riigieesvoolude prioriteetidest; "
            "2) riigieesvoolude hoiutööde mahtudest. "
            "Vesikonna maaparandushoiu kokkuvõttes analüüsitakse muutusi."
        )
    )

    assert parsed.text == ""
    assert len(parsed.children) == 1
    subsection = parsed.children[0]
    assert subsection.kind is IRNodeKind.SUBSECTION
    assert subsection.label == "1"
    assert subsection.text == "Vesikonna maaparandushoiu kokkuvõttes antakse ülevaade:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "riigieesvoolude prioriteetidest;"),
        (
            "2",
            (
                "riigieesvoolude hoiutööde mahtudest. "
                "Vesikonna maaparandushoiu kokkuvõttes analüüsitakse muutusi."
            ),
        ),
    ]


def test_replay_ee_to_pit_preserves_heading_for_short_section_text_replacement() -> None:
    from lawvm.estonia.grafter import apply_ee_ops

    base = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.SECTION,
                    label="14",
                    text="Hoiukava koostamise kord",
                    children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana tekst."),),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee/test/op",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "14"),)),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Hoiukavad koostab Põllumajandusamet."),
        source=OperationSource(
            statute_id="ee/test",
            raw_text=(
                "paragrahvi 14 tekst sõnastatakse järgmiselt: "
                "„Hoiukavad koostab Põllumajandusamet.”;"
            ),
        ),
        provenance_tags=(
            "paragrahvi 14 tekst sõnastatakse järgmiselt: „Hoiukavad koostab Põllumajandusamet.”;",
        ),
    )

    updated = apply_ee_ops(base, [op])
    section = updated.body.children[0]
    assert section.text == "Hoiukava koostamise kord"
    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Hoiukavad koostab Põllumajandusamet.")
    ]


def test_parse_ee_amendment_ops_uses_direct_html_intro_for_single_target_regulation() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("115122020002", archive),
        "ee/115122020002",
        target_title="Kohustusliku pensionifondi osakute kord",
    )

    assert len(ops) >= 20
    assert any(
        op.action == StructuralAction.INSERT
        and op.target.path == (("section", "2"), ("item", "10"))
        for op in ops
    )
    assert any(
        op.action == StructuralAction.INSERT
        and op.target.path == (("section", "13_1"),)
        for op in ops
    )


def test_extract_ee_ops_treats_after_word_as_text_replace_anchor() -> None:
    text = (
        'paragrahvi 23 lõike 2 punktis 3, § 43 pealkirjas ja selle lõikes 2 '
        'asendatakse pärast sõna "kutseõppeasutuse" tekstiosa '
        '"päevases õppevormis või täiskoormusega" tekstiosaga '
        '"statsionaarses õppevormis";'
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "23"), ("subsection", "2"), ("item", "3")),
        (("section", "43"), ("subsection", "2")),
        (("section", "43"),),
    ]
    assert ops[2].target.special is FacetKind.HEADING
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "päevases õppevormis või täiskoormusega" for op in ops)
    assert all(_payload(op).text == "statsionaarses õppevormis" for op in ops)
    assert all(_payload(op).attrs["rewrite_mode"] == "replace" for op in ops)
    assert all(
        _payload(op).attrs["source_family"] == "ee_text_replace_after_anchor_clause"
        for op in ops
    )


def test_extract_ee_ops_keeps_quoted_target_title_before_text_replace_verb() -> None:
    text = (
        "paragrahvis 1, § 3 lõikes 2, § 12 lõikes 1, § 15 lõikes 2, "
        "§ 16 lõikes 1, § 17 lõikes 1, § 18 lõikes 1, §-s 19 ja määruse "
        "lisas „Tasandus- ja toetusfondi jaotus” asendatakse sõna „lisa” "
        "tekstiosaga „lisa 1” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "1"),),
        (("section", "3"), ("subsection", "2")),
        (("section", "12"), ("subsection", "1")),
        (("section", "15"), ("subsection", "2")),
        (("section", "16"), ("subsection", "1")),
        (("section", "17"), ("subsection", "1")),
        (("section", "18"), ("subsection", "1")),
        (("section", "19"),),
    ]
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "lisa" for op in ops)
    assert all(_payload(op).text == "lisa 1" for op in ops)
    assert all(_payload(op).attrs["case_inflected"] is True for op in ops)


def test_extract_ee_ops_recovers_missing_closing_quote_in_replacement_payload() -> None:
    text = (
        "paragrahvi 9 lõikes 2 asendatakse tekstiosa „§ 18 punktis 1“ "
        "tekstiosaga „§ 18 lõike 1 punktis 1;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "9"), ("subsection", "2"))
    assert _payload(ops[0]).attrs["old_text"] == "§ 18 punktis 1"
    assert _payload(ops[0]).text == "§ 18 lõike 1 punktis 1"


def test_extract_ee_ops_accepts_estonian_open_ascii_close_structural_payload() -> None:
    text = (
        'määrust täiendatakse §-ga 62 4 järgmises sõnastuses: '
        '„§ 62 4 . Kutseõppes täis- ja osakoormusega õppevormis õppimine '
        'Enne 2013/14 õppeaastat kutseõppesse õppima asunud isikute suhtes '
        'kohaldatakse täis- ja osakoormusega õppevormi ning õppekoormust."'
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "62_4"),)
    assert ops[0].payload is not None
    assert not ops[0].payload.text.endswith('"')
    assert "õppekoormust." in ops[0].payload.text


def test_extract_ee_ops_strips_direct_target_title_before_global_text_replace() -> None:
    text = (
        "Majandus- ja kommunikatsiooniministri 27. juuni 2011. a määruses nr 53 "
        "„Maastikusõiduki registreerimise tingimused ja kord” asendatakse sõna "
        "„Maanteeamet” sõnaga „Transpordiamet” vastavas käändes."
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target.path == ()
    assert _payload(ops[0]).attrs["old_text"] == "Maanteeamet"
    assert _payload(ops[0]).text == "Transpordiamet"
    assert _payload(ops[0]).attrs["case_inflected"] is True


def test_extract_ee_ops_does_not_strip_inner_quoted_source_citation_as_act_title() -> None:
    text = (
        "paragrahvi 2 lõiked 2 ja 3 sõnastatakse järgmiselt: "
        "„(2) Hoiukavad koostatakse Vabariigi Valitsuse 9. septembri 2010. a "
        "määruse nr 132 „Vesikondade ja alamvesikondade määramine” §-s 1 "
        "nimetatud vesikondade kohta. (3) Hoiukava koostatakse.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "2"), ("subsection", "2"))),
        (StructuralAction.REPLACE, (("section", "2"), ("subsection", "3"))),
    ]
    assert "Vesikondade ja alamvesikondade määramine" in _payload(ops[0]).text
    assert _payload(ops[1]).text == "(3) Hoiukava koostatakse."


def test_parse_ee_amendment_ops_splits_plaintext_single_target_regulation_body() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("114032018001", archive),
        "ee/114032018001",
        target_title=(
            "Riigi eelarvestrateegia ja ministeeriumi valitsemisala eelarve projekti "
            "koostamise ning riigieelarve vahendite ülekandmise kord"
        ),
    )

    assert len(ops) >= 40
    assert any(
        op.action == StructuralAction.REPLACE
        and op.target.path == (("section", "22"), ("subsection", "1"))
        for op in ops
    )
    assert not any(op.action == StructuralAction.REPEAL and op.target.path == (("section", "2"),) for op in ops)


def test_parse_ee_amendment_ops_recovers_unstructured_single_clause_body() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("110052016001", archive),
        "ee/110052016001",
        target_title="Newcastle’i haiguse tõrje eeskiri",
    )

    assert [(op.action, op.target) for op in ops] == [
        (
            StructuralAction.REPEAL,
            LegalAddress(path=(("section", "4"), ("subsection", "3"), ("item", "7"))),
        ),
        (
            StructuralAction.REPEAL,
            LegalAddress(path=(("section", "4_1"), ("subsection", "8"))),
        ),
    ]
    assert all("ee_compound_section_item_subsection_repeal" in op.provenance_tags for op in ops)


def test_parse_ee_amendment_ops_preserves_intro_only_item_fanout_in_maagaasiseadus() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("102052024002", archive),
        "ee/102052024002",
        target_title="Maagaasiseadus",
    )

    targets = {str(op.target) for op in ops if "26_7" in str(op.target)}
    assert {
        "section:26_7/heading",
        "section:26_7/subsection:1",
        "section:26_7/subsection:2",
        "section:26_7/subsection:2/item:2",
        "section:26_7/subsection:2/item:4",
        "section:26_7/subsection:2/item:5",
        "section:26_7/subsection:2/item:6",
        "section:26_7/subsection:3",
        "section:26_7/subsection:4",
        "section:26_7/subsection:5",
        "section:26_7/subsection:6",
        "section:26_7/subsection:7",
    }.issubset(targets)
    subsection_two_op = next(op for op in ops if str(op.target) == "section:26_7/subsection:2")
    assert subsection_two_op.payload is not None
    scope_meta = read_subsection_text_scope_meta(subsection_two_op.payload)
    assert scope_meta is not None
    assert scope_meta.intro_only is True


def test_parse_ee_statute_strips_kehtetu_marker_from_subsection_text() -> None:
    xml = b"""
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>4</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
            <paragrahv>
              <paragrahvNr>13</paragrahvNr>
              <loige>
                <loigeNr>1</loigeNr>
                <sisuTekst>
                  <tavatekst>[Kehtetu - </tavatekst>
                  <viide>
                    <kuvatavTekst>RT I 1998, 61, 988</kuvatavTekst>
                  </viide>
                  <tavatekst> - j\xc3\xb5ust. 16.07.1998] T\xc3\xb6\xc3\xb6otsijate ja t\xc3\xb6\xc3\xb6tute koolitus.</tavatekst>
                </sisuTekst>
              </loige>
            </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == "Tööotsijate ja töötute koolitus."


def test_parse_ee_statute_strips_inline_rt_editorial_parenthetical() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>1</paragrahvNr>
            <paragrahvPealkiri>Üldsäte</paragrahvPealkiri>
            <loige>
              <loigeNr>2</loigeNr>
              <sisuTekst>
                <tavatekst>
                  Käesolevas seaduses ettenähtud haldusmenetlusele kohaldatakse
                  haldusmenetluse seaduse (RT I 2001, 58, 354) sätteid.
                </tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == (
        "Käesolevas seaduses ettenähtud haldusmenetlusele kohaldatakse haldusmenetluse seaduse sätteid."
    )


def test_parse_ee_statute_normalizes_space_before_closing_parenthesis() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>5</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>21</paragrahvNr>
            <paragrahvPealkiri>Juhtimisõigus</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>
                  Mootorsõiduki juhtimise õiguse (edaspidi juhtimisõigus ) seisukohalt.
                </tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == "Mootorsõiduki juhtimise õiguse (edaspidi juhtimisõigus) seisukohalt."


def test_parse_ee_statute_keeps_non_item_reavahetus_continuation_text() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>14 1</peatykkNr>
          <peatykkPealkiri>Karistused</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr ylaIndeks="15">74</paragrahvNr>
            <kuvatavNr><![CDATA[§ 74<sup>15</sup>. ]]></kuvatavNr>
            <paragrahvPealkiri>Testparagrahv</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Keelu rikkumise eest –<reavahetus/>karistatakse rahatrahviga kuni 50 trahviühikut.</tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == "Keelu rikkumise eest – karistatakse rahatrahviga kuni 50 trahviühikut."


def test_parse_ee_statute_keeps_reavahetus_numbered_items_structural() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>12</paragrahvNr>
            <paragrahvPealkiri>Loetelu</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Intro:<reavahetus/>1) esimene;<reavahetus/>2) teine.</tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == "Intro:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "esimene;"),
        ("2", "teine."),
    ]


def test_parse_ee_statute_keeps_section_level_reavahetus_items_structural() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>11</paragrahvNr>
          <kuvatavNr>§ 11.</kuvatavNr>
          <sisuTekst>
            <tavatekst>Üldosakonna juhataja:<reavahetus/>1) vastutab;<reavahetus/>3 1) koordineerib.</tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0]
    subsection = section.children[0]

    assert subsection.text == "Üldosakonna juhataja:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "vastutab;"),
        ("3_1", "koordineerib."),
    ]


def test_parse_ee_statute_recovers_real_section_level_reavahetus_items() -> None:
    archive = open_rt_archive(readonly=True)
    statute = parse_ee_statute(fetch_rt_xml("128032013017", archive), "128032013017")

    section_11 = next(child for child in statute.body.children if child.kind == IRNodeKind.SECTION and child.label == "11")
    subsection_11 = section_11.children[0]
    section_16 = next(child for child in statute.body.children if child.kind == IRNodeKind.SECTION and child.label == "16")
    subsection_16 = section_16.children[0]

    assert subsection_11.text == "Üldosakonna juhataja:"
    assert [item.label for item in subsection_11.children] == ["1", "2", "3", "4"]
    assert "vastutab osakonnale pandud ülesannete täitmise eest" in (subsection_11.children[0].text or "")
    assert {"3_1", "3_2"}.issubset({item.label for item in subsection_16.children})


def test_parse_ee_statute_drops_reavahetus_item_amendment_history_notes() -> None:
    archive = open_rt_archive(readonly=True)
    statute = parse_ee_statute(fetch_rt_xml("128032013017", archive), "128032013017")

    section_16 = next(child for child in statute.body.children if child.kind == IRNodeKind.SECTION and child.label == "16")
    subsection_16 = section_16.children[0]
    items = {item.label: item for item in subsection_16.children}

    assert "RT I" not in (items["2"].text or "")
    assert "RT I" not in (items["3_1"].text or "")
    assert "jõust" not in (items["19"].text or "")


def test_parse_ee_statute_drops_item_repealed_range_residue_with_marker() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>10 1</paragrahvNr>
            <paragrahvPealkiri>Loetelu</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Intro.</tavatekst>
              </sisuTekst>
              <alampunkt>
                <alampunktNr>3</alampunktNr>
                <sisuTekst>
                  <tavatekst>Kehtiv punkt.</tavatekst>
                </sisuTekst>
              </alampunkt>
              <alampunkt>
                <alampunktNr>4</alampunktNr>
                <sisuTekst>
                  <tavatekst>--6)</tavatekst>
                </sisuTekst>
                <muutmismarge>
                  <tavatekst>kehtetud –</tavatekst>
                </muutmismarge>
              </alampunkt>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert [(item.label, item.text) for item in subsection.children] == [("3", "Kehtiv punkt.")]
    assert subsection.attrs["source_cleanup_rules"] == ("ee_drop_repealed_range_residue",)
    assert subsection.attrs["dropped_repealed_residues"] == ("--6)",)


def test_parse_ee_statute_preserves_residue_like_item_without_repeal_marker() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>10 1</paragrahvNr>
            <paragrahvPealkiri>Loetelu</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <alampunkt>
                <alampunktNr>4</alampunktNr>
                <sisuTekst>
                  <tavatekst>--6)</tavatekst>
                </sisuTekst>
              </alampunkt>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert [(item.label, item.text) for item in subsection.children] == [("4", "--6)")]
    assert "source_cleanup_rules" not in subsection.attrs


def test_parse_ee_statute_drops_subsection_repealed_range_residue_with_marker() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>1</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>16</paragrahvNr>
            <paragrahvPealkiri>Loiked</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Kehtiv lõige.</tavatekst>
              </sisuTekst>
            </loige>
            <loige>
              <loigeNr>2</loigeNr>
              <sisuTekst>
                <tavatekst>–(3)</tavatekst>
              </sisuTekst>
              <muutmismarge>
                <tavatekst>Kehtetud –</tavatekst>
              </muutmismarge>
            </loige>
            <loige>
              <sisuTekst>
                <tavatekst>§-d 121–13</tavatekst>
              </sisuTekst>
              <muutmismarge>
                <tavatekst>Kehtetud –</tavatekst>
              </muutmismarge>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0]

    assert [(subsection.label, subsection.text) for subsection in section.children] == [("1", "Kehtiv lõige.")]
    assert section.attrs["source_cleanup_rules"] == ("ee_drop_repealed_range_residue",)
    assert section.attrs["dropped_repealed_residues"] == ("–(3)", "§-d 121–13")


def test_extract_ee_ops_keeps_nested_french_quotes_inside_payload() -> None:
    ops = extract_ee_ops(
        (
            "Paragrahv 16 muudetakse ja sõnastatakse järgmiselt: "
            "« § 16. Sõiduki kindlustamiskohustus "
            "(2) Registrisse kantud sõiduki suhtes tehakse registritoiming või registrikanne "
            "ainult kehtiva liikluskindlustuse lepingu olemasolul. Kehtiva liikluskindlustuse "
            "lepingu olemasolu ei ole nõutav sõiduki registrist kustutamiseks ega Eesti "
            "kaitsejõudude, Piirivalveameti, Kaitsepolitseiameti ja politseiasutuse valduses "
            "olevatel sõidukitel, mille registreerimistunnistusele on sõiduki omaniku kohale "
            "märgitud «kaitsejõud» või mõni eelnimetatud asutus.»"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "16"),)
    assert ops[0].payload is not None


def test_extract_ee_ops_classifies_bare_sonastatakse_as_replace() -> None:
    ops = extract_ee_ops(
        'paragrahvi 1 tekst sõnastatakse järgmiselt: „(1) Uus tekst.”',
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "1"),)
    assert ops[0].payload is not None


def test_extract_ee_ops_keeps_inner_jargmiselt_inside_section_payload() -> None:
    text = (
        "paragrahvi 1 tekst sõnastatakse järgmiselt: „(1) Taotlus esitatakse. "
        "(2) Arve lisatakse. (3) Arve sisaldab järgmisi andmeid: 1) nimetus. "
        "(4) Kogus märgitakse järgmiselt: 1) alkohol liitrites; "
        "2) elektrienergia vastavalt ühikule.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "1"),)
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("(1) Taotlus esitatakse.")
    assert "(4) Kogus märgitakse järgmiselt:" in ops[0].payload.text
    assert not ops[0].payload.text.startswith("1) alkohol liitrites")


def test_extract_ee_ops_uses_later_marker_after_repeated_target_title() -> None:
    text = (
        "§ 3 täiendatakse punktiga 20 järgmises sõnastuses: "
        "„20) matemaatika (kirjalik) – 7. juuni 2013.a.“. "
        "Haridus- ja teadusministri määruse nr 20 „Riigieksamite ajad“ "
        "§ 3 täiendatakse punktiga 20 järgmises sõnastuses: "
        "„20) matemaatika (kirjalik) – 7. juuni 2013.a.“."
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "3"), ("item", "20"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == "20) matemaatika (kirjalik) – 7. juuni 2013.a."


def test_extract_ee_ops_classifies_bare_sonastatakse_subsection_as_replace() -> None:
    ops = extract_ee_ops(
        'paragrahvi 13 2 lõige 5 sõnastatakse järgmiselt: „(5) Uus tekst.”',
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "13_2"), ("subsection", "5"))
    assert ops[0].payload is not None


def test_extract_ee_ops_expands_mixed_superscript_repeal_ranges() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 2 lõiked 1, 2, 6, 8¹–13, 15, 16–18¹ ja 20, "
            "2. peatüki 1.–3. jagu, § 13¹ ja § 13² lõiked 1–4 ja 6 "
            "tunnistatakse kehtetuks;"
        ),
        OperationSource(statute_id="ee/test"),
    )
    targets = {op.target.path for op in ops if op.action is StructuralAction.REPEAL}

    assert (("chapter", "2"), ("division", "1")) in targets
    assert (("chapter", "2"), ("division", "2")) in targets
    assert (("chapter", "2"), ("division", "3")) in targets
    assert (("section", "2"), ("subsection", "8_1")) in targets
    assert (("section", "2"), ("subsection", "13")) in targets
    assert (("section", "2"), ("subsection", "18_1")) in targets
    assert (("section", "13_1"),) in targets
    assert (("section", "13_2"), ("subsection", "1")) in targets
    assert (("section", "13_2"), ("subsection", "4")) in targets
    assert (("section", "13_2"), ("subsection", "6")) in targets
    assert len(targets) == 24


def test_extract_ee_ops_expands_mixed_chapter_superscript_repeal_ranges() -> None:
    ops = extract_ee_ops(
        "4.–6. ja 7¹.–8¹. peatükk ning määruse lisad 1, 3 ja 4 tunnistatakse kehtetuks.",
        OperationSource(statute_id="ee/test"),
    )
    targets = [op.target.path for op in ops if op.action is StructuralAction.REPEAL]

    assert targets == [
        (("chapter", "4"),),
        (("chapter", "5"),),
        (("chapter", "6"),),
        (("chapter", "7_1"),),
        (("chapter", "8"),),
        (("chapter", "8_1"),),
    ]


def test_text_merge_signature_prefers_typed_text_patch() -> None:
    op = LegalOperation(
        op_id="ee-signature",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        text_patch=TextPatchSpec(
            kind=TextPatchKindEnum.REPLACE,
            selector=TextSelector(match_text="typed match", occurrence=7),
            replacement="typed replacement",
        ),
    )
    signature = _text_merge_signature(op)

    assert signature == ("typed match", "typed replacement", 7)


def test_generic_text_replace_emitters_populate_typed_text_patch() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktipealkiri>ministrite ametinimetuste asendamine</aktipealkiri>
      <sisu>
        <tavatekst>valdkonna eest vastutav minister</tavatekst>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    minister_ops = _parse_generic_minister_rename_ops(
        xml,
        source_id="ee/test",
        target_title="Testseadus",
    )
    assert minister_ops
    assert minister_ops[0].text_patch is not None
    assert minister_ops[0].text_patch.selector.match_text == "valdkonna eest vastutav minister"
    assert minister_ops[0].text_patch.replacement == "valdkondade eest vastutavad ministrid"

    reorg_xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktipealkiri>ministeeriumide ja nende valitsemisalade ümberkorraldamine</aktipealkiri>
      <sisu>
        <tavatekst>Keskkonnaministeerium asendatakse Kliimaministeeriumiga.</tavatekst>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")
    reorg_ops = _parse_generic_ministry_reorganization_ops(
        reorg_xml,
        source_id="ee/test",
        target_title="Testseadus",
    )
    assert reorg_ops
    assert reorg_ops[0].text_patch is not None
    assert reorg_ops[0].text_patch.selector.match_text
    assert reorg_ops[0].text_patch.replacement


def test_extract_ee_ops_keeps_right_quote_only_replace_payload() -> None:
    ops = extract_ee_ops(
        (
            "Välisriigi kutsekvalifikatsiooni tunnustamise seaduse § 7 lõike 4 teine lause "
            "muudetakse ja sõnastatakse järgmiselt: ”Käesoleva seaduse tähenduses on "
            "tugikeskus Haridus- ja Noorteamet, mis täidab Eestis akadeemilise "
            "tunnustamise infokeskuse (ENIC/NARIC keskus) ülesandeid.”"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "7"), ("subsection", "4"))
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("Käesoleva seaduse tähenduses on tugikeskus")


def test_extract_ee_ops_strips_terminal_quote_residue_from_marker_fallback_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 10 lõige 1 muudetakse ja sõnastatakse järgmiselt: "
            "Mittetöötavale puudega õppurile, kes õpib gümnaasiumi 10.–12. klassis, "
            "kutseõppeasutuses või kõrgkoolis või on arvatud Sotsiaalministeeriumi "
            "hallatava riigiasutuse statsionaarse õppega täienduskoolituse kursuse "
            "nimekirja ja kellel on puudest tingituna õppetööga seotud lisakulutusi, "
            "makstakse igakuiselt, välja arvatud juuli-ja augustikuu, õppetoetust.”."
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "10"), ("subsection", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.text.endswith("õppetoetust.")
    assert not ops[0].payload.text.endswith("õppetoetust.”.")
    assert "„" not in ops[0].payload.text[-4:]


def test_parse_target_prefers_insert_form_section_ref_over_quoted_body_cross_reference() -> None:
    target = parse_target(
        (
            "Pärimisseadust täiendatakse §-ga 165 1 järgmises sõnastuses: "
            "„§ 165 1 . Euroopa Parlamendi ja nõukogu määruse (EL) 2020/1783 rakendamine\x01 "
            "(1) Eesti notar loetakse Euroopa Parlamendi ja nõukogu määruse (EL) 2020/1783, "
            "mis käsitleb liikmesriikide kohtute vahelist koostööd tõendite kogumisel tsiviil- "
            "ja kaubandusasjades, artikli 2 punkti 1 tähenduses kohtuks pärimisasja menetlemisel. "
            "(2) Eesti notari taotlusel mõnes muus Euroopa Liidu liikmesriigis tõendite kogumiseks "
            "abi osutamisele tema menetluses olevas pärimisasjas kohaldatakse käesolevas seaduses "
            "sätestatut niivõrd, kuivõrd Euroopa Parlamendi ja nõukogu määruses (EL) 2020/1783 "
            "sätestatust ei tulene teisiti.”."
        )
    )

    assert target is not None
    assert target.path == (("section", "165_1"),)


def test_parse_target_keeps_part_qualified_chapter_heading() -> None:
    target = parse_target("seaduse 7. osa 1. peatüki pealkiri muudetakse ja sõnastatakse järgmiselt")

    assert target is not None
    assert target.path == (("part", "7"), ("chapter", "1"))
    assert target.special is FacetKind.HEADING


def test_extract_ee_ops_recovers_part_repeal_target() -> None:
    ops = extract_ee_ops(
        "määruse 3. osa tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("part", "3"),)


def test_extract_ee_ops_fans_out_shared_heading_text_replace_targets() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 3. peatüki ja 3. peatüki 3. jao pealkirjas asendatakse sõnad "
            "„reederi ja kapteni” sõnadega „reederi, kapteni ja riigi”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 2
    assert [op.target for op in ops] == [
        LegalAddress(path=(("chapter", "3"),), special=FacetKind.HEADING),
        LegalAddress(path=(("chapter", "3"), ("division", "3")), special=FacetKind.HEADING),
    ]
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch.selector.match_text == "reederi ja kapteni" for op in ops if op.text_patch is not None)
    assert all(op.text_patch.replacement == "reederi, kapteni ja riigi" for op in ops if op.text_patch is not None)


def test_extract_ee_ops_keeps_statute_level_section_insert_out_of_cross_reference_item_scope() -> None:
    ops = extract_ee_ops(
        (
            "Pärimisseadust täiendatakse §-ga 165 1 järgmises sõnastuses: "
            "„§ 165 1 . Euroopa Parlamendi ja nõukogu määruse (EL) 2020/1783 rakendamine\x01 "
            "(1) Eesti notar loetakse Euroopa Parlamendi ja nõukogu määruse (EL) 2020/1783, "
            "mis käsitleb liikmesriikide kohtute vahelist koostööd tõendite kogumisel tsiviil- "
            "ja kaubandusasjades, artikli 2 punkti 1 tähenduses kohtuks pärimisasja menetlemisel. "
            "(2) Eesti notari taotlusel mõnes muus Euroopa Liidu liikmesriigis tõendite kogumiseks "
            "abi osutamisele tema menetluses olevas pärimisasjas kohaldatakse käesolevas seaduses "
            "sätestatut niivõrd, kuivõrd Euroopa Parlamendi ja nõukogu määruses (EL) 2020/1783 "
            "sätestatust ei tulene teisiti.”."
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "165_1"),)


def test_extract_ee_ops_fans_out_mixed_text_replace_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 20 lõikes 6 ning § 60 lõikes 2 asendatakse sõna "
            "«politseiseadus» sõnadega «politsei ja piirivalve seadus» "
            "vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "20"), ("subsection", "6"))),
        (StructuralAction.TEXT_REPLACE, (("section", "60"), ("subsection", "2"))),
    ]
    assert all(_payload(op).attrs.get("old_text") == "politseiseadus" for op in ops)
    assert all(_payload(op).attrs.get("rewrite_mode") == "replace" for op in ops)
    assert all(_payload(op).attrs.get("case_inflected") is True for op in ops)


def test_extract_ee_ops_fans_out_item_and_subsection_text_replace_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 36 lõike 1 punktis 3 ja § 142 lõike 3 esimeses lauses "
            "asendatakse sõna «registreerimine» sõnaga «kinnitamine» "
            "vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "36"), ("subsection", "1"), ("item", "3"))),
        (StructuralAction.TEXT_REPLACE, (("section", "142"), ("subsection", "3"))),
    ]


def test_extract_ee_ops_does_not_retarget_replace_from_payload_cross_reference_items() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 6 lõige 2 muudetakse ja sõnastatakse järgmiselt: "
            "„(2) Käesoleva paragrahvi lõikes 1 sätestatud kindlustuskohustuse "
            "erisust ei kohaldata käesoleva seaduse § 4 lõike 1 punktides 3, 4 "
            "ja 5 ning lõikes 2 nimetatud sõidukite ja maastikusõidukite ning "
            "nende haagiste suhtes.”"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "6"), ("subsection", "2"))),
    ]


def test_extract_ee_ops_treats_insert_before_word_clause_as_text_replace() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 14 lõiget 2 täiendatakse enne sõna „sõidukit” sõnadega „registreerimisele kuuluvat”;"),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "14"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs.get("old_text") == "sõidukit"
    assert ops[0].payload.text == "registreerimisele kuuluvat sõidukit"
    assert ops[0].payload.attrs.get("rewrite_mode") == "insert_before"
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "sõidukit"
    assert ops[0].text_patch.replacement == "registreerimisele kuuluvat sõidukit"


def test_extract_ee_ops_handles_mixed_targets_with_section_inessive_shorthand() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 11 lõikes 2, §-s 78 ning § 80 lõigetes 1 ja 3 "
            "asendatakse sõnad „kindlustuskohustuse täitmine” sõnadega "
            "„liikluskindlustuse olemasolu” vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "11"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "78"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "80"), ("subsection", "1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "80"), ("subsection", "3"))),
    ]
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch.selector.match_text == "kindlustuskohustuse täitmine" for op in ops if op.text_patch)
    assert all(op.text_patch.replacement == "liikluskindlustuse olemasolu" for op in ops if op.text_patch)


def test_extract_ee_ops_recovers_later_same_section_target_after_quoted_pair() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 21 lõike 1 punktis 3 asendatakse tekstiosa "
            "„§ 4 punktis 4” tekstiosaga „§ 4 lõike 1 punktis 4” ja "
            "lõikes 4 asendatakse tekstiosa „lähevad lepingust” "
            "tekstiosaga „lähevad jõustunud lepingust”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "21"), ("subsection", "1"), ("item", "3"))),
        (StructuralAction.TEXT_REPLACE, (("section", "21"), ("subsection", "4"))),
    ]


def test_extract_ee_ops_recovers_same_section_heading_and_plural_subsections() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 8 pealkirjas ning lõigetes 1 ja 3 asendatakse sõnad "
            "„veterinaar- ja zootehnilise kontrolli” sõnaga "
            "„veterinaarkontrolli”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "8"),), FacetKind.HEADING),
            (StructuralAction.TEXT_REPLACE, (("section", "8"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "8"), ("subsection", "3")), None),
        ]
    )


def test_extract_ee_ops_recovers_mixed_heading_and_subsection_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 29 pealkirjas ja lõike 1 esimeses lauses ning "
            "§ 30 pealkirjas asendatakse sõnad "
            "„Ülalpidamishüvitise ja töövõimetushüvitise” sõnaga "
            "„Töövõimetushüvitise”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "29"),), FacetKind.HEADING),
            (StructuralAction.TEXT_REPLACE, (("section", "29"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "30"),), FacetKind.HEADING),
        ]
    )


def test_extract_ee_ops_recovers_heading_with_comma_prefixed_subsection_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 87 1 pealkirjas, lõike 1 sissejuhatavas lauseosas ja lõike 2 "
            "esimeses lauses asendatakse sõnad „elukoha andmete” sõnadega "
            "„linna ja linnaosa või valla täpsusega elukoha aadressi”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "87_1"),), FacetKind.HEADING),
            (StructuralAction.TEXT_REPLACE, (("section", "87_1"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "87_1"), ("subsection", "2")), None),
        ]
    )


def test_extract_ee_ops_recovers_section_intro_and_item_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 7 sissejuhatavas lauseosas ja punktis 1 asendatakse sõnad "
            "„kantserogeenid või mutageenid” tekstiosaga "
            "„kantserogeenid, mutageenid või reproduktiivtoksilised ained” "
            "vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path) for op in ops) == [
        (StructuralAction.TEXT_REPLACE, (("section", "7"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "7"), ("item", "1"))),
    ]
    assert read_subsection_text_scope_meta(_payload(ops[0])).intro_only is True
    assert read_subsection_text_scope_meta(_payload(ops[1])) is None


def test_extract_ee_ops_keeps_section_heading_when_same_clause_also_targets_subsection() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 45 pealkirja ja lõiget 1 täiendatakse pärast tekstiosa „tegemise,” tekstiosaga „soetamise,”;"),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "45"),), FacetKind.HEADING),
            (StructuralAction.TEXT_REPLACE, (("section", "45"), ("subsection", "1")), None),
        ]
    )


def test_extract_ee_ops_recovers_same_section_subsection_and_later_item_target() -> None:
    ops = extract_ee_ops(
        (
            "Meditsiiniseadme seaduse § 29 lõikes 1 ja lõike 2 punktis 7 "
            "asendatakse sõnad „Eesti Haigekassa” sõnaga „Tervisekassa”."
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "29"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "29"), ("subsection", "2"), ("item", "7")), None),
        ]
    )


def test_extract_ee_ops_recovers_leading_plural_section_targets_before_later_subsections() -> None:
    ops = extract_ee_ops(
        (
            "paragrahve 5 ja 6, § 36 lõiget 1 ning § 37 lõiget 1 ja lõike 2 "
            "sissejuhatavat lauseosa täiendatakse pärast tekstiosa "
            "„valdkonna eest vastutav minister” tekstiosaga "
            "„või tema volitatud ministeeriumi ametnik”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path, op.target.special) for op in ops) == sorted(
        [
            (StructuralAction.TEXT_REPLACE, (("section", "5"),), None),
            (StructuralAction.TEXT_REPLACE, (("section", "6"),), None),
            (StructuralAction.TEXT_REPLACE, (("section", "36"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "37"), ("subsection", "1")), None),
            (StructuralAction.TEXT_REPLACE, (("section", "37"), ("subsection", "2")), None),
        ]
    )


def test_extract_ee_ops_splits_combined_replace_and_delete_in_same_clause() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 93 lõikes 7 asendatakse arv „3” arvuga „4 2 ” ja lõikest jäetakse välja tekstiosa „ja 5–7”;"),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "93"), ("subsection", "7"))),
        (StructuralAction.TEXT_REPLACE, (("section", "93"), ("subsection", "7"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == "4 2"
    assert ops[0].payload.attrs["old_text"] == "3"
    assert ops[1].payload is not None
    assert ops[1].payload.text == ""
    assert ops[1].payload.attrs["old_text"] == "ja 5–7"


def test_extract_ee_ops_recovers_trailing_subsection_repeal_after_section_ref() -> None:
    ops = extract_ee_ops(
        "paragrahvi 26 lõige 9, § 27 ja § 28 lõige 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert sorted((op.action, op.target.path) for op in ops) == sorted(
        [
            (StructuralAction.REPEAL, (("section", "26"), ("subsection", "9"))),
            (StructuralAction.REPEAL, (("section", "27"),)),
            (StructuralAction.REPEAL, (("section", "28"), ("subsection", "2"))),
        ]
    )


def test_parse_ee_amendment_ops_skips_foreign_untitled_omnibus_intro_with_rt_parenthetical() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              Kriminaalmenetluse seadustiku ja teiste seaduste muutmise seaduses
              (RT I, 21.03.2011, 2) tehakse järgmised muudatused:
            </tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahv 2 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„<b>§ 2. Kriminaalmenetluse seadustiku rakendamise seaduse muutmine</b></p>
              <p>Kriminaalmenetluse seadustiku rakendamise seadust täiendatakse §-ga 25<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 25<sup>1</sup>. Jälitustoimingu lubade kehtivus</b></p>
              <p>Kuni 2012. aasta 31. detsembrini antud jälitustoimingu load kehtivad.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Jälitustegevuse seadus")

    assert ops == []


def test_parse_section_blocks_does_not_split_on_internal_section_citation() -> None:
    nodes = _parse_section_blocks(
        "§ 52 1 . Raamatupidamine likvideerimise ajal\x01 "
        "(1) Likvideeritav sihtasutus peab raamatupidamist. "
        "(2) Lõpetamisotsuse vastuvõtmisel koostavad likvideerijad likvideerimisaruande. "
        "(3) Lõpetamisotsusega võib otsustada eelneva majandusaasta perioodi muutmise "
        "tulenevalt raamatupidamise seaduse § 13 2. lõikest. "
        "(4) Kui käesoleva paragrahvi 3. lõikes nimetatud uue majandusaasta algusest "
        "on möödunud 12 kuud, koostatakse likvideerimise vahearuanne. "
        "(5) Sihtasutusel on audiitorkontrolli kohustus. "
        "(6) Kohus võib sihtasutuse vabastada audiitorkontrollist."
    )

    assert len(nodes) == 1
    assert nodes[0].label == "52_1"
    assert nodes[0].text == "Raamatupidamine likvideerimise ajal"
    assert [child.label for child in nodes[0].children] == ["1", "2", "3", "4", "5", "6"]
    assert nodes[0].children[2].text.endswith("raamatupidamise seaduse § 13 2. lõikest.")


def test_extract_ee_ops_fans_out_plural_item_text_replace_with_punkte_form() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 709 lõike 15 1 punkte 4 ja 5 täiendatakse pärast sõnu "
            "«nimetatud makseteenus» sõnadega «Euroopa Liidu piires»;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "709"), ("subsection", "15_1"), ("item", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "709"), ("subsection", "15_1"), ("item", "5"))),
    ]
    assert all(_payload(op).attrs.get("old_text") == "nimetatud makseteenus" for op in ops)
    assert all(_payload(op).text == "nimetatud makseteenus Euroopa Liidu piires" for op in ops)
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch.selector.match_text == "nimetatud makseteenus" for op in ops if op.text_patch)
    assert all(op.text_patch.replacement == "nimetatud makseteenus Euroopa Liidu piires" for op in ops if op.text_patch)


def test_extract_ee_ops_fans_out_same_section_subsection_text_replace_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 83 52 lõiget 2 ning lõike 3 esimest lauset täiendatakse "
            "pärast sõna „ettevõtja” tekstiosaga "
            "„ja sama paragrahvi lõikes 3 1 nimetatud ettevõtja”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "83_52"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "83_52"), ("subsection", "3"))),
    ]
    assert all(op.payload is not None for op in ops)
    assert all(op.payload.attrs.get("old_text") == "ettevõtja" for op in ops if op.payload is not None)


def test_extract_ee_ops_does_not_keep_bare_section_when_same_clause_names_subsections() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 14 lõikeid 1 ja 2 täiendatakse pärast sõna „jäätmeid” "
            "sõnadega „või põletatakse neid energiakasutuse otstarbel”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [op.target.path for op in ops] == [
        (("section", "14"), ("subsection", "1")),
        (("section", "14"), ("subsection", "2")),
    ]


def test_extract_ee_ops_splits_plural_section_replace_payload_by_section() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvid 18 ja 19 muudetakse ning sõnastatakse järgmiselt: "
            "«§ 18. Ekspordi fütosanitaarsertifikaadi kehtivus "
            "(1) Esimese paragrahvi tekst. "
            "§ 19. Ekspordi fütosanitaarsertifikaadi kasutamine "
            "Väljaandja kinnitamata muudatustega sertifikaat on kehtetu.»"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "18"),)),
        (StructuralAction.REPLACE, (("section", "19"),)),
    ]
    assert ops[0].payload is not None
    assert ops[1].payload is not None
    assert ops[0].payload.text.startswith("§ 18. Ekspordi fütosanitaarsertifikaadi kehtivus")
    assert "§ 19." not in ops[0].payload.text
    assert ops[1].payload.text.startswith("§ 19. Ekspordi fütosanitaarsertifikaadi kasutamine")


def test_extract_ee_ops_emits_section_renumber_before_insert_for_loetakse_paragrahviks_clause() -> None:
    ops = extract_ee_ops(
        (
            "Paragrahv 27 1 loetakse §-ks 27 2 ja seadust täiendatakse §-ga 27 1 "
            "järgmises sõnastuses: "
            "„§ 27 1. Abivajavast lapsest teatamata jätmine "
            "(1) Uus tekst.”"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 2
    assert ops[0].action is StructuralAction.RENUMBER
    assert ops[0].target.path == (("section", "27_1"),)
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "27_2"),)
    assert ops[1].action is StructuralAction.INSERT
    assert ops[1].target.path == (("section", "27_1"),)
    assert ops[1].payload is not None
    assert ops[1].payload.text.startswith("§ 27 1. Abivajavast lapsest teatamata jätmine")


def test_extract_ee_ops_emits_subsection_renumber_before_insert_for_loetakse_loikeks_clause() -> None:
    text = (
        "paragrahvi 7 lõige 2 1 loetakse lõikeks 2 2 ning paragrahvi täiendatakse "
        "lõikega 2 1 järgmises sõnastuses: „(2 1) Uus lõige.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.RENUMBER, (("section", "7"), ("subsection", "2_1"))),
        (StructuralAction.INSERT, (("section", "7"), ("subsection", "2_1"))),
    ]
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "7"), ("subsection", "2_2"))
    assert ops[0].witness_rule_id == "ee_subsection_sequence_renumber_before_insert"
    assert ops[1].witness_rule_id == "ee_subsection_sequence_renumber_before_insert"
    assert _payload(ops[1]).text == "(2 1) Uus lõige."


def test_extract_ee_ops_emits_senine_text_subsection_renumber_before_insert() -> None:
    text = (
        "paragrahvi 23 senine tekst loetakse lõikeks 2 ja paragrahvi täiendatakse "
        "lõikega 1 järgmises sõnastuses: „(1) Rakendusüksusel on õigus: "
        "1) tutvuda dokumentidega; 2) küsida lisateavet.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.RENUMBER, (("section", "23"), ("subsection", "1"))),
        (StructuralAction.INSERT, (("section", "23"), ("subsection", "1"))),
    ]
    assert ops[0].destination is not None
    assert ops[0].destination.path == (("section", "23"), ("subsection", "2"))
    assert ops[0].witness_rule_id == "ee_senine_text_subsection_renumber_before_insert"
    assert ops[1].witness_rule_id == "ee_senine_text_subsection_renumber_before_insert"
    assert _payload(ops[1]).text.startswith("(1) Rakendusüksusel on õigus:")


def test_extract_ee_ops_emits_plural_section_renumbers_before_new_occupied_section_insert() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvid 1 ja 1 1 loetakse §-deks 1 1 ja 1 2 ning määrust "
            "täiendatakse §-ga 1 järgmises sõnastuses: "
            "„§ 1. Määruse reguleerimisala Määrusega kehtestatakse: 1) loetelu.”"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [
        (op.action, op.target.path, op.destination.path if op.destination is not None else None)
        for op in ops[:2]
    ] == [
        (StructuralAction.RENUMBER, (("section", "1_1"),), (("section", "1_2"),)),
        (StructuralAction.RENUMBER, (("section", "1"),), (("section", "1_1"),)),
    ]
    assert [op.witness_rule_id for op in ops[:2]] == [
        "ee_section_sequence_renumber_before_insert",
        "ee_section_sequence_renumber_before_insert",
    ]
    assert ops[2].action is StructuralAction.INSERT
    assert ops[2].target.path == (("section", "1"),)
    assert ops[2].payload is not None
    assert ops[2].payload.text.startswith("§ 1. Määruse reguleerimisala")


def test_extract_ee_ops_does_not_emit_unpaired_plural_section_renumber() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvid 1 ja 1 1 loetakse §-deks 1 2 ning määrust "
            "täiendatakse §-ga 1 järgmises sõnastuses: "
            "„§ 1. Määruse reguleerimisala Määrusega kehtestatakse: 1) loetelu.”"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert all(op.action is not StructuralAction.RENUMBER for op in ops)
    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "1"),)


def test_extract_ee_ops_does_not_invent_section_renumber_for_appendix_replacement() -> None:
    text = (
        "Keskkonnaministri 22. mai 2013. a määruse nr 25 „Jahitunnistuse vorm, "
        "jahiteooriaeksami ja laskekatse sooritamise ning jahitunnistuse "
        "taotlemise ja andmise kord, jahindusalasele koolitusele ja koolitajale "
        "esitatavad nõuded ning koolitamise kord” lisa 5 kehtestatakse uues "
        "sõnastuses."
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="113092017001", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.META
    assert ops[0].target.path == ()
    assert ops[0].payload is None


def test_parse_ee_amendment_ops_carries_wrapper_section_to_numbered_subsection_items() -> None:
    archive = open_rt_archive(readonly=True)
    target_title = (
        "Üldgeoloogilise uurimistöö ning maavara geoloogilise uuringu kord ja "
        "nõuded ning nõuded fosforiidi, metallitoorme, põlevkivi, aluskorra "
        "ehituskivi, järvelubja, järvemuda, meremuda, kruusa, liiva, lubjakivi, "
        "dolokivi, savi ja turba omaduste kohta maavarana arvelevõtmiseks"
    )
    xml = fetch_rt_xml("129122024006", archive)

    ops = parse_ee_amendment_ops(xml, "ee/129122024006", target_title=target_title)

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "45"), ("subsection", "1"))),
        (StructuralAction.REPLACE, (("section", "45"), ("subsection", "2"))),
        (StructuralAction.REPEAL, (("section", "45"), ("subsection", "4"))),
        (StructuralAction.REPLACE, (("section", "45"), ("subsection", "5"))),
        (StructuralAction.REPLACE, (("section", "45"), ("subsection", "6"))),
    ]
    assert all(
        "old_format_amendment_section:6" in op.provenance_tags
        for op in ops
    )
    assert all(str(op.target).startswith("section:45") for op in ops)


def test_parse_ee_amendment_ops_strips_direct_target_title_before_chapter_repeals() -> None:
    archive = open_rt_archive(readonly=True)
    target_title = (
        "Üldise õigusnõustamise sihtgrupp ja õigusvaldkonnad ning selle "
        "nõustamise kättesaadavuse parandamiseks antava toetuse jagamise "
        "tingimused ja kord"
    )
    xml = fetch_rt_xml("117022021004", archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/117022021004",
        target_title=target_title,
        ref_effective="2021-02-20",
    )

    assert [(op.action, op.target.path) for op in ops[:3]] == [
        (StructuralAction.REPEAL, (("chapter", "1"),)),
        (StructuralAction.REPEAL, (("chapter", "3"),)),
        (StructuralAction.REPEAL, (("chapter", "5"),)),
    ]
    assert [op.witness_rule_id for op in ops[:3]] == [
        "ee_direct_target_title_prefix_stripped_for_structural_repeal",
        "ee_direct_target_title_prefix_stripped_for_structural_repeal",
        "ee_direct_target_title_prefix_stripped_for_structural_repeal",
    ]
    assert all("old_format_amendment_section:38" in op.provenance_tags for op in ops[:3])


def test_extract_ee_ops_keeps_trailing_same_section_subsection_repeal_after_item_repeal() -> None:
    ops = extract_ee_ops(
        "paragrahvi 14 lõike 1 punkt 7 ja lõige 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "14"), ("subsection", "1"), ("item", "7"))),
        (StructuralAction.REPEAL, (("section", "14"), ("subsection", "2"))),
    ]


def test_extract_ee_ops_keeps_trailing_same_section_item_repeal_after_item_repeal() -> None:
    ops = extract_ee_ops(
        "paragrahvi 8 1 lõike 6 punkt 1, lõike 8 punkt 5 ja lõige 12 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "8_1"), ("subsection", "6"), ("item", "1"))),
        (StructuralAction.REPEAL, (("section", "8_1"), ("subsection", "8"), ("item", "5"))),
        (StructuralAction.REPEAL, (("section", "8_1"), ("subsection", "12"))),
    ]


def test_extract_ee_ops_keeps_plain_subsections_before_same_section_item_targets_in_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 30 lõikes 4 ja lõike 5 punktis 1, § 54 5 lõikes 2 ja lõike 4 punktis 1 "
            "ning § 62 lõikes 2 asendatakse tekstiosa „§-des 391–393” tekstiosaga „§-s 391 või 393”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert {(op.action, op.target.path) for op in ops} == {
        (StructuralAction.TEXT_REPLACE, (("section", "30"), ("subsection", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "30"), ("subsection", "5"), ("item", "1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "54_5"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "54_5"), ("subsection", "4"), ("item", "1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "62"), ("subsection", "2"))),
    }
    assert all(_payload(op).text == "§-s 391 või 393" for op in ops)
    assert all(_payload(op).attrs.get("old_text") == "§-des 391–393" for op in ops)


def test_extract_ee_ops_keeps_secondary_same_section_plural_subsection_repeals_after_section_list() -> None:
    ops = extract_ee_ops(
        "paragrahvid 8–10 ning § 11 lõiked 1 ja 3 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "8"),)),
        (StructuralAction.REPEAL, (("section", "9"),)),
        (StructuralAction.REPEAL, (("section", "10"),)),
        (StructuralAction.REPEAL, (("section", "11"), ("subsection", "1"))),
        (StructuralAction.REPEAL, (("section", "11"), ("subsection", "3"))),
    ]


def test_extract_ee_ops_expands_mixed_plural_item_ranges_and_same_clause_subsection_repeals() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 14 lõike 1 punktid 3 1, 4, 5 1–8 ja 11–18 ning lõiked 2–4 tunnistatakse kehtetuks;"),
        OperationSource(statute_id="ee/test"),
    )

    targets = {(op.action, op.target.path) for op in ops}

    for item_label in ("3_1", "4", "5_1", "6", "7", "8", "11", "12", "13", "14", "15", "16", "17", "18"):
        assert (StructuralAction.REPEAL, (("section", "14"), ("subsection", "1"), ("item", item_label))) in targets
    for sub_label in ("2", "3", "4"):
        assert (StructuralAction.REPEAL, (("section", "14"), ("subsection", sub_label))) in targets


def test_extract_ee_ops_keeps_trailing_section_item_repeal_after_plural_subsection_list() -> None:
    ops = extract_ee_ops(
        "paragrahvi 36 lõiked 5–7 ja 9 ning § 37 lõike 1 punkt 4 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert {(op.action, op.target.path) for op in ops} == {
        (StructuralAction.REPEAL, (("section", "36"), ("subsection", "5"))),
        (StructuralAction.REPEAL, (("section", "36"), ("subsection", "6"))),
        (StructuralAction.REPEAL, (("section", "36"), ("subsection", "7"))),
        (StructuralAction.REPEAL, (("section", "36"), ("subsection", "9"))),
        (StructuralAction.REPEAL, (("section", "37"), ("subsection", "1"), ("item", "4"))),
    }


def test_parse_ee_amendment_ops_strictly_filters_similar_statute_titles_in_omnibus_act() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/oigusakt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Kohtute seaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Kohtute seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>Kohtute seaduses tehakse järgmised muudatused:</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> seadust täiendatakse §-ga 9<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 9<sup>1</sup>. Maakohtu tsiviilosakond ja süüteoosakond</b></p>
              <p>(1) Maakohtus on tsiviilosakond ja süüteoosakond.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <paragrahvPealkiri>Kohtutäituri seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>Kohtutäituri seaduse §-s 18 asendatakse tekstiosa „§ 117 1” tekstiosaga „§ 119 1 käesolevas seaduses sätestatud erisustega”.</tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Kohtutäituri seadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert all(("section", "9_1") not in op.target.path for op in ops)


def test_parse_ee_amendment_ops_does_not_match_prefix_title_as_application_law() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Kaitseväeteenistuse seaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Kaitseväeteenistuse seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Kaitseväeteenistuse seaduses tehakse järgmised muudatused:</p>
              <p><b>1)</b> seadust täiendatakse §-ga 7<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 7<sup>1</sup>. Vabatahtlik teenistus</b></p>
              <p>(1) Vabatahtlik teenistus on test.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Kaitseväeteenistuse seaduse rakendamise seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Kaitseväeteenistuse seaduse rakendamise seaduse 1. peatükki täiendatakse §-ga 39<sup>15</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 39<sup>15</sup>. Üleminekusäte</b></p>
              <p>(1) Test.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Kaitseväeteenistuse seaduse rakendamise seadus",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("chapter", "1"), ("section", "39_15"))),
    ]
    assert all(("section", "7_1") not in op.target.path for op in ops)


def test_parse_ee_amendment_ops_strict_title_match_accepts_compound_genitive_title() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Taimekaitseseaduse muutmise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Taimekaitseseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Taimekaitseseaduses tehakse järgmised muudatused:</p>
              <p><b>1)</b> seadust täiendatakse §-ga 45<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 45<sup>1</sup>. Test</b></p>
              <p>(1) Test.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Taimekaitseseadus",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "45_1"),)),
    ]


def test_parse_ee_amendment_ops_extracts_embedded_intro_target_section_from_html() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>152)</b> paragrahvi 112<sup>1</sup> tekst muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„Riigilõivuseaduses tehakse järgmised muudatused:</p>
              <p><b>10)</b> seaduse 3. osa 11. peatüki 2. jagu täiendatakse 6. jaotisega järgmises sõnastuses:</p>
              <p align="center">„<b>6. jaotis<br/>Rahapesu ja terrorismi rahastamise tõkestamise seaduse alusel tehtavad toimingud</b></p>
              <p><b>§ 256<sup>1</sup>. Rahapesu ja terrorismi rahastamise tõkestamise seaduse alusel väljastatava tegevusloa taotluse läbivaatamine</b></p>
              <p>Rahapesu ja terrorismi rahastamise tõkestamise seaduse alusel väljastatava tegevusloa taotluse läbivaatamise eest tasutakse riigilõivu 343,20 eurot.”;</p>
              <p><b>153)</b> paragrahvi 118 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„<b>1)</b> paragrahvi 63 tekst muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„(1) Paljundus- ja kultiveerimismaterjali tarnija ...”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Riigilõivuseadus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("chapter", "11"), ("division", "2"), ("section", "256_1"))),
    ]
    assert ops[0].payload is not None
    assert "Rahapesu ja terrorismi rahastamise tõkestamise seaduse alusel" in ops[0].payload.text
    assert all(("section", "63") not in op.target.path for op in ops)


def test_parse_ee_amendment_ops_materializes_generic_justice_ministry_reorg() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/oigusakt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Taristuseaduse muutmise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>11</paragrahvNr>
          <paragrahvPealkiri>Rakendussätted</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>11)</b> paragrahvi 105<sup>19</sup> täiendatakse lõigetega 10–12 järgmises sõnastuses:</p>
              <p>„(10) Justiitsministeerium korraldatakse ümber Justiits- ja Digiministeeriumiks.</p>
              <p>(12) Kehtivates ja tulevikus jõustuvates õigusaktides loetakse sõna „Justiitsministeerium” asendatuks sõnadega „Justiits- ja Digiministeerium” vastavas käändes.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Kohtutäituri seadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == ()
    assert ops[0].payload is not None
    assert ops[0].payload.attrs.get("old_text") == "Justiitsministeerium"
    assert ops[0].payload.text == "Justiits- ja Digiministeerium"
    assert ops[0].payload.attrs.get("case_inflected") is True


def test_extract_ee_ops_flattens_division_jaotis_insert_into_section_inserts() -> None:
    source = OperationSource(statute_id="ee/test", title="Kohtutäituri seaduse muutmine")

    ops = extract_ee_ops(
        (
            "21) seaduse 3. peatüki 2. jagu täiendatakse 4 1. jaotisega järgmises sõnastuses: "
            "„4 1. jaotis Metoodikakomisjon § 97 1. Metoodikakomisjon "
            "(1) Metoodikakomisjon moodustatakse vähemalt viieliikmelisena viieks aastaks. "
            "§ 97 2. Metoodikakomisjoni pädevus "
            "Metoodikakomisjon: 1) korraldab ja viib läbi usaldusisiku eksami.”;"
        ),
        source,
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("chapter", "3"), ("division", "2"), ("section", "97_1"))),
        (StructuralAction.INSERT, (("chapter", "3"), ("division", "2"), ("section", "97_2"))),
    ]


def test_parse_ee_amendment_ops_recurses_into_nested_muutmispunkt_wrapper() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Testmuudatus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <paragrahvPealkiri>Kohtutäituri seaduse täiendamine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 2 täiendatakse muutmispunktiga 13<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>13<sup>1</sup>)</b> paragrahvi 37<sup>1</sup> täiendatakse lõikega 5 järgmises sõnastuses:</p>
              <p>„(5) Välisriigist laekuva elatise vahendamise tasu maksmise täpsemad tingimused ja korra kehtestab valdkonna eest vastutav minister määrusega.”;”;</p>
              <p><b>2)</b> paragrahvi 2 täiendatakse muutmispunktiga 14<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>14<sup>1</sup>)</b> seadust täiendatakse §-ga 37<sup>3</sup> järgmises sõnastuses:</p>
              <p>„§ 37<sup>3</sup>. Riigi makstava tasu väljamaksmine</p>
              <p>Käesoleva seaduse §-des 37<sup>1</sup> ja 37<sup>2</sup> nimetatud tasu väljamaksmist kohtutäituritele korraldab koda.”;”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Kohtutäituri seadus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "37_1"), ("subsection", "5"))),
        (StructuralAction.INSERT, (("section", "37_3"),)),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == (
        "(5) Välisriigist laekuva elatise vahendamise tasu maksmise täpsemad "
        "tingimused ja korra kehtestab valdkonna eest vastutav minister määrusega."
    )
    assert ops[1].payload is not None
    assert ops[1].payload.text == (
        "§ 37 3 . Riigi makstava tasu väljamaksmine Käesoleva seaduse §-des "
        "37 1 ja 37 2 nimetatud tasu väljamaksmist kohtutäituritele korraldab koda."
    )


def test_parse_ee_statute_skips_blank_alampunkt_editorial_placeholder() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>9</peatykkNr>
          <peatykkPealkiri>Peatukk</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr ylaIndeks="1">46</paragrahvNr>
            <kuvatavNr><![CDATA[§ 46<sup>1</sup>. ]]></kuvatavNr>
            <paragrahvPealkiri>Järelevalve</paragrahvPealkiri>
            <loige>
              <loigeNr>5</loigeNr>
              <sisuTekst>
                <tavatekst>Kehtiv tekst.</tavatekst>
              </sisuTekst>
              <alampunkt>
                <alampunktNr />
                <kuvatavNr />
                <sisuTekst>
                  <HTMLKonteiner><![CDATA[<br/><p>Vana sõnastus</p>]]></HTMLKonteiner>
                </sisuTekst>
              </alampunkt>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    subsection = statute.body.children[0].children[0].children[0]

    assert subsection.text == "Kehtiv tekst."
    assert subsection.children == ()


def test_parse_ee_statute_flattens_jaotis_sections_under_parent_division() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>3</peatykkNr>
          <peatykkPealkiri>Koda</peatykkPealkiri>
          <jagu>
            <jaguNr>2</jaguNr>
            <jaguPealkiri>Koja organid</jaguPealkiri>
            <jaotis>
              <jaotisNr ylaIndeks="1">4</jaotisNr>
              <jaotisPealkiri>Metoodikakomisjon</jaotisPealkiri>
              <paragrahv>
                <paragrahvNr ylaIndeks="1">97</paragrahvNr>
                <kuvatavNr><![CDATA[§ 97<sup>1</sup>. ]]></kuvatavNr>
                <paragrahvPealkiri>Metoodikakomisjon</paragrahvPealkiri>
                <loige>
                  <loigeNr>1</loigeNr>
                  <sisuTekst>
                    <tavatekst>Metoodikakomisjon moodustatakse.</tavatekst>
                  </sisuTekst>
                </loige>
              </paragrahv>
              <paragrahv>
                <paragrahvNr ylaIndeks="2">97</paragrahvNr>
                <kuvatavNr><![CDATA[§ 97<sup>2</sup>. ]]></kuvatavNr>
                <paragrahvPealkiri>Metoodikakomisjoni pädevus</paragrahvPealkiri>
                <loige>
                  <loigeNr>1</loigeNr>
                  <sisuTekst>
                    <tavatekst>Metoodikakomisjon korraldab eksamit.</tavatekst>
                  </sisuTekst>
                </loige>
              </paragrahv>
            </jaotis>
          </jagu>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    division = statute.body.children[0].children[0]

    assert division.kind == IRNodeKind.DIVISION
    assert [(child.kind, child.label, child.text) for child in division.children] == [
        (IRNodeKind.SECTION, "97_1", "Metoodikakomisjon"),
        (IRNodeKind.SECTION, "97_2", "Metoodikakomisjoni pädevus"),
    ]


def test_parse_ee_statute_flattens_alljaotis_sections_under_parent_division() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>2</peatykkNr>
          <peatykkPealkiri>Koda</peatykkPealkiri>
          <jagu>
            <jaguNr>5</jaguNr>
            <jaguPealkiri>Tasud</jaguPealkiri>
            <jaotis>
              <jaotisNr>3</jaotisNr>
              <jaotisPealkiri>Riigi tasu</jaotisPealkiri>
              <alljaotis>
                <alljaotisNr ylaIndeks="1">2</alljaotisNr>
                <alljaotisPealkiri>Väljamaksmine</alljaotisPealkiri>
                <paragrahv>
                  <paragrahvNr ylaIndeks="3">37</paragrahvNr>
                  <kuvatavNr><![CDATA[§ 37<sup>3</sup>. ]]></kuvatavNr>
                  <paragrahvPealkiri>Riigi makstava tasu väljamaksmine</paragrahvPealkiri>
                  <loige>
                    <loigeNr>1</loigeNr>
                    <sisuTekst>
                      <tavatekst>Koda korraldab väljamaksmist.</tavatekst>
                    </sisuTekst>
                  </loige>
                </paragrahv>
              </alljaotis>
            </jaotis>
          </jagu>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0].children[0]

    assert section.kind == IRNodeKind.SECTION
    assert section.label == "37_3"
    assert section.text == "Riigi makstava tasu väljamaksmine"
    assert section.attrs["jaotis"] == "3"
    assert section.attrs["alljaotis"] == "2"


def test_parse_section_payload_keeps_superscript_item_label_together() -> None:
    parsed = _parse_section_payload(
        (
            "§ 78. Koja ülesanded\x01 Koja ülesanded on muu hulgas: "
            "8) kohtutäiturite ning pankrotihaldurite ja saneerimisnõustajate väljaõppe läbiviimine; "
            "8 1) usaldusisikute esmase koolituse korraldamine; "
            "9) kohtutäituri, kohtutäituri abi, pankrotihalduri, saneerimisnõustaja ja usaldusisiku eksami läbiviimine."
        ),
        kind=IRNodeKind.SECTION,
    )

    assert parsed.children[0].label == "1"
    assert [(item.label, item.text) for item in parsed.children[0].children] == [
        ("8", "kohtutäiturite ning pankrotihaldurite ja saneerimisnõustajate väljaõppe läbiviimine;"),
        ("8_1", "usaldusisikute esmase koolituse korraldamine;"),
        (
            "9",
            "kohtutäituri, kohtutäituri abi, pankrotihalduri, saneerimisnõustaja ja usaldusisiku eksami läbiviimine.",
        ),
    ]


def test_parse_ee_statute_splits_embedded_appendix_block_into_following_subsections() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>15</peatykkNr>
          <peatykkPealkiri>Rakendussätted</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>79</paragrahvNr>
            <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Käesolev seadus jõustub 2001. aasta 1. veebruaril.</tavatekst>
              </sisuTekst>
              <sisuTekst>
                <HTMLKonteiner><![CDATA[
                  <table summary="seotud dokument"><tr><td><font size="-1">Lisa 1</font></td></tr></table>
                ]]></HTMLKonteiner>
                <tavatekst><b>MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE</b></tavatekst>
                <tavatekst>Käesolevas seaduses tuleb termineid mõista alljärgnevalt.</tavatekst>
                <HTMLKonteiner><![CDATA[
                  <table summary="seotud dokument"><tr><td><font size="-1">Lisa 2</font></td></tr></table>
                ]]></HTMLKonteiner>
                <tavatekst>LIIKUMISPUUDEGA INIMESE SÕIDUKI PARKIMISKAART</tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Käesolev seadus jõustub 2001. aasta 1. veebruaril."),
        ("2", "Lisa 1"),
        (
            "3",
            "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
            "Käesolevas seaduses tuleb termineid mõista alljärgnevalt. "
            "Lisa 2 LIIKUMISPUUDEGA INIMESE SÕIDUKI PARKIMISKAART",
        ),
    ]


def test_parse_ee_statute_preserves_appendix_table_html_text() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>15</peatykkNr>
          <peatykkPealkiri>Rakendussätted</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>79</paragrahvNr>
            <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
            <loige>
              <loigeNr>1</loigeNr>
              <sisuTekst>
                <tavatekst>Käesolev seadus jõustub 2001. aasta 1. veebruaril.</tavatekst>
              </sisuTekst>
              <sisuTekst>
                <HTMLKonteiner><![CDATA[
                  <table summary="seotud dokument"><tr><td><font size="-1">Lisa 1</font></td></tr></table>
                ]]></HTMLKonteiner>
                <tavatekst><b>MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE</b></tavatekst>
                <HTMLKonteiner><![CDATA[
                  <table class="data">
                    <tr><td>Kategooria</td><td>Sõiduki liik ja iseloomustus</td></tr>
                    <tr><td>B</td><td>auto kuni 3500 kg</td></tr>
                    <tr><td>BE</td><td>autorong</td></tr>
                  </table>
                ]]></HTMLKonteiner>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Käesolev seadus jõustub 2001. aasta 1. veebruaril."),
        ("2", "Lisa 1"),
        (
            "3",
            "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
            "Kategooria Sõiduki liik ja iseloomustus B auto kuni 3500 kg BE autorong",
        ),
    ]


def test_parse_ee_statute_drops_orphan_appendix_marker_html_from_subsection_text() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>5</peatykkNr>
          <peatykkPealkiri>Rakendussätted</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>32</paragrahvNr>
            <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
            <loige>
              <loigeNr>2</loigeNr>
              <sisuTekst>
                <tavatekst>Käesolev norm jõustub 2011. aasta 1. jaanuaril.</tavatekst>
              </sisuTekst>
              <sisuTekst>
                <HTMLKonteiner><![CDATA[
                  <table><tr><td><p align="center"><br/>Lisa 1<br/>seaduse juurde</p></td></tr></table>
                ]]></HTMLKonteiner>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("2", "Käesolev norm jõustub 2011. aasta 1. jaanuaril."),
    ]
    assert section.children[0].attrs["source_cleanup_rule"] == "ee_drop_orphan_appendix_marker_html"
    assert section.children[0].attrs["dropped_appendix_marker"] == "Lisa 1"


def test_parse_ee_statute_preserves_table_html_in_existing_appendix_subsection() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>15</peatykkNr>
          <peatykkPealkiri>Rakendussätted</peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>79</paragrahvNr>
            <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
            <loige>
              <loigeNr>4</loigeNr>
              <sisuTekst>
                <tavatekst><b>MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE</b></tavatekst>
              </sisuTekst>
              <sisuTekst>
                <HTMLKonteiner><![CDATA[
                  <table class="data">
                    <tr><td>Kategooria</td><td>Sõiduki liik ja iseloomustus</td></tr>
                    <tr><td>B</td><td>auto kuni 3500 kg</td></tr>
                  </table>
                ]]></HTMLKonteiner>
                <tavatekst>Käesolevas seaduses tuleb termineid mõista alljärgnevalt.</tavatekst>
              </sisuTekst>
            </loige>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    section = statute.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        (
            "4",
            "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
            "Kategooria Sõiduki liik ja iseloomustus B auto kuni 3500 kg "
            "Käesolevas seaduses tuleb termineid mõista alljärgnevalt.",
        ),
    ]


def test_parse_ee_statute_keeps_chapter_title_with_inline_bold_and_reavahetus() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi>
        <nimi>
          <pealkiri>Testseadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <peatykk>
          <peatykkNr>12</peatykkNr>
          <peatykkPealkiri>
            <b>ESIMENE PEALKIRI.</b>
            <reavahetus />
            <b>TEINE PEALKIRI</b>
          </peatykkPealkiri>
          <paragrahv>
            <paragrahvNr>62</paragrahvNr>
            <paragrahvPealkiri>Liiklusregister</paragrahvPealkiri>
          </paragrahv>
        </peatykk>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    chapter = statute.body.children[0]

    assert chapter.text == "ESIMENE PEALKIRI. TEINE PEALKIRI"


def test_parse_html_op_items_strips_spacing_inside_parentheses() -> None:
    items = parse_html_op_items(
        (
            "<p><b>1)</b> paragrahvi 12<sup>6</sup> lõike 1 punkt 4 muudetakse ja "
            "sõnastatakse järgmiselt:</p>"
            "<p>„tema õppekava on liigitatud õppekavarühma rahvusvahelise ühtse "
            "hariduse liigituse ISCED ( International Standard Classification of "
            "Education ) alusel.”</p>"
        )
    )

    assert items == [
        "1) paragrahvi 12 6 lõike 1 punkt 4 muudetakse ja sõnastatakse järgmiselt: "
        "„tema õppekava on liigitatud õppekavarühma rahvusvahelise ühtse hariduse "
        "liigituse ISCED (International Standard Classification of Education) alusel.”"
    ]


def test_parse_html_op_items_splits_parenthesized_old_format_markers() -> None:
    items = parse_html_op_items(
        (
            "<p><b>(8)</b> Paragrahv 73 tunnistatakse kehtetuks.</p>"
            "<p><b>(9)</b> Paragrahv 74<sup>39</sup> tunnistatakse kehtetuks.</p>"
        )
    )

    assert items == [
        "(8) Paragrahv 73 tunnistatakse kehtetuks.",
        "(9) Paragrahv 74 39 tunnistatakse kehtetuks.",
    ]


def test_parse_html_op_items_splits_uppercase_old_format_markers() -> None:
    items = parse_html_op_items(
        (
            "<P><b> 1) </b> paragrahvi 2 lõikes 1 asendatakse sõna „a” sõnaga „b”;</P>"
            "<P><strong> 2) </strong> paragrahvi 3 lõige 1 sõnastatakse järgmiselt:</P>"
            "<P>„(1) Uus tekst.”;</P>"
        )
    )

    assert items == [
        "1) paragrahvi 2 lõikes 1 asendatakse sõna „a” sõnaga „b”;",
        "2) paragrahvi 3 lõige 1 sõnastatakse järgmiselt: „(1) Uus tekst.”;",
    ]


def test_parse_html_op_items_splits_plain_paragraph_item_markers() -> None:
    items = parse_html_op_items(
        (
            "<p>1) paragrahvi 2 täiendatakse punktiga 7 järgmises sõnastuses:</p>"
            "<p>„7) <b>IT õppevahendid</b> – digitaalsed õppevahendid.”;</p>"
            "<p>2) paragrahvi 3 lõige 2 sõnastatakse järgmiselt:</p>"
            "<p>„(2) Uus lõike tekst.”.</p>"
        ),
        allow_plain_paragraph_items=True,
    )

    assert items == [
        "1) paragrahvi 2 täiendatakse punktiga 7 järgmises sõnastuses: "
        "„7) IT õppevahendid – digitaalsed õppevahendid.”;",
        "2) paragrahvi 3 lõige 2 sõnastatakse järgmiselt: „(2) Uus lõike tekst.”.",
    ]


def test_parse_html_op_items_splits_plain_partitive_act_item_markers() -> None:
    items = parse_html_op_items(
        (
            "<p>10) paragrahv 16 sõnastatakse järgmiselt:</p>"
            "<p>„§ 16. Nõuded toetuse saajale</p>"
            "<p>(1) Uus § 16 tekst.”;</p>"
            "<p>11) määrust täiendatakse §-ga 16<sup>1</sup> järgmises sõnastuses:</p>"
            "<p>„§ 16<sup>1</sup>. Tegevustega seotud muudatused</p>"
            "<p>(1) Uus § 16<sup>1</sup> tekst.”;</p>"
        ),
        allow_plain_paragraph_items=True,
    )

    assert items == [
        "10) paragrahv 16 sõnastatakse järgmiselt: „§ 16. Nõuded toetuse saajale "
        "(1) Uus § 16 tekst.”;",
        "11) määrust täiendatakse §-ga 16 1 järgmises sõnastuses: "
        "„§ 16 1 . Tegevustega seotud muudatused (1) Uus § 16 1 tekst.”;",
    ]


def test_parse_html_op_items_keeps_numbered_payload_paragraphs_inside_open_quote() -> None:
    items = parse_html_op_items(
        (
            "<p>11) määrust täiendatakse §-ga 16<sup>1</sup> järgmises sõnastuses:</p>"
            "<p>“<b>§ 16<sup>1</sup>. Tegevustega seotud muudatused</b></p>"
            "<p>(1) Esimene lõige.</p>"
            "<p>(2) Lõikes 1 nimetatud juhul esitatakse:</p>"
            "<p>1) esimene dokument;</p>"
            "<p>2) paragrahvis 7 nimetatud hinnapakkumus;</p>"
            "<p>(3) Kolmas lõige.”;</p>"
            "<p>12) paragrahvi 18 täiendatakse lõikega 1<sup>1</sup>:</p>"
            "<p>“(1<sup>1</sup>) Järgmine muudatus.”;</p>"
        ),
        allow_plain_paragraph_items=True,
    )

    assert len(items) == 2
    assert items[0].startswith("11) määrust täiendatakse")
    assert "2) paragrahvis 7 nimetatud hinnapakkumus" in items[0]
    assert "(3) Kolmas lõige.”;" in items[0]
    assert items[1].startswith("12) paragrahvi 18 täiendatakse")


def test_parse_html_op_items_does_not_split_plain_quoted_payload_items() -> None:
    items = parse_html_op_items(
        (
            "<p>„1) esimene payloadi punkt;</p>"
            "<p>2) teine payloadi punkt.”</p>"
            "<p>(1) Payloadi lõige.</p>"
        ),
        allow_plain_paragraph_items=True,
    )

    assert items == []


def test_parse_ee_amendment_ops_splits_plain_paragraph_items_for_2022_001() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128062022001", archive),
        "ee/128062022001",
        target_title=(
            "„Mitmekesine ja kvaliteetne haridus digitaalse õppevaraga” "
            "elluviimiseks struktuuritoetuse andmise tingimused ja kord"
        ),
    )

    targets = {op.target.path for op in ops}
    assert len(ops) == 17
    assert (("section", "2"), ("item", "7")) in targets
    assert (("section", "3"), ("subsection", "2")) in targets
    assert (("section", "10"), ("subsection", "2"), ("item", "1")) in targets
    assert (("section", "27"), ("item", "4")) in targets


def test_parse_ee_amendment_ops_splits_partitive_plain_act_item_for_2012_001() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("120072012001", archive),
        "ee/120072012001",
        target_title=(
            "Mikropõllumajandusettevõtte arendamise investeeringutoetuse saamise "
            "nõuded, toetuse taotlemise ja taotluse menetlemise täpsem kord"
        ),
    )

    by_target = {op.target.path: op for op in ops}
    section_16 = by_target[(("section", "16"),)]
    section_16_1 = by_target[(("section", "16_1"),)]

    assert section_16.action == StructuralAction.REPLACE
    assert section_16.payload is not None
    assert "Nõuded toetuse saajale" in section_16.payload.text
    assert "Tegevustega seotud muudatused" not in section_16.payload.text
    assert section_16_1.action == StructuralAction.INSERT
    assert section_16_1.payload is not None
    assert "Tegevustega seotud muudatused" in section_16_1.payload.text


def test_parse_ee_amendment_ops_splits_flat_plain_paragraph_items_for_2013_011() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("129052013011", archive),
        "ee/129052013011",
        target_title="Klastrite arendamise toetamise tingimused ja kord",
    )

    targets = {op.target.path for op in ops}
    assert len(ops) == 20
    assert (("section", "7"), ("subsection", "1")) in targets
    assert (("section", "8"), ("subsection", "1")) in targets
    assert (("section", "21"), ("subsection", "9_2")) in targets
    assert (("section", "21"), ("subsection", "9_3")) in targets
    assert all("ee_plain_paragraph_html_items_extracted" in op.provenance_tags for op in ops)


def test_parse_ee_amendment_ops_prefers_old_format_plain_html_over_preambul_recovery_for_2011_003() -> None:
    archive = open_rt_archive(readonly=True)
    target_title = parse_ee_statute(fetch_rt_xml("123112010049", archive)).title
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("122072011003", archive),
        "ee/122072011003",
        target_title=target_title,
    )

    rule_id = "ee_old_format_html_section_preferred_over_preambul_plain_body"
    by_target = {op.target.path: op for op in ops}

    assert len(ops) == 34
    assert sum(1 for op in ops if op.action is StructuralAction.META) == 0
    assert by_target[(("section", "2"), ("subsection", "2"), ("item", "6"))].action is StructuralAction.REPEAL
    assert by_target[(("section", "9"), ("subsection", "1"))].action is StructuralAction.REPLACE
    assert by_target[(("section", "18"), ("subsection", "2"), ("item", "9"))].action is StructuralAction.INSERT
    assert by_target[(("section", "31"), ("subsection", "1"))].payload is not None
    assert any(rule_id in op.provenance_tags for op in ops)
    assert all(op.witness_rule_id == rule_id for op in ops if op.action is not StructuralAction.META)


def test_extract_ee_ops_records_chapter_scope_for_global_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "1) seaduse 1.–6. peatükis asendatakse sõnad "
            "„täienduskoolitusasutuse pidaja” sõnaga „täienduskoolitusasutus” "
            "vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].text_patch is not None
    assert ops[0].text_patch.selector.match_text == "täienduskoolitusasutuse pidaja"
    assert ops[0].text_patch.replacement == "täienduskoolitusasutus"
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["scope_chapters"] == ("1", "2", "3", "4", "5", "6")
    assert isinstance(ops[0].payload.attrs.get("rewrite_witness"), object)
    assert type(ops[0].payload.attrs["rewrite_witness"]).__name__ == "EETextRewriteWitness"
    assert ops[0].payload.attrs["rewrite_witness"].rewrite.scope_chapters == ("1", "2", "3", "4", "5", "6")
    assert ops[0].payload.attrs["rewrite_witness"].rewrite.old_surface == "täienduskoolitusasutuse pidaja"


def test_extract_ee_ops_splits_multiple_old_quotes_to_one_new_global_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "10) seaduse 11 1. peatükis asendatakse sõnad "
            "„sõjarelv, laskemoon” ja „sõjarelvad, laskemoon” sõnadega "
            "„sõjarelv, relvasüsteem, sõjarelva laskemoon” vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 2
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert all(op.payload is not None for op in ops)
    assert {op.payload.attrs["old_text"] for op in ops if op.payload is not None} == {
        "sõjarelv, laskemoon",
        "sõjarelvad, laskemoon",
    }
    assert all(
        op.payload is not None
        and op.payload.text == "sõjarelv, relvasüsteem, sõjarelva laskemoon"
        and op.payload.attrs["scope_chapters"] == ("11_1",)
        and op.payload.attrs.get("case_inflected") is True
        for op in ops
    )


def test_extract_ee_ops_splits_title_and_text_global_text_replace_pairs() -> None:
    text = (
        "määruse pealkirjas ja tekstis asendatakse läbivalt sõna "
        "„õnnemäng” sõnaga „hasartmäng” ning sõna "
        "„õnnemängukorraldaja” sõnaga „hasartmängukorraldaja” "
        "vastavas käändes;"
    )

    ops = extract_ee_ops(
        text,
        OperationSource(statute_id="ee/test", raw_text=text),
    )

    assert len(ops) == 2
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert all(op.target.path == () for op in ops)
    assert all(op.payload is not None for op in ops)
    assert {
        op.payload.attrs["old_text"]
        for op in ops
        if op.payload is not None
    } == {
        "õnnemäng",
        "õnnemängukorraldaja",
    }
    assert {
        op.payload.text
        for op in ops
        if op.payload is not None
    } == {
        "hasartmäng",
        "hasartmängukorraldaja",
    }
    assert all(
        op.payload is not None
        and op.payload.attrs.get("all_occurrences") is True
        and op.payload.attrs.get("case_inflected") is True
        and op.payload.attrs.get("compose_future_payloads") is False
        and op.payload.attrs.get("rewrite_scope_surface") == "title_and_text"
        and "ee_global_title_text_rewrite_no_payload_composition" in op.provenance_tags
        for op in ops
    )


def test_extract_ee_ops_emits_global_text_replace_with_heading_exclusion() -> None:
    text = (
        "määruse tekstis, välja arvatud § 4 pealkirjas, asendatakse "
        "läbivalt sõna „karusloom“ sõnaga „tšintšilja“ vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.TEXT_REPLACE
    assert op.target.path == ()
    assert op.payload is not None
    assert op.payload.attrs["old_text"] == "karusloom"
    assert op.payload.attrs["exclude_heading_paths"] == ((("section", "4"),),)


def test_parse_ee_amendment_ops_does_not_promote_heading_exclusion_to_body_path_exclusions() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <sisuTekst><HTMLKonteiner><![CDATA[
          <p><b>&sect; 1.</b> Testm&auml;&auml;rust muudetakse j&auml;rgmiselt:</p>
          <p><b>1)</b> m&auml;&auml;ruse tekstis, v&auml;lja arvatud &sect; 4 pealkirjas, asendatakse l&auml;bivalt s&otilde;na &bdquo;karusloom&ldquo; s&otilde;naga &bdquo;t&scaron;int&scaron;ilja&ldquo; vastavas k&auml;&auml;ndes;</p>
          <p><b>2)</b> paragrahvi 4 l&otilde;ikes 1 asendatakse tekstiosa &bdquo;karusloomi (edaspidi karusloomakasvandus)&ldquo; tekstiosaga &bdquo;t&scaron;int&scaron;iljasid (edaspidi t&scaron;int&scaron;iljakasvandus)&ldquo;.</p>
        ]]></HTMLKonteiner></sisuTekst>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Testmäärus")

    global_op = next(op for op in ops if op.target.path == () and _payload(op).attrs.get("old_text") == "karusloom")
    assert "exclude_paths" not in _payload(global_op).attrs
    assert _payload(global_op).attrs["exclude_heading_paths"] == ((("section", "4"),),)


def test_extract_ee_ops_recovers_mixed_repeal_singular_subsection_between_groups() -> None:
    text = (
        "paragrahvi 1 lõige 2, § 3 lõige 5, § 8 lõiked 6 ja 7 "
        "ning §-d 9, 10 ja 12 tunnistatakse kehtetuks;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert {(op.action, op.target.path) for op in ops} == {
        (StructuralAction.REPEAL, (("section", "1"), ("subsection", "2"))),
        (StructuralAction.REPEAL, (("section", "8"), ("subsection", "6"))),
        (StructuralAction.REPEAL, (("section", "8"), ("subsection", "7"))),
        (StructuralAction.REPEAL, (("section", "3"), ("subsection", "5"))),
        (StructuralAction.REPEAL, (("section", "9"),)),
        (StructuralAction.REPEAL, (("section", "10"),)),
        (StructuralAction.REPEAL, (("section", "12"),)),
    }


def test_extract_ee_ops_fans_out_mixed_repeal_item_targets() -> None:
    text = (
        "paragrahvi 3 punktid 7 ja 8, § 5 lõike 4 punkt 1, "
        "§ 6 lõike 1 punktid 2–4, § 7 punkt 6 ning § 13 "
        "tunnistatakse kehtetuks;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "3"), ("item", "7"))),
        (StructuralAction.REPEAL, (("section", "3"), ("item", "8"))),
        (StructuralAction.REPEAL, (("section", "5"), ("subsection", "4"), ("item", "1"))),
        (StructuralAction.REPEAL, (("section", "6"), ("subsection", "1"), ("item", "2"))),
        (StructuralAction.REPEAL, (("section", "6"), ("subsection", "1"), ("item", "3"))),
        (StructuralAction.REPEAL, (("section", "6"), ("subsection", "1"), ("item", "4"))),
        (StructuralAction.REPEAL, (("section", "7"), ("item", "6"))),
        (StructuralAction.REPEAL, (("section", "13"),)),
    ]


def test_extract_ee_ops_lowers_chapter_heading_insert_after_section() -> None:
    text = (
        "määrust täiendatakse pärast § 14 peatüki pealkirjaga "
        "järgmises sõnastuses: „4. peatükk RAKENDUSSÄTE”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.INSERT
    assert op.target.path == (("chapter", "4"),)
    assert op.payload is not None
    assert op.payload.text == "4. peatükk RAKENDUSSÄTE"
    assert op.payload.attrs["insert_after_section"] == "14"
    assert op.witness_rule_id == "ee_chapter_heading_insert_after_section"


def test_extract_ee_ops_marks_combined_section_heading_text_replace_as_heading_special() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 89 16 pealkirjas ja lõikes 1 asendatakse tekstiosa "
            "„nende oluliste osade,” tekstiosaga "
            "„relvasüsteemide, nende oluliste osade, sõjarelva”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 2
    assert any(
        op.target.path == (("section", "89_16"), ("subsection", "1")) and op.target.special is None for op in ops
    )
    assert any(op.target.path == (("section", "89_16"),) and op.target.special == FacetKind.HEADING for op in ops)


def test_extract_ee_ops_targets_chapter_heading_declension() -> None:
    ops = extract_ee_ops(
        ("§ 11. Seaduse 8. peatüki pealkirjast jäetakse välja sõna «peatamine,»."),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("chapter", "8"),)
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "peatamine,"
    assert ops[0].payload.text == ""


def test_extract_ee_ops_keeps_chapter_heading_replace_out_of_body_scope() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 11 1 . peatüki pealkirjas asendatakse sõnad "
            "„SELLE LASKEMOONA” sõnadega "
            "„RELVASÜSTEEMI, SÕJARELVA LASKEMOONA”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("chapter", "11_1"),)
    assert ops[0].target.special == FacetKind.HEADING
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "SELLE LASKEMOONA"
    assert ops[0].payload.text == "RELVASÜSTEEMI, SÕJARELVA LASKEMOONA"
    assert "scope_chapters" not in ops[0].payload.attrs


def test_extract_ee_ops_repeals_plural_items_from_paragraph_sign_clause() -> None:
    ops = extract_ee_ops(
        "Rahuaja riigikaitse seaduse § 5 lõike 2 punktid 15 ja 16 tunnistatakse kehtetuks.",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "5"), ("subsection", "2"), ("item", "15"))),
        (StructuralAction.REPEAL, (("section", "5"), ("subsection", "2"), ("item", "16"))),
    ]


def test_extract_ee_ops_reclassifies_chapter_text_as_division() -> None:
    ops = extract_ee_ops(
        (
            "9) seaduse 3. peatüki tekst loetakse 1. jaoks ja see pealkirjastatakse "
            "järgmiselt: „1. jagu Täienduskoolituse läbiviimise nõuded ja teabe "
            "avalikustamine”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("chapter", "3"), ("division", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == "1. jagu Täienduskoolituse läbiviimise nõuded ja teabe avalikustamine"


def test_extract_ee_ops_handles_french_quote_chapter_insert() -> None:
    ops = extract_ee_ops(
        (
            "1) seadust täiendatakse 4 1. peatükiga järgmises sõnastuses: "
            "«4 1. peatükk JUHI TÖÖ- JA PUHKEAEG § 20 3. Erinõuded juhi töö- ja puhkeajale»"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("chapter", "4_1"),)
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("4 1. peatükk JUHI TÖÖ- JA PUHKEAEG")


def test_extract_ee_ops_handles_postposed_chapter_number_insert() -> None:
    ops = extract_ee_ops(
        (
            "3) määrust täiendatakse peatükiga 4 1 järgmises sõnastuses: "
            "„4 1 . peatükk Metsuri eriala õppekava üldosa § 15 1 . Metsuri eriala "
            "kutsekeskharidusõppe eesmärk ja õpiväljundid\x01 (1) Õpetusega taotletakse.”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("chapter", "4_1"),)
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("4 1 . peatükk Metsuri eriala õppekava üldosa")


def test_extract_ee_ops_keeps_appendix_addition_out_of_body_replay() -> None:
    ops = extract_ee_ops(
        (
            "4) määrust täiendatakse lisaga 5 "
            "„Metsuri eriala põhiõpingute moodulite kirjeldused”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.META
    assert ops[0].target.path == ()
    assert ops[0].payload is None
    assert ops[0].witness_rule_id == "ee_appendix_addition_not_body_replay"


def test_parse_section_payload_strips_bare_leading_section_number() -> None:
    node = _parse_section_payload(
        ("28 1 . Mootorsõidukijuhi ja juhtimisõiguse taotleja tervisekontroll (1) Esimene lõige.")
    )

    assert node.text == "Mootorsõidukijuhi ja juhtimisõiguse taotleja tervisekontroll"
    assert len(node.children) == 1
    assert node.children[0].label == "1"
    assert node.children[0].text == "Esimene lõige."


def test_extract_ee_ops_handles_plural_subsection_replace_with_french_quotes() -> None:
    ops = extract_ee_ops(
        (
            "1) paragrahvi 36 lõiked 1 ja 4 muudetakse ning sõnastatakse järgmiselt: "
            "«(1) Esimene lõige.»; «(4) Neljas lõige.»"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 2
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "36"), ("subsection", "1"))
    assert ops[1].target.path == (("section", "36"), ("subsection", "4"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == "(1) Esimene lõige."
    assert ops[1].payload is not None
    assert ops[1].payload.text == "(4) Neljas lõige."


def test_extract_ee_ops_handles_mixed_heading_and_plural_subsection_replace() -> None:
    ops = extract_ee_ops(
        (
            "Paragrahvi 15 pealkiri ning lõiked 1 ja 2 muudetakse ning sõnastatakse "
            "järgmiselt: „§ 15. Sõiduki ja autorongi suurimad lubatud mõõtmed, massid "
            "ja teljekoormused\x01 (1) Sõiduki ja autorongi suurimad lubatud mõõtmed "
            "koormaga ja koormata, sõiduki ja autorongi massid ning teljekoormuse "
            "kehtestab majandus- ja kommunikatsiooniminister. (2) Autorongi "
            "koosseisus oleva haagise registrimass ei või ületada vedukiga vedada "
            "lubatud haagise suurimat registrimassi.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "15"), ("subsection", "1"))),
        (StructuralAction.REPLACE, (("section", "15"), ("subsection", "2"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("§ 15. Sõiduki ja autorongi suurimad lubatud mõõtmed")
    assert ops[1].payload is not None
    assert ops[1].payload.text.startswith("(2) Autorongi koosseisus oleva haagise registrimass")


def test_extract_ee_ops_splits_plural_subsection_replace_ranges_by_label() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 119 7 lõiked 3–5 muudetakse ning sõnastatakse järgmiselt: "
            "«(3) Kolmas lõige. (4) Neljas lõige. (5) Viies lõige.»"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "119_7"), ("subsection", "3"))),
        (StructuralAction.REPLACE, (("section", "119_7"), ("subsection", "4"))),
        (StructuralAction.REPLACE, (("section", "119_7"), ("subsection", "5"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == "(3) Kolmas lõige."
    assert ops[1].payload is not None
    assert ops[1].payload.text == "(4) Neljas lõige."
    assert ops[2].payload is not None
    assert ops[2].payload.text == "(5) Viies lõige."


def test_extract_ee_ops_splits_plural_item_insert_payloads_by_label() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 235 täiendatakse punktidega 7 9 ja 7 10 järgmises sõnastuses: "
            "„7 9) nõuda kellelt tahes positsiooni või riskipositsiooni suuruse vähendamist; "
            "7 10) piirata kelle tahes õigust kaubatuletisinstrumentidesse investeerida;”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "235"), ("item", "7_9"))),
        (StructuralAction.INSERT, (("section", "235"), ("item", "7_10"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == "7 9) nõuda kellelt tahes positsiooni või riskipositsiooni suuruse vähendamist;"
    assert ops[1].payload is not None
    assert ops[1].payload.text == "7 10) piirata kelle tahes õigust kaubatuletisinstrumentidesse investeerida;"


def test_extract_ee_ops_keeps_leading_item_repeal_in_compound_plural_subsection_clause() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 47 lõike 1 1 punkt 3, § 85 7 ning § 87 3 lõiked 11 ja 12 tunnistatakse kehtetuks;"),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "47"), ("subsection", "1_1"), ("item", "3"))),
        (StructuralAction.REPEAL, (("section", "87_3"), ("subsection", "11"))),
        (StructuralAction.REPEAL, (("section", "87_3"), ("subsection", "12"))),
        (StructuralAction.REPEAL, (("section", "85_7"),)),
    ]


def test_extract_ee_ops_handles_plural_section_repeal_with_spaced_commas() -> None:
    ops = extract_ee_ops(
        "§ 10. Paragrahvid 74 28 , 74 34 ja 74 41 tunnistatakse kehtetuks.",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "74_28"),)),
        (StructuralAction.REPEAL, (("section", "74_34"),)),
        (StructuralAction.REPEAL, (("section", "74_41"),)),
    ]


def test_extract_ee_ops_handles_division_level_repeal() -> None:
    ops = extract_ee_ops(
        "seaduse 11. peatüki 3. jagu tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("chapter", "11"), ("division", "3"))),
    ]


def test_extract_ee_ops_handles_subdivision_level_repeal() -> None:
    ops = extract_ee_ops(
        "seaduse 12. peatüki 3. jao 3. jaotis tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("chapter", "12"), ("division", "3"), ("subdivision", "3"))),
    ]


def test_extract_ee_ops_handles_mixed_division_section_and_subsection_repeal_clause() -> None:
    ops = extract_ee_ops(
        ("Riigikaitseseaduse 5. peatüki 2. jagu, §-d 90 ja 92 ning § 96 lõige 2 tunnistatakse kehtetuks."),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert sorted((op.action, op.target.path) for op in ops) == sorted(
        [
            (StructuralAction.REPEAL, (("chapter", "5"), ("division", "2"))),
            (StructuralAction.REPEAL, (("section", "90"),)),
            (StructuralAction.REPEAL, (("section", "92"),)),
            (StructuralAction.REPEAL, (("section", "96"), ("subsection", "2"))),
        ]
    )


def test_extract_ee_ops_handles_section_and_chapter_compound_repeal() -> None:
    ops = extract_ee_ops(
        "paragrahv 17 ja seaduse 6. peatükk tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "17"),)),
        (StructuralAction.REPEAL, (("chapter", "6"),)),
    ]


def test_extract_ee_ops_preserves_division_context_for_multi_section_insert() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 3. peatüki 1. jagu täiendatakse §-dega 47 1–47 3 "
            "järgmises sõnastuses: „§ 47 1. Üldised põhimõtted "
            "(1) Esimene. § 47 2. Riskijuhtimissüsteem (1) Teine. "
            "§ 47 3. Siseaudit (1) Kolmas.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("chapter", "3"), ("division", "1"), ("section", "47_1"))),
        (StructuralAction.INSERT, (("chapter", "3"), ("division", "1"), ("section", "47_2"))),
        (StructuralAction.INSERT, (("chapter", "3"), ("division", "1"), ("section", "47_3"))),
    ]


def test_extract_ee_ops_splits_alljaotis_multi_section_insert_payloads() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 2. peatüki 3. jao 3. jaotise 1. alljaotist täiendatakse "
            "§-dega 34 1 ja 34 2 järgmises sõnastuses: "
            "„§ 34 1 . Riigisaladuse töötlemise lubatavus (1) Esimene. "
            "§ 34 2 . Riigisaladuse elektroonilise töötlemise lubatavus (1) Teine.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("chapter", "2"), ("division", "3"), ("section", "34_1"))),
        (StructuralAction.INSERT, (("chapter", "2"), ("division", "3"), ("section", "34_2"))),
    ]
    assert ops[0].payload is not None
    assert ops[1].payload is not None
    assert "§ 34 1" in ops[0].payload.text
    assert "§ 34 2" not in ops[0].payload.text
    assert "§ 34 2" in ops[1].payload.text


def test_extract_ee_ops_targets_division_heading() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 3. peatüki 1. jao pealkiri muudetakse ja sõnastatakse "
            "järgmiselt: „1. jagu Kindlustusandja juhtimissüsteem”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("chapter", "3"), ("division", "1"))
    assert ops[0].target.special == FacetKind.HEADING
    assert ops[0].payload is not None
    assert ops[0].payload.text == "1. jagu Kindlustusandja juhtimissüsteem"


def test_extract_ee_ops_handles_division_level_replace() -> None:
    ops = extract_ee_ops(
        (
            "seaduse 2. peatüki 1. jagu muudetakse ja sõnastatakse järgmiselt: "
            "„1. jagu Mõisted § 3. Taim, taimne saadus ja muu objekt "
            "(1) Taim käesoleva seaduse tähenduses on taim. "
            "§ 3 1. Kaubasaadetis, turustamine ja lõppkasutaja "
            "(1) Kaubasaadetis käesoleva seaduse tähenduses on kogum. "
            "§ 4. Ohtlik taimekahjustaja Ohtlik taimekahjustaja käesoleva "
            "seaduse tähenduses on taimekahjustaja.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("chapter", "2"), ("division", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("1. jagu Mõisted § 3.")


def test_extract_ee_ops_handles_chapter_level_replace() -> None:
    ops = extract_ee_ops(
        (
            "Ühistranspordiseaduse 10. peatükk muudetakse ja sõnastatakse järgmiselt: "
            "„10. peatükk RIIKLIK JÄRELEVALVE JA ERISÄTTED "
            "§ 53 5. Riiklik järelevalve "
            "(1) Järelevalve käib siin. "
            "§ 53 6. Riikliku järelevalve erimeetmed "
            "(1) Erimeede käib siin.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("chapter", "10"),)
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("10. peatükk RIIKLIK JÄRELEVALVE JA ERISÄTTED")


def test_extract_ee_ops_handles_mixed_section_and_subsection_repeal_clause() -> None:
    ops = extract_ee_ops(
        ("paragrahvid 39 ja 40, § 41 lõiked 1–2 ja lõige 8, §-d 41 1, 43 ja 44 tunnistatakse kehtetuks;"),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "39"),)),
        (StructuralAction.REPEAL, (("section", "40"),)),
        (StructuralAction.REPEAL, (("section", "41"), ("subsection", "1"))),
        (StructuralAction.REPEAL, (("section", "41"), ("subsection", "2"))),
        (StructuralAction.REPEAL, (("section", "41"), ("subsection", "8"))),
        (StructuralAction.REPEAL, (("section", "41_1"),)),
        (StructuralAction.REPEAL, (("section", "43"),)),
        (StructuralAction.REPEAL, (("section", "44"),)),
    ]
    subsection_ops = [
        op
        for op in ops
        if op.target.path[:1] == (("section", "41"),)
        and len(op.target.path) == 2
        and op.target.path[1][0] == "subsection"
    ]
    assert len(subsection_ops) == 3
    for op in subsection_ops:
        selection_meta = read_subsection_selection_meta(_payload(op))
        assert selection_meta is not None
        assert selection_meta.explicit_labels == ("1", "2", "8")
        assert selection_meta.plain_numeric_ranges == (("1", "2"),)
        assert selection_meta.label_ranges == (("1", "2"),)


def test_extract_ee_ops_keeps_leading_plain_section_repeal_in_mixed_clause() -> None:
    ops = extract_ee_ops(
        (
            "Elektroonilise side seaduse § 87 2, § 100 3 lõige 3, "
            "§ 100 4 lõige 2, § 100 5 lõige 2, § 133 lõige 5, "
            "§ 170 1 ja § 188 lõige 8 tunnistatakse kehtetuks."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "87_2"),)),
        (StructuralAction.REPEAL, (("section", "170_1"),)),
        (StructuralAction.REPEAL, (("section", "100_3"), ("subsection", "3"))),
        (StructuralAction.REPEAL, (("section", "100_4"), ("subsection", "2"))),
        (StructuralAction.REPEAL, (("section", "100_5"), ("subsection", "2"))),
        (StructuralAction.REPEAL, (("section", "133"), ("subsection", "5"))),
        (StructuralAction.REPEAL, (("section", "188"), ("subsection", "8"))),
    ]


def test_extract_ee_ops_handles_plural_subsection_repeal_with_rt_spaced_commas() -> None:
    ops = extract_ee_ops(
        "24) paragrahvi 9 lõiked 7 1 , 7 2 , 10 ja 11 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "9"), ("subsection", "7_1"))),
        (StructuralAction.REPEAL, (("section", "9"), ("subsection", "7_2"))),
        (StructuralAction.REPEAL, (("section", "9"), ("subsection", "10"))),
        (StructuralAction.REPEAL, (("section", "9"), ("subsection", "11"))),
    ]


def test_extract_ee_ops_treats_plural_subsection_sentence_repeal_as_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 57 lõigete 2–5, 7–12 ja 14 teine lause tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "2")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "3")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "4")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "5")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "7")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "8")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "9")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "10")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "11")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "12")), ""),
        (StructuralAction.REPLACE, (("section", "57"), ("subsection", "14")), ""),
    ]


def test_extract_ee_ops_treats_singular_subsection_sentence_repeal_as_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 10 lõike 1 teine lause tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "10"), ("subsection", "1")), ""),
    ]
    assert ops[0].provenance_tags[-1] == "teine lause tunnistatakse kehtetuks"


def test_extract_ee_ops_treats_another_singular_subsection_sentence_repeal_as_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 18 lõike 2 teine lause tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "18"), ("subsection", "2")), ""),
    ]
    assert ops[0].provenance_tags[-1] == "teine lause tunnistatakse kehtetuks"


def test_extract_ee_ops_treats_mixed_explicit_subsection_sentence_deletion_as_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 12 lõikest 4 ja § 13 lõikest 3 jäetakse välja teine lause;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "12"), ("subsection", "4")), ""),
        (StructuralAction.REPLACE, (("section", "13"), ("subsection", "3")), ""),
    ]
    assert all(op.provenance_tags[-1] == "teine lause jäetakse välja" for op in ops)


def test_extract_ee_ops_treats_section_sentence_repeal_as_replace() -> None:
    ops = extract_ee_ops(
        "Kommertspandiseaduse § 37 esimene lause tunnistatakse kehtetuks.",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "37"),), ""),
    ]
    assert ops[0].provenance_tags[-1] == "esimene lause tunnistatakse kehtetuks"


def test_extract_ee_ops_handles_aastaarv_text_replace_across_multiple_subsections() -> None:
    ops = extract_ee_ops(
        ("paragrahvi 9 2 lõigetes 1 1 ja 1 2 asendatakse aastaarv ”2019” aastaarvuga ”2024”."),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "9_2"), ("subsection", "1_1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "9_2"), ("subsection", "1_2"))),
    ]
    assert all(_payload(op).text == "2024" for op in ops)
    assert all(_payload(op).attrs.get("old_text") == "2019" for op in ops)
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch.selector.match_text == "2019" for op in ops if op.text_patch)
    assert all(op.text_patch.replacement == "2024" for op in ops if op.text_patch)


def test_extract_ee_ops_handles_mixed_item_sentence_subsection_and_section_repeal_clause() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 21 lõike 1 punktid 5, 6 1 ja lõige 1 1 ning §-d 22, 22 1 ja 24, "
            "§ 27 lõike 1 teine lause, lõike 3 teine lause ja lõige 4 ning §-d 27 1 –29 "
            "tunnistatakse kehtetuks;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    triples = {
        (
            op.action,
            op.target.path,
            op.payload.text if op.payload is not None else None,
        )
        for op in ops
    }

    assert (
        StructuralAction.REPEAL,
        (("section", "21"), ("subsection", "1"), ("item", "5")),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "21"), ("subsection", "1"), ("item", "6_1")),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "21"), ("subsection", "1_1")),
        "",
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "22"),),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "22_1"),),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "24"),),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "21"), ("subsection", "4")),
        "",
    ) not in triples
    subsection_1_1_op = next(
        op for op in ops if op.target.path == (("section", "21"), ("subsection", "1_1"))
    )
    subsection_selection_meta = read_subsection_selection_meta(_payload(subsection_1_1_op))
    assert subsection_selection_meta is not None
    assert subsection_selection_meta.explicit_labels == ("1_1",)
    assert (
        StructuralAction.REPLACE,
        (("section", "27"), ("subsection", "1")),
        "",
    ) in triples
    assert (
        StructuralAction.REPLACE,
        (("section", "27"), ("subsection", "3")),
        "",
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "27"), ("subsection", "4")),
        "",
    ) in triples
    subsection_27_4_op = next(
        op for op in ops if op.target.path == (("section", "27"), ("subsection", "4"))
    )
    subsection_selection_meta = read_subsection_selection_meta(_payload(subsection_27_4_op))
    assert subsection_selection_meta is not None
    assert subsection_selection_meta.explicit_labels == ("4",)
    assert (
        StructuralAction.REPEAL,
        (("section", "27_1"),),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "28"),),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "29"),),
        None,
    ) in triples


def test_extract_ee_ops_keeps_companion_subsection_repeals_for_each_explicit_section_item_segment() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 90 lõike 3 punkt 2 ja lõige 4 ning § 121 lõike 3 punkt 2 ja lõige 4 "
            "tunnistatakse kehtetuks;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    triples = {
        (
            op.action,
            op.target.path,
            op.payload.text if op.payload is not None else None,
        )
        for op in ops
    }

    assert (
        StructuralAction.REPEAL,
        (("section", "90"), ("subsection", "3"), ("item", "2")),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "90"), ("subsection", "4")),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "121"), ("subsection", "3"), ("item", "2")),
        None,
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "121"), ("subsection", "4")),
        "",
    ) in triples

    subsection_121_4_op = next(
        op for op in ops if op.target.path == (("section", "121"), ("subsection", "4"))
    )
    subsection_selection_meta = read_subsection_selection_meta(_payload(subsection_121_4_op))
    assert subsection_selection_meta is not None
    assert subsection_selection_meta.explicit_labels == ("4",)


def test_extract_ee_ops_keeps_same_section_companion_item_repeal() -> None:
    ops = extract_ee_ops(
        "paragrahvi 43 1 lõike 3 punkt 8 ja lõike 6 punkt 8 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    triples = {(op.action, op.target.path) for op in ops}

    assert (
        StructuralAction.REPEAL,
        (("section", "43_1"), ("subsection", "3"), ("item", "8")),
    ) in triples
    assert (
        StructuralAction.REPEAL,
        (("section", "43_1"), ("subsection", "6"), ("item", "8")),
    ) in triples


def test_extract_ee_ops_does_not_invent_companion_subsection_repeal_without_local_tail() -> None:
    ops = extract_ee_ops(
        "paragrahvi 90 lõike 3 punkt 2 ning § 121 lõike 3 punkt 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    paths = {op.target.path for op in ops}

    assert (("section", "90"), ("subsection", "4")) not in paths
    assert (("section", "121"), ("subsection", "4")) not in paths


def test_extract_ee_ops_treats_item_multi_sentence_repeal_as_replace() -> None:
    ops = extract_ee_ops(
        "paragrahvi 1 lõike 3 punkti 1 teine ja kolmas lause tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.payload.text if op.payload else None) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "1"), ("subsection", "3"), ("item", "1")), ""),
    ]
    assert ops[0].provenance_tags[-1] == "teine ja kolmas lause tunnistatakse kehtetuks"


def test_extract_ee_ops_expands_plain_to_superscript_section_repeal_range() -> None:
    ops = extract_ee_ops(
        "paragrahvid 42–42 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "42"),)),
        (StructuralAction.REPEAL, (("section", "42_1"),)),
        (StructuralAction.REPEAL, (("section", "42_2"),)),
    ]


def test_extract_ee_ops_handles_leading_paragraph_sign_section_repeal_with_ning_and_future_effect_tail() -> None:
    ops = extract_ee_ops(
        "§-d 1–25 ning 26 1 tunnistatakse kehtetuks alates 2012. aasta 1. jaanuarist.",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    targets = [(op.action, op.target.path) for op in ops]

    assert len(targets) == 26
    assert targets[0] == (StructuralAction.REPEAL, (("section", "1"),))
    assert targets[24] == (StructuralAction.REPEAL, (("section", "25"),))
    assert targets[25] == (StructuralAction.REPEAL, (("section", "26_1"),))
    assert all(op.source is not None and op.source.effective == "2012-01-01" for op in ops)
    selection_meta = read_section_selection_meta(_payload(ops[0]))
    assert selection_meta is not None
    assert selection_meta.explicit_labels[:3] == ("1", "2", "3")
    assert selection_meta.explicit_labels[-2:] == ("25", "26_1")
    assert selection_meta.plain_numeric_ranges == (("1", "25"),)


def test_extract_ee_ops_does_not_treat_quoted_payload_alates_phrase_as_clause_local_effective_date() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 2 lõike 6 punkt 1 muudetakse ja sõnastatakse järgmiselt: "
            "„1) seaduse § 72 lõike 6 alusel kehtestatav ööpäevaringse "
            "erihooldusteenuse maksimaalne maksumus kohtumäärusega "
            "hoolekandeasutusse paigutatud isiku kohta 1966 eurot kalendrikuus, "
            "alates 2021. aasta 1. aprillist 2067 eurot kalendrikuus;”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].source is not None
    assert ops[0].source.effective == ""


def test_extract_ee_ops_does_not_mark_superscript_only_section_range_as_plain_numeric_range() -> None:
    ops = extract_ee_ops(
        "paragrahvid 42–42 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    selection_meta = read_section_selection_meta(_payload(ops[0]))

    assert selection_meta is not None
    assert selection_meta.explicit_labels == ("42", "42_1", "42_2")
    assert selection_meta.plain_numeric_ranges == ()


def test_extract_ee_ops_tags_trailing_old_format_subsection_range_after_item_repeals() -> None:
    ops = extract_ee_ops(
        "paragrahvi 14 lõike 1 punktid 3 1, 4, 5 1–8 ja 11–18 ning lõiked 2–4 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    subsection_ops = [
        op
        for op in ops
        if op.target.path[:1] == (("section", "14"),)
        and len(op.target.path) == 2
        and op.target.path[1][0] == "subsection"
    ]

    assert [op.target.path[-1][1] for op in subsection_ops] == ["2", "3", "4"]
    assert all(op.payload is not None for op in subsection_ops)
    for op in subsection_ops:
        selection_meta = read_subsection_selection_meta(_payload(op))
        assert selection_meta is not None
        assert selection_meta.explicit_labels == ("2", "3", "4")


def test_extract_ee_ops_keeps_singular_trailing_old_format_subsection_repeal_narrow() -> None:
    ops = extract_ee_ops(
        "paragrahvi 21 lõike 1 punktid 5, 6 1 ja lõige 1 1 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    subsection_ops = [
        op
        for op in ops
        if op.target.path[:1] == (("section", "21"),)
        and len(op.target.path) == 2
        and op.target.path[1][0] == "subsection"
    ]

    assert [op.target.path[-1][1] for op in subsection_ops] == ["1_1"]
    selection_meta = read_subsection_selection_meta(_payload(subsection_ops[0]))
    assert selection_meta is not None
    assert selection_meta.explicit_labels == ("1_1",)


def test_parse_html_op_items_preserves_section_boundary_for_entity_encoded_sect_marker() -> None:
    items = parse_html_op_items(
        (
            "<p><b>3)</b> seaduse 8. peatüki 7. jagu täiendatakse 8. jaotisega "
            "järgmises sõnastuses:</p>"
            '<p align="center">&rdquo;<b>8. jaotis<br/>Meediateenuste seaduse alusel tehtavad toimingud</b></p>'
            "<p><b>&sect; 202<sup>1</sup>. Televisiooni- ja raadioteenuse osutamise tegevusloa taotluse läbivaatamine</b></p>"
            "<p>Televisiooni- või raadioteenuse osutamise tegevusloa taotluse läbivaatamise eest tasutakse riigilõivu 255,64 eurot.&rdquo;.</p>"
        )
    )

    assert len(items) == 1
    assert "\x01" in items[0]
    assert "§ 202 1 . Televisiooni- ja raadioteenuse osutamise tegevusloa taotluse läbivaatamine\x01" in items[0]


def test_extract_ee_ops_keeps_nested_estonian_inner_quotes_inside_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 181 3 lõige 1 muudetakse ja sõnastatakse järgmiselt: "
            "„(1) Laevale meresõiduohutust või keskkonnaohutust tõendava tunnistuse, "
            "sõidukõlblikkuse tunnistuse, mõõtekirja, laadungimärgi tunnistuse, "
            "meretöötunnistuse, meretöönõuetele vastavuse deklaratsiooni, "
            "ajutise meretöötunnistuse, kalandustöötunnistuse, tunnistuse "
            "„Laevaandmete alaline register” või ühekordse ülesõiduloa väljastamise eest "
            "tasutakse riigilõivu 6 eurot iga lehekülje eest.”."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].payload is not None
    assert "Laevaandmete alaline register” või ühekordse ülesõiduloa" in ops[0].payload.text


def test_extract_ee_ops_does_not_case_inflect_symbolic_section_reference_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 18 lõike 1 punktis 13 ja § 23 lõike 1 punktis 5 "
            "asendatakse tekstiosa „§ 84” tekstiosaga „§ 47 7” vastavas käändes;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "18"), ("subsection", "1"), ("item", "13"))),
        (StructuralAction.TEXT_REPLACE, (("section", "23"), ("subsection", "1"), ("item", "5"))),
    ]
    assert all(_payload(op).attrs["old_text"] == "§ 84" for op in ops)
    assert all("case_inflected" not in _payload(op).attrs for op in ops)


def test_parse_ee_amendment_ops_materializes_generic_minister_rename_as_global_text_replace() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi><nimi><pealkiri>Vabariigi Valitsuse seaduse muutmine</pealkiri></nimi></aktinimi>
      <globaalID>129062014109</globaalID>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              § 107 3. Ministrite ametinimetuste asendamine.
              Kehtivates seadustes loetakse sõnad „rahandusminister” asendatuks
              sõnadega „valdkonna eest vastutav minister” vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/129062014109", target_title="Kindlustustegevuse seadus")

    assert ops
    plural_ops = [op for op in ops if op.payload is not None and op.payload.attrs.get("generic_minister_plural")]
    assert len(plural_ops) == 1
    assert plural_ops[0].payload is not None
    assert plural_ops[0].payload.text == "valdkondade eest vastutavad ministrid"
    rahandusminister_ops = [
        op for op in ops if op.payload is not None and op.payload.attrs.get("old_text") == "rahandusminister"
    ]
    assert len(rahandusminister_ops) == 1
    assert rahandusminister_ops[0].action is StructuralAction.TEXT_REPLACE
    assert rahandusminister_ops[0].target.path == ()
    assert _payload(rahandusminister_ops[0]).text == "valdkonna eest vastutav minister"
    assert _payload(rahandusminister_ops[0]).attrs.get("case_inflected") is True


def test_parse_ee_amendment_ops_materializes_generic_ministry_reorganization_as_global_text_replace() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi><nimi><pealkiri>Vabariigi Valitsuse seaduse muutmine</pealkiri></nimi></aktinimi>
      <globaalID>130062023001</globaalID>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              § 105 19. Ministeeriumide ja nende valitsemisalade ümberkorraldamine.
              Maaeluministeerium korraldatakse ümber Regionaal- ja Põllumajandusministeeriumiks
              alates 2023. aasta 1. juulist.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/130062023001", target_title="Taimekaitseseadus")

    assert ops
    maaelu_ops = [
        op for op in ops if op.payload is not None and op.payload.attrs.get("old_text") == "Maaeluministeerium"
    ]
    assert len(maaelu_ops) == 1
    assert maaelu_ops[0].action is StructuralAction.TEXT_REPLACE
    assert maaelu_ops[0].target.path == ()
    assert _payload(maaelu_ops[0]).text == "Regionaal- ja Põllumajandusministeerium"
    assert _payload(maaelu_ops[0]).attrs.get("case_inflected") is True
    assert _payload(maaelu_ops[0]).attrs.get("source_family") == "generic_ministry_reorganization"


def test_parse_ee_amendment_ops_materializes_generic_ministry_reorganization_exceptions() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi><nimi><pealkiri>Vabariigi Valitsuse seaduse muutmine</pealkiri></nimi></aktinimi>
      <globaalID>130062023001</globaalID>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              § 105 19. Ministeeriumide ja nende valitsemisalade ümberkorraldamine.
              Maaeluministeerium korraldatakse ümber Regionaal- ja
              Põllumajandusministeeriumiks alates 2023. aasta 1. juulist.
              Kehtivates ja tulevikus jõustuvates seadustes, välja arvatud
              kalapüügiseaduse § 90 2 lõikes 2 ja 2023. aasta riigieelarve seaduses,
              loetakse alates 2023. aasta 1. juulist sõna „Maaeluministeerium”
              asendatuks sõnadega „Regionaal- ja Põllumajandusministeerium”
              vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/130062023001", target_title="Kalapüügiseadus")
    maaelu_ops = [
        op for op in ops if op.payload is not None and op.payload.attrs.get("old_text") == "Maaeluministeerium"
    ]

    assert len(maaelu_ops) == 1
    payload = _payload(maaelu_ops[0])
    assert payload.text == "Regionaal- ja Põllumajandusministeerium"
    assert payload.attrs["exclude_paths"] == ((("section", "90_2"), ("subsection", "2")),)
    assert payload.attrs["exclusion_rule"] == "ee_generic_ministry_reorganization_explicit_exceptions"


def test_parse_ee_amendment_ops_ignores_other_statute_generic_ministry_exceptions() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi><nimi><pealkiri>Vabariigi Valitsuse seaduse muutmine</pealkiri></nimi></aktinimi>
      <globaalID>130062023001</globaalID>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              § 105 19. Ministeeriumide ja nende valitsemisalade ümberkorraldamine.
              Maaeluministeerium korraldatakse ümber Regionaal- ja
              Põllumajandusministeeriumiks alates 2023. aasta 1. juulist.
              Kehtivates ja tulevikus jõustuvates seadustes, välja arvatud
              kalapüügiseaduse § 90 2 lõikes 2 ja 2023. aasta riigieelarve seaduses,
              loetakse alates 2023. aasta 1. juulist sõna „Maaeluministeerium”
              asendatuks sõnadega „Regionaal- ja Põllumajandusministeerium”
              vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/130062023001", target_title="Taimekaitseseadus")
    maaelu_ops = [
        op for op in ops if op.payload is not None and op.payload.attrs.get("old_text") == "Maaeluministeerium"
    ]

    assert len(maaelu_ops) == 1
    assert "exclude_paths" not in _payload(maaelu_ops[0]).attrs


def test_parse_ee_amendment_ops_materializes_pollumajandusministeerium_name_substitution() -> None:
    xml = """
    <tyviseadus xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <aktinimi><nimi><pealkiri>Vabariigi Valitsuse seaduse muutmine</pealkiri></nimi></aktinimi>
      <globaalID>130062015004</globaalID>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst>
              § 107 4. Põllumajandusministeeriumi nime asendamine.
              Kehtivates seadustes loetakse sõna „Põllumajandusministeerium”
              asendatuks sõnaga „Maaeluministeerium” vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/130062015004", target_title="Rahuaja riigikaitse seadus")

    maaelu_ops = [
        op for op in ops if op.payload is not None and op.payload.attrs.get("old_text") == "Põllumajandusministeerium"
    ]
    assert len(maaelu_ops) == 1
    assert maaelu_ops[0].payload is not None
    assert maaelu_ops[0].payload.text == "Maaeluministeerium"
    assert maaelu_ops[0].payload.attrs.get("case_inflected") is True


def test_parse_ee_amendment_ops_keeps_dedicated_target_ops_alongside_generic_reorg_ops() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Vabariigi Valitsuse seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>
              Ministeeriumide ja nende valitsemisalade ümberkorraldamine.
              Maaeluministeerium korraldatakse ümber Regionaal- ja
              Põllumajandusministeeriumiks alates 2023. aasta 1. juulist.
              Kehtivates ja tulevikus jõustuvates õigusaktides loetakse sõna
              „Maaeluministeerium” asendatuks sõnadega
              „Regionaal- ja Põllumajandusministeerium” vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Eesti territooriumi haldusjaotuse seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>
              Eesti territooriumi haldusjaotuse seaduse tekstis asendatakse sõna
              „Rahandusministeerium” sõnadega
              „Regionaal- ja Põllumajandusministeerium” vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/130062023001",
        target_title="Eesti territooriumi haldusjaotuse seadus",
    )

    old_texts = [op.payload.attrs.get("old_text") for op in ops if op.payload is not None]
    assert "Põllumajandusministeerium" in old_texts
    assert "Maaeluministeerium" in old_texts
    assert "Rahandusministeerium" in old_texts
    assert len(old_texts) == 3


def test_parse_ee_amendment_ops_excludes_specific_phrase_targets_from_global_text_replace() -> None:
    try:
        archive = open_rt_archive(readonly=True)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"EE archive unavailable in this environment: {exc}")
    xml = archive.get("https://www.riigiteataja.ee/akt/108122020001.xml")
    assert xml is not None

    ops = parse_ee_amendment_ops(xml, "ee/108122020001", target_title="Mahepõllumajanduse seadus")

    global_ops = [
        op
        for op in ops
        if op.action is StructuralAction.TEXT_REPLACE
        and op.target.path == ()
        and op.payload is not None
        and op.payload.attrs.get("old_text") in {"Põllumajandusamet", "Veterinaar- ja Toiduamet"}
    ]
    assert len(global_ops) == 2
    expected_paths = {
        (("section", "10"),),
        (("section", "14"),),
        (("section", "14"), ("subsection", "2")),
        (("section", "5"), ("subsection", "3")),
        (("section", "7"), ("subsection", "5")),
        (("section", "9"), ("subsection", "1")),
        (("section", "19_1"), ("subsection", "2")),
        (("section", "19_1"), ("subsection", "5")),
        (("section", "19_1"), ("subsection", "6")),
        (("section", "19_2"), ("subsection", "3")),
    }
    for op in global_ops:
        excluded = {tuple(path) for path in _payload(op).attrs.get("exclude_paths", [])}
        assert excluded == expected_paths
        assert op.text_patch is not None


def test_parse_ee_amendment_ops_extracts_compound_statute_title_text_replace() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Vabariigi Valitsuse seaduse muutmise ja sellega seonduvalt teiste seaduste muutmise seadus</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>
              Ministeeriumide ja nende valitsemisalade ümberkorraldamine.
              Keskkonnaministeerium korraldatakse ümber Kliimaministeeriumiks.
              Kehtivates ja tulevikus jõustuvates õigusaktides loetakse sõna
              „Keskkonnaministeerium” asendatuks sõnaga „Kliimaministeerium”
              vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>12</paragrahvNr>
          <paragrahvPealkiri>Autoveoseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>
              Autoveoseaduse tekstis asendatakse tekstiosa
              „Majandus- ja Kommunikatsiooniministeerium” tekstiosaga
              „Kliimaministeerium” vastavas käändes.
            </tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/130062023001",
        target_title="Autoveoseadus",
    )

    targeted_ops = [
        op
        for op in ops
        if op.payload is not None and op.payload.attrs.get("old_text") == "Majandus- ja Kommunikatsiooniministeerium"
    ]
    assert len(targeted_ops) == 1
    assert targeted_ops[0].action is StructuralAction.TEXT_REPLACE
    assert targeted_ops[0].target.path == ()
    assert targeted_ops[0].payload is not None
    assert targeted_ops[0].payload.text == "Kliimaministeerium"
    assert targeted_ops[0].payload.attrs.get("case_inflected") is True
    assert not any(
        op.payload is None and "Majandus- ja Kommunikatsiooniministeerium" in " ".join(op.provenance_tags) for op in ops
    )


def test_extract_ee_ops_emits_multiple_targeted_text_replace_pairs() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 46 1 lõikes 5 asendatakse sõnad "
            "«10 000 krooni» sõnadega «640 eurot» ja sõnad "
            "«50 000 krooni» sõnadega «3200 eurot»;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "46_1"), ("subsection", "5"))),
        (StructuralAction.TEXT_REPLACE, (("section", "46_1"), ("subsection", "5"))),
    ]
    assert ops[0].payload is not None


def test_parse_ee_amendment_ops_keeps_superscript_jagu_insert_as_division_target() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Korteriomandi- ja korteriühistuseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>24)</b> seadust täiendatakse 6<sup>1</sup>. peatükiga järgmises sõnastuses:</p>
              <p align="center">„<b>6<sup>1</sup>. peatükk<br/>Vaidluste kohtuväline lahendamine</b></p>
              <p><b>§ 63<sup>1</sup>. Korteriomandi ja korteriühistu vaidluste kohtuväline lahendamine</b></p>
              <p>(1) Foo.</p>
              <p><b>25)</b> seaduse 7. peatükki täiendatakse 1<sup>1</sup>. jaoga järgmises sõnastuses:</p>
              <p align="center">„<b>1<sup>1</sup>. jagu<br/>Seaduse kohaldamine korteriomanike vahel sõlmitud kasutuskorra kokkuleppele</b></p>
              <p><b>§ 64<sup>1</sup>. Korteriomanike vahel sõlmitud kasutuskorra kokkuleppe muutmine eriomandi kokkuleppe osaks</b></p>
              <p>(1) Bar.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Korteriomandi- ja korteriühistuseadus",
    )

    assert any(
        op.action is StructuralAction.INSERT
        and op.target.path == (("chapter", "7"), ("division", "1_1"))
        and op.payload is not None
        and "1 1 . jagu" in op.payload.text
        and "§ 64 1 ." in op.payload.text
        for op in ops
    )
    assert not any(op.target.path == (("section", "62_1 25"),) for op in ops)


def test_parse_ee_statute_preserves_superscript_division_labels() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/akt/1.0">
      <sisu>
        <peatykk>
          <peatykkNr>7</peatykkNr>
          <peatykkPealkiri>Koostoime</peatykkPealkiri>
          <jagu>
            <jaguNr>1</jaguNr>
            <jaguPealkiri>Esimene jagu</jaguPealkiri>
          </jagu>
          <jagu>
            <kuvatavNr><![CDATA[1<sup>1</sup>.]]></kuvatavNr>
            <jaguNr>1</jaguNr>
            <jaguPealkiri>Pooltevaheline kokkulepe</jaguPealkiri>
            <paragrahv>
              <paragrahvNr>64</paragrahvNr>
              <kuvatavNr><![CDATA[§ 64<sup>1</sup>.]]></kuvatavNr>
              <paragrahvPealkiri>Kokkulepe</paragrahvPealkiri>
            </paragrahv>
          </jagu>
        </peatykk>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    statute = parse_ee_statute(xml, "ee/test")
    chapter = statute.body.children[0]

    assert [(child.kind, child.label, child.text) for child in chapter.children] == [
        (IRNodeKind.DIVISION, "1", "Esimene jagu"),
        (IRNodeKind.DIVISION, "1_1", "Pooltevaheline kokkulepe"),
    ]
    assert chapter.children[1].children[0].label == "64_1"


def test_extract_ee_ops_fans_out_targeted_word_replace_after_explicit_target_list() -> None:
    ops = extract_ee_ops(
        (
            "Eesti territooriumi haldusjaotuse seaduses asendatakse § 8 lõike 4 punktis 2 "
            "ja lõikes 5, § 8 1 lõigetes 3, 4, 11 ja 13, § 9 lõigetes 3, 4, 13 ja 14 "
            "ning § 12 lõikes 3 sõna „Siseministeerium” sõnaga "
            "„Rahandusministeerium” vastavas käändes."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 11
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "Siseministeerium" for op in ops)
    assert all(_payload(op).text == "Rahandusministeerium" for op in ops)
    assert all(_payload(op).attrs.get("case_inflected") is True for op in ops)
    assert all(op.text_patch is not None for op in ops)
    assert all(op.text_patch.selector.match_text == "Siseministeerium" for op in ops if op.text_patch)
    assert all(op.text_patch.replacement == "Rahandusministeerium" for op in ops if op.text_patch)


def test_extract_ee_ops_strips_nested_quoted_title_before_targeted_word_replace() -> None:
    ops = extract_ee_ops(
        (
            "Regionaalministri 15. mai 2008. a määruse nr 3 „Meetme "
            "„Linnaliste piirkondade arendamine” tingimused ja investeeringute "
            "kava koostamise kord” § 3 lõikes 1, § 9 lõikes 6, § 10 lõigetes "
            "1 ja 2, § 11 lõigetes 1–6, § 12 lõigetes 1, 2 1 , 3 1 , 4 ja 6, "
            "§ 13 lõigetes 1, 4, 4 1 ja 6, § 13 1 lõigetes 1, 1 2 , 2 ja 4, "
            "§ 13 2 lõigetes 1, 3, 6, 8, 9 ja 11, § 15 lõike 1 punktis 1, "
            "§ 16 lõike 5 punktis 4, § 17 lõikes 3, § 17 1 lõike 1 1 punktis 1 "
            "ning § 23 punktis 2 asendatakse sõna „Siseministeerium” sõnaga "
            "„Rahandusministeerium” vastavas käändes."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    paths = [op.target.path for op in ops]

    assert len(ops) == 34
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert (("section", "3"), ("subsection", "1")) in paths
    assert (("section", "17_1"), ("subsection", "1_1"), ("item", "1")) in paths
    assert (("section", "23"), ("item", "2")) in paths
    assert (("section", "12"), ("subsection", "9")) not in paths
    assert all(op.payload is not None and op.payload.attrs.get("case_inflected") is True for op in ops)


def test_parse_ee_amendment_ops_does_not_leak_kohtute_into_kohtutaituri_target() -> None:
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Kohtute seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 9 täiendatakse lõikega 1 järgmises sõnastuses: "kohtute tekst";</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvPealkiri>Kohtutäituri seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 18 lõikes 1 asendatakse sõna "vana" sõnaga "uus";</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Kohtutäituri seadus",
    )

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "18"), ("subsection", "1"))


def test_extract_ee_ops_expands_plural_subsection_replace_figure_dash_range() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 6 lõiked 1‒3 muudetakse ja sõnastatakse järgmiselt: "
            "„(1) Vald ja linn jagunevad asustusüksusteks. "
            "(2) Asustusüksused on asulad, milleks on linnad, külad, alevikud ja alevid. "
            "(3) Linn haldusüksusena on samades piirides ka asula.”"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert [op.target.path for op in ops] == [
        (("section", "6"), ("subsection", "1")),
        (("section", "6"), ("subsection", "2")),
        (("section", "6"), ("subsection", "3")),
    ]
    assert all(op.action is StructuralAction.REPLACE for op in ops)
    assert ops[1].payload is not None
    assert ops[1].payload.text.startswith("(2) Asustusüksused on asulad")


def test_extract_ee_ops_expands_plural_subsection_insert_figure_dash_ranges() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 14 1 täiendatakse lõigetega 4 1‒4 5 ja 11‒14 "
            "järgmises sõnastuses: „(4 1) esimene. (14) viimane.”;"
        ),
        OperationSource(statute_id="121062016001", raw_text="test"),
    )

    assert [op.target.path for op in ops] == [
        (("section", "14_1"), ("subsection", "4_1")),
        (("section", "14_1"), ("subsection", "4_2")),
        (("section", "14_1"), ("subsection", "4_3")),
        (("section", "14_1"), ("subsection", "4_4")),
        (("section", "14_1"), ("subsection", "4_5")),
        (("section", "14_1"), ("subsection", "11")),
        (("section", "14_1"), ("subsection", "12")),
        (("section", "14_1"), ("subsection", "13")),
        (("section", "14_1"), ("subsection", "14")),
    ]
    assert all(op.action is StructuralAction.INSERT for op in ops)


def test_extract_ee_ops_fans_out_elative_plural_subsection_text_delete_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 7 lõike 1 punktist 3, § 37 pealkirjast, lõike 1 "
            "sissejuhatavast lauseosast ning lõigetest 6 ja 7, § 38 pealkirjast, "
            "lõike 1 sissejuhatavast lauseosast ja punktist 2 ning lõigetest 3 ja 4, "
            "§ 40 pealkirjast ning lõigetest 1, 2 ja 4, § 62 pealkirjast ja lõikest 1 "
            "ning § 72 lõigetest 1 ja 2 jäetakse välja tekstiosa „, vabaladu” "
            "vastavas käändes;"
        ),
        OperationSource(statute_id="116062017001", raw_text="test"),
    )

    assert [(op.action, op.target.path, op.target.special) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "7"), ("subsection", "1"), ("item", "3")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "37"), ("subsection", "1")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "38"), ("subsection", "1")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "38"), ("subsection", "1"), ("item", "2")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "62"), ("subsection", "1")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "72"), ("subsection", "1")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "72"), ("subsection", "2")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "37"),), FacetKind.HEADING),
        (StructuralAction.TEXT_REPLACE, (("section", "37"), ("subsection", "6")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "37"), ("subsection", "7")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "38"),), FacetKind.HEADING),
        (StructuralAction.TEXT_REPLACE, (("section", "38"), ("subsection", "3")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "38"), ("subsection", "4")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "40"), ("subsection", "1")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "40"), ("subsection", "2")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "40"), ("subsection", "4")), None),
        (StructuralAction.TEXT_REPLACE, (("section", "62"),), FacetKind.HEADING),
        (StructuralAction.TEXT_REPLACE, (("section", "40"),), FacetKind.HEADING),
    ]
    assert all(op.payload is not None and op.payload.attrs.get("case_inflected") is True for op in ops)
    section_38_intro = next(op for op in ops if op.target.path == (("section", "38"), ("subsection", "1")))
    assert section_38_intro.payload is not None
    assert section_38_intro.payload.attrs["subsection_text_scope_meta"].intro_only is True


def test_extract_ee_ops_fans_out_numeric_text_replace_with_rt_quote_prime() -> None:
    text = "paragrahvi 85 lõikes 1, § 86 lõikes 1 ja § 87 lõikes 1 asendatakse arv ˮ200ˮ arvuga ˮ300ˮ;"

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [op.target.path for op in ops] == [
        (("section", "85"), ("subsection", "1")),
        (("section", "86"), ("subsection", "1")),
        (("section", "87"), ("subsection", "1")),
    ]
    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "200" for op in ops)
    assert all(_payload(op).text == "300" for op in ops)


def test_extract_ee_ops_parses_numeric_text_replace_with_rt_quote_prime_on_section() -> None:
    text = "paragrahvis 85 1 asendatakse arv ˮ100ˮ arvuga ˮ300ˮ."

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "85_1"),)
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "100"
    assert ops[0].payload.text == "300"


def test_extract_ee_ops_treats_insert_after_tekstiosa_as_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 74 35 lõiget 2 täiendatakse pärast tekstiosa "
            "«kuni 100 trahviühikut» tekstiosaga "
            "«või sõiduki juhtimise õiguse äravõtmisega kuni kuue kuuni.»;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "74_35"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "kuni 100 trahviühikut"
    assert ops[0].payload.text == ("kuni 100 trahviühikut või sõiduki juhtimise õiguse äravõtmisega kuni kuue kuuni.")
    assert ops[0].payload.attrs.get("rewrite_mode") == "insert_after"


def test_extract_ee_ops_splits_mixed_multi_target_insert_after_and_replace() -> None:
    text = (
        "paragrahvi 5 lõike 2 punkti 4 viimast lauset, § 12 lõiget 10, "
        "§ 13 lõike 12 punkti 7 viiendat lauset ja § 23 lõike 4 kolmandat "
        "lauset täiendatakse tekstiosa „LOADMAN-” järel tekstiosaga "
        "„või INSPECTOR-” ja asendatakse sõnad „korrutatud üleminekuteguriga” "
        "sõnadega „teisendatud võrreldavaks”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "5"), ("subsection", "2"), ("item", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "5"), ("subsection", "2"), ("item", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "12"), ("subsection", "10"))),
        (StructuralAction.TEXT_REPLACE, (("section", "12"), ("subsection", "10"))),
        (StructuralAction.TEXT_REPLACE, (("section", "13"), ("subsection", "12"), ("item", "7"))),
        (StructuralAction.TEXT_REPLACE, (("section", "13"), ("subsection", "12"), ("item", "7"))),
        (StructuralAction.TEXT_REPLACE, (("section", "23"), ("subsection", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "23"), ("subsection", "4"))),
    ]
    assert [_payload(op).attrs["old_text"] for op in ops[:2]] == [
        "LOADMAN-",
        "korrutatud üleminekuteguriga",
    ]
    assert [_payload(op).text for op in ops[:2]] == [
        "LOADMAN- või INSPECTOR-",
        "teisendatud võrreldavaks",
    ]
    assert [_payload(op).attrs["rewrite_mode"] for op in ops[:2]] == [
        "insert_after",
        "replace",
    ]
    assert all(
        _payload(op).attrs.get("source_family")
        == "ee_mixed_multi_target_insert_after_and_replace"
        for op in ops
    )
    last_sentence_meta = read_sentence_target_meta(_payload(ops[0]))
    fifth_sentence_meta = read_sentence_target_meta(_payload(ops[4]))
    third_sentence_meta = read_sentence_target_meta(_payload(ops[6]))
    assert last_sentence_meta is not None
    assert fifth_sentence_meta is not None
    assert third_sentence_meta is not None
    assert last_sentence_meta.sentence_indexes == (1_000_000,)
    assert fifth_sentence_meta.sentence_indexes == (4,)
    assert third_sentence_meta.sentence_indexes == (2,)


def test_extract_ee_ops_splits_mixed_insert_after_and_delete_same_target() -> None:
    text = (
        "paragrahvi 35 lõike 4 punkti 10 1 esimest lauset täiendatakse pärast "
        "sõna „mille” sõnadega „taotluses esitatud” ning punkti mõlemast "
        "lausest jäetakse välja sõnad „tegevusaasta kohta”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 2
    assert [op.action for op in ops] == [StructuralAction.TEXT_REPLACE, StructuralAction.TEXT_REPLACE]
    assert [op.target.path for op in ops] == [
        (("section", "35"), ("subsection", "4"), ("item", "10_1")),
        (("section", "35"), ("subsection", "4"), ("item", "10_1")),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("mille", "mille taotluses esitatud"),
        ("tegevusaasta kohta", ""),
    ]
    assert [_payload(op).attrs["rewrite_mode"] for op in ops] == ["insert_after", "delete"]
    assert all(
        _payload(op).attrs["source_family"] == "ee_mixed_insert_after_and_delete_same_target"
        for op in ops
    )
    assert all(op.witness_rule_id == "ee_mixed_insert_after_and_delete_same_target" for op in ops)
    sentence_meta = read_sentence_target_meta(_payload(ops[0]))
    assert sentence_meta is not None
    assert sentence_meta.sentence_indexes == (0,)


def test_extract_ee_ops_splits_mixed_delete_and_insert_after_same_target() -> None:
    text = (
        "paragrahvi 6 lõikest 2 jäetakse välja sõnad „EUREKA koostöövõrgustiku” "
        "ning täiendatakse pärast sõna „toetatakse” sõnadega „rakendusuuringu ja”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 2
    assert [op.target.path for op in ops] == [
        (("section", "6"), ("subsection", "2")),
        (("section", "6"), ("subsection", "2")),
    ]
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("EUREKA koostöövõrgustiku", ""),
        ("toetatakse", "toetatakse rakendusuuringu ja"),
    ]
    assert [_payload(op).attrs["rewrite_mode"] for op in ops] == ["delete", "insert_after"]
    assert all(
        _payload(op).attrs["source_family"] == "ee_mixed_insert_after_and_delete_same_target"
        for op in ops
    )


def test_extract_ee_ops_splits_two_insert_afters_and_after_anchor_delete() -> None:
    text = (
        "paragrahvi 12 lõike 2 esimest lauset täiendatakse pärast sõna "
        "„taotlusvoorudest” tekstiosaga „, toetuse tingimustest” ja teist lauset "
        "pärast sõna „taotlusvoorud” tekstiosaga „, nende tingimused” ning "
        "jäetakse teisest lausest pärast sõna „ning” välja sõna „nende”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 3
    assert [(_payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ("taotlusvoorudest", "taotlusvoorudest, toetuse tingimustest"),
        ("taotlusvoorud", "taotlusvoorud, nende tingimused"),
        ("ning nende", "ning"),
    ]
    assert [_payload(op).attrs["rewrite_mode"] for op in ops] == [
        "insert_after",
        "insert_after",
        "replace",
    ]
    sentence_meta = read_sentence_target_meta(_payload(ops[2]))
    assert sentence_meta is not None
    assert sentence_meta.sentence_indexes == (1,)


def test_extract_ee_ops_treats_insert_after_arvu_as_text_replace_without_spacing_gap() -> None:
    ops = extract_ee_ops(
        'paragrahvi 16 lõiget 2 täiendatakse pärast arvu "15²" tekstiosaga "–15⁵".',
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "16"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "15²"
    assert ops[0].payload.text == "15²–15⁵"


def test_extract_ee_ops_treats_insert_after_sonu_as_insert_after_mode() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 1 lõiget 1 täiendatakse pärast sõnu "
            "„teenuse korralduse,” sõnaga „terrorismiohvrile,”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "1"), ("subsection", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "teenuse korralduse,"
    assert ops[0].payload.attrs.get("rewrite_mode") == "insert_after"
    assert ops[0].payload.text == "teenuse korralduse, terrorismiohvrile,"


def test_extract_ee_ops_marks_insert_after_terminal_punctuation_boundary() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 5 lõike 10 punkti 3 täiendatakse pärast sõnu "
            "„kolme kuu” tekstiosaga „,välja arvatud juhul, kui seda on tehtud "
            "Sektoritevahelise mobiilsuse toetusmeetmest ja järgmine projekt on "
            "eelneva tegevuse edasiarendus;”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "5"), ("subsection", "10"), ("item", "3"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "kolme kuu"
    assert ops[0].payload.attrs["source_family"] == "ee_insert_after_terminal_punctuation_boundary"
    assert ops[0].payload.text.endswith("edasiarendus;")


def test_extract_ee_ops_does_not_take_subsection_target_from_insert_after_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 1 täiendatakse pärast sõnu “tunnistamise kord” "
            "tekstiosaga “ning taotluse vorm kooskõlas nõukogu määruse "
            "artikli 18 lõike 4 alusel heaks kiidetud kavaga.”;"
        ),
        OperationSource(statute_id="120072012001", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "1"),)
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "tunnistamise kord"
    assert ops[0].payload.attrs["rewrite_mode"] == "insert_after"
    assert "lõike 4" in ops[0].payload.text


def test_extract_ee_ops_preserves_nested_quote_in_insert_after_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 21 lõike 1 punkti 1 täiendatakse pärast sõna "
            "„dokumendid” tekstiosaga „, või elektrooniliselt "
            "„Välissuhtlemisseaduse” § 9 lõike 14 alusel selleks loodud "
            "Eesti arengukoostöö andmekogus”;"
        ),
        OperationSource(statute_id="109082017005", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "21"), ("subsection", "1"), ("item", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "dokumendid"
    assert ops[0].payload.attrs["rewrite_mode"] == "insert_after"
    assert ops[0].payload.text == (
        "dokumendid, või elektrooniliselt „Välissuhtlemisseaduse” § 9 "
        "lõike 14 alusel selleks loodud Eesti arengukoostöö andmekogus"
    )


def test_extract_ee_ops_emits_appendix_table_update_for_section_79_clause() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 79, 5) lisa 1 «Mootorsõidukite kategooriad vastavalt "
            "juhtimisõigusele» tabelis muudetakse B- ja BE-kategooria veerg "
            "«Sõiduki liik ja iseloomustus» ning sõnastatakse järgmiselt: "
            "B auto, mille registrimass ei ületa 3500 kg; "
            "BE autorong, mille registrimass ületab 3500 kg § 2.\x01 Käesolev seadus jõustub."
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "79"),)
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["appendix_table_update"] is True
    assert ops[0].payload.attrs["appendix_marker"] == "Lisa 1"
    assert ops[0].payload.attrs["appendix_table_categories"] == ("B", "BE")
    assert _source_witness(ops[0]).rewrite.appendix_table_update is True
    assert _source_witness(ops[0]).rewrite.appendix_marker == "Lisa 1"
    assert _source_witness(ops[0]).rewrite.appendix_table_categories == ("B", "BE")


def test_extract_ee_ops_emits_global_text_replace_with_exclusions() -> None:
    source = OperationSource(statute_id="ee/test", raw_text="test")
    ops = extract_ee_ops(
        (
            "Veterinaarkorralduse seaduses, välja arvatud §-s 50 1 ja § 50 2 lõikes 2, "
            "asendatakse sõnad „Veterinaar- ja Toiduamet” sõnadega "
            "„Põllumajandus- ja Toiduamet” vastavas käändes."
        ),
        source,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == ()
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "Veterinaar- ja Toiduamet"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[0].payload.attrs["exclude_paths"] == (
        (("section", "50_1"),),
        (("section", "50_2"), ("subsection", "2")),
    )
    assert ops[0].payload.text == "Põllumajandus- ja Toiduamet"
    assert ops[0].payload.attrs["rewrite_witness"] is not None
    assert type(ops[0].payload.attrs["rewrite_witness"]).__name__ == "EETextRewriteWitness"
    assert ops[0].payload.attrs["rewrite_witness"].rewrite.exclude_paths == (
        (("section", "50_1"),),
        (("section", "50_2"), ("subsection", "2")),
    )
    assert ops[0].payload.attrs["rewrite_witness"].rewrite.case_inflected is True


def test_extract_ee_ops_splits_direct_title_agency_pair_rename() -> None:
    text = (
        "Põllumajandusministri 20. veebruari 2009. a määruses nr 25 "
        "„Mahepõllumajandusliku tootmise nõuded” asendatakse sõna "
        "„Põllumajandusamet” ning sõnad „Veterinaar- ja Toiduamet” sõnadega "
        "„Põllumajandus- ja Toiduamet” vastavas käändes."
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 2
    assert [(op.target.path, _payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ((), "Põllumajandusamet", "Põllumajandus- ja Toiduamet"),
        ((), "Veterinaar- ja Toiduamet", "Põllumajandus- ja Toiduamet"),
    ]
    assert all(op.witness_rule_id == "ee_direct_title_global_text_replace" for op in ops)
    assert all(_payload(op).attrs["case_inflected"] is True for op in ops)
    assert all(_payload(op).attrs["source_family"] == "ee_direct_title_global_text_replace" for op in ops)


def test_parse_ee_amendment_ops_extracts_real_104112020001_agency_rename() -> None:
    try:
        archive = open_rt_archive(readonly=True)
        xml = fetch_rt_xml("104112020001", archive)
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"EE archive unavailable in this environment: {exc}")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/104112020001",
        target_title="Mahepõllumajandusliku tootmise nõuded",
    )

    agency_ops = [
        op
        for op in ops
        if op.action is StructuralAction.TEXT_REPLACE
        and op.payload is not None
        and op.payload.text == "Põllumajandus- ja Toiduamet"
    ]
    assert [(op.payload.attrs["old_text"], op.witness_rule_id) for op in agency_ops] == [
        ("Põllumajandusamet", "ee_old_format_direct_title_unnumbered_text_replace"),
        ("Veterinaar- ja Toiduamet", "ee_old_format_direct_title_unnumbered_text_replace"),
    ]
    assert all(
        "ee_old_format_direct_title_unnumbered_text_replace" in op.provenance_tags
        for op in agency_ops
    )
    assert all(op.payload.attrs["case_inflected"] is True for op in agency_ops)


def test_parse_ee_amendment_ops_slices_old_format_preambul_html_sections_for_2021_012() -> None:
    archive = open_rt_archive(readonly=True)
    try:
        xml = fetch_rt_xml("123122021012", archive)
    finally:
        archive.close()

    ops = parse_ee_amendment_ops(
        xml,
        "ee/123122021012",
        target_title="Mahepõllumajandusliku tootmise nõuded",
    )

    by_target = {(op.action, op.target.path) for op in ops}
    assert (StructuralAction.REPEAL, (("section", "6"),)) in by_target
    assert (StructuralAction.REPEAL, (("section", "16_1"),)) in by_target
    assert (StructuralAction.REPEAL, (("section", "22"),)) in by_target
    assert (StructuralAction.INSERT, (("section", "7"), ("item", "15"))) not in by_target
    assert all(
        "ee_plain_paragraph_html_items_extracted" not in op.provenance_tags
        for op in ops
    )
    section_5_subsection_3 = next(
        op for op in ops
        if op.target.path == (("section", "5"), ("subsection", "3"))
    )
    assert section_5_subsection_3.payload is not None
    assert not section_5_subsection_3.payload.text.endswith("ˮ")


def test_extract_ee_ops_emits_global_text_replace_with_plural_subsection_exclusions() -> None:
    source = OperationSource(statute_id="ee/test", raw_text="test")
    ops = extract_ee_ops(
        (
            "Raskeveokimaksu seaduses, välja arvatud § 13 lõigetes 2 ja 3, "
            "asendatakse sõna „Maanteeamet” sõnaga „Transpordiamet” vastavas käändes."
        ),
        source,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == ()
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "Maanteeamet"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[0].payload.attrs["exclude_paths"] == (
        (("section", "13"), ("subsection", "2")),
        (("section", "13"), ("subsection", "3")),
    )
    assert ops[0].payload.text == "Transpordiamet"


def test_parse_ee_amendment_ops_skips_self_referential_amendment_act_title() -> None:
    xml = b"""
    <akt xmlns="akt_1_10.06.2010">
      <sisu>
        <osa>
          <paragrahv>
            <paragrahvPealkiri>Ettev\xc3\xb5tlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seaduse muutmine</paragrahvPealkiri>
            <sisuTekst>
              <tavatekst>Ettev\xc3\xb5tlustulu lihtsustatud maksustamise seaduse ja tulumaksuseaduse muutmise ning julgeolekumaksu seaduse kehtetuks tunnistamise seaduse \xc2\xa7-d 1 ja 3 ning \xc2\xa7 4 l\xc3\xb5ige 1 j\xc3\xa4etakse v\xc3\xa4lja.</tavatekst>
            </sisuTekst>
          </paragrahv>
        </osa>
      </sisu>
    </akt>
    """

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        "Ettevõtlustulu lihtsustatud maksustamise seadus",
    )

    assert ops == []


def test_parse_ee_amendment_ops_admits_section_scoped_target_paragraph_title() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/oigusakt/1.0">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Asjaõigusseaduse § 126 muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Asjaõigusseaduse § 126 tekst muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„(1) Test.”</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Asjaõigusseadus",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "126"),)),
    ]


def test_parse_ee_amendment_ops_handles_preambul_single_target_insert() -> None:
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Riigikogu liikme staatuse seaduse täiendamise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <preambul>
          <tavatekst><b>Riigikogu liikme staatuse seadust</b> täiendatakse §-ga 60<sup>2</sup> järgmises sõnastuses:</tavatekst>
        </preambul>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p>„<b>§ 60<sup>2</sup>. Riigikogu liikme tööga seotud kulude hüvitamise ajutine korraldus</b></p>
            <p>2025. aasta 1. jaanuarist kuni Riigikogu XV koosseisu volituste lõpuni hüvitatakse Riigikogu liikmele kuludokumentide alusel tööga seotud kulutused kuni 25% Riigikogu liikme ametipalgast Riigikogu juhatuse kehtestatud korras.”.</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Riigikogu liikme staatuse seadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "60_2"),)


def test_parse_ee_amendment_ops_does_not_duplicate_direct_body_intro_payload() -> None:
    xml = """
    <oigusakt xmlns="muutmismaarus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Sotsiaalministri määruse nr 70 „Meetme «Õendus- ja hooldusteenuste infrastruktuuri arendamine» toetuse andmise ja toetuse kasutamise seire tingimused ja kord” muutmine</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <preambul>
          <tavatekst>Määrus kehtestatakse volitusnormi alusel.</tavatekst>
        </preambul>
        <sisuTekst>
          <tavatekst>Sotsiaalministri määruse nr 70 „Meetme «Õendus- ja hooldusteenuste infrastruktuuri arendamine» toetuse andmise ja toetuse kasutamise seire tingimused ja kord” § 6 lõige 2 sõnastatakse järgmiselt:<reavahetus/>„(2) Projekti abikõlblikkuse perioodi alguskuupäev ei või olla hilisem kui 31. detsember 2015.”.</tavatekst>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title=(
            "Meetme «Õendus- ja hooldusteenuste infrastruktuuri arendamine» "
            "toetuse andmise ja toetuse kasutamise seire tingimused ja kord"
        ),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "6"), ("subsection", "2"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == (
        "(2) Projekti abikõlblikkuse perioodi alguskuupäev ei või olla hilisem "
        "kui 31. detsember 2015."
    )
    assert ops[0].payload.text.count("Projekti abikõlblikkuse") == 1
    assert "Õendus- ja hooldusteenuste" not in ops[0].payload.text


def test_parse_ee_amendment_ops_keeps_html_direct_body_intro_as_payload_carrier() -> None:
    xml = """
    <oigusakt xmlns="muutmismaarus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Vabariigi Valitsuse määruse nr 312 „Pirita jõeoru maastikukaitseala kaitse-eeskiri” muutmine</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <preambul>
          <tavatekst>Määrus kehtestatakse volitusnormi alusel.</tavatekst>
        </preambul>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p><b>§ 1. Määruse muutmine</b></p>
            <p>Vabariigi Valitsuse määruse nr 312 „Pirita jõeoru maastikukaitseala kaitse-eeskiri” § 7 lõike 2 punkt 4 sõnastatakse järgmiselt:</p>
            <p>„4) Botaanikaaia piiranguvööndis kaitseala valitseja nõusolekul ehitiste püstitamine;”.</p>
            <p><b>§ 2. Muudatuse põhjendus</b></p>
            <p>Seletuskirjas on esitatud põhjendus.</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Pirita jõeoru maastikukaitseala kaitse-eeskiri",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "7"), ("subsection", "2"), ("item", "4"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == (
        "4) Botaanikaaia piiranguvööndis kaitseala valitseja nõusolekul "
        "ehitiste püstitamine;"
    )
    assert "Muudatuse põhjendus" not in ops[0].payload.text


def test_parse_ee_amendment_ops_strips_html_amendment_section_heading_before_direct_target() -> None:
    xml = """
    <oigusakt xmlns="muutmismaarus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Sotsiaalministri määruste muutmine</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <preambul>
          <tavatekst>Määrus kehtestatakse volitusnormi alusel.</tavatekst>
        </preambul>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p><b>§ 4. Sotsiaalministri määruse nr 103 „Haigla liikide nõuded” muutmine</b></p>
            <p>Sotsiaalministri määruse nr 103 „Haigla liikide nõuded” § 1 lõige 4 tunnistatakse kehtetuks.</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Haigla liikide nõuded",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "1"), ("subsection", "4"))
    assert "ee_direct_target_title_prefix_stripped_for_structural_repeal" in ops[0].provenance_tags


def test_parse_ee_amendment_ops_strips_real_html_amendment_section_heading_wrapper() -> None:
    archive = open_rt_archive(readonly=True)
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("129122020040", archive),
        "ee/129122020040",
        target_title="Haigla liikide nõuded",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "1"), ("subsection", "4"))
    assert "ee_html_amendment_section_heading_wrapper_stripped" in ops[0].provenance_tags
    assert ops[0].witness_rule_id == "ee_html_amendment_section_heading_wrapper_stripped"


def test_parse_ee_amendment_ops_keeps_target_header_single_sentence_delete() -> None:
    xml = """
    <akt xmlns="akt_1_10.06.2010">
      <sisu>
        <osa>
          <paragrahv>
            <paragrahvNr>2</paragrahvNr>
            <paragrahvPealkiri>Sõjaaja riigikaitse seaduse muutmine</paragrahvPealkiri>
            <sisuTekst>
              <tavatekst>Sõjaaja riigikaitse seaduse (RT I, 08.07.2011, 72) § 4 lõikest 1 jäetakse välja teine lause.</tavatekst>
            </sisuTekst>
          </paragrahv>
        </osa>
      </sisu>
    </akt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Sõjaaja riigikaitse seadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "4"), ("subsection", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == ""


def test_parse_ee_amendment_ops_single_target_preambul_preserves_sections_5_to_10() -> None:
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <sisu>
        <preambul>
          <tavatekst><b>Ehitusseaduse</b> § 5, 6, 7, 8, 9 ja 10 muutmine</tavatekst>
        </preambul>
        <sisuTekst>
          <tavatekst>Ehitusseaduse § 5, 6, 7, 8, 9 ja 10 muudatused:</tavatekst>
          <HTMLKonteiner><![CDATA[
            <p><b>1)</b> § 5 jäetakse välja.</p>
            <p><b>2)</b> § 6 asendatakse sõnaga \"kattega\".</p>
            <p><b>3)</b> § 7 jäetakse välja.</p>
            <p><b>4)</b> § 8 asendatakse sõnaga \"kattega\".</p>
            <p><b>5)</b> § 9 jäetakse välja.</p>
            <p><b>6)</b> § 10 asendatakse sõnaga \"kattega\".</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/104072013003",
        target_title="Ehitusseadus",
    )

    assert len(ops) == 6
    section_actions = {dict(op.target.path)["section"]: op.action for op in ops if "section" in dict(op.target.path)}
    assert section_actions == {
        "5": StructuralAction.REPEAL,
        "6": StructuralAction.TEXT_REPLACE,
        "7": StructuralAction.REPEAL,
        "8": StructuralAction.TEXT_REPLACE,
        "9": StructuralAction.REPEAL,
        "10": StructuralAction.TEXT_REPLACE,
    }
    replace_ops = [op for op in ops if op.action is StructuralAction.TEXT_REPLACE]
    assert len(replace_ops) == 3
    assert all(op.text_patch is None for op in replace_ops)
    for op in replace_ops:
        meta = read_payload_rewrite_meta(_payload(op))
        assert meta.rewrite is not None
        assert meta.rewrite.old_surface == ""
        assert meta.rewrite.new_surface == "kattega"
        assert meta.rewrite.mode.value == "replace"


def test_parse_ee_amendment_ops_handles_preambul_only_single_target_replace() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <preambul>
          <tavatekst><b>Turvaseaduse</b> § 21 lõike 2 esimeses lauses ja § 22 lõike 1 esimeses lauses asendatakse tekstiosa „19-aastane” tekstiosaga „18-aastane”.</tavatekst>
        </preambul>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Turvaseadus",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "21"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "22"), ("subsection", "1"))),
    ]
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "19-aastane" for op in ops)
    assert all(_payload(op).text == "18-aastane" for op in ops)


def test_parse_ee_amendment_ops_untitled_target_paragraph_keeps_itemized_ops() -> None:
    from farchive import Farchive

    from lawvm.estonia.fetch import _DEFAULT_RT_DB

    act = "107072015003"
    try:
        raw = Farchive(_DEFAULT_RT_DB, readonly=True).get(f"https://www.riigiteataja.ee/akt/{act}.xml")
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"EE archive unavailable in this environment: {exc}")

    assert raw is not None
    ops = parse_ee_amendment_ops(
        raw,
        act,
        target_title="Maapõueseadus",
    )

    assert any(op.target.path == (("section", "25_1"), ("subsection", "2")) for op in ops)
    assert any(op.target.path == (("section", "25_2"),) for op in ops)
    assert any(op.target.path == (("section", "68_3"),) for op in ops)
    assert any(op.target.path == (("section", "75"), ("subsection", "11")) for op in ops)


def test_parse_ee_amendment_ops_keeps_genitive_target_clause_in_pure_tavatekst_paragraph() -> None:
    try:
        xml = fetch_rt_xml("102012025003", open_rt_archive(readonly=True))
    except (OSError, RuntimeError) as exc:
        pytest.skip(f"EE archive unavailable in this environment: {exc}")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/102012025003",
        target_title="Majandustegevuse seadustiku üldosa seadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "22"), ("subsection", "6"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs.get("old_text") == "rahvatervise"
    assert ops[0].payload.text == "rahvastiku tervise"


def test_parse_ee_amendment_ops_handles_constitutional_review_repeal() -> None:
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Korruptsioonivastase seaduse § 19 lõike 2 punkti 2 põhiseaduspärasuse kontroll</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p><b>RESOLUTSIOON</b></p>
            <p><b>Tunnistada korruptsioonivastase seaduse § 19 lõige 2 punkt 2 põhiseadusega vastuolus olevaks ja kehtetuks.</b></p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Korruptsioonivastane seadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "19"), ("subsection", "2"), ("item", "2"))


def test_parse_ee_amendment_ops_handles_constitutional_review_repeal_in_genitive_citation_form() -> None:
    xml = """
    <oigusakt xmlns="akt_1_10.06.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Korruptsioonivastase seaduse § 19 lõike 2 punkti 2 põhiseaduspärasuse kontroll</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p><b>RESOLUTSIOON</b></p>
            <p><b>Tunnistada korruptsioonivastase seaduse § 19 lõike 2 punkti 2 põhiseadusega vastuolus olevaks ja kehtetuks.</b></p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Korruptsioonivastane seadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == (("section", "19"), ("subsection", "2"), ("item", "2"))


def test_parse_ee_amendment_ops_keeps_direct_target_clause_inside_other_act_paragraph() -> None:
    xml = """
    <akt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Alusharidusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> Toiduseaduse § 8 lõike 1 punktis 1<sup>2</sup> asendatakse sõnad „koolieelne lasteasutus” sõnaga „lasteaed”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </akt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Toiduseadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "8"), ("subsection", "1"), ("item", "1_2"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "koolieelne lasteasutus"
    assert ops[0].payload.text == "lasteaed"


def test_parse_ee_amendment_ops_keeps_direct_target_clause_inside_other_act_wrapper_payload() -> None:
    xml = """
    <akt xmlns="akt_1_10.06.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Alusharidusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>4)</b> paragrahvi 75 tekst muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„Toiduseaduse § 8 lõike 1 punktis 1<sup>2</sup> asendatakse sõnad „koolieelne lasteasutus” sõnaga „lasteaed”.”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </akt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Toiduseadus",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "8"), ("subsection", "1"), ("item", "1_2"))
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "koolieelne lasteasutus"
    assert ops[0].payload.text == "lasteaed"


def test_parse_ee_amendment_ops_accepts_adjectival_target_header_match() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Korruptsioonivastase seaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>Korruptsioonivastases seaduses tehakse järgmised muudatused:</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 14 lõike 8 teist lauset täiendatakse pärast sõna „abieluvaralepingu” sõnadega „või kooselulepingu”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Korruptsioonivastane seadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.TEXT_REPLACE
    assert ops[0].target.path == (("section", "14"), ("subsection", "8"))


def test_old_format_target_section_with_plain_clause_does_not_fall_back_to_whole_act() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>\xc2\xa7 1. Huvikooli seadus</b></p>
        <p>Huvikooli seaduse \xc2\xa7 13 muudetakse ja s\xc3\xb5nastatakse j\xc3\xa4rgmiselt:</p>
        <p>\xc2\xab\xc2\xa7 13. Huviharidus\xc2\xbb</p>
        <p><b>\xc2\xa7 35. Liiklusseaduse muutmine</b></p>
        <p>Liiklusseaduse (RT I 2001, 3, 6; 2005, 68, 529) \xc2\xa7 6 l\xc3\xb5ige 2 muudetakse ja s\xc3\xb5nastatakse j\xc3\xa4rgmiselt:</p>
        <p>\xc2\xab(2) Laste liikluskasvatust viivad l\xc3\xa4bi ka huvikoolid.\xc2\xbb</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "6"), ("subsection", "2"))


def test_target_embedded_whole_act_repeal_section_does_not_fall_back_to_foreign_ops() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>§ 1. Kriminaalmenetluse seadustiku muutmine</b></p>
              <p>Kriminaalmenetluse seadustiku § 1 lõikes 1 asendatakse sõna „vana” sõnaga „uus”.</p>
              <p><b>§ 19. Jälitustegevuse seaduse kehtetuks tunnistamine</b></p>
              <p>Jälitustegevuse seadus tunnistatakse kehtetuks.</p>
              <p><b>§ 20. Seaduse jõustumine</b></p>
              <p>Käesolev seadus jõustub 2012. aasta 1. jaanuaril.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Jälitustegevuse seadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPEAL
    assert ops[0].target.path == ()
    assert ops[0].target.special is None


def test_new_format_dedicated_tavatekst_section_skips_primary_act_leakage() -> None:
    xml = b"""
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <sisuTekst>
            <tavatekst>Paragrahvi 16 l\xc3\xb5iget 6 t\xc3\xa4iendatakse s\xc3\xb5nadega \xc2\xableke\xc2\xbb.</tavatekst>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <sisuTekst>
            <tavatekst><b>Liiklusseadust</b> t\xc3\xa4iendatakse \xc2\xa7-ga 50<sup>4</sup> j\xc3\xa4rgmises s\xc3\xb5nastuses:</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>\xc2\xab<b>\xc2\xa7 50<sup>4</sup>. Viivistasu sundt\xc3\xa4itmise aegumine</b></p>
              <p>Viivistasu sundt\xc3\xa4itmise aegumisele kohaldatakse maksukorralduse seadust.\xc2\xbb</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "50_4"),)


def test_new_format_dedicated_html_wrapper_section_seeds_item_context() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvPealkiri>Jäätmeseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Jäätmeseaduse § 105 lõikes 4 asendatakse sõna „vana” sõnaga „uus”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Liiklusseaduse § 45 lõikes 5 tehakse järgmised muudatused:</p>
              <p><b>1)</b> punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) riigimaanteel – Maanteeamet;”;</p>
              <p><b>2)</b> punkt 2 tunnistatakse kehtetuks.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.REPLACE, (("section", "45"), ("subsection", "5"), ("item", "1"))) in targets
    assert (StructuralAction.REPEAL, (("section", "45"), ("subsection", "5"), ("item", "2"))) in targets


def test_old_format_multi_section_no_target_match_returns_no_ops() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>\xc2\xa7 1. Avaliku teabe seaduses</b> tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>1)</b> paragrahvi 2 l\xc3\xb5iget 1 t\xc3\xa4iendatakse punktiga 2<sup>1</sup>.</p>
        <p><b>\xc2\xa7 2. Arhiiviseaduses</b> tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>1)</b> paragrahvi 5 l\xc3\xb5ige 2 muudetakse ja s\xc3\xb5nastatakse j\xc3\xa4rgmiselt.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert ops == []


def test_old_format_parenthesized_repeal_item_parses_distinct_section_repeal() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>\xc2\xa7 1. Liiklusseaduses</b> tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>(8)</b> Paragrahv 73 tunnistatakse kehtetuks.</p>
        <p><b>(9)</b> Paragrahv 74<sup>39</sup> tunnistatakse kehtetuks.</p>
        <p><b>\xc2\xa7 2.</b> K\xc3\xa4esoleva seaduse \xc2\xa7 1 l\xc3\xb5iked 2 ja 3 j\xc3\xb5ustuvad 2005. aasta 1. juunil.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.REPEAL, (("section", "73"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "74_39"),)) in targets


def test_parse_ee_amendment_ops_handles_real_staged_repeal_from_kofs_act() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <aktinimi>
        <nimi>
          <pealkiri>Kohaliku omavalitsuse üksuse finantsjuhtimise seadus</pealkiri>
        </nimi>
      </aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>65</paragrahvNr>
          <paragrahvPealkiri>Valla- ja linnaeelarve seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Valla- ja linnaeelarve seaduses (RT I 1993, 42, 615; 2009, 35, 232) tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 8 lõige 6 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>«(6) Valla- ja linnavalitsus on kohustatud esitama sõlmitud laenulepingu ...»;</p>
                <p><b>2)</b> paragrahvi 8 lõiked 7, 8 ja 9 tunnistatakse kehtetuks;</p>
                <p><b>3)</b> paragrahv 8<sup>1</sup> muudetakse ja sõnastatakse järgmiselt:</p>
                <p>«<b>§ 8<sup>1</sup>. Kohustuste võtmise piirangud seoses eelarve puudujäägiga</b></p>
                <p>(1) Kohustuse võtmise tähtajaline piirang ...»</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>66</paragrahvNr>
          <paragrahvPealkiri>Seaduse kehtetuks tunnistamine</paragrahvPealkiri>
          <loige>
            <loigeNr>1</loigeNr>
            <sisuTekst>
              <tavatekst>Valla- ja linnaeelarve seaduse (RT I 1993, 42, 615; 2009, 35, 232) §-d 1–25 ning 26<sup>1</sup> tunnistatakse kehtetuks alates 2012. aasta 1. jaanuarist.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Valla- ja linnaeelarve seadus")
    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.REPLACE, (("section", "8"), ("subsection", "6"))) in targets
    assert (StructuralAction.REPEAL, (("section", "8"), ("subsection", "7"))) in targets
    assert (StructuralAction.REPEAL, (("section", "8"), ("subsection", "8"))) in targets
    assert (StructuralAction.REPEAL, (("section", "8"), ("subsection", "9"))) in targets
    assert (StructuralAction.REPLACE, (("section", "8_1"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "1"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "25"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "26_1"),)) in targets
    assert all(path for _, path in targets)
    delayed_repeals = [
        op for op in ops
        if op.target.path and op.target.path[0] in {( "section", "1"), ( "section", "25"), ( "section", "26_1")}
    ]
    assert delayed_repeals
    assert all(op.source is not None and op.source.effective == "2012-01-01" for op in delayed_repeals)


def test_parse_ee_amendment_ops_keeps_plural_section_repeal_as_fresh_target_in_new_format_act() -> None:
    xml = fetch_rt_xml("131122025002", open_rt_archive(readonly=True))

    ops = parse_ee_amendment_ops(xml, "ee/131122025002", target_title="Maaparandusseadus")
    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.INSERT, (("section", "98_1"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "99"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "100"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "101"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "102"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "103"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "98_1 7"),)) not in targets


def test_parse_ee_amendment_ops_assigns_old_format_commencement_dates_to_named_items() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Liiklusseaduses tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>„1) esimene muudatus;”;</p>
                <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
                <p><b>3)</b> paragrahvi 9 täiendatakse lõikega 2 järgmises sõnastuses:</p>
                <p>„(2) kolmas muudatus.”;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <loigeNr>1</loigeNr>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse § 1 punktid 2 ja 3 jõustuvad 2019. aasta 1. jaanuaril.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == ""
    assert effective_by_target["section:8/subsection:1"] == "2019-01-01"
    assert effective_by_target["section:9/subsection:2"] == "2019-01-01"


def test_parse_ee_amendment_ops_reads_old_format_dates_from_rakendamine_section() -> None:
    archive = open_rt_archive(readonly=True)
    target_title = (
        "Eesti Haigekassa meditsiiniseadmete loetelu ja meditsiiniseadmete "
        "loetellu kantud meditsiiniseadme eest tasu maksmise kohustuse "
        "ülevõtmise kord"
    )
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("108012013001", archive),
        "ee/108012013001",
        target_title=target_title,
        ref_effective="2013-04-01",
        has_earlier_same_act_slice=True,
    )
    effective_by_item = {
        next(
            tag.split(":", 1)[1]
            for tag in op.provenance_tags
            if tag.startswith("old_format_amendment_item:")
        ): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_item["6"] == "2013-04-01"
    assert effective_by_item["17"] == "2013-03-01"
    assert effective_by_item["1"] == ""


def test_parse_ee_amendment_ops_assigns_old_format_commencement_dates_when_sentence_lists_other_sections() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Riigipiiri seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Riigipiiri seaduses tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 7<sup>3</sup> lõige 4 muudetakse ja sõnastatakse järgmiselt:<br/>
                ˮ(4) esimene muudatus.ˮ;</p>
                <p><b>2)</b> paragrahvi 7<sup>4</sup> täiendatakse lõikega 5 järgmises sõnastuses:<br/>
                ˮ(5) teine muudatus.ˮ;</p>
                <p><b>3)</b> paragrahvi 11 lõikes 7 asendatakse arv ˮ1ˮ arvuga ˮ2ˮ;</p>
                <p><b>4)</b> paragrahvi 11 lõiked 9 ja 10 tunnistatakse kehtetuks;</p>
                <p><b>5)</b> paragrahvi 11 täiendatakse lõikega 12<sup>1</sup> järgmises sõnastuses:<br/>
                ˮ(12<sup>1</sup>) viies muudatus.ˮ;</p>
                <p><b>6)</b> paragrahvi 20 lõike 2 esimest lauset täiendatakse pärast sõna ˮpiiriesindajadˮ sõnadega ˮ, kelle määrab ministerˮ.</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>6</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <loigeNr>1</loigeNr>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse §-d 1 ja 2 ning § 5 punktid 4 ja 5 jõustuvad 2025. aasta 12. oktoobril.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Riigipiiri seadus",
        ref_effective="2025-10-12",
    )
    effective_by_item = {
        next(
            tag.split(":", 1)[1]
            for tag in op.provenance_tags
            if tag.startswith("old_format_amendment_item:")
        ): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_item["1"] == ""
    assert effective_by_item["2"] == ""
    assert effective_by_item["3"] == ""
    assert effective_by_item["4"] == "2025-10-12"
    assert effective_by_item["5"] == "2025-10-12"
    assert effective_by_item["6"] == ""


def test_parse_ee_amendment_ops_assigns_old_format_commencement_after_item_clause_gap() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <aktinimi><nimi><pealkiri>Testseaduse muutmise seadus</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>164</paragrahvNr>
          <paragrahvPealkiri>Testseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Testseaduse 10. peatükk muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„10. peatükk Uus peatükk § 53. Test (1) Test.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>169</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>Korrakaitseseadus ja käesoleva seaduse §-d 1–99, § 100 punktid 1–5 ja 8–12 ning §-d 101–168 jõustuvad 2014. aasta 1. juulil.</tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Testseadus",
        ref_effective="2014-07-01",
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("chapter", "10"),)
    assert ops[0].source is not None
    assert ops[0].source.effective == "2014-07-01"


def test_parse_ee_amendment_ops_routes_old_format_general_order_to_whole_act_default() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>74</paragrahvNr>
          <paragrahvPealkiri>Asjaõigusseaduse rakendamise seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Asjaõigusseaduse rakendamise seaduse § 12 lõike 5 esimest lauset täiendatakse pärast viimast sõna tekstiosaga „, sealhulgas ehitise jagamist reaalosadeks”.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>95</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <loigeNr>1</loigeNr>
            <sisuTekst>
              <tavatekst>Käesolev seadus jõustub 2018. aasta 1. jaanuaril.</tavatekst>
            </sisuTekst>
          </loige>
          <loige>
            <loigeNr>2</loigeNr>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse § 82 punktid 2, 4 ja 5 jõustuvad 2016. aasta 1. jaanuaril.</tavatekst>
            </sisuTekst>
          </loige>
          <loige>
            <loigeNr>3</loigeNr>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse § 74 jõustub üldises korras.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    later_ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Asjaõigusseaduse rakendamise seadus",
        ref_effective="2018-01-01",
        has_earlier_same_act_slice=True,
    )
    assert len(later_ops) == 1
    assert later_ops[0].source is not None
    assert later_ops[0].source.effective == "2018-01-01"

    earlier_ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Asjaõigusseaduse rakendamise seadus",
        ref_effective="2016-01-01",
        has_earlier_same_act_slice=True,
    )
    assert earlier_ops == []


def test_parse_ee_amendment_ops_stamps_old_format_whole_act_default_with_same_section_exception() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>15</paragrahvNr>
          <paragrahvPealkiri>Võlgade ümberkujundamise ja võlakaitse seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 6 lõikes 1 asendatakse sõna ˮkohtunikuabiˮ sõnaga ˮkohtujuristˮ;</p>
                <p><b>2)</b> paragrahvi 6 lõiget 3 täiendatakse pärast sõna ˮtühistamiseˮ sõnadega ˮ, samuti tasu määramiseˮ;</p>
                <p><b>5)</b> seadust täiendatakse §-ga 52 järgmises sõnastuses:</p>
                <p>ˮ<b>§ 52. Ajutine säte</b></p>
                <p>(1) Ajutine tekst.ˮ.</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>17</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Käesolev seadus jõustub 2021. aasta 1. veebruaril.</tavatekst>
            </sisuTekst>
          </loige>
          <loige>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse § 15 punkt 5 jõustub Riigi Teatajas avaldamisele järgneval päeval.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Võlgade ümberkujundamise ja võlakaitse seadus",
        ref_effective="2021-02-01",
        has_earlier_same_act_slice=True,
    )
    effective_by_item = {
        next(
            tag.split(":", 1)[1]
            for tag in op.provenance_tags
            if tag.startswith("old_format_amendment_item:")
        ): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_item["1"] == "2021-02-01"
    assert effective_by_item["2"] == "2021-02-01"
    assert effective_by_item["5"] == "2021-02-01"


def test_parse_ee_amendment_ops_ignores_quoted_old_format_commencement_payloads() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Liiklusseaduses tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 7 lõige 1 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>„(1) esimene muudatus.”;</p>
                <p><b>2)</b> seadust täiendatakse §-ga 8<sup>1</sup> järgmises sõnastuses:</p>
                <p>„<b>§ 8<sup>1</sup>. Uus säte</b></p>
                <p>(1) teine muudatus.”;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>7</paragrahvNr>
          <paragrahvPealkiri>Teise seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahv 2 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>„<b>§ 2. Seaduse jõustumine</b></p>
                <p>(1) Käesolev seadus jõustub 2020. aasta 1. augustil.</p>
                <p>(2) Käesoleva seaduse § 1 punktid 1 ja 2 jõustuvad 2020. aasta 1. augustil.</p>”.</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>8</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Käesolev seadus jõustub 2020. aasta 24. mail.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1"] == ""
    assert effective_by_target["section:8_1"] == ""


def test_parse_ee_amendment_ops_reads_old_format_target_from_tavatekst_intro_with_html_block() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <sisuTekst>
            <tavatekst><b>Liiklusseaduses</b> tehakse järgmised muudatused:</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõikes 1 asendatakse arv „1” arvuga „2”;</p>
              <p><b>2)</b> seadust täiendatakse §-ga 8<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 8<sup>1</sup>. Uus säte</b></p>
              <p>(1) teine muudatus.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <sisuTekst>
            <tavatekst><b>Teises seaduses</b> tehakse järgmised muudatused:</tavatekst>
          </sisuTekst>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 3 lõiget 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„(1) kõrvaline muudatus.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")
    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.TEXT_REPLACE, (("section", "7"), ("subsection", "1"))) in targets
    assert (StructuralAction.INSERT, (("section", "8_1"),)) in targets
    assert all("old_format_amendment_section:1" in op.provenance_tags for op in ops)


def test_parse_ee_amendment_ops_skips_old_format_later_slice_when_target_section_is_not_delayed() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Esimese seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 14 lõike 2 punkt 2 tunnistatakse kehtetuks;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <paragrahvPealkiri>Teise seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 7 lõikes 1 asendatakse arv „1” arvuga „2”;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Käesoleva seaduse § 2 jõustub 2014. aasta 1. juulil.</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    earlier_ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Esimese seaduse",
        ref_effective="2014-05-31",
    )
    later_ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Esimese seaduse",
        ref_effective="2014-07-01",
        has_earlier_same_act_slice=True,
    )

    assert [(op.action, str(op.target)) for op in earlier_ops] == [
        (StructuralAction.REPEAL, "section:14/subsection:2/item:2"),
    ]
    assert later_ops == []


def test_parse_ee_amendment_ops_extracts_quote_prime_payload_without_wrappers() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Riigipiiri seaduse muutmine</paragrahvPealkiri>
          <loige>
            <sisuTekst>
              <tavatekst>Riigipiiri seaduses tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 7<sup>3</sup> lõige 4 muudetakse ja sõnastatakse järgmiselt:<br/>
                ˮ(4) Kui tekst muutub.ˮ;</p>
                <p><b>2)</b> paragrahvi 7<sup>4</sup> täiendatakse lõikega 5 järgmises sõnastuses:<br/>
                ˮ(5) Piiriületuse ootejärjekorra andmekogu andmed ei ole avalikud.ˮ;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Riigipiiri seadus")
    payload_texts = [op.payload.text for op in ops if op.payload is not None and op.payload.text]

    assert "(4) Kui tekst muutub." in payload_texts
    assert "(5) Piiriületuse ootejärjekorra andmekogu andmed ei ole avalikud." in payload_texts
    assert not any(text.startswith("ˮ") for text in payload_texts)
    assert not any(text.endswith("ˮ;") for text in payload_texts)


def test_parse_ee_amendment_ops_assigns_new_format_html_commencement_dates_to_named_items() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) esimene muudatus;”;</p>
              <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
              <p><b>3)</b> paragrahvi 9 täiendatakse lõikega 2 järgmises sõnastuses:</p>
              <p>„(2) kolmas muudatus.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>(1) Käesolev seadus jõustub 2018. aasta 1. juulil.</p>
              <p>(2) Käesoleva seaduse § 1 punktid 2 ja 3 ning § 4 jõustuvad 2019. aasta 1. jaanuaril.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == ""
    assert effective_by_target["section:8/subsection:1"] == "2019-01-01"
    assert effective_by_target["section:9/subsection:2"] == "2019-01-01"


def test_parse_ee_amendment_ops_assigns_later_same_act_slice_from_whole_act_default() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) esimene muudatus;”;</p>
              <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
              <p><b>3)</b> paragrahvi 9 täiendatakse lõikega 2 järgmises sõnastuses:</p>
              <p>„(2) kolmas muudatus.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>(1) Käesolev seadus jõustub 2019. aasta 1. jaanuaril.</p>
              <p>(2) Käesoleva seaduse § 1 punkt 2 jõustub üldises korras.</p>
              <p>(3) Käesoleva seaduse § 1 punkt 3 jõustub 2018. aasta 1. juulil.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Liiklusseadus",
        ref_effective="2019-01-01",
        has_earlier_same_act_slice=True,
    )
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == "2019-01-01"
    assert effective_by_target["section:8/subsection:1"] == "2019-01-01"
    assert effective_by_target["section:9/subsection:2"] == "2018-07-01"


def test_parse_ee_amendment_ops_assigns_whole_act_default_without_general_order_exceptions() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) esimene muudatus;”;</p>
              <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>(1) Käesolev seadus jõustub 2019. aasta 1. jaanuaril.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Liiklusseadus",
        ref_effective="2019-01-01",
        has_earlier_same_act_slice=True,
    )
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == "2019-01-01"
    assert effective_by_target["section:8/subsection:1"] == "2019-01-01"


def test_parse_ee_amendment_ops_routes_earlier_same_act_slice_ops_to_later_whole_act_default() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) esimene muudatus;”;</p>
              <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>(1) Käesolev seadus jõustub 2022. aasta 1. jaanuaril.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Liiklusseadus",
        ref_effective="2021-01-01",
        has_earlier_same_act_slice=False,
    )
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == "2022-01-01"
    assert effective_by_target["section:8/subsection:1"] == "2022-01-01"


def test_parse_ee_amendment_ops_does_not_default_later_same_act_slice_without_whole_act_clause() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Liiklusseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>1)</b> paragrahvi 7 lõike 1 punkt 1 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„1) esimene muudatus;”;</p>
              <p><b>2)</b> paragrahvi 8 lõikes 1 asendatakse arv „9” arvuga „10”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>5</paragrahvNr>
          <paragrahvPealkiri>Seaduse jõustumine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>(1) Käesoleva seaduse § 1 punkt 2 jõustub 2019. aasta 1. jaanuaril.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Liiklusseadus",
        ref_effective="2019-01-01",
        has_earlier_same_act_slice=True,
    )
    effective_by_target = {
        str(op.target): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_target["section:7/subsection:1/item:1"] == ""
    assert effective_by_target["section:8/subsection:1"] == "2019-01-01"


def test_parse_ee_amendment_ops_collects_plain_direct_target_clause_without_html_wrapper() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>20</paragrahvNr>
          <paragrahvPealkiri>Erakonnaseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <tavatekst>Erakonnaseaduse § 12 20 lõige 1 tunnistatakse kehtetuks.</tavatekst>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Erakonnaseadus")

    assert [(op.action, str(op.target)) for op in ops] == [
        (StructuralAction.REPEAL, "section:12_20/subsection:1"),
    ]


def test_parse_ee_amendment_ops_carries_section_context_from_new_format_s_inflected_wrapper_intro() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>2</paragrahvNr>
          <paragrahvPealkiri>Eestisse lähetatud töötajate töötingimuste seaduse § 5<sup>1</sup> muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Eestisse lähetatud töötajate töötingimuste seaduse §-s 5<sup>1</sup> tehakse järgmised muudatused:</p>
              <p><b>1)</b> lõike 1 punktist 3 jäetakse välja sõnad „arv, nende”;</p>
              <p><b>2)</b> lõike 1 punktist 4 jäetakse välja sõnad „eeldatav kestvus ning”;</p>
              <p><b>3)</b> paragrahvi täiendatakse lõigetega 7 ja 8 järgmises sõnastuses:</p>
              <p>„(7) seitsmes lõige.</p>
              <p>(8) kaheksas lõige.”.</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="Eestisse lähetatud töötajate töötingimuste seadus",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "5_1"), ("subsection", "1"), ("item", "3"))),
        (StructuralAction.TEXT_REPLACE, (("section", "5_1"), ("subsection", "1"), ("item", "4"))),
        (StructuralAction.INSERT, (("section", "5_1"), ("subsection", "7"))),
        (StructuralAction.INSERT, (("section", "5_1"), ("subsection", "8"))),
    ]


def test_parse_ee_amendment_ops_keeps_delete_and_replace_segments_distinct() -> None:
    xml = """
    <muutmisseadus xmlns="muutmisseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Käibemaksuseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p><b>18)</b> paragrahvi 27 lõike 1 teisest lausest jäetakse välja tekstiosa „ja selle lisa (edaspidi koos käibedeklaratsioon)” ja viiendas lauses asendatakse sõna „vorm” sõnaga „andmekoosseis”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </muutmisseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Käibemaksuseadus")
    target_ops = [op for op in ops if str(op.target) == "section:27/subsection:1"]

    assert len(target_ops) == 2

    ops_by_old_text = {
        str(_payload(op).attrs.get("old_text") or ""): op
        for op in target_ops
    }

    delete_op = ops_by_old_text["ja selle lisa (edaspidi koos käibedeklaratsioon)"]
    delete_meta = read_sentence_target_meta(_payload(delete_op))
    assert _payload(delete_op).text == ""
    assert delete_meta is not None
    assert delete_meta.sentence_indexes == (1,)

    replace_op = ops_by_old_text["vorm"]
    replace_meta = read_sentence_target_meta(_payload(replace_op))
    assert _payload(replace_op).text == "andmekoosseis"
    assert replace_meta is not None
    assert replace_meta.sentence_indexes == (4,)


def test_old_format_section_with_act_name_outside_bold_does_not_leak_neighbor_sections() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>\xc2\xa7 14.</b> Notariaadiseaduse (RT I 2000, 104, 684) \xc2\xa7 29 l\xc3\xb5ike 1 punkt 8 muudetakse j\xc3\xa4rgmiselt.</p>
        <p><b>\xc2\xa7 15.</b> Liiklusseaduse (RT I 2001, 3, 6) \xc2\xa7 27 l\xc3\xb5ige 5.</p>
        <p><b>\xc2\xa7 16.</b> Advokatuuriseaduse (RT I 2001, 36, 201) \xc2\xa7-d 66\xe2\x80\x9378 muudetakse j\xc3\xa4rgmiselt.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert ops == []


def test_old_format_embedded_specific_regulation_header_routes_target_section() -> None:
    xml = """
    <oigusakt xmlns="muutmismaarus_1_10.02.2010">
      <sisuTekst>
        <HTMLKonteiner><![CDATA[
          <p><b>§ 1.</b> Siseministri määruse nr 1 „Võõrmäärus” muutmine</p>
          <p><b>7)</b> määruse lisas asendatakse tekst. </p>
          <p><b>§ 2. </b><b>Siseministri 1. jaanuari 2004. a määruse „Sihtmäärus” muutmine</b></p>
          <p>Sihtmääruses tehakse järgmised muudatused:</p>
          <p><b>1)</b> paragrahvi 4 lõige 2 tunnistatakse kehtetuks;</p>
          <p><b>§ 3.</b> Siseministri määruse nr 3 „Muu määrus” muutmine</p>
          <p><b>1)</b> paragrahvi 9 lõige 1 tunnistatakse kehtetuks;</p>
        ]]></HTMLKonteiner>
      </sisuTekst>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Sihtmäärus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "4"), ("subsection", "2"))),
    ]
    assert "old_format_amendment_section:2" in ops[0].provenance_tags
    assert "old_format_amendment_item:1" in ops[0].provenance_tags


def test_old_format_html_commencement_assigns_embedded_section_item_dates() -> None:
    xml = """
    <oigusakt xmlns="muutmismaarus_1_10.02.2010">
      <sisuTekst>
        <HTMLKonteiner><![CDATA[
          <p><b>§ 1.</b> Siseministri määruse nr 1 „Võõrmäärus” muutmine</p>
          <p><b>7)</b> määruse lisas asendatakse tekst. </p>
          <p><b>§ 2. </b><b>Siseministri 1. jaanuari 2004. a määruse „Sihtmäärus” muutmine</b></p>
          <p>Sihtmääruses tehakse järgmised muudatused:</p>
          <p><b>1)</b> paragrahvi 4 lõige 2 tunnistatakse kehtetuks;</p>
          <p><b>2)</b> paragrahvi 5 lõige 1 tunnistatakse kehtetuks;</p>
          <p><b>§ 3.</b> Määruse jõustumine</p>
          <p>Määruse § 2 punkt 2 jõustub 2014. aasta 1. oktoobril.</p>
        ]]></HTMLKonteiner>
      </sisuTekst>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Sihtmäärus")
    effective_by_item = {
        next(
            tag.split(":", 1)[1]
            for tag in op.provenance_tags
            if tag.startswith("old_format_amendment_item:")
        ): (op.source.effective if op.source is not None else "")
        for op in ops
    }

    assert effective_by_item == {"1": "", "2": "2014-10-01"}


def test_extract_ee_ops_emits_structural_textosa_chapter_heading_relabel() -> None:
    ops = extract_ee_ops(
        (
            "64) määruse tekstiosa „2. peatükk RISKIDE KONTROLL” "
            "asendatakse tekstiosaga „4. peatükk RISKIDE KONTROLL”;"
        ),
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action is StructuralAction.RENUMBER
    assert op.target.path == (("chapter", "2"),)
    assert op.destination is not None
    assert op.destination.path == (("chapter", "4"),)
    assert op.payload is not None
    assert op.payload.attrs["rule_id"] == "ee_structural_textosa_heading_relabel"
    assert op.payload.attrs["old_heading"] == "RISKIDE KONTROLL"
    assert op.payload.attrs["new_heading"] == "RISKIDE KONTROLL"
    assert "ee_structural_textosa_heading_relabel" in op.provenance_tags


def test_parse_ee_amendment_ops_recovers_real_structural_textosa_chapter_heading_relabels() -> None:
    archive = open_rt_archive(readonly=True)
    base = parse_ee_statute(fetch_rt_xml("117032011030", archive))
    ops = parse_ee_amendment_ops(
        fetch_rt_xml("120122011001", archive),
        "ee/120122011001",
        target_title=base.title,
        ref_effective="2011-12-31",
    )

    relabels = [op for op in ops if op.witness_rule_id == "ee_structural_textosa_heading_relabel"]

    assert [(op.target.path, op.destination.path if op.destination is not None else ()) for op in relabels] == [
        ((("chapter", "2"),), (("chapter", "4"),)),
        ((("chapter", "3"),), (("chapter", "5"),)),
        ((("chapter", "4"),), (("chapter", "6"),)),
        ((("chapter", "5"),), (("chapter", "7"),)),
    ]


def test_muutmisseadus_untitled_omnibus_paragraph_keeps_matching_tavatekst_target() -> None:
    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>296</paragrahvNr>
          <loige>
            <sisuTekst>
              <tavatekst><b>Avaliku teenistuse seaduses</b> (RT I 1995, 16, 228; 2009, 7, 29) tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahvi 135 pealkiri ning lõiked 1 ja 2 muudetakse ning sõnastatakse järgmiselt:</p>
                <p>„<b>§ 135. Nõudeõigus õigusvastasel teenistusest vabastamisel</b></p>
                <p>(1) Õigusvastaselt teenistusest vabastatud ametnikul on õigus nõuda vabastamise kohta antud haldusakti tühistamist.</p>
                <p>(2) Kui õigusvastaselt teenistusest vabastatud ametnik loobub ennistamisest, on tal õigus nõuda vabastamise õigusvastaseks tunnistamist.”;</p>
                <p><b>2)</b> paragrahvi 160 lõige 1 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>„(1) Ametnikul on õigus esitada halduskohtumenetluse seadustikus sätestatud tingimustel ja korras halduskohtule kaebus teenistusalastes küsimustes antud haldusakti või tehtud toimingute peale.”;</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>297</paragrahvNr>
          <loige>
            <sisuTekst>
              <tavatekst><b>Haldusmenetluse seaduses</b> (RT I 2001, 58, 354; 2009, 27, 164) tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
            <sisuTekst>
              <HTMLKonteiner><![CDATA[
                <p><b>1)</b> paragrahv 87 muudetakse ja sõnastatakse järgmiselt:</p>
                <p>„<b>§ 87. Edasikaebamise õigus</b></p>
                <p>(1) Isikul on õigus pöörduda halduskohtusse.”</p>
              ]]></HTMLKonteiner>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Avaliku teenistuse seadus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "135"), ("subsection", "1"))),
        (StructuralAction.REPLACE, (("section", "135"), ("subsection", "2"))),
        (StructuralAction.REPLACE, (("section", "160"), ("subsection", "1"))),
    ]


def test_is_omnibus_amendment_detects_untitled_tavatekst_target_sections() -> None:
    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <paragrahv>
          <paragrahvNr>296</paragrahvNr>
          <loige>
            <sisuTekst>
              <tavatekst><b>Avaliku teenistuse seaduses</b> (RT I 1995, 16, 228; 2009, 7, 29) tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
        <paragrahv>
          <paragrahvNr>297</paragrahvNr>
          <loige>
            <sisuTekst>
              <tavatekst><b>Haldusmenetluse seaduses</b> (RT I 2001, 58, 354; 2009, 27, 164) tehakse järgmised muudatused:</tavatekst>
            </sisuTekst>
          </loige>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    root = ET.fromstring(xml)

    assert _is_omnibus_amendment(root, "tyviseadus_1_10.02.2010", "Avaliku teenistuse seadus") is True


def test_extract_ee_ops_strips_leading_embedded_reference_wrapper_for_inner_target() -> None:
    source = OperationSource(
        statute_id="ee/test",
        title="test",
        effective="2025-09-01",
        enacted="2025-06-18",
        raw_text="",
    )

    ops = extract_ee_ops(
        (
            "2) paragrahvi 1 punktis 11 esitatud Eesti Vabariigi haridusseaduse "
            "§ 36 6 lõike 2 5 punktis 5 asendatakse sõna „koolijuhtide” "
            "sõnadega „haridusasutuste juhtide”."
        ),
        source,
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "36_6"), ("subsection", "2_5"), ("item", "5"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "koolijuhtide"
    assert ops[0].payload.text == "haridusasutuste juhtide"


def test_extract_intro_statute_fragment_handles_section_scoped_untitled_intro() -> None:
    text = "Jälitustegevuse seaduse (RT I, 29.12.2011, 56) § 9 lõike 2 punkt 7 muudetakse ja sõnastatakse järgmiselt:"

    assert _extract_intro_statute_fragment(text) == "Jälitustegevuse seaduse"


def test_extract_intro_statute_fragment_handles_section_scoped_delete_intro() -> None:
    text = "Vabariigi Presidendi töökorra seaduse § 5 lõikest 1 jäetakse välja sõna „puhkuse,”."

    assert _extract_intro_statute_fragment(text) == "Vabariigi Presidendi töökorra seaduse § 5 lõikest 1"


def test_extract_intro_statute_fragment_handles_year_prefixed_statute_intro() -> None:
    text = "2024. aasta riigieelarve seaduses tehakse järgmised muudatused:"

    assert _extract_intro_statute_fragment(text) == "2024. aasta riigieelarve seaduses"


def test_extract_intro_statute_fragment_handles_quoted_regulation_title_intro() -> None:
    text = (
        "Rahandusministri 5. juuni 2009. a määruses nr 38 "
        "„Täiendavad juhised kauba sisenemis- ja väljumisformaalsuste teostamiseks” "
        "tehakse järgmised muudatused:"
    )

    assert (
        _extract_intro_statute_fragment(text)
        == "Täiendavad juhised kauba sisenemis- ja väljumisformaalsuste teostamiseks"
    )


def test_extract_intro_statute_fragment_handles_quoted_regulation_title_accusative_intro() -> None:
    text = (
        "Põllumajandusministri 21. aprilli 2010. a määrust nr 46 "
        "„Keskkonnasõbraliku majandamise toetuse saamise nõuded, toetuse "
        "taotlemise ja taotluse menetlemise täpsem kord” muudetakse järgmiselt:"
    )

    assert _extract_intro_statute_fragment(text) == (
        "Keskkonnasõbraliku majandamise toetuse saamise nõuded, toetuse "
        "taotlemise ja taotluse menetlemise täpsem kord"
    )


def test_extract_intro_statute_fragment_handles_left_double_quote_as_closer() -> None:
    text = (
        "Rahandusministri 20. septembri 2005. a määruses nr 64 "
        "„Kohustusliku pensionifondi osakute kord“ tehakse järgmised muudatused:"
    )

    assert _extract_intro_statute_fragment(text) == "Kohustusliku pensionifondi osakute kord"


def test_extract_intro_statute_fragment_handles_quoted_regulation_title_section_scope() -> None:
    text = (
        "Vabariigi Valitsuse 20. detsembri 2007. a määruse nr 251 "
        "„Aadressiandmete süsteem” §-d 1–5 tunnistatakse kehtetuks."
    )

    assert _extract_intro_statute_fragment(text) == "Aadressiandmete süsteem"


def test_parse_ee_amendment_ops_admits_html_only_year_prefixed_target_with_generic_paragraph_title() -> None:
    xml = """
    <oigusakt xmlns="http://www.riigiteataja.ee/ns/oigusakt/1.0">
      <sisu>
        <paragrahv>
          <paragrahvNr>3</paragrahvNr>
          <paragrahvPealkiri>Muudatused tekstiparagrahvides</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>2024. aasta riigieelarve seaduses tehakse järgmised muudatused:</p>
              <p><b>1)</b> paragrahvi 2 lõikes 5 asendatakse arv „11,69” arvuga „11,70”;</p>
              <p><b>2)</b> paragrahvi 6 lõikes 1 asendatakse arv „4 283 880” arvuga „1 206 880”;</p>
              <p><b>3)</b> paragrahvi 10 lõige 2 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„(2) Kaitseministeerium võib tema käsutuses olevate kinnistute ning vallasvara müügist laekunud vahenditest soetada Kaitseministeeriumi valitsemisalale põhitegevuseks vajalikku vara ja kaitseotstarbelisi varusid, olles kavandatavast tehingust varem teavitanud Rahandusministeeriumi.”</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(
        xml,
        "ee/test",
        target_title="2024. aasta riigieelarve seadus",
        ref_effective="2024-12-12",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "2"), ("subsection", "5"))),
        (StructuralAction.TEXT_REPLACE, (("section", "6"), ("subsection", "1"))),
        (StructuralAction.REPLACE, (("section", "10"), ("subsection", "2"))),
    ]


def test_parse_ee_amendment_ops_parses_riigieelarve_year_prefixed_corpus_source() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("111122024011", archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/111122024011",
        target_title="2024. aasta riigieelarve seadus",
        ref_effective="2024-12-12",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.META, (("section", "1"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "2"), ("subsection", "5"))),
        (StructuralAction.TEXT_REPLACE, (("section", "6"), ("subsection", "1"))),
        (StructuralAction.REPLACE, (("section", "10"), ("subsection", "2"))),
    ]


def test_old_format_roman_numeral_target_section_isolated_by_paragraph_split() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liikluskindlustuse seaduses</b> (RT I 2001, 43, 238) tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>\xc2\xa7 1.</b> Paragrahv 4 muudetakse ja s\xc3\xb5nastatakse j\xc3\xa4rgmiselt:</p>
        <p>\xc2\xab\xc2\xa7 4. Kindlustamisele kuuluv s\xc3\xb5iduk\xc2\xbb</p>
        <p><b>III. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>\xc2\xa7 44.</b> Paragrahvi 9 t\xc3\xa4iendatakse l\xc3\xb5ikega 5 j\xc3\xa4rgmises s\xc3\xb5nastuses:</p>
        <p>\xc2\xab(5) Juht peab enne s\xc3\xb5idu alustamist veenduma lepingu olemasolus.\xc2\xbb</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "9"), ("subsection", "5"))


def test_old_format_wrapper_header_seeds_section_context_for_nested_items() -> None:
    xml = b"""
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse j\xc3\xa4rgmised muudatused:</p>
        <p><b>\xc2\xa7 3.</b> Paragrahvi 20<sup>3</sup>:</p>
        <p><b>1)</b> l\xc3\xb5ige 1 muudetakse ja s\xc3\xb5nastatakse j\xc3\xa4rgmiselt:</p>
        <p>\xc2\xab(1) Juhi t\xc3\xb6\xc3\xb6aeg.\xc2\xbb</p>
        <p><b>2)</b> t\xc3\xa4iendatakse l\xc3\xb5ikega 1<sup>1</sup> j\xc3\xa4rgmises s\xc3\xb5nastuses:</p>
        <p>\xc2\xab(1<sup>1</sup>) Lisatingimus.\xc2\xbb</p>
        <p><b>\xc2\xa7 4.</b> Paragrahvi 20<sup>4</sup>:</p>
        <p><b>1)</b> t\xc3\xa4iendatakse l\xc3\xb5ikega 1<sup>1</sup> j\xc3\xa4rgmises s\xc3\xb5nastuses:</p>
        <p>\xc2\xab(1<sup>1</sup>) Teine plokk.\xc2\xbb</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert len(ops) == 3
    assert ops[0].target.path == (("section", "20_3"), ("subsection", "1"))
    assert ops[1].target.path == (("section", "20_3"), ("subsection", "1_1"))
    assert ops[2].target.path == (("section", "20_4"), ("subsection", "1_1"))


def test_old_format_wrapper_context_survives_until_later_top_level_parenthesized_items() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>§ 1. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse järgmised muudatused:</p>
        <p><b>(1)</b> Paragrahvi 17 lõige 2 tunnistatakse kehtetuks.</p>
        <p><b>(7)</b> Paragrahvi 50<sup>2</sup>:</p>
        <p><b>1)</b> lõige 1 muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« (1) Parkimisjärelevalve ametiisik teeb viivistasu määramise otsuse. »</p>
        <p><b>2)</b> lõike 4 punkt 5 muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« 5) mootorsõiduki tüüp, mark ja registreerimisnumber; »</p>
        <p><b>(8)</b> Paragrahv 73 tunnistatakse kehtetuks.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = [(op.action, op.target.path) for op in ops]

    assert (StructuralAction.REPEAL, (("section", "17"), ("subsection", "2"))) in targets
    assert (StructuralAction.REPLACE, (("section", "50_2"), ("subsection", "1"))) in targets
    assert (StructuralAction.REPLACE, (("section", "50_2"), ("subsection", "4"), ("item", "5"))) in targets
    assert (StructuralAction.REPEAL, (("section", "73"),)) in targets


def test_old_format_wrapper_clause_with_structural_dash_section_insert_prefers_payload_section() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>§ 23<sup>11</sup>.</b> Erakooliseaduse täiendamine</p>
        <p><b>11)</b> seadust täiendatakse §‑ga 46 järgmises sõnastuses:</p>
        <p>„<b>§ 46. Koolieelse lasteasutuse tegevusloa taotluste ja seni väljastatud tegevuslubade kehtivus</b></p>
        <p>Enne 2025. aasta 1. septembrit koolieelses lasteasutuses õppe läbiviimiseks esitatud tegevusloa taotlusi menetletakse taotluse esitamise ajal kehtinud tingimustel ja korras.</p>
        <p>”.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Erakooliseadus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "46"),)),
    ]


def test_old_format_plain_wrapper_clause_preserves_header_context_for_whole_block_ops() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse järgmised muudatused:</p>
        <p><b>§ 2.</b> Seadust täiendatakse §-ga 18<sup>1</sup> järgmises sõnastuses:</p>
        <p>« <b>§ 18<sup>1</sup>. Mootorsõidukijuhi ja sõitja kohustused</b></p>
        <p>(1) Uus tekst.</p>
        <p>»</p>
        <p><b>§ 6.</b> Paragrahvi 30 täiendatakse lõikega 6 järgmises sõnastuses:</p>
        <p>« (6) Täiendav lõige. »</p>
        <p><b>§ 11.</b> Paragrahv 74<sup>44</sup> muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« <b>§ 74<sup>44</sup>. Mootorsõidukijuhile kehtestatud ööpäevase sõiduaja nõuete rikkumine</b></p>
        <p>(1) Esimene lõige.</p>
        <p>(2) Teine lõige. »</p>
        <p><b>§ 14.</b> Seadust täiendatakse §-dega 74<sup>58</sup>–74<sup>59</sup> järgmises sõnastuses:</p>
        <p>« <b>§ 74<sup>58</sup>. A</b></p>
        <p>A tekst.</p>
        <p><b>§ 74<sup>59</sup>. B</b></p>
        <p>B tekst. »</p>
        <p><b>§ 16.</b> Seaduse normitehniline märkus muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« 1 Euroopa Parlamendi ja nõukogu direktiiv. »</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = {(op.action, op.target.path) for op in ops}

    assert (StructuralAction.INSERT, (("section", "18_1"),)) in targets
    assert (StructuralAction.INSERT, (("section", "30"), ("subsection", "6"))) in targets
    assert (StructuralAction.REPLACE, (("section", "74_44"),)) in targets
    assert (StructuralAction.INSERT, (("section", "74_58"),)) in targets
    assert (StructuralAction.INSERT, (("section", "74_59"),)) in targets
    assert all(op.target.path != (("section", "16"),) for op in ops)


def test_extract_ee_ops_keeps_normitehniline_markus_visible_without_fabricating_section_target() -> None:
    ops = extract_ee_ops(
        "seaduse normitehnilist märkust täiendatakse tekstiosaga „Euroopa Parlamendi ja nõukogu direktiiv.”;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )

    assert len(ops) == 1
    assert ops[0].target.path == ()
    assert "normitehniline_markus" in ops[0].provenance_tags


def test_parse_ee_amendment_ops_decodes_old_format_numeric_quote_entities_in_wrapperless_text_replace() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>§ 2. Advokatuuriseaduse muutmine</b> Advokatuuriseaduse § 45 lõikes 4<sup>1</sup> ja § 46 lõikes 3 asendatakse sõna &#750;Justiitsministeerium&#750; sõnadega &#750;maksejõuetuse teenistus&#750; vastavas käändes.</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Advokatuuriseadus")

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "45"), ("subsection", "4_1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "46"), ("subsection", "3"))),
    ]
    assert all(op.payload is not None for op in ops)
    assert all(_payload(op).attrs["old_text"] == "Justiitsministeerium" for op in ops)
    assert all(_payload(op).text == "maksejõuetuse teenistus" for op in ops)


def test_old_format_payload_cross_reference_does_not_override_wrapper_section_context() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse järgmised muudatused:</p>
        <p><b>§ 4.</b> Paragrahvi 20<sup>4</sup>:</p>
        <p><b>1)</b> täiendatakse lõikega 1<sup>1</sup> järgmises sõnastuses:</p>
        <p>« (1<sup>1</sup>) Teine plokk. »;</p>
        <p><b>2)</b> lõike 2 punkt 2 muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« 2) mida kasutatakse käesoleva seaduse § 20<sup>3</sup> lõike 8 alusel. »</p>
        <p><b>§ 9.</b> Paragrahvi 46<sup>1</sup>:</p>
        <p><b>4)</b> täiendatakse lõigetega 3<sup>1</sup> ja 3<sup>2</sup> järgmises sõnastuses:</p>
        <p>« (3<sup>1</sup>) Esimene uus lõige, mis viitab käesoleva seaduse § 20<sup>3</sup> lõigetele.</p>
        <p>(3<sup>2</sup>) Teine uus lõige. »</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = [op.target.path for op in ops]

    assert (("section", "20_4"), ("subsection", "2"), ("item", "2")) in targets
    assert (("section", "46_1"), ("subsection", "3_1")) in targets
    assert (("section", "46_1"), ("subsection", "3_2")) in targets


def test_old_format_payload_verb_does_not_displace_wrapper_instruction() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse järgmised muudatused:</p>
        <p><b>§ 4.</b> Seadust täiendatakse 4<sup>1</sup>. peatükiga järgmises sõnastuses:</p>
        <p>«4<sup>1</sup>. peatükk JUHI TÖÖ- JA PUHKEAEG</p>
        <p><b>§ 20<sup>3</sup>. Erinõuded juhi töö- ja puhkeajale</b></p>
        <p>(1) Käesolevas peatükis sätestatakse kohustused, mida ei tunnistatakse kehtetuks.</p>
        <p>»</p>
        <p><b>§ 20.</b> Paragrahvi 72 lõige 5 muudetakse ja sõnastatakse järgmiselt:</p>
        <p>«(5) Trammi juhtimise õigus võetakse ära, tunnistatakse kehtetuks ja taastatakse seadusega sätestatu kohaselt.»</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    targets = {(op.action, op.target.path) for op in ops}

    assert (StructuralAction.INSERT, (("chapter", "4_1"),)) in targets
    assert (StructuralAction.REPLACE, (("section", "72"), ("subsection", "5"))) in targets


def test_old_format_split_wrapper_header_recombines_following_quoted_payload() -> None:
    xml = """
    <tyviseadus xmlns="tyviseadus_1_10.02.2010">
      <HTMLKonteiner><![CDATA[
        <p><b>I. Liiklusseaduses</b> (RT I 2001, 3, 6) tehakse järgmised muudatused:</p>
        <p><b>§ 6.</b> Paragrahvi 62 lõige 1 muudetakse ja sõnastatakse järgmiselt:</p>
        <p>« (1) Liiklusregister on uus tekst. »</p>
        <p><b>§ 7.</b> Paragrahvi 65 täiendatakse lõikega 4 järgmises sõnastuses:</p>
        <p>« (4) Andmeid edastada on lubatud. »</p>
      ]]></HTMLKonteiner>
    </tyviseadus>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Liiklusseadus")

    assert len(ops) == 2
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "62"), ("subsection", "1"))
    assert ops[0].payload is not None
    assert ops[0].payload.text == "(1) Liiklusregister on uus tekst."
    assert ops[1].action is StructuralAction.INSERT
    assert ops[1].target.path == (("section", "65"), ("subsection", "4"))
    assert ops[1].payload is not None
    assert ops[1].payload.text == "(4) Andmeid edastada on lubatud."


def test_extract_ee_ops_keeps_single_explicit_subsection_target_in_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 65 lõikes 2 asendatakse tekstiosa "
            "„§-des 28 1 , 42, 113 2 , 401 2 , 406 2 , 419 2 , 419 3 , 710 1 , 721 1 –721 4 ja § 721 5 lõikes 1 ning § 725 lõikes 9” "
            "tekstiosaga "
            "„§-des 28 1 , 42, 113 2 , 401 2 , 406 2 , 419 2 , 419 3 ja § 725 lõikes 9”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "65"), ("subsection", "2"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.attrs.get("old_text") == (
        "§-des 28 1 , 42, 113 2 , 401 2 , 406 2 , 419 2 , 419 3 , 710 1 , "
        "721 1 –721 4 ja § 721 5 lõikes 1 ning § 725 lõikes 9"
    )


def test_extract_ee_ops_supports_fourth_sentence_subsection_repeal() -> None:
    ops = extract_ee_ops(
        "paragrahvi 4 lõike 6 neljas lause tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "4"), ("subsection", "6"))),
    ]
    assert ops[0].payload is not None
    assert ops[0].payload.text == ""
    assert any("neljas lause" in note for note in ops[0].provenance_tags)


def test_extract_ee_ops_expands_same_base_superscript_section_ranges() -> None:
    ops = extract_ee_ops(
        "paragrahvid 72 1 –72 3 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "72_1"),)),
        (StructuralAction.REPEAL, (("section", "72_2"),)),
        (StructuralAction.REPEAL, (("section", "72_3"),)),
    ]


def test_extract_ee_ops_inserts_space_for_after_number_text_replacements() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 47 lõiget 3, § 51 lõikeid 1 ja 2 ning lõike 5 punkti 1 "
            "ning § 54 lõiget 1 täiendatakse pärast arvu „44” tekstiosaga „lõike 1”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert all(op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert any(op.target.path == (("section", "47"), ("subsection", "3")) for op in ops)
    assert all(op.payload is not None for op in ops)
    assert all(op.payload.attrs.get("old_text") == "44" for op in ops if op.payload is not None)
    assert all(op.payload.text == "44 lõike 1" for op in ops if op.payload is not None)


def test_extract_ee_ops_recovers_same_section_partitive_plural_subsection_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 47 lõiget 3, § 51 lõikeid 1 ja 2 ning lõike 5 punkti 1 "
            "ning § 54 lõiget 1 täiendatakse pärast arvu „44” tekstiosaga „lõike 1”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert {(op.action, op.target.path) for op in ops} == {
        (StructuralAction.TEXT_REPLACE, (("section", "47"), ("subsection", "3"))),
        (StructuralAction.TEXT_REPLACE, (("section", "51"), ("subsection", "1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "51"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "51"), ("subsection", "5"), ("item", "1"))),
        (StructuralAction.TEXT_REPLACE, (("section", "54"), ("subsection", "1"))),
    }
    assert all(op.payload is not None and op.payload.text == "44 lõike 1" for op in ops)


def test_parse_ee_amendment_ops_does_not_truncate_target_block_after_first_quoted_multi_section_replace() -> None:
    xml = """
    <oigusakt xmlns="muutmisseadus_1_10.02.2010">
      <aktinimi><nimi><pealkiri>Testseaduse muutmise seadus</pealkiri></nimi></aktinimi>
      <sisu>
        <paragrahv>
          <paragrahvNr>1</paragrahvNr>
          <paragrahvPealkiri>Testseaduse muutmine</paragrahvPealkiri>
          <sisuTekst>
            <HTMLKonteiner><![CDATA[
              <p>Testseaduses tehakse järgmised muudatused:</p>
              <p><b>1)</b> paragrahvid 12 ja 13 muudetakse ning sõnastatakse järgmiselt:</p>
              <p>„<b>§ 12. Registreerimise üldised alused</b></p>
              <p>(1) Esimene tekst.</p>
              <p><b>§ 13. Registreerimise taotlemine</b></p>
              <p>(1) Teine tekst.”;</p>
              <p><b>2)</b> paragrahv 14 tunnistatakse kehtetuks;</p>
              <p><b>3)</b> paragrahvid 15 ja 16 muudetakse ning sõnastatakse järgmiselt:</p>
              <p>„<b>§ 15. Registreerimine</b></p>
              <p>(1) Kolmas tekst.</p>
              <p><b>§ 16. Registreerimisest keeldumine</b></p>
              <p>(1) Neljas tekst.”;</p>
              <p><b>4)</b> paragrahvid 17 ja 18 tunnistatakse kehtetuks;</p>
              <p><b>5)</b> paragrahv 19 muudetakse ja sõnastatakse järgmiselt:</p>
              <p>„<b>§ 19. Registreeringu muutmine</b></p>
              <p>(1) Viies tekst.”;</p>
              <p><b>6)</b> seadust täiendatakse §-ga 20<sup>1</sup> järgmises sõnastuses:</p>
              <p>„<b>§ 20<sup>1</sup>. Registreeringu kehtivusaeg</b></p>
              <p>(1) Kuues tekst.”;</p>
            ]]></HTMLKonteiner>
          </sisuTekst>
        </paragrahv>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Testseadus")

    assert {
        (StructuralAction.REPLACE, (("section", "12"),)),
        (StructuralAction.REPLACE, (("section", "13"),)),
        (StructuralAction.REPEAL, (("section", "14"),)),
        (StructuralAction.REPLACE, (("section", "15"),)),
        (StructuralAction.REPLACE, (("section", "16"),)),
        (StructuralAction.REPEAL, (("section", "17"),)),
        (StructuralAction.REPEAL, (("section", "18"),)),
        (StructuralAction.REPLACE, (("section", "19"),)),
        (StructuralAction.INSERT, (("section", "20_1"),)),
    }.issubset({(op.action, op.target.path) for op in ops})


def test_extract_ee_ops_fans_out_mixed_section_and_direct_item_text_replace_targets() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 12, § 13 punkte 2–4 ja § 14 punkti 3 täiendatakse pärast sõna "
            "ˮsätestatudˮ tekstiosaga ˮvõi § 7 1 lõike 6 alusel kehtestatudˮ;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert {(op.action, op.target.path) for op in ops} == {
        (StructuralAction.TEXT_REPLACE, (("section", "12"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "13"), ("item", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "13"), ("item", "3"))),
        (StructuralAction.TEXT_REPLACE, (("section", "13"), ("item", "4"))),
        (StructuralAction.TEXT_REPLACE, (("section", "14"), ("item", "3"))),
    }


def test_extract_ee_ops_preserves_nested_closing_quote_in_unbalanced_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 95 tekst sõnastatakse järgmiselt: "
            "„E-portaalis valitakse päritoluametiks Eesti või vormi MM2 "
            "andmeväljale 1 märgitakse „Estonia“;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "95"),)
    assert ops[0].payload is not None
    assert ops[0].payload.text.endswith("„Estonia“")


def test_extract_ee_ops_keeps_whole_section_body_references_out_of_wrapper_recursion() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 8 tekst sõnastatakse järgmiselt: "
            "„(1) PRIA otsustab toetuse vähendamise komisjoni määruses, "
            "millega täiendatakse Euroopa Parlamendi ja nõukogu määrust, "
            "sätestatud alustel. (2) Kui on rikutud §-s 4 sätestatud nõuet, "
            "vähendab PRIA toetust: 1) kuni 60%, kui rikkumine toimus enne tähtaega; "
            "3) kuni 40%, kui rikkumine oli korduv.”;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "8"),)
    assert ops[0].payload is not None
    assert "§-s 4 sätestatud nõuet" in ops[0].payload.text
    assert "1) kuni 60%" in ops[0].payload.text


def test_extract_ee_ops_splits_mixed_multi_section_replace_payload() -> None:
    ops = extract_ee_ops(
        (
            "paragrahv 107 ning § 108 pealkiri ja lõige 1 sõnastatakse järgmiselt: "
            "„§ 107. Kinnitamine ja allakirjutamine päritoluameti poolt\x01 "
            "Vormi MM2 kasutamise puhul lisab Patendiamet taotlusele oma kinnituse "
            "ja allkirja. E-taotluse puhul on päritoluameti kinnitus elektrooniline. "
            "§ 108. Lõivude arvestus\x01 (1) Vormi MM2 kasutamise puhul peab lõivude "
            "arvestuse lehel olema märgitud luba.“;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path, op.target.special) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "107"),), None),
        (StructuralAction.REPLACE, (("section", "108"), ("subsection", "1")), None),
    ]
    assert all(op.witness_rule_id == "ee_mixed_multi_section_replace_payload_split" for op in ops)
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("§ 107.")
    assert ops[1].payload is not None
    assert ops[1].payload.text.startswith("§ 108.")


def test_extract_ee_ops_expands_plural_subsection_range_with_minus_sign() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 101 lõiked 3−5 sõnastatakse järgmiselt: "
            "„(3) Kolmas. (4) Neljas. (5) Viies.“;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPLACE, (("section", "101"), ("subsection", "3"))),
        (StructuralAction.REPLACE, (("section", "101"), ("subsection", "4"))),
        (StructuralAction.REPLACE, (("section", "101"), ("subsection", "5"))),
    ]
    assert [op.payload.text for op in ops if op.payload is not None] == [
        "(3) Kolmas.",
        "(4) Neljas.",
        "(5) Viies.",
    ]


def test_old_format_wrapper_split_keeps_quoted_embedded_section_payload_together() -> None:
    xml = """
    <oigusakt xmlns="tyviseadus_1_10.02.2010">
      <sisu>
        <sisuTekst>
          <HTMLKonteiner><![CDATA[
            <p><strong>§ 5.</strong> Justiitsministri määruse nr 12 „Kaubamärgimäärus“ muutmine</p>
            <p><strong>45)</strong> paragrahv 107 ning § 108 pealkiri ja lõige 1 sõnastatakse järgmiselt:</p>
            <p>„<strong>§ 107. Kinnitamine ja allakirjutamine päritoluameti poolt</strong></p>
            <p>Vormi MM2 kasutamise puhul lisab Patendiamet taotlusele oma kinnituse ja allkirja.</p>
            <p><strong>§ 108. Lõivude arvestus</strong></p>
            <p>(1) Vormi MM2 kasutamise puhul peab lõivude arvestuse lehel olema märgitud luba.“;</p>
            <p><strong>46)</strong> paragrahvi 108 lõiget 2 täiendatakse enne sõnu „Lõivude arvestuse lehele“ sõnadega „Vormi MM2“;</p>
          ]]></HTMLKonteiner>
        </sisuTekst>
      </sisu>
    </oigusakt>
    """.encode("utf-8")

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title="Kaubamärgimäärus")

    targets = [(op.action, op.target.path, op.witness_rule_id) for op in ops]
    assert (
        StructuralAction.REPLACE,
        (("section", "107"),),
        "ee_mixed_multi_section_replace_payload_split",
    ) in targets
    assert (
        StructuralAction.REPLACE,
        (("section", "108"), ("subsection", "1")),
        "ee_mixed_multi_section_replace_payload_split",
    ) in targets


def test_old_format_commencement_scan_ignores_plain_joustumisest_body_text() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("13310847", archive=archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/13310847",
        target_title="Ringhäälinguseadus",
        ref_effective="2011-01-01",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "43_4"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "43_5"),)),
    ]


def test_old_format_right_quote_payload_preserves_inner_right_quoted_titles() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("121052014003", archive=archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/121052014003",
        target_title="Euroopa Liidu ühise põllumajanduspoliitika rakendamise seadus",
    )

    section_116_3 = next(op for op in ops if op.target.path == (("section", "116_3"),))
    assert section_116_3.payload is not None
    assert section_116_3.action == StructuralAction.INSERT
    assert section_116_3.payload.text.count("Eesti maaelu arengukava 2014–2020") == 2


def test_quoted_regulation_title_direct_target_paragraph_repeals_mixed_section_groups() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("113102015002", archive=archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/113102015002",
        target_title="Aadressiandmete süsteem",
    )

    targets = [(op.action, op.target.path) for op in ops]
    assert (StructuralAction.REPEAL, (("section", "1"),)) in targets
    assert (StructuralAction.REPEAL, (("section", "6"), ("subsection", "10"))) in targets
    assert (StructuralAction.REPEAL, (("section", "11"), ("subsection", "14"))) in targets
    assert (StructuralAction.REPEAL, (("section", "18"),)) in targets


def test_extract_ee_ops_recovers_flat_sectionless_singleton_item_insert() -> None:
    text = (
        "Põllumajandusministri 5. jaanuari 2011. a määrust nr 1 "
        "“2011. aastal toetatavad “Euroopa Kalandusfondi 2007-2013 rakenduskava” "
        "meetmed ja tegevuste liigid” (RT I, 15.03.2011, 10) täiendatakse "
        "punktiga 12 järgmises sõnastuses: "
        "“12) meede 3.1 “Ühistegevused” tegevus “Ühisinvesteeringud”.“"
    )

    ops = extract_ee_ops(
        text,
        OperationSource(statute_id="ee/101092011002", raw_text=text, effective="2011-09-04"),
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.INSERT
    assert op.target.path == (("section", "1"), ("subsection", "1"), ("item", "12"))
    assert op.payload is not None
    assert op.payload.text == "meede 3.1 “Ühistegevused” tegevus “Ühisinvesteeringud”."
    assert op.witness_rule_id == "ee_flat_sectionless_singleton_item_insert"
    assert op.payload.attrs["scope_confidence"] == "inferred_from_live_unique"


def test_extract_ee_ops_keeps_nested_quote_tail_in_insert_after_textosa() -> None:
    text = (
        "paragrahvi 1 täiendatakse pärast sõnu “tunnistamise kord” tekstiosaga "
        "“ning enne töö, teenuse või vara soetamise eest tasumist toetatava tegevuse "
        "elluviimise riigieelarvelistest vahenditest rahastamise taotlemise ja taotluse "
        "menetlemise kord ning taotluse vorm kooskõlas nõukogu määruse (EÜ) nr 1698/2005 "
        "Maaelu Arengu Euroopa Põllumajandusfondist (EAFRD) antavate maaelu arengu toetuste "
        "kohta (ELT L 277, 21.10.2005, lk 1–40) artikli 18 lõike 4 alusel heaks kiidetud "
        "“Eesti maaelu arengukavaga 2007–2013” (edaspidi arengukava)”;"
    )

    ops = extract_ee_ops(
        text,
        OperationSource(statute_id="ee/104092012001", raw_text=text, effective="2012-09-07"),
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.TEXT_REPLACE
    assert op.target.path == (("section", "1"),)
    assert op.payload is not None
    assert op.payload.attrs["old_text"] == "tunnistamise kord"
    assert op.payload.text.endswith("“Eesti maaelu arengukavaga 2007–2013” (edaspidi arengukava)")
    assert op.text_patch is not None
    assert op.text_patch.replacement is not None
    assert op.text_patch.replacement.endswith("“Eesti maaelu arengukavaga 2007–2013” (edaspidi arengukava)")


def test_parse_ee_statute_canonicalizes_singleton_empty_section_label_for_2011_010() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("115032011010", archive=archive)

    statute = parse_ee_statute(xml, "ee/115032011010")

    assert len(statute.body.children) == 1
    section = statute.body.children[0]
    assert section.kind == IRNodeKind.SECTION
    assert section.label == "1"
    assert section.attrs["source_cleanup_rules"] == ("ee_singleton_empty_section_label_to_1",)
    assert section.attrs["source_empty_section_label"] == ""


def test_quoted_ministerial_target_intro_admits_direct_html_items() -> None:
    archive = open_rt_archive(readonly=True)
    xml = fetch_rt_xml("121042016001", archive=archive)

    ops = parse_ee_amendment_ops(
        xml,
        "ee/121042016001",
        target_title="Täiendavad juhised kauba sisenemis- ja väljumisformaalsuste teostamiseks",
    )

    targets = [(op.action, op.target.path) for op in ops]
    assert (StructuralAction.TEXT_REPLACE, ()) in targets
    assert (StructuralAction.REPEAL, (("section", "18"),)) in targets
    assert (StructuralAction.INSERT, (("section", "31_1"),)) in targets


def test_extract_ee_ops_stops_at_quote_prime_payload_for_direct_item_text_replace() -> None:
    ops = extract_ee_ops(
        (
            "paragrahvi 14 punkti 7 täiendatakse pärast sõna ˮsätestatudˮ "
            "tekstiosaga ˮvõi § 7 1 lõike 6 alusel kehtestatudˮ;"
        ),
        OperationSource(statute_id="ee/test"),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "14"), ("item", "7"))),
    ]


def test_extract_ee_ops_normalizes_unicode_superscript_section_targets() -> None:
    text = (
        "36) paragrahvi 91 lõike 2 esimesest lausest ja § 110¹ tekstist jäetakse välja "
        "sõnad „, Euroopa Majanduspiirkonna liikmesriigi ja Šveitsi Konföderatsiooni "
        "kodanik” vastavas käändes;"
    )

    ops = extract_ee_ops(
        text,
        OperationSource(statute_id="ee/test", raw_text=text),
    )

    assert {str(op.target) for op in ops} == {
        "section:91/subsection:2",
        "section:110_1",
    }
    assert all(_payload(op).attrs.get("case_inflected") is True for op in ops)


def test_extract_ee_ops_keeps_mixed_subsection_and_sentence_repeals_distinct() -> None:
    text = (
        "paragrahvi 8 lõiked 3 ja 3 1 ning § 9 lõike 2 neljas lause "
        "tunnistatakse kehtetuks;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, str(op.target)) for op in ops] == [
        (StructuralAction.REPEAL, "section:8/subsection:3"),
        (StructuralAction.REPEAL, "section:8/subsection:3_1"),
        (StructuralAction.REPLACE, "section:9/subsection:2"),
    ]
    selection_meta = read_subsection_selection_meta(_payload(ops[0]))
    assert selection_meta is not None
    assert selection_meta.explicit_labels == ("3", "3_1")
    sentence_meta = read_sentence_target_meta(_payload(ops[2]))
    assert sentence_meta is not None
    assert sentence_meta.sentence_indexes == (3,)


def test_extract_ee_ops_ignores_premarker_title_quote_for_section_text_replace() -> None:
    text = (
        "§ 1 tekst sõnastatakse järgmiselt: "
        "Vabariigi Valitsuse 20. jaanuari 2022. a määruse nr 10 "
        "„Väikesaarte nimistu” § 1 tekst sõnastatakse järgmiselt: "
        "„Väikesaarte nimistus on Abruka saar ja Vormsi saar.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "1"),)
    assert _payload(ops[0]).text == "Väikesaarte nimistus on Abruka saar ja Vormsi saar."
    assert (
        _payload(ops[0]).attrs["source_family"]
        == "ee_payload_after_marker_ignores_premarker_title_quote"
    )


def test_extract_ee_ops_ignores_premarker_title_quote_for_item_replace() -> None:
    text = (
        "§ 4 lõike 1 punkt 1 sõnastatakse järgmiselt: "
        "Kultuuriministri 8. detsembri 2022. a määruse nr 20 "
        "„Eesti sõltumatu erameedia toetamise tingimused ja kord” "
        "§ 4 lõike 1 punkt 1 sõnastatakse järgmiselt: "
        "„1) kes annab välja eesti- ja venekeelseid meediaväljaandeid.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].target.path == (("section", "4"), ("subsection", "1"), ("item", "1"))
    assert _payload(ops[0]).text == "1) kes annab välja eesti- ja venekeelseid meediaväljaandeid."
    assert (
        _payload(ops[0]).attrs["source_family"]
        == "ee_payload_after_marker_ignores_premarker_title_quote"
    )


def test_parse_html_op_items_preserves_table_cells_without_paragraph_wrappers() -> None:
    html = (
        "<p><b>1)</b> paragrahvi 7 lõige 1 sõnastatakse järgmiselt:</p>"
        "<p>„(1) Liigitus on järgmine:</p>"
        "<table><tr><td>T1</td><td>I tasand</td><td>T2</td><td>II tasand</td></tr>"
        "<tr><td><p>1</p></td><td><p>Territoriaalüksused</p></td></tr></table>"
        "<p><b>2)</b> paragrahvi 7 lõikes 2 asendatakse sõna „volitatud” sõnaga „vastutaval”.</p>"
    )

    items = parse_html_op_items(html, allow_plain_paragraph_items=True)

    assert len(items) == 2
    assert "T1 I tasand T2 II tasand" in items[0]
    assert "1 Territoriaalüksused" in items[0]


def test_parse_ee_amendment_ops_prefers_richer_old_format_table_payload_with_target_title() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("122112024001", archive),
        "ee/122112024001",
        target_title="Kohanime vormistamise ja kasutamise kord",
    )

    replace_op = next(
        op
        for op in ops
        if op.action == StructuralAction.REPLACE
        and op.target.path == (("section", "7"), ("subsection", "1"))
    )
    assert "T1 I tasand T2 II tasand" in _payload(replace_op).text
    assert "ee_old_format_html_section_richer_payload_preferred" in replace_op.provenance_tags


def test_parse_ee_statute_materializes_numbered_html_table_rows_as_items() -> None:
    archive = open_rt_archive(readonly=True)

    statute = parse_ee_statute(fetch_rt_xml("120022014005", archive), "ee/120022014005")
    section = next(node for node in statute.body.children if node.kind == IRNodeKind.SECTION and node.label == "1")
    subsection = next(node for node in section.children if node.kind == IRNodeKind.SUBSECTION and node.label == "1")

    assert subsection.text == (
        "Universaalse postiteenuse makse määrad rahastamiskohustusega "
        "postiteenuse osutajale kehtestatakse järgmiselt:"
    )
    assert subsection.attrs["source_cleanup_rules"] == ("ee_html_table_numbered_items_materialized",)
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "lihtsaadetisena edastatav kirisaadetis 0,08 eurot;"),
        ("2", "tähtsaadetisena edastatav kirisaadetis 0,40 eurot;"),
        ("3", "väärtsaadetisena edastatav kirisaadetis 0,40 eurot;"),
        ("4", "lihtsaadetisena edastatav postipakk 0 eurot;"),
        ("5", "tähtsaadetisena edastatav postipakk 0 eurot;"),
        ("6", "väärtsaadetisena edastatav postipakk 0 eurot."),
    ]
    assert all(
        item.attrs["source_cleanup_rule"] == "ee_html_table_numbered_items_materialized"
        for item in subsection.children
    )


def test_parse_ee_amendment_ops_uses_direct_old_format_header_target_section() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("125072012002", archive),
        "ee/125072012002",
        target_title=(
            "Eesti Haigekassas kindlustuskaitse tekkimiseks, lõppemiseks ja "
            "peatumiseks vajalike dokumentide loetelu ning nendes sisalduvate "
            "andmete koosseis"
        ),
    )

    assert len(ops) == 1
    op = ops[0]
    assert op.action == StructuralAction.REPLACE
    assert op.target.path == (("section", "4"),)
    assert op.witness_rule_id == "ee_old_format_direct_header_target_section"
    assert "ee_old_format_direct_header_target_section" in op.provenance_tags
    assert "old_format_amendment_section:1" in op.provenance_tags
    assert _payload(op).text.endswith("kindlustatavate isikute nimekiri.")
    assert not _payload(op).text.endswith('nimekiri."')


def test_parse_ee_amendment_ops_splits_plaintext_preamble_and_repeal_range() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128012017002", archive),
        "ee/128012017002",
        target_title=(
            "Elamisloa ja elamisõiguse menetluses ning isikut tõendava "
            "dokumendi väljaandmise menetluses sõrmejälgede võtmise kord"
        ),
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.META, ()),
        (StructuralAction.REPEAL, (("section", "1"),)),
        (StructuralAction.REPEAL, (("section", "2"),)),
        (StructuralAction.REPEAL, (("section", "3"),)),
        (StructuralAction.REPEAL, (("section", "4"),)),
        (StructuralAction.REPEAL, (("section", "6"),)),
    ]
    assert ops[0].witness_rule_id == "ee_preamble_clause_non_body"
    assert _payload(ops[0]).attrs["source_family"] == "ee_preamble_clause_non_body"


def test_parse_ee_amendment_ops_slices_parenthesized_multi_regulation_block() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("128032025001", archive),
        "ee/128032025001",
        target_title="Lendorava püsielupaikade kaitse alla võtmine ja kaitse-eeskiri",
    )

    targets = {(op.action, op.target.path) for op in ops}
    assert (StructuralAction.INSERT, (("section", "1"), ("subsection", "2"))) in targets
    assert (StructuralAction.INSERT, (("section", "2"), ("subsection", "1"))) in targets
    assert (StructuralAction.REPLACE, (("section", "2"), ("subsection", "2_2"), ("item", "1"))) in targets
    assert (StructuralAction.TEXT_REPLACE, (("section", "4"), ("subsection", "9"))) in targets
    assert (StructuralAction.INSERT, (("section", "5"),)) in targets
    assert any(
        op.witness_rule_id == "ee_parenthesized_target_html_block_sliced"
        for op in ops
        if op.action is not StructuralAction.META
    )
    assert any(
        _payload(op).attrs.get("source_family") == "ee_out_of_body_appendix_or_note_clause"
        for op in ops
        if op.action is StructuralAction.META
    )


def test_extract_subsection_text_does_not_split_habitat_type_codes() -> None:
    payload = (
        "(2) Kuuse-Jaani püsielupaigas elupaigatüüpide lamminiidud (6450), "
        "vanad loodusmetsad (9010*) ja rohundirikkad kuusikud (9050) kaitse, "
        "Palasi püsielupaigas elupaigatüüpide vanad loodusmetsad (9010*), "
        "soo-lehtmetsad (9080*) ja rohundirikkad kuusikud (9050) kaitse."
    )

    extracted = _extract_subsection_text(payload, "2")

    assert "rohundirikkad kuusikud (9050) kaitse, Palasi" in extracted
    assert extracted.endswith("rohundirikkad kuusikud (9050) kaitse.")


def test_parse_ee_amendment_ops_does_not_smuggle_target_act_header_into_items() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("126102023005", archive),
        "ee/126102023005",
        target_title="Sigade Aafrika katku ennetamise ja tõrje täpsemad meetmed",
    )

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "1"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "2"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "2"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "2"),)),
        (StructuralAction.REPLACE, (("section", "2"), ("item", "1"))),
        (StructuralAction.INSERT, (("section", "2"), ("item", "1_1"))),
        (StructuralAction.INSERT, (("section", "2"), ("subsection", "2"))),
        (StructuralAction.TEXT_REPLACE, (("section", "3"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "4"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "5"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "6"),)),
    ]
    assert all(op.target.path != (("section", "1"),) or op.action is StructuralAction.TEXT_REPLACE for op in ops)
    assert any(
        "ee_new_format_target_act_header_not_wrapper_instruction" in op.provenance_tags
        for op in ops
    )


def test_extract_ee_ops_splits_section_heading_and_text_replace_scope() -> None:
    text = (
        "paragrahvi 2 pealkirjas ning tekstis asendatakse sõna "
        "„ettevõte” sõnaga „loomapidamisettevõte” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, op.target.special) for op in ops] == [
        ((("section", "2"),), FacetKind.HEADING),
        ((("section", "2"),), None),
    ]
    assert ops[1].witness_rule_id == "ee_section_heading_and_text_replace_split"


def test_extract_ee_ops_recovers_flat_sectionless_singleton_item_repeals() -> None:
    text = "määruse punktid 2, 4, 15 ja 16 tunnistatakse kehtetuks;"

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.REPEAL, (("section", "1"), ("subsection", "1"), ("item", "2"))),
        (StructuralAction.REPEAL, (("section", "1"), ("subsection", "1"), ("item", "4"))),
        (StructuralAction.REPEAL, (("section", "1"), ("subsection", "1"), ("item", "15"))),
        (StructuralAction.REPEAL, (("section", "1"), ("subsection", "1"), ("item", "16"))),
    ]
    assert all(op.witness_rule_id == "ee_flat_sectionless_singleton_item_repeal" for op in ops)


def test_extract_ee_ops_recovers_flat_sectionless_singleton_subsection_item_scope() -> None:
    prefixed_text = "määruse lõike 3 punktis 3 asendatakse sõna „vana” sõnaga „uus”;"

    prefixed_ops = extract_ee_ops(prefixed_text, OperationSource(statute_id="ee/test", raw_text=prefixed_text))

    assert [(op.action, op.target.path) for op in prefixed_ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "1"), ("subsection", "3"), ("item", "3"))),
    ]
    assert prefixed_ops[0].witness_rule_id == "ee_flat_sectionless_singleton_subsection_scope"


def test_extract_ee_ops_handles_plural_elative_section_text_replace() -> None:
    text = 'paragrahvidest 9 ja 12 jäetakse läbivalt välja tekstiosa „, elektrišokirelva”;'

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.TEXT_REPLACE, (("section", "9"),)),
        (StructuralAction.TEXT_REPLACE, (("section", "12"),)),
    ]
    assert [op.payload.text for op in ops if op.payload is not None] == [
        ", elektrišokirelva",
        ", elektrišokirelva",
    ]


def test_parse_ee_amendment_ops_does_not_carry_section_into_appendix_clauses() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("119032026009", archive),
        "ee/119032026009",
        target_title="Metsise püsielupaikade kaitse alla võtmine",
    )

    appendix_ops = [
        op
        for op in ops
        if any("määruse lisa" in tag for tag in op.provenance_tags)
    ]

    assert appendix_ops
    assert not any(op.target.path == (("section", "2"),) and op.action is not StructuralAction.META for op in appendix_ops)

    bare_appendix_ops = parse_ee_amendment_ops(
        fetch_rt_xml("101062021005", archive),
        "ee/101062021005",
        target_title="Must-toonekure ja suur-konnakotka püsielupaikade kaitse alla võtmine ja kaitse-eeskiri",
    )
    assert not any(
        op.target.path == (("section", "5"),)
        and any("lisas esitatud Koidula" in tag for tag in op.provenance_tags)
        for op in bare_appendix_ops
    )

    old_format_appendix_ops = parse_ee_amendment_ops(
        fetch_rt_xml("106072023001", archive),
        "ee/106072023001",
        target_title="Testide andmekogu asutamine ja põhimäärus",
    )
    assert not any(
        op.target.path == (("section", "16"),)
        and op.target.special is FacetKind.HEADING
        and any("määruse senise lisa" in tag for tag in op.provenance_tags)
        for op in old_format_appendix_ops
    )


def test_parse_ee_amendment_ops_does_not_carry_old_format_section_into_appendix_repeal() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("129062024002", archive),
        "ee/129062024002",
        target_title="Liikluskindlustuse seadus",
    )

    appendix_ops = [
        op
        for op in ops
        if any("seaduse lisa tunnistatakse kehtetuks" in tag for tag in op.provenance_tags)
    ]

    assert appendix_ops
    assert appendix_ops[0].action is StructuralAction.META
    assert appendix_ops[0].witness_rule_id == "ee_out_of_body_appendix_or_note_clause"
    assert not any(op.target.path == (("section", "85_2"),) and op.action is StructuralAction.REPEAL for op in appendix_ops)


def test_parse_ee_amendment_ops_keeps_numeric_measure_titles_distinct() -> None:
    archive = open_rt_archive(readonly=True)

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("109032012002", archive),
        "ee/109032012002",
        target_title=(
            "„Euroopa Kalandusfondi 2007–2013 rakenduskava” meetme 3.5 "
            "„Katseprojektid” raames toetuse andmise ja kasutamise tingimused ja kord"
        ),
    )

    amendment_sections = {
        tag.removeprefix("old_format_amendment_section:")
        for op in ops
        for tag in op.provenance_tags
        if tag.startswith("old_format_amendment_section:")
    }

    assert amendment_sections == {"12"}
    assert not any(
        "meetme 1.3" in tag or "meetme 3.1" in tag or "meetme 3.2" in tag
        for op in ops
        for tag in op.provenance_tags
    )


def test_extract_ee_ops_recovers_subsection_target_without_space_after_loige() -> None:
    ops = extract_ee_ops(
        "paragrahvi 8 lõige1 sõnastatakse järgmiselt: „(1) Uus tekst.”.",
        OperationSource(statute_id="ee/test"),
        1,
    )

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "8"), ("subsection", "1"))
    assert ops[0].witness_rule_id == "ee_optional_target_label_space"


def test_extract_ee_ops_splits_plural_subsection_insert_payload_by_label() -> None:
    text = (
        "paragrahvi 7 täiendatakse lõigetega 3 ja 4 järgmises sõnastuses: "
        "„(3) Kolmanda lõike tekst: 1) esimene punkt; 2) teine punkt. "
        "(4) Neljanda lõike tekst.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.action, op.target.path) for op in ops] == [
        (StructuralAction.INSERT, (("section", "7"), ("subsection", "3"))),
        (StructuralAction.INSERT, (("section", "7"), ("subsection", "4"))),
    ]
    assert _payload(ops[0]).text == "(3) Kolmanda lõike tekst: 1) esimene punkt; 2) teine punkt."
    assert _payload(ops[1]).text == "(4) Neljanda lõike tekst."
    assert all(op.witness_rule_id == "ee_plural_subsection_insert_payload_split" for op in ops)


def test_extract_ee_ops_uses_ascii_outer_payload_over_nested_title_quotes() -> None:
    text = (
        'paragrahvi 12 täiendatakse lõikega 6 1 järgnevas sõnastuses: '
        '"(6 1) Taotlusvormile lisatakse Euroopa Komisjoni otsuse '
        '„Euroopa Liidu toimimise lepingu artikli 106 lõike 2 kohaldamise kohta” '
        'artikli 4 tähenduses dokument.";'
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.INSERT
    assert ops[0].target.path == (("section", "12"), ("subsection", "6_1"))
    assert _payload(ops[0]).text.startswith("(6 1) Taotlusvormile lisatakse")
    assert "artikli 4 tähenduses dokument" in _payload(ops[0]).text
    assert _payload(ops[0]).attrs["source_family"] == "ee_ascii_quoted_marker_payload"


def test_parse_html_op_items_keeps_ascii_quoted_payload_items_together() -> None:
    html = (
        "<p>16) paragrahvi 12 lõiget 5 täiendatakse punktiga 8 järgmises "
        'sõnastuses: "8) esimene payload.";</p>'
        "<p>17) paragrahvi 12 lõike 6 preambulile lisatakse pärast sõna "
        "„Taotlusvormile“ sõnad „I-V taotlusvoorus“;</p>"
        "<p>18) paragrahvi 12 täiendatakse lõikega 6 1 järgnevas "
        'sõnastuses: "(6 1) esimene lause.”;</p>'
    )

    items = parse_html_op_items(html, allow_plain_paragraph_items=True)

    assert len(items) == 3
    assert items[0].startswith("16)")
    assert items[1].startswith("17)")
    assert items[2].startswith("18)")


def test_extract_ee_ops_splits_multi_target_text_delete_groups() -> None:
    text = (
        "paragrahvi 12 lõikest 3 ning § 14 lõigetest 4, 6 ja 7 jäetakse "
        "välja sõnad „eeltaotluse ja”, ning § 14 lõigetest 5, 7 ja 8 "
        "jäetakse välja sõnad „eeltaotlus” ja „eeltaotlus või” vastavas käändes;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, _payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ((("section", "12"), ("subsection", "3")), "eeltaotluse ja", ""),
        ((("section", "14"), ("subsection", "4")), "eeltaotluse ja", ""),
        ((("section", "14"), ("subsection", "6")), "eeltaotluse ja", ""),
        ((("section", "14"), ("subsection", "7")), "eeltaotluse ja", ""),
        ((("section", "14"), ("subsection", "5")), "eeltaotlus", ""),
        ((("section", "14"), ("subsection", "5")), "eeltaotlus või", ""),
        ((("section", "14"), ("subsection", "7")), "eeltaotlus", ""),
        ((("section", "14"), ("subsection", "7")), "eeltaotlus või", ""),
        ((("section", "14"), ("subsection", "8")), "eeltaotlus", ""),
        ((("section", "14"), ("subsection", "8")), "eeltaotlus või", ""),
    ]
    assert all(op.witness_rule_id == "ee_multi_target_text_delete_split" for op in ops)


def test_extract_ee_ops_splits_mixed_replace_and_delete_same_target() -> None:
    text = (
        "paragrahvi 3 lõike 2 punktis 1 asendatakse sõna „põhipalk” "
        "sõnaga „töötasu”, jäetakse välja sõnad „asendustasu” ja "
        "„õppepuhkusetasu” ning asendatakse sõna „palgatasemega” "
        "sõnaga „töötasuga”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, _payload(op).attrs["old_text"], _payload(op).text) for op in ops] == [
        ((("section", "3"), ("subsection", "2"), ("item", "1")), "põhipalk", "töötasu"),
        ((("section", "3"), ("subsection", "2"), ("item", "1")), "asendustasu ja õppepuhkusetasu", ""),
        ((("section", "3"), ("subsection", "2"), ("item", "1")), "asendustasu", ""),
        ((("section", "3"), ("subsection", "2"), ("item", "1")), "õppepuhkusetasu", ""),
        ((("section", "3"), ("subsection", "2"), ("item", "1")), "palgatasemega", "töötasuga"),
    ]
    assert [_payload(op).attrs["rewrite_mode"] for op in ops] == [
        "replace",
        "delete",
        "delete",
        "delete",
        "replace",
    ]
    assert all(
        _payload(op).attrs["source_family"] == "ee_mixed_delete_and_replace_same_target"
        for op in ops
    )
    assert all(op.witness_rule_id == "ee_mixed_delete_and_replace_same_target" for op in ops)


def test_parse_ee_amendment_ops_extracts_cross_act_transitional_section_repeals() -> None:
    target_title = "Põllumassiivi kaardi koostamise kord"
    xml = f"""
    <akt>
      <sisu>
        <paragrahv>
          <paragrahvNr>13</paragrahvNr>
          <paragrahvPealkiri>Rakendussätted</paragrahvPealkiri>
          <loige>
            <tavatekst>
              Põllumajandusministri 10. märtsi 2015. a määruse nr 22
              „{target_title}” §-d 2-4, 6 ja 7 tunnistatakse kehtetuks.
            </tavatekst>
          </loige>
        </paragrahv>
      </sisu>
    </akt>
    """.encode()

    ops = parse_ee_amendment_ops(xml, "ee/test", target_title)
    repeal_ops = [
        op
        for op in ops
        if op.witness_rule_id == "ee_cross_act_transitional_section_repeal"
    ]

    assert [(op.action, op.target.path) for op in repeal_ops] == [
        (StructuralAction.REPEAL, (("section", "2"),)),
        (StructuralAction.REPEAL, (("section", "3"),)),
        (StructuralAction.REPEAL, (("section", "4"),)),
        (StructuralAction.REPEAL, (("section", "6"),)),
        (StructuralAction.REPEAL, (("section", "7"),)),
    ]
    assert all(target_title in (op.source.raw_text if op.source else "") for op in repeal_ops)


def test_parse_ee_amendment_ops_does_not_retarget_cross_act_transitional_repeals_without_title() -> None:
    xml = """
    <akt>
      <sisu>
        <paragrahv>
          <paragrahvNr>13</paragrahvNr>
          <paragrahvPealkiri>Rakendussatted</paragrahvPealkiri>
          <loige>
            <tavatekst>
              Teise maaruse nimi &quot;Teine kord&quot; §-d 2-4, 6 ja 7 tunnistatakse kehtetuks.
            </tavatekst>
          </loige>
        </paragrahv>
      </sisu>
    </akt>
    """.encode()

    ops = parse_ee_amendment_ops(xml, "ee/test", "Põllumassiivi kaardi koostamise kord")

    assert all(op.witness_rule_id != "ee_cross_act_transitional_section_repeal" for op in ops)


def test_parse_ee_amendment_ops_recovers_2022_2028_transitional_section_repeals() -> None:
    archive = open_rt_archive(readonly=True)
    base = parse_ee_statute(fetch_rt_xml("111022022016", archive), "ee/111022022016")

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("122122022028", archive),
        "ee/122122022028",
        base.title,
        ref_effective="2023-01-01",
    )
    repeal_ops = [
        op
        for op in ops
        if op.witness_rule_id == "ee_cross_act_transitional_section_repeal"
    ]

    assert [op.target.path for op in repeal_ops] == [
        (("section", "2"),),
        (("section", "3"),),
        (("section", "4"),),
        (("section", "6"),),
        (("section", "7"),),
    ]


def test_extract_ee_ops_marks_section_intro_replace_without_widening_to_whole_section() -> None:
    text = (
        "paragrahvi 2 sissejuhatav lauseosa muudetakse ja sõnastatakse järgmiselt: "
        "„Meetme eesmärgiks on VKEde konkurentsivõime suurendamine, mille tulemusena:”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "2"),)
    assert ops[0].payload is not None
    scope_meta = read_subsection_text_scope_meta(ops[0].payload)
    assert scope_meta is not None
    assert scope_meta.intro_only is True
    assert ops[0].witness_rule_id == "ee_section_intro_replace_to_first_subsection"


def test_parse_html_op_items_preserves_words_split_by_inline_style_tags() -> None:
    html = (
        "<p><b>5)</b> paragrahvi 4 punkt 3 muudetakse ja sõnastatakse järgmiselt:</p>"
        "<p>„3) teadus- ja arendusasutus (edaspidi <i>TA&#160;asutu</i>s);”;</p>"
    )

    items = parse_html_op_items(html)

    assert "TA asutus" in items[0]
    assert "TA asutu s" not in items[0]


def test_extract_ee_ops_treats_kehtestatakse_uues_sonastuses_as_replace() -> None:
    text = (
        "paragrahvi 6 lõige 7 kehtestatakse uues sõnastuses järgmiselt: "
        "„(7) Uus lõike tekst: 1) esimene; 2) teine.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert len(ops) == 1
    assert ops[0].action is StructuralAction.REPLACE
    assert ops[0].target.path == (("section", "6"), ("subsection", "7"))
    assert ops[0].payload is not None
    assert ops[0].payload.text.startswith("(7) Uus lõike tekst")


def test_parse_ee_amendment_ops_keeps_excluded_global_rewrite_selectors_source_literal() -> None:
    archive = open_rt_archive(readonly=True)
    base = parse_ee_statute(fetch_rt_xml("109072013006", archive), "ee/109072013006")

    ops = parse_ee_amendment_ops(
        fetch_rt_xml("121042016001", archive),
        "ee/121042016001",
        target_title=base.title,
    )

    op = next(
        op
        for op in ops
        if op.target.path == (("section", "2"), ("subsection", "1_1"))
        and op.action is StructuralAction.TEXT_REPLACE
    )

    assert op.payload is not None
    assert op.payload.attrs["old_text"] == "ühenduse tolliseadustiku artiklites 36a ja 182a"
    assert (
        "ee_source_local_global_text_replace_selector_composition_skipped_for_excluded_target"
        in op.provenance_tags
    )


def test_extract_ee_ops_preserves_explicit_plural_item_insert_terminals() -> None:
    text = (
        "paragrahvi 4 täiendatakse punktidega 6 ja 7 järgmises sõnastuses: "
        "„6) saatemeeskonnaks loetakse sihtturult saabuvad isikud; "
        "7) sihtturg on taotluses määratletud välisriik.”;"
    )

    ops = extract_ee_ops(text, OperationSource(statute_id="ee/test", raw_text=text))

    assert [(op.target.path, _payload(op).text) for op in ops] == [
        ((("section", "4"), ("item", "6")), "6) saatemeeskonnaks loetakse sihtturult saabuvad isikud;"),
        ((("section", "4"), ("item", "7")), "7) sihtturg on taotluses määratletud välisriik."),
    ]
    assert all(
        _payload(op).attrs["source_family"] == "ee_explicit_item_replacement_terminal_preserved"
        for op in ops
    )
