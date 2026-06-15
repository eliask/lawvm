"""Unit tests for lawvm.finland.merge — IRNode-level merge functions."""
from typing import Literal

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.merge import (
    _has_section_omissions_ir,
    _heading_intro_replace_preserve_items_ir,
    _merge_sparse_item_subsection_ir,
    _merge_section_with_targeted_ops_ir,
    _merge_same_numbered_container_insert_ir,
    _merge_section_with_omission_ir,
    _merge_subsection_with_omission_ir,
    _multi_subsection_sparse_item_section_replace_merge_ir,
    _paragraph_signatures_ir,
)
from lawvm.finland.ops import AmendmentOp

# ---------------------------------------------------------------------------
# IRNode fixture helpers
# ---------------------------------------------------------------------------


def _sub(label: str, *children: IRNode, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, text=text, children=tuple(children))


def _para(label: str, text: str = "") -> IRNode:
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, text=text)


def _omission() -> IRNode:
    return IRNode(kind=IRNodeKind.OMISSION)


def _sec(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SECTION, label=label, children=tuple(children))


def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _op(
    op_type: Literal["REPLACE", "INSERT", "REPEAL"],
    *,
    target_section: str,
    target_paragraph: int | None = None,
) -> AmendmentOp:
    return AmendmentOp(
        op_id=f"{op_type.lower()}_{target_section}_{target_paragraph or 'sec'}",
        op_type=op_type,
        target_section=target_section,
        target_unit_kind="section",
        target_paragraph=target_paragraph,
        source_statute="2006/395",
    )


# ---------------------------------------------------------------------------
# _has_section_omissions_ir
# ---------------------------------------------------------------------------


def test_has_section_omissions_detects_direct_omission_child() -> None:
    sec = _sec("3", _omission())
    assert _has_section_omissions_ir(sec) is True


def test_has_section_omissions_detects_nested_omission_inside_subsection() -> None:
    sec = _sec("3", _sub("1", _omission()))
    assert _has_section_omissions_ir(sec) is True


def test_has_section_omissions_false_when_no_omissions() -> None:
    sec = _sec("3", _sub("1", _para("1", "text")))
    assert _has_section_omissions_ir(sec) is False


def test_has_section_omissions_false_for_empty_section() -> None:
    sec = _sec("3")
    assert _has_section_omissions_ir(sec) is False


# ---------------------------------------------------------------------------
# _merge_subsection_with_omission_ir
# ---------------------------------------------------------------------------


def test_merge_subsection_with_trailing_omission_fills_from_master() -> None:
    master_sub = _sub("1",
        _para("1", "Master p1"),
        _para("2", "Master p2"),
        _para("3", "Master p3"),
    )
    amend_sub = _sub("1",
        _para("1", "Amended p1"),
        _omission(),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    # Paragraphs 2 and 3 from master should be filled in
    labels = [c.label for c in result.children]
    assert "1" in labels
    assert "2" in labels
    assert "3" in labels


def test_merge_subsection_with_omission_returns_none_when_no_omission() -> None:
    master_sub = _sub("1", _para("1", "p1"))
    amend_sub = _sub("1", _para("1", "amended p1"))

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is None


def test_merge_subsection_omission_preserves_amended_leading_para() -> None:
    master_sub = _sub("1",
        _para("1", "Master p1"),
        _para("2", "Master p2"),
    )
    amend_sub = _sub("1",
        _para("1", "New text for p1"),
        _omission(),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    first_para = next(c for c in result.children if c.kind is IRNodeKind.PARAGRAPH and c.label == "1")
    assert first_para.text == "New text for p1"


def test_merge_subsection_omission_with_trailing_explicit_para() -> None:
    """Trailing explicit para after omission should appear at the end."""
    master_sub = _sub("1",
        _para("1", "M1"),
        _para("2", "M2"),
        _para("3", "M3"),
    )
    amend_sub = _sub("1",
        _para("1", "A1"),
        _omission(),
        _para("3", "A3-new"),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    texts = {c.label: c.text for c in result.children if c.kind is IRNodeKind.PARAGRAPH}
    assert texts.get("1") == "A1"
    assert texts.get("3") == "A3-new"


def test_merge_subsection_trailing_omission_no_duplicate_when_last_amend_matches_first_splice() -> None:
    """Regression: 2015/548 ch4 s20 amended by 2017/829.

    Amendment provides items 1-6 explicitly, then trailing omission.
    Master has items 1-7 where old item 7 = amendment's new item 6 (renumbered).
    The trailing omission must NOT re-splice master item 7 because the amendment
    already absorbed that content as its item 6.
    Result: 6 paragraphs with no duplicate text.
    """
    shared_text = "vangin velvollisuus esittää palkkatiedot."
    master_sub = _sub("1",
        _para("1", "päivittäinen lähtöaika;"),
        _para("2", "kulkuväline;"),
        _para("3", "työn sisältö;"),
        _para("4", "luvan valvontatapa;"),
        _para("5", "työpaikan yhdyshenkilö;"),
        _para("6", "ruoka- ja ylläpitokorvauksen periminen;"),  # deleted by amendment
        _para("7", shared_text),                                # renumbered to 6
    )
    amend_sub = _sub("1",
        _para("1", "päivittäinen lähtöaika;"),
        _para("2", "kulkuväline;"),
        _para("3", "työn sisältö;"),
        _para("4", "luvan valvontatapa;"),
        _para("5", "työpaikan yhdyshenkilö;"),
        _para("6", shared_text),    # old item 7 renumbered to 6
        _omission(),                # trailing omission — NOT a pointer to preserved tail
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    paras = [c for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
    assert len(paras) == 6, (
        f"Expected 6 paragraphs, got {len(paras)}: {[p.text for p in paras]}"
    )
    texts = [p.text for p in paras]
    assert texts.count(shared_text) == 1, (
        f"Duplicate detected — '{shared_text}' appears {texts.count(shared_text)} times"
    )
    assert texts[-1] == shared_text
    assert "ruoka- ja ylläpitokorvauksen periminen;" not in texts


def test_merge_subsection_trailing_omission_no_duplicate_with_numbering_prefix() -> None:
    """Regression: 2016/447 ch3 s7 amended by 2020/743.

    Amendment restructures an 8-item list into 7 items: some items merge, and
    the last amendment item ('7) toimiala...') has the same base text as master's
    last item ('8) toimiala...') with only the number prefix differing.
    The trailing omission must NOT re-splice master item 8 because the amendment
    already absorbed that content as its item 7 (renumbered).
    Result: 7 paragraphs with no duplicate.
    """
    shared_base = "toimiala, jolla lähetetty työntekijä tulee työskentelemään."
    master_sub = _sub("3",
        _para("1", "1) lähettävän yrityksen yksilöintitiedot;"),
        _para("2", "2) tilaajan tiedot;"),
        _para("3", "3) rakennusalan tiedot;"),
        _para("4", "4) lähetettyjen työntekijöiden ennakoitu lukumäärä;"),
        _para("5", "5) edustajan tiedot;"),
        _para("6", "6) lähettämisen alkamispäivä;"),
        _para("7", "7) työntekopaikka;"),
        _para("8", "8) " + shared_base),   # renumbered to 7 in amendment
    )
    amend_sub = _sub("3",
        _para("1", "1) lähettävän yrityksen yksilöintitiedot;"),
        _para("2", "2) tilaajan tiedot;"),
        _para("3", "3) rakennusalan tiedot;"),
        _para("4", "4) kunkin lähetetyn työntekijän henkilötiedot;"),
        _para("5", "5) edustajan tiedot;"),
        _para("6", "6) työntekopaikka tai -paikat;"),
        _para("7", "7) " + shared_base),   # old item 8 renumbered to 7
        _omission(),                        # trailing omission — no tail to preserve
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    paras = [c for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
    assert len(paras) == 7, (
        f"Expected 7 paragraphs, got {len(paras)}: {[p.text for p in paras]}"
    )
    texts = [p.text for p in paras]
    # Item 7 should appear exactly once (not duplicated as 7 and 8)
    assert sum(shared_base in t for t in texts) == 1, (
        f"Duplicate base text detected in: {texts}"
    )


def test_merge_subsection_single_content_replacement_no_master_content_appended() -> None:
    """Regression: 2015/1589 s1 amended by 2024/978.

    Amendment subsection contains new replacement content followed by a trailing
    omission.  Master subsection has exactly one content node (old text).
    The trailing omission signals end-of-amendment, NOT 'append master content'.
    Result: one content node with the amendment text; master text must not appear.
    """
    master_sub = _sub("2",
        _content("Vanhan vakuutusyhtiön on ilmoitettava esteestä seitsemän päivän kuluessa."),
    )
    amend_sub = _sub("2",
        _content("Vanhan vakuutusyhtiön on ilmoitettava esteestä kahden viikon kuluessa."),
        _omission(),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    content_nodes = [c for c in result.children if c.kind is IRNodeKind.CONTENT]
    assert len(content_nodes) == 1, (
        f"Expected 1 content node, got {len(content_nodes)}: {[c.text for c in content_nodes]}"
    )
    assert "kahden viikon" in content_nodes[0].text
    assert "seitsemän päivän" not in content_nodes[0].text


def test_multi_subsection_sparse_item_section_replace_merge_preserves_untouched_items() -> None:
    master_sec = _sec(
        "24",
        _sub("1", _content("Ensimmäinen momentti.")),
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriön asetuksella annetaan:"),
            _para("1", "one"),
            _para("2", "two"),
            _para("3", "three"),
            _para("4", "four"),
            _para("5", "five"),
            _para("6", "six-old"),
            _para("7", "seven"),
            _para("8", "eight-old"),
        ),
        _sub("3", _content("Kolmas momentti.")),
    )
    amend_sec = _sec(
        "24",
        _sub("1", _content("Ensimmäinen momentti.")),
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriön asetuksella annetaan:"),
            _para("6", "six-new"),
            _para("8", "eight-new"),
        ),
        _sub("3", _content("Kolmas momentti.")),
    )

    result = _multi_subsection_sparse_item_section_replace_merge_ir(master_sec, amend_sec)

    assert result is not None
    merged_sub2 = next(child for child in result.children if child.kind is IRNodeKind.SUBSECTION and child.label == "2")
    texts = {child.label: child.text for child in merged_sub2.children if child.kind is IRNodeKind.PARAGRAPH}
    assert texts == {
        "1": "one",
        "2": "two",
        "3": "three",
        "4": "four",
        "5": "five",
        "6": "six-new",
        "7": "seven",
        "8": "eight-new",
    }


def test_multi_subsection_sparse_item_section_replace_merge_skips_complete_subsection_payload() -> None:
    master_sec = _sec(
        "24",
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriön asetuksella annetaan:"),
            _para("1", "one"),
            _para("2", "two"),
            _para("3", "three"),
            _para("4", "four"),
        ),
    )
    amend_sec = _sec(
        "24",
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Ministeriön asetuksella annetaan:"),
            _para("1", "one-new"),
            _para("2", "two-new"),
            _para("3", "three-new"),
            _para("4", "four-new"),
        ),
    )

    result = _multi_subsection_sparse_item_section_replace_merge_ir(master_sec, amend_sec)

    assert result is None


def test_merge_sparse_item_subsection_ir_preserves_neighbors_in_three_item_subsection() -> None:
    master_sub = _sub(
        "2",
        IRNode(kind=IRNodeKind.INTRO, text="Tietoja saadaan luovuttaa, jos ne ovat välttämättömiä:"),
        _para("1", "one"),
        _para("2", "two-old"),
        _para("3", "three"),
    )
    amend_sub = _sub(
        "2",
        IRNode(kind=IRNodeKind.INTRO, text="Tietoja saadaan luovuttaa, jos ne ovat välttämättömiä:"),
        _para("2", "two-new"),
    )

    result = _merge_sparse_item_subsection_ir(master_sub, amend_sub)

    assert result is not None
    texts = {child.label: child.text for child in result.children if child.kind is IRNodeKind.PARAGRAPH}
    assert texts == {"1": "one", "2": "two-new", "3": "three"}


def test_merge_sparse_item_subsection_ir_uses_amend_intro_when_item_subset_is_sparse() -> None:
    master_sub = _sub(
        "1",
        IRNode(kind=IRNodeKind.INTRO, text="Vanha johdanto:"),
        _para("1", "one"),
        _para("2", "two-old"),
        _para("3", "three"),
        _para("4", "four"),
    )
    amend_sub = _sub(
        "1",
        IRNode(kind=IRNodeKind.INTRO, text="Uusi johdanto:"),
        _para("2", "two-new"),
        _para("3", "three-new"),
    )

    result = _merge_sparse_item_subsection_ir(master_sub, amend_sub)

    assert result is not None
    intro = next(child for child in result.children if child.kind is IRNodeKind.INTRO)
    texts = {child.label: child.text for child in result.children if child.kind is IRNodeKind.PARAGRAPH}
    assert intro.text == "Uusi johdanto:"
    assert texts == {"1": "one", "2": "two-new", "3": "three-new", "4": "four"}


def test_multi_subsection_sparse_item_section_replace_merge_uses_positional_match_for_unlabeled_subsections() -> None:
    master_sec = _sec(
        "39",
        _sub(
            "1",
            IRNode(kind=IRNodeKind.INTRO, text="Rajavartiolaitos saa luovuttaa tietoja:"),
            _para("1", "one"),
            _para("2", "two-old"),
            _para("3", "three-old"),
            _para("4", "four-old"),
            _para("5", "five"),
            _para("6", "six"),
            _para("7", "seven"),
        ),
        _sub(
            "2",
            IRNode(kind=IRNodeKind.INTRO, text="Tietoja saadaan luovuttaa myös, jos ne ovat välttämättömiä:"),
            _para("1", "one-b"),
            _para("2", "two-b-old"),
            _para("3", "three-b"),
        ),
        _sub("3", _content("third moment")),
    )
    amend_sec = _sec(
        "39",
        _sub(
            "",
            IRNode(kind=IRNodeKind.INTRO, text="Rajavartiolaitos saa luovuttaa tietoja:"),
            _omission(),
            _para("2", "two-new"),
            _para("3", "three-new"),
            _para("4", "four-new"),
        ),
        _sub(
            "",
            IRNode(kind=IRNodeKind.INTRO, text="Tietoja saadaan luovuttaa myös, jos ne ovat välttämättömiä:"),
            _para("2", "two-b-new"),
        ),
        _sub("", _content("third moment")),
    )

    result = _multi_subsection_sparse_item_section_replace_merge_ir(master_sec, amend_sec)

    assert result is not None
    merged_subs = [child for child in result.children if child.kind is IRNodeKind.SUBSECTION]
    sub1 = merged_subs[0]
    sub2 = merged_subs[1]
    sub1_texts = {child.label: child.text for child in sub1.children if child.kind is IRNodeKind.PARAGRAPH}
    sub2_texts = {child.label: child.text for child in sub2.children if child.kind is IRNodeKind.PARAGRAPH}

    assert sub1_texts == {
        "1": "one",
        "2": "two-new",
        "3": "three-new",
        "4": "four-new",
        "5": "five",
        "6": "six",
        "7": "seven",
    }
    assert sub2_texts == {
        "1": "one-b",
        "2": "two-b-new",
        "3": "three-b",
    }


# ---------------------------------------------------------------------------
# _merge_section_with_omission_ir
# ---------------------------------------------------------------------------


def test_merge_section_with_omission_replaces_omission_with_master_subsections() -> None:
    master_sec = _sec("3",
        _sub("1", _para("1", "M1")),
        _sub("2", _para("1", "M2")),
        _sub("3", _para("1", "M3")),
    )
    amend_sec = _sec("3",
        _sub("1", _para("1", "A1")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    subsec_labels = [c.label for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert "1" in subsec_labels
    assert "2" in subsec_labels
    assert "3" in subsec_labels


def test_merge_section_returns_none_when_no_omission_and_no_inner_omission() -> None:
    master_sec = _sec("3", _sub("1", _para("1", "M1")))
    amend_sec = _sec("3", _sub("1", _para("1", "A1")))

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    # No omission at section level, no inner omission -> returns None
    assert result is None


def test_merge_section_with_omission_1to1_mapping() -> None:
    """1:1 case: same number of slots as master subsections."""
    master_sec = _sec("5",
        _sub("1", _para("1", "M1")),
        _sub("2", _para("1", "M2")),
    )
    amend_sec = _sec("5",
        _omission(),
        _sub("2", _para("1", "A2")),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    subsec_labels = [c.label for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert "1" in subsec_labels
    assert "2" in subsec_labels


def test_merge_section_with_nested_subsection_omission_preserves_master_tail() -> None:
    """A subsection with an inner omission must not swallow the master's tail.

    Regression for the 1981/555 §11 shape: a section-level omission followed by
    a subsection that itself ends in an omission should leave the untouched
    master tail subsection in place.
    """
    master_sec = _sec(
        "11",
        _sub("1", _content("M1")),
        _sub("2", _content("M2")),
        _sub("3", _content("M3")),
        _sub("4", _content("M4")),
    )
    amend_sec = _sec(
        "11",
        _sub("1", _content("A1")),
        _omission(),
        _sub(
            "2",
            _content("A2"),
            _omission(),
        ),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    result_subsec_labels = [c.label for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert result_subsec_labels == ["1", "2", "3", "4"]
    tail_sub = next(c for c in result.children if c.kind is IRNodeKind.SUBSECTION and c.label == "4")
    tail_text = " ".join(c.text or "" for c in tail_sub.children if c.text)
    assert tail_text == "M4"


def test_merge_section_with_omission_relabels_trailing_subsections_to_master_slots() -> None:
    """Trailing explicit subsections should bind to the master slots they replace.

    Regression for statutes where the amendment XML keeps source labels that do
    not match the master slot numbers after an omission.
    """
    master_sec = _sec(
        "11",
        _sub("1", _content("M1")),
        _sub("2", _content("M2")),
        _sub("3", _content("M3")),
        _sub("4", _content("M4")),
    )
    amend_sec = _sec(
        "11",
        _sub("1", _content("A1")),
        _omission(),
        _sub("2", _content("A2")),
        _sub("3", _content("A3")),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    result_subsec_labels = [c.label for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert result_subsec_labels == ["1", "2", "3", "4"]
    assert "M4" in " ".join(c.text or "" for c in next(c for c in result.children if c.kind is IRNodeKind.SUBSECTION and c.label == "4").children)


def test_merge_section_with_leading_omission_anchors_insert_to_matching_master_slot() -> None:
    """Leading omission should anchor the first explicit subsection to its real master slot.

    Regression for 2006/395 §118 and §205: a single omission marker does not mean
    one preserved subsection. If the first explicit amendment subsection is 4,
    it must replace master slot 4, not master slot 2.
    """
    master_sec = _sec(
        "3",
        _sub("1", _para("1", "M1")),
        _sub("2", _para("1", "M2")),
        _sub("3", _para("1", "M3")),
        _sub("4", _para("1", "M4")),
    )
    amend_sec = _sec(
        "3",
        _omission(),
        _sub("4", _para("1", "A4")),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3", "4"]
    sub2 = next(c for c in result_subsecs if c.label == "2")
    sub4 = next(c for c in result_subsecs if c.label == "4")
    assert "M2" in irnode_to_text(sub2)
    assert "A4" in irnode_to_text(sub4)
    assert "M4" not in irnode_to_text(sub4)


def test_merge_section_with_targeted_insert_and_shifted_replace_preserves_tail() -> None:
    """Whole-section omission merge should honor plain subsection ops from johtolause.

    Regression for 2006/395 §32: `INSERT 1 mom` plus a carried replacement
    subsection must yield `[new1, changed_old1, old2]`, not drop old2.
    """
    master_sec = _sec(
        "32",
        _sub("1", _content("old 1")),
        _sub("2", _content("old 2")),
    )
    amend_sec = _sec(
        "32",
        _sub("1", _content("new 1")),
        _sub("2", _content("changed old 1")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[_op("INSERT", target_section="32", target_paragraph=1)],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3"]
    assert irnode_to_text(result_subsecs[0]).strip() == "new 1"
    assert irnode_to_text(result_subsecs[1]).strip() == "changed old 1"
    assert irnode_to_text(result_subsecs[2]).strip() == "old 2"


def test_merge_section_with_targeted_insert_before_live_tail_preserves_shifted_suffix() -> None:
    """Leading/trailing omission with INSERT target preserves the shifted live tail.

    Regression for 2006/395 §118: `INSERT 4 mom` must produce a new 4th moment
    and keep the former 4th moment as new 5th.
    """
    master_sec = _sec(
        "118",
        _sub("1", _content("old 1")),
        _sub("2", _content("old 2")),
        _sub("3", _content("old 3")),
        _sub("4", _content("old 4")),
    )
    amend_sec = _sec(
        "118",
        _omission(),
        _sub("1", _content("new 4")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[_op("INSERT", target_section="118", target_paragraph=4)],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3", "4", "5"]
    assert irnode_to_text(result_subsecs[3]).strip() == "new 4"
    assert irnode_to_text(result_subsecs[4]).strip() == "old 4"


def test_merge_section_with_targeted_replaces_and_tail_insert_keeps_middle_master_subsection() -> None:
    """Targeted section ops should preserve untouched middle master subsections.

    Regression for 2006/395 §122: `REPLACE 1`, `REPLACE 2`, `INSERT 4` must
    keep the old 3rd moment in place and append the new 4th moment after it.
    """
    master_sec = _sec(
        "122",
        _sub("1", _content("old 1")),
        _sub("2", _content("old 2")),
        _sub("3", _content("old 3")),
    )
    amend_sec = _sec(
        "122",
        _sub("1", _content("new 1")),
        _sub("2", _content("new 2")),
        _omission(),
        _sub("3", _content("new 4")),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[
            _op("REPLACE", target_section="122", target_paragraph=1),
            _op("REPLACE", target_section="122", target_paragraph=2),
            _op("INSERT", target_section="122", target_paragraph=4),
        ],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3", "4"]
    assert irnode_to_text(result_subsecs[0]).strip() == "new 1"
    assert irnode_to_text(result_subsecs[1]).strip() == "new 2"
    assert irnode_to_text(result_subsecs[2]).strip() == "old 3"
    assert irnode_to_text(result_subsecs[3]).strip() == "new 4"


def test_merge_section_targeted_replace_with_trailing_omission_preserves_all_master_tail() -> None:
    """Targeted REPLACE(1) plus trailing omission must preserve all following master subsections.

    Regression for 2009/617 §15 / amendment 2016/533: `REPLACE 1 mom` with a
    trailing omission means "replace subsec 1, preserve the rest".  Before the
    fix, _merge_section_with_targeted_ops_ir heuristically skipped the first
    unprocessed master subsection (subsec 2) as a "redundant trailing" entry,
    losing it permanently and causing a later REPEAL of subsec 3 to fail.
    """
    master_sec = _sec(
        "15",
        _sub("1", _content("old 1 item list")),
        _sub("2", _content("old 2 plain")),
        _sub("3", _content("old 3 plain")),
    )
    amend_sec = _sec(
        "15",
        _sub("1", _content("new 1 item list")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[
            _op("REPLACE", target_section="15"),
            _op("REPLACE", target_section="15", target_paragraph=1),
        ],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3"]
    assert irnode_to_text(result_subsecs[0]).strip() == "new 1 item list"
    assert irnode_to_text(result_subsecs[1]).strip() == "old 2 plain"
    assert irnode_to_text(result_subsecs[2]).strip() == "old 3 plain"


def test_merge_section_targeted_replace_with_explicit_child_repeal_skips_repealed_slot() -> None:
    """Targeted omission merge must not preserve a master slot explicitly repealed in the same group.

    Regression for 1990/1341 <- 2010/512 §8 a: source body carries only
    changed `1 mom`, omission, and changed `5 mom`, while johtolause also
    explicitly repeals `2 mom`. The omission merge must preserve only live
    `3-4 mom`, not resurrect old `2 mom`.
    """
    master_sec = _sec(
        "8a",
        _sub("1", _content("old 1")),
        _sub("2", _content("old 2")),
        _sub("3", _content("old 3")),
        _sub("4", _content("old 4")),
        _sub("5", _content("old 5")),
    )
    amend_sec = _sec(
        "8a",
        _sub("1", _content("new 1")),
        _omission(),
        _sub("2", _content("new 5")),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[
            _op("REPEAL", target_section="8a", target_paragraph=2),
            _op("REPLACE", target_section="8a"),
            _op("REPLACE", target_section="8a", target_paragraph=1),
            _op("REPLACE", target_section="8a", target_paragraph=5),
        ],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "3", "4", "5"]
    assert irnode_to_text(result_subsecs[0]).strip() == "new 1"
    assert irnode_to_text(result_subsecs[1]).strip() == "old 3"
    assert irnode_to_text(result_subsecs[2]).strip() == "old 4"
    assert irnode_to_text(result_subsecs[3]).strip() == "new 5"


def test_merge_section_with_targeted_replace_after_leading_omission_preserves_prefix() -> None:
    """Leading omission plus targeted REPLACE must preserve the untouched prefix.

    Regression for 2006/395 §205: `REPLACE 3 mom` must leave 1 and 2 intact,
    replace 3, and keep the tail unchanged.
    """
    master_sec = _sec(
        "205",
        _sub("1", _content("old 1")),
        _sub("2", _content("old 2")),
        _sub("3", _content("old 3")),
        _sub("4", _content("old 4")),
    )
    amend_sec = _sec(
        "205",
        _omission(),
        _sub("1", _content("new 3")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(
        master_sec,
        amend_sec,
        group_ops=[_op("REPLACE", target_section="205", target_paragraph=3)],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert [c.label for c in result_subsecs] == ["1", "2", "3", "4"]
    assert irnode_to_text(result_subsecs[0]).strip() == "old 1"
    assert irnode_to_text(result_subsecs[1]).strip() == "old 2"
    assert irnode_to_text(result_subsecs[2]).strip() == "new 3"
    assert irnode_to_text(result_subsecs[3]).strip() == "old 4"


def test_merge_section_with_omission_trims_duplicate_prefix_from_preserved_master_prefix() -> None:
    """Sparse section merges should not keep the same carried prose twice.

    When a later amendment subsection restates prose that was already carried
    in the untouched master prefix, the preserved prefix should lose that
    duplicated leading prose while keeping its remaining structure.
    """
    master_sec = _sec(
        "3",
        _sub("1", _content("Alpha sentence. Beta sentence."), _para("1", "M1")),
        _sub("2", _content("M2")),
        _sub("3", _content("M3")),
    )
    amend_sec = _sec(
        "3",
        _omission(),
        _sub("2", _content("Updated middle")),
        _sub("3", _content("Alpha sentence.")),
        _omission(),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    result_sub1 = next(c for c in result.children if c.kind is IRNodeKind.SUBSECTION and c.label == "1")
    result_text = irnode_to_text(result_sub1)
    assert result_text.count("Alpha sentence.") == 0
    assert "Beta sentence." in result_text
    assert "M1" in result_text
    assert "Updated middle" in irnode_to_text(result)


def test_merge_subsection_with_omission_fails_closed_on_duplicate_paragraph_labels() -> None:
    master_sub = _sub(
        "1",
        _para("1", "Master p1"),
        _para("2", "Master p2a"),
        _para("2", "Master p2b"),
        _para("3", "Master p3"),
    )
    amend_sub = _sub(
        "1",
        _para("1", "Amended p1"),
        _omission(),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is None


def test_merge_section_deduplicates_when_master_has_duplicate_labels() -> None:
    """Equal-state duplicate subsection ownership must fail closed.

    Scenario: master has a duplicate label '2' (source pathology), amendment
    inserts a new subsection after position 2. The merge expands the omission
    to include master subs [1, 2, 2_dup], then appends sub3_new. Because both
    duplicate '2' slots are untouched, omission merge must not silently choose
    one by position.
    """
    master_sec = _sec(
        "4",
        _sub("1", _para("1", "sub1 text")),
        _sub("2", _para("1", "sub2 original")),
        _sub("2", _para("1", "sub2 duplicate")),  # duplicate label — source pathology
        _sub("3", _para("1", "sub3 text")),
    )
    # Amendment: [omission, sub3_new(3)] — inserts new sub at position 3
    # T=2 < M=4 → T<M branch: omission expands to master[0:3]=[sub1,sub2,sub2_dup]
    # then appends sub3_new → before dedup: [sub1, sub2, sub2_dup, sub3_new]
    amend_sec = _sec(
        "4",
        _omission(),
        _sub("3", _para("1", "sub3 new amendment text")),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is None


def test_merge_section_prefers_amended_duplicate_subsection_over_stale_master_duplicate() -> None:
    """Omission-aware section merge must keep the amended payload, not stale residue.

    Regression for the duplicate-subsection family seen in real Finland material
    such as the 2014/917 and 2014/255 replay paths: when the master already has
    a duplicate subsection label, a sparse omission merge must still surface the
    amended replacement for that label instead of retaining the later stale
    master copy.
    """
    master_sec = _sec(
        "4",
        _sub("1", _para("1", "sub1 text")),
        _sub("2", _para("1", "sub2 original")),
        _sub("2", _para("1", "sub2 stale duplicate")),
        _sub("3", _para("1", "sub3 text")),
    )
    amend_sec = _sec(
        "4",
        _sub("1", _para("1", "sub1 text")),
        _omission(),
        _sub("2", _para("1", "sub2 amendment replacement")),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    result_labels = [c.label for c in result_subsecs]
    assert len(result_labels) == len(set(result_labels)), (
        f"Duplicate labels after merge: {result_labels}"
    )
    result_sub2 = next(c for c in result_subsecs if c.label == "2")
    result_text = irnode_to_text(result_sub2)
    assert "sub2 amendment replacement" in result_text
    assert "sub2 stale duplicate" not in result_text


def test_merge_section_targeted_ops_keeps_first_duplicate_when_neither_duplicate_is_replaced() -> None:
    """Targeted merge must not re-own duplicate labels by later position alone."""
    master_sec = _sec(
        "15",
        _sub("1", _content("old 1")),
        _sub("2", _content("first duplicate")),
        _sub("2", _content("later duplicate")),
        _sub("3", _content("old 3")),
    )
    amend_sec = _sec(
        "15",
        _sub("1", _content("new 1")),
        _omission(),
    )

    result = _merge_section_with_targeted_ops_ir(
        master_sec,
        amend_sec,
        group_ops=[
            _op("REPLACE", target_section="15"),
            _op("REPLACE", target_section="15", target_paragraph=1),
        ],
    )

    assert result is not None
    result_subsecs = [c for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    result_sub2 = next(c for c in result_subsecs if c.label == "2")

    assert [c.label for c in result_subsecs].count("2") == 1
    assert irnode_to_text(result_sub2).strip() == "first duplicate"


def test_merge_section_keeps_carried_tail_when_text_only_prefix_matches() -> None:
    """Carried tail subsection must survive when only a prefix matches.

    Regression for the section-tail overmatch class: a carried subsection should
    only be dropped when it is an exact structural duplicate of the absorbed
    tail, not when it merely shares a leading sentence fragment.
    """
    tail_text = "Tail sentence. More context."
    carried_text = "Tail sentence."
    tail_sub = IRNode(
        kind=IRNodeKind.SUBPARAGRAPH,
        label="1",
        children=(_content(tail_text),),
    )
    master_sec = _sec(
        "6",
        _sub(
            "1",
            _para("1", "master 1"),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="seven old"),
                    tail_sub,
                ),
            ),
        ),
        _sub("2", _content(carried_text)),
        _sub("3", _content("m3")),
    )
    amend_sec = _sec(
        "6",
        _sub(
            "1",
            _para("1", "master 1 new"),
            IRNode(
                kind=IRNodeKind.PARAGRAPH,
                label="7",
                children=(
                    IRNode(kind=IRNodeKind.CONTENT, text="seven old"),
                    tail_sub,
                ),
            ),
        ),
        _omission(),
    )

    result = _merge_section_with_omission_ir(master_sec, amend_sec)
    assert result is not None

    result_labels = [c.label for c in result.children if c.kind is IRNodeKind.SUBSECTION]
    assert result_labels == ["1", "2", "3"]


def test_merge_same_numbered_container_insert_fails_closed_on_duplicate_section_labels() -> None:
    """Container insert must not silently prune duplicate section ownership by position."""
    master = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        children=(
            _sec("1", _sub("1", _content("section 1"))),
            _sec("2", _sub("1", _content("section 2a"))),
            _sec("2", _sub("1", _content("section 2b"))),
        ),
    )
    amend = IRNode(
        kind=IRNodeKind.CHAPTER,
        label="1",
        children=(
            _sec("3", _sub("1", _content("section 3"))),
        ),
    )

    result = _merge_same_numbered_container_insert_ir(master, amend)

    assert result is None


# ---------------------------------------------------------------------------
# _paragraph_signatures_ir
# ---------------------------------------------------------------------------


def test_paragraph_signatures_ir_returns_empty_for_multi_subsection() -> None:
    sec = _sec("3",
        _sub("1", _para("1", "p1")),
        _sub("2", _para("1", "p2")),
    )
    assert _paragraph_signatures_ir(sec) == []


def test_paragraph_signatures_ir_returns_empty_for_no_subsections() -> None:
    sec = _sec("3")
    assert _paragraph_signatures_ir(sec) == []


def test_paragraph_signatures_ir_returns_paragraph_texts_for_single_sub() -> None:
    def _para_with_content(label: str, text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=(_content(text),))

    sec = _sec("1",
        _sub("1",
            _para_with_content("1", "Alpha text"),
            _para_with_content("2", "Beta text"),
        )
    )
    sigs = _paragraph_signatures_ir(sec)
    assert len(sigs) == 2
    assert any("Alpha" in s for s in sigs)
    assert any("Beta" in s for s in sigs)


def test_paragraph_signatures_ir_normalizes_whitespace() -> None:
    def _para_with_content(label: str, text: str) -> IRNode:
        return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=(_content(text),))

    sec = _sec("1",
        _sub("1",
            _para_with_content("1", "  lots   of   space  "),
        )
    )
    sigs = _paragraph_signatures_ir(sec)
    assert sigs[0] == "lots of space"


# ---------------------------------------------------------------------------
# Omission merge invariants (per PRO_RESPONSE4_1.md Query 2)
#
# 1. No explicit target is lost.
# 2. No preserved untouched slot is duplicated.
# 3. No untouched slot outside explicit ownership is silently dropped.
# 4. Relative order inside preserved segments is stable.
# 5. Label/slot identity remains coherent.
# 6. Tombstone/scaffold occupancy survives in execution state.
# 7. If interval boundaries are ambiguous, emit a degraded finding
#    instead of guessing.
# ---------------------------------------------------------------------------

def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


class TestOmissionMergeInvariants:
    """Structural invariant tests for _merge_subsection_with_omission_ir."""

    def test_invariant1_explicit_targets_not_lost(self):
        """Amendment-owned units must appear in output."""
        master = _sub("1", _para("1", "old A"), _para("2", "old B"), _para("3", "old C"))
        amend = _sub("1", _para("1", "new A"), _para("2", "new B"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        texts = [c.text for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        assert "new A" in texts
        assert "new B" in texts

    def test_invariant2_no_duplication(self):
        """Preserved master slots must not duplicate explicit amendment units."""
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        amend = _sub("1", _para("1", "A-new"), _para("2", "B-new"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        assert len(labels) == len(set(labels)), f"Duplicate labels: {labels}"

    def test_invariant3_untouched_not_dropped(self):
        """Master slots outside amendment ownership must survive in omission intervals."""
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"), _para("4", "D"))
        amend = _sub("1", _para("1", "A-new"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        assert "2" in labels, "Master item 2 should be preserved"
        assert "3" in labels, "Master item 3 should be preserved"
        assert "4" in labels, "Master item 4 should be preserved"

    def test_invariant4_order_preserved(self):
        """Relative order of preserved master slots must be stable."""
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"), _para("4", "D"))
        amend = _sub("1", _para("1", "A-new"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        # Amendment item 1 comes first, then preserved 2, 3, 4 in order
        preserved = [l for l in labels if l != "1"]
        assert preserved == sorted(preserved, key=lambda x: int(x)), f"Out of order: {preserved}"

    def test_invariant5_labels_coherent(self):
        """Labels in output must match their structural position."""
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        amend = _sub("1", _para("2", "B-new"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        # All labels must be non-empty
        assert all(labels)

    def test_invariant6_tombstone_survives(self):
        """Repealed placeholder slots in master must survive omission merge."""
        tombstone = IRNode(kind=IRNodeKind.PARAGRAPH, label="2", attrs={"lawvm_repeal_placeholder": "1"})
        master = _sub("1", _para("1", "A"), tombstone, _para("3", "C"))
        amend = _sub("1", _para("1", "A-new"), _omission())
        result = _merge_subsection_with_omission_ir(master, amend)
        assert result is not None
        labels = [c.label for c in result.children if c.kind is IRNodeKind.PARAGRAPH]
        assert "2" in labels, "Tombstone slot must survive"
        p2 = next(c for c in result.children if c.kind is IRNodeKind.PARAGRAPH and c.label == "2")
        assert p2.attrs.get("lawvm_repeal_placeholder") == "1"


# ---------------------------------------------------------------------------
# Typed merge operator — ReplaceMode, MergeEvent, invariant checks
# ---------------------------------------------------------------------------

from lawvm.finland.merge import (
    ReplaceMode,
    MergeEvent,
    MergeInvariantViolation,
    MergeResult,
    validate_merge_invariants,
    build_merge_event,
    merge_section_with_invariants,
    merge_subsection_with_invariants,
    merge_container_with_invariants,
    merge_sparse_section_with_invariants,
    _tree_has_omission,
    _collect_labels,
)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


class TestReplaceMode:
    """ReplaceMode enum values and membership."""

    def test_replace_mode_values(self):
        assert ReplaceMode.EXACT_REPLACE == "exact_replace"
        assert ReplaceMode.OMISSION_MERGE == "omission_merge"
        assert ReplaceMode.SPARSE_MERGE == "sparse_merge"
        assert ReplaceMode.PLACEHOLDER_REPLACE == "placeholder_replace"

    def test_replace_mode_is_string(self):
        for mode in ReplaceMode:
            assert isinstance(mode, str)


class TestMergeEventBasics:
    """MergeEvent construction and properties."""

    def test_empty_event_has_no_violations(self):
        event = MergeEvent(replace_mode=ReplaceMode.OMISSION_MERGE)
        assert not event.has_violations
        assert event.hard_violations == ()

    def test_event_with_violations(self):
        v = MergeInvariantViolation(
            code="TEST", severity="hard", message="test violation"
        )
        event = MergeEvent(
            replace_mode=ReplaceMode.OMISSION_MERGE,
            violations=(v,),
        )
        assert event.has_violations
        assert len(event.hard_violations) == 1

    def test_event_warning_not_hard(self):
        v = MergeInvariantViolation(
            code="TEST", severity="warning", message="test warning"
        )
        event = MergeEvent(
            replace_mode=ReplaceMode.OMISSION_MERGE,
            violations=(v,),
        )
        assert event.has_violations
        assert len(event.hard_violations) == 0


class TestTreeHasOmission:
    """_tree_has_omission recursive checker."""

    def test_direct_omission(self):
        assert _tree_has_omission(_omission()) is True

    def test_nested_omission(self):
        node = _sub("1", _para("1", "x"), _omission())
        assert _tree_has_omission(node) is True

    def test_deeply_nested_omission(self):
        inner = _sub("1", _omission())
        outer = _sec("1", inner)
        assert _tree_has_omission(outer) is True

    def test_no_omission(self):
        node = _sub("1", _para("1", "x"), _para("2", "y"))
        assert _tree_has_omission(node) is False


class TestCollectLabels:
    """_collect_labels returns direct child labels."""

    def test_collects_paragraph_labels(self):
        node = _sub("1", _para("1", "A"), _para("2", "B"))
        assert _collect_labels(node) == ["1", "2"]

    def test_skips_empty_labels(self):
        node = _sub("1", _para("1", "A"), _content("text"))
        labels = _collect_labels(node)
        assert labels == ["1"]

    def test_empty_children(self):
        node = _sec("1")
        assert _collect_labels(node) == []


class TestValidateMergeInvariants:
    """Post-merge invariant checking."""

    def test_no_violations_on_clean_merge(self):
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        payload = _sub("1", _para("1", "A-new"), _omission())
        # Simulate a clean merge result (no omission markers, all payload labels present)
        result = _sub("1", _para("1", "A-new"), _para("2", "B"), _para("3", "C"))
        violations = validate_merge_invariants(
            result, master, payload, ReplaceMode.OMISSION_MERGE
        )
        assert len(violations) == 0

    def test_surviving_omission_is_hard_violation(self):
        """Invariant 1: no omission markers survive in output."""
        master = _sub("1", _para("1", "A"), _para("2", "B"))
        payload = _sub("1", _para("1", "A-new"), _omission())
        # Bad result: omission marker survived
        result = _sub("1", _para("1", "A-new"), _omission(), _para("2", "B"))
        violations = validate_merge_invariants(
            result, master, payload, ReplaceMode.OMISSION_MERGE
        )
        assert len(violations) >= 1
        assert any(v.code == "OMISSION_SURVIVES_MERGE" for v in violations)
        assert all(v.severity == "hard" for v in violations if "OMISSION" in v.code)

    def test_omission_in_non_merge_mode_is_hard_violation(self):
        """Invariant 2: omission in non-merge mode is hard failure."""
        master = _sub("1", _para("1", "A"))
        payload = _sub("1", _para("1", "A-new"))
        # Bad result: omission marker in exact_replace mode
        result = _sub("1", _para("1", "A-new"), _omission())
        violations = validate_merge_invariants(
            result, master, payload, ReplaceMode.EXACT_REPLACE
        )
        assert len(violations) >= 1
        assert any(v.code == "OMISSION_SURVIVES_NON_MERGE" for v in violations)

    def test_missing_payload_descendants_is_hard_violation(self):
        """Invariant 3: all payload descendants must appear in result."""
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        payload = _sub("1", _para("1", "A-new"), _para("2", "B-new"), _omission())
        # Bad result: payload para "2" missing
        result = _sub("1", _para("1", "A-new"), _para("3", "C"))
        violations = validate_merge_invariants(
            result, master, payload, ReplaceMode.OMISSION_MERGE
        )
        assert len(violations) >= 1
        assert any(v.code == "PAYLOAD_DESCENDANTS_MISSING" for v in violations)
        missing_v = next(v for v in violations if v.code == "PAYLOAD_DESCENDANTS_MISSING")
        missing_labels = missing_v.detail["missing_labels"]
        assert isinstance(missing_labels, list)
        assert "2" in missing_labels

    def test_no_missing_payload_check_for_exact_replace(self):
        """Invariant 3 is only checked for merge modes, not exact replace."""
        master = _sub("1", _para("1", "A"), _para("2", "B"))
        payload = _sub("1", _para("1", "A-new"), _para("2", "B-new"))
        # Result drops para 2 but mode is EXACT — no payload-missing check
        result = _sub("1", _para("1", "A-new"))
        violations = validate_merge_invariants(
            result, master, payload, ReplaceMode.EXACT_REPLACE
        )
        # No PAYLOAD_DESCENDANTS_MISSING (exact mode doesn't check for preserved labels)
        assert not any(v.code == "PAYLOAD_DESCENDANTS_MISSING" for v in violations)


class TestBuildMergeEvent:
    """build_merge_event correctly classifies labels and runs invariant checks."""

    def test_labels_classified_correctly(self):
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        payload = _sub("1", _para("1", "A-new"), _omission())
        result = _sub("1", _para("1", "A-new"), _para("2", "B"), _para("3", "C"))
        event = build_merge_event(
            result, master, payload, ReplaceMode.OMISSION_MERGE,
            omission_slots_expanded=1,
        )
        assert "1" in event.payload_labels
        assert "2" in event.preserved_labels
        assert "3" in event.preserved_labels
        assert event.omission_slots_expanded == 1
        assert not event.has_violations

    def test_trailing_dedup_flag(self):
        master = _sub("1", _para("1", "A"), _para("2", "B"))
        payload = _sub("1", _para("1", "A-new"), _omission())
        result = _sub("1", _para("1", "A-new"), _para("2", "B"))
        event = build_merge_event(
            result, master, payload, ReplaceMode.OMISSION_MERGE,
            trailing_omission_dedup_fired=True,
        )
        assert event.trailing_omission_dedup_fired is True


class TestMergeSubsectionWithInvariants:
    """merge_subsection_with_invariants wraps correctly."""

    def test_returns_none_for_no_omission(self):
        master = _sub("1", _para("1", "A"))
        amend = _sub("1", _para("1", "A-new"))
        result = merge_subsection_with_invariants(master, amend)
        assert result is None

    def test_returns_merge_result_with_event(self):
        master = _sub("1", _para("1", "A"), _para("2", "B"), _para("3", "C"))
        amend = _sub("1", _para("1", "A-new"), _omission())
        mr = merge_subsection_with_invariants(master, amend, source_statute="2024/123")
        assert mr is not None
        assert isinstance(mr, MergeResult)
        assert isinstance(mr.event, MergeEvent)
        assert mr.event.replace_mode == ReplaceMode.OMISSION_MERGE
        # Payload label 1 should be tracked
        assert "1" in mr.event.payload_labels
        # Preserved labels 2, 3 should be tracked
        assert "2" in mr.event.preserved_labels
        assert "3" in mr.event.preserved_labels
        assert not mr.event.has_violations

    def test_detects_trailing_dedup(self):
        """When amendment's last item matches master splice point (renumber case)."""
        shared_text = "sama teksti"
        master = _sub("1",
            _para("1", "A"),
            _para("2", shared_text),
        )
        amend = _sub("1",
            _para("1", shared_text),
            _omission(),
        )
        mr = merge_subsection_with_invariants(master, amend)
        assert mr is not None
        assert mr.event.trailing_omission_dedup_fired is True


class TestMergeSectionWithInvariants:
    """merge_section_with_invariants wraps correctly."""

    def test_returns_none_for_no_omission(self):
        master = _sec("3", _sub("1", _para("1", "M1")))
        amend = _sec("3", _sub("1", _para("1", "A1")))
        result = merge_section_with_invariants(master, amend)
        assert result is None

    def test_returns_merge_result(self):
        master = _sec("3",
            _sub("1", _para("1", "M1")),
            _sub("2", _para("1", "M2")),
            _sub("3", _para("1", "M3")),
        )
        amend = _sec("3",
            _sub("1", _para("1", "A1")),
            _omission(),
        )
        mr = merge_section_with_invariants(master, amend, source_statute="2024/100")
        assert mr is not None
        assert mr.event.replace_mode == ReplaceMode.OMISSION_MERGE
        assert mr.event.omission_slots_expanded >= 1
        assert not mr.event.has_violations

    def test_section_merge_tracks_labels(self):
        master = _sec("5",
            _sub("1", _para("1", "M1")),
            _sub("2", _para("1", "M2")),
        )
        amend = _sec("5",
            _omission(),
            _sub("2", _para("1", "A2")),
        )
        mr = merge_section_with_invariants(master, amend)
        assert mr is not None
        # Sub "1" preserved from master, sub "2" from payload
        assert "1" in mr.event.preserved_labels
        assert "2" in mr.event.payload_labels


class TestMergeContainerWithInvariants:
    """merge_container_with_invariants wraps correctly."""

    def test_container_merge_returns_result(self):
        master = IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(
            IRNode(kind=IRNodeKind.HEADING, text="Master heading"),
            _sec("1", _sub("1", _para("1", "M1"))),
            _sec("2", _sub("1", _para("1", "M2"))),
        ))
        amend = IRNode(kind=IRNodeKind.CHAPTER, label="1", children=(
            IRNode(kind=IRNodeKind.HEADING, text="New heading"),
            _sec("1", _sub("1", _para("1", "A1"))),
        ))
        mr = merge_container_with_invariants(master, amend, source_statute="2024/200")
        assert isinstance(mr, MergeResult)
        assert mr.event.replace_mode == ReplaceMode.SPARSE_MERGE
        # Section "2" preserved from master, "1" from payload
        assert "2" in mr.event.preserved_labels
        assert "1" in mr.event.payload_labels


class TestMergeResultReplace:
    """replace_mode classification and propagation."""

    def test_replace_mode_propagated_in_event(self):
        event = MergeEvent(
            replace_mode=ReplaceMode.SPARSE_MERGE,
            preserved_labels=("2", "3"),
            payload_labels=("1",),
        )
        assert event.replace_mode == ReplaceMode.SPARSE_MERGE

    def test_merge_result_node_is_accessible(self):
        master = _sub("1", _para("1", "A"), _para("2", "B"))
        amend = _sub("1", _para("1", "A-new"), _omission())
        mr = merge_subsection_with_invariants(master, amend)
        assert mr is not None
        # The .node field is the actual IRNode result
        assert mr.node.kind is IRNodeKind.SUBSECTION
        labels = [c.label for c in mr.node.children if c.kind is IRNodeKind.PARAGRAPH]
        assert "1" in labels
        assert "2" in labels


class TestMergeSparseWithInvariants:
    """merge_sparse_section_with_invariants wraps correctly."""

    def test_returns_none_when_not_sparse_shape(self):
        """Non-sparse payload returns None."""
        master = _sec("1", _sub("1", _para("1", "A"), _para("2", "B")))
        amend = _sec("1", _sub("1", _para("1", "A-new")))
        result = merge_sparse_section_with_invariants(master, amend)
        assert result is None

    def test_sparse_merge_returns_result(self):
        """Sparse section merge with enough items returns MergeResult."""
        # Master: single subsection with intro + 5 numbered paragraphs
        master = _sec("1", _sub("1",
            _intro("Seuraavat kohdat:"),
            _para("1", "item 1"),
            _para("2", "item 2"),
            _para("3", "item 3"),
            _para("4", "item 4"),
            _para("5", "item 5"),
        ))
        # Amendment: same intro + only items 3 and 5 changed (non-contiguous)
        amend = _sec("1", _sub("1",
            _intro("Seuraavat kohdat:"),
            _para("3", "item 3-new"),
            _para("5", "item 5-new"),
        ))
        mr = merge_sparse_section_with_invariants(master, amend, source_statute="2024/300")
        assert mr is not None
        assert mr.event.replace_mode == ReplaceMode.SPARSE_MERGE
        assert not mr.event.has_violations


# ---------------------------------------------------------------------------
# Omission-aware payload claims: CONTEXT_CARRIED pre-omission intro nodes
# (PRO_RESPONSE_5_1 §5 / bug: 1974/16 amended by 1977/18)
# ---------------------------------------------------------------------------


def test_merge_subsection_context_carried_intro_uses_master_content() -> None:
    """Regression: 1974/16 amended by 1977/18.

    Amendment §2 has 1 momentti containing:
    - intro ("kaksi hehtaaria" — johdantokappale context, unchanged per drafting guide)
    - omission marker (items 1 are omitted — context, not changed)
    - paragraph[2], paragraph[3], paragraph[4] (items 2-4 — actual new law)

    The clause only targets items 2-4.  Item 1 (the intro) is CONTEXT_CARRIED
    and must NOT overwrite the master's item 1 ("yhden hehtaarin" from 1976/356).

    Expected: master's intro ("yhden hehtaarin") is preserved; items 2-4 from
    amendment are applied; no "kaksi hehtaaria" in the result.
    """
    # Master (prior law from 1976/356): subsection with intro + items 1-4
    master_sub = _sub(
        "1",
        IRNode(kind=IRNodeKind.INTRO, text="yhden hehtaarin"),
        _para("1", "ensimmäinen kohta"),
        _para("2", "toinen kohta vanha"),
        _para("3", "kolmas kohta vanha"),
        _para("4", "neljäs kohta vanha"),
    )
    # Amendment 1977/18: intro (context, unchanged) + omission + new items 2-4
    amend_sub = _sub(
        "1",
        IRNode(kind=IRNodeKind.INTRO, text="kaksi hehtaaria"),  # context — must NOT overwrite
        _omission(),
        _para("2", "toinen kohta uusi"),
        _para("3", "kolmas kohta uusi"),
        _para("4", "neljäs kohta uusi"),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None

    # The context text "kaksi hehtaaria" must NOT appear in the result
    result_text = " ".join(
        (c.text or "") for c in result.children
    )
    assert "kaksi hehtaaria" not in result_text, (
        "Amendment context text must not overwrite master intro"
    )

    # Master intro "yhden hehtaarin" must be preserved
    assert "yhden hehtaarin" in result_text, (
        "Master intro must be preserved (CONTEXT_CARRIED rule)"
    )

    # Items 2-4 from amendment must be applied
    paras = {c.label: c for c in result.children if c.kind is IRNodeKind.PARAGRAPH}
    assert paras.get("2") is not None and "uusi" in (paras["2"].text or "")
    assert paras.get("3") is not None and "uusi" in (paras["3"].text or "")
    assert paras.get("4") is not None and "uusi" in (paras["4"].text or "")

    # Item 1 from master must be preserved
    assert paras.get("1") is not None, "Item 1 from master must survive"
    assert "ensimmäinen kohta" in (paras["1"].text or ""), (
        "Master item 1 must be preserved unchanged"
    )


def test_merge_subsection_labeled_pre_omission_still_candidate_payload() -> None:
    """When pre-omission children are labeled paragraphs, treat as CANDIDATE_PAYLOAD.

    This is the original behaviour: labeled paragraph before omission IS new law.
    For example: amendment replaces item 1, omission preserves items 2+.
    This must not regress.
    """
    master_sub = _sub(
        "1",
        _para("1", "item 1 old"),
        _para("2", "item 2 old"),
        _para("3", "item 3 old"),
    )
    amend_sub = _sub(
        "1",
        _para("1", "item 1 new"),  # LABELED — candidate payload, not context
        _omission(),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    paras = {c.label: c for c in result.children if c.kind is IRNodeKind.PARAGRAPH}
    # Item 1 from amendment (new law) must apply
    assert "new" in (paras.get("1", IRNode(kind=IRNodeKind.PARAGRAPH)).text or "")
    # Items 2, 3 from master must be preserved
    assert paras.get("2") is not None
    assert paras.get("3") is not None


def test_merge_subsection_context_carried_content_node() -> None:
    """Content node (not intro) before omission is also CONTEXT_CARRIED.

    Some subsections use a <content> node as their johdantokappale.
    Same rule applies: if it's unlabeled content before an omission,
    use master content.
    """
    master_sub = _sub(
        "1",
        _content("Master content intro"),
        _para("1", "item 1 old"),
        _para("2", "item 2 old"),
    )
    amend_sub = _sub(
        "1",
        _content("Amendment context intro"),  # context — must NOT overwrite
        _omission(),
        _para("2", "item 2 new"),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    assert result is not None
    result_text = " ".join((c.text or "") for c in result.children)
    assert "Amendment context intro" not in result_text, (
        "Unlabeled content node before omission must not overwrite master"
    )
    assert "Master content intro" in result_text, (
        "Master content intro must be preserved"
    )


def test_merge_subsection_without_omission_unchanged_behavior() -> None:
    """Amendments WITHOUT omission markers still return None (full replacement path)."""
    master_sub = _sub(
        "1",
        _para("1", "item 1 old"),
        _para("2", "item 2 old"),
    )
    amend_sub = _sub(
        "1",
        _para("1", "item 1 new"),
        _para("2", "item 2 new"),
    )

    result = _merge_subsection_with_omission_ir(master_sub, amend_sub)

    # No omission → function returns None (caller does exact replace)
    assert result is None


# ---------------------------------------------------------------------------
# _heading_intro_replace_preserve_items_ir
# ---------------------------------------------------------------------------


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def test_heading_intro_replace_preserves_master_paragraphs() -> None:
    """Amendment with intro-only subsection (no paragraphs) preserves master items.

    Pattern: amendment changes the introductory sentence of a list section
    but omits the paragraph items (unchanged). LawVM must preserve the items
    from the master while replacing the intro.
    """
    master_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("Hakemukseen on liitettävä seuraavat asiakirjat:"),
            _para("1", "passijäljennös"),
            _para("2", "tuloilmoitus"),
            _para("3", "asuinpaikkaselvitys"),
        ),
    )
    amend_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("Hakemukseen on liitettävä seuraavat todistukset:"),
            # No paragraph items — amendment omits them (unchanged)
        ),
    )

    result = _heading_intro_replace_preserve_items_ir(master_sec, amend_sec)

    assert result is not None, "Should match the intro-only amendment pattern"

    result_sub = next(c for c in result.children if c.kind is IRNodeKind.SUBSECTION)
    children_by_kind = {c.kind: c for c in result_sub.children}

    # Amended intro must be applied
    intro = children_by_kind.get(IRNodeKind.INTRO)
    assert intro is not None
    assert "todistukset" in (intro.text or ""), "Amended intro text not applied"
    assert "asiakirjat" not in (intro.text or ""), "Old intro text must not appear"

    # Master paragraphs must be preserved
    paras = [c for c in result_sub.children if c.kind is IRNodeKind.PARAGRAPH]
    assert len(paras) == 3, f"Expected 3 paragraphs from master, got {len(paras)}"
    labels = {p.label for p in paras}
    assert labels == {"1", "2", "3"}, f"Paragraph labels mismatch: {labels}"


def test_heading_intro_replace_returns_none_when_amend_has_paragraphs() -> None:
    """When the amendment DOES have paragraph items, do NOT use the intro-preserve path.

    The amendment is replacing the items too — let normal replacement handle it.
    """
    master_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("johtolause vanha"),
            _para("1", "vanha kohta 1"),
            _para("2", "vanha kohta 2"),
        ),
    )
    amend_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("johtolause uusi"),
            _para("1", "uusi kohta 1"),  # amendment HAS items — normal replace
        ),
    )

    result = _heading_intro_replace_preserve_items_ir(master_sec, amend_sec)

    assert result is None, "Should NOT match when amendment has paragraph items"


def test_heading_intro_replace_returns_none_when_master_has_few_paragraphs() -> None:
    """Guard: master section must have ≥2 paragraphs for the pattern to apply."""
    master_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("johtolause"),
            _para("1", "ainoa kohta"),
        ),
    )
    amend_sec = _sec(
        "3",
        _sub(
            "1",
            _intro("johtolause uusi"),
            # No paragraphs
        ),
    )

    result = _heading_intro_replace_preserve_items_ir(master_sec, amend_sec)

    assert result is None, "Should not match when master has fewer than 2 paragraphs"


def test_heading_intro_replace_returns_none_when_intro_unchanged() -> None:
    """Guard: if intro text is already the same, skip — let normal path handle."""
    same_intro = "Hakemukseen on liitettävä:"
    master_sec = _sec(
        "3",
        _sub(
            "1",
            _intro(same_intro),
            _para("1", "kohta 1"),
            _para("2", "kohta 2"),
        ),
    )
    amend_sec = _sec(
        "3",
        _sub(
            "1",
            _intro(same_intro),  # same — no change
        ),
    )

    result = _heading_intro_replace_preserve_items_ir(master_sec, amend_sec)

    assert result is None, "Should not match when intro text is already identical"
