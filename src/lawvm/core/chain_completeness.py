"""Section-chain completeness certificate.

A precondition for negative evidence proofs. Before concluding
"no amendment touched this section, therefore oracle is editorial,"
we must verify the amendment chain is actually complete for that section.

Attack #9 from adversarial review: missing compiler input can
masquerade as oracle drift without chain completeness.

Usage
-----
``compute_chain_completeness()`` takes compile-level artifacts and returns
a dict mapping section_label -> ChainCompletenessStatus. Each status
records whether the amendment chain for that section is complete or, if
not, the specific incompleteness reasons.

Evidence rules that rely on negative proofs (e.g. "no blame + no timeline
=> oracle editorial drift") MUST check chain completeness before emitting
PROVED_ORACLE_INCORRECT. If the chain is incomplete, the rule must
downgrade to UNRESOLVED.

API tier
--------
Stable proof/certificate surface for section-level completeness. Older
per-reason buckets remain as explicit read-model projections; blocker records
are the authoritative model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, cast

from lawvm.core.target_scope import matching_sections_for_scope, resolve_internal_target_scope

SOURCE_INCOMPLETE_BLOCKER = "APPLY.SOURCE_INCOMPLETE"
EXTRACTION_FALLBACK_BLOCKER = "PARSE.EXTRACTION_FALLBACK"
FAILED_OPERATION_BLOCKER = "APPLY.FAILED_OPERATION"
REJECTED_OPERATION_BLOCKER = "ELAB.STRICT_REJECTED_OPERATION"
BOUNDARY_VIOLATION_BLOCKERS = (
    "REPLAY_SKIPPED_OP_MUTATED_TREE",
    "REPLAY_FAILED_OP_MUTATED_TREE",
    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
)
MISSING_EFFECTIVE_DATE_BLOCKER = "TIME.MISSING_EFFECTIVE_DATE"

CompletenessBlockerKind = Literal[
    "APPLY.SOURCE_INCOMPLETE",
    "PARSE.EXTRACTION_FALLBACK",
    "APPLY.FAILED_OPERATION",
    "ELAB.STRICT_REJECTED_OPERATION",
    "REPLAY_SKIPPED_OP_MUTATED_TREE",
    "REPLAY_FAILED_OP_MUTATED_TREE",
    "REPLAY_MISSING_PRIMARY_TARGET_CONSUMPTION",
    "REPLAY_APPLY_BOUNDARY_UNRESOLVED",
    "REPLAY_APPLY_BOUNDARY_TOUCH_OUTSIDE_TARGET",
    "TIME.MISSING_EFFECTIVE_DATE",
]
_ALL_BLOCKER_KINDS = frozenset(
    (
        SOURCE_INCOMPLETE_BLOCKER,
        EXTRACTION_FALLBACK_BLOCKER,
        FAILED_OPERATION_BLOCKER,
        REJECTED_OPERATION_BLOCKER,
        *BOUNDARY_VIOLATION_BLOCKERS,
        MISSING_EFFECTIVE_DATE_BLOCKER,
    )
)


@dataclass(frozen=True)
class CompletenessBlocker:
    """A scoped reason why negative proof is unsafe for a section."""

    kind: CompletenessBlockerKind
    scope_kind: str
    scope_ref: str
    source_statute: str = ""

    def __post_init__(self) -> None:
        if self.kind not in _ALL_BLOCKER_KINDS:
            raise ValueError("CompletenessBlocker.kind is not supported")
        for field_name, value in (
            ("scope_kind", self.scope_kind),
            ("scope_ref", self.scope_ref),
        ):
            if not isinstance(value, str) or not value:
                raise ValueError(f"CompletenessBlocker.{field_name} must be a non-empty string")
        if not isinstance(self.source_statute, str):
            raise TypeError("CompletenessBlocker.source_statute must be a string")


@dataclass(frozen=True, init=False)
class ChainCompletenessStatus:
    """Per-section chain completeness assessment.

    The authoritative completeness state is the scoped ``blockers`` ledger.
    Derivative summary reasons remain available, but the old per-reason
    source-list constructor inputs and accessors are gone.
    """

    section_label: str
    is_complete: bool
    blockers: tuple[CompletenessBlocker, ...] = field(default_factory=tuple)

    def __init__(
        self,
        *,
        section_label: str,
        is_complete: bool,
        blockers: list[CompletenessBlocker] | tuple[CompletenessBlocker, ...] | None = None,
    ) -> None:
        if not isinstance(section_label, str) or not section_label:
            raise ValueError("ChainCompletenessStatus.section_label must be a non-empty string")
        if not isinstance(is_complete, bool):
            raise TypeError("ChainCompletenessStatus.is_complete must be a bool")
        normalized_blockers = tuple(blockers or ())
        if any(not isinstance(blocker, CompletenessBlocker) for blocker in normalized_blockers):
            raise TypeError("ChainCompletenessStatus.blockers must contain CompletenessBlocker")
        object.__setattr__(self, "section_label", section_label)
        object.__setattr__(self, "is_complete", is_complete)
        object.__setattr__(self, "blockers", normalized_blockers)
        if bool(self.blockers) == bool(self.is_complete):
            raise ValueError(
                "ChainCompletenessStatus.is_complete contradicts the blocker ledger: "
                f"is_complete={self.is_complete!r}, blockers={len(self.blockers)!r}"
            )

    @property
    def incompleteness_reasons(self) -> list[str]:
        """Human-readable summary of why the chain is incomplete."""
        reasons: list[str] = []
        source_incomplete_count = sum(1 for blocker in self.blockers if blocker.kind == SOURCE_INCOMPLETE_BLOCKER)
        extraction_fallback_count = sum(1 for blocker in self.blockers if blocker.kind == EXTRACTION_FALLBACK_BLOCKER)
        failed_op_count = sum(1 for blocker in self.blockers if blocker.kind == FAILED_OPERATION_BLOCKER)
        rejected_op_count = sum(1 for blocker in self.blockers if blocker.kind == REJECTED_OPERATION_BLOCKER)
        boundary_violation_counts = {
            kind: sum(1 for blocker in self.blockers if blocker.kind == kind)
            for kind in BOUNDARY_VIOLATION_BLOCKERS
        }
        unresolved_date_count = sum(1 for blocker in self.blockers if blocker.kind == MISSING_EFFECTIVE_DATE_BLOCKER)
        if source_incomplete_count:
            reasons.append(f"{SOURCE_INCOMPLETE_BLOCKER}:{source_incomplete_count}")
        if extraction_fallback_count:
            reasons.append(f"{EXTRACTION_FALLBACK_BLOCKER}:{extraction_fallback_count}")
        if failed_op_count:
            reasons.append(f"{FAILED_OPERATION_BLOCKER}:{failed_op_count}")
        if rejected_op_count:
            reasons.append(f"{REJECTED_OPERATION_BLOCKER}:{rejected_op_count}")
        for kind, count in boundary_violation_counts.items():
            if count:
                reasons.append(f"{kind}:{count}")
        if unresolved_date_count:
            reasons.append(f"{MISSING_EFFECTIVE_DATE_BLOCKER}:{unresolved_date_count}")
        return reasons


def _sources_to_blockers(
    *,
    kind: CompletenessBlockerKind,
    section_label: str,
    sources: list[str],
) -> list[CompletenessBlocker]:
    blockers: list[CompletenessBlocker] = []
    for source in sources:
        source_text = str(source or "").strip()
        if not source_text:
            continue
        if source_text == "statute_wide":
            blockers.append(
                CompletenessBlocker(
                    kind=kind,
                    scope_kind="statute",
                    scope_ref="statute_wide",
                )
            )
            continue
        blockers.append(
            CompletenessBlocker(
                kind=kind,
                scope_kind="section",
                scope_ref=section_label,
                source_statute=source_text,
            )
        )
    return blockers


def _matching_sections_for_row(
    *,
    row: dict[str, Any],
    section_labels: list[str],
) -> list[str]:
    """Return section labels touched by one compiled/failed-op style row.

    Supports:
    - direct section labels already in section-key format
    - chapter-qualified section rows (`target_chapter` + `target_norm`/`target_section`)
    - neutral scope rows (`target_unit_kind`, `target_norm`, `target_chapter`)
    """
    return matching_sections_for_scope(
        scope=resolve_internal_target_scope(row),
        section_labels=section_labels,
    )


def compute_chain_completeness(
    *,
    section_labels: list[str],
    strict_fail_reasons: list[str],
    failed_ops: list[dict[str, Any]],
    compiled_ops: list[dict[str, Any]],
) -> dict[str, ChainCompletenessStatus]:
    """Compute per-section chain completeness from compile artifacts.

    For each section, checks whether any amendment targeting it had:
    - Source not available (APPLY.SOURCE_INCOMPLETE in strict_fail_reasons)
    - Extraction fallback (PARSE.EXTRACTION_FALLBACK in strict_fail_reasons)
    - Failed op (from failed_ops list, matching target_section)
    - Missing effective date (TIME.MISSING_EFFECTIVE_DATE in strict_fail_reasons)

    Returns a dict mapping section_label -> ChainCompletenessStatus.

    Parameters
    ----------
    section_labels
        The sections to assess.
    strict_fail_reasons
        Statute-wide strict fail reasons from a top-level compile/facade path.
    failed_ops
        Failed operation dicts. Expected key: "target_section".
        May also contain "source_statute".
    compiled_ops
        Compiled operation dicts. Expected keys: "target_section" and
        typed provenance carriers such as "extraction_provenance_tags".
        May also contain "source_statute".
    """
    sfr_set = set(strict_fail_reasons)

    # Statute-wide signals
    has_source_incomplete = SOURCE_INCOMPLETE_BLOCKER in sfr_set
    has_extraction_fallback = EXTRACTION_FALLBACK_BLOCKER in sfr_set
    has_rejected_operation = REJECTED_OPERATION_BLOCKER in sfr_set
    boundary_violations = tuple(kind for kind in BOUNDARY_VIOLATION_BLOCKERS if kind in sfr_set)
    has_missing_date = MISSING_EFFECTIVE_DATE_BLOCKER in sfr_set

    # Per-section: index failed_ops by touched sections
    failed_by_section: dict[str, list[str]] = {}
    for fop in failed_ops:
        touched_sections = _matching_sections_for_row(
            row=fop,
            section_labels=section_labels,
        )
        if touched_sections:
            src = str(fop.get("source_statute") or "unknown")
            for ts in touched_sections:
                failed_by_section.setdefault(ts, []).append(src)

    # Per-section: index all touched amendment sources from compiled/failed ops.
    # This lets statute-wide gaps poison only sections that were actually in the
    # amendment traffic we saw, instead of pessimistically marking every section
    # incomplete. If we have no touch map at all, we still fall back to the old
    # statute-wide behavior for safety.
    touched_sources_by_section: dict[str, list[str]] = {}
    for cop in compiled_ops:
        src = str(cop.get("source_statute") or "unknown")
        for ts in _matching_sections_for_row(row=cop, section_labels=section_labels):
            bucket = touched_sources_by_section.setdefault(ts, [])
            if src not in bucket:
                bucket.append(src)
    for ts, sources in failed_by_section.items():
        bucket = touched_sources_by_section.setdefault(ts, [])
        for src in sources:
            if src not in bucket:
                bucket.append(src)
    has_any_section_touch_map = bool(touched_sources_by_section)
    # Per-section: check compiled_ops for typed extraction fallback provenance.
    extraction_fallback_tags = {
        "extraction_fallback_heuristic",
        "extraction_title_fallback",
        "extraction_sec1_body_johto",
        "repeal_reenact_normalized",
        "fallback_insert_supplement",
        "root_insert_supplement",
    }
    extraction_fallback_by_section: dict[str, list[str]] = {}
    for cop in compiled_ops:
        extraction_tags = cop.get("extraction_provenance_tags")
        tags: set[str] = set()
        if isinstance(extraction_tags, list):
            tags.update(str(part).strip() for part in extraction_tags if str(part).strip())
        if tags & extraction_fallback_tags:
            src = str(cop.get("source_statute") or "unknown")
            for ts in _matching_sections_for_row(row=cop, section_labels=section_labels):
                extraction_fallback_by_section.setdefault(ts, []).append(src)

    results: dict[str, ChainCompletenessStatus] = {}
    for label in section_labels:
        missing: list[str] = []
        extraction_fb: list[str] = []
        failed: list[str] = []
        rejected: list[str] = []
        boundary_violation_sources: dict[str, list[str]] = {kind: [] for kind in boundary_violations}
        unresolved_dates: list[str] = []
        touched_sources = list(touched_sources_by_section.get(label) or [])

        # Statute-wide source incompleteness only poisons sections that were
        # actually touched in the compiled amendment traffic when we can tell.
        # If there is no touch map at all, keep the old statute-wide fallback.
        if has_source_incomplete:
            if touched_sources:
                missing.extend(touched_sources)
            elif not has_any_section_touch_map:
                missing.append("statute_wide")

        # Same policy for extraction fallback: section-local when possible,
        # statute-wide only when no touch map exists.
        if has_extraction_fallback:
            if touched_sources:
                extraction_fb.extend(touched_sources)
            elif not has_any_section_touch_map:
                extraction_fb.append("statute_wide")

        # Per-section extraction fallback from compiled_ops hints
        if label in extraction_fallback_by_section:
            for src in extraction_fallback_by_section[label]:
                if src not in extraction_fb:
                    extraction_fb.append(src)

        # Per-section failed ops
        if label in failed_by_section:
            failed.extend(failed_by_section[label])

        # Same policy for pre-apply rejected operations: section-local when possible,
        # statute-wide only when no touch map exists.
        if has_rejected_operation:
            if touched_sources:
                rejected.extend(src for src in touched_sources if src not in rejected)
            elif not has_any_section_touch_map:
                rejected.append("statute_wide")

        for kind in boundary_violations:
            if touched_sources:
                boundary_violation_sources[kind].extend(
                    src for src in touched_sources if src not in boundary_violation_sources[kind]
                )
            elif not has_any_section_touch_map:
                boundary_violation_sources[kind].append("statute_wide")

        # Same policy for missing dates.
        if has_missing_date:
            if touched_sources:
                unresolved_dates.extend(
                    src for src in touched_sources if src not in unresolved_dates
                )
            elif not has_any_section_touch_map:
                unresolved_dates.append("statute_wide")

        is_complete = not (missing or extraction_fb or failed or rejected or unresolved_dates)
        if is_complete:
            is_complete = not any(boundary_violation_sources.values())

        blockers = [
            *_sources_to_blockers(
                kind=SOURCE_INCOMPLETE_BLOCKER,
                section_label=label,
                sources=missing,
            ),
            *_sources_to_blockers(
                kind=EXTRACTION_FALLBACK_BLOCKER,
                section_label=label,
                sources=extraction_fb,
            ),
            *_sources_to_blockers(
                kind=FAILED_OPERATION_BLOCKER,
                section_label=label,
                sources=failed,
            ),
            *_sources_to_blockers(
                kind=REJECTED_OPERATION_BLOCKER,
                section_label=label,
                sources=rejected,
            ),
        ]
        for kind, sources in boundary_violation_sources.items():
            blockers.extend(
                _sources_to_blockers(
                    kind=cast(CompletenessBlockerKind, kind),
                    section_label=label,
                    sources=sources,
                )
            )
        blockers.extend(
            _sources_to_blockers(
                kind=MISSING_EFFECTIVE_DATE_BLOCKER,
                section_label=label,
                sources=unresolved_dates,
            )
        )

        results[label] = ChainCompletenessStatus(
            section_label=label,
            is_complete=is_complete,
            blockers=blockers,
        )

    return results
