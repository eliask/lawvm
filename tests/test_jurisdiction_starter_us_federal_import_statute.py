"""Tests for U.S. federal Statutes-at-Large import (older public laws).

No network: a committed real-derived volume fixture (a 3-law slice of govinfo
``STATUTE-115`` USLM) is sliced + imported into a tmp farchive. The canonical
corpus is never touched.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree

from lawvm.us_federal.import_statute import (
    ACQUISITION_CHANNEL,
    import_statute_sources,
    import_statute_volume,
    iter_statute_plaws,
)
from lawvm.us_federal.sources import (
    open_us_federal_farchive,
    plaw_locator,
    read_plaw,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "us_federal"
SAMPLE_VOLUME = FIXTURE_DIR / "STATUTE-115-uslm-sample.xml"


def test_iter_statute_plaws_classifies_units() -> None:
    units = list(iter_statute_plaws(SAMPLE_VOLUME.read_bytes()))
    # Fixture holds two public laws (107-58, 107-53) and one private law.
    assert len(units) == 3
    publics = sorted((u.congress, u.number) for u in units if u.is_public)
    privates = [(u.congress, u.number) for u in units if not u.is_public]
    assert publics == [(107, 53), (107, 58)]
    assert privates == [(107, 1)]
    # Identity-bearing metadata survives the slice.
    pub58 = next(u for u in units if u.is_public and u.number == 58)
    assert pub58.locator == "us://plaw/107/publ58.xml"
    assert pub58.citable_as is not None and "Public Law" in pub58.citable_as


def test_import_dry_run_counts(tmp_path: Path) -> None:
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_statute_volume(SAMPLE_VOLUME, archive, dry_run=True)
    finally:
        archive.close()

    assert report.total_plaw_units == 3
    assert report.total_imported == 2  # two public laws
    assert report.total_skipped == 1  # one private law filtered
    assert report.total_errors == 0
    rule_ids = {row["rule_id"] for row in report.skipped_entries}
    assert "us_statute_import_private_law_filtered" in rule_ids
    # Dry-run wrote nothing.
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert list(archive2.locators("us://plaw/%")) == []
    finally:
        archive2.close()


def test_import_writes_standalone_parseable_slices(tmp_path: Path) -> None:
    db_path = tmp_path / "us_federal.farchive"
    report = import_statute_sources([str(SAMPLE_VOLUME)], db_path=db_path)

    assert report.total_imported == 2
    assert report.total_skipped == 1
    assert report.total_errors == 0
    assert report.congress_counts == {107: 2}

    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        body = read_plaw(archive, 107, 58)
        assert body is not None
        # Stored slice is independently well-formed USLM XML.
        root = etree.fromstring(body)
        assert root.tag == "{http://schemas.gpo.gov/xml/uslm}statuteSlice"
        assert root.find(".//{http://schemas.gpo.gov/xml/uslm}pLaw") is not None

        span = archive.resolve(plaw_locator(107, 58))
        assert span is not None and span.last_metadata is not None
        meta = span.last_metadata
        assert meta["acquisition_channel"] == ACQUISITION_CHANNEL
        assert meta["congress"] == "107"
        assert meta["law_number"] == "58"
        assert "approved_date" in meta

        # Private law not stored.
        assert read_plaw(archive, 107, 1) is None
    finally:
        archive.close()


def test_import_skip_existing_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "us_federal.farchive"
    first = import_statute_sources([str(SAMPLE_VOLUME)], db_path=db_path)
    assert first.total_imported == 2

    second = import_statute_sources(
        [str(SAMPLE_VOLUME)], db_path=db_path, skip_existing=True
    )
    assert second.total_imported == 0
    skip_rules = {row["rule_id"] for row in second.skipped_entries}
    assert "us_statute_import_existing_content_skipped" in skip_rules


_MISMATCH_VOLUME = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<statutesAtLarge xmlns="http://schemas.gpo.gov/xml/uslm"'
    ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
    "<main><publicLaws>"
    "<pLaw><meta>"
    "<dc:type>Public Law</dc:type><docNumber>78</docNumber>"
    "<citableAs>Public Law 111–78</citableAs>"
    "<approvedDate>2009-10-19</approvedDate>"
    # Defective <congress>: meta says 110 but the citation says 111.
    "<congress>110</congress><publicPrivate>public</publicPrivate>"
    "</meta></pLaw>"
    "</publicLaws></main></statutesAtLarge>"
).encode("utf-8")


def test_citation_overrides_defective_congress_meta(tmp_path: Path) -> None:
    units = list(iter_statute_plaws(_MISMATCH_VOLUME))
    assert len(units) == 1
    unit = units[0]
    # Citation (111-78) wins over the defective <congress>110</congress>.
    assert (unit.congress, unit.number) == (111, 78)
    assert unit.congress_mismatch == 110
    assert unit.locator == "us://plaw/111/publ78.xml"

    volume_path = tmp_path / "STATUTE-mismatch.xml"
    volume_path.write_bytes(_MISMATCH_VOLUME)
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_statute_volume(volume_path, archive)
        span = archive.resolve(plaw_locator(111, 78))
        assert span is not None and span.last_metadata is not None
        assert span.last_metadata["source_congress_meta"] == "110"
        # The misfiled 110-78 locator was never written from this unit.
        assert archive.resolve(plaw_locator(110, 78)) is None
    finally:
        archive.close()
    assert report.total_imported == 1
    assert any(
        row["rule_id"] == "us_statute_import_congress_meta_mismatch"
        for row in report.skipped_entries
    )


def test_unparsable_volume_is_typed_error(tmp_path: Path) -> None:
    bad = tmp_path / "STATUTE-bad.xml"
    bad.write_bytes(b"<statutesAtLarge><pLaw>unterminated")
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_statute_volume(bad, archive)
    finally:
        archive.close()
    assert report.total_errors == 1
    assert report.total_imported == 0
    assert any(
        row["rule_id"] == "us_statute_import_volume_unparsable"
        for row in report.skipped_entries
    )
