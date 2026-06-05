from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts import uk_public_divergence_packets as packets


def _write_json(path: Path, payload) -> Path:
    path.write_text(json.dumps(payload))
    return path


def test_packet_uses_supplement_operation_evidence_without_replay_claims(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/2010/37",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["section-3"],
                }
            ]
        },
    )
    supplement_path = _write_json(
        tmp_path / "supplement.json",
        [
            {
                "statute_id": "ukpga/2010/37",
                "retained_targets": ["section-3"],
                "current_urls": [
                    "https://www.legislation.gov.uk/ukpga/2010/37/section/3"
                ],
                "base_source": "https://www.legislation.gov.uk/ukpga/2010/37/enacted/data.xml",
                "oracle_source": "https://www.legislation.gov.uk/ukpga/2010/37/data.xml",
                "matched_ops": [
                    {
                        "action": "repeal",
                        "affected": "s. 3",
                        "source_statute": "uksi/2010/2996",
                        "affecting_provisions": "art. 2",
                        "effect_type": "repealed",
                        "effective_date": "2010-12-21",
                        "source_preview": "section 3 of the Superannuation Act 2010 is repealed",
                        "affecting_source_sha256": "abc123",
                        "auth": "replay_authorized",
                    }
                ],
            }
        ],
    )

    rows = packets.load_packets(candidates_path, supplement_path=supplement_path)
    payload = json.loads(packets._emit_json(rows))

    assert payload["truth_claim"] == "public_review_packet_not_legal_conclusion"
    assert payload["automated_consolidation_claims"] is False
    assert payload["source_truth_claims"] is False
    assert "review_lead_as_automatic_consolidation_change" in payload["summary"][
        "forbidden_shortcuts"
    ]
    row = payload["rows"][0]
    assert row["review_family"] == (
        "current_page_retains_apparently_repealed_or_omitted_provision"
    )
    assert row["current_page_urls"] == [
        "https://www.legislation.gov.uk/ukpga/2010/37/section/3"
    ]
    assert row["amending_source_urls"] == [
        "https://www.legislation.gov.uk/uksi/2010/2996",
        "https://www.legislation.gov.uk/uksi/2010/2996/article/2",
    ]
    assert row["operation_evidence"][0]["affecting_source_id"] == "uksi/2010/2996"
    assert "auth" not in row["operation_evidence"][0]
    assert row["missing_standalone_evidence"] == ["public_response_snapshots"]


def test_packet_without_supplement_marks_missing_operation_fragment(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "eur/2020/2220",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["article-6"],
                }
            ]
        },
    )

    row = packets.load_packets(candidates_path)[0]

    assert row.current_page_urls == (
        "https://www.legislation.gov.uk/eur/2020/2220/article/6",
    )
    assert row.operation_evidence == ()
    assert row.missing_standalone_evidence == (
        "amending_source_operation_fragment",
        "public_response_snapshots",
    )


def test_require_standalone_evidence_filters_before_limit(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/1920/50",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["/whole_act"],
                },
                {
                    "statute_id": "ukpga/2010/37",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["section-3"],
                },
            ]
        },
    )
    supplement_path = _write_json(
        tmp_path / "supplement.json",
        [
            {
                "statute_id": "ukpga/2010/37",
                "retained_targets": ["section-3"],
                "current_urls": [
                    "https://www.legislation.gov.uk/ukpga/2010/37/section/3"
                ],
                "matched_ops": [
                    {
                        "action": "repeal",
                        "affected": "s. 3",
                        "source_statute": "uksi/2010/2996",
                        "affecting_provisions": "art. 2",
                        "effect_type": "repealed",
                        "source_preview": "section 3 is repealed",
                    }
                ],
            }
        ],
    )

    def fetcher(url: str):
        return url, 200, "text/xml", f"<body>{url}</body>".encode()

    rows = packets.load_packets(
        candidates_path,
        supplement_path=supplement_path,
        fetch_public_snapshots=True,
        snapshot_dir=tmp_path / "snapshots",
        require_standalone_evidence=True,
        limit=1,
        fetcher=fetcher,
    )

    assert len(rows) == 1
    assert rows[0].statute_id == "ukpga/2010/37"
    assert rows[0].missing_standalone_evidence == ()


def test_si_paragraph_and_schedule_source_urls_are_exposed() -> None:
    urls = packets._public_source_urls("uksi/1994/1443", "para. 3(2) Sch. 4")

    assert urls == (
        "https://www.legislation.gov.uk/uksi/1994/1443",
        "https://www.legislation.gov.uk/uksi/1994/1443/schedule/4",
        "https://www.legislation.gov.uk/uksi/1994/1443/article/3",
    )


def test_fetch_public_snapshots_writes_digest_backed_bytes(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/2010/37",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["section-3"],
                }
            ]
        },
    )
    bodies = {
        "https://www.legislation.gov.uk/ukpga/2010/37/section/3": b"<html>section 3</html>",
        "https://www.legislation.gov.uk/ukpga/2010/37/enacted/data.xml": b"<xml>enacted</xml>",
        "https://www.legislation.gov.uk/ukpga/2010/37/data.xml": b"<xml>current</xml>",
    }

    def fetcher(url: str):
        return url + "/final", 200, "text/xml", bodies[url]

    rows = packets.load_packets(
        candidates_path,
        fetch_public_snapshots=True,
        snapshot_dir=tmp_path / "snapshots",
        fetcher=fetcher,
    )

    assert rows[0].missing_standalone_evidence == (
        "amending_source_operation_fragment",
    )
    snapshots = rows[0].public_snapshots
    assert [snapshot.role for snapshot in snapshots] == [
        "current_page",
        "enacted_source_xml",
        "current_source_xml",
    ]
    for snapshot in snapshots:
        body = bodies[snapshot.requested_url]
        assert snapshot.sha256 == hashlib.sha256(body).hexdigest()
        assert Path(snapshot.storage_path).read_bytes() == body


def test_current_page_status_witness_extracts_no_outstanding_effects(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/2011/22",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["section-4"],
                }
            ]
        },
    )
    html = b"""
      <div id="timelineData">
        <ul>
          <li><a href="/ukpga/2011/22/section/4/2012-02-14">old</a></li>
          <li class="currentVersion"><a href="/ukpga/2011/22/section/4/2012-09-14">current</a></li>
        </ul>
      </div></div>
      <div id="statusWarning" class="uptoDate">
        <p class="intro">There are currently no known outstanding effects for
        the London Olympic Games and Paralympic Games (Amendment) Act 2011,
        Section 4.<a href="#help">Help</a></p>
      </div>
    """
    bodies = {
        "https://www.legislation.gov.uk/ukpga/2011/22/section/4": html,
        "https://www.legislation.gov.uk/ukpga/2011/22/enacted/data.xml": b"<xml>enacted</xml>",
        "https://www.legislation.gov.uk/ukpga/2011/22/data.xml": b"<xml>current</xml>",
    }

    def fetcher(url: str):
        return url, 200, "text/html", bodies[url]

    row = packets.load_packets(
        candidates_path,
        fetch_public_snapshots=True,
        snapshot_dir=tmp_path / "snapshots",
        fetcher=fetcher,
    )[0]

    assert len(row.current_page_status_witnesses) == 1
    witness = row.current_page_status_witnesses[0]
    assert witness.status_warning_class == "uptoDate"
    assert witness.no_known_outstanding_effects is True
    assert witness.timeline_version_dates == ("2012-02-14", "2012-09-14")
    assert witness.current_timeline_date == "2012-09-14"
    assert witness.current_timeline_source_xml_url == (
        "https://www.legislation.gov.uk/ukpga/2011/22/section/4/2012-09-14/data.xml"
    )
    assert witness.current_timeline_source_xml_snapshot_sha256 == ""
    assert witness.current_timeline_source_xml_snapshot_path == ""
    assert witness.current_timeline_source_xml_byte_count == 0
    assert witness.snapshot_sha256 == hashlib.sha256(html).hexdigest()


def test_fetch_current_timeline_xml_writes_dated_oracle_snapshot(tmp_path) -> None:
    candidates_path = _write_json(
        tmp_path / "candidates.json",
        {
            "rows": [
                {
                    "statute_id": "ukpga/2011/22",
                    "candidate_family": "oracle_retains_source_repealed_state",
                    "confidence": "high",
                    "retained_repeal_targets": ["section-4"],
                }
            ]
        },
    )
    current_page = "https://www.legislation.gov.uk/ukpga/2011/22/section/4"
    dated_xml = f"{current_page}/2012-09-14/data.xml"
    bodies = {
        current_page: b"""
          <div id="timelineData">
            <ul><li class="currentVersion"><a href="/ukpga/2011/22/section/4/2012-09-14">current</a></li></ul>
          </div></div>
          <div id="statusWarning" class="uptoDate">
            <p class="intro">There are currently no known outstanding effects.</p>
          </div>
        """,
        "https://www.legislation.gov.uk/ukpga/2011/22/enacted/data.xml": b"<xml>enacted</xml>",
        "https://www.legislation.gov.uk/ukpga/2011/22/data.xml": b"<xml>current</xml>",
        dated_xml: b"<xml><Text>. . . repealed . . .</Text></xml>",
    }

    def fetcher(url: str):
        return url, 200, "text/xml", bodies[url]

    row = packets.load_packets(
        candidates_path,
        fetch_public_snapshots=True,
        fetch_current_timeline_xml=True,
        snapshot_dir=tmp_path / "snapshots",
        fetcher=fetcher,
    )[0]

    timeline_snapshots = [
        snapshot
        for snapshot in row.public_snapshots
        if snapshot.role == "current_timeline_source_xml"
    ]
    assert len(timeline_snapshots) == 1
    assert timeline_snapshots[0].requested_url == dated_xml
    assert timeline_snapshots[0].sha256 == hashlib.sha256(bodies[dated_xml]).hexdigest()
    assert Path(timeline_snapshots[0].storage_path).read_bytes() == bodies[dated_xml]
    assert row.current_page_status_witnesses[
        0
    ].current_timeline_source_xml_snapshot_sha256 == timeline_snapshots[0].sha256
    assert row.current_page_status_witnesses[
        0
    ].current_timeline_source_xml_snapshot_path == timeline_snapshots[0].storage_path
    payload = json.loads(packets._emit_json([row]))
    assert payload["summary"]["packets_with_current_page_status_witnesses"] == 1
    assert payload["summary"]["current_timeline_source_xml_snapshot_count"] == 1
