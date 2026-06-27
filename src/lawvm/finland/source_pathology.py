"""Typed Finland replay-time source-pathology helpers."""

from __future__ import annotations

from lawvm.core.compile_result import SourcePathology
from lawvm.core.payload_surface import TargetUnitKind
from lawvm.core.recovery_kind import RecoveryKind
from lawvm.core.quirks_disposition import QuirksDisposition


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


def build_body_section_label_mismatch_payload_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    observed_section: str,
    target_chapter: str = "",
    source_unit_id: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for one-section payload label drift."""
    return SourcePathology.from_scope(
        code="BODY_SECTION_LABEL_MISMATCH_PAYLOAD",
        message=(
            "The operative formula explicitly targeted one section, but the only "
            "source-body section payload carried a conflicting label; the payload "
            "was bound to the explicit formula target and the label drift was "
            "recorded as source pathology."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "observed_section": observed_section,
            "source_unit_id": source_unit_id,
            "diagnostic_reason": "single_body_section_label_mismatch",
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


def build_section_replace_bootstrap_parent_missing_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    target_part: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for rejected scoped section bootstrap."""
    return SourcePathology.from_scope(
        code="SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING",
        message=(
            "Whole-section replace could not bootstrap a missing target because the "
            "explicit scoped parent container was absent; replay refused to insert at an unproven parent."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_part": target_part,
            "target_chapter": target_chapter,
            "target_section": target_section,
            "recovery_kind": RecoveryKind.SECTION_REPLACE_BOOTSTRAP_PARENT_MISSING,
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


def build_same_effective_container_repeal_shadowed_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    prior_source_statute: str,
    effective: str,
) -> SourcePathology:
    """Build a typed record for a same-date repeal shadowed by a replacement.

    This covers repeal/rebirth pairs where one act repeals an old container and
    another same-effective-date act inserts or replaces the same container slot.
    Literal replay must not let the repeal delete the newly inserted subtree.
    """
    return SourcePathology.from_scope(
        code="SAME_EFFECTIVE_CONTAINER_REPEAL_SHADOWED",
        message=(
            "Whole-container repeal was skipped because replay history already "
            "contains a same-effective-date replacement/insert for the same "
            "container path from another source."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={
            "prior_source_statute": prior_source_statute,
            "effective": effective,
            "recovery_kind": RecoveryKind.SAME_EFFECTIVE_CONTAINER_REPEAL_SHADOWED,
            "strict_disposition": "record",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
    )


__all__ = [
    "build_container_replace_target_absent_pathology",
    "build_container_op_target_absent_pathology",
    "build_container_otsikko_payload_absent_pathology",
    "build_section_insert_scoped_parent_absent_pathology",
    "build_unhandled_structure_op_pathology",
    "build_container_membership_mismatch_pathology",
    "build_recodification_source_chain_gap_pathology",
    "build_recodification_omission_only_section_shell_pathology",
    "build_destructive_shape_loss_risk_pathology",
    "build_empty_operative_body_pathology",
    "build_item_target_structure_absent_pathology",
    "build_item_target_slot_occupied_pathology",
    "build_item_target_anchor_absent_pathology",
    "build_item_target_positional_rebind_pathology",
    "build_subsection_target_rebound_pathology",
    "build_subsection_target_absent_pathology",
    "build_temporary_section_rebase_pathology",
    "build_partial_whole_section_payload_pathology",
    "build_sparse_item_body_missing_pathology",
    "build_malformed_broad_replace_body_pathology",
    "build_section_replace_bootstrap_parent_missing_pathology",
    "build_same_effective_container_repeal_shadowed_pathology",
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


def build_recodification_omission_only_section_shell_pathology(
    *,
    source_statute: str,
    source_target_norm: str,
    destination_target_norm: str,
    target_chapter: str = "",
    target_part: str = "",
) -> SourcePathology:
    """Build a source-pathology record for renumber-only omission shells.

    Large recodification waves can publish destination-number sections as
    heading-plus-omission shells without operative body text. Replay may renumber
    the live section, but must not invent oracle text from uncovered recovery.
    """
    return SourcePathology.from_scope(
        code="RECODIFICATION_OMISSION_ONLY_SECTION_SHELL",
        message=(
            "Recodification destination section is an omission-only shell in source; "
            "operative body text is absent from the amendment XML."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=_target_label(destination_target_norm, target_chapter),
        detail={
            "source_target_norm": source_target_norm,
            "destination_target_norm": destination_target_norm,
            "target_chapter": target_chapter,
            "target_part": target_part,
            "source_surface": "sparse_omission_shell",
            "recovery_kind": RecoveryKind.RECODIFICATION_OMISSION_ONLY_SECTION_SHELL,
            "strict_disposition": "block",
            "quirks_disposition": QuirksDisposition.RECORD,
        },
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
    target_special: str = "",
    diagnostic_reason: str = "",
) -> SourcePathology:
    """Build a typed source-pathology record for opaque item-target material.

    Also covers special-target (otsikko/johd) repeals whose target structure is
    already absent from the live unit: the authored op is a structurally no-op
    decline, so it must be witnessed on the source-pathology ledger rather than
    returned as silent state. ``target_special`` + ``diagnostic_reason`` carry the
    op identity and the decline reason so each declined site stays distinguishable
    on the public trust surface. (LAWVM_PIPELINE_CONTRACT §1.1 no-silent-drop.)
    """
    special_label = (
        f"{target_section} § {target_paragraph} mom {target_special}".strip()
        if target_special
        else f"{target_section} § {target_paragraph} mom {target_item} kohta"
    )
    return SourcePathology.from_scope(
        code="ITEM_TARGET_STRUCTURE_ABSENT",
        message=(
            "Item-level target could not be applied literally because neither the "
            "live subsection nor the amendment payload exposed targetable item "
            "structure for that target."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=special_label,
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "target_item": target_item,
            "live_has_paragraphs": live_has_paragraphs,
            "amend_has_paragraphs": amend_has_paragraphs,
            "target_special": target_special,
            "diagnostic_reason": diagnostic_reason,
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


def build_item_target_positional_rebind_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_paragraph: str,
    source_item_label: str,
    assigned_item_label: str,
    ordinal: int,
    live_item_count: int = 0,
) -> SourcePathology:
    """Build a typed record for a letter→digit kohta rebind by ordinal position.

    Some amendments author a kohta target with a *letter* scheme (``a``/``e``)
    while the live consolidation numbers the same items with *digits*. Replay
    resolves the letter to its ordinal digit slot when the live list is uniformly
    digit-labelled. That is a positional-identity assignment (no intrinsic label
    match), so it must be witnessed on a public surface rather than left to a
    console ``logger.debug``. (LAWVM_PIPELINE_CONTRACT §1 no-silent-guess, §8
    no-positional-identity.)
    """
    return SourcePathology.from_scope(
        code="ITEM_TARGET_POSITIONAL_REBIND",
        message=(
            "Item-level target was rebound from a letter scheme to a live digit "
            "slot by ordinal position because no intrinsic label match existed; "
            "identity was assigned positionally rather than by label."
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=f"{target_section} § {target_paragraph} mom {source_item_label} kohta",
        detail={
            "target_section": target_section,
            "target_paragraph": target_paragraph,
            "source_item_label": source_item_label,
            "assigned_item_label": assigned_item_label,
            "ordinal": ordinal,
            "live_item_count": live_item_count,
            "identity_basis": "ordinal_position",
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
    rebound_kind: RecoveryKind,
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


def build_container_op_target_absent_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    op_type: str,
    target_chapter: str = "",
    target_paragraph: str | int = "",
    target_item: str = "",
    target_special: str = "",
) -> SourcePathology:
    """Build a typed record for a non-INSERT/REPLACE container op whose target is absent.

    A container (chapter/part) REPEAL/RENUMBER (any op that is neither INSERT nor
    REPLACE) named an explicit live target that could not be resolved in the live
    state. The authored op therefore applies nothing. Rather than vanish as a
    silent ``return state`` no-op (which leaves the dropped op unaccounted-for on
    every production surface), the absent-target condition is witnessed: a repeal
    or renumber of a container that is not present is a dropped edit, not a
    satisfied no-op. (LAWVM_PIPELINE_CONTRACT §1.1 no silent drop.) This is the
    REPEAL/RENUMBER sibling of ``CONTAINER_REPLACE_TARGET_ABSENT``.
    """
    return SourcePathology.from_scope(
        code="CONTAINER_OP_TARGET_ABSENT",
        message=(
            "Container operation could not be applied because the targeted live "
            "chapter/part was absent."
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
            "op_type": op_type,
        },
    )


def build_container_otsikko_payload_absent_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    op_type: str = "",
    payload_child_kinds: list[str] | None = None,
) -> SourcePathology:
    """Build a typed record for a container heading op carrying no usable heading.

    A container (chapter/part) ``otsikko`` REPLACE/other op resolved its target
    but the amendment payload exposed no ``heading`` (nor ``crossHeading``) node to
    install, so the authored heading edit applies nothing. Rather than vanish as a
    silent ``return state`` NO_APPLY_PASS, the missing-payload-heading condition is
    witnessed: a heading REPLACE with no heading in the body is a dropped/under-
    determined edit, not a satisfied no-op. (LAWVM_PIPELINE_CONTRACT §1.1 no
    silent drop.)
    """
    return SourcePathology.from_scope(
        code="CONTAINER_OTSIKKO_PAYLOAD_ABSENT",
        message=(
            "Container heading operation resolved its live target but the amendment "
            "payload exposed no heading node to install, so the authored heading "
            "edit applied nothing."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "op_type": op_type,
            "payload_child_kinds": list(payload_child_kinds or []),
        },
    )


def build_section_insert_scoped_parent_absent_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    target_part: str = "",
) -> SourcePathology:
    """Build a typed record for a scoped section INSERT refused for a missing parent.

    A section INSERT named an explicit scoped parent (part/chapter) that is absent
    from the live state, and no proven scaffold could be seeded for it. Replay
    refuses to insert at an unproven parent (mirroring the whole-section bootstrap
    refusal) rather than guess a destination, but the refusal must be witnessed:
    the authored insert applies nothing. (LAWVM_PIPELINE_CONTRACT §1.1 no silent
    drop.)
    """
    return SourcePathology.from_scope(
        code="SECTION_INSERT_SCOPED_PARENT_ABSENT",
        message=(
            "Scoped section INSERT could not be applied because its explicit scoped "
            "parent container was absent and no proven scaffold could be seeded; "
            "replay refused to insert at an unproven parent."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_part": target_part,
            "target_chapter": target_chapter,
            "target_section": target_section,
        },
    )


def build_unhandled_structure_op_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_section: str,
    target_chapter: str = "",
    op_type: str = "",
    target_special: str = "",
    helper: str = "",
) -> SourcePathology:
    """Build a typed record for a structure op that matched no apply arm.

    A resolved structural op reached the terminal fall-through of an apply helper
    without any arm executing it (an unhandled op-type / target-shape combination).
    Rather than vanish as a silent ``return state``, the unhandled op is witnessed
    so the unbounded fall-through cannot drop an authored op unaccounted-for.
    (LAWVM_PIPELINE_CONTRACT §1.1 no silent drop, §1.2 no silent guess.)
    """
    return SourcePathology.from_scope(
        code="UNHANDLED_STRUCTURE_OP",
        message=(
            "Structural operation matched no apply arm and reached the helper "
            "fall-through; the authored op applied nothing."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "op_type": op_type,
            "target_special": target_special,
            "helper": helper,
        },
    )


def build_destructive_shape_loss_risk_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: RecoveryKind,
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


def build_unscoped_root_duplicate_consumed_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    scoped_target_path: str,
    consumed_path: str,
) -> SourcePathology:
    """Build a typed record for consuming a stale unscoped section duplicate."""
    return SourcePathology.from_scope(
        code="UNSCOPED_ROOT_DUPLICATE_CONSUMED",
        message=(
            "A later explicitly scoped section replacement consumed a stale direct "
            "wrapper-level section with the same label."
        ),
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_label=target_label,
        detail={
            "recovery_kind": RecoveryKind.SECTION_REPLACE_CONSUME_UNSCOPED_ROOT_DUPLICATE,
            "scoped_target_path": scoped_target_path,
            "consumed_path": consumed_path,
        },
    )


def build_sparse_merge_invariant_skip_pathology(
    *,
    source_statute: str,
    target_unit_kind: TargetUnitKind,
    target_label: str,
    recovery_kind: RecoveryKind,
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
    recovery_kind: RecoveryKind,
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


def build_subsection_shell_replace_kept_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_chapter: str = "",
    source_clause: str,
) -> SourcePathology:
    """Witness the keep decision for a whole-section shell over a plain subsection.

    ``_drop_suspicious_partial_subsection_shell_replaces`` drops subsection-targeted
    replaces that carry a stale whole-section wrapper, EXCEPT when the source text
    explicitly targets the plain ``N momentti`` subsection (then the shell is
    legitimate and the ops are kept). That keep branch previously emitted no
    witness, so the source-plane keep decision was unrecorded. This records it,
    embedding the source clause that justified the keep.
    """
    return SourcePathology.from_scope(
        code="SUBSECTION_SHELL_REPLACE_KEPT",
        message=(
            "Whole-section shell over a subsection-targeted replace was KEPT (not "
            "dropped) because the source explicitly targets the plain subsection: "
            f"{source_clause!r}"
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "source_clause": source_clause,
            "diagnostic_reason": "explicit_plain_subsection_replace_source_kept_shell",
        },
    )


def build_unresolved_descendant_scope_cue_pathology(
    *,
    source_statute: str,
    target_section: str,
    target_chapter: str = "",
    unparsed_cue: str,
) -> SourcePathology:
    """Build a typed residual for an unresolved source descendant-scope cue.

    The amendment source named an ``N §:n ... moment/kohta/alakohta`` descendant-
    scope formula, but for no section matching the snapshot target. The apply path
    used to swallow this as a silent ``False``; this self-evidencing residual
    embeds the offending clause text so the unhandled cue is observable instead.
    """
    return SourcePathology.from_scope(
        code="UNRESOLVED_DESCENDANT_SCOPE_CUE",
        message=(
            "Source formula names a section-genitive descendant-scope cue "
            "(N §:n ... moment/kohta/alakohta) that did not resolve to the snapshot "
            f"target section: {unparsed_cue!r}"
        ),
        source_statute=source_statute,
        target_unit_kind="section",
        target_label=_target_label(target_section, target_chapter),
        detail={
            "target_chapter": target_chapter,
            "target_section": target_section,
            "unparsed_cue": unparsed_cue,
            "diagnostic_reason": "source_descendant_scope_cue_unresolved",
        },
    )
