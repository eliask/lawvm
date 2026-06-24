"""Finding builders for Finland uncovered-body recovery."""

from __future__ import annotations

from lawvm.core.observation_registry import get_finding_spec
from lawvm.core.phase_result import Finding


def strict_rejected_uncovered_body_finding(
    *,
    source_statute: str,
    stage: str,
) -> Finding:
    """Build the blocking finding for strict-profile uncovered-body rejection."""
    return Finding(
        kind="APPLY.STRICT_REJECTED_UNCOVERED_BODY",
        role="obligation",
        stage=stage,
        blocking=True,
        source_statute=source_statute,
        detail={
            "message": (
                "Uncovered body recovery rejected by strict profile "
                "(allows_uncovered_body_recovery=False)"
            ),
        },
    )


def uncovered_body_recovery_finding(
    *,
    op_id: str,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
) -> Finding | None:
    """Build the replay-owned finding for one uncovered-body recovery action."""
    if op_id.startswith("uncovered_replace_"):
        kind = "APPLY.FALLBACK_WHOLE_SECTION_REPLACE"
        message = "Fallback whole-section replacement was used."
    elif op_id.startswith("uncovered_insert_"):
        kind = "APPLY.UNCOVERED_BODY_RECOVERY"
        message = "Uncovered-body insertion supplement was used."
    elif op_id.startswith("uncovered_merge_"):
        kind = "ELAB.OMISSION_EXPANSION"
        message = "Omission-expansion merge was used."
    elif op_id.startswith("uncovered_repeal_"):
        kind = "APPLY.UNCOVERED_BODY_RECOVERY"
        message = "Uncovered-body repeal recovery was used."
    else:
        return None

    detail: dict[str, object] = {
        "message": message,
        "op_id": op_id,
        "target_unit_kind": target_unit_kind,
        "target_norm": target_norm,
    }
    if target_chapter:
        detail["target_chapter"] = target_chapter
    if target_part:
        detail["target_part"] = target_part

    spec = get_finding_spec(kind)
    if spec is not None and spec.role == "obligation":
        return Finding(
            kind=kind,
            role="obligation",
            stage="apply",
            blocking=True,
            source_statute=source_statute,
            detail={
                **detail,
                "barrier_code": kind,
            },
        )

    return Finding(
        kind="RUNTIME.VIOLATION",
        role="violation",
        stage="apply",
        blocking=True,
        source_statute=source_statute,
        detail={
            **detail,
            "barrier_code": kind,
        },
    )


def uncovered_body_recovery_skipped_finding(
    *,
    source_statute: str,
    target_section: str,
    reason: str,
    target_chapter: str | None = None,
    target_part: str | None = None,
) -> Finding:
    specific_kind = {
        "duplicate_recovered_candidate": "APPLY.UNCOVERED_BODY_DUPLICATE_CANDIDATE",
        "cross_chapter_existing_target": "APPLY.UNCOVERED_BODY_CROSS_CHAPTER_COLLISION",
        "moved_destination_mismatch": "APPLY.UNCOVERED_BODY_MOVED_DESTINATION_MISMATCH",
        "same_wave_relabel_destination_owned": "APPLY.UNCOVERED_BODY_RELABEL_DESTINATION_OWNED",
        "body_pairing_guard": "APPLY.UNCOVERED_BODY_BODY_PAIRING_GUARD",
        "no_content_ops": "APPLY.UNCOVERED_BODY_NO_CONTENT_OPS",
        "would_lose_subsections": "APPLY.UNCOVERED_BODY_WOULD_LOSE_SUBSECTIONS",
        "past_repeal_placeholder_guard": "APPLY.UNCOVERED_BODY_PAST_REPEAL_GUARD",
        "johto_guard": "APPLY.UNCOVERED_BODY_PREAMBLE_GUARD",
        "omission_merge_failed": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_FAILED",
        "omission_merge_low_text_ratio": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_LOW_TEXT_RATIO",
        "omission_merge_duplicate_subsection_labels": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_DUPLICATE_LABELS",
        "omission_merge_would_lose_subsections": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_WOULD_LOSE_SUBSECTIONS",
        "omission_merge_missing_scope": "APPLY.UNCOVERED_BODY_OMISSION_MERGE_MISSING_SCOPE",
        "omission_merge_special_subprovision_scope": "APPLY.UNCOVERED_BODY_SPECIAL_SUBPROVISION_SCOPE",
        "peg_owned_same_chapter": "APPLY.UNCOVERED_BODY_PEG_SAME_CHAPTER_OWNED",
        "peg_owned_label_collision": "APPLY.UNCOVERED_BODY_PEG_LABEL_COLLISION",
        "peg_owned_descendant_same_chapter": "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_SAME_CHAPTER_OWNED",
        "peg_owned_descendant_label_collision": "APPLY.UNCOVERED_BODY_PEG_DESCENDANT_LABEL_COLLISION",
        "future_repeal": "APPLY.UNCOVERED_BODY_FUTURE_REPEAL_SKIP",
        "chapter_payload_owned": "APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_OWNED",
    }.get(reason, "APPLY.UNCOVERED_BODY_RECOVERY_SKIPPED")
    detail: dict[str, object] = {
        "message": "Uncovered-body recovery skipped a candidate section",
        "target_section": target_section,
        "target_chapter": target_chapter or "",
        "reason": reason,
    }
    if target_part:
        detail["target_part"] = target_part
    return Finding(
        kind=specific_kind,
        role="observation",
        stage="grafter_uncovered",
        blocking=False,
        source_statute=source_statute,
        detail=detail,
    )


def uncovered_body_chapter_payload_mixed_finding(
    *,
    source_statute: str,
    target_chapter: str,
    adopted_count: int,
    owned_count: int,
) -> Finding:
    return Finding(
        kind="APPLY.UNCOVERED_BODY_CHAPTER_PAYLOAD_MIXED",
        role="observation",
        stage="grafter_uncovered",
        blocking=False,
        source_statute=source_statute,
        detail={
            "message": "Covered chapter payload mixed owned child sections with explicit uncovered-body adoptions",
            "target_chapter": target_chapter,
            "adopted_count": adopted_count,
            "owned_count": owned_count,
        },
    )
