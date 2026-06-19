"""Tests for the Finnish reference detection-recall audit tool.

The gold-set tests run in a plain environment (no corpus). The corpus-proxy
smoke test is guarded to skip when the farchive archive is absent.
"""
from __future__ import annotations

import os

import pytest

from lawvm.tools.recall_audit import (
    ALL_CLASSES,
    CLASS_BARE_SECTION,
    CLASS_EU_FORM,
    CLASS_ID_CITE,
    CLASS_NAME_HEAD,
    CLASS_TREATY,
    ProxyReport,
    run_gold,
    run_proxy,
    sweep_candidates,
)

_AKN = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _body(text: str) -> bytes:
    xml = (
        f'<akomaNtoso xmlns="{_AKN}"><act><body><p>{text}</p></body></act>'
        "</akomaNtoso>"
    )
    return xml.encode("utf-8")


# ---------------------------------------------------------------------------
# Gold set — exact detection recall
# ---------------------------------------------------------------------------


def test_gold_recall_is_perfect() -> None:
    """The detector detects every reference in the hand-labeled gold set.

    This is the TRUE (small) recall number. If a future change regresses
    detection of any tricky family (coordinated/range sections, by-name, EU
    reference-number, treaty, vague), this test fails with the exact case.
    """
    report = run_gold()
    assert report.total_expected == 6
    assert report.total_detected == report.total_expected, [
        (c.name, c.missed) for c in report.cases if c.missed
    ]
    assert report.recall == 1.0


def test_gold_every_case_has_expected_refs() -> None:
    report = run_gold()
    names = {c.name for c in report.cases}
    assert names == {
        "plain_id_cite",
        "coordinated_and_range_sections",
        "by_name_no_id",
        "eu_reference_number",
        "treaty_and_vague",
    }
    for c in report.cases:
        assert c.expected >= 1


# ---------------------------------------------------------------------------
# Sweep — the independent permissive recognizer
# ---------------------------------------------------------------------------


def test_sweep_finds_each_surface_class() -> None:
    body = (
        "lannoitelain (711/2022) 7 \xa7 ja asetus (EU) N:o 1169/2011 "
        "sek\xe4 SopS 19/1956"
    )
    hits = sweep_candidates(body)
    classes = {h.surface_class for h in hits}
    assert CLASS_ID_CITE in classes
    assert CLASS_BARE_SECTION in classes
    assert CLASS_EU_FORM in classes
    assert CLASS_TREATY in classes
    assert CLASS_NAME_HEAD in classes


def test_sweep_empty_text_no_hits() -> None:
    assert sweep_candidates("") == []
    assert sweep_candidates("no citations here at all") == []


def test_sweep_id_cite_pattern() -> None:
    hits = sweep_candidates("viittaa lakiin (711/2022).")
    id_hits = [h for h in hits if h.surface_class == CLASS_ID_CITE]
    assert len(id_hits) == 1
    assert "711/2022" in id_hits[0].text


def test_sweep_is_upper_bound_overcounts() -> None:
    """A date-like or non-referential paren can fire id_cite — the proxy must
    treat the sweep as an UPPER BOUND (overcount), not ground truth."""
    # "(12/2020)" here is a sweep id-cite candidate even in non-citation prose.
    hits = sweep_candidates("Sopimus tehtiin (12/2020) eräänä päivänä.")
    assert any(h.surface_class == CLASS_ID_CITE for h in hits)


# ---------------------------------------------------------------------------
# Proxy comparison logic — exercised with an in-memory fake corpus reader
# ---------------------------------------------------------------------------


def test_proxy_with_fake_reader_captures_detected_ids() -> None:
    """Run the proxy against synthetic bodies via a fake read_body closure.

    The detector should capture the id-cite and EU candidates, so those classes
    show a high capture rate; this validates the comparison wiring end to end
    without needing the corpus archive.
    """
    bodies = {
        "1/2020": _body(
            "lannoitelain (711/2022) 7 \xa7:ss\xe4 tarkoitettu tuote."
        ),
        "2/2020": _body(
            "Sovelletaan asetusta (EU) N:o 1169/2011 antamisesta."
        ),
    }

    def read_body(sid: str) -> bytes | None:
        return bodies.get(sid)

    report = run_proxy(["1/2020", "2/2020", "missing/9999"], read_body)
    assert isinstance(report, ProxyReport)
    assert report.statutes_scanned == 3
    assert report.statutes_with_body == 2
    assert not report.errored

    # The id-cite (711/2022) is captured by the plain-text lane; the EU
    # "N:o 1169/2011" surfaces as an eu_form candidate (no parenthetical id).
    id_stat = report.per_class[CLASS_ID_CITE]
    assert id_stat.candidates >= 1
    assert id_stat.captured >= 1
    eu_stat = report.per_class[CLASS_EU_FORM]
    assert eu_stat.candidates >= 1
    assert eu_stat.captured >= 1  # detector's EU lane found 1169/2011

    # Capture rate is a fraction in [0, 1].
    for cls in ALL_CLASSES:
        st = report.per_class[cls]
        assert 0.0 <= st.capture_rate <= 1.0


def test_proxy_reader_error_is_fail_loud_not_silent() -> None:
    def read_body(sid: str) -> bytes | None:
        raise RuntimeError("boom")

    report = run_proxy(["x/1"], read_body)
    assert report.statutes_scanned == 1
    assert report.statutes_with_body == 0
    assert len(report.errored) == 1
    assert "boom" in report.errored[0][1]


def test_proxy_uncaptured_cap_respected() -> None:
    body = _body("muutos (1/2099) ja (2/2099) ja (3/2099) ja (4/2099).")

    def read_body(sid: str) -> bytes | None:
        return body

    report = run_proxy(["a/1"], read_body, max_uncaptured=2)
    assert len(report.uncaptured) <= 2


# ---------------------------------------------------------------------------
# Corpus proxy smoke — guarded to skip when the archive is absent
# ---------------------------------------------------------------------------


def _archive_present() -> bool:
    root = os.environ.get("LAWVM_CANONICAL_DATA_ROOT")
    if not root:
        return False
    return os.path.exists(os.path.join(root, "data", "finlex.farchive"))


@pytest.mark.skipif(
    not _archive_present(),
    reason="corpus archive absent (set LAWVM_CANONICAL_DATA_ROOT)",
)
def test_corpus_proxy_smoke() -> None:
    from lawvm.tools.recall_audit import _read_body_via_store, _statute_sample

    store, read_body = _read_body_via_store()
    assert store is not None and read_body is not None
    ids = _statute_sample(store, 25)
    report = run_proxy(ids, read_body, max_uncaptured=50)
    assert report.statutes_scanned == len(ids)
    assert 0.0 <= report.overall_rate() <= 1.0
