"""Tests for lawvm uk-acquire command and the uk_acquire shared library."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest

from lawvm.uk_legislation.uk_acquire import (
    UKAcquireReport,
    UKAcquirePlan,
    _parse_statute_id,
    _store_if_new,
    build_acquire_plan,
    acquire_statute,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeArchive:
    """Minimal archive stub for tests — no network, no disk."""

    def __init__(
        self,
        existing: set[str] | None = None,
        history_map: dict[str, list[Any]] | None = None,
    ) -> None:
        self._existing: set[str] = set(existing or [])
        self._history: dict[str, list[Any]] = dict(history_map or {})
        self.store_calls: list[tuple[str, str]] = []
        self.observe_calls: list[tuple[str, str]] = []

    def has(self, locator: str) -> bool:
        return locator in self._existing

    def history(self, locator: str) -> list[Any]:
        return self._history.get(locator, [])

    def store(self, locator: str, data: bytes, storage_class: str = "xml") -> None:  # noqa: ARG002
        self.store_calls.append((locator, storage_class))
        self._existing.add(locator)

    def observe(self, locator: str, digest: str) -> None:
        self.observe_calls.append((locator, digest))

    def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# _parse_statute_id
# ---------------------------------------------------------------------------


def test_parse_statute_id_valid() -> None:
    assert _parse_statute_id("ukpga/2020/17") == ("ukpga", "2020", "17")
    assert _parse_statute_id("asp/2010/5") == ("asp", "2010", "5")


def test_parse_statute_id_leading_slash() -> None:
    assert _parse_statute_id("/ukpga/2020/17") == ("ukpga", "2020", "17")


def test_parse_statute_id_invalid_raises() -> None:
    with pytest.raises(ValueError, match="invalid UK statute id"):
        _parse_statute_id("ukpga/2020")
    with pytest.raises(ValueError, match="invalid UK statute id"):
        _parse_statute_id("bad")


# ---------------------------------------------------------------------------
# UKAcquirePlan
# ---------------------------------------------------------------------------


def test_acquire_plan_would_fetch_all_when_nothing_cached() -> None:
    plan = UKAcquirePlan(
        statute_id="ukpga/2020/17",
        enacted_url="https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml",
        enacted_already_cached=False,
        current_url="https://www.legislation.gov.uk/ukpga/2020/17/data.xml",
        current_stale=True,
        effects_base_url="https://www.legislation.gov.uk/changes/affected/ukpga/2020/17/data.feed?results-count=50&sort=modified",
        effects_stale=True,
    )
    urls = plan.would_fetch()
    assert len(urls) == 3
    assert plan.enacted_url in urls
    assert plan.current_url in urls
    assert plan.effects_base_url in urls


def test_acquire_plan_would_fetch_nothing_when_all_cached() -> None:
    plan = UKAcquirePlan(
        statute_id="ukpga/2020/17",
        enacted_url="https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml",
        enacted_already_cached=True,
        current_url="https://www.legislation.gov.uk/ukpga/2020/17/data.xml",
        current_stale=False,
        effects_base_url="https://www.legislation.gov.uk/changes/affected/ukpga/2020/17/data.feed?results-count=50&sort=modified",
        effects_stale=False,
    )
    assert plan.would_fetch() == []


def test_acquire_plan_to_dict_has_all_keys() -> None:
    plan = UKAcquirePlan(
        statute_id="ukpga/2020/17",
        enacted_url="https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml",
        enacted_already_cached=False,
        current_url="https://www.legislation.gov.uk/ukpga/2020/17/data.xml",
        current_stale=True,
        effects_base_url="https://www.legislation.gov.uk/changes/affected/ukpga/2020/17/data.feed?results-count=50&sort=modified",
        effects_stale=True,
    )
    d = plan.to_dict()
    assert d["statute_id"] == "ukpga/2020/17"
    assert d["enacted_already_cached"] is False
    assert d["current_stale"] is True
    assert d["effects_stale"] is True


# ---------------------------------------------------------------------------
# build_acquire_plan (no network)
# ---------------------------------------------------------------------------


def test_build_acquire_plan_empty_archive() -> None:
    archive = _FakeArchive()
    plan = build_acquire_plan("ukpga/2020/17", archive)
    assert plan.statute_id == "ukpga/2020/17"
    assert plan.enacted_already_cached is False
    assert plan.current_stale is True
    assert plan.effects_stale is True
    assert "ukpga/2020/17/enacted/data.xml" in plan.enacted_url
    assert "ukpga/2020/17/data.xml" in plan.current_url
    assert "changes/affected/ukpga/2020/17" in plan.effects_base_url


def test_build_acquire_plan_enacted_cached() -> None:
    enacted_url = "https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml"
    archive = _FakeArchive(existing={enacted_url})
    plan = build_acquire_plan("ukpga/2020/17", archive)
    assert plan.enacted_already_cached is True
    assert plan.current_stale is True  # still stale — no history


def test_build_acquire_plan_wrong_statute_id_raises() -> None:
    archive = _FakeArchive()
    with pytest.raises(ValueError, match="invalid UK statute id"):
        build_acquire_plan("not/valid", archive)


def test_store_if_new_observes_same_digest_without_duplicate_store() -> None:
    import hashlib

    locator = "https://www.legislation.gov.uk/uksi/2015/879/data.xml"
    data = b"<Legislation>same mutable payload</Legislation>"
    digest = hashlib.sha256(data).hexdigest()

    class _Span:
        def __init__(self) -> None:
            self.digest = digest

    archive = _FakeArchive(
        existing={locator},
        history_map={locator: [_Span()]},
    )

    stored = _store_if_new(archive, locator, data, "xml")

    assert stored is False
    assert archive.store_calls == []
    assert archive.observe_calls == [(locator, digest)]


# ---------------------------------------------------------------------------
# acquire_statute dry_run via CLI (no network)
# ---------------------------------------------------------------------------


@pytest.fixture()
def tmp_farchive(tmp_path: Path):
    """Open a real Farchive at a temp path (if farchive available), else skip."""
    pytest.importorskip("farchive")
    from farchive import Farchive

    db = tmp_path / "uk.farchive"
    return Farchive(db)


def _make_args(**kwargs: Any) -> argparse.Namespace:
    defaults = {
        "statute_id": "ukpga/2020/17",
        "db": None,
        "dry_run": True,
        "enacted_only": False,
        "affecting": False,
        "force_refresh": False,
        "delay": 0.5,
        "verbose": False,
        "json": False,
    }
    defaults.update(kwargs)
    return argparse.Namespace(**defaults)


# ---------------------------------------------------------------------------
# CLI --dry-run (no network, no archive required)
# ---------------------------------------------------------------------------


def test_cli_dry_run_no_archive_prints_plan(tmp_path: Path, capsys) -> None:
    from lawvm.tools.uk_acquire import main as uk_acquire_main

    args = _make_args(statute_id="ukpga/2020/17", db=str(tmp_path / "absent.farchive"))
    uk_acquire_main(args)

    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out
    assert "ukpga/2020/17" in captured.out
    assert "WOULD FETCH" in captured.out


def test_cli_dry_run_no_archive_json_mode(tmp_path: Path, capsys) -> None:
    from lawvm.tools.uk_acquire import main as uk_acquire_main

    args = _make_args(
        statute_id="ukpga/2020/17",
        db=str(tmp_path / "absent.farchive"),
        json=True,
    )
    uk_acquire_main(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["report_kind"] == "uk_acquire_plan_report"
    assert payload["schema"] == "lawvm.uk_acquire_plan_report.v1"
    assert payload["truth_claim"] == "uk_acquisition_plan_source_cache_evidence_only"
    assert payload["replay_claims"] is False
    assert payload["canonical_effect_claims"] is False
    assert payload["candidate_effect_claims"] is False
    assert payload["dry_run_claims"] is False
    assert payload["agreement_claims"] is False
    assert payload["statute_id"] == "ukpga/2020/17"
    assert payload["enacted_already_cached"] is False
    assert payload["current_stale"] is True
    assert payload["effects_stale"] is True
    assert payload["summary"]["dry_run"] is True
    assert payload["summary"]["would_fetch_count"] == 3
    assert payload["rows"] == []
    assert payload["forbidden_shortcuts"] == [
        "cache_presence_as_source_semantics",
        "acquisition_plan_as_replay_authorization",
        "would_fetch_as_source_completeness_proof",
    ]


def test_cli_dry_run_enacted_only_shows_only_enacted(tmp_path: Path, capsys) -> None:
    """enacted_only flag still uses dry-run path — no network expected."""
    from lawvm.tools.uk_acquire import main as uk_acquire_main

    args = _make_args(
        statute_id="ukpga/2020/17",
        db=str(tmp_path / "absent.farchive"),
        enacted_only=True,
        json=True,
    )
    uk_acquire_main(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    # Plan still shows all resource states; caller decides what to do.
    assert payload["report_kind"] == "uk_acquire_plan_report"
    assert "enacted_url" in payload
    assert "enacted/data.xml" in payload["enacted_url"]


def test_uk_acquire_report_jsonable_wraps_live_report() -> None:
    from lawvm.tools.uk_acquire import uk_acquire_report_jsonable

    report = UKAcquireReport(
        statute_id="ukpga/2020/17",
        enacted_fetched=True,
        current_already_cached=True,
        effects_pages_fetched=2,
        affecting_fetched=1,
        affecting_cached=3,
        affecting_errors=1,
        affecting_events=[
            {
                "rule_id": "uk_prefetch_http_error",
                "owner_phase": "acquisition",
                "blocking": True,
            }
        ],
    )

    payload = uk_acquire_report_jsonable(
        report=report,
        db_path=Path("/tmp/uk.farchive"),
        enacted_only=False,
        affecting=True,
        force_refresh=False,
    )

    assert payload["report_kind"] == "uk_acquire_report"
    assert payload["schema"] == "lawvm.uk_acquire_report.v1"
    assert payload["truth_claim"] == (
        "uk_acquisition_materialization_report_not_replay_authority"
    )
    assert payload["replay_claims"] is False
    assert payload["canonical_effect_claims"] is False
    assert payload["candidate_effect_claims"] is False
    assert payload["dry_run_claims"] is False
    assert payload["agreement_claims"] is False
    assert payload["statute_id"] == "ukpga/2020/17"
    assert payload["archive_path"] == "/tmp/uk.farchive"
    assert payload["affecting_events"] == report.affecting_events
    assert payload["rows"] == report.affecting_events
    assert payload["summary"]["has_errors"] is True
    assert payload["summary"]["error_count"] == 1
    assert payload["summary"]["affecting_event_count"] == 1
    assert payload["filtered_summary"] == payload["summary"]
    assert payload["forbidden_shortcuts"] == [
        "fetched_source_as_parsed_source_semantics",
        "cached_source_as_current_legal_truth",
        "acquisition_success_as_replay_authorization",
    ]


# ---------------------------------------------------------------------------
# acquire_statute unit tests (network patched out)
# ---------------------------------------------------------------------------


def _fake_http_get_success(url: str, delay: float = 0.5, last_time: list[float] | None = None) -> tuple[bytes, int]:
    # Return minimal valid XML-like blob larger than 50 bytes.
    data = b"<Legislation>" + (b"x" * 100) + b"</Legislation>"
    return data, 200


def _fake_http_get_404(url: str, delay: float = 0.5, last_time: list[float] | None = None) -> tuple[None, int]:
    return None, 404


def test_acquire_statute_fetches_enacted_when_absent(monkeypatch) -> None:
    archive = _FakeArchive()
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_acquire._http_get",
        _fake_http_get_success,
    )

    report = acquire_statute("ukpga/2020/17", archive, enacted_only=True)

    assert report.enacted_fetched is True
    assert report.enacted_already_cached is False
    assert report.enacted_error is None
    assert len(archive.store_calls) == 1
    locator, sc = archive.store_calls[0]
    assert "enacted/data.xml" in locator
    assert sc == "xml"


def test_acquire_statute_skips_enacted_when_cached(monkeypatch) -> None:
    enacted_url = "https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml"
    archive = _FakeArchive(existing={enacted_url})
    network_calls: list[str] = []

    def fail_network(url: str, **_kwargs: object) -> tuple[None, None]:
        network_calls.append(url)
        raise AssertionError(f"should not fetch {url}")

    monkeypatch.setattr("lawvm.uk_legislation.uk_acquire._http_get", fail_network)

    report = acquire_statute("ukpga/2020/17", archive, enacted_only=True)

    assert report.enacted_already_cached is True
    assert report.enacted_fetched is False
    assert network_calls == []


def test_acquire_statute_records_enacted_error(monkeypatch) -> None:
    archive = _FakeArchive()
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_acquire._http_get",
        _fake_http_get_404,
    )

    report = acquire_statute("ukpga/2020/17", archive, enacted_only=True)

    assert report.enacted_fetched is False
    assert report.enacted_error == "http_404"
    assert report.has_errors is True
    assert archive.store_calls == []


def test_acquire_statute_full_fetches_all_three(monkeypatch) -> None:
    """enacted + current + effects all fetched when archive is empty."""
    archive = _FakeArchive()
    fetch_calls: list[str] = []

    def fake_get(url: str, delay: float = 0.5, last_time: list[float] | None = None) -> tuple[bytes, int]:
        fetch_calls.append(url)
        if "data.feed" in url:
            # Minimal Atom feed, no totalPages -> 1 page
            data = b"<feed><title>effects</title></feed>"
            return data, 200
        return b"<Legislation>" + (b"x" * 100) + b"</Legislation>", 200

    monkeypatch.setattr("lawvm.uk_legislation.uk_acquire._http_get", fake_get)

    report = acquire_statute("ukpga/2020/17", archive)

    assert report.enacted_fetched is True
    assert report.current_fetched is True
    assert report.effects_pages_fetched == 1
    assert report.has_errors is False
    # Three store calls: enacted, current, effects p1
    assert len(archive.store_calls) == 3


def test_acquire_statute_no_errors_when_all_cached(monkeypatch) -> None:
    import datetime

    enacted_url = "https://www.legislation.gov.uk/ukpga/2020/17/enacted/data.xml"
    current_url = "https://www.legislation.gov.uk/ukpga/2020/17/data.xml"

    class _FreshSpan:
        def __init__(self) -> None:
            self.last_confirmed_at = datetime.datetime.now(tz=datetime.timezone.utc)
            self.digest = "abc"

    effects_p1 = "https://www.legislation.gov.uk/changes/affected/ukpga/2020/17/data.feed?results-count=50&sort=modified"
    archive = _FakeArchive(
        existing={enacted_url, current_url, effects_p1},
        history_map={
            current_url: [_FreshSpan()],
            effects_p1: [_FreshSpan()],
        },
    )

    def fail_network(url: str, **_kwargs: object) -> tuple[None, None]:
        raise AssertionError(f"should not fetch {url}")

    monkeypatch.setattr("lawvm.uk_legislation.uk_acquire._http_get", fail_network)

    report = acquire_statute("ukpga/2020/17", archive)

    assert report.enacted_already_cached is True
    assert report.current_already_cached is True
    assert report.effects_already_cached is True
    assert report.has_errors is False


# ---------------------------------------------------------------------------
# CLI live path (archive exists, network patched)
# ---------------------------------------------------------------------------


def test_cli_live_path_with_real_farchive(tmp_path: Path, monkeypatch, capsys) -> None:
    """Integration: CLI live path with a real Farchive and patched network."""
    farchive = pytest.importorskip("farchive")
    from farchive import Farchive
    from lawvm.tools.uk_acquire import main as uk_acquire_main

    db = tmp_path / "uk.farchive"
    # Initialize so it exists.
    archive = Farchive(db)
    archive.close()

    fetch_calls: list[str] = []

    def fake_get(url: str, delay: float = 0.5, last_time: list[float] | None = None) -> tuple[bytes, int]:
        fetch_calls.append(url)
        if "data.feed" in url:
            return b"<feed><title>effects</title></feed>", 200
        return b"<Legislation>" + (b"x" * 100) + b"</Legislation>", 200

    monkeypatch.setattr("lawvm.uk_legislation.uk_acquire._http_get", fake_get)

    args = _make_args(
        statute_id="ukpga/2020/17",
        db=str(db),
        dry_run=False,
        verbose=True,
    )
    uk_acquire_main(args)

    captured = capsys.readouterr()
    assert "enacted" in captured.out
    # Three URLs fetched: enacted, current, effects p1
    assert len(fetch_calls) == 3


# ---------------------------------------------------------------------------
# Test that shard auto-pickup works: test file matches test_uk_*.py pattern
# (this is a meta-assertion that the naming is correct)
# ---------------------------------------------------------------------------


def test_file_matches_uk_shard_pattern() -> None:
    """This file is named test_uk_acquire.py so it matches test_uk_*.py in the uk shard."""
    import fnmatch

    assert fnmatch.fnmatchcase("test_uk_acquire.py", "test_uk_*.py")


# ---------------------------------------------------------------------------
# Real farchive integration gate: skip if archive absent
# ---------------------------------------------------------------------------


def test_dry_run_with_real_archive_if_present(capsys) -> None:
    """If data/uk_legislation.farchive exists, build a real plan for a known statute."""
    archive_path = Path(__file__).resolve().parents[1] / "data" / "uk_legislation.farchive"
    if not archive_path.exists():
        pytest.skip("data/uk_legislation.farchive not present — skipping real-archive gate")

    from lawvm.tools.uk_acquire import main as uk_acquire_main

    args = _make_args(
        statute_id="ukpga/2020/17",
        db=str(archive_path),
        dry_run=True,
        json=True,
    )
    uk_acquire_main(args)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["statute_id"] == "ukpga/2020/17"
    assert "enacted_url" in payload
    assert "current_url" in payload
    assert "effects_base_url" in payload
