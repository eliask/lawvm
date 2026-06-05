from __future__ import annotations

import json
from pathlib import Path

from scripts import uk_effective_oracle_review as review


def _write_packets(path: Path, rows: list[dict[str, object]]) -> Path:
    path.write_text(json.dumps({"rows": rows}))
    return path


def _packet(
    *,
    statute_id: str = "ukpga/2011/22",
    kind: str,
    dotted: bool,
    markup: bool = False,
    notes: list[str],
) -> dict[str, object]:
    return {
        "statute_id": statute_id,
        "current_targets": ["section-4"],
        "current_page_status_witnesses": [
            {
                "current_page_url": f"https://www.legislation.gov.uk/{statute_id}/section/4",
                "no_known_outstanding_effects": True,
            }
        ],
        "current_timeline_xml_witnesses": [
            {
                "source_xml_url": (
                    f"https://www.legislation.gov.uk/{statute_id}/section/4/"
                    "2012-09-14/data.xml"
                ),
                "effective_oracle_kind": kind,
                "has_dotted_repeal_text": dotted,
                "has_repeal_markup": markup,
                "repeal_commentary_texts": notes,
            }
        ],
        "operation_evidence": [
            {
                "action": "repeal",
                "affected_provision": "s. 4",
                "affecting_source_id": statute_id,
                "affecting_provisions": "s. 10(2)",
                "source_preview": "Sections 4 to 9 are repealed.",
                "public_source_urls": [f"https://www.legislation.gov.uk/{statute_id}"],
            }
        ],
    }


def test_dotted_dated_current_xml_refutes_raw_oracle_divergence(tmp_path) -> None:
    path = _write_packets(
        tmp_path / "packets.json",
        [
            _packet(
                kind="dated_current_xml_repealed",
                dotted=True,
                notes=["S. 4 repealed by s. 10(2)"],
            )
        ],
    )

    row = review.load_reviews(path)[0]

    assert row.review_status == "refuted_by_dated_current_xml"
    assert row.dated_current_xml_repealed_count == 1
    assert row.no_known_outstanding_effects_count == 1
    assert row.simplest_public_check[0].startswith("Open current provision page:")
    assert row.agreement_residual["family"] == "oracle_editorial_pathology"
    assert row.agreement_residual["status"] == "frontier"
    assert row.agreement_residual["agreement_surface"] == (
        "whole_act_current_xml_vs_page_declared_current_timeline_xml"
    )


def test_repeal_note_without_dotted_text_is_likely_not_divergence(tmp_path) -> None:
    path = _write_packets(
        tmp_path / "packets.json",
        [
            _packet(
                kind="dated_current_xml_repeal_note_without_dotted_text",
                dotted=False,
                notes=["S. 53 repealed by S.I. 1994/1443"],
            )
        ],
    )

    row = review.load_reviews(path)[0]

    assert row.review_status == "likely_not_divergence_because_repeal_note_present"
    assert row.dated_current_xml_repeal_note_only_count == 1
    assert "preserves historical wording" in row.remaining_question
    assert row.agreement_residual["rule_id"] == (
        "uk_effective_oracle_likely_not_divergence_because_repeal_note_present"
    )


def test_no_dated_current_xml_marker_survives_as_plausible_divergence(tmp_path) -> None:
    path = _write_packets(
        tmp_path / "packets.json",
        [
            _packet(
                statute_id="eur/2020/2220",
                kind="dated_current_xml_no_repeal_marker",
                dotted=False,
                notes=[],
            )
        ],
    )

    rows = review.load_reviews(path)
    payload = json.loads(review._emit_json(rows))

    assert rows[0].review_status == "plausible_true_divergence"
    assert rows[0].dated_current_xml_no_marker_count == 1
    assert rows[0].agreement_residual["status"] == "residual"
    assert rows[0].agreement_residual["missing_proofs"] == [
        "savings_extent_or_revival_review",
        "editorial_policy_review",
    ]
    assert payload["report_kind"] == "uk_effective_oracle_review"
    assert payload["schema"] == "lawvm.uk_effective_oracle_review.v1"
    assert payload["agreement_claims"] is True
    assert payload["replay_claims"] is False
    assert payload["summary"]["plausible_true_divergence_count"] == 1
    assert payload["summary"]["agreement_residual_status_counts"] == {"residual": 1}


def test_repeal_markup_refutes_raw_oracle_divergence(tmp_path) -> None:
    path = _write_packets(
        tmp_path / "packets.json",
        [
            _packet(
                statute_id="eur/2020/2220",
                kind="dated_current_xml_repeal_markup",
                dotted=False,
                markup=True,
                notes=["Arts. 1-4 omitted by S.S.I. 2021/33"],
            )
        ],
    )

    row = review.load_reviews(path)[0]

    assert row.review_status == "refuted_by_dated_current_xml"
    assert row.dated_current_xml_repeal_markup_count == 1
    assert "explicit repeal markup" in row.refutation_reason
    assert row.agreement_residual["detail"]["dated_current_xml_repeal_markup_count"] == 1
