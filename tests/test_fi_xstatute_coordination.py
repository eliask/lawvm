"""Cross-statute (by-id) <ref> coordination enumeration.

A Finlex inline ``<ref>`` element's ``href`` anchors only the FIRST member of a
coordinated section list (``(360/1968) 18 a ja 18 b §:ssä`` → ``#sec_18a``). The
LawVM convention elsewhere is to enumerate EVERY coordinated member, so the
cross-statute lane re-parses the ref's own surface text through the shared body
recognizer and emits a section-level CITES edge for each coordinated sibling the
href dropped. These tests pin that behaviour (``1967/543``-shaped fixtures) plus
the underlying expander helper.
"""
from __future__ import annotations

from lawvm.finland.cross_refs import extract_cross_refs
from lawvm.finland.references.sections import (
    coordinated_member_paths_from_ref_surface,
    parse_body_provision_tail,
)


def _xml_with_ref(href: str, surface: str) -> bytes:
    return (
        '<akomaNtoso xmlns="http://docs.oasis-open.org/legaldocml/ns/akn/3.0">'
        "<act><body><section><num>3 §</num><paragraph><content>"
        f'<p>... annetun lain <ref href="{href}">{surface}</ref> säädetään.</p>'
        "</content></paragraph></section></body></act></akomaNtoso>"
    ).encode("utf-8")


def _cites_targets(edges) -> set[str]:
    return {
        e.target_section
        for e in edges
        if e.edge_type == "CITES"
    }


# ── Helper-level: the coordinated-member expander ───────────────────────────


def test_helper_ja_letter_suffix_pair_adds_second_member() -> None:
    extra = coordinated_member_paths_from_ref_surface(
        "(360/1968) 18 a ja 18 b §:ssä", "sec_18a"
    )
    assert extra == ["sec_18b"]


def test_helper_ja_letter_suffix_non_adjacent_pair() -> None:
    extra = coordinated_member_paths_from_ref_surface(
        "(360/1968) 6 a ja 6 d §:ssä", "sec_6a"
    )
    assert extra == ["sec_6d"]


def test_helper_three_member_list() -> None:
    extra = coordinated_member_paths_from_ref_surface(
        "(123/2000) 6 a, 6 b ja 6 c §:ssä", "sec_6a"
    )
    assert extra == ["sec_6b", "sec_6c"]


def test_helper_range_form_enumerates_every_member() -> None:
    extra = coordinated_member_paths_from_ref_surface(
        "(123/2000) 33 a–33 d §:ssä", "sec_33a"
    )
    assert extra == ["sec_33b", "sec_33c", "sec_33d"]


def test_helper_bare_single_section_adds_nothing() -> None:
    assert coordinated_member_paths_from_ref_surface(
        "(123/2000) 5 §:ssä", "sec_5"
    ) == []


def test_helper_momentti_only_coordination_adds_no_section() -> None:
    # One section, two momentit: no further SECTION member, so no additions.
    assert coordinated_member_paths_from_ref_surface(
        "(123/2000) 6 §:n 1 ja 2 momentissa", "sec_6"
    ) == []


def test_helper_excludes_anchored_member_even_if_first_in_surface() -> None:
    # Anchored member must never be re-emitted by the expander.
    extra = coordinated_member_paths_from_ref_surface(
        "(360/1968) 18 a ja 18 b §:ssä", "sec_18a"
    )
    assert "sec_18a" not in extra


def test_helper_unparsable_surface_is_safe() -> None:
    assert coordinated_member_paths_from_ref_surface("(360/1968)", "sec_18a") == []
    assert coordinated_member_paths_from_ref_surface("", "sec_18a") == []


# ── End-to-end: extract_cross_refs over a <ref> element ─────────────────────


def test_cross_ref_letter_suffix_ja_enumerates_both_members() -> None:
    xml = _xml_with_ref(
        "/akn/fi/act/statute-consolidated/1968/360#sec_18a",
        "(360/1968) 18 a ja 18 b §:ssä",
    )
    edges = extract_cross_refs(xml, "1967/543")
    targets = _cites_targets(edges)
    assert "sec_18a" in targets
    assert "sec_18b" in targets


def test_cross_ref_non_adjacent_letter_suffix_pair() -> None:
    xml = _xml_with_ref(
        "/akn/fi/act/statute-consolidated/1968/360#sec_6a",
        "(360/1968) 6 a ja 6 d §:ssä",
    )
    edges = extract_cross_refs(xml, "1967/543")
    targets = _cites_targets(edges)
    assert "sec_6a" in targets
    assert "sec_6d" in targets
    # Strict: no spurious intermediate members (6b/6c) for a non-range "ja" list.
    assert "sec_6b" not in targets
    assert "sec_6c" not in targets


def test_cross_ref_single_member_unchanged() -> None:
    xml = _xml_with_ref(
        "/akn/fi/act/statute-consolidated/1968/360#sec_5",
        "(360/1968) 5 §:ssä",
    )
    edges = extract_cross_refs(xml, "1967/543")
    targets = _cites_targets(edges)
    assert targets == {"sec_5"}


# ── Regression: the body-tail recognizer still enumerates coordinations ──────


def test_body_tail_internal_coordination_still_enumerates() -> None:
    # Internal tai/ja coordination + ranges remain fully enumerated (unchanged).
    assert [t.section_label for t in parse_body_provision_tail("52 a, 52 d tai 52 e §")] == [
        "52a",
        "52d",
        "52e",
    ]
    assert [t.section_label for t in parse_body_provision_tail("33 a–33 d §")] == [
        "33a",
        "33b",
        "33c",
        "33d",
    ]
    assert [t.section_label for t in parse_body_provision_tail("6 ja 8 §")] == ["6", "8"]
