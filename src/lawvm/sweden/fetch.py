"""Sweden official-source acquisition helpers.

The official SFS doc page is used as a locator/provenance layer only.
The primary archived source artifact is the official PDF plus a derived
plain-text extraction produced by `pdftotext`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
import json
import re
import time
from pathlib import Path
import subprocess
from typing import Any, Callable, Protocol, Optional, cast
from urllib.parse import urlencode, urljoin

from lawvm.core.comparison_normalization import ComparisonNormalizationRule, normalize_comparison_text
from lawvm.core.diagnostic_records import diagnostic_detail
from lawvm.core.ir import IRNode, IRStatute, LegalOperation
from lawvm.core.ir_helpers import ir_statute_from_dict
from lawvm.core.semantic_types import FacetKind, IRNodeKind, StructuralAction
from lawvm.core import tree_ops
from lawvm.core.adjudication_evidence import adjudication_finding_evidence_rows
from lawvm.replay_adjudication import CompileAdjudication
from lawvm.sweden.grafter import SESourceRecord, parse_se_source_record, parse_se_statute
from lawvm.sweden.grafter import (
    apply_se_ops,
    build_se_official_base_statute,
    canonicalize_se_table_section_text,
    enrich_se_source_record_with_doc_page,
    extract_se_current_section_texts,
    materialize_se_statute_as_of,
    se_appendix_text_map,
    se_official_clause_surface_to_dict,
    se_official_elaboration_to_dict,
    se_official_effect_plan_to_dict,
    se_official_payload_surface_to_dict,
    se_heading_before_section_map,
    parse_se_official_act_text,
    parse_se_official_pdf_url,
    _build_se_official_clause_surface,
    _build_se_official_elaboration,
    _build_se_official_effects_plan,
    _build_se_official_payload_surface,
    _infer_amended_act_sfs_id_from_clause,
    _coerce_official_act,
    _lower_se_official_effects_plan,
    se_legal_operation_from_dict,
    se_section_text_map,
    se_statute_invariant_violation_records,
    se_legal_operation_to_dict,
    se_statute_invariant_violations,
    se_official_doc_url,
    se_official_act_text_to_dict,
    se_pdf_bytes_to_text,
)


_DEFAULT_CACHE = Path(__file__).parent.parent.parent.parent / "data" / "sweden.farchive"
_IMMUTABLE_CACHE_HOURS = float("inf")
_CURRENT_SURFACE_CACHE_HOURS = 24.0


class _ArchiveLike(Protocol):
    def store(self, locator: str, data: bytes, *, storage_class: str | None = None) -> str: ...

    def get(self, locator: str) -> bytes | None: ...

    def has(self, locator: str, *, max_age_hours: float = ...) -> bool: ...


@dataclass(frozen=True)
class SEOfficialArtifacts:
    sfs_id: str
    doc_url: str
    doc_locator: str
    pdf_url: str
    pdf_locator: str
    pdf_text_url: str
    pdf_cleaned_text_url: str


@dataclass(frozen=True)
class SESourceBundle:
    source_record: SESourceRecord
    current_statute: IRStatute
    official_artifacts: Optional[SEOfficialArtifacts] = None


_WS_RE = re.compile(r"\s+")
_PAGE_NUMBER_RE = re.compile(r"^\d+$")
_SFS_HEADER_RE = re.compile(r"^SFS\s+\d{4}:\d+[a-zA-Z]?$", re.IGNORECASE)
_PAGE_FURNITURE_RE = re.compile(r"^(Sida|Page)\s+\d+(\s+av\s+\d+)?$", re.IGNORECASE)
_DIGIT_GARBAGE_RE = re.compile(r"^[0-9:;.,()\-\s]{8,}$")
_RK_UTFARDAD_RE = re.compile(r"Utfärdad:</span>\s*([0-9]{4}-[0-9]{2}-[0-9]{2})", re.IGNORECASE)
_SE_ATTRIBUTION_SFS_RE = re.compile(r"(?:Förordning|Lag)\s+\((\d{4}:\d+)\)\.?\s*$", re.IGNORECASE)
_SE_RENUMBER_PLACEHOLDER_SFS_RE = re.compile(
    r"Har betecknats\s+.+?\s+genom\s+(?:förordning|lag)\s+\((\d{4}:\d+)\)\.?",
    re.IGNORECASE,
)
_CLOUDFLARE_BLOCK_RE = re.compile(rb"(?:Attention Required|cloudflare|cf-browser-verification)", re.IGNORECASE)
_LEGACY_SFSPDF_PDF_RE = re.compile(r'href="(?P<href>/SFSdoc/\d{2}/\d+\.PDF)"', re.IGNORECASE)
_SE_DOC_URL_RE = re.compile(r"^https://svenskforfattningssamling\.se/doc/(?P<year>\d{4})(?P<number>\d+)\.html$")
_SE_FETCH_RETRY_ATTEMPTS = 5
_SE_FETCH_RETRY_INITIAL_DELAY_SECONDS = 0.5
_SE_FETCH_RETRY_MAX_DELAY_SECONDS = 8.0


def _se_archive_fetch(
    archive: _ArchiveLike,
    url: str,
    *,
    max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    storage_class: str | None = None,
) -> bytes | None:
    """Fetch URL with Farchive caching: return cached content if fresh, else HTTP-fetch and store."""
    import math

    if math.isinf(max_age_hours):
        cached = archive.get(url)
        if cached is not None:
            return cached
    else:
        if archive.has(url, max_age_hours=max_age_hours):
            cached = archive.get(url)
            if cached is not None:
                return cached
    fetch_method = getattr(archive, "fetch", None)

    def _attempt() -> bytes | None:
        if callable(fetch_method):
            try:
                data = fetch_method(
                    url,
                    max_age_hours=max_age_hours,
                    content_type=storage_class or "auto",
                )
            except TypeError:
                data = fetch_method(url)
            return data if data else None

        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={"User-Agent": "LawVM-SE/1.0 (+https://github.com/lawvm)"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
        except Exception:
            return None
        return data if data else None

    # Sweden acquisition is network-flaky enough that a small retry loop is
    # worth the cost, but the archive contract remains unchanged: success still
    # stores the fetched bytes under the same real URL locator.
    data = _retry_bytes_fetch(_attempt, label=url)
    if data:
        archive.store(url, data, storage_class=storage_class)
    return data


def _retry_bytes_fetch(
    fetch: Callable[[], bytes | None], *, label: str, attempts: int = _SE_FETCH_RETRY_ATTEMPTS
) -> bytes | None:
    last_delay = 0.0
    for attempt in range(1, attempts + 1):
        data = fetch()
        if data:
            return data
        if attempt >= attempts:
            break
        last_delay = min(
            _SE_FETCH_RETRY_INITIAL_DELAY_SECONDS * (2 ** (attempt - 1)),
            _SE_FETCH_RETRY_MAX_DELAY_SECONDS,
        )
        time.sleep(last_delay)
    return None


def se_rk_current_url(sfs_id: str) -> str:
    return f"https://rkrattsbaser.gov.se/sfst?bet={sfs_id}"


def se_legacy_sfspdf_index_url() -> str:
    return "https://rkrattsdb.gov.se/sfspdf/"


def se_legacy_sfspdf_search_url() -> str:
    return "https://rkrattsdb.gov.se/sfspdf/sql_search_rsp.asp"


def open_se_archive(db_path: Path | None = None):  # returns Farchive
    from farchive import Farchive

    return Farchive(db_path or _DEFAULT_CACHE)


def se_official_doc_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/official.doc.html"


def se_official_pdf_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/official.pdf"


def se_rk_current_json_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/rk.current.json"


def se_source_record_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/source_record.json"


def se_current_ir_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/current.ir.json"


def se_bundle_manifest_locator(sfs_id: str) -> str:
    return f"se://sfs/{sfs_id}/bundle.json"


def se_pdf_text_locator(sfs_id: str) -> str:
    """Canonical archive locator for text extracted from the official SFS PDF."""
    return f"se://sfs/{sfs_id}/official.pdf.txt"


def se_pdf_cleanup_locator(sfs_id: str) -> str:
    """Reserved locator for future deterministic cleanup over extracted PDF text."""
    return f"se://sfs/{sfs_id}/official.cleaned.txt"


def se_official_act_locator(sfs_id: str) -> str:
    """Canonical archive locator for structured text parsed from the official SFS PDF."""
    return f"se://sfs/{sfs_id}/official.act.json"


def se_official_base_ir_locator(sfs_id: str) -> str:
    """Canonical archive locator for a non-amending official-act IR seed."""
    return f"se://sfs/{sfs_id}/official.base.ir.json"


def se_official_ops_locator(sfs_id: str) -> str:
    """Canonical archive locator for compiled first-pass ops from the official act."""
    return f"se://sfs/{sfs_id}/official.ops.json"


def se_official_ops_adjudications_locator(sfs_id: str) -> str:
    """Canonical archive locator for official-op compile adjudications."""
    return f"se://sfs/{sfs_id}/official.ops.adjudications.json"


def se_official_clause_surface_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act clause surface."""
    return f"se://sfs/{sfs_id}/official.clause.json"


def se_official_payload_surface_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act payload surface."""
    return f"se://sfs/{sfs_id}/official.payload.json"


def se_official_elaboration_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden official-act elaboration waist."""
    return f"se://sfs/{sfs_id}/official.elaboration.json"


def se_official_effects_plan_locator(sfs_id: str) -> str:
    """Canonical archive locator for the Sweden canonical-effects plan waist."""
    return f"se://sfs/{sfs_id}/official.effects.plan.json"


def se_backfill_official_checkpoint_locator() -> str:
    """Canonical archive locator for Sweden official backfill run-state."""
    return "se://sweden/backfill-official/checkpoint.json"


def se_backfill_official_status_locator() -> str:
    """Canonical archive locator for Sweden official backfill live status."""
    return "se://sweden/backfill-official/status.json"


def se_backfill_official_history_locator() -> str:
    """Canonical archive locator for Sweden official backfill run history."""
    return "se://sweden/backfill-official/history.json"


def se_backfill_official_completeness_locator() -> str:
    """Canonical archive locator for Sweden official backfill completeness."""
    return "se://sweden/backfill-official/completeness.json"


def se_backfill_official_gap_report_locator() -> str:
    """Canonical archive locator for Sweden official backfill year/range gaps."""
    return "se://sweden/backfill-official/gap-report.json"


def se_backfill_official_chunk_plan_locator() -> str:
    """Canonical archive locator for Sweden official backfill chunk planning."""
    return "se://sweden/backfill-official/chunk-plan.json"


def se_sfs_id_from_doc_url(doc_url: str) -> str | None:
    match = _SE_DOC_URL_RE.fullmatch(doc_url.strip())
    if not match:
        return None
    return f"{match.group('year')}:{int(match.group('number'))}"


def clean_se_pdf_text(pdf_text: str) -> str:
    """Apply conservative deterministic cleanup to `pdftotext` output.

    The goal is not perfect reconstruction. It is to remove obvious page
    furniture while preserving legal wording and paragraph boundaries.
    """
    normalized = pdf_text.replace("\r\n", "\n").replace("\r", "\n").replace("\f", "\n")
    out_lines: list[str] = []
    previous_blank = True
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            if not previous_blank:
                out_lines.append("")
            previous_blank = True
            continue
        if _PAGE_NUMBER_RE.fullmatch(line):
            continue
        if _SFS_HEADER_RE.fullmatch(line):
            continue
        if _PAGE_FURNITURE_RE.fullmatch(line):
            continue
        if _DIGIT_GARBAGE_RE.fullmatch(line):
            continue
        line = _WS_RE.sub(" ", line)
        out_lines.append(line)
        previous_blank = False
    while out_lines and not out_lines[-1]:
        out_lines.pop()
    cleaned = "\n".join(out_lines)

    # Superscript-like footnote digits sometimes get glued onto 4-digit years
    # in headings/titles, e.g. "år 20311" when footnote "1 ..." appears later.
    footnote_ids = {m.group(1) for m in re.finditer(r"(?m)^([1-9])\s", cleaned)}
    for footnote_id in footnote_ids:
        cleaned = re.sub(rf"(\b\d{{4}}){footnote_id}\b", r"\1", cleaned)
    return cleaned


def _json_bytes(data: object) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


_SE_COMPARE_NORMALIZATION_RULES = (
    ComparisonNormalizationRule(
        name="se_compare_dash_glyph_equivalence",
        rule_class="presentation_cleanup",
        kind="translation",
        description="Project Swedish comparison text dash variants to ASCII hyphen.",
        translation=str.maketrans({"–": "-", "—": "-", "\u2212": "-"}),
    ),
    ComparisonNormalizationRule(
        name="se_compare_editorial_attribution_suffix",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore trailing Förordning attribution suffixes in comparison text.",
        pattern=re.compile(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$"),
    ),
    ComparisonNormalizationRule(
        name="se_compare_leading_section_number",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore publisher-leading section numbers before capitalized text.",
        pattern=re.compile(r"^\d+\s+(?=[A-ZÅÄÖ])"),
    ),
    ComparisonNormalizationRule(
        name="se_compare_inline_list_numbering",
        rule_class="presentation_cleanup",
        kind="regex",
        description="Ignore inline list numbering inserted after whitespace before lowercase text.",
        pattern=re.compile(r"(?<=\s)\d+\.\s+(?=[a-zåäö])"),
    ),
)


def _normalize_compare_text(text: str) -> str:
    normalized = normalize_comparison_text(text.strip(), _SE_COMPARE_NORMALIZATION_RULES).text
    return " ".join(normalized.split())


def _classify_replay_row(replay_text: str, post_text: str) -> str:
    replay_editorial = " ".join(re.sub(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$", "", replay_text.strip()).split())
    post_editorial = " ".join(re.sub(r"\s*Förordning\s+\(\d{4}:\d+\)\.\s*$", "", post_text.strip()).split())
    if replay_editorial == post_editorial and replay_text.strip() != post_text.strip():
        return "editorial_attribution_only"
    replay_norm = _normalize_compare_text(replay_text)
    post_norm = _normalize_compare_text(post_text)
    if replay_norm == post_norm and replay_text.strip() != post_text.strip():
        return "inline_numbering_only"
    table_markers = ("Uppgift lämnas av", "Uppgift lämnas om")
    if any(marker in replay_text for marker in table_markers) and any(marker in post_text for marker in table_markers):
        return "table_layout_mismatch"
    return "content_mismatch"


def _normalize_appendix_compare_text(text: str) -> str:
    normalized = _normalize_compare_text(text)
    normalized = re.sub(r"(?:(?<=\s)|^)\d+\.\s+(?=[A-ZÅÄÖ])", "", normalized)
    return " ".join(normalized.split())


def _normalize_jsonable(value: object) -> object:
    if isinstance(value, dict):
        return {str(k): _normalize_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize_jsonable(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_jsonable(v) for v in value]
    if hasattr(value, "value"):
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
    return value


def se_source_record_to_dict(source_record: SESourceRecord) -> dict[str, Any]:
    return cast(dict[str, Any], _normalize_jsonable(asdict(source_record)))


def se_source_bundle_to_dict(bundle: SESourceBundle) -> dict[str, Any]:
    return {
        "source_record": se_source_record_to_dict(bundle.source_record),
        "current_statute": bundle.current_statute.to_jsonable_dict(),
        "official_artifacts": _normalize_jsonable(asdict(bundle.official_artifacts))
        if bundle.official_artifacts is not None
        else None,
    }


def _curl_json_post(url: str, *, headers: list[str], payload: dict) -> Optional[bytes]:
    result = subprocess.run(
        [
            "curl",
            "-s",
            "--max-time",
            "30",
            url,
            *headers,
            "--data",
            json.dumps(payload, ensure_ascii=False),
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _curl_form_post(url: str, *, payload: dict[str, str]) -> Optional[bytes]:
    result = subprocess.run(
        [
            "curl",
            "-L",
            "-s",
            "--max-time",
            "30",
            "-H",
            "content-type: application/x-www-form-urlencoded",
            "--data",
            urlencode(payload),
            url,
        ],
        capture_output=True,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return result.stdout


def _curl_probe_bytes(url: str, *, byte_range: str | None = None) -> tuple[int | None, bytes]:
    cmd = ["curl", "-L", "-s", "--max-time", "20"]
    if byte_range:
        cmd.extend(["-r", byte_range])
    cmd.extend(["-o", "-", "-w", "\n%{http_code}", url])
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        return (None, b"")
    stdout = result.stdout
    if b"\n" not in stdout:
        return (None, stdout)
    body, _, code_bytes = stdout.rpartition(b"\n")
    try:
        status_code = int(code_bytes.decode("ascii", errors="replace").strip())
    except ValueError:
        status_code = None
    return (status_code, body)


def parse_se_rk_issue_date(rk_html: bytes | str) -> Optional[str]:
    if isinstance(rk_html, bytes):
        rk_html = rk_html.decode("utf-8", errors="replace")
    match = _RK_UTFARDAD_RE.search(rk_html)
    return match.group(1) if match else None


def guess_se_official_pdf_url(sfs_id: str, issue_date: str) -> str:
    year, number = sfs_id.split(":", 1)
    month = issue_date[:7]
    return f"https://svenskforfattningssamling.se/sites/default/files/sfs/{month}/SFS{year}-{int(number)}.pdf"


def guess_se_official_pdf_url_candidates(sfs_id: str) -> list[str]:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)", sfs_id.strip())
    if not match:
        return []
    year = match.group("year")
    number = int(match.group("number"))
    return [
        f"https://svenskforfattningssamling.se/sites/default/files/sfs/{year}-{month:02d}/SFS{year}-{number}.pdf"
        for month in range(1, 13)
    ]


def guess_se_legacy_pdf_url(sfs_id: str) -> str:
    match = re.fullmatch(r"(?P<year>\d{4}):(?P<number>\d+)", sfs_id.strip())
    if not match:
        raise ValueError(f"invalid Sweden SFS ID: {sfs_id!r}")
    year = match.group("year")
    year_short = year[2:]
    number = int(match.group("number"))
    return f"https://rkrattsdb.gov.se/SFSdoc/{year_short}/{year_short}{number:04d}.PDF"


def parse_se_legacy_pdf_url(search_html: bytes | str) -> str | None:
    if isinstance(search_html, bytes):
        text = search_html.decode("latin-1", errors="replace")
    else:
        text = search_html
    match = _LEGACY_SFSPDF_PDF_RE.search(text)
    if not match:
        return None
    return urljoin(se_legacy_sfspdf_index_url(), match.group("href"))


def search_se_legacy_pdf_url(sfs_id: str) -> str | None:
    html = _curl_form_post(
        se_legacy_sfspdf_search_url(),
        payload={
            "SFS_nr": sfs_id,
            "title": "",
            "author": "",
            "departement": "",
            "ACTION": "  Sök  ",
        },
    )
    if not html:
        return None
    return parse_se_legacy_pdf_url(html)


def _looks_like_pdf_bytes(data: bytes | None) -> bool:
    if not data:
        return False
    return data[:1024].lstrip().startswith(b"%PDF-")


def has_valid_se_official_pdf(archive: _ArchiveLike, sfs_id: str) -> bool:
    return _looks_like_pdf_bytes(archive.get(se_official_pdf_locator(sfs_id)))


def ingest_se_scraped_doc_html_map(
    payload: bytes | str | dict[str, str],
    archive: _ArchiveLike,
) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload
    else:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("expected scraped Sweden doc payload to decode to a JSON object")
        data = decoded

    imported = 0
    skipped = 0
    resolved_pdf_links = 0
    sfs_ids: list[str] = []
    skipped_entries: list[dict[str, Any]] = []
    for entry_index, (doc_url, html) in enumerate(data.items()):
        if not isinstance(doc_url, str) or not isinstance(html, str):
            skipped += 1
            skipped_entries.append(
                {
                    "rule_id": "se_scraped_doc_entry_invalid_shape",
                    "phase": "acquisition",
                    "family": "source_pathology",
                    "entry_index": entry_index,
                    "doc_url_type": type(doc_url).__name__,
                    "html_type": type(html).__name__,
                    "reason": "scraped Sweden document map entry did not have string URL and HTML values",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
            continue
        sfs_id = se_sfs_id_from_doc_url(doc_url)
        if not sfs_id:
            skipped += 1
            skipped_entries.append(
                {
                    "rule_id": "se_scraped_doc_entry_unrecognized_url",
                    "phase": "acquisition",
                    "family": "source_pathology",
                    "entry_index": entry_index,
                    "doc_url": doc_url,
                    "reason": "scraped Sweden document URL did not resolve to an SFS id",
                    "blocking": True,
                    "strict_disposition": "block",
                    "quirks_disposition": "record",
                }
            )
            continue
        html_bytes = html.encode("utf-8")
        archive.store(doc_url, html_bytes, storage_class="html")
        archive.store(se_official_doc_locator(sfs_id), html_bytes, storage_class="html")
        imported += 1
        sfs_ids.append(sfs_id)
        if parse_se_official_pdf_url(html_bytes, doc_url):
            resolved_pdf_links += 1

    return {
        "entry_count": len(data),
        "imported_count": imported,
        "skipped_count": skipped,
        "skipped_entries": skipped_entries,
        "resolved_pdf_link_count": resolved_pdf_links,
        "sfs_ids": sfs_ids,
    }


def probe_se_public_source_status(sfs_id: str) -> dict[str, Any]:
    doc_url = se_official_doc_url(sfs_id)
    doc_status_code, doc_body = _curl_probe_bytes(doc_url)
    doc_status = "unreachable"
    parsed_pdf_url = ""
    if doc_status_code == 200:
        parsed_pdf_url = parse_se_official_pdf_url(doc_body, doc_url) or ""
        doc_status = "pdf_link" if parsed_pdf_url else "html_no_pdf_link"
    elif doc_status_code == 403 and _CLOUDFLARE_BLOCK_RE.search(doc_body):
        doc_status = "cloudflare_blocked"
    elif doc_status_code == 404:
        doc_status = "not_found"
    elif doc_status_code is not None:
        doc_status = f"http_{doc_status_code}"

    pdf_status = "unreachable"
    resolved_pdf_url = ""
    candidate_urls = [parsed_pdf_url] if parsed_pdf_url else []
    candidate_urls.extend(
        candidate for candidate in guess_se_official_pdf_url_candidates(sfs_id) if candidate not in candidate_urls
    )
    for candidate_url in candidate_urls:
        status_code, body = _curl_probe_bytes(candidate_url, byte_range="0-1023")
        if status_code == 200 and _looks_like_pdf_bytes(body):
            resolved_pdf_url = candidate_url
            pdf_status = "valid_pdf"
            break
        if status_code == 404:
            pdf_status = "not_found"
            continue
        if status_code == 403 and _CLOUDFLARE_BLOCK_RE.search(body):
            pdf_status = "cloudflare_blocked"
            continue
        if status_code is not None:
            pdf_status = f"http_{status_code}"
    if not candidate_urls and pdf_status == "unreachable":
        pdf_status = "no_candidate"
    return {
        "doc_url": doc_url,
        "doc_status": doc_status,
        "pdf_status": pdf_status,
        "resolved_pdf_url": resolved_pdf_url,
        "public_source_viable": pdf_status == "valid_pdf",
    }


def attach_official_artifacts_to_bundle(
    bundle: SESourceBundle,
    official_artifacts: Optional[SEOfficialArtifacts],
) -> SESourceBundle:
    if official_artifacts is None:
        return bundle
    source_record = bundle.source_record
    if source_record.sfs_id == official_artifacts.sfs_id:
        source_record = replace(
            source_record,
            source_urls=replace(
                source_record.source_urls,
                official_sfs_doc_url=official_artifacts.doc_url,
                official_sfs_pdf_url=official_artifacts.pdf_url,
            ),
        )
    return SESourceBundle(
        source_record=source_record,
        current_statute=bundle.current_statute,
        official_artifacts=official_artifacts,
    )


def fetch_se_official_artifacts(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    force_reextract: bool = False,
    pdf_url_override: str | None = None,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> Optional[SEOfficialArtifacts]:
    """Fetch Sweden official doc page + PDF and archive extracted text.

    Cache policy:
    - official original-promulgation sources are treated as immutable by default
    - TTLs should only be used for list/current/consolidated surfaces

    Storage policy:
    - doc page HTML is cached by real HTTP URL and mirrored to `se://.../official.doc.html`
    - official PDF is cached by real HTTP URL and mirrored to `se://.../official.pdf`
    - extracted raw text is archived at `se://.../official.pdf.txt`
    - deterministic cleaned text is archived at `se://.../official.cleaned.txt`
    - structured parsed act text is archived at `se://.../official.act.json`
    """
    doc_url = se_official_doc_url(sfs_id)
    pdf_source_attempts: list[dict[str, str]] = []
    selected_pdf_lane = ""
    doc_html = _se_archive_fetch(archive, doc_url, max_age_hours=max_age_hours, storage_class="html")
    parsed_doc_pdf_url = parse_se_official_pdf_url(doc_html, doc_url) if doc_html else None
    doc_status = "pdf_link_found" if parsed_doc_pdf_url else ("no_pdf_link" if doc_html else "missing")
    if doc_html and parsed_doc_pdf_url:
        archive.store(se_official_doc_locator(sfs_id), doc_html, storage_class="html")

    pdf_url = parsed_doc_pdf_url
    if pdf_url:
        selected_pdf_lane = "official_doc_pdf_link"
    if not pdf_url and pdf_url_override:
        pdf_url = pdf_url_override
        selected_pdf_lane = "explicit_pdf_url_override"
    if not pdf_url:
        rk_html = _se_archive_fetch(
            archive,
            se_rk_current_url(sfs_id),
            max_age_hours=_CURRENT_SURFACE_CACHE_HOURS,
            storage_class="html",
        )
        if rk_html:
            issue_date = parse_se_rk_issue_date(rk_html)
            if issue_date:
                pdf_url = guess_se_official_pdf_url(sfs_id, issue_date)
                selected_pdf_lane = "rk_issue_date_guess"
    pdf_bytes = (
        _se_archive_fetch(archive, pdf_url, max_age_hours=max_age_hours, storage_class="pdf") if pdf_url else None
    )
    if pdf_url:
        pdf_source_attempts.append(
            {
                "lane": selected_pdf_lane or "unknown",
                "url": pdf_url,
                "status": "valid_pdf" if _looks_like_pdf_bytes(pdf_bytes) else "missing_or_non_pdf",
            }
        )
    if pdf_bytes is not None and not _looks_like_pdf_bytes(pdf_bytes):
        pdf_bytes = None
    if not pdf_bytes:
        legacy_direct_url = guess_se_legacy_pdf_url(sfs_id)
        legacy_direct_bytes = _se_archive_fetch(
            archive, legacy_direct_url, max_age_hours=max_age_hours, storage_class="pdf"
        )
        pdf_source_attempts.append(
            {
                "lane": "legacy_direct_guess",
                "url": legacy_direct_url,
                "status": "valid_pdf" if _looks_like_pdf_bytes(legacy_direct_bytes) else "missing_or_non_pdf",
            }
        )
        if _looks_like_pdf_bytes(legacy_direct_bytes):
            doc_url = se_legacy_sfspdf_index_url()
            pdf_url = legacy_direct_url
            pdf_bytes = legacy_direct_bytes
            selected_pdf_lane = "legacy_direct_guess"
    if not pdf_bytes:
        legacy_search_pdf_url = search_se_legacy_pdf_url(sfs_id)
        if not legacy_search_pdf_url:
            pdf_source_attempts.append(
                {
                    "lane": "legacy_search_result",
                    "url": se_legacy_sfspdf_search_url(),
                    "status": "no_result",
                }
            )
        else:
            legacy_search_bytes = _se_archive_fetch(
                archive, legacy_search_pdf_url, max_age_hours=max_age_hours, storage_class="pdf"
            )
            pdf_source_attempts.append(
                {
                    "lane": "legacy_search_result",
                    "url": legacy_search_pdf_url,
                    "status": "valid_pdf" if _looks_like_pdf_bytes(legacy_search_bytes) else "missing_or_non_pdf",
                }
            )
            if _looks_like_pdf_bytes(legacy_search_bytes):
                doc_url = se_legacy_sfspdf_search_url()
                pdf_url = legacy_search_pdf_url
                pdf_bytes = legacy_search_bytes
                selected_pdf_lane = "legacy_search_result"
    if not pdf_bytes:
        for candidate_url in guess_se_official_pdf_url_candidates(sfs_id):
            if candidate_url == pdf_url:
                continue
            candidate_bytes = _se_archive_fetch(
                archive, candidate_url, max_age_hours=max_age_hours, storage_class="pdf"
            )
            pdf_source_attempts.append(
                {
                    "lane": "official_month_probe",
                    "url": candidate_url,
                    "status": "valid_pdf" if _looks_like_pdf_bytes(candidate_bytes) else "missing_or_non_pdf",
                }
            )
            if _looks_like_pdf_bytes(candidate_bytes):
                pdf_url = candidate_url
                pdf_bytes = candidate_bytes
                selected_pdf_lane = "official_month_probe"
                break
    if not pdf_url or not pdf_bytes:
        _record_se_official_artifacts_diagnostic(
            diagnostics_out,
            rule_id="se_official_artifacts_unavailable",
            sfs_id=sfs_id,
            locator=se_official_pdf_locator(sfs_id),
            reason="Sweden official SFS PDF artifact could not be located or fetched",
            doc_url=doc_url,
            pdf_url=pdf_url,
            pdf_source_attempts=tuple(pdf_source_attempts),
        )
        return None
    if selected_pdf_lane not in {"", "official_doc_pdf_link", "explicit_pdf_url_override"}:
        _record_se_official_artifacts_diagnostic(
            diagnostics_out,
            rule_id="se_official_pdf_source_lane_fallback",
            sfs_id=sfs_id,
            locator=se_official_pdf_locator(sfs_id),
            reason="Sweden official SFS PDF was recovered through a fallback source lane",
            doc_url=doc_url,
            pdf_url=pdf_url,
            blocking=False,
            doc_status=doc_status,
            selected_pdf_lane=selected_pdf_lane,
            pdf_source_attempts=tuple(pdf_source_attempts),
        )
    archive.store(se_official_pdf_locator(sfs_id), pdf_bytes, storage_class="pdf")

    text_url = se_pdf_text_locator(sfs_id)
    cleaned_text_url = se_pdf_cleanup_locator(sfs_id)
    existing_text = archive.get(text_url)
    existing_cleaned = archive.get(cleaned_text_url)
    act_json_url = se_official_act_locator(sfs_id)
    if existing_text is not None and existing_cleaned is None and not force_reextract:
        archive.store(
            cleaned_text_url,
            clean_se_pdf_text(existing_text.decode("utf-8", errors="replace")).encode("utf-8"),
            storage_class="text",
        )
    elif existing_text is None or force_reextract or existing_cleaned is None:
        pdf_text = se_pdf_bytes_to_text(pdf_bytes)
        if pdf_text:
            archive.store(text_url, pdf_text.encode("utf-8"), storage_class="text")
            archive.store(cleaned_text_url, clean_se_pdf_text(pdf_text).encode("utf-8"), storage_class="text")
        else:
            _record_se_official_artifacts_diagnostic(
                diagnostics_out,
                rule_id="se_official_pdf_text_extraction_failed",
                sfs_id=sfs_id,
                locator=text_url,
                reason="Sweden official SFS PDF was fetched but text extraction produced no payload",
                doc_url=doc_url,
                pdf_url=pdf_url,
                phase="extraction",
            )

    cleaned_bytes = archive.get(cleaned_text_url)
    if cleaned_bytes is not None:
        act_text = parse_se_official_act_text(
            cleaned_bytes.decode("utf-8", errors="replace"),
            sfs_id=sfs_id,
        )
        archive.store(
            act_json_url,
            _json_bytes(se_official_act_text_to_dict(act_text)),
            storage_class="json",
        )
        if not act_text.is_amending_act:
            try:
                base_statute = build_se_official_base_statute(se_official_act_text_to_dict(act_text), statute_id=sfs_id)
            except ValueError as exc:
                _record_se_official_artifacts_diagnostic(
                    diagnostics_out,
                    rule_id="se_official_base_ir_build_failed",
                    sfs_id=sfs_id,
                    locator=se_official_base_ir_locator(sfs_id),
                    reason="Sweden official act text was parsed but base IR construction failed",
                    doc_url=doc_url,
                    pdf_url=pdf_url,
                    phase="extraction",
                    exception_type=type(exc).__name__,
                )
            else:
                archive.store(
                    se_official_base_ir_locator(sfs_id),
                    _json_bytes(base_statute.to_jsonable_dict()),
                    storage_class="json",
                )

    artifacts = SEOfficialArtifacts(
        sfs_id=sfs_id,
        doc_url=doc_url,
        doc_locator=se_official_doc_locator(sfs_id),
        pdf_url=pdf_url,
        pdf_locator=se_official_pdf_locator(sfs_id),
        pdf_text_url=text_url,
        pdf_cleaned_text_url=cleaned_text_url,
    )
    archive_se_official_artifacts_manifest(archive, artifacts)
    return artifacts


def _record_se_official_artifacts_diagnostic(
    diagnostics_out: list[dict[str, Any]] | None,
    *,
    rule_id: str,
    sfs_id: str,
    locator: str,
    reason: str,
    doc_url: str,
    pdf_url: str | None,
    phase: str = "acquisition",
    exception_type: str = "",
    blocking: bool = True,
    doc_status: str = "",
    selected_pdf_lane: str = "",
    pdf_source_attempts: tuple[dict[str, str], ...] = (),
) -> None:
    if diagnostics_out is None:
        return
    diagnostic = diagnostic_detail(
        rule_id=rule_id,
        family="source_pathology",
        phase=phase,
        reason=reason,
        blocking=blocking,
        sfs_id=sfs_id,
        locator=locator,
        doc_url=doc_url,
        pdf_url=pdf_url or "",
    )
    if exception_type:
        diagnostic["exception_type"] = exception_type
    if doc_status:
        diagnostic["doc_status"] = doc_status
    if selected_pdf_lane:
        diagnostic["selected_pdf_lane"] = selected_pdf_lane
    if pdf_source_attempts:
        diagnostic["pdf_source_attempts"] = pdf_source_attempts
    diagnostics_out.append(diagnostic)


def fetch_se_rk_current_json(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    max_age_hours: float = _CURRENT_SURFACE_CACHE_HOURS,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> Optional[bytes]:
    locator = se_rk_current_json_locator(sfs_id)
    if archive.has(locator, max_age_hours=max_age_hours):
        cached = archive.get(locator)
        if cached is not None:
            return cached

    url = "https://beta.rkrattsbaser.gov.se/elasticsearch/SearchEsByRawJson"
    payload = {
        "searchIndexes": ["Sfs"],
        "api": "search",
        "json": {
            "query": {
                "bool": {
                    "must": [
                        {"term": {"beteckning.keyword": sfs_id}},
                        {"term": {"publicerad": True}},
                    ]
                }
            },
            "size": 1,
        },
    }
    raw = _curl_json_post(
        url,
        headers=[
            "-H",
            "content-type: application/json",
            "-H",
            f"referer: https://beta.rkrattsbaser.gov.se/sfs/item?bet={sfs_id.replace(':', '%3A')}&tab=forfattningstext",
        ],
        payload=payload,
    )
    if raw is None:
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_fetch_failed",
            sfs_id=sfs_id,
            locator=locator,
            phase="acquisition",
            reason="Sweden RK current JSON request returned no payload",
        )
        return None
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_json",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response could not be decoded",
        )
        return None
    if not isinstance(decoded, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_root",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response root was not an object",
        )
        return None
    hits_parent = decoded.get("hits")
    if not isinstance(hits_parent, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_missing_hits_container",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response did not contain a hits object",
        )
        return None
    hits = hits_parent.get("hits")
    if not isinstance(hits, list) or not hits:
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_no_hits",
            sfs_id=sfs_id,
            locator=locator,
            phase="acquisition",
            reason="Sweden RK current JSON response contained no published SFS hit",
        )
        return None
    first_hit = hits[0]
    if not isinstance(first_hit, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_hit",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response first hit was not an object",
        )
        return None
    source = first_hit.get("_source")
    if not isinstance(source, dict):
        _record_se_rk_current_diagnostic(
            diagnostics_out,
            rule_id="se_rk_current_invalid_source",
            sfs_id=sfs_id,
            locator=locator,
            phase="parse",
            reason="Sweden RK current JSON response first hit did not contain an object _source",
        )
        return None

    current_json = _json_bytes(source)
    archive.store(locator, current_json, storage_class="json")
    return current_json


def _record_se_rk_current_diagnostic(
    diagnostics_out: list[dict[str, Any]] | None,
    *,
    rule_id: str,
    sfs_id: str,
    locator: str,
    phase: str,
    reason: str,
) -> None:
    if diagnostics_out is None:
        return
    diagnostics_out.append(
        diagnostic_detail(
            rule_id=rule_id,
            family="source_pathology",
            phase=phase,
            reason=reason,
            blocking=True,
            sfs_id=sfs_id,
            locator=locator,
        )
    )


def build_se_source_bundle(
    payload: bytes | str | dict,
    *,
    doc_html: bytes | str | None = None,
) -> SESourceBundle:
    """Build the first Sweden bundle from current-text JSON and optional doc HTML."""
    source_record = parse_se_source_record(payload)
    if doc_html is not None:
        source_record = enrich_se_source_record_with_doc_page(source_record, doc_html)
    current_statute = parse_se_statute(payload)
    return SESourceBundle(
        source_record=source_record,
        current_statute=current_statute,
        official_artifacts=None,
    )


def archive_se_source_bundle(
    payload: bytes | str | dict,
    archive: _ArchiveLike,
    *,
    doc_html: bytes | str | None = None,
) -> SESourceBundle:
    """Archive Sweden current-source artifacts from local JSON and optional doc HTML.

    Stored artifacts:
    - `se://.../rk.current.json`
    - `se://.../source_record.json`
    - `se://.../current.ir.json`
    - `se://.../bundle.json`
    - `se://.../official.doc.html` when doc HTML is provided
    """
    bundle = build_se_source_bundle(payload, doc_html=doc_html)
    sfs_id = bundle.source_record.sfs_id

    archive.store(
        se_rk_current_json_locator(sfs_id),
        _json_bytes(_normalize_jsonable(_coerce_payload_to_dict(payload))),
        storage_class="json",
    )
    archive.store(
        se_source_record_locator(sfs_id),
        _json_bytes(se_source_record_to_dict(bundle.source_record)),
        storage_class="json",
    )
    archive.store(
        se_current_ir_locator(sfs_id),
        _json_bytes(bundle.current_statute.to_jsonable_dict()),
        storage_class="json",
    )
    archive.store(
        se_bundle_manifest_locator(sfs_id), _json_bytes(se_source_bundle_to_dict(bundle)), storage_class="json"
    )

    if doc_html is not None:
        if isinstance(doc_html, str):
            doc_bytes = doc_html.encode("utf-8")
        else:
            doc_bytes = doc_html
        archive.store(se_official_doc_locator(sfs_id), doc_bytes, storage_class="html")

    return bundle


def _read_json_locator(archive: _ArchiveLike, locator: str) -> Optional[dict]:
    raw = archive.get(locator)
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"archive locator {locator} did not decode to a JSON object")
    return data


def load_se_source_record_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_source_record_locator(sfs_id))


def load_se_current_ir_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_current_ir_locator(sfs_id))


def load_se_bundle_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_bundle_manifest_locator(sfs_id))


def load_se_official_act_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_act_locator(sfs_id))


def load_se_official_base_ir_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_base_ir_locator(sfs_id))


def load_se_official_clause_surface_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_clause_surface_locator(sfs_id))


def load_se_official_payload_surface_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_payload_surface_locator(sfs_id))


def load_se_official_elaboration_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_elaboration_locator(sfs_id))


def load_se_official_effects_plan_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[dict]:
    return _read_json_locator(archive, se_official_effects_plan_locator(sfs_id))


def load_se_backfill_official_checkpoint_from_archive(archive: _ArchiveLike) -> Optional[dict]:
    return _read_json_locator(archive, se_backfill_official_checkpoint_locator())


def load_se_backfill_official_status_from_archive(archive: _ArchiveLike) -> Optional[dict]:
    return _read_json_locator(archive, se_backfill_official_status_locator())


def load_se_backfill_official_history_from_archive(archive: _ArchiveLike) -> Optional[list[dict]]:
    raw = archive.get(se_backfill_official_history_locator())
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"archive locator {se_backfill_official_history_locator()} did not decode to a JSON array")
    malformed_indexes = [index for index, item in enumerate(data) if not isinstance(item, dict)]
    if malformed_indexes:
        indexes = ", ".join(str(index) for index in malformed_indexes)
        raise ValueError(
            f"archive locator {se_backfill_official_history_locator()} contains non-object entries at indexes: {indexes}"
        )
    return data


def load_se_backfill_official_completeness_from_archive(archive: _ArchiveLike) -> Optional[dict]:
    return _read_json_locator(archive, se_backfill_official_completeness_locator())


def load_se_backfill_official_gap_report_from_archive(archive: _ArchiveLike) -> Optional[dict]:
    return _read_json_locator(archive, se_backfill_official_gap_report_locator())


def load_se_backfill_official_chunk_plan_from_archive(archive: _ArchiveLike) -> Optional[dict]:
    return _read_json_locator(archive, se_backfill_official_chunk_plan_locator())


def compile_se_official_ops_to_archive(archive: _ArchiveLike, sfs_id: str) -> list[dict]:
    act = load_se_official_act_from_archive(archive, sfs_id)
    if act is None:
        raise FileNotFoundError(f"no archived official act surface for {sfs_id}")
    act_surface = _coerce_official_act(act)
    clause_surface = _build_se_official_clause_surface(act_surface)
    payload_surface = _build_se_official_payload_surface(act_surface)
    elaboration = _build_se_official_elaboration(act_surface)
    effects_plan = _build_se_official_effects_plan(elaboration)
    archive.store(
        se_official_clause_surface_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_clause_surface_to_dict(clause_surface))),
        storage_class="json",
    )
    archive.store(
        se_official_payload_surface_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_payload_surface_to_dict(payload_surface))),
        storage_class="json",
    )
    archive.store(
        se_official_elaboration_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_elaboration_to_dict(elaboration))),
        storage_class="json",
    )
    archive.store(
        se_official_effects_plan_locator(sfs_id),
        _json_bytes(_normalize_jsonable(se_official_effect_plan_to_dict(effects_plan))),
        storage_class="json",
    )
    adjudications: list[CompileAdjudication] = []
    try:
        ops = _lower_se_official_effects_plan(effects_plan, source_id=sfs_id, adjudications_out=adjudications)
    except NotImplementedError:
        archive.store(
            se_official_ops_adjudications_locator(sfs_id),
            _json_bytes(_normalize_jsonable([asdict(item) for item in adjudications])),
            storage_class="json",
        )
        archive_se_official_phase_artifacts_manifest(archive, sfs_id)
        raise
    ops_json = [se_legal_operation_to_dict(op) for op in ops]
    archive.store(se_official_ops_locator(sfs_id), _json_bytes(ops_json), storage_class="json")
    archive.store(
        se_official_ops_adjudications_locator(sfs_id),
        _json_bytes(_normalize_jsonable([asdict(item) for item in adjudications])),
        storage_class="json",
    )
    archive_se_official_phase_artifacts_manifest(archive, sfs_id)
    return ops_json


def archive_se_backfill_official_checkpoint(archive: _ArchiveLike, checkpoint: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_checkpoint_locator(),
        _json_bytes(_normalize_jsonable(checkpoint)),
        storage_class="json",
    )


def archive_se_backfill_official_status(archive: _ArchiveLike, status: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_status_locator(),
        _json_bytes(_normalize_jsonable(status)),
        storage_class="json",
    )


def archive_se_backfill_official_history(archive: _ArchiveLike, history: list[dict[str, Any]]) -> None:
    archive.store(
        se_backfill_official_history_locator(),
        _json_bytes(_normalize_jsonable(history)),
        storage_class="json",
    )


def archive_se_backfill_official_completeness(archive: _ArchiveLike, completeness: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_completeness_locator(),
        _json_bytes(_normalize_jsonable(completeness)),
        storage_class="json",
    )


def archive_se_backfill_official_gap_report(archive: _ArchiveLike, gap_report: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_gap_report_locator(),
        _json_bytes(_normalize_jsonable(gap_report)),
        storage_class="json",
    )


def archive_se_backfill_official_chunk_plan(archive: _ArchiveLike, chunk_plan: dict[str, Any]) -> None:
    archive.store(
        se_backfill_official_chunk_plan_locator(),
        _json_bytes(_normalize_jsonable(chunk_plan)),
        storage_class="json",
    )


def load_se_official_ops_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[list[dict]]:
    raw = archive.get(se_official_ops_locator(sfs_id))
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"archive locator {se_official_ops_locator(sfs_id)} did not decode to a JSON array")
    non_object_indexes = [
        str(index)
        for index, item in enumerate(data)
        if not isinstance(item, dict)
    ]
    if non_object_indexes:
        indexes = ", ".join(non_object_indexes)
        raise ValueError(
            f"archive locator {se_official_ops_locator(sfs_id)} contained non-object op entries at indexes: {indexes}"
        )
    return data


def load_se_official_ops_adjudications_from_archive(archive: _ArchiveLike, sfs_id: str) -> Optional[list[dict]]:
    raw = archive.get(se_official_ops_adjudications_locator(sfs_id))
    if raw is None:
        return None
    data = json.loads(raw.decode("utf-8"))
    if not isinstance(data, list):
        raise ValueError(
            f"archive locator {se_official_ops_adjudications_locator(sfs_id)} did not decode to a JSON array"
        )
    non_object_indexes = [
        str(index)
        for index, item in enumerate(data)
        if not isinstance(item, dict)
    ]
    if non_object_indexes:
        indexes = ", ".join(non_object_indexes)
        raise ValueError(
            f"archive locator {se_official_ops_adjudications_locator(sfs_id)} "
            f"contained non-object adjudication entries at indexes: {indexes}"
        )
    return data


def _detect_se_current_surface_contamination(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
    *,
    amending_sfs_id: str = "",
) -> list[dict[str, str]]:
    pre_section_texts = se_section_text_map(pre_statute)
    pre_sections = set(pre_section_texts)
    pre_headings = set(se_heading_before_section_map(pre_statute))
    pre_appendix_texts = se_appendix_text_map(pre_statute)
    pre_appendices = set(pre_appendix_texts)

    def _provenance_fields(text: str) -> dict[str, str]:
        normalized = " ".join(text.split())
        placeholder_match = _SE_RENUMBER_PLACEHOLDER_SFS_RE.search(normalized)
        attribution_match = _SE_ATTRIBUTION_SFS_RE.search(normalized)
        source_sfs_id = ""
        origin_hint = "unknown"
        if placeholder_match:
            source_sfs_id = placeholder_match.group(1)
            origin_hint = "renumber_placeholder"
        elif attribution_match:
            source_sfs_id = attribution_match.group(1)
            origin_hint = "trailing_attribution"
        reverse_patch_candidate = (
            "yes"
            if amending_sfs_id and source_sfs_id == amending_sfs_id
            else ("unknown" if not source_sfs_id else "no")
        )
        return {
            "source_sfs_id": source_sfs_id,
            "origin_hint": origin_hint,
            "reverse_patch_candidate": reverse_patch_candidate,
        }

    issues: list[dict[str, str]] = []
    for op in ops:
        if op.target.leaf_kind() == "section":
            if op.action is StructuralAction.INSERT and op.target.special is not FacetKind.HEADING:
                label = op.target.leaf_label()
                if label in pre_sections:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                            **_provenance_fields(pre_section_texts.get(label, "")),
                        }
                    )
            elif op.action is StructuralAction.RENUMBER and op.destination is not None:
                destination_label = op.destination.leaf_label()
                if destination_label in pre_sections:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": destination_label,
                            "issue": "preexisting_renumber_destination",
                            "action": op.action.value,
                            **_provenance_fields(pre_section_texts.get(destination_label, "")),
                        }
                    )
            elif op.target.special is FacetKind.HEADING and op.action is StructuralAction.INSERT:
                label = op.target.leaf_label()
                if label in pre_headings:
                    issues.append(
                        {
                            "target_kind": "heading",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                            "source_sfs_id": "",
                            "origin_hint": "unknown",
                            "reverse_patch_candidate": "unknown",
                        }
                    )
        elif op.target.leaf_kind() == "appendix" and op.action is StructuralAction.INSERT:
            label = op.target.leaf_label()
            if label in pre_appendices:
                issues.append(
                    {
                        "target_kind": "appendix",
                        "label": label,
                        "issue": "preexisting_insert_target",
                        "action": op.action.value,
                        **_provenance_fields(pre_appendix_texts.get(label, "")),
                    }
                )
    return issues


def _parse_se_sfs_sort_key(sfs_id: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d{4}):(\d+)", sfs_id.strip())
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2)))


def _classify_se_historical_recovery_strategy(
    amending_sfs_id: str,
    contamination: list[dict[str, str]],
    self_reverse_residual: list[dict[str, str]],
    later_reverse_residual: list[dict[str, str]],
) -> str:
    if not contamination:
        return "direct_replay"
    if not self_reverse_residual:
        return "self_reverse_only"
    if not later_reverse_residual:
        return "available_later_reverse_chain"
    residual_sources = {
        str(item.get("source_sfs_id") or "") for item in later_reverse_residual if str(item.get("source_sfs_id") or "")
    }
    if residual_sources and all(
        _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id) for source in residual_sources
    ):
        return "later_reverse_chain"
    return "older_base_required"


def _build_se_later_chain_hints(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    self_reverse_residual: list[dict[str, str]],
) -> list[dict[str, Any]]:
    later_sources = sorted(
        {
            source
            for item in self_reverse_residual
            if (source := str(item.get("source_sfs_id") or ""))
            and _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id)
        },
        key=_parse_se_sfs_sort_key,
    )
    hints: list[dict[str, Any]] = []
    for source in later_sources:
        hints.append(
            {
                "sfs_id": source,
                "official_act_available": load_se_official_act_from_archive(archive, source) is not None,
                "pdf_available": has_valid_se_official_pdf(archive, source),
                "doc_available": archive.get(se_official_doc_locator(source)) is not None,
            }
        )
    return hints


def _clone_irnode_with_label(node: IRNode, label: str) -> IRNode:
    return IRNode(
        kind=node.kind,
        label=label,
        text=node.text,
        attrs=dict(node.attrs),
        children=tuple(node.children),
    )


def _reverse_patch_se_self_contamination(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
    contamination: list[dict[str, str]],
) -> IRStatute:
    body = pre_statute.body
    supplements = list(pre_statute.supplements)
    for item in contamination:
        if str(item.get("reverse_patch_candidate") or "") != "yes":
            continue
        target_kind = str(item.get("target_kind") or "")
        label = str(item.get("label") or "")
        issue = str(item.get("issue") or "")
        action = str(item.get("action") or "")
        if target_kind == "section" and issue == "preexisting_insert_target" and action == "insert":
            path = tree_ops.find(body, "section", label)
            if path is not None:
                body = tree_ops.remove_at(body, path)
            continue
        if target_kind == "section" and issue == "preexisting_renumber_destination" and action == "renumber":
            matching = next(
                (
                    op
                    for op in ops
                    if op.action is StructuralAction.RENUMBER and op.destination is not None and op.destination.leaf_label() == label
                ),
                None,
            )
            if matching is None:
                continue
            path = tree_ops.find(body, "section", label)
            if path is None:
                continue
            node = tree_ops.resolve(body, path)
            if node is None:
                continue
            body = tree_ops.replace_at(body, path, _clone_irnode_with_label(node, matching.target.leaf_label()))
            continue
        if target_kind == "appendix" and issue == "preexisting_insert_target" and action == "insert":
            supplements = [
                supplement
                for supplement in supplements
                if not (supplement.kind is IRNodeKind.APPENDIX and (supplement.label or "") == label)
            ]
    metadata = dict(pre_statute.metadata)
    metadata["self_reverse_patch_applied"] = True
    return IRStatute(
        statute_id=pre_statute.statute_id,
        title=pre_statute.title,
        body=body,
        supplements=supplements,
        metadata=metadata,
    )


def _invert_se_reversible_ops(ops: list[LegalOperation], *, source_sfs_id: str) -> list[LegalOperation]:
    inverse_ops: list[LegalOperation] = []
    next_sequence = 1
    for op in reversed(ops):
        inverse: LegalOperation | None = None
        if op.target.leaf_kind() == "section":
            if op.target.special is FacetKind.HEADING and op.action is StructuralAction.INSERT:
                inverse = LegalOperation(
                    op_id=f"se_reverse_heading_{source_sfs_id}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.REPEAL,
                    target=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
            elif op.action is StructuralAction.INSERT:
                inverse = LegalOperation(
                    op_id=f"se_reverse_insert_{source_sfs_id}_{op.target.leaf_label()}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.REPEAL,
                    target=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
            elif op.action is StructuralAction.RENUMBER and op.destination is not None:
                inverse = LegalOperation(
                    op_id=f"se_reverse_renumber_{source_sfs_id}_{op.destination.leaf_label()}_{next_sequence}",
                    sequence=next_sequence,
                    action=StructuralAction.RENUMBER,
                    target=op.destination,
                    destination=op.target,
                    source=op.source,
                    provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                    group_id=f"se_reverse_chain::{source_sfs_id}",
                )
        elif op.target.leaf_kind() == "appendix" and op.action is StructuralAction.INSERT:
            inverse = LegalOperation(
                op_id=f"se_reverse_appendix_{source_sfs_id}_{op.target.leaf_label()}_{next_sequence}",
                sequence=next_sequence,
                action=StructuralAction.REPEAL,
                target=op.target,
                source=op.source,
                provenance_tags=("sweden_later_chain_reverse_v1", f"source_sfs_id={source_sfs_id}"),
                group_id=f"se_reverse_chain::{source_sfs_id}",
            )
        if inverse is not None:
            inverse_ops.append(inverse)
            next_sequence += 1
    return inverse_ops


def _reverse_patch_se_available_later_chain(
    archive: _ArchiveLike,
    pre_statute: IRStatute,
    amending_sfs_id: str,
    self_reverse_residual: list[dict[str, str]],
) -> IRStatute:
    later_sources = sorted(
        {
            source
            for item in self_reverse_residual
            if (source := str(item.get("source_sfs_id") or ""))
            and _parse_se_sfs_sort_key(source) > _parse_se_sfs_sort_key(amending_sfs_id)
        },
        key=_parse_se_sfs_sort_key,
        reverse=True,
    )
    statute = pre_statute
    reverse_adjudications: list[CompileAdjudication] = []
    for source in later_sources:
        ops_json = load_se_official_ops_from_archive(archive, source)
        if ops_json is None and load_se_official_act_from_archive(archive, source) is not None:
            try:
                ops_json = compile_se_official_ops_to_archive(archive, source)
            except (FileNotFoundError, NotImplementedError, ValueError):
                ops_json = None
        if not ops_json:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        inverse_ops = _invert_se_reversible_ops(later_ops, source_sfs_id=source)
        if not inverse_ops:
            continue
        for inverse_op in inverse_ops:
            if inverse_op.action is StructuralAction.RENUMBER and inverse_op.destination is not None:
                destination_label = inverse_op.destination.leaf_label()
                existing_text = se_section_text_map(statute).get(destination_label, "")
                placeholder_match = _SE_RENUMBER_PLACEHOLDER_SFS_RE.search(" ".join(existing_text.split()))
                if placeholder_match and placeholder_match.group(1) == source:
                    placeholder_path = tree_ops.find(statute.body, "section", destination_label)
                    if placeholder_path is not None:
                        body = tree_ops.remove_at(statute.body, placeholder_path)
                        statute = IRStatute(
                            statute_id=statute.statute_id,
                            title=statute.title,
                            body=body,
                            supplements=list(statute.supplements),
                            metadata=dict(statute.metadata),
                        )
            try:
                before_adjudication_count = len(reverse_adjudications)
                statute = apply_se_ops(statute, [inverse_op], adjudications_out=reverse_adjudications)
                if len(reverse_adjudications) > before_adjudication_count:
                    latest = reverse_adjudications[-1]
                    latest_detail = dict(latest.detail)
                    latest_detail.setdefault("reverse_source_sfs_id", source)
                    reverse_adjudications[-1] = CompileAdjudication(
                        kind=latest.kind,
                        message=latest.message,
                        source_statute=latest.source_statute,
                        op_id=latest.op_id,
                        detail=latest_detail,
                    )
            except (LookupError, NotImplementedError, ValueError) as exc:
                reverse_adjudications.append(
                    CompileAdjudication(
                        kind="se_later_chain_reverse_op_exception",
                        message="Sweden later-chain reverse patch skipped an inverse operation after replay raised.",
                        source_statute=f"se/{source}",
                        op_id=inverse_op.op_id,
                        detail={
                            "rule_id": "se_later_chain_reverse_op_exception",
                            "phase": "replay",
                            "family": "target_resolution_recovery",
                            "blocking": True,
                            "strict_disposition": "block",
                            "quirks_disposition": "record",
                            "reverse_source_sfs_id": source,
                            "action": inverse_op.action.value,
                            "target": inverse_op.target.leaf_label(),
                            "exception_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                )
                continue
    metadata = dict(statute.metadata)
    metadata["later_chain_reverse_applied"] = True
    if reverse_adjudications:
        metadata["later_chain_reverse_adjudications"] = [
            {
                "kind": adjudication.kind,
                "message": adjudication.message,
                "source_statute": adjudication.source_statute,
                "op_id": adjudication.op_id,
                "detail": adjudication.detail,
            }
            for adjudication in reverse_adjudications
        ]
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=statute.body,
        supplements=list(statute.supplements),
        metadata=metadata,
    )


def _has_se_noninvertible_placeholder_blocker(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    residual_items: list[dict[str, str]],
) -> bool:
    for item in residual_items:
        source_sfs_id = str(item.get("source_sfs_id") or "")
        if not source_sfs_id or _parse_se_sfs_sort_key(source_sfs_id) <= _parse_se_sfs_sort_key(amending_sfs_id):
            continue
        label = str(item.get("label") or "")
        ops_json = load_se_official_ops_from_archive(archive, source_sfs_id)
        if ops_json is None and load_se_official_act_from_archive(archive, source_sfs_id) is not None:
            try:
                ops_json = compile_se_official_ops_to_archive(archive, source_sfs_id)
            except (FileNotFoundError, NotImplementedError, ValueError):
                ops_json = None
        if not ops_json:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        destination_labels = {
            op.destination.leaf_label()
            for op in later_ops
            if op.action is StructuralAction.RENUMBER and op.destination is not None and op.target.leaf_label() == label
        }
        if not destination_labels:
            continue
        repealed_labels = {
            op.target.leaf_label() for op in later_ops if op.action is StructuralAction.REPEAL and op.target.leaf_kind() == "section"
        }
        if destination_labels & repealed_labels:
            return True
    return False


def _detect_se_replay_precondition_issues(
    pre_statute: IRStatute,
    ops: list[LegalOperation],
) -> list[dict[str, str]]:
    section_labels = set(se_section_text_map(pre_statute))
    appendix_labels = set(se_appendix_text_map(pre_statute))
    issues: list[dict[str, str]] = []
    for op in ops:
        leaf_kind = op.target.leaf_kind()
        label = op.target.leaf_label()
        if leaf_kind == "section":
            if op.target.special is FacetKind.HEADING:
                if label not in section_labels:
                    issues.append(
                        {
                            "target_kind": "heading",
                            "label": label,
                            "issue": "missing_heading_anchor_section",
                            "action": op.action.value,
                        }
                    )
                continue
            if op.action is StructuralAction.INSERT:
                if label in section_labels:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                        }
                    )
                else:
                    section_labels.add(label)
                continue
            if op.action is StructuralAction.RENUMBER:
                destination_label = op.destination.leaf_label() if op.destination is not None else ""
                if label not in section_labels:
                    issues.append(
                        {
                            "target_kind": "section",
                            "label": label,
                            "issue": "missing_renumber_source",
                            "action": op.action.value,
                        }
                    )
                else:
                    section_labels.discard(label)
                    if destination_label:
                        section_labels.add(destination_label)
                continue
            if op.action.value in {"replace", "repeal"} and label not in section_labels:
                issues.append(
                    {
                        "target_kind": "section",
                        "label": label,
                        "issue": f"missing_{op.action.value}_source",
                        "action": op.action.value,
                    }
                )
            elif op.action.value == "repeal":
                section_labels.discard(label)
                continue
        elif leaf_kind == "appendix":
            if op.action.value == "insert":
                if label in appendix_labels:
                    issues.append(
                        {
                            "target_kind": "appendix",
                            "label": label,
                            "issue": "preexisting_insert_target",
                            "action": op.action.value,
                        }
                    )
                else:
                    appendix_labels.add(label)
                continue
            if op.action.value in {"replace", "repeal"} and label not in appendix_labels:
                issues.append(
                    {
                        "target_kind": "appendix",
                        "label": label,
                        "issue": f"missing_{op.action.value}_source",
                        "action": op.action.value,
                    }
                )
            elif op.action.value == "repeal":
                appendix_labels.discard(label)
    return issues


def _scope_mentions_se_label(scope_text: str, label: str) -> bool:
    normalized = " ".join(scope_text.lower().replace("§", " ").split())
    match = re.fullmatch(r"(\d+)([a-z]?)", label.lower())
    if not match:
        return False
    number = match.group(1)
    suffix = match.group(2)
    if suffix:
        return re.search(rf"\b{number}\s*{suffix}\b", normalized) is not None
    return re.search(rf"\b{number}\b", normalized) is not None


def _build_se_replay_precondition_ancestry_hints(
    archive: _ArchiveLike,
    current_json: bytes,
    amending_sfs_id: str,
    effective_date: str,
    precondition_issues: list[dict[str, str]],
    later_chain_hints: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    source_record = parse_se_source_record(current_json)
    hints: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for chain_hint in later_chain_hints:
        source_sfs_id = str(chain_hint.get("sfs_id") or "")
        if not source_sfs_id or not bool(chain_hint.get("official_act_available")):
            continue
        ops_json = load_se_official_ops_from_archive(archive, source_sfs_id)
        if ops_json is None:
            continue
        later_ops = [se_legal_operation_from_dict(op) for op in ops_json]
        renumber_map = {
            op.destination.leaf_label(): op.target.leaf_label()
            for op in later_ops
            if op.action.value == "renumber" and op.destination is not None
        }
        for issue in precondition_issues:
            label = str(issue.get("label") or "")
            derived_from_label = renumber_map.get(label, "")
            direct_later_actions = sorted(
                {
                    (
                        "renumber_destination"
                        if op.action.value == "renumber"
                        and op.destination is not None
                        and op.destination.leaf_label() == label
                        else op.action.value
                    )
                    for op in later_ops
                    if (
                        op.target.leaf_kind() == str(issue.get("target_kind") or "") and op.target.leaf_label() == label
                    )
                    or (
                        op.action.value == "renumber"
                        and op.destination is not None
                        and op.destination.leaf_label() == label
                    )
                }
            )
            if not derived_from_label:
                if not direct_later_actions:
                    continue
                derived_from_label = ""
            candidate_chain_sfs_ids = [
                entry.amending_sfs_id
                for entry in source_record.amendment_register
                if entry.amending_sfs_id
                and entry.amending_sfs_id != source_sfs_id
                and entry.effective_date
                and entry.effective_date > effective_date
                and _scope_mentions_se_label(entry.scope_text, derived_from_label)
            ]
            key = (label, derived_from_label, source_sfs_id)
            if key in seen:
                continue
            seen.add(key)
            hints.append(
                {
                    "label": label,
                    "issue": str(issue.get("issue") or ""),
                    "derived_from_label": derived_from_label,
                    "via_later_source": source_sfs_id,
                    "direct_later_actions": direct_later_actions,
                    "noninvertible_blocker": any(action in {"repeal", "replace"} for action in direct_later_actions),
                    "candidate_chain_sfs_ids": candidate_chain_sfs_ids,
                }
            )
    return hints


def _infer_se_effective_date_from_base_register(
    current_json: bytes,
    amending_sfs_id: str,
) -> str:
    source_record = parse_se_source_record(current_json)
    for entry in source_record.amendment_register:
        if entry.amending_sfs_id == amending_sfs_id and entry.effective_date:
            return entry.effective_date
    return ""


def _older_base_chain_entries(
    current_json: bytes,
    *,
    pre_date: str,
    exclude_sfs_id: str,
) -> list[dict[str, str]]:
    source_record = parse_se_source_record(current_json)
    rows: list[dict[str, str]] = []
    for entry in source_record.amendment_register:
        if not entry.amending_sfs_id or entry.amending_sfs_id == exclude_sfs_id:
            continue
        if not entry.effective_date or entry.effective_date > pre_date:
            continue
        rows.append(
            {
                "sfs_id": entry.amending_sfs_id,
                "effective_date": entry.effective_date,
                "title": entry.amending_title,
                "scope_text": entry.scope_text,
            }
        )
    rows.sort(
        key=lambda item: (str(item.get("effective_date") or ""), _parse_se_sfs_sort_key(str(item.get("sfs_id") or "")))
    )
    return rows


def _se_rebuild_chain_blocker_diagnostic(row: dict[str, Any]) -> dict[str, Any] | None:
    ops_status = str(row.get("ops_status") or "")
    if ops_status == "compiled":
        return None
    match ops_status:
        case "missing_official_act":
            rule_id = "se_official_rebuild_chain_missing_official_act"
            phase = "acquisition"
            reason = "prior Sweden amendment official act is unavailable"
        case "unsupported":
            rule_id = "se_official_rebuild_chain_ops_unsupported"
            phase = "lowering"
            reason = "prior Sweden amendment official act uses unsupported effect shape"
        case "invalid_official_act":
            rule_id = "se_official_rebuild_chain_invalid_official_act"
            phase = "extraction"
            reason = "prior Sweden amendment official act could not be parsed into replayable operations"
        case _:
            rule_id = "se_official_rebuild_chain_unknown_ops_status"
            phase = "replay_planning"
            reason = "prior Sweden amendment has an unknown rebuild-chain status"
    return {
        "rule_id": rule_id,
        "phase": phase,
        "family": "source_pathology",
        "blocking": True,
        "strict_disposition": "block",
        "quirks_disposition": "record",
        "sfs_id": str(row.get("sfs_id") or ""),
        "effective_date": str(row.get("effective_date") or ""),
        "scope_text": str(row.get("scope_text") or ""),
        "ops_status": ops_status,
        "error": str(row.get("error") or ""),
        "reason": reason,
    }


def plan_se_older_base_rebuild(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
    fetch_missing: bool = False,
    probe_sources: bool = False,
) -> dict[str, Any]:
    analysis = analyze_se_official_replay_feasibility(
        archive,
        amending_sfs_id,
        base_sfs_id=base_sfs_id,
        as_of=as_of,
    )
    resolved_base_sfs_id = str(analysis["base_sfs_id"])
    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    if current_json is None:
        raise FileNotFoundError(f"no archived RK current JSON for base statute {resolved_base_sfs_id}")

    def _ensure_official_artifacts(sfs_id: str) -> None:
        if not fetch_missing:
            return
        if load_se_official_act_from_archive(archive, sfs_id) is not None:
            return
        try:
            fetch_se_official_artifacts(sfs_id, archive)
        except Exception:
            return

    _ensure_official_artifacts(resolved_base_sfs_id)
    base_seed: dict[str, Any] = {
        "sfs_id": resolved_base_sfs_id,
        "official_act_available": load_se_official_act_from_archive(archive, resolved_base_sfs_id) is not None,
        "official_base_ir_available": load_se_official_base_ir_from_archive(archive, resolved_base_sfs_id) is not None,
        "pdf_available": has_valid_se_official_pdf(archive, resolved_base_sfs_id),
        "doc_available": archive.get(se_official_doc_locator(resolved_base_sfs_id)) is not None,
    }
    if probe_sources and not (base_seed["official_act_available"] or base_seed["pdf_available"]):
        base_seed["public_source_probe"] = probe_se_public_source_status(resolved_base_sfs_id)

    chain_rows: list[dict[str, Any]] = []
    for item in _older_base_chain_entries(
        current_json,
        pre_date=str(analysis["pre_date"]),
        exclude_sfs_id=amending_sfs_id,
    ):
        sfs_id = str(item["sfs_id"])
        _ensure_official_artifacts(sfs_id)
        official_act_available = load_se_official_act_from_archive(archive, sfs_id) is not None
        pdf_available = has_valid_se_official_pdf(archive, sfs_id)
        doc_available = archive.get(se_official_doc_locator(sfs_id)) is not None
        ops_status = "missing_official_act"
        op_count = 0
        error = ""
        if official_act_available:
            ops_json = load_se_official_ops_from_archive(archive, sfs_id)
            if ops_json is None:
                try:
                    ops_json = compile_se_official_ops_to_archive(archive, sfs_id)
                except FileNotFoundError as exc:
                    error = str(exc)
                    ops_status = "missing_official_act"
                except NotImplementedError as exc:
                    error = str(exc)
                    ops_status = "unsupported"
                except ValueError as exc:
                    error = str(exc)
                    ops_status = "invalid_official_act"
            if ops_json is not None:
                op_count = len(ops_json)
                ops_status = "compiled"
        chain_rows.append(
            {
                **item,
                "official_act_available": official_act_available,
                "pdf_available": pdf_available,
                "doc_available": doc_available,
                "ops_status": ops_status,
                "op_count": op_count,
                "error": error,
            }
        )
        if probe_sources and ops_status == "missing_official_act":
            chain_rows[-1]["public_source_probe"] = probe_se_public_source_status(sfs_id)

    compiled_count = sum(1 for item in chain_rows if item["ops_status"] == "compiled")
    missing_count = sum(1 for item in chain_rows if item["ops_status"] == "missing_official_act")
    unsupported_count = sum(1 for item in chain_rows if item["ops_status"] == "unsupported")
    invalid_count = sum(1 for item in chain_rows if item["ops_status"] == "invalid_official_act")
    chain_diagnostics = tuple(
        diagnostic
        for item in chain_rows
        if (diagnostic := _se_rebuild_chain_blocker_diagnostic(item)) is not None
    )
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": analysis["effective_date"],
        "pre_date": analysis["pre_date"],
        "recovery_strategy": analysis["recovery_strategy"],
        "base_seed": base_seed,
        "prior_amendment_count": len(chain_rows),
        "compiled_count": compiled_count,
        "missing_official_count": missing_count,
        "unsupported_count": unsupported_count,
        "invalid_count": invalid_count,
        "chain_diagnostics": chain_diagnostics,
        "official_chain_ready": bool(base_seed["official_act_available"])
        and all(item["ops_status"] == "compiled" for item in chain_rows),
        "seed_ready": bool(base_seed["official_base_ir_available"]),
        "rebuild_ready": bool(base_seed["official_base_ir_available"])
        and all(item["ops_status"] == "compiled" for item in chain_rows),
        "chain": chain_rows,
        "note": (
            "official_chain_ready measures source and compiler coverage; "
            "rebuild_ready additionally requires a non-amending base IR seed"
        ),
    }


def rebuild_se_older_base_from_official_chain(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
    plan: dict[str, Any] | None = None,
) -> IRStatute:
    if plan is None:
        plan = plan_se_older_base_rebuild(
            archive,
            amending_sfs_id,
            base_sfs_id=base_sfs_id,
            as_of=as_of,
        )
    if not bool(plan.get("rebuild_ready")):
        raise NotImplementedError(f"older-base rebuild prerequisites not met for {amending_sfs_id}")

    resolved_base_sfs_id = str(plan.get("base_sfs_id") or base_sfs_id or "")
    base_ir_json = load_se_official_base_ir_from_archive(archive, resolved_base_sfs_id)
    if base_ir_json is None:
        raise FileNotFoundError(f"no archived official base IR for {resolved_base_sfs_id}")
    statute = ir_statute_from_dict(base_ir_json)

    chain_rows = cast(list[Any], plan.get("chain") or [])
    for item in chain_rows:
        sfs_id = str(item.get("sfs_id") or "")
        if str(item.get("ops_status") or "") != "compiled" or not sfs_id:
            raise NotImplementedError(f"older-base chain for {amending_sfs_id} is not fully compiled")
        ops_json = load_se_official_ops_from_archive(archive, sfs_id)
        if ops_json is None:
            ops_json = compile_se_official_ops_to_archive(archive, sfs_id)
        statute = apply_se_ops(
            statute,
            [se_legal_operation_from_dict(op) for op in ops_json],
        )

    metadata = dict(statute.metadata)
    metadata["historical_rebuild_for"] = amending_sfs_id
    metadata["historical_rebuild_pre_date"] = str(plan.get("pre_date") or "")
    return IRStatute(
        statute_id=statute.statute_id,
        title=statute.title,
        body=statute.body,
        supplements=list(statute.supplements),
        metadata=metadata,
    )


def analyze_se_official_replay_feasibility(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    """Assess whether archived Sweden sources are sufficient for trusted replay.

    This is a feasibility/evidence function, not the trusted historical path
    itself. Current consolidated text may still be used here as an oracle,
    contamination detector, or temporary recovery aid while older-base rebuild
    infrastructure matures.
    """
    official_act = load_se_official_act_from_archive(archive, amending_sfs_id)
    if official_act is None:
        raise FileNotFoundError(f"no archived official act surface for {amending_sfs_id}")

    resolved_base_sfs_id = base_sfs_id or str(official_act.get("amended_act_sfs_id") or "")
    if not resolved_base_sfs_id:
        resolved_base_sfs_id = _infer_amended_act_sfs_id_from_clause(_coerce_official_act(official_act))
    if not resolved_base_sfs_id:
        raise ValueError(f"could not determine base SFS ID for {amending_sfs_id}")

    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    if current_json is None:
        raise FileNotFoundError(f"no archived RK current JSON for base statute {resolved_base_sfs_id}")

    ops_json = load_se_official_ops_from_archive(archive, amending_sfs_id)
    if ops_json is None:
        ops_json = compile_se_official_ops_to_archive(archive, amending_sfs_id)
    elif not as_of:
        first_source = (ops_json[0].get("source") or {}) if ops_json else {}
        if not str(first_source.get("effective") or "") and str(official_act.get("effective_clause") or ""):
            ops_json = compile_se_official_ops_to_archive(archive, amending_sfs_id)

    effective_date = as_of
    if effective_date is None:
        first_source = (ops_json[0].get("source") or {}) if ops_json else {}
        effective_date = str(first_source.get("effective") or "")
    if not effective_date:
        effective_date = _infer_se_effective_date_from_base_register(current_json, amending_sfs_id)
    if not effective_date:
        raise ValueError(f"could not determine effective date for {amending_sfs_id}")

    try:
        pre_date = (date.fromisoformat(effective_date) - timedelta(days=1)).isoformat()
    except ValueError as exc:
        raise ValueError(f"invalid effective date {effective_date!r}") from exc

    base_current = parse_se_statute(current_json, statute_id=resolved_base_sfs_id)
    pre_statute = materialize_se_statute_as_of(base_current, pre_date)
    ops = [se_legal_operation_from_dict(op) for op in ops_json]
    contamination = _detect_se_current_surface_contamination(
        pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    self_reverse_pre_statute = _reverse_patch_se_self_contamination(pre_statute, ops, contamination)
    self_reverse_residual = _detect_se_current_surface_contamination(
        self_reverse_pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    later_reverse_pre_statute = _reverse_patch_se_available_later_chain(
        archive,
        self_reverse_pre_statute,
        amending_sfs_id,
        self_reverse_residual,
    )
    later_reverse_residual = _detect_se_current_surface_contamination(
        later_reverse_pre_statute,
        ops,
        amending_sfs_id=amending_sfs_id,
    )
    recovery_strategy = _classify_se_historical_recovery_strategy(
        amending_sfs_id,
        contamination,
        self_reverse_residual,
        later_reverse_residual,
    )
    later_chain_hints = _build_se_later_chain_hints(
        archive,
        amending_sfs_id,
        self_reverse_residual,
    )
    recovered_pre_statute = pre_statute
    recovery_mode = "direct"
    if contamination:
        if not self_reverse_residual:
            recovered_pre_statute = self_reverse_pre_statute
            recovery_mode = "self_reverse"
        elif not later_reverse_residual:
            recovered_pre_statute = later_reverse_pre_statute
            recovery_mode = "later_reverse_chain"
    replay_precondition_issues = (
        _detect_se_replay_precondition_issues(recovered_pre_statute, ops) if not later_reverse_residual else []
    )
    replay_precondition_ancestry_hints = _build_se_replay_precondition_ancestry_hints(
        archive,
        current_json,
        amending_sfs_id,
        effective_date,
        replay_precondition_issues,
        later_chain_hints,
    )
    if replay_precondition_issues and any(
        bool(item.get("noninvertible_blocker")) for item in replay_precondition_ancestry_hints
    ):
        recovery_strategy = "older_base_required"
    elif later_reverse_residual and _has_se_noninvertible_placeholder_blocker(
        archive,
        amending_sfs_id,
        later_reverse_residual,
    ):
        recovery_strategy = "older_base_required"
    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": effective_date,
        "pre_date": pre_date,
        "op_count": len(ops),
        "contamination": contamination,
        "replay_feasible": not contamination,
        "self_reverse_feasible": not self_reverse_residual,
        "self_reverse_residual_contamination": self_reverse_residual,
        "later_chain_reverse_feasible": not later_reverse_residual,
        "later_chain_residual_contamination": later_reverse_residual,
        "recovery_mode": recovery_mode,
        "replay_ready": not contamination and not replay_precondition_issues
        if not contamination
        else (not later_reverse_residual and not replay_precondition_issues),
        "replay_precondition_issues": replay_precondition_issues,
        "replay_precondition_ancestry_hints": replay_precondition_ancestry_hints,
        "reverse_patchable_count": sum(
            1 for item in contamination if str(item.get("reverse_patch_candidate") or "") == "yes"
        ),
        "recovery_strategy": recovery_strategy,
        "later_chain_hints": later_chain_hints,
    }


def check_se_official_replay(
    archive: _ArchiveLike,
    amending_sfs_id: str,
    *,
    base_sfs_id: str | None = None,
    as_of: str | None = None,
) -> dict[str, Any]:
    analysis = analyze_se_official_replay_feasibility(
        archive,
        amending_sfs_id,
        base_sfs_id=base_sfs_id,
        as_of=as_of,
    )
    resolved_base_sfs_id = str(analysis["base_sfs_id"])
    effective_date = str(analysis["effective_date"])
    pre_date = str(analysis["pre_date"])
    official_act = load_se_official_act_from_archive(archive, amending_sfs_id)
    assert official_act is not None
    current_json = archive.get(se_rk_current_json_locator(resolved_base_sfs_id))
    assert current_json is not None
    ops_json = load_se_official_ops_from_archive(archive, amending_sfs_id)
    assert ops_json is not None
    base_current = parse_se_statute(current_json, statute_id=resolved_base_sfs_id)
    pre_statute = materialize_se_statute_as_of(base_current, pre_date)
    post_statute = materialize_se_statute_as_of(base_current, effective_date)
    current_raw_sections = extract_se_current_section_texts(current_json, effective_date)
    official_provisions = {
        str(provision.get("label") or ""): str(provision.get("text") or "")
        for provision in official_act.get("provisions", [])
        if isinstance(provision, dict)
    }
    official_headings = {
        str(heading.get("before_label") or ""): str(heading.get("text") or "")
        for heading in official_act.get("inserted_headings", [])
        if isinstance(heading, dict)
    }
    official_appendices = {
        str(appendix.get("label") or ""): " ".join(
            part
            for part in [
                str(appendix.get("title") or "").strip(),
                str(appendix.get("text") or "").strip(),
            ]
            if part
        )
        for appendix in official_act.get("appendices", [])
        if isinstance(appendix, dict)
    }
    ops = [se_legal_operation_from_dict(op) for op in ops_json]
    contamination = cast(list[Any], analysis["contamination"])
    precondition_issues = cast(list[Any], analysis.get("replay_precondition_issues") or [])
    rebuilt_pre_statute: IRStatute | None = None
    if str(analysis.get("recovery_strategy") or "") == "older_base_required":
        older_base_plan = plan_se_older_base_rebuild(
            archive,
            amending_sfs_id,
            base_sfs_id=resolved_base_sfs_id,
            as_of=effective_date,
        )
        if bool(older_base_plan.get("rebuild_ready")):
            rebuilt_pre_statute = rebuild_se_older_base_from_official_chain(
                archive,
                amending_sfs_id,
                base_sfs_id=resolved_base_sfs_id,
                as_of=effective_date,
                plan=older_base_plan,
            )
            pre_statute = rebuilt_pre_statute
            precondition_issues = _detect_se_replay_precondition_issues(pre_statute, ops)
            contamination = []
    if precondition_issues:
        issues_text = ", ".join(
            f"{item['target_kind']}:{item['label']}:{item['issue']}" for item in precondition_issues
        )
        raise NotImplementedError(
            f"recovered Sweden base for {resolved_base_sfs_id} still lacks required replay targets "
            f"for {amending_sfs_id}: {issues_text}"
        )
    replay_base_statute = pre_statute
    comparison_post_statute = post_statute
    recovery_mode = "direct"
    if rebuilt_pre_statute is not None:
        recovery_mode = "older_base_rebuild"
        self_reverse_residual = cast(list[Any], analysis.get("self_reverse_residual_contamination") or [])
        if bool(analysis.get("later_chain_reverse_feasible")) and self_reverse_residual:
            comparison_post_statute = _reverse_patch_se_available_later_chain(
                archive,
                post_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
    elif contamination:
        self_reverse_pre_statute = _reverse_patch_se_self_contamination(pre_statute, ops, contamination)
        self_reverse_residual = _detect_se_current_surface_contamination(
            self_reverse_pre_statute,
            ops,
            amending_sfs_id=amending_sfs_id,
        )
        if not self_reverse_residual:
            replay_base_statute = self_reverse_pre_statute
            recovery_mode = "self_reverse"
        elif bool(analysis.get("later_chain_reverse_feasible")):
            replay_base_statute = _reverse_patch_se_available_later_chain(
                archive,
                self_reverse_pre_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
            comparison_post_statute = _reverse_patch_se_available_later_chain(
                archive,
                post_statute,
                amending_sfs_id,
                self_reverse_residual,
            )
            recovery_mode = "later_reverse_chain"
        else:
            contamination_text = ", ".join(
                f"{item['target_kind']}:{item['label']}:{item['issue']}" for item in contamination
            )
            raise NotImplementedError(
                f"base current surface for {resolved_base_sfs_id} already contains post-amendment targets "
                f"before {effective_date}: {contamination_text}; "
                "historical replay requires an older base surface or reverse patching"
            )
    baseline_invariants = set(se_statute_invariant_violations(replay_base_statute))
    baseline_typed_invariant_messages = {
        violation.message for violation in se_statute_invariant_violation_records(replay_base_statute)
    }
    replay_adjudications: list = []
    replayed = apply_se_ops(replay_base_statute, ops, adjudications_out=replay_adjudications)
    skipped_op_ids = {item.op_id for item in replay_adjudications if item.op_id}
    finding_rows = adjudication_finding_evidence_rows(
        replay_adjudications,
        frontend_id="sweden",
        base_id=resolved_base_sfs_id,
        as_of=effective_date,
    )

    post_sections = se_section_text_map(comparison_post_statute)
    replay_sections = se_section_text_map(replayed)
    post_headings = se_heading_before_section_map(comparison_post_statute)
    replay_headings = se_heading_before_section_map(replayed)
    post_appendices = se_appendix_text_map(comparison_post_statute)
    replay_appendices = se_appendix_text_map(replayed)
    covered_targets = {
        (
            op.target.leaf_kind(),
            op.target.special or "",
            op.target.leaf_label(),
        )
        for op in ops
        if op.action is not StructuralAction.RENUMBER
    }

    def _official_oracle_classification(post_text: str) -> str:
        return (
            "official_oracle_match_missing_current_post"
            if not post_text.strip()
            else "official_oracle_match_current_surface_drift"
        )

    rows: list[dict[str, Any]] = []
    for op in ops:
        if op.op_id and op.op_id in skipped_op_ids:
            continue
        label = op.target.leaf_label()
        if op.action is StructuralAction.RENUMBER:
            destination_label = op.destination.leaf_label() if op.destination is not None else ""
            destination_key = (op.target.leaf_kind(), op.target.special or "", destination_label)
            if destination_label and destination_key in covered_targets:
                continue
            label = destination_label or label
        if op.target.leaf_kind() == "section" and op.target.special is FacetKind.HEADING:
            replay_text = replay_headings.get(label, "")
            post_text = post_headings.get(label, "")
            official_raw_text = official_headings.get(label, "")
            match = _normalize_compare_text(replay_text) == _normalize_compare_text(post_text)
            classification = "exact" if match else "content_mismatch"
            if (
                not match
                and official_raw_text
                and _normalize_compare_text(replay_text) == _normalize_compare_text(official_raw_text)
            ):
                match = True
                classification = _official_oracle_classification(post_text)
            rows.append(
                {
                    "target_kind": "heading",
                    "section": label,
                    "match": match,
                    "classification": classification,
                    "replay_text": replay_text,
                    "post_text": post_text,
                    "official_text": official_raw_text,
                }
            )
            continue
        if op.target.leaf_kind() == "appendix":
            replay_text = replay_appendices.get(label, "")
            post_text = post_appendices.get(label, "")
            official_raw_text = official_appendices.get(label, "")
            replay_norm = _normalize_appendix_compare_text(replay_text)
            post_norm = _normalize_appendix_compare_text(post_text)
            match = replay_norm == post_norm
            classification = "exact" if match else _classify_replay_row(replay_text, post_text)
            if not match and official_raw_text and replay_norm == _normalize_appendix_compare_text(official_raw_text):
                match = True
                classification = _official_oracle_classification(post_text)
            rows.append(
                {
                    "target_kind": "appendix",
                    "appendix": label,
                    "match": match,
                    "classification": classification,
                    "replay_text": replay_text,
                    "post_text": post_text,
                    "official_text": official_raw_text,
                }
            )
            continue
        replay_text = replay_sections.get(label, "")
        post_text = post_sections.get(label, "")
        current_raw_text = current_raw_sections.get(label, "")
        official_raw_text = official_provisions.get(label, "")
        if "Uppgift lämnas av" in current_raw_text and "Uppgift lämnas av" in official_raw_text:
            replay_canonical = canonicalize_se_table_section_text(official_raw_text)
            post_canonical = canonicalize_se_table_section_text(current_raw_text)
            match = replay_canonical == post_canonical
            classification = "table_rows_match" if match else "table_layout_mismatch"
        else:
            match = _normalize_compare_text(replay_text) == _normalize_compare_text(post_text)
            classification = (
                "exact"
                if match and replay_text.strip() == post_text.strip()
                else (
                    _classify_replay_row(replay_text, post_text)
                    if not (match and replay_text.strip() == post_text.strip())
                    else "exact"
                )
            )
        if (
            not match
            and official_raw_text
            and _normalize_compare_text(replay_text) == _normalize_compare_text(official_raw_text)
        ):
            match = True
            classification = _official_oracle_classification(post_text)
        rows.append(
            {
                "target_kind": "section",
                "section": label,
                "match": match,
                "classification": classification,
                "replay_text": replay_text,
                "post_text": post_text,
            }
        )

    return {
        "amending_sfs_id": amending_sfs_id,
        "base_sfs_id": resolved_base_sfs_id,
        "effective_date": effective_date,
        "pre_date": pre_date,
        "recovery_mode": recovery_mode,
        "target_count": len(rows),
        "match_count": sum(1 for row in rows if row["match"]),
        "invariant_violations": [
            violation
            for violation in replayed.metadata.get("invariant_violations", [])
            if violation not in baseline_invariants
        ],
        "typed_invariant_violations": [
            violation.to_dict()
            for violation in se_statute_invariant_violation_records(replayed)
            if violation.message not in baseline_typed_invariant_messages
        ],
        "adjudications": [asdict(item) for item in replay_adjudications],
        "evidence": {
            "finding_rows": [row.to_dict() for row in finding_rows],
        },
        "rows": rows,
    }


def archive_se_official_artifacts_manifest(
    archive: _ArchiveLike,
    official_artifacts: SEOfficialArtifacts,
) -> None:
    bundle_data = load_se_bundle_from_archive(archive, official_artifacts.sfs_id)
    if bundle_data is None:
        return
    bundle_data["official_artifacts"] = _normalize_jsonable(asdict(official_artifacts))
    source_record = bundle_data.get("source_record")
    if isinstance(source_record, dict):
        source_urls = source_record.get("source_urls")
        if isinstance(source_urls, dict):
            source_urls["official_sfs_doc_url"] = official_artifacts.doc_url
            source_urls["official_sfs_pdf_url"] = official_artifacts.pdf_url
        archive.store(
            se_source_record_locator(official_artifacts.sfs_id),
            _json_bytes(source_record),
            storage_class="json",
        )
    archive.store(
        se_bundle_manifest_locator(official_artifacts.sfs_id),
        _json_bytes(bundle_data),
        storage_class="json",
    )


def archive_se_official_phase_artifacts_manifest(archive: _ArchiveLike, sfs_id: str) -> None:
    bundle_data = load_se_bundle_from_archive(archive, sfs_id)
    if bundle_data is None:
        return
    bundle_data["official_phase_artifacts"] = {
        "clause_surface": se_official_clause_surface_locator(sfs_id),
        "payload_surface": se_official_payload_surface_locator(sfs_id),
        "elaboration": se_official_elaboration_locator(sfs_id),
        "effects_plan": se_official_effects_plan_locator(sfs_id),
        "effects": se_official_ops_locator(sfs_id),
        "effects_adjudications": se_official_ops_adjudications_locator(sfs_id),
    }
    archive.store(
        se_bundle_manifest_locator(sfs_id),
        _json_bytes(bundle_data),
        storage_class="json",
    )


def hydrate_se_bundle_live(
    sfs_id: str,
    archive: _ArchiveLike,
    *,
    pdf_url_override: str | None = None,
    current_max_age_hours: float = _CURRENT_SURFACE_CACHE_HOURS,
    official_max_age_hours: float = _IMMUTABLE_CACHE_HOURS,
    force_reextract: bool = False,
    diagnostics_out: list[dict[str, Any]] | None = None,
) -> Optional[SESourceBundle]:
    current_json = fetch_se_rk_current_json(
        sfs_id,
        archive,
        max_age_hours=current_max_age_hours,
        diagnostics_out=diagnostics_out,
    )
    if current_json is None:
        return None
    bundle = archive_se_source_bundle(current_json, archive)
    official = fetch_se_official_artifacts(
        sfs_id,
        archive,
        max_age_hours=official_max_age_hours,
        force_reextract=force_reextract,
        pdf_url_override=pdf_url_override,
    )
    return attach_official_artifacts_to_bundle(bundle, official)


def _coerce_payload_to_dict(payload: bytes | str | dict) -> dict:
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("expected Sweden source document to decode to a JSON object")
    return data
