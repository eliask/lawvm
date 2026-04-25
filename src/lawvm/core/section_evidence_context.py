"""Section-level evidence context for claim construction.

Bundles all section-local facts used by section claim construction into one
typed object per section so callers do not shuttle large ad hoc lookup bags.

API tier
--------
Internal evidence/compiler helper.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, TypedDict, cast

from lawvm.core.chain_completeness import ChainCompletenessStatus
from lawvm.core.compile_result import SectionStrictVerdict
from lawvm.core.evidence_support import (
    ORACLE_INCORRECT_DIAGNOSES,
    REPLAY_BUG_DIAGNOSES,
    has_negligible_blame_drop_on_preexisting_residue,
    section_similarity,
)


class AlternativeReplayMatch(TypedDict, total=False):
    best_replay_section: str
    same_section_score: float


class OracleRangeMatch(TypedDict):
    oracle_range_section: str
    oracle_range_label: str


class CrossChapterOracleMatch(TypedDict):
    oracle_section: str
    oracle_section_score: float
    same_section_score: float


class CrossChapterReplayMatch(TypedDict, total=False):
    replay_section: str
    replay_section_score: float
    same_section_score: float
    runner_up_replay_section: str
    runner_up_replay_section_score: float


@dataclass
class SectionEvidenceContext:
    """All section-local facts bundled for evidence claim construction.

    The factory function `build_section_contexts()` populates these from the
    same raw inputs used by section claim construction.
    """

    # --- Core identity (from section_results item) ---
    section_label: str
    diagnosis: str = ""
    blame_source: str = ""
    similarity: float = 0.0

    # Raw texts for downstream similarity / content checks
    oracle_text: str = ""
    replay_text: str = ""
    oracle_content_absent: bool = False

    # --- From section_bisect ---
    bisect_support: Mapping[str, Any] = field(default_factory=dict)

    # --- From section_strict_verdicts (C1) ---
    strict_verdict: Optional[SectionStrictVerdict] = None
    strict_payload_confidence: str = "unknown"

    # --- From section_invariant_violations (C3) ---
    invariant_violations: List[Dict[str, Any]] = field(default_factory=list)

    # --- Oracle / replay match lookups ---
    alternative_replay_match: Optional[AlternativeReplayMatch] = None
    oracle_range_match: Optional[OracleRangeMatch] = None
    cross_chapter_oracle_match: Optional[CrossChapterOracleMatch] = None
    cross_chapter_replay_match: Optional[CrossChapterReplayMatch] = None

    # --- Timeline ---
    has_timeline_entry: Optional[bool] = None

    # --- HTML topology (statute-wide, but relevant per section) ---
    html_noncommensurable_reason: str = ""

    # --- Strict fail reasons (statute-wide extraction gap) ---
    has_extraction_gap: bool = False

    # --- Oracle suspect (statute-wide, but relevant per section) ---
    oracle_suspect_detail: str = ""

    # --- Strict fail reasons (raw, for defeater support payloads) ---
    strict_fail_reasons: List[str] = field(default_factory=list)

    # --- Chain completeness (attack #9 guard for negative proofs) ---
    chain_completeness: Optional[ChainCompletenessStatus] = None

    # ------------------------------------------------------------------
    # Derived booleans (A1 proof algebra)
    # ------------------------------------------------------------------

    @property
    def is_oracle_incorrect_diagnosis(self) -> bool:
        """True when diagnosis is in the oracle-incorrect family."""
        return self.diagnosis in ORACLE_INCORRECT_DIAGNOSES

    @property
    def is_replay_bug_diagnosis(self) -> bool:
        """True when diagnosis is in the replay-bug family."""
        return self.diagnosis in REPLAY_BUG_DIAGNOSES

    @property
    def has_preexisting_residue_support(self) -> bool:
        """True when bisect says divergence predates all amendments."""
        return bool(self.bisect_support.get("preexisting_before_any_drop"))

    @property
    def has_negligible_preexisting_drop_support(self) -> bool:
        """True when bisect says blame delta is negligible on preexisting residue."""
        return has_negligible_blame_drop_on_preexisting_residue(self.bisect_support)

    @property
    def has_source_barrier(self) -> bool:
        """True when section strict verdict has source/extraction barriers."""
        if self.strict_verdict is None:
            return False
        if self.strict_verdict.is_strict_clean:
            return False
        families = self.strict_verdict.barrier_families
        return bool(families & {"source", "extraction"})

    @property
    def has_recovery_barrier(self) -> bool:
        """True when section strict verdict has recovery-family barriers."""
        if self.strict_verdict is None:
            return False
        if self.strict_verdict.is_strict_clean:
            return False
        families = self.strict_verdict.barrier_families
        return bool(families & {"recovery", "resolution", "temporal", "text_level"})

    @property
    def baseline_alternative_match(self) -> Mapping[str, Any]:
        """Baseline alternative replay match from bisect support."""
        val = self.bisect_support.get("baseline_alternative_replay_match")
        if isinstance(val, dict):
            return val
        return {}

    @property
    def baseline_same_section_structure_drift(self) -> Mapping[str, Any]:
        """Baseline unmatched oracle subsections from bisect support."""
        val = self.bisect_support.get("baseline_unmatched_oracle_subsections")
        if isinstance(val, dict):
            return val
        return {}

    @property
    def negligible_blame_drop_on_preexisting_residue(self) -> bool:
        """Alias for has_negligible_preexisting_drop_support."""
        return self.has_negligible_preexisting_drop_support

    @property
    def has_complete_chain(self) -> bool:
        """True when chain completeness is verified and complete.

        Unknown chain state must not satisfy guards used for negative-proof
        promotion. This property therefore fails closed: it returns True only
        when chain completeness was computed and is explicitly complete.
        """
        if self.chain_completeness is None:
            return False
        return self.chain_completeness.is_complete

    @property
    def chain_incomplete_reasons(self) -> list[str]:
        """Compatibility projection of chain incompleteness reasons."""
        if self.chain_completeness is None:
            return []
        return self.chain_completeness.incompleteness_reasons

    @property
    def strict_status(self) -> str:
        """Strict verdict status or an empty value when unavailable."""
        if self.strict_verdict is None:
            return ""
        return self.strict_verdict.status

    @property
    def strict_amendment_id(self) -> str:
        """Strict verdict amendment id or an empty value when unavailable."""
        if self.strict_verdict is None:
            return ""
        return self.strict_verdict.amendment_id

    @property
    def strict_barrier_kinds(self) -> tuple[str, ...]:
        """Sorted barrier kinds from the section strict verdict."""
        if self.strict_verdict is None:
            return ()
        return tuple(sorted(self.strict_verdict.barrier_kinds))

    @property
    def strict_barrier_families(self) -> tuple[str, ...]:
        """Sorted barrier families from the section strict verdict."""
        if self.strict_verdict is None:
            return ()
        return tuple(sorted(self.strict_verdict.barrier_families))


def _scoped_html_noncommensurable_reason(
    section: str,
    html_topology: Optional[Dict[str, Any]],
) -> str:
    """Project a statute-wide HTML noncommensurability reason to one section.

    Only duplicate unscoped oracle-label reasons currently carry section-local
    payloads. Statute-level reporting should continue to use raw html_topology.
    """
    reason = str((html_topology or {}).get("noncommensurable_reason") or "")
    prefix = "duplicate_unscoped_oracle_labels:"
    if not reason.startswith(prefix):
        return ""
    detail = reason.removeprefix(prefix)
    canonical = section if section.startswith("section:") else f"section:{section}"
    labels = {part.strip() for part in detail.split(",") if part.strip()}
    if canonical in labels:
        return reason
    return ""


def _compute_strict_payload_confidence(
    section_label: str,
    section_strict_verdicts: Optional[Dict[str, SectionStrictVerdict]],
) -> str:
    """Derive payload confidence from a section-local strict verdict.

    Mirrors the B4 logic in _build_section_claims().
    """
    if not section_strict_verdicts:
        return "unknown"
    ssv = section_strict_verdicts.get(section_label)
    if ssv is None:
        return "unknown"
    status = ssv.status
    if status == "strict_clean":
        return "strict_clean"
    if status == "source_incomplete":
        return "source_incomplete"
    if status == "strict_blocked_by_recovery":
        return "recovery_dependent"
    return "degraded"


def _has_timeline(
    section_label: str,
    timeline_addresses: Optional[set[str]],
) -> Optional[bool]:
    """Check whether a section label appears in the timeline address set.

    Returns None when timeline_addresses is None (data not available),
    True/False otherwise.  Mirrors the canonical-suffix matching in
    _build_section_claims().
    """
    if timeline_addresses is None:
        return None
    suffix = f"section:{section_label}"
    return any(
        addr == suffix or addr.endswith(f"/{suffix}")
        for addr in timeline_addresses
    )


def build_section_contexts(
    *,
    section_results: List[Dict],
    section_bisect: Optional[List[Dict]] = None,
    alternative_replay_matches: Optional[Mapping[str, Mapping[str, Any]]] = None,
    oracle_range_matches: Optional[Mapping[str, Mapping[str, Any]]] = None,
    cross_chapter_oracle_matches: Optional[Mapping[str, Mapping[str, Any]]] = None,
    cross_chapter_replay_matches: Optional[Mapping[str, Mapping[str, Any]]] = None,
    html_topology: Optional[Dict[str, Any]] = None,
    strict_fail_reasons: Optional[List[str]] = None,
    timeline_addresses: Optional[set[str]] = None,
    oracle_suspect_detail: str = "",
    section_strict_verdicts: Optional[Dict[str, SectionStrictVerdict]] = None,
    section_invariant_violations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    chain_completeness_by_section: Optional[Dict[str, ChainCompletenessStatus]] = None,
) -> Dict[str, SectionEvidenceContext]:
    """Build per-section evidence contexts from raw inputs.

    The signature mirrors _build_section_claims() exactly so that A1 can
    later do a mechanical swap: call build_section_contexts() first, then
    iterate the contexts instead of repeating per-section lookups.

    Returns a dict keyed by section label.
    """
    # Pre-build bisect lookup (same as _build_section_claims line 80-84)
    support_by_section: Dict[str, Dict] = {
        str(item.get("section") or ""): item
        for item in (section_bisect or [])
        if str(item.get("section") or "")
    }

    # Statute-wide: extraction gap
    sfr = set(strict_fail_reasons or [])
    extraction_gap = "PARSE.EXTRACTION_FALLBACK" in sfr

    contexts: Dict[str, SectionEvidenceContext] = {}
    for item in section_results:
        section = str(item.get("section") or "")
        if not section:
            continue
        diagnosis = str(item.get("diagnosis") or "")
        blame_source = str(item.get("blame_source") or "")
        oracle_text = str(item.get("oracle_text") or "")
        replay_text = str(item.get("replay_text") or "")
        # Use pre-computed similarity from upstream (build_evidence_bundle)
        # when available, avoiding redundant O(n*m) Levenshtein computation.
        _precomputed = item.get("similarity")
        if _precomputed is not None:
            similarity = round(float(_precomputed), 6)
        else:
            similarity = round(
                section_similarity(replay_text, oracle_text),
                6,
            )

        # Per-section lookups
        support = dict(support_by_section.get(section) or {})
        alt_match = (alternative_replay_matches or {}).get(section) or None
        range_match = (oracle_range_matches or {}).get(section) or None
        cc_match = (cross_chapter_oracle_matches or {}).get(section) or None
        cr_match = (cross_chapter_replay_matches or {}).get(section) or None

        # Strict verdict + confidence
        ssv = section_strict_verdicts.get(section) if section_strict_verdicts else None
        payload_confidence = _compute_strict_payload_confidence(
            section, section_strict_verdicts,
        )

        # Invariant violations
        inv_violations = (
            list((section_invariant_violations or {}).get(section) or [])
        )

        # Timeline
        tl_entry = _has_timeline(section, timeline_addresses)

        chain_cc = (
            (chain_completeness_by_section or {}).get(section) or None
        )
        contexts[section] = SectionEvidenceContext(
            section_label=section,
            diagnosis=diagnosis,
            blame_source=blame_source,
            similarity=similarity,
            oracle_text=oracle_text,
            replay_text=replay_text,
            oracle_content_absent=bool(item.get("oracle_content_absent")),
            bisect_support=support,
            strict_verdict=ssv,
            strict_payload_confidence=payload_confidence,
            invariant_violations=inv_violations,
            alternative_replay_match=cast(AlternativeReplayMatch | None, alt_match if alt_match else None),
            oracle_range_match=cast(OracleRangeMatch | None, range_match if range_match else None),
            cross_chapter_oracle_match=cast(CrossChapterOracleMatch | None, cc_match if cc_match else None),
            cross_chapter_replay_match=cast(CrossChapterReplayMatch | None, cr_match if cr_match else None),
            has_timeline_entry=tl_entry,
            html_noncommensurable_reason=_scoped_html_noncommensurable_reason(
                section, html_topology
            ),
            has_extraction_gap=extraction_gap,
            oracle_suspect_detail=oracle_suspect_detail,
            strict_fail_reasons=list(strict_fail_reasons or []),
            chain_completeness=chain_cc,
        )

    return contexts
