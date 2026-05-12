from __future__ import annotations

import io
from email.message import Message
from types import SimpleNamespace
from urllib.error import HTTPError

from lawvm.uk_legislation import uk_prefetch


class _FakeArchive:
    def __init__(self, existing: set[str] | None = None, marker: set[str] | None = None) -> None:
        self._existing = set(existing or [])
        self._markers = set(marker or [])
        self.store_calls: list[tuple[str, str]] = []

    def has(self, locator: str) -> bool:
        return locator in self._existing or locator in self._markers

    def store(self, locator: str, data: bytes, storage_class: str = "xml") -> None:
        self.store_calls.append((locator, storage_class))
        self._existing.add(locator)


def _make_structural_effects(*act_ids: str) -> list[SimpleNamespace]:
    return [SimpleNamespace(is_structural=True, affecting_act_id=act_id) for act_id in act_ids]


def _mark_and_fail_network_call(calls: list[str], _req: object | None = None, timeout: float = 30) -> None:
    calls.append("network")
    raise AssertionError("should not fetch")


def test_fetch_missing_for_statute_skips_permanently_missing_without_network(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/10"
    miss = uk_prefetch._missing_affecting_locator(act_id)
    archive = _FakeArchive(marker={miss})
    calls: list[str] = []

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(
        uk_prefetch.urllib.request,
        "urlopen",
        lambda _req, timeout=30: _mark_and_fail_network_call(calls, _req, timeout),
    )

    fetched, cached, errors = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (fetched, cached, errors) == (0, 1, 0)
    assert not calls
    assert archive.store_calls == []


def test_fetch_missing_for_statute_marks_404_as_missing_marker(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/11"
    miss = uk_prefetch._missing_affecting_locator(act_id)
    archive = _FakeArchive()

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)

    def _raise_404(_req, timeout=30):
        raise HTTPError(_req.full_url, 404, "missing", Message(), io.BytesIO(b""))

    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", _raise_404)

    first_report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)
    fetched, cached, errors = first_report

    assert (fetched, cached, errors) == (0, 1, 0)
    assert archive.has(miss)
    assert first_report.events[0]["rule_id"] == "uk_prefetch_affecting_act_permanent_missing"
    assert first_report.events[0]["reason"] == "http_404"
    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)
    assert report.events[0]["rule_id"] == "uk_prefetch_permanent_missing_marker_skipped"
    assert report.events[0]["affecting_act_id"] == act_id
    assert report.events[0]["blocking"] is False


def test_fetch_missing_for_statute_marks_410_as_missing_marker(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/12"
    miss = uk_prefetch._missing_affecting_locator(act_id)
    archive = _FakeArchive()

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)

    def _raise_410(_req, timeout=30):
        raise HTTPError(_req.full_url, 410, "gone", Message(), io.BytesIO(b""))

    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", _raise_410)

    fetched, cached, errors = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (fetched, cached, errors) == (0, 1, 0)
    assert archive.has(miss)


def test_fetch_missing_for_statute_records_http_error_event(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/13"
    archive = _FakeArchive()

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)

    def _raise_500(_req, timeout=30):
        raise HTTPError(_req.full_url, 500, "server error", Message(), io.BytesIO(b""))

    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", _raise_500)

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)
    fetched, cached, errors = report

    assert (fetched, cached, errors) == (0, 0, 1)
    assert report.to_dict()["error_count"] == 1
    assert report.events == (
        {
            "rule_id": "uk_prefetch_http_error",
            "phase": "acquisition",
            "family": "source_pathology",
            "statute_id": sid,
            "affecting_act_id": act_id,
            "locator": uk_prefetch._missing_affecting_locator(act_id),
            "url": "https://www.legislation.gov.uk/ukpga/1995/13/data.xml",
            "status": "error",
            "reason": "http_500",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
        },
    )
