from __future__ import annotations

import hashlib
import io
import json
from email.message import Message
from urllib.error import HTTPError

from lawvm.uk_legislation import uk_prefetch
from lawvm.uk_legislation import uk_amendment_replay
from lawvm.uk_legislation.uk_amendment_replay import UKEffectRecord, fetch_affecting_act


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


def _make_structural_effects(*act_ids: str) -> list[UKEffectRecord]:
    return [
        UKEffectRecord(
            effect_id=f"effect-{index}",
            effect_type="inserted",
            applied=True,
            requires_applied=False,
            modified="2025-01-01",
            affected_uri="/id/ukpga/2010/1",
            affected_class="UnitedKingdomPublicGeneralAct",
            affected_year="2010",
            affected_number="1",
            affected_provisions="s. 1",
            affecting_uri=f"/id/{act_id}",
            affecting_class="UnitedKingdomPublicGeneralAct",
            affecting_year=act_id.split("/")[1],
            affecting_number=act_id.split("/")[2],
            affecting_provisions="s. 2",
            affecting_title="Test Act",
        )
        for index, act_id in enumerate(act_ids, start=1)
    ]


def _make_nonstructural_candidate_effect(act_id: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="effect-nonstructural-candidate",
        effect_type="revoked",
        applied=True,
        requires_applied=False,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2010/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri=f"/id/{act_id}",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year=act_id.split("/")[1],
        affecting_number=act_id.split("/")[2],
        affecting_provisions="s. 2",
        affecting_title="Test Act",
    )


def test_fetch_affecting_act_writes_sha256_metadata(monkeypatch, tmp_path) -> None:
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return b"<Legislation>affecting</Legislation>"

    def fake_urlopen(req, timeout: int):
        assert timeout == 30
        assert req.full_url == "https://www.legislation.gov.uk/ukpga/2025/1/data.xml"
        return _FakeResponse()

    monkeypatch.setattr(uk_amendment_replay, "urlopen", fake_urlopen)
    out_path = tmp_path / "ukpga-2025-1.xml"

    assert fetch_affecting_act("ukpga/2025/1", out_path) is True

    assert out_path.read_bytes() == b"<Legislation>affecting</Legislation>"
    meta = json.loads(out_path.with_suffix(".xml.meta.json").read_text(encoding="utf-8"))
    assert meta == {
        "url": "https://www.legislation.gov.uk/ukpga/2025/1/data.xml",
        "bytes": len(b"<Legislation>affecting</Legislation>"),
        "sha256": hashlib.sha256(b"<Legislation>affecting</Legislation>").hexdigest(),
    }


def _make_commencement_effect(act_id: str) -> UKEffectRecord:
    return UKEffectRecord(
        effect_id="effect-commencement",
        effect_type="coming into force",
        applied=True,
        requires_applied=True,
        modified="2025-01-01",
        affected_uri="/id/ukpga/2010/1",
        affected_class="UnitedKingdomPublicGeneralAct",
        affected_year="2010",
        affected_number="1",
        affected_provisions="s. 1",
        affecting_uri=f"/id/{act_id}",
        affecting_class="UnitedKingdomPublicGeneralAct",
        affecting_year=act_id.split("/")[1],
        affecting_number=act_id.split("/")[2],
        affecting_provisions="art. 2",
        affecting_title="Commencement Order",
    )


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
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
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
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
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
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
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
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
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


def test_fetch_missing_for_statute_uses_shared_too_small_source_threshold(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/17"
    archive = _FakeArchive()
    data = b"x" * 99

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return data

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", lambda _req, timeout=30: _FakeResponse())

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (0, 0, 1)
    assert archive.store_calls == []
    assert report.events[0]["rule_id"] == "uk_prefetch_suspicious_small_response"
    assert report.events[0]["reason"] == "suspiciously_small_response:99"
    assert report.events[0]["blocking"] is True


def test_fetch_missing_for_statute_stores_shared_available_threshold_boundary(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/18"
    archive = _FakeArchive()
    data = b"x" * 100

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return data

        def geturl(self) -> str:
            return "https://www.legislation.gov.uk/ukpga/1995/18/data.xml"

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", lambda _req, timeout=30: _FakeResponse())

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (1, 0, 0)
    assert archive.store_calls == [("https://www.legislation.gov.uk/ukpga/1995/18/data.xml", "xml")]
    assert report.events[0]["rule_id"] == "uk_prefetch_affecting_act_fetched"
    assert report.events[0]["bytes"] == 100
    assert report.events[0]["blocking"] is False


def test_fetch_missing_for_statute_includes_supported_nonstructural_replay_families(
    monkeypatch,
) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/14"
    archive = _FakeArchive()

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: [_make_nonstructural_candidate_effect(act_id)],
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0, dry_run=True)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (1, 0, 0)
    assert report.events == (
        {
            "rule_id": "uk_prefetch_affecting_act_would_fetch",
            "phase": "acquisition",
            "family": "source_witness",
            "statute_id": sid,
            "affecting_act_id": act_id,
            "locator": "https://www.legislation.gov.uk/ukpga/1995/14/data.xml",
            "url": "https://www.legislation.gov.uk/ukpga/1995/14/data.xml",
            "status": "dry_run_would_fetch",
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )
    assert report.to_dict()["blocking_event_rule_counts"] == {}


def test_fetch_missing_for_statute_records_successful_fetch_source_witness(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/15"
    archive = _FakeArchive()
    data = b"<Legislation>" + (b"x" * 120) + b"</Legislation>"

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return data

        def geturl(self) -> str:
            return "https://www.legislation.gov.uk/ukpga/1995/15/data.xml?view=extent"

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)
    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", lambda _req, timeout=30: _FakeResponse())

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (1, 0, 0)
    assert archive.store_calls == [("https://www.legislation.gov.uk/ukpga/1995/15/data.xml", "xml")]
    assert report.events == (
        {
            "rule_id": "uk_prefetch_affecting_act_fetched",
            "phase": "acquisition",
            "family": "source_witness",
            "statute_id": sid,
            "affecting_act_id": act_id,
            "locator": "https://www.legislation.gov.uk/ukpga/1995/15/data.xml",
            "url": "https://www.legislation.gov.uk/ukpga/1995/15/data.xml",
            "final_url": "https://www.legislation.gov.uk/ukpga/1995/15/data.xml?view=extent",
            "http_status": 200,
            "status": "fetched",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )
    assert report.to_dict()["event_rule_counts"] == {"uk_prefetch_affecting_act_fetched": 1}
    assert report.to_dict()["blocking_event_rule_counts"] == {}


def test_fetch_missing_for_statute_records_cached_source_witness(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/16"
    archive = _FakeArchive()
    data = b"<Legislation>cached affecting source</Legislation>"

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: data,
    )
    monkeypatch.setattr(
        uk_prefetch.urllib.request,
        "urlopen",
        lambda _req, timeout=30: (_ for _ in ()).throw(AssertionError("cached source should not fetch")),
    )

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (0, 1, 0)
    assert archive.store_calls == []
    assert report.events == (
        {
            "rule_id": "uk_prefetch_affecting_act_cached",
            "phase": "acquisition",
            "family": "source_witness",
            "statute_id": sid,
            "affecting_act_id": act_id,
            "locator": "https://www.legislation.gov.uk/ukpga/1995/16/data.xml",
            "url": "https://www.legislation.gov.uk/ukpga/1995/16/data.xml",
            "status": "cached",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "blocking": False,
            "strict_disposition": "record",
            "quirks_disposition": "record",
        },
    )
    assert report.to_dict()["event_rule_counts"] == {"uk_prefetch_affecting_act_cached": 1}
    assert report.to_dict()["blocking_event_rule_counts"] == {}


def test_fetch_missing_for_statute_can_fetch_enacted_affecting_lane(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "ukpga/1995/16"
    archive = _FakeArchive()
    current_data = b"<Legislation>cached current affecting source</Legislation>"
    enacted_data = b"<Legislation>" + (b"fetched enacted affecting source" * 4) + b"</Legislation>"

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: _make_structural_effects(act_id),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        lambda _act_id, _archive: current_data,
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_enacted_xml_from_archive",
        lambda _act_id, _archive: None,
    )
    monkeypatch.setattr(uk_prefetch.time, "sleep", lambda _secs: None)

    class _FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def read(self) -> bytes:
            return enacted_data

        def geturl(self) -> str:
            return f"https://www.legislation.gov.uk/{act_id}/enacted/data.xml"

    def fake_urlopen(req, timeout=30):
        assert timeout == 30
        assert req.full_url == f"https://www.legislation.gov.uk/{act_id}/enacted/data.xml"
        return _FakeResponse()

    monkeypatch.setattr(uk_prefetch.urllib.request, "urlopen", fake_urlopen)

    report = uk_prefetch.fetch_missing_for_statute(
        sid,
        archive,
        delay=0.0,
        include_enacted=True,
    )

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (1, 1, 0)
    assert archive.store_calls == [
        (f"https://www.legislation.gov.uk/{act_id}/enacted/data.xml", "xml")
    ]
    assert [event["rule_id"] for event in report.events] == [
        "uk_prefetch_affecting_act_cached",
        "uk_prefetch_affecting_act_fetched",
    ]
    assert report.events[1]["url"] == f"https://www.legislation.gov.uk/{act_id}/enacted/data.xml"
    assert report.events[1]["sha256"] == hashlib.sha256(enacted_data).hexdigest()


def test_fetch_missing_for_statute_skips_unsupported_commencement_rows(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    act_id = "uksi/1995/14"
    archive = _FakeArchive()

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        lambda _sid, _archive, **_kwargs: [_make_commencement_effect(act_id)],
    )

    def fail_if_source_checked(_act_id, _archive):  # noqa: ANN001
        raise AssertionError("commencement rows should not be checked for affecting XML")

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.get_affecting_act_xml_from_archive",
        fail_if_source_checked,
    )

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0, dry_run=True)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (0, 0, 0)
    assert report.events == ()


def test_fetch_missing_for_statute_threads_feed_parse_rejections(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    archive = _FakeArchive()

    def fake_load_effects(_sid, _archive, *, parse_rejections_out=None):
        assert _sid == sid
        assert parse_rejections_out is not None
        parse_rejections_out.append(
            {
                "rule_id": "uk_effect_feed_xml_parse_rejected",
                "phase": "parse",
                "family": "source_pathology",
                "feed_locator": "https://example.test/data.feed",
                "blocking": True,
                "strict_disposition": "block",
                "quirks_disposition": "record",
            }
        )
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        fake_load_effects,
    )

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)

    assert (report.fetched_count, report.already_cached_count, report.error_count) == (0, 0, 1)
    assert report.events == (
        {
            "rule_id": "uk_effect_feed_xml_parse_rejected",
            "phase": "parse",
            "family": "source_pathology",
            "feed_locator": "https://example.test/data.feed",
            "blocking": True,
            "strict_disposition": "block",
            "quirks_disposition": "record",
            "statute_id": sid,
        },
    )


def test_prefetch_report_defaults_legacy_feed_events_to_blocking(monkeypatch) -> None:
    sid = "ukpga/2010/1"
    archive = _FakeArchive()

    def fake_load_effects(_sid, _archive, *, parse_rejections_out=None):
        assert _sid == sid
        assert parse_rejections_out is not None
        parse_rejections_out.extend(
            (
                {
                    "rule_id": "uk_effect_feed_xml_parse_rejected",
                    "phase": "parse",
                    "family": "source_pathology",
                    "feed_locator": "https://example.test/bad.feed",
                },
                {
                    "rule_id": "uk_effect_feed_pages_absent_recorded",
                    "phase": "acquisition",
                    "family": "source_pathology",
                    "feed_locator": "https://example.test/missing.feed",
                    "strict_disposition": "record",
                },
            )
        )
        return []

    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_amendment_replay.load_effects_for_statute_from_archive",
        fake_load_effects,
    )

    report = uk_prefetch.fetch_missing_for_statute(sid, archive, delay=0.0)
    payload = report.to_dict()

    assert report.error_count == 1
    assert payload["event_count"] == 2
    assert payload["blocking_event_count"] == 1
    assert payload["event_rule_counts"] == {
        "uk_effect_feed_pages_absent_recorded": 1,
        "uk_effect_feed_xml_parse_rejected": 1,
    }
    assert payload["blocking_event_rule_counts"] == {
        "uk_effect_feed_xml_parse_rejected": 1,
    }
