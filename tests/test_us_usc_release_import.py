"""Tests for the full OLRC USLM-USC release-point importer (XML-only).

Synthetic-only: a crafted USLM 1.0 title XML is written to a loose file and
ingested into a tmp farchive. No zip is involved (archive-first, XML-only) and
no network is hit.
"""

from __future__ import annotations

from pathlib import Path

from lawvm.us_federal.import_usc_release import (
    import_usc_release_dir,
    import_usc_release_xml,
    validate_release_point,
)
from lawvm.us_federal.sources import (
    UscUslmReleaseIdentity,
    open_us_federal_farchive,
    parse_usc_uslm_member_name,
    parse_usc_uslm_release_locator,
    usc_uslm_release_locator,
)

_USLM_TITLE = """<?xml version="1.0" encoding="UTF-8"?>
<uscDoc xmlns="http://xml.house.gov/schemas/uslm/1.0" identifier="/us/usc/t1">
<meta><docNumber>1</docNumber>
<property role="is-positive-law">yes</property></meta>
<main>
<title identifier="/us/usc/t1"><num value="1">Title 1—</num><heading>GENERAL</heading>
<section identifier="/us/usc/t1/s1"><num value="1">§ 1.</num>
<heading>Words</heading><content><p>text</p></content>
<sourceCredit>(July 30, 1947, ch. 388)</sourceCredit></section>
</title>
</main>
</uscDoc>
""".encode()


def test_validate_release_point_normalizes() -> None:
    assert validate_release_point("119-99") == "119-99"
    assert validate_release_point(" 119-099 ") == "119-99"


def test_validate_release_point_rejects_garbage() -> None:
    import pytest

    with pytest.raises(ValueError):
        validate_release_point("not-a-pin")


def test_member_name_and_locator_roundtrip() -> None:
    assert parse_usc_uslm_member_name("usc01.xml") == (1, "")
    assert parse_usc_uslm_member_name("usc05A.xml") == (5, "A")
    loc = usc_uslm_release_locator("119-99", 5, "A")
    assert loc == "us://usc-uslm/119-99/title5a.xml"
    ident = parse_usc_uslm_release_locator(loc)
    assert ident == UscUslmReleaseIdentity(release_point="119-99", title=5, suffix="a")


def test_import_one_title_xml_roundtrip(tmp_path: Path) -> None:
    src = tmp_path / "usc01.xml"
    src.write_bytes(_USLM_TITLE)
    db = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db, allow_create=True)
    try:
        report = import_usc_release_xml(
            src, archive, release_point="119-99"
        )
    finally:
        archive.close()
    assert report.total_imported == 1
    assert report.total_errors == 0
    locator = usc_uslm_release_locator("119-99", 1)
    assert locator in report.imported_locators

    ro = open_us_federal_farchive(db, readonly=True)
    try:
        span = ro.resolve(locator)
        assert span is not None
        assert ro.get(locator) == _USLM_TITLE
        assert span.last_metadata is not None
        assert span.last_metadata["release_point"] == "119-99"
        assert span.last_metadata["acquisition_channel"] == "olrc_full_release_point_uslm"
    finally:
        ro.close()


def test_import_dir_and_skip_existing(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "usc01.xml").write_bytes(_USLM_TITLE)
    (stage / "README.txt").write_text("not a member")  # ignored, not a usc member
    db = tmp_path / "us_federal.farchive"

    first = import_usc_release_dir(stage, release_point="119-99", db_path=db)
    assert first.total_imported == 1
    assert first.total_scanned == 1  # README is filtered before scan

    second = import_usc_release_dir(
        stage, release_point="119-99", db_path=db, skip_existing=True
    )
    assert second.total_imported == 0
    assert second.total_skipped == 1


def test_unrecognized_member_is_typed_skip(tmp_path: Path) -> None:
    src = tmp_path / "garbage.xml"
    src.write_bytes(b"<x/>")
    db = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db, allow_create=True)
    try:
        report = import_usc_release_xml(src, archive, release_point="119-99")
    finally:
        archive.close()
    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.skipped_entries[0]["rule_id"] == "us_usc_release_unrecognized_member"
