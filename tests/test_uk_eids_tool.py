from __future__ import annotations

from argparse import Namespace
import hashlib
import json

import pytest

from lawvm.tools import uk_eids
from lawvm.tools.uk_eids import (
    _eid_side_report_jsonable,
    _iter_prefixed_rows,
    _limit_rows_with_evidence,
    _source_state,
    uk_eids_report_jsonable,
)


def test_iter_prefixed_rows_filters_and_deduplicates() -> None:
    eid_map = {
        "n1": "section-72-4",
        "n2": "section-72-4-c",
        "n3": "section-72-4-c",
        "n4": "section-73-1",
    }
    text_map = {
        "section-72-4": "subsection 4",
        "section-72-4-c": "paragraph c",
        "section-73-1": "other",
    }

    rows = list(_iter_prefixed_rows(eid_map, text_map, prefix="section-72"))

    assert rows == [
        ("section-72-4", "subsection 4"),
        ("section-72-4-c", "paragraph c"),
    ]


def test_limit_rows_with_evidence_preserves_total_matches() -> None:
    rows = [
        ("section-72-1", "one"),
        ("section-72-2", "two"),
        ("section-72-3", "three"),
    ]

    assert _limit_rows_with_evidence(rows, limit=None) == (rows, 3, False)
    assert _limit_rows_with_evidence(rows, limit=2) == (rows[:2], 3, True)
    assert _limit_rows_with_evidence(rows, limit=3) == (rows, 3, False)


def test_source_state_distinguishes_absent_too_small_and_available() -> None:
    assert _source_state(None) == ("absent", 0)
    assert _source_state(b"") == ("too_small", 0)
    assert _source_state(b"<xml/>") == ("too_small", 6)
    assert _source_state(b"x" * 100) == ("available", 100)


def test_eid_side_report_jsonable_controls_text_lane() -> None:
    rows = [
        ("section-72-1", "one " * 100),
        ("section-72-2", "two"),
    ]

    without_text = _eid_side_report_jsonable(
        statute_id="ukpga/2000/1",
        prefix="section-72",
        side="oracle",
        source_url="https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        rows=rows,
        total_matches=3,
        truncated=True,
        missing=False,
        source_status="available",
        source_size=123,
        source_sha256="source-sha",
        show_text=False,
    )
    with_text = _eid_side_report_jsonable(
        statute_id="ukpga/2000/1",
        prefix="section-72",
        side="oracle",
        source_url="https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        rows=rows,
        total_matches=3,
        truncated=True,
        missing=False,
        source_status="available",
        source_size=123,
        source_sha256="source-sha",
        show_text=True,
    )

    assert without_text == {
        "statute_id": "ukpga/2000/1",
        "side": "oracle",
        "source_url": "https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        "source_status": "available",
        "source_size": 123,
        "source_sha256": "source-sha",
        "prefix": "section-72",
        "missing": False,
        "source_parse_failed": False,
        "source_parse_observation_count": 0,
        "source_parse_observation_rule_counts": {},
        "source_parse_observations": [],
        "source_parse_rejection_count": 0,
        "source_parse_rejection_rule_counts": {},
        "source_parse_rejections": [],
        "matches": 3,
        "emitted": 2,
        "truncated": True,
        "rows": [
            {"eid": "section-72-1"},
            {"eid": "section-72-2"},
        ],
    }
    assert with_text["rows"][0]["eid"] == "section-72-1"
    assert with_text["rows"][0]["text"].endswith("...")
    assert with_text["rows"][1] == {"eid": "section-72-2", "text": "two"}


def test_eid_side_report_jsonable_splits_source_parse_observations_from_rejections() -> None:
    report = _eid_side_report_jsonable(
        statute_id="ukpga/2000/1",
        prefix="section-72",
        side="oracle",
        source_url="https://www.legislation.gov.uk/ukpga/2000/1/data.xml",
        rows=[],
        total_matches=0,
        truncated=False,
        missing=True,
        source_status="available",
        source_size=123,
        source_sha256="source-sha",
        show_text=False,
        source_parse_rejections=[
            {
                "rule_id": "uk_oracle_xml_parse_recorded",
                "strict_disposition": "record",
            },
            {
                "rule_id": "uk_oracle_xml_parse_rejected",
                "blocking": True,
            },
        ],
    )

    assert report["source_parse_failed"] is True
    assert report["source_parse_observation_count"] == 2
    assert report["source_parse_observation_rule_counts"] == {
        "uk_oracle_xml_parse_recorded": 1,
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert report["source_parse_rejection_count"] == 1
    assert report["source_parse_rejection_rule_counts"] == {
        "uk_oracle_xml_parse_rejected": 1,
    }
    assert report["source_parse_rejections"] == [
        {
            "rule_id": "uk_oracle_xml_parse_rejected",
            "blocking": True,
        }
    ]


def test_uk_eids_report_jsonable_wraps_inspection_in_evidence_envelope() -> None:
    reports = [
        _eid_side_report_jsonable(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="base",
            source_url="https://example.test/ukpga/2000/1/enacted/data.xml",
            rows=[("section-72-1", "one")],
            total_matches=2,
            truncated=True,
            missing=False,
            source_status="available",
            source_size=123,
            source_sha256="base-sha",
            show_text=False,
        ),
        _eid_side_report_jsonable(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="oracle",
            source_url="https://example.test/ukpga/2000/1/data.xml",
            rows=[],
            total_matches=0,
            truncated=False,
            missing=True,
            source_status="absent",
            source_size=0,
            source_sha256="",
            show_text=False,
            source_parse_rejections=[
                {
                    "rule_id": "uk_oracle_xml_parse_rejected",
                    "blocking": True,
                }
            ],
        ),
    ]

    report = uk_eids_report_jsonable(
        statute_id="ukpga/2000/1",
        archive_path="/tmp/uk.farchive",
        prefix="section-72",
        side="both",
        show_text=False,
        reports=reports,
    )

    assert report["report_kind"] == "uk_eids_report"
    assert report["schema"] == "lawvm.uk_eids_report.v1"
    assert report["truth_claim"] == "uk_eid_source_inspection_evidence_only"
    assert report["replay_claims"] is False
    assert report["canonical_effect_claims"] is False
    assert report["candidate_effect_claims"] is False
    assert report["dry_run_claims"] is False
    assert report["agreement_claims"] is False
    assert report["archive_path"] == "/tmp/uk.farchive"
    assert report["prefix"] == "section-72"
    assert report["sides"] == reports
    assert report["rows"] == reports
    assert report["rows_truncated"] is True
    assert report["summary"] == {
        "statute_id": "ukpga/2000/1",
        "side_count": 2,
        "missing_side_count": 1,
        "available_side_count": 1,
        "source_status_counts": {"absent": 1, "available": 1},
        "source_parse_observation_count": 1,
        "source_parse_observation_rule_counts": {
            "uk_oracle_xml_parse_rejected": 1,
        },
        "source_parse_rejection_count": 1,
        "source_parse_rejection_rule_counts": {
            "uk_oracle_xml_parse_rejected": 1,
        },
        "match_count": 2,
        "emitted_count": 1,
        "truncated_side_count": 1,
    }
    assert report["filtered_summary"] == report["summary"]
    assert report["forbidden_shortcuts"] == [
        "eid_prefix_match_as_target_authority",
        "eid_inspection_as_replay_authorization",
        "text_preview_as_payload_identity",
    ]
    assert "mutation_boundary_proof" in report["next_promotion_requires"]


def test_uk_eids_json_provenance_preserves_limit_zero_matches(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
            return None

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda blob: {
            "eid_map": {
                "n1": "section-72-1",
                "n2": "section-72-2",
                "n3": "section-73",
            },
            "text_map": {
                "section-72-1": "one",
                "section-72-2": "two",
            },
        },
    )

    uk_eids.main(
        Namespace(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="both",
            limit=0,
            show_text=False,
            json=True,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["archive_path"] == str(db_path)
    assert payload["prefix"] == "section-72"
    assert [side["side"] for side in payload["sides"]] == ["base", "oracle"]
    base, oracle = payload["sides"]
    assert base["source_url"] == "https://example.test/ukpga/2000/1/enacted/data.xml"
    assert base["source_status"] == "available"
    assert base["source_size"] > 100
    assert base["source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert base["missing"] is False
    assert base["matches"] == 2
    assert base["emitted"] == 0
    assert base["truncated"] is True
    assert base["rows"] == []
    assert oracle["source_url"] == "https://example.test/ukpga/2000/1/data.xml"
    assert oracle["missing"] is True
    assert oracle["source_status"] == "absent"
    assert oracle["source_size"] == 0
    assert oracle["source_sha256"] == ""


def test_uk_eids_json_records_available_source_parse_failure(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url):
            return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"

    def fail_eid_extract(_blob: bytes):
        raise ValueError("bad source XML")

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        fail_eid_extract,
    )

    uk_eids.main(
        Namespace(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="base",
            limit=None,
            show_text=False,
            json=True,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    side = payload["sides"][0]
    assert side["side"] == "base"
    assert side["missing"] is True
    assert side["source_status"] == "available"
    assert side["source_sha256"] == hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert side["source_parse_failed"] is True
    assert side["source_parse_observation_count"] == 1
    assert side["source_parse_observation_rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
    }
    assert side["source_parse_rejection_count"] == 1
    assert side["source_parse_rejection_rule_counts"] == {
        "uk_enacted_xml_parse_rejected": 1,
    }
    rejection = side["source_parse_rejections"][0]
    assert rejection["blocking"] is True
    assert rejection["strict_disposition"] == "block"
    assert rejection["quirks_disposition"] == "record"
    assert rejection["side"] == "enacted"
    assert rejection["source_url"] == "https://example.test/ukpga/2000/1/enacted/data.xml"


def test_uk_eids_json_marks_too_small_source_separately(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            if str(url).endswith("/enacted/data.xml"):
                return b"<short/>"
            return None

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )

    uk_eids.main(
        Namespace(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="both",
            limit=None,
            show_text=False,
            json=True,
            db=str(db_path),
        )
    )

    payload = json.loads(capsys.readouterr().out)
    base, oracle = payload["sides"]
    assert base["missing"] is True
    assert base["source_status"] == "too_small"
    assert base["source_size"] == len(b"<short/>")
    assert base["source_sha256"] == hashlib.sha256(b"<short/>").hexdigest()
    assert oracle["missing"] is True
    assert oracle["source_status"] == "absent"
    assert oracle["source_sha256"] == ""


def test_uk_eids_text_output_includes_archive_and_source_urls(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, url):
            return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        lambda blob: {
            "eid_map": {"n1": "section-72-1"},
            "text_map": {"section-72-1": "one"},
        },
    )

    uk_eids.main(
        Namespace(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="both",
            limit=None,
            show_text=False,
            json=False,
            db=str(db_path),
        )
    )

    out = capsys.readouterr().out
    assert f"Archive: {db_path}" in out
    assert "source: https://example.test/ukpga/2000/1/enacted/data.xml" in out
    assert "source: https://example.test/ukpga/2000/1/data.xml" in out
    assert "source_status: available" in out
    source_sha256 = hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert f"source_sha256: {source_sha256}" in out


def test_uk_eids_text_reports_available_source_parse_failure(monkeypatch, tmp_path, capsys) -> None:
    import farchive

    db_path = tmp_path / "uk.farchive"
    db_path.write_bytes(b"placeholder")

    class FakeArchive:
        def __init__(self, path):
            self.path = path

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            return False

        def get(self, _url):
            return b"<Legislation>" + (b"x" * 120) + b"</Legislation>"

    def fail_eid_extract(_blob: bytes):
        raise ValueError("bad source XML")

    monkeypatch.setattr(farchive, "Farchive", FakeArchive)
    monkeypatch.setattr(
        "lawvm.tools.uk_replay._archive_url_for_statute",
        lambda statute_id, *, pit_date, enacted: (
            f"https://example.test/{statute_id}/enacted/data.xml"
            if enacted
            else f"https://example.test/{statute_id}/data.xml"
        ),
    )
    monkeypatch.setattr(
        "lawvm.uk_legislation.uk_grafter.extract_eid_map_bytes",
        fail_eid_extract,
    )

    uk_eids.main(
        Namespace(
            statute_id="ukpga/2000/1",
            prefix="section-72",
            side="oracle",
            limit=None,
            show_text=False,
            json=False,
            db=str(db_path),
        )
    )

    out = capsys.readouterr().out
    assert "(unavailable: source XML parse rejected)" in out
    source_sha256 = hashlib.sha256(
        b"<Legislation>" + (b"x" * 120) + b"</Legislation>"
    ).hexdigest()
    assert f"source_sha256: {source_sha256}" in out
    assert "source_parse_observation_rules: uk_oracle_xml_parse_rejected=1" in out
    assert "source_parse_rejection_rules: uk_oracle_xml_parse_rejected=1" in out


def test_uk_eids_rejects_negative_limit(capsys) -> None:
    args = Namespace(
        statute_id="ukpga/2000/1",
        prefix="section-1",
        side="both",
        limit=-1,
        show_text=False,
        db="does-not-matter.farchive",
    )

    with pytest.raises(SystemExit) as excinfo:
        uk_eids.main(args)

    assert excinfo.value.code == 2
    assert "--limit must be zero or a positive integer" in capsys.readouterr().err
