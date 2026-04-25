"""Typed statute-level proof algebra (A2).

Replaces the ad-hoc _build_proof_claims() dict-building function with a
typed statute-level rollup, following the Pro design in
notes/PRO_PROOF_CLAIMS_ALGEBRA.md.

Architecture (four parts, per spec §6):
  1. build_statute_context(...)       -> StatuteEvidenceContext
  2. partition_replay_bug_sections()  -> StatuteRollupPartition
  3. emit_statute_claims()            -> tuple[StatuteClaimRecord, ...]
  4. StatuteResolvedClaims (result wrapper)

The typed path (build_proof_claims_typed) MUST produce bit-identical
output to the legacy _build_proof_claims() path via to_legacy_claims().
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, TypeAlias

from lawvm.tools._evidence_helpers import (
    _build_support_lookup_maps,
    _has_negligible_blame_drop_on_preexisting_residue,
    _lookup_support_row,
    _obs,
    _ORACLE_INCORRECT_DIAGNOSES,
    _REPLAY_BUG_DIAGNOSES,
    _section_similarity,
)
from lawvm.tools.evidence_claim_algebra import (
    PositiveClaim,
    ProofTier,
    UnresolvedSink,
)
from lawvm.tools.evidence_claims import _is_deterministic_sparse_oracle_stale_support


# ---------------------------------------------------------------------------
# Core typed wrappers (spec §1)
# ---------------------------------------------------------------------------

StatuteClaim: TypeAlias = PositiveClaim | UnresolvedSink


@dataclass(frozen=True)
class StatuteClaimRecord:
    """One emitted statute-level claim plus its presentation payload."""

    claim: StatuteClaim
    summary: str
    trigger_observations: tuple[dict[str, Any], ...] = ()

    @property
    def tier(self) -> ProofTier:
        return self.claim.tier

    @property
    def kind(self) -> str:
        return self.claim.kind

    @property
    def inference_rule(self) -> str:
        return self.claim.inference_rule

    def to_legacy_dict(self) -> dict[str, Any]:
        return {
            "tier": self.claim.tier.value,
            "kind": self.claim.kind,
            "summary": self.summary,
            "inference_rule": self.claim.inference_rule,
            "trigger_observations": list(self.trigger_observations),
            "support": dict(self.claim.support),
        }


STATUTE_PRIMARY_TIER_ORDER: tuple[ProofTier, ...] = (
    ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
    ProofTier.PROVED_SOURCE_PATHOLOGY,
    ProofTier.PROVED_ORACLE_INCORRECT,
    ProofTier.PROVED_REPLAY_BUG,
    ProofTier.UNRESOLVED,
)


@dataclass(frozen=True)
class StatuteResolvedClaims:
    """Ordered statute-level claim list with derived summary properties."""

    claims: tuple[StatuteClaimRecord, ...]

    @property
    def primary_tier(self) -> ProofTier:
        seen = {claim.tier for claim in self.claims}
        for tier in STATUTE_PRIMARY_TIER_ORDER:
            if tier in seen:
                return tier
        return ProofTier.UNRESOLVED

    @property
    def primary_claims(self) -> tuple[StatuteClaimRecord, ...]:
        pt = self.primary_tier
        return tuple(claim for claim in self.claims if claim.tier is pt)

    def to_legacy_claims(self) -> list[dict[str, Any]]:
        return [claim.to_legacy_dict() for claim in self.claims]


# ---------------------------------------------------------------------------
# Section seeds and bisect lookup family (spec §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SectionRollupSeed:
    """Typed replacement for the small sec={...} dicts in _build_proof_claims."""

    section: str
    diagnosis: str
    blame_source: str
    blame_title: str
    similarity: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "section": self.section,
            "diagnosis": self.diagnosis,
            "blame_source": self.blame_source,
            "blame_title": self.blame_title,
            "similarity": self.similarity,
        }


@dataclass(frozen=True)
class RollupLookupFamily:
    """Typed three-tier lookup: exact, by_source_chapter_label, by_source_label."""

    exact: Dict[str, Dict[str, Any]]
    by_source_chapter_label: Dict[tuple[str, str, str], Dict[str, Any]]
    by_source_label: Dict[tuple[str, str], Dict[str, Any]]

    def lookup(self, seed: SectionRollupSeed) -> Dict[str, Any] | None:
        return _lookup_support_row(
            seed.to_dict(),
            self.exact,
            self.by_source_chapter_label,
            self.by_source_label,
        )


def _build_family(items: list[dict[str, Any]]) -> RollupLookupFamily:
    exact: Dict[str, Dict[str, Any]] = {str(item.get("section") or ""): item for item in items}
    by_scl_raw, by_sl_raw = _build_support_lookup_maps(items)
    # _build_support_lookup_maps returns typed dicts with Mapping[str, Any] values;
    # cast to Dict[str, Any] for _lookup_support_row compatibility.
    by_scl: Dict[tuple[str, str, str], Dict[str, Any]] = {
        k: dict(v) for k, v in by_scl_raw.items()
    }
    by_sl: Dict[tuple[str, str], Dict[str, Any]] = {
        k: dict(v) for k, v in by_sl_raw.items()
    }
    return RollupLookupFamily(
        exact=exact,
        by_source_chapter_label=by_scl,
        by_source_label=by_sl,
    )


# ---------------------------------------------------------------------------
# Typed bisect index (spec §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteBisectIndex:
    preexisting: RollupLookupFamily
    negligible_preexisting_drop: RollupLookupFamily
    improved: RollupLookupFamily
    repeal_only_without_payload: RollupLookupFamily
    payload_prefers_replay: RollupLookupFamily
    sparse_elaboration: RollupLookupFamily
    deterministic_sparse_oracle_stale: RollupLookupFamily
    baseline_same_chapter_drift: RollupLookupFamily
    baseline_same_section_structure_drift: RollupLookupFamily


def _build_bisect_index(section_bisect: list[dict[str, Any]]) -> StatuteBisectIndex:
    return StatuteBisectIndex(
        preexisting=_build_family(
            [i for i in section_bisect if bool(i.get("preexisting_before_any_drop"))]
        ),
        negligible_preexisting_drop=_build_family(
            [i for i in section_bisect if _has_negligible_blame_drop_on_preexisting_residue(i)]
        ),
        improved=_build_family(
            [i for i in section_bisect if bool(i.get("blame_source_improved_or_equal"))]
        ),
        repeal_only_without_payload=_build_family(
            [i for i in section_bisect if bool(i.get("blame_only_repeal_without_payload"))]
        ),
        payload_prefers_replay=_build_family(
            [i for i in section_bisect if bool(i.get("blame_payload_prefers_replay"))]
        ),
        sparse_elaboration=_build_family(
            [i for i in section_bisect if bool(i.get("blame_sparse_elaboration"))]
        ),
        deterministic_sparse_oracle_stale=_build_family(
            [i for i in section_bisect if _is_deterministic_sparse_oracle_stale_support(i)]
        ),
        baseline_same_chapter_drift=_build_family(
            [
                i for i in section_bisect
                if isinstance(i.get("baseline_alternative_replay_match"), dict)
                and bool((i.get("baseline_alternative_replay_match") or {}).get("best_replay_section"))
            ]
        ),
        baseline_same_section_structure_drift=_build_family(
            [
                i for i in section_bisect
                if isinstance(i.get("baseline_unmatched_oracle_subsections"), dict)
                and bool((i.get("baseline_unmatched_oracle_subsections") or {}).get("count"))
            ]
        ),
    )


# ---------------------------------------------------------------------------
# Statute evidence context (spec §2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatuteEvidenceContext:
    section_results: tuple[SectionRollupSeed, ...]
    stale_sections: tuple[SectionRollupSeed, ...]
    replay_bug_pool: tuple[SectionRollupSeed, ...]
    source_pathologies: tuple[dict[str, Any], ...]
    html_noncommensurable_reason: str
    html_error: str
    missing_from_xml: tuple[str, ...]
    extra_in_xml: tuple[str, ...]
    contingent_effective_sources: tuple[str, ...]
    corrigendum_support: tuple[dict[str, Any], ...]
    oracle_suspect_detail: str
    oracle_suspect_pending: str
    bisect_index: StatuteBisectIndex
    alternative_replay_matches: Mapping[str, dict[str, Any]]
    oracle_range_matches: Mapping[str, dict[str, Any]]
    cross_chapter_oracle_matches: Mapping[str, dict[str, Any]]
    cross_chapter_replay_matches: Mapping[str, dict[str, Any]]
    # Typed section claim summaries (derived from ResolvedSectionClaims)
    selected_replay_divergence_sections: frozenset[str]
    selected_section_tiers: frozenset[ProofTier]
    selected_section_kinds: frozenset[str]
    selected_section_outcomes: tuple[tuple[str, str, str], ...]
    has_section_results: bool
    all_sections_match: bool
    # Whether the section-claims residual gate should be applied (mirrors "if section_claims is not None")
    apply_section_claims_gate: bool
    # Raw section_claims rows (legacy dicts) — for Gap 1/4 unanimous logic
    section_claims_rows: tuple[dict[str, Any], ...] | None
    # Content-based version drift proof (from detect_content_version_drift)
    content_version_drift: dict[str, Any] | None = None


def build_statute_context(
    *,
    section_results: List[Dict[str, Any]],
    source_pathologies: List[Dict[str, Any]],
    html_topology: Dict[str, Any],
    contingent_effective_sources: List[str],
    corrigendum_support: List[Dict[str, Any]],
    oracle_suspect_detail: str = "",
    oracle_suspect_pending: str = "",
    html_error: str = "",
    section_bisect: Optional[List[Dict[str, Any]]] = None,
    alternative_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    oracle_range_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    section_claims: Optional[List[Dict[str, Any]]] = None,
    typed_section_results: Optional[Any] = None,  # list[ResolvedSectionClaims]
    content_version_drift: Optional[Dict[str, Any]] = None,
) -> StatuteEvidenceContext:
    """Build a fully typed statute evidence context from raw inputs."""
    # Build section seeds and initial stale/replay pools (mirrors _build_proof_claims lines 783-801)
    stale_seeds: list[SectionRollupSeed] = []
    replay_seeds: list[SectionRollupSeed] = []
    all_seeds: list[SectionRollupSeed] = []

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
        seed = SectionRollupSeed(
            section=str(item.get("section") or ""),
            diagnosis=diag,
            blame_source=str(item.get("blame_source") or ""),
            blame_title=str(item.get("blame_title") or ""),
            similarity=_sim_value,
        )
        all_seeds.append(seed)
        if diag in _ORACLE_INCORRECT_DIAGNOSES:
            stale_seeds.append(seed)
        elif diag in _REPLAY_BUG_DIAGNOSES:
            replay_seeds.append(seed)

    # Build bisect index
    bisect_index = _build_bisect_index(list(section_bisect or []))

    # Derive typed section claim summaries (spec §4)
    selected_replay_divergence_sections: frozenset[str] = frozenset()
    selected_section_tiers: frozenset[ProofTier] = frozenset()
    selected_section_kinds: frozenset[str] = frozenset()
    selected_section_outcomes: tuple[tuple[str, str, str], ...] = ()

    if typed_section_results is not None:
        # Use typed ResolvedSectionClaims objects directly
        selected_replay_divergence_sections = frozenset(
            result.ctx.section_label
            for result in typed_section_results
            if (
                result.selected is not None
                and result.selected.tier is ProofTier.PROVED_REPLAY_BUG
                and result.selected.kind == "replay_divergence"
            )
        )
        selected_section_tiers = frozenset(
            result.selected.tier
            for result in typed_section_results
            if result.selected is not None
        )
        selected_section_kinds = frozenset(
            result.selected.kind
            for result in typed_section_results
            if result.selected is not None
        )
        selected_section_outcomes = tuple(
            (
                str(result.ctx.section_label or ""),
                str(result.selected.kind if result.selected is not None else ""),
                str(result.selected.tier.value if result.selected is not None else ""),
            )
            for result in typed_section_results
        )
    elif section_claims is not None:
        # Fall back to legacy dict rows (parity path)
        selected_replay_divergence_sections = frozenset(
            str(row.get("section") or "")
            for row in section_claims
            if str(row.get("selected_kind") or "") == "replay_divergence"
            and str(row.get("selected_tier") or "") == "PROVED_REPLAY_BUG"
            and str(row.get("section") or "")
        )
        selected_section_tiers = frozenset(
            ProofTier(str(row.get("selected_tier") or ""))
            for row in section_claims
            if str(row.get("selected_tier") or "")
            and str(row.get("selected_tier") or "") in {t.value for t in ProofTier}
        )
        selected_section_kinds = frozenset(
            str(row.get("selected_kind") or "")
            for row in section_claims
            if str(row.get("selected_kind") or "")
        )
        selected_section_outcomes = tuple(
            (
                str(row.get("section") or ""),
                str(row.get("selected_kind") or ""),
                str(row.get("selected_tier") or ""),
            )
            for row in section_claims
        )

    # HTML topology fields
    noncomm_reason = str(html_topology.get("noncommensurable_reason") or "").strip()
    html_error = str(html_error or html_topology.get("html_error") or "").strip()
    missing_from_xml = [str(v) for v in html_topology.get("missing_from_xml", []) if str(v)]
    extra_in_xml = [str(v) for v in html_topology.get("extra_in_xml", []) if str(v)]

    # Derived convenience flags
    has_section_results = bool(section_results)
    all_sections_match = bool(section_results) and all(
        str(item.get("diagnosis") or "") == "MATCH"
        for item in section_results
    )
    # Whether the section-claims gate should be applied at all (mirrors "if section_claims is not None")
    apply_section_claims_gate = section_claims is not None or typed_section_results is not None

    return StatuteEvidenceContext(
        section_results=tuple(all_seeds),
        stale_sections=tuple(stale_seeds),
        replay_bug_pool=tuple(replay_seeds),
        source_pathologies=tuple(source_pathologies),
        html_noncommensurable_reason=noncomm_reason,
        html_error=html_error,
        missing_from_xml=tuple(missing_from_xml),
        extra_in_xml=tuple(extra_in_xml),
        contingent_effective_sources=tuple(
            str(v) for v in contingent_effective_sources if str(v)
        ),
        corrigendum_support=tuple(corrigendum_support),
        oracle_suspect_detail=str(oracle_suspect_detail or "").strip(),
        oracle_suspect_pending=str(oracle_suspect_pending or ""),
        bisect_index=bisect_index,
        alternative_replay_matches=dict(alternative_replay_matches or {}),
        oracle_range_matches=dict(oracle_range_matches or {}),
        cross_chapter_oracle_matches=dict(cross_chapter_oracle_matches or {}),
        cross_chapter_replay_matches=dict(cross_chapter_replay_matches or {}),
        selected_replay_divergence_sections=selected_replay_divergence_sections,
        selected_section_tiers=selected_section_tiers,
        selected_section_kinds=selected_section_kinds,
        selected_section_outcomes=selected_section_outcomes,
        has_section_results=has_section_results,
        all_sections_match=all_sections_match,
        apply_section_claims_gate=apply_section_claims_gate,
        section_claims_rows=(
            tuple(section_claims) if section_claims is not None else None
        ),
        content_version_drift=content_version_drift,
    )


# ---------------------------------------------------------------------------
# Partition types (spec §3.B)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BucketMatch:
    seed: SectionRollupSeed
    payload: Mapping[str, Any]

    def to_support_dict(self) -> dict[str, Any]:
        base = dict(self.seed.to_dict())
        base.update(self.payload)
        return base


@dataclass(frozen=True)
class PartitionOutcome:
    matched: tuple[BucketMatch, ...]
    remaining: tuple[SectionRollupSeed, ...]


@dataclass(frozen=True)
class StatuteRollupPartition:
    initial_pool: tuple[SectionRollupSeed, ...]
    stale_sections: tuple[BucketMatch, ...]
    preexisting_replay_sections: tuple[BucketMatch, ...]
    unsupported_replay_sections: tuple[BucketMatch, ...]
    payload_supported_replay_sections: tuple[BucketMatch, ...]
    deterministic_sparse_stale_sections: tuple[BucketMatch, ...]
    elaboration_replay_sections: tuple[BucketMatch, ...]
    baseline_same_chapter_drift_sections: tuple[BucketMatch, ...]
    baseline_same_section_structure_drift_sections: tuple[BucketMatch, ...]
    oracle_range_drift_sections: tuple[BucketMatch, ...]
    cross_chapter_oracle_drift_sections: tuple[BucketMatch, ...]
    cross_chapter_replay_drift_sections: tuple[BucketMatch, ...]
    same_chapter_drift_sections: tuple[BucketMatch, ...]
    improved_replay_sections: tuple[BucketMatch, ...]
    residual_replay_bug_sections: tuple[SectionRollupSeed, ...]


# ---------------------------------------------------------------------------
# Partition pipeline (spec §3.B / REPLAY_POOL_PARTITION_RULES order)
# ---------------------------------------------------------------------------


def _partition_preexisting(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    """Partition out sections that are preexisting OR have negligible blame drop."""
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        pre_row = bisect_index.preexisting.lookup(seed)
        neg_row = bisect_index.negligible_preexisting_drop.lookup(seed)
        if pre_row is not None or neg_row is not None:
            matched.append(BucketMatch(seed=seed, payload=dict(pre_row or neg_row or {})))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_unsupported(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.repeal_only_without_payload.lookup(seed)
        if row is not None:
            matched.append(BucketMatch(seed=seed, payload=dict(row)))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_payload_supported(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.payload_prefers_replay.lookup(seed)
        if row is not None:
            matched.append(BucketMatch(seed=seed, payload=dict(row)))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_deterministic_sparse_stale(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    """Sections that are deterministic-sparse-oracle-stale get augmented payloads."""
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.deterministic_sparse_oracle_stale.lookup(seed)
        if row is not None:
            row_dict = dict(row)
            # Augment with derived fields (mirrors lines 996-1051 in _build_proof_claims)
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["first_drop_source"] = row_dict.get("first_drop_source")
            augmented["drop_sources"] = sorted(
                {
                    str(item.get("source_id") or "")
                    for item in list(row_dict.get("worst_drops") or [])
                    if str(item.get("source_id") or "")
                }
            )
            augmented["observation_kinds"] = list(
                row_dict.get("blame_elaboration_kinds") or []
            )
            augmented["apply_helpers"] = list(
                row_dict.get("blame_apply_helpers_for_section") or []
            )
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_elaboration(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.sparse_elaboration.lookup(seed)
        if row is not None:
            matched.append(BucketMatch(seed=seed, payload=dict(row)))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_baseline_same_chapter_drift(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.baseline_same_chapter_drift.lookup(seed)
        if row is not None:
            row_dict = dict(row)
            bm = (row_dict.get("baseline_alternative_replay_match") or {})
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["best_replay_section"] = bm.get("best_replay_section")
            augmented["best_replay_score"] = bm.get("best_replay_score")
            augmented["same_section_score"] = bm.get("same_section_score")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_baseline_same_section_structure_drift(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.baseline_same_section_structure_drift.lookup(seed)
        if row is not None:
            row_dict = dict(row)
            bu = (row_dict.get("baseline_unmatched_oracle_subsections") or {})
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["unmatched_oracle_subsection_count"] = bu.get("count")
            augmented["unmatched_oracle_subsection_excerpts"] = bu.get("oracle_text_excerpts")
            augmented["max_best_replay_score"] = bu.get("max_best_replay_score")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_oracle_range_drift(
    pool: tuple[SectionRollupSeed, ...],
    oracle_range_matches: Mapping[str, dict[str, Any]],
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = dict(oracle_range_matches).get(seed.section)
        if row:
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["oracle_range_section"] = row.get("oracle_range_section")
            augmented["oracle_range_label"] = row.get("oracle_range_label")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_cross_chapter_oracle_drift(
    pool: tuple[SectionRollupSeed, ...],
    cross_chapter_oracle_matches: Mapping[str, dict[str, Any]],
    exact_cross_chapter_oracle_sections: frozenset[str],
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = dict(cross_chapter_oracle_matches).get(seed.section)
        if row:
            if seed.section in exact_cross_chapter_oracle_sections:
                remaining.append(seed)
                continue
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["oracle_section"] = row.get("oracle_section")
            augmented["oracle_section_score"] = row.get("oracle_section_score")
            augmented["same_section_score"] = row.get("same_section_score")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_cross_chapter_replay_drift(
    pool: tuple[SectionRollupSeed, ...],
    cross_chapter_replay_matches: Mapping[str, dict[str, Any]],
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = dict(cross_chapter_replay_matches).get(seed.section)
        if row:
            score = float(row.get("replay_section_score") or 0.0)
            runner_up_score = float(row.get("runner_up_replay_section_score") or 0.0)
            if score >= 0.95 and score >= (runner_up_score + 0.05):
                remaining.append(seed)
                continue
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["replay_section"] = row.get("replay_section")
            augmented["replay_section_score"] = row.get("replay_section_score")
            augmented["same_section_score"] = row.get("same_section_score")
            augmented["runner_up_replay_section"] = row.get("runner_up_replay_section")
            augmented["runner_up_replay_section_score"] = row.get("runner_up_replay_section_score")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_same_chapter_drift(
    pool: tuple[SectionRollupSeed, ...],
    alternative_replay_matches: Mapping[str, dict[str, Any]],
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = dict(alternative_replay_matches).get(seed.section)
        if row:
            augmented: dict[str, Any] = dict(seed.to_dict())
            augmented["best_replay_section"] = row.get("best_replay_section")
            augmented["best_replay_score"] = row.get("best_replay_score")
            augmented["same_section_score"] = row.get("same_section_score")
            matched.append(BucketMatch(seed=seed, payload=augmented))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def _partition_improved(
    pool: tuple[SectionRollupSeed, ...],
    bisect_index: StatuteBisectIndex,
) -> tuple[tuple[BucketMatch, ...], tuple[SectionRollupSeed, ...]]:
    matched: list[BucketMatch] = []
    remaining: list[SectionRollupSeed] = []
    for seed in pool:
        row = bisect_index.improved.lookup(seed)
        if row is not None:
            matched.append(BucketMatch(seed=seed, payload=dict(row)))
        else:
            remaining.append(seed)
    return tuple(matched), tuple(remaining)


def partition_replay_bug_sections(
    ctx: StatuteEvidenceContext,
) -> StatuteRollupPartition:
    """Run all ordered partition rules, accumulating stale/buckets/residual."""
    initial_pool = ctx.replay_bug_pool
    pool = initial_pool
    stale_extra: list[BucketMatch] = []

    # Step 1: preexisting / negligible drop
    preexisting, pool = _partition_preexisting(pool, ctx.bisect_index)

    # Step 2: unsupported (repeal-only)
    unsupported, pool = _partition_unsupported(pool, ctx.bisect_index)

    # Step 3: payload prefers replay
    payload_supported, pool = _partition_payload_supported(pool, ctx.bisect_index)

    # Step 4: deterministic sparse stale → moves into stale_sections
    det_sparse, pool = _partition_deterministic_sparse_stale(pool, ctx.bisect_index)
    stale_extra.extend(det_sparse)

    # Step 5: same-section elaboration ambiguity
    elaboration, pool = _partition_elaboration(pool, ctx.bisect_index)

    # Step 6: baseline same-chapter drift
    baseline_chapter_drift, pool = _partition_baseline_same_chapter_drift(pool, ctx.bisect_index)

    # Step 7: baseline same-section structure drift
    baseline_struct_drift, pool = _partition_baseline_same_section_structure_drift(
        pool, ctx.bisect_index
    )

    # Step 8: oracle range drift
    oracle_range_drift, pool = _partition_oracle_range_drift(pool, ctx.oracle_range_matches)

    exact_cross_chapter_oracle_sections = frozenset(
        section
        for section, selected_kind, selected_tier in ctx.selected_section_outcomes
        if selected_kind == "address_relocation_cross_chapter_exact"
        and selected_tier == ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE.value
        and section
    )

    # Step 9: cross-chapter oracle drift
    cross_chapter_drift, pool = _partition_cross_chapter_oracle_drift(
        pool, ctx.cross_chapter_oracle_matches, exact_cross_chapter_oracle_sections
    )

    # Step 10: cross-chapter replay drift
    cross_chapter_replay_drift, pool = _partition_cross_chapter_replay_drift(
        pool, ctx.cross_chapter_replay_matches
    )

    # Step 11: same-chapter replay drift (alternative_replay_matches)
    same_chapter_drift, pool = _partition_same_chapter_drift(pool, ctx.alternative_replay_matches)

    # Step 12: improved replay sections
    improved, pool = _partition_improved(pool, ctx.bisect_index)

    # Gate residual with typed section claims (spec §4).
    # Mirrors: "if section_claims is not None: replay_bug_sections = [sec for sec in ...
    #   if str(sec["section"]) in selected_replay_sections]"
    # The gate applies even when selected_replay_divergence_sections is empty —
    # which removes ALL residual sections if no section has replay_divergence selected.
    if ctx.apply_section_claims_gate:
        pool = tuple(
            seed for seed in pool
            if seed.section in ctx.selected_replay_divergence_sections
        )

    # Build final stale_sections (initial oracle-incorrect seeds + det_sparse upgrades)
    # Initial stale seeds as BucketMatch (no extra payload)
    initial_stale_matches = tuple(
        BucketMatch(seed=s, payload={}) for s in ctx.stale_sections
    )

    return StatuteRollupPartition(
        initial_pool=initial_pool,
        stale_sections=initial_stale_matches + tuple(stale_extra),
        preexisting_replay_sections=preexisting,
        unsupported_replay_sections=unsupported,
        payload_supported_replay_sections=payload_supported,
        deterministic_sparse_stale_sections=tuple(det_sparse),
        elaboration_replay_sections=elaboration,
        baseline_same_chapter_drift_sections=baseline_chapter_drift,
        baseline_same_section_structure_drift_sections=baseline_struct_drift,
        oracle_range_drift_sections=oracle_range_drift,
        cross_chapter_oracle_drift_sections=cross_chapter_drift,
        cross_chapter_replay_drift_sections=cross_chapter_replay_drift,
        same_chapter_drift_sections=same_chapter_drift,
        improved_replay_sections=improved,
        residual_replay_bug_sections=pool,
    )


# ---------------------------------------------------------------------------
# Direct statute claim emitters (spec §3.A)
# ---------------------------------------------------------------------------


def _make_pos(
    rule_id: str,
    tier: ProofTier,
    kind: str,
    inference_rule: str,
    support: dict[str, Any],
) -> PositiveClaim:
    return PositiveClaim(
        rule_id=rule_id,
        tier=tier,
        kind=kind,
        inference_rule=inference_rule,
        observation_sources=(),
        support=support,
    )


def _make_unresolved(
    rule_id: str,
    kind: str,
    inference_rule: str,
    support: dict[str, Any],
) -> UnresolvedSink:
    return UnresolvedSink(
        rule_id=rule_id,
        kind=kind,
        inference_rule=inference_rule,
        observation_sources=(),
        support=support,
    )


def rule_html_noncommensurable_reason(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    if not ctx.html_noncommensurable_reason:
        return ()
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.HTML_NONCOMM",
                ProofTier.PROVED_HTML_XML_NONCOMMENSURABLE,
                "html_xml_scope_noncommensurable",
                "html_noncommensurable_reason_present",
                {"reason": ctx.html_noncommensurable_reason},
            ),
            summary=(
                "Live Finlex HTML and consolidated XML are not commensurable "
                "for section-topology comparison."
            ),
            trigger_observations=(
                _obs(
                    "html_topology",
                    "noncommensurable_reason",
                    ctx.html_noncommensurable_reason,
                    scope="statute",
                ),
            ),
        ),
    )


def rule_html_fetch_error(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    if not ctx.html_error:
        return ()
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.SINK.HTML_FETCH_ERROR",
                "html_fetch_error",
                "html_fetch_or_parse_failed",
                {"html_error": ctx.html_error},
            ),
            summary=(
                "Live Finlex HTML fetch or parse failed; topology comparison is unavailable."
            ),
            trigger_observations=(
                _obs(
                    "html_topology",
                    "html_error",
                    ctx.html_error,
                    scope="statute",
                ),
            ),
        ),
    )


def rule_oracle_metadata_inconsistency(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Metadata-based heuristic: oracle XML headers disagree with amendment dates.

    This is NOT proof that the oracle content is wrong — only that the XML
    metadata (dateConsolidated, FRBRthis oracle version amendment id) is internally inconsistent.
    Content-based version drift detection is a separate, authoritative check.
    """
    if not ctx.oracle_suspect_detail:
        return ()
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.ORACLE_METADATA_INCONSISTENCY",
                ProofTier.PROVED_ORACLE_INCORRECT,
                "oracle_metadata_inconsistency",
                "oracle_version_mid_conflicts_with_consolidated_cutoff",
                {
                    "suspect_detail": ctx.oracle_suspect_detail,
                    "pending_detail": ctx.oracle_suspect_pending,
                },
            ),
            summary=(
                "The consolidated oracle points to an oracle version amendment id whose "
                "effective or expiry date is inconsistent with the published cutoff. "
                "This is a metadata inconsistency — not necessarily a content error."
            ),
            trigger_observations=(
                _obs(
                    "oracle_version_gate",
                    "suspect_detail",
                    ctx.oracle_suspect_detail,
                    scope="statute",
                ),
            ),
        ),
    )


def rule_source_pathologies(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    if not ctx.source_pathologies:
        return ()
    grouped_codes = sorted(
        {str(item.get("code") or "") for item in ctx.source_pathologies if str(item.get("code") or "")}
    )
    grouped_sources = sorted(
        {
            str(item.get("source_statute") or "")
            for item in ctx.source_pathologies
            if str(item.get("source_statute") or "")
        }
    )
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.SOURCE_PATHOLOGY",
                ProofTier.PROVED_SOURCE_PATHOLOGY,
                "source_pathology",
                "live_source_pathology_detected",
                {
                    "codes": grouped_codes,
                    "source_statutes": grouped_sources,
                    "examples": list(ctx.source_pathologies[:10]),
                },
            ),
            summary="Replay encountered source publication pathologies in the amendment chain.",
            trigger_observations=(
                _obs("source_pathology", "codes", grouped_codes, scope="statute"),
                _obs("source_pathology", "source_statutes", grouped_sources, scope="statute"),
            ),
        ),
    )


def rule_contingent_effective_sources(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    if not ctx.contingent_effective_sources:
        return ()
    sources = sorted({str(v) for v in ctx.contingent_effective_sources if str(v)})[:20]
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.CONTINGENT_EFFECTIVE",
                ProofTier.PROVED_SOURCE_PATHOLOGY,
                "contingent_effective_date",
                "contingent_effective_sources_present",
                {"source_statutes": sources},
            ),
            summary=(
                "Replay detected contingent effective-date dependencies that make "
                "plain consolidated comparison non-commensurable."
            ),
            trigger_observations=(
                _obs(
                    "contingent_effective_date",
                    "source_statutes",
                    sources,
                    scope="statute",
                ),
            ),
        ),
    )


def rule_content_based_version_drift(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Content-based proof that the oracle is behind by K amendments.

    Uses the drift proof from detect_content_version_drift(), which
    demonstrates that replaying with stop_before produces a perfect match.
    This is authoritative — unlike the metadata heuristic, it proves the
    oracle content is stale.
    """
    drift = ctx.content_version_drift
    if not drift:
        return ()
    behind_by = int(drift.get("behind_by", 0))
    if behind_by < 1:
        return ()
    matched_at = str(drift.get("matched_at") or "")
    unapplied = list(drift.get("unapplied") or [])
    scores = dict(drift.get("scores") or {})
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.CONTENT_VERSION_DRIFT",
                ProofTier.PROVED_ORACLE_INCORRECT,
                "oracle_cutoff_version_drift",
                "content_based_stop_before_replay_matches_oracle",
                {
                    "matched_at": matched_at,
                    "behind_by": behind_by,
                    "unapplied": unapplied,
                    "scores": scores,
                    "detection_method": "content_based",
                },
            ),
            summary=(
                f"Oracle content matches replay at amendment {matched_at} "
                f"but not after applying {behind_by} later amendment(s). "
                f"Content-based proof that the oracle is behind."
            ),
            trigger_observations=(
                _obs(
                    "version_drift",
                    "content_based_behind_by",
                    behind_by,
                    scope="statute",
                ),
                _obs(
                    "version_drift",
                    "unapplied_amendments",
                    unapplied,
                    scope="statute",
                ),
            ),
        ),
    )


DIRECT_STATUTE_CLAIM_RULES = (
    rule_html_fetch_error,
    rule_html_noncommensurable_reason,
    rule_content_based_version_drift,
    rule_oracle_metadata_inconsistency,
    rule_source_pathologies,
    rule_contingent_effective_sources,
)


# ---------------------------------------------------------------------------
# Rollup claim emitters (per partition buckets) (spec §3.A ROLLUP_CLAIM_RULES)
# ---------------------------------------------------------------------------


def rule_oracle_support_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    """Emit oracle stale / topology drift claim if stale sections or topology drift exist."""
    # stale_sections from partition includes both initial oracle-incorrect sections
    # and deterministic_sparse_stale sections that were moved into stale
    stale = partition.stale_sections
    missing_from_xml = list(ctx.missing_from_xml)
    extra_in_xml = list(ctx.extra_in_xml)
    corrigenda = [
        item
        for item in ctx.corrigendum_support
        if item.get("official_item_count", 0) > 0 or item.get("manual_override_count", 0) > 0
    ][:20]

    # Build legacy stale sections list: initial oracle-incorrect seeds + det_sparse augments
    # We need to produce the same dict shape as legacy stale_sections
    stale_section_dicts: list[dict[str, Any]] = []
    for bm in stale:
        if bm.payload:
            # det_sparse augmented match — use the payload directly
            stale_section_dicts.append(dict(bm.payload))
        else:
            stale_section_dicts.append(bm.seed.to_dict())

    oracle_support: dict[str, Any] = {
        "html_missing_from_xml": missing_from_xml,
        "html_extra_in_xml": extra_in_xml,
        "html_error": ctx.html_error,
        "sections": stale_section_dicts[:20],
        "corrigenda": corrigenda,
    }
    if missing_from_xml or extra_in_xml:
        if not ctx.html_error:
            kind = "xml_html_topology_drift"
            summary = (
                "Live Finlex HTML and consolidated XML disagree on section topology, "
                "which is evidence of oracle-side XML drift."
            )
            inference_rule = "html_xml_topology_drift_detected"
            trigger_observations: tuple[dict[str, Any], ...] = (
                _obs("html_topology", "missing_from_xml", missing_from_xml, scope="statute"),
                _obs("html_topology", "extra_in_xml", extra_in_xml, scope="statute"),
            )
            return (
                StatuteClaimRecord(
                    claim=_make_pos(
                        "STAT.POS.ORACLE_SUPPORT_ROLLUP",
                        ProofTier.PROVED_ORACLE_INCORRECT,
                        kind,
                        inference_rule,
                        oracle_support,
                    ),
                    summary=summary,
                    trigger_observations=trigger_observations,
                ),
            )
        missing_from_xml = []
        extra_in_xml = []

    if not stale_section_dicts:
        return ()

    kind = "oracle_section_stale"
    summary = (
        "Current evidence shows the consolidated oracle disagrees with replay "
        "for reasons classified as oracle-side stale/editorial state."
    )
    inference_rule = "oracle_stale_sections_detected"
    trigger_observations = (
        _obs(
            "oracle_check",
            "oracle_stale_sections",
            stale_section_dicts[:20],
            scope="sections",
        ),
    )

    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.ORACLE_SUPPORT_ROLLUP",
                ProofTier.PROVED_ORACLE_INCORRECT,
                kind,
                inference_rule,
                oracle_support,
            ),
            summary=summary,
            trigger_observations=trigger_observations,
        ),
    )


def rule_preexisting_replay_sections_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.preexisting_replay_sections:
        return ()
    preexisting_section_support: list[dict[str, Any]] = []
    for bm in partition.preexisting_replay_sections:
        pre_row = ctx.bisect_index.preexisting.lookup(bm.seed)
        inference_rule = "replay_residue_predates_any_amendment_drop"
        if pre_row is None:
            neg_row = ctx.bisect_index.negligible_preexisting_drop.lookup(bm.seed)
            if neg_row is not None:
                pre_row = neg_row
            inference_rule = "material_divergence_predates_blamed_change_and_blame_delta_is_negligible"
        support_entry: dict[str, Any] = dict(bm.seed.to_dict())
        support_entry["baseline_score"] = (pre_row or {}).get("baseline_score")
        support_entry["first_bad_source"] = (pre_row or {}).get("first_bad_source")
        support_entry["blame_before_score"] = (pre_row or {}).get("blame_before_score")
        support_entry["blame_after_score"] = (pre_row or {}).get("blame_after_score")
        blame_before = (pre_row or {}).get("blame_before_score")
        blame_after = (pre_row or {}).get("blame_after_score")
        support_entry["blame_delta"] = (
            (float(blame_before) - float(blame_after))
            if blame_before is not None and blame_after is not None
            else None
        )
        support_entry["inference_rule"] = inference_rule
        preexisting_section_support.append(support_entry)
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.PREEXISTING_REPLAY",
                "UNRESOLVED.preexisting.baseline_residue",
                "material_replay_residue_predates_blamed_change",
                {"sections": preexisting_section_support},
            ),
            summary=(
                "Some replay-labeled residual sections were already materially divergent "
                "before the blamed amendment, or the blamed amendment only caused a negligible "
                "score drop on top of that preexisting divergence."
            ),
            trigger_observations=(
                _obs(
                    "section_bisect",
                    "preexisting_residual_sections",
                    preexisting_section_support,
                    scope="sections",
                ),
            ),
        ),
    )


def rule_improved_replay_sections_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.improved_replay_sections:
        return ()
    trigger_list: list[dict[str, Any]] = []
    support_list: list[dict[str, Any]] = []
    for bm in partition.improved_replay_sections:
        row = ctx.bisect_index.improved.lookup(bm.seed)
        trigger_list.append(
            {
                "section": bm.seed.section,
                "blame_source": (row or {}).get("blame_source"),
                "before_score": (row or {}).get("blame_before_score"),
                "after_score": (row or {}).get("blame_after_score"),
            }
        )
        support_entry = dict(bm.seed.to_dict())
        support_entry["blame_before_score"] = (row or {}).get("blame_before_score")
        support_entry["blame_after_score"] = (row or {}).get("blame_after_score")
        support_list.append(support_entry)
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.IMPROVED_REPLAY",
                "UNRESOLVED.source_underdetermined.amendment_improves_section",
                "blamed_amendment_improves_or_preserves_section_similarity",
                {"sections": support_list},
            ),
            summary=(
                "Some replay-labeled residual sections improve or hold steady across the "
                "blamed amendment, so current evidence does not support attributing those "
                "residuals to replay semantics in that amendment."
            ),
            trigger_observations=(
                _obs(
                    "section_trace",
                    "blame_source_improved_or_equal",
                    trigger_list,
                    scope="sections",
                ),
            ),
        ),
    )


def rule_unsupported_replay_sections_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.unsupported_replay_sections:
        return ()
    trigger_list: list[dict[str, Any]] = []
    support_list: list[dict[str, Any]] = []
    for bm in partition.unsupported_replay_sections:
        row = ctx.bisect_index.repeal_only_without_payload.lookup(bm.seed)
        trigger_list.append(
            {
                "section": bm.seed.section,
                "blame_source": (row or {}).get("blame_source"),
                "compiled_actions": (row or {}).get("blame_compiled_actions_for_section"),
            }
        )
        support_entry = dict(bm.seed.to_dict())
        support_entry["blame_source"] = (row or {}).get("blame_source")
        support_entry["compiled_actions"] = (row or {}).get("blame_compiled_actions_for_section")
        support_list.append(support_entry)
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.UNSUPPORTED_REPLAY",
                ProofTier.PROVED_SOURCE_PATHOLOGY,
                "blamed_source_lacks_payload_support",
                "blamed_amendment_has_only_repeal_support_without_section_payload",
                {"sections": support_list},
            ),
            summary=(
                "Some replay-labeled residual sections are blamed on amendments whose source "
                "XML carries no section payload and compiles only a repeal for that section, "
                "so the source publication does not support attributing the residual to "
                "replay-side replacement semantics."
            ),
            trigger_observations=(
                _obs(
                    "source_payload",
                    "repeal_only_without_payload",
                    trigger_list,
                    scope="sections",
                ),
            ),
        ),
    )


def rule_payload_supported_replay_sections_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.payload_supported_replay_sections:
        return ()
    trigger_list: list[dict[str, Any]] = []
    support_list: list[dict[str, Any]] = []
    for bm in partition.payload_supported_replay_sections:
        row = ctx.bisect_index.payload_prefers_replay.lookup(bm.seed)
        trigger_list.append(
            {
                "section": bm.seed.section,
                "blame_source": (row or {}).get("blame_source"),
                "payload_vs_replay_score": (row or {}).get("blame_payload_vs_replay_score"),
                "payload_vs_oracle_score": (row or {}).get("blame_payload_vs_oracle_score"),
            }
        )
        support_entry = dict(bm.seed.to_dict())
        support_entry["blame_source"] = (row or {}).get("blame_source")
        support_entry["payload_vs_replay_score"] = (row or {}).get("blame_payload_vs_replay_score")
        support_entry["payload_vs_oracle_score"] = (row or {}).get("blame_payload_vs_oracle_score")
        support_list.append(support_entry)
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.PAYLOAD_PREFERS_REPLAY",
                ProofTier.PROVED_SOURCE_PATHOLOGY,
                "blamed_source_payload_prefers_replay",
                "blamed_section_payload_matches_replay_better_than_oracle",
                {"sections": support_list},
            ),
            summary=(
                "Some replay-labeled residual sections are blamed on amendments whose "
                "published section payload matches replay materially better than the oracle, "
                "so current evidence supports source/oracle-side divergence rather than "
                "replay-side replacement semantics."
            ),
            trigger_observations=(
                _obs(
                    "source_payload",
                    "payload_prefers_replay",
                    trigger_list,
                    scope="sections",
                ),
            ),
        ),
    )


def rule_elaboration_replay_sections_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.elaboration_replay_sections:
        return ()
    trigger_list: list[dict[str, Any]] = []
    support_list: list[dict[str, Any]] = []
    for bm in partition.elaboration_replay_sections:
        row = ctx.bisect_index.sparse_elaboration.lookup(bm.seed)
        trigger_list.append(
            {
                "section": bm.seed.section,
                "blame_source": (row or {}).get("blame_source"),
                "observation_kinds": (row or {}).get("blame_elaboration_kinds"),
                "apply_helpers": (row or {}).get("blame_apply_helpers_for_section"),
            }
        )
        support_entry = dict(bm.seed.to_dict())
        support_entry["blame_source"] = (row or {}).get("blame_source")
        support_entry["observation_kinds"] = (row or {}).get("blame_elaboration_kinds")
        support_entry["apply_helpers"] = (row or {}).get("blame_apply_helpers_for_section")
        support_list.append(support_entry)
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.ELABORATION_AMBIGUITY",
                "UNRESOLVED.source_underdetermined.elaboration_ambiguity",
                "blamed_amendment_has_same_section_elaboration_observation",
                {"sections": support_list},
            ),
            summary=(
                "Some replay-labeled residual sections are blamed on amendments whose "
                "compilation already required same-section elaboration, so current "
                "evidence does not yet cleanly separate replay execution fault from "
                "elaboration ambiguity."
            ),
            trigger_observations=(
                _obs(
                    "elaboration",
                    "same_section_sparse_elaboration",
                    trigger_list,
                    scope="sections",
                ),
            ),
        ),
    )


def rule_baseline_same_chapter_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.baseline_same_chapter_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.baseline_same_chapter_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.BASELINE_CHAPTER_DRIFT",
                "UNRESOLVED.address_projection.same_chapter_section_drift",
                "preexisting_same_chapter_replay_section_matches_oracle_better_than_same_number_section",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some replay-labeled residual sections already match a different same-chapter "
                "replay section materially better in the baseline state before the first bad "
                "amendment step, so current evidence points to preexisting chapter-local "
                "numbering drift rather than a blamed replay execution fault."
            ),
            trigger_observations=(
                _obs(
                    "section_bisect",
                    "baseline_alternative_replay_section_match",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_baseline_same_section_structure_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.baseline_same_section_structure_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.baseline_same_section_structure_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.BASELINE_STRUCT_DRIFT",
                "UNRESOLVED.preexisting.same_section_structure_drift",
                "oracle_has_unmatched_same_section_subsection_fragments_before_blamed_amendment",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some replay-labeled residual sections already face unmatched oracle subsection "
                "fragments before the blamed amendment, so current evidence points to preexisting "
                "same-section structural drift rather than a clean blamed replay execution fault."
            ),
            trigger_observations=(
                _obs(
                    "section_bisect",
                    "baseline_unmatched_oracle_subsections",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_oracle_range_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.oracle_range_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.oracle_range_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.ORACLE_RANGE_DRIFT",
                ProofTier.PROVED_ORACLE_INCORRECT,
                "same_chapter_oracle_range_drift",
                "oracle_uses_same_chapter_section_range_instead_of_exact_section_label",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some replay-labeled residual sections map to same-chapter oracle range sections "
                "instead of exact section labels, so current evidence points to oracle-side "
                "section topology drift rather than replay execution fault."
            ),
            trigger_observations=(
                _obs(
                    "oracle_check",
                    "oracle_range_section_match",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_cross_chapter_oracle_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.cross_chapter_oracle_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.cross_chapter_oracle_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.CROSS_CHAPTER_DRIFT",
                "UNRESOLVED.address_projection.cross_chapter_oracle_drift",
                "oracle_matches_same_label_section_in_different_chapter",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some replay-labeled residual sections match a same-label oracle section in a "
                "different chapter materially better than the same-path oracle section, so "
                "current evidence points to cross-chapter path drift rather than a clean "
                "replay execution fault."
            ),
            trigger_observations=(
                _obs(
                    "oracle_check",
                    "cross_chapter_oracle_section_match",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_cross_chapter_replay_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.cross_chapter_replay_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.cross_chapter_replay_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.CROSS_CHAPTER_REPLAY_DRIFT",
                "UNRESOLVED.address_projection.cross_chapter_replay_drift",
                "replay_matches_same_label_section_in_different_chapter_than_oracle",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some oracle-labeled missing sections match a same-label replay section in a "
                "different chapter better than the same-path replay section, but current evidence "
                "does not identify a uniquely dominant replay target, so the result remains an "
                "unresolved cross-chapter replay drift."
            ),
            trigger_observations=(
                _obs(
                    "oracle_check",
                    "cross_chapter_replay_section_match",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_same_chapter_drift_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    if not partition.same_chapter_drift_sections:
        return ()
    sections_dicts = [bm.payload for bm in partition.same_chapter_drift_sections]
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.SAME_CHAPTER_DRIFT",
                "UNRESOLVED.address_projection.same_chapter_replay_drift",
                "same_chapter_replay_section_matches_oracle_better_than_same_number_section",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Some replay-labeled residual sections match a different same-chapter replay "
                "section materially better than the same-number replay section, so current "
                "evidence points to chapter-local section drift rather than a clean replay "
                "execution fault."
            ),
            trigger_observations=(
                _obs(
                    "oracle_check",
                    "alternative_replay_section_match",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


def rule_residual_replay_bug_rollup(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    residual = partition.residual_replay_bug_sections
    if not residual:
        return ()
    sections_dicts = [s.to_dict() for s in residual]
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.RESIDUAL_REPLAY_BUG",
                ProofTier.PROVED_REPLAY_BUG,
                "replay_divergence",
                "residual_replay_bug_diagnoses_present",
                {"sections": sections_dicts[:20]},
            ),
            summary=(
                "Residual divergences remain classified as replay-side bugs after "
                "current oracle/source demotions."
            ),
            trigger_observations=(
                _obs(
                    "oracle_check",
                    "replay_bug_sections",
                    sections_dicts[:20],
                    scope="sections",
                ),
            ),
        ),
    )


ROLLUP_CLAIM_RULES = (
    rule_oracle_support_rollup,
    rule_preexisting_replay_sections_rollup,
    # NOTE: improved is emitted before unsupported/payload — per legacy emit order.
    # (Partition order is different: improved is classified last in the pool pipeline.)
    rule_improved_replay_sections_rollup,
    rule_unsupported_replay_sections_rollup,
    rule_payload_supported_replay_sections_rollup,
    rule_elaboration_replay_sections_rollup,
    rule_baseline_same_chapter_drift_rollup,
    rule_baseline_same_section_structure_drift_rollup,
    rule_oracle_range_drift_rollup,
    rule_cross_chapter_oracle_drift_rollup,
    rule_cross_chapter_replay_drift_rollup,
    rule_same_chapter_drift_rollup,
    rule_residual_replay_bug_rollup,
)


# ---------------------------------------------------------------------------
# Late fallback rules (spec §3.A LATE_FALLBACK_RULES)
# ---------------------------------------------------------------------------


def rule_unanimous_section_claims_oracle_or_noncomm(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Gap 1: All section-level claims resolve to PROVED_ORACLE_INCORRECT or
    PROVED_HTML_XML_NONCOMMENSURABLE.  Promote to dominant section tier.
    """
    if not ctx.selected_section_outcomes:
        return ()
    _sc_tiers = {
        selected_tier
        for _, _, selected_tier in ctx.selected_section_outcomes
        if selected_tier
    }
    if not _sc_tiers:
        return ()
    if not (_sc_tiers <= {"PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE"}):
        return ()
    _dominant = (
        "PROVED_ORACLE_INCORRECT"
        if "PROVED_ORACLE_INCORRECT" in _sc_tiers
        else "PROVED_HTML_XML_NONCOMMENSURABLE"
    )
    _sc_kinds = sorted(
        {selected_kind for _, selected_kind, _ in ctx.selected_section_outcomes if selected_kind}
    )
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.UNANIMOUS_SECTION_ORACLE",
                ProofTier(_dominant),
                "section_claims_unanimously_oracle_incorrect",
                "all_section_claims_resolve_to_oracle_or_noncommensurable",
                {
                    "section_tiers": sorted(_sc_tiers),
                    "section_claim_kinds": _sc_kinds,
                    "section_count": len(ctx.selected_section_outcomes),
                },
            ),
            summary=(
                "All section-level claims resolve to oracle-incorrect or noncommensurable. "
                "Statute-level rollup promotes to the dominant section tier."
            ),
            trigger_observations=(
                _obs(
                    "section_claims",
                    "unanimous_section_tiers",
                    sorted(_sc_tiers),
                    scope="statute",
                ),
            ),
        ),
    )


def rule_oracle_body_empty_with_proved_sections(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Gap 4: Mixed proved + empty-unverified sections → PROVED_ORACLE_INCORRECT."""
    if not ctx.selected_section_outcomes:
        return ()
    _sc_tiers_g4 = {
        selected_tier
        for _, _, selected_tier in ctx.selected_section_outcomes
        if selected_tier
    }
    _unresolved_kinds_g4 = {
        selected_kind
        for _, selected_kind, selected_tier in ctx.selected_section_outcomes
        if selected_tier == "UNRESOLVED" and selected_kind
    }
    _has_proved_g4 = bool(
        _sc_tiers_g4 & {"PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE"}
    )
    _only_empty_unverified_g4 = (
        _unresolved_kinds_g4
        <= {"UNRESOLVED.source_underdetermined.oracle_text_empty_unverified"}
    )
    if not (_has_proved_g4 and _only_empty_unverified_g4 and _unresolved_kinds_g4):
        return ()
    _proved_count = sum(
        1
        for _, _, selected_tier in ctx.selected_section_outcomes
        if selected_tier in {"PROVED_ORACLE_INCORRECT", "PROVED_HTML_XML_NONCOMMENSURABLE"}
    )
    _unresolved_count = sum(
        1
        for _, _, selected_tier in ctx.selected_section_outcomes
        if selected_tier == "UNRESOLVED"
    )
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.ORACLE_BODY_EMPTY_PROVED",
                ProofTier.PROVED_ORACLE_INCORRECT,
                "oracle_body_empty_with_proved_sections",
                "mixed_proved_and_empty_unverified_sections_"
                "promote_when_all_unresolved_are_empty_oracle",
                {
                    "proved_section_count": _proved_count,
                    "empty_unverified_section_count": _unresolved_count,
                    "total_section_count": len(ctx.selected_section_outcomes),
                },
            ),
            summary=(
                "Oracle has empty body for sections that replay produces. "
                "All UNRESOLVED sections are oracle_text_empty_unverified "
                "and at least one section is independently proved oracle-incorrect."
            ),
            trigger_observations=(
                _obs(
                    "section_claims",
                    "empty_unverified_only_unresolved",
                    True,
                    scope="statute",
                ),
            ),
        ),
    )


def rule_all_sections_match(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Gap 3a: All section-level comparisons match (perfect score or non-section diff)."""
    if not ctx.all_sections_match:
        return ()
    return (
        StatuteClaimRecord(
            claim=_make_pos(
                "STAT.POS.ALL_SECTIONS_MATCH",
                ProofTier.PROVED_ORACLE_INCORRECT,
                "compilation_sections_correct",
                "all_sections_match_therefore_compilation_correct",
                {
                    "section_count": len(ctx.section_results),
                    "all_match": True,
                },
            ),
            summary=(
                "All section-level comparisons match. Any remaining statute-level "
                "divergence is in non-section content (johtolause, liite, voimaantulo)."
            ),
            trigger_observations=(
                _obs("oracle_check", "all_sections_match", True, scope="statute"),
            ),
        ),
    )


def rule_trivially_empty(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    """Gap 3b: No section results at all."""
    if ctx.has_section_results:
        return ()
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.TRIVIALLY_EMPTY",
                "trivially_empty",
                "no_section_results_available",
                {"section_count": 0},
            ),
            summary=(
                "Neither replay nor oracle produced section content. "
                "No comparison is possible."
            ),
            trigger_observations=(),
        ),
    )


LATE_FALLBACK_RULES = (
    rule_unanimous_section_claims_oracle_or_noncomm,
    rule_oracle_body_empty_with_proved_sections,
    rule_all_sections_match,
    rule_trivially_empty,
)


def rule_no_strong_claim(
    ctx: StatuteEvidenceContext,
) -> tuple[StatuteClaimRecord, ...]:
    return (
        StatuteClaimRecord(
            claim=_make_unresolved(
                "STAT.UNRESOLVED.NO_STRONG_CLAIM",
                "no_strong_claim",
                "no_claim_trigger_matched",
                {},
            ),
            summary=(
                "No strong replay/oracle/source proof claim was derived from the "
                "current statute evidence."
            ),
            trigger_observations=(),
        ),
    )


FINAL_FALLBACK_RULE = rule_no_strong_claim


# ---------------------------------------------------------------------------
# Claim emitter (spec §3)
# ---------------------------------------------------------------------------


def emit_statute_claims(
    ctx: StatuteEvidenceContext,
    partition: StatuteRollupPartition,
) -> tuple[StatuteClaimRecord, ...]:
    """Emit all statute-level claims in legacy order.

    Order mirrors _build_proof_claims():
      1. Direct statute facts (html_noncomm, oracle_cutoff, source_pathologies, contingent_eff)
      2. Oracle/stale aggregate + bucket rollup claims
      3. Late fallback claims (Gap 1/4/3a/3b)
      4. Final fallback (no_strong_claim)
    """
    claims: list[StatuteClaimRecord] = []

    # 1. Direct statute claim rules
    for rule in DIRECT_STATUTE_CLAIM_RULES:
        claims.extend(rule(ctx))

    # 2. Rollup claim rules (oracle aggregate + all buckets)
    for rule in ROLLUP_CLAIM_RULES:
        claims.extend(rule(ctx, partition))

    if claims:
        return tuple(claims)

    # 3. Late fallback rules (only if no claims yet)
    for rule in LATE_FALLBACK_RULES:
        results = rule(ctx)
        if results:
            claims.extend(results)
            return tuple(claims)

    # 4. Final fallback
    claims.extend(FINAL_FALLBACK_RULE(ctx))
    return tuple(claims)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_proof_claims_typed(
    *,
    section_results: List[Dict[str, Any]],
    source_pathologies: List[Dict[str, Any]],
    html_topology: Dict[str, Any],
    contingent_effective_sources: List[str],
    corrigendum_support: List[Dict[str, Any]],
    oracle_suspect_detail: str = "",
    oracle_suspect_pending: str = "",
    section_bisect: Optional[List[Dict[str, Any]]] = None,
    alternative_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    oracle_range_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Dict[str, Dict[str, Any]]] = None,
    section_claims: Optional[List[Dict[str, Any]]] = None,
    typed_section_results: Optional[Any] = None,
    content_version_drift: Optional[Dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Typed statute-level proof claim builder — produces identical output to legacy.

    Builds a StatuteEvidenceContext, runs the partition engine, emits typed
    StatuteClaimRecord objects, then serialises back to legacy dicts via
    to_legacy_dict().

    Drop-in replacement for _build_proof_claims().  The returned list of dicts
    is bit-identical to the legacy path.
    """
    ctx = build_statute_context(
        section_results=section_results,
        source_pathologies=source_pathologies,
        html_topology=html_topology,
        contingent_effective_sources=contingent_effective_sources,
        corrigendum_support=corrigendum_support,
        oracle_suspect_detail=oracle_suspect_detail,
        oracle_suspect_pending=oracle_suspect_pending,
        section_bisect=section_bisect,
        alternative_replay_matches=alternative_replay_matches,
        oracle_range_matches=oracle_range_matches,
        cross_chapter_oracle_matches=cross_chapter_oracle_matches,
        cross_chapter_replay_matches=cross_chapter_replay_matches,
        section_claims=section_claims,
        typed_section_results=typed_section_results,
        content_version_drift=content_version_drift,
    )
    partition = partition_replay_bug_sections(ctx)
    resolved = StatuteResolvedClaims(
        claims=emit_statute_claims(ctx, partition),
    )
    return resolved.to_legacy_claims()
