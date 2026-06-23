from __future__ import annotations

from tests.corpus_pin_helpers import pinned_replay
from lawvm.finland.johtolause.api import parse_clause
from lawvm.finland.johtolause.surface_model import (
    SurfaceRenumberTail,
    SurfaceTargetRef,
    TargetKind,
    VerbKind,
)
from lawvm.tools.inspect_amendment import build_amendment_bundle


def test_parse_clause_handles_qualified_jolloin_chapter_renumber() -> None:
    text = (
        "lisätään lakiin uusi 8 luku, jolloin nykyinen 8 luku, "
        "sellaisena kuin se on mainitussa 23 päivänä toukokuuta 1986 annetussa "
        "laissa, siirtyy 9 luvuksi"
    )

    result = parse_clause(text)
    sc = result.surface_clause
    assert sc is not None
    assert sc.verb_groups

    first_vg = sc.verb_groups[0]
    assert first_vg.verb == VerbKind.SIIRTAA
    assert len(first_vg.nodes) == 2

    target, tail = first_vg.nodes
    assert isinstance(target, SurfaceTargetRef)
    assert target.kind == TargetKind.CHAPTER
    assert target.label == "8"

    assert isinstance(tail, SurfaceRenumberTail)
    assert tail.new_label == "9"


def test_1990_811_compiles_the_qualified_jolloin_chapter_renumber() -> None:
    bundle = build_amendment_bundle("1978/38", "1990/811", "legal_pit")
    compiled_ops = bundle["compiled_ops"]

    assert "RENUMBER 8 luku" in compiled_ops
    assert "INSERT 8 luku" in compiled_ops


def test_2007_349_compiles_siirtaa_current_section_renumber_tail() -> None:
    bundle = build_amendment_bundle("2007/349", "2010/322", "legal_pit")
    compiled_ops = bundle["compiled_ops"]

    assert "RENUMBER 8 luku 63 §" in compiled_ops
    assert "RENUMBER 8 luku 64 §" in compiled_ops


def test_1978_38_preserves_shifted_old_chapter_9_after_1990_811() -> None:
    state = pinned_replay("1978/38", mode="legal_pit", stop_before="1994/16", quiet=True)
    chapter_labels = [
        child.label
        for child in state.ir.children
        if child.kind.value == "chapter"
    ]

    assert "8" in chapter_labels
    assert "9" in chapter_labels


def _chapter_section_labels(ir, chapter_label: str) -> tuple[str, list[str]]:
    """Return (heading_text, section_labels) for the named chapter in *ir*."""
    for chapter in ir.children:
        if chapter.kind.value != "chapter" or chapter.label != chapter_label:
            continue
        heading = ""
        for child in chapter.children:
            if child.kind.value == "heading":
                heading = (child.text or "").strip()
                break
        sections = [
            child.label
            for child in chapter.children
            if child.kind.value == "section" and child.label
        ]
        return heading, sections
    raise AssertionError(f"chapter {chapter_label!r} not found")


def test_1978_38_consumer_credit_chapter_7_not_mislabelled_as_12() -> None:
    # Regression: chapter 7 ("Kuluttajaluotot") was fully replaced by 2010/746
    # ("muutetaan ... 7 luku"). The label 7 had earlier been vacated by a
    # 1986->...->1997 renumber chain whose final occupant is chapter 12
    # ("Erinäisiä säännöksiä"). Following that stale renumber chain for the
    # reborn chapter-7 body misfiled the consumer-credit sections under the
    # chapter-12 node and left chapter 7 holding only §22.
    state = pinned_replay("1978/38", mode="official_consolidation", quiet=True)

    ch7_heading, ch7_sections = _chapter_section_labels(state.ir, "7")
    ch12_heading, ch12_sections = _chapter_section_labels(state.ir, "12")

    assert ch7_heading == "Kuluttajaluotot"
    assert ch12_heading == "Erinäisiä säännöksiä"

    # The consumer-credit body lives under chapter 7. These labels are unique to
    # chapter 7 (chapter 12 only carries §1, §1a–§1f, §2), so they must never
    # appear under the chapter-12 node.
    for label in ("11a", "11b", "17a", "35", "49", "50", "51"):
        assert label in ch7_sections, f"§{label} should be under chapter 7"
        assert label not in ch12_sections, f"§{label} must not be under chapter 12"

    # Chapter 7 is the substantive consumer-credit chapter, not a §22-only stub.
    assert len(ch7_sections) > 40
    # Chapter 12 holds only the miscellaneous final provisions.
    assert ch12_sections == ["1", "1a", "1b", "1c", "1d", "1e", "1f", "2"]
