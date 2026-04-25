"""Regression tests for bench_comparable oracle selection (Option Z fix).

Exercises _is_self_comparable_cached_artifact and _select_from_cached_artifacts
with hand-crafted fixtures that reproduce the 2013/331 pathology:
all cached artifacts share the same date_consolidated but differ in which
amendment version is embedded.

The fix (Option Z): self-comparability is determined from the amendment's own
effective/issue date, not from date_consolidated.  When date_consolidated is
the same for every artifact, the latest embedded version must still be selected.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from lawvm.finland import consolidated_store
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.consolidated_store import (
    CachedConsolidatedArtifact,
    SelectionProvenance,
    _is_self_comparable_cached_artifact,
    _select_from_cached_artifacts,
    _select_from_cached_artifacts_with_info,
)

# ---------------------------------------------------------------------------
# AKN XML fixture helpers
# ---------------------------------------------------------------------------

_AKN_NS = "http://docs.oasis-open.org/legaldocml/ns/akn/3.0"


def _amendment_xml(
    *,
    effective_date: str | None = None,
    issue_date: str | None = None,
    expiry_date: str | None = None,
) -> bytes:
    """Build a minimal AKN amendment XML with the requested dates."""
    frbrdate_blocks: list[str] = []
    if issue_date:
        frbrdate_blocks.append(
            f'<FRBRdate xmlns="{_AKN_NS}" date="{issue_date}" name="dateIssued"/>'
        )
    if expiry_date:
        # Expiry text pattern recognized by _amendment_expiry_date.
        # The function scans act body text for "on voimassa N päivään MONTH YEAR".
        # For test simplicity we embed it as a FRBRdate with a custom name and
        # instead mock the function — but actually the simplest approach is to
        # include the recognized text in the act body.
        pass  # handled below via body text

    eif_block = ""
    if effective_date:
        eif_block = (
            f'<dateEntryIntoForce xmlns="{_AKN_NS}" date="{effective_date}"/>'
        )

    expiry_body = ""
    if expiry_date:
        # text form recognised by _amendment_expiry_date regex
        expiry_body = (
            f'<p xmlns="{_AKN_NS}">Tämä asetus on voimassa 31 päivään joulukuuta'
            f' {expiry_date[:4]}.</p>'
        )

    return (
        f'<act xmlns="{_AKN_NS}">'
        f"<meta>"
        f"<identification>"
        f"<FRBRWork>{''.join(frbrdate_blocks)}</FRBRWork>"
        f"</identification>"
        f"{eif_block}"
        f"</meta>"
        f"<body><section><content>{expiry_body}</content></section></body>"
        f"</act>"
    ).encode()


class _FixtureArchive:
    """A minimal ConsolidatedArchiveLike backed by explicit byte maps."""

    def __init__(self, items: dict[str, bytes]) -> None:
        self._items = items

    def get(self, url: str) -> bytes | None:
        return self._items.get(url)

    def locators(self, pattern: str = "%") -> list[str]:
        return list(self._items.keys())


def _make_artifact(
    sid: str,
    version_tag: str,
    date_consolidated: dt.date | None,
) -> CachedConsolidatedArtifact:
    locator = f"finlex://sd-cons/{sid}/fin@{version_tag}/main.xml"
    return CachedConsolidatedArtifact(
        sid=sid,
        locator=locator,
        canonical_locator=locator,
        xml=b"<placeholder/>",
        version_tag=version_tag,
        date_consolidated=date_consolidated,
    )


# ---------------------------------------------------------------------------
# Unit tests for _is_self_comparable_cached_artifact
# ---------------------------------------------------------------------------


def test_is_self_comparable_normal_case() -> None:
    """An artifact whose effective date is before date_consolidated is accepted."""
    # version tag 20150103 → amendment id 2015/103
    artifact = _make_artifact("2013/331", "20150103", dt.date(2015, 2, 1))
    archive = _FixtureArchive(
        {
            "finlex://sd/2015/103/fin/main.xml": _amendment_xml(
                effective_date="2015-02-01"
            )
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is True


def test_is_self_comparable_effective_after_date_consolidated_option_z() -> None:
    """Option Z: artifact whose effective date is after date_consolidated is STILL accepted.

    This is the 2013/331-class pathology where Finlex stamps all artifacts with
    date_consolidated = issue date of the latest amendment, but the latest
    amendment's effective date is later.
    """
    # version tag 20211030 → amendment id 2021/1030
    artifact = _make_artifact("2013/331", "20211030", dt.date(2021, 11, 25))
    archive = _FixtureArchive(
        {
            # effective_date 2021-12-01 is AFTER date_consolidated 2021-11-25
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                effective_date="2021-12-01",
                issue_date="2021-11-25",
            )
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is True


def test_is_self_comparable_no_source_returns_false() -> None:
    artifact = _make_artifact("2013/331", "20211030", dt.date(2021, 11, 25))
    archive = _FixtureArchive({})
    assert _is_self_comparable_cached_artifact(artifact, archive) is False


def test_is_self_comparable_no_ordering_date_returns_false() -> None:
    """If neither effective nor issue date can be found, artifact is rejected."""
    artifact = _make_artifact("2013/331", "20211030", dt.date(2021, 11, 25))
    archive = _FixtureArchive(
        {
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml()
            # no effective_date or issue_date supplied
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is False


def test_is_self_comparable_already_expired_returns_false() -> None:
    """An artifact whose amendment expired before it took effect is rejected."""
    # Build XML that has an expiry in the same year as effective (degenerate)
    artifact = _make_artifact("2013/331", "20211030", dt.date(2021, 11, 25))

    # Patch the expiry detection: use a real expiry that the regex will find.
    # effective 2021-12-01, expiry 2021-12-01 → expiry <= ordering_date → reject.
    # The easiest way: supply effective_date = 2021-12-01 and mock expiry.
    import unittest.mock as mock

    with mock.patch(
        "lawvm.finland.metadata._amendment_expiry_date",
        return_value=dt.date(2021, 12, 1),
    ):
        archive = _FixtureArchive(
            {
                "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                    effective_date="2021-12-01",
                    issue_date="2021-11-25",
                )
            }
        )
        assert _is_self_comparable_cached_artifact(artifact, archive) is False


# ---------------------------------------------------------------------------
# Integration test: collapsed date_consolidated selects latest embedded version
# ---------------------------------------------------------------------------


def test_select_bench_comparable_picks_latest_when_dates_collapsed() -> None:
    """The collapsed-dates pathology must not cause bench_comparable to fall back.

    Given four artifacts all sharing date_consolidated = 2021-11-25 but with
    embedded version tags corresponding to amendments effective on different
    dates, bench_comparable must select the artifact whose embedded amendment
    has the latest effective date.
    """
    # These mirror the 2013/331 case structure.
    SHARED_DC = dt.date(2021, 11, 25)
    artifacts = [
        _make_artifact("2013/331", "20150103", SHARED_DC),  # 2015/103  eff 2015
        _make_artifact("2013/331", "20160960", SHARED_DC),  # 2016/960  eff 2016
        _make_artifact("2013/331", "20180781", SHARED_DC),  # 2018/781  eff 2018
        _make_artifact("2013/331", "20211030", SHARED_DC),  # 2021/1030 eff 2021-12-01
    ]

    archive = _FixtureArchive(
        {
            "finlex://sd/2015/103/fin/main.xml": _amendment_xml(
                effective_date="2015-02-03"
            ),
            "finlex://sd/2016/960/fin/main.xml": _amendment_xml(
                effective_date="2016-12-01"
            ),
            "finlex://sd/2018/781/fin/main.xml": _amendment_xml(
                effective_date="2018-09-06"
            ),
            # 2021/1030: issue_date = date_consolidated, effective_date > date_consolidated
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                effective_date="2021-12-01",
                issue_date="2021-11-25",
            ),
        }
    )

    selected = _select_from_cached_artifacts(
        artifacts,
        selector=ConsolidatedArtifactSelector.bench_comparable(),
        lang="fin",
        archive=archive,
    )

    assert selected is not None
    assert selected.version_tag == "20211030", (
        f"Expected 20211030 but got {selected.version_tag}. "
        "bench_comparable must not fall back to stale version when all "
        "date_consolidated values are identical."
    )


def test_select_bench_comparable_logs_oracle_metadata_collapsed_dates(
    caplog: Any,
) -> None:
    """Observation ORACLE_METADATA_COLLAPSED_DATES must appear in logs when Option Z fires."""
    import logging

    consolidated_store._SEEN_COLLAPSED_DATE_WARNINGS.clear()
    SHARED_DC = dt.date(2021, 11, 25)
    artifacts = [
        _make_artifact("2013/331", "20211030", SHARED_DC),
    ]
    archive = _FixtureArchive(
        {
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                effective_date="2021-12-01",
                issue_date="2021-11-25",
            )
        }
    )
    with caplog.at_level(logging.INFO, logger="lawvm.finland.consolidated_store"):
        _is_self_comparable_cached_artifact(artifacts[0], archive)

    assert any(
        "ORACLE_METADATA_COLLAPSED_DATES" in r.message for r in caplog.records
    ), "Expected ORACLE_METADATA_COLLAPSED_DATES warning in log"


# ---------------------------------------------------------------------------
# 180-day tolerance boundary tests (corpus.py:404 convention)
# ---------------------------------------------------------------------------
# version_tag 20200101 → amendment_id 2020/101
# version_tag 20200102 → amendment_id 2020/102
# version_tag 20200103 → amendment_id 2020/103
# version_tag 20200104 → amendment_id 2020/104
# version_tag 20200105 → amendment_id 2020/105

_BASE_DC = dt.date(2020, 1, 1)


def test_180day_tolerance_fixture_a_gap_6_accepted() -> None:
    """Fixture A: gap = 6 days (like 2013/331) → accepted."""
    artifact = _make_artifact("2020/101", "20200101", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/101/fin/main.xml": _amendment_xml(
                effective_date="2020-01-07",  # 6 days after date_consolidated
                issue_date="2020-01-01",
            )
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is True


def test_180day_tolerance_fixture_b_gap_179_accepted() -> None:
    """Fixture B: gap = 179 days → accepted (still within tolerance)."""
    artifact = _make_artifact("2020/102", "20200102", _BASE_DC)
    # 2020-01-01 + 179 days = 2020-06-28 (verified: dt.date(2020,6,28) - dt.date(2020,1,1) == 179)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/102/fin/main.xml": _amendment_xml(
                effective_date="2020-06-28",  # 179 days after date_consolidated
                issue_date="2020-01-01",
            )
        }
    )
    gap = (dt.date(2020, 6, 28) - _BASE_DC).days
    assert gap == 179, f"Fixture setup error: expected gap=179, got {gap}"
    assert _is_self_comparable_cached_artifact(artifact, archive) is True


def test_180day_tolerance_fixture_c_gap_181_rejected() -> None:
    """Fixture C: gap = 181 days → REJECTED (exceeds 180-day tolerance)."""
    artifact = _make_artifact("2020/103", "20200103", _BASE_DC)
    # 2020-01-01 + 181 days = 2020-06-30 (2020 is a leap year)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/103/fin/main.xml": _amendment_xml(
                effective_date="2020-06-30",  # 181 days after date_consolidated
                issue_date="2020-01-01",
            )
        }
    )
    gap = (dt.date(2020, 6, 30) - _BASE_DC).days
    assert gap == 181, f"Fixture setup error: expected gap=181, got {gap}"
    assert _is_self_comparable_cached_artifact(artifact, archive) is False


def test_180day_tolerance_fixture_d_gap_365_rejected() -> None:
    """Fixture D: gap = 365 days → REJECTED (well beyond 180-day tolerance).

    Note: 2020 is a leap year, so 2021-01-01 is actually 366 days from 2020-01-01;
    the label "365 days" is approximate — what matters is that the gap >> 180.
    """
    artifact = _make_artifact("2020/104", "20200104", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/104/fin/main.xml": _amendment_xml(
                effective_date="2021-01-01",  # 366 days after date_consolidated
                issue_date="2020-01-01",
            )
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is False


def test_180day_tolerance_fixture_e_negative_gap_accepted() -> None:
    """Fixture E: negative gap (ordering_date < date_consolidated) → accepted.

    This is the common case: the amendment was already in force by the time
    Finlex stamped date_consolidated.  gap_days < 0, which is <= 180, so
    the artifact is accepted and no ORACLE_METADATA_COLLAPSED_DATES warning fires.
    """
    artifact = _make_artifact("2020/105", "20200105", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/105/fin/main.xml": _amendment_xml(
                effective_date="2019-06-01",  # well before date_consolidated
                issue_date="2019-05-15",
            )
        }
    )
    assert _is_self_comparable_cached_artifact(artifact, archive) is True


def test_180day_tolerance_warning_fires_on_positive_gap(caplog: Any) -> None:
    """ORACLE_METADATA_COLLAPSED_DATES log must fire for accepted Finlex-ahead cases (gap > 0, <= 180)."""
    import logging

    consolidated_store._SEEN_COLLAPSED_DATE_WARNINGS.clear()
    artifact = _make_artifact("2020/101", "20200101", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/101/fin/main.xml": _amendment_xml(
                effective_date="2020-01-07",  # gap = 6 days, accepted
                issue_date="2020-01-01",
            )
        }
    )
    with caplog.at_level(logging.INFO, logger="lawvm.finland.consolidated_store"):
        result = _is_self_comparable_cached_artifact(artifact, archive)

    assert result is True
    assert any(
        "ORACLE_METADATA_COLLAPSED_DATES" in r.message for r in caplog.records
    ), "Expected ORACLE_METADATA_COLLAPSED_DATES log for Finlex-ahead accepted case"


def test_180day_tolerance_warning_deduplicates_repeated_artifact(caplog: Any) -> None:
    """Repeated acceptance of the same Option Z artifact should log only once."""
    import logging

    consolidated_store._SEEN_COLLAPSED_DATE_WARNINGS.clear()
    artifact = _make_artifact("2020/101", "20200101", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/101/fin/main.xml": _amendment_xml(
                effective_date="2020-01-07",
                issue_date="2020-01-01",
            )
        }
    )

    with caplog.at_level(logging.INFO, logger="lawvm.finland.consolidated_store"):
        first = _is_self_comparable_cached_artifact(artifact, archive)
        second = _is_self_comparable_cached_artifact(artifact, archive)

    assert first is True
    assert second is True
    matching = [
        record for record in caplog.records if "ORACLE_METADATA_COLLAPSED_DATES" in record.message
    ]
    assert len(matching) == 1


def test_180day_tolerance_no_warning_on_negative_gap(caplog: Any) -> None:
    """No ORACLE_METADATA_COLLAPSED_DATES log for negative-gap (amendment already in force) case."""
    import logging

    artifact = _make_artifact("2020/105", "20200105", _BASE_DC)
    archive = _FixtureArchive(
        {
            "finlex://sd/2020/105/fin/main.xml": _amendment_xml(
                effective_date="2019-06-01",
                issue_date="2019-05-15",
            )
        }
    )
    with caplog.at_level(logging.INFO, logger="lawvm.finland.consolidated_store"):
        result = _is_self_comparable_cached_artifact(artifact, archive)

    assert result is True
    assert not any(
        "ORACLE_METADATA_COLLAPSED_DATES" in r.message for r in caplog.records
    ), "No ORACLE_METADATA_COLLAPSED_DATES log expected when ordering_date <= date_consolidated"


# ---------------------------------------------------------------------------
# Item 4: OracleSelectorInfo / SelectionProvenance provenance tests
# ---------------------------------------------------------------------------


def test_selection_provenance_populated_for_bench_comparable() -> None:
    """_select_from_cached_artifacts_with_info returns a SelectionProvenance.

    Reproduces the 2013/331-class fixture: four artifacts, all with the same
    date_consolidated.  bench_comparable mode should select 20211030 and
    report tolerance_applied=True (because that artifact's effective date is
    after date_consolidated).
    """
    SHARED_DC = dt.date(2021, 11, 25)
    artifacts = [
        _make_artifact("2013/331", "20150103", SHARED_DC),
        _make_artifact("2013/331", "20160960", SHARED_DC),
        _make_artifact("2013/331", "20180781", SHARED_DC),
        _make_artifact("2013/331", "20211030", SHARED_DC),
    ]
    archive = _FixtureArchive(
        {
            "finlex://sd/2015/103/fin/main.xml": _amendment_xml(effective_date="2015-02-03"),
            "finlex://sd/2016/960/fin/main.xml": _amendment_xml(effective_date="2016-12-01"),
            "finlex://sd/2018/781/fin/main.xml": _amendment_xml(effective_date="2018-09-06"),
            # 2021/1030: effective_date > date_consolidated → tolerance_applied
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                effective_date="2021-12-01",
                issue_date="2021-11-25",
            ),
        }
    )
    artifact, prov = _select_from_cached_artifacts_with_info(
        artifacts,
        selector=ConsolidatedArtifactSelector.bench_comparable(),
        lang="fin",
        archive=archive,
    )

    assert artifact is not None
    assert isinstance(prov, SelectionProvenance)

    # selector_mode must reflect caller's intent ("bench_comparable")
    assert prov.selector_mode == "bench_comparable", (
        f"Expected selector_mode='bench_comparable', got {prov.selector_mode!r}"
    )

    # chosen version must be the latest self-comparable (20211030)
    assert prov.chosen_version_tag == "20211030", (
        f"Expected chosen_version_tag='20211030', got {prov.chosen_version_tag!r}"
    )

    # tolerance_applied must be True (20211030 effective > date_consolidated)
    assert prov.tolerance_applied is True, (
        "Expected tolerance_applied=True for the 2013/331-class pathology "
        "(effective date 2021-12-01 > date_consolidated 2021-11-25)"
    )

    # rejected_candidates should list the other three that were NOT chosen
    # (all were self-comparable, none were screened out by the filter)
    # In bench_comparable mode rejected_version_tags are artifacts that
    # _is_self_comparable returned False for — here all four pass.
    assert prov.rejected_version_tags == (), (
        f"Expected no rejected candidates, got {prov.rejected_version_tags}"
    )


def test_selection_provenance_rejected_candidates_populated() -> None:
    """Artifacts that fail _is_self_comparable appear in rejected_version_tags."""
    SHARED_DC = dt.date(2021, 11, 25)
    artifacts = [
        # This one fails: no source XML in archive → _is_self_comparable returns False
        _make_artifact("2013/331", "20150103", SHARED_DC),
        # This one passes
        _make_artifact("2013/331", "20211030", SHARED_DC),
    ]
    archive = _FixtureArchive(
        {
            # Only provide the source for 20211030; 20150103 will be rejected
            "finlex://sd/2021/1030/fin/main.xml": _amendment_xml(
                effective_date="2021-12-01",
                issue_date="2021-11-25",
            ),
        }
    )
    artifact, prov = _select_from_cached_artifacts_with_info(
        artifacts,
        selector=ConsolidatedArtifactSelector.bench_comparable(),
        lang="fin",
        archive=archive,
    )
    assert artifact is not None
    assert "20150103" in prov.rejected_version_tags, (
        f"Expected 20150103 in rejected_version_tags, got {prov.rejected_version_tags}"
    )
    assert prov.chosen_version_tag == "20211030"


def test_selection_provenance_selector_mode_for_latest_cached() -> None:
    """SelectionProvenance reflects selector_mode for non-BENCH_COMPARABLE calls."""
    SHARED_DC = dt.date(2021, 11, 25)
    artifacts = [_make_artifact("2013/331", "20211030", SHARED_DC)]
    archive = _FixtureArchive({})  # archive not used for latest_cached_editorial
    _, prov = _select_from_cached_artifacts_with_info(
        artifacts,
        selector=ConsolidatedArtifactSelector.latest_cached_editorial(),
        lang="fin",
        archive=archive,
    )
    assert prov.selector_mode == "latest_cached_editorial"
    assert prov.tolerance_applied is False
    assert prov.rejected_version_tags == ()
