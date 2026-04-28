from __future__ import annotations

from dataclasses import replace
from typing import Any, cast

import lawvm.estonia.grafter as grafter_module

from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    StructuralAction,
)
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.estonia.ee_instruction_waist import (
    EEInstructionFamily,
    EEParsedInstruction,
    EETextRewrite,
    EETextRewriteWitness,
    make_sentence_target_meta,
    make_subsection_selection_meta,
    make_subsection_text_scope_meta,
)
from lawvm.estonia.peg import extract_ee_ops
from lawvm.estonia.grafter import _ee_apply_op, _ee_apply_text_replace_value, apply_ee_ops
from lawvm.replay_adjudication import CompileAdjudication


def _child_subsection(section: IRNode, label: str) -> IRNode:
    for child in section.children:
        if child.kind == IRNodeKind.SUBSECTION and child.label == label:
            return child
    raise AssertionError(f"subsection {label!r} not found in {section.children!r}")


def _body_with_section_and_subsection(section_label: str, subsection_label: str, text: str) -> IRNode:
    return IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label=section_label,
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label=subsection_label, text=text),),
                    ),
                ),
            ),
        ),
    )


def test_text_replace_on_section_target_rewrites_descendant_text() -> None:
    body = _body_with_section_and_subsection(
        "1",
        "1",
        "kõrvaldamist ning krooni ja euro paralleelkäivet",
    )
    op = LegalOperation(
        op_id="ee_test_replace_descendant",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kõrvaldamist, krooni ja euro paralleelkäivet ning eurodes tehtavate sularahamaksete arveldamist",
            attrs={"old_text": "kõrvaldamist ning krooni ja euro paralleelkäivet"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "kõrvaldamist, krooni ja euro paralleelkäivet ning eurodes tehtavate sularahamaksete arveldamist"
    )


def test_insert_subsection_prefers_typed_insert_before_second_sentence() -> None:
    body = _body_with_section_and_subsection(
        "3",
        "2_1",
        "Esimene lause. Kolmas lause.",
    )
    op = LegalOperation(
        op_id="ee_test_insert_before_second_sentence",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "3"), ("subsection", "2_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Teine lause.",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(1,), mode="insert_before")},
        ),
        provenance_tags=("teine lause loetakse kolmandaks lauseks ja lõiget täiendatakse teise lausega",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause. Teine lause. Kolmas lause."


def test_insert_section_sentence_targets_existing_first_subsection_instead_of_duplicate_section() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="82",
                        text="Põlvnemise õiguslik tähendus",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Vanemate ja laste vastastikused õigused ja kohustused tulenevad laste põlvnemisest.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_section_insert_second_sentence",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "82"),)),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Lapsel ei või olla rohkem kui kaks vanemat."),
        provenance_tags=("paragrahvi 82 täiendatakse teise lausega järgmises sõnastuses",),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert len(result.children[0].children) == 1
    assert section.text == "Põlvnemise õiguslik tähendus"
    assert section.children[0].text == (
        "Vanemate ja laste vastastikused õigused ja kohustused tulenevad laste "
        "põlvnemisest. Lapsel ei või olla rohkem kui kaks vanemat."
    )


def test_insert_section_noops_when_identical_section_already_exists() -> None:
    existing_section = IRNode(
        kind=IRNodeKind.SECTION,
        label="29_1",
        text="Karjatamise üldnõuded veekaitsevööndis",
        children=(
            IRNode(
                kind=IRNodeKind.SUBSECTION,
                label="1",
                text="Karjatamine veekaitsevööndis ei tohi põhjustada kaldaerosiooni.",
            ),
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(existing_section,),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_identical_section_noop",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "29_1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "§ 29 1. Karjatamise üldnõuded veekaitsevööndis "
                "(1) Karjatamine veekaitsevööndis ei tohi põhjustada kaldaerosiooni."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert len(chapter.children) == 1
    assert chapter.children[0] == existing_section


def test_insert_item_noops_when_identical_item_already_exists() -> None:
    existing_item = IRNode(
        kind=IRNodeKind.ITEM,
        label="7_1",
        text=(
            "kooselulepingu sõlmimise ja lõpetamise tahteavalduse tõestamine ning "
            "kooselulepingu sõlmimise ja selle lõpetamise kohta andmete andmine ja "
            "kannete tegemine kooseluseaduse, abieluvararegistri seaduse ja "
            "rahvastikuregistri seaduse kohaselt;"
        ),
    )
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="29",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                children=(existing_item,),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_identical_item_noop",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "29"), ("subsection", "3"), ("item", "7_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "7 1) kooselulepingu sõlmimise ja lõpetamise tahteavalduse tõestamine ning "
                "kooselulepingu sõlmimise ja selle lõpetamise kohta andmete andmine ja "
                "kannete tegemine kooseluseaduse, abieluvararegistri seaduse ja "
                "rahvastikuregistri seaduse kohaselt;"
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0].children[0]

    assert item == existing_item


def test_insert_after_text_replace_rewrites_only_first_match() -> None:
    replaced = _ee_apply_text_replace_value(
        (
            "teenuse korralduse, inimkaubanduse ohvrile ja seksuaalselt väärkoheldud "
            "alaealisele ohvriabiteenuse korralduse, ohvriabiteenuse osutamise"
        ),
        "teenuse korralduse,",
        "teenuse korralduse, terrorismiohvrile,",
        mode="insert_after",
        case_inflected=False,
    )

    assert replaced == (
        "teenuse korralduse, terrorismiohvrile, inimkaubanduse ohvrile ja "
        "seksuaalselt väärkoheldud alaealisele ohvriabiteenuse korralduse, "
        "ohvriabiteenuse osutamise"
    )


def test_insert_after_text_replace_preserves_acronym_prefix_suffix_case() -> None:
    replaced = _ee_apply_text_replace_value(
        "võrreldud LOADMAN-tüüpi seadmega",
        "LOADMAN-",
        "LOADMAN- või INSPECTOR-",
        mode="insert_after",
        case_inflected=False,
    )

    assert replaced == "võrreldud LOADMAN-või INSPECTOR-tüüpi seadmega"

    sentence_start = _ee_apply_text_replace_value(
        "Kauba ebaseadusliku toimetamise eest",
        "kauba",
        "kauba teadvalt",
        mode="insert_after",
        case_inflected=False,
    )

    assert sentence_start == "Kauba teadvalt ebaseadusliku toimetamise eest"


def test_text_replace_with_typed_rewrite_mode_insert_after() -> None:
    body = _body_with_section_and_subsection("1", "1", "Määrus sisaldab kuni 100 trahviühikut ja seda rakendatakse.")
    op = LegalOperation(
        op_id="ee_test_replace_typed_mode_after",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="või lisafraas",
            attrs={
                "old_text": "kuni 100 trahviühikut",
                "rewrite_mode": "insert_after",
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == ("Määrus sisaldab kuni 100 trahviühikut või lisafraas ja seda rakendatakse.")


def test_text_replace_with_intro_only_subsection_scope_preserves_items() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="26_7",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Strateegilise gaasivaru haldamise kulud on:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="strateegilise gaasivaru hoidmisega seotud kulu;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="strateegilise gaasivaru kindlustamise kulu;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="3", text="strateegilise gaasivaru koguse kontrollimise kulu;"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_intro_only_subsection_scope",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "26_7"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="ning veeldatud maagaasi terminali haalamiskai ja taristu",
            attrs={
                "old_text": "gaasivaru",
                "rewrite_mode": "insert_after",
                "subsection_text_scope_meta": make_subsection_text_scope_meta(intro_only=True),
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Strateegilise gaasivaru ning veeldatud maagaasi terminali haalamiskai ja taristu haldamise kulud on:"
    )
    assert [child.text for child in subsection.children] == [
        "strateegilise gaasivaru hoidmisega seotud kulu;",
        "strateegilise gaasivaru kindlustamise kulu;",
        "strateegilise gaasivaru koguse kontrollimise kulu;",
    ]


def test_insert_after_text_replace_can_rewrite_all_matches_when_marked_labivalt() -> None:
    replaced = _ee_apply_text_replace_value(
        "abikaasade ühine avaldus ja abikaasade suhtes tehtud otsustus",
        "abikaasade",
        "abikaasade või registreeritud elukaaslaste",
        mode="insert_after",
        case_inflected=False,
        all_occurrences=True,
    )

    assert replaced == (
        "abikaasade või registreeritud elukaaslaste ühine avaldus ja "
        "abikaasade või registreeritud elukaaslaste suhtes tehtud otsustus"
    )


def test_case_inflected_insert_after_rewrites_all_matching_occurrences() -> None:
    replaced = _ee_apply_text_replace_value(
        (
            "Ühe abikaasa poolt eraldi esitatud maksejõuetusavalduse korral tuleb "
            "võlanimekirjas eraldi märkida kohustused, mille eest vastutab ka teine abikaasa."
        ),
        "abikaasa",
        "abikaasa või registreeritud elukaaslane",
        mode="insert_after",
        case_inflected=True,
        all_occurrences=True,
    )

    assert replaced == (
        "Ühe abikaasa või registreeritud elukaaslase poolt eraldi esitatud "
        "maksejõuetusavalduse korral tuleb võlanimekirjas eraldi märkida "
        "kohustused, mille eest vastutab ka teine abikaasa või registreeritud elukaaslane."
    )


def test_text_replace_preserves_space_when_replacement_drops_leading_comma() -> None:
    replaced = _ee_apply_text_replace_value(
        "teeb ettepaneku suulise menetluse uue aja kohta, tasub riigilõivu ja esitab andmed tasutud riigilõivu kohta",
        ", tasub riigilõivu ja esitab andmed tasutud riigilõivu kohta",
        "ja tasub riigilõivu",
        case_inflected=False,
    )

    assert replaced == "teeb ettepaneku suulise menetluse uue aja kohta ja tasub riigilõivu"


def test_text_replace_case_inflected_delete_supports_leading_voi_phrase() -> None:
    replaced = _ee_apply_text_replace_value(
        (
            "Kui avalduse esitab isik, kellele registreerimistaotlus üle läheb, peab ta "
            "avaldusele lisama üleminekut tõendava dokumendi või selle ametlikult kinnitatud ärakirja."
        ),
        "või selle ametlikult kinnitatud ärakiri",
        "",
        mode="delete",
        case_inflected=True,
    )

    assert replaced == (
        "Kui avalduse esitab isik, kellele registreerimistaotlus üle läheb, peab ta "
        "avaldusele lisama üleminekut tõendava dokumendi."
    )


def test_item_text_replace_preserves_lowercase_sentence_start_from_source() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="24",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="2",
                                        text=(
                                            "Välisministeerium Vabariigi Valitsusele, "
                                            "kui välislepingu on sõlminud Vabariigi Valitsus."
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_item_lowercase_sentence_start",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "24"), ("subsection", "1"), ("item", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="välislepingu sõlmimise algatanud ministeerium või Riigikantselei",
            attrs={"old_text": "Välisministeerium"},
        ),
        source=OperationSource(statute_id="ee/test"),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0].children[0]

    assert item.text == (
        "välislepingu sõlmimise algatanud ministeerium või Riigikantselei "
        "Vabariigi Valitsusele, kui välislepingu on sõlminud Vabariigi Valitsus."
    )


def test_subsection_text_replace_keeps_lowercase_mid_sentence_replacement() -> None:
    body = _body_with_section_and_subsection(
        "158",
        "1",
        (
            "Audiitorettevõtja, kelle usalduskliendiks lõppenud Audiitorkogu "
            "majandusaastal oli avaliku huvi üksus, avaldab aruande."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_subsection_lowercase_mid_sentence",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "158"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="majandusaastal",
            attrs={"old_text": "Audiitorkogu majandusaastal"},
        ),
        source=OperationSource(statute_id="ee/test"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Audiitorettevõtja, kelle usalduskliendiks lõppenud majandusaastal oli avaliku huvi üksus, avaldab aruande."
    )


def test_text_replace_after_words_does_not_leave_double_terminal_period() -> None:
    body = _body_with_section_and_subsection(
        "74_22",
        "3",
        (
            "Mootorsõiduki juhi poolt lubatud suurima sõidukiiruse ületamise eest "
            "üle 40 kilomeetri tunnis – karistatakse rahatrahviga kuni 200 "
            "trahviühikut või arestiga."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_after_words_period",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "74_22"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="või arestiga või sõiduki juhtimise õiguse äravõtmisega kuni ühe aastani.",
            attrs={"old_text": "või arestiga"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.endswith("või arestiga või sõiduki juhtimise õiguse äravõtmisega kuni ühe aastani.")
    assert not subsection.text.endswith("..")


def test_insert_fragment_into_existing_subsection_appends_before_terminal_period() -> None:
    body = _body_with_section_and_subsection(
        "2",
        "2",
        "Käesoleva paragrahvi lõikes 1 nimetamata isikud on kohustatud vastu võtma "
        "korraga kuni 50 kehtivat euro münti sõltumata nende väärtusest, pangatähti "
        "aga piiranguteta.",
    )
    op = LegalOperation(
        op_id="ee_test_append_fragment",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=", kui ei ole muu makseviisi kasutamise kokkulepet"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.endswith("pangatähti aga piiranguteta, kui ei ole muu makseviisi kasutamise kokkulepet.")


def test_replace_subsection_materializes_inline_numbered_items() -> None:
    body = _body_with_section_and_subsection(
        "64",
        "1",
        "Liiklusregistrisse andmete esitajaks on:",
    )
    op = LegalOperation(
        op_id="ee_test_replace_subsection_items",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "64"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=("(1) Liiklusregistrisse andmete esitajaks on: 1) sõiduki omanik; 2) juhiloa taotleja."),
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Liiklusregistrisse andmete esitajaks on:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "sõiduki omanik;"),
        ("2", "juhiloa taotleja."),
    ]


def test_replace_subsection_materializes_follow_on_subsection_from_same_payload() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="5",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="83_23",
                                text="Nõuded juhtidele",
                                children=(
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Vana teine."),
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas jääb alles."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_subsection_with_follow_on_subsection",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("chapter", "11_1"),
                ("division", "5"),
                ("section", "83_23"),
                ("subsection", "2"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "(2) Ettevõtja juhiks võib valida või määrata vaid isiku, kes on: "
                "1) Euroopa Liidu liikmesriigi kodanik või "
                "2) NATO liikmesriigi kodanik. "
                "(2 1) Käesoleva paragrahvi lõike 2 punkti 2 kohaselt antud "
                "haldusakti aluseks olevaid põhjendusi ei avaldata."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0].children[0]

    assert [(child.kind, child.label) for child in section.children] == [
        (IRNodeKind.SUBSECTION, "1"),
        (IRNodeKind.SUBSECTION, "2"),
        (IRNodeKind.SUBSECTION, "2_1"),
        (IRNodeKind.SUBSECTION, "3"),
    ]
    subsection = section.children[1]
    assert subsection.text == "Ettevõtja juhiks võib valida või määrata vaid isiku, kes on:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "Euroopa Liidu liikmesriigi kodanik või"),
        ("2", "NATO liikmesriigi kodanik."),
    ]
    assert section.children[2].text == (
        "Käesoleva paragrahvi lõike 2 punkti 2 kohaselt antud haldusakti aluseks olevaid põhjendusi ei avaldata."
    )


def test_apply_ee_ops_plain_subsection_repeal_range_does_not_clear_intervening_superscripts_without_typed_selection_meta() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="14",
                            text="Register",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_1", text="Kaks üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_2", text="Kaks kaks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Neljas."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="105052022001")
    ops = [
        LegalOperation(
            op_id="ee_test_repeal_14_2",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "2"))),
            source=source,
            provenance_tags=("paragrahvi 14 lõike 1 punktid 3 1, 4 ja lõiked 2–4 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_14_3",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "3"))),
            source=source,
            provenance_tags=("paragrahvi 14 lõike 1 punktid 3 1, 4 ja lõiked 2–4 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_14_4",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "4"))),
            source=source,
            provenance_tags=("paragrahvi 14 lõike 1 punktid 3 1, 4 ja lõiked 2–4 tunnistatakse kehtetuks;",),
        ),
    ]

    result = apply_ee_ops(statute, ops)
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Esimene."),
        ("2", ""),
        ("2_1", "Kaks üks."),
        ("2_2", "Kaks kaks."),
        ("3", ""),
        ("4", ""),
    ]


def test_apply_ee_ops_plain_subsection_repeal_range_prefers_typed_selection_meta_over_note_text() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="14",
                            text="Register",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_1", text="Kaks üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_2", text="Kaks kaks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Neljas."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="105052022001")
    ops = [
        LegalOperation(
            op_id="ee_test_repeal_14_2_typed",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "2"))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "subsection_selection_meta": make_subsection_selection_meta(
                        explicit_labels=("2", "3", "4"),
                        plain_numeric_ranges=(("2", "4"),),
                        label_ranges=(("2", "4"),),
                    )
                },
            ),
            provenance_tags=("paragrahvi 14 lõige 2 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_14_3_typed",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "3"))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "subsection_selection_meta": make_subsection_selection_meta(
                        explicit_labels=("2", "3", "4"),
                        plain_numeric_ranges=(("2", "4"),),
                        label_ranges=(("2", "4"),),
                    )
                },
            ),
            provenance_tags=("paragrahvi 14 lõige 3 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_14_4_typed",
            sequence=3,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "4"))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "subsection_selection_meta": make_subsection_selection_meta(
                        explicit_labels=("2", "3", "4"),
                        plain_numeric_ranges=(("2", "4"),),
                        label_ranges=(("2", "4"),),
                    )
                },
            ),
            provenance_tags=("paragrahvi 14 lõige 4 tunnistatakse kehtetuks;",),
        ),
    ]

    result = apply_ee_ops(statute, ops)
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Esimene."),
        ("2", ""),
        ("2_1", ""),
        ("2_2", ""),
        ("3", ""),
        ("4", ""),
    ]


def test_apply_ee_ops_plain_subsection_repeal_range_excludes_endpoint_superscript() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="7",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="156",
                            text="Tasuta eraldamine",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Kaks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolm."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Neli."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="5", text="Viis."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="5_1", text="Viis üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="6", text="Kuus."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="102102025001")
    ops = [
        LegalOperation(
            op_id=f"ee_test_repeal_156_{label}",
            sequence=sequence,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "156"), ("subsection", label))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={
                    "subsection_selection_meta": make_subsection_selection_meta(
                        explicit_labels=("2", "3", "4", "5"),
                        plain_numeric_ranges=(("2", "5"),),
                        label_ranges=(("2", "5"),),
                    )
                },
            ),
        )
        for sequence, label in enumerate(("2", "3", "4", "5"), start=1)
    ]

    result = apply_ee_ops(statute, ops)
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("2", ""),
        ("3", ""),
        ("4", ""),
        ("5", ""),
        ("5_1", "Viis üks."),
        ("6", "Kuus."),
    ]


def test_apply_ee_ops_superscript_subsection_repeal_typed_selection_does_not_clear_plain_base() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="14",
                            text="Register",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_1", text="Kaks üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2_2", text="Kaks kaks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="ee/test")
    ops = [
        LegalOperation(
            op_id="ee_test_repeal_14_2_1_typed",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "2_1"))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={"subsection_selection_meta": make_subsection_selection_meta(explicit_labels=("2_1", "2_2"))},
            ),
            provenance_tags=("paragrahvi 14 lõiked 2 1 ja 2 2 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_14_2_2_typed",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "2_2"))),
            source=source,
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={"subsection_selection_meta": make_subsection_selection_meta(explicit_labels=("2_1", "2_2"))},
            ),
            provenance_tags=("paragrahvi 14 lõiked 2 1 ja 2 2 tunnistatakse kehtetuks;",),
        ),
    ]

    result = apply_ee_ops(statute, ops)
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("2", "Teine."),
        ("2_1", ""),
        ("2_2", ""),
        ("3", "Kolmas."),
    ]


def test_apply_ee_ops_plain_subsection_repeal_list_does_not_clear_same_base_superscripts() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="2",
                            text="Mõisted",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1_1", text="Üks üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_plain_list_no_superscript_clear",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "2"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "subsection_selection_meta": make_subsection_selection_meta(
                    explicit_labels=("1", "2"),
                )
            },
        ),
    )

    result = apply_ee_ops(statute, [op])
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", ""),
        ("1_1", "Üks üks."),
        ("2", ""),
        ("3", "Kolmas."),
    ]


def test_apply_ee_ops_subsection_repeal_range_includes_live_intervening_superscripts() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="2",
                            text="Mõisted",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="16", text="Kuusteist."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="17", text="Seitseteist."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="17_1", text="Seitseteist üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="18", text="Kaheksateist."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="18_1", text="Kaheksateist üks."),
                                IRNode(kind=IRNodeKind.SUBSECTION, label="19", text="Üheksateist."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_subsection_label_range",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "2"), ("subsection", "16"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "subsection_selection_meta": make_subsection_selection_meta(
                    explicit_labels=("16", "17", "18", "18_1"),
                    label_ranges=(("16", "18_1"),),
                )
            },
        ),
    )

    result = apply_ee_ops(statute, [op])
    section = result.body.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("16", ""),
        ("17", ""),
        ("17_1", ""),
        ("18", ""),
        ("18_1", ""),
        ("19", "Üheksateist."),
    ]


def test_apply_ee_ops_item_repeals_finalize_last_live_normal_item_before_short_empty_stub_tail() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="14",
                            text="Register",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="1",
                                    text="Intro:",
                                    children=(
                                        IRNode(kind=IRNodeKind.ITEM, label="10", text="põhikirja jõustumise aeg;"),
                                        IRNode(kind=IRNodeKind.ITEM, label="10_1", text="majandusaasta algus ja lõpp;"),
                                        IRNode(kind=IRNodeKind.ITEM, label="11", text="järgmine punkt;"),
                                        IRNode(kind=IRNodeKind.ITEM, label="12", text="viimane punkt."),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="105052022001")
    ops = [
        LegalOperation(
            op_id="ee_test_repeal_item_11",
            sequence=1,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "1"), ("item", "11"))),
            source=source,
            provenance_tags=("paragrahvi 14 lõike 1 punktid 11 ja 12 tunnistatakse kehtetuks;",),
        ),
        LegalOperation(
            op_id="ee_test_repeal_item_12",
            sequence=2,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", "14"), ("subsection", "1"), ("item", "12"))),
            source=source,
            provenance_tags=("paragrahvi 14 lõike 1 punktid 11 ja 12 tunnistatakse kehtetuks;",),
        ),
    ]

    result = apply_ee_ops(statute, ops)
    items = result.body.children[0].children[0].children[0].children

    assert [(item.label, item.text) for item in items] == [
        ("10", "põhikirja jõustumise aeg;"),
        ("10_1", "majandusaasta algus ja lõpp."),
        ("11", ""),
        ("12", ""),
    ]


def test_global_case_inflected_text_replace_handles_coordinated_relvaseadus_phrase() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11_1",
                text="SÕJARELVA, SELLE LASKEMOONA JA LAHINGUMOONA KÄITLEMINE",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        text="Üldsätted",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="83_2",
                                text="Sõjarelva, laskemoona ja lahingumoona käitlemine",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="1",
                                        text=("Sõjarelva, laskemoona ja lahingumoona valmistamine ning hoidmine."),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_relvaseadus_global_case_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="sõjarelv, relvasüsteem, sõjarelva laskemoon",
            attrs={
                "old_text": "sõjarelv, laskemoon",
                "case_inflected": True,
                "scope_chapters": ["11_1"],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]
    section = chapter.children[0].children[0]

    assert chapter.text == "SÕJARELVA, SELLE LASKEMOONA JA LAHINGUMOONA KÄITLEMINE"
    assert section.text == "Sõjarelva, relvasüsteemi, sõjarelva laskemoona ja lahingumoona käitlemine"
    assert section.children[0].text == (
        "Sõjarelva, relvasüsteemi, sõjarelva laskemoona ja lahingumoona valmistamine ning hoidmine."
    )


def test_replace_subsection_plain_text_clears_old_item_children() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="83_3",
                                text="Liigitamine",
                                children=(
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Vana teine."),
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="2_1",
                                        text="Vana loetelu:",
                                        children=(
                                            IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                            IRNode(kind=IRNodeKind.ITEM, label="2", text="teine."),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_subsection_plain_text_clears_items",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(
            path=(
                ("chapter", "11_1"),
                ("division", "1"),
                ("section", "83_3"),
                ("subsection", "2_1"),
            )
        ),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="(2 1) Sõjarelva laskemoon on spetsiaalselt sõjarelvas kasutamiseks mõeldud laskemoon.",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0].children[1]

    assert subsection.text == "Sõjarelva laskemoon on spetsiaalselt sõjarelvas kasutamiseks mõeldud laskemoon."
    assert subsection.children == ()


def test_case_inflected_text_replace_handles_lepinguriik_phrase() -> None:
    body = _body_with_section_and_subsection(
        "66",
        "2",
        (
            "Käesoleva paragrahvi lõikes 1 nimetatud tegevusluba on nõutav ka isikul, "
            "kes omab mõne teise Euroopa Majanduspiirkonna lepinguriigi väljastatud tegevusluba."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_lepinguriik_case_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "66"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Euroopa Liidu liikmesriik",
            attrs={
                "old_text": "Euroopa Majanduspiirkonna lepinguriik",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Käesoleva paragrahvi lõikes 1 nimetatud tegevusluba on nõutav ka isikul, "
        "kes omab mõne teise Euroopa Liidu liikmesriigi väljastatud tegevusluba."
    )


def test_global_case_inflected_text_replace_handles_relvaseadus_plural_coordination() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="7",
                        text="Tegevusluba sõjarelvade, laskemoona ja lahingumoonaga seotud tegevusaladel",
                        children=(),
                    ),
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="83_2",
                                text="Käitlemine",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="5",
                                        text=(
                                            "Sõjarelvade, laskemoona, lahingumoona ja nende oluliste "
                                            "osade käitlemine majandustegevusena."
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="3",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="83_15",
                                text="Võõrandamine",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="1",
                                        text=(
                                            "Sõjarelvi, laskemoona, lahingumoona ja nende olulisi osi "
                                            "võib võõrandada ainult loa alusel."
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_relvaseadus_plural_coordination",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="sõjarelv, relvasüsteem, sõjarelva laskemoon",
            attrs={
                "old_text": "sõjarelvad, laskemoon",
                "case_inflected": True,
                "scope_chapters": ["11_1"],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]
    division = chapter.children[0]
    subsection = chapter.children[1].children[0].children[0]
    subsection_partitive = chapter.children[2].children[0].children[0]

    assert division.text == (
        "Tegevusluba sõjarelvade, relvasüsteemi, sõjarelva laskemoona ja lahingumoonaga seotud tegevusaladel"
    )
    assert subsection.text == (
        "Sõjarelvade, relvasüsteemi, sõjarelva laskemoona, lahingumoona ja nende "
        "oluliste osade käitlemine majandustegevusena."
    )
    assert subsection_partitive.text == (
        "Sõjarelvi, relvasüsteemi, sõjarelva laskemoona, lahingumoona ja nende "
        "olulisi osi võib võõrandada ainult loa alusel."
    )


def test_replace_subsection_with_whole_section_payload_preserves_untouched_subsections() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="15",
                        text="Vana pealkiri",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana esimene."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Vana teine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas jääb alles."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Neljas jääb alles."),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_promote_whole_section_payload",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "15"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "§ 15. Sõiduki ja autorongi suurimad lubatud mõõtmed, massid ja "
                "teljekoormused\x01 (1) Uus esimene lõige. (2) Uus teine lõige."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.text == "Sõiduki ja autorongi suurimad lubatud mõõtmed, massid ja teljekoormused"
    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Uus esimene lõige."),
        ("2", "Uus teine lõige."),
        ("3", "Kolmas jääb alles."),
        ("4", "Neljas jääb alles."),
    ]


def test_replace_subsection_resolves_duplicate_container_labels_to_matching_branch() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="13_1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="118",
                                text="Muu peatükk",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene haru."),),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="13_2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="119_7",
                                text="Aruandlusteenuse osutaja",
                                children=(
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Vana kolmas."),
                                    IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Vana neljas."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_duplicate_container_resolution",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "119_7"), ("subsection", "3"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="(3) Uus kolmas lõige."),
    )

    result = _ee_apply_op(body, op)
    updated = result.children[1].children[0].children[0].children[0]

    assert updated.text == "Uus kolmas lõige."


def test_repeal_division_preserves_child_section_stubs() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="5",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="2",
                        text="Kaitseväeteenistus",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="57",
                                text="Kaitseväeteenistuse nõuete rakendamine",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Sisu."),),
                            ),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="58",
                                text="Sõjaväelise auastme andmine",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Veel sisu."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_division_repeal_stubs",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "5"), ("division", "2"))),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0]

    assert division.kind == IRNodeKind.DIVISION
    assert division.text == "Kaitseväeteenistus"
    assert [
        (section.label, section.text, section.attrs.get("kehtetu"), section.children) for section in division.children
    ] == [
        ("57", "Kaitseväeteenistuse nõuete rakendamine", True, ()),
        ("58", "Sõjaväelise auastme andmine", True, ()),
    ]


def test_repeal_subdivision_marks_only_matching_jaotis_sections_kehtetu() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="12",
                        children=(
                            IRNode(
                                kind=IRNodeKind.DIVISION,
                                label="3",
                                text="Terviseameti toimingud",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="278",
                                        text="Ravimiseaduse alusel tehtavad toimingud",
                                        attrs={"jaotis": "2"},
                                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jääb alles."),),
                                    ),
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="279",
                                        text="Töötervishoiuteenuse osutajana registreerimise taotluse läbivaatamine",
                                        attrs={"jaotis": "3"},
                                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Kustub."),),
                                    ),
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="280",
                                        text="Rahvatervise seaduse alusel tehtavad toimingud",
                                        attrs={"jaotis": "4"},
                                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jääb samuti alles."),),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_subdivision",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "12"), ("division", "3"), ("subdivision", "3"))),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0].children[0]

    assert [
        (section.label, section.text, section.attrs.get("jaotis"), section.attrs.get("kehtetu"), section.children)
        for section in division.children
    ] == [
        (
            "278",
            "Ravimiseaduse alusel tehtavad toimingud",
            "2",
            None,
            (IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jääb alles."),),
        ),
        ("279", "Töötervishoiuteenuse osutajana registreerimise taotluse läbivaatamine", "3", True, ()),
        (
            "280",
            "Rahvatervise seaduse alusel tehtavad toimingud",
            "4",
            None,
            (IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jääb samuti alles."),),
        ),
    ]


def test_insert_item_without_explicit_subsection_uses_existing_subsection_item_parent() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="24",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="235",
                        text="Õigused ettekirjutuse tegemisel",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Inspektsioonil on õigus ettekirjutusega:",
                                children=(IRNode(kind=IRNodeKind.ITEM, label="7_8", text="eelmine punkt."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_item_under_existing_subsection",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "235"), ("item", "7_9"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="7 9) uus punkt."),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert [child.label for child in subsection.children] == ["7_8", "7_9"]
    assert subsection.children[0].text == "eelmine punkt;"
    assert subsection.children[1].text == "uus punkt."


def test_insert_section_with_superscript_label_anchors_to_nested_base_section_parent() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="8",
                        children=(
                            IRNode(
                                kind=IRNodeKind.DIVISION,
                                label="7",
                                text="Raudtee toimingud",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SECTION,
                                        label="198",
                                        text="Registreerimistunnistuse ja registreerimismärgi väljastamine",
                                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Olemasolev sisu."),),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="14",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="199",
                        text="Hiljem paiknev peatükk",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vale naaber."),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_section_under_nested_parent",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "198_1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "§ 198 1 . Raudteeveeremi kasutuselevõtmise lubamine\x01"
                "Raudteeveeremi kasutuselevõtmise lubamise taotluse läbivaatamise eest "
                "tasutakse riigilõivu."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0].children[0]
    late_chapter = result.children[1]

    assert [child.label for child in division.children] == ["198", "198_1"]
    assert division.children[1].text == "Raudteeveeremi kasutuselevõtmise lubamine"
    assert [child.label for child in late_chapter.children] == ["199"]


def test_replace_subsection_first_sentence_preserves_following_tail() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="18_2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="163_10",
                        text="Kaubatuletisinstrumentide positsioonide haldamine",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=(
                                    "Kui kauplemiskohas kaubeldakse kaubatuletisinstrumentidega, "
                                    "rakendab kauplemiskoha korraldaja positsioonide haldamise kontrolle. "
                                    "Selleks on kauplemiskoha korraldajal õigus:"
                                ),
                                children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene punkt;"),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_first_sentence_only",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "163_10"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Kui kauplemiskohas kaubeldakse kaubatuletisinstrumentidega või "
                "heitkoguse ühikute tuletisinstrumentidega, rakendab kauplemiskoha "
                "korraldaja positsioonide haldamise kontrolle."
            ),
        ),
        provenance_tags=("paragrahvi 163 10 lõike 1 esimene lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Kui kauplemiskohas kaubeldakse kaubatuletisinstrumentidega või heitkoguse "
        "ühikute tuletisinstrumentidega, rakendab kauplemiskoha korraldaja "
        "positsioonide haldamise kontrolle. Selleks on kauplemiskoha korraldajal õigus:"
    )
    assert subsection.children[0].text == "esimene punkt;"


def test_replace_subsection_first_sentence_does_not_split_on_date_ordinal() -> None:
    body = _body_with_section_and_subsection(
        "13_4",
        "3",
        (
            "Vana esimene lause sellele aastale eelneva aasta 1. novembriks, "
            "kui Euroopa Liit ei anna püügivõimalusi hilisemal ajal. "
            "Teine lause jääb alles."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_first_sentence_keeps_ordinal_date",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "13_4"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Kalavarude seisundist lähtudes kehtestab Vabariigi Valitsus "
                "määrusega eelseisvaks aastaks püügivõimalused sellele aastale "
                "eelneva aasta 1. novembriks, kui Euroopa Liit ei sea "
                "püügivõimalusi hiljem."
            ),
        ),
        provenance_tags=("paragrahvi 13 4 lõike 3 esimene lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Kalavarude seisundist lähtudes kehtestab Vabariigi Valitsus määrusega "
        "eelseisvaks aastaks püügivõimalused sellele aastale eelneva aasta 1. "
        "novembriks, kui Euroopa Liit ei sea püügivõimalusi hiljem. "
        "Teine lause jääb alles."
    )


def test_replace_subsection_second_sentence_preserves_leading_sentence() -> None:
    body = _body_with_section_and_subsection(
        "7",
        "4",
        "Esimene lause jääb alles. Vana teine lause.",
    )
    op = LegalOperation(
        op_id="ee_test_replace_second_sentence_only",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "7"), ("subsection", "4"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Uus teine lause."),
        provenance_tags=("paragrahvi 7 lõike 4 teine lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause jääb alles. Uus teine lause."


def test_replace_subsection_prefers_typed_sentence_target_meta_over_note_text() -> None:
    body = _body_with_section_and_subsection(
        "7",
        "4",
        "Esimene lause jääb alles. Vana teine lause.",
    )
    op = LegalOperation(
        op_id="ee_test_replace_subsection_prefers_typed_first_sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "7"), ("subsection", "4"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Uus esimene lause.",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,))},
        ),
        provenance_tags=("paragrahvi 7 lõike 4 teine lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Uus esimene lause. Vana teine lause."


def test_replace_section_prefers_typed_sentence_target_meta_over_note_text() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="37",
                        text="Rakendamise määrused",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Esimene lause. Teine lause.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_section_prefers_typed_first_sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "37"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,))},
        ),
        provenance_tags=(
            "Kommertspandiseaduse § 37 teine lause tunnistatakse kehtetuks.",
            "teine lause tunnistatakse kehtetuks",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Teine lause."


def test_replace_subsection_second_sentence_with_empty_payload_removes_sentence() -> None:
    body = _body_with_section_and_subsection(
        "57",
        "2",
        "Esimene lause jääb alles. Teine lause kustub.",
    )
    op = LegalOperation(
        op_id="ee_test_remove_second_sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "57"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=""),
        provenance_tags=("paragrahvi 57 lõike 2 teine lause tunnistatakse kehtetuks",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause jääb alles."


def test_replace_subsection_third_sentence_preserves_leading_sentences() -> None:
    body = _body_with_section_and_subsection(
        "102",
        "2",
        "Esimene lause jääb alles. Teine lause jääb alles. Vana kolmas lause.",
    )
    op = LegalOperation(
        op_id="ee_test_replace_third_sentence_only",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "102"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Uus kolmas lause."),
        provenance_tags=("paragrahvi 102 lõike 2 kolmas lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause jääb alles. Teine lause jääb alles. Uus kolmas lause."


def test_replace_section_sentence_repeal_redirects_to_subsection_one_when_section_is_heading() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="37",
                        text="Rakendamise määrused",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=(
                                    "Valdkonna eest vastutav minister võib anda määrusi registriosakondade tegevuse korraldamiseks. "
                                    "Valdkonna eest vastutav minister kehtestab määrusega kommertspandiregistri kaardi vormi."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_section_sentence_repeal",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "37"),)),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=""),
        provenance_tags=(
            "Kommertspandiseaduse § 37 esimene lause tunnistatakse kehtetuks.",
            "esimene lause tunnistatakse kehtetuks",
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.text == "Rakendamise määrused"
    assert (
        section.children[0].text
        == "Valdkonna eest vastutav minister kehtestab määrusega kommertspandiregistri kaardi vormi."
    )


def test_replace_section_payload_without_dot_strips_section_number_from_title() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11_2",
                        text="Vana pealkiri",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana tekst."),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_section_payload_without_dot",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "11_2"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "§ 11 2 Teaduseetika komitee\x01 Käesoleva seaduse §-s 11 1 nimetatud andmekogust "
                "teadusuuringu või statistika vajadusteks isikuandmete väljastamise eetilisust ja "
                "põhjendatust hindab teaduseetika komitee."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.text == "Teaduseetika komitee"
    assert section.children[0].text == (
        "Käesoleva seaduse §-s 11 1 nimetatud andmekogust teadusuuringu või statistika vajadusteks "
        "isikuandmete väljastamise eetilisust ja põhjendatust hindab teaduseetika komitee."
    )


def test_text_replace_insert_after_phrase_does_not_duplicate_inserted_suffix() -> None:
    body = _body_with_section_and_subsection(
        "103",
        "3",
        (
            "Aukohtu distsiplinaarkaristuse määramise otsus tehakse pärast otsuse "
            "vaidlustamistähtaja möödumist teatavaks ameti-või kutsekogu "
            "liikmetele olenevalt sellest, kelle tegevuse peale esitatud "
            "kaebust aukohus läbi vaatas."
        ),
    )
    payload = IRNode(
        kind=IRNodeKind.CONTENT,
        text="ameti- või kutsekogu liikmetele või usaldusisikule",
        attrs={"old_text": "ameti- või kutsekogu liikmetele"},
    )
    op = LegalOperation(
        op_id="ee_test_insert_after_phrase_once",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "103"), ("subsection", "3"))),
        payload=payload,
        provenance_tags=(
            "paragrahvi 103 lõiget 3 täiendatakse pärast sõnu "
            "„ameti- või kutsekogu liikmetele” sõnadega „või usaldusisikule”",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Aukohtu distsiplinaarkaristuse määramise otsus tehakse pärast otsuse "
        "vaidlustamistähtaja möödumist teatavaks ameti-või kutsekogu liikmetele "
        "või usaldusisikule olenevalt sellest, kelle tegevuse peale esitatud "
        "kaebust aukohus läbi vaatas."
    )


def test_replace_section_strips_rt_editorial_parentheticals_from_payload_subsections() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="12",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="63",
                        text="Liiklusregistri andmebaasid",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Senine tekst."),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_strip_rt_parenthetical_section_replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "63"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "§ 63. Liiklusregistri andmebaasid "
                "(3) Väikelaevade andmebaasis peetakse arvestust meresõiduohutuse seaduse "
                "(RT I 2002, 1, 1; 61, 375) nõuetele vastavate väikelaevade üle. "
                "(7) Juhtide ametikoolituse andmebaasis peetakse arvestust autoveoseaduse "
                "(RT I 2000, 54, 346; 2002, 32, 190) kohase juhtide ametikoolituse üle."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.text == "Liiklusregistri andmebaasid"
    assert [(child.label, child.text) for child in section.children] == [
        (
            "3",
            "Väikelaevade andmebaasis peetakse arvestust meresõiduohutuse seaduse nõuetele vastavate väikelaevade üle.",
        ),
        (
            "7",
            "Juhtide ametikoolituse andmebaasis peetakse arvestust autoveoseaduse kohase juhtide ametikoolituse üle.",
        ),
    ]


def test_replace_subsection_strips_rt_editorial_parenthetical_from_payload_text() -> None:
    body = _body_with_section_and_subsection(
        "63",
        "3",
        "Senine tekst.",
    )
    op = LegalOperation(
        op_id="ee_test_strip_rt_parenthetical_subsection_replace",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "63"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "(3) Väikelaevade andmebaasis peetakse arvestust meresõiduohutuse "
                "seaduse (RT I 2002, 1, 1; 61, 375) nõuetele vastavate "
                "väikelaevade üle."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Väikelaevade andmebaasis peetakse arvestust meresõiduohutuse seaduse nõuetele vastavate väikelaevade üle."
    )


def test_replace_subsection_normalizes_payload_spacing_and_numeric_ranges() -> None:
    body = _body_with_section_and_subsection(
        "74_68",
        "1",
        "Senine tekst.",
    )
    op = LegalOperation(
        op_id="ee_test_normalize_payload_spacing",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "74_68"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "(1) Käesoleva seaduse §-des 74 1 – 74 65 sätestatud väärtegudele "
                "kohaldatakse karistusseadustiku üldosa. Tööinspektor  teeb otsuse."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Käesoleva seaduse §-des 74 1–74 65 sätestatud väärtegudele "
        "kohaldatakse karistusseadustiku üldosa. Tööinspektor teeb otsuse."
    )


def test_global_case_inflected_text_replace_rewrites_estonian_case_forms() -> None:
    body = _body_with_section_and_subsection(
        "3",
        "1",
        "Täienduskoolitusasutuse pidaja kehtestab tingimused. "
        "Täienduskoolitusasutuse pidajana tegutsemiseks tuleb esitada teade. "
        "Täienduskoolitusasutuse pidajale kehtestatud nõuded kohalduvad ka siis, "
        "Õppepuhkust antakse täienduskoolitusasutuse pidaja läbiviidavas täienduskoolituses. "
        "Järelevalvet tehakse täienduskoolitusasutuse pidajate ja nende tegevuse üle. "
        "kui Tasemeõppes osalemine jätkub.",
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_global_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="täienduskoolitusasutus",
            attrs={
                "old_text": "täienduskoolitusasutuse pidaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Täienduskoolitusasutus kehtestab tingimused." in subsection.text
    assert "Täienduskoolitusasutusena tegutsemiseks" in subsection.text
    assert "Täienduskoolitusasutusele kehtestatud" in subsection.text
    assert "täienduskoolitusasutuse läbiviidavas täienduskoolituses" in subsection.text
    assert "täienduskoolitusasutuste ja nende tegevuse üle" in subsection.text


def test_global_case_inflected_text_replace_rewrites_nik_partitive() -> None:
    body = _body_with_section_and_subsection(
        "21",
        "3",
        "Väljasaadetav teavitab eelnevalt migratsioonijärelevalveametnikku.",
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_nik_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kinnipidamiskeskuse ametnik",
            attrs={
                "old_text": "migratsioonijärelevalveametnik",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Väljasaadetav teavitab eelnevalt kinnipidamiskeskuse ametnikku."


def test_global_case_inflected_text_replace_consumes_waist_instruction(monkeypatch) -> None:
    body = _body_with_section_and_subsection(
        "3",
        "1",
        "Täienduskoolitusasutuse pidaja kehtestab tingimused. "
        "Täienduskoolitusasutuse pidajana tegutsemiseks tuleb esitada teade. "
        "Täienduskoolitusasutuse pidajale kehtestatud nõuded kohalduvad ka siis, "
        "Õppepuhkust antakse täienduskoolitusasutuse pidaja läbiviidavas täienduskoolituses. "
        "Järelevalvet tehakse täienduskoolitusasutuse pidajate ja nende tegevuse üle. "
        "kui Tasemeõppes osalemine jätkub.",
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_global_replace_waist",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="täienduskoolitusasutus",
            attrs={
                "old_text": "täienduskoolitusasutuse pidaja",
            },
        ),
    )

    calls: list[tuple[int, str, str | None]] = []
    real = grafter_module.to_ee_parsed_instructions

    def recording_to_ee_parsed_instructions(
        ops,
        *,
        source_rule="estonia/peg:extract_ee_ops",
        wrapper_source_text=None,
    ):
        calls.append((len(ops), source_rule, wrapper_source_text))
        instructions = real(ops, source_rule=source_rule, wrapper_source_text=wrapper_source_text)
        return [
            replace(inst, rewrite=replace(inst.rewrite, case_inflected=True)) if inst.rewrite is not None else inst
            for inst in instructions
        ]

    monkeypatch.setattr(grafter_module, "to_ee_parsed_instructions", recording_to_ee_parsed_instructions)

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert calls == [(1, "estonia/grafter:_ee_apply_op", None)]
    assert "Täienduskoolitusasutus kehtestab tingimused." in subsection.text
    assert "Täienduskoolitusasutusena tegutsemiseks" in subsection.text
    assert "Täienduskoolitusasutusele kehtestatud" in subsection.text
    assert "täienduskoolitusasutuse läbiviidavas täienduskoolituses" in subsection.text
    assert "täienduskoolitusasutuste ja nende tegevuse üle" in subsection.text

    op2 = LegalOperation(
        op_id="ee_test_case_inflected_global_replace_2",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="formaalõpe",
            attrs={"old_text": "tasemeõpe", "case_inflected": True},
        ),
    )
    result2 = _ee_apply_op(result, op2)
    subsection2 = result2.children[0].children[0].children[0]

    assert "Formaalõppes osalemine" in subsection2.text


def test_global_text_replace_consumes_typed_scope_and_exclusions(monkeypatch) -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Kindlustusandja teavitab kindlustusvõtjat.",
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Kindlustusandja pakkumus ei või olla tingimuslik.",
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Kindlustusandja teavitab kindlustusvõtjat.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_global_scope_waist",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kindlustuse turustaja",
            attrs={
                "old_text": "kindlustusandja",
                "scope_chapters": ["2"],
                "exclude_paths": [[("chapter", "2"), ("section", "1")]],
            },
        ),
    )

    calls: list[tuple[int, str, str | None]] = []
    real = grafter_module.to_ee_parsed_instructions

    def recording_to_ee_parsed_instructions(
        ops,
        *,
        source_rule="estonia/peg:extract_ee_ops",
        wrapper_source_text=None,
    ):
        calls.append((len(ops), source_rule, wrapper_source_text))
        instructions = real(ops, source_rule=source_rule, wrapper_source_text=wrapper_source_text)
        return [
            replace(
                inst,
                rewrite=replace(
                    inst.rewrite,
                    scope_chapters=("2",),
                    exclude_paths=((("chapter", "2"), ("section", "1")),),
                ),
                rewrite_witness=EETextRewriteWitness(
                    source_text="typed witness",
                    rewrite=EETextRewrite(
                        old_surface="kindlustusandja",
                        new_surface="kindlustuse turustaja",
                        scope_chapters=("1",),
                        exclude_paths=((("chapter", "1"), ("section", "2")),),
                    ),
                ),
            )
            if inst.rewrite is not None
            else inst
            for inst in instructions
        ]

    monkeypatch.setattr(grafter_module, "to_ee_parsed_instructions", recording_to_ee_parsed_instructions)

    result = _ee_apply_op(body, op)
    chapter1 = result.children[0]
    chapter2 = result.children[1]

    assert calls == [(1, "estonia/grafter:_ee_apply_op", None)]
    assert chapter1.children[0].children[0].text == "Kindlustuse turustaja teavitab kindlustusvõtjat."
    assert chapter1.children[1].children[0].text == "Kindlustusandja pakkumus ei või olla tingimuslik."
    assert chapter2.children[0].children[0].text == "Kindlustusandja teavitab kindlustusvõtjat."


def test_case_inflected_text_replace_handles_mine_nominalizations() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="36",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="3",
                                        text="prospekti registreerimisel esitatud teave on osutunud olulisel määral ebaõigeks.",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_mine_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "36"), ("subsection", "1"), ("item", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kinnitamine",
            attrs={"old_text": "registreerimine", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0].children[0]

    assert item.text == "prospekti kinnitamisel esitatud teave on osutunud olulisel määral ebaõigeks."


def test_extract_ee_ops_emits_case_inflected_global_text_replace_pairs() -> None:
    source = OperationSource(statute_id="ee/test", raw_text="test")
    ops = extract_ee_ops(
        (
            "1) seaduse 1.–6. peatükis asendatakse sõnad "
            "„täienduskoolitusasutuse pidaja” sõnaga „täienduskoolitusasutus” ja sõnad "
            "„täienduskoolitusasutuse pidajad” asendatakse sõnaga "
            "„täienduskoolitusasutused” vastavas käändes;"
        ),
        source,
    )

    assert len(ops) == 2
    assert ops[0].target.path == ()
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "täienduskoolitusasutuse pidaja"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[1].payload is not None
    assert ops[1].payload.attrs["old_text"] == "täienduskoolitusasutuse pidajad"


def test_extract_ee_ops_pairs_vastavalt_global_text_replacements_correctly() -> None:
    source = OperationSource(statute_id="ee/test", raw_text="test")
    ops = extract_ee_ops(
        (
            "1) seaduse tekstis asendatakse sõnad "
            "«Teede- ja Sideministeerium» ning «teede- ja sideminister» "
            "vastavalt sõnadega «Majandus- ja Kommunikatsiooniministeerium» "
            "ning «majandus- ja kommunikatsiooniminister» nõutavas käändes;"
        ),
        source,
    )

    assert len(ops) == 2
    assert ops[0].payload is not None
    assert ops[0].payload.attrs["old_text"] == "Teede- ja Sideministeerium"
    assert ops[0].payload.text == "Majandus- ja Kommunikatsiooniministeerium"
    assert ops[1].payload is not None
    assert ops[1].payload.attrs["old_text"] == "teede- ja sideminister"
    assert ops[1].payload.text == "majandus- ja kommunikatsiooniminister"
    assert ops[0].payload.attrs["case_inflected"] is True
    assert ops[1].payload.attrs["case_inflected"] is True


def test_global_case_inflected_text_replace_honors_excluded_paths() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="10",
                text="Rakendamine",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="50_1",
                        text="Erand",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Veterinaar-ja Toiduamet jääb siia.",
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="50_2",
                        text="Teine erand",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Veterinaar-ja Toiduamet muutub siin.",
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Veterinaar-ja Toiduamet jääb ka siia.",
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="51",
                        text="Tavaline säte",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Veterinaar-ja Toiduamet muutub siin samuti.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_global_replace_with_exclusions",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Põllumajandus- ja Toiduamet",
            attrs={
                "old_text": "Veterinaar- ja Toiduamet",
                "case_inflected": True,
                "exclude_paths": [
                    (("section", "50_1"),),
                    (("section", "50_2"), ("subsection", "2")),
                ],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert chapter.children[0].children[0].text == "Veterinaar-ja Toiduamet jääb siia."
    assert chapter.children[1].children[0].text == "Põllumajandus-ja Toiduamet muutub siin."
    assert chapter.children[1].children[1].text == "Veterinaar-ja Toiduamet jääb ka siia."
    assert chapter.children[2].children[0].text == "Põllumajandus-ja Toiduamet muutub siin samuti."


def test_case_inflected_text_replace_rewrites_valmistis_to_segu_family() -> None:
    from lawvm.estonia.grafter import _ee_apply_text_replace_value

    text = "Valmistise tervise-ja keskkonnaohtlikkust määratakse ka konventsionaalse meetodiga."
    replaced = _ee_apply_text_replace_value(text, "valmistis", "segu", case_inflected=True)
    assert replaced == "Segu tervise-ja keskkonnaohtlikkust määratakse ka konventsionaalse meetodiga."

    text2 = (
        "Kõrge lämmastikusisaldusega ammooniumnitraat on tahke ammooniumnitraat nii ainena kui ka "
        "valmistise koostises, mis sisaldab rohkem kui 28 massiprotsenti ammooniumnitraadipõhist lämmastikku."
    )
    replaced2 = _ee_apply_text_replace_value(text2, "valmistis", "segu", case_inflected=True)
    assert replaced2 == (
        "Kõrge lämmastikusisaldusega ammooniumnitraat on tahke ammooniumnitraat nii ainena kui ka "
        "segu koostises, mis sisaldab rohkem kui 28 massiprotsenti ammooniumnitraadipõhist lämmastikku."
    )


def test_case_inflected_text_replace_delete_handles_veterinaararst_phrase_family() -> None:
    from lawvm.estonia.grafter import _ee_apply_text_replace_value

    text = (
        "teavitama piirkonda teenindavat volitatud veterinaararsti või Veterinaar-ja Toiduametit "
        "üle 24 kuu vanuse veise ning üle 18 kuu vanuse lamba ja kitse enda tarbeks "
        "tapmisest vähemalt 24 tundi ette;"
    )
    replaced = _ee_apply_text_replace_value(
        text,
        "piirkonda teenindav volitatud veterinaararst või",
        "",
        case_inflected=True,
    )
    assert replaced == (
        "teavitama Veterinaar-ja Toiduametit üle 24 kuu vanuse veise ning üle 18 kuu vanuse "
        "lamba ja kitse enda tarbeks tapmisest vähemalt 24 tundi ette;"
    )

    text2 = (
        "Teatamiskohustusliku loomataudi kahtluse korral on veterinaararst kohustatud "
        "teavitama sellest kohe korrakaitseorganit või volitatud veterinaararsti ja loomapidajat."
    )
    replaced2 = _ee_apply_text_replace_value(
        text2,
        "või volitatud veterinaararst",
        "",
        case_inflected=True,
    )
    assert replaced2 == (
        "Teatamiskohustusliku loomataudi kahtluse korral on veterinaararst kohustatud "
        "teavitama sellest kohe korrakaitseorganit ja loomapidajat."
    )


def test_global_case_inflected_text_replace_handles_minister_phrase_genitive() -> None:
    body = _body_with_section_and_subsection(
        "64",
        "2",
        (
            "Käesolevas seaduses ettenähtud ülesannete täitmiseks on teede- ja "
            "sideministri määratud asutusel õigus nõuda andmeid."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_minister_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="majandus- ja kommunikatsiooniminister",
            attrs={
                "old_text": "teede- ja sideminister",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "majandus- ja kommunikatsiooniministri määratud asutusel" in subsection.text


def test_global_case_inflected_text_replace_handles_multiword_minister_phrase_cases() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text="Kindlustustegevuse liikide alaliigid kehtestatakse teede- ja sideministri määrusega.",
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="71",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="7",
                                text="Teede- ja sideministril on õigus kehtestada kindlustusandja omavahendite normatiivi arvutamise täpsem kord.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_generic_minister_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkonna eest vastutav minister",
            attrs={
                "old_text": "teede- ja sideminister",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section_11 = result.children[0].children[0].children[0]
    section_71 = result.children[0].children[1].children[0]

    assert "valdkonna eest vastutava ministri määrusega" in section_11.text
    assert "Valdkonna eest vastutaval ministril" in section_71.text


def test_case_inflected_text_replace_keeps_postposed_minister_subject_before_kaskkirjaga() -> None:
    text = (
        "Kui püügivõimalused on ammendatud, keelab ja lõpetab kalapüügi juhul, "
        "kui kalapüüki ei ole lõpetanud Euroopa Liit, põllumajandusminister käskkirjaga."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "põllumajandusminister",
        "valdkonna eest vastutav minister",
        case_inflected=True,
    )

    assert updated == (
        "Kui püügivõimalused on ammendatud, keelab ja lõpetab kalapüügi juhul, "
        "kui kalapüüki ei ole lõpetanud Euroopa Liit, valdkonna eest vastutav minister käskkirjaga."
    )


def test_global_case_inflected_text_replace_handles_ambiguous_genitive_phrase_context() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "1",
        (
            "Teabevaldaja salastatud teabekandjate evakueerimist käsitlev teave "
            "ning teabevaldaja taotlusel tehtud kontroll."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_ambiguous_genitive_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "teabevaldaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Töötleva üksuse salastatud teabekandjate" in subsection.text
    assert "töötleva üksuse taotlusel" in subsection.text


def test_global_case_inflected_text_replace_handles_taotlusel_after_finite_verb() -> None:
    body = _body_with_section_and_subsection(
        "23",
        "2",
        (
            "Amet algatab asutuse, põhiseadusliku institutsiooni või juriidilise isiku "
            "taotlusel töötlussüsteemi akrediteerimise."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_finite_verb_taotlusel_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "asutus, põhiseaduslik institutsioon või juriidiline isik",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Amet algatab töötleva üksuse taotlusel töötlussüsteemi akrediteerimise."
    )


def test_case_inflected_text_replace_handles_i_and_ist_phrase_forms() -> None:
    text = (
        "Kohtunik otsustab võlgade ümberkujundamise asjas enda ja kohtunikuabi "
        "täpse tööjaotuse ning võib anda kohtunikuabile suuniseid."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "kohtunikuabi",
        "kohtujurist",
        case_inflected=True,
    )

    assert replaced == (
        "Kohtunik otsustab võlgade ümberkujundamise asjas enda ja kohtujuristi "
        "täpse tööjaotuse ning võib anda kohtujuristile suuniseid."
    )


def test_case_inflected_text_replace_handles_protocol_compound_forms() -> None:
    text = (
        "Õde täidab vereülekandeprotokolli ning kleebib etiketi "
        "vereülekandeprotokolli."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "vereülekandeprotokoll",
        "transfusiooniprotokoll",
        case_inflected=True,
    )

    assert replaced == (
        "Õde täidab transfusiooniprotokolli ning kleebib etiketi "
        "transfusiooniprotokolli."
    )


def test_case_inflected_text_replace_handles_register_compound_forms() -> None:
    text = (
        "Andmed on täitemenetlusregistris. "
        "Kanne eemaldatakse täitemenetlusregistrist."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "täitemenetlusregister",
        "täitmisregister",
        case_inflected=True,
    )

    assert replaced == (
        "Andmed on täitmisregistris. "
        "Kanne eemaldatakse täitmisregistrist."
    )


def test_case_inflected_text_replace_handles_o_family_phrase_forms() -> None:
    text = (
        "Käesoleva paragrahvi lõikes 1 nimetatud sotsiaaltoetus makstakse "
        "sotsiaaltoetuse saaja arveldusarvele või teise isiku arveldusarvele Eestis."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "arveldusarve",
        "arvelduskonto",
        case_inflected=True,
    )

    assert replaced == (
        "Käesoleva paragrahvi lõikes 1 nimetatud sotsiaaltoetus makstakse "
        "sotsiaaltoetuse saaja arvelduskontole või teise isiku arvelduskontole Eestis."
    )


def test_case_inflected_text_replace_handles_terminal_to_koht_phrase_forms() -> None:
    text = (
        "Hoiulevõetud alkoholi hoitakse tolliterminalis ja antakse vajaduse korral "
        "vastutavale hoiule tolliterminali."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "tolliterminal",
        "ajutise ladustamise koht",
        case_inflected=True,
    )

    assert replaced == (
        "Hoiulevõetud alkoholi hoitakse ajutise ladustamise kohas ja antakse "
        "vajaduse korral vastutavale hoiule ajutise ladustamise kohta."
    )


def test_text_replace_without_case_inflection_does_not_rewrite_i_family_suffix_forms() -> None:
    text = "Kohtunik võib anda kohtunikuabile suuniseid."

    replaced = _ee_apply_text_replace_value(
        text,
        "kohtunikuabi",
        "kohtujurist",
        case_inflected=False,
    )

    assert replaced == text


def test_case_inflected_text_replace_handles_heading_like_start_phrase() -> None:
    text = "Kohtunikuabi pädevus"

    replaced = _ee_apply_text_replace_value(
        text,
        "kohtunikuabi",
        "kohtujurist",
        case_inflected=True,
    )

    assert replaced == "Kohtujuristi pädevus"


def test_case_inflected_text_replace_keeps_nominative_before_nominalization_with_joiner_tail() -> None:
    text = "Põllumajandusamet asendustäitmise ja sunniraha seaduses sätestatud korras."

    replaced = _ee_apply_text_replace_value(
        text,
        "Põllumajandusamet",
        "Põllumajandus-ja Toiduamet",
        case_inflected=True,
    )

    assert replaced == "Põllumajandus-ja Toiduamet asendustäitmise ja sunniraha seaduses sätestatud korras."


def test_text_replace_delete_collapses_duplicate_head_after_comma_qualifier_removal() -> None:
    text = (
        "Kui enne käesoleva paragrahvi jõustumist on Euroopa Liidu kodanikul, "
        "Euroopa Majanduspiirkonna liikmesriigi ja Šveitsi Konföderatsiooni "
        "kodanikul tekkinud elukoha aadress."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        ", Euroopa Majanduspiirkonna liikmesriigi ja Šveitsi Konföderatsiooni",
        "",
        case_inflected=False,
    )

    assert replaced == (
        "Kui enne käesoleva paragrahvi jõustumist on Euroopa Liidu kodanikul "
        "tekkinud elukoha aadress."
    )


def test_text_replace_delete_without_comma_qualifier_keeps_duplicate_head_surface() -> None:
    text = "kodanikul kodanikul"

    replaced = _ee_apply_text_replace_value(
        text,
        "muu tekst",
        "",
        case_inflected=False,
    )

    assert replaced == text


def test_case_inflected_text_replace_delete_keeps_punctuation_prefixed_phrase_bounded() -> None:
    text = (
        "Kui enne käesoleva paragrahvi jõustumist on Euroopa Liidu kodanikul, "
        "Euroopa Majanduspiirkonna liikmesriigi ja Šveitsi Konföderatsiooni "
        "kodanikul tekkinud elukoha aadress."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        ", Euroopa Majanduspiirkonna liikmesriigi ja Šveitsi Konföderatsiooni kodanik",
        "",
        case_inflected=True,
    )

    assert replaced == (
        "Kui enne käesoleva paragrahvi jõustumist on Euroopa Liidu kodanikul "
        "tekkinud elukoha aadress."
    )


def test_text_replace_skips_overlapping_replacement_tail_already_present_after_match() -> None:
    text = (
        "Registrisse kantakse andmed isiku käesoleva seaduse alusel tunnustatud "
        "ettevõtte kohta või ettevõtte kohta, millest on käesoleva seaduse "
        "kohaselt teavitatud."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "ettevõtte kohta",
        "ettevõtte kohta või ettevõtte kohta, millest on käesoleva seaduse kohaselt teavitatud",
        case_inflected=False,
    )

    assert replaced == text


def test_insert_after_text_replace_skips_suffix_already_present_after_match() -> None:
    text = "Käesolev seadus sätestab dopinguvastaste ja spordieetika reeglite järgimise nõuded."

    replaced = _ee_apply_text_replace_value(
        text,
        "dopinguvastaste",
        "dopinguvastaste ja spordieetika",
        mode="insert_after",
        case_inflected=False,
    )

    assert replaced == text


def test_text_replace_skips_match_inside_existing_replacement_surface() -> None:
    body = _body_with_section_and_subsection(
        "5",
        "2_1",
        (
            "Erakonnast väljaastumiseks esitab erakonna liige kirjaliku avalduse "
            "erakonnale või Tartu Maakohtu registriosakonnale (edaspidi registriosakond)."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_skip_nested_existing_replacement_surface",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "5"), ("subsection", "2_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Tartu Maakohtu registriosakonnale (edaspidi registriosakond)",
            attrs={"old_text": "kohtu registriosakonnale"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Erakonnast väljaastumiseks esitab erakonna liige kirjaliku avalduse "
        "erakonnale või Tartu Maakohtu registriosakonnale (edaspidi registriosakond)."
    )


def test_global_case_inflected_text_replace_keeps_nominative_before_regular_noun_phrase() -> None:
    body = _body_with_section_and_subsection(
        "12",
        "1",
        (
            "Lisaks majandustegevuse seadustiku üldosa seaduse § 31 lõikes 2 sätestatule "
            "teeb täienduskoolitusasutuse pidaja täienduskoolituses osalejale ja "
            "koolituse rahastajale teatavaks vähemalt järgmised andmed ja dokumendid:"
        ),
    )
    op = LegalOperation(
        op_id="ee_test_keep_nominative_before_regular_noun_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="täienduskoolitusasutus",
            attrs={
                "old_text": "täienduskoolitusasutuse pidaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Lisaks majandustegevuse seadustiku üldosa seaduse § 31 lõikes 2 sätestatule "
        "teeb täienduskoolitusasutus täienduskoolituses osalejale ja koolituse "
        "rahastajale teatavaks vähemalt järgmised andmed ja dokumendid:"
    )


def test_global_case_inflected_text_replace_handles_teabevaldaja_possessive_contexts() -> None:
    body = _body_with_section_and_subsection(
        "10",
        "1",
        (
            "Teabevaldaja kohustused. "
            "Teabevaldaja turvaala valve- ja häiresüsteeme käsitlev teave. "
            "Teabevaldaja arhiivis hoitav teabekandja. "
            "Teabevaldaja seadusest tulenevate ülesannete täitmine."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_teabevaldaja_possessive_contexts",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "teabevaldaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Töötleva üksuse kohustused." in subsection.text
    assert "Töötleva üksuse turvaala" in subsection.text
    assert "Töötleva üksuse arhiivis" in subsection.text
    assert "Töötleva üksuse seadusest tulenevate" in subsection.text


def test_case_inflected_text_replace_handles_ametikoht_phrase_forms() -> None:
    body = _body_with_section_and_subsection(
        "58",
        "1",
        (
            "Isik, kes töötab ametikohal, millel töötamise eeltingimuseks on nõutava loa omamine. "
            "Kui isik soovib asuda ametikohale, millel töötamise eeltingimuseks on juurdepääsuõigus. "
            "Loetelus on ametikohad, millel töötamise eeltingimuseks on juurdepääsuõigus."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_ametikoht_phrase_forms",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "58"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töö- või ametikoht, mille ülesannete täitmise",
            attrs={
                "old_text": "ametikoht, millel töötamise",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "töö- või ametikohal, mille ülesannete täitmise" in subsection.text
    assert "töö- või ametikohale, mille ülesannete täitmise" in subsection.text
    assert "töö- või ametikohad, mille ülesannete täitmise" in subsection.text


def test_case_inflected_text_replace_handles_kaitsevagi_genitive_through() -> None:
    body = _body_with_section_and_subsection(
        "51",
        "1",
        (
            "Käesoleva seaduse § 30 1 lõikes 1 nimetatud isik esitab dokumendid "
            "Kaitseväe kaudu julgeolekukontrolli teostavale asutusele. "
            "Asutus teavitab viivitamata Kaitseväge juurdepääsuõiguse andmisest."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_kaitsevagi_genitive_through",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "51"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Kaitseministeeriumi valitsemisala valitsusasutus",
            attrs={
                "old_text": "Kaitsevägi",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Kaitseministeeriumi valitsemisala valitsusasutuse kaudu" in subsection.text
    assert "Kaitseministeeriumi valitsemisala valitsusasutust juurdepääsuõiguse andmisest" in subsection.text


def test_global_case_inflected_text_replace_handles_plural_a_noun_phrase_forms() -> None:
    body = _body_with_section_and_subsection(
        "23",
        "2",
        "Amet nõustab teabevaldajaid riigisaladuse kaitse tagamisel.",
    )
    op = LegalOperation(
        op_id="ee_test_plural_a_noun_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "teabevaldaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Amet nõustab töötlevaid üksusi riigisaladuse kaitse tagamisel."


def test_global_case_inflected_text_replace_uses_genitive_modifier_for_comitative_phrase() -> None:
    body = _body_with_section_and_subsection(
        "28",
        "2",
        "Kaitsetegevuse operatiivkava kehtestab Kaitseväe juhataja kooskõlastatult kaitseministriga üheks aastaks.",
    )
    op = LegalOperation(
        op_id="ee_test_minister_phrase_comitative",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkonna eest vastutav minister",
            attrs={
                "old_text": "kaitseminister",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Kaitsetegevuse operatiivkava kehtestab Kaitseväe juhataja "
        "kooskõlastatult valdkonna eest vastutava ministriga üheks aastaks."
    )


def test_global_text_replace_preserves_all_caps_heading_case() -> None:
    body = IRNode(
        kind=cast(Any, "statute"),
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                text="KAITSEMINISTER JA KAITSEMINISTEERIUM",
                children=(),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_uppercase_heading_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkonna eest vastutav minister",
            attrs={
                "old_text": "kaitseminister",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert chapter.text == "VALDKONNA EEST VASTUTAV MINISTER JA KAITSEMINISTEERIUM"


def test_generic_minister_plural_text_replace_collapses_lists_and_shared_head_pairs() -> None:
    body = IRNode(
        kind=cast(Any, "statute"),
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="4",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "Riigikaitse Nõukogu koosseisu kuuluvad peaminister, "
                                    "valdkonna eest vastutav minister, valdkonna eest vastutav minister "
                                    "ja valdkonna eest vastutav minister."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="5",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="10",
                                        text=(
                                            "otsustab, kui valdkonna eest vastutav minister või "
                                            "valdkonna eest vastutav minister on teinud ettepaneku;"
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="26",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "Enne heakskiitmist kuulavad välis- ja valdkonna eest vastutav minister "
                                    "ära komisjoni seisukohad."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_generic_minister_plural",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkondade eest vastutavad ministrid",
            attrs={
                "generic_minister_plural": True,
                "old_titles": [
                    "kaitseminister",
                    "välisminister",
                    "rahandusminister",
                    "siseminister",
                    "justiitsminister",
                ],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]
    item = result.children[1].children[0].children[0].children[0]
    shared_head = result.children[2].children[0].children[0]

    assert subsection.text == (
        "Riigikaitse Nõukogu koosseisu kuuluvad peaminister ja valdkondade eest vastutavad ministrid."
    )
    assert item.text == ("otsustab, kui valdkondade eest vastutavad ministrid on teinud ettepaneku;")
    assert shared_head.text == (
        "Enne heakskiitmist kuulavad valdkondade eest vastutavad ministrid ära komisjoni seisukohad."
    )


def test_generic_minister_plural_text_replace_collapses_redundant_tail_before_non_minister() -> None:
    body = _body_with_section_and_subsection(
        "4",
        "3",
        (
            "Riigikaitse Nõukogu koosseisu kuuluvad valdkonna eest vastutav minister, "
            "valdkonna eest vastutav minister, valdkonna eest vastutava ministri ning "
            "Kaitseväe juhataja."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_generic_minister_plural_tail",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkondade eest vastutavad ministrid",
            attrs={
                "generic_minister_plural": True,
                "old_titles": [
                    "kaitseminister",
                    "välisminister",
                    "siseminister",
                ],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Riigikaitse Nõukogu koosseisu kuuluvad valdkondade eest vastutavad ministrid "
        "ning Kaitseväe juhataja."
    )


def test_generic_minister_plural_text_replace_consumes_typed_metadata(monkeypatch) -> None:
    body = IRNode(
        kind=cast(Any, "statute"),
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=(
                                    "Enne heakskiitmist kuulavad välis- ja valdkonna eest vastutav minister "
                                    "ära komisjoni seisukohad."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_generic_minister_plural_typed",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkondade eest vastutavad ministrid",
            attrs={
                "generic_minister_plural": False,
                "old_titles": ["valepealkiri"],
            },
        ),
    )
    typed_instruction = EEParsedInstruction(
        family=EEInstructionFamily.text_replace,
        action=StructuralAction.TEXT_REPLACE,
        target=op.target,
        source_statute_id="ee/test",
        source_title="Testseadus",
        source_raw_text="typed generic minister plural",
        source_rule="test",
        payload_text=op.payload.text if op.payload is not None else "",
        rewrite=EETextRewrite(
            old_surface="valdkonna eest vastutav minister",
            new_surface="valdkondade eest vastutavad ministrid",
            generic_minister_plural=True,
            old_titles=("välisminister",),
            source_family="",
        ),
    )

    monkeypatch.setattr(
        grafter_module,
        "to_ee_parsed_instructions",
        lambda *args, **kwargs: [typed_instruction],
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Enne heakskiitmist kuulavad valdkondade eest vastutavad ministrid ära komisjoni seisukohad."
    )


def test_global_case_inflected_text_replace_handles_coordinated_old_phrase_variants() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        ("Riigisaladust valdava asutuse, põhiseadusliku institutsiooni või juriidilise isiku juht korraldab kaitset."),
    )
    op = LegalOperation(
        op_id="ee_test_coordinated_old_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "asutus, põhiseaduslik institutsioon või juriidiline isik",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Riigisaladust valdava töötleva üksuse juht korraldab kaitset."


def test_global_case_inflected_text_replace_handles_coordinated_person_list_variant() -> None:
    body = _body_with_section_and_subsection(
        "52",
        "1",
        (
            "Arvestust peetakse salastatud välisteabe ja seda valdavate asutuste, "
            "põhiseaduslike institutsioonide ning füüsiliste ja juriidiliste isikute üle."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_coordinated_person_list_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "asutus, põhiseaduslik institutsioon ja juriidiline isik",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Arvestust peetakse salastatud välisteabe ja seda valdavate töötlevate üksuste üle."


def test_global_case_inflected_text_replace_does_not_rewrite_partial_person_list_variant() -> None:
    text = (
        "Arvestust peetakse asutuste, põhiseaduslike institutsioonide ning "
        "füüsiliste isikute üle."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "asutus, põhiseaduslik institutsioon ja juriidiline isik",
        "töötlev üksus",
        case_inflected=True,
    )

    assert replaced == text


def test_targeted_text_replace_can_extend_shadowed_teabevaldajale_phrase() -> None:
    body = _body_with_section_and_subsection(
        "22",
        "3",
        "Amet võib teha töötlevale üksusele ettekirjutusi.",
    )
    op = LegalOperation(
        op_id="ee_test_shadowed_teabevaldajale_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "22"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlevale üksusele ja juurdepääsuõigusega füüsilisele isikule",
            attrs={"old_text": "teabevaldajale"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Amet võib teha töötlevale üksusele ja juurdepääsuõigusega füüsilisele isikule ettekirjutusi."
    )


def test_targeted_text_replace_can_extend_shadowed_teabevaldaja_subject_phrase() -> None:
    body = _body_with_section_and_subsection(
        "25",
        "1",
        "Töötlev üksus on kohustatud enne riigisaladusele juurdepääsu andmist kontrollima luba.",
    )
    op = LegalOperation(
        op_id="ee_test_shadowed_teabevaldaja_subject_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "25"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus ja juurdepääsuõigusega füüsiline isik",
            attrs={"old_text": "teabevaldaja"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Töötlev üksus ja juurdepääsuõigusega füüsiline isik on kohustatud "
        "enne riigisaladusele juurdepääsu andmist kontrollima luba."
    )


def test_global_case_inflected_text_replace_handles_genitive_before_poolt() -> None:
    body = _body_with_section_and_subsection(
        "52",
        "1",
        "teabevaldaja poolt salastatud välisteabe kaitse korralduse nõuetele vastavus.",
    )
    op = LegalOperation(
        op_id="ee_test_genitive_before_poolt",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="töötlev üksus",
            attrs={
                "old_text": "teabevaldaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.startswith("töötleva üksuse poolt salastatud välisteabe")


def test_case_inflected_text_replace_keeps_nominative_subject_before_k2esoleva_phrase() -> None:
    text = (
        "Kui ametliku kontrollimise koha määramist taotleb ettevõtja, teeb "
        "Põllumajandusamet käesoleva paragrahvi lõigetes 4 ja 5 sätestatud otsuse."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "Põllumajandusamet",
        "Põllumajandus- ja Toiduamet",
        case_inflected=True,
    )

    assert updated == (
        "Kui ametliku kontrollimise koha määramist taotleb ettevõtja, teeb "
        "Põllumajandus- ja Toiduamet käesoleva paragrahvi lõigetes 4 ja 5 sätestatud otsuse."
    )


def test_case_inflected_text_replace_keeps_nominative_before_k2esoleva_law_reference_tail() -> None:
    text = (
        "veeliikluses valdkonna eest vastutav minister käesoleva seaduse § 15 lõike 1 "
        "punktis 3 nimetatud laeva-, väikelaeva- ja parvlaevaliinile;"
    )

    updated = _ee_apply_text_replace_value(
        text,
        "valdkonna eest vastutav minister",
        "valdkonna eest vastutav minister",
        case_inflected=True,
    )

    assert updated == text


def test_case_inflected_text_replace_keeps_nominative_in_comma_coordination_list() -> None:
    text = (
        "Karistusseadustiku § 218 lõigetes 1 ja 2, §-s 275 ning § 325 lõikes 1 "
        "ettenähtud väärtegude kohtuväline menetleja on Politsei-ja Piirivalveamet, "
        "Justiitsministeerium ja vangla."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "Justiitsministeerium",
        "Justiits- ja Digiministeerium",
        case_inflected=True,
    )

    assert updated == (
        "Karistusseadustiku § 218 lõigetes 1 ja 2, §-s 275 ning § 325 lõikes 1 "
        "ettenähtud väärtegude kohtuväline menetleja on Politsei-ja Piirivalveamet, "
        "Justiits- ja Digiministeerium ja vangla."
    )


def test_global_case_inflected_text_replace_handles_coordinated_or_phrase_cases() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Dokumendid esitatakse registripidajale koos lisadega.",
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="21",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2_1",
                                text="Kui registripidajal on elektrooniline juurdepääs registrile, ei pea lisasid esitama.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_coordinated_or_phrase_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="perekonnaseisuametnik või perekonnaseisuasutuse ülesandeid täitev isik",
            attrs={
                "old_text": "registripidaja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section_8 = result.children[0].children[0].children[0]
    section_21 = result.children[0].children[1].children[0]

    assert "perekonnaseisuametnikule või perekonnaseisuasutuse ülesandeid täitvale isikule" in section_8.text
    assert "perekonnaseisuametnikul või perekonnaseisuasutuse ülesandeid täitval isikul" in section_21.text


def test_case_inflected_text_replace_handles_elukaaslane_genitive_family() -> None:
    text = (
        "Ühe abikaasa poolt eraldi esitatud maksejõuetusavalduse korral tuleb "
        "võlanimekirjas eraldi märkida kohustused, mille eest vastutab või võib "
        "vastutada ka teine abikaasa, samuti teise abikaasa kohustused, mille "
        "eest võib vastutada võlgnik."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "abikaasa",
        "abikaasa või registreeritud elukaaslane",
        case_inflected=True,
    )

    assert updated == (
        "Ühe abikaasa või registreeritud elukaaslase poolt eraldi esitatud "
        "maksejõuetusavalduse korral tuleb võlanimekirjas eraldi märkida "
        "kohustused, mille eest vastutab või võib vastutada ka teine abikaasa "
        "või registreeritud elukaaslane, samuti teise abikaasa või "
        "registreeritud elukaaslase kohustused, mille eest võib vastutada "
        "võlgnik."
    )


def test_case_inflected_insert_after_normalizes_spacing_before_genitive_followup() -> None:
    text = (
        "Kui täisealine ei suuda vaimuhaiguse, nõrgamõistuslikkuse või muu "
        "psüühikahäire tõttu kestvalt oma tegudest aru saada või neid juhtida, "
        "määrab kohus tema enda, tema vanema, abikaasa või täisealise lapse või "
        "valla- või linnavalitsuse avalduse alusel või omal algatusel talle "
        "eestkostja."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "abikaasa",
        "abikaasa , registreeritud elukaaslane",
        mode="insert_after",
        case_inflected=True,
    )

    assert updated == (
        "Kui täisealine ei suuda vaimuhaiguse, nõrgamõistuslikkuse või muu "
        "psüühikahäire tõttu kestvalt oma tegudest aru saada või neid juhtida, "
        "määrab kohus tema enda, tema vanema, abikaasa, registreeritud "
        "elukaaslase või täisealise lapse või valla- või linnavalitsuse "
        "avalduse alusel või omal algatusel talle eestkostja."
    )


def test_case_inflected_text_replace_does_not_genitivize_inside_allative_phrase_form() -> None:
    text = "Koolitusasutus esitab selle Põllumajandusametile heakskiitmiseks."

    updated = _ee_apply_text_replace_value(
        text,
        "Põllumajandusamet",
        "Põllumajandus- ja Toiduamet",
        case_inflected=True,
    )

    assert updated == "Koolitusasutus esitab selle Põllumajandus- ja Toiduametile heakskiitmiseks."


def test_sentence_scoped_text_replace_applies_multiple_typed_sentence_indexes() -> None:
    body = _body_with_section_and_subsection(
        "155",
        "1",
        (
            "Lapsendaja abikaasa esitab nõusoleku kohtule. "
            "Lapsendaja abikaasa võib avaldada oma nõusoleku notariaalselt."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_sentence_scoped_note_indexes",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "155"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="lapsendaja abikaasa või registreeritud elukaaslane",
            attrs={
                "old_text": "lapsendaja abikaasa",
                "rewrite_mode": "replace",
                "sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0, 1)),
            },
        ),
        provenance_tags=(
            "paragrahvi 155 lõike 1 esimest ja teist lauset täiendatakse pärast sõnu "
            "„lapsendaja abikaasa” sõnadega „või registreeritud elukaaslane”;",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Lapsendaja abikaasa või registreeritud elukaaslane esitab nõusoleku "
        "kohtule. Lapsendaja abikaasa või registreeritud elukaaslane võib "
        "avaldada oma nõusoleku notariaalselt."
    )


def test_global_case_inflected_text_replace_handles_amet_and_ministeerium_forms() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Põllumajandusametile esitatakse taotlus Maaeluministeeriumi kaudu.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op_1 = LegalOperation(
        op_id="ee_test_amet_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Põllumajandus- ja Toiduamet",
            attrs={"old_text": "Põllumajandusamet", "case_inflected": True},
        ),
    )
    op_2 = LegalOperation(
        op_id="ee_test_ministeerium_case",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Regionaal- ja Põllumajandusministeerium",
            attrs={"old_text": "Maaeluministeerium", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op_1)
    result = _ee_apply_op(result, op_2)
    subsection = result.children[0].children[0].children[0]

    assert "Põllumajandus- ja Toiduametile" in subsection.text
    assert "Regionaal- ja Põllumajandusministeeriumi" in subsection.text


def test_case_inflected_text_replace_keeps_source_lowercase_for_mid_sentence_institution_rename() -> None:
    replaced = _ee_apply_text_replace_value(
        (
            "Kutsesaladuse rikkumiseks ei peeta Justiitsministeeriumile andmete "
            "avaldamist seoses järelevalvega pankrotihaldurina tegutsemise asjades."
        ),
        "Justiitsministeerium",
        "maksejõuetuse teenistus",
        case_inflected=True,
    )

    assert replaced is not None
    assert "maksejõuetuse teenistusele" in replaced
    assert "Maksejõuetuse teenistusele" not in replaced


def test_global_case_inflected_text_replace_handles_ameti_kohalik_asutus_phrase_forms() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="46",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="4",
                                text=(
                                    "Sertifikaadi saamiseks esitatakse Ameti kohalikule asutusele "
                                    "kirjalik taotlus ja sertifikaadi koopiat säilitatakse "
                                    "Ameti kohalikus asutuses kolm aastat."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_ameti_kohalik_asutus_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Amet",
            attrs={"old_text": "Ameti kohalik asutus", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Ametile" in subsection.text
    assert "Ametis kolm aastat" in subsection.text


def test_global_case_inflected_text_replace_handles_ameti_kohaliku_asutuse_juht_phrase_forms() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="47",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=(
                                    "Sertifikaadi väljaandmise õigus on Ameti kohaliku asutuse "
                                    "juhi poolt selleks volitatud veterinaarjärelevalve ametnikul."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_ameti_kohaliku_asutuse_juht_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Ameti peadirektor",
            attrs={"old_text": "Ameti kohaliku asutuse juht", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Ameti peadirektori poolt" in subsection.text


def test_ee_apply_text_replace_prefers_genitive_over_partitive_on_phrase_collision() -> None:
    text = (
        "Sertifikaadi väljaandmise õigus on Ameti kohaliku asutuse juhi poolt "
        "selleks volitatud veterinaarjärelevalve ametnikul."
    )

    updated = _ee_apply_text_replace_value(
        text,
        "Ameti kohaliku asutuse juht",
        "Ameti peadirektor",
        case_inflected=True,
    )

    assert updated is not None
    assert "Ameti peadirektori poolt" in updated
    assert "Ameti peadirektorit poolt" not in updated


def test_ee_apply_text_replace_keeps_lowercase_item_replacement_fragment() -> None:
    text = "Välisministeerium Vabariigi Valitsusele, kui välislepingu on sõlminud Vabariigi Valitsus."

    updated = _ee_apply_text_replace_value(
        text,
        "Välisministeerium",
        "välislepingu sõlmimise algatanud ministeerium või Riigikantselei",
        case_inflected=False,
        capitalize_sentence_start=False,
    )

    assert updated is not None
    assert updated.startswith("välislepingu sõlmimise algatanud ministeerium või Riigikantselei")


def test_ee_apply_text_replace_matches_range_text_across_dash_variants() -> None:
    updated = _ee_apply_text_replace_value(
        "vastab käesoleva seaduse § 36 lõigetes 2‒5 sätestatud nõuetele",
        "2–5",
        "2–4",
        case_inflected=False,
    )

    assert updated == "vastab käesoleva seaduse § 36 lõigetes 2–4 sätestatud nõuetele"


def test_global_case_inflected_text_replace_handles_hyphenated_aastane_forms() -> None:
    body = _body_with_section_and_subsection(
        "34",
        "1",
        (
            "Riigisisesel liinil tee-, vee-ja raudteeliikluses on vedaja kohustatud tasuta vedama "
            "puudega kuni 16-aastast isikut, sügava puudega 16-aastast ja vanemat isikut."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_hyphenated_aastane",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="18-aastane",
            attrs={"old_text": "16-aastane", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "puudega kuni 18-aastast isikut" in subsection.text
    assert "sügava puudega 18-aastast ja vanemat isikut" in subsection.text


def test_global_case_inflected_text_replace_handles_meri_irregular_forms() -> None:
    body = _body_with_section_and_subsection(
        "187",
        "1",
        (
            "süvendatakse veekogu või paigutatakse veekogu põhja süvenduspinnast mahuga alates 100 kuupmeetrist; "
            "paigutatakse veekogusse tahkeid aineid mahuga alates 100 kuupmeetrist;"
        ),
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_veekogu_meri",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="meri",
            attrs={"old_text": "veekogu", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "süvendatakse merd" in subsection.text
    assert "mere põhja" in subsection.text
    assert "paigutatakse merre" in subsection.text
    assert "veekogu" not in subsection.text


def test_ee_apply_text_replace_handles_ning_coordinated_phrase_inflection() -> None:
    text = "Andmed avalikustatakse Põllumajandusameti ning Veterinaar- ja Toiduameti veebilehel."

    updated = _ee_apply_text_replace_value(
        text,
        "Põllumajandusamet ning Veterinaar- ja Toiduamet",
        "Põllumajandus- ja Toiduamet",
        case_inflected=True,
    )

    assert updated is not None
    assert updated == "Andmed avalikustatakse Põllumajandus- ja Toiduameti veebilehel."


def test_item_replace_preserves_bare_conjunction_terminal() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="25",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="muudatus ei ole sisulist laadi;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="teine punkt."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_item_replace_preserve_ja",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "25"), ("subsection", "2"), ("item", "1"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="muudatus ei vaja käesoleva seaduse § 20 kohast ratifitseerimist ja"),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0].children[0]
    assert item.text == "muudatus ei vaja käesoleva seaduse § 20 kohast ratifitseerimist ja"


def test_global_case_inflected_text_replace_does_not_reapply_inside_inserted_suffix() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="6",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text=(
                                    "Tehnilise Järelevalve Ametil on õigus keelduda. "
                                    "Tehnilise Järelevalve Amet informeerib ettevõtjat."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_no_self_reapply_inside_inserted_suffix",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Tarbijakaitse ja Tehnilise Järelevalve Amet",
            attrs={"old_text": "Tehnilise Järelevalve Amet", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Tarbijakaitse ja Tehnilise Järelevalve Ametil on õigus keelduda. "
        "Tarbijakaitse ja Tehnilise Järelevalve Amet informeerib ettevõtjat."
    )


def test_global_case_inflected_text_replace_handles_maavanem_forms() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="8",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="4",
                                text=(
                                    "Maavanema ülesanded on järgmised. "
                                    "Asjaomane volikogu esitab maavanemale dokumendid "
                                    "ja nõuab neid vajaduse korral maavanemalt."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_maavanem_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Rahandusministeerium",
            attrs={"old_text": "maavanem", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "Rahandusministeeriumi ülesanded" in subsection.text
    assert "Rahandusministeeriumile dokumendid" in subsection.text
    assert "Rahandusministeeriumilt" in subsection.text


def test_global_case_inflected_text_replace_handles_ioon_family_forms() -> None:
    body = _body_with_section_and_subsection(
        "7",
        "1",
        (
            "Keskkonnainspektsioonil on õigus nõuda ärakirja, "
            "Keskkonnainspektsiooni kirjaliku ettepaneku alusel ning "
            "Keskkonnainspektsiooniga kooskõlastatult."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_ioon_family_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Keskkonnaamet",
            attrs={"old_text": "Keskkonnainspektsioon", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Keskkonnaametil on õigus nõuda ärakirja, "
        "Keskkonnaameti kirjaliku ettepaneku alusel ning "
        "Keskkonnaametiga kooskõlastatult."
    )


def test_global_case_inflected_text_replace_handles_ambiguous_ioon_object_after_teavitab() -> None:
    body = _body_with_section_and_subsection(
        "6",
        "3",
        "teavitab Keskkonnainspektsiooni keskkonda kahjustavast tegevusest.",
    )
    op = LegalOperation(
        op_id="ee_test_ioon_partitive_object",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Keskkonnaamet",
            attrs={"old_text": "Keskkonnainspektsioon", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "teavitab Keskkonnaametit keskkonda kahjustavast tegevusest."


def test_global_case_inflected_text_replace_handles_mine_to_olu_phrase_family() -> None:
    body = _body_with_section_and_subsection(
        "80",
        "1",
        "kontrollib kindlustuskohustuse täitmist politseiametnik.",
    )
    op = LegalOperation(
        op_id="ee_test_mine_to_olu_phrase_family",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="liikluskindlustuse olemasolu",
            attrs={"old_text": "kindlustuskohustuse täitmine", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "kontrollib liikluskindlustuse olemasolu politseiametnik."


def test_global_case_inflected_text_replace_handles_tud_modifier_phrase() -> None:
    body = _body_with_section_and_subsection(
        "46",
        "1",
        "tuvastamata jäänud sõidukiga põhjustatud kahju hüvitab fond.",
    )
    op = LegalOperation(
        op_id="ee_test_tud_modifier_phrase",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kindlustuskohustusega hõlmatud sõiduk",
            attrs={"old_text": "sõiduk", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert (
        subsection.text == "tuvastamata jäänud kindlustuskohustusega hõlmatud sõidukiga põhjustatud kahju hüvitab fond."
    )


def test_global_case_inflected_text_replace_handles_line_adjective_forms() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="41",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "Kui Põllumajandus-ja Toiduametil ei ole võimalik "
                                    "saastumiskahtlust kindlaks teha, peatab ta "
                                    "kaubasaadetise ühendusevälisest riigist Eestisse "
                                    "toimetamise ja võib anda saastumata osa "
                                    "ühendusevälisesse riiki tagasi."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_line_case",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="liiduväline",
            attrs={"old_text": "ühenduseväline", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "liiduvälisest riigist" in subsection.text
    assert "liiduvälisesse riiki" in subsection.text


def test_case_preserving_replace_keeps_lowercase_common_noun_mid_sentence() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="44_4",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="5",
                                text=("Enammakstud järelevalvetasu tagastamise korra kehtestab Vabariigi Valitsus."),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_mid_sentence_lowercase_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "44_4"), ("subsection", "5"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="valdkonna eest vastutav minister määrusega",
            attrs={"old_text": "Vabariigi Valitsus"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Enammakstud järelevalvetasu tagastamise korra kehtestab valdkonna eest vastutav minister määrusega."
    )


def test_text_replace_second_sentence_scope_only_rewrites_target_sentence() -> None:
    body = _body_with_section_and_subsection(
        "9",
        "5",
        (
            "Ohtliku taimekahjustaja puhul, mille liigile kohased tõrjeabinõud on "
            "kehtestatud käesoleva paragrahvi lõike 4 alusel, otsustab asutus. "
            "Ohtliku taimekahjustaja puhul, mille liigile kohaseid tõrjeabinõusid ei "
            "ole õigusaktiga kehtestatud, otsustab asutus."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_second_sentence_text_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "9"), ("subsection", "5"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kehtestatud või mille puhul ei ole kehtestatud tõrjeabinõu kohaldamine osutunud tõhusaks",
            attrs={"old_text": "kehtestatud"},
        ),
        provenance_tags=("paragrahvi 9 lõike 5 teist lauset täiendatakse pärast sõna „kehtestatud” sõnadega „...”",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.startswith(
        "Ohtliku taimekahjustaja puhul, mille liigile kohased tõrjeabinõud on "
        "kehtestatud käesoleva paragrahvi lõike 4 alusel"
    )
    assert (
        "mille liigile kohaseid tõrjeabinõusid ei ole õigusaktiga "
        "kehtestatud või mille puhul ei ole kehtestatud tõrjeabinõu "
        "kohaldamine osutunud tõhusaks"
    ) in subsection.text


def test_case_inflected_phrase_deletion_handles_inflected_old_forms() -> None:
    body = _body_with_section_and_subsection(
        "46_1",
        "3",
        "Tööinspektoril või Tööinspektsiooni kohaliku asutuse juhatajal on õigus teha ettekirjutus.",
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_phrase_deletion",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "46_1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "old_text": "või Tööinspektsiooni kohaliku asutuse juhataja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Tööinspektoril on õigus teha ettekirjutus."


def test_case_inflected_text_replace_delete_handles_vabaladu_forms() -> None:
    text = (
        "toimetamist ühendusevälisest riigist Euroopa Liidu territooriumil "
        "asuvasse vabatsooni, vabalattu või tollilattu; "
        "kaubasaadetis paigutatakse vabatsoonis, vabalaos või tollilaos ning "
        "eemaldatakse vabatsoonist, vabalaost või tollilaost."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        ", vabaladu",
        "",
        case_inflected=True,
    )

    assert replaced == (
        "toimetamist ühendusevälisest riigist Euroopa Liidu territooriumil "
        "asuvasse vabatsooni või tollilattu; "
        "kaubasaadetis paigutatakse vabatsoonis või tollilaos ning "
        "eemaldatakse vabatsoonist või tollilaost."
    )


def test_text_replace_delete_without_case_inflection_keeps_vabaladu_forms() -> None:
    text = "toimetamist vabatsooni, vabalattu või tollilattu."

    replaced = _ee_apply_text_replace_value(
        text,
        ", vabaladu",
        "",
        case_inflected=False,
    )

    assert replaced == text


def test_text_replace_handles_inflected_paragraph_marker_citations() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="18",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="1",
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.ITEM,
                                                label="13",
                                                text="kindlustusandja sise-eeskirjad vastavalt käesoleva seaduse §-le 84 või nende projekt;",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="23",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.SUBSECTION,
                                        label="1",
                                        children=(
                                            IRNode(
                                                kind=IRNodeKind.ITEM,
                                                label="5",
                                                text="käesoleva seaduse §-s 84 nimetatud kindlustusandja sise-eeskirjad ei ole piisavalt täpsed;",
                                            ),
                                        ),
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    ops = [
        LegalOperation(
            op_id="ee_test_section_reference_replace_1",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "18"), ("subsection", "1"), ("item", "13"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="§ 47 7",
                attrs={"old_text": "§ 84"},
            ),
        ),
        LegalOperation(
            op_id="ee_test_section_reference_replace_2",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "23"), ("subsection", "1"), ("item", "5"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="§ 47 7",
                attrs={"old_text": "§ 84"},
            ),
        ),
    ]

    result = body
    for op in ops:
        result = _ee_apply_op(result, op)
    item_13 = result.children[0].children[0].children[0].children[0].children[0]
    item_5 = result.children[0].children[0].children[1].children[0].children[0]

    assert "§-le 47 7" in item_13.text
    assert "§-s 47 7" in item_5.text


def test_text_replace_on_subsection_target_rewrites_all_descendant_items() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="12",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Sissejuhatav tekst.",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="6", text="õppemaksu tasumise kord;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="7", text="õppemaksu tagastamise kord;"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_subtree_replace_all",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "12"), ("subsection", "1"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="õppetasu", attrs={"old_text": "õppemaksu"}),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[0].text == "õppetasu tasumise kord;"
    assert subsection.children[1].text == "õppetasu tagastamise kord;"


def test_text_replace_on_subsection_target_normalizes_numeric_range_spacing() -> None:
    body = _body_with_section_and_subsection(
        "74_35",
        "1",
        (
            "Mootorsõiduki- või trammijuhi poolt liiklusnõuete rikkumise eest, kui puudub "
            "käesoleva seaduse §-des 74 1–74 27 või 74 30–74 32 sätestatud "
            "väärteokoosseis – karistatakse rahatrahviga kuni 50 trahviühikut."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_range_spacing_text_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "74_35"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="§-des 74 1 –74 27 , 74 30 –74 32 või 74 64",
            attrs={"old_text": "§-des 74 1 –74 27 või 74 30 –74 32"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert "§-des 74 1–74 27, 74 30–74 32 või 74 64 sätestatud väärteokoosseis" in subsection.text


def test_text_replace_on_subsection_target_tolerates_minus_sign_range_surface() -> None:
    body = _body_with_section_and_subsection(
        "93",
        "7",
        "Käesoleva paragrahvi lõigetes 1 1−4 2 ja 5−7 sätestatud korras.",
    )
    op = LegalOperation(
        op_id="ee_test_minus_sign_range_delete",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "93"), ("subsection", "7"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"old_text": "ja 5–7"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Käesoleva paragrahvi lõigetes 1 1−4 2 sätestatud korras."


def test_text_replace_on_subsection_target_tolerates_hyphen_spacing_surface() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "3",
        "Erinevused võivad olla ohtlikud rahvatervisele või-ohutusele.",
    )
    op = LegalOperation(
        op_id="ee_test_hyphen_surface_text_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "20"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="rahvastiku tervisele ja vähendada rahvastiku ohutust",
            attrs={"old_text": "rahvatervisele või -ohutusele"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]


def test_heading_replace_on_part_qualified_chapter_targets_correct_part() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.PART,
                label="6",
                text="6. osa VÕLAÕIGUS",
                children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", text="VÕLAÕIGUSE ÜLDSÄTTED JA LEPINGUD"),),
            ),
            IRNode(
                kind=IRNodeKind.PART,
                label="7",
                text="7. osa PEREKONNAÕIGUS",
                children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", text="ABIELU"),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_part_qualified_chapter_heading",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("part", "7"), ("chapter", "1")), special=FacetKind.HEADING),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="7. osa PEREKONNAÕIGUS 1. peatükk ABIELU JA REGISTREERITUD KOOSELU",
        ),
    )

    result = _ee_apply_op(body, op)

    assert result.children[0].children[0].text == "VÕLAÕIGUSE ÜLDSÄTTED JA LEPINGUD"
    assert result.children[1].children[0].text == "ABIELU JA REGISTREERITUD KOOSELU"


def test_text_replace_single_word_does_not_overmatch_inside_hyphen_compound() -> None:
    body = _body_with_section_and_subsection(
        "2",
        "2",
        "Hoiu-laenuühistutele kohaldatakse ühistute kohta sätestatut.",
    )
    op = LegalOperation(
        op_id="ee_test_single_word_no_compound_overmatch",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "2"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="tulundusühistute",
            attrs={"old_text": "ühistute"},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Hoiu-laenuühistutele kohaldatakse tulundusühistute kohta sätestatut."


def test_text_replace_case_inflected_is_word_family() -> None:
    body = _body_with_section_and_subsection(
        "12_1",
        "3",
        "See ohustab märkimisväärselt rahvatervist ja võib olla seotud rahvatervisega.",
    )
    op = LegalOperation(
        op_id="ee_test_case_inflected_is_family",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "12_1"), ("subsection", "3"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="rahvastiku tervis",
            attrs={"old_text": "rahvatervis", "case_inflected": True},
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "See ohustab märkimisväärselt rahvastiku tervist ja võib olla seotud rahvastiku tervisega."
    )


def test_section_text_replace_phrase_removal_normalizes_descendant_spacing() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="9",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="46_1",
                        text="Sõidukijuhi töö-, sõidu- ja puhkeaja nõuete täitmise järelevalve",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text=(
                                    "Järelevalvet teostavad tööinspektor või "
                                    "Tööinspektsiooni kohaliku asutuse juhataja ning teel "
                                    "politseiametnikud."
                                ),
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "Tööinspektoril või Tööinspektsiooni kohaliku asutuse "
                                    "juhatajal on õigus teha ettekirjutus."
                                ),
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3_1",
                                text=("Tööinspektor või Tööinspektsiooni kohaliku asutuse juhataja teeb otsuse."),
                            ),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="4",
                                text=(
                                    "Käesoleva paragrahvi lõikes 3 nimetatud ettekirjutuse "
                                    "tähtajaks täitmata jätmise korral võib tööinspektor või "
                                    "Tööinspektsiooni kohaliku asutuse juhataja rakendada "
                                    "sunniraha."
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_section_text_replace_phrase_removal",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "46_1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={
                "old_text": "või Tööinspektsiooni kohaliku asutuse juhataja",
                "case_inflected": True,
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert [child.text for child in section.children] == [
        "Järelevalvet teostavad tööinspektor ning teel politseiametnikud.",
        "Tööinspektoril on õigus teha ettekirjutus.",
        "Tööinspektor teeb otsuse.",
        "Käesoleva paragrahvi lõikes 3 nimetatud ettekirjutuse tähtajaks täitmata jätmise korral võib tööinspektor rakendada sunniraha.",
    ]


def test_global_text_replace_can_be_scoped_to_selected_chapters() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Täienduskoolitusasutuse pidaja tegutseb."),
                        ),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="7",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="20",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Täienduskoolitusasutuse pidaja jääb muutmata."),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_scoped_global_replace",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="täienduskoolitusasutus",
            attrs={
                "old_text": "täienduskoolitusasutuse pidaja",
                "case_inflected": True,
                "scope_chapters": ["1", "2", "3", "4", "5", "6"],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    sub_ch1 = result.children[0].children[0].children[0]
    sub_ch7 = result.children[1].children[0].children[0]

    assert sub_ch1.text == "Täienduskoolitusasutus tegutseb."
    assert sub_ch7.text == "Täienduskoolitusasutuse pidaja jääb muutmata."


def test_insert_division_one_wraps_existing_chapter_sections_with_title() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                text="Täienduskoolituse läbiviimine ja teabe avalikustamine",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="7", text="S7"),
                    IRNode(kind=IRNodeKind.SECTION, label="8", text="S8"),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_division_one",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "3"), ("division", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="1. jagu Täienduskoolituse läbiviimise nõuded ja teabe avalikustamine",
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert len(chapter.children) == 1
    division = chapter.children[0]
    assert division.kind == IRNodeKind.DIVISION
    assert division.label == "1"
    assert division.text == "Täienduskoolituse läbiviimise nõuded ja teabe avalikustamine"
    assert [(c.kind, c.label) for c in division.children] == [
        (IRNodeKind.SECTION, "7"),
        (IRNodeKind.SECTION, "8"),
    ]


def test_replace_chapter_materializes_structured_chapter_payload() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="10",
                text="VANA PEATÜKK",
                children=(IRNode(kind=IRNodeKind.SECTION, label="53_5", text="Vana jagu", children=()),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_chapter",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "10"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "10. peatükk RIIKLIK JÄRELEVALVE JA ERISÄTTED "
                "§ 53 5. Riiklik järelevalve "
                "(1) Järelevalve käib siin. "
                "§ 53 6. Riikliku järelevalve erimeetmed "
                "(1) Erimeede käib siin."
            ),
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("chapter replace",),
    )

    result = _ee_apply_op(body, op)

    chapter = result.children[0]
    assert chapter.kind == IRNodeKind.CHAPTER
    assert chapter.label == "10"
    assert chapter.text == "RIIKLIK JÄRELEVALVE JA ERISÄTTED"
    assert [child.label for child in chapter.children] == ["53_5", "53_6"]
    assert chapter.children[0].children[0].text == "Järelevalve käib siin."


def test_insert_superscript_section_prefers_split_chapter_with_best_predecessor() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="10",
                text="JÄRELEVALVE",
                children=(IRNode(kind=IRNodeKind.SECTION, label="54", text="§ 54", children=()),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="10_1",
                text="VASTUTUS",
                children=(IRNode(kind=IRNodeKind.SECTION, label="54_11", text="§ 54 11", children=()),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_split_chapter_predecessor",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "10"), ("section", "54_12"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="§ 54 12. Uus koosseis (1) Uus tekst.",
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("chapter-qualified insert",),
    )

    result = _ee_apply_op(body, op)

    chapter_10 = result.children[0]
    chapter_10_1 = result.children[1]
    assert [child.label for child in chapter_10.children] == ["54"]
    assert [child.label for child in chapter_10_1.children] == ["54_11", "54_12"]
    assert chapter_10_1.children[1].children[0].text == "Uus tekst."


def test_insert_superscript_section_does_not_jump_to_different_split_chapter_family() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3_1",
                text="VASTUTUS",
                children=(IRNode(kind=IRNodeKind.SECTION, label="23_8", text="Menetlus", children=()),),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                text="LÕPPSÄTTED",
                children=(IRNode(kind=IRNodeKind.SECTION, label="24", text="Lõppsäte", children=()),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_section_keeps_explicit_chapter_family",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "4"), ("section", "23_9"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="§ 23 9. Riiklik järelevalve (1) Uus tekst.",
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("chapter-qualified insert",),
    )

    result = _ee_apply_op(body, op)

    chapter_3_1 = result.children[0]
    chapter_4 = result.children[1]
    assert [child.label for child in chapter_3_1.children] == ["23_8"]
    assert [child.label for child in chapter_4.children] == ["23_9", "24"]
    assert chapter_4.children[0].children[0].text == "Uus tekst."


def test_insert_flat_section_prefers_nested_same_base_parent_in_mixed_statute() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.SECTION, label="1", text="Flat root section"),
            IRNode(
                kind=IRNodeKind.PART,
                label="8",
                children=(
                    IRNode(
                        kind=IRNodeKind.CHAPTER,
                        label="40",
                        children=(
                            IRNode(
                                kind=IRNodeKind.DIVISION,
                                label="2",
                                children=(
                                    IRNode(kind=IRNodeKind.SECTION, label="711", text="Olemasolev 711"),
                                    IRNode(kind=IRNodeKind.SECTION, label="720", text="Olemasolev 720"),
                                    IRNode(kind=IRNodeKind.SECTION, label="721", text="Olemasolev 721"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_flat_section_nested_parent_in_mixed_body",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "720_1"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="§ 720 1 . Põhimakseteenuse lepingu ülesütlemine\x01 (1) Test.",
        ),
    )

    result = _ee_apply_op(body, op)

    direct_sections = [child.label for child in result.children if child.kind == IRNodeKind.SECTION]
    division = result.children[1].children[0].children[0]
    division_sections = [child.label for child in division.children if child.kind == IRNodeKind.SECTION]

    assert direct_sections == ["1"]
    assert division_sections == ["711", "720", "720_1", "721"]
    assert division.children[2].text == "Põhimakseteenuse lepingu ülesütlemine"


def test_insert_superscript_section_keeps_explicit_division_parent() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                text="Vana peatükk",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="3",
                        text="Vana jagu",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="47", text="§ 47", children=()),),
                    ),
                ),
            ),
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                text="Uus peatükk",
                children=(IRNode(kind=IRNodeKind.DIVISION, label="1", text="Uus jagu", children=()),),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_division_qualified_superscript_section",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "3"), ("division", "1"), ("section", "47_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="§ 47 1. Üldised põhimõtted (1) Uus tekst.",
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("division-qualified insert",),
    )

    result = _ee_apply_op(body, op)

    old_division = result.children[0].children[0]
    new_division = result.children[1].children[0]
    assert [child.label for child in old_division.children] == ["47"]
    assert [child.label for child in new_division.children] == ["47_1"]
    assert new_division.children[0].text == "Üldised põhimõtted"
    assert new_division.children[0].children[0].text == "Uus tekst."


def test_insert_into_existing_item_appends_sentence_instead_of_replacing_text() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Mõisted:",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="1",
                                        text="Senine esimene lause;",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_existing_item_sentence",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"), ("item", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Uus neljas lause.",
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("punkti 1 täiendatakse neljanda lausega",),
    )

    result = _ee_apply_op(body, op)

    item = result.children[0].children[0].children[0].children[0]
    assert item.text == "Senine esimene lause. Uus neljas lause;"


def test_insert_into_existing_subsection_appends_full_sentence_instead_of_duplicate_label() -> None:
    body = _body_with_section_and_subsection(
        "11",
        "1",
        "Täiskasvanute koolitaja on käesoleva seaduse tähenduses spetsialist.",
    )
    op = LegalOperation(
        op_id="ee_test_append_sentence_to_existing_subsection",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "11"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Täiskasvanute koolitajal peavad olema koolitatavas valdkonnas erialased kompetentsid.",
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert len(section.children) == 1
    assert section.children[0].label == "1"
    assert section.children[0].text == (
        "Täiskasvanute koolitaja on käesoleva seaduse tähenduses spetsialist. "
        "Täiskasvanute koolitajal peavad olema koolitatavas valdkonnas erialased kompetentsid."
    )


def test_insert_existing_item_prefers_typed_prepend_mode_over_note_text() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Mõisted:",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="1",
                                        text="Senine esimene lause;",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_existing_item_typed_prepend",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"), ("item", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Uus algus.",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(), mode="prepend_item")},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("punkti 1 täiendatakse neljanda lausega",),
    )

    result = _ee_apply_op(body, op)

    item = result.children[0].children[0].children[0].children[0]
    assert item.text == "Uus algus. Senine esimene lause;"


def test_insert_into_existing_item_does_not_prepend_from_provenance_tags_without_typed_sentence_target_meta() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="1",
                                        text="Senine esimene lause;",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_existing_item_note_only_append",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "2"), ("item", "1"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Uus algus."),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("punkti 1 algust täiendatakse järgmises sõnastuses",),
    )

    result = _ee_apply_op(body, op)

    item = result.children[0].children[0].children[0].children[0]
    assert item.text == "Senine esimene lause. Uus algus;"


def test_insert_into_existing_subsection_after_first_sentence() -> None:
    body = _body_with_section_and_subsection(
        "16",
        "6",
        "Esimene lause. Teine lause. Kolmas lause.",
    )
    op = LegalOperation(
        op_id="ee_test_insert_after_first_sentence",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "16"), ("subsection", "6"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Lisatud lause."),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 16 lõiget 6 täiendatakse pärast esimest lauset lausega järgmises sõnastuses",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause. Lisatud lause. Teine lause. Kolmas lause."


def test_insert_into_existing_subsection_after_first_sentence_prefers_typed_sentence_target_meta() -> None:
    body = _body_with_section_and_subsection(
        "16",
        "6",
        "Esimene lause. Teine lause. Kolmas lause.",
    )
    op = LegalOperation(
        op_id="ee_test_insert_after_first_sentence_typed",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "16"), ("subsection", "6"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Lisatud lause.",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,), mode="insert_after")},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 16 lõiget 6 täiendatakse esimese lausega järgmises sõnastuses",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause. Lisatud lause. Teine lause. Kolmas lause."


def test_insert_into_existing_subsection_after_first_sentence_with_plural_lausetega_note_does_not_take_sentence_scoped_branch_without_typed_meta() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        "Esimene lause. Teine lause.",
    )
    op = LegalOperation(
        op_id="ee_test_insert_after_first_sentence_lausetega",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Lisatud teine lause. Lisatud kolmas lause."),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 20 lõiget 2 täiendatakse pärast esimest lauset lausetega järgmises sõnastuses",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause. Teine lause. Lisatud teine lause. Lisatud kolmas lause."


def test_insert_into_existing_subsection_before_existing_text_when_old_text_becomes_second_sentence() -> None:
    body = _body_with_section_and_subsection(
        "9",
        "5",
        (
            "Korteriomanik võib asjassepuutuva piiratud asjaõiguse omajalt nõuda "
            "käesoleva paragrahvi lõikes 1 nimetatud muudatuse tegemiseks "
            "vajalike tahteavalduste andmist, kui piiratud asjaõiguse omaja "
            "õigustatud huve ei kahjustata muudatusega ülemääraselt."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_before_existing_sentence",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "9"), ("subsection", "5"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Eriomandi kokkuleppe muudatuse kandmiseks kinnistusraamatusse on "
                "vajalik asjassepuutuva piiratud asjaõiguse omaja kui "
                "kinnistusraamatuseaduse tähenduses puudutatud isiku nõusolek."
            ),
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=(
            "paragrahvi 9 lõike 5 tekst loetakse teiseks lauseks ja lõiget "
            "täiendatakse esimese lausega järgmises sõnastuses",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.startswith("Eriomandi kokkuleppe muudatuse kandmiseks kinnistusraamatusse on vajalik")
    assert "Korteriomanik võib asjassepuutuva piiratud asjaõiguse omajalt nõuda" in subsection.text


def test_insert_into_existing_subsection_before_existing_text_prefers_typed_sentence_target_meta() -> None:
    body = _body_with_section_and_subsection(
        "9",
        "5",
        (
            "Korteriomanik võib asjassepuutuva piiratud asjaõiguse omajalt nõuda "
            "käesoleva paragrahvi lõikes 1 nimetatud muudatuse tegemiseks "
            "vajalike tahteavalduste andmist."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_before_existing_sentence_typed",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "9"), ("subsection", "5"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Eriomandi kokkuleppe muudatuse kandmiseks kinnistusraamatusse on vajalik nõusolek.",
            attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,), mode="insert_before")},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 9 lõike 5 tekst loetakse kolmandaks lauseks",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text.startswith("Eriomandi kokkuleppe muudatuse kandmiseks kinnistusraamatusse on vajalik")


def test_text_replace_scoped_to_first_sentence_recognizes_esimeses_lauses_note() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        "Kindlustusandja teavitab kindlustusvõtjat. Kindlustusandja pakkumus ei või olla tingimuslik.",
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_first_sentence_esimeses_lauses",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="kindlustuse turustaja",
            attrs={"old_text": "kindlustusandja"},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=(
            "paragrahvi 20 lõike 2 esimeses lauses asendatakse sõna „kindlustusandja” sõnadega „kindlustuse turustaja”",
        ),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Kindlustuse turustaja teavitab kindlustusvõtjat. Kindlustusandja pakkumus ei või olla tingimuslik."
    )


def test_text_replace_scoped_to_first_sentence_does_not_fallback_to_second_sentence() -> None:
    body = _body_with_section_and_subsection(
        "99",
        "1",
        (
            "Esimene lause jääb alles. Teises lauses kustutatav tekst jääks alles, "
            "kui allikas nimetab esimese lause."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_first_sentence_no_broader_fallback",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "99"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"old_text": "kustutatav tekst"},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 99 lõike 1 esimesest lausest jäetakse välja tekstiosa",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Esimene lause jääb alles. Teises lauses kustutatav tekst jääks alles, "
        "kui allikas nimetab esimese lause."
    )


def test_text_replace_scoped_to_first_sentence_ignores_ordinal_periods() -> None:
    body = _body_with_section_and_subsection(
        "12",
        "2",
        (
            "Vajaduse korral tellitakse hindamine kehtiv 7. taseme hindaja kutsega "
            "isikult või selgitab väärtuse välja Maa-amet. Teine lause jääb alles."
        ),
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_first_sentence_ordinal_period",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "12"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Maa- ja Ruumiamet",
            attrs={
                "old_text": "Maa-amet",
                "sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,)),
            },
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 12 lõike 2 esimeses lauses asendatakse tekstiosa",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Vajaduse korral tellitakse hindamine kehtiv 7. taseme hindaja kutsega "
        "isikult või selgitab väärtuse välja Maa- ja Ruumiamet. Teine lause jääb alles."
    )


def test_text_replace_scoped_to_first_sentence_prefers_typed_sentence_target_meta_over_note_text() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        "Alpha. Beta.",
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_prefers_typed_sentence_target_meta",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="rewrite",
            attrs={
                "old_text": "Alpha",
                "sentence_target_meta": make_sentence_target_meta(sentence_indexes=(0,)),
            },
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 20 lõike 2 teises lauses asendatakse sõna „Alpha” sõnaga „rewrite”",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Rewrite. Beta."


def test_text_replace_scoped_to_first_sentence_consumes_waist_instruction(monkeypatch) -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        "Alpha. Beta.",
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_first_sentence_esimeses_lauses_waist",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="rewrite",
            attrs={"old_text": "Alpha"},
        ),
        source=OperationSource(statute_id="ee/test"),
        provenance_tags=("paragrahvi 20 lõike 2 esimeses lauses asendatakse sõna „Alpha” sõnadega „witness”",),
    )

    calls: list[tuple[int, str, str | None]] = []

    def recording_to_ee_parsed_instructions(
        ops,
        *,
        source_rule="estonia/peg:extract_ee_ops",
        wrapper_source_text=None,
    ):
        calls.append((len(ops), source_rule, wrapper_source_text))
        return [
            EEParsedInstruction(
                family=EEInstructionFamily.text_replace,
                action=StructuralAction.TEXT_REPLACE,
                target=op.target,
                source_statute_id="ee/test",
                source_title="",
                source_raw_text="",
                source_rule=source_rule,
                payload_text="rewrite",
                rewrite=EETextRewrite(old_surface="Alpha", new_surface="rewrite"),
                rewrite_witness=EETextRewriteWitness(
                    source_text="witness sentence",
                    rewrite=EETextRewrite(old_surface="Alpha", new_surface="witness"),
                ),
                provenance_tags=tuple(op.provenance_tags),
            )
        ]

    monkeypatch.setattr(grafter_module, "to_ee_parsed_instructions", recording_to_ee_parsed_instructions)

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert calls == [(1, "estonia/grafter:_ee_apply_op", None)]
    assert subsection.text == ("Witness. Beta.")


def test_insert_into_existing_subsection_materializes_later_numbered_items() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="20",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Esmakordsel ajutisel töötamisel esitab taotleja järgmised dokumendid:",
                                children=(IRNode(kind=IRNodeKind.ITEM, label="6", text="kuues dokument."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_later_items_into_existing_subsection",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="7) seitsmes dokument; 8) kaheksas dokument."),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esmakordsel ajutisel töötamisel esitab taotleja järgmised dokumendid:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("6", "kuues dokument;"),
        ("7", "seitsmes dokument;"),
        ("8", "kaheksas dokument."),
    ]


def test_insert_subsection_does_not_split_citation_range_into_item() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="7",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="Neljas."),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_subsection_with_citation_range",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "7"), ("subsection", "3_1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "(3 1) Käesoleva paragrahvi lõigetes 1–3 nimetatud ülesannete täitmisel "
                "kasutavad koordineeriv asutus ja pädevad asutused ennekõike siseturu "
                "infosüsteemi, mida reguleerib Euroopa Parlamendi ja nõukogu määrus (EL) "
                "nr 1024/2012, mis käsitleb siseturu infosüsteemi kaudu tehtavat "
                "halduskoostööd ning millega tunnistatakse kehtetuks komisjoni otsus "
                "2008/49/EÜ (ELT L 316, 14.11.2012, lk 1–11) (IMI määrus) "
                "(edaspidi siseturu infosüsteem)."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]
    subsection = next(child for child in section.children if child.label == "3_1")

    assert subsection.text.endswith("(IMI määrus) (edaspidi siseturu infosüsteem).")
    assert subsection.children == ()


def test_repeal_division_collapses_to_rt_boundary_stubs() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="11",
                text="Järelevalve",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="3",
                        text="Järelevalve finantskonglomeraadi üle",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="187",
                                text="Finantskonglomeraat",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Algne tekst."),),
                            ),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="188",
                                text="Vaheparagrahv",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Veel tekst."),),
                            ),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="198",
                                text="Lõpuparagrahv",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Lõputekst."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_division",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "11"), ("division", "3"))),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0]

    assert division.kind == IRNodeKind.DIVISION
    assert division.label == "3"
    assert division.text == "Järelevalve finantskonglomeraadi üle"
    assert [
        (child.kind, child.label, child.text, child.attrs.get("kehtetu"), child.children) for child in division.children
    ] == [
        (IRNodeKind.SECTION, "187", "Finantskonglomeraat", True, ()),
        (IRNodeKind.SECTION, "188", "Vaheparagrahv", True, ()),
        (IRNodeKind.SECTION, "198", "Lõpuparagrahv", True, ()),
    ]


def test_repeal_chapter_preserves_division_boundary_stubs() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                text="BILANSIVASTUTUS",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        text="Bilansihalduse korraldus",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="48",
                                text="Bilansihaldur",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Kustub."),),
                            ),
                        ),
                    ),
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="2",
                        text="Bilansi selgitamine",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="51",
                                text="Üldsätted",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Kustub ka."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_chapter_stubs",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("chapter", "8"),)),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert chapter.kind == IRNodeKind.CHAPTER
    assert chapter.label == "8"
    assert [(child.kind, child.label, child.text) for child in chapter.children] == [
        (IRNodeKind.DIVISION, "1", "Bilansihalduse korraldus"),
        (IRNodeKind.DIVISION, "2", "Bilansi selgitamine"),
    ]
    assert [
        (section.label, section.text, section.attrs.get("kehtetu"), section.children)
        for division in chapter.children
        for section in division.children
    ] == [
        ("48", "Bilansihaldur", True, ()),
        ("51", "Üldsätted", True, ()),
    ]


def test_replace_division_heading_updates_only_division_title() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="3",
                text="Kindlustusandja juhtimine",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        text="Vana jao pealkiri",
                        children=(IRNode(kind=IRNodeKind.SECTION, label="47_1", text="Üldised põhimõtted"),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_division_heading",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "3"), ("division", "1")), special=FacetKind.HEADING),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="1. jagu Kindlustusandja juhtimissüsteem"),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0]

    assert division.text == "Kindlustusandja juhtimissüsteem"
    assert [(child.kind, child.label, child.text) for child in division.children] == [
        (IRNodeKind.SECTION, "47_1", "Üldised põhimõtted"),
    ]


def test_replace_division_replaces_title_and_section_children() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                text="Taimetervis",
                children=(
                    IRNode(
                        kind=IRNodeKind.DIVISION,
                        label="1",
                        text="Vana jagu",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="3",
                                text="Vana § 3",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana esimene."),),
                            ),
                            IRNode(
                                kind=IRNodeKind.SECTION,
                                label="4",
                                text="Vana § 4",
                                children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana teine."),),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_division",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "2"), ("division", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "1. jagu Mõisted § 3. Taim, taimne saadus ja muu objekt "
                "(1) Taim käesoleva seaduse tähenduses on taim. "
                "§ 3 1. Kaubasaadetis, turustamine ja lõppkasutaja "
                "(1) Kaubasaadetis käesoleva seaduse tähenduses on kogum. "
                "§ 4. Ohtlik taimekahjustaja Ohtlik taimekahjustaja käesoleva "
                "seaduse tähenduses on taimekahjustaja."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    division = result.children[0].children[0]

    assert division.kind == IRNodeKind.DIVISION
    assert division.label == "1"
    assert division.text == "Mõisted"
    assert [(child.kind, child.label, child.text) for child in division.children] == [
        (IRNodeKind.SECTION, "3", "Taim, taimne saadus ja muu objekt"),
        (IRNodeKind.SECTION, "3_1", "Kaubasaadetis, turustamine ja lõppkasutaja"),
        (
            IRNodeKind.SECTION,
            "4",
            "Ohtlik taimekahjustaja Ohtlik taimekahjustaja käesoleva seaduse tähenduses on taimekahjustaja.",
        ),
    ]


def test_replace_section_sentence_like_payload_materializes_inline_items_under_subsection_one() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="14",
                        text="Avalduse sisu",
                        children=(IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana tekst."),),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_section_replace_inline_item_body",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "14"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=(
                "Hoiu-laenuühistu äriregistrisse kandmiseks esitab juhatus avalduse: "
                "1) osakapitali suurus; 2) revisjonikomisjoni liikmete nimed."
            ),
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]
    subsection = section.children[0]

    assert section.text == "Avalduse sisu"
    assert subsection.label == "1"
    assert subsection.text == "Hoiu-laenuühistu äriregistrisse kandmiseks esitab juhatus avalduse:"
    assert [(item.label, item.text) for item in subsection.children] == [
        ("1", "osakapitali suurus;"),
        ("2", "revisjonikomisjoni liikmete nimed."),
    ]


def test_apply_ee_ops_sorts_same_source_text_replace_run_by_specificity() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Testseadus",
        body=_body_with_section_and_subsection(
            "20",
            "3",
            "Erinevused võivad olla ohtlikud rahvatervisele või -ohutusele.",
        ),
    )
    source = OperationSource(statute_id="ee/102012025003")
    ops = [
        LegalOperation(
            op_id="global-text-replace",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="rahvastiku tervis",
                attrs={"old_text": "rahvatervis", "case_inflected": True},
            ),
            source=source,
        ),
        LegalOperation(
            op_id="specific-text-replace",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "20"), ("subsection", "3"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="rahvastiku tervisele ja vähendada rahvastiku ohutust",
                attrs={"old_text": "rahvatervisele või -ohutusele"},
            ),
            source=source,
        ),
    ]

    result = apply_ee_ops(statute, ops)
    subsection = result.body.children[0].children[0].children[0]

    assert subsection.text == ("Erinevused võivad olla ohtlikud rahvastiku tervisele ja vähendada rahvastiku ohutust.")


def test_apply_ee_ops_prefers_explicit_target_law_replace_over_generic_ministry_reorg_same_old_text() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Kalapüügiseadus",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="10",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="7",
                                    text="Loa annab Keskkonnaministeerium.",
                                ),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="90_2",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="1",
                                    text="Andmed esitati Keskkonnaministeeriumile.",
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="ee/130062023001")
    generic_op = LegalOperation(
        op_id="ee-generic-ministry-reorg",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Kliimaministeerium",
            attrs={
                "old_text": "Keskkonnaministeerium",
                "case_inflected": True,
                "source_family": "generic_ministry_reorganization",
            },
        ),
        source=source,
    )
    explicit_op = LegalOperation(
        op_id="ee-target-law-ministry-replace",
        sequence=2,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=()),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Regionaal- ja Põllumajandusministeerium",
            attrs={
                "old_text": "Keskkonnaministeerium",
                "case_inflected": True,
                "exclude_paths": [((("section", "90_2"), ("subsection", "1")))],
            },
        ),
        source=source,
    )

    result = apply_ee_ops(statute, [generic_op, explicit_op])

    section_10 = result.body.children[0].children[0]
    section_90_2 = result.body.children[0].children[1]
    assert section_10.children[0].text == "Loa annab Regionaal- ja Põllumajandusministeerium."
    assert section_90_2.children[0].text == "Andmed esitati Kliimaministeeriumile."


def test_exact_target_insert_after_with_repeated_source_surface_emits_ambiguity() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Maagaasiseadus",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="26_7",
                            children=(
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="1",
                                    text=(
                                        "Varumakse katab strateegilise gaasivaru kulud ja "
                                        "gaasivaru hoidmise korralduse."
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee-test-ambiguous-insert-after",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("section", "26_7"), ("subsection", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="gaasivaru ning terminali taristu",
            attrs={
                "old_text": "gaasivaru",
                "rewrite_mode": "insert_after",
            },
        ),
        source=OperationSource(statute_id="ee/test-source"),
    )
    adjudications: list[CompileAdjudication] = []

    result = apply_ee_ops(statute, [op], adjudications_out=adjudications)

    subsection = result.body.children[0].children[0].children[0]
    assert subsection.text == statute.body.children[0].children[0].children[0].text
    ambiguity = [record for record in adjudications if record.kind == "ee_ambiguous_single_occurrence_text_replace"]
    assert len(ambiguity) == 1
    assert ambiguity[0].detail["target"] == "section:26_7/subsection:1"
    assert ambiguity[0].detail["match_count"] == "2"


def test_apply_ee_ops_records_unsupported_action() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(kind=IRNodeKind.BODY, children=()),
    )
    ops = [
        LegalOperation(
            op_id="ee-unsupported",
            sequence=1,
            action=StructuralAction.TEXT_REPEAL,
            target=LegalAddress(path=(("section", "1"),)),
            source=OperationSource(statute_id="2026/1"),
        )
    ]
    adjudications: list[CompileAdjudication] = []

    apply_ee_ops(statute, ops, adjudications_out=adjudications)

    assert len(adjudications) == 1
    assert adjudications[0].kind == "ee_replay_unsupported_action"
    assert adjudications[0].op_id == "ee-unsupported"
    assert adjudications[0].detail["action"] == "text_repeal"


def test_apply_ee_ops_renumbers_existing_section_before_inserting_new_same_label_section() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    text="Peatukk",
                    children=(
                        IRNode(
                            kind=IRNodeKind.SECTION,
                            label="27_1",
                            text="Abivajava lapse väljaselgitamine",
                            children=(
                                IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Vana tekst."),
                                IRNode(
                                    kind=IRNodeKind.SUBSECTION,
                                    label="2",
                                    text="Veel vana teksti.",
                                    children=(IRNode(kind=IRNodeKind.ITEM, label="1", text="Punkt."),),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    source = OperationSource(statute_id="2024/1")
    ops = [
        LegalOperation(
            op_id="ee-renumber-27_1-27_2",
            sequence=1,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", "27_1"),)),
            destination=LegalAddress(path=(("section", "27_2"),)),
            source=source,
        ),
        LegalOperation(
            op_id="ee-insert-new-27_1",
            sequence=2,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", "27_1"),)),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="§ 27 1. Abivajavast lapsest teatamata jätmine (1) Uus tekst.",
            ),
            source=source,
        ),
    ]

    result = apply_ee_ops(statute, ops)
    chapter = result.body.children[0]

    assert [(child.kind, child.label) for child in chapter.children] == [
        (IRNodeKind.SECTION, "27_1"),
        (IRNodeKind.SECTION, "27_2"),
    ]
    assert chapter.children[0].text == "Abivajavast lapsest teatamata jätmine"
    assert chapter.children[0].children[0].text == "Uus tekst."
    assert chapter.children[1].text == "Abivajava lapse väljaselgitamine"
    assert chapter.children[1].children[0].text == "Vana tekst."
    assert chapter.children[1].children[1].text == "Veel vana teksti."
    assert chapter.children[1].children[1].children[0].label == "1"


def test_structural_textosa_heading_relabel_resolves_duplicate_chapter_by_heading() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(kind=IRNodeKind.CHAPTER, label="2", text="OMAVAHENDID"),
            IRNode(kind=IRNodeKind.CHAPTER, label="3", text="KAPITALI ADEKVAATSUS"),
            IRNode(kind=IRNodeKind.CHAPTER, label="2", text="RISKIDE KONTROLL"),
            IRNode(kind=IRNodeKind.CHAPTER, label="4", text="ARUANDLUS"),
        ),
    )
    relabel_risk = LegalOperation(
        op_id="ee-heading-relabel-risk",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "2"),)),
        destination=LegalAddress(path=(("chapter", "4"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="RISKIDE KONTROLL",
            attrs={
                "rule_id": "ee_structural_textosa_heading_relabel",
                "old_heading": "RISKIDE KONTROLL",
                "new_heading": "RISKIDE KONTROLL",
                "allow_occupied_destination": True,
            },
        ),
    )
    relabel_reports = LegalOperation(
        op_id="ee-heading-relabel-reports",
        sequence=2,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("chapter", "4"),)),
        destination=LegalAddress(path=(("chapter", "6"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="ARUANDLUS",
            attrs={
                "rule_id": "ee_structural_textosa_heading_relabel",
                "old_heading": "ARUANDLUS",
                "new_heading": "ARUANDLUS",
                "allow_occupied_destination": True,
            },
        ),
    )

    after_risk = _ee_apply_op(body, relabel_risk)
    after_reports = _ee_apply_op(after_risk, relabel_reports)

    assert [(child.label, child.text) for child in after_reports.children] == [
        ("2", "OMAVAHENDID"),
        ("3", "KAPITALI ADEKVAATSUS"),
        ("4", "RISKIDE KONTROLL"),
        ("6", "ARUANDLUS"),
    ]


def test_high_division_insert_relabels_unique_duplicate_division_suffix_with_adjudication() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Testmäärus",
        body=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    text="PEATUKK",
                    children=(
                        IRNode(kind=IRNodeKind.DIVISION, label="1", text="One"),
                        IRNode(kind=IRNodeKind.DIVISION, label="2", text="Two A"),
                        IRNode(kind=IRNodeKind.DIVISION, label="2", text="Two B"),
                        IRNode(kind=IRNodeKind.DIVISION, label="3", text="Three"),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee-insert-division-5",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "3"), ("division", "5"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="5. jagu Five § 50. Inserted section (1) Inserted.",
        ),
        source=OperationSource(statute_id="ee/test-source"),
    )
    adjudications: list[CompileAdjudication] = []

    result = apply_ee_ops(statute, [op], adjudications_out=adjudications)
    chapter = result.body.children[0]

    assert [(child.label, child.text) for child in chapter.children] == [
        ("1", "One"),
        ("2", "Two A"),
        ("3", "Two B"),
        ("4", "Three"),
        ("5", "Five"),
    ]
    assert [adjudication.kind for adjudication in adjudications] == [
        "ee_implicit_division_sequence_relabel_after_high_jagu_insert"
    ]


def test_insert_lauseosa_append_is_idempotent_when_target_already_contains_phrase() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.SECTION,
                label="16",
                children=(
                    IRNode(
                        kind=IRNodeKind.SUBSECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.ITEM,
                                label="1",
                                text=(
                                    "lapse sünnitunnistus, kui selle kohta ei ole kantud "
                                    "andmed rahvastikuregistrisse;"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee-lauseosa-idempotent",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "16"), ("subsection", "1"), ("item", "1"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text=", kui selle kohta ei ole kantud andmed rahvastikuregistrisse",
            attrs={"source_family": "ee_targeted_lauseosa_append"},
        ),
        provenance_tags=("paragrahvi 16 lõike 1 punkti 1 täiendatakse lauseosaga",),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0]

    assert item.text == (
        "lapse sünnitunnistus, kui selle kohta ei ole kantud "
        "andmed rahvastikuregistrisse;"
    )


def test_apply_ee_ops_records_unresolved_target_and_noop() -> None:
    statute = IRStatute(
        statute_id="ee/test",
        title="Test",
        body=_body_with_section_and_subsection(
            "1",
            "1",
            "Kehtiv tekst.",
        ),
    )
    unresolved = [
        LegalOperation(
            op_id="ee-missing-target",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "99"),)),
            payload=IRNode(kind=IRNodeKind.SECTION, label="99", text="Uus"),
            source=OperationSource(statute_id="2026/2"),
        ),
    ]
    noop_ops = [
        LegalOperation(
            op_id="ee-noop",
            sequence=2,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="uus tekst",
                attrs={"old_text": "puudub"},
            ),
            source=OperationSource(statute_id="2026/3"),
        ),
    ]
    adjudications: list[CompileAdjudication] = []

    apply_ee_ops(statute, [*unresolved, *noop_ops], adjudications_out=adjudications)

    assert len(adjudications) == 2
    assert adjudications[0].kind == "ee_replay_target_not_found"
    assert adjudications[0].op_id == "ee-missing-target"
    assert adjudications[1].kind == "ee_replay_noop"
    assert adjudications[1].op_id == "ee-noop"


def test_insert_subsection_shifts_later_numeric_subsections_before_inserting() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="79",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION, label="1", text="Käesolev seadus jõustub 2001. aasta 1. veebruaril."
                            ),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Lisa 1"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
                                    "Lisa 2 LIIKUMISPUUDEGA INIMESE SÕIDUKI PARKIMISKAART"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_shift_appendix_subsections",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "79"), ("subsection", "2"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="Käesoleva seaduse § 27 lõige 5 jõustub Eesti ühinemisel Euroopa Liiduga eraldi seadusega.",
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Käesolev seadus jõustub 2001. aasta 1. veebruaril."),
        ("2", "Käesoleva seaduse § 27 lõige 5 jõustub Eesti ühinemisel Euroopa Liiduga eraldi seadusega."),
        ("3", "Lisa 1"),
        (
            "4",
            "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
            "Lisa 2 LIIKUMISPUUDEGA INIMESE SÕIDUKI PARKIMISKAART",
        ),
    ]


def test_insert_subsection_shifts_down_after_collapsed_repealed_range() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="6",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="30",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Esimene."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Teine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="3", text="Kolmas."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="4", text="–(5)"),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_shift_after_repealed_range",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "30"), ("subsection", "6"))),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="(6) Uus ametikoolituse lõige.",
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert [(child.label, child.text) for child in section.children] == [
        ("1", "Esimene."),
        ("2", "Teine."),
        ("3", "Kolmas."),
        ("4", "–(5)"),
        ("5", "Uus ametikoolituse lõige."),
    ]


def test_text_replace_on_chapter_heading_only_updates_title() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                text="MOOTORSÕIDUKI JUHTIMISE ÕIGUSE PEATAMINE, ÄRAVÕTMINE",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="41_1",
                        text="Ajutise juhiloa väljaandmine",
                        children=(),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_chapter_heading",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("chapter", "8"),), special=FacetKind.HEADING),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"old_text": "PEATAMINE,"},
        ),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert chapter.text == "MOOTORSÕIDUKI JUHTIMISE ÕIGUSE ÄRAVÕTMINE"
    assert chapter.children[0].text == "Ajutise juhiloa väljaandmine"


def test_text_replace_on_chapter_heading_consumes_waist_instruction(monkeypatch) -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                text="Alfa beta gamma",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="41_1",
                        text="Ajutise juhiloa väljaandmine",
                        children=(),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_text_replace_chapter_heading_waist",
        sequence=1,
        action=StructuralAction.TEXT_REPLACE,
        target=LegalAddress(path=(("chapter", "8"),), special=FacetKind.HEADING),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="",
            attrs={"old_text": "beta"},
        ),
    )

    calls: list[tuple[int, str, str | None]] = []

    def recording_to_ee_parsed_instructions(
        ops,
        *,
        source_rule="estonia/peg:extract_ee_ops",
        wrapper_source_text=None,
    ):
        calls.append((len(ops), source_rule, wrapper_source_text))
        return [
            EEParsedInstruction(
                family=EEInstructionFamily.text_replace,
                action=StructuralAction.TEXT_REPLACE,
                target=op.target,
                source_statute_id="ee/test",
                source_title="",
                source_raw_text="",
                source_rule=source_rule,
                payload_text="rewrite",
                rewrite=EETextRewrite(old_surface="beta", new_surface="rewrite"),
                rewrite_witness=EETextRewriteWitness(
                    source_text="witness heading",
                    rewrite=EETextRewrite(old_surface="beta", new_surface="witness"),
                ),
                provenance_tags=tuple(op.provenance_tags),
            )
        ]

    monkeypatch.setattr(grafter_module, "to_ee_parsed_instructions", recording_to_ee_parsed_instructions)

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert calls == [(1, "estonia/grafter:_ee_apply_op", None)]
    assert chapter.text == "Alfa witness gamma"
    assert chapter.children[0].text == "Ajutise juhiloa väljaandmine"


def test_repeal_inserted_item_keeps_empty_placeholder_and_preserves_previous_item_terminal() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="12",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Intro.",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="6", text="muud seadusest tulenevad dokumendid;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="6_1", text="ajutine lisadokument."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_inserted_item_placeholder",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "12"), ("subsection", "1"), ("item", "6_1"))),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[0].text == "muud seadusest tulenevad dokumendid;"
    assert subsection.children[1].label == "6_1"
    assert subsection.children[1].text == ""


def test_repeal_item_preserves_existing_terminals_when_only_empty_item_placeholders_follow() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="2",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Erakooli liigid on:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="koolieelne lasteasutus;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="lasteaed;"),
                                    IRNode(
                                        kind=IRNodeKind.ITEM, label="10", text="ülikool, mis tegutseb kõrgharidusseaduse alusel."
                                    ),
                                    IRNode(kind=IRNodeKind.ITEM, label="11", text="huvikool;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="12", text=""),
                                    IRNode(kind=IRNodeKind.ITEM, label="13", text=""),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_item_with_trailing_placeholders",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "2"), ("subsection", "2"), ("item", "1"))),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[2].text == "ülikool, mis tegutseb kõrgharidusseaduse alusel."
    assert subsection.children[3].text == "huvikool;"
    assert subsection.children[4].text == ""
    assert subsection.children[5].text == ""


def test_repeal_item_keeps_previous_semicolon_when_later_items_remain() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="9",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="45",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="5",
                                text="Teeomanikuks käesoleva paragrahvi tähenduses on:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="riigimaanteel – Maanteeamet;"),
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="2",
                                        text="riigi tugi- ja kõrvalmaanteel – Maanteeameti kohalik asutus;",
                                    ),
                                    IRNode(kind=IRNodeKind.ITEM, label="3", text="kohalikul teel – valla- või linnavalitsus;"),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_middle_item_placeholder",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "45"), ("subsection", "5"), ("item", "2"))),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[0].text == "riigimaanteel – Maanteeamet;"
    assert subsection.children[1].label == "2"
    assert subsection.children[1].text == ""
    assert subsection.children[2].text == "kohalikul teel – valla- või linnavalitsus."


def test_insert_item_converts_previous_terminal_period_to_semicolon() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="2",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="11",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text="Mootorsõiduki kasutada andmise akt peab sisaldama:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="teine;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="3", text="kolmas."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_insert_item_semicolon",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "11"), ("subsection", "3"), ("item", "4"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="4) neljas."),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[2].text == "kolmas;"
    assert subsection.children[3].text == "neljas."


def test_replace_last_item_finalizes_terminal_semicolon_to_period() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="18",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Volikiri sisaldab:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="teine;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="5", text="vana viimane."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_last_item_period",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "18"), ("subsection", "1"), ("item", "5"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="5) uus viimane;"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[-1].text == "uus viimane."


def test_replace_item_before_two_trailing_empty_stubs_finalizes_to_period() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="18",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Volikiri sisaldab:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="teine;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="5", text="vana kolmas;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="6", text=""),
                                    IRNode(kind=IRNodeKind.ITEM, label="7", text=""),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_item_before_empty_stubs_semicolon",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "18"), ("subsection", "1"), ("item", "5"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="5) uus kolmas;"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[2].text == "uus kolmas."


def test_replace_item_before_long_trailing_empty_stub_run_keeps_semicolon() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="18",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Volikiri sisaldab:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="teine;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="5", text="vana kolmas;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="6", text=""),
                                    IRNode(kind=IRNodeKind.ITEM, label="7", text=""),
                                    IRNode(kind=IRNodeKind.ITEM, label="8", text=""),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_item_before_long_empty_stubs_semicolon",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "18"), ("subsection", "1"), ("item", "5"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="5) uus kolmas;"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[2].text == "uus kolmas;"


def test_replace_item_before_single_empty_stub_finalizes_to_period() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="18",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Volikiri sisaldab:",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="5", text="vana viimane;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="6", text=""),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_item_before_single_empty_stub_period",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "18"), ("subsection", "1"), ("item", "5"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="5) uus viimane;"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[1].text == "uus viimane."


def test_replace_item_drops_multiple_targeted_sentences_from_notes() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text="",
                                children=(
                                    IRNode(
                                        kind=IRNodeKind.ITEM,
                                        label="1",
                                        text="Esimene lause jääb. Teine lause kaob. Kolmas lause kaob;",
                                    ),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_item_multi_sentence_repeal",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "1"), ("subsection", "3"), ("item", "1"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text=""),
        provenance_tags=("teine ja kolmas lause tunnistatakse kehtetuks",),
    )

    result = _ee_apply_op(body, op)
    item = result.children[0].children[0].children[0].children[0]
    assert item.text == "Esimene lause jääb."


def test_replace_non_last_item_adds_terminal_semicolon_when_payload_omits_it() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="4",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="20_1",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="2",
                                text="Juht kõrvaldatakse sõiduki juhtimiselt, kui",
                                children=(
                                    IRNode(kind=IRNodeKind.ITEM, label="1", text="esimene;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="2", text="vana teine;"),
                                    IRNode(kind=IRNodeKind.ITEM, label="3", text="kolmas."),
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_replace_middle_item_semicolon",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "20_1"), ("subsection", "2"), ("item", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="2) uus teine"),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.children[1].text == "uus teine;"
    assert subsection.children[2].text == "kolmas."


def test_repeal_section_becomes_empty_title_stub() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="14_1",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="74_20",
                        text="Isiku kõrvalehoidumine joobeseisundit tuvastavast läbivaatusest",
                        children=(
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="1",
                                text="Senine tekst.",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_section_stub",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "74_20"),)),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.label == "74_20"
    assert section.text == ""
    assert section.children == ()
    assert section.attrs.get("kehtetu") is True


def test_replace_section_applies_appendix_table_row_update_after_marker() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="79",
                        text="Seaduse jõustumine",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jõustumine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Lisa 1"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
                                    "Kategooria Sõiduki liik ja iseloomustus "
                                    "A mootorratas "
                                    "B vana B-rida "
                                    "BE vana BE-rida "
                                    "C järgmine rida"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_appendix_table_update",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "79"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="B uus B-rida BE uus BE-rida",
            attrs={
                "appendix_table_update": True,
                "appendix_marker": "Lisa 1",
                "appendix_table_categories": ["B", "BE"],
                "rewrite_witness": EETextRewriteWitness(
                    source_text="appendix witness",
                    rewrite=EETextRewrite(
                        appendix_table_update=True,
                        appendix_marker="Lisa 1",
                        appendix_table_categories=("B", "BE"),
                        new_surface="B uus B-rida BE uus BE-rida",
                    ),
                ),
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert section.children[2].text == (
        "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
        "Kategooria Sõiduki liik ja iseloomustus "
        "A mootorratas "
        "B uus B-rida BE uus BE-rida "
        "C järgmine rida"
    )


def test_replace_section_ignores_appendix_raw_attrs_without_witness() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="79",
                        text="Seaduse jõustumine",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jõustumine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Lisa 1"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
                                    "Kategooria Sõiduki liik ja iseloomustus "
                                    "A mootorratas "
                                    "B vana B-rida "
                                    "BE vana BE-rida "
                                    "C järgmine rida"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_appendix_table_update_raw_only",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "79"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="B raw B-rida BE raw BE-rida",
            attrs={
                "appendix_table_update": True,
                "appendix_marker": "Lisa 1",
                "appendix_table_categories": ["B", "BE"],
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert _child_subsection(section, "3").text == (
        "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
        "Kategooria Sõiduki liik ja iseloomustus "
        "A mootorratas "
        "B vana B-rida "
        "BE vana BE-rida "
        "C järgmine rida"
    )


def test_replace_section_prefers_appendix_witness_over_raw_attrs() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="15",
                children=(
                    IRNode(
                        kind=IRNodeKind.SECTION,
                        label="79",
                        text="Seaduse jõustumine",
                        children=(
                            IRNode(kind=IRNodeKind.SUBSECTION, label="1", text="Jõustumine."),
                            IRNode(kind=IRNodeKind.SUBSECTION, label="2", text="Lisa 2"),
                            IRNode(
                                kind=IRNodeKind.SUBSECTION,
                                label="3",
                                text=(
                                    "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
                                    "Kategooria Sõiduki liik ja iseloomustus "
                                    "A mootorratas "
                                    "B vana B-rida "
                                    "BE vana BE-rida "
                                    "C järgmine rida"
                                ),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_appendix_table_update_witness",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "79"),)),
        payload=IRNode(
            kind=IRNodeKind.CONTENT,
            text="B raw B-rida BE raw BE-rida",
            attrs={
                "appendix_table_update": True,
                "appendix_marker": "Lisa 1",
                "appendix_table_categories": ["B", "BE"],
                "rewrite_witness": EETextRewriteWitness(
                    source_text="appendix witness",
                    rewrite=EETextRewrite(
                        appendix_table_update=True,
                        appendix_marker="Lisa 2",
                        appendix_table_categories=("B", "BE"),
                        new_surface="B witness B-rida BE witness BE-rida",
                    ),
                ),
            },
        ),
    )

    result = _ee_apply_op(body, op)
    section = result.children[0].children[0]

    assert _child_subsection(section, "3").text == (
        "MOOTORSÕIDUKITE KATEGOORIAD VASTAVALT JUHTIMISÕIGUSELE "
        "Kategooria Sõiduki liik ja iseloomustus "
        "A mootorratas "
        "B witness B-rida BE witness BE-rida "
        "C järgmine rida"
    )


def test_repeal_superscript_section_drops_when_base_section_is_already_kehtetu() -> None:
    body = IRNode(
        kind=IRNodeKind.BODY,
        children=(
            IRNode(
                kind=IRNodeKind.CHAPTER,
                label="8",
                children=(
                    IRNode(kind=IRNodeKind.SECTION, label="41", text="", attrs={"kehtetu": True}, children=()),
                    IRNode(kind=IRNodeKind.SECTION, label="41_1", text="Ajutise juhiloa väljaandmine", children=()),
                    IRNode(kind=IRNodeKind.SECTION, label="42", text="Juhtimisõiguse äravõtmine", children=()),
                ),
            ),
        ),
    )
    op = LegalOperation(
        op_id="ee_test_repeal_superscript_section_drop",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "41_1"),)),
    )

    result = _ee_apply_op(body, op)
    chapter = result.children[0]

    assert [(child.label, child.text) for child in chapter.children] == [
        ("41", ""),
        ("42", "Juhtimisõiguse äravõtmine"),
    ]


def test_sentence_indexes_from_notes_supports_fourth_sentence() -> None:
    from lawvm.estonia.grafter import _sentence_indexes_from_notes

    assert _sentence_indexes_from_notes("neljas lause tunnistatakse kehtetuks") == [3]
    assert _sentence_indexes_from_notes("kolmas ja neljas lause tunnistatakse kehtetuks") == [2, 3]


def test_sentence_indexes_from_notes_supports_sixth_sentence() -> None:
    from lawvm.estonia.grafter import _sentence_indexes_from_notes

    assert _sentence_indexes_from_notes("kuues lause muudetakse ja sõnastatakse järgmiselt") == [5]


def test_text_morphology_sentence_indexes_from_notes_supports_notes() -> None:
    from lawvm.estonia.text_morphology import (
        case_preserved_replacement,
        replace_case_preserving,
        sentence_index_from_notes,
        sentence_indexes_from_notes,
        surface_pattern,
        wrap_word_boundaries,
    )
    import re

    assert sentence_indexes_from_notes("neljas lause tunnistatakse kehtetuks") == [3]
    assert sentence_indexes_from_notes("kolmas ja neljas lause tunnistatakse kehtetuks") == [2, 3]
    assert sentence_index_from_notes("kuues lause muudetakse ja sõnastatakse järgmiselt") == 5
    assert surface_pattern("konkursi-ja") == r"konkursi\s*[–‒−-]\s*ja"
    assert wrap_word_boundaries("amet", "amet") == r"(?<![A-Za-zÄÖÕÜäöõüŠŽšž-])amet(?![A-Za-zÄÖÕÜäöõüŠŽšž-])"
    match = re.compile(r"amet", re.IGNORECASE).search("Amet")  # pragma: no cover - direct helper setup
    assert match is not None
    assert case_preserved_replacement(match, "asutus") == "Asutus"
    assert replace_case_preserving("Amet on siin.", "amet", "asutus") == "Asutus on siin."


def test_case_inflected_rewrite_preserves_nominative_in_kohustatud_subject_context() -> None:
    old = "Veterinaar- ja Toiduameti kohalik asutus"
    new = "Veterinaar- ja Toiduamet"

    assert (
        _ee_apply_text_replace_value(
            "Veterinaar- ja Toiduameti kohalik asutus kohustatud määrama",
            old,
            new,
            case_inflected=True,
        )
        == "Veterinaar- ja Toiduamet kohustatud määrama"
    )
    assert (
        _ee_apply_text_replace_value(
            "Veterinaar- ja Toiduameti kohaliku asutuse määratud ametnik",
            old,
            new,
            case_inflected=True,
        )
        == "Veterinaar- ja Toiduameti määratud ametnik"
    )


def test_case_inflected_rewrite_preserves_nominative_before_coordinated_tud_modifier() -> None:
    assert (
        _ee_apply_text_replace_value(
            "järelevalveametnik või volitatud veterinaararst peab kontrollima",
            "järelevalveametnik",
            "veterinaarjärelevalveametnik",
            case_inflected=True,
        )
        == "veterinaarjärelevalveametnik või volitatud veterinaararst peab kontrollima"
    )


def test_case_inflected_rewrite_preserves_nominative_after_arvates_temporal_phrase() -> None:
    text = (
        "Volitatud laboratooriumina tegutsemiseks volituse andmise otsuse teeb "
        "20 tööpäeva jooksul laboratooriumi kirjaliku taotluse saamisest arvates "
        "Veterinaar- ja Toiduamet."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "Veterinaar- ja Toiduamet",
        "Põllumajandus-ja Toiduamet",
        case_inflected=True,
    )

    assert replaced == (
        "Volitatud laboratooriumina tegutsemiseks volituse andmise otsuse teeb "
        "20 tööpäeva jooksul laboratooriumi kirjaliku taotluse saamisest arvates "
        "Põllumajandus-ja Toiduamet."
    )


def test_case_inflected_rewrite_handles_plural_id_to_jad_family() -> None:
    text = (
        "Pedagoogidele, kelle palgad kaetakse riigieelarvest, nähakse tööalaseks "
        "koolituseks ette vahendid riigieelarves pedagoogide tööalaseks "
        "koolituseks ettenähtud vahenditest."
    )

    replaced = _ee_apply_text_replace_value(
        text,
        "pedagoogid",
        "õpetajad",
        case_inflected=True,
    )

    assert replaced == (
        "Õpetajatele, kelle palgad kaetakse riigieelarvest, nähakse tööalaseks "
        "koolituseks ette vahendid riigieelarves õpetajate tööalaseks "
        "koolituseks ettenähtud vahenditest."
    )


def test_case_inflected_rewrite_handles_vagi_genitive_family() -> None:
    replaced = _ee_apply_text_replace_value(
        "kaitseväe juhataja vastutab kaitseväe mobilisatsiooniplaanide eest.",
        "kaitsevägi",
        "Kaitsevägi",
        case_inflected=True,
        all_occurrences=True,
    )

    assert replaced == "Kaitseväe juhataja vastutab Kaitseväe mobilisatsiooniplaanide eest."


def test_case_inflected_rewrite_handles_ik_plural_forms() -> None:
    replaced = _ee_apply_text_replace_value(
        "reservväelasi vastavalt vajadusele ja reservväelastest üksuste koosseisus",
        "reservväelane",
        "reservis olev isik",
        case_inflected=True,
        all_occurrences=True,
    )

    assert replaced == (
        "reservis olevaid isikuid vastavalt vajadusele ja "
        "reservis olevatest isikutest üksuste koosseisus"
    )


def test_case_inflected_rewrite_preserves_passive_object_nominative() -> None:
    replaced = _ee_apply_text_replace_value(
        "Mobilisatsiooni korral kutsutakse reservväelane teenistusse.",
        "reservväelane",
        "reservis olev isik",
        case_inflected=True,
        all_occurrences=True,
    )

    assert replaced == "Mobilisatsiooni korral kutsutakse reservis olev isik teenistusse."


def test_case_inflected_rewrite_matches_normalized_inflected_hyphen_spacing() -> None:
    assert (
        _ee_apply_text_replace_value(
            "Veterinaar-ja Toiduameti kohaliku asutuse juhi määratud ametnik",
            "Veterinaar- ja Toiduameti kohaliku asutuse juht",
            "Veterinaar- ja Toiduamet",
            case_inflected=True,
        )
        == "Veterinaar-ja Toiduameti määratud ametnik"
    )


def test_nested_quote_delete_matches_guillemet_source_text() -> None:
    assert (
        _ee_apply_text_replace_value(
            "otse «Toiduseaduse» § 10 lõike 1 alusel tunnustatud käitlemisettevõttesse",
            "„Toiduseaduse” § 10 lõike 1 alusel tunnustatud",
            "",
            case_inflected=False,
        )
        == "otse käitlemisettevõttesse"
    )


def test_replace_sentence_note_preserves_other_sentences_for_sixth_sentence() -> None:
    body = _body_with_section_and_subsection(
        "21",
        "6",
        ("Üldkoosolek protokollitakse. Vana teine lause. Kolmas lause. Neljas lause. Viies lause. Vana kuues lause."),
    )
    op = LegalOperation(
        op_id="ee_test_replace_sixth_sentence_only",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "21"), ("subsection", "6"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Uus kuues lause."),
        provenance_tags=("paragrahvi 21 lõike 6 kuues lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == (
        "Üldkoosolek protokollitakse. Vana teine lause. Kolmas lause. Neljas lause. Viies lause. Uus kuues lause."
    )


def test_replace_sentence_note_targets_last_sentence() -> None:
    body = _body_with_section_and_subsection(
        "20",
        "2",
        "Esimene lause. Vana viimane lause.",
    )
    op = LegalOperation(
        op_id="ee_test_replace_last_sentence",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("section", "20"), ("subsection", "2"))),
        payload=IRNode(kind=IRNodeKind.CONTENT, text="Uus viimane lause."),
        provenance_tags=("paragrahvi 20 lõike 2 viimane lause muudetakse ja sõnastatakse järgmiselt",),
    )

    result = _ee_apply_op(body, op)
    subsection = result.children[0].children[0].children[0]

    assert subsection.text == "Esimene lause. Uus viimane lause."
