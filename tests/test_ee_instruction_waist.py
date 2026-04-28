from __future__ import annotations

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.peg import extract_ee_ops
from lawvm.estonia.ee_instruction_waist import (
    EEInstructionFamily,
    EESentenceTargetMeta,
    EESubsectionSelectionMeta,
    EETextReplaceMode,
    EETextRewrite,
    EETextRewriteWitness,
    make_sentence_target_meta,
    make_text_rewrite_witness,
    parse_wrapper_quoted_clause,
    to_ee_parsed_instructions,
)


def test_to_ee_parsed_instructions_maps_structural_family() -> None:
    source = OperationSource(statute_id="ee/test", title="Riigilõivuseadus")
    ops = []
    ops.extend(extract_ee_ops("paragrahvi 10 tunnistatakse kehtetuks.", source))
    ops.extend(extract_ee_ops('paragrahvi 11 täiendatakse järgmises sõnastuses: „(1) uus sisu”.', source))

    instructions = to_ee_parsed_instructions(ops)

    assert [inst.family for inst in instructions] == [
        EEInstructionFamily.structural,
        EEInstructionFamily.structural,
    ]
    assert [inst.action for inst in instructions] == [StructuralAction.REPEAL, StructuralAction.INSERT]
    assert instructions[0].target.path == (("section", "10"),)
    assert instructions[1].target.path == (("section", "11"),)


def test_to_ee_parsed_instructions_maps_text_replace_family() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        'paragrahvi 12 lõige 1 asendatakse sõnaga „koolieelne lasteasutus” '
        'sõnaga „lastehoid”.',
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    inst = instructions[0]
    assert inst.family == EEInstructionFamily.text_replace
    assert inst.rewrite == EETextRewrite(
        old_surface="koolieelne lasteasutus",
        new_surface="lastehoid",
        mode=EETextReplaceMode.replace,
        case_inflected=False,
        scope_chapters=(),
        exclude_paths=(),
        generic_minister_plural=False,
        old_titles=(),
        source_family="",
    )


def test_to_ee_parsed_instructions_preserves_rewrite_mode() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        'paragrahvi 74 35 lõiget 2 täiendatakse pärast tekstiosa '
        '«kuni 100 trahviühikut» tekstiosaga '
        '«või sõiduki juhtimise õiguse äravõtmisega kuni kuue kuuni.»;',
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].rewrite is not None
    assert instructions[0].rewrite.mode == EETextReplaceMode.insert_after


def test_to_ee_parsed_instructions_exposes_text_rewrite_witness() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        'paragrahvi 12 lõige 1 asendatakse sõnaga „koolieelne lasteasutus” '
        'sõnaga „lastehoid”.',
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].rewrite_witness is not None
    assert isinstance(instructions[0].rewrite_witness, EETextRewriteWitness)
    assert instructions[0].rewrite_witness.source_text.startswith("paragrahvi 12 lõige 1")
    assert instructions[0].rewrite_witness.rewrite == instructions[0].rewrite


def test_to_ee_parsed_instructions_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = [
        LegalOperation(
            op_id="ee_test_sentence_target_meta",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "37"),)),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="",
                attrs={"sentence_target_meta": make_sentence_target_meta(sentence_indexes=(2, 3))},
            ),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(2, 3))


def test_extract_ee_ops_text_replace_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        'paragrahvi 20 lõike 2 esimeses lauses asendatakse sõna „Alpha” sõnaga „rewrite”.',
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(0,))


def test_extract_ee_ops_insert_exposes_sentence_target_meta_with_mode() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        'paragrahvi 16 lõiget 6 täiendatakse pärast esimest lauset lausega järgmises sõnastuses: „Lisatud lause.”',
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta == EESentenceTargetMeta(
        sentence_indexes=(0,),
        mode="insert_after",
    )


def test_extract_ee_ops_singular_subsection_sentence_repeal_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 57 lõike 2 teine lause tunnistatakse kehtetuks.",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(1,))


def test_extract_ee_ops_singular_subsection_sentence_replace_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 7 lõike 4 teine lause muudetakse ja sõnastatakse järgmiselt: „Uus teine lause.”",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(1,))


def test_extract_ee_ops_subsection_replace_ignores_sentence_words_inside_quoted_payload() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        (
            "paragrahvi 4 lõige 2 muudetakse ja sõnastatakse järgmiselt: "
            "„(2) Nimetatud juhul ei kohaldata Eesti territooriumi haldusjaotuse seaduse "
            "§ 9 lõikes 3 1, lõike 8 teises lauses, lõike 9 punktis 2 ja lõikes 13 "
            "sätestatut.”"
        ),
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].sentence_target_meta is None


def test_extract_ee_ops_plural_subsection_sentence_replace_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 7 lõiked 4 ja 5 teine lause muudetakse ja sõnastatakse järgmiselt: „Uus teine lause.”",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 2
    assert all(
        inst.sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(1,))
        for inst in instructions
    )


def test_extract_ee_ops_plural_subsection_sentence_insert_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 20 täiendatakse lõigetega 2 ja 3 pärast esimest lauset lausega järgmises sõnastuses: „Lisatud lause.”",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 2
    assert all(
        inst.sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(0,), mode="insert_after")
        for inst in instructions
    )


def test_extract_ee_ops_plural_item_sentence_replace_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 12 lõike 1 punktid 2 ja 3 teine lause muudetakse ja sõnastatakse järgmiselt: „Uus teine lause.”",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 2
    assert all(
        inst.sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(1,))
        for inst in instructions
    )


def test_extract_ee_ops_plural_item_sentence_insert_exposes_sentence_target_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 12 lõike 1 punkte 2 ja 3 täiendatakse pärast esimest lauset lausega järgmises sõnastuses: „Lisatud lause.”",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 2
    assert all(
        inst.sentence_target_meta == EESentenceTargetMeta(sentence_indexes=(0,), mode="insert_after")
        for inst in instructions
    )


def test_extract_ee_ops_plural_subsection_repeal_exposes_subsection_selection_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 14 lõiked 2–4 tunnistatakse kehtetuks.",
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 3
    assert all(
        inst.subsection_selection_meta
        == EESubsectionSelectionMeta(
            explicit_labels=("2", "3", "4"),
            plain_numeric_ranges=(("2", "4"),),
            label_ranges=(("2", "4"),),
        )
        for inst in instructions
    )


def test_to_ee_parsed_instructions_preserves_source_family() -> None:
    source = OperationSource(statute_id="ee/test", title="Taimekaitseseadus")
    ops = [
        LegalOperation(
            op_id="ee_test_generic_ministry_reorg",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=()),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="Regionaal- ja Põllumajandusministeerium",
                attrs={
                    "old_text": "Maaeluministeerium",
                    "case_inflected": True,
                    "source_family": "generic_ministry_reorganization",
                },
            ),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].rewrite is not None
    assert instructions[0].rewrite.source_family == "generic_ministry_reorganization"


def test_make_text_rewrite_witness_carries_appendix_metadata() -> None:
    witness = make_text_rewrite_witness(
        'paragrahvi 79, 5) lisa 1 tabelis muudetakse B- ja BE-kategooria veerg '
        'ning sõnastatakse järgmiselt: B auto; BE autorong;',
        new_surface="B auto; BE autorong;",
        appendix_table_update=True,
        appendix_marker="Lisa 1",
        appendix_table_categories=("B", "BE"),
    )

    assert witness.rewrite.appendix_table_update is True
    assert witness.rewrite.appendix_marker == "Lisa 1"
    assert witness.rewrite.appendix_table_categories == ("B", "BE")


def test_to_ee_parsed_instructions_maps_wrapper_quoted_payload_family() -> None:
    source = OperationSource(statute_id="ee/test", title="Toiduseadus")
    nested_clause = (
        "Toiduseaduse § 8 lõige 1 punktis 1² asendatakse sõnaga "
        '„koolieelne lasteasutus” sõnaga „lastehoid”.'
    )

    instructions = parse_wrapper_quoted_clause(nested_clause, source)

    assert len(instructions) == 1
    assert all(inst.family == EEInstructionFamily.wrapper_quoted_payload for inst in instructions)
    assert all(inst.is_wrapper_payload for inst in instructions)
    assert instructions[0].rewrite is not None
    assert instructions[0].rewrite.old_surface == "koolieelne lasteasutus"
    assert instructions[0].action == StructuralAction.TEXT_REPLACE
    assert instructions[0].target.path == (("section", "8"), ("subsection", "1"), ("item", "1_2"))
