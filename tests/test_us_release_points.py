"""Tests for U.S. Code USLM release-point acquisition.

No network: synthetic USLM XML fixtures are built in-process from committed
bytes and used to exercise the zip-extraction, URL construction, and farchive
storage paths. The Wayback HTTP fetch is mocked — never hit the network.

Patterns mirror ``test_jurisdiction_starter_us_federal_import.py``:
- synth zip built via ``zipfile.ZipFile(...).writestr(...)``
- tmp farchive via ``open_us_federal_farchive(db_path, allow_create=True)``
- storage class + metadata round-trip via ``archive.resolve(locator)``
"""

from __future__ import annotations

import email.message
import io
import urllib.error
import zipfile
from pathlib import Path

import pytest

from lawvm.us_federal.import_release import (
    DEFAULT_WAYBACK_TIMESTAMP,
    OLRC_RELEASE_POINT_URL,
    ReleasePointIdentity,
    ReleasePointMemberNotFound,
    build_wayback_url,
    extract_release_point_xml,
    import_release_point,
    import_release_point_titles,
    import_release_point_zip_bytes,
)
from lawvm.us_federal.sources import (
    open_us_federal_farchive,
    reserved_usc_release_point_locator,
)


# Smallest valid USLM XML snippet for synthetic fixtures (USLM namespace
# matches the real GPO schema; content is illustrative, not legal text).
_USLM_SNIPPET = (
    b'<?xml version="1.0" encoding="UTF-8"?>\n'
    b'<usc xmlns="http://schemas.gpo.gov/xml/uslm" '
    b'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
    b'  <title identifier="/us/usc/t10">Title 10</title>\n'
    b'  <section identifier="/us/usc/t10/s1">\n'
    b'    <num>1</num>\n'
    b'    <heading>Definitions</heading>\n'
    b'    <subsection chg="NEW" identifier="/us/usc/t10/s1/1">\n'
    b'      <num>(a)</num>\n'
    b'      <content>synthetic USLM test fixture</content>\n'
    b'    </subsection>\n'
    b'  </section>\n'
    b"</usc>\n"
)


def _build_synthetic_release_zip(
    *,
    pl_congress: int = 113,
    pl_number: int = 100,
    title: int = 10,
    extra_titles: tuple[int, ...] = (),
    include_readme: bool = False,
) -> bytes:
    """Build a release-point zip in-memory with one or more USLM XML files."""
    members: list[tuple[str, bytes]] = []
    members.append(
        (
            f"xml_usc{title:02d}@{pl_congress}-{pl_number}.xml",
            _USLM_SNIPPET,
        )
    )
    for extra_title in extra_titles:
        members.append(
            (
                f"xml_usc{extra_title:02d}@{pl_congress}-{pl_number}.xml",
                _USLM_SNIPPET.replace(
                    b"/us/usc/t10", f"/us/usc/t{extra_title}".encode()
                ),
            )
        )
    if include_readme:
        members.append(("README.txt", b"OLRC release notes\n"))

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Wayback URL construction
# ---------------------------------------------------------------------------


def test_build_wayback_url_default_timestamp() -> None:
    """Default timestamp builds the canonical Wayback raw-bytes URL."""
    olrc = OLRC_RELEASE_POINT_URL.format(
        congress=113, pl_number=100, title=10
    )
    url = build_wayback_url(olrc)
    assert url == (
        "https://web.archive.org/web/2025id_/"
        "http://uscode.house.gov/download/releasepoints/us/pl/113/100/"
        "xml_usc10@113-100.zip"
    )


def test_build_wayback_url_explicit_timestamp() -> None:
    """An explicit timestamp survives into the URL."""
    olrc = OLRC_RELEASE_POINT_URL.format(
        congress=114, pl_number=10, title=5
    )
    url = build_wayback_url(olrc, timestamp="20241201")
    assert url.startswith("https://web.archive.org/web/20241201id_/")
    assert url.endswith("xml_usc05@114-10.zip")


def test_release_point_identity_urls() -> None:
    """ReleasePointIdentity composes OLRC + locator consistently."""
    ident = ReleasePointIdentity(congress=113, pl_number=100, title=10)
    assert ident.zip_filename == "xml_usc10@113-100.zip"
    assert ident.expected_zip_member_name == "xml_usc10@113-100.xml"
    assert ident.locator == "us://usc/release/pl113-100/title10.xml"
    assert ident.source_url == (
        "http://uscode.house.gov/download/releasepoints/us/pl/113/100/"
        "xml_usc10@113-100.zip"
    )
    assert ident.public_law_label == "Public Law 113-100"


def test_olrc_release_point_url_pads_title_to_two_digits() -> None:
    """Title 5 -> xml_usc05@..., Title 10 -> xml_usc10@... (OLRC convention)."""
    assert (
        OLRC_RELEASE_POINT_URL.format(congress=113, pl_number=100, title=5)
        .rsplit("/", 1)[-1]
        == "xml_usc05@113-100.zip"
    )
    assert (
        OLRC_RELEASE_POINT_URL.format(congress=113, pl_number=100, title=10)
        .rsplit("/", 1)[-1]
        == "xml_usc10@113-100.zip"
    )


# ---------------------------------------------------------------------------
# Zip extraction
# ---------------------------------------------------------------------------


def test_extract_release_point_xml_single_title() -> None:
    """A single-title zip extracts the requested title's XML bytes."""
    zip_bytes = _build_synthetic_release_zip(title=10)
    xml = extract_release_point_xml(zip_bytes, 10)
    assert xml == _USLM_SNIPPET
    assert b"http://schemas.gpo.gov/xml/uslm" in xml


def test_extract_release_point_xml_multi_title_returns_correct_one() -> None:
    """A multi-title zip returns only the XML matching the requested title."""
    zip_bytes = _build_synthetic_release_zip(
        title=10, extra_titles=(5, 18, 42)
    )
    xml_18 = extract_release_point_xml(zip_bytes, 18)
    # Title 18's fixture has its body rewritten to /us/usc/t18.
    assert b"/us/usc/t18\"" in xml_18

    xml_5 = extract_release_point_xml(zip_bytes, 5)
    assert b"/us/usc/t5\"" in xml_5

    xml_10 = extract_release_point_xml(zip_bytes, 10)
    assert b"/us/usc/t10\"" in xml_10


def test_extract_release_point_xml_missing_title_raises_typed_error() -> None:
    """Missing title is a typed exception, not a silent None / first-match.

    AGENTS.md §1.10: missing member must surface a named diagnostic with the
    available names embedded — no re-fetch needed to triage.
    """
    zip_bytes = _build_synthetic_release_zip(title=10, extra_titles=(5,))
    with pytest.raises(ReleasePointMemberNotFound) as exc_info:
        extract_release_point_xml(zip_bytes, 18)

    err = exc_info.value
    assert err.requested_title == 18
    # The diagnostic embeds the available members — triage without re-fetch.
    assert "xml_usc10@113-100.xml" in str(err)
    assert "xml_usc05@113-100.xml" in str(err)
    # And exposes them as a tuple field for programmatic triage.
    assert isinstance(err.available, tuple)
    assert "xml_usc05@113-100.xml" in err.available
    assert "xml_usc10@113-100.xml" in err.available


def test_extract_release_point_xml_with_readme_ignores_non_xml_members() -> None:
    """Non-XML members (README, etc.) do not interfere with title matching."""
    zip_bytes = _build_synthetic_release_zip(
        title=10, include_readme=True
    )
    xml = extract_release_point_xml(zip_bytes, 10)
    assert xml == _USLM_SNIPPET


def test_extract_release_point_xml_bad_zip_raises_bad_zip_file() -> None:
    """Bytes that aren't a zip raise BadZipFile (not a silent skip)."""
    with pytest.raises(zipfile.BadZipFile):
        extract_release_point_xml(b"<html>not a zip</html>", 10)


def test_extract_release_point_xml_handles_subdirectory_member_names() -> None:
    """OLRC occasionally nests XML under a directory; the matcher handles it.

    The matcher's ``(?:.*/)?`` prefix tolerates subdirectory prefixes.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xml_usc10@113-100.xml", _USLM_SNIPPET)
        zf.writestr("notes/README.txt", b"release notes\n")
    zip_bytes = buf.getvalue()
    assert extract_release_point_xml(zip_bytes, 10) == _USLM_SNIPPET


# ---------------------------------------------------------------------------
# Farchive storage (no network: zip bytes built in-process)
# ---------------------------------------------------------------------------


def test_import_release_point_zip_bytes_stores_correct_locator_and_metadata(
    tmp_path: Path,
) -> None:
    """Storing one title writes the canonical locator with typed metadata."""
    zip_bytes = _build_synthetic_release_zip(pl_congress=113, pl_number=100, title=10)
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_release_point_zip_bytes(
            113, 100, 10, zip_bytes, archive,
            source_label="synthetic://test",
        )
    finally:
        archive.close()

    assert report.total_scanned == 1
    assert report.total_imported == 1
    assert report.total_skipped == 0
    assert report.total_errors == 0
    assert report.bytes_stored == len(_USLM_SNIPPET)
    expected_locator = "us://usc/release/pl113-100/title10.xml"
    assert report.imported_locators == [expected_locator]

    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        stored = archive2.get(expected_locator)
        assert stored == _USLM_SNIPPET
        span = archive2.resolve(expected_locator)
        assert span is not None
        meta = span.last_metadata
        assert meta is not None
        assert meta["congress"] == "113"
        assert meta["pl_number"] == "100"
        assert meta["title"] == "10"
        assert meta["public_law"] == "Public Law 113-100"
        # The ACTUAL matched zip member name (synthetic fixture uses the full
        # form); the real OLRC zip stores "usc10.xml" inside but our matcher
        # tolerates both shapes — see test_extract_release_point_xml_olrc_observed_shape.
        assert meta["zip_member_name"] == "xml_usc10@113-100.xml"
        assert meta["zip_filename"] == "xml_usc10@113-100.zip"
        assert meta["acquisition_channel"] == "usc_release_point_wayback"
        assert meta["source_url"].endswith("xml_usc10@113-100.zip")
    finally:
        archive2.close()


def test_extract_release_point_xml_olrc_observed_shape() -> None:
    """The OLRC inner zip member is ``usc{NN}.xml`` (no PL suffix) — observed
    shape after fetching PL 113-100 Title 10 from Wayback.

    The matcher tolerates both the post-PL-observed simple form and the
    speculative full ``xml_uscNN@c-n.xml`` form.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("usc10.xml", _USLM_SNIPPET)
        zf.writestr("usc05.xml", _USLM_SNIPPET.replace(b"t10", b"t5"))
        zf.writestr("README.txt", b"OLRC release notes\n")
    zip_bytes = buf.getvalue()

    xml = extract_release_point_xml(zip_bytes, 10)
    assert xml == _USLM_SNIPPET

    xml_5 = extract_release_point_xml(zip_bytes, 5)
    assert b"/us/usc/t5\"" in xml_5


def test_import_release_point_zip_bytes_olrc_observed_member_name_in_metadata(
    tmp_path: Path,
) -> None:
    """When OLRC's actual member name is the short form (``usc10.xml``),
    the storage metadata's ``zip_member_name`` reflects the ACTUAL matched
    name, not the spec form. This honest provenance avoids lying about what
    was extracted (AGENTS.md §1.10).
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("usc10.xml", _USLM_SNIPPET)
    zip_bytes = buf.getvalue()

    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_release_point_zip_bytes(
            113, 100, 10, zip_bytes, archive
        )
    finally:
        archive.close()

    assert report.total_imported == 1
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        span = archive2.resolve("us://usc/release/pl113-100/title10.xml")
        assert span is not None
        meta = span.last_metadata
        assert meta is not None
        # The ACTUAL matched member name, not the speculative full form.
        assert meta["zip_member_name"] == "usc10.xml"
        assert meta["zip_filename"] == "xml_usc10@113-100.zip"
    finally:
        archive2.close()


def test_import_release_point_zip_bytes_missing_title_is_typed_skip(
    tmp_path: Path,
) -> None:
    """A zip without the requested title is a typed skip, not a silent drop."""
    zip_bytes = _build_synthetic_release_zip(title=10)  # only title 10
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_release_point_zip_bytes(
            113, 100, 18, zip_bytes, archive,  # request title 18 from a title-10 zip
        )
    finally:
        archive.close()

    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.total_errors == 1
    skip = report.skipped_entries[0]
    assert skip["rule_id"] == "us_release_point_member_not_found"
    assert skip["family"] == "source_pathology"
    assert skip["locator"] == "us://usc/release/pl113-100/title18.xml"
    assert skip["exception_type"] == "ReleasePointMemberNotFound"
    # Available members are embedded (No re-fetch needed to triage).
    assert "xml_usc10@113-100.xml" in skip["reason"]

    # Nothing persisted.
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert archive2.get("us://usc/release/pl113-100/title18.xml") is None
    finally:
        archive2.close()


def test_import_release_point_zip_bytes_skip_existing_is_idempotent(
    tmp_path: Path,
) -> None:
    """A second import with skip_existing=True short-circuits on identical digest."""
    zip_bytes = _build_synthetic_release_zip(title=10)
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        first = import_release_point_zip_bytes(113, 100, 10, zip_bytes, archive)
        second = import_release_point_zip_bytes(
            113, 100, 10, zip_bytes, archive, skip_existing=True
        )
    finally:
        archive.close()

    assert first.total_imported == 1
    assert second.total_imported == 0
    assert second.total_skipped == 1
    assert second.skipped_entries[0]["rule_id"] == "us_release_point_existing_content_skipped"


def test_import_release_point_zip_bytes_dry_run_writes_nothing(
    tmp_path: Path,
) -> None:
    """Dry-run reports an import but writes no locator to the archive."""
    zip_bytes = _build_synthetic_release_zip(title=10)
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_release_point_zip_bytes(
            113, 100, 10, zip_bytes, archive, dry_run=True
        )
    finally:
        archive.close()

    assert report.total_imported == 1
    assert report.imported_locators == [
        "us://usc/release/pl113-100/title10.xml"
    ]
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert archive2.get("us://usc/release/pl113-100/title10.xml") is None
    finally:
        archive2.close()


def test_import_release_point_zip_bytes_bad_zip_bytes_emits_typed_skip(
    tmp_path: Path,
) -> None:
    """Bytes that aren't a zip become a typed transport skip, not a crash."""
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_release_point_zip_bytes(
            113, 100, 10, b"<html>wayback 502 error page</html>", archive
        )
    finally:
        archive.close()

    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.total_errors == 1
    skip = report.skipped_entries[0]
    assert skip["rule_id"] == "us_release_point_zip_unreadable"
    assert skip["exception_type"] == "BadZipFile"
    assert skip["locator"] == "us://usc/release/pl113-100/title10.xml"


# ---------------------------------------------------------------------------
# End-to-end import_release_point (mocks fetch_release_point_zip — no network)
# ---------------------------------------------------------------------------


def test_import_release_point_end_to_end_via_mocked_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """import_release_point stores via a mocked fetch (no network call)."""

    synthetic_zip = _build_synthetic_release_zip(pl_congress=113, pl_number=100, title=10)

    def _fake_fetch(
        congress: int, pl_number: int, title: int, *, timestamp: str = DEFAULT_WAYBACK_TIMESTAMP
    ) -> bytes:
        assert congress == 113
        assert pl_number == 100
        assert title == 10
        assert timestamp == DEFAULT_WAYBACK_TIMESTAMP
        return synthetic_zip

    monkeypatch.setattr(
        "lawvm.us_federal.import_release.fetch_release_point_zip", _fake_fetch
    )

    db_path = tmp_path / "us_federal.farchive"
    report = import_release_point(db_path, 113, 100, 10)
    assert report.total_imported == 1
    assert report.total_errors == 0
    assert report.imported_locators == [
        "us://usc/release/pl113-100/title10.xml"
    ]

    # Stored content round-trips through a fresh archive handle.
    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert (
            archive.get("us://usc/release/pl113-100/title10.xml")
            == _USLM_SNIPPET
        )
    finally:
        archive.close()


def test_import_release_point_http_404_is_typed_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Wayback 404 (unarchived PL/title) is a typed skip, not a silent drop."""

    def _raise_404(
        congress: int, pl_number: int, title: int, *, timestamp: str = DEFAULT_WAYBACK_TIMESTAMP
    ) -> bytes:
        raise urllib.error.HTTPError(
            url="https://web.archive.org/web/2025id_/fake",
            code=404,
            msg="Not Found",
            hdrs=email.message.Message(),
            fp=None,
        )

    monkeypatch.setattr(
        "lawvm.us_federal.import_release.fetch_release_point_zip", _raise_404
    )

    db_path = tmp_path / "us_federal.farchive"
    report = import_release_point(db_path, 113, 100, 10)
    assert report.total_imported == 0
    assert report.total_skipped == 1
    assert report.total_errors == 1
    skip = report.skipped_entries[0]
    assert skip["rule_id"] == "us_release_point_http_error"
    assert skip["http_status"] == "404"
    assert "xml_usc10@113-100.zip" in skip["reason"]


def test_import_release_point_titles_adds_evidence_for_each_title(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """--all-titles mode produces one report row per title tried.

    404s for titles the PL didn't touch are typed skips — never silent
    disappearance (AGENTS.md §1.8).
    """
    # Only title 10 succeeds; titles 5 and 18 return 404.
    def _fake_fetch(
        congress: int, pl_number: int, title: int, *, timestamp: str = DEFAULT_WAYBACK_TIMESTAMP
    ) -> bytes:
        if title == 10:
            return _build_synthetic_release_zip(pl_congress=congress, pl_number=pl_number, title=10)
        raise urllib.error.HTTPError(
            url="https://web.archive.org/web/2025id_/fake",
            code=404,
            msg="Not Found",
            hdrs=email.message.Message(),
            fp=None,
        )

    monkeypatch.setattr(
        "lawvm.us_federal.import_release.fetch_release_point_zip", _fake_fetch
    )

    db_path = tmp_path / "us_federal.farchive"
    report = import_release_point_titles(
        db_path, 113, 100, titles=[5, 10, 18]
    )
    assert report.total_scanned == 3
    assert report.total_imported == 1
    assert report.total_skipped == 2  # title 5, title 18 (404)
    assert report.total_errors == 2
    assert report.imported_locators == [
        "us://usc/release/pl113-100/title10.xml"
    ]
    rule_ids = {row["rule_id"] for row in report.skipped_entries}
    assert rule_ids == {"us_release_point_http_error"}


# ---------------------------------------------------------------------------
# Never-mutate-existing-locator sanity (AGENTS.md §0)
# ---------------------------------------------------------------------------


def test_release_point_uses_reserved_locator_namespace() -> None:
    """Storage locator matches the reserved namespace in sources.py."""
    ident = ReleasePointIdentity(congress=119, pl_number=95, title=11)
    assert ident.locator == reserved_usc_release_point_locator(119, 95, 11)
    assert ident.locator == "us://usc/release/pl119-95/title11.xml"
