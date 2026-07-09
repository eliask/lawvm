"""Hermetic tests for the ``fi-parse-corpus`` A/B harness.

No real archive / no model: the per-member processor is a scripted stub returning
canned ``RowResult`` rows, and the XML-sibling pairing is exercised over a synthetic
locator set. Asserts the discovered locator scheme, the table shape, the worst-first
ranking, the success-criterion computation, and byte-for-byte DETERMINISM (run the
driver twice → identical table).
"""
from __future__ import annotations

from lawvm.tools.fi_parse_corpus import (
    CorpusMember,
    RowResult,
    _select_members,
    _xml_sibling,
    render_table,
    report_to_json,
    run_corpus,
)

# --- fixture locator universe (the real finlex scheme, VERIFIED) ------------ #
_PDF_A = "finlex://sd-cons/1990/631/fin@20021259/media/2110.pdf"
_PDF_B = "finlex://sd/2011/1531/fin/media/6066.pdf"
_PDF_ORPHAN = "finlex://sd/9999/1/fin/media/nope.pdf"  # no sibling main.xml
_PDF_CORR = "finlex://sd-cons/1734/4-000/fin@20180107/media/corrigenda/sk1_1.pdf"
_PDF_HTTPS = "https://example.test/external.pdf"
_UNIVERSE = frozenset(
    {
        _PDF_A,
        _PDF_B,
        _PDF_ORPHAN,
        _PDF_CORR,
        _PDF_HTTPS,
        "finlex://sd-cons/1990/631/fin@20021259/main.xml",
        "finlex://sd/2011/1531/fin/main.xml",
    }
)


def test_xml_sibling_pairs_media_pdf_with_main_xml() -> None:
    assert (
        _xml_sibling(_PDF_A, _UNIVERSE)
        == "finlex://sd-cons/1990/631/fin@20021259/main.xml"
    )
    assert _xml_sibling(_PDF_B, _UNIVERSE) == "finlex://sd/2011/1531/fin/main.xml"


def test_xml_sibling_none_when_gold_absent_or_corrigendum() -> None:
    assert _xml_sibling(_PDF_ORPHAN, _UNIVERSE) is None
    # corrigenda live under media/corrigenda/ → the media-leaf regex excludes them
    assert _xml_sibling(_PDF_CORR, _UNIVERSE) is None


def test_only_with_xml_and_limit_are_a_deterministic_prefix() -> None:
    members = [
        CorpusMember(_PDF_B, "finlex://sd/2011/1531/fin/main.xml"),
        CorpusMember(_PDF_A, "finlex://sd-cons/1990/631/fin@20021259/main.xml"),
        CorpusMember(_PDF_ORPHAN, None),
    ]
    with_xml = _select_members(members, only_with_xml=True, limit=None)
    assert all(m.has_xml for m in with_xml)
    assert len(with_xml) == 2
    limited = _select_members(members, only_with_xml=True, limit=1)
    assert limited == with_xml[:1]  # stable prefix, not a random sample


# --- scripted A/B rows ------------------------------------------------------ #


def _stub(rows_by_locator: dict[str, RowResult]):
    def _proc(member: CorpusMember) -> RowResult:
        return rows_by_locator[member.pdf_locator]

    return _proc


def _ab_row(loc: str, *, be: int, bs: int, ed: int, sd: int, md: int, nd: int) -> RowResult:
    accepted = (ed + sd) < 0 and md <= 0 and nd == 0
    return RowResult(
        pdf_locator=loc,
        xml_locator=loc.replace("/media/x.pdf", "/main.xml"),
        status="ab",
        baseline_extra=be,
        baseline_structure=bs,
        extra_delta=ed,
        structure_delta=sd,
        missing_delta=md,
        numeric_delta=nd,
        accepted=accepted,
    )


_WORST = "finlex://sd/1/a/media/x.pdf"   # base EXTRA+STRUCTURE = 7 (worst)
_MID = "finlex://sd/2/b/media/x.pdf"     # base = 4
_BEST = "finlex://sd/3/c/media/x.pdf"    # base = 1


def _members():
    return [
        CorpusMember(_BEST, _BEST),
        CorpusMember(_MID, _MID),
        CorpusMember(_WORST, _WORST),
        CorpusMember(_PDF_ORPHAN, None),
    ]


def _rows():
    return {
        _WORST: _ab_row(_WORST, be=5, bs=2, ed=-3, sd=-1, md=0, nd=0),
        _MID: _ab_row(_MID, be=3, bs=1, ed=-1, sd=0, md=0, nd=0),
        _BEST: _ab_row(_BEST, be=1, bs=0, ed=0, sd=0, md=0, nd=0),
        _PDF_ORPHAN: RowResult(_PDF_ORPHAN, None, "coverage_only"),
    }


def test_ranking_is_worst_first_and_aggregate_is_correct() -> None:
    report = run_corpus(_members(), processor=_stub(_rows()), workers=4)
    ab_locs = [r.pdf_locator for r in report.rows if r.status == "ab"]
    assert ab_locs == [_WORST, _MID, _BEST]  # descending baseline EXTRA+STRUCTURE
    # coverage-only row sits after the ranked A/B block
    assert report.rows[-1].pdf_locator == _PDF_ORPHAN
    assert report.n_ab == 3
    assert report.n_coverage_only == 1
    assert report.n_failed == 0
    # aggregate deltas summed over the 3 A/B rows
    assert report.total_extra_delta == -4
    assert report.total_structure_delta == -1
    assert report.total_missing_delta == 0
    assert report.total_numeric_delta == 0
    # EXTRA+STRUCTURE down (-5), MISSING not up, NUMERIC unchanged → corpus accepted
    assert report.corpus_accepted
    assert report.n_accepted == 2  # WORST + MID accept; BEST has no improvement


def test_corpus_rejected_when_missing_goes_up() -> None:
    rows = _rows()
    # over-dedup on the worst row: a new MISSING finding pushes corpus MISSING up
    rows[_WORST] = _ab_row(_WORST, be=5, bs=2, ed=-3, sd=-1, md=1, nd=0)
    report = run_corpus(_members(), processor=_stub(rows), workers=4)
    assert report.total_missing_delta == 1
    assert not report.corpus_accepted


def test_corpus_rejected_when_numeric_changes() -> None:
    rows = _rows()
    rows[_MID] = _ab_row(_MID, be=3, bs=1, ed=-1, sd=0, md=0, nd=1)  # euro corrupted
    report = run_corpus(_members(), processor=_stub(rows), workers=4)
    assert report.total_numeric_delta == 1
    assert not report.corpus_accepted


def test_table_shape_header_and_aggregate_row() -> None:
    report = run_corpus(_members(), processor=_stub(_rows()), workers=4)
    table = render_table(report)
    lines = table.splitlines()
    assert lines[0].startswith("# fi-parse-corpus A/B")
    assert "SUCCESS = EXTRA+STRUCTURE strictly DOWN" in table
    # the CSV header line
    header_idx = next(i for i, ln in enumerate(lines) if ln.startswith("rank,pdf_locator"))
    # first data row is the worst PDF, rank 1
    first = lines[header_idx + 1].split(",")
    assert first[0] == "1"
    assert first[1] == _WORST
    assert first[2] == "ab"
    # trailing AGGREGATE row
    assert lines[-1].startswith("AGGREGATE,")


def test_table_is_byte_identical_across_two_runs() -> None:
    r1 = run_corpus(_members(), processor=_stub(_rows()), workers=4)
    r2 = run_corpus(_members(), processor=_stub(_rows()), workers=4)
    assert render_table(r1) == render_table(r2)  # thread-pool order never leaks
    # a higher worker count must not change the deterministic output either
    r3 = run_corpus(_members(), processor=_stub(_rows()), workers=1)
    assert render_table(r1) == render_table(r3)


def test_json_row_order_matches_table_order() -> None:
    report = run_corpus(_members(), processor=_stub(_rows()), workers=4)
    payload = report_to_json(report)
    assert payload["success_criterion"].startswith("EXTRA+STRUCTURE strictly down")
    ranks = [row["rank"] for row in payload["rows"]]
    assert ranks == list(range(1, len(payload["rows"]) + 1))
    assert payload["rows"][0]["pdf_locator"] == _WORST
    assert payload["aggregate"]["corpus_accepted"] is True
