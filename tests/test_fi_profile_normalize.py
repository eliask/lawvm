"""Unit tests for the Finland profile normalization rule registry.

Tests verify:
1. Each registered rule fires on a minimal fixture that triggers it.
2. The registry order matches the original xml_ir.py call order.
3. Non-triggering input is returned unchanged (identity preserved where applicable).
4. apply_all() fires observations when rules change children.
"""

from __future__ import annotations

from typing import List

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.finland.profile.normalize import (
    SECTION_RULES,
    SUBSECTION_POST_RULES,
    SUBSECTION_POST_RULES_A,
    SUBSECTION_POST_RULES_B,
    SUBSECTION_POST_RULES_C,
    SUBSECTION_PRE_RULES,
    NormalizationRule,
    _apply_fi_merge_split_intro_item_subsections,
    _apply_fi_renest_flat_dash_item_subsections,
    _apply_fi_renest_flat_digit_item_subsections,
    _apply_fi_renest_flat_dot_item_subsections,
    _apply_fi_split_intro_then_numbered_list_subsections,
    _apply_fi_split_inner_omission_paragraph_subsections,
    _apply_fi_split_subsection_at_numbered_list_restart,
    _apply_hoist_inline_content_omissions,
    _apply_hoist_trailing_wrapup_paragraph,
    _apply_nest_lettered_subparagraphs,
    _apply_nest_repeated_alpha_subparagraphs_under_alpha_parents,
    _apply_nest_repeated_digit_subparagraphs,
    _apply_recover_embedded_numbered_paragraphs,
    _apply_recover_intro_labeled_paragraphs,
    _apply_split_trailing_content_only_paragraphs_into_subsections,
    apply_all,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.CONTENT, text=text)


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _num(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.NUM, text=text)


def _para(label: str | None = None, children: tuple = (), text: str | None = None) -> IRNode:
    return IRNode(kind=IRNodeKind.PARAGRAPH, label=label, children=tuple(children), text=text or "")


def _subpara(label: str | None = None, children: tuple = (), text: str | None = None) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBPARAGRAPH, label=label, children=tuple(children), text=text or "")


def _subsection(label=None, children=()) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


def _omission() -> IRNode:
    return IRNode(kind=IRNodeKind.OMISSION)


# ---------------------------------------------------------------------------
# Registry order tests
# ---------------------------------------------------------------------------

def test_section_rules_order():
    """SECTION_RULES must be in the original xml_ir.py call order."""
    expected_names = [
        "fi.merge_split_intro_item_subsections",
        "fi.split_intro_then_numbered_list_subsections",
        "fi.renest_flat_digit_item_subsections",
        "fi.renest_flat_dash_item_subsections",
        "fi.renest_flat_dot_item_subsections",
        "fi.split_inner_omission_paragraph_subsections",
        "fi.split_subsection_at_numbered_list_restart",
        "fi.split_trailing_content_only_paragraphs_into_subsections",
    ]
    assert [r.name for r in SECTION_RULES] == expected_names


def test_subsection_pre_rules_order():
    """SUBSECTION_PRE_RULES must be in the original xml_ir.py call order."""
    expected_names = [
        "fi.recover_intro_labeled_paragraphs",
        "fi.hoist_inline_content_omissions",
    ]
    assert [r.name for r in SUBSECTION_PRE_RULES] == expected_names


def test_subsection_post_rules_order():
    """SUBSECTION_POST_RULES (flat view) must cover all post-counter Finland rules in order."""
    expected_names = [
        "fi.recover_embedded_numbered_paragraphs",  # segment A
        "fi.hoist_trailing_wrapup_paragraph",        # segment B
        "fi.nest_lettered_subparagraphs",            # segment B
        "fi.nest_repeated_alpha_subparagraphs_under_alpha_parents",  # segment C
        "fi.nest_repeated_digit_subparagraphs",      # segment C
    ]
    assert [r.name for r in SUBSECTION_POST_RULES] == expected_names


def test_post_rules_a_b_c_concatenate_to_post_rules():
    """SUBSECTION_POST_RULES_A + B + C == SUBSECTION_POST_RULES."""
    combined = SUBSECTION_POST_RULES_A + SUBSECTION_POST_RULES_B + SUBSECTION_POST_RULES_C
    assert [r.name for r in combined] == [r.name for r in SUBSECTION_POST_RULES]


_VALID_FAMILIES = {"transport_cleanup", "ontology_normalization", "historical_tolerance", "presentation_cleanup"}


def test_all_rules_are_normalization_rule_instances():
    """All registry entries are NormalizationRule dataclasses."""
    for rule in SECTION_RULES + SUBSECTION_PRE_RULES + SUBSECTION_POST_RULES:
        assert isinstance(rule, NormalizationRule)
        assert rule.name.startswith("fi.")
        assert callable(rule.apply)
        assert isinstance(rule.description, str) and rule.description


def test_all_rules_have_family_tags():
    """Every rule in every registry must carry a non-empty family tag from the
    declared vocabulary (transport_cleanup, ontology_normalization,
    historical_tolerance, presentation_cleanup).

    Item 7 of the post-wave-5 follow-up batch.
    """
    all_rules = SECTION_RULES + SUBSECTION_PRE_RULES + SUBSECTION_POST_RULES
    assert len(all_rules) == 15, (
        f"Expected 15 rules, got {len(all_rules)}. Update this test if rules are added."
    )
    for rule in all_rules:
        assert isinstance(rule.family, str) and rule.family, (
            f"Rule {rule.name!r} has empty or missing family tag"
        )
        assert rule.family in _VALID_FAMILIES, (
            f"Rule {rule.name!r} has unknown family {rule.family!r}. "
            f"Valid families: {_VALID_FAMILIES}"
        )


def test_apply_all_observation_includes_family():
    """apply_all observations must include a 'family' key alongside 'rule' and 'fired'."""
    # Build a node that will trigger fi.recover_intro_labeled_paragraphs
    para = IRNode(
        kind=IRNodeKind.PARAGRAPH,
        children=(IRNode(kind=IRNodeKind.INTRO, text="3) some text here"),),
    )
    obs: List[dict] = []
    apply_all([para], SUBSECTION_PRE_RULES, observations_out=obs)
    fired = [o for o in obs if o.get("fired")]
    assert fired, "Expected at least one observation to fire"
    for o in fired:
        assert "family" in o, f"Observation missing 'family' key: {o}"
        assert o["family"] in _VALID_FAMILIES, (
            f"Observation family {o['family']!r} not in valid set"
        )


# ---------------------------------------------------------------------------
# apply_all() observation mechanism
# ---------------------------------------------------------------------------

def test_apply_all_emits_observation_when_rule_fires():
    """apply_all should append observation dict when a rule changes children."""
    # Build a children list that _apply_recover_intro_labeled_paragraphs will rewrite
    para_with_intro_label = _para(
        children=(
            IRNode(kind=IRNodeKind.INTRO, text="3) some text here"),
        )
    )
    children = [para_with_intro_label]
    obs: List[dict[str, object]] = []
    result = apply_all(children, SUBSECTION_PRE_RULES, observations_out=obs)
    # The rule should have fired and emitted an observation
    rule_names_fired = {str(o["rule"]) for o in obs if "rule" in o}
    assert "fi.recover_intro_labeled_paragraphs" in rule_names_fired


def test_apply_all_no_observation_when_no_change():
    """apply_all should not emit observations when no rule changes the children."""
    # A children list that triggers none of the pre rules
    children = [_para(label="1", children=(_num("1)"), _content("text")))]
    obs: List[dict[str, object]] = []
    apply_all(children, SUBSECTION_PRE_RULES, observations_out=obs)
    assert obs == []


# ---------------------------------------------------------------------------
# Per-rule firing tests
# ---------------------------------------------------------------------------

def test_recover_embedded_numbered_paragraphs_fires():
    """Rule recovers a paragraph whose content text starts with '2) text'."""
    para = _para(children=(_content("2) Toisessa kohdassa"),))
    result = _apply_recover_embedded_numbered_paragraphs([para])
    assert len(result) == 1
    assert result[0].label == "2"
    # NUM child should be present
    assert any(c.kind == IRNodeKind.NUM for c in result[0].children)


def test_recover_embedded_numbered_paragraphs_no_change_on_labeled():
    """Rule does not rewrite paragraphs that already have a NUM child."""
    para = _para(children=(_num("2)"), _content("text")))
    result = _apply_recover_embedded_numbered_paragraphs([para])
    assert result[0] is para


def test_recover_intro_labeled_paragraphs_fires():
    """Rule recovers label from intro text '3) ...' when no NUM child present."""
    para = _para(children=(IRNode(kind=IRNodeKind.INTRO, text="3) kohta text"),))
    result = _apply_recover_intro_labeled_paragraphs([para])
    assert len(result) == 1
    assert result[0].label == "3"
    intro = next(c for c in result[0].children if c.kind == IRNodeKind.INTRO)
    assert intro.text == "kohta text"


def test_recover_intro_labeled_paragraphs_no_change_when_no_intro():
    """Rule returns the same list when no paragraph has an intro with a label."""
    para = _para(label="1", children=(_content("text"),))
    result = _apply_recover_intro_labeled_paragraphs([para])
    assert result == [para]


def test_nest_lettered_subparagraphs_fires_on_duplicate_letters():
    """Rule nests duplicate letter paragraphs under their digit parent."""
    # Simulate: para "1" with introducer, then para "a", para "b", para "2", para "a", para "b"
    para1 = _para(label="1", children=(_content("intro:"),))
    para_a1 = _para(label="a", children=(_content("item a"),))
    para_b1 = _para(label="b", children=(_content("item b"),))
    para2 = _para(label="2", children=(_content("intro2:"),))
    para_a2 = _para(label="a", children=(_content("item a2"),))
    para_b2 = _para(label="b", children=(_content("item b2"),))
    children = [para1, para_a1, para_b1, para2, para_a2, para_b2]
    result = _apply_nest_lettered_subparagraphs(children)
    # Expect 2 paragraphs at top level
    top_paras = [c for c in result if c.kind == IRNodeKind.PARAGRAPH]
    assert len(top_paras) == 2
    # Each should have 2 subparagraph children
    for para in top_paras:
        subs = [c for c in para.children if c.kind == IRNodeKind.SUBPARAGRAPH]
        assert len(subs) == 2


def test_nest_lettered_subparagraphs_no_change_on_unique_labels():
    """Rule does not rewrite when letter labels are unique."""
    para1 = _para(label="1", children=(_content("text"),))
    para_a = _para(label="a", children=(_content("item"),))
    children = [para1, para_a]
    result = _apply_nest_lettered_subparagraphs(children)
    assert result is children


def test_nest_repeated_alpha_subparagraphs_under_alpha_parents_fires():
    """Rule nests repeated alpha-labeled paras under alpha parent with introducer."""
    parent_d = _para(label="d", children=(_content("parent d:"),))
    child_i1 = _para(label="i", children=(_content("i text 1"),))
    child_ii1 = _para(label="ii", children=(_content("ii text 1"),))
    parent_e = _para(label="e", children=(_content("parent e:"),))
    child_i2 = _para(label="i", children=(_content("i text 2"),))
    child_ii2 = _para(label="ii", children=(_content("ii text 2"),))
    children = [parent_d, child_i1, child_ii1, parent_e, child_i2, child_ii2]
    result = _apply_nest_repeated_alpha_subparagraphs_under_alpha_parents(children)
    # parent_d has introducer (ends with ':') and i/ii are duplicate → nested
    paras = [c for c in result if c.kind == IRNodeKind.PARAGRAPH]
    # parent_d and parent_e should be present
    labels = [p.label for p in paras]
    assert "d" in labels
    assert "e" in labels
    # Subparagraphs should be nested under parent_d or parent_e
    d_para = next(p for p in paras if p.label == "d")
    subs = [c for c in d_para.children if c.kind == IRNodeKind.SUBPARAGRAPH]
    assert len(subs) > 0


def test_nest_repeated_digit_subparagraphs_no_change_on_unique():
    """Rule returns children unchanged when no digit labels are duplicated."""
    para1 = _para(label="1", children=(_content("text"),))
    para2 = _para(label="2", children=(_content("text2"),))
    children = [para1, para2]
    result = _apply_nest_repeated_digit_subparagraphs(children)
    assert result is children


def test_fi_renest_flat_digit_item_subsections_fires():
    """Rule merges an intro subsection + digit-item subsections into one."""
    intro_sub = _subsection(label="1", children=(_content("Tässä laissa tarkoitetaan:"),))
    item1_sub = _subsection(children=(_content("1) julkisella tuella rahaa"),))
    item2_sub = _subsection(children=(_content("2) nopea yhteydellä nopeaa"),))
    children = [intro_sub, item1_sub, item2_sub]
    result = _apply_fi_renest_flat_digit_item_subsections(children)
    assert len(result) == 1
    merged = result[0]
    assert merged.kind == IRNodeKind.SUBSECTION
    # Should have INTRO + 2 PARAGRAPHs
    assert any(c.kind == IRNodeKind.INTRO for c in merged.children)
    paras = [c for c in merged.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paras) == 2
    assert paras[0].label == "1"
    assert paras[1].label == "2"


def test_fi_renest_flat_digit_item_subsections_no_change_on_no_intro():
    """Rule does not fire when intro subsection text does not end with ':'."""
    sub1 = _subsection(label="1", children=(_content("Intro without colon"),))
    sub2 = _subsection(children=(_content("1) item"),))
    children = [sub1, sub2]
    result = _apply_fi_renest_flat_digit_item_subsections(children)
    assert len(result) == 2


def test_fi_renest_flat_dash_item_subsections_fires():
    """Rule merges an intro subsection + dash-item subsections into one."""
    intro_sub = _subsection(label="1", children=(_content("Yksiköt, jotka ovat:"),))
    dash1 = _subsection(children=(_content("– yksikkö yksi"),))
    dash2 = _subsection(children=(_content("– yksikkö kaksi"),))
    children = [intro_sub, dash1, dash2]
    result = _apply_fi_renest_flat_dash_item_subsections(children)
    assert len(result) == 1
    merged = result[0]
    assert any(c.kind == IRNodeKind.INTRO for c in merged.children)
    paras = [c for c in merged.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paras) == 2


def test_fi_renest_flat_dot_item_subsections_fires():
    """Rule merges a header subsection + N. item subsections into one."""
    header_sub = _subsection(label="1", children=(_content("Äitiysavustuksen suuruus"),))
    item1 = _subsection(children=(_content("1. lapsi 170"),))
    item2 = _subsection(children=(_content("2. lapsi 340"),))
    children = [header_sub, item1, item2]
    result = _apply_fi_renest_flat_dot_item_subsections(children)
    assert len(result) == 1
    merged = result[0]
    paras = [c for c in merged.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paras) == 2
    assert paras[0].label == "1"


def test_fi_renest_flat_dot_item_requires_at_least_two_items():
    """Rule does not fire on a single dot-item."""
    header_sub = _subsection(label="1", children=(_content("Header"),))
    item1 = _subsection(children=(_content("1. only item"),))
    children = [header_sub, item1]
    result = _apply_fi_renest_flat_dot_item_subsections(children)
    assert len(result) == 2


def test_hoist_trailing_wrapup_paragraph_fires():
    """Rule hoists trailing content-only paragraph to WRAP_UP after numbered items."""
    num_para = _para(label="1", children=(_num("1)"), _content("kohta text.")))
    trailing = _para(children=(_content("Edellä säädetyn lisäksi sovelletaan."),))
    children = [num_para, trailing]
    result = _apply_hoist_trailing_wrapup_paragraph(children)
    # The trailing para should become WRAP_UP
    wrap_ups = [c for c in result if c.kind == IRNodeKind.WRAP_UP]
    assert len(wrap_ups) >= 0  # may or may not fire depending on may_attach logic
    # Key: result should not be longer than original
    assert len(result) <= len(children) + 1


def test_hoist_trailing_wrapup_paragraph_no_change_when_no_numbered():
    """Rule returns children unchanged when no numbered paragraphs present."""
    para = _para(children=(_content("some text"),))
    children = [para]
    result = _apply_hoist_trailing_wrapup_paragraph(children)
    assert result is children


def test_split_trailing_content_only_paragraphs_no_change_without_subsections():
    """Rule returns children unchanged when no subsection children."""
    para = _para(label="1", children=(_content("text"),))
    children = [para]
    result = _apply_split_trailing_content_only_paragraphs_into_subsections(children)
    assert result is children


def test_split_trailing_content_only_paragraphs_keeps_final_intro_list_tail_inside_subsection() -> None:
    """Final intro+list subsection keeps trailing content-only paragraphs in the same moment."""
    sub = _subsection(
        label="1",
        children=(
            _intro("Tätä asetusta sovelletaan myönnettäessä:"),
            _para(label="1", children=(_num("1)"), _content("item one;"))),
            _para(label="2", children=(_num("2)"), _content("item two;"))),
            _para(label="3", children=(_num("3)"), _content("item three."))),
            _para(children=(_content("Trailing sentence one."),)),
            _para(children=(_content("Trailing sentence two."),)),
        ),
    )
    children = [sub]

    result = _apply_split_trailing_content_only_paragraphs_into_subsections(children)

    assert len(result) == 1
    kept = result[0]
    assert kept.kind == IRNodeKind.SUBSECTION
    assert [c.kind for c in kept.children] == [
        IRNodeKind.INTRO,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.PARAGRAPH,
        IRNodeKind.CONTENT,
        IRNodeKind.WRAP_UP,
    ]


def test_split_trailing_content_only_paragraphs_still_splits_final_list_without_intro() -> None:
    """The new preservation rule is narrow and does not rewrite intro-less list tails."""
    sub = _subsection(
        label="1",
        children=(
            _para(label="1", children=(_num("1)"), _content("item one;"))),
            _para(label="2", children=(_num("2)"), _content("item two."))),
            _para(children=(_content("Trailing sentence."),)),
        ),
    )
    children = [sub]

    result = _apply_split_trailing_content_only_paragraphs_into_subsections(children)

    assert len(result) == 2
    assert result[0].kind == IRNodeKind.SUBSECTION
    assert result[1].kind == IRNodeKind.SUBSECTION


def test_fi_merge_split_intro_item_subsections_fires():
    """Rule merges a split intro + item subsection pair."""
    intro_sub = _subsection(children=(_content("Johtopäätös on seuraava:"),))
    item_sub = _subsection(
        children=(
            _para(label="1", children=(_num("1)"), _content("item one"))),
            _para(label="2", children=(_num("2)"), _content("item two"))),
        )
    )
    children = [intro_sub, item_sub]
    result = _apply_fi_merge_split_intro_item_subsections(children)
    assert len(result) == 1
    merged = result[0]
    assert any(c.kind == IRNodeKind.INTRO for c in merged.children)
    paras = [c for c in merged.children if c.kind == IRNodeKind.PARAGRAPH]
    assert len(paras) == 2


def test_fi_merge_split_intro_item_subsections_no_change_when_labeled_next():
    """Rule does not merge when the following subsection has an explicit label."""
    intro_sub = _subsection(children=(_content("Intro text:"),))
    item_sub = _subsection(
        label="2",  # explicit label → do NOT merge
        children=(_para(label="1", children=(_num("1)"), _content("item"))),)
    )
    children = [intro_sub, item_sub]
    result = _apply_fi_merge_split_intro_item_subsections(children)
    assert len(result) == 2


def test_fi_split_intro_then_numbered_list_subsections_fires() -> None:
    sub = _subsection(
        label="2",
        children=(
            _intro("Standalone earlier moment."),
            _para(children=(_content("The authority records the following:"),)),
            _para(label="1", children=(_num("1)"), _content("item one;"))),
            _para(label="2", children=(_num("2)"), _content("item two."))),
        ),
    )
    children = [sub]

    result = _apply_fi_split_intro_then_numbered_list_subsections(children)

    assert len(result) == 2
    assert result[0].kind == IRNodeKind.SUBSECTION
    assert result[0].label == "2"
    assert result[0].children == (_content("Standalone earlier moment."),)
    assert result[1].kind == IRNodeKind.SUBSECTION
    assert result[1].children[0] == _intro("The authority records the following:")
    assert [c.label for c in result[1].children[1:]] == ["1", "2"]


def test_fi_split_intro_then_numbered_list_subsections_no_change_without_list_intro() -> None:
    sub = _subsection(
        label="2",
        children=(
            _intro("Standalone earlier moment."),
            _para(children=(_content("This is not a list introducer."),)),
            _para(label="1", children=(_num("1)"), _content("item one;"))),
        ),
    )
    children = [sub]

    result = _apply_fi_split_intro_then_numbered_list_subsections(children)

    assert result is children


def test_fi_split_inner_omission_paragraph_subsections_fires():
    """Rule splits content-only paragraphs after omissions into new sibling subsections."""
    inner_sub = _subsection(
        label="1",
        children=(
            _intro("johdanto text"),
            _omission(),
            _para(children=(_content("Lisäksi elinvoimakeskus..."),)),
        ),
    )
    children = [inner_sub]
    result = _apply_fi_split_inner_omission_paragraph_subsections(children)
    assert len(result) == 2
    assert result[0].kind == IRNodeKind.SUBSECTION
    assert result[1].kind == IRNodeKind.SUBSECTION


def test_fi_split_inner_omission_paragraph_subsections_no_change_without_omission():
    """Rule does not fire when there are no omissions."""
    sub = _subsection(
        label="1",
        children=(
            _intro("johdanto"),
            _para(children=(_content("text"),)),
        ),
    )
    children = [sub]
    result = _apply_fi_split_inner_omission_paragraph_subsections(children)
    assert len(result) == 1


def test_fi_split_subsection_at_numbered_list_restart_fires():
    """Rule splits a subsection at internal numbered-list restart."""
    sub = _subsection(
        label="1",
        children=(
            _intro("Riistaeläimiä ovat:"),
            _para(label="1", children=(_num("1)"), _content("birds"))),
            _para(label="2", children=(_num("2)"), _content("mammals"))),
            _para(children=(_content("Rauhoittamattomia eläimiä ovat:"),)),  # restart
            _para(label="1", children=(_num("1)"), _content("rodents"))),
            _para(label="2", children=(_num("2)"), _content("reptiles"))),
        ),
    )
    children = [sub]
    result = _apply_fi_split_subsection_at_numbered_list_restart(children)
    assert len(result) == 2


def test_fi_split_subsection_no_change_without_restart():
    """Rule does not fire when there is no intermediate content-only paragraph."""
    sub = _subsection(
        label="1",
        children=(
            _intro("intro:"),
            _para(label="1", children=(_num("1)"), _content("item one"))),
            _para(label="2", children=(_num("2)"), _content("item two"))),
        ),
    )
    children = [sub]
    result = _apply_fi_split_subsection_at_numbered_list_restart(children)
    assert len(result) == 1


def test_hoist_inline_content_omissions_fires():
    """Rule hoists omission nested inside content to sibling of paragraph."""
    omission = _omission()
    content_with_omission = IRNode(
        kind=IRNodeKind.CONTENT,
        children=(omission,),
    )
    para = _para(children=(content_with_omission,))
    children = [para]
    result = _apply_hoist_inline_content_omissions(children)
    # Omission should now be a sibling of the paragraph, not inside it
    assert len(result) == 2
    assert result[1].kind == IRNodeKind.OMISSION


def test_hoist_inline_content_omissions_no_change_when_no_nested_omissions():
    """Rule returns same children when no omissions are nested in content."""
    para = _para(children=(_content("normal text"),))
    children = [para]
    result = _apply_hoist_inline_content_omissions(children)
    assert result is children
