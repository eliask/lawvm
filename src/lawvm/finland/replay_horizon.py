"""Materialization horizon decisions for Finland replay."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable, Iterable, Literal, Optional

from lawvm.core.ir import LegalOperation
from lawvm.core.semantic_types import StructuralAction


def oracle_version_future_repeal_only_uses_cutoff_date(
    *,
    compiled_ops: Iterable[dict[str, object]],
    oracle_version_amendment_id: str,
    oracle_cutoff_iso: Optional[str],
) -> bool:
    """Return True when a future-effective oracle-version amendment is repeal-only.

    Finlex oracle materialization usually follows the oracle-version
    amendment's own effective date. That is correct for future-effective
    replacement families such as ``2016/258 <- 2021/1199``.

    Some oracle-version amendments are different: they are pure future repeals
    that Finlex still shows only as editorial future notice at the consolidated
    cutoff, without projecting the repeal into the selected XML. In that
    bounded family, materialization must stay at the oracle cutoff.
    """
    if not oracle_version_amendment_id or oracle_cutoff_iso is None:
        return False

    saw_oracle_version_op = False
    for op in compiled_ops:
        if str(op.get("source_statute") or "") != oracle_version_amendment_id:
            continue
        saw_oracle_version_op = True
        if str(op.get("action") or "").strip().lower() != "repeal":
            return False

    return saw_oracle_version_op


def _oracle_version_has_mixed_repeal_and_non_repeal_ops(
    *,
    compiled_ops: Iterable[dict[str, object]],
    oracle_version_amendment_id: str,
) -> bool:
    if not oracle_version_amendment_id:
        return False
    saw_repeal = False
    saw_non_repeal = False
    for op in compiled_ops:
        if str(op.get("source_statute") or "") != oracle_version_amendment_id:
            continue
        action = str(op.get("action") or "").strip().lower()
        if action == "repeal":
            saw_repeal = True
        else:
            saw_non_repeal = True
    return saw_repeal and saw_non_repeal


def _is_repeal_like_legal_operation(lo: LegalOperation) -> bool:
    """Return True for replay ops that remove visible legal state."""
    return lo.action is StructuralAction.REPEAL or (
        lo.action is StructuralAction.REPLACE
        and lo.payload is not None
        and lo.payload.attrs.get("lawvm_repeal_placeholder") == "1"
    )


def _can_detach_expiry_for_future_repeal(lo: LegalOperation) -> bool:
    """Return True for future repeal shapes that may project absence only.

    Section 1 repeal clauses are often generic preamble carriers, and subsection
    repeal placeholders are visible audit state inside their parent section.
    The detached expiry horizon is only for ordinary section-level repeals such
    as a selected oracle-version repeal of a provision that is absent from the
    consolidation surface.
    """
    if not _is_repeal_like_legal_operation(lo) or not lo.target.path:
        return False
    target_kind, target_label = lo.target.path[-1]
    return target_kind == "section" and target_label != "1"


@dataclass(frozen=True, slots=True)
class ReplayHorizonRequest:
    """Inputs for choosing replay materialization and expiry horizons."""

    mode: Literal["official_consolidation", "legal_pit"]
    as_of: str
    cutoff_date: Optional[dt.date]
    amendment_records: list[dict[str, object]]
    oracle_version_amendment_id: str
    compiled_ops: Iterable[dict[str, object]]
    legal_operations: Iterable[LegalOperation]
    oracle_reflected_section_original_versions: Iterable[str]
    oracle_single_future_repeal_overlay_versions: Iterable[str]
    replay_print: Callable[[str], None]


@dataclass(frozen=True, slots=True)
class ReplayHorizonDecision:
    """Selected materialization and expiry horizons."""

    materialize_as_of: str
    expires_as_of: str
    oracle_materialize_as_of: Optional[str]


def choose_replay_horizon(request: ReplayHorizonRequest) -> ReplayHorizonDecision:
    """Choose PIT materialization and expiry horizons using the existing FI policy."""
    oracle_materialize_as_of: Optional[str] = None
    oracle_expiry_as_of: Optional[str] = None
    if request.mode == "official_consolidation":
        reflected_section_original_versions = frozenset(
            str(source_id)
            for source_id in request.oracle_reflected_section_original_versions
            if str(source_id)
        )
        single_future_repeal_overlay_versions = frozenset(
            str(source_id)
            for source_id in request.oracle_single_future_repeal_overlay_versions
            if str(source_id)
        )
        oracle_cutoff_iso: Optional[str] = (
            request.cutoff_date.isoformat() if request.cutoff_date is not None else None
        )
        oracle_vid_id = request.oracle_version_amendment_id or ""
        compiled_ops = tuple(request.compiled_ops)
        oracle_vid_repeal_only_future = oracle_version_future_repeal_only_uses_cutoff_date(
            compiled_ops=compiled_ops,
            oracle_version_amendment_id=oracle_vid_id,
            oracle_cutoff_iso=oracle_cutoff_iso,
        )
        oracle_vid_mixed_repeal_future = _oracle_version_has_mixed_repeal_and_non_repeal_ops(
            compiled_ops=compiled_ops,
            oracle_version_amendment_id=oracle_vid_id,
        ) and oracle_vid_id in single_future_repeal_overlay_versions
        oracle_dates: list[str] = []
        for rec in request.amendment_records:
            if not bool(rec.get("included", True)):
                continue
            oracle_effective = rec.get("effective_date")
            oracle_issue = rec.get("issue_date")
            rec_sid = rec.get("statute_id", "")
            eff_iso: Optional[str] = None
            if isinstance(oracle_effective, dt.date):
                eff_iso = oracle_effective.isoformat()
            elif isinstance(oracle_effective, str) and oracle_effective:
                eff_iso = oracle_effective
            iss_iso: Optional[str] = None
            if isinstance(oracle_issue, dt.date):
                iss_iso = oracle_issue.isoformat()
            elif isinstance(oracle_issue, str) and oracle_issue:
                iss_iso = oracle_issue
            date_for_oracle = eff_iso or iss_iso
            if date_for_oracle is None:
                continue
            if (
                rec_sid != oracle_vid_id
                and oracle_cutoff_iso is not None
                and iss_iso is not None
                and iss_iso >= oracle_cutoff_iso
                and eff_iso is not None
                and eff_iso > oracle_cutoff_iso
                and rec_sid not in reflected_section_original_versions
            ):
                continue
            if (
                rec_sid == oracle_vid_id
                and oracle_vid_repeal_only_future
                and oracle_cutoff_iso is not None
                and eff_iso is not None
                and eff_iso > oracle_cutoff_iso
            ):
                date_for_oracle = oracle_cutoff_iso
            if (
                rec_sid == oracle_vid_id
                and oracle_vid_mixed_repeal_future
                and oracle_cutoff_iso is not None
                and eff_iso is not None
                and eff_iso > oracle_cutoff_iso
            ):
                date_for_oracle = oracle_cutoff_iso
            oracle_dates.append(date_for_oracle)
        if oracle_dates:
            oracle_materialize_as_of = max(oracle_dates)

        if oracle_vid_id:
            for lo in request.legal_operations:
                lo_src = lo.source
                if lo_src is None or lo_src.statute_id != oracle_vid_id:
                    continue
                lo_eff = lo_src.effective
                if not lo_eff:
                    continue
                is_repeal_like = _is_repeal_like_legal_operation(lo)
                if not is_repeal_like and lo_src.statute_id not in reflected_section_original_versions:
                    continue
                if (
                    oracle_vid_mixed_repeal_future
                    and oracle_cutoff_iso is not None
                    and lo_eff > oracle_cutoff_iso
                    and lo_src.statute_id == oracle_vid_id
                ):
                    continue
                if oracle_materialize_as_of is None or lo_eff > oracle_materialize_as_of:
                    oracle_materialize_as_of = lo_eff
                    if (
                        _can_detach_expiry_for_future_repeal(lo)
                        and oracle_vid_repeal_only_future
                        and oracle_cutoff_iso is not None
                        and lo_eff > oracle_cutoff_iso
                    ):
                        oracle_expiry_as_of = oracle_cutoff_iso
                    op_family = "REPEAL" if is_repeal_like else "non-REPEAL"
                    request.replay_print(
                        f"  oracle_materialize_as_of extended to {lo_eff}"
                        f" by {op_family} op {lo.op_id!r} from {oracle_vid_id}"
                    )

    if request.as_of:
        materialize_as_of = request.as_of
    elif request.mode == "legal_pit" and request.cutoff_date is not None:
        materialize_as_of = request.cutoff_date.isoformat()
    elif request.mode == "official_consolidation" and oracle_materialize_as_of is not None:
        materialize_as_of = oracle_materialize_as_of
    elif request.mode == "official_consolidation" and request.cutoff_date is not None:
        materialize_as_of = request.cutoff_date.isoformat()
    else:
        materialize_as_of = "9999-12-31"

    expires_as_of = ""
    if request.mode == "official_consolidation":
        if oracle_expiry_as_of is not None:
            expires_as_of = oracle_expiry_as_of
        elif oracle_materialize_as_of is not None:
            expires_as_of = oracle_materialize_as_of
        elif request.cutoff_date is not None:
            expires_as_of = request.cutoff_date.isoformat()
        else:
            expires_as_of = dt.date.today().isoformat()

    return ReplayHorizonDecision(
        materialize_as_of=materialize_as_of,
        expires_as_of=expires_as_of,
        oracle_materialize_as_of=oracle_materialize_as_of,
    )
