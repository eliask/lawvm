"""lawvm failures — structured analysis of replay FailedOp records.

Usage:
    lawvm failures                    # summary across bench corpus
    lawvm failures 2012/999           # failures for one statute
    lawvm failures --pattern kohta    # filter by description pattern
    lawvm failures --top 20           # show top N affected statutes
    lawvm failures --detail           # categorize each failure and proof lane
    lawvm failures --detail --json    # emit machine-readable detail rows
    lawvm failures --from-bench v33   # only replay imperfect statutes from bench run
    lawvm failures --parallel 8       # parallel replay workers
"""
from __future__ import annotations

import csv
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from lawvm.core.compile_result import SourcePathology
from lawvm.core.elaboration_context import TargetUnitKind
from lawvm.finland.ops import FailedOp
from lawvm.finland.xml_statute import XMLStatute
from lawvm.finland.replay_entrypoint import replay_xml
from lawvm.finland.source_pathology_proof_registry import source_pathology_proof_rule
from lawvm.finland.replay_request import ReplayXmlRequest, ReplayXmlSinks, call_replay_xml
from lawvm.core.tree_ops import normalized_label_key


# ---------------------------------------------------------------------------
# Corpus loading (match bench.py defaults)
# ---------------------------------------------------------------------------


def _lawvm_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_bench_sids() -> List[str]:
    """Load bench corpus statute IDs using same priority as bench.py."""
    d = _lawvm_dir()
    # Same priority as bench.py _default_corpus_path:
    # 1. bench_core.csv  2. bench_corpus.csv  3. legacy fallback
    for candidate in [
        d / "data" / "finland" / "bench_core.csv",
        d / "data" / "finland" / "bench_corpus.csv",
        d / ".tmp" / "batch_test_list.csv",
    ]:
        if candidate.exists():
            sids = []
            with open(candidate) as f:
                for row in csv.reader(f):
                    if len(row) >= 2:
                        sids.append(row[1].strip())
            return sids
    print("Bench corpus CSV not found", file=sys.stderr)
    return []


def _load_imperfect_sids_from_bench(label: str) -> Optional[List[str]]:
    """Load statute IDs that scored below 1.0 from a bench run.

    Returns None if the labeled run cannot be found.
    """
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    if not runs_dir.exists():
        return None
    candidates = sorted(runs_dir.glob(f"*_{label}.csv"))
    if not candidates:
        return None
    path = candidates[-1]
    sids = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sim_str = row.get("similarity", "ERR")
            try:
                sim = float(sim_str)
            except ValueError:
                # ERR or unparseable — include it (might have failures)
                sids.append(row["statute_id"])
                continue
            if sim < 0.9999:
                sids.append(row["statute_id"])
    return sids


# ---------------------------------------------------------------------------
# Failure cache (sidecar JSON alongside bench runs)
# ---------------------------------------------------------------------------

def _cache_path(label: str) -> Optional[Path]:
    """Return path to failures cache sidecar for a bench label."""
    runs_dir = _lawvm_dir() / "data" / "bench_runs"
    if not runs_dir.exists():
        return None
    return runs_dir / f"failures_{label}.json"


def _save_failure_cache(label: str, failures: List[FailedOp]) -> Path:
    """Serialize FailedOp list to a JSON sidecar file."""
    p = _cache_path(label)
    if p is None:
        p = _lawvm_dir() / "data" / "bench_runs" / f"failures_{label}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
    records = []
    for f in failures:
        scope = f.scope_detail()
        records.append({
            "amendment_id": f.amendment_id,
            "description": f.description,
            "reason": f.reason,
            "reason_code": f.reason_code,
            "target_statute_id": f.target_statute_id,
            "target_unit_kind": f.target_unit_kind,
            "target_section": scope.get("target_section"),
            "target_chapter": scope.get("target_chapter"),
            "target_part": scope.get("target_part"),
            "target_subsection": scope.get("target_subsection"),
            "target_item": scope.get("target_item"),
        })
    p.write_text(json.dumps(records, ensure_ascii=False, indent=1))
    return p


def _load_failure_cache(label: str) -> Optional[List[FailedOp]]:
    """Load FailedOp list from a JSON sidecar file, if it exists."""
    p = _cache_path(label)
    if p is None or not p.exists():
        return None
    try:
        records = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    failures = []
    for r in records:
        failures.append(FailedOp(
            amendment_id=r["amendment_id"],
            description=r["description"],
            reason=r["reason"],
            reason_code=str(r.get("reason_code") or ""),
            target_statute_id=r.get("target_statute_id"),
            target_section=r["target_section"],
            target_chapter=r.get("target_chapter"),
            target_part=r.get("target_part"),
            target_subsection=r.get("target_subsection"),
            target_item=r.get("target_item"),
            target_unit_kind=_failure_target_unit_kind(r),
        ))
    return failures


def _failure_target_unit_kind(record: Dict[str, Any]) -> TargetUnitKind:
    target_unit_kind = record.get("target_unit_kind")
    if target_unit_kind:
        return target_unit_kind
    compat_target_kind_code = str(record.get("target_kind") or "").strip().upper()
    if compat_target_kind_code == "L":
        return "chapter"
    if compat_target_kind_code == "O":
        return "part"
    if compat_target_kind_code in {"P", "A"}:
        return "section"
    raise ValueError(f"Unsupported legacy failure target_kind code: {compat_target_kind_code!r}")


# ---------------------------------------------------------------------------
# Replay collection (sequential + parallel)
# ---------------------------------------------------------------------------

def _replay_one_for_failures(sid: str) -> List[Dict[str, Any]]:
    """Replay one statute, return serializable failure dicts.

    Designed for use with ProcessPoolExecutor (no unpicklable objects).
    """
    failed: List[FailedOp] = []
    try:
        call_replay_xml(
            replay_xml,
            request=ReplayXmlRequest(parent_id=sid, quiet=True),
            sinks=ReplayXmlSinks(failed_ops_out=failed),
        )
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass
    return [
        {
            "sid": sid,
            "amendment_id": f.amendment_id,
            "description": f.description,
            "reason": f.reason,
            "reason_code": f.reason_code,
            "target_statute_id": sid,
            **f.scope_detail(),
            "target_unit_kind": f.target_unit_kind,
        }
        for f in failed
    ]


def _collect_failures(
    sids: List[str],
    verbose: bool = False,
    need_masters: bool = False,
    parallel: int = 1,
) -> Tuple[List[FailedOp], Dict[str, XMLStatute], Dict[str, Set[tuple[str, str, str]]]]:
    """Replay each statute and collect FailedOp records.

    Returns (all_failures, masters_by_sid).
    masters_by_sid is populated only when need_masters=True (requires sequential).
    """
    t0 = time.time()

    if parallel > 1 and not need_masters:
        # Parallel path — cannot return XMLStatute objects (unpicklable)
        return _collect_failures_parallel(sids, verbose, parallel)

    # Sequential path (original behavior, needed for --detail which uses masters)
    all_failures: List[FailedOp] = []
    masters_by_sid: Dict[str, XMLStatute] = {}
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]] = {}
    ok = 0
    for i, sid in enumerate(sids):
        if verbose and (i + 1) % 50 == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(sids) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i+1}/{len(sids)}] {rate:.1f} stat/s, ETA {eta:.0f}s",
                file=sys.stderr,
            )
        try:
            failed: List[FailedOp] = []
            source_pathologies: List[SourcePathology] = []
            master = call_replay_xml(
                replay_xml,
                request=ReplayXmlRequest(parent_id=sid, quiet=True),
                sinks=ReplayXmlSinks(
                    failed_ops_out=failed,
                    source_pathologies_out=source_pathologies,
                ),
            )
            all_failures.extend(dataclass_replace(f, target_statute_id=sid) for f in failed)
            if need_masters and failed:
                masters_by_sid[sid] = master
                pathologies_by_sid[sid] = _source_pathology_keys(source_pathologies)
            ok += 1
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
    if verbose:
        elapsed = time.time() - t0
        print(
            f"Replayed {ok}/{len(sids)} statutes in {elapsed:.1f}s",
            file=sys.stderr,
        )
    return all_failures, masters_by_sid, pathologies_by_sid


def _collect_failures_parallel(
    sids: List[str],
    verbose: bool,
    workers: int,
) -> Tuple[List[FailedOp], Dict[str, XMLStatute], Dict[str, Set[tuple[str, str, str]]]]:
    """Parallel failure collection using ProcessPoolExecutor."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    t0 = time.time()
    all_failures: List[FailedOp] = []
    done = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_replay_one_for_failures, sid): sid for sid in sids
        }
        for future in as_completed(futures):
            done += 1
            if verbose and done % 50 == 0:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(sids) - done) / rate if rate > 0 else 0
                print(
                    f"  [{done}/{len(sids)}] {rate:.1f} stat/s, ETA {eta:.0f}s",
                    file=sys.stderr,
                )
            try:
                records = future.result()
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                continue
            for r in records:
                all_failures.append(FailedOp(
                    amendment_id=r["amendment_id"],
                    description=r["description"],
                    reason=r["reason"],
                    reason_code=str(r.get("reason_code") or ""),
                    target_statute_id=r.get("target_statute_id"),
                    target_section=r["target_section"],
                    target_chapter=r.get("target_chapter"),
                    target_part=r.get("target_part"),
                    target_subsection=r.get("target_subsection"),
                    target_item=r.get("target_item"),
                    target_unit_kind=_failure_target_unit_kind(r),
                ))

    if verbose:
        elapsed = time.time() - t0
        print(
            f"Replayed {done}/{len(sids)} statutes in {elapsed:.1f}s "
            f"({workers} workers)",
            file=sys.stderr,
        )
    return all_failures, {}, {}


def _source_pathology_keys(
    pathologies: List[SourcePathology],
) -> Set[tuple[str, str, str]]:
    return {
        (
            pathology.source_statute,
            pathology.code,
            str(pathology.as_detail().get("target_label", "")),
        )
        for pathology in pathologies
    }


def _collect_detail_masters(
    failures: List[FailedOp],
    verbose: bool = False,
) -> Tuple[Dict[str, XMLStatute], Dict[str, Set[tuple[str, str, str]]]]:
    """Replay only statutes needed to categorize already-collected failures.

    ``--detail`` used to force the full failure collection pass down the
    sequential path because XMLStatute masters are not picklable.  That made
    detail mode replay every imperfect bench row even when only a small subset
    emitted FailedOp records.  Keep the parallel failure scan separate from the
    master materialization pass: once failures are known, only their target
    statutes need masters and source-pathology context.
    """
    target_sids = sorted({f.target_statute_id for f in failures if f.target_statute_id})
    masters_by_sid: Dict[str, XMLStatute] = {}
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]] = {}
    if verbose and target_sids:
        print(
            f"Replaying {len(target_sids)} failed target statutes for detail context",
            file=sys.stderr,
        )
    for sid in target_sids:
        try:
            source_pathologies: List[SourcePathology] = []
            master = call_replay_xml(
                replay_xml,
                request=ReplayXmlRequest(parent_id=sid, quiet=True),
                sinks=ReplayXmlSinks(
                    failed_ops_out=[],
                    source_pathologies_out=source_pathologies,
                ),
            )
            masters_by_sid[sid] = master
            pathologies_by_sid[sid] = _source_pathology_keys(source_pathologies)
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            continue
    return masters_by_sid, pathologies_by_sid


# ---------------------------------------------------------------------------
# Detail categorisation helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class FailureFrontierProjection:
    required_claim_kind: str
    owner_phase: str
    frontier_family: str
    frontier_status: str


@dataclass(frozen=True, slots=True)
class FailureMaterializationProbe:
    probe_status: str
    target_present: bool
    detail: str


@dataclass(frozen=True, slots=True)
class FailureDetailRow:
    failure: FailedOp
    category: str
    frontier_projection: FailureFrontierProjection
    materialization_probe: FailureMaterializationProbe


def _failure_target_label(f: FailedOp) -> str:
    target_paragraph, target_item = _parse_desc_fields(f.description)
    if target_item is not None and target_paragraph is not None:
        return f"{f.target_section} § {target_paragraph} mom {target_item} kohta"
    if target_paragraph is not None:
        return f"{f.target_section} § {target_paragraph} mom"
    return f"{f.target_section} §"


def _parse_desc_fields(desc: str) -> Tuple[Optional[int], Optional[str]]:
    """Extract (target_paragraph, target_item) from a description string.

    AmendmentOp.description() appends:
      " N mom"   when target_paragraph is set
      " X kohta" when target_item is set
    Returns (paragraph_int_or_None, item_str_or_None).
    """
    paragraph: Optional[int] = None
    item: Optional[str] = None
    m_mom = re.search(r'\b(\d+)\s+mom\b', desc)
    if m_mom:
        paragraph = int(m_mom.group(1))
    m_kohta = re.search(r'\b(\S+)\s+kohta\b', desc)
    if m_kohta:
        item = m_kohta.group(1)
    return paragraph, item


def _failure_reason_category(f: FailedOp) -> Optional[str]:
    """Return a replay-time category carried by the FailedOp itself.

    Detail mode also inspects the final replay tree, but final-tree shape is
    not always the same as the live state at the failed operation.  Prefer the
    explicit failed-op reason when it names a replay-time boundary.
    """
    reason_code = (f.reason_code or "").strip().lower()
    if reason_code:
        return f"failed_op:{reason_code}"
    reason = f.reason.strip().lower()
    if reason.startswith("master §") and reason.endswith("not found"):
        return "failed_op:section_not_found"
    return None


def _node_kind_value(node: object) -> str:
    kind = getattr(node, "kind", "")
    return str(getattr(kind, "value", kind))


def _categorize_failure(
    f: FailedOp,
    master: XMLStatute,
    source_pathologies: Optional[Set[tuple[str, str, str]]] = None,
) -> str:
    """Categorize one FailedOp into a replay/frontier label.

    Prefer typed source-pathology or FailedOp reason codes when they exist;
    only fall back to final-master tree heuristics for older/unowned failures.
    Source-pathology categories project to registered proof rules, while other
    replay failures remain in the generic failed-operation resolution lane.
    """
    desc = f.description

    if source_pathologies:
        match = next((code for source_statute, code, target_label in source_pathologies if source_statute == f.amendment_id and target_label == _failure_target_label(f)), None)
        if match:
            return f"source_pathology:{match}"

    # RENUMBER ops — distinct structural category
    if desc.startswith("RENUMBER"):
        return "renumber"

    reason_category = _failure_reason_category(f)
    if reason_category and reason_category != "failed_op:no_deterministic_path":
        return reason_category

    target_paragraph, target_item = _parse_desc_fields(desc)

    # Find the target section in master.ir
    sec_node = master.find_section(f.target_section, f.target_chapter)
    if sec_node is None:
        return "target_section_absent_in_detail_master"

    if target_item is not None:
        # This is a kohta op
        subsecs = [c for c in sec_node.children if _node_kind_value(c) == "subsection"]
        if not subsecs:
            return "kohta_no_subsections"

        # Determine which subsection to examine
        if target_paragraph is not None:
            if target_paragraph > len(subsecs):
                # momentti itself is out of range — kohta_mom_oor
                return "kohta_mom_oor"
            target_sub = subsecs[target_paragraph - 1]
        else:
            target_sub = subsecs[0]

        paras = [c for c in target_sub.children if _node_kind_value(c) == "paragraph"]
        if not paras:
            return "kohta_no_paras"

        # Check if matching label exists
        item_norm = normalized_label_key(target_item)
        matching = [p for p in paras if normalized_label_key(p.label or "") == item_norm]
        if matching:
            return "kohta_amend_extract_fail"

        # Label not found — distinguish an out-of-range numeric target from a
        # missing/synthetic label inside an otherwise populated paragraph list.
        max_label_idx = len(paras)
        item_label = str(target_item)
        try:
            want_idx = int(re.sub(r'[^\d]', '', item_label) or "0")
        except ValueError:
            want_idx = 0
        if want_idx > max_label_idx:
            return f"kohta_label_gap(max={max_label_idx},want={want_idx})"
        if want_idx > 0:
            return f"kohta_label_missing(count={max_label_idx},want={want_idx})"
        return f"kohta_label_missing(count={max_label_idx},want={item_label})"

    if target_paragraph is not None:
        # This is a mom (momentti/subsection) op
        subsecs = [c for c in sec_node.children if _node_kind_value(c) == "subsection"]
        actual_count = len(subsecs)
        if target_paragraph > actual_count:
            gap = target_paragraph - actual_count
            return f"mom_oor(gap={gap})"
        return "mom_amend_extract_fail"

    return "other"


def _frontier_projection_for_failure_category(category: str) -> FailureFrontierProjection:
    if category.startswith("source_pathology:"):
        code = category.split(":", 1)[1]
        rule = source_pathology_proof_rule(code)
        return FailureFrontierProjection(
            required_claim_kind=rule.required_claim_kind,
            owner_phase=rule.owner_phase,
            frontier_family=rule.frontier_family,
            frontier_status=rule.frontier_status,
        )
    if category == "renumber":
        return FailureFrontierProjection(
            required_claim_kind="",
            owner_phase="replay_apply",
            frontier_family="",
            frontier_status="",
        )
    return FailureFrontierProjection(
        required_claim_kind="fi.v1.FAILED_OPERATION_RESOLUTION",
        owner_phase="replay_apply",
        frontier_family="fi_failed_operation_resolution",
        frontier_status="failed_operation_frontier",
    )


def _materialization_probe_for_failure(
    failure: FailedOp,
    master: Optional[XMLStatute],
) -> FailureMaterializationProbe:
    if master is None:
        return FailureMaterializationProbe(
            probe_status="unavailable_no_detail_master",
            target_present=False,
            detail="no replayed target statute master was available for final-tree probing",
        )

    target_paragraph, target_item = _parse_desc_fields(failure.description)
    sec_node = master.find_section(failure.target_section, failure.target_chapter)
    if sec_node is None:
        return FailureMaterializationProbe(
            probe_status="target_section_absent",
            target_present=False,
            detail="target section is absent from the final materialized tree",
        )
    if target_paragraph is None:
        return FailureMaterializationProbe(
            probe_status="target_section_present",
            target_present=True,
            detail="target section is present in the final materialized tree",
        )

    subsecs = [c for c in sec_node.children if _node_kind_value(c) == "subsection"]
    if target_paragraph > len(subsecs):
        return FailureMaterializationProbe(
            probe_status="target_subsection_absent",
            target_present=False,
            detail=f"target subsection {target_paragraph} absent from {len(subsecs)} final subsections",
        )
    target_sub = subsecs[target_paragraph - 1]
    if target_item is None:
        return FailureMaterializationProbe(
            probe_status="target_subsection_present",
            target_present=True,
            detail=f"target subsection {target_paragraph} is present in the final materialized tree",
        )

    paras = [c for c in target_sub.children if _node_kind_value(c) == "paragraph"]
    item_norm = normalized_label_key(target_item)
    if any(normalized_label_key(p.label or "") == item_norm for p in paras):
        return FailureMaterializationProbe(
            probe_status="target_item_present",
            target_present=True,
            detail=f"target item {target_item} is present in the final materialized tree",
        )
    return FailureMaterializationProbe(
        probe_status="target_item_absent",
        target_present=False,
        detail=f"target item {target_item} absent from final labels {[p.label for p in paras]}",
    )


def _find_master_for_failure(
    failure: FailedOp,
    masters_by_sid: Dict[str, XMLStatute],
) -> Optional[XMLStatute]:
    # FailedOp.amendment_id is the amending statute, not the bench statute being
    # amended. New failure rows carry target_statute_id, so prefer that exact
    # master. Keep the section scan as a compatibility fallback for old caches.
    if failure.target_statute_id:
        master = masters_by_sid.get(failure.target_statute_id)
        if master is not None:
            return master
    for master in masters_by_sid.values():
        if master.find_section(failure.target_section, failure.target_chapter) is not None:
            return master
    return None


def _detail_rows(
    failures: List[FailedOp],
    masters_by_sid: Dict[str, XMLStatute],
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]],
    pattern: Optional[str],
) -> List[FailureDetailRow]:
    """Build typed detail rows for human and JSON report surfaces."""
    if pattern:
        failures = [f for f in failures if re.search(pattern, f.description, re.I)]

    rows: List[FailureDetailRow] = []
    for fo in failures:
        master = _find_master_for_failure(fo, masters_by_sid)
        if master is not None:
            sid = fo.target_statute_id or next(
                (sid for sid, master_for_sid in masters_by_sid.items() if master_for_sid is master),
                "",
            )
            cat = _categorize_failure(fo, master, pathologies_by_sid.get(sid))
        else:
            # No master available (section not found in any replayed statute) —
            # still categorize what we can from the description alone
            if fo.description.startswith("RENUMBER"):
                cat = "renumber"
            elif reason_category := _failure_reason_category(fo):
                cat = reason_category
            else:
                cat = "other"
        projection = _frontier_projection_for_failure_category(cat)
        rows.append(
            FailureDetailRow(
                failure=fo,
                category=cat,
                frontier_projection=projection,
                materialization_probe=_materialization_probe_for_failure(fo, master),
            )
        )
    return rows


def _detail_row_record(row: FailureDetailRow) -> Dict[str, Any]:
    failure = row.failure
    projection = row.frontier_projection
    return {
        "amendment_id": failure.amendment_id,
        "target_statute_id": failure.target_statute_id,
        "description": failure.description,
        "reason": failure.reason,
        "reason_code": failure.reason_code,
        "category": row.category,
        "target_unit_kind": failure.target_unit_kind,
        "target_section": failure.target_section,
        "target_chapter": failure.target_chapter,
        "target_part": failure.target_part,
        "required_claim_kind": projection.required_claim_kind,
        "owner_phase": projection.owner_phase,
        "frontier_family": projection.frontier_family,
        "frontier_status": projection.frontier_status,
        "materialized_target_status": row.materialization_probe.probe_status,
        "materialized_target_present": row.materialization_probe.target_present,
        "materialized_target_detail": row.materialization_probe.detail,
    }


def _print_detail_json(rows: List[FailureDetailRow]) -> None:
    records = [_detail_row_record(row) for row in rows]
    materialization_counts: Counter[str] = Counter(
        row.materialization_probe.probe_status for row in rows
    )
    print(
        json.dumps(
            {
                "total_failures": len(records),
                "materialized_target_present": sum(
                    1 for row in rows if row.materialization_probe.target_present
                ),
                "materialized_target_status_counts": dict(materialization_counts),
                "failures": records,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _print_detail_rows(rows: List[FailureDetailRow]) -> None:
    """Print per-failure root-cause categorization with a summary table."""

    cat_counts: Counter[str] = Counter(row.category for row in rows)
    claim_kind_counts: Counter[str] = Counter(
        row.frontier_projection.required_claim_kind or "<none>" for row in rows
    )
    materialization_counts: Counter[str] = Counter(
        row.materialization_probe.probe_status for row in rows
    )

    print(f"Total failures: {len(rows)}")
    print()
    print("=== Root-cause categories ===")
    for cat, count in cat_counts.most_common():
        print(f"  {count:4d}  {cat}")
    print()
    print("=== Required claim kinds ===")
    for claim_kind, count in claim_kind_counts.most_common():
        print(f"  {count:4d}  {claim_kind}")
    print()
    print("=== Final materialized target status ===")
    for status, count in materialization_counts.most_common():
        print(f"  {count:4d}  {status}")
    print()

    print(f"=== Detailed failure list ({len(rows)}) ===")
    for row in rows:
        fo = row.failure
        projection = row.frontier_projection
        target_statute = f" target={fo.target_statute_id}" if fo.target_statute_id else ""
        reason = f" reason={fo.reason_code or fo.reason}" if (fo.reason_code or fo.reason) else ""
        claim = (
            f" claim={projection.required_claim_kind}"
            if projection.required_claim_kind
            else ""
        )
        frontier = (
            f" frontier={projection.frontier_family}/{projection.frontier_status}"
            if projection.frontier_family or projection.frontier_status
            else ""
        )
        materialized = (
            f" materialized={row.materialization_probe.probe_status}"
            if row.materialization_probe.probe_status
            else ""
        )
        print(
            f"  [{fo.amendment_id}]{target_statute} {fo.description}"
            f"  sec={fo.target_section} ch={fo.target_chapter}"
            f"{reason} \u2192 {row.category}"
            f"{claim} owner_phase={projection.owner_phase}{frontier}{materialized}"
        )


def _print_detail(
    failures: List[FailedOp],
    masters_by_sid: Dict[str, XMLStatute],
    pathologies_by_sid: Dict[str, Set[tuple[str, str, str]]],
    pattern: Optional[str],
    top: int,
    *,
    json_output: bool = False,
) -> None:
    rows = _detail_rows(failures, masters_by_sid, pathologies_by_sid, pattern)
    if json_output:
        _print_detail_json(rows)
    else:
        _print_detail_rows(rows)


def _print_summary(failures: List[FailedOp], pattern: Optional[str], top: int) -> None:
    """Print structured failure analysis."""
    if pattern:
        failures = [f for f in failures if re.search(pattern, f.description, re.I)]

    reason_counts = Counter(f.reason for f in failures)
    statute_counts = Counter(f.amendment_id.split("/")[0] + "/" + f.amendment_id.split("/")[1]
                             if "/" in f.amendment_id else f.amendment_id
                             for f in failures)
    target_statute_counts: Counter[str] = Counter(
        f.target_statute_id or "<unknown>" for f in failures
    )
    # Group by target section (within the statute being amended).
    target_counts: Counter[str] = Counter()
    for f in failures:
        target_counts[f.target_section] += 1

    # Pattern analysis
    desc_patterns: Counter[str] = Counter()
    for f in failures:
        m = re.match(r'(INSERT|REPLACE|REPEAL)\s+(\S+)\s+\u00a7\s+(.+)', f.description)
        if m:
            rest_norm = re.sub(r'\d+', 'N', m.group(3))
            desc_patterns[f'{m.group(1)} X \u00a7 {rest_norm}'] += 1
        else:
            desc_patterns[f.description] += 1

    print(f"Total failures: {len(failures)}")
    print()

    print("=== Failure reasons ===")
    for reason, count in reason_counts.most_common(20):
        print(f"  {count:4d}  {reason}")
    print()

    print(f"=== Description patterns (top {top}) ===")
    for pat, count in desc_patterns.most_common(top):
        print(f"  {count:3d}  {pat}")
    print()

    print(f"=== Amendment sources (top {top}) ===")
    for sid, count in statute_counts.most_common(top):
        print(f"  {count:3d}  {sid}")
    print()

    print(f"=== Target statutes (top {top}) ===")
    for sid, count in target_statute_counts.most_common(top):
        print(f"  {count:3d}  {sid}")
    print()

    print(f"=== All failures ({len(failures)}) ===")
    for f in failures:
        target_statute = f" target={f.target_statute_id}" if f.target_statute_id else ""
        print(f"  [{f.amendment_id}]{target_statute} {f.description}  "
              f"kind={f.compat_target_kind_code} sec={f.target_section} ch={f.target_chapter}")


def main(
    statute_id: Optional[str] = None,
    pattern: Optional[str] = None,
    top: int = 15,
    verbose: bool = False,
    detail: bool = False,
    from_bench: Optional[str] = None,
    parallel: int = 1,
    save_cache: Optional[str] = None,
    json_output: bool = False,
) -> int:
    cached: Optional[List[FailedOp]] = None
    if statute_id:
        sids = [statute_id]
    elif from_bench:
        # Try loading from cache first (instant if available)
        cached = _load_failure_cache(from_bench)
        if cached is not None and not detail:
            print(
                f"Loaded {len(cached)} cached failures from bench run "
                f"'{from_bench}'",
                file=sys.stderr,
            )
            _print_summary(cached, pattern, top)
            return 0
        if cached is not None and detail and all(f.target_statute_id for f in cached):
            sids = []
        else:
            # No cache or legacy cache without target statute IDs — filter to
            # imperfect statutes before collecting failures.
            sids_or_none = _load_imperfect_sids_from_bench(from_bench)
            if sids_or_none is None:
                print(
                    f"Bench run '{from_bench}' not found in data/bench_runs/",
                    file=sys.stderr,
                )
                return 1
            sids = sids_or_none
            print(
                f"Replaying {len(sids)} imperfect statutes from bench run "
                f"'{from_bench}'",
                file=sys.stderr,
            )
    else:
        sids = _load_bench_sids()
        if not sids:
            return 1
        print(f"Replaying {len(sids)} statutes...", file=sys.stderr)

    if detail and cached is not None and all(f.target_statute_id for f in cached):
        print(
            f"Loaded {len(cached)} cached failures from bench run "
            f"'{from_bench}'",
            file=sys.stderr,
        )
        failures = cached
        masters, pathologies_by_sid = _collect_detail_masters(
            failures, verbose=verbose,
        )
    else:
        failures, masters, pathologies_by_sid = _collect_failures(
            sids, verbose=verbose, need_masters=False, parallel=parallel,
        )
        if detail:
            masters, pathologies_by_sid = _collect_detail_masters(
                failures, verbose=verbose,
            )

    # Save cache if requested
    cache_label = save_cache or from_bench
    if cache_label and not detail:
        p = _save_failure_cache(cache_label, failures)
        print(f"Saved {len(failures)} failures to {p}", file=sys.stderr)

    if detail:
        _print_detail(
            failures,
            masters,
            pathologies_by_sid,
            pattern,
            top,
            json_output=json_output,
        )
    else:
        _print_summary(failures, pattern, top)
    return 0
