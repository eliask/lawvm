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
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path
from typing import Any, List, Optional

from lawvm.core.compile_result import (
    TemporalEvent,
)
from lawvm.core.temporal import TemporalScope
from lawvm.replay_adjudication import CompileAdjudication, SourceAdjudication
from lawvm.core.ir import IRStatute, LegalAddress, LegalOperation, OperationSource
from lawvm.core.timeline import compile_timelines, materialize_pit
from lawvm.core.timeline_consistency import ingest_consolidated, verify_consistency
from lawvm.estonia.grafter import (
    _extract_intro_statute_fragment,
    _first_tavatekst_text,
    _normalize_num,
    _strict_title_match_para,
    _title_matches_para,
    apply_ee_ops,
    parse_ee_amendment_ops,
    parse_ee_statute,
)
from lawvm.estonia.peg import parse_html_op_items
from lawvm.estonia.pair_planning import EEOraclePairPlan, plan_ee_oracle_pair
from lawvm.estonia.compare import irnode_to_ee_comparison_text, normalize_ee_comparison_text
from lawvm.estonia.fetch import (
    AmendmentRef,
    fetch_rt_xml,
    open_rt_archive,
)


def _ee_ref_sort_key(ref) -> tuple[str, str, str]:
    return (ref.joustumine, ref.passed, ref.aktViide)


def _ee_xml_ns(root: ET.Element) -> str:
    return root.tag.split("}")[0].strip("{")


def _ee_extract_act_title(xml_bytes: bytes) -> str:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
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


def _ee_extract_target_matching_paragraph_numbers(xml_bytes: bytes, target_title: str) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
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


def _ee_extract_repealed_source_paragraph_numbers(
    xml_bytes: bytes,
    amended_act_title: str,
) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
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
) -> set[str]:
    try:
        root = ET.fromstring(xml_bytes)
    except Exception:
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


def _ee_filter_cancelled_pending_refs(
    refs: list[AmendmentRef],
    *,
    target_title: str,
    archive: Any,
) -> list[AmendmentRef]:
    if len(refs) < 2 or not target_title:
        return refs

    ref_xml: dict[str, bytes] = {}
    ref_titles: dict[str, str] = {}
    target_sections: dict[str, set[str]] = {}
    for ref in refs:
        try:
            xml_bytes = fetch_rt_xml(ref.aktViide, archive)
        except Exception:
            continue
        ref_xml[ref.aktViide] = xml_bytes
        ref_titles[ref.aktViide] = _ee_extract_act_title(xml_bytes)
        target_sections[ref.aktViide] = _ee_extract_target_matching_paragraph_numbers(
            xml_bytes,
            target_title,
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
            )
            if target_paras and target_paras.issubset(repealed_paras):
                cancelled.add(ref.aktViide)
                break
            rewritten_paras = _ee_extract_rewritten_source_paragraph_numbers(
                repealer_xml,
                ref_title,
            )
            if target_paras and target_paras.issubset(rewritten_paras):
                cancelled.add(ref.aktViide)
                break

    return [ref for ref in refs if ref.aktViide not in cancelled]


def _ee_filter_ops_for_ref_slice(
    ops: list[LegalOperation],
    *,
    ref: AmendmentRef,
    base_refs: tuple[AmendmentRef, ...],
    all_refs: tuple[AmendmentRef, ...] = (),
    as_of: str = "",
) -> list[LegalOperation]:
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

    if any_local_slice_ops:
        filtered_ops: list[LegalOperation] = []
        for op in ops:
            effective = op.source.effective if op.source is not None else ""
            if not effective:
                if not has_earlier_slice:
                    filtered_ops.append(op)
                continue
            effective_window_date = effective
            if effective < ref.joustumine:
                if has_earlier_slice:
                    continue
                effective_window_date = ref.joustumine
            if next_later_ref_date and effective_window_date >= next_later_ref_date:
                continue
            if as_of and effective > as_of:
                continue
            filtered_ops.append(op)
        return filtered_ops

    return ops


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
    timelines: Optional[dict] = None
    temporal_events: tuple[TemporalEvent, ...] = ()

    # Consistency check
    divergences: list = field(default_factory=list)
    n_mismatch: int = 0
    n_ops_missing: int = 0    # in oracle but not in replay
    n_con_missing: int = 0    # in replay but not in oracle

    # Error
    error: Optional[str] = None

    # Optional replay-adjudication stream from operation application.
    adjudications: list[CompileAdjudication] = field(default_factory=list)


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
    except Exception as e:
        result.error = f"Failed to load base: {e}"
        return result

    try:
        base = parse_ee_statute(base_xml, f"ee/{base_id}")
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

    if pair_plan.oracle_is_base:
        result.oracle = base
    elif planning.oracle_xml is not None and pair_plan.oracle_id is not None:
        try:
            result.oracle = parse_ee_statute(planning.oracle_xml, f"ee/{pair_plan.oracle_id}")
        except Exception as e:
            _log(f"WARN: oracle parse failed: {e}")

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
    to_apply = _ee_filter_cancelled_pending_refs(
        sorted(pair_plan.amendments_to_apply, key=_ee_ref_sort_key),
        target_title=base.title,
        archive=_archive,
    )
    to_skip = [
        ref for ref in pair_plan.base_refs if ref.aktViide not in {x.aktViide for x in to_apply}
    ]
    result.amendments_skipped = [r.aktViide for r in to_skip]
    _log(f"Apply: {len(to_apply)} | Skip: {len(to_skip)}")

    # ── Step 4: Fetch + parse ops ─────────────────────────────────────────────
    all_ops: List[LegalOperation] = []
    global_seq = 1

    for ref in sorted(to_apply, key=_ee_ref_sort_key):
        _log(f"  {ref.aktViide}  effective={ref.joustumine}...")
        try:
            amend_xml = fetch_rt_xml(ref.aktViide, _archive)
        except Exception as e:
            _log(f"    fetch failed: {e}")
            result.amendments_failed.append(ref.aktViide)
            continue

        try:
            same_act_refs = tuple(
                candidate
                for candidate in (*pair_plan.base_refs, *pair_plan.amendments_to_apply)
                if candidate.aktViide == ref.aktViide and candidate.joustumine
            )
            ops = parse_ee_amendment_ops(amend_xml, f"ee/{ref.aktViide}",
                                         target_title=base.title,
                                         ref_effective=ref.joustumine,
                                         has_earlier_same_act_slice=any(
                                             candidate.joustumine < ref.joustumine
                                             for candidate in same_act_refs
                                         ))
        except Exception as e:
            _log(f"    parse failed: {e}")
            result.amendments_failed.append(ref.aktViide)
            continue
        ops = _ee_filter_ops_for_ref_slice(
            ops,
            ref=ref,
            base_refs=pair_plan.base_refs,
            all_refs=pair_plan.amendments_to_apply,
            as_of=as_of,
        )

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
            )
            for i, op in enumerate(ops)
        ]
        global_seq += len(ops)
        all_ops.extend(ops)
        result.amendments_applied.append(ref.aktViide)
        _log(f"    {len(ops)} ops (total so far: {len(all_ops)})")

    result.n_ops = len(all_ops)
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
                amend_xml = fetch_rt_xml(ref.aktViide, _archive)
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
                )
            except Exception as e:
                _log(f"    temporal scan failed for {ref.aktViide}: {e}")
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
    lo_ops_out: list = []
    adjudications: list[CompileAdjudication] = []
    try:
        result.replayed = apply_ee_ops(
            base,
            all_ops,
            lo_ops_out=lo_ops_out,
            adjudications_out=adjudications,
        )
    except Exception as e:
        result.error = f"Failed to apply ops: {e}"
        return result

    result.adjudications = adjudications
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
            replay_tl = ingest_consolidated(result.replayed, as_of="0000-00-00")
            oracle_tl  = ingest_consolidated(result.oracle, as_of="0000-00-00")
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
        except Exception as e:
            _log(f"WARN: consistency check failed: {e}")

    return result
