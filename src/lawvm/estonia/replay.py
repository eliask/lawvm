"""Estonia point-in-time amendment replay pipeline.

Public API:
  replay_ee_to_pit(base_id, as_of, archive, verbose) → EEPitResult

Full e2e flow:
  1. Fetch base act XML (by aktViide or local path)
  2. Read terviktekstiGrupiID → fetch redactions feed → select oracle for as_of
  3. Read muutmismarge → get all AmendmentRefs with effective dates
  4. Fetch + parse amendment ops (joustumine ≤ as_of only)
  5. Apply ops in chronological order via apply_ee_ops()
  6. Compare replayed state to RT oracle via verify_consistency()
  7. Return EEPitResult

All HTTP I/O goes through a Farchive (content-addressed archive).
"""
from __future__ import annotations

import sys
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, List, Optional

from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.filter_result import FilterResult, RejectedItem, filter_result_from_parts
from lawvm.core.source_lane import SourceLaneAttempt, SourceLaneSelectionEvidence
from lawvm.core.temporal import TemporalEvent, TemporalScope
from lawvm.replay_adjudication import CompileAdjudication, SourceAdjudication
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource, ProvisionTimeline, StructuralAction
from lawvm.core.replay_contracts import ReplaySummary
from lawvm.core.timeline import compile_timelines, materialize_pit
from lawvm.core.timeline_consistency import ingest_consolidated, verify_consistency
from lawvm.estonia.grafter import (
    _extract_intro_statute_fragment,
    _first_tavatekst_text,
    _normalize_num,
    _old_format_commencement_date,
    _strict_title_match_para,
    _title_matches_para,
    apply_ee_ops,
    apply_ee_ops_conserved,
    parse_ee_amendment_ops,
    parse_ee_statute,
)
from lawvm.estonia.peg import _attribute_generic_structural_ops, parse_html_op_items
from lawvm.estonia.pair_planning import EEOraclePairPlan, plan_ee_oracle_pair
from lawvm.estonia.compare import irnode_to_ee_comparison_text, normalize_ee_comparison_text
from lawvm.estonia.fetch import (
    AmendmentRef,
    fetch_rt_xml,
    open_rt_archive,
)
from lawvm.core.quirks_disposition import QuirksDisposition

# Expected, non-fatal failure modes of a best-effort RT source fetch: the act
# does not resolve (fetch_rt_xml → RuntimeError "Failed to fetch: …"), or the
# Farchive is read-only and cannot cache a freshly-curled blob
# (sqlite3.OperationalError "attempt to write a readonly database", e.g.
# inspection/test contexts). Both mean "this amendment source is unavailable"
# and are recorded with a typed source-lane adjudication; any OTHER exception is
# an unexpected bug and must propagate (AGENTS.md §1.10).
_EE_AMENDMENT_FETCH_EXPECTED_ERRORS = (RuntimeError, sqlite3.OperationalError)


def _ee_ref_sort_key(ref) -> tuple[str, str, str]:
    return (ref.joustumine, ref.passed, ref.aktViide)


def _ee_xml_ns(root: ET.Element[str]) -> str:
    return root.tag.split("}")[0].strip("{")


def _ee_fetch_rt_xml_cached(
    akt_viide: str,
    archive: Any,
    successful_xml_cache: dict[str, bytes] | None,
) -> bytes:
    if successful_xml_cache is not None and akt_viide in successful_xml_cache:
        return successful_xml_cache[akt_viide]
    xml_bytes = fetch_rt_xml(akt_viide, archive)
    if successful_xml_cache is not None:
        successful_xml_cache[akt_viide] = xml_bytes
    return xml_bytes


def _ee_extract_act_title(
    xml_bytes: bytes,
    *,
    akt_viide: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    # lawvm-failloud (AGENTS.md §1.10): the only genuinely-expected failure of
    # ET.fromstring on fetched RT source is ET.ParseError (malformed XML). The
    # prefilter records a typed parse-failed adjudication and conservatively
    # returns empty; any other exception is an unexpected bug and must propagate.
    except ET.ParseError as exc:
        _ee_emit_prefilter_parse_failed_adjudication(
            rule_id=_EE_EXTRACT_ACT_TITLE_PARSE_FAILED_RULE,
            failure=exc,
            akt_viide=akt_viide or "unknown",
            adjudications_out=adjudications_out,
            message=(
                "Estonia pending-amendment prefilter skipped act-title "
                "extraction because the fetched source XML failed to parse."
            ),
            reason="act_title_source_xml_parse_failed",
        )
        return ""
    ns = _ee_xml_ns(root)
    aktinimi = root.find(f"{{{ns}}}aktinimi")
    if aktinimi is None:
        return ""
    nimi = aktinimi.find(f"{{{ns}}}nimi")
    if nimi is None:
        return ""
    pealkiri = nimi.find(f"{{{ns}}}pealkiri")
    return (pealkiri.text or "").strip() if pealkiri is not None and pealkiri.text else ""


_EE_MONTH_PREFIXES: tuple[tuple[str, int], ...] = (
    ("jaanuar", 1),
    ("veebruar", 2),
    ("märts", 3),
    ("aprill", 4),
    ("mai", 5),
    ("juuni", 6),
    ("juuli", 7),
    ("august", 8),
    ("septemb", 9),
    ("oktoob", 10),
    ("novemb", 11),
    ("detsemb", 12),
)


def _ee_month_number(raw_month: str) -> int | None:
    normalized = raw_month.strip().lower()
    if normalized.endswith("ni"):
        normalized = normalized[:-2]
    for prefix, number in _EE_MONTH_PREFIXES:
        if normalized.startswith(prefix):
            return number
    return None


def _ee_exclusive_date_after_until(year: str, day: str, month: str) -> str | None:
    month_number = _ee_month_number(month)
    if month_number is None:
        return None
    return (date(int(year), month_number, int(day)) + timedelta(days=1)).isoformat()


def _derive_ee_temporal_expiry_events(
    ops: list[LegalOperation],
    *,
    target_statute: str,
) -> tuple[TemporalEvent, ...]:
    """Lower explicit ``kehtib kuni`` provision clauses into temporal expiry events."""
    events: list[TemporalEvent] = []
    seen: set[tuple[tuple[tuple[str, str], ...], str, str]] = set()
    expiry_pattern = re.compile(
        r"§\s*("
        r"\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*"
        r")\s+l[oõ]ige\s+("
        r"\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*"
        r")\s+kehtib\s+kuni\s+(\d{4})\.\s*aasta\s+(\d{1,2})\.\s+([A-Za-zÕÄÖÜŠŽõäöüšž]+)",
        re.IGNORECASE,
    )
    for op in ops:
        payload_text = op.payload.text if op.payload is not None else ""
        source_text = op.source.raw_text if op.source is not None else ""
        witness_text = " ".join(part for part in (payload_text, source_text) if part)
        if "kehtib kuni" not in witness_text:
            continue
        for match in expiry_pattern.finditer(witness_text):
            section = _normalize_num(match.group(1))
            subsection = _normalize_num(match.group(2))
            expires = _ee_exclusive_date_after_until(
                match.group(3),
                match.group(4),
                match.group(5),
            )
            if expires is None:
                continue
            address = LegalAddress(path=(("section", section), ("subsection", subsection)))
            key = (address.path, expires, op.source.statute_id if op.source is not None else "")
            if key in seen:
                continue
            seen.add(key)
            event_source = op.source
            if event_source is not None:
                event_source = replace(event_source, expires=expires)
            events.append(
                TemporalEvent(
                    event_id=(
                        f"ee-expire-{section}-{subsection}-{expires}-"
                        f"{op.source.statute_id if op.source is not None else op.op_id}"
                    ),
                    kind="expire",
                    scope=TemporalScope(
                        target_statute=target_statute,
                        address_prefixes=(address,),
                        include_future_descendants=True,
                    ),
                    expires=expires,
                    source=event_source,
                    group_id=f"ee-expiry:{op.op_id}",
                )
            )
    return tuple(events)


def _unique_ee_refs(refs: tuple[AmendmentRef, ...] | list[AmendmentRef]) -> tuple[AmendmentRef, ...]:
    seen: set[tuple[str, str]] = set()
    unique: list[AmendmentRef] = []
    for ref in refs:
        key = (ref.aktViide, ref.joustumine)
        if key in seen:
            continue
        seen.add(key)
        unique.append(ref)
    return tuple(unique)


def _ee_suffix_address_matches(statute: IRStatute, suffix: LegalAddress) -> tuple[LegalAddress, ...]:
    matches: list[LegalAddress] = []
    suffix_len = len(suffix.path)
    if suffix_len == 0:
        return ()

    def _walk(node, path: tuple[tuple[str, str], ...]) -> None:
        for child in node.children:
            child_path = path + ((child.kind.value, child.label or ""),)
            if child_path[-suffix_len:] == suffix.path:
                matches.append(LegalAddress(path=child_path, special=suffix.special))
            _walk(child, child_path)

    _walk(statute.body, ())
    return tuple(matches)


def _resolve_ee_temporal_event_scopes(
    events: tuple[TemporalEvent, ...],
    statute: IRStatute,
) -> tuple[TemporalEvent, ...]:
    resolved_events: list[TemporalEvent] = []
    for event in events:
        resolved_prefixes: list[LegalAddress] = []
        for prefix in event.scope.address_prefixes:
            matches = _ee_suffix_address_matches(statute, prefix)
            if matches:
                resolved_prefixes.extend(matches)
            else:
                resolved_prefixes.append(prefix)
        if not resolved_prefixes:
            resolved_events.append(event)
            continue
        resolved_events.append(
            replace(
                event,
                scope=TemporalScope(
                    target_statute=event.scope.target_statute,
                    exact_addresses=event.scope.exact_addresses,
                    address_prefixes=tuple(resolved_prefixes),
                    predicates=event.scope.predicates,
                    include_future_descendants=event.scope.include_future_descendants,
                ),
            )
        )
    return tuple(resolved_events)


def _ee_extract_target_matching_paragraph_numbers(
    xml_bytes: bytes,
    target_title: str,
    *,
    akt_viide: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    # lawvm-failloud (AGENTS.md §1.10): only ET.ParseError (malformed RT XML) is
    # the genuinely-expected failure; the prefilter records a typed parse-failed
    # adjudication and conservatively returns empty. Other exceptions propagate.
    except ET.ParseError as exc:
        _ee_emit_prefilter_parse_failed_adjudication(
            rule_id=_EE_EXTRACT_TARGET_MATCHING_PARAGRAPHS_PARSE_FAILED_RULE,
            failure=exc,
            akt_viide=akt_viide or "unknown",
            adjudications_out=adjudications_out,
            message=(
                "Estonia cancelled-pending-ref prefilter skipped paragraph-"
                "matching extraction because the fetched source XML failed "
                "to parse."
            ),
            reason="target_matching_paragraphs_source_xml_parse_failed",
        )
        return set()
    ns = _ee_xml_ns(root)
    matches: set[str] = set()
    for para in root.iter(f"{{{ns}}}paragrahv"):
        para_nr = para.findtext(f"{{{ns}}}paragrahvNr") or ""
        para_title = para.findtext(f"{{{ns}}}paragrahvPealkiri") or ""
        first_tava = _first_tavatekst_text(para, ns)
        stat_fragment = _extract_intro_statute_fragment(first_tava)
        if para_nr and (
            (para_title and _strict_title_match_para(target_title, para_title))
            or (not para_title and stat_fragment and _title_matches_para(target_title, stat_fragment))
        ):
            matches.add(para_nr.strip())
    return matches


def _ee_source_surface_may_target_title(xml_bytes: bytes, target_title: str) -> bool:
    """Conservative prefilter for expensive source-targeted reparsing.

    A parse failure returns true: this helper may only skip clearly unrelated
    source surfaces, never hide uncertainty.
    """
    if not target_title.strip():
        return False
    try:
        root = ET.fromstring(xml_bytes)
    # lawvm-failloud (AGENTS.md §1.10): a malformed-XML ET.ParseError is the
    # expected failure; this conservative prefilter returns True (never hides
    # uncertainty — see docstring). Any other exception is an unexpected bug and
    # must propagate rather than be absorbed into a spurious "may target".
    except ET.ParseError:
        return True
    ns = _ee_xml_ns(root)
    act_title = _ee_extract_act_title(xml_bytes)
    if act_title and _title_matches_para(target_title, act_title):
        return True
    for para in root.iter(f"{{{ns}}}paragrahv"):
        para_title = para.findtext(f"{{{ns}}}paragrahvPealkiri") or ""
        first_tava = _first_tavatekst_text(para, ns)
        stat_fragment = _extract_intro_statute_fragment(first_tava)
        if para_title and (
            _strict_title_match_para(target_title, para_title)
            or _title_matches_para(target_title, para_title)
        ):
            return True
        if stat_fragment and _title_matches_para(target_title, stat_fragment):
            return True
        if first_tava and _title_matches_para(target_title, first_tava):
            return True
    body_text = " ".join(part.strip() for part in root.itertext() if part.strip())
    if body_text and _title_matches_para(target_title, body_text):
        return True
    return False


def _ee_extract_repealed_source_paragraph_numbers(
    xml_bytes: bytes,
    amended_act_title: str,
    *,
    akt_viide: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    # lawvm-failloud (AGENTS.md §1.10): only ET.ParseError (malformed RT XML) is
    # the genuinely-expected failure; the prefilter records a typed parse-failed
    # adjudication and conservatively returns empty. Other exceptions propagate.
    except ET.ParseError as exc:
        _ee_emit_prefilter_parse_failed_adjudication(
            rule_id=_EE_EXTRACT_REPEALED_SOURCE_PARAGRAPHS_PARSE_FAILED_RULE,
            failure=exc,
            akt_viide=akt_viide or "unknown",
            adjudications_out=adjudications_out,
            message=(
                "Estonia cancelled-pending-ref prefilter skipped repealed-section "
                "extraction because the fetched source XML failed to parse."
            ),
            reason="repealed_source_paragraphs_source_xml_parse_failed",
        )
        return set()
    ns = _ee_xml_ns(root)
    repealed: set[str] = set()
    for para in root.iter(f"{{{ns}}}paragrahv"):
        para_title = (para.findtext(f"{{{ns}}}paragrahvPealkiri") or "").strip()
        first_tava = _first_tavatekst_text(para, ns)
        if not para_title or not first_tava:
            continue
        if not _strict_title_match_para(amended_act_title, para_title):
            continue
        if "jäetakse välja" not in first_tava.lower():
            continue
        prefix = first_tava.split("jäetakse välja", 1)[0]
        for sec_chunk in re.findall(r'§[^§]+', prefix):
            numbers = re.findall(r'\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*', sec_chunk)
            if not numbers:
                continue
            repealed.add(numbers[0].strip())
            if "lõige" not in sec_chunk and "punkt" not in sec_chunk:
                for extra in numbers[1:]:
                    repealed.add(extra.strip())
    return repealed


def _ee_extract_rewritten_source_paragraph_numbers(
    xml_bytes: bytes,
    amended_act_title: str,
    *,
    akt_viide: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    # lawvm-failloud (AGENTS.md §1.10): only ET.ParseError (malformed RT XML) is
    # the genuinely-expected failure; the prefilter records a typed parse-failed
    # adjudication and conservatively returns empty. Other exceptions propagate.
    except ET.ParseError as exc:
        _ee_emit_prefilter_parse_failed_adjudication(
            rule_id=_EE_EXTRACT_REWRITTEN_SOURCE_PARAGRAPHS_PARSE_FAILED_RULE,
            failure=exc,
            akt_viide=akt_viide or "unknown",
            adjudications_out=adjudications_out,
            message=(
                "Estonia cancelled-pending-ref prefilter skipped rewritten-section "
                "extraction because the fetched source XML failed to parse."
            ),
            reason="rewritten_source_paragraphs_source_xml_parse_failed",
        )
        return set()
    ns = _ee_xml_ns(root)
    rewritten: set[str] = set()
    for para in root.iter(f"{{{ns}}}paragrahv"):
        para_title = (para.findtext(f"{{{ns}}}paragrahvPealkiri") or "").strip()
        first_tava = _first_tavatekst_text(para, ns)
        if not para_title and not first_tava:
            continue
        if not _strict_title_match_para(amended_act_title, para_title or first_tava):
            continue
        texts: list[str] = []
        if first_tava:
            texts.append(first_tava)
        for st in para.iter(f"{{{ns}}}sisuTekst"):
            for hk in st.findall(f"{{{ns}}}HTMLKonteiner"):
                texts.extend(parse_html_op_items(hk.text or ""))
            for t in st.findall(f"{{{ns}}}tavatekst"):
                txt = " ".join(str(_t) for _t in t.itertext()).replace('\xa0', ' ')
                txt = re.sub(r'\s+', ' ', txt).strip()
                if txt:
                    texts.append(txt)
        for txt in texts:
            plain = re.sub(r'^\(?\d[\d\s_]*\)\s*', '', txt).strip()
            for match in re.finditer(
                r'\bparagrahvi\s+(\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*)\b'
                r'(?:(?!\bparagrahvi\b).){0,120}\btekst(?:i)?\b'
                r'(?:(?!\bparagrahvi\b).){0,120}\bmuudetakse\b',
                plain,
                re.IGNORECASE | re.DOTALL,
            ):
                rewritten.add(_normalize_num(match.group(1).strip()))
    return rewritten


_EE_CANCELLED_PENDING_REF_FILTER_RULE = "ee_cancelled_pending_amendment_ref_filtered"
_EE_CANCELLED_PENDING_REF_FETCH_FAILED_RULE = "ee_cancelled_pending_ref_source_fetch_failed"
_EE_CANCELLED_PENDING_REF_METADATA_PARSE_FAILED_RULE = "ee_cancelled_pending_ref_metadata_parse_failed"
_EE_REF_SLICE_OP_FILTER_RULE = "ee_ref_slice_operation_filtered"
_EE_AMENDMENT_SOURCE_FETCH_FAILED_RULE = "ee_amendment_source_fetch_failed"
_EE_AMENDMENT_PARSE_FAILED_RULE = "ee_amendment_parse_failed"
_EE_TEMPORAL_SOURCE_SCAN_FAILED_RULE = "ee_temporal_source_scan_failed"
_EE_PENDING_SOURCE_ACT_COMMENCEMENT_FETCH_FAILED_RULE = "ee_pending_source_act_commencement_source_fetch_failed"
_EE_PENDING_AMENDMENT_METAPASS_PARSE_FAILED_RULE = "ee_pending_amendment_metapass_parse_failed"
_EE_ORACLE_PARSE_FAILED_RULE = "ee_oracle_parse_failed"
_EE_CONSISTENCY_CHECK_FAILED_RULE = "ee_consistency_check_failed"
_EE_EXTRACT_ACT_TITLE_PARSE_FAILED_RULE = "ee_extract_act_title_parse_failed"
_EE_EXTRACT_TARGET_MATCHING_PARAGRAPHS_PARSE_FAILED_RULE = (
    "ee_extract_target_matching_paragraphs_parse_failed"
)
_EE_EXTRACT_REPEALED_SOURCE_PARAGRAPHS_PARSE_FAILED_RULE = (
    "ee_extract_repealed_source_paragraphs_parse_failed"
)
_EE_EXTRACT_REWRITTEN_SOURCE_PARAGRAPHS_PARSE_FAILED_RULE = (
    "ee_extract_rewritten_source_paragraphs_parse_failed"
)


def _ee_rt_xml_source_lane_detail(
    *,
    rule_id: str,
    phase: str,
    reason: str,
    akt_viide: str,
    attempt_status: str,
    selected_lane: str,
    selected: bool,
    blocking: bool = True,
) -> dict[str, Any]:
    locator = f"ee/{akt_viide}"
    return SourceLaneSelectionEvidence(
        rule_id=rule_id,
        phase=phase,
        reason=reason,
        selected_lane=selected_lane,
        selected_locator=locator if selected else "",
        attempts=(
            SourceLaneAttempt(
                lane="riigi_teataja_xml",
                locator=locator,
                lane_attempt_status=attempt_status,
            ),
        ),
        blocking=blocking,
        strict_disposition="block" if blocking else "record",
        quirks_disposition=QuirksDisposition.RECORD,
    ).to_diagnostic_detail()


def _ee_orchestration_adjudication(
    *,
    kind: str,
    message: str,
    source_statute: str,
    detail: dict[str, Any],
    phase: str,
    family: str,
    blocking: bool = False,
    op_id: str = "",
) -> CompileAdjudication:
    local_detail = dict(detail)
    reason = str(local_detail.pop("reason", "") or "")
    normalized_detail = diagnostic_detail(
        rule_id=kind,
        phase=phase,
        family=family,
        reason=reason,
        blocking=blocking,
        detail=local_detail,
    )
    return CompileAdjudication(
        kind=kind,
        message=message,
        source_statute=source_statute,
        op_id=op_id,
        blocking=blocking,
        phase=phase,
        detail=normalized_detail,
    )


def _ee_emit_prefilter_parse_failed_adjudication(
    *,
    rule_id: str,
    failure: BaseException,
    akt_viide: str,
    adjudications_out: list[CompileAdjudication] | None,
    message: str,
    reason: str,
) -> None:
    """Append a non-blocking ``record`` adjudication for an XML-parse failure
    swallowed by a prefilter helper.

    Per AGENTS.md §1.10 / §1.8: prefilter helpers that wrap
    ``xml.etree.ElementTree.fromstring`` in a broad ``except Exception``
    must emit a typed diagnostic before their conservative empty return,
    not silently swallow. Prefilter helpers never widen scope (their
    empty result is a conservative skip), so the disposition is
    non-blocking ``record`` rather than ``block``.
    """
    if adjudications_out is None:
        return
    adjudications_out.append(
        _ee_orchestration_adjudication(
            kind=rule_id,
            message=message,
            source_statute=f"ee/{akt_viide}",
            detail={
                "ref_amendment": akt_viide,
                "reason": reason,
                "exception_type": type(failure).__name__,
                "exception": str(failure),
                "source_lane_selection": _ee_rt_xml_source_lane_detail(
                    rule_id=rule_id,
                    phase="parse",
                    reason=reason,
                    akt_viide=akt_viide,
                    attempt_status="selected_parse_failed",
                    selected_lane="riigi_teataja_xml",
                    selected=True,
                ),
            },
            phase="parse",
            family="source_lane_failure",
            blocking=False,
        )
    )


def _ee_filter_cancelled_pending_refs(
    refs: list[AmendmentRef],
    *,
    target_title: str,
    archive: Any,
    adjudications_out: list[CompileAdjudication] | None = None,
    successful_xml_cache: dict[str, bytes] | None = None,
) -> FilterResult[AmendmentRef]:
    if len(refs) < 2 or not target_title:
        return filter_result_from_parts(accepted_items=tuple(refs))

    ref_xml: dict[str, bytes] = {}
    ref_titles: dict[str, str] = {}
    target_sections: dict[str, set[str]] = {}
    for ref in refs:
        try:
            xml_bytes = _ee_fetch_rt_xml_cached(ref.aktViide, archive, successful_xml_cache)
        # lawvm-failloud (AGENTS.md §1.10): only an expected source-unavailable
        # fetch failure (_EE_AMENDMENT_FETCH_EXPECTED_ERRORS) is recorded here as
        # an incomplete source lane; the ref is conservatively retained. Other
        # exceptions are unexpected bugs and must propagate.
        except _EE_AMENDMENT_FETCH_EXPECTED_ERRORS as exc:
            if adjudications_out is not None:
                adjudications_out.append(
                    _ee_orchestration_adjudication(
                        kind=_EE_CANCELLED_PENDING_REF_FETCH_FAILED_RULE,
                        message=(
                            "Could not fetch an Estonia pending-amendment source while "
                            "checking cancellation by same-commencement later acts; retaining "
                            "the reference and recording the incomplete source lane."
                        ),
                        source_statute=f"ee/{ref.aktViide}",
                        phase="acquisition",
                        family="pending_amendment_cancellation_filter",
                        blocking=True,
                        detail={
                            "ref_amendment": ref.aktViide,
                            "reason": "pending_ref_source_fetch_failed",
                            "exception_type": type(exc).__name__,
                            "source_lane_selection": _ee_rt_xml_source_lane_detail(
                                rule_id=_EE_CANCELLED_PENDING_REF_FETCH_FAILED_RULE,
                                phase="acquisition",
                                reason="pending_ref_source_fetch_failed",
                                akt_viide=ref.aktViide,
                                attempt_status="fetch_failed",
                                selected_lane="no_source_lane_selected_fetch_failed",
                                selected=False,
                            ),
                        },
                    )
            )
            continue
        try:
            ET.fromstring(xml_bytes)
        except ET.ParseError as exc:
            if adjudications_out is not None:
                adjudications_out.append(
                    _ee_orchestration_adjudication(
                        kind=_EE_CANCELLED_PENDING_REF_METADATA_PARSE_FAILED_RULE,
                        message=(
                            "Could not parse an Estonia pending-amendment source while "
                            "checking cancellation by same-commencement later acts; retaining "
                            "the reference and recording the incomplete metadata lane."
                        ),
                        source_statute=f"ee/{ref.aktViide}",
                        phase="metadata_extraction",
                        family="pending_amendment_cancellation_filter",
                        blocking=True,
                        detail={
                            "ref_amendment": ref.aktViide,
                            "reason": "pending_ref_metadata_parse_failed",
                            "exception_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                )
            continue
        ref_xml[ref.aktViide] = xml_bytes
        ref_titles[ref.aktViide] = _ee_extract_act_title(
            xml_bytes,
            akt_viide=ref.aktViide,
            adjudications_out=adjudications_out,
        )
        target_sections[ref.aktViide] = _ee_extract_target_matching_paragraph_numbers(
            xml_bytes,
            target_title,
            akt_viide=ref.aktViide,
            adjudications_out=adjudications_out,
        )

    cancelled: set[str] = set()
    sorted_refs = sorted(refs, key=_ee_ref_sort_key)
    for idx, ref in enumerate(sorted_refs):
        target_paras = target_sections.get(ref.aktViide) or set()
        if not target_paras:
            continue
        ref_title = ref_titles.get(ref.aktViide, "")
        if not ref_title:
            continue
        for later in sorted_refs[idx + 1:]:
            if later.joustumine > ref.joustumine:
                break
            repealer_xml = ref_xml.get(later.aktViide)
            if not repealer_xml:
                continue
            repealed_paras = _ee_extract_repealed_source_paragraph_numbers(
                repealer_xml,
                ref_title,
                akt_viide=later.aktViide,
                adjudications_out=adjudications_out,
            )
            if target_paras and target_paras.issubset(repealed_paras):
                cancelled.add(ref.aktViide)
                if adjudications_out is not None:
                    adjudications_out.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_CANCELLED_PENDING_REF_FILTER_RULE,
                            message=(
                                "Filtered a pending Estonia amendment reference because a later "
                                "same-commencement source act repeals all target paragraphs before replay."
                            ),
                            source_statute=f"ee/{later.aktViide}",
                            phase="temporal",
                            family="pending_amendment_cancellation_filter",
                            detail={
                                "filtered_amendment": ref.aktViide,
                                "filtering_amendment": later.aktViide,
                                "reason": "source_paragraphs_repealed_before_commencement",
                                "target_paragraphs": tuple(sorted(target_paras)),
                                "matched_paragraphs": tuple(sorted(repealed_paras)),
                                "target_title": target_title,
                                "source_act_title": ref_title,
                            },
                        )
                    )
                break
            rewritten_paras = _ee_extract_rewritten_source_paragraph_numbers(
                repealer_xml,
                ref_title,
                akt_viide=later.aktViide,
                adjudications_out=adjudications_out,
            )
            if target_paras and target_paras.issubset(rewritten_paras):
                cancelled.add(ref.aktViide)
                if adjudications_out is not None:
                    adjudications_out.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_CANCELLED_PENDING_REF_FILTER_RULE,
                            message=(
                                "Filtered a pending Estonia amendment reference because a later "
                                "same-commencement source act rewrites all target paragraphs before replay."
                            ),
                            source_statute=f"ee/{later.aktViide}",
                            phase="temporal",
                            family="pending_amendment_cancellation_filter",
                            detail={
                                "filtered_amendment": ref.aktViide,
                                "filtering_amendment": later.aktViide,
                                "reason": "source_paragraphs_rewritten_before_commencement",
                                "target_paragraphs": tuple(sorted(target_paras)),
                                "matched_paragraphs": tuple(sorted(rewritten_paras)),
                                "target_title": target_title,
                                "source_act_title": ref_title,
                            },
                        )
                    )
                break

    accepted = [ref for ref in refs if ref.aktViide not in cancelled]
    rejected = [
        RejectedItem(
            item=ref,
            reason=f"cancelled_pending_amendment: {ref.aktViide} is cancelled by a later same-commencement source act",
            reason_code="cancelled_pending_amendment",
            blocking=False,
        )
        for ref in refs
        if ref.aktViide in cancelled
    ]
    return FilterResult(
        accepted_items=tuple(accepted),
        rejected_items=tuple(rejected),
    )


def _ee_filter_ops_for_ref_slice(
    ops: list[LegalOperation],
    *,
    ref: AmendmentRef,
    base_refs: tuple[AmendmentRef, ...],
    all_refs: tuple[AmendmentRef, ...] = (),
    as_of: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> FilterResult[LegalOperation]:
    """Filter one act's ops to the executable slice owned by ``ref``.

    Earliest known slices may carry unsliced ops plus clause-local ops for the
    same date. Later slices may only carry clause-local ops that are explicitly
    tagged with that later effective date.
    """
    same_act_refs = tuple(
        candidate
        for candidate in (*base_refs, *all_refs)
        if candidate.aktViide == ref.aktViide and candidate.joustumine
    )
    has_earlier_slice = any(candidate.joustumine < ref.joustumine for candidate in same_act_refs)
    later_ref_dates = sorted({candidate.joustumine for candidate in same_act_refs if candidate.joustumine > ref.joustumine})
    next_later_ref_date = later_ref_dates[0] if later_ref_dates else ""
    any_local_slice_ops = any(
        op.source is not None and op.source.effective
        for op in ops
    )

    def _record_filtered_op(op: LegalOperation, reason: str, *, effective: str = "") -> None:
        if adjudications_out is None:
            return
        adjudications_out.append(
            _ee_orchestration_adjudication(
                kind=_EE_REF_SLICE_OP_FILTER_RULE,
                message="Filtered an Estonia operation outside the executable slice for this amendment reference.",
                source_statute=op.source.statute_id if op.source is not None else f"ee/{ref.aktViide}",
                op_id=op.op_id,
                phase="temporal",
                family="ref_slice_filter",
                detail={
                    "reason": reason,
                    "ref_amendment": ref.aktViide,
                    "ref_effective": ref.joustumine,
                    "op_effective": effective,
                    "next_later_ref_effective": next_later_ref_date,
                    "as_of": as_of,
                    "target": str(op.target),
                    "action": op.action.value,
                },
            )
        )

    if any_local_slice_ops:
        filtered_ops: list[LegalOperation] = []
        rejected_ops: list[RejectedItem[LegalOperation]] = []

        def _record_and_reject(op: LegalOperation, reason: str, *, effective: str = "") -> None:
            _record_filtered_op(op, reason, effective=effective)
            rejected_ops.append(
                RejectedItem(
                    item=op,
                    reason=f"{reason}: op effective {effective!r} outside ref slice {ref.joustumine!r}",
                    reason_code=reason,
                    blocking=False,
                )
            )

        for op in ops:
            effective = op.source.effective if op.source is not None else ""
            if not effective:
                if not has_earlier_slice:
                    filtered_ops.append(op)
                else:
                    _record_and_reject(op, "unsliced_op_after_earlier_same_act_slice", effective=effective)
                continue
            effective_window_date = effective
            if effective < ref.joustumine:
                if has_earlier_slice:
                    _record_and_reject(op, "op_effective_before_ref_after_earlier_same_act_slice", effective=effective)
                    continue
                effective_window_date = ref.joustumine
            if next_later_ref_date and effective_window_date >= next_later_ref_date:
                _record_and_reject(op, "op_effective_belongs_to_later_same_act_slice", effective=effective)
                continue
            if as_of and effective > as_of:
                _record_and_reject(op, "op_effective_after_requested_pit", effective=effective)
                continue
            filtered_ops.append(op)
        return FilterResult(
            accepted_items=tuple(filtered_ops),
            rejected_items=tuple(rejected_ops),
        )

    return filter_result_from_parts(accepted_items=ops)


_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE = "ee_pending_amendment_text_precompose"
_EE_PENDING_SOURCE_ACT_COMMENCEMENT_PRECOMPOSE_RULE = "ee_pending_source_act_commencement_precompose"


def _ee_old_format_tag_value(op: LegalOperation, prefix: str) -> str:
    for tag in op.provenance_tags:
        if tag.startswith(prefix):
            return tag[len(prefix) :].strip()
    return ""


def _ee_pending_patch_target_parts(op: LegalOperation) -> tuple[str, str] | None:
    section = ""
    item = ""
    for kind, label in op.target.path:
        if kind == "section":
            section = label
        elif kind == "item":
            item = label
    if not section or not item:
        return None
    return section, item


def _ee_extract_source_act_commencement_replacement(
    xml_bytes: bytes,
    *,
    amended_act_title: str,
    akt_viide: str = "",
    adjudications_out: list[CompileAdjudication] | None = None,
) -> str:
    """Return a replacement commencement date for ``amended_act_title`` when explicit."""
    later_title = _ee_extract_act_title(
        xml_bytes,
        akt_viide=akt_viide,
        adjudications_out=adjudications_out,
    )
    if not _strict_title_match_para(amended_act_title, later_title):
        return ""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ""
    ns = _ee_xml_ns(root)
    for html_container in root.iter(f"{{{ns}}}HTMLKonteiner"):
        for item_text in parse_html_op_items(html_container.text or ""):
            item_lower = item_text.lower()
            if "seaduse jõustumine" not in item_lower:
                continue
            if "jõustub" not in item_lower:
                continue
            if not re.search(
                r"\bparagrahv(?:i)?\s+\d[\d\s¹²³⁴⁵⁶⁷⁸⁹⁰]*\s+muudetakse\s+ja\s+sõnastatakse",
                item_text,
                re.IGNORECASE,
            ):
                continue
            date_text = _old_format_commencement_date(item_text)
            if date_text:
                return date_text
    return ""


def _ee_precompose_pending_source_act_commencements(
    refs: tuple[AmendmentRef, ...],
    *,
    as_of: str,
    archive: Any,
    adjudications_out: list[CompileAdjudication] | None = None,
    successful_xml_cache: dict[str, bytes] | None = None,
) -> FilterResult[AmendmentRef]:
    """Apply source-backed commencement amendments to pending source-act refs."""
    if len(refs) < 2:
        return filter_result_from_parts(accepted_items=refs)

    xml_by_ref: dict[str, bytes] = {}
    title_by_ref: dict[str, str] = {}
    adjudications = adjudications_out if adjudications_out is not None else []
    for ref in refs:
        try:
            xml_bytes = _ee_fetch_rt_xml_cached(ref.aktViide, archive, successful_xml_cache)
        # lawvm-failloud (AGENTS.md §1.10): only an expected source-unavailable
        # fetch failure (_EE_AMENDMENT_FETCH_EXPECTED_ERRORS) is recorded here;
        # the amendment is skipped from precomposition. Other exceptions are
        # unexpected bugs and must propagate.
        except _EE_AMENDMENT_FETCH_EXPECTED_ERRORS as exc:
            adjudications.append(
                _ee_orchestration_adjudication(
                    kind=_EE_PENDING_SOURCE_ACT_COMMENCEMENT_FETCH_FAILED_RULE,
                    message=(
                        "Estonia pending source-act commencement precomposition skipped "
                        "an amendment because its source XML could not be fetched."
                    ),
                    source_statute=f"ee/{ref.aktViide}",
                    phase="acquisition",
                    family="source_pathology",
                    blocking=True,
                    detail={
                        "amendment": ref.aktViide,
                        "effective": ref.joustumine,
                        "as_of": as_of,
                        "error": str(exc),
                        "source_lane_selection": _ee_rt_xml_source_lane_detail(
                            rule_id=_EE_PENDING_SOURCE_ACT_COMMENCEMENT_FETCH_FAILED_RULE,
                            phase="acquisition",
                            reason="pending_source_act_commencement_source_fetch_failed",
                            akt_viide=ref.aktViide,
                            attempt_status="fetch_failed",
                            selected_lane="no_source_lane_selected_fetch_failed",
                            selected=False,
                        ),
                    },
                )
            )
            continue
        xml_by_ref[ref.aktViide] = xml_bytes
        title_by_ref[ref.aktViide] = _ee_extract_act_title(
            xml_bytes,
            akt_viide=ref.aktViide,
            adjudications_out=adjudications,
        )

    overrides: dict[str, tuple[str, AmendmentRef]] = {}
    for earlier_ref in sorted(refs, key=lambda ref: (ref.passed, ref.joustumine, ref.aktViide)):
        earlier_title = title_by_ref.get(earlier_ref.aktViide, "")
        if not earlier_title:
            continue
        for later_ref in sorted(refs, key=lambda ref: (ref.passed, ref.joustumine, ref.aktViide)):
            if later_ref.aktViide == earlier_ref.aktViide:
                continue
            if later_ref.passed < earlier_ref.passed:
                continue
            later_xml = xml_by_ref.get(later_ref.aktViide)
            if later_xml is None:
                continue
            replacement_date = _ee_extract_source_act_commencement_replacement(
                later_xml,
                amended_act_title=earlier_title,
                akt_viide=later_ref.aktViide,
                adjudications_out=adjudications,
            )
            if not replacement_date or replacement_date == earlier_ref.joustumine:
                continue
            overrides[earlier_ref.aktViide] = (replacement_date, later_ref)
            adjudications.append(
                _ee_orchestration_adjudication(
                    kind=_EE_PENDING_SOURCE_ACT_COMMENCEMENT_PRECOMPOSE_RULE,
                    message=(
                        "Applied an explicit source-act commencement replacement before "
                        "deciding whether the pending source act is executable at this PIT date."
                    ),
                    source_statute=f"ee/{later_ref.aktViide}",
                    phase="temporal",
                    family="pending_source_act_precomposition",
                    detail={
                        "earlier_amendment": earlier_ref.aktViide,
                        "later_amendment": later_ref.aktViide,
                        "old_effective": earlier_ref.joustumine,
                        "new_effective": replacement_date,
                        "as_of": as_of,
                        "amended_act_title": earlier_title,
                    },
                )
            )
            break

    updated_refs: list[AmendmentRef] = []
    for ref in refs:
        override = overrides.get(ref.aktViide)
        if override is None:
            updated_refs.append(ref)
            continue
        replacement_date, _later_ref = override
        if replacement_date <= as_of:
            updated_refs.append(
                AmendmentRef(
                    aktViide=ref.aktViide,
                    passed=ref.passed,
                    joustumine=replacement_date,
                )
            )
    return FilterResult(accepted_items=tuple(sorted(updated_refs, key=_ee_ref_sort_key)))


def _ee_precompose_pending_amendment_text_patches(
    ops: list[LegalOperation],
    *,
    refs: tuple[AmendmentRef, ...],
    amendment_xml_by_ref: dict[str, bytes],
) -> tuple[list[LegalOperation], tuple[CompileAdjudication, ...]]:
    """Apply explicit amendments to not-yet-live amendment payloads.

    Estonia sometimes amends a pending amendment act before that earlier act's
    own target-law mutation has taken effect. This pass composes source-backed
    text replacements against earlier old-format amendment items, final-target
    operations already owned by the earlier pending amendment, and sibling
    text replacements introduced on an already-owned final target.
    """
    updated_ops = list(ops)
    adjudications: list[CompileAdjudication] = []
    sorted_refs = tuple(sorted(refs, key=_ee_ref_sort_key))
    parsed_meta_ops: dict[tuple[str, str], tuple[LegalOperation, ...]] = {}
    title_by_ref: dict[str, str] = {}
    for ref in sorted_refs:
        xml_bytes = amendment_xml_by_ref.get(ref.aktViide)
        if xml_bytes is None:
            continue
        title_by_ref[ref.aktViide] = _ee_extract_act_title(
            xml_bytes,
            akt_viide=ref.aktViide,
            adjudications_out=adjudications,
        )
    surface_may_target_cache: dict[tuple[str, str], bool] = {}

    for later_index, later_ref in enumerate(sorted_refs):
        later_xml = amendment_xml_by_ref.get(later_ref.aktViide)
        if later_xml is None:
            continue
        for earlier_ref in sorted_refs[:later_index]:
            if earlier_ref.aktViide == later_ref.aktViide:
                continue
            earlier_xml = amendment_xml_by_ref.get(earlier_ref.aktViide)
            if earlier_xml is None:
                continue
            earlier_title = title_by_ref.get(earlier_ref.aktViide, "")
            if not earlier_title:
                continue
            surface_key = (later_ref.aktViide, earlier_title)
            if surface_key not in surface_may_target_cache:
                surface_may_target_cache[surface_key] = _ee_source_surface_may_target_title(
                    later_xml,
                    earlier_title,
                )
            if not surface_may_target_cache[surface_key]:
                continue
            meta_key = (later_ref.aktViide, earlier_title)
            if meta_key not in parsed_meta_ops:
                try:
                    parsed = parse_ee_amendment_ops(
                        later_xml,
                        f"ee/{later_ref.aktViide}",
                        target_title=earlier_title,
                        ref_effective=later_ref.joustumine,
                        adjudications_out=adjudications,
                    )
                # lawvm-failloud (AGENTS.md §1.10 / §1.8): NOT a silent swallow.
                # During the pending-amendment metapass that re-reads future-oracle
                # amendments to live-update still-targeted text, parse_ee_amendment_ops
                # raises on any unsupported/unparseable amendment shape (ET.ParseError,
                # ValueError, …) — the genuinely-expected source pathology for this
                # best-effort precomposition. The broad catch is intentional: it emits a
                # distinct ee_pending_amendment_metapass_parse_failed adjudication
                # embedding exception_type + message (self-evidencing) before treating
                # the parse as empty, so the failure is accounted for rather than hidden
                # behind a bare ``parsed = []``. Same parse contract the blocking
                # acquisition path (above) records; here non-blocking because the
                # metapass is an optional text-patch refinement, not a load-bearing lane.
                except Exception as e:
                    parsed = []
                    adjudications.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_PENDING_AMENDMENT_METAPASS_PARSE_FAILED_RULE,
                            message=(
                                "Estonia pending-amendment metapass skipped a "
                                "later amendment because operation parsing "
                                "failed during text-patch precomposition."
                            ),
                            source_statute=f"ee/{later_ref.aktViide}",
                            detail={
                                "ref_amendment": later_ref.aktViide,
                                "target_title": earlier_title,
                                "reason": "metapass_parse_failed",
                                "exception_type": type(e).__name__,
                                "exception": str(e),
                                "source_lane_selection": _ee_rt_xml_source_lane_detail(
                                    rule_id=_EE_PENDING_AMENDMENT_METAPASS_PARSE_FAILED_RULE,
                                    phase="parse",
                                    reason="metapass_parse_failed",
                                    akt_viide=later_ref.aktViide,
                                    attempt_status="selected_parse_failed",
                                    selected_lane="riigi_teataja_xml",
                                    selected=True,
                                ),
                            },
                            phase="parse",
                            family="source_lane_failure",
                            blocking=False,
                        )
                    )
                parsed_meta_ops[meta_key] = tuple(parsed)
            for meta_op in parsed_meta_ops[meta_key]:
                if meta_op.text_patch is None:
                    continue
                target_parts = _ee_pending_patch_target_parts(meta_op)
                match_text = meta_op.text_patch.selector.match_text
                replacement = meta_op.text_patch.replacement
                if not match_text or replacement is None:
                    continue
                patched_candidate = False
                if target_parts is not None:
                    target_section, target_item = target_parts
                    for index, candidate in enumerate(updated_ops):
                        if candidate.source is None or candidate.source.statute_id != f"ee/{earlier_ref.aktViide}":
                            continue
                        if _ee_old_format_tag_value(candidate, "old_format_amendment_section:") != target_section:
                            continue
                        if _ee_old_format_tag_value(candidate, "old_format_amendment_item:") != target_item:
                            continue
                        if candidate.payload is None or match_text not in candidate.payload.text:
                            continue
                        patched_payload = replace(
                            candidate.payload,
                            text=candidate.payload.text.replace(match_text, replacement),
                        )
                        patched_op = replace(
                            candidate,
                            payload=patched_payload,
                            witness_rule_id=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                            provenance_tags=(
                                *candidate.provenance_tags,
                                (
                                    f"{_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE}:"
                                    f"{later_ref.aktViide}:{target_section}:{target_item}"
                                ),
                            ),
                        )
                        updated_ops[index] = patched_op
                        adjudications.append(
                            _ee_orchestration_adjudication(
                                kind=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                                message=(
                                    "Applied source-backed text replacement to a pending "
                                    "amendment payload before replaying it into the target statute."
                                ),
                                source_statute=f"ee/{later_ref.aktViide}",
                                op_id=candidate.op_id,
                                phase="payload",
                                family="pending_amendment_precomposition",
                                detail={
                                    "earlier_amendment": earlier_ref.aktViide,
                                    "later_amendment": later_ref.aktViide,
                                    "amendment_section": target_section,
                                    "amendment_item": target_item,
                                    "match_text": match_text,
                                    "replacement": replacement,
                                },
                            )
                        )
                        patched_candidate = True
                        break
                    if patched_candidate:
                        continue
                for index, candidate in enumerate(updated_ops):
                    if candidate.source is None or candidate.source.statute_id != f"ee/{earlier_ref.aktViide}":
                        continue
                    if candidate.target != meta_op.target:
                        continue
                    if candidate.text_patch is None or candidate.payload is None:
                        continue
                    if candidate.text_patch.selector.match_text != match_text:
                        continue
                    patched_payload = replace(candidate.payload, text=replacement)
                    patched_patch = replace(candidate.text_patch, replacement=replacement)
                    patched_op = replace(
                        candidate,
                        payload=patched_payload,
                        text_patch=patched_patch,
                        witness_rule_id=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                        provenance_tags=(
                            *candidate.provenance_tags,
                            (
                                f"{_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE}:"
                                f"{later_ref.aktViide}:target:{str(candidate.target)}"
                            ),
                        ),
                    )
                    updated_ops[index] = patched_op
                    adjudications.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                            message=(
                                "Applied source-backed text replacement to a pending "
                                "amendment payload by matching the final target address."
                            ),
                            source_statute=f"ee/{later_ref.aktViide}",
                            op_id=candidate.op_id,
                            phase="payload",
                            family="pending_amendment_precomposition",
                            detail={
                                "earlier_amendment": earlier_ref.aktViide,
                                "later_amendment": later_ref.aktViide,
                                "target": str(candidate.target),
                                "match_text": match_text,
                                "replacement": replacement,
                            },
                        )
                    )
                    patched_candidate = True
                    break
                if patched_candidate:
                    continue
                owns_same_final_target = any(
                    candidate.source is not None
                    and candidate.source.statute_id == f"ee/{earlier_ref.aktViide}"
                    and candidate.target == meta_op.target
                    and candidate.text_patch is not None
                    for candidate in updated_ops
                )
                if target_parts is None and meta_op.target.path and owns_same_final_target:
                    sequence = max((op.sequence for op in updated_ops), default=0) + 1
                    appended_op = replace(
                        meta_op,
                        sequence=sequence,
                        source=OperationSource(
                            statute_id=f"ee/{later_ref.aktViide}",
                            title=meta_op.source.title if meta_op.source else "",
                            enacted=later_ref.passed,
                            effective=later_ref.joustumine,
                            raw_text=meta_op.source.raw_text if meta_op.source else "",
                        ),
                        witness_rule_id=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                        provenance_tags=(
                            *meta_op.provenance_tags,
                            (
                                f"{_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE}:"
                                f"{later_ref.aktViide}:added-target:{str(meta_op.target)}"
                            ),
                        ),
                    )
                    updated_ops.append(appended_op)
                    adjudications.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                            message=(
                                "Added a source-backed pending amendment text replacement "
                                "introduced by a later amendment to the amendment act."
                            ),
                            source_statute=f"ee/{later_ref.aktViide}",
                            op_id=appended_op.op_id,
                            phase="payload",
                            family="pending_amendment_precomposition",
                            detail={
                                "earlier_amendment": earlier_ref.aktViide,
                                "later_amendment": later_ref.aktViide,
                                "target": str(meta_op.target),
                                "match_text": match_text,
                                "replacement": replacement,
                                "mode": "added_final_target_op",
                            },
                        )
                    )
            for meta_op in parsed_meta_ops[meta_key]:
                if meta_op.action is not StructuralAction.REPLACE or meta_op.payload is None:
                    continue
                for index, candidate in enumerate(updated_ops):
                    if candidate.source is None or candidate.source.statute_id != f"ee/{earlier_ref.aktViide}":
                        continue
                    if candidate.action is not StructuralAction.REPLACE:
                        continue
                    if candidate.target != meta_op.target or candidate.payload is None:
                        continue
                    patched_op = replace(
                        candidate,
                        payload=meta_op.payload,
                        witness_rule_id=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                        provenance_tags=(
                            *candidate.provenance_tags,
                            (
                                f"{_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE}:"
                                f"{later_ref.aktViide}:target:{str(candidate.target)}"
                            ),
                        ),
                    )
                    updated_ops[index] = patched_op
                    adjudications.append(
                        _ee_orchestration_adjudication(
                            kind=_EE_PENDING_AMENDMENT_PRECOMPOSE_RULE,
                            message=(
                                "Applied source-backed replacement to a pending "
                                "amendment payload by matching the final target address."
                            ),
                            source_statute=f"ee/{later_ref.aktViide}",
                            op_id=candidate.op_id,
                            phase="payload",
                            family="pending_amendment_precomposition",
                            detail={
                                "earlier_amendment": earlier_ref.aktViide,
                                "later_amendment": later_ref.aktViide,
                                "target": str(candidate.target),
                            },
                        )
                    )
                    break
    return updated_ops, tuple(adjudications)


def _collapse_subdivision_in_timelines(timelines: dict[LegalAddress, Any]) -> dict[LegalAddress, Any]:
    """Re-key timelines so ``subdivision`` path segments are dropped.

    EE jaotis (subdivision) is an editorial wrapper: a new section inserted by
    an amendment is addressed at division level (chapter/division/section), but
    the consolidated oracle nests it under a subdivision. Both denote the same
    provision. Collapsing the subdivision segment for the consistency comparison
    makes section identity insensitive to whether a section sits inside a
    subdivision wrapper — matching ``section_key_from_path``, which already
    skips division/subdivision. Subdivision container nodes themselves carry no
    own comparison text once collapsed and fold into their parent division.
    """
    collapsed: dict[LegalAddress, Any] = {}
    for address, timeline in timelines.items():
        new_path = tuple((kind, label) for kind, label in address.path if kind != "subdivision")
        if new_path == address.path:
            collapsed[address] = timeline
            continue
        if not new_path:
            # A bare subdivision container collapses away entirely.
            continue
        new_address = LegalAddress(path=new_path)
        # Prefer an existing concrete (non-container) entry on collision.
        collapsed.setdefault(new_address, timeline)
    return collapsed


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class EEPitResult:
    """Result of a point-in-time Estonia amendment replay."""
    base_id: str
    as_of: str

    # Replayed state
    base_title: str = ""
    replayed: Optional[IRStatute] = None
    n_ops: int = 0

    # Amendment discovery
    grupi_id: Optional[str] = None
    amendments_total: List[str]  = field(default_factory=list)
    amendments_applied: List[str] = field(default_factory=list)
    amendments_skipped: List[str] = field(default_factory=list)
    amendments_failed: List[str]  = field(default_factory=list)

    # Oracle
    oracle: Optional[IRStatute] = None
    oracle_id: Optional[str] = None
    pair_plan: Optional[EEOraclePairPlan] = None
    source_basis: str = ""
    comparison_class: str = ""
    source_adjudication: Optional[SourceAdjudication] = None

    # Timelines (populated after timeline-primary flip)
    timelines: Optional[dict[LegalAddress, ProvisionTimeline]] = None
    temporal_events: tuple[TemporalEvent, ...] = ()
    compiled_ops: tuple[LegalOperation, ...] = ()
    applied_snapshot_ops: tuple[LegalOperation, ...] = ()

    # Consistency check
    divergences: list[Any] = field(default_factory=list)
    n_mismatch: int = 0
    n_ops_missing: int = 0    # in oracle but not in replay
    n_con_missing: int = 0    # in replay but not in oracle

    # Error
    error: Optional[str] = None

    # Optional replay-adjudication stream from operation application.
    adjudications: list[CompileAdjudication] = field(default_factory=list)

    # Conserved apply-phase partition (§1.8): every input op accepted or
    # rejected with a RejectedItem receipt.
    apply_filter_result: Optional[FilterResult] = None

    def to_replay_summary(self) -> ReplaySummary:
        """Project this EE-specific result onto the shared ``ReplaySummary``
        contract from ``core/replay_contracts.py``, giving downstream
        tooling (bench / frontier / cross-jurisdiction commands) a
        jurisdiction-agnostic output shape.

        Maps EE-specific fields (``grupi_id``, ``source_basis``,
        ``comparison_class``, etc.) into ``detail``; the core
        ``ReplaySummary`` fields (amendment_count, applied_count,
        divergence_count, consistent, etc.) are populated directly from
        the EE-parallel fields."""
        has_oracle = self.oracle is not None and self.error is None
        return ReplaySummary(
            jurisdiction="ee",
            base_id=self.base_id,
            as_of=self.as_of,
            title=self.base_title,
            replay_status="error" if self.error else "ok",
            error=self.error,
            oracle_id=self.oracle_id or "",
            source_id="",
            amendment_count=len(self.amendments_total),
            applied_count=len(self.amendments_applied),
            skipped_count=len(self.amendments_skipped),
            failed_count=len(self.amendments_failed),
            op_count=self.n_ops,
            consistent=(not bool(self.divergences)) if has_oracle else None,
            divergence_count=len(self.divergences) if has_oracle else None,
            steps=(),
            text_view=None,
            detail={
                "source_basis": self.source_basis,
                "comparison_class": self.comparison_class,
                "grupi_id": self.grupi_id or "",
                "n_mismatch": self.n_mismatch,
                "n_ops_missing": self.n_ops_missing,
                "n_con_missing": self.n_con_missing,
                "adjudication_count": len(self.adjudications),
            },
        )


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def replay_ee_to_pit(
    base_id: str,
    as_of: str,
    archive: Any = None,
    verbose: bool = False,
    oracle_id: Optional[str] = None,
    temporal_events: tuple[TemporalEvent, ...] = (),
) -> EEPitResult:
    """Full e2e Estonia PIT replay.

    Args:
        base_id:  Riigi Teataja aktViide (e.g. "113032019003") or local XML path.
        as_of:    Target date YYYY-MM-DD. Amendments effective on or before this
                  date are applied.
        archive:  Farchive instance (default: open riigiteataja_archive.db).
        verbose:  Print progress to stderr.
        oracle_id: Explicit oracle aktViide. If provided, skips redaction feed
                   lookup and uses this terviktekst directly as oracle.

    Returns:
        EEPitResult with replayed statute, oracle, divergence report, and metadata.
    """
    def _log(msg: str) -> None:
        if verbose:
            print(f"  {msg}", file=sys.stderr)

    _archive = archive or open_rt_archive()
    result = EEPitResult(base_id=base_id, as_of=as_of, temporal_events=temporal_events)

    # ── Step 1: Load base act ─────────────────────────────────────────────────
    _log(f"Loading base act {base_id}...")
    try:
        p = Path(base_id)
        if p.suffix == ".xml" or "/" in base_id:
            base_xml = p.read_bytes()
        else:
            base_xml = fetch_rt_xml(base_id, _archive)
    # lawvm-failloud (AGENTS.md §1.10): NOT a silent swallow. Base acquisition
    # can fail by local file IO (OSError) or RT fetch (RuntimeError /
    # readonly-archive sqlite3.OperationalError); the broad catch is intentional
    # because it records a distinct, self-evidencing "Failed to load base: {e}"
    # banner that classify_ee_replayability maps to BASE_SOURCE_UNAVAILABLE.
    except Exception as e:
        result.error = f"Failed to load base: {e}"
        return result

    try:
        base = parse_ee_statute(base_xml, f"ee/{base_id}")
    # lawvm-failloud (AGENTS.md §1.10): NOT a silent swallow. parse_ee_statute
    # raises on any malformed/unsupported base XML (ET.ParseError, ValueError,
    # …); the broad catch records a distinct "Failed to parse base: {e}" banner
    # (exception embedded) that classify_ee_replayability maps to
    # BASE_SOURCE_PARSE_ERROR. The failure is recorded and classified, never
    # guessed past.
    except Exception as e:
        result.error = f"Failed to parse base: {e}"
        return result

    result.base_title = base.title
    _log(f"Base: {base.title[:60]}")

    # ── Step 2: Discover oracle + plan the commensurable pair ─────────────────
    planning = plan_ee_oracle_pair(
        base_id=base_id,
        as_of=as_of,
        base_xml=base_xml,
        archive=_archive,
        oracle_id=oracle_id,
    )
    pair_plan = planning.plan
    result.pair_plan = pair_plan
    result.grupi_id = pair_plan.grupi_id
    result.oracle_id = pair_plan.oracle_id
    result.source_basis = pair_plan.source_basis.value
    result.comparison_class = pair_plan.comparison_class
    result.source_adjudication = pair_plan.source_adjudication

    if oracle_id:
        _log(f"Explicit oracle: {pair_plan.oracle_id}")
    elif pair_plan.grupi_id:
        _log(f"grupiId: {pair_plan.grupi_id}")
        _log(f"Oracle redaction: {pair_plan.oracle_id or '(none found for this date)'}")

    oracle_parse_adjudications: list[CompileAdjudication] = []
    if pair_plan.oracle_is_base:
        result.oracle = base
    elif planning.oracle_xml is not None and pair_plan.oracle_id is not None:
        try:
            result.oracle = parse_ee_statute(planning.oracle_xml, f"ee/{pair_plan.oracle_id}")
        # lawvm-failloud (AGENTS.md §1.10): the oracle is the comparison
        # reference; leaving result.oracle = None silently skips the entire
        # consistency check downstream (it is guarded on `result.oracle is not
        # None`), so a parse failure here must NOT be a verbose-only WARN. Record
        # a distinct, self-evidencing ee_oracle_parse_failed adjudication
        # embedding the oracle id, exception type/message, and a source snippet,
        # so a silently-uncompared replay is always accounted for. parse_ee_statute
        # raises on any malformed/unsupported oracle XML (ET.ParseError,
        # ValueError, …), all of which are this expected source-pathology lane.
        except Exception as e:
            _log(f"WARN: oracle parse failed: {e}")
            oracle_parse_adjudications.append(
                _ee_orchestration_adjudication(
                    kind=_EE_ORACLE_PARSE_FAILED_RULE,
                    message=(
                        "Estonia replay could not parse the RT oracle consolidation; "
                        "the consistency check against the oracle is skipped and the "
                        "result is left uncompared."
                    ),
                    source_statute=f"ee/{pair_plan.oracle_id}",
                    detail={
                        "oracle_id": pair_plan.oracle_id,
                        "reason": "oracle_source_xml_parse_failed",
                        "exception_type": type(e).__name__,
                        "exception": str(e),
                        "oracle_xml_head": planning.oracle_xml[:400].decode(
                            "utf-8", errors="replace"
                        ),
                    },
                    phase="parse",
                    family="source_lane_failure",
                    blocking=True,
                )
            )

    # ── Step 3: Amendment discovery ───────────────────────────────────────────
    _log(f"Base tekstiliik: {'terviktekst' if pair_plan.base_is_consolidated else 'algtekst'}")

    if pair_plan.base_is_consolidated and pair_plan.oracle_refs:
        _log(
            f"Terviktekst mode: {len(pair_plan.base_refs)} in base, "
            f"{len(pair_plan.oracle_refs)} in oracle, "
            f"{len(pair_plan.amendments_to_apply)} new amendments to apply "
            f"(joustumine <= {as_of})"
        )
    elif pair_plan.base_is_consolidated:
        _log(f"Terviktekst mode (no delta): base is the oracle for {as_of}, 0 new amendments")
    else:
        _log(
            f"algtekst mode: {len(pair_plan.amendments_to_apply)} of "
            f"{len(pair_plan.base_refs)} amendments apply by {as_of}"
        )

    result.amendments_total = [
        ref.aktViide
        for ref in (
            pair_plan.base_refs if pair_plan.base_is_consolidated else pair_plan.amendments_to_apply
        )
    ]
    cancellation_filter_adjudications: list[CompileAdjudication] = []
    successful_amendment_xml_cache: dict[str, bytes] = {}
    cancelled_pending_result = _ee_filter_cancelled_pending_refs(
        sorted(pair_plan.amendments_to_apply, key=_ee_ref_sort_key),
        target_title=base.title,
        archive=_archive,
        adjudications_out=cancellation_filter_adjudications,
        successful_xml_cache=successful_amendment_xml_cache,
    )
    to_apply = list(cancelled_pending_result.accepted_items)
    commencement_precomposition_adjudications: list[CompileAdjudication] = []
    commencement_precomposition_result = _ee_precompose_pending_source_act_commencements(
        tuple(to_apply),
        as_of=as_of,
        archive=_archive,
        adjudications_out=commencement_precomposition_adjudications,
        successful_xml_cache=successful_amendment_xml_cache,
    )
    to_apply = list(commencement_precomposition_result.accepted_items)
    to_skip = [
        ref for ref in pair_plan.base_refs if ref.aktViide not in {x.aktViide for x in to_apply}
    ]
    result.amendments_skipped = [r.aktViide for r in to_skip]
    _log(f"Apply: {len(to_apply)} | Skip: {len(to_skip)}")

    # ── Step 4: Fetch + parse ops ─────────────────────────────────────────────
    all_ops: List[LegalOperation] = []
    global_seq = 1
    amendment_xml_by_ref: dict[str, bytes] = {}
    slice_filter_adjudications: list[CompileAdjudication] = []
    source_lane_failure_adjudications: list[CompileAdjudication] = []

    for ref in sorted(to_apply, key=_ee_ref_sort_key):
        _log(f"  {ref.aktViide}  effective={ref.joustumine}...")
        try:
            amend_xml = _ee_fetch_rt_xml_cached(ref.aktViide, _archive, successful_amendment_xml_cache)
        # lawvm-failloud (AGENTS.md §1.10): only an expected source-unavailable
        # fetch failure (_EE_AMENDMENT_FETCH_EXPECTED_ERRORS) marks the amendment
        # failed and records a blocking source-lane adjudication. Other
        # exceptions are unexpected bugs and must propagate.
        except _EE_AMENDMENT_FETCH_EXPECTED_ERRORS as e:
            _log(f"    fetch failed: {e}")
            result.amendments_failed.append(ref.aktViide)
            source_lane_failure_adjudications.append(
                _ee_orchestration_adjudication(
                    kind=_EE_AMENDMENT_SOURCE_FETCH_FAILED_RULE,
                    message="Estonia replay skipped amendment because source XML fetch failed.",
                    source_statute=f"ee/{ref.aktViide}",
                    detail={
                        "ref_amendment": ref.aktViide,
                        "reason": "amendment_source_fetch_failed",
                        "exception_type": type(e).__name__,
                        "exception": str(e),
                        "source_lane_selection": _ee_rt_xml_source_lane_detail(
                            rule_id=_EE_AMENDMENT_SOURCE_FETCH_FAILED_RULE,
                            phase="acquisition",
                            reason="amendment_source_fetch_failed",
                            akt_viide=ref.aktViide,
                            attempt_status="fetch_failed",
                            selected_lane="no_source_lane_selected_fetch_failed",
                            selected=False,
                        ),
                    },
                    phase="acquisition",
                    family="source_lane_failure",
                    blocking=True,
                )
            )
            continue
        amendment_xml_by_ref[ref.aktViide] = amend_xml

        try:
            same_act_refs = tuple(
                candidate
                for candidate in (*pair_plan.base_refs, *pair_plan.amendments_to_apply)
                if candidate.aktViide == ref.aktViide and candidate.joustumine
            )
            ops = parse_ee_amendment_ops(
                amend_xml,
                f"ee/{ref.aktViide}",
                target_title=base.title,
                ref_effective=ref.joustumine,
                has_earlier_same_act_slice=any(
                    candidate.joustumine < ref.joustumine
                    for candidate in same_act_refs
                ),
                adjudications_out=slice_filter_adjudications,
            )
        # lawvm-failloud (AGENTS.md §1.10): NOT a silent swallow. parse_ee_amendment_ops
        # signals every unsupported/unparseable amendment shape by raising (ET.ParseError
        # for malformed XML, ValueError for an unsupported XML shape, etc.); each is the
        # genuinely-expected source-lane failure for this acquisition path. The broad
        # catch is intentional and §1.10-compliant because it emits a distinct, BLOCKING
        # ee_amendment_parse_failed adjudication that embeds exception_type + message
        # (self-evidencing) and marks the amendment failed — it never degrades to a
        # guessed/empty result. Contract pinned by
        # test_replay_ee_to_pit_adjudicates_amendment_parse_failure.
        except Exception as e:
            _log(f"    parse failed: {e}")
            result.amendments_failed.append(ref.aktViide)
            source_lane_failure_adjudications.append(
                _ee_orchestration_adjudication(
                    kind=_EE_AMENDMENT_PARSE_FAILED_RULE,
                    message="Estonia replay skipped amendment because operation parsing failed.",
                    source_statute=f"ee/{ref.aktViide}",
                    detail={
                        "ref_amendment": ref.aktViide,
                        "reason": "amendment_parse_failed",
                        "exception_type": type(e).__name__,
                        "exception": str(e),
                        "source_lane_selection": _ee_rt_xml_source_lane_detail(
                            rule_id=_EE_AMENDMENT_PARSE_FAILED_RULE,
                            phase="parse",
                            reason="amendment_parse_failed",
                            akt_viide=ref.aktViide,
                            attempt_status="selected_parse_failed",
                            selected_lane="riigi_teataja_xml",
                            selected=True,
                        ),
                    },
                    phase="parse",
                    family="source_lane_failure",
                    blocking=True,
                )
            )
            continue
        ops = list(_ee_filter_ops_for_ref_slice(
            ops,
            ref=ref,
            base_refs=pair_plan.base_refs,
            all_refs=pair_plan.amendments_to_apply,
            as_of=as_of,
            adjudications_out=slice_filter_adjudications,
        ).accepted_items)

        # Stamp each op with provenance dates; renumber to global sequence
        ops = [
            replace(
                op,
                source=OperationSource(
                    statute_id=f"ee/{ref.aktViide}",
                    title=op.source.title if op.source else "",
                    enacted=ref.passed,
                    effective=(op.source.effective if op.source and op.source.effective else ref.joustumine),
                    raw_text=op.source.raw_text if op.source else "",
                ),
                sequence=global_seq + i,
                op_id=f"{op.op_id}-{global_seq + i}",
            )
            for i, op in enumerate(ops)
        ]
        global_seq += len(ops)
        all_ops.extend(ops)
        result.amendments_applied.append(ref.aktViide)
        _log(f"    {len(ops)} ops (total so far: {len(all_ops)})")

    precomposition_adjudications: tuple[CompileAdjudication, ...] = ()
    all_ops, precomposition_adjudications = _ee_precompose_pending_amendment_text_patches(
        all_ops,
        refs=tuple(to_apply),
        amendment_xml_by_ref=amendment_xml_by_ref,
    )
    if precomposition_adjudications:
        _log(f"Pending amendment precompositions: {len(precomposition_adjudications)}")

    # FINAL back-fill: attribute any GENERIC structural op (replace/insert/
    # repeal/text_replace minted directly from the amending act's content) that
    # every upstream parse/grafter/target-resolution pass left without a
    # witness_rule_id. ADDITIVE METADATA ONLY — never overwrites an existing id
    # and never changes op identity/payload/action/order, so materialization,
    # divergences, and consistency are byte-identical apart from the field.
    all_ops = _attribute_generic_structural_ops(all_ops)

    result.n_ops = len(all_ops)
    result.compiled_ops = tuple(all_ops)
    _log(f"Total ops: {len(all_ops)}")
    temporal_source_ops: list[LegalOperation] = list(all_ops)
    if pair_plan.base_is_consolidated and not to_apply:
        temporal_refs = _unique_ee_refs(
            [
                ref
                for ref in (*pair_plan.base_refs, *pair_plan.oracle_refs)
                if ref.joustumine and ref.joustumine <= as_of
            ]
        )
        applied_keys = {(ref.aktViide, ref.joustumine) for ref in to_apply}
        for ref in temporal_refs:
            if (ref.aktViide, ref.joustumine) in applied_keys:
                continue
            try:
                amend_xml = _ee_fetch_rt_xml_cached(ref.aktViide, _archive, successful_amendment_xml_cache)
                temporal_ops = parse_ee_amendment_ops(
                    amend_xml,
                    f"ee/{ref.aktViide}",
                    target_title=base.title,
                    ref_effective=ref.joustumine,
                    has_earlier_same_act_slice=any(
                        candidate.aktViide == ref.aktViide
                        and candidate.joustumine
                        and candidate.joustumine < ref.joustumine
                        for candidate in temporal_refs
                    ),
                    adjudications_out=slice_filter_adjudications,
                )
            # lawvm-failloud (AGENTS.md §1.10): NOT a silent swallow. The temporal
            # source scan fetches then parses; its genuinely-expected failures are an
            # unavailable amendment source (fetch RuntimeError / readonly-archive
            # sqlite3.OperationalError) or any unsupported/unparseable amendment shape
            # that parse_ee_amendment_ops raises (ET.ParseError, ValueError, …). The
            # broad catch is intentional and §1.10-compliant because it emits a
            # distinct, BLOCKING ee_temporal_source_scan_failed adjudication embedding
            # exception_type + message; it never degrades to a guess. Contract pinned by
            # test_replay_ee_to_pit_adjudicates_temporal_source_scan_failure.
            except Exception as e:
                _log(f"    temporal scan failed for {ref.aktViide}: {e}")
                source_lane_failure_adjudications.append(
                    _ee_orchestration_adjudication(
                        kind=_EE_TEMPORAL_SOURCE_SCAN_FAILED_RULE,
                        message="Estonia replay skipped temporal source scan because source fetch or parsing failed.",
                        source_statute=f"ee/{ref.aktViide}",
                        detail={
                            "ref_amendment": ref.aktViide,
                            "reason": "temporal_source_scan_failed",
                            "exception_type": type(e).__name__,
                            "exception": str(e),
                            "source_lane_selection": _ee_rt_xml_source_lane_detail(
                                rule_id=_EE_TEMPORAL_SOURCE_SCAN_FAILED_RULE,
                                phase="temporal",
                                reason="temporal_source_scan_failed",
                                akt_viide=ref.aktViide,
                                attempt_status="selected_scan_failed",
                                selected_lane="riigi_teataja_xml",
                                selected=True,
                            ),
                        },
                        phase="temporal",
                        family="source_lane_failure",
                        blocking=True,
                    )
                )
                continue
            temporal_source_ops.extend(
                replace(
                    op,
                    source=OperationSource(
                        statute_id=f"ee/{ref.aktViide}",
                        title=op.source.title if op.source else "",
                        enacted=ref.passed,
                        effective=(op.source.effective if op.source and op.source.effective else ref.joustumine),
                        raw_text=op.source.raw_text if op.source else "",
                    ),
                )
                for op in temporal_ops
            )
    derived_temporal_events = _derive_ee_temporal_expiry_events(
        temporal_source_ops,
        target_statute=base.statute_id,
    )
    result.temporal_events = (*temporal_events, *derived_temporal_events)
    if derived_temporal_events:
        _log(f"Derived temporal expiry events: {len(derived_temporal_events)}")

    # ── Step 5: Apply ops ─────────────────────────────────────────────────────
    lo_ops_out: list[LegalOperation] = []
    adjudications: list[CompileAdjudication] = []
    try:
        try:
            apply_result = apply_ee_ops_conserved(
                base,
                all_ops,
                lo_ops_out=lo_ops_out,
                adjudications_out=adjudications,
            )
            result.replayed = apply_result.statute
            result.apply_filter_result = apply_result.filter_result
        except ValueError:
            # Fall back to non-conserved apply when op_id uniqueness
            # preconditions fail (duplicate op_ids from same-target
            # text_replace runs in old-format wrapper blocks).
            result.replayed = apply_ee_ops(
                base,
                all_ops,
                lo_ops_out=lo_ops_out,
                adjudications_out=adjudications,
            )
    # lawvm-failloud (AGENTS.md §1.10): NOT a silent swallow. An apply-stage
    # failure is recorded as a distinct, self-evidencing "Failed to apply ops:
    # {e}" banner (exception embedded) that classify_ee_replayability maps to
    # REPLAY_ERROR_OTHER; the broad catch is intentional because the failure is
    # surfaced and classified, never absorbed into a partial/guessed tree.
    except Exception as e:
        result.error = f"Failed to apply ops: {e}"
        return result

    result.adjudications = [
        *oracle_parse_adjudications,
        *cancellation_filter_adjudications,
        *commencement_precomposition_adjudications,
        *slice_filter_adjudications,
        *source_lane_failure_adjudications,
        *precomposition_adjudications,
        *adjudications,
    ]
    result.applied_snapshot_ops = tuple(lo_ops_out)
    _log(f"Timeline snapshots emitted: {len(lo_ops_out)}")

    # ── Step 5b: Timeline-primary — compile timelines + materialize PIT ────
    # The replay tree (result.replayed) is internal machinery for address
    # resolution during compilation.  The output is timeline-derived.
    if result.replayed is not None:
        replay_base = result.replayed  # capture pre-PIT tree for base-template
        result.temporal_events = _resolve_ee_temporal_event_scopes(
            result.temporal_events,
            replay_base,
        )
        timelines = compile_timelines(
            replay_base,
            lo_ops_out,
            temporal_events=result.temporal_events,
        )
        pit = materialize_pit(timelines, as_of=as_of, base=replay_base)
        result.replayed = IRStatute(
            statute_id=replay_base.statute_id,
            title=replay_base.title,
            body=pit.body,
            supplements=replay_base.supplements,
            metadata=replay_base.metadata,
        )
        result.timelines = timelines
        _log("Timeline-primary PIT materialized")

    # ── Step 6: Consistency check ─────────────────────────────────────────────
    if result.oracle is not None and result.replayed is not None:
        _log("Running verify_consistency...")
        try:
            replay_tl = _collapse_subdivision_in_timelines(
                ingest_consolidated(result.replayed, as_of="0000-00-00")
            )
            oracle_tl = _collapse_subdivision_in_timelines(
                ingest_consolidated(result.oracle, as_of="0000-00-00")
            )
            divs = verify_consistency(
                replay_tl,
                oracle_tl,
                as_of="0000-00-00",
                irnode_to_text=irnode_to_ee_comparison_text,
                text_normalizer=normalize_ee_comparison_text,
                missing_equals_empty=True,
            )
            result.divergences  = divs
            result.n_mismatch   = sum(1 for d in divs if d.divergence_type == "MISMATCH")
            result.n_ops_missing = sum(1 for d in divs if d.divergence_type == "OPS_MISSING")
            result.n_con_missing = sum(1 for d in divs if d.divergence_type == "CONSOLIDATED_MISSING")
        # lawvm-failloud (AGENTS.md §1.10): the consistency check IS the
        # verification step; a crash here would leave result.divergences unset,
        # making a replay that was never actually compared look divergence-free.
        # That must NOT be a verbose-only WARN — record a distinct,
        # self-evidencing ee_consistency_check_failed adjudication embedding the
        # oracle id and exception so an uncompared result is always accounted
        # for downstream.
        except Exception as e:
            _log(f"WARN: consistency check failed: {e}")
            result.adjudications = [
                *result.adjudications,
                _ee_orchestration_adjudication(
                    kind=_EE_CONSISTENCY_CHECK_FAILED_RULE,
                    message=(
                        "Estonia replay/oracle consistency check crashed; the "
                        "result is left uncompared (no divergences computed) and "
                        "must not be read as agreement."
                    ),
                    source_statute=result.oracle.statute_id,
                    detail={
                        "oracle_id": result.oracle_id or "",
                        "reason": "consistency_check_crashed",
                        "exception_type": type(e).__name__,
                        "exception": str(e),
                    },
                    phase="compare",
                    family="source_lane_failure",
                    blocking=True,
                ),
            ]

    return result
