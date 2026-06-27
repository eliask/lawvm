"""Future-repeal pre-scan for uncovered body recovery."""
from __future__ import annotations

import re
import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Mapping, Optional

import lxml.etree as etree

from lawvm.core.semantic_types import StructuralAction
from lawvm.core.xml_parse import parse_corpus_xml
from lawvm.finland.acquisition import build_amendment_acquisition_result
from lawvm.finland.future_repeal import RepealTargetRef
from lawvm.finland.helpers import _norm_num_token
from lawvm.finland.johtolause import extract_legal_ops as extract_johtolause_legal_ops
from lawvm.finland.metadata import _amendment_effective_date
from lawvm.finland.vts import VtsSkippedTarget, VtsSourceDiagnostic, extract_voimaantulo_repeals
from lawvm.core.quirks_disposition import QuirksDisposition

if TYPE_CHECKING:
    from lawvm.corpus_store import CorpusStore

PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID = "PARSE.FUTURE_REPEAL_PRESCAN_DIAGNOSTIC"

PreScanRepealDiagnosticReason = Literal[
    "missing_source",
    "prescan_parse_error",
    "vts_extraction_error",
]


@dataclass(frozen=True, slots=True)
class PreScanRepealDiagnostic:
    """Typed visibility record for future-repeal pre-scan blind spots."""

    rule_id: str
    reason_code: PreScanRepealDiagnosticReason
    source_reason: str
    source_statute: str
    source_excerpt: str = ""
    exception_type: str = ""
    exception_message: str = ""
    phase: str = "frontend_extraction"
    family: str = "future_repeal_prescan"
    blocking: bool = False
    strict_disposition: str = "record"
    quirks_disposition: QuirksDisposition = QuirksDisposition.RECORD

    def as_detail(self) -> dict[str, object]:
        return {
            "rule_id": self.rule_id,
            "reason_code": self.reason_code,
            "source_reason": self.source_reason,
            "source_statute": self.source_statute,
            "source_excerpt": self.source_excerpt,
            "exception_type": self.exception_type,
            "exception_message": self.exception_message,
            "phase": self.phase,
            "family": self.family,
            "blocking": self.blocking,
            "strict_disposition": self.strict_disposition,
            "quirks_disposition": self.quirks_disposition,
        }


@dataclass(frozen=True, slots=True)
class PreScanRepealTargetsRequest:
    """Inputs for lightweight future-repeal pre-scan over an amendment schedule."""

    muutoslait: list[str]
    corpus_store: "CorpusStore"
    parent_id: str = ""
    parent_title: str = ""
    cutoff_date: Optional[dt.date] = None
    effective_dates_by_amendment: Mapping[str, dt.date] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PreScanRepealTargetsSinks:
    """Diagnostic channels for VTS extraction during future-repeal pre-scan."""

    vts_skipped_targets_out: Optional[list[VtsSkippedTarget]] = None
    vts_source_diagnostics_out: Optional[list[VtsSourceDiagnostic]] = None
    prescan_diagnostics_out: Optional[list[PreScanRepealDiagnostic]] = None


def _prescan_source_excerpt(xml_bytes: bytes | None) -> str:
    if not xml_bytes:
        return ""
    return re.sub(r"\s+", " ", xml_bytes.decode("utf-8", errors="replace")).strip()[:160]


def _record_prescan_diagnostic(
    diagnostics_out: Optional[list[PreScanRepealDiagnostic]],
    *,
    reason_code: PreScanRepealDiagnosticReason,
    source_reason: str,
    source_statute: str,
    xml_bytes: bytes | None = None,
    exc: BaseException | None = None,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        PreScanRepealDiagnostic(
            rule_id=PRESCAN_REPEAL_TARGET_DIAGNOSTIC_RULE_ID,
            reason_code=reason_code,
            source_reason=source_reason,
            source_statute=source_statute,
            source_excerpt=_prescan_source_excerpt(xml_bytes),
            exception_type=exc.__class__.__name__ if exc is not None else "",
            exception_message=str(exc)[:240] if exc is not None else "",
        )
    )


def _pre_scan_repeal_targets(
    request: PreScanRepealTargetsRequest,
    sinks: Optional[PreScanRepealTargetsSinks] = None,
) -> list[set[RepealTargetRef]]:
    """Scan amendment schedule and return per-amendment REPEAL target sets."""
    muutoslait = request.muutoslait
    corpus_store = request.corpus_store
    parent_id = request.parent_id
    parent_title = request.parent_title
    cutoff_date = request.cutoff_date
    effective_dates_by_amendment = request.effective_dates_by_amendment
    vts_skipped_targets_out = sinks.vts_skipped_targets_out if sinks is not None else None
    vts_source_diagnostics_out = sinks.vts_source_diagnostics_out if sinks is not None else None
    prescan_diagnostics_out = sinks.prescan_diagnostics_out if sinks is not None else None

    per_amendment: list[set[RepealTargetRef]] = []

    for amendment_id in muutoslait:
        targets: set[RepealTargetRef] = set()
        xml_bytes = corpus_store.read_source(amendment_id)
        if xml_bytes is None:
            _record_prescan_diagnostic(
                prescan_diagnostics_out,
                reason_code="missing_source",
                source_reason="future-repeal pre-scan could not read amendment source",
                source_statute=amendment_id,
            )
            per_amendment.append(targets)
            continue
        try:
            eff_date = effective_dates_by_amendment.get(amendment_id)
            if eff_date is None:
                tree = parse_corpus_xml(xml_bytes)
                eff_date = _amendment_effective_date(tree)
            if cutoff_date is not None and eff_date is not None and eff_date > cutoff_date:
                per_amendment.append(targets)
                continue
            if b"kumotaan" in xml_bytes.lower():
                acquisition = build_amendment_acquisition_result(
                    xml_bytes=xml_bytes,
                    parent_id=parent_id,
                    amendment_id=amendment_id,
                    source_title="",
                    parent_title=parent_title,
                )
                johto = acquisition.decision.chosen_normalized_text
            else:
                johto = ""
            if johto and "kumotaan" in johto.lower():
                legal_ops = extract_johtolause_legal_ops(johto)
                for lo in legal_ops:
                    if lo.action is not StructuralAction.REPEAL:
                        continue
                    path_dict = {k: v for k, v in lo.target.path}
                    has_sub = (
                        "subsection" in path_dict
                        or "paragraph" in path_dict
                        or "item" in path_dict
                    )
                    if "section" in path_dict and not has_sub:
                        sec_norm = _norm_num_token(str(path_dict["section"]))
                        ch_raw = path_dict.get("chapter")
                        ch_norm: Optional[str] = (
                            _norm_num_token(str(ch_raw)).removesuffix("luku") if ch_raw else None
                        )
                        targets.add(RepealTargetRef.section(sec_norm, ch_norm))
                    elif "chapter" in path_dict and not has_sub:
                        ch_norm = _norm_num_token(str(path_dict["chapter"])).removesuffix("luku")
                        targets.add(RepealTargetRef.chapter(ch_norm))
            if parent_id:
                try:
                    vts_ops = extract_voimaantulo_repeals(
                        xml_bytes,
                        parent_id,
                        parent_title=parent_title,
                        skipped_targets_out=vts_skipped_targets_out,
                        source_diagnostics_out=vts_source_diagnostics_out,
                    )
                    for op in vts_ops:
                        sec_norm = _norm_num_token(op.target_cols.target_section) if op.target_cols.target_section else ""
                        ch_norm: Optional[str] = (
                            _norm_num_token(op.target_cols.target_chapter).removesuffix("luku")
                            if op.target_cols.target_chapter
                            else None
                        )
                        if sec_norm:
                            targets.add(RepealTargetRef(op.target_cols.target_unit_kind, sec_norm, ch_norm))
                except (ValueError, KeyError, AttributeError, TypeError, IndexError) as exc:
                    _record_prescan_diagnostic(
                        prescan_diagnostics_out,
                        reason_code="vts_extraction_error",
                        source_reason="future-repeal pre-scan VTS extraction failed",
                        source_statute=amendment_id,
                        xml_bytes=xml_bytes,
                        exc=exc,
                    )
        except (ValueError, KeyError, AttributeError, TypeError, IndexError, etree.XMLSyntaxError) as exc:
            _record_prescan_diagnostic(
                prescan_diagnostics_out,
                reason_code="prescan_parse_error",
                source_reason="future-repeal pre-scan could not inspect amendment source",
                source_statute=amendment_id,
                xml_bytes=xml_bytes,
                exc=exc,
            )
        per_amendment.append(targets)

    return per_amendment
