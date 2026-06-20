"""Tests for U.S. federal locator scheme and archive-backed resolution.

No network: every test uses a tmp farchive, never the canonical corpus.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lawvm.us_federal.sources import (
    PlawMemberIdentity,
    content_digest,
    list_plaw_identities,
    list_plaw_locators,
    open_us_federal_farchive,
    parse_plaw_locator,
    parse_plaw_member_name,
    plaw_locator,
    plaw_locator_glob,
    read_plaw,
    read_plaw_locator,
    reserved_usc_release_point_locator,
    resolve_us_federal_farchive_path,
    usc_annual_locator,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "us_federal"


def test_plaw_locator_canonical_form() -> None:
    assert plaw_locator(118, 5) == "us://plaw/118/publ5.xml"
    assert plaw_locator(118, 5, kind="pvtl") == "us://plaw/118/pvtl5.xml"


def test_plaw_locator_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        plaw_locator(118, 5, kind="bogus")


def test_parse_plaw_member_name_public() -> None:
    identity = parse_plaw_member_name("PLAW-118publ5.xml")
    assert identity == PlawMemberIdentity(congress=118, number=5, kind="publ")
    assert identity is not None
    assert identity.is_public
    assert identity.locator == "us://plaw/118/publ5.xml"
    assert identity.member_name == "PLAW-118publ5.xml"
    assert identity.public_law_label == "Public Law 118-5"


def test_parse_plaw_member_name_private_and_invalid() -> None:
    private = parse_plaw_member_name("PLAW-118pvtl2.xml")
    assert private is not None
    assert not private.is_public
    assert private.public_law_label == "Private Law 118-2"
    assert parse_plaw_member_name("README.txt") is None
    assert parse_plaw_member_name("PLAW-118publ.xml") is None


def test_locator_round_trip_parse() -> None:
    locator = plaw_locator(117, 177)
    parsed = parse_plaw_locator(locator)
    assert parsed == PlawMemberIdentity(congress=117, number=177, kind="publ")
    assert parse_plaw_locator("us://plaw/bad") is None


def test_plaw_locator_glob() -> None:
    assert plaw_locator_glob() == "us://plaw/%/publ%.xml"
    assert plaw_locator_glob(118) == "us://plaw/118/publ%.xml"


def test_content_digest_is_sha256() -> None:
    import hashlib

    data = b"hello uslm"
    assert content_digest(data) == hashlib.sha256(data).hexdigest()


def test_resolve_path_honors_canonical_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LAWVM_US_FEDERAL_FARCHIVE_DB", raising=False)
    canon_root = tmp_path / "canon"
    monkeypatch.setenv("LAWVM_CANONICAL_DATA_ROOT", str(canon_root))
    path, rule = resolve_us_federal_farchive_path()
    assert path == canon_root / "data" / "us_federal.farchive"
    assert "us_federal.farchive" in rule


def test_resolve_path_honors_explicit_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    explicit = tmp_path / "explicit.farchive"
    monkeypatch.setenv("LAWVM_US_FEDERAL_FARCHIVE_DB", str(explicit))
    path, rule = resolve_us_federal_farchive_path()
    assert path == explicit
    assert "explicit" in rule


def test_open_us_federal_farchive_defaults_readonly_without_creating_missing_archive(tmp_path: Path) -> None:
    missing = tmp_path / "unused.farchive"

    try:
        archive = open_us_federal_farchive(missing)
    except Exception:
        pass
    else:
        archive.close()

    assert not missing.exists()


def test_archive_store_and_resolution_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "us_federal.farchive"
    archive = open_us_federal_farchive(db_path, allow_create=True)
    try:
        body = (FIXTURE_DIR / "PLAW-117publ177.xml").read_bytes()
        archive.store(
            plaw_locator(117, 177),
            body,
            storage_class="xml",
            metadata={"congress": "117", "law_number": "177"},
        )
        archive.store(
            plaw_locator(116, 52),
            (FIXTURE_DIR / "PLAW-116publ52.xml").read_bytes(),
            storage_class="xml",
            metadata={"congress": "116", "law_number": "52"},
        )

        # read_plaw round-trips the exact stored bytes
        assert read_plaw(archive, 117, 177) == body
        assert read_plaw_locator(archive, "us://plaw/117/publ177.xml") == body
        assert read_plaw(archive, 999, 999) is None

        # locators() globbing
        all_locs = list_plaw_locators(archive)
        assert set(all_locs) == {
            "us://plaw/117/publ177.xml",
            "us://plaw/116/publ52.xml",
        }
        assert list_plaw_locators(archive, 116) == ["us://plaw/116/publ52.xml"]

        # typed identities sorted by (congress, number)
        identities = list_plaw_identities(archive)
        assert [(i.congress, i.number) for i in identities] == [(116, 52), (117, 177)]
    finally:
        archive.close()


def test_usc_annual_locator_implemented_form() -> None:
    # The annual-edition htm namespace is now implemented (see import_usc).
    assert usc_annual_locator(2023, 11) == "us://usc/2023/title11.htm"
    assert usc_annual_locator(2024, 18) == "us://usc/2024/title18.htm"


def test_reserved_usc_release_point_locator_documented_not_acquired() -> None:
    # The OLRC release-point namespace remains reserved (geo-blocked); nothing
    # is fetched here.
    assert (
        reserved_usc_release_point_locator(119, 95, 11)
        == "us://usc/release/pl119-95/title11.xml"
    )
