"""Typed EE base/oracle pair planning for PIT replay."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from lawvm.estonia.fetch import (
    AmendmentRef,
    extract_amendment_refs,
    extract_effective_date,
    extract_grupi_id,
    extract_tekstiliik,
    fetch_rt_xml,
    get_oracle_aktviide_for_pit,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.estonia.source_adjudication import (
    EESourceBasis,
    build_ee_source_adjudication,
    classify_ee_oracle_pair,
    classify_ee_source_basis,
)


def _ref_slice_key(ref: AmendmentRef) -> tuple[str, str]:
    """Return the identity of one RT amendment-effect slice."""
    return (ref.aktViide, ref.joustumine or ref.passed)


def _dedupe_refs_by_slice(refs: tuple[AmendmentRef, ...] | list[AmendmentRef]) -> tuple[AmendmentRef, ...]:
    """Return refs without duplicate (act, effective-date) slices."""
    seen: set[tuple[str, str]] = set()
    deduped: list[AmendmentRef] = []
    for ref in _sort_refs(list(refs)):
        key = _ref_slice_key(ref)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return tuple(deduped)


def _unique_ref_ids(refs: tuple[AmendmentRef, ...] | list[AmendmentRef]) -> tuple[str, ...]:
    """Return amendment ids in stable sorted order without duplicates."""
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in _sort_refs(list(refs)):
        if ref.aktViide in seen:
            continue
        seen.add(ref.aktViide)
        ordered.append(ref.aktViide)
    return tuple(ordered)


def _sort_refs(refs: list[AmendmentRef]) -> tuple[AmendmentRef, ...]:
    """Return refs in deterministic legal-order-friendly order."""
    return tuple(
        sorted(
            refs,
            key=lambda ref: (
                ref.joustumine,
                ref.passed,
                ref.aktViide,
            ),
        )
    )


def _effective_pending_base_refs(
    *,
    base_refs: tuple[AmendmentRef, ...],
    base_effective: str,
    as_of: str,
) -> tuple[AmendmentRef, ...]:
    """Return delayed-effect refs already embedded in a consolidated base."""
    if not base_effective:
        return ()
    return _dedupe_refs_by_slice(
        [
            ref
            for ref in base_refs
            if ref.joustumine and base_effective < ref.joustumine <= as_of
        ]
    )


@dataclass(frozen=True)
class EEOraclePairPlan:
    """Typed pair-selection/adjudication product for one EE PIT replay."""

    base_id: str
    as_of: str
    grupi_id: Optional[str]
    oracle_id: Optional[str]
    base_is_consolidated: bool
    oracle_is_base: bool
    source_basis: EESourceBasis
    comparison_class: str
    base_refs: tuple[AmendmentRef, ...]
    oracle_refs: tuple[AmendmentRef, ...]
    amendments_to_apply: tuple[AmendmentRef, ...]
    effective_new_amendments: tuple[str, ...]
    future_new_amendments: tuple[str, ...]
    source_adjudication: SourceAdjudication


@dataclass(frozen=True)
class EEPairPlanningResult:
    """Pair-planning stage output plus the raw oracle XML when available."""

    plan: EEOraclePairPlan
    oracle_xml: bytes | None = None


def plan_ee_oracle_pair(
    *,
    base_id: str,
    as_of: str,
    base_xml: bytes,
    archive: Any = None,
    oracle_id: Optional[str] = None,
) -> EEPairPlanningResult:
    """Plan the EE replay/oracle pair before amendment replay begins."""
    grupi_id = extract_grupi_id(base_xml)
    selected_oracle_id = oracle_id
    oracle_xml: bytes | None = None

    if selected_oracle_id is None and grupi_id:
        selected_oracle_id = get_oracle_aktviide_for_pit(grupi_id, as_of, archive)

    if selected_oracle_id == base_id:
        oracle_xml = base_xml
    elif selected_oracle_id:
        try:
            oracle_xml = fetch_rt_xml(selected_oracle_id, archive)
        except Exception:
            oracle_xml = None

    base_is_consolidated = extract_tekstiliik(base_xml) == "terviktekst"
    base_effective = extract_effective_date(base_xml) if base_is_consolidated else ""
    base_refs = _dedupe_refs_by_slice(tuple(extract_amendment_refs(base_xml)))
    base_slice_keys = {_ref_slice_key(ref) for ref in base_refs}
    oracle_refs: tuple[AmendmentRef, ...] = ()

    if base_is_consolidated and oracle_xml is not None and selected_oracle_id != base_id:
        try:
            oracle_refs = _dedupe_refs_by_slice(tuple(extract_amendment_refs(oracle_xml)))
        except Exception:
            oracle_refs = ()

    if base_is_consolidated and oracle_refs:
        pending_base_refs = _effective_pending_base_refs(
            base_refs=base_refs,
            base_effective=base_effective,
            as_of=as_of,
        )
        effective_new_refs = tuple(
            ref
            for ref in oracle_refs
            if _ref_slice_key(ref) not in base_slice_keys and ref.joustumine and ref.joustumine <= as_of
        )
        amendments_to_apply = _dedupe_refs_by_slice(
            [*pending_base_refs, *effective_new_refs]
        )
    elif base_is_consolidated:
        amendments_to_apply = _effective_pending_base_refs(
            base_refs=base_refs,
            base_effective=base_effective,
            as_of=as_of,
        )
    else:
        amendments_to_apply = _sort_refs(
            [ref for ref in base_refs if ref.joustumine and ref.joustumine <= as_of]
        )

    effective_new_refs = _dedupe_refs_by_slice(
        [
            ref
            for ref in oracle_refs
            if _ref_slice_key(ref) not in base_slice_keys and ref.joustumine and ref.joustumine <= as_of
        ]
    )
    future_new_refs = _dedupe_refs_by_slice(
        [
            ref
            for ref in oracle_refs
            if _ref_slice_key(ref) not in base_slice_keys and ref.joustumine and ref.joustumine > as_of
        ]
    )
    effective_new_amendments = _unique_ref_ids(effective_new_refs)
    future_new_amendments = _unique_ref_ids(future_new_refs)
    same_chain = bool(oracle_refs) and {_ref_slice_key(ref) for ref in oracle_refs} == base_slice_keys
    comparison_class = classify_ee_oracle_pair(
        has_oracle=oracle_xml is not None,
        base_is_consolidated=base_is_consolidated,
        oracle_matches_base=selected_oracle_id == base_id,
        effective_new_amendments=effective_new_amendments,
        future_new_amendments=future_new_amendments,
        same_chain=same_chain,
    )
    source_basis = classify_ee_source_basis(
        has_oracle=oracle_xml is not None,
        base_is_consolidated=base_is_consolidated,
        oracle_matches_base=selected_oracle_id == base_id,
        effective_new_amendments=effective_new_amendments,
        future_new_amendments=future_new_amendments,
        same_chain=same_chain,
    )

    lineage = [
        {
            "kind": "ee_pair_classification",
            "source_basis": source_basis.value,
            "comparison_class": comparison_class,
            "base_is_consolidated": base_is_consolidated,
            "base_amendment_count": len(base_refs),
            "oracle_amendment_count": len(oracle_refs),
            "effective_new_amendments": list(effective_new_amendments),
            "future_new_amendments": list(future_new_amendments),
            "effective_new_amendment_slices": [
                {"aktViide": ref.aktViide, "joustumine": ref.joustumine}
                for ref in effective_new_refs
            ],
            "future_new_amendment_slices": [
                {"aktViide": ref.aktViide, "joustumine": ref.joustumine}
                for ref in future_new_refs
            ],
        }
    ]
    source_adjudication = build_ee_source_adjudication(
        statute_id=f"ee/{base_id}",
        base_id=base_id,
        oracle_id=selected_oracle_id or "",
        as_of=as_of,
        comparison_class=comparison_class,
        lineage=lineage,
    )

    return EEPairPlanningResult(
        plan=EEOraclePairPlan(
            base_id=base_id,
            as_of=as_of,
            grupi_id=grupi_id,
            oracle_id=selected_oracle_id,
            base_is_consolidated=base_is_consolidated,
            oracle_is_base=selected_oracle_id == base_id,
            source_basis=source_basis,
            comparison_class=comparison_class,
            base_refs=base_refs,
            oracle_refs=oracle_refs,
            amendments_to_apply=amendments_to_apply,
            effective_new_amendments=effective_new_amendments,
            future_new_amendments=future_new_amendments,
            source_adjudication=source_adjudication,
        ),
        oracle_xml=oracle_xml,
    )


__all__ = [
    "EEOraclePairPlan",
    "EEPairPlanningResult",
    "plan_ee_oracle_pair",
]
