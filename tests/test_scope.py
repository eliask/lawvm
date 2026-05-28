from types import SimpleNamespace
from typing import Any, cast

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import FacetKind, IRNodeKind
from lawvm.finland.scope import (
    _johtolause_explicitly_binds_chapter_section,
    _johtolause_explicitly_mentions_chaptered_section_target,
    assign_chapter_scope_from_johtolause,
    assign_scope_from_renumber_destinations,
    strip_unjustified_chapter_scope_from_unique_sections,
)
from lawvm.finland.ops import ScopeConfidence, lo_with_scope_confidence


def test_strip_unjustified_chapter_scope_keeps_explicit_chapters_when_master_is_flat() -> None:
    master = SimpleNamespace(
        ir=SimpleNamespace(kind=IRNodeKind.BODY, children=()),
        find_section=lambda section, chapter=None: None,
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="t1",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "2"), ("section", "2a"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "lisätään 2 lukuun uusi 2 a §",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "2"), ("section", "2a"))


def test_strip_unjustified_chapter_scope_keeps_grouped_insert_chunk_binding() -> None:
    master = SimpleNamespace(
        ir=SimpleNamespace(
            kind=IRNodeKind.BODY,
            children=(SimpleNamespace(kind=IRNodeKind.CHAPTER, label="1", children=()),),
        ),
        find_section=lambda section, chapter=None: None,
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="t2",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "2"), ("section", "2a"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "lisätään 2 lukuun uusi 2 a, 3 a ja 7 a §",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "2"), ("section", "2a"))


def test_strip_unjustified_chapter_scope_strips_facet_insert_from_carried_wrong_chapter() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="1", children=()),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            object() if section == "5" and chapter == "2" else None
        ),
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="t2a",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "1"), ("section", "5")), special=FacetKind.INTRO),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "lisätään 1 lukuun uusi 1 a § sekä 5 §:ään uusi johdantokappale",
        cast(Any, master),
    )

    assert got[0].target.path == (("section", "5"),)
    assert "chapter_scope_stripped_section_facet_insert" in got[0].provenance_tags


def test_strip_unjustified_chapter_scope_keeps_explicit_chaptered_facet_insert() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(kind=IRNodeKind.CHAPTER, label="1", children=()),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            object() if section == "5" and chapter == "2" else None
        ),
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="t2b",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "1"), ("section", "5")), special=FacetKind.INTRO),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "lisätään 1 luvun 5 §:ään uusi johdantokappale",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "1"), ("section", "5"))


def test_strip_unjustified_chapter_scope_keeps_explicit_renumber_scope() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(IRNode(kind=IRNodeKind.SECTION, label="11"),)),),
        ),
        find_section=lambda section, chapter=None: (
            object() if chapter == "1" and section == "11" else None
        ),
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="t3",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "4"), ("section", "11"))),
        provenance_tags=("renumber_clause",),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "muutetaan 4 luvun 3–10 §:n numero 44–51:ksi sekä 11 §:n numero 52:ksi",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "4"), ("section", "11"))


def test_johtolause_explicitly_binds_does_not_treat_plain_luku_as_section_scope() -> None:
    assert _johtolause_explicitly_binds_chapter_section(
        "muutetaan 3 luku, 23-25 §, 26 §:n 3 momentti sekä 43 §",
        "3",
        "26",
    ) is False


def test_johtolause_explicitly_binds_range_interior_section() -> None:
    # "8 lukuun uusi 31–33 §" covers §32 even though "32" doesn't appear literally.
    # Regression test for 2023/1308 §32 misrouted to chapter:1 instead of chapter:8.
    johto = "lisätään 8 lukuun uusi 31–33 §, seuraavasti:"
    assert _johtolause_explicitly_binds_chapter_section(johto, "8", "32") is True
    # Range endpoints are already handled by literal match; range check also fires for them
    assert _johtolause_explicitly_binds_chapter_section(johto, "8", "31") is True
    assert _johtolause_explicitly_binds_chapter_section(johto, "8", "33") is True
    # Different chapter → not bound
    assert _johtolause_explicitly_binds_chapter_section(johto, "9", "32") is False


def test_johtolause_explicitly_mentions_chaptered_section_target_requires_direct_pairing() -> None:
    assert _johtolause_explicitly_mentions_chaptered_section_target(
        "lisätään 1 luvun 5 §:n 1 momenttiin uusi 14 kohta",
        "1",
        "5",
    ) is True
    assert _johtolause_explicitly_mentions_chaptered_section_target(
        "lisätään 1 lukuun uusi 1 a §, 5 §:n 1 momenttiin uusi 14 kohta",
        "1",
        "5",
    ) is False


def test_strip_unjustified_chapter_scope_keeps_inline_same_label_move_targets() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="34"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="33"), IRNode(kind=IRNodeKind.SECTION, label="34")),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            next(
                (
                    child
                    for ch in (
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="5",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="34"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="6",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="33"), IRNode(kind=IRNodeKind.SECTION, label="34")),
                        ),
                    )
                    if ch.label == chapter
                    for child in ch.children
                    if child.kind == IRNodeKind.SECTION and child.label == section
                ),
                None,
            )
        ),
        duplicate_section_labels={"34"},
    )
    moved33 = LegalOperation(
        op_id="m1",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "5"), ("section", "33"))),
    )
    moved34 = LegalOperation(
        op_id="m2",
        sequence=2,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "5"), ("section", "34"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [moved33, moved34],
        "muutetaan 31-34 §, joista 33 ja 34 § samalla siirretään 5 lukuun",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "5"), ("section", "33"))
    assert got[1].target.path == (("chapter", "5"), ("section", "34"))


def test_strip_unjustified_chapter_scope_keeps_singular_same_label_move_target() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5b",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="29e"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="29e"),),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            next(
                (
                    child
                    for ch in (
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="5b",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="29e"),),
                        ),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="6",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="29e"),),
                        ),
                    )
                    if ch.label == chapter
                    for child in ch.children
                    if child.kind == IRNodeKind.SECTION and child.label == section
                ),
                None,
            )
        ),
        duplicate_section_labels={"29e"},
    )
    moved29e = LegalOperation(
        op_id="m3",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "5b"), ("section", "29e"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [moved29e],
        "muutetaan 29 a-29 d §, 29 e §, joka samalla siirretään 5 b lukuun, sekä 29 g ja 30-32 §",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "5b"), ("section", "29e"))
    assert "chapter_scope_stripped_unique_section" not in got[0].provenance_tags


def test_chapter_chunks_accept_genitive_luvun_form() -> None:
    from lawvm.finland.scope import chapter_chunks_from_johtolause

    assert chapter_chunks_from_johtolause(
        "muutetaan 3 luvun otsikko, 3 §:n 1 momentti, 4 § ja 5 a §"
    ) == [("3", " otsikko, 3 §:n 1 momentti, 4 § ja 5 a §")]


def test_assign_chapter_scope_from_explicit_chunk_for_unique_sections() -> None:
    text = (
        "muutetaan 3 luvun otsikko, 3 §:n 1 momentti, 4 §, "
        "5 §:n 2 momentin 3 ja 4 kohta sekä 5 a ja 8-10 §"
    )
    legal_ops = [
        LegalOperation(
            op_id="op1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "3"), ("subsection", "1"))),
        ),
        LegalOperation(
            op_id="op2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "4"),)),
        ),
        LegalOperation(
            op_id="op3",
            sequence=3,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"), ("subsection", "2"), ("item", "3"))),
        ),
        LegalOperation(
            op_id="op4",
            sequence=4,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5"), ("subsection", "2"), ("item", "4"))),
        ),
        LegalOperation(
            op_id="op5",
            sequence=5,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "5a"),)),
        ),
        LegalOperation(
            op_id="op6",
            sequence=6,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "8"),)),
        ),
        LegalOperation(
            op_id="op7",
            sequence=7,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "9"),)),
        ),
        LegalOperation(
            op_id="op8",
            sequence=8,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "10"),)),
        ),
    ]
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="3"),
                        IRNode(kind=IRNodeKind.SECTION, label="4"),
                        IRNode(kind=IRNodeKind.SECTION, label="5"),
                        IRNode(kind=IRNodeKind.SECTION, label="5a"),
                        IRNode(kind=IRNodeKind.SECTION, label="8"),
                        IRNode(kind=IRNodeKind.SECTION, label="9"),
                        IRNode(kind=IRNodeKind.SECTION, label="10"),),
                ),),
        ),
        find_section=lambda section, chapter=None: (
            object() if chapter == "3" and section in {"3", "4", "5", "5a", "8", "9", "10"} else None
        ),
        duplicate_section_labels=set(),
    )

    scoped = assign_chapter_scope_from_johtolause(legal_ops, text, cast(Any, master))

    for lo in scoped:
        assert ("chapter", "3") in lo.target.path
        assert "chapter_scope_from_johtolause" not in lo.provenance_tags
    explicit_chunk_ids = {lo.op_id for lo in scoped if "chapter_scope_from_explicit_chunk" in lo.provenance_tags}
    carry_forward_ids = {lo.op_id for lo in scoped if "chapter_scope_carry_forward" in lo.provenance_tags}
    assert explicit_chunk_ids == {"op1", "op2", "op3", "op5", "op6", "op7", "op8"}
    assert carry_forward_ids == {"op4"}
    assert getattr(scoped[0], "scope_confidence", None) is not None
    scope_confidence = cast(Any, scoped[0]).scope_confidence
    assert scope_confidence.tag == "chapter_scope_from_explicit_chunk"


def test_assign_chapter_scope_from_johtolause_respects_part_scope() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="I",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="1"), IRNode(kind=IRNodeKind.SECTION, label="2")),
                        ),
                    ),
                ),
                IRNode(
                    kind=IRNodeKind.PART,
                    label="II",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="1",
                            children=(
                                IRNode(kind=IRNodeKind.SECTION, label="1"),
                                IRNode(kind=IRNodeKind.SECTION, label="2"),
                                IRNode(kind=IRNodeKind.SECTION, label="3"),
                            ),
                        ),
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="2",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="3"),),
                        ),
                    ),
                ),
            ),
        ),
        duplicate_section_labels={"1", "2", "3"},
    )
    lo = LegalOperation(
        op_id="part_scope_1",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "II"), ("section", "3"))),
        destination=LegalAddress(path=(("section", "5"),)),
        provenance_tags=("renumber_clause",),
    )

    scoped = assign_chapter_scope_from_johtolause(
        [lo],
        (
            "muutetaan II osan 1 luvun 1 §:n numero 3:ksi, 2 §:n numero 4:ksi "
            "ja 3 §:n numero 5:ksi, 2 luvun 1 §:n numero 19:ksi"
        ),
        cast(Any, master),
    )

    assert scoped[0].target.path == (("part", "II"), ("chapter", "1"), ("section", "3"))
    assert "chapter_scope_from_johtolause" in scoped[0].provenance_tags
    scope_confidence = cast(Any, scoped[0]).scope_confidence
    assert scope_confidence.resolved_chapter == "1"
    assert scope_confidence.source == "johtolause"
    assert scope_confidence.confidence == "inferred"


def test_strip_scope_keeps_explicit_chunk_whole_section_target_for_later_body_backed_rewrite() -> None:
    lo = LegalOperation(
        op_id="op84",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("part", "3"), ("chapter", "7"), ("section", "84"))),
        provenance_tags=("chapter_scope_from_explicit_chunk",),
    )
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="84"),),
                        ),
                    ),
                ),
            ),
        ),
        duplicate_section_labels=set(),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "muutetaan III osan ja 7 luvun otsikko sekä 84 §",
        cast(Any, master),
    )

    assert got[0].target.path == (("part", "3"), ("chapter", "7"), ("section", "84"))
    assert "chapter_scope_stripped_unique_section" not in got[0].provenance_tags


def test_strip_scope_keeps_explicit_chunk_whole_section_target_from_scope_confidence() -> None:
    lo = lo_with_scope_confidence(
        LegalOperation(
            op_id="op84_conf",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("part", "3"), ("chapter", "7"), ("section", "84"))),
        ),
        ScopeConfidence(
            tag="chapter_scope_from_explicit_chunk",
            source="explicit_chunk",
            confidence="explicit",
            resolved_chapter="7",
        ),
    )
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.PART,
                    label="5",
                    children=(
                        IRNode(
                            kind=IRNodeKind.CHAPTER,
                            label="13",
                            children=(IRNode(kind=IRNodeKind.SECTION, label="84"),),
                        ),
                    ),
                ),
            ),
        ),
        duplicate_section_labels=set(),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "muutetaan III osan ja 7 luvun otsikko sekä 84 §",
        cast(Any, master),
    )

    assert got[0].target.path == (("part", "3"), ("chapter", "7"), ("section", "84"))
    assert "chapter_scope_stripped_unique_section" not in got[0].provenance_tags


def test_assign_chapter_scope_prefers_plain_section_chunk_over_subsection_mention() -> None:
    text = (
        "muutetaan 1 luvun 1 §:n 4 momentti, 2 §:n 4 momentti, "
        "6 §:n 1 momentti, 7 §:n 1 momentti, 2 luvun otsikko ja 1 §, "
        "2 §:n 2 momentti, 4 §:n 1 momentin 4 kohta, 8 §:n 4 momentti ja 10 §:n 1 momentti"
    )
    legal_ops = [
        LegalOperation(
            op_id="op1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "1"), ("section", "1"), ("subsection", "4"))),
        ),
        LegalOperation(
            op_id="op2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        ),
        LegalOperation(
            op_id="op3",
            sequence=3,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "2"), ("subsection", "2"))),
        ),
    ]
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"), IRNode(kind=IRNodeKind.SECTION, label="2")),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"), IRNode(kind=IRNodeKind.SECTION, label="2")),
                ),),
        ),
        find_section=lambda section, chapter=None: (
            object() if chapter in {"1", "2"} and section in {"1", "2"} else None
        ),
        duplicate_section_labels={"1", "2"},
    )

    scoped = assign_chapter_scope_from_johtolause(legal_ops, text, cast(Any, master))

    assert dict(scoped[1].target.path).get("chapter") == "2"
    assert "chapter_scope_from_johtolause" in scoped[1].provenance_tags
    assert dict(scoped[2].target.path).get("chapter") == "2"


def test_assign_chapter_scope_does_not_bind_plain_section_to_subsection_only_genitive() -> None:
    text = "muutetaan 1 luvun 1 §:n 4 momentti sekä 2 luvun otsikko ja 1 §"
    legal_ops = [
        LegalOperation(
            op_id="op1",
            sequence=1,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("chapter", "1"), ("section", "1"), ("subsection", "4"))),
        ),
        LegalOperation(
            op_id="op2",
            sequence=2,
            action=StructuralAction.REPLACE,
            target=LegalAddress(path=(("section", "1"),)),
        ),
    ]
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="1",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="2",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            object() if chapter in {"1", "2"} and section == "1" else None
        ),
        duplicate_section_labels={"1"},
    )

    scoped = assign_chapter_scope_from_johtolause(legal_ops, text, cast(Any, master))

    assert dict(scoped[1].target.path).get("chapter") == "2"
    assert "chapter_scope_from_johtolause" in scoped[1].provenance_tags


def test_assign_scope_from_renumber_destinations_carries_section_scope() -> None:
    renumber = LegalOperation(
        op_id="renumber_5_159",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "5"))),
        destination=LegalAddress(path=(("section", "159"),)),
    )
    insert = LegalOperation(
        op_id="insert_159_4",
        sequence=2,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "159"), ("subsection", "4"))),
    )

    scoped = assign_scope_from_renumber_destinations([renumber, insert])
    insert_path = dict(scoped[1].target.path)

    assert insert_path.get("part") == "III"
    assert insert_path.get("chapter") == "2"
    assert "grouped_part_scope" in scoped[1].provenance_tags
    assert "chapter_scope_carry_forward" in scoped[1].provenance_tags
    assert getattr(scoped[1], "scope_confidence", None) is not None
    scope_confidence = cast(Any, scoped[1]).scope_confidence
    assert scope_confidence.tag == "grouped_part_scope"
    assert scope_confidence.source == "grouped_part"
    assert scope_confidence.confidence == "inferred"
    assert scope_confidence.resolved_chapter == "2"


def test_assign_scope_from_renumber_destinations_consumes_carry_forward_once() -> None:
    renumber = LegalOperation(
        op_id="renumber_5_159",
        sequence=1,
        action=StructuralAction.RENUMBER,
        target=LegalAddress(path=(("part", "III"), ("chapter", "2"), ("section", "5"))),
        destination=LegalAddress(path=(("section", "159"),)),
    )
    first_insert = LegalOperation(
        op_id="insert_159_4",
        sequence=2,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "159"), ("subsection", "4"))),
    )
    second_insert = LegalOperation(
        op_id="insert_159_5",
        sequence=3,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "159"), ("subsection", "5"))),
    )

    scoped = assign_scope_from_renumber_destinations([renumber, first_insert, second_insert])

    first_path = dict(scoped[1].target.path)
    second_path = dict(scoped[2].target.path)

    assert first_path.get("part") == "III"
    assert first_path.get("chapter") == "2"
    assert "grouped_part_scope" in scoped[1].provenance_tags
    assert "chapter_scope_carry_forward" in scoped[1].provenance_tags
    assert getattr(scoped[1], "scope_confidence", None) is not None
    scope_confidence = cast(Any, scoped[1]).scope_confidence
    assert scope_confidence.tag == "grouped_part_scope"
    assert second_path.get("part") is None
    assert second_path.get("chapter") is None
    assert "grouped_part_scope" not in scoped[2].provenance_tags
    assert "chapter_scope_carry_forward" not in scoped[2].provenance_tags

def test_strip_unjustified_chapter_scope_keeps_insert_when_section_absent_from_stated_chapter() -> None:
    # Regression test for 2011/587 §4a.
    # §4a exists in chapter:15 (from a VÄLIAIKAINEN amendment) but NOT in chapter:3.
    # The johtolause inserts §4a into chapter:3 ("sekä lukuun uusi 4 a §" back-ref).
    # The unique-chapter check should NOT strip chapter:3 scope just because §4a
    # happens to live in chapter:15 — the INSERT is creating a genuinely new entry.
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="15",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="4a"),),
                ),
            ),
        ),
        find_section=lambda section, chapter=None: (
            next(
                (
                    child
                    for ch in (
                        IRNode(kind=IRNodeKind.CHAPTER, label="3", children=(IRNode(kind=IRNodeKind.SECTION, label="4"),)),
                        IRNode(kind=IRNodeKind.CHAPTER, label="15", children=(IRNode(kind=IRNodeKind.SECTION, label="4a"),)),
                    )
                    if ch.label == chapter
                    for child in ch.children
                    if child.kind == IRNodeKind.SECTION and child.label == section
                ),
                None,
            )
        ),
        duplicate_section_labels=set(),
    )
    insert_4a = LegalOperation(
        op_id="insert_3_4a",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "3"), ("section", "4a"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [insert_4a],
        # "lukuun" is a back-ref to "3 luvun" earlier in the sentence
        "lisätään 3 luvun 4 §:ään uusi 3 momentti sekä lukuun uusi 4 a §",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "3"), ("section", "4a"))
    assert "chapter_scope_stripped_unique_section" not in got[0].provenance_tags


from lawvm.core.ir import LegalAddress, LegalOperation, StructuralAction


def test_strip_subsection_insert_with_chapter_carryforward_different_chapter() -> None:
    """Regression: subsection-level INSERT with chapter carry-forward must have its
    chapter stripped when the section uniquely lives in a different chapter.

    Pattern: "lisätään 1 lukuun uusi 1 a §, 5 §:n 1 momenttiin uusi 14 kohta"
    produces INSERT chapter:1 section:5 subsection:1 item:14, but section:5 is
    in chapter:2. The carry-forward chapter:1 must be stripped so the op can
    find section:5 globally.

    Regression for 1984/602 + 1994/1317 FAILED ops.
    """
    ch1 = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        children=(IRNode(kind=IRNodeKind.SECTION, label="1"),),
    )
    ch2 = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2",
        children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
    )
    master = SimpleNamespace(
        ir=IRNode(kind=IRNodeKind.BODY, children=(ch1, ch2)),
        find_section=lambda section, chapter=None: (
            object() if chapter == "2" and section == "5" else None
        ),
        duplicate_section_labels=set(),
    )
    # Subsection-level INSERT: add item:14 to §5's subsection:1
    subsec_insert = LegalOperation(
        op_id="ins_sub",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(("chapter", "1"), ("section", "5"), ("subsection", "1"), ("item", "14"))
        ),
    )
    johto = "lisätään 1 lukuun uusi 1 a §, 5 §:n 1 momenttiin uusi 14 kohta"

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [subsec_insert], johto, cast(Any, master)
    )

    # Carry-forward chapter scope must be stripped so later phases can find the
    # real owning section.
    pd = dict(got[0].target.path)
    assert pd.get("chapter") is None, f"Expected chapter stripped, got path={dict(got[0].target.path)}"
    assert pd.get("section") == "5"
    assert "chapter_scope_stripped_subsection_insert" in got[0].provenance_tags


def test_strip_subsection_insert_keeps_direct_chaptered_section_phrase() -> None:
    ch1 = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        children=(),
    )
    ch2 = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="2",
        children=(IRNode(kind=IRNodeKind.SECTION, label="5"),),
    )
    master = SimpleNamespace(
        ir=IRNode(kind=IRNodeKind.BODY, children=(ch1, ch2)),
        find_section=lambda section, chapter=None: (
            object() if chapter == "2" and section == "5" else None
        ),
        duplicate_section_labels=set(),
    )
    subsec_insert = LegalOperation(
        op_id="ins_sub_direct",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(
            path=(("chapter", "1"), ("section", "5"), ("subsection", "1"), ("item", "14"))
        ),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [subsec_insert],
        "lisätään 1 luvun 5 §:n 1 momenttiin uusi 14 kohta",
        cast(Any, master),
    )

    pd = dict(got[0].target.path)
    assert pd.get("chapter") == "1"
    assert pd.get("section") == "5"
    assert "chapter_scope_stripped_subsection_insert" not in got[0].provenance_tags


def test_strip_whole_section_insert_with_chapter_carryforward_preserves_scope() -> None:
    """Regression guard: whole-section INSERT with chapter carry-forward where the
    section does NOT exist yet must preserve chapter scope (it's genuinely new there).
    """
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(IRNode(kind=IRNodeKind.CHAPTER, label="1", children=()),),
        ),
        find_section=lambda section, chapter=None: None,
        duplicate_section_labels=set(),
    )
    whole_insert = LegalOperation(
        op_id="ins_whole",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("chapter", "1"), ("section", "1a"))),
    )
    johto = "lisätään 1 lukuun uusi 1 a §"

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [whole_insert], johto, cast(Any, master)
    )

    # Chapter must NOT be stripped: §1a is genuinely new in chapter:1.
    pd = dict(got[0].target.path)
    assert pd.get("chapter") == "1"
    assert pd.get("section") == "1a"


def test_strip_unjustified_chapter_scope_requires_same_batch_chapter_anchor() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="23"),),
                ),
            ),
        ),
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="replace_3_23_no_anchor",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "3"), ("section", "23"))),
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo],
        "muutetaan 23 §",
        cast(Any, master),
    )

    assert dict(got[0].target.path).get("chapter") == "3"
    assert "chapter_scope_stripped_unique_section" not in got[0].provenance_tags


def test_duplicate_label_scope_without_chapter_anchor_is_not_stripped() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="10"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="5",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="23"),),
                ),
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="6",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="23"),),
                ),
            ),
        ),
        duplicate_section_labels={"23"},
    )
    lo = LegalOperation(
        op_id="replace_3_23",
        sequence=1,
        action=StructuralAction.REPLACE,
        target=LegalAddress(path=(("chapter", "3"), ("section", "23"))),
    )
    johto = (
        "muutetaan 2 §:n johdantokappale sekä 4-6, 10, 18 ja 20 kohta, "
        "5 ja 7 §, 3 luvun otsikko, 14 §, 18 §:n 4 momentti sekä 19 ja 23 §"
    )

    got = strip_unjustified_chapter_scope_from_unique_sections(
        [lo], johto, cast(Any, master)
    )

    assert dict(got[0].target.path).get("chapter") == "3"
    assert "chapter_scope_stripped_duplicate_label_outside_stated_chapter" not in got[0].provenance_tags


def test_assign_chapter_scope_from_johtolause_scopes_unique_insert_without_chapter_chunk() -> None:
    master = SimpleNamespace(
        ir=IRNode(
            kind=IRNodeKind.BODY,
            children=(
                IRNode(
                    kind=IRNodeKind.CHAPTER,
                    label="3",
                    children=(IRNode(kind=IRNodeKind.SECTION, label="10"),),
                ),
            ),
        ),
        duplicate_section_labels=set(),
    )
    lo = LegalOperation(
        op_id="insert_10_no_chunk",
        sequence=1,
        action=StructuralAction.INSERT,
        target=LegalAddress(path=(("section", "10"),)),
    )

    got = assign_chapter_scope_from_johtolause(
        [lo],
        "lisätään työjärjestykseen uusi 10 §, jolloin nykyinen 10 § siirtyy 10 a §:ksi",
        cast(Any, master),
    )

    assert got[0].target.path == (("chapter", "3"), ("section", "10"))
    assert "chapter_scope_carry_forward" in got[0].provenance_tags
