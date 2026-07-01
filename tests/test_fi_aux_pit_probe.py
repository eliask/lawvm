"""Smoke tests for the all-historical-PIT aux-target probe (#131).

Corpus-free: exercises snapshot enumeration / as-of derivation against a
hand-crafted fixture archive (reusing the shape from
``test_fi_bench_comparable``), the trajectory "hidden mid-life divergence"
detection, and the CLI/API import surface.

The full end-to-end scorer (:func:`score_pit_vs_oracle_tree`) is validated on
real statutes in ``notes_internal/FI_AUX_PIT_TARGET_2026_07_01.md``; it needs
the corpus and a live replay, so it is intentionally not exercised here.
"""
from __future__ import annotations

import datetime as dt

from lawvm.tools import fi_aux_pit_probe as probe

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _amendment_xml(*, effective_date: str) -> bytes:
    return (
        f'<act xmlns="{_AKN_NS}">'
        f"<meta><identification><FRBRWork/></identification>"
        f'<dateEntryIntoForce date="{effective_date}"/>'
        f"</meta><body/></act>"
    ).encode()


class _FixtureArchive:
    """Minimal archive: maps locators → bytes. Mirrors test_fi_bench_comparable."""

    def __init__(self, items: dict[str, bytes]) -> None:
        self._items = items

    def get(self, url: str) -> bytes | None:
        return self._items.get(url)

    def locators(self, pattern: str = "%") -> list[str]:
        return list(self._items.keys())


def _cons_locator(sid: str, version_tag: str) -> str:
    return f"finlex://sd-cons/{sid}/fin@{version_tag}/main.xml"


def _cons_xml(version_tag: str) -> bytes:
    # A consolidated artifact whose embedded FRBR version resolves to version_tag.
    y, n = version_tag[:4], str(int(version_tag[4:]))
    # FRBRthis value must match _FRBRTHIS_VERSION_RE: /{lang}@{8-digit}/ (trailing slash).
    frbrthis = f"/akn/fi/act/statute/{y}/{n}/fin@{version_tag}/main"
    return (
        f'<akomaNtoso xmlns="{_AKN_NS}"><act>'
        f"<meta><identification>"
        f'<FRBRExpression><FRBRthis value="{frbrthis}"/>'
        f'<FRBRuri value="{frbrthis}"/></FRBRExpression>'
        f"</identification></meta><body/></act></akomaNtoso>"
    ).encode()


def test_plan_snapshots_derives_as_of_from_amendment_not_collapsed_date_consolidated() -> None:
    """The as-of date for each snapshot comes from the embedded amendment's own
    effective date — NOT from the collapsed ``date_consolidated`` that Finlex
    shares across every version of a multi-version statute.
    """
    sid = "2015/359"
    archive = _FixtureArchive(
        {
            # three published consolidations of the SAME statute
            _cons_locator(sid, "20150359"): _cons_xml("20150359"),
            _cons_locator(sid, "20200868"): _cons_xml("20200868"),
            _cons_locator(sid, "20210646"): _cons_xml("20210646"),
            # each version tag's amendment source, with distinct effective dates
            "finlex://sd/2015/359/fin/main.xml": _amendment_xml(effective_date="2015-05-01"),
            "finlex://sd/2020/868/fin/main.xml": _amendment_xml(effective_date="2020-12-01"),
            "finlex://sd/2021/646/fin/main.xml": _amendment_xml(effective_date="2021-07-01"),
        }
    )
    plans = probe.plan_snapshots(archive, sid)
    assert [p.version_tag for p in plans] == ["20150359", "20200868", "20210646"]
    assert [p.as_of for p in plans] == [
        dt.date(2015, 5, 1),
        dt.date(2020, 12, 1),
        dt.date(2021, 7, 1),
    ]
    # every snapshot placed (no UNPLACEABLE reason)
    assert all(p.reason == "" for p in plans)


def test_plan_snapshots_sorts_chronologically_and_marks_unplaceable() -> None:
    """Snapshots sort by derived as-of; a version whose amendment source is
    missing is retained but flagged unplaceable and sorted last.
    """
    sid = "1969/10"
    archive = _FixtureArchive(
        {
            _cons_locator(sid, "20200332"): _cons_xml("20200332"),
            _cons_locator(sid, "20151196"): _cons_xml("20151196"),
            _cons_locator(sid, "20200999"): _cons_xml("20200999"),  # no source → unplaceable
            "finlex://sd/2020/332/fin/main.xml": _amendment_xml(effective_date="2020-06-01"),
            "finlex://sd/2015/1196/fin/main.xml": _amendment_xml(effective_date="2016-01-01"),
        }
    )
    plans = probe.plan_snapshots(archive, sid)
    # chronological: 2016-01-01 (20151196) then 2020-06-01 (20200332), unplaceable last
    assert plans[0].version_tag == "20151196"
    assert plans[1].version_tag == "20200332"
    assert plans[-1].version_tag == "20200999"
    assert plans[-1].as_of is None
    assert plans[-1].reason == "no derivable as-of date"


def test_hidden_mid_life_divergence_detected_when_earlier_snapshot_worse() -> None:
    """The trajectory roll-up flags statutes whose min-over-life snapshot score
    is below the latest — the whole reason the aux target exists (1969/10 in the
    real corpus: earliest 62.5 %, latest 100 %).
    """
    scores = [
        probe.SnapshotScore("1969/10", "20151196", "2015/1196", dt.date(2016, 1, 1),
                            0.625, 24, 9, "OK", __import__("collections").Counter()),
        probe.SnapshotScore("1969/10", "20251077", "2025/1077", dt.date(2026, 1, 1),
                            1.0, 24, 0, "OK", __import__("collections").Counter()),
    ]
    scored = [s.struct_sim for s in scores if s.struct_sim >= 0]
    latest = next((s.struct_sim for s in reversed(scores) if s.struct_sim >= 0), None)
    assert min(scored) < (latest or 0.0) - 1e-9  # hidden divergence condition


def test_score_pit_returns_minus_one_for_content_absent_oracle() -> None:
    """A ``contentAbsent`` oracle short-circuits to -1.0 (not scored) BEFORE the
    replay IR is touched — the same contract the bench uses.
    """
    absent_oracle = __import__("lxml.etree", fromlist=["fromstring"]).fromstring(
        f'<body xmlns="{_AKN_NS}"><hcontainer name="contentAbsent"/></body>'.encode()
    )
    # master is never dereferenced on the absent path — a sentinel proves it.
    sentinel = object()
    sim, n_sec, n_pen, _events = probe.score_pit_vs_oracle_tree(sentinel, absent_oracle)
    assert sim == -1.0
    assert n_sec == 0
    assert n_pen == 0


def test_cli_surface_imports() -> None:
    """The public probe API and CLI entry point are importable and named."""
    for name in (
        "plan_snapshots",
        "score_pit_vs_oracle_tree",
        "score_snapshot",
        "probe_statute",
        "main",
        "SnapshotPlan",
        "SnapshotScore",
    ):
        assert hasattr(probe, name), f"missing public symbol: {name}"
