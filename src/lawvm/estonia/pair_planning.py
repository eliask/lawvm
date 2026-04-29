"""Typed EE base/oracle pair planning for PIT replay."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from typing import Any, Optional

from lawvm.estonia.fetch import (
    AmendmentRef,
    extract_amendment_refs,
    extract_effective_date,
    extract_grupi_id,
    extract_tekstiliik,
    fetch_rt_xml,
    get_oracle_aktviide_for_pit,
    normalize_aktviide,
)
from lawvm.replay_adjudication import SourceAdjudication
from lawvm.estonia.source_adjudication import (
    EESourceBasis,
    build_ee_source_adjudication,
    classify_ee_oracle_pair,
    classify_ee_source_basis,
)

_EE_MUUTMISMARGE_AKTVIIDE_PUBLICATION_YEAR_REPAIR_RULE = (
    "ee_muutmismarge_aktviide_publication_year_repair"
)
_EE_MUUTMISMARGE_AKTVIIDE_PUBLICATION_NUMBER_REPAIR_RULE = (
    "ee_muutmismarge_aktviide_publication_number_repair"
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


def _repair_muutmismarge_publication_year_refs(
    refs: tuple[AmendmentRef, ...],
    *,
    archive: Any = None,
) -> tuple[tuple[AmendmentRef, ...], tuple[dict[str, str], ...]]:
    """Repair impossible RT muutmismarge act-id years using the passed-date witness."""
    repaired_refs: list[AmendmentRef] = []
    findings: list[dict[str, str]] = []
    for ref in refs:
        aid = ref.aktViide
        passed_year = ref.passed[:4] if ref.passed else ""
        if (
            len(aid) == 12
            and aid.isdigit()
            and aid.startswith("1")
            and passed_year.isdigit()
            and aid[5:9].isdigit()
            and aid[5:9] < passed_year
        ):
            candidate = f"{aid[:5]}{passed_year}{aid[9:]}"
            try:
                fetch_rt_xml(candidate, archive)
            except Exception:
                repaired_refs.append(ref)
                continue
            repaired_refs.append(replace(ref, aktViide=candidate))
            findings.append(
                {
                    "kind": "source_pathology",
                    "rule": _EE_MUUTMISMARGE_AKTVIIDE_PUBLICATION_YEAR_REPAIR_RULE,
                    "original_aktViide": aid,
                    "repaired_aktViide": candidate,
                    "passed": ref.passed,
                    "joustumine": ref.joustumine,
                }
            )
            continue
        repaired_refs.append(ref)
    return tuple(repaired_refs), tuple(findings)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(element: ET.Element, child_name: str) -> str:
    for child in element:
        if _local_name(child.tag) == child_name:
            return (child.text or "").strip()
    return ""


def _nested_child_text(element: ET.Element, *child_names: str) -> str:
    current = element
    for child_name in child_names:
        next_child = next((child for child in current if _local_name(child.tag) == child_name), None)
        if next_child is None:
            return ""
        current = next_child
    return (current.text or "").strip()


def _publication_candidate_aktviide(muutmismarge: ET.Element) -> str:
    note_text = " ".join(text.strip() for text in muutmismarge.itertext() if text and text.strip())
    match = re.search(r"\bRT\s+I\s+(\d{4})-(\d{2})-(\d{2})\s+(\d+)\b", note_text)
    if match is None:
        return ""
    year, month, day, number = match.groups()
    return f"1{day}{month}{year}{number.zfill(3)}"


def _repair_muutmismarge_publication_number_refs(
    xml_bytes: bytes,
    refs: tuple[AmendmentRef, ...],
    *,
    archive: Any = None,
) -> tuple[tuple[AmendmentRef, ...], tuple[dict[str, str], ...]]:
    """Repair unfetchable RT refs when the publication citation gives the act id."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return refs, ()
    publication_candidates: dict[tuple[str, str], str] = {}
    for muutmismarge in root.iter():
        if _local_name(muutmismarge.tag) != "muutmismarge":
            continue
        aid = normalize_aktviide(_nested_child_text(muutmismarge, "avaldamismarge", "aktViide"))
        joustumine = _child_text(muutmismarge, "joustumine")
        passed = _child_text(muutmismarge, "aktikuupaev")
        candidate = _publication_candidate_aktviide(muutmismarge)
        if aid and candidate and candidate != aid:
            publication_candidates[(aid, joustumine or passed)] = candidate

    repaired_refs: list[AmendmentRef] = []
    findings: list[dict[str, str]] = []
    for ref in refs:
        candidate = publication_candidates.get(_ref_slice_key(ref), "")
        if not candidate:
            repaired_refs.append(ref)
            continue
        try:
            fetch_rt_xml(ref.aktViide, archive)
        except Exception:
            try:
                fetch_rt_xml(candidate, archive)
            except Exception:
                repaired_refs.append(ref)
                continue
            repaired_refs.append(replace(ref, aktViide=candidate))
            findings.append(
                {
                    "kind": "source_pathology",
                    "rule": _EE_MUUTMISMARGE_AKTVIIDE_PUBLICATION_NUMBER_REPAIR_RULE,
                    "original_aktViide": ref.aktViide,
                    "repaired_aktViide": candidate,
                    "passed": ref.passed,
                    "joustumine": ref.joustumine,
                }
            )
            continue
        repaired_refs.append(ref)
    return tuple(repaired_refs), tuple(findings)


@dataclass(frozen=True)
class EEOraclePairPlan:
    """Typed pair-selection/adjudication product for one EE PIT replay."""

    base_id: str
    as_of: str
    grupi_id: Optional[str]
    oracle_id: Optional[str]
    oracle_grupi_id: Optional[str]
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

    oracle_grupi_id = extract_grupi_id(oracle_xml) if oracle_xml is not None else None
    group_mismatch = bool(
        selected_oracle_id
        and selected_oracle_id != base_id
        and grupi_id
        and oracle_grupi_id
        and oracle_grupi_id != grupi_id
    )
    base_is_consolidated = extract_tekstiliik(base_xml) == "terviktekst"
    base_effective = extract_effective_date(base_xml) if base_is_consolidated else ""
    base_refs, base_ref_repair_findings = _repair_muutmismarge_publication_year_refs(
        _dedupe_refs_by_slice(tuple(extract_amendment_refs(base_xml))),
        archive=archive,
    )
    base_refs, base_ref_number_repair_findings = _repair_muutmismarge_publication_number_refs(
        base_xml,
        base_refs,
        archive=archive,
    )
    base_slice_keys = {_ref_slice_key(ref) for ref in base_refs}
    oracle_refs: tuple[AmendmentRef, ...] = ()
    oracle_ref_repair_findings: tuple[dict[str, str], ...] = ()
    oracle_ref_number_repair_findings: tuple[dict[str, str], ...] = ()

    if (
        base_is_consolidated
        and oracle_xml is not None
        and selected_oracle_id != base_id
        and not group_mismatch
    ):
        try:
            oracle_refs, oracle_ref_repair_findings = _repair_muutmismarge_publication_year_refs(
                _dedupe_refs_by_slice(tuple(extract_amendment_refs(oracle_xml))),
                archive=archive,
            )
            oracle_refs, oracle_ref_number_repair_findings = _repair_muutmismarge_publication_number_refs(
                oracle_xml,
                oracle_refs,
                archive=archive,
            )
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
    if group_mismatch:
        comparison_class = "cross_statute_oracle_mismatch"
        source_basis = EESourceBasis.NONCOMMENSURABLE
    else:
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
        *base_ref_repair_findings,
        *base_ref_number_repair_findings,
        *oracle_ref_repair_findings,
        *oracle_ref_number_repair_findings,
        {
            "kind": "ee_pair_classification",
            "source_basis": source_basis.value,
            "comparison_class": comparison_class,
            "base_is_consolidated": base_is_consolidated,
            "base_grupi_id": grupi_id or "",
            "oracle_grupi_id": oracle_grupi_id or "",
            "rule": "ee_oracle_group_mismatch" if group_mismatch else "",
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
            oracle_grupi_id=oracle_grupi_id,
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
