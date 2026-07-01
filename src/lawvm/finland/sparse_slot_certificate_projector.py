"""Project Finland sparse-slot elaboration rows into candidate-set certificates."""

from __future__ import annotations

import hashlib
from typing import Any, Mapping

from lawvm.core.candidate_set_coverage import CandidateSetCoverage
from lawvm.finland.proof_surface_row_helpers import positive_int, string_sequence

SPARSE_SLOT_PROMOTION_PROOFS: tuple[str, ...] = (
    "full_sparse_slot_candidate_enumeration",
    "target_uniqueness_proof",
    "slot_uniqueness_proof",
    "payload_identity_proof",
    "rejected_candidate_accounting_proof",
    "mutation_boundary_proof_before_replay_promotion",
)

def sparse_slot_candidate_set_coverage_rows(
    projection_rows: tuple[Mapping[str, Any], ...],
    *,
    statute_id: str = "",
) -> list[dict[str, Any]]:
    """Project Finland sparse-slot report rows into candidate certificates."""

    certificates: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, tuple[str, ...]]] = set()
    for row in projection_rows:
        certificate = sparse_slot_candidate_certificate(row, statute_id=statute_id)
        if certificate is None:
            continue
        payload = certificate.to_dict()
        key = (
            str(payload.get("scope_id") or ""),
            str(payload.get("rule_id") or ""),
            str(payload.get("reason") or ""),
            tuple(str(candidate) for candidate in payload.get("candidate_ids", []) or []),
        )
        if key in seen:
            continue
        seen.add(key)
        certificates.append(payload)
    return certificates


def sparse_slot_candidate_certificate(
    row: Mapping[str, Any],
    *,
    statute_id: str = "",
) -> CandidateSetCoverage | None:
    kind = str(row.get("kind") or "")
    detail_raw = row.get("detail")
    if not isinstance(detail_raw, Mapping):
        return None
    detail = dict(detail_raw)
    if kind == "ELAB.SPARSE_SLOT_BINDING":
        return sparse_slot_binding_candidate_certificate(detail, statute_id=statute_id)
    if kind == "ELAB.SPARSE_PAYLOAD_LEFTOVER":
        return sparse_leftover_candidate_certificate(detail, statute_id=statute_id)
    if kind in {"ELAB.AMBIGUOUS_BINDING", "ELAB.POSITIONAL_FALLBACK_BINDING"}:
        return sparse_ambiguous_binding_candidate_certificate(detail, statute_id=statute_id)
    if kind == "ELAB.UNASSIGNED_SPARSE_SLOTS":
        return sparse_leftover_candidate_certificate(detail, statute_id=statute_id)
    return None


def sparse_slot_binding_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCoverage:
    source_statute = str(detail.get("source_statute") or statute_id or "unknown")
    target_unit_kind = str(detail.get("target_unit_kind") or "")
    target_norm = str(detail.get("target_norm") or "")
    target_chapter = str(detail.get("target_chapter") or "")
    slot_index = positive_int(detail.get("payload_slot_index"))
    slot_label = str(detail.get("payload_slot_label") or "")
    candidate_id = sparse_payload_slot_candidate_id(slot_index=slot_index, slot_label=slot_label)
    scope_id = sparse_scope_id(
        source_statute=source_statute,
        target_unit_kind=target_unit_kind,
        target_norm=target_norm,
        target_chapter=target_chapter,
        suffix=f"binding:{candidate_id}",
    )
    return CandidateSetCoverage(
        scope_id=scope_id,
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_slot_binding_candidate_set",
        reason="selected_sparse_slot_binding_recorded_without_full_candidate_enumeration",
        completeness_status="partial",
        candidate_count=1,
        candidate_ids=(candidate_id,),
        selected_candidate_ids=(candidate_id,),
        blocker_counts={"candidate_set_not_enumerated": 1},
        blocker_families=("candidate_set_completeness",),
        next_promotion_allowed=False,
        next_promotion_requires=SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "target_unit_kind_witness": target_unit_kind,
            "target_norm_witness": target_norm,
            "target_chapter_witness": target_chapter,
            "op_description": str(detail.get("op_description") or ""),
            "op_type": str(detail.get("op_type") or ""),
            "target_paragraph": str(detail.get("target_paragraph") or ""),
            "target_item": str(detail.get("target_item") or ""),
            "target_special": str(detail.get("target_special") or ""),
            "payload_slot_index": slot_index,
            "payload_slot_label": slot_label,
            "projection_only": True,
        },
    )


def sparse_ambiguous_binding_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCoverage:
    source_statute = str(detail.get("amendment_id") or detail.get("source_statute") or statute_id or "unknown")
    slot_id = positive_int(detail.get("slot_id"))
    candidate_count = max(positive_int(detail.get("candidate_count")), 1)
    candidate_id = f"payload-slot:{slot_id}" if slot_id else "payload-slot:unknown"
    return CandidateSetCoverage(
        scope_id=sparse_scope_id(
            source_statute=source_statute,
            target_unit_kind="",
            target_norm="",
            target_chapter="",
            suffix=f"ambiguous:{candidate_id}",
        ),
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_slot_ambiguous_binding_candidate_set",
        reason="ambiguous_sparse_slot_binding",
        completeness_status="partial",
        candidate_count=candidate_count,
        candidate_ids=(candidate_id,),
        blocker_counts={"ambiguous_binding": 1},
        blocker_families=("sparse_slot_ambiguity",),
        next_promotion_allowed=False,
        next_promotion_requires=SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "slot_id": slot_id,
            "admissibility": str(detail.get("admissibility") or ""),
            "projection_only": True,
        },
    )


def sparse_leftover_candidate_certificate(
    detail: Mapping[str, Any],
    *,
    statute_id: str,
) -> CandidateSetCoverage | None:
    slots = string_sequence(detail.get("unassigned_slots"))
    if not slots:
        return None
    source_statute = str(detail.get("source_statute") or statute_id or "unknown")
    target_unit_kind = str(detail.get("target_unit_kind") or "")
    target_norm = str(detail.get("target_norm") or "")
    target_chapter = str(detail.get("target_chapter") or "")
    candidate_ids = tuple(sparse_payload_slot_candidate_id_from_text(slot) for slot in slots)
    return CandidateSetCoverage(
        scope_id=sparse_scope_id(
            source_statute=source_statute,
            target_unit_kind=target_unit_kind,
            target_norm=target_norm,
            target_chapter=target_chapter,
            suffix="unassigned:" + hashlib.sha256("|".join(candidate_ids).encode("utf-8")).hexdigest()[:12],
        ),
        candidate_set_kind="fi_sparse_payload_slot_assignment",
        phase="typed_elaboration",
        rule_id="fi_sparse_unassigned_payload_slot_candidate_set",
        reason="unassigned_sparse_payload_slots",
        completeness_status="rejected",
        candidate_count=len(candidate_ids),
        candidate_ids=candidate_ids,
        blocker_counts={"unassigned_payload_slot": len(candidate_ids)},
        blocker_families=("sparse_payload_leftover",),
        next_promotion_allowed=False,
        next_promotion_requires=SPARSE_SLOT_PROMOTION_PROOFS,
        detail={
            "jurisdiction": "fi",
            "source_statute": source_statute,
            "target_unit_kind_witness": target_unit_kind,
            "target_norm_witness": target_norm,
            "target_chapter_witness": target_chapter,
            "unassigned_slots": slots,
            "projection_only": True,
        },
    )
def sparse_payload_slot_candidate_id(*, slot_index: int, slot_label: str) -> str:
    label = slot_label.strip() or "unlabeled"
    index = str(slot_index) if slot_index else "unknown"
    return f"payload-slot:{index}:{label}"


def sparse_payload_slot_candidate_id_from_text(slot: str) -> str:
    text = str(slot or "").strip()
    if ":" not in text:
        return f"payload-slot:unknown:{text or 'unlabeled'}"
    index, label = text.split(":", 1)
    return sparse_payload_slot_candidate_id(
        slot_index=positive_int(index),
        slot_label=label.strip("()") or "unlabeled",
    )


def sparse_scope_id(
    *,
    source_statute: str,
    target_unit_kind: str,
    target_norm: str,
    target_chapter: str,
    suffix: str,
) -> str:
    scope_parts = (
        source_statute or "unknown",
        target_unit_kind or "unknown-target-kind",
        target_chapter or "no-chapter",
        target_norm or "unknown-target",
        suffix,
    )
    safe = ":".join(part.replace("/", "_").replace(" ", "_") for part in scope_parts)
    return f"fi-sparse-slot:{safe}"

__all__ = [
    "SPARSE_SLOT_PROMOTION_PROOFS",
    "sparse_slot_candidate_set_coverage_rows",
]
