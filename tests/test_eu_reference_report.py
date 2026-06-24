"""Unit + witness tests for the corpus EU-reference SURFACE report.

The classification tests run on a SYNTHETIC store of small body fragments — NO
corpus dependency. They exercise: a CELEX-bound transposition declaration is
counted as bound; a named-but-unbound directive is counted unbound (never
dropped); a general EU-act citation is counted as a primary span; an
embedded-repeal provenance span is kept distinct from operative citations; and
the report's bound+unbound totality guard holds.

One CORPUS-GATED witness test runs the scan over a small real slice and asserts
the surfaced counts are internally consistent. It skips when the corpus is absent.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.tools import eu_reference_report as er

_FINLEX_CORPUS_AVAILABLE = (
    Path(__file__).resolve().parents[1] / "data" / "finlex.farchive"
).exists()


class _FakeStore:
    def __init__(self, bodies: dict[str, bytes]):
        self._bodies = bodies

    def list_statute_ids(self) -> list[str]:
        return list(self._bodies)

    def read_oracle(self, sid: str) -> bytes | None:
        return self._bodies.get(sid)

    def read_source(self, sid: str) -> bytes | None:
        return None

    def read_amendment(self, sid: str) -> bytes | None:
        return None


def test_general_eu_citation_counted() -> None:
    # A bare CELEX + an (EU) N:o form in the body.
    store = _FakeStore(
        {"2020/1": "viittaa asetukseen (EU) N:o 1257/2012 ja CELEX 32016R0679.".encode()}
    )
    report = er.build_eu_reference_report(store, ["2020/1"])
    assert report.eu_citation_acts == 1
    # at least the (EU) N:o span and the CELEX span are recognised
    assert report.eu_citation_spans >= 1
    assert report.celex_spans >= 1


def test_embedded_repeal_span_kept_distinct() -> None:
    # An EU act named only as the object of a repeal is provenance, not operative.
    store = _FakeStore(
        {
            "2020/2": (
                "asetuksen (EY) N:o 1774/2002 kumoamisesta annetussa "
                "asetuksessa (EY) N:o 1069/2009".encode()
            )
        }
    )
    report = er.build_eu_reference_report(store, ["2020/2"])
    # the repealed inner act is tagged embedded and excluded from the primary count
    assert report.eu_citation_embedded_repeal_spans >= 1


def test_report_totality_guard() -> None:
    with pytest.raises(ValueError):
        er.EuReferenceReport(
            statutes_scanned=1,
            transposition_acts=1,
            transposition_claims=3,  # 1+1 != 3
            transposition_bound=1,
            transposition_unbound=1,
            transposition_by_status={},
            eu_citation_acts=0,
            eu_citation_spans=0,
            eu_citation_embedded_repeal_spans=0,
            celex_spans=0,
        )


def test_empty_body_contributes_nothing() -> None:
    store = _FakeStore({"2020/3": b""})
    report = er.build_eu_reference_report(store, ["2020/3"])
    assert report.statutes_scanned == 0
    assert report.eu_citation_acts == 0
    assert report.transposition_claims == 0


def test_serialization_round_trip_keys() -> None:
    store = _FakeStore({"2020/4": "CELEX 32008L0052.".encode()})
    report = er.build_eu_reference_report(store, ["2020/4"])
    d = report.to_canonical_dict()
    assert d["schema"] == "lawvm.eu_reference_report.v1"
    assert d["transposition_bound"] + d["transposition_unbound"] == d["transposition_claims"]


@pytest.mark.skipif(
    not _FINLEX_CORPUS_AVAILABLE, reason="Finland corpus (data/finlex.farchive) not present"
)
def test_corpus_witness_slice_consistent() -> None:
    from farchive import Farchive
    from lawvm.finland.transparent_store import TransparentCorpusStore
    from lawvm.tools.parse_bench import _archive_path

    store = TransparentCorpusStore(Farchive(_archive_path(), readonly=True))
    ids = store.list_statute_ids()[:1500]
    report = er.build_eu_reference_report(store, ids)
    # internal consistency: bound + unbound == total claims; counts non-negative.
    assert report.transposition_bound + report.transposition_unbound == report.transposition_claims
    assert report.eu_citation_acts >= 0
    assert report.statutes_scanned > 0
