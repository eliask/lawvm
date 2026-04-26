"""lawvm oracle-check — classify divergences as replay bugs vs oracle issues.

Runs the explain heuristics across the full corpus (or a subset) and
produces a classification summary. Separates "our bugs" from "their bugs"
and computes an adjusted score excluding known oracle issues.

Usage:
    lawvm oracle-check <statute_id>              # one statute
    lawvm oracle-check --corpus                  # configured corpus list (.tmp/batch_test_list.csv)
    lawvm oracle-check --corpus --save           # save results to CSV
    lawvm oracle-check --corpus-full             # expanded corpus list (.tmp/migration/expanded_batch_test_list.csv)
    lawvm oracle-check --corpus-full --db divergences.db  # write SQLite
    lawvm oracle-check --corpus-full --parallel 16        # concurrency
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import csv
import io
import json
import sqlite3
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Literal, Optional, Tuple, cast

if TYPE_CHECKING:
    from lawvm.core.ir import IRNode  # noqa: F401

import Levenshtein
from lxml import etree

from lawvm.tools.editorial_hygiene import (
    strip_editorial_annotations,
    strip_kumottu_attribution,
    strip_temporary_residue_annotations,
)
from lawvm.tools.divergence_heuristics import blame_title_indicates_temporary_amendment
from lawvm.tools.divergence_heuristics import blame_source_postdates_oracle_version
from lawvm.tools.divergence_heuristics import is_probable_repeal_stale_oracle
from lawvm.tools.divergence_heuristics import oracle_has_future_repeal_overlay
from lawvm.tools.divergence_heuristics import oracle_has_repeal_banner_with_prior_wording
from lawvm.tools.divergence_heuristics import oracle_section_duplicates_adjacent_section
from lawvm.tools.divergence_heuristics import oracle_text_has_removable_duplicate_sentence
from lawvm.tools.divergence_heuristics import oracle_text_reduces_to_replay_by_dropping_sentences
from lawvm.tools.divergence_heuristics import oracle_text_reduces_to_bare_section_stub
from lawvm.tools.divergence_heuristics import replay_section_matches_text_at_cutoff
from lawvm.tools.divergence_heuristics import replay_section_has_future_effective_version
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.replay_products import fi_label_norm
from lawvm.tools.section_keys import (
    extract_ir_sections,
    extract_oracle_sections,
    norm_section_label,
    reconcile_unique_unscoped_aliases,
    section_key_from_compile_failure,
    section_key_from_compiled_scope_row,
)
from lawvm.finland.grafter import (
    _resolve_applicable_amendment_records,
    get_consolidated_oracle_context,
    get_corpus,
    get_ground_truth_tree,
    process_muutoslaki,
    replay_xml,
)
from lawvm.finland.corpus import get_consolidated_meta as _get_consolidated_meta
from lawvm.finland.statute import StatuteContext, ReplayState
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.tools.classify_result import ClassifyResult
from lawvm.tools._worker_pool import managed_executor


_LATEST_CONSOLIDATED_SELECTOR = ConsolidatedArtifactSelector.latest_cached_editorial()
get_consolidated_meta = _get_consolidated_meta


# ---------------------------------------------------------------------------
# Shared helpers (from explain.py — kept independent)
# ---------------------------------------------------------------------------

def _tag(el: etree._Element) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


def _num_text(el: etree._Element) -> str:
    num = el.find("{*}num")
    if num is None:
        num = el.find("num")
    if num is not None and num.text:
        return num.text.strip()
    return ""


def _norm_num(s: str) -> str:
    # Also strip trailing periods (pre-1980s nums like "1 §.")
    return norm_section_label(s)


def _el_text(el: etree._Element) -> str:
    return etree.tostring(el, method="text", encoding="unicode").strip()


def _clean(text: str) -> str:
    return re.sub(r'[^a-z0-9äöå]', '', text.lower())


def _extract_sections(root: etree._Element) -> Dict[str, etree._Element]:
    # Oracle check needs kumottu tombstones to classify EDITORIAL_CONVENTION
    # (replay repeal-placeholder vs oracle kumottu notice → same legal state).
    return extract_oracle_sections(root, exclude_kumottu_stubs=False)


def _oracle_root_has_content_absent(root: etree._Element) -> bool:
    return bool(
        root.xpath('.//*[local-name()="hcontainer" and @name="contentAbsent"]')
    )


def _source_pathology_diagnosis_for_blame(
    master: object,
    blame_op: dict[str, Any] | None,
) -> str | None:
    """Return SOURCE_PATHOLOGY when replay already emitted ownership evidence."""
    if not blame_op:
        return None

    blame_source = str(blame_op.get("source_statute", "") or "")
    if not blame_source:
        return None

    target_section = str(blame_op.get("target_norm", "") or "")
    target_paragraph = str(blame_op.get("target_paragraph", "") or "")
    target_item = str(blame_op.get("target_item", "") or "")
    target_chapter = str(blame_op.get("target_chapter", "") or "")

    source_pathology_rows = getattr(master, "source_pathology_rows", None)
    if not callable(source_pathology_rows):
        return None

    exact_target_label = ""
    if target_section and target_paragraph and target_item:
        exact_target_label = f"{target_section} § {target_paragraph} mom {target_item} kohta"
    elif target_section and target_paragraph:
        exact_target_label = f"{target_section} § {target_paragraph} mom"

    matched_codes: set[str] = set()
    for row in source_pathology_rows() or ():
        if str(row.get("source_statute") or "") != blame_source:
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        detail = row.get("detail") if isinstance(row, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        detail_target_chapter = str(detail.get("target_chapter") or "")
        if target_chapter and detail_target_chapter and detail_target_chapter != target_chapter:
            continue
        if exact_target_label and str(row.get("target_label") or "") == exact_target_label:
            matched_codes.add(code)
            continue
        if (
            target_section
            and str(detail.get("target_section") or "") == target_section
            and (not target_paragraph or str(detail.get("target_paragraph") or "") == target_paragraph)
            and (not target_item or str(detail.get("target_item") or "") == target_item)
        ):
            matched_codes.add(code)

    if not matched_codes:
        return None

    findings = tuple(getattr(master, "findings", ()) or ())
    has_degraded_coverage = any(
        getattr(finding, "kind", "") == "COVERAGE.HIGH_UNCOVERED_BODY_DEGRADED"
        and (
            getattr(finding, "source_statute", "") == blame_source
            or str((getattr(finding, "detail", {}) or {}).get("amendment_id") or "") == blame_source
        )
        for finding in findings
    )
    has_failed_no_deterministic = any(
        getattr(finding, "kind", "") == "APPLY.FAILED_OPERATION"
        and str((getattr(finding, "detail", {}) or {}).get("amendment_id") or "") == blame_source
        and str((getattr(finding, "detail", {}) or {}).get("reason_code") or "") == "no_deterministic_path"
        and str((getattr(finding, "detail", {}) or {}).get("target_section") or "") == target_section
        and str((getattr(finding, "detail", {}) or {}).get("target_chapter") or "") == target_chapter
        for finding in findings
    )
    if not has_degraded_coverage and not has_failed_no_deterministic:
        return None
    return "SOURCE_PATHOLOGY"


def _section_points_to_empty_body_amendment(
    oracle_el: etree._Element | None,
    oracle_text: str,
    amendment_ids: set[str],
) -> bool:
    """True when oracle evidence points at a known empty-operative-body amendment."""
    if oracle_el is None or not amendment_ids:
        return False

    attr_blob = " ".join(str(value) for value in oracle_el.attrib.values())
    text_blob = f"{attr_blob}\n{oracle_text}"
    for amendment_id in amendment_ids:
        year, _, number = amendment_id.partition("/")
        if not year or not number:
            continue
        if amendment_id in text_blob:
            return True
        compact = f"@{year}{number.zfill(4)}"
        if compact in text_blob:
            return True
    return False


def _section_label_text_from_key(section_key: str) -> str:
    short_label = section_key.rsplit("section:", 1)[-1] if "section:" in section_key else section_key
    spaced = re.sub(r"(?<=\d)(?=[A-Za-zÅÄÖåäö])", " ", short_label)
    return f"{spaced} §".strip()


def _blame_section_points_to_empty_body_origin(
    blame_source: str,
    section_key: str,
    empty_body_sources: set[str],
) -> bool:
    """True when the blame amendment explicitly back-references the section to an empty-body source."""
    if not blame_source or not empty_body_sources:
        return False
    year, _, number = blame_source.partition("/")
    if not year or not number:
        return False
    xml_bytes = get_corpus().read_locator(f"finlex://sd/{year}/{number}/fin/main.xml")
    if xml_bytes is None:
        return False
    try:
        source_tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return False
    source_text = " ".join(str(source_tree.xpath("string(.)")).split())
    if not source_text:
        return False

    section_label = _section_label_text_from_key(section_key)
    for amendment_id in empty_body_sources:
        src_year, _, src_number = amendment_id.partition("/")
        if not src_year or not src_number:
            continue
        citation = f"{int(src_number)}/{src_year}"
        pattern = re.compile(
            rf"{re.escape(section_label)}\s+"
            rf"(?:laissa|asetuksessa|päätöksessä)\s+"
            rf"{re.escape(citation)}\b",
            re.IGNORECASE,
        )
        if pattern.search(source_text):
            return True
    return False


def _raw_master_source_lacks_section(
    statute_id: str,
    section_key: str,
) -> bool:
    """True when the raw master source lane does not contain the section at all."""
    year, _, number = statute_id.partition("/")
    if not year or not number:
        return False
    xml_bytes = get_corpus().read_locator(f"finlex://sd/{year}/{number}/fin/main.xml")
    if xml_bytes is None:
        return False
    try:
        source_tree = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return False

    target_label = norm_section_label(_section_label_text_from_key(section_key))
    section_els = cast(List[etree._Element], source_tree.xpath('.//*[local-name()="section"]'))
    for section_el in section_els:
        num_el = section_el.find('{*}num')
        if num_el is None:
            continue
        if norm_section_label(" ".join(str(num_el.xpath("string(.)")).split())) == target_label:
            return False
    return True


def _extract_attachment_info(root: etree._Element) -> tuple:
    """Return (count, [title_str]) of individual attachment hcontainers."""
    atts = cast(List[etree._Element], root.xpath('.//*[local-name()="hcontainer" and @name="attachment"]'))
    titles = []
    for att in atts:
        h = att.find(".//{*}heading")
        p = att.find(".//{*}p")
        if h is not None and (h.text or "").strip():
            titles.append((h.text or "").strip()[:60])
        elif p is not None:
            titles.append(_el_text(p)[:60])
    return len(atts), titles


def _extract_attachment_info_ir(body: "IRNode") -> "Tuple[int, List[str]]":
    """Return (count, [title_str]) of attachment hcontainers from an IRNode tree."""
    def _collect(node: IRNode, results: list) -> None:
        if node.kind == "hcontainer" and node.attrs.get("name") == "attachment":
            title = ""
            for child in node.children:
                if child.kind == "heading" and child.text.strip():
                    title = child.text.strip()[:60]
                    break
            if not title:
                for child in node.children:
                    if child.kind == "p":
                        # Gather all text from p node (including nested children)
                        def _node_text(n: IRNode) -> str:
                            parts = [n.text] if n.text else []
                            for c in n.children:
                                parts.append(_node_text(c))
                            return "".join(parts)
                        title = _node_text(child).strip()[:60]
                        break
            results.append(title)
        for child in node.children:
            _collect(child, results)

    titles: List[str] = []
    _collect(body, titles)
    return len(titles), titles


def _ir_node_has_repeal_placeholder(node: Any) -> bool:
    """Return True if this IR node OR any descendant carries lawvm_repeal_placeholder=1.

    Used to check whether a compared section node (or any of its sub-nodes such
    as a subsection/momentti or item/kohta) is a repeal placeholder.  This allows
    REPEAL_NOTICE classification to work at ALL addressable levels, not just when
    the top-level section is fully repealed.
    """
    if getattr(node, "attrs", {}).get("lawvm_repeal_placeholder") == "1":
        return True
    for child in getattr(node, "children", ()):
        if _ir_node_has_repeal_placeholder(child):
            return True
    return False


def _score_pair(r_el: etree._Element, o_el: etree._Element) -> float:
    r = _clean(_el_text(r_el))
    o = _clean(_el_text(o_el))
    if not r and not o:
        return 1.0
    if not r or not o:
        return 0.0
    return Levenshtein.ratio(r, o)


def _build_blame_map(compiled_ops: list) -> Dict[str, dict]:
    blame: Dict[str, dict] = {}
    for op in compiled_ops:
        key = section_key_from_compiled_scope_row(op)
        if key:
            blame[key] = op
    return blame


def _lookup_blame_op(blame_map: Dict[str, dict], key: str) -> dict:
    exact = blame_map.get(key)
    if exact is not None:
        return exact
    if "/" not in key:
        return {}
    suffix = key.split("/")[-1]
    matches = [
        op
        for blame_key, op in blame_map.items()
        if blame_key == suffix or blame_key.endswith("/" + suffix)
    ]
    if len(matches) == 1:
        return matches[0]
    return {}


# Oracle issues = not caused by our replay logic
# CORRIGENDUM_APPLIED: LawVM applied a corrigendum (legal_pit mode); Finlex has not.
#   LawVM output is legally correct; Finlex is stale at the corrigendum level.
#   This is the inverse of ORACLE_STALE and equally interesting as a finding.
ORACLE_CATEGORIES = {
    "ORACLE_STALE",
    "EDITORIAL_CONVENTION",
    "CORRIGENDUM_APPLIED",
    "SOURCE_PATHOLOGY",
}

# Minimum score threshold to consider pre-blame state a match for oracle
PRE_BLAME_THRESHOLD = 0.95
PRE_BLAME_IMPROVEMENT_EPS = 0.01


def _oracle_suspect_value(master: object) -> str:
    if not hasattr(master, "source_adjudication"):
        return ""
    source_adjudication = master.source_adjudication
    if source_adjudication is None or not hasattr(source_adjudication, "oracle_suspect"):
        return ""
    return str(source_adjudication.oracle_suspect or "")


def _diagnose(
    r_text: str,
    o_text: str,
    blame_op: Optional[dict],
    *,
    oracle_selector_mode: str = "latest_cached_editorial",
) -> str:
    o_without_temporary = strip_temporary_residue_annotations(o_text)
    if o_without_temporary != o_text:
        if (
            oracle_selector_mode == "bench_comparable"
            and oracle_text_reduces_to_bare_section_stub(o_text)
        ):
            return "EDITORIAL_CONVENTION"
        if Levenshtein.ratio(_clean(r_text), _clean(o_without_temporary)) >= 0.95:
            return "ORACLE_STALE"

    stub_text = strip_editorial_annotations(o_text)
    lowered = o_text.lower()
    if not any(token in lowered for token in ("kumottu", "väliaik", "voimassa", "tulee voimaan")):
        stripped_stub = re.sub(
            r"^\d+\s*[a-zäöå]?\s*§\s*",
            "",
            stub_text,
            count=2,
            flags=re.IGNORECASE,
        ).strip()
        if not stripped_stub:
            return "EDITORIAL_CONVENTION"

    r_stripped = strip_editorial_annotations(r_text)
    o_stripped = strip_editorial_annotations(o_text)
    if Levenshtein.ratio(_clean(r_stripped), _clean(o_stripped)) >= 0.999:
        return "EDITORIAL_CONVENTION"

    # Kumottu attribution: replay says "X § on kumottu." oracle says
    # "X § on kumottu L:lla DD.MM.YYYY/NNN." — strip attribution and compare
    if 'kumottu' in r_text and 'kumottu' in o_text:
        r_k = strip_kumottu_attribution(r_text)
        o_k = strip_kumottu_attribution(o_text)
        if Levenshtein.ratio(_clean(r_k), _clean(o_k)) >= 0.95:
            return "EDITORIAL_CONVENTION"

    # Compare cleaned (alphanumeric-only) versions for accurate length/similarity
    c_r = _clean(r_text)
    c_o = _clean(o_text)
    if c_r and c_o:
        sim = Levenshtein.ratio(c_r, c_o)
        if sim >= 0.95:
            return "EDITORIAL_CONVENTION"

    if oracle_text_has_removable_duplicate_sentence(r_text, o_text):
        return "ORACLE_STALE"

    c_diff = len(c_r) - len(c_o)
    if c_diff > 40:
        return "REPLAY_EXTRA"
    if c_diff < -40:
        return "REPLAY_MISSING"
    return "UNKNOWN"


def _batch_pre_blame_sections(
    sid: str, blame_sources: List[str], mode: Literal["finlex_oracle", "legal_pit"]
) -> Dict[str, tuple]:
    """Replay sid once, snapshotting IR at each blame stop point.

    Returns {blame_source: (sections_dict, last_amendment_id_applied)} for all
    requested blame_sources. Single pass through the amendment chain —
    O(A) instead of O(B × A) where B=blame sources, A=amendments.
    """
    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        return {src: ({}, None) for src in blame_sources}

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    amendment_records, _, _ = _resolve_applicable_amendment_records(sid, mode)

    wanted = set(blame_sources)
    result: Dict[str, tuple] = {}
    last_amendment_id: Optional[str] = None
    for rec in amendment_records:
        amendment_id = str(rec["statute_id"])
        if amendment_id in wanted:
            # Snapshot IR before applying this amendment
            result[amendment_id] = (extract_ir_sections(state.ir), last_amendment_id)
            wanted.discard(amendment_id)
            if not wanted:
                break
        _null = io.StringIO()
        with contextlib.redirect_stdout(_null):
            state = process_muutoslaki(
                amendment_id,
                state,
                ctx,
                replay_mode=mode,
                parent_id=sid,
            ).output
        last_amendment_id = amendment_id

    # Any blame sources not found in amendment chain get empty result
    for src in blame_sources:
        if src not in result:
            result[src] = ({}, None)
    return result


def _get_pre_blame_sections(
    sid: str, stop_before_source: str, mode: Literal["finlex_oracle", "legal_pit"]
) -> tuple:
    """Replay sid stopping before the given source amendment.

    Returns (sections_dict, last_amendment_id_applied) where last_amendment_id_applied is the
    statute_id of the last amendment that was incorporated before stop_before_source
    (i.e. the version Finlex appears to have), or None if no amendments preceded it.
    """
    result = _batch_pre_blame_sections(sid, [stop_before_source], mode)
    return result.get(stop_before_source, ({}, None))


# ---------------------------------------------------------------------------
# Per-statute classification
# ---------------------------------------------------------------------------

def _classify_statute(
    sid: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    *,
    replay_result: Optional[Any] = None,
    precomputed_compiled_ops: Optional[List] = None,
    oracle_root: Optional[Any] = None,
    html_audit_result: Optional[Any] = None,
) -> Optional[ClassifyResult]:
    """Classify all divergences for one statute. Returns a ClassifyResult.

    Optional pre-computed parameters avoid redundant replay/fetch calls
    when invoked from build_evidence_bundle which already has these objects.
    The returned ClassifyResult exposes ``replay_result`` and ``compiled_ops``
    attributes so callers can extract and reuse them.
    """
    try:
        if replay_result is not None:
            master = replay_result
            compiled_ops = precomputed_compiled_ops if precomputed_compiled_ops is not None else []
            failed_ops: list = []
        else:
            compiled_ops: list = precomputed_compiled_ops if precomputed_compiled_ops is not None else []
            failed_ops = []
            master = replay_xml(
                sid,
                mode=mode,
                compiled_ops_out=compiled_ops,
                failed_ops_out=failed_ops,
                quiet=True,
            )
        oracle_ctx = get_consolidated_oracle_context(
            sid,
            selector=_LATEST_CONSOLIDATED_SELECTOR,
        )
        meta_cutoff_date = None
        meta_oracle_version_amendment_id = None
        try:
            meta_cutoff_date, meta_oracle_version_amendment_id = get_consolidated_meta(sid)
        except Exception:
            meta_cutoff_date = None
            meta_oracle_version_amendment_id = None
        if oracle_root is None:
            oracle_root = get_ground_truth_tree(sid)
            if oracle_root is None:
                return ClassifyResult(sid=sid, error="NO_ORACLE")
        oracle_content_absent = _oracle_root_has_content_absent(oracle_root)
        if oracle_ctx.locator:
            oracle_cutoff_date = meta_cutoff_date or oracle_ctx.cutoff_date
            oracle_version_amendment_id = meta_oracle_version_amendment_id or oracle_ctx.oracle_version_amendment_id or None
        else:
            oracle_cutoff_date = oracle_ctx.cutoff_date or meta_cutoff_date
            oracle_version_amendment_id = oracle_ctx.oracle_version_amendment_id or meta_oracle_version_amendment_id or None
        selector_mode = str(getattr(oracle_ctx, "selector_mode", "") or "latest_cached_editorial")

        from lawvm.core.ir_helpers import irnode_to_text
        replay_secs_ir = extract_ir_sections(master.materialized_state.ir)
        oracle_secs = _extract_sections(oracle_root)
        replay_secs_ir, oracle_secs = reconcile_unique_unscoped_aliases(
            replay_secs_ir, oracle_secs
        )
        failed_section_keys = {
            key
            for failure in failed_ops
            for key in [section_key_from_compile_failure(failure)]
            if key
        }
        source_pathology_rows = getattr(master, "source_pathology_rows", None)
        if not callable(source_pathology_rows):
            raise TypeError("replay result must expose source_pathology_rows()")
        source_pathologies = [
            {
                "code": str(pathology.get("code") or ""),
                "message": str(pathology.get("message") or ""),
                "source_statute": str(pathology.get("source_statute") or ""),
                "target_unit_kind": str(pathology.get("target_unit_kind") or ""),
                "target_label": str(pathology.get("target_label") or ""),
                "detail": dict(pathology.get("detail") or {}) if isinstance(pathology.get("detail"), dict) else {},
            }
            for pathology in source_pathology_rows()
            if isinstance(pathology, dict)
        ]
        oracle_suspect = _oracle_suspect_value(master)
        html_topology = {
            "mismatch": False,
            "missing_from_xml": [],
            "extra_in_xml": [],
            "html_error": "",
            "noncommensurable_reason": "",
        }
        try:
            if html_audit_result is None:
                from lawvm.tools.audit import _audit_html_one
                html_audit_result = _audit_html_one(sid)
            html_topology = {
                "mismatch": bool(
                    not getattr(html_audit_result, "noncommensurable_reason", "")
                    and (html_audit_result.missing_from_xml or html_audit_result.extra_in_xml)
                ),
                "missing_from_xml": list(html_audit_result.missing_from_xml),
                "extra_in_xml": list(html_audit_result.extra_in_xml),
                "html_error": html_audit_result.html_error,
                "noncommensurable_reason": getattr(html_audit_result, "noncommensurable_reason", ""),
            }
        except (NameError, TypeError, AttributeError):
            raise  # programming bugs — fail loud
        except Exception:
            pass
        contingent_effective_sources = sorted(
            {
                str(finding.source_statute)
                for finding in master.findings
                if getattr(finding, "kind", "") == "TIME.CONTINGENT_EFFECTIVE_DATE"
                and getattr(finding, "source_statute", "")
            }
        )
        blame_map = _build_blame_map(compiled_ops)

        # Overall score (full body text)
        c_res = _clean(master.serialize_text())
        c_truth = _clean(_el_text(oracle_root))
        overall_score = Levenshtein.ratio(c_res, c_truth) if c_res and c_truth else 0.0

        # Section-only score
        r_sec_text = _clean("".join(irnode_to_text(n) for n in replay_secs_ir.values()))
        o_sec_text = _clean("".join(_el_text(el) for el in oracle_secs.values()))
        # Build text map for downstream comparison
        replay_secs_text = {k: irnode_to_text(n) for k, n in replay_secs_ir.items()}
        oracle_secs_text = {k: _el_text(el) for k, el in oracle_secs.items()}
        replay_secs = replay_secs_ir  # for key iteration
        section_score = Levenshtein.ratio(r_sec_text, o_sec_text) if r_sec_text and o_sec_text else 0.0

        section_results: List[Dict] = []
        for key in set(replay_secs) | set(oracle_secs):
            r_node = replay_secs.get(key)
            o_el = oracle_secs.get(key)
            r_text = replay_secs_text.get(key, "") if r_node is not None else ""
            o_text = _el_text(o_el) if o_el is not None else ""
            blame_op = _lookup_blame_op(blame_map, key)
            future_version = replay_section_has_future_effective_version(
                master,
                key,
                oracle_cutoff_date,
            )
            if r_node is None or o_el is None:
                if r_node is None:
                    diag = "MISSING"
                    if o_el is not None:
                        if oracle_text_reduces_to_bare_section_stub(o_text):
                            diag = "EDITORIAL_CONVENTION"
                        elif mode == "legal_pit" and blame_title_indicates_temporary_amendment(
                            str(blame_op.get("source_title", ""))
                        ):
                            diag = "ORACLE_STALE"
                else:
                    diag = "EXTRA"
                    if future_version:
                        diag = "ORACLE_STALE"
            else:
                cr = _clean(r_text)
                co = _clean(o_text)
                score = Levenshtein.ratio(cr, co) if cr and co else (1.0 if not cr and not co else 0.0)
                if score >= 0.9999:
                    continue
                # Skip whitespace-only divergences
                if re.sub(r'\s+', ' ', r_text).strip() == re.sub(r'\s+', ' ', o_text).strip():
                    continue
                diag = _diagnose(
                    r_text,
                    o_text,
                    blame_op,
                    oracle_selector_mode=selector_mode,
                )
                if future_version and diag in {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN"}:
                    diag = "ORACLE_STALE"
                # Repeal-placeholder vs repeal-notice: the compared IR node (or
                # any of its sub-nodes — subsection/momentti, item/kohta) is a
                # repeal placeholder (lawvm_repeal_placeholder=1) and the oracle
                # carries "on kumottu"/"har upphävts" editorial text.  Same legal
                # state, different editorial rendering → EDITORIAL_CONVENTION.
                #
                # The check works at ALL addressable levels: a section may be
                # fully repealed (placeholder at the section level itself) OR it
                # may be live but contain one or more repealed sub-nodes.  Only
                # classify as EDITORIAL_CONVENTION when the IR tree actually
                # contains a repeal-placeholder node — otherwise "kumottu" in the
                # oracle text refers to substantive law (e.g. a provision that
                # references another repealed statute) and must not be suppressed.
                #
                # (PRO_RESPONSE_5_1 §8: repeal_notice subkind;
                #  PRO_RESPONSE4_2 Q2: node-level granularity)
                if (
                    diag in {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN"}
                    and r_node is not None
                    and _ir_node_has_repeal_placeholder(r_node)
                    and (
                        "kumottu" in (o_text or "").lower()
                        or "upphävts" in (o_text or "").lower()
                    )
                ):
                    diag = "EDITORIAL_CONVENTION"
                if (
                    mode == "legal_pit"
                    and diag in {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN"}
                    and oracle_has_future_repeal_overlay(o_text)
                ):
                    diag = "ORACLE_STALE"
                if (
                    diag in {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "EXTRA", "MISSING"}
                    and oracle_cutoff_date is not None
                    and replay_section_matches_text_at_cutoff(
                        master,
                        key,
                        o_text,
                        oracle_cutoff_date.isoformat(),
                        statute_id=sid,
                        title=master.title,
                        label_norm=fi_label_norm,
                    )
                ):
                    diag = "ORACLE_STALE"
                if (
                    diag in {"REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN"}
                    and blame_title_indicates_temporary_amendment(
                        str(blame_op.get("source_title", ""))
                    )
                ):
                    diag = "ORACLE_STALE"
            section_results.append({
                "section": key,
                "diagnosis": diag,
                "replay_text": r_text,
                "oracle_text": o_text,
                "oracle_content_absent": oracle_content_absent,
                "blame_source": blame_op.get("source_statute", ""),
                "blame_title": blame_op.get("source_title", ""),
                "oracle_version_amendment_id": "",
            })

        # Pre-blame check: for REPLAY_EXTRA/UNKNOWN sections with a known blame
        # amendment, re-replay stopping before that amendment. If the pre-blame
        # section text ≈ oracle, the oracle is stale at that amendment → ORACLE_STALE.
        #
        # Also handle structural EXTRA (section exists in replay, absent in oracle):
        # if the blame op is an INSERT, oracle simply hasn't incorporated it yet.
        for sec in section_results:
            if sec["diagnosis"] == "EXTRA" and sec["blame_source"]:
                if _lookup_blame_op(blame_map, sec["section"]).get("action", "").lower() == "insert":
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version_amendment_id"] = oracle_version_amendment_id or ""

        if oracle_cutoff_date is not None:
            for sec in section_results:
                if sec["diagnosis"] not in (
                    "REPLAY_EXTRA",
                    "REPLAY_MISSING",
                    "UNKNOWN",
                    "EXTRA",
                    "MISSING",
                ):
                    continue
                if replay_section_matches_text_at_cutoff(
                    master,
                    str(sec["section"]),
                    str(sec.get("oracle_text") or ""),
                    oracle_cutoff_date.isoformat(),
                    statute_id=sid,
                    title=master.title,
                    label_norm=fi_label_norm,
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version_amendment_id"] = oracle_version_amendment_id or ""

        if oracle_version_amendment_id:
            for sec in section_results:
                if (
                    sec["diagnosis"] in ("REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "EXTRA", "MISSING")
                    and oracle_suspect
                    and sec.get("blame_source", "") == oracle_version_amendment_id
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version_amendment_id"] = oracle_version_amendment_id or ""
                elif (
                    sec["diagnosis"] in ("REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "EXTRA", "MISSING")
                    and blame_source_postdates_oracle_version(
                        sec.get("blame_source", ""),
                        oracle_version_amendment_id,
                    )
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version_amendment_id"] = oracle_version_amendment_id or ""

        missing_from_xml = cast(List[str], (html_topology or {}).get("missing_from_xml") or [])
        missing_from_xml_keys = {
            norm_section_label(str(label or ""))
            for label in missing_from_xml
            if str(label or "")
        }
        for sec in section_results:
            if sec["diagnosis"] not in ("REPLAY_EXTRA", "EXTRA"):
                continue
            section_label = str(sec.get("section") or "")
            short_label = section_label.rsplit("section:", 1)[-1] if "section:" in section_label else section_label
            if norm_section_label(short_label) in missing_from_xml_keys:
                sec["diagnosis"] = "ORACLE_STALE"

        # NOTE: Previously had a repeal-vs-oracle heuristic here that reclassified
        # short "§N on kumottu" replay vs long oracle as ORACLE_STALE. REVERTED:
        # investigation showed these are repeal+re-enact cases where LawVM correctly
        # applied the repeal but missed a later amendment that re-inserted the section
        # with new content. The oracle has the re-enacted content. These are genuine
        # REPLAY_MISSING (missed re-insert op), not oracle staleness.

        # In legal_pit mode, detect CORRIGENDUM_APPLIED: divergences blamed on an
        # amendment that had a corrigendum patch applied.  These are legally correct
        # (LawVM > Finlex), not replay bugs.  Detection: blame_source is in the
        # corrigendum patch table (any correction type).
        if mode == "legal_pit":
            try:
                from lawvm.finland.corrigendum import get_patch_table as _gpt
                _pt = _gpt()
                _patched_mids = set(_pt._patches.keys())  # YEAR/NUM keys
            except (NameError, TypeError, AttributeError):
                raise  # programming bugs — fail loud
            except Exception:
                _patched_mids = set()
            for sec in section_results:
                blame = sec.get("blame_source", "")
                if blame and blame in _patched_mids and sec["diagnosis"] in (
                    "REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "EXTRA",
                ):
                    sec["diagnosis"] = "CORRIGENDUM_APPLIED"

        candidates: Dict[str, List[Dict]] = defaultdict(list)
        for sec in section_results:
            if sec["diagnosis"] in ("REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN", "MISSING") and sec["blame_source"]:
                candidates[sec["blame_source"]].append(sec)

        # Batch pre-blame replays: sort blame sources in amendment order,
        # replay incrementally, cache IR snapshots at each stop point.
        # This turns O(B × A) into O(A) where B=blame sources, A=amendments.
        _pre_blame_cache: Dict[str, tuple] = {}
        if candidates:
            _pre_blame_cache = _batch_pre_blame_sections(sid, list(candidates.keys()), mode)

        for blame_source, secs in candidates.items():
            pre_secs, oracle_version = _pre_blame_cache.get(blame_source, ({}, None))
            for sec in secs:
                oracle_el = oracle_secs.get(sec["section"])
                if oracle_el is None:
                    continue
                scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                    dict(pre_secs),
                    {sec["section"]: oracle_el},
                )
                pre_el = scoped_pre_secs.get(sec["section"])
                if pre_el is None:
                    continue
                pre_text = irnode_to_text(pre_el) if hasattr(pre_el, "kind") else _el_text(pre_el)
                replay_text = sec["replay_text"]
                oracle_text = _el_text(oracle_el)
                pre_r = _clean(pre_text)
                post_r = _clean(replay_text)
                ora_r = _clean(oracle_text)
                blame_action = _lookup_blame_op(blame_map, sec["section"]).get("action", "").lower()
                if (
                    sec["diagnosis"] == "REPLAY_MISSING"
                    and blame_action == "repeal"
                    and is_probable_repeal_stale_oracle(replay_text, oracle_text, pre_text)
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version"] = oracle_version or ""
                    continue
                if (
                    sec["diagnosis"] == "REPLAY_MISSING"
                    and blame_action == "repeal"
                    and "kumottu" in replay_text.lower()
                    and oracle_section_duplicates_adjacent_section(
                        sec["section"],
                        oracle_text,
                        oracle_secs_text,
                    )
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version"] = oracle_version or ""
                    continue
                if (
                    blame_action == "repeal"
                    and sec["diagnosis"] in ("REPLAY_EXTRA", "REPLAY_MISSING", "UNKNOWN")
                    and pre_r
                    and post_r
                    and ora_r
                ):
                    pre_ratio = Levenshtein.ratio(pre_r, ora_r)
                    post_ratio = Levenshtein.ratio(post_r, ora_r)
                    if post_ratio >= pre_ratio + PRE_BLAME_IMPROVEMENT_EPS:
                        sec["diagnosis"] = "ORACLE_STALE"
                        sec["oracle_version"] = oracle_version or ""
                        continue
                if (
                    sec["diagnosis"] in ("MISSING", "REPLAY_MISSING")
                    and blame_action == "repeal"
                    and oracle_has_repeal_banner_with_prior_wording(oracle_text)
                    and pre_r
                    and ora_r
                    and Levenshtein.ratio(pre_r, ora_r) >= PRE_BLAME_THRESHOLD
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version"] = oracle_version or ""
                    continue
                if pre_r and ora_r and Levenshtein.ratio(pre_r, ora_r) >= PRE_BLAME_THRESHOLD:
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version"] = oracle_version or ""
                    continue
                if (
                    sec["diagnosis"] == "REPLAY_MISSING"
                    and blame_action in {"replace", "insert"}
                    and pre_r
                    and not any(
                        str(sec["section"]) == failed_key
                        or str(sec["section"]).endswith("/" + failed_key)
                        for failed_key in failed_section_keys
                    )
                    and oracle_text_reduces_to_replay_by_dropping_sentences(
                        replay_text,
                        oracle_text,
                    )
                ):
                    sec["diagnosis"] = "ORACLE_STALE"
                    sec["oracle_version"] = oracle_version or ""

        empty_body_sources = {
            str(row.get("source_statute") or "")
            for row in source_pathologies
            if str(row.get("code") or "") == "EMPTY_OPERATIVE_BODY"
            and str(row.get("source_statute") or "")
        }
        raw_master_gap_sources = {
            str(sec.get("blame_source") or "")
            for sec in section_results
            if sec["diagnosis"] in ("MISSING", "REPLAY_MISSING", "UNKNOWN", "EXTRA")
            and str(sec.get("blame_source") or "")
        }
        raw_master_gap_pre_blame_cache: Dict[str, tuple] = {}
        if raw_master_gap_sources:
            raw_master_gap_pre_blame_cache = _batch_pre_blame_sections(
                sid,
                list(raw_master_gap_sources),
                mode,
            )
        empty_body_origin_cache: Dict[Tuple[str, str], bool] = {}
        raw_master_missing_cache: Dict[str, bool] = {}
        pre_blame_absent_cache: Dict[Tuple[str, str], bool] = {}
        for sec in section_results:
            if sec["diagnosis"] not in (
                "REPLAY_EXTRA",
                "REPLAY_MISSING",
                "UNKNOWN",
                "EXTRA",
                "MISSING",
                "EDITORIAL_CONVENTION",
            ):
                continue
            oracle_el = oracle_secs.get(sec["section"])
            if _section_points_to_empty_body_amendment(
                oracle_el,
                str(sec.get("oracle_text") or ""),
                empty_body_sources,
            ):
                sec["diagnosis"] = "SOURCE_INCOMPLETE"
                continue
            blame_source = str(sec.get("blame_source") or "")
            cache_key = (blame_source, str(sec["section"]))
            if cache_key not in empty_body_origin_cache:
                empty_body_origin_cache[cache_key] = _blame_section_points_to_empty_body_origin(
                    blame_source,
                    str(sec["section"]),
                    empty_body_sources,
                )
            if empty_body_origin_cache[cache_key]:
                sec["diagnosis"] = "SOURCE_INCOMPLETE"
                continue

            if sec["diagnosis"] not in ("MISSING", "REPLAY_MISSING", "UNKNOWN", "EXTRA"):
                continue
            if not blame_source:
                continue
            if str(sec["section"]) not in raw_master_missing_cache:
                raw_master_missing_cache[str(sec["section"])] = _raw_master_source_lacks_section(
                    sid,
                    str(sec["section"]),
                )
            if not raw_master_missing_cache[str(sec["section"])]:
                continue
            if cache_key not in pre_blame_absent_cache:
                pre_secs, _oracle_version = raw_master_gap_pre_blame_cache.get(blame_source, ({}, None))
                oracle_el = oracle_secs.get(sec["section"])
                scoped_pre_secs = dict(pre_secs)
                if oracle_el is not None:
                    scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                        dict(pre_secs),
                        {str(sec["section"]): oracle_el},
                    )
                pre_blame_section = scoped_pre_secs.get(sec["section"])
                if pre_blame_section is None and "section:" in str(sec["section"]):
                    section_label = str(sec["section"]).rsplit("section:", 1)[1].split("/", 1)[0]
                    pre_blame_section = scoped_pre_secs.get(f"section:{section_label}")
                pre_blame_absent_cache[cache_key] = pre_blame_section is None
            if pre_blame_absent_cache[cache_key]:
                sec["diagnosis"] = "SOURCE_INCOMPLETE"

        for sec in section_results:
            if sec["diagnosis"] != "UNKNOWN":
                continue
            diagnosis = _source_pathology_diagnosis_for_blame(
                master,
                _lookup_blame_op(blame_map, str(sec["section"])),
            )
            if diagnosis is not None:
                sec["diagnosis"] = diagnosis

        # Attachment / liite comparison — just count; show indicator if mismatch
        r_att_count, r_att_titles = _extract_attachment_info_ir(master.materialized_state.ir)
        o_att_count, o_att_titles = _extract_attachment_info(oracle_root)
        if r_att_count != o_att_count:
            r_desc = f"Liitteitä {r_att_count} kpl" + (": " + "; ".join(r_att_titles) if r_att_titles else "")
            o_desc = f"Liitteitä {o_att_count} kpl" + (": " + "; ".join(o_att_titles) if o_att_titles else "")
            section_results.append({
                "section": "liitteet",
                "diagnosis": "LIITE_DIFF",
                "replay_text": r_desc,
                "oracle_text": o_desc,
                "blame_source": "",
                "blame_title": "",
                "oracle_version": "",
            })

        return ClassifyResult(
            sid=sid,
            title=master.title or "",
            mode=mode,
            overall_score=overall_score,
            section_score=section_score,
            section_results=section_results,
            source_pathologies=source_pathologies,
            html_topology=html_topology,
            contingent_effective_sources=contingent_effective_sources,
            replay_result=master,
            compiled_ops=compiled_ops,
            oracle_version_amendment_id=oracle_version_amendment_id or "",
            oracle_sections=oracle_secs,
        )
    except Exception as e:
        return ClassifyResult(sid=sid, error=str(e)[:100])


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------

def _print_statute_summary(result: ClassifyResult) -> None:
    sid = result.sid
    if result.error:
        print(f"  {sid}: ERROR — {result.error}")
        return
    score = result.overall_score
    sections = result.section_results
    if not sections:
        print(f"  {sid}: {score:.1%}  (no divergences)")
        return
    counts: Dict[str, int] = defaultdict(int)
    for sec in sections:
        counts[sec["diagnosis"]] += 1
    parts = "  ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    print(f"  {sid}: {score:.1%}  ({len(sections)} diverging)  {parts}")
    source_pathologies = [
        p for p in result.source_pathologies
        if isinstance(p, dict) and str(p.get("code") or "")
    ]
    if source_pathologies:
        codes = sorted({str(p.get("code") or "") for p in source_pathologies if str(p.get("code") or "")})
        print(f"    source-pathology: {', '.join(codes)}")
    html_topology = result.html_topology or {}
    html_missing = [str(v) for v in html_topology.get("missing_from_xml", []) if str(v)]
    html_extra = [str(v) for v in html_topology.get("extra_in_xml", []) if str(v)]
    html_noncomm = str(html_topology.get("noncommensurable_reason") or "")
    if html_noncomm:
        print(f"    html-topology: noncommensurable={html_noncomm}")
    elif html_missing or html_extra:
        detail = []
        if html_missing:
            detail.append(f"missing_from_xml={','.join(html_missing)}")
        if html_extra:
            detail.append(f"extra_in_xml={','.join(html_extra)}")
        print(f"    html-topology: {'  '.join(detail)}")
    contingent_sources = [str(v) for v in result.contingent_effective_sources if str(v)]
    if contingent_sources:
        print(f"    contingent-effective-date: {', '.join(contingent_sources)}")


def _print_corpus_summary(results: List[ClassifyResult], save_path: Optional[str]) -> None:
    # Aggregate
    total_statutes = len(results)
    errors = [r for r in results if r.error]
    ok_results = [r for r in results if not r.error]

    all_section_diags: Dict[str, List[str]] = defaultdict(list)  # diag → [sid]
    scores = []
    adjusted_scores = []
    source_pathology_statutes = 0
    html_topology_statutes = 0
    html_noncommensurable_statutes = 0
    contingent_effective_statutes = 0

    for r in ok_results:
        scores.append(r.overall_score)
        sections = r.section_results
        if any(isinstance(p, dict) and str(p.get("code") or "") for p in r.source_pathologies):
            source_pathology_statutes += 1
        html_topology = r.html_topology or {}
        if html_topology.get("noncommensurable_reason"):
            html_noncommensurable_statutes += 1
        elif html_topology.get("missing_from_xml") or html_topology.get("extra_in_xml"):
            html_topology_statutes += 1
        if any(str(v) for v in r.contingent_effective_sources):
            contingent_effective_statutes += 1

        # Count oracle-issue sections (excluded from adjusted score)
        oracle_issue_keys = {s["section"] for s in sections if s["diagnosis"] in ORACLE_CATEGORIES}
        replay_secs_count = len(sections)

        for sec in sections:
            all_section_diags[sec["diagnosis"]].append(r.sid)

        # Adjusted score: pretend oracle-issue sections are perfect
        # Simple approximation — reuse overall score as base
        # If all divergences are oracle issues → adjusted = 1.0
        # Otherwise → keep overall score for non-oracle divergences
        if replay_secs_count > 0:
            oracle_fraction = len(oracle_issue_keys) / replay_secs_count
            adj = r.overall_score + (1.0 - r.overall_score) * oracle_fraction
        else:
            adj = r.overall_score
        adjusted_scores.append(min(adj, 1.0))

    mean_score = sum(scores) / len(scores) if scores else 0.0
    mean_adj = sum(adjusted_scores) / len(adjusted_scores) if adjusted_scores else 0.0

    print(f"\nCorpus divergence classification ({total_statutes} statutes, "
          f"{len(errors)} errors):")
    print()
    for diag in [
        "ORACLE_STALE",
        "CORRIGENDUM_APPLIED",
        "EDITORIAL_CONVENTION",
        "SOURCE_PATHOLOGY",
        "REPLAY_EXTRA",
        "REPLAY_MISSING",
        "UNKNOWN",
        "MISSING",
        "EXTRA",
    ]:
        sids = all_section_diags.get(diag, [])
        if sids:
            unique_statutes = len(set(sids))
            print(f"  {diag:<22}  {len(sids):4} sections  {unique_statutes:4} statutes")

    print()
    print(f"  Replay mode         : {ok_results[0].mode if ok_results else 'unknown'}")
    print(f"  Mean score          : {mean_score:.2%}")
    excluded_diags = " + ".join(sorted(ORACLE_CATEGORIES))
    print(f"  Adjusted score      : {mean_adj:.2%}  (excl. {excluded_diags})")
    print(f"  Improvement         : {mean_adj - mean_score:+.2%}")
    print(f"  Source-pathology    : {source_pathology_statutes} statutes")
    print(f"  HTML topology       : {html_topology_statutes} statutes")
    print(f"  HTML noncommensurable: {html_noncommensurable_statutes} statutes")
    print(f"  Contingent eff-date : {contingent_effective_statutes} statutes")

    if save_path:
        with open(save_path, 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['statute_id', 'overall_score', 'section', 'diagnosis', 'blame_source'])
            for r in ok_results:
                for sec in r.section_results:
                    w.writerow([r.sid, f"{r.overall_score:.4f}",
                                sec["section"], sec["diagnosis"], sec["blame_source"]])
        print(f"\n  Saved to: {save_path}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _corpus_path(full: bool) -> str:
    here = Path(__file__).resolve()
    tmp = here.parent.parent.parent.parent / ".tmp"
    if full:
        return str(tmp / "migration" / "expanded_batch_test_list.csv")
    return str(tmp / "batch_test_list.csv")


def _corpus_selection_detail(full: bool) -> str:
    path = Path(_corpus_path(full=full))
    label = "expanded corpus list" if full else "configured corpus list"
    detail = f"{label} ({path.name})"

    other_path = Path(_corpus_path(full=not full))
    try:
        if path.exists() and other_path.exists() and path.read_bytes() == other_path.read_bytes():
            other_flag = "--corpus" if full else "--corpus-full"
            detail += f"; same rows as {other_flag} on this tree"
    except OSError:
        pass

    return detail


def _write_db(results: List[ClassifyResult], db_path: str) -> None:
    con = sqlite3.connect(db_path)
    con.executescript('''
        DROP TABLE IF EXISTS corpus_stats;
        CREATE TABLE corpus_stats (
            total_examined  INTEGER,
            total_matching  INTEGER,
            total_diverging INTEGER,
            total_errors    INTEGER
        );
        DROP TABLE IF EXISTS divergences;
        CREATE TABLE divergences (
            statute_id      TEXT,
            title           TEXT,
            overall_score   REAL,
            section_score   REAL,
            section         TEXT,
            diagnosis       TEXT,
            blame_source    TEXT,
            blame_title     TEXT,
            oracle_version  TEXT,
            replay_text     TEXT,
            oracle_text     TEXT
        );
        CREATE INDEX idx_div_statute ON divergences(statute_id);
        CREATE INDEX idx_div_diag    ON divergences(diagnosis);
        CREATE INDEX idx_div_blame   ON divergences(blame_source);
        DROP TABLE IF EXISTS statute_signals;
        CREATE TABLE statute_signals (
            statute_id                    TEXT PRIMARY KEY,
            source_pathology              INTEGER,
            source_pathology_codes        TEXT,
            source_pathology_rows_json    TEXT,
            html_topology_mismatch        INTEGER,
            html_missing_from_xml         TEXT,
            html_extra_in_xml             TEXT,
            html_noncommensurable_reason  TEXT,
            contingent_effective_sources  TEXT
        );
    ''')
    ok = [r for r in results if not r.error]
    errors = [r for r in results if r.error]
    diverging = [r for r in ok if r.section_results]
    matching = len(ok) - len(diverging)

    con.execute("INSERT INTO corpus_stats VALUES (?,?,?,?)",
                (len(results), matching, len(diverging), len(errors)))

    rows = []
    signal_rows = []
    for r in ok:
        for sec in r.section_results:
            rows.append((
                r.sid, r.title, r.overall_score,
                r.section_score,
                sec["section"], sec["diagnosis"],
                sec["blame_source"], sec["blame_title"],
                sec.get("oracle_version", ""),
                sec["replay_text"], sec["oracle_text"],
            ))
        source_pathologies = [
            p for p in r.source_pathologies
            if isinstance(p, dict)
        ]
        source_pathology_codes = sorted(
            {
                str(p.get("code") or "")
                for p in source_pathologies
                if str(p.get("code") or "")
            }
        )
        source_pathology_rows_json = (
            json.dumps(source_pathologies, ensure_ascii=True, sort_keys=True)
            if source_pathologies
            else ""
        )
        html_topology = r.html_topology or {}
        html_missing = [
            str(v)
            for v in html_topology.get("missing_from_xml", [])
            if str(v)
        ]
        html_extra = [
            str(v)
            for v in html_topology.get("extra_in_xml", [])
            if str(v)
        ]
        html_noncomm = str(html_topology.get("noncommensurable_reason") or "")
        contingent_sources = [
            str(v)
            for v in r.contingent_effective_sources
            if str(v)
        ]
        signal_rows.append((
            r.sid,
            1 if source_pathology_codes else 0,
            "|".join(source_pathology_codes),
            source_pathology_rows_json,
            1 if (bool(html_topology.get("mismatch")) or bool(html_missing or html_extra)) else 0,
            "|".join(html_missing),
            "|".join(html_extra),
            html_noncomm,
            "|".join(contingent_sources),
        ))
    con.executemany("INSERT INTO divergences VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    con.executemany("INSERT INTO statute_signals VALUES (?,?,?,?,?,?,?,?,?)", signal_rows)
    con.commit()
    con.close()
    size_mb = Path(db_path).stat().st_size / 1e6
    print(f"\n  Saved DB: {db_path} ({size_mb:.1f} MB, {len(rows)} diverging sections)")


def _sort_sids_by_chain_length(sids: List[str]) -> List[str]:
    """Sort statute IDs longest-amendment-chain-first.

    Replay time scales with amendment count, so submitting the longest chains
    first to ProcessPoolExecutor prevents a few large statutes from stalling
    completion while workers sit idle (long-tail parallelism effect).
    Uses the cached _amendment_children_by_parent() index — effectively free.
    """
    from lawvm.finland.grafter import _amendment_children_by_parent
    children = _amendment_children_by_parent()
    return sorted(sids, key=lambda s: len(children.get(s, ())), reverse=True)


def _classify_statute_sync(sid: str, mode: Literal["finlex_oracle", "legal_pit"]) -> Optional[ClassifyResult]:
    """Sync wrapper so ProcessPoolExecutor can run each statute in its own process."""
    return _classify_statute(sid, mode)


def _run_corpus(sids: List[str], mode: Literal["finlex_oracle", "legal_pit"], parallel: int) -> List[ClassifyResult]:
    total = len(sids)
    done = 0
    results: List[ClassifyResult] = []

    with managed_executor(parallel) as executor:
        try:
            futs = {executor.submit(_classify_statute_sync, sid, mode): sid for sid in sids}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    result = fut.result()
                except Exception as e:
                    sid = futs[fut]
                    result = ClassifyResult(sid=sid, error=str(e)[:100])
                if result:
                    results.append(result)
                done += 1
                if done % 50 == 0:
                    print(f"  [{done}/{total}]...", flush=True)
        except KeyboardInterrupt:
            print("\nInterrupted — cancelling workers...", flush=True)
            raise

    return results


def main(args) -> None:
    corpus = getattr(args, "corpus", False)
    corpus_full = getattr(args, "corpus_full", False)
    save = getattr(args, "save", False)
    db_path = getattr(args, "db", None)
    mode = getattr(args, "mode", "finlex_oracle")
    import os as _os
    _par = getattr(args, "parallel", None)
    parallel = _par if _par is not None else max(8, _os.cpu_count() or 4)
    sid = getattr(args, "statute_id", None)

    if corpus or corpus_full:
        path = _corpus_path(full=corpus_full)
        if not Path(path).exists():
            print(f"ERROR: corpus not found: {path}", file=sys.stderr)
            sys.exit(1)
        with open(path, newline='') as f:
            rows = list(csv.reader(f))
        sids = [row[1].strip() for row in rows if len(row) >= 2]

        sids = _sort_sids_by_chain_length(sids)
        label = _corpus_selection_detail(corpus_full)
        print(f"oracle-check: {len(sids)} statutes ({label}, parallel={parallel}, longest-chain-first)")
        results = [r for r in _run_corpus(sids, mode, parallel) if r]

        for r in results:
            _print_statute_summary(r)

        save_path = "oracle_check_results.csv" if save else None
        _print_corpus_summary(results, save_path)

        if db_path:
            _write_db(results, db_path)

    elif sid:
        result = _classify_statute(sid, mode)
        if result:
            print(f"  Replay mode         : {mode}")
            _print_statute_summary(result)
            sections = result.section_results
            if sections:
                counts: Dict[str, int] = defaultdict(int)
                for sec in sections:
                    counts[sec["diagnosis"]] += 1
                print()
                for diag, n in sorted(counts.items()):
                    print(f"  {diag:<22}  {n} section(s)")
    else:
        print("ERROR: provide <statute_id> or --corpus[--full]", file=sys.stderr)
        sys.exit(1)
