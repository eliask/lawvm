"""Hermetic tests for the ``fi-vision-read-calibration`` harness.

NO ``:8080`` backend, NO libvoikko, NO real archive render: ground truth is built from
synthetic :class:`PageTextLine` fixtures and the vision reader is a STUB whose fidelity is
scripted (perfect / garbled / hallucinating). Exercises the four seams the mission calls
out: GT extraction+validation, the CER/WER/hallucination math on known pairs, the
crop/reflow geometry, and the read-cache keying — plus the config-grid runner and the
multi-read consensus + agreement-predicts-correctness measurement.
"""
from __future__ import annotations

from lawvm.core.source_document.anchors import BBox
from lawvm.tools.fi_vision_read_calibration import (
    Config,
    Decode,
    GTItem,
    ItemResult,
    PageTextLine,
    build_gt_items,
    default_grid,
    hallucination_rate,
    image_address,
    is_gt_text_clean,
    page_is_corrupt,
    read_cache_fingerprint,
    reflow_cut_pixels,
    reflow_stack,
    run_sweep,
    summarize,
)

# --------------------------------------------------------------------------- #
# Fixtures — synthetic born-digital lines with geometry + word gaps.            #
# --------------------------------------------------------------------------- #


def _line(text: str, y_top: float, x0: float = 40.0, x1: float = 400.0,
          gaps: tuple[float, ...] = ()) -> PageTextLine:
    return PageTextLine(text=text, bbox=BBox(x0=x0, y0=y_top - 10.0, x1=x1, y1=y_top),
                        word_gap_x=gaps)


_CLEAN_LINES = (
    _line("Pääministeri Orpon hallitusohjelmassa linjattiin", 700.0, gaps=(120.0, 240.0)),
    _line("perhevapaakorvauksen kertakorvaus 1 500 euroa", 688.0, gaps=(150.0, 280.0)),
    _line("Laki tulee voimaan 1 päivänä tammikuuta 2025.", 676.0, gaps=(140.0, 260.0)),
    _line("Muutetaan lain 14 §:n 2 momentti seuraavasti.", 664.0, gaps=(130.0, 250.0)),
    _line("Sosiaali- ja terveysministeriön asetuksella", 652.0, gaps=(160.0,)),
    _line("säädetään korvauksen enimmäismäärästä erikseen.", 640.0, gaps=(180.0, 300.0)),
    _line("Tämä koskee myös aiemmin myönnettyjä etuuksia.", 628.0, gaps=(120.0, 240.0)),
    _line("Valtioneuvoston yleisistunto hyväksyi esityksen.", 616.0, gaps=(150.0, 270.0)),
)


# --------------------------------------------------------------------------- #
# GT extraction + validation.                                                   #
# --------------------------------------------------------------------------- #


class TestGroundTruth:
    def test_clean_finnish_line_validates(self) -> None:
        assert is_gt_text_clean("Muutetaan lain 14 §:n 2 momentti seuraavasti.")

    def test_pua_garble_rejected(self) -> None:
        # A Private-Use-Area glyph (corrupt-font) must never pass as GT.
        assert not is_gt_text_clean("Muutetaan " + chr(0xF0E1) + "lain seuraavasti")

    def test_replacement_char_rejected(self) -> None:
        assert not is_gt_text_clean("Laki tulee voi�maan 2025")

    def test_trivial_rejected(self) -> None:
        assert not is_gt_text_clean("12")
        assert not is_gt_text_clean("")

    def test_page_corruption_flag(self) -> None:
        clean = _CLEAN_LINES
        assert not page_is_corrupt(clean)
        garbled = tuple(_line("", 700 - 12 * i)
                        for i in range(8))
        assert page_is_corrupt(garbled)

    def test_build_items_shapes(self) -> None:
        items = build_gt_items("HE 1/2025 vp", 0, _CLEAN_LINES, lines_per_page=3)
        kinds = [it.kind for it in items]
        assert kinds.count("line") == 3
        assert ("band", 4) in [(it.kind, len(it.lines)) for it in items]
        assert ("band", 8) in [(it.kind, len(it.lines)) for it in items]
        assert kinds.count("page") == 1
        # Every item's GT is validated-clean, non-empty.
        for it in items:
            assert it.text.strip()

    def test_dirty_line_excluded_from_items(self) -> None:
        lines = (*_CLEAN_LINES[:4], _line(" corrupt", 630.0))
        items = build_gt_items("HE 1/2025 vp", 0, lines, lines_per_page=5)
        for it in items:
            assert "" not in it.text


# --------------------------------------------------------------------------- #
# Metrics on known pairs.                                                        #
# --------------------------------------------------------------------------- #


class TestMetrics:
    def test_perfect_read_zero_error(self) -> None:
        from lawvm.tools.fi_vision_read_calibration import char_error_rate, word_error_rate

        gt = "Muutetaan lain 14 pykala"
        assert char_error_rate(gt, gt) == 0.0
        assert word_error_rate(gt, gt) == 0.0
        assert hallucination_rate(gt, gt) == 0.0

    def test_hallucination_counts_absent_tokens(self) -> None:
        gt = "laki tulee voimaan"
        # Two of four read tokens ("uusi", "pian") are absent from GT.
        assert hallucination_rate(gt, "laki uusi voimaan pian") == 0.5

    def test_hallucination_substitution(self) -> None:
        # A plausible substitution (momentti->pykala) is absent from GT → hallucinated.
        assert hallucination_rate("14 momentti", "14 pykala") == 0.5

    def test_empty_read_no_hallucination(self) -> None:
        assert hallucination_rate("laki tulee voimaan", "") == 0.0

    def test_cer_partial(self) -> None:
        from lawvm.tools.fi_vision_read_calibration import char_error_rate

        cer = char_error_rate("voimaan", "voimacn")
        assert 0.0 < cer < 0.3

    def test_summarize_distribution(self) -> None:
        s = summarize([0.0, 0.1, 0.2, 0.3, 1.0])
        assert s["n"] == 5
        assert s["median"] == 0.2
        assert s["max"] == 1.0
        assert s["p90"] >= s["median"]

    def test_summarize_empty(self) -> None:
        s = summarize([])
        assert s["n"] == 0 and s["max"] == 0.0


# --------------------------------------------------------------------------- #
# Crop / reflow geometry (never mid-glyph).                                      #
# --------------------------------------------------------------------------- #


class TestReflowGeometry:
    def test_reflow_cuts_land_on_word_gaps(self) -> None:
        line = _line("aaa bbb ccc ddd", 700.0, x0=0.0, x1=100.0, gaps=(25.0, 50.0, 75.0))
        cuts = reflow_cut_pixels(line, scale=2.0, k=2)
        assert cuts is not None and len(cuts) == 1
        # The single cut snaps to the middle gap (50pt → 100px at scale 2).
        assert cuts == [100]

    def test_reflow_k3_two_cuts(self) -> None:
        line = _line("aaa bbb ccc ddd eee fff", 700.0, x0=0.0, x1=120.0,
                     gaps=(20.0, 40.0, 60.0, 80.0, 100.0))
        cuts = reflow_cut_pixels(line, scale=1.0, k=3)
        assert cuts is not None and len(cuts) == 2
        assert cuts == sorted(cuts)

    def test_reflow_no_gaps_returns_none(self) -> None:
        # A gap-less line (single long token) CANNOT be reflowed — honest None, never a
        # mid-glyph cut (the empty-read defect a naive mid-word split produced).
        line = _line("perustuslakivaliokunta", 700.0, x0=0.0, x1=100.0, gaps=())
        assert reflow_cut_pixels(line, scale=2.0, k=2) is None

    def test_reflow_too_few_gaps_for_k(self) -> None:
        line = _line("aaa bbb", 700.0, x0=0.0, x1=60.0, gaps=(30.0,))
        assert reflow_cut_pixels(line, scale=1.0, k=3) is None

    def test_reflow_stack_dimensions(self) -> None:
        from PIL import Image

        strip = Image.new("RGB", (120, 10), (0, 0, 0))
        stacked = reflow_stack(strip, cut_px=[60], gap_px=8)
        # Two 60px-wide segments stacked with an 8px gap → 60 wide, 28 tall.
        assert stacked.width == 60
        assert stacked.height == 10 + 10 + 8


# --------------------------------------------------------------------------- #
# Read-cache keying.                                                            #
# --------------------------------------------------------------------------- #


class TestCacheKeying:
    def test_image_address_content_addressed(self) -> None:
        assert image_address(b"abc") == image_address(b"abc")
        assert image_address(b"abc") != image_address(b"abd")

    def test_fingerprint_rekeys_on_prompt(self) -> None:
        d1 = Decode("minimal_transcribe", 0.0, 2048, 1)
        d2 = Decode("structured", 0.0, 2048, 1)
        assert read_cache_fingerprint("m", d1) != read_cache_fingerprint("m", d2)

    def test_fingerprint_rekeys_on_model_and_decode(self) -> None:
        d = Decode("minimal_transcribe", 0.0, 2048, 1)
        assert read_cache_fingerprint("m1", d) != read_cache_fingerprint("m2", d)
        d_temp = Decode("minimal_transcribe", 0.5, 2048, 1)
        d_seed = Decode("minimal_transcribe", 0.0, 2048, 2)
        assert read_cache_fingerprint("m", d) != read_cache_fingerprint("m", d_temp)
        assert read_cache_fingerprint("m", d) != read_cache_fingerprint("m", d_seed)

    def test_fingerprint_stable(self) -> None:
        d = Decode("minimal_transcribe", 0.0, 2048, 1)
        assert read_cache_fingerprint("m", d) == read_cache_fingerprint("m", d)


# --------------------------------------------------------------------------- #
# Runner + consensus with a scripted stub reader.                               #
# --------------------------------------------------------------------------- #


def _item(text: str, kind: str = "line", page: int = 0) -> GTItem:
    ln = _line(text, 700.0, gaps=(120.0, 240.0))
    return GTItem("HE 1/2025 vp", page, kind, ln.bbox, text, (ln,))


class TestRunner:
    def _corpus(self) -> tuple[dict[str, list[GTItem]], dict[int, bytes], dict[int, float]]:
        # Single-page synthetic corpus rendered from a 1x1 white "PDF"? No — we bypass real
        # rendering by monkeypatching in the test below. Here we just build the item maps.
        items = build_gt_items("HE 1/2025 vp", 0, _CLEAN_LINES, lines_per_page=3)
        by_kind: dict[str, list[GTItem]] = {"line": [], "band": [], "page": []}
        for it in items:
            by_kind[it.kind].append(it)
        return by_kind, {0: b"%PDF-fake"}, {0: 841.9}

    def test_perfect_reader_zero_error(self, monkeypatch) -> None:
        import lawvm.tools.fi_vision_read_calibration as mod

        # Bypass real rendering: every item renders to a deterministic per-text PNG whose
        # bytes encode the GT, and the stub returns that GT verbatim.
        def fake_render_images(item, cfg, cache, page_h, pdf_bytes, scale):
            return [item.text.encode("utf-8")], None

        monkeypatch.setattr(mod, "_render_item_images", fake_render_images)

        # Perfect reader: echo the PNG (which is the GT text bytes).
        def reader(png: bytes, decode: Decode) -> str:
            return png.decode("utf-8")

        by_kind, pdf_by_page, heights = self._corpus()
        configs = [Config(name="single_line", variable="mechanism")]
        result = run_sweep(by_kind, configs, reader, pdf_by_page, heights)
        assert result.rows
        assert all(r.cer == 0.0 for r in result.rows)
        assert result.sanity_floor_ok is True

    def test_garbled_reader_high_error_and_agreement_predicts(self, monkeypatch) -> None:
        import lawvm.tools.fi_vision_read_calibration as mod

        def fake_render_images(item, cfg, cache, page_h, pdf_bytes, scale):
            # Encode item identity + scale so scale-varied witnesses get distinct PNGs.
            return [f"{item.text}|s={scale:.1f}".encode("utf-8")], None

        monkeypatch.setattr(mod, "_render_item_images", fake_render_images)

        def reader(png: bytes, decode: Decode) -> str:
            text, _, tail = png.decode("utf-8").partition("|")
            # Low scale (<2.5) garbles heavily; high scale reads faithfully → witnesses
            # AGREE only when both are faithful (agreement should predict low CER).
            if "s=1.5" in tail or "s=2.0" in tail:
                return text[: len(text) // 2] + " XXXX YYYY"
            return text

        by_kind, pdf_by_page, heights = self._corpus()
        configs = [
            Config(name="majority3_scale", variable="mechanism", n_reads=3,
                   independence="scale", consensus="majority3"),
        ]
        result = run_sweep(by_kind, configs, reader, pdf_by_page, heights)
        multi = [r for r in result.rows if r.agreement is not None]
        assert multi, "multi-read rows must carry an agreement score"
        # Consensus (medoid of 3 scale-varied reads, majority faithful) → low CER.
        assert all(r.cer < 0.2 for r in multi)

    def test_reflow_failure_recorded_not_silent(self, monkeypatch) -> None:
        import lawvm.tools.fi_vision_read_calibration as mod

        # A gap-less line cannot reflow → the runner records a typed note, never a silent read.
        gapless = GTItem("HE 1/2025 vp", 0, "line",
                         BBox(x0=0.0, y0=690.0, x1=100.0, y1=700.0),
                         "perustuslakivaliokunta",
                         (_line("perustuslakivaliokunta", 700.0, x0=0.0, x1=100.0, gaps=()),))

        def fake_render_page(pdf_bytes, page_index, scale):
            from PIL import Image

            return Image.new("RGB", (300, 900), (255, 255, 255))

        monkeypatch.setattr(mod, "render_page_pil", fake_render_page)
        by_kind = {"line": [gapless], "band": [], "page": []}
        configs = [Config(name="aspect_reflow_k2_g0", variable="aspect", aspect="reflow_k2_g0")]
        result = run_sweep(by_kind, configs, lambda png, d: "", {0: b"x"}, {0: 900.0})
        assert result.rows
        assert any("reflow_no_word_gaps" in r.note for r in result.rows)


class TestDefaultGrid:
    def test_grid_isolates_one_axis_each(self) -> None:
        grid = default_grid()
        names = {c.name for c in grid}
        # A representative from each axis is present.
        assert "scale_5.0" in names
        assert "crop_full_page" in names and "crop_single_line" in names
        assert "aspect_pad_to_square" in names and "aspect_reflow_k3_g0" in names
        assert "temp_0.5" in names and "prompt_structured" in names
        assert "single_read" in names and "majority3_scale" in names

    def test_grid_baseline_held_on_scale_axis(self) -> None:
        grid = {c.name: c for c in default_grid()}
        for s in (1.5, 2.0, 2.5, 3.0, 4.0, 5.0):
            c = grid[f"scale_{s}"]
            assert c.crop == "single_line" and c.aspect == "thin_strip_as_is"
            assert c.temperature == 0.0 and c.prompt_variant == "minimal_transcribe"


def test_item_result_is_serializable() -> None:
    from lawvm.tools.fi_vision_read_calibration import row_to_dict

    r = ItemResult("c", "scale", "HE 1/2025 vp", 0, "line", 0.1, 0.2, 0.0, 0.9, 2, "read", "")
    d = row_to_dict(r)
    assert d["config"] == "c" and d["cer"] == 0.1 and d["agreement"] == 0.9
