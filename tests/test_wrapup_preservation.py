"""Tests for wrapUp (loppukappale/conclusion) preservation during item insertion.

When amendments INSERT new items after the last existing item in a numbered
list, the trailing conclusion text (wrapUp) must float to after the newly
inserted items, not stay glued to the old last item.

Example: 2008/1005 section 37 mom 1 -- amendment 2022/256 inserts items 14-17
after old item 13. The conclusion "on tuomittava..." should end up after
item 17, not concatenated onto item 13.
"""

from lawvm.core.ir import IRNode
from lawvm.core.semantic_types import IRNodeKind
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.finland.apply_ir_ops import _insert_item_with_suffix_renumber_ir
from tests.corpus_pin_helpers import pinned_replay


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _para(label: str, text: str = "") -> IRNode:
    return IRNode(
        kind=IRNodeKind.PARAGRAPH,
        label=label,
        children=(
            IRNode(kind=IRNodeKind.NUM, text=f"{label})"),
            IRNode(kind=IRNodeKind.CONTENT, text=text or f"item {label} text"),
        ),
    )


def _intro(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.INTRO, text=text)


def _wrapup(text: str) -> IRNode:
    return IRNode(kind=IRNodeKind.WRAP_UP, text=text)


def _sub(label: str, *children: IRNode) -> IRNode:
    return IRNode(kind=IRNodeKind.SUBSECTION, label=label, children=tuple(children))


# ---------------------------------------------------------------------------
# _insert_item_with_suffix_renumber_ir + wrapUp preservation
# ---------------------------------------------------------------------------


def test_insert_item_preserves_wrapup_after_new_item() -> None:
    """Insert after last item: wrapUp must end up after the inserted item."""
    sub = _sub(
        "1",
        _intro("Joka tahallaan rikkoo"),
        _para("1", "ensimmäinen kohta,"),
        _para("2", "toinen kohta,"),
        _para("3", "kolmas kohta,"),
        _wrapup("on tuomittava sakkoon."),
    )
    new_para = _para("4", "neljäs kohta,")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "4", anchor_idx=2)

    kinds = [c.kind for c in result.children]
    labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    # New item should be present
    assert "4" in labels
    # wrapUp must be the last child
    assert kinds[-1] == IRNodeKind.WRAP_UP
    assert result.children[-1].text == "on tuomittava sakkoon."
    # Paragraphs should be in order before wrapUp
    assert labels == ["1", "2", "3", "4"]


def test_insert_item_at_end_no_anchor_preserves_wrapup() -> None:
    """Insert with no anchor (append): wrapUp must end up after the new item."""
    sub = _sub(
        "1",
        _intro("Joka rikkoo"),
        _para("1", "ensimmäinen,"),
        _para("2", "toinen,"),
        _wrapup("on tuomittava sakkoon."),
    )
    new_para = _para("3", "kolmas,")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "3", anchor_idx=None)

    kinds = [c.kind for c in result.children]
    labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    assert labels == ["1", "2", "3"]
    assert kinds[-1] == IRNodeKind.WRAP_UP
    assert result.children[-1].text == "on tuomittava sakkoon."


def test_insert_item_middle_preserves_wrapup() -> None:
    """Insert in the middle of the list: wrapUp stays at the end."""
    sub = _sub(
        "1",
        _intro("Joka rikkoo"),
        _para("1", "ensimmäinen,"),
        _para("3", "kolmas,"),
        _wrapup("on tuomittava sakkoon."),
    )
    new_para = _para("2", "toinen,")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "2", anchor_idx=0)

    kinds = [c.kind for c in result.children]
    labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    assert labels == ["1", "2", "3"]
    assert kinds[-1] == IRNodeKind.WRAP_UP


def test_insert_item_without_wrapup_still_works() -> None:
    """Subsection without wrapUp: insertion works normally."""
    sub = _sub(
        "1",
        _intro("Joka rikkoo"),
        _para("1", "ensimmäinen,"),
        _para("2", "toinen."),
    )
    new_para = _para("3", "kolmas.")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "3", anchor_idx=1)

    labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]
    assert labels == ["1", "2", "3"]
    # No wrapUp means last child is the last paragraph
    assert result.children[-1].kind == IRNodeKind.PARAGRAPH


def test_insert_item_with_renumber_preserves_wrapup() -> None:
    """Insert that triggers renumbering: wrapUp must remain last."""
    sub = _sub(
        "1",
        _intro("Joka rikkoo"),
        _para("1", "ensimmäinen,"),
        _para("2", "toinen,"),
        _para("3", "kolmas,"),
        _wrapup("on tuomittava sakkoon."),
    )
    # Insert item "2" after item "1", which should renumber old "2" -> "3", old "3" -> "4"
    new_para = _para("2", "uusi toinen,")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "2", anchor_idx=0)

    kinds = [c.kind for c in result.children]
    labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    assert labels == ["1", "2", "3", "4"]
    assert kinds[-1] == IRNodeKind.WRAP_UP
    assert result.children[-1].text == "on tuomittava sakkoon."


def test_insert_non_numeric_item_preserves_wrapup() -> None:
    """Non-numeric (letter-suffixed) item insertion: wrapUp must remain last."""
    sub = _sub(
        "1",
        _intro("Joka rikkoo"),
        _para("1", "ensimmäinen,"),
        _para("1a", "ensimmäinen a,"),
        _wrapup("on tuomittava sakkoon."),
    )
    new_para = _para("1b", "ensimmäinen b,")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "1b", anchor_idx=1)

    kinds = [c.kind for c in result.children]
    assert kinds[-1] == IRNodeKind.WRAP_UP
    assert result.children[-1].text == "on tuomittava sakkoon."


def test_insert_numeric_item_uses_sort_position_when_anchor_missing() -> None:
    """Regression: insert item 16 with anchor 15 absent (repealed) must go after 14, not at end.

    Scenario: 2008/878 §5 — kohta 15 was repealed (removed entirely).
    2025/163 inserts kohta 16 'tilalle'.  Existing items are 1-14, 17-44.
    anchor_idx=None (item 15 not found), but numeric sort must place 16 after 14.
    """
    # Build a subsection with items 1-14 and 17-20 (gap at 15-16, simulating
    # that 15 was repealed and 16 does not yet exist).
    items_before = [_para(str(i), f"item {i}") for i in range(1, 15)]
    items_after = [_para(str(i), f"item {i}") for i in range(17, 21)]
    sub = _sub("1", *items_before, *items_after)

    new_para = _para("16", "new item 16")

    # anchor_idx=None: anchor item 15 is absent (was repealed)
    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "16", anchor_idx=None)

    para_labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]

    # Item 16 must appear immediately after 14, not at the end
    idx_14 = para_labels.index("14")
    idx_16 = para_labels.index("16")
    assert idx_16 == idx_14 + 1, (
        f"Expected item 16 at position {idx_14 + 1}, got {idx_16}. Labels: {para_labels}"
    )

    # Items after 16 must be renumbered: original 17→17, 18→18 etc. (no clash, so unchanged)
    assert "17" in para_labels
    assert "18" in para_labels


def test_insert_numeric_item_sort_position_target_before_all() -> None:
    """Insert item 1 with no anchor and items 3-5 existing: goes at position 0."""
    sub = _sub(
        "1",
        _para("3", "third"),
        _para("4", "fourth"),
        _para("5", "fifth"),
    )
    new_para = _para("1", "first")

    result = _insert_item_with_suffix_renumber_ir(sub, new_para, "1", anchor_idx=None)

    para_labels = [c.label for c in result.children if c.kind == IRNodeKind.PARAGRAPH]
    assert para_labels[0] == "1", f"Expected item 1 first, got {para_labels}"


def test_replay_xml_hoists_trailing_wrapup_after_numbered_items() -> None:
    replay = pinned_replay("2015/517", mode="finlex_oracle", quiet=True)
    section = replay.find_section("72", "11")
    assert section is not None
    subsection = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "1")
    assert subsection.children[-1].kind == IRNodeKind.WRAP_UP
    assert subsection.children[-1].text.startswith(
        "on tuomittava, jollei teosta muualla laissa säädetä ankarampaa rangaistusta,"
    )
    assert subsection.children[-1].text.endswith("sakkoon.")


def test_replay_xml_keeps_1981_555_section_11_moments_split() -> None:
    """Maa-aineslaki § 11 must keep the 4th and 5th moments distinct."""
    replay = pinned_replay("1981/555", mode="finlex_oracle", quiet=True)
    section = replay.find_section("11")
    assert section is not None

    subsection_labels = [child.label for child in section.children if child.kind == IRNodeKind.SUBSECTION]
    assert subsection_labels == ["1", "2", "3", "4", "5"]

    sub3 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "3")
    sub4 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "4")
    sub5 = next(child for child in section.children if child.kind == IRNodeKind.SUBSECTION and child.label == "5")
    assert irnode_to_text(sub3) == (
        "Lupamääräyksiä voidaan lisäksi antaa: 1) ottamiseen liittyvistä laitteista ja liikenteen "
        "järjestämisestä erityisesti pohjaveden suojelemiseksi; 2) ajasta, jonka kuluessa tämän pykälän "
        "nojalla määrätyt toimenpiteet on suoritettava; sekä 3) muista hankkeesta aiheutuvien haittojen "
        "välttämiseksi tai rajoittamiseksi tarpeellisista toimenpiteistä"
    )
    assert irnode_to_text(sub4) == (
        "Määräykset eivät saa aiheuttaa luvan saajalle sellaista vahinkoa ja haittaa, jota on pidettävä "
        "hankkeen laajuuteen ja hänen saamaansa hyötyyn nähden kohtuuttomana."
    )
    assert irnode_to_text(sub5) == (
        "Lupapäätöksen sisällöstä ja luvan edellyttämien toimenpiteiden määräajasta säädetään "
        "tarkemmin valtioneuvoston asetuksella."
    )
