"""Sweden frontend helpers for LawVM.

This first Sweden slice is intentionally source-layered rather than replay-first.
It consumes structured RK beta/RK-style JSON documents and exposes:

- `SESourceRecord`: source/provenance metadata for one SFS act
- `SEAmendmentRegisterEntry`: structured amendment-register rows
- `parse_se_statute()`: current-text IR parser for Swedish consolidated text

Original-SFS PDF acquisition and amendment-op compilation are separate later
phases. The current code keeps those entry points explicit but unimplemented.

Architectural observations
--------------------------
- Sweden is intentionally source-layered right now, which is coherent for the
  current maturity level.
- The main architectural gap is that the shared waists are not yet explicit
  here: clause surface, payload surface, and direct core adjudication ownership
  are still mostly future work rather than enforced seams.

TODO
----
- Introduce an explicit clause/effect surface for Swedish enacting clauses
  before replay-specific heuristics accumulate.
- Converge replay findings toward shared/core adjudication vocab instead of
  leaving them wrapper-only.

Actionables
-----------
- Keep source acquisition/provenance concerns separate from semantic lowering.
- When official-act op compilation deepens, establish the waist boundaries
  early instead of letting `LegalOperation` become another long-lived catch-all.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from enum import Enum
import json
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any, Optional, cast
from urllib.parse import quote, urljoin

from lawvm.core import tree_ops
from lawvm.core.ir import (
    IRNode,
    IRStatute,
    LegalAddress,
    LegalOperation,
    OperationSource,
    TextPatchSpec,
    TextSelector,
)
from lawvm.core.ir_helpers import irnode_from_dict
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction, TextPatchKindEnum
from lawvm.replay_adjudication import CompileAdjudication

_SFS_ID_RE = re.compile(r"\b(\d{4}:\d+)\b")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_CHAPTER_RE = re.compile(r"^(?P<label>\d+[a-z]?|[IVXLC]+)\s+kap\.\s*(?P<title>.*)$", re.IGNORECASE)
_SECTION_RE = re.compile(r"^(?P<label>\d+\s*[a-z]?)\s*§(?P<tail>.*)$", re.IGNORECASE)
_ITEM_RE = re.compile(r"^(?P<label>\d+|[a-z])[\.\)]\s+(?P<text>.+)$", re.IGNORECASE)
_APPENDIX_RE = re.compile(r"^(Bilaga)\s*(?:\*\s*)?(?P<label>\d+[a-z]?|[A-Z])?\s*(?P<title>.*)$", re.IGNORECASE)
_MARKER_RE = re.compile(r"/(?P<phrase>[^/]*?)\s+(?P<kind>[IU]):(?P<date>\d{4}-\d{2}-\d{2})/")
_PROP_RE = re.compile(r"\bProp\.\s*([^,;]+)")
_BET_RE = re.compile(r"\bbet\.\s*([^,;]+)", re.IGNORECASE)
_RSKR_RE = re.compile(r"\brskr\.\s*([^,;]+)", re.IGNORECASE)
_PDF_HREF_RE = re.compile(r'href=["\']([^"\']+\.pdf(?:\?[^"\']*)?)["\']', re.IGNORECASE)
_FOOTNOTE_LINE_RE = re.compile(
    r"^\d+\s+"
    r"(?:Jfr\b|Senaste lydelse\b|Tidigare lydelse\b|Lydelse enligt\b|"
    r"Paragrafen\b|Rubriken\b|Förordningen omtryckt\b).+$",
    re.IGNORECASE,
)
_TITLE_BASE_SFS_RE = re.compile(r"\((\d{4}:\d+)\)")
_SV_DATE_TEXT_RE = re.compile(
    r"\b(?P<day>\d{1,2})\s+"
    r"(?P<month>januari|februari|mars|april|maj|juni|juli|augusti|september|oktober|november|december)\s+"
    r"(?P<year>\d{4})\b",
    re.IGNORECASE,
)
_AMENDING_TITLE_RE = re.compile(r"\bom ändring i\b", re.IGNORECASE)
_TABLE_COLUMN_SPLIT_RE = re.compile(r"\t+|\s{2,}")
_TABLE_ROW_START_RE = re.compile(r"^\s*(\d+)\.\s*(.+?)\s*$")

_SV_MONTHS = {
    "januari": "01",
    "februari": "02",
    "mars": "03",
    "april": "04",
    "maj": "05",
    "juni": "06",
    "juli": "07",
    "augusti": "08",
    "september": "09",
    "oktober": "10",
    "november": "11",
    "december": "12",
}


@dataclass
class _SEMutableNode:
    """Local mutable builder used only inside Sweden parsing before freezing to core IR."""

    kind: str
    label: str | None = None
    text: str = ""
    attrs: dict[str, object] = field(default_factory=dict)
    children: list["_SEMutableNode"] = field(default_factory=list)

    def to_irnode(self) -> IRNode:
        return IRNode(
            kind=IRNodeKind(self.kind),
            label=self.label,
            text=self.text,
            attrs=dict(self.attrs),
            children=tuple(child.to_irnode() for child in self.children),
        )


class SESourceConfidence(str, Enum):
    OFFICIAL_PDF_ONLY = "OFFICIAL_PDF_ONLY"
    CURRENT_TEXT_ONLY = "CURRENT_TEXT_ONLY"
    CURRENT_TEXT_PLUS_REGISTER = "CURRENT_TEXT_PLUS_REGISTER"
    PDF_PLUS_REGISTER = "PDF_PLUS_REGISTER"
    PDF_PLUS_REGISTER_PLUS_BASE_VIEW = "PDF_PLUS_REGISTER_PLUS_BASE_VIEW"
    HISTORICAL_SNAPSHOT_CONFIRMED = "HISTORICAL_SNAPSHOT_CONFIRMED"


@dataclass(frozen=True)
class SESourceUrls:
    official_sfs_doc_url: Optional[str] = None
    official_sfs_pdf_url: Optional[str] = None
    rk_sfst_url: Optional[str] = None
    rk_beta_base_url: Optional[str] = None
    rk_beta_current_url: Optional[str] = None
    rk_sfsr_url: Optional[str] = None
    riksdagen_sfs_url: Optional[str] = None


@dataclass(frozen=True)
class SEParliamentaryPackageLink:
    sfs_id: str
    prop_id: str = ""
    bet_id: str = ""
    rskr_id: str = ""
    eu_reference: str = ""
    riksdagen_document_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SEAmendmentRegisterEntry:
    base_sfs_id: str
    amending_sfs_id: str
    amending_title: str
    scope_text: str
    effective_date: str = ""
    has_overgangsbestammelser: bool = False
    preparatory_works: str = ""
    parliamentary_links: tuple[SEParliamentaryPackageLink, ...] = ()
    raw_row_text: str = ""


@dataclass(frozen=True)
class SESourceRecord:
    sfs_id: str
    title: str
    act_type: str
    department: str = ""
    issued_date: str = ""
    published_date: str = ""
    effective_markers: tuple[str, ...] = ()
    amended_through_sfs: str = ""
    repealed: bool = False
    repealed_by_sfs: str = ""
    repeal_date: str = ""
    source_urls: SESourceUrls = field(default_factory=SESourceUrls)
    source_text_kind: str = ""
    source_confidence: SESourceConfidence = SESourceConfidence.CURRENT_TEXT_ONLY
    parliamentary_links: tuple[SEParliamentaryPackageLink, ...] = ()
    amendment_register: tuple[SEAmendmentRegisterEntry, ...] = ()


@dataclass(frozen=True)
class SEOfficialProvisionText:
    label: str
    text: str


@dataclass(frozen=True)
class SEOfficialHeadingText:
    before_label: str
    text: str


@dataclass(frozen=True)
class SEOfficialAppendixText:
    label: str
    title: str = ""
    text: str = ""


@dataclass(frozen=True)
class SEOfficialActText:
    sfs_id: str
    title: str
    act_type: str
    amended_act_sfs_id: str = ""
    is_amending_act: bool = False
    published_date: str = ""
    issued_date: str = ""
    enacting_clause: str = ""
    effective_clause: str = ""
    affected_section_labels: tuple[str, ...] = ()
    provisions: tuple[SEOfficialProvisionText, ...] = ()
    inserted_headings: tuple[SEOfficialHeadingText, ...] = ()
    appendices: tuple[SEOfficialAppendixText, ...] = ()
    signatories: tuple[str, ...] = ()
    footnotes: tuple[str, ...] = ()


@dataclass(frozen=True)
class SEOfficialClauseSurface:
    """Typed clause surface for Sweden official-act lowering."""

    sfs_id: str
    title: str
    amended_act_sfs_id: str = ""
    is_amending_act: bool = False
    enacting_clause: str = ""
    effective_clause: str = ""
    affected_section_labels: tuple[str, ...] = ()
    repealed_section_labels: tuple[str, ...] = ()
    renumber_pairs: tuple[tuple[str, str], ...] = ()
    replace_section_labels: tuple[str, ...] = ()
    inserted_section_labels: tuple[str, ...] = ()
    inserted_appendix_labels: tuple[str, ...] = ()
    effective_date: str = ""


@dataclass(frozen=True)
class SEOfficialPayloadSurface:
    """Typed payload surface for Sweden official-act lowering."""

    provisions: tuple[SEOfficialProvisionText, ...] = ()
    inserted_headings: tuple[SEOfficialHeadingText, ...] = ()
    appendices: tuple[SEOfficialAppendixText, ...] = ()


@dataclass(frozen=True)
class SEOfficialElaboratedIntent:
    """Typed elaboration between Sweden source surfaces and canonical lowering."""

    clause_surface: SEOfficialClauseSurface
    payload_surface: SEOfficialPayloadSurface
    issued_date: str = ""
    provision_labels: tuple[str, ...] = ()
    supported_section_labels: tuple[str, ...] = ()
    inserted_heading_labels: tuple[str, ...] = ()
    appendix_labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class SEOfficialEffectPlanItem:
    """One planned canonical effect before lowering to `LegalOperation`."""

    kind: str
    target_label: str = ""
    destination_label: str = ""
    payload_label: str = ""
    text_patch: TextPatchSpec | None = None


@dataclass(frozen=True)
class SEOfficialEffectsPlan:
    """Typed canonical-effects plan for Sweden official-act lowering."""

    sfs_id: str
    title: str
    amended_act_sfs_id: str = ""
    is_amending_act: bool = False
    enacting_clause: str = ""
    effective_clause: str = ""
    effective_date: str = ""
    issued_date: str = ""
    elaboration: SEOfficialElaboratedIntent | None = None
    frontier_classification: str = ""
    frontier_detail: str = ""
    planned_items: tuple[SEOfficialEffectPlanItem, ...] = ()
    planned_operation_count: int = 0


def _coerce_document(payload: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("expected Sweden source document to decode to a JSON object")
    return data


def _date_only(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    match = _DATE_RE.search(value)
    return match.group(1) if match else ""


def _normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def _join_wrapped_lines(lines: list[str]) -> str:
    parts: list[str] = []
    for line in lines:
        if not line:
            continue
        if parts and parts[-1].endswith("-"):
            parts[-1] = parts[-1][:-1] + line
        else:
            parts.append(line)
    return _normalize_space(" ".join(parts))


def _join_preserving_paragraphs(lines: list[str]) -> str:
    paragraphs: list[str] = []
    current: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if current:
                paragraphs.append(_join_wrapped_lines(current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(_join_wrapped_lines(current))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph)


def _text_blocks(text: str) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[str] = []
    current: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if current:
                blocks.append(_join_wrapped_lines(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append(_join_wrapped_lines(current))
    return [block for block in blocks if block]


def _parse_swedish_date_text(text: str) -> str:
    match = _SV_DATE_TEXT_RE.search(text)
    if not match:
        return ""
    month = _SV_MONTHS.get(match.group("month").lower())
    if not month:
        return ""
    return f"{match.group('year')}-{month}-{int(match.group('day')):02d}"


def _split_paragraphs_preserve_lines(text: str) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw_line.strip():
            if current:
                paragraphs.append(current)
                current = []
            continue
        current.append(raw_line.rstrip())
    if current:
        paragraphs.append(current)
    return paragraphs


def _split_se_current_raw_blocks(text: str) -> list[list[str]]:
    """Split RK current fulltext into raw blocks, forcing new blocks at top-level sections."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        cleaned_line, _, _ = _extract_markers(stripped)
        is_top_level_section = raw_line == raw_line.lstrip(" \t") and bool(_SECTION_RE.match(cleaned_line))
        if is_top_level_section and current:
            blocks.append(current)
            current = []
        if not stripped:
            if current:
                blocks.append(current)
                current = []
            continue
        current.append(raw_line.rstrip())
    if current:
        blocks.append(current)
    return blocks


def _extract_section_labels_from_clause(clause: str) -> tuple[str, ...]:
    point_labels = _extract_point_labels_from_clause(clause)
    if point_labels:
        return point_labels
    match = re.search(r"\batt\s+(.+?)\s+§{1,2}(?:\s|$)", clause, re.IGNORECASE)
    if not match:
        return ()
    head = match.group(1)
    labels = re.findall(r"\b(\d+\s*[a-z]?)\b", head, re.IGNORECASE)
    out: list[str] = []
    for label in labels:
        normalized = _label_norm(label)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def _extract_point_labels_from_clause(clause: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(
        r"\bpunkt(?:en|erna)?\s+((?:\d+\s*[a-z]?\s*(?:,|\s+och\s+)?\s*)+)(?=\s+i\b)",
        clause,
        re.IGNORECASE,
    ):
        for label in _extract_labels_from_label_list(match.group(1)):
            if label not in out:
                out.append(label)
    return tuple(out)


def _extract_labels_from_label_list(text: str) -> tuple[str, ...]:
    out: list[str] = []
    for label in re.findall(r"\b(\d+\s*[a-z]?)\b", text, re.IGNORECASE):
        normalized = _label_norm(label)
        if normalized and normalized not in out:
            out.append(normalized)
    return tuple(out)


def _extract_replace_section_labels_from_clause(clause: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(
        r"(?:dels\s+att|att)\s+((?:(?!\bdels\s+att\b).)+?)\s+ska(?:ll)? ha följande lydelse",
        clause,
        re.IGNORECASE | re.DOTALL,
    ):
        segment = match.group(1)
        for section_match in re.finditer(
            r"(?:den\s+nya\s+)?((?:\d+\s*[a-z]?\s*(?:,|\s+och\s+)?\s*)+)\s*§{1,2}",
            segment,
            re.IGNORECASE,
        ):
            for label in _extract_labels_from_label_list(section_match.group(1)):
                if label not in out:
                    out.append(label)
    return tuple(out)


def _extract_repealed_section_labels_from_clause(clause: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(
        r"(?:dels\s+att|att)\s+((?:(?!\bdels\s+att\b).)+?)\s+ska(?:ll)? upphöra att gälla",
        clause,
        re.IGNORECASE | re.DOTALL,
    ):
        segment = match.group(1)
        for section_match in re.finditer(
            r"((?:\d+\s*[a-z]?\s*(?:,|\s+och\s+)?\s*)+)\s*§{1,2}",
            segment,
            re.IGNORECASE,
        ):
            for label in _extract_labels_from_label_list(section_match.group(1)):
                if label not in out:
                    out.append(label)
    return tuple(out)


def _extract_inserted_section_labels_from_clause(clause: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(
        r"(?:ny|nya)\s+paragra(?:f|fer),\s*(.+?)\s*§{1,2}",
        clause,
        re.IGNORECASE,
    ):
        for label in _extract_labels_from_label_list(match.group(1)):
            if label not in out:
                out.append(label)
    return tuple(out)


def _extract_inserted_appendix_labels_from_clause(clause: str) -> tuple[str, ...]:
    out: list[str] = []
    for match in re.finditer(r"ny\s+bilaga,\s*bilaga\s+(\d+[a-z]?|[A-Z])\b", clause, re.IGNORECASE):
        label = _label_norm(match.group(1))
        if label and label not in out:
            out.append(label)
    return tuple(out)


def _extract_inserted_point_labels_from_clause(clause: str) -> tuple[str, ...]:
    """Recover inserted transition-point labels that are named in the clause."""
    out: list[str] = []
    for match in re.finditer(
        r"ny\s+punkt(?:er)?(?:,|\s+)(.+?)\s+av\s+följande\s+lydelse",
        clause,
        re.IGNORECASE | re.DOTALL,
    ):
        for label in _extract_labels_from_label_list(match.group(1)):
            if label and label not in out:
                out.append(label)
    return tuple(out)


def _extract_section_renumber_pairs_from_clause(clause: str) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    for match in re.finditer(
        r"nuvarande\s+(.+?)\s*§{1,2}\s+ska(?:ll)? betecknas\s+(.+?)\s*§{1,2}",
        clause,
        re.IGNORECASE | re.DOTALL,
    ):
        sources = _extract_labels_from_label_list(match.group(1))
        destinations = _extract_labels_from_label_list(match.group(2))
        if len(sources) != len(destinations):
            continue
        for src, dst in zip(sources, destinations, strict=False):
            if src and dst and (src, dst) not in out:
                out.append((src, dst))
    return tuple(out)


def _extract_point_provisions_from_payload_text(payload_text: str) -> tuple[SEOfficialProvisionText, ...]:
    """Recover numbered transition-point payloads embedded after the clause marker."""
    matches = list(
        re.finditer(
            r"(?:^|(?<=[\.\n\r:;,]))\s*(?P<label>\d+\s*[a-z]?)\.\s+",
            payload_text,
            re.IGNORECASE,
        )
    )
    if not matches:
        return ()
    provisions: list[SEOfficialProvisionText] = []
    for index, match in enumerate(matches):
        label = _label_norm(match.group("label"))
        if not label:
            continue
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(payload_text)
        text = payload_text[start:end].strip()
        if text:
            provisions.append(SEOfficialProvisionText(label=label, text=text))
    return tuple(provisions)


def _infer_amended_act_sfs_id_from_clause(act: SEOfficialActText) -> str:
    """Recover a missing amended-act SFS ID from a uniquely named enacting clause."""

    if act.amended_act_sfs_id:
        return act.amended_act_sfs_id

    candidates: list[str] = []
    for match in _SFS_ID_RE.finditer(act.enacting_clause):
        candidate = match.group(1)
        if candidate and candidate != act.sfs_id and candidate not in candidates:
            candidates.append(candidate)
    if len(candidates) == 1:
        return candidates[0]
    return ""


def _marker_active(attrs: dict[str, str], as_of: str) -> bool:
    start = attrs.get("RestrictStartDate", "")
    end = attrs.get("RestrictEndDate", "")
    if start and as_of < start:
        return False
    if end and as_of >= end:
        return False
    return True


def extract_se_current_section_texts(payload: bytes | str | dict[str, Any], as_of: str) -> dict[str, str]:
    """Extract active raw section texts from Sweden RK current fulltext at one date."""
    document = _coerce_document(payload)
    fulltext_value = document.get("fulltext") or {}
    if isinstance(fulltext_value, dict):
        fulltext = str(fulltext_value.get("forfattningstext") or "")
    else:
        fulltext = str(fulltext_value or "")
    paragraphs = _split_se_current_raw_blocks(fulltext)
    sections: dict[str, str] = {}
    current_label = ""
    current_blocks: list[str] = []
    current_active = False
    pending_section_attrs: dict[str, str] = {}

    def _flush() -> None:
        if current_label and current_active:
            text = "\n\n".join(block for block in current_blocks if block.strip()).strip()
            if text:
                sections[current_label] = text

    for index, paragraph in enumerate(paragraphs):
        raw_block = "\n".join(line.rstrip() for line in paragraph).strip()
        cleaned_block, block_attrs, block_markers = _extract_markers(
            _join_wrapped_lines([line.strip() for line in paragraph])
        )
        if not cleaned_block and block_markers:
            pending_section_attrs.update(block_attrs)
            continue

        next_cleaned_block = ""
        if index + 1 < len(paragraphs):
            next_cleaned_block, _, _ = _extract_markers(
                _join_wrapped_lines([line.strip() for line in paragraphs[index + 1]])
            )

        section_match = _SECTION_RE.match(cleaned_block)
        if section_match:
            _flush()
            attrs = dict(pending_section_attrs)
            attrs.update(block_attrs)
            current_label = _label_norm(section_match.group("label"))
            current_blocks = []
            pending_section_attrs.clear()
            current_active = _marker_active(attrs, as_of)
            tail = _normalize_space(section_match.group("tail") or "")
            if current_active and tail:
                current_blocks.append(tail)
            continue

        pending_section_attrs.clear()
        if current_active and current_label and _looks_like_heading(cleaned_block, next_cleaned_block):
            _flush()
            current_label = ""
            current_blocks = []
            current_active = False
            continue

        if current_active and current_label and raw_block:
            current_blocks.append(raw_block)
    _flush()
    return sections


def canonicalize_se_table_section_text(text: str) -> str:
    """Canonicalize Sweden two-column table sections into a stable row signature."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$", "", normalized.strip())
    if "Uppgift lämnas av" not in normalized:
        return _normalize_space(normalized)

    def _finalize_row(
        rows: list[str],
        row_num: str,
        authority_parts: list[str],
        desc_parts: list[str],
    ) -> None:
        authority = _normalize_space(" ".join(part for part in authority_parts if part.strip()))
        desc = _normalize_space(" ".join(part for part in desc_parts if part.strip()))
        rows.append(f"{row_num}:{authority}|{desc}")

    rows: list[str] = []
    intro_parts: list[str] = []
    row_num = ""
    authority_parts: list[str] = []
    desc_parts: list[str] = []

    # If a numbered row was glued onto the previous paragraph, force a split.
    normalized = re.sub(r"(?<=\.)\s+(\d+\.\s+[A-ZÅÄÖ])", r"\n\n\1", normalized)
    paragraphs = _split_paragraphs_preserve_lines(normalized)
    current_style = any(
        any(("\t" in line or _TABLE_COLUMN_SPLIT_RE.search(line)) and _TABLE_ROW_START_RE.match(line) for line in para)
        for para in paragraphs
    )

    if current_style:
        for para in paragraphs:
            for raw_line in para:
                stripped = raw_line.strip()
                if not stripped:
                    continue
                if "Uppgift lämnas av" in stripped and "Uppgift lämnas om" in stripped:
                    continue
                if stripped in {"Uppgift lämnas av", "Uppgift lämnas om"}:
                    continue
                row_match = _TABLE_ROW_START_RE.match(stripped)
                if row_match:
                    if row_num:
                        _finalize_row(rows, row_num, authority_parts, desc_parts)
                    row_num = row_match.group(1)
                    authority_parts = []
                    desc_parts = []
                    remainder = row_match.group(2)
                    split = _TABLE_COLUMN_SPLIT_RE.split(remainder, maxsplit=1)
                    authority_parts.append(split[0].strip())
                    if len(split) > 1 and split[1].strip():
                        desc_parts.append(split[1].strip())
                    continue
                if not row_num:
                    intro_parts.append(stripped)
                    continue
                split = _TABLE_COLUMN_SPLIT_RE.split(raw_line.rstrip(), maxsplit=1)
                if len(split) > 1:
                    left = split[0].strip()
                    right = split[1].strip()
                    if left:
                        authority_parts.append(left)
                    if right:
                        desc_parts.append(right)
                else:
                    desc_parts.append(stripped)
        if row_num:
            _finalize_row(rows, row_num, authority_parts, desc_parts)
    else:
        for para in paragraphs:
            block = _join_wrapped_lines([line.strip() for line in para])
            if not block:
                continue
            if block in {"Uppgift lämnas av", "Uppgift lämnas om"}:
                continue
            if block == "Uppgift lämnas av Uppgift lämnas om":
                continue
            row_match = _TABLE_ROW_START_RE.match(block)
            if row_match:
                if row_num:
                    _finalize_row(rows, row_num, authority_parts, desc_parts)
                row_num = row_match.group(1)
                authority_parts = [row_match.group(2)]
                desc_parts = []
                continue
            if not row_num:
                intro_parts.append(block)
            else:
                desc_parts.append(block)
        if row_num:
            _finalize_row(rows, row_num, authority_parts, desc_parts)

    intro = _normalize_space(" ".join(intro_parts))
    intro = re.sub(r"\s+Uppgift lämnas av(?:\s+Uppgift lämnas om)?\s*$", "", intro).strip()
    if intro:
        return " || ".join([intro, *rows])
    return " || ".join(rows)


def se_official_act_text_to_dict(act_text: SEOfficialActText) -> dict[str, Any]:
    return asdict(act_text)


def se_official_clause_surface_to_dict(clause_surface: SEOfficialClauseSurface) -> dict[str, Any]:
    return asdict(clause_surface)


def se_official_payload_surface_to_dict(payload_surface: SEOfficialPayloadSurface) -> dict[str, Any]:
    return asdict(payload_surface)


def se_official_elaboration_to_dict(elaboration: SEOfficialElaboratedIntent) -> dict[str, Any]:
    return asdict(elaboration)


def se_official_effect_plan_to_dict(plan: SEOfficialEffectsPlan) -> dict[str, Any]:
    return asdict(plan)


def se_legal_operation_to_dict(op: LegalOperation) -> dict[str, Any]:
    patch = op.text_patch
    data = {
        "op_id": op.op_id,
        "sequence": op.sequence,
        "action": op.action.value,
        "target": {
            "path": [list(step) for step in op.target.path],
            "special": op.target.special.value if op.target.special else None,
        },
        "payload": op.payload.to_jsonable_dict() if op.payload is not None else None,
        "anchor": (
            {"path": [list(step) for step in op.anchor.path], "special": op.anchor.special}
            if op.anchor is not None
            else None
        ),
        "destination": (
            {"path": [list(step) for step in op.destination.path], "special": op.destination.special}
            if op.destination is not None
            else None
        ),
        "source": asdict(op.source) if op.source is not None else None,
        "applicability": [asdict(predicate) for predicate in op.applicability],
        "provenance_tags": list(op.provenance_tags),
        "group_id": op.group_id,
        "text_patch": (
            {
                "kind": str(patch.kind),
                "selector": {
                    "match_text": patch.selector.match_text,
                    "occurrence": patch.selector.occurrence,
                },
                "replacement": patch.replacement,
            }
            if patch is not None
            else None
        ),
    }
    return data


def se_legal_operation_from_dict(data: dict[str, Any]) -> LegalOperation:
    action_str = str(data.get("action") or "replace")
    action = StructuralAction(action_str)
    target_dict = data.get("target") or {}
    target = LegalAddress(
        path=tuple((str(kind), str(label)) for kind, label in target_dict.get("path", [])),
        special=target_dict.get("special"),
    )
    payload = irnode_from_dict(data["payload"]) if isinstance(data.get("payload"), dict) else None
    source = OperationSource(**data["source"]) if isinstance(data.get("source"), dict) else None
    anchor_dict = data.get("anchor") or {}
    anchor = (
        LegalAddress(
            path=tuple((str(kind), str(label)) for kind, label in anchor_dict.get("path", [])),
            special=anchor_dict.get("special"),
        )
        if isinstance(anchor_dict, dict) and anchor_dict.get("path")
        else None
    )
    destination_dict = data.get("destination") or {}
    destination = (
        LegalAddress(
            path=tuple((str(kind), str(label)) for kind, label in destination_dict.get("path", [])),
            special=destination_dict.get("special"),
        )
        if isinstance(destination_dict, dict) and destination_dict.get("path")
        else None
    )
    text_patch_data = data.get("text_patch")
    text_patch = None
    if isinstance(text_patch_data, dict):
        selector_raw = text_patch_data.get("selector")
        selector_data = selector_raw if isinstance(selector_raw, dict) else {}
        match_text = str(selector_data.get("match_text") or "")
        if match_text:
            kind = str(text_patch_data.get("kind") or "replace")
            replacement = text_patch_data.get("replacement")
            if kind == "delete":
                replacement = None
            text_patch = TextPatchSpec(
                kind=cast(Any, kind),
                selector=TextSelector(
                    match_text=match_text,
                    occurrence=int(selector_data.get("occurrence") or 0),
                ),
                replacement=replacement,
            )
    if text_patch is None:
        legacy_match = str(data.get("text_match") or "")
        if legacy_match:
            legacy_replacement = data.get("text_replacement")
            legacy_occurrence = int(data.get("text_occurrence") or 0)
            if action == StructuralAction.TEXT_REPEAL or legacy_replacement is None:
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.DELETE,
                    selector=TextSelector(
                        match_text=legacy_match,
                        occurrence=legacy_occurrence,
                    ),
                )
            else:
                text_patch = TextPatchSpec(
                    kind=TextPatchKindEnum.REPLACE,
                    selector=TextSelector(
                        match_text=legacy_match,
                        occurrence=legacy_occurrence,
                    ),
                    replacement=str(legacy_replacement),
                )
    return LegalOperation(
        op_id=str(data.get("op_id") or ""),
        sequence=int(data.get("sequence") or 0),
        action=action,
        target=target,
        payload=payload,
        anchor=anchor,
        destination=destination,
        source=source,
        provenance_tags=tuple(str(note) for note in data.get("provenance_tags", [])),
        text_patch=text_patch,
        group_id=data.get("group_id"),
    )


def _extract_markers(text: str) -> tuple[str, dict[str, str], list[str]]:
    attrs: dict[str, str] = {}
    markers: list[str] = []
    for match in _MARKER_RE.finditer(text):
        phrase = _normalize_space(match.group("phrase"))
        kind = match.group("kind")
        date = match.group("date")
        markers.append(f"{phrase} {date}".strip())
        if kind == "I":
            attrs["RestrictStartDate"] = date
        elif kind == "U":
            attrs["RestrictEndDate"] = date
    cleaned = _normalize_space(_MARKER_RE.sub("", text))
    return cleaned, attrs, markers


def _label_norm(label: str) -> str:
    return _normalize_space(label.replace("§", "")).replace(" ", "")


def _build_rk_urls(sfs_id: str) -> SESourceUrls:
    encoded = quote(sfs_id, safe="")
    doc_token = sfs_id.replace(":", "")
    return SESourceUrls(
        official_sfs_doc_url=f"https://svenskforfattningssamling.se/doc/{doc_token}.html",
        rk_sfst_url=f"https://rkrattsbaser.gov.se/sfst?bet={sfs_id}",
        rk_beta_base_url=f"https://beta.rkrattsbaser.gov.se/sfs/item?bet={encoded}&tab=grundforfattning",
        rk_beta_current_url=f"https://beta.rkrattsbaser.gov.se/sfs/item?bet={encoded}&tab=forfattningstext",
        rk_sfsr_url=f"https://rkrattsbaser.gov.se/sfsr?bet={sfs_id}",
    )


def se_official_doc_url(sfs_id: str) -> str:
    """Return the official Svensk författningssamling document page URL."""
    return _build_rk_urls(sfs_id).official_sfs_doc_url or ""


def parse_se_official_pdf_url(doc_html: bytes | str, doc_url: str) -> Optional[str]:
    """Extract the official PDF URL from an SFS document page HTML blob."""
    if isinstance(doc_html, bytes):
        doc_html = doc_html.decode("utf-8", errors="replace")
    match = _PDF_HREF_RE.search(doc_html)
    if not match:
        return None
    return urljoin(doc_url, match.group(1))


def enrich_se_source_record_with_doc_page(
    source_record: SESourceRecord,
    doc_html: bytes | str,
    doc_url: Optional[str] = None,
) -> SESourceRecord:
    """Attach official SFS page-derived URLs to an existing source record."""
    resolved_doc_url = (
        doc_url or source_record.source_urls.official_sfs_doc_url or se_official_doc_url(source_record.sfs_id)
    )
    pdf_url = parse_se_official_pdf_url(doc_html, resolved_doc_url)
    return replace(
        source_record,
        source_urls=replace(
            source_record.source_urls,
            official_sfs_doc_url=resolved_doc_url,
            official_sfs_pdf_url=pdf_url,
        ),
    )


def se_pdf_bytes_to_text(
    pdf_bytes: bytes,
    *,
    pdftotext_bin: str = "pdftotext",
    timeout: int = 30,
) -> Optional[str]:
    """Extract plain text from PDF bytes using `pdftotext` subprocess."""
    tmp_path: Optional[str] = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
            handle.write(pdf_bytes)
            tmp_path = handle.name
        result = subprocess.run(
            [pdftotext_bin, tmp_path, "-"],
            capture_output=True,
            timeout=timeout,
        )
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8", errors="replace")
    except FileNotFoundError:
        return None
    except Exception:
        return None
    finally:
        if tmp_path:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass


def _parse_parliamentary_links(sfs_id: str, raw_text: str) -> tuple[SEParliamentaryPackageLink, ...]:
    raw_text = _normalize_space(raw_text)
    if not raw_text:
        return ()
    prop_match = _PROP_RE.search(raw_text)
    bet_match = _BET_RE.search(raw_text)
    rskr_match = _RSKR_RE.search(raw_text)
    if not any((prop_match, bet_match, rskr_match)):
        return ()
    return (
        SEParliamentaryPackageLink(
            sfs_id=sfs_id,
            prop_id=_normalize_space(prop_match.group(1)) if prop_match else "",
            bet_id=_normalize_space(bet_match.group(1)) if bet_match else "",
            rskr_id=_normalize_space(rskr_match.group(1)) if rskr_match else "",
        ),
    )


def _parse_amended_through(raw_text: Any) -> str:
    if not isinstance(raw_text, str):
        return ""
    match = _SFS_ID_RE.search(raw_text)
    return match.group(1) if match else ""


def _classify_source_confidence(document: dict[str, Any]) -> SESourceConfidence:
    fulltext = document.get("fulltext") or {}
    if isinstance(fulltext, dict):
        fulltext_text = str(fulltext.get("forfattningstext") or "")
    else:
        fulltext_text = str(fulltext or "")
    has_fulltext = bool(fulltext_text.strip())
    has_register_rows = bool(document.get("andringsforfattningar"))
    if has_fulltext and has_register_rows:
        return SESourceConfidence.CURRENT_TEXT_PLUS_REGISTER
    if has_fulltext:
        return SESourceConfidence.CURRENT_TEXT_ONLY
    return SESourceConfidence.OFFICIAL_PDF_ONLY


def parse_se_amendment_register(payload: bytes | str | dict[str, Any]) -> list[SEAmendmentRegisterEntry]:
    """Parse Sweden amendment-register rows from an RK-style JSON document."""
    document = _coerce_document(payload)
    base_sfs_id = str(document.get("beteckning") or "")
    rows = document.get("andringsforfattningar") or []
    if not isinstance(rows, list):
        return []

    entries: list[SEAmendmentRegisterEntry] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope_text = _normalize_space(str(row.get("anteckningar") or ""))
        preparatory_works = _normalize_space(str(row.get("forarbeten") or ""))
        raw_row_text = _normalize_space(
            " | ".join(
                part
                for part in (
                    str(row.get("beteckning") or ""),
                    str(row.get("rubrik") or ""),
                    scope_text,
                    preparatory_works,
                )
                if part
            )
        )
        entries.append(
            SEAmendmentRegisterEntry(
                base_sfs_id=base_sfs_id,
                amending_sfs_id=str(row.get("beteckning") or ""),
                amending_title=_normalize_space(str(row.get("rubrik") or "")),
                scope_text=scope_text,
                effective_date=_date_only(row.get("ikraftDateTime")),
                has_overgangsbestammelser=bool(row.get("ikraftOvergangsbestammelse")),
                preparatory_works=preparatory_works,
                parliamentary_links=_parse_parliamentary_links(
                    str(row.get("beteckning") or ""),
                    preparatory_works,
                ),
                raw_row_text=raw_row_text,
            )
        )
    return entries


def parse_se_source_record(payload: bytes | str | dict[str, Any]) -> SESourceRecord:
    """Build a typed Sweden SourceRecord from an RK-style JSON document."""
    document = _coerce_document(payload)
    fulltext = document.get("fulltext") or {}
    if not isinstance(fulltext, dict):
        fulltext = {"forfattningstext": str(fulltext)}
    organisation = document.get("organisation") or {}
    if not isinstance(organisation, dict):
        organisation = {}
    register = document.get("register") or {}
    if not isinstance(register, dict):
        register = {}
    title = _normalize_space(str(document.get("rubrik") or ""))
    sfs_id = str(document.get("beteckning") or "")
    amendment_register = tuple(parse_se_amendment_register(document))

    marker_values: list[str] = []
    effective_date = _date_only(document.get("ikraftDateTime"))
    if effective_date:
        marker_values.append(f"Ikraft {effective_date}")
    if bool(document.get("ikraftOvergangsbestammelse")):
        marker_values.append("Ikraft via overgangsbestammelse")
    repeal_date = _date_only(document.get("upphavdDateTime"))
    if repeal_date:
        marker_values.append(f"Upphavd {repeal_date}")

    return SESourceRecord(
        sfs_id=sfs_id,
        title=title,
        act_type=_normalize_space(str(document.get("forfattningstypNamn") or "")).lower(),
        department=_normalize_space(str(organisation.get("namnOchEnhet") or organisation.get("namn") or "")),
        issued_date=_date_only(fulltext.get("utfardadDateTime")),
        published_date=_date_only(document.get("publiceradDateTime")),
        effective_markers=tuple(marker_values),
        amended_through_sfs=_parse_amended_through(fulltext.get("andringInford")),
        repealed=bool(document.get("upphavdDateTime") or fulltext.get("upphavdGenom")),
        repealed_by_sfs=_parse_amended_through(fulltext.get("upphavdGenom")),
        repeal_date=repeal_date,
        source_urls=_build_rk_urls(sfs_id),
        source_text_kind="current_consolidated_html" if fulltext.get("forfattningstext") else "",
        source_confidence=_classify_source_confidence(document),
        parliamentary_links=_parse_parliamentary_links(
            sfs_id,
            str(register.get("forarbeten") or ""),
        ),
        amendment_register=amendment_register,
    )


def _append_subsection(section: _SEMutableNode, text: str) -> _SEMutableNode:
    subsection_count = sum(1 for child in section.children if child.kind == "subsection")
    node = _SEMutableNode(kind="subsection", label=str(subsection_count + 1), text=text)
    section.children.append(node)
    return node


def _last_subsection(section: _SEMutableNode) -> Optional[_SEMutableNode]:
    for child in reversed(section.children):
        if child.kind == "subsection":
            return child
    return None


def _append_item(section: _SEMutableNode, label: str, text: str) -> None:
    subsection = _last_subsection(section)
    if subsection is None:
        subsection = _append_subsection(section, "")
    subsection.children.append(_SEMutableNode(kind="item", label=label, text=text))


def _container_last_subsection(container: _SEMutableNode) -> Optional[_SEMutableNode]:
    for child in reversed(container.children):
        if child.kind == "subsection":
            return child
    return None


def _append_container_subsection(container: _SEMutableNode, text: str) -> _SEMutableNode:
    subsection_count = sum(1 for child in container.children if child.kind == "subsection")
    node = _SEMutableNode(kind="subsection", label=str(subsection_count + 1), text=text)
    container.children.append(node)
    return node


def _append_container_item(container: _SEMutableNode, label: str, text: str) -> None:
    subsection = _container_last_subsection(container)
    if subsection is None:
        subsection = _append_container_subsection(container, "")
    subsection.children.append(_SEMutableNode(kind="item", label=label, text=text))


def _replace_se_text_in_node(node: IRNode, old_text: str, new_text: str) -> tuple[IRNode, bool]:
    """Replace one word/phrase inside a Sweden subtree, preserving structure."""
    changed = False
    new_node_text = node.text
    if node.text:
        replaced_text = re.sub(
            rf"(?<!\w){re.escape(old_text)}(?!\w)",
            new_text,
            node.text,
        )
        if replaced_text != node.text:
            new_node_text = replaced_text
            changed = True
    new_children: list[IRNode] = []
    for child in node.children:
        replaced_child, child_changed = _replace_se_text_in_node(child, old_text, new_text)
        if child_changed:
            changed = True
        new_children.append(replaced_child)
    if not changed:
        return node, False
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=new_node_text,
        attrs=dict(node.attrs),
        children=tuple(new_children),
    ), True


def _looks_like_heading(block: str, next_block: str) -> bool:
    if block.endswith((".", ":", ";")):
        return False
    if _ITEM_RE.match(block):
        return False
    if _SFS_ID_RE.fullmatch(block):
        return False
    return bool(
        _SECTION_RE.match(next_block)
        or _CHAPTER_RE.match(next_block)
        or _APPENDIX_RE.match(next_block)
        or next_block.lower() == "overgångsbestämmelser"
    )


def _clean_official_section_tail(raw_line: str, tail: str) -> str:
    cleaned_tail = _normalize_space(tail)
    if not cleaned_tail:
        return ""
    if re.match(r"^\d+\s*[a-z]?\s*§\d+\s+\S", raw_line, re.IGNORECASE):
        stripped = re.sub(r"^\d+\s+", "", cleaned_tail, count=1)
        if stripped:
            cleaned_tail = stripped
    return cleaned_tail


def _is_bare_official_section_citation(text: str) -> bool:
    return bool(re.fullmatch(r"\d{4}:\d+\.?", text))


def parse_se_statute(payload: bytes | str | dict[str, Any], statute_id: Optional[str] = None) -> IRStatute:
    """Parse Sweden current-text JSON into the shared IRStatute tree.

    The parser is intentionally deterministic and conservative:

    - consumes RK-style `fulltext.forfattningstext`
    - preserves chapter/section/subsection/item structure
    - preserves inline temporal markers in node attrs
    - groups `Övergångsbestämmelser` into a dedicated transition container
    """
    document = _coerce_document(payload)
    source_record = parse_se_source_record(document)
    fulltext = document.get("fulltext") or {}
    if isinstance(fulltext, dict):
        fulltext_text = str(fulltext.get("forfattningstext") or "")
    else:
        fulltext_text = str(fulltext or "")
    blocks = _text_blocks(fulltext_text)

    body_children: list[_SEMutableNode] = []
    supplements: list[_SEMutableNode] = []
    current_chapter: Optional[_SEMutableNode] = None
    current_section: Optional[_SEMutableNode] = None
    current_transition: Optional[_SEMutableNode] = None
    current_schedule: Optional[_SEMutableNode] = None
    pending_section_attrs: dict[str, str] = {}
    pending_section_markers: list[str] = []

    def active_container() -> list[_SEMutableNode]:
        if current_schedule is not None:
            return current_schedule.children
        if current_transition is not None:
            return current_transition.children
        if current_chapter is not None:
            return current_chapter.children
        return body_children

    def parent_container() -> list[_SEMutableNode]:
        if current_schedule is not None:
            return current_schedule.children
        if current_chapter is not None:
            return current_chapter.children
        return body_children

    for index, block in enumerate(blocks):
        cleaned_block, block_attrs, block_markers = _extract_markers(block)
        if not cleaned_block and block_markers:
            pending_section_attrs.update(block_attrs)
            pending_section_markers.extend(block_markers)
            continue

        next_block = blocks[index + 1] if index + 1 < len(blocks) else ""
        cleaned_next_block, _, _ = _extract_markers(next_block)

        appendix_match = _APPENDIX_RE.match(cleaned_block)
        if appendix_match:
            current_section = None
            current_transition = None
            current_chapter = None
            current_schedule = _SEMutableNode(
                kind="appendix",
                label=appendix_match.group("label") or None,
                attrs=dict(block_attrs),
            )
            title = _normalize_space(appendix_match.group("title") or "")
            if title:
                current_schedule.children.append(_SEMutableNode(kind="heading", text=title, attrs=dict(block_attrs)))
            supplements.append(current_schedule)
            continue

        if cleaned_block.lower() == "övergångsbestämmelser":
            current_section = None
            current_schedule = None
            current_transition = _SEMutableNode(
                kind="part",
                label="overgangsbestammelser",
                attrs={"role": "transition"},
            )
            current_transition.children.append(_SEMutableNode(kind="heading", text=block, attrs=dict(block_attrs)))
            parent_container().append(current_transition)
            continue

        chapter_match = _CHAPTER_RE.match(cleaned_block)
        if chapter_match and current_schedule is None and current_transition is None:
            current_section = None
            current_chapter = _SEMutableNode(
                kind="chapter",
                label=_label_norm(chapter_match.group("label")),
                attrs=dict(block_attrs),
            )
            title = _normalize_space(chapter_match.group("title") or "")
            if title:
                current_chapter.children.append(_SEMutableNode(kind="heading", text=title, attrs=dict(block_attrs)))
            body_children.append(current_chapter)
            continue

        section_match = _SECTION_RE.match(cleaned_block)
        if section_match:
            attrs: dict[str, object] = dict(pending_section_attrs)
            attrs.update(block_attrs)
            markers = [*pending_section_markers, *block_markers]
            if markers:
                attrs["TemporalMarkers"] = " | ".join(markers)
            current_section = _SEMutableNode(
                kind="section",
                label=_label_norm(section_match.group("label")),
                attrs=attrs,
            )
            active_container().append(current_section)
            pending_section_attrs.clear()
            pending_section_markers.clear()
            section_text = _normalize_space(section_match.group("tail") or "")
            if section_text:
                _append_subsection(current_section, section_text)
            continue

        pending_section_attrs.clear()
        pending_section_markers.clear()

        item_match = _ITEM_RE.match(cleaned_block)
        if item_match:
            if current_section is not None:
                _append_item(current_section, _label_norm(item_match.group("label")), item_match.group("text"))
                continue
            if current_transition is not None:
                _append_container_item(
                    current_transition,
                    _label_norm(item_match.group("label")),
                    item_match.group("text"),
                )
                continue
            if current_schedule is not None:
                _append_container_item(
                    current_schedule,
                    _label_norm(item_match.group("label")),
                    item_match.group("text"),
                )
                continue
            continue

        if current_section is not None and not _looks_like_heading(cleaned_block, cleaned_next_block):
            _append_subsection(current_section, cleaned_block)
            continue

        if current_transition is not None:
            _append_container_subsection(current_transition, cleaned_block)
            continue

        if current_schedule is not None:
            _append_container_subsection(current_schedule, cleaned_block)
            continue

        current_section = None
        active_container().append(_SEMutableNode(kind="heading", text=cleaned_block, attrs=dict(block_attrs)))

    metadata = {
        "jurisdiction": "se",
        "source_confidence": source_record.source_confidence.value,
        "source_text_kind": source_record.source_text_kind,
        "amended_through_sfs": source_record.amended_through_sfs,
        "effective_markers": list(source_record.effective_markers),
    }
    return IRStatute(
        statute_id=statute_id or source_record.sfs_id,
        title=source_record.title,
        body=IRNode(kind=IRNodeKind.BODY, children=tuple(child.to_irnode() for child in body_children)),
        supplements=tuple(supplement.to_irnode() for supplement in supplements),
        metadata=metadata,
    )


def parse_se_official_act_text(text: str, sfs_id: str) -> SEOfficialActText:
    """Parse cleaned official SFS PDF text into a structured act surface."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]

    while lines and not lines[0]:
        lines.pop(0)
    if lines and lines[0].lower() == "svensk författningssamling":
        lines.pop(0)

    title_lines: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue
        if line.lower() == "publicerad" or line.lower().startswith("utfärdad den "):
            break
        title_lines.append(line)
        i += 1

    title = _normalize_space(" ".join(title_lines))
    act_type = _normalize_space(title_lines[0]).lower() if title_lines else ""
    amended_act_match = _TITLE_BASE_SFS_RE.search(title) if _AMENDING_TITLE_RE.search(title) else None
    amended_act_sfs_id = amended_act_match.group(1) if amended_act_match else ""
    is_amending_act = bool(amended_act_sfs_id) or bool(_AMENDING_TITLE_RE.search(title))

    published_date = ""
    issued_date = ""
    if i < len(lines) and lines[i].lower() == "publicerad":
        if i + 1 < len(lines):
            published_date = _parse_swedish_date_text(lines[i + 1])
        i += 2
    while i < len(lines) and not lines[i]:
        i += 1
    if i < len(lines) and lines[i].lower().startswith("utfärdad den "):
        issued_date = _parse_swedish_date_text(lines[i])
        i += 1

    body_lines = lines[i:]
    signoff_index = next(
        (idx for idx, line in enumerate(body_lines) if line.lower() == "på regeringens vägnar"),
        len(body_lines),
    )
    main_lines = body_lines[:signoff_index]
    trailing_lines = body_lines[signoff_index + 1 :] if signoff_index < len(body_lines) else []

    main_lines, footnotes = _extract_footnotes(main_lines)

    enacting_clause_lines: list[str] = []
    provision_lines: list[str] = []
    effective_lines: list[str] = []
    seen_section = False
    for line in main_lines:
        if not line:
            if not seen_section:
                if enacting_clause_lines and enacting_clause_lines[-1]:
                    enacting_clause_lines.append("")
            elif provision_lines and provision_lines[-1]:
                provision_lines.append("")
            continue
        if _SECTION_RE.match(line):
            seen_section = True
        if seen_section:
            if line.lower().startswith("denna "):
                effective_lines.append(line)
            elif effective_lines:
                effective_lines.append(line)
            else:
                provision_lines.append(line)
        else:
            enacting_clause_lines.append(line)

    # Some acts insert a heading immediately before the first inserted section.
    # In the PDF text surface, that heading can appear after the enacting clause
    # and before the first section start, so peel it back out of the clause block.
    enacting_clause_probe = " ".join(line.strip().lower() for line in enacting_clause_lines if line.strip())
    if "ny rubrik" in enacting_clause_probe and enacting_clause_lines and provision_lines:
        split_index = -1
        for idx in range(len(enacting_clause_lines) - 1, -1, -1):
            if not enacting_clause_lines[idx]:
                split_index = idx
                break
        if split_index >= 0:
            trailing_heading_lines = [line for line in enacting_clause_lines[split_index + 1 :] if line]
            if trailing_heading_lines:
                enacting_clause_lines = enacting_clause_lines[:split_index]
                provision_lines = trailing_heading_lines + provision_lines

    provisions: list[SEOfficialProvisionText] = []
    inserted_headings: list[SEOfficialHeadingText] = []
    current_label: Optional[str] = None
    current_lines: list[str] = []

    def _flush_current() -> None:
        if current_label is not None:
            provisions.append(
                SEOfficialProvisionText(
                    label=current_label,
                    text=_join_preserving_paragraphs(current_lines),
                )
            )

    def _next_meaningful_line(start_index: int) -> str:
        for candidate in provision_lines[start_index + 1 :]:
            if not candidate:
                continue
            candidate_match = _SECTION_RE.match(candidate)
            if candidate_match:
                candidate_tail = _clean_official_section_tail(candidate, candidate_match.group("tail") or "")
                if _is_bare_official_section_citation(candidate_tail):
                    continue
            return candidate
        return ""

    for index, line in enumerate(provision_lines):
        next_line = _next_meaningful_line(index)
        match = _SECTION_RE.match(line)
        if match:
            next_label = _label_norm(match.group("label"))
            tail = _clean_official_section_tail(line, match.group("tail") or "")
            if _is_bare_official_section_citation(tail):
                continue
            if current_label is not None and next_label == current_label:
                current_lines.append(line)
                continue
            _flush_current()
            current_label = next_label
            current_lines = [tail] if tail else []
            continue
        if line and _looks_like_heading(line, next_line) and _SECTION_RE.match(next_line):
            if current_label is not None:
                _flush_current()
                current_label = None
                current_lines = []
            _m_next = _SECTION_RE.match(next_line)
            next_label = _label_norm(_m_next.group("label")) if _m_next else ""
            inserted_headings.append(SEOfficialHeadingText(before_label=next_label, text=line))
            continue
        if current_label is not None:
            current_lines.append(line)
    _flush_current()

    if not effective_lines:
        recovered_effective = next(
            (line for line in footnotes if line.lower().startswith("denna ")),
            "",
        )
        if recovered_effective:
            effective_lines.append(recovered_effective)

    appendix_start = next(
        (idx for idx, line in enumerate(trailing_lines) if _APPENDIX_RE.match(line)),
        len(trailing_lines),
    )
    signatories = tuple(line for line in trailing_lines[:appendix_start] if line and not _FOOTNOTE_LINE_RE.match(line))
    appendices = _parse_se_official_appendices(trailing_lines[appendix_start:])
    if not appendices:
        appendices = _parse_embedded_se_official_appendices(main_lines)
    main_text = _join_preserving_paragraphs(main_lines)
    if (
        not provisions
        and not inserted_headings
        and not appendices
        and not any(_SECTION_RE.match(line) for line in main_lines)
    ):
        marker_matches = list(
            re.finditer(
                r"\b(?:ska|skall)\s+ha\s+följande\s+lydelse\.\s*|\bav\s+följande\s+lydelse\.\s*",
                main_text,
                re.IGNORECASE,
            )
        )
        marker_match = marker_matches[-1] if marker_matches else None
        if marker_match is not None:
            payload_text = main_text[marker_match.end() :]
            payload_stop = len(payload_text)
            for sentinel in (" Denna ", " På regeringens vägnar "):
                sentinel_index = payload_text.find(sentinel)
                if sentinel_index >= 0:
                    payload_stop = min(payload_stop, sentinel_index)
            payload_text = payload_text[:payload_stop].strip()
            point_provisions = _extract_point_provisions_from_payload_text(payload_text) if payload_text else ()
            if point_provisions:
                provisions = list(point_provisions)
            else:
                # Some older full-text replacement clauses do not expose a structured
                # section payload, but the target label is still recoverable from the
                # amendment clause itself. Prefer the dedicated replacement-label
                # extractor so we do not mistake citation noise for the target.
                replace_labels = _extract_replace_section_labels_from_clause(
                    _join_preserving_paragraphs(enacting_clause_lines)
                )
                if len(replace_labels) == 1:
                    provisions = [
                        SEOfficialProvisionText(
                            label=replace_labels[0],
                            text=payload_text,
                        )
                    ]
                else:
                    label = _extract_section_labels_from_clause(_join_preserving_paragraphs(enacting_clause_lines))
                    if len(label) == 1:
                        provisions = [
                            SEOfficialProvisionText(
                                label=label[0],
                                text=payload_text,
                            )
                        ]
    enacting_clause = _join_preserving_paragraphs(enacting_clause_lines)
    return SEOfficialActText(
        sfs_id=sfs_id,
        title=title,
        act_type=act_type,
        amended_act_sfs_id=amended_act_sfs_id,
        is_amending_act=is_amending_act,
        published_date=published_date,
        issued_date=issued_date,
        enacting_clause=enacting_clause,
        effective_clause=_join_preserving_paragraphs(effective_lines),
        affected_section_labels=_extract_section_labels_from_clause(enacting_clause),
        provisions=tuple(provisions),
        inserted_headings=tuple(inserted_headings),
        appendices=appendices,
        signatories=signatories,
        footnotes=footnotes,
    )


def _coerce_official_act(payload: bytes | str | dict[str, Any]) -> SEOfficialActText:
    document = _coerce_document(payload)
    provisions = tuple(
        SEOfficialProvisionText(
            label=_label_norm(str(provision.get("label") or "")),
            text=str(provision.get("text") or ""),
        )
        for provision in document.get("provisions", [])
        if isinstance(provision, dict) and str(provision.get("label") or "").strip()
    )
    inserted_headings = tuple(
        SEOfficialHeadingText(
            before_label=_label_norm(str(heading.get("before_label") or "")),
            text=str(heading.get("text") or ""),
        )
        for heading in document.get("inserted_headings", [])
        if isinstance(heading, dict) and str(heading.get("before_label") or "").strip()
    )
    appendices = tuple(
        SEOfficialAppendixText(
            label=_label_norm(str(appendix.get("label") or "")),
            title=_normalize_space(str(appendix.get("title") or "")),
            text=str(appendix.get("text") or ""),
        )
        for appendix in document.get("appendices", [])
        if isinstance(appendix, dict)
    )
    return SEOfficialActText(
        sfs_id=str(document.get("sfs_id") or ""),
        title=_normalize_space(str(document.get("title") or "")),
        act_type=_normalize_space(str(document.get("act_type") or "")).lower(),
        amended_act_sfs_id=str(document.get("amended_act_sfs_id") or ""),
        is_amending_act=bool(document.get("is_amending_act")),
        published_date=str(document.get("published_date") or ""),
        issued_date=str(document.get("issued_date") or ""),
        enacting_clause=str(document.get("enacting_clause") or ""),
        effective_clause=str(document.get("effective_clause") or ""),
        affected_section_labels=tuple(
            _label_norm(str(label)) for label in document.get("affected_section_labels", []) if str(label).strip()
        ),
        provisions=provisions,
        inserted_headings=inserted_headings,
        appendices=appendices,
        signatories=tuple(str(value) for value in document.get("signatories", []) if str(value).strip()),
        footnotes=tuple(str(value) for value in document.get("footnotes", []) if str(value).strip()),
    )


def _build_se_official_clause_surface(act: SEOfficialActText) -> SEOfficialClauseSurface:
    enacting_clause = act.enacting_clause
    amended_act_sfs_id = _infer_amended_act_sfs_id_from_clause(act)
    repealed_labels = _extract_repealed_section_labels_from_clause(enacting_clause)
    renumber_pairs = tuple(
        sorted(
            _extract_section_renumber_pairs_from_clause(enacting_clause),
            key=lambda pair: _se_label_sort_key(pair[0]),
            reverse=True,
        )
    )
    extracted_replace_labels = _extract_replace_section_labels_from_clause(enacting_clause)
    inserted_section_label_list: list[str] = []
    for label in _extract_inserted_section_labels_from_clause(enacting_clause):
        if label:
            inserted_section_label_list.append(label)
    for label in _extract_inserted_point_labels_from_clause(enacting_clause):
        if label:
            inserted_section_label_list.append(label)
    inserted_section_labels = cast(
        tuple[str, ...],
        tuple(sorted(dict.fromkeys(inserted_section_label_list), key=_se_label_sort_key)),
    )
    renumber_destinations = {_label_norm(destination) for _, destination in renumber_pairs if _label_norm(destination)}
    if extracted_replace_labels:
        replace_labels = extracted_replace_labels
    else:
        replace_labels = tuple(
            label
            for label in (_label_norm(raw_label) for raw_label in act.affected_section_labels)
            if label
            and label not in repealed_labels
            and label not in inserted_section_labels
            and label not in renumber_destinations
        )
    return SEOfficialClauseSurface(
        sfs_id=act.sfs_id,
        title=act.title,
        amended_act_sfs_id=amended_act_sfs_id,
        is_amending_act=act.is_amending_act,
        enacting_clause=enacting_clause,
        effective_clause=act.effective_clause,
        affected_section_labels=act.affected_section_labels,
        repealed_section_labels=repealed_labels,
        renumber_pairs=renumber_pairs,
        replace_section_labels=replace_labels,
        inserted_section_labels=inserted_section_labels,
        inserted_appendix_labels=_extract_inserted_appendix_labels_from_clause(enacting_clause),
        effective_date=_parse_swedish_date_text(act.effective_clause) if act.effective_clause else "",
    )


def _build_se_official_payload_surface(act: SEOfficialActText) -> SEOfficialPayloadSurface:
    """Typed payload surface for official-act body material.

    Clause extraction and payload shaping are intentionally separate seams so
    the lowering path can stay explicit as Sweden grows.
    """

    return SEOfficialPayloadSurface(
        provisions=act.provisions,
        inserted_headings=act.inserted_headings,
        appendices=act.appendices,
    )


def _build_se_official_elaboration(act: SEOfficialActText) -> SEOfficialElaboratedIntent:
    """Combine the clause and payload waists into a typed elaboration object."""
    clause_surface = _build_se_official_clause_surface(act)
    payload_surface = _build_se_official_payload_surface(act)
    provision_labels = tuple(
        _label_norm(provision.label) for provision in payload_surface.provisions if _label_norm(provision.label)
    )
    supported_section_labels_set: set[str] = set(clause_surface.replace_section_labels)
    supported_section_labels_set.update(clause_surface.inserted_section_labels)
    supported_section_labels = cast(
        tuple[str, ...],
        tuple(sorted(supported_section_labels_set, key=_se_label_sort_key)),
    )
    inserted_heading_labels = tuple(
        _label_norm(heading.before_label)
        for heading in payload_surface.inserted_headings
        if _label_norm(heading.before_label)
    )
    appendix_labels = tuple(
        _label_norm(appendix.label) for appendix in payload_surface.appendices if _label_norm(appendix.label)
    )
    return SEOfficialElaboratedIntent(
        clause_surface=clause_surface,
        payload_surface=payload_surface,
        issued_date=act.issued_date,
        provision_labels=provision_labels,
        supported_section_labels=supported_section_labels,
        inserted_heading_labels=inserted_heading_labels,
        appendix_labels=appendix_labels,
    )


def _build_se_official_effect_plan_items(intent: SEOfficialElaboratedIntent) -> tuple[SEOfficialEffectPlanItem, ...]:
    surface = intent.clause_surface
    payload_surface = intent.payload_surface
    items: list[SEOfficialEffectPlanItem] = []
    for label in surface.repealed_section_labels:
        items.append(SEOfficialEffectPlanItem(kind="repeal", target_label=label))
    for source_label, destination_label in surface.renumber_pairs:
        items.append(
            SEOfficialEffectPlanItem(
                kind="renumber",
                target_label=source_label,
                destination_label=destination_label,
            )
        )
    replace_labels = set(surface.replace_section_labels)
    inserted_section_labels = set(surface.inserted_section_labels)
    if payload_surface.provisions:
        replace_labels.update(
            label
            for label in (_label_norm(provision.label) for provision in payload_surface.provisions)
            if label and label not in inserted_section_labels
        )
    for provision in payload_surface.provisions:
        label = _label_norm(provision.label)
        if not label:
            continue
        items.append(
            SEOfficialEffectPlanItem(
                kind="replace_section" if label in replace_labels else "insert_section",
                target_label=label,
                payload_label=label,
            )
        )
    for heading in payload_surface.inserted_headings:
        label = _label_norm(heading.before_label)
        if not label or label not in inserted_section_labels:
            continue
        items.append(
            SEOfficialEffectPlanItem(
                kind="insert_heading",
                target_label=label,
                payload_label=label,
            )
        )
    unlabeled_appendix_count = sum(1 for appendix in payload_surface.appendices if not _label_norm(appendix.label))
    inferred_appendix_label = ""
    if unlabeled_appendix_count == 1 and len(surface.inserted_appendix_labels) == 1:
        inferred_appendix_label = surface.inserted_appendix_labels[0]
    for appendix in payload_surface.appendices:
        label = _label_norm(appendix.label)
        if not label and inferred_appendix_label:
            label = inferred_appendix_label
        if not label:
            continue
        items.append(SEOfficialEffectPlanItem(kind="insert_appendix", target_label=label, payload_label=label))
    if not items and surface.affected_section_labels:
        word_substitution = _extract_se_official_word_substitution_pair(surface.enacting_clause)
        if word_substitution is not None:
            old_text, new_text = word_substitution
            for label in surface.affected_section_labels:
                normalized_label = _label_norm(label)
                if not normalized_label:
                    continue
                items.append(
                    SEOfficialEffectPlanItem(
                        kind="text_replace",
                        target_label=normalized_label,
                        text_patch=TextPatchSpec(
                            kind=TextPatchKindEnum.REPLACE,
                            selector=TextSelector(match_text=old_text),
                            replacement=new_text,
                        ),
                    )
                )
    return tuple(items)


def _classify_se_official_effects_plan_frontier(
    intent: SEOfficialElaboratedIntent, planned_items: tuple[SEOfficialEffectPlanItem, ...]
) -> str:
    surface = intent.clause_surface
    payload_surface = intent.payload_surface
    if not surface.is_amending_act:
        return "non_amending"
    if not surface.amended_act_sfs_id:
        return "missing_base_act"
    if planned_items:
        return "supported"
    if payload_surface.provisions or payload_surface.inserted_headings or payload_surface.appendices:
        return "empty_effect_plan_with_payload"
    if (
        surface.repealed_section_labels
        or surface.renumber_pairs
        or surface.replace_section_labels
        or surface.inserted_section_labels
        or surface.inserted_appendix_labels
        or "bilaga" in _normalize_space(surface.enacting_clause).lower()
    ):
        return "empty_effect_plan_with_clause_targets"
    return "empty_effect_plan_without_targets"


def _classify_se_official_effects_plan_frontier_detail(
    intent: SEOfficialElaboratedIntent,
    planned_items: tuple[SEOfficialEffectPlanItem, ...],
    frontier_classification: str,
) -> str:
    if frontier_classification != "empty_effect_plan_with_clause_targets":
        return ""
    clause = _normalize_space(intent.clause_surface.enacting_clause).lower()
    if "upphöra att gälla" in clause:
        return "repeal_clause_only"
    if ("ordet" in clause or "orden" in clause) and ("bytas ut mot" in clause or "ersättas med" in clause):
        return "word_substitution_clause_only"
    if "punkt" in clause:
        return "point_clause_only"
    if "bilaga" in clause:
        if intent.clause_surface.inserted_appendix_labels:
            return "appendix_clause_only_labeled"
        return "appendix_clause_only_unlabeled"
    if (
        intent.clause_surface.repealed_section_labels
        or intent.clause_surface.renumber_pairs
        or intent.clause_surface.replace_section_labels
        or intent.clause_surface.inserted_section_labels
        or intent.clause_surface.inserted_appendix_labels
    ):
        return "clause_targets_without_payload"
    return "unclassified_clause_targets"


_SE_WORD_SUBSTITUTION_RE = re.compile(
    r"\b(?:ordet|orden)\b.*?(?:bytas ut mot|ersättas med).*",
    re.IGNORECASE | re.DOTALL,
)


def _extract_se_official_word_substitution_pair(clause: str) -> tuple[str, str] | None:
    """Extract one word-substitution pair from a Sweden enacting clause."""
    normalized = _normalize_space(clause)
    if not normalized:
        return None
    if not _SE_WORD_SUBSTITUTION_RE.search(normalized):
        return None
    quoted_values = [
        _normalize_space(match.group(1))
        for match in re.finditer(r"[\"“”](.+?)[\"“”]", normalized)
        if _normalize_space(match.group(1))
    ]
    if len(quoted_values) >= 2:
        return quoted_values[0], quoted_values[1]
    return None


def _build_se_official_effects_plan(intent: SEOfficialElaboratedIntent) -> SEOfficialEffectsPlan:
    """Package the elaboration into a canonical-effects plan artifact."""
    clause_surface = intent.clause_surface
    planned_items = _build_se_official_effect_plan_items(intent)
    frontier_classification = _classify_se_official_effects_plan_frontier(intent, planned_items)
    frontier_detail = _classify_se_official_effects_plan_frontier_detail(intent, planned_items, frontier_classification)
    return SEOfficialEffectsPlan(
        sfs_id=clause_surface.sfs_id,
        title=clause_surface.title,
        amended_act_sfs_id=clause_surface.amended_act_sfs_id,
        is_amending_act=clause_surface.is_amending_act,
        enacting_clause=clause_surface.enacting_clause,
        effective_clause=clause_surface.effective_clause,
        effective_date=clause_surface.effective_date,
        issued_date=intent.issued_date,
        elaboration=intent,
        frontier_classification=frontier_classification,
        frontier_detail=frontier_detail,
        planned_items=planned_items,
        planned_operation_count=len(planned_items),
    )


def _lower_se_official_effect_plan_item(
    plan: SEOfficialEffectsPlan,
    item: SEOfficialEffectPlanItem,
    source: OperationSource,
    sequence: int,
) -> tuple[list[LegalOperation], int]:
    """Lower one planned Sweden canonical effect into replay operations."""
    intent = plan.elaboration
    if intent is None:
        return [], sequence
    surface = intent.clause_surface
    payload_surface = intent.payload_surface
    if item.kind == "repeal":
        label = item.target_label
        op = LegalOperation(
            op_id=f"se_official_repeal_{surface.sfs_id}_{label}",
            sequence=sequence,
            action=StructuralAction.REPEAL,
            target=LegalAddress(path=(("section", label),)),
            source=source,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"repeal_section={label}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    if item.kind == "renumber":
        source_label = item.target_label
        destination_label = item.destination_label
        op = LegalOperation(
            op_id=f"se_official_renumber_{surface.sfs_id}_{source_label}_to_{destination_label}",
            sequence=sequence,
            action=StructuralAction.RENUMBER,
            target=LegalAddress(path=(("section", source_label),)),
            destination=LegalAddress(path=(("section", destination_label),)),
            source=source,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"renumber_section={source_label}->{destination_label}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    if item.kind in {"replace_section", "insert_section"}:
        provision = next(
            (
                provision
                for provision in payload_surface.provisions
                if _label_norm(provision.label) == item.payload_label
                or _label_norm(provision.label) == item.target_label
            ),
            None,
        )
        if provision is None:
            return [], sequence
        label = _label_norm(provision.label)
        action_kind = StructuralAction.REPLACE if item.kind == "replace_section" else StructuralAction.INSERT
        action_str = "replace" if item.kind == "replace_section" else "insert"
        op = LegalOperation(
            op_id=f"se_official_{action_str}_{surface.sfs_id}_{label}",
            sequence=sequence,
            action=action_kind,
            target=LegalAddress(path=(("section", label),)),
            payload=_parse_se_official_provision_payload(provision),
            source=source,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"target_section={label}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    if item.kind == "insert_heading":
        heading = next(
            (
                heading
                for heading in payload_surface.inserted_headings
                if _label_norm(heading.before_label) == item.payload_label
                or _label_norm(heading.before_label) == item.target_label
            ),
            None,
        )
        if heading is None:
            return [], sequence
        label = _label_norm(heading.before_label)
        op = LegalOperation(
            op_id=f"se_official_insert_heading_{surface.sfs_id}_{label}",
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("section", label),), special=FacetKind.HEADING),
            payload=IRNode(kind=IRNodeKind.HEADING, text=_normalize_space(heading.text)),
            source=source,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"target_heading_before_section={label}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    if item.kind == "insert_appendix":
        appendix = next(
            (
                appendix
                for appendix in payload_surface.appendices
                if _label_norm(appendix.label) == item.payload_label or _label_norm(appendix.label) == item.target_label
            ),
            None,
        )
        if appendix is None:
            unlabeled_appendices = [
                appendix for appendix in payload_surface.appendices if not _label_norm(appendix.label)
            ]
            if len(unlabeled_appendices) == 1 and (item.payload_label or item.target_label):
                appendix = unlabeled_appendices[0]
        if appendix is None:
            return [], sequence
        label = _label_norm(appendix.label)
        if not label:
            label = item.payload_label or item.target_label
        op = LegalOperation(
            op_id=f"se_official_insert_appendix_{surface.sfs_id}_{label}",
            sequence=sequence,
            action=StructuralAction.INSERT,
            target=LegalAddress(path=(("appendix", label),)),
            payload=_parse_se_official_appendix_payload(appendix),
            source=source,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"target_appendix={label}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    if item.kind == "text_replace":
        patch = item.text_patch
        old_text = _normalize_space(patch.selector.match_text if patch is not None else "")
        new_text = _normalize_space(patch.replacement if patch is not None and patch.replacement is not None else "")
        if not old_text or not new_text:
            return [], sequence
        text_patch = patch
        if text_patch is None:
            return [], sequence
        op = LegalOperation(
            op_id=f"se_official_text_replace_{surface.sfs_id}_{item.target_label}",
            sequence=sequence,
            action=StructuralAction.TEXT_REPLACE,
            target=LegalAddress(path=(("section", item.target_label),)),
            source=source,
            text_patch=text_patch,
            provenance_tags=(
                "sweden_official_act_v1",
                f"base_sfs_id={surface.amended_act_sfs_id}",
                f"target_section={item.target_label}",
                f"text_replace={old_text}->{new_text}",
            ),
            group_id=f"se_official_act::{surface.sfs_id}",
        )
        return [op], sequence + 1
    raise NotImplementedError(f"unsupported Sweden official act plan item kind: {item.kind}")


def _extract_footnotes(lines: list[str]) -> tuple[list[str], tuple[str, ...]]:
    kept: list[str] = []
    footnotes: list[str] = []
    footnote_ref_line_re = re.compile(r"^\d+\s*[a-z]?\s*§\s+\d{4}:\d+\.?$", re.IGNORECASE)
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        if not _FOOTNOTE_LINE_RE.match(line):
            kept.append(line)
            idx += 1
            continue
        parts = [line]
        while idx + 1 < len(lines):
            next_line = lines[idx + 1]
            if not next_line:
                if idx + 2 < len(lines):
                    cont_line = lines[idx + 2]
                    if cont_line and footnote_ref_line_re.match(cont_line):
                        parts.append(cont_line)
                        idx += 2
                        continue
                    if cont_line and parts[-1].endswith("-") and cont_line[:1].islower():
                        parts.append(cont_line)
                        idx += 2
                        continue
                break
            if _FOOTNOTE_LINE_RE.match(next_line) or _SECTION_RE.match(next_line):
                break
            if next_line.lower().startswith("denna ") or next_line.lower() == "på regeringens vägnar":
                break
            if footnote_ref_line_re.match(next_line):
                parts.append(next_line)
                idx += 1
                continue
            if parts[-1].endswith("-") or next_line[:1].islower():
                parts.append(next_line)
                idx += 1
                continue
            break
        footnotes.append(_join_wrapped_lines(parts))
        idx += 1
    return kept, tuple(footnotes)


def _parse_se_official_appendices(lines: list[str]) -> tuple[SEOfficialAppendixText, ...]:
    blocks = _text_blocks("\n".join(lines))
    appendices: list[SEOfficialAppendixText] = []
    current_label: Optional[str] = None
    current_title = ""
    current_blocks: list[str] = []

    def _flush() -> None:
        if current_label is not None:
            appendices.append(
                SEOfficialAppendixText(
                    label=current_label,
                    title=current_title,
                    text="\n\n".join(block for block in current_blocks if block.strip()),
                )
            )

    for block in blocks:
        block_lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not block_lines:
            continue
        head = block_lines[0]
        if (
            current_label is not None
            and current_label == ""
            and not current_title
            and not current_blocks
            and not head.lower().startswith("bilaga")
        ):
            current_title = _normalize_space(head)
            if len(block_lines) > 1:
                current_blocks.append(_join_wrapped_lines(block_lines[1:]))
            continue
        appendix_match = _APPENDIX_RE.match(head)
        if appendix_match:
            _flush()
            current_label = _label_norm(appendix_match.group("label") or "")
            current_title = _normalize_space(appendix_match.group("title") or "")
            if len(current_label) == 1 and current_title and current_title[:1].islower():
                current_title = f"{current_label}{current_title}"
                current_label = ""
            current_blocks = []
            remainder_lines = block_lines[1:]
            if current_label == "" and not current_title and remainder_lines:
                current_title = _normalize_space(remainder_lines[0])
                remainder_lines = remainder_lines[1:]
            if remainder_lines:
                current_blocks.append(_join_wrapped_lines(remainder_lines))
            continue
        if current_label is not None:
            current_blocks.append(block)
    _flush()
    return tuple(appendices)


def _parse_embedded_se_official_appendices(lines: list[str]) -> tuple[SEOfficialAppendixText, ...]:
    """Recover appendix payloads embedded in the main act text."""
    appendix_start = next((idx for idx, line in enumerate(lines) if _APPENDIX_RE.match(line)), len(lines))
    if appendix_start >= len(lines):
        return ()
    appendix_end = next(
        (
            idx
            for idx in range(appendix_start + 1, len(lines))
            if lines[idx].lower().startswith("denna ") or lines[idx].lower() == "på regeringens vägnar"
        ),
        len(lines),
    )
    appendices = _parse_se_official_appendices(lines[appendix_start:appendix_end])
    if not appendices:
        return ()
    if not any(_normalize_space(appendix.label) for appendix in appendices):
        return ()
    return appendices


def _parse_se_official_provision_payload(provision: SEOfficialProvisionText) -> IRNode:
    section = _SEMutableNode(kind="section", label=_label_norm(provision.label))
    current_subsection: Optional[_SEMutableNode] = None
    subsection_count = 0
    for block in _text_blocks(provision.text):
        item_match = _ITEM_RE.match(block)
        if item_match:
            if current_subsection is None:
                subsection_count += 1
                current_subsection = _SEMutableNode(kind="subsection", label=str(subsection_count))
                section.children.append(current_subsection)
            current_subsection.children.append(
                _SEMutableNode(
                    kind="item",
                    label=_label_norm(item_match.group("label")),
                    text=_normalize_space(item_match.group("text")),
                )
            )
            continue
        subsection_count += 1
        current_subsection = _SEMutableNode(
            kind="subsection",
            label=str(subsection_count),
            text=_normalize_space(block),
        )
        section.children.append(current_subsection)
    return section.to_irnode()


def _parse_se_official_appendix_payload(appendix: SEOfficialAppendixText) -> IRNode:
    node = _SEMutableNode(kind="appendix", label=appendix.label or None)
    if appendix.title:
        node.children.append(_SEMutableNode(kind="heading", text=appendix.title))
    blocks = _text_blocks(appendix.text)
    subsection_count = 0
    for block in blocks:
        item_match = re.match(r"^(?P<label>\d+)[\.\)]\s+(?P<text>.+)$", block)
        if item_match:
            subsection_count += 1
            subsection = _SEMutableNode(kind="subsection", label=str(subsection_count))
            subsection.children.append(
                _SEMutableNode(
                    kind="item",
                    label=_label_norm(item_match.group("label")),
                    text=_normalize_space(item_match.group("text")),
                )
            )
            node.children.append(subsection)
            continue
        subsection_count += 1
        node.children.append(
            _SEMutableNode(kind="subsection", label=str(subsection_count), text=_normalize_space(block))
        )
    return node.to_irnode()


def build_se_official_base_statute(
    payload: bytes | str | dict[str, Any],
    *,
    statute_id: str | None = None,
) -> IRStatute:
    """Build a first IR seed from a non-amending official Sweden act."""
    act = _coerce_official_act(payload)
    if act.is_amending_act:
        raise ValueError(f"official act {act.sfs_id or statute_id or ''} is amending; not a base act seed")

    payload_surface = _build_se_official_payload_surface(act)
    body_children: list[IRNode] = []
    supplements: list[IRNode] = []
    headings_by_label: dict[str, list[SEOfficialHeadingText]] = {}
    for heading in payload_surface.inserted_headings:
        label = _label_norm(heading.before_label)
        if not label:
            continue
        headings_by_label.setdefault(label, []).append(heading)

    for provision in payload_surface.provisions:
        label = _label_norm(provision.label)
        for heading in headings_by_label.pop(label, []):
            if heading.text.strip():
                body_children.append(IRNode(kind=IRNodeKind.HEADING, text=_normalize_space(heading.text)))
        body_children.append(_parse_se_official_provision_payload(provision))

    for remaining in headings_by_label.values():
        for heading in remaining:
            if heading.text.strip():
                body_children.append(IRNode(kind=IRNodeKind.HEADING, text=_normalize_space(heading.text)))

    for appendix in payload_surface.appendices:
        supplements.append(_parse_se_official_appendix_payload(appendix))

    statute = IRStatute(
        statute_id=statute_id or act.sfs_id,
        title=act.title,
        body=IRNode(kind=IRNodeKind.BODY, children=tuple(body_children)),
        supplements=tuple(supplements),
        metadata={
            "jurisdiction": "se",
            "source_confidence": SESourceConfidence.OFFICIAL_PDF_ONLY.value,
            "source_text_kind": "official_pdf_act",
            "published_date": act.published_date,
            "issued_date": act.issued_date,
        },
    )
    return statute


def _lower_se_official_effects_plan(
    plan: SEOfficialEffectsPlan,
    *,
    source_id: str = "",
) -> list[LegalOperation]:
    """Lower a typed Sweden canonical-effects plan into canonical operations."""
    intent = plan.elaboration
    if intent is None:
        return []
    surface = intent.clause_surface
    sid = source_id or surface.sfs_id
    if not surface.is_amending_act:
        return []
    if not surface.amended_act_sfs_id:
        raise NotImplementedError(
            f"Sweden official act {sid} does not identify a base act "
            f"[{plan.frontier_classification or 'missing_base_act'}]"
        )
    if not plan.planned_items:
        raise NotImplementedError(
            f"Sweden official act {sid} has no planned canonical effects "
            f"[{plan.frontier_classification or 'empty_effect_plan_without_targets'}]"
        )

    source = OperationSource(
        statute_id=surface.sfs_id,
        title=surface.title,
        enacted=intent.issued_date,
        effective=surface.effective_date,
        raw_text=surface.enacting_clause,
    )
    ops: list[LegalOperation] = []
    next_sequence = 1
    for item in plan.planned_items:
        planned_ops, next_sequence = _lower_se_official_effect_plan_item(plan, item, source, next_sequence)
        ops.extend(planned_ops)
    return ops


def se_statute_invariant_violations(statute: IRStatute) -> list[str]:
    violations = [f"body:{violation}" for violation in _check_se_invariants(statute.body)]
    for supplement in statute.supplements:
        for violation in _check_se_invariants(supplement):
            violations.append(f"supplement:{supplement.label or ''}:{violation}")
    return violations


def compile_se_official_act_ops(payload: bytes | str | dict[str, Any], source_id: str = "") -> list[LegalOperation]:
    """Compile a first-pass Sweden amendment op set from official act JSON.

    Supported shape:
    - whole-section replacements named in the enacting clause
    - inserted sections named as `ny paragraf`
    - inserted heading payloads that sit immediately before an inserted section
    - inserted appendices named as `ny bilaga`

    Architectural note:
    this is intentionally narrow. Sweden routes official-act compilation
    through explicit clause-surface, payload-surface, elaboration, and
    canonical-effects plan waists before lowering to canonical execution
    artifacts.
    """
    act = _coerce_official_act(payload)
    plan = _build_se_official_effects_plan(_build_se_official_elaboration(act))
    return _lower_se_official_effects_plan(plan, source_id=source_id)


def _is_node_active_on(node: IRNode, as_of: str) -> bool:
    start = node.attrs.get("RestrictStartDate", "")
    end = node.attrs.get("RestrictEndDate", "")
    if start and as_of < start:
        return False
    if end and as_of >= end:
        return False
    return True


def _materialize_irnode_as_of(node: IRNode, as_of: str) -> Optional[IRNode]:
    if node.kind is not IRNodeKind.BODY and not _is_node_active_on(node, as_of):
        return None
    new_children: list[IRNode] = []
    for child in node.children:
        materialized_child = _materialize_irnode_as_of(child, as_of)
        if materialized_child is not None:
            new_children.append(materialized_child)
    return IRNode(
        kind=node.kind,
        label=node.label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(new_children),
    )


def materialize_se_statute_as_of(statute: IRStatute, as_of: str) -> IRStatute:
    """Materialize a Sweden current-text IR tree at one date using node attrs."""
    body = _materialize_irnode_as_of(statute.body, as_of) or IRNode(kind=IRNodeKind.BODY)
    supplements = [
        materialized
        for supplement in statute.supplements
        if (materialized := _materialize_irnode_as_of(supplement, as_of)) is not None
    ]
    metadata = dict(statute.metadata)
    metadata["materialized_as_of"] = as_of
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=supplements,
        metadata=metadata,
    )


def _se_label_sort_key(label: str | None) -> tuple[int, str, int]:
    normalized = _label_norm(label or "")
    match = re.fullmatch(r"(\d+)([a-z]*)", normalized, flags=re.IGNORECASE)
    if match:
        return (int(match.group(1)), match.group(2).lower(), 0)
    return (10**9, normalized.lower(), 0)


def se_section_text_map(statute: IRStatute) -> dict[str, str]:
    label_map: dict[str, str] = {}

    def _full_text(node: IRNode) -> str:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node.children:
            child_text = _full_text(child)
            if child_text:
                parts.append(child_text)
        return " ".join(parts)

    def _walk(node: IRNode) -> None:
        if node.kind is IRNodeKind.SECTION and node.label:
            label_map[_label_norm(node.label)] = _full_text(node)
        for child in node.children:
            _walk(child)

    _walk(statute.body)
    for schedule in statute.supplements:
        _walk(schedule)
    return label_map


def se_appendix_text_map(statute: IRStatute) -> dict[str, str]:
    def _full_text(node: IRNode) -> str:
        parts: list[str] = []
        if node.text:
            parts.append(node.text)
        for child in node.children:
            child_text = _full_text(child)
            if child_text:
                parts.append(child_text)
        return " ".join(parts)

    return {
        _label_norm(schedule.label or ""): _full_text(schedule)
        for schedule in statute.supplements
        if schedule.kind is IRNodeKind.APPENDIX and schedule.label
    }


def se_heading_before_section_map(statute: IRStatute) -> dict[str, str]:
    headings: dict[str, str] = {}

    def _walk(node: IRNode) -> None:
        last_heading = ""
        for child in node.children:
            if child.kind is IRNodeKind.HEADING:
                last_heading = _normalize_space(child.text)
                continue
            if child.kind is IRNodeKind.SECTION and child.label and last_heading:
                headings[_label_norm(child.label)] = last_heading
            _walk(child)

    _walk(statute.body)
    return headings


def parse_se_amendment_ops(json_bytes: bytes, source_id: str) -> list[LegalOperation]:
    """Compile first-pass Sweden ops from archived official act JSON."""
    return compile_se_official_act_ops(json_bytes, source_id=source_id)


def _find_se_section_parent_path(body: IRNode, section_label: str) -> tuple[tuple[str, str], ...]:
    target_key = _se_label_sort_key(section_label)
    candidates: list[tuple[tuple[tuple[str, str], ...], tuple[int, str, int]]] = []

    def _walk(node: IRNode, path: tuple[tuple[str, str], ...]) -> None:
        section_labels = [
            _label_norm(child.label or "") for child in node.children if child.kind is IRNodeKind.SECTION and child.label
        ]
        if section_labels:
            lower = [label for label in section_labels if _se_label_sort_key(label) < target_key]
            upper = [label for label in section_labels if _se_label_sort_key(label) > target_key]
            score = (
                _se_label_sort_key(lower[-1]) if lower else (-1, "", 0),
                _se_label_sort_key(upper[0]) if upper else (10**9, "", 0),
            )
            candidates.append((path, score[0]))
        for child in node.children:
            if child.kind in {IRNodeKind.BODY, IRNodeKind.CHAPTER, IRNodeKind.PART}:
                child_path = path
                if child.kind is not IRNodeKind.BODY and child.label:
                    child_path = path + ((str(child.kind), child.label),)
                _walk(child, child_path)

    _walk(body, ())
    if not candidates:
        return ()
    candidates.sort(key=lambda item: item[1], reverse=True)
    return candidates[0][0]


def _insert_se_heading_before_section(body: IRNode, section_label: str, heading: IRNode) -> IRNode:
    parent_path = _find_se_section_parent_path(body, section_label)
    container = tree_ops.resolve(body, list(parent_path)) if parent_path else body
    if container is None:
        raise LookupError(f"could not resolve container for Sweden heading before section {section_label}")
    children = list(container.children)
    insert_at = next(
        (
            idx
            for idx, child in enumerate(children)
            if child.kind is IRNodeKind.SECTION and _label_norm(child.label or "") == _label_norm(section_label)
        ),
        None,
    )
    if insert_at is None:
        raise LookupError(f"section {section_label} not found for Sweden heading insertion")
    new_children = children[:insert_at] + [heading] + children[insert_at:]
    replacement = IRNode(
        kind=container.kind,
        label=container.label,
        text=container.text,
        attrs=dict(container.attrs),
        children=tuple(new_children),
    )
    if not parent_path:
        return replacement
    return tree_ops.replace_at(body, list(parent_path), replacement)


def _remove_se_heading_before_section(body: IRNode, section_label: str) -> IRNode:
    parent_path = _find_se_section_parent_path(body, section_label)
    container = tree_ops.resolve(body, list(parent_path)) if parent_path else body
    if container is None:
        raise LookupError(f"could not resolve container for Sweden heading before section {section_label}")
    children = list(container.children)
    section_index = next(
        (
            idx
            for idx, child in enumerate(children)
            if child.kind is IRNodeKind.SECTION and _label_norm(child.label or "") == _label_norm(section_label)
        ),
        None,
    )
    if section_index is None:
        raise LookupError(f"section {section_label} not found for Sweden heading repeal")
    if section_index == 0 or children[section_index - 1].kind is not IRNodeKind.HEADING:
        raise LookupError(f"heading before section {section_label} not found in Sweden statute")
    new_children = children[: section_index - 1] + children[section_index:]
    replacement = IRNode(
        kind=container.kind,
        label=container.label,
        text=container.text,
        attrs=dict(container.attrs),
        children=tuple(new_children),
    )
    if not parent_path:
        return replacement
    return tree_ops.replace_at(body, list(parent_path), replacement)


def _insert_se_appendix_sorted(supplements: list[IRNode], appendix: IRNode) -> list[IRNode]:
    target_key = _se_label_sort_key(appendix.label or "")
    out: list[IRNode] = []
    inserted = False
    for supplement in supplements:
        if not inserted and supplement.kind is IRNodeKind.APPENDIX and _se_label_sort_key(supplement.label or "") > target_key:
            out.append(appendix)
            inserted = True
        out.append(supplement)
    if not inserted:
        out.append(appendix)
    return out


def _check_se_invariants(node: IRNode) -> list[str]:
    filtered: list[str] = []
    for violation in tree_ops.check_invariants(node):
        if "unexpected heading inside body" in violation:
            continue
        if "unexpected item inside subsection" in violation:
            continue
        if "unexpected subsection inside appendix" in violation:
            continue
        filtered.append(violation)
    return filtered


def _clone_se_node_with_label(node: IRNode, label: str) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(node.children),
    )


def _append_se_replay_adjudication(
    adjudications_out: list[CompileAdjudication] | None,
    *,
    kind: str,
    message: str,
    op: LegalOperation,
    detail: dict[str, object] | None = None,
) -> None:
    if adjudications_out is None:
        return
    normalized_detail = dict(detail or {})
    action_value = normalized_detail.get("action")
    if isinstance(action_value, StructuralAction):
        normalized_detail["action"] = action_value.value
    adjudications_out.append(
        CompileAdjudication(
            kind=kind,
            message=message,
            source_statute=op.source.statute_id if op.source else "",
            op_id=op.op_id,
            detail=normalized_detail,
        )
    )


def apply_se_ops(
    statute: IRStatute,
    ops: list[LegalOperation],
    adjudications_out: list[CompileAdjudication] | None = None,
) -> IRStatute:
    """Apply the currently supported Sweden op subset to an IRStatute."""
    body = statute.body
    supplements = list(statute.supplements)
    for op in ops:
        leaf_kind = op.target.leaf_kind()
        if leaf_kind == "section":
            if op.target.special is FacetKind.HEADING:
                if op.action is StructuralAction.INSERT:
                    heading = op.payload
                    if heading is None or heading.kind is not IRNodeKind.HEADING:
                        _append_se_replay_adjudication(
                            adjudications_out,
                            kind="se_replay_payload_missing",
                            message="Sweden heading insert skipped: payload missing or wrong kind.",
                            op=op,
                            detail={"action": op.action, "target": op.target.leaf_label()},
                        )
                        continue
                    body = _insert_se_heading_before_section(body, op.target.leaf_label(), heading)
                    continue
                if op.action is StructuralAction.REPEAL:
                    body = _remove_se_heading_before_section(body, op.target.leaf_label())
                    continue
                _append_se_replay_adjudication(
                    adjudications_out,
                    kind="se_replay_unsupported_action",
                    message="Sweden heading replay skipped: unsupported action.",
                    op=op,
                    detail={"action": op.action, "target": op.target.leaf_label()},
                )
                continue
            if op.action is StructuralAction.RENUMBER:
                section_label = op.target.leaf_label()
                destination_label = _label_norm(op.destination.leaf_label() if op.destination is not None else "")
                if not destination_label:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_destination_missing",
                        message="Sweden renumber replay skipped: destination not provided.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                section_path = tree_ops.find(body, "section", section_label)
                if section_path is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden renumber replay skipped: source section not found.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                if tree_ops.find(body, "section", destination_label) is not None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_renumber_collision",
                        message="Sweden renumber replay skipped: destination already exists.",
                        op=op,
                        detail={"action": op.action, "target": section_label, "destination": destination_label},
                    )
                    continue
                existing = tree_ops.resolve(body, section_path)
                if existing is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden renumber replay skipped: source section could not be resolved.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                moved = _clone_se_node_with_label(existing, destination_label)
                body = tree_ops.remove_at(body, section_path)
                parent_path = _find_se_section_parent_path(body, destination_label)
                body = tree_ops.insert_sorted(body, list(parent_path), moved, sort_key_fn=_se_label_sort_key)
                continue
            if op.action is StructuralAction.REPEAL:
                section_label = op.target.leaf_label()
                section_path = tree_ops.find(body, "section", section_label)
                if section_path is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden section repeal skipped: target not found.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                body = tree_ops.remove_at(body, section_path)
                continue
            if op.action is StructuralAction.TEXT_REPLACE:
                section_label = op.target.leaf_label()
                section_path = tree_ops.find(body, "section", section_label)
                if section_path is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden section text replacement skipped: target not found.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                patch = op.text_patch
                if patch is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_payload_missing",
                        message="Sweden section text replacement skipped: structured text_patch missing.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                old_text = _normalize_space(patch.selector.match_text)
                new_text = _normalize_space(patch.replacement or "")
                if not old_text and op.payload is not None:
                    old_text = _normalize_space(str(op.payload.attrs.get("old_text") or ""))
                if not new_text and op.payload is not None:
                    new_text = _normalize_space(op.payload.text or "")
                if not old_text or not new_text:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_payload_missing",
                        message="Sweden section text replacement skipped: old or new text missing.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                section_node = tree_ops.resolve(body, section_path)
                if section_node is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden section text replacement skipped: source section could not be resolved.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                replaced_section, changed = _replace_se_text_in_node(section_node, old_text, new_text)
                if not changed:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_text_replace_no_match",
                        message="Sweden section text replacement skipped: old text not found in target subtree.",
                        op=op,
                        detail={
                            "action": op.action.value,
                            "target": section_label,
                            "old_text": old_text,
                            "new_text": new_text,
                        },
                    )
                    continue
                body = tree_ops.replace_at(body, section_path, replaced_section)
                continue
            if op.payload is None or op.payload.kind is not IRNodeKind.SECTION:
                _append_se_replay_adjudication(
                    adjudications_out,
                    kind="se_replay_payload_missing",
                    message="Sweden section replay skipped: payload missing or wrong kind.",
                    op=op,
                    detail={"action": op.action, "target": op.target.leaf_label()},
                )
                continue
            section_label = op.target.leaf_label()
            section_path = tree_ops.find(body, "section", section_label)
            if op.action is StructuralAction.REPLACE:
                if section_path is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden section replace skipped: target not found.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                body = tree_ops.replace_at(body, section_path, op.payload)
                continue
            if op.action is StructuralAction.INSERT:
                if section_path is not None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_unsupported_action",
                        message="Sweden section insert replay skipped: section already exists.",
                        op=op,
                        detail={"action": op.action, "target": section_label},
                    )
                    continue
                parent_path = _find_se_section_parent_path(body, section_label)
                body = tree_ops.insert_sorted(body, list(parent_path), op.payload, sort_key_fn=_se_label_sort_key)
                continue
            _append_se_replay_adjudication(
                adjudications_out,
                kind="se_replay_unsupported_action",
                message="Sweden section replay skipped: unsupported action.",
                op=op,
                detail={"action": op.action, "target": section_label},
            )
            continue
        if leaf_kind == "appendix":
            appendix_label = _label_norm(op.target.leaf_label())
            existing_index = next(
                (
                    idx
                    for idx, supplement in enumerate(supplements)
                    if supplement.kind is IRNodeKind.APPENDIX and _label_norm(supplement.label or "") == appendix_label
                ),
                None,
            )
            if op.action is StructuralAction.REPEAL:
                if existing_index is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden appendix repeal replay skipped: target not found.",
                        op=op,
                        detail={"action": op.action, "target": appendix_label},
                    )
                    continue
                supplements.pop(existing_index)
                continue
            if op.payload is None or op.payload.kind is not IRNodeKind.APPENDIX:
                _append_se_replay_adjudication(
                    adjudications_out,
                    kind="se_replay_payload_missing",
                    message="Sweden appendix replay skipped: payload missing or wrong kind.",
                    op=op,
                    detail={"action": op.action, "target": appendix_label},
                )
                continue
            if op.action is StructuralAction.REPLACE:
                if existing_index is None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_target_not_found",
                        message="Sweden appendix replace replay skipped: target not found.",
                        op=op,
                        detail={"action": op.action, "target": appendix_label},
                    )
                    continue
                supplements[existing_index] = op.payload
                continue
            if op.action is StructuralAction.INSERT:
                if existing_index is not None:
                    _append_se_replay_adjudication(
                        adjudications_out,
                        kind="se_replay_unsupported_action",
                        message="Sweden appendix insert replay skipped: appendix already exists.",
                        op=op,
                        detail={"action": op.action, "target": appendix_label},
                    )
                    continue
                supplements = _insert_se_appendix_sorted(supplements, op.payload)
                continue
            _append_se_replay_adjudication(
                adjudications_out,
                kind="se_replay_unsupported_action",
                message="Sweden appendix replay skipped: unsupported action.",
                op=op,
                detail={"action": op.action, "target": appendix_label},
            )
            continue
        _append_se_replay_adjudication(
            adjudications_out,
            kind="se_replay_unsupported_target_kind",
            message="Sweden replay skipped: unsupported target kind.",
            op=op,
            detail={"target_kind": leaf_kind, "target": op.target.leaf_label(), "action": op.action},
        )
    metadata = dict(statute.metadata)
    metadata["applied_op_count"] = metadata.get("applied_op_count", 0) + len(ops)
    invariant_violations = se_statute_invariant_violations(
        IRStatute(
            statute_id=statute.statute_id,
            title=statute.title,
            body=body,
            supplements=supplements,
            metadata={},
        )
    )
    if invariant_violations:
        metadata["invariant_violations"] = invariant_violations
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=body,
        supplements=supplements,
        metadata=metadata,
    )
