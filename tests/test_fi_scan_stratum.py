"""Scanned / text-poor corpus census (fi_scan_stratum) — hermetic.

Asserts the text-layer stratum classifier (born_digital / mixed / scanned) at its
threshold boundaries, the empty-doc and unreadable typed records, the
content-key-stable CSV ordering (independent of worker completion order), the
aggregate line, the ``scanned`` hard-case subset filter, and the JSON mirror. No
farchive, no PDF lib: the per-page char counts are injected through a fake
``page_text_char_counts`` seam and enumeration through ``locators=``.
"""
from __future__ import annotations

import json

import pytest

from lawvm.tools import fi_scan_stratum as fss


# --- pure classifier at the threshold boundaries ---------------------------

def test_classify_thresholds() -> None:
    # scanned: mean < 50 (near-zero text layer)
    assert fss.classify(0.0) == fss.STRATUM_SCANNED
    assert fss.classify(49.9) == fss.STRATUM_SCANNED
    # mixed: 50 <= mean < 300 (partial text layer)
    assert fss.classify(50.0) == fss.STRATUM_MIXED
    assert fss.classify(135.0) == fss.STRATUM_MIXED
    assert fss.classify(299.9) == fss.STRATUM_MIXED
    # born_digital: mean >= 300 (dense text layer)
    assert fss.classify(300.0) == fss.STRATUM_BORN_DIGITAL
    assert fss.classify(2000.0) == fss.STRATUM_BORN_DIGITAL


def test_measure_pdf_uses_mean_and_min(monkeypatch) -> None:
    # A born-digital doc that nonetheless has one text-poor (scanned) page:
    # classified born_digital by MEAN, but min_page_chars surfaces the poor page.
    monkeypatch.setattr(fss, "page_text_char_counts", lambda b: [1200, 0, 1500])
    rec = fss.measure_pdf("finlex://sd/2020/1/fin/media/a.pdf", b"%PDF-x")
    assert rec.n_pages == 3
    assert rec.stratum == fss.STRATUM_BORN_DIGITAL
    assert rec.min_page_chars == 0
    assert rec.mean_text_chars_per_page == pytest.approx(900.0)


def test_measure_pdf_empty_doc_is_scanned(monkeypatch) -> None:
    monkeypatch.setattr(fss, "page_text_char_counts", lambda b: [])
    rec = fss.measure_pdf("finlex://sd/2020/2/fin/media/b.pdf", b"%PDF-x")
    assert rec.n_pages == 0 and rec.stratum == fss.STRATUM_SCANNED


# --- census: deterministic ordering + strata + hard case -------------------

def _wire_counts(monkeypatch, per_loc: dict[str, list[int]]) -> None:
    """Seam the farchive resolve/read + pdfium behind an in-memory char map."""
    import lawvm.tools.fi_scan_stratum as mod

    class _Span:
        def __init__(self, digest: str) -> None:
            self.digest = digest

    class _FakeFarchive:
        def __init__(self, path: str) -> None:
            self.path = path

        def resolve(self, loc: str):
            return _Span(loc) if loc in per_loc else None

        def read(self, digest: str) -> bytes:
            # non-empty sentinel bytes; the real parse is seamed out below
            return b"%PDF-" + digest.encode()

        def close(self) -> None:
            pass

    # `_measure_one` imports Farchive from the top-level `farchive` module inside
    # the function body; patch that symbol.
    import farchive

    monkeypatch.setattr(farchive, "Farchive", _FakeFarchive)
    monkeypatch.setattr(
        mod, "page_text_char_counts", lambda b: per_loc[b.decode()[len("%PDF-"):]]
    )


def test_census_is_content_key_sorted_and_strata_correct(monkeypatch) -> None:
    # Deliberately UNSORTED input; a mix of all three strata.
    per_loc = {
        "finlex://sd/2020/3/fin/media/c.pdf": [1500, 1600],   # born_digital
        "finlex://sd/2020/1/fin/media/a.pdf": [0, 0, 0],       # scanned
        "finlex://sd/2020/2/fin/media/b.pdf": [1000, 10, 20],  # mean ~343 → born_digital, min 10
        "finlex://sd/2019/9/fin/media/z.pdf": [120, 130],      # mixed
    }
    _wire_counts(monkeypatch, per_loc)
    report = fss.census_scan_strata(
        finlex_path="X", workers=4, locators=list(per_loc.keys())
    )

    # Content-key (locator) sorted regardless of thread completion order.
    locs = [r.locator for r in report.records]
    assert locs == sorted(per_loc.keys())

    by = {r.locator: r for r in report.records}
    assert by["finlex://sd/2020/1/fin/media/a.pdf"].stratum == fss.STRATUM_SCANNED
    assert by["finlex://sd/2019/9/fin/media/z.pdf"].stratum == fss.STRATUM_MIXED
    assert by["finlex://sd/2020/3/fin/media/c.pdf"].stratum == fss.STRATUM_BORN_DIGITAL
    assert by["finlex://sd/2020/2/fin/media/b.pdf"].stratum == fss.STRATUM_BORN_DIGITAL

    counts = dict(report.counts)
    assert counts[fss.STRATUM_BORN_DIGITAL] == 2
    assert counts[fss.STRATUM_MIXED] == 1
    assert counts[fss.STRATUM_SCANNED] == 1
    assert counts[fss.STRATUM_UNREADABLE] == 0

    # Hard case = scanned + mixed.
    hard = {r.locator for r in report.hard_case}
    assert hard == {
        "finlex://sd/2020/1/fin/media/a.pdf",
        "finlex://sd/2019/9/fin/media/z.pdf",
    }


def test_unreadable_pdf_is_typed_not_a_crash(monkeypatch) -> None:
    class _FakeFarchive:
        def __init__(self, path: str) -> None:
            pass

        def resolve(self, loc: str):
            return None  # not resolvable → typed unreadable

        def read(self, digest: str) -> bytes:  # pragma: no cover - unreached
            return b""

        def close(self) -> None:
            pass

    import farchive

    monkeypatch.setattr(farchive, "Farchive", _FakeFarchive)
    report = fss.census_scan_strata(
        finlex_path="X", locators=["finlex://sd/2020/1/fin/media/a.pdf"]
    )
    assert len(report.records) == 1
    assert report.records[0].stratum == fss.STRATUM_UNREADABLE
    assert dict(report.counts)[fss.STRATUM_UNREADABLE] == 1


def test_limit_caps_after_sort(monkeypatch) -> None:
    per_loc = {f"finlex://sd/2020/{i}/fin/media/x.pdf": [1000] for i in range(10)}
    _wire_counts(monkeypatch, per_loc)
    report = fss.census_scan_strata(
        finlex_path="X", limit=3, locators=list(per_loc.keys())
    )
    # Sorted-then-capped: the 3 lexicographically smallest locators.
    assert [r.locator for r in report.records] == sorted(per_loc.keys())[:3]


# --- emitters ---------------------------------------------------------------

def _sample_report(monkeypatch) -> fss.ScanStratumReport:
    per_loc = {
        "finlex://sd/2020/3/fin/media/c.pdf": [1500, 1600],
        "finlex://sd/2020/1/fin/media/a.pdf": [0, 0],
        "finlex://sd/2019/9/fin/media/z.pdf": [120, 130],
    }
    _wire_counts(monkeypatch, per_loc)
    return fss.census_scan_strata(finlex_path="X", locators=list(per_loc.keys()))


def test_render_csv_header_rows_and_aggregate(monkeypatch) -> None:
    report = _sample_report(monkeypatch)
    csv = fss.render_csv(report)
    lines = csv.strip().splitlines()
    assert lines[0] == fss.render_csv.__globals__["_CSV_HEADER"]
    # 3 data rows in sorted order + 1 aggregate line.
    assert len(lines) == 1 + 3 + 1
    assert lines[1].startswith("finlex://sd/2019/9/fin/media/z.pdf,2,125.0,120,mixed")
    agg = lines[-1]
    assert agg.startswith("#agg,3,")
    assert "born_digital=1" in agg and "mixed=1" in agg and "scanned=1" in agg


def test_render_csv_stratum_filter_keeps_full_aggregate(monkeypatch) -> None:
    report = _sample_report(monkeypatch)
    csv = fss.render_csv(report, stratum=fss.STRATUM_SCANNED)
    lines = csv.strip().splitlines()
    # Header + 1 scanned data row + aggregate (still the FULL census counts).
    data = [ln for ln in lines[1:-1]]
    assert len(data) == 1 and data[0].endswith(",scanned")
    assert lines[-1].startswith("#agg,3,")  # full denominator preserved


def test_render_json_mirror(monkeypatch) -> None:
    report = _sample_report(monkeypatch)
    obj = json.loads(fss.render_json(report))
    assert obj["total"] == 3
    assert obj["counts"]["scanned"] == 1
    assert obj["hard_case_count"] == 2  # scanned + mixed
    assert obj["thresholds"]["scanned_max_chars_per_page"] == fss.SCANNED_MAX_CHARS_PER_PAGE
    # records content-key sorted
    assert [r["locator"] for r in obj["records"]] == sorted(
        r.locator for r in report.records
    )
