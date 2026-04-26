"""Typed Finland replay-time source-pathology helpers."""

from __future__ import annotations

from lawvm.core.compile_result import SourcePathology
from lawvm.core.payload_surface import TargetUnitKind


def _target_label(target_section: str, target_chapter: str = "") -> str:
    return f"{target_chapter} luku {target_section} §".strip() if target_chapter else f"{target_section} §"


def build_partial_whole_section_payload_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    live_paragraph_count: int = 0,
    amend_paragraph_count: int = 0,
    live_text_chars: int = 0,
    amend_text_chars: int = 0,
    diagnostic_reason: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for suspicious partial section payloads."""
    return SourcePathology.from_scope(
        code="PARTIAL_WHOLE_SECTION_PAYLOAD",
        message=(
            "Whole-section replace target is paired with only a partial payload body; "
            "the source should be treated as suspicious rather than silently literal."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "live_paragraph_count": live_paragraph_count,
            "amend_paragraph_count": amend_paragraph_count,
            "live_text_chars": live_text_chars,
            "amend_text_chars": amend_text_chars,
            "diagnostic_reason": diagnostic_reason,
        },
    )


def build_malformed_broad_replace_body_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    live_paragraph_count: int = 0,
    amend_paragraph_count: int = 0,
    live_text_chars: int = 0,
    amend_text_chars: int = 0,
    diagnostic_reason: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for partial broad replace bodies."""
    return SourcePathology.from_scope(
        code="MALFORMED_BROAD_REPLACE_BODY",
        message=(
            "Broad replace target is paired with a suspiciously partial source body; "
            "literal replay would risk destructive shape loss."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "live_paragraph_count": live_paragraph_count,
            "amend_paragraph_count": amend_paragraph_count,
            "live_text_chars": live_text_chars,
            "amend_text_chars": amend_text_chars,
            "diagnostic_reason": diagnostic_reason,
        },
    )


def build_empty_operative_body_pathology(
    *,
    source_statute: str,
    source_title: str = "",
    has_sec1_fallback_text: bool = False,
    operative_tags_detected: list[str] | None = None,
) -> SourcePathology:
    """Build a typed source-pathology record for bodyless operative amendments."""
    return SourcePathology(
        code="EMPTY_OPERATIVE_BODY",
        message=(
            "Amendment source lacks operative body text/structure, so replay cannot "
            "extract legal effects literally from the published XML."
        ),
        source_statute=source_statute,
        target_label=source_title.strip() or source_statute,
        detail={
            "has_sec1_fallback_text": has_sec1_fallback_text,
            "operative_tags_detected": list(operative_tags_detected or []),
        },
    )


__all__ = [
    "build_container_replace_target_absent_pathology",
    "build_container_membership_mismatch_pathology",
    "build_recodification_source_chain_gap_pathology",
    "build_destructive_shape_loss_risk_pathology",
    "build_empty_operative_body_pathology",
    "build_item_target_structure_absent_pathology",
    "build_item_target_slot_occupied_pathology",
    "build_item_target_anchor_absent_pathology",
    "build_subsection_target_rebound_pathology",
    "build_subsection_target_absent_pathology",
    "build_temporary_section_rebase_pathology",
    "build_partial_whole_section_payload_pathology",
    "build_sparse_item_body_missing_pathology",
    "build_malformed_broad_replace_body_pathology",
]


def build_container_membership_mismatch_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    pruned_sections: list[str],
) -> SourcePathology:
    """Build a typed source-pathology record for malformed container membership."""
    return SourcePathology.from_scope(
        code="CONTAINER_MEMBERSHIP_MISMATCH",
        message=(
            "Container payload bundled standalone sections that do not belong to the "
            "live container membership and had to be pruned."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={"pruned_sections": list(pruned_sections)},
    )


def build_recodification_source_chain_gap_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    diagnostic_reason: str,
) -> SourcePathology:
    """Build a typed source-pathology record for recodification source-chain gaps."""
    return SourcePathology.from_scope(
        code="RECODIFICATION_SOURCE_CHAIN_GAP",
        message=(
            "Recodification relabel target could not be resolved against the executable "
            "pre-wave source chain without guessing."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={"diagnostic_reason": diagnostic_reason},
    )


def build_sparse_item_body_missing_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str,
    target_item: str,
) -> SourcePathology:
    """Build a typed source-pathology record for sparse omission payload item loss."""
    return SourcePathology.from_scope(
        code="SPARSE_ITEM_BODY_MISSING",
        message=(
            "Sparse omission payload did not reproduce the targeted item body, so the "
            "item-level replace could not be applied literally."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom {target_item} kohta",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
        },
    )


def build_item_target_structure_absent_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str,
    target_item: str,
    live_has_paragraphs: bool,
    amend_has_paragraphs: bool,
) -> SourcePathology:
    """Build a typed source-pathology record for opaque item-target material."""
    return SourcePathology.from_scope(
        code="ITEM_TARGET_STRUCTURE_ABSENT",
        message=(
            "Item-level target could not be applied literally because neither the "
            "live subsection nor the amendment payload exposed targetable item "
            "structure for that target."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom {target_item} kohta",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
        },
    )


def build_item_target_slot_occupied_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str,
    target_item: str,
    occupied_item_label: str,
    live_has_paragraphs: bool,
    amend_has_paragraphs: bool,
) -> SourcePathology:
    """Build a typed source-pathology record for an occupied item slot collision."""
    return SourcePathology.from_scope(
        code="ITEM_TARGET_SLOT_OCCUPIED",
        message=(
            "Item-level insert could not be applied literally because the targeted "
            "slot was already occupied by a live item label."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom {target_item} kohta",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
            "occupied_item_label": occupied_item_label,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
        },
    )


def build_item_target_anchor_absent_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str,
    target_item: str,
    live_label: str = "",
    live_has_paragraphs: bool = False,
    amend_has_paragraphs: bool = False,
) -> SourcePathology:
    """Build a typed source-pathology record for a missing numeric anchor."""
    return SourcePathology.from_scope(
        code="ITEM_TARGET_ANCHOR_ABSENT",
        message=(
            "Item-level replace could not be applied literally because the "
            "expected numeric anchor was absent from the live subsection."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom {target_item} kohta",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
            "live_label": live_label,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
        },
    )


def build_subsection_target_absent_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str | int,
    live_label: str = "",
    has_higher_live_numeric_label: bool = False,
    live_has_paragraphs: bool = False,
    amend_has_paragraphs: bool = False,
) -> SourcePathology:
    """Build a typed source-pathology record for an unmatched subsection target."""
    return SourcePathology.from_scope(
        code="SUBSECTION_TARGET_ABSENT",
        message=(
            "Subsection-level target could not be applied literally because the "
            "requested moment was absent from the live structure."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "live_label": live_label,
            "has_higher_live_numeric_label": has_higher_live_numeric_label,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
        },
    )


def build_subsection_target_rebound_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str | int,
    rebound_kind: str,
    stale_fragment_idx: int = -1,
    live_has_paragraphs: bool = False,
    amend_has_paragraphs: bool = False,
) -> SourcePathology:
    """Build a typed source-pathology record for a subsection target rebound."""
    return SourcePathology.from_scope(
        code="SUBSECTION_TARGET_REBOUND",
        message="Subsection-level target was rebound to a live slot during replay structure recovery.",
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "rebound_kind": rebound_kind,
            "stale_fragment_idx": stale_fragment_idx,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
        },
    )


def build_temporary_section_rebase_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_chapter: str = "",
    rebase_context: str,
    rebase_kind: str,
    latest_snapshot_expires: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for temporary section base rebasing."""
    return SourcePathology.from_scope(
        code="TEMPORARY_SECTION_REBASE",
        message=(
            "Section merge base was rebound away from an expired temporary snapshot "
            "during replay."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "rebase_context": rebase_context,
            "rebase_kind": rebase_kind,
            "latest_snapshot_expires": latest_snapshot_expires,
        },
    )


def build_container_replace_target_absent_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    target_paragraph: str | int = "",
    target_item: str = "",
    target_special: str = "",
    has_payload: bool = False,
) -> SourcePathology:
    """Build a typed source-pathology record for missing container replace targets."""
    return SourcePathology.from_scope(
        code="CONTAINER_REPLACE_TARGET_ABSENT",
        message=(
            "Container REPLACE could not be applied literally because the targeted "
            "live chapter/part was absent."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
            "target_special": target_special,
            "has_payload": has_payload,
        },
    )


def build_destructive_shape_loss_risk_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: str,
    live_sibling_count: int = 0,
    payload_sibling_count: int = 0,
) -> SourcePathology:
    """Build a typed source-pathology record for apply-time sparse merge recovery."""
    return SourcePathology.from_scope(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        message=(
            "Literal replay would discard untouched live sibling structure; replay used "
            "a sparse merge recovery instead."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={
            "recovery_kind": recovery_kind,
            "live_sibling_count": live_sibling_count,
            "payload_sibling_count": payload_sibling_count,
        },
    )


def build_sparse_merge_invariant_skip_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: str,
    live_sibling_count: int = 0,
    payload_sibling_count: int = 0,
) -> SourcePathology:
    """Build a typed record for sparse merge attempts skipped on invariant failure."""
    return SourcePathology.from_scope(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        message=(
            "Sparse merge recovery was rejected by an invariant check; replay preserved "
            "the live structure instead of applying an unsafe merge."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={
            "recovery_kind": recovery_kind,
            "live_sibling_count": live_sibling_count,
            "payload_sibling_count": payload_sibling_count,
        },
    )


def build_unique_payload_insert_under_live_duplicates_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: str,
    live_sibling_count: int = 0,
    payload_sibling_count: int = 0,
) -> SourcePathology:
    """Build a typed record for a unique insert into a duplicate-bearing live container."""
    return SourcePathology.from_scope(
        code="DESTRUCTIVE_SHAPE_LOSS_RISK",
        message=(
            "Live container has duplicate labels, but the amendment payload owns a "
            "unique new child; replay preserved live duplicates and admitted the unique payload."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={
            "recovery_kind": recovery_kind,
            "live_sibling_count": live_sibling_count,
            "payload_sibling_count": payload_sibling_count,
        },
    )
