"""Deterministic OMISSION census hermetic tests (no backend / PDF lib / model).

Drives ``ingest.coverage_census`` on SYNTHETIC ``PageElements`` + a real born-digital
simulacrum, proving the DROP blind-spot fix: a seeded dropped section/op surfaces
both an ``pdf.omission_suspect`` (its ink region claimed by no emitted unit) and a
``pdf.sequence_gap`` (the ``§`` ordinal hole); a clean page flags NOTHING; page-number
furniture is distinguished by GEOMETRY (not flagged when unclaimed); and a printed
page-number gap surfaces as a dropped-page sequence gap. Ingest-carrier family.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from lawvm.core.source_document.anchors import BBox
from lawvm.core.source_document.coverage import RegionOwnership, ResidualFamily
from lawvm.core.source_document.extraction import SourceManifestation
from lawvm.ingest.born_digital import born_digital_page
from lawvm.ingest.coverage_census import (
    ink_regions_from_page_elements,
    page_census,
    page_ordinals,
    run_census,
    section_ordinals,
    sequence_gap_residuals,
)
from lawvm.ingest.page_elements import PageElements, PageLine
from lawvm.ingest.simulacrum import PageSimulacrum

PAGE_H = 800.0
PAGE_W = 500.0
DIGEST = "a" * 64


def _man() -> SourceManifestation:
    return SourceManifestation(
        artifact_digest=DIGEST,
        source_bytes=b"%PDF-1.4",
        locator="doc.pdf",
        source_role="government_proposal_draft",
        fetched_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        media_type="application/pdf",
    )


def _pl(
    text: str,
    y_order: int,
    *,
    x0: float = 60.0,
    top: float,
    h: float = 12.0,
    w: float = 380.0,
    band: str = "body",
) -> PageLine:
    return PageLine(
        text=text,
        y_order=y_order,
        bbox=BBox(x0=x0, y0=top - h, x1=x0 + w, y1=top),
        band=band,
        indent=int(x0 // 18),
    )


def _sectioned_page(page_num: int = 1) -> PageElements:
    """Title + three ``§`` sections (3/4/5) + bottom page-number furniture."""
    lines = [
        _pl("LAKI VALMISTEVEROSTA", 0, top=770, band="top"),
        _pl("3 §", 1, top=752),
        _pl("Kolmannen pykalan sisalto jatkuu tassa kohdassa aivan.", 2, top=738),
        _pl("4 §", 3, top=716),
        _pl("Sen lisaksi, mita 1 momentissa saadetaan, hakijalle nyt", 4, top=700),
        _pl("palautetaan valmisteveroa nelja senttia litralta seka", 5, top=686),
        _pl("muita maksuja koskevien saannosten mukaisesti kaikki.", 6, top=672),
        _pl("5 §", 7, top=650),
        _pl("Viidennen pykalan teksti tulee kokonaan tahan kohtaan nyt.", 8, top=636),
        _pl("12", 9, top=40, band="bottom"),
    ]
    return PageElements(
        page_num=page_num,
        lines=tuple(x.text for x in lines),
        page_lines=tuple(lines),
        page_width=PAGE_W,
        page_height=PAGE_H,
    )


def _drop_units(sim: PageSimulacrum, first_words: set[str]) -> PageSimulacrum:
    """Return a simulacrum with the units whose head starts with any given token removed."""
    kept = tuple(
        n
        for n in sim.nodes
        if n.text.split("\n", 1)[0].split(" ", 1)[0] not in first_words
    )
    return PageSimulacrum(
        page_num=sim.page_num,
        nodes=kept,
        freeform=sim.freeform,
        convergence=sim.convergence,
        assurance=sim.assurance,
        raw_wire_digests=sim.raw_wire_digests,
    )


# --------------------------------------------------------------------------- #
# Clean page — the no-false-flag baseline.                                      #
# --------------------------------------------------------------------------- #


def test_clean_page_flags_nothing_and_furniture_is_claimed() -> None:
    pe = _sectioned_page()
    sim = born_digital_page(_man(), 1, pe).simulacrum
    led = run_census([pe], [sim], artifact_digest=DIGEST)
    assert led.omission_count == 0
    assert led.sequence_residuals == ()
    (pc,) = led.pages
    assert pc.coverage_ratio == 1.0
    # The bottom "12" is recognized as furniture by geometry AND claimed by a unit.
    assert pc.ink_furniture == 1
    ink = ink_regions_from_page_elements(pe, page_count=1)
    assert any(r.is_furniture and r.text.strip() == "12" for r in ink)


# --------------------------------------------------------------------------- #
# Seeded drop — the core proof (unclaimed ink + sequence gap).                  #
# --------------------------------------------------------------------------- #


def test_seeded_section_drop_flags_ink_and_sequence_gap() -> None:
    pe = _sectioned_page()
    sim = born_digital_page(_man(), 1, pe).simulacrum
    # Drop the §4 heading unit AND its body paragraph (a dropped op/section).
    dropped = _drop_units(sim, {"4", "Sen"})
    led = run_census([pe], [dropped], artifact_digest=DIGEST)

    omissions = [
        r for r in led.residuals if r.family is ResidualFamily.PDF_OMISSION_SUSPECT
    ]
    seq = [r for r in led.residuals if r.family is ResidualFamily.PDF_SEQUENCE_GAP]
    # Every line of the dropped section is now unclaimed ink → flagged.
    snippets = " || ".join(r.snippet for r in omissions)
    assert "4 §" in snippets
    assert "Sen lisaksi" in snippets
    assert len(omissions) >= 2
    # And the §-ordinal run 3, 5 (4 missing) surfaces one sequence gap at 4.
    assert len(seq) == 1
    assert seq[0].snippet == "4"
    assert "section ordinal 4 is missing" in seq[0].detail
    # All findings ride the existing typed-residual channel (RESIDUAL, never OWNED).
    for r in led.residuals:
        assert r.ownership is RegionOwnership.RESIDUAL
        assert r.anchor.artifact_digest == DIGEST


def test_unclaimed_furniture_is_not_flagged_as_omission() -> None:
    # Drop the page-number ("12") unit but leave everything else claimed: the
    # unclaimed region is FURNITURE (bottom band + bare page number) → NOT an omission.
    pe = _sectioned_page()
    sim = born_digital_page(_man(), 1, pe).simulacrum
    dropped = _drop_units(sim, {"12"})
    led = run_census([pe], [dropped], artifact_digest=DIGEST)
    assert led.omission_count == 0
    assert all(
        r.family is not ResidualFamily.PDF_OMISSION_SUSPECT for r in led.residuals
    )


def test_missing_simulacrum_flags_all_content_ink() -> None:
    # A page with NO produced simulacrum (unread page) → every content region is a
    # silent hole; err toward flagging (furniture still excluded).
    pe = _sectioned_page()
    led = run_census([pe], [None], artifact_digest=DIGEST)
    (pc,) = led.pages
    assert pc.coverage_ratio == 0.0
    # 10 ink lines, 1 furniture → 9 content omissions.
    assert pc.unclaimed_content == 9


# --------------------------------------------------------------------------- #
# Page-number sequence continuity (dropped-page omission on the ink itself).    #
# --------------------------------------------------------------------------- #


def test_printed_page_number_gap_is_a_sequence_gap() -> None:
    # Three physical pages whose printed page numbers jump 10, 11, 13 (12 dropped).
    def _numbered(printed: int) -> PageElements:
        lines = [
            _pl("Jokin runsaasti sisaltoa kantava leipatekstin rivi tassa.", 0, top=700),
            _pl(str(printed), 1, top=40, band="bottom"),
        ]
        return PageElements(
            page_num=printed,
            lines=tuple(x.text for x in lines),
            page_lines=tuple(lines),
            page_width=PAGE_W,
            page_height=PAGE_H,
        )

    pages = [_numbered(10), _numbered(11), _numbered(13)]
    ords = page_ordinals(pages)
    assert [v for v, _ in ords] == [10, 11, 13]
    gaps = sequence_gap_residuals(ords, artifact_digest=DIGEST, kind="page")
    assert len(gaps) == 1
    assert gaps[0].snippet == "12"
    assert gaps[0].family is ResidualFamily.PDF_SEQUENCE_GAP


def test_wide_structural_jump_is_not_flagged() -> None:
    # A legitimate large renumber (chapter restart) 3 -> 40 is NOT a per-integer flood.
    gaps = sequence_gap_residuals(
        ((3, 1), (40, 2)), artifact_digest=DIGEST, kind="section"
    )
    assert gaps == ()


# --------------------------------------------------------------------------- #
# Producer-neutral interface + determinism.                                     #
# --------------------------------------------------------------------------- #


def test_section_ordinals_derived_from_emitted_units() -> None:
    pe = _sectioned_page()
    sim = born_digital_page(_man(), 1, pe).simulacrum
    assert [v for v, _ in section_ordinals([sim])] == [3, 4, 5]


def test_census_is_deterministic() -> None:
    pe = _sectioned_page()
    sim = born_digital_page(_man(), 1, pe).simulacrum
    dropped = _drop_units(sim, {"4", "Sen"})

    def _key(led) -> List[tuple]:
        return [(r.family.value, r.snippet, r.anchor.locator) for r in led.residuals]

    a = run_census([pe], [dropped], artifact_digest=DIGEST)
    b = run_census([pe], [dropped], artifact_digest=DIGEST)
    assert _key(a) == _key(b)


def test_wired_verify_pass_is_additive_over_born_digital_lane() -> None:
    # The opt-in verify pass wired at the born-digital lane consumes the produced
    # pages WITHOUT touching them (byte-identical), and returns typed omissions.
    from lawvm.ingest.born_digital import (
        born_digital_page as _bdp,
        census_born_digital_coverage,
    )

    pe = _sectioned_page()
    bd = _bdp(_man(), 1, pe)
    before = tuple(n.text for n in bd.simulacrum.nodes)
    # Clean pass: nothing flagged, and the simulacrum is untouched.
    clean = census_born_digital_coverage(_man(), [pe], [bd])
    assert clean.omission_count == 0
    assert tuple(n.text for n in bd.simulacrum.nodes) == before  # additive, no mutation
    # A page the geom lane did not produce (None) → all content ink flagged.
    holed = census_born_digital_coverage(_man(), [pe], [None])
    assert holed.omission_count == 9


def test_page_census_direct_over_synthetic_claims() -> None:
    # The core coverage primitive over hand-built ink + claim bboxes (producer-free).
    ink = ink_regions_from_page_elements(_sectioned_page(), page_count=1)
    # Claim a box covering only the very top title line.
    title = ink[0].bbox
    pc = page_census([ink[0], ink[1]], [title], artifact_digest=DIGEST, page_num=1)
    # Title claimed; the "3 §" line below is unclaimed content → one omission.
    assert pc.unclaimed_content == 1
    assert pc.residuals[0].snippet.strip() == "3 §"
