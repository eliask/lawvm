#!/usr/bin/env python3
"""Farchive-native broad UK replay-vs-oracle baseline.

The 9-statute gate (``scripts/uk_regression_test.py``) is too narrow to detect
regressions in oracle grounding, which touches *every* statute's score. This
tool scores replay-vs-oracle EID-set similarity for an arbitrary sample of UK
statutes drawn straight from the farchive (no on-disk raw XML required), so a
grounding change can be checked against a broad baseline before it ships.

Two scoring lanes per statute:
  - ``aligned``   : apply_ops with oracle EID alignment (the production score).
  - ``unaligned`` : apply_ops with ``allow_oracle_alignment=False`` (structural
                    replay only). The aligned/unaligned gap is the #53 signal —
                    when grounding is unstable the aligned score moves under node
                    removal while the unaligned score does not.

Each statute is scored in its OWN subprocess (``--one ID``) so peak RSS stays
bounded under WSL2 (per the source-root-lifecycle note); the driver runs one or
more child subprocesses and aggregates a JSON snapshot.

Usage:
  # score an explicit list, write a snapshot
  uv run python scripts/uk_broad_baseline.py --ids ukpga/1978/30 ukpga/1985/6 \
      --out .tmp/uk_baseline.json

  # sample N statutes that have BOTH enacted+current in the archive
  uv run python scripts/uk_broad_baseline.py --sample 150 --seed 7 \
      --out .tmp/uk_baseline.json

  # run the same isolated per-statute scorer with bounded parent-side parallelism
  uv run python scripts/uk_broad_baseline.py --sample 150 --seed 7 \
      --parallel 8 --out .tmp/uk_baseline.json

  # score one statute (subprocess unit; prints one JSON line)
  uv run python scripts/uk_broad_baseline.py --one ukpga/1978/30

  # compare two snapshots (regression gate)
  uv run python scripts/uk_broad_baseline.py --compare before.json after.json
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections.abc import Iterable, Mapping
from collections import Counter
import hashlib
import json
import random
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    # Forward-only import to satisfy the type-only annotation reference to
    # ``Observation`` on the ``assert_classification_exclusive`` return type
    # (the actual runtime import lives inside the function — kept local to
    # avoid forcing the script's import-time cost on every subcommand path).
    from lawvm.core.phase_result import Observation

from lxml import etree as ET

from lawvm.core.agreement_residual import (
    AgreementResidual,
    AgreementResidualFamily,
    AgreementResidualStatus,
)
from lawvm.core.evidence_surface_report import EvidenceSurfaceReport
from lawvm.core.frontier_work_item import FrontierWorkItem
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.core.source_witness import (
    DigestWitness,
    SourceWitness,
    source_witness_digest_coverage,
    source_witness_role_key,
)
from lawvm.roman import arabic_to_roman
from lawvm.uk_legislation.execution_authorization import (
    uk_execution_authorization_from_compile_record,
)
from lawvm.uk_legislation.phase_discipline import (
    UK_PHASE_AFFECTING_SOURCE_EXTRACTION,
    UK_PHASE_CANONICAL_OP_COMPILATION,
    UK_PHASE_COMPARE_ORACLE_CLASSIFICATION,
    UK_PHASE_EFFECT_METADATA_FRONTEND,
    UK_PHASE_REPLAY_INVARIANTS,
    UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER,
    UK_PHASE_TYPED_ELABORATION,
    uk_phase_owner_for_diagnostic,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "uk_legislation.farchive"
_LEG_BASE = "https://www.legislation.gov.uk"

# A statute is flagged a regression if its aligned score drops by more than this
# many percentage points versus the baseline snapshot.
_REGRESSION_TOL = 0.1
_HIGH_FIDELITY_AFTER_GROUNDING_THRESHOLD = 95.0
_GROUNDING_DOMINATED_DELTA_THRESHOLD = 20.0
_STRUCTURAL_MATCH_THRESHOLD = 99.5


def _rounded_phase_timings(timings: Mapping[str, float]) -> dict[str, float]:
    return {key: round(value, 6) for key, value in sorted(timings.items())}
_COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS = 25
_LOW_VOLUME_RESIDUAL_MAX_MISSES = 25
_LOW_VOLUME_RESIDUAL_MIN_SCORE = 85.0
_MANUAL_FRONTIER_BLOCKING_RULES = frozenset(
    {
        "uk_effect_repeal_table_replacement_payload_rejected",
        "uk_effect_repeal_table_structural_repeal_unresolved",
        "uk_effect_source_payload_without_instruction_context_rejected",
        "uk_effect_table_entry_instruction_rejected",
        "uk_effect_whole_act_word_level_text_patch_rejected",
    }
)
_MANUAL_FRONTIER_ACTIONABLE_STATUSES = frozenset(
    {
        "manual_compile_candidate",
        "deterministic_frontend_candidate",
        "source_insufficient",
    }
)
_MANUAL_FRONTIER_TEMPLATE_ACTIONABLE_STATUSES = frozenset(
    {
        "manual_compile_candidate",
        "deterministic_frontend_candidate",
        "source_or_feed_target_conflict",
    }
)
_ACTIVE_UNCLASSIFIED_RESIDUAL_BUCKETS = frozenset(
    {
        "compile_rejection_dominated_residual",
        "grounding_dominated_residual",
        "residual_after_grounding",
        "retained_eu_mixed_representation_residual",
        "structural_match_eid_scheme_residual",
    }
)
_NON_CORE_COMPARISON_TRIAGE_BUCKETS = frozenset(
    {
        "base_metadata_only_frontier",
        "body_oracle_collapsed_range_granularity_residual",
        "body_oracle_first_paragraph_sectionization_residual",
        "body_nested_list_oracle_granularity_residual",
        "effect_feed_absent_frontier",
        "error",
        "manual_compile_frontier_residual",
        "no_compiled_ops_frontier",
        "no_effect_rows_frontier",
        "nonreplay_effect_frontier",
        "oracle_addition_source_chain_frontier",
        "oracle_expansion_without_effects",
        "retained_eu_mixed_representation_residual",
        "retained_eu_schedule_oracle_granularity_residual",
        "retained_repeal_oracle_branch",
        "source_backed_temporal_recovery_oracle_residual",
        "structural_match_eid_scheme_residual",
        "temporal_commencement_frontier",
        "zero_oracle_retention",
    }
)
_MANUAL_SOURCE_CHAIN_FRONTIER_REASONS = frozenset(
    {
        "manual_frontier_manual_compile_candidate",
        "manual_frontier_source_insufficient",
        "manual_frontier_source_chain_text_patch_gap",
    }
)
_MANUAL_FRONTIER_SOURCE_CHAIN_TEXT_PATCH_RULES = frozenset(
    {
        "uk_manual_frontier_text_patch_preimage_chain_gap",
        "uk_manual_frontier_text_patch_target_source_chain_gap",
        "uk_manual_frontier_text_patch_postimage_chain_gap",
    }
)
_MANUAL_FRONTIER_SOURCE_CHAIN_TEXT_PATCH_OPERATION_FAMILY = "source_chain_text_patch"
_REPLAY_LENS_FRONTIER_REASONS = frozenset(
    {
        "effect_rows_not_admitted_by_replay_lens",
    }
)
_OFFICIAL_EMPTY_EFFECT_FEED_FRONTIER_REASONS = frozenset(
    {
        "effect_feed_empty",
        "effect_feed_pages_absent",
        "oracle_addition_changeid_source_chain_gap",
    }
)
_SOURCE_OR_ORACLE_PATHOLOGY_FRONTIER_REASONS = frozenset(
    {
        "base_and_oracle_metadata_only",
        "base_metadata_only",
        "base_multiple_choices",
        "base_too_small",
        "oracle_metadata_only",
        "oracle_multiple_choices",
    }
)
_SOURCE_CHAIN_COMPLETENESS_EXCLUDED_REASONS = (
    _MANUAL_SOURCE_CHAIN_FRONTIER_REASONS | _REPLAY_LENS_FRONTIER_REASONS
    | _OFFICIAL_EMPTY_EFFECT_FEED_FRONTIER_REASONS
    | _SOURCE_OR_ORACLE_PATHOLOGY_FRONTIER_REASONS
)


def _eids(nodes: list[Any], pit_date: Optional[str] = None) -> set[str]:
    from lawvm.core.ir_helpers import is_zombie

    out: set[str] = set()
    for n in nodes:
        if is_zombie(n, pit_date):
            continue
        eid = n.attrs.get("eId") or n.attrs.get("id")
        if eid:
            out.add(eid)
        out.update(_eids(n.children, pit_date=pit_date))
    return out


def _similarity(replay_eids: set[str], oracle_eids: set[str]) -> float:
    from lawvm.uk_legislation.grounding_collateral import eid_set_similarity

    return eid_set_similarity(replay_eids, oracle_eids)


def _normalized_compare_eids(
    replay_eids: set[str],
    oracle_eids: set[str],
    *,
    oracle_physical_eid_aliases: dict[str, str],
    oracle_visible_number_eid_aliases: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Normalize broad-gate EID comparison through the same lens as uk-misses.

    Also canonicalizes the numbering scheme (Roman→Arabic) and strips cosmetic
    trailing dots, the same comparison-layer normalization ``uk_bench`` applies, so
    an old Act's ``section-II`` matches the oracle's ``section-2``. Identity on a
    modern Arabic corpus (the current grounding set), so the broad gate is
    unchanged; it future-proofs the score should the corpus include pre-1860 Acts.
    """
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.uk_legislation.canonicalize import canonicalize_compare_eid

    replay_norm, oracle_norm = normalize_uk_replay_compare_eids(
        replay_eids,
        oracle_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )
    return (
        {canonicalize_compare_eid(e) for e in replay_norm},
        {canonicalize_compare_eid(e) for e in oracle_norm},
    )


def _oracle_only_addition_change_id_evidence(
    *,
    current_xml: bytes,
    oracle_only_eids: set[str],
    oracle_physical_eid_aliases: dict[str, str],
    oracle_visible_number_eid_aliases: dict[str, str],
    compiled_change_ids: set[str],
) -> dict[str, Any]:
    """Report oracle-only current XML additions not backed by compiled effects."""
    if not oracle_only_eids:
        return {}
    from lawvm.uk_legislation.source_adjudication import _normalize_uk_source_container_eid

    alias_norm: dict[str, str] = {}
    for aliases in (oracle_physical_eid_aliases, oracle_visible_number_eid_aliases):
        for original, replacement in aliases.items():
            normalized_original = _normalize_uk_source_container_eid(original)
            normalized_replacement = _normalize_uk_source_container_eid(replacement)
            if normalized_original and normalized_replacement:
                alias_norm[normalized_original] = normalized_replacement

    root = ET.fromstring(current_xml)
    change_ids_by_eid: dict[str, tuple[str, ...]] = {}
    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        raw_eid = str(element.get("id") or element.get("eId") or "")
        normalized = _normalize_uk_source_container_eid(raw_eid)
        if not normalized:
            continue
        compare_eid = alias_norm.get(normalized, normalized)
        if compare_eid not in oracle_only_eids:
            continue
        change_ids = _element_addition_change_ids(element)
        if change_ids:
            change_ids_by_eid[compare_eid] = change_ids

    uncompiled_eids = sorted(
        eid
        for eid, change_ids in change_ids_by_eid.items()
        if any(change_id not in compiled_change_ids for change_id in change_ids)
    )
    all_change_ids = sorted(
        {change_id for change_ids in change_ids_by_eid.values() for change_id in change_ids}
    )
    uncompiled_change_ids = sorted(
        change_id
        for change_id in all_change_ids
        if change_id not in compiled_change_ids
    )
    return {
        "n_oracle_only_addition_eids": len(change_ids_by_eid),
        "oracle_only_addition_eid_samples": sorted(change_ids_by_eid)[:20],
        "oracle_only_addition_change_ids": all_change_ids[:20],
        "n_oracle_only_uncompiled_addition_eids": len(uncompiled_eids),
        "oracle_only_uncompiled_addition_eid_samples": uncompiled_eids[:20],
        "oracle_only_uncompiled_addition_change_ids": uncompiled_change_ids[:20],
    }


def _element_addition_change_ids(element: ET._Element) -> tuple[str, ...]:
    change_ids: set[str] = set()
    for candidate in element.iter():
        if not isinstance(candidate.tag, str):
            continue
        if ET.QName(candidate).localname != "Addition":
            continue
        change_id = str(candidate.get("ChangeId") or "").strip()
        if change_id:
            change_ids.add(change_id)
    return tuple(sorted(change_ids))


def _compiled_source_chain_ids(
    ops: list[Any],
    effect_rows: list[Any],
) -> set[str]:
    """Collect source/effect ids already represented by compiled replay inputs."""
    compiled_ids: set[str] = set()
    for effect in effect_rows:
        effect_id = str(getattr(effect, "effect_id", "") or "").strip()
        if effect_id:
            compiled_ids.add(effect_id)
    for op in ops:
        op_id = str(getattr(op, "op_id", "") or "").strip()
        if not op_id:
            continue
        compiled_ids.add(op_id)
        compiled_ids.add(_base_effect_id_from_op_id(op_id))
    return {identifier for identifier in compiled_ids if identifier}


def _base_effect_id_from_op_id(op_id: str) -> str:
    """Remove LawVM lowering suffixes from operation ids where possible."""
    if not op_id.startswith("key-"):
        return op_id
    for suffix in (
        "_structured_tail_",
        "_definition_child_",
        "_inserted_subsection_child_range_",
        "_parent_child_substitution",
        "_definition_child_structural_substitution",
        "_definition_child_tail_connector",
        "_definition_child_insert_",
        "_crossheading",
        "_anchor_tail",
        "_semicolon",
        "_insert_",
        "_repeal_",
        "_insert",
    ):
        if suffix in op_id:
            return op_id.split(suffix, 1)[0]
    return op_id


def _retained_repeal_oracle_targets(
    ops: list[Any],
    oracle_only_eids: set[str],
    *,
    statute_id: str,
) -> list[str]:
    """Find source-backed repeal roots still exposed by the current oracle."""
    from lawvm.core.semantic_types import StructuralAction
    from lawvm.uk_legislation.target_anchors import _fallback_target_eid

    targets: set[str] = set()
    for op in ops:
        if op.action is not StructuralAction.REPEAL:
            continue
        if str(getattr(op.target, "special", "") or "") == "whole_act":
            if oracle_only_eids:
                targets.add("/whole_act")
            continue
        target_eid = _fallback_target_eid(op.target)
        candidate_eids = {target_eid}
        if statute_id.startswith("eur/"):
            candidate_eids.update(_retained_eu_oracle_eid_aliases(target_eid))
        matched_eids = candidate_eids & oracle_only_eids
        targets.update(sorted(matched_eids))
    return sorted(targets)


def _retained_eu_oracle_eid_aliases(target_eid: str) -> set[str]:
    """Map LawVM section/schedule target ids to retained-EU oracle article ids."""
    aliases: set[str] = set()
    if target_eid.startswith("section-"):
        aliases.add(f"article-{target_eid.removeprefix('section-')}")
    if target_eid.startswith("schedule-"):
        schedule_label = target_eid.removeprefix("schedule-")
        roman = _positive_int_to_roman(schedule_label)
        if roman:
            aliases.add(f"annex-{roman.lower()}")
    return aliases


def _positive_int_to_roman(value: str) -> str:
    if not value.isdecimal():
        return ""
    number = int(value)
    if number <= 0 or number > 20:
        return ""
    return arabic_to_roman(number)


def _op_targets_schedule_surface(op: Any) -> bool:
    target = getattr(op, "target", None)
    if target is None:
        return False
    for kind, _label in getattr(target, "path", ()) or ():
        if str(kind or "").lower() == "schedule":
            return True
    return str(getattr(target, "special", "") or "").lower() == "schedule"


def _oracle_only_schedule_eids(oracle_only_eids: set[str]) -> list[str]:
    return sorted(
        eid for eid in oracle_only_eids if str(eid or "").startswith("schedule")
    )


def _mutation_boundary_diagnostics(
    mutation_events: list[Any],
) -> dict[str, Any]:
    """Summarize passive mutation-boundary accounting for one replay run."""
    from lawvm.core.mutation_accounting import build_mutation_invariant_reports
    from lawvm.core.mutation_boundary import tree_path_to_diagnostic_string

    reports = build_mutation_invariant_reports(mutation_events)
    unexplained_reports = [
        report
        for report in reports
        if report.unexplained_changed_paths or not report.path_set_invariant_holds
    ]
    result_code_counts = Counter(
        result.code
        for report in reports
        for result in report.results
    )
    helper_counts = Counter(report.helper for report in reports)
    proofs = tuple(
        MutationBoundaryProof.from_mutation_invariant_report(
            report,
            proof_id=f"uk-broad-mutation-boundary:{index}:{report.op_id or '<missing>'}",
            jurisdiction="uk",
            materialization_surface="uk_broad_baseline_replay",
            owner_phase=UK_PHASE_REPLAY_INVARIANTS,
            safe_default="treat_unproved_boundary_as_replay_invariant_residual",
            forbidden_shortcuts=(
                "ignore_unexplained_changed_paths",
                "use_oracle_agreement_as_boundary_proof",
                "broaden_target_region_after_replay",
            ),
        )
        for index, report in enumerate(reports)
    )
    proof_status_counts = Counter(proof.boundary_proof_status for proof in proofs)
    proof_rule_counts = Counter(proof.rule_id for proof in proofs)
    proof_owner_phase_counts = Counter(proof.owner_phase for proof in proofs)
    samples = [
        {
            "op_id": report.op_id,
            "helper": report.helper,
            "outcome": report.outcome,
            "result_codes": [result.code for result in report.results],
            "unexplained_paths": [
                tree_path_to_diagnostic_string(path)
                for path in report.unexplained_changed_paths
            ],
        }
        for report in unexplained_reports[:5]
    ]
    proof_samples = [
        proof.to_dict()
        for proof in proofs
        if proof.boundary_proof_status in {"unresolved", "violated"}
    ][:5]
    return {
        "n_mutation_events": len(mutation_events),
        "n_mutation_boundary_reports": len(reports),
        "n_mutation_boundary_unexplained_reports": len(unexplained_reports),
        "n_mutation_boundary_unexplained_paths": sum(
            len(report.unexplained_changed_paths)
            for report in unexplained_reports
        ),
        "mutation_boundary_result_code_counts": dict(
            sorted(result_code_counts.items())
        ),
        "mutation_boundary_helper_counts": dict(sorted(helper_counts.items())),
        "mutation_boundary_proof_status_counts": dict(
            sorted(proof_status_counts.items())
        ),
        "mutation_boundary_proof_rule_counts": dict(sorted(proof_rule_counts.items())),
        "mutation_boundary_proof_owner_phase_counts": dict(
            sorted(proof_owner_phase_counts.items())
        ),
        "mutation_boundary_unexplained_samples": samples,
        "mutation_boundary_proof_samples": proof_samples,
    }


def score_one(statute_id: str) -> dict[str, Any]:
    """Score one statute from the farchive. Returns a result dict (never raises)."""
    from farchive import Farchive
    from lawvm.uk_legislation.effects import load_effects_for_statute_from_archive
    from lawvm.uk_legislation.source_state import classify_uk_statute_xml_content
    from lawvm.uk_legislation.uk_amendment_replay import (
        UKDiagnosticReplayFilterMode,
        UKReplayPipeline,
    )
    from lawvm.uk_legislation.uk_grafter import extract_eid_map_bytes, parse_uk_statute_ir_bytes

    result: dict[str, Any] = {"statute_id": statute_id}
    archive = Farchive(DB_PATH, readonly=True)
    try:
        enacted_locator = f"{_LEG_BASE}/{statute_id}/enacted/data.xml"
        current_locator = f"{_LEG_BASE}/{statute_id}/data.xml"
        enacted = archive.get(enacted_locator)
        current = archive.get(current_locator)
        result.update(
            _source_witness_fields(
                "base",
                statute_id=statute_id,
                locator=enacted_locator,
                source_lane="enacted_xml",
                data=enacted,
                source_status="absent" if not enacted else "unclassified",
            )
        )
        result.update(
            _source_witness_fields(
                "oracle",
                statute_id=statute_id,
                locator=current_locator,
                source_lane="current_xml",
                data=current,
                source_status="absent" if not current else "unclassified",
            )
        )
        if not enacted:
            return {
                **result,
                "base_source_status": "absent",
                "oracle_source_status": "unknown",
                "score_status": "source_frontier",
                "source_frontier_reason": "base_absent",
            }
        if not current:
            return {
                **result,
                "base_source_status": "unknown",
                "oracle_source_status": "absent",
                "score_status": "source_frontier",
                "source_frontier_reason": "oracle_absent",
            }
        base_source = classify_uk_statute_xml_content(enacted)
        current_source = classify_uk_statute_xml_content(current)
        result.update(_source_state_fields("base", base_source))
        result.update(_source_state_fields("oracle", current_source))
        result.update(
            _source_witness_fields(
                "base",
                statute_id=statute_id,
                locator=enacted_locator,
                source_lane="enacted_xml",
                data=enacted,
                source_status=base_source.xml_content_status.value,
            )
        )
        result.update(
            _source_witness_fields(
                "oracle",
                statute_id=statute_id,
                locator=current_locator,
                source_lane="current_xml",
                data=current,
                source_status=current_source.xml_content_status.value,
            )
        )
        if base_source.xml_content_status.value == "metadata_only":
            source_frontier_reason = (
                "base_and_oracle_metadata_only"
                if current_source.xml_content_status.value == "metadata_only"
                else "base_metadata_only"
            )
            return {
                **result,
                "score_status": "source_frontier",
                "source_frontier_reason": source_frontier_reason,
            }
        if base_source.xml_content_status.value in {"too_small", "multiple_choices", "parse_error"}:
            return {
                **result,
                "score_status": "source_frontier",
                "source_frontier_reason": f"base_{base_source.xml_content_status.value}",
            }
        if current_source.xml_content_status.value in {
            "too_small",
            "multiple_choices",
            "parse_error",
            "metadata_only",
        }:
            return {
                **result,
                "score_status": "source_frontier",
                "source_frontier_reason": f"oracle_{current_source.xml_content_status.value}",
            }

        oracle_data = extract_eid_map_bytes(current)
        eid_map = oracle_data.get("eid_map", {})
        text_map = oracle_data.get("text_map", {})
        oracle_eids = {str(eid) for eid in eid_map.values() if eid}
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )

        pipeline = UKReplayPipeline(REPO_ROOT)
        effect_rows = load_effects_for_statute_from_archive(statute_id, archive)
        result["n_effects"] = len(effect_rows)
        effect_feed_parse_rejections: list[dict[str, Any]] = []
        lowering_rejections: list[dict[str, Any]] = []
        authority_rejections: list[dict[str, Any]] = []
        effect_diagnostics: list[dict[str, Any]] = []
        compile_phase_timings: dict[str, float] = {}
        # §manual_claims: opt-in authored-claim loading. With the feature flag off
        # (or no authored file for this statute) the loaded bucket-set is empty and
        # ``compile_kwargs`` yields all-``None`` opt-in params ⇒ this call is
        # byte-identical to the no-claims path. Each loaded claim is still gated by
        # its ``validate_*`` inside ``compile_ops_for_statute``; loading only
        # deserializes, it never validates or applies.
        from lawvm.uk_legislation.manual_claim_store import (
            load_manual_claims_for_statute,
        )

        manual_claims = load_manual_claims_for_statute(statute_id)
        result["n_manual_claims_loaded"] = manual_claims.total_claims()
        compile_wall_t0 = time.perf_counter()
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            archive=archive,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
            effect_diagnostics_out=effect_diagnostics,
            compile_phase_timings_out=compile_phase_timings,
            diagnostic_replay_filter_mode=UKDiagnosticReplayFilterMode.OBSERVE_ONLY,
            **manual_claims.compile_kwargs(),
        )
        result["compile_wall_seconds"] = round(time.perf_counter() - compile_wall_t0, 6)
        result["compile_phase_timings"] = _rounded_phase_timings(
            compile_phase_timings
        )
        result["n_ops"] = len(ops)
        compiled_source_chain_ids = _compiled_source_chain_ids(ops, effect_rows)
        result["n_compiled_source_chain_ids"] = len(compiled_source_chain_ids)
        compile_rejections = [
            *_compile_authorization_rows(
                effect_feed_parse_rejections,
                lane="effect_feed_parse",
            ),
            *_compile_authorization_rows(lowering_rejections, lane="lowering"),
            *_compile_authorization_rows(authority_rejections, lane="authority"),
        ]
        blocking_compile_rejections = _blocking_records(compile_rejections)
        result["n_compile_rejections"] = len(compile_rejections)
        result["compile_rejection_rule_counts"] = _rule_counts(compile_rejections)
        result["compile_rejection_owner_phase_counts"] = _owner_phase_counts(
            compile_rejections
        )
        result["compile_rejection_authorization_status_counts"] = (
            _authorization_status_counts(compile_rejections)
        )
        result["compile_rejection_missing_proof_counts"] = (
            _authorization_missing_proof_counts(compile_rejections)
        )
        result["compile_rejection_rule_owner_phase_counts"] = (
            _rule_owner_phase_counts(compile_rejections)
        )
        result["n_blocking_compile_rejections"] = len(blocking_compile_rejections)
        result["blocking_compile_rejection_rule_counts"] = _rule_counts(
            blocking_compile_rejections
        )
        result["blocking_compile_rejection_owner_phase_counts"] = _owner_phase_counts(
            blocking_compile_rejections
        )
        result["blocking_compile_rejection_authorization_status_counts"] = (
            _authorization_status_counts(blocking_compile_rejections)
        )
        result["blocking_compile_rejection_missing_proof_counts"] = (
            _authorization_missing_proof_counts(blocking_compile_rejections)
        )
        result["blocking_compile_rejection_rule_owner_phase_counts"] = (
            _rule_owner_phase_counts(blocking_compile_rejections)
        )
        manual_frontier_records = [
            row
            for row in effect_diagnostics
            if row.get("rule_id") == "uk_manual_compile_frontier_classified"
        ]
        result["n_manual_frontier_records"] = len(manual_frontier_records)
        result["manual_frontier_status_counts"] = _manual_frontier_status_counts(
            manual_frontier_records
        )
        result["manual_frontier_rule_counts"] = _manual_frontier_rule_counts(
            manual_frontier_records
        )
        result["manual_frontier_owner_phase_counts"] = _owner_phase_counts(
            manual_frontier_records
        )
        result["manual_frontier_authorization_status_counts"] = (
            _manual_frontier_authorization_status_counts(manual_frontier_records)
        )
        result["manual_frontier_authorization_status_owner_phase_counts"] = (
            _manual_frontier_authorization_status_owner_phase_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_missing_proof_counts"] = (
            _manual_frontier_missing_proof_counts(manual_frontier_records)
        )
        result["manual_frontier_work_item_family_counts"] = (
            _manual_frontier_work_item_field_counts(
                manual_frontier_records,
                "frontier_family",
            )
        )
        result["manual_frontier_work_item_authorization_status_counts"] = (
            _manual_frontier_work_item_field_counts(
                manual_frontier_records,
                "authorization_status",
            )
        )
        result["manual_frontier_work_item_candidate_operation_family_counts"] = (
            _manual_frontier_work_item_field_counts(
                manual_frontier_records,
                "candidate_operation_family",
            )
        )
        result["manual_frontier_work_item_required_validator_check_counts"] = (
            _manual_frontier_work_item_sequence_field_counts(
                manual_frontier_records,
                "required_validator_checks",
            )
        )
        result["manual_frontier_work_item_packet_ready_counts"] = (
            _manual_frontier_work_item_packet_ready_counts(manual_frontier_records)
        )
        result["manual_frontier_work_item_packet_missing_field_counts"] = (
            _manual_frontier_work_item_packet_missing_field_counts(
                manual_frontier_records
            )
        )
        result[
            "manual_frontier_work_item_packet_execution_authorization_validation_issue_counts"
        ] = _manual_frontier_work_item_packet_validation_issue_counts(
            manual_frontier_records,
            "execution_authorization_validation_issues",
        )
        result[
            "manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts"
        ] = _manual_frontier_work_item_packet_validation_issue_counts(
            manual_frontier_records,
            "frontier_work_item_validation_issues",
        )
        result[
            "manual_frontier_work_item_packet_target_resolution_coverage_counts"
        ] = _manual_frontier_work_item_packet_target_resolution_coverage_counts(
            manual_frontier_records
        )
        result["manual_frontier_work_item_target_resolution_status_counts"] = (
            _manual_frontier_work_item_target_resolution_status_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_target_resolution_gap_counts"] = (
            _manual_frontier_work_item_target_resolution_gap_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_target_resolution_exempt_counts"] = (
            _manual_frontier_work_item_target_resolution_exempt_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_candidate_set_status_counts"] = (
            _manual_frontier_work_item_candidate_set_status_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_candidate_set_gap_counts"] = (
            _manual_frontier_work_item_candidate_set_gap_counts(manual_frontier_records)
        )
        result["manual_frontier_work_item_candidate_set_exempt_counts"] = (
            _manual_frontier_work_item_candidate_set_exempt_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_source_membership_status_counts"] = (
            _manual_frontier_work_item_source_membership_status_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_source_membership_blocker_counts"] = (
            _manual_frontier_work_item_source_membership_blocker_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_exclusion_scope_status_counts"] = (
            _manual_frontier_work_item_exclusion_scope_status_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_exclusion_scope_blocker_counts"] = (
            _manual_frontier_work_item_exclusion_scope_blocker_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_proof_obligation_status_counts"] = (
            _manual_frontier_work_item_proof_obligation_status_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_proof_obligation_proved_counts"] = (
            _manual_frontier_work_item_proof_obligation_proved_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_proof_obligation_blocker_counts"] = (
            _manual_frontier_work_item_proof_obligation_blocker_counts(
                manual_frontier_records
            )
        )
        result["manual_frontier_work_item_source_witness_role_counts"] = (
            _manual_frontier_work_item_source_witness_role_counts(
                manual_frontier_records
            )
        )
        result[
            "manual_frontier_work_item_source_witness_digest_coverage_counts"
        ] = _manual_frontier_work_item_source_witness_digest_coverage_counts(
            manual_frontier_records
        )
        result[
            "manual_frontier_work_item_missing_candidate_operation_family_count"
        ] = _manual_frontier_work_item_missing_field_count(
            manual_frontier_records,
            "candidate_operation_family",
        )
        result["manual_frontier_work_item_missing_required_validator_checks_count"] = (
            _manual_frontier_work_item_missing_sequence_field_count(
                manual_frontier_records,
                "required_validator_checks",
            )
        )
        result["manual_frontier_rule_owner_phase_counts"] = (
            _manual_frontier_rule_owner_phase_counts(manual_frontier_records)
        )
        result["manual_frontier_manual_compile_candidate_rule_counts"] = (
            _manual_frontier_rule_counts_for_status(
                manual_frontier_records,
                "manual_compile_candidate",
            )
        )
        result["manual_frontier_manual_compile_candidate_rule_owner_phase_counts"] = (
            _manual_frontier_rule_owner_phase_counts_for_status(
                manual_frontier_records,
                "manual_compile_candidate",
            )
        )
        result["manual_frontier_deterministic_candidate_rule_counts"] = (
            _manual_frontier_rule_counts_for_status(
                manual_frontier_records,
                "deterministic_frontend_candidate",
            )
        )
        result["manual_frontier_deterministic_candidate_rule_owner_phase_counts"] = (
            _manual_frontier_rule_owner_phase_counts_for_status(
                manual_frontier_records,
                "deterministic_frontend_candidate",
            )
        )
        result["manual_frontier_template_status_counts"] = (
            _manual_frontier_template_status_counts(manual_frontier_records)
        )
        result["manual_frontier_template_gap_status_counts"] = (
            _manual_frontier_template_gap_status_counts(manual_frontier_records)
        )
        result["manual_frontier_template_gap_rule_counts"] = (
            _manual_frontier_template_gap_rule_counts(manual_frontier_records)
        )

        lanes: dict[str, float] = {}
        for lane, aligned in (("aligned", True), ("unaligned", False)):
            base_ir = parse_uk_statute_ir_bytes(enacted, statute_id=statute_id)
            alignment_events: list[dict[str, Any]] = []
            mutation_events: list[Any] = []
            replayed = pipeline.apply_ops(
                base_ir,
                ops,
                eid_map=eid_map,
                text_map=text_map,
                allow_oracle_alignment=aligned,
                oracle_alignment_events_out=alignment_events if aligned else None,
                mutation_events_out=mutation_events if aligned else None,
            )
            replay_eids = _eids([replayed.body]) | {
                e for s in replayed.supplements for e in _eids([s])
            }
            replay_compare_eids, oracle_compare_eids = _normalized_compare_eids(
                replay_eids,
                oracle_eids,
                oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
            )
            lanes[lane] = round(
                100.0 * _similarity(replay_compare_eids, oracle_compare_eids),
                2,
            )
            if lane == "aligned":
                from lawvm.uk_legislation.grounding_collateral import (
                    score_with_grounding_collateral_excluded,
                )

                common_eids = replay_compare_eids & oracle_compare_eids
                oracle_only_eids = oracle_compare_eids - replay_compare_eids
                collateral_score = score_with_grounding_collateral_excluded(
                    replay_compare_eids,
                    oracle_compare_eids,
                    alignment_events,
                )
                retained_repeal_targets = _retained_repeal_oracle_targets(
                    ops,
                    oracle_only_eids,
                    statute_id=statute_id,
                )
                oracle_only_schedule_eids = _oracle_only_schedule_eids(
                    oracle_only_eids
                )
                replay_only_eids = replay_compare_eids - oracle_compare_eids
                result["n_common"] = len(common_eids)
                result["n_only_in_oracle"] = len(oracle_only_eids)
                result["n_only_in_replayed"] = len(replay_only_eids)
                result["oracle_only_eid_samples"] = sorted(oracle_only_eids)[:20]
                result["replay_only_eid_samples"] = sorted(replay_only_eids)[:20]
                result.update(
                    _oracle_only_addition_change_id_evidence(
                        current_xml=current,
                        oracle_only_eids=oracle_only_eids,
                        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
                        oracle_visible_number_eid_aliases=(
                            oracle_visible_number_eid_aliases
                        ),
                        compiled_change_ids=compiled_source_chain_ids,
                    )
                )
                result["n_replay"] = len(replay_compare_eids)
                result["n_oracle"] = len(oracle_compare_eids)
                result["retained_repeal_oracle_targets"] = retained_repeal_targets
                result["n_retained_repeal_oracle_targets"] = len(
                    retained_repeal_targets
                )
                result["n_oracle_only_schedule_eids"] = len(
                    oracle_only_schedule_eids
                )
                result["oracle_only_schedule_eid_samples"] = (
                    oracle_only_schedule_eids[:20]
                )
                result["has_schedule_targeting_ops"] = any(
                    _op_targets_schedule_surface(op) for op in ops
                )
                result["n_grounding_collateral"] = len(collateral_score.collateral_eids)
                result.update(_mutation_boundary_diagnostics(mutation_events))
                result["n_zero_oracle_retention_eids"] = (
                    len(replay_compare_eids) if not oracle_compare_eids else 0
                )
                result["aligned_excluding_grounding_collateral"] = round(
                    100.0 * collateral_score.collateral_excluded_similarity,
                    2,
                )
        result["score_status"] = "scored"
        result["aligned"] = lanes["aligned"]
        result["unaligned"] = lanes["unaligned"]
        return result
    except Exception as exc:
        return {**result, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        archive.close()


def summarize_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize broad-baseline row diagnostics without reclassifying rows."""
    scored = [
        r for r in results if "error" not in r and r.get("score_status") != "source_frontier"
    ]
    errored = [r for r in results if "error" in r]
    source_frontier = [
        r for r in results if "error" not in r and r.get("score_status") == "source_frontier"
    ]
    source_frontier_reasons = Counter(
        str(r.get("source_frontier_reason") or "unknown") for r in source_frontier
    )
    source_frontier_source_witness_role_counts = (
        _source_frontier_source_witness_role_counts(source_frontier)
    )
    source_frontier_source_witness_digest_coverage_counts = (
        _source_frontier_source_witness_digest_coverage_counts(source_frontier)
    )
    source_frontier_work_item_family_counts = (
        _source_frontier_work_item_field_counts(source_frontier, "frontier_family")
    )
    source_frontier_work_item_authorization_status_counts = (
        _source_frontier_work_item_field_counts(source_frontier, "authorization_status")
    )
    source_frontier_work_item_missing_proof_counts = (
        _source_frontier_work_item_sequence_field_counts(
            source_frontier,
            "required_proofs",
        )
    )
    source_chain_frontier_reasons = Counter(
        reason
        for r in results
        for reason in _source_chain_frontier_reasons_for_row(r)
    )
    source_chain_frontier_statutes: dict[str, list[str]] = {}
    for row in results:
        for reason in _source_chain_frontier_reasons_for_row(row):
            statute_id = str(row.get("statute_id") or "")
            if not statute_id:
                continue
            source_chain_frontier_statutes.setdefault(reason, []).append(statute_id)
    source_chain_frontier_statutes = {
        reason: sorted(statute_ids)
        for reason, statute_ids in sorted(source_chain_frontier_statutes.items())
    }
    non_manual_source_chain_frontier_statutes = sorted(
        {
            str(row.get("statute_id") or "")
            for row in results
            if str(row.get("statute_id") or "")
            and any(
                reason not in _SOURCE_CHAIN_COMPLETENESS_EXCLUDED_REASONS
                for reason in _source_chain_frontier_reasons_for_row(row)
            )
        }
    )
    replay_lens_frontier_statutes = sorted(
        {
            str(row.get("statute_id") or "")
            for row in results
            if str(row.get("statute_id") or "")
            and any(
                reason in _REPLAY_LENS_FRONTIER_REASONS
                for reason in _source_chain_frontier_reasons_for_row(row)
            )
        }
    )
    empty_effect_feed_frontier_statutes = sorted(
        {
            str(row.get("statute_id") or "")
            for row in results
            if str(row.get("statute_id") or "")
            and any(
                reason in _OFFICIAL_EMPTY_EFFECT_FEED_FRONTIER_REASONS
                for reason in _source_chain_frontier_reasons_for_row(row)
            )
        }
    )
    source_or_oracle_pathology_frontier_statutes = sorted(
        {
            str(row.get("statute_id") or "")
            for row in results
            if str(row.get("statute_id") or "")
            and any(
                reason in _SOURCE_OR_ORACLE_PATHOLOGY_FRONTIER_REASONS
                for reason in _source_chain_frontier_reasons_for_row(row)
            )
        }
    )
    source_or_oracle_pathology_frontier_reasons = {
        reason: count
        for reason, count in source_chain_frontier_reasons.items()
        if reason in _SOURCE_OR_ORACLE_PATHOLOGY_FRONTIER_REASONS
    }
    source_or_oracle_pathology_frontier_reason_statutes = {
        reason: statute_ids
        for reason, statute_ids in source_chain_frontier_statutes.items()
        if reason in _SOURCE_OR_ORACLE_PATHOLOGY_FRONTIER_REASONS
    }
    zero_oracle_retention = [
        r
        for r in scored
        if int(r.get("n_oracle") or 0) == 0 and int(r.get("n_replay") or 0) > 0
    ]
    zero_oracle_retention_reasons = Counter(
        reason
        for row in zero_oracle_retention
        for reason in _zero_oracle_retention_reasons_for_row(row)
    )
    zero_oracle_retention_reason_statutes: dict[str, list[str]] = {}
    for row in zero_oracle_retention:
        statute_id = str(row.get("statute_id") or "")
        if not statute_id:
            continue
        for reason in _zero_oracle_retention_reasons_for_row(row):
            zero_oracle_retention_reason_statutes.setdefault(reason, []).append(
                statute_id
            )
    zero_oracle_retention_reason_statutes = {
        reason: sorted(statute_ids)
        for reason, statute_ids in sorted(zero_oracle_retention_reason_statutes.items())
    }
    triage_buckets = Counter(_triage_bucket_for_row(r) for r in results)
    triage_bucket_statutes = _triage_bucket_statutes(results)
    manual_frontier_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_status_counts"
    )
    manual_frontier_rule_counts = _aggregate_row_count_maps(
        results, "manual_frontier_rule_counts"
    )
    manual_frontier_owner_phase_counts = _aggregate_row_count_maps(
        results, "manual_frontier_owner_phase_counts"
    )
    manual_frontier_authorization_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_authorization_status_counts"
    )
    manual_frontier_authorization_status_owner_phase_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_authorization_status_owner_phase_counts",
        )
    )
    manual_frontier_missing_proof_counts = _aggregate_row_count_maps(
        results, "manual_frontier_missing_proof_counts"
    )
    manual_frontier_work_item_family_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_family_counts"
    )
    manual_frontier_work_item_authorization_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_authorization_status_counts"
    )
    manual_frontier_work_item_candidate_operation_family_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_work_item_candidate_operation_family_counts",
        )
    )
    manual_frontier_work_item_required_validator_check_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_work_item_required_validator_check_counts",
        )
    )
    (
        manual_frontier_work_item_packet_ready_counts,
        manual_frontier_work_item_packet_missing_field_counts,
        manual_frontier_work_item_packet_target_resolution_coverage_counts,
        manual_frontier_work_item_packet_execution_authorization_validation_issue_counts,
        manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts,
    ) = _aggregate_manual_frontier_work_item_packet_counts(results)
    manual_frontier_work_item_target_resolution_status_counts = (
        _aggregate_manual_frontier_work_item_target_resolution_status_counts(results)
    )
    manual_frontier_work_item_target_resolution_gap_counts = _aggregate_row_count_maps(
        results,
        "manual_frontier_work_item_target_resolution_gap_counts",
    )
    manual_frontier_work_item_target_resolution_exempt_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_work_item_target_resolution_exempt_counts",
        )
    )
    manual_frontier_work_item_candidate_set_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_candidate_set_status_counts"
    )
    manual_frontier_work_item_candidate_set_gap_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_candidate_set_gap_counts"
    )
    manual_frontier_work_item_candidate_set_exempt_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_candidate_set_exempt_counts"
    )
    manual_frontier_work_item_source_membership_status_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_source_membership_status_counts"
        )
    )
    manual_frontier_work_item_source_membership_blocker_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_source_membership_blocker_counts"
        )
    )
    manual_frontier_work_item_exclusion_scope_status_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_exclusion_scope_status_counts"
        )
    )
    manual_frontier_work_item_exclusion_scope_blocker_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_exclusion_scope_blocker_counts"
        )
    )
    manual_frontier_work_item_proof_obligation_status_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_proof_obligation_status_counts"
        )
    )
    manual_frontier_work_item_proof_obligation_proved_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_proof_obligation_proved_counts"
        )
    )
    manual_frontier_work_item_proof_obligation_blocker_counts = (
        _aggregate_row_count_maps(
            results, "manual_frontier_work_item_proof_obligation_blocker_counts"
        )
    )
    manual_frontier_work_item_source_witness_role_counts = _aggregate_row_count_maps(
        results, "manual_frontier_work_item_source_witness_role_counts"
    )
    manual_frontier_work_item_source_witness_digest_coverage_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_work_item_source_witness_digest_coverage_counts",
        )
    )
    manual_frontier_work_item_missing_candidate_operation_family_count = sum(
        int(
            row.get(
                "manual_frontier_work_item_missing_candidate_operation_family_count"
            )
            or 0
        )
        for row in results
    )
    manual_frontier_work_item_missing_required_validator_checks_count = sum(
        int(
            row.get(
                "manual_frontier_work_item_missing_required_validator_checks_count"
            )
            or 0
        )
        for row in results
    )
    manual_frontier_rule_owner_phase_counts = _aggregate_row_count_maps(
        results, "manual_frontier_rule_owner_phase_counts"
    )
    compile_rejection_owner_phase_counts = _aggregate_row_count_maps(
        results, "compile_rejection_owner_phase_counts"
    )
    compile_rejection_authorization_status_counts = _aggregate_row_count_maps(
        results, "compile_rejection_authorization_status_counts"
    )
    compile_rejection_missing_proof_counts = _aggregate_row_count_maps(
        results, "compile_rejection_missing_proof_counts"
    )
    compile_rejection_rule_owner_phase_counts = _aggregate_row_count_maps(
        results, "compile_rejection_rule_owner_phase_counts"
    )
    blocking_compile_rejection_owner_phase_counts = _aggregate_row_count_maps(
        results, "blocking_compile_rejection_owner_phase_counts"
    )
    blocking_compile_rejection_authorization_status_counts = _aggregate_row_count_maps(
        results,
        "blocking_compile_rejection_authorization_status_counts",
    )
    blocking_compile_rejection_missing_proof_counts = _aggregate_row_count_maps(
        results,
        "blocking_compile_rejection_missing_proof_counts",
    )
    blocking_compile_rejection_rule_owner_phase_counts = _aggregate_row_count_maps(
        results, "blocking_compile_rejection_rule_owner_phase_counts"
    )
    manual_frontier_manual_compile_candidate_rule_counts = _aggregate_row_count_maps(
        results,
        "manual_frontier_manual_compile_candidate_rule_counts",
    )
    manual_frontier_manual_compile_candidate_rule_owner_phase_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_manual_compile_candidate_rule_owner_phase_counts",
        )
    )
    manual_frontier_deterministic_candidate_rule_counts = _aggregate_row_count_maps(
        results,
        "manual_frontier_deterministic_candidate_rule_counts",
    )
    manual_frontier_deterministic_candidate_rule_owner_phase_counts = (
        _aggregate_row_count_maps(
            results,
            "manual_frontier_deterministic_candidate_rule_owner_phase_counts",
        )
    )
    manual_frontier_template_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_template_status_counts"
    )
    manual_frontier_template_gap_status_counts = _aggregate_row_count_maps(
        results, "manual_frontier_template_gap_status_counts"
    )
    manual_frontier_template_gap_rule_counts = _aggregate_row_count_maps(
        results, "manual_frontier_template_gap_rule_counts"
    )
    mutation_boundary_result_code_counts = _aggregate_row_count_maps(
        results, "mutation_boundary_result_code_counts"
    )
    mutation_boundary_helper_counts = _aggregate_row_count_maps(
        results, "mutation_boundary_helper_counts"
    )
    mutation_boundary_proof_status_counts = _aggregate_row_count_maps(
        results, "mutation_boundary_proof_status_counts"
    )
    mutation_boundary_proof_rule_counts = _aggregate_row_count_maps(
        results, "mutation_boundary_proof_rule_counts"
    )
    mutation_boundary_proof_owner_phase_counts = _aggregate_row_count_maps(
        results, "mutation_boundary_proof_owner_phase_counts"
    )
    mutation_boundary_unexplained_rows = [
        r
        for r in scored
        if int(r.get("n_mutation_boundary_unexplained_reports") or 0) > 0
        or int(r.get("n_mutation_boundary_unexplained_paths") or 0) > 0
    ]
    active_unclassified_residuals = [
        r
        for r in results
        if _triage_bucket_for_row(r) in _ACTIVE_UNCLASSIFIED_RESIDUAL_BUCKETS
    ]
    agreement_residuals = [
        _agreement_residual_for_row(row).to_dict() for row in results
    ]
    deterministic_frontend_candidate_rows = [
        r
        for r in results
        if int(
            (r.get("manual_frontier_status_counts") or {}).get(
                "deterministic_frontend_candidate",
                0,
            )
            or 0
        )
        > 0
    ]
    comparison_core_rows = [r for r in scored if _is_comparison_core_row(r)]
    comparison_non_core_rows = [r for r in scored if not _is_comparison_core_row(r)]
    return {
        "scored": scored,
        "errored": errored,
        "source_frontier": source_frontier,
        "source_frontier_reasons": dict(sorted(source_frontier_reasons.items())),
        "source_frontier_source_witness_role_counts": (
            source_frontier_source_witness_role_counts
        ),
        "source_frontier_source_witness_digest_coverage_counts": (
            source_frontier_source_witness_digest_coverage_counts
        ),
        "source_frontier_work_item_family_counts": (
            source_frontier_work_item_family_counts
        ),
        "source_frontier_work_item_authorization_status_counts": (
            source_frontier_work_item_authorization_status_counts
        ),
        "source_frontier_work_item_missing_proof_counts": (
            source_frontier_work_item_missing_proof_counts
        ),
        "source_chain_frontier_reasons": dict(
            sorted(source_chain_frontier_reasons.items())
        ),
        "source_chain_frontier_statutes": source_chain_frontier_statutes,
        "non_manual_source_chain_frontier_count": len(
            non_manual_source_chain_frontier_statutes
        ),
        "non_manual_source_chain_frontier_statutes": (
            non_manual_source_chain_frontier_statutes
        ),
        "replay_lens_frontier_count": len(replay_lens_frontier_statutes),
        "replay_lens_frontier_statutes": replay_lens_frontier_statutes,
        "empty_effect_feed_frontier_count": len(empty_effect_feed_frontier_statutes),
        "empty_effect_feed_frontier_statutes": empty_effect_feed_frontier_statutes,
        "source_or_oracle_pathology_frontier_count": len(
            source_or_oracle_pathology_frontier_statutes
        ),
        "source_or_oracle_pathology_frontier_statutes": (
            source_or_oracle_pathology_frontier_statutes
        ),
        "source_or_oracle_pathology_frontier_reasons": (
            source_or_oracle_pathology_frontier_reasons
        ),
        "source_or_oracle_pathology_frontier_reason_statutes": (
            source_or_oracle_pathology_frontier_reason_statutes
        ),
        "triage_buckets": dict(sorted(triage_buckets.items())),
        "triage_bucket_statutes": triage_bucket_statutes,
        "agreement_residual_family_counts": _agreement_residual_field_counts(
            agreement_residuals,
            "family",
        ),
        "agreement_residual_status_counts": _agreement_residual_field_counts(
            agreement_residuals,
            "agreement_residual_status",
        ),
        "agreement_residual_owner_phase_counts": _agreement_residual_field_counts(
            agreement_residuals,
            "owner_phase",
        ),
        "agreement_residual_rule_counts": _agreement_residual_field_counts(
            agreement_residuals,
            "rule_id",
        ),
        "manual_frontier_status_counts": manual_frontier_status_counts,
        "manual_frontier_rule_counts": manual_frontier_rule_counts,
        "manual_frontier_owner_phase_counts": manual_frontier_owner_phase_counts,
        "manual_frontier_authorization_status_counts": (
            manual_frontier_authorization_status_counts
        ),
        "manual_frontier_authorization_status_owner_phase_counts": (
            manual_frontier_authorization_status_owner_phase_counts
        ),
        "manual_frontier_missing_proof_counts": manual_frontier_missing_proof_counts,
        "manual_frontier_work_item_family_counts": (
            manual_frontier_work_item_family_counts
        ),
        "manual_frontier_work_item_authorization_status_counts": (
            manual_frontier_work_item_authorization_status_counts
        ),
        "manual_frontier_work_item_candidate_operation_family_counts": (
            manual_frontier_work_item_candidate_operation_family_counts
        ),
        "manual_frontier_work_item_required_validator_check_counts": (
            manual_frontier_work_item_required_validator_check_counts
        ),
        "manual_frontier_work_item_packet_ready_counts": (
            manual_frontier_work_item_packet_ready_counts
        ),
        "manual_frontier_work_item_packet_missing_field_counts": (
            manual_frontier_work_item_packet_missing_field_counts
        ),
        "manual_frontier_work_item_packet_execution_authorization_validation_issue_counts": (
            manual_frontier_work_item_packet_execution_authorization_validation_issue_counts
        ),
        "manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts": (
            manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts
        ),
        "manual_frontier_work_item_packet_target_resolution_coverage_counts": (
            manual_frontier_work_item_packet_target_resolution_coverage_counts
        ),
        "manual_frontier_work_item_target_resolution_status_counts": (
            manual_frontier_work_item_target_resolution_status_counts
        ),
        "manual_frontier_work_item_target_resolution_gap_counts": (
            manual_frontier_work_item_target_resolution_gap_counts
        ),
        "manual_frontier_work_item_target_resolution_exempt_counts": (
            manual_frontier_work_item_target_resolution_exempt_counts
        ),
        "manual_frontier_work_item_candidate_set_status_counts": (
            manual_frontier_work_item_candidate_set_status_counts
        ),
        "manual_frontier_work_item_candidate_set_gap_counts": (
            manual_frontier_work_item_candidate_set_gap_counts
        ),
        "manual_frontier_work_item_candidate_set_exempt_counts": (
            manual_frontier_work_item_candidate_set_exempt_counts
        ),
        "manual_frontier_work_item_source_membership_status_counts": (
            manual_frontier_work_item_source_membership_status_counts
        ),
        "manual_frontier_work_item_source_membership_blocker_counts": (
            manual_frontier_work_item_source_membership_blocker_counts
        ),
        "manual_frontier_work_item_exclusion_scope_status_counts": (
            manual_frontier_work_item_exclusion_scope_status_counts
        ),
        "manual_frontier_work_item_exclusion_scope_blocker_counts": (
            manual_frontier_work_item_exclusion_scope_blocker_counts
        ),
        "manual_frontier_work_item_proof_obligation_status_counts": (
            manual_frontier_work_item_proof_obligation_status_counts
        ),
        "manual_frontier_work_item_proof_obligation_proved_counts": (
            manual_frontier_work_item_proof_obligation_proved_counts
        ),
        "manual_frontier_work_item_proof_obligation_blocker_counts": (
            manual_frontier_work_item_proof_obligation_blocker_counts
        ),
        "manual_frontier_work_item_source_witness_role_counts": (
            manual_frontier_work_item_source_witness_role_counts
        ),
        "manual_frontier_work_item_source_witness_digest_coverage_counts": (
            manual_frontier_work_item_source_witness_digest_coverage_counts
        ),
        "manual_frontier_work_item_missing_candidate_operation_family_count": (
            manual_frontier_work_item_missing_candidate_operation_family_count
        ),
        "manual_frontier_work_item_missing_required_validator_checks_count": (
            manual_frontier_work_item_missing_required_validator_checks_count
        ),
        "manual_frontier_rule_owner_phase_counts": (
            manual_frontier_rule_owner_phase_counts
        ),
        "compile_rejection_owner_phase_counts": compile_rejection_owner_phase_counts,
        "compile_rejection_authorization_status_counts": (
            compile_rejection_authorization_status_counts
        ),
        "compile_rejection_missing_proof_counts": (
            compile_rejection_missing_proof_counts
        ),
        "compile_rejection_rule_owner_phase_counts": (
            compile_rejection_rule_owner_phase_counts
        ),
        "blocking_compile_rejection_owner_phase_counts": (
            blocking_compile_rejection_owner_phase_counts
        ),
        "blocking_compile_rejection_authorization_status_counts": (
            blocking_compile_rejection_authorization_status_counts
        ),
        "blocking_compile_rejection_missing_proof_counts": (
            blocking_compile_rejection_missing_proof_counts
        ),
        "blocking_compile_rejection_rule_owner_phase_counts": (
            blocking_compile_rejection_rule_owner_phase_counts
        ),
        "manual_frontier_manual_compile_candidate_rule_counts": (
            manual_frontier_manual_compile_candidate_rule_counts
        ),
        "manual_frontier_manual_compile_candidate_rule_owner_phase_counts": (
            manual_frontier_manual_compile_candidate_rule_owner_phase_counts
        ),
        "manual_frontier_deterministic_candidate_rule_counts": (
            manual_frontier_deterministic_candidate_rule_counts
        ),
        "manual_frontier_deterministic_candidate_rule_owner_phase_counts": (
            manual_frontier_deterministic_candidate_rule_owner_phase_counts
        ),
        "manual_frontier_template_status_counts": manual_frontier_template_status_counts,
        "manual_frontier_template_gap_status_counts": (
            manual_frontier_template_gap_status_counts
        ),
        "manual_frontier_template_gap_rule_counts": (
            manual_frontier_template_gap_rule_counts
        ),
        "manual_frontier_template_gap_count": sum(
            int(count or 0)
            for count in manual_frontier_template_gap_status_counts.values()
        ),
        "mutation_boundary_event_count": sum(
            int(r.get("n_mutation_events") or 0) for r in scored
        ),
        "mutation_boundary_report_count": sum(
            int(r.get("n_mutation_boundary_reports") or 0) for r in scored
        ),
        "mutation_boundary_unexplained_report_count": sum(
            int(r.get("n_mutation_boundary_unexplained_reports") or 0)
            for r in scored
        ),
        "mutation_boundary_unexplained_path_count": sum(
            int(r.get("n_mutation_boundary_unexplained_paths") or 0)
            for r in scored
        ),
        "mutation_boundary_result_code_counts": mutation_boundary_result_code_counts,
        "mutation_boundary_helper_counts": mutation_boundary_helper_counts,
        "mutation_boundary_proof_status_counts": (
            mutation_boundary_proof_status_counts
        ),
        "mutation_boundary_proof_rule_counts": mutation_boundary_proof_rule_counts,
        "mutation_boundary_proof_owner_phase_counts": (
            mutation_boundary_proof_owner_phase_counts
        ),
        "mutation_boundary_unexplained_statutes": sorted(
            str(r.get("statute_id") or "")
            for r in mutation_boundary_unexplained_rows
        ),
        "active_unclassified_residual_count": len(active_unclassified_residuals),
        "active_unclassified_residual_statutes": sorted(
            str(r.get("statute_id") or "") for r in active_unclassified_residuals
        ),
        "deterministic_frontend_candidate_count": sum(
            int(
                (r.get("manual_frontier_status_counts") or {}).get(
                    "deterministic_frontend_candidate",
                    0,
                )
                or 0
            )
            for r in deterministic_frontend_candidate_rows
        ),
        "deterministic_frontend_candidate_statutes": sorted(
            str(r.get("statute_id") or "")
            for r in deterministic_frontend_candidate_rows
        ),
        "comparison_core_count": len(comparison_core_rows),
        "comparison_core_statutes": sorted(
            str(r.get("statute_id") or "") for r in comparison_core_rows
        ),
        "comparison_core_mean_aligned": _mean_score_field(
            comparison_core_rows,
            "aligned",
        ),
        "comparison_core_mean_aligned_excluding_grounding_collateral": _mean_score_field(
            comparison_core_rows,
            "aligned_excluding_grounding_collateral",
            fallback_field="aligned",
        ),
        "comparison_non_core_count": len(comparison_non_core_rows),
        "comparison_non_core_bucket_counts": dict(
            sorted(
                Counter(
                    _triage_bucket_for_row(row) for row in comparison_non_core_rows
                ).items()
            )
        ),
        "comparison_non_core_statutes": sorted(
            str(r.get("statute_id") or "") for r in comparison_non_core_rows
        ),
        "zero_oracle_retention_count": len(zero_oracle_retention),
        "zero_oracle_retention_statutes": sorted(
            str(r.get("statute_id") or "") for r in zero_oracle_retention
        ),
        "zero_oracle_retention_eids": sum(
            int(r.get("n_zero_oracle_retention_eids") or r.get("n_replay") or 0)
            for r in zero_oracle_retention
        ),
        "zero_oracle_retention_reasons": dict(
            sorted(zero_oracle_retention_reasons.items())
        ),
        "zero_oracle_retention_reason_statutes": (
            zero_oracle_retention_reason_statutes
        ),
    }


def uk_broad_baseline_report_jsonable(
    results: list[dict[str, Any]],
    *,
    ids: list[str],
    snapshot_path: Path | None = None,
) -> dict[str, Any]:
    """Build the typed report envelope for broad-baseline agreement output."""
    summary_payload = _broad_baseline_summary_payload(summarize_results(results))
    return EvidenceSurfaceReport(
        jurisdiction="uk",
        report_kind="uk_broad_baseline_agreement_report",
        schema="lawvm.uk_broad_baseline_agreement_report.v1",
        truth_claim="uk_replay_oracle_agreement_regression_guard_not_source_truth",
        replay_claims=True,
        canonical_effect_claims=False,
        candidate_effect_claims=False,
        dry_run_claims=False,
        agreement_claims=True,
        summary=summary_payload,
        filters={
            "ids": list(ids),
            "snapshot_path": str(snapshot_path) if snapshot_path is not None else "",
        },
        filtered_summary=summary_payload,
        rows=tuple(_row_with_agreement_residual(row) for row in results),
        rows_truncated=False,
        written_paths=(str(snapshot_path),) if snapshot_path is not None else (),
        detail={
            "source_footing": "farchive_enacted_xml_plus_current_xml_oracle_eid_sets",
            "agreement_surface": "replay_eid_set_vs_current_oracle_eid_set",
            "safe_default": "treat_disagreement_as_residual_until_phase_owned",
            "forbidden_shortcuts": (
                "oracle_score_as_source_truth",
                "agreement_as_execution_authorization",
                "candidate_effect_as_replay_authority",
                "source_or_target_over_promotion",
            ),
            "next_promotion_requires": (
                "source_identity",
                "target_identity",
                "payload_identity",
                "temporal_extent_applicability",
                "mutation_boundary_proof",
            ),
        },
    ).to_dict()


def _broad_baseline_summary_payload(summary: dict[str, Any]) -> dict[str, Any]:
    payload = {
        key: value
        for key, value in summary.items()
        if key not in {"scored", "errored", "source_frontier"}
    }
    payload["scored_count"] = len(summary.get("scored") or ())
    payload["errored_count"] = len(summary.get("errored") or ())
    payload["source_frontier_count"] = len(summary.get("source_frontier") or ())
    payload["completion_gate_failure_counts"] = _completion_gate_failure_counts(
        summary
    )
    payload["completion_gate_clean"] = not payload["completion_gate_failure_counts"]
    return payload


def _triage_bucket_statutes(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    statutes_by_bucket: dict[str, list[str]] = {}
    for row in rows:
        statute_id = str(row.get("statute_id") or "")
        if not statute_id:
            continue
        bucket = _triage_bucket_for_row(row)
        statutes_by_bucket.setdefault(bucket, []).append(statute_id)
    return {
        bucket: sorted(statute_ids)
        for bucket, statute_ids in sorted(statutes_by_bucket.items())
    }


def _annotate_row_work_selection(row: dict[str, Any]) -> dict[str, Any]:
    """Add machine-readable work-selection fields to one baseline row."""
    row["triage_bucket"] = _triage_bucket_for_row(row)
    source_chain_reasons = _source_chain_frontier_reasons_for_row(row)
    row["source_chain_frontier"] = bool(source_chain_reasons)
    row["source_chain_frontier_reason"] = (
        source_chain_reasons[0] if source_chain_reasons else ""
    )
    row["source_chain_frontier_reasons"] = list(source_chain_reasons)
    zero_oracle_reasons = _zero_oracle_retention_reasons_for_row(row)
    row["zero_oracle_retention_reason"] = (
        zero_oracle_reasons[0] if zero_oracle_reasons else ""
    )
    row["zero_oracle_retention_reasons"] = list(zero_oracle_reasons)
    row["agreement_residual"] = _agreement_residual_for_row(row).to_dict()
    if row.get("score_status") == "source_frontier":
        row["source_frontier_work_item"] = _source_frontier_work_item(row).to_dict()
    return row


def _row_with_agreement_residual(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    return _annotate_row_work_selection(payload)


def _source_frontier_work_item(row: Mapping[str, Any]) -> FrontierWorkItem:
    statute_id = str(row.get("statute_id") or "")
    reason = str(row.get("source_frontier_reason") or "unknown")
    primary_side = _source_frontier_primary_side(row)
    primary_witness = row.get(f"{primary_side}_source_witness")
    primary_witness = primary_witness if isinstance(primary_witness, Mapping) else {}
    source_unit_id = f"{primary_side}:{reason}"
    return FrontierWorkItem(
        work_item_id=f"uk-source-frontier-{statute_id}-{reason}",
        jurisdiction="uk",
        source_artifact_id=statute_id,
        source_unit_id=source_unit_id,
        source_witness=primary_witness,
        compare_witness={
            "base_source_status": str(row.get("base_source_status") or ""),
            "oracle_source_status": str(row.get("oracle_source_status") or ""),
            "source_frontier_reason": reason,
            "triage_bucket": str(row.get("triage_bucket") or ""),
        },
        owner_phase=UK_PHASE_AFFECTING_SOURCE_EXTRACTION,
        frontier_family=f"uk_source_frontier_{reason}",
        frontier_status="source_footing_gap",
        candidate_operation_family="source_footing_resolution",
        required_claim_kind="source_footing_resolution",
        required_validator_checks=(
            "locate_authoritative_base_oracle_xml_source",
            "classify_source_pathology_without_replay_promotion",
        ),
        required_proofs=(
            "source_identity",
            "official_source_body_or_accepted_source_pathology",
        ),
        safe_default="classify_source_frontier_without_replay",
        forbidden_shortcuts=(
            "metadata_only_xml_as_executable_text",
            "oracle_metadata_as_source_truth",
            "source_frontier_as_replay_authorization",
        ),
        executable=False,
        replay_authorized=False,
        authorization_status="source_footing_gap",
        detail={
            "statute_id": statute_id,
            "source_frontier_reason": reason,
            "base_source_witness": row.get("base_source_witness") or {},
            "oracle_source_witness": row.get("oracle_source_witness") or {},
            "base_source_witness_digest_coverage": (
                _source_frontier_source_witness_digest_coverage(row, "base")
            ),
            "oracle_source_witness_digest_coverage": (
                _source_frontier_source_witness_digest_coverage(row, "oracle")
            ),
            "truth_claim": "source_frontier_work_item_not_replay_authorization",
        },
    )


def _source_frontier_primary_side(row: Mapping[str, Any]) -> str:
    reason = str(row.get("source_frontier_reason") or "")
    if reason.startswith("oracle_"):
        return "oracle"
    if reason == "base_and_oracle_metadata_only":
        return "base"
    return "base"


def _triage_bucket_for_row(row: dict[str, Any]) -> str:
    """Classify a broad-baseline row for work selection, not scoring."""
    if "error" in row:
        return "error"
    if row.get("score_status") == "source_frontier":
        reason = str(row.get("source_frontier_reason") or "unknown")
        return f"source_frontier:{reason}"
    n_oracle = int(row.get("n_oracle") or 0)
    n_replay = int(row.get("n_replay") or 0)
    if n_oracle == 0 and n_replay > 0:
        return "zero_oracle_retention"
    if row.get("base_source_status") == "metadata_only":
        return "base_metadata_only_frontier"
    aligned = float(row.get("aligned") or 0.0)
    aligned_no_gc = float(row.get("aligned_excluding_grounding_collateral", aligned) or 0.0)
    unaligned = float(row.get("unaligned") or 0.0)
    if aligned_no_gc >= _HIGH_FIDELITY_AFTER_GROUNDING_THRESHOLD:
        return "high_fidelity_after_grounding"
    if unaligned >= _STRUCTURAL_MATCH_THRESHOLD:
        return "structural_match_eid_scheme_residual"
    if row.get("n_ops") is not None and int(row.get("n_ops") or 0) == 0:
        if _has_effect_feed_absent_record(row):
            return "effect_feed_absent_frontier"
        if int(row.get("n_effects") or 0) == 0:
            return "no_effect_rows_frontier"
        if int(row.get("n_compile_rejections") or 0) > 0:
            return "nonreplay_effect_frontier"
        return "no_compiled_ops_frontier"
    if (
        int(row.get("n_grounding_collateral") or 0) > 0
        and aligned_no_gc - aligned >= _GROUNDING_DOMINATED_DELTA_THRESHOLD
    ):
        return "grounding_dominated_residual"
    if _is_manual_compile_frontier_residual(row):
        return "manual_compile_frontier_residual"
    if _is_oracle_expansion_without_effects(row):
        return "oracle_expansion_without_effects"
    if _is_temporal_commencement_frontier(row):
        return "temporal_commencement_frontier"
    if _is_source_backed_temporal_recovery_oracle_residual(row):
        return "source_backed_temporal_recovery_oracle_residual"
    if int(row.get("n_retained_repeal_oracle_targets") or 0) > 0:
        return "retained_repeal_oracle_branch"
    if _is_compile_rejection_dominated_residual(row):
        return "compile_rejection_dominated_residual"
    if _is_retained_eu_mixed_representation_residual(row):
        return "retained_eu_mixed_representation_residual"
    if _is_retained_eu_schedule_oracle_granularity_residual(row):
        return "retained_eu_schedule_oracle_granularity_residual"
    if _is_body_nested_list_oracle_granularity_residual(row):
        return "body_nested_list_oracle_granularity_residual"
    if _is_body_oracle_collapsed_range_granularity_residual(row):
        return "body_oracle_collapsed_range_granularity_residual"
    if _is_body_oracle_first_paragraph_sectionization_residual(row):
        return "body_oracle_first_paragraph_sectionization_residual"
    if _is_bounded_low_volume_residual(row):
        return "bounded_low_volume_residual"
    if _is_oracle_addition_source_chain_frontier(row):
        return "oracle_addition_source_chain_frontier"
    return "residual_after_grounding"


def _is_comparison_core_row(row: dict[str, Any]) -> bool:
    bucket = _triage_bucket_for_row(row)
    return (
        not bucket.startswith("source_frontier:")
        and bucket not in _NON_CORE_COMPARISON_TRIAGE_BUCKETS
    )


def _mean_score_field(
    rows: list[dict[str, Any]],
    field: str,
    *,
    fallback_field: str | None = None,
) -> float | None:
    values: list[float] = []
    for row in rows:
        value = row.get(field)
        if value is None and fallback_field is not None:
            value = row.get(fallback_field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values.append(float(value))
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _agreement_residual_for_row(row: dict[str, Any]) -> AgreementResidual:
    """Project one broad-baseline row into the shared agreement residual shape."""
    bucket = _triage_bucket_for_row(row)
    family = _agreement_residual_family(bucket)
    status = _agreement_residual_status(bucket, row)
    return AgreementResidual(
        residual_id=f"uk-broad:{str(row.get('statute_id') or 'unknown')}",
        jurisdiction="uk",
        agreement_surface="replay_eid_set_vs_current_oracle_eid_set",
        family=family,
        agreement_residual_status=status,
        owner_phase=_agreement_residual_owner_phase(bucket),
        rule_id=f"uk_broad_{bucket}",
        source_artifact_id=str(row.get("statute_id") or ""),
        replay_count=_nonnegative_int(row.get("n_replay")),
        oracle_count=_nonnegative_int(row.get("n_oracle")),
        missing_proofs=_agreement_residual_missing_proofs(bucket, row),
        safe_default="classify_residual_without_replay_promotion",
        forbidden_shortcuts=(
            "oracle_score_as_source_truth",
            "agreement_as_execution_authorization",
            "source_or_target_over_promotion",
        ),
        detail={
            "triage_bucket": bucket,
            "score_status": str(row.get("score_status") or ""),
            "aligned": row.get("aligned"),
            "aligned_excluding_grounding_collateral": row.get(
                "aligned_excluding_grounding_collateral"
            ),
            "unaligned": row.get("unaligned"),
            "source_frontier_reason": str(row.get("source_frontier_reason") or ""),
            "source_chain_frontier_reasons": _source_chain_frontier_reasons_for_row(row),
            "n_grounding_collateral": _nonnegative_int(
                row.get("n_grounding_collateral")
            ),
            "n_only_in_oracle": _nonnegative_int(row.get("n_only_in_oracle")),
            "n_only_in_replayed": _nonnegative_int(row.get("n_only_in_replayed")),
            "oracle_only_eid_samples": row.get("oracle_only_eid_samples") or [],
            "replay_only_eid_samples": row.get("replay_only_eid_samples") or [],
            "manual_frontier_status_counts": row.get("manual_frontier_status_counts")
            or {},
            "compile_rejection_rule_counts": row.get("compile_rejection_rule_counts")
            or {},
        },
    )


def _agreement_residual_family(bucket: str) -> AgreementResidualFamily:
    if bucket == "error":
        return "error"
    if bucket.startswith("source_frontier:"):
        return "source_footing_gap"
    if bucket in {
        "base_metadata_only_frontier",
        "zero_oracle_retention",
    }:
        return "non_commensurable_surface"
    if bucket in {
        "effect_feed_absent_frontier",
        "no_compiled_ops_frontier",
        "no_effect_rows_frontier",
        "nonreplay_effect_frontier",
        "oracle_addition_source_chain_frontier",
        "oracle_expansion_without_effects",
        "temporal_commencement_frontier",
    }:
        return "source_footing_gap"
    if bucket == "manual_compile_frontier_residual":
        return "accepted_non_executable_frontier"
    if bucket == "retained_repeal_oracle_branch":
        return "oracle_editorial_pathology"
    if bucket == "source_backed_temporal_recovery_oracle_residual":
        return "temporal_mismatch"
    if bucket in {
        "bounded_low_volume_residual",
        "body_oracle_collapsed_range_granularity_residual",
        "body_oracle_first_paragraph_sectionization_residual",
        "body_nested_list_oracle_granularity_residual",
        "retained_eu_schedule_oracle_granularity_residual",
        "retained_eu_mixed_representation_residual",
        "structural_match_eid_scheme_residual",
    }:
        return "topology_granularity_mismatch"
    if bucket == "grounding_dominated_residual":
        return "target_recovery_mismatch"
    if bucket == "high_fidelity_after_grounding":
        return "agreement"
    if bucket in {
        "compile_rejection_dominated_residual",
        "residual_after_grounding",
    }:
        return "replay_bug"
    return "unknown"


def _agreement_residual_status(bucket: str, row: dict[str, Any]) -> AgreementResidualStatus:
    if bucket == "error":
        return "error"
    if bucket.startswith("source_frontier:") or bucket.endswith("_frontier"):
        return "frontier"
    if bucket in {
        "manual_compile_frontier_residual",
        "oracle_expansion_without_effects",
        "zero_oracle_retention",
        "base_metadata_only_frontier",
        "body_oracle_collapsed_range_granularity_residual",
        "body_oracle_first_paragraph_sectionization_residual",
        "body_nested_list_oracle_granularity_residual",
        "retained_repeal_oracle_branch",
        "retained_eu_schedule_oracle_granularity_residual",
    }:
        return "frontier"
    aligned = float(
        row.get("aligned_excluding_grounding_collateral")
        or row.get("aligned")
        or 0.0
    )
    misses = _nonnegative_int(row.get("n_only_in_oracle")) + _nonnegative_int(
        row.get("n_only_in_replayed")
    )
    if bucket == "high_fidelity_after_grounding" and aligned >= 100.0 and misses == 0:
        return "agrees"
    return "residual"


def _agreement_residual_owner_phase(bucket: str) -> str:
    if bucket == "error":
        return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    if bucket.startswith("source_frontier:"):
        return UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    if bucket in {
        "base_metadata_only_frontier",
        "body_oracle_collapsed_range_granularity_residual",
        "body_oracle_first_paragraph_sectionization_residual",
        "body_nested_list_oracle_granularity_residual",
        "zero_oracle_retention",
        "retained_repeal_oracle_branch",
        "retained_eu_schedule_oracle_granularity_residual",
        "source_backed_temporal_recovery_oracle_residual",
        "structural_match_eid_scheme_residual",
    }:
        return UK_PHASE_COMPARE_ORACLE_CLASSIFICATION
    if bucket in {
        "effect_feed_absent_frontier",
        "no_effect_rows_frontier",
        "nonreplay_effect_frontier",
        "oracle_addition_source_chain_frontier",
        "oracle_expansion_without_effects",
        "temporal_commencement_frontier",
    }:
        return UK_PHASE_EFFECT_METADATA_FRONTEND
    if bucket == "manual_compile_frontier_residual":
        return UK_PHASE_TYPED_ELABORATION
    if bucket == "no_compiled_ops_frontier":
        return UK_PHASE_CANONICAL_OP_COMPILATION
    if bucket == "grounding_dominated_residual":
        return UK_PHASE_COMPARE_ORACLE_CLASSIFICATION
    if bucket in {
        "compile_rejection_dominated_residual",
        "residual_after_grounding",
    }:
        return UK_PHASE_REPLAY_INVARIANTS
    return UK_PHASE_COMPARE_ORACLE_CLASSIFICATION


def _agreement_residual_missing_proofs(
    bucket: str,
    row: dict[str, Any],
) -> tuple[str, ...]:
    proofs: list[str] = []
    if bucket == "error":
        proofs.append("successful_execution")
    if bucket.startswith("source_frontier:") or bucket in {
        "effect_feed_absent_frontier",
        "no_effect_rows_frontier",
        "nonreplay_effect_frontier",
        "no_compiled_ops_frontier",
        "oracle_addition_source_chain_frontier",
        "oracle_expansion_without_effects",
    }:
        proofs.append("source_identity")
    if bucket == "oracle_addition_source_chain_frontier":
        proofs.append("source_chain_completeness")
    if bucket == "temporal_commencement_frontier":
        proofs.append("temporal_extent_applicability")
    if bucket == "source_backed_temporal_recovery_oracle_residual":
        proofs.append("oracle_temporal_commensurability")
    if bucket in {
        "base_metadata_only_frontier",
        "zero_oracle_retention",
    }:
        proofs.append("commensurable_oracle_surface")
    if bucket == "manual_compile_frontier_residual":
        proofs.extend(
            (
                "target_identity",
                "payload_or_boundary_identity",
                "mutation_boundary_proof",
            )
        )
    if bucket in {
        "compile_rejection_dominated_residual",
        "residual_after_grounding",
    }:
        proofs.append("canonical_operation_compilation")
    if bucket in {
        "bounded_low_volume_residual",
        "body_oracle_collapsed_range_granularity_residual",
        "body_oracle_first_paragraph_sectionization_residual",
        "body_nested_list_oracle_granularity_residual",
        "retained_eu_schedule_oracle_granularity_residual",
        "retained_eu_mixed_representation_residual",
        "structural_match_eid_scheme_residual",
    }:
        proofs.append("topology_or_eid_scheme_reconciliation")
    if bucket == "grounding_dominated_residual":
        proofs.append("target_identity")
    if _nonnegative_int(row.get("n_mutation_boundary_unexplained_paths")) > 0:
        proofs.append("mutation_boundary_proof")
    return tuple(dict.fromkeys(proofs))


def _agreement_residual_field_counts(
    residuals: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts = Counter(str(residual.get(field) or "unknown") for residual in residuals)
    return dict(sorted(counts.items()))


def _nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    return 0


def _source_chain_frontier_reason_for_row(row: dict[str, Any]) -> str:
    """Classify acquisition/source-chain rows without changing score buckets."""
    reasons = _source_chain_frontier_reasons_for_row(row)
    return reasons[0] if reasons else ""


def _source_chain_frontier_reasons_for_row(row: dict[str, Any]) -> tuple[str, ...]:
    """Classify acquisition/source-chain reasons without changing score buckets."""
    reasons: list[str] = []
    bucket = _triage_bucket_for_row(row)
    if bucket.startswith("source_frontier:"):
        reasons.append(bucket.removeprefix("source_frontier:"))
    elif bucket == "effect_feed_absent_frontier":
        reasons.append("effect_feed_pages_absent")
    elif bucket == "oracle_addition_source_chain_frontier":
        reasons.append("oracle_addition_changeid_source_chain_gap")
    elif bucket == "no_effect_rows_frontier":
        if _has_empty_effect_feed_record(row):
            reasons.append("effect_feed_empty")
        else:
            reasons.append("effect_rows_absent_or_unpublished")
    elif bucket == "nonreplay_effect_frontier":
        if _has_replay_lens_or_source_insufficient_only_manual_frontier(row):
            reasons.append("effect_rows_not_admitted_by_replay_lens")
        elif _has_manual_compile_candidate_record(row):
            reasons.append("manual_frontier_manual_compile_candidate")
        elif _has_missing_structural_payload_record(row):
            reasons.append("effect_rows_missing_structural_payload")
        else:
            reasons.append("effect_rows_nonreplayable")
    if _has_effect_feed_absent_record(row):
        reasons.append("effect_feed_pages_absent")
    if _has_empty_effect_feed_record(row):
        reasons.append("effect_feed_empty")
    if _has_replay_lens_or_source_insufficient_only_manual_frontier(row):
        reasons.append("effect_rows_not_admitted_by_replay_lens")
    if _has_manual_frontier_source_insufficient_record(row):
        reasons.append("manual_frontier_source_insufficient")
    if _has_manual_frontier_source_chain_text_patch_gap(row):
        reasons.append("manual_frontier_source_chain_text_patch_gap")
    return tuple(dict.fromkeys(reasons))


def _zero_oracle_retention_reasons_for_row(row: dict[str, Any]) -> tuple[str, ...]:
    """Explain zero-oracle rows without treating the oracle as replay authority."""
    if int(row.get("n_oracle") or 0) != 0 or int(row.get("n_replay") or 0) <= 0:
        return ()
    reasons: list[str] = []
    if _has_effect_feed_absent_record(row):
        reasons.append("effect_feed_pages_absent")
    if _has_empty_effect_feed_record(row):
        reasons.append("effect_feed_empty")
    oracle_status = str(row.get("oracle_source_status") or "")
    if oracle_status and oracle_status != "available":
        reasons.append(f"oracle_{oracle_status}")
    elif bool(row.get("oracle_source_has_body")) or bool(
        row.get("oracle_source_has_schedules")
    ):
        reasons.append("oracle_current_projection_no_live_eids")
    else:
        reasons.append("oracle_current_projection_no_structural_eids")
    return tuple(dict.fromkeys(reasons))


def _is_compile_rejection_dominated_residual(row: dict[str, Any]) -> bool:
    """Classify rows where explicit compile rejections dominate missing oracle state."""
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections < _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    return n_only_in_oracle >= max(1, n_only_in_replayed)


def _has_effect_feed_absent_record(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("uk_effect_feed_pages_absent_recorded") or 0) > 0


def _has_empty_effect_feed_record(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("uk_effect_feed_empty_recorded") or 0) > 0


def _is_temporal_commencement_frontier(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    temporal_count = int(
        counts.get("uk_effect_undated_applied_si_commencement_unresolved") or 0
    )
    if temporal_count <= 0:
        return False
    other_counts = {
        str(rule_id): int(count or 0)
        for rule_id, count in counts.items()
        if str(rule_id) != "uk_effect_undated_applied_si_commencement_unresolved"
        and int(count or 0) > 0
    }
    return not other_counts


def _is_source_backed_temporal_recovery_oracle_residual(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    if int(counts.get("uk_effect_undated_applied_si_commencement_date") or 0) <= 0:
        return False
    if int(counts.get("uk_effect_undated_applied_si_commencement_unresolved") or 0) > 0:
        return False
    if int(row.get("n_blocking_compile_rejections") or 0) > 0:
        return False
    if int(row.get("n_ops") or 0) <= 0:
        return False
    return (
        int(row.get("n_only_in_oracle") or 0) + int(row.get("n_only_in_replayed") or 0)
    ) > 0


def _is_oracle_expansion_without_effects(row: dict[str, Any]) -> bool:
    """Classify current-oracle schedule expansions not backed by replay effects."""
    n_oracle_only_schedule_eids = int(row.get("n_oracle_only_schedule_eids") or 0)
    if n_oracle_only_schedule_eids <= 0:
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    if n_only_in_oracle <= 0:
        return False
    if bool(row.get("has_schedule_targeting_ops")):
        return False
    return n_oracle_only_schedule_eids * 2 >= n_only_in_oracle


def _is_oracle_addition_source_chain_frontier(row: dict[str, Any]) -> bool:
    """Classify oracle-only additions whose source-chain ids are not compiled."""
    if not bool(row.get("oracle_source_has_body")) and not bool(
        row.get("oracle_source_has_schedules")
    ):
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    if n_only_in_oracle <= 0:
        return False
    if int(row.get("n_only_in_replayed") or 0) > 0:
        return False
    if int(row.get("n_blocking_compile_rejections") or 0) > 0:
        return False
    n_uncompiled_additions = int(
        row.get("n_oracle_only_uncompiled_addition_eids") or 0
    )
    if n_uncompiled_additions != n_only_in_oracle:
        return False
    uncompiled_change_ids = row.get("oracle_only_uncompiled_addition_change_ids")
    return isinstance(uncompiled_change_ids, list | tuple) and bool(
        uncompiled_change_ids
    )


def _has_missing_structural_payload_record(row: dict[str, Any]) -> bool:
    counts = row.get("compile_rejection_rule_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("uk_effect_missing_structural_payload_rejected") or 0) > 0


def _has_manual_frontier_source_insufficient_record(row: dict[str, Any]) -> bool:
    counts = row.get("manual_frontier_status_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("source_insufficient") or 0) > 0


def _has_manual_compile_candidate_record(row: dict[str, Any]) -> bool:
    counts = row.get("manual_frontier_status_counts") or {}
    if not isinstance(counts, dict):
        return False
    return int(counts.get("manual_compile_candidate") or 0) > 0


def _has_manual_frontier_source_chain_text_patch_gap(row: dict[str, Any]) -> bool:
    rule_counts = row.get("manual_frontier_rule_counts") or {}
    if isinstance(rule_counts, dict) and any(
        int(rule_counts.get(rule_id) or 0) > 0
        for rule_id in _MANUAL_FRONTIER_SOURCE_CHAIN_TEXT_PATCH_RULES
    ):
        return True
    family_counts = (
        row.get("manual_frontier_work_item_candidate_operation_family_counts") or {}
    )
    if not isinstance(family_counts, dict):
        return False
    return (
        int(
            family_counts.get(
                _MANUAL_FRONTIER_SOURCE_CHAIN_TEXT_PATCH_OPERATION_FAMILY
            )
            or 0
        )
        > 0
    )


def _has_replay_lens_or_source_insufficient_only_manual_frontier(
    row: dict[str, Any],
) -> bool:
    counts = row.get("manual_frontier_status_counts") or {}
    if not isinstance(counts, dict):
        return False
    total = sum(int(value or 0) for value in counts.values())
    if total == 0:
        return False
    replay_lens_count = int(counts.get("non_textual_or_out_of_scope") or 0)
    source_insufficient_count = int(counts.get("source_insufficient") or 0)
    replay_authorized_count = int(counts.get("deterministic_frontend_supported") or 0)
    if replay_lens_count == 0:
        return False
    return replay_lens_count + source_insufficient_count + replay_authorized_count == total


def _is_retained_eu_mixed_representation_residual(row: dict[str, Any]) -> bool:
    """Classify retained-EU rows with unresolved source and replay-only shape noise."""
    statute_id = str(row.get("statute_id") or "")
    if not statute_id.startswith("eur/"):
        return False
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections < _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    return n_only_in_oracle > 0 and n_only_in_replayed > 0


def _is_retained_eu_schedule_oracle_granularity_residual(row: dict[str, Any]) -> bool:
    """Classify bounded retained-EU schedule oracle surplus without replay promotion."""
    statute_id = str(row.get("statute_id") or "")
    if not statute_id.startswith("eur/"):
        return False
    if not bool(row.get("base_source_has_schedules")):
        return False
    if not bool(row.get("oracle_source_has_schedules")):
        return False
    if bool(row.get("base_source_has_body")) or bool(row.get("oracle_source_has_body")):
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    if n_only_in_oracle <= 0 or n_only_in_replayed > 0:
        return False
    if n_only_in_oracle > _LOW_VOLUME_RESIDUAL_MAX_MISSES:
        return False
    if int(row.get("n_blocking_compile_rejections") or 0) > 0:
        return False
    n_effects = int(row.get("n_effects") or 0)
    n_ops = int(row.get("n_ops") or 0)
    if n_effects <= 0 or n_ops <= 0:
        return False
    status_counts = row.get("manual_frontier_status_counts") or {}
    if not isinstance(status_counts, dict):
        return False
    return int(status_counts.get("deterministic_frontend_supported") or 0) >= n_effects


def _is_body_nested_list_oracle_granularity_residual(row: dict[str, Any]) -> bool:
    """Classify bounded body-list oracle child expansion without replay promotion."""
    if not bool(row.get("base_source_has_body")):
        return False
    if not bool(row.get("oracle_source_has_body")):
        return False
    if bool(row.get("base_source_has_schedules")):
        return False
    if bool(row.get("oracle_source_has_schedules")):
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    if n_only_in_oracle <= 0 or n_only_in_replayed > 0:
        return False
    if n_only_in_oracle > _LOW_VOLUME_RESIDUAL_MAX_MISSES:
        return False
    if int(row.get("n_blocking_compile_rejections") or 0) > 0:
        return False
    samples = row.get("oracle_only_eid_samples") or ()
    if not isinstance(samples, list | tuple):
        return False
    return bool(samples) and all(
        _looks_like_nested_body_list_eid(str(eid)) for eid in samples
    )


def _looks_like_nested_body_list_eid(eid: str) -> bool:
    parts = [part for part in eid.split("-") if part]
    if len(parts) < 5:
        return False
    parent = parts[-2]
    child = parts[-1]
    roman = {"i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"}
    return len(parent) == 1 and parent.isalpha() and (child.isdigit() or child in roman)


def _is_body_oracle_collapsed_range_granularity_residual(row: dict[str, Any]) -> bool:
    """Classify current-oracle range nodes such as section-1320 without replay promotion."""
    if not bool(row.get("base_source_has_body")):
        return False
    if not bool(row.get("oracle_source_has_body")):
        return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    if n_only_in_oracle <= 0 or n_only_in_replayed > 0:
        return False
    if int(row.get("n_blocking_compile_rejections") or 0) > 0:
        return False
    samples = row.get("oracle_only_eid_samples") or ()
    if not isinstance(samples, list | tuple):
        return False
    return any(_looks_like_collapsed_section_range_eid(str(eid)) for eid in samples)


def _is_body_oracle_first_paragraph_sectionization_residual(row: dict[str, Any]) -> bool:
    """Classify oracle reification of an enacted unnumbered first body paragraph."""
    if not bool(row.get("base_source_has_body")):
        return False
    if not bool(row.get("oracle_source_has_body")):
        return False
    if bool(row.get("base_source_has_schedules")):
        return False
    if bool(row.get("oracle_source_has_schedules")):
        return False
    if _nonnegative_int(row.get("n_only_in_oracle")) != 1:
        return False
    if _nonnegative_int(row.get("n_only_in_replayed")) != 0:
        return False
    if _nonnegative_int(row.get("n_blocking_compile_rejections")) > 0:
        return False
    base_provisions = _positive_int_field(row.get("base_source_number_of_provisions"))
    oracle_provisions = _positive_int_field(row.get("oracle_source_number_of_provisions"))
    if base_provisions <= 0 or oracle_provisions != base_provisions + 1:
        return False
    samples = row.get("oracle_only_eid_samples") or ()
    if not isinstance(samples, list | tuple):
        return False
    normalized_samples = {str(eid).strip().lower() for eid in samples}
    return normalized_samples in ({"section-1"}, {"section-1."})


def _positive_int_field(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str) and value.isdecimal():
        parsed = int(value)
        return parsed if parsed > 0 else 0
    return 0


def _looks_like_collapsed_section_range_eid(eid: str) -> bool:
    match = re.fullmatch(r"section-(?P<label>\d{4,})\.?", eid.strip().lower())
    if match is None:
        return False
    label = match.group("label").rstrip(".")
    if len(label) % 2 != 0:
        return False
    midpoint = len(label) // 2
    start = int(label[:midpoint])
    end = int(label[midpoint:])
    return start > 0 and end > start


def _is_bounded_low_volume_residual(row: dict[str, Any]) -> bool:
    """Keep tiny residual miss sets visible without treating them as family bugs."""
    aligned = float(row.get("aligned_excluding_grounding_collateral") or row.get("aligned") or 0.0)
    if aligned < _LOW_VOLUME_RESIDUAL_MIN_SCORE:
        return False
    n_blocking_compile_rejections = int(row.get("n_blocking_compile_rejections") or 0)
    if n_blocking_compile_rejections >= _COMPILE_REJECTION_DOMINATED_MIN_REJECTIONS:
        return False
    n_misses = int(row.get("n_only_in_oracle") or 0) + int(row.get("n_only_in_replayed") or 0)
    return n_misses <= _LOW_VOLUME_RESIDUAL_MAX_MISSES


def _is_manual_compile_frontier_residual(row: dict[str, Any]) -> bool:
    """Classify residuals with explicit manual/source-frontier workqueue evidence."""
    aligned = float(row.get("aligned_excluding_grounding_collateral") or row.get("aligned") or 0.0)
    has_manual_source_chain_frontier = (
        _has_manual_frontier_source_insufficient_record(row)
        or _has_manual_frontier_source_chain_text_patch_gap(row)
    )
    has_replay_lens_frontier = (
        _has_replay_lens_or_source_insufficient_only_manual_frontier(row)
    )
    if aligned < _LOW_VOLUME_RESIDUAL_MIN_SCORE:
        status_counts = row.get("manual_frontier_status_counts") or {}
        if (
            not _has_actionable_manual_frontier_status(status_counts)
            and not has_manual_source_chain_frontier
            and not has_replay_lens_frontier
        ):
            return False
    n_only_in_oracle = int(row.get("n_only_in_oracle") or 0)
    n_only_in_replayed = int(row.get("n_only_in_replayed") or 0)
    if n_only_in_oracle < max(1, n_only_in_replayed):
        return False
    status_counts = row.get("manual_frontier_status_counts") or {}
    if (
        _has_actionable_manual_frontier_status(status_counts)
        or has_manual_source_chain_frontier
        or has_replay_lens_frontier
    ):
        return True
    blocking_counts = row.get("blocking_compile_rejection_rule_counts") or {}
    if not isinstance(blocking_counts, dict):
        return False
    return any(
        int(blocking_counts.get(rule_id) or 0) > 0
        for rule_id in _MANUAL_FRONTIER_BLOCKING_RULES
    )


def _has_actionable_manual_frontier_status(status_counts: Any) -> bool:
    if not isinstance(status_counts, dict):
        return False
    return any(
        int(status_counts.get(status) or 0) > 0
        for status in _MANUAL_FRONTIER_ACTIONABLE_STATUSES
    )


def _manual_frontier_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("manual_compile_status") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _manual_frontier_rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("manual_compile_rule_id") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _manual_frontier_authorization_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts = Counter(str(row.get("authorization_status") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _manual_frontier_authorization_status_owner_phase_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts = Counter(
        _phase_rule_key(row, str(row.get("authorization_status") or "unknown"))
        for row in rows
    )
    return dict(sorted(counts.items()))


def _manual_frontier_missing_proof_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        required_proofs = row.get("required_proofs") or ()
        if not isinstance(required_proofs, list | tuple):
            counts["invalid_required_proofs_shape"] += 1
            continue
        counts.update(str(proof or "unknown") for proof in required_proofs)
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_field_counts(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            continue
        value = str(work_item.get(field) or "")
        if value:
            counts[value] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_sequence_field_counts(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            continue
        values = work_item.get(field) or ()
        if not isinstance(values, list | tuple):
            counts[f"invalid_{field}_shape"] += 1
            continue
        counts.update(str(value) for value in values if str(value))
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_missing_field_count(
    rows: list[dict[str, Any]],
    field: str,
) -> int:
    missing = 0
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict) or not str(work_item.get(field) or ""):
            missing += 1
    return missing


def _manual_frontier_work_item_missing_sequence_field_count(
    rows: list[dict[str, Any]],
    field: str,
) -> int:
    missing = 0
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            missing += 1
            continue
        values = work_item.get(field) or ()
        if not isinstance(values, list | tuple) or not any(str(value) for value in values):
            missing += 1
    return missing


def _manual_frontier_work_item_packet_ready_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        packet = _manual_frontier_work_item_packet_completeness(row)
        if not packet:
            counts["missing_packet_completeness"] += 1
            continue
        key = (
            "ready"
            if packet.get("ready_for_manual_claim_validation") is True
            else "not_ready"
        )
        counts[key] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_packet_missing_field_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        packet = _manual_frontier_work_item_packet_completeness(row)
        if not packet:
            counts["packet_completeness"] += 1
            continue
        missing_fields = packet.get("missing_fields") or ()
        if not isinstance(missing_fields, list | tuple):
            counts["invalid_missing_fields_shape"] += 1
            continue
        counts.update(str(field) for field in missing_fields if str(field))
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_packet_validation_issue_counts(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        packet = _manual_frontier_work_item_packet_completeness(row)
        if not packet:
            continue
        issues = packet.get(field) or ()
        if not isinstance(issues, list | tuple):
            counts[f"invalid_{field}_shape"] += 1
            continue
        counts.update(str(issue) for issue in issues if str(issue))
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_packet_target_resolution_coverage_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        packet = _manual_frontier_work_item_packet_completeness(row)
        if not packet:
            counts["missing_packet_completeness"] += 1
            continue
        has_certificate = packet.get("has_target_resolution_coverage")
        if has_certificate is True:
            counts["present"] += 1
        elif has_certificate is False:
            counts["missing"] += 1
        else:
            counts["unproven"] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_target_resolution_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            counts["missing_work_item"] += 1
            continue
        detail = work_item.get("detail")
        if not isinstance(detail, dict):
            counts["missing_detail"] += 1
            continue
        certificate = detail.get("target_resolution_coverage")
        if not isinstance(certificate, dict):
            counts["missing_certificate"] += 1
            continue
        status = str(certificate.get("target_resolution_status") or "")
        counts[status or "unproven"] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_target_resolution_gap_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            counts["missing_work_item"] += 1
            continue
        status = _target_resolution_status_for_work_item(work_item)
        if status in {"resolved", "ambiguous"}:
            continue
        if _target_resolution_gap_exempt(work_item):
            continue
        counts[status] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_target_resolution_exempt_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            continue
        status = _target_resolution_status_for_work_item(work_item)
        if status in {"resolved", "ambiguous"}:
            continue
        if _target_resolution_gap_exempt(work_item):
            counts[_target_resolution_exempt_key(work_item, status)] += 1
    return dict(sorted(counts.items()))


def _target_resolution_status_for_work_item(work_item: Mapping[str, Any]) -> str:
    detail = work_item.get("detail")
    if not isinstance(detail, Mapping):
        return "missing_detail"
    certificate = detail.get("target_resolution_coverage")
    if not isinstance(certificate, Mapping):
        return "missing_certificate"
    return str(certificate.get("target_resolution_status") or "unproven")


def _target_resolution_gap_exempt(work_item: Mapping[str, Any]) -> bool:
    return (
        str(work_item.get("authorization_status") or "") == "out_of_scope"
        and str(work_item.get("candidate_operation_family") or "")
        == "non_textual_or_out_of_scope"
        and work_item.get("replay_authorized") is False
    )


def _target_resolution_exempt_key(
    work_item: Mapping[str, Any],
    status: str,
) -> str:
    family = str(work_item.get("frontier_family") or "unknown")
    return f"{status}:{family}"


def _aggregate_manual_frontier_work_item_packet_counts(
    results: list[dict[str, Any]],
) -> tuple[dict[str, int], dict[str, int], dict[str, int], dict[str, int], dict[str, int]]:
    ready_counts: Counter[str] = Counter()
    missing_field_counts: Counter[str] = Counter()
    target_resolution_counts: Counter[str] = Counter()
    execution_authorization_validation_issue_counts: Counter[str] = Counter()
    frontier_work_item_validation_issue_counts: Counter[str] = Counter()
    for row in results:
        row_ready_counts = Counter(
            _count_mapping(
                row.get("manual_frontier_work_item_packet_ready_counts"),
            )
        )
        row_missing_field_counts = Counter(
            _count_mapping(
                row.get("manual_frontier_work_item_packet_missing_field_counts"),
            )
        )
        row_target_resolution_counts = Counter(
            _count_mapping(
                row.get(
                    "manual_frontier_work_item_packet_target_resolution_coverage_counts"
                ),
            )
        )
        row_execution_authorization_validation_issue_counts = Counter(
            _count_mapping(
                row.get(
                    "manual_frontier_work_item_packet_execution_authorization_validation_issue_counts"
                ),
            )
        )
        row_frontier_work_item_validation_issue_counts = Counter(
            _count_mapping(
                row.get(
                    "manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts"
                ),
            )
        )
        if _row_has_unproven_target_resolution_packet_schema(
            row_ready_counts,
            row_target_resolution_counts,
        ):
            packet_count = int(row_ready_counts.get("ready", 0) or 0) + int(
                row_ready_counts.get("not_ready", 0) or 0
            )
            ready_count = int(row_ready_counts.get("ready", 0) or 0)
            if ready_count:
                row_ready_counts["ready"] -= ready_count
                if row_ready_counts["ready"] <= 0:
                    del row_ready_counts["ready"]
                row_ready_counts["not_ready"] += ready_count
            row_missing_field_counts["target_resolution_coverage"] += packet_count
            row_target_resolution_counts["unproven"] += packet_count
        ready_counts.update(row_ready_counts)
        missing_field_counts.update(row_missing_field_counts)
        target_resolution_counts.update(row_target_resolution_counts)
        execution_authorization_validation_issue_counts.update(
            row_execution_authorization_validation_issue_counts
        )
        frontier_work_item_validation_issue_counts.update(
            row_frontier_work_item_validation_issue_counts
        )
    return (
        dict(sorted(ready_counts.items())),
        dict(sorted(missing_field_counts.items())),
        dict(sorted(target_resolution_counts.items())),
        dict(sorted(execution_authorization_validation_issue_counts.items())),
        dict(sorted(frontier_work_item_validation_issue_counts.items())),
    )


def _aggregate_manual_frontier_work_item_target_resolution_status_counts(
    results: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in results:
        row_status_counts = Counter(
            _count_mapping(
                row.get("manual_frontier_work_item_target_resolution_status_counts"),
            )
        )
        if not row_status_counts:
            unproven_count = _target_resolution_packet_count_without_status(row)
            if unproven_count:
                row_status_counts["unproven"] += unproven_count
        counts.update(row_status_counts)
    return dict(sorted(counts.items()))


def _target_resolution_packet_count_without_status(row: Mapping[str, Any]) -> int:
    certificate_counts = _count_mapping(
        row.get("manual_frontier_work_item_packet_target_resolution_coverage_counts")
    )
    if certificate_counts:
        return sum(
            int(count or 0)
            for status, count in certificate_counts.items()
            if status != "missing_packet_completeness"
        )
    ready_counts = _count_mapping(
        row.get("manual_frontier_work_item_packet_ready_counts")
    )
    return int(ready_counts.get("ready", 0) or 0) + int(
        ready_counts.get("not_ready", 0) or 0
    )


def _count_mapping(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        return {}
    return {
        str(key): int(count or 0)
        for key, count in value.items()
        if str(key) and int(count or 0)
    }


def _row_has_unproven_target_resolution_packet_schema(
    ready_counts: Mapping[str, int],
    target_resolution_counts: Mapping[str, int],
) -> bool:
    packet_count = int(ready_counts.get("ready", 0) or 0) + int(
        ready_counts.get("not_ready", 0) or 0
    )
    if packet_count <= 0:
        return False
    return not target_resolution_counts


def _manual_frontier_work_item_candidate_set_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            continue
        detail = work_item.get("detail")
        if not isinstance(detail, dict):
            continue
        certificate = detail.get("candidate_set_coverage")
        if not isinstance(certificate, dict):
            continue
        status = str(certificate.get("completeness_status") or "")
        if status:
            counts[status] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_candidate_set_gap_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            counts["missing_work_item"] += 1
            continue
        status = _candidate_set_status_for_work_item(work_item)
        if status == "complete":
            continue
        if _target_resolution_gap_exempt(work_item):
            continue
        counts[status] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_candidate_set_exempt_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            continue
        status = _candidate_set_status_for_work_item(work_item)
        if status == "complete":
            continue
        if _target_resolution_gap_exempt(work_item):
            counts[_target_resolution_exempt_key(work_item, status)] += 1
    return dict(sorted(counts.items()))


def _candidate_set_status_for_work_item(work_item: Mapping[str, Any]) -> str:
    detail = work_item.get("detail")
    if not isinstance(detail, Mapping):
        return "missing_detail"
    certificate = detail.get("candidate_set_coverage")
    if not isinstance(certificate, Mapping):
        return "missing_certificate"
    return str(certificate.get("completeness_status") or "unproven")


def _manual_frontier_work_item_source_membership_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _source_membership_certificate_for_row(row)
        if not certificate:
            continue
        status = str(certificate.get("completeness_status") or "")
        counts[status or "unproven"] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_source_membership_blocker_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _source_membership_certificate_for_row(row)
        if not certificate:
            continue
        counts.update(_count_mapping(certificate.get("blocker_counts")))
    return dict(sorted(counts.items()))


def _source_membership_certificate_for_row(
    row: Mapping[str, Any],
) -> Mapping[str, Any]:
    work_item = row.get("frontier_work_item")
    if not isinstance(work_item, Mapping):
        return {}
    detail = work_item.get("detail")
    if not isinstance(detail, Mapping):
        return {}
    certificate = detail.get("source_membership_certificate")
    return certificate if isinstance(certificate, Mapping) else {}


def _manual_frontier_work_item_exclusion_scope_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _exclusion_scope_certificate_for_row(row)
        if not certificate:
            continue
        status = str(certificate.get("completeness_status") or "")
        counts[status or "unproven"] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_exclusion_scope_blocker_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _exclusion_scope_certificate_for_row(row)
        if not certificate:
            continue
        counts.update(_count_mapping(certificate.get("blocker_counts")))
    return dict(sorted(counts.items()))


def _exclusion_scope_certificate_for_row(
    row: Mapping[str, Any],
) -> Mapping[str, Any]:
    work_item = row.get("frontier_work_item")
    if not isinstance(work_item, Mapping):
        return {}
    detail = work_item.get("detail")
    if not isinstance(detail, Mapping):
        return {}
    certificate = detail.get("exclusion_scope_certificate")
    return certificate if isinstance(certificate, Mapping) else {}


def _manual_frontier_work_item_proof_obligation_status_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _proof_obligation_coverage_for_row(row)
        if not certificate:
            continue
        status = str(certificate.get("proof_status") or "")
        counts[status or "unproven"] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_proof_obligation_proved_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _proof_obligation_coverage_for_row(row)
        if not certificate:
            continue
        counts.update(str(proof) for proof in certificate.get("proved_proofs") or ())
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_proof_obligation_blocker_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        certificate = _proof_obligation_coverage_for_row(row)
        if not certificate:
            continue
        counts.update(_count_mapping(certificate.get("blocker_counts")))
    return dict(sorted(counts.items()))


def _proof_obligation_coverage_for_row(
    row: Mapping[str, Any],
) -> Mapping[str, Any]:
    work_item = row.get("frontier_work_item")
    if not isinstance(work_item, Mapping):
        return {}
    detail = work_item.get("detail")
    if not isinstance(detail, Mapping):
        return {}
    certificate = detail.get("proof_obligation_coverage")
    return certificate if isinstance(certificate, Mapping) else {}


def _manual_frontier_work_item_source_witness_role_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            counts["__missing_work_item__"] += 1
            continue
        source_witness = work_item.get("source_witness")
        if not isinstance(source_witness, dict):
            source_witness = {}
        counts[source_witness_role_key(source_witness)] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_source_witness_digest_coverage_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        work_item = row.get("frontier_work_item")
        if not isinstance(work_item, dict):
            counts["missing_work_item"] += 1
            continue
        source_witness = work_item.get("source_witness")
        if not isinstance(source_witness, dict):
            source_witness = {}
        counts[source_witness_digest_coverage(source_witness)] += 1
    return dict(sorted(counts.items()))


def _manual_frontier_work_item_packet_completeness(
    row: dict[str, Any],
) -> Mapping[str, Any]:
    work_item = row.get("frontier_work_item")
    if not isinstance(work_item, dict):
        return {}
    detail = work_item.get("detail")
    if not isinstance(detail, dict):
        return {}
    packet = detail.get("packet_completeness")
    return packet if isinstance(packet, dict) else {}


def _compile_authorization_rows(
    rows: list[dict[str, Any]],
    *,
    lane: str,
) -> list[dict[str, Any]]:
    authorized_rows: list[dict[str, Any]] = []
    for row in rows:
        owner_phase = uk_phase_owner_for_diagnostic(row)
        authorization = uk_execution_authorization_from_compile_record(
            record=row,
            lane=lane,
            owner_phase=owner_phase,
        ).to_dict()
        authorized_row = {
            **row,
            "owner_phase": owner_phase,
            "execution_authorization": authorization,
            "executable": authorization["executable"],
            "replay_authorized": authorization["replay_authorized"],
            "authorization_status": authorization["authorization_status"],
            "authorization_rule_id": authorization["authorization_rule_id"],
            "required_proofs": authorization["required_proofs"],
            "safe_default": authorization["safe_default"],
            "forbidden_shortcuts": authorization["forbidden_shortcuts"],
        }
        authorized_rows.append(authorized_row)
    return authorized_rows


def _authorization_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("authorization_status") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _authorization_missing_proof_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        if row.get("replay_authorized") is True:
            continue
        required_proofs = row.get("required_proofs") or ()
        if not isinstance(required_proofs, list | tuple):
            counts["invalid_required_proofs_shape"] += 1
            continue
        counts.update(str(proof or "unknown") for proof in required_proofs)
    return dict(sorted(counts.items()))


def _manual_frontier_rule_counts_for_status(
    rows: list[dict[str, Any]],
    status: str,
) -> dict[str, int]:
    counts = Counter(
        str(row.get("manual_compile_rule_id") or "unknown")
        for row in rows
        if str(row.get("manual_compile_status") or "") == status
    )
    return dict(sorted(counts.items()))


def _manual_frontier_rule_owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        _phase_rule_key(row, str(row.get("manual_compile_rule_id") or "unknown"))
        for row in rows
    )
    return dict(sorted(counts.items()))


def _manual_frontier_rule_owner_phase_counts_for_status(
    rows: list[dict[str, Any]],
    status: str,
) -> dict[str, int]:
    counts = Counter(
        _phase_rule_key(row, str(row.get("manual_compile_rule_id") or "unknown"))
        for row in rows
        if str(row.get("manual_compile_status") or "") == status
    )
    return dict(sorted(counts.items()))


def _manual_frontier_template_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(row.get("suggested_claim_template_status") or "none")
        for row in rows
    )
    return dict(sorted(counts.items()))


def _manual_frontier_template_gap_status_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(row.get("manual_compile_status") or "unknown")
        for row in rows
        if str(row.get("manual_compile_status") or "")
        in _MANUAL_FRONTIER_TEMPLATE_ACTIONABLE_STATUSES
        and str(row.get("suggested_claim_template_status") or "") == "not_available"
    )
    return dict(sorted(counts.items()))


def _manual_frontier_template_gap_rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        str(row.get("manual_compile_rule_id") or "unknown")
        for row in rows
        if str(row.get("manual_compile_status") or "")
        in _MANUAL_FRONTIER_TEMPLATE_ACTIONABLE_STATUSES
        and str(row.get("suggested_claim_template_status") or "") == "not_available"
    )
    return dict(sorted(counts.items()))


def _aggregate_row_count_maps(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        row_counts = row.get(field) or {}
        if not isinstance(row_counts, dict):
            continue
        counts.update({str(key): int(value or 0) for key, value in row_counts.items()})
    return dict(sorted(counts.items()))


def _source_frontier_source_witness_role_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for side in ("base", "oracle"):
            witness = row.get(f"{side}_source_witness")
            witness = witness if isinstance(witness, Mapping) else {}
            counts[f"{side}:{source_witness_role_key(witness)}"] += 1
    return dict(sorted(counts.items()))


def _source_frontier_source_witness_digest_coverage_counts(
    rows: list[dict[str, Any]],
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        for side in ("base", "oracle"):
            coverage = _source_frontier_source_witness_digest_coverage(row, side)
            counts[f"{side}:{coverage}"] += 1
    return dict(sorted(counts.items()))


def _source_frontier_source_witness_digest_coverage(
    row: Mapping[str, Any],
    side: str,
) -> str:
    witness = row.get(f"{side}_source_witness")
    witness = witness if isinstance(witness, Mapping) else {}
    coverage = source_witness_digest_coverage(witness)
    if coverage != "missing_source_witness":
        return coverage
    if f"{side}_source_status" in row or f"{side}_source_size" in row:
        return "stale_snapshot_missing_source_witness"
    return coverage


def _source_frontier_work_item_field_counts(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        work_item = row.get("source_frontier_work_item")
        if not isinstance(work_item, Mapping):
            counts["__missing_work_item__"] += 1
            continue
        value = str(work_item.get(field) or "")
        counts[value if value else f"__missing_{field}__"] += 1
    return dict(sorted(counts.items()))


def _source_frontier_work_item_sequence_field_counts(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for row in rows:
        work_item = row.get("source_frontier_work_item")
        if not isinstance(work_item, Mapping):
            counts["__missing_work_item__"] += 1
            continue
        values = work_item.get(field) or ()
        if not isinstance(values, list | tuple):
            counts[f"invalid_{field}_shape"] += 1
            continue
        counts.update(str(value) for value in values if str(value))
    return dict(sorted(counts.items()))


def _blocking_records(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from lawvm.core.compile_records import is_blocking_compile_record

    return [row for row in rows if is_blocking_compile_record(row)]


def _rule_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(str(row.get("rule_id") or "unknown") for row in rows)
    return dict(sorted(counts.items()))


def _phase_rule_key(row: dict[str, Any], rule_id: str) -> str:
    return f"{uk_phase_owner_for_diagnostic(row)}:{rule_id}"


def _rule_owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(
        _phase_rule_key(row, str(row.get("rule_id") or "unknown"))
        for row in rows
    )
    return dict(sorted(counts.items()))


def _owner_phase_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = Counter(uk_phase_owner_for_diagnostic(row) for row in rows)
    return dict(sorted(counts.items()))


def _source_state_fields(prefix: str, state: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        f"{prefix}_source_status": state.xml_content_status.value,
        f"{prefix}_source_number_of_provisions": state.number_of_provisions,
        f"{prefix}_source_has_body": state.has_body,
        f"{prefix}_source_has_schedules": state.has_schedules,
        f"{prefix}_source_size": state.size,
    }
    if state.parse_error:
        fields[f"{prefix}_source_parse_error"] = state.parse_error
    multiple_choice_candidates = getattr(state, "multiple_choice_candidates", ())
    if multiple_choice_candidates:
        fields[f"{prefix}_source_multiple_choice_candidates"] = [
            candidate.to_dict() for candidate in multiple_choice_candidates
        ]
    return fields


def _source_witness_fields(
    prefix: str,
    *,
    statute_id: str,
    locator: str,
    source_lane: str,
    data: bytes | None,
    source_status: str,
) -> dict[str, Any]:
    digest = None
    preview_digest = None
    preview = ""
    if data:
        digest = DigestWitness(
            digest_algorithm="sha256",
            digest=hashlib.sha256(data).hexdigest(),
        )
        preview = _bounded_source_preview(data)
        if preview:
            preview_digest = DigestWitness(
                digest_algorithm="sha256",
                digest=hashlib.sha256(preview.encode("utf-8")).hexdigest(),
            )
    witness = SourceWitness(
        source_role=f"uk_broad_{prefix}_source",
        artifact_id=statute_id,
        source_unit_id=source_lane,
        locator=locator,
        digest=digest,
        bounded_preview=preview,
        preview_digest=preview_digest,
        source_lane=source_lane,
        metadata={
            "source_status": source_status,
            "source_size": len(data or b""),
            "truth_claim": "source_footing_witness_not_replay_authorization",
        },
    ).to_dict()
    return {
        f"{prefix}_source_locator": locator,
        f"{prefix}_source_witness": witness,
        f"{prefix}_source_witness_digest_coverage": (
            source_witness_digest_coverage(witness)
        ),
    }


def _bounded_source_preview(data: bytes, *, limit: int = 500) -> str:
    text = data[:4096].decode("utf-8", errors="replace")
    return " ".join(text.split())[:limit]


def _is_sampleable_uk_statute_id(statute_id: str) -> bool:
    parts = statute_id.split("/")
    if len(parts) == 3:
        return parts[1].isdigit()
    if len(parts) == 4:
        return not parts[1].isdigit() and ":" not in parts[1] and ":" not in parts[2]
    return False


def sample_statutes(n: int, seed: int, classes: Optional[list[str]]) -> list[str]:
    """Sample n statute IDs that have BOTH enacted and current XML in the archive."""
    from farchive import Farchive
    from lawvm.uk_legislation.source_state import (
        UKSourceStatus,
        classify_uk_source_blob,
    )

    archive = Farchive(DB_PATH, readonly=True)
    try:
        enacted = set()
        current = set()
        suffix_enacted = "/enacted/data.xml"
        suffix_current = "/data.xml"
        for loc in archive.locators(f"{_LEG_BASE}/%/enacted/data.xml"):
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_enacted)]
            if _is_sampleable_uk_statute_id(sid) and (
                classify_uk_source_blob(archive.get(loc)).source_state_status
                is UKSourceStatus.AVAILABLE
            ):
                enacted.add(sid)
        for loc in archive.locators(f"{_LEG_BASE}/%/data.xml"):
            if loc.endswith(suffix_enacted):
                continue
            sid = loc[len(_LEG_BASE) + 1 : -len(suffix_current)]
            if (
                _is_sampleable_uk_statute_id(sid)
                and "/changes/" not in loc
                and "/affecting/" not in loc
                and classify_uk_source_blob(archive.get(loc)).source_state_status
                is UKSourceStatus.AVAILABLE
            ):
                current.add(sid)
    finally:
        archive.close()

    both = sorted(enacted & current)
    if classes:
        both = [s for s in both if s.split("/", 1)[0] in classes]
    rng = random.Random(seed)
    rng.shuffle(both)
    return both[:n]


def _load_snapshot_results(snapshot_path: Path) -> list[dict[str, Any]]:
    raw = json.loads(snapshot_path.read_text())
    if isinstance(raw, Mapping):
        report_rows = raw.get("rows")
        if isinstance(report_rows, list):
            return _load_snapshot_row_list(report_rows)
        results: list[dict[str, Any]] = []
        for statute_id, row in raw.items():
            if not isinstance(row, Mapping):
                raise ValueError(
                    f"snapshot row for {statute_id!r} must be an object"
                )
            payload = dict(row)
            payload.setdefault("statute_id", str(statute_id))
            results.append(payload)
        return results
    if isinstance(raw, list):
        return _load_snapshot_row_list(raw)
    raise ValueError("snapshot must be a statute-id object map or row list")


def _load_snapshot_row_list(rows: list[Any]) -> list[dict[str, Any]]:
    results = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            raise ValueError(f"snapshot row {index} must be an object")
        payload = dict(row)
        if not payload.get("statute_id"):
            raise ValueError(f"snapshot row {index} is missing statute_id")
        results.append(payload)
    return results


def _load_ids_file(path: Path) -> list[str]:
    """Load statute IDs from a newline file or a CSV with a statute_id column."""
    text = path.read_text(encoding="utf-8-sig")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    first = lines[0]
    if "," in first and "statute_id" in {part.strip() for part in first.split(",")}:
        import csv
        import io

        rows = csv.DictReader(io.StringIO(text))
        ids = []
        for index, row in enumerate(rows, start=2):
            statute_id = str(row.get("statute_id") or "").strip()
            if not statute_id:
                raise ValueError(f"{path}: row {index} is missing statute_id")
            ids.append(statute_id)
        return ids
    return [line.split("#", 1)[0].strip() for line in lines if line.split("#", 1)[0].strip()]


def _hard_gate_exit_code(
    summary: dict[str, Any],
    *,
    fail_on_completion_gaps: bool = False,
    fail_on_active_unclassified_residuals: bool = False,
    fail_on_manual_frontier_template_gaps: bool = False,
    fail_on_frontier_work_item_gaps: bool = False,
    fail_on_deterministic_frontend_candidates: bool = False,
    fail_on_non_manual_source_chain_frontier: bool = False,
    fail_on_mutation_boundary_unexplained: bool = False,
    fail_on_frontier_work_item_packet_gaps: bool = False,
    fail_on_frontier_candidate_set_gaps: bool = False,
    fail_on_frontier_target_resolution_gaps: bool = False,
    fail_on_frontier_source_witness_gaps: bool = False,
    fail_on_source_frontier_work_item_gaps: bool = False,
) -> int:
    if fail_on_completion_gaps and _completion_gate_failure_counts(summary):
        return 1
    if (
        fail_on_active_unclassified_residuals
        and summary["active_unclassified_residual_count"]
    ):
        return 1
    if (
        fail_on_manual_frontier_template_gaps
        and summary["manual_frontier_template_gap_rule_counts"]
    ):
        return 1
    if fail_on_frontier_work_item_gaps and (
        summary["manual_frontier_work_item_missing_candidate_operation_family_count"]
        or summary["manual_frontier_work_item_missing_required_validator_checks_count"]
    ):
        return 1
    if (
        fail_on_deterministic_frontend_candidates
        and summary["deterministic_frontend_candidate_count"]
    ):
        return 1
    if (
        fail_on_non_manual_source_chain_frontier
        and summary["non_manual_source_chain_frontier_count"]
    ):
        return 1
    if fail_on_mutation_boundary_unexplained and (
        summary["mutation_boundary_unexplained_report_count"]
        or summary["mutation_boundary_unexplained_path_count"]
    ):
        return 1
    if fail_on_frontier_work_item_packet_gaps and _frontier_work_item_packet_gap_count(
        summary
    ):
        return 1
    if fail_on_frontier_candidate_set_gaps and _frontier_candidate_set_gap_count(
        summary
    ):
        return 1
    if (
        fail_on_frontier_target_resolution_gaps
        and _frontier_target_resolution_gap_count(summary)
    ):
        return 1
    if fail_on_frontier_source_witness_gaps and _frontier_source_witness_gap_count(
        summary
    ):
        return 1
    if (
        fail_on_source_frontier_work_item_gaps
        and _source_frontier_work_item_gap_count(summary)
    ):
        return 1
    return 0


def _completion_gate_failure_counts(summary: Mapping[str, Any]) -> dict[str, int]:
    counts = {
        "errors": len(summary.get("errored") or ()),
        "active_unclassified_residuals": int(
            summary.get("active_unclassified_residual_count") or 0
        ),
        "manual_frontier_template_gaps": _count_mapping_values(
            summary.get("manual_frontier_template_gap_rule_counts")
        ),
        "frontier_work_item_missing_candidate_operation_family": int(
            summary.get(
                "manual_frontier_work_item_missing_candidate_operation_family_count"
            )
            or 0
        ),
        "frontier_work_item_missing_required_validator_checks": int(
            summary.get(
                "manual_frontier_work_item_missing_required_validator_checks_count"
            )
            or 0
        ),
        "deterministic_frontend_candidates": int(
            summary.get("deterministic_frontend_candidate_count") or 0
        ),
        "non_manual_source_chain_frontier": int(
            summary.get("non_manual_source_chain_frontier_count") or 0
        ),
        "mutation_boundary_unexplained_reports": int(
            summary.get("mutation_boundary_unexplained_report_count") or 0
        ),
        "mutation_boundary_unexplained_paths": int(
            summary.get("mutation_boundary_unexplained_path_count") or 0
        ),
        "frontier_work_item_packet_gaps": _frontier_work_item_packet_gap_count(
            summary
        ),
        "frontier_candidate_set_gaps": _frontier_candidate_set_gap_count(summary),
        "frontier_target_resolution_gaps": _frontier_target_resolution_gap_count(
            summary
        ),
        "frontier_source_witness_gaps": _frontier_source_witness_gap_count(summary),
        "source_frontier_work_item_gaps": _source_frontier_work_item_gap_count(
            summary
        ),
    }
    return {key: count for key, count in counts.items() if count}


def _count_mapping_values(value: Any) -> int:
    if not isinstance(value, Mapping):
        return 0
    return sum(int(count or 0) for count in value.values())


def _frontier_work_item_packet_gap_count(summary: Mapping[str, Any]) -> int:
    ready_counts = summary.get("manual_frontier_work_item_packet_ready_counts")
    missing_counts = summary.get(
        "manual_frontier_work_item_packet_missing_field_counts"
    )
    ready_counts = ready_counts if isinstance(ready_counts, Mapping) else {}
    missing_counts = missing_counts if isinstance(missing_counts, Mapping) else {}
    return (
        int(ready_counts.get("not_ready", 0) or 0)
        + int(ready_counts.get("missing_packet_completeness", 0) or 0)
        + sum(int(count or 0) for count in missing_counts.values())
    )


def _frontier_candidate_set_gap_count(summary: Mapping[str, Any]) -> int:
    gap_counts = summary.get("manual_frontier_work_item_candidate_set_gap_counts")
    exempt_counts = summary.get("manual_frontier_work_item_candidate_set_exempt_counts")
    if isinstance(gap_counts, Mapping) and (gap_counts or exempt_counts):
        return sum(int(count or 0) for count in gap_counts.values())
    status_counts = summary.get("manual_frontier_work_item_candidate_set_status_counts")
    status_counts = status_counts if isinstance(status_counts, Mapping) else {}
    return sum(
        int(count or 0)
        for status, count in status_counts.items()
        if str(status) != "complete"
    )


def _frontier_target_resolution_gap_count(summary: Mapping[str, Any]) -> int:
    gap_counts = summary.get("manual_frontier_work_item_target_resolution_gap_counts")
    exempt_counts = summary.get(
        "manual_frontier_work_item_target_resolution_exempt_counts"
    )
    if isinstance(gap_counts, Mapping) and (gap_counts or exempt_counts):
        return sum(int(count or 0) for count in gap_counts.values())
    status_counts = summary.get(
        "manual_frontier_work_item_target_resolution_status_counts"
    )
    status_counts = status_counts if isinstance(status_counts, Mapping) else {}
    accepted = {"resolved", "ambiguous"}
    return sum(
        int(count or 0)
        for status, count in status_counts.items()
        if str(status) not in accepted
    )


def _frontier_source_witness_gap_count(summary: Mapping[str, Any]) -> int:
    role_counts = summary.get("manual_frontier_work_item_source_witness_role_counts")
    digest_counts = summary.get(
        "manual_frontier_work_item_source_witness_digest_coverage_counts"
    )
    role_counts = role_counts if isinstance(role_counts, Mapping) else {}
    digest_counts = digest_counts if isinstance(digest_counts, Mapping) else {}
    return (
        int(role_counts.get("__missing__", 0) or 0)
        + int(role_counts.get("__missing_work_item__", 0) or 0)
        + int(digest_counts.get("missing_source_witness", 0) or 0)
        + int(digest_counts.get("missing_work_item", 0) or 0)
        + int(digest_counts.get("missing_digest", 0) or 0)
    )


def _source_frontier_work_item_gap_count(summary: Mapping[str, Any]) -> int:
    family_counts = summary.get("source_frontier_work_item_family_counts")
    status_counts = summary.get("source_frontier_work_item_authorization_status_counts")
    proof_counts = summary.get("source_frontier_work_item_missing_proof_counts")
    digest_counts = summary.get(
        "source_frontier_source_witness_digest_coverage_counts"
    )
    family_counts = family_counts if isinstance(family_counts, Mapping) else {}
    status_counts = status_counts if isinstance(status_counts, Mapping) else {}
    proof_counts = proof_counts if isinstance(proof_counts, Mapping) else {}
    digest_counts = digest_counts if isinstance(digest_counts, Mapping) else {}
    return (
        int(family_counts.get("__missing_work_item__", 0) or 0)
        + int(status_counts.get("__missing_work_item__", 0) or 0)
        + int(proof_counts.get("__missing_work_item__", 0) or 0)
        + sum(
            int(count or 0)
            for coverage, count in digest_counts.items()
            if str(coverage).endswith(":missing_source_witness")
            or str(coverage).endswith(":stale_snapshot_missing_source_witness")
            or str(coverage).endswith(":missing_digest")
        )
    )


def run_report_from_snapshot(
    snapshot_path: Path,
    out_report: Path,
    *,
    fail_on_completion_gaps: bool = False,
    fail_on_active_unclassified_residuals: bool = False,
    fail_on_manual_frontier_template_gaps: bool = False,
    fail_on_frontier_work_item_gaps: bool = False,
    fail_on_deterministic_frontend_candidates: bool = False,
    fail_on_non_manual_source_chain_frontier: bool = False,
    fail_on_mutation_boundary_unexplained: bool = False,
    fail_on_frontier_work_item_packet_gaps: bool = False,
    fail_on_frontier_candidate_set_gaps: bool = False,
    fail_on_frontier_target_resolution_gaps: bool = False,
    fail_on_frontier_source_witness_gaps: bool = False,
    fail_on_source_frontier_work_item_gaps: bool = False,
) -> int:
    """Regenerate the typed report envelope from a saved raw snapshot.

    This is intentionally report-only: it does not rescore statutes, read the
    archive, or promote agreement evidence into replay authority.  It exists so
    report-layer classifier fixes can refresh stale EvidenceSurfaceReport files
    without rerunning a broad UK replay sweep.
    """
    results = _load_snapshot_results(snapshot_path)
    results = [_annotate_row_work_selection(dict(row)) for row in results]
    ids = [str(row.get("statute_id") or "") for row in results]
    report = uk_broad_baseline_report_jsonable(
        results,
        ids=ids,
        snapshot_path=snapshot_path,
    )
    out_report.parent.mkdir(parents=True, exist_ok=True)
    out_report.write_text(json.dumps(report, indent=2, sort_keys=True))
    summary = report["summary"]
    print(
        f"Wrote broad-baseline evidence report -> {out_report} "
        f"(from snapshot {snapshot_path}; scored={summary['scored_count']} "
        f"source_frontier={summary['source_frontier_count']} "
        f"active_unclassified={summary['active_unclassified_residual_count']} "
        f"non_manual_source_chain={summary['non_manual_source_chain_frontier_count']})",
        flush=True,
    )
    return _hard_gate_exit_code(
        summary,
        fail_on_completion_gaps=fail_on_completion_gaps,
        fail_on_active_unclassified_residuals=fail_on_active_unclassified_residuals,
        fail_on_manual_frontier_template_gaps=fail_on_manual_frontier_template_gaps,
        fail_on_frontier_work_item_gaps=fail_on_frontier_work_item_gaps,
        fail_on_deterministic_frontend_candidates=(
            fail_on_deterministic_frontend_candidates
        ),
        fail_on_non_manual_source_chain_frontier=(
            fail_on_non_manual_source_chain_frontier
        ),
        fail_on_mutation_boundary_unexplained=(
            fail_on_mutation_boundary_unexplained
        ),
        fail_on_frontier_work_item_packet_gaps=(
            fail_on_frontier_work_item_packet_gaps
        ),
        fail_on_frontier_candidate_set_gaps=fail_on_frontier_candidate_set_gaps,
        fail_on_frontier_target_resolution_gaps=(
            fail_on_frontier_target_resolution_gaps
        ),
        fail_on_frontier_source_witness_gaps=fail_on_frontier_source_witness_gaps,
        fail_on_source_frontier_work_item_gaps=(
            fail_on_source_frontier_work_item_gaps
        ),
    )


def run_driver(
    ids: list[str],
    out: Optional[Path],
    out_report: Optional[Path] = None,
    *,
    parallel: int = 1,
    fail_on_completion_gaps: bool = False,
    fail_on_active_unclassified_residuals: bool = False,
    fail_on_manual_frontier_template_gaps: bool = False,
    fail_on_frontier_work_item_gaps: bool = False,
    fail_on_deterministic_frontend_candidates: bool = False,
    fail_on_non_manual_source_chain_frontier: bool = False,
    fail_on_mutation_boundary_unexplained: bool = False,
    fail_on_frontier_work_item_packet_gaps: bool = False,
    fail_on_frontier_candidate_set_gaps: bool = False,
    fail_on_frontier_target_resolution_gaps: bool = False,
    fail_on_frontier_source_witness_gaps: bool = False,
    fail_on_source_frontier_work_item_gaps: bool = False,
) -> int:
    workers = max(1, int(parallel or 1))
    indexed_results: list[tuple[int, dict[str, Any]]] = []

    def score_child(index: int, statute_id: str) -> tuple[int, str, dict[str, Any]]:
        proc = subprocess.run(
            [sys.executable, __file__, "--one", statute_id],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        row: dict[str, Any]
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            row = json.loads(line)
        except (json.JSONDecodeError, IndexError):
            row = {
                "statute_id": statute_id,
                "error": f"subprocess_exit_{proc.returncode}",
            }
            if proc.stderr.strip():
                row["stderr_tail"] = proc.stderr.strip().splitlines()[-1][:200]
        _annotate_row_work_selection(row)
        return index, statute_id, row

    def print_row(done: int, total: int, statute_id: str, row: dict[str, Any]) -> None:
        if "error" in row:
            print(f"[{done}/{total}] {statute_id:24s} ERROR {row['error']}", flush=True)
        elif row.get("score_status") == "source_frontier":
            reason = str(row.get("source_frontier_reason") or "unknown")
            print(f"[{done}/{total}] {statute_id:24s} SOURCE-FRONTIER {reason}", flush=True)
        else:
            base_status = str(row.get("base_source_status") or "unknown")
            base_suffix = "" if base_status == "available" else f" base={base_status}"
            zero_oracle_suffix = (
                " zero_oracle_retention"
                if int(row.get("n_oracle") or 0) == 0
                and int(row.get("n_replay") or 0) > 0
                else ""
            )
            print(
                f"[{done}/{total}] {statute_id:24s} aligned={row['aligned']:5.1f}% "
                f"aligned_no_gc={row.get('aligned_excluding_grounding_collateral', row['aligned']):5.1f}% "
                f"unaligned={row['unaligned']:5.1f}% "
                f"gc={row.get('n_grounding_collateral', 0)} "
                f"(replay={row.get('n_replay')} oracle={row.get('n_oracle')})"
                f"{base_suffix}{zero_oracle_suffix}",
                flush=True,
            )

    if workers == 1:
        for i, sid in enumerate(ids, 1):
            index, statute_id, row = score_child(i - 1, sid)
            indexed_results.append((index, row))
            print_row(i, len(ids), statute_id, row)
    else:
        print(f"Scoring {len(ids)} statutes with parallel={workers}", flush=True)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(score_child, i, sid)
                for i, sid in enumerate(ids)
            ]
            for done, future in enumerate(as_completed(futures), 1):
                index, statute_id, row = future.result()
                indexed_results.append((index, row))
                print_row(done, len(ids), statute_id, row)

    results = [row for _, row in sorted(indexed_results, key=lambda item: item[0])]

    snapshot = {r["statute_id"]: r for r in results}
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snapshot, indent=2, sort_keys=True))
        print(f"\nWrote {len(snapshot)} rows -> {out}")
    if out_report:
        report = uk_broad_baseline_report_jsonable(
            results,
            ids=list(ids),
            snapshot_path=out,
        )
        out_report.parent.mkdir(parents=True, exist_ok=True)
        out_report.write_text(json.dumps(report, indent=2, sort_keys=True))
        print(f"Wrote broad-baseline evidence report -> {out_report}")

    summary = summarize_results(results)
    completion_gate_failure_counts = _completion_gate_failure_counts(summary)
    scored = summary["scored"]
    errored = summary["errored"]
    source_frontier = summary["source_frontier"]
    if scored:
        avg = sum(r["aligned"] for r in scored) / len(scored)
        avg_no_gc = sum(
            r.get("aligned_excluding_grounding_collateral", r["aligned"])
            for r in scored
        ) / len(scored)
        gc_total = sum(r.get("n_grounding_collateral", 0) for r in scored)
        metadata_only_base_total = sum(
            1 for r in scored if r.get("base_source_status") == "metadata_only"
        )
        print(
            f"\nScored {len(scored)} / {len(results)}  "
            f"mean aligned={avg:.2f}%  mean aligned_no_gc={avg_no_gc:.2f}%  "
            f"grounding_collateral={gc_total}  "
            f"metadata_only_base={metadata_only_base_total}  errors={len(errored)}"
            f"  source_frontier={len(source_frontier)}"
        )
        core_avg = summary["comparison_core_mean_aligned"]
        core_avg_no_gc = summary[
            "comparison_core_mean_aligned_excluding_grounding_collateral"
        ]
        if core_avg is not None and core_avg_no_gc is not None:
            print(
                "  comparison_core="
                f"{summary['comparison_core_count']} rows  "
                f"mean aligned={core_avg:.2f}%  "
                f"mean aligned_no_gc={core_avg_no_gc:.2f}%  "
                f"non_core={summary['comparison_non_core_count']}"
            )
        else:
            print(
                "  comparison_core="
                f"{summary['comparison_core_count']} rows  "
                f"non_core={summary['comparison_non_core_count']}"
            )
        if summary["zero_oracle_retention_count"]:
            print(
                "  zero_oracle_retention="
                f"{summary['zero_oracle_retention_count']} rows / "
                f"{summary['zero_oracle_retention_eids']} replay eIds"
            )
            if summary["zero_oracle_retention_reasons"]:
                reasons = ", ".join(
                    f"{reason}={count}"
                    for reason, count in summary[
                        "zero_oracle_retention_reasons"
                    ].items()
                )
                print(f"  zero_oracle_retention_reasons: {reasons}")
    else:
        print(
            f"\nScored 0 / {len(results)}  source_frontier={len(source_frontier)}  "
            f"errors={len(errored)}"
        )
    if summary["source_frontier_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in summary["source_frontier_reasons"].items()
        )
        print(f"  source_frontier_reasons: {reasons}")
    if summary["source_frontier_source_witness_role_counts"]:
        counts = ", ".join(
            f"{role}={count}"
            for role, count in summary[
                "source_frontier_source_witness_role_counts"
            ].items()
        )
        print(f"  source_frontier_source_witness_role_counts: {counts}")
    if summary["source_frontier_source_witness_digest_coverage_counts"]:
        counts = ", ".join(
            f"{coverage}={count}"
            for coverage, count in summary[
                "source_frontier_source_witness_digest_coverage_counts"
            ].items()
        )
        print(f"  source_frontier_source_witness_digest_coverage_counts: {counts}")
    if summary["source_frontier_work_item_family_counts"]:
        counts = ", ".join(
            f"{family}={count}"
            for family, count in summary[
                "source_frontier_work_item_family_counts"
            ].items()
        )
        print(f"  source_frontier_work_item_family_counts: {counts}")
    if summary["source_frontier_work_item_authorization_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "source_frontier_work_item_authorization_status_counts"
            ].items()
        )
        print(f"  source_frontier_work_item_authorization_status_counts: {counts}")
    if summary["source_frontier_work_item_missing_proof_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "source_frontier_work_item_missing_proof_counts"
            ].items()
        )
        print(f"  source_frontier_work_item_missing_proof_counts: {counts}")
    if summary["source_chain_frontier_reasons"]:
        reasons = ", ".join(
            f"{reason}={count}"
            for reason, count in summary["source_chain_frontier_reasons"].items()
        )
        print(f"  source_chain_frontier_reasons: {reasons}")
    if summary["source_chain_frontier_statutes"]:
        for reason, statute_ids in summary["source_chain_frontier_statutes"].items():
            print(f"  source_chain_frontier[{reason}]: {', '.join(statute_ids)}")
    if summary["non_manual_source_chain_frontier_count"]:
        print(
            "  non_manual_source_chain_frontier="
            f"{summary['non_manual_source_chain_frontier_count']}: "
            f"{', '.join(summary['non_manual_source_chain_frontier_statutes'])}"
        )
    else:
        print("  non_manual_source_chain_frontier=0")
    if summary["replay_lens_frontier_count"]:
        print(
            "  replay_lens_frontier="
            f"{summary['replay_lens_frontier_count']}: "
            f"{', '.join(summary['replay_lens_frontier_statutes'])}"
        )
    if summary["empty_effect_feed_frontier_count"]:
        print(
            "  empty_effect_feed_frontier="
            f"{summary['empty_effect_feed_frontier_count']}: "
            f"{', '.join(summary['empty_effect_feed_frontier_statutes'])}"
        )
    if summary["source_or_oracle_pathology_frontier_count"]:
        print(
            "  source_or_oracle_pathology_frontier="
            f"{summary['source_or_oracle_pathology_frontier_count']}: "
            f"{', '.join(summary['source_or_oracle_pathology_frontier_statutes'])}"
        )
        if summary["source_or_oracle_pathology_frontier_reasons"]:
            reasons = ", ".join(
                f"{reason}={count}"
                for reason, count in summary[
                    "source_or_oracle_pathology_frontier_reasons"
                ].items()
            )
            print(f"  source_or_oracle_pathology_frontier_reasons: {reasons}")
        for reason, statute_ids in summary[
            "source_or_oracle_pathology_frontier_reason_statutes"
        ].items():
            print(
                "  source_or_oracle_pathology_frontier"
                f"[{reason}]: {', '.join(statute_ids)}"
            )
    if summary["triage_buckets"]:
        buckets = ", ".join(
            f"{bucket}={count}"
            for bucket, count in summary["triage_buckets"].items()
        )
        print(f"  triage_buckets: {buckets}")
    if summary["triage_bucket_statutes"]:
        for bucket, statute_ids in summary["triage_bucket_statutes"].items():
            if bucket == "high_fidelity_after_grounding":
                continue
            print(f"  triage_bucket[{bucket}]: {', '.join(statute_ids)}")
    if summary["manual_frontier_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary["manual_frontier_status_counts"].items()
        )
        print(f"  manual_frontier_status_counts: {counts}")
    if summary["manual_frontier_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase}={count}"
            for phase, count in summary["manual_frontier_owner_phase_counts"].items()
        )
        print(f"  manual_frontier_owner_phase_counts: {counts}")
    if summary["manual_frontier_authorization_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_authorization_status_counts"
            ].items()
        )
        print(f"  manual_frontier_authorization_status_counts: {counts}")
    if summary["manual_frontier_authorization_status_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_status}={count}"
            for phase_status, count in summary[
                "manual_frontier_authorization_status_owner_phase_counts"
            ].items()
        )
        print(
            "  manual_frontier_authorization_status_owner_phase_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_missing_proof_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "manual_frontier_missing_proof_counts"
            ].items()
        )
        print(f"  manual_frontier_missing_proof_counts: {counts}")
    if summary["manual_frontier_work_item_family_counts"]:
        counts = ", ".join(
            f"{family}={count}"
            for family, count in summary[
                "manual_frontier_work_item_family_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_family_counts: {counts}")
    if summary["manual_frontier_work_item_authorization_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_authorization_status_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_authorization_status_counts: {counts}")
    if summary["manual_frontier_work_item_candidate_operation_family_counts"]:
        counts = ", ".join(
            f"{family}={count}"
            for family, count in summary[
                "manual_frontier_work_item_candidate_operation_family_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_candidate_operation_family_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_required_validator_check_counts"]:
        counts = ", ".join(
            f"{check}={count}"
            for check, count in summary[
                "manual_frontier_work_item_required_validator_check_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_required_validator_check_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_packet_ready_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_packet_ready_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_packet_ready_counts: {counts}")
    if summary["manual_frontier_work_item_packet_missing_field_counts"]:
        counts = ", ".join(
            f"{field}={count}"
            for field, count in summary[
                "manual_frontier_work_item_packet_missing_field_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_packet_missing_field_counts: "
            f"{counts}"
        )
    if summary[
        "manual_frontier_work_item_packet_execution_authorization_validation_issue_counts"
    ]:
        counts = ", ".join(
            f"{issue}={count}"
            for issue, count in summary[
                "manual_frontier_work_item_packet_execution_authorization_validation_issue_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_packet_execution_authorization_validation_issue_counts: "
            f"{counts}"
        )
    if summary[
        "manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts"
    ]:
        counts = ", ".join(
            f"{issue}={count}"
            for issue, count in summary[
                "manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_packet_frontier_work_item_validation_issue_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_packet_target_resolution_coverage_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_packet_target_resolution_coverage_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_packet_target_resolution_coverage_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_target_resolution_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_target_resolution_status_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_target_resolution_status_counts: {counts}")
    if summary["manual_frontier_work_item_target_resolution_gap_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_target_resolution_gap_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_target_resolution_gap_counts: {counts}")
    if summary["manual_frontier_work_item_target_resolution_exempt_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_target_resolution_exempt_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_target_resolution_exempt_counts: {counts}")
    if summary["manual_frontier_work_item_candidate_set_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_candidate_set_status_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_candidate_set_status_counts: {counts}")
    if summary["manual_frontier_work_item_candidate_set_gap_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_candidate_set_gap_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_candidate_set_gap_counts: {counts}")
    if summary["manual_frontier_work_item_candidate_set_exempt_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_candidate_set_exempt_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_candidate_set_exempt_counts: {counts}")
    if summary["manual_frontier_work_item_source_membership_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_source_membership_status_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_source_membership_status_counts: {counts}")
    if summary["manual_frontier_work_item_source_membership_blocker_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_source_membership_blocker_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_source_membership_blocker_counts: {counts}")
    if summary["manual_frontier_work_item_exclusion_scope_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_exclusion_scope_status_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_exclusion_scope_status_counts: {counts}")
    if summary["manual_frontier_work_item_exclusion_scope_blocker_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_exclusion_scope_blocker_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_exclusion_scope_blocker_counts: {counts}")
    if summary["manual_frontier_work_item_proof_obligation_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_work_item_proof_obligation_status_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_proof_obligation_status_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_proof_obligation_proved_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "manual_frontier_work_item_proof_obligation_proved_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_proof_obligation_proved_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_proof_obligation_blocker_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "manual_frontier_work_item_proof_obligation_blocker_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_proof_obligation_blocker_counts: "
            f"{counts}"
        )
    if summary["manual_frontier_work_item_source_witness_role_counts"]:
        counts = ", ".join(
            f"{role}={count}"
            for role, count in summary[
                "manual_frontier_work_item_source_witness_role_counts"
            ].items()
        )
        print(f"  manual_frontier_work_item_source_witness_role_counts: {counts}")
    if summary["manual_frontier_work_item_source_witness_digest_coverage_counts"]:
        counts = ", ".join(
            f"{coverage}={count}"
            for coverage, count in summary[
                "manual_frontier_work_item_source_witness_digest_coverage_counts"
            ].items()
        )
        print(
            "  manual_frontier_work_item_source_witness_digest_coverage_counts: "
            f"{counts}"
        )
    missing_family_count = int(
        summary.get("manual_frontier_work_item_missing_candidate_operation_family_count")
        or 0
    )
    missing_check_count = int(
        summary.get("manual_frontier_work_item_missing_required_validator_checks_count")
        or 0
    )
    if missing_family_count or missing_check_count:
        print(
            "  manual_frontier_work_item_missing_completeness_counts: "
            f"candidate_operation_family={missing_family_count}, "
            f"required_validator_checks={missing_check_count}"
        )
    if summary["manual_frontier_rule_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_rule}={count}"
            for phase_rule, count in summary[
                "manual_frontier_rule_owner_phase_counts"
            ].items()
        )
        print(f"  manual_frontier_rule_owner_phase_counts: {counts}")
    if summary["compile_rejection_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase}={count}"
            for phase, count in summary["compile_rejection_owner_phase_counts"].items()
        )
        print(f"  compile_rejection_owner_phase_counts: {counts}")
    if summary["compile_rejection_authorization_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "compile_rejection_authorization_status_counts"
            ].items()
        )
        print(f"  compile_rejection_authorization_status_counts: {counts}")
    if summary["compile_rejection_missing_proof_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "compile_rejection_missing_proof_counts"
            ].items()
        )
        print(f"  compile_rejection_missing_proof_counts: {counts}")
    if summary["compile_rejection_rule_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_rule}={count}"
            for phase_rule, count in summary[
                "compile_rejection_rule_owner_phase_counts"
            ].items()
        )
        print(f"  compile_rejection_rule_owner_phase_counts: {counts}")
    if summary["blocking_compile_rejection_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase}={count}"
            for phase, count in summary[
                "blocking_compile_rejection_owner_phase_counts"
            ].items()
        )
        print(f"  blocking_compile_rejection_owner_phase_counts: {counts}")
    if summary["blocking_compile_rejection_authorization_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "blocking_compile_rejection_authorization_status_counts"
            ].items()
        )
        print(f"  blocking_compile_rejection_authorization_status_counts: {counts}")
    if summary["blocking_compile_rejection_missing_proof_counts"]:
        counts = ", ".join(
            f"{proof}={count}"
            for proof, count in summary[
                "blocking_compile_rejection_missing_proof_counts"
            ].items()
        )
        print(f"  blocking_compile_rejection_missing_proof_counts: {counts}")
    if summary["blocking_compile_rejection_rule_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_rule}={count}"
            for phase_rule, count in summary[
                "blocking_compile_rejection_rule_owner_phase_counts"
            ].items()
        )
        print(f"  blocking_compile_rejection_rule_owner_phase_counts: {counts}")
    if summary["mutation_boundary_event_count"]:
        print(
            "  mutation_boundary: "
            f"events={summary['mutation_boundary_event_count']} "
            f"reports={summary['mutation_boundary_report_count']} "
            f"unexplained_reports={summary['mutation_boundary_unexplained_report_count']} "
            f"unexplained_paths={summary['mutation_boundary_unexplained_path_count']}"
        )
    if summary["mutation_boundary_result_code_counts"]:
        counts = ", ".join(
            f"{code}={count}"
            for code, count in summary[
                "mutation_boundary_result_code_counts"
            ].items()
        )
        print(f"  mutation_boundary_result_code_counts: {counts}")
    if summary["mutation_boundary_proof_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "mutation_boundary_proof_status_counts"
            ].items()
        )
        print(f"  mutation_boundary_proof_status_counts: {counts}")
    if summary["mutation_boundary_proof_rule_counts"]:
        counts = ", ".join(
            f"{rule_id}={count}"
            for rule_id, count in summary[
                "mutation_boundary_proof_rule_counts"
            ].items()
        )
        print(f"  mutation_boundary_proof_rule_counts: {counts}")
    if summary["mutation_boundary_unexplained_statutes"]:
        print(
            "  mutation_boundary_unexplained_statutes: "
            f"{', '.join(summary['mutation_boundary_unexplained_statutes'])}"
        )
    if summary["manual_frontier_manual_compile_candidate_rule_counts"]:
        counts = ", ".join(
            f"{rule_id}={count}"
            for rule_id, count in summary[
                "manual_frontier_manual_compile_candidate_rule_counts"
            ].items()
        )
        print(f"  manual_compile_candidate_rule_counts: {counts}")
    if summary["manual_frontier_manual_compile_candidate_rule_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_rule}={count}"
            for phase_rule, count in summary[
                "manual_frontier_manual_compile_candidate_rule_owner_phase_counts"
            ].items()
        )
        print(f"  manual_compile_candidate_rule_owner_phase_counts: {counts}")
    if summary["manual_frontier_deterministic_candidate_rule_counts"]:
        counts = ", ".join(
            f"{rule_id}={count}"
            for rule_id, count in summary[
                "manual_frontier_deterministic_candidate_rule_counts"
            ].items()
        )
        print(f"  deterministic_frontend_candidate_rule_counts: {counts}")
    if summary["manual_frontier_deterministic_candidate_rule_owner_phase_counts"]:
        counts = ", ".join(
            f"{phase_rule}={count}"
            for phase_rule, count in summary[
                "manual_frontier_deterministic_candidate_rule_owner_phase_counts"
            ].items()
        )
        print(f"  deterministic_frontend_candidate_rule_owner_phase_counts: {counts}")
    if summary["manual_frontier_template_status_counts"]:
        counts = ", ".join(
            f"{status}={count}"
            for status, count in summary[
                "manual_frontier_template_status_counts"
            ].items()
        )
        print(f"  manual_frontier_template_status_counts: {counts}")
    if summary["manual_frontier_template_gap_rule_counts"]:
        counts = ", ".join(
            f"{rule_id}={count}"
            for rule_id, count in summary[
                "manual_frontier_template_gap_rule_counts"
            ].items()
        )
        print(f"  manual_frontier_template_gaps: {counts}")
    else:
        print("  manual_frontier_template_gaps=0")
    if completion_gate_failure_counts:
        counts = ", ".join(
            f"{gate}={count}" for gate, count in completion_gate_failure_counts.items()
        )
        print(f"  completion_gate_failures: {counts}")
    else:
        print("  completion_gate_failures=0")
    if summary["active_unclassified_residual_count"]:
        print(
            "  active_unclassified_residuals="
            f"{summary['active_unclassified_residual_count']}: "
            f"{', '.join(summary['active_unclassified_residual_statutes'])}"
        )
    else:
        print("  active_unclassified_residuals=0")
    if summary["deterministic_frontend_candidate_count"]:
        print(
            "  deterministic_frontend_candidates="
            f"{summary['deterministic_frontend_candidate_count']}: "
            f"{', '.join(summary['deterministic_frontend_candidate_statutes'])}"
        )
    else:
        print("  deterministic_frontend_candidates=0")
    if fail_on_completion_gaps and completion_gate_failure_counts:
        return 1
    if (
        fail_on_active_unclassified_residuals
        and summary["active_unclassified_residual_count"]
    ):
        return 1
    if (
        fail_on_manual_frontier_template_gaps
        and summary["manual_frontier_template_gap_rule_counts"]
    ):
        return 1
    if fail_on_frontier_work_item_gaps and (
        summary["manual_frontier_work_item_missing_candidate_operation_family_count"]
        or summary["manual_frontier_work_item_missing_required_validator_checks_count"]
    ):
        return 1
    if (
        fail_on_deterministic_frontend_candidates
        and summary["deterministic_frontend_candidate_count"]
    ):
        return 1
    if (
        fail_on_non_manual_source_chain_frontier
        and summary["non_manual_source_chain_frontier_count"]
    ):
        return 1
    if fail_on_mutation_boundary_unexplained and (
        summary["mutation_boundary_unexplained_report_count"]
        or summary["mutation_boundary_unexplained_path_count"]
    ):
        return 1
    if fail_on_frontier_work_item_packet_gaps and _frontier_work_item_packet_gap_count(
        summary
    ):
        return 1
    if fail_on_frontier_candidate_set_gaps and _frontier_candidate_set_gap_count(
        summary
    ):
        return 1
    if (
        fail_on_frontier_target_resolution_gaps
        and _frontier_target_resolution_gap_count(summary)
    ):
        return 1
    if fail_on_frontier_source_witness_gaps and _frontier_source_witness_gap_count(
        summary
    ):
        return 1
    if (
        fail_on_source_frontier_work_item_gaps
        and _source_frontier_work_item_gap_count(summary)
    ):
        return 1
    return 0


def run_compare(before_path: Path, after_path: Path) -> int:
    before = json.loads(before_path.read_text())
    after = json.loads(after_path.read_text())
    regressions: list[tuple[str, float, float]] = []
    improvements: list[tuple[str, float, float]] = []
    for sid, a in after.items():
        b = before.get(sid)
        if not b or "aligned" not in a or "aligned" not in b:
            continue
        delta = a["aligned"] - b["aligned"]
        if delta < -_REGRESSION_TOL:
            regressions.append((sid, b["aligned"], a["aligned"]))
        elif delta > _REGRESSION_TOL:
            improvements.append((sid, b["aligned"], a["aligned"]))

    for sid, b, a in sorted(improvements, key=lambda x: x[2] - x[1], reverse=True):
        print(f"  IMPROVED   {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")
    for sid, b, a in sorted(regressions, key=lambda x: x[2] - x[1]):
        print(f"  REGRESSION {sid:24s} {b:6.2f} -> {a:6.2f}  ({a - b:+.2f})")

    print(f"\n{len(improvements)} improved, {len(regressions)} regressed")
    return 1 if regressions else 0


# --------------------------------------------------------------------------- #
# D10 — COMPARE.DETERMINISTIC_GAP_VS_MANUAL_FRONTIER_PARITY audit              #
# --------------------------------------------------------------------------- #
# Per audit_impl_D10.md + AGENTS.md §0: every replay-vs-oracle divergence must
# classify into EXACTLY ONE of {deterministic_gap, manual_compilation_frontier,
# oracle_suspect}. Per-EID taxonomic uniqueness is the contract. This audit
# surfaces double-classified EIDs (an EID in >=2 of the three classes within
# one statute row) as blocking ``COMPARE.EID_DOUBLE_CLASSIFIED`` Observations.
# It is `evidence` not authority per §2.10: firing does not demote either class
# (the conflict's ``detail`` carries both ``source_rule_id``s so triage decides
# which is stale; resolution per spec §9 is via attestation retraction or
# manual-frontier claim promotion, never silent demotion).
#
# WIRE STATUS: the audit helper + AdjudicationRow/EidClassificationConflict
# carriers + the synthetic regression test are LANDSCAPE-LANDED here; the
# WIRE into ``summarize_results`` after ``manual_frontier_records`` and the
# per-EID ``oracle_suspect``/``deterministic_gap`` projections exist is staged
# as a follow-up commit per the D7/D8/D11 staged-wire discipline. Until that
# wire, the audit runs from the unit/helper lane only; the strict-block code
# is unreachable from production. Declared honestly via NO_FIRE_DRILL_YET in
# tests/test_fi_guard_liveness.py per AGENTS.md §2.9.

COMPAREEID_DOUBLE_CLASSIFIED_FINDING_CODE = "COMPARE.EID_DOUBLE_CLASSIFIED"
_D10_AUDIT_STAGE = "compare_oracle_classification"
_D10_AUDIT_OWNER = "compare_oracle_classification"
# Closed-set of the three exhaustively-disjoint §0 classes (audit_impl_D10 §2).
# A new class added here is a §0 contract change, not an inline improvisation.
COMPARE_CLASSIFICATION_KNOWN_CLASSES: frozenset[str] = frozenset(
    {
        "deterministic_gap",
        "manual_compilation_frontier",
        "oracle_suspect",
    }
)


@dataclass(frozen=True, slots=True)
class AdjudicationRow:
    """One per-EID classification row from the broad-baseline projection.

    Lightweight typed carrier per §1.9 — no positional tuple escape hatch.
    Fields:
    * ``statute_id``: the statute whose baseline projection emitted the row.
    * ``eid``: the affected-EID identifier (e.g. ``section-5``).
    * ``classification``: one of :data:`COMPARE_CLASSIFICATION_KNOWN_CLASSES`.
    * ``source_rule_id``: the rule_id / field that asserted the class (e.g.
      ``uk_manual_frontier_missing_payload_source_insufficient`` for manual-
      compilation_frontier; ``uk_compare_text_patch_preimage_consumed_by_
      replay_chain`` for oracle_suspect; ``uk_broad_residual_after_grounding``
      for deterministic_gap).
    * ``witness``: a human-readable locator / snippet; free text, NFC-normalised.
    """

    statute_id: str
    eid: str
    classification: str
    source_rule_id: str
    witness: str = ""


@dataclass(frozen=True, slots=True)
class EidClassificationConflict:
    """One EID classified into >=2 of the three §0 classes for the same statute.

    A typed carrier (§1.9) so a triager can answer §3.2's evidence path
    (which EID + which classes + which source_rule_ids) without re-running
    the broad baseline.

    Fields:
    * ``statute_id``: the statute under audit.
    * ``eid``: the offending EID.
    * ``classes``: the >=2 offending class names (subset of
      :data:`COMPARE_CLASSIFICATION_KNOWN_CLASSES`).
    * ``sources``: the ``source_rule_id`` for each class in ``classes``'s
      ordinal position (parallel tuple — index ``i`` of ``classes`` corresponds
      to index ``i`` of ``sources``).
    * ``detail``: the full per-class projection rows so triage sees the
      witness text too.
    """

    statute_id: str
    eid: str
    classes: tuple[str, ...]
    sources: tuple[str, ...]
    detail: Mapping[str, Any]


def _adjudication_row_key(row: AdjudicationRow) -> tuple[str, str]:
    """Stable per-(statute_id, eid) grouping key."""
    return (row.statute_id, row.eid)


def assert_classification_exclusive(
    adjudications: Iterable[AdjudicationRow],
) -> tuple[int, tuple["Observation", ...]]:
    """Return (conflict_count, observations) for EIDs in >=2 of the three classes.

    Per AGENTS.md §0 + audit_impl_D10: an EID classified into >=2 of
    {deterministic_gap, manual_compilation_frontier, oracle_suspect} for the
    same statute is a §0 disjoint-partition contract break. The audit groups
    by ``(statute_id, eid)`` and surfaces any group whose class set size >= 2
    as one ``COMPARE.EID_DOUBLE_CLASSIFIED`` Observation.

    The audit never returns ``None`` (§1.10 fail-loud discipline — the
    absence of a conflict is a valid result, but None would be silent
    folklore). Empty input is the clean-state witness (empty Observations
    tuple + count 0).

    DOES NOT demote either class (§2.10 evidence, not authority). The
    conflict's Observation detail carries both ``source_rule_id``s and the
    per-class witness rows so triage can decide which class is stale per
    spec §9 (retraction via attestation or claim promotion, not silent
    demotion).
    """
    from lawvm.core.phase_result import Observation  # noqa: PLC0415

    # Group rows by (statute_id, eid) preserving input class set.
    groups: dict[tuple[str, str], list[AdjudicationRow]] = {}
    for row in adjudications:
        # Closed-set discipline: a class outside the known set is NOT silently
        # absorbed into a conflict here — it is forwarded upstream via the
        # projection's existing source-pathology receipt. This audit only
        # compares the three §0 classes.
        if row.classification not in COMPARE_CLASSIFICATION_KNOWN_CLASSES:
            continue
        groups.setdefault(_adjudication_row_key(row), []).append(row)

    conflicts: list[EidClassificationConflict] = []
    for (statute_id, eid), rows in groups.items():
        # Dedupe the (classification, source_rule_id) tuples — the same EID
        # might appear under one class multiple times if the projection emits
        # duplicate rows; that is NOT a multi-class conflict (per spec §0 an
        # EID is in a class once). Use a stable ordering so Observations are
        # reproducible across runs.
        seen: dict[str, str] = {}
        for row in rows:
            seen.setdefault(row.classification, row.source_rule_id)
        if len(seen) < 2:
            continue
        classes_sorted = sorted(seen.keys())
        sources_in_order = tuple(seen[c] for c in classes_sorted)
        conflicts.append(
            EidClassificationConflict(
                statute_id=statute_id,
                eid=eid,
                classes=tuple(classes_sorted),
                sources=sources_in_order,
                detail={
                    "rows": [
                        {
                            "classification": r.classification,
                            "source_rule_id": r.source_rule_id,
                            "witness": r.witness,
                        }
                        for r in rows
                    ],
                    "rule_id": COMPAREEID_DOUBLE_CLASSIFIED_FINDING_CODE,
                    "owner": _D10_AUDIT_OWNER,
                },
            )
        )

    observations = tuple(
        Observation(
            kind=COMPAREEID_DOUBLE_CLASSIFIED_FINDING_CODE,
            stage=_D10_AUDIT_STAGE,
            detail={
                "statute_id": c.statute_id,
                "eid": c.eid,
                "classes": c.classes,
                "sources": c.sources,
                "rows": c.detail["rows"],
                "rule_id": c.detail["rule_id"],
                "owner": c.detail["owner"],
                "reason": (
                    "one EID classified into >=2 of {deterministic_gap, "
                    "manual_compilation_frontier, oracle_suspect} for the same "
                    "statute (§0 disjoint-partition contract break)"
                ),
            },
            source_statute=c.statute_id,
        )
        for c in conflicts
    )
    return (len(observations), observations)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--one", metavar="ID", help="Score a single statute (subprocess unit; prints one JSON line)")
    ap.add_argument("--ids", nargs="+", help="Explicit statute IDs to score")
    ap.add_argument(
        "--ids-file",
        type=Path,
        help=(
            "Read statute IDs from a newline-delimited file or a CSV with a "
            "statute_id column"
        ),
    )
    ap.add_argument("--sample", type=int, help="Sample N statutes with both enacted+current in the archive")
    ap.add_argument("--seed", type=int, default=0, help="Sample RNG seed (default 0)")
    ap.add_argument("--classes", nargs="+", help="Restrict sample to these act-type classes (e.g. ukpga uksi)")
    ap.add_argument(
        "--parallel",
        type=int,
        default=1,
        help=(
            "Number of isolated --one subprocesses to run concurrently "
            "(default 1)"
        ),
    )
    ap.add_argument("--out", type=Path, help="Write JSON snapshot here")
    ap.add_argument(
        "--out-report",
        type=Path,
        help=(
            "Write a typed EvidenceSurfaceReport envelope for the broad-baseline "
            "agreement run without changing the raw snapshot format"
        ),
    )
    ap.add_argument(
        "--report-from-snapshot",
        type=Path,
        metavar="SNAPSHOT",
        help=(
            "Regenerate only the typed EvidenceSurfaceReport envelope from an "
            "existing raw snapshot, using current report-layer classifiers. "
            "Requires --out-report and does not rescore statutes."
        ),
    )
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"), help="Compare two snapshots")
    ap.add_argument(
        "--fail-on-active-unclassified-residuals",
        action="store_true",
        help="Exit nonzero when scored rows still sit in active unclassified residual buckets",
    )
    ap.add_argument(
        "--fail-on-completion-gaps",
        action="store_true",
        help=(
            "Exit nonzero when any current UK broad-baseline completion gate "
            "counter is nonzero"
        ),
    )
    ap.add_argument(
        "--fail-on-manual-frontier-template-gaps",
        action="store_true",
        help=(
            "Exit nonzero when actionable manual/deterministic frontier rows "
            "lack a suggested claim template"
        ),
    )
    ap.add_argument(
        "--fail-on-frontier-work-item-gaps",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier work items lack a candidate "
            "operation family or required validator checks"
        ),
    )
    ap.add_argument(
        "--fail-on-deterministic-frontend-candidates",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier diagnostics still include "
            "deterministic frontend candidates"
        ),
    )
    ap.add_argument(
        "--fail-on-non-manual-source-chain-frontier",
        action="store_true",
        help=(
            "Exit nonzero when source-chain frontier rows remain outside "
            "manual-frontier source-insufficient work"
        ),
    )
    ap.add_argument(
        "--fail-on-mutation-boundary-unexplained",
        action="store_true",
        help=(
            "Exit nonzero when mutation-boundary accounting reports "
            "unexplained reports or paths"
        ),
    )
    ap.add_argument(
        "--fail-on-frontier-work-item-packet-gaps",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier work items lack complete "
            "ExecutionAuthorization/frontier packet readiness fields"
        ),
    )
    ap.add_argument(
        "--fail-on-frontier-candidate-set-gaps",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier CandidateSetCoverage "
            "status counters contain any non-complete status"
        ),
    )
    ap.add_argument(
        "--fail-on-frontier-target-resolution-gaps",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier TargetResolutionCoverage "
            "status counters contain unresolved, missing, or unproven status"
        ),
    )
    ap.add_argument(
        "--fail-on-frontier-source-witness-gaps",
        action="store_true",
        help=(
            "Exit nonzero when manual-frontier work items lack source "
            "witnesses or source/preview digest coverage"
        ),
    )
    ap.add_argument(
        "--fail-on-source-frontier-work-item-gaps",
        action="store_true",
        help=(
            "Exit nonzero when source-frontier rows lack non-executable "
            "work-item packets or digest-backed source witnesses"
        ),
    )
    args = ap.parse_args(argv)
    if args.parallel < 1:
        print("error: --parallel must be a positive integer", file=sys.stderr)
        return 2
    if args.report_from_snapshot and not args.out_report:
        print("error: --report-from-snapshot requires --out-report", file=sys.stderr)
        return 2

    if args.one:
        row = score_one(args.one)
        _annotate_row_work_selection(row)
        print(json.dumps(row))
        return 0
    if args.compare:
        return run_compare(Path(args.compare[0]), Path(args.compare[1]))
    if args.report_from_snapshot:
        return run_report_from_snapshot(
            args.report_from_snapshot,
            args.out_report,
            fail_on_completion_gaps=args.fail_on_completion_gaps,
            fail_on_active_unclassified_residuals=(
                args.fail_on_active_unclassified_residuals
            ),
            fail_on_manual_frontier_template_gaps=(
                args.fail_on_manual_frontier_template_gaps
            ),
            fail_on_frontier_work_item_gaps=args.fail_on_frontier_work_item_gaps,
            fail_on_deterministic_frontend_candidates=(
                args.fail_on_deterministic_frontend_candidates
            ),
            fail_on_non_manual_source_chain_frontier=(
                args.fail_on_non_manual_source_chain_frontier
            ),
            fail_on_mutation_boundary_unexplained=(
                args.fail_on_mutation_boundary_unexplained
            ),
            fail_on_frontier_work_item_packet_gaps=(
                args.fail_on_frontier_work_item_packet_gaps
            ),
            fail_on_frontier_candidate_set_gaps=(
                args.fail_on_frontier_candidate_set_gaps
            ),
            fail_on_frontier_target_resolution_gaps=(
                args.fail_on_frontier_target_resolution_gaps
            ),
            fail_on_frontier_source_witness_gaps=(
                args.fail_on_frontier_source_witness_gaps
            ),
            fail_on_source_frontier_work_item_gaps=(
                args.fail_on_source_frontier_work_item_gaps
            ),
        )

    ids: list[str] = []
    if args.ids:
        ids.extend(args.ids)
    if args.ids_file:
        ids.extend(_load_ids_file(args.ids_file))
    if args.sample:
        ids.extend(sample_statutes(args.sample, args.seed, args.classes))
    if not ids:
        ap.error("nothing to do: pass --one, --ids, --ids-file, --sample, or --compare")
    return run_driver(
        ids,
        args.out,
        args.out_report,
        parallel=args.parallel,
        fail_on_completion_gaps=args.fail_on_completion_gaps,
        fail_on_active_unclassified_residuals=args.fail_on_active_unclassified_residuals,
        fail_on_manual_frontier_template_gaps=(
            args.fail_on_manual_frontier_template_gaps
        ),
        fail_on_frontier_work_item_gaps=args.fail_on_frontier_work_item_gaps,
        fail_on_deterministic_frontend_candidates=(
            args.fail_on_deterministic_frontend_candidates
        ),
        fail_on_non_manual_source_chain_frontier=(
            args.fail_on_non_manual_source_chain_frontier
        ),
        fail_on_mutation_boundary_unexplained=(
            args.fail_on_mutation_boundary_unexplained
        ),
        fail_on_frontier_work_item_packet_gaps=(
            args.fail_on_frontier_work_item_packet_gaps
        ),
        fail_on_frontier_candidate_set_gaps=(
            args.fail_on_frontier_candidate_set_gaps
        ),
        fail_on_frontier_target_resolution_gaps=(
            args.fail_on_frontier_target_resolution_gaps
        ),
        fail_on_frontier_source_witness_gaps=(
            args.fail_on_frontier_source_witness_gaps
        ),
        fail_on_source_frontier_work_item_gaps=(
            args.fail_on_source_frontier_work_item_gaps
        ),
    )


if __name__ == "__main__":
    sys.path.insert(0, str(REPO_ROOT / "src"))
    raise SystemExit(main())
