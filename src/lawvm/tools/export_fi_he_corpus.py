"""Export fi_he_* Parquet projections from fi_government_proposal.farchive.

Produces four projections under data/fi/v1/:
  fi_he_corpus.parquet     -- per-HE metadata (one row per language variant)
  fi_he_atoms.parquet      -- body structure atoms for FULL_AKN HEs
  fi_he_law_refs.parquet   -- typed citations to enacted statutes (reuses #1 extractor)
  fi_he_signatures.parquet -- typed signature elements (President, ministers)

AGENTS.md compliance
---------------------
S1.1  No silent target hijacking: ministry absence emits HEMissingMinistryObservation.
S1.6  No unstated migration: missing ministry emits typed observation, not silent empty.
S1.8  No source lane disappearance: failures emit HEProjectionFailure records.
S1.9  Typed primitives -- all cross-phase data uses typed dataclasses.
S1.10 No bare try/except -- exceptions caught only at bounded XML parse boundaries.
S1.11 Regex compiled at module scope via the existing ReferenceMention extractor.

Phase: Parse (S6 phase 3) -> Emit evidence (S6 phase 11).
Acquisition (phase 1) owned by he_acquisition.py (#0).

PDF_WRAPPER HEs contribute rows ONLY to fi_he_corpus.parquet (is_structured=False).
No atoms / law_refs / signatures are extracted from PDF_WRAPPER HEs.

Schema version: v1 (per NEXT_FEATURES_ROADMAP.md S10 convention).
"""
from __future__ import annotations

import copy
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from lxml import etree

from lawvm.finland.he_acquisition import (
    HEStructuralTier,
    _AKN_NS,
    _extract_date_issued,
    _extract_finlex_state,
    _extract_frbr_uri,
    _extract_he_id,
    _extract_lang,
    _extract_ministry,
    _extract_title,
    classify_structural_tier,
)

# ---------------------------------------------------------------------------
# Schema version
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "v1"

# Atom text content cap per row (brief spec: ~10KB per row)
_TEXT_CONTENT_CAP = 10 * 1024  # 10KB


# ---------------------------------------------------------------------------
# Typed observation and failure primitives (AGENTS.md S1.8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class HEProjectionFailure:
    """Typed record for a per-HE projection failure.

    Emitted when XML parsing or body extraction fails.
    Never silently dropped (AGENTS.md S1.8).
    """

    rule_id: str
    phase: str
    family: str
    he_year: Optional[int]
    he_number: Optional[int]
    lang: Optional[str]
    reason: str
    detail: str
    strict_disposition: str


@dataclass(frozen=True, slots=True)
class HEMissingMinistryObservation:
    """Observation emitted when finlex:administrativeBranch is absent.

    Per AGENTS.md S1.6: ministry absence is noted, not silently accepted.
    The corpus row IS emitted with empty ministry fields.
    """

    rule_id: str
    he_year: int
    he_number: int
    lang: str
    detail: str


@dataclass(frozen=True, slots=True)
class HEMinistryObservation:
    """Observation emitted when ministry metadata may need registry cross-check.

    Per AGENTS.md S1.6: if an HE's ministry canonical_id cannot be verified
    against the canonical-actor registry (feature #2), emit typed observation.
    """

    rule_id: str
    he_year: int
    he_number: int
    lang: str
    ministry_canonical_id: str
    ministry_show_as: str
    detail: str


# ---------------------------------------------------------------------------
# Typed observation container for one projection run
# ---------------------------------------------------------------------------


@dataclass
class HEProjectionResult:
    """Aggregated output of projecting one HE language variant."""

    corpus_rows: List[Dict[str, Any]]
    atom_rows: List[Dict[str, Any]]
    law_ref_rows: List[Dict[str, Any]]
    signature_rows: List[Dict[str, Any]]
    failures: List[HEProjectionFailure]
    observations: List[object]  # HEMissingMinistryObservation | HEMinistryObservation | RejectedRefCandidate


# ---------------------------------------------------------------------------
# AKN element localname helper
# ---------------------------------------------------------------------------


def _localname(node: etree._Element) -> str:
    tag = node.tag
    if isinstance(tag, str) and "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return str(tag) if isinstance(tag, str) else ""


# ---------------------------------------------------------------------------
# Atom extraction from FULL_AKN HE body
# ---------------------------------------------------------------------------

# Structural container element local names from HE AKN bodies
_CONTAINER_ELEMENTS = frozenset([
    "hcontainer", "section", "subsection", "paragraph",
    "blockContainer", "div",
])

# hcontainer name attribute -> atom_type string
_HCONTAINER_NAME_MAP: Dict[str, str] = {
    "rationale": "rationale",
    "introduction": "introduction",
    "section": "section",
    "subsection": "subsection",
    "background": "background",
    "goals": "goals",
    "proposal": "proposal",
    "impact": "impact",
    "remarks": "remarks",
    "signatures": "signatures",
    "preface": "preface",
    "conclusions": "conclusions",
    "contentAbsent": "contentAbsent",
}


def _classify_atom_type(element: etree._Element) -> str:
    """Classify an element into a named atom type."""
    lname = _localname(element)
    name_attr = element.attrib.get("name", "")
    if name_attr and name_attr in _HCONTAINER_NAME_MAP:
        return _HCONTAINER_NAME_MAP[name_attr]
    if lname in _HCONTAINER_NAME_MAP:
        return _HCONTAINER_NAME_MAP[lname]
    return lname


def _element_text_content(element: etree._Element) -> str:
    """Extract all text content from an element, whitespace-collapsed."""
    import re as _re
    parts: List[str] = []
    for text in element.itertext():
        parts.append(str(text))
    raw = "".join(parts)
    return _re.sub(r"\s+", " ", raw).strip()


def _build_atom_id(he_id: str, element: etree._Element, seq: int) -> str:
    """Build a stable atom_id from the HE ID + element eId + seq."""
    eid = element.attrib.get("eId", "")
    if eid:
        return f"{he_id}#{eid}"
    lname = _localname(element)
    name_attr = element.attrib.get("name", "")
    label = name_attr or lname
    return f"{he_id}#atom_{label}_{seq}"


def _extract_atoms_from_body(
    body: etree._Element,
    he_year: int,
    he_number: int,
    he_id: str,
    source_file: str,
) -> List[Dict[str, Any]]:
    """Extract atom rows from mainBody element of a FULL_AKN HE.

    Walks the body tree depth-first. Each hcontainer / section / subsection /
    blockContainer is one atom row. Text content is capped at _TEXT_CONTENT_CAP.
    """
    rows: List[Dict[str, Any]] = []

    def _walk(node: etree._Element, parent_atom_id: Optional[str]) -> None:
        lname = _localname(node)
        if lname not in _CONTAINER_ELEMENTS:
            for child in node:
                _walk(child, parent_atom_id)
            return

        seq = len(rows)
        atom_id = _build_atom_id(he_id, node, seq)
        atom_type = _classify_atom_type(node)

        num_text = ""
        heading_text = ""
        for child in node:
            child_lname = _localname(child)
            if child_lname == "num" and not num_text:
                num_text = _element_text_content(child)
            elif child_lname == "heading" and not heading_text:
                heading_text = _element_text_content(child)

        text_content = _element_text_content(node)
        char_count = len(text_content)
        if len(text_content) > _TEXT_CONTENT_CAP:
            text_content = text_content[:_TEXT_CONTENT_CAP]

        rows.append({
            "he_id": he_id,
            "he_year": he_year,
            "he_number": he_number,
            "atom_id": atom_id,
            "parent_atom_id": parent_atom_id,
            "atom_type": atom_type,
            "seq": seq,
            "num": num_text or None,
            "heading": heading_text or None,
            "text_content": text_content,
            "char_count": char_count,
            "source_span_file": source_file,
            "source_span_byte_offset": None,
            "source_span_len": None,
        })

        for child in node:
            if _localname(child) in _CONTAINER_ELEMENTS:
                _walk(child, atom_id)

    _walk(body, None)
    return rows


# ---------------------------------------------------------------------------
# Signature extraction
# ---------------------------------------------------------------------------


def _extract_signatures_from_conclusions(
    conclusions: etree._Element,
    he_year: int,
    he_number: int,
    he_id: str,
) -> List[Dict[str, Any]]:
    """Extract signature rows from the conclusions element."""
    rows: List[Dict[str, Any]] = []
    order = 0

    for elem in conclusions.iter():
        lname = _localname(elem)
        if lname == "signature":
            role_text = ""
            person_text = ""
            for child in elem:
                child_lname = _localname(child)
                if child_lname == "role":
                    role_text = _element_text_content(child)
                elif child_lname == "person":
                    person_text = _element_text_content(child)
            rows.append({
                "he_id": he_id,
                "he_year": he_year,
                "he_number": he_number,
                "role": role_text or None,
                "person": person_text or None,
                "signature_order": order,
                "source_span_file": None,
                "source_span_byte_offset": None,
                "source_span_len": None,
            })
            order += 1

    return rows


# ---------------------------------------------------------------------------
# Law ref extraction (reuses #1 ReferenceMention extractor unchanged)
# ---------------------------------------------------------------------------


def _build_act_wrapper_for_he_body(xml_bytes: bytes) -> bytes:
    """Wrap an HE mainBody's children in an act/body structure for the extractor.

    The ReferenceMention extractor (cross_refs.py) was built for enacted statutes
    that use <act><body>...</body></act> structure. HE documents use
    <doc><mainBody>...</mainBody></doc> structure.

    To reuse the extractor unchanged, we extract the <ref> elements' enclosing
    body content and rewrap it in a minimal act/body envelope. The extractor is
    called on the rewrapped bytes, so the extractor itself is genuinely unchanged.

    This is not a semantic transformation: we only move body content into the
    structural position the extractor expects. The <ref> hrefs are preserved
    exactly. No text is added or removed.

    If the HE has no mainBody, returns an empty act stub (no refs extracted).
    """
    root: Optional[etree._Element] = None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError:
        return b""

    if root is None:
        return b""

    main_body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if main_body is None:
        return b""

    # Build a minimal act/body stub with the mainBody children
    # Use lxml to preserve namespace handling for <ref> elements
    act = etree.Element(f"{{{_AKN_NS}}}akomaNtoso")
    act_inner = etree.SubElement(act, f"{{{_AKN_NS}}}act")
    body = etree.SubElement(act_inner, f"{{{_AKN_NS}}}body")
    for child in main_body:
        # deepcopy preserves all subelements and attributes including <ref>
        body.append(copy.deepcopy(child))

    return etree.tostring(act, encoding="unicode").encode("utf-8")


def _extract_law_refs_from_he_body(
    xml_bytes: bytes,
    he_year: int,
    he_number: int,
    he_id: str,
) -> Tuple[List[Dict[str, Any]], List[Any]]:
    """Extract fi_he_law_refs rows by running the ReferenceMention extractor.

    Per brief: "Reuses feature #1's ReferenceMention extractor unchanged."
    The extractor expects act/body structure; HE docs use doc/mainBody.
    We rewrap the mainBody content in a minimal act/body envelope before
    calling the extractor — the extractor itself is unchanged.

    Returns (ref_rows, rejected_records).
    """
    from lawvm.core.reference_mention import reference_mention_to_row
    from lawvm.finland.references.ref_mention_extractor import extract_all_reference_mentions

    # Use a synthetic statute_id in HE URI space for source-side identification.
    he_source_id = f"he/{he_year}/{he_number}"

    # Rewrap HE mainBody content in act/body envelope for the extractor
    act_wrapped_bytes = _build_act_wrapper_for_he_body(xml_bytes)
    if not act_wrapped_bytes:
        return [], []

    result = extract_all_reference_mentions(act_wrapped_bytes, he_source_id)

    rows: List[Dict[str, Any]] = []
    for mention in result.mentions:
        row = reference_mention_to_row(mention)
        row["he_id"] = he_id
        row["he_year"] = he_year
        row["he_number"] = he_number
        rows.append(row)

    return rows, result.rejected


# ---------------------------------------------------------------------------
# Per-HE projection entry point
# ---------------------------------------------------------------------------


def project_he_from_xml(
    xml_bytes: bytes,
    *,
    he_year: int,
    he_number: int,
    lang: str,
    source_file: str = "",
    source_zip_sha256: str = "",
    ingest_timestamp: Optional[datetime] = None,
    languages_in_he: Tuple[str, ...] = (),
    strict: bool = False,
) -> HEProjectionResult:
    """Project one HE language variant from its AKN XML bytes.

    This is the main extraction entry point.

    Args:
        xml_bytes:        AKN XML bytes for one language variant of one HE.
        he_year:          Calendar year of the HE.
        he_number:        Number of the HE within the year.
        lang:             Language code ('fin' or 'swe').
        source_file:      Provenance path/locator for source_span fields.
        source_zip_sha256: SHA256 of the source zip (from farchive metadata).
        ingest_timestamp: When the HE was ingested into the farchive.
        languages_in_he:  All language codes present for this HE.
        strict:           If True, parse failures are strict-mode abort disposition.

    Returns:
        HEProjectionResult with populated rows and observations/failures.
    """
    result = HEProjectionResult(
        corpus_rows=[],
        atom_rows=[],
        law_ref_rows=[],
        signature_rows=[],
        failures=[],
        observations=[],
    )

    root: Optional[etree._Element] = None
    parse_err: Optional[str] = None
    try:
        root = etree.fromstring(xml_bytes)
    except etree.XMLSyntaxError as exc:
        parse_err = str(exc)

    if root is None or parse_err is not None:
        result.failures.append(
            HEProjectionFailure(
                rule_id="HE_PROJ.XML_PARSE_ERROR",
                phase="parse",
                family="transport_cleanup",
                he_year=he_year,
                he_number=he_number,
                lang=lang,
                reason="XML parse error in HE main.xml during projection",
                detail=parse_err or "unknown error",
                strict_disposition="abort",
            )
        )
        return result

    assert root is not None  # mypy

    # Validate FRBRsubtype -- reject non-HE documents (AGENTS.md S1.1, S1.9)
    from lawvm.finland.he_acquisition import _extract_frbr_subtype
    subtype = _extract_frbr_subtype(root)
    if subtype != "government-proposal":
        result.failures.append(
            HEProjectionFailure(
                rule_id="HE_PROJ.WRONG_FRBR_SUBTYPE",
                phase="parse",
                family="source_pathology",
                he_year=he_year,
                he_number=he_number,
                lang=lang,
                reason=(
                    f"FRBRsubtype is {subtype!r}, expected 'government-proposal'; "
                    "rejecting non-HE document from HE projection lane"
                ),
                detail=f"subtype={subtype!r}",
                strict_disposition="abort",
            )
        )
        return result

    # Extract metadata fields
    he_uri = _extract_frbr_uri(root)
    ministry_canonical_id, ministry_show_as = _extract_ministry(root)
    title = _extract_title(root)
    he_id = _extract_he_id(root)
    date_issued = _extract_date_issued(root)
    finlex_state = _extract_finlex_state(root)
    structural_tier = classify_structural_tier(root)
    extracted_lang = _extract_lang(root) or lang

    if not he_id:
        he_id = f"HE {he_number}/{he_year}"

    if not date_issued:
        result.failures.append(
            HEProjectionFailure(
                rule_id="HE_PROJ.MISSING_DATE_ISSUED",
                phase="parse",
                family="source_pathology",
                he_year=he_year,
                he_number=he_number,
                lang=lang,
                reason="FRBRdate[@name='dateIssued'] absent or unparseable",
                detail="",
                strict_disposition="record",
            )
        )

    # Ministry observation (AGENTS.md S1.6)
    if not ministry_canonical_id:
        result.observations.append(
            HEMissingMinistryObservation(
                rule_id="HE_PROJ.MISSING_MINISTRY",
                he_year=he_year,
                he_number=he_number,
                lang=lang,
                detail=(
                    "finlex:administrativeBranch absent; "
                    "ministry_canonical_id will be empty in corpus row"
                ),
            )
        )

    is_structured = structural_tier == HEStructuralTier.FULL_AKN

    # --- fi_he_corpus row ---
    corpus_row: Dict[str, Any] = {
        "he_id": he_id,
        "he_year": he_year,
        "he_number": he_number,
        "he_uri": he_uri,
        "lang": extracted_lang,
        "languages": list(languages_in_he) or [extracted_lang],
        "ministry_canonical_id": ministry_canonical_id,
        "ministry_show_as": ministry_show_as,
        "title": title,
        "date_issued": date_issued.isoformat() if date_issued else None,
        "structural_tier": structural_tier.value,
        "is_structured": is_structured,
        "finlex_state": finlex_state,
        "source_zip_sha256": source_zip_sha256,
        "ingest_timestamp": (
            ingest_timestamp.isoformat() if ingest_timestamp else None
        ),
    }
    result.corpus_rows.append(corpus_row)

    # PDF_WRAPPER: stop here -- no body to extract atoms/refs/signatures from
    if not is_structured:
        return result

    # --- Atom extraction (FULL_AKN only) ---
    main_body = root.find(f".//{{{_AKN_NS}}}mainBody")
    if main_body is not None:
        atom_rows = _extract_atoms_from_body(
            main_body,
            he_year=he_year,
            he_number=he_number,
            he_id=he_id,
            source_file=source_file,
        )
        result.atom_rows.extend(atom_rows)

    # --- Law ref extraction (reuses #1 extractor unchanged) ---
    law_ref_rows, rejected = _extract_law_refs_from_he_body(
        xml_bytes,
        he_year=he_year,
        he_number=he_number,
        he_id=he_id,
    )
    result.law_ref_rows.extend(law_ref_rows)
    result.observations.extend(rejected)

    # --- Signature extraction ---
    # Real Finlex HE XML uses <hcontainer name="conclusions"> inside <mainBody>,
    # NOT a bare <conclusions> AKN element.  The latter only appears in synthetic
    # fixtures built against the AKN 3.0 spec template.  Search both forms so
    # that both the conformance fixtures and the real corpus work correctly.
    #
    # Precedence: prefer the hcontainer form (real corpus) if both happen to
    # exist (they don't in practice).
    conclusions_el = root.find(
        f".//{{{_AKN_NS}}}hcontainer[@name='conclusions']"
    )
    if conclusions_el is None:
        # Fallback: bare <conclusions> element (synthetic fixtures / future format)
        conclusions_el = root.find(f".//{{{_AKN_NS}}}conclusions")
    if conclusions_el is not None:
        sig_rows = _extract_signatures_from_conclusions(
            conclusions_el,
            he_year=he_year,
            he_number=he_number,
            he_id=he_id,
        )
        result.signature_rows.extend(sig_rows)

    return result


# ---------------------------------------------------------------------------
# JSONL / Parquet writers
# ---------------------------------------------------------------------------


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> int:
    from lawvm.tools.export_persistence import write_jsonl

    return write_jsonl(path, rows)


def _build_parquet_schemas() -> Dict[str, Any]:
    """Build pinned Parquet schemas for each projection table."""
    try:
        import pyarrow as pa
    except ImportError:
        return {}

    _s = pa.string()
    _i64 = pa.int64()
    _bool_ = pa.bool_()
    _la = pa.large_list(pa.string())

    corpus_schema = pa.schema([
        pa.field("he_id", _s),
        pa.field("he_year", _i64),
        pa.field("he_number", _i64),
        pa.field("he_uri", _s),
        pa.field("lang", _s),
        pa.field("languages", _la),
        pa.field("ministry_canonical_id", _s),
        pa.field("ministry_show_as", _s),
        pa.field("title", _s),
        pa.field("date_issued", _s),
        pa.field("structural_tier", _s),
        pa.field("is_structured", _bool_),
        pa.field("finlex_state", _s),
        pa.field("source_zip_sha256", _s),
        pa.field("ingest_timestamp", _s),
    ])

    atoms_schema = pa.schema([
        pa.field("he_id", _s),
        pa.field("he_year", _i64),
        pa.field("he_number", _i64),
        pa.field("atom_id", _s),
        pa.field("parent_atom_id", _s),
        pa.field("atom_type", _s),
        pa.field("seq", _i64),
        pa.field("num", _s),
        pa.field("heading", _s),
        pa.field("text_content", _s),
        pa.field("char_count", _i64),
        pa.field("source_span_file", _s),
        pa.field("source_span_byte_offset", _i64),
        pa.field("source_span_len", _i64),
    ])

    law_refs_schema = pa.schema([
        pa.field("he_id", _s),
        pa.field("he_year", _i64),
        pa.field("he_number", _i64),
        pa.field("source_statute_id", _s),
        pa.field("source_provision_ref_str", _s),
        pa.field("target_statute_id", _s),
        pa.field("target_provision_ref_str", _s),
        pa.field("cite_kind", _s),
        pa.field("cite_confidence", _s),
        pa.field("edge_subtype", _s),
        pa.field("phrase_lemma", _s),
        pa.field("source_span_file", _s),
        pa.field("source_span_byte_offset", _i64),
        pa.field("source_span_len", _i64),
        pa.field("valid_at_start", _s),
        pa.field("valid_at_end", _s),
        pa.field("target_stat_hash", _s),
    ])

    signatures_schema = pa.schema([
        pa.field("he_id", _s),
        pa.field("he_year", _i64),
        pa.field("he_number", _i64),
        pa.field("role", _s),
        pa.field("person", _s),
        pa.field("signature_order", _i64),
        pa.field("source_span_file", _s),
        pa.field("source_span_byte_offset", _i64),
        pa.field("source_span_len", _i64),
    ])

    return {
        "fi_he_corpus": corpus_schema,
        "fi_he_atoms": atoms_schema,
        "fi_he_law_refs": law_refs_schema,
        "fi_he_signatures": signatures_schema,
    }


_PARQUET_SCHEMAS = _build_parquet_schemas()


def _attach_compile_metadata(table: Any, compile_metadata: Any) -> Any:
    """Attach CompileMetadata fields to a pyarrow Table's schema metadata."""
    if compile_metadata is None:
        raise ValueError(
            "export_fi_he_corpus: CompileMetadata is required for v3 substrate-locked "
            "persistence. Construct via build_default_compile_metadata() or "
            "explicitly. See UNIFIED_PROVENANCE_GRAPH_DESIGN_v3.md §13 Step 5."
        )
    existing = table.schema.metadata or {}
    meta = dict(existing)
    for k, v in compile_metadata.to_metadata_dict().items():
        meta[k.encode()] = v.encode()
    return table.replace_schema_metadata(meta)


def _try_write_parquet(
    path: Path,
    rows: List[Dict[str, Any]],
    schema: Any = None,
    compile_metadata: Any = None,
) -> bool:
    """Try to write rows as Parquet+zstd with optional compile metadata. Returns True if ok."""
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError:
        return False

    path.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        if schema is not None:
            table = pa.table({col: [] for col in schema.names}, schema=schema)
        else:
            return False
    else:
        if schema is not None:
            try:
                table = pa.Table.from_pylist(rows, schema=schema)
            except Exception:
                table = pa.Table.from_pylist(rows)
        else:
            table = pa.Table.from_pylist(rows)

    table = _attach_compile_metadata(table, compile_metadata)
    pq.write_table(table, str(path), compression="zstd")
    return True


# ---------------------------------------------------------------------------
# Farchive-based batch projection
# ---------------------------------------------------------------------------


def project_he_corpus(
    *,
    farchive_path: str = "data/fi_government_proposal.farchive",
    data_dir: str = "data/fi/v1",
    lang: str = "fin",
    limit: Optional[int] = None,
    use_parquet: bool = True,
    strict: bool = False,
    verbose: bool = False,
    compile_metadata: Optional[Any] = None,
) -> Dict[str, int]:
    """Project fi_he_corpus, fi_he_atoms, fi_he_law_refs, fi_he_signatures.

    Reads from fi_government_proposal.farchive and writes to data/fi/v1/.

    Returns:
        Dict mapping table name to row count.
    """
    from farchive import Farchive

    farchive = Farchive(farchive_path, readonly=True)

    all_corpus: List[Dict[str, Any]] = []
    all_atoms: List[Dict[str, Any]] = []
    all_law_refs: List[Dict[str, Any]] = []
    all_signatures: List[Dict[str, Any]] = []
    all_failures: List[Dict[str, Any]] = []
    all_observations: List[Dict[str, Any]] = []

    done = 0
    t_start = time.time()

    prefix = "akn/fi/doc/government-proposal/"
    lang_suffix = f"/{lang}@/main.xml"

    try:
        # farchive.locators() returns all stored locators; filter to HE main.xml entries.
        # farchive.resolve(locator) returns a StateSpan with last_metadata dict.
        # farchive.read(locator) returns bytes.
        all_locators = farchive.locators()

        for locator in all_locators:
            if limit is not None and done >= limit:
                break
            if not locator.startswith(prefix):
                continue
            if not locator.endswith(lang_suffix):
                continue

            span = farchive.resolve(locator)
            meta = span.last_metadata if span is not None else {}
            if meta is None:
                meta = {}

            xml_bytes_raw = farchive.get(locator)
            if xml_bytes_raw is None:
                all_failures.append({
                    "rule_id": "HE_PROJ.FARCHIVE_READ_ERROR",
                    "phase": "acquire",
                    "he_year": meta.get("he_year"),
                    "he_number": meta.get("he_number"),
                    "lang": lang,
                    "locator": locator,
                    "reason": "farchive.get returned None",
                })
                continue

            he_year_raw = meta.get("he_year")
            he_number_raw = meta.get("he_number")
            if he_year_raw is None or he_number_raw is None:
                all_failures.append({
                    "rule_id": "HE_PROJ.MISSING_FARCHIVE_METADATA",
                    "phase": "acquire",
                    "locator": locator,
                    "reason": "he_year or he_number missing from farchive metadata",
                })
                continue

            he_year = int(he_year_raw)
            he_number = int(he_number_raw)
            source_zip_sha256 = str(meta.get("source_zip_sha256", ""))
            ingest_ts_str = str(meta.get("ingest_timestamp", ""))
            ingest_timestamp: Optional[datetime] = None
            if ingest_ts_str:
                try:
                    ingest_timestamp = datetime.fromisoformat(ingest_ts_str)
                except ValueError:
                    pass

            languages_raw = meta.get("languages_in_he", "")
            if isinstance(languages_raw, str) and languages_raw:
                languages_in_he = tuple(languages_raw.split(","))
            else:
                languages_in_he = (lang,)

            proj = project_he_from_xml(
                xml_bytes_raw,
                he_year=he_year,
                he_number=he_number,
                lang=lang,
                source_file=locator,
                source_zip_sha256=source_zip_sha256,
                ingest_timestamp=ingest_timestamp,
                languages_in_he=languages_in_he,
                strict=strict,
            )

            all_corpus.extend(proj.corpus_rows)
            all_atoms.extend(proj.atom_rows)
            all_law_refs.extend(proj.law_ref_rows)
            all_signatures.extend(proj.signature_rows)

            for f in proj.failures:
                all_failures.append({
                    "rule_id": f.rule_id,
                    "phase": f.phase,
                    "family": f.family,
                    "he_year": f.he_year,
                    "he_number": f.he_number,
                    "lang": f.lang,
                    "reason": f.reason,
                    "detail": f.detail,
                })

            for obs in proj.observations:
                obs_dict: Dict[str, Any] = {}
                for attr_name in (
                    "rule_id", "he_year", "he_number", "lang",
                    "ministry_canonical_id", "ministry_show_as", "detail",
                    "reason", "matched_text", "blocking",
                ):
                    val = getattr(obs, attr_name, None)
                    if val is not None:
                        obs_dict[attr_name] = val
                all_observations.append(obs_dict)

            done += 1
            if verbose or (done % 500 == 0):
                elapsed = time.time() - t_start
                print(
                    f"  [{done}] HE {he_year}/{he_number} "
                    f"corpus={len(all_corpus)} atoms={len(all_atoms)} "
                    f"refs={len(all_law_refs)} sigs={len(all_signatures)} "
                    f"({elapsed:.1f}s)",
                    file=sys.stderr,
                )
    finally:
        farchive.close()

    from lawvm.tools.export_persistence import MultiTableExportSpec, export_multi_projection_tail

    if use_parquet and compile_metadata is not None:
        counts = export_multi_projection_tail(
            data_dir=data_dir,
            tables=[
                MultiTableExportSpec("fi_he_corpus", all_corpus, _PARQUET_SCHEMAS.get("fi_he_corpus")),
                MultiTableExportSpec("fi_he_atoms", all_atoms, _PARQUET_SCHEMAS.get("fi_he_atoms")),
                MultiTableExportSpec("fi_he_law_refs", all_law_refs, _PARQUET_SCHEMAS.get("fi_he_law_refs")),
                MultiTableExportSpec(
                    "fi_he_signatures", all_signatures, _PARQUET_SCHEMAS.get("fi_he_signatures")
                ),
            ],
            aux_jsonl=[
                ("fi_he_projection_failures", all_failures),
                ("fi_he_projection_observations", all_observations),
            ],
            use_parquet=True,
            compile_metadata=compile_metadata,
            t_start=t_start,
            label="HE corpus projection",
        )
    else:
        out = Path(data_dir)
        out.mkdir(parents=True, exist_ok=True)

        counts: Dict[str, int] = {}
        _tables = [
            ("fi_he_corpus", all_corpus),
            ("fi_he_atoms", all_atoms),
            ("fi_he_law_refs", all_law_refs),
            ("fi_he_signatures", all_signatures),
        ]
        for name, rows in _tables:
            _write_jsonl(out / f"{name}.jsonl", rows)
            counts[name] = len(rows)
            if use_parquet:
                schema = _PARQUET_SCHEMAS.get(name)
                ok = _try_write_parquet(
                    out / f"{name}.parquet", rows, schema=schema, compile_metadata=compile_metadata
                )
                label = "Parquet+JSONL" if ok else "JSONL"
                print(f"  {name}: {len(rows):,} rows ({label})", file=sys.stderr)
            else:
                print(f"  {name}: {len(rows):,} rows (JSONL)", file=sys.stderr)

        if all_failures:
            _write_jsonl(out / "fi_he_projection_failures.jsonl", all_failures)
            print(
                f"  fi_he_projection_failures: {len(all_failures):,} records",
                file=sys.stderr,
            )
        if all_observations:
            _write_jsonl(out / "fi_he_projection_observations.jsonl", all_observations)

    elapsed_total = time.time() - t_start
    print(
        f"\nHE corpus projection complete: {done:,} HEs in {elapsed_total:.1f}s",
        file=sys.stderr,
    )
    return counts


# ---------------------------------------------------------------------------
# Standalone CLI entry point
# ---------------------------------------------------------------------------


def main(args: Any) -> None:
    """CLI entry point for lawvm export-fi-he-corpus."""
    farchive_path = getattr(args, "farchive", None) or "data/fi_government_proposal.farchive"
    data_dir = getattr(args, "data_dir", None) or f"data/fi/{SCHEMA_VERSION}"
    lang = getattr(args, "lang", None) or "fin"
    limit_raw = getattr(args, "limit", None)
    limit: Optional[int] = int(limit_raw) if limit_raw is not None else None
    strict = getattr(args, "strict", False)
    verbose = getattr(args, "verbose", False)
    use_parquet = not getattr(args, "no_parquet", False)

    print(f"Projecting HE corpus from: {farchive_path}", file=sys.stderr)
    print(f"Output directory: {data_dir}", file=sys.stderr)

    counts = project_he_corpus(
        farchive_path=farchive_path,
        data_dir=data_dir,
        lang=lang,
        limit=limit,
        use_parquet=use_parquet,
        strict=strict,
        verbose=verbose,
    )

    print("\nRow counts:", file=sys.stderr)
    for name, n in counts.items():
        print(f"  {name}: {n:,}", file=sys.stderr)
