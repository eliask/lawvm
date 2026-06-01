"""UK frontend phase-owner classification for diagnostics and work queues."""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any


UK_PHASE_EFFECT_METADATA_FRONTEND = "effect_metadata_frontend"
UK_PHASE_AFFECTING_SOURCE_EXTRACTION = "affecting_source_extraction"
UK_PHASE_TYPED_ELABORATION = "typed_elaboration"
UK_PHASE_CANONICAL_OP_COMPILATION = "canonical_op_compilation"
UK_PHASE_REPLAY_INVARIANTS = "replay_invariants"
UK_PHASE_COMPARE_ORACLE_CLASSIFICATION = "compare_oracle_classification"
UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER = "source_pathology_manual_frontier"

UK_PHASE_OWNER_VALUES = frozenset(
    {
        UK_PHASE_EFFECT_METADATA_FRONTEND,
        UK_PHASE_AFFECTING_SOURCE_EXTRACTION,
        UK_PHASE_TYPED_ELABORATION,
        UK_PHASE_CANONICAL_OP_COMPILATION,
        UK_PHASE_REPLAY_INVARIANTS,
        UK_PHASE_COMPARE_ORACLE_CLASSIFICATION,
        UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER,
    }
)

_EFFECT_METADATA_RULE_TOKENS = (
    "application_by_reference",
    "application_modification",
    "as_if_application",
    "commencement",
    "conditional_temporal",
    "empty_type",
    "non_textual",
    "out_of_scope",
    "pit_prospective",
    "prospective_commencement",
    "temporal",
)
_SOURCE_EXTRACTION_RULE_TOKENS = (
    "fragment_context",
    "instruction_header",
    "missing_payload",
    "payload_missing",
    "payload_without_action",
    "parser_or_extraction",
    "reference_only",
    "source_carried",
    "source_payload",
    "source_pathology_insufficient",
    "text_patch_postimage_chain_gap",
    "text_patch_preimage_chain_gap",
    "text_patch_target_source_chain_gap",
    "unquoted_preimage",
)
_TYPED_ELABORATION_RULE_TOKENS = (
    "amendment_program",
    "appropriate_place",
    "cross_container",
    "crossheading",
    "definition",
    "heading_facet",
    "index_entry",
    "misselected_target",
    "mixed_",
    "range_to_container",
    "referent_qualified",
    "repeal_table",
    "savings_qualified",
    "schedule_list_entry",
    "schedule_note",
    "structural",
    "table",
    "whole_act",
)


def uk_phase_owner_for_manual_frontier(
    *,
    manual_compile_status: str,
    manual_compile_rule_id: str,
    source_pathology: str = "",
) -> str:
    """Return the UK frontend phase that owns a manual-frontier classification."""
    status = str(manual_compile_status or "")
    rule_id = str(manual_compile_rule_id or "")
    pathology = str(source_pathology or "")
    combined = f"{status} {rule_id} {pathology}".lower()
    if not combined.strip():
        return UK_PHASE_CANONICAL_OP_COMPILATION
    if status == "deterministic_frontend_supported":
        return UK_PHASE_CANONICAL_OP_COMPILATION
    if any(token in combined for token in _EFFECT_METADATA_RULE_TOKENS):
        return UK_PHASE_EFFECT_METADATA_FRONTEND
    if status == "source_insufficient" or any(
        token in combined for token in _SOURCE_EXTRACTION_RULE_TOKENS
    ):
        return UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    if any(token in combined for token in _TYPED_ELABORATION_RULE_TOKENS):
        return UK_PHASE_TYPED_ELABORATION
    if "unclassified" in combined:
        return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER


def uk_phase_owner_for_diagnostic(record: Mapping[str, Any]) -> str:
    """Infer the UK frontend phase that owns a diagnostic record."""
    explicit = str(record.get("owner_phase") or "")
    if explicit in UK_PHASE_OWNER_VALUES:
        return explicit
    manual_rule_id = str(record.get("manual_compile_rule_id") or "")
    manual_status = str(record.get("manual_compile_status") or "")
    if manual_rule_id or manual_status:
        return uk_phase_owner_for_manual_frontier(
            manual_compile_status=manual_status,
            manual_compile_rule_id=manual_rule_id,
            source_pathology=str(record.get("source_pathology") or ""),
        )
    rule_id = str(record.get("rule_id") or "").lower()
    family = str(record.get("family") or "").lower()
    phase = str(record.get("phase") or "").lower()
    combined = f"{rule_id} {family} {phase}"
    if "manual_compile_frontier" in combined:
        return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    if "source_pathology" in combined:
        return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER
    if (
        "source_acquisition" in combined
        or "source_parse" in combined
        or "affecting_act" in combined
    ):
        return UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    if "effect_feed" in combined or "metadata" in combined:
        return UK_PHASE_EFFECT_METADATA_FRONTEND
    if any(token in combined for token in _SOURCE_EXTRACTION_RULE_TOKENS):
        return UK_PHASE_AFFECTING_SOURCE_EXTRACTION
    if "replay" in combined:
        return UK_PHASE_REPLAY_INVARIANTS
    if "oracle" in combined or "compare" in combined:
        return UK_PHASE_COMPARE_ORACLE_CLASSIFICATION
    if (
        "elaboration" in combined
        or "amendment_program" in combined
        or "target_resolution" in combined
        or "range_to_container" in combined
    ):
        return UK_PHASE_TYPED_ELABORATION
    if phase == "lowering" or rule_id.startswith("uk_effect_"):
        return UK_PHASE_CANONICAL_OP_COMPILATION
    return UK_PHASE_SOURCE_PATHOLOGY_MANUAL_FRONTIER


def uk_phase_owner_counts_for_diagnostics(
    records: Iterable[Mapping[str, Any]],
) -> dict[str, int]:
    """Return stable owner-phase counts for a diagnostic/workqueue row set."""
    counts = Counter(uk_phase_owner_for_diagnostic(record) for record in records)
    return dict(sorted(counts.items()))
