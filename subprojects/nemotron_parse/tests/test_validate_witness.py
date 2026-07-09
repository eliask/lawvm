"""Hermetic tests for the cross-witness validation harness (pure core).

Drives ``validate_witness``'s PURE core with FAKE ``PageWitness`` carriers — no
PDF, no network, no lawvm import, no heavy deps. Pins the comparison math
(token normalization, pairwise jaccard, corroboration, disagreement
localization) and the deterministic line-based (never-JSON) report, plus the
``run_pages`` witness-inventory / absent-witness behavior via a fake witness fn.

Run: uv run --project subprojects/nemotron_parse pytest subprojects/nemotron_parse/tests -p no:cacheprovider
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_witness as vw  # noqa: E402


# --------------------------------------------------------------------------- #
# Token normalization                                                          #
# --------------------------------------------------------------------------- #


def test_normalize_tokens_folds_case_and_splits_words() -> None:
    assert vw.normalize_tokens("Sen 4 §:ään, litralta.") == ("sen", "4", "ään", "litralta")


def test_normalize_tokens_nfkc_folds_compatibility_forms() -> None:
    # NFKC folds a fullwidth digit to ascii so two witnesses' glyph variants match.
    assert vw.normalize_tokens("４ §") == vw.normalize_tokens("4 §")


def test_fragment_key_is_order_preserving_token_join() -> None:
    assert vw._fragment_key("  Sen  lisäksi  ") == "sen lisäksi"
    assert vw._fragment_key("   ") == ""  # empty-after-normalization collapses


# --------------------------------------------------------------------------- #
# compare_page — pairs, corroboration, localization                            #
# --------------------------------------------------------------------------- #


def _w(pid: str, *frags: str, status: str = "ok") -> vw.PageWitness:
    return vw.PageWitness(pid, fragments=frags, status=status)


def test_two_identical_witnesses_agree_fully() -> None:
    a = _w("pdfium", "4 §", "Sen lisäksi litralta.")
    b = _w("nemotron", "4 §", "Sen lisäksi litralta.")
    c = vw.compare_page(1, [a, b])
    (pair,) = c.pairs
    assert pair.jaccard == 1.0
    assert pair.only_a_tokens == 0 and pair.only_b_tokens == 0
    # both fragments corroborated (present in both witnesses)
    assert "4 §".casefold().strip() and set(c.corroborated) == {"4", "sen lisäksi litralta"}
    # nothing unique to either
    assert all(keys == () for _pid, keys in c.localized)


def test_disagreement_is_localized_to_the_unique_witness() -> None:
    a = _w("pdfium", "valmisteveroa 4 senttiä")
    b = _w("nemotron", "valmisteveroa 4 senttia")  # 'senttia' garble (missing ä)
    c = vw.compare_page(2, [a, b])
    (pair,) = c.pairs
    # shared tokens: valmisteveroa, 4 ; unique: senttiä (a) vs senttia (b)
    assert pair.shared_tokens == 2
    assert pair.only_a_tokens == 1 and pair.only_b_tokens == 1
    loc = dict(c.localized)
    # the whole fragment key is unique to each witness (token order differs by 1)
    assert loc["pdfium"] == ("valmisteveroa 4 senttiä",)
    assert loc["nemotron"] == ("valmisteveroa 4 senttia",)
    assert c.corroborated == ()  # no fragment key is shared verbatim


def test_three_witnesses_corroborate_a_line_two_of_three_share() -> None:
    a = _w("pdfium", "4 §", "extra pdfium line")
    b = _w("vision", "4 §")
    d = _w("nemotron", "4 §")
    c = vw.compare_page(3, [a, b, d])
    assert len(c.pairs) == 3  # 3 choose 2
    assert "4" in c.corroborated  # seen by all three
    loc = dict(c.localized)
    assert loc["pdfium"] == ("extra pdfium line",)  # only pdfium saw it
    assert loc["vision"] == () and loc["nemotron"] == ()


def test_absent_and_error_witnesses_are_reported_but_never_scored() -> None:
    a = _w("pdfium", "4 §")
    b = vw.PageWitness("nemotron", status="absent")
    e = vw.PageWitness("vision", status="error: NemotronParseFailure: boom")
    c = vw.compare_page(4, [a, b, e])
    # only ONE ok witness -> no pairs, no fabricated disagreement against absent
    assert c.pairs == ()
    assert len(c.witnesses) == 3  # all three still inventoried
    (only_ok,) = c.localized  # localization only over ok witnesses
    assert only_ok[0] == "pdfium"


# --------------------------------------------------------------------------- #
# render_report — deterministic, line-based, never JSON                        #
# --------------------------------------------------------------------------- #


def test_report_is_line_based_never_json() -> None:
    a = _w("pdfium", "valmisteveroa 4 senttiä")
    b = _w("nemotron", "valmisteveroa 4 senttia")
    report = vw.render_report([vw.compare_page(1, [a, b])], source="unit.pdf")
    assert not report.lstrip().startswith("{")  # not JSON
    assert "# cross-witness validation :: unit.pdf" in report
    assert "## page 1" in report
    assert "witness pdfium status=ok fragments=1" in report
    assert "agreement pdfium vs nemotron jaccard=0.500" in report
    assert "disagreement pdfium unique=1" in report
    assert "  only:pdfium: valmisteveroa 4 senttiä" in report
    # deterministic: same input -> byte-identical output
    assert report == vw.render_report([vw.compare_page(1, [a, b])], source="unit.pdf")


def test_report_single_witness_says_no_cross_signal() -> None:
    a = _w("pdfium", "4 §")
    absent = vw.PageWitness("nemotron", status="absent")
    report = vw.render_report([vw.compare_page(1, [a, absent])], source="s")
    assert "agreement none (single witness — no cross signal)" in report


# --------------------------------------------------------------------------- #
# run_pages — witness inventory + absent synthesis (fake witness fn)           #
# --------------------------------------------------------------------------- #


def test_run_pages_runs_each_page_and_synthesizes_absent_witnesses() -> None:
    def fake_pdfium(pdf_bytes: bytes, page_num: int) -> vw.PageWitness:
        return vw.PageWitness("pdfium", fragments=(f"page {page_num} line",))

    comparisons = vw.run_pages(b"%PDF-fake", [1, 2], [("pdfium", fake_pdfium)])
    assert [c.page_num for c in comparisons] == [1, 2]
    # pdfium ran; vision + nemotron synthesized as absent (stable inventory)
    ids = {w.producer_id: w.status for w in comparisons[0].witnesses}
    assert ids == {"pdfium": "ok", "vision": "absent", "nemotron": "absent"}


def test_parse_pages_spec_forms() -> None:
    assert vw._parse_pages("1-3") == [1, 2, 3]
    assert vw._parse_pages("3") == [3]
    assert vw._parse_pages("7,1,4") == [1, 4, 7]
    assert vw._parse_pages("0,2") == [2]  # page 0 dropped (1-indexed)
