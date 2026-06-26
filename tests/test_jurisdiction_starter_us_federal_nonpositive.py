"""Starter-shard tests for the non-positive-law act→USC target resolver.

Covers, with NO network and small in-test fixtures:

- positive-law title membership;
- the inline ``(N U.S.C. M(...))`` parenthetical parser (typed USC address);
- the structural-vs-``note`` href discrimination (a ``note`` ref is an
  uncodified editorial cross-ref, never a structural target);
- the four resolution outcomes (paren+href agree, href-only, paren-only,
  unmapped) of :func:`resolve_nonpositive_target`;
- the ``us_nonpositive_target_unmapped`` / ``us_nonpositive_target_note_only``
  typed findings for targets with no reachable codified address;
- the title-window resolve-rate scan over a tmp farchive built from synthetic
  PLAW USLM (the feasibility-number machinery), including an act-named target
  with no USC signal that stays a non-claim.
"""

from __future__ import annotations

from pathlib import Path

from farchive import Farchive

from lawvm.us_federal.nonpositive import (
    NOTE_ONLY_FINDING_RULE_ID,
    RULE_HREF,
    RULE_PAREN,
    RULE_PAREN_HREF_AGREE,
    RULE_PAREN_HREF_DISAGREE,
    UNMAPPED_FINDING_RULE_ID,
    is_positive_law_title,
    measure_nonpositive_resolve_rate,
    parse_usc_paren_cite,
    parse_usc_structural_href,
    resolve_nonpositive_target,
)
from lawvm.us_federal.sources import plaw_locator

_USLM_NS = "http://schemas.gpo.gov/xml/uslm"


# ---------------------------------------------------------------------------
# Positive-law title membership
# ---------------------------------------------------------------------------


def test_positive_law_title_membership() -> None:
    # A sample of the 27 positive-law titles.
    for t in (1, 11, 18, 28, 35, 38, 49):
        assert is_positive_law_title(t)
    # Non-positive titles handled by this module.
    for t in (7, 15, 26, 42):
        assert not is_positive_law_title(t)


# ---------------------------------------------------------------------------
# Parenthetical parsing
# ---------------------------------------------------------------------------


def test_paren_cite_section_only() -> None:
    addr = parse_usc_paren_cite("Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)")
    assert addr is not None
    assert addr.path == (("title", "15"), ("section", "77e"))


def test_paren_cite_with_sub_segments() -> None:
    addr = parse_usc_paren_cite(
        "Section 461(l)(1) of the Internal Revenue Code of 1986 (26 U.S.C. 461(l)(1))"
    )
    assert addr is not None
    assert addr.path == (
        ("title", "26"),
        ("section", "461"),
        ("subsection", "l"),
        ("paragraph", "1"),
    )


def test_paren_cite_app_form() -> None:
    addr = parse_usc_paren_cite("(50 U.S.C. App. 2401)")
    assert addr is not None
    assert addr.path == (("title", "50"), ("section", "2401"))


def test_paren_cite_absent_returns_none() -> None:
    assert parse_usc_paren_cite("Section 5 of the Securities Act of 1933") is None


# ---------------------------------------------------------------------------
# Structural-vs-note href discrimination
# ---------------------------------------------------------------------------


def test_structural_href_parses() -> None:
    addr = parse_usc_structural_href("/us/usc/t15/s78o-10/a")
    assert addr is not None
    assert addr.path == (
        ("title", "15"),
        ("section", "78o-10"),
        ("subsection", "a"),
    )


def test_note_href_is_not_a_structural_target() -> None:
    # A ``... note`` ref is an editorial cross-ref to an uncodified provision.
    assert parse_usc_structural_href("/us/usc/t15/s636/note") is None


def test_mixed_href_drops_note_facet() -> None:
    addr = parse_usc_structural_href("/us/usc/t42/s10403/d/note")
    assert addr is not None
    assert addr.path == (
        ("title", "42"),
        ("section", "10403"),
        ("subsection", "d"),
    )


# ---------------------------------------------------------------------------
# resolve_nonpositive_target: the four outcomes + findings
# ---------------------------------------------------------------------------


def test_resolve_paren_href_agree() -> None:
    w = resolve_nonpositive_target(
        target_phrase="Section 5 of the Securities Act of 1933 (15 U.S.C. 77e)",
        target_href="/us/usc/t15/s77e",
    )
    assert w.resolve_status == "paren_href_agree"
    assert w.rule_id == RULE_PAREN_HREF_AGREE
    assert w.resolved
    assert w.address is not None
    assert w.address.path == (("title", "15"), ("section", "77e"))


def test_resolve_href_only() -> None:
    w = resolve_nonpositive_target(
        target_phrase="Section 1402 of the Patient Protection and Affordable Care Act",
        target_href="/us/usc/t42/s18071/f",
    )
    assert w.resolve_status == "href"
    assert w.rule_id == RULE_HREF
    assert w.resolved
    assert w.address is not None
    assert w.address.path[0] == ("title", "42")


def test_resolve_paren_only() -> None:
    w = resolve_nonpositive_target(
        target_phrase="Section 303 of the Family Violence Act (42 U.S.C. 10403)",
        target_href="",
    )
    assert w.resolve_status == "paren"
    assert w.rule_id == RULE_PAREN
    assert w.resolved
    assert w.address is not None
    assert w.title == 42


def test_resolve_disagree_takes_href() -> None:
    # Drafter cites the section; the USLM ref lands deeper. Href is canonical;
    # disagreement is flagged.
    w = resolve_nonpositive_target(
        target_phrase="Section 636 of the Small Business Act (15 U.S.C. 636)",
        target_href="/us/usc/t15/s636/a/36/D",
    )
    assert w.resolve_status == "href"
    assert w.rule_id == RULE_PAREN_HREF_DISAGREE
    assert w.address is not None
    assert w.address.path[-1] == ("subparagraph", "D")


def test_resolve_note_only_unmapped() -> None:
    # Amendment to an uncodified provision: only a ``note`` cross-ref, no paren.
    w = resolve_nonpositive_target(
        target_phrase="Section 702(a) of division N of the Consolidated Appropriations Act, 2021",
        target_href="/us/usc/t7/s2011/note",
    )
    assert not w.resolved
    assert w.resolve_status == "note_only"
    assert w.rule_id == NOTE_ONLY_FINDING_RULE_ID
    assert w.address is None


def test_resolve_unmapped_no_signal() -> None:
    # Act-named target with no parenthetical and no USC href: never guessed.
    w = resolve_nonpositive_target(
        target_phrase="Section 40114 of the Violence Against Women Act of 1994",
        target_href="",
    )
    assert not w.resolved
    assert w.resolve_status == "unmapped"
    assert w.rule_id == UNMAPPED_FINDING_RULE_ID
    assert w.address is None


# ---------------------------------------------------------------------------
# Title-window resolve-rate scan over a synthetic farchive (no network)
# ---------------------------------------------------------------------------


def _plaw(meta_num: str, sections: str) -> bytes:
    return (
        f'<lawDoc xmlns="{_USLM_NS}">'
        "<meta><congress>117</congress>"
        f"<docNumber>{meta_num}</docNumber>"
        "<approvedDate>2021-01-01</approvedDate></meta>"
        f"<main>{sections}</main></lawDoc>"
    ).encode("utf-8")


# One law with a resolvable title-15 target (paren + structural href agree) and
# one act-named title-15 target whose only ref is a ``note`` (stays unmapped).
_SYNTH_PLAW_1 = _plaw(
    "10",
    "<section>"
    "<num>1.</num>"
    "<content>"
    'Section 5 of the Securities Act of 1933 (<ref href="/us/usc/t15/s77e">15 U.S.C. 77e</ref>) '
    'is amended by <amendingAction type="amend">striking</amendingAction> '
    "<quotedText>old</quotedText> and inserting "
    "<quotedText>new</quotedText>.</content>"
    "</section>"
    "<section>"
    "<num>2.</num>"
    "<content>"
    'Section 99 of the Example Uncodified Act (<ref href="/us/usc/t15/s9999/note">15 USC 9999 note</ref>) '
    "is amended by <amendingAction type=\"amend\">striking</amendingAction> "
    "<quotedText>x</quotedText>.</content>"
    "</section>",
)

# A second law: an act-named title-15 target resolved purely by paren (no href).
_SYNTH_PLAW_2 = _plaw(
    "20",
    "<section>"
    "<num>1.</num>"
    "<content>"
    "Section 21 of the Securities Exchange Act of 1934 (15 U.S.C. 78u) is amended by "
    "<amendingAction type=\"add\">adding</amendingAction> at the end the following: "
    "<quotedContent>(z) New.</quotedContent></content>"
    "</section>",
)


def _build_archive(tmp_path: Path) -> Farchive:
    arch = Farchive(tmp_path / "us_federal.farchive", readonly=False)
    arch.store(plaw_locator(117, 10), _SYNTH_PLAW_1, storage_class="xml", metadata={})
    arch.store(plaw_locator(117, 20), _SYNTH_PLAW_2, storage_class="xml", metadata={})
    return arch


def test_measure_resolve_rate_over_synthetic_archive(tmp_path: Path) -> None:
    arch = _build_archive(tmp_path)
    try:
        report = measure_nonpositive_resolve_rate(
            arch, title=15, congress_window=[117]
        )
    finally:
        arch.close()

    # Three title-15 amendment units: two resolved (paren+href agree, paren-only),
    # one note-only (unmapped).
    assert report.units == 3
    assert report.resolved == 2
    assert report.unmapped == 1
    assert report.status_counts.get("note_only") == 1
    assert 0.66 <= report.resolve_rate <= 0.67
    js = report.to_jsonable()
    assert js["positive_law_title"] is False
    assert js["report_kind"] == "nonpositive_resolve_rate"


def test_measure_skips_other_titles(tmp_path: Path) -> None:
    arch = _build_archive(tmp_path)
    try:
        # Title 11 is positive-law and not present in these synthetic laws.
        report = measure_nonpositive_resolve_rate(
            arch, title=11, congress_window=[117]
        )
    finally:
        arch.close()
    assert report.units == 0
    assert report.resolve_rate == 0.0
