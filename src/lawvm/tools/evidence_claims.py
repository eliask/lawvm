"""Proof claim construction for evidence bundles.

Extracted from evidence.py — builds section-level and statute-level
proof claims with tier classification.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional, cast

from lawvm.core.section_evidence_context import _scoped_html_noncommensurable_reason
from lawvm.tools._evidence_helpers import (
    _build_support_lookup_maps,
    _has_negligible_blame_drop_on_preexisting_residue,
    _lookup_support_row,
    _obs,
    _ORACLE_INCORRECT_DIAGNOSES,
    _PRIMARY_TIER_ORDER,
    _REPLAY_BUG_DIAGNOSES,
    _section_similarity,
)

_ALIGN_SPARSE_OMISSION_KIND = "ELAB.ALIGN_SPARSE_OMISSION_TO_LIVE"
_PAYLOAD_COMPLETENESS_KIND = "ELAB.PAYLOAD_COMPLETENESS"
_DETERMINISTIC_SUBSECTION_HELPER = "_apply_deterministic_subsection_op"


def _is_deterministic_sparse_oracle_stale_support(support: Mapping[str, Any]) -> bool:
    if not support:
        return False
    if bool(support.get("preexisting_before_any_drop")):
        return False
    if bool(support.get("blame_payload_prefers_replay")):
        return False
    if bool(support.get("blame_only_repeal_without_payload")):
        return False
    first_drop_source = str(support.get("first_drop_source") or "")
    blame_source = str(support.get("blame_source") or "")
    if not first_drop_source or not blame_source or first_drop_source == blame_source:
        return False
    drop_sources = {
        str(item.get("source_id") or "")
        for item in list(support.get("worst_drops") or [])
        if str(item.get("source_id") or "")
    }
    if len(drop_sources) < 2:
        return False

    def _only_align_kind(field: str) -> bool:
        kinds = [str(item or "") for item in list(support.get(field) or []) if str(item or "")]
        return bool(kinds) and set(kinds) == {_ALIGN_SPARSE_OMISSION_KIND}

    def _only_deterministic_helper(field: str) -> bool:
        helpers = [str(item or "") for item in list(support.get(field) or []) if str(item or "")]
        return bool(helpers) and set(helpers) == {_DETERMINISTIC_SUBSECTION_HELPER}

    return (
        _only_align_kind("first_drop_elaboration_kinds")
        and _only_align_kind("blame_elaboration_kinds")
        and bool(int(support.get("first_drop_sparse_slot_binding_count") or 0) > 0)
        and bool(int(support.get("blame_sparse_slot_binding_count") or 0) > 0)
        and int(support.get("first_drop_sparse_leftover_count") or 0) == 0
        and int(support.get("blame_sparse_leftover_count") or 0) == 0
        and _only_deterministic_helper("first_drop_apply_helpers_for_section")
        and _only_deterministic_helper("blame_apply_helpers_for_section")
    )


def _is_deterministic_payload_completeness_oracle_stale_support(
    support: Mapping[str, Any],
) -> bool:
    if not support:
        return False
    if bool(support.get("preexisting_before_any_drop")):
        return False
    if bool(support.get("blame_payload_prefers_replay")):
        return False
    if bool(support.get("blame_only_repeal_without_payload")):
        return False
    if isinstance(support.get("baseline_unmatched_oracle_subsections"), dict):
        return False
    first_drop_source = str(support.get("first_drop_source") or "")
    blame_source = str(support.get("blame_source") or "")
    if not first_drop_source or not blame_source:
        return False

    def _only_payload_completeness(field: str) -> bool:
        kinds = [str(item or "") for item in list(support.get(field) or []) if str(item or "")]
        return bool(kinds) and set(kinds) == {_PAYLOAD_COMPLETENESS_KIND}

    def _only_deterministic_helper(field: str) -> bool:
        helpers = [str(item or "") for item in list(support.get(field) or []) if str(item or "")]
        return bool(helpers) and set(helpers) == {_DETERMINISTIC_SUBSECTION_HELPER}

    return (
        bool(support.get("blame_body_has_section_payload"))
        and _only_payload_completeness("first_drop_elaboration_kinds")
        and _only_payload_completeness("blame_elaboration_kinds")
        and bool(int(support.get("first_drop_sparse_slot_binding_count") or 0) > 0)
        and bool(int(support.get("blame_sparse_slot_binding_count") or 0) > 0)
        and int(support.get("first_drop_sparse_leftover_count") or 0) == 0
        and int(support.get("blame_sparse_leftover_count") or 0) == 0
        and _only_deterministic_helper("first_drop_apply_helpers_for_section")
        and _only_deterministic_helper("blame_apply_helpers_for_section")
    )


def _build_section_claims(
    *,
    section_results: List[Dict],
    section_bisect: Optional[List[Dict]] = None,
    alternative_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    oracle_range_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    html_topology: Optional[Dict[str, Any]] = None,
    strict_fail_reasons: Optional[List[str]] = None,
    timeline_addresses: Optional[set[str]] = None,
    oracle_suspect_detail: str = "",
    section_strict_verdicts: Optional[Dict[str, Any]] = None,
    section_invariant_violations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[Dict]:
    support_by_section = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if str(item.get("section") or "")
    }
    rows: List[Dict] = []
    for item in section_results:
        section = str(item.get("section") or "")
        diagnosis = str(item.get("diagnosis") or "")
        blame_source = str(item.get("blame_source") or "")
        # Use pre-computed similarity when available (from build_evidence_bundle),
        # avoiding redundant O(n*m) Levenshtein computation per section.
        _precomputed_sim = item.get("similarity")
        if _precomputed_sim is not None:
            similarity = round(float(_precomputed_sim), 6)
        else:
            similarity = round(
                _section_similarity(
                    str(item.get("replay_text") or ""),
                    str(item.get("oracle_text") or ""),
                ),
                6,
            )
        support = support_by_section.get(section) or {}
        alternative_match = (alternative_replay_matches or {}).get(section) or {}
        oracle_range_match = (oracle_range_matches or {}).get(section) or {}
        cross_chapter_oracle_match = (cross_chapter_oracle_matches or {}).get(section) or {}
        cross_chapter_replay_match = (cross_chapter_replay_matches or {}).get(section) or {}
        html_noncommensurable_reason = _scoped_html_noncommensurable_reason(
            section, html_topology
        )
        baseline_alternative_match = (
            (support.get("baseline_alternative_replay_match") or {})
            if isinstance(support.get("baseline_alternative_replay_match"), dict)
            else {}
        )
        baseline_same_section_structure_drift = (
            (support.get("baseline_unmatched_oracle_subsections") or {})
            if isinstance(support.get("baseline_unmatched_oracle_subsections"), dict)
            else {}
        )
        negligible_blame_drop_on_preexisting_residue = _has_negligible_blame_drop_on_preexisting_residue(
            support
        )
        candidates: List[Dict] = []

        if diagnosis in _ORACLE_INCORRECT_DIAGNOSES:
            candidates.append(
                {
                    "tier": "PROVED_ORACLE_INCORRECT",
                    "kind": "oracle_section_stale",
                    "inference_rule": "oracle_stale_section_diagnosis_present",
                    "observation_sources": ["oracle_check"],
                    "support": {},
                }
            )
        # B2: Temporal impossibility at cutoff — if the oracle is suspect
        # (version effective date > cutoff date), the oracle is presenting a
        # temporally ineligible version. This is a proved oracle error.
        if (
            oracle_suspect_detail
            and not candidates
            and diagnosis in _REPLAY_BUG_DIAGNOSES
        ):
            candidates.append(
                {
                    "tier": "PROVED_ORACLE_INCORRECT",
                    "kind": "oracle_temporal_impossibility",
                    "inference_rule": (
                        "oracle_version_effective_date_exceeds_cutoff_"
                        "therefore_oracle_presents_temporally_ineligible_state"
                    ),
                    "observation_sources": ["oracle_check", "timeline"],
                    "support": {
                        "oracle_suspect_detail": oracle_suspect_detail,
                    },
                }
            )
        if not candidates and diagnosis in _REPLAY_BUG_DIAGNOSES:
            # EXTRA sections with no oracle text at all AND no alternative match.
            # Pro audit finding #10: empty oracle_text is not proof of contentAbsent —
            # it could be parse/fetch/extraction loss. Demote to UNRESOLVED unless
            # we have explicit contentAbsent evidence. The replay has content the
            # oracle doesn't, so we can't blame replay — but we also can't prove
            # oracle is wrong without checking the contentAbsent flag.
            if (
                diagnosis == "EXTRA"
                and not str(item.get("oracle_text") or "").strip()
                and not cross_chapter_oracle_match
                and not alternative_match
                and not oracle_range_match
            ):
                _explicit_absent = bool(item.get("oracle_content_absent"))
                candidates.append(
                    {
                        "tier": "PROVED_ORACLE_INCORRECT" if _explicit_absent else "UNRESOLVED",
                        "kind": "oracle_section_stale" if _explicit_absent else "UNRESOLVED.source_underdetermined.oracle_text_empty_unverified",
                        "inference_rule": (
                            "oracle_content_absent_replay_has_content"
                            if _explicit_absent
                            else "oracle_text_empty_but_contentAbsent_not_verified"
                        ),
                        "observation_sources": ["oracle_check"],
                        "support": {
                            "reason": (
                                "oracle is contentAbsent — no consolidated text available"
                                if _explicit_absent
                                else "oracle text is empty but contentAbsent flag not explicitly checked"
                            ),
                            "explicit_content_absent": _explicit_absent,
                        },
                    }
                )
            if (
                not str(item.get("oracle_text") or "").strip()
                and html_noncommensurable_reason.startswith("duplicate_unscoped_oracle_labels:")
            ):
                candidates.append(
                    {
                        "tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                        "kind": "html_xml_scope_noncommensurable",
                        "inference_rule": "empty_oracle_section_with_duplicate_unscoped_oracle_labels",
                        "observation_sources": ["html_topology", "oracle_check"],
                        "support": {
                            "noncommensurable_reason": html_noncommensurable_reason,
                        },
                    }
                )
            if oracle_range_match:
                candidates.append(
                    {
                        "tier": "PROVED_ORACLE_INCORRECT",
                        "kind": "same_chapter_oracle_range_drift",
                        "inference_rule": (
                            "oracle_uses_same_chapter_section_range_instead_of_exact_section_label"
                        ),
                        "observation_sources": ["oracle_check"],
                        "support": {
                            "oracle_range_section": oracle_range_match.get("oracle_range_section"),
                            "oracle_range_label": oracle_range_match.get("oracle_range_label"),
                        },
                    }
                )
            if cross_chapter_oracle_match:
                _cc_score = float(cross_chapter_oracle_match.get("oracle_section_score") or 0.0)
                _cc_runner_up = float(
                    cross_chapter_oracle_match.get("runner_up_oracle_section_score") or 0.0
                )
                if _cc_score >= 0.95 and _cc_score >= (_cc_runner_up + 0.05):
                    candidates.append(
                        {
                            "tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                            "kind": "address_relocation_cross_chapter_exact",
                            "inference_rule": "oracle_matches_same_label_section_in_different_chapter",
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "oracle_section": cross_chapter_oracle_match.get("oracle_section"),
                                "oracle_section_score": cross_chapter_oracle_match.get("oracle_section_score"),
                                "same_section_score": cross_chapter_oracle_match.get("same_section_score"),
                                "runner_up_oracle_section": cross_chapter_oracle_match.get("runner_up_oracle_section"),
                                "runner_up_oracle_section_score": cross_chapter_oracle_match.get("runner_up_oracle_section_score"),
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.address_projection.cross_chapter_oracle_drift",
                            "inference_rule": "oracle_matches_same_label_section_in_different_chapter",
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "oracle_section": cross_chapter_oracle_match.get("oracle_section"),
                                "oracle_section_score": cross_chapter_oracle_match.get("oracle_section_score"),
                                "same_section_score": cross_chapter_oracle_match.get("same_section_score"),
                                "runner_up_oracle_section": cross_chapter_oracle_match.get("runner_up_oracle_section"),
                                "runner_up_oracle_section_score": cross_chapter_oracle_match.get("runner_up_oracle_section_score"),
                            },
                        }
                    )
            if cross_chapter_replay_match:
                _cr_score = float(cross_chapter_replay_match.get("replay_section_score") or 0.0)
                _runner_up_score = float(
                    cross_chapter_replay_match.get("runner_up_replay_section_score") or 0.0
                )
                if _cr_score >= 0.95 and _cr_score >= (_runner_up_score + 0.05):
                    candidates.append(
                        {
                            "tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                            "kind": "address_relocation_cross_chapter_exact",
                            "inference_rule": "replay_matches_same_label_section_in_different_chapter_than_oracle",
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "replay_section": cross_chapter_replay_match.get("replay_section"),
                                "replay_section_score": cross_chapter_replay_match.get("replay_section_score"),
                                "same_section_score": cross_chapter_replay_match.get("same_section_score"),
                                "runner_up_replay_section": cross_chapter_replay_match.get("runner_up_replay_section"),
                                "runner_up_replay_section_score": cross_chapter_replay_match.get("runner_up_replay_section_score"),
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.address_projection.cross_chapter_replay_drift",
                            "inference_rule": "replay_matches_same_label_section_in_different_chapter_than_oracle",
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "replay_section": cross_chapter_replay_match.get("replay_section"),
                                "replay_section_score": cross_chapter_replay_match.get("replay_section_score"),
                                "same_section_score": cross_chapter_replay_match.get("same_section_score"),
                                "runner_up_replay_section": cross_chapter_replay_match.get("runner_up_replay_section"),
                                "runner_up_replay_section_score": cross_chapter_replay_match.get("runner_up_replay_section_score"),
                            },
                        }
                    )
            if bool(support.get("preexisting_before_any_drop")):
                _baseline_score = float(support.get("baseline_score") or 0.0)
                if _baseline_score >= 0.95:
                    candidates.append(
                        {
                            "tier": "PROVED_ORACLE_INCORRECT",
                            "kind": "oracle_editorial_drift_baseline_witness",
                            "inference_rule": (
                                "divergence_predates_all_amendments_and_baseline_replay_"
                                "matches_base_statute_therefore_oracle_is_editorial"
                            ),
                            "observation_sources": ["section_bisect", "baseline_witness"],
                            "support": {
                                "baseline_score": _baseline_score,
                                "first_bad_source": support.get("first_bad_source"),
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.preexisting.baseline_residue",
                            "inference_rule": "replay_residue_predates_any_amendment_drop",
                            "observation_sources": ["section_bisect"],
                            "support": {
                                "baseline_score": support.get("baseline_score"),
                                "first_bad_source": support.get("first_bad_source"),
                            },
                        }
                    )
            elif negligible_blame_drop_on_preexisting_residue:
                _baseline_score_neg = float(support.get("baseline_score") or 0.0)
                if _baseline_score_neg >= 0.95:
                    candidates.append(
                        {
                            "tier": "PROVED_ORACLE_INCORRECT",
                            "kind": "oracle_editorial_drift_baseline_witness",
                            "inference_rule": (
                                "divergence_predates_all_amendments_and_baseline_replay_"
                                "matches_base_statute_therefore_oracle_is_editorial"
                            ),
                            "observation_sources": ["section_bisect", "baseline_witness"],
                            "support": {
                                "baseline_score": _baseline_score_neg,
                                "first_bad_source": support.get("first_bad_source"),
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.preexisting.baseline_residue",
                            "inference_rule": "material_divergence_predates_blamed_change_and_blame_delta_is_negligible",
                            "observation_sources": ["section_bisect", "section_trace"],
                            "support": {
                                "baseline_score": support.get("baseline_score"),
                                "first_bad_source": support.get("first_bad_source"),
                                "blame_before_score": support.get("blame_before_score"),
                                "blame_after_score": support.get("blame_after_score"),
                                "blame_delta": (
                                    float(support.get("blame_before_score") or 0.0)
                                    - float(support.get("blame_after_score") or 0.0)
                                ),
                            },
                        }
                    )
            if baseline_same_section_structure_drift:
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.preexisting.same_section_structure_drift",
                        "inference_rule": (
                            "oracle_has_unmatched_same_section_subsection_fragments_"
                            "before_blamed_amendment"
                        ),
                        "observation_sources": ["section_bisect", "oracle_check"],
                        "support": {
                            "unmatched_oracle_subsection_count": baseline_same_section_structure_drift.get("count"),
                            "unmatched_oracle_subsection_excerpts": list(
                                baseline_same_section_structure_drift.get("oracle_text_excerpts", []) or []
                            ),
                            "max_best_replay_score": baseline_same_section_structure_drift.get(
                                "max_best_replay_score"
                            ),
                        },
                    }
                )
            if bool(support.get("blame_only_repeal_without_payload")):
                candidates.append(
                    {
                        "tier": "PROVED_SOURCE_PATHOLOGY",
                        "kind": "blamed_source_lacks_payload_support",
                        "inference_rule": "blamed_amendment_has_only_repeal_support_without_section_payload",
                        "observation_sources": ["source_payload", "section_bisect"],
                        "support": {
                            "compiled_actions": list(support.get("blame_compiled_actions_for_section", []) or []),
                        },
                    }
                )
            if bool(support.get("blame_payload_prefers_replay")):
                candidates.append(
                    {
                        "tier": "PROVED_SOURCE_PATHOLOGY",
                        "kind": "blamed_source_payload_prefers_replay",
                        "inference_rule": "blamed_section_payload_matches_replay_better_than_oracle",
                        "observation_sources": ["source_payload", "section_bisect"],
                        "support": {
                            "payload_vs_replay_score": support.get("blame_payload_vs_replay_score"),
                            "payload_vs_oracle_score": support.get("blame_payload_vs_oracle_score"),
                        },
                    }
                )
            if (
                _is_deterministic_sparse_oracle_stale_support(support)
                or _is_deterministic_payload_completeness_oracle_stale_support(support)
            ):
                candidates.append(
                    {
                        "tier": "PROVED_ORACLE_INCORRECT",
                        "kind": "oracle_section_stale",
                        "inference_rule": (
                            "deterministic_payload_completeness_same_section_drop_"
                            "leaves_oracle_stale"
                            if _is_deterministic_payload_completeness_oracle_stale_support(support)
                            else "deterministic_sparse_same_section_drops_leave_oracle_stale"
                        ),
                        "observation_sources": ["section_bisect", "elaboration", "apply_mutation"],
                        "support": {
                            "first_drop_source": str(support.get("first_drop_source") or ""),
                            "blame_source": blame_source,
                            "drop_sources": sorted(
                                {
                                    str(item.get("source_id") or "")
                                    for item in list(support.get("worst_drops") or [])
                                    if str(item.get("source_id") or "")
                                }
                            ),
                            "observation_kinds": list(
                                support.get("blame_elaboration_kinds", []) or []
                            ),
                            "first_drop_binding_labels": list(
                                support.get("first_drop_sparse_slot_binding_labels", []) or []
                            ),
                            "blame_binding_labels": list(
                                support.get("blame_sparse_slot_binding_labels", []) or []
                            ),
                        },
                    }
                )
            if bool(support.get("blame_sparse_elaboration")):
                inference_rule = "blamed_amendment_has_same_section_elaboration_observation"
                if (
                    int(support.get("blame_sparse_leftover_count") or 0) > 0
                    and not list(support.get("blame_elaboration_kinds", []) or [])
                ):
                    inference_rule = "blamed_amendment_has_same_section_sparse_leftovers"
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
                        "inference_rule": inference_rule,
                        "observation_sources": ["elaboration", "apply_mutation", "section_bisect"],
                        "support": {
                            "observation_kinds": list(support.get("blame_elaboration_kinds", []) or []),
                            "sparse_slot_binding_count": int(
                                support.get("blame_sparse_slot_binding_count") or 0
                            ),
                            "sparse_slot_binding_labels": list(
                                support.get("blame_sparse_slot_binding_labels", []) or []
                            ),
                            "sparse_leftover_count": int(
                                support.get("blame_sparse_leftover_count") or 0
                            ),
                            "apply_helpers": list(support.get("blame_apply_helpers_for_section", []) or []),
                        },
                    }
                )
            if (
                bool(support.get("first_drop_sparse_elaboration"))
                and str(support.get("first_drop_source") or "")
                and str(support.get("first_drop_source") or "") != blame_source
            ):
                inference_rule = "first_drop_amendment_has_same_section_elaboration_observation"
                if (
                    int(support.get("first_drop_sparse_leftover_count") or 0) > 0
                    and not list(support.get("first_drop_elaboration_kinds", []) or [])
                ):
                    inference_rule = "first_drop_amendment_has_same_section_sparse_leftovers"
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.preexisting.elaboration_ambiguity",
                        "inference_rule": inference_rule,
                        "observation_sources": ["elaboration", "apply_mutation", "section_bisect"],
                        "support": {
                            "first_drop_source": str(support.get("first_drop_source") or ""),
                            "observation_kinds": list(
                                support.get("first_drop_elaboration_kinds", []) or []
                            ),
                            "sparse_slot_binding_count": int(
                                support.get("first_drop_sparse_slot_binding_count") or 0
                            ),
                            "sparse_slot_binding_labels": list(
                                support.get("first_drop_sparse_slot_binding_labels", []) or []
                            ),
                            "sparse_leftover_count": int(
                                support.get("first_drop_sparse_leftover_count") or 0
                            ),
                            "apply_helpers": list(
                                support.get("first_drop_apply_helpers_for_section", []) or []
                            ),
                        },
                    }
                )
            if bool(support.get("blame_source_improved_or_equal")):
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.source_underdetermined.amendment_improves_section",
                        "inference_rule": "blamed_amendment_improves_or_preserves_section_similarity",
                        "observation_sources": ["section_trace"],
                        "support": {
                            "before_score": support.get("blame_before_score"),
                            "after_score": support.get("blame_after_score"),
                        },
                    }
                )
            if baseline_alternative_match:
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.address_projection.same_chapter_section_drift",
                        "inference_rule": (
                            "preexisting_same_chapter_replay_section_matches_oracle_"
                            "better_than_same_number_section"
                        ),
                        "observation_sources": ["section_bisect", "oracle_check"],
                        "support": {
                            "best_replay_section": baseline_alternative_match.get("best_replay_section"),
                            "best_replay_score": baseline_alternative_match.get("best_replay_score"),
                            "same_section_score": baseline_alternative_match.get("same_section_score"),
                        },
                    }
                )
            if alternative_match:
                _alt_score = float(alternative_match.get("best_replay_score") or 0.0)
                if _alt_score >= 0.95:
                    candidates.append(
                        {
                            "tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                            "kind": "address_relocation_same_chapter_exact",
                            "inference_rule": (
                                "same_chapter_replay_section_matches_oracle_better_"
                                "than_same_number_section"
                            ),
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "best_replay_section": alternative_match.get("best_replay_section"),
                                "best_replay_score": alternative_match.get("best_replay_score"),
                                "same_section_score": alternative_match.get("same_section_score"),
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.address_projection.same_chapter_replay_drift",
                            "inference_rule": (
                                "same_chapter_replay_section_matches_oracle_better_"
                                "than_same_number_section"
                            ),
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "best_replay_section": alternative_match.get("best_replay_section"),
                                "best_replay_score": alternative_match.get("best_replay_score"),
                                "same_section_score": alternative_match.get("same_section_score"),
                            },
                        }
                    )
            if (
                not blame_source
                and not any(
                    "preexisting" in str(candidate.get("kind") or "") and "baseline_residue" in str(candidate.get("kind") or "")
                    for candidate in candidates
                )
            ):
                # Defensible rule: if section has no blame (no amendment ever
                # touched it) AND no timeline entry exists for it, the oracle
                # text is editorial — the section was never modified by any
                # amendment, so any divergence is oracle editorial drift.
                # Pro review: use exact canonical address suffix matching,
                # not string containment. The section label must match as
                # the final "section:LABEL" component of the address path.
                _section_suffix = f"section:{section}"
                _has_timeline = (
                    timeline_addresses is not None
                    and any(
                        addr == _section_suffix
                        or addr.endswith(f"/{_section_suffix}")
                        for addr in timeline_addresses
                    )
                ) if timeline_addresses is not None else None
                if _has_timeline is False:
                    candidates.append(
                        {
                            "tier": "PROVED_ORACLE_INCORRECT",
                            "kind": "oracle_editorial_drift_no_timeline",
                            "inference_rule": (
                                "section_has_no_blamed_amendment_and_no_timeline_entry_"
                                "therefore_oracle_text_is_editorial_not_legislative"
                            ),
                            "observation_sources": ["oracle_check", "timeline_invariants"],
                            "support": {
                                "diagnosis": diagnosis,
                                "similarity": similarity,
                                "timeline_present": False,
                            },
                        }
                    )
                else:
                    candidates.append(
                        {
                            "tier": "UNRESOLVED",
                            "kind": "UNRESOLVED.preexisting.baseline_residue",
                            "inference_rule": "residual_replay_divergence_has_no_blamed_amendment",
                            "observation_sources": ["oracle_check"],
                            "support": {
                                "diagnosis": diagnosis,
                                "similarity": similarity,
                            },
                        }
                    )
            # Defensible rule: if the statute has extraction_fallback and this
            # section's divergence could be caused by missed extraction, demote
            # from PROVED_REPLAY_BUG to UNRESOLVED with extraction_gap kind.
            # This is NOT a heuristic — extraction_fallback means we provably
            # did not extract all ops, so blaming replay is unsupported.
            _sfr = set(strict_fail_reasons or [])
            _has_extraction_gap = bool(
                _sfr & {"PARSE.EXTRACTION_FALLBACK", "extraction_fallback_required"}
            )
            if not candidates and _has_extraction_gap:
                candidates.append(
                    {
                        "tier": "UNRESOLVED",
                        "kind": "UNRESOLVED.source_underdetermined.extraction_coverage_gap",
                        "inference_rule": (
                            "statute_has_extraction_fallback_so_replay_divergence_"
                            "cannot_be_attributed_to_replay_logic"
                        ),
                        "observation_sources": ["oracle_check", "compile_result"],
                        "support": {
                            "diagnosis": diagnosis,
                            "similarity": similarity,
                            "extraction_fallback": True,
                        },
                    }
                )
            # C1: Section-local strict lineage — if the blamed amendment's
            # section-local verdict is not strict_clean, the divergence cannot
            # be cleanly attributed to replay logic alone.  Source/recovery
            # barriers demote to UNRESOLVED.
            if not candidates and section_strict_verdicts:
                _ssv = section_strict_verdicts.get(section)
                if _ssv is not None and not getattr(_ssv, "is_strict_clean", True):
                    _barrier_kinds = getattr(_ssv, "barrier_kinds", set())
                    _barrier_families = getattr(_ssv, "barrier_families", set())
                    _source_families = {"source", "extraction"}
                    _recovery_families = {"recovery", "resolution", "temporal", "text_level"}
                    if _barrier_families & _source_families:
                        candidates.append(
                            {
                                "tier": "UNRESOLVED",
                                "kind": "UNRESOLVED.source_underdetermined.section_strict_lineage",
                                "inference_rule": (
                                    "blamed_amendment_section_has_source_or_extraction_"
                                    "strict_barriers_so_replay_attribution_unsupported"
                                ),
                                "observation_sources": ["compile_result", "section_strict_lineage"],
                                "support": {
                                    "amendment_id": getattr(_ssv, "amendment_id", ""),
                                    "status": getattr(_ssv, "status", ""),
                                    "barrier_kinds": sorted(_barrier_kinds),
                                    "barrier_families": sorted(_barrier_families),
                                },
                            }
                        )
                    elif _barrier_families & _recovery_families:
                        candidates.append(
                            {
                                "tier": "UNRESOLVED",
                                "kind": "UNRESOLVED.source_underdetermined.section_recovery_barriers",
                                "inference_rule": (
                                    "blamed_amendment_section_required_recovery_paths_"
                                    "so_replay_divergence_may_be_recovery_artifact"
                                ),
                                "observation_sources": ["compile_result", "section_strict_lineage"],
                                "support": {
                                    "amendment_id": getattr(_ssv, "amendment_id", ""),
                                    "status": getattr(_ssv, "status", ""),
                                    "barrier_kinds": sorted(_barrier_kinds),
                                    "barrier_families": sorted(_barrier_families),
                                },
                            }
                        )
            # C3: Section-local invariant breach → PROVED_REPLAY_BUG.
            # Timeline invariant violations are direct proof of replay bugs.
            if section_invariant_violations:
                _inv_violations = section_invariant_violations.get(section, [])
                if _inv_violations:
                    candidates.append(
                        {
                            "tier": "PROVED_REPLAY_BUG",
                            "kind": "timeline_invariant_violation",
                            "inference_rule": (
                                "section_has_timeline_invariant_violation_"
                                "therefore_replay_state_is_inconsistent"
                            ),
                            "observation_sources": ["timeline_invariants"],
                            "support": {
                                "violation_count": len(_inv_violations),
                                "violation_kinds": sorted({
                                    str(v.get("kind", "")) for v in _inv_violations
                                    if str(v.get("kind", ""))
                                }),
                                "violations": _inv_violations[:5],
                            },
                        }
                    )
            if not candidates:
                replay_support: Dict[str, Any] = {}
                if alternative_match:
                    replay_support.update(
                        {
                            "best_replay_section": alternative_match.get("best_replay_section"),
                            "best_replay_score": alternative_match.get("best_replay_score"),
                            "same_section_score": alternative_match.get("same_section_score"),
                        }
                    )
                candidates.append(
                    {
                        "tier": "PROVED_REPLAY_BUG",
                        "kind": "replay_divergence",
                        "inference_rule": "residual_replay_bug_diagnosis_present",
                        "observation_sources": ["oracle_check"],
                        "support": replay_support,
                    }
                )

        # Pro audit finding #9: sort candidates by tier proof-strength so
        # that a stronger proof always wins over a weaker one, regardless
        # of append order. This is not a heuristic — tier ordering is a
        # logical priority (proved > unresolved > replay_bug).
        _TIER_PRIORITY = {
            "PROVED_ORACLE_INCORRECT": 0,
            "PROVED_SOURCE_PATHOLOGY": 1,
            "PROVED_HTML_XML_NONCOMMENSURABLE": 2,
            "UNRESOLVED": 3,
            "PROVED_REPLAY_BUG": 4,
        }
        if len(candidates) > 1:
            candidates.sort(key=lambda c: _TIER_PRIORITY.get(str(c.get("tier") or ""), 99))

        # B4: Strict source payload confidence — how trustworthy is the
        # compilation path for this section? Derives from C1 section-local
        # strict verdict. "strict_clean" = highest confidence (no recovery,
        # no source pathology). Machine-readable for downstream consumers.
        _payload_confidence = "unknown"
        if section_strict_verdicts:
            _ssv_b4 = section_strict_verdicts.get(section)
            if _ssv_b4 is not None:
                _status_b4 = getattr(_ssv_b4, "status", "")
                if _status_b4 == "strict_clean":
                    _payload_confidence = "strict_clean"
                elif _status_b4 == "source_incomplete":
                    _payload_confidence = "source_incomplete"
                elif _status_b4 == "strict_blocked_by_recovery":
                    _payload_confidence = "recovery_dependent"
                else:
                    _payload_confidence = "degraded"

        rows.append(
            {
                "section": section,
                "diagnosis": diagnosis,
                "blame_source": blame_source,
                "similarity": similarity,
                "strict_payload_confidence": _payload_confidence,
                "selected_kind": str(candidates[0].get("kind") or "") if candidates else "",
                "selected_tier": str(candidates[0].get("tier") or "") if candidates else "",
                "selected_inference_rule": (
                    str(candidates[0].get("inference_rule") or "") if candidates else ""
                ),
                "oracle_range_match": oracle_range_match or None,
                "cross_chapter_oracle_match": cross_chapter_oracle_match or None,
                "alternative_replay_match": alternative_match or None,
                "candidate_count": len(candidates),
                "candidate_kinds": [str(candidate.get("kind") or "") for candidate in candidates if str(candidate.get("kind") or "")],
                "defeated_candidate_kinds": [
                    str(candidate.get("kind") or "")
                    for candidate in candidates[1:]
                    if str(candidate.get("kind") or "")
                ],
                "defeated_candidates": [
                    {
                        "kind": str(candidate.get("kind") or ""),
                        "tier": str(candidate.get("tier") or ""),
                        "inference_rule": str(candidate.get("inference_rule") or ""),
                        "defeated_by_kind": str(candidates[0].get("kind") or ""),
                        "defeated_by_inference_rule": str(candidates[0].get("inference_rule") or ""),
                        "defeated_by_observation_sources": [
                            str(source or "")
                            for source in (candidates[0].get("observation_sources") or [])
                            if str(source or "")
                        ],
                    }
                    for candidate in candidates[1:]
                    if str(candidate.get("kind") or "")
                ],
                "candidates": candidates,
            }
        )

    return rows


def _build_proof_claims(
    *,
    section_results: List[Dict],
    source_pathologies: List[Dict],
    html_topology: Dict,
    contingent_effective_sources: List[str],
    corrigendum_support: List[Dict],
    oracle_suspect_detail: str = "",
    oracle_suspect_pending: str = "",
    section_bisect: Optional[List[Dict]] = None,
    alternative_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    oracle_range_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    section_claims: Optional[List[Dict]] = None,
) -> List[Dict]:
    claims: List[Dict] = []
    stale_sections: List[Dict] = []
    replay_bug_sections: List[Dict] = []
    for item in section_results:
        diag = str(item.get("diagnosis") or "")
        # Use pre-computed similarity when available (from build_evidence_bundle),
        # avoiding redundant O(n*m) Levenshtein computation per section.
        _precomputed_sim = item.get("similarity")
        if _precomputed_sim is not None:
            _sim_value = round(float(_precomputed_sim), 6)
        else:
            _sim_value = round(
                _section_similarity(
                    str(item.get("replay_text") or ""),
                    str(item.get("oracle_text") or ""),
                ),
                6,
            )
        sec = {
            "section": str(item.get("section") or ""),
            "diagnosis": diag,
            "blame_source": str(item.get("blame_source") or ""),
            "blame_title": str(item.get("blame_title") or ""),
            "similarity": _sim_value,
        }
        if diag in _ORACLE_INCORRECT_DIAGNOSES:
            stale_sections.append(sec)
        elif diag in _REPLAY_BUG_DIAGNOSES:
            replay_bug_sections.append(sec)

    bisect_preexisting = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if bool(item.get("preexisting_before_any_drop"))
    }
    bisect_preexisting_by_source_chapter_label, bisect_preexisting_by_source_label = (
        _build_support_lookup_maps(
            [item for item in (section_bisect or []) if bool(item.get("preexisting_before_any_drop"))]
        )
    )
    bisect_negligible_preexisting_drop = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if _has_negligible_blame_drop_on_preexisting_residue(item)
    }
    (
        bisect_negligible_preexisting_drop_by_source_chapter_label,
        bisect_negligible_preexisting_drop_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if _has_negligible_blame_drop_on_preexisting_residue(item)
        ]
    )
    bisect_improved = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if bool(item.get("blame_source_improved_or_equal"))
    }
    bisect_improved_by_source_chapter_label, bisect_improved_by_source_label = (
        _build_support_lookup_maps(
            [item for item in (section_bisect or []) if bool(item.get("blame_source_improved_or_equal"))]
        )
    )
    bisect_repeal_only_without_payload = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if bool(item.get("blame_only_repeal_without_payload"))
    }
    (
        bisect_repeal_only_without_payload_by_source_chapter_label,
        bisect_repeal_only_without_payload_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if bool(item.get("blame_only_repeal_without_payload"))
        ]
    )
    bisect_payload_prefers_replay = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if bool(item.get("blame_payload_prefers_replay"))
    }
    bisect_payload_prefers_replay_by_source_chapter_label, bisect_payload_prefers_replay_by_source_label = (
        _build_support_lookup_maps(
            [item for item in (section_bisect or []) if bool(item.get("blame_payload_prefers_replay"))]
        )
    )
    bisect_sparse_elaboration = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if bool(item.get("blame_sparse_elaboration"))
    }
    (
        bisect_sparse_elaboration_by_source_chapter_label,
        bisect_sparse_elaboration_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if bool(item.get("blame_sparse_elaboration"))
        ]
    )
    bisect_deterministic_sparse_oracle_stale = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if (
            _is_deterministic_sparse_oracle_stale_support(item)
            or _is_deterministic_payload_completeness_oracle_stale_support(item)
        )
    }
    (
        bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
        bisect_deterministic_sparse_oracle_stale_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if (
                _is_deterministic_sparse_oracle_stale_support(item)
                or _is_deterministic_payload_completeness_oracle_stale_support(item)
            )
        ]
    )
    bisect_baseline_same_chapter_drift = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if isinstance(item.get("baseline_alternative_replay_match"), dict)
        and bool((item.get("baseline_alternative_replay_match") or {}).get("best_replay_section"))
    }
    (
        bisect_baseline_same_chapter_drift_by_source_chapter_label,
        bisect_baseline_same_chapter_drift_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if isinstance(item.get("baseline_alternative_replay_match"), dict)
            and bool((item.get("baseline_alternative_replay_match") or {}).get("best_replay_section"))
        ]
    )
    bisect_baseline_same_section_structure_drift = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if isinstance(item.get("baseline_unmatched_oracle_subsections"), dict)
        and bool((item.get("baseline_unmatched_oracle_subsections") or {}).get("count"))
    }
    (
        bisect_baseline_same_section_structure_drift_by_source_chapter_label,
        bisect_baseline_same_section_structure_drift_by_source_label,
    ) = _build_support_lookup_maps(
        [
            item
            for item in (section_bisect or [])
            if isinstance(item.get("baseline_unmatched_oracle_subsections"), dict)
            and bool((item.get("baseline_unmatched_oracle_subsections") or {}).get("count"))
        ]
    )

    preexisting_replay_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_preexisting,
            bisect_preexisting_by_source_chapter_label,
            bisect_preexisting_by_source_label,
        ) is not None
        or _lookup_support_row(
            sec,
            bisect_negligible_preexisting_drop,
            bisect_negligible_preexisting_drop_by_source_chapter_label,
            bisect_negligible_preexisting_drop_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_preexisting,
            bisect_preexisting_by_source_chapter_label,
            bisect_preexisting_by_source_label,
        ) is None
        and _lookup_support_row(
            sec,
            bisect_negligible_preexisting_drop,
            bisect_negligible_preexisting_drop_by_source_chapter_label,
            bisect_negligible_preexisting_drop_by_source_label,
        ) is None
    ]
    unsupported_replay_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_repeal_only_without_payload,
            bisect_repeal_only_without_payload_by_source_chapter_label,
            bisect_repeal_only_without_payload_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_repeal_only_without_payload,
            bisect_repeal_only_without_payload_by_source_chapter_label,
            bisect_repeal_only_without_payload_by_source_label,
        ) is None
    ]
    payload_supported_replay_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_payload_prefers_replay,
            bisect_payload_prefers_replay_by_source_chapter_label,
            bisect_payload_prefers_replay_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_payload_prefers_replay,
            bisect_payload_prefers_replay_by_source_chapter_label,
            bisect_payload_prefers_replay_by_source_label,
        ) is None
    ]
    deterministic_sparse_stale_sections = [
        {
            **sec,
            "first_drop_source": (
                (_lookup_support_row(
                    sec,
                    bisect_deterministic_sparse_oracle_stale,
                    bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
                    bisect_deterministic_sparse_oracle_stale_by_source_label,
                ) or {}).get("first_drop_source")
            ),
            "drop_sources": sorted(
                {
                    str(item.get("source_id") or "")
                    for item in list(
                        (
                            (_lookup_support_row(
                                sec,
                                bisect_deterministic_sparse_oracle_stale,
                                bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
                                bisect_deterministic_sparse_oracle_stale_by_source_label,
                            ) or {}).get("worst_drops")
                            or []
                        )
                    )
                    if str(item.get("source_id") or "")
                }
            ),
            "observation_kinds": list(
                (
                    (_lookup_support_row(
                        sec,
                        bisect_deterministic_sparse_oracle_stale,
                        bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
                        bisect_deterministic_sparse_oracle_stale_by_source_label,
                    ) or {}).get("blame_elaboration_kinds")
                    or []
                )
            ),
            "apply_helpers": list(
                (
                    (_lookup_support_row(
                        sec,
                        bisect_deterministic_sparse_oracle_stale,
                        bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
                        bisect_deterministic_sparse_oracle_stale_by_source_label,
                    ) or {}).get("blame_apply_helpers_for_section")
                    or []
                )
            ),
        }
        for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_deterministic_sparse_oracle_stale,
            bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
            bisect_deterministic_sparse_oracle_stale_by_source_label,
        ) is not None
    ]
    stale_sections.extend(deterministic_sparse_stale_sections)
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_deterministic_sparse_oracle_stale,
            bisect_deterministic_sparse_oracle_stale_by_source_chapter_label,
            bisect_deterministic_sparse_oracle_stale_by_source_label,
        ) is None
    ]
    elaboration_replay_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_sparse_elaboration,
            bisect_sparse_elaboration_by_source_chapter_label,
            bisect_sparse_elaboration_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_sparse_elaboration,
            bisect_sparse_elaboration_by_source_chapter_label,
            bisect_sparse_elaboration_by_source_label,
        ) is None
    ]
    baseline_same_chapter_drift_sections = [
        {
            **sec,
            "best_replay_section": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_chapter_drift,
                    bisect_baseline_same_chapter_drift_by_source_chapter_label,
                    bisect_baseline_same_chapter_drift_by_source_label,
                ) or {}).get("baseline_alternative_replay_match") or {}
            ).get("best_replay_section"),
            "best_replay_score": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_chapter_drift,
                    bisect_baseline_same_chapter_drift_by_source_chapter_label,
                    bisect_baseline_same_chapter_drift_by_source_label,
                ) or {}).get("baseline_alternative_replay_match") or {}
            ).get("best_replay_score"),
            "same_section_score": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_chapter_drift,
                    bisect_baseline_same_chapter_drift_by_source_chapter_label,
                    bisect_baseline_same_chapter_drift_by_source_label,
                ) or {}).get("baseline_alternative_replay_match") or {}
            ).get("same_section_score"),
        }
        for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_baseline_same_chapter_drift,
            bisect_baseline_same_chapter_drift_by_source_chapter_label,
            bisect_baseline_same_chapter_drift_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_baseline_same_chapter_drift,
            bisect_baseline_same_chapter_drift_by_source_chapter_label,
            bisect_baseline_same_chapter_drift_by_source_label,
        ) is None
    ]
    baseline_same_section_structure_drift_sections = [
        {
            **sec,
            "unmatched_oracle_subsection_count": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_section_structure_drift,
                    bisect_baseline_same_section_structure_drift_by_source_chapter_label,
                    bisect_baseline_same_section_structure_drift_by_source_label,
                ) or {}).get("baseline_unmatched_oracle_subsections") or {}
            ).get("count"),
            "unmatched_oracle_subsection_excerpts": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_section_structure_drift,
                    bisect_baseline_same_section_structure_drift_by_source_chapter_label,
                    bisect_baseline_same_section_structure_drift_by_source_label,
                ) or {}).get("baseline_unmatched_oracle_subsections") or {}
            ).get("oracle_text_excerpts"),
            "max_best_replay_score": (
                (_lookup_support_row(
                    sec,
                    bisect_baseline_same_section_structure_drift,
                    bisect_baseline_same_section_structure_drift_by_source_chapter_label,
                    bisect_baseline_same_section_structure_drift_by_source_label,
                ) or {}).get("baseline_unmatched_oracle_subsections") or {}
            ).get("max_best_replay_score"),
        }
        for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_baseline_same_section_structure_drift,
            bisect_baseline_same_section_structure_drift_by_source_chapter_label,
            bisect_baseline_same_section_structure_drift_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_baseline_same_section_structure_drift,
            bisect_baseline_same_section_structure_drift_by_source_chapter_label,
            bisect_baseline_same_section_structure_drift_by_source_label,
        ) is None
    ]
    oracle_range_drift_sections = [
        {
            **sec,
            "oracle_range_section": (oracle_range_matches or {}).get(sec["section"], {}).get("oracle_range_section"),
            "oracle_range_label": (oracle_range_matches or {}).get(sec["section"], {}).get("oracle_range_label"),
        }
        for sec in replay_bug_sections
        if (oracle_range_matches or {}).get(sec["section"])
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if not (oracle_range_matches or {}).get(sec["section"])
    ]
    exact_cross_chapter_oracle_sections = {
        str(row.get("section") or "")
        for row in (section_claims or [])
        if str(row.get("selected_kind") or "") == "address_relocation_cross_chapter_exact"
        and str(row.get("selected_tier") or "") == "PROVED_HTML_XML_NONCOMMENSURABLE"
        and str(row.get("section") or "")
    }
    cross_chapter_oracle_drift_sections = [
        {
            **sec,
            "oracle_section": (cross_chapter_oracle_matches or {}).get(sec["section"], {}).get("oracle_section"),
            "oracle_section_score": (cross_chapter_oracle_matches or {}).get(sec["section"], {}).get("oracle_section_score"),
            "same_section_score": (cross_chapter_oracle_matches or {}).get(sec["section"], {}).get("same_section_score"),
        }
        for sec in replay_bug_sections
        if (cross_chapter_oracle_matches or {}).get(sec["section"])
        and str(sec.get("section") or "") not in exact_cross_chapter_oracle_sections
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if not (cross_chapter_oracle_matches or {}).get(sec["section"])
        or str(sec.get("section") or "") in exact_cross_chapter_oracle_sections
    ]
    exact_cross_chapter_replay_sections = {
        str(row.get("section") or "")
        for row in (section_claims or [])
        if str(row.get("selected_kind") or "") == "address_relocation_cross_chapter_exact"
        and str(row.get("selected_tier") or "") == "PROVED_HTML_XML_NONCOMMENSURABLE"
        and str(row.get("section") or "")
    }
    cross_chapter_replay_drift_sections = [
        {
            **sec,
            "replay_section": (cross_chapter_replay_matches or {}).get(sec["section"], {}).get("replay_section"),
            "replay_section_score": (cross_chapter_replay_matches or {}).get(sec["section"], {}).get("replay_section_score"),
            "same_section_score": (cross_chapter_replay_matches or {}).get(sec["section"], {}).get("same_section_score"),
            "runner_up_replay_section": (cross_chapter_replay_matches or {}).get(sec["section"], {}).get("runner_up_replay_section"),
            "runner_up_replay_section_score": (cross_chapter_replay_matches or {}).get(sec["section"], {}).get("runner_up_replay_section_score"),
        }
        for sec in replay_bug_sections
        if (cross_chapter_replay_matches or {}).get(sec["section"])
        and str(sec.get("section") or "") not in exact_cross_chapter_replay_sections
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if not (cross_chapter_replay_matches or {}).get(sec["section"])
        or str(sec.get("section") or "") in exact_cross_chapter_replay_sections
    ]
    same_chapter_drift_sections = [
        {
            **sec,
            "best_replay_section": (alternative_replay_matches or {}).get(sec["section"], {}).get("best_replay_section"),
            "best_replay_score": (alternative_replay_matches or {}).get(sec["section"], {}).get("best_replay_score"),
            "same_section_score": (alternative_replay_matches or {}).get(sec["section"], {}).get("same_section_score"),
        }
        for sec in replay_bug_sections
        if (alternative_replay_matches or {}).get(sec["section"])
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if not (alternative_replay_matches or {}).get(sec["section"])
    ]
    improved_replay_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_improved,
            bisect_improved_by_source_chapter_label,
            bisect_improved_by_source_label,
        ) is not None
    ]
    replay_bug_sections = [
        sec for sec in replay_bug_sections
        if _lookup_support_row(
            sec,
            bisect_improved,
            bisect_improved_by_source_chapter_label,
            bisect_improved_by_source_label,
        ) is None
    ]

    noncomm_reason = str(html_topology.get("noncommensurable_reason") or "").strip()
    html_error = str(html_topology.get("html_error") or "").strip()
    missing_from_xml = [str(v) for v in html_topology.get("missing_from_xml", []) if str(v)]
    extra_in_xml = [str(v) for v in html_topology.get("extra_in_xml", []) if str(v)]

    if noncomm_reason:
        claims.append(
            {
                "tier": "PROVED_HTML_XML_NONCOMMENSURABLE",
                "kind": "html_xml_scope_noncommensurable",
                "summary": "Live Finlex HTML and consolidated XML are not commensurable for section-topology comparison.",
                "inference_rule": "html_noncommensurable_reason_present",
                "trigger_observations": [
                    _obs(
                        "html_topology",
                        "noncommensurable_reason",
                        noncomm_reason,
                        scope="statute",
                    )
                ],
                "support": {
                    "reason": noncomm_reason,
                },
            }
        )

    if html_error:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "html_fetch_error",
                "summary": "Live Finlex HTML fetch or parse failed; topology comparison is unavailable.",
                "inference_rule": "html_fetch_or_parse_failed",
                "trigger_observations": [
                    _obs(
                        "html_topology",
                        "html_error",
                        html_error,
                        scope="statute",
                    )
                ],
                "support": {
                    "html_error": html_error,
                },
            }
        )

    oracle_suspect_text = str(oracle_suspect_detail or "").strip()
    if oracle_suspect_text:
        claims.append(
            {
                "tier": "PROVED_ORACLE_INCORRECT",
                "kind": "oracle_metadata_inconsistency",
                "summary": (
                    "The consolidated oracle points to an oracle version amendment id whose "
                    "effective or expiry date is inconsistent with the published cutoff. "
                    "This is a metadata inconsistency — not necessarily a content error."
                ),
                "inference_rule": "oracle_version_mid_conflicts_with_consolidated_cutoff",
                "trigger_observations": [
                    _obs(
                        "oracle_version_gate",
                        "suspect_detail",
                        oracle_suspect_text,
                        scope="statute",
                    )
                ],
                "support": {
                    "suspect_detail": oracle_suspect_text,
                    "pending_detail": str(oracle_suspect_pending or ""),
                },
            }
        )

    if source_pathologies:
        grouped_codes = sorted(
            {
                str(item.get("code") or "")
                for item in source_pathologies
                if str(item.get("code") or "")
            }
        )
        grouped_sources = sorted(
            {
                str(item.get("source_statute") or "")
                for item in source_pathologies
                if str(item.get("source_statute") or "")
            }
        )
        claims.append(
            {
                "tier": "PROVED_SOURCE_PATHOLOGY",
                "kind": "source_pathology",
                "summary": "Replay encountered source publication pathologies in the amendment chain.",
                "inference_rule": "live_source_pathology_detected",
                "trigger_observations": [
                    _obs("source_pathology", "codes", grouped_codes, scope="statute"),
                    _obs("source_pathology", "source_statutes", grouped_sources, scope="statute"),
                ],
                "support": {
                    "codes": grouped_codes,
                    "source_statutes": grouped_sources,
                    "examples": source_pathologies[:10],
                },
            }
        )

    if contingent_effective_sources:
        claims.append(
            {
                "tier": "PROVED_SOURCE_PATHOLOGY",
                "kind": "contingent_effective_date",
                "summary": "Replay detected contingent effective-date dependencies that make plain consolidated comparison non-commensurable.",
                "inference_rule": "contingent_effective_sources_present",
                "trigger_observations": [
                    _obs(
                        "contingent_effective_date",
                        "source_statutes",
                        sorted({str(v) for v in contingent_effective_sources if str(v)})[:20],
                        scope="statute",
                    )
                ],
                "support": {
                    "source_statutes": sorted({str(v) for v in contingent_effective_sources if str(v)})[:20],
                },
            }
        )

    oracle_support = {
        "html_missing_from_xml": missing_from_xml,
        "html_extra_in_xml": extra_in_xml,
        "html_error": html_error,
        "sections": stale_sections[:20],
        "corrigenda": [
            item
            for item in corrigendum_support
            if item["official_item_count"] > 0 or item["manual_override_count"] > 0
        ][:20],
    }
    if (missing_from_xml or extra_in_xml or stale_sections) and not html_error:
        kind = "oracle_section_stale"
        summary = "Current evidence shows the consolidated oracle disagrees with replay for reasons classified as oracle-side stale/editorial state."
        if missing_from_xml or extra_in_xml:
            kind = "xml_html_topology_drift"
            summary = "Live Finlex HTML and consolidated XML disagree on section topology, which is evidence of oracle-side XML drift."
        claims.append(
            {
                "tier": "PROVED_ORACLE_INCORRECT",
                "kind": kind,
                "summary": summary,
                "inference_rule": (
                    "html_xml_topology_drift_detected"
                    if (missing_from_xml or extra_in_xml)
                    else "oracle_stale_sections_detected"
                ),
                "trigger_observations": (
                    [
                        _obs("html_topology", "missing_from_xml", missing_from_xml, scope="statute"),
                        _obs("html_topology", "extra_in_xml", extra_in_xml, scope="statute"),
                    ]
                    if (missing_from_xml or extra_in_xml)
                    else [
                        _obs("oracle_check", "oracle_stale_sections", stale_sections[:20], scope="sections"),
                    ]
                ),
                "support": oracle_support,
            }
        )

    if preexisting_replay_sections:
        preexisting_section_support = []
        for sec in preexisting_replay_sections:
            support_row = _lookup_support_row(
                sec,
                bisect_preexisting,
                bisect_preexisting_by_source_chapter_label,
                bisect_preexisting_by_source_label,
            )
            inference_rule = "replay_residue_predates_any_amendment_drop"
            if support_row is None:
                support_row = _lookup_support_row(
                    sec,
                    bisect_negligible_preexisting_drop,
                    bisect_negligible_preexisting_drop_by_source_chapter_label,
                    bisect_negligible_preexisting_drop_by_source_label,
                )
                inference_rule = (
                    "material_divergence_predates_blamed_change_and_blame_delta_is_negligible"
                )
            preexisting_section_support.append(
                {
                    **sec,
                    "baseline_score": (support_row or {}).get("baseline_score"),
                    "first_bad_source": (support_row or {}).get("first_bad_source"),
                    "blame_before_score": (support_row or {}).get("blame_before_score"),
                    "blame_after_score": (support_row or {}).get("blame_after_score"),
                    "blame_delta": (
                        (
                            float((support_row or {}).get("blame_before_score") or 0.0)
                            - float((support_row or {}).get("blame_after_score") or 0.0)
                        )
                        if (support_row or {}).get("blame_before_score") is not None
                        and (support_row or {}).get("blame_after_score") is not None
                        else None
                    ),
                    "inference_rule": inference_rule,
                }
            )
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.preexisting.baseline_residue",
                "summary": "Some replay-labeled residual sections were already materially divergent before the blamed amendment, or the blamed amendment only caused a negligible score drop on top of that preexisting divergence.",
                "inference_rule": "material_replay_residue_predates_blamed_change",
                "trigger_observations": [
                    _obs(
                        "section_bisect",
                        "preexisting_residual_sections",
                        preexisting_section_support,
                        scope="sections",
                    ),
                ],
                "support": {"sections": preexisting_section_support},
            }
        )

    if improved_replay_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.source_underdetermined.amendment_improves_section",
                "summary": "Some replay-labeled residual sections improve or hold steady across the blamed amendment, so current evidence does not support attributing those residuals to replay semantics in that amendment.",
                "inference_rule": "blamed_amendment_improves_or_preserves_section_similarity",
                "trigger_observations": [
                    _obs(
                        "section_trace",
                        "blame_source_improved_or_equal",
                        [
                            {
                                "section": sec["section"],
                                "blame_source": (_lookup_support_row(
                                    sec,
                                    bisect_improved,
                                    bisect_improved_by_source_chapter_label,
                                    bisect_improved_by_source_label,
                                ) or {}).get("blame_source"),
                                "before_score": (_lookup_support_row(
                                    sec,
                                    bisect_improved,
                                    bisect_improved_by_source_chapter_label,
                                    bisect_improved_by_source_label,
                                ) or {}).get("blame_before_score"),
                                "after_score": (_lookup_support_row(
                                    sec,
                                    bisect_improved,
                                    bisect_improved_by_source_chapter_label,
                                    bisect_improved_by_source_label,
                                ) or {}).get("blame_after_score"),
                            }
                            for sec in improved_replay_sections
                        ],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": [
                        {
                            **sec,
                            "blame_before_score": (_lookup_support_row(
                                sec,
                                bisect_improved,
                                bisect_improved_by_source_chapter_label,
                                bisect_improved_by_source_label,
                            ) or {}).get("blame_before_score"),
                            "blame_after_score": (_lookup_support_row(
                                sec,
                                bisect_improved,
                                bisect_improved_by_source_chapter_label,
                                bisect_improved_by_source_label,
                            ) or {}).get("blame_after_score"),
                        }
                        for sec in improved_replay_sections
                    ]
                },
            }
        )

    if unsupported_replay_sections:
        claims.append(
            {
                "tier": "PROVED_SOURCE_PATHOLOGY",
                "kind": "blamed_source_lacks_payload_support",
                "summary": "Some replay-labeled residual sections are blamed on amendments whose source XML carries no section payload and compiles only a repeal for that section, so the source publication does not support attributing the residual to replay-side replacement semantics.",
                "inference_rule": "blamed_amendment_has_only_repeal_support_without_section_payload",
                "trigger_observations": [
                    _obs(
                        "source_payload",
                        "repeal_only_without_payload",
                        [
                            {
                                "section": sec["section"],
                                "blame_source": (_lookup_support_row(
                                    sec,
                                    bisect_repeal_only_without_payload,
                                    bisect_repeal_only_without_payload_by_source_chapter_label,
                                    bisect_repeal_only_without_payload_by_source_label,
                                ) or {}).get("blame_source"),
                                "compiled_actions": (_lookup_support_row(
                                    sec,
                                    bisect_repeal_only_without_payload,
                                    bisect_repeal_only_without_payload_by_source_chapter_label,
                                    bisect_repeal_only_without_payload_by_source_label,
                                ) or {}).get("blame_compiled_actions_for_section"),
                            }
                            for sec in unsupported_replay_sections
                        ],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": [
                        {
                            **sec,
                            "blame_source": (_lookup_support_row(
                                sec,
                                bisect_repeal_only_without_payload,
                                bisect_repeal_only_without_payload_by_source_chapter_label,
                                bisect_repeal_only_without_payload_by_source_label,
                            ) or {}).get("blame_source"),
                            "compiled_actions": (_lookup_support_row(
                                sec,
                                bisect_repeal_only_without_payload,
                                bisect_repeal_only_without_payload_by_source_chapter_label,
                                bisect_repeal_only_without_payload_by_source_label,
                            ) or {}).get("blame_compiled_actions_for_section"),
                        }
                        for sec in unsupported_replay_sections
                    ]
                },
            }
        )

    if payload_supported_replay_sections:
        claims.append(
            {
                "tier": "PROVED_SOURCE_PATHOLOGY",
                "kind": "blamed_source_payload_prefers_replay",
                "summary": "Some replay-labeled residual sections are blamed on amendments whose published section payload matches replay materially better than the oracle, so current evidence supports source/oracle-side divergence rather than replay-side replacement semantics.",
                "inference_rule": "blamed_section_payload_matches_replay_better_than_oracle",
                "trigger_observations": [
                    _obs(
                        "source_payload",
                        "payload_prefers_replay",
                        [
                            {
                                "section": sec["section"],
                                "blame_source": (_lookup_support_row(
                                    sec,
                                    bisect_payload_prefers_replay,
                                    bisect_payload_prefers_replay_by_source_chapter_label,
                                    bisect_payload_prefers_replay_by_source_label,
                                ) or {}).get("blame_source"),
                                "payload_vs_replay_score": (_lookup_support_row(
                                    sec,
                                    bisect_payload_prefers_replay,
                                    bisect_payload_prefers_replay_by_source_chapter_label,
                                    bisect_payload_prefers_replay_by_source_label,
                                ) or {}).get("blame_payload_vs_replay_score"),
                                "payload_vs_oracle_score": (_lookup_support_row(
                                    sec,
                                    bisect_payload_prefers_replay,
                                    bisect_payload_prefers_replay_by_source_chapter_label,
                                    bisect_payload_prefers_replay_by_source_label,
                                ) or {}).get("blame_payload_vs_oracle_score"),
                            }
                            for sec in payload_supported_replay_sections
                        ],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": [
                        {
                            **sec,
                            "blame_source": (_lookup_support_row(
                                sec,
                                bisect_payload_prefers_replay,
                                bisect_payload_prefers_replay_by_source_chapter_label,
                                bisect_payload_prefers_replay_by_source_label,
                            ) or {}).get("blame_source"),
                            "payload_vs_replay_score": (_lookup_support_row(
                                sec,
                                bisect_payload_prefers_replay,
                                bisect_payload_prefers_replay_by_source_chapter_label,
                                bisect_payload_prefers_replay_by_source_label,
                            ) or {}).get("blame_payload_vs_replay_score"),
                            "payload_vs_oracle_score": (_lookup_support_row(
                                sec,
                                bisect_payload_prefers_replay,
                                bisect_payload_prefers_replay_by_source_chapter_label,
                                bisect_payload_prefers_replay_by_source_label,
                            ) or {}).get("blame_payload_vs_oracle_score"),
                        }
                        for sec in payload_supported_replay_sections
                    ]
                },
            }
        )

    if elaboration_replay_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
                "summary": "Some replay-labeled residual sections are blamed on amendments whose compilation already required same-section elaboration, so current evidence does not yet cleanly separate replay execution fault from elaboration ambiguity.",
                "inference_rule": "blamed_amendment_has_same_section_elaboration_observation",
                "trigger_observations": [
                    _obs(
                        "elaboration",
                        "same_section_sparse_elaboration",
                        [
                            {
                                "section": sec["section"],
                                "blame_source": (_lookup_support_row(
                                    sec,
                                    bisect_sparse_elaboration,
                                    bisect_sparse_elaboration_by_source_chapter_label,
                                    bisect_sparse_elaboration_by_source_label,
                                ) or {}).get("blame_source"),
                                "observation_kinds": (_lookup_support_row(
                                    sec,
                                    bisect_sparse_elaboration,
                                    bisect_sparse_elaboration_by_source_chapter_label,
                                    bisect_sparse_elaboration_by_source_label,
                                ) or {}).get("blame_elaboration_kinds"),
                                "apply_helpers": (_lookup_support_row(
                                    sec,
                                    bisect_sparse_elaboration,
                                    bisect_sparse_elaboration_by_source_chapter_label,
                                    bisect_sparse_elaboration_by_source_label,
                                ) or {}).get("blame_apply_helpers_for_section"),
                            }
                            for sec in elaboration_replay_sections
                        ],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": [
                        {
                            **sec,
                            "blame_source": (_lookup_support_row(
                                sec,
                                bisect_sparse_elaboration,
                                bisect_sparse_elaboration_by_source_chapter_label,
                                bisect_sparse_elaboration_by_source_label,
                            ) or {}).get("blame_source"),
                            "observation_kinds": (_lookup_support_row(
                                sec,
                                bisect_sparse_elaboration,
                                bisect_sparse_elaboration_by_source_chapter_label,
                                bisect_sparse_elaboration_by_source_label,
                            ) or {}).get("blame_elaboration_kinds"),
                            "apply_helpers": (_lookup_support_row(
                                sec,
                                bisect_sparse_elaboration,
                                bisect_sparse_elaboration_by_source_chapter_label,
                                bisect_sparse_elaboration_by_source_label,
                            ) or {}).get("blame_apply_helpers_for_section"),
                        }
                        for sec in elaboration_replay_sections
                    ]
                },
            }
        )

    if baseline_same_chapter_drift_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.address_projection.same_chapter_section_drift",
                "summary": "Some replay-labeled residual sections already match a different same-chapter replay section materially better in the baseline state before the first bad amendment step, so current evidence points to preexisting chapter-local numbering drift rather than a blamed replay execution fault.",
                "inference_rule": "preexisting_same_chapter_replay_section_matches_oracle_better_than_same_number_section",
                "trigger_observations": [
                    _obs(
                        "section_bisect",
                        "baseline_alternative_replay_section_match",
                        baseline_same_chapter_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": baseline_same_chapter_drift_sections[:20],
                },
            }
        )

    if baseline_same_section_structure_drift_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.preexisting.same_section_structure_drift",
                "summary": (
                    "Some replay-labeled residual sections already face unmatched oracle subsection fragments "
                    "before the blamed amendment, so current evidence points to preexisting same-section "
                    "structural drift rather than a clean blamed replay execution fault."
                ),
                "inference_rule": (
                    "oracle_has_unmatched_same_section_subsection_fragments_before_blamed_amendment"
                ),
                "trigger_observations": [
                    _obs(
                        "section_bisect",
                        "baseline_unmatched_oracle_subsections",
                        baseline_same_section_structure_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": baseline_same_section_structure_drift_sections[:20],
                },
            }
        )

    if oracle_range_drift_sections:
        claims.append(
            {
                "tier": "PROVED_ORACLE_INCORRECT",
                "kind": "same_chapter_oracle_range_drift",
                "summary": (
                    "Some replay-labeled residual sections map to same-chapter oracle range sections "
                    "instead of exact section labels, so current evidence points to oracle-side section "
                    "topology drift rather than replay execution fault."
                ),
                "inference_rule": (
                    "oracle_uses_same_chapter_section_range_instead_of_exact_section_label"
                ),
                "trigger_observations": [
                    _obs(
                        "oracle_check",
                        "oracle_range_section_match",
                        oracle_range_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": oracle_range_drift_sections[:20],
                },
            }
        )

    if cross_chapter_oracle_drift_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.address_projection.cross_chapter_oracle_drift",
                "summary": "Some replay-labeled residual sections match a same-label oracle section in a different chapter materially better than the same-path oracle section, so current evidence points to cross-chapter path drift rather than a clean replay execution fault.",
                "inference_rule": "oracle_matches_same_label_section_in_different_chapter",
                "trigger_observations": [
                    _obs(
                        "oracle_check",
                        "cross_chapter_oracle_section_match",
                        cross_chapter_oracle_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": cross_chapter_oracle_drift_sections[:20],
                },
            }
        )

    if cross_chapter_replay_drift_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.address_projection.cross_chapter_replay_drift",
                "summary": "Some oracle-labeled missing sections match a same-label replay section in a different chapter materially better than the same-path replay section, so current evidence points to cross-chapter path drift rather than a clean replay execution fault.",
                "inference_rule": "replay_matches_same_label_section_in_different_chapter_than_oracle",
                "trigger_observations": [
                    _obs(
                        "oracle_check",
                        "cross_chapter_replay_section_match",
                        cross_chapter_replay_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": cross_chapter_replay_drift_sections[:20],
                },
            }
        )

    if same_chapter_drift_sections:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "UNRESOLVED.address_projection.same_chapter_replay_drift",
                "summary": "Some replay-labeled residual sections match a different same-chapter replay section materially better than the same-number replay section, so current evidence points to chapter-local section drift rather than a clean replay execution fault.",
                "inference_rule": "same_chapter_replay_section_matches_oracle_better_than_same_number_section",
                "trigger_observations": [
                    _obs(
                        "oracle_check",
                        "alternative_replay_section_match",
                        same_chapter_drift_sections[:20],
                        scope="sections",
                    ),
                ],
                "support": {
                    "sections": same_chapter_drift_sections[:20],
                },
            }
        )

    if section_claims is not None:
        selected_replay_sections = {
            str(row.get("section") or "")
            for row in section_claims
            if str(row.get("selected_kind") or "") == "replay_divergence"
            and str(row.get("selected_tier") or "") == "PROVED_REPLAY_BUG"
            and str(row.get("section") or "")
        }
        replay_bug_sections = [
            sec for sec in replay_bug_sections
            if str(sec.get("section") or "") in selected_replay_sections
        ]

    if replay_bug_sections:
        claims.append(
            {
                "tier": "PROVED_REPLAY_BUG",
                "kind": "replay_divergence",
                "summary": "Residual divergences remain classified as replay-side bugs after current oracle/source demotions.",
                "inference_rule": "residual_replay_bug_diagnoses_present",
                "trigger_observations": [
                    _obs("oracle_check", "replay_bug_sections", replay_bug_sections[:20], scope="sections"),
                ],
                "support": {
                    "sections": replay_bug_sections[:20],
                },
            }
        )

    # Gap 1 (no_strong_claim investigation): If section claims exist and ALL
    # resolve to PROVED_ORACLE_INCORRECT (e.g. repealed statutes where every
    # section is EXTRA with no timeline), promote to statute-level oracle claim.
    if not claims and section_claims is not None:
        _sc_tiers = {
            str(row.get("selected_tier") or "")
            for row in section_claims
            if str(row.get("selected_tier") or "")
        }
        if _sc_tiers and _sc_tiers <= {"PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE"}:
            _dominant = (
                "PROVED_ORACLE_INCORRECT"
                if "PROVED_ORACLE_INCORRECT" in _sc_tiers
                else "PROVED_HTML_XML_NONCOMMENSURABLE"
            )
            _sc_kinds = sorted({
                str(row.get("selected_kind") or "")
                for row in section_claims
                if str(row.get("selected_kind") or "")
            })
            claims.append(
                {
                    "tier": _dominant,
                    "kind": "section_claims_unanimously_oracle_incorrect",
                    "summary": (
                        "All section-level claims resolve to oracle-incorrect or noncommensurable. "
                        "Statute-level rollup promotes to the dominant section tier."
                    ),
                    "inference_rule": "all_section_claims_resolve_to_oracle_or_noncommensurable",
                    "trigger_observations": [
                        _obs(
                            "section_claims",
                            "unanimous_section_tiers",
                            sorted(_sc_tiers),
                            scope="statute",
                        ),
                    ],
                    "support": {
                        "section_tiers": sorted(_sc_tiers),
                        "section_claim_kinds": _sc_kinds,
                        "section_count": len(section_claims),
                    },
                }
            )

    # Gap 4: Mixed section tiers where the only UNRESOLVED sections are
    # "oracle_text_empty_unverified" — the oracle body is entirely empty but
    # replay produced real sections.  The empty-unverified sections are also
    # oracle-incorrect (oracle lacks text that should be there); we just can't
    # independently verify via contentAbsent.  If at least one section IS proved,
    # the statute-level pattern "oracle has zero body sections" is strong enough.
    if not claims and section_claims is not None:
        _sc_tiers_g4 = {
            str(row.get("selected_tier") or "")
            for row in section_claims
            if str(row.get("selected_tier") or "")
        }
        _unresolved_kinds_g4 = {
            str(row.get("selected_kind") or "")
            for row in section_claims
            if str(row.get("selected_tier") or "") == "UNRESOLVED"
            and str(row.get("selected_kind") or "")
        }
        _has_proved_g4 = bool(
            _sc_tiers_g4 & {"PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE"}
        )
        _only_empty_unverified_g4 = (
            _unresolved_kinds_g4
            <= {"UNRESOLVED.source_underdetermined.oracle_text_empty_unverified"}
        )
        if _has_proved_g4 and _only_empty_unverified_g4 and _unresolved_kinds_g4:
            _proved_count = sum(
                1 for row in section_claims
                if str(row.get("selected_tier") or "") in {
                    "PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE",
                }
            )
            _unresolved_count = sum(
                1 for row in section_claims
                if str(row.get("selected_tier") or "") == "UNRESOLVED"
            )
            claims.append(
                {
                    "tier": "PROVED_ORACLE_INCORRECT",
                    "kind": "oracle_body_empty_with_proved_sections",
                    "summary": (
                        "Oracle has empty body for sections that replay produces. "
                        "All UNRESOLVED sections are oracle_text_empty_unverified "
                        "and at least one section is independently proved oracle-incorrect."
                    ),
                    "inference_rule": (
                        "mixed_proved_and_empty_unverified_sections_"
                        "promote_when_all_unresolved_are_empty_oracle"
                    ),
                    "trigger_observations": [
                        _obs(
                            "section_claims",
                            "empty_unverified_only_unresolved",
                            True,
                            scope="statute",
                        ),
                    ],
                    "support": {
                        "proved_section_count": _proved_count,
                        "empty_unverified_section_count": _unresolved_count,
                        "total_section_count": len(section_claims),
                    },
                }
            )

    # Gap 3a: No divergent sections at all — compilation is trivially correct.
    # This covers Category C (perfect score) and Category D-perf (sections match
    # but non-section text differs — johtolause, liite, voimaantulo).
    if not claims and section_results:
        _all_match = all(
            str(item.get("diagnosis") or "") == "MATCH"
            for item in section_results
        )
        if _all_match:
            claims.append(
                {
                    "tier": "PROVED_ORACLE_INCORRECT",
                    "kind": "compilation_sections_correct",
                    "summary": (
                        "All section-level comparisons match. Any remaining statute-level "
                        "divergence is in non-section content (johtolause, liite, voimaantulo)."
                    ),
                    "inference_rule": "all_sections_match_therefore_compilation_correct",
                    "trigger_observations": [
                        _obs(
                            "oracle_check",
                            "all_sections_match",
                            True,
                            scope="statute",
                        ),
                    ],
                    "support": {
                        "section_count": len(section_results),
                        "all_match": True,
                    },
                }
            )

    # Gap 3b: No section results at all — trivially empty statute
    # (announcements, SPB decisions, budget amendments with no sections).
    if not claims and not section_results:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "trivially_empty",
                "summary": (
                    "Neither replay nor oracle produced section content. "
                    "No comparison is possible."
                ),
                "inference_rule": "no_section_results_available",
                "trigger_observations": [],
                "support": {"section_count": 0},
            }
        )

    if not claims:
        claims.append(
            {
                "tier": "UNRESOLVED",
                "kind": "no_strong_claim",
                "summary": "No strong replay/oracle/source proof claim was derived from the current statute evidence.",
                "inference_rule": "no_claim_trigger_matched",
                "trigger_observations": [],
                "support": {},
            }
        )
    return claims


def _primary_proof_tier(claims: List[Dict]) -> str:
    seen = {str(item.get("tier") or "") for item in claims}
    for tier in _PRIMARY_TIER_ORDER:
        if tier in seen:
            return tier
    return "UNRESOLVED"


# ---------------------------------------------------------------------------
# Typed path (A1 proof algebra)
# ---------------------------------------------------------------------------


def build_section_claims_typed(
    *,
    section_results: List[Dict],
    section_bisect: Optional[List[Dict]] = None,
    alternative_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    oracle_range_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    html_topology: Optional[Dict[str, Any]] = None,
    strict_fail_reasons: Optional[List[str]] = None,
    timeline_addresses: Optional[set[str]] = None,
    oracle_suspect_detail: str = "",
    section_strict_verdicts: Optional[Dict[str, Any]] = None,
    section_invariant_violations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    chain_completeness_by_section: Optional[Dict[str, Any]] = None,
) -> list:
    """Typed section claim construction — produces identical output to legacy.

    Uses the A1 proof algebra: SectionEvidenceContext -> typed rules ->
    staged resolver -> ResolvedSectionClaims.  Each result can be
    serialised to a legacy row via to_legacy_row().
    """
    from lawvm.core.section_evidence_context import build_section_contexts
    from lawvm.core.section_evidence_context import (
        AlternativeReplayMatch,
        CrossChapterOracleMatch,
        CrossChapterReplayMatch,
        OracleRangeMatch,
    )
    from lawvm.tools.evidence_claim_algebra import (
        ResolvedSectionClaims,
        resolve,
    )
    from lawvm.tools.evidence_section_rules import (
        FALLBACK_DEFEATER_RULES,
        FINAL_FALLBACK_RULES,
        PREEMPTIVE_POSITIVE_RULES,
        PRIMARY_POSITIVE_RULES,
        PRIMARY_SINK_RULES,
        PROMOTION_POSITIVE_RULES,
    )

    contexts = build_section_contexts(
        section_results=section_results,
        section_bisect=section_bisect,
        alternative_replay_matches=cast(
            Optional[Dict[str, AlternativeReplayMatch]],
            alternative_replay_matches,
        ),
        oracle_range_matches=cast(
            Optional[Dict[str, OracleRangeMatch]],
            oracle_range_matches,
        ),
        cross_chapter_oracle_matches=cast(
            Optional[Dict[str, CrossChapterOracleMatch]],
            cross_chapter_oracle_matches,
        ),
        cross_chapter_replay_matches=cast(
            Optional[Dict[str, CrossChapterReplayMatch]],
            cross_chapter_replay_matches,
        ),
        html_topology=html_topology,
        strict_fail_reasons=strict_fail_reasons,
        timeline_addresses=timeline_addresses,
        oracle_suspect_detail=oracle_suspect_detail,
        section_strict_verdicts=section_strict_verdicts,
        section_invariant_violations=section_invariant_violations,
        chain_completeness_by_section=chain_completeness_by_section,
    )

    results: List[ResolvedSectionClaims] = []
    # Iterate in section_results order to match legacy row ordering
    for item in section_results:
        section = str(item.get("section") or "")
        if not section:
            continue
        ctx = contexts.get(section)
        if ctx is None:
            continue
        result = resolve(
            ctx,
            preemptive_positive_rules=PREEMPTIVE_POSITIVE_RULES,
            primary_positive_rules=PRIMARY_POSITIVE_RULES,
            primary_sink_rules=PRIMARY_SINK_RULES,
            fallback_defeater_rules=FALLBACK_DEFEATER_RULES,
            promotion_positive_rules=PROMOTION_POSITIVE_RULES,
            final_fallback_rules=FINAL_FALLBACK_RULES,
        )
        results.append(result)
    return results
