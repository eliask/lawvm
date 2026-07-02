from __future__ import annotations

import pytest

from lawvm.core.ir import IRNode, LegalAddress, LegalOperation, OperationSource, StructuralAction
from lawvm.core.semantic_types import IRNodeKind
from lawvm.estonia.peg import extract_ee_ops
from lawvm.estonia.ee_instruction_waist import (
    EE_DIAGNOSTIC_CLAUSE_NOTE_PREFIX,
    EE_SELECTION_META_NOTE_PREFIX,
    EEInstructionFamily,
    EEItemSelectionMeta,
    EESectionSelectionMeta,
    EESelectionMetaCarrierError,
    EESentenceTargetMeta,
    EESubsectionSelectionMeta,
    EESubsectionTextScopeMeta,
    EETextReplaceMode,
    EETextRewrite,
    EETextRewriteWitness,
    EEUnparsedClauseDiagnostic,
    decode_ee_selection_meta_note,
    encode_ee_diagnostic_clause_note,
    encode_ee_selection_meta_note,
    make_item_selection_meta,
    make_section_selection_meta,
    make_sentence_target_meta,
    make_subsection_selection_meta,
    make_subsection_text_scope_meta,
    make_text_rewrite_witness,
    parse_wrapper_quoted_clause,
    read_ee_unparsed_clause_diagnostic,
    read_op_section_selection_meta,
    read_op_subsection_selection_meta,
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


def test_extract_ee_ops_insert_second_sentence_exposes_insert_position_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        "paragrahvi 82 täiendatakse teise lausega järgmises sõnastuses: "
        "„Lapsel ei või olla rohkem kui kaks vanemat.”",
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


def test_extract_ee_ops_plural_item_range_exposes_item_selection_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = extract_ee_ops(
        (
            "paragrahvi 6 lõike 10 punktid 7–15 sõnastatakse järgmiselt: "
            "„7) seitse; 8) kaheksa; 9) üheksa; 10) kümme; 11) üksteist; "
            "12) kaksteist; 13) kolmteist; 14) neliteist; 15) viisteist.“"
        ),
        source,
    )

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 9
    assert all(
        inst.item_selection_meta
        == EEItemSelectionMeta(
            explicit_labels=tuple(str(label) for label in range(7, 16)),
            plain_numeric_ranges=(("7", "15"),),
            label_ranges=(("7", "15"),),
        )
        for inst in instructions
    )


def test_to_ee_parsed_instructions_leaves_item_selection_meta_absent_without_payload_evidence() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = [
        LegalOperation(
            op_id="ee_test_no_item_selection_meta",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "6"), ("subsection", "10"), ("item", "7"))),
            payload=IRNode(kind=IRNodeKind.CONTENT, text="7) uus tekst."),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].item_selection_meta is None


def test_to_ee_parsed_instructions_preserves_explicit_item_selection_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    selection_meta = make_item_selection_meta(
        explicit_labels=("7", "8"),
        plain_numeric_ranges=(("7", "8"),),
        label_ranges=(("7", "8"),),
    )
    ops = [
        LegalOperation(
            op_id="ee_test_item_selection_meta",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "6"), ("subsection", "10"), ("item", "7"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="7) uus tekst.",
                attrs={"item_selection_meta": selection_meta},
            ),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].item_selection_meta == selection_meta


def test_to_ee_parsed_instructions_preserves_subsection_text_scope_and_postpass_meta() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    scope_meta = make_subsection_text_scope_meta(intro_only=True)
    ops = [
        LegalOperation(
            op_id="ee_test_subsection_scope_meta",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "6"), ("subsection", "10"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="uus sissejuhatav tekst",
                attrs={
                    "old_text": "vana sissejuhatav tekst",
                    "subsection_text_scope_meta": scope_meta,
                    "persistent_postpass": True,
                },
            ),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].subsection_text_scope_meta == EESubsectionTextScopeMeta(intro_only=True)
    assert instructions[0].persistent_postpass is True


def test_to_ee_parsed_instructions_leaves_scope_and_postpass_defaults_without_payload_evidence() -> None:
    source = OperationSource(statute_id="ee/test", title="Testseadus")
    ops = [
        LegalOperation(
            op_id="ee_test_no_subsection_scope_meta",
            sequence=1,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", "6"), ("subsection", "10"))),
            payload=IRNode(
                kind=IRNodeKind.CONTENT,
                text="uus sissejuhatav tekst",
                attrs={"old_text": "vana sissejuhatav tekst"},
            ),
            source=source,
        )
    ]

    instructions = to_ee_parsed_instructions(ops)

    assert len(instructions) == 1
    assert instructions[0].subsection_text_scope_meta is None
    assert instructions[0].persistent_postpass is False


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


def test_selection_meta_note_round_trips_section_and_subsection() -> None:
    section_meta = make_section_selection_meta(
        explicit_labels=("1", "2", "26_1"),
        plain_numeric_ranges=(("1", "25"),),
    )
    section_note = encode_ee_selection_meta_note(section_meta)
    assert section_note.startswith(EE_SELECTION_META_NOTE_PREFIX)
    decoded_section = decode_ee_selection_meta_note(section_note)
    assert isinstance(decoded_section, EESectionSelectionMeta)
    assert decoded_section == section_meta

    subsection_meta = make_subsection_selection_meta(
        explicit_labels=("2", "3", "4"),
        plain_numeric_ranges=(("2", "4"),),
        label_ranges=(("2", "4"),),
    )
    subsection_note = encode_ee_selection_meta_note(subsection_meta)
    decoded_subsection = decode_ee_selection_meta_note(subsection_note)
    assert isinstance(decoded_subsection, EESubsectionSelectionMeta)
    assert decoded_subsection == subsection_meta
    # Tuples survive the JSON list round-trip as tuples.
    assert decoded_subsection.plain_numeric_ranges == (("2", "4"),)
    assert decoded_subsection.label_ranges == (("2", "4"),)


def test_decode_selection_meta_note_raises_on_malformed() -> None:
    with pytest.raises(EESelectionMetaCarrierError):
        decode_ee_selection_meta_note("not-an-ee-note")
    with pytest.raises(EESelectionMetaCarrierError):
        decode_ee_selection_meta_note(EE_SELECTION_META_NOTE_PREFIX + "{not json")
    with pytest.raises(EESelectionMetaCarrierError):
        decode_ee_selection_meta_note(
            EE_SELECTION_META_NOTE_PREFIX + '{"kind":"bogus","data":{}}'
        )


def test_read_op_selection_meta_raises_on_duplicate_notes() -> None:
    note = encode_ee_selection_meta_note(
        make_subsection_selection_meta(explicit_labels=("1",))
    )
    op = LegalOperation(
        op_id="ee-repeal-dup",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=(("section", "1"), ("subsection", "1"))),
        payload=None,
        provenance_tags=("clause", note, note),
    )
    with pytest.raises(EESelectionMetaCarrierError):
        read_op_subsection_selection_meta(op)


def test_diagnostic_clause_note_round_trips() -> None:
    diag = EEUnparsedClauseDiagnostic(clause_text="midagi tundmatut", parser_action="repeal")
    note = encode_ee_diagnostic_clause_note(diag)
    assert note.startswith(EE_DIAGNOSTIC_CLAUSE_NOTE_PREFIX)
    op = LegalOperation(
        op_id="ee-unknown-1",
        sequence=1,
        action=StructuralAction.REPEAL,
        target=LegalAddress(path=()),
        payload=None,
        provenance_tags=("no_target: midagi tundmatut", note),
    )
    decoded = read_ee_unparsed_clause_diagnostic(op)
    assert decoded is not None
    assert decoded.clause_text == "midagi tundmatut"
    assert decoded.parser_action == "repeal"


def test_plural_subsection_repeal_ops_carry_no_payload_but_selection_note() -> None:
    ops = extract_ee_ops(
        "paragrahvi 30 lõiked 12, 13 ja 18–20 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )
    repeal_ops = [op for op in ops if op.action == StructuralAction.REPEAL]
    assert repeal_ops, "expected at least one REPEAL op"
    for op in repeal_ops:
        assert op.payload is None
        meta = read_op_subsection_selection_meta(op)
        assert meta is not None
        assert meta.explicit_labels == ("12", "13", "18", "19", "20")
        assert meta.plain_numeric_ranges == (("18", "20"),)


def test_plural_section_repeal_ops_carry_no_payload_but_selection_note() -> None:
    ops = extract_ee_ops(
        "paragrahvid 42–42 2 tunnistatakse kehtetuks;",
        OperationSource(statute_id="ee/test", raw_text="test"),
    )
    repeal_ops = [op for op in ops if op.action == StructuralAction.REPEAL]
    assert repeal_ops, "expected at least one REPEAL op"
    for op in repeal_ops:
        assert op.payload is None
        meta = read_op_section_selection_meta(op)
        assert meta is not None
        assert meta.explicit_labels == ("42", "42_1", "42_2")
