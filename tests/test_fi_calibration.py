"""Hermetic tests for the ``fi-calibration`` reliability U-curve harness.

No real archive / no vision model: pages are synthetic ``PageElements`` fixtures
and the region reader is a scripted FAKE whose fidelity DEGRADES with region
coarseness (a whole-page read garbles a numeric token; a fine read is faithful) —
so the ceiling-detection, threshold extraction, adaptive policy, and proxy
validation are all exercised deterministically. Asserts the metric math, the
U-curve ceiling logic, the operating-point derating (0.7x), the version-tag fold,
the deterministic render, and byte-for-byte reproducibility (two runs diff empty).
"""
from __future__ import annotations

from lawvm.core.source_document.anchors import BBox
from lawvm.ingest.page_elements import PageElements, PageLine
from lawvm.tools.fi_calibration import (
    CLIFF_DERATE,
    GRANULARITY_LEVELS,
    Region,
    SweepConfig,
    char_error_rate,
    default_sweep,
    detect_ceiling,
    emit_policy,
    numeric_exact_failures,
    render_report,
    report_to_json,
    run_calibration,
    score_config,
    stratify_page,
    structural_boundary_f1,
    subdivide,
    textlayer_coverage,
    validate_proxies,
    word_error_rate,
)

# --------------------------------------------------------------------------- #
# Synthetic page fixtures (born-digital: full text layer on every line).       #
# --------------------------------------------------------------------------- #


def _line(text: str, y_order: int, y_top: float, x0: float = 40.0, x1: float = 300.0) -> PageLine:
    """A born-digital text line with geometry (10pt tall, at y_top)."""
    return PageLine(
        text=text,
        y_order=y_order,
        bbox=BBox(x0=x0, y0=y_top - 10.0, x1=x1, y1=y_top),
        band="body",
        indent=int(x0 // 18),
    )


def _make_page(page_num: int, texts, *, page_h: float = 800.0, page_w: float = 600.0) -> PageElements:
    """A single-column born-digital page from a list of line texts, top-to-bottom."""
    lines = tuple(texts)
    page_lines = tuple(
        _line(t, i, y_top=page_h - 40.0 - i * 14.0) for i, t in enumerate(texts)
    )
    return PageElements(
        page_num=page_num,
        lines=lines,
        page_lines=page_lines,
        page_width=page_w,
        page_height=page_h,
    )


# A page with protected numeric tokens (``14 §``, a euro amount, a date) embedded
# among ~26 lines of context. A COARSE read (many lines at once) garbles the
# numbers; a FINE read (a small band isolating them) preserves them — a genuine
# U-curve where finer tiling below the garble threshold recovers the tokens.
_PAGE_TEXTS = (
    ["Laki eräiden säännösten muuttamisesta"]
    + [f"Johdantorivi {i} taustaa varten" for i in range(6)]
    + [
        "14 § Tässä pykälässä säädetään menettelystä",
        "Kustannusvaikutus on arviolta 400 miljoonaa euroa",
        "Muutos tulee voimaan 1.1.2026 lukien",
    ]
    + [f"Loppurivi {i} lisää kontekstia varten" for i in range(16)]
)


def _page() -> PageElements:
    return _make_page(1, _PAGE_TEXTS)


# --------------------------------------------------------------------------- #
# A fidelity-degrading fake region reader (the U-curve substrate).             #
# --------------------------------------------------------------------------- #


def _faithful_reader(page: PageElements):
    """Fake reader: reads each region's core-line text back EXACTLY (perfect)."""

    def _read(page_num: int, region: Region, dpi: int) -> str:
        return "\n".join(
            page.lines[i] for i in region.core_line_indexes if i < len(page.lines)
        )

    return _read


def _coarse_garbling_reader(page: PageElements):
    """Fake reader whose fidelity degrades with region size (the U-curve).

    A region covering MANY lines (coarse) garbles the euro amount and drops the
    ``14 §`` token (truncation/garble at the coarse end); a region covering FEW
    lines (fine) reads faithfully. This makes the coarse configs FAIL the numeric
    gate and the fine configs pass — a synthetic but faithful U-curve.
    """

    def _read(page_num: int, region: Region, dpi: int) -> str:
        text = "\n".join(
            page.lines[i] for i in region.core_line_indexes if i < len(page.lines)
        )
        # Coarse region (>= 7 core lines) → garble the numbers + drop the section
        # ref. band6 (<=6 lines) reads faithfully; band12/band24/block/column/
        # whole_page (coarser) garble → a genuine U-curve with band6 at ceiling.
        if len(region.core_line_indexes) >= 7:
            text = text.replace("400", "4OO").replace("14 §", "l4")
        return text

    return _read


# --------------------------------------------------------------------------- #
# Metric math.                                                                  #
# --------------------------------------------------------------------------- #


def test_word_error_rate_perfect_and_degraded() -> None:
    assert word_error_rate("a b c d", "a b c d") == 0.0
    # one substitution over four words = 0.25
    assert abs(word_error_rate("a b c d", "a b x d") - 0.25) < 1e-9
    # empty gold, empty hyp = perfect
    assert word_error_rate("", "") == 0.0
    # empty gold, non-empty hyp = all insertions
    assert word_error_rate("", "x y") == 1.0


def test_char_error_rate_tracks_garble() -> None:
    assert char_error_rate("400 euroa", "400 euroa") == 0.0
    assert char_error_rate("400 euroa", "4OO euroa") > 0.0


def test_numeric_exact_failures_counts_dropped_and_garbled_tokens() -> None:
    gold = "14 § maksaa 400 euroa 1.1.2026"
    # faithful → zero failures
    assert numeric_exact_failures(gold, gold) == 0
    # garbled euro + dropped section ref → failures > 0
    bad = "l4 maksaa 4OO euroa 1.1.2026"
    assert numeric_exact_failures(gold, bad) > 0


def test_structural_boundary_f1_identity_and_merge() -> None:
    gold = "line one\nline two\nline three"
    assert structural_boundary_f1(gold, gold) == 1.0
    merged = "line one line two\nline three"  # two lines merged into one
    assert structural_boundary_f1(gold, merged) < 1.0


def test_textlayer_coverage_full_for_born_digital() -> None:
    assert textlayer_coverage(_page()) == 1.0
    # a page of blank lines → scanned stratum (no text layer)
    scanned = PageElements(page_num=2, lines=("", "", ""), page_lines=())
    assert textlayer_coverage(scanned) == 0.0
    assert stratify_page(scanned).scanned is True


# --------------------------------------------------------------------------- #
# Region subdivision (the pure geometry policy under test).                     #
# --------------------------------------------------------------------------- #


def test_subdivide_whole_page_is_one_region_covering_all_lines() -> None:
    regs = subdivide(_page(), "whole_page")
    assert len(regs) == 1
    assert regs[0].core_line_indexes == tuple(range(len(_PAGE_TEXTS)))
    # gold is the full text-layer text of the page
    assert "14 § Tässä pykälässä säädetään menettelystä" in regs[0].gold_text


def test_subdivide_bands_are_finer_and_cover_all_lines() -> None:
    regs6 = subdivide(_page(), "band6")
    covered = [i for r in regs6 for i in r.core_line_indexes]
    assert sorted(covered) == list(range(len(_PAGE_TEXTS)))
    # band6 over 6 lines is one band; band with k smaller than n splits.
    regs = subdivide(_make_page(1, [f"line {i}" for i in range(20)]), "band6")
    assert len(regs) >= 3  # 20 lines / 6 ≈ 4 bands


def test_subdivide_carries_absolute_bbox_when_geometry_present() -> None:
    # born-digital fixture has per-line bboxes → each region carries an abs_bbox
    regs = subdivide(_page(), "band6")
    assert all(r.abs_bbox is not None for r in regs)
    # a page with NO line geometry (degraded lane) → abs_bbox is None (un-croppable)
    nogeo = PageElements(
        page_num=1, lines=("14 § foo", "400 euroa"), page_lines=()
    )
    regs2 = subdivide(nogeo, "whole_page")
    assert regs2 and regs2[0].abs_bbox is None


def test_subdivide_overlap_adds_corroboration_lines_outside_core() -> None:
    page = _make_page(1, [f"line {i}" for i in range(12)])
    regs = subdivide(page, "band6", overlap=1)
    # a middle band's line_indexes (with overlap) is a superset of its core
    mid = regs[1]
    assert set(mid.core_line_indexes).issubset(set(mid.line_indexes))
    assert len(mid.line_indexes) > len(mid.core_line_indexes)


# --------------------------------------------------------------------------- #
# score_config end-to-end post-stitch.                                          #
# --------------------------------------------------------------------------- #


def test_score_config_faithful_reader_is_perfect() -> None:
    page = _page()
    sc = score_config(page, SweepConfig("band6", 200, 0), _faithful_reader(page))
    assert sc.numeric_failures == 0
    assert sc.wer == 0.0
    assert sc.cer == 0.0


def test_score_config_coarse_reader_fails_numeric_gate_fine_passes() -> None:
    page = _page()
    reader = _coarse_garbling_reader(page)
    coarse = score_config(page, SweepConfig("whole_page", 144, 0), reader)
    fine = score_config(page, SweepConfig("band6", 300, 0), reader)
    # coarse whole-page (6 lines) garbles → numeric failures; fine band reads clean
    assert coarse.numeric_failures > 0
    assert fine.numeric_failures == 0
    # pixels-per-glyph rises with DPI and with finer tiling
    assert fine.pixels_per_glyph > 0


# --------------------------------------------------------------------------- #
# Ceiling detection → operating-point derating.                                #
# --------------------------------------------------------------------------- #


def test_detect_ceiling_picks_coarsest_faithful_and_derates_0_7() -> None:
    page = _page()
    reader = _coarse_garbling_reader(page)
    cfgs = default_sweep()
    scores = [score_config(page, c, reader) for c in cfgs]
    ceil = detect_ceiling("cols1/mid/text", scores)
    assert ceil.cliff_config is not None
    # the coarsest faithful config is NOT whole_page (that one garbles)
    assert ceil.cliff_config.granularity != "whole_page"
    # operating point is 0.7x the cliff physical load
    assert abs(ceil.operating_pixels_per_glyph - ceil.cliff_pixels_per_glyph * CLIFF_DERATE) < 1e-6
    assert ceil.operating_output_tokens == int(ceil.cliff_output_tokens * CLIFF_DERATE)


def test_detect_ceiling_none_when_no_config_faithful() -> None:
    # a reader that ALWAYS garbles → no faithful config → cliff is None
    page = _page()

    def _always_bad(page_num: int, region: Region, dpi: int) -> str:
        text = "\n".join(page.lines[i] for i in region.core_line_indexes)
        return text.replace("400", "4OO").replace("14 §", "l4")

    scores = [score_config(page, c, _always_bad) for c in default_sweep()]
    ceil = detect_ceiling("s", scores)
    assert ceil.cliff_config is None


# --------------------------------------------------------------------------- #
# Emitted adaptive policy + version tag.                                        #
# --------------------------------------------------------------------------- #


def test_emit_policy_folds_thresholds_into_version_tag_and_applies_purely() -> None:
    page = _page()
    reader = _coarse_garbling_reader(page)
    scores = [score_config(page, c, reader) for c in default_sweep()]
    ceilings = {"cols1/mid/text": detect_ceiling("cols1/mid/text", scores)}
    policy = emit_policy(ceilings, default_stratum="cols1/mid/text")
    assert policy.version_tag.startswith("calib.v1+derate")
    # apply is a pure function of geometry → a region tree
    regs = policy.apply(page)
    assert all(isinstance(r, Region) for r in regs)
    # different thresholds → different version tag (content-addressed determinism).
    # A faithful reader → a COARSER cliff (whole_page) with different physical
    # thresholds, so the folded digest (and thus the version tag) differs.
    faithful_scores = [score_config(page, c, _faithful_reader(page)) for c in default_sweep()]
    other = {"cols1/mid/text": detect_ceiling("cols1/mid/text", faithful_scores)}
    assert emit_policy(other).version_tag != policy.version_tag


# --------------------------------------------------------------------------- #
# Proxy validation — the experiment's real product.                            #
# --------------------------------------------------------------------------- #


def test_validate_proxies_tracks_true_error() -> None:
    page = _page()
    reader = _coarse_garbling_reader(page)
    scores = [score_config(page, c, reader) for c in default_sweep()]
    pv = validate_proxies(scores)
    assert pv.n_samples == len(scores)
    # the cross-reader proxy should positively correlate with numeric failures
    # (a garbled region disagrees with the pdfium gold AND fails the numeric gate)
    assert pv.corr_cross_reader_vs_numeric >= 0.0


# --------------------------------------------------------------------------- #
# Full run + deterministic render.                                             #
# --------------------------------------------------------------------------- #


def test_run_calibration_is_deterministic_and_renders() -> None:
    page = _page()
    reader = _coarse_garbling_reader(page)
    r1 = run_calibration([page], reader, variance_repeats=2)
    r2 = run_calibration([page], reader, variance_repeats=2)
    out1 = render_report(r1)
    out2 = render_report(r2)
    assert out1 == out2  # byte-identical across runs
    assert "PER-CONFIG SCORES" in out1
    assert "CEILINGS + OPERATING THRESHOLDS" in out1
    assert "PROXY VALIDATION" in out1
    # JSON form is also deterministic + carries the policy version
    j1 = report_to_json(r1)
    assert str(j1["policy_version"]).startswith("calib.v1")
    assert j1["n_pages"] == 1
    # a faithful reader over the same page yields a coarser ceiling than the garbler
    faithful = run_calibration([page], _faithful_reader(page), variance_repeats=1)
    fc = faithful.ceilings[0]
    assert fc.cliff_config is not None
    # faithful reads → whole_page is already at ceiling (coarsest possible)
    assert fc.cliff_config.granularity == GRANULARITY_LEVELS[0]


def test_variance_probe_is_recorded() -> None:
    page = _page()
    r = run_calibration([page], _faithful_reader(page), variance_repeats=3)
    # a faithful (deterministic) reader → zero WER delta across repeats
    assert r.variance_probe
    assert all(delta == 0.0 for _tag, delta in r.variance_probe)
