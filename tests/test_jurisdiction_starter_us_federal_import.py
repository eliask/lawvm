"""Tests for U.S. federal PLAW import + inventory.

No network: a synthetic zip is built in-process from committed XML fixtures and
imported into a tmp farchive. The canonical corpus is never touched.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from lawvm.us_federal.import_plaw import import_plaw_sources, import_plaw_zip
from lawvm.us_federal.inventory import build_inventory, inventory_us_federal
from lawvm.us_federal.sources import (
    open_us_federal_farchive,
    plaw_locator,
    read_plaw,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "us_federal"


def _build_fixture_zip(zip_path: Path, *, include_private: bool, include_junk: bool) -> None:
    """Build a govinfo-shaped PLAW zip (flat members) from committed fixtures."""
    members = {
        "PLAW-117publ177.xml": (FIXTURE_DIR / "PLAW-117publ177.xml").read_bytes(),
        "PLAW-116publ52.xml": (FIXTURE_DIR / "PLAW-116publ52.xml").read_bytes(),
        "PLAW-114publ89.xml": (FIXTURE_DIR / "PLAW-114publ89.xml").read_bytes(),
    }
    if include_private:
        # Synthetic private-law member (none exist in the public bulkdata zips);
        # exercises the public-only filter.
        members["PLAW-117pvtl1.xml"] = b"<pLaw>synthetic private law</pLaw>"
    if include_junk:
        members["README.txt"] = b"not a public law"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_import_dry_run_counts(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=True, include_junk=True)

    # Dry-run still needs an archive handle for resolve(); use a tmp one.
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_plaw_zip(zip_path, archive, dry_run=True)
    finally:
        archive.close()

    assert report.total_scanned == 5  # 3 publ + 1 pvtl + 1 junk
    assert report.total_imported == 3  # only public laws
    assert report.total_skipped == 2  # private + junk
    assert report.total_errors == 0
    rule_ids = {row["rule_id"] for row in report.skipped_entries}
    assert "us_plaw_import_private_law_filtered" in rule_ids
    assert "us_plaw_import_unrecognized_member" in rule_ids
    # Dry-run wrote nothing.
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert list(archive2.locators("us://plaw/%")) == []
    finally:
        archive2.close()


def test_import_sources_dry_run_does_not_create_missing_dest(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=False, include_junk=False)
    db_path = tmp_path / "unused"

    report = import_plaw_sources([zip_path], db_path=db_path, dry_run=True)

    assert report.total_imported == 3
    assert not db_path.exists()


def test_import_sources_rejects_extensionless_dest(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=False, include_junk=False)
    db_path = tmp_path / "unused"

    with pytest.raises(ValueError, match="extensionless farchive destination"):
        import_plaw_sources([zip_path], db_path=db_path)

    assert not db_path.exists()


def test_import_writes_and_round_trips(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=True, include_junk=False)
    db_path = tmp_path / "us_federal.farchive"

    report = import_plaw_sources([zip_path], db_path=db_path)
    assert report.total_imported == 3
    assert report.total_skipped == 1  # private filtered
    assert report.total_errors == 0
    assert report.bytes_raw > 0
    assert report.congress_counts == {114: 1, 116: 1, 117: 1}

    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        body = read_plaw(archive, 117, 177)
        assert body == (FIXTURE_DIR / "PLAW-117publ177.xml").read_bytes()
        # Stored metadata names the real govinfo member URL.
        span = archive.resolve(plaw_locator(117, 177))
        assert span is not None
        metadata = span.last_metadata
        assert metadata is not None
        assert "PLAW-117publ177.xml" in metadata["source_url"]
        assert metadata["congress"] == "117"
        # Private law not stored.
        assert read_plaw(archive, 117, 1) is None
    finally:
        archive.close()


def test_import_skip_existing_is_idempotent(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=False, include_junk=False)
    db_path = tmp_path / "us_federal.farchive"

    first = import_plaw_sources([zip_path], db_path=db_path)
    assert first.total_imported == 3

    second = import_plaw_sources([zip_path], db_path=db_path, skip_existing=True)
    assert second.total_imported == 0
    assert second.total_skipped == 3
    rule_ids = {row["rule_id"] for row in second.skipped_entries}
    assert rule_ids == {"us_plaw_import_existing_content_skipped"}


def test_inventory_over_imported_fixtures(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=False, include_junk=False)
    db_path = tmp_path / "us_federal.farchive"
    import_plaw_sources([zip_path], db_path=db_path)

    inv = inventory_us_federal(db_path=db_path)
    assert inv.total_units == 3
    assert inv.congresses == (114, 116, 117)
    assert inv.counts_per_congress == {114: 1, 116: 1, 117: 1}
    assert (117, 177) in inv.units

    payload = inv.to_dict()
    assert payload["total_units"] == 3
    assert payload["oracle_status"]["usc_oracle"] == "out_of_scope_blocked"
    # Honest report: makes no replay/coverage claim.
    assert "no replay" in payload["truth_claim"]


def test_inventory_congress_filter(tmp_path: Path) -> None:
    zip_path = tmp_path / "PLAW-mixed-public.zip"
    _build_fixture_zip(zip_path, include_private=False, include_junk=False)
    db_path = tmp_path / "us_federal.farchive"
    import_plaw_sources([zip_path], db_path=db_path)

    archive = open_us_federal_farchive(db_path, readonly=True)
    try:
        inv = build_inventory(archive, congress=116)
    finally:
        archive.close()
    assert inv.total_units == 1
    assert inv.units == ((116, 52),)


def test_duplicate_logical_locator_skipped(tmp_path: Path) -> None:
    # Two zip members map to the same canonical locator; later one is skipped.
    zip_path = tmp_path / "PLAW-dup-public.zip"
    body = (FIXTURE_DIR / "PLAW-117publ177.xml").read_bytes()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("PLAW-117publ177.xml", body)
        zf.writestr("sub/PLAW-117publ177.xml", b"<pLaw>different bytes</pLaw>")
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_plaw_zip(zip_path, archive)
    finally:
        archive.close()
    assert report.total_imported == 1
    assert report.total_skipped == 1
    assert any(
        row["rule_id"] == "us_plaw_import_duplicate_logical_locator"
        for row in report.skipped_entries
    )


def test_unreadable_zip_member_emits_typed_skip_not_silent_drop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Guard-liveness: a zip member that raises on read (truncated payload, CRC
    # mismatch, or other IO corruption) MUST surface as a typed rejection in
    # report.skipped_entries — never a bare stderr print + `continue` that drops
    # the lane silently (AGENTS.md §1.8/§1.10). Drives a known-violating input
    # through the FULL production import path (not a unit test of the catch).
    zip_path = tmp_path / "PLAW-corrupt-member.zip"
    body = (FIXTURE_DIR / "PLAW-116publ52.xml").read_bytes()
    member_name = "PLAW-116publ52.xml"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member_name, body)

    real_read = zipfile.ZipFile.read

    def _raise_on_plaw(self: zipfile.ZipFile, name: str) -> bytes:
        if name == member_name:
            raise zipfile.BadZipFile(f"Bad CRC-32 (synthetic corrupt member): {name}")
        return real_read(self, name)

    monkeypatch.setattr(zipfile.ZipFile, "read", _raise_on_plaw)

    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        report = import_plaw_zip(zip_path, archive)
    finally:
        archive.close()

    # The corrupt member is refused, not silently dropped: the report totals
    # charge it to both errors and skips (the lane-disappearance receipt counts).
    assert report.total_errors == 1
    assert report.total_skipped == 1
    assert report.total_imported == 0
    skip = next(
        row for row in report.skipped_entries
        if row["rule_id"] == "us_plaw_import_unreadable_zip_member"
    )
    assert skip["family"] == "transport_cleanup"
    assert skip["entry_name"] == member_name
    # The distinct named diagnostic MUST embed the underlying exception class so
    # triaging the acquisition gap does not require re-running extraction.
    assert "BadZipFile" in skip["reason"]
    # _record_import_skip merges detail keys into the record (not a nested dict).
    assert skip["exception_type"] == "BadZipFile"
    # The receiving archive did not persist the corrupt member (no silent
    # half-import that masquerades as a successful ingest).
    archive2 = open_us_federal_farchive(db_path, readonly=True)
    try:
        assert list(archive2.locators("us://plaw/%")) == []
    finally:
        archive2.close()
