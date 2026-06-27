"""UK oracle-check — 3-bucket divergence classifier for UK statutes.

For each EID divergence between replay and oracle, classifies into:

  deterministic-gap   — replay missing a node that an amendment should have
                        produced; cross-ref compile rejections / unwarranted ops
  manual-frontier     — commencement-gated, appropriate-place, span/range, savings
                        or other source-insufficient effects; reuses source_adjudication
                        classifiers and effect_diagnostics_out from compile_ops_for_statute
  oracle-suspect      — replay coherent + source-faithful but oracle differs;
                        includes oracle-only EIDs that have no corresponding
                        apply op and no source warrant

Usage (via CLI):
    lawvm -j uk oracle-check ukpga/1978/30
    lawvm -j uk oracle-check nia/2000/1
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lawvm.core.mutation_accounting import build_mutation_invariant_reports
from lawvm.core.mutation_boundary import tree_path_to_diagnostic_string
from lawvm.core.mutation_boundary_proof import MutationBoundaryProof
from lawvm.uk_legislation.grounding_classification import (
    GROUNDING_CLASSIFICATIONS,
    grounding_classification_for_event,
    is_suppression_event,
    unclassified_suppression_events,
)
from lawvm.uk_legislation.grounding_collateral import (
    grounding_collateral_eids as _shared_grounding_collateral_eids,
    score_with_grounding_collateral_excluded,
)
from lawvm.uk_legislation.phase_discipline import UK_PHASE_REPLAY_INVARIANTS
from lawvm.uk_legislation.phase_discipline import uk_phase_owner_counts_for_diagnostics
from lawvm.uk_legislation.source_adjudication import (
    UK_EFFECT_COMPARE_SHAPE_CLASSES,
    UK_EFFECT_SOURCE_PATHOLOGY_CLASSES,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_DB = _REPO_ROOT / "data" / "uk_legislation.farchive"

# ── manual-frontier rule_id prefixes ────────────────────────────────────────
# Any lowering rejection whose rule_id starts with one of these is a
# manual-frontier classification, not a deterministic-gap.
_MANUAL_FRONTIER_RULE_PREFIX = "uk_manual_frontier_"

# Out-of-scope rule IDs map directly to manual-frontier
_OUT_OF_SCOPE_RULE_IDS = frozenset(
    {
        "uk_manual_frontier_application_by_reference_out_of_scope",
        "uk_manual_frontier_as_if_application_modification_out_of_scope",
        "uk_manual_frontier_commencement_effect_out_of_scope",
        "uk_manual_frontier_conditional_temporal_repeal_out_of_scope",
    }
)

# Repeal source warrant rule
_REPEAL_NOT_WARRANTED_RULE_ID = "uk_repeal_target_not_source_warranted"

# ── finer per-EID source-pathology label ────────────────────────────────────
# Loud sentinel for a divergence whose covering diagnostic carries no finer
# source-pathology / manual-frontier / compare-shape class.  Never silently "":
# an empty label would be indistinguishable from "no covering row at all", and
# would hide an unclassified-but-covered divergence from the later ledger
# adapter.
_UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL = "unclassified"


def _is_manual_frontier_rule(rule_id: str) -> bool:
    return rule_id.startswith(_MANUAL_FRONTIER_RULE_PREFIX)


def _grounding_collateral_eids(
    replayed_eids: set[str],
    oracle_eids: set[str],
    alignment_events: list[dict[str, Any]],
) -> list[str]:
    """Compatibility wrapper for the shared UK grounding-collateral helper."""

    return list(
        _shared_grounding_collateral_eids(
            replayed_eids,
            oracle_eids,
            alignment_events,
        )
    )


def _format_owner_phase_counts(counts: dict[str, int]) -> str:
    if not counts:
        return "{}"
    return ", ".join(f"{phase}={count}" for phase, count in counts.items())


def _format_counts(counts: Counter[str]) -> str:
    if not counts:
        return "{}"
    return ", ".join(f"{key}={count}" for key, count in sorted(counts.items()))


def _collect_replay_eids(replayed_ir: Any) -> set[str]:
    """Collect all non-zombie EIDs from the replayed IR."""
    from lawvm.core.ir_helpers import is_zombie

    eids: set[str] = set()

    def _walk(node: Any) -> None:
        if is_zombie(node, pit_date=None):
            return
        eid = node.attrs.get("eId") or node.attrs.get("id")
        if eid:
            eids.add(eid)
        for child in node.children:
            _walk(child)

    _walk(replayed_ir.body)
    for schedule in replayed_ir.supplements:
        _walk(schedule)
    return eids


def _classify_divergences(
    *,
    only_replay: set[str],
    only_oracle: set[str],
    text_diff: set[str],
    lowering_rejections: list[dict[str, Any]],
    effect_diagnostics: list[dict[str, Any]],
    effect_feed_parse_rejections: list[dict[str, Any]],
    authority_rejections: list[dict[str, Any]],
) -> dict[str, list[str]]:
    """Assign each divergent EID to one of the three AGENTS.md §2.1 buckets.

    Returns dict with keys:
      "deterministic_gap"  — replay should have produced this node
      "manual_frontier"    — needs owned claim; source is ambiguous/out-of-scope
      "oracle_suspect"     — replay coherent; oracle is stale/editorial/wrong
      "text_diff"          — both sides have the EID but text differs (unclassified further)

    Classification logic:
      only_oracle  → deterministic_gap by default (oracle has it, replay missed it);
                     promoted to manual_frontier if the rule_ids explaining the
                     miss are all manual-frontier or out-of-scope;
                     promoted to oracle_suspect if there are NO compile rejections
                     at all covering the bucket (oracle-extra with no source ops)
      only_replay  → oracle_suspect by default (replay produced something oracle lacks;
                     the not-source-warranted repeal rule is a strong signal here)
      text_diff    → reported as text_diff (requires deeper per-text analysis)
    """
    # Build a set of affected EIDs implied by manual-frontier rejections
    manual_frontier_eids: set[str] = set()
    deterministic_gap_eids: set[str] = set()

    all_rejections = (
        lowering_rejections
        + effect_feed_parse_rejections
        + authority_rejections
    )
    for rejection in all_rejections:
        rule_id = str(rejection.get("rule_id") or "")
        # affected_provisions is a comma/space-separated list or a single EID fragment
        ap = str(rejection.get("affected_provisions") or "")
        if _is_manual_frontier_rule(rule_id):
            # Manual-frontier rejections: their affected provisions are MF
            if ap:
                manual_frontier_eids.add(ap)
        elif rule_id and rule_id != _REPEAL_NOT_WARRANTED_RULE_ID:
            # Any other blocking rejection that is NOT a warranted repeal
            # is a deterministic gap signal
            if ap:
                deterministic_gap_eids.add(ap)

    # Diagnostics from effect_diagnostics_out carry repeal-not-warranted
    # observations which make only_replay EIDs oracle_suspect (replay correctly
    # retained an EID that the source tried to repeal without warrant)
    repeal_not_warranted_affected: set[str] = set()
    for diag in effect_diagnostics:
        rule_id = str(diag.get("rule_id") or "")
        if rule_id == _REPEAL_NOT_WARRANTED_RULE_ID:
            ap = str(diag.get("affected_provisions") or "")
            if ap:
                repeal_not_warranted_affected.add(ap)

    result: dict[str, list[str]] = {
        "deterministic_gap": [],
        "manual_frontier": [],
        "oracle_suspect": [],
        "text_diff": [],
    }

    # Classify only_oracle EIDs
    for eid in sorted(only_oracle):
        # If any manual-frontier rejection covers a provision that looks like
        # this EID, treat it as manual-frontier
        eid_lower = eid.lower()
        covered_by_mf = any(
            mf_ap and (mf_ap.lower() in eid_lower or eid_lower in mf_ap.lower())
            for mf_ap in manual_frontier_eids
        )
        covered_by_det = any(
            det_ap and (det_ap.lower() in eid_lower or eid_lower in det_ap.lower())
            for det_ap in deterministic_gap_eids
        )
        if covered_by_mf and not covered_by_det:
            result["manual_frontier"].append(eid)
        elif covered_by_det:
            result["deterministic_gap"].append(eid)
        else:
            # Default: oracle has it, replay does not, no clear rejection reason
            # → deterministic gap (the most actionable classification)
            result["deterministic_gap"].append(eid)

    # Classify only_replay EIDs
    for eid in sorted(only_replay):
        eid_lower = eid.lower()
        # If covered by repeal-not-warranted, the replay held the EID correctly
        # while oracle removed it without source warrant → oracle_suspect
        covered_by_rnw = any(
            ap and (ap.lower() in eid_lower or eid_lower in ap.lower())
            for ap in repeal_not_warranted_affected
        )
        if covered_by_rnw:
            result["oracle_suspect"].append(eid)
        else:
            # Replay produced an EID the oracle lacks: likely oracle_suspect
            # (oracle not yet updated) but could be a replay overshoot
            result["oracle_suspect"].append(eid)

    # Text-diff EIDs: report as separate bucket for further investigation
    for eid in sorted(text_diff):
        result["text_diff"].append(eid)

    return result


@dataclass(frozen=True)
class UKDivergenceRow:
    """One per-EID UK replay-vs-oracle divergence, already classified.

    Additive sibling of the summary surface: the summary reports *counts* per
    bucket; this is the finest-grained per-EID list the classifier already
    builds internally. ``diagnosis`` is the §2.1 bucket name
    (``deterministic_gap`` / ``manual_frontier`` / ``oracle_suspect`` /
    ``text_diff``); ``blame_source`` is the affecting-act id of the covering
    compile rejection/diagnostic when one is attributable. ``phase_owner`` and
    ``authority_layer`` let UK blind-spots be bucketed by owning phase / source
    purity; they are "" when not attributable.

    ``source_pathology_label`` is a FINER, parallel signal to ``diagnosis``: the
    covering effect's own source-pathology / manual-frontier / compare-shape
    class (from ``source_adjudication``), where the coarse bucket ``diagnosis``
    only names ``deterministic_gap`` / ``manual_frontier`` / ``oracle_suspect`` /
    ``text_diff``.  It is a loud ``"unclassified"`` sentinel — never silently ""
    — whenever a covering diagnostic row exists but carries no finer class, so an
    unclassified-but-covered divergence stays visible.  It is "" only when no
    covering diagnostic row was found at all (same condition under which
    ``blame_source`` / ``phase_owner`` / ``rule_id`` are also "").  This field is
    additive: it does NOT alter ``diagnosis``, which the ledger adapter keys on.
    """

    eid: str
    diagnosis: str
    blame_source: str = ""
    phase_owner: str = ""
    authority_layer: str = ""
    rule_id: str = ""
    source_pathology_label: str = ""


@dataclass
class UKDivergenceState:
    """Shared compute carrier: the classified per-EID buckets plus the rejection/
    diagnostic rows that explain them. Read-only product of compile+classify."""

    error: str = ""
    buckets: dict[str, list[str]] = field(default_factory=dict)
    lowering_rejections: list[dict[str, Any]] = field(default_factory=list)
    effect_feed_parse_rejections: list[dict[str, Any]] = field(default_factory=list)
    authority_rejections: list[dict[str, Any]] = field(default_factory=list)
    effect_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    n_ops: int = 0


def _diagnostic_rows_for_state(state: UKDivergenceState) -> list[dict[str, Any]]:
    """All compile rejection + diagnostic rows that may explain a divergence."""
    return (
        list(state.lowering_rejections)
        + list(state.effect_feed_parse_rejections)
        + list(state.authority_rejections)
        + list(state.effect_diagnostics)
    )


def _covering_diagnostic_row(eid: str, rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first rejection/diagnostic row whose affected_provisions
    substring-matches ``eid`` (same loose matching the bucket classifier uses)."""
    eid_lower = eid.lower()
    for row in rows:
        ap = str(row.get("affected_provisions") or "")
        if not ap:
            continue
        ap_lower = ap.lower()
        if ap_lower in eid_lower or eid_lower in ap_lower:
            return row
    return None


def _source_pathology_label_for_cover(cover: dict[str, Any] | None) -> str:
    """Derive the finer per-EID source-pathology label from a covering row.

    The coarse ``diagnosis`` bucket is left untouched; this is the parallel finer
    signal.  Derivation precedence (stable / deterministic, first hit wins):

      1. the covering effect's ``source_pathology`` class when it is a recognized
         ``UK_EFFECT_SOURCE_PATHOLOGY_CLASSES`` / ``UK_EFFECT_COMPARE_SHAPE_CLASSES``
         class from ``source_adjudication`` (the finest, type-checked signal);
      2. the manual-frontier classification id (``manual_compile_rule_id`` /
         ``nonblocking_reclassification`` manual id, or any ``uk_manual_frontier_*``
         rule_id) when the covering row is a manual-frontier classification;
      3. a non-empty but *unrecognized* ``source_pathology`` string surfaced
         verbatim (loud, not dropped — an unmodelled finer class is still a finer
         class and must stay visible for the next adapter to model);
      4. otherwise the loud ``_UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL`` sentinel.

    Returns "" only when there is no covering diagnostic row at all (caller's
    ``cover is None`` branch), matching the other covering-derived fields.
    """
    if cover is None:
        return ""
    raw_pathology = str(cover.get("source_pathology") or "")
    if (
        raw_pathology in UK_EFFECT_SOURCE_PATHOLOGY_CLASSES
        or raw_pathology in UK_EFFECT_COMPARE_SHAPE_CLASSES
    ):
        return raw_pathology
    manual_id = str(cover.get("manual_compile_rule_id") or "")
    if _is_manual_frontier_rule(manual_id):
        return manual_id
    rule_id = str(cover.get("rule_id") or "")
    if _is_manual_frontier_rule(rule_id):
        return rule_id
    if raw_pathology:
        # Covered, with a finer class we do not yet model — surface it loudly
        # rather than collapse to the sentinel, so a new class is discoverable.
        return raw_pathology
    return _UNCLASSIFIED_SOURCE_PATHOLOGY_LABEL


def uk_divergence_rows_for_statute(
    statute_id: str,
    *,
    db_path: Path | None = None,
) -> list[UKDivergenceRow]:
    """Additive per-EID divergence surface for one UK statute.

    Returns one ``UKDivergenceRow`` per divergent EID, mirroring the Finland
    ledger adapter's per-section ``DivergenceRow`` shape. The diagnosis is the
    §2.1 bucket the existing classifier already assigns; where a covering
    compile rejection/diagnostic exists, the row also carries that row's
    affecting-act blame, owning phase, and authority layer.

    This does not change the summary surface (``oracle_check_uk_statute``); both
    consume the same ``_compute_uk_divergence_state`` core.
    """
    state = _compute_uk_divergence_state(statute_id, db_path=db_path)
    if state.error:
        return []
    diag_rows = _diagnostic_rows_for_state(state)
    rows: list[UKDivergenceRow] = []
    for bucket_name, eids in state.buckets.items():
        for eid in eids:
            cover = _covering_diagnostic_row(eid, diag_rows)
            blame = ""
            phase_owner = ""
            authority_layer = ""
            rule_id = ""
            if cover is not None:
                blame = str(cover.get("affecting_act_id") or "")
                phase_owner = str(cover.get("owner_phase") or "")
                authority_layer = str(cover.get("authority_layer") or "")
                rule_id = str(cover.get("rule_id") or "")
            rows.append(
                UKDivergenceRow(
                    eid=eid,
                    diagnosis=bucket_name,
                    blame_source=blame,
                    phase_owner=phase_owner,
                    authority_layer=authority_layer,
                    rule_id=rule_id,
                    source_pathology_label=_source_pathology_label_for_cover(cover),
                )
            )
    return rows


def _compute_uk_divergence_state(
    statute_id: str,
    *,
    db_path: Path | None = None,
) -> UKDivergenceState:
    """Compile + replay + classify a UK statute into per-EID divergence buckets.

    Shared read-only core for both the human summary and the per-EID surface.
    On any acquisition error returns a state carrying ``error`` (callers decide
    how to surface it); never raises for a missing archive/source.
    """
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.tools.uk_structural_review import (
        _collect_replay_eid_texts,
        _build_norm_to_raw,
        _build_oracle_norm_text_map,
        _build_oracle_retain_text_elided_norm_map,
        _classify_eids,
        _CLASS_ONLY_REPLAY,
        _CLASS_ONLY_ORACLE,
        _CLASS_TEXT_DIFF,
    )

    resolved_db = db_path if db_path is not None else _DEFAULT_DB
    if not resolved_db.exists():
        return UKDivergenceState(error=f"Archive not found at {resolved_db}")

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(resolved_db) as archive:
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            return UKDivergenceState(error=f"Enacted XML missing: {enacted_url}")
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            return UKDivergenceState(error=f"Oracle XML missing: {oracle_url}")
        oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map: dict[str, str] = oracle_data.get("eid_map", {})
        text_map: dict[str, str] = oracle_data.get("text_map", {})
        retain_text_elided_text_map: dict[str, str] = oracle_data.get(
            "retain_text_elided_text_map", {}
        )
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )
        current_eids: set[str] = set(eid_map.values())

        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        alignment_events: list[dict[str, Any]] = []
        replayed_ir = pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=True,
            oracle_alignment_events_out=alignment_events,
        )

    replay_eid_texts, replay_leaf_eids = _collect_replay_eid_texts(replayed_ir)
    replayed_eids: set[str] = set(replay_eid_texts)

    replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
        replayed_eids,
        current_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )

    replay_norm_to_raw = _build_norm_to_raw(replayed_eids)
    oracle_norm_text_map = _build_oracle_norm_text_map(text_map)
    oracle_retain_text_elided_norm_map = _build_oracle_retain_text_elided_norm_map(
        retain_text_elided_text_map
    )

    classified = _classify_eids(
        replay_eid_texts,
        oracle_norm_text_map,
        replay_norm_set=frozenset(replay_compare_eids),
        oracle_norm_set=frozenset(oracle_compare_eids),
        replay_norm_to_raw=replay_norm_to_raw,
        replay_leaf_eids=frozenset(replay_leaf_eids),
        oracle_retain_text_elided_norm_map=oracle_retain_text_elided_norm_map,
    )

    only_replay_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_REPLAY}
    only_oracle_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_ORACLE}
    text_diff_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_TEXT_DIFF}

    buckets = _classify_divergences(
        only_replay=only_replay_eids,
        only_oracle=only_oracle_eids,
        text_diff=text_diff_eids,
        lowering_rejections=lowering_rejections,
        effect_diagnostics=effect_diagnostics,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        authority_rejections=authority_rejections,
    )

    return UKDivergenceState(
        buckets=buckets,
        lowering_rejections=lowering_rejections,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        authority_rejections=authority_rejections,
        effect_diagnostics=effect_diagnostics,
        n_ops=len(ops),
    )


def oracle_check_uk_statute(
    statute_id: str,
    *,
    db_path: Path | None = None,
    max_sample: int = 5,
    blocking_findings_out: list[str] | None = None,
) -> str:
    """Run UK oracle-check for one statute. Returns a human-readable string.

    Three-bucket output:
      deterministic_gap   — replay should have produced these EIDs
      manual_frontier     — requires owned claims (commencement / appropriate-place / etc.)
      oracle_suspect      — replay coherent; oracle appears stale or wrong
      text_diff           — both have the EID but text differs (investigate further)

    Grounding totality: every unmatched (after_eid=None) alignment event must
    carry exactly one of the four grounding classifications. Any suppression
    event with no usable classification mechanism is a blocking contract
    violation — appended to ``blocking_findings_out`` (when supplied) so the
    caller can fail loud (``main`` exits non-zero).
    """
    from farchive import Farchive
    from lawvm.tools.uk_replay import _archive_url_for_statute
    from lawvm.uk_legislation.uk_grafter import (
        extract_eid_map_bytes,
        parse_uk_statute_ir_bytes,
    )
    from lawvm.uk_legislation import uk_amendment_replay as uk_replay_module
    from lawvm.uk_legislation.source_adjudication import normalize_uk_replay_compare_eids
    from lawvm.uk_legislation.source_state import (
        UKStatuteXmlContentStatus,
        classify_uk_statute_xml_content,
    )
    from lawvm.tools.uk_structural_review import (
        _collect_replay_eid_texts,
        _build_norm_to_raw,
        _build_oracle_norm_text_map,
        _build_oracle_retain_text_elided_norm_map,
        _classify_eids,
        _CLASS_ONLY_REPLAY,
        _CLASS_ONLY_ORACLE,
        _CLASS_TEXT_DIFF,
        _CLASS_SAME,
    )

    resolved_db = db_path if db_path is not None else _DEFAULT_DB
    if not resolved_db.exists():
        return (
            f"=== {statute_id} — UK oracle-check ERROR ===\n"
            f"Archive not found at {resolved_db}\n"
        )

    effect_feed_parse_rejections: list[dict[str, Any]] = []
    effect_diagnostics: list[dict[str, Any]] = []
    lowering_rejections: list[dict[str, Any]] = []
    authority_rejections: list[dict[str, Any]] = []

    with Farchive(resolved_db) as archive:
        enacted_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=True)
        base_bytes = archive.get(enacted_url)
        if base_bytes is None:
            return (
                f"=== {statute_id} — UK oracle-check ERROR ===\n"
                f"Enacted XML missing from archive: {enacted_url}\n"
            )
        base_source = classify_uk_statute_xml_content(base_bytes)
        base_ir = parse_uk_statute_ir_bytes(
            base_bytes,
            statute_id=statute_id,
            version_label="enacted",
            source_path=enacted_url,
        )

        oracle_url = _archive_url_for_statute(statute_id, pit_date=None, enacted=False)
        oracle_bytes = archive.get(oracle_url)
        if oracle_bytes is None:
            return (
                f"=== {statute_id} — UK oracle-check ERROR ===\n"
                f"Oracle XML missing from archive: {oracle_url}\n"
            )
        oracle_data = extract_eid_map_bytes(oracle_bytes, pit_date=None)
        eid_map: dict[str, str] = oracle_data.get("eid_map", {})
        text_map: dict[str, str] = oracle_data.get("text_map", {})
        retain_text_elided_text_map: dict[str, str] = oracle_data.get(
            "retain_text_elided_text_map", {}
        )
        oracle_physical_eid_aliases: dict[str, str] = oracle_data.get(
            "physical_eid_aliases", {}
        )
        oracle_visible_number_eid_aliases: dict[str, str] = oracle_data.get(
            "visible_number_eid_aliases", {}
        )
        current_eids: set[str] = set(eid_map.values())

        pipeline = uk_replay_module.UKReplayPipeline(_REPO_ROOT)
        ops = pipeline.compile_ops_for_statute(
            statute_id,
            pit_date=None,
            archive=archive,
            allow_metadata_backfill=True,
            applicability_mode="effective_date_plus_feed_applied",
            authority_mode="current_mixed",
            allow_metadata_only_effects=True,
            effect_feed_parse_rejections_out=effect_feed_parse_rejections,
            effect_diagnostics_out=effect_diagnostics,
            lowering_rejections_out=lowering_rejections,
            authority_rejections_out=authority_rejections,
        )

        mutation_events: list[Any] = []
        alignment_events: list[dict[str, Any]] = []
        replayed_ir = pipeline.apply_ops(
            base_ir,
            ops,
            eid_map=eid_map,
            text_map=text_map,
            allow_oracle_alignment=True,
            oracle_alignment_events_out=alignment_events,
            mutation_events_out=mutation_events,
        )

    # Collect replay EID texts + leaf EIDs
    replay_eid_texts, replay_leaf_eids = _collect_replay_eid_texts(replayed_ir)
    replayed_eids: set[str] = set(replay_eid_texts)

    # Normalize both EID sets
    replay_compare_eids, oracle_compare_eids = normalize_uk_replay_compare_eids(
        replayed_eids,
        current_eids,
        oracle_physical_eid_aliases=oracle_physical_eid_aliases,
        oracle_visible_number_eid_aliases=oracle_visible_number_eid_aliases,
    )

    replay_norm_to_raw = _build_norm_to_raw(replayed_eids)
    oracle_norm_text_map = _build_oracle_norm_text_map(text_map)
    oracle_retain_text_elided_norm_map = _build_oracle_retain_text_elided_norm_map(
        retain_text_elided_text_map
    )

    classified = _classify_eids(
        replay_eid_texts,
        oracle_norm_text_map,
        replay_norm_set=frozenset(replay_compare_eids),
        oracle_norm_set=frozenset(oracle_compare_eids),
        replay_norm_to_raw=replay_norm_to_raw,
        replay_leaf_eids=frozenset(replay_leaf_eids),
        oracle_retain_text_elided_norm_map=oracle_retain_text_elided_norm_map,
    )

    only_replay_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_REPLAY}
    only_oracle_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_ONLY_ORACLE}

    grounding_collateral_eids = _grounding_collateral_eids(
        replayed_eids, current_eids, alignment_events
    )

    # Grounding totality over the negative space: every unmatched
    # (after_eid=None) alignment event must carry exactly one of the four
    # grounding classifications. Tally the buckets and surface any suppression
    # event that lacks a usable classification mechanism as a blocking finding.
    grounding_suppression_total = sum(
        1 for event in alignment_events if is_suppression_event(event)
    )
    grounding_classification_counts: dict[str, int] = {
        value: 0 for value in GROUNDING_CLASSIFICATIONS
    }
    for event in alignment_events:
        classification = grounding_classification_for_event(event)
        if classification is None:
            continue
        if classification in grounding_classification_counts:
            grounding_classification_counts[classification] += 1
    grounding_unclassified_events = unclassified_suppression_events(alignment_events)
    collateral_score = score_with_grounding_collateral_excluded(
        replay_compare_eids,
        oracle_compare_eids,
        [
            {
                **event,
                "after_eid": str(event.get("after_eid") or "").lower(),
            }
            for event in alignment_events
        ],
    )
    text_diff_eids = {e for e, v in classified.items() if v["kind"] == _CLASS_TEXT_DIFF}
    same_count = sum(1 for v in classified.values() if v["kind"] == _CLASS_SAME)

    buckets = _classify_divergences(
        only_replay=only_replay_eids,
        only_oracle=only_oracle_eids,
        text_diff=text_diff_eids,
        lowering_rejections=lowering_rejections,
        effect_diagnostics=effect_diagnostics,
        effect_feed_parse_rejections=effect_feed_parse_rejections,
        authority_rejections=authority_rejections,
    )

    compile_rejection_rows = lowering_rejections + effect_feed_parse_rejections + authority_rejections
    manual_frontier_rejection_rows = [
        r for r in lowering_rejections if _is_manual_frontier_rule(str(r.get("rule_id") or ""))
    ]
    deterministic_rejection_rows = [
        r
        for r in compile_rejection_rows
        if not _is_manual_frontier_rule(str(r.get("rule_id") or ""))
        and str(r.get("rule_id") or "") != _REPEAL_NOT_WARRANTED_RULE_ID
    ]
    compile_rejection_owner_phase_counts = uk_phase_owner_counts_for_diagnostics(
        compile_rejection_rows
    )
    manual_frontier_owner_phase_counts = uk_phase_owner_counts_for_diagnostics(
        manual_frontier_rejection_rows
    )
    deterministic_rejection_owner_phase_counts = uk_phase_owner_counts_for_diagnostics(
        deterministic_rejection_rows
    )

    # Count compile rejections by category
    n_mf_rejections = sum(
        1 for r in lowering_rejections
        if _is_manual_frontier_rule(str(r.get("rule_id") or ""))
    )
    n_det_rejections = sum(
        1 for r in deterministic_rejection_rows
    )
    n_rnw_diagnostics = sum(
        1 for d in effect_diagnostics
        if str(d.get("rule_id") or "") == _REPEAL_NOT_WARRANTED_RULE_ID
    )
    mutation_reports = build_mutation_invariant_reports(mutation_events)
    mutation_proofs = tuple(
        MutationBoundaryProof.from_mutation_invariant_report(
            report,
            proof_id=f"uk-oracle-check-mutation-boundary:{index}:{report.op_id or '<missing>'}",
            jurisdiction="uk",
            materialization_surface="uk_oracle_check_replay",
            owner_phase=UK_PHASE_REPLAY_INVARIANTS,
            safe_default="treat_unproved_boundary_as_replay_invariant_residual",
            forbidden_shortcuts=(
                "ignore_unexplained_changed_paths",
                "use_oracle_agreement_as_boundary_proof",
                "broaden_target_region_after_replay",
            ),
        )
        for index, report in enumerate(mutation_reports)
    )
    mutation_proof_status_counts = Counter(proof.boundary_proof_status for proof in mutation_proofs)
    mutation_proof_rule_counts = Counter(proof.rule_id for proof in mutation_proofs)
    mutation_unexplained_reports = [
        report
        for report in mutation_reports
        if report.unexplained_changed_paths or not report.path_set_invariant_holds
    ]
    mutation_unexplained_path_count = sum(
        len(report.unexplained_changed_paths)
        for report in mutation_unexplained_reports
    )

    common = replay_compare_eids & oracle_compare_eids
    similarity = len(common) / max(len(replay_compare_eids), len(oracle_compare_eids), 1)

    lines: list[str] = [
        f"=== {statute_id} — UK oracle-check ===",
        (
            f"Similarity: {similarity:.1%}  "
            f"replay={len(replay_compare_eids)}  oracle={len(oracle_compare_eids)}  "
            f"common={len(common)}  same={same_count}"
        ),
        (
            "Similarity excluding grounding collateral: "
            f"{collateral_score.collateral_excluded_similarity:.1%}  "
            f"excluded={len(grounding_collateral_eids)}"
        ),
        f"Ops compiled: {len(ops)}  "
        f"Rejections: det={n_det_rejections} mf={n_mf_rejections}  "
        f"repeal-not-warranted diagnostics={n_rnw_diagnostics}",
        (
            "Rejection owner phases: "
            f"all={_format_owner_phase_counts(compile_rejection_owner_phase_counts)}  "
            f"det={_format_owner_phase_counts(deterministic_rejection_owner_phase_counts)}  "
            f"mf={_format_owner_phase_counts(manual_frontier_owner_phase_counts)}"
        ),
        (
            f"Mutation boundary: events={len(mutation_events)}  "
            f"reports={len(mutation_reports)}  "
            f"unexplained_reports={len(mutation_unexplained_reports)}  "
            f"unexplained_paths={mutation_unexplained_path_count}"
        ),
        (
            "Mutation boundary proof statuses: "
            f"{_format_counts(Counter(str(key) for key, count in mutation_proof_status_counts.items() for _ in range(count)))}"
        ),
        (
            "Mutation boundary proof rules: "
            f"{_format_counts(mutation_proof_rule_counts)}"
        ),
        (
            "Base source: "
            f"{base_source.xml_content_status.value}  "
            f"bytes={base_source.size}  "
            f"NumberOfProvisions={base_source.number_of_provisions or '<unknown>'}  "
            f"body={base_source.has_body}  schedules={base_source.has_schedules}"
        ),
        "",
        "DIVERGENCE BUCKET SUMMARY:",
        f"  deterministic_gap  : {len(buckets['deterministic_gap'])}  "
        "(replay should have produced these EIDs; investigate compile rejections)",
        f"  manual_frontier    : {len(buckets['manual_frontier'])}  "
        "(needs owned claim: commencement/appropriate-place/span/savings)",
        f"  oracle_suspect     : {len(buckets['oracle_suspect'])}  "
        "(replay coherent; oracle may be stale or wrong)",
        f"  text_diff          : {len(buckets['text_diff'])}  "
        "(both sides have the EID but text differs; investigate per-EID)",
        f"  grounding_collateral: {len(grounding_collateral_eids)}  "
        "(subset of only-replay EIDs minted by oracle-alignment local_fallback, not a source op)",
        "",
        "GROUNDING CLASSIFICATION TOTALITY:",
        f"  suppression events (unmatched nodes): {grounding_suppression_total}",
        f"  source_faithful_oracle_absent : "
        f"{grounding_classification_counts['source_faithful_oracle_absent']}",
        f"  parser_structure_desync       : "
        f"{grounding_classification_counts['parser_structure_desync']}",
        f"  non_commensurable             : "
        f"{grounding_classification_counts['non_commensurable']}",
        f"  unresolved                    : "
        f"{grounding_classification_counts['unresolved']}  "
        "(conservative default; numerator-excluded, never folded as source-faithful)",
        f"  UNCLASSIFIED (contract violation): {len(grounding_unclassified_events)}",
        "",
    ]

    if base_source.xml_content_status is UKStatuteXmlContentStatus.METADATA_ONLY:
        lines.extend(
            [
                "BASE_SOURCE_FRONTIER:",
                (
                    "  Enacted XML is a metadata-only legal-source envelope. "
                    "Oracle-only original provisions are source-acquisition frontier "
                    "evidence here, not proof that replay should synthesize the base "
                    "from current text."
                ),
                "",
            ]
        )

    for bucket_name, bucket_eids in buckets.items():
        if not bucket_eids:
            continue
        sample = bucket_eids[:max_sample]
        lines.append(f"{bucket_name.upper()} ({len(bucket_eids)} EIDs):")
        for eid in sample:
            lines.append(f"  {eid}")
        if len(bucket_eids) > max_sample:
            lines.append(f"  ... ({len(bucket_eids) - max_sample} more)")
        lines.append("")

    if grounding_collateral_eids:
        lines.append(
            f"GROUNDING_COLLATERAL ({len(grounding_collateral_eids)} EIDs minted by "
            "oracle-alignment local_fallback, no source op):"
        )
        for eid in grounding_collateral_eids[:max_sample]:
            lines.append(f"  {eid}")
        if len(grounding_collateral_eids) > max_sample:
            lines.append(f"  ... ({len(grounding_collateral_eids) - max_sample} more)")
        lines.append("")

    if mutation_unexplained_reports:
        lines.append(
            f"MUTATION_BOUNDARY_UNEXPLAINED ({len(mutation_unexplained_reports)} reports):"
        )
        for report in mutation_unexplained_reports[:max_sample]:
            result_codes = ", ".join(result.code for result in report.results) or "<none>"
            unexplained_paths = [
                tree_path_to_diagnostic_string(path)
                for path in report.unexplained_changed_paths
            ]
            path_preview = ", ".join(unexplained_paths[:3]) or "<none>"
            if len(unexplained_paths) > 3:
                path_preview = f"{path_preview}, ..."
            lines.append(
                "  "
                f"op_id={report.op_id or '<missing>'} helper={report.helper} "
                f"outcome={report.outcome} "
                f"result_codes={result_codes} "
                f"unexplained_paths={len(report.unexplained_changed_paths)} "
                f"paths={path_preview}"
            )
        if len(mutation_unexplained_reports) > max_sample:
            lines.append(f"  ... ({len(mutation_unexplained_reports) - max_sample} more)")
        lines.append("")

    if n_mf_rejections > 0:
        lines.append(f"TOP MANUAL-FRONTIER REJECTION RULES ({n_mf_rejections} total):")
        rule_counter: Counter[str] = Counter()
        for r in lowering_rejections:
            rule_id = str(r.get("rule_id") or "")
            if _is_manual_frontier_rule(rule_id):
                rule_counter[rule_id] += 1
        for rule_id, count in rule_counter.most_common(5):
            lines.append(f"  {count:4d}  {rule_id}")
        lines.append("")

    if n_det_rejections > 0:
        lines.append(f"TOP DETERMINISTIC-GAP REJECTION RULES ({n_det_rejections} total):")
        rule_counter_det: Counter[str] = Counter()
        for r in compile_rejection_rows:
            rule_id = str(r.get("rule_id") or "")
            if (
                rule_id
                and not _is_manual_frontier_rule(rule_id)
                and rule_id != _REPEAL_NOT_WARRANTED_RULE_ID
            ):
                rule_counter_det[rule_id] += 1
        for rule_id, count in rule_counter_det.most_common(5):
            lines.append(f"  {count:4d}  {rule_id}")
        lines.append("")

    # Fail loud on any unmatched node left structurally unclassified. The
    # grounding-classification contract is total over the negative space; an
    # uncovered suppression event is a guard-liveness defect, not legal noise.
    if grounding_unclassified_events:
        finding = (
            f"UK_GROUNDING_CLASSIFICATION_INCOMPLETE: {len(grounding_unclassified_events)} "
            "suppression event(s) carry no usable grounding classification mechanism "
            "for statute "
            f"{statute_id}"
        )
        lines.append("BLOCKING FINDING:")
        lines.append(f"  {finding}")
        for event in grounding_unclassified_events[:max_sample]:
            lines.append(
                "    "
                f"kind={event.get('kind')!r} label={event.get('label')!r} "
                f"before_eid={event.get('before_eid')!r} "
                f"match_method={event.get('match_method')!r}"
            )
        if len(grounding_unclassified_events) > max_sample:
            lines.append(
                f"    ... ({len(grounding_unclassified_events) - max_sample} more)"
            )
        lines.append("")
        if blocking_findings_out is not None:
            blocking_findings_out.append(finding)

    return "\n".join(lines) + "\n"


def main(args: Any) -> None:
    """Entry point for ``lawvm -j uk oracle-check <statute_id>``."""
    db_arg = getattr(args, "db", None)
    db_path = Path(db_arg) if db_arg else None
    sid = getattr(args, "statute_id", None)

    if not sid:
        print("ERROR: provide <statute_id>", file=sys.stderr)
        raise SystemExit(1)

    blocking_findings: list[str] = []
    result = oracle_check_uk_statute(
        sid, db_path=db_path, blocking_findings_out=blocking_findings
    )
    print(result, end="")
    if blocking_findings:
        for finding in blocking_findings:
            print(finding, file=sys.stderr)
        raise SystemExit(1)
