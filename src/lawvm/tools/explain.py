"""lawvm explain — divergence explainer per provision.

Combines blame + diff + amendment text lookup into one diagnostic output.
For each diverging provision between replay and oracle, shows:
  - similarity score and length delta
  - last amendment to touch the provision
  - the johtolause text from that amendment
  - a specific divergence snippet
  - auto-diagnosis (ORACLE_STALE / REPLAY_EXTRA / REPLAY_MISSING /
                    SOURCE_PATHOLOGY / EDITORIAL_CONVENTION / UNKNOWN)

Usage:
    lawvm explain <statute_id>                       # all diverging sections
    lawvm explain <statute_id> --section "63 §"      # single section
    lawvm explain <statute_id> --threshold 0.95      # only below 95%
"""
from __future__ import annotations

import contextlib
import difflib
import io
import re
import sys
from typing import Any, Dict, Literal, Optional, Tuple

import Levenshtein
from lxml import etree

from lawvm.core.ir import IRNode
from lawvm.core.ir_helpers import irnode_to_text
from lawvm.core.compile_facade import (
    CompileFacade,
)
from lawvm.core.compile_result import (
    StrictProfile,
    strict_fail_reasons_from_findings_and_verdict,
)
from lawvm.core.compile_views import (
    quirks_used_from_findings,
    source_completeness_issues_from_findings,
    source_pathology_rows_from_findings,
)
from lawvm.tools.divergence_heuristics import blame_title_indicates_temporary_amendment
from lawvm.tools.divergence_heuristics import blame_source_postdates_oracle_version
from lawvm.tools.divergence_heuristics import is_probable_repeal_stale_oracle
from lawvm.tools.divergence_heuristics import oracle_has_repeal_banner_with_prior_wording
from lawvm.tools.divergence_heuristics import oracle_section_duplicates_adjacent_section
from lawvm.tools.divergence_heuristics import oracle_text_has_removable_duplicate_sentence
from lawvm.tools.divergence_heuristics import oracle_text_reduces_to_replay_by_dropping_sentences
from lawvm.tools.divergence_heuristics import oracle_text_reduces_to_bare_section_stub
from lawvm.tools.divergence_heuristics import replay_section_matches_text_at_cutoff
from lawvm.tools.divergence_heuristics import replay_section_has_future_effective_version
from lawvm.tools._compile_report_record import report_record_from_facade
from lawvm.finland.consolidated_artifacts import ConsolidatedArtifactSelector
from lawvm.finland.replay_products import fi_label_norm
from lawvm.tools.editorial_hygiene import (
    strip_editorial_annotations,
    strip_kumottu_attribution,
    strip_temporary_residue_annotations,
)
from lawvm.tools.section_keys import (
    extract_ir_sections,
    extract_oracle_sections,
    norm_section_label,
    normalize_address_filter,
    reconcile_unique_unscoped_aliases,
    section_key_from_compile_failure,
    section_key_from_compiled_scope_row,
    section_key_from_target_dict,
    section_key_sort_key,
)
from lawvm.finland.strict_profile import FINLAND_INGESTION_V1
from lawvm.finland.helpers import _fi_label_postprocessor
from lawvm.finland.statute import ReplayState, StatuteContext

PRE_BLAME_IMPROVEMENT_EPS = 0.01
_LATEST_CONSOLIDATED_SELECTOR = ConsolidatedArtifactSelector.latest_cached_editorial()


def _oracle_suspect_value(master: object) -> str:
    if not hasattr(master, "source_adjudication"):
        return ""
    source_adjudication = master.source_adjudication
    if source_adjudication is None or not hasattr(source_adjudication, "oracle_suspect"):
        return ""
    return str(source_adjudication.oracle_suspect or "")


def _source_pathology_diagnosis_for_blame(
    master: object,
    blame_op: dict[str, Any] | None,
) -> tuple[str, str] | None:
    """Return a narrow source-pathology diagnosis for one blamed op.

    Explain should only demote an UNKNOWN row when the same amendment/target is
    already owned by explicit replay findings. This keeps the classification
    tied to pre-existing emitted evidence rather than inventing a new heuristic.
    """
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

    matched_codes: set[str] = set()
    exact_target_label = ""
    if target_section and target_paragraph and target_item:
        exact_target_label = f"{target_section} § {target_paragraph} mom {target_item} kohta"
    elif target_section and target_paragraph:
        exact_target_label = f"{target_section} § {target_paragraph} mom"

    for row in source_pathology_rows() or ():
        if str(row.get("source_statute") or "") != blame_source:
            continue
        code = str(row.get("code") or "").strip()
        if not code:
            continue
        detail = row.get("detail") if isinstance(row, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
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
    if not matched_codes:
        return None
    if not has_degraded_coverage and not has_failed_no_deterministic:
        return None

    explanation = (
        f"blamed amendment {blame_source} already carries "
        f"{', '.join(sorted(matched_codes))}"
    )
    extra_parts: list[str] = []
    if has_degraded_coverage:
        extra_parts.append("degraded uncovered-body coverage")
    if has_failed_no_deterministic:
        extra_parts.append("no-deterministic-path failed-op ownership")
    if extra_parts:
        explanation += " + " + " + ".join(extra_parts)
    return ("SOURCE_PATHOLOGY", explanation)


def _oracle_version_amendment_id_to_version_tag(oracle_version_amendment_id: str) -> str:
    """Map a YYYY/NNN amendment id to the 8-digit consolidated version tag."""
    match = re.fullmatch(r"(?P<year>\d{4})/(?P<num>\d{1,4})", oracle_version_amendment_id.strip())
    if match is None:
        raise ValueError(f"invalid oracle version amendment id: {oracle_version_amendment_id!r}")
    return f"{match.group('year')}{int(match.group('num')):04d}"


def _oracle_selector_from_args(
    oracle_selector_mode: str,
    oracle_version_amendment_id: str = "",
) -> ConsolidatedArtifactSelector:
    """Build the consolidated-oracle selector requested on the CLI."""
    if oracle_version_amendment_id:
        return ConsolidatedArtifactSelector.exact_embedded_version(
            _oracle_version_amendment_id_to_version_tag(oracle_version_amendment_id),
        )
    if oracle_selector_mode == "bench_comparable":
        return ConsolidatedArtifactSelector.bench_comparable()
    return ConsolidatedArtifactSelector.latest_cached_editorial()


def _irnode_text(node) -> str:
    """Extract text from IRNode or lxml element."""
    if isinstance(node, IRNode):
        return irnode_to_text(node)
    return etree.tostring(node, method="text", encoding="unicode").strip()


# ---------------------------------------------------------------------------
# Re-use helpers from diff and blame (duplicated to keep tools independent)
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
    # Also strip periods (pre-1980s nums like "1 §.")
    return norm_section_label(s)


def _el_text(el: etree._Element) -> str:
    return etree.tostring(el, method="text", encoding="unicode").strip()


def _clean(text: str) -> str:
    return re.sub(r'[^a-z0-9äöå]', '', text.lower())


def _section_sort_key(key: str):
    return section_key_sort_key(key)


def _extract_sections(root: etree._Element) -> Dict[str, etree._Element]:
    return extract_oracle_sections(root)


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
        # Compiled ops use flat keys (target_norm, target_unit_kind, etc.)
        key = section_key_from_compiled_scope_row(op)
        if not key:
            # Fallback: legacy nested-dict format (kept for forward compat)
            key = section_key_from_target_dict(op.get("target", {}))
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


def _get_pre_blame_sections(
    sid: str,
    blame_source: str,
    mode: Literal["finlex_oracle", "legal_pit"],
    oracle_selector: ConsolidatedArtifactSelector | None = None,
) -> Dict[str, Any]:
    from lawvm.finland.grafter import (
        _resolve_applicable_amendment_records,
        get_corpus,
        process_muutoslaki,
    )

    cs = get_corpus()
    xml_bytes = cs.read_source(sid)
    if xml_bytes is None:
        return {}

    ctx = StatuteContext.from_xml(xml_bytes, _fi_label_postprocessor)
    state = ReplayState(ir=ctx.base_ir)
    amendment_records, _, _ = _resolve_applicable_amendment_records(
        sid,
        mode,
        selector=oracle_selector,
    )

    for rec in amendment_records:
        amendment_id = str(rec["statute_id"])
        if amendment_id == blame_source:
            break
        with contextlib.redirect_stdout(io.StringIO()):
            state = process_muutoslaki(
                amendment_id,
                state,
                ctx,
                replay_mode=mode,
                parent_id=sid,
            ).output

    return extract_ir_sections(state.ir)


def _section_filter_matches(key: str, section_filter: str) -> bool:
    if ":" in section_filter:
        return key == normalize_address_filter(section_filter)
    target_key = _norm_num(section_filter)
    return key == target_key or key.endswith(f"/section:{target_key}") or key == f"section:{target_key}"


# ---------------------------------------------------------------------------
# Johtolause loader
# ---------------------------------------------------------------------------

def _load_johtolause(source_amendment_id: str) -> str:
    """Load and normalize johtolause for an amendment ID."""
    from lawvm.finland.grafter import get_corpus, get_johtolause, _normalize_johtolause_verbs

    try:
        cs = get_corpus()
        xml_bytes = cs.read_source(source_amendment_id)
        if xml_bytes is None:
            return ""
        johto = get_johtolause(xml_bytes)
        if johto:
            return _normalize_johtolause_verbs(johto)
    except (NameError, TypeError, AttributeError):
        raise  # programming bugs — fail loud
    except Exception:
        pass
    return ""


# ---------------------------------------------------------------------------
# Divergence snippet via difflib
# ---------------------------------------------------------------------------

def _find_divergence_snippet(r_text: str, o_text: str, ctx: int = 15,
                              max_len: int = 90) -> str:
    """Find the first significant difference between two texts.

    Returns a human-readable string showing the diverging fragment in both.
    """
    s = difflib.SequenceMatcher(None, r_text, o_text, autojunk=False)
    for op, i1, i2, j1, j2 in s.get_opcodes():
        if op == 'equal':
            continue
        r_lo = max(0, i1 - ctx)
        o_lo = max(0, j1 - ctx)
        r_snip = r_text[r_lo: i2 + ctx].replace('\n', ' ')[:max_len]
        o_snip = o_text[o_lo: j2 + ctx].replace('\n', ' ')[:max_len]
        return f'replay: "{r_snip}" / oracle: "{o_snip}"'
    return ""


def _diagnose(
    r_text: str,
    o_text: str,
    blame_op: Optional[dict],
    *,
    oracle_selector_mode: str = "latest_cached_editorial",
) -> Tuple[str, str]:
    """Return (diagnosis_code, explanation) for a diverging provision."""

    o_without_temporary = strip_temporary_residue_annotations(o_text)
    if o_without_temporary != o_text:
        if (
            oracle_selector_mode == "bench_comparable"
            and oracle_text_reduces_to_bare_section_stub(o_text)
        ):
            return (
                "EDITORIAL_CONVENTION",
                "bench-comparable oracle carries only temporary-law editorial residue",
            )
        if Levenshtein.ratio(_clean(r_text), _clean(o_without_temporary)) >= 0.95:
            return (
                "ORACLE_STALE",
                "oracle retains expired temporary-residue annotations beyond the live replay state",
            )

    # EDITORIAL_CONVENTION: differences vanish when stripping editorial markers
    r_stripped = strip_editorial_annotations(r_text)
    o_stripped = strip_editorial_annotations(o_text)
    r_c = _clean(r_stripped)
    o_c = _clean(o_stripped)
    if Levenshtein.ratio(r_c, o_c) >= 0.999:
        return ("EDITORIAL_CONVENTION",
                "divergence is repeal placeholders or date annotations — oracle editorial choice")

    if 'kumottu' in r_text and 'kumottu' in o_text:
        r_k = strip_kumottu_attribution(r_text)
        o_k = strip_kumottu_attribution(o_text)
        if Levenshtein.ratio(_clean(r_k), _clean(o_k)) >= 0.95:
            return ("EDITORIAL_CONVENTION",
                    "divergence is repeal attribution or aiempi-sanamuoto residue — oracle editorial choice")

    if r_c and o_c and Levenshtein.ratio(r_c, o_c) >= 0.95:
        return ("EDITORIAL_CONVENTION",
                "divergence is inline editorial residue — oracle editorial choice")

    if oracle_text_has_removable_duplicate_sentence(r_text, o_text):
        return (
            "ORACLE_STALE",
            "oracle duplicates one same-section sentence fragment beyond the replay/source-backed text",
        )

    # Use cleaned lengths for the sign check: oracle XML whitespace can add
    # hundreds of chars via etree.tostring, making oracle appear longer than
    # replay even when replay has MORE content.  _clean strips all whitespace
    # and punctuation so only alphanumeric content is compared.
    clean_len_diff = len(_clean(r_text)) - len(_clean(o_text))
    src = blame_op.get("source_statute", "?") if blame_op else None
    action = blame_op.get("action", "") if blame_op else ""

    # Keep the coarse replay-vs-oracle size heuristic aligned with
    # oracle_check._diagnose(): moderate extra/missing content should not fall
    # through to UNKNOWN in explain when the other diagnostic surface already
    # classifies it as REPLAY_EXTRA/REPLAY_MISSING.
    if clean_len_diff > 40:
        if blame_op and action in ("REPLACE", "INSERT"):
            return ("ORACLE_STALE",
                    f"replay incorporates {src}, oracle may not have this amendment")
        return ("REPLAY_EXTRA",
                "replay has significantly more content — possible double-insert or uncleaned placeholder")

    if clean_len_diff < -40:
        return ("REPLAY_MISSING",
                "replay has significantly less content — possible missed operation")

    return ("UNKNOWN",
            "similar length but different content — needs manual investigation")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _print_facade_summary(
    facade: CompileFacade,
    *,
    html_noncommensurable_reason: str = "",
) -> None:
    """Print a short CompileFacade summary block to stdout."""
    quirks = tuple(quirks_used_from_findings(facade.finding_ledger))
    source_completeness = tuple(source_completeness_issues_from_findings(facade.finding_ledger))
    fail_reasons = tuple(
        strict_fail_reasons_from_findings_and_verdict(
            facade.finding_ledger,
            verdict=facade.verdict,
        )
    )
    source_pathologies = tuple(source_pathology_rows_from_findings(facade.finding_ledger))
    pathology_codes = tuple(
        sorted({
            str(row.get("code") or "")
            for row in source_pathologies
            if str(row.get("code") or "")
        })
    )
    pathology_reasons = tuple(
        sorted({
            str((row.get("detail") or {}).get("diagnostic_reason") or "")
            for row in source_pathologies
            if isinstance(row.get("detail"), dict)
            and str((row.get("detail") or {}).get("diagnostic_reason") or "")
        })
    )
    pass_label = "YES" if not facade.has_blocking else "NO"
    findings = len(getattr(facade, "finding_ledger", ()))
    bundle = getattr(facade, "bundle", None)
    temporal_events = len(getattr(bundle, "temporal_events", ()))
    quirks_used = len(quirks)
    source_completeness_issues = len(source_completeness)
    print(
        f"CompileFacade : strict={pass_label}"
        f"  findings={findings}"
        f"  temporal_events={temporal_events}"
        f"  quirks_used={quirks_used}"
        f"  source_completeness_issues={source_completeness_issues}"
    )
    if quirks_used:
        print(f"  Quirks       : {', '.join(sorted({str(item.kind) for item in quirks}))}")
    if source_completeness_issues:
        print(f"  SC issues    : {', '.join(sorted({str(item.kind) for item in source_completeness}))}")
    if fail_reasons:
        print(f"  Fail reasons : {', '.join(fail_reasons)}")
    if pathology_codes:
        print(f"  Pathologies  : {', '.join(pathology_codes)}")
    if pathology_reasons:
        print(f"  Pathology reasons : {', '.join(pathology_reasons)}")
    html_noncomm_reason = str(html_noncommensurable_reason or "").strip()
    if html_noncomm_reason:
        print(f"  HTML/XML reason : {html_noncomm_reason}")
    print()


def _projection_row_detail_suffix(detail: dict[str, Any]) -> str:
    """Render compact high-signal projection-row detail for human summaries."""
    parts: list[str] = []
    code = str(detail.get("code") or "").strip()
    if code:
        parts.append(f"code={code}")
    target_unit_kind = str(detail.get("target_unit_kind") or "").strip()
    target_kind = str(detail.get("target_kind") or "").strip()
    if not target_kind:
        if target_unit_kind == "section":
            target_kind = "P"
        elif target_unit_kind == "chapter":
            target_kind = "L"
        elif target_unit_kind == "part":
            target_kind = "O"
    target_norm = str(detail.get("target_norm") or "").strip()
    target_chapter = str(detail.get("target_chapter") or "").strip()
    if target_kind or target_norm or target_chapter:
        target_parts: list[str] = []
        if target_kind:
            target_parts.append(f"kind={target_kind}")
        if target_norm:
            target_parts.append(f"norm={target_norm}")
        if target_chapter:
            target_parts.append(f"chapter={target_chapter}")
        parts.append("target(" + ", ".join(target_parts) + ")")
    target_label = str(detail.get("target_label") or "").strip()
    if target_label:
        parts.append(f"target_label={target_label}")
    diagnostic_reason = str(detail.get("diagnostic_reason") or "").strip()
    if diagnostic_reason:
        parts.append(f"diagnostic_reason={diagnostic_reason}")
    tag = str(detail.get("tag") or "").strip()
    if tag:
        parts.append(f"tag={tag}")
    if not parts:
        return ""
    return "  [" + "; ".join(parts) + "]"


def _print_compile_summary(
    *,
    report_record: Any,
) -> None:
    """Print the short summary from the facade-backed report record."""
    def _field(name: str, default: Any = None) -> Any:
        if isinstance(report_record, dict):
            return report_record.get(name, default)
        return getattr(report_record, name, default)

    projection_value = _field("projection_rows", ())
    if callable(projection_value):
        projection_rows = list(projection_value() or ())
    else:
        projection_rows = list(projection_value or ())
    canonical_ops = list(_field("canonical_ops", ()) or ())
    failed_ops = list(_field("failed_ops", ()) or ())
    source_pathologies = list(_field("source_pathologies", ()) or ())
    n_canonical = len(canonical_ops)
    n_failed = len(failed_ops)
    n_projection_rows = len(projection_rows)
    strict_fail_reasons = list(_field("strict_fail_reasons", ()) or [])
    pass_label = "YES" if not bool(strict_fail_reasons) else "NO"
    print(
        f"Compile summary: strict={pass_label}  canonical={n_canonical}  "
        f"failed={n_failed}  projection_rows={n_projection_rows}"
    )
    fail_reasons = strict_fail_reasons
    if fail_reasons:
        print(f"  Fail reasons : {', '.join(fail_reasons)}")
    if projection_rows:
        kinds = sorted(
            {
                str((a.get("kind") if isinstance(a, dict) else getattr(a, "kind", "")) or "")
                for a in projection_rows
            }
        )
        print(f"  Projection rows: {', '.join(kinds)}")
        for row in projection_rows:
            detail = row.get("detail", {}) if isinstance(row, dict) else getattr(row, "detail", {})
            detail_suffix = _projection_row_detail_suffix(detail) if isinstance(detail, dict) else ""
            kind = row.get("kind", "") if isinstance(row, dict) else getattr(row, "kind", "")
            print(f"    - {kind}{detail_suffix}")
    if source_pathologies:
        codes = sorted(
            {str(p.get("code") or "") for p in source_pathologies if isinstance(p, dict) and str(p.get("code") or "")}
        )
        print(f"  Source pathologies: {', '.join(codes)}")
    print()


def _format_effect_intent(intent: Any) -> str:
    """Return a one-line human-readable description of an EffectIntent."""
    kind = getattr(intent, "kind", "?")
    raw = getattr(intent, "raw_text", "")[:80]
    if kind == "commencement":
        eff = getattr(intent, "effective_date", None)
        contingent = getattr(intent, "is_contingent", False)
        if contingent:
            return f"Commencement: contingent (decree-set)  [{raw}]"
        return f"Commencement: {eff}  [{raw}]"
    if kind == "expiry":
        exp = getattr(intent, "expiry_date", None)
        return f"Expiry: {exp}  [{raw}]"
    if kind == "suspension":
        until = getattr(intent, "suspended_until", None)
        return f"Suspension: until {until}  [{raw}]"
    if kind == "applicability":
        return f"Applicability: {raw}"
    if kind == "revival":
        rev = getattr(intent, "revived_from", None)
        return f"Revival: from {rev}  [{raw}]"
    return f"{kind}: {raw}"


def _print_temporal_debug_block(
    *,
    temporal_events: tuple[Any, ...],
) -> None:
    if not temporal_events:
        return
    print(f"TemporalEvents ({len(temporal_events)}):")
    for event in temporal_events:
        print(f"  {event}")
    print()


def _explain_sync(
    sid: str,
    section_filter: Optional[str],
    threshold: float,
    mode: Literal["finlex_oracle", "legal_pit"],
    oracle_selector: ConsolidatedArtifactSelector | None = None,
    show_compile_summary: bool = False,
    strict_profile: Optional["StrictProfile"] = None,
    show_facade: bool = False,
) -> None:
    compiled_ops: list = []
    replay_meta: dict[str, object] = {}
    failed_ops: list = []
    _dossier_canonical_ops: list = []
    needs_dossier = show_compile_summary or show_facade
    from lawvm.finland.grafter import (
        _oracle_version_label,
        get_consolidated_oracle_context,
        get_corpus,
        replay_xml,
    )

    master = replay_xml(
        sid,
        mode=mode,
        quiet=True,
        compiled_ops_out=compiled_ops,
        replay_meta_out=replay_meta,
        strict_profile=strict_profile,
        lo_ops_out=_dossier_canonical_ops if needs_dossier else None,
        failed_ops_out=failed_ops,
        oracle_selector=oracle_selector,
    )
    oracle_ctx = get_consolidated_oracle_context(
        sid,
        selector=oracle_selector or _LATEST_CONSOLIDATED_SELECTOR,
    )
    oracle_root = None
    if oracle_ctx.locator:
        corpus = get_corpus()
        oracle_bytes = corpus.read_locator(oracle_ctx.locator)
        if oracle_bytes is not None:
            oracle_root = etree.fromstring(oracle_bytes)

    if oracle_root is None:
        print(f"ERROR: no oracle found for {sid}", file=sys.stderr)
        sys.exit(1)

    replay_secs = extract_ir_sections(master.ir)
    oracle_secs = _extract_sections(oracle_root)
    replay_secs, oracle_secs = reconcile_unique_unscoped_aliases(
        replay_secs, oracle_secs
    )
    oracle_secs_text = {k: _el_text(v) for k, v in oracle_secs.items()}
    blame_map = _build_blame_map(compiled_ops)
    repeal_blame_map = {
        key: op
        for key, op in blame_map.items()
        if str(op.get("action", "")).lower() == "repeal"
    }
    failed_section_keys = {
        key
        for failure in failed_ops
        for key in [section_key_from_compile_failure(failure)]
        if key
    }

    all_keys = sorted(set(replay_secs) | set(oracle_secs), key=_section_sort_key)

    # Section filter
    if section_filter:
        all_keys = [k for k in all_keys if _section_filter_matches(k, section_filter)]

    oracle_ver = _oracle_version_label(oracle_ctx.locator) if oracle_ctx.locator else "absent"
    oracle_cutoff_date = oracle_ctx.cutoff_date
    oracle_ver_mid = oracle_ctx.oracle_version_amendment_id or None

    print(f"Statute : {sid}")
    print(f"Title   : {master.title}")
    print(f"Mode    : {mode}")
    print(f"Oracle  : {oracle_ver}")
    print()

    _print_temporal_debug_block(
        temporal_events=master.products.temporal_events,
    )

    facade = None
    if show_compile_summary or show_facade:
        from lawvm.finland.compile import compile_fi_facade_from_replay

        facade = compile_fi_facade_from_replay(
            parent_id=sid,
            replay_result=master,
            replay_mode=mode,
            strict_profile=strict_profile,
            compiled_ops=compiled_ops,
            replay_meta=replay_meta,
            canonical_ops=_dossier_canonical_ops,
            failed_ops=failed_ops,
        )

    if show_compile_summary:
        assert facade is not None
        _print_compile_summary(
            report_record=report_record_from_facade(
                statute_id=sid,
                facade=facade,
                compiled_ops=compiled_ops,
                failed_ops=failed_ops,
                source_adjudication=master.source_adjudication,
            ),
        )

    if show_facade:
        assert facade is not None
        _print_facade_summary(
            facade,
            html_noncommensurable_reason=(
                str(master.source_adjudication.html_noncommensurable_reason or "")
                if master.source_adjudication is not None
                else ""
            ),
        )

    counts = {"ORACLE_STALE": 0, "REPLAY_EXTRA": 0, "REPLAY_MISSING": 0,
              "SOURCE_PATHOLOGY": 0, "EDITORIAL_CONVENTION": 0, "UNKNOWN": 0, "MISSING": 0}
    shown = 0
    pre_blame_cache: Dict[str, Dict[str, Any]] = {}
    oracle_suspect = _oracle_suspect_value(master)

    for key in all_keys:
        r_node = replay_secs.get(key)
        o_el = oracle_secs.get(key)
        blame_op = _lookup_blame_op(blame_map, key)
        future_version = replay_section_has_future_effective_version(
            master,
            key,
            oracle_cutoff_date,
        )

        if r_node is None:
            raw_num = _num_text(o_el) if o_el is not None else key
            if o_el is not None:
                stripped_oracle = _el_text(o_el)
                missing_blame_op = blame_op
                if str(missing_blame_op.get("action", "")).lower() != "repeal":
                    repeal_blame = _lookup_blame_op(repeal_blame_map, key)
                    if repeal_blame:
                        missing_blame_op = repeal_blame
                if oracle_text_reduces_to_bare_section_stub(stripped_oracle):
                    counts["EDITORIAL_CONVENTION"] += 1
                    print(f"  {raw_num} — EDITORIAL_CONVENTION")
                    print("    oracle: editorial stub only — omission is acceptable")
                    print()
                    shown += 1
                    continue
                if (
                    missing_blame_op
                    and str(missing_blame_op.get("action", "")).lower() == "repeal"
                    and oracle_has_repeal_banner_with_prior_wording(stripped_oracle)
                ):
                    pre_secs = pre_blame_cache.get(str(missing_blame_op.get("source_statute", "")))
                    if pre_secs is None:
                        pre_secs = _get_pre_blame_sections(
                            sid,
                            str(missing_blame_op.get("source_statute", "")),
                            mode,
                            oracle_selector=oracle_selector,
                        )
                        pre_blame_cache[str(missing_blame_op.get("source_statute", ""))] = pre_secs
                    scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                        dict(pre_secs),
                        {key: o_el},
                    )
                    pre_node = scoped_pre_secs.get(key)
                    pre_text = _irnode_text(pre_node) if pre_node is not None else ""
                    if pre_text and Levenshtein.ratio(_clean(pre_text), _clean(stripped_oracle)) >= 0.95:
                        counts["ORACLE_STALE"] += 1
                        print(f"  {raw_num} — ORACLE_STALE")
                        print(
                            "    oracle: repeal banner retains prior wording after the blamed repeal"
                        )
                        print()
                        shown += 1
                        continue
                if mode == "legal_pit" and blame_op and blame_title_indicates_temporary_amendment(
                    str(blame_op.get("source_title", ""))
                ):
                    counts["ORACLE_STALE"] += 1
                    print(f"  {raw_num} — ORACLE_STALE")
                    print(
                        "    oracle: temporary-amendment version residue only — "
                        "legal_pit omission is acceptable"
                    )
                    print()
                    shown += 1
                    continue
                if future_version:
                    counts["ORACLE_STALE"] += 1
                    print(f"  {raw_num} — ORACLE_STALE")
                    print(
                        "    oracle: replay timeline carries only a future-effective "
                        "version beyond the oracle cutoff"
                    )
                    print()
                    shown += 1
                    continue
                if (
                    oracle_cutoff_date is not None
                    and replay_section_matches_text_at_cutoff(
                        master,
                        key,
                        stripped_oracle,
                        oracle_cutoff_date.isoformat(),
                        statute_id=sid,
                        title=master.title,
                        label_norm=fi_label_norm,
                    )
                ):
                    counts["ORACLE_STALE"] += 1
                    print(f"  {raw_num} — ORACLE_STALE")
                    print(
                        "    oracle: snapshot mixes future-effective versioning with "
                        "cutoff-date section text"
                    )
                    print()
                    shown += 1
                    continue
                if blame_op and str(blame_op.get("action", "")).lower() == "repeal":
                    blame_source = str(blame_op.get("source_statute", ""))
                    if blame_source:
                        pre_secs = pre_blame_cache.get(blame_source)
                        if pre_secs is None:
                            pre_secs = _get_pre_blame_sections(
                                sid,
                                blame_source,
                                mode,
                                oracle_selector=oracle_selector,
                            )
                            pre_blame_cache[blame_source] = pre_secs
                        scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                            dict(pre_secs),
                            {key: o_el},
                        )
                        pre_node = scoped_pre_secs.get(key)
                        pre_text = _irnode_text(pre_node) if pre_node is not None else ""
                        if pre_text and Levenshtein.ratio(_clean(pre_text), _clean(stripped_oracle)) >= 0.95:
                            counts["ORACLE_STALE"] += 1
                            print(f"  {raw_num} — ORACLE_STALE")
                            print(
                                f"    oracle: appears stale against pre-{blame_source} section text after repeal"
                            )
                            print()
                            shown += 1
                            continue
                if (
                    missing_blame_op
                    and str(missing_blame_op.get("action", "")).lower() == "repeal"
                ):
                    blame_source = str(missing_blame_op.get("source_statute", ""))
                    if blame_source:
                        pre_secs = pre_blame_cache.get(blame_source)
                        if pre_secs is None:
                            pre_secs = _get_pre_blame_sections(
                                sid,
                                blame_source,
                                mode,
                                oracle_selector=oracle_selector,
                            )
                            pre_blame_cache[blame_source] = pre_secs
                        scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                            dict(pre_secs),
                            {key: o_el},
                        )
                        pre_node = scoped_pre_secs.get(key)
                        pre_text = _irnode_text(pre_node) if pre_node is not None else ""
                        if pre_text and Levenshtein.ratio(_clean(pre_text), _clean(stripped_oracle)) >= 0.95:
                            counts["ORACLE_STALE"] += 1
                            print(f"  {raw_num} — ORACLE_STALE")
                            print(
                                f"    oracle: appears stale against pre-{blame_source} section text after repeal"
                            )
                            print()
                            shown += 1
                            continue
            counts["MISSING"] += 1
            print(f"  {raw_num} — MISSING from replay")
            print(f"    oracle: {_el_text(o_el)[:80]}…" if o_el is not None else "")
            shown += 1
            continue

        if o_el is None:
            raw_num = key
            print(f"  {raw_num} — EXTRA in replay (not in oracle)")
            shown += 1
            continue

        r_text = _irnode_text(r_node)
        o_text = _el_text(o_el)
        cr = _clean(r_text)
        co = _clean(o_text)
        score = Levenshtein.ratio(cr, co) if cr and co else (1.0 if not cr and not co else 0.0)
        if score >= 0.9999:
            continue
        if score >= threshold:
            continue
        # Use cleaned-content length diff: oracle raw text has 2-4x whitespace
        # inflation from XML structure, making raw len_diff meaningless.
        len_diff = len(cr) - len(co)
        sign = "+" if len_diff >= 0 else ""

        selector_mode = "latest_cached_editorial"
        if getattr(getattr(oracle_selector, "mode", None), "value", "") == "bench_comparable":
            selector_mode = "bench_comparable"

        diagnosis, explanation = _diagnose(
            r_text,
            o_text,
            blame_op,
            oracle_selector_mode=selector_mode,
        )
        if future_version and diagnosis in ("REPLAY_MISSING", "REPLAY_EXTRA", "UNKNOWN"):
            diagnosis = "ORACLE_STALE"
            explanation = "replay materializes a future-dated version beyond the oracle cutoff"
        if (
            diagnosis in ("REPLAY_MISSING", "REPLAY_EXTRA", "UNKNOWN")
            and blame_op
            and blame_title_indicates_temporary_amendment(
                str(blame_op.get("source_title", ""))
            )
        ):
            diagnosis = "ORACLE_STALE"
            explanation = (
                "oracle appears to retain temporary-amendment text beyond the live replay state"
            )
        if (
            diagnosis in ("MISSING", "EXTRA", "REPLAY_MISSING", "REPLAY_EXTRA", "UNKNOWN")
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
            diagnosis = "ORACLE_STALE"
            explanation = (
                "oracle snapshot mixes future-effective versioning with cutoff-date section text"
            )
        if diagnosis in ("REPLAY_MISSING", "REPLAY_EXTRA", "UNKNOWN") and blame_op:
            blame_source = blame_op.get("source_statute", "")
            blame_action = blame_op.get("action", "").lower()
            if blame_source:
                if (
                    oracle_suspect
                    and oracle_ver_mid
                    and blame_source == oracle_ver_mid
                ):
                    diagnosis = "ORACLE_STALE"
                    explanation = (
                        f"oracle version {oracle_ver} is itself future-effective relative to the cutoff"
                    )
                if blame_source_postdates_oracle_version(blame_source, oracle_ver_mid or ""):
                    diagnosis = "ORACLE_STALE"
                    explanation = (
                        f"oracle version {oracle_ver} predates blamed amendment {blame_source}"
                    )
                if (
                    diagnosis != "ORACLE_STALE"
                    and oracle_suspect
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
                    diagnosis = "ORACLE_STALE"
                    explanation = (
                        "oracle snapshot mixes future-effective versioning with cutoff-date section text"
                    )
                if diagnosis != "ORACLE_STALE":
                    pre_secs = pre_blame_cache.get(blame_source)
                    if pre_secs is None:
                        pre_secs = _get_pre_blame_sections(
                            sid,
                            blame_source,
                            mode,
                            oracle_selector=oracle_selector,
                        )
                        pre_blame_cache[blame_source] = pre_secs
                    scoped_pre_secs, _ = reconcile_unique_unscoped_aliases(
                        dict(pre_secs),
                        {key: o_el},
                    )
                    pre_node = scoped_pre_secs.get(key)
                    if pre_node is not None:
                        pre_text = _irnode_text(pre_node)
                        if (
                            diagnosis == "REPLAY_MISSING"
                            and blame_action == "repeal"
                            and is_probable_repeal_stale_oracle(
                                r_text,
                                o_text,
                                pre_text,
                            )
                        ):
                            diagnosis = "ORACLE_STALE"
                            explanation = f"oracle appears stale against repeal in {blame_source}"
                        else:
                            pre_r = _clean(pre_text)
                            post_r = _clean(r_text)
                            ora_r = _clean(o_text)
                            if (
                                blame_action == "repeal"
                                and pre_r
                                and post_r
                                and ora_r
                            ):
                                pre_ratio = Levenshtein.ratio(pre_r, ora_r)
                                post_ratio = Levenshtein.ratio(post_r, ora_r)
                                if post_ratio >= pre_ratio + PRE_BLAME_IMPROVEMENT_EPS:
                                    diagnosis = "ORACLE_STALE"
                                    explanation = (
                                        f"oracle appears stale against repeal in {blame_source} "
                                        f"(post-repeal state is closer than pre-{blame_source})"
                                    )
                            if (
                                diagnosis != "ORACLE_STALE"
                                and diagnosis == "REPLAY_MISSING"
                                and blame_action == "repeal"
                                and "kumottu" in r_text.lower()
                                and oracle_section_duplicates_adjacent_section(
                                    key,
                                    o_text,
                                    oracle_secs_text,
                                )
                            ):
                                diagnosis = "ORACLE_STALE"
                                explanation = (
                                    f"oracle appears stale against repeal in {blame_source} "
                                    f"(oracle section duplicates adjacent section text)"
                                )
                            if diagnosis != "ORACLE_STALE" and pre_r and ora_r and Levenshtein.ratio(pre_r, ora_r) >= 0.95:
                                diagnosis = "ORACLE_STALE"
                                if blame_title_indicates_temporary_amendment(
                                    str(blame_op.get("source_title", ""))
                                ):
                                    explanation = (
                                        f"oracle appears stale at temporary amendment {blame_source}"
                                    )
                                else:
                                    explanation = (
                                        f"oracle appears stale against pre-{blame_source} state"
                                    )
                            if (
                                diagnosis != "ORACLE_STALE"
                                and diagnosis == "REPLAY_MISSING"
                                and blame_action in {"replace", "insert"}
                                and pre_r
                                and post_r
                                and not any(
                                    key == failed_key or key.endswith("/" + failed_key)
                                    for failed_key in failed_section_keys
                                )
                                and oracle_text_reduces_to_replay_by_dropping_sentences(
                                    r_text,
                                    o_text,
                                )
                            ):
                                diagnosis = "ORACLE_STALE"
                                explanation = (
                                    f"oracle retains superseded same-section sentence residue beyond {blame_source}"
                                )
        if diagnosis == "UNKNOWN":
            source_pathology_diagnosis = _source_pathology_diagnosis_for_blame(
                master,
                blame_op,
            )
            if source_pathology_diagnosis is not None:
                diagnosis, explanation = source_pathology_diagnosis
        counts[diagnosis] = counts.get(diagnosis, 0) + 1

        raw_num = (r_node.label + ' §' if isinstance(r_node, IRNode) and r_node.label else key)
        print(f"  {raw_num} — {score*100:.1f}%  (replay {sign}{len_diff} chars)")

        if blame_op:
            src = blame_op.get("source_statute", "?")
            title = blame_op.get("source_title", "")[:50]
            witness_rid = blame_op.get("witness_rule_id")
            witness_suffix = f"  [{witness_rid}]" if witness_rid else ""
            print(f"    Last modified: {src}  {title}{witness_suffix}")
            johto = _load_johtolause(src)
            if johto:
                # Collapse whitespace runs from verb normalization, truncate
                johto_short = re.sub(r'\s+', ' ', johto).strip()[:120]
                print(f"    Johtolause   : \"{johto_short}\"")
        else:
            print("    Last modified: (base statute — no op compiled)")

        snippet = _find_divergence_snippet(r_text, o_text)
        if snippet:
            print(f"    Divergence   : {snippet}")

        print(f"    Diagnosis    : {diagnosis} — {explanation}")
        print()
        shown += 1

    if shown == 0:
        print(f"  All sections at or above threshold ({threshold:.0%}) — no divergence to explain")
    else:
        print(f"Summary: {sum(counts.values())} diverging sections")
        for code, n in counts.items():
            if n:
                print(f"  {code:<22} {n}")


def main(args) -> None:
    section_filter = getattr(args, "section", None)
    threshold = getattr(args, "threshold", 1.0)
    mode: Literal["finlex_oracle", "legal_pit"] = getattr(args, "mode", "finlex_oracle")
    oracle_selector_mode = getattr(args, "oracle_selector_mode", "latest_cached_editorial")
    oracle_version_amendment_id = getattr(args, "oracle_version_amendment_id", "")
    oracle_selector = _oracle_selector_from_args(
        oracle_selector_mode,
        oracle_version_amendment_id,
    )
    show_compile_summary = getattr(args, "compile_summary", False)
    show_facade = getattr(args, "facade", False)
    use_strict = getattr(args, "strict", False)
    strict_profile = FINLAND_INGESTION_V1 if use_strict else None

    _explain_sync(
        sid=args.statute_id,
        section_filter=section_filter,
        threshold=threshold,
        mode=mode,
        oracle_selector=oracle_selector,
        show_compile_summary=show_compile_summary,
        strict_profile=strict_profile,
        show_facade=show_facade,
    )
