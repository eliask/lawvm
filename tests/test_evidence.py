from __future__ import annotations

import json
import pytest
import warnings
from types import SimpleNamespace

from lawvm.core.evidence_contracts import validate_corpus_finding_evidence_row
from lawvm.tools.classify_result import ClassifyResult
from lawvm.tools._evidence_helpers import (
    _cross_chapter_same_label_oracle_matches,
    _cross_chapter_same_label_replay_matches,
    _normalize_observation_streams,
    _same_chapter_oracle_range_matches,
)
from lawvm.tools._section_debug import render_node_text
from lawvm.tools.oracle_check import get_ground_truth_tree
from lawvm.tools.section_keys import extract_ir_sections, extract_oracle_sections
from tests.corpus_pin_helpers import pinned_replay
from lawvm.tools.evidence import (
    _compiler_observation_summary,
    _section_bisect_support,
    _build_section_claims,
    _build_proof_claims,
    _corrigendum_support_for_amendments,
    _primary_proof_tier,
    _same_chapter_alternative_replay_matches,
    _section_similarity,
    _oracle_text_temporary_source_id,
    build_evidence_bundle,
    build_oracle_proof_bundle,
    main,
    _review_bundles,
)
from lawvm.tools.evidence_claims import build_section_claims_typed
from lawvm.tools.evidence_statute_rules import build_proof_claims_typed


def _ground_truth_tree(statute_id: str):
    root = get_ground_truth_tree(statute_id)
    assert root is not None
    return root


def test_oracle_text_temporary_source_id_accepts_bare_citation_suffix() -> None:
    text = "21 b § oli väliaikaisesti voimassa 24.11.2021–30.1.2022 L 984/2021."

    assert _oracle_text_temporary_source_id(text) == "2021/984"


def test_build_proof_claims_marks_oracle_incorrect_for_html_topology_drift() -> None:
    claims = _build_proof_claims(
        section_results=[],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": ["4 a §"],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
    )

    assert any(
        claim["tier"] == "PROVED_ORACLE_INCORRECT"
        and claim["kind"] == "xml_html_topology_drift"
        for claim in claims
    )
    claim = next(
        claim for claim in claims
        if claim["tier"] == "PROVED_ORACLE_INCORRECT"
        and claim["kind"] == "xml_html_topology_drift"
    )
    assert claim["inference_rule"] == "html_xml_topology_drift_detected"
    assert claim["trigger_observations"][0]["source"] == "html_topology"
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_build_proof_claims_marks_html_xml_noncommensurable() -> None:
    claims = _build_proof_claims(
        section_results=[],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "duplicate_unscoped_oracle_labels:section:5",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
    )

    assert claims[0]["tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"


def test_build_proof_claims_marks_html_fetch_error_separately() -> None:
    claims = _build_proof_claims(
        section_results=[],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": ["4 a §"],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
            "html_error": "fetch/parse failed (https://example.test)",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
    )

    assert any(
        claim["kind"] == "html_fetch_error"
        and claim["tier"] == "UNRESOLVED"
        for claim in claims
    )
    assert not any(claim["kind"] == "xml_html_topology_drift" for claim in claims)


def test_build_proof_claims_marks_oracle_cutoff_version_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:3",
                "diagnosis": "UNKNOWN",
                "blame_source": "1998/643",
                "blame_title": "Test",
                "replay_text": "Replay text",
                "oracle_text": "Oracle text",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        oracle_suspect_detail="2009/1710 eff 2010-01-01 > cutoff 2009-12-29",
    )

    claim = next(
        claim
        for claim in claims
        if claim["tier"] == "PROVED_ORACLE_INCORRECT"
        and claim["kind"] == "oracle_metadata_inconsistency"
    )
    assert claim["inference_rule"] == "oracle_version_mid_conflicts_with_consolidated_cutoff"
    assert claim["trigger_observations"][0]["source"] == "oracle_version_gate"
    assert claim["support"]["suspect_detail"] == "2009/1710 eff 2010-01-01 > cutoff 2009-12-29"
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_build_proof_claims_marks_replay_bug_when_replay_diagnosis_remains() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:4",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2017/1153",
                "blame_title": "Test",
                "replay_text": "4 § on kumottu.",
                "oracle_text": "4 § Tässä on sisältöä.",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
    )

    assert any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    assert _primary_proof_tier(claims) == "PROVED_REPLAY_BUG"


def test_build_proof_claims_uses_selected_section_claims_to_gate_replay_claims() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:4",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2017/1153",
                "blame_title": "Test",
                "replay_text": "",
                "oracle_text": "4 § on kumottu P:llä 8.11.2013/415 v. 2014.",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_claims=[
            {
                "section": "section:4",
                "selected_kind": "oracle_section_stale",
                "selected_tier": "PROVED_ORACLE_INCORRECT",
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)


def test_build_proof_claims_demotes_preexisting_no_drop_replay_residue() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:17",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "2000/796",
                "blame_title": "Test",
                "replay_text": "17 § Replay",
                "oracle_text": "17 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:17",
                "baseline_score": 0.925,
                "first_bad_source": "1992/1624",
                "first_drop_source": "",
                "worst_drops": [],
                "preexisting_before_any_drop": True,
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    unresolved = next(claim for claim in claims if claim["kind"] == "UNRESOLVED.preexisting.baseline_residue")
    assert unresolved["tier"] == "UNRESOLVED"
    assert unresolved["trigger_observations"][0]["source"] == "section_bisect"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_keeps_true_replay_bug_when_other_sections_are_preexisting() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:16",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2017/350",
                "blame_title": "Test",
                "replay_text": "16 § Replay",
                "oracle_text": "16 § Oracle",
            },
            {
                "section": "section:17",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "2000/796",
                "blame_title": "Test",
                "replay_text": "17 § Replay",
                "oracle_text": "17 § Oracle",
            },
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:16",
                "baseline_score": 0.402,
                "first_bad_source": "1992/1624",
                "first_drop_source": "1996/1074",
                "worst_drops": [{"source_id": "1996/1074", "delta": -0.0028}],
                "preexisting_before_any_drop": False,
            },
            {
                "section": "section:17",
                "baseline_score": 0.925,
                "first_bad_source": "1992/1624",
                "first_drop_source": "",
                "worst_drops": [],
                "preexisting_before_any_drop": True,
            },
        ],
    )

    replay_claim = next(claim for claim in claims if claim["tier"] == "PROVED_REPLAY_BUG")
    assert [item["section"] for item in replay_claim["support"]["sections"]] == ["section:16"]
    assert any(claim["kind"] == "UNRESOLVED.preexisting.baseline_residue" for claim in claims)
    assert _primary_proof_tier(claims) == "PROVED_REPLAY_BUG"


def test_build_proof_claims_demotes_sections_improved_by_blamed_amendment() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:136",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2008/732",
                "blame_title": "Test",
                "replay_text": "136 § Replay",
                "oracle_text": "136 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:136",
                "baseline_score": 0.748,
                "first_bad_source": "1992/330",
                "first_drop_source": "2008/732",
                "worst_drops": [{"source_id": "2008/732", "delta": -0.01}],
                "preexisting_before_any_drop": False,
                "blame_source": "2008/732",
                "blame_trace_available": True,
                "blame_before_score": 0.748,
                "blame_after_score": 0.891,
                "blame_source_improved_or_equal": True,
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    improved = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.source_underdetermined.amendment_improves_section"
    )
    assert improved["tier"] == "UNRESOLVED"
    assert improved["trigger_observations"][0]["source"] == "section_trace"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_keeps_replay_bug_when_blamed_amendment_worsens_section() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:11",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2008/732",
                "blame_title": "Test",
                "replay_text": "11 § on kumottu.",
                "oracle_text": "11 § Tässä on sisältöä.",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:11",
                "baseline_score": 0.459,
                "first_bad_source": "1992/330",
                "first_drop_source": "2008/732",
                "worst_drops": [{"source_id": "2008/732", "delta": -0.357}],
                "preexisting_before_any_drop": False,
                "blame_source": "2008/732",
                "blame_trace_available": True,
                "blame_before_score": 0.459,
                "blame_after_score": 0.102,
                "blame_source_improved_or_equal": False,
            }
        ],
    )

    replay_claim = next(claim for claim in claims if claim["tier"] == "PROVED_REPLAY_BUG")
    assert replay_claim["support"]["sections"][0]["section"] == "section:11"
    assert _primary_proof_tier(claims) == "PROVED_REPLAY_BUG"


def test_build_proof_claims_demotes_repeal_only_without_payload_sections() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:1/section:11",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2008/732",
                "blame_title": "Test",
                "replay_text": "11 § on kumottu.",
                "oracle_text": "11 § Tässä on sisältöä.",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "chapter:1/section:11",
                "baseline_score": 0.459,
                "first_bad_source": "1992/330",
                "first_drop_source": "2008/732",
                "worst_drops": [{"source_id": "2008/732", "delta": -0.357}],
                "preexisting_before_any_drop": False,
                "blame_source": "2008/732",
                "blame_trace_available": True,
                "blame_before_score": 0.459,
                "blame_after_score": 0.102,
                "blame_source_improved_or_equal": False,
                "blame_body_has_section_payload": False,
                "blame_compiled_actions_for_section": ["repeal"],
                "blame_only_repeal_without_payload": True,
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    pathology = next(
        claim for claim in claims
        if claim["kind"] == "blamed_source_lacks_payload_support"
    )
    assert pathology["tier"] == "PROVED_SOURCE_PATHOLOGY"
    assert pathology["trigger_observations"][0]["source"] == "source_payload"
    assert _primary_proof_tier(claims) == "PROVED_SOURCE_PATHOLOGY"


def test_build_proof_claims_demotes_sections_whose_payload_matches_replay_better_than_oracle() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:35",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1993/688",
                "blame_title": "Test",
                "replay_text": "35 § Oppilaiden äidinkieli huomioidaan opetuksessa.",
                "oracle_text": (
                    "35 § Oppilaiden äidinkieli huomioidaan opetuksessa. "
                    "Koulussa, jossa saamenkielisille, romanikielisille ja vieraskielisille oppilaille."
                ),
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:35",
                "baseline_score": 0.685,
                "first_bad_source": "1993/688",
                "first_drop_source": "1993/688",
                "worst_drops": [{"source_id": "1993/688", "delta": -0.076}],
                "preexisting_before_any_drop": False,
                "blame_source": "1993/688",
                "blame_trace_available": True,
                "blame_before_score": 0.685,
                "blame_after_score": 0.924,
                "blame_source_improved_or_equal": True,
                "blame_body_has_section_payload": True,
                "blame_compiled_actions_for_section": ["replace"],
                "blame_only_repeal_without_payload": False,
                "blame_payload_vs_replay_score": 0.97,
                "blame_payload_vs_oracle_score": 0.82,
                "blame_payload_prefers_replay": True,
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    pathology = next(
        claim for claim in claims
        if claim["kind"] == "blamed_source_payload_prefers_replay"
    )
    assert pathology["tier"] == "PROVED_SOURCE_PATHOLOGY"
    assert pathology["trigger_observations"][0]["source"] == "source_payload"
    assert _primary_proof_tier(claims) == "PROVED_SOURCE_PATHOLOGY"


def test_build_proof_claims_demotes_sections_with_same_section_elaboration() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:35",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1993/805",
                "blame_title": "Test",
                "replay_text": "35 § Replay",
                "oracle_text": "35 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:35",
                "baseline_score": 0.71,
                "first_bad_source": "1993/805",
                "first_drop_source": "1993/805",
                "worst_drops": [{"source_id": "1993/805", "delta": -0.04}],
                "preexisting_before_any_drop": False,
                "blame_source": "1993/805",
                "blame_trace_available": True,
                "blame_before_score": 0.71,
                "blame_after_score": 0.63,
                "blame_source_improved_or_equal": False,
                "blame_body_has_section_payload": True,
                "blame_compiled_actions_for_section": ["replace", "insert"],
                "blame_only_repeal_without_payload": False,
                "blame_payload_vs_replay_score": None,
                "blame_payload_vs_oracle_score": None,
                "blame_payload_prefers_replay": False,
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    unresolved = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.source_underdetermined.elaboration_ambiguity"
    )
    assert unresolved["tier"] == "UNRESOLVED"
    assert unresolved["trigger_observations"][0]["source"] == "elaboration"
    assert unresolved["support"]["sections"][0]["apply_helpers"] == [
        "_apply_deterministic_subsection_op"
    ]
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_demotes_deterministic_sparse_multistep_stale_oracle_sections() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:3",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "2023/846",
                "blame_title": "Test",
                "replay_text": "Replay expanded",
                "oracle_text": "Oracle stale",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "section:3",
                "baseline_score": 1.0,
                "first_bad_source": "2021/1279",
                "first_drop_source": "2021/1279",
                "worst_drops": [
                    {"source_id": "2021/1279", "delta": -0.12},
                    {"source_id": "2022/465", "delta": -0.06},
                    {"source_id": "2023/846", "delta": -0.01},
                ],
                "preexisting_before_any_drop": False,
                "blame_source": "2023/846",
                "blame_trace_available": True,
                "blame_before_score": 0.81,
                "blame_after_score": 0.79,
                "blame_source_improved_or_equal": False,
                "blame_body_has_section_payload": True,
                "blame_compiled_actions_for_section": ["replace"],
                "blame_only_repeal_without_payload": False,
                "blame_payload_vs_replay_score": 0.31,
                "blame_payload_vs_oracle_score": 0.39,
                "blame_payload_prefers_replay": False,
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_labels": ["6"],
                "blame_sparse_leftover_count": 0,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
                "first_drop_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "first_drop_sparse_elaboration": True,
                "first_drop_sparse_slot_binding_count": 2,
                "first_drop_sparse_slot_binding_labels": ["4", "5"],
                "first_drop_sparse_leftover_count": 0,
                "first_drop_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    oracle_claim = next(
        claim for claim in claims
        if claim["tier"] == "PROVED_ORACLE_INCORRECT"
        and claim["kind"] == "oracle_section_stale"
    )
    assert oracle_claim["inference_rule"] == "oracle_stale_sections_detected"
    assert oracle_claim["support"]["sections"][0]["section"] == "section:3"
    assert oracle_claim["support"]["sections"][0]["first_drop_source"] == "2021/1279"
    assert oracle_claim["support"]["sections"][0]["drop_sources"] == [
        "2021/1279",
        "2022/465",
        "2023/846",
    ]
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_build_proof_claims_promotes_mixed_proved_and_empty_unverified_oracle_sections() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:3",
                "diagnosis": "EXTRA",
                "blame_source": "2020/177",
                "blame_title": "Test",
                "replay_text": "Replay expanded",
                "oracle_text": "",
                "oracle_content_absent": True,
            },
            {
                "section": "section:44",
                "diagnosis": "EXTRA",
                "blame_source": "2001/100",
                "blame_title": "Test",
                "replay_text": "44 § Replay text",
                "oracle_text": "",
                "oracle_content_absent": False,
            },
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_claims=[
            {
                "section": "section:3",
                "selected_kind": "oracle_section_stale",
                "selected_tier": "PROVED_ORACLE_INCORRECT",
                "selected_inference_rule": "oracle_content_absent_replay_has_content",
            },
            {
                "section": "section:44",
                "selected_kind": "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
                "selected_tier": "UNRESOLVED",
                "selected_inference_rule": "oracle_text_empty_but_contentAbsent_not_verified",
            },
        ],
    )

    claim = next(c for c in claims if c["kind"] == "oracle_body_empty_with_proved_sections")
    assert claim["tier"] == "PROVED_ORACLE_INCORRECT"
    assert (
        claim["inference_rule"]
        == "mixed_proved_and_empty_unverified_sections_promote_when_all_unresolved_are_empty_oracle"
    )
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_build_proof_claims_keeps_extraction_coverage_gap_unresolved() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:52",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2004/500",
                "blame_title": "Test",
                "replay_text": "52 § Replay",
                "oracle_text": "52 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_claims=[
            {
                "section": "section:52",
                "selected_kind": "UNRESOLVED.source_underdetermined.extraction_coverage_gap",
                "selected_tier": "UNRESOLVED",
                "selected_inference_rule": (
                    "statute_has_extraction_fallback_so_replay_divergence_"
                    "cannot_be_attributed_to_replay_logic"
                ),
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_section_claims_keeps_section_level_elaboration_candidate() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:35",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1993/805",
                "replay_text": "35 § Replay",
                "oracle_text": "35 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "section:35",
                "blame_source": "1993/805",
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
            }
        ],
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["section"] == "section:35"
    assert row["diagnosis"] == "REPLAY_MISSING"
    assert row["blame_source"] == "1993/805"
    assert row["similarity"] == pytest.approx(_section_similarity("35 § Replay", "35 § Oracle"))
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.elaboration_ambiguity"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == "blamed_amendment_has_same_section_elaboration_observation"
    assert row["candidate_count"] == 1
    assert row["candidate_kinds"] == ["UNRESOLVED.source_underdetermined.elaboration_ambiguity"]
    assert row["defeated_candidate_kinds"] == []
    assert row["defeated_candidates"] == []
    assert row["candidates"] == [
        {
            "tier": "UNRESOLVED",
            "kind": "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
            "inference_rule": "blamed_amendment_has_same_section_elaboration_observation",
            "observation_sources": ["elaboration", "apply_mutation", "section_bisect"],
            "support": {
                "observation_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "ELAB.SPLIT_SPARSE_OMISSION_CONSECUTIVE",
                ],
                "sparse_slot_binding_count": 0,
                "sparse_slot_binding_labels": [],
                "sparse_leftover_count": 0,
                "apply_helpers": ["_apply_deterministic_subsection_op"],
            },
        }
    ]


def test_build_section_claims_marks_deterministic_sparse_multistep_oracle_stale() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:3",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "2023/846",
                "replay_text": "Replay expanded",
                "oracle_text": "Oracle stale",
            }
        ],
        section_bisect=[
            {
                "section": "section:3",
                "baseline_score": 1.0,
                "first_bad_source": "2021/1279",
                "first_drop_source": "2021/1279",
                "worst_drops": [
                    {"source_id": "2021/1279", "delta": -0.12},
                    {"source_id": "2022/465", "delta": -0.06},
                    {"source_id": "2023/846", "delta": -0.01},
                ],
                "preexisting_before_any_drop": False,
                "blame_source": "2023/846",
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_labels": ["6"],
                "blame_sparse_leftover_count": 0,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
                "first_drop_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "first_drop_sparse_elaboration": True,
                "first_drop_sparse_slot_binding_count": 2,
                "first_drop_sparse_slot_binding_labels": ["4", "5"],
                "first_drop_sparse_leftover_count": 0,
                "first_drop_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
                "blame_payload_prefers_replay": False,
                "blame_only_repeal_without_payload": False,
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "oracle_section_stale"
    assert row["selected_tier"] == "PROVED_ORACLE_INCORRECT"
    assert (
        row["selected_inference_rule"]
        == "deterministic_sparse_same_section_drops_leave_oracle_stale"
    )
    assert row["candidate_kinds"] == [
        "oracle_section_stale",
        "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
        "UNRESOLVED.preexisting.elaboration_ambiguity",
    ]
    assert row["defeated_candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
        "UNRESOLVED.preexisting.elaboration_ambiguity",
    ]


def test_build_section_claims_marks_deterministic_payload_completeness_oracle_stale() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:9/section:79",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/295",
                "replay_text": "79 § Replay",
                "oracle_text": "79 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "chapter:9/section:79",
                "baseline_score": 0.837209,
                "first_bad_source": "1985/360",
                "first_drop_source": "1996/295",
                "worst_drops": [
                    {"source_id": "1996/295", "delta": -0.13835956917978454},
                ],
                "preexisting_before_any_drop": False,
                "blame_source": "1996/295",
                "blame_body_has_section_payload": True,
                "blame_payload_prefers_replay": False,
                "blame_only_repeal_without_payload": False,
                "blame_elaboration_kinds": ["ELAB.PAYLOAD_COMPLETENESS"],
                "blame_sparse_elaboration": False,
                "blame_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_labels": ["1"],
                "blame_sparse_leftover_count": 0,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
                "first_drop_elaboration_kinds": ["ELAB.PAYLOAD_COMPLETENESS"],
                "first_drop_sparse_elaboration": False,
                "first_drop_sparse_slot_binding_count": 1,
                "first_drop_sparse_slot_binding_labels": ["1"],
                "first_drop_sparse_leftover_count": 0,
                "first_drop_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
                "baseline_unmatched_oracle_subsections": None,
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "oracle_section_stale"
    assert row["selected_tier"] == "PROVED_ORACLE_INCORRECT"
    assert (
        row["selected_inference_rule"]
        == "deterministic_payload_completeness_same_section_drop_leaves_oracle_stale"
    )


def test_build_section_claims_marks_empty_oracle_text_unverified() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:44",
                "diagnosis": "EXTRA",
                "blame_source": "2001/100",
                "replay_text": "44 § Replay text",
                "oracle_text": "",
                "oracle_content_absent": False,
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == "oracle_text_empty_but_contentAbsent_not_verified"
    assert row["candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified"
    ]


def test_build_section_claims_marks_extraction_coverage_gap() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:52",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2004/500",
                "replay_text": "52 § Replay",
                "oracle_text": "52 § Oracle",
            }
        ],
        strict_fail_reasons=["PARSE.EXTRACTION_FALLBACK"],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.extraction_coverage_gap"
    assert row["selected_tier"] == "UNRESOLVED"
    assert (
        row["selected_inference_rule"]
        == "statute_has_extraction_fallback_so_replay_divergence_cannot_be_attributed_to_replay_logic"
    )
    assert row["candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.extraction_coverage_gap"
    ]


def test_build_section_claims_marks_section_strict_lineage_barrier() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:61",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2006/600",
                "replay_text": "61 § Replay",
                "oracle_text": "61 § Oracle",
            }
        ],
        section_strict_verdicts={
            "section:61": SimpleNamespace(
                is_strict_clean=False,
                amendment_id="2006/600",
                status="blocked",
                barrier_kinds={"PARSE.EXTRACTION_FALLBACK"},
                barrier_families={"source", "extraction"},
            )
        },
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.section_strict_lineage"
    assert row["selected_tier"] == "UNRESOLVED"
    assert (
        row["selected_inference_rule"]
        == "blamed_amendment_section_has_source_or_extraction_strict_barriers_so_replay_attribution_unsupported"
    )
    assert row["candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.section_strict_lineage"
    ]


def test_build_section_claims_marks_section_recovery_barriers() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:62",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2006/601",
                "replay_text": "62 § Replay",
                "oracle_text": "62 § Oracle",
            }
        ],
        section_strict_verdicts={
            "section:62": SimpleNamespace(
                is_strict_clean=False,
                amendment_id="2006/601",
                status="blocked",
                barrier_kinds={"RECOVERY.CONTEXTUAL_REBIND"},
                barrier_families={"recovery", "resolution"},
            )
        },
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.section_recovery_barriers"
    assert row["selected_tier"] == "UNRESOLVED"
    assert (
        row["selected_inference_rule"]
        == "blamed_amendment_section_required_recovery_paths_so_replay_divergence_may_be_recovery_artifact"
    )
    assert row["candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.section_recovery_barriers"
    ]


def test_build_section_claims_records_defeated_candidates() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:35",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1993/805",
                "replay_text": "35 § Replay",
                "oracle_text": "35 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "section:35",
                "preexisting_before_any_drop": True,
                "first_bad_source": "1993/805",
                "baseline_score": 0.71,
                "blame_source": "1993/805",
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_apply_helpers_for_section": ["_apply_deterministic_subsection_op"],
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.preexisting.baseline_residue"
    assert row["selected_inference_rule"] == "replay_residue_predates_any_amendment_drop"
    assert row["candidate_count"] == 2
    assert row["candidate_kinds"] == [
        "UNRESOLVED.preexisting.baseline_residue",
        "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
    ]
    assert row["defeated_candidate_kinds"] == [
        "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
    ]
    assert row["defeated_candidates"] == [
        {
            "kind": "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
            "tier": "UNRESOLVED",
            "inference_rule": "blamed_amendment_has_same_section_elaboration_observation",
            "defeated_by_kind": "UNRESOLVED.preexisting.baseline_residue",
            "defeated_by_inference_rule": "replay_residue_predates_any_amendment_drop",
            "defeated_by_observation_sources": ["section_bisect"],
        }
    ]


def test_build_section_claims_uses_sparse_leftovers_as_elaboration_ambiguity_signal() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "section:35",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1993/805",
                "replay_text": "35 § Replay",
                "oracle_text": "35 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "section:35",
                "blame_source": "1993/805",
                "blame_elaboration_kinds": [],
                "blame_sparse_elaboration": True,
                "blame_sparse_leftover_count": 2,
                "blame_apply_helpers_for_section": [],
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.source_underdetermined.elaboration_ambiguity"
    assert row["selected_inference_rule"] == "blamed_amendment_has_same_section_sparse_leftovers"
    assert row["candidates"] == [
        {
            "tier": "UNRESOLVED",
            "kind": "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
            "inference_rule": "blamed_amendment_has_same_section_sparse_leftovers",
            "observation_sources": ["elaboration", "apply_mutation", "section_bisect"],
            "support": {
                "observation_kinds": [],
                "sparse_slot_binding_count": 0,
                "sparse_slot_binding_labels": [],
                "sparse_leftover_count": 2,
                "apply_helpers": [],
            },
        }
    ]


def test_same_chapter_alternative_replay_matches_finds_better_neighbor() -> None:
    got = _same_chapter_alternative_replay_matches(
        [
            {
                "section": "chapter:8/section:38",
                "replay_text": "38 § Vanha pakokaasupäästöjen tarkastus.",
                "oracle_text": "41 § Katsastuksessa hyväksyminen ja hylkääminen.",
            }
        ],
        {
            "chapter:8/section:38": "38 § Vanha pakokaasupäästöjen tarkastus.",
            "chapter:8/section:41": "41 § Katsastuksessa hyväksyminen ja hylkääminen.",
            "chapter:7/section:41": "41 § Eri luvun pykälä ei saa voittaa.",
        },
    )

    assert got["chapter:8/section:38"]["best_replay_section"] == "chapter:8/section:41"
    assert got["chapter:8/section:38"]["best_replay_score"] == pytest.approx(
        round(
            _section_similarity(
                "41 § Katsastuksessa hyväksyminen ja hylkääminen.",
                "41 § Katsastuksessa hyväksyminen ja hylkääminen.",
            ),
            6,
        )
    )
    assert got["chapter:8/section:38"]["same_section_score"] == pytest.approx(
        round(
            _section_similarity(
                "38 § Vanha pakokaasupäästöjen tarkastus.",
                "41 § Katsastuksessa hyväksyminen ja hylkääminen.",
            ),
            6,
        )
    )


def test_build_section_claims_surfaces_alternative_replay_match() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:8/section:38",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1995/190",
                "replay_text": "38 § Replay",
                "oracle_text": "38 § Oracle",
            }
        ],
        section_bisect=[],
        alternative_replay_matches={
            "chapter:8/section:38": {
                "best_replay_section": "chapter:8/section:41",
                "best_replay_score": 0.751523,
                "same_section_score": 0.362177,
            }
        },
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.address_projection.same_chapter_replay_drift"
    assert row["alternative_replay_match"] == {
        "best_replay_section": "chapter:8/section:41",
        "best_replay_score": 0.751523,
        "same_section_score": 0.362177,
    }
    assert row["candidates"] == [
        {
            "tier": "UNRESOLVED",
            "kind": "UNRESOLVED.address_projection.same_chapter_replay_drift",
            "inference_rule": "same_chapter_replay_section_matches_oracle_better_than_same_number_section",
            "observation_sources": ["oracle_check"],
            "support": {
                "best_replay_section": "chapter:8/section:41",
                "best_replay_score": 0.751523,
                "same_section_score": 0.362177,
            },
        }
    ]


def test_build_section_claims_demotes_cross_chapter_oracle_section_drift() -> None:
    """Cross-chapter match with score >= 0.95 now classifies as NONCOMMENSURABLE."""
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:19/section:146",
                "diagnosis": "EXTRA",
                "blame_source": "2017/1001",
                "replay_text": "146 § Replay",
                "oracle_text": "",
            }
        ],
        cross_chapter_oracle_matches={
            "chapter:19/section:146": {
                "oracle_section": "section:146",
                "oracle_section_score": 1.0,
                "same_section_score": 0.0,
            }
        },
    )

    row = rows[0]
    assert row["selected_kind"] == "address_relocation_cross_chapter_exact"
    assert row["selected_tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert row["selected_inference_rule"] == (
        "oracle_matches_same_label_section_in_different_chapter"
    )
    assert row["cross_chapter_oracle_match"] == {
        "oracle_section": "section:146",
        "oracle_section_score": 1.0,
        "same_section_score": 0.0,
    }


def test_build_section_claims_cross_chapter_exact_noncommensurable() -> None:
    """Cross-chapter match with score 0.98 -> PROVED_HTML_XML_NONCOMMENSURABLE."""
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:5/section:40",
                "diagnosis": "EXTRA",
                "blame_source": "2020/500",
                "replay_text": "40 section text that is nearly identical",
                "oracle_text": "",
            }
        ],
        cross_chapter_oracle_matches={
            "chapter:5/section:40": {
                "oracle_section": "section:40",
                "oracle_section_score": 0.98,
                "same_section_score": 0.10,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert row["selected_kind"] == "address_relocation_cross_chapter_exact"
    assert row["selected_inference_rule"] == (
        "oracle_matches_same_label_section_in_different_chapter"
    )


def test_build_section_claims_cross_chapter_low_score_stays_unresolved() -> None:
    """Cross-chapter match with score 0.60 -> UNRESOLVED (unchanged)."""
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:5/section:40",
                "diagnosis": "EXTRA",
                "blame_source": "2020/500",
                "replay_text": "40 section text that differs substantially",
                "oracle_text": "",
            }
        ],
        cross_chapter_oracle_matches={
            "chapter:5/section:40": {
                "oracle_section": "section:40",
                "oracle_section_score": 0.60,
                "same_section_score": 0.10,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_kind"] == "UNRESOLVED.address_projection.cross_chapter_oracle_drift"
    assert row["selected_inference_rule"] == (
        "oracle_matches_same_label_section_in_different_chapter"
    )


def test_cross_chapter_oracle_match_helper_keeps_ambiguous_near_tie_candidates() -> None:
    matches = _cross_chapter_same_label_oracle_matches(
        [
            {
                "section": "chapter:31/section:243",
                "diagnosis": "EXTRA",
                "replay_text": "same relocation text",
                "oracle_text": "",
            }
        ],
        {
            "chapter:26/section:243": "same relocation text",
            "chapter:1/section:243": "same relocation txt",
        },
    )

    row = matches["chapter:31/section:243"]
    assert row["oracle_section"] == "chapter:26/section:243"
    assert row["runner_up_oracle_section"] == "chapter:1/section:243"
    assert float(row["runner_up_oracle_section_score"]) > 0.0


def test_build_section_claims_cross_chapter_oracle_near_tie_stays_unresolved() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:31/section:243",
                "diagnosis": "EXTRA",
                "blame_source": "2019/371",
                "replay_text": "243 section shared text",
                "oracle_text": "",
            }
        ],
        cross_chapter_oracle_matches={
            "chapter:31/section:243": {
                "oracle_section": "chapter:26/section:243",
                "oracle_section_score": 0.952259,
                "same_section_score": 0.0,
                "runner_up_oracle_section": "chapter:1/section:243",
                "runner_up_oracle_section_score": 0.936741,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_kind"] == "UNRESOLVED.address_projection.cross_chapter_oracle_drift"
    assert row["selected_inference_rule"] == (
        "oracle_matches_same_label_section_in_different_chapter"
    )
    assert row["cross_chapter_oracle_match"]["runner_up_oracle_section"] == "chapter:1/section:243"


def test_build_section_claims_cross_chapter_replay_exact_noncommensurable() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:31/section:243",
                "diagnosis": "MISSING",
                "blame_source": "2019/371",
                "replay_text": "",
                "oracle_text": "243 section text that is nearly identical",
            }
        ],
        cross_chapter_replay_matches={
            "chapter:31/section:243": {
                "replay_section": "chapter:26/section:243",
                "replay_section_score": 0.98,
                "same_section_score": 0.10,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert row["selected_kind"] == "address_relocation_cross_chapter_exact"
    assert row["selected_inference_rule"] == (
        "replay_matches_same_label_section_in_different_chapter_than_oracle"
    )


def test_build_section_claims_cross_chapter_replay_low_score_stays_unresolved() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:32/section:266",
                "diagnosis": "MISSING",
                "blame_source": "2021/1244",
                "replay_text": "",
                "oracle_text": "266 section text that differs substantially",
            }
        ],
        cross_chapter_replay_matches={
            "chapter:32/section:266": {
                "replay_section": "chapter:27/section:266",
                "replay_section_score": 0.60,
                "same_section_score": 0.10,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_kind"] == "UNRESOLVED.address_projection.cross_chapter_replay_drift"
    assert row["selected_inference_rule"] == (
        "replay_matches_same_label_section_in_different_chapter_than_oracle"
    )


def test_cross_chapter_replay_match_helper_skips_ambiguous_near_tie_candidates() -> None:
    matches = _cross_chapter_same_label_replay_matches(
        [
            {
                "section": "chapter:31/section:243",
                "diagnosis": "MISSING",
                "replay_text": "",
                "oracle_text": "243 oracle text",
            }
        ],
        {
            "chapter:26/section:243": "243 oracle text alpha",
            "chapter:1/section:243": "243 oracle text beta",
        },
    )

    row = matches["chapter:31/section:243"]
    assert {
        row["replay_section"],
        row["runner_up_replay_section"],
    } == {"chapter:26/section:243", "chapter:1/section:243"}
    assert float(row["runner_up_replay_section_score"]) > 0.0


def test_build_section_claims_same_chapter_exact_noncommensurable() -> None:
    """Same-chapter alternative match with score 0.97 -> PROVED_HTML_XML_NONCOMMENSURABLE."""
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:8/section:38",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1995/190",
                "replay_text": "38 section nearly identical text",
                "oracle_text": "38 section nearly identical text!",
            }
        ],
        section_bisect=[],
        alternative_replay_matches={
            "chapter:8/section:38": {
                "best_replay_section": "chapter:8/section:41",
                "best_replay_score": 0.97,
                "same_section_score": 0.362177,
            }
        },
    )

    row = rows[0]
    assert row["selected_tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert row["selected_kind"] == "address_relocation_same_chapter_exact"
    assert row["selected_inference_rule"] == (
        "same_chapter_replay_section_matches_oracle_better_than_same_number_section"
    )


def test_build_section_claims_demotes_same_chapter_oracle_range_drift() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:11/section:97",
                "diagnosis": "EXTRA",
                "blame_source": "1987/1094",
                "replay_text": "97 § Replay",
                "oracle_text": "",
            }
        ],
        oracle_range_matches={
            "chapter:11/section:97": {
                "oracle_range_section": "chapter:11/section:96a–97",
                "oracle_range_label": "96a–97",
            }
        },
    )

    row = rows[0]
    assert row["selected_kind"] == "same_chapter_oracle_range_drift"
    assert row["selected_tier"] == "PROVED_ORACLE_INCORRECT"
    assert row["selected_inference_rule"] == (
        "oracle_uses_same_chapter_section_range_instead_of_exact_section_label"
    )
    assert row["oracle_range_match"] == {
        "oracle_range_section": "chapter:11/section:96a–97",
        "oracle_range_label": "96a–97",
    }


def test_build_section_claims_demotes_preexisting_same_chapter_section_drift() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:8/section:33",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/761",
                "replay_text": "33 § Replay",
                "oracle_text": "33 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "chapter:8/section:33",
                "blame_source": "1996/761",
                "baseline_alternative_replay_match": {
                    "best_replay_section": "chapter:8/section:34",
                    "best_replay_score": 0.877551,
                    "same_section_score": 0.305684,
                },
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.address_projection.same_chapter_section_drift"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == (
        "preexisting_same_chapter_replay_section_matches_oracle_better_than_same_number_section"
    )
    assert row["candidate_kinds"] == ["UNRESOLVED.address_projection.same_chapter_section_drift"]


def test_build_section_claims_demotes_preexisting_same_section_structure_drift() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:9/section:78",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1996/295",
                "replay_text": "78 § Replay",
                "oracle_text": "78 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "chapter:9/section:78",
                "blame_source": "1996/295",
                "baseline_unmatched_oracle_subsections": {
                    "count": 1,
                    "max_best_replay_score": 0.214,
                    "oracle_text_excerpts": [
                        "Jos henkilö on suorittanut korkeakoulututkinnon luokanopettajan koulutusohjelman mukaan..."
                    ],
                },
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.preexisting.same_section_structure_drift"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == (
        "oracle_has_unmatched_same_section_subsection_fragments_before_blamed_amendment"
    )
    assert row["candidate_kinds"] == ["UNRESOLVED.preexisting.same_section_structure_drift"]


def test_build_section_claims_demotes_negligible_blame_drop_on_preexisting_residue() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "chapter:2/section:8",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "2022/544",
                "replay_text": "8 § Replay",
                "oracle_text": "8 § Oracle",
            }
        ],
        section_bisect=[
            {
                "section": "chapter:2/section:8",
                "blame_source": "2022/544",
                "baseline_score": 0.401025,
                "first_bad_source": "2019/1491",
                "blame_before_score": 0.401025,
                "blame_after_score": 0.399271,
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.preexisting.baseline_residue"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == (
        "material_divergence_predates_blamed_change_and_blame_delta_is_negligible"
    )
    assert row["candidate_kinds"] == ["UNRESOLVED.preexisting.baseline_residue"]


def test_build_section_claims_demotes_unblamed_replay_residue() -> None:
    rows = _build_section_claims(
        section_results=[
            {
                "section": "part:iv/chapter:12/section:8",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "",
                "replay_text": "8 § Replay text with extra tail.",
                "oracle_text": "8 § Replay text.",
            }
        ],
    )

    row = rows[0]
    assert row["selected_kind"] == "UNRESOLVED.preexisting.baseline_residue"
    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_inference_rule"] == (
        "residual_replay_divergence_has_no_blamed_amendment"
    )
    assert row["candidate_kinds"] == ["UNRESOLVED.preexisting.baseline_residue"]


def test_build_proof_claims_demotes_same_chapter_alternative_replay_match() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:8/section:38",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1995/190",
                "blame_title": "Test",
                "replay_text": "38 § Replay",
                "oracle_text": "38 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        alternative_replay_matches={
            "chapter:8/section:38": {
                "best_replay_section": "chapter:8/section:41",
                "best_replay_score": 0.751523,
                "same_section_score": 0.362177,
            }
        },
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.address_projection.same_chapter_replay_drift"
    )
    assert drift["tier"] == "UNRESOLVED"
    assert drift["trigger_observations"][0]["source"] == "oracle_check"
    assert len(drift["support"]["sections"]) == 1
    row = drift["support"]["sections"][0]
    assert row["section"] == "chapter:8/section:38"
    assert row["diagnosis"] == "REPLAY_EXTRA"
    assert row["blame_source"] == "1995/190"
    assert row["blame_title"] == "Test"
    assert row["similarity"] == pytest.approx(
        round(_section_similarity("38 § Replay", "38 § Oracle"), 6)
    )
    assert row["best_replay_section"] == "chapter:8/section:41"
    assert row["best_replay_score"] == 0.751523
    assert row["same_section_score"] == 0.362177
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_demotes_same_chapter_oracle_range_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:11/section:97",
                "diagnosis": "EXTRA",
                "blame_source": "1987/1094",
                "blame_title": "Test",
                "replay_text": "97 § Replay",
                "oracle_text": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        oracle_range_matches={
            "chapter:11/section:97": {
                "oracle_range_section": "chapter:11/section:96a–97",
                "oracle_range_label": "96a–97",
            }
        },
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "same_chapter_oracle_range_drift"
    )
    assert drift["tier"] == "PROVED_ORACLE_INCORRECT"
    assert drift["trigger_observations"][0]["source"] == "oracle_check"
    assert drift["support"]["sections"][0]["oracle_range_section"] == "chapter:11/section:96a–97"
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_build_proof_claims_demotes_cross_chapter_oracle_section_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:19/section:146",
                "diagnosis": "EXTRA",
                "blame_source": "2017/1001",
                "blame_title": "Test",
                "replay_text": "146 § Replay",
                "oracle_text": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        cross_chapter_oracle_matches={
            "chapter:19/section:146": {
                "oracle_section": "section:146",
                "oracle_section_score": 1.0,
                "same_section_score": 0.0,
            }
        },
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.address_projection.cross_chapter_oracle_drift"
    )
    assert drift["tier"] == "UNRESOLVED"
    assert drift["trigger_observations"][0]["source"] == "oracle_check"
    assert drift["support"]["sections"][0]["oracle_section"] == "section:146"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_exact_cross_chapter_oracle_yields_unanimous_noncommensurable() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:5/section:40",
                "diagnosis": "EXTRA",
                "blame_source": "2020/500",
                "blame_title": "Test",
                "replay_text": "40 section text that is nearly identical",
                "oracle_text": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        cross_chapter_oracle_matches={
            "chapter:5/section:40": {
                "oracle_section": "section:40",
                "oracle_section_score": 0.98,
                "same_section_score": 0.10,
            }
        },
        section_claims=[
            {
                "section": "chapter:5/section:40",
                "selected_kind": "address_relocation_cross_chapter_exact",
                "selected_tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
            }
        ],
    )

    assert not any(
        claim["kind"] == "UNRESOLVED.address_projection.cross_chapter_oracle_drift"
        for claim in claims
    )
    claim = next(
        c for c in claims if c["kind"] == "section_claims_unanimously_oracle_incorrect"
    )
    assert claim["tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert _primary_proof_tier(claims) == "PROVED_HTML_XML_NONCOMMENSURABLE"


def test_build_proof_claims_demotes_cross_chapter_replay_section_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:31/section:243",
                "diagnosis": "MISSING",
                "blame_source": "2019/371",
                "blame_title": "Test",
                "replay_text": "",
                "oracle_text": "243 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        cross_chapter_replay_matches={
            "chapter:31/section:243": {
                "replay_section": "chapter:26/section:243",
                "replay_section_score": 0.98,
                "same_section_score": 0.0,
            }
        },
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.address_projection.cross_chapter_replay_drift"
    )
    assert drift["tier"] == "UNRESOLVED"
    assert drift["trigger_observations"][0]["source"] == "oracle_check"
    assert drift["support"]["sections"][0]["replay_section"] == "chapter:26/section:243"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_exact_cross_chapter_replay_yields_unanimous_noncommensurable() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:32/section:266",
                "diagnosis": "MISSING",
                "blame_source": "2021/1244",
                "blame_title": "Test",
                "replay_text": "",
                "oracle_text": "266 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[],
        cross_chapter_replay_matches={
            "chapter:32/section:266": {
                "replay_section": "chapter:14/section:266",
                "replay_section_score": 1.0,
                "same_section_score": 0.0,
                "runner_up_replay_section": "chapter:27/section:266",
                "runner_up_replay_section_score": 0.734955,
            }
        },
        section_claims=[
            {
                "section": "chapter:32/section:266",
                "selected_kind": "address_relocation_cross_chapter_exact",
                "selected_tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
            }
        ],
    )

    claim = next(
        c for c in claims if c["kind"] == "section_claims_unanimously_oracle_incorrect"
    )
    assert claim["tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert _primary_proof_tier(claims) == "PROVED_HTML_XML_NONCOMMENSURABLE"


def test_1984_719_same_chapter_oracle_range_match_finds_section_97_real_corpus() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    replay_key = "chapter:11/section:97"

    matches = _same_chapter_oracle_range_matches(
        [
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
            }
        ],
        oracle_sections,
    )

    assert matches[replay_key]["oracle_range_section"] == "chapter:11/section:96a–97"
    assert matches[replay_key]["oracle_range_label"] == "96a–97"


def test_1984_719_typed_section_claims_select_same_chapter_oracle_range_drift_for_97() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    replay_key = "chapter:11/section:97"

    matches = _same_chapter_oracle_range_matches(
        [
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
            }
        ],
        oracle_sections,
    )

    rows = build_section_claims_typed(
        section_results=[
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
                "blame_source": "1987/1094",
                "blame_title": "",
            }
        ],
        oracle_range_matches=matches,
    )
    row = rows[0].to_legacy_row()

    assert row["selected_tier"] == "PROVED_ORACLE_INCORRECT"
    assert row["selected_kind"] == "same_chapter_oracle_range_drift"
    assert row["selected_inference_rule"] == (
        "oracle_uses_same_chapter_section_range_instead_of_exact_section_label"
    )
    assert row["oracle_range_match"]["oracle_range_section"] == "chapter:11/section:96a–97"


def test_1984_719_typed_proof_claims_promote_same_chapter_oracle_range_drift_for_97() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    replay_key = "chapter:11/section:97"

    matches = _same_chapter_oracle_range_matches(
        [
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
            }
        ],
        oracle_sections,
    )

    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
                "blame_source": "1987/1094",
                "blame_title": "",
            }
        ],
        oracle_range_matches=matches,
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": replay_key,
                "diagnosis": "EXTRA",
                "replay_text": replay_texts.get(replay_key, ""),
                "oracle_text": "",
                "blame_source": "1987/1094",
                "blame_title": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        oracle_range_matches=matches,
        typed_section_results=typed_rows,
    )

    claim = next(c for c in claims if c["kind"] == "same_chapter_oracle_range_drift")
    assert claim["tier"] == "PROVED_ORACLE_INCORRECT"
    assert claim["support"]["sections"][0]["oracle_range_section"] == "chapter:11/section:96a–97"
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_1984_719_bisect_support_finds_oracle_section_stale_for_79() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:79"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    row = next(r for r in bisect_rows if r["section"] == oracle_key)
    assert row["first_drop_source"] == "1996/295"
    assert row["blame_source"] == "1996/295"
    assert row["blame_elaboration_kinds"] == ["ELAB.PAYLOAD_COMPLETENESS"]
    assert row["blame_sparse_elaboration"] is False
    assert row["baseline_unmatched_oracle_subsections"] is None


def test_1984_719_bisect_support_finds_preexisting_same_section_structure_drift_for_78() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:78"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    row = next(r for r in bisect_rows if r["section"] == oracle_key)
    unmatched = row["baseline_unmatched_oracle_subsections"]
    assert unmatched["count"] == 1
    assert float(unmatched["max_best_replay_score"]) < 0.5
    assert unmatched["oracle_text_excerpts"]


def test_1984_719_typed_section_claims_select_preexisting_same_section_structure_drift_for_78() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:78"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )
    row = rows[0].to_legacy_row()

    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_kind"] == "UNRESOLVED.preexisting.same_section_structure_drift"
    assert row["selected_inference_rule"] == (
        "oracle_has_unmatched_same_section_subsection_fragments_before_blamed_amendment"
    )


def test_1984_719_typed_proof_claims_keep_preexisting_same_section_structure_drift_for_78() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:78"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_MISSING",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=bisect_rows,
        typed_section_results=typed_rows,
    )

    claim = next(
        c for c in claims if c["kind"] == "UNRESOLVED.preexisting.same_section_structure_drift"
    )
    assert claim["tier"] == "UNRESOLVED"
    assert claim["support"]["sections"][0]["section"] == "chapter:9/section:78"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_1984_719_typed_section_claims_select_oracle_section_stale_for_79() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:79"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )
    row = rows[0].to_legacy_row()

    assert row["selected_tier"] == "PROVED_ORACLE_INCORRECT"
    assert row["selected_kind"] == "oracle_section_stale"
    assert (
        row["selected_inference_rule"]
        == "deterministic_payload_completeness_same_section_drop_leaves_oracle_stale"
    )


def test_1984_719_typed_proof_claims_promote_oracle_section_stale_for_79() -> None:
    master = pinned_replay("1984/719", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1984/719")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:9/section:79"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1984/719",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/295",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/295",
                "blame_title": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=bisect_rows,
        typed_section_results=typed_rows,
    )

    claim = next(
        c for c in claims if c["kind"] == "section_claims_unanimously_oracle_incorrect"
    )
    assert claim["tier"] == "PROVED_ORACLE_INCORRECT"
    assert claim["inference_rule"] == "all_section_claims_resolve_to_oracle_or_noncommensurable"
    assert claim["support"]["section_claim_kinds"] == ["oracle_section_stale"]
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_1992_1702_same_chapter_replay_match_finds_section_38_real_corpus() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:38"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    matches = _same_chapter_alternative_replay_matches(
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        replay_texts,
    )

    assert matches[oracle_key]["best_replay_section"] == "chapter:8/section:41"
    assert float(matches[oracle_key]["best_replay_score"]) >= 0.70


def test_1992_1702_typed_section_claims_keep_section_38_as_same_chapter_replay_drift() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:38"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    matches = _same_chapter_alternative_replay_matches(
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        replay_texts,
    )

    rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1995/190",
                "blame_title": "",
            }
        ],
        alternative_replay_matches=matches,
    )
    row = rows[0].to_legacy_row()

    assert row["selected_tier"] == "UNRESOLVED"
    assert row["selected_kind"] == "UNRESOLVED.address_projection.same_chapter_replay_drift"
    assert row["selected_inference_rule"] == (
        "same_chapter_replay_section_matches_oracle_better_than_same_number_section"
    )


def test_1992_1702_bisect_support_finds_preexisting_same_chapter_section_drift_for_33() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:33"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        rows = _section_bisect_support(
            "1992/1702",
            "legal_pit",
            [
                {
                    "section": oracle_key,
                    "diagnosis": "REPLAY_EXTRA",
                    "blame_source": "1996/761",
                    "blame_title": "",
                    "replay_text": replay_texts.get(oracle_key, ""),
                    "oracle_text": oracle_text,
                }
            ],
            oracle_root=oracle_root,
        )

    row = next(r for r in rows if r["section"] == oracle_key)
    baseline = row["baseline_alternative_replay_match"]
    assert baseline["best_replay_section"] == "chapter:8/section:34"
    assert float(baseline["best_replay_score"]) >= 0.85
    assert float(baseline["same_section_score"]) < 0.4
    assert not any(
        issubclass(w.category, FutureWarning)
        and "Truth-testing of elements" in str(w.message)
        for w in caught
    )


def test_1992_1702_typed_proof_claims_keep_section_38_as_same_chapter_replay_drift() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:38"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    matches = _same_chapter_alternative_replay_matches(
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        replay_texts,
    )

    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1995/190",
                "blame_title": "",
            }
        ],
        alternative_replay_matches=matches,
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1995/190",
                "blame_title": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        alternative_replay_matches=matches,
        typed_section_results=typed_rows,
    )

    claim = next(
        c for c in claims if c["kind"] == "UNRESOLVED.address_projection.same_chapter_replay_drift"
    )
    assert claim["tier"] == "UNRESOLVED"
    assert claim["inference_rule"] == (
        "same_chapter_replay_section_matches_oracle_better_than_same_number_section"
    )
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_1992_1702_typed_section_claims_keep_same_chapter_section_drift_visible_for_33() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:33"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1992/1702",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/761",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/761",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )
    row = rows[0].to_legacy_row()

    assert row["selected_kind"] == "UNRESOLVED.preexisting.baseline_residue"
    assert "UNRESOLVED.address_projection.same_chapter_section_drift" in row["candidate_kinds"]
    assert "UNRESOLVED.address_projection.same_chapter_section_drift" in row["defeated_candidate_kinds"]


def test_1992_1702_typed_proof_claims_keep_section_33_at_no_strong_claim() -> None:
    master = pinned_replay("1992/1702", mode="legal_pit", quiet=True)
    replay_sections = extract_ir_sections(master.materialized_state.ir)
    replay_texts = {key: render_node_text(node) for key, node in replay_sections.items()}

    oracle_root = _ground_truth_tree("1992/1702")
    oracle_sections = extract_oracle_sections(oracle_root, exclude_kumottu_stubs=False)
    oracle_key = "chapter:8/section:33"
    oracle_text = render_node_text(oracle_sections.get(oracle_key))

    bisect_rows = _section_bisect_support(
        "1992/1702",
        "legal_pit",
        [
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/761",
                "blame_title": "",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
            }
        ],
        oracle_root=oracle_root,
    )

    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/761",
                "blame_title": "",
            }
        ],
        section_bisect=bisect_rows,
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": oracle_key,
                "diagnosis": "REPLAY_EXTRA",
                "replay_text": replay_texts.get(oracle_key, ""),
                "oracle_text": oracle_text,
                "blame_source": "1996/761",
                "blame_title": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        typed_section_results=typed_rows,
    )

    claim = next(c for c in claims if c["kind"] == "no_strong_claim")
    assert claim["tier"] == "UNRESOLVED"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_typed_proof_claims_exact_cross_chapter_oracle_stays_noncommensurable() -> None:
    typed_rows = build_section_claims_typed(
        section_results=[
            {
                "section": "chapter:5/section:40",
                "diagnosis": "EXTRA",
                "replay_text": "40 section text that is nearly identical",
                "oracle_text": "",
                "blame_source": "2020/500",
                "blame_title": "Test",
            }
        ],
        cross_chapter_oracle_matches={
            "chapter:5/section:40": {
                "oracle_section": "section:40",
                "oracle_section_score": 0.98,
                "same_section_score": 0.10,
            }
        },
    )

    claims = build_proof_claims_typed(
        section_results=[
            {
                "section": "chapter:5/section:40",
                "diagnosis": "EXTRA",
                "replay_text": "40 section text that is nearly identical",
                "oracle_text": "",
                "blame_source": "2020/500",
                "blame_title": "Test",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        cross_chapter_oracle_matches={
            "chapter:5/section:40": {
                "oracle_section": "section:40",
                "oracle_section_score": 0.98,
                "same_section_score": 0.10,
            }
        },
        typed_section_results=typed_rows,
    )

    assert not any(
        c["kind"] == "UNRESOLVED.address_projection.cross_chapter_oracle_drift"
        for c in claims
    )
    claim = next(
        c for c in claims if c["kind"] == "section_claims_unanimously_oracle_incorrect"
    )
    assert claim["tier"] == "PROVED_HTML_XML_NONCOMMENSURABLE"
    assert _primary_proof_tier(claims) == "PROVED_HTML_XML_NONCOMMENSURABLE"




def test_build_proof_claims_demotes_preexisting_same_chapter_section_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:8/section:33",
                "diagnosis": "REPLAY_EXTRA",
                "blame_source": "1996/761",
                "blame_title": "Test",
                "replay_text": "33 § Replay",
                "oracle_text": "33 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "chapter:8/section:33",
                "blame_source": "1996/761",
                "baseline_alternative_replay_match": {
                    "best_replay_section": "chapter:8/section:34",
                    "best_replay_score": 0.877551,
                    "same_section_score": 0.305684,
                },
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.address_projection.same_chapter_section_drift"
    )
    assert drift["tier"] == "UNRESOLVED"
    assert drift["trigger_observations"][0]["source"] == "section_bisect"
    assert drift["support"]["sections"][0]["best_replay_section"] == "chapter:8/section:34"
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_demotes_preexisting_same_section_structure_drift() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:9/section:78",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "1996/295",
                "blame_title": "Test",
                "replay_text": "78 § Replay",
                "oracle_text": "78 § Oracle",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "chapter:9/section:78",
                "blame_source": "1996/295",
                "baseline_unmatched_oracle_subsections": {
                    "count": 1,
                    "max_best_replay_score": 0.214,
                    "oracle_text_excerpts": [
                        "Jos henkilö on suorittanut korkeakoulututkinnon luokanopettajan koulutusohjelman mukaan..."
                    ],
                },
            }
        ],
    )

    assert not any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    drift = next(
        claim for claim in claims
        if claim["kind"] == "UNRESOLVED.preexisting.same_section_structure_drift"
    )
    assert drift["tier"] == "UNRESOLVED"
    assert drift["trigger_observations"][0]["source"] == "section_bisect"
    assert drift["support"]["sections"][0]["unmatched_oracle_subsection_count"] == 1
    assert _primary_proof_tier(claims) == "UNRESOLVED"


def test_build_proof_claims_does_not_cross_match_chaptered_support_by_section_label() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "chapter:1/section:11",
                "diagnosis": "EXTRA",
                "blame_source": "2008/732",
                "blame_title": "Test",
                "replay_text": "11 § Replay",
                "oracle_text": "",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": [],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
        section_bisect=[
            {
                "section": "chapter:3/section:11",
                "blame_source": "2008/732",
                "preexisting_before_any_drop": True,
                "baseline_score": 0.0,
                "first_bad_source": "1992/330",
            }
        ],
    )

    assert any(claim["tier"] == "PROVED_REPLAY_BUG" for claim in claims)
    assert not any(claim["kind"] == "UNRESOLVED.preexisting.baseline_residue" for claim in claims)


def test_primary_proof_tier_prefers_non_replay_cause_in_mixed_case() -> None:
    claims = _build_proof_claims(
        section_results=[
            {
                "section": "section:4",
                "diagnosis": "REPLAY_MISSING",
                "blame_source": "2017/1153",
                "blame_title": "Test",
                "replay_text": "4 § on kumottu.",
                "oracle_text": "4 § Tässä on sisältöä.",
            }
        ],
        source_pathologies=[],
        html_topology={
            "missing_from_xml": ["4 a §"],
            "extra_in_xml": [],
            "noncommensurable_reason": "",
        },
        contingent_effective_sources=[],
        corrigendum_support=[],
    )

    assert {claim["tier"] for claim in claims} == {
        "PROVED_ORACLE_INCORRECT",
        "PROVED_REPLAY_BUG",
    }
    assert _primary_proof_tier(claims) == "PROVED_ORACLE_INCORRECT"


def test_corrigendum_support_for_amendments_summarizes_official_verified_and_manual_counts() -> None:
    support = _corrigendum_support_for_amendments(
        ["442/2016", "991/2012"],
        patch_records=[
            {"amendment_id": "442/2016", "verified_in_source": True},
            {"amendment_id": "442/2016", "verified_in_source": False},
            {"amendment_id": "991/2012", "verified_in_source": True},
        ],
        source_records=[
            {"amendment_id": "442/2016", "pdf_name": "sk20160442_1.pdf"},
            {"amendment_id": "442/2016", "pdf_name": "sk20160442_1.pdf"},
            {"amendment_id": "991/2012", "pdf_name": "sk20120991_1.pdf"},
        ],
        manual_override_counts={"442/2016": 2, "991/2012": 1},
    )

    by_amendment_id = {item["amendment_id"]: item for item in support}
    assert by_amendment_id["442/2016"]["official_item_count"] == 2
    assert by_amendment_id["442/2016"]["verified_in_source_count"] == 1
    assert by_amendment_id["442/2016"]["unverified_item_count"] == 1
    assert by_amendment_id["442/2016"]["source_pdf_count"] == 1
    assert by_amendment_id["442/2016"]["manual_override_count"] == 2
    assert by_amendment_id["991/2012"]["official_item_count"] == 1
    assert by_amendment_id["991/2012"]["manual_override_count"] == 1


def test_build_oracle_proof_bundle_filters_to_oracle_claims(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda statute_id, mode="legal_pit", include_bisect=False: {
            "statute_id": statute_id,
            "title": "Test",
            "mode": mode,
            "proof_contract": {"version": "lawvm-proof-v1", "status": "defeasible_current_system"},
            "oracle_version_amendment_id": "2020/1",
            "html_topology": {"missing_from_xml": ["4 a §"], "extra_in_xml": [], "noncommensurable_reason": ""},
            "supporting_amendments": [],
            "section_results": [
                {"section": "section:4a", "diagnosis": "ORACLE_STALE"},
                {"section": "section:5", "diagnosis": "REPLAY_MISSING"},
            ],
            "proof_claims": [
                {"tier": "PROVED_ORACLE_INCORRECT", "kind": "xml_html_topology_drift"},
                {"tier": "PROVED_REPLAY_BUG", "kind": "replay_divergence"},
            ],
            "proof_tiers": ["PROVED_ORACLE_INCORRECT", "PROVED_REPLAY_BUG"],
        },
    )

    bundle = build_oracle_proof_bundle("1991/1707", mode="legal_pit")

    assert bundle["proved"] is True
    assert bundle["primary_proof_tier"] == "PROVED_ORACLE_INCORRECT"
    assert bundle["alternative_tiers"] == ["PROVED_REPLAY_BUG"]
    assert bundle["proof_claims"] == [
        {"tier": "PROVED_ORACLE_INCORRECT", "kind": "xml_html_topology_drift"}
    ]


def test_evidence_main_json_output_is_clean(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence.build_evidence_bundle",
        lambda statute_id, mode="legal_pit": {
            "statute_id": statute_id,
            "title": "Test",
            "mode": mode,
            "proof_contract": {"version": "lawvm-proof-v1", "status": "defeasible_current_system"},
            "proof_claims": [],
            "proof_tiers": ["UNRESOLVED"],
            "primary_proof_tier": "UNRESOLVED",
        },
    )

    main(SimpleNamespace(command="evidence", statute_id=["1991/1707"], mode="legal_pit", json=True))

    payload = json.loads(capsys.readouterr().out)
    assert payload["statute_id"] == "1991/1707"


def test_evidence_main_forwards_with_bisect(monkeypatch, capsys) -> None:
    seen: dict[str, object] = {}

    def _fake_build(statute_id, mode="legal_pit", include_bisect=False):
        seen["include_bisect"] = include_bisect
        return {
            "statute_id": statute_id,
            "title": "Test",
            "mode": mode,
            "proof_contract": {"version": "lawvm-proof-v1", "status": "defeasible_current_system"},
            "proof_claims": [],
            "proof_tiers": ["UNRESOLVED"],
            "primary_proof_tier": "UNRESOLVED",
            "section_bisect": [{"section": "section:17", "baseline_score": 0.925, "first_drop_source": ""}],
        }

    monkeypatch.setattr("lawvm.tools.evidence.build_evidence_bundle", _fake_build)

    main(
        SimpleNamespace(
            command="evidence",
            statute_id=["1991/827"],
            mode="legal_pit",
            json=True,
            with_bisect=True,
        )
    )

    payload = json.loads(capsys.readouterr().out)
    assert seen["include_bisect"] is True
    assert payload["section_bisect"][0]["section"] == "section:17"


def test_build_evidence_bundle_auto_includes_bisect_for_replay_residue(monkeypatch) -> None:
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        "lawvm.tools.evidence._classify_statute",
        lambda statute_id, mode="legal_pit", **_kw: ClassifyResult(
            sid=statute_id,
            title="Test",
            mode=mode,
            overall_score=0.9,
            section_score=0.95,
            section_results=[
                {
                    "section": "section:27",
                    "diagnosis": "REPLAY_MISSING",
                    "replay_text": "Replay",
                    "oracle_text": "Oracle",
                    "blame_source": "",
                    "blame_title": "",
                }
            ],
            source_pathologies=[],
            contingent_effective_sources=[],
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.replay_xml",
        lambda statute_id, mode="legal_pit", **_kw: SimpleNamespace(
            source_adjudication=None,
            materialized_state=SimpleNamespace(ir=None),
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.compile_fi_facade_from_replay",
        lambda **_kw: SimpleNamespace(
            projection_rows=lambda: (),
            summary_projection=lambda: SimpleNamespace(
                strict_fail_reasons=(),
            ),
            bundle=SimpleNamespace(structural_ops=()),
            source_pathology_rows=lambda: (),
            strict_profile_name="",
            finding_ledger=(),
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence._audit_html_one",
        lambda statute_id: SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )
    monkeypatch.setattr("lawvm.tools.evidence.get_ground_truth_tree", lambda statute_id: object())
    monkeypatch.setattr("lawvm.tools.evidence._corrigendum_support_for_amendments", lambda mids: [])

    def _fake_section_bisect(statute_id: str, mode: str, section_results, **_kw):
        seen["called"] = True
        return [
            {
                "section": "section:27",
                "baseline_score": 0.91,
                "first_bad_source": "1992/1624",
                "first_drop_source": "",
                "worst_drops": [],
                "preexisting_before_any_drop": True,
            }
        ]

    monkeypatch.setattr("lawvm.tools.evidence._section_bisect_support", _fake_section_bisect)

    bundle = build_evidence_bundle("1991/827", mode="legal_pit")

    assert seen["called"] is True
    assert bundle["section_bisect"][0]["section"] == "section:27"
    # Gap 4 and other rollup rules may promote from UNRESOLVED;
    # the key assertion is that bisect was auto-triggered.
    assert bundle["primary_proof_tier"] in ("UNRESOLVED", "PROVED_ORACLE_INCORRECT")


def test_build_evidence_bundle_summarizes_compiler_observations(monkeypatch) -> None:
    replay_meta: dict[str, object] = {
        "elaboration_observations": [
            {
                "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                "stage": "group_payload_normalization",
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_norm": "35",
                "target_chapter": "",
            },
            {
                "kind": "ELAB.CONTAINER_PRUNED_SHADOWED",
                "stage": "group_payload_normalization",
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_norm": "40",
                "target_chapter": "",
            },
            {
                "kind": "ELAB.PAYLOAD_COMPLETENESS",
                "stage": "group_payload_normalization",
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_norm": "35",
                "target_chapter": "",
                "payload_completeness_kind": "fragmentary",
                "tail_policy": "preserve_unstated_tail",
                "detail": {
                    "payload_completeness_kind": "fragmentary",
                    "tail_policy": "preserve_unstated_tail",
                },
            },
        ],
        "sparse_slot_bindings": [
            {
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_norm": "35",
                "target_chapter": "",
                "op_description": "REPLACE 35 § 2 mom",
                "op_type": "REPLACE",
                "target_paragraph": 2,
                "target_item": "",
                "target_special": "",
                "payload_slot_index": 1,
                "payload_slot_label": "2",
            }
        ],
        "sparse_leftovers": [
            {
                "source_statute": "1993/805",
                "target_unit_kind": "section",
                "target_norm": "35",
                "target_chapter": "",
                "unassigned_slots": ["2:2", "3:(unlabeled)"],
            }
        ],
        "apply_mutation_events": [
            {
                "helper": "_apply_deterministic_subsection_op",
                "source_statute": "1993/805",
                "resolved_target_path": (("section", "35"), ("subsection", "2")),
            },
            {
                "helper": "_apply_deterministic_subsection_op",
                "source_statute": "1993/805",
                "resolved_target_path": (("section", "35"), ("subsection", "3")),
            },
            {
                "helper": "_apply_whole_section_op",
                "source_statute": "1993/805",
                "resolved_target_path": (("section", "40"),),
            },
        ],
    }
    monkeypatch.setattr(
        "lawvm.tools.evidence._classify_statute",
        lambda statute_id, mode="legal_pit", **_kw: ClassifyResult(
            sid=statute_id,
            title="Test",
            mode=mode,
            overall_score=0.82,
            section_score=0.73,
            section_results=[
                {
                    "section": "section:35",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "1993/805",
                    "replay_text": "Replay",
                    "oracle_text": "Oracle",
                }
            ],
            source_pathologies=[],
            contingent_effective_sources=[],
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.replay_xml",
        lambda statute_id, mode="legal_pit", replay_meta_out=None, **_kw: (
            replay_meta_out.update(replay_meta) if replay_meta_out is not None else None
        ) or SimpleNamespace(
            source_adjudication=None,
            materialized_state=SimpleNamespace(ir=None),
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.compile_fi_facade_from_replay",
        lambda **_kw: SimpleNamespace(
            projection_rows=lambda: (
                {
                    "kind": "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                    "message": "align sparse omission to live",
                    "source": "",
                    "detail": {},
                },
                {
                    "kind": "PARSE.TARGET_GUESSING",
                    "message": "target guessing",
                    "source": "1993/805",
                    "detail": {
                        "tag": "normalize_item_like_target",
                        "target_unit_kind": "section",
                        "target_norm": "35",
                        "target_chapter": "",
                    },
                },
                {
                    "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                    "message": "context dependent anchor resolution",
                    "source": "1993/805",
                    "detail": {
                        "tag": "context_anchor",
                        "target_unit_kind": "section",
                        "target_norm": "40",
                        "target_chapter": "",
                    },
                },
                {
                    "kind": "other",
                    "message": "other",
                    "source": "",
                    "detail": {},
                },
            ),
            summary_projection=lambda: SimpleNamespace(
                strict_fail_reasons=(),
            ),
            bundle=SimpleNamespace(structural_ops=()),
            source_pathology_rows=lambda: (),
            strict_profile_name="",
            finding_ledger=(),
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence._audit_html_one",
        lambda statute_id: SimpleNamespace(
            noncommensurable_reason="",
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
        ),
    )
    monkeypatch.setattr("lawvm.tools.evidence.get_ground_truth_tree", lambda statute_id: object())
    monkeypatch.setattr("lawvm.tools.evidence._corrigendum_support_for_amendments", lambda mids: [])
    monkeypatch.setattr(
        "lawvm.tools.evidence._section_bisect_support",
        lambda statute_id, mode, section_results, **_kw: [
            {
                "section": "section:35",
                "baseline_score": 0.71,
                "first_bad_source": "1993/805",
                "first_drop_source": "1993/805",
                "worst_drops": [],
                "preexisting_before_any_drop": False,
                "blame_source": "1993/805",
                "blame_elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "blame_sparse_elaboration": True,
                "blame_sparse_slot_binding_count": 1,
                "blame_sparse_slot_binding_labels": ["2"],
                "blame_sparse_leftover_count": 2,
                "blame_apply_helpers_for_section": [
                    "_apply_deterministic_subsection_op",
                ],
            }
        ],
    )

    bundle = build_evidence_bundle("1990/1295", mode="legal_pit")

    observations = bundle["compiler_observations"]
    assert "adjudication_kinds" not in bundle
    assert "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION" in bundle["projection_kinds"]
    assert "PARSE.TARGET_GUESSING" in bundle["projection_kinds"]
    assert "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE" in bundle["projection_kinds"]
    assert observations["normalized_section_observation_count"] == 8
    assert observations["normalized_observation_family_counts"] == {
        "apply_mutation": 3,
        "elaboration": 3,
        "sparse_slot_binding": 1,
        "sparse_leftover": 1,
    }
    assert observations["elaboration_observation_count"] == 3
    assert observations["elaboration_projection_count"] == 1
    assert observations["elaboration_kind_counts"] == {
        "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE": 1,
        "ELAB.CONTAINER_PRUNED_SHADOWED": 1,
        "ELAB.PAYLOAD_COMPLETENESS": 1,
    }
    assert observations["elaboration_stage_counts"] == {
        "group_payload_normalization": 3,
    }
    assert observations["payload_completeness_kind_counts"] == {
        "fragmentary": 1,
    }
    assert observations["payload_completeness_tail_policy_counts"] == {
        "preserve_unstated_tail": 1,
    }
    assert observations["provenance_projection_count"] == 2
    assert observations["provenance_projection_kind_counts"] == {
        "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION": 1,
        "PARSE.TARGET_GUESSING": 1,
    }
    assert observations["provenance_projection_rows"] == [
            {
                "kind": "LOWER.CONTEXT_DEPENDENT_ANCHOR_RESOLUTION",
                "source_statute": "1993/805",
                "tag": "context_anchor",
                "target_unit_kind": "section",
                "target_norm": "40",
                "target_chapter": "",
            },
            {
                "kind": "PARSE.TARGET_GUESSING",
                "source_statute": "1993/805",
                "tag": "normalize_item_like_target",
                "target_unit_kind": "section",
                "target_norm": "35",
                "target_chapter": "",
            },
    ]
    assert observations["sparse_slot_binding_count"] == 1
    assert observations["sparse_slot_binding_labels"] == ["2"]
    assert observations["sparse_leftover_count"] == 1
    assert observations["sparse_leftover_slot_count"] == 2
    assert observations["sparse_leftover_labels"] == ["2:2", "3:(unlabeled)"]
    assert observations["apply_mutation_event_count"] == 3
    assert observations["apply_helper_counts"] == {
        "_apply_deterministic_subsection_op": 2,
        "_apply_whole_section_op": 1,
    }
    assert observations["section_bisect_observation_row_count"] == 1
    assert observations["section_bisect_sparse_blocker_row_count"] == 1
    assert observations["section_bisect_rows_with_observation_support"] == [
            {
                "section": "section:35",
                "blame_source": "1993/805",
                "elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "sparse_slot_binding_count": 1,
                "sparse_slot_binding_labels": ["2"],
                "sparse_leftover_labels": ["2:2", "3:(unlabeled)"],
                "payload_completeness_kinds": ["fragmentary"],
                "payload_completeness_tail_policies": ["preserve_unstated_tail"],
                "sparse_leftover_count": 1,
                "sparse_leftover_slot_count": 2,
                "apply_helpers": [
                    "_apply_deterministic_subsection_op",
                ],
            }
    ]
    assert observations["section_bisect_rows_with_sparse_blocker"] == [
            {
                "section": "section:35",
                "blame_source": "1993/805",
                "elaboration_kinds": [
                    "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE",
                ],
                "sparse_slot_binding_count": 1,
                "sparse_slot_binding_labels": ["2"],
                "sparse_leftover_labels": ["2:2", "3:(unlabeled)"],
                "payload_completeness_kinds": ["fragmentary"],
                "payload_completeness_tail_policies": ["preserve_unstated_tail"],
                "sparse_leftover_count": 1,
                "sparse_leftover_slot_count": 2,
                "apply_helpers": [
                    "_apply_deterministic_subsection_op",
                ],
            }
    ]


def test_build_evidence_bundle_records_context_degradation(monkeypatch) -> None:
    monkeypatch.setattr(
        "lawvm.tools.evidence._classify_statute",
        lambda statute_id, mode="legal_pit", **_kw: ClassifyResult(
            sid=statute_id,
            title="Test",
            mode=mode,
            overall_score=0.82,
            section_score=0.73,
            section_results=[
                {
                    "section": "section:1",
                    "diagnosis": "REPLAY_MISSING",
                    "blame_source": "2020/100",
                    "replay_text": "Replay",
                    "oracle_text": "Oracle",
                }
            ],
            source_pathologies=[],
            contingent_effective_sources=[],
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.replay_xml",
        lambda statute_id, mode="legal_pit", **_kw: SimpleNamespace(
            source_adjudication=None,
            materialized_state=SimpleNamespace(ir=None),
            timelines={},
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence.compile_fi_facade_from_replay",
        lambda **_kw: SimpleNamespace(
            projection_rows=lambda: (),
            summary_projection=lambda: SimpleNamespace(strict_fail_reasons=()),
            bundle=SimpleNamespace(structural_ops=()),
            source_pathology_rows=lambda: (),
            strict_profile_name="",
            finding_ledger=(),
        ),
    )
    monkeypatch.setattr(
        "lawvm.tools.evidence._audit_html_one",
        lambda statute_id: SimpleNamespace(
            missing_from_xml=[],
            extra_in_xml=[],
            html_error="",
            noncommensurable_reason="",
        ),
    )
    monkeypatch.setattr("lawvm.tools.evidence.get_ground_truth_tree", lambda statute_id: object())
    monkeypatch.setattr("lawvm.tools.evidence._corrigendum_support_for_amendments", lambda mids: [])
    monkeypatch.setattr(
        "lawvm.tools.evidence._section_bisect_support",
        lambda statute_id, mode, section_results, **_kw: [
            {
                "section": "section:1",
                "blame_source": "2020/100",
            }
        ],
    )

    def _raise_section_strict(*_args, **_kwargs):
        raise RuntimeError("strict rail offline")

    def _raise_chain_completeness(*_args, **_kwargs):
        raise RuntimeError("chain rail offline")

    monkeypatch.setattr(
        "lawvm.core.compile_result.compute_section_strict_verdicts",
        _raise_section_strict,
    )
    monkeypatch.setattr(
        "lawvm.core.chain_completeness.compute_chain_completeness",
        _raise_chain_completeness,
    )

    bundle = build_evidence_bundle("1990/1295", mode="legal_pit", include_bisect=True)

    diagnostics = bundle["evidence_context_diagnostics"]
    assert {
        (item["rail"], item["exception_type"], item["message"])
        for item in diagnostics
    } >= {
        ("section_strict_verdicts", "RuntimeError", "strict rail offline"),
        ("chain_completeness", "RuntimeError", "chain rail offline"),
    }
    rows = bundle["evidence"]["finding_rows"]
    assert {
        (row["rule_id"], row["phase"], row["source_artifact_id"])
        for row in rows
    } >= {
        ("evidence_context_degraded:section_strict_verdicts", "evidence_context", "1990/1295"),
        ("evidence_context_degraded:chain_completeness", "evidence_context", "1990/1295"),
    }
    assert all(validate_corpus_finding_evidence_row(row) == () for row in rows)


def test_evidence_review_filters_context_degradation() -> None:
    bundles = [
        {
            "statute_id": "1990/1295",
            "title": "A",
            "primary_proof_tier": "UNRESOLVED",
            "proof_tiers": ["UNRESOLVED"],
            "proof_claims": [],
            "section_claims": [],
            "strict_fail_reasons": [],
            "compiler_observations": {},
            "source_pathologies": [],
            "html_topology": {},
            "evidence_context_diagnostics": [
                {
                    "kind": "evidence_context_degraded",
                    "rail": "chain_completeness",
                    "exception_type": "RuntimeError",
                    "message": "chain rail offline",
                }
            ],
        },
        {
            "statute_id": "1991/1",
            "title": "B",
            "primary_proof_tier": "UNRESOLVED",
            "proof_tiers": ["UNRESOLVED"],
            "proof_claims": [],
            "section_claims": [],
            "strict_fail_reasons": [],
            "compiler_observations": {},
            "source_pathologies": [],
            "html_topology": {},
            "evidence_context_diagnostics": [],
        },
    ]

    review = _review_bundles(
        bundles,
        evidence_context_degraded_only=True,
        evidence_context_rail="chain_completeness",
    )

    assert review["selected_count"] == 1
    assert review["evidence_context_degraded_count"] == 1
    assert review["by_evidence_context_degradation_rail"] == {"chain_completeness": 1}
    assert review["by_evidence_context_degradation_exception"] == {"RuntimeError": 1}
    assert review["rows"][0]["evidence_context_degradation_count"] == 1
    assert review["rows"][0]["evidence_context_degradation_rails"] == ["chain_completeness"]


def test_evidence_review_rejects_malformed_source_pathology_rows() -> None:
    bundles = [
        {
            "statute_id": "1990/1",
            "title": "A",
            "primary_proof_tier": "UNRESOLVED",
            "proof_tiers": ["UNRESOLVED"],
            "proof_claims": [],
            "section_claims": [],
            "strict_fail_reasons": [],
            "compiler_observations": {},
            "source_pathologies": [{"code": "OK"}, "silently-dropped-before", 42],
            "html_topology": {},
            "evidence_context_diagnostics": [],
        }
    ]

    with pytest.raises(ValueError, match="field source_pathologies contains non-object entries at indexes: 1, 2"):
        _review_bundles(bundles)


def test_evidence_review_rejects_malformed_context_diagnostics() -> None:
    bundles = [
        {
            "statute_id": "1990/1",
            "title": "A",
            "primary_proof_tier": "UNRESOLVED",
            "proof_tiers": ["UNRESOLVED"],
            "proof_claims": [],
            "section_claims": [],
            "strict_fail_reasons": [],
            "compiler_observations": {},
            "source_pathologies": [],
            "html_topology": {},
            "evidence_context_diagnostics": [{"kind": "OK"}, "silently-dropped-before"],
        }
    ]

    with pytest.raises(
        ValueError,
        match="field evidence_context_diagnostics contains non-object entries at indexes: 1",
    ):
        _review_bundles(bundles)


def test_normalize_observation_streams_keeps_apply_mutations_unowned_without_resolved_target() -> None:
    normalized = _normalize_observation_streams(
        apply_mutation_events=[
            {
                "family": "apply_mutation",
                "helper": "_apply_container_op",
                "source_statute": "2025/1349",
                "outcome": "APPLIED",
                "target_unit_kind": "chapter",
                "parent_path": (("chapter", "5"),),
            }
        ]
    )

    assert len(normalized) == 1
    record = normalized[0]
    assert record["family"] == "apply_mutation"
    assert record["helper"] == "_apply_container_op"
    assert record["target_unit_kind"] == "chapter"
    assert record["target_kind"] == ""
    assert record["target_norm"] == ""
    assert record["target_chapter"] == ""
    assert record["target_path"] == (("chapter", "5"),)
    assert record["section"] == ""
    assert record["section_label"] == ""
    assert record["chapter_label"] == ""


def test_normalize_observation_streams_includes_apply_mutation_invariant_reports() -> None:
    normalized = _normalize_observation_streams(
        apply_mutation_invariant_reports=[
            {
                "helper": "_apply_whole_section_op",
                "source_statute": "2025/1349",
                "path_set_invariant_holds": False,
                "allowed_effect_region_paths": [(("chapter", "5"), ("section", "2"))],
                "covered_changed_paths": [],
                "unexplained_changed_paths": [(("chapter", "5"), ("section", "3"))],
                "results": [{"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"}],
                "matched_allowance_rule_ids": ["section_move_replace_destination_rebind"],
            }
        ]
    )

    assert normalized == [
        {
            "family": "apply_mutation_invariant",
            "kind": "PATH_SET_INVARIANT_BROKEN",
            "stage": "apply",
            "source_statute": "2025/1349",
            "helper": "_apply_whole_section_op",
            "target_unit_kind": "",
            "target_kind": "",
            "target_norm": "",
            "target_chapter": "",
            "target_path": (("chapter", "5"), ("section", "2")),
            "section": "chapter:5/section:2",
            "section_label": "2",
            "chapter_label": "5",
            "path_set_invariant_holds": False,
            "covered_changed_count": 0,
            "unexplained_changed_count": 1,
            "declared_recovery_rule_ids": [],
            "declared_migration_rule_ids": [],
            "result_codes": ["REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"],
            "matched_allowance_rule_ids": ["section_move_replace_destination_rebind"],
        }
    ]


def test_normalize_observation_streams_prefers_invariant_reports_over_raw_apply_events() -> None:
    normalized = _normalize_observation_streams(
        apply_mutation_events=[
            {
                "helper": "_apply_deterministic_subsection_op",
                "source_statute": "2025/1349",
                "resolved_target_path": (("chapter", "5"), ("section", "2")),
            }
        ],
        apply_mutation_invariant_reports=[
            {
                "helper": "_apply_whole_section_op",
                "source_statute": "2025/1349",
                "path_set_invariant_holds": True,
                "allowed_effect_region_paths": [(("chapter", "5"), ("section", "2"))],
                "covered_changed_paths": [(("chapter", "5"), ("section", "2"))],
                "unexplained_changed_paths": [],
                "results": [{"code": "REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED"}],
                "matched_allowance_rule_ids": [],
            }
        ],
    )

    assert normalized == [
        {
            "family": "apply_mutation_invariant",
            "kind": "PATH_SET_INVARIANT_HOLDS",
            "stage": "apply",
            "source_statute": "2025/1349",
            "helper": "_apply_whole_section_op",
            "target_unit_kind": "",
            "target_kind": "",
            "target_norm": "",
            "target_chapter": "",
            "target_path": (("chapter", "5"), ("section", "2")),
            "section": "chapter:5/section:2",
            "section_label": "2",
            "chapter_label": "5",
            "path_set_invariant_holds": True,
            "covered_changed_count": 1,
            "unexplained_changed_count": 0,
            "declared_recovery_rule_ids": [],
            "declared_migration_rule_ids": [],
            "result_codes": ["REPLAY_APPLY_BOUNDARY_TOUCH_ALLOWED"],
            "matched_allowance_rule_ids": [],
        }
    ]


def test_compiler_observation_summary_uses_invariant_apply_rows_for_helper_support() -> None:
    replay_meta: dict[str, object] = {
        "apply_mutation_events": [
            {
                "helper": "_apply_deterministic_subsection_op",
                "source_statute": "1993/805",
                "resolved_target_path": (("section", "35"), ("subsection", "2")),
            }
        ],
        "apply_mutation_invariant_reports": [
            {
                "helper": "_apply_whole_section_op",
                "source_statute": "1993/805",
                "path_set_invariant_holds": False,
                "allowed_effect_region_paths": [(("section", "35"),)],
                "covered_changed_paths": [],
                "unexplained_changed_paths": [(("section", "36"),)],
                "results": [{"code": "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET"}],
                "matched_allowance_rule_ids": [],
            }
        ],
    }

    observations = _compiler_observation_summary(
        replay_meta=replay_meta,
        projection_rows=[],
        section_bisect=[
            {
                "section": "section:35",
                "blame_source": "1993/805",
                "blame_sparse_elaboration": False,
            }
        ],
    )

    assert observations["normalized_observation_family_counts"] == {
        "apply_mutation_invariant": 1,
    }
    assert observations["apply_mutation_event_count"] == 1
    assert observations["apply_helper_counts"] == {
        "_apply_whole_section_op": 1,
    }
    assert observations["section_bisect_observation_row_count"] == 0
    assert observations["section_bisect_rows_with_observation_support"] == []


def test_compiler_observation_summary_rejects_malformed_projection_rows() -> None:
    with pytest.raises(ValueError, match="non-object entries at indexes: 1, 2"):
        _compiler_observation_summary(
            replay_meta={},
            projection_rows=[
                {"kind": "PARSE.TARGET_GUESSING"},
                "silently-dropped-before",
                42,
            ],
        )
